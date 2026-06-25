"""Operator data-backup API (role: operator).

Register a list of DMS DM **sync** jobs (a "batch", up to a few thousand) and run
them as a mirror backup (optionally with --delete). Safety model: the operator
PREVIEWS the whole batch (non-destructive dry-run on every job), reviews the
aggregate (files / bytes / failures), then APPROVES, after which the BFF
orchestrator auto-confirms + executes each job. Persistence is the portal DB; the
actual sync runs on DMS (this BFF never touches data directly).

State machine (batch.status): draft -> previewing -> previewed -> running -> done
(or cancelled). Job state: registered -> preview_pending -> preview_ready ->
running -> succeeded|failed (preview_failed / cancelled are terminal too).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import Settings
from ..db import Database
from ..deps import get_db, get_dms_client
from ..dms_client import DmsApiError, DmsClient
from ..security import ROLE_OPERATOR, require_role


class BackupJobIn(BaseModel):
    src_storage: str
    src_path: str
    dst_storage: str
    dst_path: str


class BatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    delete_enabled: bool = False
    options: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None
    jobs: list[BackupJobIn] = Field(default_factory=list)


def _actor(user: dict[str, Any]) -> str:
    return str(user.get("username") or ROLE_OPERATOR)


def _clean_rel(path: str) -> str:
    """Storage-relative path: no leading/trailing slash, no traversal."""
    p = (path or "").strip().strip("/")
    if not p or ".." in p.split("/") or "\x00" in p:
        raise ValueError(f"invalid path: {path!r}")
    return p


def _normalize_jobs(jobs: list[BackupJobIn]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for i, j in enumerate(jobs):
        try:
            rows.append(
                {
                    "src_storage": j.src_storage.strip(),
                    "src_path": _clean_rel(j.src_path),
                    "dst_storage": j.dst_storage.strip(),
                    "dst_path": _clean_rel(j.dst_path),
                }
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"job {i + 1}: {exc}") from exc
        if not rows[-1]["src_storage"] or not rows[-1]["dst_storage"]:
            raise HTTPException(
                status_code=422, detail=f"job {i + 1}: storage name required"
            )
    return rows


def backup_router(settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/operator/backup",
        tags=["operator-backup"],
        dependencies=[Depends(require_role(ROLE_OPERATOR))],
    )
    requester = settings.backup_requester

    @router.post("/batches", status_code=201)
    async def create_batch(
        payload: BatchCreate,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        batch_id = uuid.uuid4().hex
        rows = _normalize_jobs(payload.jobs)
        await db.create_batch(
            batch_id=batch_id,
            name=payload.name.strip(),
            delete_enabled=payload.delete_enabled,
            options=payload.options or {},
            requester_id=requester,
            created_by=_actor(user),
            note=payload.note,
        )
        added = await db.add_jobs(batch_id, rows)
        return {"id": batch_id, "added": added}

    @router.get("/batches")
    async def list_batches(db: Database = Depends(get_db)) -> list[dict[str, Any]]:
        return await db.list_batches()

    @router.get("/batches/{batch_id}")
    async def get_batch(
        batch_id: str, db: Database = Depends(get_db)
    ) -> dict[str, Any]:
        batch = await db.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        batch["state_counts"] = await db.batch_state_counts(batch_id)
        batch["preview_totals"] = await db.preview_totals(batch_id)
        return batch

    @router.delete("/batches/{batch_id}")
    async def delete_batch(
        batch_id: str, db: Database = Depends(get_db)
    ) -> dict[str, Any]:
        batch = await db.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        if batch["status"] in ("previewing", "running"):
            raise HTTPException(
                status_code=409, detail="cancel the batch before deleting"
            )
        await db.delete_batch(batch_id)
        return {"id": batch_id, "deleted": True}

    @router.post("/batches/{batch_id}/jobs")
    async def add_jobs(
        batch_id: str,
        jobs: list[BackupJobIn],
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        batch = await db.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        if batch["status"] != "draft":
            raise HTTPException(
                status_code=409, detail="jobs can only be added to a draft batch"
            )
        rows = _normalize_jobs(jobs)
        added = await db.add_jobs(batch_id, rows)
        return {"id": batch_id, "added": added}

    @router.get("/batches/{batch_id}/jobs")
    async def list_jobs(
        batch_id: str,
        state: str | None = Query(default=None),
        limit: int = Query(default=200, le=2000),
        offset: int = Query(default=0, ge=0),
        db: Database = Depends(get_db),
    ) -> list[dict[str, Any]]:
        return await db.list_jobs(batch_id, state=state, limit=limit, offset=offset)

    @router.post("/batches/{batch_id}:preview")
    async def preview(
        batch_id: str, db: Database = Depends(get_db)
    ) -> dict[str, Any]:
        batch = await db.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        if batch["status"] not in ("draft", "previewed"):
            raise HTTPException(
                status_code=409, detail=f"cannot preview from {batch['status']}"
            )
        counts = await db.batch_state_counts(batch_id)
        if not counts.get("registered"):
            raise HTTPException(status_code=422, detail="no registered jobs to preview")
        await db.set_batch_status(batch_id, "previewing")
        return {"id": batch_id, "status": "previewing"}

    @router.post("/batches/{batch_id}:approve")
    async def approve(
        batch_id: str, db: Database = Depends(get_db)
    ) -> dict[str, Any]:
        batch = await db.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        if batch["status"] != "previewed":
            raise HTTPException(
                status_code=409, detail="batch must be 'previewed' to approve"
            )
        counts = await db.batch_state_counts(batch_id)
        if not counts.get("preview_ready"):
            raise HTTPException(
                status_code=422, detail="no preview_ready jobs to run"
            )
        await db.set_batch_status(batch_id, "running")
        return {"id": batch_id, "status": "running", "to_run": counts["preview_ready"]}

    @router.post("/batches/{batch_id}:cancel")
    async def cancel(
        batch_id: str,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        batch = await db.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        await db.set_batch_status(batch_id, "cancelled")
        live = await db.cancel_jobs(batch_id)
        actor = f"{settings.backup_actor_prefix}{_actor(user)}"
        cancelled = 0
        for job_id in live:
            try:
                await dms.cancel_job(job_id, actor=actor)
                cancelled += 1
            except DmsApiError:
                pass  # best-effort; terminal jobs simply ignore cancel
        return {"id": batch_id, "status": "cancelled", "dms_cancelled": cancelled}

    return router
