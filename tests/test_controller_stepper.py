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
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


def test_stepper_loop_registered_second(db):
    loops = build_loops(_Settings(), Repositories(db))
    assert [l.name for l in loops] == [
        "planner", "job-stepper", "storage-reconciler", "retention"]
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
