from dms.repositories import Repositories
from dms.batch_orchestrator import BatchOrchestrator
from dms.domain import RequestState

class _S:  # 최소 settings 더미
    preview_ttl_seconds = 900

def _orch(db):
    return BatchOrchestrator(Repositories(db), settings=_S())

def test_scan_throttles_materialize(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"cephfs-dms","target":f"a{i}"} for i in range(5)], status="Running")
    _orch(db).run_once()
    mats = [it for it in repos.batches.list_items(bid) if it["status"]=="Materialized"]
    assert len(mats)==2                      # 상한 2만 materialize
    # 각 materialize된 자식은 실제 Pending 요청
    for it in mats:
        assert repos.requests.get(it["request_id"])["state"]=="Pending"
        assert repos.requests.get(it["request_id"])["batch_id"]==bid

def test_scan_aggregates_and_completes(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=5, options={}, note=None,
        items=[{"storage":"cephfs-dms","target":"a"},{"storage":"cephfs-dms","target":"b"}],
        status="Running")
    _orch(db).run_once()                     # 둘 다 materialize
    items = repos.batches.list_items(bid)
    # 자식 완료를 시뮬레이션: 하나 Succeeded, 하나 Failed
    repos.requests.set_state(items[0]["request_id"], RequestState.SUCCEEDED, actor="t")
    repos.requests.set_state(items[1]["request_id"], RequestState.FAILED, actor="t")
    _orch(db).run_once()                     # 집계 + Completed
    b = repos.batches.get(bid)
    assert b["status"]=="Completed" and b["succeeded_count"]==1 and b["failed_count"]==1
    sts = sorted(it["status"] for it in repos.batches.list_items(bid))
    assert sts==["Failed","Succeeded"]
