"""로그인 과부하·경보 장치(2026-11-03, 설계 v6 §5 · v3 2(마) 「대기형 전체 스위치 설계」).

세 장치 모두 프로세스 메모리(app.state)에 산다 -- dms-api 레플리카가 N 이면 실효 값이 N 배이고, 재시작이 모두 비운다
(replicas 1 전제는 login_limiter 와 같다). 시계는 주입할 수 있다(clock=time.monotonic -- 단위 테스트가 창을 조작한다).

1. GlobalPacer -- 대기형 전체 스위치(기본 꺼짐 = limit 0, 사용자 결정). 켜면 전 아이디의 로그인 **실패**를 W 초에 L 회
   속도로 늦춘다. 거절하지 않고 기다리게 하는 것이 요점이다(차단형 원안은 짧은 몰림에도 정상 로그인을 창 내내 막았다):
   - 예약형 토큰 버킷: 도착하면 토큰 1개를 먼저 뺀다(잔고가 0 아래로 가는 빚을 허용). 빚 d 개면 d ÷ (L/W) 초 뒤가 이
     요청의 차례다 -- 깨어날 시각을 도착 순서대로 미리 정하므로 깨어난 요청끼리 다시 다투지 않는다.
   - 예상 대기가 M 초를 넘으면 **상태를 바꾸지 않고** 거절한다(라우트가 503 login_busy + Retry-After). 상한이 없으면 대기
     요청·연결이 끝없이 쌓인다(라우트 타임아웃 0s·uvicorn 연결 상한 없음·프런트 fetch 타임아웃 없음). 이 503 은 차단이
     아니라 「지금 줄이 M 초보다 길다」는 용량 신호다 -- 누구에 대해서도 판정을 기억하지 않는다.
   - 401 이 아닌 결과(성공·재확인 429·슬롯 503·예외)는 라우트가 refund 한다 -- 「실패만 센다」(login_limiter)는 원칙.
2. LoginGate -- scrypt 대기형 슬롯(늘 켜짐). 비밀번호 검증(1회 16MiB·CPU 1코어)은 동시에 slots 개만 돈다. 슬롯을 못 얻은
   로그인은 **이벤트 루프에서**(스레드를 잡지 않고) 최대 max_wait 초 기다린 뒤 LoginBusy(라우트가 503 login_busy) --
   공격 중에도 나머지 API 와 /healthz 가 스레드풀·CPU 를 잃지 않게.
3. LoginAlarms -- 경보(이벤트만, 차단 없음). 창 안 전체 실패·전체 시도(성공 포함)·원 IP 하나의 실패를 세고, 임계에 닿으면
   (종류, 키)마다 창에 한 번만 이벤트 재료를 돌려준다(once -- 바쁨·대기 이벤트의 중복 방지에도 쓴다). 매 거절마다 쓰면
   공격자가 events 테이블을 채운다(routes_auth 의 기존 원칙). 키 공간(원 IP)은 공격자가 부풀릴 수 있어 login_limiter 처럼
   만료 키를 걷어 묶는다. 카운터는 초 단위 버킷이라 메모리가 공격 속도와 무관하다(2026-11-03 리뷰). 아이디별 「상한에
   닿음」 이벤트(login_rate_limited·login_attempt_limited)도 account_event 로 (종류, 아이디)마다 창에 한 번 + 종류별 창당
   ACCOUNT_EVENT_BUDGET 건으로 묶는다 -- 아이디는 공격자가 고르는 키 공간이다. 출발지 경보(login_source_alarm)도 같은 몫으로
   묶는다(2026-11-03 리뷰) -- 앞단 계약이 깨진 배치(직접 접속·port-forward·XFF 를 덧붙이지 않는 프록시)에선 원 IP(XFF 마지막
   항목)도 클라이언트가 고르는 키라, 위조 IP 하나마다 실패 source_failures 회로 이벤트 한 건을 만들 수 있었다.
"""
from __future__ import annotations

import asyncio
import math
import threading
import time
from collections import deque
from contextlib import asynccontextmanager

import anyio


# 아이디별 「상한에 닿음」 이벤트의 종류별 창(DMS_LOGIN_ALARM_WINDOW_SECONDS)당 상한(LoginAlarms.account_event, 2026-11-03
# 리뷰). 기본 창 600초면 하루 최대 7,200건 -- 사람이 보는 보안 이벤트 화면을 한 공격이 덮지 않을 만큼이고, 정상 운영(오타로
# 10회 실패한 사람 몇)은 닿지 않는다. 출발지 경보(login_source_alarm)도 같은 수로 묶는다(note_failure -- 정상 운영에서 창 안에
# 실패 20회를 넘기는 출발지는 몇 개뿐이다).
ACCOUNT_EVENT_BUDGET = 50


class LoginBusy(Exception):
    """scrypt 슬롯을 max_wait 초 안에 얻지 못했다 -- 라우트가 503 login_busy 로 바꾼다(과부하 신호, 차단 아님)."""


class GlobalPacer:
    def __init__(self, limit: int, window_seconds: int, max_wait_seconds: int, clock=time.monotonic):
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_wait_seconds = max_wait_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._tokens = float(limit) if self.enabled else 0.0
        self._last = clock()
        self._waiting = 0

    @property
    def enabled(self) -> bool:
        # 0 이하 = 꺼짐(login_limiter 와 같은 명시적 비활성 관례). 꺼져 있으면 라우트가 3단계를 통째로 건너뛴다.
        return self.limit > 0 and self.window_seconds > 0

    @property
    def rate(self) -> float:
        return self.limit / self.window_seconds

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._last)
        self._tokens = min(float(self.limit), self._tokens + elapsed * self.rate)
        self._last = now

    def reserve(self) -> tuple[bool, float]:
        """(받아들였나, 기다릴 초). 받아들이면 토큰을 이미 뺐다(빚 허용) -- 호출자는 그 초만큼 잔 뒤 진행하고, 결과가 401 이
        아니면 refund 한다. 예상 대기가 max_wait 를 넘으면 상태를 바꾸지 않고 (False, 예상 대기)."""
        if not self.enabled:
            return True, 0.0
        with self._lock:
            self._refill(self._clock())
            after = self._tokens - 1
            wait = max(0.0, -after / self.rate)
            if wait > self.max_wait_seconds:
                return False, wait
            self._tokens = after
            return True, wait

    def refund(self) -> None:
        """401 이 아닌 결과의 토큰을 돌려준다. 용량 L 을 넘지 않는다(돌려받은 토큰으로 버스트가 L 보다 커지지 않게)."""
        if not self.enabled:
            return
        with self._lock:
            self._refill(self._clock())
            self._tokens = min(float(self.limit), self._tokens + 1)

    def enter_wait(self) -> None:
        with self._lock:
            self._waiting += 1

    def leave_wait(self) -> None:
        with self._lock:
            self._waiting -= 1

    def snapshot(self) -> dict:
        """상태 화면용. tokens 는 꺼졌으면 None(「없음」 -- 0 은 「켜졌고 잔고 0」이라 섞지 않는다), 빚이면 음수."""
        with self._lock:
            if not self.enabled:
                return {"tokens": None, "waiting": self._waiting}
            self._refill(self._clock())
            return {"tokens": round(self._tokens, 2), "waiting": self._waiting}


class LoginGate:
    def __init__(self, slots: int, max_wait_seconds: int):
        self.slots = slots
        self.max_wait_seconds = max_wait_seconds
        self._lock = threading.Lock()
        self._sem: asyncio.Semaphore | None = None
        self._loop = None
        self._in_use = 0
        self._waiting = 0

    def _semaphore(self) -> asyncio.Semaphore:
        # 세마포어는 실행 중인 이벤트 루프마다 지연 생성한다(2026-11-03): asyncio 기본 요소는 처음 대기한 루프에 묶이는데,
        # starlette TestClient 는 with 없이 쓰면 요청마다 새 루프(새 스레드)를 쓴다 -- 한 번 만든 것을 다른 루프에서 기다리면
        # RuntimeError 다. 운영(uvicorn)은 루프가 하나라 처음 한 번만 만든다. 루프 객체를 강참조로 들고 있어 id 재사용으로
        # 헷갈릴 일이 없다. 테스트에서 루프 여럿이 동시에 돌면 루프마다 따로 세지만(상한이 루프 수만큼), 운영엔 해당 없다.
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._loop is not loop:
                self._sem = asyncio.Semaphore(self.slots)
                self._loop = loop
            return self._sem

    def _count(self, field: str, delta: int) -> None:
        with self._lock:
            setattr(self, field, getattr(self, field) + delta)

    @asynccontextmanager
    async def slot(self):
        """슬롯을 **직접** 잡는다(fail_after 안의 acquire, 해제는 finally). 검증은 이 안에서
        anyio.to_thread.run_sync(fn) 로 -- limiter 인자를 넘기지 않는다(2026-11-03): 같은 CapacityLimiter 를 슬롯과
        run_sync 양쪽에 쓰면 한 태스크가 토큰을 두 번 잡아 RuntimeError("this borrower is already holding one of this
        CapacityLimiter's tokens")가 난다(anyio 4.14 실측, v3 2(마) 6단계). 그래서 슬롯은 별도 세마포어이고 run_sync 는
        기본 스레드풀이다."""
        sem = self._semaphore()
        self._count("_waiting", 1)
        try:
            with anyio.fail_after(self.max_wait_seconds):
                await sem.acquire()
        except TimeoutError:
            raise LoginBusy() from None
        finally:
            self._count("_waiting", -1)
        self._count("_in_use", 1)
        try:
            yield
        finally:
            self._count("_in_use", -1)
            sem.release()

    def snapshot(self) -> dict:
        with self._lock:
            return {"in_use": self._in_use, "waiting": self._waiting}


class _Buckets:
    __slots__ = ("q", "total")

    def __init__(self):
        self.q: deque = deque()     # [초(floor), 그 초의 수] -- 오래된 것부터
        self.total = 0


class _SlidingCounter:
    """키별 슬라이딩 창 카운터 -- **초 단위 버킷**(2026-11-03 리뷰). 경보는 「창 안 개수가 임계를 넘었나」만 보면 되는데, 시도마다
    시각을 하나씩 들고 있으면 메모리가 「공격 속도 × 창(600초)」에 비례했다(없는 아이디 스프레이는 scrypt 를 타지 않아 빠르다 --
    실측 실패 1회당 약 73바이트). 버킷이면 키당 길이가 창 초 수 이하라 메모리가 공격 속도와 무관하다. 대가: 버킷 [b, b+1) 의
    기록은 b 가 창 밖이 되는 순간 함께 빠진다(최대 1초 이르게) -- 경보 임계 판정엔 무관하고, 정수 시각에서는 옛 동작과 같다.
    키 공간은 공격자가 부풀릴 수 있어 키 수가 문턱을 넘으면 창이 지난 키를 걷는다(login_limiter._sweep 과 같은 상각)."""

    def __init__(self, window_seconds: int):
        self.window_seconds = window_seconds
        self._hits: dict[str, _Buckets] = {}
        self._sweep_at = 4096

    def _expire(self, b: _Buckets, now: float) -> None:
        cutoff = now - self.window_seconds
        q = b.q
        while q and q[0][0] <= cutoff:
            b.total -= q.popleft()[1]

    def add(self, key: str, now: float) -> int:
        b = self._hits.get(key)
        if b is None:
            b = self._hits[key] = _Buckets()
        self._expire(b, now)
        second = math.floor(now)
        if b.q and b.q[-1][0] == second:
            b.q[-1][1] += 1
        else:
            b.q.append([second, 1])
        b.total += 1
        if len(self._hits) >= self._sweep_at:
            cutoff = now - self.window_seconds
            for k in [k for k, v in self._hits.items() if not v.q or v.q[-1][0] <= cutoff]:
                del self._hits[k]
            self._sweep_at = max(4096, 2 * len(self._hits))
        return b.total

    def count(self, key: str, now: float) -> int:
        b = self._hits.get(key)
        if b is None:
            return 0
        self._expire(b, now)
        return b.total


class LoginAlarms:
    """경보 재료를 돌려줄 뿐 이벤트는 쓰지 않는다 -- DB 쓰기는 라우트가 스레드에서(가능하면 슬롯 밖에서) 한다."""

    def __init__(self, window_seconds: int, failures: int, attempts: int, source_failures: int,
                 clock=time.monotonic):
        self.window_seconds = window_seconds
        self.failures = failures
        self.attempts = attempts
        self.source_failures = source_failures
        self._clock = clock
        self._lock = threading.Lock()
        # 카운터는 임계가 0(그 경보 끔)이어도 **늘 센다** -- 상태 화면이 최근 실패·시도 수를 보인다. 0 은 이벤트만 끈다.
        self._global = _SlidingCounter(window_seconds)
        self._source = _SlidingCounter(window_seconds)
        self._budget = _SlidingCounter(window_seconds)      # account_event·출발지 경보의 종류별 창 안 기록 수
        self.account_event_budget = ACCOUNT_EVENT_BUDGET    # 인스턴스 값 -- 라우트 안내문·테스트가 같은 값을 본다
        self._emitted: dict[tuple[str, str], float] = {}
        self._emitted_sweep_at = 4096

    def _once_locked(self, kind: str, key: str, now: float) -> bool:
        last = self._emitted.get((kind, key))
        if last is not None and now - last < self.window_seconds:
            return False
        self._emitted[(kind, key)] = now
        # 문턱은 상각한다(2026-11-03 리뷰): 고정 4096 이면 창 안 키가 그보다 많을 때(출발지·아이디 키는 공격자가 고른다) 호출마다
        # 전체를 훑어 실패 하나가 O(키 수)가 됐다. 걷은 뒤 살아남은 키의 2배(최소 4096)를 다음 문턱으로 -- login_limiter 와 같다.
        if len(self._emitted) >= self._emitted_sweep_at:
            for k in [k for k, t in self._emitted.items() if now - t >= self.window_seconds]:
                del self._emitted[k]
            self._emitted_sweep_at = max(4096, 2 * len(self._emitted))
        return True

    def once(self, kind: str, key: str) -> bool:
        """같은 (종류, 키)는 창(window_seconds)에 한 번만 True -- 경보·바쁨(login_busy)·대기 시작(login_pacing_engaged)
        이벤트를 공격 중에도 창당 한 건으로 묶는다."""
        with self._lock:
            return self._once_locked(kind, key, self._clock())

    def account_event(self, kind: str, key: str) -> "int | None":
        """아이디별 「상한에 닿음」 이벤트(login_rate_limited·login_attempt_limited)를 남길지(2026-11-03 리뷰). 키(아이디)는
        공격자가 고른다 -- 없는 아이디는 scrypt 를 건너뛰어 클라이언트 하나가 초당 천 건 넘게 실패하고, 아이디마다 한 건씩이면
        events 가 초당 백 건 넘게 쌓였다(예전엔 IP 키가 출발지당 분당 10실패로 묶었다). 그래서 두 겹으로 묶는다:
        (종류, 아이디)마다 창에 한 번(once) + 종류별로 창에 account_event_budget(기본 ACCOUNT_EVENT_BUDGET) 건. 넘친 것은
        남기지 않는다 -- 전체 실패 경보(login_failure_alarm)가 같은 창의 규모를 요약한다. 차단과는 무관하다(429 판정은 limiter
        가 그대로 한다).

        None = 남기지 않는다. 정수 = 이번 것을 남긴 뒤 이 창에 남은 몫(0 이면 이번이 마지막 -- 라우트가 메시지에 적는다).
        몫을 먼저 보고 once 를 쓴다: 몫이 없어 버린 아이디가 once 자리를 차지해 몫이 풀린 뒤에도 그 창 내내 빠지지 않게."""
        with self._lock:
            now = self._clock()
            if self._budget.count(kind, now) >= self.account_event_budget:
                return None
            if not self._once_locked(f"account:{kind}", key, now):
                return None
            return self.account_event_budget - self._budget.add(kind, now)

    def note_attempt(self) -> list[dict]:
        """전체 시도 +1(성공 포함 -- 맞는 비밀번호 반복의 scrypt 증폭도 보인다)."""
        with self._lock:
            now = self._clock()
            count = self._global.add("attempt", now)
            if 0 < self.attempts <= count and self._once_locked("attempt", "global", now):
                return [{"event_type": "login_attempt_alarm", "count": count,
                         "threshold": self.attempts, "window_seconds": self.window_seconds}]
        return []

    def note_failure(self, client_ip: str | None, username: str) -> list[dict]:
        """전체 실패 +1, 출발지(원 IP -- 모르면 "unknown") 실패 +1. 출발지 경보는 **차단하지 않는다** -- 운영자가 보고
        앞단(Avi·방화벽)에서 막을 재료다. 원 IP 가 프록시 한 주소로 모이는 배치는 임계를 0 으로(20-config.yaml).

        출발지 경보는 (출발지)마다 창에 한 번 + 창당 account_event_budget 건이다(2026-11-03 리뷰 -- 앞단 계약이 깨진 배치에선
        원 IP 도 클라이언트가 고르는 키라, 키 수에 상한이 없으면 events 유입이 공격 속도에 비례했다). 몫을 먼저 보고 once 를
        쓴다(account_event 와 같은 이유). 남긴 경보에는 budget_left(이번 것을 남긴 뒤 이 창에 남은 몫)가 붙는다 -- 라우트가
        0 이면 메시지에 안내를 붙이고 payload 에서는 뺀다. 넘친 출발지는 전체 실패 경보(login_failure_alarm)가 요약한다."""
        out: list[dict] = []
        source = "unknown" if client_ip is None else client_ip
        with self._lock:
            now = self._clock()
            count = self._global.add("failure", now)
            if 0 < self.failures <= count and self._once_locked("failure", "global", now):
                out.append({"event_type": "login_failure_alarm", "count": count,
                            "threshold": self.failures, "window_seconds": self.window_seconds})
            per_source = self._source.add(source, now)
            if (0 < self.source_failures <= per_source
                    and self._budget.count("login_source_alarm", now) < self.account_event_budget
                    and self._once_locked("source", source, now)):
                left = self.account_event_budget - self._budget.add("login_source_alarm", now)
                out.append({"event_type": "login_source_alarm", "count": per_source,
                            "threshold": self.source_failures, "window_seconds": self.window_seconds,
                            "last_username": username, "budget_left": left})
        return out

    def snapshot(self) -> dict:
        with self._lock:
            now = self._clock()
            return {"failures": self._global.count("failure", now),
                    "attempts": self._global.count("attempt", now)}


def retry_after_seconds(wait: float) -> int:
    """대기 초 → Retry-After 정수(≥1). 0.2초 대기도 「1초 뒤」로 -- 0 은 「지금 다시」라 몰림을 부른다."""
    return max(1, math.ceil(wait))
