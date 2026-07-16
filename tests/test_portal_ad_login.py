"""Portal 회사-AD(end-user) 로그인 — 현재는 더미 스텁(authenticate_ad).

임시 더미 경로: 아무 아이디로 로그인 성공(role=user, method=ad, dummy=true), 아이디를
비우면 'ad-user'. PORTAL_ALLOW_DUMMY_AD=false면 더미 경로가 막혀 401(fail-closed) —
실제 AD 미구현 상태에서 더미를 끄면 사용자 로그인이 차단됨을 보장.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from portal.backend.auth import auth_router
from portal.backend.config import Settings


def _client(allow_dummy_ad: bool = True) -> TestClient:
    s = Settings(
        session_secret="t" * 32,
        allow_insecure_defaults=True,
        allow_dummy_ad=allow_dummy_ad,
    )
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session")
    app.include_router(auth_router(s))
    return TestClient(app)


def test_dummy_ad_empty_body_logs_in_as_ad_user():
    r = _client().post("/api/auth/login/ad", json={})
    assert r.status_code == 200
    u = r.json()["user"]
    assert u == {"username": "ad-user", "role": "user", "method": "ad", "dummy": True}


def test_dummy_ad_uses_supplied_username():
    r = _client().post("/api/auth/login/ad", json={"username": "alice"})
    assert r.status_code == 200
    u = r.json()["user"]
    assert u["username"] == "alice" and u["role"] == "user" and u["method"] == "ad"


def test_dummy_ad_null_username_falls_back():
    r = _client().post("/api/auth/login/ad", json={"username": None})
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "ad-user"


def test_dummy_ad_disabled_returns_401():
    # 실제 AD 미구현 + 더미 OFF → 로그인 불가(fail-closed).
    r = _client(allow_dummy_ad=False).post("/api/auth/login/ad", json={"username": "bob"})
    assert r.status_code == 401
    assert r.json()["detail"] == "ad_auth_failed"
