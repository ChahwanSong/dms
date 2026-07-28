"""End-user data-sync API (role: user).

The user 데이터 Sync menu: submit ONE-SHOT ``data.sync`` (copy) jobs, restricted vs
the operator tab. Rows live in the SAME ``sync_jobs`` table (origin='user') and are
driven by the SAME SyncOrchestrator — this router only owns creation policy + a
user-scoped view (a user sees ONLY their own jobs).

Restrictions (vs operator):
  - Endpoints must be BOTH filesystem-native OR BOTH PVC-backed (fs subtype 'pv').
    A filesystem↔PVC mix is refused — the user must ask an operator.
  - Fixed: priority=Mid, parallel nodes=policy default (node_count omitted),
    open_noatime=on, batch_files=100000, bufsize=4 MiB. No chmod/chown.
  - User-selectable: --delete, contents, memo.
  - Execution identity: FS↔FS runs as the USER's own identity (requester_id=<user>,
    so DMS resolves the user's POSIX id). PVC↔PVC runs as root FOR NOW (a future
    namespace-permission pre-check will gate it — see _check_pvc_sync_permission).

Job state (sync_jobs.state): registered -> preview_pending -> preview_ready ->
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
from ..security import ROLE_USER, require_role

# terminal job states — no longer driven by the orchestrator.
_TERMINAL = {"succeeded", "failed", "preview_failed", "cancelled"}
# states that still hold an in-flight DMS job/request the user can cancel.
_CANCELLABLE = {"preview_pending", "preview_ready", "running"}

# DMS filesystem backends (cephfs/gpfs/wekafs) — the only sync-capable storages;
# k8s CSI mappings are namespace-quota only. Mirrors the frontend isFsBackend.
_FS_BACKEND_TYPES = {"cephfs", "gpfs", "wekafs"}

# Fixed sync options for user jobs (policy). open_noatime always on; batch_files /
# bufsize pinned to the portal's conventional defaults (= mpifileutils tool defaults,
# within the DMS-accepted bounds batch_files∈[1,1e6], bufsize∈[4KiB,1GiB]).
_FIXED_BATCH_FILES = 100000
_FIXED_BUFSIZE = 4 * 1024 * 1024  # 4 MiB
_FIXED_PRIORITY = "Mid"  # 스케줄러 우선순위 = 중간 (fixed)


class UserSyncCreate(BaseModel):
    src_storage: str = Field(min_length=1)
    src_path: str
    dst_storage: str = Field(min_length=1)
    dst_path: str
    delete_enabled: bool = False
    contents: bool = False
    memo: str | None = None


def _actor(user: dict[str, Any]) -> str:
    """The portal-side OWNERSHIP key (``sync_jobs.created_by``, "my jobs" filters).

    Always the login id, matching user_scan.py / voc.py. It must never follow
    ``posix_username``: that would re-key ownership and orphan every row the user
    already created — their own jobs would disappear from the list and fail the
    created_by check.
    """
    return str(user.get("username") or ROLE_USER)


def _dms_requester(user: dict[str, Any]) -> str:
    """The DMS EXECUTION identity — DMS resolves this to a POSIX uid via LDAP.

    Separate from ``_actor`` because the portal login id is a company-mail local
    part, which need not equal the company POSIX account name (and while the test
    domain is gmail.com it may not exist in LDAP at all). Falls back to the login id,
    so this is a no-op until ``user_accounts.posix_username`` is set.
    """
    return str(user.get("posix_username") or user.get("username") or ROLE_USER)


def _clean_rel(path: str) -> str:
    """Storage-relative path, CANONICALIZED to match DMS's resource_key: no leading/
    trailing slash, collapse empty/'.' segments, reject '..' and NUL. Canonical form
    matters so a scan's resource_key (built from DMS's canonical path) matches."""
    if "\x00" in (path or ""):
        raise ValueError(f"invalid path: {path!r}")
    parts = [seg for seg in (path or "").strip().split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        raise ValueError(f"invalid path: {path!r}")
    return "/".join(parts)


def _backend_type(m: dict[str, Any]) -> str | None:
    bt = (m.get("backend_template") or {}).get("backend_type")
    return bt if isinstance(bt, str) and bt else None


def _is_fs_backend(m: dict[str, Any]) -> bool:
    return _backend_type(m) in _FS_BACKEND_TYPES


def _is_pv(m: dict[str, Any]) -> bool:
    """Portal-only fs subtype: a filesystem that BACKS Kubernetes PV/PVC provisioning.
    Absent key = 'fs-native'. Only meaningful for fs backends."""
    return (m.get("backend_template") or {}).get("filesystem_subtype") == "pv"


def _managed_root(m: dict[str, Any]) -> str | None:
    v = (m.get("backend_template") or {}).get("managed_root")
    return v.strip() if isinstance(v, str) and v.strip() else None


def _check_pvc_sync_permission(
    user: dict[str, Any],
    src_mapping: dict[str, Any],
    src_path: str,
    dst_mapping: dict[str, Any],
    dst_path: str,
) -> None:
    """PLACEHOLDER for the future PVC-namespace permission pre-check.

    PVC↔PVC user syncs currently run as ROOT. Before that root execution is allowed
    we will verify the logged-in user actually has rights on the namespace each PVC
    belongs to (a pre-check on source & destination path/namespace permissions). Until
    that check is built this is a deliberate no-op that always permits — the
    restriction is enforced operationally, not here. Raise HTTPException(403, ...)
    from here once the check exists. TODO(user-pvc-perms).
    """
    return None


def user_sync_router(settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/user",
        tags=["user-sync"],
        dependencies=[Depends(require_role(ROLE_USER))],
    )

    def dms_actor(user: dict[str, Any]) -> str:
        # Same shape the SyncOrchestrator derives from created_by, so the actor that
        # reads/cancels a job matches the one that created it in DMS.
        return f"{settings.backup_actor_prefix}{_actor(user)}"

    async def _owned_or_404(
        db: Database, job_id: int, user: dict[str, Any]
    ) -> dict[str, Any]:
        row = await db.get_sync_job(job_id)
        # scope to the user's OWN self-service jobs; anything else is 404 (never leak
        # another user's / an operator's job).
        if not row or row.get("origin") != "user" or row.get("created_by") != _actor(user):
            raise HTTPException(status_code=404, detail="sync_job_not_found")
        return row

    @router.get("/storages")
    async def list_storages(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> list[dict[str, Any]]:
        """Filesystem-backend storages usable as a sync/scan endpoint. Non-secret
        fields only (name, backend_type, fs subtype, managed_root)."""
        try:
            mappings = await dms.list_storage_mappings(actor=dms_actor(user))
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)
        out = [
            {
                "storage_name": m.get("storage_name"),
                "backend_type": _backend_type(m),
                "filesystem_subtype": "pv" if _is_pv(m) else "fs-native",
                "managed_root": _managed_root(m),
            }
            for m in (mappings or [])
            if _is_fs_backend(m)
        ]
        out.sort(key=lambda s: str(s["storage_name"]))
        return out

    @router.post("/sync-jobs", status_code=201)
    async def create_job(
        payload: UserSyncCreate,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        try:
            src_path = _clean_rel(payload.src_path)
            dst_path = _clean_rel(payload.dst_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        src_storage = payload.src_storage.strip()
        dst_storage = payload.dst_storage.strip()
        if not src_storage or not dst_storage:
            raise HTTPException(status_code=422, detail="storage name required")
        if src_storage == dst_storage and src_path == dst_path:
            raise HTTPException(status_code=422, detail="destination must differ from source")

        # resolve both endpoints' storage mappings (authoritative fs/subtype check;
        # the frontend also filters, but never trust the client for the FS↔PVC gate).
        try:
            mappings = await dms.list_storage_mappings(actor=dms_actor(user))
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)
        by_name = {m.get("storage_name"): m for m in (mappings or [])}
        src_m = by_name.get(src_storage)
        dst_m = by_name.get(dst_storage)
        for name, m in (("출발", src_m), ("대상", dst_m)):
            if m is None:
                raise HTTPException(status_code=404, detail=f"{name} 스토리지를 찾을 수 없습니다")
            if not _is_fs_backend(m):
                raise HTTPException(
                    status_code=422,
                    detail=f"{name} 스토리지는 파일시스템 백엔드가 아닙니다 (sync 불가)",
                )

        src_pv = _is_pv(src_m)
        dst_pv = _is_pv(dst_m)
        if src_pv != dst_pv:
            # filesystem ↔ PVC mix: not permitted for self-service. The frontend keys
            # off this detail string to show the "운영자에게 요청" guidance.
            raise HTTPException(
                status_code=422,
                detail=(
                    "파일시스템과 K8S PVC 간 sync는 직접 실행할 수 없습니다. "
                    "운영자에게 요청하세요."
                ),
            )

        if src_pv:
            # PVC↔PVC runs as ROOT (uid 0) for now — there is no per-user identity on
            # PVC-backed storage yet. Until the namespace-permission pre-check exists
            # (_check_pvc_sync_permission), REFUSE the destructive --delete: a root
            # mirror-delete with no ownership check could wipe another tenant's PVC
            # data. Plain copy is still allowed. FS↔FS keeps --delete (POSIX-limited).
            if payload.delete_enabled:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "PVC↔PVC sync은 현재 root로 실행되어 --delete(미러 삭제)를 "
                        "사용할 수 없습니다. 삭제가 필요하면 운영자에게 요청하세요."
                    ),
                )
            _check_pvc_sync_permission(user, src_m, src_path, dst_m, dst_path)
            requester_id = settings.backup_requester  # "root"
        else:
            # FS↔FS: runs as the user's own POSIX identity (NOT the ownership key —
            # see _dms_requester).
            requester_id = _dms_requester(user)

        # fixed options (server-built; client-supplied options are ignored on purpose).
        options: dict[str, Any] = {
            "open_noatime": True,
            "batch_files": _FIXED_BATCH_FILES,
            "bufsize": _FIXED_BUFSIZE,
        }
        if payload.contents:
            options["contents"] = True

        row = await db.create_sync_job(
            src_storage=src_storage,
            src_path=src_path,
            dst_storage=dst_storage,
            dst_path=dst_path,
            requester_id=requester_id,
            owner_username=None,
            options=options,
            delete_enabled=payload.delete_enabled,
            priority=_FIXED_PRIORITY,
            node_count=None,  # policy default (병렬 노드 수 고정)
            memo=(payload.memo or None),
            created_by=_actor(user),
            origin="user",
        )
        return row

    @router.get("/sync-jobs")
    async def list_jobs(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        me = _actor(user)
        items = await db.list_sync_jobs(
            limit=limit, offset=offset, origin="user", created_by=me
        )
        total = (
            await db.count_sync_jobs(origin="user", created_by=me)
            if offset == 0
            else None
        )
        return {"items": items, "total": total}

    @router.get("/sync-jobs/{job_id}")
    async def get_job(
        job_id: int,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        return await _owned_or_404(db, job_id, user)

    @router.post("/sync-jobs/{job_id}:approve")
    async def approve_job(
        job_id: int,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        row = await _owned_or_404(db, job_id, user)
        if row["state"] != "preview_ready":
            raise HTTPException(
                status_code=409,
                detail=f"cannot approve a '{row['state']}' job (needs preview_ready)",
            )
        await db.approve_sync_job(job_id)
        return {"id": job_id, "approved": True}

    @router.post("/sync-jobs/{job_id}:cancel")
    async def cancel_job(
        job_id: int,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        row = await _owned_or_404(db, job_id, user)
        if row["state"] in _TERMINAL:
            raise HTTPException(status_code=409, detail=f"already {row['state']}")
        if row.get("dms_job_id"):
            try:
                await dms.cancel_job(row["dms_job_id"], actor=dms_actor(user))
            except DmsApiError:
                pass
        await db.update_sync_job(job_id, state="cancelled")
        return {"id": job_id, "state": "cancelled"}

    @router.delete("/sync-jobs/{job_id}")
    async def delete_job(
        job_id: int,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        row = await _owned_or_404(db, job_id, user)
        if row.get("dms_job_id"):
            try:
                if row["state"] in _CANCELLABLE:
                    await dms.cancel_job(row["dms_job_id"], actor=dms_actor(user))
                else:
                    await dms.delete_data_job(row["dms_job_id"], actor=dms_actor(user))
            except DmsApiError:
                pass
        await db.delete_sync_job(job_id)
        return {"deleted": job_id}

    @router.get("/sync-jobs/{job_id}/job")
    async def job_detail(
        job_id: int,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        row = await _owned_or_404(db, job_id, user)
        if not row.get("dms_job_id"):
            return {"available": False, "state": row["state"],
                    "note": "아직 DMS 작업이 시작되지 않았습니다."}
        try:
            return await dms.get_sync_job(row["dms_job_id"], actor=dms_actor(user))
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    @router.get("/sync-jobs/{job_id}/logs")
    async def job_logs(
        job_id: int,
        tail: int = Query(default=400, ge=1, le=5000),
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        row = await _owned_or_404(db, job_id, user)
        if not row.get("dms_job_id"):
            return {"job_id": None, "available": False, "pods": [], "logs": "",
                    "note": "아직 실행되지 않았습니다."}
        try:
            return await dms.get_data_job_logs(row["dms_job_id"], tail=tail, actor=dms_actor(user))
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return router
