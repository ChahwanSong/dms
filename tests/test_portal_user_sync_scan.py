"""End-user 데이터 Sync / 데이터 스캔 self-service router tests.

Exercises the two user routers over HTTP (TestClient) with in-memory DB + DMS fakes:
  - Sync creation policy: FS↔FS runs as the USER identity, PVC↔PVC as root, FS↔PVC is
    refused (ask an operator); fixed options (Mid priority, policy-default nodes,
    open_noatime, batch_files/bufsize) are server-forced; --delete/contents honored.
  - Ownership scoping: a user only sees / acts on their OWN origin='user' jobs and
    their OWN scan items (everything else is 404).
  - Scan is READ-ONLY: items live-pull the latest Succeeded operator scan by
    (storage, path); no match → has_result=false.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from portal.backend import deps, security
from portal.backend.config import Settings
from portal.backend.routers.user_scan import user_scan_router
from portal.backend.routers.user_sync import user_sync_router

USER = "/api/user"


# --- fakes ------------------------------------------------------------------


class FakeDB:
    def __init__(self) -> None:
        self.sync: dict[int, dict[str, Any]] = {}
        self.scan: dict[int, dict[str, Any]] = {}
        self._sn = 1
        self._in = 1

    # --- sync jobs ---
    async def create_sync_job(self, **kw: Any) -> dict[str, Any]:
        jid = self._sn
        self._sn += 1
        row = {
            "id": jid, "state": "registered", "approved": False,
            "dms_request_id": None, "dms_job_id": None, "fingerprint": None,
            "preview": None, "result": None, "error": None, **kw,
        }
        self.sync[jid] = row
        return dict(row)

    async def list_sync_jobs(self, *, limit=200, offset=0, origin=None, created_by=None):
        rows = [
            r for r in self.sync.values()
            if (origin is None or r.get("origin") == origin)
            and (created_by is None or r.get("created_by") == created_by)
        ]
        rows.sort(key=lambda r: r["id"], reverse=True)
        return [dict(r) for r in rows[offset:offset + limit]]

    async def count_sync_jobs(self, *, origin=None, created_by=None) -> int:
        return len(await self.list_sync_jobs(limit=10**9, origin=origin, created_by=created_by))

    async def get_sync_job(self, job_id: int):
        r = self.sync.get(job_id)
        return dict(r) if r else None

    async def approve_sync_job(self, job_id: int) -> bool:
        r = self.sync.get(job_id)
        if r and r["state"] == "preview_ready":
            r["approved"] = True
            return True
        return False

    async def update_sync_job(self, job_id: int, **fields: Any) -> None:
        self.sync[job_id].update(fields)

    async def delete_sync_job(self, job_id: int):
        return self.sync.pop(job_id, None)

    # --- scan items ---
    async def create_user_scan_item(self, *, username, storage, path, memo):
        for r in self.scan.values():
            if (r["username"], r["storage"], r["path"]) == (username, storage, path):
                return None
        iid = self._in
        self._in += 1
        row = {
            "id": iid, "username": username, "storage": storage, "path": path,
            "memo": memo, "created_at": "t", "updated_at": "t",
        }
        self.scan[iid] = row
        return dict(row)

    async def list_user_scan_items(self, *, username):
        rows = [r for r in self.scan.values() if r["username"] == username]
        rows.sort(key=lambda r: r["id"], reverse=True)
        return [dict(r) for r in rows]

    async def get_user_scan_item(self, *, item_id, username):
        r = self.scan.get(item_id)
        return dict(r) if r and r["username"] == username else None

    async def update_user_scan_item_memo(self, *, item_id, username, memo):
        r = self.scan.get(item_id)
        if not r or r["username"] != username:
            return None
        r["memo"] = memo
        return dict(r)

    async def delete_user_scan_item(self, *, item_id, username):
        r = self.scan.get(item_id)
        if not r or r["username"] != username:
            return None
        return self.scan.pop(item_id)


def _mapping(name: str, backend_type: str, subtype: str | None = None) -> dict[str, Any]:
    tpl: dict[str, Any] = {"backend_type": backend_type, "managed_root": f"/mnt/{name}"}
    if subtype:
        tpl["filesystem_subtype"] = subtype
    return {"storage_name": name, "backend_template": tpl}


class FakeDms:
    def __init__(self, mappings=None, scan_jobs=None) -> None:
        self.mappings = mappings if mappings is not None else [
            _mapping("fs-a", "cephfs"),
            _mapping("fs-b", "gpfs"),
            _mapping("pv-a", "cephfs", "pv"),
            _mapping("pv-b", "gpfs", "pv"),
            _mapping("csi-x", "ceph-csi"),
        ]
        self.scan_jobs = scan_jobs or []
        self.cancelled: list[str] = []
        self.deleted: list[str] = []

    async def list_storage_mappings(self, *, actor=None, **_):
        return [dict(m) for m in self.mappings]

    async def list_data_jobs(self, *, actor, operation=None, storage_name=None,
                             state=None, limit=500, offset=0):
        out = []
        for j in self.scan_jobs:
            if operation and j.get("operation") != operation:
                continue
            if storage_name and j.get("storage_name") != storage_name:
                continue
            if state and j.get("state") != state:
                continue
            out.append(dict(j))
        return out

    async def cancel_job(self, job_id, *, actor):
        self.cancelled.append(job_id)
        return {"job_id": job_id}

    async def delete_data_job(self, job_id, *, actor):
        self.deleted.append(job_id)
        return {"job_id": job_id}

    async def get_sync_job(self, job_id, *, actor):
        return {"job_id": job_id, "state": "Running"}

    async def get_data_job_logs(self, job_id, *, tail=400, actor):
        return {"job_id": job_id, "available": True, "pods": [], "logs": "..."}


def make_client(db: FakeDB, dms: FakeDms, username: str = "alice") -> TestClient:
    app = FastAPI()
    s = Settings(session_secret="t" * 32, allow_insecure_defaults=True)
    app.include_router(user_sync_router(s))
    app.include_router(user_scan_router(s))
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_dms_client] = lambda: dms
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": username, "role": "user", "method": "ad",
    }
    return TestClient(app)


@pytest.fixture
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture
def dms() -> FakeDms:
    return FakeDms()


@pytest.fixture
def client(db, dms) -> TestClient:
    return make_client(db, dms)


def _sync(**kw):
    base = {"src_storage": "fs-a", "src_path": "src", "dst_storage": "fs-b", "dst_path": "dst"}
    base.update(kw)
    return base


# --- storages ---------------------------------------------------------------


def test_storages_only_fs_with_subtype(client):
    r = client.get(f"{USER}/storages")
    assert r.status_code == 200, r.text
    names = {s["storage_name"]: s for s in r.json()}
    assert set(names) == {"fs-a", "fs-b", "pv-a", "pv-b"}  # csi-x excluded
    assert names["fs-a"]["filesystem_subtype"] == "fs-native"
    assert names["pv-a"]["filesystem_subtype"] == "pv"


# --- sync creation policy ---------------------------------------------------


def test_fs_to_fs_runs_as_user_with_fixed_options(client, db):
    r = client.post(f"{USER}/sync-jobs", json=_sync(delete_enabled=True, contents=True))
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["origin"] == "user"
    assert row["created_by"] == "alice"
    assert row["requester_id"] == "alice"          # FS↔FS → user identity
    assert row["priority"] == "Mid"                # 중간 고정
    assert row["node_count"] is None               # policy default
    assert row["delete_enabled"] is True
    opt = row["options"]
    assert opt["open_noatime"] is True
    assert opt["batch_files"] == 100000
    assert opt["bufsize"] == 4 * 1024 * 1024
    assert opt["contents"] is True
    assert "chmod" not in opt and "chown" not in opt
    assert "delete" not in opt  # delete rides the flag, folded by the orchestrator


def test_fs_to_fs_without_contents_omits_it(client):
    r = client.post(f"{USER}/sync-jobs", json=_sync(contents=False))
    assert r.status_code == 201
    assert "contents" not in r.json()["options"]


def test_pvc_to_pvc_runs_as_root(client):
    r = client.post(f"{USER}/sync-jobs", json=_sync(src_storage="pv-a", dst_storage="pv-b"))
    assert r.status_code == 201, r.text
    assert r.json()["requester_id"] == "root"      # PVC↔PVC → root (for now)


def test_pvc_to_pvc_delete_refused(client):
    # root mirror-delete with no per-user permission check is blocked until the
    # namespace pre-check exists; plain copy (above) is allowed.
    r = client.post(f"{USER}/sync-jobs",
                    json=_sync(src_storage="pv-a", dst_storage="pv-b", delete_enabled=True))
    assert r.status_code == 422
    assert "delete" in str(r.json()["detail"]).lower()


def test_fs_to_fs_delete_allowed(client):
    # FS↔FS runs as the user's own POSIX identity, so --delete is fine.
    r = client.post(f"{USER}/sync-jobs", json=_sync(delete_enabled=True))
    assert r.status_code == 201
    assert r.json()["delete_enabled"] is True


def test_path_canonicalized_for_scan_match(client):
    # '//' and './' noise collapses so the resource_key matches the operator scan.
    r = client.post(f"{USER}/sync-jobs", json=_sync(src_path="a//b/./c", dst_path="x/y"))
    assert r.status_code == 201
    assert r.json()["src_path"] == "a/b/c"


def test_fs_to_pvc_refused(client):
    r = client.post(f"{USER}/sync-jobs", json=_sync(src_storage="fs-a", dst_storage="pv-a"))
    assert r.status_code == 422
    assert "운영자" in str(r.json()["detail"])       # ask an operator


def test_pvc_to_fs_refused(client):
    r = client.post(f"{USER}/sync-jobs", json=_sync(src_storage="pv-b", dst_storage="fs-b"))
    assert r.status_code == 422


def test_non_fs_storage_refused(client):
    r = client.post(f"{USER}/sync-jobs", json=_sync(src_storage="csi-x", dst_storage="fs-b"))
    assert r.status_code == 422


def test_unknown_storage_404(client):
    r = client.post(f"{USER}/sync-jobs", json=_sync(src_storage="ghost"))
    assert r.status_code == 404


def test_same_src_dst_rejected(client):
    r = client.post(f"{USER}/sync-jobs", json=_sync(src_storage="fs-a", dst_storage="fs-a",
                                                    src_path="p", dst_path="p"))
    assert r.status_code == 422


def test_path_traversal_rejected(client):
    r = client.post(f"{USER}/sync-jobs", json=_sync(src_path="../etc"))
    assert r.status_code == 422


# --- sync listing + ownership ----------------------------------------------


def test_list_scoped_to_own_user_origin(db, dms):
    # alice's user job, an operator job, and bob's user job all in the shared table.
    ca = make_client(db, dms, "alice")
    ca.post(f"{USER}/sync-jobs", json=_sync())
    db.sync[99] = {"id": 99, "origin": "operator", "created_by": "op", "state": "registered",
                   "src_storage": "x", "src_path": "a", "dst_storage": "y", "dst_path": "b",
                   "requester_id": "root", "options": {}, "delete_enabled": False,
                   "priority": "Low", "node_count": None, "memo": None, "approved": False,
                   "dms_request_id": None, "dms_job_id": None, "fingerprint": None,
                   "preview": None, "result": None, "error": None}
    cb = make_client(db, dms, "bob")
    cb.post(f"{USER}/sync-jobs", json=_sync())

    la = ca.get(f"{USER}/sync-jobs").json()
    assert la["total"] == 1 and la["items"][0]["created_by"] == "alice"


def test_cannot_touch_another_users_job(db, dms):
    ca = make_client(db, dms, "alice")
    jid = ca.post(f"{USER}/sync-jobs", json=_sync()).json()["id"]
    cb = make_client(db, dms, "bob")
    assert cb.get(f"{USER}/sync-jobs/{jid}").status_code == 404
    assert cb.post(f"{USER}/sync-jobs/{jid}:cancel").status_code == 404
    assert cb.delete(f"{USER}/sync-jobs/{jid}").status_code == 404


def test_cannot_touch_operator_job(client, db):
    db.sync[7] = {"id": 7, "origin": "operator", "created_by": "op", "state": "preview_ready",
                  "dms_job_id": "j7", "requester_id": "root"}
    assert client.get(f"{USER}/sync-jobs/7").status_code == 404
    assert client.post(f"{USER}/sync-jobs/7:approve").status_code == 404


def test_approve_requires_preview_ready(client, db):
    jid = client.post(f"{USER}/sync-jobs", json=_sync()).json()["id"]
    assert client.post(f"{USER}/sync-jobs/{jid}:approve").status_code == 409
    db.sync[jid]["state"] = "preview_ready"
    assert client.post(f"{USER}/sync-jobs/{jid}:approve").status_code == 200
    assert db.sync[jid]["approved"] is True


def test_cancel_calls_dms_and_marks_cancelled(client, db, dms):
    jid = client.post(f"{USER}/sync-jobs", json=_sync()).json()["id"]
    db.sync[jid]["state"] = "running"
    db.sync[jid]["dms_job_id"] = "job-xyz"
    r = client.post(f"{USER}/sync-jobs/{jid}:cancel")
    assert r.status_code == 200
    assert db.sync[jid]["state"] == "cancelled"
    assert "job-xyz" in dms.cancelled


# --- scan (read-only pull) --------------------------------------------------


def _scan_job(storage, path, files, bytes_):
    return {
        "operation": "data.scan", "storage_name": storage, "state": "Succeeded",
        "job_id": f"scan-{storage}-{path}", "resource_key": f"data.scan:{storage}:{path}",
        "updated_at": "2026-07-11T00:00:00Z",
        "result_summary": {"summary": {
            "file_count": files, "directory_count": 3, "total_bytes": bytes_,
            "error_count": 0, "scan_root": f"/mnt/{storage}/{path}", "selected_tool": "dscan",
        }},
    }


def _scan_job_failed(storage, path, reason="preflight_failed"):
    return {
        "operation": "data.scan", "storage_name": storage, "state": "Failed",
        "job_id": f"scan-{storage}-{path}", "resource_key": f"data.scan:{storage}:{path}",
        "updated_at": "2026-07-11T00:00:00Z",
        "preflight_result": {"reason": reason},
    }


def test_scan_item_registration_and_pull(db):
    dms = FakeDms(scan_jobs=[_scan_job("fs-a", "projects/teamA", 42, 1024)])
    c = make_client(db, dms, "alice")
    r = c.post(f"{USER}/scan-items", json={"storage": "fs-a", "path": "projects/teamA", "memo": "m"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "succeeded"
    assert body["has_result"] is True
    assert body["result"]["file_count"] == 42
    assert body["result"]["total_bytes"] == 1024


def test_scan_item_failed_status(db):
    dms = FakeDms(scan_jobs=[_scan_job_failed("fs-a", "projects/bad")])
    c = make_client(db, dms, "alice")
    r = c.post(f"{USER}/scan-items", json={"storage": "fs-a", "path": "projects/bad"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "failed"
    assert body["has_result"] is False
    assert "preflight" in (body["error"] or "")


def test_scan_item_none_status_when_never_scanned(db):
    dms = FakeDms(scan_jobs=[])
    c = make_client(db, dms, "alice")
    r = c.post(f"{USER}/scan-items", json={"storage": "fs-a", "path": "x"})
    body = r.json()
    assert body["status"] == "none" and body["has_result"] is False


def test_failed_rescan_keeps_prior_good_result(db):
    # latest attempt failed, but an older Succeeded scan exists: show 실패 badge AND
    # keep the prior result (don't hide good data behind a failed re-scan).
    dms = FakeDms(scan_jobs=[
        _scan_job_failed("fs-a", "projects/teamA"),      # newest = failed
        _scan_job("fs-a", "projects/teamA", 9, 900),      # older = succeeded
    ])
    c = make_client(db, dms, "alice")
    r = c.post(f"{USER}/scan-items", json={"storage": "fs-a", "path": "projects/teamA"})
    body = r.json()
    assert body["status"] == "failed"        # latest attempt
    assert body["has_result"] is True         # prior success preserved
    assert body["result"]["file_count"] == 9
    assert "preflight" in (body["error"] or "")


def test_running_rescan_keeps_prior_good_result(db):
    running = {
        "operation": "data.scan", "storage_name": "fs-a", "state": "Running",
        "job_id": "scan-run", "resource_key": "data.scan:fs-a:p", "updated_at": "z",
    }
    dms = FakeDms(scan_jobs=[running, _scan_job("fs-a", "p", 3, 30)])
    c = make_client(db, dms, "alice")
    r = c.post(f"{USER}/scan-items", json={"storage": "fs-a", "path": "p"})
    body = r.json()
    assert body["status"] == "running" and body["has_result"] is True
    assert body["result"]["file_count"] == 3


def test_scan_item_no_result_when_operator_never_scanned(db):
    dms = FakeDms(scan_jobs=[])
    c = make_client(db, dms, "alice")
    r = c.post(f"{USER}/scan-items", json={"storage": "fs-a", "path": "projects/none"})
    assert r.status_code == 201
    assert r.json()["has_result"] is False and r.json()["result"] is None


def test_scan_list_auto_reflects_operator_scan(db):
    # register first with NO scan, then an operator scan appears -> list reflects it.
    dms = FakeDms(scan_jobs=[])
    c = make_client(db, dms, "alice")
    c.post(f"{USER}/scan-items", json={"storage": "fs-a", "path": "projects/teamA"})
    assert c.get(f"{USER}/scan-items").json()["items"][0]["has_result"] is False
    dms.scan_jobs.append(_scan_job("fs-a", "projects/teamA", 7, 700))
    item = c.get(f"{USER}/scan-items").json()["items"][0]
    assert item["has_result"] is True and item["result"]["file_count"] == 7


def test_scan_item_duplicate_409(client):
    p = {"storage": "fs-a", "path": "dup"}
    assert client.post(f"{USER}/scan-items", json=p).status_code == 201
    assert client.post(f"{USER}/scan-items", json=p).status_code == 409


def test_scan_item_non_fs_storage_422(client):
    assert client.post(f"{USER}/scan-items", json={"storage": "csi-x", "path": "p"}).status_code == 422


def test_scan_item_unknown_storage_404(client):
    assert client.post(f"{USER}/scan-items", json={"storage": "ghost", "path": "p"}).status_code == 404


def test_scan_item_ownership(db, dms):
    ca = make_client(db, dms, "alice")
    iid = ca.post(f"{USER}/scan-items", json={"storage": "fs-a", "path": "mine"}).json()["id"]
    cb = make_client(db, dms, "bob")
    assert cb.get(f"{USER}/scan-items/{iid}").status_code == 404
    assert cb.delete(f"{USER}/scan-items/{iid}").status_code == 404
    assert cb.patch(f"{USER}/scan-items/{iid}", json={"memo": "x"}).status_code == 404
    # alice can
    assert ca.patch(f"{USER}/scan-items/{iid}", json={"memo": "x"}).status_code == 200
