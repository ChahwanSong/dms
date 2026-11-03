"""관리 → 보안 이벤트(2026-11-03, routes_security): 보호 장치 상태·인증번호 잠금 목록·잠금 해제(아이디별·전체)·보안 이벤트 목록,
그리고 프런트 라벨 파일과의 이벤트 종류 대조."""
import re
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dms.api.app import create_app
from dms.api.routes_security import SECURITY_EVENT_TYPES
from dms.db import load_json
from dms.repositories import Repositories

ROOT = Path(__file__).resolve().parent.parent
TOKEN = {"Authorization": "Bearer tok-shared"}


def _login(c, username, password):
    return c.post("/api/auth/login", json={"username": username, "password": password})


@pytest.fixture
def repos(db):
    return Repositories(db)


@pytest.fixture
def admin(db, settings, repos):
    repos.accounts.create("boss", "pw", "admin")
    c = TestClient(create_app(replace(settings, account_verification_required=True), db))
    assert _login(c, "boss", "pw").status_code == 200
    return c


def _fail_rows(db, rows):
    for username, purpose, window_start, failures in rows:
        db.execute("""INSERT INTO verification_failures (username, purpose, window_start, failures)
                      VALUES (:u, :p, :w, :f)""", {"u": username, "p": purpose, "w": window_start, "f": failures})


def _now_minus(seconds):
    from dms.db import iso_plus, utc_now_iso
    return iso_plus(utc_now_iso(), -seconds)


# --- 상태 ---

def test_status_reflects_settings_and_memory_counters(db, settings, repos):
    repos.accounts.create("boss", "pw", "admin")
    c = TestClient(create_app(replace(
        settings, login_id_attempt_limit=60, login_global_pace_limit=0, verification_guess_global_limit=1000,
        login_alarm_source_failures=0), db))
    _login(c, "boss", "pw")
    _login(c, "nobody", "x")                                         # 실패 1(경보 카운터는 늘 센다)
    r = c.get("/api/admin/security/status", headers=TOKEN)          # 조회는 토큰 관리자도
    assert r.status_code == 200
    body = r.json()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", body["started_at"])
    assert body["started_at"] == c.app.state.started_at
    assert body["login"] == {
        "id_failure_limit": 10, "id_failure_window_seconds": 60,
        "id_attempt_limit": 60, "id_attempt_window_seconds": 60,
        "scrypt_slots": 2, "scrypt_max_wait_seconds": 5, "scrypt_in_use": 0, "scrypt_waiting": 0,
        "pacing": {"enabled": False, "limit": 0, "window_seconds": 60, "max_wait_seconds": 15,
                   "tokens": None, "waiting": 0}}
    assert body["alarms"] == {"window_seconds": 600, "failures": 30, "attempts": 300, "source_failures": 0,
                              "recent_failures": 1, "recent_attempts": 2}
    assert body["verification"] == {
        "code_digits": 6, "code_ttl_seconds": 300, "per_code_attempts": 5,
        "per_id_failure_limit": 10, "per_id_failure_window_seconds": 86400,
        "global_guess_limit": 1000, "global_guess_window_seconds": 3600,
        "global_guess_failures": 0, "global_locked": False, "global_retry_after_seconds": None}


def test_status_shows_pacing_tokens_and_a_global_lock(db, settings, repos):
    c = TestClient(create_app(replace(settings, login_global_pace_limit=5,
                                      verification_guess_global_limit=2), db))
    limiter = c.app.state.verification_guess_limiter
    limiter.record_failure("global")
    limiter.record_failure("global")
    body = c.get("/api/admin/security/status", headers=TOKEN).json()
    assert body["login"]["pacing"]["enabled"] is True
    assert body["login"]["pacing"]["tokens"] == pytest.approx(5.0)
    v = body["verification"]
    assert (v["global_guess_failures"], v["global_locked"]) == (2, True)
    assert 1 <= v["global_retry_after_seconds"] <= 3600
    # 기동할 때 스위치가 켜져 있으면 한 줄 남긴다(켜 둔 채 잊지 않게)
    ev = db.query("SELECT payload FROM events WHERE event_type = 'login_pacing_enabled'")
    assert len(ev) == 1 and load_json(ev[0]["payload"]) == {"limit": 5, "window_seconds": 60, "max_wait_seconds": 15}


def test_status_and_lists_need_an_admin(client, repos):
    assert client.get("/api/admin/security/status").status_code == 401
    repos.accounts.create("pleb", "pw", "user")
    _login(client, "pleb", "pw")
    for path in ("/api/admin/security/status", "/api/admin/security/verification-locks",
                 "/api/admin/security/events"):
        r = client.get(path)
        assert (r.status_code, r.json()["detail"]) == (403, "admin_required"), path


# --- 아이디별 잠금 ---

def test_locks_list(admin, db):
    _fail_rows(db, [("a.user", "signup", _now_minus(3600), 10),
                    ("b.user", "password_reset", _now_minus(60), 2),
                    ("old.one", "signup", _now_minus(90000), 10)])
    r = admin.get("/api/admin/security/verification-locks")
    assert r.status_code == 200
    body = r.json()
    assert (body["truncated"], body["limit"]) == (False, 200)
    assert [(i["username"], i["purpose"], i["locked"]) for i in body["items"]] == [
        ("a.user", "signup", True), ("b.user", "password_reset", False)]
    assert 82000 < body["items"][0]["retry_after_seconds"] <= 82800
    assert body["items"][1]["retry_after_seconds"] is None
    r = admin.get("/api/admin/security/verification-locks?limit=1")
    assert (r.json()["truncated"], len(r.json()["items"])) == (True, 1)
    for bad in ("0", "1001"):
        assert admin.get(f"/api/admin/security/verification-locks?limit={bad}").status_code == 422


def test_unlock_one_account_then_it_can_get_a_code_again(admin, db, repos):
    repos.accounts.create("alice", "pw", "user")
    _fail_rows(db, [("alice", "password_reset", _now_minus(60), 10), ("alice", "signup", _now_minus(60), 3)])
    r = admin.post("/api/auth/verification-codes", json={"username": "alice", "purpose": "password_reset"})
    assert (r.status_code, r.json()["detail"]) == (429, "verification_locked")
    r = admin.post("/api/admin/security/verification-unlock", json={"username": "alice", "purpose": "password_reset"},
                   headers={"x-forwarded-for": "10.5.5.5"})
    assert (r.status_code, r.json()) == (200, {"username": "alice", "purpose": "password_reset", "cleared": 1})
    assert admin.post("/api/auth/verification-codes",
                      json={"username": "alice", "purpose": "password_reset"}).status_code == 200
    audit = db.query_one("SELECT actor, target_key, before_state FROM audit_log "
                         "WHERE mutation_class = 'security' AND operation = 'verification_unlock'")
    assert (audit["actor"], audit["target_key"]) == ("boss", "alice")
    assert load_json(audit["before_state"])["rows"][0]["failures"] == 10
    ev = db.query_one("SELECT payload FROM events WHERE event_type = 'verification_unlocked'")
    p = load_json(ev["payload"])
    assert (p["username"], p["purpose"], p["cleared"], p["actor"], p["client_ip"]) == \
        ("alice", "password_reset", 1, "boss", "10.5.5.5")
    # purpose 생략(null) = 두 용도 모두 -- 남은 signup 행
    r = admin.post("/api/admin/security/verification-unlock", json={"username": "alice"})
    assert r.json() == {"username": "alice", "purpose": None, "cleared": 1}
    # 지울 것이 없어도 200 cleared 0(감사는 남는다)
    r = admin.post("/api/admin/security/verification-unlock", json={"username": "alice", "purpose": None})
    assert (r.status_code, r.json()["cleared"]) == (200, 0)
    assert len(db.query("SELECT 1 AS x FROM audit_log WHERE operation = 'verification_unlock'")) == 3


def test_unlock_works_for_names_without_an_account(admin, db):
    # 가입 잠금은 아직 계정이 없는 아이디의 것 -- 형식(정규식)도 보지 않는다(숫자 아이디의 옛 재설정 행도 풀 수 있게)
    _fail_rows(db, [("new.comer", "signup", _now_minus(60), 10), ("1234", "password_reset", _now_minus(60), 10)])
    assert admin.post("/api/admin/security/verification-unlock",
                      json={"username": "new.comer", "purpose": "signup"}).json()["cleared"] == 1
    assert admin.post("/api/admin/security/verification-unlock", json={"username": "1234"}).json()["cleared"] == 1


def test_unlock_rejections(admin, db, repos):
    r = TestClient(admin.app).post("/api/admin/security/verification-unlock",
                                   json={"username": "alice"}, headers=TOKEN)
    assert (r.status_code, r.json()["detail"]) == (403, "accounts_session_required")
    for name in ("", "   ", "x" * 129):
        r = admin.post("/api/admin/security/verification-unlock", json={"username": name})
        assert (r.status_code, r.json()["detail"]) == (422, "invalid_username"), name
    r = admin.post("/api/admin/security/verification-unlock", json={"username": "alice", "purpose": "login"})
    assert (r.status_code, r.json()["detail"]) == (422, "invalid_verification_purpose")
    # 특권 목록 이름은 특권 세션 관리자만(boss 는 목록 밖)
    _fail_rows(db, [("root", "password_reset", _now_minus(60), 10)])
    r = admin.post("/api/admin/security/verification-unlock", json={"username": "root"})
    assert (r.status_code, r.json()["detail"]) == (403, "privileged_account_protected")
    assert db.query_one("SELECT failures FROM verification_failures WHERE username = 'root'")["failures"] == 10
    repos.accounts.create("admin", "pw", "admin")
    priv = TestClient(admin.app)
    _login(priv, "admin", "pw")
    assert priv.post("/api/admin/security/verification-unlock", json={"username": "root"}).json()["cleared"] == 1


# --- 전체 잠금 해제 ---

def test_global_unlock_reopens_self_service(db, settings, repos):
    repos.accounts.create("boss", "pw", "admin")
    repos.accounts.create("alice", "old", "user")
    c = TestClient(create_app(replace(settings, account_verification_required=True,
                                      verification_guess_global_limit=2), db))
    _login(c, "boss", "pw")
    code = c.post("/api/auth/verification-codes",
                  json={"username": "alice", "purpose": "password_reset"}).json()["stub_code"]
    wrong = f"{(int(code) + 1) % 1000000:06d}"
    for _ in range(2):
        assert c.post("/api/auth/password-reset",
                      json={"username": "alice", "password": "x", "code": wrong}).status_code == 422
    r = c.post("/api/auth/password-reset", json={"username": "alice", "password": "new", "code": code})
    assert (r.status_code, r.json()["detail"]) == (429, "verification_global_locked")
    # 토큰은 못 푼다
    r = TestClient(c.app).post("/api/admin/security/verification-global-unlock", json={}, headers=TOKEN)
    assert (r.status_code, r.json()["detail"]) == (403, "accounts_session_required")
    r = c.post("/api/admin/security/verification-global-unlock", json={"ignored": True})
    assert (r.status_code, r.json()) == (200, {"cleared_failures": 2})
    assert c.get("/api/admin/security/status").json()["verification"]["global_locked"] is False
    r = c.post("/api/auth/password-reset", json={"username": "alice", "password": "new", "code": code})
    assert r.status_code == 200
    audit = db.query_one("SELECT actor, target_key, before_state, after_state FROM audit_log "
                         "WHERE mutation_class = 'security' AND operation = 'verification_global_unlock'")
    assert (audit["actor"], audit["target_key"]) == ("boss", "verification_guess_global")
    assert (load_json(audit["before_state"]), load_json(audit["after_state"])) == ({"failures": 2}, {"failures": 0})
    ev = db.query_one("SELECT payload FROM events WHERE event_type = 'verification_global_unlocked'")
    assert {k: load_json(ev["payload"])[k] for k in ("cleared_failures", "actor")} == {"cleared_failures": 2,
                                                                                        "actor": "boss"}
    # 비어 있을 때도 200 cleared 0
    assert c.post("/api/admin/security/verification-global-unlock").json() == {"cleared_failures": 0}


# --- 이벤트 목록 ---

def _seed_events(repos):
    rec = repos.observability.record_event
    rec(component="auth", severity="warning", event_type="login_rate_limited", message="user:Alice: 10 failed",
        payload={"username": "Alice", "client_ip": "10.9.9.9", "peer": "10.0.0.1"})
    rec(component="db", severity="info", event_type="db_reconnected", message="10.9.9.9 not security")
    rec(component="auth", severity="warning", event_type="login_source_alarm", message="12 failed from 10.9.9.9",
        payload={"count": 12, "client_ip": "10.9.9.9", "peer": "10.0.0.1", "last_username": "bob"})
    rec(component="mailer", severity="error", event_type="verification_email_failed", message="x 100% down",
        payload={"purpose": "signup", "recipient": "c@samsung.com", "reason": "relay_unreachable"})
    rec(component="accounts", severity="info", event_type="password_admin_reset", message="boss reset alice",
        payload={"username": "alice", "actor": "boss", "client_ip": None, "peer": "testclient"})
    rec(component="mailer", severity="info", event_type="verification_email_sent", message="not security either")


def test_events_list_only_security_types_newest_first(admin, repos):
    _seed_events(repos)
    r = admin.get("/api/admin/security/events")
    assert r.status_code == 200
    rows = r.json()
    assert [e["event_type"] for e in rows] == [
        "password_admin_reset", "verification_email_failed", "login_source_alarm", "login_rate_limited"]
    assert set(rows[0]) == {"id", "at", "component", "severity", "event_type", "message", "payload"}
    assert rows[0]["payload"] == {"username": "alice", "actor": "boss", "client_ip": None, "peer": "testclient"}
    assert TestClient(admin.app).get("/api/admin/security/events", headers=TOKEN).status_code == 200


def test_events_filters_search_and_keyset(admin, repos):
    _seed_events(repos)
    get = lambda q: admin.get(f"/api/admin/security/events{q}")   # noqa: E731
    assert [e["event_type"] for e in get("?type=login_source_alarm").json()] == ["login_source_alarm"]
    assert [e["event_type"] for e in get("?severity=error").json()] == ["verification_email_failed"]
    assert [e["event_type"] for e in get("?severity=warning&type=login_rate_limited").json()] == ["login_rate_limited"]
    # q: message·payload 부분 일치, 대소문자 무시, 토큰 사이 AND
    assert [e["event_type"] for e in get("?q=10.9.9.9").json()] == ["login_source_alarm", "login_rate_limited"]
    assert [e["event_type"] for e in get("?q=ALICE").json()] == ["password_admin_reset", "login_rate_limited"]
    assert [e["event_type"] for e in get("?q=alice%20boss").json()] == ["password_admin_reset"]
    assert [e["event_type"] for e in get("?q=bob").json()] == ["login_source_alarm"]       # payload 만
    assert [e["event_type"] for e in get("?q=100%25").json()] == ["verification_email_failed"]   # % 는 리터럴
    assert get("?q=%25").json() == [e for e in get("").json() if "%" in (e["message"] or "")]
    # 빈 값은 필터 없음
    assert len(get("?type=&severity=&q=").json()) == 4
    # 키셋
    first = get("?limit=2").json()
    assert len(first) == 2
    rest = get(f"?limit=2&before={first[-1]['id']}").json()
    assert [e["event_type"] for e in rest] == ["login_source_alarm", "login_rate_limited"]
    assert get(f"?before={rest[-1]['id']}").json() == []
    # 잘못된 필터·범위
    for bad in ("?type=db_reconnected", "?type=nope", "?severity=critical"):
        r = get(bad)
        assert (r.status_code, r.json()["detail"]) == (422, "invalid_event_filter"), bad
    for bad in ("?limit=0", "?limit=201", "?before=0", "?q=" + "x" * 201):
        assert get(bad).status_code == 422, bad


def test_login_failures_land_in_the_security_list(db, settings, repos):
    repos.accounts.create("boss", "pw", "admin")
    c = TestClient(create_app(replace(settings, login_alarm_failures=3), db))
    for i in range(3):
        _login(c, f"ghost{i}", "x")
    _login(c, "boss", "pw")
    rows = c.get("/api/admin/security/events?type=login_failure_alarm").json()
    assert len(rows) == 1 and rows[0]["payload"]["count"] == 3 and rows[0]["payload"]["threshold"] == 3


# --- 프런트 라벨 대조 ---

def test_security_event_types_match_the_frontend_labels_exactly():
    path = ROOT / "frontend" / "src" / "features" / "security" / "eventLabels.ts"
    assert path.is_file(), f"{path} 가 없다 -- 프런트 보안 이벤트 라벨(SECURITY_EVENT_LABELS)"
    src = path.read_text()
    block = re.search(r"SECURITY_EVENT_LABELS[^{]*\{(.*?)\n\}", src, re.S)
    assert block is not None
    keys = re.findall(r"^  ([a-z_]+): \"", block.group(1), re.M)
    assert len(keys) == len(set(keys))
    assert set(keys) == set(SECURITY_EVENT_TYPES)
    assert len(SECURITY_EVENT_TYPES) == len(set(SECURITY_EVENT_TYPES)) == 20
