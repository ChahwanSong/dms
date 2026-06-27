"""Portal BFF — PUT /batches/{id}/requests (replace all, draft-only).

Backs the inline request-table editor: the table is the full desired request set,
so saving replaces the draft batch's requests in one call.
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
        self.requests: dict[str, list[dict[str, str]]] = {}

    def seed_batch(self, bid: str, *, status: str = "draft") -> None:
        self.batches[bid] = {"id": bid, "status": status}
        self.requests.setdefault(bid, [])

    async def get_batch(self, bid: str) -> dict[str, Any] | None:
        b = self.batches.get(bid)
        return dict(b) if b else None

    async def replace_requests(self, bid: str, rows: list[dict[str, str]]) -> int:
        self.requests[bid] = list(rows)
        return len(rows)


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


def test_replace_swaps_request_set(client, db):
    db.seed_batch("b1", status="draft")
    db.requests["b1"] = [_req(src_path="old1"), _req(src_path="old2"), _req(src_path="old3")]
    resp = client.put(
        f"{BASE}/batches/b1/requests",
        json=[_req(src_path="new1"), _req(src_path="new2")],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 2
    assert [r["src_path"] for r in db.requests["b1"]] == ["new1", "new2"]


def test_replace_normalizes_paths(client, db):
    db.seed_batch("b1", status="draft")
    resp = client.put(
        f"{BASE}/batches/b1/requests",
        json=[_req(src_path="/x/y/", dst_path="z/")],
    )
    assert resp.status_code == 200, resp.text
    assert db.requests["b1"][0]["src_path"] == "x/y"
    assert db.requests["b1"][0]["dst_path"] == "z"


def test_replace_rejects_traversal_422(client, db):
    db.seed_batch("b1", status="draft")
    resp = client.put(f"{BASE}/batches/b1/requests", json=[_req(src_path="../etc")])
    assert resp.status_code == 422


def test_replace_non_draft_409(client, db):
    db.seed_batch("b1", status="previewing")
    resp = client.put(f"{BASE}/batches/b1/requests", json=[_req()])
    assert resp.status_code == 409


def test_replace_missing_batch_404(client, db):
    resp = client.put(f"{BASE}/batches/nope/requests", json=[_req()])
    assert resp.status_code == 404
