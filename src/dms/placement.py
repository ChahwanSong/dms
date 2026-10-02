"""도구 선택 + 후보 노드 산정. 신선한 에이전트 증거(리포트)만으로 판단하는 순수 함수."""

from .domain import PRIORITIES, PRIORITY_CLASS
from .repositories.node_exclusions import NODE_EXCLUDED, NODE_UNSCHEDULABLE, k8s_unschedulable


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
                   require_writable, excluded=frozenset(), honor_k8s=True):
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
                               owner, privileged, excluded_nodes=frozenset()):
    """excluded_nodes: 관리자 배치 제외 노드 이름(node_exclusions). k8s 스케줄 불가는 보고(k8s_node)에서 읽는다.

    0대인데 **막힘을 무시하면 배치가 서는** 경우에만 nodes_excluded 로 말한다 -- 기존 no_eligible_nodes("실행
    가능한 노드가 없습니다")로는 노드가 멀쩡한데 관리자가 막았다는 사실이 화면에 안 보인다(planner 는 거부할 때
    노드별 사유를 버린다). 막힘을 무시해도 안 되면(미마운트·도구 없음이 진짜 원인) 기존 코드 그대로다(리뷰: sync 의
    한쪽 면이 막혔어도 다른 면이 아예 없으면 원인은 막힘이 아니다). 신원 전파 대기 노드가 있으면 기존 코드를 지켜
    planner 의 신원 유예가 걸리게 한다(planner._GRACE_REASONS)."""
    ex = frozenset(excluded_nodes)
    try:
        return _select(operation, fresh_reports, storage_name=storage_name, source_storage=source_storage,
                       destination_storage=destination_storage, owner=owner, privileged=privileged,
                       excluded=ex, honor_k8s=True)
    except PlacementError as exc:
        if exc.reason_code not in ("no_eligible_nodes", "no_ready_sync_candidate") or _identity_pending(exc.rejections):
            raise
        try:
            _select(operation, fresh_reports, storage_name=storage_name, source_storage=source_storage,
                    destination_storage=destination_storage, owner=owner, privileged=privileged,
                    excluded=frozenset(), honor_k8s=False)
        except PlacementError:
            raise exc
        raise PlacementError("nodes_excluded", storage_name or "", rejections=exc.rejections) from exc


def _select(operation, fresh_reports, *, storage_name, source_storage, destination_storage,
            owner, privileged, excluded, honor_k8s):
    ex = excluded
    if operation == "scan":
        nodes, rej = eligible_nodes(fresh_reports, storage_name, tool="dscan",
                                    owner=owner, privileged=privileged,
                                    require_writable=False, excluded=ex, honor_k8s=honor_k8s)
        if not nodes:
            raise PlacementError("no_eligible_nodes", storage_name, rejections=rej)
        return {"tool": "dscan", "candidates": {"primary": nodes}, "rejections": rej}
    if operation == "rm":
        nodes, rej = eligible_nodes(fresh_reports, storage_name, tool="drm",
                                    owner=owner, privileged=privileged,
                                    require_writable=True, excluded=ex, honor_k8s=honor_k8s)
        if not nodes:
            raise PlacementError("no_eligible_nodes", storage_name, rejections=rej)
        return {"tool": "drm", "candidates": {"primary": nodes}, "rejections": rej}
    if operation == "sync":
        src_dsync, rej_s = eligible_nodes(fresh_reports, source_storage, tool="dsync",
                                          owner=owner, privileged=privileged,
                                          require_writable=False, excluded=ex, honor_k8s=honor_k8s)
        dst_dsync, rej_d = eligible_nodes(fresh_reports, destination_storage,
                                          tool="dsync", owner=owner,
                                          privileged=privileged, require_writable=True,
                                          excluded=ex, honor_k8s=honor_k8s)
        colocated = sorted(set(src_dsync) & set(dst_dsync))
        rejections = {"source": rej_s, "destination": rej_d}
        if colocated:
            return {"tool": "dsync", "candidates": {"primary": colocated},
                    "rejections": rejections}
        src_n, rej_ns = eligible_nodes(fresh_reports, source_storage, tool="nsync",
                                       owner=owner, privileged=privileged,
                                       require_writable=False, excluded=ex, honor_k8s=honor_k8s)
        dst_n, rej_nd = eligible_nodes(fresh_reports, destination_storage, tool="nsync",
                                       owner=owner, privileged=privileged,
                                       require_writable=True, excluded=ex, honor_k8s=honor_k8s)
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


def resolve_fanout(policy, candidates, *, priority, requested_node_count=None,
                   requested_procs_per_node=None):
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
    if "primary" in candidates:
        node_count = min(len(candidates["primary"]), max_nodes)
        return {**common, "node_count": node_count,
                "process_count": node_count * per_node}
    source_count = min(len(candidates["source"]), max_nodes)
    destination_count = min(len(candidates["destination"]), max_nodes)
    return {**common, "source_count": source_count,
            "destination_count": destination_count,
            "node_count": source_count + destination_count,
            "process_count": (source_count + destination_count) * per_node}
