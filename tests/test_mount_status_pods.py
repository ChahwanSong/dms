"""마운트 현황의 DMS 파드 덧붙임(mount_status_pods, 2026-11-23) -- 순수 판정. 끝에서 끝(api_base_checks·control_state 실 DB)은
tests/test_api_mount_status.py 가 본다.

규칙 원문은 src/dms/mount_status_pods.py 모듈 docstring: 신선한 행만, 점검 결과는 지금 base 를 본 것만, 꼬리표는 행당 한 번,
read_failed 는 권한 사유를 빼고, DMS 공용 열은 한 칸(이미 센 공용 문제에 파드 실패를 겹쳐 세지 않는다)."""
import ast
from pathlib import Path

import pytest

from dms import mount_status_pods
from dms.artifact_base import ARTIFACT_BASE_CHECK_REASONS
from dms.mount_status import build
from dms.mount_status_pods import (API_POD, CONTROLLER_POD, POSTURE_REASONS, controller_fresh_seconds, decorate,
                                   pods_from)

URI = "file:///dms_shared/dms/artifacts"
BASE = "/dms_shared/dms/artifacts"
CEPH = {"storage_name": "ceph", "mount_path": "/cephfs", "enabled": 1, "user_enabled": 1, "backend_type": "cephfs"}
CUT = "2026-11-23T00:27:00Z"           # 신선 창 시작(api·컨트롤러 공용으로 둔다 -- 테스트가 시각을 고른다)
NOW = "2026-11-23T00:30:00Z"


def _api(instance, node, *, ok=True, reason=None, uri=URI, checked_at=NOW, started_at=NOW, gate_state="passed"):
    return {"instance": instance, "node_name": node, "uri": uri, "ok": ok, "reason": reason, "posture": None,
            "fstype": "nfs4", "gate_state": gate_state, "started_at": started_at, "checked_at": checked_at,
            "first_ok_at": None, "ready_at": None}


def _ctl(node, *, ok=1, reason=None, uri=URI, at=NOW):
    return {"build_node_name": None, "artifact_base_check_node": node, "artifact_base_check_uri": uri,
            "artifact_base_check_ok": ok, "artifact_base_check_reason": reason, "artifact_base_check_at": at}


def _pods(api_rows=(), control=None, *, base_uri=URI):
    return pods_from(list(api_rows), control, base_uri=base_uri, api_cutoff=CUT, controller_cutoff=CUT)


def _ab(mount_point="/dms_shared", fstype="nfs4", exists=True):
    return {"path": BASE, "exists": exists, "writable": True, "mount_point": mount_point, "fstype": fstype}


def _node(name, *, fresh=True, ab=None):
    report = {"node_name": name, "probe_mode": "host_root",
              "mounts": [{"storage_name": "ceph", "mount_path": "/cephfs", "status": "Ready", "writable": True}],
              "artifact_base": ab if ab is not None else _ab()}
    return {"node_name": name, "reported_at": NOW, "fresh": fresh, "report": report}


def _body(nodes, *, base_path=BASE):
    return build(list(nodes), [CEPH], exclusions={}, artifact_base_path=base_path, state_root=None, build_node="w1",
                 node_list=None)


def _rows(body):
    return {r["node_name"]: r for r in body["nodes"]}


# --- pods_from -----------------------------------------------------------------------------------------------------

def test_fresh_api_rows_and_controller_become_pods_in_a_fixed_order():
    pods = _pods([_api("api-b", "w2"), _api("api-a", "w2"), _api("api-c", "w1")], _ctl("w1"))
    assert [(p["role"], p["node_name"], p["instance"]) for p in pods] == [
        (API_POD, "w1", "api-c"), (API_POD, "w2", "api-a"), (API_POD, "w2", "api-b"), (CONTROLLER_POD, "w1", None)]
    api = pods[0]
    assert api == {"role": API_POD, "instance": "api-c", "node_name": "w1", "ok": True, "reason": None,
                   "read_failed": False, "checked_at": NOW, "gate_state": "passed", "in_table": False}
    assert pods[-1]["gate_state"] is None and pods[-1]["ok"] is True       # sqlite 정수 1 → True


def test_only_fresh_rows_with_a_node_are_placed():
    pods = _pods([
        _api("old", "w1", checked_at="2026-11-23T00:20:00Z", started_at="2026-11-23T00:20:00Z"),   # 죽은 파드의 행
        _api("nonode", None),                                                                    # 노드를 모르는 행
        _api("blank", ""),
        _api("booting", "w3", checked_at=None, started_at=NOW, ok=None, gate_state="waiting"),  # 첫 회차 전
    ], _ctl("w2", at="2026-11-23T00:10:00Z"))                                                  # 멈춘 옛 컨트롤러 기록
    assert [(p["instance"], p["node_name"], p["ok"]) for p in pods] == [("booting", "w3", None)]


def test_controller_needs_a_node_and_a_check_time():
    assert _pods([], _ctl(None)) == []
    assert _pods([], {**_ctl("w1"), "artifact_base_check_at": None}) == []
    assert _pods([], None) == []


def test_results_from_another_base_are_unknown_but_the_tag_stays():
    pods = _pods([_api("api-a", "w1", ok=False, reason="artifact_base_jobs_missing", uri="file:///old")],
                 _ctl("w2", ok=0, reason="artifact_base_missing", uri="file:///old"))
    assert [(p["node_name"], p["ok"], p["reason"], p["read_failed"]) for p in pods] == [
        ("w1", None, None, False), ("w2", None, None, False)]


def test_read_failed_ignores_posture_reasons():
    pods = _pods([_api("a", "w1", ok=False, reason="artifact_base_jobs_missing"),
                  _api("b", "w2", ok=False, reason="artifact_base_not_owned"),
                  _api("c", "w3", ok=None)],
                 _ctl("w4", ok=0, reason="artifact_base_outside_allowlist"))
    assert {p["instance"] or p["role"]: p["read_failed"] for p in pods} == {
        "a": True, "b": False, "c": False, CONTROLLER_POD: False}
    assert _pods([], _ctl("w1", ok=False, reason="artifact_base_check_timeout"))[0]["read_failed"] is True
    # 이름과 달리 읽기 실패만이 아니다 -- 컨트롤러 쓰기 왕복 실패도 경로 점검 실패다(포탈 글자는 중립 「점검 실패」, 이식 리뷰)
    assert _pods([], _ctl("w1", ok=False, reason="artifact_base_not_writable"))[0]["read_failed"] is True


@pytest.mark.parametrize("garbage", [None, [], ["x", 3], [{"node_name": 7, "checked_at": 5}]])
def test_garbage_never_raises(garbage):
    assert pods_from(garbage, "nope", base_uri=URI, api_cutoff=CUT, controller_cutoff=CUT) == []


def test_controller_window_is_three_intervals_with_a_floor():
    assert controller_fresh_seconds(30) == 180
    assert controller_fresh_seconds(120) == 360
    assert controller_fresh_seconds(None) == 180


def test_posture_reasons_are_real_check_reasons():
    assert POSTURE_REASONS <= ARTIFACT_BASE_CHECK_REASONS


# --- decorate ------------------------------------------------------------------------------------------------------

def test_tags_go_on_matching_rows_once_and_after_existing_roles():
    body = _body([_node("w1"), _node("w2")])
    pods = _pods([_api("api-a", "w1"), _api("api-b", "w1")], _ctl("w1"))
    decorate(body, pods)
    rows = _rows(body)
    assert rows["w1"]["roles"] == ["build", API_POD, CONTROLLER_POD]
    assert rows["w2"]["roles"] == []
    assert body["control_pods"] is pods and all(p["in_table"] for p in pods)
    assert body["totals"]["problem_nodes"] == 0


def test_pods_on_nodes_without_an_agent_row_are_listed_outside_the_table():
    body = _body([_node("w1")])
    decorate(body, _pods([_api("api-a", "cp1")], _ctl("cp2")))
    assert [(p["node_name"], p["in_table"]) for p in body["control_pods"]] == [("cp1", False), ("cp2", False)]
    assert _rows(body)["w1"]["roles"] == ["build"]


def test_read_failure_on_a_healthy_looking_row_counts_one_problem():
    body = _body([_node("w1"), _node("w2")])
    decorate(body, _pods([_api("api-a", "w1", ok=False, reason="artifact_base_jobs_missing")],
                         _ctl("w1", ok=0, reason="artifact_base_jobs_missing")))
    w1 = _rows(body)["w1"]
    assert (w1["problem"], w1["problem_count"]) == (True, 1)          # API·컨트롤러 둘 다 실패해도 공용 열은 한 칸
    assert body["totals"]["problem_nodes"] == 1
    assert body["totals"]["shared_problem"] == 0                       # 공용 디렉터리 수는 에이전트 보고 그대로(코어 계약)


def test_shared_problem_already_counted_is_not_counted_twice():
    body = _body([_node("w1", ab=_ab(mount_point="/", fstype="ext4"))])   # 사고 모양: 공용 디렉터리가 로컬
    w1 = _rows(body)["w1"]
    assert (w1["shared_dir"]["state"], w1["problem_count"]) == ("local", 1)
    decorate(body, _pods([_api("api-a", "w1", ok=False, reason="artifact_base_jobs_missing")]))
    assert (w1["problem_count"], w1["roles"]) == (1, ["build", API_POD])
    assert body["totals"]["problem_nodes"] == 1


def test_stale_rows_posture_and_unknown_results_do_not_change_counts():
    body = _body([_node("w1", fresh=False), _node("w2"), _node("w3")])
    decorate(body, _pods([_api("a", "w1", ok=False, reason="artifact_base_jobs_missing"),
                          _api("b", "w2", ok=False, reason="artifact_base_group_writable"),
                          _api("c", "w3", ok=False, reason="artifact_base_jobs_missing", uri="file:///old")]))
    rows = _rows(body)
    assert rows["w1"]["problem_count"] is None and rows["w1"]["problem"] is True
    assert (rows["w2"]["problem_count"], rows["w3"]["problem_count"]) == (0, 0)
    assert body["totals"]["problem_nodes"] == 1


def test_no_shared_column_means_tags_only():
    body = _body([_node("w1")], base_path=None)
    decorate(body, _pods([_api("a", "w1", ok=False, reason="artifact_base_jobs_missing")]))
    w1 = _rows(body)["w1"]
    assert (w1["roles"], w1["problem_count"]) == (["build", API_POD], 0)


def test_overlay_module_does_no_io_and_knows_no_k8s():
    tree = ast.parse(Path(mount_status_pods.__file__).read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
    assert imported == ["mount_status"], imported
