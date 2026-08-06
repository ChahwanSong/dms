from dms.domain import DataJobState, RequestState
from dms.execution import StubExecutionAdapter
from dms.repositories import Repositories


class _Settings:
    agent_report_stale_seconds = 300
    reconcile_interval_seconds = 30
    retention_interval_seconds = 3600
    planner_interval_seconds = 10
    stepper_interval_seconds = 5
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    agent_report_retention_days = 30
    event_retention_days = 30
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    batch_orchestrator_interval_seconds = 5
    pod_gc_after_seconds = 3600
    pod_gc_interval_seconds = 600


def _orphan(repos):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "s", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s", target="a", options={}, tool="dscan",
        worker_pool={}, precondition={}, actor="planner")
    # 잡만 터미널(크래시 흉내), 요청은 Planned로 남음
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    return rid, jid


def test_terminal_jobs_with_live_request(db):
    repos = Repositories(db)
    rid, jid = _orphan(repos)
    orphans = repos.data_jobs.terminal_jobs_with_live_request()
    assert [(o["job_id"], o["request_id"]) for o in orphans] == [(jid, rid)]


def test_orphan_recovery_via_controller(db):
    from dms.controller import build_loops, run_all_once
    repos = Repositories(db)
    rid, jid = _orphan(repos)
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")  # job-stepper 스텝이 고아 복구
    assert repos.requests.get(rid)["state"] == "Succeeded"
    assert repos.data_jobs.terminal_jobs_with_live_request() == []


def test_orphan_recovery_propagates_job_result_summary(db):
    """SUCCEEDED로 result_summary를 이미 가진 채 request finalize 직전 크래시한
    시나리오: 복구된 request의 결과 summary가 잡의 result_summary와 같아야 한다
    (Task 8이 겨냥한 데이터 유실 방지 시나리오)."""
    from dms.controller import build_loops, run_all_once
    repos = Repositories(db)
    rid, jid = _orphan(repos)
    repos.data_jobs.set_artifact(jid, artifact_uri=None,
                                 result_summary={"files": 7, "bytes": 999})
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")
    assert repos.requests.get(rid)["state"] == "Succeeded"
    result = db.query_one("SELECT summary FROM results WHERE request_id = :r", {"r": rid})
    from dms.db import load_json
    assert load_json(result["summary"]) == {"files": 7, "bytes": 999}
