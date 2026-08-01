"""Portal BFF — selective preview.

POST /batches/{id}:preview with `request_ids` previews only those registered
requests; the rest are parked ('held') and the batch goes to 'previewing'. The
orchestrator releases held -> registered on advance (covered in the orchestrator
suite). Whole-batch preview (no body) is unchanged.
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


class FakeDB:
    configured = True

    def __init__(self) -> None:
        self.batches: dict[str, dict[str, Any]] = {}
        self.requests: dict[int, dict[str, Any]] = {}
        self._n = 1

    def seed_batch(self, bid: str, *, status: str = "draft") -> None:
        self.batches[bid] = {"id": bid, "name": "b", "status": status}

    def seed_request(self, bid: str, *, state: str = "registered") -> int:
        rid = self._n
        self._n += 1
        self.requests[rid] = {"id": rid, "batch_id": bid, "state": state}
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

    async def hold_unselected_registered(self, bid: str, keep_ids: list[int]) -> int:
        n = 0
        for r in self.requests.values():
            if r["batch_id"] == bid and r["state"] == "registered" and r["id"] not in keep_ids:
                r["state"] = "held"
                n += 1
        return n

    async def release_held(self, bid: str) -> int:
        n = 0
        for r in self.requests.values():
            if r["batch_id"] == bid and r["state"] == "held":
                r["state"] = "registered"
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


def test_selective_preview_holds_unselected(client: TestClient, db: FakeDB):
    db.seed_batch("b1", status="draft")
    r1 = db.seed_request("b1")
    r2 = db.seed_request("b1")
    r3 = db.seed_request("b1")
    resp = client.post(f"{BASE}/batches/b1:preview", json={"request_ids": [r1]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "previewing" and body["scoped"] is True
    assert db.requests[r1]["state"] == "registered"  # selected -> previewed
    assert db.requests[r2]["state"] == "held"          # unselected parked
    assert db.requests[r3]["state"] == "held"


def test_whole_batch_preview_holds_nothing(client: TestClient, db: FakeDB):
    db.seed_batch("b1", status="draft")
    r1 = db.seed_request("b1")
    r2 = db.seed_request("b1")
    resp = client.post(f"{BASE}/batches/b1:preview")  # no body = preview all
    assert resp.status_code == 200
    assert resp.json()["scoped"] is False
    assert db.requests[r1]["state"] == "registered"
    assert db.requests[r2]["state"] == "registered"  # nothing held


def test_selective_preview_none_registered_selected_422(client: TestClient, db: FakeDB):
    db.seed_batch("b1", status="previewed")
    r_ready = db.seed_request("b1", state="preview_ready")  # not registered
    r_reg = db.seed_request("b1", state="registered")
    # select only the already-previewed one -> nothing registered to preview
    resp = client.post(f"{BASE}/batches/b1:preview", json={"request_ids": [r_ready]})
    assert resp.status_code == 422
    # the other registered request must be restored (not left held)
    assert db.requests[r_reg]["state"] == "registered"
    assert db.batches["b1"]["status"] == "previewed"  # unchanged


def test_selective_preview_requires_registered(client: TestClient, db: FakeDB):
    db.seed_batch("b1", status="draft")
    db.seed_request("b1", state="preview_ready")
    resp = client.post(f"{BASE}/batches/b1:preview", json={"request_ids": [999]})
    assert resp.status_code == 422  # no registered requests at all
