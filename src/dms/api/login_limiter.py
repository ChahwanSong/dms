"""로그인 무차별 대입 감속(2026-09-07, 사용자 결정: **1분에 10회**).

키 두 벌을 따로 센다 -- 사용자명(계정 하나를 겨냥한 대입)과 클라이언트 IP(여러
계정에 흩뿌리는 spraying). 어느 한쪽이 상한이면 그 요청은 **비밀번호 검증 전에**
429 로 거절된다(검증이 곧 오라클이라 검증 뒤에 막으면 감속이 아니다).

**실패만** 센다. 성공 로그인은 대입이 아니고, 성공까지 세면 e2e·자동화가 스스로
잠긴다. 성공하면 그 사용자명 키를 비운다(정상 사용자가 오타 몇 번 뒤 성공했을 때
잔여 카운트가 다음 실수와 합쳐져 잠기지 않게). IP 키는 비우지 않는다 -- 한 계정
성공이 같은 IP 의 다른 계정 대입을 면제해 줄 이유가 없다.

창은 슬라이딩(실패 시각 deque)이다 -- 고정 창은 경계에서 상한의 2배가 샌다. 상한에
닿은 뒤 거절된 요청은 실패로 **세지 않는다**: 세면 공격자가 1분에 한 번씩 찔러
계정을 영구히 잠글 수 있다(DoS). 이건 잠금(lockout)이 아니라 감속이다 -- 창이
지나면 저절로 풀린다(10회/분 = 하루 14,400회 -- scrypt 해시 뒤의 온라인 대입으로는
무의미한 속도).

프로세스 메모리에 산다. 레플리카 N 개면 실효 상한이 N 배다(dms-api 는 replicas 1,
40-api.yaml 주석의 빌드 락 제약과 같은 이유로 당분간 1). 재시작하면 창이 비지만
"온라인 대입 감속"이라는 목적엔 영향이 없다. 키 공간은 공격자가 임의 사용자명·
IP 로 부풀릴 수 있으므로 기록 시점에 만료 키를 걷어 메모리를 (창 안 실패 수 ×
활성 키)로 묶는다.

max_attempts 또는 window_seconds 가 0 이하면 **명시적 비활성**(readyz_exit_failures=0
과 같은 관례) -- 운영자가 장치를 끄고 관찰만 하고 싶을 때의 탈출구다."""
import math
import threading
import time
from collections import deque


class LoginRateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int, clock=time.monotonic):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, deque] = {}
        self._sweep_at = 4096

    @property
    def enabled(self) -> bool:
        return self.max_attempts > 0 and self.window_seconds > 0

    def retry_after(self, *keys: str) -> int | None:
        """어느 키든 상한이면 가장 늦게 풀리는 키 기준 대기 초(≥1), 아니면 None."""
        if not self.enabled:
            return None
        now = self._clock()
        worst = None
        with self._lock:
            for key in keys:
                q = self._live(key, now)
                if q is not None and len(q) >= self.max_attempts:
                    wait = q[0] + self.window_seconds - now
                    worst = wait if worst is None else max(worst, wait)
        if worst is None:
            return None
        return max(1, math.ceil(worst))

    def record_failure(self, *keys: str) -> list[str]:
        """실패를 기록하고, **이번 실패로 상한에 막 닿은** 키를 돌려준다 -- 호출자가
        상한당 한 번만 이벤트를 남기게(매 거절마다 남기면 공격자가 이벤트 테이블을
        채운다)."""
        if not self.enabled:
            return []
        now = self._clock()
        reached: list[str] = []
        with self._lock:
            for key in keys:
                q = self._failures.setdefault(key, deque())
                self._expire(q, now)
                q.append(now)
                if len(q) == self.max_attempts:
                    reached.append(key)
            if len(self._failures) >= self._sweep_at:
                self._sweep(now)
        return reached

    def clear(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)

    def failures(self, key: str) -> int:
        """창 안 실패 수(테스트·진단용)."""
        with self._lock:
            q = self._live(key, self._clock())
            return 0 if q is None else len(q)

    # --- 내부 ---
    def _expire(self, q: deque, now: float) -> None:
        cutoff = now - self.window_seconds
        while q and q[0] <= cutoff:
            q.popleft()

    def _live(self, key: str, now: float):
        q = self._failures.get(key)
        if q is None:
            return None
        self._expire(q, now)
        if not q:
            del self._failures[key]
            return None
        return q

    def _sweep(self, now: float) -> None:
        for key in [k for k, q in self._failures.items()
                    if not q or q[-1] <= now - self.window_seconds]:
            del self._failures[key]
        # 다음 스윕 문턱: 살아남은 키의 2배(최소 4096) -- 스윕 비용을 상각한다.
        self._sweep_at = max(4096, 2 * len(self._failures))
