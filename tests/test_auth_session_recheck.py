from fastapi.testclient import TestClient

ADMIN = {"Authorization": "Bearer tok-shared"}


def _op(client):
    # 계정 변경은 세션 관리자만(accounts_session_required, 2026-10-07) -- 피험자(alice)와 쿠키가 섞이지 않게
    # 같은 앱에 붙은 별도 클라이언트로 운영자 세션을 만든다.
    client.post("/api/admin/accounts", json={"username": "opadm", "password": "pw"},
                headers={"x-admin-token": "tok-admin"})
    op = TestClient(client.app)
    assert op.post("/api/auth/login", json={"username": "opadm", "password": "pw"}).status_code == 200
    return op


def _login(client, name="alice", pw="pw"):
    client.post("/api/auth/signup", json={"username": name, "password": pw})
    r = client.post("/api/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200


def test_disabling_kills_an_existing_session(client):
    _login(client)
    assert client.get("/api/auth/me").status_code == 200          # 세션 살아있음
    assert _op(client).put("/api/admin/accounts/alice/disabled",
                           json={"disabled": True}).status_code == 200
    r = client.get("/api/auth/me")                                 # 같은 세션으로
    assert r.status_code == 401
    assert r.json()["detail"] == "account_disabled"


def test_role_change_takes_effect_on_the_existing_session(client):
    # 예비 admin 을 먼저 둔다 -- 슬라이스 19 의 마지막 활성 관리자 가드가 붙은 뒤로는
    # alice 가 유일 admin 인 상태에서의 강등이 409 로 막혀 이 테스트의 관심사(세션에
    # 역할 변경이 즉시 반영되는가)에 도달하지 못한다. 관리자가 둘이면 강등은 통과한다.
    # (계정 변경은 세션 관리자만 하므로 그 운영자 세션이 예비 admin 을 겸한다.)
    op = _op(client)      # 운영자 admin 이 곧 예비 admin 이다
    _login(client)
    # user 는 admin 라우트에 403
    assert client.get("/api/admin/policies").status_code == 403
    assert op.put("/api/admin/accounts/alice/role", json={"role": "admin"}).status_code == 200
    assert client.get("/api/admin/policies").status_code == 200    # 승격 즉시 반영
    assert op.put("/api/admin/accounts/alice/role", json={"role": "user"}).status_code == 200
    assert client.get("/api/admin/policies").status_code == 403    # 강등도 즉시


def test_bearer_token_path_is_unaffected(client):
    # 공유 토큰은 계정과 무관하다 — 존재하지 않는 actor 로도 동작한다
    assert client.get("/api/admin/policies", headers=ADMIN).status_code == 200


def test_deleted_account_session_is_rejected(client, db):
    _login(client)
    db.execute("DELETE FROM accounts WHERE username = 'alice'")
    assert client.get("/api/auth/me").status_code == 401
