"""Portal BFF — backup full re-run (:rerun) + per-item live job detail / log tail.

These hit the actual backup/scan routers via TestClient against in-memory
FakeDB/FakeDms (mirroring tests/test_portal_batch_bulk.py). They assert:
  - backup :rerun resets ALL terminal requests (incl succeeded) to 'registered',
    CLEARS their job/preview/result fields, and moves the batch to 'previewing'
    (NOT auto-run — sync is destructive).
  - the per-request /job and /logs routes proxy the DMS response verbatim, return
    {available:false} when the portal row has no DMS job yet, and forward
    DmsApiError as the same HTTP status + detail (like the :cancel routes).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from portal.backend import deps, security
from portal.backend.config import Settings
from portal.backend.dms_client import DmsApiError
from portal.backend.routers.backup import backup_router
from portal.backend.routers.scan import scan_router

BACKUP = "/api/operator/backup"
SCAN = "/api/operator/scan"

# Terminal request states (what :rerun's all_terminal reset targets).
BACKUP_TERMINAL = {"succeeded", "failed", "preview_failed", "cancelled"}
# Fields cleared on reset (mirrors db.reset_requests SQL).
CLEARED = ("dms_job_id", "dms_request_id", "fingerprint", "preview", "result", "error")


class FakeDB:
    configured = True

    def __init__(self) -> None:
        self.bb: dict[str, dict[str, Any]] = {}  # backup batches
        self.sb: dict[str, dict[str, Any]] = {}  # scan batches
        self.reqs: dict[int, dict[str, Any]] = {}  # all requests (backup + scan)
        self._n = 1

    # --- seeding ---
    def seed_backup(self, bid: str, status: str) -> None:
        self.bb[bid] = {"id": bid, "status": status}

    def seed_scan(self, bid: str, status: str) -> None:
        self.sb[bid] = {"id": bid, "status": status}

    def add_req(self, bid: str, state: str, **kw: Any) -> int:
        rid = self._n
        self._n += 1
        r = {
            "id": rid, "batch_id": bid, "state": state,
            "src_storage": "s", "src_path": "a", "dst_storage": "d", "dst_path": "b",
            "storage": "cephfs-dms", "path": "e2e/src",
            "dms_job_id": kw.get("dms_job_id"),
            "dms_request_id": kw.get("dms_request_id"),
            "fingerprint": kw.get("fingerprint"),
            "preview": kw.get("preview"), "result": kw.get("result"),
            "error": kw.get("error"),
        }
        self.reqs[rid] = r
        return rid

    # --- batches ---
    async def get_batch(self, bid: str) -> dict[str, Any] | None:
        b = self.bb.get(bid)
        return dict(b) if b else None

    async def get_scan_batch(self, bid: str) -> dict[str, Any] | None:
        b = self.sb.get(bid)
        return dict(b) if b else None

    async def set_batch_status(self, bid: str, status: str) -> None:
        self.bb[bid]["status"] = status

    async def batch_state_counts(self, bid: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.reqs.values():
            if r["batch_id"] == bid:
                out[r["state"]] = out.get(r["state"], 0) + 1
        return out

    # --- requests ---
    async def get_request(self, rid: int) -> dict[str, Any] | None:
        r = self.reqs.get(rid)
        return dict(r) if r else None

    async def get_scan_request(self, rid: int) -> dict[str, Any] | None:
        r = self.reqs.get(rid)
        return dict(r) if r else None

    async def release_held(self, bid: str) -> int:
        n = 0
        for r in self.reqs.values():
            if r["batch_id"] == bid and r["state"] == "held":
                r["state"] = "registered"
                n += 1
        return n

    async def reset_requests(
        self, bid: str, *, request_ids: list[int] | None = None,
        failed_only: bool = False, all_terminal: bool = False,
    ) -> list[str | None]:
        cleared: list[str | None] = []
        for r in self.reqs.values():
            if r["batch_id"] != bid:
                continue
            if all_terminal:
                match = r["state"] in BACKUP_TERMINAL
            elif failed_only:
                match = r["state"] in {"failed", "preview_failed"}
            else:
                match = r["id"] in (request_ids or []) and r["state"] in {
                    "registered", "preview_ready", "preview_failed", "failed", "cancelled",
                }
            if match:
                cleared.append(r.get("dms_job_id"))
                r["state"] = "registered"
                for k in CLEARED:
                    r[k] = None
        return cleared


class FakeDms:
    def __init__(
        self,
        sync_job: dict[str, Any] | None = None,
        scan_job: dict[str, Any] | None = None,
        logs: dict[str, Any] | None = None,
        fail: Exception | None = None,
    ) -> None:
        self.sync_job = sync_job or {}
        self.scan_job = scan_job or {}
        self.logs = logs or {}
        self.fail = fail
        self.calls: list[tuple[str, Any]] = []

    async def get_sync_job(self, job_id: str, *, actor: str) -> dict[str, Any]:
        self.calls.append(("sync", job_id))
        if self.fail:
            raise self.fail
        return self.sync_job

    async def get_scan_job(self, job_id: str, *, actor: str) -> dict[str, Any]:
        self.calls.append(("scan", job_id))
        if self.fail:
            raise self.fail
        return self.scan_job

    async def get_data_job_logs(self, job_id: str, *, tail: int, actor: str) -> dict[str, Any]:
        self.calls.append(("logs", (job_id, tail)))
        if self.fail:
            raise self.fail
        return self.logs

    async def cancel_job(self, job_id: str, *, actor: str) -> dict[str, Any]:
        self.calls.append(("cancel", job_id))
        return {"job_id": job_id, "cancelled": True}


def make_client(db: FakeDB, dms: FakeDms) -> TestClient:
    app = FastAPI()
    app.include_router(backup_router(Settings()))
    app.include_router(scan_router(Settings()))
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_dms_client] = lambda: dms
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": "op", "role": "operator", "method": "local",
    }
    return TestClient(app)


@pytest.fixture
def db() -> FakeDB:
    return FakeDB()


# ============================ backup :rerun ============================


def test_rerun_resets_all_terminal_incl_succeeded_to_previewing(db):
    dms = FakeDms()
    client = make_client(db, dms)
    db.seed_backup("b1", "done")
    ok = db.add_req("b1", "succeeded", dms_job_id="j1", dms_request_id="r1",
                    fingerprint="fp", preview={"files": 5}, result={"state": "Succeeded"})
    bad = db.add_req("b1", "failed", dms_job_id="j2", error="boom")
    r = client.post(f"{BACKUP}/batches/b1:rerun")
    assert r.status_code == 200, r.text
    body = r.json()
    # (C) reset cancels each reset item's orphaned DMS job (j1, j2) so the fresh
    # re-submit can't hit a resource-key Conflict against a still-live prior job.
    assert body == {"id": "b1", "status": "previewing", "reset": 2, "dms_cancelled": 2}
    assert sorted(c for k, c in dms.calls if k == "cancel") == ["j1", "j2"]
    assert db.bb["b1"]["status"] == "previewing"
    # succeeded item was reset too (the gap :rerun closes vs the retry route).
    assert db.reqs[ok]["state"] == "registered"
    assert db.reqs[bad]["state"] == "registered"


def test_rerun_clears_job_and_preview_fields(db):
    dms = FakeDms()
    client = make_client(db, dms)
    db.seed_backup("b1", "done")
    rid = db.add_req("b1", "succeeded", dms_job_id="j1", dms_request_id="r1",
                     fingerprint="fp", preview={"files": 5}, result={"state": "Succeeded"})
    r = client.post(f"{BACKUP}/batches/b1:rerun")
    assert r.status_code == 200, r.text
    row = db.reqs[rid]
    for k in CLEARED:
        assert row[k] is None, f"{k} not cleared"


def test_rerun_allowed_from_cancelled_and_previewed(db):
    dms = FakeDms()
    client = make_client(db, dms)
    for bid, status in (("c", "cancelled"), ("p", "previewed")):
        db.seed_backup(bid, status)
        db.add_req(bid, "succeeded", dms_job_id="jx")
        r = client.post(f"{BACKUP}/batches/{bid}:rerun")
        assert r.status_code == 200, r.text
        assert db.bb[bid]["status"] == "previewing"


def test_rerun_rejects_inflight_batch_409(db):
    dms = FakeDms()
    client = make_client(db, dms)
    db.seed_backup("b1", "running")
    db.add_req("b1", "succeeded")
    r = client.post(f"{BACKUP}/batches/b1:rerun")
    assert r.status_code == 409


def test_rerun_missing_batch_404(db):
    client = make_client(db, FakeDms())
    r = client.post(f"{BACKUP}/batches/nope:rerun")
    assert r.status_code == 404


def test_rerun_no_requests_422(db):
    dms = FakeDms()
    client = make_client(db, dms)
    db.seed_backup("b1", "done")  # no requests at all
    r = client.post(f"{BACKUP}/batches/b1:rerun")
    assert r.status_code == 422


# ====================== per-item job detail (proxy) ======================


def test_backup_job_proxies_full_dms_dict(db):
    job = {
        "state": "Succeeded", "selected_tool": "dsync", "volcano_job_ref": "vc/x",
        "artifact_uri": "s3://a", "log_uri": "s3://l",
        "result_summary": {"summary": {"file_count": 9},
                           "file_size_histogram": [{"bucket": "[0,1K]", "count": 3}]},
        "preflight_result": {"reason": None},
    }
    dms = FakeDms(sync_job=job)
    client = make_client(db, dms)
    db.seed_backup("b1", "running")
    rid = db.add_req("b1", "running", dms_job_id="j1")
    r = client.get(f"{BACKUP}/batches/b1/requests/{rid}/job")
    assert r.status_code == 200, r.text
    assert r.json() == job
    assert dms.calls == [("sync", "j1")]


def test_backup_job_available_false_when_no_job(db):
    dms = FakeDms()
    client = make_client(db, dms)
    db.seed_backup("b1", "draft")
    rid = db.add_req("b1", "registered")  # never submitted -> no dms_job_id
    r = client.get(f"{BACKUP}/batches/b1/requests/{rid}/job")
    assert r.status_code == 200, r.text
    assert r.json()["available"] is False
    assert dms.calls == []  # DMS not called


def test_backup_job_forwards_dms_error(db):
    dms = FakeDms(fail=DmsApiError(status_code=403, detail="forbidden"))
    client = make_client(db, dms)
    db.seed_backup("b1", "running")
    rid = db.add_req("b1", "running", dms_job_id="j1")
    r = client.get(f"{BACKUP}/batches/b1/requests/{rid}/job")
    assert r.status_code == 403
    assert r.json()["detail"] == "forbidden"


def test_backup_job_unknown_batch_404(db):
    client = make_client(db, FakeDms())
    r = client.get(f"{BACKUP}/batches/nope/requests/1/job")
    assert r.status_code == 404


def test_scan_job_proxies_full_dms_dict(db):
    job = {"state": "Running", "selected_tool": "dscan", "volcano_job_ref": "vc/y"}
    dms = FakeDms(scan_job=job)
    client = make_client(db, dms)
    db.seed_scan("s1", "scanning")
    rid = db.add_req("s1", "running", dms_job_id="sj1")
    r = client.get(f"{SCAN}/batches/s1/requests/{rid}/job")
    assert r.status_code == 200, r.text
    assert r.json() == job
    assert dms.calls == [("scan", "sj1")]


def test_scan_job_available_false_when_no_job(db):
    dms = FakeDms()
    client = make_client(db, dms)
    db.seed_scan("s1", "draft")
    rid = db.add_req("s1", "registered")
    r = client.get(f"{SCAN}/batches/s1/requests/{rid}/job")
    assert r.status_code == 200, r.text
    assert r.json()["available"] is False


# ========================= per-item log tail (proxy) =========================


def test_backup_logs_proxies_dms_payload(db):
    payload = {
        "job_id": "j1", "available": True,
        "pods": [{"name": "p-0", "node_name": "n1", "role": "launcher", "phase": "Running"}],
        "logs": "line1\nline2", "note": "",
    }
    dms = FakeDms(logs=payload)
    client = make_client(db, dms)
    db.seed_backup("b1", "running")
    rid = db.add_req("b1", "running", dms_job_id="j1")
    r = client.get(f"{BACKUP}/batches/b1/requests/{rid}/logs?tail=50")
    assert r.status_code == 200, r.text
    assert r.json() == payload
    assert dms.calls == [("logs", ("j1", 50))]


def test_backup_logs_available_false_when_no_job(db):
    dms = FakeDms()
    client = make_client(db, dms)
    db.seed_backup("b1", "draft")
    rid = db.add_req("b1", "registered")
    r = client.get(f"{BACKUP}/batches/b1/requests/{rid}/logs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False and body["logs"] == "" and body["pods"] == []
    assert dms.calls == []


def test_backup_logs_forwards_dms_error(db):
    dms = FakeDms(fail=DmsApiError(status_code=502, detail="dms_unreachable"))
    client = make_client(db, dms)
    db.seed_backup("b1", "running")
    rid = db.add_req("b1", "running", dms_job_id="j1")
    r = client.get(f"{BACKUP}/batches/b1/requests/{rid}/logs")
    assert r.status_code == 502
    assert r.json()["detail"] == "dms_unreachable"


def test_backup_logs_tail_bound_422(db):
    client = make_client(db, FakeDms())
    db.seed_backup("b1", "running")
    rid = db.add_req("b1", "running", dms_job_id="j1")
    r = client.get(f"{BACKUP}/batches/b1/requests/{rid}/logs?tail=99999")
    assert r.status_code == 422  # le=5000


def test_scan_logs_proxies_dms_payload(db):
    payload = {"job_id": "sj1", "available": True, "pods": [], "logs": "x", "note": ""}
    dms = FakeDms(logs=payload)
    client = make_client(db, dms)
    db.seed_scan("s1", "scanning")
    rid = db.add_req("s1", "running", dms_job_id="sj1")
    r = client.get(f"{SCAN}/batches/s1/requests/{rid}/logs")
    assert r.status_code == 200, r.text
    assert r.json() == payload
    assert dms.calls == [("logs", ("sj1", 400))]  # default tail


def test_scan_logs_forwards_dms_error(db):
    dms = FakeDms(fail=DmsApiError(status_code=403, detail="nope"))
    client = make_client(db, dms)
    db.seed_scan("s1", "scanning")
    rid = db.add_req("s1", "running", dms_job_id="sj1")
    r = client.get(f"{SCAN}/batches/s1/requests/{rid}/logs")
    assert r.status_code == 403
    assert r.json()["detail"] == "nope"
