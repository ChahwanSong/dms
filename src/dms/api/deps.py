"""Common FastAPI dependencies and request-submission helpers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from ..adapters import IdentityLookupAdapter, LdapIdentityLookupAdapter
from ..config import Settings
from ..domain import RequestEnvelope
from ._services import AppServices

MAINTENANCE_REJECT_DETAIL = (
    "DMS is in maintenance/drain mode; mutating requests are temporarily rejected"
)


def get_services(request: Request) -> AppServices:
    return request.app.state.services


def authenticated_actor(request: Request, services: AppServices) -> str:
    result = services.auth.verify(request)
    if not result.authenticated or not result.actor:
        services.observability.safe_record_event(
            component="api-auth",
            severity="WARN",
            event_type="authentication_rejected",
            message=result.reason or "authentication rejected",
            payload={"path": str(request.url.path)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=result.reason or "authentication rejected",
        )
    return result.actor


def submit_request(
    *,
    services: AppServices,
    request: Request,
    envelope: RequestEnvelope,
) -> dict[str, Any]:
    actor = authenticated_actor(request, services)
    _reject_if_maintenance_blocked(services)
    request_id = services.repository.create_request(
        requester_id=envelope.requester_id,
        actor=actor,
        operation=envelope.operation.value,
        resource_kind=envelope.resource_kind.value,
        resource_key=envelope.resource_key,
        payload=envelope.payload,
    )
    allowed, reason = services.authorization.authorize(
        actor=actor,
        requester_id=envelope.requester_id,
        operation=envelope.operation.value,
        resource_kind=envelope.resource_kind.value,
        resource_key=envelope.resource_key,
        payload=envelope.payload,
    )
    if not allowed:
        services.repository.record_authorization_failed(request_id, reason)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"request_id": request_id, "reason": reason},
        )
    return {"request_id": request_id, "status": "Persisted"}


def _reject_if_maintenance_blocked(services: AppServices) -> None:
    if services.repository.scheduling_blocked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=MAINTENANCE_REJECT_DETAIL,
        )


def identity_lookup_from_settings(settings: Settings) -> IdentityLookupAdapter | None:
    if not settings.ldap_uri and not settings.ldap_base_dn:
        return None
    return LdapIdentityLookupAdapter.from_settings(settings)
