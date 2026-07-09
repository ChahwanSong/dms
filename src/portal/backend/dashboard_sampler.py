"""Dashboard time-series sampler.

DMS exposes only point-in-time work counts (``/operations/work-summary`` +
``/operations/data-jobs/summary``) — there is no history/count-over-time endpoint.
So the portal BFF samples those counts on a fixed cadence and persists one snapshot
row per tick to its own DB (``dashboard_samples``). The
``/api/operator/dashboard/timeseries`` endpoint then serves that history for the
request / in-progress-job trend chart.

This is a pure DMS API *client* (read-only DMS calls) — it never touches the DMS DB
or schema, honouring the portal's "consume DMS over HTTP only" rule.

Same shape as the backup/scan/sync/rm orchestrators: a single crash-tolerant asyncio
loop (never lets the loop die) started at app startup and stopped cleanly at shutdown.
Each tick also prunes rows older than the retention window so the table stays bounded.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .config import Settings
from .db import Database
from .dms_client import DmsApiError, DmsClient

log = logging.getLogger("portal.sampler")


def _int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _num(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _node_row(key: tuple, m: dict) -> dict:
    """One node metric row from a report's os_metrics. The agent nests it as
    {cpu:{percent,cores}, memory:{used_pct,...}, load:{load1,...}, disk:{used_pct,...}}
    (missing values → None)."""
    cpu = m.get("cpu") or {}
    mem = m.get("memory") or {}
    load = m.get("load") or {}
    disk = m.get("disk") or {}
    return {
        "cluster_name": key[0],
        "node_name": key[1],
        "cpu_percent": _num(cpu.get("percent")),
        "mem_used_pct": _num(mem.get("used_pct")),
        "load1": _num(load.get("load1")),
        "disk_used_pct": _num(disk.get("used_pct")),
        "_has": bool(m),
    }


class DashboardSampler:
    """Periodically snapshot DMS work counts into ``dashboard_samples``."""

    def __init__(self, db: Database, dms: DmsClient, settings: Settings) -> None:
        self._db = db
        self._dms = dms
        self._settings = settings
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if not self._db.configured or not self._settings.dms_configured:
            return
        self._task = asyncio.create_task(self._run(), name="dashboard-sampler")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _sample_once(self) -> None:
        actor = self._settings.dms_actor
        work = await self._dms.get_work_summary(actor=actor)
        jobs = await self._dms.get_data_job_summary(actor=actor)
        runs = (work or {}).get("runs") or {}
        plans = (work or {}).get("plans") or {}
        reqs = (work or {}).get("requests") or {}
        counts = {
            # active plans ~= requests currently being processed (the "요청" line)
            "active_plans": _int(plans.get("total_active")),
            # in-progress RM+DM runs (the "진행중 작업" line)
            "active_runs": _int(runs.get("total_active")),
            # in-progress data jobs (scan/sync/rm) — kept for an alt series
            "active_data_jobs": _int((jobs or {}).get("active_total")),
            "action_required": _int(reqs.get("action_required")),
            # cumulative all-time data jobs (its per-bucket delta = throughput)
            "data_jobs_total": _int((jobs or {}).get("total")),
        }
        await self._db.insert_dashboard_sample(counts)
        # per-node OS metrics for the 워커 노드 detail graphs (DMS serves only ~24h).
        try:
            await self._sample_nodes(actor)
        except DmsApiError as exc:
            log.warning("node metric sample skipped: %s", exc)
        except Exception:  # noqa: BLE001
            log.exception("node metric sample failed")
        # retention: keep BOTH sample tables bounded regardless of uptime.
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(days=self._settings.dashboard_retention_days)
        ).isoformat()
        try:
            await self._db.prune_dashboard_samples(cutoff)
            await self._db.prune_node_samples(cutoff)
        except Exception:  # noqa: BLE001
            log.exception("dashboard sample prune failed")

    async def _sample_nodes(self, actor: str) -> None:
        """Snapshot each node's current OS metrics. latest_per_node returns one row per
        (node, role); os_metrics is host-level (may be on only one role's report), so we
        keep one metric-bearing row per node."""
        reports = await self._dms.list_agent_reports(
            actor=actor, latest_per_node=True
        )
        per_node: dict[tuple, dict] = {}
        for r in reports or []:
            key = (r.get("cluster_name"), r.get("node_name"))
            m = (r.get("report") or {}).get("os_metrics") or {}
            cur = per_node.get(key)
            if cur is None or (m and not cur["_has"]):
                per_node[key] = _node_row(key, m)
        rows = [{k: v for k, v in n.items() if k != "_has"} for n in per_node.values()]
        await self._db.insert_node_samples(rows)

    async def _run(self) -> None:
        log.info(
            "dashboard sampler started (every %ss, retain %sd)",
            self._settings.dashboard_sample_seconds,
            self._settings.dashboard_retention_days,
        )
        while not self._stopping.is_set():
            try:
                await self._sample_once()
            except DmsApiError as exc:
                # DMS momentarily unavailable — skip this tick, try again next.
                log.warning("dashboard sample skipped: %s", exc)
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("dashboard sampler cycle error")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    # floor at 5s so a 0/negative misconfig can't spin a tight loop.
                    timeout=max(5.0, self._settings.dashboard_sample_seconds),
                )
            except asyncio.TimeoutError:
                pass
