"""배치 생성 특권 게이트 통일 — 모든 배치 생성이 특권 3중 게이트를 통과해야 한다.

배치 메뉴는 관리자 운영 메뉴고 사용자 결정은 "배치(scan/sync)는 전부 기본 관리자
특권(root) 실행"이다. 이전 계약(owner_username 이 있을 때만 게이트)은 allowlist 밖
admin·토큰 생성 배치를 조용히 비특권으로 강등시켰고, LDAP 밖 계정이면 자식이
ldap_identity_not_found 로 죽었다 — 강등 대신 생성 시점 403 으로 명시 거부한다.

게이트는 3중: 기능 플래그(allow_privileged_requesters) + allowlist
(privileged_requesters) + 세션 인증. admin 역할은 require_admin 이 이미 보장한다.
세션 조건이 필요한 이유: 배치는 생성 시점 인증 방식을 batches.auth_method 로 박제해
자식이 물려받으므로 박제 전에 라우트가 끊어야 토큰 생성 배치가 특권을 실어 나르지
못한다(단건은 planner 가 요청 auth_method 로 재검증).
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
    # from_env 앱은 라이브 자세(비밀번호 봉인 필수, 2026-09-07)라 로그인은 봉인해
    # 보낸다 -- 토큰 부트스트랩은 평문 허용.
    from dms.api.password_transport import seal_with_info
    client.post("/api/admin/accounts", json={"username": username, "password": "p"},
                headers={"x-admin-token": "tok-admin"})
    info = client.get("/api/auth/transport-key").json()
    r = client.post("/api/auth/login", json={
        "username": username,
        "password_enc": seal_with_info(info, "p", purpose="login", username=username)})
    assert r.status_code == 200, r.text


def test_allowlist_session_admin_without_owner_accepted(db):
    # 통과 경로(owner 미지정): 소유자 기록은 선택이다 — NULL(기본: 생성자).
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="ops")
    _admin_login(client)
    r = client.post("/api/admin/batches", json=BATCH)
    assert r.status_code == 202
    b = client.app.state.repos.batches.get(r.json()["batch_id"])
    assert b["owner_username"] is None
    assert b["auth_method"] == "session"


def test_allowlist_session_admin_with_owner_stores_owner_and_auth(db):
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="ops")
    _admin_login(client)
    r = client.post("/api/admin/batches", json={**BATCH, "owner_username": "victim"})
    assert r.status_code == 202
    b = client.app.state.repos.batches.get(r.json()["batch_id"])
    assert b["owner_username"] == "victim"
    assert b["auth_method"] == "session"


def test_admin_not_in_allowlist_denied_even_without_owner(db):
    # 통일 게이트의 새 계약: owner 미지정이어도 allowlist 밖 admin 은 403 —
    # 이전의 "조용한 비특권 202"를 명시 거부로 대체한다(의도된 계약 변경).
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="someone-else")
    _admin_login(client)
    r = client.post("/api/admin/batches", json=BATCH)
    assert r.status_code == 403
    assert r.json()["detail"] == "privileged_not_authorized"


def test_admin_not_in_allowlist_denied_with_owner(db):
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="someone-else")
    _admin_login(client)
    r = client.post("/api/admin/batches", json={**BATCH, "owner_username": "victim"})
    assert r.status_code == 403
    assert r.json()["detail"] == "privileged_not_authorized"


def test_token_created_batch_denied(db):
    # allowlist 에 shared-token 을 넣어도 토큰 인증이면 거부 -- 세션 인증 조건이
    # 단독으로 게이트를 닫는다. owner 유무와 무관하다(통일 게이트): 토큰 생성
    # 배치는 이제 아예 만들어지지 않으므로 auth_method="token" 신규 행도 없다
    # (구형 행의 token/NULL 폴백은 orchestrator 상속이 그대로 다룬다).
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="shared-token")
    r = client.post("/api/admin/batches", json=BATCH,
                    headers={"Authorization": "Bearer tok-shared"})
    assert r.status_code == 403
    assert r.json()["detail"] == "privileged_not_authorized"


def test_feature_flag_off_denied(db):
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="false",
                          DMS_PRIVILEGED_REQUESTERS="ops")
    _admin_login(client)
    r = client.post("/api/admin/batches", json=BATCH)
    assert r.status_code == 403
    assert r.json()["detail"] == "privileged_not_authorized"


def test_invalid_owner_username_rejected(db):
    # 형식 검증은 단건(_validated_payload 의 validate_owner_username)과 같은 도메인
    # 함수 재사용 -- 쓰레기 사용자명이 배치 행에 박제되지 않는다. 게이트(403)가
    # 검증(422)보다 앞선다 — 이 테스트는 게이트를 통과하는 클라이언트로 검증만 본다.
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="ops")
    _admin_login(client)
    r = client.post("/api/admin/batches",
                    json={**BATCH, "owner_username": "bad name!"})
    assert r.status_code == 422
    assert r.json()["detail"] == "invalid_owner_username"
