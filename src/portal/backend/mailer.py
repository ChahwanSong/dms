"""Verification-mail delivery — a pluggable seam, plus a dev-only log provider.

The portal proves a user controls ``<id>@PORTAL_EMAIL_DOMAIN`` by mailing them a
6-digit code. *How* that mail physically leaves the building is deliberately the only
part of the flow that is swappable: everything else (code generation, HMAC storage,
rate limits, one-live-code-per-id, enumeration-safe responses) is provider-agnostic
and must not be re-implemented per provider.

╔══════════════════════════════════════════════════════════════════════════════╗
║  회사 메일 연동 지점 — 구현할 것은 `deliver_company_mail()` 함수 하나뿐이다.  ║
║  다른 파일은 건드릴 필요가 없다. 상세 가이드:                                 ║
║  install/portal-02-user-auth.md §5                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝

Providers (``PORTAL_EMAIL_DELIVERY``):

* ``none``    — 배송 수단 없음. 인증요청이 503으로 **fail-closed** 된다(기본값).
* ``log``     — 인증번호를 서버 로그로만 출력하는 **개발 전용** 경로. 메일 연동 전에
                화면/플로우를 그대로 테스트하기 위한 것이며, 코드가 로그에 평문으로
                남으므로 ``PORTAL_ALLOW_INSECURE_DEFAULTS``와 이중 게이트다.
* ``company`` — 사내 메일 발송 시스템. ``deliver_company_mail()``에 구현한다.

SMTP 직접 발송은 의도적으로 제거했다. 사내 발송은 SMTP 릴레이가 아니라 사내 API를
경유할 예정이라, 쓰지도 않을 릴레이 설정(host/port/TLS/자격증명)을 남겨두면 설정
표면과 비밀값만 늘어난다. SMTP가 필요해지면 ``deliver_company_mail()`` 안에서
``smtplib``을 쓰면 되고(§5 참고), 그때도 이 파일 밖은 바뀌지 않는다.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from .email_codes import PURPOSE_REGISTER

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import Settings

log = logging.getLogger("portal.mailer")

# Provider ids. Kept here (not in config.py) so adding a provider touches one module.
DELIVERY_NONE = "none"
DELIVERY_LOG = "log"
DELIVERY_COMPANY = "company"
DELIVERY_PROVIDERS = (DELIVERY_NONE, DELIVERY_LOG, DELIVERY_COMPANY)

KIND_VERIFICATION_CODE = "verification_code"
KIND_ALREADY_REGISTERED = "already_registered"


class EmailNotConfigured(RuntimeError):
    """No usable delivery provider. Callers must fail closed (503), never fall back
    to creating an account without proof of mailbox control."""


@dataclass(frozen=True)
class OutboundEmail:
    """One mail to send, already rendered.

    Plain text only and no attachments — deliberately the smallest shape every
    delivery channel can accept (SMTP, an internal REST API, a message queue), so a
    new provider never has to care how the body was built.

    ``code`` is carried alongside the rendered ``body`` for providers whose API takes
    template variables instead of a body string. It is None for non-code mail.
    """

    to_addr: str
    subject: str
    body: str
    kind: str
    code: str | None = None


def address_for(settings: "Settings", username: str) -> str:
    """The one place a recipient address is built.

    The UI hint ("<id>@<domain> 으로 인증메일이 발송됩니다") and the actual recipient
    both come from here, so they can never drift apart and mislead the user about
    where their code went. The domain comes from config only — never from user input,
    which is what keeps "the code reaches a company mailbox" true.
    """
    return f"{username}@{settings.email_domain}"


def build_code_message(
    settings: "Settings", *, to_addr: str, code: str, purpose: str
) -> OutboundEmail:
    """The verification mail.

    The code is deliberately NOT in the subject — subjects surface in lock-screen
    notification previews. The body contains no URL at all, so the mail cannot train
    users to click links in "your verification code" mail (the phishing pattern it
    would otherwise mirror).
    """
    is_register = purpose == PURPOSE_REGISTER
    minutes = max(1, settings.email_code_ttl_seconds // 60)
    action = "계정 생성" if is_register else "비밀번호 재설정"
    return OutboundEmail(
        to_addr=to_addr,
        subject=(
            "[DMS 포탈] 계정 생성 인증번호"
            if is_register
            else "[DMS 포탈] 비밀번호 재설정 인증번호"
        ),
        body=(
            f"DMS 포탈 {action} 인증번호입니다.\n"
            f"\n"
            f"    {code}\n"
            f"\n"
            f"포탈 화면에 이 인증번호를 입력하세요. 유효시간은 {minutes}분입니다.\n"
            f"\n"
            f"본인이 요청하지 않았다면 이 메일을 무시하세요. 이 메일만으로는 계정이\n"
            f"만들어지거나 비밀번호가 바뀌지 않습니다.\n"
        ),
        kind=KIND_VERIFICATION_CODE,
        code=code,
    )


def build_already_registered_message(
    settings: "Settings", *, to_addr: str
) -> OutboundEmail:
    """Sent instead of a code when signup is requested for an existing account.

    This is what makes the "always 202, never say whether the id exists" response
    safe: the distinguishing information still reaches the legitimate owner, through
    the mailbox only they can read rather than the HTTP response.
    """
    return OutboundEmail(
        to_addr=to_addr,
        subject="[DMS 포탈] 계정 생성 요청 안내",
        body=(
            "DMS 포탈에 이 주소로 계정 생성 요청이 접수되었습니다.\n"
            "\n"
            "이미 계정이 있어 새로 만들지 않았습니다. 비밀번호가 기억나지 않으면\n"
            "로그인 화면의 '비밀번호 재설정'을 이용하세요.\n"
            "\n"
            "본인이 요청하지 않았다면 이 메일을 무시하세요.\n"
        ),
        kind=KIND_ALREADY_REGISTERED,
    )


# ===========================================================================
# ★ 회사 메일 연동 지점 — 여기만 구현하면 된다 ★
# ===========================================================================
async def deliver_company_mail(settings: "Settings", email: OutboundEmail) -> None:
    """사내 메일 발송 시스템으로 `email`을 보낸다. (미구현 — 여기를 채우세요)

    구현 계약
    ---------
    * 성공하면 그냥 반환한다. 실패하면 **예외를 던진다** — 호출자가 방금 발급한
      인증번호를 무효화해서 "메일은 안 갔는데 코드는 살아있는" 상태를 막는다.
    * 반드시 **논블로킹**이어야 한다. 포탈은 단일 이벤트 루프에서 백업/스캔/싱크
      오케스트레이터와 대시보드 샘플러를 함께 돌리므로, 동기 라이브러리(`smtplib`,
      `requests` 등)를 쓴다면 `await asyncio.to_thread(...)`로 감싸고 **반드시
      타임아웃을 지정**한다(`to_thread`는 취소가 불가능해 타임아웃이 유일한 상한이다).
      httpx를 쓴다면 `app.state`에 만들어 둔 클라이언트를 재사용한다.
    * 자격증명·수신자 전체 주소·인증번호를 **로그에 남기지 않는다**. Pod 로그는
      광범위하게 열람된다.

    쓸 수 있는 값
    -------------
    * `email.to_addr` — 수신자 (`<아이디>@PORTAL_EMAIL_DOMAIN`)
    * `email.subject` / `email.body` — 이미 렌더된 한국어 제목·본문(평문)
    * `email.kind` — `verification_code` | `already_registered`
    * `email.code` — 인증번호(사내 API가 본문 대신 템플릿 변수를 받는 경우 사용)
    * `settings.*` — 새 설정이 필요하면 `config.py`에 `PORTAL_*`로 추가한다
      (비밀값은 매니페스트에 placeholder만 두고 라이브 Secret에 주입: §6)

    구현 후에는 `PORTAL_EMAIL_DELIVERY=company`로 바꾸고, 개발용 `log` 경로는 끈다.
    **REST/SMTP 예시 골격과 설정 절차는 install/portal-02-user-auth.md §5**에 있다.
    """
    raise EmailNotConfigured(
        "PORTAL_EMAIL_DELIVERY=company 이지만 deliver_company_mail()이 아직 "
        "구현되지 않았습니다. src/portal/backend/mailer.py 를 참고하세요."
    )


async def _deliver_log(settings: "Settings", email: OutboundEmail) -> None:
    """Dev-only: write the code to the server log instead of mailing it.

    The code goes to the LOG and never to the HTTP response — putting it in a
    response would turn this endpoint into remote account takeover for anyone who can
    reach the portal. Reachable only with PORTAL_ALLOW_INSECURE_DEFAULTS as well
    (create_app refuses to boot otherwise).
    """
    # Both at WARNING: the portal configures no logging handlers, so Python's
    # lastResort handler is what reaches the pod log — and it only emits WARNING and
    # above. An INFO line here would be silently dropped, making "did the mail go
    # out?" unanswerable in exactly the mode that exists to answer it.
    if email.kind == KIND_VERIFICATION_CODE:
        log.warning(
            "SECURITY: DEV CODE ECHO (개발 전용) to=%s subject=%s code=%s",
            email.to_addr,
            email.subject,
            email.code,
        )
    else:
        log.warning(
            "DEV MAIL (개발 전용, 인증번호 아님) to=%s subject=%s",
            email.to_addr,
            email.subject,
        )


async def deliver(settings: "Settings", email: OutboundEmail) -> str:
    """Send one mail via the configured provider. Returns the provider id used.

    Raises EmailNotConfigured when there is no usable provider — callers must fail
    closed rather than proceeding without proof of mailbox control.
    """
    provider = settings.email_delivery
    if provider == DELIVERY_LOG and settings.allow_insecure_defaults:
        await _deliver_log(settings, email)
        return DELIVERY_LOG
    if provider == DELIVERY_COMPANY:
        await deliver_company_mail(settings, email)
        return DELIVERY_COMPANY
    raise EmailNotConfigured(f"no usable email delivery provider (provider={provider!r})")


async def send_code(
    settings: "Settings", *, to_addr: str, code: str, purpose: str
) -> str:
    """Render and deliver a verification code."""
    return await deliver(
        settings,
        build_code_message(settings, to_addr=to_addr, code=code, purpose=purpose),
    )
