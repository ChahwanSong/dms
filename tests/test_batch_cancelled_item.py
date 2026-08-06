"""배치 item이 Cancelled로 끝난 자식은 성공도 실패도 아니다 — bump_counts로
failed 카운터를 올리지 않는다. Rejected/Failed는 기존대로 failed 카운터를 올린다.
카운터를 올리지 않아도 배치 완료 판정(item 상태 terminal == total)은 그대로 동작해
배치가 Completed에 도달해야 한다."""
from dms.repositories import Repositories
from dms.batch_orchestrator import BatchOrchestrator
from dms.domain import RequestState


class _S:  # 최소 settings 더미
    preview_ttl_seconds = 900


def _orch(db):
    return BatchOrchestrator(Repositories(db), settings=_S())


def _batch_with_three_children(db):
    repos = Repositories(db)
    bid = repos.batches.create(
        operation="scan", requester_id="admin", actor="admin",
        max_concurrency=5, options={}, note=None,
        items=[{"storage": "cephfs-dms", "target": "a"},
               {"storage": "cephfs-dms", "target": "b"},
               {"storage": "cephfs-dms", "target": "c"}],
        status="Running")
    _orch(db).run_once()  # 셋 다 materialize
    return repos, bid


def test_batch_item_carries_the_requests_real_reason_not_its_state(db):
    # 지금은 reason_code 에 "Rejected" 같은 상태값이 그대로 들어가 바로 옆 StatusPill과
    # 완전히 중복된다. 운영자가 화면에서 알아야 하는 것은 "왜" 거절됐는가다.
    repos = Repositories(db)
    bid = repos.batches.create(
        operation="scan", requester_id="admin", actor="admin",
        max_concurrency=5, options={}, note=None,
        items=[{"storage": "cephfs-dms", "target": "a"}],
        status="Running")
    _orch(db).run_once()  # materialize
    req_id = repos.batches.list_items(bid)[0]["request_id"]
    repos.requests.set_state(req_id, RequestState.REJECTED,
                             reason_code="identity_denied", actor="planner")

    _orch(db).run_once()  # 집계 + Completed

    item = repos.batches.list_items(bid)[0]
    assert item["status"] == "Rejected"
    assert item["reason_code"] == "identity_denied"  # 상태값이 아니라 진짜 사유


def test_cancelled_child_becomes_cancelled_item_without_bumping_failed(db):
    repos, bid = _batch_with_three_children(db)
    items = repos.batches.list_items(bid)
    repos.requests.set_state(items[0]["request_id"], RequestState.CANCELLED, actor="t")
    repos.requests.set_state(items[1]["request_id"], RequestState.REJECTED, actor="t")
    repos.requests.set_state(items[2]["request_id"], RequestState.FAILED, actor="t")

    _orch(db).run_once()  # 집계 + Completed

    b = repos.batches.get(bid)
    assert b["status"] == "Completed"
    assert b["succeeded_count"] == 0
    # Cancelled는 세지 않는다: Rejected(1) + Failed(1) = 2
    assert b["failed_count"] == 2

    by_target = {}
    for it in repos.batches.list_items(bid):
        by_target[it["payload"]["target"]] = it
    assert by_target["a"]["status"] == "Cancelled"
    # 위 set_state 호출들은 reason_code 를 주지 않았다 — 진짜 사유가 없으므로 NULL이어야
    # 한다(상태값을 사유인 양 중복 표시하지 않는다는 설계 결정).
    assert by_target["a"]["reason_code"] is None
    assert by_target["b"]["status"] == "Rejected"
    assert by_target["b"]["reason_code"] is None
    assert by_target["c"]["status"] == "Failed"
    assert by_target["c"]["reason_code"] is None


def test_batch_reaches_completed_even_when_all_children_cancelled(db):
    repos = Repositories(db)
    bid = repos.batches.create(
        operation="scan", requester_id="admin", actor="admin",
        max_concurrency=5, options={}, note=None,
        items=[{"storage": "cephfs-dms", "target": "a"},
               {"storage": "cephfs-dms", "target": "b"}],
        status="Running")
    _orch(db).run_once()
    items = repos.batches.list_items(bid)
    for it in items:
        repos.requests.set_state(it["request_id"], RequestState.CANCELLED, actor="t")

    _orch(db).run_once()

    b = repos.batches.get(bid)
    # 카운터를 하나도 올리지 않아도(둘 다 0) item 상태 기준 terminal == total 이므로 Completed.
    assert b["status"] == "Completed"
    assert b["succeeded_count"] == 0
    assert b["failed_count"] == 0
    sts = sorted(it["status"] for it in repos.batches.list_items(bid))
    assert sts == ["Cancelled", "Cancelled"]
