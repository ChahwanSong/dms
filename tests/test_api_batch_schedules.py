"""배치 스케줄 API(2026-11-16, routes_batch_schedules): 게이트(무장 = 특권 3중 게이트 + 유지보수 거부, 끄기 = require_admin,
삭제 = require_session_admin) · 파괴적 스케줄 동의 · 응답 모양(지문 원문 없음, k8s child_count·confirm_wait) · 미리보기 ·
배치 삭제 두 경로의 캐스케이드와 BATCH_DELETE_TABLES 그물(잠금 순서 스케줄 → 회차 -- 설계 비판 #10) · 취소와 같은 요청의
스케줄 끄기(설계 비판 #4) · :rescan 의 상태 CAS."""
import inspect
import json

import pytest
from fastapi.testclient import TestClient

from dms.batch_scheduler import parse_utc
from dms.config import Settings
from dms.db import utc_now_iso
from dms.repositories.batches import BATCH_DELETE_EXEMPT_TABLES, BATCH_DELETE_TABLES, BatchesRepository
from dms.repositories.request_purges import MAX_BATCH_DELETE_CHILDREN, RequestPurgesRepository

TOKEN = {"Authorization": "Bearer tok-shared"}
SCAN_ITEM = {"storage": "s1", "target": "a"}
SYNC_ITEM = {"source_storage": "s1", "source": "a", "destination_storage": "s2", "destination": "b"}
RM_ITEM = {"storage": "s1", "target": "old"}
DAILY = {"kind": "daily", "spec": {"hour": 2, "minute": 0}}
URL = "/api/admin/batch-schedules"


def _app(db, **overrides):
    from dms.api.app import create_app
    base = {"DMS_DATABASE_URL": "unused", "DMS_SHARED_TOKEN": "tok-shared",
            "DMS_ADMIN_TOKEN": "tok-admin", "DMS_SESSION_SECRET": "sess",
            "DMS_ALLOW_PRIVILEGED_REQUESTERS": "true", "DMS_PRIVILEGED_REQUESTERS": "ops", **overrides}
    return create_app(Settings.from_env(base), db)


def _login(client, username, role="admin"):
    # from_env 앱은 라이브 자세(비밀번호 봉인 필수) -- 로그인은 봉인해 보낸다.
    from dms.api.password_transport import seal_with_info
    if client.app.state.repos.accounts.get(username) is None:
        client.app.state.repos.accounts.create(username, "p", role, actor="t")
    info = client.get("/api/auth/transport-key").json()
    r = client.post("/api/auth/login", json={
        "username": username,
        "password_enc": seal_with_info(info, "p", purpose="login", username=username)})
    assert r.status_code == 200, r.text


@pytest.fixture
def client(db):
    c = TestClient(_app(db))
    repos = c.app.state.repos
    for name in ("s1", "s2"):
        repos.storages.create(storage_name=name, mount_path=f"/mnt/{name}", managed_root=f"/mnt/{name}/managed",
                              backend_type="nfs", actor="t")
        repos.storages.set_status(name, "Ready")
    _login(c, "ops")
    return c


def _repos(client):
    return client.app.state.repos


def _batch(client, *, op="scan", status="Completed", options=None, requester="ops", auth="session", auto_by=None,
           n=1):
    repos = _repos(client)
    if repos.accounts.get(requester) is None:
        repos.accounts.create(requester, "p", "admin", actor="t")
    base = {"sync": SYNC_ITEM, "rm": RM_ITEM}.get(op, SCAN_ITEM)
    key = "destination" if op == "sync" else "target"
    items = [{**base, key: f"{base[key]}{i}"} for i in range(n)]
    if options is None:
        options = {"recursive": True} if op == "rm" else {}
    bid = repos.batches.create(operation=op, requester_id=requester, actor=requester, max_concurrency=2,
                               options=options, items=items, note=None, status=status, auth_method=auth,
                               auto_confirm_by=auto_by)
    for seq in range(n):
        repos.batches.set_item_status(bid, seq, "Succeeded")
    return bid


def _create(client, bid, *, headers=None, **body):
    return client.post(URL, json={"batch_id": bid, **DAILY, **body}, headers=headers or {})


def _audit(client, mutation_class, operation):
    return _repos(client).db.query(
        "SELECT actor, target_key, before_state, after_state FROM audit_log WHERE mutation_class = :m AND operation = :o",
        {"m": mutation_class, "o": operation})


def _maintenance(client, on: bool):
    _repos(client).db.execute("UPDATE control_state SET maintenance = :m WHERE id = 1", {"m": 1 if on else 0})


# --- 생성·응답 모양 -------------------------------------------------------------------------------------------------

def test_create_returns_the_row_without_the_raw_fingerprint(client):
    bid = _batch(client, n=2)
    r = _create(client, bid)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["batch_id"] == bid and row["kind"] == "daily" and row["spec"] == {"hour": 2, "minute": 0}
    assert row["enabled"] is True and row["armed_by"] == "ops" and row["created_by"] == "ops"
    assert row["next_run_at"] > utc_now_iso() and row["last_outcome"] is None
    assert "ack_fingerprint" not in row and row["acknowledged_irreversible"] is False
    assert row["destructive"] is False and row["confirm_wait"] is None
    assert row["batch"]["operation"] == "scan" and row["batch"]["item_count"] == 2
    assert row["batch"]["storage"] == "s1" and row["batch"]["auto_confirm"] is False
    assert (row["child_count"], row["child_count_capped"], row["child_count_limit"]) == \
        (0, False, MAX_BATCH_DELETE_CHILDREN)
    assert row["child_count_limit"] == 5000
    # 회차 보존(2026-11-16): 설정값과 정리 결과 -- 아직 정리 없음은 null(0 이 아니다)
    assert (row["keep_rounds"], row["pruned_at"], row["pruned_total"]) == (5, None, None)
    a = _audit(client, "batch_schedule", "create")
    assert len(a) == 1 and a[0]["actor"] == "ops" and a[0]["target_key"] == bid
    # KST 02:00 = UTC 17:00
    assert parse_utc(row["next_run_at"]).hour == 17


def test_create_disabled_has_no_next_run(client):
    r = _create(client, _batch(client), enabled=False)
    assert r.status_code == 201 and r.json()["enabled"] is False and r.json()["next_run_at"] is None


def test_list_shows_confirm_wait_for_unconfirmed_daily_sync_and_child_count(client):
    bid = _batch(client, op="sync")
    created = _create(client, bid)
    assert created.status_code == 201
    # 생성 응답에도 같은 경고(설계 비판 #8) -- 판정은 그대로 건너뜀 기록이고 이것은 안내다
    assert created.json()["confirm_wait"]["unconfirmed_every_seconds"] == 172800
    repos = _repos(client)
    for i in range(3):                               # 지난 회차의 자식 요청(재실행 이력 포함 전부 센다)
        repos.requests.create(operation="sync", requester_id="ops", actor="ops", resource_key=f"k{i}",
                              payload=dict(SYNC_ITEM), priority="mid", batch_id=bid, auth_method="session")
    rows = client.get(URL).json()
    assert len(rows) == 1
    assert rows[0]["confirm_wait"] == {"period_seconds": 86400, "preview_ttl_seconds": 86400,
                                       "unconfirmed_every_seconds": 172800}
    assert rows[0]["child_count"] == 3 and rows[0]["child_count_capped"] is False
    assert rows[0]["batch"]["source_storage"] == "s1" and rows[0]["batch"]["destination_storage"] == "s2"


def test_auto_confirm_sync_has_no_confirm_wait(client):
    bid = _batch(client, op="sync", auto_by="ops")
    assert _create(client, bid).json()["confirm_wait"] is None


def test_list_and_runs_are_readable_with_the_shared_token(client):
    sid = _create(client, _batch(client)).json()["schedule_id"]
    assert client.get(URL, headers=TOKEN).status_code == 200
    r = client.get(f"{URL}/{sid}/runs", headers=TOKEN)
    assert r.status_code == 200 and r.json() == []
    assert client.get(f"{URL}/nope/runs").json()["detail"] == "batch_schedule_not_found"


# --- 게이트 ---------------------------------------------------------------------------------------------------------

def test_shared_token_cannot_arm(client):
    bid = _batch(client)
    r = _create(client, bid, headers=TOKEN)
    assert r.status_code == 403 and r.json()["detail"] == "privileged_not_authorized"
    sid = _create(client, bid, enabled=False).json()["schedule_id"]
    assert client.patch(f"{URL}/{sid}", json=DAILY, headers=TOKEN).status_code == 403
    assert client.post(f"{URL}/{sid}:enable", json={}, headers=TOKEN).status_code == 403


def test_admin_outside_the_privileged_list_cannot_arm(client, db):
    bid = _batch(client)
    other = TestClient(client.app)
    _login(other, "bob")
    r = _create(other, bid)
    assert r.status_code == 403 and r.json()["detail"] == "privileged_not_authorized"


def test_maintenance_blocks_arming_but_not_the_brakes(client):
    bid = _batch(client)
    sid = _create(client, bid).json()["schedule_id"]
    _maintenance(client, True)
    assert _create(client, _batch(client)).status_code == 503
    assert client.patch(f"{URL}/{sid}", json=DAILY).status_code == 503
    r = client.post(f"{URL}/{sid}:disable")
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert client.post(f"{URL}/{sid}:enable", json={}).status_code == 503
    assert client.delete(f"{URL}/{sid}").status_code == 200


def test_disable_is_idempotent_and_token_can_disable(client):
    sid = _create(client, _batch(client)).json()["schedule_id"]
    r = client.post(f"{URL}/{sid}:disable", headers=TOKEN)
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["next_run_at"] is None
    assert client.post(f"{URL}/{sid}:disable").status_code == 200
    a = _audit(client, "batch_schedule", "disable")
    assert len(a) == 1 and a[0]["actor"] != "shared-token"          # 토큰 호출은 출처를 표시한 actor(audit_actor)
    assert client.post(f"{URL}/nope:disable").status_code == 404


def test_delete_requires_a_session_admin(client):
    sid = _create(client, _batch(client)).json()["schedule_id"]
    r = client.delete(f"{URL}/{sid}", headers=TOKEN)
    assert r.status_code == 403 and r.json()["detail"] == "admin_session_required"
    r = client.delete(f"{URL}/{sid}")
    assert r.status_code == 200 and r.json() == {"deleted": sid}
    assert client.get(URL).json() == []
    assert len(_audit(client, "batch_schedule", "delete")) == 1
    assert client.delete(f"{URL}/{sid}").status_code == 404


# --- 파괴적 스케줄 동의 ---------------------------------------------------------------------------------------------

def test_rm_schedule_requires_explicit_irreversible_ack(client):
    bid = _batch(client, op="rm")
    r = _create(client, bid)
    assert r.status_code == 422 and r.json()["detail"] == "batch_schedule_ack_required"
    assert _create(client, bid, acknowledge_irreversible=False).json()["detail"] == "batch_schedule_ack_required"
    assert _create(client, bid, acknowledge_irreversible="true").status_code == 422     # StrictBool
    r = _create(client, bid, acknowledge_irreversible=True)
    assert r.status_code == 201, r.text
    assert r.json()["acknowledged_irreversible"] is True and r.json()["destructive"] is True
    after = json.loads(_audit(client, "batch_schedule", "create")[0]["after_state"])
    assert after["acknowledged_irreversible"] is True and after["fingerprint"].startswith("sha256:")


def test_sync_with_delete_requires_ack_and_plain_sync_does_not(client):
    bid = _batch(client, op="sync", options={"delete": True})
    assert _create(client, bid).json()["detail"] == "batch_schedule_ack_required"
    assert _create(client, bid, acknowledge_irreversible=True).status_code == 201
    plain = _batch(client, op="sync")
    r = _create(client, plain)
    assert r.status_code == 201 and r.json()["acknowledged_irreversible"] is False


def test_patch_rearms_and_destructive_needs_a_new_ack(client):
    bid = _batch(client, op="rm")
    sid = _create(client, bid, acknowledge_irreversible=True).json()["schedule_id"]
    weekly = {"kind": "weekly", "spec": {"weekdays": [1, 4], "hour": 3, "minute": 0}}
    assert client.patch(f"{URL}/{sid}", json=weekly).json()["detail"] == "batch_schedule_ack_required"
    _repos(client).db.execute("UPDATE accounts SET root_granted = 1 WHERE username = 'ops'")
    r = client.patch(f"{URL}/{sid}", json={**weekly, "acknowledge_irreversible": True})
    assert r.status_code == 200 and r.json()["kind"] == "weekly"
    assert r.json()["spec"]["weekdays"] == [1, 4] and r.json()["armed_by"] == "ops"
    assert client.patch("/api/admin/batch-schedules/nope", json=weekly).status_code == 404


def test_enable_after_auto_pause_requires_ack_for_destructive(client):
    bid = _batch(client, op="rm")
    sid = _create(client, bid, acknowledge_irreversible=True).json()["schedule_id"]
    _repos(client).db.execute(
        "UPDATE batch_schedules SET enabled = 0, next_run_at = NULL, paused_reason_code = 'batch_schedule_batch_changed' "
        "WHERE schedule_id = :s", {"s": sid})
    r = client.post(f"{URL}/{sid}:enable", json={})
    assert r.status_code == 422 and r.json()["detail"] == "batch_schedule_ack_required"
    r = client.post(f"{URL}/{sid}:enable", json={"acknowledge_irreversible": True})
    assert r.status_code == 200
    row = r.json()
    assert row["enabled"] is True and row["paused_reason_code"] is None and row["next_run_at"] > utc_now_iso()
    assert client.post(f"{URL}/{sid}:enable", json={"acknowledge_irreversible": True}).status_code == 200  # 멱등


# --- 거부 -----------------------------------------------------------------------------------------------------------

def test_create_rejections(client):
    bid = _batch(client)
    assert _create(client, "nope").json()["detail"] == "batch_not_found"
    assert _create(client, "nope").status_code == 404
    r = client.post(URL, json={"batch_id": bid, "kind": "daily", "spec": {"hour": 25, "minute": 0}})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_batch_schedule"
    r = client.post(URL, json={"batch_id": bid, "kind": "weekly", "spec": {"weekdays": [], "hour": 1, "minute": 0}})
    assert r.json()["detail"] == "invalid_batch_schedule"
    assert _create(client, bid).status_code == 201
    r = _create(client, bid)
    assert r.status_code == 409 and r.json()["detail"] == "batch_schedule_exists"


def test_arming_uses_the_same_predicate_as_the_rounds(client):
    """켤 때 통과한 배치가 첫 회차에서 같은 규칙으로 멈추지 않게 -- 무장도 arm_problem 으로 거부한다."""
    lost = _batch(client, requester="lost")
    assert _create(client, lost).json()["detail"] == "batch_schedule_requester_not_privileged"
    token_batch = _batch(client, auth="token")
    assert _create(client, token_batch).json()["detail"] == "batch_schedule_requester_not_privileged"
    gone = _batch(client)
    _repos(client).db.execute("DELETE FROM storages WHERE storage_name = 's1'")
    r = _create(client, gone)
    assert r.status_code == 422 and r.json()["detail"] == "storage_missing"


# --- 미리보기 -------------------------------------------------------------------------------------------------------

def test_preview_returns_five_server_computed_slots(client):
    r = client.post(f"{URL}:preview", json={"kind": "monthly", "spec": {"day": 31, "hour": 5, "minute": 0}})
    assert r.status_code == 200
    runs = r.json()["next_runs"]
    assert len(runs) == 5 and runs == sorted(runs) and runs[0] > utc_now_iso()
    assert r.json()["confirm_wait"] is None
    assert client.post(f"{URL}:preview", json={"kind": "x", "spec": {}}).json()["detail"] == "invalid_batch_schedule"
    assert client.post(f"{URL}:preview", json=DAILY, headers=TOKEN).status_code == 200


def test_preview_with_a_batch_warns_about_confirm_wait(client):
    bid = _batch(client, op="rm")
    r = client.post(f"{URL}:preview", json={**DAILY, "batch_id": bid})
    assert r.json()["confirm_wait"]["unconfirmed_every_seconds"] == 172800
    r = client.post(f"{URL}:preview", json={**DAILY, "batch_id": "nope"})
    assert r.status_code == 200 and r.json()["confirm_wait"] is None


def test_preview_carries_keep_rounds_limit_and_prune_preview(client):
    """추가 창의 보존 안내 재료(2026-11-16): keep_rounds·child_count_limit 은 늘, batch_id 가 있으면 prune_preview(실제 정리와
    같은 경계 계산 -- 기록 6회차 배치면 곧 정리될 작업 = 첫 회차(기록 이전) 자식 수)."""
    r = client.post(f"{URL}:preview", json=DAILY).json()
    assert r["keep_rounds"] == 5 and r["child_count_limit"] == MAX_BATCH_DELETE_CHILDREN and "prune_preview" not in r
    repos = _repos(client)
    bid = _batch(client, n=2)
    first = [repos.requests.create(operation="scan", requester_id="ops", actor="ops", resource_key=f"f{i}",
                                   payload=dict(SCAN_ITEM), priority="mid", batch_id=bid, auth_method="session")
             for i in range(2)]
    for rnd in range(6):
        assert repos.batches.rescan_if_terminal(bid, actor="ops") is not None
        for i in range(2):
            repos.requests.create(operation="scan", requester_id="ops", actor="ops", resource_key=f"r{rnd}-{i}",
                                  payload=dict(SCAN_ITEM), priority="mid", batch_id=bid, auth_method="session")
        repos.db.execute("UPDATE batches SET status = 'Completed' WHERE batch_id = :b", {"b": bid})
        repos.db.execute("UPDATE batch_items SET status = 'Succeeded' WHERE batch_id = :b", {"b": bid})
    pp = client.post(f"{URL}:preview", json={**DAILY, "batch_id": bid}).json()["prune_preview"]
    # 기록 6회차 · 보존 5 → 보존하는 가장 오래된 회차 = 2번째 기록 회차, 그 전 = 첫 실행 자식 2 + 1번째 기록 회차 자식 2
    assert pp == {"rounds_recorded": 6, "would_prune": len(first) + 2, "would_prune_capped": False}
    fresh = _batch(client)
    assert client.post(f"{URL}:preview", json={**DAILY, "batch_id": fresh}).json()["prune_preview"] == \
        {"rounds_recorded": 0, "would_prune": 0, "would_prune_capped": False}
    assert client.post(f"{URL}:preview", json={**DAILY, "batch_id": "nope"}).json()["prune_preview"]["would_prune"] == 0


def test_list_and_batch_summary_carry_prune_results(client):
    bid = _batch(client)
    sid = _create(client, bid).json()["schedule_id"]
    repos = _repos(client)
    repos.db.execute("UPDATE batch_schedules SET pruned_at = '2026-11-16T10:00:00Z', pruned_total = 0 "
                     "WHERE schedule_id = :s", {"s": sid})
    row = client.get(URL).json()[0]
    assert row["pruned_at"] == "2026-11-16T10:00:00Z" and row["pruned_total"] == 0   # 0 은 정상값
    sched = client.get(f"/api/admin/batches/{bid}?view=summary").json()["schedule"]
    assert sched["pruned_total"] == 0 and sched["keep_rounds"] == 5
    listed = {r["batch_id"]: r for r in client.get("/api/admin/batches").json()}
    assert listed[bid]["schedule"]["keep_rounds"] == 5


# --- 배치 삭제 캐스케이드 · 그물 ------------------------------------------------------------------------------------

def _schedule_with_runs(client, bid):
    sid = _create(client, bid).json()["schedule_id"]
    repos = _repos(client)
    row = repos.batch_schedules.get(sid)
    repos.batch_schedules._insert_run(row, outcome="skipped", reason_code="batch_schedule_batch_running",
                                      now=utc_now_iso(), batch_status="Running")
    return sid


def _schedule_rows(client, bid):
    db = _repos(client).db
    return (db.query_one("SELECT COUNT(*) AS n FROM batch_schedules WHERE batch_id = :b", {"b": bid})["n"],
            db.query_one("SELECT COUNT(*) AS n FROM batch_schedule_runs WHERE batch_id = :b", {"b": bid})["n"])


def _round_rows(client, bid):
    return _repos(client).db.query_one("SELECT COUNT(*) AS n FROM batch_rounds WHERE batch_id = :b", {"b": bid})["n"]


def test_batch_record_delete_cascades_schedule_and_runs(client):
    bid = _batch(client)
    _schedule_with_runs(client, bid)
    assert client.post(f"/api/admin/batches/{bid}:rescan").status_code == 200
    _repos(client).db.execute("UPDATE batches SET status = 'Completed' WHERE batch_id = :b", {"b": bid})
    assert _schedule_rows(client, bid) == (1, 1) and _round_rows(client, bid) == 1
    r = client.delete(f"/api/admin/batches/{bid}")
    assert r.status_code == 200, r.text
    assert _schedule_rows(client, bid) == (0, 0) and _round_rows(client, bid) == 0
    a = _audit(client, "batch_schedule", "delete")
    assert len(a) == 1 and json.loads(a[0]["before_state"])["run_count"] == 1


def test_batch_unit_delete_cascades_schedule_and_runs(client):
    bid = _batch(client)
    _schedule_with_runs(client, bid)
    assert _repos(client).batches.rescan_if_terminal(bid, actor="ops") is not None
    _repos(client).db.execute("UPDATE batches SET status = 'Completed' WHERE batch_id = :b", {"b": bid})
    r = _repos(client).request_purges.delete_batch(bid, expected_request_count=0, actor="ops", artifact_base=None,
                                                   quiet_seconds=60, max_children=MAX_BATCH_DELETE_CHILDREN)
    assert r["deleted"] is True, r
    assert _schedule_rows(client, bid) == (0, 0) and _round_rows(client, bid) == 0
    assert len(_audit(client, "batch_schedule", "delete")) == 1


# ('batch_schedule', 'delete') 감사 before 의 키 -- 세 경로(스케줄 DELETE 라우트·배치 기록 삭제·배치 단위 삭제)가 같은 한 벌
# (batches.schedule_delete_audit_before). d29 라이브 실증: 라우트 쪽 감사 977 에 pruned_total·pruned_at 이 없었다.
_SCHEDULE_DELETE_AUDIT_KEYS = {
    "schedule_id", "batch_id", "kind", "spec", "enabled", "next_run_at", "armed_by", "acknowledged_irreversible",
    "last_run_at", "last_outcome", "last_reason_code", "paused_reason_code", "pruned_total", "pruned_at", "run_count",
    "deleted_with_batch"}


def _delete_schedule_route(client, bid, sid):
    assert client.delete(f"{URL}/{sid}").status_code == 200


def _delete_batch_record(client, bid, sid):
    r = client.delete(f"/api/admin/batches/{bid}")
    assert r.status_code == 200, r.text


def _delete_batch_unit(client, bid, sid):
    r = _repos(client).request_purges.delete_batch(bid, expected_request_count=0, actor="ops", artifact_base=None,
                                                   quiet_seconds=60, max_children=MAX_BATCH_DELETE_CHILDREN)
    assert r["deleted"] is True, r


@pytest.mark.parametrize("delete,with_batch", [(_delete_schedule_route, False), (_delete_batch_record, True),
                                               (_delete_batch_unit, True)])
def test_schedule_delete_audit_has_the_same_before_shape_on_every_path(client, delete, with_batch):
    bid = _batch(client)
    sid = _schedule_with_runs(client, bid)
    _repos(client).db.execute("UPDATE batch_schedules SET pruned_total = 12, pruned_at = '2026-11-16T12:42:00Z' "
                              "WHERE schedule_id = :s", {"s": sid})
    delete(client, bid, sid)
    a = _audit(client, "batch_schedule", "delete")
    assert len(a) == 1 and a[0]["target_key"] == bid and a[0]["after_state"] is None
    before = json.loads(a[0]["before_state"])
    assert set(before) == _SCHEDULE_DELETE_AUDIT_KEYS
    assert before["pruned_total"] == 12 and before["pruned_at"] == "2026-11-16T12:42:00Z"
    assert before["schedule_id"] == sid and before["spec"] == DAILY["spec"] and before["enabled"] is True
    assert before["run_count"] == 1 and before["deleted_with_batch"] is with_batch
    assert before["last_outcome"] is None and before["acknowledged_irreversible"] is False


def test_schedule_delete_audit_keeps_never_pruned_as_null_not_zero(client):
    """정리 전에 지운 스케줄 -- pruned_total null(아직 정리 없음)은 0 으로 바뀌지 않는다(null ≠ 0). 변조 값도 모름(None)."""
    for raw, want in ((None, None), (0, 0), (-1, None)):
        bid = _batch(client)
        sid = _create(client, bid).json()["schedule_id"]
        _repos(client).db.execute("UPDATE batch_schedules SET pruned_total = :n WHERE schedule_id = :s",
                                  {"n": raw, "s": sid})
        assert client.delete(f"{URL}/{sid}").status_code == 200
        before = json.loads([r for r in _audit(client, "batch_schedule", "delete") if r["target_key"] == bid][0]
                            ["before_state"])
        assert before["pruned_total"] == want and before["pruned_at"] is None and before["run_count"] == 0


def test_batch_delete_tables_cover_every_batch_id_column(db):
    """batch_id 로 배치를 가리키는 새 테이블은 배치 삭제 두 경로에도(BATCH_DELETE_TABLES) -- 아니면 지운 배치의 스케줄이 고아로
    남아 컨트롤러가 batch_not_found 로 일시정지를 쓴다."""
    if db.dialect == "postgresql":
        tables = {r["table_name"] for r in db.query(
            "SELECT table_name FROM information_schema.columns WHERE column_name = 'batch_id' "
            "AND table_schema = current_schema()")}
    else:
        tables = set()
        for t in db.query("SELECT name FROM sqlite_master WHERE type = 'table'"):
            if any(c["name"] == "batch_id" for c in db.query(f"PRAGMA table_info({t['name']})")):
                tables.add(t["name"])
    assert tables == set(BATCH_DELETE_TABLES) | set(BATCH_DELETE_EXEMPT_TABLES)
    assert not set(BATCH_DELETE_TABLES) & set(BATCH_DELETE_EXEMPT_TABLES)


@pytest.mark.parametrize("path", [BatchesRepository.delete, RequestPurgesRepository.delete_batch])
def test_both_batch_delete_paths_delete_every_table_schedule_before_runs(path):
    """두 경로 소스에 테이블마다 DELETE 가 있고, 회차 경계를 스케줄보다, 스케줄을 회차 이력보다 먼저 지운다(fire 의 회차 경계 →
    스케줄 → 회차, record 의 스케줄 → 회차 순서와 같게 -- 거꾸로면 PG 에서 서로 상대 행을 기다린다)."""
    src = inspect.getsource(path)
    pos = {}
    for t in BATCH_DELETE_TABLES:
        needle = f"DELETE FROM {t} "
        assert needle in src, f"{path.__qualname__} does not delete {t}"
        pos[t] = src.index(needle)
    assert pos["batch_rounds"] < pos["batch_schedules"] < pos["batch_schedule_runs"]
    order = [BATCH_DELETE_TABLES.index(t) for t in ("batch_rounds", "batch_schedules", "batch_schedule_runs")]
    assert order == sorted(order)


# --- 취소와 같은 요청의 스케줄 끄기(설계 비판 #4) · :rescan ----------------------------------------------------------

def test_cancel_with_disable_schedule_turns_it_off_in_the_same_request(client):
    bid = _batch(client, status="Running")
    _repos(client).db.execute("UPDATE batch_items SET status = 'Queued' WHERE batch_id = :b", {"b": bid})
    sid = _create(client, bid).json()["schedule_id"]
    r = client.post(f"/api/admin/batches/{bid}:cancel", json={"disable_schedule": True})
    assert r.status_code == 200 and r.json()["status"] == "Cancelled", r.text
    s = _repos(client).batch_schedules.get(sid)
    assert s["enabled"] is False and s["next_run_at"] is None
    a = _audit(client, "batch_schedule", "disable")
    assert len(a) == 1 and json.loads(a[0]["after_state"])["via"] == "batch_cancel"


def test_cancel_response_says_which_schedule_it_turned_off(client):
    bid = _batch(client, status="Running")
    _repos(client).db.execute("UPDATE batch_items SET status = 'Queued' WHERE batch_id = :b", {"b": bid})
    sid = _create(client, bid).json()["schedule_id"]
    assert client.post(f"/api/admin/batches/{bid}:cancel", json={"disable_schedule": True}).json()["schedule_disabled"] == sid
    # 스케줄이 없는 배치 -- null(끈 것이 없다), 키 자체는 플래그를 보냈을 때만
    plain = _batch(client, status="Running")
    _repos(client).db.execute("UPDATE batch_items SET status = 'Queued' WHERE batch_id = :b", {"b": plain})
    r = client.post(f"/api/admin/batches/{plain}:cancel", json={"disable_schedule": True}).json()
    assert r["status"] == "Cancelled" and r["schedule_disabled"] is None
    assert client.post(f"/api/admin/batches/{plain}:cancel", json={"disable_schedule": "yes"}).status_code == 422


def test_batch_list_and_summary_carry_the_schedule_summary(client):
    """배치 목록 행과 상세 머리(view=summary)에 schedule 요약 -- 지문 원문 없음. view 없음·all 은 모양을 바꾸지 않는다(옛 탭·확인
    창 응답)."""
    bid = _batch(client)
    other = _batch(client)
    sid = _create(client, bid).json()["schedule_id"]
    rows = {r["batch_id"]: r for r in client.get("/api/admin/batches").json()}
    sched = rows[bid]["schedule"]
    assert sched["schedule_id"] == sid and sched["enabled"] is True and sched["kind"] == "daily"
    assert sched["spec"] == {"hour": 2, "minute": 0} and sched["next_run_at"] is not None
    assert sched["paused_reason_code"] is None and "ack_fingerprint" not in sched
    assert rows[other]["schedule"] is None
    summary = client.get(f"/api/admin/batches/{bid}?view=summary").json()
    assert summary["schedule"]["schedule_id"] == sid
    assert client.get(f"/api/admin/batches/{other}?view=summary").json()["schedule"] is None
    assert "schedule" not in client.get(f"/api/admin/batches/{bid}").json()
    assert "schedule" not in client.get(f"/api/admin/batches/{bid}?view=all").json()


def test_cancel_without_the_flag_keeps_the_schedule(client):
    bid = _batch(client, status="Running")
    _repos(client).db.execute("UPDATE batch_items SET status = 'Queued' WHERE batch_id = :b", {"b": bid})
    sid = _create(client, bid).json()["schedule_id"]
    assert client.post(f"/api/admin/batches/{bid}:cancel").json()["status"] == "Cancelled"
    assert _repos(client).batch_schedules.get(sid)["enabled"] is True


def test_cancel_that_loses_does_not_disable(client):
    bid = _batch(client)                              # Completed -- 취소할 수 없다
    sid = _create(client, bid).json()["schedule_id"]
    r = client.post(f"/api/admin/batches/{bid}:cancel", json={"disable_schedule": True})
    assert r.status_code == 409
    assert _repos(client).batch_schedules.get(sid)["enabled"] is True


def test_rescan_route_is_a_state_cas(client):
    bid = _batch(client, n=2)
    r = client.post(f"/api/admin/batches/{bid}:rescan")
    assert r.status_code == 200 and r.json() == {"status": "Running", "requeued": 2}
    r = client.post(f"/api/admin/batches/{bid}:rescan")
    assert r.status_code == 409 and r.json()["detail"] == "batch_not_rescannable"
    # 회차 경계(2026-11-16): 이긴 쪽만 1행, 누른 세션 사용자가 actor(via rescan). 응답 모양은 그대로다.
    rounds = _repos(client).db.query("SELECT via, actor, requeued FROM batch_rounds WHERE batch_id = :b", {"b": bid})
    assert rounds == [{"via": "rescan", "actor": "ops", "requeued": 2}]
