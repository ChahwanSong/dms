import pytest
from dms.domain import DataJobState, RequestState
from dms.repositories import Repositories


def _req(repos):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
                                resource_key="k", payload={}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="stepper")
    return rid


@pytest.mark.parametrize("job_state,expected", [
    (DataJobState.SUCCEEDED, "Succeeded"),
    (DataJobState.FAILED, "Failed"),
    (DataJobState.TIMED_OUT, "Failed"),
    (DataJobState.CANCELLED, "Cancelled"),
    (DataJobState.REJECTED, "Rejected"),
    (DataJobState.PREVIEW_EXPIRED, "Rejected"),
])
def test_finalize_maps_states(db, job_state, expected):
    repos = Repositories(db)
    rid = _req(repos)
    repos.requests.finalize_from_job(rid, job_state, reason_code="rc",
                                     summary={"n": 1}, actor="stepper")
    assert repos.requests.get(rid)["state"] == expected
    result = db.query_one("SELECT terminal_state, reason_code FROM results WHERE request_id = :r",
                          {"r": rid})
    assert result["terminal_state"] == expected and result["reason_code"] == "rc"


def test_finalize_nonterminal_raises(db):
    repos = Repositories(db)
    rid = _req(repos)
    with pytest.raises(ValueError):
        repos.requests.finalize_from_job(rid, DataJobState.PREFLIGHT, actor="stepper")


def test_finalize_is_idempotent(db):
    repos = Repositories(db)
    rid = _req(repos)
    repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    # 두 번째 호출은 no-op (이미 터미널)
    repos.requests.finalize_from_job(rid, DataJobState.FAILED, actor="stepper")
    assert repos.requests.get(rid)["state"] == "Succeeded"
    assert len(db.query("SELECT request_id FROM results WHERE request_id = :r", {"r": rid})) == 1
