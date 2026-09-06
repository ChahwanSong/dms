import pytest
from dms.config import Settings


def _client_with(db, **overrides):
    from fastapi.testclient import TestClient
    from dms.api.app import create_app
    base = {"DMS_DATABASE_URL": "unused", "DMS_SHARED_TOKEN": "tok-shared",
            "DMS_ADMIN_TOKEN": "tok-admin", "DMS_SESSION_SECRET": "sess",
            # from_env 는 인증번호 게이트가 기본 켜짐(운영 fail-closed) -- 이
            # 파일의 무인증 signup 픽스처를 위해 명시로 끈다(conftest 관례).
            "DMS_ACCOUNT_VERIFICATION_REQUIRED": "false",
            # 같은 이유로 비밀번호 전송 봉인(from_env 기본 필수, 2026-09-07)도 끈다 --
            # 봉인 자체는 test_api_auth_hardening 이 라이브 자세로 검증한다.
            "DMS_PASSWORD_ENCRYPTION_REQUIRED": "false",
            # 이 파일의 관심사는 **특권/owner 게이트**이지 사용자 연산 allowlist 가
            # 아니다 -- rm/scan 을 사용자로 제출해 owner 게이트를 검증하려면
            # allowlist 를 넓혀 그 게이트가 먼저 가로채지 않게 한다(2026-08-22).
            "DMS_USER_ALLOWED_OPERATIONS": "sync,scan,rm", **overrides}
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
