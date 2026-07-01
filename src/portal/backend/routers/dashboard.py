"""Operator dashboard API (role: operator).

Read-only aggregation over DMS operations endpoints. The summary endpoint
fans in several DMS calls in parallel and tolerates partial failure (a failed
section is returned as null + error so one bad panel never breaks the page).
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..config import Settings
from ..db import Database
from ..deps import get_db, get_dms_client
from ..dms_client import DmsApiError, DmsClient
from ..security import ROLE_OPERATOR, require_role


class DismissIn(BaseModel):
    """Items to hide from 조치 필요 (portal-side acknowledge). Each carries its
    stable fingerprint plus issue_type/label for the 숨김 항목 review list."""
    items: list[dict[str, Any]] = Field(default_factory=list)


class FingerprintsIn(BaseModel):
    fingerprints: list[str] = Field(default_factory=list)


class ResolveIn(BaseModel):
    resolution: str  # "abandon" | "succeeded"
    reason: str = ""


def _actor(user: dict[str, Any], settings: Settings) -> str:
    return str(user.get("username") or settings.dms_actor)


async def _section(coro) -> dict[str, Any]:
    """Wrap a DMS call so a failure becomes {data:null, error:...} not a 500."""
    try:
        return {"data": await coro, "error": None}
    except DmsApiError as exc:
        return {"data": None, "error": str(exc.detail)}


# Single-fetch caps for the bounded dashboard lists. Storage mappings and the
# in-flight / attention-state runs are naturally bounded, so the portal fetches the
# whole set in ONE call with a high cap and flags `truncated` only if the cap was
# actually hit (DMS then had even more — should never happen at these sizes). Data
# jobs are GROWING history, so that table stays capped and gets its true total from
# the uncapped COUNT summary instead of fetching everything.
_STORAGE_LIMIT = 10000
_RUNS_ACTIVE_LIMIT = 1000
_RUNS_STALE_LIMIT = 2000
_DATA_JOBS_CAP = 500


def _mark_truncated(section: dict[str, Any], limit: int) -> dict[str, Any]:
    """Flag a single-fetch `_section` as truncated iff it returned the full cap."""
    data = section.get("data")
    section["truncated"] = bool(isinstance(data, list) and len(data) >= limit)
    return section


def _data_jobs_total(
    summary: dict[str, Any] | None,
    *,
    state: str | None,
    operation: str | None,
    storage_name: str | None,
) -> int | None:
    """Exact filtered total from the uncapped data-job COUNT summary, used when the
    capped data-jobs page is truncated. Returns None when the active filter combo
    can't be derived from the marginal counts (storage_name, or state+operation
    together) — the caller then reports the total as unknown rather than wrong."""
    if not isinstance(summary, dict):
        return None
    if storage_name or (state and operation):
        return None
    if state:
        val = (summary.get("by_state") or {}).get(state)
    elif operation:
        val = (summary.get("by_operation") or {}).get(operation)
    else:
        val = summary.get("total")
    return val if isinstance(val, int) else None


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


def _storage_node_matrix(
    reports: list[dict[str, Any]], mappings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Per-cluster (worker node × filesystem storage) mount-readiness matrix.

    Columns = registered FILESYSTEM storages (cephfs/gpfs/wekafs) from the current
    mappings — CSI/agentless mappings are excluded (they aren't host-mounted). Rows =
    worker nodes (one per node; RM+DM role-reports collapsed since mounts are
    host-level — keep the newest report's mounts/freshness). Each cell is that node's
    probed mount status for that storage (Ready/Missing/…) from agent report `mounts`,
    or absent (→ '미구성' in the UI) when the node never reported that storage.
    """
    # FS storages grouped by cluster (registered mappings only, CSI excluded).
    by_cluster_storages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in mappings or []:
        bt = (m.get("backend_template") or {}).get("backend_type") or ""
        if bt in _FS_BACKENDS:
            by_cluster_storages[m.get("cluster_name")].append(
                {"storage_name": m.get("storage_name"), "backend_type": bt}
            )

    # Collapse role-reports to one entry per (cluster, node): newest report wins for
    # host-level mounts/freshness; roles are unioned.
    nodes: dict[tuple, dict[str, Any]] = {}
    for r in reports or []:
        key = (r.get("cluster_name"), r.get("node_name"))
        cur = nodes.get(key)
        rep = r.get("report") or {}
        at = r.get("reported_at") or ""
        if cur is None:
            cur = {
                "cluster_name": r.get("cluster_name"),
                "node_name": r.get("node_name"),
                "roles": set(),
                "freshness": r.get("freshness_status"),
                "reported_at": at,
                "mounts": rep.get("mounts") or [],
            }
            nodes[key] = cur
        elif at > (cur.get("reported_at") or ""):
            cur["freshness"] = r.get("freshness_status")
            cur["reported_at"] = at
            cur["mounts"] = rep.get("mounts") or []
        if r.get("worker_role"):
            cur["roles"].add(r.get("worker_role"))

    clusters: dict[str, dict[str, Any]] = {}
    for (cluster, _node), nd in nodes.items():
        storages = by_cluster_storages.get(cluster, [])
        # skip clusters that have worker nodes but no FS storage to show
        if not storages:
            continue
        slot = clusters.setdefault(
            cluster,
            {"cluster_name": cluster, "storages": storages, "nodes": []},
        )
        names = {s["storage_name"] for s in storages}
        cells: dict[str, Any] = {}
        for mnt in nd["mounts"]:
            sn = mnt.get("storage_name")
            if sn in names:
                cells[sn] = {
                    "status": mnt.get("status"),
                    "mount_path": mnt.get("mount_path"),
                    "readable": mnt.get("readable"),
                    "writable": mnt.get("writable"),
                    "is_mountpoint": mnt.get("is_mountpoint"),
                    "filesystem_type": mnt.get("filesystem_type"),
                    "reason": mnt.get("reason"),
                }
        slot["nodes"].append(
            {
                "node_name": nd["node_name"],
                "roles": sorted(nd["roles"]),
                "freshness": nd["freshness"],
                "reported_at": nd["reported_at"],
                "cells": cells,
            }
        )

    out = sorted(clusters.values(), key=lambda c: c["cluster_name"] or "")
    for c in out:
        c["storages"].sort(key=lambda s: s["storage_name"] or "")
        c["nodes"].sort(key=lambda n: n["node_name"] or "")
    return out


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
# FORCE these to INFO regardless of the severity DMS emits: a terminated data job
# that just ended (preflight gate failed, or operator/user cancelled) is a
# notification, not something to act on — the genuinely actionable preflight causes
# surface as their own granular issue_types (data_job_permission_denied,
# data_job_identity_unresolved, data_job_no_ready_candidate, …), which keep their
# severity. INFO is hidden by default, so this de-noises 현재 조치 필요 while the
# records remain in 과거 작업 이력 (and via the INFO filter chip).
_ATTENTION_SEVERITY_OVERRIDE = {
    "data_job_preflight_failed": "INFO",
    "data_job_cancelled": "INFO",
}
# DMS emits CRITICAL for some quota issues (usage >=95%, query failed); rank it above ERROR.
_SEVERITY_RANK = {"CRITICAL": 0, "ERROR": 1, "WARN": 2, "INFO": 3}


def _attention_category(issue_type: str, resource_kind: str | None) -> str:
    """live  = current request/mapping/agent/resource state — actionable now.
    history = a terminated data job or a past operation result that failed."""
    # A stuck REQUEST (request_attention: StaleClaim/RecoveryNeeded/…) is actionable NOW
    # even for a data job — it must NOT be bucketed into 과거 이력 just because its
    # resource_kind is data_job (that rule is meant for the terminated data_job_* items).
    if issue_type == "request_attention":
        return "live"
    if resource_kind == "data_job":
        return "history"
    if issue_type in {"filesystem_soft_deleted", "filesystem_expired_unblocked"}:
        return "live"
    if issue_type.startswith("filesystem_") or "sweep" in issue_type:
        return "history"
    return "live"


def _fingerprint(it: dict[str, Any]) -> str:
    """Stable key per issue for the portal dismiss layer: issue_type + best identifier.
    Terminal items (data_job/request) key on their id; resource/storage on their key."""
    ns = it.get("namespace_name") or it.get("namespace")
    key = (
        it.get("resource_key")
        or it.get("request_id")
        or it.get("job_id")
        or it.get("report_id")
        or it.get("storage_name")
        or (f"{it.get('cluster_name')}:{ns}" if ns else None)
        or it.get("node_name")
        or ""
    )
    return f"{it.get('issue_type') or ''}|{key}"


# Attention items can carry huge nested blobs (a data_job_preflight_failed item
# embeds the whole preflight_result + result_summary — tens of KB each, ~2.4MB total
# across the list), yet the panel only ever renders the first ~240 chars of an object
# field. So we compact any oversized nested field before sending it to the browser:
# keep the useful scalar top-level keys, drop the heavy nested detail. (Deep inspection
# uses the dedicated job detail/logs view, not this list.)
_MAX_ATTENTION_FIELD_BYTES = 600


def _compact_value(value: Any) -> Any:
    try:
        size = len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return value
    if size <= _MAX_ATTENTION_FIELD_BYTES:
        return value
    if isinstance(value, dict):
        scalars: dict[str, Any] = {}
        dropped: list[str] = []
        for k, v in value.items():
            if v is None or isinstance(v, (str, int, float, bool)):
                scalars[k] = v[:240] + "…" if isinstance(v, str) and len(v) > 240 else v
            else:
                dropped.append(k)
        if dropped:
            scalars["_trimmed"] = (
                f"{len(dropped)} nested field(s) omitted: {', '.join(dropped[:6])}"
            )
        if len(json.dumps(scalars, default=str)) <= _MAX_ATTENTION_FIELD_BYTES * 2:
            return scalars
    return f"[trimmed {size}B]"


def _refine_attention(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backfill severity and tag each item live/history so the panel can filter and
    group, and compact oversized nested fields. CSI false-positive readiness warnings
    are suppressed upstream in DMS action_required, so no per-mapping cross-check here."""
    refined: list[dict[str, Any]] = []
    for it in items or []:
        issue_type = it.get("issue_type") or ""
        severity = (
            _ATTENTION_SEVERITY_OVERRIDE.get(issue_type)
            or it.get("severity")
            or _ATTENTION_SEVERITY_DEFAULT.get(issue_type, "WARN")
        )
        compact = {k: _compact_value(v) for k, v in it.items()}
        refined.append(
            {
                **compact,
                "severity": severity,
                "category": _attention_category(issue_type, it.get("resource_kind")),
                "fingerprint": _fingerprint(it),
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
            # latest_per_node => one row per node/role; node health stays complete
            # AND cheap on this polled path (no agent-report history scan/transfer).
            _section(dms.list_agent_reports(actor=actor, latest_per_node=True)),
            _section(dms.list_storage_mappings(actor=actor, limit=_STORAGE_LIMIT)),
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
        # latest_per_node => DMS already returns one row per node/role (newest);
        # the dedup below is now a cheap safety net. Complete node health without
        # transferring the accumulating report history.
        reports = await dms.list_agent_reports(
            actor=_actor(user, settings), latest_per_node=True
        )
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

    @router.get("/storage-node-matrix")
    async def storage_node_matrix(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        # Worker-node × filesystem-storage mount-readiness matrix (CSI excluded),
        # grouped per cluster. Reads the same latest_per_node agent reports (mounts)
        # + storage mappings the dashboard already uses — no DMS change.
        actor = _actor(user, settings)
        reports, mappings = await asyncio.gather(
            _section(dms.list_agent_reports(actor=actor, latest_per_node=True)),
            _section(dms.list_storage_mappings(actor=actor, limit=_STORAGE_LIMIT)),
        )
        return {
            "clusters": _storage_node_matrix(
                reports["data"] or [], mappings["data"] or []
            ),
            "errors": {"reports": reports["error"], "mappings": mappings["error"]},
        }

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
    ) -> dict[str, Any]:
        # Single-fetch the bounded mapping set; CSI control hosts are a subset.
        mappings = await dms.list_storage_mappings(
            actor=_actor(user, settings), limit=_STORAGE_LIMIT
        )
        return {
            "items": _control_hosts(mappings),
            "truncated": len(mappings) >= _STORAGE_LIMIT,
        }

    @router.get("/runs")
    async def runs(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        actor = _actor(user, settings)
        active, stale = await asyncio.gather(
            _section(dms.list_active_runs(actor=actor, limit=_RUNS_ACTIVE_LIMIT)),
            _section(dms.list_stale_runs(actor=actor, limit=_RUNS_STALE_LIMIT)),
        )
        _mark_truncated(active, _RUNS_ACTIVE_LIMIT)
        _mark_truncated(stale, _RUNS_STALE_LIMIT)
        return {"active": active, "stale": stale}

    @router.get("/requests")
    async def requests(
        state: str | None = Query(default=None),
        operation: str | None = Query(default=None),
        storage_name: str | None = Query(default=None),
        limit: int = Query(default=_DATA_JOBS_CAP, le=1000),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        # Data jobs are GROWING history (~10k/day). Keep a CAPPED page (never
        # fetch-all) and, only when that page is truncated, get the exact total from
        # the uncapped COUNT summary (one cheap GROUP BY, not a row transfer).
        actor = _actor(user, settings)
        jobs = await dms.list_data_jobs(
            actor=actor,
            limit=limit,
            state=state,
            operation=operation,
            storage_name=storage_name,
        )
        truncated = len(jobs) >= limit
        if not truncated:
            total: int | None = len(jobs)  # we already have every matching row
        else:
            try:
                summary = await dms.get_data_job_summary(actor=actor)
            except DmsApiError:
                summary = None
            total = _data_jobs_total(
                summary, state=state, operation=operation, storage_name=storage_name
            )
        return {"jobs": jobs, "total": total, "truncated": truncated}

    @router.get("/request-activity")
    async def request_activity(
        request: Request,
        operation: str | None = Query(default=None),
        resource_kind: str | None = Query(default=None),
        status: str | None = Query(default=None),
        requester_id: str | None = Query(default=None),
        limit: int = Query(default=_DATA_JOBS_CAP, le=2000),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        # ALL requests (RM + DM) by lifecycle status — the operator 액티비티 뷰.
        # GROWING history: capped page + truncated flag (no fetch-all / no COUNT).
        items = await dms.list_request_activity(
            actor=_actor(user, settings),
            operation=operation or None,
            resource_kind=resource_kind or None,
            status=status or None,
            requester_id=requester_id or None,
            limit=limit,
        )
        # Consistency with 조치 필요: flag requests that were hidden there (dismiss/ack)
        # so the activity view can hide them too (front-end default-hides, toggle shows).
        db: Database = request.app.state.db
        hidden_count = 0
        if db.configured:
            hidden_ids = await db.hidden_request_ids()
            for it in items:
                it["_hidden"] = bool(it.get("request_id") in hidden_ids)
                if it["_hidden"]:
                    hidden_count += 1
        return {
            "requests": items,
            "truncated": len(items) >= limit,
            "hidden_count": hidden_count,
        }

    @router.get("/requests/{request_id}")
    async def request_detail(
        request_id: str,
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        # Full lifecycle detail for one request (액티비티 행 펼침): request + plan +
        # results (terminal outcome/reason) + state transition history. Heavy nested
        # fields are compacted (same as the attention list) so a verbose preflight/
        # verification blob doesn't bloat the row.
        try:
            detail = await dms.get_request(request_id, actor=_actor(user, settings))
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        def _compact_row(row: Any) -> Any:
            if not isinstance(row, dict):
                return row
            return {k: _compact_value(v) for k, v in row.items()}

        return {
            "request": _compact_row(detail.get("request")),
            "plan": _compact_row(detail.get("plan")),
            "results": [_compact_row(r) for r in (detail.get("results") or [])],
            "transitions": detail.get("transitions") or [],
        }

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
        request: Request,
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> list[dict[str, Any]]:
        items = _refine_attention(
            await dms.list_action_required(actor=_actor(user, settings))
        )
        # filter portal-side dismissed items (graceful if no portal DB configured)
        db: Database = request.app.state.db
        if db.configured:
            dismissed = await db.dismissed_fingerprints()
            items = [it for it in items if it.get("fingerprint") not in dismissed]
        return items

    @router.get("/attention/dismissed")
    async def attention_dismissed(
        dms: DmsClient = Depends(get_dms_client),
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> list[dict[str, Any]]:
        # 처리 내역 = portal-local rows (숨김 + rich 확인 mirrors) MERGED with the
        # DMS server-side ack list, so an ack is always reflected here — even one made
        # by another client (or after the portal DB was reset). The portal rows are the
        # rich source; DMS acks not tracked locally are synthesized as minimal rows.
        rows: list[dict[str, Any]] = []
        tracked: set[str] = set()
        if db.configured:
            rows = await db.list_dismissals()  # non-archived, rich
            tracked = await db.dismissed_fingerprints()  # ALL (incl archived) → skip
        try:
            acks = await dms.list_action_acks(actor=_actor(user, settings))
        except DmsApiError:
            acks = []  # DMS unreachable/not-configured → show just the portal rows
        ack_fps = {a.get("fingerprint") for a in acks}
        # in_dms tells the UI whether a '확인' is reflected server-side (all clients)
        # or is a legacy portal-only ack from before the server-side wiring.
        for r in rows:
            if r.get("kind") == "ack":
                r["in_dms"] = r.get("fingerprint") in ack_fps
        extra = [
            {
                "fingerprint": a.get("fingerprint"),
                "issue_type": a.get("issue_type"),
                "label": None,
                "reason": a.get("reason"),
                "kind": "ack",
                "job_id": None,
                "request_id": None,
                "status": None,
                "item_at": a.get("acked_at"),
                "dismissed_by": a.get("acked_by"),
                "dismissed_at": a.get("acked_at"),
                "in_dms": True,
                "source": "dms",
            }
            for a in acks
            if a.get("fingerprint") and a["fingerprint"] not in tracked
        ]
        return rows + extra

    @router.post("/attention/dismiss")
    async def attention_dismiss(
        body: DismissIn,
        dms: DmsClient = Depends(get_dms_client),
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        items = [i for i in body.items if i.get("fingerprint")]
        # '확인(ack)' is a real, cross-client close-out → record it server-side in DMS
        # (record-preserving; action_required() then excludes it for every client).
        # '숨김(dismissed)' stays a portal-local view preference and never reaches DMS.
        acks = [i for i in items if i.get("kind") == "ack"]
        if acks:
            try:
                await dms.ack_action_required(
                    [
                        {
                            "fingerprint": i["fingerprint"],
                            "issue_type": i.get("issue_type"),
                            "reason": i.get("reason"),
                        }
                        for i in acks
                    ],
                    actor=_actor(user, settings),
                )
            except DmsApiError as exc:
                raise HTTPException(
                    status_code=exc.status_code, detail=exc.detail
                ) from exc
        # Portal mirror: rich 처리 내역 display + hides 숨김 items in THIS portal.
        n = await db.add_dismissals(
            items, dismissed_by=str(user.get("username") or "operator")
        )
        return {"dismissed": n}

    @router.post("/attention/undismiss")
    async def attention_undismiss(
        body: FingerprintsIn,
        dms: DmsClient = Depends(get_dms_client),
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        # 복원: un-ack in DMS (a no-op for fingerprints that were only 숨김/never acked)
        # AND drop the portal mirror, so an acked item reappears for all clients.
        fps = [f for f in body.fingerprints if f]
        if fps:
            try:
                await dms.unack_action_required(fps, actor=_actor(user, settings))
            except DmsApiError as exc:
                raise HTTPException(
                    status_code=exc.status_code, detail=exc.detail
                ) from exc
        n = await db.remove_dismissals(fps)
        return {"undismissed": n}

    @router.post("/attention/archive")
    async def attention_archive(
        body: FingerprintsIn,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        # '이전 정리': archive (keep hidden, drop from 처리 내역) — does NOT un-hide, so
        # terminated items don't resurface in 조치 필요/과거 이력, and a server-side ack
        # stays in effect (we intentionally do NOT un-ack here).
        n = await db.archive_dismissals(
            [f for f in body.fingerprints if f],
            archived_by=str(user.get("username") or "operator"),
        )
        return {"archived": n}

    @router.post("/requests/{request_id}/resolve")
    async def resolve_request(
        request_id: str,
        body: ResolveIn,
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        try:
            return await dms.resolve_request(
                request_id,
                resolution=body.resolution,
                reason=body.reason,
                actor=_actor(user, settings),
            )
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.delete("/data-jobs/{job_id}")
    async def delete_data_job(
        job_id: str,
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        try:
            return await dms.delete_data_job(job_id, actor=_actor(user, settings))
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return router
