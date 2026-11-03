"""가입·비밀번호 변경 완료 안내 메일(2026-11-03, 설계 v6 §5 「선택 4종」) -- **최선 노력**이다.

보내는 경우는 셋뿐이다: 셀프 가입(signup), 셀프 비밀번호 재설정(password_reset), 관리자 비밀번호 재설정
(password_admin_reset). 관리자 계정 생성에는 보내지 않는다(관리자가 직접 비밀번호를 전달한다 -- 사용자 결정).

규칙(「왜」):
- 응답 **뒤에** 보낸다(라우트가 BackgroundTasks 로 예약 -- 응답이 나간 뒤 스레드풀에서 돈다). 릴레이가 느리거나 죽어도
  가입·변경 응답은 늦어지지 않는다.
- send_account_notice 는 **절대 예외를 올리지 않는다**. 메일 실패로 가입·변경을 되돌리거나 막지 않는다 -- 결과는
  account_notice_sent·_stub·_failed·_skipped 이벤트로만 남는다(관리 → 보안 이벤트).
- 인증 메일과 **같은** 수신자·전체 상한(app.state.mail_throttle·mail_throttle_global)과 발송 슬롯(mail_send_slots)을
  거친다. 셋 다 통과할 때만 둘 다 기록한다(routes_auth.request_verification_code 와 같은 「모두 통과할 때만 모두 기록」 --
  앞 상한만 태우고 뒤에서 거절하면 남의 몫이 줄었다). 못 거치면 기다리지 않고 건너뛴다(skipped).
- 본문에 비밀번호·인증번호·IP 를 넣지 않는다(mailer.render_account_notice_email). 이벤트에도 넣지 않는다 -- payload 의
  client_ip/peer 는 요청 출발지 기록(보안 이벤트 공통)이고 메일 본문과 무관하다.
"""
from __future__ import annotations

import logging

from ..mail_config import MAILER_BACKENDS, resolve_mail_config
from .mailer import MailerError, render_account_notice_email, send_mail_via_relay

logger = logging.getLogger(__name__)

NOTICE_KINDS = ("signup", "password_reset", "password_admin_reset")


def notice_mail_mode(repos, settings) -> str:
    """관리자 재설정 응답의 notice_mail -- 지금 메일 설정으로 안내 메일이 어떻게 될지(라우트가 바로 정한다):
    knox_relay = "queued"(보낼 예정 -- 실제 결과는 이벤트), stub = "stub"(보내지 않음), 그 밖 = "unavailable"(설정 오류)."""
    backend = resolve_mail_config(repos, settings).backend
    if backend == "knox_relay":
        return "queued"
    if backend == "stub":
        return "stub"
    return "unavailable"


def _record(repos, event_type: str, severity: str, message: str, payload: dict) -> None:
    repos.observability.record_event(component="mailer", severity=severity, event_type=event_type,
                                     message=message, payload=payload)


def send_account_notice(state, *, kind: str, username: str, actor: str | None = None, origin: dict) -> None:
    """완료 안내 메일 한 통(BackgroundTasks 에서 부른다). 어떤 실패도 올리지 않는다 -- 예기치 못한 오류는
    account_notice_failed(reason internal_error)로 남긴다."""
    try:
        _send(state, kind=kind, username=username, actor=actor, origin=origin)
    except Exception as exc:  # noqa: BLE001 -- 최선 노력: 동작(가입·변경)은 이미 끝났다
        logger.warning("account notice failed kind=%s user=%s: %s", kind, username, type(exc).__name__)
        try:
            _record(state.repos, "account_notice_failed", "warning",
                    f"{username} ({kind}) -- notice mail not sent: internal error {type(exc).__name__}",
                    {"kind": kind, "username": username, "recipient": None, "reason": "internal_error",
                     **origin})
        except Exception:  # noqa: BLE001 -- record_event 는 원래 올리지 않지만, 여기서 새면 백그라운드 태스크가 죽는다
            pass


def _send(state, *, kind: str, username: str, actor: str | None, origin: dict) -> None:
    repos, settings = state.repos, state.settings
    account = repos.accounts.get(username)
    recipient = (account or {}).get("email") or f"{username}@{settings.account_email_domain}"
    base = {"kind": kind, "username": username, "recipient": recipient, **origin}
    what = f"{recipient} ({kind})"
    cfg = resolve_mail_config(repos, settings)
    if cfg.backend not in MAILER_BACKENDS:
        _record(repos, "account_notice_skipped", "warning",
                f"{what} -- notice mail skipped: mailer backend {cfg.backend!r} is not configured",
                {**base, "reason": "mailer_misconfigured"})
        return
    if cfg.backend == "stub":
        _record(repos, "account_notice_stub", "info", f"{what} -- stub, notice mail not actually sent", base)
        return
    if not state.mail_send_slots.acquire(blocking=False):
        _record(repos, "account_notice_skipped", "warning",
                f"{what} -- notice mail skipped: relay calls in flight", {**base, "reason": "busy"})
        return
    try:
        with state.mail_throttle_lock:
            blocked = state.mail_throttle.peek(recipient) or state.mail_throttle_global.peek("*")
            if not blocked:
                state.mail_throttle.record(recipient)
                state.mail_throttle_global.record("*")
        if blocked:
            _record(repos, "account_notice_skipped", "warning",
                    f"{what} -- notice mail skipped: mail limit reached (retry in {blocked}s)",
                    {**base, "reason": "throttled"})
            return
        if cfg.token_unreadable:
            _record(repos, "account_notice_failed", "warning",
                    f"{what} -- notice mail not sent: stored relay token cannot be decrypted",
                    {**base, "reason": "relay_misconfigured"})
            return
        subject, html_body = render_account_notice_email(
            kind=kind, username=username, service_name=cfg.service_name, actor=actor)
        try:
            send_mail_via_relay(relay_url=cfg.relay_url, token=cfg.relay_token, to=recipient,
                                subject=subject, body=html_body, content_type="HTML",
                                timeout=cfg.timeout_seconds)
        except MailerError as exc:
            # relay_no_response(갔을 수도 있다)도 실패로 남긴다 -- 안내 메일은 재시도하지 않으므로 「갔는지 모름」을 운영자가
            # 사유로 구분하면 된다.
            _record(repos, "account_notice_failed", "warning", f"{what} -- notice mail not sent: {exc}",
                    {**base, "reason": exc.reason})
            return
        _record(repos, "account_notice_sent", "info", f"{what} -- notice mail sent via knox_relay", base)
    finally:
        state.mail_send_slots.release()
