"""인증 메일(Knox 릴레이, 2026-10-01) -- routes_auth.request_verification_code 의 knox_relay 경로를 가짜 릴레이
(tests/fake_knox_relay.py)에 붙여 가입·비밀번호 재설정을 끝까지 완주한다. 실제 사내 릴레이·Knox 는 테스트베드
밖이라 여기서는 mailer.py 가 기대하는 계약까지만 증명한다."""
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from dms.api.app import create_app
from dms.repositories import Repositories
from fake_knox_relay import FakeRelay

TOKEN = "relay-token-abc"


@pytest.fixture
def relay():
    r = FakeRelay(token=TOKEN)
    r.start()
    yield r
    r.stop()


def _app(db, settings, relay=None, **over):
    base = {"account_verification_required": True, "mailer_backend": "knox_relay",
            "mail_relay_url": relay.base_url if relay else "", "mail_relay_token": TOKEN}
    return TestClient(create_app(replace(settings, **{**base, **over}), db))


def _code_from(mail) -> str:
    return re.search(r"letter-spacing:14px;color:#191f28;\">(\d{4})</td>", mail["body"]).group(1)


def test_signup_through_the_relay_without_echoing_the_code(db, settings, relay):
    c = _app(db, settings, relay)
    r = c.post("/api/auth/verification-codes", json={"username": "cocoa.song", "purpose": "signup"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"email": "cocoa.song@samsung.com", "expires_in_seconds": 300}   # stub_code 없음
    mail = relay.sent[0]
    assert mail["to"] == ["cocoa.song@samsung.com"] and mail["content_type"] == "HTML"
    assert mail["subject"] == "[Supercom 포털] 회원가입 인증번호 안내"
    code = _code_from(mail)
    assert code not in mail["subject"]
    ev = db.query_one("SELECT message FROM events WHERE event_type = 'verification_email_sent'")
    assert "cocoa.song@samsung.com" in ev["message"] and code not in ev["message"]
    # 메일로 받은 코드로 가입 완주
    assert c.post("/api/auth/signup", json={"username": "cocoa.song", "password": "pw",
                                            "code": code}).status_code == 201
    assert c.post("/api/auth/login", json={"username": "cocoa.song", "password": "pw"}).status_code == 200


def test_password_reset_through_the_relay(db, settings, relay):
    Repositories(db).accounts.create("rst.user", "old", "user")
    c = _app(db, settings, relay)
    assert c.post("/api/auth/verification-codes",
                  json={"username": "rst.user", "purpose": "password_reset"}).status_code == 200
    mail = relay.sent[-1]
    assert mail["subject"] == "[Supercom 포털] 비밀번호 재설정 인증번호 안내"
    assert c.post("/api/auth/password-reset", json={"username": "rst.user", "password": "new",
                                                    "code": _code_from(mail)}).status_code == 200
    assert c.post("/api/auth/login", json={"username": "rst.user", "password": "new"}).status_code == 200


def test_throttle_blocks_before_issuing_so_the_mailed_code_survives(db, settings, relay):
    c = _app(db, settings, relay)
    for _ in range(5):
        assert c.post("/api/auth/verification-codes",
                      json={"username": "spam.me", "purpose": "signup"}).status_code == 200
    last = _code_from(relay.sent[-1])
    r = c.post("/api/auth/verification-codes", json={"username": "spam.me", "purpose": "signup"})
    assert r.status_code == 429 and r.json()["detail"] == "verification_rate_limited"
    assert 0 < int(r.headers["Retry-After"]) <= 601
    assert len(relay.sent) == 5                                           # 6번째는 보내지 않았다
    ev = db.query_one("SELECT message FROM events WHERE event_type = 'verification_email_throttled'")
    assert "spam.me@samsung.com" in ev["message"]
    # 발급 전에 막았으니 메일함의 마지막 코드가 그대로 유효하다
    assert c.post("/api/auth/signup", json={"username": "spam.me", "password": "pw",
                                            "code": last}).status_code == 201


def test_relay_rate_limit_maps_to_429_with_its_retry_after(db, settings):
    with FakeRelay(token=TOKEN, rate_limit=1, window=600) as relay:
        c = _app(db, settings, relay)
        assert c.post("/api/auth/verification-codes",
                      json={"username": "a.b", "purpose": "signup"}).status_code == 200
        first = _code_from(relay.sent[0])
        r = c.post("/api/auth/verification-codes", json={"username": "a.b", "purpose": "signup"})
        assert r.status_code == 429 and r.json()["detail"] == "verification_rate_limited"
        # 릴레이가 준 값(≈600초)을 그대로 -- 참고 구현의 기본 60 이 아니다
        assert 60 < int(r.headers["Retry-After"]) <= 601
        # 릴레이가 막아 새 코드는 저장되지 않았다 -- 메일함의 첫 코드가 그대로 유효(발송 성공 뒤에만 저장)
        assert c.post("/api/auth/signup", json={"username": "a.b", "password": "pw",
                                                "code": first}).status_code == 201


@pytest.mark.parametrize("relay_kwargs,reason", [
    ({"token": "not-the-same"}, "unauthorized"),
    ({"token": TOKEN, "knox_mode": "reject"}, "knox_rejected"),
    ({"token": TOKEN, "allowed_domains": {"corp.example"}}, "recipient_not_allowed"),
])
def test_send_failure_is_502_and_never_pretends(db, settings, relay_kwargs, reason):
    with FakeRelay(**relay_kwargs) as relay:
        c = _app(db, settings, relay)
        r = c.post("/api/auth/verification-codes", json={"username": "x.y", "purpose": "signup"})
        assert r.status_code == 502 and r.json()["detail"] == "verification_email_failed"
        assert "stub_code" not in r.text
    ev = db.query_one("SELECT message FROM events WHERE event_type = 'verification_email_failed'")
    assert reason in ev["message"] and TOKEN not in ev["message"]
    # 발송이 실패하면 코드를 저장하지 않는다(발송 성공 뒤에만 저장) -- 메일로도 화면으로도 안 간 코드다
    assert db.query("SELECT 1 AS one FROM verification_codes WHERE username = 'x.y'") == []


def test_a_failed_send_keeps_the_code_already_in_the_mailbox(db, settings):
    with FakeRelay(token=TOKEN) as relay:
        c = _app(db, settings, relay)
        assert c.post("/api/auth/verification-codes",
                      json={"username": "keep.me", "purpose": "signup"}).status_code == 200
        mailed = _code_from(relay.sent[0])
        relay.knox_mode = "reject"                                   # 두 번째 요청은 Knox 가 거부
        assert c.post("/api/auth/verification-codes",
                      json={"username": "keep.me", "purpose": "signup"}).status_code == 502
        assert c.post("/api/auth/signup", json={"username": "keep.me", "password": "pw",
                                                "code": mailed}).status_code == 201


def test_unreachable_relay_is_502(db, settings):
    c = _app(db, settings, None, mail_relay_url="http://127.0.0.1:1")
    r = c.post("/api/auth/verification-codes", json={"username": "x.z", "purpose": "signup"})
    assert r.status_code == 502
    ev = db.query_one("SELECT message FROM events WHERE event_type = 'verification_email_failed'")
    assert "relay_unreachable" in ev["message"]


@pytest.mark.parametrize("backend", ["smtp", ""])
def test_misconfigured_backend_refuses_before_issuing_a_code(db, settings, backend):
    # 빈 값도 오타와 같다(조용히 stub 이 되면 인증번호가 화면에 에코된다) -- 참고 구현과 같은 500
    c = _app(db, settings, None, mailer_backend=backend)
    r = c.post("/api/auth/verification-codes", json={"username": "q.q", "purpose": "signup"})
    assert r.status_code == 500 and r.json()["detail"] == "mailer_misconfigured"
    assert db.query("SELECT 1 AS one FROM verification_codes") == []
    # 순서: 발송 방식 검사가 목적·아이디 검사보다 먼저(참고 구현)
    r = c.post("/api/auth/verification-codes", json={"username": "Bad:Name", "purpose": "hack"})
    assert r.status_code == 500 and r.json()["detail"] == "mailer_misconfigured"


def test_per_client_cap_and_concurrency_slots(db, settings, relay):
    import threading
    from dms.api.routes_auth import VERIFICATION_MAIL_PER_CLIENT_LIMIT
    c = _app(db, settings, relay)
    for i in range(VERIFICATION_MAIL_PER_CLIENT_LIMIT):
        assert c.post("/api/auth/verification-codes",
                      json={"username": f"emp{i}", "purpose": "signup"}).status_code == 200
    r = c.post("/api/auth/verification-codes", json={"username": "emp-next", "purpose": "signup"})
    assert r.status_code == 429 and r.json()["detail"] == "verification_rate_limited"
    assert len(relay.sent) == VERIFICATION_MAIL_PER_CLIENT_LIMIT
    # 동시 발송 슬롯이 다 차 있으면 기다리지 않고 429(공유 스레드풀을 릴레이 장애에 묶지 않는다)
    c2 = _app(db, settings, relay)
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    c2.app.state.mail_send_slots = slots
    r = c2.post("/api/auth/verification-codes", json={"username": "busy.one", "purpose": "signup"})
    assert r.status_code == 429 and r.headers["Retry-After"] == "10"


def test_portal_settings_switch_delivery_without_restart(db, settings, relay):
    # env 는 stub -- 포탈에서 knox_relay·주소·토큰을 넣으면 다음 요청부터 메일로 간다(DB > env, 재시작 없음)
    c = TestClient(create_app(replace(settings, account_verification_required=True), db))
    assert "stub_code" in c.post("/api/auth/verification-codes",
                                 json={"username": "sw.itch", "purpose": "signup"}).json()
    host, port = relay.base_url.rsplit("//", 1)[1].rsplit(":", 1)
    Repositories(db).accounts.create("ops.admin", "pw", "admin")          # 설정 변경은 세션 관리자만
    admin = TestClient(c.app)
    assert admin.post("/api/auth/login", json={"username": "ops.admin", "password": "pw"}).status_code == 200
    assert admin.put("/api/admin/mail-settings", json={
        "backend": "knox_relay", "relay_host": host, "relay_port": int(port),
        "relay_token": TOKEN}).status_code == 200
    r = c.post("/api/auth/verification-codes", json={"username": "sw.itch", "purpose": "signup"})
    assert r.status_code == 200 and "stub_code" not in r.json()
    assert relay.sent[-1]["to"] == ["sw.itch@samsung.com"]
    assert c.get("/api/auth/mail-info").json() == {"email_domain": "samsung.com", "delivery": "knox_relay"}


def test_mail_info_reports_domain_and_delivery(db, settings):
    c = TestClient(create_app(replace(settings, account_email_domain="corp.example"), db))
    assert c.get("/api/auth/mail-info").json() == {"email_domain": "corp.example", "delivery": "stub"}


# --- 2026-10-01 재리뷰 ------------------------------------------------------------------------

def test_rejected_requests_do_not_burn_other_quotas(db, settings, relay):
    # 거절된 요청은 어느 상한에도 기록되지 않는다 -- 예전엔 수신자 상한을 먼저 기록해, 자기 IP 상한을 다 쓴
    # 공격자가 메일 한 통 없이 남의 재설정을 10분씩 막았다.
    import threading
    from dms.api.routes_auth import VERIFICATION_MAIL_PER_CLIENT_LIMIT
    Repositories(db).accounts.create("victim.one", "pw", "user")
    c = _app(db, settings, relay)
    attacker = {"x-real-ip": "10.66.66.66"}
    for i in range(VERIFICATION_MAIL_PER_CLIENT_LIMIT):
        assert c.post("/api/auth/verification-codes", headers=attacker,
                      json={"username": f"att{i}", "purpose": "signup"}).status_code == 200
    sent = len(relay.sent)
    for _ in range(8):
        r = c.post("/api/auth/verification-codes", headers=attacker,
                   json={"username": "victim.one", "purpose": "password_reset"})
        assert r.status_code == 429
    assert len(relay.sent) == sent                                         # 메일 0통
    assert c.app.state.mail_throttle.peek("victim.one@samsung.com") == 0   # 피해자 몫은 그대로
    assert "victim.one@samsung.com" not in c.app.state.mail_throttle._hits
    # 슬롯이 다 차서 거절된 요청도 상한을 태우지 않는다
    real = c.app.state.mail_send_slots
    busy = threading.BoundedSemaphore(1)
    busy.acquire()
    c.app.state.mail_send_slots = busy
    for _ in range(6):
        assert c.post("/api/auth/verification-codes", headers={"x-real-ip": "10.1.1.1"},
                      json={"username": "victim.one", "purpose": "password_reset"}).status_code == 429
    c.app.state.mail_send_slots = real
    r = c.post("/api/auth/verification-codes", headers={"x-real-ip": "10.1.1.1"},
               json={"username": "victim.one", "purpose": "password_reset"})
    assert r.status_code == 200 and relay.sent[-1]["to"] == ["victim.one@samsung.com"]


def test_throttled_events_are_written_once_per_limit_and_key(db, settings, relay):
    # 무인증 경로라 거절마다 events 를 쓰면 상한에 걸린 요청이 테이블을 무제한으로 채운다.
    c = _app(db, settings, relay)
    for _ in range(5):
        c.post("/api/auth/verification-codes", json={"username": "ev.one", "purpose": "signup"})
    for _ in range(12):
        assert c.post("/api/auth/verification-codes",
                      json={"username": "ev.one", "purpose": "signup"}).status_code == 429
    rows = db.query("SELECT message FROM events WHERE event_type = 'verification_email_throttled'")
    assert len(rows) == 1 and "ev.one@samsung.com" in rows[0]["message"]


def test_global_cap(db, settings, relay):
    from dms.api.mailer import SendThrottle
    c = _app(db, settings, relay)
    c.app.state.mail_throttle_global = SendThrottle(2, 600)
    for i in range(2):
        assert c.post("/api/auth/verification-codes", headers={"x-real-ip": f"10.2.0.{i}"},
                      json={"username": f"g{i}", "purpose": "signup"}).status_code == 200
    r = c.post("/api/auth/verification-codes", headers={"x-real-ip": "10.2.0.9"},
               json={"username": "g9", "purpose": "signup"})
    assert r.status_code == 429 and r.json()["detail"] == "verification_rate_limited"
    assert len(relay.sent) == 2
    # 전체 상한에 걸린 요청은 그 수신자·IP 몫을 태우지 않았다
    assert "g9@samsung.com" not in c.app.state.mail_throttle._hits
    assert "10.2.0.9" not in c.app.state.mail_throttle_client._hits


def test_send_slot_is_released_after_failed_sends(db, settings):
    import threading
    with FakeRelay(token="not-the-same") as relay:
        c = _app(db, settings, relay)
        c.app.state.mail_send_slots = threading.BoundedSemaphore(1)        # 슬롯 하나 -- 새면 두 번째부터 429
        for i in range(4):
            assert c.post("/api/auth/verification-codes",
                          json={"username": f"fail{i}", "purpose": "signup"}).status_code == 502
        relay.token = TOKEN
        assert c.post("/api/auth/verification-codes",
                      json={"username": "ok.now", "purpose": "signup"}).status_code == 200
        c.app.state.repos.mail_settings.update({"relay_token_enc": "v1:not-a-valid-seal"}, actor="t")
        assert c.post("/api/auth/verification-codes",                       # 읽을 수 없는 키도 슬롯을 돌려준다
                      json={"username": "fail.x", "purpose": "signup"}).status_code == 502
        assert c.post("/api/auth/verification-codes",
                      json={"username": "fail.y", "purpose": "signup"}).status_code == 502
    ev = db.query("SELECT message FROM events WHERE event_type = 'verification_email_failed'")
    assert any("relay_misconfigured" in e["message"] for e in ev)


def test_uncertain_delivery_stores_the_code_and_says_so(db, settings):
    # 요청은 갔는데 응답 전에 타임아웃 -- 메일이 갔을 수 있으니 코드를 저장하고 200 + delivery_uncertain.
    import time
    with FakeRelay(token=TOKEN, delay=1.5) as relay:
        c = _app(db, settings, relay, mail_relay_timeout_seconds=0.3)
        r = c.post("/api/auth/verification-codes", json={"username": "slow.mail", "purpose": "signup"})
        assert r.status_code == 200
        assert r.json() == {"email": "slow.mail@samsung.com", "expires_in_seconds": 300,
                            "delivery_uncertain": True}
        deadline = time.time() + 5
        while not relay.sent and time.time() < deadline:
            time.sleep(0.05)
        code = _code_from(relay.sent[0])
    ev = db.query_one("SELECT message FROM events WHERE event_type = 'verification_email_uncertain'")
    assert "relay_no_response" in ev["message"] and code not in ev["message"]
    assert c.post("/api/auth/signup", json={"username": "slow.mail", "password": "pw",
                                            "code": code}).status_code == 201


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10000:04d}"


def test_brute_force_lock_survives_reissue(db, settings):
    # 재리뷰(high): 코드당 5회 상한은 재발급마다 0 이 돼 "5번 틀리고 재발급"으로 4자리를 하루 ~30% 맞혔다.
    # (아이디, 용도)별 누적 10회면 24시간 발급·확인 모두 429 verification_locked -- 맞는 코드여도.
    from dms.repositories.accounts import VERIFICATION_FAILURE_LIMIT, VERIFICATION_MAX_ATTEMPTS
    Repositories(db).accounts.create("victim.admin", "old", "admin")
    c = _app(db, settings, None, mailer_backend="stub")                    # 메일 상한 없이 코드를 바로 받는다
    req = {"username": "victim.admin", "purpose": "password_reset"}
    failures = 0
    while failures < VERIFICATION_FAILURE_LIMIT:
        code = c.post("/api/auth/verification-codes", json=req).json()["stub_code"]
        for _ in range(min(VERIFICATION_MAX_ATTEMPTS, VERIFICATION_FAILURE_LIMIT - failures)):
            r = c.post("/api/auth/password-reset", json={**req, "password": "pwn", "code": _wrong(code)})
            assert r.status_code == 422 and r.json()["detail"] == "verification_invalid"
            failures += 1
    assert db.query_one("SELECT message FROM events WHERE event_type = 'verification_locked'") is not None
    r = c.post("/api/auth/verification-codes", json=req)
    assert r.status_code == 429 and r.json()["detail"] == "verification_locked"
    assert 86000 < int(r.headers["Retry-After"]) <= 86400
    # 잠금 전에 받아 둔 코드가 맞아도 잠금 중엔 통하지 않는다
    r = c.post("/api/auth/password-reset", json={**req, "password": "pwn", "code": code})
    assert r.status_code == 429 and r.json()["detail"] == "verification_locked"
    assert c.post("/api/auth/login", json={"username": "victim.admin", "password": "old"}).status_code == 200
    # 다른 용도(가입)·다른 아이디는 별개
    assert c.post("/api/auth/verification-codes",
                  json={"username": "someone.else", "purpose": "signup"}).status_code == 200
    # 창(첫 실패부터 24시간)이 지나면 풀리고, 성공한 확인은 누적을 지운다
    db.execute("UPDATE verification_failures SET window_start = '2000-01-01T00:00:00Z'")
    code = c.post("/api/auth/verification-codes", json=req).json()["stub_code"]
    assert c.post("/api/auth/password-reset", json={**req, "password": "new", "code": code}).status_code == 200
    assert db.query("SELECT 1 AS one FROM verification_failures WHERE username = 'victim.admin'") == []


def test_failures_count_across_reissue_but_reset_after_the_window(db, settings):
    from dms.repositories.accounts import VERIFICATION_FAILURE_LIMIT
    repos = Repositories(db)
    for _ in range(VERIFICATION_FAILURE_LIMIT - 1):
        code = repos.accounts.issue_verification_code("w.user", "signup")
        assert repos.accounts.consume_verification_code("w.user", "signup", _wrong(code)) == "verification_invalid"
    assert repos.accounts.verification_lock_seconds("w.user", "signup") is None   # 상한 미만
    row = db.query_one("SELECT failures FROM verification_failures WHERE username = 'w.user'")
    assert row["failures"] == VERIFICATION_FAILURE_LIMIT - 1                      # 재발급이 지우지 않았다
    db.execute("UPDATE verification_failures SET window_start = '2000-01-01T00:00:00Z'")
    code = repos.accounts.issue_verification_code("w.user", "signup")
    assert repos.accounts.consume_verification_code("w.user", "signup", _wrong(code)) == "verification_invalid"
    row = db.query_one("SELECT failures FROM verification_failures WHERE username = 'w.user'")
    assert row["failures"] == 1                                                   # 창이 지나 새 창



def test_one_client_cannot_spray_guesses_across_accounts(db, settings):
    # 3차 리뷰(high): 아이디별 잠금만으로는 한 IP 가 여러 계정에 추측을 나눠 하루 ~1.4 계정을 맞힌다. 같은 IP 의
    # 틀린 코드는 하루 VERIFICATION_GUESS_PER_CLIENT_LIMIT 회 -- 넘으면 코드를 비교하지도 않고 429.
    from dms.api.routes_auth import VERIFICATION_GUESS_PER_CLIENT_LIMIT
    repos = Repositories(db)
    c = _app(db, settings, None, mailer_backend="stub")
    attacker = {"x-real-ip": "10.77.0.1"}
    guesses = 0
    victim = 0
    while guesses < VERIFICATION_GUESS_PER_CLIENT_LIMIT:
        name = f"spray{victim}"
        victim += 1
        repos.accounts.create(name, "old", "user")
        code = c.post("/api/auth/verification-codes", json={"username": name, "purpose": "password_reset"}).json()["stub_code"]
        for _ in range(min(3, VERIFICATION_GUESS_PER_CLIENT_LIMIT - guesses)):    # 계정당 3회 -- 아이디 잠금(10) 아래
            r = c.post("/api/auth/password-reset", headers=attacker,
                       json={"username": name, "purpose": "password_reset", "password": "x", "code": _wrong(code)})
            assert r.status_code == 422
            guesses += 1
    # 상한 뒤엔 새 계정의 **맞는** 코드도 비교하지 않고 429
    repos.accounts.create("spray.last", "old", "user")
    code = c.post("/api/auth/verification-codes",
                  json={"username": "spray.last", "purpose": "password_reset"}).json()["stub_code"]
    r = c.post("/api/auth/password-reset", headers=attacker,
               json={"username": "spray.last", "password": "pwn", "code": code})
    assert r.status_code == 429 and r.json()["detail"] == "verification_client_locked"
    assert 86000 < int(r.headers["Retry-After"]) <= 86400
    r = c.post("/api/auth/signup", headers=attacker, json={"username": "new.one", "password": "p", "code": "0000"})
    assert r.status_code == 429 and r.json()["detail"] == "verification_client_locked"   # 가입도 같은 상한
    rows = db.query("SELECT message FROM events WHERE event_type = 'verification_client_locked'")
    assert len(rows) == 1 and "ip:10.77.0.1" in rows[0]["message"]
    # 다른 IP 의 정상 사용자는 영향 없음
    r = c.post("/api/auth/password-reset", headers={"x-real-ip": "10.77.0.2"},
               json={"username": "spray.last", "password": "new", "code": code})
    assert r.status_code == 200


def test_non_http_relay_is_502_and_stores_nothing(db, settings):
    # 3차 리뷰: 포트를 SSH 등으로 잘못 넣으면 "확인이 늦어지고 있다"(200)가 아니라 502 -- 코드도 저장하지 않는다.
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                conn.recv(65536)
                conn.sendall(b"SSH-2.0-OpenSSH_9.6p1\r\n")
            finally:
                conn.close()

    threading.Thread(target=serve, daemon=True).start()
    try:
        c = _app(db, settings, None, mail_relay_url=f"http://127.0.0.1:{srv.getsockname()[1]}")
        r = c.post("/api/auth/verification-codes", json={"username": "ssh.port", "purpose": "signup"})
    finally:
        srv.close()
    assert r.status_code == 502 and r.json()["detail"] == "verification_email_failed"
    assert db.query("SELECT 1 AS one FROM verification_codes WHERE username = 'ssh.port'") == []
    ev = db.query_one("SELECT message FROM events WHERE event_type = 'verification_email_failed'")
    assert "relay_bad_response" in ev["message"]


def test_client_throttle_event_names_the_client(db, settings, relay):
    from dms.api.routes_auth import VERIFICATION_MAIL_PER_CLIENT_LIMIT
    c = _app(db, settings, relay)
    for i in range(VERIFICATION_MAIL_PER_CLIENT_LIMIT + 1):
        c.post("/api/auth/verification-codes", headers={"x-real-ip": "10.88.0.1"},
               json={"username": f"n{i}", "purpose": "signup"})
    ev = db.query_one("SELECT message FROM events WHERE event_type = 'verification_email_throttled'")
    assert "[client=10.88.0.1]" in ev["message"]



def test_per_client_guess_cap_holds_under_concurrency(db, settings):
    # 4차 리뷰: 검사 -> 소비 -> 기록이 따로면 동시 요청이 모두 검사를 통과한 뒤 비교돼 상한의 2~3배를 실제로 비교했다.
    # 소비를 일부러 느리게 해 경합 창을 넓힌 뒤 동시에 던져도, 실제로 비교된(422) 수는 상한을 넘지 않는다.
    import time
    from concurrent.futures import ThreadPoolExecutor
    from dms.api.routes_auth import VERIFICATION_GUESS_PER_CLIENT_LIMIT
    repos = Repositories(db)
    c = _app(db, settings, None, mailer_backend="stub")
    names = [f"race{i}" for i in range(10)]
    codes = {}
    for n in names:
        repos.accounts.create(n, "old", "user")
        codes[n] = c.post("/api/auth/verification-codes", json={"username": n, "purpose": "password_reset"}).json()["stub_code"]
    real = c.app.state.repos.accounts.consume_verification_code

    def slow(*a, **kw):
        time.sleep(0.02)
        return real(*a, **kw)

    c.app.state.repos.accounts.consume_verification_code = slow

    def guess(i):
        n = names[i % len(names)]
        with TestClient(c.app) as tc:
            r = tc.post("/api/auth/password-reset", headers={"x-real-ip": "10.99.0.1"},
                        json={"username": n, "password": "x", "code": _wrong(codes[n])})
        return r.status_code, r.json()["detail"]

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(guess, range(40)))      # 계정당 4회 -- 아이디 잠금(10)·코드 상한(5) 아래
    compared = sum(1 for r in results if r == (422, "verification_invalid"))
    assert compared == VERIFICATION_GUESS_PER_CLIENT_LIMIT
    assert all(r in ((422, "verification_invalid"), (429, "verification_client_locked")) for r in results)
