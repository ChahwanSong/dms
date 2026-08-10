def test_healthz_is_public(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_ok(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_admin_route_requires_auth(client):
    # /api/admin/storages 는 Task 14에서 생기므로 여기서는 보호 확인용 임시 라우트 대신
    # 아직 없는 경로는 401/404 어느 쪽도 될 수 있다 — 인증 자체는 /api/auth/me 로 검증한다.
    assert client.get("/api/auth/me").status_code == 401


def test_shared_token_grants_admin(client):
    # 슬라이스 19: x-dms-actor 없이도 토큰만으로 admin 이다. actor 는 shared-token 으로
    # 정규화된다(임의 actor 지정은 이제 400 -- test_actor_spoof_regression 이 고정).
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer tok-shared"})
    assert r.status_code == 200
    assert r.json() == {"actor": "shared-token", "role": "admin"}


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


def test_admin_account_bootstrap_actor_is_in_a_reserved_namespace(client, db):
    # M8: actor="admin-token"을 그대로 쓰면, _USERNAME_RE가 "admin-token"을 유효한
    # 사용자명으로 허용하고(영숫자+하이픈) /api/auth/signup은 무인증이라 누구나 그
    # 이름으로 셀프 가입해 부트스트랩 경로가 남긴 감사 행과 구분 안 되는 행을 만들
    # 수 있다. ':'는 사용자명에 금지돼 있어 "token:admin-token"은 어떤 사용자도
    # 도달할 수 없는 네임스페이스다.
    client.post("/api/admin/accounts", json={"username": "boss", "password": "pw"},
               headers={"x-admin-token": "tok-admin"})
    rows = db.query("SELECT * FROM audit_log WHERE mutation_class = 'account'")
    assert rows[-1]["actor"] == "token:admin-token"


def test_auth_me_returns_raw_actor_for_token_auth(client):
    # /api/auth/me는 감사 로그가 아니라 로그인 신원 표시용이다 -- audit_actor()의
    # token: 접두가 여기 새어 들어가면 프론트 헤더와 기존 단언이 깨진다. 슬라이스 19
    # 이후 기본 토큰 actor 는 shared-token(콜론 없음)이다.
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer tok-shared"})
    assert r.json()["actor"] == "shared-token"
    assert ":" not in r.json()["actor"]


def test_auth_me_default_token_actor_is_raw_shared_token(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer tok-shared"})
    assert r.json()["actor"] == "shared-token"


def test_reserved_prefix_in_actor_header_is_rejected(client):
    # 접두가 서버 소유의 출처 표식이 아니게 되면 의미가 없다. token:alice 는
    # node:<이름> 이 아니라 슬라이스 19 의 더 넓은 actor 게이트에 흡수돼 400 이다 --
    # 접두 위조로 감사 로그를 오염시키던 경로는 여전히 닫혀 있다.
    r = client.get("/api/auth/me", headers={
        "Authorization": "Bearer tok-shared", "x-dms-actor": "token:alice"})
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_actor"


def test_non_ascii_token_is_rejected_not_500(client):
    # httpx(TestClient)는 str 헤더값을 기본 ascii로만 인코딩하므로, 와이어 상에서
    # 실클라이언트가 보낼 수 있는 latin-1 바이트를 직접 넘겨 ASGI 디코딩 경로를 재현한다.
    r = client.get("/api/auth/me",
                    headers={"Authorization": "Bearer caf\xe9".encode("latin-1")})
    assert r.status_code == 401
    r = client.post("/api/admin/accounts", json={"username": "x", "password": "p"},
                    headers={"x-admin-token": "caf\xe9".encode("latin-1")})
    assert r.status_code == 403
