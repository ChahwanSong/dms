"""로그인 감속기 단위 테스트(api/login_limiter.py) -- 가짜 시계로 창을 조작한다."""
from dms.api.login_limiter import LoginRateLimiter


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _limiter(max_attempts=10, window=60, clock=None):
    return LoginRateLimiter(max_attempts, window, clock=clock or Clock())


def test_allows_until_the_limit_then_blocks_with_retry_after():
    clock = Clock()
    lim = _limiter(clock=clock)
    for _ in range(9):
        assert lim.record_failure("user:a") == []
        assert lim.retry_after("user:a") is None
    assert lim.record_failure("user:a") == ["user:a"]   # 10번째에 상한 도달
    assert lim.retry_after("user:a") == 60
    clock.t += 59.2
    assert lim.retry_after("user:a") == 1               # 올림, 최소 1초
    clock.t += 1
    assert lim.retry_after("user:a") is None            # 창이 지나면 저절로 풀린다


def test_window_is_sliding_not_fixed():
    clock = Clock()
    lim = _limiter(clock=clock)
    for _ in range(5):
        lim.record_failure("k")
    clock.t += 30
    for _ in range(5):
        lim.record_failure("k")
    assert lim.retry_after("k") == 30                   # 가장 오래된 실패 기준
    clock.t += 30                                       # 앞 5개 만료
    assert lim.retry_after("k") is None
    assert lim.failures("k") == 5


def test_blocked_attempts_are_not_counted_by_design():
    # 상한에 닿은 뒤의 요청은 호출자가 record_failure 를 부르지 않는다(429 로 검증
    # 전에 거절) -- 그래서 공격자가 계정을 영구히 잠글 수 없다. 여기서는 그 계약을
    # 감속기 쪽에서 재확인한다: 창이 지나면 정확히 풀린다.
    clock = Clock()
    lim = _limiter(clock=clock)
    for _ in range(10):
        lim.record_failure("k")
    clock.t += 60
    assert lim.retry_after("k") is None


def test_multiple_keys_report_the_worst_wait():
    clock = Clock()
    lim = _limiter(max_attempts=2, clock=clock)
    lim.record_failure("ip:1")
    lim.record_failure("ip:1")
    clock.t += 20
    lim.record_failure("user:a")
    lim.record_failure("user:a")
    assert lim.retry_after("user:a", "ip:1") == 60      # user 키가 더 늦게 풀린다
    assert lim.retry_after("ip:1") == 40


def test_clear_only_touches_named_keys():
    lim = _limiter(max_attempts=1)
    lim.record_failure("user:a", "ip:1")
    lim.clear("user:a")
    assert lim.retry_after("user:a") is None
    assert lim.retry_after("ip:1") == 60


def test_threshold_is_reported_once_per_key():
    lim = _limiter(max_attempts=3)
    assert lim.record_failure("a", "b") == []
    assert lim.record_failure("a", "b") == []
    assert lim.record_failure("a", "b") == ["a", "b"]
    assert lim.record_failure("a") == []                # 초과분은 다시 알리지 않는다


def test_zero_disables_the_limiter():
    for lim in (_limiter(max_attempts=0), _limiter(window=0)):
        assert not lim.enabled
        for _ in range(50):
            assert lim.record_failure("k") == []
        assert lim.retry_after("k") is None


def test_sweep_drops_expired_keys_but_keeps_live_ones():
    clock = Clock()
    lim = _limiter(clock=clock)
    lim._sweep_at = 8                                    # 테스트용으로 문턱을 낮춘다
    for i in range(6):
        lim.record_failure(f"old:{i}")
    clock.t += 61
    lim.record_failure("live:1")
    for i in range(3):
        lim.record_failure(f"fresh:{i}")                 # 문턱 도달 -> 스윕
    assert not any(k.startswith("old:") for k in lim._failures)
    assert lim.failures("live:1") == 1 and lim.failures("fresh:0") == 1
