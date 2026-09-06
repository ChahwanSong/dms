"""인증·계정 셀프서비스 라우트.

비밀번호를 받는 경로는 넷이다 -- login / signup / password-reset / admin accounts.
넷 모두 `_password_from(body, ...)` 하나를 통해 평문을 얻는다(2026-09-07 전송
봉인): 본문의 `password_enc`(브라우저 WebCrypto 봉인, password_transport.py)를
서버 키로 열거나, 정책이 허용할 때만 평문 `password` 를 받는다. 새로 비밀번호를
받는 엔드포인트를 만들면 반드시 이 헬퍼를 거친다 -- 우회하면 그 경로만 평문이
되고 아무 테스트도 빨간불이 안 된다(test_api_password_transport 가 네 경로를
전수 고정한다).

로그인은 실패 기준 사용자명·IP 별 1분 10회 감속(login_limiter.py)을 **검증 전에**
거친다."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError, ROLE_ADMIN, ROLE_USER
from ..repositories.accounts import (VERIFICATION_PURPOSES,
                                     VERIFICATION_TTL_SECONDS, valid_username)
from .auth import Identity, client_ip, require_admin, require_user, tokens_match
from .password_transport import PasswordTransportError

router = APIRouter()


class EncryptedPassword(BaseModel):
    """password_transport.py 와이어 형식. 필드 검증은 복호 단계가 한다(여기서
    길이를 검사하면 오류 종류가 갈라져 복호 오라클이 된다)."""
    version: int
    kid: str
    epk: str
    iv: str
    ct: str


class _PasswordBody(BaseModel):
    """비밀번호를 받는 본문의 공통 부분. 둘 중 하나만 채운다 -- 포탈은 항상
    password_enc, 평문 password 는 정책(password_encryption_required=false) 또는
    x-admin-token 부트스트랩 경로에서만 받아들여진다."""
    username: str
    password: str | None = None
    password_enc: EncryptedPassword | None = None


class SignupBody(_PasswordBody):
    # 인증번호(2026-08-20): account_verification_required(기본 켜짐)면 필수.
    # email 필드는 받지 않는다 -- 이메일은 항상 <아이디>@<도메인> 파생이다.
    code: str | None = None


class LoginBody(_PasswordBody):
    pass


class VerificationBody(BaseModel):
    username: str
    purpose: str


class PasswordResetBody(_PasswordBody):
    code: str


def _derived_email(settings, username: str) -> str:
    return f"{username}@{settings.account_email_domain}"


def _password_from(request: Request, body: _PasswordBody, *, purpose: str,
                   allow_plaintext: bool | None = None) -> str:
    """본문에서 평문 비밀번호를 얻는 유일한 통로.

    password_enc 가 있으면 연다(kid 불일치·복호 실패는 422 사유 코드). 없으면 평문
    password 인데, allow_plaintext 가 None 이면 settings.password_encryption_required
    가 결정하고(라이브 기본 True → 422 password_encryption_required), 호출자가
    명시하면(토큰 부트스트랩 경로 True) 그 값을 따른다. 둘 다 없으면
    password_missing -- 빈 문자열은 "없음"이 아니다(null ≠ 빈값 규약)."""
    if body.password_enc is not None:
        transport = request.app.state.password_transport
        try:
            return transport.decrypt(body.password_enc.model_dump(),
                                     purpose=purpose, username=body.username)
        except PasswordTransportError as e:
            raise HTTPException(status_code=422, detail=e.reason_code)
    if body.password is None:
        raise HTTPException(status_code=422, detail="password_missing")
    if allow_plaintext is None:
        allow_plaintext = not request.app.state.settings.password_encryption_required
    if not allow_plaintext:
        raise HTTPException(status_code=422, detail="password_encryption_required")
    return body.password


@router.get("/api/auth/transport-key")
def transport_key(request: Request):
    """비밀번호 봉인용 서버 공개키(무인증 -- 공개키는 비밀이 아니고 로그인 전에
    필요하다). 프런트는 이 값을 캐시하고 kid 불일치 422 를 받으면 다시 받는다."""
    return request.app.state.password_transport.public_info()


@router.post("/api/auth/verification-codes")
def request_verification_code(body: VerificationBody, request: Request):
    """계정 셀프서비스 인증번호 발급(2026-08-20). 4자리·5분 TTL 을 만들어 사내
    이메일(<아이디>@도메인 파생)로 보낸다. 지금 메일러는 stub 뿐(사내 메일 연동
    불가) -- stub 이면 응답에 코드를 에코해 화면이 흐름을 완주한다. 실메일
    백엔드가 생기면 에코가 빠지는 것이 계약이고, 이벤트(verification_email_stub)
    는 코드 없이 수신자·목적만 남긴다."""
    repos = request.app.state.repos
    settings = request.app.state.settings
    if body.purpose not in VERIFICATION_PURPOSES:
        raise HTTPException(status_code=422, detail="invalid_verification_purpose")
    if not valid_username(body.username):
        raise HTTPException(status_code=422, detail="invalid_username")
    exists = repos.accounts.get(body.username) is not None
    # 존재 여부를 발급 시점에 정직하게 알린다(사내 포탈 -- 계정 열거 방어보다
    # "왜 안 되는지"가 우선): signup 은 이미 있으면 409, reset 은 없으면 404.
    if body.purpose == "signup" and exists:
        raise HTTPException(status_code=409, detail="account_exists")
    if body.purpose == "password_reset" and not exists:
        raise HTTPException(status_code=404, detail="account_not_found")
    code = repos.accounts.issue_verification_code(body.username, body.purpose)
    email = _derived_email(settings, body.username)
    repos.observability.record_event(
        component="mailer", severity="info", event_type="verification_email_stub",
        message=f"{email} ({body.purpose}) -- stub, not actually sent")
    out = {"email": email, "expires_in_seconds": VERIFICATION_TTL_SECONDS}
    if settings.mailer_backend == "stub":
        out["stub_code"] = code
    return out


@router.post("/api/auth/signup", status_code=201)
def signup(body: SignupBody, request: Request):
    repos = request.app.state.repos
    settings = request.app.state.settings
    # 봉인을 먼저 연다: 인증번호는 소비형(한 번 쓰면 삭제)이라, 코드를 소비한 뒤
    # 비밀번호 봉인이 깨져 422 가 나면 사용자는 유효한 코드를 잃는다.
    password = _password_from(request, body, purpose="signup")
    # 인증번호 게이트(기본 켜짐): 코드 없이는 계정이 생기지 않는다. 끄는 경로
    # (DMS_ACCOUNT_VERIFICATION_REQUIRED=false)는 테스트·개발 편의다.
    if settings.account_verification_required:
        if not body.code:
            raise HTTPException(status_code=422, detail="verification_required")
        reason = repos.accounts.consume_verification_code(
            body.username, "signup", body.code)
        if reason is not None:
            raise HTTPException(status_code=422, detail=reason)
    try:
        repos.accounts.create(
            body.username, password, ROLE_USER,
            email=_derived_email(settings, body.username), actor=body.username)
    except DomainValidationError as e:
        raise HTTPException(status_code=409 if e.reason_code == "account_exists" else 422,
                            detail=e.reason_code)
    return {"username": body.username}


@router.post("/api/auth/password-reset")
def password_reset(body: PasswordResetBody, request: Request):
    """인증번호 검증 후 비밀번호 변경(셀프서비스). 코드는 항상 필수 -- 이 흐름
    자체가 코드 기반이라 verification_required 게이트와 무관하다."""
    repos = request.app.state.repos
    # signup 과 같은 순서 이유: 코드 소비 전에 봉인을 연다.
    password = _password_from(request, body, purpose="password_reset")
    reason = repos.accounts.consume_verification_code(
        body.username, "password_reset", body.code)
    if reason is not None:
        raise HTTPException(status_code=422, detail=reason)
    try:
        repos.accounts.reset_password(body.username, password, actor=body.username)
    except DomainValidationError as e:
        raise HTTPException(status_code=404, detail=e.reason_code)
    return {"username": body.username}


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request):
    repos = request.app.state.repos
    limiter = request.app.state.login_limiter
    # 감속 키: 사용자명(한 계정 대입)과 클라이언트 IP(spraying). 검증 **전에** 본다.
    keys = (f"user:{body.username}", f"ip:{client_ip(request)}")
    wait = limiter.retry_after(*keys)
    if wait is not None:
        raise HTTPException(status_code=429, detail="login_rate_limited",
                            headers={"Retry-After": str(wait)})
    password = _password_from(request, body, purpose="login")
    role = repos.accounts.verify(body.username, password)
    if role is None:
        # 봉인 오류(위 422)는 비밀번호 추측이 아니라 세지 않는다 -- 여기 401 만 센다.
        for key in limiter.record_failure(*keys):
            # 상한에 **닿는 순간** 한 번만 남긴다(거절마다 남기면 공격자가 events 를 채운다).
            repos.observability.record_event(
                component="auth", severity="warning", event_type="login_rate_limited",
                message=(f"{key}: {limiter.max_attempts} failed logins within "
                         f"{limiter.window_seconds}s -- further attempts rejected "
                         "until the window passes"))
        raise HTTPException(status_code=401, detail="invalid_credentials")
    limiter.clear(f"user:{body.username}")
    request.session.clear()
    request.session["username"] = body.username
    # 슬라이스 33(H): 로그인 성공 시 프로브 타깃 조건부 선등록. 신원 전파는
    # register_probe_target -> 에이전트 리포트 응답 -> 노드별 getpwnam 프로브 ->
    # 다음 리포트 상행의 왕복(~130s)이라, 첫 데이터 요청 시점에 시작하면 그만큼
    # 적격 노드가 늦게 늘어난다. 로그인 시점에 미리 시작해 예열한다.
    # LDAP 에 해석되는 계정만 등록한다 -- 로컬 전용 계정(mason 류)을 넣으면 전
    # 노드가 영원히 status Missing 프로브만 쌓는다(예열 이득 0, 프로브 낭비만).
    resolver = request.app.state.identity_resolver
    if resolver is not None:
        try:
            if resolver.resolve(body.username) is not None:
                repos.control.register_probe_target(body.username)
        except Exception:
            # 선등록은 예열 최적화일 뿐 로그인의 전제가 아니다 -- LDAP 불가
            # (IdentityUnavailable)를 포함한 어떤 실패도 로그인을 막으면 안 되므로
            # 전부 삼킨다(fail-soft). 진짜 신원 판정은 planner 의
            # resolve_job_identity 가 fail-closed 로 다시 한다.
            pass
    return {"actor": body.username, "role": role}


@router.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


@router.get("/api/auth/me")
def me(identity: Identity = Depends(require_user)):
    return {"actor": identity.actor, "role": identity.role}


class AdminCreateBody(_PasswordBody):
    # 세션 admin 경로에서만 존중된다(기본 user). 토큰 부트스트랩은 admin 고정.
    role: str = ROLE_USER


@router.post("/api/admin/accounts", status_code=201)
def create_admin_account(body: AdminCreateBody, request: Request):
    settings = request.app.state.settings
    supplied = request.headers.get("x-admin-token", "")
    if supplied and not tokens_match(supplied, settings.admin_token):
        # 토큰을 **제시했는데 틀린** 경우는 기존 계약 그대로 403 -- 세션 경로로
        # 흘리면 틀린 토큰이 401(미인증)로 위장돼 진단이 흐려진다.
        raise HTTPException(status_code=403, detail="admin_token_required")
    if not supplied:
        # 토큰 미제시 = 포탈 세션 admin 경로(2026-08-20, 사용자 결정: 운영자
        # 화면의 계정 생성). require_admin 이 401/403 을 그대로 나른다.
        identity = require_admin(request)
        if body.role not in (ROLE_USER, ROLE_ADMIN):
            raise HTTPException(status_code=422, detail="invalid_role")
        password = _password_from(request, body, purpose="admin_create")
        try:
            request.app.state.repos.accounts.create(
                body.username, password, body.role,
                email=_derived_email(settings, body.username),
                actor=identity.actor)
        except DomainValidationError as e:
            raise HTTPException(
                status_code=409 if e.reason_code == "account_exists" else 422,
                detail=e.reason_code)
        return {"username": body.username, "role": body.role}
    # 토큰 부트스트랩(운영자 curl, 대개 클러스터 안에서): 평문을 허용한다 --
    # 토큰 보유자는 이미 admin 이고, 첫 관리자를 만들 때 브라우저가 없다. 봉인해
    # 보내면(password_enc) 그대로 받는다(seal_with_info 로 스크립트가 봉인 가능).
    password = _password_from(request, body, purpose="admin_create",
                              allow_plaintext=True)
    try:
        # M8: actor="admin-token"을 그대로 쓰면 accounts._USERNAME_RE가 "admin-token"을
        # 유효한 사용자명으로 허용하고 /api/auth/signup은 무인증이라, 누구나 그 이름으로
        # 셀프 가입해 이 부트스트랩 경로가 남긴 감사 행과 구분 안 되는 행을 만들 수
        # 있다. ':'는 사용자명에 금지돼 있어(auth.py의 audit_actor()가 감사 actor에
        # token: 접두를 붙일 때 쓰는 것과 같은 예약 네임스페이스) 어떤 사용자도 절대
        # 이 값에 도달할 수 없다.
        request.app.state.repos.accounts.create(
            body.username, password, ROLE_ADMIN, actor="token:admin-token")
    except DomainValidationError as e:
        raise HTTPException(status_code=409 if e.reason_code == "account_exists" else 422,
                            detail=e.reason_code)
    return {"username": body.username, "role": ROLE_ADMIN}
