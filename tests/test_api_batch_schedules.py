"""배치 스케줄 API(2026-11-16, routes_batch_schedules) + :rescan 경합 수정 + 배치 삭제 두 경로의 스케줄 연쇄 삭제.

게이트: 무장(생성·규칙 수정·켜기) = 유지보수 거부 + 특권 3중 게이트(공유 토큰·목록 밖 관리자 403), 파괴적 스케줄은 무장마다
「되돌릴 수 없음」 동의. 끄기·삭제 = require_admin(유지보수 중에도). 조회·미리보기 = require_admin(토큰 가능)."""
import inspect
import json

import pytest
from fastapi.testclient import TestClient

from dms.batch_scheduler import KST, armed_fingerprint, parse_utc
from dms.config import Settings
from dms.repositories.batches import BATCH_DELETE_EXEMPT_TABLES, BATCH_DELETE_TABLES, BatchesRepository
from dms.repositories.request_purges import RequestPurgesRepository

TOKEN = {"Authorization": "Bearer tok-shared"}
DAILY = {"kind": "daily", "spec": {"hour": 2, "minute": 0}}
SCAN_ITEM = {"storage": "s1", "target": "a"}
SYNC_ITEM = {"source_storage": "s1", "source": "a", "destination_storage": "s2", "destination": "b"}
RM_ITEM = {"storage": "s1", "target": "old"}


def _app(db, **overrides):
    from dms.api.app import create_app
    base = {"DMS_DATABASE_URL": "unused", "DMS_SHARED_TOKEN": "tok-shared",
            "DMS_ADMIN_TOKEN": "tok-admin", "DMS_SESSION_SECRET": "sess",
            "DMS_ALLOW_PRIVILEGED_REQUESTERS": "true", "DMS_PRIVILEGED_REQUESTERS": "ops", **overrides}
    return create_app(Settings.from_env(base), db)


def _login(client, username="ops", role="admin"):
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
    _login(c)
    return c


def _batch(client, *, op="scan", status="Completed", options=None, requester="ops", auth="session", n=1):
    repos = client.app.state.repos
    base = {"sync": SYNC_ITEM, "rm": RM_ITEM}.get(op, SCAN_ITEM)
    key = "destination" if op == "sync" else "target"
    items = [{**base, key: f"{base[key]}{i}"} for i in range(n)]
    if options is None:
        options = {"recursive": True} if op == "rm" else {}
    return repos.batches.create(operation=op, requester_id=requester, actor=requester, max_concurrency=1,
                                options=options, items=items, note=None, status=status, auth_method=auth)


def _audit(db, operation, mutation_class="batch_schedule"):
    return db.query("SELECT actor, target_key, before_state, after_state FROM audit_log "
                    "WHERE mutation_class = :m AND operation = :o", {"m": mutation_class, "o": operation})


def _create(client, bid, **extra):
    return client.post("/api/admin/batch-schedules", json={"batch_id": bid, **DAILY, **extra})


# --- 생성 -------------------------------------------------------------------------------------------------------------

def test_create_scan_schedule_returns_the_row_with_batch_summary(client, db):
    bid = _batch(client)
    r = _create(client, bid)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["batch_id"] == bid and row["kind"] == "daily" and row["spec"] == {"hour": 2, "minute": 0}
    assert row["enabled"] is True and row["armed_by"] == "ops" and row["created_by"] == "ops"
    assert row["acknowledged_irreversible"] is False and row["destructive"] is False
    assert "ack_fingerprint" not in row
    assert (row["last_outcome"], row["last_run_at"], row["paused_reason_code"]) == (None, None, None)
    # 다음 실행 = 지금 뒤 첫 02:00 KST(= 17:00 UTC).
    nxt = parse_utc(row["next_run_at"])
    assert (nxt.hour, nxt.minute, nxt.second) == (17, 0, 0)
    assert row["batch"] == {"batch_id": bid, "name": None, "operation": "scan", "status": "Completed", "item_count": 1,
                            "auto_confirm": False, "options": {}, "storage": "s1", "source_storage": None,
                            "destination_storage": None}
    [audit] = _audit(db, "create")
    assert audit["actor"] == "ops" and audit["target_key"] == bid
    after = json.loads(audit["after_state"])
    assert after["schedule_id"] == row["schedule_id"] and "acknowledged_irreversible" not in after
    # 목록도 같은 모양.
    assert client.get("/api/admin/batch-schedules").json() == [row]


def test_sync_storage_fields_and_disabled_create(client):
    bid = _batch(client, op="sync")
    r = _create(client, bid, enabled=False)
    assert r.status_code == 201
    row = r.json()
    assert row["enabled"] is False and row["next_run_at"] is None
    assert (row["batch"]["storage"], row["batch"]["source_storage"], row["batch"]["destination_storage"]) == \
        (None, "s1", "s2")


@pytest.mark.parametrize("payload", [
    {"kind": "hourly", "spec": {"hour": 2, "minute": 0}},
    {"kind": "daily", "spec": {"hour": 2}},
    {"kind": "daily", "spec": "02:00"},
    {"kind": "weekly", "spec": {"weekdays": [], "hour": 2, "minute": 0}},
    {"kind": "interval", "spec": {"every_hours": 5, "hour": 0, "minute": 0}},
    {},
])
def test_invalid_rule_is_422_with_one_code(client, payload):
    bid = _batch(client)
    r = client.post("/api/admin/batch-schedules", json={"batch_id": bid, **payload})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_batch_schedule"


def test_create_errors(client, db):
    assert _create(client, "nope").json() == {"detail": "batch_not_found"}
    assert _create(client, "nope").status_code == 404
    bid = _batch(client)
    assert _create(client, bid).status_code == 201
    r = _create(client, bid)
    assert (r.status_code, r.json()["detail"]) == (409, "batch_schedule_exists")
    weird = _batch(client)
    db.execute("UPDATE batches SET operation = 'chmod' WHERE batch_id = :b", {"b": weird})
    r = _create(client, weird)
    assert (r.status_code, r.json()["detail"]) == (422, "batch_schedule_unsupported_operation")
    r = client.post("/api/admin/batch-schedules", json={"batch_id": bid, **DAILY, "enabled": "yes"})
    assert r.status_code == 422                                  # StrictBool


def test_create_rejects_a_batch_the_scheduler_could_not_run(client, db):
    # 회차 판정과 같은 술어(arm_problem) -- 켤 때 통과한 배치가 첫 회차에서 같은 규칙으로 멈추지 않게.
    token_batch = _batch(client, auth="token")
    r = _create(client, token_batch)
    assert (r.status_code, r.json()["detail"]) == (422, "batch_schedule_requester_not_privileged")
    client.app.state.repos.accounts.create("eve", "p", "admin", actor="t")
    eve_batch = _batch(client, requester="eve")
    assert _create(client, eve_batch).json()["detail"] == "batch_schedule_requester_not_privileged"
    stale = _batch(client, op="rm", options={})
    r = _create(client, stale, acknowledge_irreversible=True)
    assert (r.status_code, r.json()["detail"]) == (422, "rm_recursive_required")
    assert db.query("SELECT * FROM batch_schedules") == []


# --- 파괴적 동의 --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("op,options", [("rm", None), ("sync", {"delete": True})])
def test_destructive_schedule_needs_explicit_ack(client, db, op, options):
    bid = _batch(client, op=op, options=options)
    for extra in ({}, {"acknowledge_irreversible": False}):
        r = _create(client, bid, **extra)
        assert (r.status_code, r.json()["detail"]) == (422, "batch_schedule_ack_required")
    assert _create(client, bid, acknowledge_irreversible="yes").status_code == 422      # StrictBool
    r = _create(client, bid, acknowledge_irreversible=True)
    assert r.status_code == 201
    assert r.json()["acknowledged_irreversible"] is True and r.json()["destructive"] is True
    repos = client.app.state.repos
    stored = repos.batch_schedules.get_by_batch(bid)["ack_fingerprint"]
    assert stored == armed_fingerprint(repos, repos.batches.get(bid), repos.batches.list_items(bid))
    after = json.loads(_audit(db, "create")[0]["after_state"])
    assert after["acknowledged_irreversible"] is True and after["fingerprint"] == stored


def test_plain_sync_needs_no_ack(client):
    bid = _batch(client, op="sync")
    r = _create(client, bid)
    assert r.status_code == 201 and r.json()["destructive"] is False


# --- 게이트 -----------------------------------------------------------------------------------------------------------

def test_shared_token_cannot_arm_but_can_read(db):
    c = TestClient(_app(db))
    c.app.state.repos.accounts.create("ops", "p", "admin", actor="t")
    bid = c.app.state.repos.batches.create(operation="scan", requester_id="ops", actor="ops", max_concurrency=1,
                                           options={}, items=[SCAN_ITEM], note=None, status="Completed",
                                           auth_method="session")
    r = c.post("/api/admin/batch-schedules", json={"batch_id": bid, **DAILY}, headers=TOKEN)
    assert (r.status_code, r.json()["detail"]) == (403, "privileged_not_authorized")
    sid = c.app.state.repos.batch_schedules.create(batch_id=bid, kind="daily", spec=DAILY["spec"], enabled=False,
                                                   next_run_at=None, actor="ops", arm=lambda b, i: None)
    assert c.patch(f"/api/admin/batch-schedules/{sid}", json=DAILY, headers=TOKEN).status_code == 403
    assert c.post(f"/api/admin/batch-schedules/{sid}:enable", headers=TOKEN).status_code == 403
    assert c.get("/api/admin/batch-schedules", headers=TOKEN).status_code == 200
    assert c.post("/api/admin/batch-schedules:preview", json=DAILY, headers=TOKEN).status_code == 200
    assert c.get(f"/api/admin/batch-schedules/{sid}/runs", headers=TOKEN).status_code == 200


def test_admin_outside_the_privileged_list_cannot_arm(db):
    c = TestClient(_app(db, DMS_PRIVILEGED_REQUESTERS="someone-else"))
    _login(c, "ops")
    bid = _batch(c)
    r = _create(c, bid)
    assert (r.status_code, r.json()["detail"]) == (403, "privileged_not_authorized")


def test_non_admin_is_403(db):
    c = TestClient(_app(db))
    _login(c, "alice", role="user")
    assert c.get("/api/admin/batch-schedules").status_code == 403


def test_maintenance_blocks_arming_but_not_disable_or_delete(client, db):
    bid = _batch(client)
    sid = _create(client, bid).json()["schedule_id"]
    other = _batch(client)
    db.execute("UPDATE control_state SET maintenance = 1 WHERE id = 1")
    assert _create(client, other).json()["detail"] == "maintenance_mode"
    assert client.patch(f"/api/admin/batch-schedules/{sid}", json=DAILY).status_code == 503
    r = client.post(f"/api/admin/batch-schedules/{sid}:disable")
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert client.post(f"/api/admin/batch-schedules/{sid}:enable").status_code == 503
    assert client.delete(f"/api/admin/batch-schedules/{sid}").status_code == 200


# --- 수정·켜기·끄기·삭제 -------------------------------------------------------------------------------------------------

def test_patch_rearms_with_the_editor(db):
    c = TestClient(_app(db, DMS_PRIVILEGED_REQUESTERS="ops,kim"))
    _login(c, "ops")
    bid = _batch(c)
    sid = _create(c, bid).json()["schedule_id"]
    _login(c, "kim")
    weekly = {"kind": "weekly", "spec": {"weekdays": [5, 1], "hour": 9, "minute": 30}}
    r = c.patch(f"/api/admin/batch-schedules/{sid}", json=weekly)
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["kind"] == "weekly" and row["spec"]["weekdays"] == [1, 5] and row["armed_by"] == "kim"
    nxt = parse_utc(row["next_run_at"])
    assert (nxt.hour, nxt.minute) == (0, 30)                      # 09:30 KST
    [audit] = _audit(db, "update")
    assert json.loads(audit["before_state"])["armed_by"] == "ops" and audit["actor"] == "kim"
    assert c.patch(f"/api/admin/batch-schedules/{sid}", json={"kind": "daily"}).json()["detail"] == \
        "invalid_batch_schedule"
    assert c.patch("/api/admin/batch-schedules/nope", json=DAILY).status_code == 404


def test_patch_of_a_disabled_schedule_stays_disabled(client):
    bid = _batch(client)
    sid = _create(client, bid, enabled=False).json()["schedule_id"]
    row = client.patch(f"/api/admin/batch-schedules/{sid}", json=DAILY).json()
    assert row["enabled"] is False and row["next_run_at"] is None


def test_patch_destructive_needs_ack_again(client, db):
    bid = _batch(client, op="rm")
    sid = _create(client, bid, acknowledge_irreversible=True).json()["schedule_id"]
    r = client.patch(f"/api/admin/batch-schedules/{sid}", json=DAILY)
    assert (r.status_code, r.json()["detail"]) == (422, "batch_schedule_ack_required")
    r = client.patch(f"/api/admin/batch-schedules/{sid}", json={**DAILY, "acknowledge_irreversible": True})
    assert r.status_code == 200
    assert json.loads(_audit(db, "update")[0]["after_state"])["acknowledged_irreversible"] is True


def test_disable_then_enable(client, db):
    bid = _batch(client)
    sid = _create(client, bid).json()["schedule_id"]
    row = client.post(f"/api/admin/batch-schedules/{sid}:disable").json()
    assert (row["enabled"], row["next_run_at"], row["paused_reason_code"]) == (False, None, None)
    # 멱등: 이미 꺼진 것을 다시 끄면 아무것도 쓰지 않는다.
    client.post(f"/api/admin/batch-schedules/{sid}:disable")
    assert len(_audit(db, "disable")) == 1
    row = client.post(f"/api/admin/batch-schedules/{sid}:enable").json()
    assert row["enabled"] is True and row["next_run_at"] is not None
    client.post(f"/api/admin/batch-schedules/{sid}:enable")
    assert len(_audit(db, "enable")) == 1
    assert client.post("/api/admin/batch-schedules/nope:disable").status_code == 404
    assert client.post("/api/admin/batch-schedules/nope:enable").status_code == 404


def test_enable_computes_the_next_run_from_the_locked_rule(client, db, monkeypatch):
    # 꺼진 스케줄의 규칙이 (켜기 라우트가 읽은 뒤) PATCH 로 바뀌었다 -- 다음 실행은 저장소가 잠근 행의 새 규칙으로 잡는다.
    # 예전 라우트는 트랜잭션 밖에서 읽은 옛 규칙(매일 02:00)의 시각을 새 규칙(매주 수 05:30)과 함께 저장했다.
    repos = client.app.state.repos
    bid = _batch(client)
    sid = _create(client, bid, enabled=False).json()["schedule_id"]
    stale = repos.batch_schedules.get(sid)
    weekly = {"weekdays": [3], "hour": 5, "minute": 30}
    db.execute("UPDATE batch_schedules SET kind = 'weekly', spec = :sp WHERE schedule_id = :s",
               {"sp": json.dumps(weekly), "s": sid})
    monkeypatch.setattr(repos.batch_schedules, "get", lambda s: dict(stale))
    row = client.post(f"/api/admin/batch-schedules/{sid}:enable").json()
    slot = parse_utc(row["next_run_at"]).astimezone(KST)
    assert (slot.isoweekday(), slot.hour, slot.minute) == (3, 5, 30)


def test_enable_with_a_tampered_rule_is_422_and_writes_nothing(client, db):
    bid = _batch(client)
    sid = _create(client, bid, enabled=False).json()["schedule_id"]
    db.execute("UPDATE batch_schedules SET spec = '{broken' WHERE schedule_id = :s", {"s": sid})
    r = client.post(f"/api/admin/batch-schedules/{sid}:enable")
    assert (r.status_code, r.json()["detail"]) == (422, "invalid_batch_schedule")
    s = client.app.state.repos.batch_schedules.get(sid)
    assert (s["enabled"], s["next_run_at"]) == (False, None) and _audit(db, "enable") == []


def test_reenabling_an_auto_paused_destructive_schedule_needs_a_fresh_ack(client, db):
    repos = client.app.state.repos
    bid = _batch(client, op="rm")
    sid = _create(client, bid, acknowledge_irreversible=True).json()["schedule_id"]
    old = repos.batch_schedules.get(sid)["ack_fingerprint"]
    repos.batches.replace_items(bid, [{"storage": "s1", "target": "elsewhere"}])
    # 스케줄러의 자동 일시정지와 같은 모양.
    db.execute("""UPDATE batch_schedules SET enabled = 0, next_run_at = NULL,
                         paused_reason_code = 'batch_schedule_batch_changed' WHERE schedule_id = :s""", {"s": sid})
    r = client.post(f"/api/admin/batch-schedules/{sid}:enable")
    assert (r.status_code, r.json()["detail"]) == (422, "batch_schedule_ack_required")
    assert client.post(f"/api/admin/batch-schedules/{sid}:enable",
                       json={"acknowledge_irreversible": False}).status_code == 422
    r = client.post(f"/api/admin/batch-schedules/{sid}:enable", json={"acknowledge_irreversible": True})
    assert r.status_code == 200
    s = repos.batch_schedules.get(sid)
    assert s["enabled"] is True and s["paused_reason_code"] is None and s["armed_by"] == "ops"
    assert s["ack_fingerprint"] != old
    assert s["ack_fingerprint"] == armed_fingerprint(repos, repos.batches.get(bid), repos.batches.list_items(bid))
    before = json.loads(_audit(db, "enable")[0]["before_state"])
    assert before["paused_reason_code"] == "batch_schedule_batch_changed"


def test_delete_removes_runs_and_keeps_audit(client, db):
    repos = client.app.state.repos
    bid = _batch(client)
    sid = _create(client, bid).json()["schedule_id"]
    row = repos.batch_schedules.get(sid)
    repos.batch_schedules.record(row, outcome="skipped", reason_code="batch_schedule_empty",
                                 next_run_at="2099-01-01T00:00:00Z", now="2026-11-16T00:00:00Z",
                                 batch_status="Completed")
    assert client.delete(f"/api/admin/batch-schedules/{sid}").json() == {"deleted": sid}
    assert db.query("SELECT * FROM batch_schedule_runs") == [] and repos.batch_schedules.get(sid) is None
    assert len(_audit(db, "delete")) == 1
    assert client.delete(f"/api/admin/batch-schedules/{sid}").status_code == 404


# --- 미리보기·기록 -----------------------------------------------------------------------------------------------------

def test_preview_returns_five_future_utc_slots(client):
    r = client.post("/api/admin/batch-schedules:preview",
                    json={"kind": "monthly", "spec": {"day": 31, "hour": 2, "minute": 0}})
    assert r.status_code == 200
    runs = r.json()["next_runs"]
    assert len(runs) == 5 and runs == sorted(set(runs)) and all(x.endswith("Z") for x in runs)
    r = client.post("/api/admin/batch-schedules:preview", json={"kind": "daily", "spec": {"hour": 24, "minute": 0}})
    assert (r.status_code, r.json()["detail"]) == (422, "invalid_batch_schedule")


def test_runs_endpoint_limits_and_404(client):
    repos = client.app.state.repos
    bid = _batch(client)
    sid = _create(client, bid).json()["schedule_id"]
    for i in range(3):
        row = repos.batch_schedules.get(sid)
        repos.batch_schedules.record(row, outcome="skipped", reason_code="maintenance_mode",
                                     next_run_at=f"2099-01-0{i + 1}T00:00:00Z", now="2026-11-16T00:00:00Z",
                                     batch_status="Completed")
    runs = client.get(f"/api/admin/batch-schedules/{sid}/runs").json()
    assert [r["reason_code"] for r in runs] == ["maintenance_mode"] * 3
    assert runs[0]["run_id"] > runs[-1]["run_id"]                       # 최근 순
    assert runs[0]["requeued"] is None                                  # 건너뜀엔 리셋이 없다 -- 0 이 아니다
    assert len(client.get(f"/api/admin/batch-schedules/{sid}/runs?limit=2").json()) == 2
    assert client.get(f"/api/admin/batch-schedules/{sid}/runs?limit=999").status_code == 200    # 상한으로 자른다
    assert client.get(f"/api/admin/batch-schedules/{sid}/runs?limit=0").status_code == 422
    assert client.get("/api/admin/batch-schedules/nope/runs").status_code == 404


def test_summaries_for(client):
    repos = client.app.state.repos
    a, b = _batch(client), _batch(client)
    sid = _create(client, a).json()["schedule_id"]
    out = repos.batch_schedules.summaries_for([a, b, a])
    assert set(out) == {a}
    assert out[a]["schedule_id"] == sid and out[a]["enabled"] is True and out[a]["kind"] == "daily"
    assert set(out[a]) == {"schedule_id", "kind", "spec", "enabled", "next_run_at", "last_run_at", "last_outcome",
                           "last_reason_code", "paused_reason_code"}


SUMMARY_KEYS = {"schedule_id", "kind", "spec", "enabled", "next_run_at", "last_run_at", "last_outcome",
                "last_reason_code", "paused_reason_code"}


def test_batch_list_and_summary_carry_the_schedule_summary(client, db):
    # 통합(2026-11-16): 배치 목록 행과 상세 머리(view=summary)에 schedule(요약 | null). view 없음·all 은 모양을 바꾸지 않는다
    # (옛 탭·확인 창 응답 -- 바이트 단위로 그대로).
    a, b = _batch(client), _batch(client)
    row = _create(client, a).json()
    repo = client.app.state.repos.batch_schedules
    repo.record(repo.get(row["schedule_id"]), outcome="skipped", reason_code="batch_schedule_batch_running",
                next_run_at="2099-01-01T00:00:00Z", now="2026-11-16T00:00:00Z", batch_status="Running")
    rows = {r["batch_id"]: r for r in client.get("/api/admin/batches").json()}
    sch = rows[a]["schedule"]
    assert set(sch) == SUMMARY_KEYS
    assert sch["schedule_id"] == row["schedule_id"] and sch["kind"] == "daily" and sch["spec"] == {"hour": 2, "minute": 0}
    assert sch["enabled"] is True and sch["next_run_at"] == "2099-01-01T00:00:00Z"
    assert (sch["last_run_at"], sch["last_outcome"], sch["last_reason_code"], sch["paused_reason_code"]) == (
        "2026-11-16T00:00:00Z", "skipped", "batch_schedule_batch_running", None)
    assert rows[b]["schedule"] is None                                        # 없음 = null(키는 있다)
    summary = client.get(f"/api/admin/batches/{a}?view=summary").json()
    assert summary["schedule"] == sch
    assert client.get(f"/api/admin/batches/{b}?view=summary").json()["schedule"] is None
    plain = client.get(f"/api/admin/batches/{a}").json()
    assert "schedule" not in plain and "schedule" not in client.get(f"/api/admin/batches/{a}?view=all").json()
    # 끄면 꺼진 요약(next_run_at null)이 실린다.
    client.post(f"/api/admin/batch-schedules/{row['schedule_id']}:disable", json={})
    off = client.get(f"/api/admin/batches/{a}?view=summary").json()["schedule"]
    assert off["enabled"] is False and off["next_run_at"] is None and off["paused_reason_code"] is None


def test_batch_list_reads_schedules_in_one_call(client, monkeypatch):
    # 목록 한 쪽 = 스케줄 질의 한 번(행마다 묻지 않는다).
    ids = [_batch(client) for _ in range(3)]
    for bid in ids[:2]:
        assert _create(client, bid).status_code == 201
    repo = client.app.state.repos.batch_schedules
    calls = []
    real = repo.summaries_for
    monkeypatch.setattr(repo, "summaries_for", lambda batch_ids: (calls.append(list(batch_ids)), real(batch_ids))[1])
    rows = client.get("/api/admin/batches").json()
    assert len(calls) == 1 and set(calls[0]) == {r["batch_id"] for r in rows} >= set(ids)
    assert sum(r["schedule"] is not None for r in rows) == 2


def test_broken_first_payload_does_not_500(client, db):
    bid = _batch(client)
    _create(client, bid)
    db.execute("UPDATE batch_items SET payload = '[1, 2]' WHERE batch_id = :b", {"b": bid})
    row = client.get("/api/admin/batch-schedules").json()[0]
    assert (row["batch"]["storage"], row["batch"]["source_storage"]) == (None, None)


# --- :rescan 경합 수정 -------------------------------------------------------------------------------------------------

def test_rescan_route_loses_cleanly_when_the_batch_was_revived_in_between(client, db, monkeypatch):
    # 라우트가 읽은 스냅숏은 종단인데 그 사이 다른 재실행·스케줄 회차가 되살렸다 -- 예전엔 무조건 set_status 로 덮었다.
    repos = client.app.state.repos
    bid = _batch(client, op="sync")
    stale = repos.batches.get(bid)
    db.execute("UPDATE batches SET status = 'Running' WHERE batch_id = :b", {"b": bid})
    real_get = repos.batches.get
    monkeypatch.setattr(repos.batches, "get", lambda b: dict(stale) if b == bid else real_get(b))
    r = client.post(f"/api/admin/batches/{bid}:rescan")
    assert (r.status_code, r.json()["detail"]) == (409, "batch_not_rescannable")
    assert real_get(bid)["status"] == "Running"                               # Previewing 으로 덮이지 않았다
    assert [it["status"] for it in repos.batches.list_items(bid)] == ["Queued"]


def test_rescan_if_terminal_shape(client, db):
    repos = client.app.state.repos
    bid = _batch(client, op="rm")
    repos.batches.set_item_status(bid, 0, "Failed")
    db.execute("UPDATE batches SET failed_count = 1 WHERE batch_id = :b", {"b": bid})
    assert repos.batches.rescan_if_terminal(bid) == {"status": "Previewing", "requeued": 1}
    b = repos.batches.get(bid)
    assert (b["status"], b["failed_count"]) == ("Previewing", 0)
    assert repos.batches.rescan_if_terminal(bid) is None                      # 이제 비종단
    assert repos.batches.rescan_if_terminal("nope") is None


# --- 배치 삭제 두 경로의 연쇄 삭제 -----------------------------------------------------------------------------------------

def _with_schedule_and_run(client, bid):
    repos = client.app.state.repos
    sid = _create(client, bid).json()["schedule_id"]
    repos.batch_schedules.record(repos.batch_schedules.get(sid), outcome="skipped", reason_code="maintenance_mode",
                                 next_run_at="2099-01-01T00:00:00Z", now="2026-11-16T00:00:00Z",
                                 batch_status="Completed")
    return sid


def _schedule_rows(db, bid):
    return (db.query("SELECT * FROM batch_schedules WHERE batch_id = :b", {"b": bid}),
            db.query("SELECT * FROM batch_schedule_runs WHERE batch_id = :b", {"b": bid}))


def test_batch_record_delete_cascades_schedules(client, db):
    bid = _batch(client)
    keep = _batch(client)
    _with_schedule_and_run(client, bid)
    _with_schedule_and_run(client, keep)
    assert client.delete(f"/api/admin/batches/{bid}").status_code == 200
    assert _schedule_rows(db, bid) == ([], [])
    assert all(len(rows) == 1 for rows in _schedule_rows(db, keep))          # 다른 배치는 그대로
    assert len(_audit(db, "create")) == 2                                     # 감사 행은 남는다


def test_batch_unit_delete_cascades_schedules(client, db):
    repos = client.app.state.repos
    bid = _batch(client)
    _with_schedule_and_run(client, bid)
    out = repos.request_purges.delete_batch(bid, expected_request_count=0, actor="ops", artifact_base=None,
                                            quiet_seconds=0, max_children=1000)
    assert out["deleted"] is True, out
    assert _schedule_rows(db, bid) == ([], [])
    assert repos.batches.get(bid) is None


def _schedule_delete_audit(db, bid, sid, actor):
    [row] = _audit(db, "delete")
    assert (row["target_key"], row["actor"], row["after_state"]) == (bid, actor, None)
    before = json.loads(row["before_state"])
    assert (before["schedule_id"], before["batch_id"], before["kind"], before["spec"], before["armed_by"]) == \
        (sid, bid, "daily", DAILY["spec"], "ops")
    assert (before["enabled"], before["acknowledged_irreversible"], before["deleted_with_batch"]) == (True, False, True)
    assert (before["run_count"], before["last_outcome"], before["last_reason_code"]) == (1, "skipped", "maintenance_mode")


def test_batch_record_delete_audits_the_schedule_it_removes_even_via_the_shared_token(client, db):
    # 2026-11-16 리뷰: 배치 삭제는 require_admin 이라 공유 Bearer 토큰으로도 되는데, 켜진 스케줄과 회차 이력 전체를 감사 행
    # 없이 지웠다 -- 스케줄 DELETE 라우트와 같은 ('batch_schedule', 'delete') 행을 남긴다(토큰이면 actor 에 token: 표식).
    bid = _batch(client)
    sid = _with_schedule_and_run(client, bid)
    assert TestClient(client.app).delete(f"/api/admin/batches/{bid}", headers=TOKEN).status_code == 200
    assert _schedule_rows(db, bid) == ([], [])
    _schedule_delete_audit(db, bid, sid, "token:shared-token")


def test_batch_record_delete_without_a_schedule_writes_no_schedule_audit(client, db):
    bid = _batch(client)
    assert client.delete(f"/api/admin/batches/{bid}").status_code == 200
    assert _audit(db, "delete") == []


def test_batch_unit_delete_audits_the_schedule_it_removes(client, db):
    repos = client.app.state.repos
    bid = _batch(client)
    sid = _with_schedule_and_run(client, bid)
    out = repos.request_purges.delete_batch(bid, expected_request_count=0, actor="ops", artifact_base=None,
                                            quiet_seconds=0, max_children=1000)
    assert out["deleted"] is True, out
    _schedule_delete_audit(db, bid, sid, "ops")
    assert len(_audit(db, "delete", mutation_class="batch")) == 1               # 배치 자체의 감사 행은 그대로


def test_every_batch_id_table_is_covered_by_both_batch_delete_paths(db):
    # 그물: 스키마에서 batch_id 컬럼을 가진 테이블 전수 = 배치 삭제 목록 ∪ 명시 예외. 새 테이블이 batch_id 를 담으면 여기서
    # 빨개진다 -- 두 삭제 경로(배치 기록 삭제·배치 단위 삭제)에 DELETE 를 넣고 BATCH_DELETE_TABLES 에 올리거나, 지우지 않는
    # 이유와 함께 BATCH_DELETE_EXEMPT_TABLES 에. FK 가 0건이라 빠뜨리면 지운 배치의 행이 조용히 고아가 된다.
    tables = [r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
              if not r["name"].startswith("sqlite_")]
    with_batch_id = {t for t in tables if "batch_id" in {c["name"] for c in db.query(f"PRAGMA table_info({t})")}}
    assert with_batch_id == set(BATCH_DELETE_TABLES) | set(BATCH_DELETE_EXEMPT_TABLES)
    assert not set(BATCH_DELETE_TABLES) & set(BATCH_DELETE_EXEMPT_TABLES)
    for entry in (BatchesRepository.delete, RequestPurgesRepository.delete_batch):
        src = inspect.getsource(entry)
        for t in BATCH_DELETE_TABLES:
            assert f"DELETE FROM {t} " in src, (entry.__qualname__, t)
