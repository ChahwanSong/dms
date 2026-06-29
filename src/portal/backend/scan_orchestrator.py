"""Data-scan orchestrator.

A single background asyncio loop that drives scan batches through the DMS DM
**scan** flow WITHOUT changing DMS. Scan is READ-ONLY and has NO preview/confirm:
``POST /scan`` runs directly (Pending -> Running -> Succeeded). So a scan batch is
a SIMPLIFIED backup batch with a single phase:

  scanning : submit each registered request (DMS runs the scan), poll to a terminal
             state, capture the scan summary -> succeeded (or failed). When nothing
             is registered/running left, the batch is 'done'.

Scan jobs run as the privileged `root` requester (same as backups; a read-only scan
needs no ownership override), which DMS gates behind an mTLS-verified operator, so
the loop sends actor "mtls:<operator>".

Single sequential loop == single writer, so no row locking is needed. The loop is
crash-tolerant: state lives in Postgres, so on restart in-flight requests (running,
already carrying a dms_job_id) are simply re-polled, and a running request that
never got a request_id (crash mid-submit) is re-submitted.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .config import Settings
from .db import Database
from .dms_client import DmsApiError, DmsClient

log = logging.getLogger("portal.scan")

# DMS DataJobState -> our coarse outcome buckets. Scan has no preview, so a job
# goes straight from running to a terminal state.
_SCAN_SUCCEEDED = {"Succeeded"}
_SCAN_FAILED = {"PreflightFailed", "Failed", "PreviewExpired", "Cancelled", "TimedOut"}

# The only DMS scan options we forward (DATA_SCAN_OPTION_TYPES). Anything else in a
# batch's options blob is ignored defensively (the router already filters on write).
_SCAN_OPTION_KEYS = frozenset(
    {"summary_only", "max_depth", "follow_symlinks", "one_file_system"}
)


def _scan_result(dms_job: dict[str, Any]) -> dict[str, Any]:
    """Extract a succeeded scan job's summary into our compact result shape."""
    rs = dms_job.get("result_summary") or {}
    s = rs.get("summary") or {}
    return {
        "file_count": s.get("file_count"),
        "directory_count": s.get("directory_count"),
        "total_bytes": s.get("total_bytes"),
        "error_count": s.get("error_count"),
        "scan_root": s.get("scan_root"),
        "tool": s.get("selected_tool") or rs.get("tool") or dms_job.get("selected_tool"),
    }


def _reason(dms_job: dict[str, Any]) -> str:
    pf = dms_job.get("preflight_result") or {}
    return (
        pf.get("reason")
        or pf.get("error")
        or dms_job.get("state")
        or "unknown"
    )


def scan_body(batch: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
    """The DMS DataJobRequest for one scan request. requester=root (read-only scan;
    ownership is irrelevant). Only the four scan options are forwarded; the caller
    adds priority + resources.node_count."""
    options: dict[str, Any] = {
        k: v
        for k, v in (batch.get("options") or {}).items()
        if k in _SCAN_OPTION_KEYS
    }
    body: dict[str, Any] = {
        "requester_id": batch.get("requester_id") or "root",
        "target": {"storage_name": req["storage"], "path": req["path"]},
    }
    if options:
        body["options"] = options
    return body


class ScanOrchestrator:
    def __init__(self, db: Database, dms: DmsClient, settings: Settings):
        self._db = db
        self._dms = dms
        self._settings = settings
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if not self._db.configured or not self._settings.dms_configured:
            return
        self._task = asyncio.create_task(self._run(), name="scan-orchestrator")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def _actor(self, batch: dict[str, Any]) -> str:
        op = batch.get("created_by") or self._settings.dms_actor
        return f"{self._settings.backup_actor_prefix}{op}"

    async def _run(self) -> None:
        log.info("scan orchestrator started")
        while not self._stopping.is_set():
            try:
                for batch in await self._db.active_scan_batches():
                    if batch["status"] == "scanning":
                        await self._drive_scan(batch)
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("scan orchestrator cycle error")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._settings.backup_poll_seconds
                )
            except asyncio.TimeoutError:
                pass

    # --- scanning phase -------------------------------------------------

    async def _drive_scan(self, batch: dict[str, Any]) -> None:
        bid = batch["id"]
        actor = self._actor(batch)
        running = await self._db.scan_requests_in_states(bid, ["running"])

        # 1) submit registered requests up to the concurrency cap.
        slots = max(0, self._settings.backup_concurrency - len(running))
        if slots:
            registered = await self._db.list_scan_requests(
                bid, state="registered", limit=slots
            )
            for req in registered:
                await self._submit_one(batch, req, actor)
            running = await self._db.scan_requests_in_states(bid, ["running"])

        # 2) resolve freshly-submitted request_ids -> job_ids (DMS ignores the
        #    request_id filter, so match the newest-first scan list client-side).
        unresolved = [
            r for r in running if r.get("dms_request_id") and not r.get("dms_job_id")
        ]
        if unresolved:
            by_req = await self._data_jobs_by_request(actor)
            for req in unresolved:
                dj = by_req.get(req["dms_request_id"])
                if dj:
                    await self._db.update_scan_request(req["id"], dms_job_id=dj["job_id"])
                    req["dms_job_id"] = dj["job_id"]
        # re-submit any running that never got a request_id (crash mid-submit)
        for req in running:
            if not req.get("dms_request_id"):
                await self._submit_one(batch, req, actor, resubmit=True)

        # 3) poll resolved running jobs to terminal.
        for req in running:
            if not req.get("dms_job_id"):
                continue
            try:
                dj = await self._dms.get_scan_job(req["dms_job_id"], actor=actor)
            except DmsApiError as exc:
                log.warning("scan poll failed job=%s: %s", req["dms_job_id"], exc)
                continue
            state = dj.get("state")
            if state in _SCAN_SUCCEEDED:
                await self._db.update_scan_request(
                    req["id"], state="succeeded", result=_scan_result(dj)
                )
            elif state in _SCAN_FAILED:
                await self._db.update_scan_request(
                    req["id"], state="failed", error=_reason(dj)
                )

        # 4) advance the batch once nothing is registered/running. A selective run
        #    parks the unselected as 'held' (not counted here); once the selected
        #    ones finish, mark done and release the held back to 'registered' so they
        #    stay available for a later run.
        counts = await self._db.scan_batch_state_counts(bid)
        if not counts.get("registered") and not counts.get("running"):
            await self._db.set_scan_batch_status(bid, "done")
            await self._db.release_held_scan(bid)

    async def _submit_one(
        self,
        batch: dict[str, Any],
        req: dict[str, Any],
        actor: str,
        *,
        resubmit: bool = False,
    ) -> None:
        if not resubmit:
            await self._db.update_scan_request(req["id"], state="running")
        body = scan_body(batch, req)
        # Per-batch Volcano scheduling priority (operator-selected; default Low).
        body["priority"] = batch.get("priority") or self._settings.backup_priority
        # Per-batch worker parallelism. None = use the DMS policy default.
        if batch.get("node_count"):
            body["resources"] = {"node_count": batch["node_count"]}
        try:
            resp = await self._dms.submit_scan(body, actor=actor)
        except DmsApiError as exc:
            await self._db.update_scan_request(
                req["id"], state="failed", error=str(exc.detail)
            )
            return
        await self._db.update_scan_request(
            req["id"], dms_request_id=resp.get("request_id"), state="running"
        )

    # --- helpers --------------------------------------------------------

    async def _data_jobs_by_request(self, actor: str) -> dict[str, dict[str, Any]]:
        try:
            jobs = await self._dms.list_data_jobs(
                actor=actor, limit=500, operation="data.scan"
            )
        except DmsApiError as exc:
            log.warning("list_data_jobs failed: %s", exc)
            return {}
        out: dict[str, dict[str, Any]] = {}
        for j in jobs or []:
            rid = j.get("request_id")
            if rid and rid not in out:
                out[rid] = j
        return out
