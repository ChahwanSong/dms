"""배치 상세 조회 방식(GET /api/admin/batches/{id}?view=summary|items|payloads|all, 2026-11-16 -- 큰 배치 상세가 폴링마다 전 항목
상세 조인·수 MB 응답을 내던 것을 쪼갰다)과 항목 검색(q -- 경로·PV 이름, batch_item_search)의 계약."""
import pytest
from fastapi.testclient import TestClient

from dms import batch_item_search
from dms.api.app import create_app

from test_k8s_submit import _k8s_settings, _login, world  # noqa: F401 -- world 는 픽스처(k8s 실데이터 동기화)

GPFS_ROOT = "pvc-52c6132d-bce6-4cbe-842a-dbe35245ab2c/pvc-52c6132d-bce6-4cbe-842a-dbe35245ab2c-data"   # PV 이름 models


def _admin(client):
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})


def _scan_batch(repos, targets, *, storage="s1", status="Running", options=None):
    return repos.batches.create(operation="scan", requester_id="admin", actor="admin", max_concurrency=1,
                                options=options or {}, note=None, status=status,
                                items=[{"storage": storage, "target": t} for t in targets])


def _view(client, bid, **params):
    r = client.get(f"/api/admin/batches/{bid}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_no_view_and_all_are_the_old_full_response(client):
    # 롤링 중 옛 포탈 탭은 view 를 모른다 -- 항목 없는 응답을 받으면 확인 창이 「0 / 전체 0」으로 열려 root 삭제를 맹목 승인한다.
    _admin(client)
    repos = client.app.state.repos
    bid = _scan_batch(repos, ["a", "b"])
    old = _view(client, bid)
    assert [it["seq"] for it in old["items"]] == [0, 1] and "auto_confirm_hold" in old
    assert "item_total" not in old and "filters" not in old
    assert _view(client, bid, view="all") == old


def test_summary_has_no_items_and_counts_every_known_status(client):
    _admin(client)
    repos = client.app.state.repos
    bid = _scan_batch(repos, ["a", "b", "c"])
    repos.batches.set_item_status(bid, 1, "Succeeded")
    s = _view(client, bid, view="summary")
    assert "items" not in s
    assert s["item_total"] == 3 and s["item_count"] == 3
    assert s["status_counts"] == {"Queued": 2, "Materialized": 0, "Succeeded": 1, "Failed": 0, "Rejected": 0,
                                  "Cancelled": 0}
    assert s["first_payload"] == {"storage": "s1", "target": "a"}
    assert (s["storage"], s["source_storage"], s["destination_storage"]) == ("s1", None, None)
    # scan 은 확인 단계가 없어 실행 중 배너 재료를 세지 않는다(null = 말할 것 없음), direct 꺼짐도 null
    assert s["executing_count"] is None and s["direct_tools"] is None and s["auto_confirm_hold"] is None


def test_summary_executing_count_only_in_the_preview_phase_and_direct_tools_only_when_direct(client):
    _admin(client)
    repos = client.app.state.repos
    items = [{"source_storage": "s1", "source": f"a{i}", "destination_storage": "s2", "destination": f"b{i}"}
             for i in range(3)]
    bid = repos.batches.create(operation="sync", requester_id="admin", actor="admin", max_concurrency=2,
                               options={"direct": True}, note=None, status="Previewing", items=items)
    rid = repos.requests.create(operation="sync", requester_id="admin", actor="admin", resource_key="k-v-0",
                                payload={}, priority="mid", batch_id=bid)
    repos.batches.set_item_materialized(bid, 0, rid)
    plan = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan, operation="sync", priority="mid", source_storage="s1",
                                     destination_storage="s2", source="a0", destination="b0", options={},
                                     tool="nsync", worker_pool={}, precondition={}, actor="planner")
    client.app.state.repos.batches._db.execute("UPDATE data_jobs SET state = 'Executing' WHERE job_id = :j", {"j": jid})
    repos.batches.set_item_status(bid, 2, "Rejected", reason_code="x")
    s = _view(client, bid, view="summary")
    assert s["executing_count"] == 1
    # 도구를 아는 항목 1(nsync), 미정 = 도구 모름이고 앞으로 돌 수 있는 항목(거부로 끝난 #2 는 빼고 #1 만)
    assert s["direct_tools"] == {"tools_known": 1, "nsync": 1, "undecided": 1}
    assert (s["source_storage"], s["destination_storage"], s["storage"]) == ("s1", "s2", None)
    repos.batches.set_status(bid, "Running")
    assert _view(client, bid, view="summary")["executing_count"] is None          # 미리보기 단계가 아니면 배너 없음


def test_items_pages_cover_the_whole_list_in_seq_order(client):
    _admin(client)
    repos = client.app.state.repos
    bid = _scan_batch(repos, [f"t{i}" for i in range(230)])
    for s in (0, 1, 2, 3):
        repos.batches.delete_item(bid, s * 50, only_queued=False)        # seq 구멍(단건 삭제는 다시 매기지 않는다)
    full = [it["seq"] for it in _view(client, bid)["items"]]
    got, offset = [], 0
    while True:
        p = _view(client, bid, view="items", offset=offset, limit=100)
        assert p["total"] == 226 and p["offset"] == offset and p["limit"] == 100
        assert p["filters"] == {"q": None, "status": None}
        if not p["items"]:
            break
        got += [it["seq"] for it in p["items"]]
        offset += 100
    assert got == full and len(set(got)) == 226
    # 쪽 행은 전체 조회의 그 seq 행과 같은 모양(상세 조인 열 포함)
    row = _view(client, bid, view="items", limit=1)["items"][0]
    assert row == _view(client, bid)["items"][0]
    assert {"request_state", "files_count", "job_state", "preview_summary", "job_tool", "completed_at"} <= set(row)


def test_items_limit_is_clamped_and_bad_values_are_422(client):
    _admin(client)
    repos = client.app.state.repos
    bid = _scan_batch(repos, [f"t{i}" for i in range(600)])
    assert _view(client, bid, view="items")["limit"] == 100                       # 기본 = 화면 쪽 크기
    assert len(_view(client, bid, view="items", limit=0)["items"]) == 1          # 0 → 1 로 접는다
    big = _view(client, bid, view="items", limit=9999, offset=0)
    assert big["limit"] == 500 and len(big["items"]) == 500
    tail = _view(client, bid, view="items", limit=500, offset=500)
    assert [it["seq"] for it in tail["items"]] == list(range(500, 600))
    out = _view(client, bid, view="items", offset=10_000)
    assert out["items"] == [] and out["total"] == 600                             # 범위 밖 = 빈 쪽, 수는 그대로
    assert client.get(f"/api/admin/batches/{bid}", params={"view": "items", "offset": -1}).status_code == 422
    assert client.get(f"/api/admin/batches/{bid}", params={"view": "bogus"}).status_code == 422
    assert client.get(f"/api/admin/batches/{bid}", params={"view": "items", "q": "x" * 513}).status_code == 422
    assert client.get("/api/admin/batches/nope", params={"view": "items"}).status_code == 404


def test_items_status_filter_is_exact_and_unknown_is_empty(client):
    _admin(client)
    repos = client.app.state.repos
    bid = _scan_batch(repos, ["a", "b", "c", "d"])
    repos.batches.set_item_status(bid, 1, "Failed", reason_code="x")
    repos.batches.set_item_status(bid, 3, "Failed", reason_code="x")
    p = _view(client, bid, view="items", status="Failed")
    assert [it["seq"] for it in p["items"]] == [1, 3] and p["total"] == 2
    assert p["filters"] == {"q": None, "status": "Failed"}
    none = _view(client, bid, view="items", status="Nope")
    assert none["items"] == [] and none["total"] == 0                              # 0 = 결과 없음(정상값)
    assert _view(client, bid, view="items", status="")["total"] == 4             # 빈 값 = 거르지 않음


def test_items_search_tokens_and_case_insensitive_on_paths(client):
    # 사용량 검색과 같은 뜻: 토큰마다 대소문자 무시 부분 일치, 토큰 사이 AND, 화살표는 구분자. 스토리지 이름은 칸이 아니다.
    _admin(client)
    repos = client.app.state.repos
    bid = _scan_batch(repos, ["Team/Alpha/run1", "team/beta/run2", "other/alpha", "x_y%z"], storage="nfs-store")
    seqs = lambda **kw: [it["seq"] for it in _view(client, bid, view="items", **kw)["items"]]  # noqa: E731
    assert seqs(q="alpha") == [0, 2]
    assert seqs(q="TEAM alpha") == [0]
    assert seqs(q="team → run2") == [1]
    assert seqs(q="nfs") == []                                                      # 스토리지 이름은 거르지 않는다
    assert seqs(q="x_y%") == [3] and seqs(q="_") == [3]                            # 와일드카드 없음(글자 그대로)
    assert seqs(q="   ") == [0, 1, 2, 3]                                            # 토큰 없음 = 거르지 않음
    p = _view(client, bid, view="items", q="alpha", status="Queued", limit=1)
    assert p["total"] == 2 and [it["seq"] for it in p["items"]] == [0]
    assert p["filters"] == {"q": "alpha", "status": "Queued"}


def test_items_search_never_matches_a_broken_payload_and_only_that_page_fails(db, settings):
    client = TestClient(create_app(settings, db), raise_server_exceptions=False)
    _admin(client)
    repos = client.app.state.repos
    bid = _scan_batch(repos, [f"t{i}" for i in range(150)])
    db.execute("UPDATE batch_items SET payload = '{broken' WHERE batch_id = :b AND seq = 120", {"b": bid})
    assert _view(client, bid, view="summary")["item_total"] == 150               # 요약은 산다
    assert len(_view(client, bid, view="items", limit=100)["items"]) == 100      # 다른 쪽도 산다
    hits = _view(client, bid, view="items", q="t12")
    assert [it["seq"] for it in hits["items"]] == [12] + list(range(121, 130))   # 깨진 #120 은 어떤 검색어에도 안 걸린다
    # 그 행이 든 쪽은 view 없음과 같이 실패한다(행 payload 를 지어내지 않는다)
    assert client.get(f"/api/admin/batches/{bid}", params={"view": "items", "offset": 100}).status_code == 500


def test_payloads_is_every_item_without_the_join(client):
    _admin(client)
    repos = client.app.state.repos
    bid = _scan_batch(repos, ["a", "b"])
    repos.batches.set_item_status(bid, 1, "Succeeded")
    assert _view(client, bid, view="payloads") == {"items": [
        {"seq": 0, "payload": {"storage": "s1", "target": "a"}, "status": "Queued"},
        {"seq": 1, "payload": {"storage": "s1", "target": "b"}, "status": "Succeeded"}]}


def test_list_rows_carry_the_target_storage(client):
    _admin(client)
    repos = client.app.state.repos
    _scan_batch(repos, ["a"], storage="cephfs-dms")
    repos.batches.create(operation="sync", requester_id="admin", actor="admin", max_concurrency=1, options={},
                         note=None, status="Previewing",
                         items=[{"source_storage": "k8s-pv-nfs", "source": "a", "destination_storage": "k8s-pv-gpfs",
                                 "destination": "b"}])
    rows = client.get("/api/admin/batches", params={"limit": 101}).json()
    got = sorted((r["operation"], r["storage"], r["source_storage"], r["destination_storage"]) for r in rows)
    assert got == [("scan", "cephfs-dms", None, None), ("sync", None, "k8s-pv-nfs", "k8s-pv-gpfs")]


def test_detail_views_are_readable_with_the_shared_token(client):
    # 읽기라 require_admin(다른 관리 GET 과 같다) -- 공유 토큰 조회는 지금도 된다(변경 라우트가 아니다).
    repos = client.app.state.repos
    bid = _scan_batch(repos, ["a"])
    for view in ("summary", "items", "payloads", "all"):
        r = client.get(f"/api/admin/batches/{bid}", params={"view": view},
                       headers={"Authorization": "Bearer tok-shared"})
        assert r.status_code == 200, (view, r.text)


# --- PV 이름 검색(k8s -- 사용량 검색과 같은 판정: 지금 인벤토리의 가장 깊은 PV 루트의 PVC 이름) ---

@pytest.fixture
def pvclient(db, world):  # noqa: F811 -- world 는 위에서 가져온 픽스처
    return TestClient(create_app(_k8s_settings(), db))


def test_items_search_matches_the_pv_name_of_a_path(pvclient):
    c = _login(pvclient, "ops")
    repos = c.app.state.repos
    bid = _scan_batch(repos, [f"{GPFS_ROOT}/run1", f"{GPFS_ROOT}/run2/", "elsewhere/run1"], storage="k8s-pv-gpfs")
    seqs = lambda **kw: [it["seq"] for it in _view(c, bid, view="items", **kw)["items"]]  # noqa: E731
    assert seqs(q="models") == [0, 1]                    # 경로엔 없는 PV 이름(끝 「/」 표기도 정규화해 판정)
    assert seqs(q="MODELS run1") == [0]                  # 토큰마다 경로 또는 PV 이름, 토큰 사이 AND
    assert seqs(q="run1") == [0, 2]
    assert seqs(q="models elsewhere") == []


def test_items_search_on_sync_joins_both_sides(pvclient):
    c = _login(pvclient, "ops")
    repos = c.app.state.repos
    bid = repos.batches.create(operation="sync", requester_id="ops", actor="ops", max_concurrency=1, options={},
                               note=None, status="Previewing",
                               items=[{"source_storage": "k8s-pv-gpfs", "source": f"{GPFS_ROOT}/a",
                                       "destination_storage": "k8s-pv-nfs", "destination": "team-a/datasets/copy"},
                                      {"source_storage": "k8s-pv-gpfs", "source": "plain/a",
                                       "destination_storage": "k8s-pv-nfs", "destination": "team-b/scratch/copy"}])
    seqs = lambda q: [it["seq"] for it in _view(c, bid, view="items", q=q)["items"]]  # noqa: E731
    assert seqs("models datasets") == [0]                # 출발 PV 이름 + 도착 경로(같은 항목 안에서 AND)
    assert seqs("scratch") == [1]
    assert seqs("models scratch") == []                  # 다른 항목의 칸끼리는 섞이지 않는다


def test_items_search_on_a_storage_without_pvs_is_path_only(pvclient, monkeypatch):
    c = _login(pvclient, "ops")
    repos = c.app.state.repos
    bid = _scan_batch(repos, ["models/a", "b"], storage="cephfs-dms")
    calls = []
    real = batch_item_search.collect
    monkeypatch.setattr(batch_item_search, "collect", lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    assert [it["seq"] for it in _view(c, bid, view="items", q="models")["items"]] == [0]
    assert calls == []                                   # PV 가 생길 수 없는 스토리지는 소속 판정 재료를 모으지 않는다
    nfs = _scan_batch(repos, ["team-a/datasets/x"], storage="k8s-pv-nfs")
    assert [it["seq"] for it in _view(c, nfs, view="items", q="datasets")["items"]] == [0]
    assert calls == ["k8s-pv-nfs"]                       # 스토리지마다 한 번
