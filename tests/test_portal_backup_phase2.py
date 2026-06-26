"""Portal BFF — Phase 2: selective/staged approval, close, per-item cancel.

Router logic against an in-memory fake Database + fake DmsClient (no Postgres,
no session). Covers the state transitions that carry risk: which requests get
approved/excluded/cancelled and how the batch status advances.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from portal.backend import deps, security
from portal.backend.config import Settings
from portal.backend.routers.backup import backup_router

BASE = "/api/operator/backup"
TERMINAL = {"succeeded", "failed", "cancelled", "preview_failed"}


class FakeDB:
    configured = True

    def __init__(self) -> None:
        self.batches: dict[str, dict[str, Any]] = {}
        self.requests: dict[int, dict[str, Any]] = {}
        self._next = 1

    def seed_batch(self, bid: str, *, status: str = "previewed", **kw: Any) -> None:
        self.batches[bid] = {"id": bid, "name": kw.get("name", "b"), "status": status,
                             "delete_enabled": kw.get("delete_enabled", False),
                             "options": {}, "requester_id": "root", "created_by": "op", "note": None}

    def seed_request(self, bid: str, *, state: str = "preview_ready", dms_job_id: str | None = None) -> int:
        rid = self._next
        self._next += 1
        self.requests[rid] = {"id": rid, "batch_id": bid, "state": state,
                              "src_storage": "s", "src_path": "a", "dst_storage": "d", "dst_path": "b",
                              "dms_job_id": dms_job_id, "preview": None, "error": None}
        return rid

    async def get_batch(self, bid: str) -> dict[str, Any] | None:
        b = self.batches.get(bid)
        return dict(b) if b else None

    async def set_batch_status(self, bid: str, status: str) -> None:
        self.batches[bid]["status"] = status

    async def batch_state_counts(self, bid: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.requests.values():
            if r["batch_id"] == bid:
                out[r["state"]] = out.get(r["state"], 0) + 1
        return out

    async def approve_requests(self, bid: str, request_ids: list[int] | None) -> int:
        n = 0
        for r in self.requests.values():
            if r["batch_id"] == bid and r["state"] == "preview_ready" and (
                request_ids is None or r["id"] in request_ids
            ):
                r["state"] = "approved"
                n += 1
        return n

    async def exclude_preview_ready(self, bid: str) -> int:
        n = 0
        for r in self.requests.values():
            if r["batch_id"] == bid and r["state"] == "preview_ready":
                r["state"] = "cancelled"
                n += 1
        return n

    async def get_request(self, rid: int) -> dict[str, Any] | None:
        r = self.requests.get(rid)
        return dict(r) if r else None

    async def cancel_request(self, bid: str, rid: int) -> tuple[bool, str | None]:
        r = self.requests.get(rid)
        if r and r["batch_id"] == bid and r["state"] not in TERMINAL:
            r["state"] = "cancelled"
            return True, r["dms_job_id"]
        return False, None


class FakeDms:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel_job(self, job_id: str, *, actor: str) -> dict[str, Any]:
        self.cancelled.append(job_id)
        return {}


def make_client(db: FakeDB, dms: FakeDms) -> TestClient:
    app = FastAPI()
    app.include_router(backup_router(Settings()))
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_dms_client] = lambda: dms
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": "op", "role": "operator", "method": "local",
    }
    return TestClient(app)


@pytest.fixture
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture
def dms() -> FakeDms:
    return FakeDms()


@pytest.fixture
def client(db: FakeDB, dms: FakeDms) -> TestClient:
    return make_client(db, dms)


# --- :approve (selective / staged) ------------------------------------------

def test_approve_selected_only(client, db):
    db.seed_batch("b1", status="previewed")
    r1 = db.seed_request("b1", state="preview_ready")
    r2 = db.seed_request("b1", state="preview_ready")
    resp = client.post(f"{BASE}/batches/b1:approve", json={"request_ids": [r1]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved"] == 1
    assert db.requests[r1]["state"] == "approved"
    assert db.requests[r2]["state"] == "preview_ready"
    assert db.batches["b1"]["status"] == "running"


def test_approve_all_when_no_body(client, db):
    db.seed_batch("b1", status="previewed")
    r1 = db.seed_request("b1", state="preview_ready")
    r2 = db.seed_request("b1", state="preview_ready")
    resp = client.post(f"{BASE}/batches/b1:approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved"] == 2
    assert db.requests[r1]["state"] == "approved"
    assert db.requests[r2]["state"] == "approved"


def test_approve_allowed_while_running_staged(client, db):
    db.seed_batch("b1", status="running")
    db.seed_request("b1", state="running")  # earlier approved, now executing
    r2 = db.seed_request("b1", state="preview_ready")
    resp = client.post(f"{BASE}/batches/b1:approve", json={"request_ids": [r2]})
    assert resp.status_code == 200, resp.text
    assert db.requests[r2]["state"] == "approved"


def test_approve_nothing_matched_422(client, db):
    db.seed_batch("b1", status="previewed")
    db.seed_request("b1", state="preview_ready")
    resp = client.post(f"{BASE}/batches/b1:approve", json={"request_ids": [99999]})
    assert resp.status_code == 422


def test_approve_from_draft_409(client, db):
    db.seed_batch("b1", status="draft")
    db.seed_request("b1", state="registered")
    resp = client.post(f"{BASE}/batches/b1:approve")
    assert resp.status_code == 409


# --- :close (마감) ----------------------------------------------------------

def test_close_excludes_and_completes(client, db):
    db.seed_batch("b1", status="previewed")
    db.seed_request("b1", state="preview_ready")
    db.seed_request("b1", state="preview_ready")
    resp = client.post(f"{BASE}/batches/b1:close")
    assert resp.status_code == 200, resp.text
    assert resp.json()["excluded"] == 2
    assert resp.json()["status"] == "done"
    assert all(r["state"] == "cancelled" for r in db.requests.values())


def test_close_leaves_running_when_work_remains(client, db):
    db.seed_batch("b1", status="running")
    db.seed_request("b1", state="approved")
    db.seed_request("b1", state="preview_ready")
    resp = client.post(f"{BASE}/batches/b1:close")
    assert resp.status_code == 200, resp.text
    assert resp.json()["excluded"] == 1
    assert resp.json()["status"] == "running"  # approved item still pending


# --- per-item cancel --------------------------------------------------------

def test_cancel_running_request_cancels_dms(client, db, dms):
    db.seed_batch("b1", status="running")
    rid = db.seed_request("b1", state="running", dms_job_id="job-x")
    resp = client.post(f"{BASE}/batches/b1/requests/{rid}:cancel")
    assert resp.status_code == 200, resp.text
    assert db.requests[rid]["state"] == "cancelled"
    assert resp.json()["dms_cancelled"] == 1
    assert dms.cancelled == ["job-x"]


def test_cancel_preview_ready_request_no_dms(client, db, dms):
    db.seed_batch("b1", status="previewed")
    rid = db.seed_request("b1", state="preview_ready")
    resp = client.post(f"{BASE}/batches/b1/requests/{rid}:cancel")
    assert resp.status_code == 200, resp.text
    assert db.requests[rid]["state"] == "cancelled"
    assert resp.json()["dms_cancelled"] == 0
    assert dms.cancelled == []


def test_cancel_terminal_request_409(client, db):
    db.seed_batch("b1", status="running")
    rid = db.seed_request("b1", state="succeeded")
    resp = client.post(f"{BASE}/batches/b1/requests/{rid}:cancel")
    assert resp.status_code == 409
    assert db.requests[rid]["state"] == "succeeded"


def test_cancel_request_wrong_batch_404(client, db):
    db.seed_batch("b1", status="running")
    db.seed_batch("b2", status="running")
    rid = db.seed_request("b2", state="running")
    resp = client.post(f"{BASE}/batches/b1/requests/{rid}:cancel")
    assert resp.status_code == 404
