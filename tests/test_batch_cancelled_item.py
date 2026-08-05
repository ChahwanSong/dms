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
    assert by_target["a"]["reason_code"] == RequestState.CANCELLED.value
    assert by_target["b"]["status"] == "Rejected"
    assert by_target["b"]["reason_code"] == RequestState.REJECTED.value
    assert by_target["c"]["status"] == "Failed"
    assert by_target["c"]["reason_code"] == RequestState.FAILED.value


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
