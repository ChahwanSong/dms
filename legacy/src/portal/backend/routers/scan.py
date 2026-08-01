"""Operator data-scan API (role: operator).

Register a list of DMS DM **scan** jobs (a "batch", up to a few thousand) and run
them. Scan is READ-ONLY and has NO preview/confirm/approve flow — ``POST /scan``
runs directly (Pending -> Running -> Succeeded) — so this is a SIMPLIFIED mirror of
the data-backup tab: a batch has a single ``:run`` action (plus ``:rescan`` to
re-run a finished batch). Persistence is the portal DB; the actual scan runs on DMS
(this BFF never touches data directly).

State machine (batch.status): draft -> scanning -> done (or cancelled). Request
state: registered -> running -> succeeded|failed (cancelled is terminal too; 'held'
is an internal parking state used by a selective run).
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


class ScanRequestIn(BaseModel):
    storage: str
    path: str


class BatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    options: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None
    priority: str = "Low"
    node_count: int | None = Field(default=None, ge=1)  # None = use DMS policy default
    requests: list[ScanRequestIn] = Field(default_factory=list)


class BatchUpdate(BaseModel):
    """Partial edit of a batch's metadata/options. Only the fields actually sent are
    changed (``model_dump(exclude_unset=True)``)."""

    name: str | None = Field(default=None, max_length=200)
    options: dict[str, Any] | None = None
    note: str | None = None
    priority: str | None = None
    node_count: int | None = Field(default=None, ge=1)  # None = use DMS policy default


class RunIn(BaseModel):
    """Selective run: only these registered request ids are scanned; the rest are
    parked ('held') and restored to 'registered' afterwards. None = run all."""

    request_ids: list[int] | None = None


class ResetIn(BaseModel):
    """Reset fixable requests to 'registered' for a re-run (retry)."""

    request_ids: list[int] | None = None
    failed_only: bool = False


class DeleteIn(BaseModel):
    """Bulk delete the given request ids from a batch."""

    request_ids: list[int] = Field(default_factory=list)


class CancelIn(BaseModel):
    """Bulk cancel the given (non-terminal) request ids."""

    request_ids: list[int] = Field(default_factory=list)


class BatchIdsIn(BaseModel):
    """Bulk batch operation (delete / cancel) over the given batch ids."""

    batch_ids: list[str] = Field(default_factory=list)


# A scan request is terminal in these states (its DMS job, if any, is settled).
_TERMINAL_REQUEST_STATES = {"succeeded", "failed", "cancelled"}

# A request can be edited / reset only in these states — never while in-flight
# (running). Scan can re-run a succeeded request, so 'succeeded' is editable too.
_EDITABLE_REQUEST_STATES = {"registered", "failed", "cancelled", "succeeded"}

# Batch item (request) edits — add/delete/replace — are allowed on any batch that is
# NOT actively scanning; 'scanning' is protected so we never mutate the request set
# the orchestrator is driving.
_INFLIGHT_BATCH_STATES = {"scanning"}

# Selectable Volcano scheduling priorities for a batch (DMS maps to 200/100/50).
_PRIORITIES = {"High", "Mid", "Low"}

# The only DMS scan options (DATA_SCAN_OPTION_TYPES) the operator may set, with the
# expected python type. Unknown keys are dropped on write (lenient, like the backup
# tab); max_depth must be a non-negative integer.
_SCAN_OPTION_TYPES: dict[str, type] = {
    "summary_only": bool,
    "max_depth": int,
    "follow_symlinks": bool,
    "one_file_system": bool,
}


def _actor(user: dict[str, Any]) -> str:
    return str(user.get("username") or ROLE_OPERATOR)


def _clean_rel(path: str) -> str:
    """Storage-relative path: no leading/trailing slash, no traversal."""
    p = (path or "").strip().strip("/")
    if not p or ".." in p.split("/") or "\x00" in p:
        raise ValueError(f"invalid path: {path!r}")
    return p


def _normalize_requests(requests: list[ScanRequestIn]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for i, r in enumerate(requests):
        try:
            rows.append({"storage": r.storage.strip(), "path": _clean_rel(r.path)})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"request {i + 1}: {exc}") from exc
        if not rows[-1]["storage"]:
            raise HTTPException(
                status_code=422, detail=f"request {i + 1}: storage name required"
            )
    return rows


def _validate_options(options: dict[str, Any]) -> dict[str, Any]:
    """Keep only the four DMS scan options, type-checked; drop everything else.
    bool/int are validated and ``max_depth`` must be >= 0."""
    clean: dict[str, Any] = {}
    for key, expected in _SCAN_OPTION_TYPES.items():
        if key not in options:
            continue
        val = options[key]
        if expected is bool:
            if not isinstance(val, bool):
                raise HTTPException(
                    status_code=422, detail=f"option {key} must be a boolean"
                )
            clean[key] = val
        else:  # int (max_depth) — bool is a subclass of int, so reject it explicitly
            if isinstance(val, bool) or not isinstance(val, int):
                raise HTTPException(
                    status_code=422, detail=f"option {key} must be an integer"
                )
            if val < 0:
                raise HTTPException(
                    status_code=422, detail=f"option {key} must be >= 0"
                )
            clean[key] = val
    return clean


async def _require_mutable_batch(db: Database, batch_id: str) -> dict[str, Any]:
    """Fetch a batch and assert it (and its request set) may be edited — exists and
    not actively scanning. Edited/added items become 'registered' and are re-run on
    the next ``:run``."""
    batch = await db.get_scan_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_not_found")
    if batch["status"] in _INFLIGHT_BATCH_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"cannot edit a '{batch['status']}' batch — cancel or wait",
        )
    return batch


async def _require_request(
    db: Database, batch_id: str, request_id: int
) -> dict[str, Any]:
    """Fetch a scan request and assert it belongs to the given batch."""
    req = await db.get_scan_request(request_id)
    if not req or req["batch_id"] != batch_id:
        raise HTTPException(status_code=404, detail="request_not_found")
    return req


def scan_router(settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/operator/scan",
        tags=["operator-scan"],
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
        options = _validate_options(payload.options or {})
        await db.create_scan_batch(
            batch_id=batch_id,
            name=payload.name.strip(),
            options=options,
            requester_id=requester,
            created_by=_actor(user),
            note=payload.note,
            priority=payload.priority,
            node_count=payload.node_count,
        )
        added = await db.add_scan_requests(batch_id, rows)
        return {"id": batch_id, "added": added}

    @router.get("/node-policy")
    async def node_policy(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        """DMS worker-node policy default for scans (tool ``dscan``), so the UI can
        show what "자동" resolves to. null when no policy is configured."""
        actor = f"{settings.backup_actor_prefix}{_actor(user)}"
        try:
            policies = await dms.list_data_management_policies(actor=actor)
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)
        by_op = {p.get("operation"): p for p in (policies or [])}

        def pick(op: str) -> dict[str, Any] | None:
            p = by_op.get(op)
            if not p:
                return None
            return {
                "default_worker_nodes": p.get("default_worker_nodes"),
                "max_worker_nodes": p.get("max_worker_nodes"),
            }

        # DMS keys the policy by OPERATION ("scan"), not the CLI tool name ("dscan").
        return {"dscan": pick("scan")}

    @router.get("/batches")
    async def list_batches(db: Database = Depends(get_db)) -> list[dict[str, Any]]:
        return await db.list_scan_batches()

    @router.post("/batches:delete")
    async def delete_batches(
        payload: BatchIdsIn,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        """Bulk-delete scan batches, skip-and-report (never 409). Actively scanning
        batches are skipped with reason 'active'; unknown ids with reason
        'not_found'. Distinct path from POST /batches and /batches/{id}."""
        ids = payload.batch_ids
        if not ids:
            raise HTTPException(status_code=422, detail="batch_ids required")
        statuses = await db.scan_batch_statuses(ids)
        skipped: list[dict[str, str]] = []
        seen: set[str] = set()
        for bid in ids:
            if bid in seen:
                continue
            seen.add(bid)
            status = statuses.get(bid)
            if status is None:
                skipped.append({"id": bid, "reason": "not_found"})
            elif status in _INFLIGHT_BATCH_STATES:
                skipped.append({"id": bid, "reason": "active"})
        deleted = await db.delete_scan_batches(ids)
        return {"deleted": deleted, "skipped": skipped}

    @router.post("/batches:cancel")
    async def cancel_batches(
        payload: BatchIdsIn,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        """Bulk-cancel scan batches (same per-batch logic as the single :cancel): set
        each to cancelled and best-effort cancel any live DMS jobs. Unknown ids are
        skipped with reason 'not_found'."""
        ids = payload.batch_ids
        if not ids:
            raise HTTPException(status_code=422, detail="batch_ids required")
        actor = f"{settings.backup_actor_prefix}{_actor(user)}"
        cancelled: list[str] = []
        skipped: list[dict[str, str]] = []
        dms_cancelled = 0
        seen: set[str] = set()
        for bid in ids:
            if bid in seen:
                continue
            seen.add(bid)
            if not await db.get_scan_batch(bid):
                skipped.append({"id": bid, "reason": "not_found"})
                continue
            await db.set_scan_batch_status(bid, "cancelled")
            live = await db.cancel_scan_requests(bid)
            for job_id in live:
                try:
                    await dms.cancel_job(job_id, actor=actor)
                    dms_cancelled += 1
                except DmsApiError:
                    pass  # best-effort; terminal jobs simply ignore cancel
            cancelled.append(bid)
        return {"cancelled": cancelled, "dms_cancelled": dms_cancelled, "skipped": skipped}

    @router.get("/batches/{batch_id}")
    async def get_batch(
        batch_id: str, db: Database = Depends(get_db)
    ) -> dict[str, Any]:
        batch = await db.get_scan_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        batch["state_counts"] = await db.scan_batch_state_counts(batch_id)
        batch["result_totals"] = await db.scan_result_totals(batch_id)
        return batch

    @router.delete("/batches/{batch_id}")
    async def delete_batch(
        batch_id: str, db: Database = Depends(get_db)
    ) -> dict[str, Any]:
        batch = await db.get_scan_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        if batch["status"] == "scanning":
            raise HTTPException(
                status_code=409, detail="cancel the batch before deleting"
            )
        await db.delete_scan_batch(batch_id)
        return {"id": batch_id, "deleted": True}

    @router.patch("/batches/{batch_id}")
    async def update_batch(
        batch_id: str,
        payload: BatchUpdate,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        await _require_mutable_batch(db, batch_id)
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
        if "options" in fields:
            fields["options"] = _validate_options(fields["options"] or {})
        await db.update_scan_batch(batch_id, **fields)
        return await db.get_scan_batch(batch_id)

    @router.put("/batches/{batch_id}/requests")
    async def replace_requests(
        batch_id: str,
        requests: list[ScanRequestIn],
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        """Replace the entire request set (inline-table editor / CSV upload). Allowed
        on any non-scanning batch; the new set is all 'registered'."""
        await _require_mutable_batch(db, batch_id)
        rows = _normalize_requests(requests)
        count = await db.replace_scan_requests(batch_id, rows)
        return {"id": batch_id, "count": count}

    @router.post("/batches/{batch_id}/requests:add")
    async def add_requests(
        batch_id: str,
        requests: list[ScanRequestIn],
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        """Append requests (registered) to a non-scanning batch."""
        await _require_mutable_batch(db, batch_id)
        if not requests:
            raise HTTPException(status_code=422, detail="no requests to add")
        rows = _normalize_requests(requests)
        added = await db.add_scan_requests(batch_id, rows)
        return {"id": batch_id, "added": added}

    @router.post("/batches/{batch_id}/requests:delete")
    async def delete_requests(
        batch_id: str,
        payload: DeleteIn,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        """Delete the given requests from a non-scanning batch (in-flight items skipped)."""
        await _require_mutable_batch(db, batch_id)
        if not payload.request_ids:
            raise HTTPException(status_code=422, detail="request_ids required")
        deleted = await db.delete_scan_requests(batch_id, payload.request_ids)
        return {"id": batch_id, "deleted": deleted}

    @router.get("/batches/{batch_id}/requests")
    async def list_requests(
        batch_id: str,
        state: str | None = Query(default=None),
        limit: int = Query(default=200, le=2000),
        offset: int = Query(default=0, ge=0),
        db: Database = Depends(get_db),
    ) -> list[dict[str, Any]]:
        return await db.list_scan_requests(
            batch_id, state=state, limit=limit, offset=offset
        )

    @router.patch("/batches/{batch_id}/requests/{request_id}")
    async def update_request(
        batch_id: str,
        request_id: int,
        payload: ScanRequestIn,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        if not await db.get_scan_batch(batch_id):
            raise HTTPException(status_code=404, detail="batch_not_found")
        req = await _require_request(db, batch_id, request_id)
        if req["state"] not in _EDITABLE_REQUEST_STATES:
            raise HTTPException(
                status_code=409, detail=f"cannot edit a '{req['state']}' request"
            )
        row = _normalize_requests([payload])[0]
        await db.edit_scan_request_path(request_id, row)
        return await db.get_scan_request(request_id)

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
        if not await db.get_scan_batch(batch_id):
            raise HTTPException(status_code=404, detail="batch_not_found")
        req = await _require_request(db, batch_id, request_id)
        if req["state"] in _TERMINAL_REQUEST_STATES:
            raise HTTPException(status_code=409, detail=f"request already {req['state']}")
        _changed, job_id = await db.cancel_scan_request(batch_id, request_id)
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

    @router.get("/batches/{batch_id}/requests/{request_id}/job")
    async def request_job(
        batch_id: str,
        request_id: int,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        """Live DMS scan-job detail for a single request (read-only). Returns the
        FULL DMS job dict (state, result_summary incl file_size_histogram,
        preflight_result, selected_tool, volcano_job_ref, artifact_uri, log_uri,
        timestamps) — fuller than the portal-stored subset. ``{available:false}``
        when the request has no DMS job yet (still registered / never submitted)."""
        if not await db.get_scan_batch(batch_id):
            raise HTTPException(status_code=404, detail="batch_not_found")
        req = await _require_request(db, batch_id, request_id)
        job_id = req.get("dms_job_id")
        if not job_id:
            return {"available": False, "note": "아직 DMS 작업이 시작되지 않았습니다."}
        actor = f"{settings.backup_actor_prefix}{_actor(user)}"
        try:
            return await dms.get_scan_job(job_id, actor=actor)
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    @router.get("/batches/{batch_id}/requests/{request_id}/logs")
    async def request_logs(
        batch_id: str,
        request_id: int,
        tail: int = Query(default=400, ge=1, le=5000),
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        """Tail the launcher pod logs of a request's DMS job (read-only). Proxies
        DMS GET /operations/data-jobs/{job_id}/logs verbatim
        ({job_id, available, pods, logs, note}); ``{available:false}`` when the
        request has no DMS job yet."""
        if not await db.get_scan_batch(batch_id):
            raise HTTPException(status_code=404, detail="batch_not_found")
        req = await _require_request(db, batch_id, request_id)
        job_id = req.get("dms_job_id")
        if not job_id:
            return {
                "available": False,
                "note": "아직 DMS 작업이 시작되지 않았습니다.",
                "pods": [],
                "logs": "",
            }
        actor = f"{settings.backup_actor_prefix}{_actor(user)}"
        try:
            return await dms.get_data_job_logs(job_id, tail=tail, actor=actor)
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    @router.post("/batches/{batch_id}/requests:cancel")
    async def cancel_requests_bulk(
        batch_id: str,
        payload: CancelIn,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        """Cancel the selected non-terminal requests (bulk); best-effort cancel any
        live DMS jobs. Works even while scanning (it's the in-flight escape hatch)."""
        if not await db.get_scan_batch(batch_id):
            raise HTTPException(status_code=404, detail="batch_not_found")
        if not payload.request_ids:
            raise HTTPException(status_code=422, detail="request_ids required")
        live = await db.cancel_scan_requests(batch_id, request_ids=payload.request_ids)
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
        """Reset fixable requests to 'registered' so the next ``:run`` re-runs them
        (retry)."""
        batch = await db.get_scan_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        if batch["status"] == "scanning":
            raise HTTPException(status_code=409, detail="cannot reset while scanning")
        p = payload or ResetIn()
        if not p.failed_only and not p.request_ids:
            raise HTTPException(
                status_code=422, detail="specify request_ids or failed_only"
            )
        n = await db.reset_scan_requests(
            batch_id, request_ids=p.request_ids, failed_only=p.failed_only
        )
        return {"id": batch_id, "reset": n}

    @router.post("/batches/{batch_id}:run")
    async def run(
        batch_id: str,
        payload: RunIn | None = None,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        batch = await db.get_scan_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        if batch["status"] not in ("draft", "done"):
            raise HTTPException(
                status_code=409, detail=f"cannot run from {batch['status']}"
            )
        # Self-heal any held requests left by a crashed selective run, so the
        # 'registered' count below reflects everything runnable.
        await db.release_held_scan(batch_id)
        counts = await db.scan_batch_state_counts(batch_id)
        if not counts.get("registered"):
            raise HTTPException(status_code=422, detail="no registered requests to scan")
        ids = payload.request_ids if payload else None
        scoped = False
        if ids:
            # Selective: park every registered request that isn't selected; only the
            # selected ones get scanned (the rest are restored afterwards).
            await db.hold_unselected_registered_scan(batch_id, ids)
            remaining = await db.scan_batch_state_counts(batch_id)
            if not remaining.get("registered"):
                await db.release_held_scan(batch_id)  # none selected were registered
                raise HTTPException(
                    status_code=422, detail="no registered requests among the selected"
                )
            scoped = True
        await db.set_scan_batch_status(batch_id, "scanning")
        return {"id": batch_id, "status": "scanning", "scoped": scoped}

    @router.post("/batches/{batch_id}:rescan")
    async def rescan(
        batch_id: str, db: Database = Depends(get_db)
    ) -> dict[str, Any]:
        """Re-run a finished batch: reset ALL terminal requests (succeeded/failed/
        cancelled) to 'registered', then scan them all again."""
        batch = await db.get_scan_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        if batch["status"] != "done":
            raise HTTPException(
                status_code=409, detail=f"cannot rescan a '{batch['status']}' batch"
            )
        reset = await db.reset_scan_requests(batch_id, all_terminal=True)
        await db.release_held_scan(batch_id)  # self-heal any stragglers
        counts = await db.scan_batch_state_counts(batch_id)
        if not counts.get("registered"):
            raise HTTPException(status_code=422, detail="no requests to rescan")
        await db.set_scan_batch_status(batch_id, "scanning")
        return {"id": batch_id, "status": "scanning", "reset": reset}

    @router.post("/batches/{batch_id}:cancel")
    async def cancel(
        batch_id: str,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        batch = await db.get_scan_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch_not_found")
        await db.set_scan_batch_status(batch_id, "cancelled")
        live = await db.cancel_scan_requests(batch_id)
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
