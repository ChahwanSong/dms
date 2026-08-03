from dms.execution import ExecStatus, StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


def _scan_job(repos):
    from dms.domain import RequestState
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


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


def test_pending_to_preflight_submits(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    adapter.script(f"stub-preflight-{jid}", [ExecStatus.RUNNING])  # preflight 아직 안 끝남
    _stepper(repos, adapter).run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Preflight"
    assert job["phase_refs"]["preflight"] == f"stub-preflight-{jid}"
    assert adapter.submitted_specs()[0].phase == "preflight"
    assert adapter.submitted_specs()[0].dryrun is False


def test_full_scan_lifecycle_to_succeeded(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()  # 모든 poll 기본 Succeeded
    adapter.set_summary(f"stub-execution-{jid}", {"files": 42, "bytes": 1000})
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight (submit)
    assert repos.data_jobs.get_job(jid)["state"] == "Preflight"
    stepper.run_once()   # Preflight poll Succeeded → Running (exec submit)
    assert repos.data_jobs.get_job(jid)["state"] == "Running"
    stepper.run_once()   # Running poll Succeeded → Succeeded
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded"
    assert job["result_summary"] == {"files": 42, "bytes": 1000}
    assert repos.requests.get(rid)["state"] == "Succeeded"


def test_preflight_failure_rejects(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()  # → Preflight
    adapter.script(f"stub-preflight-{jid}", [ExecStatus.FAILED])
    stepper.run_once()  # Preflight poll Failed → Rejected
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert repos.requests.get(rid)["state"] == "Rejected"


def test_execution_failure(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()  # Preflight
    stepper.run_once()  # Running (preflight succeeded)
    adapter.script(f"stub-execution-{jid}", [ExecStatus.FAILED])
    stepper.run_once()  # Running poll Failed → Failed
    assert repos.data_jobs.get_job(jid)["state"] == "Failed"
    assert repos.requests.get(rid)["state"] == "Failed"


def test_drain_stops_stepping(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    repos.control.set_control_state(maintenance=False, drain=True, reason="x", actor="admin")
    result = _stepper(repos, StubExecutionAdapter()).run_once()
    assert result == {}
    assert repos.data_jobs.get_job(jid)["state"] == "Pending"
