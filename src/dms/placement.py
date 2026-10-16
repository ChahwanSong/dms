"""도구 선택 + 후보 노드 산정. 신선한 에이전트 증거(리포트)만으로 판단하는 순수 함수.

노드 CPU 상한(2026-10-16, node_cpu 모듈 docstring): 워커는 노드당 프로세스 수만큼 CPU 코어를 요청한다. 1코어도 못 담는
노드는 후보에서 빠지고(노드별 사유 insufficient_cpu, 0대면 node_cpu_insufficient 거부), resolve_fanout 이 **실제로 고른
노드**(자르기도 여기서 한다 -- 순서는 resolve_fanout docstring)의 상한으로 노드당 프로세스 수를 줄인다."""

from .domain import PRIORITIES, PRIORITY_CLASS
from .execution_manifests import MAX_PROCESSES_PER_WORKER, processes_per_node
from .node_cpu import DEFAULT_NODE_CPU_RESERVE_MILLICORES, node_cpu_cap, per_node_limit
from .repositories.node_exclusions import NODE_EXCLUDED, NODE_UNSCHEDULABLE, candidate_nodes, k8s_unschedulable

# 노드별 탈락 사유(rejections -- 화면 계약 아님, node_exclusions.NODE_EXCLUDED 와 같은 급).
INSUFFICIENT_CPU = "insufficient_cpu"


class PlacementError(Exception):
    def __init__(self, reason_code: str, detail: str = "", *, rejections=None):
        self.reason_code = reason_code
        self.detail = detail
        # 노드별 탈락 사유. 플래너의 신원 전파 유예(설계 §2.3)가 "왜 0대인가"를
        # 여기서 읽는다 -- 이것 없이는 신원 지연과 진짜 결격(미마운트·도구 없음)을
        # 가를 수 없다. shape 는 select_tool_and_candidates 의 rejections 와 같다:
        # scan/rm 은 flat {node: reason}, sync 는 {"source"|"destination": {node: reason}}.
        self.rejections = dict(rejections or {})
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def _mount_for(report, storage_name):
    for mount in report.get("mounts", []):
        if mount.get("storage_name") == storage_name:
            return mount
    return None


def _tool_ready(report, tool):
    return any(t.get("name") == tool and t.get("status") == "Ready"
               for t in report.get("tools", []))


def _identity_ready(report, owner):
    return any(i.get("username") == owner and i.get("status") == "Ready"
               for i in report.get("identities", []))


def eligible_nodes(fresh_reports, storage_name, *, tool, owner, privileged,
                   require_writable, excluded=frozenset(), honor_k8s=True,
                   cpu_reserve_m=DEFAULT_NODE_CPU_RESERVE_MILLICORES, honor_cpu=True):
    ok, reasons = [], {}
    for entry in fresh_reports:
        node = entry["node_name"]
        report = entry.get("report") or {}
        mount = _mount_for(report, storage_name)
        if mount is None or mount.get("status") != "Ready":
            reasons[node] = "missing_target_mount"
            continue
        if require_writable and mount.get("writable") is False:
            reasons[node] = "target_mount_read_only"
            continue
        if not _tool_ready(report, tool):
            reasons[node] = f"missing_tool:{tool}"
            continue
        # 노드 배치 제외(repositories/node_exclusions.py): 도구 검사 **뒤** -- 사유가 "제외만 아니었으면 쓸 수 있었던
        # 노드"를 정확히 뜻한다(거부 사유 nodes_excluded 판정의 근거). 신원 검사 **앞** -- 뒤에 두면 막힌 노드가
        # identity_not_ready_on_node 로 기록돼 planner 의 신원 유예가 쓰지도 않을 노드를 최대 grace 동안 기다린다.
        if node in excluded:
            reasons[node] = NODE_EXCLUDED
            continue
        if honor_k8s and k8s_unschedulable(report):
            reasons[node] = NODE_UNSCHEDULABLE
            continue
        # 노드 CPU 상한(node_cpu): 워커 1개(1코어) + launcher 몫도 못 담는 노드는 그 노드가 낀 gang 이 영원히 서지 않는다.
        # 신원 검사 **앞** -- 막힌 노드와 같은 이유(쓰지도 않을 노드를 신원 유예가 기다리지 않게). 모름(None)은 막지 않는다.
        if honor_cpu:
            cap = node_cpu_cap(report, cpu_reserve_m)
            if cap is not None and cap < 1:
                reasons[node] = INSUFFICIENT_CPU
                continue
        if not privileged and not _identity_ready(report, owner):
            reasons[node] = "identity_not_ready_on_node"
            continue
        ok.append(node)
    return sorted(ok), reasons


_IDENTITY_PENDING = "identity_not_ready_on_node"


def _identity_pending(rejections) -> bool:
    for value in rejections.values():
        if isinstance(value, dict):
            if _IDENTITY_PENDING in value.values():
                return True
        elif value == _IDENTITY_PENDING:
            return True
    return False


def select_tool_and_candidates(operation, fresh_reports, *, storage_name=None,
                               source_storage=None, destination_storage=None,
                               owner, privileged, excluded_nodes=frozenset(),
                               cpu_reserve_m=DEFAULT_NODE_CPU_RESERVE_MILLICORES):
    """excluded_nodes: 관리자 배치 제외 노드 이름(node_exclusions). k8s 스케줄 불가는 보고(k8s_node)에서 읽는다.
    cpu_reserve_m: 노드 CPU 상한의 예비 몫(설정 DMS_NODE_CPU_RESERVE_MILLICORES, node_cpu 모듈 docstring).

    0대인데 **막힘을 무시하면 배치가 서는** 경우에만 nodes_excluded 로 말한다 -- 기존 no_eligible_nodes("실행
    가능한 노드가 없습니다")로는 노드가 멀쩡한데 관리자가 막았다는 사실이 화면에 안 보인다(planner 는 거부할 때
    노드별 사유를 버린다). 막힘을 무시해도 안 되면(미마운트·도구 없음이 진짜 원인) 기존 코드 그대로다(리뷰: sync 의
    한쪽 면이 막혔어도 다른 면이 아예 없으면 원인은 막힘이 아니다). 신원 전파 대기 노드가 있으면 기존 코드를 지켜
    planner 의 신원 유예가 걸리게 한다(planner._GRACE_REASONS). 막힘을 무시해도 안 되고 CPU 상한까지 무시해야 서면
    node_cpu_insufficient 다(2026-10-16 -- 막힘이 먼저인 이유: 관리자가 풀 수 있는 원인을 먼저 말한다).

    반환에 node_cpu_caps({후보 노드: 상한 | None}) -- resolve_fanout 이 고른 노드의 상한으로 노드당 프로세스를 줄인다."""
    ex = frozenset(excluded_nodes)
    common = dict(storage_name=storage_name, source_storage=source_storage,
                  destination_storage=destination_storage, owner=owner, privileged=privileged,
                  cpu_reserve_m=cpu_reserve_m)
    try:
        out = _select(operation, fresh_reports, excluded=ex, honor_k8s=True, honor_cpu=True, **common)
    except PlacementError as exc:
        if exc.reason_code not in ("no_eligible_nodes", "no_ready_sync_candidate") or _identity_pending(exc.rejections):
            raise
        try:
            _select(operation, fresh_reports, excluded=frozenset(), honor_k8s=False, honor_cpu=True, **common)
        except PlacementError:
            pass
        else:
            raise PlacementError("nodes_excluded", storage_name or "", rejections=exc.rejections) from exc
        try:
            _select(operation, fresh_reports, excluded=frozenset(), honor_k8s=False, honor_cpu=False, **common)
        except PlacementError:
            raise exc
        raise PlacementError("node_cpu_insufficient", storage_name or "", rejections=exc.rejections) from exc
    reports = {e["node_name"]: e.get("report") for e in fresh_reports}
    out["node_cpu_caps"] = {n: node_cpu_cap(reports.get(n), cpu_reserve_m)
                            for nodes in out["candidates"].values() for n in nodes}
    return out


def _select(operation, fresh_reports, *, storage_name, source_storage, destination_storage,
            owner, privileged, excluded, honor_k8s, honor_cpu, cpu_reserve_m):
    ex = excluded
    cpu = {"cpu_reserve_m": cpu_reserve_m, "honor_cpu": honor_cpu}
    if operation == "scan":
        nodes, rej = eligible_nodes(fresh_reports, storage_name, tool="dscan",
                                    owner=owner, privileged=privileged,
                                    require_writable=False, excluded=ex, honor_k8s=honor_k8s, **cpu)
        if not nodes:
            raise PlacementError("no_eligible_nodes", storage_name, rejections=rej)
        return {"tool": "dscan", "candidates": {"primary": nodes}, "rejections": rej}
    if operation == "rm":
        nodes, rej = eligible_nodes(fresh_reports, storage_name, tool="drm",
                                    owner=owner, privileged=privileged,
                                    require_writable=True, excluded=ex, honor_k8s=honor_k8s, **cpu)
        if not nodes:
            raise PlacementError("no_eligible_nodes", storage_name, rejections=rej)
        return {"tool": "drm", "candidates": {"primary": nodes}, "rejections": rej}
    if operation == "sync":
        src_dsync, rej_s = eligible_nodes(fresh_reports, source_storage, tool="dsync",
                                          owner=owner, privileged=privileged,
                                          require_writable=False, excluded=ex, honor_k8s=honor_k8s, **cpu)
        dst_dsync, rej_d = eligible_nodes(fresh_reports, destination_storage,
                                          tool="dsync", owner=owner,
                                          privileged=privileged, require_writable=True,
                                          excluded=ex, honor_k8s=honor_k8s, **cpu)
        colocated = sorted(set(src_dsync) & set(dst_dsync))
        rejections = {"source": rej_s, "destination": rej_d}
        if colocated:
            return {"tool": "dsync", "candidates": {"primary": colocated},
                    "rejections": rejections}
        src_n, rej_ns = eligible_nodes(fresh_reports, source_storage, tool="nsync",
                                       owner=owner, privileged=privileged,
                                       require_writable=False, excluded=ex, honor_k8s=honor_k8s, **cpu)
        dst_n, rej_nd = eligible_nodes(fresh_reports, destination_storage, tool="nsync",
                                       owner=owner, privileged=privileged,
                                       require_writable=True, excluded=ex, honor_k8s=honor_k8s, **cpu)
        if src_n and dst_n:
            return {"tool": "nsync",
                    "candidates": {"source": src_n, "destination": dst_n},
                    "rejections": rejections}
        # sync 도 유예 대상이다(설계 §2.3 정정 -- 슬라이스 15에서 실제로 거부된
        # 전이가 no_ready_sync_candidate 였다). 중첩 shape 그대로 싣고, 두 쪽의
        # 합집합으로 판정하는 것은 플래너 몫이다.
        raise PlacementError("no_ready_sync_candidate", rejections=rejections)
    raise PlacementError("invalid_operation", operation)


TOOL_TO_POLICY = {"dscan": "scan", "drm": "rm", "dsync": "dsync", "nsync": "nsync"}


def _clamp_priority(requested, policy_max):
    if PRIORITIES.index(requested) <= PRIORITIES.index(policy_max):
        return requested
    return policy_max


def dms_node_load(worker_pools) -> dict:
    """비종단 잡들의 worker_pool -> {노드: DMS 가 그 노드에 예약한(또는 곧 예약할) 코어 수}. resolve_fanout 의 노드 순서
    입력이다(2026-10-16 리뷰 -- 아래 docstring). 노드당 값 = 그 잡의 procs_per_node(워커 CPU 요청과 같은 값), 키가 없거나
    깨졌으면 process_count // 워커 수(execution_manifests.processes_per_node -- 이 기능 전에 계획된 잡). nsync 의 양면에 같은
    노드가 있으면 워커가 둘이라 두 번 센다(node_cpu.per_node_limit 과 같은 규칙). launcher(100m)는 어느 후보에 앉을지
    몰라 세지 않는다.

    worker_pool 은 DB 신뢰 경계 밖이다 -- 모양이 깨진 행(비 dict·후보 없음·process_count 변조·워커당 1024 초과)은 **조용히
    건너뛴다**: 순서를 정하는 어림값일 뿐이라 한 행의 변조가 계획 전체를 막으면 안 된다(배정 자체의 방어는 stepper·빌더)."""
    load: dict = {}
    for wp in worker_pools:
        if not isinstance(wp, dict):
            continue
        cand = wp.get("candidates")
        names = candidate_nodes(cand)
        if not names:
            continue
        ppn = wp.get("procs_per_node")
        if not isinstance(ppn, int) or isinstance(ppn, bool) or not 1 <= ppn <= MAX_PROCESSES_PER_WORKER:
            try:
                ppn = processes_per_node(wp.get("process_count"), cand)
            except (ValueError, TypeError, AttributeError):
                continue
        for n in names:
            load[n] = load.get(n, 0) + ppn
    return load


def _pick_nodes(nodes, count, *, per_node, caps, load):
    """후보에서 count 대를 고른다(2026-10-16 리뷰). 키 = (상한이 요청 노드당 프로세스를 다 못 담으면 1·담거나 모르면 0,
    남은 여유(상한 − DMS 예약 코어)가 노드당 프로세스를 못 담으면 1·담거나 모르면 0, DMS 예약 코어, 이름) -- 결정적이다
    (같은 DB 상태면 같은 선택). 고른 집합은 이름순으로 돌려준다(worker_pool 표현 그대로).
    - 남은 여유를 따로 본다(재검토 지적): 예약 코어는 절대값이라, 크기가 섞인 클러스터에서 작은 노드(상한 3)가 큰 노드
      (상한 126)와 같은 예약을 가지면 이름순으로 번갈아 뽑혀 작은 노드에 줄을 섰다(그 노드는 워커 하나만 담는다).
    - 상한 미달 노드를 뒤로: 상한은 고른 노드의 최솟값이라(hostfile slots 는 전 워커 같은 값) 작은 노드 하나가 잡 전체의
      rank 수를 끌어내린다(이질 클러스터의 작은 인프라 노드). 담을 노드가 충분하면 그 노드를 쓰지 않는다.
    - 예약이 적은 노드부터: 워커가 CPU 를 실제로 요청하고 required nodeAffinity 로 노드가 굳으므로, 이름순 앞 N대 고정이면
      다른 노드가 놀아도 같은 노드 집합에 줄을 선다(Volcano 는 affinity 밖으로 옮기지 못한다).
    - 모름(None)은 0 쪽 -- fail-open 관례(모르는 노드를 벌하지 않는다)."""
    def key(n):
        cap = caps.get(n)
        used = load.get(n, 0)
        short = 0 if cap is None or cap >= per_node else 1
        full = 0 if cap is None or cap - used >= per_node else 1
        return (short, full, used, n)
    return sorted(sorted(nodes, key=key)[:count])


def resolve_fanout(policy, candidates, *, priority, requested_node_count=None,
                   requested_procs_per_node=None, node_cpu_caps=None, node_load=None):
    """정책·요청 -> 큐·우선순위·노드 수·프로세스 수 + **실제로 쓸 노드**(candidates).

    자르기를 여기서 하는 이유(2026-10-16): 노드당 프로세스의 CPU 상한은 실제로 고른 노드들의 최솟값이어야 한다(슬롯은
    전 워커가 같은 값 -- hostfile slots). planner 가 따로 자르면 상한을 본 노드 집합과 굳힌 노드 집합이 갈라질 수 있다.
    고르는 순서는 _pick_nodes(상한 충족 → 남은 여유 충족 → DMS 예약 코어 적은 순 → 이름) -- 예전의 이름순 앞 N대 고정은 워커 CPU 요청과
    겹쳐 노드가 놀아도 같은 노드에 줄을 세웠다(2026-10-16 리뷰). nsync 는 면마다 따로 고른다.
    node_cpu_caps: {노드: 상한 | None}(select_tool_and_candidates). None·키 부재 = 모름 = 클램프 안 함(fail-open) --
    worker_pool 에 cpu_cap_known=False 로 남는다. node_load: {노드: DMS 예약 코어}(dms_node_load) -- None·키 부재 = 0.
    반환의 procs_per_node(실제)·procs_per_node_requested(정책·요청 min, 클램프 전)·node_cpu_caps(고른 노드만)는
    worker_pool 에 그대로 실린다(화면 jobTool.toolSummary)."""
    if policy is None:
        raise PlacementError("missing_policy")
    if not policy.get("enabled"):
        raise PlacementError("policy_disabled")
    max_nodes = policy["max_nodes"]
    # 요청은 정책을 **줄일 수만** 있다(min) — payload 가 DB 변조로 부풀려져도 정책
    # max_nodes 를 초과할 수 없다. sync 는 max_nodes 가 면당 상한이므로 요청값도
    # 같은 의미 자리에서 면당 캡된다. None(미지정)은 정책 그대로 — null≠0.
    if requested_node_count is not None:
        max_nodes = min(max_nodes, requested_node_count)
    per_node = policy["procs_per_node"]
    # procs_per_node 도 같은 min-캡 — per_node 는 단면(primary)·양면(sync) 공용
    # 자리라 process_count 산식 양쪽에 그대로 반영된다. None(미지정)은 정책값.
    if requested_procs_per_node is not None:
        per_node = min(per_node, requested_procs_per_node)
    clamped = _clamp_priority(priority, policy["max_priority"])
    common = {"queue": policy["queue"], "priority_class": PRIORITY_CLASS[clamped]}
    all_caps = node_cpu_caps or {}
    pick = {"per_node": per_node, "caps": all_caps, "load": node_load or {}}
    if "primary" in candidates:
        node_count = min(len(candidates["primary"]), max_nodes)
        chosen = {"primary": _pick_nodes(candidates["primary"], node_count, **pick)}
        counts = {"node_count": node_count}
    else:
        source_count = min(len(candidates["source"]), max_nodes)
        destination_count = min(len(candidates["destination"]), max_nodes)
        chosen = {"source": _pick_nodes(candidates["source"], source_count, **pick),
                  "destination": _pick_nodes(candidates["destination"], destination_count, **pick)}
        counts = {"source_count": source_count, "destination_count": destination_count,
                  "node_count": source_count + destination_count}
    # 상한을 보는 집합 == 굳히는 집합(chosen) -- 이 아래는 chosen 만 본다.
    workers = [n for nodes in chosen.values() for n in nodes]
    caps = {n: all_caps.get(n) for n in workers}
    requested_per_node = per_node
    limit = per_node_limit(workers, caps)
    if limit is not None:
        # eligible_nodes 가 상한 < 1 노드를 이미 뺐으니 여기 0 은 한 노드에 워커가 둘(nsync 양면)인데 1코어뿐인 경우다.
        if limit < 1:
            raise PlacementError("node_cpu_insufficient")
        per_node = min(per_node, limit)
    return {**common, **counts, "candidates": chosen,
            "process_count": counts["node_count"] * per_node,
            "procs_per_node": per_node, "procs_per_node_requested": requested_per_node,
            "node_cpu_caps": caps, "cpu_cap_known": all(c is not None for c in caps.values())}
