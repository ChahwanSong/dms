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
from ..repositories.accounts import (VERIFICATION_FAILURE_LIMIT, VERIFICATION_FAILURE_WINDOW_SECONDS,
                                     VERIFICATION_PURPOSES, VERIFICATION_TTL_SECONDS,
                                     new_verification_code, valid_username)
from .auth import (Identity, can_run_as_root, client_ip, require_admin, require_user,
                   tokens_match)
from .password_transport import PasswordTransportError
from ..mail_config import MAILER_BACKENDS, resolve_mail_config
from .mailer import MailerError, render_verification_email, send_mail_via_relay

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


# 수신자별 인증 메일 상한(knox_relay 에만 적용). 프로세스 메모리라 워커가 여럿이면 워커별로 센다.
# 감속기 인스턴스는 app.state.mail_throttle(create_app) -- 모듈 전역이면 테스트·앱 인스턴스 사이로 새어 나간다.
VERIFICATION_MAIL_LIMIT = 5
VERIFICATION_MAIL_WINDOW_SECONDS = 600
# 비로그인 엔드포인트의 추가 상한(2026-10-01 리뷰): 수신자별만 있으면 한 클라이언트가 사번을 돌며 전 직원에게
# 회사 Knox 발신으로 메일을 쏘거나, 남의 재설정 몫을 소진할 수 있다 -- 클라이언트 IP 별·전체 상한을 더한다.
# 동시 발송(릴레이 호출, 최대 타임아웃만큼 스레드를 잡는다)은 슬롯 수로 막는다 -- 공유 스레드풀(healthz·login)
# 이 릴레이 장애에 끌려가지 않게. 셋 다 프로세스 메모리(app.state, create_app).
VERIFICATION_MAIL_PER_CLIENT_LIMIT = 20
VERIFICATION_MAIL_GLOBAL_LIMIT = 300
VERIFICATION_MAIL_CONCURRENCY = 8
# 접속 IP 별 틀린 인증번호 상한(2026-10-01 3차 리뷰, _consume_code): 하루 20회 -- 한 IP 가 여러 계정에 추측을
# 나눠도 맞힐 확률이 하루 0.2% 를 넘지 않는다. 같은 IP 뒤의 정상 사용자(공용 프록시)도 함께 막힐 수 있다는 것이 대가.
VERIFICATION_GUESS_PER_CLIENT_LIMIT = 20
VERIFICATION_GUESS_WINDOW_SECONDS = 86400


@router.get("/api/auth/mail-info")
def mail_info(request: Request):
    """로그인 전 화면(가입·비밀번호 재설정)이 "인증번호가 <아이디>@<도메인> 으로 전송됩니다" 를 그리는 재료
    (2026-10-01). 예전엔 화면이 도메인을 하드코딩해 DMS_ACCOUNT_EMAIL_DOMAIN 을 바꾼 사이트에서 틀렸다.
    비밀은 없다 -- 도메인과 발송 방식(stub = 화면에 코드가 보이는 개발 모드)만."""
    settings = request.app.state.settings
    cfg = resolve_mail_config(request.app.state.repos, settings)
    return {"email_domain": settings.account_email_domain,
            "delivery": cfg.backend if cfg.backend in MAILER_BACKENDS else "unknown"}


@router.post("/api/auth/verification-codes")
def request_verification_code(body: VerificationBody, request: Request):
    """계정 셀프서비스 인증번호 발급(2026-08-20). 4자리·5분 TTL 을 만들어 사내
    이메일(<아이디>@도메인 파생)로 보낸다. 메일러 백엔드(mail_config.resolve_mail_config --
    포탈 메일 설정 > env DMS_MAILER_BACKEND):

    - "stub": 실제 발송 없음. 응답에 코드를 에코(stub_code)해 화면이 흐름을 완주한다.
    - "knox_relay"(2026-10-01): 메신저 서버의 knox_mail_dms_certi 를 거쳐 Knox 메일로 HTML
      인증 메일을 TO 로 보낸다. 실메일 백엔드이므로 계약대로 에코가 빠진다.
      - 수신자별 상한(5통/10분)은 코드를 발급하기 *전에* 검사해 429
        (verification_rate_limited, Retry-After)로 알린다. 발급 뒤에 막으면 새 코드가
        이전 코드를 대체해 메일함의 코드까지 죽일 수 있기 때문.
      - 발송에 실패하면 보낸 척하지 않고 502(verification_email_failed). 릴레이 쪽
        상한(최후 방어선)에 걸린 경우는 429. 요청은 나갔는데 응답을 못 받은 경우(relay_no_response
        -- 메일이 갔을 수도 있다)는 코드를 저장하고 200 + delivery_uncertain 으로 알린다(저장하지 않으면
        도착한 메일의 코드가 영영 안 맞는다).

    누적 실패 잠금(accounts.VERIFICATION_FAILURE_LIMIT -- 재발급으로 초기화되지 않는 무차별 대입 상한)이면
    어느 백엔드든 발급 전에 429(verification_locked, Retry-After).

    이벤트(verification_email_stub / _sent / _uncertain / _throttled / _failed)는 어느 쪽이든 코드 없이
    수신자·목적(실패면 사유)만 남긴다. _throttled 는 같은 (상한, 키)에 창마다 한 번만 -- 무인증 경로라
    거절마다 쓰면 상한에 걸린 요청이 events 테이블을 무제한으로 채운다."""
    repos = request.app.state.repos
    settings = request.app.state.settings
    cfg = resolve_mail_config(repos, settings)
    if cfg.backend not in MAILER_BACKENDS:
        # 설정 오타가 조용히 "보낸 척"으로 이어지지 않게, 코드를 발급하기 전에 막는다.
        raise HTTPException(status_code=500, detail="mailer_misconfigured")
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
    locked = repos.accounts.verification_lock_seconds(body.username, body.purpose)
    if locked is not None:
        raise HTTPException(status_code=429, detail="verification_locked",
                            headers={"Retry-After": str(locked)})
    email = _derived_email(settings, body.username)
    out = {"email": email, "expires_in_seconds": VERIFICATION_TTL_SECONDS}

    if cfg.backend == "stub":
        code = repos.accounts.issue_verification_code(body.username, body.purpose)
        repos.observability.record_event(
            component="mailer", severity="info", event_type="verification_email_stub",
            message=f"{email} ({body.purpose}) -- stub, not actually sent")
        out["stub_code"] = code
        return out

    # knox_relay -- 상한은 코드를 만들기 *전에*. 발송 슬롯을 먼저 잡고, 세 상한(수신자별·클라이언트 IP 별·
    # 전체)은 한 락 아래 **모두 통과할 때만 모두 기록**한다(2026-10-01 리뷰: 앞 상한을 기록한 뒤 뒤 상한·슬롯에서
    # 거절하면, 자기 IP 상한을 다 쓴 공격자가 메일 한 통 없이 남의 수신자 몫을 태워 재설정을 막았다).
    state = request.app.state
    if not state.mail_send_slots.acquire(blocking=False):
        _note_throttled(state, repos, "busy", "*",
                        f"{email} ({body.purpose}) -- {VERIFICATION_MAIL_CONCURRENCY} relay calls in flight")
        raise HTTPException(status_code=429, detail="verification_rate_limited",
                            headers={"Retry-After": "10"})
    try:
        checks = ((state.mail_throttle, "recipient", email, f"over {VERIFICATION_MAIL_LIMIT} mails per recipient"),
                  (state.mail_throttle_client, "client", client_ip(request),
                   f"over {VERIFICATION_MAIL_PER_CLIENT_LIMIT} mails per client"),
                  (state.mail_throttle_global, "global", "*",
                   f"over {VERIFICATION_MAIL_GLOBAL_LIMIT} mails in total"))
        blocked = None
        with state.mail_throttle_lock:
            for throttle, kind, key, what in checks:
                retry_after = throttle.peek(key)
                if retry_after:
                    blocked = (kind, key, what, retry_after)
                    break
            if blocked is None:
                for throttle, _kind, key, _what in checks:
                    throttle.record(key)
        if blocked is not None:
            kind, key, what, retry_after = blocked
            _note_throttled(state, repos, kind, key,
                            f"{email} ({body.purpose}) -- {what} "
                            f"per {VERIFICATION_MAIL_WINDOW_SECONDS}s, retry in {retry_after}s")
            raise HTTPException(status_code=429, detail="verification_rate_limited",
                                headers={"Retry-After": str(retry_after)})
        # 코드는 메일이 릴레이에 접수된 **뒤에** 저장한다(2026-10-01 리뷰, 참고 구현은 발급 후 발송): 발송이
        # 실패하면(릴레이 429·Knox 거부·연결 실패) 메일함에 있던 이전 코드가 그대로 유효하다 -- 수신자별 상한을
        # 발급 전에 보는 것과 같은 이유. 인증번호는 HTML 본문에만 들어간다(제목·이벤트·예외 메시지에는 없음).
        code = new_verification_code()
        subject, html_body = render_verification_email(
            code=code, purpose=body.purpose, username=body.username,
            ttl_seconds=VERIFICATION_TTL_SECONDS, service_name=cfg.service_name)
        try:
            if cfg.token_unreadable:
                # 포탈에 저장된 봉인을 열 수 없다(세션 시크릿 교체 등) -- 빈 토큰으로 보내 401 을 받는 대신
                # 원인을 그대로 남긴다(관리 → 메일 설정에서 인증 키를 다시 입력).
                raise MailerError("relay_misconfigured", "stored relay token cannot be decrypted -- re-enter it")
            send_mail_via_relay(
                relay_url=cfg.relay_url, token=cfg.relay_token,
                to=email, subject=subject, body=html_body, content_type="HTML",
                timeout=cfg.timeout_seconds)
        except MailerError as exc:
            if exc.reason == "relay_no_response":
                # 요청은 나갔고 응답만 못 받았다 -- 메일이 갔을 수 있으니 코드를 저장한다(보낸 척은 아니다:
                # 화면이 "확인이 늦어지고 있다"고 알린다).
                repos.accounts.issue_verification_code(body.username, body.purpose, code=code)
                repos.observability.record_event(
                    component="mailer", severity="warning", event_type="verification_email_uncertain",
                    message=f"{email} ({body.purpose}) -- {exc}")
                return {**out, "delivery_uncertain": True}
            repos.observability.record_event(
                component="mailer", severity="error", event_type="verification_email_failed",
                message=f"{email} ({body.purpose}) -- {exc}")
            if exc.reason == "rate_limited":
                raise HTTPException(
                    status_code=429, detail="verification_rate_limited",
                    headers={"Retry-After": str(exc.retry_after or 60)}) from None
            raise HTTPException(status_code=502, detail="verification_email_failed") from None
    finally:
        state.mail_send_slots.release()
    repos.accounts.issue_verification_code(body.username, body.purpose, code=code)
    repos.observability.record_event(
        component="mailer", severity="info", event_type="verification_email_sent",
        message=f"{email} ({body.purpose}) -- sent via knox_relay")
    return out


def _note_throttled(state, repos, kind: str, key: str, message: str) -> None:
    """verification_email_throttled 이벤트를 (상한 종류, 키)마다 창(10분)에 한 번만 남긴다 -- 무인증 경로의
    거절마다 쓰면 상한에 걸린 공격자도 events 를 무제한으로 채운다(2026-10-01 리뷰). 메시지에 [종류=키]를 싣는다
    -- 클라이언트 상한이면 차단할 IP 가 운영자에게 보여야 한다."""
    if state.mail_throttle_notes.acquire(f"{kind}:{key}") == 0:
        repos.observability.record_event(
            component="mailer", severity="warning", event_type="verification_email_throttled",
            message=f"{message} [{kind}={key}] (same limit+key not logged again for "
                    f"{VERIFICATION_MAIL_WINDOW_SECONDS}s)")


def _consume_code(request: Request, username: str, purpose: str, code) -> None:
    """signup·password-reset 의 인증번호 소비. 실패면 HTTPException.

    접속 IP 별 틀린 코드 상한(2026-10-01 3차 리뷰): 아이디별 누적 잠금만으로는 한 IP 가 메일 상한(20통/10분)
    안에서 여러 계정에 추측을 나눠 던져(계정당 2통 x 5회) 하루 ~1.4 계정을 맞힌다. 같은 IP 의 틀린 코드를
    VERIFICATION_GUESS_PER_CLIENT_LIMIT 회/24시간으로 묶는다(로그인 감속기와 같은 LoginRateLimiter, 프로세스
    메모리). 상한이면 코드를 비교하지도 않고 429 verification_client_locked -- 틀린 코드(verification_invalid)만
    센다(만료·없음은 추측이 아니다). 성공은 지우지 않는다(자기 계정 성공으로 남의 계정 추측 몫을 되살리지 않게).
    검사 -> 소비 -> 기록은 verification_guess_lock 하나 아래에서 한다(4차 리뷰: 따로 하면 동시 요청이 모두 검사를
    통과한 뒤 비교돼, 한 번의 몰아치기로 상한의 2~3배를 실제로 비교했다). DB 호출은 어차피 Database 의 RLock 으로
    한 줄로 서므로 처리량 손실이 없고, 그 락을 잡은 채 이 락을 잡는 경로가 없어 교착도 없다."""
    repos = request.app.state.repos
    limiter = request.app.state.verification_guess_limiter
    ip_key = f"ip:{client_ip(request)}"
    with request.app.state.verification_guess_lock:
        retry = limiter.retry_after(ip_key)
        if retry is not None:
            raise HTTPException(status_code=429, detail="verification_client_locked",
                                headers={"Retry-After": str(retry)})
        reason = repos.accounts.consume_verification_code(username, purpose, code)
        if reason is None:
            return
        reached = reason == "verification_invalid" and limiter.record_failure(ip_key)
    if reached:
        repos.observability.record_event(
            component="accounts", severity="warning", event_type="verification_client_locked",
            message=f"{ip_key} -- {VERIFICATION_GUESS_PER_CLIENT_LIMIT} wrong verification codes "
                    f"(last: {username} {purpose}), blocked for {VERIFICATION_GUESS_WINDOW_SECONDS}s")
    raise _verification_failed(repos, username, purpose, reason)


def _verification_failed(repos, username: str, purpose: str, reason: str):
    """인증번호 소비 실패 -> HTTPException. 누적 실패 잠금은 429 + Retry-After(언제 다시 되는지), 나머지는 422.
    이번 실패로 잠금에 막 들어갔으면 운영 신호를 한 번 남긴다."""
    if reason == "verification_locked":
        retry = repos.accounts.verification_lock_seconds(username, purpose) or 60
        return HTTPException(status_code=429, detail="verification_locked", headers={"Retry-After": str(retry)})
    if reason == "verification_invalid" and repos.accounts.verification_lock_seconds(username, purpose):
        repos.observability.record_event(
            component="accounts", severity="warning", event_type="verification_locked",
            message=f"{username} ({purpose}) -- {VERIFICATION_FAILURE_LIMIT} wrong codes, "
                    f"locked for up to {VERIFICATION_FAILURE_WINDOW_SECONDS}s")
    return HTTPException(status_code=422, detail=reason)


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
        _consume_code(request, body.username, "signup", body.code)
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
    _consume_code(request, body.username, "password_reset", body.code)
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
def me(request: Request, identity: Identity = Depends(require_user)):
    # can_run_as_root: 포탈이 'root 권한으로 실행' 을 보여 주고 관리자 기본값(root)을 켤지
    # 정하는 근거(2026-09-30). 자격 없는 관리자에게 기본 root 를 켜 두면 제출이 403 이 된다.
    return {"actor": identity.actor, "role": identity.role,
            "can_run_as_root": can_run_as_root(identity, request.app.state.settings)}


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
