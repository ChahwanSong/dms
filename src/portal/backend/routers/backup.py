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


class BackupRequestIn(BaseModel):
    src_storage: str
    src_path: str
    dst_storage: str
    dst_path: str


class BatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    delete_enabled: bool = False
    options: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None
    priority: str = "Low"
    node_count: int | None = Field(default=None, ge=1)  # None = use DMS policy default
    requests: list[BackupRequestIn] = Field(default_factory=list)


class BatchUpdate(BaseModel):
    """Partial edit of a draft batch's metadata/options. Only the fields actually
    sent are changed (``model_dump(exclude_unset=True)``)."""

    name: str | None = Field(default=None, max_length=200)
    delete_enabled: bool | None = None
    options: dict[str, Any] | None = None
    note: str | None = None
    priority: str | None = None
    node_count: int | None = Field(default=None, ge=1)  # None = use DMS policy default


class ApproveIn(BaseModel):
    """Selective approval: the preview_ready request ids to run. None / no body =
    approve all preview_ready (backward-compatible whole-batch approval)."""

    request_ids: list[int] | None = None


_TERMINAL_REQUEST_STATES = {"succeeded", "failed", "cancelled", "preview_failed"}


class ResetIn(BaseModel):
    """Reset fixable requests to 'registered' for re-preview (retry)."""

    request_ids: list[int] | None = None
    failed_only: bool = False


class DeleteIn(BaseModel):
    """Bulk delete the given request ids from a batch."""

    request_ids: list[int] = Field(default_factory=list)


class CancelIn(BaseModel):
    """Bulk cancel the given (non-terminal) request ids."""

    request_ids: list[int] = Field(default_factory=list)


# A request can be edited (and is reset to 'registered') only in these states —
# never while in-flight (preview_pending/approved/running) or succeeded.
_EDITABLE_REQUEST_STATES = {
    "registered",
    "preview_ready",
    "preview_failed",
    "failed",
    "cancelled",
}

# Selectable Volcano scheduling priorities for a batch (DMS maps to 200/100/50).
_PRIORITIES = {"High", "Mid", "Low"}


def _actor(user: dict[str, Any]) -> str:
    return str(user.get("username") or ROLE_OPERATOR)


def _clean_rel(path: str) -> str:
    """Storage-relative path: no leading/trailing slash, no traversal."""
    p = (path or "").strip().strip("/")
    if not p or ".." in p.split("/") or "\x00" in p:
        raise ValueError(f"invalid path: {path!r}")
    return p


def _normalize_requests(requests: list[BackupRequestIn]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for i, j in enumerate(requests):
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
            raise HTTPException(status_code=422, detail=f"request {i + 1}: {exc}") from exc
        if not rows[-1]["src_storage"] or not rows[-1]["dst_storage"]:
            raise HTTPException(
                status_code=422, detail=f"request {i + 1}: storage name required"
            )
    return rows


async def _require_draft_batch(db: Database, batch_id: str) -> dict[str, Any]:
    """Fetch a batch and assert it is editable (exists + still a draft)."""
    batch = await db.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_not_found")
    if batch["status"] != "draft":
        raise HTTPException(
            status_code=409, detail=f"cannot edit a '{batch['status']}' batch"
        )
    return batch


# Batch item (request) edits — add/delete/replace — are allowed on any batch that
# is NOT actively in flight; previewing/running are protected so we never mutate
# the request set the orchestrator is driving.
_INFLIGHT_BATCH_STATES = {"previewing", "running"}


async def _require_mutable_batch(db: Database, batch_id: str) -> dict[str, Any]:
    """Fetch a batch and assert its request set may be edited (exists + not in
    flight). Edited/added items become 'registered' and need a (re-)preview."""
    batch = await db.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_not_found")
    if batch["status"] in _INFLIGHT_BATCH_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"cannot edit items of a '{batch['status']}' batch — cancel or wait",
        )
    return batch


async def _require_request(
    db: Database, batch_id: str, request_id: int
) -> dict[str, Any]:
    """Fetch a request and assert it belongs to the given batch."""
    req = await db.get_request(request_id)
    if not req or req["batch_id"] != batch_id:
        raise HTTPException(status_code=404, detail="request_not_found")
    return req


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
        if payload.priority not in _PRIORITIES:
            raise HTTPException(status_code=422, detail="priority must be High, Mid, or Low")
        batch_id = uuid.uuid4().hex
        rows = _normalize_requests(payload.requests)
        await db.create_batch(
            batch_id=batch_id,
            name=payload.name.strip(),
            delete_enabled=payload.delete_enabled,
            options=payload.options or {},
            requester_id=requester,
            created_by=_actor(user),
            note=payload.note,
            priority=payload.priority,
            node_count=payload.node_count,
        )
        added = await db.add_requests(batch_id, rows)
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

    @router.patch("/batches/{batch_id}")
    async def update_batch(
        batch_id: str,
        payload: BatchUpdate,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        await _require_draft_batch(db, batch_id)
        fields = payload.model_dump(exclude_unset=True)
        if not fields:
            raise HTTPException(status_code=422, detail="no fields to update")
        if "name" in fields:
            name = (fields["name"] or "").strip()
            if not name:
                raise HTTPException(status_code=422, detail="name must not be empty")
            fields["name"] = name
        if "priority" in fields and fields["priority"] not in _PRIORITIES:
            raise HTTPException(status_code=422, detail="priority must be High, Mid, or Low")
        await db.update_batch(batch_id, **fields)
        return await db.get_batch(batch_id)

    @router.put("/batches/{batch_id}/requests")
    async def replace_requests(
        batch_id: str,
        requests: list[BackupRequestIn],
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        """Replace the entire request set (inline-table editor / CSV upload). Allowed
        on any non-in-flight batch; the new set is all 'registered' (needs preview)."""
        await _require_mutable_batch(db, batch_id)
        rows = _normalize_requests(requests)
        count = await db.replace_requests(batch_id, rows)
        return {"id": batch_id, "count": count}

    @router.post("/batches/{batch_id}/requests:add")
    async def add_requests(
        batch_id: str,
        requests: list[BackupRequestIn],
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        """Append requests (registered) to a non-in-flight batch."""
        await _require_mutable_batch(db, batch_id)
        if not requests:
            raise HTTPException(status_code=422, detail="no requests to add")
        rows = _normalize_requests(requests)
        added = await db.add_requests(batch_id, rows)
        return {"id": batch_id, "added": added}

    @router.post("/batches/{batch_id}/requests:delete")
    async def delete_requests(
        batch_id: str,
        payload: DeleteIn,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        """Delete the given requests from a non-in-flight batch (in-flight items skipped)."""
        await _require_mutable_batch(db, batch_id)
        if not payload.request_ids:
            raise HTTPException(status_code=422, detail="request_ids required")
        deleted = await db.delete_requests(batch_id, payload.request_ids)
        return {"id": batch_id, "deleted": deleted}

    @router.get("/batches/{batch_id}/requests")
    async def list_requests(
        batch_id: str,
        state: str | None = Query(default=None),
        limit: int = Query(default=200, le=2000),
        offset: int = Query(default=0, ge=0),
        db: Database = Depends(get_db),
    ) -> list[dict[str, Any]]:
        return await db.list_requests(batch_id, state=state, limit=limit, offset=offset)

    @router.patch("/batches/{batch_id}/requests/{request_id}")
    async def update_request(
        batch_id: str,
        request_id: int,
        payload: BackupRequestIn,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        if not await db.get_batch(batch_id):
            raise HTTPException(status_code=404, detail="batch_not_found")
        req = await _require_request(db, batch_id, request_id)
        if req["state"] not in _EDITABLE_REQUEST_STATES:
            raise HTTPException(
                status_code=409, detail=f"cannot edit a '{req['state']}' request"
            )
        row = _normalize_requests([payload])[0]
        await db.edit_request_paths(request_id, row)
        return await db.get_request(request_id)

    @router.post("/batches/{batch_id}/requests/{request_id}:cancel")
    async def cancel_one_request(
        batch_id: str,
        request_id: int,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        """Cancel a single non-terminal request without cancelling the batch.
        Best-effort cancels the live DMS job if one is in flight."""
        if not await db.get_batch(batch_id):
            raise HTTPException(status_code=404, detail="batch_not_found")
        req = await _require_request(db, batch_id, request_id)
        if req["state"] in _TERMINAL_REQUEST_STATES:
            raise HTTPException(status_code=409, detail=f"request already {req['state']}")
        _changed, job_id = await db.cancel_request(batch_id, request_id)
        dms_cancelled = 0
        if job_id:
            actor = f"{settings.backup_actor_prefix}{_actor(user)}"
            try:
                await dms.cancel_job(job_id, actor=actor)
                dms_cancelled = 1
            except DmsApiError:
                pass
        return {
            "id": batch_id,
            "request_id": request_id,
            "cancelled": True,
            "dms_cancelled": dms_cancelled,
        }

    @router.post("/batches/{batch_id}/requests:cancel")
    async def cancel_requests_bulk(
        batch_id: str,
        payload: CancelIn,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        """Cancel the selected non-terminal requests (bulk); best-effort cancel any
        live DMS jobs. Works even while running (it's the in-flight escape hatch)."""
        if not await db.get_batch(batch_id):
            raise HTTPException(status_code=404, detail="batch_not_found")
        if not payload.request_ids:
            raise HTTPException(status_code=422, detail="request_ids required")
        live = await db.cancel_requests(batch_id, request_ids=payload.request_ids)
        actor = f"{settings.backup_actor_prefix}{_actor(user)}"
        cancelled = 0
        for job_id in live:
            try:
                await dms.cancel_job(job_id, actor=actor)
                cancelled += 1
            except DmsApiError:
                pass
        return {"id": batch_id, "cancelled": True, "dms_cancelled": cancelled}

    @router.post("/batches/{batch_id}/requests:reset")
    async def reset_requests(
        batch_id: str,
        payload: ResetIn | None = None,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        """Reset fixable requests to 'registered' so a re-preview re-runs them
        (retry). Then the operator triggers :preview again."""
        batch = await db.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        if batch["status"] == "previewing":
            raise HTTPException(status_code=409, detail="cannot reset while previewing")
        p = payload or ResetIn()
        if not p.failed_only and not p.request_ids:
            raise HTTPException(
                status_code=422, detail="specify request_ids or failed_only"
            )
        n = await db.reset_requests(
            batch_id, request_ids=p.request_ids, failed_only=p.failed_only
        )
        return {"id": batch_id, "reset": n}

    @router.post("/batches/{batch_id}:preview")
    async def preview(
        batch_id: str, db: Database = Depends(get_db)
    ) -> dict[str, Any]:
        batch = await db.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        if batch["status"] not in ("draft", "previewed", "done"):
            raise HTTPException(
                status_code=409, detail=f"cannot preview from {batch['status']}"
            )
        counts = await db.batch_state_counts(batch_id)
        if not counts.get("registered"):
            raise HTTPException(status_code=422, detail="no registered requests to preview")
        await db.set_batch_status(batch_id, "previewing")
        return {"id": batch_id, "status": "previewing"}

    @router.post("/batches/{batch_id}:approve")
    async def approve(
        batch_id: str,
        payload: ApproveIn | None = None,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        batch = await db.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        # previewed = first decision; running = approve more in stages.
        if batch["status"] not in ("previewed", "running"):
            raise HTTPException(
                status_code=409, detail=f"cannot approve a '{batch['status']}' batch"
            )
        request_ids = payload.request_ids if payload else None
        approved = await db.approve_requests(batch_id, request_ids)
        if not approved:
            raise HTTPException(
                status_code=422, detail="no preview_ready requests to approve"
            )
        await db.set_batch_status(batch_id, "running")
        return {"id": batch_id, "status": "running", "approved": approved}

    @router.post("/batches/{batch_id}:close")
    async def close(
        batch_id: str, db: Database = Depends(get_db)
    ) -> dict[str, Any]:
        """Finish a batch: drop still-undecided preview_ready requests, then
        complete if nothing is approved/running."""
        batch = await db.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        if batch["status"] not in ("previewed", "running"):
            raise HTTPException(
                status_code=409, detail=f"cannot close a '{batch['status']}' batch"
            )
        excluded = await db.exclude_preview_ready(batch_id)
        counts = await db.batch_state_counts(batch_id)
        if not counts.get("approved") and not counts.get("running"):
            await db.set_batch_status(batch_id, "done")
        batch = await db.get_batch(batch_id)
        return {"id": batch_id, "status": batch["status"], "excluded": excluded}

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
        live = await db.cancel_requests(batch_id)
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
