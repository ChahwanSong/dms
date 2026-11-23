"""API 파드 자체 artifact base 점검 모니터(2026-11-23 사고 대응, 설계 v2 §C).

사고: dms-api 가 CephFS 미마운트 노드(/mgmt_storage = 로컬 빈 디렉터리)에 떠 아티팩트·scan 리포트를 못 읽었는데,
control_state.artifact_base_check_ok 는 **컨트롤러 파드**(마운트된 노드) 기준이라 아무도 몰랐다. 이 모듈은 API 프로세스가
자기 파일시스템에서 같은 base 를 주기 점검해 메모리 스냅숏·DB(api_base_checks)·events 에 남기고, readyz 의 시작 게이트를
판정한다.

- 점검 = artifact_base.api_view_check(쓰기 없음: 디렉터리 열기 + 첫 dirent + 최근 잡 디렉터리 + 자세 + fstype). FS 접근은
  전부 프로세스 단일 BaseCheckRunner 안(10초 타임아웃, single-flight) -- readyz·요청 스레드는 메모리만 읽는다.
- 게이트는 **시작 래치 + 형제 조건**이다: 한 번 Ready(200)를 낸 프로세스는 base 때문에 다시 503 하지 않는다(런타임 실패는
  경고만). 시작 전에도 막는 것은 (첫 회차 미완료 ∧ 시작 60초 이내) 또는 (마지막 회차 실패 ∧ 서비스 중인 형제 파드 있음)뿐
  -- replicas 1 의 서지 없는 재시작(eviction·노드 재부팅·컨테이너 재시작)·새 설치·CephFS 전체 장애에서 부분 장애(아티팩트
  읽기)를 포탈 전면 장애로 키우지 않는다. 막아서 이득인 경우는 정확히 「옛 파드가 Ready 로 서비스 중」(롤링 서지)이다.
- create_app 은 늘 이 객체를 만들지만 **cli.py `dms api` 만 start** 한다 -- 테스트의 create_app 은 미시작 = 게이트 없음.
"""
import os
import socket
import sys
import threading
import time

from ..artifact_base import (CHECK_CACHE_SECONDS, NodeNameResolver, api_view_check, normalize_api_check,
                             identity_job_ids, resolve_artifact_base, strip_scheme)
from ..db import utc_now_iso

API_SELECTOR = "app.kubernetes.io/name=dms-api"
FIRST_TICK_GRACE_SECONDS = 60      # 첫 회차가 끝나기 전 막는 상한(그 뒤엔 결과 없이도 막지 않는다)
RETRY_BEFORE_FIRST_OK_SECONDS = 5
RETRY_WHILE_FAILING_SECONDS = 15
CHECK_NOW_WAIT_SECONDS = 12
_FRESH_FLOOR_SECONDS = 180

GATE_OFF = "off"                    # 모니터 끔(주기 0) 또는 미시작(테스트·로컬)
GATE_DISABLED = "disabled"          # env 비상구로 게이트만 끔
GATE_WAITING = "waiting"            # 아직 Ready(200)를 내지 않음
GATE_PASSED = "passed"              # 점검 ok 로 Ready
GATE_STARTED_DEGRADED = "started_degraded"   # 형제가 없어(또는 결과 없이) 실패인 채 Ready


def fresh_window_seconds(interval: int) -> int:
    """점검 기록이 「신선」한 창 -- 주기 3배, 최소 180초. 주기 0(끔)이면 180초."""
    return max(3 * interval, _FRESH_FLOOR_SECONDS) if interval > 0 else _FRESH_FLOOR_SECONDS


def api_check_key(path: str):
    return ("api", path)


def run_api_check(state, uri: str, *, cache_seconds: float = CHECK_CACHE_SECONDS, job_ids=None) -> dict:
    """API 홉 점검 한 번(모니터·요청 프로브·관리 GET 공용 진입점) -- api_view_check 모양으로 정규화해 돌려준다. job_ids 를
    주지 않으면 DB 에서 고르고, DB 가 실패하면 잡 판정만 생략한다(디렉터리 열기는 그대로 -- 실패 경로의 프로브가 DB 오류로
    500 이 되지 않게)."""
    path = strip_scheme(uri)
    runner = state.base_check_runner
    if job_ids is None:
        # 캐시 적중이면 DB 질의(최근 잡 200행)도 하지 않는다 -- 빈 목록 폴링(실행 전 잡)마다 프로브가 돈다.
        hit = runner.peek(api_check_key(path), cache_seconds)
        if hit is not None:
            return normalize_api_check(hit)
        try:
            job_ids = identity_job_ids(state.repos, state.settings, uri)
        except Exception:       # noqa: BLE001 -- 잡 판정 생략(모름)
            job_ids = []
    prefixes = getattr(state.settings, "artifact_base_allowed_prefixes", ())
    return normalize_api_check(runner.run(
        api_check_key(path), lambda: api_view_check(path, job_ids, prefixes), cache_seconds=cache_seconds))


class ApiBaseMonitor:
    def __init__(self, *, settings, repos, rollout_runner, runner, environ=None, clock=time.monotonic):
        self._settings = settings
        self._repos = repos
        self._rollout_runner = rollout_runner
        self._runner = runner
        self._clock = clock
        env = os.environ if environ is None else environ
        self.interval = int(getattr(settings, "api_artifact_base_check_seconds", 60))
        self.enabled = self.interval > 0
        self.gate_on = bool(getattr(settings, "api_artifact_base_startup_gate", True))
        self.instance = (env.get("HOSTNAME") or "").strip() or socket.gethostname()
        self._node = NodeNameResolver(rollout_runner, API_SELECTOR, environ=env, hostname=self.instance)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._snap = None
        self._started_mono = None
        self.started_at = None
        self._served_ready = False
        self._ready_at = None
        self._latched_state = None
        self._first_ok_at = None
        self._peer_ready = False
        self._seq_started = 0
        self._seq_done = 0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self.last_db_error_at = None

    # ---- 수명 ----
    @property
    def started(self) -> bool:
        return self._thread is not None

    def start(self, *, spawn: bool = True) -> None:
        """모니터 시작(시작 시각 = 게이트의 60초 기준). spawn False 는 테스트용 -- 스레드 없이 시작 상태만 만들고 회차는
        호출자가 tick() 으로 돌린다(비결정적 백그라운드 회차 없이 게이트 행렬을 검증)."""
        if not self.enabled or self._thread is not None:
            return
        self._started_mono = self._clock()
        self.started_at = utc_now_iso()
        self._thread = threading.Thread(target=self._loop, name="dms-api-base-monitor", daemon=True)
        if spawn:
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def kick(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:       # noqa: BLE001 -- 모니터 스레드는 죽지 않는다(다음 회차가 다시 본다)
                print(f"api artifact base monitor: tick failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            self._wake.wait(self._next_delay())
            self._wake.clear()

    def _next_delay(self) -> float:
        snap = self._snap
        if self._first_ok_at is None:
            return RETRY_BEFORE_FIRST_OK_SECONDS
        if snap is not None and snap["ok"] is False:
            return RETRY_WHILE_FAILING_SECONDS
        return self.interval

    # ---- 회차 ----
    def tick(self) -> "dict | None":
        with self._cond:
            self._seq_started += 1
            seq = self._seq_started
        try:
            return self._tick()
        finally:
            with self._cond:
                self._seq_done = max(self._seq_done, seq)
                self._cond.notify_all()

    def _tick(self) -> "dict | None":
        now_iso = utc_now_iso()
        try:
            uri = resolve_artifact_base(self._repos.control, self._settings)
            job_ids = identity_job_ids(self._repos, self._settings, uri)
        except Exception as exc:       # noqa: BLE001 -- DB 실패: 회차를 건너뛰고 스냅숏은 그대로(시각이 낡아 stale 로 보인다)
            self.last_db_error_at = now_iso
            print(f"api artifact base monitor: db unavailable, round skipped: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return None
        res = run_api_check(self._holder(), uri, cache_seconds=0, job_ids=job_ids)
        avail = res["availability"]
        node = self._node.get()
        peer = self._peer_ready
        if not self._served_ready:
            peer = self._compute_peer_ready(uri, now_iso)
        with self._lock:
            prev = self._snap
            ok = avail["ok"] is True
            if ok and self._first_ok_at is None:
                self._first_ok_at = now_iso
            self._peer_ready = peer
            snap = {
                "uri": uri, "ok": ok, "reason": avail["reason"], "posture": res["posture"],
                "fstype": res["fstype"], "local_fs": res["local_fs"],
                "jobs_checked": avail["jobs_checked"], "jobs_found": avail["jobs_found"],
                "checked_at": now_iso, "checked_mono": self._clock(), "stuck_since": res.get("stuck_since"),
                "started_at": self.started_at, "instance": self.instance, "node_name": node,
                "first_ok_at": self._first_ok_at, "ready_at": self._ready_at, "peer_ready": peer,
            }
            self._snap = snap
        self._note_transition(prev, snap)
        self._persist()
        return snap

    def _holder(self):
        # run_api_check 는 app.state 모양(repos·settings·base_check_runner)을 받는다 -- 모니터 자신을 그 모양으로 넘긴다.
        return _StateView(self._repos, self._settings, self._runner)

    def _compute_peer_ready(self, uri: str, now_iso: str) -> bool:
        """서비스 중인 형제 dms-api 파드가 있나. k8s(파드 Ready 조건)가 답하면 그것만, 못 보면(스텁·조회 실패) DB 의
        다른 인스턴스 신선 행(같은 uri·ok·ready_at 있음)으로 대신한다 -- 죽은 파드의 행은 신선 창(최소 180초)이 지나면
        빠진다."""
        try:
            try_fn = getattr(self._rollout_runner, "try_pod_briefs", None)
            briefs = try_fn(selector=API_SELECTOR) if try_fn is not None else None
            rows = None
            try:
                rows = self._repos.api_base_checks.list_recent(
                    now_iso=now_iso, within_seconds=fresh_window_seconds(self.interval))
            except Exception as exc:       # noqa: BLE001 -- DB 를 못 보면 k8s Ready 만으로(아래)
                print(f"api artifact base monitor: peer rows unavailable: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
            if briefs is not None:
                # Ready ≠ 건강: 한 번 Ready 가 된 파드는 base 가 깨져도 Ready 를 유지한다(런타임은 경고만). 같은 uri 에서
                # 실패 중인 신선 행이 있는 형제는 세지 않는다 -- 옛 파드도 같은 장애면 막아서 지킬 「정상 옛 파드」가 없고
                # 릴리스만 멈춘다(2026-11-23 리뷰). 행이 없는 형제(옛 버전 파드·첫 배포)는 Ready 만으로 센다.
                failing = {r["instance"] for r in (rows or []) if r["uri"] == uri and r["ok"] is False}
                return any(b.get("name") != self.instance and b.get("deleting") is not True
                           and b.get("ready") is True and b.get("name") not in failing for b in briefs)
            return any(r["instance"] != self.instance and r["ok"] is True and r["uri"] == uri
                       and r["ready_at"] is not None for r in (rows or []))
        except Exception as exc:       # noqa: BLE001 -- 모름 = 형제 없음(막지 않는 쪽)
            print(f"api artifact base monitor: peer lookup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return False

    def _note_transition(self, prev, snap) -> None:
        """ok 반전·사유 변화마다 events + stderr 1줄(kubelet 이벤트엔 503 코드만 남는다). 첫 회차가 정상이면 남기지 않는다."""
        if prev is None and snap["ok"]:
            return
        if prev is not None and prev["ok"] == snap["ok"] and prev["reason"] == snap["reason"]:
            return
        recovered = snap["ok"]
        event_type = "api_artifact_base_recovered" if recovered else "api_artifact_base_unavailable"
        message = (f"instance={self.instance} node={snap['node_name'] or '-'} "
                   f"reason={snap['reason'] or '-'} uri={snap['uri']}")
        print(f"api artifact base: {event_type} {message}", file=sys.stderr)
        self._repos.observability.record_event(
            component="api-artifact-base", severity="info" if recovered else "warning",
            event_type=event_type, message=message[:500],
            payload={"instance": self.instance, "node_name": snap["node_name"],
                     "reason": snap["reason"], "uri": snap["uri"]})

    def _persist(self) -> None:
        """api_base_checks upsert + 24시간 지난 행 정리(best-effort -- 기록 실패가 점검·게이트를 막지 않는다)."""
        snap = self._snap
        if snap is None:
            return
        try:
            self._repos.api_base_checks.upsert({
                "instance": self.instance, "node_name": snap["node_name"], "uri": snap["uri"],
                "ok": snap["ok"], "reason": snap["reason"], "posture": snap["posture"],
                "fstype": snap["fstype"], "gate_state": self.gate_state(),
                "started_at": self.started_at or snap["checked_at"], "checked_at": snap["checked_at"],
                "first_ok_at": self._first_ok_at, "ready_at": self._ready_at})
            self._repos.api_base_checks.prune()
        except Exception as exc:       # noqa: BLE001
            print(f"api artifact base monitor: record failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    # ---- 게이트(readyz 가 메모리만 읽는다) ----
    def gate_blocking(self) -> bool:
        if not (self.enabled and self.gate_on and self.started) or self._served_ready:
            return False
        snap = self._snap
        if snap is None:
            return self._clock() - self._started_mono < FIRST_TICK_GRACE_SECONDS
        return snap["ok"] is False and self._peer_ready

    def mark_served_ready(self) -> None:
        """readyz 가 200 을 낼 때 부른다(래치). 그때의 스냅숏으로 gate_state 를 굳히고 기록을 앞당긴다(kick)."""
        if self._served_ready:
            return
        with self._lock:
            if self._served_ready:
                return
            self._served_ready = True
            self._ready_at = utc_now_iso()
            snap = self._snap
            self._latched_state = (GATE_PASSED if snap is not None and snap["ok"] is True
                                   else GATE_STARTED_DEGRADED)
            if snap is not None:
                self._snap = {**snap, "ready_at": self._ready_at}
        if self.started:
            self.kick()

    def gate_state(self) -> str:
        if not self.enabled or not self.started:
            return GATE_OFF
        if not self.gate_on:
            return GATE_DISABLED
        if self._served_ready:
            return self._latched_state
        return GATE_WAITING

    # ---- 읽기 ----
    @property
    def ready_at(self) -> "str | None":
        return self._ready_at

    def node_name(self) -> "str | None":
        snap = self._snap
        if snap is not None and snap["node_name"] is not None:
            return snap["node_name"]
        return self._node.get()

    def snapshot(self) -> "dict | None":
        """현재 스냅숏(복사본) + 게이트 상태. 아직 회차가 없으면 None."""
        snap = self._snap
        if snap is None:
            return None
        out = {k: v for k, v in snap.items() if k != "checked_mono"}
        out["gate_state"] = self.gate_state()
        out["served_ready"] = self._served_ready
        return out

    def degraded(self) -> bool:
        """readyz 200 본문의 "artifact_base":"degraded" 조건 -- 스냅숏이 실패일 때만(모름은 아님)."""
        snap = self._snap
        return snap is not None and snap["ok"] is False

    def is_fresh(self, snap_or_none=None) -> bool:
        snap = self._snap if snap_or_none is None else snap_or_none
        return (snap is not None and "checked_mono" in snap
                and self._clock() - snap["checked_mono"] <= fresh_window_seconds(self.interval))

    def reports_timeout(self, uri: str) -> bool:
        """신선한 현재 uri 스냅숏이 타임아웃(멈춘 마운트)을 말하는가 -- 라우트의 hung 단락 신호."""
        snap = self._snap
        return (snap is not None and snap["uri"] == uri and snap["reason"] == "artifact_base_check_timeout"
                and self.is_fresh(snap))

    def check_now(self, timeout: float = CHECK_NOW_WAIT_SECONDS) -> bool:
        """지금 시작하는 회차가 끝날 때까지(최대 timeout) 기다린다. 모니터가 돌지 않으면 False(호출자가 즉석 점검)."""
        if not self.started:
            return False
        deadline = self._clock() + timeout
        with self._cond:
            want = self._seq_started + 1
        self.kick()
        with self._cond:
            while self._seq_done < want:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
        return True


class _StateView:
    __slots__ = ("repos", "settings", "base_check_runner")

    def __init__(self, repos, settings, runner):
        self.repos = repos
        self.settings = settings
        self.base_check_runner = runner
