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


def test_terminal_guard_records_event_with_calling_actor_as_component(db):
    # 배선 회귀 가드: 종단 가드가 트립될 때 events에 실제로 남는지, 그리고 component가
    # "stepper"로 하드코딩되지 않고 이 호출의 actor(batch_orchestrator.py 등도
    # set_job_state를 부른다)에서 유도되는지 함께 고정한다.
    repos = _repos(db)
    job_id = _mk_job(repos)
    rid = repos.data_jobs.get_job(job_id)["request_id"]
    repos.data_jobs.set_job_state(job_id, DataJobState.CANCELLED,
                                  reason_code="cancelled_by_user", actor="alice")

    repos.data_jobs.set_job_state(job_id, DataJobState.EXECUTING, actor="batch-orchestrator")

    events = repos.observability.events_for_request(rid)
    assert len(events) == 1
    assert events[0]["component"] == "batch-orchestrator"  # actor를 그대로 썼다
    assert events[0]["event_type"] == "terminal_guard_skip"
    assert events[0]["severity"] == "info"
    assert "Cancelled" in events[0]["message"] and "Executing" in events[0]["message"]


def test_terminal_guard_component_is_normalized_for_api_callers(db):
    # M2 고카디널리티 가드: routes_jobs.py/routes_requests.py/routes_batches.py는
    # set_job_state(actor=identity.actor)로 부른다 -- 사용자가 잡을 취소하면 actor는
    # 실제 사용자명("alice")이다. component에 그대로 쓰면 사용자 수만큼 값이 늘어나
    # 슬라이스 14 대시보드가 component를 그룹핑 키로 못 쓴다. "stepper"/
    # "batch-orchestrator"처럼 고정된 소수 리터럴이 아닌 actor는 "api"로 접고,
    # 실제 발신자는 message에 남긴다.
    repos = _repos(db)
    job_id = _mk_job(repos)
    rid = repos.data_jobs.get_job(job_id)["request_id"]
    repos.data_jobs.set_job_state(job_id, DataJobState.CANCELLED,
                                  reason_code="cancelled_by_user", actor="alice")

    repos.data_jobs.set_job_state(job_id, DataJobState.EXECUTING, actor="alice")

    events = repos.observability.events_for_request(rid)
    assert len(events) == 1
    assert events[0]["component"] == "api"          # "alice"가 아니라 정규화된 값
    assert "actor=alice" in events[0]["message"]     # 실제 발신자는 message에 남는다


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
