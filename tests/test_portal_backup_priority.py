"""Portal BFF — per-batch scheduling priority (selectable, default Low)."""

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
        self.created: dict[str, Any] | None = None
        self.batches: dict[str, dict[str, Any]] = {}
        self.updated: dict[str, Any] | None = None

    async def create_batch(self, *, batch_id, name, delete_enabled, options,
                           requester_id, created_by, note, priority) -> None:
        self.created = {"batch_id": batch_id, "name": name, "priority": priority,
                        "delete_enabled": delete_enabled, "options": options}
        self.batches[batch_id] = {"id": batch_id, "status": "draft", "name": name,
                                  "priority": priority, "delete_enabled": delete_enabled,
                                  "options": options, "requester_id": "root"}

    async def add_requests(self, batch_id, rows) -> int:
        return len(rows)

    async def get_batch(self, bid) -> dict[str, Any] | None:
        b = self.batches.get(bid)
        return dict(b) if b else None

    async def update_batch(self, bid, **fields) -> None:
        self.updated = fields
        self.batches[bid].update(fields)


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


def test_create_stores_selected_priority(client, db):
    r = client.post(f"{BASE}/batches", json={"name": "b", "priority": "High", "requests": []})
    assert r.status_code == 201, r.text
    assert db.created["priority"] == "High"


def test_create_defaults_priority_low(client, db):
    r = client.post(f"{BASE}/batches", json={"name": "b", "requests": []})
    assert r.status_code == 201, r.text
    assert db.created["priority"] == "Low"


def test_create_rejects_bad_priority(client, db):
    r = client.post(f"{BASE}/batches", json={"name": "b", "priority": "Bogus", "requests": []})
    assert r.status_code == 422


def test_patch_updates_priority(client, db):
    db.batches["b1"] = {"id": "b1", "status": "draft", "priority": "Low"}
    r = client.patch(f"{BASE}/batches/b1", json={"priority": "Mid"})
    assert r.status_code == 200, r.text
    assert db.batches["b1"]["priority"] == "Mid"


def test_patch_rejects_bad_priority(client, db):
    db.batches["b1"] = {"id": "b1", "status": "draft", "priority": "Low"}
    r = client.patch(f"{BASE}/batches/b1", json={"priority": "Bogus"})
    assert r.status_code == 422
