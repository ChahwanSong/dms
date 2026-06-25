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


class LoginRequest(BaseModel):
    username: str
    password: str


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
            stored = await db.operator_password_hash(payload.username)
            ok = stored is not None and verify_password(payload.password, stored)
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

    return router
