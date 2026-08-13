"""배치 특권 실행(owner_username) 게이트 — test_api_requests_privileged.py 관례 미러.

단건 제출의 3중 게이트(admin + 기능 플래그 + allowlist)에 세션 인증을 더한 4중이다:
단건은 planner 가 요청의 auth_method 로 세션을 재검증하지만, 배치는 생성 시점의
인증 방식을 batches.auth_method 로 박제해 자식이 물려받으므로 박제 전에 라우트가
세션을 확인해야 토큰 생성 배치가 특권을 실어 나르지 못한다.
"""
from dms.config import Settings


def _client_with(db, **overrides):
    from fastapi.testclient import TestClient
    from dms.api.app import create_app
    base = {"DMS_DATABASE_URL": "unused", "DMS_SHARED_TOKEN": "tok-shared",
            "DMS_ADMIN_TOKEN": "tok-admin", "DMS_SESSION_SECRET": "sess", **overrides}
    settings = Settings.from_env(base)
    return TestClient(create_app(settings, db))


BATCH = {"operation": "scan", "max_concurrency": 1, "options": {}, "note": None,
         "items": [{"storage": "s1", "target": "a"}]}


def _admin_login(client, username="ops"):
    client.post("/api/admin/accounts", json={"username": username, "password": "p"},
                headers={"x-admin-token": "tok-admin"})
    client.post("/api/auth/login", json={"username": username, "password": "p"})


def test_admin_in_allowlist_stores_owner_and_auth(db):
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="ops")
    _admin_login(client)
    r = client.post("/api/admin/batches", json={**BATCH, "owner_username": "victim"})
    assert r.status_code == 202
    b = client.app.state.repos.batches.get(r.json()["batch_id"])
    assert b["owner_username"] == "victim"
    assert b["auth_method"] == "session"


def test_admin_not_in_allowlist_denied(db):
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="someone-else")
    _admin_login(client)
    r = client.post("/api/admin/batches", json={**BATCH, "owner_username": "victim"})
    assert r.status_code == 403
    assert r.json()["detail"] == "privileged_not_authorized"


def test_token_created_batch_denied(db):
    # allowlist 에 shared-token 을 넣어도 토큰 인증이면 거부 -- 세션 인증 조건이
    # 단독으로 게이트를 닫는다(자식이 물려받을 auth_method 가 "session" 이 될 수
    # 없는 생성 경로).
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="shared-token")
    r = client.post("/api/admin/batches", json={**BATCH, "owner_username": "victim"},
                    headers={"Authorization": "Bearer tok-shared"})
    assert r.status_code == 403
    assert r.json()["detail"] == "privileged_not_authorized"


def test_feature_flag_off_denied(db):
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="false",
                          DMS_PRIVILEGED_REQUESTERS="ops")
    _admin_login(client)
    r = client.post("/api/admin/batches", json={**BATCH, "owner_username": "victim"})
    assert r.status_code == 403
    assert r.json()["detail"] == "privileged_not_authorized"


def test_owner_absent_or_self_skips_gate_but_stamps_auth(db):
    # allowlist 밖 admin 이라도 owner 미지정/자기 자신이면 게이트 미발동(단건 관례).
    # auth_method 박제는 특권 여부와 무관하다 -- 생성 시점 사실의 기록.
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="someone-else")
    _admin_login(client)
    r = client.post("/api/admin/batches", json=BATCH)
    assert r.status_code == 202
    b = client.app.state.repos.batches.get(r.json()["batch_id"])
    assert b["owner_username"] is None
    assert b["auth_method"] == "session"
    r2 = client.post("/api/admin/batches", json={**BATCH, "owner_username": "ops"})
    assert r2.status_code == 202
    assert client.app.state.repos.batches.get(
        r2.json()["batch_id"])["owner_username"] == "ops"


def test_token_created_batch_without_owner_stamps_token(db):
    # 토큰 생성 배치(특권 없음)는 auth_method="token" 박제 -- 자식도 token 을
    # 물려받아 planner 의 세션 재검증에서 특권을 얻을 수 없다(fail-closed 연쇄).
    client = _client_with(db)
    r = client.post("/api/admin/batches", json=BATCH,
                    headers={"Authorization": "Bearer tok-shared"})
    assert r.status_code == 202
    assert client.app.state.repos.batches.get(
        r.json()["batch_id"])["auth_method"] == "token"


def test_invalid_owner_username_rejected(db):
    # 형식 검증은 단건(_validated_payload 의 validate_owner_username)과 같은 도메인
    # 함수 재사용 -- 쓰레기 사용자명이 배치 행에 박제되지 않는다.
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="ops")
    _admin_login(client)
    r = client.post("/api/admin/batches",
                    json={**BATCH, "owner_username": "bad name!"})
    assert r.status_code == 422
    assert r.json()["detail"] == "invalid_owner_username"
