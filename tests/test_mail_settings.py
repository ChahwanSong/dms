"""포탈 메일 설정(2026-10-01) -- 저장용 봉인(secret_box), 포탈 > env 해석(mail_config), 관리자 API
(routes_mail_settings: 조회·저장·연결 확인·테스트 메일). 토큰 값은 응답·감사 로그·이벤트 어디에도 없어야 하고,
토큰은 주소에 묶인다(주소를 바꿔 테스트 메일로 키를 빼내지 못하게). 변경·발송은 세션 관리자만."""
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from dms.api import password_transport as pt
from dms.api.app import create_app
from dms.mail_config import resolve_mail_config, seal_token
from dms.repositories import Repositories
from dms.secret_box import open_at_rest, seal_at_rest
from fake_knox_relay import FakeRelay

BEARER = {"Authorization": "Bearer tok-shared"}
TOKEN = "relay-secret-XYZ"


@pytest.fixture
def admin(client, db):
    """세션으로 로그인한 관리자 -- 메일 설정 변경·발송은 세션 관리자만 된다."""
    Repositories(db).accounts.create("ops.admin", "pw", "admin")
    assert client.post("/api/auth/login", json={"username": "ops.admin", "password": "pw"}).status_code == 200
    return client


# --- secret_box ----------------------------------------------------------------------------

def test_secret_box_roundtrip_and_failures():
    blob = seal_at_rest(TOKEN, secret="s1", purpose="mail_relay_token")
    assert blob.startswith("v1:") and TOKEN not in blob
    assert open_at_rest(blob, secret="s1", purpose="mail_relay_token") == TOKEN
    assert open_at_rest(blob, secret="s2", purpose="mail_relay_token") is None      # 세션 시크릿 교체
    assert open_at_rest(blob, secret="s1", purpose="other") is None                 # 용도가 다르면 안 열린다
    assert open_at_rest("v1:!!!", secret="s1", purpose="mail_relay_token") is None
    assert open_at_rest(None, secret="s1", purpose="mail_relay_token") is None
    assert seal_at_rest(TOKEN, secret="s1", purpose="p") != seal_at_rest(TOKEN, secret="s1", purpose="p")  # 매번 새 nonce


# --- 해석(포탈 > env > 기본) ------------------------------------------------------------------

def test_env_url_is_used_verbatim_and_env_token_only_for_the_env_endpoint(db, settings):
    repos = Repositories(db)
    env = replace(settings, mailer_backend="knox_relay", mail_relay_url="http://10.1.2.3:9025",
                  mail_relay_token="env-token", mail_relay_timeout_seconds=15.0, mail_service_name="Env 포털")
    cfg = resolve_mail_config(repos, env)
    assert (cfg.backend, cfg.relay_scheme, cfg.relay_host, cfg.relay_port) == ("knox_relay", "http", "10.1.2.3", 9025)
    assert cfg.relay_url == "http://10.1.2.3:9025" and cfg.relay_token == "env-token"
    assert cfg.token_source == "env" and cfg.endpoint_source == "env"
    assert cfg.timeout_seconds == 15.0 and cfg.service_name == "Env 포털"
    # 포탈이 주소를 바꾸면 env 토큰은 그 주소로 가지 않는다(관리자 권한만으로 k8s Secret 을 빼내는 길을 막는다)
    repos.mail_settings.update({"relay_host": "10.9.9.9", "relay_port": 8025}, actor="ops")
    cfg = resolve_mail_config(repos, env)
    assert cfg.relay_url == "http://10.9.9.9:8025" and cfg.endpoint_source == "portal"
    assert cfg.relay_token == "" and cfg.token_source is None and cfg.env_token_unbound is True
    assert cfg.sources["relay_host"] == "portal" and cfg.sources["backend"] == "env"
    repos.mail_settings.update({"relay_token_enc": seal_token("portal-token", env)}, actor="ops")
    cfg = resolve_mail_config(repos, env)
    assert cfg.relay_token == "portal-token" and cfg.token_source == "portal"
    # 세션 시크릿이 바뀌면 봉인을 못 연다 -- 빈 값처럼 조용히 넘어가지 않고 unreadable 로 드러난다
    cfg = resolve_mail_config(repos, replace(env, session_secret="rotated"))
    assert cfg.token_unreadable and cfg.relay_token == "" and cfg.token_source == "portal"


def test_env_url_semantics_are_not_rewritten(db, settings):
    # 참고 구현처럼 env URL 은 그대로 -- 경로를 버리거나 포트를 8025 로 바꾸지 않는다
    repos = Repositories(db)
    cfg = resolve_mail_config(repos, replace(settings, mail_relay_url="https://relay.corp/knox/"))
    assert cfg.relay_url == "https://relay.corp/knox/" and cfg.relay_port == 443
    assert resolve_mail_config(repos, replace(settings, mail_relay_url="not a url")).relay_url == "not a url"


def test_blank_env_backend_is_not_silently_stub(db, settings):
    assert resolve_mail_config(Repositories(db), replace(settings, mailer_backend="")).backend == ""


def test_resolve_defaults_and_ipv6(db, settings):
    repos = Repositories(db)
    cfg = resolve_mail_config(repos, settings)
    assert (cfg.backend, cfg.relay_url, cfg.relay_port, cfg.token_source) == ("stub", "", 8025, None)
    repos.mail_settings.update({"relay_host": "fd00::5"}, actor="ops")
    assert resolve_mail_config(repos, settings).relay_url == "http://[fd00::5]:8025"


# --- 관리자 API ----------------------------------------------------------------------------

def _no_secret_anywhere(db, client):
    rows = db.query("SELECT before_state, after_state FROM audit_log WHERE mutation_class = 'mail_settings'")
    events = db.query("SELECT message FROM events WHERE component = 'mailer'")
    blob = json.dumps([rows, events, client.get("/api/admin/mail-settings", headers=BEARER).json()],
                      ensure_ascii=False)
    assert TOKEN not in blob
    stored = db.query_one("SELECT relay_token_enc FROM mail_settings WHERE id = 1")
    if stored and stored["relay_token_enc"]:
        assert stored["relay_token_enc"] not in blob          # 봉인 자체도 응답·감사에 안 나간다


def test_admin_can_set_and_clear_fields_and_token_never_leaks(admin, db):
    r = admin.put("/api/admin/mail-settings", json={
        "backend": "knox_relay", "relay_host": "10.20.30.40", "relay_port": 8025,
        "timeout_seconds": 20, "service_name": "Supercom 포털", "relay_token": TOKEN})
    assert r.status_code == 200, r.json()
    v = r.json()
    assert v["relay_url"] == "http://10.20.30.40:8025" and v["backend"] == "knox_relay"
    assert v["token"] == {"configured": True, "source": "portal", "unreadable": False,
                          "env_configured": False, "env_unbound": False}
    assert v["portal"]["relay_host"] == "10.20.30.40" and v["sources"]["relay_host"] == "portal"
    assert v["endpoint_source"] == "portal" and v["updated_by"] == "ops.admin" and v["email_domain"] == "samsung.com"
    _no_secret_anywhere(db, admin)
    # 빈 문자열·null = 포탈 값을 지워 env 기본값으로 -- 주소가 바뀌므로 키도 함께(지우기) 다룬다
    v = admin.put("/api/admin/mail-settings",
                  json={"relay_host": "", "service_name": None, "clear_relay_token": True}).json()
    assert v["relay_host"] == "" and v["relay_url"] == "" and v["sources"]["relay_host"] == "env"
    assert v["service_name"] == "Supercom 포털" and v["sources"]["service_name"] == "env"
    assert v["backend"] == "knox_relay"                     # 보내지 않은 칸은 그대로
    assert v["token"]["configured"] is False and v["token"]["source"] is None


def test_changing_the_endpoint_requires_re_entering_the_token(admin):
    admin.put("/api/admin/mail-settings", json={"relay_host": "10.0.0.1", "relay_port": 8025,
                                                "relay_token": TOKEN})
    r = admin.put("/api/admin/mail-settings", json={"relay_host": "10.6.6.6"})
    assert r.status_code == 422 and r.json()["detail"] == "mail_relay_token_required"
    r = admin.put("/api/admin/mail-settings", json={"relay_port": 9999})
    assert r.status_code == 422 and r.json()["detail"] == "mail_relay_token_required"
    # 주소가 그대로면 다른 칸은 키 없이 바꿀 수 있다
    assert admin.put("/api/admin/mail-settings", json={"service_name": "X", "relay_port": 8025}).status_code == 200
    # 키를 함께 넣으면 된다
    v = admin.put("/api/admin/mail-settings", json={"relay_host": "10.6.6.6", "relay_token": "new-one"}).json()
    assert v["relay_url"] == "http://10.6.6.6:8025" and v["token"]["configured"] is True


def test_audit_records_token_transitions_without_values(admin, db):
    admin.put("/api/admin/mail-settings", json={"relay_host": "10.0.0.1", "relay_token": TOKEN})
    admin.put("/api/admin/mail-settings", json={"relay_token": "second-token"})
    admin.put("/api/admin/mail-settings", json={"clear_relay_token": True})
    admin.put("/api/admin/mail-settings", json={"service_name": "only name"})
    rows = db.query("SELECT after_state FROM audit_log WHERE mutation_class = 'mail_settings' ORDER BY id")
    changes = [json.loads(r["after_state"]).get("relay_token_change") for r in rows]
    assert changes == ["set", "replaced", "cleared", None]
    assert "second-token" not in json.dumps(rows) and TOKEN not in json.dumps(rows)


@pytest.mark.parametrize("body,code", [
    ({"backend": "smtp"}, "invalid_mail_backend"),
    ({"relay_scheme": "ftp"}, "invalid_mail_relay_scheme"),
    ({"relay_host": "http://10.0.0.1:8025"}, "invalid_mail_relay_host"),
    ({"relay_host": "bad host"}, "invalid_mail_relay_host"),
    ({"relay_host": "fe80::1%eth0 x"}, "invalid_mail_relay_host"),
    ({"relay_port": 0}, "invalid_mail_relay_port"),
    ({"relay_port": 70000}, "invalid_mail_relay_port"),
    ({"timeout_seconds": 0.5}, "invalid_mail_timeout"),
    ({"timeout_seconds": 500}, "invalid_mail_timeout"),
    ({"service_name": "x" * 61}, "invalid_mail_service_name"),
    ({"relay_token": "has space"}, "invalid_mail_relay_token"),
    ({"relay_token": "한글토큰"}, "invalid_mail_relay_token"),
    ({"relay_token": TOKEN, "clear_relay_token": True}, "mail_relay_token_conflict"),
])
def test_put_validation(admin, body, code):
    r = admin.put("/api/admin/mail-settings", json=body)
    assert r.status_code == 422 and r.json()["detail"] == code
    assert "has space" not in r.text and "한글토큰" not in r.text      # 토큰 값을 되돌려 주지 않는다


def test_live_policy_requires_a_sealed_token(db, settings):
    live_settings = replace(settings, password_encryption_required=True)
    live = TestClient(create_app(live_settings, db))
    Repositories(db).accounts.create("ops.admin", "pw", "admin")
    info = live.get("/api/auth/transport-key").json()
    assert live.post("/api/auth/login", json={"username": "ops.admin", "password_enc": pt.seal_with_info(
        info, "pw", purpose="login", username="ops.admin")}).status_code == 200
    r = live.put("/api/admin/mail-settings", json={"relay_token": TOKEN})
    assert r.status_code == 422 and r.json()["detail"] == "password_encryption_required"
    wrong = pt.seal_with_info(info, TOKEN, purpose="login", username="mail_settings")      # 용도가 다르다
    r = live.put("/api/admin/mail-settings", json={"relay_token_enc": wrong})
    assert r.status_code == 422 and r.json()["detail"] == "password_encryption_invalid"
    sealed = pt.seal_with_info(info, TOKEN, purpose="mail_relay_token", username="mail_settings")
    r = live.put("/api/admin/mail-settings", json={"relay_token_enc": sealed})
    assert r.status_code == 200 and r.json()["token"]["configured"] is True
    assert resolve_mail_config(Repositories(db), live_settings).relay_token == TOKEN


def test_changes_and_sending_need_a_session_admin(client):
    # 공유 토큰(노드 에이전트의 node:* 포함)은 조회만 -- 주소·발송 방식 변경·발송·연결 확인은 세션 관리자만
    assert client.get("/api/admin/mail-settings", headers=BEARER).status_code == 200
    for method, path, body in (("PUT", "/api/admin/mail-settings", {"backend": "stub"}),
                               ("POST", "/api/admin/mail-settings/test-mail", {}),
                               ("POST", "/api/admin/mail-settings/health-check", None)):
        for headers in (BEARER, {**BEARER, "x-dms-actor": "node:storage-01"}):
            r = client.request(method, path, json=body, headers=headers)
            assert r.status_code == 403 and r.json()["detail"] == "mail_settings_session_required", (path, r.json())


def test_mail_settings_api_is_admin_only(client):
    assert client.get("/api/admin/mail-settings").status_code == 401
    assert client.put("/api/admin/mail-settings", json={}).status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/mail-settings").status_code == 403
    assert client.put("/api/admin/mail-settings", json={"backend": "stub"}).status_code == 403
    assert client.post("/api/admin/mail-settings/health-check").status_code == 403
    assert client.post("/api/admin/mail-settings/test-mail", json={}).status_code == 403


# --- 연결 확인 · 테스트 메일 ------------------------------------------------------------------

def _point_at(client, relay, token=TOKEN):
    host, port = relay.base_url.rsplit("//", 1)[1].rsplit(":", 1)
    r = client.put("/api/admin/mail-settings", json={
        "relay_host": host, "relay_port": int(port), "relay_token": token})
    assert r.status_code == 200, r.json()


def test_health_check_and_test_mail_through_the_fake_relay(admin, db):
    with FakeRelay(token=TOKEN) as relay:
        _point_at(admin, relay)
        assert admin.post("/api/admin/mail-settings/health-check").json()["ok"] is True
        r = admin.post("/api/admin/mail-settings/test-mail", json={"to": "ops@samsung.com"}).json()
        assert r == {"ok": True, "to": "ops@samsung.com"}
        assert relay.sent[0]["subject"] == "[Supercom 포털] 메일 발송 설정 테스트"
        assert relay.sent[0]["content_type"] == "HTML"
        # 발송 방식이 stub 이어도 테스트 메일은 릴레이를 끝까지 태운다(바꾸기 전 확인용)
        assert admin.get("/api/admin/mail-settings").json()["backend"] == "stub"
        # 받는 사람 생략 = 요청한 관리자의 파생 주소
        r = admin.post("/api/admin/mail-settings/test-mail", json={}).json()
        assert r["to"] == "ops.admin@samsung.com"
        _no_secret_anywhere(db, admin)


def test_health_check_and_test_mail_report_reasons(admin, db):
    assert admin.post("/api/admin/mail-settings/health-check").json()["reason"] == "relay_misconfigured"   # 주소 없음
    with FakeRelay(token="other") as relay:
        _point_at(admin, relay)
        r = admin.post("/api/admin/mail-settings/test-mail", json={"to": "ops@samsung.com"}).json()
        assert r["ok"] is False and r["reason"] == "unauthorized"
    ev = db.query_one("SELECT message FROM events WHERE event_type = 'mail_test_failed'")
    assert "unauthorized" in ev["message"] and TOKEN not in ev["message"]
    r = admin.post("/api/admin/mail-settings/test-mail", json={"to": "not-an-email"})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_mail_recipient"


def test_unreadable_portal_token_is_reported_not_sent_empty(admin, db):
    Repositories(db).mail_settings.update({"relay_host": "127.0.0.1", "relay_port": 1,
                                           "relay_token_enc": seal_at_rest(TOKEN, secret="old-secret",
                                                                           purpose="mail_relay_token")}, actor="ops")
    v = admin.get("/api/admin/mail-settings").json()
    assert v["token"]["unreadable"] is True and v["token"]["configured"] is False
    r = admin.post("/api/admin/mail-settings/test-mail", json={"to": "a@samsung.com"}).json()
    assert r["reason"] == "relay_misconfigured" and "re-enter" in r["detail"]



# --- 2026-10-01 재리뷰: 주소-키 묶음 검사는 저장 트랜잭션 안에서 -----------------------------------

def test_update_guard_sees_the_locked_current_row_and_a_refusal_writes_nothing(db):
    repos = Repositories(db)
    repos.mail_settings.update({"relay_host": "10.0.0.1"}, actor="a")
    audits = len(db.query("SELECT 1 AS one FROM audit_log WHERE mutation_class = 'mail_settings'"))
    seen = []

    def refuse(row):
        seen.append(dict(row))
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        repos.mail_settings.update({"relay_host": "10.9.9.9"}, actor="b", guard=refuse)
    assert seen[0]["relay_host"] == "10.0.0.1"
    assert repos.mail_settings.get()["relay_host"] == "10.0.0.1"                 # 거절은 아무것도 안 쓴다
    assert len(db.query("SELECT 1 AS one FROM audit_log WHERE mutation_class = 'mail_settings'")) == audits
    # 행이 아직 없을 때도 guard 는 빈 행을 받고(만든 뒤 잠근다), 감사의 before 는 없음이다
    db.execute("DELETE FROM mail_settings")
    repos.mail_settings.update({"service_name": "X"}, actor="c", guard=lambda row: seen.append(dict(row)))
    assert seen[-1]["service_name"] is None and seen[-1]["id"] == 1
    last = db.query("SELECT before_state FROM audit_log WHERE mutation_class = 'mail_settings' ORDER BY id DESC")[0]
    assert last["before_state"] is None


def test_endpoint_token_rule_is_checked_against_the_row_at_write_time(admin, db):
    # 다른 관리자가 그 사이 키를 저장했다면, 키 없이 주소만 바꾸는 이 저장은 잠근 최신 행 기준으로 거절돼야 한다
    # (검사를 트랜잭션 밖에서 하면 키가 그 키를 입력한 적 없는 주소에 묶였다).
    repos = Repositories(db)
    assert admin.put("/api/admin/mail-settings", json={"relay_host": "10.0.0.1"}).status_code == 200
    real_update = repos.mail_settings.__class__.update

    def concurrent_token_save_then_update(self, changes, *, actor, guard=None):
        # 이 요청의 검사 직전에 다른 관리자가 키를 저장한 상황
        real_update(self, {"relay_token_enc": seal_token(TOKEN, admin.app.state.settings)}, actor="other")
        return real_update(self, changes, actor=actor, guard=guard)

    admin.app.state.repos.mail_settings.update = concurrent_token_save_then_update.__get__(
        admin.app.state.repos.mail_settings)
    r = admin.put("/api/admin/mail-settings", json={"relay_host": "10.6.6.6"})
    assert r.status_code == 422 and r.json()["detail"] == "mail_relay_token_required"
    assert repos.mail_settings.get()["relay_host"] == "10.0.0.1"



def test_stale_tab_token_save_is_refused(admin, db):
    # 3차 리뷰: 다른 관리자가 그 사이 주소를 바꿨는데 오래된 탭이 "키만 저장"하면 진짜 키가 그 탭이 본 적 없는 주소에
    # 묶였다. 포탈은 키를 보낼 때 화면이 보던 적용 주소(seen_relay_url)를 싣고, 다르면 409.
    assert admin.put("/api/admin/mail-settings", json={"relay_host": "10.20.30.40", "relay_token": "OLD"}).status_code == 200
    seen = admin.get("/api/admin/mail-settings").json()["relay_url"]
    assert seen == "http://10.20.30.40:8025"
    # 다른 관리자(같은 권한)가 주소를 바꾸고 키를 지운다
    assert admin.put("/api/admin/mail-settings", json={"relay_host": "10.6.6.6", "clear_relay_token": True}).status_code == 200
    r = admin.put("/api/admin/mail-settings", json={"relay_token": "REAL", "seen_relay_url": seen})
    assert r.status_code == 409 and r.json()["detail"] == "mail_settings_changed"
    v = admin.get("/api/admin/mail-settings").json()
    assert v["token"]["configured"] is False and v["relay_url"] == "http://10.6.6.6:8025"
    # 지금 주소를 보고 넣으면 저장된다
    r = admin.put("/api/admin/mail-settings", json={"relay_token": "REAL", "seen_relay_url": v["relay_url"]})
    assert r.status_code == 200 and r.json()["token"]["configured"] is True


def test_view_exposes_the_env_url_for_the_screen(db, settings):
    c = TestClient(create_app(replace(settings, mail_relay_url=" http://10.1.2.3:8025 "), db))
    Repositories(db).accounts.create("ops.admin", "pw", "admin")
    assert c.post("/api/auth/login", json={"username": "ops.admin", "password": "pw"}).status_code == 200
    v = c.get("/api/admin/mail-settings").json()
    assert v["env"]["relay_url"] == "http://10.1.2.3:8025"
