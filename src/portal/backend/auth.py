"""Authentication routes for the portal BFF.

Both roles authenticate with an id/password; the role is decided by **which store
the credential matched**, not by the login method (see security.py):

- ``operator`` — ``portal.operator_users``. Account creation / password reset is
  gated by the shared operational secret (``PORTAL_ADMIN_TOKEN``).
- ``user`` — ``portal.user_accounts``. Self-service: the id is the company-mail
  local part, and creating or re-keying an account requires a 6-digit code mailed
  to ``<id>@PORTAL_EMAIL_DOMAIN``. There is no admin token in this path — proving
  control of the mailbox IS the authorization.

The former dummy AD login (``/login/ad``) is gone. It accepted **any** id without
verifying anything, and that id flows through to DMS as the POSIX execution
identity, so it was an authentication bypass rather than a placeholder.

Session state lives in Starlette's signed cookie (``request.session``); no DB.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from .config import Settings
from .db import hash_password, verify_password
from .email_codes import (
    ALLOWED_PURPOSES,
    PURPOSE_REGISTER,
    PURPOSE_RESET,
    burn_dummy,
    code_digest,
    generate_code,
    normalize_username,
)
from .mailer import (
    EmailNotConfigured,
    address_for,
    build_already_registered_message,
    build_code_message,
    send_code,
    send_message,
)
from .security import (
    ROLE_OPERATOR,
    ROLE_USER,
    current_user,
    session_user,
)

log = logging.getLogger("portal.auth")

# New operator ids created from the login screen must match this; the bootstrap
# `admin` account (seeded from PORTAL_OPERATOR_USERS) is grandfathered for login.
USERNAME_RE = re.compile(r"^admin_[a-z0-9_]{2,30}$")
MIN_PASSWORD_LEN = 8


class LoginRequest(BaseModel):
    username: str
    password: str


class UserLoginRequest(BaseModel):
    username: str
    password: str


class RequestCodeRequest(BaseModel):
    """Ask for a verification code. Deliberately does NOT accept a password.

    ``extra="forbid"`` is the enforcement: if a password were accepted here, the
    server would be holding an unverified credential chosen by whoever made the
    request, while the code goes to the mailbox owner — i.e. an attacker could set
    the password on someone else's pending account. Collecting it only at the
    confirm step keeps "who proved the mailbox" and "who chose the password" the
    same person.
    """

    model_config = ConfigDict(extra="forbid")

    username: str
    purpose: str


class UserRegisterRequest(BaseModel):
    username: str
    password: str
    code: str


class UserResetRequest(BaseModel):
    username: str
    new_password: str
    code: str


class RegisterRequest(BaseModel):
    """Create an operator account from the login screen. Gated by the shared
    operational secret token (PORTAL_ADMIN_TOKEN), NOT a login session."""
    username: str
    password: str
    token: str


class ResetPasswordRequest(BaseModel):
    """Reset ('찾기'/변경) an operator's password from the login screen — the
    password is one-way hashed, so there is no recovery, only a token-gated reset."""
    username: str
    new_password: str
    token: str


def _require_admin_token(settings: Settings, token: str) -> None:
    """Verify the shared operational secret. 503 if the feature isn't configured
    (no PORTAL_ADMIN_TOKEN), 403 if the presented token is wrong."""
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="account_token_not_configured")
    if not hmac.compare_digest(token, settings.admin_token):
        raise HTTPException(status_code=403, detail="invalid_token")


def _check_username(username: str) -> None:
    if not USERNAME_RE.match(username):
        raise HTTPException(
            status_code=422,
            detail="invalid_username ('admin_' 접두어 + 소문자/숫자/밑줄, 예: admin_ops)",
        )


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=422, detail=f"password_too_short (최소 {MIN_PASSWORD_LEN}자)"
        )


def _require_db(request: Request):
    db = getattr(request.app.state, "db", None)
    if db is None or not db.configured:
        raise HTTPException(status_code=503, detail="portal_db_not_configured")
    return db


def _verify_operator(settings: Settings, username: str, password: str) -> bool:
    expected = settings.operator_users.get(username)
    if expected is None:
        # Still compare against a dummy value to keep timing roughly uniform.
        hmac.compare_digest(password, "\0")
        return False
    return hmac.compare_digest(password, expected)


async def _hash_async(password: str) -> str:
    """PBKDF2 (240k iters) is ~100ms of CPU. These endpoints are unauthenticated, so
    running it inline would let plain requests stall the single event loop — and that
    loop also drives the backup/scan/sync orchestrators."""
    return await asyncio.to_thread(hash_password, password)


async def _verify_async(password: str, stored: str) -> bool:
    return await asyncio.to_thread(verify_password, password, stored)


def auth_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post("/login")
    async def login(payload: LoginRequest, request: Request) -> dict[str, Any]:
        """Operator id/password login (operator-only).

        Verified against the portal DB's ``operator_users`` (PBKDF2-hashed,
        seeded from PORTAL_OPERATOR_USERS) when a DB is configured; otherwise
        against the in-memory env store.
        """
        db = getattr(request.app.state, "db", None)
        if db is not None and db.configured:
            rec = await db.operator_auth_record(payload.username)
            # a disabled (is_active=false) account can't log in even with the
            # right password; treat it like an invalid credential.
            ok = (
                rec is not None
                and rec.get("is_active", True)
                # off the event loop: PBKDF2 240k is ~100ms and this route is public
                and await _verify_async(payload.password, rec["password_hash"])
            )
        else:
            ok = _verify_operator(settings, payload.username, payload.password)
        if not ok:
            raise HTTPException(status_code=401, detail="invalid_credentials")
        user = session_user(payload.username, ROLE_OPERATOR, method="local")
        # Drop anything left in the pre-login session so no key survives the
        # privilege change.
        request.session.clear()
        request.session["user"] = user
        return {"user": user}

    @router.post("/logout")
    def logout(request: Request) -> dict[str, str]:
        request.session.clear()
        return {"status": "ok"}

    @router.get("/me")
    def me(request: Request) -> dict[str, Any]:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="not_authenticated")
        return {"user": user}

    @router.get("/account-token-required")
    def account_token_required() -> dict[str, Any]:
        """Whether the login-screen 계정 만들기 / 비밀번호 재설정 flows are available
        (i.e. PORTAL_ADMIN_TOKEN is configured). Public — lets the login SPA show or
        hide those tabs. Never returns the token itself."""
        return {"available": bool(settings.admin_token)}

    @router.post("/register")
    async def register(payload: RegisterRequest, request: Request) -> dict[str, Any]:
        """Create an operator account from the login screen, gated ONLY by the
        shared operational secret token (no login session required). New ids must
        use the `admin_` prefix; the password is stored PBKDF2-hashed."""
        _require_admin_token(settings, payload.token)
        db = _require_db(request)
        username = payload.username.strip()
        _check_username(username)
        _check_password(payload.password)
        created = await db.create_operator(username, payload.password, created_by="self-register")
        if not created:
            raise HTTPException(status_code=409, detail="username_exists")
        return {"registered": username}

    @router.post("/reset-password")
    async def reset_password(payload: ResetPasswordRequest, request: Request) -> dict[str, Any]:
        """Reset an operator's password from the login screen, gated ONLY by the
        shared operational secret token. 404 if the username doesn't exist."""
        _require_admin_token(settings, payload.token)
        db = _require_db(request)
        _check_password(payload.new_password)
        n = await db.set_operator_password(payload.username.strip(), payload.new_password)
        if not n:
            raise HTTPException(status_code=404, detail="operator_not_found")
        return {"reset": payload.username.strip()}

    # ------------------------------------------------------------------
    # End-user self-service (email-verified). All under /api/auth/user/.
    # ------------------------------------------------------------------

    @router.get("/user/config")
    def user_config() -> dict[str, Any]:
        """Public. Lets the login SPA build the "<id>@<domain> 으로 인증메일이
        발송됩니다" hint and size its inputs without hard-coding anything — so
        switching gmail.com -> the company domain at install time needs no rebuild.

        Never exposes the SMTP host/user/password.
        """
        delivery = (
            "smtp"
            if settings.email_configured
            else ("dev-echo" if settings.email_dev_echo else "none")
        )
        return {
            "available": delivery != "none",
            "email_domain": settings.email_domain,
            "code_length": settings.email_code_length,
            "code_ttl_seconds": settings.email_code_ttl_seconds,
            "resend_cooldown_seconds": settings.email_resend_cooldown_seconds,
            "min_password_len": MIN_PASSWORD_LEN,
            "signup_enabled": settings.user_signup_enabled,
            "email_delivery": delivery,
        }

    @router.post("/user/request-code", status_code=202)
    async def user_request_code(
        payload: RequestCodeRequest, request: Request, background: BackgroundTasks
    ) -> dict[str, Any]:
        """Mail a verification code. ALWAYS answers 202 with the same body.

        Whether the id exists is never revealed here: doing so would turn this into a
        directory scanner that needs no mail delivery at all. The distinguishing
        information is moved into the mailbox instead — an existing account gets an
        "you already have an account" mail rather than a code.
        """
        purpose = (payload.purpose or "").strip()
        if purpose not in ALLOWED_PURPOSES:
            raise HTTPException(status_code=422, detail="invalid_purpose")
        username = normalize_username(
            payload.username, settings, enforce_allowlist=(purpose == PURPOSE_REGISTER)
        )
        if purpose == PURPOSE_REGISTER and not settings.user_signup_enabled:
            raise HTTPException(
                status_code=403,
                detail="signup_disabled (사용자 계정 생성이 비활성화되어 있습니다)",
            )
        # Fail closed BEFORE minting anything: no relay means no proof of ownership
        # is possible, and creating an account without that proof is the one thing
        # this flow must never do.
        if not settings.email_configured and not (
            settings.email_dev_echo and settings.allow_insecure_defaults
        ):
            raise HTTPException(
                status_code=503,
                detail="email_not_configured (메일 발송이 설정되지 않았습니다. 관리자에게 문의하세요)",
            )
        db = _require_db(request)

        # Relay-abuse kill switch: per-id limits alone cannot stop "10k ids x 5 mails",
        # and getting the portal flagged as a spam source would kill the feature.
        if await db.global_sends_last_hour(settings.email_send_window_seconds) >= (
            settings.email_global_max_sends_per_hour
        ):
            log.warning("email send quota exceeded (global hourly cap reached)")
            raise HTTPException(
                status_code=429,
                detail="send_quota_exceeded (메일 발송 한도를 초과했습니다. 잠시 후 다시 시도하세요)",
            )

        to_addr = address_for(settings, username)
        exists = await db.user_exists(username)
        ttl = settings.email_code_ttl_seconds
        cooldown = settings.email_resend_cooldown_seconds
        same_answer = {
            "requested": True,
            "expires_in": ttl,
            "resend_after": cooldown,
        }

        # The verification row is written on EVERY path — including the ones that will
        # not mail a code. It is what enforces the cooldown/quota, so skipping it for
        # "account already exists" would (a) let that path be replayed without limit
        # (a mail bomb aimed at one address, invisible to the global cap because it
        # never increments `sends`) and (b) leak existence outright: the skipped path
        # answers 202 forever while the other one starts answering 429 on the second
        # call. Same row, same limits, same responses — whatever the account state.
        code = generate_code(settings.email_code_length)
        # A reset for an unknown id sends nothing; everything else does (a code, or the
        # "you already have an account" notice).
        will_mail = not (purpose == PURPOSE_RESET and not exists)
        issued = await db.issue_verification(
            username=username,
            purpose=purpose,
            email=to_addr,
            code_hash=code_digest(
                settings, purpose=purpose, username=username, code=code
            ),
            ttl_seconds=ttl,
            cooldown_seconds=cooldown,
            window_seconds=settings.email_send_window_seconds,
            max_sends=settings.email_max_sends_per_window,
            failure_window_seconds=settings.email_failure_window_seconds,
            max_failures=settings.email_max_failures_per_window,
            will_mail=will_mail,
            requested_ip=(request.client.host if request.client else None),
        )
        if issued is None:
            # One status for three causes (cooldown / hourly send cap / failure budget)
            # so the response cannot be used to probe account state — but say so, since
            # the real wait ranges from a minute to the end of the hour window.
            raise HTTPException(
                status_code=429,
                detail=(
                    "resend_too_soon (재발송은 "
                    f"{cooldown}초 간격입니다. 요청/실패가 반복된 경우 최대 "
                    f"{max(1, settings.email_send_window_seconds // 60)}분 후 다시 시도할 수 있습니다)"
                ),
                headers={"Retry-After": str(cooldown)},
            )

        if purpose == PURPOSE_REGISTER and exists:
            # Tell the owner (via their mailbox, not the HTTP response) that they
            # already have an account. The code just minted stays unused and expires.
            burn_dummy(settings, purpose=purpose, username=username)
            if settings.email_configured:
                background.add_task(
                    _send_quietly,
                    settings,
                    to_addr,
                    build_already_registered_message(settings, to_addr=to_addr),
                )
            return same_answer
        if purpose == PURPOSE_RESET and not exists:
            # Nothing to reset, so nothing is mailed — but the row above already
            # charged this request against the same limits.
            burn_dummy(settings, purpose=purpose, username=username)
            return same_answer

        # Deliver in the background so a slow relay neither holds the response nor
        # makes the "account exists" path measurably faster than this one.
        background.add_task(
            _deliver_code, settings, db, username, purpose, to_addr, code
        )
        return same_answer

    @router.post("/user/register", status_code=201)
    async def user_register(payload: UserRegisterRequest, request: Request) -> dict[str, Any]:
        """Redeem a signup code and create the account (one atomic statement)."""
        username = normalize_username(payload.username, settings)
        _check_password(payload.password)
        db = _require_db(request)
        digest = code_digest(
            settings,
            purpose=PURPOSE_REGISTER,
            username=username,
            code=(payload.code or "").strip(),
        )
        # Filter junk codes BEFORE paying for PBKDF2 (see verification_matches).
        if not await db.verification_matches(
            username=username,
            purpose=PURPOSE_REGISTER,
            code_hash=digest,
            max_attempts=settings.email_code_max_attempts,
        ):
            await _fail_code(db, settings, username, PURPOSE_REGISTER)
        password_hash = await _hash_async(payload.password)
        result = await db.consume_verification_register(
            username=username,
            code_hash=digest,
            password_hash=password_hash,
            max_attempts=settings.email_code_max_attempts,
        )
        if not result.get("matched"):
            await _fail_code(db, settings, username, PURPOSE_REGISTER)
        if not result.get("created"):
            raise HTTPException(
                status_code=409, detail="username_exists (이미 존재하는 아이디입니다)"
            )
        return {"registered": username}

    @router.post("/user/reset-password")
    async def user_reset_password(
        payload: UserResetRequest, request: Request
    ) -> dict[str, Any]:
        """Redeem a reset code and re-key the account (one atomic statement)."""
        username = normalize_username(payload.username, settings, enforce_allowlist=False)
        _check_password(payload.new_password)
        db = _require_db(request)
        digest = code_digest(
            settings,
            purpose=PURPOSE_RESET,
            username=username,
            code=(payload.code or "").strip(),
        )
        if not await db.verification_matches(
            username=username,
            purpose=PURPOSE_RESET,
            code_hash=digest,
            max_attempts=settings.email_code_max_attempts,
        ):
            await _fail_code(db, settings, username, PURPOSE_RESET)
        password_hash = await _hash_async(payload.new_password)
        result = await db.consume_verification_reset(
            username=username,
            code_hash=digest,
            password_hash=password_hash,
            max_attempts=settings.email_code_max_attempts,
        )
        if not result.get("matched"):
            await _fail_code(db, settings, username, PURPOSE_RESET)
        if not result.get("updated"):
            # Code was valid but the account is gone/disabled.
            raise HTTPException(
                status_code=404, detail="user_not_found (계정을 찾을 수 없거나 비활성 상태입니다)"
            )
        return {"reset": username}

    @router.post("/user/login")
    async def user_login(payload: UserLoginRequest, request: Request) -> dict[str, Any]:
        """End-user login. Reads user_accounts ONLY and hard-codes ROLE_USER, so no
        path here can produce an operator session."""
        # Not normalize_username(): a 422 would leak the id policy/existence, and
        # tightening the rule later must not lock out accounts created under the old
        # one. Canonicalization (lower/strip) still has to match how ids are stored.
        username = (payload.username or "").strip().lower()
        db = _require_db(request)
        rec = await db.user_auth_record(username)
        ok = bool(
            rec
            and rec.get("is_active", True)
            and await _verify_async(payload.password, rec["password_hash"])
        )
        if not ok:
            raise HTTPException(status_code=401, detail="invalid_credentials")
        user = session_user(
            username,
            ROLE_USER,
            method="local",
            posix_username=(rec.get("posix_username") or username),
        )
        request.session.clear()
        request.session["user"] = user
        await db.touch_user_login(username)
        return {"user": user}

    return router


async def _send_quietly(settings: Settings, to_addr: str, msg: Any) -> None:
    """Background delivery whose failure must not surface to the caller (it would be
    an existence oracle). Logs the failure class only — never the credentials."""
    try:
        await send_message(settings, to_addr=to_addr, msg=msg)
    except Exception as exc:  # noqa: BLE001 - background task must not raise
        log.warning("notice mail delivery failed: %s", type(exc).__name__)


async def _deliver_code(
    settings: Settings, db: Any, username: str, purpose: str, to_addr: str, code: str
) -> None:
    """Send the code; drop the pending row if delivery fails so we never leave a live
    code behind a mail that never arrived."""
    try:
        await send_code(settings, to_addr=to_addr, code=code, purpose=purpose)
    except Exception as exc:  # noqa: BLE001 - background task must not raise
        log.warning("verification mail delivery failed: %s", type(exc).__name__)
        try:
            await db.discard_verification(username=username, purpose=purpose)
        except Exception:  # noqa: BLE001
            pass


async def _fail_code(db: Any, settings: Settings, username: str, purpose: str) -> None:
    """Count a wrong/expired/exhausted code and raise. Deliberately ONE error code for
    all three: distinguishing them tells an attacker whether a code is live."""
    state = await db.bump_verification_failure(
        username=username,
        purpose=purpose,
        failure_window_seconds=settings.email_failure_window_seconds,
    )
    if int(state.get("attempts", 0)) >= settings.email_code_max_attempts:
        raise HTTPException(
            status_code=429,
            detail="code_attempts_exhausted (인증번호 입력 횟수를 초과했습니다. 인증번호를 다시 요청하세요)",
        )
    raise HTTPException(
        status_code=400,
        detail="invalid_code (인증번호가 올바르지 않거나 만료되었습니다)",
    )
