"""사내 SSO 로그인 라우트(2026-11-18, api/oidc.py 의 프로토콜을 세션 쿠키·리다이렉트로 잇는다).

  GET /api/auth/sso-info        로그인 화면이 버튼을 그릴지(enabled)와 그 글자(label). 비밀 없음.
  GET /api/auth/oidc/login      IdP 로 302. interaction 은 세션 쿠키(서명)에 얼린다 -- 서버 상태가 없어 dms-api 가 바뀌어도 된다.
  GET /api/auth/oidc/callback   code 교환 → 아이디 → 계정 → 비밀번호 로그인과 **같은** 세션(username + epoch) → next 로 303.

실패는 모두 /login?sso_error=<사유> 로 돌려보낸다(로그인 화면이 reasonText 로 보인다). 아이디 규칙:
- 이메일 <아이디>@<DMS_ACCOUNT_EMAIL_DOMAIN> 만 받는다(oidc.username_from_email) -- 계정 아이디 = 사내 메일 아이디.
- root·admin 은 SSO 로 들어오지 못한다(설치기·운영자의 비상 계정 이름 -- IdP 에 같은 이름이 있어도 넘겨주지 않는다).
- 계정이 없으면 user 역할로 만든다(DMS_OIDC_AUTO_PROVISION, 기본 켬). 특권 목록 이름(protected_names)은 만들지 않는다 --
  특권 계정은 운영자가 먼저 만든다(guard_privileged_account 와 같은 경계). 이미 있는 특권 계정은 SSO 로 로그인할 수 있다:
  IdP 설정은 Secret(클러스터 운영자)에 있어 포탈 세션 관리자가 바꿀 수 없다(메일 재설정을 특권 계정에 막는 이유와 다르다).
- 비활성 계정은 account_disabled.
비밀번호 로그인의 아이디 축 상한(login_limiter)은 쓰지 않는다 -- 비밀번호 추측이 아니고, IdP 가 자기 상한을 가진다."""
import logging
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..domain import DomainValidationError, ROLE_USER, reserved_account_id
from ..privileged import protected_names
from ..repositories.accounts import valid_username
from . import oidc
from .oidc import OidcError

router = APIRouter()
log = logging.getLogger(__name__)

SESSION_KEY = "oidc"
SSO_BLOCKED_NAMES = frozenset({"root", "admin"})
_LOGIN_PATH = "/login"


def _safe_next(raw) -> str:
    """로그인 뒤 돌아갈 포탈 경로. 같은 출처의 절대 경로만(//host·\\·스킴·API 경로 거부) -- 열린 리다이렉트를 막는다."""
    if not isinstance(raw, str) or not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return "/"
    if raw.startswith("/api/") or raw == _LOGIN_PATH or raw.startswith(_LOGIN_PATH + "?"):
        return "/"
    if any(ord(c) < 0x20 for c in raw) or len(raw) > 512:
        return "/"
    return raw


def _back(*, reason_code: str) -> RedirectResponse:
    return RedirectResponse(f"{_LOGIN_PATH}?sso_error={reason_code}", status_code=303)


@router.get("/api/auth/sso-info")
def sso_info(request: Request):
    settings = request.app.state.settings
    enabled = settings.oidc_enabled
    return {"enabled": enabled, "label": settings.oidc_button_label if enabled else ""}


@router.get("/api/auth/oidc/login")
def oidc_login(request: Request, next: str | None = None):
    settings = request.app.state.settings
    if not settings.oidc_enabled:
        return _back(reason_code="sso_not_configured")
    try:
        url, interaction = oidc.start(settings)
    except OidcError as e:
        log.warning("sso login start failed: %s", e)
        return _back(reason_code=e.reason_code)
    interaction["next"] = _safe_next(next)
    request.session[SESSION_KEY] = interaction
    return RedirectResponse(url, status_code=302)


def _ensure_account(settings, repos, username: str) -> dict:
    """SSO 아이디 → 활성 계정의 session_state({"role", "disabled", "session_epoch"})."""
    if username in SSO_BLOCKED_NAMES or not valid_username(username) or reserved_account_id(username):
        raise OidcError("sso_username_invalid", f"username {username!r}")
    state = repos.accounts.session_state(username)
    if state is None:
        if username in protected_names(settings, repos):
            raise OidcError("sso_privileged_account_missing", f"{username!r} is a privileged name without an account")
        if not settings.oidc_auto_provision:
            raise OidcError("sso_account_not_found", f"no account {username!r} and auto provisioning is off")
        try:
            # 비밀번호는 아무도 모르는 무작위 값이다 -- 이 계정은 SSO 로 쓰고, 비밀번호 로그인이 필요하면 셀프 재설정(메일)으로 정한다.
            repos.accounts.create(username, secrets.token_urlsafe(32), ROLE_USER,
                                  email=f"{username}@{settings.account_email_domain}", actor="sso")
            log.info("sso: created account %s (role %s)", username, ROLE_USER)
        except DomainValidationError as e:
            if e.reason_code != "account_exists":       # 같은 사람이 두 창에서 동시에 처음 들어온 경우만 정상
                raise OidcError("sso_username_invalid", e.reason_code) from None
        state = repos.accounts.session_state(username)
        if state is None:
            raise OidcError("sso_account_not_found", f"account {username!r} vanished after create")
    if state["disabled"]:
        raise OidcError("account_disabled", username)
    return state


@router.get("/api/auth/oidc/callback")
def oidc_callback(request: Request, code: str | None = None, state: str | None = None,
                  error: str | None = None):
    settings = request.app.state.settings
    interaction = request.session.pop(SESSION_KEY, None)   # 한 번 쓰고 버린다(재사용·재전송 차단)
    if not settings.oidc_enabled:
        return _back(reason_code="sso_not_configured")
    if error is not None:
        # 사용자가 IdP 화면에서 취소했거나 IdP 가 거절했다(access_denied 등). code 는 오지 않는다.
        log.info("sso callback error from IdP: %s", error[:64])
        return _back(reason_code="sso_denied")
    try:
        claims = oidc.finish(settings, interaction, code, state)
        verified = oidc.email_verified(settings, claims)
        username = oidc.username_from_email(claims.get("email"), settings.account_email_domain)
        if not verified:
            # DMS_OIDC_REQUIRE_EMAIL_VERIFIED 꺼짐 -- 운영자가 IdP 가 email_verified 를 내는지 보고 켤지 정하게 남긴다(§6-9).
            log.info("sso: %s email_verified=%r (DMS_OIDC_REQUIRE_EMAIL_VERIFIED off -- the IdP must not let users edit "
                     "their email)", username, claims.get("email_verified"))
        account = _ensure_account(settings, request.app.state.repos, username)
    except OidcError as e:
        log.warning("sso login failed: %s", e)
        return _back(reason_code=e.reason_code)
    request.session.clear()
    request.session["username"] = username
    # 비밀번호 로그인과 같은 세션 세대(auth.current_identity) -- 비밀번호 변경·계정 재생성이면 SSO 세션도 끝난다.
    request.session["epoch"] = account["session_epoch"]
    log.info("sso: %s logged in (sub %s)", username, str(claims.get("sub"))[:64])
    return RedirectResponse(interaction.get("next") or "/", status_code=303)
