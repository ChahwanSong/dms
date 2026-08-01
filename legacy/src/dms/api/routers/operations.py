"""Operational query router (read-only + control state mutations)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ...domain import LifecycleState, TERMINAL_LIFECYCLE_STATES
from .._helpers.inventory import inventory_service
from .._helpers.storage_mapping import (
    redact_storage_mapping,
    redact_storage_mappings,
)
from .._models import ActionAckBody, ActionUnackBody, ControlStateBody
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


def _select_data_job_log_ref(volcano_job_ref: Any) -> str | None:
    """Pick the most relevant Volcano/MPI job ref to tail launcher logs from.

    ``volcano_job_ref`` (data_jobs column) is either flat (``{"job_ref": ...}``)
    or phase-keyed (``{"preview": {"job_ref": ...}, "execution": {"job_ref": ...}}``;
    written early by the DM worker at submit time, so present mid-RUNNING). Prefer
    the execution phase (the live run an operator most wants to watch), then a scan
    ref, then preview, then any remaining ref. Returns ``None`` when nothing has
    been scheduled yet.
    """
    if not isinstance(volcano_job_ref, dict):
        return None
    flat = volcano_job_ref.get("job_ref")
    if isinstance(flat, str) and flat:
        return flat
    for phase in ("execution", "scan", "preview"):
        entry = volcano_job_ref.get(phase)
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("job_ref"), str)
            and entry["job_ref"]
        ):
            return entry["job_ref"]
    for entry in volcano_job_ref.values():
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("job_ref"), str)
            and entry["job_ref"]
        ):
            return entry["job_ref"]
    return None


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
                detail="maintenance always blocks scheduling",
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
        # expire never-confirmed previews first (moves their plan out of Blocked), then
        # close orphaned runs so the recovery pass keeps the attention set free of dead
        # records: DM preview runs parked in Blocked (plan moved on) + stuck runs whose
        # owning request already reached a terminal state.
        expired_previews = services.repository.expire_stale_preview_jobs(actor=actor)
        superseded = services.repository.close_superseded_preview_runs(actor=actor)
        orphaned = services.repository.close_orphaned_stuck_runs(actor=actor)
        stale = services.query.stale_or_recovery_runs()
        services.repository.record_control_mutation(
            actor=actor,
            mutation_kind="runs.mark_stale",
            payload={
                "count": count,
                "expired_previews": expired_previews,
                "superseded_preview_runs": superseded,
                "orphaned_stuck_runs": orphaned,
            },
            result_summary={"stale_or_recovery_count": len(stale)},
        )
        return {
            "marked": count,
            "expired_previews": expired_previews,
            "superseded_preview_runs": superseded,
            "orphaned_stuck_runs": orphaned,
            "stale_or_recovery_runs": stale,
        }

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
        offset: int = Query(default=0, ge=0),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.query.active_runs(
            states=tuple(state) if state else None,
            worker_role=worker_role,
            worker_id=worker_id,
            lease_expiring_within_seconds=lease_expiring_within_seconds,
            limit=limit,
            offset=offset,
        )

    @router.get("/action-required")
    def action_required(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.query.action_required()

    @router.post("/action-required:ack")
    def action_required_ack(
        body: ActionAckBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        # Mark action-required items handled (by fingerprint). Record-preserving:
        # action_required() then excludes them for ALL clients; nothing is deleted.
        actor = authenticated_actor(request, services)
        n = services.repository.add_action_acks(
            [item.model_dump() for item in body.items], acked_by=actor
        )
        return {"acked": n}

    @router.post("/action-required:unack")
    def action_required_unack(
        body: ActionUnackBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        n = services.repository.remove_action_acks(body.fingerprints)
        return {"unacked": n}

    @router.get("/action-required/acks")
    def action_required_acks(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_action_acks()

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
        latest_per_node: bool = Query(default=False),
        limit: int = Query(default=100, gt=0, le=10000),
        offset: int = Query(default=0, ge=0),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_agent_reports(
            freshness=freshness.capitalize() if freshness else None,
            stale_seconds=services.settings.agent_report_stale_seconds,
            update_stale=True,
            latest_per_node=latest_per_node,
            limit=limit,
            offset=offset,
        )

    @router.get("/agent-reports/metrics")
    def agent_report_metrics(
        request: Request,
        since_seconds: int = 21600,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        """Per-node OS-metric samples (cpu/mem/load/disk) over a time window, for the
        dashboard node workload graphs. Read-only; serves the existing agent_reports
        history (one row per report, ~1/min)."""
        authenticated_actor(request, services)
        window = max(60, min(int(since_seconds), 86400))  # clamp 1m..24h
        since_iso = (datetime.now(timezone.utc) - timedelta(seconds=window)).isoformat()
        return services.repository.list_agent_metric_samples(since_iso=since_iso)

    @router.get("/storage-mappings")
    def storage_mappings(
        request: Request,
        cluster_name: str | None = None,
        limit: int = Query(default=100, gt=0, le=10000),
        offset: int = Query(default=0, ge=0),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return redact_storage_mappings(
            services.repository.list_storage_mappings(
                limit=limit, cluster_name=cluster_name, offset=offset
            )
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

    @router.get("/request-activity")
    def request_activity(
        request: Request,
        operation: str | None = Query(default=None),
        resource_kind: str | None = Query(default=None),
        status: str | None = Query(default=None),
        requester_id: str | None = Query(default=None),
        search: str | None = Query(default=None),
        since: str | None = Query(default=None),
        until: str | None = Query(default=None),
        limit: int = Query(default=200, gt=0, le=2000),
        offset: int = Query(default=0, ge=0),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        """Flexible, read-only request activity — ALL requests (any operation/
        resource_kind/status), newest-first, paginated. Unlike /requests this does
        not require requester_id, powering the operator 액티비티 뷰. `search` is a
        case-insensitive needle over requester + target (resource_key / payload), so
        it spans ALL history. GROWING history: keep `limit` capped (le 2000) — never
        fetch-all; the portal pages via offset (infinite scroll)."""
        authenticated_actor(request, services)
        try:
            since_iso = _normalize_request_date(since, "since", end_of_day=False)
            until_iso = _normalize_request_date(until, "until", end_of_day=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return services.repository.search_requests(
            requester_id=requester_id,
            operation=operation,
            resource_kind=resource_kind,
            status=status,
            search=search,
            since=since_iso,
            until=until_iso,
            limit=limit,
            offset=offset,
        )

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

    @router.get("/runs/stale")
    def stale_runs(
        request: Request,
        limit: int = Query(default=100, gt=0, le=5000),
        offset: int = Query(default=0, ge=0),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.query.stale_or_recovery_runs(limit=limit, offset=offset)

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
        offset: int = Query(default=0, ge=0),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_data_jobs(
            limit=limit,
            requester_id=requester_id,
            operation=operation,
            storage_name=storage_name,
            state=state,
            offset=offset,
        )

    @router.get("/data-jobs/summary")
    def data_job_summary(
        request: Request,
        storage_name: str | None = None,
        operation: str | None = None,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.repository.data_job_summary(
            storage_name=storage_name,
            operation=operation,
        )

    @router.get("/data-jobs/{job_id}")
    def data_job_status(
        job_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.query.data_job_status(job_id)

    @router.get("/data-jobs/{job_id}/logs")
    def data_job_logs(
        job_id: str,
        request: Request,
        tail: int = Query(default=400, gt=0, le=5000),
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        """Read-only tail of the MPI launcher pod logs for a data job.

        Resolves the data_jobs row -> its ``volcano_job_ref`` (written early at
        submit, so present mid-RUNNING) -> tails the launcher pod via the volcano
        adapter. Never 500s for the expected unavailable cases (not yet scheduled,
        pod garbage-collected, kubectl error): returns ``available=False`` with a
        human ``note`` instead.
        """
        authenticated_actor(request, services)
        job = services.repository.get_data_job(job_id)
        if not job or not job.get("job_id"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"data job not found: {job_id}",
            )
        job_ref = _select_data_job_log_ref(job.get("volcano_job_ref"))
        if not job_ref:
            return {
                "job_id": job_id,
                "available": False,
                "pods": [],
                "logs": "",
                "note": "job not yet scheduled (no Volcano/MPI job ref recorded yet)",
            }
        result = services.volcano_adapter.tail_logs(job_ref, tail_lines=tail)
        return {"job_id": job_id, **result}

    @router.get("/diagnostics/{correlation_id}")
    def diagnostics(
        correlation_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.query.diagnostic_correlation(correlation_id)

    @router.get("/volcano")
    def volcano_status(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.volcano_adapter.volcano_status()

    @router.get("/volcano/job-metrics")
    def volcano_job_metrics(
        request: Request,
        limit: int = 1000,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        """Per-job Volcano lifecycle metrics (timestamps, latencies, status counts,
        requested resources) for the dashboard throughput/latency/top-offenders views.
        Read-only live snapshot."""
        authenticated_actor(request, services)
        return services.volcano_adapter.volcano_job_metrics(limit=max(1, min(int(limit), 2000)))

    @router.post("/requests/{request_id}:resolve", status_code=200)
    def resolve_request(
        request_id: str,
        body: dict[str, Any],
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        """Manually terminalize a request STUCK in a non-terminal state so its resource
        stops blocking new requests (the planner Conflicts a new request while a prior
        one for the same resource is still active).

        Resolvable states: UnknownAfterSideEffect, BackendApplyFailed, StaleClaim,
        RecoveryNeeded, VerificationFailed — the full set of stuck states that surface in
        조치 필요 (ACTION_REQUIRED_REQUEST_STATUSES). Actively-leased (Claimed/Running/
        Applying/Verifying) and already-terminal requests are rejected (409).

        body fields:
          - resolution: "abandon" (→ Cancelled for StaleClaim [no side effect], else
            Failed) or "succeeded" (→ Succeeded, when the operator confirms it applied)
          - reason: human-readable explanation (required)

        Abandon of RecoveryNeeded/UnknownAfterSideEffect may leave a PARTIALLY-applied
        backend side effect (the worker died mid-apply); the caller should verify the
        backend first. The audit result records backend_state_verified=false for these.
        The request's still-open plan + run are terminalized in the same transaction.
        """
        RESOLVABLE_STATES = {
            LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value,
            LifecycleState.BACKEND_APPLY_FAILED.value,
            LifecycleState.STALE_CLAIM.value,
            LifecycleState.RECOVERY_NEEDED.value,
            LifecycleState.VERIFICATION_FAILED.value,
        }

        actor = authenticated_actor(request, services)
        resolution = body.get("resolution")
        reason = body.get("reason", "").strip()
        if resolution not in ("abandon", "succeeded"):
            raise HTTPException(
                status_code=422,
                detail="resolution must be 'abandon' or 'succeeded'",
            )
        if not reason:
            raise HTTPException(status_code=422, detail="reason is required")

        try:
            req = services.repository.get_request(request_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="request not found") from None

        current_status = req.get("status")
        if (
            current_status in TERMINAL_LIFECYCLE_STATES
            and current_status not in RESOLVABLE_STATES
        ):
            raise HTTPException(
                status_code=409,
                detail=f"request is already in terminal state '{current_status}' and cannot be resolved",
            )
        if current_status not in RESOLVABLE_STATES:
            raise HTTPException(
                status_code=409,
                detail=f"request is in state '{current_status}'; only {sorted(RESOLVABLE_STATES)} can be resolved",
            )

        # abandon: StaleClaim never ran a side effect -> Cancelled; other stuck states
        # may have a (possibly partial) side effect -> Failed. succeeded -> Succeeded.
        if resolution == "succeeded":
            target_state = LifecycleState.SUCCEEDED
        elif current_status == LifecycleState.STALE_CLAIM.value:
            target_state = LifecycleState.CANCELLED
        else:
            target_state = LifecycleState.FAILED
        # RecoveryNeeded/UnknownAfterSideEffect abandon: a backend side effect may be
        # partially applied and was not verified — record that in the audit result.
        backend_unverified = resolution == "abandon" and current_status in {
            LifecycleState.RECOVERY_NEEDED.value,
            LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value,
            LifecycleState.VERIFICATION_FAILED.value,
        }
        services.repository.resolve_stuck_request(
            request_id,
            terminal_status=target_state,
            reason=f"manually resolved by {actor}: {reason}",
            actor=actor,
            backend_unverified=backend_unverified,
        )
        return {
            "request_id": request_id,
            "previous_status": current_status,
            "resolved_to": target_state.value,
            "resolution": resolution,
            "backend_state_verified": not backend_unverified,
            "actor": actor,
            "reason": reason,
        }

    return router
