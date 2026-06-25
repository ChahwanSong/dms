"""Operator dashboard API (role: operator).

Read-only aggregation over DMS operations endpoints. The summary endpoint
fans in several DMS calls in parallel and tolerates partial failure (a failed
section is returned as null + error so one bad panel never breaks the page).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..config import Settings
from ..deps import get_dms_client
from ..dms_client import DmsApiError, DmsClient
from ..security import ROLE_OPERATOR, require_role


def _actor(user: dict[str, Any], settings: Settings) -> str:
    return str(user.get("username") or settings.dms_actor)


async def _section(coro) -> dict[str, Any]:
    """Wrap a DMS call so a failure becomes {data:null, error:...} not a 500."""
    try:
        return {"data": await coro, "error": None}
    except DmsApiError as exc:
        return {"data": None, "error": str(exc.detail)}


_FS_BACKENDS = {"cephfs", "gpfs", "wekafs"}


def _control_hosts(mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """CSI (non-fs) storage mappings + their ResourceQuota mutation transport.

    fs mappings (cephfs/gpfs/wekafs) run node agents and belong in the worker-node
    panel; CSI/free-form mappings are agentless and instead reach their cluster via
    (ssh-)kubectl from a control host. Surface that host + reachability/can-i from
    the sanity `mutation_observed` block (already returned by storage-mappings).
    """
    rows: list[dict[str, Any]] = []
    for m in mappings or []:
        bt = (m.get("backend_template") or {}).get("backend_type") or ""
        if bt in _FS_BACKENDS:
            continue
        mo = (m.get("sanity_result") or {}).get("mutation_observed") or {}
        rows.append(
            {
                "storage_name": m.get("storage_name"),
                "cluster_name": m.get("cluster_name"),
                "backend_type": bt,
                "sanity_status": m.get("sanity_status"),
                "mode": mo.get("mode"),
                "control_host": mo.get("control_host"),
                "reachable": mo.get("reachable"),
                "can_mutate": mo.get("can_mutate"),
                "permissions": mo.get("permissions") or {},
                "detail": mo.get("detail"),
            }
        )
    rows.sort(key=lambda r: (r.get("cluster_name") or "", r.get("storage_name") or ""))
    return rows


def _latest_per_node(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse agent reports to the most recent one per (cluster, node, role).

    The DMS agent-reports endpoint returns recent reports including historical
    ones, so a single node-role agent appears many times (older ones eventually
    marked Stale). The dashboard shows CURRENT node health, so we keep only each
    agent's latest report by reported_at — otherwise stale history inflates the
    row count and the Fresh/Stale tallies.
    """
    latest: dict[tuple, dict[str, Any]] = {}
    for r in reports or []:
        key = (r.get("cluster_name"), r.get("node_name"), r.get("worker_role"))
        cur = latest.get(key)
        if cur is None or (r.get("reported_at") or "") > (cur.get("reported_at") or ""):
            latest[key] = r
    return sorted(
        latest.values(),
        key=lambda r: (
            r.get("cluster_name") or "",
            r.get("node_name") or "",
            r.get("worker_role") or "",
        ),
    )


def dashboard_router(settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/operator/dashboard",
        tags=["operator-dashboard"],
        dependencies=[Depends(require_role(ROLE_OPERATOR))],
    )

    @router.get("/summary")
    async def summary(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        actor = _actor(user, settings)
        control, work, jobs, reports = await asyncio.gather(
            _section(dms.get_control_state(actor=actor)),
            _section(dms.get_work_summary(actor=actor)),
            _section(dms.get_data_job_summary(actor=actor)),
            _section(dms.list_agent_reports(actor=actor)),
        )
        # node counts derived from each agent's LATEST report (Fresh/Stale by role)
        nodes = {"fresh": 0, "stale": 0, "by_role": {}}
        if reports["data"]:
            for r in _latest_per_node(reports["data"]):
                fresh = r.get("freshness_status") == "Fresh"
                nodes["fresh"] += 1 if fresh else 0
                nodes["stale"] += 0 if fresh else 1
                role = r.get("worker_role") or "?"
                slot = nodes["by_role"].setdefault(role, {"fresh": 0, "stale": 0})
                slot["fresh" if fresh else "stale"] += 1
        return {
            "control_state": control,
            "work_summary": work,
            "data_jobs": jobs,
            "nodes": {"data": nodes, "error": reports["error"]},
        }

    @router.get("/nodes")
    async def nodes(
        freshness: str | None = Query(default=None),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> list[dict[str, Any]]:
        # Fetch all reports, collapse to each agent's latest, then filter by the
        # latest report's freshness — so the filter reflects current node state,
        # not historical reports.
        reports = await dms.list_agent_reports(actor=_actor(user, settings))
        latest = _latest_per_node(reports)
        if freshness:
            latest = [r for r in latest if r.get("freshness_status") == freshness]
        return latest

    @router.get("/control-hosts")
    async def control_hosts(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> list[dict[str, Any]]:
        mappings = await dms.list_storage_mappings(actor=_actor(user, settings))
        return _control_hosts(mappings)

    @router.get("/runs")
    async def runs(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        actor = _actor(user, settings)
        active, stale = await asyncio.gather(
            _section(dms.list_active_runs(actor=actor)),
            _section(dms.list_stale_runs(actor=actor)),
        )
        return {"active": active, "stale": stale}

    @router.get("/requests")
    async def requests(
        state: str | None = Query(default=None),
        operation: str | None = Query(default=None),
        storage_name: str | None = Query(default=None),
        limit: int = Query(default=100, le=1000),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> list[dict[str, Any]]:
        return await dms.list_data_jobs(
            actor=_actor(user, settings),
            limit=limit,
            state=state,
            operation=operation,
            storage_name=storage_name,
        )

    @router.get("/attention")
    async def attention(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> list[dict[str, Any]]:
        return await dms.list_action_required(actor=_actor(user, settings))

    return router
