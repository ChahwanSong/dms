from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from .. import k8s_access
from ..domain import DomainValidationError
from ..repositories.control import DENY_SUBJECT_TYPES
from .auth import Identity, audit_actor, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class DenyBody(BaseModel):
    reason: str | None = None


@router.get("/api/admin/identity-denylist")
def list_denylist(request: Request):
    return request.app.state.repos.control.list_denylist()


@router.put("/api/admin/identity-denylist/{subject_type}/{subject}", status_code=201)
def deny(subject_type: str, subject: str, body: DenyBody, request: Request,
         identity: Identity = Depends(require_admin)):
    if subject_type not in DENY_SUBJECT_TYPES:
        raise HTTPException(status_code=422, detail="invalid_denylist_subject_type")
    # k8s 신원 모드(2026-10-25 dms-k8s 설계 D3): LDAP 이 없어 그룹 규칙은 평가되지 않는다(identity.resolve_job_identity 가
    # groups=[] 로 이름 규칙만 본다). 등록은 되는데 아무것도 막지 않는 규칙을 만들지 않게 여기서 거절한다. 이미 있는 그룹
    # 규칙(모드 전환 전)은 목록·삭제(DELETE)로 정리할 수 있다.
    if subject_type == "group" and k8s_access.k8s_mode(request.app.state.settings):
        raise HTTPException(status_code=422, detail="denylist_group_unsupported")
    try:
        request.app.state.repos.control.deny(subject_type, subject, body.reason,
                                             audit_actor(identity))
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    return {"subject_type": subject_type, "subject": subject.lower()}


@router.delete("/api/admin/identity-denylist/{subject_type}/{subject}")
def allow(subject_type: str, subject: str, request: Request,
          identity: Identity = Depends(require_admin)):
    request.app.state.repos.control.allow(subject_type, subject, audit_actor(identity))
    return {"subject_type": subject_type, "subject": subject.lower()}
