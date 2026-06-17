"""Operational query router (read-only + control state mutations)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ...domain import ResourceKind
from .._helpers.inventory import inventory_service
from .._helpers.storage_mapping import (
    redact_storage_mapping,
    redact_storage_mappings,
)
from .._models import ControlStateBody
from .._services import AppServices
from ..deps import authenticated_actor, get_services


def _normalize_request_date(
    value: str | None, field_name: str, *, end_of_day: bool
) -> str | None:
    """Accept 'YYYY-MM-DD' or full ISO8601. Date-only values are widened to a UTC day boundary.

    `since` and date-only `until` widen to inclusive day-range comparison: caller filters with
    requested_at >= since AND requested_at < until, so until is shifted to the next day 00:00.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be YYYY-MM-DD or ISO8601") from exc
        if end_of_day:
            day = day + timedelta(days=1)
        return day.isoformat()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD or ISO8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def operational_query_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/operations", tags=["operational-query"])

    @router.get("/control-state")
    def control_state(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.repository.control_state()

    @router.post("/control-state:enter-maintenance")
    def enter_maintenance(
        body: ControlStateBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        if not body.block_scheduling:
            raise HTTPException(
                status_code=422,
                detail="Phase 18 maintenance always blocks scheduling",
            )
        state = services.repository.update_control_state(
            maintenance_mode=True,
            drain_mode=False,
            scheduling_blocked=True,
            reason=body.reason or "maintenance",
            actor=actor,
            mutation_kind="control.enter_maintenance",
            payload=body.model_dump(),
        )
        return {"control_state": state}

    @router.post("/control-state:begin-drain")
    def begin_drain(
        body: ControlStateBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        state = services.repository.update_control_state(
            maintenance_mode=True,
            drain_mode=True,
            scheduling_blocked=True,
            reason=body.reason or "drain",
            actor=actor,
            mutation_kind="control.begin_drain",
            payload=body.model_dump(),
        )
        drain = services.query.drain_status()
        return {
            "control_state": state,
            "active_runs": drain["active_runs"],
            "ready_for_shutdown": drain["ready_for_shutdown"],
        }

    @router.get("/drain-status")
    def drain_status(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.query.drain_status()

    @router.post("/control-state:resume")
    def resume(
        body: ControlStateBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        blockers = services.query.resume_blockers()
        if blockers and not body.force:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "reason": "recovery blockers require operator review before resume",
                    "blockers": blockers,
                },
            )
        state = services.repository.update_control_state(
            maintenance_mode=False,
            drain_mode=False,
            scheduling_blocked=False,
            reason=body.reason or "resume",
            actor=actor,
            mutation_kind="control.resume",
            payload=body.model_dump(),
            result_summary={"blockers": blockers, "forced": body.force},
        )
        return {"control_state": state, "forced": body.force, "blockers": blockers}

    @router.post("/runs:mark-stale")
    def mark_stale_runs(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        count = services.repository.mark_stale_runs(actor=actor)
        stale = services.query.stale_or_recovery_runs()
        services.repository.record_control_mutation(
            actor=actor,
            mutation_kind="runs.mark_stale",
            payload={"count": count},
            result_summary={"stale_or_recovery_count": len(stale)},
        )
        return {"marked": count, "stale_or_recovery_runs": stale}

    @router.get("/work-summary")
    def work_summary(
        request: Request,
        lease_expiring_within_seconds: int = Query(default=60, ge=0),
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.query.work_summary(
            lease_expiring_within_seconds=lease_expiring_within_seconds
        )

    @router.get("/plans/active")
    def active_plans(
        request: Request,
        status: list[str] | None = Query(default=None),
        worker_role: str | None = None,
        limit: int = Query(default=100, gt=0, le=1000),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.query.active_plans(
            statuses=tuple(status) if status else None,
            worker_role=worker_role,
            limit=limit,
        )

    @router.get("/runs/active")
    def active_runs(
        request: Request,
        state: list[str] | None = Query(default=None),
        worker_role: str | None = None,
        worker_id: str | None = None,
        lease_expiring_within_seconds: int = Query(default=60, ge=0),
        limit: int = Query(default=100, gt=0, le=1000),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.query.active_runs(
            states=tuple(state) if state else None,
            worker_role=worker_role,
            worker_id=worker_id,
            lease_expiring_within_seconds=lease_expiring_within_seconds,
            limit=limit,
        )

    @router.get("/action-required")
    def action_required(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.query.action_required()

    @router.get("/inventory")
    def inventory(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return inventory_service(services).effective_inventory()

    @router.get("/agent-reports")
    def agent_reports(
        request: Request,
        freshness: str | None = None,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_agent_reports(
            freshness=freshness.capitalize() if freshness else None,
            stale_seconds=services.settings.agent_report_stale_seconds,
            update_stale=True,
        )

    @router.get("/storage-mappings")
    def storage_mappings(
        request: Request,
        cluster_name: str | None = None,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return redact_storage_mappings(
            services.repository.list_storage_mappings(cluster_name=cluster_name)
        )

    @router.get("/storage-mappings/{storage_name}")
    def storage_mapping(
        storage_name: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        mapping = services.repository.get_storage_mapping(storage_name)
        if not mapping:
            raise HTTPException(status_code=404, detail="storage mapping not found")
        return redact_storage_mapping(mapping)

    @router.get("/requests")
    def requests(
        request: Request,
        requester_id: str = Query(..., min_length=1),
        limit: int | None = Query(default=None, gt=0),
        since: str | None = Query(default=None),
        until: str | None = Query(default=None),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        try:
            since_iso = _normalize_request_date(since, "since", end_of_day=False)
            until_iso = _normalize_request_date(until, "until", end_of_day=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        kwargs: dict[str, Any] = {"requester_id": requester_id}
        if limit is not None:
            kwargs["limit"] = limit
        if since_iso is not None:
            kwargs["since"] = since_iso
        if until_iso is not None:
            kwargs["until"] = until_iso
        return services.repository.list_requests(**kwargs)

    @router.get("/requests/{request_id}")
    def request_history(
        request_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.query.request_history(request_id)

    @router.get("/resources")
    def resource_history(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_resources()

    @router.get("/filesystems/expiring")
    def filesystem_expiring(
        request: Request,
        storage_name: str | None = None,
        status: str = "expired",
        before: str | None = None,
        within_seconds: int | None = Query(default=None, gt=0),
        include_blocked: bool = False,
        limit: int | None = Query(default=None, gt=0),
        brief: bool = False,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        try:
            return services.query.filesystem_expiring(
                storage_name=storage_name,
                status=status,
                before=before,
                within_seconds=within_seconds,
                include_blocked=include_blocked,
                limit=limit,
                brief=brief,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/filesystems/{storage_name}/{directory_name}")
    def filesystem_get(
        storage_name: str,
        directory_name: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        resource_key = f"{storage_name}:{directory_name}"
        resource = services.repository.get_resource(
            ResourceKind.FILESYSTEM.value, resource_key
        )
        if not resource or resource.get("status") == "Deleted":
            raise HTTPException(status_code=404, detail="filesystem resource not found")
        desired = resource.get("desired_state") or {}
        observed = resource.get("observed_state") or {}
        request_summary = services.query._filesystem_request_summary(resource_key)
        return {
            "resource_key": resource_key,
            "storage_name": storage_name,
            "directory_name": directory_name,
            "status": resource.get("status"),
            "resource_type": resource.get("resource_type"),
            "requester_id": desired.get("requester_id"),
            "users": desired.get("users"),
            "quota": desired.get("quota"),
            "expires_at": desired.get("expires_at"),
            "access_group": desired.get("access_group"),
            "mode": desired.get("mode"),
            "path": observed.get("path") or observed.get("junction_path"),
            "fileset_name": observed.get("fileset_name"),
            "access_group_info": observed.get("access_group"),
            "quota_state": observed.get("quota_state"),
            "last_block_request_id": request_summary.get("last_block_request_id"),
            "last_block_status": request_summary.get("last_block_status"),
            "updated_at": resource.get("updated_at"),
        }

    @router.get("/filesystems/{storage_name}")
    def filesystem_list(
        storage_name: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        all_resources = services.repository.list_resources()
        results = []
        prefix = f"{storage_name}:"
        for r in all_resources:
            if r.get("resource_kind") != ResourceKind.FILESYSTEM.value:
                continue
            if not r.get("resource_key", "").startswith(prefix):
                continue
            if r.get("status") == "Deleted":
                continue
            desired = r.get("desired_state") or {}
            directory_name = r["resource_key"][len(prefix) :]
            results.append(
                {
                    "resource_key": r["resource_key"],
                    "storage_name": storage_name,
                    "directory_name": directory_name,
                    "status": r.get("status"),
                    "requester_id": desired.get("requester_id"),
                    "quota": desired.get("quota"),
                    "expires_at": desired.get("expires_at"),
                    "updated_at": r.get("updated_at"),
                }
            )
        return results

    @router.get("/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}")
    def kubernetes_namespace_quota(
        cluster_name: str,
        namespace_name: str,
        request: Request,
        source: str = "both",
        include_non_dms: bool = False,
        include_status_used: bool = True,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        try:
            return services.query.kubernetes_namespace_quota(
                cluster_name=cluster_name,
                namespace_name=namespace_name,
                source=source,
                include_non_dms=include_non_dms,
                include_status_used=include_status_used,
                kubernetes_adapter=services.kubernetes_quota,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/kubernetes/namespace-quotas/expiring")
    def kubernetes_namespace_quota_expiring(
        request: Request,
        cluster_name: str | None = None,
        namespace_name: str | None = None,
        resource_type: str | None = None,
        status: str = "expired",
        before: str | None = None,
        within_seconds: int | None = Query(default=None, gt=0),
        include_blocked: bool = False,
        limit: int | None = Query(default=None, gt=0),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        try:
            return services.query.kubernetes_namespace_quota_expiring(
                cluster_name=cluster_name,
                namespace_name=namespace_name,
                resource_type=resource_type,
                status=status,
                before=before,
                within_seconds=within_seconds,
                include_blocked=include_blocked,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/runs/stale")
    def stale_runs(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.query.stale_or_recovery_runs()

    @router.get("/worker-agent-health")
    def worker_agent_health(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.query.worker_agent_health()

    @router.get("/data-jobs")
    def list_data_jobs(
        request: Request,
        requester_id: str | None = None,
        operation: str | None = None,
        storage_name: str | None = None,
        state: str | None = None,
        limit: int = Query(default=100, gt=0, le=1000),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_data_jobs(
            limit=limit,
            requester_id=requester_id,
            operation=operation,
            storage_name=storage_name,
            state=state,
        )

    @router.get("/data-jobs/{job_id}")
    def data_job_status(
        job_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.query.data_job_status(job_id)

    @router.get("/diagnostics/{correlation_id}")
    def diagnostics(
        correlation_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.query.diagnostic_correlation(correlation_id)

    return router
