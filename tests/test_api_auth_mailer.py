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
    # 6자리(2026-11-03 -- 4자리를 대체)
    return re.search(r"letter-spacing:14px;color:#191f28;\">(\d{6})</td>", mail["body"]).group(1)


def _events(db, event_type):
    from dms.db import load_json
    return [{**r, "payload": load_json(r["payload"])}
            for r in db.query("SELECT message, payload FROM events WHERE event_type = :t ORDER BY id",
                              {"t": event_type})]


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


def test_one_source_is_not_capped_and_concurrency_slots_still_hold(db, settings, relay):
    # 2026-11-03: 클라이언트 IP 별 메일 상한(20통/10분) 삭제 -- SNAT 뒤 전사가 한 IP 라 한 사람이 전원의 메일을 막았다.
    # 같은 출발지에서 21통 이상도 수신자·전체 상한 안이면 200.
    import threading
    c = _app(db, settings, relay)
    same = {"x-forwarded-for": "10.66.0.1"}
    for i in range(25):
        assert c.post("/api/auth/verification-codes", headers=same,
                      json={"username": f"emp{i}", "purpose": "signup"}).status_code == 200
    assert len(relay.sent) == 25
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
    # 거절된 요청은 어느 상한에도 기록되지 않는다 -- 예전엔 수신자 상한을 먼저 기록해, 앞 상한을 다 쓴 공격자가 메일 한 통
    # 없이 남의 재설정을 10분씩 막았다. 축은 수신자·전체 둘이다(2026-11-03 IP 축 삭제).
    import threading
    from dms.api.mailer import SendThrottle
    Repositories(db).accounts.create("victim.one", "pw", "user")
    c = _app(db, settings, relay)
    c.app.state.mail_throttle_global = SendThrottle(3, 600)
    attacker = {"x-forwarded-for": "10.66.66.66"}
    for i in range(3):
        assert c.post("/api/auth/verification-codes", headers=attacker,
                      json={"username": f"att{i}", "purpose": "signup"}).status_code == 200
    sent = len(relay.sent)
    for _ in range(8):
        r = c.post("/api/auth/verification-codes", headers=attacker,
                   json={"username": "victim.one", "purpose": "password_reset"})
        assert r.status_code == 429                                        # 전체 상한
    assert len(relay.sent) == sent                                         # 메일 0통
    assert c.app.state.mail_throttle.peek("victim.one@samsung.com") == 0   # 피해자 몫은 그대로
    assert "victim.one@samsung.com" not in c.app.state.mail_throttle._hits
    # 슬롯이 다 차서 거절된 요청도 상한을 태우지 않는다
    c.app.state.mail_throttle_global = SendThrottle(300, 600)
    real = c.app.state.mail_send_slots
    busy = threading.BoundedSemaphore(1)
    busy.acquire()
    c.app.state.mail_send_slots = busy
    for _ in range(6):
        assert c.post("/api/auth/verification-codes",
                      json={"username": "victim.one", "purpose": "password_reset"}).status_code == 429
    c.app.state.mail_send_slots = real
    r = c.post("/api/auth/verification-codes", json={"username": "victim.one", "purpose": "password_reset"})
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
        assert c.post("/api/auth/verification-codes", headers={"x-forwarded-for": f"10.2.0.{i}"},
                      json={"username": f"g{i}", "purpose": "signup"}).status_code == 200
    r = c.post("/api/auth/verification-codes", headers={"x-forwarded-for": "10.2.0.9"},
               json={"username": "g9", "purpose": "signup"})
    assert r.status_code == 429 and r.json()["detail"] == "verification_rate_limited"
    assert len(relay.sent) == 2
    # 전체 상한에 걸린 요청은 그 수신자 몫을 태우지 않았다
    assert "g9@samsung.com" not in c.app.state.mail_throttle._hits
    assert not hasattr(c.app.state, "mail_throttle_client")               # IP 축은 없다(2026-11-03)


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
    # 실패 이벤트와 같은 payload(수신자·용도·사유 + 출발지, 2026-11-11) -- 코드는 없다
    (uncertain,) = _events(db, "verification_email_uncertain")
    assert {k: uncertain["payload"][k] for k in ("purpose", "recipient", "reason")} == {
        "purpose": "signup", "recipient": "slow.mail@samsung.com", "reason": "relay_no_response"}
    assert code not in str(uncertain["payload"])
    assert c.post("/api/auth/signup", json={"username": "slow.mail", "password": "pw",
                                            "code": code}).status_code == 201


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1000000:06d}"


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



def test_global_wrong_code_cap_locks_issuance_and_confirmation_for_everyone(db, settings):
    # 2026-11-03 사용자 결정: 옛 접속 IP 별 오답 상한(20회/24h)을 **전체** 오답 상한(env, 기본 1000회/1시간)으로 바꿨다.
    # 여러 계정에 나눈 오답이 상한에 닿으면 모든 계정의 확인(맞는 코드 포함)과 발급이 429 verification_global_locked
    # + Retry-After, 이벤트는 한 번.
    repos = Repositories(db)
    c = _app(db, settings, None, mailer_backend="stub", verification_guess_global_limit=3)
    names = ["glob.a", "glob.b", "glob.c"]
    codes = {}
    for n in names:
        repos.accounts.create(n, "old", "user")
        codes[n] = c.post("/api/auth/verification-codes",
                          json={"username": n, "purpose": "password_reset"}).json()["stub_code"]
    for i, n in enumerate(names):                     # 계정마다 1회 -- 아이디 잠금(10)·코드 상한(5) 한참 아래, 출발지도 제각각
        r = c.post("/api/auth/password-reset", headers={"x-forwarded-for": f"10.77.0.{i}"},
                   json={"username": n, "password": "x", "code": _wrong(codes[n])})
        assert (r.status_code, r.json()["detail"]) == (422, "verification_invalid")
    # 맞는 코드도 비교하지 않고 429(다른 출발지여도)
    r = c.post("/api/auth/password-reset", headers={"x-forwarded-for": "10.99.0.1"},
               json={"username": "glob.a", "password": "new", "code": codes["glob.a"]})
    assert (r.status_code, r.json()["detail"]) == (429, "verification_global_locked")
    assert 1 <= int(r.headers["Retry-After"]) <= 3600
    # 가입도 같은 상한, 발급도 거절(확인이 막힌 동안 메일을 보내지 않는다)
    r = c.post("/api/auth/signup", json={"username": "new.one", "password": "p", "code": "000000"})
    assert (r.status_code, r.json()["detail"]) == (429, "verification_global_locked")
    r = c.post("/api/auth/verification-codes", json={"username": "new.one", "purpose": "signup"})
    assert (r.status_code, r.json()["detail"]) == (429, "verification_global_locked")
    assert 1 <= int(r.headers["Retry-After"]) <= 3600
    assert db.query("SELECT 1 AS one FROM verification_codes WHERE username = 'new.one'") == []
    ev = _events(db, "verification_global_locked")
    assert len(ev) == 1
    p = ev[0]["payload"]
    assert (p["limit"], p["window_seconds"], p["last_username"], p["last_purpose"]) == \
        (3, 3600, "glob.c", "password_reset")
    assert p["client_ip"] == "10.77.0.2" and p["peer"] == "testclient"
    # 로그인은 무관
    assert c.post("/api/auth/login", json={"username": "glob.a", "password": "old"}).status_code == 200
    # 잠금이 풀리면(관리자 전체 해제 = limiter clear) 그대로 남아 있던 코드로 다시 된다
    c.app.state.verification_guess_limiter.clear("global")
    r = c.post("/api/auth/password-reset", json={"username": "glob.a", "password": "new", "code": codes["glob.a"]})
    assert r.status_code == 200


def test_global_wrong_code_cap_logs_one_event_while_an_attack_keeps_it_full(db, settings):
    # 2026-11-03 리뷰: 상한에 닿은 뒤 가장 오래된 오답이 창 밖으로 빠질 때마다 한 건이 다시 받아들여지고, 예전엔 그때마다
    # verification_global_locked 가 남았다(공격이 이어지면 시간당 약 1000건). 「잠김」 이벤트는 창에 한 번이다.
    from dms.api.login_limiter import LoginRateLimiter
    repos = Repositories(db)
    clock = [0.0]
    c = _app(db, settings, None, mailer_backend="stub")
    c.app.state.verification_guess_limiter = LoginRateLimiter(3, 100, clock=lambda: clock[0])
    names = ["slip.a", "slip.b", "slip.c"]
    codes = {}
    for n in names:
        repos.accounts.create(n, "old", "user")
        codes[n] = c.post("/api/auth/verification-codes",
                          json={"username": n, "purpose": "password_reset"}).json()["stub_code"]
    results = []
    for i, t in enumerate((0, 1, 2, 50, 100, 101)):
        clock[0] = float(t)
        n = names[i % 3]
        r = c.post("/api/auth/password-reset", json={"username": n, "password": "x", "code": _wrong(codes[n])})
        results.append((r.status_code, r.json()["detail"]))
    assert results == [(422, "verification_invalid")] * 3 + [(429, "verification_global_locked")] + \
        [(422, "verification_invalid")] * 2                         # 창에서 빠진 만큼만 다시 비교한다(판정 불변)
    assert len(_events(db, "verification_global_locked")) == 1


def test_others_from_the_same_source_do_not_block_my_issuance(db, settings, relay):
    # 2026-11-03: 같은 출발지(SNAT)의 남의 메일·오답은 내 발급을 막지 않는다(전체 상한 아래에서).
    repos = Repositories(db)
    c = _app(db, settings, relay)
    office = {"x-forwarded-for": "10.10.10.1"}
    for i in range(22):
        assert c.post("/api/auth/verification-codes", headers=office,
                      json={"username": f"cow{i}", "purpose": "signup"}).status_code == 200
    for i in range(5):                                                   # 남들의 오답(전체 1000 아래)
        r = c.post("/api/auth/signup", headers=office,
                   json={"username": f"cow{i}", "password": "p", "code": _wrong(_code_from(relay.sent[i]))})
        assert r.json()["detail"] == "verification_invalid"
    repos.accounts.create("me.here", "pw", "user")
    r = c.post("/api/auth/verification-codes", headers=office,
               json={"username": "me.here", "purpose": "password_reset"})
    assert r.status_code == 200 and relay.sent[-1]["to"] == ["me.here@samsung.com"]


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


def test_throttle_event_payload_names_the_limit_kind_not_a_client(db, settings, relay):
    # 상한 종류는 recipient | global | busy 뿐이다(client 없음, 2026-11-03). 출발지는 payload 에 기록만 한다.
    c = _app(db, settings, relay)
    for _ in range(6):
        c.post("/api/auth/verification-codes", headers={"x-forwarded-for": "10.88.0.1"},
               json={"username": "same.one", "purpose": "signup"})
    ev = _events(db, "verification_email_throttled")
    assert len(ev) == 1
    p = ev[0]["payload"]
    assert p["kind"] in ("recipient", "global", "busy") and p["kind"] == "recipient"
    assert (p["purpose"], p["recipient"], p["client_ip"]) == ("signup", "same.one@samsung.com", "10.88.0.1")
    assert "[client=" not in ev[0]["message"]


def test_global_guess_cap_holds_under_concurrency(db, settings):
    # 4차 리뷰: 검사 -> 소비 -> 기록이 따로면 동시 요청이 모두 검사를 통과한 뒤 비교돼 상한의 2~3배를 실제로 비교했다.
    # 소비를 일부러 느리게 해 경합 창을 넓힌 뒤 동시에 던져도, 실제로 비교된(422) 수는 상한을 넘지 않는다(2026-11-03 전체 축).
    import time
    from concurrent.futures import ThreadPoolExecutor
    limit = 20
    repos = Repositories(db)
    c = _app(db, settings, None, mailer_backend="stub", verification_guess_global_limit=limit)
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
            r = tc.post("/api/auth/password-reset", headers={"x-forwarded-for": f"10.99.0.{i}"},
                        json={"username": n, "password": "x", "code": _wrong(codes[n])})
        return r.status_code, r.json()["detail"]

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(guess, range(40)))      # 계정당 4회 -- 아이디 잠금(10)·코드 상한(5) 아래
    compared = sum(1 for r in results if r == (422, "verification_invalid"))
    assert compared == limit
    assert all(r in ((422, "verification_invalid"), (429, "verification_global_locked")) for r in results)


# --- 완료 안내 메일(2026-11-03, api/account_notice.py -- 최선 노력) ------------------------------------------------

def test_signup_and_reset_send_a_notice_without_secrets(db, settings, relay):
    c = _app(db, settings, relay)
    assert c.post("/api/auth/verification-codes",
                  json={"username": "notice.me", "purpose": "signup"}).status_code == 200
    code = _code_from(relay.sent[-1])
    assert c.post("/api/auth/signup", json={"username": "notice.me", "password": "Secret-pw-1",
                                            "code": code}).status_code == 201
    mail = relay.sent[-1]
    assert mail["to"] == ["notice.me@samsung.com"] and mail["content_type"] == "HTML"
    assert mail["subject"] == "[Supercom 포털] 계정 생성 완료 안내"
    assert "Secret-pw-1" not in mail["body"] and code not in mail["body"] and "testclient" not in mail["body"]
    assert "notice.me" in mail["body"]
    ev = _events(db, "account_notice_sent")
    assert len(ev) == 1 and ev[0]["payload"]["kind"] == "signup"
    assert ev[0]["payload"]["recipient"] == "notice.me@samsung.com"
    # 비밀번호 재설정 → 「비밀번호 변경 안내」
    assert c.post("/api/auth/verification-codes",
                  json={"username": "notice.me", "purpose": "password_reset"}).status_code == 200
    code = _code_from(relay.sent[-1])
    assert c.post("/api/auth/password-reset", json={"username": "notice.me", "password": "Other-pw-2",
                                                    "code": code}).status_code == 200
    mail = relay.sent[-1]
    assert mail["subject"] == "[Supercom 포털] 비밀번호 변경 안내"
    assert "Other-pw-2" not in mail["body"] and code not in mail["body"]
    assert "다시 로그인" in mail["body"]
    assert [e["payload"]["kind"] for e in _events(db, "account_notice_sent")] == ["signup", "password_reset"]


def test_stub_backend_records_the_notice_without_calling_the_relay(db, settings, relay):
    c = _app(db, settings, relay, mailer_backend="stub")
    code = c.post("/api/auth/verification-codes", json={"username": "stub.one", "purpose": "signup"}).json()["stub_code"]
    assert c.post("/api/auth/signup", json={"username": "stub.one", "password": "pw", "code": code}).status_code == 201
    assert relay.sent == []
    ev = _events(db, "account_notice_stub")
    assert len(ev) == 1 and ev[0]["payload"]["kind"] == "signup"
    assert _events(db, "account_notice_sent") == []


def test_relay_failure_never_blocks_the_signup(db, settings):
    with FakeRelay(token="not-the-same") as relay:
        c = _app(db, settings, relay, account_verification_required=False)
        r = c.post("/api/auth/signup", json={"username": "fail.notice", "password": "pw"})
        assert (r.status_code, r.json()) == (201, {"username": "fail.notice"})
    ev = _events(db, "account_notice_failed")
    assert len(ev) == 1 and ev[0]["payload"]["reason"] == "unauthorized"
    assert TOKEN not in ev[0]["message"]
    assert c.post("/api/auth/login", json={"username": "fail.notice", "password": "pw"}).status_code == 200


def test_notice_is_skipped_when_the_send_slots_are_full(db, settings, relay):
    import threading
    c = _app(db, settings, relay, account_verification_required=False)
    busy = threading.BoundedSemaphore(1)
    busy.acquire()
    c.app.state.mail_send_slots = busy
    assert c.post("/api/auth/signup", json={"username": "busy.notice", "password": "pw"}).status_code == 201
    assert relay.sent == []
    ev = _events(db, "account_notice_skipped")
    assert len(ev) == 1 and ev[0]["payload"]["reason"] == "busy"


def test_notice_respects_the_recipient_cap(db, settings, relay):
    from dms.api.mailer import SendThrottle
    c = _app(db, settings, relay, account_verification_required=False)
    c.app.state.mail_throttle = SendThrottle(1, 600)
    c.app.state.mail_throttle.record("capped.one@samsung.com")          # 수신자 몫을 이미 다 씀
    assert c.post("/api/auth/signup", json={"username": "capped.one", "password": "pw"}).status_code == 201
    assert relay.sent == []
    ev = _events(db, "account_notice_skipped")
    assert len(ev) == 1 and ev[0]["payload"]["reason"] == "throttled"
    assert "*" not in c.app.state.mail_throttle_global._hits             # 걸린 요청은 전체 몫을 태우지 않는다
