from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError, ROLE_ADMIN
from .auth import Identity, audit_actor, can_run_as_root, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class RoleBody(BaseModel):
    role: str


class DisabledBody(BaseModel):
    disabled: bool


def _require_session(identity: Identity) -> None:
    """계정 생성·삭제·역할·비활성화 변경은 **세션으로 로그인한 관리자**만(2026-10-07 리뷰). require_admin 은 모든
    노드 에이전트가 가진 공유 토큰도 관리자로 받는데, 그 경로로 특권 allowlist 에 있는 이름의 계정을 지우고 다시
    만들면(비밀번호는 토큰 보유자가 정한다) 그 이름의 세션으로 배치 특권 게이트(_require_batch_privilege)·root
    실행 자격을 그대로 얻는다. 조회(GET)는 토큰 관리자도 된다(routes_mail_settings._require_session 과 같은 축)."""
    if identity.auth != "session":
        raise HTTPException(status_code=403, detail="accounts_session_required")


def guard_privileged_account(request: Request, identity: Identity, username: str) -> None:
    """특권 목록(DMS_PRIVILEGED_REQUESTERS) 이름의 계정은 **특권 세션 관리자**만 만들고·역할을 바꾸고·끄고·지운다
    (2026-10-07 리뷰). 목록은 관리자 사이의 경계다(can_run_as_root: 관리자 + 목록 + 세션) -- 목록 밖 관리자가
    계정 화면에서 "root"·"admin" 계정을 새로 만들거나(비밀번호는 자기가 정한다) 지웠다 다시 만들거나 그 이름의
    가입 계정을 승격하면, 그 세션으로 배치 특권 게이트·root 실행 자격을 그대로 얻었다. 특권 실행이 꺼져 있으면
    (allow_privileged_requesters=false) 그 이름에 특권이 없으므로 가드도 없다. 첫 관리자 부트스트랩(x-admin-token)은
    별도 비밀이라 대상이 아니다."""
    settings = request.app.state.settings
    if not settings.allow_privileged_requesters or username not in settings.privileged_requesters:
        return
    if not can_run_as_root(identity, settings):
        raise HTTPException(status_code=403, detail="privileged_account_protected")


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
    _require_session(identity)
    # 존재 확인을 self-guard보다 먼저: 존재하지 않는 계정을 대상으로 하면
    # (설령 그 이름이 자신의 actor와 같더라도) 409가 아니라 404여야 한다.
    account = request.app.state.repos.accounts.get(username)
    if account is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    guard_privileged_account(request, identity, username)
    _guard_self(identity, username)
    # 강등(admin -> 그 외)만 마지막 관리자 가드 대상. 승격은 관리자 수를 안 줄인다.
    if body.role != ROLE_ADMIN:
        _guard_last_active_admin(request.app.state.repos, account)
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
    _require_session(identity)
    account = request.app.state.repos.accounts.get(username)
    if account is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    guard_privileged_account(request, identity, username)
    _guard_self(identity, username)
    # 비활성화만 마지막 관리자 가드 대상. 재활성화는 관리자 수를 안 줄인다.
    if body.disabled:
        _guard_last_active_admin(request.app.state.repos, account)
    try:
        request.app.state.repos.accounts.set_disabled(username, body.disabled,
                                                       actor=audit_actor(identity))
    except KeyError:
        raise HTTPException(status_code=404, detail="account_not_found")
    return request.app.state.repos.accounts.get(username)


@router.delete("/api/admin/accounts/{username}", status_code=204)
def delete_account(username: str, request: Request,
                   identity: Identity = Depends(require_admin)):
    _require_session(identity)
    repos = request.app.state.repos
    # 존재 확인을 가드보다 먼저(set_role 관례): 없는 계정은 409 가 아니라 404.
    account = repos.accounts.get(username)
    if account is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    guard_privileged_account(request, identity, username)
    # 자기 삭제 차단. 토큰 경로 actor 는 node:/shared-token 로 좁혀졌으므로(슬라이스 19
    # actor 게이트) 이 가드는 실질적으로 세션 경로에서만 의미가 있다(설계 §2.3-1).
    if identity.actor == username:
        raise HTTPException(status_code=409, detail="cannot_delete_self")
    _guard_last_active_admin(repos, account)
    # 비종단 요청 보유 계정은 삭제하지 않는다 -- 소유자 없는 잡을 예방(설계 §2.3-3).
    if repos.requests.has_active_for_requester(username):
        raise HTTPException(status_code=409, detail="account_has_active_requests")
    repos.accounts.delete(username, actor=audit_actor(identity))
