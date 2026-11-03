"""로그인 무차별 대입 감속(2026-09-07, 사용자 결정: **1분에 10회**) -- 아이디 키(+ 총 시도 상한에 재사용). IP 키 없음
(2026-11-03).

키는 아이디 하나다(`user:<아이디>` -- 계정 하나를 겨냥한 대입). 상한이면 그 요청은 **비밀번호 검증 전에** 429 로
거절된다(검증이 곧 오라클이라 검증 뒤에 막으면 감속이 아니다). 2026-11-03 까지 있던 클라이언트 IP 키(spraying 축)는
지웠다 -- SNAT·NAT 뒤의 전사가 한 IP 라 한 사람의 오타·스크립트가 전원의 로그인을 막았고, 앞단 헤더 계약이 틀리면 위조한
IP 로 남을 잠글 수 있었다. 원 IP 는 이제 기록·경보 전용이다(auth.client_ip, login_guard.LoginAlarms).

**실패 인스턴스(app.state.login_limiter)는 실패만** 센다. 성공 로그인은 대입이 아니고, 성공까지 세면 e2e·자동화가
스스로 잠긴다. 성공하면 그 아이디 키를 비운다(정상 사용자가 오타 몇 번 뒤 성공했을 때 잔여 카운트가 다음 실수와 합쳐져
잠기지 않게).

같은 클래스의 다른 인스턴스 둘(2026-11-03)은 다르게 쓴다 -- 클래스는 「창 안 기록 수가 상한이면 거절」만 안다:
- 총 시도 인스턴스(app.state.login_attempt_limiter, DMS_LOGIN_ID_ATTEMPT_*)는 **성공도 센다**(record 별칭) -- 맞는
  비밀번호를 반복해 scrypt 를 태우는 경로를 아이디별로 묶는다. 성공해도 비우지 않는다.
- 인증번호 전체 오답 인스턴스(app.state.verification_guess_limiter)는 키 "global" 하나로 모든 아이디의 틀린 인증번호를
  센다(routes_auth._consume_code).

창은 슬라이딩(실패 시각 deque)이다 -- 고정 창은 경계에서 상한의 2배가 샌다. 상한에 닿은 뒤 거절된 요청은 실패로
**세지 않는다**: 세면 공격자가 1분에 한 번씩 찔러 계정을 영구히 잠글 수 있다(DoS). 이건 잠금(lockout)이 아니라
감속이다 -- 창이 지나면 저절로 풀린다(10회/분 = 하루 14,400회 -- scrypt 해시 뒤의 온라인 대입으로는 무의미한 속도).

프로세스 메모리에 산다. 레플리카 N 개면 실효 상한이 N 배다(dms-api 는 replicas 1, 40-api.yaml 주석의 빌드 락 제약과
같은 이유로 당분간 1). 재시작하면 창이 비지만 "온라인 대입 감속"이라는 목적엔 영향이 없다. 키 공간은 공격자가 임의
아이디로 부풀릴 수 있으므로 기록 시점에 만료 키를 걷어 메모리를 (창 안 실패 수 × 활성 키)로 묶는다.

max_attempts 또는 window_seconds 가 0 이하면 **명시적 비활성**(readyz_exit_failures=0 과 같은 관례) -- 운영자가 장치를
끄고 관찰만 하고 싶을 때의 탈출구다."""
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
        # 키별 「상한에 닿았다」고 마지막으로 알린 시각(2026-11-03 리뷰). 알린 시각은 그 키의 마지막 기록 시각 이하라, 키의
        # deque 가 비어 지워질 때 함께 지운다(_live·_sweep·clear) -- 이 dict 는 _failures 키의 부분집합으로 묶인다.
        self._reported: dict[str, float] = {}
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
        채운다).

        「막 닿음」은 키마다 **창에 한 번**이다(2026-11-03 리뷰): 예전엔 기록 직후 길이 == 상한이면 돌려줬는데, 상한에 한 번
        닿은 뒤에도 가장 오래된 기록이 창 밖으로 빠질 때마다 한 건이 다시 받아들여져 길이가 또 상한이 된다 -- 상한 근처에
        머무는 정상 상태(총 시도 상한은 성공도 세서 잘못 돈 자동화 하나로 충분하다)에서 받아들여지는 시도마다 「막 닿음」이
        나와 events 를 초당 한 건씩 채웠다. 그래서 마지막으로 알린 시각에서 창이 지나야 다시 알린다(clear 는 알림도 지운다)."""
        if not self.enabled:
            return []
        now = self._clock()
        reached: list[str] = []
        with self._lock:
            for key in keys:
                q = self._failures.setdefault(key, deque())
                self._expire(q, now)
                q.append(now)
                if len(q) >= self.max_attempts:
                    last = self._reported.get(key)
                    if last is None or last <= now - self.window_seconds:
                        self._reported[key] = now
                        reached.append(key)
            if len(self._failures) >= self._sweep_at:
                self._sweep(now)
        return reached

    # 총 시도 상한(성공 포함, 2026-11-03)은 같은 기록을 「실패」가 아니라 「시도」로 부른다 -- 호출부가 읽히게 하는 별칭이다.
    record = record_failure

    def clear(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)
                self._reported.pop(key, None)

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
            self._reported.pop(key, None)
            return None
        return q

    def _sweep(self, now: float) -> None:
        for key in [k for k, q in self._failures.items()
                    if not q or q[-1] <= now - self.window_seconds]:
            del self._failures[key]
            self._reported.pop(key, None)
        # 다음 스윕 문턱: 살아남은 키의 2배(최소 4096) -- 스윕 비용을 상각한다.
        self._sweep_at = max(4096, 2 * len(self._failures))
