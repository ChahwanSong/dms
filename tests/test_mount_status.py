"""마운트 현황 공용 코어(mount_status, 2026-11-23) -- 칸 판정·공용 디렉터리 판정·집계·reconciler 와의 숫자 일치·이식 경계.

규칙 원문은 src/dms/mount_status.py 모듈 docstring. 리포트는 스키마 검증 없이 저장되므로(DB 가 신뢰 경계) 쓰레기 모양에서도
예외 없이 pending·null 로 내려앉는지를 같이 본다."""
import ast
import json
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


def test_dead_or_stale_mount_with_a_host_mountpoint_is_broken_not_absent():
    """끊긴 마운트(2026-11-27 dms-ssc d207 리뷰 이식): 호스트 PID 1 mountinfo 엔 그 지점의 마운트가 있는데(is_mountpoint True) 경로가
    안 보인다(isdir 가 ENOTCONN·ESTALE 를 삼켜 False -- 죽은 ceph-fuse·낡은 NFS, 부모 마운트가 덮거나 전파가 끊겨 자식 지점이
    안 보임). 에이전트는 Missing + missing_mount_path 로 보고하지만 「안 붙인 노드」가 아니다 -- 숨기면 이 화면이 잡으려는 끊긴
    마운트가 문제 목록에서 빠진다. 사유는 에이전트 원문 그대로(화면이 「끊긴 마운트」로 가른다)."""
    entry = _m(status="Missing", reason="missing_mount_path", exists=False, is_mountpoint=True)
    cell = classify_cell({"mounts": [entry]}, CEPH)
    assert (cell["state"], cell["status"], cell["reason"], cell["is_mountpoint"], cell["exists"]) == (
        "broken", "Missing", "missing_mount_path", True, False)
    assert cell["disk"] is None                    # 마운트된 칸이 아니다(statvfs 는 루트 fs 수치일 수 있다)


@pytest.mark.parametrize("is_mountpoint", [False, None, "yes", 1, "absent-key"])
def test_missing_mount_path_without_a_host_mountpoint_stays_absent(is_mountpoint):
    # 호스트에도 마운트가 없거나(False), 모르면(None·키 없음 -- 옛 보고, 비정상 값) 지금처럼 「안 붙인 노드」(absent)
    entry = _m(status="Missing", reason="missing_mount_path", is_mountpoint=is_mountpoint)
    if is_mountpoint == "absent-key":
        del entry["is_mountpoint"]
    cell = classify_cell({"mounts": [entry]}, CEPH)
    expected_flag = False if is_mountpoint is False else None      # bool 이 아닌 값은 모름(None)으로 읽는다
    assert (cell["state"], cell["reason"], cell["is_mountpoint"]) == ("absent", "missing_mount_path", expected_flag)


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


ABSENT = _m(status="Missing", reason="missing_mount_path")
DEAD = _m(status="Missing", reason="missing_mount_path", exists=False, is_mountpoint=True)


def _counts(body):
    return [(r["node_name"], r["problem"], r["problem_count"]) for r in body["nodes"]]


def test_absent_counts_as_a_problem_only_when_the_column_is_mounted_nowhere():
    """「없음」(absent)은 같은 열에 신선한 마운트(ready·read_only) 칸이 하나라도 있으면 문제 아님(그 스토리지를 붙이지 않은 노드) --
    하나도 없으면 그 스토리지는 어디에도 마운트돼 있지 않다(mount_path 오타·마운트 배포 전): 열 전체가 회색 「없음」인데 「문제 0」
    이던 구멍(2026-11-27 dms-ssc d207 리뷰 이식). read_only 바깥값(_ro_outlier_columns)과 같은 열 단위 판정이다."""
    # 열 전체가 없음 -- 신선한 두 노드 모두 문제
    body = _build([_node("a", [ABSENT], ab=_ab()), _node("b", [ABSENT], ab=_ab())], storages=(CEPH,))
    assert _counts(body) == [("a", True, 1), ("b", True, 1)]
    t = body["totals"]
    assert (t["absent"], t["broken"], t["problem_nodes"]) == (2, 0, 2)     # 칸 상태·집계의 없음 수는 그대로
    assert body["storages"][0]["summary"]["absent"] == 2
    # 한 노드라도 정상이면 없음은 문제 아님
    body = _build([_node("a", [_m()], ab=_ab()), _node("b", [ABSENT], ab=_ab())], storages=(CEPH,))
    assert _counts(body) == [("a", False, 0), ("b", False, 0)]
    # 읽기 전용도 마운트다(그 ro 자체도 쓰기 가능 노드가 없어 바깥값이 아니다)
    body = _build([_node("a", [_m(writable=False)], ab=_ab()), _node("b", [ABSENT], ab=_ab())], storages=(CEPH,))
    assert _counts(body) == [("a", False, 0), ("b", False, 0)]
    # 리포트 지연 노드의 정상은 근거가 아니다(지금 상태 모름) -- 지연 행은 그대로 None
    body = _build([_node("a", [ABSENT], ab=_ab()), _node("b", [_m()], fresh=False, ab=_ab())], storages=(CEPH,))
    assert _counts(body) == [("a", True, 1), ("b", True, None)]
    # 확인 대기(보고 항목 없음)는 마운트가 아니고 그 칸 자체도 문제 아님 -- 없음 칸만 센다
    body = _build([_node("a", [ABSENT], ab=_ab()), _node("b", [], ab=_ab())], storages=(CEPH,))
    assert [r["cells"]["ceph"]["state"] for r in body["nodes"]] == ["absent", "pending"]
    assert _counts(body) == [("a", True, 1), ("b", False, 0)]
    # 마운트 문제·끊긴 마운트 옆의 없음도 센다(마운트된 노드가 없다)
    body = _build([_node("a", [ABSENT], ab=_ab()), _node("b", [_m(status="Missing", reason="not_a_mountpoint")], ab=_ab()),
                   _node("c", [DEAD], ab=_ab())], storages=(CEPH,))
    assert [r["cells"]["ceph"]["state"] for r in body["nodes"]] == ["absent", "broken", "broken"]
    assert _counts(body) == [("a", True, 1), ("b", True, 1), ("c", True, 1)]
    assert body["totals"]["broken"] == 2 and body["storages"][0]["summary"]["broken"] == 2
    # 열마다 따로 -- ceph 는 붙어 있고 sec 만 어디에도 없다
    body = _build([_node("a", [_m(), _m("sec", "/sec", "Missing", "missing_mount_path")], ab=_ab()),
                   _node("b", [_m("ceph", "/cephfs", "Missing", "missing_mount_path"),
                               _m("sec", "/sec", "Missing", "missing_mount_path")], ab=_ab())])
    assert _counts(body) == [("a", True, 1), ("b", True, 1)]


CASES = json.loads((Path(__file__).resolve().parent.parent / "frontend" / "src" / "features" / "mounts"
                    / "mountProblemCases.json").read_text(encoding="utf-8"))
CASE_CELL_FIELDS = ("state", "last", "status", "reason", "is_mountpoint")


def test_problem_rules_cross_fixture_matches_the_server():
    """서버 ↔ 포탈 교차 픽스처(mountProblemCases.json -- 2026-11-27): 같은 input 의 서버 출력이 expected 와 같다. 포탈
    mountProblemCases.test.ts 가 같은 expected 로 거울(columnFacts·cellProblem·columnProblem)이 같은 problem_count·problem_columns
    를 내는지 본다 -- 서버 규칙을 바꾸면 두 시험이 함께 빨개진다."""
    inp, exp = CASES["input"], CASES["expected"]
    nodes = [{"node_name": n["node_name"], "reported_at": "2026-11-27T00:00:00Z", "fresh": n["fresh"],
              "report": {"node_name": n["node_name"], "mounts": n["mounts"]}} for n in inp["nodes"]]
    body = build(nodes, inp["storages"], exclusions={}, artifact_base_path=None, state_root=None, build_node=None,
                 node_list=None)
    assert [{"node_name": r["node_name"], "fresh": r["fresh"], "problem_count": r["problem_count"],
             "cells": {k: {f: c[f] for f in CASE_CELL_FIELDS} for k, c in r["cells"].items()}}
            for r in body["nodes"]] == exp["nodes"]
    assert [{"storage_name": c["storage_name"], "summary": c["summary"]} for c in body["storages"]] == exp["storages"]
    assert body["totals"] == exp["totals"]
    # problem_columns = 신선한 행에 서버 칸 판정(_cell_problem)이 참인 칸이 있는 열(포탈 columnProblem 의 정의)
    ro, mounted = mount_status._ro_outlier_columns(body["nodes"]), mount_status._mounted_columns(body["nodes"])
    problem_columns = [s["storage_name"] for s in inp["storages"]
                       if any(r["fresh"] and mount_status._cell_problem(s["storage_name"], r["cells"][s["storage_name"]],
                                                                         ro, mounted) for r in body["nodes"])]
    assert problem_columns == exp["problem_columns"]


def test_dead_mount_counts_as_a_problem_even_beside_mounted_nodes():
    # 끊긴 마운트는 broken -- 다른 노드가 붙어 있어도 문제(예전엔 absent 라 「문제 0」으로 숨었다)
    body = _build([_node("a", [_m()], ab=_ab()), _node("b", [DEAD], ab=_ab())], storages=(CEPH,))
    assert body["nodes"][1]["cells"]["ceph"]["state"] == "broken"
    assert _counts(body) == [("a", False, 0), ("b", True, 1)]
    t = body["totals"]
    assert (t["absent"], t["broken"], t["problem_nodes"]) == (0, 1, 1)
    summary = body["storages"][0]["summary"]
    assert (summary["broken"], summary["absent"], summary["ready"], summary["total"]) == (1, 0, 1, 2)


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
    for name, path in (("ceph", "/cephfs"), ("sec", "/sec"), ("third", "/third"), ("nobody", "/nobody"),
                       ("dead", "/dead")):
        repos.storages.create(storage_name=name, mount_path=path, managed_root=path, backend_type="cephfs", actor="t")
    fresh_at, stale_at = "2026-08-02T09:59:00Z", "2026-08-02T09:00:00Z"
    reports = {
        # dead: n1 은 끊긴 마운트(칸 broken -- 2026-11-27), n2 정상 -- 칸 판정이 바뀌어도 배지 숫자는 그대로여야 한다
        "n1": ([_m(), _m("sec", "/sec"), _m("third", "/third", writable=False),
                _m("dead", "/dead", "Missing", "missing_mount_path", exists=False, is_mountpoint=True)], fresh_at),
        "n2": ([_m(), _m("sec", "/sec", "Missing", "missing_mount_path"),
                _m("third", "/old-third", "Missing", "not_a_mountpoint"), _m("third", "/third"), _m("dead", "/dead")],
               fresh_at),
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
    assert seen == 5
    third = next(c for c in body["storages"] if c["storage_name"] == "third")["summary"]
    assert (third["ready"], third["total"]) == (2, 3)          # 경로 불일치 항목·중복도 센다(reconciler 그대로)
    nobody = next(c for c in body["storages"] if c["storage_name"] == "nobody")["summary"]
    assert (nobody["status"], nobody["total"]) == ("Unknown", 0)
    dead = next(c for c in body["storages"] if c["storage_name"] == "dead")["summary"]
    assert (dead["status"], dead["detail"], dead["broken"], dead["absent"]) == ("Degraded", "ready_nodes=1/2", 1, 0)


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
