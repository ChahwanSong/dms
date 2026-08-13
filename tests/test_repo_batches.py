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

def test_create_stores_priority_and_node_count(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"s1","target":"a"}], status="Running",
        priority="high", node_count=4)
    b = repos.batches.get(bid)
    assert b["priority"] == "high" and b["node_count"] == 4

def test_create_defaults_priority_node_count_to_null(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"s1","target":"a"}], status="Running")
    b = repos.batches.get(bid)
    # null(모름) ≠ 0 — 미지정은 NULL(정책 기본)이어야 한다
    assert b["priority"] is None
    assert b["node_count"] is None

def test_reset_all_items(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"s1","target":t} for t in ("a","b","c","d")],
        status="Completed")
    repos.batches.set_item_materialized(bid, 0, "req-0")
    repos.batches.set_item_status(bid, 0, "Succeeded")
    repos.batches.set_item_materialized(bid, 1, "req-1")
    repos.batches.set_item_status(bid, 1, "Failed", reason_code="x")
    repos.batches.set_item_status(bid, 2, "Cancelled", reason_code="cancelled_by_user")
    repos.batches.set_item_materialized(bid, 3, "req-3")  # 비종단(Materialized)
    repos.batches.bump_counts(bid, succeeded=1, failed=1)
    n = repos.batches.reset_all_items(bid)
    assert n == 3  # 종단 3건만 리셋, Materialized 는 무접촉
    items = repos.batches.list_items(bid)
    for it in items[:3]:
        assert it["status"] == "Queued"
        assert it["request_id"] is None and it["reason_code"] is None
    assert items[3]["status"] == "Materialized" and items[3]["request_id"] == "req-3"
    b = repos.batches.get(bid)
    # 감산이 아니라 0 리셋 — 전체 재시작이라 절대값이 진실
    assert b["succeeded_count"] == 0 and b["failed_count"] == 0

# --- 노드당 프로세스 수 override: node_count 저장 관례의 미러 ---

def test_create_stores_procs_per_node(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"s1","target":"a"}], status="Running",
        procs_per_node=4)
    assert repos.batches.get(bid)["procs_per_node"] == 4

def test_create_defaults_procs_per_node_to_null(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"s1","target":"a"}], status="Running")
    # null(모름) ≠ 0 — 미지정은 NULL(정책 기본)이어야 한다
    assert repos.batches.get(bid)["procs_per_node"] is None

def test_requests_create_with_batch_id(db):
    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="admin", actor="admin",
        resource_key="k", payload={"storage":"s","target":"a"}, priority="mid", batch_id="b1")
    assert repos.requests.get(rid)["batch_id"]=="b1"
