"""실행·미리보기 실패 사유 세분화(2026-10-02): 러너의 DMS_EXEC_REASON= 마커를 잡 reason_code 로 승격한다.

프로덕션 간헐 실패(dscan rc 255, "Could not resolve hostname <worker-0>.<svc>")는 화면에 사유 없는
execution_failed 로만 보였다. 러너가 이제 워커 준비(IP 해석 + ssh)를 제한 시간 안에 못 끝내면 mpirun 없이
DMS_EXEC_REASON=workers_unreachable 을 찍는다 -- 스테퍼가 그 마커를 읽어 사유로 올린다. 승격은 화이트리스트
(execution_manifests.EXECUTION_REASONS)로만 한다(파드 로그는 신뢰 입력이 아니다).
"""
import json

import pytest

from dms.domain import RequestState
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.execution_manifests import EXECUTION_REASONS, parse_execution_reason
from dms.repositories import Repositories
from dms.stepper import JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def _seed_storage(repos, name):
    if repos.storages.get(name) is None:
        repos.storages.create(storage_name=name, mount_path=f"/{name}",
                              managed_root=f"/{name}/dms", backend_type="cephfs", actor="test")


def _pool(tool):
    return {"tool": tool, "identity": {"uid": 10001, "gid": 10000, "username": "alice", "groups": [],
                                       "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8, "queue": "dms-data",
            "priority_class": "dms-mid"}


def _scan_job(repos, key="k-scan"):
    _seed_storage(repos, "s1")
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice", resource_key=key,
                                payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid", storage_name="s1",
                                     target="a", options={}, tool="dscan", worker_pool=_pool("dscan"),
                                     precondition={}, actor="planner")
    return rid, jid


def _sync_job(repos):
    _seed_storage(repos, "src")
    _seed_storage(repos, "dst")
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice", resource_key="k-sync",
                                payload={"source_storage": "src", "source": "a", "destination_storage": "dst",
                                         "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid", source_storage="src",
                                     source="a", destination_storage="dst", destination="b", options={},
                                     tool="dsync", worker_pool=_pool("dsync"), precondition={}, actor="planner")
    return rid, jid


LAUNCHER_LOG = ("DMS_JR_WORKER_READY host=w1.job ip=10.42.6.11 waited=0s\n"
                "DMS_EXEC_REASON=workers_unreachable\n"
                "DMS_JR_WORKER_UNREACHABLE host=w0.job stage=resolve waited=300s ready=1/4\n")


def _fail_execution(repos, adapter, jid, status, log):
    stepper = JobStepper(repos, adapter, settings=_Settings())
    stepper.run_once()                                   # Pending -> Preflight
    stepper.run_once()                                   # Preflight ok -> Running(execution 제출)
    ref = f"stub-execution-{jid}"
    adapter.script(ref, [status])
    adapter.set_log(ref, [("j-launcher-0", log, None)])
    stepper.run_once()
    return repos.data_jobs.get_job(jid)


def test_worker_marker_becomes_the_execution_failure_reason(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    job = _fail_execution(repos, StubExecutionAdapter(), jid, ExecStatus.FAILED, LAUNCHER_LOG)
    assert job["state"] == "Failed"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "workers_unreachable"
    assert repos.requests.last_reason_code(rid) == "workers_unreachable"
    # 진단 로그 박제는 그대로(한 번) -- 화면에서 어느 워커·어느 단계였는지 본다
    doc = json.loads(repos.data_jobs.get_job(jid)["diag_logs"])
    assert doc["phase"] == "execution" and "stage=resolve" in doc["entries"][0]["log"]


@pytest.mark.parametrize("log", ["Traceback (most recent call last) ...", "",
                                 "DMS_EXEC_REASON=rm_everything\n", None])
def test_no_or_unknown_marker_keeps_execution_failed(db, log):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    _fail_execution(repos, StubExecutionAdapter(), jid, ExecStatus.FAILED, log)
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "execution_failed"


def test_timed_out_execution_is_unchanged(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    job = _fail_execution(repos, StubExecutionAdapter(), jid, ExecStatus.TIMED_OUT, LAUNCHER_LOG)
    assert job["state"] == "TimedOut"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "execution_failed"


def test_preview_failure_with_worker_marker(db):
    # dsync 미리보기(--dryrun)도 같은 러너·mpirun 을 탄다 -- 같은 마커가 preview_failed 대신 사유가 된다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    stepper = JobStepper(repos, adapter, settings=_Settings())
    stepper.run_once()                                   # Pending -> Preflight
    ref = f"stub-preview-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("pv-launcher-0", LAUNCHER_LOG, None)])
    stepper.run_once()                                   # Preflight ok -> PreviewRunning
    stepper.run_once()                                   # Preview FAILED
    assert repos.data_jobs.get_job(jid)["state"] == "Failed"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "workers_unreachable"
    doc = json.loads(repos.data_jobs.get_job(jid)["diag_logs"])
    assert doc["phase"] == "preview"


def test_preview_failure_without_marker_keeps_preview_failed(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    stepper = JobStepper(repos, adapter, settings=_Settings())
    stepper.run_once()
    ref = f"stub-preview-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("pv-launcher-0", "boom", None)])
    stepper.run_once()
    stepper.run_once()
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "preview_failed"


def test_failed_worker_pod_group_marker_becomes_the_reason(db):
    # 2026-10-07 보조 그룹: 워커 셸(_identity_materialize_stmt)이 그룹 물질화·자기검증 실패에 마커를 찍고 exit 1
    # → PodFailed AbortJob. 어댑터는 launcher 와 **실패 워커** 파드 로그를 함께 돌려준다 -- launcher 로그엔 마커가
    # 없어도(워커가 안 떠 mpirun 자체가 실패) 워커 항목에서 승격돼야 한다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = JobStepper(repos, adapter, settings=_Settings())
    stepper.run_once()                                   # Pending -> Preflight
    stepper.run_once()                                   # Preflight ok -> Running(execution 제출)
    ref = f"stub-execution-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("j-launcher-0", "ORTE was unable to reliably start one or more daemons\n", None),
                          ("j-worker-0", "dms: groups= 10000  expected= 10000 10010 \n"
                                         "DMS_EXEC_REASON=identity_groups_not_applied\n", None)])
    stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Failed"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "identity_groups_not_applied"
    assert repos.requests.last_reason_code(rid) == "identity_groups_not_applied"


def test_parse_execution_reason_whitelist_and_shape():
    # target_path_redirected(2026-10-21): 러너의 drm 직전 rm 대상 봉쇄 재확인(runner.rm_target_redirected).
    assert EXECUTION_REASONS == frozenset({"workers_unreachable", "identity_groups_not_applied", "target_path_redirected"})
    assert parse_execution_reason([("launcher", "DMS_EXEC_REASON=target_path_redirected\n", None)]) == "target_path_redirected"
    assert parse_execution_reason([("p", "x\n  DMS_EXEC_REASON=workers_unreachable  \n", None)]) == "workers_unreachable"
    assert parse_execution_reason([("p", None, "ImagePullBackOff"), ("w", "DMS_EXEC_REASON=workers_unreachable", None)]) \
        == "workers_unreachable"
    assert parse_execution_reason([("p", "DMS_PREFLIGHT_REASON=target_not_readable", None)]) is None
    assert parse_execution_reason(None) is None and parse_execution_reason([]) is None


def _batch_child(repos, db, rid, status):
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin", max_concurrency=1,
                               options={}, note=None, items=[{"storage": "s1", "target": "a"}],
                               status=status, name="c", auth_method="session")
    db.execute("UPDATE requests SET batch_id = :b WHERE request_id = :r", {"b": bid, "r": rid})
    return bid


def _mark(repos, rid, bid):
    from dms.batch_cancel import mark_cancel_reached
    mark_cancel_reached(repos, rid, bid, component="api")


@pytest.mark.parametrize("batch_status,marked,expected", [
    ("Cancelling", True, "Cancelled"), ("Cancelled", True, "Cancelled"),
    ("Cancelling", False, "Failed"),          # 취소가 아직 닿지 않은 자식의 진짜 실패(거짓 취소 금지)
    ("Running", True, "Failed")])
def test_failure_of_a_cancelled_batch_child_is_recorded_as_cancelled(db, batch_status, marked, expected):
    """배치 취소가 지운 vcjob 을 stepper 가 FAILED 로 본 것 -- 취소 중·취소된 배치의 자식이고 취소가 그 자식을 지우러 왔다는
    증거(batch_cancel.CANCEL_MARK_EVENT)가 있으면 Cancelled·cancelled_by_batch(2026-10-16: 실패 수·「실패분 재실행」에 들어가지
    않게). 증거가 없으면 진짜 실패 그대로. 진단 박제는 그대로 한다. 활성 배치의 실패는 실패 그대로."""
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    bid = _batch_child(repos, db, rid, "Running")
    adapter = StubExecutionAdapter()
    stepper = JobStepper(repos, adapter, settings=_Settings())
    stepper.run_once()                                   # Pending -> Preflight
    stepper.run_once()                                   # Preflight ok -> Running(execution 제출 -- 배치가 굴러갈 때)
    repos.batches.set_status(bid, batch_status)          # 그 뒤 취소
    if marked:
        _mark(repos, rid, bid)
    ref = f"stub-execution-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("j-launcher-0", LAUNCHER_LOG, None)])
    stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == expected
    assert repos.requests.get(rid)["state"] == expected
    want = "cancelled_by_batch" if expected == "Cancelled" else "workers_unreachable"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == want
    assert json.loads(repos.data_jobs.get_job(jid)["diag_logs"])["phase"] == "execution"


def test_preflight_pod_vanished_by_the_cancel_is_cancelled_not_rejected(db):
    # 사전 점검 파드를 취소가 지우면 폴링이 FAILED -> REJECTED(preflight_failed) 다. 증거가 있으면 Cancelled.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    bid = _batch_child(repos, db, rid, "Cancelling")
    adapter = StubExecutionAdapter()
    stepper = JobStepper(repos, adapter, settings=_Settings())
    repos.batches.set_status(bid, "Running")
    stepper.run_once()                                   # Pending -> Preflight(제출은 배치가 굴러갈 때)
    repos.batches.set_status(bid, "Cancelling")
    _mark(repos, rid, bid)
    adapter.script(f"stub-preflight-{jid}", [ExecStatus.FAILED])
    stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Cancelled"
    assert repos.requests.get(rid)["state"] == "Cancelled"


@pytest.mark.parametrize("operation", ["scan", "sync"])
def test_cancelling_batch_child_does_not_submit_the_next_phase(db, operation):
    """취소 중 배치의 자식은 사전 점검을 마쳐도 다음 단계(scan 실행·sync 미리보기)를 제출하지 않고 Cancelled 로 끝난다
    (2026-10-16 검증 지적: 종료가 실패해 Cancelling 에 머문 배치의 자식이 실행 vcjob 을 제출했다 -- 보고 증상의 재현)."""
    repos = Repositories(db)
    rid, jid = _scan_job(repos) if operation == "scan" else _sync_job(repos)
    bid = _batch_child(repos, db, rid, "Running")
    adapter = StubExecutionAdapter()
    stepper = JobStepper(repos, adapter, settings=_Settings())
    stepper.run_once()                                   # Pending -> Preflight
    repos.batches.set_status(bid, "Cancelling")
    stepper.run_once()                                   # 사전 점검 성공 -- 다음 단계를 내지 않는다
    assert [sp.phase for sp in adapter.submitted_specs()] == ["preflight"]
    assert repos.data_jobs.get_job(jid)["state"] == "Cancelled"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "cancelled_by_batch"
    assert repos.requests.get(rid)["state"] == "Cancelled"


def test_cancelled_batch_child_pending_job_is_not_submitted(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    _batch_child(repos, db, rid, "Cancelling")
    adapter = StubExecutionAdapter()
    JobStepper(repos, adapter, settings=_Settings()).run_once()
    assert adapter.submitted_specs() == []
    assert repos.data_jobs.get_job(jid)["state"] == "Cancelled"
