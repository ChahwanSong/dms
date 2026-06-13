"""Identity-mapping router: upsert/refresh/disable/list/sync-all."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ...domain import IdentityMappingInput, IdentityMappingStatus
from .._helpers.identity import (
    expected_identity_mismatches,
    lookup_identity_or_http,
    mapping_drift,
    perform_identity_lookup,
    require_identity_lookup,
)
from .._models import DisableIdentityMappingBody
from .._services import AppServices
from ..deps import (
    _reject_if_maintenance_blocked,
    authenticated_actor,
    get_services,
)


def identity_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/identity-mappings", tags=["identity-mapping"])

    @router.put("/{identity_provider}/{requester_id}")
    def upsert_identity_mapping(
        identity_provider: str,
        requester_id: str,
        body: IdentityMappingInput,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        if (
            body.identity_provider != identity_provider
            or body.requester_id != requester_id
        ):
            raise HTTPException(
                status_code=400, detail="path and body identity mismatch"
            )
        lookup = require_identity_lookup(services)
        result = lookup_identity_or_http(
            lookup=lookup,
            provider=identity_provider,
            posix_username=body.posix_username,
        )
        mismatches = expected_identity_mismatches(body, result)
        mapping_status = (
            IdentityMappingStatus.NEEDS_REVIEW
            if mismatches
            else IdentityMappingStatus.ACTIVE
        )
        mapping_id = services.repository.upsert_identity_mapping(
            body,
            uid=result.uid,
            gid=result.primary_gid,
            groups=result.groups,
            status=mapping_status,
            verification_result="mismatch" if mismatches else "matched",
            mismatch_reason="; ".join(mismatches) if mismatches else None,
            source_metadata=result.source_metadata,
        )
        mapping = services.repository.get_identity_mapping(
            requester_id, identity_provider
        )
        return {
            "mapping_id": mapping_id,
            "status": mapping_status.value,
            "mapping": mapping,
        }

    @router.post("/{identity_provider}/{requester_id}:refresh")
    def refresh_identity_mapping(
        identity_provider: str,
        requester_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        lookup = require_identity_lookup(services)
        mapping = services.repository.get_identity_mapping(
            requester_id, identity_provider
        )
        if not mapping:
            raise HTTPException(status_code=404, detail="identity mapping not found")
        if mapping["status"] == IdentityMappingStatus.DISABLED.value:
            return {
                "requester_id": requester_id,
                "identity_provider": identity_provider,
                "status": IdentityMappingStatus.DISABLED.value,
                "mapping": mapping,
            }
        result = perform_identity_lookup(
            lookup=lookup,
            provider=identity_provider,
            posix_username=mapping["posix_username"],
        )
        if result is None:
            mapping = services.repository.mark_identity_mapping_stale(
                requester_id=requester_id,
                provider=identity_provider,
                mismatch_reason=f"LDAP user not found: {mapping['posix_username']}",
                verification_result="not_found",
                source_metadata={"adapter": "ldap3-direct", "read_only": True},
            )
            return {
                "requester_id": requester_id,
                "identity_provider": identity_provider,
                "status": mapping["status"],
                "mapping": mapping,
            }
        drift = mapping_drift(mapping, result)
        if drift:
            mapping = services.repository.mark_identity_mapping_stale(
                requester_id=requester_id,
                provider=identity_provider,
                mismatch_reason="; ".join(drift),
                verification_result="drift",
                source_metadata=result.source_metadata,
            )
        else:
            mapping = services.repository.verify_identity_mapping(
                requester_id=requester_id,
                provider=identity_provider,
                source_metadata=result.source_metadata,
            )
        return {
            "requester_id": requester_id,
            "identity_provider": identity_provider,
            "status": mapping["status"],
            "mapping": mapping,
        }

    @router.post("/{identity_provider}/{requester_id}:disable")
    def disable_identity_mapping(
        identity_provider: str,
        requester_id: str,
        request: Request,
        body: DisableIdentityMappingBody | None = None,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        services.repository.disable_identity_mapping(
            requester_id,
            identity_provider,
            reason=body.reason if body else None,
        )
        return {
            "requester_id": requester_id,
            "identity_provider": identity_provider,
            "status": "Disabled",
        }

    @router.get("")
    def list_identity_mappings(
        request: Request,
        requester_id: str | None = None,
        identity_provider: str | None = None,
        status: str | None = None,
        failed: bool = False,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_identity_mappings(
            requester_id=requester_id,
            identity_provider=identity_provider,
            status=status,
            failed=failed,
        )

    @router.post("/sync-all")
    def sync_identity_mappings(
        request: Request,
        identity_provider: str = "ldap",
        dry_run: bool = False,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        """DB의 모든 identity mapping을 LDAP에서 uid/gid/groups 일괄 재동기화."""
        authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        lookup = require_identity_lookup(services)

        t0 = time.monotonic()
        mappings = services.repository.list_all_identity_mappings_for_sync(
            identity_provider=identity_provider,
            exclude_status=[IdentityMappingStatus.DISABLED.value],
        )
        if not mappings:
            return {
                "synced": 0,
                "not_found": 0,
                "errors": 0,
                "dry_run": dry_run,
                "elapsed_seconds": round(time.monotonic() - t0, 2),
            }

        posix_usernames = [m["posix_username"] for m in mappings]

        ldap_results, errors = lookup.bulk_lookup_all(
            identity_provider, posix_usernames
        )

        updates: list[dict[str, Any]] = []
        not_found: list[str] = []

        for mapping in mappings:
            username = mapping["posix_username"]
            if username not in ldap_results:
                not_found.append(username)
                updates.append(
                    {
                        "requester_id": mapping["requester_id"],
                        "identity_provider": mapping["identity_provider"],
                        "uid": mapping["uid"],
                        "gid": mapping["gid"],
                        "groups": mapping["groups"],
                        "status": IdentityMappingStatus.STALE.value,
                        "verification_result": "not_found",
                        "mismatch_reason": f"LDAP user not found: {username}",
                        "source_metadata": mapping.get("ldap_source_metadata") or {},
                    }
                )
                continue
            result = ldap_results[username]
            updates.append(
                {
                    "requester_id": mapping["requester_id"],
                    "identity_provider": mapping["identity_provider"],
                    "uid": result.uid,
                    "gid": result.primary_gid,
                    "groups": result.groups,
                    "status": IdentityMappingStatus.ACTIVE.value,
                    "verification_result": "matched",
                    "mismatch_reason": None,
                    "source_metadata": result.source_metadata,
                }
            )

        updated = 0
        if not dry_run:
            updated = services.repository.bulk_update_ldap_sync(updates)

        elapsed = round(time.monotonic() - t0, 2)
        return {
            "synced": len(ldap_results),
            "not_found": len(not_found),
            "not_found_usernames": not_found,
            "errors": len(errors),
            "error_details": errors[:20],
            "dry_run": dry_run,
            "elapsed_seconds": elapsed,
            "total": len(mappings),
            "updated": updated,
        }

    return router
