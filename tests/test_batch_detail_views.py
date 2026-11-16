"""배치 목록 「대상 스토리지」 열 + 상세 조회 방식(view=summary|items|payloads|all) -- 2026-11-16.

- 목록(BatchesRepository.list): 첫 항목(MIN(seq)) payload 에서 storage·source_storage·destination_storage 를 파이썬으로
  뽑는다(_storage_fields). 깨진 JSON·dict 아님·문자열 아님 → None, 원문 payload 는 응답에 없다.
- 상세 view 없음·all: 지금 그대로(items 포함) -- 옛 탭이 항목 없는 응답으로 「0개」 확인을 하면 root 삭제의 맹목 승인이다.
- summary: items 없이 머리 집계. executing_count·direct_tools 는 포탈 BatchDetail(executing·directNote)과 같은 규칙.
- items: 가볍게 거르고 자른 뒤 그 쪽의 seq 만 상세 조인 -- 행은 전체 조회와 같다.
- payloads: 전 항목 {seq, payload, status}.
"""
from fastapi.testclient import TestClient

from dms.batch_orchestrator import direct_tool_counts, preview_files
from dms.domain import DataJobState, RequestState
from dms.repositories import Repositories
from dms.repositories import batches as batches_mod

SYNC = {"source_storage": "s1", "source": "src/a", "destination_storage": "s2", "destination": "dst/a"}


def _admin(client):  # test_api_batches 와 같은 세션 로그인(admin)
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})


def _create(repos, op, items, *, status=None, options=None):
    default = {"scan": "Running"}.get(op, "Previewing")
    return repos.batches.create(operation=op, requester_id="admin", actor="admin", max_concurrency=2,
                                options={} if options is None else options, note=None, items=items,
                                status=status or default, auth_method="session")


def _child(repos, bid, seq, *, state=DataJobState.CONFIRM_PENDING, tool="dsync", summary=None, created_at=None):
    """항목 seq 를 materialize 하고(이미 됐으면 그 요청에) 자식 잡 하나를 만든다. created_at 을 주면 잡 행의 생성 시각을
    고정한다(「최신 잡 하나」 판정을 결정적으로)."""
    item = repos.batches.get_item(bid, seq)
    rid = item["request_id"]
    if rid is None:
        rid = repos.requests.create(operation="sync", requester_id="admin", actor="admin",
                                    resource_key=f"k-{bid}-{seq}", payload=dict(SYNC), priority="mid",
                                    batch_id=bid, auth_method="session")
        repos.batches.set_item_materialized(bid, seq, rid)
        repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan, operation="sync", priority="mid", source_storage="s1",
                                     destination_storage="s2", source="a", destination="b", options={},
                                     tool=tool, worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint=f"sha256:{jid}", expires_at="2099-01-01T00:00:00Z",
                                artifact_uri="x", summary=summary if summary is not None else {"files": 3})
    repos.data_jobs.set_job_state(jid, state, actor="stepper")
    if created_at is not None:
        repos.data_jobs._db.execute("UPDATE data_jobs SET created_at = :t WHERE job_id = :j",
                                    {"t": created_at, "j": jid})
    return rid, jid


def _set_payload_text(db, bid, seq, text):
    db.execute("UPDATE batch_items SET payload = :p WHERE batch_id = :b AND seq = :s", {"p": text, "b": bid, "s": seq})


def _spy(db):
    seen = []
    real = db.query

    def spy(sql, params=None):
        seen.append(sql)
        return real(sql, params)
    db.query = spy
    return seen, real


# --- 목록: 대상 스토리지 열 ---------------------------------------------------------------------------

def test_list_carries_storage_fields_from_the_first_item(db):
    repos = Repositories(db)
    scan = _create(repos, "scan", [{"storage": "s1", "target": "a"}])
    rm = _create(repos, "rm", [{"storage": "s3", "target": "x"}], options={"recursive": True})
    sync = _create(repos, "sync", [dict(SYNC)])
    rows = {r["batch_id"]: r for r in repos.batches.list()}
    assert {k: rows[scan][k] for k in ("storage", "source_storage", "destination_storage")} == {
        "storage": "s1", "source_storage": None, "destination_storage": None}
    assert rows[rm]["storage"] == "s3"
    assert (rows[sync]["storage"], rows[sync]["source_storage"], rows[sync]["destination_storage"]) == (None, "s1", "s2")
    # 원문 payload 는 응답에 싣지 않는다 -- 옵션 hydrate 는 그대로(test_ops_hardening_small 의 그물)
    assert all("first_item_payload" not in r and "payload" not in r for r in rows.values())
    assert rows[rm]["options"] == {"recursive": True}


def test_list_reads_the_lowest_remaining_seq_and_none_for_an_emptied_batch(db):
    repos = Repositories(db)
    holed = _create(repos, "scan", [{"storage": "s1", "target": "a"}, {"storage": "s1", "target": "b"}],
                    status="Completed")
    _set_payload_text(db, holed, 1, '{"storage": "s9", "target": "b"}')   # seq 1 만 다른 값 -- 무엇을 읽는지 가른다
    assert repos.batches.delete_item(holed, 0, only_queued=False)
    emptied = _create(repos, "scan", [{"storage": "s1", "target": "a"}], status="Completed")
    repos.batches.delete_item(emptied, 0, only_queued=False)
    rows = {r["batch_id"]: r for r in repos.batches.list()}
    assert rows[holed]["storage"] == "s9"                 # seq 0 이 지워진 구멍 -- seq 1 을 읽는다
    assert rows[emptied]["item_count"] == 0
    assert (rows[emptied]["storage"], rows[emptied]["source_storage"], rows[emptied]["destination_storage"]) == (
        None, None, None)


def test_list_tolerates_broken_or_tampered_first_payloads(db):
    repos = Repositories(db)
    cases = {"{not json": None, "[1, 2]": None, '"s1"': None, '{"storage": 7}': None,
             '{"storage": null}': None, '{"storage": ""}': ""}
    ids = {}
    for text in cases:
        bid = _create(repos, "scan", [{"storage": "s1", "target": "a"}])
        _set_payload_text(db, bid, 0, text)
        ids[bid] = text
    rows = {r["batch_id"]: r for r in repos.batches.list()}     # 행 하나 때문에 목록 전체가 500 이 되지 않는다
    for bid, text in ids.items():
        assert rows[bid]["storage"] == cases[text], text
        assert rows[bid]["source_storage"] is None and rows[bid]["destination_storage"] is None


def test_list_reads_the_first_payload_through_a_bounded_derived_table(db):
    # 문장 모양 고정: 파생 테이블이 먼저 limit 행을 고르고 그 행마다 seq 순 첫 항목만 -- `FROM batches b ... LIMIT` 로 풀면
    # sqlite 가 정렬 전에 모든 배치 행에서 서브쿼리를 돈다.
    repos = Repositories(db)
    _create(repos, "scan", [{"storage": "s1", "target": "a"}])
    seen, real = _spy(db)
    try:
        repos.batches.list(limit=7)
    finally:
        db.query = real
    [sql] = seen
    assert "FROM (SELECT * FROM batches ORDER BY created_at DESC LIMIT :n) b" in sql
    assert "ORDER BY bi.seq LIMIT 1" in sql


def test_list_route_returns_storage_fields(client):
    _admin(client)
    scan = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1, "options": {},
                                                   "items": [{"storage": "s1", "target": "a"}]}).json()["batch_id"]
    sync = client.post("/api/admin/batches", json={"operation": "sync", "max_concurrency": 1, "options": {},
                                                   "items": [dict(SYNC)]}).json()["batch_id"]
    rows = {r["batch_id"]: r for r in client.get("/api/admin/batches").json()}
    assert rows[scan]["storage"] == "s1" and rows[scan]["source_storage"] is None
    assert rows[sync]["source_storage"] == "s1" and rows[sync]["destination_storage"] == "s2"
    assert rows[sync]["storage"] is None


# --- list_items_detail 의 seq 부분 조회 ----------------------------------------------------------------

def _rich_batch(repos, n=6):
    bid = _create(repos, "sync", [dict(SYNC, destination=f"dst/{i}") for i in range(n)], status="PreviewReady")
    _child(repos, bid, 0, summary={"files": 1, "tool": "nsync"}, tool="nsync")
    _child(repos, bid, 2, state=DataJobState.EXECUTING)
    _child(repos, bid, 3, summary={"files": 0})
    repos.batches.set_item_status(bid, 4, "Rejected", reason_code="preview_expired")
    return bid


def test_list_items_detail_seq_subset_rows_equal_the_full_rows(db):
    repos = Repositories(db)
    bid = _rich_batch(repos)
    full = repos.batches.list_items_detail(bid)
    by_seq = {r["seq"]: r for r in full}
    for seqs in ([0], [3, 1], [5, 0, 2, 2], [0, 1, 2, 3, 4, 5]):
        part = repos.batches.list_items_detail(bid, seqs=seqs)
        assert part == [by_seq[s] for s in sorted(set(seqs))]          # 같은 행, seq 순, 중복 없음
    # 없는 seq(쪽을 고른 뒤 지워진 항목)는 조용히 빠지고, 빈 목록은 문장 없이 []
    assert repos.batches.list_items_detail(bid, seqs=[1, 99]) == [by_seq[1]]
    seen, real = _spy(db)
    try:
        assert repos.batches.list_items_detail(bid, seqs=[]) == []
    finally:
        db.query = real
    assert seen == []


def test_list_items_detail_chunks_long_seq_lists_in_seq_order(db, monkeypatch):
    repos = Repositories(db)
    bid = _rich_batch(repos)
    monkeypatch.setattr(batches_mod, "_SEQ_CHUNK", 2)
    seen, real = _spy(db)
    try:
        part = repos.batches.list_items_detail(bid, seqs=[5, 4, 3, 2, 1])
    finally:
        db.query = real
    assert [r["seq"] for r in part] == [1, 2, 3, 4, 5]
    assert len(seen) == 3 and all("AND bi.seq IN (" in q for q in seen)
    assert part == [r for r in repos.batches.list_items_detail(bid) if r["seq"] != 0]


def test_confirm_audit_summary_input_is_unchanged_by_the_subset_argument(db):
    # 확인 감사 요약(preview_files(list_items_detail(...)))은 seqs 없는 전체 경로 그대로다 -- 전 seq 를 넘긴 부분 조회와
    # 같은 행·같은 요약이어야 한다(두 경로가 갈라지면 화면 쪽 행과 감사 행이 다른 사실을 말한다).
    repos = Repositories(db)
    bid = _rich_batch(repos)
    full = repos.batches.list_items_detail(bid)
    every = repos.batches.list_items_detail(bid, seqs=[r["seq"] for r in full])
    assert every == full
    assert preview_files(full) == preview_files(every) == {
        "files_total": 1, "files_unknown": 0, "files_by_tool": {"nsync": 1, "dsync": 0}}


# --- 요약 재료: item_overview · count_executing_items · direct_tool_counts ------------------------------

def test_item_overview_counts_every_known_status_with_zeros_and_reads_min_seq(db):
    repos = Repositories(db)
    bid = _create(repos, "scan", [{"storage": "s1", "target": t} for t in ("a", "b", "c")], status="Completed")
    repos.batches.set_item_status(bid, 1, "Succeeded")
    repos.batches.set_item_status(bid, 2, "Failed", reason_code="x")
    db.execute("UPDATE batch_items SET status = 'Weird' WHERE batch_id = :b AND seq = 0", {"b": bid})
    ov = repos.batches.item_overview(bid)
    assert ov["item_total"] == 3
    assert ov["status_counts"] == {"Queued": 0, "Materialized": 0, "Succeeded": 1, "Failed": 1, "Rejected": 0,
                                   "Cancelled": 0, "Weird": 1}
    assert ov["first_payload"] == {"storage": "s1", "target": "a"}
    repos.batches.delete_item(bid, 0, only_queued=False)
    ov = repos.batches.item_overview(bid)
    assert ov["first_payload"] == {"storage": "s1", "target": "b"} and ov["storage"] == "s1"
    assert ov["item_total"] == 2


def test_item_overview_of_an_emptied_batch_is_zero_not_unknown(db):
    repos = Repositories(db)
    bid = _create(repos, "scan", [{"storage": "s1", "target": "a"}], status="Completed")
    repos.batches.delete_item(bid, 0, only_queued=False)
    ov = repos.batches.item_overview(bid)
    assert ov["item_total"] == 0 and set(ov["status_counts"].values()) == {0}
    assert ov["first_payload"] is None and ov["storage"] is None


def test_count_executing_items_follows_the_latest_job_like_the_portal(db):
    repos = Repositories(db)
    bid = _create(repos, "sync", [dict(SYNC, destination=f"d{i}") for i in range(4)], status="Previewing")
    _child(repos, bid, 0, state=DataJobState.EXECUTING)                                   # 센다
    # 옛 잡이 Executing 이어도 최신 잡이 ConfirmPending 이면 세지 않는다(같은 「최신 잡 하나」 -- created_at DESC)
    _child(repos, bid, 1, state=DataJobState.EXECUTING, created_at="2026-01-01T00:00:00Z")
    _child(repos, bid, 1, state=DataJobState.CONFIRM_PENDING, created_at="2026-01-02T00:00:00Z")
    _child(repos, bid, 2, state=DataJobState.EXECUTING)
    repos.batches.set_item_status(bid, 2, "Succeeded")                                   # Materialized 가 아니다
    n = repos.batches.count_executing_items(bid)
    portal = sum(1 for it in repos.batches.list_items_detail(bid)
                 if it["status"] == "Materialized" and it["job_state"] == "Executing")
    assert n == portal == 1


def test_direct_tool_counts_mirror_the_portal_direct_note_rule(db):
    repos = Repositories(db)
    bid = _create(repos, "sync", [dict(SYNC, destination=f"d{i}") for i in range(7)], status="Previewing",
                  options={"direct": True})
    _child(repos, bid, 0, tool="nsync", summary={"tool": "dsync"})     # 잡 행이 권위 -- nsync
    _child(repos, bid, 1, tool="bogus", summary={"tool": "nsync"})     # 행이 허용 밖 → summary v2 의 tool
    _child(repos, bid, 2, tool="bogus", summary={"tool": "weird"})     # 둘 다 밖 → 모름(미정)
    _child(repos, bid, 3, tool="dsync")
    repos.batches.set_item_status(bid, 4, "Rejected", reason_code="r")  # 도구 없이 끝남 -- 미정에 넣지 않는다
    repos.batches.set_item_status(bid, 5, "Succeeded")                  # 성공인데 도구 모름 -- 미정(포탈 ITEM_NOT_RUN 밖)
    # seq 6: Queued, 잡 없음 -- 미정
    light = direct_tool_counts(repos.batches.list_item_tools(bid))
    assert light == {"tools_known": 3, "nsync": 2, "undecided": 3}
    assert direct_tool_counts(repos.batches.list_items_detail(bid)) == light     # 같은 열·같은 「최신 잡 하나」


def test_list_item_tools_tolerates_a_broken_preview_summary(db):
    repos = Repositories(db)
    bid = _create(repos, "sync", [dict(SYNC)], status="Previewing", options={"direct": True})
    _, jid = _child(repos, bid, 0, tool="bogus")
    db.execute("UPDATE data_jobs SET preview_summary = '{broken' WHERE job_id = :j", {"j": jid})
    assert direct_tool_counts(repos.batches.list_item_tools(bid)) == {"tools_known": 0, "nsync": 0, "undecided": 1}


# --- 상세 라우트: view 별 응답 ---------------------------------------------------------------------------

def _post_sync(client, n, **extra):
    items = [dict(SYNC, source=f"src/f{i}", destination=f"dst/F{i}") for i in range(n)]
    return client.post("/api/admin/batches", json={"operation": "sync", "max_concurrency": 1, "options": {},
                                                   "items": items, **extra}).json()["batch_id"]


def test_detail_without_view_is_unchanged_and_all_is_the_same_response(client):
    _admin(client)
    bid = _post_sync(client, 3)
    plain = client.get(f"/api/admin/batches/{bid}")
    assert plain.status_code == 200
    body = plain.json()
    assert len(body["items"]) == 3 and "auto_confirm_hold" in body
    for key in ("item_total", "status_counts", "first_payload", "executing_count", "direct_tools", "storage"):
        assert key not in body
    assert client.get(f"/api/admin/batches/{bid}?view=all").json() == body
    assert list(body)[-2:] == ["items", "auto_confirm_hold"]          # 키 순서까지 지금과 같다


def test_view_all_reads_the_batch_row_before_the_items(client, monkeypatch):
    # 확인 창이 고정하는 회차가 보여 준 항목보다 오래되지 않게 -- 배치 행 먼저, 항목 나중.
    _admin(client)
    bid = _post_sync(client, 2)
    repo = client.app.state.repos.batches
    order = []
    real_get, real_detail = repo.get, repo.list_items_detail
    monkeypatch.setattr(repo, "get", lambda *a, **k: (order.append("batch"), real_get(*a, **k))[1])
    monkeypatch.setattr(repo, "list_items_detail", lambda *a, **k: (order.append("items"), real_detail(*a, **k))[1])
    assert client.get(f"/api/admin/batches/{bid}?view=all").status_code == 200
    assert order == ["batch", "items"]


def test_view_summary_shape(client):
    _admin(client)
    bid = _post_sync(client, 3)
    repos = client.app.state.repos
    repos.batches.set_item_status(bid, 2, "Cancelled", reason_code="c")
    s = client.get(f"/api/admin/batches/{bid}?view=summary").json()
    assert "items" not in s
    assert s["batch_id"] == bid and s["status"] == "Previewing" and s["preview_round"] == 0
    assert s["item_total"] == 3
    assert s["status_counts"] == {"Queued": 2, "Materialized": 0, "Succeeded": 0, "Failed": 0, "Rejected": 0,
                                  "Cancelled": 1}
    assert s["first_payload"] == dict(SYNC, source="src/f0", destination="dst/F0")
    assert (s["storage"], s["source_storage"], s["destination_storage"]) == (None, "s1", "s2")
    assert s["executing_count"] == 0                     # 미리보기 단계 확인 게이트 배치 -- 셌고 0(정상값)
    assert s["direct_tools"] is None                     # direct 꺼짐 -- 말할 것 없음
    assert s["auto_confirm_hold"] is None
    assert client.get(f"/api/admin/batches/{bid}").json()["auto_confirm_hold"] == s["auto_confirm_hold"]


def test_view_summary_executing_count_only_in_the_preview_phase(client):
    _admin(client)
    repos = client.app.state.repos
    bid = _post_sync(client, 2)
    _child(repos, bid, 0, state=DataJobState.EXECUTING)
    get = lambda: client.get(f"/api/admin/batches/{bid}?view=summary").json()["executing_count"]  # noqa: E731
    assert get() == 1
    repos.batches.set_status(bid, "PreviewReady")
    assert get() == 1
    repos.batches.set_status(bid, "Running")
    assert get() is None                                 # 배너가 없는 상태 -- 세지 않는다(null ≠ 0)
    scan = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1, "options": {},
                                                   "items": [{"storage": "s1", "target": "a"}]}).json()["batch_id"]
    assert client.get(f"/api/admin/batches/{scan}?view=summary").json()["executing_count"] is None


def test_view_summary_direct_tools_only_for_an_explicit_true(client):
    _admin(client)
    repos = client.app.state.repos
    for options, expected in (({"direct": True}, {"tools_known": 1, "nsync": 1, "undecided": 1}),
                              ({"direct": "true"}, None), ({"direct": False}, None), ({}, None)):
        bid = _create(repos, "sync", [dict(SYNC, destination=f"d{i}") for i in range(2)], options=options)
        _child(repos, bid, 0, tool="nsync")
        got = client.get(f"/api/admin/batches/{bid}?view=summary").json()["direct_tools"]
        assert got == expected, options


def test_view_summary_survives_a_broken_first_payload(client):
    _admin(client)
    bid = _post_sync(client, 2)
    _set_payload_text(client.app.state.repos.batches._db, bid, 0, "{broken")
    r = client.get(f"/api/admin/batches/{bid}?view=summary")
    assert r.status_code == 200
    s = r.json()
    assert s["first_payload"] is None and s["source_storage"] is None and s["item_total"] == 2


def test_view_items_pages_and_reports_the_filtered_total(client):
    _admin(client)
    bid = _post_sync(client, 7)
    page = client.get(f"/api/admin/batches/{bid}?view=items&offset=2&limit=3").json()
    assert set(page) == {"total", "offset", "limit", "items"}
    assert (page["total"], page["offset"], page["limit"]) == (7, 2, 3)
    assert [it["seq"] for it in page["items"]] == [2, 3, 4]
    full = {it["seq"]: it for it in client.get(f"/api/admin/batches/{bid}").json()["items"]}
    assert page["items"] == [full[s] for s in (2, 3, 4)]          # 행 모양은 전체 조회와 같다
    tail = client.get(f"/api/admin/batches/{bid}?view=items&offset=6&limit=3").json()
    assert [it["seq"] for it in tail["items"]] == [6] and tail["total"] == 7
    past = client.get(f"/api/admin/batches/{bid}?view=items&offset=50").json()
    assert past["items"] == [] and past["total"] == 7 and past["offset"] == 50


def test_view_items_clamps_limit_and_rejects_negative_offset(client):
    _admin(client)
    bid = _post_sync(client, 2)
    lim = lambda qs: client.get(f"/api/admin/batches/{bid}?view=items{qs}").json()["limit"]  # noqa: E731
    assert lim("") == 100
    assert lim("&limit=0") == 1 and lim("&limit=-5") == 1
    assert lim("&limit=9999") == 500 and lim("&limit=500") == 500
    assert len(client.get(f"/api/admin/batches/{bid}?view=items&limit=0").json()["items"]) == 1
    assert client.get(f"/api/admin/batches/{bid}?view=items&offset=-1").status_code == 422


def test_view_items_search_is_a_case_sensitive_path_substring(client):
    _admin(client)
    bid = _post_sync(client, 3)                      # source src/f{i}, destination dst/F{i}
    q = lambda term: [it["seq"] for it in client.get(  # noqa: E731
        f"/api/admin/batches/{bid}", params={"view": "items", "q": term}).json()["items"]]
    assert q("f1") == [1]                            # source
    assert q("F2") == [2]                            # destination
    assert q("dst/") == [0, 1, 2]
    assert q("DST/") == []                           # 대소문자를 접지 않는다(사용량 검색과 같다)
    assert q("s1") == []                             # 스토리지 이름은 검색 대상이 아니다
    assert q("") == [0, 1, 2]                        # 빈 검색어 = 거르지 않음
    scan = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1, "options": {},
                                                   "items": [{"storage": "s1", "target": "x/abc"},
                                                             {"storage": "s1", "target": "y"}]}).json()["batch_id"]
    r = client.get(f"/api/admin/batches/{scan}", params={"view": "items", "q": "ab"}).json()
    assert [it["seq"] for it in r["items"]] == [0] and r["total"] == 1


def test_view_items_status_filter_is_exact_and_unknown_is_empty(client):
    _admin(client)
    bid = _post_sync(client, 4)
    repo = client.app.state.repos.batches
    repo.set_item_status(bid, 1, "Failed", reason_code="x")
    repo.set_item_status(bid, 3, "Failed", reason_code="x")
    st = lambda v: client.get(f"/api/admin/batches/{bid}", params={"view": "items", "status": v}).json()  # noqa: E731
    r = st("Failed")
    assert r["total"] == 2 and [it["seq"] for it in r["items"]] == [1, 3]
    assert st("failed")["total"] == 0                       # 정확 일치
    assert st("NoSuchStatus") == {"total": 0, "offset": 0, "limit": 100, "items": []}
    both = client.get(f"/api/admin/batches/{bid}", params={"view": "items", "status": "Failed", "q": "F3"}).json()
    assert [it["seq"] for it in both["items"]] == [3]


def test_view_items_joins_only_the_page_seqs(client, monkeypatch):
    _admin(client)
    bid = _post_sync(client, 6)
    repo = client.app.state.repos.batches
    calls = []
    real = repo.list_items_detail

    def spy(batch_id, seqs=None):
        calls.append(seqs)
        return real(batch_id, seqs=seqs)
    monkeypatch.setattr(repo, "list_items_detail", spy)
    client.get(f"/api/admin/batches/{bid}", params={"view": "items", "offset": 1, "limit": 2, "q": "dst/"})
    assert calls == [[1, 2]]
    calls.clear()
    client.get(f"/api/admin/batches/{bid}", params={"view": "items", "q": "nothing"})
    assert calls == [[]]                                    # 0건 쪽은 조인할 seq 가 없다


def test_view_items_survives_a_broken_payload_outside_the_page(client):
    _admin(client)
    bid = _post_sync(client, 3)
    _set_payload_text(client.app.state.repos.batches._db, bid, 2, "{broken")
    r = client.get(f"/api/admin/batches/{bid}?view=items&limit=2")
    assert r.status_code == 200 and r.json()["total"] == 3 and [it["seq"] for it in r.json()["items"]] == [0, 1]
    # 깨진 항목은 어떤 검색어에도 걸리지 않는다 -- 검색은 산다
    hit = client.get(f"/api/admin/batches/{bid}", params={"view": "items", "q": "dst/"}).json()
    assert [it["seq"] for it in hit["items"]] == [0, 1] and hit["total"] == 2
    # 깨진 항목이 든 쪽의 행은 payload 를 지어내지 않는다 -- view 없음과 같이 그 응답만 실패한다
    raw = TestClient(client.app, raise_server_exceptions=False)
    raw.cookies.update(client.cookies)
    assert raw.get(f"/api/admin/batches/{bid}?view=items&offset=2").status_code == 500
    assert raw.get(f"/api/admin/batches/{bid}?view=summary").status_code == 200


def test_view_payloads_lists_every_item_lightly(client):
    _admin(client)
    bid = _post_sync(client, 3)
    repo = client.app.state.repos.batches
    repo.set_item_status(bid, 1, "Succeeded")
    repo.delete_item(bid, 0, only_queued=True)
    r = client.get(f"/api/admin/batches/{bid}?view=payloads").json()
    assert r == {"items": [
        {"seq": 1, "payload": dict(SYNC, source="src/f1", destination="dst/F1"), "status": "Succeeded"},
        {"seq": 2, "payload": dict(SYNC, source="src/f2", destination="dst/F2"), "status": "Queued"}]}


def test_views_of_an_empty_batch(client):
    _admin(client)
    repos = client.app.state.repos
    bid = _create(repos, "scan", [{"storage": "s1", "target": "a"}], status="Completed")
    repos.batches.delete_item(bid, 0, only_queued=False)
    s = client.get(f"/api/admin/batches/{bid}?view=summary").json()
    assert s["item_total"] == 0 and s["first_payload"] is None and s["storage"] is None
    assert client.get(f"/api/admin/batches/{bid}?view=items").json() == {
        "total": 0, "offset": 0, "limit": 100, "items": []}
    assert client.get(f"/api/admin/batches/{bid}?view=payloads").json() == {"items": []}
    assert client.get(f"/api/admin/batches/{bid}").json()["items"] == []


def test_unknown_view_is_a_plain_422_and_missing_batch_is_404_in_every_view(client):
    _admin(client)
    bid = _post_sync(client, 1)
    r = client.get(f"/api/admin/batches/{bid}?view=everything")
    assert r.status_code == 422 and not isinstance(r.json()["detail"], str)   # pydantic 일반 422 -- 새 사유 코드 없음
    assert client.get(f"/api/admin/batches/{bid}?view=").status_code == 422
    for view in ("", "?view=summary", "?view=items", "?view=payloads", "?view=all"):
        r = client.get(f"/api/admin/batches/{'0' * 32}{view}")
        assert r.status_code == 404 and r.json()["detail"] == "batch_not_found", view


def test_every_view_requires_admin(client):
    for view in ("", "?view=summary", "?view=items", "?view=payloads", "?view=all"):
        assert client.get(f"/api/admin/batches/x{view}").status_code == 401
