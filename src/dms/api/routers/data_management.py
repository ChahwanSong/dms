"""Data-management router: policies, data jobs (scan/sync/rm), confirm/cancel."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ...domain import DataJobRequest, DataManagementPolicyInput, OperationKind
from ...workers import cancel_data_job, confirm_data_job
from .._helpers.data_job import (
    data_job_request,
    policy_operation_or_422,
    validate_data_job_or_422,
    validate_data_options_or_422,
)
from .._models import ConfirmDataJobBody
from .._services import AppServices
from ..deps import (
    _reject_if_maintenance_blocked,
    authenticated_actor,
    get_services,
)


def data_management_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/data-management", tags=["data-management"])

    @router.get("/policies")
    def list_policies(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_data_management_policies()

    @router.get("/policies/{operation}")
    def get_policy(
        operation: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        policy = services.repository.get_data_management_policy(
            policy_operation_or_422(operation)
        )
        if not policy:
            raise HTTPException(
                status_code=404, detail="data management policy not found"
            )
        return policy

    @router.put("/policies/{operation}")
    def put_policy(
        operation: str,
        body: DataManagementPolicyInput,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        path_operation = policy_operation_or_422(operation)
        if body.operation != path_operation:
            raise HTTPException(
                status_code=400, detail="path and body operation mismatch"
            )
        services.repository.upsert_data_management_policy(body, actor=actor)
        policy = services.repository.get_data_management_policy(path_operation)
        return {"operation": path_operation, "status": "stored", "policy": policy}

    @router.post("/sync", status_code=202)
    def data_sync(
        body: DataJobRequest,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        validate_data_job_or_422(body, OperationKind.DATA_SYNC)
        validate_data_options_or_422(body, OperationKind.DATA_SYNC, services)
        return data_job_request(body, OperationKind.DATA_SYNC, request, services)

    @router.post("/rm", status_code=202)
    def data_rm(
        body: DataJobRequest,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        validate_data_job_or_422(body, OperationKind.DATA_RM)
        validate_data_options_or_422(body, OperationKind.DATA_RM, services)
        return data_job_request(body, OperationKind.DATA_RM, request, services)

    @router.post("/scan", status_code=202)
    def data_scan(
        body: DataJobRequest,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        validate_data_job_or_422(body, OperationKind.DATA_SCAN)
        return data_job_request(body, OperationKind.DATA_SCAN, request, services)

    @router.get("/scan")
    def list_scan_jobs(
        request: Request,
        requester_id: str | None = None,
        storage_name: str | None = None,
        state: str | None = None,
        limit: int = Query(default=100, gt=0, le=1000),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_data_jobs(
            limit=limit,
            requester_id=requester_id,
            operation=OperationKind.DATA_SCAN.value,
            storage_name=storage_name,
            state=state,
        )

    @router.get("/scan/jobs/{job_id}")
    def scan_job_status(
        job_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.query.data_job_status(job_id)

    @router.get("/sync")
    def list_sync_jobs(
        request: Request,
        requester_id: str | None = None,
        storage_name: str | None = None,
        state: str | None = None,
        limit: int = Query(default=100, gt=0, le=1000),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_data_jobs(
            limit=limit,
            requester_id=requester_id,
            operation=OperationKind.DATA_SYNC.value,
            storage_name=storage_name,
            state=state,
        )

    @router.get("/sync/jobs/{job_id}")
    def sync_job_status(
        job_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.query.data_job_status(job_id)

    @router.get("/rm")
    def list_rm_jobs(
        request: Request,
        requester_id: str | None = None,
        storage_name: str | None = None,
        state: str | None = None,
        limit: int = Query(default=100, gt=0, le=1000),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_data_jobs(
            limit=limit,
            requester_id=requester_id,
            operation=OperationKind.DATA_RM.value,
            storage_name=storage_name,
            state=state,
        )

    @router.get("/rm/jobs/{job_id}")
    def rm_job_status(
        job_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.query.data_job_status(job_id)

    @router.post("/jobs/{job_id}:confirm")
    def confirm_job(
        job_id: str,
        request: Request,
        body: ConfirmDataJobBody | None = None,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        try:
            confirm_data_job(
                services.repository,
                job_id,
                actor=actor,
                requester_id=body.requester_id if body else None,
                confirm=body.confirm if body else False,
                preview_observed_hash=body.preview_observed_hash if body else None,
                require_preview_fingerprint=services.settings.dm_confirm_require_preview_fingerprint,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"job_id": job_id, "status": "Confirmed"}

    @router.post("/jobs/{job_id}:cancel")
    def cancel_job(
        job_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        cancel_data_job(
            services.repository, services.volcano_adapter, job_id, actor=actor
        )
        return {"job_id": job_id, "status": "Cancelled"}

    @router.get("/help")
    def data_help() -> dict[str, Any]:
        return {
            "operations": {
                "sync": {
                    "status": "enabled",
                    "requires_preview_confirm": True,
                    "tool_selection": ["dsync", "nsync"],
                    "path_model": "structured source/destination storage-relative paths",
                    "legacy_path_model": "storage_name plus source_path/destination_path is accepted for same-storage sync",
                },
                "rm": {
                    "status": "enabled",
                    "requires_preview_confirm": True,
                    "tool": "drm",
                    "path_model": "structured target.storage_name plus storage-relative target.path",
                    "legacy_path_model": "storage_name plus target_path is accepted during migration",
                },
                "scan": {
                    "requires_preview_confirm": False,
                    "tool": "dscan",
                    "path_model": "structured target.storage_name plus storage-relative target.path",
                    "legacy_path_model": "storage_name plus target_path is accepted during migration",
                },
            },
            "raw_command_line_options": "rejected",
        }

    return router
