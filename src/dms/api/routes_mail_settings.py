"""포탈 메일 설정 API(2026-10-01 사용자 요청: "knox mail 인증에 관련된 키, relay용 knox 서버 IP, relay port
(8025) 등을 포탈에서 설정"). 관리자 전용.

  GET  /api/admin/mail-settings               적용값(포탈 > env > 기본) + 칸별 출처 + 토큰 상태(값은 절대 안 줌)
  PUT  /api/admin/mail-settings               보낸 칸만 바꾼다. 값 null = 포탈 값을 지워 env 기본값으로
  POST /api/admin/mail-settings/health-check  릴레이 GET /healthz ("연결 확인")
  POST /api/admin/mail-settings/test-mail     릴레이로 테스트 메일 한 통 ("테스트 메일") -- 발송 방식과 무관하게
                                              릴레이 경로를 끝까지 태워 본다(knox_relay 로 바꾸기 전 확인용)

릴레이 토큰(RELAY_TOKEN):
  - 전송: 비밀번호와 같은 봉인 통로(password_transport, 용도 mail_relay_token, AAD 사용자 자리 "mail_settings").
    평문 relay_token 은 password_encryption_required=false(테스트)일 때만 받는다 -- 라이브는 422.
  - 저장: secret_box 봉인(mail_settings.relay_token_enc). 응답·감사 로그·이벤트 어디에도 값이 나가지 않는다
    (configured/source/unreadable 상태만).
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..domain import DomainValidationError
from ..mail_config import (MAILER_BACKENDS, _env_relay_parts, resolve_from_row, resolve_mail_config, seal_token,
                           validate_backend, validate_host, validate_port, validate_scheme,
                           validate_service_name, validate_timeout, validate_token)
from ..repositories.mail_settings import FIELDS
from .auth import Identity, audit_actor, require_admin
from .mailer import MailerError, check_relay_health, render_test_email, send_mail_via_relay
from .password_transport import PasswordTransportError
from .routes_auth import EncryptedPassword, _derived_email

router = APIRouter(dependencies=[Depends(require_admin)])

# 봉인 전송(password_transport)의 용도·AAD 사용자 자리 -- 프런트 MailSettingsPage 와 같은 값.
SEAL_PURPOSE = "mail_relay_token"
SEAL_SUBJECT = "mail_settings"
# 연결 확인(healthz)은 짧게 -- 릴레이가 살아 있는지만 본다(발송 타임아웃과 별개).
HEALTH_TIMEOUT_SECONDS = 5.0
_EMAIL_RE = re.compile(r"[^@\s]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
_VALIDATORS = {"backend": validate_backend, "relay_scheme": validate_scheme,
               "relay_host": validate_host, "relay_port": validate_port,
               "timeout_seconds": validate_timeout, "service_name": validate_service_name}
_UNREADABLE = "stored relay token cannot be decrypted -- re-enter it in 관리 → 메일 설정"


def _require_session(identity: Identity) -> None:
    """설정 변경·발송·연결 확인은 **세션으로 로그인한 관리자**만(2026-10-01 리뷰). require_admin 은 공유 토큰
    (노드 에이전트가 x-dms-actor node:<이름> 로 쓰는 것 포함)도 관리자로 받는데, 그 경로로 릴레이 주소를 바꾸고
    테스트 메일을 누르면 토큰을 빼내거나 발송 방식을 stub 으로 돌릴 수 있다. 조회(GET)는 토큰 관리자도 된다."""
    if identity.auth != "session":
        raise HTTPException(status_code=403, detail="mail_settings_session_required")


class MailSettingsBody(BaseModel):
    """보낸 칸만 바꾼다(model_fields_set). 값 null(또는 빈 문자열) = 포탈 값을 지워 env 기본값으로."""
    backend: str | None = None
    relay_scheme: str | None = None
    relay_host: str | None = None
    relay_port: int | None = None
    timeout_seconds: float | None = None
    service_name: str | None = None
    relay_token: str | None = None                    # 평문 -- 봉인 정책이 꺼진 환경(테스트)만
    relay_token_enc: EncryptedPassword | None = None  # 포탈은 항상 이쪽
    clear_relay_token: bool = False
    # 키를 실을 때 화면이 보고 있던 적용 주소(GET 의 relay_url). 저장 시점의 주소가 이것과 다르면 409 -- 다른 관리자가
    # 그 사이 주소를 바꿨는데 오래된 탭의 "키만 저장"이 진짜 키를 그 주소에 묶는 것을 막는다(2026-10-01 3차 리뷰).
    # 포탈은 키를 보낼 때 항상 싣는다. 생략(API 스크립트)이면 대조하지 않는다.
    seen_relay_url: str | None = None


class TestMailBody(BaseModel):
    to: str | None = None


def _view(request: Request) -> dict:
    repos = request.app.state.repos
    settings = request.app.state.settings
    cfg = resolve_mail_config(repos, settings)
    row = repos.mail_settings.get() or {}
    env_scheme, env_host, env_port = _env_relay_parts(settings)
    return {
        "backend": cfg.backend, "relay_scheme": cfg.relay_scheme, "relay_host": cfg.relay_host,
        "relay_port": cfg.relay_port, "relay_url": cfg.relay_url,
        "timeout_seconds": cfg.timeout_seconds, "service_name": cfg.service_name,
        "sources": cfg.sources,
        # 포탈에 저장된 값(None = 그 칸은 env 기본값을 쓴다) -- 화면 입력칸의 현재 값.
        "portal": {f: row.get(f) for f in FIELDS},
        # env·기본값(비밀 제외) -- 화면이 "비우면 이 값" 으로 보여 준다.
        "env": {"backend": settings.mailer_backend, "relay_scheme": env_scheme or "http",
                "relay_host": env_host, "relay_port": env_port or 8025,
                # env URL 원문 -- 포탈이 주소 칸을 다 비우면 이것을 그대로 쓴다(화면이 서버와 같은 규칙으로 주소를
                # 조립해 "주소가 바뀌나"를 판단한다). 비밀 아님.
                "relay_url": (settings.mail_relay_url or "").strip(),
                "timeout_seconds": settings.mail_relay_timeout_seconds,
                "service_name": settings.mail_service_name},
        "token": {"configured": bool(cfg.relay_token), "source": cfg.token_source,
                  "unreadable": cfg.token_unreadable,
                  "env_configured": bool((settings.mail_relay_token or "").strip()),
                  # env 키가 있지만 포탈이 주소를 바꿔 env 키를 쓰지 않는 중(포탈에 키를 넣어야 한다).
                  "env_unbound": cfg.env_token_unbound},
        "endpoint_source": cfg.endpoint_source,
        "email_domain": settings.account_email_domain,
        "backends": list(MAILER_BACKENDS),
        "updated_at": row.get("updated_at"), "updated_by": row.get("updated_by"),
    }


def _token_from(request: Request, body: MailSettingsBody) -> "str | None":
    if body.relay_token_enc is not None:
        try:
            return request.app.state.password_transport.decrypt(
                body.relay_token_enc.model_dump(), purpose=SEAL_PURPOSE, username=SEAL_SUBJECT)
        except PasswordTransportError as e:
            raise HTTPException(status_code=422, detail=e.reason_code)
    if body.relay_token is None:
        return None
    if request.app.state.settings.password_encryption_required:
        raise HTTPException(status_code=422, detail="password_encryption_required")
    return body.relay_token


@router.get("/api/admin/mail-settings")
def get_mail_settings(request: Request):
    return _view(request)


@router.put("/api/admin/mail-settings")
def put_mail_settings(body: MailSettingsBody, request: Request,
                      identity: Identity = Depends(require_admin)):
    _require_session(identity)
    sent = body.model_fields_set
    changes: dict = {}
    try:
        for name, validator in _VALIDATORS.items():
            if name not in sent:
                continue
            value = getattr(body, name)
            if isinstance(value, str) and value.strip() == "":
                value = None
            changes[name] = None if value is None else validator(value)
        token = _token_from(request, body)
        if token is not None and body.clear_relay_token:
            raise HTTPException(status_code=422, detail="mail_relay_token_conflict")
        if token is not None:
            changes["relay_token_enc"] = seal_token(validate_token(token), request.app.state.settings)
        elif body.clear_relay_token:
            changes["relay_token_enc"] = None
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    # 토큰은 주소에 묶인다(2026-10-01 리뷰): 포탈에 저장된 키가 있는데 릴레이 주소를 바꾸면 같은 요청에서 키를
    # 다시 넣어야 한다 -- 아니면 관리자 권한만으로 주소를 자기 서버로 돌리고 테스트 메일로 키를 받아낼 수 있다.
    # 검사는 저장 트랜잭션 안에서 잠근 행으로 한다(repositories.mail_settings.update 의 guard).
    settings = request.app.state.settings

    def endpoint_token_guard(row: dict) -> None:
        before = resolve_from_row(row, settings).relay_url
        after = resolve_from_row({**row, **changes}, settings).relay_url
        if token is not None and body.seen_relay_url is not None and before != body.seen_relay_url.strip():
            raise HTTPException(status_code=409, detail="mail_settings_changed")
        if (after != before and row.get("relay_token_enc") and token is None
                and not body.clear_relay_token):
            raise HTTPException(status_code=422, detail="mail_relay_token_required")

    request.app.state.repos.mail_settings.update(changes, actor=audit_actor(identity), guard=endpoint_token_guard)
    return _view(request)


@router.post("/api/admin/mail-settings/health-check")
def mail_health_check(request: Request, identity: Identity = Depends(require_admin)):
    """릴레이가 살아 있나(GET /healthz). 실패도 200 + ok:false -- 진단 화면이 사유(reason)로 조치를 안내한다.
    토큰을 싣지 않는다 -- 인증 키·허용 IP 는 테스트 메일(POST /send)이 확인한다."""
    _require_session(identity)
    cfg = resolve_mail_config(request.app.state.repos, request.app.state.settings)
    try:
        check_relay_health(relay_url=cfg.relay_url,
                           timeout=min(cfg.timeout_seconds, HEALTH_TIMEOUT_SECONDS))
    except MailerError as e:
        return {"ok": False, "reason": e.reason, "detail": e.detail, "relay_url": cfg.relay_url}
    return {"ok": True, "relay_url": cfg.relay_url}


@router.post("/api/admin/mail-settings/test-mail")
def mail_test(body: TestMailBody, request: Request, identity: Identity = Depends(require_admin)):
    """테스트 메일 한 통. 받는 사람 생략 = 요청한 관리자의 파생 주소(<아이디>@도메인). 인증번호 메일과 같은
    릴레이·토큰·타임아웃을 쓴다 -- 성공하면 인증 메일 경로도 산다. 실패는 200 + ok:false + reason."""
    _require_session(identity)
    repos = request.app.state.repos
    settings = request.app.state.settings
    cfg = resolve_mail_config(repos, settings)
    to = (body.to or "").strip() or _derived_email(settings, identity.actor)
    if not _EMAIL_RE.match(to):
        raise HTTPException(status_code=422, detail="invalid_mail_recipient")
    actor = audit_actor(identity)
    subject, html = render_test_email(service_name=cfg.service_name, requested_by=actor)
    try:
        if cfg.token_unreadable:
            raise MailerError("relay_misconfigured", _UNREADABLE)
        send_mail_via_relay(relay_url=cfg.relay_url, token=cfg.relay_token, to=to,
                            subject=subject, body=html, content_type="HTML",
                            timeout=cfg.timeout_seconds)
    except MailerError as e:
        repos.observability.record_event(
            component="mailer", severity="warning", event_type="mail_test_failed",
            message=f"{to} -- {e} (by {actor})")
        return {"ok": False, "to": to, "reason": e.reason, "detail": e.detail,
                "retry_after": e.retry_after}
    repos.observability.record_event(
        component="mailer", severity="info", event_type="mail_test_sent",
        message=f"{to} -- sent via relay (by {actor})")
    return {"ok": True, "to": to}
