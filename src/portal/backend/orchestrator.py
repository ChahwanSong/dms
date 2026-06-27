"""Data-backup orchestrator.

A single background asyncio loop that drives backup batches through the DMS DM
sync flow WITHOUT changing DMS. Per active batch:

  previewing : submit each registered job (sync -> DMS runs a non-destructive
               preview), poll to ConfirmPending, capture the preview summary +
               fingerprint -> preview_ready. When all are preview_ready/failed ->
               batch 'previewed' (awaiting operator approval).
  running    : (after operator approval) confirm each preview_ready job with its
               fingerprint, poll to a terminal state. When all are terminal ->
               batch 'done'.

Backup jobs run as the privileged `root` requester (so original ownership is
preserved — root runs dsync without a chown override), which DMS gates behind an
mTLS-verified operator; the loop sends actor "mtls:<operator>".

Single sequential loop == single writer, so no row locking is needed. The loop is
crash-tolerant: state lives in Postgres, so on restart in-flight jobs (preview_
pending / running, already carrying a dms_job_id) are simply re-polled, and a
preview_pending job that never got a request_id (crash mid-submit) is re-submitted.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .config import Settings
from .db import Database
from .dms_client import DmsApiError, DmsClient

log = logging.getLogger("portal.backup")

# DMS DataJobState -> our coarse outcome buckets.
_PREVIEW_READY = {"ConfirmPending"}
_PREVIEW_FAILED = {"PreflightFailed", "Failed", "PreviewExpired", "Cancelled", "TimedOut"}
_EXEC_SUCCEEDED = {"Succeeded"}
_EXEC_FAILED = {"Failed", "PreflightFailed", "PreviewExpired", "Cancelled", "TimedOut"}


def _preview_metrics(dms_job: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    pv = (dms_job.get("result_summary") or {}).get("preview") or {}
    s = pv.get("summary") or {}
    metrics = {
        "files": s.get("file_count"),
        "dirs": s.get("directory_count"),
        "bytes": s.get("total_bytes"),
        "errors": s.get("error_count"),
        "tool": s.get("selected_tool") or dms_job.get("selected_tool"),
    }
    return metrics, pv.get("fingerprint")


def _reason(dms_job: dict[str, Any]) -> str:
    pf = dms_job.get("preflight_result") or {}
    return (
        pf.get("reason")
        or pf.get("error")
        or dms_job.get("state")
        or "unknown"
    )


def sync_body(batch: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """The DMS DataJobRequest for one backup job. requester=root + no chown so
    the original file/dir ownership is preserved; delete per the batch flag."""
    options: dict[str, Any] = dict(batch.get("options") or {})
    if batch.get("delete_enabled"):
        options["delete"] = True
    body: dict[str, Any] = {
        "requester_id": batch.get("requester_id") or "root",
        "source": {"storage_name": job["src_storage"], "path": job["src_path"]},
        "destination": {
            "storage_name": job["dst_storage"],
            "path": job["dst_path"],
        },
    }
    if options:
        body["options"] = options
    return body


class BackupOrchestrator:
    def __init__(self, db: Database, dms: DmsClient, settings: Settings):
        self._db = db
        self._dms = dms
        self._settings = settings
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if not self._db.configured or not self._settings.dms_configured:
            return
        self._task = asyncio.create_task(self._run(), name="backup-orchestrator")

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
        log.info("backup orchestrator started")
        while not self._stopping.is_set():
            try:
                for batch in await self._db.active_batches():
                    if batch["status"] == "previewing":
                        await self._drive_preview(batch)
                    elif batch["status"] == "running":
                        await self._drive_execute(batch)
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("backup orchestrator cycle error")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._settings.backup_poll_seconds
                )
            except asyncio.TimeoutError:
                pass

    # --- preview phase --------------------------------------------------

    async def _drive_preview(self, batch: dict[str, Any]) -> None:
        bid = batch["id"]
        actor = self._actor(batch)
        pending = await self._db.requests_in_states(bid, ["preview_pending"])

        # 1) submit registered jobs up to the concurrency cap.
        slots = max(0, self._settings.backup_concurrency - len(pending))
        if slots:
            registered = await self._db.list_requests(
                bid, state="registered", limit=slots
            )
            for job in registered:
                await self._submit_one(batch, job, actor)
            pending = await self._db.requests_in_states(bid, ["preview_pending"])

        # 2) resolve freshly-submitted request_ids -> job_ids (DMS ignores the
        #    request_id filter, so match newest-first list client-side).
        unresolved = [j for j in pending if j.get("dms_request_id") and not j.get("dms_job_id")]
        if unresolved:
            by_req = await self._data_jobs_by_request(actor)
            for job in unresolved:
                dj = by_req.get(job["dms_request_id"])
                if dj:
                    await self._db.update_request(job["id"], dms_job_id=dj["job_id"])
                    job["dms_job_id"] = dj["job_id"]
        # re-submit any pending that never got a request_id (crash mid-submit)
        for job in pending:
            if not job.get("dms_request_id"):
                await self._submit_one(batch, job, actor, resubmit=True)

        # 3) poll resolved preview_pending jobs.
        for job in pending:
            if not job.get("dms_job_id"):
                continue
            try:
                dj = await self._dms.get_sync_job(job["dms_job_id"], actor=actor)
            except DmsApiError as exc:
                log.warning("preview poll failed job=%s: %s", job["dms_job_id"], exc)
                continue
            state = dj.get("state")
            if state in _PREVIEW_READY:
                metrics, fp = _preview_metrics(dj)
                await self._db.update_request(
                    job["id"],
                    state="preview_ready",
                    fingerprint=fp,
                    preview=metrics,
                )
            elif state in _PREVIEW_FAILED:
                await self._db.update_request(
                    job["id"], state="preview_failed", error=_reason(dj)
                )

        # 4) advance the batch once nothing is registered/pending.
        counts = await self._db.batch_state_counts(bid)
        if not counts.get("registered") and not counts.get("preview_pending"):
            await self._db.set_batch_status(bid, "previewed")

    async def _submit_one(
        self,
        batch: dict[str, Any],
        job: dict[str, Any],
        actor: str,
        *,
        resubmit: bool = False,
    ) -> None:
        if not resubmit:
            await self._db.update_request(job["id"], state="preview_pending")
        body = sync_body(batch, job)
        # Per-batch Volcano scheduling priority (operator-selected; default Low).
        body["priority"] = batch.get("priority") or self._settings.backup_priority
        try:
            resp = await self._dms.submit_sync(body, actor=actor)
        except DmsApiError as exc:
            await self._db.update_request(
                job["id"], state="preview_failed", error=str(exc.detail)
            )
            return
        await self._db.update_request(
            job["id"], dms_request_id=resp.get("request_id"), state="preview_pending"
        )

    # --- execute phase --------------------------------------------------

    async def _drive_execute(self, batch: dict[str, Any]) -> None:
        bid = batch["id"]
        actor = self._actor(batch)
        running = await self._db.requests_in_states(bid, ["running"])

        # 1) confirm operator-approved jobs up to the cap (selective approval:
        #    preview_ready awaits a decision, only 'approved' gets executed).
        slots = max(0, self._settings.backup_concurrency - len(running))
        if slots:
            ready = await self._db.list_requests(bid, state="approved", limit=slots)
            for job in ready:
                await self._confirm_one(job, actor)
            running = await self._db.requests_in_states(bid, ["running"])

        # 2) poll running jobs to terminal.
        for job in running:
            if not job.get("dms_job_id"):
                continue
            try:
                dj = await self._dms.get_sync_job(job["dms_job_id"], actor=actor)
            except DmsApiError as exc:
                log.warning("exec poll failed job=%s: %s", job["dms_job_id"], exc)
                continue
            state = dj.get("state")
            if state in _EXEC_SUCCEEDED:
                rs = dj.get("result_summary") or {}
                await self._db.update_request(
                    job["id"], state="succeeded", result=rs.get("execution") or rs
                )
            elif state in _EXEC_FAILED:
                await self._db.update_request(
                    job["id"], state="failed", error=_reason(dj)
                )

        # 3) advance once nothing is approved/running: return to 'previewed' if
        #    undecided preview_ready remain (operator may approve more in stages),
        #    otherwise the batch is done.
        counts = await self._db.batch_state_counts(bid)
        if not counts.get("approved") and not counts.get("running"):
            if counts.get("preview_ready"):
                await self._db.set_batch_status(bid, "previewed")
            else:
                await self._db.set_batch_status(bid, "done")

    async def _confirm_one(self, job: dict[str, Any], actor: str) -> None:
        if not job.get("dms_job_id") or not job.get("fingerprint"):
            await self._db.update_request(
                job["id"], state="failed", error="missing job_id/fingerprint"
            )
            return
        await self._db.update_request(job["id"], state="running")
        try:
            await self._dms.confirm_job(
                job["dms_job_id"],
                {
                    "confirm": True,
                    "preview_observed_hash": job["fingerprint"],
                    "requester_id": "root",
                },
                actor=actor,
            )
        except DmsApiError as exc:
            await self._db.update_request(
                job["id"], state="failed", error=f"confirm: {exc.detail}"
            )

    # --- helpers --------------------------------------------------------

    async def _data_jobs_by_request(self, actor: str) -> dict[str, dict[str, Any]]:
        try:
            jobs = await self._dms.list_data_jobs(actor=actor, limit=500)
        except DmsApiError as exc:
            log.warning("list_data_jobs failed: %s", exc)
            return {}
        out: dict[str, dict[str, Any]] = {}
        for j in jobs or []:
            rid = j.get("request_id")
            if rid and rid not in out:
                out[rid] = j
        return out
