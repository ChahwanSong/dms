"""Tests for controller job-stepper loop integration."""
from dms.controller import build_loops, run_all_once
from dms.execution import StubExecutionAdapter
from dms.domain import RequestState
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
    vcjob_ttl_seconds = 86400
    pod_gc_after_seconds = 3600
    pod_gc_interval_seconds = 600


def test_stepper_loop_registered_second(db):
    loops = build_loops(_Settings(), Repositories(db))
    assert [l.name for l in loops] == [
        "planner", "job-stepper", "storage-reconciler", "retention",
        "batch-orchestrator", "pod-gc", "artifact-base-check"]
    assert loops[1].interval_seconds == 5


def test_stepper_loop_advances_pending_job(db):
    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={"identity": {"uid": 1}, "candidates": {"primary": ["n1"]},
                     "process_count": 8, "queue": "dms-data",
                     "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")  # job-stepper 루프가 Pending → Preflight
    assert repos.data_jobs.get_job(jid)["state"] == "Preflight"


def test_expired_preview_sweep_finalizes_request(db):
    from dms.domain import DataJobState, RequestState
    from dms.execution import StubExecutionAdapter
    repos = Repositories(db)
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="k", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync", worker_pool={}, precondition={}, actor="planner")
    # ConfirmPending + 과거 만료 시각
    repos.data_jobs.set_preview(jid, fingerprint="sha256:abc",
        expires_at="2000-01-01T00:00:00Z", artifact_uri=None)
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    # controller stepper 루프 1회
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")
    # 잡은 PreviewExpired, 요청은 Rejected + results 행
    assert repos.data_jobs.get_job(jid)["state"] == "PreviewExpired"
    assert repos.requests.get(rid)["state"] == "Rejected"
    result = db.query_one("SELECT terminal_state, reason_code FROM results WHERE request_id = :r",
                          {"r": rid})
    assert result["terminal_state"] == "Rejected" and result["reason_code"] == "preview_expired"
    # resource_key 잠금 해제 확인: 동일 key 새 요청은 find_active가 비터미널로 잡지 않음
    assert repos.requests.find_active("k") is None
