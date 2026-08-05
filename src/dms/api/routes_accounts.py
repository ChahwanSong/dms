from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError
from .auth import Identity, require_admin

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
    _guard_self(identity, username)
    try:
        request.app.state.repos.accounts.set_role(username, body.role,
                                                   actor=identity.actor)
    except KeyError:
        raise HTTPException(status_code=404, detail="account_not_found")
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    return request.app.state.repos.accounts.get(username)


@router.put("/api/admin/accounts/{username}/disabled")
def set_disabled(username: str, body: DisabledBody, request: Request,
                  identity: Identity = Depends(require_admin)):
    _guard_self(identity, username)
    try:
        request.app.state.repos.accounts.set_disabled(username, body.disabled,
                                                       actor=identity.actor)
    except KeyError:
        raise HTTPException(status_code=404, detail="account_not_found")
    return request.app.state.repos.accounts.get(username)
