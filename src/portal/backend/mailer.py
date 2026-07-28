"""Verification-code email delivery (stdlib smtplib only — no new dependency).

Two things here are load-bearing and easy to get wrong:

**Blocking.** ``smtplib`` is a fully synchronous API. Calling it directly from an
async route would park the single uvicorn event loop for the whole TLS handshake +
EHLO/AUTH/DATA round trip — and that same loop also runs the Backup/Scan/Sync/Rm
orchestrators and the dashboard sampler. So every send goes through
``asyncio.to_thread`` AND sets an explicit socket timeout: ``to_thread`` cannot be
cancelled, so the timeout is the only real upper bound on a stuck relay.

**Secrets in logs.** ``smtplib.set_debuglevel()`` is never enabled: it prints the
AUTH exchange (base64 of the relay credentials) to stdout, and pod logs are widely
readable. Exception paths log the failure class, never the credentials.
"""

from __future__ import annotations

import asyncio
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
import logging
import smtplib
import ssl
from typing import TYPE_CHECKING

from .email_codes import PURPOSE_REGISTER

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import Settings

log = logging.getLogger("portal.mailer")


class EmailNotConfigured(RuntimeError):
    """No SMTP relay is configured and the dev echo path is not enabled."""


def address_for(settings: "Settings", username: str) -> str:
    """The one place a recipient address is built.

    The UI hint ("<id>@<domain> 으로 인증메일이 발송됩니다") and the actual envelope
    recipient both come from here, so they can never drift apart and mislead the
    user about where their code went. The domain comes from config only.
    """
    return f"{username}@{settings.email_domain}"


def _from_addr(settings: "Settings") -> str:
    # Gmail (and most relays) require the From address to match the authenticated
    # account; defaulting to smtp_user makes the common setup work with no extra env.
    return settings.smtp_from or settings.smtp_user or f"no-reply@{settings.email_domain}"


def build_code_message(
    settings: "Settings", *, to_addr: str, code: str, purpose: str
) -> EmailMessage:
    """The verification mail.

    The code is deliberately NOT in the subject — subjects surface in lock-screen
    notification previews, which would leak the code to anyone holding the phone.
    The body contains no URL at all, so the mail cannot train users to click
    links in "your verification code" mail (the phishing pattern it would mirror).
    """
    is_register = purpose == PURPOSE_REGISTER
    msg = EmailMessage()
    msg["From"] = formataddr((settings.smtp_from_name, _from_addr(settings)))
    msg["To"] = Address(addr_spec=to_addr)  # defence in depth; the regex is the primary gate
    msg["Subject"] = (
        "[DMS 포탈] 계정 생성 인증번호" if is_register else "[DMS 포탈] 비밀번호 재설정 인증번호"
    )
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings.email_domain or None)
    msg["Auto-Submitted"] = "auto-generated"  # suppress vacation auto-replies
    minutes = max(1, settings.email_code_ttl_seconds // 60)
    action = "계정 생성" if is_register else "비밀번호 재설정"
    msg.set_content(
        f"DMS 포탈 {action} 인증번호입니다.\n"
        f"\n"
        f"    {code}\n"
        f"\n"
        f"포탈 화면에 이 인증번호를 입력하세요. 유효시간은 {minutes}분입니다.\n"
        f"\n"
        f"본인이 요청하지 않았다면 이 메일을 무시하세요. 이 메일만으로는 계정이\n"
        f"만들어지거나 비밀번호가 바뀌지 않습니다.\n"
    )
    return msg


def build_already_registered_message(settings: "Settings", *, to_addr: str) -> EmailMessage:
    """Sent instead of a code when signup is requested for an existing account.

    This is what makes the "always 202, never say whether the id exists" response
    safe to use: the distinguishing information still reaches the legitimate owner,
    just through the mailbox only they can read rather than the HTTP response.
    """
    msg = EmailMessage()
    msg["From"] = formataddr((settings.smtp_from_name, _from_addr(settings)))
    msg["To"] = Address(addr_spec=to_addr)
    msg["Subject"] = "[DMS 포탈] 계정 생성 요청 안내"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings.email_domain or None)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(
        "DMS 포탈에 이 주소로 계정 생성 요청이 접수되었습니다.\n"
        "\n"
        "이미 계정이 있어 새로 만들지 않았습니다. 비밀번호가 기억나지 않으면\n"
        "로그인 화면의 '비밀번호 재설정'을 이용하세요.\n"
        "\n"
        "본인이 요청하지 않았다면 이 메일을 무시하세요.\n"
    )
    return msg


def _send_sync(settings: "Settings", msg: EmailMessage, to_addr: str) -> None:
    """BLOCKING. Always called via asyncio.to_thread."""
    timeout = settings.smtp_timeout_seconds
    host, port = settings.smtp_host, settings.smtp_port
    if settings.smtp_security == "ssl":
        client = smtplib.SMTP_SSL(
            host, port, timeout=timeout, context=ssl.create_default_context()
        )
    else:
        client = smtplib.SMTP(host, port, timeout=timeout)
    try:
        client.ehlo()
        if settings.smtp_security == "starttls":
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if settings.smtp_user:
            client.login(settings.smtp_user, settings.smtp_password or "")
        # Pass the envelope recipient explicitly rather than letting smtplib derive
        # it from the headers.
        client.send_message(msg, to_addrs=[to_addr])
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001 - teardown must not mask a send error
            pass


async def send_message(settings: "Settings", *, to_addr: str, msg: EmailMessage) -> None:
    await asyncio.to_thread(_send_sync, settings, msg, to_addr)


async def send_code(
    settings: "Settings", *, to_addr: str, code: str, purpose: str
) -> str:
    """Deliver a verification code. Returns the delivery channel used.

    With no relay configured this raises (fail-closed) rather than letting an
    account be created without proving mailbox ownership. The dev echo path writes
    the code to the server log ONLY — putting it in an HTTP response would turn
    this endpoint into remote account takeover for anyone who can reach the portal.
    """
    if not settings.email_configured:
        if settings.email_dev_echo and settings.allow_insecure_defaults:
            log.warning(
                "SECURITY: DEV CODE ECHO (개발 전용) to=%s purpose=%s code=%s",
                to_addr,
                purpose,
                code,
            )
            return "dev-echo"
        raise EmailNotConfigured("smtp relay is not configured")
    await send_message(
        settings, to_addr=to_addr, msg=build_code_message(
            settings, to_addr=to_addr, code=code, purpose=purpose
        )
    )
    return "smtp"
