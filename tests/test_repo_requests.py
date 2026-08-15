from dms.domain import RequestState
from dms.repositories.requests import RequestsRepository


def _create(repo, key="data.scan:s1:a:ff"):
    return repo.create(operation="scan", requester_id="alice", actor="alice",
                       resource_key=key, payload={"target": "a"}, priority="mid")


def test_create_and_get(db):
    repo = RequestsRepository(db)
    rid = _create(repo)
    row = repo.get(rid)
    assert row["state"] == "Pending"
    assert row["payload"] == {"target": "a"}
    assert row["commit_order"] == 1
    assert _create(repo, key="k2") != rid
    assert repo.get("nope") is None


def test_transitions_recorded(db):
    repo = RequestsRepository(db)
    rid = _create(repo)
    repo.set_state(rid, RequestState.PLANNED, actor="planner")
    repo.set_state(rid, RequestState.REJECTED, reason_code="storage_missing", actor="planner")
    ts = repo.transitions(rid)
    assert [(t["from_state"], t["to_state"]) for t in ts] == [
        (None, "Pending"), ("Pending", "Planned"), ("Planned", "Rejected")]
    assert ts[2]["reason_code"] == "storage_missing"


def test_find_active_excludes_terminal(db):
    repo = RequestsRepository(db)
    rid = _create(repo, key="dup")
    assert repo.find_active("dup")["request_id"] == rid
    repo.set_state(rid, RequestState.CANCELLED, actor="admin")
    assert repo.find_active("dup") is None


def test_record_result_and_list(db):
    repo = RequestsRepository(db)
    rid = _create(repo)
    repo.set_state(rid, RequestState.FAILED, reason_code="x", actor="stepper")
    repo.record_result(rid, RequestState.FAILED, reason_code="x", message="boom",
                       summary={"n": 1})
    assert repo.list(requester_id="alice")[0]["request_id"] == rid
    assert repo.list(requester_id="bob") == []


def test_result_returns_the_terminal_row_with_the_summary_parsed(db):
    # 요청 상세가 「사유」를 그리려면 results 행을 읽을 수단이 있어야 한다 — 지금까지
    # 이 테이블을 읽는 코드는 배치 items 조인(SQL)뿐이라 요청 단건 조회가 없었다.
    repo = RequestsRepository(db)
    rid = _create(repo)
    repo.set_state(rid, RequestState.FAILED, reason_code="execution_failed", actor="stepper")
    repo.record_result(rid, RequestState.FAILED, reason_code="execution_failed",
                       message="boom", summary={"files": 0})
    row = repo.result(rid)
    assert row["terminal_state"] == "Failed"
    assert row["reason_code"] == "execution_failed"
    assert row["message"] == "boom"
    # summary 는 TEXT(JSON) 컬럼이다 — get()의 payload 관례대로 파싱해서 돌려준다.
    assert row["summary"] == {"files": 0}
    assert row["completed_at"]


def test_result_is_none_when_the_request_has_not_finished(db):
    # None = "결과 행 없음"이지 "사유 없음"이 아니다(null≠0 규약의 같은 결) —
    # 호출자가 비종단과 결손을 구분할 수 있어야 한다.
    repo = RequestsRepository(db)
    assert repo.result(_create(repo)) is None
    assert repo.result("nope") is None


def test_last_reason_code_returns_the_terminal_transitions_reason(db):
    repo = RequestsRepository(db)
    rid = _create(repo)
    repo.set_state(rid, RequestState.PLANNED, actor="planner")
    repo.set_state(rid, RequestState.REJECTED, reason_code="storage_missing", actor="planner")
    assert repo.last_reason_code(rid) == "storage_missing"


def test_last_reason_code_is_none_when_the_terminal_transition_has_no_reason(db):
    # 사유 없이 종단화된 경우(예: 취소)는 NULL을 돌려줘야 한다 — 상태값을 사유인 양
    # 중복 표시하느니 화면을 비우는 편이 낫다는 설계 결정.
    repo = RequestsRepository(db)
    rid = _create(repo)
    repo.set_state(rid, RequestState.CANCELLED, actor="admin")
    assert repo.last_reason_code(rid) is None


def test_last_reason_code_does_not_resurrect_a_stale_reason_from_an_earlier_transition(db):
    # 중간 전이(Planned)에는 사유가 있고 마지막 전이(Cancelled)에는 없다 — 마지막
    # 전이가 진짜로 사유 없이 끝났으면 그보다 오래된 사유를 잘못 집어오면 안 된다.
    repo = RequestsRepository(db)
    rid = _create(repo)
    repo.set_state(rid, RequestState.PLANNED, reason_code="replanned_due_to_capacity",
                   actor="planner")
    repo.set_state(rid, RequestState.CANCELLED, actor="admin")
    assert repo.last_reason_code(rid) is None


def test_set_state_from_state_is_atomic(db):
    import threading
    repo = RequestsRepository(db)
    rid = _create(repo)
    states = [RequestState.PLANNED, RequestState.RUNNING]

    def advance(s):
        repo.set_state(rid, s, actor="t")

    threads = [threading.Thread(target=advance, args=(s,)) for s in states]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ts = repo.transitions(rid)
    # 어떤 순서로 실행됐든 전이 체인은 이어져야 한다: from[i] == to[i-1]
    for prev, cur in zip(ts, ts[1:]):
        assert cur["from_state"] == prev["to_state"]
