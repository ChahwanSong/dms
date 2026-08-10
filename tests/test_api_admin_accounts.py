ADMIN = {"Authorization": "Bearer tok-shared"}


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
    assert set(resp.json().keys()) == {"username", "role", "email", "disabled", "created_at"}


def test_set_disabled_updates_account(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    resp = client.put("/api/admin/accounts/u1/disabled", json={"disabled": True},
                      headers=ADMIN)
    assert resp.status_code == 200
    assert resp.json()["disabled"] == 1
    assert set(resp.json().keys()) == {"username", "role", "email", "disabled", "created_at"}


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


def test_nonexistent_target_is_404_even_when_name_equals_self_actor(client):
    # 공유 토큰으로 인증하면 identity.actor는 계정 row 없이도 존재하는 이름이다
    # (슬라이스 19 이후 "shared-token"). 그 값이 자기 자신처럼 보일 수 있으므로,
    # 존재하지 않는 계정을 대상으로 하면 self-guard(409)보다 먼저 404가 나야 한다.
    resp = client.put("/api/admin/accounts/ops/role", json={"role": "admin"},
                      headers=ADMIN)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "account_not_found"

    resp2 = client.put("/api/admin/accounts/ops/disabled", json={"disabled": True},
                       headers=ADMIN)
    assert resp2.status_code == 404
    assert resp2.json()["detail"] == "account_not_found"


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


def _mk_admin(client, name):
    # x-admin-token 부트스트랩은 계정을 곧바로 ROLE_ADMIN 으로 만든다
    # (routes_auth.create_admin_account -> accounts.create(..., ROLE_ADMIN)).
    client.post("/api/admin/accounts", json={"username": name, "password": "p"},
                headers={"x-admin-token": "tok-admin"})


def test_delete_account_removes_it_and_audits(client, db):
    client.post("/api/auth/signup", json={"username": "victim", "password": "p"})
    assert client.delete("/api/admin/accounts/victim", headers=ADMIN).status_code == 204
    listed = client.get("/api/admin/accounts", headers=ADMIN).json()
    assert all(r["username"] != "victim" for r in listed)   # 목록에서 사라졌다
    rows = db.query(
        "SELECT * FROM audit_log WHERE mutation_class='account' AND operation='delete'")
    assert len(rows) == 1 and rows[0]["target_key"] == "victim"
    # 토큰 호출이므로 감사 actor 는 token: 접두(감사 표식 회귀 지점).
    assert rows[0]["actor"] == "token:shared-token"


def test_delete_missing_account_404(client):
    r = client.delete("/api/admin/accounts/nope", headers=ADMIN)
    assert r.status_code == 404 and r.json()["detail"] == "account_not_found"


def test_delete_self_forbidden(client):
    # 세션으로 로그인한 관리자가 자기 자신을 삭제하려 하면 409, 상태 불변. self-guard 가
    # 마지막 관리자 가드보다 먼저이므로 selfadm 이 유일 admin 이어도 cannot_delete_self.
    _mk_admin(client, "selfadm")
    _login(client, "selfadm")
    r = client.delete("/api/admin/accounts/selfadm")
    assert r.status_code == 409 and r.json()["detail"] == "cannot_delete_self"
    listed = client.get("/api/admin/accounts", headers=ADMIN).json()
    assert any(row["username"] == "selfadm" for row in listed)


def test_delete_last_active_admin_forbidden(client):
    # 유일한 사람 admin 을 토큰(shared-token, actor 불일치라 self-guard 미발동)으로
    # 삭제 시도 -> 409, 계정 불변.
    _mk_admin(client, "onlyadm")
    r = client.delete("/api/admin/accounts/onlyadm", headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "last_active_admin"
    listed = client.get("/api/admin/accounts", headers=ADMIN).json()
    assert any(row["username"] == "onlyadm" for row in listed)


def test_delete_one_of_two_admins_succeeds(client):
    # 대조: admin 이 둘이면 한 명 삭제는 통과한다(마지막 관리자 가드는 '마지막'만 막는다).
    _mk_admin(client, "adm1")
    _mk_admin(client, "adm2")
    assert client.delete("/api/admin/accounts/adm2", headers=ADMIN).status_code == 204


def test_delete_account_with_active_request_forbidden(client, db):
    # 비종단 요청을 가진 계정 삭제는 409 -- 잡 신원은 plan 시점에 구워져 삭제가
    # 소급되지 않으므로(설계 §1-6) 소유자 없는 잡을 예방한다.
    client.post("/api/auth/signup", json={"username": "busy", "password": "p"})
    db.execute(
        """INSERT INTO requests (request_id, commit_order, operation, requester_id, actor,
               resource_key, priority, payload, state, created_at, updated_at, auth_method)
           VALUES ('rq1', 1, 'scan', 'busy', 'busy', 'k', 'mid', '{}', 'Pending',
               '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z', 'session')""")
    r = client.delete("/api/admin/accounts/busy", headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "account_has_active_requests"
    listed = client.get("/api/admin/accounts", headers=ADMIN).json()
    assert any(row["username"] == "busy" for row in listed)   # busy 여전히 존재


def test_delete_account_with_only_terminal_requests_succeeds(client, db):
    # 대조: 종단 요청만 있으면 가드가 걸리지 않는다 -- 이력은 남고 계정만 사라진다.
    client.post("/api/auth/signup", json={"username": "done", "password": "p"})
    db.execute(
        """INSERT INTO requests (request_id, commit_order, operation, requester_id, actor,
               resource_key, priority, payload, state, created_at, updated_at, auth_method)
           VALUES ('rq2', 2, 'scan', 'done', 'done', 'k2', 'mid', '{}', 'Succeeded',
               '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z', 'session')""")
    assert client.delete("/api/admin/accounts/done", headers=ADMIN).status_code == 204
    # 이력 보존: 요청 행의 requester_id 문자열은 남는다(설계 §2.3, FK 0건).
    assert db.query_one("SELECT requester_id FROM requests WHERE request_id='rq2'"
                        )["requester_id"] == "done"
