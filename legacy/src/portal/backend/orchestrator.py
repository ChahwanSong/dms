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
from datetime import datetime, timezone
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

# Terminal DMS *data-job* states (mirrors domain.TERMINAL_DATA_JOB_STATES; the
# portal must not import the DMS package).
_TERMINAL_DATA_JOB = {
    "Succeeded", "Failed", "Cancelled", "TimedOut",
    "AuthorizationFailed", "PreflightFailed", "PreviewExpired",
}
# Terminal DMS *request* lifecycle states (mirrors domain.TERMINAL_LIFECYCLE_STATES).
# A preview_pending whose request reaches one of these WITHOUT a usable data_job is
# dead — fail it rather than poll forever. 'Conflict' is handled specially (it may
# be recoverable by adopting the prior request's still-valid preview).
_TERMINAL_LIFECYCLE = {
    "AuthenticationRejected", "AuthorizationFailed", "Succeeded", "Failed",
    "TimedOut", "Cancelled", "Conflict", "Rejected", "BackendApplyFailed",
}


def _conflict_prior_request_id(info: dict[str, Any]) -> str | None:
    """For a request that terminated in 'Conflict' (ordering: a prior request for the
    same resource_key is still non-terminal), return that prior request_id."""
    for res in info.get("results") or []:
        if res.get("terminal_status") == "Conflict":
            vs = res.get("verification_summary") or {}
            prior = vs.get("prior_request_id")
            if prior:
                return str(prior)
    return None


def _age_seconds(updated_at: Any) -> float:
    """Seconds since a request row's updated_at (DB datetime/ISO string). Returns 0
    when unknown so the timeout never fires on a value we can't parse."""
    if updated_at is None:
        return 0.0
    dt = updated_at
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
    if not isinstance(dt, datetime):
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


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
        by_req: dict[str, dict[str, Any]] = {}
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

        # 2b) (B) a pending that HAS a request_id but STILL has no job did not match
        #     any data_job — its DMS request may have terminated WITHOUT producing one
        #     (e.g. a resource-key 'Conflict'). Inspect the request and recover so the
        #     item can't park the batch in 'previewing' forever.
        for job in pending:
            if job.get("dms_request_id") and not job.get("dms_job_id"):
                await self._resolve_stuck_preview(job, actor, by_req)

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

        # 3.5) (A) also confirm/execute any operator-approved items WHILE previewing,
        #      so a single slow/stuck preview can't block approving the ready ones.
        await self._execute_steps(batch, actor)

        # 4) advance the batch once nothing is registered/pending. A selective
        #    preview parks the unselected as 'held' (not counted here); once the
        #    selected ones finish, release the held back to 'registered' (kept idle —
        #    a later explicit re-preview runs them) and move on. If items were
        #    approved mid-preview (A), enter the execute phase ('running'); otherwise
        #    the preview phase is complete (ready and/or failed) -> 'previewed'.
        counts = await self._db.batch_state_counts(bid)
        if not counts.get("registered") and not counts.get("preview_pending"):
            await self._db.release_held(bid)
            after = await self._db.batch_state_counts(bid)
            if after.get("approved") or after.get("running"):
                await self._db.set_batch_status(bid, "running")
            else:
                await self._db.set_batch_status(bid, "previewed")

    async def _resolve_stuck_preview(
        self, job: dict[str, Any], actor: str, by_req: dict[str, dict[str, Any]]
    ) -> None:
        """Recover a preview_pending whose DMS request produced no data_job.

        - Conflict + prior request's preview job still ConfirmPending -> ADOPT it
          (the prior preview is exactly what we wanted; the normal poll then marks it
          preview_ready with the fingerprint).
        - Conflict + prior preview job now terminal (blocker gone, e.g. cancelled on
          re-preview) -> re-register for a fresh submit.
        - Conflict + prior still working -> wait (timeout backstop below).
        - Any other terminal request status (no job) -> preview_failed.
        - Non-terminal but no job past the timeout -> preview_failed (safety net)."""
        rid = job.get("dms_request_id")
        try:
            info = await self._dms.get_request(rid, actor=actor)
        except DmsApiError:
            info = None
        status = ((info or {}).get("request") or {}).get("status")
        if status == "Conflict" and info is not None:
            prior_id = _conflict_prior_request_id(info)
            prior_job = by_req.get(prior_id) if prior_id else None
            pstate = (prior_job or {}).get("state")
            if prior_job and pstate == "ConfirmPending":
                # adopt the prior's still-valid preview; step-3 poll will mark it ready
                await self._db.update_request(
                    job["id"],
                    dms_request_id=prior_id,
                    dms_job_id=prior_job["job_id"],
                    error=None,
                )
                job["dms_job_id"] = prior_job["job_id"]
                return
            if prior_job and pstate in _TERMINAL_DATA_JOB:
                # blocker resolved -> drop the conflicted request, re-submit fresh
                await self._db.update_request(
                    job["id"],
                    state="registered",
                    dms_request_id=None,
                    dms_job_id=None,
                    error=None,
                )
                return
            # prior has no job yet / still running: wait, then fall through to timeout
        elif status and status in _TERMINAL_LIFECYCLE:
            await self._db.update_request(
                job["id"], state="preview_failed", error=f"preview request {status}"
            )
            return
        # timeout backstop: a request that never yields a job must not wait forever.
        if _age_seconds(job.get("updated_at")) > self._settings.backup_preview_timeout_seconds:
            await self._db.update_request(
                job["id"],
                state="preview_failed",
                error="preview_timeout: DMS produced no job",
            )

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
        # Per-batch worker parallelism. None = use the DMS policy default; a single
        # node_count covers dsync and nsync (nsync source/dest hints fall back to it).
        if batch.get("node_count"):
            body["resources"] = {"node_count": batch["node_count"]}
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
        await self._execute_steps(batch, actor)
        # advance once nothing is approved/running: undecided preview_ready (operator
        # may approve more in stages) or leftover registered (released held) keep the
        # batch at 'previewed'; otherwise it's done.
        counts = await self._db.batch_state_counts(bid)
        if not counts.get("approved") and not counts.get("running"):
            await self._db.release_held(bid)
            after = await self._db.batch_state_counts(bid)
            if after.get("preview_ready") or after.get("registered"):
                await self._db.set_batch_status(bid, "previewed")
            else:
                await self._db.set_batch_status(bid, "done")

    async def _execute_steps(self, batch: dict[str, Any], actor: str) -> None:
        """Confirm operator-approved items and poll running ones to terminal. Shared
        by the execute phase AND the preview phase (so approving a ready item works
        even while other items in the batch are still previewing). No status change."""
        bid = batch["id"]
        running = await self._db.requests_in_states(bid, ["running"])

        # 1) confirm operator-approved jobs up to the cap (selective approval:
        #    preview_ready awaits a decision, only 'approved' gets executed).
        slots = max(0, self._settings.backup_concurrency - len(running))
        if slots:
            ready = await self._db.list_requests(bid, state="approved", limit=slots)
            for job in ready:
                await self._confirm_one(job, batch, actor)
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

    async def _confirm_one(
        self, job: dict[str, Any], batch: dict[str, Any], actor: str
    ) -> None:
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
                    # MUST match the requester the submit used (_submit_one above):
                    # DMS rejects a confirm whose requester_id differs from the data
                    # job's with a 409. Hardcoding "root" here meant every approved
                    # item failed whenever PORTAL_BACKUP_REQUESTER was not root.
                    "requester_id": batch.get("requester_id") or "root",
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
            # Scope to sync jobs (mirrors the scan orchestrator's data.scan filter)
            # so a freshly-submitted sync request_id never matches a scan/rm job.
            jobs = await self._dms.list_data_jobs(
                actor=actor, limit=500, operation="data.sync"
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
