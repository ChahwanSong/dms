from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError
from .auth import Identity, audit_actor, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class RoleBody(BaseModel):
    role: str


class DisabledBody(BaseModel):
    disabled: bool


def _guard_self(identity: Identity, username: str) -> None:
    # 마지막 관리자가 스스로를 잠가 포탈에서 잠기는 사고를 막는다.
    if identity.actor == username:
        raise HTTPException(status_code=409, detail="cannot_lock_self")


@router.get("/api/admin/accounts")
def list_accounts(request: Request):
    return request.app.state.repos.accounts.list()


@router.put("/api/admin/accounts/{username}/role")
def set_role(username: str, body: RoleBody, request: Request,
             identity: Identity = Depends(require_admin)):
    # 존재 확인을 self-guard보다 먼저: 존재하지 않는 계정을 대상으로 하면
    # (설령 그 이름이 자신의 actor와 같더라도) 409가 아니라 404여야 한다.
    if request.app.state.repos.accounts.get(username) is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    _guard_self(identity, username)
    try:
        request.app.state.repos.accounts.set_role(username, body.role,
                                                   actor=audit_actor(identity))
    except KeyError:
        raise HTTPException(status_code=404, detail="account_not_found")
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    return request.app.state.repos.accounts.get(username)


@router.put("/api/admin/accounts/{username}/disabled")
def set_disabled(username: str, body: DisabledBody, request: Request,
                  identity: Identity = Depends(require_admin)):
    if request.app.state.repos.accounts.get(username) is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    _guard_self(identity, username)
    try:
        request.app.state.repos.accounts.set_disabled(username, body.disabled,
                                                       actor=audit_actor(identity))
    except KeyError:
        raise HTTPException(status_code=404, detail="account_not_found")
    return request.app.state.repos.accounts.get(username)
