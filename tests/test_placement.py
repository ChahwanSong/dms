import pytest
from dms.placement import PlacementError, eligible_nodes, select_tool_and_candidates


def _report(node, *, mounts, tools=("dscan", "dsync", "nsync", "drm")):
    return {"node_name": node,
            "report": {
                "mounts": mounts,
                "tools": [{"name": t, "status": "Ready"} for t in tools]}}


def _mount(name, *, status="Ready", writable=True):
    return {"storage_name": name, "mount_path": f"/mnt/{name}",
            "status": status, "writable": writable}


def test_eligible_nodes_filters_and_reasons():
    reports = [
        _report("n1", mounts=[_mount("s1")]),
        _report("n2", mounts=[_mount("s1", status="Missing")]),
        _report("n3", mounts=[_mount("s1")], tools=("dsync",)),      # dscan 없음
    ]
    ok, reasons = eligible_nodes(reports, "s1", tool="dscan", require_writable=False)
    assert ok == ["n1"]
    assert reasons == {"n2": "missing_target_mount", "n3": "missing_tool:dscan"}


def test_require_writable_rejects_ro_mount():
    reports = [_report("n1", mounts=[_mount("s1", writable=False)])]
    ok, reasons = eligible_nodes(reports, "s1", tool="drm", require_writable=True)
    assert ok == [] and reasons["n1"] == "target_mount_read_only"


def test_node_identity_is_not_checked():
    # 모든 잡이 root 라(dms-k8s, 2026-10-26) 노드에 실행 신원이 전파됐는지 보지 않는다 -- 옛 에이전트의 identities 가
    # 비어 있어도 적격이다(예전 identity_not_ready_on_node 사유는 없다).
    report = _report("n1", mounts=[_mount("s1")])
    report["report"]["identities"] = []
    ok, reasons = eligible_nodes([report], "s1", tool="dscan", require_writable=False)
    assert ok == ["n1"] and reasons == {}


def test_select_scan_and_rm():
    reports = [_report("n1", mounts=[_mount("s1")])]
    scan = select_tool_and_candidates("scan", reports, storage_name="s1")
    assert scan["tool"] == "dscan" and scan["candidates"]["primary"] == ["n1"]
    rm = select_tool_and_candidates("rm", reports, storage_name="s1")
    assert rm["tool"] == "drm"


def test_select_sync_dsync_when_colocated():
    reports = [_report("n1", mounts=[_mount("src"), _mount("dst")])]
    out = select_tool_and_candidates("sync", reports, source_storage="src",
                                     destination_storage="dst")
    assert out["tool"] == "dsync" and out["candidates"]["primary"] == ["n1"]


def test_select_sync_nsync_when_disjoint():
    reports = [
        _report("n1", mounts=[_mount("src")]),
        _report("n2", mounts=[_mount("dst")]),
    ]
    out = select_tool_and_candidates("sync", reports, source_storage="src",
                                     destination_storage="dst")
    assert out["tool"] == "nsync"
    assert out["candidates"]["source"] == ["n1"]
    assert out["candidates"]["destination"] == ["n2"]


def test_no_candidates_raise():
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates("scan", [], storage_name="s1")
    assert e.value.reason_code == "no_eligible_nodes"
    reports = [_report("n1", mounts=[_mount("src")])]  # dst 없음
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates("sync", reports, source_storage="src",
                                   destination_storage="dst")
    assert e.value.reason_code == "no_ready_sync_candidate"


def test_no_eligible_nodes_error_carries_rejections():
    # 노드별 탈락 사유는 예외에 실린다(nodes_excluded·node_cpu_insufficient 판정이 읽는다).
    reports = [_report("n1", mounts=[_mount("s1")], tools=("dsync",))]
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates("scan", reports, storage_name="s1")
    assert e.value.reason_code == "no_eligible_nodes"
    assert e.value.rejections == {"n1": "missing_tool:dscan"}


def test_sync_no_candidate_error_carries_nested_rejections():
    # sync 의 rejections 는 {"source": {...}, "destination": {...}} 중첩 -- 양쪽 사유가 모두 실려야 한다.
    reports = [_report("n1", mounts=[_mount("src")], tools=("dscan",)),
               _report("n2", mounts=[_mount("dst")], tools=("dscan",))]
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates("sync", reports, source_storage="src",
                                   destination_storage="dst")
    assert e.value.reason_code == "no_ready_sync_candidate"
    assert e.value.rejections == {
        "source": {"n1": "missing_tool:dsync", "n2": "missing_target_mount"},
        "destination": {"n1": "missing_target_mount", "n2": "missing_tool:dsync"}}


# Task 5: 정책 fan-out 산정
from dms.placement import TOOL_TO_POLICY, resolve_fanout

POLICY = {"max_nodes": 3, "procs_per_node": 8, "queue": "dms-data",
          "default_priority": "mid", "max_priority": "high",
          "execution_timeout_seconds": 3600, "enabled": 1}


def test_tool_to_policy_map():
    assert TOOL_TO_POLICY == {"dscan": "scan", "drm": "rm",
                              "dsync": "dsync", "nsync": "nsync"}


def test_fanout_primary_clamps_to_max():
    out = resolve_fanout(POLICY, {"primary": ["n1", "n2", "n3", "n4", "n5"]},
                         priority="mid")
    assert out["node_count"] == 3 and out["process_count"] == 24
    assert out["queue"] == "dms-data" and out["priority_class"] == "dms-mid"


def test_fanout_uses_all_when_below_max():
    out = resolve_fanout(POLICY, {"primary": ["n1"]}, priority="mid")
    assert out["node_count"] == 1 and out["process_count"] == 8


def test_fanout_nsync_roles():
    out = resolve_fanout(POLICY, {"source": ["n1", "n2", "n3", "n4"],
                                  "destination": ["n5", "n6"]}, priority="low")
    assert out["source_count"] == 3 and out["destination_count"] == 2
    assert out["node_count"] == 5 and out["process_count"] == 40
    assert out["priority_class"] == "dms-low"


def test_priority_clamped_to_policy_max():
    capped = {**POLICY, "max_priority": "mid"}
    out = resolve_fanout(capped, {"primary": ["n1"]}, priority="high")
    assert out["priority_class"] == "dms-mid"


# --- 슬라이스 32: requested_node_count min-캡 ---

def test_fanout_requested_node_count_caps_below_policy():
    out = resolve_fanout(POLICY, {"primary": ["n1", "n2", "n3", "n4", "n5"]},
                         priority="mid", requested_node_count=2)
    assert out["node_count"] == 2 and out["process_count"] == 16


def test_fanout_requested_above_policy_policy_wins():
    # 요청은 정책을 줄일 수만 있다 — DB 변조로도 정책 초과 불가
    out = resolve_fanout(POLICY, {"primary": ["n1", "n2", "n3", "n4", "n5"]},
                         priority="mid", requested_node_count=10)
    assert out["node_count"] == 3 and out["process_count"] == 24


def test_fanout_requested_none_keeps_policy_behavior():
    out = resolve_fanout(POLICY, {"primary": ["n1", "n2", "n3", "n4", "n5"]},
                         priority="mid", requested_node_count=None)
    assert out["node_count"] == 3 and out["process_count"] == 24


def test_fanout_requested_caps_sync_per_side():
    # sync 는 max_nodes 가 면당 상한 — 요청값도 면당 min-캡(의미 자리 동일)
    out = resolve_fanout(POLICY, {"source": ["n1", "n2", "n3", "n4"],
                                  "destination": ["n5", "n6"]},
                         priority="low", requested_node_count=1)
    assert out["source_count"] == 1 and out["destination_count"] == 1
    assert out["node_count"] == 2 and out["process_count"] == 16


# --- requested_procs_per_node min-캡: requested_node_count 와 같은 규칙의 미러 ---

def test_fanout_requested_procs_per_node_caps_below_policy():
    out = resolve_fanout(POLICY, {"primary": ["n1", "n2", "n3", "n4", "n5"]},
                         priority="mid", requested_procs_per_node=4)
    assert out["node_count"] == 3 and out["process_count"] == 12


def test_fanout_requested_procs_above_policy_policy_wins():
    # 요청은 정책을 줄일 수만 있다 — DB 변조로도 정책 초과 불가
    out = resolve_fanout(POLICY, {"primary": ["n1", "n2", "n3", "n4", "n5"]},
                         priority="mid", requested_procs_per_node=16)
    assert out["node_count"] == 3 and out["process_count"] == 24


def test_fanout_requested_procs_none_keeps_policy_behavior():
    out = resolve_fanout(POLICY, {"primary": ["n1", "n2", "n3", "n4", "n5"]},
                         priority="mid", requested_procs_per_node=None)
    assert out["node_count"] == 3 and out["process_count"] == 24


def test_fanout_requested_procs_caps_sync_both_sides():
    # sync 도 per_node 는 한 자리(양면 공용) — process_count 산식에 그대로 반영
    out = resolve_fanout(POLICY, {"source": ["n1", "n2", "n3", "n4"],
                                  "destination": ["n5", "n6"]},
                         priority="low", requested_procs_per_node=2)
    assert out["source_count"] == 3 and out["destination_count"] == 2
    assert out["node_count"] == 5 and out["process_count"] == 10


def test_missing_and_disabled_policy():
    with pytest.raises(PlacementError) as e:
        resolve_fanout(None, {"primary": ["n1"]}, priority="mid")
    assert e.value.reason_code == "missing_policy"
    with pytest.raises(PlacementError) as e:
        resolve_fanout({**POLICY, "enabled": 0}, {"primary": ["n1"]}, priority="mid")
    assert e.value.reason_code == "policy_disabled"
