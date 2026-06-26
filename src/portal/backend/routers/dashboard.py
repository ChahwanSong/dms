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

# Volcano job phases that are finished — anything else counts as "active".
_VOLCANO_TERMINAL = {"Completed", "Succeeded", "Failed", "Aborted", "Terminated"}


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


def _volcano_summary(v: dict[str, Any]) -> dict[str, Any]:
    """Card-level rollup of Volcano status: queue/job counts + component health.

    The detail panel renders the full queue/job/scheduler tables; the dashboard
    card only needs counts. volcano-system pods are grouped into the three
    Volcano components by name prefix (scheduler/controllers/admission).
    """
    queues = v.get("queues") or []
    jobs = v.get("jobs") or []
    sched = v.get("scheduler") or []
    active = [j for j in jobs if (j.get("phase") or "") not in _VOLCANO_TERMINAL]

    def _ok(p: dict[str, Any]) -> bool:
        # A one-shot init Job pod (e.g. volcano-admission-init) ends Succeeded and
        # is not "ready" yet is perfectly healthy — count it as ok, matching the
        # detail panel, so a completed init never reads as a degraded component.
        return bool(p.get("ready")) or p.get("phase") == "Succeeded"

    components: dict[str, dict[str, int]] = {}
    for p in sched:
        name = p.get("name") or ""
        if "scheduler" in name:
            role = "scheduler"
        elif "controller" in name:
            role = "controllers"
        elif "admission" in name:
            role = "admission"
        else:
            role = "other"
        slot = components.setdefault(role, {"ready": 0, "total": 0})
        slot["total"] += 1
        if _ok(p):
            slot["ready"] += 1
    errors = v.get("errors") or {}
    return {
        "queues": len(queues),
        "queues_open": sum(1 for q in queues if q.get("state") == "Open"),
        "jobs_active": len(active),
        "jobs_total": len(jobs),
        "ready": sum(1 for p in sched if _ok(p)),
        "total": len(sched),
        "components": components,
        "has_errors": any(errors.get(k) for k in ("queues", "jobs", "scheduler")),
    }


# ---- attention (action-required) refinement ----
# Some DMS issues carry no severity (request/readiness/mapping state) — give them
# a sensible default so the UI can filter by severity uniformly.
_ATTENTION_SEVERITY_DEFAULT = {
    "request_attention": "WARN",
    "missing_rm_readiness": "WARN",
    "missing_dm_readiness": "WARN",
    "storage_mapping_unknown": "WARN",
    "storage_mapping_failed": "ERROR",
    "agent_report_stale": "WARN",
}
# CSI (agentless) mappings legitimately have no RM/DM worker readiness; these two
# issue types are false positives for them and are dropped.
_READINESS_ISSUES = {"missing_rm_readiness", "missing_dm_readiness"}
_SEVERITY_RANK = {"ERROR": 0, "WARN": 1, "INFO": 2}


def _attention_category(issue_type: str, resource_kind: str | None) -> str:
    """live  = current request/mapping/agent/resource state — actionable now.
    history = a terminated data job or a past operation result that failed."""
    if resource_kind == "data_job":
        return "history"
    if issue_type in {"filesystem_soft_deleted", "filesystem_expired_unblocked"}:
        return "live"
    if issue_type.startswith("filesystem_") or "sweep" in issue_type:
        return "history"
    return "live"


def _refine_attention(
    items: list[dict[str, Any]], mappings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Drop CSI false-positive readiness warnings, backfill severity, and tag each
    item live/history so the panel can filter and group."""
    csi_names = {
        m.get("storage_name")
        for m in mappings or []
        if ((m.get("backend_template") or {}).get("backend_type") or "")
        not in _FS_BACKENDS
    }
    refined: list[dict[str, Any]] = []
    for it in items or []:
        issue_type = it.get("issue_type") or ""
        if issue_type in _READINESS_ISSUES and it.get("storage_name") in csi_names:
            continue  # agentless CSI: Missing RM/DM readiness is expected
        severity = it.get("severity") or _ATTENTION_SEVERITY_DEFAULT.get(
            issue_type, "WARN"
        )
        refined.append(
            {
                **it,
                "severity": severity,
                "category": _attention_category(issue_type, it.get("resource_kind")),
            }
        )
    refined.sort(
        key=lambda x: (
            0 if x["category"] == "live" else 1,
            _SEVERITY_RANK.get(x["severity"], 1),
            x.get("issue_type") or "",
        )
    )
    return refined


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
        control, work, jobs, reports, mappings, volcano = await asyncio.gather(
            _section(dms.get_control_state(actor=actor)),
            _section(dms.get_work_summary(actor=actor)),
            _section(dms.get_data_job_summary(actor=actor)),
            _section(dms.list_agent_reports(actor=actor)),
            _section(dms.list_storage_mappings(actor=actor)),
            _section(dms.get_volcano_status(actor=actor)),
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
        # CSI control host rollup (reachable / can-i) folded into the node card.
        ch = {"total": 0, "reachable": 0, "can_mutate": 0}
        if mappings["data"]:
            hosts = _control_hosts(mappings["data"])
            ch = {
                "total": len(hosts),
                "reachable": sum(1 for h in hosts if h.get("reachable")),
                "can_mutate": sum(1 for h in hosts if h.get("can_mutate")),
            }
        vol = _volcano_summary(volcano["data"]) if volcano["data"] else None
        return {
            "control_state": control,
            "work_summary": work,
            "data_jobs": jobs,
            "nodes": {"data": nodes, "error": reports["error"]},
            "control_hosts": {"data": ch, "error": mappings["error"]},
            "volcano": {"data": vol, "error": volcano["error"]},
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
        # os_metrics (cpu/mem/load/disk) is host-level — lift it from the agent's
        # full report and SHARE it across a node's role-rows, so a node shows
        # metrics even if only one of its agents (RM/DM) reports them.
        for r in latest:
            r["os_metrics"] = (r.get("report") or {}).get("os_metrics") or {}
        by_node: dict[tuple, dict[str, Any]] = {
            (r.get("cluster_name"), r.get("node_name")): r["os_metrics"]
            for r in latest
            if r["os_metrics"]
        }
        for r in latest:
            if not r["os_metrics"]:
                r["os_metrics"] = by_node.get(
                    (r.get("cluster_name"), r.get("node_name")), {}
                )
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

    @router.get("/volcano")
    async def volcano(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        return await dms.get_volcano_status(actor=_actor(user, settings))

    @router.get("/attention")
    async def attention(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> list[dict[str, Any]]:
        actor = _actor(user, settings)
        items, mappings = await asyncio.gather(
            dms.list_action_required(actor=actor),
            dms.list_storage_mappings(actor=actor),
        )
        return _refine_attention(items, mappings)

    return router
