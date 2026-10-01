"""api/mailer.py -- Knox 메일 릴레이 클라이언트(2026-10-01). 실제 릴레이 대신 계약을 흉내 내는
가짜(tests/fake_knox_relay.py)에 붙여 성공·사유별 실패·비밀 비노출·본문 렌더링·발송 상한을 고정한다."""
import re
import socket
import time
import urllib.request
from datetime import datetime

import pytest

from dms.api import mailer
from dms.api.mailer import (KST, MailerError, SendThrottle, check_relay_health, render_test_email,
                            render_verification_email, send_mail_via_relay)
from fake_knox_relay import FakeRelay

TOKEN = "relay-token-123"


@pytest.fixture
def relay():
    r = FakeRelay(token=TOKEN)
    r.start()
    yield r
    r.stop()


def _send(relay_url, token=TOKEN, to="alice@samsung.com", **kw):
    send_mail_via_relay(relay_url=relay_url, token=token, to=to, subject="[DMS] 제목",
                        body="<p>본문</p>", content_type="HTML", timeout=5, **kw)


def _reason(fn):
    with pytest.raises(MailerError) as e:
        fn()
    return e.value


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --- 릴레이 호출 ---------------------------------------------------------------------------

def test_send_posts_the_contract_shape_with_bearer_token(relay):
    _send(relay.base_url)
    assert relay.sent[0]["to"] == ["alice@samsung.com"]
    assert relay.sent[0]["subject"] == "[DMS] 제목" and relay.sent[0]["content_type"] == "HTML"
    assert relay.requests[0]["authorization"] == f"Bearer {TOKEN}"
    assert relay.requests[0]["content_type"].startswith("application/json")


def test_token_trailing_newline_is_stripped(relay):
    # 시크릿 파일에서 읽으면 끝에 개행이 붙기 쉽다
    _send(relay.base_url, token=TOKEN + "\n")
    assert len(relay.sent) == 1


@pytest.mark.parametrize("kwargs,reason", [
    ({"token": "wrong"}, "unauthorized"),
])
def test_relay_error_codes_are_raised_as_reasons(relay, kwargs, reason):
    assert _reason(lambda: _send(relay.base_url, **kwargs)).reason == reason


def test_client_and_recipient_allowlists():
    with FakeRelay(token=TOKEN, allowed_clients={"10.9.9.9"}) as r:
        assert _reason(lambda: _send(r.base_url)).reason == "client_not_allowed"
    with FakeRelay(token=TOKEN, allowed_domains={"corp.example"}) as r:
        assert _reason(lambda: _send(r.base_url)).reason == "recipient_not_allowed"
        _send(r.base_url, to="bob@corp.example")


def test_relay_rate_limit_carries_retry_after():
    with FakeRelay(token=TOKEN, rate_limit=1, window=600) as r:
        _send(r.base_url)
        e = _reason(lambda: _send(r.base_url))
        assert e.reason == "rate_limited" and isinstance(e.retry_after, int) and 0 < e.retry_after <= 601


def test_knox_side_failures():
    with FakeRelay(token=TOKEN, knox_mode="reject") as r:
        e = _reason(lambda: _send(r.base_url))
        assert e.reason == "knox_rejected" and "knox HTTP 401" in e.detail
    with FakeRelay(token=TOKEN, knox_mode="unreachable") as r:
        assert _reason(lambda: _send(r.base_url)).reason == "knox_unreachable"
    with FakeRelay(token=TOKEN, knox_mode="garbage") as r:
        assert _reason(lambda: _send(r.base_url)).reason == "relay_bad_response"


def test_unreachable_and_misconfigured():
    port = _closed_port()
    assert _reason(lambda: _send(f"http://127.0.0.1:{port}")).reason == "relay_unreachable"
    assert _reason(lambda: _send("127.0.0.1:8025")).reason == "relay_misconfigured"   # 스킴 없음
    assert _reason(lambda: _send("http://127.0.0.1:abc")).reason == "relay_misconfigured"
    assert _reason(lambda: _send("http://127.0.0.1:8025", token="")).reason == "relay_misconfigured"
    e = _reason(lambda: _send("http://127.0.0.1:8025", token="tök\x00en"))
    assert e.reason == "relay_misconfigured" and "tök" not in str(e)   # 토큰 값이 메시지에 안 나온다


def _proxies(opener):
    # 빈 ProxyHandler({}) 는 *_open 메서드가 없어 opener 에 등록조차 안 된다 -> []. 지우면 build_opener 가 env 를
    # 읽는 기본 ProxyHandler() 를 넣어 [{'http': ...}] 가 된다.
    return [h.proxies for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]


def test_proxy_environment_is_ignored(relay, monkeypatch):
    # 웹서버에 http(s)_proxy 가 잡혀 있어도 릴레이는 사내망 직통이다(_build_relay_opener 의 빈 ProxyHandler).
    # 모듈 opener 는 import 때 만들어져 여기서 env 를 바꿔도 영향이 없다 -- 그래서 env 를 건 **뒤에** opener 를
    # 새로 만들어 그것으로 보낸다(2026-10-01 재리뷰: 예전 테스트는 빈 ProxyHandler 를 지워도 통과했다).
    # no_proxy 가 127.0.0.1 을 덮고 있으면 이 테스트가 아무것도 증명하지 못하므로 지운다.
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{_closed_port()}")
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{_closed_port()}")
    fresh = mailer._build_relay_opener()
    assert _proxies(fresh) == []
    assert _proxies(urllib.request.build_opener()) != []       # 대조: 기본 opener 는 이 env 프록시를 탄다
    monkeypatch.setattr(mailer, "_RELAY_OPENER", fresh)
    _send(relay.base_url)
    assert len(relay.sent) == 1
    assert _proxies(mailer._build_relay_opener()) == []


def test_redirects_are_not_followed_and_the_token_does_not_leave(relay):
    # 2026-10-01 리뷰: urllib 기본은 302 를 GET 으로 따라가며 Authorization 을 싣는다 -- 대상이 {"ok": true} 면
    # 보내지 않은 메일이 '성공'. 따라가지 않아야 한다(3xx = relay_http_30x). 가짜 릴레이는 GET 을 포함한 모든
    # 요청을 requests 에 남기므로, 따라갔다면 리다이렉트 대상(relay)에 GET /send 가 찍힌다.
    for target in ("/send", "/healthz"):
        with FakeRelay(token=TOKEN, knox_mode="redirect", redirect_to=relay.base_url + target) as bouncer:
            e = _reason(lambda: _send(bouncer.base_url))
            assert e.reason == "relay_http_302"
            assert len(bouncer.requests) == 1
    assert relay.requests == [] and relay.sent == []          # 리다이렉트 대상에 아무것도(토큰도) 안 갔다


def test_redirect_target_would_have_seen_the_token_if_followed(relay):
    # 위 테스트가 실제로 누출을 볼 수 있는지의 대조군: 따라가는 opener(urllib 기본)로 같은 리다이렉트를 타면
    # 대상에 GET 과 Bearer 토큰이 도착한다.
    with FakeRelay(token=TOKEN, knox_mode="redirect", redirect_to=relay.base_url + "/healthz") as bouncer:
        body = b'{"to": ["a@samsung.com"], "subject": "s", "body": "b", "content_type": "HTML"}'
        req = urllib.request.Request(bouncer.base_url + "/send", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5) as res:
            res.read()
    assert [(r["method"], r["authorization"]) for r in relay.requests] == [("GET", f"Bearer {TOKEN}")]


def test_no_response_after_sending_is_uncertain_not_unreachable():
    # 연결 거부(요청이 안 나감) = relay_unreachable, 요청은 갔는데 응답 전에 타임아웃 = relay_no_response(갔을 수도
    # 있다 -- 인증 메일은 이때 코드를 저장한다). 가짜 릴레이는 기다린 뒤 메일을 그대로 기록한다.
    assert _reason(lambda: _send(f"http://127.0.0.1:{_closed_port()}")).reason == "relay_unreachable"
    with FakeRelay(token=TOKEN, delay=1.5) as slow:
        e = _reason(lambda: send_mail_via_relay(relay_url=slow.base_url, token=TOKEN, to="a@samsung.com",
                                                subject="s", body="<p>b</p>", timeout=0.3))
        assert e.reason == "relay_no_response"
        assert TOKEN not in str(e)
        deadline = time.time() + 5
        while not slow.sent and time.time() < deadline:
            time.sleep(0.05)
        assert len(slow.sent) == 1                             # 실제로는 갔다


def test_non_json_errors_and_header_only_retry_after():
    with FakeRelay(token=TOKEN, knox_mode="http500") as r:
        assert _reason(lambda: _send(r.base_url)).reason == "relay_http_500"
    with FakeRelay(token=TOKEN, knox_mode="retry_header_only") as r:
        e = _reason(lambda: _send(r.base_url))
        assert e.reason == "relay_http_429" and e.retry_after == 77   # 본문이 없으면 Retry-After 헤더


def test_health_check(relay):
    check_relay_health(relay_url=relay.base_url, timeout=5)
    assert _reason(lambda: check_relay_health(
        relay_url=f"http://127.0.0.1:{_closed_port()}", timeout=2)).reason == "relay_unreachable"
    assert _reason(lambda: check_relay_health(relay_url="", timeout=2)).reason == "relay_misconfigured"


# --- 본문 ----------------------------------------------------------------------------------

NOW = datetime(2026, 10, 1, 9, 30, tzinfo=KST)


def test_verification_email_puts_the_code_only_in_the_body():
    subject, body = render_verification_email(code="4821", purpose="signup", username="alice",
                                              ttl_seconds=300, service_name="Supercom 포털", now=NOW)
    assert subject == "[Supercom 포털] 회원가입 인증번호 안내" and "4821" not in subject
    assert ">4821</td>" in body
    assert "5분간" in body and "09:35 KST까지" in body and "2026-10-01 09:30 (KST)" in body
    # 프리헤더(미리보기 줄)에도 코드가 없다
    preheader = re.search(r'mso-hide:all;">(.*?)</div>', body).group(1)
    assert "4821" not in preheader
    assert "${" not in body                                                   # 치환자가 남지 않는다


def test_verification_email_escapes_user_supplied_text_and_handles_purposes():
    subject, body = render_verification_email(code="1234", purpose="password_reset",
                                              username='<b>x"&</b>', ttl_seconds=300,
                                              service_name="A&B", now=NOW)
    assert subject == "[A&B] 비밀번호 재설정 인증번호 안내"
    assert "&lt;b&gt;x&quot;&amp;&lt;/b&gt;" in body and '<b>x"&</b>' not in body
    assert "A&amp;B" in body
    _, other = render_verification_email(code="1", purpose="weird", username="u", ttl_seconds=59, now=NOW)
    assert "본인 확인 인증번호 안내" in other and "1분간" in other       # 미지 목적 = 대체 문구, 최소 1분


def test_test_email_has_no_secret_and_escapes():
    subject, body = render_test_email(service_name="DMS<1>", requested_by="mason", now=NOW)
    assert subject == "[DMS<1>] 메일 발송 설정 테스트"
    assert "DMS&lt;1&gt;" in body and "mason" in body and "2026-10-01 09:30" in body


# --- 발송 상한 -----------------------------------------------------------------------------

def test_send_throttle_limits_per_recipient_case_insensitively(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(mailer.time, "monotonic", lambda: t[0])
    th = SendThrottle(2, 600)
    assert th.acquire("A@x.com") == 0 and th.acquire("a@X.com") == 0
    retry = th.acquire("a@x.com")
    assert retry == 601                         # 첫 기록이 창을 벗어나기까지 남은 초 + 1
    assert th.acquire("b@x.com") == 0           # 다른 수신자는 무관
    t[0] += 600
    assert th.acquire("a@x.com") == 0           # 창이 지나면 다시 허용
    assert SendThrottle(0, 600).acquire("a@x.com") == 0   # limit<=0 = 끔


def test_throttle_peek_does_not_record_or_create_keys_and_record_counts():
    t = SendThrottle(limit=2, window_seconds=600)
    for _ in range(5):
        assert t.peek("a@x") == 0
    assert t._hits == {}                                      # 거절·조회만으로 키가 생기지 않는다
    t.record("A@x")
    t.record("a@x")
    assert t.peek("a@x") > 0 and t.acquire("a@x") > 0          # 대소문자 무시, 상한
    assert t.peek("b@x") == 0 and "b@x" not in t._hits



def _banner_server(banner: bytes):
    """연결을 받으면 HTTP 가 아닌 배너(SSH 류)를 보내고 닫는 리스너 -- 포트를 잘못 넣은 경우."""
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                conn.recv(65536)
                conn.sendall(banner)
            finally:
                conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.getsockname()[1]}"


def test_non_http_listener_is_bad_response_not_maybe_sent():
    # 3차 리뷰: BadStatusLine(HTTP 가 아닌 응답)을 "갔을 수도 있다"(relay_no_response)로 두면 인증 메일 라우트가
    # 보내지도 않은 코드를 저장하고 200 을 준다. 릴레이가 아닌 것이 답한 것이다 -- relay_bad_response.
    srv, url = _banner_server(b"SSH-2.0-OpenSSH_9.6p1\r\n")
    try:
        assert _reason(lambda: _send(url)).reason == "relay_bad_response"
        assert _reason(lambda: check_relay_health(relay_url=url, timeout=5)).reason == "relay_bad_response"
    finally:
        srv.close()
    srv, url = _banner_server(b"")                                   # 응답 없이 끊김 = 갔을 수도 있다
    try:
        assert _reason(lambda: _send(url)).reason == "relay_no_response"
    finally:
        srv.close()
