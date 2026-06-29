"""Portal BFF — bulk batch delete / cancel (backup + scan).

These hit the actual routers via TestClient against an in-memory FakeDB/FakeDms
(mirroring tests/test_portal_backup_item_mgmt.py). They assert the skip-and-report
contract:
  - bulk delete -> {deleted:[id], skipped:[{id,reason}]} where reason is 'active'
    (batch in flight) or 'not_found' (no such id) — NEVER a 409.
  - bulk cancel -> {cancelled:[id], dms_cancelled:int, skipped:[{id,reason}]}; it
    reuses the single-cancel logic per batch and best-effort cancels live DMS jobs.

The FakeDB mirrors the real SQL semantics (delete skips in-flight; cancel skips
terminal requests and returns the in-flight dms_job_ids).
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

# active = the in-flight set the single delete blocks on (mirrors the routers).
BACKUP_ACTIVE = {"previewing", "running"}
SCAN_ACTIVE = {"scanning"}
BACKUP_TERMINAL = {"succeeded", "failed", "cancelled", "preview_failed"}
SCAN_TERMINAL = {"succeeded", "failed", "cancelled"}


class FakeDB:
    configured = True

    def __init__(self) -> None:
        self.bb: dict[str, dict[str, Any]] = {}  # backup batches
        self.sb: dict[str, dict[str, Any]] = {}  # scan batches
        self.br: list[dict[str, Any]] = []  # backup requests
        self.sr: list[dict[str, Any]] = []  # scan requests
        self._n = 1

    # --- seeding helpers ---
    def seed_backup(self, bid: str, status: str) -> None:
        self.bb[bid] = {"id": bid, "status": status}

    def seed_scan(self, bid: str, status: str) -> None:
        self.sb[bid] = {"id": bid, "status": status}

    def add_backup_req(self, bid: str, state: str, dms_job_id: str | None = None) -> int:
        rid = self._n
        self._n += 1
        self.br.append({"id": rid, "batch_id": bid, "state": state, "dms_job_id": dms_job_id})
        return rid

    def add_scan_req(self, bid: str, state: str, dms_job_id: str | None = None) -> int:
        rid = self._n
        self._n += 1
        self.sr.append({"id": rid, "batch_id": bid, "state": state, "dms_job_id": dms_job_id})
        return rid

    # --- backup bulk delete ---
    async def batch_statuses(self, ids: list[str]) -> dict[str, str]:
        return {bid: b["status"] for bid, b in self.bb.items() if bid in ids}

    async def delete_batches(self, ids: list[str]) -> list[str]:
        idset = set(ids)
        deleted: list[str] = []
        for bid in list(self.bb):
            if bid in idset and self.bb[bid]["status"] not in BACKUP_ACTIVE:
                del self.bb[bid]
                self.br[:] = [r for r in self.br if r["batch_id"] != bid]
                deleted.append(bid)
        return deleted

    # --- backup bulk cancel ---
    async def get_batch(self, bid: str) -> dict[str, Any] | None:
        b = self.bb.get(bid)
        return dict(b) if b else None

    async def set_batch_status(self, bid: str, status: str) -> None:
        self.bb[bid]["status"] = status

    async def cancel_requests(self, bid: str, request_ids: list[int] | None = None) -> list[str]:
        out: list[str] = []
        for r in self.br:
            if r["batch_id"] != bid:
                continue
            if request_ids is not None and r["id"] not in request_ids:
                continue
            if r["state"] in BACKUP_TERMINAL:
                continue
            r["state"] = "cancelled"
            if r.get("dms_job_id"):
                out.append(r["dms_job_id"])
        return out

    # --- scan bulk delete ---
    async def scan_batch_statuses(self, ids: list[str]) -> dict[str, str]:
        return {bid: b["status"] for bid, b in self.sb.items() if bid in ids}

    async def delete_scan_batches(self, ids: list[str]) -> list[str]:
        idset = set(ids)
        deleted: list[str] = []
        for bid in list(self.sb):
            if bid in idset and self.sb[bid]["status"] not in SCAN_ACTIVE:
                del self.sb[bid]
                self.sr[:] = [r for r in self.sr if r["batch_id"] != bid]
                deleted.append(bid)
        return deleted

    # --- scan bulk cancel ---
    async def get_scan_batch(self, bid: str) -> dict[str, Any] | None:
        b = self.sb.get(bid)
        return dict(b) if b else None

    async def set_scan_batch_status(self, bid: str, status: str) -> None:
        self.sb[bid]["status"] = status

    async def cancel_scan_requests(self, bid: str, request_ids: list[int] | None = None) -> list[str]:
        out: list[str] = []
        for r in self.sr:
            if r["batch_id"] != bid:
                continue
            if request_ids is not None and r["id"] not in request_ids:
                continue
            if r["state"] in SCAN_TERMINAL:
                continue
            r["state"] = "cancelled"
            if r.get("dms_job_id"):
                out.append(r["dms_job_id"])
        return out


class FakeDms:
    def __init__(self, fail: list[str] | None = None) -> None:
        self.cancelled: list[str] = []
        self.fail = set(fail or [])

    async def cancel_job(self, job_id: str, *, actor: str) -> dict[str, Any]:
        if job_id in self.fail:
            raise DmsApiError(status_code=409, detail="terminal")
        self.cancelled.append(job_id)
        return {}


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


@pytest.fixture
def dms() -> FakeDms:
    return FakeDms()


@pytest.fixture
def client(db: FakeDB, dms: FakeDms) -> TestClient:
    return make_client(db, dms)


def _skipped_map(payload: dict[str, Any]) -> dict[str, str]:
    return {s["id"]: s["reason"] for s in payload["skipped"]}


# --- backup bulk delete -----------------------------------------------------


def test_backup_bulk_delete_partitions(client, db):
    db.seed_backup("ok", "previewed")
    db.seed_backup("active", "running")
    # "missing" is intentionally not seeded.
    r = client.post(f"{BACKUP}/batches:delete", json={"batch_ids": ["ok", "active", "missing"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == ["ok"]
    assert _skipped_map(body) == {"active": "active", "missing": "not_found"}
    # only the deletable batch is gone; the active one survives.
    assert "ok" not in db.bb and "active" in db.bb


def test_backup_bulk_delete_previewing_is_active(client, db):
    db.seed_backup("p", "previewing")
    r = client.post(f"{BACKUP}/batches:delete", json={"batch_ids": ["p"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == []
    assert _skipped_map(body) == {"p": "active"}


def test_backup_bulk_delete_empty_422(client):
    r = client.post(f"{BACKUP}/batches:delete", json={"batch_ids": []})
    assert r.status_code == 422


def test_backup_bulk_delete_distinct_from_single_routes(client, db):
    """POST /batches:delete must not be swallowed by GET/DELETE /batches/{id}; a
    200 with the bulk shape proves the bulk handler ran (no route collision)."""
    db.seed_backup("ok", "done")
    r = client.post(f"{BACKUP}/batches:delete", json={"batch_ids": ["ok"]})
    assert r.status_code == 200
    assert set(r.json()) == {"deleted", "skipped"}


# --- backup bulk cancel -----------------------------------------------------


def test_backup_bulk_cancel(client, db, dms):
    db.seed_backup("b1", "running")
    db.add_backup_req("b1", "running", dms_job_id="j1")
    db.add_backup_req("b1", "succeeded", dms_job_id="jX")  # terminal: untouched
    db.seed_backup("b2", "running")
    db.add_backup_req("b2", "approved", dms_job_id="j2")
    r = client.post(f"{BACKUP}/batches:cancel", json={"batch_ids": ["b1", "b2", "missing"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cancelled"] == ["b1", "b2"]
    assert _skipped_map(body) == {"missing": "not_found"}
    assert body["dms_cancelled"] == 2
    assert sorted(dms.cancelled) == ["j1", "j2"]  # jX (terminal) not cancelled
    assert db.bb["b1"]["status"] == "cancelled" and db.bb["b2"]["status"] == "cancelled"


def test_backup_bulk_cancel_best_effort_on_dms_error(db):
    """A DMS cancel failure is swallowed (best-effort): the request is still marked
    cancelled locally and only successful DMS cancels are counted."""
    dms = FakeDms(fail=["j2"])
    client = make_client(db, dms)
    db.seed_backup("b1", "running")
    db.add_backup_req("b1", "running", dms_job_id="j1")
    db.add_backup_req("b1", "running", dms_job_id="j2")
    r = client.post(f"{BACKUP}/batches:cancel", json={"batch_ids": ["b1"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cancelled"] == ["b1"]
    assert body["dms_cancelled"] == 1  # j2 raised, not counted
    assert dms.cancelled == ["j1"]
    assert all(rq["state"] == "cancelled" for rq in db.br)


def test_backup_bulk_cancel_empty_422(client):
    r = client.post(f"{BACKUP}/batches:cancel", json={"batch_ids": []})
    assert r.status_code == 422


# --- scan bulk delete -------------------------------------------------------


def test_scan_bulk_delete_partitions(client, db):
    db.seed_scan("ok", "done")
    db.seed_scan("active", "scanning")
    r = client.post(f"{SCAN}/batches:delete", json={"batch_ids": ["ok", "active", "missing"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == ["ok"]
    assert _skipped_map(body) == {"active": "active", "missing": "not_found"}
    assert "ok" not in db.sb and "active" in db.sb


def test_scan_bulk_delete_empty_422(client):
    r = client.post(f"{SCAN}/batches:delete", json={"batch_ids": []})
    assert r.status_code == 422


# --- scan bulk cancel -------------------------------------------------------


def test_scan_bulk_cancel(client, db, dms):
    db.seed_scan("s1", "scanning")
    db.add_scan_req("s1", "running", dms_job_id="sj1")
    db.add_scan_req("s1", "succeeded", dms_job_id="sjX")  # terminal: untouched
    r = client.post(f"{SCAN}/batches:cancel", json={"batch_ids": ["s1", "missing"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cancelled"] == ["s1"]
    assert _skipped_map(body) == {"missing": "not_found"}
    assert body["dms_cancelled"] == 1
    assert dms.cancelled == ["sj1"]
    assert db.sb["s1"]["status"] == "cancelled"


def test_scan_bulk_cancel_empty_422(client):
    r = client.post(f"{SCAN}/batches:cancel", json={"batch_ids": []})
    assert r.status_code == 422
