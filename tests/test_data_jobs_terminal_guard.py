"""터미널 가드: 종단 상태(TERMINAL_DATA_JOB_STATES)에 들어간 잡은 늦게 도착한
stepper 틱 등 어떤 set_job_state 호출로도 되돌아가지 않는다. 조용한 멱등 무시 —
예외를 던지지 않고, 일어나지 않은 전이는 state_transitions에 기록하지 않는다."""
from dms.domain import DataJobState, TERMINAL_DATA_JOB_STATES
from dms.repositories import Repositories


def _repos(db):
    return Repositories(db)


def _mk_request(repos):
    return repos.requests.create(
        operation="scan", requester_id="alice", actor="alice",
        resource_key="data.scan:s1:a:ff", payload={"storage": "s1", "target": "a"},
        priority="mid")


def _mk_job(repos):
    rid = _mk_request(repos)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool="dscan", worker_pool={}, precondition={},
        actor="planner")
    return job_id


def test_terminal_job_state_is_not_overwritten(db):
    repos = _repos(db)
    job_id = _mk_job(repos)
    repos.data_jobs.set_job_state(job_id, DataJobState.CANCELLED,
                                  reason_code="cancelled_by_user", actor="user")
    before = repos.data_jobs.job_transitions(job_id)

    # 늦게 도착한 stepper 틱
    repos.data_jobs.set_job_state(job_id, DataJobState.EXECUTING, actor="stepper")

    job = repos.data_jobs.get_job(job_id)
    assert job["state"] == "Cancelled"
    assert job["reason_code"] == "cancelled_by_user"
    # 일어나지 않은 전이는 기록하지 않는다
    assert repos.data_jobs.job_transitions(job_id) == before


def test_non_terminal_transitions_still_work(db):
    repos = _repos(db)
    job_id = _mk_job(repos)
    before = repos.data_jobs.job_transitions(job_id)

    repos.data_jobs.set_job_state(job_id, DataJobState.PREFLIGHT, actor="stepper")

    job = repos.data_jobs.get_job(job_id)
    assert job["state"] == "Preflight"
    after = repos.data_jobs.job_transitions(job_id)
    assert len(after) == len(before) + 1
    assert (after[-1]["from_state"], after[-1]["to_state"]) == ("Pending", "Preflight")


def test_guard_applies_to_every_terminal_state(db):
    repos = _repos(db)
    for terminal_state in TERMINAL_DATA_JOB_STATES:
        job_id = _mk_job(repos)
        repos.data_jobs.set_job_state(job_id, terminal_state,
                                      reason_code="reached_terminal", actor="stepper")
        before = repos.data_jobs.job_transitions(job_id)

        repos.data_jobs.set_job_state(job_id, DataJobState.EXECUTING, actor="stepper")

        job = repos.data_jobs.get_job(job_id)
        assert job["state"] == terminal_state.value
        assert repos.data_jobs.job_transitions(job_id) == before
