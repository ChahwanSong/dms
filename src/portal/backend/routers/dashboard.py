"""Operator dashboard API (role: operator).

Read-only aggregation over DMS operations endpoints. The summary endpoint
fans in several DMS calls in parallel and tolerates partial failure (a failed
section is returned as null + error so one bad panel never breaks the page).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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


def _node_metrics(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group flat per-report metric samples (oldest→newest) into per-node time-series.

    A node with both an RM and a DM agent emits ~2 samples/min for the same host, so we
    bucket per minute and keep the latest sample in each bucket — one point per minute
    per node. Each node gets cpu/mem series ({t, v}) + a `current` snapshot."""
    nodes: dict[tuple, dict[str, Any]] = {}
    for s in samples or []:
        key = (s.get("cluster_name"), s.get("node_name"))
        node = nodes.get(key)
        if node is None:
            node = {
                "cluster_name": s.get("cluster_name"),
                "node_name": s.get("node_name"),
                "buckets": {},
            }
            nodes[key] = node
        minute = (s.get("reported_at") or "")[:16]  # YYYY-MM-DDTHH:MM
        cur = node["buckets"].get(minute)
        if cur is None or (s.get("reported_at") or "") >= (cur.get("reported_at") or ""):
            node["buckets"][minute] = s

    out: list[dict[str, Any]] = []
    for node in nodes.values():
        ordered = [b for _, b in sorted(node["buckets"].items())]
        last = ordered[-1] if ordered else {}
        out.append(
            {
                "cluster_name": node["cluster_name"],
                "node_name": node["node_name"],
                "current": {
                    "cpu_percent": last.get("cpu_percent"),
                    "cpu_cores": last.get("cpu_cores"),
                    "mem_used_pct": last.get("mem_used_pct"),
                    "mem_total_kb": last.get("mem_total_kb"),
                    "load1": last.get("load1"),
                    "disk_used_pct": last.get("disk_used_pct"),
                    "reported_at": last.get("reported_at"),
                },
                "cpu_series": [{"t": b.get("reported_at"), "v": b.get("cpu_percent")} for b in ordered],
                "mem_series": [{"t": b.get("reported_at"), "v": b.get("mem_used_pct")} for b in ordered],
            }
        )
    out.sort(key=lambda n: (n.get("cluster_name") or "", n.get("node_name") or ""))
    return out


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


# ---- Volcano job metrics (throughput / latency / top offenders) ----
_VOL_WINDOWS = [("1h", 3600), ("6h", 21600), ("24h", 86400), ("72h", 259200)]
_VOL_SUCCESS = {"Completed", "Succeeded"}
_VOL_FAIL = {"Failed", "Aborted", "Terminated"}
_VOL_STAGES = ["job_to_pod_s", "pod_to_sched_s", "sched_to_start_s", "run_s"]


def _iso_ts(value: str | None) -> float | None:
    """Parse a k8s/RFC3339 timestamp to an epoch float (None if absent/invalid)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _pctile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 2)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    if f >= len(sorted_vals) - 1:
        return round(sorted_vals[-1], 2)
    return round(sorted_vals[f] + (sorted_vals[f + 1] - sorted_vals[f]) * (k - f), 2)


def _stage_stats(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"mean": None, "p50": None, "p95": None, "p99": None, "n": 0}
    s = sorted(vals)
    return {
        "mean": round(sum(s) / len(s), 2),
        "p50": _pctile(s, 50),
        "p95": _pctile(s, 95),
        "p99": _pctile(s, 99),
        "n": len(s),
    }


_VOL_TERMINAL = {"Completed", "Succeeded", "Failed", "Aborted", "Terminated"}


def _job_card(j: dict[str, Any]) -> dict[str, Any]:
    """Detail fields shown when an offender row is expanded (shared by the longest-
    pending and longest-running lists). Requester + per-tool route + ids + timing."""
    return {
        "name": j.get("name"),
        "queue": j.get("queue"),
        "phase": j.get("phase"),
        "phase_kind": j.get("phase_kind"),
        "tool": j.get("tool"),
        "requester": j.get("requester"),
        "request_id": j.get("request_id"),
        "data_job_id": j.get("data_job_id"),
        "src_storage": j.get("src_storage"), "dst_storage": j.get("dst_storage"),
        "src_path": j.get("src_path"), "dst_path": j.get("dst_path"),
        "scan_storage": j.get("scan_storage"), "scan_path": j.get("scan_path"),
        "rm_storage": j.get("rm_storage"), "rm_path": j.get("rm_path"),
        "req_pods": j.get("req_pods"), "req_cpu_cores": j.get("req_cpu_cores"),
        "req_mem_bytes": j.get("req_mem_bytes"),
        "created_at": j.get("created_at"), "started_at": j.get("started_at"),
        "finished_at": j.get("finished_at"),
    }


def _volcano_metrics(jobs: list[dict[str, Any]], now_ts: float) -> dict[str, Any]:
    """Windowed throughput + latency stats and current top-offenders from the per-job
    Volcano snapshot. Window membership = finished_at within the window."""
    windows: dict[str, Any] = {}
    for label, secs in _VOL_WINDOWS:
        cutoff = now_ts - secs
        in_win = [j for j in jobs if (_iso_ts(j.get("finished_at")) or -1) >= cutoff]
        latency = {
            stage: _stage_stats(
                [
                    (j.get("latencies") or {}).get(stage)
                    for j in in_win
                    if (j.get("latencies") or {}).get(stage) is not None
                ]
            )
            for stage in _VOL_STAGES
        }
        windows[label] = {
            "throughput": {
                "completed": len(in_win),
                "succeeded": sum(1 for j in in_win if j.get("phase") in _VOL_SUCCESS),
                "failed": sum(1 for j in in_win if j.get("phase") in _VOL_FAIL),
            },
            "latency": latency,
        }

    # Offenders: longest-pending (still queued) and longest-running. For running we
    # rank by elapsed/run duration — currently-running jobs (now − started, still
    # growing) and completed runs within the 72h horizon; ancient completed runs drop.
    recent_cut = now_ts - 259200
    pending = []
    running = []
    for j in jobs:
        started = _iso_ts(j.get("started_at"))
        finished = _iso_ts(j.get("finished_at"))
        if finished is None and started is None:
            created = _iso_ts(j.get("created_at"))
            if created is None:
                continue
            pending.append({**_job_card(j), "pending_s": round(now_ts - created, 1)})
        elif started is not None:
            active = finished is None and j.get("phase") not in _VOL_TERMINAL
            created = _iso_ts(j.get("created_at")) or 0
            if not active and created < recent_cut:
                continue  # old completed run, outside the 72h horizon
            end = finished if finished is not None else now_ts
            running.append({
                **_job_card(j), "running_s": round(end - started, 1), "active": active,
            })
    pending.sort(key=lambda x: x["pending_s"], reverse=True)
    running.sort(key=lambda x: x["running_s"], reverse=True)
    top = {
        "longest_pending": pending[:5],
        "longest_running": running[:5],
    }
    return {"windows": windows, "top": top}


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
# DMS emits CRITICAL for some quota issues (usage >=95%, query failed); rank it above ERROR.
_SEVERITY_RANK = {"CRITICAL": 0, "ERROR": 1, "WARN": 2, "INFO": 3}


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


def _refine_attention(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backfill severity and tag each item live/history so the panel can filter and
    group. CSI false-positive readiness warnings are suppressed upstream in DMS
    action_required, so no per-mapping cross-check is needed here."""
    refined: list[dict[str, Any]] = []
    for it in items or []:
        issue_type = it.get("issue_type") or ""
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

    @router.get("/node-metrics")
    async def node_metrics(
        since_seconds: int = Query(default=21600, ge=60, le=86400),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        # Per-node CPU/mem/load/disk time-series for the worker-node workload graphs.
        samples = await dms.list_agent_metric_samples(
            since_seconds=since_seconds, actor=_actor(user, settings)
        )
        return {"nodes": _node_metrics(samples), "window_seconds": since_seconds}

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

    @router.get("/volcano-metrics")
    async def volcano_metrics(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        # Windowed throughput/latency + top offenders from the per-job Volcano snapshot.
        try:
            data = await dms.volcano_job_metrics(actor=_actor(user, settings))
        except DmsApiError as exc:
            return {"windows": {}, "top": {}, "error": str(exc.detail)}
        now_ts = datetime.now(timezone.utc).timestamp()
        result = _volcano_metrics(data.get("jobs") or [], now_ts)
        result["error"] = data.get("error")
        return result

    @router.get("/attention")
    async def attention(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> list[dict[str, Any]]:
        items = await dms.list_action_required(actor=_actor(user, settings))
        return _refine_attention(items)

    return router
