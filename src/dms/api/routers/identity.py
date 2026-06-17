"""DM identity denylist router.

`identity_mappings` was removed: DM resolves the requester's POSIX identity by a
READ-ONLY LDAP lookup at preflight time. The only DM-side identity state is this
denylist — an instant per-requester / per-owner / per-group kill-switch and admission
block (default empty = allow all).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ...domain import DM_DENYLIST_SUBJECT_TYPES, DmIdentityDenylistBody
from .._services import AppServices
from ..deps import (
    _reject_if_maintenance_blocked,
    authenticated_actor,
    get_services,
)


def identity_denylist_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/data-management/identity-denylist",
        tags=["dm-identity-denylist"],
    )

    def _validate_subject_type(subject_type: str) -> None:
        if subject_type not in DM_DENYLIST_SUBJECT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"subject_type must be one of {DM_DENYLIST_SUBJECT_TYPES}",
            )

    @router.put("/{subject_type}/{subject}")
    def add_entry(
        subject_type: str,
        subject: str,
        request: Request,
        body: DmIdentityDenylistBody | None = None,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        _validate_subject_type(subject_type)
        entry = services.repository.add_dm_denylist_entry(
            subject_type=subject_type,
            subject=subject,
            reason=body.reason if body else None,
            actor=actor,
        )
        services.observability.safe_record_event(
            component="dm-identity-denylist",
            severity="WARN",
            event_type="dm_identity_denylist_added",
            message=f"DM identity denylist add: {subject_type}/{subject}",
            payload=entry,
        )
        return {"status": "Denied", "entry": entry}

    @router.delete("/{subject_type}/{subject}")
    def remove_entry(
        subject_type: str,
        subject: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        _validate_subject_type(subject_type)
        removed = services.repository.remove_dm_denylist_entry(
            subject_type=subject_type, subject=subject
        )
        if not removed:
            raise HTTPException(status_code=404, detail="denylist entry not found")
        services.observability.safe_record_event(
            component="dm-identity-denylist",
            severity="INFO",
            event_type="dm_identity_denylist_removed",
            message=f"DM identity denylist remove: {subject_type}/{subject}",
            payload={"subject_type": subject_type, "subject": subject, "actor": actor},
        )
        return {"status": "Allowed", "subject_type": subject_type, "subject": subject}

    @router.get("")
    def list_entries(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_dm_denylist()

    return router
