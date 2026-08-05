ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def _login(client, name="alice", pw="pw"):
    client.post("/api/auth/signup", json={"username": name, "password": pw})
    r = client.post("/api/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200


def test_disabling_kills_an_existing_session(client):
    _login(client)
    assert client.get("/api/auth/me").status_code == 200          # 세션 살아있음
    client.put("/api/admin/accounts/alice/disabled", json={"disabled": True},
               headers=ADMIN)
    r = client.get("/api/auth/me")                                 # 같은 세션으로
    assert r.status_code == 401
    assert r.json()["detail"] == "account_disabled"


def test_role_change_takes_effect_on_the_existing_session(client):
    _login(client)
    # user 는 admin 라우트에 403
    assert client.get("/api/admin/policies").status_code == 403
    client.put("/api/admin/accounts/alice/role", json={"role": "admin"}, headers=ADMIN)
    assert client.get("/api/admin/policies").status_code == 200    # 승격 즉시 반영
    client.put("/api/admin/accounts/alice/role", json={"role": "user"}, headers=ADMIN)
    assert client.get("/api/admin/policies").status_code == 403    # 강등도 즉시


def test_bearer_token_path_is_unaffected(client):
    # 공유 토큰은 계정과 무관하다 — 존재하지 않는 actor 로도 동작한다
    assert client.get("/api/admin/policies", headers=ADMIN).status_code == 200


def test_deleted_account_session_is_rejected(client, db):
    _login(client)
    db.execute("DELETE FROM accounts WHERE username = 'alice'")
    assert client.get("/api/auth/me").status_code == 401
