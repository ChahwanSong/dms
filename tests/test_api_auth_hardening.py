"""웹 인증 하드닝(2026-09-07) API 테스트: 비밀번호 전송 봉인(네 경로 전수) + 로그인
감속(1분 10회). conftest 의 client 픽스처는 봉인 정책이 꺼져 있어(평문 허용 --
기존 테스트 수백 곳) 여기서는 라이브 기본(required=True)을 켠 앱을 따로 만든다."""
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


# --- 네 경로 전수: 봉인 수용 ---

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
    c.post("/api/auth/logout")
    assert c.post("/api/auth/login", json={
        "username": "made.byalice",
        "password_enc": _sealed(c, "pw3", purpose="login", username="made.byalice")}
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


def _login(client, username, password, ip=None):
    headers = {"x-real-ip": ip} if ip else {}
    return client.post("/api/auth/login", json={"username": username, "password": password},
                       headers=headers)


def test_ten_failures_per_minute_then_429_even_with_the_right_password(client, db):
    _seed(client)
    for _ in range(10):
        assert _login(client, "alice", "wrong").status_code == 401
    r = _login(client, "alice", "pw")
    assert (r.status_code, r.json()["detail"]) == (429, "login_rate_limited")
    assert 1 <= int(r.headers["retry-after"]) <= 60
    # 상한 도달 이벤트는 키당 한 번(user 키 + ip 키)
    rows = db.query("SELECT message FROM events WHERE event_type = 'login_rate_limited'")
    assert len(rows) == 2
    assert any(m["message"].startswith("user:alice") for m in rows)
    assert any(m["message"].startswith("ip:") for m in rows)
    # 거절된 시도는 세지 않고 이벤트도 늘리지 않는다
    _login(client, "alice", "pw")
    assert len(db.query("SELECT 1 AS x FROM events WHERE event_type = 'login_rate_limited'")) == 2


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
    # 성공으로 user 키가 비었으니 다시 9번 실패해도 아직 막히지 않는다(다른 IP 로
    # 해서 ip 키 누적을 분리)
    for _ in range(9):
        assert _login(client, "alice", "wrong", ip="10.0.0.2").status_code == 401
    assert _login(client, "alice", "pw", ip="10.0.0.2").status_code == 200


def test_ip_key_stops_password_spraying_across_usernames(client):
    _seed(client, "victim", "pw")
    for i in range(10):
        assert _login(client, f"ghost{i}", "x", ip="10.9.9.9").status_code == 401
    assert _login(client, "victim", "pw", ip="10.9.9.9").status_code == 429
    # 다른 IP 에서는 그 사용자가 막히지 않았다(user 키는 깨끗)
    assert _login(client, "victim", "pw", ip="10.9.9.8").status_code == 200


def test_username_key_stops_targeting_from_many_ips(client):
    _seed(client, "victim", "pw")
    for i in range(10):
        _login(client, "victim", "x", ip=f"10.1.0.{i}")
    assert _login(client, "victim", "pw", ip="10.1.0.99").status_code == 429


def test_x_forwarded_for_last_hop_is_used_when_no_real_ip(client):
    _seed(client)
    # 첫 항목은 클라이언트가 심을 수 있다 -- 마지막(신뢰 프록시가 덧붙인) 항목이 키다
    for i in range(10):
        client.post("/api/auth/login", json={"username": f"u{i}", "password": "x"},
                    headers={"x-forwarded-for": f"1.1.1.{i}, 203.0.113.7"})
    r = client.post("/api/auth/login", json={"username": "alice", "password": "pw"},
                    headers={"x-forwarded-for": "9.9.9.9, 203.0.113.7"})
    assert r.status_code == 429


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
