"""Operator account management (operator console → 계정 관리).

Two privilege tiers:
- **self-service** — any logged-in operator can change THEIR OWN password
  (needs the current password). Only an authenticated session is required.
- **admin** — create accounts, reset other operators' passwords, disable/enable,
  delete. Gated by ``require_operator_admin``: the operator must first unlock
  관리자 모드 by presenting ``PORTAL_ADMIN_TOKEN`` (verified server-side; only a
  session flag is stored, the token never reaches the browser). This admin token
  is deliberately SEPARATE from the DMS API token.

Passwords are one-way PBKDF2 hashes, so there is no "find password" — only an
admin RESET. New operator usernames must start with the ``admin_`` prefix (the
bootstrap ``admin`` account, seeded from PORTAL_OPERATOR_USERS, is grandfathered).
"""

from __future__ import annotations

import hmac
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import Settings
from ..db import Database, verify_password
from ..deps import get_db
from ..security import (
    ROLE_OPERATOR,
    SESSION_ADMIN_KEY,
    require_operator_admin,
    require_role,
)

# New operator ids must match this; the bootstrap `admin` is grandfathered (it can
# still log in / be managed, but new accounts follow the naming convention).
USERNAME_RE = re.compile(r"^admin_[a-z0-9_]{2,30}$")
MIN_PASSWORD_LEN = 8


class UnlockIn(BaseModel):
    token: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class CreateOperatorIn(BaseModel):
    username: str
    password: str


class NewPasswordIn(BaseModel):
    new_password: str = Field(min_length=1)


def _check_password(pw: str) -> None:
    if len(pw) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"password_too_short (최소 {MIN_PASSWORD_LEN}자)",
        )


def _check_username(username: str) -> None:
    if not USERNAME_RE.match(username):
        raise HTTPException(
            status_code=422,
            detail="invalid_username (소문자/숫자/밑줄, 'admin_' 접두어 필수 — 예: admin_ops)",
        )


def accounts_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/operator/accounts", tags=["operator-accounts"])

    # ---- admin-mode unlock (token) -------------------------------------

    @router.get("/status")
    def status(request: Request, user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR))):
        # tells the UI whether the admin section is available (token configured)
        # and whether THIS session has unlocked it.
        return {
            "admin_available": bool(settings.admin_token),
            "unlocked": bool(request.session.get(SESSION_ADMIN_KEY)),
            "username": user.get("username"),
        }

    @router.post("/unlock")
    def unlock(body: UnlockIn, request: Request,
               user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR))):
        if not settings.admin_token:
            raise HTTPException(status_code=503, detail="admin_token_not_configured")
        if not hmac.compare_digest(body.token, settings.admin_token):
            # don't set the flag; keep timing roughly uniform is not critical here.
            raise HTTPException(status_code=403, detail="invalid_admin_token")
        request.session[SESSION_ADMIN_KEY] = True
        return {"unlocked": True}

    @router.post("/lock")
    def lock(request: Request, user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR))):
        request.session.pop(SESSION_ADMIN_KEY, None)
        return {"unlocked": False}

    # ---- self-service: change own password -----------------------------

    @router.post("/change-password")
    async def change_password(
        body: ChangePasswordIn,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        username = str(user.get("username") or "")
        rec = await db.operator_auth_record(username)
        if rec is None or not verify_password(body.current_password, rec["password_hash"]):
            raise HTTPException(status_code=403, detail="current_password_incorrect")
        _check_password(body.new_password)
        await db.set_operator_password(username, body.new_password)
        return {"changed": True}

    # ---- admin: manage operator accounts -------------------------------

    @router.get("")
    async def list_accounts(
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_operator_admin),
    ) -> list[dict[str, Any]]:
        # never returns password hashes.
        return await db.list_operators()

    @router.post("")
    async def create_account(
        body: CreateOperatorIn,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_operator_admin),
    ) -> dict[str, Any]:
        username = body.username.strip()
        _check_username(username)
        _check_password(body.password)
        created = await db.create_operator(
            username, body.password, created_by=str(user.get("username") or "operator")
        )
        if not created:
            raise HTTPException(status_code=409, detail="username_exists")
        return {"created": username}

    @router.post("/{username}/reset-password")
    async def reset_password(
        username: str,
        body: NewPasswordIn,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_operator_admin),
    ) -> dict[str, Any]:
        _check_password(body.new_password)
        n = await db.set_operator_password(username, body.new_password)
        if not n:
            raise HTTPException(status_code=404, detail="operator_not_found")
        return {"reset": username}

    @router.post("/{username}/enable")
    async def enable_account(
        username: str,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_operator_admin),
    ) -> dict[str, Any]:
        n = await db.set_operator_active(username, True)
        if not n:
            raise HTTPException(status_code=404, detail="operator_not_found")
        return {"enabled": username}

    @router.post("/{username}/disable")
    async def disable_account(
        username: str,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_operator_admin),
    ) -> dict[str, Any]:
        # lock-out guards: can't disable yourself or the last active account.
        if username == user.get("username"):
            raise HTTPException(status_code=409, detail="cannot_disable_self")
        rec = await db.operator_auth_record(username)
        if rec is None:
            raise HTTPException(status_code=404, detail="operator_not_found")
        if rec.get("is_active") and await db.count_active_operators() <= 1:
            raise HTTPException(status_code=409, detail="cannot_disable_last_active")
        await db.set_operator_active(username, False)
        return {"disabled": username}

    @router.delete("/{username}")
    async def delete_account(
        username: str,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_operator_admin),
    ) -> dict[str, Any]:
        # lock-out guards: can't delete yourself or the last active account.
        if username == user.get("username"):
            raise HTTPException(status_code=409, detail="cannot_delete_self")
        rec = await db.operator_auth_record(username)
        if rec is None:
            raise HTTPException(status_code=404, detail="operator_not_found")
        if rec.get("is_active") and await db.count_active_operators() <= 1:
            raise HTTPException(status_code=409, detail="cannot_delete_last_active")
        await db.delete_operator(username)
        return {"deleted": username}

    return router
