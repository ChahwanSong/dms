"""Roles and session-backed auth dependencies for the portal BFF.

Role model (login method == role):
- ``operator`` — signs in with an id/password from the operator credential store
  (multiple operator accounts allowed). Gets the operator/admin interface.
- ``user`` — signs in with a company AD account. Gets the end-user interface.

The role lives in the signed session cookie (``request.session["user"]``) and is
the single source of truth used to gate both API routes (here) and the SPA's
top-level interface (frontend).
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request

ROLE_USER = "user"
ROLE_OPERATOR = "operator"

# Session flag set once an operator unlocks 관리자 모드 by presenting the admin
# token (PORTAL_ADMIN_TOKEN). Gates operator-account MANAGEMENT endpoints.
SESSION_ADMIN_KEY = "operator_admin"


def session_user(username: str, role: str, method: str, **extra: Any) -> dict[str, Any]:
    """Build the canonical user object stored in the session / returned by /me."""
    user = {"username": username, "role": role, "method": method}
    user.update(extra)
    return user


def current_user(request: Request) -> dict[str, Any] | None:
    return request.session.get("user")


def require_authenticated(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="not_authenticated")
    return user


def require_role(*roles: str):
    """Dependency factory: allow only sessions whose role is in ``roles``."""

    def _dep(user: dict[str, Any] = Depends(require_authenticated)) -> dict[str, Any]:
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="forbidden")
        return user

    return _dep


def require_operator_admin(request: Request) -> dict[str, Any]:
    """Gate operator-account MANAGEMENT (create / reset others / disable / delete).

    Requires a logged-in operator session that has ALSO unlocked 관리자 모드 by
    presenting PORTAL_ADMIN_TOKEN (verified server-side; only a session flag is
    stored — the token itself never reaches the browser). Self password change
    does NOT use this — it only needs an authenticated session + current password.
    """
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="not_authenticated")
    if user.get("role") != ROLE_OPERATOR:
        raise HTTPException(status_code=403, detail="forbidden")
    if not request.session.get(SESSION_ADMIN_KEY):
        raise HTTPException(status_code=403, detail="admin_locked")
    return user
