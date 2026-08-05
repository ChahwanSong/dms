"""Task 4: _poll_execution의 SUCCEEDED 분기가 artifact_uri를 기록하는지 확인.

scan은 preview 게이트가 없으므로 execution 성공 경로가 artifact_uri를 남기는
유일한 지점이다. sync는 preview에서 이미 기록되므로, execution 성공 경로가
그 값을 유지하는지(같은 URI로 COALESCE) 함께 확인한다.
"""
from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


def _scan_job(repos):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={"tool": "dscan", "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _sync_job(repos):
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="k", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync",
        worker_pool={"tool": "dsync", "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def test_scan_execution_success_records_artifact_uri(db):
    """scan은 preview 게이트를 거치지 않는다 — execution SUCCEEDED 분기가
    artifact_uri를 기록하는 유일한 지점."""
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-execution-{jid}", {"files": 42, "bytes": 1000})
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight (submit)
    stepper.run_once()   # Preflight poll Succeeded → Running (exec submit)
    stepper.run_once()   # Running poll Succeeded → Succeeded
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded"
    assert job["artifact_uri"] == f"file:///art/{jid}"


def test_sync_execution_success_keeps_preview_artifact_uri(db):
    """sync는 preview에서 먼저 artifact_uri를 기록한다. execution 성공 분기가
    같은 값을 다시 쓰더라도(COALESCE) sync 경로가 흔들리면 안 된다."""
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3})
    stepper = _stepper(repos, adapter)
    stepper.run_once(); stepper.run_once(); stepper.run_once()  # → ConfirmPending
    preview_uri = repos.data_jobs.get_job(jid)["artifact_uri"]
    assert preview_uri == f"file:///art/{jid}"
    # confirm을 흉내: 상태를 Executing으로, confirmed_fingerprint 저장
    fp = repos.data_jobs.get_job(jid)["preview_fingerprint"]
    repos.data_jobs.set_confirmed(jid, fp)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    stepper.run_once()  # Executing, ref 없음 → exec_preflight submit (재검증)
    stepper.run_once()  # exec_preflight poll Succeeded → execution submit
    stepper.run_once()  # execution poll Succeeded → Succeeded
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded"
    assert job["artifact_uri"] == preview_uri == f"file:///art/{jid}"
