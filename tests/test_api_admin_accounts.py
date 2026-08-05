ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def _login(client, username, password="p"):
    client.post("/api/auth/login", json={"username": username, "password": password})


def test_accounts_require_admin(client):
    assert client.get("/api/admin/accounts").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/accounts").status_code == 403


def test_list_accounts_excludes_password_hash(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    resp = client.get("/api/admin/accounts", headers=ADMIN)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    for row in rows:
        assert set(row.keys()) == {"username", "role", "email", "disabled", "created_at"}


def test_set_role_updates_account(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    resp = client.put("/api/admin/accounts/u1/role", json={"role": "admin"},
                      headers=ADMIN)
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_set_disabled_updates_account(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    resp = client.put("/api/admin/accounts/u1/disabled", json={"disabled": True},
                      headers=ADMIN)
    assert resp.status_code == 200
    assert resp.json()["disabled"] == 1


def test_set_role_missing_account_404(client):
    resp = client.put("/api/admin/accounts/nope/role", json={"role": "admin"},
                      headers=ADMIN)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "account_not_found"


def test_set_disabled_missing_account_404(client):
    resp = client.put("/api/admin/accounts/nope/disabled", json={"disabled": True},
                      headers=ADMIN)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "account_not_found"


def test_set_role_invalid_role_422(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    resp = client.put("/api/admin/accounts/u1/role", json={"role": "superadmin"},
                      headers=ADMIN)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "invalid_role"


def test_self_lock_role_forbidden(client):
    # 세션으로 로그인한 관리자가 자기 자신을 강등하려 하면 409, 상태 불변.
    client.post("/api/auth/signup", json={"username": "selfadmin", "password": "p"})
    client.put("/api/admin/accounts/selfadmin/role", json={"role": "admin"},
              headers=ADMIN)
    _login(client, "selfadmin")  # 세션에 role=admin이 실림

    resp = client.put("/api/admin/accounts/selfadmin/role", json={"role": "user"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "cannot_lock_self"

    # 상태 불변 확인 (Bearer로 조회)
    listed = client.get("/api/admin/accounts", headers=ADMIN).json()
    row = next(r for r in listed if r["username"] == "selfadmin")
    assert row["role"] == "admin"


def test_self_lock_disabled_forbidden(client):
    client.post("/api/auth/signup", json={"username": "selfadmin2", "password": "p"})
    client.put("/api/admin/accounts/selfadmin2/role", json={"role": "admin"},
              headers=ADMIN)
    _login(client, "selfadmin2")

    resp = client.put("/api/admin/accounts/selfadmin2/disabled", json={"disabled": True})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "cannot_lock_self"

    listed = client.get("/api/admin/accounts", headers=ADMIN).json()
    row = next(r for r in listed if r["username"] == "selfadmin2")
    assert row["disabled"] == 0
