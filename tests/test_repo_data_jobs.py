from dms.domain import DataJobState
from dms.repositories import Repositories


def _repos(db):
    return Repositories(db)


def _mk_request(repos):
    return repos.requests.create(
        operation="scan", requester_id="alice", actor="alice",
        resource_key="data.scan:s1:a:ff", payload={"storage": "s1", "target": "a"},
        priority="mid")


def test_create_plan_and_job_links(db):
    repos = _repos(db)
    rid = _mk_request(repos)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={"top_k": 5}, tool="dscan",
        worker_pool={"tool": "dscan", "candidates": {"primary": ["n1"]}},
        precondition={"job_id": "x"}, actor="planner")
    job = repos.data_jobs.get_job(job_id)
    assert job["state"] == "Pending"
    assert job["tool"] == "dscan"
    assert job["options"] == {"top_k": 5}
    assert job["worker_pool"]["candidates"]["primary"] == ["n1"]
    plan = db.query_one("SELECT job_id, state FROM plans WHERE plan_id = :p", {"p": plan_id})
    assert plan["job_id"] == job_id and plan["state"] == "Planned"


def test_job_state_transitions_recorded(db):
    repos = _repos(db)
    rid = _mk_request(repos)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool="dscan", worker_pool={}, precondition={},
        actor="planner")
    repos.data_jobs.set_job_state(job_id, DataJobState.PREFLIGHT, actor="stepper")
    repos.data_jobs.set_job_state(job_id, DataJobState.REJECTED,
                                 reason_code="posix_permission_denied", actor="stepper")
    ts = repos.data_jobs.job_transitions(job_id)
    assert [(t["from_state"], t["to_state"]) for t in ts] == [
        (None, "Pending"), ("Pending", "Preflight"), ("Preflight", "Rejected")]
    assert ts[2]["reason_code"] == "posix_permission_denied"
    assert repos.data_jobs.get_job(job_id)["reason_code"] == "posix_permission_denied"


def test_list_jobs_by_request(db):
    repos = _repos(db)
    rid = _mk_request(repos)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool="dscan", worker_pool={}, precondition={},
        actor="planner")
    assert [j["job_id"] for j in repos.data_jobs.list_jobs(request_id=rid)] == [job_id]
    assert repos.data_jobs.list_jobs(request_id="nope") == []
