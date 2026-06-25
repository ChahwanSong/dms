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
ALL_ROLES = (ROLE_USER, ROLE_OPERATOR)


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
