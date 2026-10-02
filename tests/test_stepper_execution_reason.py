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


def test_parse_execution_reason_whitelist_and_shape():
    assert EXECUTION_REASONS == frozenset({"workers_unreachable"})
    assert parse_execution_reason([("p", "x\n  DMS_EXEC_REASON=workers_unreachable  \n", None)]) == "workers_unreachable"
    assert parse_execution_reason([("p", None, "ImagePullBackOff"), ("w", "DMS_EXEC_REASON=workers_unreachable", None)]) \
        == "workers_unreachable"
    assert parse_execution_reason([("p", "DMS_PREFLIGHT_REASON=target_not_readable", None)]) is None
    assert parse_execution_reason(None) is None and parse_execution_reason([]) is None
