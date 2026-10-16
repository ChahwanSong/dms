"""노드 CPU 상한(2026-10-16 사용자 결정 「워커가 프로세스 개수만큼 코어를 실제로 요청 -- 옵션이 아니라 항상」).

워커 컨테이너는 resources.requests.cpu = 그 워커의 rank 수를 요청한다(execution_manifests._processes_per_node -- hostfile
slots 와 같은 단일 원천). 그런데 DMS 잡의 노드는 k8s 스케줄러가 아니라 planner 가 에이전트 보고로 골라 N대를 굳히고
(required nodeAffinity + 노드당 워커 1개 -- 고르는 순서는 placement.resolve_fanout), Volcano gang 은 전원이 놓여야 선다 --
한 노드라도 요청을 담지 못하면 잡이 Pending 에 영원히 멈춘다(데이터 잡엔 Pending 상한이 없고, 그 잡의 Inqueue 예약이 뒤
잡의 enqueue 까지 막는다). 그래서 planner(placement)·stepper 가 노드별 상한으로 노드당 프로세스 수를 **줄이고**(클램프),
1코어도 못 담는 노드는 후보에서 뺀다(0대면 node_cpu_insufficient 거부). 제출 뒤 상한이 알려졌는데 PENDING vcjob 의 노드당
프로세스보다 작으면 stepper 가 node_cpu_insufficient_at_step 으로 끊는다(stepper._raise_if_cpu_short).

상한 = floor((allocatable − launcher 요청 − 예비) / 1000) 코어.
- allocatable 은 에이전트가 자기 Node 에서 읽어 보고한다(report.k8s_node.allocatable_cpu_m, agent/probes.probe_k8s_node).
  **정적** allocatable 기준이다 -- 지금 남은 CPU 로 정하면 다른 DMS 잡과의 경합(정상적인 큐잉)에 따라 프로세스 수가 틱마다
  달라진다.
- launcher 몫을 빼는 이유: launcher 는 후보 노드 중 하나에 워커와 함께 앉을 수 있다.
- 예비(DMS_NODE_CPU_RESERVE_MILLICORES, 기본 500)는 DaemonSet·제어면·빌드 파드 몫이다. 컨트롤러·에이전트는 다른 파드의
  requests 를 볼 수 없다(클러스터 전역 pods list 권한이 필요 -- 보안 범위를 넓히는 결정이라 하지 않았다, BACKLOG).

모름(옛 에이전트·재시작 뒤 첫 Node 읽기 전·클러스터 밖) = None = 클램프하지 않는다(fail-open, k8s_node 관례 -- 조회 문제가
전 노드를 배치 불가로 만들면 안 된다). Node 읽기가 한 번 실패한 보고는 모름이 아니다: 에이전트가 마지막으로 읽은 값을
allocatable_stale=True 와 함께 이어 싣는다(agent.runner.carry_node_cpu -- allocatable 은 정적인 값이다). os.cpu_count 로
대신하지 않는다: 호스트 코어 수는 allocatable 보다 커서(테스트베드 2 > 1.8) 바로 그 영구 Pending 을 못 막는다. 이 모듈은
표준 라이브러리만 쓴다(에이전트 이미지도 import 한다).
"""
import re
from decimal import Decimal, InvalidOperation

# launcher 는 rank 를 돌리지 않는다(hostfile 은 워커 IP 만 -- dms_job_runner.runner._wait_workers_ready). mpirun(HNP)·ssh·
# 러너뿐이라 작게 잡는다. 0 이 아닌 이유: BestEffort 로 남으면 Volcano allocate 가 건너뛰고 backfill 이 gang 밖에서 따로
# 놓는다(CPU 회계에서도 빠진다). 매니페스트(execution_manifests)와 상한 계산이 이 상수 하나를 쓴다.
LAUNCHER_CPU_REQUEST_MILLICORES = 100
DEFAULT_NODE_CPU_RESERVE_MILLICORES = 500
# 파싱 결과의 위생 상한(밀리코어 = 10^12 코어). 그 위는 보고 변조·단위 오류라 모름으로 접는다 -- 지수 표기("1e999999")를
# int 로 만들다 메모리를 태우지 않게 Decimal 단계에서 끊는다.
_MAX_MILLICORES = 10 ** 15

# k8s resource.Quantity 문법(<부호 없는 수><접미사>): 10진 SI·2진 SI·지수. CPU 는 실제로 "2"·"1800m"·"1.5" 꼴이다.
_QUANTITY = re.compile(r"^\+?(\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d{1,4})|(Ki|Mi|Gi|Ti|Pi|Ei|n|u|m|k|M|G|T|P|E))?$")
_SUFFIX = {
    None: Decimal(1), "n": Decimal("1e-9"), "u": Decimal("1e-6"), "m": Decimal("1e-3"),
    "k": Decimal("1e3"), "M": Decimal("1e6"), "G": Decimal("1e9"), "T": Decimal("1e12"),
    "P": Decimal("1e15"), "E": Decimal("1e18"),
    "Ki": Decimal(2 ** 10), "Mi": Decimal(2 ** 20), "Gi": Decimal(2 ** 30),
    "Ti": Decimal(2 ** 40), "Pi": Decimal(2 ** 50), "Ei": Decimal(2 ** 60),
}


def parse_cpu_millicores(value) -> "int | None":
    """k8s CPU 수량("1800m", "2", "1.5", "250u", "1e3") -> 정수 밀리코어(**내림** -- 상한 계산은 보수적이어야 한다).
    파싱 못 하면 None(모름). 숫자(int·float 코어)도 받는다(python 클라이언트는 문자열을 주지만 모양을 가정하지 않는다).
    bool·음수·NaN·위생 상한 초과는 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            cores = Decimal(str(value))
        except InvalidOperation:
            return None
    elif isinstance(value, str):
        m = _QUANTITY.match(value.strip())
        if m is None:
            return None
        cores = Decimal(m.group(1))
        if m.group(2) is not None:
            cores = cores.scaleb(int(m.group(2)))
        else:
            cores = cores * _SUFFIX[m.group(3)]
    else:
        return None
    if not cores.is_finite() or cores < 0:
        return None
    milli = cores * 1000
    if milli > _MAX_MILLICORES:
        return None
    return int(milli)        # Decimal → int 는 0 쪽으로 자른다(음수는 위에서 거절) = 내림


def node_cpu_cap(report, reserve_millicores: int) -> "int | None":
    """에이전트 보고 하나 -> 노드당 프로세스 상한(코어, ≥ 0) | None(모름). 보고는 신뢰 경계 밖(DB) -- allocatable_cpu_m 이
    int(bool 제외)·≥ 0 이 아니면 모름이다."""
    node = report.get("k8s_node") if isinstance(report, dict) else None
    alloc = node.get("allocatable_cpu_m") if isinstance(node, dict) else None
    if not isinstance(alloc, int) or isinstance(alloc, bool) or alloc < 0:
        return None
    return max(0, (alloc - LAUNCHER_CPU_REQUEST_MILLICORES - reserve_millicores) // 1000)


def per_node_limit(nodes, caps) -> "int | None":
    """워커가 앉을 노드 목록(nodes -- 같은 노드가 두 번 나오면 그 노드에 워커가 둘이다: nsync 의 source-worker·
    destination-worker 는 서로 다른 task 라 반친화가 갈라 놓지 않는다)과 노드별 상한(caps) -> 노드당 프로세스 상한 |
    None(아는 상한이 하나도 없다 = 클램프 안 함). 슬롯은 전 워커가 같은 값이라(hostfile slots) 가장 작은 상한이 정한다.
    모르는 노드(None)는 제약을 더하지 않는다(fail-open)."""
    counts: dict = {}
    for n in nodes:
        counts[n] = counts.get(n, 0) + 1
    known = [caps.get(n) // k for n, k in counts.items() if caps.get(n) is not None]
    return min(known) if known else None
