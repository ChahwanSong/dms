"""Agent ingestion router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ...agent import AgentReportIngestionService
from ...domain import AgentReport
from .._helpers.agent_rollout import (
    KubernetesUnavailable,
    agent_rollout_status,
    restart_agents,
)
from .._services import AppServices
from ..deps import (
    _reject_if_maintenance_blocked,
    authenticated_actor,
    get_services,
)


def agent_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

    @router.post("/reports")
    def submit_agent_report(
        report: AgentReport,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        service = AgentReportIngestionService(
            services.repository, services.observability
        )
        try:
            report_id = service.ingest(report, actor=actor)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        _maybe_recompute_readiness_on_ingest(services, report)
        # On-demand identity probing: hand the agent the recently-requested POSIX
        # usernames (registered by the DM worker at identity-resolve time) so its
        # NEXT report carries identity evidence for them — no static-list edit or
        # DaemonSet restart needed for a new requester. Fail-soft: never block a
        # report ingest on this read.
        try:
            probe_targets = services.repository.list_identity_probe_targets(
                ttl_seconds=services.settings.dm_identity_probe_target_ttl_seconds
            )
        except Exception:  # noqa: BLE001
            probe_targets = []
        return {
            "report_id": report_id,
            "status": "Fresh",
            "identity_probe_targets": probe_targets,
        }

    @router.post("/rollout-restart")
    def rollout_restart(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        """Rolling-restart the RM + DM agent DaemonSets so they re-read storages.json
        after a storage mapping change. Operator action (authenticated)."""
        authenticated_actor(request, services)
        try:
            return restart_agents(services.settings)
        except KubernetesUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - k8s API error
            raise HTTPException(
                status_code=502, detail=f"agent_rollout_failed: {exc}"
            ) from exc

    @router.get("/rollout-status")
    def rollout_status(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        """Per-DaemonSet rollout progress for the agent restart."""
        authenticated_actor(request, services)
        try:
            return agent_rollout_status(services.settings)
        except KubernetesUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - k8s API error
            raise HTTPException(
                status_code=502, detail=f"agent_rollout_status_failed: {exc}"
            ) from exc

    return router


def _maybe_recompute_readiness_on_ingest(
    services: AppServices, report: AgentReport
) -> None:
    """Layer (2): when a DM agent (re)reports, refresh readiness for the storages it
    mounts so a freshly-arrived agent is reflected within one report interval instead of
    waiting for the periodic reconciler. Off by default; never fails ingestion."""
    settings = getattr(services, "settings", None)
    if settings is None or not getattr(settings, "sanity_event_recompute_enabled", False):
        return
    if getattr(report.worker_role, "value", report.worker_role) != "DM":
        return
    try:
        from ...sanity_reconciler import build_sanity_service, recompute_storage_readiness

        sanity = build_sanity_service(services.repository, settings)
        seen: set[str] = set()
        for mount in report.mounts or []:
            name = mount.get("storage_name") if isinstance(mount, dict) else getattr(
                mount, "storage_name", None
            )
            if not name or name in seen:
                continue
            seen.add(name)
            recompute_storage_readiness(
                services.repository,
                sanity,
                name,
                observability=services.observability,
            )
    except Exception:  # noqa: BLE001 - readiness refresh must never break ingestion
        pass
