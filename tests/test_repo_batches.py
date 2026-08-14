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

# --- 배치 이름(name): note 저장 관례의 미러 ---

def test_create_stores_name(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"s1","target":"a"}], status="Running",
        name="8월 정기 스캔 1차")
    assert repos.batches.get(bid)["name"] == "8월 정기 스캔 1차"

def test_create_defaults_name_to_null(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"s1","target":"a"}], status="Running")
    # null = 이름 없음(미지정) — 빈 문자열로 뭉개지 않는다
    assert repos.batches.get(bid)["name"] is None

def test_update_meta_partial(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note="메모",
        items=[{"storage":"s1","target":"a"}], status="Running", name="이름")
    before = repos.batches.get(bid)["updated_at"]
    repos.batches.update_meta(bid, name="새 이름")           # note 무접촉(부분 갱신)
    b = repos.batches.get(bid)
    assert b["name"] == "새 이름" and b["note"] == "메모"
    assert b["updated_at"] >= before
    repos.batches.update_meta(bid, name=None, note=None)     # 명시적 지우기(NULL)
    b = repos.batches.get(bid)
    assert b["name"] is None and b["note"] is None

# --- 항목 수정·삭제·추가(배치 항목 편집) ---

def _edit_batch(repos, n=3, status="Running"):
    return repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage": "s1", "target": f"t{i}"} for i in range(n)], status=status)


def test_get_item_returns_row_with_parsed_payload(db):
    repos = Repositories(db)
    bid = _edit_batch(repos, n=2)
    it = repos.batches.get_item(bid, 1)
    assert it["seq"] == 1 and it["payload"] == {"storage": "s1", "target": "t1"}
    assert repos.batches.get_item(bid, 99) is None


def test_update_item_payload_resets_to_queued_and_recounts(db):
    repos = Repositories(db)
    bid = _edit_batch(repos, n=2, status="Completed")
    repos.batches.set_item_materialized(bid, 0, "req-0")
    repos.batches.set_item_status(bid, 0, "Succeeded")
    repos.batches.bump_counts(bid, succeeded=1)
    ok = repos.batches.update_item_payload(bid, 0, {"storage": "s1", "target": "new"},
                                           only_queued=False)
    assert ok is True
    it = repos.batches.get_item(bid, 0)
    # 편집 = 미실행 상태로 리셋 — 종단 상태·request_id 를 남기면 새 payload 가
    # 옛 결과를 낸 것처럼 보이는 거짓 기록이 된다
    assert it["payload"] == {"storage": "s1", "target": "new"}
    assert it["status"] == "Queued"
    assert it["request_id"] is None and it["reason_code"] is None
    b = repos.batches.get(bid)
    assert b["succeeded_count"] == 0 and b["item_count"] == 2


def test_update_item_payload_only_queued_guard_rejects_materialized(db):
    repos = Repositories(db)
    bid = _edit_batch(repos, n=1)
    repos.batches.set_item_materialized(bid, 0, "req-0")
    ok = repos.batches.update_item_payload(bid, 0, {"storage": "s1", "target": "new"},
                                           only_queued=True)
    assert ok is False
    # 가드 패배 시 무접촉 — payload·status 그대로
    it = repos.batches.get_item(bid, 0)
    assert it["payload"] == {"storage": "s1", "target": "t0"}
    assert it["status"] == "Materialized" and it["request_id"] == "req-0"


def test_delete_item_recounts_absolute(db):
    repos = Repositories(db)
    bid = _edit_batch(repos, n=3, status="Completed")
    repos.batches.set_item_status(bid, 0, "Succeeded")
    repos.batches.set_item_status(bid, 1, "Failed", reason_code="x")
    repos.batches.bump_counts(bid, succeeded=1, failed=1)
    assert repos.batches.delete_item(bid, 0, only_queued=False) is True
    b = repos.batches.get(bid)
    assert b["item_count"] == 2
    assert b["succeeded_count"] == 0 and b["failed_count"] == 1
    # seq 는 재부여하지 않는다(구멍 유지) — seq 는 식별자다
    assert [it["seq"] for it in repos.batches.list_items(bid)] == [1, 2]


def test_delete_item_only_queued_guard_rejects_materialized(db):
    repos = Repositories(db)
    bid = _edit_batch(repos, n=1)
    repos.batches.set_item_materialized(bid, 0, "req-0")
    assert repos.batches.delete_item(bid, 0, only_queued=True) is False
    assert repos.batches.get_item(bid, 0) is not None
    assert repos.batches.get(bid)["item_count"] == 1


def test_add_item_appends_max_seq_plus_one(db):
    repos = Repositories(db)
    bid = _edit_batch(repos, n=3)
    assert repos.batches.delete_item(bid, 1, only_queued=True) is True
    # MAX(seq)+1 이지 COUNT 가 아니다 — 중간 삭제 후 COUNT(=2)로 매기면 살아있는
    # seq 2 와 PK 충돌하거나 구멍(seq 1)을 재사용해 목록 순서 의미가 흐려진다
    seq = repos.batches.add_item(bid, {"storage": "s1", "target": "added"})
    assert seq == 3
    it = repos.batches.get_item(bid, 3)
    assert it["status"] == "Queued" and it["payload"]["target"] == "added"
    assert repos.batches.get(bid)["item_count"] == 3


def test_add_item_to_empty_batch_starts_at_zero(db):
    repos = Repositories(db)
    bid = _edit_batch(repos, n=1)
    assert repos.batches.delete_item(bid, 0, only_queued=True) is True
    assert repos.batches.add_item(bid, {"storage": "s1", "target": "a"}) == 0


def test_requests_create_with_batch_id(db):
    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="admin", actor="admin",
        resource_key="k", payload={"storage":"s","target":"a"}, priority="mid", batch_id="b1")
    assert repos.requests.get(rid)["batch_id"]=="b1"
