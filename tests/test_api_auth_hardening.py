"""웹 인증 하드닝(2026-09-07) API 테스트: 비밀번호 전송 봉인(다섯 경로 전수 -- 2026-11-03 관리자 비밀번호 재설정 추가)
+ 로그인 감속(아이디별 1분 10회 실패, 2026-11-03 IP 축 제거 · 아이디별 총 시도 상한 · 원 IP 는 기록·경보 전용).
conftest 의 client 픽스처는 봉인 정책이 꺼져 있어(평문 허용 -- 기존 테스트 수백 곳) 여기서는 라이브 기본
(required=True)을 켠 앱을 따로 만든다."""
from dataclasses import replace

import pytest
from dms.api.app import create_app
from dms.api.login_limiter import LoginRateLimiter
from dms.api.password_transport import PasswordTransport, seal_with_info
from dms.repositories import Repositories
from fastapi.testclient import TestClient


@pytest.fixture
def sealed_client(db, settings):
    """라이브 자세: 평문 password 거부, 인증번호 게이트도 켬."""
    return TestClient(create_app(replace(
        settings, password_encryption_required=True,
        account_verification_required=True), db))


def _key(client):
    r = client.get("/api/auth/transport-key")
    assert r.status_code == 200
    return r.json()


def _sealed(client, password, *, purpose, username):
    return seal_with_info(_key(client), password, purpose=purpose, username=username)


def _code(client, username, purpose):
    return client.post("/api/auth/verification-codes",
                       json={"username": username, "purpose": purpose}).json()["stub_code"]


# --- 키 배포 ---

def test_transport_key_is_public_and_derived_from_the_session_secret(client, settings):
    info = _key(client)
    expected = PasswordTransport(settings.session_secret)
    assert info == {"version": 1, "kid": expected.kid,
                    "public_key": expected.public_info()["public_key"]}
    assert _key(client) == info                          # 안정적(캐시 가능)


# --- 다섯 경로 전수: 봉인 수용 ---

def test_sealed_signup_login_reset_admin_paths_under_live_policy(sealed_client, db):
    c = sealed_client
    # signup(인증번호 + 봉인)
    code = _code(c, "alice", "signup")
    r = c.post("/api/auth/signup", json={
        "username": "alice", "code": code,
        "password_enc": _sealed(c, "pw1", purpose="signup", username="alice")})
    assert (r.status_code, r.json()) == (201, {"username": "alice"})
    # 저장은 여전히 scrypt 해시 -- 평문은 어디에도 없다
    row = db.query_one("SELECT password_hash FROM accounts WHERE username = 'alice'")
    assert row["password_hash"].startswith("scrypt$") and "pw1" not in row["password_hash"]
    # login(봉인)
    r = c.post("/api/auth/login", json={
        "username": "alice",
        "password_enc": _sealed(c, "pw1", purpose="login", username="alice")})
    assert (r.status_code, r.json()) == (200, {"actor": "alice", "role": "user"})
    assert c.get("/api/auth/me").json()["actor"] == "alice"
    c.post("/api/auth/logout")
    # 틀린 비밀번호를 봉인해도 401(봉인은 인증이 아니다)
    r = c.post("/api/auth/login", json={
        "username": "alice",
        "password_enc": _sealed(c, "nope", purpose="login", username="alice")})
    assert (r.status_code, r.json()["detail"]) == (401, "invalid_credentials")
    # password-reset(인증번호 + 봉인)
    code = _code(c, "alice", "password_reset")
    r = c.post("/api/auth/password-reset", json={
        "username": "alice", "code": code,
        "password_enc": _sealed(c, "pw2", purpose="password_reset", username="alice")})
    assert r.status_code == 200
    assert c.post("/api/auth/login", json={
        "username": "alice",
        "password_enc": _sealed(c, "pw2", purpose="login", username="alice")}).status_code == 200
    # admin accounts, 세션 admin 경로(봉인)
    Repositories(db).accounts.set_role("alice", "admin", actor="test")
    r = c.post("/api/admin/accounts", json={
        "username": "made.byalice", "role": "admin",
        "password_enc": _sealed(c, "pw3", purpose="admin_create", username="made.byalice")})
    assert (r.status_code, r.json()) == (201, {"username": "made.byalice", "role": "admin"})
    # 관리자 비밀번호 재설정(2026-11-03, 세션 admin 경로 -- 봉인 용도 admin_password_reset, AAD 사용자 = 대상)
    r = c.post("/api/admin/accounts/made.byalice/password-reset", json={
        "username": "made.byalice",
        "password_enc": _sealed(c, "pw4", purpose="admin_password_reset", username="made.byalice")})
    assert (r.status_code, r.json()["sessions_revoked"]) == (200, True)
    c.post("/api/auth/logout")
    assert c.post("/api/auth/login", json={
        "username": "made.byalice",
        "password_enc": _sealed(c, "pw3", purpose="login", username="made.byalice")}
    ).status_code == 401
    assert c.post("/api/auth/login", json={
        "username": "made.byalice",
        "password_enc": _sealed(c, "pw4", purpose="login", username="made.byalice")}
    ).status_code == 200


@pytest.mark.parametrize("path,extra,purpose", [
    ("/api/auth/login", {}, "login"),
    ("/api/auth/signup", {"code": "0000"}, "signup"),
    ("/api/auth/password-reset", {"code": "0000"}, "password_reset"),
])
def test_live_policy_rejects_plaintext_on_every_self_service_path(sealed_client, path, extra, purpose):
    # 인증번호보다 봉인 검사가 먼저다 -- 평문이면 코드가 맞든 틀리든 422 required.
    r = sealed_client.post(path, json={"username": "alice", "password": "pw", **extra})
    assert (r.status_code, r.json()["detail"]) == (422, "password_encryption_required")
    r = sealed_client.post(path, json={"username": "alice", **extra})
    assert (r.status_code, r.json()["detail"]) == (422, "password_missing")


def test_live_policy_rejects_plaintext_on_admin_session_path_but_not_token_bootstrap(
        sealed_client, client, db):
    c = sealed_client
    # 토큰 부트스트랩: 평문 허용(운영자 curl -- 토큰 보유자는 이미 admin)
    r = c.post("/api/admin/accounts", json={"username": "boss", "password": "pw"},
               headers={"x-admin-token": "tok-admin"})
    assert (r.status_code, r.json()["role"]) == (201, "admin")
    # 토큰 경로도 봉인은 그대로 받는다
    r = c.post("/api/admin/accounts", json={
        "username": "boss2",
        "password_enc": _sealed(c, "pw", purpose="admin_create", username="boss2")},
        headers={"x-admin-token": "tok-admin"})
    assert r.status_code == 201
    # 토큰 경로도 비밀번호 자체가 없으면 422
    r = c.post("/api/admin/accounts", json={"username": "boss3"},
               headers={"x-admin-token": "tok-admin"})
    assert (r.status_code, r.json()["detail"]) == (422, "password_missing")
    # 세션 admin 경로: 평문 거부
    c.post("/api/auth/login", json={
        "username": "boss", "password_enc": _sealed(c, "pw", purpose="login", username="boss")})
    r = c.post("/api/admin/accounts", json={"username": "x.y", "password": "pw"})
    assert (r.status_code, r.json()["detail"]) == (422, "password_encryption_required")
    assert db.query_one("SELECT 1 AS x FROM accounts WHERE username = 'x.y'") is None


def test_default_policy_accepts_both_plaintext_and_sealed(client):
    # conftest 기본(required=False): 기존 평문 픽스처가 살고 봉인도 받는다.
    assert client.post("/api/auth/signup", json={"username": "p", "password": "pw"}).status_code == 201
    assert client.post("/api/auth/signup", json={
        "username": "s", "password_enc": _sealed(client, "pw", purpose="signup", username="s")}
    ).status_code == 201
    assert client.post("/api/auth/login", json={"username": "p", "password": "pw"}).status_code == 200
    assert client.post("/api/auth/login", json={
        "username": "s", "password_enc": _sealed(client, "pw", purpose="login", username="s")}
    ).status_code == 200


# --- 봉인 오류 사유 ---

def test_wrong_purpose_or_username_binding_is_invalid(sealed_client):
    c = sealed_client
    c.post("/api/admin/accounts", json={"username": "alice", "password": "pw"},
           headers={"x-admin-token": "tok-admin"})
    for purpose, username in (("signup", "alice"), ("login", "bob")):
        r = c.post("/api/auth/login", json={
            "username": "alice",
            "password_enc": _sealed(c, "pw", purpose=purpose, username=username)})
        assert (r.status_code, r.json()["detail"]) == (422, "password_encryption_invalid")


def test_stale_key_is_reported_as_key_mismatch(sealed_client):
    other = PasswordTransport("rotated-secret")
    r = sealed_client.post("/api/auth/login", json={
        "username": "alice",
        "password_enc": seal_with_info(other.public_info(), "pw",
                                       purpose="login", username="alice")})
    assert (r.status_code, r.json()["detail"]) == (422, "password_encryption_key_mismatch")


def test_malformed_seal_is_422_not_500(sealed_client):
    r = sealed_client.post("/api/auth/login", json={
        "username": "alice",
        "password_enc": {"version": 1, "kid": _key(sealed_client)["kid"],
                         "epk": "!!", "iv": "!!", "ct": "!!"}})
    assert (r.status_code, r.json()["detail"]) == (422, "password_encryption_invalid")


def test_seal_failure_does_not_consume_the_verification_code(sealed_client):
    # 순서 계약: 봉인을 먼저 연다 -- 깨진 봉인으로 유효한 코드를 태우지 않는다.
    c = sealed_client
    code = _code(c, "newbie", "signup")
    bad = _sealed(c, "pw", purpose="login", username="newbie")   # 용도 불일치
    r = c.post("/api/auth/signup", json={"username": "newbie", "code": code, "password_enc": bad})
    assert r.json()["detail"] == "password_encryption_invalid"
    r = c.post("/api/auth/signup", json={
        "username": "newbie", "code": code,
        "password_enc": _sealed(c, "pw", purpose="signup", username="newbie")})
    assert r.status_code == 201


def test_validation_errors_do_not_echo_the_request_body(client):
    # FastAPI 기본 422 는 오류 항목의 input 에 본문을 되돌린다 -- 비밀번호가 실린
    # 형식 오류 요청의 응답에 평문이 에코되지 않아야 한다.
    r = client.post("/api/auth/login", json={"username": 5, "password": "s3cret-value"})
    assert r.status_code == 422
    assert "s3cret-value" not in r.text
    assert all(set(item) <= {"type", "loc", "msg"} for item in r.json()["detail"])


# --- 로그인 감속 ---

def _seed(client, username="alice", password="pw"):
    client.post("/api/auth/signup", json={"username": username, "password": password})


def _login(client, username, password, ip=None, headers=None):
    # 원 IP 는 X-Forwarded-For 마지막 항목(2026-11-03 -- X-Real-IP 는 읽지 않는다). 기록·경보 전용이라 판정을 바꾸지 않는다.
    h = dict(headers or {})
    if ip:
        h["x-forwarded-for"] = ip
    return client.post("/api/auth/login", json={"username": username, "password": password}, headers=h)


def _events(db, event_type):
    from dms.db import load_json
    return [{**r, "payload": load_json(r["payload"])}
            for r in db.query("SELECT message, payload FROM events WHERE event_type = :t ORDER BY id",
                              {"t": event_type})]


def test_ten_failures_per_minute_then_429_even_with_the_right_password(client, db):
    _seed(client)
    for _ in range(10):
        assert _login(client, "alice", "wrong", ip="10.0.0.5").status_code == 401
    r = _login(client, "alice", "pw")
    assert (r.status_code, r.json()["detail"]) == (429, "login_rate_limited")
    assert 1 <= int(r.headers["retry-after"]) <= 60
    # 상한 도달 이벤트는 키당 한 번 -- 키는 아이디 하나(2026-11-03 IP 키 삭제)
    rows = _events(db, "login_rate_limited")
    assert len(rows) == 1
    assert rows[0]["message"].startswith("user:alice")
    assert rows[0]["payload"]["username"] == "alice"
    assert rows[0]["payload"]["client_ip"] == "10.0.0.5"          # 출발지는 기록만 한다
    assert rows[0]["payload"]["peer"] == "testclient"
    assert (rows[0]["payload"]["limit"], rows[0]["payload"]["window_seconds"]) == (10, 60)
    # 거절된 시도는 세지 않고 이벤트도 늘리지 않는다
    _login(client, "alice", "pw")
    assert len(_events(db, "login_rate_limited")) == 1


def test_the_window_slides_and_releases(client):
    clock = [1000.0]
    client.app.state.login_limiter = LoginRateLimiter(10, 60, clock=lambda: clock[0])
    _seed(client)
    for _ in range(10):
        _login(client, "alice", "wrong")
    assert _login(client, "alice", "pw").status_code == 429
    clock[0] += 60
    assert _login(client, "alice", "pw").status_code == 200


def test_success_clears_the_username_counter(client):
    _seed(client)
    for _ in range(9):
        _login(client, "alice", "wrong", ip="10.0.0.1")
    assert _login(client, "alice", "pw", ip="10.0.0.1").status_code == 200
    # 성공으로 user 키가 비었으니 다시 9번 실패해도 아직 막히지 않는다(같은 IP 여도 -- IP 키는 없다)
    for _ in range(9):
        assert _login(client, "alice", "wrong", ip="10.0.0.1").status_code == 401
    assert _login(client, "alice", "pw", ip="10.0.0.1").status_code == 200


def test_one_source_failing_many_accounts_does_not_block_others_but_raises_an_alarm(db, settings):
    # 2026-11-03: IP 축 삭제 -- SNAT 뒤 전사가 한 IP 라 한 사람의 오타·스크립트가 전원의 로그인을 막았다. 같은 출발지에서
    # 10개 아이디가 실패해도 다른 아이디의 로그인은 200 이고, 대신 출발지 경보(이벤트만)가 창에 한 번 남는다.
    c = TestClient(create_app(replace(settings, login_alarm_source_failures=5), db))
    _seed(c, "victim", "pw")
    for i in range(10):
        assert _login(c, f"ghost{i}", "x", ip="10.9.9.9").status_code == 401
    assert _login(c, "victim", "pw", ip="10.9.9.9").status_code == 200
    rows = _events(db, "login_source_alarm")
    assert len(rows) == 1                                           # (종류, IP)마다 창에 한 번
    p = rows[0]["payload"]
    assert (p["client_ip"], p["count"], p["threshold"], p["window_seconds"]) == ("10.9.9.9", 5, 5, 600)
    assert p["last_username"] == "ghost4"
    assert "10.9.9.9" in rows[0]["message"]
    assert _events(db, "login_rate_limited") == []                  # 아이디마다 1회 실패 -- 감속 없음


def test_source_alarms_are_capped_per_window_and_the_last_one_says_so(db, settings):
    # 2026-11-03 리뷰: 원 IP 를 클라이언트가 고를 수 있는 배치(직접 접속)에서 위조 IP 를 바꿔 가며 실패하면 출발지 경보가
    # 출발지마다 한 건씩 끝없이 쌓였다 -- 창당 몫(LoginAlarms.account_event_budget)으로 묶고, 마지막 건에 안내를 붙인다.
    c = TestClient(create_app(replace(settings, login_alarm_source_failures=1), db))
    c.app.state.login_alarms.account_event_budget = 2
    for i in range(5):
        assert _login(c, f"ghost{i}", "x", ip=f"10.66.0.{i}").status_code == 401     # 차단은 없다(경보만)
    rows = _events(db, "login_source_alarm")
    assert [r["payload"]["client_ip"] for r in rows] == ["10.66.0.0", "10.66.0.1"]
    assert "capped" not in rows[0]["message"]
    assert "[per-source alarms capped at 2 per 600s" in rows[1]["message"]
    assert all("budget_left" not in r["payload"] for r in rows)          # 몫은 메시지 안내뿐 -- payload 모양은 그대로
    assert len(_events(db, "login_failure_alarm")) == 0                  # (임계 30 -- 전체 실패는 계속 센다)
    assert c.app.state.login_alarms.snapshot()["failures"] == 5


def test_source_alarm_message_says_unknown_when_the_origin_is_unknown(db, settings):
    # 2026-11-03 리뷰: 원 IP 를 모르면(피어도 없음) 메시지가 「from None」이었다. payload 는 null(모름) 그대로 둔다.
    import anyio
    import httpx
    app = create_app(replace(settings, login_alarm_source_failures=1), db)

    async def go():
        transport = httpx.ASGITransport(app=app, client=None)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/auth/login", json={"username": "ghost", "password": "x"})
            assert r.status_code == 401

    anyio.run(go)
    [row] = _events(db, "login_source_alarm")
    assert "failed logins from unknown within" in row["message"]
    assert "None" not in row["message"]
    assert row["payload"]["client_ip"] is None and row["payload"]["peer"] is None


def test_username_key_stops_targeting_from_many_ips(client):
    _seed(client, "victim", "pw")
    for i in range(10):
        _login(client, "victim", "x", ip=f"10.1.0.{i}")
    assert _login(client, "victim", "pw", ip="10.1.0.99").status_code == 429


def test_x_forwarded_for_last_hop_is_recorded_as_the_client_ip(client, db):
    _seed(client)
    # 첫 항목은 클라이언트가 심을 수 있다 -- 마지막(앞단 프록시가 덧붙인) 항목이 원 IP 다. 기록만 하고 막지 않는다.
    for _ in range(10):
        client.post("/api/auth/login", json={"username": "alice", "password": "x"},
                    headers={"x-forwarded-for": "1.1.1.1, 203.0.113.7"})
    r = client.post("/api/auth/login", json={"username": "bob", "password": "x"},
                    headers={"x-forwarded-for": "9.9.9.9, 203.0.113.7"})
    assert r.status_code == 401                                     # 다른 아이디는 같은 출발지여도 막히지 않는다
    ev = _events(db, "login_rate_limited")
    assert len(ev) == 1 and ev[0]["payload"]["client_ip"] == "203.0.113.7"


def test_forged_x_real_ip_is_never_recorded(client, db):
    # X-Real-IP 는 읽지 않는다(2026-11-03): Envoy 의 제거 정책이 빠지면 위조값이 그대로 온다.
    _seed(client)
    for _ in range(10):
        client.post("/api/auth/login", json={"username": "alice", "password": "x"},
                    headers={"x-real-ip": "6.6.6.6"})
    ev = _events(db, "login_rate_limited")
    assert len(ev) == 1 and ev[0]["payload"]["client_ip"] == "testclient"
    assert "6.6.6.6" not in str(db.query("SELECT message, payload FROM events"))
    # XFF 와 함께 와도 X-Real-IP 는 무시하고 XFF 마지막 항목
    client.post("/api/auth/logout")
    _seed(client, "carol", "pw")
    for _ in range(10):
        client.post("/api/auth/login", json={"username": "carol", "password": "x"},
                    headers={"x-real-ip": "6.6.6.6", "x-forwarded-for": "198.51.100.4"})
    ev = _events(db, "login_rate_limited")
    assert ev[-1]["payload"]["client_ip"] == "198.51.100.4"


def test_non_ip_last_forwarded_hop_falls_back_to_the_peer(client, db):
    _seed(client)
    for _ in range(10):
        client.post("/api/auth/login", json={"username": "alice", "password": "x"},
                    headers={"x-forwarded-for": "10.0.0.1, not-an-ip"})
    ev = _events(db, "login_rate_limited")
    assert ev[0]["payload"]["client_ip"] == "testclient"


def test_per_id_total_attempt_cap_counts_successes(db, settings):
    # 아이디별 총 시도 상한(성공 포함, 2026-11-03): 맞는 비밀번호 반복으로 scrypt 를 태우는 경로. 넘으면 같은 429
    # login_rate_limited, 운영자 구분은 이벤트 종류(login_attempt_limited). 다른 아이디는 영향 없음.
    c = TestClient(create_app(replace(settings, login_id_attempt_limit=3), db))
    _seed(c, "alice", "pw")
    _seed(c, "bob", "pw")
    for _ in range(3):
        assert _login(c, "alice", "pw").status_code == 200
    r = _login(c, "alice", "pw")
    assert (r.status_code, r.json()["detail"]) == (429, "login_rate_limited")
    assert 1 <= int(r.headers["retry-after"]) <= 60
    ev = _events(db, "login_attempt_limited")
    assert len(ev) == 1 and ev[0]["payload"]["username"] == "alice"
    assert (ev[0]["payload"]["limit"], ev[0]["payload"]["window_seconds"]) == (3, 60)
    assert _events(db, "login_rate_limited") == []                  # 실패 상한과는 다른 이벤트
    assert _login(c, "bob", "pw").status_code == 200


def test_per_id_total_attempt_cap_is_off_by_default_in_direct_settings(client):
    # dataclass 기본 0(두 층 기본값) -- 직접 만든 Settings 의 테스트가 한 사용자로 수십 번 로그인해도 잠기지 않는다.
    _seed(client)
    for _ in range(70):
        assert _login(client, "alice", "pw").status_code == 200


def test_steady_state_at_the_attempt_cap_logs_one_event_not_one_per_slip(db, settings):
    # 2026-11-03 리뷰: 상한에 한 번 닿은 뒤 가장 오래된 시도가 창 밖으로 빠질 때마다 한 건이 다시 받아들여지고, 예전엔 그때마다
    # login_attempt_limited 가 남았다(맞는 비밀번호를 1초에 한 번 넘게 반복하는 자동화 하나로 하루 약 86,400행).
    clock = [0.0]
    c = TestClient(create_app(replace(settings, login_id_attempt_limit=3), db))
    c.app.state.login_attempt_limiter = LoginRateLimiter(3, 10, clock=lambda: clock[0])
    _seed(c, "bot", "pw")
    codes = []
    for t in range(40):
        clock[0] = float(t)
        codes.append(_login(c, "bot", "pw").status_code)
    assert (codes.count(200), codes.count(429)) == (12, 28)       # 판정은 그대로(창마다 3회)
    assert len(_events(db, "login_attempt_limited")) == 1


def test_oversized_username_is_refused_before_any_counter_or_event(db, settings):
    # 2026-11-03 리뷰: IP 축이 사라진 뒤 출발지 하나가 아무 길이의 아이디로 두 감속기 키(창 동안 메모리)와 이벤트를 부풀릴 수
    # 있었다(1MiB 아이디 약 50건 = api 메모리 상한 1Gi). 계정 아이디는 최대 64자라 128자 초과는 카운터 없이 401.
    c = TestClient(create_app(replace(settings, login_id_attempt_limit=60, login_alarm_failures=1,
                                      login_alarm_attempts=1, login_alarm_source_failures=1), db))
    st = c.app.state
    big = "a" * (1 << 20)
    for _ in range(3):
        r = c.post("/api/auth/login", json={"username": big, "password": "x"})
        assert (r.status_code, r.json()["detail"]) == (401, "invalid_credentials")
    r = c.post("/api/auth/login", json={"username": "c" * 129, "password": "x"})
    assert (r.status_code, r.json()["detail"]) == (401, "invalid_credentials")
    assert st.login_limiter._failures == {} and st.login_attempt_limiter._failures == {}
    assert st.login_alarms.snapshot() == {"failures": 0, "attempts": 0}
    assert db.query("SELECT event_type FROM events WHERE component = 'auth'") == []
    # 경계: 128자는 평소 경로(없는 아이디 = 401, 실패 상한·경보에 센다)
    edge = "b" * 128
    assert _login(c, edge, "x").status_code == 401
    assert st.login_limiter.failures(f"user:{edge}") == 1
    assert st.login_alarms.snapshot() == {"failures": 1, "attempts": 1}


def test_spraying_many_accounts_caps_per_account_events_but_not_the_429(client, db):
    # 2026-11-03 리뷰: 아이디는 공격자가 고르는 키 공간이라 「아이디마다 한 번」만으로는 events 가 묶이지 않는다(없는 아이디
    # 100개 × 10회 실패 = 0.6초에 login_rate_limited 100건). 종류별 창당 몫(account_event_budget) 안에서만 남기고, 마지막
    # 이벤트에 몫을 다 썼다고 적는다. 429 판정은 그대로다.
    client.app.state.login_alarms.account_event_budget = 3
    for i in range(5):
        for _ in range(10):
            assert _login(client, f"ghost{i}", "x", ip="10.9.9.9").status_code == 401
        assert _login(client, f"ghost{i}", "x").status_code == 429
    ev = _events(db, "login_rate_limited")
    assert [e["payload"]["username"] for e in ev] == ["ghost0", "ghost1", "ghost2"]
    assert "capped at 3 per 600s" in ev[-1]["message"]
    assert "capped" not in ev[0]["message"]


def test_self_service_reset_clears_the_failure_counter_like_the_admin_reset(client):
    # 2026-11-03 리뷰: 셀프 재설정은 대개 여러 번 틀린 뒤에 한다 -- 비우지 않으면 재설정에 성공하고도 새 비밀번호 첫 로그인이
    # 429 였다(관리자 재설정은 이미 비운다). 인증번호 소비를 거친 뒤라 추측 감속 우회가 아니다.
    _seed(client, "alice", "old")
    for _ in range(10):
        assert _login(client, "alice", "bad").status_code == 401
    assert _login(client, "alice", "old").status_code == 429
    code = _code(client, "alice", "password_reset")
    r = client.post("/api/auth/password-reset", json={"username": "alice", "password": "new", "code": code})
    assert r.status_code == 200
    assert _login(client, "alice", "new").status_code == 200


def test_seal_errors_do_not_count_as_failed_attempts(sealed_client):
    c = sealed_client
    c.post("/api/admin/accounts", json={"username": "alice", "password": "pw"},
           headers={"x-admin-token": "tok-admin"})
    for _ in range(12):
        r = c.post("/api/auth/login", json={"username": "alice", "password": "pw"})
        assert r.status_code == 422
    assert c.post("/api/auth/login", json={
        "username": "alice", "password_enc": _sealed(c, "pw", purpose="login", username="alice")}
    ).status_code == 200


def test_limiter_can_be_disabled_explicitly(db, settings):
    c = TestClient(create_app(replace(settings, login_rate_limit_attempts=0), db))
    _seed(c)
    for _ in range(25):
        assert _login(c, "alice", "wrong").status_code == 401
    assert _login(c, "alice", "pw").status_code == 200
