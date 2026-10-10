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


# --- 배치 삭제: 배치 행 + 항목 행만, 자식은 보존 ---

def test_delete_batch_removes_batch_and_items(db):
    repos = Repositories(db)
    bid = _edit_batch(repos, n=2, status="Completed")
    repos.batches.delete(bid)
    assert repos.batches.get(bid) is None
    assert repos.batches.list_items(bid) == []


def test_delete_batch_preserves_child_requests(db):
    repos = Repositories(db)
    bid = _edit_batch(repos, n=1, status="Completed")
    rid = repos.requests.create(operation="scan", requester_id="admin", actor="admin",
        resource_key="k", payload={"storage": "s1", "target": "t0"}, priority="mid",
        batch_id=bid)
    repos.batches.set_item_materialized(bid, 0, rid)
    repos.batches.delete(bid)
    # 자식 요청은 감사 이력 — batch_id 는 역사적 표식으로 남는다(역참조 404 수용)
    req = repos.requests.get(rid)
    assert req is not None and req["batch_id"] == bid


# --- 선택 재실행: 호출자가 준 seq 목록 중 **종단 항목만** Queued 로 되돌린다 ---

def test_reset_items_to_queued_only_terminal_and_recounts(db):
    repos = Repositories(db)
    bid = _edit_batch(repos, n=4, status="Running")
    repos.batches.set_item_materialized(bid, 0, "req-0")
    repos.batches.set_item_status(bid, 0, "Succeeded")
    repos.batches.set_item_materialized(bid, 1, "req-1")
    repos.batches.set_item_status(bid, 1, "Failed", reason_code="x")
    repos.batches.set_item_materialized(bid, 2, "req-2")          # 비종단
    repos.batches.bump_counts(bid, succeeded=1, failed=1)
    # seq 3 은 Queued(비종단), seq 2 는 Materialized(비종단) — 둘 다 걸러야 한다
    done = repos.batches.reset_items_to_queued(bid, [0, 1, 2, 3])
    assert done == [0, 1]
    items = repos.batches.list_items(bid)
    for it in items[:2]:
        assert it["status"] == "Queued"
        assert it["request_id"] is None and it["reason_code"] is None
    assert items[2]["status"] == "Materialized" and items[2]["request_id"] == "req-2"
    b = repos.batches.get(bid)
    # 카운터는 감산 분기 복제가 아니라 절대값 재계산(_recount) — 행이 진실이다
    assert b["succeeded_count"] == 0 and b["failed_count"] == 0
    assert b["item_count"] == 4


def test_reset_items_to_queued_ignores_missing_seq_and_leaves_counts(db):
    repos = Repositories(db)
    bid = _edit_batch(repos, n=1, status="Completed")
    repos.batches.set_item_status(bid, 0, "Succeeded")
    repos.batches.bump_counts(bid, succeeded=1)
    assert repos.batches.reset_items_to_queued(bid, [99]) == []
    # 아무것도 안 되돌렸으면 카운터도 무접촉 — 없는 seq 가 카운터를 흔들면 안 된다
    assert repos.batches.get(bid)["succeeded_count"] == 1


def test_reset_items_to_queued_empty_list_is_noop(db):
    repos = Repositories(db)
    bid = _edit_batch(repos, n=1, status="Completed")
    repos.batches.set_item_status(bid, 0, "Cancelled")
    assert repos.batches.reset_items_to_queued(bid, []) == []
    assert repos.batches.get_item(bid, 0)["status"] == "Cancelled"


def test_requests_create_with_batch_id(db):
    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="admin", actor="admin",
        resource_key="k", payload={"storage":"s","target":"a"}, priority="mid", batch_id="b1")
    assert repos.requests.get(rid)["batch_id"]=="b1"


# --- 배치 요약(list_summaries, 2026-10-10): 작업 목록의 「배치」 열·배치 단위 삭제의 자식 수 ---

def _insert_child(db, bid, *, state="Succeeded", order, job=None):
    """batch_id 를 가진 요청 1개(+ 선택: 잡 1개, (operation, state, target)) -- 직접 INSERT."""
    import uuid
    rid = uuid.uuid4().hex
    db.execute("""INSERT INTO requests (request_id, commit_order, operation, requester_id, actor, resource_key,
                      payload, state, created_at, updated_at, batch_id)
                  VALUES (:r, :o, 'scan', 'a', 'a', 'k', '{}', :s, 'T', 'T', :b)""",
               {"r": rid, "o": order, "s": state, "b": bid})
    if job is not None:
        op, jstate, target = job
        db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, options, priority, state, storage_name,
                          target, created_at, updated_at)
                      VALUES (:j, :r, :op, '{}', 'mid', :s, 's1', :t, 'T', 'T')""",
                   {"j": uuid.uuid4().hex, "r": rid, "op": op, "s": jstate, "t": target})
    return rid


def test_list_summaries_counts_children_live_and_successful_scans(db):
    repos = Repositories(db)
    named = repos.batches.create(operation="scan", requester_id="admin", actor="admin", max_concurrency=2,
                                 options={}, note=None, items=[{"storage": "s1", "target": "a"}],
                                 status="Running", name="성장")
    unnamed = _edit_batch(repos, n=1, status="Completed")
    # named: 재실행 이력 포함 자식 4개 -- 성공 scan 2(그중 하나는 요청 Cancelled · 잡 Succeeded 의 취소 경합),
    # 실패 scan 1, 아직 Pending(잡 없음) 1.
    _insert_child(db, named, order=1, job=("scan", "Succeeded", "a"))
    _insert_child(db, named, state="Cancelled", order=2, job=("scan", "Succeeded", "a"))
    _insert_child(db, named, state="Failed", order=3, job=("scan", "Failed", "a"))
    _insert_child(db, named, state="Pending", order=4)
    # unnamed: 성공 scan 잡이지만 target 이 없다(사용량 분석 조건 밖) -- 지점이 아니다.
    _insert_child(db, unnamed, order=5, job=("scan", "Succeeded", None))
    s = repos.batches.list_summaries([named, unnamed, named], cap=1000)
    assert s[named] == {"batch_exists": True, "batch_name": "성장", "batch_status": "Running",
                        "batch_request_count": 4, "batch_request_count_capped": False,
                        "batch_live_request_count": 1, "batch_succeeded_scan_count": 2,
                        "batch_item_count": 1}
    assert s[unnamed] == {"batch_exists": True, "batch_name": None, "batch_status": "Completed",
                          "batch_request_count": 1, "batch_request_count_capped": False,
                          "batch_live_request_count": 0,
                          "batch_succeeded_scan_count": 0, "batch_item_count": 1}


def test_list_summaries_dangling_unknown_state_and_empty_id(db):
    repos = Repositories(db)
    gone = "d" * 32                                         # 배치 기록만 지워진 묶음
    _insert_child(db, gone, order=1, job=("scan", "Succeeded", "a"))
    _insert_child(db, gone, state="Weird", order=2)         # 모르는 상태 = 살아 있다(fail-closed)
    _insert_child(db, "", order=3)                          # 빈 문자열 batch_id 도 열쇠다
    blank = _edit_batch(repos, n=1, status="Completed")
    db.execute("UPDATE batches SET name = '  ' WHERE batch_id = :b", {"b": blank})     # 변조된 빈 이름
    s = repos.batches.list_summaries([gone, "", blank, "never"], cap=1000)
    assert s[gone] == {"batch_exists": False, "batch_name": None, "batch_status": None,
                       "batch_request_count": 2, "batch_request_count_capped": False,
                       "batch_live_request_count": 1, "batch_succeeded_scan_count": 1,
                       "batch_item_count": None}                     # 배치 행이 없다 -- 항목 수는 모름
    assert s[""]["batch_exists"] is False and s[""]["batch_request_count"] == 1
    assert s[blank]["batch_name"] is None
    assert s["never"]["batch_request_count"] == 0 and s["never"]["batch_exists"] is False
    assert repos.batches.list_summaries([], cap=1000) == {}


def test_list_summaries_carries_the_item_count_for_the_delete_cap(db):
    # 배치 단위 삭제는 항목 수에도 상한이 있다(MAX_BATCH_DELETE_ITEMS) -- 요약이 항목 수를 실어야 화면이 「고를 수 없음」을
    # 미리 보인다(2026-10-11 검증 지적: 자식 1개·항목 1만+ 의 일찍 취소한 배치가 선택 가능으로 보였다). 값은
    # batches.item_count(_recount 가 유지) -- 항목을 지우면 따라 줄고, 0 은 정상값이다. 변조된 값은 None(모름).
    repos = Repositories(db)
    bid = _edit_batch(repos, n=3, status="Cancelled")
    _insert_child(db, bid, order=1)
    assert repos.batches.list_summaries([bid], cap=1000)[bid]["batch_item_count"] == 3
    for seq in (0, 1, 2):
        repos.batches.delete_item(bid, seq, only_queued=False)
    assert repos.batches.list_summaries([bid], cap=1000)[bid]["batch_item_count"] == 0
    # PG 는 INTEGER 열에 글자·소수를 넣지 못한다(음수만 변조 가능) -- sqlite 는 형 친화성이라 무엇이든 들어간다.
    for bad in ((-1,) if db.dialect == "postgresql" else (-1, "many", 1.5)):
        db.execute("UPDATE batches SET item_count = :v WHERE batch_id = :b", {"v": bad, "b": bid})
        assert repos.batches.list_summaries([bid], cap=1000)[bid]["batch_item_count"] is None, bad


def test_list_summaries_crosses_the_chunk_boundary(db):
    repos = Repositories(db)
    ids = [f"{i:032x}" for i in range(501)]                 # 묶음 500 + 1
    with db.transaction():
        for n, bid in enumerate((ids[0], ids[499], ids[500])):
            _insert_child(db, bid, order=n + 1)
    s = repos.batches.list_summaries(ids, cap=1000)
    assert len(s) == 501
    assert [s[b]["batch_request_count"] for b in (ids[0], ids[499], ids[500], ids[250])] == [1, 1, 1, 0]


def test_list_summaries_stops_counting_past_the_cap(db):
    # 폴링마다 큰 배치의 자식 전부를 세고 그 전부로 성공 scan 세미조인을 돌지 않는다(2026-10-11 검증 지적 -- 자식 2만에서
    # PG 가 data_jobs 전체 Seq Scan 으로 계획을 바꿨다). cap 을 넘는 배치는 cap + 1 에서 멈추고 capped -- 살아 있는 자식·
    # 성공 scan 은 세지 않았으므로 None(모름 ≠ 0). cap 과 같은 수는 정확한 값이다.
    repos = Repositories(db)
    big, exact = "b" * 32, "e" * 32
    order = iter(range(1, 100))
    with db.transaction():
        for _ in range(5):
            _insert_child(db, big, order=next(order), job=("scan", "Succeeded", "a"))
        _insert_child(db, big, state="Pending", order=next(order))
        for _ in range(3):
            _insert_child(db, exact, order=next(order), job=("scan", "Succeeded", "a"))
    s = repos.batches.list_summaries([big, exact], cap=3)
    assert {k: s[big][k] for k in ("batch_request_count", "batch_request_count_capped", "batch_live_request_count",
                                   "batch_succeeded_scan_count")} == {
        "batch_request_count": 4, "batch_request_count_capped": True, "batch_live_request_count": None,
        "batch_succeeded_scan_count": None}
    assert {k: s[exact][k] for k in ("batch_request_count", "batch_request_count_capped", "batch_live_request_count",
                                     "batch_succeeded_scan_count")} == {
        "batch_request_count": 3, "batch_request_count_capped": False, "batch_live_request_count": 0,
        "batch_succeeded_scan_count": 3}


def test_list_summaries_reads_each_batch_through_a_bounded_subquery(db):
    # 문장 모양 고정: 배치마다 `LIMIT :stop` 갈래(UNION ALL)로 세고, 성공 scan 판정은 그 갈래 안의 자식별 EXISTS 다 --
    # IN (자식 전부) 세미조인으로 되돌아가면 큰 배치에서 data_jobs 해시 조인이 돌아온다.
    repos = Repositories(db)
    bid = "c" * 32
    _insert_child(db, bid, order=1, job=("scan", "Succeeded", "a"))
    seen = []
    real = db.query

    def spy(sql, params=None):
        seen.append(sql)
        return real(sql, params)
    db.query = spy
    try:
        repos.batches.list_summaries([bid, "d" * 32], cap=1000)
    finally:
        db.query = real
    counting = [q for q in seen if "COUNT(*)" in q]
    assert len(counting) == 2
    assert all(q.count("LIMIT :stop") == q.count("SELECT batch_id, COUNT(*)") for q in counting)
    # 갈래의 자식 읽기는 범위 표기 + 인덱스 순서(requests.batch_match) -- `batch_id = :k LIMIT` 면 PG 가 큰 배치에서
    # Seq Scan + LIMIT 를 골라 그 배치의 물리 위치 앞의 행 전부를 읽었다(2026-10-11 검증 지적).
    assert all(q.count("ORDER BY batch_id, commit_order LIMIT :stop") == q.count("LIMIT :stop") for q in counting)
    assert all("batch_id = :" not in q for q in counting)
    assert "EXISTS (SELECT 1 FROM data_jobs" in counting[1] and "request_id IN (SELECT" not in counting[1]


def test_add_item_to_a_vanished_batch_writes_nothing(db):
    # 라우트가 배치를 읽은 뒤 INSERT 전에 배치가 지워진 경합 -- 배치 없는 고아 항목을 만들지 않는다(2026-10-10).
    repos = Repositories(db)
    assert repos.batches.add_item("f" * 32, {"storage": "s1", "target": "a"}) is None
    assert db.query_one("SELECT COUNT(*) AS n FROM batch_items WHERE batch_id = :b", {"b": "f" * 32})["n"] == 0


def test_replace_items_on_a_vanished_or_reactivated_batch_writes_nothing(db):
    # 라우트가 배치를 읽고 CSV 를 검증하는 동안 배치가 지워졌거나(None) 다시 돌기 시작했으면(False) 아무것도 쓰지 않는다
    # -- 배치 없는 고아 항목·활성 배치의 전량 교체 금지(2026-10-10 검증 지적, add_item 과 같은 경합).
    repos = Repositories(db)
    assert repos.batches.replace_items("f" * 32, [{"storage": "s1", "target": "a"}]) is None
    assert db.query_one("SELECT COUNT(*) AS n FROM batch_items WHERE batch_id = :b", {"b": "f" * 32})["n"] == 0
    for status in ("Running", "Previewing", "PreviewReady", "Weird"):
        bid = _edit_batch(repos, n=2, status=status)
        assert repos.batches.replace_items(bid, [{"storage": "s1", "target": "new"}]) is False, status
        assert [it["payload"]["target"] for it in repos.batches.list_items(bid)] == ["t0", "t1"]
    bid = _edit_batch(repos, n=2, status="Cancelled")
    assert repos.batches.replace_items(bid, [{"storage": "s1", "target": "new"}]) is True
    assert [it["payload"]["target"] for it in repos.batches.list_items(bid)] == ["new"]


class _PgSpy:
    """dialect 만 postgresql 로 보이는 sqlite 프록시 -- 문장을 기록하고 FOR UPDATE 를 떼어 실행한다."""
    dialect = "postgresql"

    def __init__(self, db):
        self._db, self.sql = db, []

    def __getattr__(self, name):
        return getattr(self._db, name)

    def query(self, sql, params=None):
        self.sql.append(sql)
        return self._db.query(sql.replace(" FOR UPDATE", ""), params)

    def query_one(self, sql, params=None):
        self.sql.append(sql)
        return self._db.query_one(sql.replace(" FOR UPDATE", ""), params)


def test_replace_items_locks_items_then_batch_on_postgres(db):
    # 잠금 순서 batch_items → batches: 오케스트레이터 _record_terminal(batch_items UPDATE → batches UPDATE, 다른
    # 프로세스)·배치 단위 삭제와 같은 방향. 배치 행을 먼저 쥐고 항목을 DELETE 하면 역순이라 교착이다.
    import re
    from dms.repositories.batches import BatchesRepository
    bid = _edit_batch(Repositories(db), n=2, status="Completed")
    spy = _PgSpy(db)
    assert BatchesRepository(spy).replace_items(bid, [{"storage": "s1", "target": "n"}]) is True
    locked = [re.search(r"FROM (\w+)", q).group(1) for q in spy.sql if q.rstrip().endswith("FOR UPDATE")]
    assert locked == ["batch_items", "batches"]
