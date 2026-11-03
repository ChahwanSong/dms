"""관리자 비밀번호 재설정(2026-11-03, POST /api/admin/accounts/{username}/password-reset -- routes_auth.admin_password_reset).

세션 관리자만(토큰 403 accounts_session_required), 특권 계정은 특권 세션 관리자만(guard_privileged_account), 자기 자신 409,
봉인 전송(용도 admin_password_reset, AAD 사용자 = 대상), 대상의 모든 세션 무효화(세션 세대 회전), 인증번호 전체 잠금과
무관(구제 경로)."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from dms.api.app import create_app
from dms.api.password_transport import seal_with_info
from dms.db import load_json
from dms.repositories import Repositories

TOKEN = {"Authorization": "Bearer tok-shared"}


def _url(username):
    return f"/api/admin/accounts/{username}/password-reset"


def _login(c, username, password):
    return c.post("/api/auth/login", json={"username": username, "password": password})


@pytest.fixture
def repos(db):
    return Repositories(db)


@pytest.fixture
def admin(db, settings, repos):
    """세션 관리자 boss(특권 목록 밖) + 대상 alice(user)."""
    repos.accounts.create("boss", "pw", "admin")
    repos.accounts.create("alice", "old", "user")
    c = TestClient(create_app(settings, db))
    assert _login(c, "boss", "pw").status_code == 200
    return c


def test_session_admin_resets_a_password(admin, db, repos):
    r = admin.post(_url("alice"), json={"username": "alice", "password": "new-pw"})
    assert (r.status_code, r.json()) == (200, {"username": "alice", "sessions_revoked": True, "notice_mail": "stub"})
    other = TestClient(admin.app)
    assert _login(other, "alice", "old").status_code == 401
    assert _login(other, "alice", "new-pw").status_code == 200
    row = db.query_one("SELECT actor, before_state, after_state FROM audit_log "
                       "WHERE mutation_class = 'account' AND operation = 'password_admin_reset'")
    assert row["actor"] == "boss"
    assert load_json(row["after_state"]) == {"username": "alice", "sessions_revoked": True}
    assert "new-pw" not in (row["after_state"] or "") and "scrypt" not in (row["after_state"] or "")
    ev = db.query_one("SELECT payload FROM events WHERE event_type = 'password_admin_reset'")
    p = load_json(ev["payload"])
    assert (p["username"], p["actor"], p["client_ip"], p["peer"]) == ("alice", "boss", "testclient", "testclient")
    # 완료 안내 메일(stub) -- 보내지 않고 이벤트만
    ev = db.query_one("SELECT payload FROM events WHERE event_type = 'account_notice_stub'")
    assert load_json(ev["payload"])["kind"] == "password_admin_reset"


def test_target_sessions_end_but_the_admin_and_bearer_are_untouched(admin):
    alice = TestClient(admin.app)
    assert _login(alice, "alice", "old").status_code == 200
    assert alice.get("/api/auth/me").json()["actor"] == "alice"
    assert admin.post(_url("alice"), json={"username": "alice", "password": "new"}).status_code == 200
    r = alice.get("/api/auth/me")
    assert (r.status_code, r.json()["detail"]) == (401, "session_revoked")
    r = alice.get("/api/auth/me")                                   # 쿠키를 비웠다 -- 다음은 그냥 미인증
    assert (r.status_code, r.json()["detail"]) == (401, "not_authenticated")
    assert admin.get("/api/auth/me").json()["actor"] == "boss"      # 관리자 자신의 세션은 그대로
    assert admin.get("/api/admin/accounts", headers=TOKEN).status_code == 200   # Bearer 무관


def test_rejections(admin, db, settings, repos):
    # 토큰(공유 Bearer) → 세션 필수
    r = TestClient(admin.app).post(_url("alice"), json={"username": "alice", "password": "x"}, headers=TOKEN)
    assert (r.status_code, r.json()["detail"]) == (403, "accounts_session_required")
    # 일반 사용자 세션 → 관리자 필수
    user = TestClient(admin.app)
    repos.accounts.create("pleb", "pw", "user")
    _login(user, "pleb", "pw")
    r = user.post(_url("alice"), json={"username": "alice", "password": "x"})
    assert (r.status_code, r.json()["detail"]) == (403, "admin_required")
    # 본문·경로 불일치(존재 확인보다 먼저)
    r = admin.post(_url("alice"), json={"username": "bob", "password": "x"})
    assert (r.status_code, r.json()["detail"]) == (422, "account_username_mismatch")
    r = admin.post(_url("ghost"), json={"username": "nobody", "password": "x"})
    assert (r.status_code, r.json()["detail"]) == (422, "account_username_mismatch")
    # 없는 계정
    r = admin.post(_url("ghost"), json={"username": "ghost", "password": "x"})
    assert (r.status_code, r.json()["detail"]) == (404, "account_not_found")
    # 자기 자신
    r = admin.post(_url("boss"), json={"username": "boss", "password": "x"})
    assert (r.status_code, r.json()["detail"]) == (409, "cannot_reset_self")
    # 비밀번호 없음
    r = admin.post(_url("alice"), json={"username": "alice"})
    assert (r.status_code, r.json()["detail"]) == (422, "password_missing")
    # 어느 거절도 비밀번호를 바꾸지 않았다
    assert _login(TestClient(admin.app), "alice", "old").status_code == 200
    assert db.query("SELECT 1 AS x FROM audit_log WHERE operation = 'password_admin_reset'") == []


def test_privileged_target_needs_a_privileged_session_admin(admin, db, settings, repos):
    # 특권 목록(기본 root·admin) 이름의 계정은 특권 세션 관리자만
    repos.accounts.create("root", "rootpw", "user")
    r = admin.post(_url("root"), json={"username": "root", "password": "x"})
    assert (r.status_code, r.json()["detail"]) == (403, "privileged_account_protected")
    assert _login(TestClient(admin.app), "root", "rootpw").status_code == 200
    repos.accounts.create("admin", "pw", "admin")                   # 특권 목록 이름의 관리자
    priv = TestClient(admin.app)
    assert _login(priv, "admin", "pw").status_code == 200
    r = priv.post(_url("root"), json={"username": "root", "password": "newroot"})
    assert r.status_code == 200
    assert _login(TestClient(admin.app), "root", "newroot").status_code == 200


def test_live_policy_requires_the_admin_password_reset_seal(db, settings, repos):
    repos.accounts.create("boss", "pw", "admin")
    repos.accounts.create("alice", "old", "user")
    c = TestClient(create_app(replace(settings, password_encryption_required=True), db))
    key = c.get("/api/auth/transport-key").json()
    assert c.post("/api/auth/login", json={
        "username": "boss", "password_enc": seal_with_info(key, "pw", purpose="login", username="boss")}
    ).status_code == 200
    r = c.post(_url("alice"), json={"username": "alice", "password": "new"})
    assert (r.status_code, r.json()["detail"]) == (422, "password_encryption_required")
    for purpose, username in (("login", "alice"), ("admin_create", "alice"), ("admin_password_reset", "bob")):
        r = c.post(_url("alice"), json={
            "username": "alice", "password_enc": seal_with_info(key, "new", purpose=purpose, username=username)})
        assert (r.status_code, r.json()["detail"]) == (422, "password_encryption_invalid"), (purpose, username)
    r = c.post(_url("alice"), json={
        "username": "alice",
        "password_enc": seal_with_info(key, "new", purpose="admin_password_reset", username="alice")})
    assert r.status_code == 200
    assert c.post("/api/auth/login", json={
        "username": "alice", "password_enc": seal_with_info(key, "new", purpose="login", username="alice")}
    ).status_code == 200


def test_works_while_the_global_verification_cap_is_active(admin):
    # 구제 경로(2026-11-03): 인증번호 전체 오답 상한이 걸려 셀프서비스가 멈춘 동안에도 관리자는 재설정·생성할 수 있다.
    limiter = admin.app.state.verification_guess_limiter
    for _ in range(limiter.max_attempts):
        limiter.record_failure("global")
    assert limiter.retry_after("global") is not None
    assert admin.post(_url("alice"), json={"username": "alice", "password": "new"}).status_code == 200
    r = admin.post("/api/admin/accounts", json={"username": "made.during.lock", "password": "pw"})
    assert r.status_code == 201


def test_reset_clears_the_login_failure_key_so_the_user_can_log_in_at_once(admin):
    other = TestClient(admin.app)
    for _ in range(10):
        assert _login(other, "alice", "guess").status_code == 401
    assert _login(other, "alice", "old").status_code == 429         # 감속 중
    assert admin.post(_url("alice"), json={"username": "alice", "password": "new"}).status_code == 200
    assert _login(other, "alice", "new").status_code == 200


@pytest.mark.parametrize("backend,mode,event", [
    ("knox_relay", "queued", "account_notice_failed"),             # 주소가 없어 실패 -- 응답은 그대로 200
    ("smtp", "unavailable", "account_notice_skipped"),
])
def test_notice_mail_mode_follows_the_mail_settings(db, settings, repos, backend, mode, event):
    repos.accounts.create("boss", "pw", "admin")
    repos.accounts.create("alice", "old", "user")
    c = TestClient(create_app(replace(settings, mailer_backend=backend), db))
    _login(c, "boss", "pw")
    r = c.post(_url("alice"), json={"username": "alice", "password": "new"})
    assert (r.status_code, r.json()["notice_mail"]) == (200, mode)
    ev = db.query_one("SELECT payload FROM events WHERE event_type = :t", {"t": event})
    assert load_json(ev["payload"])["kind"] == "password_admin_reset"
