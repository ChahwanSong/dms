from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError
from ..repositories.control import DENY_SUBJECT_TYPES
from .auth import Identity, require_admin

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
    try:
        request.app.state.repos.control.deny(subject_type, subject, body.reason,
                                             identity.actor)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    return {"subject_type": subject_type, "subject": subject.lower()}


@router.delete("/api/admin/identity-denylist/{subject_type}/{subject}")
def allow(subject_type: str, subject: str, request: Request,
          identity: Identity = Depends(require_admin)):
    request.app.state.repos.control.allow(subject_type, subject, identity.actor)
    return {"subject_type": subject_type, "subject": subject.lower()}
