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
