from fastapi.testclient import TestClient

ADMIN = {"Authorization": "Bearer tok-shared"}
OP = "opadm"


def _op(client):
    # 계정 생성·삭제·역할·비활성화는 세션 관리자만(accounts_session_required, 2026-10-07 -- 공유 토큰으로
    # allowlist 이름의 계정을 지우고 다시 만들면 배치 특권 게이트를 통과했다). 피험자 세션과 쿠키가 섞이지 않게
    # 같은 앱에 붙은 별도 클라이언트로 운영자 admin 세션을 만든다(x-admin-token 부트스트랩 = ROLE_ADMIN).
    if client.app.state.repos.accounts.get(OP) is None:
        _mk_admin(client, OP)
    op = TestClient(client.app)
    assert op.post("/api/auth/login", json={"username": OP, "password": "p"}).status_code == 200
    return op


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
    resp = _op(client).put("/api/admin/accounts/u1/role", json={"role": "admin"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"
    assert set(resp.json().keys()) == {"username", "role", "email", "disabled", "created_at"}


def test_set_disabled_updates_account(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    resp = _op(client).put("/api/admin/accounts/u1/disabled", json={"disabled": True})
    assert resp.status_code == 200
    assert resp.json()["disabled"] == 1
    assert set(resp.json().keys()) == {"username", "role", "email", "disabled", "created_at"}


def test_set_role_missing_account_404(client):
    resp = _op(client).put("/api/admin/accounts/nope/role", json={"role": "admin"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "account_not_found"


def test_set_disabled_missing_account_404(client):
    resp = _op(client).put("/api/admin/accounts/nope/disabled", json={"disabled": True})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "account_not_found"


def test_set_role_invalid_role_422(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    resp = _op(client).put("/api/admin/accounts/u1/role", json={"role": "superadmin"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "invalid_role"


def test_self_lock_role_forbidden(client):
    # 세션으로 로그인한 관리자가 자기 자신을 강등하려 하면 409, 상태 불변.
    _mk_admin(client, "selfadmin")
    _login(client, "selfadmin")  # 세션에 role=admin이 실림

    resp = client.put("/api/admin/accounts/selfadmin/role", json={"role": "user"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "cannot_lock_self"

    # 상태 불변 확인 (Bearer로 조회)
    listed = client.get("/api/admin/accounts", headers=ADMIN).json()
    row = next(r for r in listed if r["username"] == "selfadmin")
    assert row["role"] == "admin"


def test_token_cannot_mutate_accounts(client, db):
    # 공유 토큰(노드 에이전트 전부가 가진다)은 계정을 만들거나 바꾸거나 지울 수 없다 -- 세션 관리자만.
    # 그렇지 않으면 특권 allowlist 이름(기본 admin/root)의 계정을 지우고 토큰 보유자가 고른 비밀번호로 다시
    # 만들어 그 세션으로 배치 특권 게이트·root 실행 자격을 얻는다. 존재하지 않는 대상도 403 이다(404 보다
    # 세션 판정이 먼저라 토큰으로 계정 존재 여부를 캐지 못한다). 조회는 토큰도 된다.
    _mk_admin(client, "admin")
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    snapshot = "SELECT username, role, disabled, password_hash FROM accounts ORDER BY username"
    before = db.query(snapshot)
    calls = [
        client.post("/api/admin/accounts", json={"username": "root", "password": "x", "role": "admin"},
                    headers=ADMIN),
        client.put("/api/admin/accounts/u1/role", json={"role": "admin"}, headers=ADMIN),
        client.put("/api/admin/accounts/admin/disabled", json={"disabled": True}, headers=ADMIN),
        client.delete("/api/admin/accounts/admin", headers=ADMIN),
        client.put("/api/admin/accounts/nope/role", json={"role": "admin"}, headers=ADMIN),
        client.delete("/api/admin/accounts/nope", headers=ADMIN),
    ]
    for r in calls:
        assert r.status_code == 403 and r.json()["detail"] == "accounts_session_required"
    assert db.query(snapshot) == before
    assert client.get("/api/admin/accounts", headers=ADMIN).status_code == 200
    # 첫 관리자 부트스트랩(별도 비밀 x-admin-token)은 그대로 된다.
    assert client.post("/api/admin/accounts", json={"username": "boot2", "password": "p"},
                       headers={"x-admin-token": "tok-admin"}).status_code == 201


def test_self_lock_disabled_forbidden(client):
    _mk_admin(client, "selfadmin2")
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
    assert _op(client).delete("/api/admin/accounts/victim").status_code == 204
    listed = client.get("/api/admin/accounts", headers=ADMIN).json()
    assert all(r["username"] != "victim" for r in listed)   # 목록에서 사라졌다
    rows = db.query(
        "SELECT * FROM audit_log WHERE mutation_class='account' AND operation='delete'")
    assert len(rows) == 1 and rows[0]["target_key"] == "victim"
    # 세션 호출이므로 감사 actor 는 운영자 이름이다(토큰은 이제 403 -- test_token_cannot_mutate_accounts).
    assert rows[0]["actor"] == OP


def test_delete_missing_account_404(client):
    r = _op(client).delete("/api/admin/accounts/nope")
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


def _pretend_last_admin(client, monkeypatch):
    # 계정 변경이 세션 관리자 전용이 된 뒤로(2026-10-07) 호출자 자신이 활성 admin 이라, 대상이 "마지막" 이 되는
    # 길은 self-guard 가 먼저 막는다. 마지막 관리자 가드는 동시 변경(둘이 서로를 끄는 경합)에 대한 2차 방어로
    # 남으므로, 그 순간(활성 admin 수 1)을 흉내 내 가드 자체를 검증한다.
    monkeypatch.setattr(client.app.state.repos.accounts, "active_admin_count", lambda: 1)


def test_delete_last_active_admin_forbidden(client, monkeypatch):
    # 마지막 활성 admin 삭제 시도 -> 409, 계정 불변.
    _mk_admin(client, "onlyadm")
    op = _op(client)
    _pretend_last_admin(client, monkeypatch)
    r = op.delete("/api/admin/accounts/onlyadm")
    assert r.status_code == 409 and r.json()["detail"] == "last_active_admin"
    listed = client.get("/api/admin/accounts", headers=ADMIN).json()
    assert any(row["username"] == "onlyadm" for row in listed)


def test_delete_one_of_two_admins_succeeds(client):
    # 대조: admin 이 둘이면 한 명 삭제는 통과한다(마지막 관리자 가드는 '마지막'만 막는다).
    _mk_admin(client, "adm1")
    _mk_admin(client, "adm2")
    assert _op(client).delete("/api/admin/accounts/adm2").status_code == 204


def test_delete_account_with_active_request_forbidden(client, db):
    # 비종단 요청을 가진 계정 삭제는 409 -- 잡 신원은 plan 시점에 구워져 삭제가
    # 소급되지 않으므로(설계 §1-6) 소유자 없는 잡을 예방한다.
    client.post("/api/auth/signup", json={"username": "busy", "password": "p"})
    db.execute(
        """INSERT INTO requests (request_id, commit_order, operation, requester_id, actor,
               resource_key, priority, payload, state, created_at, updated_at, auth_method)
           VALUES ('rq1', 1, 'scan', 'busy', 'busy', 'k', 'mid', '{}', 'Pending',
               '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z', 'session')""")
    r = _op(client).delete("/api/admin/accounts/busy")
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
    assert _op(client).delete("/api/admin/accounts/done").status_code == 204
    # 이력 보존: 요청 행의 requester_id 문자열은 남는다(설계 §2.3, FK 0건).
    assert db.query_one("SELECT requester_id FROM requests WHERE request_id='rq2'"
                        )["requester_id"] == "done"


def test_demote_last_active_admin_forbidden(client, monkeypatch):
    # 마지막 활성 admin 을 user 로 강등 시도 -> 409, 역할 불변.
    _mk_admin(client, "onlyadm2")
    op = _op(client)
    _pretend_last_admin(client, monkeypatch)
    r = op.put("/api/admin/accounts/onlyadm2/role", json={"role": "user"})
    assert r.status_code == 409 and r.json()["detail"] == "last_active_admin"
    row = next(a for a in client.get("/api/admin/accounts", headers=ADMIN).json()
               if a["username"] == "onlyadm2")
    assert row["role"] == "admin"


def test_disable_last_active_admin_forbidden(client, monkeypatch):
    # 마지막 활성 admin 비활성화 시도 -> 409, disabled 불변.
    _mk_admin(client, "onlyadm3")
    op = _op(client)
    _pretend_last_admin(client, monkeypatch)
    r = op.put("/api/admin/accounts/onlyadm3/disabled", json={"disabled": True})
    assert r.status_code == 409 and r.json()["detail"] == "last_active_admin"
    row = next(a for a in client.get("/api/admin/accounts", headers=ADMIN).json()
               if a["username"] == "onlyadm3")
    assert row["disabled"] == 0


def test_demote_one_of_two_admins_succeeds(client):
    # 대조: admin 이 둘이면 강등 통과.
    _mk_admin(client, "adm_a")
    _mk_admin(client, "adm_b")
    assert _op(client).put("/api/admin/accounts/adm_b/role", json={"role": "user"}).status_code == 200


def test_promote_last_admin_is_not_guarded(client, monkeypatch):
    # 승격/재활성화는 관리자 수를 줄이지 않으므로 마지막 관리자 가드 대상이 아니다.
    _mk_admin(client, "onlyadm4")
    op = _op(client)
    _pretend_last_admin(client, monkeypatch)
    # admin -> admin(무변화)도 강등이 아니므로 통과해야 한다.
    assert op.put("/api/admin/accounts/onlyadm4/role", json={"role": "admin"}).status_code == 200


def test_reenable_last_admin_is_not_guarded(client, monkeypatch):
    # 대조: 비활성화된 admin 을 다시 켜는 것은 관리자 수를 늘리므로 마지막 관리자 가드를 타지 않는다.
    # (활성 admin 이 0명이 되면 x-admin-token 부트스트랩으로 새 관리자를 만들어 그 세션으로 다시 켠다 --
    # 계정 변경이 세션 전용이 된 뒤의 출구.)
    _mk_admin(client, "onlyadm5")
    op = _op(client)
    assert op.put("/api/admin/accounts/onlyadm5/disabled", json={"disabled": True}).status_code == 200
    _pretend_last_admin(client, monkeypatch)
    assert op.put("/api/admin/accounts/onlyadm5/disabled", json={"disabled": False}).status_code == 200


# --- 특권 목록 이름의 계정 보호(2026-10-07 리뷰) -------------------------------------------------------

def _session(client, username, password="p"):
    c = TestClient(client.app)
    assert c.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return c


def test_admin_outside_the_privileged_list_cannot_mint_or_take_over_privileged_names(client, db):
    # 목록(기본 root·admin) 밖 관리자(opadm)가 계정 화면으로 "root" 를 새로 만들거나, "admin" 을 지우고 다시 만들거나,
    # "root" 로 가입한 계정을 승격하면 그 세션이 배치 특권 게이트·root 실행 자격을 얻었다 -- 이제 403, DB 불변.
    _mk_admin(client, "admin")
    client.post("/api/auth/signup", json={"username": "root", "password": "p"})   # 가입 계정(user)
    op = _op(client)
    snapshot = "SELECT username, role, disabled, password_hash FROM accounts ORDER BY username"
    before = db.query(snapshot)
    calls = [
        op.post("/api/admin/accounts", json={"username": "root2", "password": "x", "role": "admin"}),  # 목록 밖 이름: 통과
        op.put("/api/admin/accounts/root/role", json={"role": "admin"}),
        op.delete("/api/admin/accounts/admin"),
        op.put("/api/admin/accounts/admin/disabled", json={"disabled": True}),
        op.put("/api/admin/accounts/admin/role", json={"role": "user"}),
    ]
    assert calls[0].status_code == 201                                             # 대조: 평범한 이름은 된다
    for r in calls[1:]:
        assert r.status_code == 403 and r.json()["detail"] == "privileged_account_protected", r.text
    assert [row for row in db.query(snapshot) if row["username"] != "root2"] == before
    me = _session(client, "root").get("/api/auth/me").json()
    assert me["role"] == "user" and me["can_run_as_root"] is False


def test_admin_outside_the_list_cannot_create_a_privileged_name_even_as_user(client):
    # user 로 만든 뒤 승격하는 길까지 막는다(생성은 역할 무관 가드).
    op = _op(client)
    for role in ("admin", "user"):
        r = op.post("/api/admin/accounts", json={"username": "root", "password": "x", "role": role})
        assert r.status_code == 403 and r.json()["detail"] == "privileged_account_protected"
    assert client.app.state.repos.accounts.get("root") is None


def test_privileged_session_admin_manages_privileged_names(client):
    # 목록 안 세션 관리자(admin)는 된다 -- 경계는 목록 밖 관리자에게만 있다.
    _mk_admin(client, "admin")
    adm = _session(client, "admin")
    assert adm.post("/api/admin/accounts", json={"username": "root", "password": "x", "role": "admin"}).status_code == 201
    assert adm.put("/api/admin/accounts/root/disabled", json={"disabled": True}).status_code == 200
    assert adm.delete("/api/admin/accounts/root").status_code == 204


def test_privileged_names_are_ordinary_when_privileged_execution_is_off(client, monkeypatch):
    # 특권 실행이 꺼져 있으면 그 이름에 특권이 없다 -- 가드도 없다.
    from dataclasses import replace
    monkeypatch.setattr(client.app.state, "settings",
                        replace(client.app.state.settings, allow_privileged_requesters=False))
    op = _op(client)
    assert op.post("/api/admin/accounts", json={"username": "root", "password": "x", "role": "admin"}).status_code == 201


def test_privileged_names_do_not_use_self_service_password_reset(client):
    # 메일 경로(메일 설정 stub·릴레이 주소)는 아무 세션 관리자나 바꿀 수 있어, 목록 밖 관리자가 "root" 의 재설정 코드를
    # 받아 root 자격을 얻었다 -- 목록 이름은 셀프 재설정 자체를 쓰지 않는다(발급·소비 둘 다). 보통 계정은 그대로다.
    _mk_admin(client, "root")
    client.post("/api/auth/signup", json={"username": "plain", "password": "p"})
    r = client.post("/api/auth/verification-codes", json={"username": "root", "purpose": "password_reset"})
    assert r.status_code == 403 and r.json()["detail"] == "privileged_account_protected"
    r = client.post("/api/auth/password-reset", json={"username": "root", "password": "new", "code": "000000"})
    assert r.status_code == 403 and r.json()["detail"] == "privileged_account_protected"
    assert client.post("/api/auth/login", json={"username": "root", "password": "p"}).status_code == 200
    assert client.post("/api/auth/verification-codes",
                       json={"username": "plain", "purpose": "password_reset"}).status_code == 200
