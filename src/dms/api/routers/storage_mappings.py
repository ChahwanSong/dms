"""Storage-mapping (스토리지 인벤토리) write API.

Registration/update/re-check/delete of the storage mappings that describe every
backend DMS can address. This is *inventory*, not a data job: the mappings are what
the planner resolves a data job's storage names against, what the node agents mount,
and what the portal's 스토리지 인벤토리 tab manages.

Reads live on the operations router (``GET /api/v1/operations/storage-mappings``);
only the mutating half is here.

Every write is gated on ``active_work_for_storage`` (409 while a request/plan/run for
that storage is still in flight), re-runs the sanity probe, and syncs the node agents'
``dms-agent-storages`` ConfigMap so a newly-registered mount is probed on the next
agent cycle.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ...domain import StorageMappingInput, validate_filesystem_managed_root
from .._helpers.configmap import sync_agent_storages_configmap
from .._helpers.inventory import sanity_service
from .._helpers.storage_mapping import (
    merge_storage_mapping_secrets,
    redact_storage_mapping,
)
from .._services import AppServices
from ..deps import (
    _reject_if_maintenance_blocked,
    authenticated_actor,
    get_services,
)


def storage_mappings_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/storage-mappings", tags=["storage-mappings"])

    @router.post("")
    def upsert_storage_mapping(
        data: StorageMappingInput,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        conflict = services.repository.active_work_for_storage(data.storage_name)
        if conflict:
            services.repository.record_storage_mapping_conflict(
                storage_name=data.storage_name, actor=actor, conflict=conflict
            )
            raise HTTPException(status_code=409, detail=conflict)
        existing = services.repository.get_storage_mapping(data.storage_name)
        merge_storage_mapping_secrets(data.backend_template, existing)
        try:
            validate_filesystem_managed_root(data.backend_template)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        sanity = sanity_service(services).check_input(data)
        data.sanity_status = sanity["status"]
        services.repository.upsert_storage_mapping(
            data,
            actor=actor,
            sanity_result=sanity,
            readiness=sanity["readiness"],
        )
        services.observability.safe_record_event(
            component="storage-mapping",
            severity="INFO" if sanity["status"] != "Failed" else "WARN",
            event_type="storage_mapping_sanity_check_completed",
            message="storage mapping sanity check completed",
            payload={
                "storage_name": data.storage_name,
                "status": sanity["status"],
                "errors": sanity["errors"],
                "warnings": sanity["warnings"],
            },
        )
        mapping = services.repository.get_storage_mapping(data.storage_name)
        sync_agent_storages_configmap(services.settings, data.storage_name, mapping)
        return {
            "storage_name": data.storage_name,
            "status": sanity["status"],
            "mapping": redact_storage_mapping(mapping),
        }

    @router.post("/{storage_name}:check")
    def check_storage_mapping(
        storage_name: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        mapping = services.repository.get_storage_mapping(storage_name)
        if not mapping:
            raise HTTPException(status_code=404, detail="storage mapping not found")
        sanity = sanity_service(services).check_mapping(mapping)
        updated = services.repository.update_storage_mapping_sanity(
            storage_name,
            sanity_result=sanity,
            readiness=sanity["readiness"],
            actor=actor,
        )
        services.observability.safe_record_event(
            component="storage-mapping",
            severity="INFO" if sanity["status"] != "Failed" else "WARN",
            event_type="storage_mapping_sanity_check_completed",
            message="storage mapping sanity check completed",
            payload={
                "storage_name": storage_name,
                "status": sanity["status"],
                "errors": sanity["errors"],
                "warnings": sanity["warnings"],
            },
        )
        return {
            "storage_name": storage_name,
            "status": sanity["status"],
            "mapping": redact_storage_mapping(updated),
        }

    @router.patch("/{storage_name}")
    def patch_storage_mapping(
        storage_name: str,
        data: StorageMappingInput,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        existing = services.repository.get_storage_mapping(storage_name)
        if not existing:
            raise HTTPException(status_code=404, detail="storage mapping not found")
        if data.storage_name != storage_name:
            raise HTTPException(
                status_code=400, detail="storage_name in body must match path"
            )
        conflict = services.repository.active_work_for_storage(storage_name)
        if conflict:
            services.repository.record_storage_mapping_conflict(
                storage_name=storage_name, actor=actor, conflict=conflict
            )
            raise HTTPException(status_code=409, detail=conflict)
        merge_storage_mapping_secrets(data.backend_template, existing)
        try:
            validate_filesystem_managed_root(data.backend_template)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        sanity = sanity_service(services).check_input(data)
        data.sanity_status = sanity["status"]
        services.repository.upsert_storage_mapping(
            data,
            actor=actor,
            sanity_result=sanity,
            readiness=sanity["readiness"],
        )
        services.observability.safe_record_event(
            component="storage-mapping",
            severity="INFO" if sanity["status"] != "Failed" else "WARN",
            event_type="storage_mapping_sanity_check_completed",
            message="storage mapping updated and sanity check completed",
            payload={
                "storage_name": storage_name,
                "status": sanity["status"],
                "errors": sanity["errors"],
                "warnings": sanity["warnings"],
            },
        )
        mapping = services.repository.get_storage_mapping(storage_name)
        sync_agent_storages_configmap(services.settings, storage_name, mapping)
        return {
            "storage_name": storage_name,
            "status": sanity["status"],
            "mapping": redact_storage_mapping(mapping),
        }

    @router.delete("/{storage_name}", status_code=200)
    def delete_storage_mapping(
        storage_name: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        existing = services.repository.get_storage_mapping(storage_name)
        if not existing:
            raise HTTPException(status_code=404, detail="storage mapping not found")
        conflict = services.repository.active_work_for_storage(storage_name)
        if conflict:
            raise HTTPException(
                status_code=409,
                detail=f"storage mapping has active work: {conflict}",
            )
        deleted = redact_storage_mapping(
            services.repository.delete_storage_mapping(storage_name, actor)
        )
        sync_agent_storages_configmap(services.settings, storage_name, None)
        services.observability.safe_record_event(
            component="storage-mapping",
            severity="INFO",
            event_type="storage_mapping_deleted",
            message="storage mapping deleted",
            payload={"storage_name": storage_name},
        )
        return {"storage_name": storage_name, "deleted": True, "mapping": deleted}

    return router
