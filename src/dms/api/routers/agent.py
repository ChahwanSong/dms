"""Agent ingestion router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ...agent import AgentReportIngestionService
from ...domain import AgentReport
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
        return {"report_id": report_id, "status": "Fresh"}

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
