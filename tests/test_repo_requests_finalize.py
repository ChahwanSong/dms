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


def test_finalize_crash_between_writes_rolls_back_both(db):
    """슬라이스 24 실증의 실 결함(BACKLOG §2.1): set_state 는 커밋됐는데
    record_result 가 터지면 요청은 종단, results 는 없음 -- 종단 요청은 고아
    스윕(terminal_jobs_with_live_request)의 시야 밖이라 결손이 영구다.
    원자화 후엔 전부-또는-전무: 크래시 주입 시 상태·전이 이력·results 셋 다
    남지 않아야 하고, 요청이 비종단으로 남아 다음 틱 재시도가 완주해야 한다."""
    repos = Repositories(db)
    rid = _req(repos)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("crash before record_result")

    repos.requests.record_result = _boom      # 인스턴스 속성이 메서드를 가린다
    with pytest.raises(RuntimeError):
        repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    del repos.requests.record_result          # 원복 -- 클래스 메서드가 되살아난다
    # 전부-또는-전무: 상태도, 전이 이력도, results 도 남지 않았다.
    assert repos.requests.get(rid)["state"] == "Running"
    assert db.query("SELECT request_id FROM results WHERE request_id = :r",
                    {"r": rid}) == []
    assert all(t["to_state"] != "Succeeded" for t in repos.requests.transitions(rid))
    # 다음 틱 재시도: 비종단이라 멱등 가드에 안 걸리고 정상 완주한다.
    repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    assert repos.requests.get(rid)["state"] == "Succeeded"
    assert len(db.query("SELECT request_id FROM results WHERE request_id = :r",
                        {"r": rid})) == 1


def test_finalize_idempotent_return_opens_no_transaction(db):
    # 멱등 가드(이미 종단이면 return)는 읽기 후 조기 반환이다 -- 트랜잭션 안에
    # 넣으면 고아 스윕이 매 틱 재호출하는 no-op 마다 빈 BEGIN/COMMIT 이 열린다.
    # 가드가 트랜잭션 밖이라는 사실 자체를 계약으로 고정한다(현행도 그렇다 --
    # 이 테스트는 원자화가 가드를 안으로 끌고 들어가는 회귀를 막는 그물이다).
    repos = Repositories(db)
    rid = _req(repos)
    repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")

    def _no_txn():
        raise AssertionError("멱등 조기 반환 경로가 트랜잭션을 열었다")

    db.transaction = _no_txn                  # 인스턴스 속성이 메서드를 가린다
    try:
        # 두 번째 finalize 는 no-op 이어야 하고, 트랜잭션을 열면 위 AssertionError.
        repos.requests.finalize_from_job(rid, DataJobState.FAILED, actor="stepper")
    finally:
        del db.transaction
    assert repos.requests.get(rid)["state"] == "Succeeded"


def test_set_state_with_result_is_atomic_and_retryable(db):
    # planner 크래시 테스트의 하부 메커니즘을 레포 수준에서 직접 고정한다
    # (finalize 크래시 테스트와 같은 골격 -- 같은 결함 계열의 같은 처방).
    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="alice",
                                actor="alice", resource_key="k2", payload={},
                                priority="mid")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("crash before record_result")

    repos.requests.record_result = _boom
    with pytest.raises(RuntimeError):
        repos.requests.set_state_with_result(
            rid, RequestState.REJECTED, reason_code="storage_missing",
            actor="planner")
    del repos.requests.record_result
    assert repos.requests.get(rid)["state"] == "Pending"
    assert all(t["to_state"] != "Rejected" for t in repos.requests.transitions(rid))
    assert db.query("SELECT request_id FROM results WHERE request_id = :r",
                    {"r": rid}) == []
    repos.requests.set_state_with_result(
        rid, RequestState.REJECTED, reason_code="storage_missing", actor="planner")
    assert repos.requests.get(rid)["state"] == "Rejected"
    assert len(db.query("SELECT request_id FROM results WHERE request_id = :r",
                        {"r": rid})) == 1
