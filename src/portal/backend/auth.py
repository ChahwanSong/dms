"""Authentication routes for the portal BFF.

Login method maps directly to role (see security.py):
- ``local`` (id/password) -> ROLE_OPERATOR. Real check against the operator
  credential store; operators may have multiple id/password accounts.
- ``ad`` (company AD account) -> ROLE_USER. A DUMMY stand-in for now (logs in as
  a placeholder identity), to be replaced by the real AD/LDAP integration.

Session state lives in Starlette's signed cookie (``request.session``); no DB.
"""

from __future__ import annotations

import hmac
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .config import Settings
from .db import verify_password
from .security import (
    ROLE_OPERATOR,
    ROLE_USER,
    current_user,
    session_user,
)

# New operator ids created from the login screen must match this; the bootstrap
# `admin` account (seeded from PORTAL_OPERATOR_USERS) is grandfathered for login.
USERNAME_RE = re.compile(r"^admin_[a-z0-9_]{2,30}$")
MIN_PASSWORD_LEN = 8


class LoginRequest(BaseModel):
    username: str
    password: str


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
                and verify_password(payload.password, rec["password_hash"])
            )
        else:
            ok = _verify_operator(settings, payload.username, payload.password)
        if not ok:
            raise HTTPException(status_code=401, detail="invalid_credentials")
        user = session_user(payload.username, ROLE_OPERATOR, method="local")
        request.session["user"] = user
        return {"user": user}

    @router.post("/login/ad")
    def login_ad(request: Request) -> dict[str, Any]:
        """Company AD login -> end user. DUMMY placeholder identity for now."""
        user = session_user("ad-user", ROLE_USER, method="ad", dummy=True)
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

    return router
