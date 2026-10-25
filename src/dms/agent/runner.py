"""에이전트 러너: 프로브 → POST → 응답으로 설정 갱신. DB를 모르는 순수 HTTP 클라이언트.

k8s 인벤토리 워커(2026-10-25 dms-k8s 설계 §4): 보고 루프와 **분리된** 데몬 스레드(K8sSyncWorker). 보고 응답의 k8s_sync.role 이
primary·secondary 인 동안만 claim 을 폴링해 외부 k8s API 를 부르고(agent/k8s_fetch.py) 결과를 올린다. 수십 초짜리 목록 조회가
보고 루프 안에 있으면 노드 보고가 늦어져 stale 이 되고 storages.status·페일오버 판정까지 오염된다(reconciler).
"""
import sys
import threading
import time
from typing import Callable, Optional

import httpx

from ..config import AGENT_TOOL_NAMES, AgentSettings
from ..db import utc_now_iso
from .directory import NslcdDirectory
from .k8s_fetch import fetch_inventory
from .probes import (CPU_COUNTER_KEYS, probe_artifact_base, probe_identities, probe_k8s_node,
                     probe_mounts, probe_os_metrics, probe_tools)


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _counter(block, key):
    # bool 은 int 의 서브클래스 -- True 가 1 jiffy 로 새면 안 된다(metrics_series._num 과 같은 가드)
    value = block.get(key) if isinstance(block, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _share(prev, cur, d_total):
    """구간 비율(%) -- 카운터가 없거나 줄었거나 결과가 0..100 밖이면 None(모름). 0 은 정상값이다."""
    if prev is None or cur is None or cur < prev:
        return None
    pct = (cur - prev) / d_total * 100
    return round(pct, 1) if 0 <= pct <= 100 else None


def cpu_utilisation(prev, cur) -> dict:
    """직전 표본(prev)과 이번 표본(cur)의 /proc/stat 누적 카운터(probes.CPU_COUNTER_KEYS) -> 구간 CPU 사용률.

    2026-10-16(사용자 보고 「코어수가 128이어도 200이 넘어가고」): 대시보드의 "CPU" 는 load1 이었다 -- 사용률은
    누적 카운터의 **차분**이라 표본 사이 상태가 필요하고, 그 상태를 가진 것이 러너다. 서버가 시계열 인접
    차분으로 계산하지 않는 이유: 노드 화면(/api/admin/nodes)은 최신 보고 1건만 보므로 보고 안에 값이 있어야
    시계열·최신값 화면이 같은 숫자를 낸다.

    - cpu_used_pct = (Δtotal - Δidle) / Δtotal, cpu_iowait_pct = Δiowait / Δtotal (×100, 소수 1자리).
    - None(모름): 첫 표본(prev 없음), Δtotal <= 0(같은 틱 안의 두 표본), total 감소(재부팅 리셋 -- 음수 사용률을
      그리느니 빈 칸이 정직하다, metrics_series._rate 와 같은 규칙), 카운터 결측·비정수.
    - 감소 판정은 지표별이다: total 이 줄면 둘 다 None, idle(idle+iowait) 이 줄면 사용률만, iowait 만 줄면
      I/O 대기만 None. iowait 단독 감소는 nohz 커널의 알려진 흔들림(proc(5))이라 그 때문에 주 지표인 사용률
      칸까지 비우지 않는다 -- idle+iowait 합은 그 흔들림에도 단조다."""
    out = {"cpu_used_pct": None, "cpu_iowait_pct": None}
    t0, t1 = _counter(prev, "cpu_total_jiffies"), _counter(cur, "cpu_total_jiffies")
    if t0 is None or t1 is None or t1 <= t0:
        return out
    d_total = t1 - t0
    i0, i1 = _counter(prev, "cpu_idle_jiffies"), _counter(cur, "cpu_idle_jiffies")
    if i0 is not None and i1 is not None and i1 >= i0:
        used = (d_total - (i1 - i0)) / d_total * 100
        # Δidle > Δtotal(다른 성분이 줄어든 오염)은 음수 사용률이다 -- 범위 밖은 모름
        if 0 <= used <= 100:
            out["cpu_used_pct"] = round(used, 1)
    out["cpu_iowait_pct"] = _share(_counter(prev, "cpu_iowait_jiffies"),
                                   _counter(cur, "cpu_iowait_jiffies"), d_total)
    return out


_NODE_CPU_KEYS = ("allocatable_cpu_m", "capacity_cpu_m")


def carry_node_cpu(last, k8s_node) -> tuple:
    """(직전에 읽은 노드 CPU | None, 이번 보고의 k8s_node) -> (실을 k8s_node, 다음에 기억할 노드 CPU). 순수 함수.

    2026-10-16 리뷰: 노드 CPU 상한(node_cpu)은 보고의 allocatable 만 보고, 서버는 노드의 **최신** 보고 하나만 읽는다
    (agent_nodes 를 통째로 바꾼다). Node GET 이 한 번 실패하면(probe_failed -- CPU 키 없음) 그 보고 주기 동안 상한이
    「모름」이 되고, 그 창에 제출된 vcjob 은 클램프 없이 나가 gang 이 영원히 서지 않았다. allocatable 은 정적인 값이라
    마지막으로 읽은 값이 「모름」보다 낫다 -- 이번에 allocatable 을 모르면(GET 실패·이상한 수량) 기억한 값을 싣고
    allocatable_stale=True 로 표시한다. 스케줄 판정(schedulable·reason)은 손대지 않는다(모름은 모름 -- cordon 을 지어내지
    않는다). 기억은 프로세스 메모리뿐이다(재시작 직후 첫 읽기 전은 여전히 모름 -- fail-open)."""
    if not isinstance(k8s_node, dict):
        return k8s_node, last
    alloc = k8s_node.get("allocatable_cpu_m")
    if isinstance(alloc, int) and not isinstance(alloc, bool):
        return k8s_node, {k: k8s_node.get(k) for k in _NODE_CPU_KEYS}
    if last is None:
        return k8s_node, None
    return {**k8s_node, **last, "allocatable_stale": True}, last


def build_report(node_name, storages, probe_targets, *, mountinfo_text,
                 tool_names=AGENT_TOOL_NAMES, mounts_fn=None, tools_fn=None,
                 identities_fn=None, os_fn=None, read_text=None,
                 net_dev_path="/proc/net/dev", virtual_net_path="",
                 artifact_base_path=None, artifact_base_fn=None,
                 host_root="", self_mountinfo_text="", directory_status=None,
                 k8s_node_fn=None, k8s_sync_status=None) -> dict:
    mounts_fn = mounts_fn or probe_mounts
    tools_fn = tools_fn or probe_tools
    identities_fn = identities_fn or probe_identities
    os_fn = os_fn or probe_os_metrics
    artifact_base_fn = artifact_base_fn or probe_artifact_base
    k8s_node_fn = k8s_node_fn or probe_k8s_node
    # 이 폴백을 지우면 probe_os_metrics 가 read_text=None 을 받는다 -- 그 안의
    # 호출은 전부 try/except Exception 이라 예외가 삼켜지고 **OS 지표 전체가
    # 조용히 null** 이 된다(테스트는 os_fn 을 주입하므로 초록을 유지한다).
    read_text = read_text or _read_text
    report = {
        "node_name": node_name,
        "probed_at": utc_now_iso(),
        # 방안 A(2026-09-16): host_root 가 있으면 경로 프로브는 전부 그 접두 아래를
        # 본다. 화면·진단이 어느 모드로 잰 값인지 알도록 모드를 함께 싣는다.
        "probe_mode": "host_root" if host_root else "direct",
        "mounts": mounts_fn(storages, mountinfo_text=mountinfo_text,
                            host_root=host_root, self_mountinfo_text=self_mountinfo_text),
        "tools": tools_fn(list(tool_names)),
        "identities": identities_fn(probe_targets),
        "os": os_fn(storages, read_text=read_text, net_dev_path=net_dev_path,
                    virtual_net_path=virtual_net_path, host_root=host_root),
        # 슬라이스 18: mounts 와 **별도** 최상위 필드 -- reconciler 가 mounts 를
        # storages.status 로 매핑하므로 거기 섞으면 스토리지 판정이 오염된다.
        # 대상 경로가 아직 없으면(부트스트랩) None.
        "artifact_base": artifact_base_fn(artifact_base_path, host_root=host_root,
                                          mountinfo_text=mountinfo_text),
        # 2026-10-02 노드 배치 제외의 cordon 자동 반영(probes.probe_k8s_node): 자기 노드가 k8s 에서 스케줄 불가
        # (cordon·NoSchedule/NoExecute taint)면 schedulable=False -- planner·stepper 가 이 노드를 후보에서 뺀다.
        # null 은 모름(조회 실패) -- 막지 않는다.
        "k8s_node": k8s_node_fn(node_name),
    }
    # 2026-09-29: nslcd 를 관리하는 에이전트만 싣는다 -- 이 키가 곧 "디렉터리 설정을
    # 받아 적용할 수 있다"는 능력 선언이고(서버는 키가 없으면 비밀번호를 보내지 않는다),
    # 값은 비밀 없는 적용 상태(해시·출처·bind 모드·오류)다.
    if directory_status is not None:
        report["directory"] = directory_status
    # k8s 인벤토리 워커(2026-10-25): 워커 스레드가 있는 에이전트만 싣는다 -- {"v": 1} 이 곧 능력 선언이다(서버는 선언한
    # 노드에게만 run·클러스터 토큰을 준다). 값은 비밀 없는 워커 상태(idle/running, 진행 중 run, 마지막 결과).
    if k8s_sync_status is not None:
        report["k8s_sync"] = k8s_sync_status
    return report


class K8sSyncWorker:
    """k8s 인벤토리 동기화 워커(데몬 스레드). 역할(role)은 보고 응답이 정한다 -- None 이면 쉬고, primary·secondary 면
    poll_seconds 마다 POST /api/agent/k8s-sync/claim. run 을 받으면 fetch_inventory 로 처리해 결과를 올리고 **바로 다시**
    claim 한다(여러 클러스터는 직렬). 토큰은 run 처리 동안만 메모리에 있다 -- 로그에는 예외 유형만 남긴다."""

    CLAIM_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
    RESULT_TIMEOUT = httpx.Timeout(60.0, connect=5.0)

    def __init__(self, settings: AgentSettings, *, client_factory: Optional[Callable[[], httpx.Client]] = None,
                 fetch=fetch_inventory):
        self._settings = settings
        self._client_factory = client_factory or (lambda: httpx.Client(base_url=settings.api_url))
        self._fetch = fetch
        self._lock = threading.Lock()
        self._role: Optional[str] = None
        self._poll = 15
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._state = "idle"
        self._run_id: Optional[str] = None
        self._last: Optional[dict] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def role(self) -> Optional[str]:
        with self._lock:
            return self._role

    def configure(self, block) -> None:
        """보고·claim 응답의 k8s_sync({role, poll_seconds}) 반영. 모양이 틀리면 역할 없음(쉰다 -- 더 좁은 쪽)."""
        role = block.get("role") if isinstance(block, dict) else None
        poll = block.get("poll_seconds") if isinstance(block, dict) else None
        with self._lock:
            was = self._role
            self._role = role if role in ("primary", "secondary") else None
            if isinstance(poll, int) and not isinstance(poll, bool) and poll >= 1:
                self._poll = poll
            now_role = self._role
        if now_role is not None and was is None:
            self._wake.set()          # 방금 지정됐다 -- 다음 폴 주기를 기다리지 않는다

    def status(self) -> dict:
        with self._lock:
            return {"v": 1, "worker": self._state, "role": self._role, "run_id": self._run_id,
                    "last": dict(self._last) if self._last else None}

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._settings.shared_token}",
                "x-dms-actor": f"node:{self._settings.node_name}"}

    def run_cycle(self, client: httpx.Client) -> bool:
        """claim 한 번 → run 이 있으면 처리·결과 POST. 처리했으면 True(곧바로 다음 claim)."""
        resp = client.post("/api/agent/k8s-sync/claim", json={"node_name": self._settings.node_name},
                           headers=self._headers(), timeout=self.CLAIM_TIMEOUT)
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict):
            raise ValueError("non-dict claim response")
        self.configure(body)
        run = body.get("run")
        if not isinstance(run, dict) or not isinstance(run.get("run_id"), str):
            return False
        run_id = run["run_id"]
        with self._lock:
            self._state, self._run_id = "running", run_id
        try:
            result = self._fetch(run)
            result["node_name"] = self._settings.node_name
            posted = client.post(f"/api/agent/k8s-sync/runs/{run_id}", json=result, headers=self._headers(),
                                 timeout=self.RESULT_TIMEOUT)
            delivered = posted.status_code
            with self._lock:
                self._last = {"run_id": run_id, "cluster": run.get("cluster"), "ok": bool(result.get("ok")),
                              "reason": result.get("reason"), "at": utc_now_iso(), "delivered": delivered}
            if delivered >= 400:
                print(f"k8s sync result rejected run={run_id} status={delivered}", file=sys.stderr)
        finally:
            run.clear()                # 토큰을 들고 있지 않는다
            with self._lock:
                self._state, self._run_id = "idle", None
        return True

    def _loop(self) -> None:
        with self._client_factory() as client:
            while not self._stop.is_set():
                if self.role is None:
                    self._wake.wait(self._poll)
                    self._wake.clear()
                    continue
                try:
                    processed = self.run_cycle(client)
                except Exception as exc:  # noqa: BLE001 -- 워커는 죽지 않는다; 메시지는 찍지 않는다(토큰 노출 방지)
                    print(f"k8s sync worker cycle failed: {type(exc).__name__}", file=sys.stderr)
                    processed = False
                if not processed:
                    self._wake.wait(self._poll)
                    self._wake.clear()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="dms-k8s-sync", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)


class AgentRunner:
    def __init__(self, settings: AgentSettings, client: httpx.Client,
                 directory: Optional[NslcdDirectory] = None,
                 k8s_worker: Optional[K8sSyncWorker] = None):
        self._settings = settings
        self._client = client
        self._directory = directory or NslcdDirectory(managed=settings.nslcd_managed)
        # k8s 인벤토리 워커(2026-10-25) -- run_loop 가 상주 모드에서만 만든다(--once 엔 없다 = 능력 미선언).
        self._k8s_worker = k8s_worker
        # 직전 /proc/stat 카운터(cpu_utilisation 의 prev). 프로세스 메모리에만 둔다 -- 재시작 직후 첫 보고는
        # 사용률 None(모름)이고 다음 보고부터 채워진다. state(응답에서 다시 짓는 설정 묶음)에 섞지 않는 이유:
        # 보고 실패 시 run_once 가 옛 state 를 그대로 돌려주므로 거기 두면 표본이 갱신되지 않는다.
        self._cpu_prev: Optional[dict] = None
        # 마지막으로 읽은 노드 allocatable/capacity(carry_node_cpu). 프로세스 메모리 -- _cpu_prev 와 같은 이유로 state 밖.
        self._node_cpu_last: Optional[dict] = None

    def _attach_cpu_utilisation(self, report: dict) -> None:
        """보고 os 블록에 cpu_used_pct·cpu_iowait_pct 를 싣고 이번 카운터를 다음 차분의 기준으로 기억한다.
        POST 성패와 무관하게 기준을 옮긴다 -- 다음 보고의 값이 "직전 보고 주기"의 사용률이 되게(실패가 길게
        이어진 뒤 첫 성공 보고가 그 긴 구간의 평균을 내지 않게)."""
        os_block = report.get("os")
        if not isinstance(os_block, dict):
            return
        os_block.update(cpu_utilisation(self._cpu_prev, os_block))
        # 카운터가 None 인 표본을 지나면 기준도 None -- 다음 한 번은 모름(metrics_series 의 네트워크 차분과 같은 관례)
        self._cpu_prev = {key: os_block.get(key) for key in CPU_COUNTER_KEYS}

    def run_once(self, state: dict) -> dict:
        try:
            mountinfo_text = _read_text(self._settings.mountinfo_path)
        except OSError:
            mountinfo_text = ""
        # 전파 자가 진단용 자기 마운트 표 -- host_root 모드에서만 의미가 있고, 못
        # 읽으면 빈 문자열(검사 생략, 거짓 stale 금지 -- probes.probe_mounts).
        self_mountinfo_text = ""
        if self._settings.host_root:
            try:
                self_mountinfo_text = _read_text("/proc/self/mountinfo")
            except OSError:
                self_mountinfo_text = ""
        report = build_report(self._settings.node_name, state["storages"],
                              state["probe_targets"], mountinfo_text=mountinfo_text,
                              net_dev_path=self._settings.net_dev_path,
                              virtual_net_path=self._settings.virtual_net_path,
                              artifact_base_path=state.get("artifact_base_path"),
                              host_root=self._settings.host_root,
                              self_mountinfo_text=self_mountinfo_text,
                              directory_status=self._directory.report(),
                              k8s_sync_status=(self._k8s_worker.status()
                                               if self._k8s_worker is not None else None))
        self._attach_cpu_utilisation(report)
        report["k8s_node"], self._node_cpu_last = carry_node_cpu(self._node_cpu_last, report.get("k8s_node"))
        try:
            response = self._client.post(
                "/api/agent/report", json=report,
                headers={
                    "Authorization": f"Bearer {self._settings.shared_token}",
                    "x-dms-actor": f"node:{self._settings.node_name}",
                })
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError(f"non-dict response: {type(body).__name__}")
            storages = body.get("storages", state["storages"])
            probe_targets = body.get("identity_probe_targets", state["probe_targets"])
            interval = body.get("report_interval_seconds", state["interval"])
            # 에이전트는 ConfigMap 을 envFrom 으로 받지 않는다(50-agent-daemonset
            # .yaml 머리 주석) -- base 를 아는 유일한 경로가 이 응답 필드다(설계
            # §2.4b). 구버전 서버 응답에는 키가 없다 -- 기존 값을 유지한다(지우면
            # 다음 리포트부터 프로브가 사라져 화면이 영구 "확인 대기 중"이 된다).
            artifact_base_path = body.get("artifact_base_path",
                                          state.get("artifact_base_path"))
            if (not isinstance(storages, list) or not isinstance(probe_targets, list)
                    or not isinstance(interval, int) or isinstance(interval, bool)
                    or not isinstance(artifact_base_path, (str, type(None)))):
                raise ValueError("malformed response fields")
            new_state = {"storages": storages, "probe_targets": probe_targets,
                         "interval": interval,
                         "artifact_base_path": artifact_base_path}
        except Exception as exc:
            print(f"agent report failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return state
        # 디렉터리 설정(2026-09-29): 나머지 응답 처리와 독립 -- 적용 실패는 directory.
        # status.error 로 다음 보고에 실리고 스토리지·프로브 대상 갱신을 막지 않는다.
        # 예외 메시지는 찍지 않는다(블록에 비밀번호가 있다 -- 유형만).
        if "directory" in body:
            try:
                self._directory.apply(body["directory"])
            except Exception as exc:  # noqa: BLE001 -- apply 는 원래 안 던진다; 최후 방어
                print(f"agent directory apply failed: {type(exc).__name__}", file=sys.stderr)
        # k8s 동기화 역할(2026-10-25): 응답에 키가 없으면(옛 서버) 역할 없음 -- 지정되지 않은 것으로 본다.
        if self._k8s_worker is not None:
            self._k8s_worker.configure(body.get("k8s_sync"))
        return new_state


def run_loop(settings: AgentSettings, *, once: bool = False,
            client_factory: Optional[Callable[[], httpx.Client]] = None,
            worker_client_factory: Optional[Callable[[], httpx.Client]] = None) -> None:
    state = {"storages": [], "probe_targets": [],
             "interval": settings.interval_seconds, "artifact_base_path": None}
    if client_factory is None:
        client_factory = lambda: httpx.Client(base_url=settings.api_url, timeout=10.0)
    with client_factory() as client:
        if once:
            runner = AgentRunner(settings, client)
            state = runner.run_once(state)   # 부트스트랩 사이클: 빈 상태로 설정(storages) 수신
            runner.run_once(state)           # 본 사이클: 받은 storages로 실제 프로브
            return
        # 상주 모드에서만 k8s 동기화 워커를 띄운다(자기 HTTP 클라이언트 -- 보고 클라이언트의 10초 타임아웃과 분리).
        worker = K8sSyncWorker(settings, client_factory=worker_client_factory)
        worker.start()
        runner = AgentRunner(settings, client, k8s_worker=worker)
        while True:
            state = runner.run_once(state)
            time.sleep(max(1, int(state["interval"])))
