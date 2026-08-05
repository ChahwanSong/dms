import pytest
from dms.config import Settings


def _client_with(db, **overrides):
    from fastapi.testclient import TestClient
    from dms.api.app import create_app
    base = {"DMS_DATABASE_URL": "unused", "DMS_SHARED_TOKEN": "tok-shared",
            "DMS_ADMIN_TOKEN": "tok-admin", "DMS_SESSION_SECRET": "sess", **overrides}
    settings = Settings.from_env(base)
    return TestClient(create_app(settings, db))


SCAN = {"operation": "scan", "storage": "s1", "target": "a"}
# scan은 관리자 전용 제출이므로(별도 게이트), non-admin으로 특권 게이트만 단독 검증하는
# 아래 테스트들은 scan 대신 rm을 사용한다.
RM = {"operation": "rm", "storage": "s1", "target": "a", "options": {"recursive": True}}


def test_owner_self_is_allowed(db):
    client = _client_with(db)
    client.post("/api/auth/signup", json={"username": "alice", "password": "p"})
    client.post("/api/auth/login", json={"username": "alice", "password": "p"})
    # owner_username 없음 → 자기 데이터 → 202
    assert client.post("/api/user/requests", json=RM).status_code == 202
    # owner_username == 자신 → 202
    assert client.post("/api/user/requests",
                       json={**RM, "owner_username": "alice"}).status_code == 202


def test_user_cannot_submit_for_other_owner(db):
    client = _client_with(db)
    client.post("/api/auth/signup", json={"username": "mallory", "password": "p"})
    client.post("/api/auth/login", json={"username": "mallory", "password": "p"})
    r = client.post("/api/user/requests", json={**RM, "owner_username": "victim"})
    assert r.status_code == 403 and r.json()["detail"] == "privileged_not_authorized"


def test_admin_operator_with_flag_can_submit_for_other(db):
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="ops")
    # 관리자 계정 생성(운영 토큰) + 로그인
    client.post("/api/admin/accounts", json={"username": "ops", "password": "p"},
                headers={"x-admin-token": "tok-admin"})
    client.post("/api/auth/login", json={"username": "ops", "password": "p"})
    r = client.post("/api/user/requests", json={**SCAN, "owner_username": "victim"})
    assert r.status_code == 202


def test_admin_not_in_allowlist_denied(db):
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="someone-else")
    client.post("/api/admin/accounts", json={"username": "ops", "password": "p"},
                headers={"x-admin-token": "tok-admin"})
    client.post("/api/auth/login", json={"username": "ops", "password": "p"})
    r = client.post("/api/user/requests", json={**SCAN, "owner_username": "victim"})
    assert r.status_code == 403
