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
