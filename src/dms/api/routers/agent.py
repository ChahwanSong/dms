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
        return {"report_id": report_id, "status": "Fresh"}

    return router
