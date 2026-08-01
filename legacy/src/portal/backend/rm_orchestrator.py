"""Data-rm orchestrator (데이터 삭제 탭).

A single background asyncio loop that drives ONE-SHOT ``data.rm`` (delete) jobs
through the DMS preview -> confirm -> execute flow WITHOUT changing DMS. This mirrors
the sync orchestrator exactly; the only differences are a SINGLE ``target`` (drm
removes one path — there is no source/destination) and no delete flag (rm IS the
delete). Each ``rm_jobs`` row is the top-level unit (no batch, no re-run).

Per non-terminal job:
  registered      : submit to DMS (a non-destructive dry-run preview) up to a
                    concurrency cap -> preview_pending.
  preview_pending : resolve request_id -> job_id, poll to ConfirmPending, capture the
                    preview summary + fingerprint -> preview_ready (or preview_failed).
                    A request that never yields a job past the timeout is failed.
  preview_ready   : awaits the operator's 실행(승인) (approved flag).
  preview_ready + approved : confirm with the observed fingerprint -> running.
  running         : poll to a terminal state -> succeeded / failed.

Rm jobs run as the configured ``backup_requester`` (default root; DMS gates this
behind an mTLS-verified operator), so DMS calls use actor "mtls:<operator>" derived
from each job's ``created_by``. Single sequential loop == single writer (no locking);
state lives in Postgres, so the loop is crash-tolerant (in-flight rows are re-polled).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .db import Database
from .dms_client import DmsApiError, DmsClient

log = logging.getLogger("portal.rm")

# DMS DataJobState -> our coarse outcome buckets (mirrors the sync orchestrator).
_PREVIEW_READY = {"ConfirmPending"}
_PREVIEW_FAILED = {"PreflightFailed", "Failed", "PreviewExpired", "Cancelled", "TimedOut"}
_EXEC_SUCCEEDED = {"Succeeded"}
_EXEC_FAILED = {"Failed", "PreflightFailed", "PreviewExpired", "Cancelled", "TimedOut"}


def _age_seconds(updated_at: Any) -> float:
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
        # drm preview also reports whether the target path exists at all, so the UI can
        # distinguish "nothing to delete (target absent)" from "0 files".
        "target_absent": s.get("target_absent"),
        "tool": s.get("selected_tool") or dms_job.get("selected_tool"),
    }
    return metrics, pv.get("fingerprint")


def _reason(dms_job: dict[str, Any]) -> str:
    pf = dms_job.get("preflight_result") or {}
    return pf.get("reason") or pf.get("error") or dms_job.get("state") or "unknown"


def rm_body(job: dict[str, Any]) -> dict[str, Any]:
    """The DMS DataJobRequest for one one-shot rm job. A SINGLE target (drm removes one
    path); rm options (stat/lite/quiet) pass through the options blob. ``recursive`` is
    ALWAYS forced true — DMS rejects a directory rm without it (domain.py
    _validate_data_rm_options), and deleting a managed subtree is inherently recursive.
    stat/lite mutual exclusion is enforced in the form; DMS re-validates."""
    options: dict[str, Any] = dict(job.get("options") or {})
    options["recursive"] = True
    body: dict[str, Any] = {
        "requester_id": job.get("requester_id") or "root",
        "target": {"storage_name": job["target_storage"], "path": job["target_path"]},
        "options": options,
    }
    if job.get("owner_username"):
        body["owner_username"] = job["owner_username"]
    return body


class RmOrchestrator:
    def __init__(self, db: Database, dms: DmsClient, settings: Settings):
        self._db = db
        self._dms = dms
        self._settings = settings
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if not self._db.configured or not self._settings.dms_configured:
            return
        self._task = asyncio.create_task(self._run(), name="rm-orchestrator")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def _actor(self, job: dict[str, Any]) -> str:
        op = job.get("created_by") or self._settings.dms_actor
        return f"{self._settings.backup_actor_prefix}{op}"

    async def _run(self) -> None:
        log.info("rm orchestrator started")
        while not self._stopping.is_set():
            try:
                await self._drive()
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("rm orchestrator cycle error")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._settings.backup_poll_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def _drive(self) -> None:
        # 1) submit registered jobs up to the concurrency cap (in-flight = preview_
        #    pending + running). Oldest-first (FIFO).
        inflight = await self._db.rm_jobs_in_states(["preview_pending", "running"])
        slots = max(0, self._settings.backup_concurrency - len(inflight))
        if slots:
            registered = await self._db.rm_jobs_in_states(["registered"])
            for job in registered[:slots]:
                await self._submit_one(job)

        # 2) resolve request_id -> job_id, then poll preview_pending jobs.
        pending = await self._db.rm_jobs_in_states(["preview_pending"])
        await self._resolve_job_ids(pending)
        for job in pending:
            if job.get("dms_job_id"):
                await self._poll_preview(job)
            elif job.get("dms_request_id") and (
                _age_seconds(job.get("updated_at"))
                > self._settings.backup_preview_timeout_seconds
            ):
                await self._db.update_rm_job(
                    job["id"],
                    state="preview_failed",
                    error="preview_timeout: DMS produced no job",
                )

        # 3) confirm operator-approved preview_ready jobs.
        for job in await self._db.rm_jobs_in_states(["preview_ready"]):
            if job.get("approved"):
                await self._confirm_one(job)

        # 4) poll running jobs to terminal.
        for job in await self._db.rm_jobs_in_states(["running"]):
            await self._poll_exec(job)

    async def _submit_one(self, job: dict[str, Any]) -> None:
        actor = self._actor(job)
        body = rm_body(job)
        body["priority"] = job.get("priority") or self._settings.backup_priority
        if job.get("node_count"):
            body["resources"] = {"node_count": job["node_count"]}
        try:
            resp = await self._dms.submit_rm(body, actor=actor)
        except DmsApiError as exc:
            await self._db.update_rm_job(
                job["id"], state="preview_failed", error=str(exc.detail)
            )
            return
        await self._db.update_rm_job(
            job["id"], dms_request_id=resp.get("request_id"), state="preview_pending"
        )

    async def _resolve_job_ids(self, pending: list[dict[str, Any]]) -> None:
        unresolved = [
            j for j in pending if j.get("dms_request_id") and not j.get("dms_job_id")
        ]
        if not unresolved:
            return
        by_req: dict[str, dict[str, Any]] = {}
        for actor in {self._actor(j) for j in unresolved}:
            by_req.update(await self._data_jobs_by_request(actor))
        for job in unresolved:
            dj = by_req.get(job["dms_request_id"])
            if dj:
                await self._db.update_rm_job(job["id"], dms_job_id=dj["job_id"])
                job["dms_job_id"] = dj["job_id"]

    async def _poll_preview(self, job: dict[str, Any]) -> None:
        try:
            dj = await self._dms.get_rm_job(job["dms_job_id"], actor=self._actor(job))
        except DmsApiError as exc:
            log.warning("preview poll failed job=%s: %s", job["dms_job_id"], exc)
            return
        state = dj.get("state")
        if state in _PREVIEW_READY:
            metrics, fp = _preview_metrics(dj)
            await self._db.update_rm_job(
                job["id"], state="preview_ready", fingerprint=fp, preview=metrics
            )
        elif state in _PREVIEW_FAILED:
            await self._db.update_rm_job(
                job["id"], state="preview_failed", error=_reason(dj)
            )

    async def _confirm_one(self, job: dict[str, Any]) -> None:
        if not job.get("dms_job_id") or not job.get("fingerprint"):
            await self._db.update_rm_job(
                job["id"], state="failed", error="missing job_id/fingerprint"
            )
            return
        await self._db.update_rm_job(job["id"], state="running")
        try:
            await self._dms.confirm_job(
                job["dms_job_id"],
                {
                    "confirm": True,
                    "preview_observed_hash": job["fingerprint"],
                    "requester_id": job.get("requester_id") or "root",
                },
                actor=self._actor(job),
            )
        except DmsApiError as exc:
            await self._db.update_rm_job(
                job["id"], state="failed", error=f"confirm: {exc.detail}"
            )

    async def _poll_exec(self, job: dict[str, Any]) -> None:
        if not job.get("dms_job_id"):
            return
        try:
            dj = await self._dms.get_rm_job(job["dms_job_id"], actor=self._actor(job))
        except DmsApiError as exc:
            log.warning("exec poll failed job=%s: %s", job["dms_job_id"], exc)
            return
        state = dj.get("state")
        if state in _EXEC_SUCCEEDED:
            rs = dj.get("result_summary") or {}
            await self._db.update_rm_job(
                job["id"], state="succeeded", result=rs.get("execution") or rs
            )
        elif state in _EXEC_FAILED:
            await self._db.update_rm_job(
                job["id"], state="failed", error=_reason(dj)
            )

    async def _data_jobs_by_request(self, actor: str) -> dict[str, dict[str, Any]]:
        try:
            jobs = await self._dms.list_data_jobs(
                actor=actor, limit=500, operation="data.rm"
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
