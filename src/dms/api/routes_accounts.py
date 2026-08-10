from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError, ROLE_ADMIN
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


def _guard_last_active_admin(repos, account: dict) -> None:
    # 활성 관리자를 0명으로 만드는 변경(삭제·강등·비활성화)을 막는다. 대상이 지금
    # 활성 관리자이고 그 수가 1이면(=대상이 마지막) 409(설계 §2.3 안전장치 2).
    if (account["role"] == ROLE_ADMIN and account["disabled"] == 0
            and repos.accounts.active_admin_count() <= 1):
        raise HTTPException(status_code=409, detail="last_active_admin")


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


@router.delete("/api/admin/accounts/{username}", status_code=204)
def delete_account(username: str, request: Request,
                   identity: Identity = Depends(require_admin)):
    repos = request.app.state.repos
    # 존재 확인을 가드보다 먼저(set_role 관례): 없는 계정은 409 가 아니라 404.
    account = repos.accounts.get(username)
    if account is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    # 자기 삭제 차단. 토큰 경로 actor 는 node:/shared-token 로 좁혀졌으므로(슬라이스 19
    # actor 게이트) 이 가드는 실질적으로 세션 경로에서만 의미가 있다(설계 §2.3-1).
    if identity.actor == username:
        raise HTTPException(status_code=409, detail="cannot_delete_self")
    _guard_last_active_admin(repos, account)
    # 비종단 요청 보유 계정은 삭제하지 않는다 -- 소유자 없는 잡을 예방(설계 §2.3-3).
    if repos.requests.has_active_for_requester(username):
        raise HTTPException(status_code=409, detail="account_has_active_requests")
    repos.accounts.delete(username, actor=audit_actor(identity))
