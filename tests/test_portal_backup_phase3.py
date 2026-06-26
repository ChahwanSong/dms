"""Portal BFF — Phase 3: re-edit + retry after preview.

Resetting a request to 'registered' lets the orchestrator re-preview it. Tests
the router pieces: generalized PATCH (edit fixable items, reset on edit),
:reset (single/bulk failed), and :preview re-allowed from done.
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
RESETTABLE = {"registered", "preview_ready", "preview_failed", "failed", "cancelled"}


class FakeDB:
    configured = True

    def __init__(self) -> None:
        self.batches: dict[str, dict[str, Any]] = {}
        self.requests: dict[int, dict[str, Any]] = {}
        self._n = 1

    def seed_batch(self, bid: str, *, status: str = "previewed", **kw: Any) -> None:
        self.batches[bid] = {"id": bid, "name": "b", "status": status, "delete_enabled": False,
                             "options": {}, "requester_id": "root", "created_by": "op", "note": None}

    def seed_request(self, bid: str, *, state: str = "preview_ready", **kw: Any) -> int:
        rid = self._n
        self._n += 1
        self.requests[rid] = {"id": rid, "batch_id": bid, "state": state,
                              "src_storage": "s", "src_path": "a", "dst_storage": "d", "dst_path": "b",
                              "dms_job_id": kw.get("dms_job_id"), "dms_request_id": kw.get("dms_request_id"),
                              "fingerprint": kw.get("fingerprint"), "preview": kw.get("preview"),
                              "result": None, "error": kw.get("error")}
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

    async def get_request(self, rid: int) -> dict[str, Any] | None:
        r = self.requests.get(rid)
        return dict(r) if r else None

    async def edit_request_paths(self, rid: int, row: dict[str, str]) -> None:
        r = self.requests[rid]
        r.update(row)
        r.update({"state": "registered", "dms_job_id": None, "dms_request_id": None,
                  "fingerprint": None, "preview": None, "result": None, "error": None})

    async def reset_requests(self, bid: str, *, request_ids=None, failed_only: bool = False) -> int:
        n = 0
        for r in self.requests.values():
            if r["batch_id"] != bid:
                continue
            if failed_only:
                if r["state"] not in ("failed", "preview_failed"):
                    continue
            else:
                if request_ids is None or r["id"] not in request_ids:
                    continue
                if r["state"] not in RESETTABLE:
                    continue
            r.update({"state": "registered", "dms_job_id": None, "dms_request_id": None,
                      "fingerprint": None, "preview": None, "result": None, "error": None})
            n += 1
        return n


def make_client(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(backup_router(Settings()))
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_dms_client] = lambda: None
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": "op", "role": "operator", "method": "local",
    }
    return TestClient(app)


@pytest.fixture
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture
def client(db: FakeDB) -> TestClient:
    return make_client(db)


PATHS = {"src_storage": "s2", "src_path": "x/y", "dst_storage": "d2", "dst_path": "z"}


# --- generalized PATCH (edit fixable items, reset on edit) -------------------

def test_patch_failed_request_edits_and_resets(client, db):
    db.seed_batch("b1", status="done")
    rid = db.seed_request("b1", state="failed", dms_job_id="j1", fingerprint="fp",
                          preview={"files": 1}, error="boom")
    resp = client.patch(f"{BASE}/batches/b1/requests/{rid}", json=PATHS)
    assert resp.status_code == 200, resp.text
    r = db.requests[rid]
    assert r["state"] == "registered"
    assert (r["src_storage"], r["src_path"]) == ("s2", "x/y")
    assert r["dms_job_id"] is None and r["fingerprint"] is None
    assert r["preview"] is None and r["error"] is None


def test_patch_preview_ready_request_ok(client, db):
    db.seed_batch("b1", status="previewed")
    rid = db.seed_request("b1", state="preview_ready")
    resp = client.patch(f"{BASE}/batches/b1/requests/{rid}", json=PATHS)
    assert resp.status_code == 200, resp.text
    assert db.requests[rid]["state"] == "registered"


def test_patch_running_request_409(client, db):
    db.seed_batch("b1", status="running")
    rid = db.seed_request("b1", state="running")
    resp = client.patch(f"{BASE}/batches/b1/requests/{rid}", json=PATHS)
    assert resp.status_code == 409


def test_patch_succeeded_request_409(client, db):
    db.seed_batch("b1", status="done")
    rid = db.seed_request("b1", state="succeeded")
    resp = client.patch(f"{BASE}/batches/b1/requests/{rid}", json=PATHS)
    assert resp.status_code == 409


# --- :reset -----------------------------------------------------------------

def test_reset_failed_only(client, db):
    db.seed_batch("b1", status="done")
    rf = db.seed_request("b1", state="failed")
    rpf = db.seed_request("b1", state="preview_failed")
    rok = db.seed_request("b1", state="succeeded")
    resp = client.post(f"{BASE}/batches/b1/requests:reset", json={"failed_only": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["reset"] == 2
    assert db.requests[rf]["state"] == "registered"
    assert db.requests[rpf]["state"] == "registered"
    assert db.requests[rok]["state"] == "succeeded"


def test_reset_by_ids_skips_inflight(client, db):
    db.seed_batch("b1", status="running")
    rfail = db.seed_request("b1", state="failed")
    rrun = db.seed_request("b1", state="running")
    resp = client.post(
        f"{BASE}/batches/b1/requests:reset", json={"request_ids": [rfail, rrun]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["reset"] == 1  # only the failed one; running is in-flight
    assert db.requests[rfail]["state"] == "registered"
    assert db.requests[rrun]["state"] == "running"


def test_reset_without_target_422(client, db):
    db.seed_batch("b1", status="done")
    db.seed_request("b1", state="failed")
    resp = client.post(f"{BASE}/batches/b1/requests:reset", json={})
    assert resp.status_code == 422


def test_reset_while_previewing_409(client, db):
    db.seed_batch("b1", status="previewing")
    db.seed_request("b1", state="failed")
    resp = client.post(f"{BASE}/batches/b1/requests:reset", json={"failed_only": True})
    assert resp.status_code == 409


# --- :preview re-allowed from done ------------------------------------------

def test_preview_from_done_allowed(client, db):
    db.seed_batch("b1", status="done")
    db.seed_request("b1", state="registered")  # a reset item awaiting re-preview
    resp = client.post(f"{BASE}/batches/b1:preview")
    assert resp.status_code == 200, resp.text
    assert db.batches["b1"]["status"] == "previewing"


def test_preview_while_previewing_409(client, db):
    db.seed_batch("b1", status="previewing")
    db.seed_request("b1", state="registered")
    resp = client.post(f"{BASE}/batches/b1:preview")
    assert resp.status_code == 409
