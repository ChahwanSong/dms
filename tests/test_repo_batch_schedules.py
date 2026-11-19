"""배치 스케줄러 회차(2026-11-16): 판정 순서(batch_scheduler.schedule_hold) · 발사 한 트랜잭션(BatchSchedulesRepository.fire --
:rescan 과 같은 _rescan_locked) · 스케줄 CAS(같은 슬롯 두 번 금지) · 같은 문장 자격 재확인 · 일시정지 · 이력 상한 · 컨트롤러 틱
격리와 예외 백오프. 설계 비판 반영: #5(무장 뒤 파괴적이 됨 → 지문 NULL 은 「다름」), #6(planner 가 결정적으로 거부할 원인 사전
판정 -- storage_missing·storage_disabled·identity_denied), #7(예외가 반복되는 행이 매 틱 슬롯을 먹지 않음), #4(취소와 같은
트랜잭션의 스케줄 끄기 몸통)."""
import json

import pytest

from dms import batch_scheduler
from dms.batch_scheduler import (SCHEDULER_ACTOR, TICK_LIMIT, BatchScheduler, armed_fingerprint, next_run_after,
                                 schedule_hold)
from dms.db import utc_now_iso
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
    preview_ttl_seconds = 86400


class _Off(_S):
    allow_privileged_requesters = False


@pytest.fixture(autouse=True)
def _fresh_backoff():
    # 예외 백오프는 모듈 수준(틱마다 새 인스턴스) -- 테스트끼리 새지 않게 비운다.
    batch_scheduler._BACKOFF.clear()
    yield
    batch_scheduler._BACKOFF.clear()


@pytest.fixture
def repos(db):
    r = Repositories(db)
    r.accounts.create("ops", "p", "admin", actor="t")
    for name in ("s1", "s2"):
        r.storages.create(storage_name=name, mount_path=f"/mnt/{name}", managed_root=f"/mnt/{name}/managed",
                          backend_type="nfs", actor="t")
        r.storages.set_status(name, "Ready")
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
        arm=(lambda b, i: armed_fingerprint(repos, b, i)) if destructive else (lambda b, i: None))


def _tick(repos, now=NOW, settings=_S):
    return BatchScheduler(repos, settings=settings, now=now).run_once()


def _runs(repos, sid):
    return repos.batch_schedules.runs(sid, limit=500)


def _audit(db, mutation_class, operation):
    return db.query("SELECT actor, target_key, before_state, after_state FROM audit_log "
                    "WHERE mutation_class = :m AND operation = :o", {"m": mutation_class, "o": operation})


def _events(db, event_type):
    return db.query("SELECT severity, message FROM events WHERE event_type = :t", {"t": event_type})


def _paused_with(repos, sid, reason):
    s = repos.batch_schedules.get(sid)
    assert s["enabled"] is False and s["next_run_at"] is None, s
    assert s["paused_reason_code"] == reason and s["last_outcome"] == "paused"
    assert _runs(repos, sid)[0]["outcome"] == "paused"
    assert _runs(repos, sid)[0]["reason_code"] == reason


# --- 발사 -----------------------------------------------------------------------------------------------------------

def test_scan_fires_running_resets_items_and_moves_to_next_slot(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    assert _tick(repos) == [(sid, "fired")]
    b = repos.batches.get(bid)
    assert b["status"] == "Running" and b["succeeded_count"] == 0 and b["failed_count"] == 0
    assert {it["status"] for it in repos.batches.list_items(bid)} == {"Queued"}
    assert all(it["request_id"] is None for it in repos.batches.list_items(bid))
    s = repos.batch_schedules.get(sid)
    assert s["next_run_at"] == NEXT and s["last_outcome"] == "fired" and s["last_run_at"] == NOW
    run = _runs(repos, sid)[0]
    assert run["outcome"] == "fired" and run["requeued"] == 2 and run["scheduled_for"] == SLOT
    assert (run["prev_status"], run["prev_item_count"], run["prev_succeeded"], run["prev_failed"]) == \
        ("Completed", 2, 2, 0)
    a = _audit(repos.db, "batch", "scheduled_rescan")
    assert len(a) == 1 and a[0]["actor"] == SCHEDULER_ACTOR and a[0]["target_key"] == bid
    after = json.loads(a[0]["after_state"])
    assert after["schedule_id"] == sid and after["armed_by"] == "ops" and after["requeued"] == 2
    assert after["scheduled_for"] == SLOT and after["next_run_at"] == NEXT


def test_sync_goes_to_previewing_only_never_confirms(repos):
    """확인 게이트 보존: sync·rm 회차는 Previewing 으로만 -- confirm·auto_confirm_by 쓰기·Running 직행 없음."""
    bid = _batch(repos, op="sync", auto_by="ops")
    sid = _schedule(repos, bid)
    assert _tick(repos) == [(sid, "fired")]
    b = repos.batches.get(bid)
    assert b["status"] == "Previewing" and b["auto_confirm_by"] == "ops"
    assert _audit(repos.db, "batch", "confirm") == []


def test_cancelled_batch_is_terminal_and_fires(repos):
    bid = _batch(repos, status="Cancelled", item_status="Cancelled")
    sid = _schedule(repos, bid)
    assert _tick(repos) == [(sid, "fired")]
    assert _runs(repos, sid)[0]["requeued"] == 2


def test_same_due_twice_fires_once(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    assert _tick(repos) == [(sid, "fired")]
    assert _tick(repos) == []                                    # 다음 슬롯은 내일
    assert [r["outcome"] for r in _runs(repos, sid)] == ["fired"]


def test_two_controllers_with_the_same_snapshot_fire_once(repos):
    """리스가 겹친 두 컨트롤러가 같은 due 행을 읽었다 -- 스케줄 CAS 로 한쪽만 쏘고 진 쪽은 통째 롤백."""
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    row = repos.batch_schedules.due(NOW, limit=5)[0]
    verify = lambda b, i: armed_fingerprint(repos, b, i)          # noqa: E731
    first = repos.batch_schedules.fire(row, next_run_at=NEXT, now=NOW, verify=verify,
                                       armed_env_listed=True, requester_env_listed=True)
    assert first is not None and first["requeued"] == 2
    # 진 쪽: 배치는 이미 Running 이라 _rescan_locked 가 None -- 아무것도 남기지 않는다
    repos.db.execute("UPDATE batches SET status = 'Completed' WHERE batch_id = :b", {"b": bid})
    second = repos.batch_schedules.fire(row, next_run_at=NEXT, now=NOW, verify=verify,
                                        armed_env_listed=True, requester_env_listed=True)
    assert second is None                                       # next_run_at 이 이미 옮겨져 CAS 0 행
    assert repos.batches.get(bid)["status"] == "Completed"       # 리셋까지 롤백
    assert [r["outcome"] for r in _runs(repos, sid)] == ["fired"]


def test_human_edit_between_read_and_fire_loses(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    row = repos.batch_schedules.due(NOW, limit=5)[0]
    repos.db.execute("UPDATE batch_schedules SET updated_at = '2026-11-16T17:00:01Z' WHERE schedule_id = :s", {"s": sid})
    assert repos.batch_schedules.fire(row, next_run_at=NEXT, now=NOW, verify=lambda b, i: "fp",
                                      armed_env_listed=True, requester_env_listed=True) is None
    assert repos.batches.get(bid)["status"] == "Completed"
    assert repos.batch_schedules.record(row, outcome="skipped", reason_code="batch_schedule_batch_running",
                                        next_run_at=NEXT, now=NOW, batch_status="Running") is False
    assert _runs(repos, sid) == []


def test_verify_failure_rolls_back_items_and_batch(repos):
    bid = _batch(repos)
    _schedule(repos, bid)
    row = repos.batch_schedules.due(NOW, limit=5)[0]
    assert repos.batch_schedules.fire(row, next_run_at=NEXT, now=NOW, verify=lambda b, i: None,
                                      armed_env_listed=True, requester_env_listed=True) is None
    assert repos.batches.get(bid)["status"] == "Completed"
    assert {it["status"] for it in repos.batches.list_items(bid)} == {"Succeeded"}
    assert repos.batch_schedules.get(row["schedule_id"])["next_run_at"] == SLOT


@pytest.mark.parametrize("revoke", ["root_grant", "disabled", "demoted"])
def test_same_statement_recheck_blocks_a_revoked_grant(repos, revoke):
    """판정 뒤 회수된 자격이 그 회차를 막는다(fire 의 EXISTS 절 -- batches.confirm 의 동의 절과 같은 모양)."""
    repos.accounts.create("granted", "p", "admin", actor="t")
    repos.db.execute("UPDATE accounts SET root_granted = 1 WHERE username = 'granted'")
    bid = _batch(repos, requester="granted")
    sid = _schedule(repos, bid, armed_by="granted")
    row = repos.batch_schedules.due(NOW, limit=5)[0]
    if revoke == "root_grant":
        repos.db.execute("UPDATE accounts SET root_granted = NULL WHERE username = 'granted'")
    elif revoke == "disabled":
        repos.db.execute("UPDATE accounts SET disabled = 1 WHERE username = 'granted'")
    else:
        repos.db.execute("UPDATE accounts SET role = 'user' WHERE username = 'granted'")
    assert repos.batch_schedules.fire(row, next_run_at=NEXT, now=NOW, verify=lambda b, i: "fp",
                                      armed_env_listed=False, requester_env_listed=False) is None
    assert repos.batches.get(bid)["status"] == "Completed"
    # 다음 틱의 판정은 같은 사유로 일시정지한다(켠 사람 = 요청자라 4단계가 먼저)
    assert _tick(repos) == [(sid, "paused")]
    _paused_with(repos, sid, "batch_schedule_owner_not_privileged")


def test_same_statement_recheck_env_listing_is_passed_from_settings(repos):
    """env 목록 소속은 설정이라 SQL 밖에서 넘긴다 -- 부여 없는 env 이름은 넘긴 값이 거짓이면(목록에서 빠진 뒤) 진다."""
    bid = _batch(repos)
    _schedule(repos, bid)
    row = repos.batch_schedules.due(NOW, limit=5)[0]
    assert repos.batch_schedules.fire(row, next_run_at=NEXT, now=NOW, verify=lambda b, i: "fp",
                                      armed_env_listed=False, requester_env_listed=True) is None
    assert repos.batch_schedules.fire(row, next_run_at=NEXT, now=NOW, verify=lambda b, i: "fp",
                                      armed_env_listed=True, requester_env_listed=False) is None
    assert repos.batches.get(bid)["status"] == "Completed"


def test_env_listed_owner_fires_without_root_grant(repos):
    bid = _batch(repos)
    _schedule(repos, bid)
    row = repos.batch_schedules.due(NOW, limit=5)[0]
    assert repos.batch_schedules.fire(row, next_run_at=NEXT, now=NOW, verify=lambda b, i: "fp",
                                      armed_env_listed=True, requester_env_listed=True) is not None


def test_maintenance_set_after_judgement_blocks_fire(repos):
    bid = _batch(repos)
    _schedule(repos, bid)
    row = repos.batch_schedules.due(NOW, limit=5)[0]
    repos.db.execute("UPDATE control_state SET drain = 1 WHERE id = 1")
    assert repos.batch_schedules.fire(row, next_run_at=NEXT, now=NOW, verify=lambda b, i: "fp",
                                      armed_env_listed=True, requester_env_listed=True) is None


def test_rescan_if_terminal_is_a_state_cas_with_set_based_reset(repos):
    bid = _batch(repos, n=3)
    assert repos.batches.rescan_if_terminal(bid, actor="ops") == {"status": "Running", "requeued": 3}
    # 이미 Running(사람 :rescan·회차가 먼저) -- 덮어쓰지 않는다
    assert repos.batches.rescan_if_terminal(bid, actor="ops") is None
    assert repos.batches.get(bid)["status"] == "Running"
    sync = _batch(repos, op="sync")
    assert repos.batches.rescan_if_terminal(sync, actor="ops")["status"] == "Previewing"
    cancelling = _batch(repos, status="Cancelling")
    assert repos.batches.rescan_if_terminal(cancelling, actor="ops") is None
    assert repos.batches.get(cancelling)["status"] == "Cancelling"


# --- 회차 경계(2026-11-16 회차 보존 정리 -- batch_rounds) ---------------------------------------------------------------

def _rounds(db, bid):
    return db.query("SELECT round_seq, started_at, floor_order, via, actor, requeued FROM batch_rounds "
                    "WHERE batch_id = :b ORDER BY round_seq", {"b": bid})


def _child(repos, bid, n=1):
    out = []
    for i in range(n):
        rid = repos.requests.create(operation="scan", requester_id="ops", actor="ops", resource_key=f"k-{bid}-{i}",
                                    payload=dict(SCAN_ITEM), priority="mid", batch_id=bid, auth_method="session")
        out.append(rid)
    return out


def test_rescan_records_one_round_boundary_with_via_actor_floor_and_requeued(repos):
    bid = _batch(repos, n=2)
    # 자식이 없을 때 -- floor NULL(그보다 오래된 자식이 없다)
    assert repos.batches.rescan_if_terminal(bid, actor="alice")["requeued"] == 2
    r = _rounds(repos.db, bid)
    assert [(x["round_seq"], x["floor_order"], x["via"], x["actor"], x["requeued"]) for x in r] == \
        [(1, None, "rescan", "alice", 2)]
    # 자식이 생긴 뒤의 회차 -- floor = 그 순간 이 배치 자식의 MAX(commit_order)
    kids = _child(repos, bid, 3)
    top = max(repos.requests.get(k)["commit_order"] for k in kids)
    repos.db.execute("UPDATE batches SET status = 'Completed' WHERE batch_id = :b", {"b": bid})
    repos.db.execute("UPDATE batch_items SET status = 'Succeeded' WHERE batch_id = :b", {"b": bid})
    other = _batch(repos)                                      # 다른 배치의 자식은 경계에 섞이지 않는다
    _child(repos, other, 2)
    assert repos.batches.rescan_if_terminal(bid, actor="bob") is not None
    r = _rounds(repos.db, bid)
    assert [(x["round_seq"], x["floor_order"], x["actor"]) for x in r] == [(1, None, "alice"), (2, top, "bob")]


def test_losing_rescan_leaves_no_round_and_bad_via_is_refused(repos):
    bid = _batch(repos, status="Running")
    assert repos.batches.rescan_if_terminal(bid, actor="ops") is None
    assert _rounds(repos.db, bid) == []
    done = _batch(repos)
    with pytest.raises(ValueError):
        with repos.db.transaction():
            repos.batches._rescan_locked(done, to_status="Running", via="other", actor="ops")
    assert repos.batches.get(done)["status"] == "Completed" and _rounds(repos.db, done) == []


def test_round_history_is_trimmed_to_the_cap(repos, monkeypatch):
    from dms.repositories import batches as batches_mod
    monkeypatch.setattr(batches_mod, "BATCH_ROUND_HISTORY_CAP", 3)
    bid = _batch(repos)
    for _ in range(5):
        assert repos.batches.rescan_if_terminal(bid, actor="ops") is not None
        repos.db.execute("UPDATE batches SET status = 'Completed' WHERE batch_id = :b", {"b": bid})
    assert [x["round_seq"] for x in _rounds(repos.db, bid)] == [3, 4, 5]


def test_fire_records_the_round_as_schedule(repos):
    bid = _batch(repos)
    _child(repos, bid, 2)
    sid = _schedule(repos, bid)
    assert _tick(repos) == [(sid, "fired")]
    r = _rounds(repos.db, bid)
    assert len(r) == 1 and r[0]["via"] == "schedule" and r[0]["actor"] == SCHEDULER_ACTOR and r[0]["requeued"] == 2
    assert r[0]["floor_order"] is not None


def test_lost_fire_rolls_back_the_round_boundary_too(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    row = repos.batch_schedules.get(sid)
    # 사람의 수정이 끼었다(updated_at 이 다르다) -- 리셋과 회차 경계가 함께 롤백된다
    stale = {**row, "updated_at": "2000-01-01T00:00:00Z"}
    assert repos.batch_schedules.fire(stale, next_run_at=NEXT, now=NOW, verify=lambda b, i: "fp",
                                      armed_env_listed=True, requester_env_listed=True) is None
    assert _rounds(repos.db, bid) == [] and repos.batches.get(bid)["status"] == "Completed"


# --- 건너뜀·놓침 ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status,reason", [
    ("Running", "batch_schedule_batch_running"),
    ("Previewing", "batch_schedule_batch_running"),
    ("PreviewReady", "batch_schedule_awaiting_confirm"),
    ("Cancelling", "batch_schedule_batch_cancelling"),
    ("Weird", "batch_not_rescannable"),
])
def test_non_terminal_batch_skips_that_slot_only(repos, status, reason):
    bid = _batch(repos, status=status)
    sid = _schedule(repos, bid)
    assert _tick(repos) == [(sid, "skipped")]
    s = repos.batch_schedules.get(sid)
    assert s["enabled"] is True and s["next_run_at"] == NEXT and s["last_reason_code"] == reason
    run = _runs(repos, sid)[0]
    assert run["outcome"] == "skipped" and run["reason_code"] == reason and run["batch_status"] == status
    assert run["requeued"] is None                               # 건너뜀은 NULL(0 아님 -- null≠0)
    assert repos.batches.get(bid)["status"] == status
    assert _events(repos.db, "batch_schedule_skipped") == []      # 건너뜀은 이력 행만


def test_maintenance_and_drain_skip(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    repos.db.execute("UPDATE control_state SET maintenance = 1 WHERE id = 1")
    assert _tick(repos) == [(sid, "skipped")]
    assert _runs(repos, sid)[0]["reason_code"] == "maintenance_mode"
    repos.db.execute("UPDATE control_state SET maintenance = 0, drain = 1 WHERE id = 1")
    assert _tick(repos, now="2026-11-17T17:00:05Z") == [(sid, "skipped")]
    assert _runs(repos, sid)[0]["reason_code"] == "batch_schedule_drain"
    assert repos.batches.get(bid)["status"] == "Completed"


def test_privileged_disabled_skips_without_pausing(repos):
    sid = _schedule(repos, _batch(repos))
    assert _tick(repos, settings=_Off) == [(sid, "skipped")]
    s = repos.batch_schedules.get(sid)
    assert s["enabled"] is True and s["last_reason_code"] == "batch_schedule_privileged_disabled"


def test_empty_batch_skips(repos):
    bid = _batch(repos, n=1)
    repos.db.execute("DELETE FROM batch_items WHERE batch_id = :b", {"b": bid})
    sid = _schedule(repos, bid)
    assert _tick(repos) == [(sid, "skipped")]
    assert _runs(repos, sid)[0]["reason_code"] == "batch_schedule_empty"


def test_storage_not_ready_skips_without_pausing(repos):
    sid = _schedule(repos, _batch(repos))
    repos.storages.set_status("s1", "Unknown")
    assert _tick(repos) == [(sid, "skipped")]
    s = repos.batch_schedules.get(sid)
    assert s["enabled"] is True and s["last_reason_code"] == "storage_not_ready"


def test_late_beyond_grace_is_missed_once_then_next_slot(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    late = "2026-11-16T18:00:06Z"                                # 유예 1시간 + 6초
    assert _tick(repos, now=late) == [(sid, "missed")]
    s = repos.batch_schedules.get(sid)
    assert s["next_run_at"] == NEXT and s["last_reason_code"] == "batch_schedule_missed"
    assert repos.batches.get(bid)["status"] == "Completed"        # 몰아서 쏘지 않는다
    assert len(_events(repos.db, "batch_schedule_missed")) == 1


def test_within_grace_still_fires(repos):
    sid = _schedule(repos, _batch(repos))
    assert _tick(repos, now="2026-11-16T17:59:59Z") == [(sid, "fired")]


def test_next_slot_is_strictly_after_max_of_due_and_now(repos):
    """시계가 뒤로 가도(now < 예정) 같은 슬롯을 두 번 쏘지 않는다 -- 다음 슬롯은 max(예정, 지금) 뒤."""
    bid = _batch(repos, status="Running")
    sid = _schedule(repos, bid)
    row = repos.batch_schedules.due(NOW, limit=5)[0]
    assert next_run_after(row["kind"], row["spec"], max(row["next_run_at"], "2026-11-16T16:00:00Z")) == NEXT
    _tick(repos)
    assert repos.batch_schedules.get(sid)["next_run_at"] == NEXT


# --- 일시정지 -------------------------------------------------------------------------------------------------------

def test_owner_not_privileged_pauses_with_audit(repos):
    repos.accounts.create("lost", "p", "admin", actor="t")
    sid = _schedule(repos, _batch(repos), armed_by="lost")
    assert _tick(repos) == [(sid, "paused")]
    _paused_with(repos, sid, "batch_schedule_owner_not_privileged")
    a = _audit(repos.db, "batch_schedule", "auto_pause")
    assert len(a) == 1 and a[0]["actor"] == SCHEDULER_ACTOR
    assert json.loads(a[0]["after_state"])["paused_reason_code"] == "batch_schedule_owner_not_privileged"
    assert len(_events(repos.db, "batch_schedule_paused")) == 1
    assert _tick(repos, now="2026-11-17T17:00:05Z") == []          # 일시정지는 다음이 없다


@pytest.mark.parametrize("requester,auth", [("lost", "session"), ("ops", "token"), ("ops", None)])
def test_requester_not_privileged_or_not_session_pauses(repos, requester, auth):
    """k8s 차이: 자격 없는 요청자의 배치 자식은 강등되지 않고 거부된다 -- 회차 전에 멈춘다."""
    repos.accounts.create("lost", "p", "admin", actor="t")
    sid = _schedule(repos, _batch(repos, requester=requester, auth=auth))
    assert _tick(repos) == [(sid, "paused")]
    _paused_with(repos, sid, "batch_schedule_requester_not_privileged")


def test_unsupported_operation_pauses(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    repos.db.execute("UPDATE batches SET operation = 'bogus' WHERE batch_id = :b", {"b": bid})
    assert _tick(repos) == [(sid, "paused")]
    _paused_with(repos, sid, "batch_schedule_unsupported_operation")


def test_stale_options_pause_with_the_validation_code(repos):
    bid = _batch(repos, op="rm", n=1)
    sid = _schedule(repos, bid, destructive=True)
    repos.db.execute("UPDATE batches SET options = :o WHERE batch_id = :b", {"o": json.dumps({}), "b": bid})
    assert _tick(repos) == [(sid, "paused")]
    reason = repos.batch_schedules.get(sid)["paused_reason_code"]
    assert reason not in (None, "batch_schedule_batch_changed")    # 옵션 검증 사유가 먼저(rm recursive 동의)


def test_deleted_batch_row_pauses(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    repos.db.execute("DELETE FROM batches WHERE batch_id = :b", {"b": bid})
    assert _tick(repos) == [(sid, "paused")]
    _paused_with(repos, sid, "batch_not_found")


def test_broken_rule_pauses_invalid(repos):
    sid = _schedule(repos, _batch(repos))
    repos.db.execute("UPDATE batch_schedules SET spec = '{broken' WHERE schedule_id = :s", {"s": sid})
    assert _tick(repos) == [(sid, "paused")]
    _paused_with(repos, sid, "invalid_batch_schedule")


def test_unreadable_item_json_pauses_instead_of_raising_every_tick(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    repos.db.execute("UPDATE batch_items SET payload = '{broken' WHERE batch_id = :b AND seq = 0", {"b": bid})
    assert _tick(repos) == [(sid, "paused")]
    _paused_with(repos, sid, "batch_schedule_batch_unreadable")
    assert _events(repos.db, "batch_schedule_error") == []


# --- 설계 비판 #6: planner 가 결정적으로 거부할 원인을 회차 전에 ---------------------------------------------------------

def test_deleted_storage_pauses_storage_missing(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    repos.db.execute("DELETE FROM storages WHERE storage_name = 's1'")
    assert _tick(repos) == [(sid, "paused")]
    _paused_with(repos, sid, "storage_missing")
    assert repos.batches.get(bid)["status"] == "Completed"        # 전 항목 Rejected 회차를 만들지 않는다


def test_disabled_storage_pauses_storage_disabled(repos):
    bid = _batch(repos, op="sync")
    sid = _schedule(repos, bid)
    repos.db.execute("UPDATE storages SET enabled = 0 WHERE storage_name = 's2'")   # sync 목적지
    assert _tick(repos) == [(sid, "paused")]
    _paused_with(repos, sid, "storage_disabled")


@pytest.mark.parametrize("subject_type,subject", [("requester", "ops"), ("owner", "svc")])
def test_denylisted_requester_or_owner_pauses_identity_denied(repos, subject_type, subject):
    bid = _batch(repos)
    if subject_type == "owner":
        repos.db.execute("UPDATE batches SET owner_username = 'svc' WHERE batch_id = :b", {"b": bid})
    sid = _schedule(repos, bid)
    repos.control.deny(subject_type, subject, "test", "t")
    assert _tick(repos) == [(sid, "paused")]
    _paused_with(repos, sid, "identity_denied")


# --- 설계 비판 #5: 파괴 여부·지문 ------------------------------------------------------------------------------------

def test_destructive_schedule_fires_while_fingerprint_matches(repos):
    bid = _batch(repos, op="rm", n=1)
    sid = _schedule(repos, bid, destructive=True)
    assert repos.batch_schedules.get(sid)["ack_fingerprint"].startswith("sha256:")
    assert _tick(repos) == [(sid, "fired")]
    assert repos.batches.get(bid)["status"] == "Previewing"
    after = json.loads(_audit(repos.db, "batch", "scheduled_rescan")[0]["after_state"])
    assert after["fingerprint"] == repos.batch_schedules.get(sid)["ack_fingerprint"]


def test_non_destructive_sync_turned_destructive_pauses(repos):
    """무장 때 비파괴(지문 NULL)였던 sync 에 나중에 delete 를 켰다 -- NULL 을 「다름」으로 읽어 멈춘다(동의 없는 root 미러 삭제 금지)."""
    bid = _batch(repos, op="sync")
    sid = _schedule(repos, bid)
    assert repos.batch_schedules.get(sid)["ack_fingerprint"] is None
    repos.db.execute("UPDATE batches SET options = :o WHERE batch_id = :b",
                     {"o": json.dumps({"delete": True}), "b": bid})
    assert _tick(repos) == [(sid, "paused")]
    _paused_with(repos, sid, "batch_schedule_batch_changed")
    assert repos.batches.get(bid)["status"] == "Completed"


@pytest.mark.parametrize("change", ["auto_confirm_on", "option", "items_replaced", "storage_path"])
def test_destructive_schedule_pauses_when_the_consented_batch_changes(repos, change):
    bid = _batch(repos, op="rm", n=1)
    sid = _schedule(repos, bid, destructive=True)
    if change == "auto_confirm_on":
        repos.db.execute("UPDATE batches SET auto_confirm_by = 'ops' WHERE batch_id = :b", {"b": bid})
    elif change == "option":
        # 검증을 통과하는 모양이어야 지문 판정까지 간다(rm 옵션 집합 -- domain._OPTIONS)
        repos.db.execute("UPDATE batches SET options = :o WHERE batch_id = :b",
                         {"o": json.dumps({"recursive": True, "quiet": True}), "b": bid})
    elif change == "items_replaced":
        repos.db.execute("UPDATE batch_items SET payload = :p WHERE batch_id = :b",
                         {"p": json.dumps({"storage": "s1", "target": "elsewhere"}), "b": bid})
    else:
        repos.db.execute("UPDATE storages SET mount_path = '/mnt/other', managed_root = '/mnt/other/m' "
                         "WHERE storage_name = 's1'")
    assert _tick(repos) == [(sid, "paused")]
    _paused_with(repos, sid, "batch_schedule_batch_changed")
    assert repos.batches.get(bid)["status"] == "Completed"


def test_fire_verify_rechecks_with_the_locked_rows(repos):
    """판정과 발사 사이에 CSV 교체가 커밋됐다 -- verify 가 잠근 행으로 지문을 다시 보고 롤백한다."""
    bid = _batch(repos, op="rm", n=1)
    sid = _schedule(repos, bid, destructive=True)
    real_fire = repos.batch_schedules.fire

    def racing_fire(row, **kw):
        repos.db.execute("UPDATE batch_items SET payload = :p WHERE batch_id = :b",
                         {"p": json.dumps({"storage": "s1", "target": "sneaky"}), "b": bid})
        return real_fire(row, **kw)
    repos.batch_schedules.fire = racing_fire
    assert _tick(repos) == [(sid, "lost")]
    assert repos.batches.get(bid)["status"] == "Completed"
    repos.batch_schedules.fire = real_fire
    assert _tick(repos) == [(sid, "paused")]
    _paused_with(repos, sid, "batch_schedule_batch_changed")


# --- 설계 비판 #7: 예외가 반복되는 행 -------------------------------------------------------------------------------

def test_poison_rows_do_not_starve_a_healthy_schedule(repos, monkeypatch):
    poison = set()
    for i in range(TICK_LIMIT + 1):
        bid = _batch(repos, n=1)
        poison.add(bid)
        _schedule(repos, bid, next_at="2026-11-16T16:59:00Z")     # 앞자리(정상 행보다 이른 예정)
    healthy_bid = _batch(repos, n=1)
    healthy = _schedule(repos, healthy_bid)
    real_get = repos.batches.get

    def get(bid):
        if bid in poison:
            raise RuntimeError("boom")
        return real_get(bid)
    monkeypatch.setattr(repos.batches, "get", get)
    first = _tick(repos)
    assert len(first) == TICK_LIMIT and {r for _, r in first} == {"error"}
    second = _tick(repos, now="2026-11-16T17:00:10Z")            # 백오프 중인 독 행은 due 에서 빠진다
    assert (healthy, "fired") in second
    assert {r for s, r in second if s != healthy} == {"error"}
    assert len(second) == 2                                      # 남은 독 행 1 + 정상 행
    assert repos.batches.get(healthy_bid)["status"] == "Running"
    # 같은 슬롯의 오류 이벤트는 스케줄마다 한 번(백오프 뒤 다시 실패해도 늘지 않는다)
    assert len(_events(repos.db, "batch_schedule_error")) == TICK_LIMIT + 1
    third = _tick(repos, now="2026-11-16T17:01:30Z")             # 백오프(60초) 뒤 다시 시도
    assert len(third) == TICK_LIMIT and {r for _, r in third} == {"error"}
    assert len(_events(repos.db, "batch_schedule_error")) == TICK_LIMIT + 1


def test_persistent_error_beyond_grace_records_missed_and_moves_on(repos, monkeypatch):
    bid = _batch(repos, n=1)
    sid = _schedule(repos, bid)
    real_get = repos.batches.get
    monkeypatch.setattr(repos.batches, "get",
                        lambda b: (_ for _ in ()).throw(RuntimeError("boom")) if b == bid else real_get(b))
    assert _tick(repos) == [(sid, "error")]
    assert repos.batch_schedules.get(sid)["next_run_at"] == SLOT    # 유예 안: 슬롯을 지킨다
    assert _tick(repos, now="2026-11-16T18:00:30Z") == [(sid, "missed")]
    s = repos.batch_schedules.get(sid)
    assert s["next_run_at"] == NEXT and s["last_reason_code"] == "batch_schedule_missed"
    assert sid not in batch_scheduler._BACKOFF


def test_transient_error_retries_after_backoff_and_fires(repos, monkeypatch):
    bid = _batch(repos, n=1)
    sid = _schedule(repos, bid)
    calls = {"n": 0}
    real_get = repos.batches.get

    def flaky(b):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("blip")
        return real_get(b)
    monkeypatch.setattr(repos.batches, "get", flaky)
    assert _tick(repos) == [(sid, "error")]
    assert _tick(repos, now="2026-11-16T17:00:30Z") == []         # 백오프 중
    assert _tick(repos, now="2026-11-16T17:01:10Z") == [(sid, "fired")]
    assert sid not in batch_scheduler._BACKOFF


# --- 이력 상한 · 끄기 · 삭제 ----------------------------------------------------------------------------------------

def test_run_history_is_capped_per_schedule(repos):
    bid = _batch(repos, status="Running")
    sid = _schedule(repos, bid)
    row = repos.batch_schedules.get(sid)
    for i in range(RUN_HISTORY_CAP + 5):
        repos.batch_schedules._insert_run(row, outcome="skipped", reason_code="batch_schedule_batch_running",
                                          now=NOW, batch_status="Running")
    runs = _runs(repos, sid)
    assert len(runs) == RUN_HISTORY_CAP
    assert runs[0]["run_id"] > runs[-1]["run_id"]


def test_disable_keeps_auto_pause_evidence_and_is_idempotent(repos):
    repos.accounts.create("lost", "p", "admin", actor="t")
    sid = _schedule(repos, _batch(repos), armed_by="lost")
    _tick(repos)
    assert repos.batch_schedules.disable(sid, actor="ops") is False   # 이미 꺼짐(일시정지) -- 사유를 지우지 않는다
    assert repos.batch_schedules.get(sid)["paused_reason_code"] == "batch_schedule_owner_not_privileged"


def test_disable_for_batch_locked_runs_inside_the_callers_transaction(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    with repos.db.transaction():
        assert repos.batch_schedules.disable_for_batch_locked(bid, actor="ops", now=NOW) == sid
    s = repos.batch_schedules.get(sid)
    assert s["enabled"] is False and s["next_run_at"] is None and s["paused_reason_code"] is None
    a = _audit(repos.db, "batch_schedule", "disable")
    assert len(a) == 1 and json.loads(a[0]["after_state"])["via"] == "batch_cancel"
    with repos.db.transaction():
        assert repos.batch_schedules.disable_for_batch_locked(bid, actor="ops", now=NOW) is None   # 이미 꺼짐
        assert repos.batch_schedules.disable_for_batch_locked("nope", actor="ops", now=NOW) is None
    assert _tick(repos) == []


def test_delete_removes_schedule_then_runs_and_audits(repos):
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    _tick(repos)
    repos.batch_schedules.delete(sid, actor="ops")
    assert repos.batch_schedules.get(sid) is None
    assert _runs(repos, sid) == []
    a = _audit(repos.db, "batch_schedule", "delete")
    assert len(a) == 1 and json.loads(a[0]["before_state"])["run_count"] == 1


def test_enable_rearms_with_next_slot_after_now_and_clears_pause(repos):
    repos.accounts.create("lost", "p", "admin", actor="t")
    sid = _schedule(repos, _batch(repos), armed_by="lost")
    _tick(repos)
    assert repos.batch_schedules.enable(sid, actor="ops", arm=lambda b, i: None) is True
    s = repos.batch_schedules.get(sid)
    assert s["enabled"] is True and s["armed_by"] == "ops" and s["paused_reason_code"] is None
    assert s["next_run_at"] is not None and s["next_run_at"] > utc_now_iso()   # 바로 쏘지 않는다 -- 지금 뒤 첫 슬롯
    assert repos.batch_schedules.enable(sid, actor="ops", arm=lambda b, i: None) is False    # 멱등


# --- 설계 비판 #10: 잠금 순서(PG) --------------------------------------------------------------------------------------

class _PgSpy:
    """dialect 만 postgresql 로 보이는 sqlite 프록시 -- 문장을 전부 기록하고 FOR UPDATE 를 떼어 실행한다."""
    dialect = "postgresql"

    def __init__(self, db):
        self._db, self.sql = db, []

    def __getattr__(self, name):
        return getattr(self._db, name)

    def _run(self, fn, sql, params):
        self.sql.append(sql)
        return fn(sql.replace(" FOR UPDATE", ""), params)

    def query(self, sql, params=None):
        return self._run(self._db.query, sql, params)

    def query_one(self, sql, params=None):
        return self._run(self._db.query_one, sql, params)

    def execute(self, sql, params=None):
        return self._run(self._db.execute, sql, params)

    def execute_count(self, sql, params=None):
        return self._run(self._db.execute_count, sql, params)


def _touch_order(sql_list):
    """배치 계열 네 테이블을 **처음 잠그는**(SELECT … FOR UPDATE·UPDATE·DELETE·INSERT) 순서 -- 평범한 읽기는 잠그지 않는다."""
    import re
    from dms.repositories.batches import BATCH_DELETE_TABLES
    out = []
    for q in sql_list:
        s = " ".join(q.split())
        m = re.match(r"(?:UPDATE|DELETE FROM|INSERT INTO) (\w+)", s)
        if m is None and s.endswith("FOR UPDATE"):
            m = re.search(r"FROM (\w+)", s)
        if m is not None and m.group(1) in BATCH_DELETE_TABLES and m.group(1) not in out:
            out.append(m.group(1))
    return out


def test_every_path_locks_items_batch_schedule_then_runs_on_postgres(repos):
    """항목 → 배치 → 회차 경계 → 스케줄 → 회차 이력(batches.BATCH_DELETE_TABLES 순서) -- 발사·기록·배치 기록 삭제·배치 단위
    삭제·취소와 함께 끄기·스케줄 무장·스케줄 삭제·사람 :rescan 이 모두 같은 방향이라 PG 에서 서로 상대 행을 기다리지 않는다(거꾸로인
    경로가 하나라도 있으면 40P01 로 한쪽이 끊긴다). 회차 보존 정리 청크는 requests → data_jobs → batch_schedules(맨 끝)만 쥔다."""
    from dms.repositories.batch_schedules import BatchSchedulesRepository
    from dms.repositories.batches import BATCH_DELETE_TABLES, BatchesRepository
    from dms.repositories.request_purges import RequestPurgesRepository
    MAX_BATCH_DELETE_CHILDREN = 50000           # 설정 기본값(2026-11-19 -- 모듈 상수는 없앴다)
    db = repos.db
    full = list(BATCH_DELETE_TABLES)
    assert full == ["batch_items", "batches", "batch_deletions", "batch_rounds", "batch_schedules", "batch_schedule_runs"]
    # 스케줄 발사·기록은 묘비 표(batch_deletions, 2026-11-19)를 만지지 않는다 -- 나머지는 같은 순서.
    fire_order = [t for t in full if t != "batch_deletions"]

    fired = _batch(repos)
    _schedule(repos, fired)
    row = repos.batch_schedules.due(NOW, limit=5)[0]
    spy = _PgSpy(db)
    assert BatchSchedulesRepository(spy).fire(row, next_run_at=NEXT, now=NOW, verify=lambda b, i: "fp",
                                              armed_env_listed=True, requester_env_listed=True) is not None
    assert _touch_order(spy.sql) == fire_order

    skipped = _batch(repos, status="Running")
    _schedule(repos, skipped)
    row = [r for r in repos.batch_schedules.due(NOW, limit=5) if r["batch_id"] == skipped][0]
    spy = _PgSpy(db)
    assert BatchSchedulesRepository(spy).record(row, outcome="skipped", reason_code="batch_schedule_batch_running",
                                                next_run_at=NEXT, now=NOW, batch_status="Running") is True
    assert _touch_order(spy.sql) == ["batch_schedules", "batch_schedule_runs"]

    for delete in (lambda r, bid: BatchesRepository(r).delete(bid, actor="ops") is True,
                   lambda r, bid: RequestPurgesRepository(r).delete_batch(
                       bid, expected_request_count=0, actor="ops", artifact_base=None, quiet_seconds=60,
                       max_children=MAX_BATCH_DELETE_CHILDREN)["deleted"] is True):
        bid = _batch(repos)
        sid = _schedule(repos, bid)
        repos.batch_schedules._insert_run(repos.batch_schedules.get(sid), outcome="skipped", reason_code="x", now=NOW,
                                          batch_status="Running")
        spy = _PgSpy(db)
        assert delete(spy, bid)
        assert _touch_order(spy.sql) == full

    # 백그라운드 배치 단위 삭제(2026-11-19): 묘비(B단계)와 마무리(셋째 배치 삭제 경로)도 같은 방향이다.
    bid = _batch(repos)
    sid = _schedule(repos, bid)
    repos.batch_schedules._insert_run(repos.batch_schedules.get(sid), outcome="skipped", reason_code="x", now=NOW,
                                      batch_status="Running")
    spy = _PgSpy(db)
    assert RequestPurgesRepository(spy).delete_batch_auto(
        bid, expected_request_count=0, actor="ops", artifact_base=None, quiet_seconds=60,
        cap=MAX_BATCH_DELETE_CHILDREN, sync_max=0, allow_sync=False)["deleting"] is True
    assert _touch_order(spy.sql) == full
    spy = _PgSpy(db)
    assert RequestPurgesRepository(spy).finish_batch_delete(bid)["done"] is True
    assert _touch_order(spy.sql) == full

    cancelled = _batch(repos, status="Running", item_status="Queued")
    _schedule(repos, cancelled, next_at=None)
    spy = _PgSpy(db)
    sched = BatchSchedulesRepository(spy)
    assert BatchesRepository(spy).begin_cancel(
        cancelled, from_states=("Running",),
        in_txn=lambda now: sched.disable_for_batch_locked(cancelled, actor="ops", now=now)) is True
    assert _touch_order(spy.sql) == ["batch_items", "batches", "batch_schedules"]

    armed = _batch(repos)
    sid = _schedule(repos, armed, next_at=None)
    repos.batch_schedules.disable(sid, actor="ops")
    for arm in (lambda r: r.update_rule(sid, kind="daily", spec=DAILY, next_run_at=NEXT, actor="ops",
                                        arm=lambda b, i: None),
                lambda r: r.enable(sid, actor="ops", arm=lambda b, i: None)):
        spy = _PgSpy(db)
        arm(BatchSchedulesRepository(spy))
        assert _touch_order(spy.sql) == ["batch_items", "batches", "batch_schedules"]
    spy = _PgSpy(db)
    BatchSchedulesRepository(spy).delete(sid, actor="ops")
    assert _touch_order(spy.sql) == ["batch_schedules", "batch_schedule_runs"]

    rescanned = _batch(repos)
    spy = _PgSpy(db)
    assert BatchesRepository(spy).rescan_if_terminal(rescanned, actor="ops") is not None
    assert _touch_order(spy.sql) == ["batch_items", "batches", "batch_rounds"]


def test_prune_chunk_locks_requests_then_jobs_then_schedule_last_on_postgres(repos):
    """회차 보존 정리 한 청크(RequestPurgesRepository.prune_round_children)의 잠금 순서: requests(request_id 순) → data_jobs →
    batch_schedules(맨 끝 -- 배치 단위 삭제 꼬리와 같은 방향). batch_items·batches·batch_rounds 는 잠그지 않는다."""
    import re
    from dms.repositories.request_purges import RequestPurgesRepository
    db = repos.db
    bid = _batch(repos)
    kids = _child(repos, bid, 3)
    for k in kids:
        repos.db.execute("UPDATE requests SET state = 'Succeeded', updated_at = '2000-01-01T00:00:00Z' "
                         "WHERE request_id = :r", {"r": k})
    _schedule(repos, bid)
    upto = max(repos.requests.get(k)["commit_order"] for k in kids)
    spy = _PgSpy(db)
    res = RequestPurgesRepository(spy).prune_round_children(bid, upto_order=upto, limit=10, actor=SCHEDULER_ACTOR,
                                                            artifact_base=None, quiet_seconds=60)
    assert sorted(res["deleted"]) == sorted(kids)
    locks = []
    for q in spy.sql:
        t = " ".join(q.split())
        if t.endswith("FOR UPDATE"):
            locks.append(re.search(r"FROM (\w+)", t).group(1))
    assert locks[:3] == ["requests", "data_jobs", "batch_schedules"], locks
    touched = {re.match(r"(?:UPDATE|DELETE FROM|INSERT INTO) (\w+)", " ".join(q.split())).group(1)
               for q in spy.sql if re.match(r"(?:UPDATE|DELETE FROM|INSERT INTO) ", " ".join(q.split()))}
    assert not touched & {"batch_items", "batches", "batch_rounds"}
    assert "batch_schedules" in touched


def test_schedule_hold_order_is_the_contract(repos):
    """0단계(깨진 규칙)가 유지보수보다, 유지보수가 놓침보다, 놓침이 자격보다 먼저다."""
    bid = _batch(repos)
    row = {"schedule_id": "x", "batch_id": bid, "kind": "daily", "spec": DAILY, "next_run_at": SLOT,
           "armed_by": "nobody", "ack_fingerprint": None}
    batch, items = repos.batches.get(bid), repos.batches.list_items(bid)
    late = "2026-11-16T19:00:00Z"
    hold = lambda r, control, now: schedule_hold(repos, _S, r, batch, items, control=control, now=now)  # noqa: E731
    assert hold({**row, "spec": None}, {"maintenance": 1}, late).reason_code == "invalid_batch_schedule"
    assert hold(row, {"maintenance": 1}, late).reason_code == "maintenance_mode"
    assert hold(row, {"maintenance": 0, "drain": 0}, late).reason_code == "batch_schedule_missed"
    assert hold(row, None, NOW).reason_code == "batch_schedule_owner_not_privileged"
    assert hold({**row, "armed_by": "ops"}, None, NOW).outcome == "fired"
