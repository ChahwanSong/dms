from __future__ import annotations

from dataclasses import dataclass
import hmac
from typing import Any

from fastapi import Request

from .config import Settings


@dataclass(frozen=True)
class AuthResult:
    authenticated: bool
    actor: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class MtlsEvidence:
    subject: str | None
    verify: str | None


class AuthVerifier:
    """Configurable trusted-ingress mTLS evidence and token verifier."""

    DMS_SUBJECT_HEADER = "x-dms-client-cert-subject"
    DMS_VERIFY_HEADER = "x-dms-client-cert-verify"
    NGINX_SUBJECT_HEADER = "ssl-client-subject-dn"
    NGINX_VERIFY_HEADER = "ssl-client-verify"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def verify(self, request: Request) -> AuthResult:
        token = request.headers.get("authorization")
        if self.settings.auth_shared_token:
            expected = f"Bearer {self.settings.auth_shared_token}"
            if token is None or not hmac.compare_digest(token, expected):
                return AuthResult(False, reason="invalid token")

        requires_mtls = (
            self.settings.require_mtls_header
            or self.settings.require_mtls_verified_header
        )
        mtls = self._mtls_evidence(request)
        if isinstance(mtls, AuthResult):
            return mtls
        if requires_mtls:
            mtls_result = self._validate_required_mtls(mtls)
            if mtls_result is not None:
                return mtls_result
            return self._mtls_actor_result(mtls, request)

        actor = request.headers.get("x-dms-actor") or self.settings.default_actor
        if not actor:
            return AuthResult(False, reason="missing actor evidence")
        return AuthResult(True, actor=actor)

    def _mtls_evidence(self, request: Request) -> MtlsEvidence | AuthResult:
        dms_subject = _trim_header(request, self.DMS_SUBJECT_HEADER)
        dms_verify = _trim_header(request, self.DMS_VERIFY_HEADER)
        nginx_subject = _trim_header(request, self.NGINX_SUBJECT_HEADER)
        nginx_verify = _trim_header(request, self.NGINX_VERIFY_HEADER)

        if dms_subject and nginx_subject and dms_subject != nginx_subject:
            return AuthResult(False, reason="mtls_evidence_conflict")
        if dms_verify and nginx_verify and dms_verify != nginx_verify:
            return AuthResult(False, reason="mtls_evidence_conflict")

        return MtlsEvidence(
            subject=dms_subject or nginx_subject,
            verify=dms_verify or nginx_verify,
        )

    def _validate_required_mtls(self, evidence: MtlsEvidence) -> AuthResult | None:
        if self.settings.require_mtls_header and not evidence.subject:
            return AuthResult(False, reason="mtls_subject_required")
        if self.settings.require_mtls_verified_header:
            if not evidence.verify:
                return AuthResult(False, reason="mtls_verify_required")
            if evidence.verify != "SUCCESS":
                return AuthResult(False, reason="mtls_verify_failed")
        return None

    def _mtls_actor_result(
        self, evidence: MtlsEvidence, request: Request
    ) -> AuthResult:
        if not evidence.subject:
            return AuthResult(False, reason="mtls_evidence_required")
        actor = f"{self.settings.mtls_actor_prefix}{evidence.subject}"
        header_actor = request.headers.get("x-dms-actor")
        if header_actor and header_actor != actor:
            return AuthResult(False, reason="actor_evidence_conflict")
        return AuthResult(True, actor=actor)


def _trim_header(request: Request, name: str) -> str | None:
    value = request.headers.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


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
