"""Login-screen operator account flows (계정 만들기 / 비밀번호 재설정).

These are PUBLIC (pre-auth) endpoints gated ONLY by the shared operational secret
token (PORTAL_ADMIN_TOKEN), entered inline on the login screen — NOT a login
session. Exercised through the real auth router with an in-memory FakeDb.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from portal.backend.auth import auth_router
from portal.backend.config import Settings
from portal.backend.db import hash_password, verify_password

TOKEN = "op-secret-token"
SEED_PW = "adminpw12"


class FakeDb:
    configured = True

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def _seed(self, username: str, password: str) -> None:
        self.rows[username] = {
            "username": username, "password_hash": hash_password(password),
            "is_active": True, "created_by": None,
        }

    async def operator_auth_record(self, username):
        r = self.rows.get(username)
        return {"password_hash": r["password_hash"], "is_active": r["is_active"]} if r else None

    async def create_operator(self, username, password, *, created_by):
        if username in self.rows:
            return False
        self.rows[username] = {
            "username": username, "password_hash": hash_password(password),
            "is_active": True, "created_by": created_by,
        }
        return True

    async def set_operator_password(self, username, password):
        if username not in self.rows:
            return 0
        self.rows[username]["password_hash"] = hash_password(password)
        return 1


def make_client(token: str | None = TOKEN) -> tuple[TestClient, FakeDb]:
    settings = Settings(
        session_secret="t" * 32, allow_insecure_defaults=True, admin_token=token,
    )
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session")
    db = FakeDb()
    db._seed("admin", SEED_PW)
    app.state.db = db
    app.include_router(auth_router(settings))
    return TestClient(app), db


AUTH = "/api/auth"


def login(c, username, password):
    return c.post(f"{AUTH}/login", json={"username": username, "password": password})


# ---- feature availability --------------------------------------------------

def test_account_token_required_reflects_config():
    c, _ = make_client(TOKEN)
    assert c.get(f"{AUTH}/account-token-required").json() == {"available": True}
    c2, _ = make_client(None)
    assert c2.get(f"{AUTH}/account-token-required").json() == {"available": False}


# ---- register (계정 만들기) -------------------------------------------------

def test_register_requires_correct_token():
    c, db = make_client()
    # wrong token → 403, no account created
    r = c.post(f"{AUTH}/register",
               json={"username": "admin_ops", "password": "pw123456", "token": "nope"})
    assert r.status_code == 403
    assert "admin_ops" not in db.rows
    # correct token → created + can log in
    r = c.post(f"{AUTH}/register",
               json={"username": "admin_ops", "password": "pw123456", "token": TOKEN})
    assert r.status_code == 200 and r.json() == {"registered": "admin_ops"}
    assert login(c, "admin_ops", "pw123456").status_code == 200


def test_register_enforces_prefix_password_and_uniqueness():
    c, _ = make_client()
    # no admin_ prefix → 422
    assert c.post(f"{AUTH}/register",
                  json={"username": "ops", "password": "pw123456", "token": TOKEN}).status_code == 422
    # short password → 422
    assert c.post(f"{AUTH}/register",
                  json={"username": "admin_ops", "password": "x", "token": TOKEN}).status_code == 422
    # ok, then duplicate → 409
    assert c.post(f"{AUTH}/register",
                  json={"username": "admin_ops", "password": "pw123456", "token": TOKEN}).status_code == 200
    assert c.post(f"{AUTH}/register",
                  json={"username": "admin_ops", "password": "pw123456", "token": TOKEN}).status_code == 409


def test_register_disabled_without_token_config():
    c, _ = make_client(None)  # PORTAL_ADMIN_TOKEN unset
    r = c.post(f"{AUTH}/register",
               json={"username": "admin_ops", "password": "pw123456", "token": "anything"})
    assert r.status_code == 503


# ---- reset password (비밀번호 재설정) --------------------------------------

def test_reset_password_with_token_changes_login():
    c, _ = make_client()
    # wrong token → 403
    assert c.post(f"{AUTH}/reset-password",
                  json={"username": "admin", "new_password": "newpw1234", "token": "nope"}).status_code == 403
    # correct token → old pw stops working, new pw works
    r = c.post(f"{AUTH}/reset-password",
               json={"username": "admin", "new_password": "newpw1234", "token": TOKEN})
    assert r.status_code == 200 and r.json() == {"reset": "admin"}
    assert login(c, "admin", SEED_PW).status_code == 401
    assert login(c, "admin", "newpw1234").status_code == 200


def test_reset_unknown_user_404_and_short_password_422():
    c, _ = make_client()
    assert c.post(f"{AUTH}/reset-password",
                  json={"username": "admin", "new_password": "x", "token": TOKEN}).status_code == 422
    assert c.post(f"{AUTH}/reset-password",
                  json={"username": "admin_nope", "new_password": "newpw1234", "token": TOKEN}).status_code == 404


# ---- disabled account still can't log in (defensive) -----------------------

def test_disabled_account_rejected_on_login():
    c, db = make_client()
    db._seed("admin_off", "pw123456")
    db.rows["admin_off"]["is_active"] = False
    assert login(c, "admin_off", "pw123456").status_code == 401
    # sanity: the hash itself is valid (rejection is purely the is_active gate)
    assert verify_password("pw123456", db.rows["admin_off"]["password_hash"])
