"""Portal BFF — draft backup-batch editing endpoints (Phase 1).

The portal DB is Postgres-only (no SQLite fallback) and there is no portal test
harness yet, so we exercise the router logic against an in-memory fake Database
with FastAPI dependency overrides — no Postgres, no session middleware. This
covers the behavior that actually carries risk: path normalization, draft-only
guards, and ownership checks.
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
    """In-memory stand-in for portal.backend.db.Database (only the methods the
    edit endpoints touch)."""

    configured = True

    def __init__(self) -> None:
        self.batches: dict[str, dict[str, Any]] = {}
        self.requests: dict[int, dict[str, Any]] = {}
        self._next_rid = 1

    # -- seeding helpers (test-only) --
    def seed_batch(self, batch_id: str, *, status: str = "draft", **kw: Any) -> dict[str, Any]:
        b = {
            "id": batch_id,
            "name": kw.get("name", "batch"),
            "status": status,
            "delete_enabled": kw.get("delete_enabled", False),
            "options": kw.get("options", {}),
            "requester_id": "root",
            "created_by": "op",
            "note": kw.get("note"),
        }
        self.batches[batch_id] = b
        return b

    def seed_request(self, batch_id: str, **paths: Any) -> int:
        rid = self._next_rid
        self._next_rid += 1
        self.requests[rid] = {
            "id": rid,
            "batch_id": batch_id,
            "state": "registered",
            "src_storage": paths.get("src_storage", "src-st"),
            "src_path": paths.get("src_path", "a"),
            "dst_storage": paths.get("dst_storage", "dst-st"),
            "dst_path": paths.get("dst_path", "b"),
            "preview": None,
            "error": None,
            "dms_job_id": None,
        }
        return rid

    # -- methods the router calls --
    async def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        b = self.batches.get(batch_id)
        return dict(b) if b else None

    async def update_batch(self, batch_id: str, **fields: Any) -> None:
        self.batches[batch_id].update(fields)

    async def get_request(self, request_id: int) -> dict[str, Any] | None:
        r = self.requests.get(request_id)
        return dict(r) if r else None

    async def update_request(self, request_id: int, **fields: Any) -> None:
        self.requests[request_id].update(fields)

    async def delete_request(self, batch_id: str, request_id: int) -> bool:
        r = self.requests.get(request_id)
        if r and r["batch_id"] == batch_id:
            del self.requests[request_id]
            return True
        return False


def make_client(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(backup_router(Settings()))
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_dms_client] = lambda: None
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": "op",
        "role": "operator",
        "method": "local",
    }
    return TestClient(app)


@pytest.fixture
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture
def client(db: FakeDB) -> TestClient:
    return make_client(db)


# --- PATCH /batches/{id} (meta + options) -----------------------------------

def test_patch_batch_updates_name_delete_and_options(client, db):
    db.seed_batch("b1", name="old", delete_enabled=False, options={})
    resp = client.patch(
        f"{BASE}/batches/b1",
        json={"name": "new name", "delete_enabled": True, "options": {"chmod": "0750"}},
    )
    assert resp.status_code == 200, resp.text
    assert db.batches["b1"]["name"] == "new name"
    assert db.batches["b1"]["delete_enabled"] is True
    assert db.batches["b1"]["options"] == {"chmod": "0750"}


def test_patch_batch_partial_leaves_other_fields(client, db):
    db.seed_batch("b1", name="keep", delete_enabled=True, options={"chmod": "0750"})
    resp = client.patch(f"{BASE}/batches/b1", json={"note": "hello"})
    assert resp.status_code == 200, resp.text
    assert db.batches["b1"]["note"] == "hello"
    # untouched fields stay
    assert db.batches["b1"]["name"] == "keep"
    assert db.batches["b1"]["delete_enabled"] is True
    assert db.batches["b1"]["options"] == {"chmod": "0750"}


def test_patch_batch_blank_name_is_422(client, db):
    db.seed_batch("b1")
    resp = client.patch(f"{BASE}/batches/b1", json={"name": "   "})
    assert resp.status_code == 422


def test_patch_batch_no_fields_is_422(client, db):
    db.seed_batch("b1")
    resp = client.patch(f"{BASE}/batches/b1", json={})
    assert resp.status_code == 422


def test_patch_batch_non_draft_is_409(client, db):
    db.seed_batch("b1", status="previewing")
    resp = client.patch(f"{BASE}/batches/b1", json={"name": "x"})
    assert resp.status_code == 409


def test_patch_batch_missing_is_404(client, db):
    resp = client.patch(f"{BASE}/batches/nope", json={"name": "x"})
    assert resp.status_code == 404


# --- PATCH /batches/{id}/requests/{rid} (paths) -----------------------------

def test_patch_request_updates_paths(client, db):
    db.seed_batch("b1")
    rid = db.seed_request("b1", src_path="a", dst_path="b")
    resp = client.patch(
        f"{BASE}/batches/b1/requests/{rid}",
        json={"src_storage": "s2", "src_path": "x/y", "dst_storage": "d2", "dst_path": "z"},
    )
    assert resp.status_code == 200, resp.text
    r = db.requests[rid]
    assert (r["src_storage"], r["src_path"], r["dst_storage"], r["dst_path"]) == (
        "s2",
        "x/y",
        "d2",
        "z",
    )


def test_patch_request_normalizes_leading_trailing_slash(client, db):
    db.seed_batch("b1")
    rid = db.seed_request("b1")
    resp = client.patch(
        f"{BASE}/batches/b1/requests/{rid}",
        json={"src_storage": "s", "src_path": "/a/b/", "dst_storage": "d", "dst_path": "c/"},
    )
    assert resp.status_code == 200, resp.text
    assert db.requests[rid]["src_path"] == "a/b"
    assert db.requests[rid]["dst_path"] == "c"


def test_patch_request_rejects_traversal_422(client, db):
    db.seed_batch("b1")
    rid = db.seed_request("b1")
    resp = client.patch(
        f"{BASE}/batches/b1/requests/{rid}",
        json={"src_storage": "s", "src_path": "../etc", "dst_storage": "d", "dst_path": "z"},
    )
    assert resp.status_code == 422


def test_patch_request_non_draft_409(client, db):
    db.seed_batch("b1", status="running")
    rid = db.seed_request("b1")
    resp = client.patch(
        f"{BASE}/batches/b1/requests/{rid}",
        json={"src_storage": "s", "src_path": "a", "dst_storage": "d", "dst_path": "b"},
    )
    assert resp.status_code == 409


def test_patch_request_wrong_batch_404(client, db):
    db.seed_batch("b1")
    db.seed_batch("b2")
    rid = db.seed_request("b2")  # belongs to b2
    resp = client.patch(
        f"{BASE}/batches/b1/requests/{rid}",
        json={"src_storage": "s", "src_path": "a", "dst_storage": "d", "dst_path": "b"},
    )
    assert resp.status_code == 404


# --- DELETE /batches/{id}/requests/{rid} ------------------------------------

def test_delete_request_removes_row(client, db):
    db.seed_batch("b1")
    rid = db.seed_request("b1")
    resp = client.delete(f"{BASE}/batches/b1/requests/{rid}")
    assert resp.status_code == 200, resp.text
    assert resp.json().get("deleted") is True
    assert rid not in db.requests


def test_delete_request_non_draft_409(client, db):
    db.seed_batch("b1", status="previewing")
    rid = db.seed_request("b1")
    resp = client.delete(f"{BASE}/batches/b1/requests/{rid}")
    assert resp.status_code == 409
    assert rid in db.requests


def test_delete_request_wrong_batch_404(client, db):
    db.seed_batch("b1")
    db.seed_batch("b2")
    rid = db.seed_request("b2")
    resp = client.delete(f"{BASE}/batches/b1/requests/{rid}")
    assert resp.status_code == 404
    assert rid in db.requests
