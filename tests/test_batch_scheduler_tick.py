"""배치 스케줄러 회차(2026-11-16): 판정 순서(batch_scheduler.schedule_hold) · 발사 한 트랜잭션(BatchSchedulesRepository.fire --
:rescan 과 같은 _rescan_locked) · 스케줄 CAS(같은 슬롯 두 번 금지) · 일시정지 · 이력 상한 · 컨트롤러 틱 격리."""
import json

import pytest

from dms import batch_scheduler
from dms.batch_scheduler import (SCHEDULER_ACTOR, BatchScheduler, armed_fingerprint, next_run_after,
                                 schedule_hold, storage_binding_digest)
from dms.repositories import Repositories
from dms.repositories.batch_schedules import RUN_HISTORY_CAP

DAILY = {"hour": 2, "minute": 0}
SLOT = "2026-11-16T17:00:00Z"            # KST 11-17 02:00
NOW = "2026-11-16T17:00:05Z"
NEXT = "2026-11-17T17:00:00Z"
SCAN_ITEM = {"storage": "s1", "target": "a"}
SYNC_ITEM = {"source_storage": "s1", "source": "a", "destination_storage": "s2", "destination": "b"}
RM_ITEM = {"storage": "s1", "target": "old"}


class _S:
    allow_privileged_requesters = True
    privileged_requesters = frozenset({"ops"})
    preview_ttl_seconds = 900


class _Off(_S):
    allow_privileged_requesters = False


@pytest.fixture
def repos(db):
    r = Repositories(db)
    r.accounts.create("ops", "p", "admin", actor="t")
    # 항목이 가리키는 스토리지(s1·s2)가 있어야 무장·회차가 돈다(없으면 storage_missing -- batch_hold).
    for name in ("s1", "s2"):
        r.storages.create(storage_name=name, mount_path=f"/mnt/{name}", managed_root=f"/mnt/{name}/managed",
                          backend_type="cephfs", actor="t")
    return r


def _batch(repos, *, op="scan", status="Completed", n=2, options=None, requester="ops", auth="session",
           auto_by=None, item_status="Succeeded"):
    base = {"sync": SYNC_ITEM, "rm": RM_ITEM}.get(op, SCAN_ITEM)
    key = "destination" if op == "sync" else "target"
    items = [{**base, key: f"{base[key]}{i}"} for i in range(n)]
    if options is None:
        options = {"recursive": True} if op == "rm" else {}
    bid = repos.batches.create(operation=op, requester_id=requester, actor=requester, max_concurrency=2,
                               options=options, items=items, note=None, status=status, auth_method=auth,
                               auto_confirm_by=auto_by)
    for seq in range(n):
        repos.batches.set_item_status(bid, seq, item_status)
    repos.db.execute("UPDATE batches SET succeeded_count = :n WHERE batch_id = :b", {"n": n, "b": bid})
    return bid


def _schedule(repos, bid, *, kind="daily", spec=None, next_at=SLOT, armed_by="ops", destructive=False):
    # 파괴적 스케줄의 동의 지문 = 라우트의 arm 과 같은 함수(armed_fingerprint -- 스토리지 바인딩 포함).
    return repos.batch_schedules.create(
        batch_id=bid, kind=kind, spec=spec or DAILY, enabled=True, next_run_at=next_at, actor=armed_by,
        arm=(lambda b, i: (armed_fingerprint(repos, b, i) if destructive else None, storage_binding_digest(repos, i))))


def _tick(repos, now=NOW, settings=_S):
    return BatchScheduler(repos, settings=settings, now=now).run_once()


def _runs(repos, sid):
    return repos.batch_schedules.runs(sid, limit=500)


def _audit(db, mutation_class, operation):
    return db.query("SELECT actor, target_key, before_state, after_state FROM audit_log "
                    "WHERE mutation_class = :m AND operation = :o", {"m": mutation_class, "o": operation})


def _events(db, event_type):
    return db.query("SELECT severity, message FROM events WHERE event_type = :t", {"t": event_type})


# --- 발사 -----------------------------------------------------------------------------------------------------------

def test_due_scan_schedule_fires_a_full_rescan(repos, db):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    assert _tick(repos) == [(sid, "fired")]
    b = repos.batches.get(bid)
    assert b["status"] == "Running"                                     # scan 은 확인 단계가 없다
    assert (b["succeeded_count"], b["failed_count"]) == (0, 0)          # 전체 재시작 -- 절대값 0
    assert [it["status"] for it in repos.batches.list_items(bid)] == ["Queued", "Queued"]
    s = repos.batch_schedules.get(sid)
    assert s["next_run_at"] == NEXT and s["enabled"] is True
    assert (s["last_outcome"], s["last_reason_code"], s["last_run_at"]) == ("fired", None, NOW)
    [run] = _runs(repos, sid)
    assert (run["outcome"], run["scheduled_for"], run["requeued"], run["batch_status"]) == ("fired", SLOT, 2, "Completed")
    # 직전 회차 결과는 리셋이 0 으로 만들기 전 값(이력 행에 남는다).
    assert (run["prev_status"], run["prev_item_count"], run["prev_succeeded"], run["prev_failed"]) == \
        ("Completed", 2, 2, 0)
    [audit] = _audit(db, "batch", "scheduled_rescan")
    assert audit["actor"] == SCHEDULER_ACTOR and audit["target_key"] == bid
    after = json.loads(audit["after_state"])
    assert after["schedule_id"] == sid and after["armed_by"] == "ops" and after["scheduled_for"] == SLOT
    assert after["requeued"] == 2 and after["status"] == "Running" and after["fingerprint"].startswith("sha256:")
    assert len(_events(db, "batch_schedule_fired")) == 1


def test_sync_rm_schedules_only_reach_previewing_and_never_confirm(repos, db):
    # 확인 게이트 그대로(M2): 자동 확인이 켜져 있어도 스케줄러는 Previewing 으로만 보낸다 -- 확인은 오케스트레이터 몫.
    sync_bid = _batch(repos, op="sync", auto_by="ops")
    rm_bid = _batch(repos, op="rm")
    _schedule(repos, sync_bid)
    _schedule(repos, rm_bid, destructive=True)
    _tick(repos)
    for bid in (sync_bid, rm_bid):
        assert repos.batches.get(bid)["status"] == "Previewing"
    assert repos.batches.get(sync_bid)["auto_confirm_by"] == "ops"           # 건드리지 않는다
    assert repos.batches.get(rm_bid)["auto_confirm_by"] is None
    assert _audit(db, "batch", "confirm") == []


def test_cancelled_batch_is_fired_too(repos):
    bid = _batch(repos, status="Cancelled", item_status="Cancelled")
    _schedule(repos, bid)
    _tick(repos)
    assert repos.batches.get(bid)["status"] == "Running"


def test_fire_twice_with_the_same_expected_slot_fires_once(repos, db):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    row = repos.batch_schedules.get(sid)
    kw = dict(next_run_at=NEXT, now=NOW, verify=lambda b, i: "sha256:x", armed_env_listed=True,
              requester_env_listed=True)
    assert repos.batch_schedules.fire(row, **kw) == {"status": "Running", "requeued": 2, "fingerprint": "sha256:x"}
    # 배치를 다시 종단으로 만들어 스케줄 CAS 만 남긴다 -- 같은 슬롯(읽은 next_run_at)의 두 번째 발사는 통째 롤백.
    db.execute("UPDATE batches SET status = 'Completed', succeeded_count = 2 WHERE batch_id = :b", {"b": bid})
    db.execute("UPDATE batch_items SET status = 'Succeeded' WHERE batch_id = :b", {"b": bid})
    assert repos.batch_schedules.fire(row, **kw) is None
    b = repos.batches.get(bid)
    assert b["status"] == "Completed" and b["succeeded_count"] == 2                  # 리셋까지 롤백됐다
    assert [it["status"] for it in repos.batches.list_items(bid)] == ["Succeeded", "Succeeded"]
    assert len(_runs(repos, sid)) == 1 and len(_audit(db, "batch", "scheduled_rescan")) == 1


def test_two_controllers_on_the_same_tick_fire_once(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    assert _tick(repos) == [(sid, "fired")]
    assert _tick(repos) == []                        # next 가 넘어가 due 가 아니다
    assert len(_runs(repos, sid)) == 1


def test_human_edit_between_read_and_fire_loses_the_cas(repos, db):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    row = repos.batch_schedules.get(sid)
    db.execute("UPDATE batch_schedules SET updated_at = '2099-01-01T00:00:00Z' WHERE schedule_id = :s", {"s": sid})
    assert repos.batch_schedules.fire(row, next_run_at=NEXT, now=NOW, verify=lambda b, i: "fp",
                                      armed_env_listed=True, requester_env_listed=True) is None
    assert repos.batches.get(bid)["status"] == "Completed"


def test_privilege_revoked_between_decision_and_fire_blocks_the_slot(repos, db):
    # 판정(파이썬) 뒤 회수된 계정 화면 부여 -- 스케줄 CAS 의 EXISTS 절이 같은 문장에서 다시 본다(batches.confirm 과 같은 결).
    repos.accounts.create("g", "p", "admin", actor="t")
    db.execute("UPDATE accounts SET root_granted = 1 WHERE username = 'g'")
    bid = _batch(repos, requester="g")
    sid = _schedule(repos, bid, armed_by="g")
    row = repos.batch_schedules.get(sid)
    batch, items = repos.batches.get(bid), repos.batches.list_items(bid)
    assert schedule_hold(repos, _S, row, batch, items, control={"maintenance": 0, "drain": 0}, now=NOW).outcome == "fired"
    db.execute("UPDATE accounts SET root_granted = 0 WHERE username = 'g'")
    assert repos.batch_schedules.fire(row, next_run_at=NEXT, now=NOW, verify=lambda b, i: "fp",
                                      armed_env_listed=False, requester_env_listed=False) is None
    assert repos.batches.get(bid)["status"] == "Completed"
    assert repos.batch_schedules.get(sid)["next_run_at"] == SLOT and _runs(repos, sid) == []
    # 다음 틱은 새 자격으로 판정해 일시정지한다.
    assert _tick(repos) == [(sid, "paused")]
    assert repos.batch_schedules.get(sid)["paused_reason_code"] == "batch_schedule_owner_not_privileged"


def test_maintenance_turned_on_between_decision_and_fire_blocks_the_slot(repos, db):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    row = repos.batch_schedules.get(sid)
    db.execute("UPDATE control_state SET maintenance = 1 WHERE id = 1")
    assert repos.batch_schedules.fire(row, next_run_at=NEXT, now=NOW, verify=lambda b, i: "fp",
                                      armed_env_listed=True, requester_env_listed=True) is None
    assert repos.batches.get(bid)["status"] == "Completed"


def test_batch_changed_between_decision_and_fire_rolls_back(repos, monkeypatch):
    # 판정은 옛 항목으로 통과했는데 발사 직전 CSV 교체가 커밋됐다 -- 잠근 행으로 다시 본 지문이 달라 롤백, 다음 틱이 일시정지.
    bid = _batch(repos, op="rm")
    sid = _schedule(repos, bid, destructive=True)
    real_fire = repos.batch_schedules.fire

    def fire_after_replace(row, **kw):
        repos.batches.replace_items(bid, [{"storage": "s1", "target": "other"}])
        return real_fire(row, **kw)

    monkeypatch.setattr(repos.batch_schedules, "fire", fire_after_replace)
    assert _tick(repos) == [(sid, "lost")]
    assert repos.batches.get(bid)["status"] == "Completed"
    monkeypatch.setattr(repos.batch_schedules, "fire", real_fire)
    assert _tick(repos) == [(sid, "paused")]
    assert repos.batch_schedules.get(sid)["paused_reason_code"] == "batch_schedule_batch_changed"


# --- 건너뜀·놓침 -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("column,code", [("maintenance", "maintenance_mode"), ("drain", "batch_schedule_drain")])
def test_maintenance_and_drain_skip_without_pausing(repos, db, column, code):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    db.execute(f"UPDATE control_state SET {column} = 1 WHERE id = 1")
    assert _tick(repos) == [(sid, "skipped")]
    s = repos.batch_schedules.get(sid)
    assert (s["enabled"], s["next_run_at"], s["last_outcome"], s["last_reason_code"]) == (True, NEXT, "skipped", code)
    assert s["paused_reason_code"] is None
    assert repos.batches.get(bid)["status"] == "Completed"
    [run] = _runs(repos, sid)
    assert (run["outcome"], run["reason_code"], run["requeued"], run["prev_status"]) == ("skipped", code, None, None)


def test_late_beyond_grace_is_missed_once_and_not_caught_up(repos, db):
    bid = _batch(repos)
    sid = _schedule(repos, bid, next_at="2026-11-14T17:00:00Z")         # 이틀 밀렸다(컨트롤러 중단)
    assert _tick(repos) == [(sid, "missed")]
    s = repos.batch_schedules.get(sid)
    assert (s["next_run_at"], s["last_reason_code"]) == (NEXT, "batch_schedule_missed")   # 밀린 회차를 몰아 쏘지 않는다
    assert repos.batches.get(bid)["status"] == "Completed"
    assert _tick(repos) == []
    assert len(_runs(repos, sid)) == 1 and len(_events(db, "batch_schedule_missed")) == 1


@pytest.mark.parametrize("kind,spec,slot,late,outcome", [
    ("daily", DAILY, SLOT, "2026-11-16T18:00:00Z", "fired"),             # 정확히 1h -- 유예 안
    ("daily", DAILY, SLOT, "2026-11-16T18:00:01Z", "missed"),
    ("interval", {"every_hours": 1, "hour": 0, "minute": 0}, "2026-11-16T17:00:00Z", "2026-11-16T17:30:00Z", "fired"),
    ("interval", {"every_hours": 1, "hour": 0, "minute": 0}, "2026-11-16T17:00:00Z", "2026-11-16T17:30:01Z", "missed"),
])
def test_grace_boundary(repos, kind, spec, slot, late, outcome):
    bid = _batch(repos)
    sid = _schedule(repos, bid, kind=kind, spec=spec, next_at=slot)
    assert _tick(repos, now=late) == [(sid, outcome)]
    assert repos.batch_schedules.get(sid)["next_run_at"] == next_run_after(kind, spec, late)


def test_site_privileged_off_skips_without_pausing(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    assert _tick(repos, settings=_Off) == [(sid, "skipped")]
    s = repos.batch_schedules.get(sid)
    assert s["enabled"] is True and s["last_reason_code"] == "batch_schedule_privileged_disabled"


def test_empty_batch_is_skipped(repos, db):
    bid = _batch(repos, n=0)
    sid = _schedule(repos, bid)
    assert _tick(repos) == [(sid, "skipped")]
    assert repos.batch_schedules.get(sid)["last_reason_code"] == "batch_schedule_empty"
    assert repos.batches.get(bid)["status"] == "Completed"           # 가짜 「실행됨」을 만들지 않는다


@pytest.mark.parametrize("status,code", [
    ("Previewing", "batch_schedule_batch_running"), ("Running", "batch_schedule_batch_running"),
    ("PreviewReady", "batch_schedule_awaiting_confirm"), ("Cancelling", "batch_schedule_batch_cancelling"),
    ("Weird", "batch_not_rescannable"),
])
def test_non_terminal_batch_skips_with_status_specific_reason(repos, db, status, code):
    bid = _batch(repos, op="sync")
    sid = _schedule(repos, bid)
    db.execute("UPDATE batches SET status = :s WHERE batch_id = :b", {"s": status, "b": bid})
    assert _tick(repos) == [(sid, "skipped")]
    s = repos.batch_schedules.get(sid)
    assert (s["enabled"], s["next_run_at"], s["last_reason_code"]) == (True, NEXT, code)
    assert repos.batches.get(bid)["status"] == status
    assert _events(db, "batch_schedule_skipped") == []        # 건너뜀은 이벤트 없음(이력 행으로 충분)


# --- 일시정지 -------------------------------------------------------------------------------------------------------

def _assert_paused(repos, db, sid, code):
    s = repos.batch_schedules.get(sid)
    assert (s["enabled"], s["next_run_at"], s["paused_reason_code"], s["last_outcome"], s["last_reason_code"]) == \
        (False, None, code, "paused", code)
    [run] = _runs(repos, sid)
    assert (run["outcome"], run["reason_code"]) == ("paused", code)
    [audit] = _audit(db, "batch_schedule", "auto_pause")
    assert audit["actor"] == SCHEDULER_ACTOR
    assert json.loads(audit["after_state"])["paused_reason_code"] == code
    assert len(_events(db, "batch_schedule_paused")) == 1
    assert _tick(repos) == []                                 # 꺼졌다 -- 자격이 돌아와도 저절로 다시 돌지 않는다


def test_armed_by_not_in_privileged_list_pauses(repos, db):
    repos.accounts.create("eve", "p", "admin", actor="t")
    bid = _batch(repos)
    sid = _schedule(repos, bid, armed_by="eve")
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "batch_schedule_owner_not_privileged")
    assert repos.batches.get(bid)["status"] == "Completed"


def test_armed_by_disabled_account_pauses(repos, db):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    db.execute("UPDATE accounts SET disabled = 1 WHERE username = 'ops'")
    assert _tick(repos) == [(sid, "paused")]
    # 켠 사람(4단계)이 먼저 걸린다(요청자도 같은 사람).
    _assert_paused(repos, db, sid, "batch_schedule_owner_not_privileged")


def test_requester_not_privileged_pauses(repos, db):
    repos.accounts.create("eve", "p", "admin", actor="t")
    bid = _batch(repos, requester="eve")
    sid = _schedule(repos, bid)
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "batch_schedule_requester_not_privileged")


@pytest.mark.parametrize("auth", ["token", None])
def test_token_or_unknown_auth_batch_pauses(repos, db, auth):
    bid = _batch(repos, auth=auth)
    sid = _schedule(repos, bid)
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "batch_schedule_requester_not_privileged")


def test_missing_batch_pauses(repos, db):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    db.execute("DELETE FROM batches WHERE batch_id = :b", {"b": bid})       # 경합·변조의 잔재(정상 삭제는 스케줄도 지운다)
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "batch_not_found")
    assert _runs(repos, sid)[0]["batch_status"] is None


def test_unknown_operation_pauses(repos, db):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    db.execute("UPDATE batches SET operation = 'chmod' WHERE batch_id = :b", {"b": bid})
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "batch_schedule_unsupported_operation")


@pytest.mark.parametrize("options,code", [
    ({}, "rm_recursive_required"),                 # 옵션 검증의 사유 그대로
    ({"recursive": True, "nope": 1}, "unknown_option"),
])
def test_stale_options_pause_with_the_validation_reason(repos, db, options, code):
    bid = _batch(repos, op="rm")
    sid = _schedule(repos, bid, destructive=True)
    db.execute("UPDATE batches SET options = :o WHERE batch_id = :b", {"o": json.dumps(options), "b": bid})
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, code)


def test_non_dict_options_pause_as_invalid_option(repos, db):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    db.execute("UPDATE batches SET options = '[1]' WHERE batch_id = :b", {"b": bid})
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "invalid_option")


def test_destructive_batch_changed_after_ack_pauses(repos, db):
    bid = _batch(repos, op="rm")
    sid = _schedule(repos, bid, destructive=True)
    repos.batches.replace_items(bid, [{"storage": "s1", "target": "elsewhere"}])
    db.execute("UPDATE batch_items SET status = 'Succeeded' WHERE batch_id = :b", {"b": bid})
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "batch_schedule_batch_changed")
    assert repos.batches.get(bid)["status"] == "Completed"


def test_auto_confirm_turned_on_later_changes_the_fingerprint(repos, db):
    bid = _batch(repos, op="rm")
    sid = _schedule(repos, bid, destructive=True)
    db.execute("UPDATE batches SET auto_confirm_by = 'ops' WHERE batch_id = :b", {"b": bid})
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "batch_schedule_batch_changed")


def test_sync_delete_turned_on_after_a_plain_schedule_pauses(repos, db):
    # 만들 때는 비파괴(동의 지문 NULL) -- 나중에 delete 를 켜면 동의 없는 파괴적 스케줄이라 멈춘다.
    bid = _batch(repos, op="sync")
    sid = _schedule(repos, bid)
    assert repos.batch_schedules.get(sid)["ack_fingerprint"] is None
    db.execute("UPDATE batches SET options = :o WHERE batch_id = :b", {"o": json.dumps({"delete": True}), "b": bid})
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "batch_schedule_batch_changed")


def test_unchanged_destructive_batch_fires(repos):
    bid = _batch(repos, op="sync", options={"delete": True})
    sid = _schedule(repos, bid, destructive=True)
    assert _tick(repos) == [(sid, "fired")]


def _storage(repos, name, root):
    if repos.storages.get(name) is not None:          # 픽스처가 만든 s1·s2 -- 이 테스트의 경로로 맞춘다
        repos.storages.update(name, mount_path=root, managed_root=f"{root}/managed", backend_type="cephfs",
                              enabled=True, actor="t")
        return
    repos.storages.create(storage_name=name, mount_path=root, managed_root=f"{root}/managed", backend_type="cephfs",
                          actor="t")


def _repoint(repos, name, root, actor="token:shared-token"):
    # 공유 Bearer 토큰도 부를 수 있는 PUT /api/admin/storages/{name}(require_admin)과 같은 저장소 호출.
    repos.storages.update(name, mount_path=root, managed_root=root, backend_type="cephfs", enabled=True, actor=actor)


@pytest.mark.parametrize("op,options,name", [("rm", None, "s1"), ("sync", {"delete": True}, "s1"),
                                             ("sync", {"delete": True}, "s2")])
def test_destructive_schedule_pauses_when_a_storage_is_repointed_after_ack(repos, db, op, options, name):
    # 2026-11-16 리뷰: payload 는 스토리지 이름 + 상대 경로뿐이고 절대 경로는 stepper 가 실행 때의 스토리지 행으로 만든다 --
    # 동의 뒤 스토리지 경로를 바꾸면(토큰으로도 된다) 같은 지문으로 사람이 본 적 없는 경로를 root 로 지웠다. 자동 확인이 켜진
    # rm 배치면 사람 없이 끝까지 간다. 소스·목적지 어느 쪽을 바꿔도 멈춘다.
    _storage(repos, "s1", "/mnt/s1")
    _storage(repos, "s2", "/mnt/s2")
    bid = _batch(repos, op=op, options=options, auto_by="ops")
    sid = _schedule(repos, bid, destructive=True)
    _repoint(repos, name, "/home")
    assert _tick(repos) == [(sid, "paused")]
    # 연산 무관 바인딩 검사(storage_binding_digest)가 파괴적 지문보다 먼저 잡는다 -- 사유도 「경로가 바뀜」으로 구체적이다.
    _assert_paused(repos, db, sid, "batch_schedule_storage_changed")
    b = repos.batches.get(bid)
    assert b["status"] == "Completed" and b["auto_confirm_by"] == "ops"


def test_destructive_schedule_with_untouched_storages_still_fires(repos):
    # 바인딩은 항목이 가리키는 스토리지의 경로만 -- 다른 스토리지의 변경·같은 값의 재저장·enabled 토글은 지문을 바꾸지 않는다.
    _storage(repos, "s1", "/mnt/s1")
    _storage(repos, "s9", "/mnt/s9")
    bid = _batch(repos, op="rm")
    sid = _schedule(repos, bid, destructive=True)
    _repoint(repos, "s9", "/home")
    repos.storages.update("s1", mount_path="/mnt/s1", managed_root="/mnt/s1/managed", backend_type="gpfs",
                          enabled=False, actor="t")
    repos.storages.update("s1", mount_path="/mnt/s1", managed_root="/mnt/s1/managed", backend_type="cephfs",
                          enabled=True, actor="t")
    assert _tick(repos) == [(sid, "fired")]


def test_storage_repointed_between_decision_and_fire_rolls_back(repos, monkeypatch):
    # 판정은 옛 경로로 통과했는데 발사 직전 스토리지 경로 변경이 커밋됐다 -- 잠근 행으로 다시 본 지문(verify)이 달라 롤백.
    _storage(repos, "s1", "/mnt/s1")
    bid = _batch(repos, op="rm")
    sid = _schedule(repos, bid, destructive=True)
    real_fire = repos.batch_schedules.fire

    def fire_after_repoint(row, **kw):
        _repoint(repos, "s1", "/home")
        return real_fire(row, **kw)

    monkeypatch.setattr(repos.batch_schedules, "fire", fire_after_repoint)
    assert _tick(repos) == [(sid, "lost")]
    assert repos.batches.get(bid)["status"] == "Completed"
    monkeypatch.setattr(repos.batch_schedules, "fire", real_fire)
    assert _tick(repos) == [(sid, "paused")]
    assert repos.batch_schedules.get(sid)["paused_reason_code"] == "batch_schedule_storage_changed"


@pytest.mark.parametrize("op,options,auto_by,name", [
    ("scan", None, None, "s1"),
    ("sync", {}, None, "s2"),
    # 2026-11-16 보안 리뷰: 자동 확인이 켜진 일반 sync 는 파괴적 지문 밖이었다 -- 회차 사이 목적지 스토리지 경로를 바꾸면
    # (공유 토큰도 된다) 사람 없이 root 로 엉뚱한 곳에 덮어썼다.
    ("sync", {}, "ops", "s2"),
    ("sync", {}, "ops", "s1"),
])
def test_any_schedule_pauses_when_a_storage_is_repointed_after_arming(repos, db, op, options, auto_by, name):
    _storage(repos, "s1", "/mnt/s1")
    _storage(repos, "s2", "/mnt/s2")
    bid = _batch(repos, op=op, options=options, auto_by=auto_by)
    sid = _schedule(repos, bid)
    assert repos.batch_schedules.get(sid)["ack_fingerprint"] is None      # 비파괴 -- 동의 지문은 없다
    _repoint(repos, name, "/etc")
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "batch_schedule_storage_changed")
    assert repos.batches.get(bid)["status"] == "Completed"                # 리셋하지 않았다


def test_schedule_pauses_when_a_referenced_storage_is_deleted(repos, db):
    # 지웠다가 같은 이름으로 다시 만들기(2026-11-16 리뷰): 없는 스토리지끼리(None == None) 바인딩이 같다고 쏘면, 그 사이 같은
    # 이름으로 만든 새 경로에서 남은 항목이 돈다 -- 없으면 멈춘다.
    bid = _batch(repos, op="scan")
    sid = _schedule(repos, bid)
    repos.storages.delete("s1", actor="token:shared-token")
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "storage_missing")
    assert repos.batches.get(bid)["status"] == "Completed"


def test_scan_schedule_item_edits_do_not_pause(repos):
    # 바인딩은 경로만 묶는다 -- scan 배치는 항목을 자주 고친다(항목 추가·교체로 멈추면 안 된다, 그건 파괴적 지문만의 몫).
    _storage(repos, "s1", "/mnt/s1")
    bid = _batch(repos, op="scan")
    sid = _schedule(repos, bid)
    repos.db.execute("UPDATE batch_items SET payload = :p WHERE batch_id = :b AND seq = 0",
                     {"p": '{"storage": "s1", "target": "elsewhere"}', "b": bid})
    assert _tick(repos) == [(sid, "fired")]


def test_schedule_without_a_storage_binding_pauses_fail_closed(repos, db):
    # 열 이전에 만든 행·변조(NULL) -- 지금 바인딩과 같을 수 없어 멈춘다. 다시 켜면 그 순간의 경로로 묶여 다시 돈다.
    _storage(repos, "s1", "/mnt/s1")
    bid = _batch(repos, op="scan")
    sid = _schedule(repos, bid)
    db.execute("UPDATE batch_schedules SET storage_binding = NULL WHERE schedule_id = :s", {"s": sid})
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "batch_schedule_storage_changed")
    assert repos.batch_schedules.enable(
        sid, actor="ops", arm=lambda b, i: (None, storage_binding_digest(repos, i))) is True
    row = repos.batch_schedules.get(sid)
    assert row["storage_binding"] == storage_binding_digest(repos, repos.batches.list_items(bid))
    db.execute("UPDATE batch_schedules SET next_run_at = :t WHERE schedule_id = :s", {"t": SLOT, "s": sid})
    assert _tick(repos) == [(sid, "fired")]


@pytest.mark.parametrize("column,value", [("spec", "{broken"), ("spec", '{"hour": 99, "minute": 0}'),
                                          ("kind", "yearly"), ("next_run_at", "0000")])
def test_tampered_rule_pauses_as_invalid(repos, db, column, value):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    db.execute(f"UPDATE batch_schedules SET {column} = :v WHERE schedule_id = :s", {"v": value, "s": sid})
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "invalid_batch_schedule")


def test_pause_cas_loses_to_a_concurrent_human_edit(repos, db):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    row = repos.batch_schedules.get(sid)
    db.execute("UPDATE batch_schedules SET enabled = 0, next_run_at = NULL WHERE schedule_id = :s", {"s": sid})
    assert repos.batch_schedules.record(row, outcome="paused", reason_code="batch_not_found", next_run_at=None,
                                        now=NOW, batch_status=None) is False
    assert _runs(repos, sid) == [] and _audit(db, "batch_schedule", "auto_pause") == []


# --- 이력 상한 · 틱 격리 ---------------------------------------------------------------------------------------------------

def test_run_history_keeps_the_newest_200(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    slots = []
    for _ in range(RUN_HISTORY_CAP + 5):
        row = repos.batch_schedules.get(sid)
        slots.append(row["next_run_at"])
        assert repos.batch_schedules.record(row, outcome="skipped", reason_code="batch_schedule_empty",
                                            next_run_at=next_run_after("daily", DAILY, row["next_run_at"]),
                                            now=NOW, batch_status="Completed")
    runs = _runs(repos, sid)
    assert len(runs) == RUN_HISTORY_CAP == 200
    assert [r["scheduled_for"] for r in runs] == list(reversed(slots[5:]))         # 최근 순, 가장 오래된 5건만 빠졌다
    ids = [r["run_id"] for r in runs]
    assert ids == sorted(ids, reverse=True) and max(ids) - min(ids) == 199        # 가장 오래된 5건이 지워졌다


def test_one_bad_row_does_not_stop_the_tick(repos, db, monkeypatch):
    good = _batch(repos)
    bad = _batch(repos)
    sid_bad = _schedule(repos, bad, next_at="2026-11-16T16:59:00Z")       # 먼저 집힌다
    sid_good = _schedule(repos, good)
    real_get = repos.batches.get

    def flaky(batch_id):
        if batch_id == bad:
            raise RuntimeError("boom")
        return real_get(batch_id)

    monkeypatch.setattr(repos.batches, "get", flaky)
    assert _tick(repos) == [(sid_bad, "error"), (sid_good, "fired")]
    [ev] = _events(db, "batch_schedule_error")
    assert ev["severity"] == "error" and "boom" in ev["message"]
    assert repos.batch_schedules.get(sid_bad)["next_run_at"] == "2026-11-16T16:59:00Z"   # 다음 틱이 다시 판정


# --- 독 행(2026-11-16 리뷰): 예외로 끝나는 행이 next_run_at 을 넘기지 못해 틱마다 다시 집히고(오류 이벤트 5초마다), TICK_LIMIT
# 개가 모이면 due 의 앞자리를 영영 차지해 다른 스케줄이 굶었다 ------------------------------------------------------------------

def _poison(db, bid, where):
    if where == "options":
        db.execute("UPDATE batches SET options = '{bad' WHERE batch_id = :b", {"b": bid})
    else:
        db.execute("UPDATE batch_items SET payload = 'nope' WHERE batch_id = :b AND seq = 1", {"b": bid})


@pytest.mark.parametrize("where", ["options", "payload"])
def test_unreadable_batch_json_pauses_instead_of_erroring_every_tick(repos, db, where):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    _poison(db, bid, where)
    assert _tick(repos) == [(sid, "paused")]
    _assert_paused(repos, db, sid, "batch_schedule_batch_unreadable")
    assert _events(db, "batch_schedule_error") == []
    [run] = _runs(repos, sid)
    assert run["batch_status"] == (None if where == "options" else "Completed")    # 읽힌 만큼만 남긴다


def test_unreadable_batch_keeps_the_hold_order(repos, db):
    # 판정 순서 그대로: 유지보수는 일시정지 없이 건너뛴다(4' 는 1단계 뒤).
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    _poison(db, bid, "payload")
    db.execute("UPDATE control_state SET maintenance = 1 WHERE id = 1")
    assert _tick(repos) == [(sid, "skipped")]
    assert repos.batch_schedules.get(sid)["enabled"] is True


def test_unreadable_batches_do_not_starve_healthy_schedules(repos, db, monkeypatch):
    monkeypatch.setattr(batch_scheduler, "TICK_LIMIT", 2)
    poisoned = []
    for where in ("options", "payload"):
        bid = _batch(repos)
        poisoned.append(_schedule(repos, bid, next_at="2026-11-16T16:59:00Z"))      # 먼저 집힌다
        _poison(db, bid, where)
    good = _batch(repos)
    sid_good = _schedule(repos, good)
    assert sorted(_tick(repos)) == sorted((sid, "paused") for sid in poisoned)    # 같은 예정 시각 -- id 순
    assert _tick(repos) == [(sid_good, "fired")]
    assert repos.batches.get(good)["status"] == "Running"


def _always_failing(repos, monkeypatch, bad_ids):
    real_get = repos.batches.get

    def flaky(batch_id):
        if batch_id in bad_ids:
            raise RuntimeError("boom")
        return real_get(batch_id)

    monkeypatch.setattr(repos.batches, "get", flaky)


def test_error_persisting_beyond_grace_is_missed_once_and_frees_the_due_window(repos, db, monkeypatch):
    # 원인 모를 예외(일시적일 수 있다)는 유예 안에서는 그대로 다시 판정하고, 유예를 넘기면 정상 판정과 같은 missed 로 그 슬롯을
    # 넘긴다 -- 실패하는 행이 due 의 앞자리를 영영 차지하지 못한다.
    monkeypatch.setattr(batch_scheduler, "TICK_LIMIT", 1)
    bad = _batch(repos)
    sid_bad = _schedule(repos, bad, next_at="2026-11-16T16:59:00Z")
    good = _batch(repos)
    sid_good = _schedule(repos, good, next_at="2026-11-16T18:00:00Z")
    _always_failing(repos, monkeypatch, {bad})
    assert _tick(repos) == [(sid_bad, "error")]                                  # 유예 안 -- 그대로 둔다
    assert repos.batch_schedules.get(sid_bad)["next_run_at"] == "2026-11-16T16:59:00Z"
    late = "2026-11-16T18:30:00Z"                                                # bad 는 1h31m 지연, good 은 30분
    assert _tick(repos, now=late) == [(sid_bad, "missed")]
    s = repos.batch_schedules.get(sid_bad)
    assert (s["enabled"], s["next_run_at"], s["last_outcome"], s["last_reason_code"]) == \
        (True, NEXT, "missed", "batch_schedule_missed")
    [run] = _runs(repos, sid_bad)
    assert (run["outcome"], run["reason_code"], run["batch_status"]) == ("missed", "batch_schedule_missed", None)
    assert len(_events(db, "batch_schedule_missed")) == 1
    assert _tick(repos, now=late) == [(sid_good, "fired")]                       # 앞자리가 비었다


def test_error_on_a_tampered_rule_pauses_as_invalid(repos, db, monkeypatch):
    # 예외 경로에서도 다음 슬롯을 계산할 수 없으면(깨진 규칙) 0단계와 같은 일시정지 -- 넘길 슬롯이 없다.
    bad = _batch(repos)
    sid = _schedule(repos, bad)
    db.execute("UPDATE batch_schedules SET spec = '{broken' WHERE schedule_id = :s", {"s": sid})
    _always_failing(repos, monkeypatch, {bad})
    assert _tick(repos) == [(sid, "paused")]
    s = repos.batch_schedules.get(sid)
    assert (s["enabled"], s["paused_reason_code"]) == (False, "invalid_batch_schedule")


def test_error_events_are_rate_limited_per_schedule(repos, db, monkeypatch):
    # batch_cancel._event 와 같은 결: 같은 스케줄의 오류 이벤트는 쿨다운 안에 한 번만(5초 틱마다 쌓이지 않게), 다른 스케줄은 따로.
    a, b = _batch(repos), _batch(repos)
    sid_a = _schedule(repos, a)
    sid_b = _schedule(repos, b)
    _always_failing(repos, monkeypatch, {a, b})
    for _ in range(3):
        assert sorted(_tick(repos)) == sorted([(sid_a, "error"), (sid_b, "error")])
    events = _events(db, "batch_schedule_error")
    assert len(events) == 2
    assert {e["message"].split()[1] for e in events} == {sid_a, sid_b}


def test_tick_limit(repos, monkeypatch):
    monkeypatch.setattr(batch_scheduler, "TICK_LIMIT", 1)
    a, b = _batch(repos), _batch(repos)
    sid_a = _schedule(repos, a, next_at="2026-11-16T16:59:00Z")
    sid_b = _schedule(repos, b)
    assert _tick(repos) == [(sid_a, "fired")]
    assert _tick(repos) == [(sid_b, "fired")]


def test_not_due_and_disabled_schedules_are_not_touched(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid, next_at="2026-11-16T17:00:06Z")             # NOW 보다 1초 뒤
    assert _tick(repos) == []
    repos.batch_schedules.disable(sid, actor="ops")
    assert _tick(repos, now="2026-11-20T00:00:00Z") == []
    assert _runs(repos, sid) == []
