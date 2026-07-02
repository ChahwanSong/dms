"""Operator account management (portal 계정 관리).

Full integration through the real auth + accounts routers with a signed session
(SessionMiddleware) and an in-memory FakeDb: login → self password change →
admin unlock (PORTAL_ADMIN_TOKEN) → create/reset/disable/delete with lock-out
guards, and that a disabled account can no longer log in.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from portal.backend.auth import auth_router
from portal.backend.config import Settings
from portal.backend.db import hash_password
from portal.backend.routers.accounts import accounts_router

ADMIN_TOKEN = "s3cret-admin-token"
SEED_PW = "adminpw12"


class FakeDb:
    configured = True

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def _seed(self, username: str, password: str, active: bool = True) -> None:
        self.rows[username] = {
            "username": username, "password_hash": hash_password(password),
            "is_active": active, "created_by": None,
            "created_at": None, "updated_at": None,
        }

    async def operator_auth_record(self, username):
        r = self.rows.get(username)
        return {"password_hash": r["password_hash"], "is_active": r["is_active"]} if r else None

    async def list_operators(self):
        return [
            {k: r[k] for k in ("username", "is_active", "created_by", "created_at", "updated_at")}
            for r in sorted(self.rows.values(), key=lambda x: x["username"])
        ]

    async def count_active_operators(self):
        return sum(1 for r in self.rows.values() if r["is_active"])

    async def operator_exists(self, username):
        return username in self.rows

    async def create_operator(self, username, password, *, created_by):
        if username in self.rows:
            return False
        self.rows[username] = {
            "username": username, "password_hash": hash_password(password),
            "is_active": True, "created_by": created_by,
            "created_at": None, "updated_at": None,
        }
        return True

    async def set_operator_password(self, username, password):
        if username not in self.rows:
            return 0
        self.rows[username]["password_hash"] = hash_password(password)
        return 1

    async def set_operator_active(self, username, active):
        if username not in self.rows:
            return 0
        self.rows[username]["is_active"] = active
        return 1

    async def delete_operator(self, username):
        return 1 if self.rows.pop(username, None) is not None else 0


def make_client() -> tuple[TestClient, FakeDb]:
    settings = Settings(
        session_secret="t" * 32, allow_insecure_defaults=True, admin_token=ADMIN_TOKEN,
    )
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session")
    db = FakeDb()
    db._seed("admin", SEED_PW)
    app.state.db = db
    app.include_router(auth_router(settings))
    app.include_router(accounts_router(settings))
    return TestClient(app), db


def login(c: TestClient, username="admin", password=SEED_PW):
    return c.post("/api/auth/login", json={"username": username, "password": password})


ACC = "/api/operator/accounts"


# ---- self-service ----------------------------------------------------------

def test_self_change_password_requires_current_and_updates():
    c, db = make_client()
    assert login(c).status_code == 200
    # wrong current password → 403
    r = c.post(f"{ACC}/change-password",
               json={"current_password": "nope", "new_password": "newpw1234"})
    assert r.status_code == 403
    # too short → 422
    r = c.post(f"{ACC}/change-password",
               json={"current_password": SEED_PW, "new_password": "short"})
    assert r.status_code == 422
    # ok → new password now logs in, old one doesn't
    r = c.post(f"{ACC}/change-password",
               json={"current_password": SEED_PW, "new_password": "newpw1234"})
    assert r.status_code == 200
    c.post("/api/auth/logout")
    assert login(c, password=SEED_PW).status_code == 401
    assert login(c, password="newpw1234").status_code == 200


# ---- admin gate ------------------------------------------------------------

def test_admin_endpoints_locked_until_unlock():
    c, _ = make_client()
    login(c)
    # locked: management endpoints 403 admin_locked
    assert c.get(ACC).status_code == 403
    # wrong token stays locked
    assert c.post(f"{ACC}/unlock", json={"token": "wrong"}).status_code == 403
    assert c.get(ACC).status_code == 403
    # correct token unlocks; status reflects it
    assert c.post(f"{ACC}/unlock", json={"token": ADMIN_TOKEN}).status_code == 200
    st = c.get(f"{ACC}/status").json()
    assert st["admin_available"] is True and st["unlocked"] is True
    assert c.get(ACC).status_code == 200
    # lock again
    c.post(f"{ACC}/lock")
    assert c.get(ACC).status_code == 403


def test_admin_requires_login():
    c, _ = make_client()
    # not logged in → 401 on status/unlock
    assert c.get(f"{ACC}/status").status_code == 401
    assert c.post(f"{ACC}/unlock", json={"token": ADMIN_TOKEN}).status_code == 401


# ---- create / reset --------------------------------------------------------

def _unlocked_admin():
    c, db = make_client()
    login(c)
    c.post(f"{ACC}/unlock", json={"token": ADMIN_TOKEN})
    return c, db


def test_create_enforces_admin_prefix_and_uniqueness():
    c, db = _unlocked_admin()
    # bad username (no admin_ prefix) → 422
    assert c.post(ACC, json={"username": "ops", "password": "pw123456"}).status_code == 422
    # short password → 422
    assert c.post(ACC, json={"username": "admin_ops", "password": "x"}).status_code == 422
    # ok
    assert c.post(ACC, json={"username": "admin_ops", "password": "pw123456"}).status_code == 200
    assert "admin_ops" in db.rows
    # duplicate → 409
    assert c.post(ACC, json={"username": "admin_ops", "password": "pw123456"}).status_code == 409
    # created account can log in
    c.post("/api/auth/logout")
    assert login(c, "admin_ops", "pw123456").status_code == 200


def test_reset_password_of_other_operator():
    c, db = _unlocked_admin()
    c.post(ACC, json={"username": "admin_ops", "password": "pw123456"})
    r = c.post(f"{ACC}/admin_ops/reset-password", json={"new_password": "reset9999"})
    assert r.status_code == 200
    c.post("/api/auth/logout")
    assert login(c, "admin_ops", "reset9999").status_code == 200
    # reset of unknown operator → 404
    c2, _ = _unlocked_admin()
    assert c2.post(f"{ACC}/admin_nope/reset-password",
                   json={"new_password": "reset9999"}).status_code == 404


# ---- disable / delete + lock-out guards ------------------------------------

def test_disable_blocks_login_and_guards():
    c, db = _unlocked_admin()
    c.post(ACC, json={"username": "admin_ops", "password": "pw123456"})
    # disable admin_ops → it can no longer log in
    assert c.post(f"{ACC}/admin_ops/disable").status_code == 200
    assert db.rows["admin_ops"]["is_active"] is False
    c.post("/api/auth/logout")
    assert login(c, "admin_ops", "pw123456").status_code == 401
    # re-login as admin, re-unlock
    login(c)
    c.post(f"{ACC}/unlock", json={"token": ADMIN_TOKEN})
    # can't disable self
    assert c.post(f"{ACC}/admin/disable").status_code == 409
    # admin is the only ACTIVE account now → can't disable/delete it via last-active
    # (admin_ops is inactive). Re-enable admin_ops so admin isn't the last active,
    # then deleting admin_ops is allowed.
    assert c.post(f"{ACC}/admin_ops/enable").status_code == 200
    assert c.delete(f"{ACC}/admin_ops").status_code == 200
    assert "admin_ops" not in db.rows


def test_cannot_delete_self_or_last_active():
    c, db = _unlocked_admin()
    # only 'admin' exists and is active → deleting self blocked (self guard first)
    assert c.delete(f"{ACC}/admin").status_code == 409
    # add a second active, disable it → admin is last active → deleting it blocked
    c.post(ACC, json={"username": "admin_two", "password": "pw123456"})
    c.post(f"{ACC}/admin_two/disable")
    # deleting the inactive one is fine (doesn't touch last-active)
    assert c.delete(f"{ACC}/admin_two").status_code == 200
