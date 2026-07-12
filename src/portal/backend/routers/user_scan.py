"""End-user data-scan API (role: user).

The user 데이터 스캔 menu does NOT run scans. A user REGISTERS single (storage, path)
items; the BFF live-pulls the OPERATOR's latest completed DMS ``data.scan`` result for
each (matched by the scan's stable ``resource_key`` = ``data.scan:{storage}:{path}``,
which carries no fingerprint). So when an operator runs a batch scan, the matching
user items reflect the fresh result automatically on next read. If no completed scan
exists, the item is returned with ``has_result=false`` and the UI tells the user to
ask an operator.

Only the registration list + memo are persisted (portal ``user_scan_items``); scan
RESULTS are never stored here.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import Settings
from ..db import Database
from ..deps import get_db, get_dms_client
from ..dms_client import DmsApiError, DmsClient
from ..security import ROLE_USER, require_role

_FS_BACKEND_TYPES = {"cephfs", "gpfs", "wekafs"}


class UserScanItemCreate(BaseModel):
    storage: str = Field(min_length=1)
    path: str
    memo: str | None = None


class UserScanItemUpdate(BaseModel):
    memo: str | None = None


def _actor(user: dict[str, Any]) -> str:
    return str(user.get("username") or ROLE_USER)


def _clean_rel(path: str) -> str:
    """Storage-relative path, CANONICALIZED to match DMS's scan resource_key
    (data.scan:{storage}:{path}): collapse empty/'.' segments, reject '..'/NUL — so
    a registered item matches the operator's scan regardless of '//' or './' noise."""
    if "\x00" in (path or ""):
        raise ValueError(f"invalid path: {path!r}")
    parts = [seg for seg in (path or "").strip().split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        raise ValueError(f"invalid path: {path!r}")
    return "/".join(parts)


def _is_fs_backend(m: dict[str, Any]) -> bool:
    bt = (m.get("backend_template") or {}).get("backend_type")
    return isinstance(bt, str) and bt in _FS_BACKEND_TYPES


def _scan_result(dms_job: dict[str, Any]) -> dict[str, Any]:
    """A succeeded scan job's summary in the portal's compact shape (mirrors the
    operator ScanOrchestrator._scan_result)."""
    rs = dms_job.get("result_summary") or {}
    s = rs.get("summary") or {}
    return {
        "file_count": s.get("file_count"),
        "directory_count": s.get("directory_count"),
        "total_bytes": s.get("total_bytes"),
        "error_count": s.get("error_count"),
        "scan_root": s.get("scan_root"),
        "tool": s.get("selected_tool") or rs.get("tool") or dms_job.get("selected_tool"),
        "atime_histogram": s.get("atime_histogram"),
    }


def _matches(job: dict[str, Any], storage: str, path: str, resource_key: str) -> bool:
    if job.get("resource_key") == resource_key:
        return True
    nt = job.get("normalized_target")
    if isinstance(nt, dict):
        return nt.get("storage_name") == storage and nt.get("path") == path
    return False


# DMS scan (read-only: Pending -> Running -> Succeeded|Failed) terminal-failure states.
_SCAN_FAIL = {"Failed", "PreflightFailed", "PreviewExpired", "Cancelled", "TimedOut"}


def _reason(dms_job: dict[str, Any]) -> str:
    pf = dms_job.get("preflight_result") or {}
    return pf.get("reason") or pf.get("error") or dms_job.get("state") or "unknown"


def _status_of(state: str | None) -> str:
    if state == "Succeeded":
        return "succeeded"
    if state in _SCAN_FAIL:
        return "failed"
    return "running"  # Pending / Running / etc.


def _enrichment(latest: dict[str, Any], latest_ok: dict[str, Any] | None) -> dict[str, Any]:
    """Combine the LATEST scan attempt (status/error) with the latest SUCCEEDED scan
    (result), so a running/failed re-scan does NOT hide a prior good result: the item
    still shows the last good numbers while the 상태 badge reflects the newest attempt.
    `scanned_at`/`dms_job_id` track the shown result's provenance (the succeeded scan)."""
    status = _status_of(latest.get("state"))
    src = latest_ok or latest  # provenance of the numbers we display
    return {
        "status": status,
        "state": latest.get("state"),
        "result": _scan_result(latest_ok) if latest_ok else None,
        "dms_job_id": src.get("job_id"),
        "scanned_at": src.get("updated_at") or src.get("finished_at"),
        "error": _reason(latest) if status == "failed" else None,
    }


def user_scan_router(settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/user",
        tags=["user-scan"],
        dependencies=[Depends(require_role(ROLE_USER))],
    )

    def dms_actor(user: dict[str, Any]) -> str:
        return f"{settings.backup_actor_prefix}{_actor(user)}"

    async def _pull_one(
        dms: DmsClient, actor: str, storage: str, path: str
    ) -> dict[str, Any] | None:
        """The item's scan state for (storage, path): the LATEST attempt (any state) for
        the 상태 badge + the latest SUCCEEDED scan for the result. None if never scanned."""
        resource_key = f"data.scan:{storage}:{path}"
        try:
            jobs = await dms.list_data_jobs(
                actor=actor,
                operation="data.scan",
                storage_name=storage,
                limit=500,
            )
        except DmsApiError:
            return None
        # newest-first (updated_at DESC): first match = latest attempt; first Succeeded
        # match = latest good result.
        latest: dict[str, Any] | None = None
        latest_ok: dict[str, Any] | None = None
        for j in jobs or []:
            if not _matches(j, storage, path, resource_key):
                continue
            if latest is None:
                latest = j
            if latest_ok is None and j.get("state") == "Succeeded":
                latest_ok = j
            if latest is not None and latest_ok is not None:
                break
        return _enrichment(latest, latest_ok) if latest else None

    def _enrich(item: dict[str, Any], pulled: dict[str, Any] | None) -> dict[str, Any]:
        out = dict(item)
        out["status"] = pulled["status"] if pulled else "none"
        out["state"] = pulled["state"] if pulled else None
        out["result"] = pulled["result"] if pulled else None
        out["has_result"] = out["result"] is not None
        out["dms_job_id"] = pulled["dms_job_id"] if pulled else None
        out["scanned_at"] = pulled["scanned_at"] if pulled else None
        out["error"] = pulled["error"] if pulled else None
        return out

    @router.post("/scan-items", status_code=201)
    async def create_item(
        payload: UserScanItemCreate,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        try:
            path = _clean_rel(payload.path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        storage = payload.storage.strip()
        if not storage:
            raise HTTPException(status_code=422, detail="storage name required")
        # validate the storage exists and is a filesystem backend (scan targets).
        try:
            mappings = await dms.list_storage_mappings(actor=dms_actor(user))
        except DmsApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)
        m = next((x for x in (mappings or []) if x.get("storage_name") == storage), None)
        if m is None:
            raise HTTPException(status_code=404, detail="스토리지를 찾을 수 없습니다")
        if not _is_fs_backend(m):
            raise HTTPException(
                status_code=422, detail="파일시스템 백엔드 스토리지만 스캔 대상이 됩니다"
            )
        me = _actor(user)
        row = await db.create_user_scan_item(
            username=me, storage=storage, path=path, memo=(payload.memo or None)
        )
        if row is None:
            raise HTTPException(status_code=409, detail="이미 등록된 항목입니다")
        pulled = await _pull_one(dms, dms_actor(user), storage, path)
        return _enrich(row, pulled)

    @router.get("/scan-items")
    async def list_items(
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        me = _actor(user)
        items = await db.list_user_scan_items(username=me)
        # group by storage → ONE DMS list call per storage, then match each item's path.
        by_storage: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for it in items:
            by_storage[it["storage"]].append(it)
        pulled_by_id: dict[int, dict[str, Any]] = {}
        actor = dms_actor(user)
        for storage, its in by_storage.items():
            try:
                jobs = await dms.list_data_jobs(
                    actor=actor,
                    operation="data.scan",
                    storage_name=storage,
                    limit=500,
                )
            except DmsApiError:
                jobs = []
            # newest-first: FIRST job per resource_key = latest attempt; first Succeeded
            # per key = latest good result (so a re-scan doesn't hide the last result).
            newest: dict[str, dict[str, Any]] = {}
            newest_ok: dict[str, dict[str, Any]] = {}
            for j in jobs or []:
                k = j.get("resource_key")
                if not isinstance(k, str):
                    continue
                if k not in newest:
                    newest[k] = j
                if j.get("state") == "Succeeded" and k not in newest_ok:
                    newest_ok[k] = j
            for it in its:
                key = f"data.scan:{storage}:{it['path']}"
                latest = newest.get(key)
                if latest is None:
                    latest = next(
                        (x for x in (jobs or []) if _matches(x, storage, it["path"], key)),
                        None,
                    )
                latest_ok = newest_ok.get(key)
                if latest_ok is None:
                    latest_ok = next(
                        (
                            x
                            for x in (jobs or [])
                            if x.get("state") == "Succeeded"
                            and _matches(x, storage, it["path"], key)
                        ),
                        None,
                    )
                pulled_by_id[it["id"]] = _enrichment(latest, latest_ok) if latest else None
        # preserve the original newest-first item order.
        enriched = [_enrich(it, pulled_by_id.get(it["id"])) for it in items]
        return {"items": enriched}

    @router.get("/scan-items/{item_id}")
    async def get_item(
        item_id: int,
        db: Database = Depends(get_db),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        me = _actor(user)
        row = await db.get_user_scan_item(item_id=item_id, username=me)
        if not row:
            raise HTTPException(status_code=404, detail="scan_item_not_found")
        pulled = await _pull_one(dms, dms_actor(user), row["storage"], row["path"])
        return _enrich(row, pulled)

    @router.patch("/scan-items/{item_id}")
    async def update_item(
        item_id: int,
        payload: UserScanItemUpdate,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        me = _actor(user)
        row = await db.update_user_scan_item_memo(
            item_id=item_id, username=me, memo=(payload.memo or None)
        )
        if not row:
            raise HTTPException(status_code=404, detail="scan_item_not_found")
        return row

    @router.delete("/scan-items/{item_id}")
    async def delete_item(
        item_id: int,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        me = _actor(user)
        row = await db.delete_user_scan_item(item_id=item_id, username=me)
        if not row:
            raise HTTPException(status_code=404, detail="scan_item_not_found")
        return {"deleted": item_id}

    return router
