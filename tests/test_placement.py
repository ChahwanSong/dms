import pytest
from dms.placement import PlacementError, eligible_nodes, select_tool_and_candidates


def _report(node, *, mounts, tools=("dscan", "dsync", "nsync", "drm"),
            identities=("alice",)):
    return {"node_name": node,
            "report": {
                "mounts": mounts,
                "tools": [{"name": t, "status": "Ready"} for t in tools],
                "identities": [{"username": u, "status": "Ready"} for u in identities]}}


def _mount(name, *, status="Ready", writable=True):
    return {"storage_name": name, "mount_path": f"/mnt/{name}",
            "status": status, "writable": writable}


def test_eligible_nodes_filters_and_reasons():
    reports = [
        _report("n1", mounts=[_mount("s1")]),
        _report("n2", mounts=[_mount("s1", status="Missing")]),
        _report("n3", mounts=[_mount("s1")], tools=("dsync",)),      # dscan 없음
        _report("n4", mounts=[_mount("s1")], identities=("bob",)),   # alice identity 없음
    ]
    ok, reasons = eligible_nodes(reports, "s1", tool="dscan", owner="alice",
                                 privileged=False, require_writable=False)
    assert ok == ["n1"]
    assert reasons["n2"] == "missing_target_mount"
    assert reasons["n3"] == "missing_tool:dscan"
    assert reasons["n4"] == "identity_not_ready_on_node"


def test_require_writable_rejects_ro_mount():
    reports = [_report("n1", mounts=[_mount("s1", writable=False)])]
    ok, reasons = eligible_nodes(reports, "s1", tool="drm", owner="alice",
                                 privileged=False, require_writable=True)
    assert ok == [] and reasons["n1"] == "target_mount_read_only"


def test_privileged_skips_identity_check():
    reports = [_report("n1", mounts=[_mount("s1")], identities=())]
    ok, _ = eligible_nodes(reports, "s1", tool="dscan", owner="root",
                           privileged=True, require_writable=False)
    assert ok == ["n1"]


def test_select_scan_and_rm():
    reports = [_report("n1", mounts=[_mount("s1")])]
    scan = select_tool_and_candidates("scan", reports, storage_name="s1",
                                      owner="alice", privileged=False)
    assert scan["tool"] == "dscan" and scan["candidates"]["primary"] == ["n1"]
    rm = select_tool_and_candidates("rm", reports, storage_name="s1",
                                    owner="alice", privileged=False)
    assert rm["tool"] == "drm"


def test_select_sync_dsync_when_colocated():
    reports = [_report("n1", mounts=[_mount("src"), _mount("dst")])]
    out = select_tool_and_candidates("sync", reports, source_storage="src",
                                     destination_storage="dst", owner="alice",
                                     privileged=False)
    assert out["tool"] == "dsync" and out["candidates"]["primary"] == ["n1"]


def test_select_sync_nsync_when_disjoint():
    reports = [
        _report("n1", mounts=[_mount("src")]),
        _report("n2", mounts=[_mount("dst")]),
    ]
    out = select_tool_and_candidates("sync", reports, source_storage="src",
                                     destination_storage="dst", owner="alice",
                                     privileged=False)
    assert out["tool"] == "nsync"
    assert out["candidates"]["source"] == ["n1"]
    assert out["candidates"]["destination"] == ["n2"]


def test_no_candidates_raise():
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates("scan", [], storage_name="s1", owner="alice",
                                   privileged=False)
    assert e.value.reason_code == "no_eligible_nodes"
    reports = [_report("n1", mounts=[_mount("src")])]  # dst 없음
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates("sync", reports, source_storage="src",
                                   destination_storage="dst", owner="alice",
                                   privileged=False)
    assert e.value.reason_code == "no_ready_sync_candidate"


def test_no_eligible_nodes_error_carries_rejections():
    # 플래너 유예(설계 §2.3)가 "신원 대기 노드가 있는가"를 예외에서 직접 읽는다 --
    # rejections 가 안 실리면 신원 지연과 진짜 결격(미마운트)을 가를 수 없다.
    reports = [_report("n1", mounts=[_mount("s1")], identities=("bob",))]
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates("scan", reports, storage_name="s1",
                                   owner="alice", privileged=False)
    assert e.value.reason_code == "no_eligible_nodes"
    assert e.value.rejections == {"n1": "identity_not_ready_on_node"}


def test_sync_no_candidate_error_carries_nested_rejections():
    # sync 도 유예 대상이다(설계 §2.3 정정 -- 슬라이스 15 실패에 sync 가 포함됐다).
    # rejections 는 {"source": {...}, "destination": {...}} 중첩이고, 플래너는 두 쪽을
    # 합집합으로 본다. 그러니 양쪽 사유가 모두 실려야 한다 -- 한쪽만 실으면 반대쪽에만
    # 신원 대기 노드가 있는 형상에서 유예가 증발한다.
    reports = [_report("n1", mounts=[_mount("src")], identities=("bob",)),
               _report("n2", mounts=[_mount("dst")], identities=("bob",))]
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates("sync", reports, source_storage="src",
                                   destination_storage="dst", owner="alice",
                                   privileged=False)
    assert e.value.reason_code == "no_ready_sync_candidate"
    assert e.value.rejections == {
        "source": {"n1": "identity_not_ready_on_node", "n2": "missing_target_mount"},
        "destination": {"n1": "missing_target_mount",
                        "n2": "identity_not_ready_on_node"}}


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


def test_missing_and_disabled_policy():
    with pytest.raises(PlacementError) as e:
        resolve_fanout(None, {"primary": ["n1"]}, priority="mid")
    assert e.value.reason_code == "missing_policy"
    with pytest.raises(PlacementError) as e:
        resolve_fanout({**POLICY, "enabled": 0}, {"primary": ["n1"]}, priority="mid")
    assert e.value.reason_code == "policy_disabled"
