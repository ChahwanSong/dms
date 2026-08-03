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
