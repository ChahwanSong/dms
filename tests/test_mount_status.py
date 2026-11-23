"""마운트 현황 공용 코어(mount_status, 2026-11-23) -- 칸 판정·공용 디렉터리 판정·집계·reconciler 와의 숫자 일치·이식 경계.

규칙 원문은 src/dms/mount_status.py 모듈 docstring. 리포트는 스키마 검증 없이 저장되므로(DB 가 신뢰 경계) 쓰레기 모양에서도
예외 없이 pending·null 로 내려앉는지를 같이 본다."""
import ast
from pathlib import Path

import pytest

from dms import mount_status
from dms.mount_status import CELL_STATES, SHARED_STATES, build, classify_cell, classify_shared
from dms.reconciler import reconcile_storages_once
from dms.repositories import Repositories

CEPH = {"storage_name": "ceph", "mount_path": "/cephfs", "enabled": 1, "user_enabled": 1, "backend_type": "cephfs"}
SEC = {"storage_name": "sec", "mount_path": "/sec", "enabled": 1, "user_enabled": 0, "backend_type": "cephfs"}
OFF = {"storage_name": "off", "mount_path": "/off", "enabled": 0, "user_enabled": 0, "backend_type": "nfs"}
BASE = "/dms_shared/dms/artifacts"


def _m(name="ceph", path="/cephfs", status="Ready", reason=None, **kw):
    return {"storage_name": name, "mount_path": path, "status": status, "reason": reason,
            "exists": status == "Ready" or reason not in ("missing_mount_path", "host_root_missing"),
            "is_mountpoint": status == "Ready", "readable": status == "Ready", "writable": status == "Ready", **kw}


def _ab(mount_point="/dms_shared", fstype="nfs4", path=BASE, exists=True, writable=True):
    return {"path": path, "exists": exists, "writable": writable, "mount_point": mount_point, "fstype": fstype}


def _node(name, mounts=(), *, fresh=True, ab=None, **extra):
    report = {"node_name": name, "mounts": list(mounts), "probe_mode": "host_root"}
    if ab is not None:
        report["artifact_base"] = ab
    report.update(extra)
    return {"node_name": name, "reported_at": "2026-11-23T00:30:00Z", "fresh": fresh, "report": report}


def _build(nodes, storages=(CEPH, SEC), **kw):
    args = dict(exclusions={}, artifact_base_path=BASE, state_root="/dms_shared/dms", build_node=None, node_list=None)
    args.update(kw)
    return build(list(nodes), list(storages), **args)


# --- classify_cell -------------------------------------------------------------------------------------------------

def test_missing_entry_is_pending_not_reported():
    cell = classify_cell({"mounts": []}, CEPH)
    assert (cell["state"], cell["pending"], cell["status"], cell["disk"]) == ("pending", "not_reported", None, None)


def test_path_mismatch_is_pending_path_changed_and_keeps_the_reported_path():
    cell = classify_cell({"mounts": [_m(path="/old-ceph")]}, CEPH)
    assert (cell["state"], cell["pending"], cell["reported_path"]) == ("pending", "path_changed", "/old-ceph")


def test_two_entries_for_one_storage_prefer_the_current_path():
    report = {"mounts": [_m(path="/old", status="Missing", reason="missing_mount_path"), _m(path="/cephfs")]}
    cell = classify_cell(report, CEPH)
    assert (cell["state"], cell["reported_path"]) == ("ready", "/cephfs")


def test_ready_read_only_and_writable_unknown():
    assert classify_cell({"mounts": [_m(writable=False)]}, CEPH)["state"] == "read_only"
    entry = _m()
    del entry["writable"]                   # 옛 에이전트 -- placement 처럼 통과(ready), 값은 모름(None)
    cell = classify_cell({"mounts": [entry]}, CEPH)
    assert (cell["state"], cell["writable"]) == ("ready", None)


def test_missing_mount_path_is_absent():
    cell = classify_cell({"mounts": [_m(status="Missing", reason="missing_mount_path")]}, CEPH)
    assert (cell["state"], cell["status"], cell["reason"]) == ("absent", "Missing", "missing_mount_path")


@pytest.mark.parametrize("status,reason", [
    ("Missing", "not_a_mountpoint"), ("Missing", "propagation_stale"), ("Missing", "not_readable"),
    ("Missing", "host_root_missing"), ("Missing", "some_future_reason"), ("Missing", None), ("Error", "not mounted"),
])
def test_other_failures_are_broken_with_the_raw_codes(status, reason):
    cell = classify_cell({"mounts": [_m(status=status, reason=reason)]}, CEPH)
    assert (cell["state"], cell["status"], cell["reason"]) == ("broken", status, reason)


@pytest.mark.parametrize("report", [
    None, {}, {"mounts": {}}, {"mounts": "x"}, {"mounts": [1, "a", None]}, {"mounts": [{"storage_name": 5}]},
    "not-a-dict",
])
def test_garbage_reports_never_raise(report):
    assert classify_cell(report, CEPH)["state"] == "pending"
    assert classify_shared(report, BASE)["state"] == "pending"


def test_disk_only_on_mounted_cells():
    disks = {"disks": [{"storage_name": "ceph", "total_bytes": 1000, "used_bytes": 410}]}
    assert classify_cell({"mounts": [_m()], "os": disks}, CEPH)["disk"] == {"total_bytes": 1000, "used_bytes": 410}
    for entry in (_m(status="Missing", reason="not_a_mountpoint"), _m(status="Missing", reason="missing_mount_path"),
                  _m(path="/old")):
        assert classify_cell({"mounts": [entry], "os": disks}, CEPH)["disk"] is None
    bad = {"disks": [{"storage_name": "ceph", "total_bytes": True, "used_bytes": 1}]}
    assert classify_cell({"mounts": [_m()], "os": bad}, CEPH)["disk"] is None
    assert classify_cell({"mounts": [_m()], "os": {"disks": "x"}}, CEPH)["disk"] is None


def test_non_bool_values_read_as_unknown():
    entry = {"storage_name": "ceph", "mount_path": "/cephfs", "status": "Ready", "exists": "yes", "is_mountpoint": 1,
             "readable": None, "writable": "no", "reason": 7}
    cell = classify_cell({"mounts": [entry]}, CEPH)
    assert cell["state"] == "ready"
    assert (cell["exists"], cell["is_mountpoint"], cell["readable"], cell["writable"], cell["reason"]) == (
        None, None, None, None, None)


def test_cell_shape_is_fixed():
    keys = {"state", "last", "pending", "status", "reason", "reported_path", "exists", "is_mountpoint", "readable",
            "writable", "disk"}
    assert set(classify_cell({}, CEPH)) == keys
    assert set(classify_cell({"mounts": [_m()]}, CEPH)) == keys


# --- classify_shared -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ab,state", [
    (None, "pending"),
    ("x", "pending"),
    (_ab(path="/old/dms/artifacts"), "pending"),
    (_ab(exists=False), "missing"),
    ({**_ab(), "exists": "yes"}, "pending"),
    (_ab(mount_point="/"), "local"),
    (_ab(writable=False), "read_only"),
    ({"path": BASE, "exists": True, "writable": True}, "ok_unverified"),     # 옛 에이전트(키 없음)
    (_ab(), "ok"),
])
def test_shared_states(ab, state):
    report = {} if ab is None else {"artifact_base": ab}
    assert classify_shared(report, BASE)["state"] == state


def test_shared_pending_on_path_mismatch_keeps_reported_path_but_no_values():
    out = classify_shared({"artifact_base": _ab(path="/old/dms/artifacts")}, BASE)
    assert out["reported_path"] == "/old/dms/artifacts"
    assert (out["exists"], out["writable"], out["mount_point"]) == (None, None, None)


def test_no_base_means_no_shared_column():
    body = _build([_node("n1", [_m()], ab=_ab())], artifact_base_path=None)
    assert body["shared_dir"] is None and body["nodes"][0]["shared_dir"] is None


# --- build: stale·비활성·differs·메타·totals -------------------------------------------------------------------------

def test_stale_node_turns_every_cell_stale_and_keeps_the_last_value():
    disks = {"disks": [{"storage_name": "ceph", "total_bytes": 10, "used_bytes": 1}]}
    body = _build([_node("n1", [_m(), _m("sec", "/sec", "Missing", "not_a_mountpoint")], fresh=False, ab=_ab(),
                         os=disks)])
    row = body["nodes"][0]
    assert row["cells"]["ceph"]["state"] == "stale" and row["cells"]["ceph"]["last"] == "ready"
    assert row["cells"]["ceph"]["disk"] is None
    assert row["cells"]["sec"]["last"] == "broken"
    assert (row["shared_dir"]["state"], row["shared_dir"]["last"]) == ("stale", "ok")
    assert row["problem"] is True
    assert body["totals"]["stale_nodes"] == 1 and body["totals"]["broken"] == 0     # stale 칸은 집계 밖


def test_disabled_storage_is_not_a_column():
    body = _build([_node("n1", [_m()])], storages=(CEPH, OFF))
    assert [c["storage_name"] for c in body["storages"]] == ["ceph"]
    assert body["disabled_storages"] == ["off"]
    assert set(body["nodes"][0]["cells"]) == {"ceph"}


def test_differs_needs_a_strict_majority_of_fresh_ok_rows():
    nodes = [_node("a", ab=_ab()), _node("b", ab=_ab()), _node("c", ab=_ab("/data", "xfs"))]
    body = _build(nodes)
    assert [r["shared_dir"]["state"] for r in body["nodes"]] == ["ok", "ok", "differs"]
    assert body["shared_dir"]["majority"] == {"mount_point": "/dms_shared", "fstype": "nfs4", "nodes": 2}
    assert body["nodes"][2]["problem"] is True
    # 둘이 서로 다르면 과반이 없다 -- 누가 틀렸는지 단정하지 않는다
    body = _build([_node("a", ab=_ab()), _node("b", ab=_ab("/data", "xfs"))])
    assert [r["shared_dir"]["state"] for r in body["nodes"]] == ["ok", "ok"]
    assert body["shared_dir"]["majority"] is None
    # stale·ok_unverified 는 과반 계산 밖(판정 재료가 아니다) -- 남은 ok 하나는 과반 대상 2개 미만
    legacy = {"path": BASE, "exists": True, "writable": True}
    body = _build([_node("a", ab=_ab()), _node("b", ab=_ab("/data", "xfs"), fresh=False), _node("c", ab=legacy)])
    assert [r["shared_dir"]["state"] for r in body["nodes"]] == ["ok", "stale", "ok_unverified"]
    assert body["shared_dir"]["majority"] is None


def test_row_meta():
    excl = {"node_name": "b", "reason": "disk", "created_by": "ops", "created_at": "2026-11-23T00:00:00Z"}
    k8s = {"schedulable": False, "reason": "cordoned", "transient": False}
    nodes = [_node("a", [_m()], k8s_node=k8s), _node("b", [_m()], k8s_node="bad")]
    body = _build(nodes, exclusions={"b": excl}, build_node="a")
    a, b = body["nodes"]
    assert a["roles"] == ["build"] and b["roles"] == []
    assert a["k8s_node"] == k8s and b["k8s_node"] is None
    assert a["exclusion"] is None and b["exclusion"] == excl
    assert a["probe_mode"] == "host_root"
    assert (a["control_plane"], a["in_cluster"]) == (None, None)
    assert body["cluster"] == {"known": False, "agentless_nodes": []}
    node_list = [{"name": "a", "control_plane": False}, {"name": "cp1", "control_plane": True},
                 {"name": "w9", "control_plane": False}]
    body = _build(nodes, node_list=node_list)
    a, b = body["nodes"]
    assert (a["control_plane"], a["in_cluster"]) == (False, True)
    assert (b["control_plane"], b["in_cluster"]) == (None, False)          # k8s 에 없다(퇴역한 노드의 옛 보고일 수 있다)
    assert body["cluster"] == {"known": True, "agentless_nodes": [
        {"node_name": "cp1", "control_plane": True}, {"node_name": "w9", "control_plane": False}]}


def test_problem_and_totals():
    legacy = {"path": BASE, "exists": True, "writable": True}
    nodes = [
        # absent·pending·ok_unverified 만 -- 문제 아님
        _node("calm", [_m(), _m("sec", "/sec", "Missing", "missing_mount_path")], ab=legacy),
        _node("pend", [_m()], ab=_ab()),                                                # sec 항목 없음 = pending
        _node("ro", [_m(writable=False), _m("sec", "/sec")], ab=_ab()),
        _node("brk", [_m(), _m("sec", "/sec", "Missing", "propagation_stale")], ab=_ab()),
        _node("loc", [_m(), _m("sec", "/sec")], ab=_ab(mount_point="/")),
        _node("miss", [_m(), _m("sec", "/sec")], ab=_ab(exists=False)),
        _node("old", [_m(), _m("sec", "/sec")], fresh=False, ab=_ab()),
    ]
    body = _build(nodes)
    problem = {r["node_name"]: r["problem"] for r in body["nodes"]}
    assert problem == {"calm": False, "pend": False, "ro": True, "brk": True, "loc": True, "miss": True, "old": True}
    count = {r["node_name"]: r["problem_count"] for r in body["nodes"]}
    assert count == {"calm": 0, "pend": 0, "ro": 1, "brk": 1, "loc": 1, "miss": 1, "old": None}   # 지연 = 모름(≠ 0)
    t = body["totals"]
    assert (t["nodes"], t["fresh_nodes"], t["stale_nodes"]) == (7, 6, 1)
    assert (t["ready"], t["read_only"], t["absent"], t["broken"], t["pending"]) == (8, 1, 1, 1, 1)
    assert (t["shared_ok"], t["shared_unverified"], t["shared_problem"], t["shared_pending"]) == (4, 1, 2, 0)
    assert t["problem_nodes"] == 5
    ceph = next(c for c in body["storages"] if c["storage_name"] == "ceph")["summary"]
    assert (ceph["ready"], ceph["total"], ceph["status"], ceph["read_only"]) == (6, 6, "Ready", 1)
    sec = next(c for c in body["storages"] if c["storage_name"] == "sec")["summary"]
    assert (sec["ready"], sec["total"], sec["status"], sec["detail"]) == (3, 5, "Degraded", "ready_nodes=3/5")
    assert (sec["broken"], sec["absent"], sec["pending"]) == (1, 1, 1)


def test_read_only_counts_as_a_problem_only_beside_a_writable_node():
    """이관 소스처럼 모든 노드에 ro 로 붙인 스토리지는 의도된 구성 -- 문제 노드로 세지 않는다(필터가 전체와 같아지지 않게).
    같은 열에 쓰기 가능(ready)인 신선한 노드가 있으면 ro 노드는 바깥값이라 센다. 지연 노드의 ready 는 근거가 아니다."""
    ro = _m(writable=False)
    body = _build([_node("a", [ro, _m("sec", "/sec")], ab=_ab()), _node("b", [ro, _m("sec", "/sec")], ab=_ab())])
    assert [(r["problem"], r["problem_count"]) for r in body["nodes"]] == [(False, 0), (False, 0)]
    assert body["totals"]["read_only"] == 2 and body["totals"]["problem_nodes"] == 0       # 칸·집계의 읽기 전용 수는 그대로
    # ro 이고 경로 없는 노드가 섞여도(소스 노드만 붙인 구성) 문제 아님
    body = _build([_node("a", [ro], ab=_ab()), _node("b", [_m(status="Missing", reason="missing_mount_path")], ab=_ab())],
                  storages=(CEPH,))
    assert [r["problem"] for r in body["nodes"]] == [False, False]
    # 쓰기 가능 노드 옆의 ro 는 바깥값 -- broken·공용 문제와 함께 센다
    body = _build([_node("a", [_m(), _m("sec", "/sec")], ab=_ab()),
                   _node("b", [ro, _m("sec", "/sec", "Missing", "not_a_mountpoint")], ab=_ab(exists=False))])
    assert [(r["problem"], r["problem_count"]) for r in body["nodes"]] == [(False, 0), (True, 3)]
    # 지연 노드만 쓰기 가능이면 근거가 없다(지금 상태를 모른다)
    body = _build([_node("a", [ro], ab=_ab()), _node("b", [_m()], fresh=False, ab=_ab())], storages=(CEPH,))
    assert [(r["problem"], r["problem_count"]) for r in body["nodes"]] == [(False, 0), (True, None)]


def test_states_stay_within_the_declared_enums():
    nodes = [_node("a", [_m(), _m("sec", "/x")], ab=_ab()), _node("b", fresh=False), _node("c", ab=_ab("/", "ext4"))]
    body = _build(nodes)
    for row in body["nodes"]:
        assert all(c["state"] in CELL_STATES for c in row["cells"].values())
        assert row["shared_dir"]["state"] in SHARED_STATES


# --- reconciler 와 같은 숫자 ----------------------------------------------------------------------------------------

NOW = "2026-08-02T10:00:00Z"


def test_column_summary_matches_the_reconciler_exactly(db):
    repos = Repositories(db)
    for name, path in (("ceph", "/cephfs"), ("sec", "/sec"), ("third", "/third"), ("nobody", "/nobody")):
        repos.storages.create(storage_name=name, mount_path=path, managed_root=path, backend_type="cephfs", actor="t")
    fresh_at, stale_at = "2026-08-02T09:59:00Z", "2026-08-02T09:00:00Z"
    reports = {
        "n1": ([_m(), _m("sec", "/sec"), _m("third", "/third", writable=False)], fresh_at),
        "n2": ([_m(), _m("sec", "/sec", "Missing", "missing_mount_path"),
                _m("third", "/old-third", "Missing", "not_a_mountpoint"), _m("third", "/third")], fresh_at),
        "n3": ([_m(status="Missing", reason="not_a_mountpoint"), _m("sec", "/sec")], fresh_at),
        "n4": ([_m(), _m("sec", "/sec"), _m("third", "/third")], stale_at),                     # stale -- 둘 다 무시
    }
    for node, (mounts, at) in reports.items():
        repos.agents.ingest(node, {"node_name": node, "mounts": mounts}, reported_at=at)
    repos.node_exclusions.exclude("n3", reason=None, actor="t")            # 제외 노드도 분모에 든다(reconciler 와 같게)
    reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW)
    nodes = repos.agents.list_nodes(stale_seconds=300, now_iso=NOW)
    body = build(nodes, repos.storages.list(), exclusions={r["node_name"]: r for r in repos.node_exclusions.list()},
                 artifact_base_path=None, state_root=None, build_node=None, node_list=None)
    seen = 0
    for col in body["storages"]:
        row = repos.storages.get(col["storage_name"])
        assert (col["summary"]["status"], col["summary"]["detail"]) == (row["status"], row["status_detail"]), col
        seen += 1
    assert seen == 4
    third = next(c for c in body["storages"] if c["storage_name"] == "third")["summary"]
    assert (third["ready"], third["total"]) == (2, 3)          # 경로 불일치 항목·중복도 센다(reconciler 그대로)
    nobody = next(c for c in body["storages"] if c["storage_name"] == "nobody")["summary"]
    assert (nobody["status"], nobody["total"]) == ("Unknown", 0)


# --- 이식 경계: 공용 코어는 k8s·PV·LDAP 를 모른다 ---------------------------------------------------------------------

def test_core_module_imports_nothing_k8s_specific():
    tree = ast.parse(Path(mount_status.__file__).read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported += [a.name for a in node.names]
    bad = [name for name in imported if any(word in name.lower() for word in ("k8s", "pv_", "ldap"))]
    assert imported and bad == [], bad
