"""인증·계정 셀프서비스 라우트.

비밀번호를 받는 경로는 다섯이다 -- login / signup / password-reset / admin accounts / admin 비밀번호 재설정
(2026-11-03). 모두 `_password_from(body, ...)` 하나를 통해 평문을 얻는다(2026-09-07 전송
봉인): 본문의 `password_enc`(브라우저 WebCrypto 봉인, password_transport.py)를
서버 키로 열거나, 정책이 허용할 때만 평문 `password` 를 받는다. 새로 비밀번호를
받는 엔드포인트를 만들면 반드시 이 헬퍼를 거친다 -- 우회하면 그 경로만 평문이
되고 아무 테스트도 빨간불이 안 된다(test_api_auth_hardening 이 다섯 경로를
전수 고정한다).

로그인(2026-11-03, async)은 아이디 축(실패 10/60s + 총 시도 60/60s, login_limiter.py) → 봉인 해제 → 대기형 전체
스위치(켜졌을 때만) → scrypt 대기형 슬롯 → 슬롯 안 재확인 → 검증 순서다(login 의 docstring, api/login_guard.py).
원 IP 는 어떤 상한의 키도 아니다(기록·경보 전용, auth.client_ip)."""
import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from .. import k8s_access
from ..domain import DomainValidationError, Operation, ROLE_ADMIN, ROLE_USER, reserved_account_id
from ..identity import IDENTITY_MODE_K8S
from ..privileged import protected_names
from ..repositories.accounts import (VERIFICATION_FAILURE_LIMIT, VERIFICATION_FAILURE_WINDOW_SECONDS,
                                     VERIFICATION_PURPOSES, VERIFICATION_TTL_SECONDS,
                                     new_verification_code, numeric_only_username, valid_username)
from .account_notice import notice_mail_mode, send_account_notice
from .auth import (Identity, audit_actor, can_run_as_root, request_origin, require_admin,
                   require_user, tokens_match)
from .login_guard import LoginBusy, retry_after_seconds
from .password_transport import PasswordTransportError
from .routes_accounts import _require_session, guard_privileged_account
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
# 회사 Knox 발신으로 메일을 쏘거나, 남의 재설정 몫을 소진할 수 있다 -- 전체 상한을 더한다. 클라이언트 IP 별 상한(20통/10분)은
# 2026-11-03 에 지웠다(SNAT 뒤 전사가 한 IP 라 한 사람이 전원의 메일을 막았다 -- 원 IP 는 기록·경보 전용, auth.client_ip).
# 동시 발송(릴레이 호출, 최대 타임아웃만큼 스레드를 잡는다)은 슬롯 수로 막는다 -- 공유 스레드풀
# 이 릴레이 장애에 끌려가지 않게. 모두 프로세스 메모리(app.state, create_app).
VERIFICATION_MAIL_GLOBAL_LIMIT = 300
VERIFICATION_MAIL_CONCURRENCY = 8
# 인증번호 전체 오답 상한은 env(DMS_VERIFICATION_GUESS_GLOBAL_LIMIT·_WINDOW_SECONDS, 기본 1000회/3600초 -- 2026-11-03 사용자
# 결정)라 상수가 없다(app.state.verification_guess_limiter, _consume_code). 옛 접속 IP 별 상한(20회/24시간)을 대체한다.
VERIFICATION_GLOBAL_KEY = "global"


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
    """계정 셀프서비스 인증번호 발급(2026-08-20). 6자리(2026-11-03)·5분 TTL 을 만들어 사내
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
    어느 백엔드든 발급 전에 429(verification_locked, Retry-After). 인증번호 전체 오답 상한(_consume_code)이 걸려 있어도
    발급 전에 429(verification_global_locked, Retry-After -- 2026-11-03: 확인이 막힌 동안 메일을 보내 봐야 메일만 낭비이고
    메일 폭주 통로가 된다). 이 거절은 어느 상한에도 기록하지 않는다.

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
    # 숫자만인 아이디·잡 이미지 시스템 계정 이름(2026-10-26): 새 가입만 막는다 -- 그런 옛 계정의 비밀번호 재설정은 그대로 된다.
    if numeric_only_username(body.username):
        if body.purpose == "signup":
            raise HTTPException(status_code=422, detail="username_numeric_only")
    elif reserved_account_id(body.username):
        if body.purpose == "signup":
            raise HTTPException(status_code=422, detail="username_reserved")
    elif not valid_username(body.username):
        raise HTTPException(status_code=422, detail="invalid_username")
    if body.purpose == "password_reset":
        _refuse_privileged_reset(request, body.username)
    account = repos.accounts.get(body.username)
    exists = account is not None
    # 존재 여부를 발급 시점에 정직하게 알린다(사내 포탈 -- 계정 열거 방어보다
    # "왜 안 되는지"가 우선): signup 은 이미 있으면 409, reset 은 없으면 404.
    if body.purpose == "signup" and exists:
        raise HTTPException(status_code=409, detail="account_exists")
    if body.purpose == "password_reset" and not exists:
        raise HTTPException(status_code=404, detail="account_not_found")
    _refuse_disabled_reset(account, body.purpose)
    locked = repos.accounts.verification_lock_seconds(body.username, body.purpose)
    if locked is not None:
        raise HTTPException(status_code=429, detail="verification_locked",
                            headers={"Retry-After": str(locked)})
    global_wait = request.app.state.verification_guess_limiter.retry_after(VERIFICATION_GLOBAL_KEY)
    if global_wait is not None:
        raise HTTPException(status_code=429, detail="verification_global_locked",
                            headers={"Retry-After": str(global_wait)})
    email = _derived_email(settings, body.username)
    out = {"email": email, "expires_in_seconds": VERIFICATION_TTL_SECONDS}
    origin = request_origin(request)
    mail_payload = {"purpose": body.purpose, "recipient": email, **origin}

    if cfg.backend == "stub":
        code = repos.accounts.issue_verification_code(body.username, body.purpose)
        repos.observability.record_event(
            component="mailer", severity="info", event_type="verification_email_stub",
            message=f"{email} ({body.purpose}) -- stub, not actually sent")
        out["stub_code"] = code
        return out

    # knox_relay -- 상한은 코드를 만들기 *전에*. 발송 슬롯을 먼저 잡고, 두 상한(수신자별·전체)은 한 락 아래
    # **모두 통과할 때만 모두 기록**한다(2026-10-01 리뷰: 앞 상한을 기록한 뒤 뒤 상한·슬롯에서 거절하면, 앞 상한을 다 쓴
    # 공격자가 메일 한 통 없이 남의 수신자 몫을 태워 재설정을 막았다).
    state = request.app.state
    if not state.mail_send_slots.acquire(blocking=False):
        _note_throttled(state, repos, "busy", "*",
                        f"{email} ({body.purpose}) -- {VERIFICATION_MAIL_CONCURRENCY} relay calls in flight",
                        mail_payload)
        raise HTTPException(status_code=429, detail="verification_rate_limited",
                            headers={"Retry-After": "10"})
    try:
        checks = ((state.mail_throttle, "recipient", email, f"over {VERIFICATION_MAIL_LIMIT} mails per recipient"),
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
                            f"per {VERIFICATION_MAIL_WINDOW_SECONDS}s, retry in {retry_after}s",
                            mail_payload)
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
                # payload 는 실패 이벤트와 같은 모양(수신자·용도·출발지·사유, 2026-11-11) -- 보안 이벤트 화면의 「메일」 묶음에
                # 보이고 「메일이 안 왔다」 문의를 수신자로 찾을 수 있게.
                repos.observability.record_event(
                    component="mailer", severity="warning", event_type="verification_email_uncertain",
                    message=f"{email} ({body.purpose}) -- {exc}",
                    payload={**mail_payload, "reason": exc.reason})
                return {**out, "delivery_uncertain": True}
            repos.observability.record_event(
                component="mailer", severity="error", event_type="verification_email_failed",
                message=f"{email} ({body.purpose}) -- {exc}",
                payload={**mail_payload, "reason": exc.reason})
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


def _note_throttled(state, repos, kind: str, key: str, message: str, payload: dict) -> None:
    """verification_email_throttled 이벤트를 (상한 종류, 키)마다 창(10분)에 한 번만 남긴다 -- 무인증 경로의
    거절마다 쓰면 상한에 걸린 공격자도 events 를 무제한으로 채운다(2026-10-01 리뷰). 종류는 recipient | global | busy
    (클라이언트 IP 상한은 2026-11-03 에 지웠다). payload 에 종류·용도·수신자와 출발지(client_ip, peer -- 기록 전용)를 싣는다
    -- 운영자가 앞단에서 막을 출발지를 본다."""
    if state.mail_throttle_notes.acquire(f"{kind}:{key}") == 0:
        repos.observability.record_event(
            component="mailer", severity="warning", event_type="verification_email_throttled",
            message=f"{message} [{kind}={key}] (same limit+key not logged again for "
                    f"{VERIFICATION_MAIL_WINDOW_SECONDS}s)",
            payload={"kind": kind, **payload})


def _consume_code(request: Request, username: str, purpose: str, code) -> None:
    """signup·password-reset 의 인증번호 소비. 실패면 HTTPException.

    전체 오답 상한(2026-11-03 사용자 결정 1000회/3600초, env DMS_VERIFICATION_GUESS_GLOBAL_*): 아이디별 누적 잠금만으로는
    여러 계정에 추측을 나눠 던지는 공격을 못 묶는다. 옛 접속 IP 별 상한(2026-10-01 3차 리뷰, 20회/24시간)은 SNAT 뒤 전사가 한
    IP 라 한 사람이 전원의 셀프서비스를 막았고 헤더 위조로 남을 잠글 수 있어 지웠다 -- 대신 **모든 아이디의** 틀린 코드를 한
    키("global")로 센다(로그인 감속기와 같은 LoginRateLimiter, 프로세스 메모리). 상한이면 코드를 비교하지도 않고 429
    verification_global_locked(맞는 코드여도 -- 상한 중 추측이 통하면 상한이 의미 없다). 대가: 공격 중엔 셀프 가입·재설정이
    최대 한 창(1시간) 멈춘다 -- 그동안 관리자 계정 생성·관리자 비밀번호 재설정은 인증번호와 무관하게 되고, 보안 이벤트 화면의
    「전체 잠금 해제」로 바로 풀 수 있다. 틀린 코드(verification_invalid)만 센다(만료·없음·아이디 잠금은 추측이 아니다).
    성공은 지우지 않는다(자기 계정 성공으로 남의 계정 추측 몫을 되살리지 않게).
    검사 -> 소비 -> 기록은 verification_guess_lock 하나 아래에서 한다(4차 리뷰: 따로 하면 동시 요청이 모두 검사를
    통과한 뒤 비교돼, 한 번의 몰아치기로 상한의 2~3배를 실제로 비교했다). DB 호출은 어차피 Database 의 RLock 으로
    한 줄로 서므로 처리량 손실이 없고, 그 락을 잡은 채 이 락을 잡는 경로가 없어 교착도 없다."""
    state = request.app.state
    repos = state.repos
    limiter = state.verification_guess_limiter
    with state.verification_guess_lock:
        retry = limiter.retry_after(VERIFICATION_GLOBAL_KEY)
        if retry is not None:
            raise HTTPException(status_code=429, detail="verification_global_locked",
                                headers={"Retry-After": str(retry)})
        reason = repos.accounts.consume_verification_code(username, purpose, code)
        if reason is None:
            return
        reached = reason == "verification_invalid" and limiter.record_failure(VERIFICATION_GLOBAL_KEY)
    origin = request_origin(request)
    if reached:
        repos.observability.record_event(
            component="accounts", severity="warning", event_type="verification_global_locked",
            message=f"{limiter.max_attempts} wrong verification codes across all accounts within "
                    f"{limiter.window_seconds}s (last: {username} {purpose}) -- self-service signup/password reset "
                    "refused until the window passes or an admin unlocks it",
            payload={"limit": limiter.max_attempts, "window_seconds": limiter.window_seconds,
                     "last_username": username, "last_purpose": purpose, **origin})
    raise _verification_failed(repos, username, purpose, reason, origin)


def _verification_failed(repos, username: str, purpose: str, reason: str, origin: dict):
    """인증번호 소비 실패 -> HTTPException. 누적 실패 잠금은 429 + Retry-After(언제 다시 되는지), 나머지는 422.
    이번 실패로 잠금에 막 들어갔으면 운영 신호를 한 번 남긴다(payload 에 아이디·용도·상한·출발지 -- 2026-11-03)."""
    if reason == "verification_locked":
        retry = repos.accounts.verification_lock_seconds(username, purpose) or 60
        return HTTPException(status_code=429, detail="verification_locked", headers={"Retry-After": str(retry)})
    if reason == "verification_invalid" and repos.accounts.verification_lock_seconds(username, purpose):
        repos.observability.record_event(
            component="accounts", severity="warning", event_type="verification_locked",
            message=f"{username} ({purpose}) -- {VERIFICATION_FAILURE_LIMIT} wrong codes, "
                    f"locked for up to {VERIFICATION_FAILURE_WINDOW_SECONDS}s",
            payload={"username": username, "purpose": purpose, "limit": VERIFICATION_FAILURE_LIMIT,
                     "window_seconds": VERIFICATION_FAILURE_WINDOW_SECONDS, **origin})
    return HTTPException(status_code=422, detail=reason)


@router.post("/api/auth/signup", status_code=201)
def signup(body: SignupBody, request: Request, background: BackgroundTasks):
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
    # 완료 안내 메일(2026-11-03, 최선 노력 -- 응답 뒤에 보내고 실패해도 가입은 그대로, api/account_notice.py).
    background.add_task(send_account_notice, request.app.state, kind="signup", username=body.username,
                        origin=request_origin(request))
    return {"username": body.username}


def _refuse_privileged_reset(request: Request, username: str) -> None:
    """특권 목록 이름의 계정은 셀프 비밀번호 재설정(메일 인증번호)을 쓰지 않는다(2026-10-07 리뷰). 비밀번호 변경도
    특권 계정의 변경인데(guard_privileged_account 는 생성·역할·비활성화·삭제·관리자 재설정·인증 잠금 해제), 메일 경로는 아무 세션 관리자나
    바꿀 수 있어(메일 설정 stub·릴레이 주소) 목록 밖 관리자가 "root" 의 재설정 코드를 받아 그 세션으로 root 자격을
    얻었다. 이 계정의 비밀번호는 계정 화면의 「비밀번호 재설정」(특권 세션 관리자만, 2026-11-03)으로 바꾼다. 특권 실행이 꺼져 있으면 보통
    계정과 같다. 특권 목록 = env ∪ 계정 화면 부여(privileged.privileged_names) -- 부여된 계정도 같은 보호를 받는다."""
    settings = request.app.state.settings
    if settings.allow_privileged_requesters and username in protected_names(settings, request.app.state.repos):
        raise HTTPException(status_code=403, detail="privileged_account_protected")


def _refuse_disabled_reset(account: "dict | None", purpose: str) -> None:
    """비활성 계정은 셀프 비밀번호 재설정(발급·변경 둘 다)을 403 account_disabled 로 막는다(2026-11-11). 예전엔 재설정이
    성공해 화면이 「새 비밀번호로 로그인하세요」를 띄웠지만 로그인은 계속 막혀 있었다 -- 비밀번호가 아니라 활성화가 필요하다는
    것을 발급 시점에 알린다(관리자에게 활성화 요청). 계정이 없으면 여기서 판정하지 않는다(발급은 404, 변경은 코드 소비가 답한다)."""
    if purpose == "password_reset" and account is not None and account["disabled"]:
        raise HTTPException(status_code=403, detail="account_disabled")


@router.post("/api/auth/password-reset")
def password_reset(body: PasswordResetBody, request: Request, background: BackgroundTasks):
    """인증번호 검증 후 비밀번호 변경(셀프서비스). 코드는 항상 필수 -- 이 흐름
    자체가 코드 기반이라 verification_required 게이트와 무관하다. 성공하면 세션 세대가 회전해 그 계정의 **모든** 세션
    (지금 브라우저 포함 -- 이 화면은 로그인 전 화면이다)이 끝나고(2026-11-03, accounts.reset_password), 완료 안내 메일을
    예약한다(최선 노력)."""
    repos = request.app.state.repos
    # 발급 전에 막았어도 업그레이드 전에 발급된 코드가 있을 수 있다 -- 소비·추측 카운터보다 먼저 거부한다.
    _refuse_privileged_reset(request, body.username)
    # 발급 뒤에 비활성화됐을 수 있다 -- 같은 이유로 코드 소비·봉인 해제 전에 거부한다.
    _refuse_disabled_reset(repos.accounts.get(body.username), "password_reset")
    # signup 과 같은 순서 이유: 코드 소비 전에 봉인을 연다.
    password = _password_from(request, body, purpose="password_reset")
    _consume_code(request, body.username, "password_reset", body.code)
    try:
        repos.accounts.reset_password(body.username, password, actor=body.username)
    except DomainValidationError as e:
        raise HTTPException(status_code=404, detail=e.reason_code)
    # 아이디 로그인 실패 카운터를 비운다(2026-11-03 리뷰 -- 관리자 재설정과 같은 처리): 셀프 재설정은 대개 비밀번호가 기억나지
    # 않아 여러 번 틀린 뒤에 한다. 비우지 않으면 실패 10회로 감속에 걸린 사용자가 재설정에 성공하고도 새 비밀번호 첫 로그인에서
    # 429 를 받았다. 인증번호 소비를 거친 뒤라 추측 감속을 우회하는 통로가 아니다. 총 시도 카운터는 그대로(비밀번호와 무관한 상한).
    request.app.state.login_limiter.clear(f"user:{body.username}")
    background.add_task(send_account_notice, request.app.state, kind="password_reset", username=body.username,
                        origin=request_origin(request))
    return {"username": body.username}


# 로그인 아이디 길이 상한(2026-11-03 리뷰). 계정 아이디는 최대 64자(accounts._USERNAME_RE)라 이보다 긴 아이디의 계정은 없다
# -- 옛 숫자·예약 이름까지 여유를 둔 값. IP 축이 사라진 뒤로 출발지 하나가 아무 길이의 아이디로 두 감속기(user: 키, 창 동안
# 메모리에 남는다)와 이벤트 message·payload 를 부풀릴 수 있었다(1MiB 아이디 50건이면 api 메모리 상한 1Gi -- OOM 재시작은
# 모든 메모리 상한을 비운다). 그래서 1단계 **앞에서** 아무 카운터·경보도 건드리지 않고 401 invalid_credentials(없는 아이디와
# 같은 답 -- 새 사유 코드·응답 모양 없음).
LOGIN_USERNAME_MAX = 128


def _id_axis_wait(state, user_key: str) -> "int | None":
    """아이디 축 판정(실패 상한 + 총 시도 상한) -- 둘 중 늦게 풀리는 쪽의 대기 초, 둘 다 여유면 None."""
    waits = [w for w in (state.login_limiter.retry_after(user_key),
                         state.login_attempt_limiter.retry_after(user_key)) if w is not None]
    return max(waits) if waits else None


async def _record_events(repos, events: list) -> None:
    """보안 이벤트 DB 쓰기는 스레드에서(이벤트 루프를 DB RLock 에 묶지 않게). record_event 는 올리지 않는다."""
    for ev in events:
        await anyio.to_thread.run_sync(lambda e=ev: repos.observability.record_event(**e))


def _auth_event(event_type: str, message: str, payload: dict, severity: str = "warning") -> dict:
    return {"component": "auth", "severity": severity, "event_type": event_type,
            "message": message, "payload": payload}


def _rate_limited(wait: int) -> HTTPException:
    return HTTPException(status_code=429, detail="login_rate_limited", headers={"Retry-After": str(wait)})


def _busy(wait: int) -> HTTPException:
    return HTTPException(status_code=503, detail="login_busy", headers={"Retry-After": str(wait)})


@router.post("/api/auth/login")
async def login(body: LoginBody, request: Request):
    """로그인(2026-11-03 async). 처리 순서는 바꾸지 않는다(설계 v6 §5, v3 2(마)) -- 바꾸면 깨지는 것을 단계마다 적는다:

    0. 아이디가 LOGIN_USERNAME_MAX(128자)보다 길면 401 invalid_credentials -- 어떤 카운터·경보·이벤트도 건드리지 않는다
       (그런 계정은 없고, 감속기 키·이벤트 크기를 공격자가 정하지 못하게).
    1. 아이디 축: user:<아이디> 의 실패(10/60s)·총 시도(성공 포함, 60/60s) 중 하나라도 상한이면 즉시 429
       login_rate_limited(Retry-After). 토큰을 쓰지 않고 기다리지 않는다(기다리게 하면 한 계정 대입이 전체 줄을 차지한다).
    2. 봉인 해제: 실패는 422(추측이 아니라 어느 카운터에도 세지 않는다 -- 3단계보다 뒤면 봉인 오류가 토큰을 쓴다).
    3. 전체 줄(대기형 전체 스위치가 켜졌을 때만): 토큰을 예약하고 차례까지 이벤트 루프에서 잔다(스레드를 잡지 않는다).
       예상 대기가 M 초를 넘으면 기다리지 않고 503 login_busy(+Retry-After) -- 용량 신호지 차단이 아니다.
    4. scrypt 슬롯을 직접 잡는다(최대 N 초, 넘으면 503 login_busy).
    5. 슬롯 안에서 아이디 축 재확인: 기다리는 동안 같은 아이디의 다른 시도가 상한을 채웠으면 429. 없으면 대기 시간만큼
       상한을 넘긴다(판정과 기록 사이가 원자적이지 않다) -- 슬롯 안이라 넘치는 양이 슬롯 수 이하로 묶인다.
    6. 검증: 총 시도를 기록하고(성공·실패 모두) 스레드에서 authenticate(limiter 인자 없이 -- login_guard.LoginGate.slot).
    7. 결과: 401 invalid_credentials 만 토큰을 쓴 채로 두고 아이디 실패·경보 카운터에 기록한다. 그 밖(성공·비밀번호가 맞은
       비활성 계정의 401 account_disabled(2026-11-11)·재확인 429·슬롯 503·예외)은 finally 에서 토큰을 돌려준다 -- 「실패만
       센다」(login_limiter).

    원 IP(auth.client_ip)는 경보·이벤트 기록에만 쓴다 -- 어떤 단계도 IP 로 거절하지 않는다. 이벤트 DB 쓰기는 모두 스레드에서,
    가능하면 슬롯 밖에서 한다."""
    if len(body.username) > LOGIN_USERNAME_MAX:                            # 0 -- 메모리에 아무것도 남기지 않는다
        raise HTTPException(status_code=401, detail="invalid_credentials")
    state = request.app.state
    repos = state.repos
    settings = state.settings
    pacer = state.login_pacer
    alarms = state.login_alarms
    user_key = f"user:{body.username}"
    origin = request_origin(request)

    wait = _id_axis_wait(state, user_key)                                  # 1
    if wait is not None:
        raise _rate_limited(wait)
    password = _password_from(request, body, purpose="login")              # 2

    reserved = False
    spent = False
    try:
        if pacer.enabled:                                                  # 3
            ok, pace_wait = pacer.reserve()
            if not ok:
                retry = retry_after_seconds(pace_wait)
                if alarms.once("busy", "pacing"):
                    await _record_events(repos, [_auth_event(
                        "login_busy",
                        f"global login pacing queue is longer than {pacer.max_wait_seconds}s "
                        f"(expected wait {pace_wait:.1f}s) -- answered 503 login_busy",
                        {"cause": "pacing", "retry_after_seconds": retry, **origin})])
                raise _busy(retry)
            reserved = True
            if pace_wait > 0:
                if alarms.once("pacing_engaged", "global"):
                    await _record_events(repos, [_auth_event(
                        "login_pacing_engaged",
                        f"global login pacing engaged: logins now wait (first wait {pace_wait:.1f}s, "
                        f"{pacer.limit} failures per {pacer.window_seconds}s)",
                        {"wait_seconds": round(pace_wait, 2), "limit": pacer.limit,
                         "window_seconds": pacer.window_seconds, **origin})])
                pacer.enter_wait()
                try:
                    await anyio.sleep(pace_wait)
                finally:
                    pacer.leave_wait()

        pending: list = []
        try:
            async with state.login_gate.slot():                            # 4
                wait = _id_axis_wait(state, user_key)                      # 5
                if wait is not None:
                    raise _rate_limited(wait)
                attempt_limiter = state.login_attempt_limiter              # 6
                if attempt_limiter.record(user_key):
                    left = alarms.account_event("login_attempt_limited", user_key)
                    if left is not None:
                        pending.append(_auth_event(
                            "login_attempt_limited",
                            f"{user_key}: {attempt_limiter.max_attempts} login attempts (including successes) within "
                            f"{attempt_limiter.window_seconds}s -- further attempts rejected until the window passes"
                            + _budget_note(left, alarms),
                            {"username": body.username, "limit": attempt_limiter.max_attempts,
                             "window_seconds": attempt_limiter.window_seconds, **origin}))
                for alarm in alarms.note_attempt():
                    pending.append(_alarm_event(alarm, origin, alarms))
                auth = await anyio.to_thread.run_sync(repos.accounts.authenticate, body.username, password)
        except LoginBusy:
            retry = max(1, settings.login_scrypt_max_wait_seconds)
            if alarms.once("busy", "slots"):
                await _record_events(repos, [_auth_event(
                    "login_busy",
                    f"no password-check slot within {settings.login_scrypt_max_wait_seconds}s "
                    f"({settings.login_scrypt_slots} slots busy) -- answered 503 login_busy",
                    {"cause": "slots", "retry_after_seconds": retry, **origin})])
            raise _busy(retry) from None

        if auth is None:                                                   # 7
            spent = True
            limiter = state.login_limiter
            # 봉인 오류(위 422)는 비밀번호 추측이 아니라 세지 않는다 -- 여기 401 만 센다.
            for key in limiter.record_failure(user_key):
                # 상한에 **닿는 순간** 키마다 창에 한 번만, 그것도 아이디를 바꿔 가며 쌓지 못하게 종류별 몫 안에서만 남긴다
                # (LoginAlarms.account_event -- 거절마다·아이디마다 남기면 공격자가 events 를 채운다).
                left = alarms.account_event("login_rate_limited", key)
                if left is not None:
                    pending.append(_auth_event(
                        "login_rate_limited",
                        f"{key}: {limiter.max_attempts} failed logins within {limiter.window_seconds}s -- "
                        "further attempts rejected until the window passes" + _budget_note(left, alarms),
                        {"username": body.username, "limit": limiter.max_attempts,
                         "window_seconds": limiter.window_seconds, **origin}))
            for alarm in alarms.note_failure(origin["client_ip"], body.username):
                pending.append(_alarm_event(alarm, origin, alarms))
            await _record_events(repos, pending)
            raise HTTPException(status_code=401, detail="invalid_credentials")
        await _record_events(repos, pending)
        if auth.get("disabled") is True:
            # 비밀번호가 맞은 비활성 계정(2026-11-11, accounts.authenticate): 추측 실패가 아니라 아이디 실패·경보 카운터에 세지
            # 않고(spent 그대로 -- 전체 스위치 토큰은 finally 가 돌려준다) 세션도 만들지 않은 채 「비활성」을 그대로 알린다.
            # 예전 401 invalid_credentials 는 사용자를 비밀번호 재설정으로 보냈고, 10번이면 429 까지 받았다.
            raise HTTPException(status_code=401, detail="account_disabled")
        state.login_limiter.clear(user_key)
        request.session.clear()
        request.session["username"] = body.username
        # 세션 세대를 쿠키에 얼린다(2026-11-03) -- 비밀번호가 바뀌면 다음 요청에서 401 session_revoked(auth.current_identity).
        request.session["epoch"] = auth["session_epoch"]
        return {"actor": body.username, "role": auth["role"]}
    finally:
        if reserved and not spent:
            pacer.refund()


_ALARM_MESSAGES = {
    "login_failure_alarm": "{count} failed logins across all accounts within {window_seconds}s (threshold {threshold})",
    "login_attempt_alarm": "{count} login attempts (including successes) within {window_seconds}s "
                           "(threshold {threshold})",
    "login_source_alarm": "{count} failed logins from {client_ip} within {window_seconds}s (threshold {threshold}, "
                          "last account {last_username}) -- not blocked; block the source upstream if it is an attack",
}


def _budget_note(left: int, alarms) -> str:
    """account_event 의 몫을 다 쓴 마지막 이벤트에 붙이는 안내(그 뒤 같은 창의 같은 종류는 기록되지 않는다 -- 429 판정은 그대로)."""
    if left > 0:
        return ""
    return (f" [per-account events of this kind capped at {alarms.account_event_budget} per {alarms.window_seconds}s -- "
            "further ones in this window are not logged; login_failure_alarm still counts every failure]")


def _alarm_event(alarm: dict, origin: dict, alarms) -> dict:
    """LoginAlarms 재료 → 이벤트 한 건. 경보는 차단하지 않는다(이벤트만).

    - 메시지의 출발지는 원 IP 를 모르면(None) "unknown" 이다(2026-11-03 리뷰 -- 「from None」으로 찍혔다). 포맷용 사본에만
      넣고 payload 의 client_ip 는 null 그대로 둔다(null = 모름, LoginAlarms 의 집계 키 "unknown" 과 같은 말).
    - budget_left(출발지 경보의 창당 몫, LoginAlarms.note_failure)는 payload 에 싣지 않는다 -- 0 이면 이번이 이 창의 마지막
      출발지 경보라는 안내를 메시지에 붙인다."""
    event_type = alarm["event_type"]
    left = alarm.get("budget_left")
    payload = {k: v for k, v in alarm.items() if k not in ("event_type", "budget_left")}
    payload.update(origin)
    shown = {**payload, "client_ip": "unknown" if payload.get("client_ip") is None else payload["client_ip"]}
    message = _ALARM_MESSAGES[event_type].format(**shown)
    if left == 0:
        message += (f" [per-source alarms capped at {alarms.account_event_budget} per {alarms.window_seconds}s -- "
                    "further sources in this window are not logged; login_failure_alarm still counts every failure]")
    return _auth_event(event_type, message, payload)


@router.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


@router.get("/api/auth/me")
def me(request: Request, identity: Identity = Depends(require_user)):
    # can_run_as_root: 포탈이 'root 권한으로 실행' 을 보여 주고 관리자 기본값(root)을 켤지
    # 정하는 근거(2026-09-30). 자격 없는 관리자에게 기본 root 를 켜 두면 제출이 403 이 된다.
    # dms-k8s 설계 D4·D15(2026-10-25) -- 포탈이 제출 화면을 가르는 세 값(표시일 뿐, 서버가 제출·계획·컨펌에서 다시 본다):
    #   identity_mode  늘 "k8s"(2026-10-26 -- dms-k8s 는 k8s 모드 전용; 옛 포탈 탭이 이 키로 화면을 가르므로 남긴다).
    #   unrestricted   맵핑 면제(= can_run_as_root -- 의미 불변, 계정 관리 경계). 면제자만 직접 경로·전체 PV·사용자 지정.
    #   user_operations 이 로그인이 제출할 수 있는 연산(관리자 = 전부, 그 밖 = DMS_USER_ALLOWED_OPERATIONS ∩ 알려진 연산 --
    #                   제출 게이트 operation_admin_only 와 같은 규칙). Operation 순서(scan·sync·rm).
    settings = request.app.state.settings
    can_root = can_run_as_root(identity, settings, request.app.state.repos)
    # scan 은 특권 운영자만(k8s_access.scan_allowed, 제출 게이트와 같은 술어).
    ops = [o.value for o in Operation
           if (identity.role == ROLE_ADMIN or o.value in settings.user_allowed_operations)
           and (o.value != "scan" or k8s_access.scan_allowed(unrestricted=can_root))]
    return {"actor": identity.actor, "role": identity.role,
            "can_run_as_root": can_root,
            "identity_mode": IDENTITY_MODE_K8S,
            "unrestricted": can_root,
            "user_operations": ops}


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
        # 토큰 미제시 + 공유 Bearer 토큰(노드 에이전트 보유)은 여기서 거부한다 -- 토큰으로 allowlist 이름의 관리자
        # 계정을 만들면 그 세션이 배치 특권 게이트를 통과한다(routes_accounts._require_session 와 같은 이유).
        # 첫 관리자 부트스트랩은 별도 비밀인 x-admin-token 경로(아래)라 영향 없다.
        if identity.auth != "session":
            raise HTTPException(status_code=403, detail="accounts_session_required")
        # 특권 목록 이름("root"·"admin" 등)의 계정은 특권 세션 관리자만 만든다(역할 무관 -- user 로 만든 뒤 승격하는
        # 길도 같은 가드가 set_role 에서 막는다). routes_accounts.guard_privileged_account 주석.
        guard_privileged_account(request, identity, body.username)
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


@router.post("/api/admin/accounts/{username}/password-reset")
def admin_password_reset(username: str, body: _PasswordBody, request: Request, background: BackgroundTasks,
                         identity: Identity = Depends(require_admin)):
    """관리자 비밀번호 재설정(2026-11-03, 설계 v6 §5 「운영자 구제」): 인증번호 없이 대상의 비밀번호를 바로 바꾼다. 인증번호
    전체 잠금(verification_global_locked)과 무관하게 동작한다 -- 셀프서비스가 멈춘 동안의 구제 경로다.

    판정 순서: 관리자(403 admin_required)·세션(403 accounts_session_required -- 공유 토큰은 모든 노드 에이전트가 가진 admin
    자격이라, 그것으로 계정 비밀번호를 정하면 계정 게이트가 무효다) → 본문 아이디 = 경로(422 account_username_mismatch --
    봉인의 AAD 사용자 자리가 본문 아이디라, 다른 계정용 봉인을 이 경로에 재사용하지 못하게 둘을 맞춘다) → 404 → 특권 계정은
    특권 세션 관리자만(403 privileged_account_protected) → 자기 자신 409 cannot_reset_self(탈취한 세션으로 비밀번호를 바꿔
    원래 주인을 잠그는 길 -- 자기 비밀번호는 셀프 재설정(인증번호)으로, 특권 계정은 다른 특권 세션 관리자가 바꾼다) → 봉인
    해제(용도 admin_password_reset, 평문은 정책이 허용할 때만 -- 토큰 부트스트랩 같은 예외 없음).

    성공하면 대상의 세션 세대가 회전해 그 계정의 모든 세션이 끝나고(관리자 자신의 세션·Bearer 는 무관), 그 아이디의 로그인
    실패 카운터를 비워 사용자가 곧바로 로그인할 수 있게 한다(총 시도 카운터는 그대로 -- 반복 로그인 상한은 비밀번호와
    무관하다), 완료 안내 메일을 예약한다(최선 노력). notice_mail 은 지금 메일 설정으로 정한 예정(queued·stub·unavailable)."""
    _require_session(identity)
    if body.username != username:
        raise HTTPException(status_code=422, detail="account_username_mismatch")
    repos = request.app.state.repos
    if repos.accounts.get(username) is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    guard_privileged_account(request, identity, username)
    if identity.actor == username:
        raise HTTPException(status_code=409, detail="cannot_reset_self")
    password = _password_from(request, body, purpose="admin_password_reset")
    actor = audit_actor(identity)
    try:
        repos.accounts.admin_reset_password(username, password, actor=actor)
    except KeyError:
        raise HTTPException(status_code=404, detail="account_not_found") from None
    state = request.app.state
    state.login_limiter.clear(f"user:{username}")
    origin = request_origin(request)
    repos.observability.record_event(
        component="accounts", severity="info", event_type="password_admin_reset",
        message=f"{actor} reset the password of {username} -- all sessions of {username} revoked",
        payload={"username": username, "actor": actor, **origin})
    mode = notice_mail_mode(repos, state.settings)
    background.add_task(send_account_notice, state, kind="password_admin_reset", username=username,
                        actor=identity.actor, origin=origin)
    return {"username": username, "sessions_revoked": True, "notice_mail": mode}
