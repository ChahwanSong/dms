"""Portal BFF — batch-detail item management: add / delete / bulk-cancel and the
relaxed replace guard (request edits allowed on any non-in-flight batch)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from portal.backend import deps, security
from portal.backend.config import Settings
from portal.backend.routers.backup import backup_router

BASE = "/api/operator/backup"
INFLIGHT = {"preview_pending", "approved", "running"}
TERMINAL = {"succeeded", "failed", "cancelled", "preview_failed"}


class FakeDB:
    configured = True

    def __init__(self) -> None:
        self.batches: dict[str, dict[str, Any]] = {}
        self.requests: dict[str, list[dict[str, Any]]] = {}
        self._n = 1

    def seed_batch(self, bid: str, *, status: str = "previewed") -> None:
        self.batches[bid] = {"id": bid, "status": status}
        self.requests.setdefault(bid, [])

    def add_req(self, bid: str, state: str = "registered", **kw: Any) -> int:
        rid = self._n
        self._n += 1
        r = {"id": rid, "batch_id": bid, "state": state, "src_storage": "s",
             "src_path": "a", "dst_storage": "d", "dst_path": "b", "dms_job_id": None}
        r.update(kw)
        self.requests.setdefault(bid, []).append(r)
        return rid

    async def get_batch(self, bid: str) -> dict[str, Any] | None:
        b = self.batches.get(bid)
        return dict(b) if b else None

    async def add_requests(self, bid: str, rows: list[dict[str, str]]) -> int:
        for row in rows:
            self.add_req(bid, "registered", **row)
        return len(rows)

    async def replace_requests(self, bid: str, rows: list[dict[str, str]]) -> int:
        self.requests[bid] = []
        for row in rows:
            self.add_req(bid, "registered", **row)
        return len(rows)

    async def delete_requests(self, bid: str, ids: list[int]) -> int:
        rows = self.requests.get(bid, [])
        keep = [r for r in rows if not (r["id"] in ids and r["state"] not in INFLIGHT)]
        n = len(rows) - len(keep)
        self.requests[bid] = keep
        return n

    async def cancel_requests(self, bid: str, request_ids: list[int] | None = None) -> list[str]:
        out: list[str] = []
        for r in self.requests.get(bid, []):
            if request_ids is not None and r["id"] not in request_ids:
                continue
            if r["state"] in TERMINAL:
                continue
            r["state"] = "cancelled"
            if r.get("dms_job_id"):
                out.append(r["dms_job_id"])
        return out


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


def _req(**kw):
    base = {"src_storage": "s", "src_path": "a", "dst_storage": "d", "dst_path": "b"}
    base.update(kw)
    return base


# --- :add -------------------------------------------------------------------


def test_add_appends_registered_on_non_inflight(client, db):
    db.seed_batch("b1", status="previewed")
    db.add_req("b1", "succeeded")
    r = client.post(f"{BASE}/batches/b1/requests:add", json=[_req(src_path="new1"), _req(src_path="new2")])
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 2
    states = [x["state"] for x in db.requests["b1"]]
    assert states.count("registered") == 2 and states.count("succeeded") == 1


def test_add_blocked_while_running_409(client, db):
    db.seed_batch("b1", status="running")
    r = client.post(f"{BASE}/batches/b1/requests:add", json=[_req()])
    assert r.status_code == 409


def test_add_missing_batch_404(client, db):
    r = client.post(f"{BASE}/batches/nope/requests:add", json=[_req()])
    assert r.status_code == 404


def test_add_rejects_traversal_422(client, db):
    db.seed_batch("b1")
    r = client.post(f"{BASE}/batches/b1/requests:add", json=[_req(src_path="../x")])
    assert r.status_code == 422


# --- :delete ----------------------------------------------------------------


def test_delete_removes_selected(client, db):
    db.seed_batch("b1", status="previewed")
    a = db.add_req("b1", "preview_failed")
    b = db.add_req("b1", "succeeded")
    c = db.add_req("b1", "registered")
    r = client.post(f"{BASE}/batches/b1/requests:delete", json={"request_ids": [a, c]})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 2
    assert [x["id"] for x in db.requests["b1"]] == [b]


def test_delete_blocked_while_previewing_409(client, db):
    db.seed_batch("b1", status="previewing")
    r = client.post(f"{BASE}/batches/b1/requests:delete", json={"request_ids": [1]})
    assert r.status_code == 409


def test_delete_empty_ids_422(client, db):
    db.seed_batch("b1")
    r = client.post(f"{BASE}/batches/b1/requests:delete", json={"request_ids": []})
    assert r.status_code == 422


# --- replace guard relaxed --------------------------------------------------


def test_replace_now_allowed_on_previewed(client, db):
    db.seed_batch("b1", status="previewed")
    db.add_req("b1", "succeeded")
    r = client.put(f"{BASE}/batches/b1/requests", json=[_req(src_path="x")])
    assert r.status_code == 200, r.text
    assert [x["state"] for x in db.requests["b1"]] == ["registered"]


def test_replace_still_blocked_while_running(client, db):
    db.seed_batch("b1", status="running")
    r = client.put(f"{BASE}/batches/b1/requests", json=[_req()])
    assert r.status_code == 409


# --- bulk :cancel -----------------------------------------------------------


def test_bulk_cancel_selected(client, db):
    db.seed_batch("b1", status="running")
    a = db.add_req("b1", "running")
    b = db.add_req("b1", "succeeded")
    c = db.add_req("b1", "approved")
    r = client.post(f"{BASE}/batches/b1/requests:cancel", json={"request_ids": [a, b, c]})
    assert r.status_code == 200, r.text
    by_id = {x["id"]: x["state"] for x in db.requests["b1"]}
    assert by_id[a] == "cancelled" and by_id[c] == "cancelled"
    assert by_id[b] == "succeeded"  # terminal not touched
