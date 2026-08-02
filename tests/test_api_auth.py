def test_healthz_is_public(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_admin_route_requires_auth(client):
    # /api/admin/storages 는 Task 14에서 생기므로 여기서는 보호 확인용 임시 라우트 대신
    # 아직 없는 경로는 401/404 어느 쪽도 될 수 있다 — 인증 자체는 /api/auth/me 로 검증한다.
    assert client.get("/api/auth/me").status_code == 401


def test_shared_token_grants_admin(client):
    r = client.get("/api/auth/me", headers={
        "Authorization": "Bearer tok-shared", "x-dms-actor": "ops-debug"})
    assert r.status_code == 200
    assert r.json() == {"actor": "ops-debug", "role": "admin"}


def test_wrong_token_rejected(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_signup_login_me_logout(client):
    assert client.post("/api/auth/signup", json={
        "username": "alice", "password": "pw1", "email": "alice@corp.example"
    }).status_code == 201
    assert client.post("/api/auth/login", json={
        "username": "alice", "password": "bad"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "alice", "password": "pw1"})
    assert r.json() == {"actor": "alice", "role": "user"}
    assert client.get("/api/auth/me").json()["actor"] == "alice"
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_duplicate_signup_409(client):
    client.post("/api/auth/signup", json={"username": "dup", "password": "x"})
    r = client.post("/api/auth/signup", json={"username": "dup", "password": "x"})
    assert r.status_code == 409
    assert r.json()["detail"] == "account_exists"


def test_admin_account_creation_requires_ops_token(client):
    assert client.post("/api/admin/accounts", json={
        "username": "boss", "password": "pw"}).status_code == 403
    assert client.post("/api/admin/accounts", json={
        "username": "boss", "password": "pw"},
        headers={"x-admin-token": "tok-admin"}).status_code == 201
    r = client.post("/api/auth/login", json={"username": "boss", "password": "pw"})
    assert r.json()["role"] == "admin"


def test_non_ascii_token_is_rejected_not_500(client):
    # httpx(TestClient)는 str 헤더값을 기본 ascii로만 인코딩하므로, 와이어 상에서
    # 실클라이언트가 보낼 수 있는 latin-1 바이트를 직접 넘겨 ASGI 디코딩 경로를 재현한다.
    r = client.get("/api/auth/me",
                    headers={"Authorization": "Bearer caf\xe9".encode("latin-1")})
    assert r.status_code == 401
    r = client.post("/api/admin/accounts", json={"username": "x", "password": "p"},
                    headers={"x-admin-token": "caf\xe9".encode("latin-1")})
    assert r.status_code == 403
