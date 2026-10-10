"""작업 목록(GET /api/user/requests)의 배치 요약(2026-10-10) -- 「배치」 열과 배치 단위 삭제(선택·확인 창의 자식 수 =
삭제 CAS 의 expected_request_count)가 쓴다. **관리자 응답의 배치 자식 행에만** 8개 키가 붙는다. 수 자체의 세부(묶음 경계·
모르는 상태·빈 이름)는 tests/test_repo_batches.py 의 list_summaries 절이 본다."""
from dms.domain import DataJobState, RequestState

KEYS = {"batch_exists", "batch_name", "batch_status", "batch_request_count", "batch_request_count_capped",
        "batch_live_request_count", "batch_succeeded_scan_count", "batch_item_count"}


def _login(client, name, role):
    client.app.state.repos.accounts.create(name, "p", role, actor="t")
    assert client.post("/api/auth/login", json={"username": name, "password": "p"}).status_code == 200


def _child(repos, bid, *, requester="opadm", target="t", finish=True):
    rid = repos.requests.create(operation="scan", requester_id=requester, actor=requester,
                                resource_key=f"k:{target}", payload={"storage": "s1", "target": target},
                                priority="mid", batch_id=bid)
    if finish:
        repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
        plan_id = repos.data_jobs.create_plan(rid, actor="planner")
        jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid", storage_name="s1",
                                         target=target, options={}, tool="dscan", precondition={}, actor="planner",
                                         worker_pool={})
        repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
        repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    return rid


def _batch(repos, *, name="배치", status="Completed"):
    return repos.batches.create(operation="scan", requester_id="opadm", actor="opadm", max_concurrency=1,
                                options={}, note=None, items=[{"storage": "s1", "target": "t"}], status=status,
                                name=name, auth_method="session")


def _rows(client):
    r = client.get("/api/user/requests?limit=200")
    assert r.status_code == 200, r.text
    return {row["request_id"]: row for row in r.json()}


def test_admin_rows_carry_the_batch_summary(client):
    repos = client.app.state.repos
    named = _batch(repos, name="성장 모니터링", status="Running")
    done = [_child(repos, named, target=f"t{i}") for i in range(2)]   # 재실행 이력 포함 성공 scan 2
    live = _child(repos, named, target="t9", finish=False)             # 아직 Pending
    unnamed = _batch(repos, name=None)
    other = _child(repos, unnamed)
    single = _child(repos, None)                                       # 단건
    _login(client, "opadm", "admin")
    rows = _rows(client)
    for rid in done + [live]:
        assert {k: rows[rid][k] for k in KEYS} == {
            "batch_exists": True, "batch_name": "성장 모니터링", "batch_status": "Running",
            "batch_request_count": 3, "batch_request_count_capped": False, "batch_live_request_count": 1,
            "batch_succeeded_scan_count": 2, "batch_item_count": 1}
    assert {k: rows[other][k] for k in KEYS} == {
        "batch_exists": True, "batch_name": None, "batch_status": "Completed",
        "batch_request_count": 1, "batch_request_count_capped": False, "batch_live_request_count": 0,
        "batch_succeeded_scan_count": 1, "batch_item_count": 1}
    assert not KEYS & set(rows[single])                                # 단건 행엔 키가 없다


def test_dangling_and_empty_batch_id_rows_are_summarised(client, db):
    repos = client.app.state.repos
    bid = _batch(repos)
    rid = _child(repos, bid)
    repos.batches.delete(bid)                                          # 배치 기록만 지움 -- 자식은 남는다
    odd = _child(repos, None, target="odd")
    db.execute("UPDATE requests SET batch_id = '' WHERE request_id = :r", {"r": odd})   # 형식 이상값도 열쇠다
    _login(client, "opadm", "admin")
    rows = _rows(client)
    assert {k: rows[rid][k] for k in KEYS} == {
        "batch_exists": False, "batch_name": None, "batch_status": None,
        "batch_request_count": 1, "batch_request_count_capped": False, "batch_live_request_count": 0,
        "batch_succeeded_scan_count": 1, "batch_item_count": None}               # 배치 행이 없다 -- 항목 수는 모름
    assert rows[odd]["batch_id"] == "" and rows[odd]["batch_exists"] is False
    assert rows[odd]["batch_request_count"] == 1


def test_non_admin_rows_have_no_batch_summary(client):
    repos = client.app.state.repos
    bid = _batch(repos)
    mine = _child(repos, bid, requester="alice")
    _login(client, "alice", "user")
    rows = _rows(client)
    assert rows[mine]["batch_id"] == bid and not KEYS & set(rows[mine])


def test_batch_id_filter_lists_only_that_batchs_jobs(client):
    # 배치 상세의 「전체 작업에서 이 배치의 작업 보기」·배치 기록만 지워진 묶음 칸이 쓴다(2026-10-11 검증 지적 -- 오래된
    # 배치의 작업은 무한 스크롤 수십 쪽 아래라 배치 단위 삭제를 시작할 행을 찾을 수 없었다). 커서와 함께 동작하고,
    # 비운영자는 여전히 자기 작업만 본다(좁히기만 한다).
    repos = client.app.state.repos
    bid = _batch(repos)
    kids = [_child(repos, bid, target=f"t{i}") for i in range(3)]
    other = _batch(repos, name="다른")
    _child(repos, other)
    _child(repos, None)
    alices = _child(repos, bid, requester="alice", target="ta")
    _login(client, "opadm", "admin")
    r = client.get(f"/api/user/requests?batch_id={bid}&limit=2")
    assert r.status_code == 200
    page1 = r.json()
    assert [row["batch_id"] for row in page1] == [bid, bid]
    page2 = client.get(f"/api/user/requests?batch_id={bid}&limit=2&before={page1[-1]['commit_order']}").json()
    assert {row["request_id"] for row in page1 + page2} == set(kids + [alices])
    assert client.get("/api/user/requests?batch_id=" + "0" * 32).json() == []
    assert len(client.get("/api/user/requests?batch_id=").json()) == 6        # 빈 값 = 필터 없음
    client.post("/api/auth/logout")
    _login(client, "alice", "user")
    assert [row["request_id"] for row in client.get(f"/api/user/requests?batch_id={bid}").json()] == [alices]


def test_summary_stops_counting_at_the_batch_delete_cap(client, monkeypatch):
    # 라우트는 배치 단위 삭제의 자식 상한(MAX_BATCH_DELETE_CHILDREN)을 cap 으로 넘긴다 -- 넘는 배치는 상한 + 1 에서
    # 세기를 멈추고 capped(화면은 「1000개 초과」, 선택 불가). 상한을 작게 바꿔 확인한다(2026-10-11 검증 지적).
    import dms.api.routes_requests as rr
    monkeypatch.setattr(rr, "MAX_BATCH_DELETE_CHILDREN", 2)
    repos = client.app.state.repos
    bid = _batch(repos)
    kids = [_child(repos, bid, target=f"t{i}") for i in range(3)]
    _login(client, "opadm", "admin")
    rows = _rows(client)
    assert {k: rows[kids[0]][k] for k in ("batch_request_count", "batch_request_count_capped",
                                         "batch_live_request_count", "batch_succeeded_scan_count")} == {
        "batch_request_count": 3, "batch_request_count_capped": True, "batch_live_request_count": None,
        "batch_succeeded_scan_count": None}


def test_summary_carries_the_item_count_so_the_portal_can_lock_oversized_batches(client):
    # 자식은 적고 항목만 많은 배치(큰 CSV 를 일찍 취소)도 배치 단위 삭제 상한(항목 10000)에 걸린다 -- 요약에 항목 수가 없으면
    # 포탈이 선택 가능으로 보였다가 서버만 batch_delete_too_large 를 냈다(2026-10-11 검증 지적).
    repos = client.app.state.repos
    bid = repos.batches.create(operation="scan", requester_id="opadm", actor="opadm", max_concurrency=1, options={},
                               note=None, items=[{"storage": "s1", "target": f"t{i}"} for i in range(4)],
                               status="Cancelled", name="큰 CSV", auth_method="session")
    rid = _child(repos, bid)
    _login(client, "opadm", "admin")
    assert _rows(client)[rid]["batch_item_count"] == 4
