"""Operator data-rm API (role: operator).

The 데이터 삭제 tab: submit ONE-SHOT ``data.rm`` (delete) jobs and track them. Like the
데이터 Sync tab there is NO batch and NO re-run — each job is the top-level unit — but
the job targets a SINGLE path (drm removes one directory tree) and there is no delete
flag (rm IS the delete). Mutating, so it goes through the DMS preview(dry-run) ->
실행(confirm) -> execute flow, driven by the RmOrchestrator loop. Persistence is the
portal DB; the actual delete runs on DMS (this BFF never touches data directly).

Job state (rm_jobs.state): registered -> preview_pending -> preview_ready ->
(approve) running -> succeeded|failed. preview_failed / cancelled are terminal too.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import Settings
from ..db import Database
from ..deps import get_db, get_dms_client
from ..dms_client import DmsApiError, DmsClient
from ..security import ROLE_OPERATOR, require_role

_PRIORITIES = {"High", "Mid", "Low"}
# terminal job states — no longer driven by the orchestrator.
_TERMINAL = {"succeeded", "failed", "preview_failed", "cancelled"}
# states that hold an in-flight DMS job/request the operator can still cancel.
_CANCELLABLE = {"preview_pending", "preview_ready", "running"}


class RmJobCreate(BaseModel):
    target_storage: str = Field(min_length=1)
    target_path: str
    options: dict[str, Any] = Field(default_factory=dict)
    priority: str = "Low"
    node_count: int | None = Field(default=None, ge=1)  # None = use DMS policy default
    memo: str | None = None


def _actor(user: dict[str, Any]) -> str:
    return str(user.get("username") or ROLE_OPERATOR)


def _clean_rel(path: str) -> str:
    """Storage-relative path: no leading/trailing slash, no traversal, no NUL."""
    p = (path or "").strip().strip("/")
    if not p or ".." in p.split("/") or "\x00" in p:
        raise ValueError(f"invalid path: {path!r}")
    return p


def rm_router(settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/operator/rm-jobs",
        tags=["operator-rm"],
        dependencies=[Depends(require_role(ROLE_OPERATOR))],
    )
    requester = settings.backup_requester

    @router.post("", status_code=201)
    async def create_job(
        payload: RmJobCreate,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        if payload.priority not in _PRIORITIES:
            raise HTTPException(status_code=422, detail="priority must be High, Mid, or Low")
        try:
            target_path = _clean_rel(payload.target_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        target_storage = payload.target_storage.strip()
        if not target_storage:
            raise HTTPException(status_code=422, detail="storage name required")
        # options are stored as-is; the DMS preview validates them authoritatively.
        row = await db.create_rm_job(
            target_storage=target_storage,
            target_path=target_path,
            requester_id=requester,
            owner_username=None,
            options=dict(payload.options or {}),
            priority=payload.priority,
            node_count=payload.node_count,
            memo=(payload.memo or None),
            created_by=_actor(user),
        )
        return row

    @router.get("/node-policy")
    async def node_policy(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        """DMS worker-node policy default for the drm tool, so the form can show what
        "자동" resolves to. null when no policy is configured."""
        actor = f"{settings.backup_actor_prefix}{_actor(user)}"
        try:
            policies = await dms.list_data_management_policies(actor=actor)
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)
        by_op = {p.get("operation"): p for p in (policies or [])}
        p = by_op.get("drm")
        drm = (
            {
                "default_worker_nodes": p.get("default_worker_nodes"),
                "max_worker_nodes": p.get("max_worker_nodes"),
            }
            if p
            else None
        )
        return {"drm": drm}

    @router.get("")
    async def list_jobs(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        items = await db.list_rm_jobs(limit=limit, offset=offset)
        # total only on the first page (배지·완결성); later pages skip the COUNT.
        total = await db.count_rm_jobs() if offset == 0 else None
        return {"items": items, "total": total}

    @router.get("/{job_id}")
    async def get_job(
        job_id: int,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        row = await db.get_rm_job(job_id)
        if not row:
            raise HTTPException(status_code=404, detail="rm_job_not_found")
        return row

    @router.post("/{job_id}:approve")
    async def approve_job(
        job_id: int,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        row = await db.get_rm_job(job_id)
        if not row:
            raise HTTPException(status_code=404, detail="rm_job_not_found")
        if row["state"] != "preview_ready":
            raise HTTPException(
                status_code=409,
                detail=f"cannot approve a '{row['state']}' job (needs preview_ready)",
            )
        await db.approve_rm_job(job_id)
        return {"id": job_id, "approved": True}

    @router.post("/{job_id}:cancel")
    async def cancel_job(
        job_id: int,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        row = await db.get_rm_job(job_id)
        if not row:
            raise HTTPException(status_code=404, detail="rm_job_not_found")
        if row["state"] in _TERMINAL:
            raise HTTPException(status_code=409, detail=f"already {row['state']}")
        # best-effort cancel of the in-flight DMS job; the portal record is cancelled
        # regardless so a stuck DMS job can't pin the row open.
        if row.get("dms_job_id"):
            actor = f"{settings.backup_actor_prefix}{row.get('created_by') or _actor(user)}"
            try:
                await dms.cancel_job(row["dms_job_id"], actor=actor)
            except DmsApiError:
                pass
        await db.update_rm_job(job_id, state="cancelled")
        return {"id": job_id, "state": "cancelled"}

    @router.delete("/{job_id}")
    async def delete_job(
        job_id: int,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        row = await db.get_rm_job(job_id)
        if not row:
            raise HTTPException(status_code=404, detail="rm_job_not_found")
        # best-effort clean-up of the DMS side: cancel if still in flight, else delete
        # the terminal data-job record. Portal row removal proceeds regardless.
        if row.get("dms_job_id"):
            actor = f"{settings.backup_actor_prefix}{row.get('created_by') or _actor(user)}"
            try:
                if row["state"] in _CANCELLABLE:
                    await dms.cancel_job(row["dms_job_id"], actor=actor)
                else:
                    await dms.delete_data_job(row["dms_job_id"], actor=actor)
            except DmsApiError:
                pass
        await db.delete_rm_job(job_id)
        return {"deleted": job_id}

    @router.get("/{job_id}/job")
    async def job_detail(
        job_id: int,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        """Live DMS rm-job detail (read-only), for the 상세/로그 modal. {available:
        false} when the job hasn't been submitted/reconciled yet."""
        row = await db.get_rm_job(job_id)
        if not row:
            raise HTTPException(status_code=404, detail="rm_job_not_found")
        if not row.get("dms_job_id"):
            return {"available": False, "state": row["state"],
                    "note": "아직 DMS 작업이 시작되지 않았습니다."}
        actor = f"{settings.backup_actor_prefix}{row.get('created_by') or _actor(user)}"
        try:
            return await dms.get_rm_job(row["dms_job_id"], actor=actor)
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    @router.get("/{job_id}/logs")
    async def job_logs(
        job_id: int,
        tail: int = Query(default=400, ge=1, le=5000),
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        row = await db.get_rm_job(job_id)
        if not row:
            raise HTTPException(status_code=404, detail="rm_job_not_found")
        if not row.get("dms_job_id"):
            return {"job_id": None, "available": False, "pods": [], "logs": "", "note": "아직 실행되지 않았습니다."}
        actor = f"{settings.backup_actor_prefix}{row.get('created_by') or _actor(user)}"
        try:
            return await dms.get_data_job_logs(row["dms_job_id"], tail=tail, actor=actor)
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return router
