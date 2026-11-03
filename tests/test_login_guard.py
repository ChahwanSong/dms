"""로그인 과부하·경보 장치 단위 테스트(2026-11-03, api/login_guard.py) + 원 IP 파싱(api/auth.client_ip).
가짜 시계로 창·버킷을 조작한다."""
import pytest
from starlette.requests import Request

from dms.api.auth import client_ip, request_origin
from dms.api.login_guard import GlobalPacer, LoginAlarms, retry_after_seconds


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# --- GlobalPacer(대기형 전체 스위치) ---

def test_pacer_is_off_at_zero():
    for limit, window in ((0, 60), (-1, 60), (60, 0)):
        p = GlobalPacer(limit, window, 15)
        assert p.enabled is False
        assert p.reserve() == (True, 0.0)                    # 꺼짐이면 아무것도 바꾸지 않는다
        p.refund()
        assert p.snapshot() == {"tokens": None, "waiting": 0}   # 꺼짐 = null(「없음」 -- 0 과 섞지 않는다)


def test_pacer_burst_then_scheduled_waits_in_arrival_order():
    clock = Clock()
    p = GlobalPacer(3, 3, 10, clock=clock)                   # 초당 1개
    assert [p.reserve() for _ in range(3)] == [(True, 0.0)] * 3   # 버스트 L
    ok, wait = p.reserve()
    assert ok and wait == pytest.approx(1.0)                 # 4번째: 빚 1 → 1초 뒤
    ok, wait = p.reserve()
    assert ok and wait == pytest.approx(2.0)                 # 5번째: 빚 2 → 2초 뒤(도착 순서대로 미리 정한다)
    assert p.snapshot()["tokens"] == pytest.approx(-2.0)


def test_pacer_refuses_without_changing_state_when_the_wait_exceeds_max():
    clock = Clock()
    p = GlobalPacer(3, 3, 1.5, clock=clock)
    for _ in range(4):
        assert p.reserve()[0] is True                        # 4번째는 1초 대기(≤ 1.5)
    first = p.reserve()
    assert first[0] is False and first[1] == pytest.approx(2.0)
    assert p.reserve() == first                              # 상태 불변 -- 다음 판정이 같다
    assert p.snapshot()["tokens"] == pytest.approx(-1.0)


def test_pacer_refills_with_the_clock_and_refund_is_capped():
    clock = Clock()
    p = GlobalPacer(3, 3, 10, clock=clock)
    for _ in range(3):
        p.reserve()
    assert p.snapshot()["tokens"] == pytest.approx(0.0)
    clock.t += 2
    assert p.snapshot()["tokens"] == pytest.approx(2.0)      # 초당 1개
    clock.t += 100
    assert p.snapshot()["tokens"] == pytest.approx(3.0)      # 용량 L 을 넘지 않는다
    p.refund()
    assert p.snapshot()["tokens"] == pytest.approx(3.0)      # 환불도 L 을 넘지 않는다
    p.reserve()
    p.refund()
    assert p.snapshot()["tokens"] == pytest.approx(3.0)


def test_pacer_counts_waiters():
    p = GlobalPacer(1, 60, 15)
    p.enter_wait()
    p.enter_wait()
    assert p.snapshot()["waiting"] == 2
    p.leave_wait()
    assert p.snapshot()["waiting"] == 1


def test_retry_after_is_at_least_one_second():
    assert retry_after_seconds(0.0) == 1
    assert retry_after_seconds(0.2) == 1
    assert retry_after_seconds(1.01) == 2
    assert retry_after_seconds(15) == 15


# --- LoginAlarms ---

def test_failure_alarm_fires_once_per_window_and_again_after_it():
    clock = Clock()
    a = LoginAlarms(600, failures=3, attempts=0, source_failures=0, clock=clock)
    assert a.note_failure("10.0.0.1", "u1") == []
    assert a.note_failure("10.0.0.2", "u2") == []
    out = a.note_failure("10.0.0.3", "u3")
    assert out == [{"event_type": "login_failure_alarm", "count": 3, "threshold": 3, "window_seconds": 600}]
    assert a.note_failure("10.0.0.4", "u4") == []            # 같은 창 안 재도달은 없음
    clock.t += 600
    for i in range(2):
        assert a.note_failure("10.0.0.5", f"v{i}") == []     # 창이 지나 카운트도 새로
    out = a.note_failure("10.0.0.5", "v2")
    assert [e["event_type"] for e in out] == ["login_failure_alarm"]


def test_attempt_alarm_counts_successes_too():
    clock = Clock()
    a = LoginAlarms(60, failures=0, attempts=2, source_failures=0, clock=clock)
    assert a.note_attempt() == []
    assert a.note_attempt() == [{"event_type": "login_attempt_alarm", "count": 2, "threshold": 2,
                                 "window_seconds": 60}]
    assert a.note_attempt() == []
    assert a.snapshot() == {"failures": 0, "attempts": 3}


def test_source_alarm_is_per_source_and_unknown_when_missing():
    clock = Clock()
    a = LoginAlarms(600, failures=0, attempts=0, source_failures=2, clock=clock)
    assert a.note_failure("10.9.9.9", "a") == []
    assert a.note_failure("10.9.9.8", "b") == []
    out = a.note_failure("10.9.9.9", "c")
    assert out == [{"event_type": "login_source_alarm", "count": 2, "threshold": 2, "window_seconds": 600,
                    "last_username": "c", "budget_left": 49}]
    assert a.note_failure(None, "d") == []
    out = a.note_failure(None, "e")                          # 출발지를 모르면 "unknown" 키
    assert [e["event_type"] for e in out] == ["login_source_alarm"]
    assert a.note_failure(None, "f") == []


def test_zero_thresholds_emit_nothing_but_still_count():
    clock = Clock()
    a = LoginAlarms(60, failures=0, attempts=0, source_failures=0, clock=clock)
    for i in range(50):
        assert a.note_attempt() == []
        assert a.note_failure("1.1.1.1", f"u{i}") == []
    assert a.snapshot() == {"failures": 50, "attempts": 50}
    clock.t += 61
    assert a.snapshot() == {"failures": 0, "attempts": 0}


def test_once_is_once_per_kind_and_key_per_window():
    clock = Clock()
    a = LoginAlarms(10, 0, 0, 0, clock=clock)
    assert a.once("busy", "slots") is True
    assert a.once("busy", "slots") is False
    assert a.once("busy", "pacing") is True                  # 키가 다르면 따로
    clock.t += 10
    assert a.once("busy", "slots") is True


# --- 원 IP(auth.client_ip) ---

def _req(headers=(), client=("192.0.2.10", 5000)):
    scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"",
             "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers]}
    if client is not None:
        scope["client"] = client
    return Request(scope)


@pytest.mark.parametrize("headers,expected", [
    ((), "192.0.2.10"),                                                   # XFF 없음 → 피어
    ((("x-forwarded-for", "203.0.113.7"),), "203.0.113.7"),
    ((("x-forwarded-for", "1.1.1.1, 203.0.113.7"),), "203.0.113.7"),      # 마지막 항목(앞은 클라이언트가 심는다)
    ((("x-forwarded-for", "  1.1.1.1 ,   203.0.113.8  "),), "203.0.113.8"),   # 공백
    ((("x-forwarded-for", "1.1.1.1"), ("x-forwarded-for", "198.51.100.9")), "198.51.100.9"),   # 여러 헤더 → 이어 붙인 마지막
    ((("x-forwarded-for", "2001:DB8:0:0::1"),), "2001:db8::1"),           # IPv6 정규화
    ((("x-forwarded-for", "10.0.0.1, not-an-ip"),), "192.0.2.10"),        # 마지막이 IP 가 아니면 피어
    ((("x-forwarded-for", "10.0.0.1:443"),), "192.0.2.10"),               # 포트가 붙은 값도 IP 가 아니다
    ((("x-forwarded-for", "10.0.0.1,"),), "192.0.2.10"),                  # 빈 마지막 항목
    ((("x-forwarded-for", ""),), "192.0.2.10"),
    ((("x-real-ip", "6.6.6.6"),), "192.0.2.10"),                          # X-Real-IP 는 읽지 않는다
    ((("x-real-ip", "6.6.6.6"), ("x-forwarded-for", "203.0.113.7")), "203.0.113.7"),
])
def test_client_ip_parsing_table(headers, expected):
    assert client_ip(_req(headers)) == expected


def test_client_ip_without_peer_is_none_and_origin_has_both():
    assert client_ip(_req((), client=None)) is None
    assert request_origin(_req((), client=None)) == {"client_ip": None, "peer": None}
    assert request_origin(_req((("x-forwarded-for", "203.0.113.7"),))) == \
        {"client_ip": "203.0.113.7", "peer": "192.0.2.10"}


# --- 2026-11-03 리뷰: 경보 메모리·아이디별 이벤트 상한 ---

def test_alarm_counters_use_second_buckets_so_memory_does_not_grow_with_attack_rate():
    # 시도마다 시각을 들고 있으면 메모리가 「속도 × 창」이었다(실패 1회당 약 73바이트). 초 단위 버킷이면 키당 창 초 수 이하.
    clock = Clock(0.0)
    a = LoginAlarms(600, failures=0, attempts=0, source_failures=0, clock=clock)
    for i in range(120_000):                                 # 초당 100회 × 1200초
        clock.t = i / 100
        a.note_attempt()
        a.note_failure("203.0.113.9", f"u{i}")
    assert len(a._global._hits["failure"].q) <= 601
    assert len(a._global._hits["attempt"].q) <= 601
    assert len(a._source._hits["203.0.113.9"].q) <= 601
    snap = a.snapshot()
    assert 59_900 <= snap["failures"] <= 60_100              # 창(600초) 안 실패 ≈ 60,000(버킷 경계 ±1초)
    assert snap["attempts"] == snap["failures"]


def test_bucketed_counter_keeps_the_old_semantics_on_whole_seconds():
    clock = Clock(100.0)
    a = LoginAlarms(10, failures=3, attempts=0, source_failures=0, clock=clock)
    a.note_failure(None, "a")
    clock.t = 105.0
    a.note_failure(None, "b")
    clock.t = 109.0
    assert [e["count"] for e in a.note_failure(None, "c") if e["event_type"] == "login_failure_alarm"] == [3]
    clock.t = 110.0                                          # 100 의 기록은 정확히 창 끝에서 빠진다(t <= now - window)
    assert a.snapshot()["failures"] == 2
    clock.t = 119.0
    assert a.snapshot()["failures"] == 0


def test_account_event_is_once_per_account_and_capped_per_kind():
    clock = Clock()
    a = LoginAlarms(600, 0, 0, 0, clock=clock)
    a.account_event_budget = 3
    assert a.account_event("login_rate_limited", "user:a") == 2
    assert a.account_event("login_rate_limited", "user:a") is None     # 같은 아이디는 창에 한 번
    assert a.account_event("login_rate_limited", "user:b") == 1
    assert a.account_event("login_rate_limited", "user:c") == 0        # 마지막 몫(라우트가 안내를 붙인다)
    assert a.account_event("login_rate_limited", "user:d") is None     # 몫을 다 썼다
    assert a.account_event("login_attempt_limited", "user:d") == 2     # 종류마다 따로
    assert a.once("busy", "slots") is True                             # 경보 once 와 자리를 나누지 않는다
    clock.t += 600
    # 몫이 없어 버린 아이디(d)는 once 자리를 차지하지 않았다 -- 창이 지나 몫이 풀리면 바로 남는다
    assert a.account_event("login_rate_limited", "user:d") == 2


def test_account_event_budget_default_is_the_module_constant():
    from dms.api.login_guard import ACCOUNT_EVENT_BUDGET
    a = LoginAlarms(600, 0, 0, 0, clock=Clock())
    assert a.account_event_budget == ACCOUNT_EVENT_BUDGET == 50
    left = [a.account_event("login_rate_limited", f"user:u{i}") for i in range(60)]
    assert left[:50] == list(range(49, -1, -1)) and left[50:] == [None] * 10


def test_source_alarm_is_capped_per_window_like_account_events():
    # 2026-11-03 리뷰: 앞단 계약이 깨진 배치(직접 접속·port-forward)에선 원 IP(XFF 마지막 항목)도 클라이언트가 고른다 -- 위조
    # IP 하나마다 실패 source_failures 회로 이벤트 한 건이 생겨 events 유입이 공격 속도에 비례했다. 창당 몫으로 묶는다.
    clock = Clock()
    a = LoginAlarms(600, failures=0, attempts=0, source_failures=2, clock=clock)
    a.account_event_budget = 3
    lefts = []
    for i in range(6):                                       # 출발지 6개가 각자 임계(2)에 닿는다
        a.note_failure(f"10.0.0.{i}", "x")
        lefts.append([e["budget_left"] for e in a.note_failure(f"10.0.0.{i}", "x")])
    assert lefts == [[2], [1], [0], [], [], []]              # 몫 3건 -- 마지막 것은 0(라우트가 안내를 붙인다)
    assert a.account_event("login_rate_limited", "user:a") == 2          # 아이디 이벤트와 몫을 나누지 않는다(종류별)
    clock.t += 600
    # 몫이 없어 버린 출발지(10.0.0.3)는 once 자리를 차지하지 않았다 -- 창이 지나 몫이 풀리면 다시 임계에 닿는 순간 남는다
    a.note_failure("10.0.0.3", "x")
    assert [e["budget_left"] for e in a.note_failure("10.0.0.3", "x")] == [2]
    assert a.snapshot()["failures"] == 2                     # 몫과 무관하게 전체 실패는 다 센다(창이 지난 뒤 2건)


def test_once_sweep_threshold_is_amortized():
    # 고정 4096 문턱이면 창 안 키가 그보다 많을 때 호출마다 전체를 훑었다(출발지·아이디 키는 공격자가 고른다).
    a = LoginAlarms(600, 0, 0, 0, clock=Clock())
    for i in range(5000):
        assert a.once("source", f"10.0.{i // 256}.{i % 256}") is True
    assert a._emitted_sweep_at == 8192                       # 4096 에서 한 번 걷고(살아 있는 4096 개) 다음 문턱을 2배로
    assert len(a._emitted) == 5000
