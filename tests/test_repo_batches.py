from dms.repositories import Repositories

def test_create_and_get(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note="n",
        items=[{"storage":"s1","target":"a"},{"storage":"s1","target":"b"}], status="Running")
    b = repos.batches.get(bid)
    assert b["operation"]=="scan" and b["status"]=="Running" and b["item_count"]==2
    items = repos.batches.list_items(bid)
    assert [it["seq"] for it in items]==[0,1]
    assert all(it["status"]=="Queued" for it in items)
    assert items[0]["payload"]=={"storage":"s1","target":"a"}

def test_materialize_and_counts(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"s1","target":"a"}], status="Running")
    repos.batches.set_item_materialized(bid, 0, "req-1")
    assert repos.batches.list_items(bid)[0]["request_id"]=="req-1"
    repos.batches.set_item_status(bid, 0, "Succeeded")
    repos.batches.bump_counts(bid, succeeded=1)
    assert repos.batches.get(bid)["succeeded_count"]==1

def test_reset_failed(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"s1","target":"a"}], status="Completed")
    repos.batches.set_item_status(bid, 0, "Failed", reason_code="x")
    repos.batches.bump_counts(bid, failed=1)
    n = repos.batches.reset_failed_items(bid)
    assert n==1 and repos.batches.list_items(bid)[0]["status"]=="Queued"
    assert repos.batches.get(bid)["failed_count"]==0

def test_active_filter(db):
    repos = Repositories(db)
    a = repos.batches.create(operation="scan", requester_id="x", actor="x", max_concurrency=1,
        options={}, note=None, items=[{"storage":"s","target":"a"}], status="Running")
    repos.batches.create(operation="scan", requester_id="x", actor="x", max_concurrency=1,
        options={}, note=None, items=[{"storage":"s","target":"b"}], status="Completed")
    assert [b["batch_id"] for b in repos.batches.list_active()]==[a]

def test_requests_create_with_batch_id(db):
    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="admin", actor="admin",
        resource_key="k", payload={"storage":"s","target":"a"}, priority="mid", batch_id="b1")
    assert repos.requests.get(rid)["batch_id"]=="b1"
