from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, StrictBool
from ..domain import DomainValidationError, ROLE_ADMIN
from ..privileged import protected_names
from .auth import Identity, audit_actor, can_run_as_root, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class RoleBody(BaseModel):
    role: str


class DisabledBody(BaseModel):
    disabled: bool


class RootGrantBody(BaseModel):
    # StrictBool: true/false 만(1·"yes" 는 422) -- 특권 부여 스위치를 호출자 버그로 켜지 않게.
    granted: StrictBool


def _require_session(identity: Identity) -> None:
    """계정 생성·삭제·역할·비활성화 변경은 **세션으로 로그인한 관리자**만(2026-10-07 리뷰). require_admin 은 모든
    노드 에이전트가 가진 공유 토큰도 관리자로 받는데, 그 경로로 특권 allowlist 에 있는 이름의 계정을 지우고 다시
    만들면(비밀번호는 토큰 보유자가 정한다) 그 이름의 세션으로 배치 특권 게이트(_require_batch_privilege)·root
    실행 자격을 그대로 얻는다. 조회(GET)는 토큰 관리자도 된다(routes_mail_settings._require_session 과 같은 축)."""
    if identity.auth != "session":
        raise HTTPException(status_code=403, detail="accounts_session_required")


def guard_privileged_account(request: Request, identity: Identity, username: str) -> None:
    """특권 목록(DMS_PRIVILEGED_REQUESTERS ∪ 계정 화면에서 root 실행 자격 플래그가 선 계정 -- 비활성이어도,
    privileged.protected_names) 이름의 계정은 **특권 세션 관리자**만 만들고·역할을 바꾸고·끄고·지운다
    (2026-10-07 리뷰). 목록은 관리자 사이의 경계다(can_run_as_root: 관리자 + 목록 + 세션) -- 목록 밖 관리자가
    계정 화면에서 "root"·"admin" 계정을 새로 만들거나(비밀번호는 자기가 정한다) 지웠다 다시 만들거나 그 이름의
    가입 계정을 승격하면, 그 세션으로 배치 특권 게이트·root 실행 자격을 그대로 얻었다. 특권 실행이 꺼져 있으면
    (allow_privileged_requesters=false) 그 이름에 특권이 없으므로 가드도 없다. 첫 관리자 부트스트랩(x-admin-token)은
    별도 비밀이라 대상이 아니다."""
    settings, repos = request.app.state.settings, request.app.state.repos
    # 보호 집합은 효력(privileged_names)이 아니라 저장된 부여까지(protected_names) -- 비활성화된 부여 계정도 보호한다.
    if not settings.allow_privileged_requesters or username not in protected_names(settings, repos):
        return
    if not can_run_as_root(identity, settings, repos):
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


def _with_root(rows, settings, repos) -> list:
    """계정 행 + root 실행 자격 표시(2026-10-12): root_source = "config"(env DMS_PRIVILEGED_REQUESTERS -- 배포 설정으로만
    바뀐다) | "granted"(계정 화면에서 특권 관리자가 줬다) | null, root_eligible = 지금 효력이 있나(허용 설정 + admin 역할 +
    활성 + 출처 있음 -- 실제 실행엔 세션 로그인도 필요하다, api.auth.can_run_as_root)."""
    granted = repos.accounts.root_granted_flags()
    reserved = repos.accounts.RESERVED_GRANT_NAMES
    out = []
    for r in rows:
        flag = bool(granted.get(r["username"]))
        source = ("config" if r["username"] in settings.privileged_requesters
                  else "granted" if flag and r["username"] not in reserved else None)
        eligible = bool(settings.allow_privileged_requesters and source is not None
                        and r["role"] == ROLE_ADMIN and r["disabled"] == 0)
        # root_granted = 저장된 부여 플래그(env 와 겹쳐도 보인다 -- env 에서 빠지면 효력이 남는다는 사실을 화면이 말하고
        # 뺄 수 있게, 리뷰).
        out.append({**r, "root_source": source, "root_eligible": eligible, "root_granted": flag})
    return out


@router.get("/api/admin/accounts")
def list_accounts(request: Request):
    repos, settings = request.app.state.repos, request.app.state.settings
    return _with_root(repos.accounts.list(), settings, repos)


@router.put("/api/admin/accounts/{username}/root")
def set_root_grant(username: str, body: RootGrantBody, request: Request,
                   identity: Identity = Depends(require_admin)):
    """root 실행 자격 부여/회수(2026-10-12 사용자 보고: 「일반 사용자명에게 admin 역할을 줬어. 근데 root로 실행 체크
    옵션이 작업 등록할때 안뜨네」 -- 역할만으로는 자격이 생기지 않는다, privileged.py). **특권 세션 관리자만**(이미 root
    자격이 있는 세션 관리자 -- can_run_as_root): 목록은 관리자 사이의 경계라(guard_privileged_account), 목록 밖 관리자가
    스스로나 남에게 자격을 주면 그 경계가 사라진다. 판정 순서: 세션(403 accounts_session_required) → 404 →
    특권 관리자(403 root_grant_privileged_only) → 자기 자신(409 cannot_lock_self) → env 목록 이름(409
    root_grant_config_managed -- 배포 설정으로만 바뀐다) → 부여는 admin 역할에만(409 root_grant_requires_admin; 회수는
    역할과 무관). 부여된 계정은 env 목록 계정과 같은 보호를 받는다(특권 관리자만 관리·셀프 비밀번호 재설정 불가)."""
    _require_session(identity)
    repos, settings = request.app.state.repos, request.app.state.settings
    account = repos.accounts.get(username)
    if account is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    if not can_run_as_root(identity, settings, repos):
        raise HTTPException(status_code=403, detail="root_grant_privileged_only")
    _guard_self(identity, username)
    # env 목록 이름에는 **주기만** 막는다 -- 빼기는 자격을 줄이기만 하므로 언제든 된다(2026-10-12 리뷰: 화면에서 준 뒤 env 에도
    # 들어간 이름은 그 사이 저장된 부여를 뺄 길이 없어, 운영자가 env 에서 이름을 빼도 부여로 root 가 남았다).
    if body.granted and username in settings.privileged_requesters:
        raise HTTPException(status_code=409, detail="root_grant_config_managed")
    if body.granted and account["role"] != ROLE_ADMIN:
        raise HTTPException(status_code=409, detail="root_grant_requires_admin")
    try:
        repos.accounts.set_root_granted(username, body.granted, actor=audit_actor(identity))
    except KeyError:
        raise HTTPException(status_code=404, detail="account_not_found")
    except DomainValidationError as e:
        # root_grant_requires_admin(쓰기 문장의 역할 조건 -- 라우트 판정 뒤 강등 경합), root_grant_reserved_name
        raise HTTPException(status_code=409, detail=e.reason_code)
    return _with_root([repos.accounts.get(username)], settings, repos)[0]


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
    repos = request.app.state.repos
    return _with_root([repos.accounts.get(username)], request.app.state.settings, repos)[0]


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
    repos = request.app.state.repos
    return _with_root([repos.accounts.get(username)], request.app.state.settings, repos)[0]


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
