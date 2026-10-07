"""배치 sync 확인 계약 강화(2026-10-07 적대적 조사 → 사용자 "고치는 방향으로 구현").

1) 특권 게이트: 새 경로를 실행시키는 배치 라우트(항목 추가·수정·교체, 선택·실패분·전체 재실행, 확인)는 생성과 같은
   3중 게이트(기능 플래그 + allowlist + 세션)를 다시 통과해야 한다 -- 자식은 배치 행의 세션 인증·요청자를 물려받아
   root 로 돈다. 공유 토큰(role admin, 모든 노드 에이전트가 보유)·allowlist 밖 관리자는 403. 실행을 줄이기만 하는
   라우트(항목 삭제·취소·메타 수정)는 require_admin 그대로다.
2) sync 재확인: Running·PreviewReady sync 배치에 Queued 가 새로 생기면(추가·수정·재실행) 배치를 Previewing 으로
   되돌린다 -- 새 항목은 미리보기 뒤 사람이 다시 확인해야 실행된다(예전엔 orchestrator 가 자동 컨펌했다).
3) 확인은 원자 갱신(WHERE status='PreviewReady' AND Queued 없음) + 감사 행.
6) 경합(같은 날 리뷰): 상태 전이는 전부 CAS -- orchestrator 의 PreviewReady·Completed 전이는 스냅샷 뒤 생긴 Queued 를
   덮지 않고, 라우트의 Previewing 복귀는 새 Queued 커밋 뒤 지금 상태로 판정하며(그 사이 취소는 되살리지 않는다),
   Running sync 배치에 Queued 가 보이면 루프가 materialize 대신 Previewing 으로 되돌린다. Running 중 만료된 미리보기는
   다시 미리보기(자동 컨펌)하지 않는다. 자식 생성은 요청 INSERT + 항목 claim(같은 저장 payload) 한 트랜잭션.
4) 배치 자식은 단건 컨펌 API 로 실행을 시작할 수 없다(409 batch_child_confirm_via_batch).
5) 배치 상세 항목에 미리보기 상태·요약·만료가 실린다(확인 대화상자의 재료).
"""
from fastapi.testclient import TestClient

from dms.batch_orchestrator import BatchOrchestrator
from dms.config import Settings
from dms.domain import DataJobState, RequestState
from dms.repositories import Repositories

TOKEN = {"Authorization": "Bearer tok-shared"}
SYNC_ITEM = {"source_storage": "s1", "source": "a", "destination_storage": "s2", "destination": "b"}
SCAN_ITEM = {"storage": "s1", "target": "a"}


def _app(db, **overrides):
    from dms.api.app import create_app
    base = {"DMS_DATABASE_URL": "unused", "DMS_SHARED_TOKEN": "tok-shared",
            "DMS_ADMIN_TOKEN": "tok-admin", "DMS_SESSION_SECRET": "sess", **overrides}
    return create_app(Settings.from_env(base), db)


def _login(client, username):
    # from_env 앱은 라이브 자세(비밀번호 봉인 필수) -- 로그인은 봉인해 보낸다.
    from dms.api.password_transport import seal_with_info
    if client.app.state.repos.accounts.get(username) is None:
        client.post("/api/admin/accounts", json={"username": username, "password": "p"},
                    headers={"x-admin-token": "tok-admin"})
    info = client.get("/api/auth/transport-key").json()
    r = client.post("/api/auth/login", json={
        "username": username,
        "password_enc": seal_with_info(info, "p", purpose="login", username=username)})
    assert r.status_code == 200, r.text


def _batch(repos, *, op="sync", status="Previewing", item_status=None, n=1):
    item = SYNC_ITEM if op == "sync" else SCAN_ITEM
    items = [{**item, ("destination" if op == "sync" else "target"): f"d{i}"} for i in range(n)]
    bid = repos.batches.create(operation=op, requester_id="ops", actor="ops", max_concurrency=2,
                               options={}, items=items, note=None, status=status,
                               auth_method="session")
    if item_status is not None:
        for it in repos.batches.list_items(bid):
            repos.batches.set_item_status(bid, it["seq"], item_status)
    return bid


def _confirmpending_child(repos, bid, seq=0, fp="sha256:fp", expires="2099-01-01T00:00:00Z"):
    """배치 항목 seq 를 materialize 하고 자식 잡을 ConfirmPending 으로 만든다(미리보기 완료 시뮬)."""
    rid = repos.requests.create(operation="sync", requester_id="ops", actor="ops", resource_key=f"k-{bid}-{seq}",
                                payload=dict(SYNC_ITEM), priority="mid", batch_id=bid, auth_method="session")
    repos.batches.set_item_materialized(bid, seq, rid)
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan, operation="sync", priority="mid", source_storage="s1",
                                     destination_storage="s2", source="a", destination="b", options={},
                                     tool="dsync", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint=fp, expires_at=expires, artifact_uri="x",
                                summary={"files": 42, "bytes": None, "returncode": 0})
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    return rid, jid


# 각 라우트 = (메서드, 경로 뒤꼬리, 본문, 필요한 배치 상태·항목 상태) -- 실행을 일으키는 변경 라우트 전부.
def _gated_cases(repos):
    return [
        ("post", "/items", dict(SYNC_ITEM, destination="new"), _batch(repos, status="Running")),
        ("put", "/items/0", dict(SYNC_ITEM, destination="new"), _batch(repos, status="Running")),
        ("put", "/items", {"items": [dict(SYNC_ITEM, destination="x")]},
         _batch(repos, status="Completed", item_status="Succeeded")),
        ("post", "/items:rerun", {"seqs": [0]}, _batch(repos, status="Completed", item_status="Failed")),
        ("post", ":rerun-failed", None, _batch(repos, status="Completed", item_status="Failed")),
        ("post", ":rescan", None, _batch(repos, status="Completed", item_status="Succeeded")),
        ("post", ":confirm", {"preview_round": 0}, _ready_batch(repos)),
    ]


def _ready_batch(repos):
    # 확인 가능한 배치 = PreviewReady + 항목 전원 미리보기 완료(Queued 가 있으면 확인 가드가 0 행).
    bid = _batch(repos, status="PreviewReady")
    _confirmpending_child(repos, bid, 0)
    return bid


def _call(client, method, bid, tail, body, headers=None):
    url = f"/api/admin/batches/{bid}{tail}"
    kw = {"headers": headers} if headers else {}
    if body is not None:
        kw["json"] = body
    return getattr(client, method)(url, **kw)


def test_shared_token_cannot_drive_execution_routes(db):
    app = _app(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true", DMS_PRIVILEGED_REQUESTERS="ops")
    client = TestClient(app)
    repos = app.state.repos
    for method, tail, body, bid in _gated_cases(repos):
        before = (repos.batches.get(bid)["status"], [(i["status"], i["payload"]) for i in repos.batches.list_items(bid)])
        r = _call(client, method, bid, tail, body, headers=TOKEN)
        assert r.status_code == 403 and r.json()["detail"] == "privileged_not_authorized", (tail, r.text)
        after = (repos.batches.get(bid)["status"], [(i["status"], i["payload"]) for i in repos.batches.list_items(bid)])
        assert after == before, tail                                   # 아무것도 바뀌지 않았다


def test_admin_outside_allowlist_cannot_drive_execution_routes(db):
    app = _app(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true", DMS_PRIVILEGED_REQUESTERS="ops")
    client = TestClient(app)
    _login(client, "other")                                            # 관리자지만 특권 목록 밖
    repos = app.state.repos
    for method, tail, body, bid in _gated_cases(repos):
        r = _call(client, method, bid, tail, body)
        assert r.status_code == 403 and r.json()["detail"] == "privileged_not_authorized", (tail, r.text)


def test_privileged_session_admin_can_drive_execution_routes(db):
    app = _app(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true", DMS_PRIVILEGED_REQUESTERS="ops")
    client = TestClient(app)
    _login(client, "ops")
    repos = app.state.repos
    for method, tail, body, bid in _gated_cases(repos):
        r = _call(client, method, bid, tail, body)
        assert r.status_code in (200, 202), (tail, r.text)


def test_routes_that_only_reduce_execution_stay_admin_only(db):
    # 항목 삭제·취소·메타 수정은 실행을 줄이기만 한다 -- 토큰(관리자)으로도 된다(의도된 분리).
    app = _app(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true", DMS_PRIVILEGED_REQUESTERS="ops")
    client = TestClient(app)
    repos = app.state.repos
    bid = _batch(repos, status="Running", n=2)
    assert client.patch(f"/api/admin/batches/{bid}", json={"note": "x"}, headers=TOKEN).status_code == 200
    assert client.delete(f"/api/admin/batches/{bid}/items/1", headers=TOKEN).status_code == 200
    assert client.post(f"/api/admin/batches/{bid}:cancel", headers=TOKEN).status_code == 200


# --- sync 재확인: Queued 가 새로 생기면 Previewing 으로 ------------------------------------------

def _admin(client):
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})


def test_adding_to_running_sync_batch_requires_a_new_confirm(client):
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="Running")
    r = client.post(f"/api/admin/batches/{bid}/items", json=dict(SYNC_ITEM, destination="new"))
    assert r.status_code == 202 and r.json()["status"] == "Previewing"
    assert repos.batches.get(bid)["status"] == "Previewing"


def test_adding_to_previewready_sync_batch_reopens_preview_and_blocks_stale_confirm(client):
    # 예전: PreviewReady 유지 → 확인 1회가 미리보기도 안 끝난 새 항목까지 실행. 이제 Previewing 으로 돌아가고,
    # 그 사이 들어온 확인(운영자가 옛 화면을 보고 누른 것)은 409.
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady")
    r = client.post(f"/api/admin/batches/{bid}/items", json=dict(SYNC_ITEM, destination="new"))
    assert r.json()["status"] == "Previewing"
    r = client.post(f"/api/admin/batches/{bid}:confirm", json={"preview_round": 0})
    assert r.status_code == 409 and r.json()["detail"] == "batch_not_confirmable"


def test_adding_to_previewing_sync_or_running_scan_keeps_status(client):
    _admin(client)
    repos = client.app.state.repos
    sync_bid = _batch(repos, status="Previewing")
    assert client.post(f"/api/admin/batches/{sync_bid}/items",
                       json=dict(SYNC_ITEM, destination="new")).json()["status"] == "Previewing"
    scan_bid = _batch(repos, op="scan", status="Running")
    assert client.post(f"/api/admin/batches/{scan_bid}/items",
                       json=dict(SCAN_ITEM, target="new")).json()["status"] == "Running"
    assert repos.batches.get(scan_bid)["status"] == "Running"


def test_editing_a_queued_item_of_running_sync_batch_requires_a_new_confirm(client):
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="Running")
    r = client.put(f"/api/admin/batches/{bid}/items/0", json=dict(SYNC_ITEM, destination="changed"))
    assert r.status_code == 200
    assert repos.batches.get(bid)["status"] == "Previewing"


def test_editing_a_terminal_sync_batch_item_does_not_restart_it(client):
    # 종단 배치의 수정은 재실행이 아니다(재실행 라우트 몫) -- 상태를 건드리지 않는다.
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="Completed", item_status="Succeeded")
    assert client.put(f"/api/admin/batches/{bid}/items/0",
                      json=dict(SYNC_ITEM, destination="changed")).status_code == 200
    assert repos.batches.get(bid)["status"] == "Completed"


def test_rerun_on_running_sync_batch_requires_a_new_confirm(client):
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="Running", n=2)
    repos.batches.set_item_status(bid, 0, "Failed")
    r = client.post(f"/api/admin/batches/{bid}/items:rerun", json={"seqs": [0]})
    assert r.json() == {"requeued": 1, "skipped": [], "status": "Previewing"}

    bid2 = _batch(repos, status="Running", n=2)
    repos.batches.set_item_status(bid2, 0, "Failed")
    r = client.post(f"/api/admin/batches/{bid2}:rerun-failed")
    assert r.json()["status"] == "Previewing"


def test_rerun_failed_on_running_scan_batch_keeps_running(client):
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, op="scan", status="Running", n=2)
    repos.batches.set_item_status(bid, 0, "Failed")
    assert client.post(f"/api/admin/batches/{bid}:rerun-failed").json()["status"] == "Running"


def test_reopened_batch_is_not_auto_confirmed_by_the_orchestrator(db, client):
    # 끝까지: Running 배치의 ConfirmPending 자식(확인됐지만 슬롯을 기다리던 것) + 새 항목 추가 → Previewing →
    # orchestrator 가 그 자식을 컨펌하지 않는다. 새 항목까지 미리보기가 끝나면 PreviewReady 로 돌아온다.
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="Running")
    _, jid = _confirmpending_child(repos, bid, 0)
    client.post(f"/api/admin/batches/{bid}/items", json=dict(SYNC_ITEM, destination="new"))

    class _S:
        preview_ttl_seconds = 900
    orch = BatchOrchestrator(Repositories(db), settings=_S())
    orch.run_once()                                                    # 새 항목 materialize, 컨펌 없음
    assert repos.data_jobs.get_job(jid)["state"] == "ConfirmPending"
    new_item = [it for it in repos.batches.list_items(bid) if it["seq"] == 1][0]
    assert new_item["status"] == "Materialized"
    plan = repos.data_jobs.create_plan(new_item["request_id"], actor="planner")   # 새 항목도 미리보기 완료 시뮬
    j2 = repos.data_jobs.create_job(new_item["request_id"], plan, operation="sync", priority="mid",
                                    source_storage="s1", destination_storage="s2", source="a", destination="new",
                                    options={}, tool="dsync", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(j2, fingerprint="sha256:new", expires_at="2099-01-01T00:00:00Z", artifact_uri="x")
    repos.data_jobs.set_job_state(j2, DataJobState.CONFIRM_PENDING, actor="stepper")
    orch.run_once()
    assert repos.batches.get(bid)["status"] == "PreviewReady"
    assert repos.data_jobs.get_job(jid)["state"] == "ConfirmPending"   # 여전히 사람 확인 대기


# --- 확인: 원자 갱신 + 감사 ----------------------------------------------------------------------

def test_confirm_records_who_confirmed_what(client, db):
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady", n=2)
    _confirmpending_child(repos, bid, 0)
    repos.batches.set_item_status(bid, 1, "Rejected")
    r = client.post(f"/api/admin/batches/{bid}:confirm", json={"preview_round": 0})
    assert r.status_code == 200 and r.json() == {"status": "Running"}
    assert repos.batches.get(bid)["status"] == "Running"
    rows = db.query("SELECT target_key, actor, before_state, after_state FROM audit_log "
                    "WHERE mutation_class = 'batch' AND operation = 'confirm'")
    assert len(rows) == 1 and rows[0]["target_key"] == bid and rows[0]["actor"] == "admin"
    import json
    after = json.loads(rows[0]["after_state"])
    assert after["status"] == "Running" and after["previewed"] == 1 and after["items"] == 2
    assert json.loads(rows[0]["before_state"]) == {"status": "PreviewReady"}


def test_confirm_route_returns_409_when_status_changes_after_read(client, db, monkeypatch):
    # 라우트의 사전 판정은 PreviewReady 를 읽었지만, 쓰는 순간엔 항목 추가로 Previewing 이 돼 있다 -- 원자 갱신이
    # 0 행이라 409, 배치는 Previewing 그대로, 감사 행 없음(사전 판정만 보고 쓰는 회귀를 잡는다).
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="Previewing")
    real_get = repos.batches.get
    monkeypatch.setattr(repos.batches, "get", lambda b: {**real_get(b), "status": "PreviewReady"})
    r = client.post(f"/api/admin/batches/{bid}:confirm", json={"preview_round": 0})
    assert r.status_code == 409 and r.json()["detail"] == "batch_not_confirmable"
    assert real_get(bid)["status"] == "Previewing"
    assert db.query("SELECT 1 AS x FROM audit_log WHERE operation = 'confirm'") == []


def test_confirm_refuses_a_batch_with_queued_items_and_reopens_it(client, db):
    # 미리보기 안 된 Queued 항목이 있는 PreviewReady(정상 경로를 놓친 경우)는 확인되지 않고(같은 문장의 가드),
    # 확인 대기에 갇히지 않게 Previewing 으로 돌아간다.
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady", n=2)
    _confirmpending_child(repos, bid, 0)                               # seq 1 은 Queued 그대로
    assert repos.batches.confirm(bid, actor="x", summary={}, expected_round=0) is False
    r = client.post(f"/api/admin/batches/{bid}:confirm", json={"preview_round": 0})
    assert r.status_code == 409 and r.json()["detail"] == "batch_not_confirmable"
    assert repos.batches.get(bid)["status"] == "Previewing"
    assert db.query("SELECT 1 AS x FROM audit_log WHERE operation = 'confirm'") == []


def test_confirm_is_atomic_against_a_status_change(client, db):
    # 저장소 원자 가드: PreviewReady 가 아니면 0 행 → False(감사 행도 없음).
    repos = client.app.state.repos
    bid = _batch(repos, status="Previewing")
    assert repos.batches.confirm(bid, actor="x", summary={}, expected_round=0) is False
    assert repos.batches.get(bid)["status"] == "Previewing"
    assert db.query("SELECT 1 AS x FROM audit_log WHERE operation = 'confirm'") == []


# --- 배치 자식은 단건 컨펌 불가 --------------------------------------------------------------------

def test_batch_child_cannot_be_confirmed_individually(client):
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady")
    _, jid = _confirmpending_child(repos, bid, 0, fp="sha256:fp")
    r = client.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": "sha256:fp"})
    assert r.status_code == 409 and r.json()["detail"] == "batch_child_confirm_via_batch"
    assert repos.data_jobs.get_job(jid)["state"] == "ConfirmPending"   # 실행이 시작되지 않았다


# --- 상세 항목의 미리보기 필드 ---------------------------------------------------------------------

def test_batch_detail_items_carry_preview_state_summary_and_expiry(client):
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady", n=2)
    _confirmpending_child(repos, bid, 0)
    items = client.get(f"/api/admin/batches/{bid}").json()["items"]
    first, second = items[0], items[1]
    assert first["job_state"] == "ConfirmPending"
    assert first["preview_summary"] == {"files": 42, "bytes": None, "returncode": 0}
    assert first["preview_expires_at"] == "2099-01-01T00:00:00Z"
    # 아직 자식이 없는 항목은 모름(null) -- 0 이나 빈 객체로 뭉개지 않는다
    assert second["job_state"] is None and second["preview_summary"] is None and second["preview_expires_at"] is None


# --- 경합: CAS 전이 -------------------------------------------------------------------------------

class _S:
    preview_ttl_seconds = 900


def _stale_list_items(monkeypatch, repos, bid, snapshot):
    """orchestrator 가 읽는 틱 스냅샷을 고정한다 -- 스냅샷 뒤 API 쓰기가 끼어든 상황."""
    real = repos.batches.list_items
    monkeypatch.setattr(repos.batches, "list_items", lambda b: snapshot if b == bid else real(b))


def test_orchestrator_does_not_mark_preview_ready_over_a_new_queued_item(client, monkeypatch):
    # 스냅샷: 유일 항목이 미리보기 완료. 그 뒤(스냅샷과 쓰기 사이) 항목이 추가됐다 -- 무조건 쓰기면 미리보기 안 된
    # Queued 를 둔 채 확인 대기가 되고, 확인하면 그 항목이 root 로 돈다.
    repos = client.app.state.repos
    bid = _batch(repos, status="Previewing")
    _confirmpending_child(repos, bid, 0)
    snapshot = repos.batches.list_items(bid)
    repos.batches.add_item(bid, dict(SYNC_ITEM, destination="late"))
    _stale_list_items(monkeypatch, repos, bid, snapshot)
    BatchOrchestrator(repos, settings=_S()).run_once()
    assert repos.batches.get(bid)["status"] == "Previewing"
    monkeypatch.undo()
    assert repos.batches.mark_preview_ready(bid) is False             # 저장소 가드 자체


def test_orchestrator_does_not_complete_over_a_new_queued_item(client, monkeypatch):
    repos = client.app.state.repos
    bid = _batch(repos, op="scan", status="Running", item_status="Succeeded")
    snapshot = repos.batches.list_items(bid)
    repos.batches.add_item(bid, dict(SCAN_ITEM, target="late"))
    _stale_list_items(monkeypatch, repos, bid, snapshot)
    BatchOrchestrator(repos, settings=_S()).run_once()
    assert repos.batches.get(bid)["status"] == "Running"              # 새 항목이 갇히지 않는다


def test_running_sync_batch_with_queued_items_goes_back_to_previewing(client):
    # 백스톱: 라우트의 복귀를 놓친 Running sync 배치의 Queued(경합·옛 행)는 materialize 하지 않고(만들면 미리보기 뒤
    # 자동 컨펌) 배치를 Previewing 으로 되돌린다. 확인받은 자식도 컨펌하지 않는다.
    repos = client.app.state.repos
    bid = _batch(repos, status="Running", n=2)
    _, jid = _confirmpending_child(repos, bid, 0)
    BatchOrchestrator(repos, settings=_S()).run_once()
    assert repos.batches.get(bid)["status"] == "Previewing"
    assert [it["status"] for it in repos.batches.list_items(bid)] == ["Materialized", "Queued"]
    assert repos.data_jobs.get_job(jid)["state"] == "ConfirmPending"


def test_expired_preview_in_running_batch_is_neither_confirmed_nor_previewed_again(client):
    # Running 중 만료 = 운영자가 확인한 미리보기가 무효. 예전엔 Queued 로 되돌려 새 미리보기를 자동 컨펌했다(사람이
    # 보지 않은 root 실행). 이제 그대로 두고 stepper 가 Rejected 로 끝낸다. 만료되지 않은 자식은 슬롯대로 컨펌된다.
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady", n=2)
    rid_old, j_old = _confirmpending_child(repos, bid, 0, expires="2000-01-01T00:00:00Z")
    _, j_ok = _confirmpending_child(repos, bid, 1)
    assert repos.batches.confirm(bid, actor="ops", summary={}, expected_round=0)   # 확인 도장 → Running
    BatchOrchestrator(repos, settings=_S()).run_once()
    first = repos.batches.list_items(bid)[0]
    assert first["status"] == "Materialized" and first["request_id"] == rid_old   # Queued 로 안 돌아갔다
    assert repos.data_jobs.get_job(j_old)["state"] == "ConfirmPending"            # 컨펌 안 됨(stepper 몫)
    assert repos.data_jobs.get_job(j_ok)["state"] == "Executing"
    assert repos.batches.get(bid)["status"] == "Running"


def test_route_reopens_a_batch_the_orchestrator_marked_ready_in_between(client, monkeypatch):
    # 라우트는 Previewing 을 읽었고(그래서 예전엔 상태를 안 건드렸다), 새 항목 INSERT 전에 orchestrator 가
    # PreviewReady 를 커밋했다. 새 Queued 커밋 뒤의 CAS 가 지금 상태(PreviewReady)를 보고 Previewing 으로 되돌린다.
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady")
    real_get = repos.batches.get
    calls = {"n": 0}

    def stale_first(b):
        calls["n"] += 1
        row = real_get(b)
        return {**row, "status": "Previewing"} if calls["n"] == 1 else row
    monkeypatch.setattr(repos.batches, "get", stale_first)
    r = client.post(f"/api/admin/batches/{bid}/items", json=dict(SYNC_ITEM, destination="new"))
    assert r.status_code == 202 and r.json()["status"] == "Previewing"
    assert real_get(bid)["status"] == "Previewing"


def test_route_does_not_resurrect_a_batch_cancelled_in_between(client, monkeypatch):
    # 라우트는 Running 을 읽었지만 그 사이 취소됐다 -- 무조건 Previewing 쓰기는 취소된 배치를 되살렸다.
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="Cancelled")
    real_get = repos.batches.get
    calls = {"n": 0}

    def stale_first(b):
        calls["n"] += 1
        row = real_get(b)
        return {**row, "status": "Running"} if calls["n"] == 1 else row
    monkeypatch.setattr(repos.batches, "get", stale_first)
    r = client.post(f"/api/admin/batches/{bid}/items", json=dict(SYNC_ITEM, destination="new"))
    assert r.status_code == 202 and r.json()["status"] == "Cancelled"
    assert real_get(bid)["status"] == "Cancelled"


def test_adding_to_a_completed_or_cancelled_batch_still_reactivates_it(client):
    _admin(client)
    repos = client.app.state.repos
    for status in ("Completed", "Cancelled"):
        bid = _batch(repos, status=status, item_status="Succeeded")
        assert client.post(f"/api/admin/batches/{bid}/items",
                           json=dict(SYNC_ITEM, destination="new")).json()["status"] == "Previewing"
        sbid = _batch(repos, op="scan", status=status, item_status="Succeeded")
        assert client.post(f"/api/admin/batches/{sbid}/items",
                           json=dict(SCAN_ITEM, target="new")).json()["status"] == "Running"


def test_materialize_skips_an_item_edited_after_the_snapshot(client, monkeypatch, db):
    # 스냅샷의 payload(옛 경로)로 자식을 만들고 항목은 새 경로를 보이던 기록 불일치 -- 이제 트랜잭션 안에서 저장값을
    # 다시 읽어 다르면 만들지 않는다(다음 틱이 새 값으로). 요청 행도 남지 않는다.
    repos = client.app.state.repos
    bid = _batch(repos, status="Previewing")
    snapshot = repos.batches.list_items(bid)
    assert repos.batches.update_item_payload(bid, 0, dict(SYNC_ITEM, destination="edited"), only_queued=True)
    _stale_list_items(monkeypatch, repos, bid, snapshot)
    before = db.query_one("SELECT COUNT(*) AS n FROM requests")["n"]
    BatchOrchestrator(repos, settings=_S()).run_once()
    assert db.query_one("SELECT COUNT(*) AS n FROM requests")["n"] == before
    monkeypatch.undo()
    item = repos.batches.list_items(bid)[0]
    assert item["status"] == "Queued" and item["payload"]["destination"] == "edited"
    BatchOrchestrator(repos, settings=_S()).run_once()                 # 다음 틱: 새 값으로 만든다
    item = repos.batches.list_items(bid)[0]
    assert item["status"] == "Materialized"
    assert repos.requests.get(item["request_id"])["payload"]["destination"] == "edited"


def test_materialize_rolls_back_the_child_request_when_the_claim_loses(client, monkeypatch, db):
    repos = client.app.state.repos
    bid = _batch(repos, status="Previewing")
    monkeypatch.setattr(repos.batches, "claim_queued_item", lambda *a, **k: False)
    before = db.query_one("SELECT COUNT(*) AS n FROM requests")["n"]
    BatchOrchestrator(repos, settings=_S()).run_once()
    assert db.query_one("SELECT COUNT(*) AS n FROM requests")["n"] == before   # 고아 자식 요청 없음
    assert repos.batches.list_items(bid)[0]["status"] == "Queued"


# --- 2차 리뷰(같은 날): 거부 경로 payload 가드, 확인 도장, 실행 중 자식, 만료, 확인 회차 -----------------

def test_reject_path_does_not_reject_an_item_fixed_after_the_snapshot(client, monkeypatch, db):
    # 스냅샷의 무효 payload 로 거부 판정을 내린 사이 관리자가 항목을 고쳤다 -- 옛 사유로 거부·실패 집계하지 않고
    # 다음 틱이 새 payload 로 판정한다(claim_queued_item 과 같은 가드).
    import json
    repos = client.app.state.repos
    bid = _batch(repos, status="Previewing")
    bad = dict(SYNC_ITEM, source="../escape")
    db.execute("UPDATE batch_items SET payload = :p WHERE batch_id = :b",
               {"p": json.dumps(bad, sort_keys=True, ensure_ascii=False), "b": bid})
    snapshot = repos.batches.list_items(bid)
    assert repos.batches.update_item_payload(bid, 0, dict(SYNC_ITEM, source="fixed"), only_queued=True)
    _stale_list_items(monkeypatch, repos, bid, snapshot)
    BatchOrchestrator(repos, settings=_S()).run_once()
    monkeypatch.undo()
    item = repos.batches.list_items(bid)[0]
    assert item["status"] == "Queued" and item["payload"]["source"] == "fixed" and item["reason_code"] is None
    assert repos.batches.get(bid)["failed_count"] == 0


def test_confirm_stamps_the_previews_it_confirmed(client, db):
    # 확인 = 그 순간 ConfirmPending 인 자식 잡에 도장(confirmed_fingerprint = 미리보기 지문) + 감사 행에 회차·도장 수.
    import json
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady", n=2)
    _, j0 = _confirmpending_child(repos, bid, 0, fp="sha256:a")
    _, j1 = _confirmpending_child(repos, bid, 1, fp="sha256:b")
    assert client.post(f"/api/admin/batches/{bid}:confirm", json={"preview_round": 0}).status_code == 200
    assert repos.data_jobs.get_job(j0)["confirmed_fingerprint"] == "sha256:a"
    assert repos.data_jobs.get_job(j1)["confirmed_fingerprint"] == "sha256:b"
    row = db.query_one("SELECT after_state FROM audit_log WHERE operation = 'confirm' AND target_key = :b", {"b": bid})
    after = json.loads(row["after_state"])
    assert after["preview_round"] == 0 and after["stamped"] == 2


def test_running_batch_does_not_execute_a_preview_nobody_confirmed(client):
    # 업그레이드 전 옛 코드가 Running sync 배치에 만든 미리보기(도장 없음)는 배포 뒤에도 자동 실행되지 않는다 --
    # 루프가 배치를 Previewing 으로 되돌려 다시 확인받게 한다.
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady")
    _confirmpending_child(repos, bid, 0, fp="sha256:seen")
    assert repos.batches.confirm(bid, actor="ops", summary={}, expected_round=0)
    repos.batches.add_item(bid, dict(SYNC_ITEM, destination="old-code"))   # 옛 코드: Running 에 추가해도 상태 그대로
    _, j1 = _confirmpending_child(repos, bid, 1, fp="sha256:never-seen")     # 옛 루프가 만든 미리보기(도장 없음)
    BatchOrchestrator(repos, settings=_S()).run_once()
    assert repos.batches.get(bid)["status"] == "Previewing"
    assert repos.data_jobs.get_job(j1)["state"] == "ConfirmPending"
    assert repos.data_jobs.get_job(j1)["confirmed_fingerprint"] is None


def test_a_re_previewed_child_needs_a_new_confirm(client, db):
    # 확인 뒤 미리보기 지문이 바뀌면(다시 미리보기) 도장과 달라 실행하지 않고 다시 확인받는다.
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady")
    _, jid = _confirmpending_child(repos, bid, 0, fp="sha256:old")
    assert repos.batches.confirm(bid, actor="ops", summary={}, expected_round=0)
    db.execute("UPDATE data_jobs SET preview_fingerprint = 'sha256:new' WHERE job_id = :j", {"j": jid})
    BatchOrchestrator(repos, settings=_S()).run_once()
    assert repos.batches.get(bid)["status"] == "Previewing"
    assert repos.data_jobs.get_job(jid)["state"] == "ConfirmPending"


def _executing_child(repos, bid, seq, fp):
    rid, jid = _confirmpending_child(repos, bid, seq, fp=fp)
    repos.data_jobs.set_confirmed(jid, fp)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="batch-orchestrator")
    return rid, jid


def _preview_done(repos, item, dest, fp):
    plan = repos.data_jobs.create_plan(item["request_id"], actor="planner")
    jid = repos.data_jobs.create_job(item["request_id"], plan, operation="sync", priority="mid", source_storage="s1",
                                     destination_storage="s2", source="a", destination=dest, options={},
                                     tool="dsync", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint=fp, expires_at="2099-01-01T00:00:00Z", artifact_uri="x")
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    return jid


def test_executing_children_do_not_block_the_re_review(client):
    # Running 에서 항목 추가로 Previewing 에 돌아와도, 이미 실행 중인 자식이 끝날 때까지 재확인조차 못 하던 정체가
    # 없다: 새 항목 미리보기가 끝나면 바로 확인 대기, 재확인하면 기다리던 자식이 슬롯대로 실행된다. 실행 중 자식은
    # 슬롯을 차지한다(동시 실행 상한 2 유지).
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady", n=2)
    _executing_child(repos, bid, 0, "sha256:long")                       # 오래 도는 자식(이미 확인됨)
    _, waiting = _confirmpending_child(repos, bid, 1, fp="sha256:wait")  # 확인받고 슬롯을 기다리던 자식
    repos.batches.set_status(bid, "Running")
    r = client.post(f"/api/admin/batches/{bid}/items", json=dict(SYNC_ITEM, destination="new"))
    assert r.json()["status"] == "Previewing"
    orch = BatchOrchestrator(repos, settings=_S())
    orch.run_once()                                                        # 빈 슬롯 1개로 새 항목 미리보기 시작
    new = [it for it in repos.batches.list_items(bid) if it["seq"] == 2][0]
    assert new["status"] == "Materialized"
    j2 = _preview_done(repos, new, "new", "sha256:new")
    orch.run_once()
    b = repos.batches.get(bid)
    assert b["status"] == "PreviewReady" and b["preview_round"] == 1     # 실행 중 자식이 있어도 확인 대기
    assert client.post(f"/api/admin/batches/{bid}:confirm", json={"preview_round": 1}).status_code == 200
    orch.run_once()                                                        # 상한 2: 실행 중 1 + 하나 더
    states = sorted(repos.data_jobs.get_job(j)["state"] for j in (waiting, j2))
    assert states == ["ConfirmPending", "Executing"]


def test_previewing_batch_does_not_reset_an_expired_preview_itself(client):
    # 만료는 stepper 가 Rejected(preview_expired)로 끝낸다 -- orchestrator 가 Queued 로 되돌려 다시 미리보기하던
    # (같은 패스에서 stepper 가 먼저 돌아 사실상 죽은) 분기는 없앴다. 항목은 자식이 종단될 때까지 Materialized.
    repos = client.app.state.repos
    bid = _batch(repos, status="Previewing", n=2)
    rid, _ = _confirmpending_child(repos, bid, 0, expires="2000-01-01T00:00:00Z")
    BatchOrchestrator(repos, settings=_S()).run_once()
    first = repos.batches.list_items(bid)[0]
    assert first["status"] == "Materialized" and first["request_id"] == rid


def test_confirm_requires_the_preview_round_the_operator_saw(client):
    # ABA: 운영자가 회차 1 의 확인 대기를 보는 사이 항목이 추가돼 Previewing → 다시 확인 대기(회차 2) -- 옛 회차의
    # 확인은 409 batch_preview_changed 이고 아무것도 실행되지 않는다. 회차 없는 확인(옛 포탈 탭·스크립트)은 422.
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="Previewing")
    _, j0 = _confirmpending_child(repos, bid, 0)
    orch = BatchOrchestrator(repos, settings=_S())
    orch.run_once()
    seen = client.get(f"/api/admin/batches/{bid}").json()
    assert seen["status"] == "PreviewReady" and seen["preview_round"] == 1
    client.post(f"/api/admin/batches/{bid}/items", json=dict(SYNC_ITEM, destination="later"))
    orch.run_once()
    later = [it for it in repos.batches.list_items(bid) if it["seq"] == 1][0]
    j1 = _preview_done(repos, later, "later", "sha256:later")
    orch.run_once()
    assert repos.batches.get(bid)["preview_round"] == 2                  # 다시 확인 대기, 회차가 바뀌었다
    assert client.post(f"/api/admin/batches/{bid}:confirm").json()["detail"] == "preview_round_required"
    r = client.post(f"/api/admin/batches/{bid}:confirm", json={"preview_round": 1})
    assert r.status_code == 409 and r.json()["detail"] == "batch_preview_changed"
    assert repos.batches.get(bid)["status"] == "PreviewReady"
    orch.run_once()
    assert {repos.data_jobs.get_job(j)["state"] for j in (j0, j1)} == {"ConfirmPending"}
    assert client.post(f"/api/admin/batches/{bid}:confirm", json={"preview_round": 2}).status_code == 200


def test_repo_confirm_cas_checks_the_round(client):
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady")
    _confirmpending_child(repos, bid, 0)
    assert repos.batches.confirm(bid, actor="x", summary={}, expected_round=5) is False
    assert repos.batches.get(bid)["status"] == "PreviewReady"
    assert repos.batches.confirm(bid, actor="x", summary={}, expected_round=0) is True


# --- 3차 리뷰(같은 날): 확인 대기 중 기록, 확인할 것 없는 확인 대기, 특권 계정 셀프 재설정 ---------------------

def _finish(repos, rid, jid, state=DataJobState.SUCCEEDED):
    repos.data_jobs.set_job_state(jid, state, actor="stepper")
    repos.requests.finalize_from_job(rid, state, reason_code=None, actor="stepper")


def test_children_finishing_while_awaiting_confirm_are_recorded_and_complete_the_batch(client):
    # 이미 확인돼 실행 중이던 자식이 확인 대기 사이에 끝나면 루프가 기록만 하러 돈다 -- 항목이 Materialized 로 남아
    # 실패분 재실행·완료가 막히고 취소가 "취소됨" 으로 덮던 상태가 없다. 전부 끝나면 확인 없이도 완료된다.
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady", n=2)
    rid0, j0 = _executing_child(repos, bid, 0, "sha256:x")
    rid1, j1 = _confirmpending_child(repos, bid, 1, expires="2099-01-01T00:00:00Z")
    orch = BatchOrchestrator(repos, settings=_S())
    _finish(repos, rid0, j0)
    orch.run_once()
    b = repos.batches.get(bid)
    assert b["status"] == "PreviewReady" and b["succeeded_count"] == 1
    assert repos.batches.list_items(bid)[0]["status"] == "Succeeded"
    assert repos.data_jobs.get_job(j1)["state"] == "ConfirmPending"     # 확인 대기는 그대로 -- 아무것도 컨펌하지 않는다
    _finish(repos, rid1, j1, DataJobState.PREVIEW_EXPIRED)               # 남은 미리보기가 만료(stepper)
    orch.run_once()
    b = repos.batches.get(bid)
    assert b["status"] == "Completed" and b["failed_count"] == 1


def test_no_empty_confirm_wait_when_only_confirmed_children_remain(client):
    # Running 배치에 항목을 추가했다가 지웠다 -- 확인할 미리보기가 없으니 확인 대기(0개 확인)가 아니라 Running 으로
    # 돌아가 실행 중 자식을 마저 기록·완료한다(회차도 그대로).
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(repos, status="PreviewReady")
    rid0, j0 = _executing_child(repos, bid, 0, "sha256:x")
    repos.batches.set_status(bid, "Running")
    seq = client.post(f"/api/admin/batches/{bid}/items", json=dict(SYNC_ITEM, destination="oops")).json()["seq"]
    assert repos.batches.get(bid)["status"] == "Previewing"
    assert client.delete(f"/api/admin/batches/{bid}/items/{seq}").status_code == 200
    orch = BatchOrchestrator(repos, settings=_S())
    orch.run_once()
    b = repos.batches.get(bid)
    assert b["status"] == "Running" and b["preview_round"] == 0
    _finish(repos, rid0, j0)
    orch.run_once()
    b = repos.batches.get(bid)
    assert b["status"] == "Completed" and b["succeeded_count"] == 1
