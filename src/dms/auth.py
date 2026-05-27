from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from .config import Settings


@dataclass(frozen=True)
class AuthResult:
    authenticated: bool
    actor: str | None = None
    reason: str | None = None


class AuthVerifier:
    """Configurable mTLS/token verifier skeleton.

    In production this boundary is where trusted ingress mTLS evidence or a
    token verifier is plugged in. Phase 1 accepts either x-dms-actor from a
    trusted caller or a configured shared bearer token.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def verify(self, request: Request) -> AuthResult:
        actor = request.headers.get("x-dms-actor") or self.settings.default_actor
        token = request.headers.get("authorization")
        if self.settings.auth_shared_token:
            expected = f"Bearer {self.settings.auth_shared_token}"
            if token != expected:
                return AuthResult(False, reason="invalid token")
        if not actor:
            return AuthResult(False, reason="missing actor evidence")
        return AuthResult(True, actor=actor)


class AuthorizationPolicy:
    """Operation authorization hook.

    The default policy only denies explicit test/config markers. Real policy
    integration belongs behind this interface, not in route handlers.
    """

    def authorize(
        self,
        *,
        actor: str,
        requester_id: str,
        operation: str,
        resource_kind: str,
        resource_key: str,
        payload: dict[str, Any],
    ) -> tuple[bool, str]:
        if actor in {"blocked", "deny"}:
            return False, "actor is not authorized for this operation"
        if payload.get("deny") is True:
            return False, "request payload denied by authorization policy"
        return True, "authorized"
