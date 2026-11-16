"""배치 스케줄 회차 보존 정리(2026-11-16 사용자 결정 「최근 5회차만 남기고 나머지 정리」 -- batch_round_prune.py,
BatchSchedulesRepository.prune_targets/_round_cut, RequestPurgesRepository.prune_round_children).

회차 = BatchesRepository._rescan_locked 한 번(여기선 사람 :rescan 경로 rescan_if_terminal 로 회차를 기록한다 -- 스케줄 회차도 같은
몸통이다, test_repo_batch_schedules 가 via schedule 을 본다). 기록 이전 자식(첫 실행·배포 전 회차)은 가장 오래된 회차 하나다.
설계 §9.1 의 목록을 따른다. PG 동시성(16)은 DMS_TEST_DB=postgresql 에서만 돈다."""
import dataclasses
import json
import os
import threading
import time
import uuid

import pytest

from dms import batch_round_prune
from dms.batch_round_prune import BACKLOG_MAX, CHUNK, BatchRoundPruner
from dms.batch_scheduler import SCHEDULER_ACTOR
from dms.db import Database
from dms.domain import DataJobState, RequestState
from dms.repositories import Repositories
from dms.repositories import batch_schedules as bs_mod
from dms.repositories.request_purges import PURGED_TABLES, SNAPSHOT_MAX_BYTES, RequestPurgesRepository

SCAN_ITEM = {"storage": "s1", "target": "a"}
OLD = "2026-01-01T00:00:00Z"
BASE = "/dms_shared/dms/artifacts"


@pytest.fixture(autouse=True)
def _fresh_module_state():
    for d in (batch_round_prune._BACKOFF, batch_round_prune._BLOCKED_WARNED, batch_round_prune._SCAN_FROM):
        d.clear()
    batch_round_prune._NEXT_AFTER[0] = None
    yield
    for d in (batch_round_prune._BACKOFF, batch_round_prune._BLOCKED_WARNED, batch_round_prune._SCAN_FROM):
        d.clear()
    batch_round_prune._NEXT_AFTER[0] = None


@pytest.fixture
def repos(db):
    r = Repositories(db)
    r.accounts.create("ops", "p", "admin", actor="t")
    r.storages.create(storage_name="s1", mount_path="/mnt/s1", managed_root="/mnt/s1/managed",
                      backend_type="nfs", actor="t")
    r.storages.set_status("s1", "Ready")
    return r


@pytest.fixture
def cfg(settings):
    return dataclasses.replace(settings, artifact_base_uri=f"file://{BASE}")


# --- 도우미 ---------------------------------------------------------------------------------------------------------

def _batch(repos, n=2):
    items = [{**SCAN_ITEM, "target": f"a{i}"} for i in range(n)]
    bid = repos.batches.create(operation="scan", requester_id="ops", actor="ops", max_concurrency=2, options={},
                               items=items, note=None, status="Completed", auth_method="session")
    for seq in range(n):
        repos.batches.set_item_status(bid, seq, "Succeeded")
    return bid


def _kid(repos, bid, *, job_state=DataJobState.SUCCEEDED, with_job=True, age=True, request_state=None):
    """그 배치의 자식 요청 1개(+ 잡 1개). job_state 가 비종단이면 요청은 request_state(기본 Cancelled -- 취소 경합 잔재)로
    둔다. with_job=False·request_state=None 이면 요청은 Pending(비종단)."""
    rid = repos.requests.create(operation="scan", requester_id="ops", actor="batch-orchestrator",
                                resource_key=f"k-{uuid.uuid4().hex[:8]}",
                                payload={**SCAN_ITEM, "run_as_root": True}, priority="mid", batch_id=bid,
                                auth_method="session")
    if with_job:
        repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
        plan_id = repos.data_jobs.create_plan(rid, actor="planner")
        jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid", storage_name="s1",
                                         target="a", options={}, tool="dscan", precondition={}, actor="planner",
                                         worker_pool={"identity": {"username": "root", "uid": 0, "gid": 0,
                                                                   "privileged": True}})
        repos.data_jobs.set_phase_ref(jid, "execution", f"vcjob/dms-scan-execution-{jid[:12]}")
        repos.data_jobs.set_job_state(jid, job_state, actor="stepper")
        if job_state in (DataJobState.SUCCEEDED, DataJobState.FAILED, DataJobState.CANCELLED):
            repos.requests.finalize_from_job(rid, job_state, actor="stepper")
        else:
            repos.db.execute("UPDATE requests SET state = :s WHERE request_id = :r",
                             {"s": request_state or "Cancelled", "r": rid})
        repos.scan_digests.put(jid, {"summary": {"total_files": 1}})
    elif request_state is not None:
        repos.db.execute("UPDATE requests SET state = :s WHERE request_id = :r", {"s": request_state, "r": rid})
    repos.observability.record_event(component="stepper", severity="info", event_type="x", request_id=rid)
    if age:
        _age(repos.db, rid)
    return rid


def _age(db, rid):
    db.execute("UPDATE requests SET updated_at = :t WHERE request_id = :r", {"t": OLD, "r": rid})
    db.execute("UPDATE data_jobs SET updated_at = :t WHERE request_id = :r", {"t": OLD, "r": rid})


def _kids(repos, bid, n):
    return [_kid(repos, bid) for _ in range(n)]


def _round(repos, bid, kids=2, actor="ops"):
    """회차 하나: 전체 재실행(회차 경계 기록) → 그 회차의 자식 kids 개 → 배치 종단(다음 회차가 이길 수 있게)."""
    assert repos.batches.rescan_if_terminal(bid, actor=actor) is not None
    made = _kids(repos, bid, kids)
    repos.db.execute("UPDATE batches SET status = 'Completed' WHERE batch_id = :b", {"b": bid})
    repos.db.execute("UPDATE batch_items SET status = 'Succeeded' WHERE batch_id = :b", {"b": bid})
    return made


def _schedule(repos, bid, *, enabled=True):
    return repos.batch_schedules.create(batch_id=bid, kind="daily", spec={"hour": 2, "minute": 0}, enabled=enabled,
                                        next_run_at="2030-01-01T00:00:00Z" if enabled else None, actor="ops",
                                        arm=lambda b, i: None)


def _prune(repos, cfg, keep=5):
    return BatchRoundPruner(repos, settings=dataclasses.replace(cfg, batch_schedule_keep_rounds=keep)).run_once()


def _alive(repos, bid):
    return {r["request_id"] for r in repos.db.query("SELECT request_id FROM requests WHERE batch_id = :b",
                                                    {"b": bid})}


def _audits(db, mc, op):
    return db.query("SELECT target_key, actor, before_state FROM audit_log WHERE mutation_class = :m AND operation = :o "
                    "ORDER BY id", {"m": mc, "o": op})


def _events(db, event_type):
    return db.query("SELECT severity, message, payload FROM events WHERE event_type = :t", {"t": event_type})


# --- 1·2·3: 회차 경계 ----------------------------------------------------------------------------------------------

def test_exactly_five_rounds_keep_everything_and_the_sixth_prunes_the_first(repos, cfg):
    bid = _batch(repos)
    first = _kids(repos, bid, 2)                       # 첫 실행(기록 이전 회차)
    kept = []
    for _ in range(4):
        kept += _round(repos, bid)                     # 기록 4 = 5회차
    _schedule(repos, bid)
    assert _prune(repos, cfg)["deleted"] == 0
    assert _alive(repos, bid) == set(first + kept)
    kept += _round(repos, bid)                         # 기록 5 = 6회차
    assert _prune(repos, cfg)["deleted"] == 2
    assert _alive(repos, bid) == set(kept) and len(kept) == 10


def test_pre_recording_rows_are_one_round_pruned_only_when_k_rounds_are_recorded(repos, cfg):
    bid = _batch(repos)
    old = _kids(repos, bid, 8)                         # 배포 전 4회차 흉내(기록 없음)
    _schedule(repos, bid)
    r1 = _round(repos, bid)
    for _ in range(3):
        _round(repos, bid)
    assert _prune(repos, cfg)["deleted"] == 0          # 기록 4 < K
    _round(repos, bid)                                 # 기록 5 -- 옛 자식 8 한꺼번에
    assert _prune(repos, cfg)["deleted"] == 8
    assert not set(old) & _alive(repos, bid) and set(r1) <= _alive(repos, bid)
    _round(repos, bid)                                 # 기록 6 -- r_1 자식만
    assert _prune(repos, cfg)["deleted"] == 2
    assert not set(r1) & _alive(repos, bid)
    assert len(_alive(repos, bid)) == 10


def test_rerun_children_belong_to_their_round(repos, cfg):
    bid = _batch(repos)
    _schedule(repos, bid)
    r1 = _round(repos, bid)
    r1_rerun = _kid(repos, bid)                        # r_1 안의 실패분 재실행 자식(회차를 만들지 않는다)
    r2 = _round(repos, bid)
    r2_rerun = _kid(repos, bid)
    for _ in range(4):
        _round(repos, bid)                             # 기록 6 -- 보존 r_2..r_6
    assert _prune(repos, cfg)["deleted"] == 3
    alive = _alive(repos, bid)
    assert not (set(r1) | {r1_rerun}) & alive
    assert set(r2) | {r2_rerun} <= alive


# --- 4·5: 보호 ----------------------------------------------------------------------------------------------------

def test_unfinished_and_recent_children_are_skipped_without_blocking_progress(repos, cfg):
    bid = _batch(repos)
    pending = _kid(repos, bid, with_job=False)                                 # (a) 요청 비종단
    running = _kid(repos, bid, job_state=DataJobState.RUNNING)                 # (b) 요청 종단·잡 비종단
    recent = _kid(repos, bid, age=False)                                       # (c) 조용한 창 안
    ok = _kids(repos, bid, 3)
    _schedule(repos, bid)
    for _ in range(5):
        _round(repos, bid)
    res = _prune(repos, cfg)
    assert res["deleted"] == 3                          # 막힌 자식이 앞자리(낮은 commit_order)여도 진행한다
    alive = _alive(repos, bid)
    assert {pending, running, recent} <= alive and not set(ok) & alive
    # (b) 의 잡이 종단이 되면 다음 틱에 지워진다
    jid = repos.db.query_one("SELECT job_id FROM data_jobs WHERE request_id = :r", {"r": running})["job_id"]
    repos.db.execute("UPDATE data_jobs SET state = 'Cancelled', updated_at = :t WHERE job_id = :j", {"t": OLD, "j": jid})
    _age(repos.db, recent)
    assert _prune(repos, cfg)["deleted"] == 2
    assert _alive(repos, bid) & {pending, running, recent} == {pending}


def test_only_blocked_children_emit_one_warning_per_hour(repos, cfg):
    bid = _batch(repos)
    stuck = _kid(repos, bid, with_job=False)
    _schedule(repos, bid)
    for _ in range(5):
        _round(repos, bid)
    assert _prune(repos, cfg)["deleted"] == 0
    assert _prune(repos, cfg)["deleted"] == 0
    ev = _events(repos.db, "batch_round_prune_blocked")
    assert len(ev) == 1 and ev[0]["severity"] == "warning" and bid in ev[0]["message"]
    assert stuck in _alive(repos, bid)


def test_pick_scans_in_bounded_windows_and_resumes_past_a_blocked_prefix(repos, cfg, monkeypatch):
    """고르기는 창 단위로 호출당 예산(PRUNE_PICK_SCAN_BUDGET)까지만 훑고, 다 못 훑으면 커서(_SCAN_FROM)로 다음 틱이 이어서 본다
    (2026-11-16 검증 지적 -- 한 문장에 판정을 두면 PG16 이 경계 이하 전부에 판정을 돌려 청크 비용이 대기량에 비례했다). 막힌
    자식이 예산보다 많이 앞에 있어도 진행이 멈추지 않고(커서를 버리면 매 틱 앞의 막힌 자식만 보다 끝난다 -- 아래 틱 2 가 깨진다),
    막힘 경고는 경계 끝까지 훑고도 고를 것이 없을 때만 낸다."""
    from dms.repositories import request_purges as rp_mod
    monkeypatch.setattr(rp_mod, "PRUNE_PICK_WINDOW", 3)
    monkeypatch.setattr(rp_mod, "PRUNE_PICK_SCAN_BUDGET", 6)
    bid = _batch(repos)
    blocked = [_kid(repos, bid, with_job=False) for _ in range(10)]     # 앞자리 10개 -- 요청 비종단(지울 수 없음)
    ok = _kids(repos, bid, 5)
    _schedule(repos, bid)
    for _ in range(5):
        _round(repos, bid)                              # 기록 5 -- 기록 이전 15개가 경계 이하
    seen = []
    real = repos.request_purges.prune_round_children

    def spy(*a, **kw):
        res = real(*a, **kw)
        seen.append((kw.get("scan_after"), res["picked"], res["scan_next"]))
        return res
    monkeypatch.setattr(repos.request_purges, "prune_round_children", spy)
    assert _prune(repos, cfg)["deleted"] == 0           # 틱 1: 막힌 6개(창 3 × 2)만 보고 예산 소진
    assert seen[-1][0] is None and seen[-1][1] == 0 and seen[-1][2] is not None
    assert _events(repos.db, "batch_round_prune_blocked") == []        # 아직 끝까지 못 봤다 -- 경고 없음
    assert _prune(repos, cfg)["deleted"] == 2           # 틱 2: 커서 뒤부터 -- 막힌 4개 + ok 2개(예산 소진)
    assert _prune(repos, cfg)["deleted"] == 3           # 틱 3: 나머지 ok 3개, 경계 끝(커서 지움)
    assert batch_round_prune._SCAN_FROM == {}
    assert not set(ok) & _alive(repos, bid) and set(blocked) <= _alive(repos, bid)
    assert _prune(repos, cfg)["deleted"] == 0           # 틱 4: 처음부터 다시 -- 예산 소진, 경고 없음
    assert _events(repos.db, "batch_round_prune_blocked") == []
    assert _prune(repos, cfg)["deleted"] == 0           # 틱 5: 경계 끝까지 봤는데 고를 것이 없다 -- 경고 1회
    assert len(_events(repos.db, "batch_round_prune_blocked")) == 1
    # 창은 FROM 서브쿼리의 LIMIT 이고 판정은 그 바깥이다(판정을 같은 WHERE 에 두면 PG 가 경계 이하 전부에 판정을 돌린다)
    sql = []
    orig_query = repos.db.query

    def capture(q, params=None):
        sql.append(" ".join(q.split()))
        return orig_query(q, params)
    monkeypatch.setattr(repos.db, "query", capture)
    repos.request_purges._prune_pick(bid, upto_order=10 ** 9, limit=1, quiet_cut=OLD)
    pick = [q for q in sql if "batch_items bi" in q]
    assert pick and all("ORDER BY batch_id, commit_order LIMIT :w) c" in q
                        and q.index("data_jobs j") < q.index("FROM (SELECT") for q in pick)


def test_item_referenced_children_are_never_pruned(repos, cfg, monkeypatch):
    bid = _batch(repos)
    ref = _kid(repos, bid)
    raced = _kid(repos, bid)
    free = _kid(repos, bid)
    repos.db.execute("UPDATE batch_items SET request_id = :r WHERE batch_id = :b AND seq = 0", {"r": ref, "b": bid})
    _schedule(repos, bid)
    for _ in range(5):
        _round(repos, bid)
    repos.db.execute("UPDATE batch_items SET request_id = :r WHERE batch_id = :b AND seq = 0", {"r": ref, "b": bid})
    # 고른 뒤·잠그기 전에 다른 항목이 raced 를 가리키게 되는 경합 -- 잠근 행 재판정이 막는다
    orig = repos.request_purges._prune_lock

    def racing_lock(rids):
        repos.db.execute("UPDATE batch_items SET request_id = :r WHERE batch_id = :b AND seq = 1",
                         {"r": raced, "b": bid})
        return orig(rids)
    monkeypatch.setattr(repos.request_purges, "_prune_lock", racing_lock)
    res = _prune(repos, cfg)
    assert res["deleted"] == 1
    alive = _alive(repos, bid)
    assert {ref, raced} <= alive and free not in alive
    audit = json.loads(_audits(repos.db, "batch", "prune_rounds")[0]["before_state"])
    assert audit["skipped"] == {"batch_child_not_deletable": 1}


# --- 6: 지금 회차·닻(commit_order 재사용 방지) ------------------------------------------------------------------------

def test_keep_one_leaves_the_anchor_child(repos, cfg):
    bid = _batch(repos)
    first = _kids(repos, bid, 2)
    _schedule(repos, bid)
    r1 = _round(repos, bid)
    r2 = _round(repos, bid)
    assert _prune(repos, cfg, keep=1)["deleted"] == 3
    # 직전 회차의 마지막 자식(닻 = r_2 의 경계)은 다음 회차까지 남는다
    assert _alive(repos, bid) == {r1[-1], *r2}
    assert not set(first) & _alive(repos, bid)


def test_kept_rounds_without_children_keep_the_anchor(repos, cfg):
    bid = _batch(repos)
    first = _kids(repos, bid, 2)
    _schedule(repos, bid)
    for _ in range(5):
        _round(repos, bid, kids=0)                      # 보존 회차가 자식을 하나도 만들지 못함
    assert _prune(repos, cfg)["deleted"] == 1
    assert _alive(repos, bid) == {first[-1]}


def _reuse_scenario(repos, cfg, bid):
    """닻이 전역 최대 commit_order 인 상태에서 정리 → 새 자식. (새 자식 commit_order, 최신 회차 경계)."""
    _kids(repos, bid, 2)
    _schedule(repos, bid)
    _round(repos, bid)                                 # 자식 있는 회차
    _round(repos, bid, kids=0)                         # 최신 회차 -- 자식을 만들기 전(경계 = 직전 회차의 마지막 자식)
    _prune(repos, cfg, keep=1)
    floor = repos.db.query_one("SELECT floor_order FROM batch_rounds WHERE batch_id = :b ORDER BY round_seq DESC LIMIT 1",
                               {"b": bid})["floor_order"]
    new = _kid(repos, bid)
    return repos.requests.get(new)["commit_order"], floor


def test_anchor_rule_prevents_commit_order_reuse(repos, cfg):
    bid = _batch(repos)
    order, floor = _reuse_scenario(repos, cfg, bid)
    assert order > floor


def test_the_reuse_test_breaks_without_the_anchor_rule(repos, cfg, monkeypatch):
    """그물 자기 검증: 닻 규칙을 뺀 변종(upto = cut)에선 위 테스트가 깨진다 -- 새 자식이 경계 이하 번호를 받아 다음 정리에서
    보존 회차 자식처럼 보이지 않게 된다(설계 비판 #1)."""
    real = bs_mod._cut_from

    def no_anchor(row):
        out = real(row)
        return None if out is None else {**out, "upto": out["cut"]}
    monkeypatch.setattr(bs_mod, "_cut_from", no_anchor)
    bid = _batch(repos)
    order, floor = _reuse_scenario(repos, cfg, bid)
    assert order <= floor


# --- 7·8: 청크·대기 상한·공평 순환 ------------------------------------------------------------------------------------

def _old_batch(repos, n_old, *, kept_kids=1):
    bid = _batch(repos)
    old = _kids(repos, bid, n_old)
    _schedule(repos, bid)
    for _ in range(5):
        _round(repos, bid, kids=kept_kids)
    return bid, old


def test_chunks_of_one_hundred_with_one_audit_row_each(repos, cfg, monkeypatch):
    monkeypatch.setattr(batch_round_prune, "BACKLOG_MAX", 1000)
    bid, old = _old_batch(repos, 250)
    res = _prune(repos, cfg)
    assert res["deleted"] == 250 and res["chunks"] == 3
    rows = _audits(repos.db, "batch", "prune_rounds")
    assert [json.loads(r["before_state"])["deleted_count"] for r in rows] == [CHUNK, CHUNK, 50]
    assert repos.batch_schedules.get_by_batch(bid)["pruned_total"] == 250


def test_backlog_cap_counts_only_this_loops_rows(repos, cfg):
    assert BACKLOG_MAX == 100
    bid, old = _old_batch(repos, 150)
    assert _prune(repos, cfg)["deleted"] == 100        # 한 틱 = 대기 행 상한만큼
    assert _prune(repos, cfg) == {"backlog": 100, "deleted": 0}   # 정리 행 100개가 아직 대기 중 -- 쉰다
    # 30개를 끝내면(정리 루프 흉내) 빈자리 30만큼만
    for row in repos.db.query("SELECT request_id, jobs FROM request_purges ORDER BY request_id LIMIT 30"):
        assert repos.request_purges.finish(row["request_id"], [j["job_id"] for j in json.loads(row["jobs"])])
    assert _prune(repos, cfg)["deleted"] == 30
    # 사람이 지운 작업의 대기 행(다른 actor)은 세지 않는다
    db = repos.db
    db.execute("UPDATE request_purges SET requested_by = 'alice'")
    assert repos.request_purges.pending_requested_by(SCHEDULER_ACTOR) == 0
    assert _prune(repos, cfg)["deleted"] == 20


def test_fair_rotation_serves_the_small_batch_in_the_same_tick(repos, cfg, monkeypatch):
    monkeypatch.setattr(batch_round_prune, "BACKLOG_MAX", 1000)
    monkeypatch.setattr(batch_round_prune, "CHUNK", 10)
    a, big = _old_batch(repos, 100)
    b, small = _old_batch(repos, 5)
    res = _prune(repos, cfg)
    assert not set(small) & _alive(repos, b)            # 큰 배치가 틱을 독차지하지 않는다
    assert res["deleted"] == 5 + 9 * 10 and res["chunks"] == 10      # 틱당 실제 청크 10(B 1 + A 9)
    assert len(set(big) & _alive(repos, a)) == 10
    assert _prune(repos, cfg)["deleted"] == 10          # 다음 틱이 이어서


# --- 9·10: 대상 범위 · 유지보수 ------------------------------------------------------------------------------------------

def test_only_batches_with_a_schedule_row_are_pruned(repos, cfg):
    plain = _batch(repos)
    _kids(repos, plain, 3)
    for _ in range(10):
        _round(repos, plain)
    assert _prune(repos, cfg)["deleted"] == 0           # 스케줄 없음
    off, old_off = _old_batch(repos, 2)
    paused, old_paused = _old_batch(repos, 2)
    repos.batch_schedules.disable(repos.batch_schedules.get_by_batch(off)["schedule_id"], actor="ops")
    repos.db.execute("UPDATE batch_schedules SET enabled = 0, next_run_at = NULL, paused_reason_code = 'x' "
                     "WHERE batch_id = :b", {"b": paused})
    assert _prune(repos, cfg)["deleted"] == 4           # 꺼짐·일시정지도 정리한다
    gone, old_gone = _old_batch(repos, 2)
    repos.batch_schedules.delete(repos.batch_schedules.get_by_batch(gone)["schedule_id"], actor="ops")
    assert _prune(repos, cfg)["deleted"] == 0           # 스케줄을 지우면 멈춘다
    assert set(old_gone) <= _alive(repos, gone)


@pytest.mark.parametrize("column", ["maintenance", "drain"])
def test_maintenance_and_drain_hold(repos, cfg, column):
    bid, old = _old_batch(repos, 2)
    repos.db.execute(f"UPDATE control_state SET {column} = 1 WHERE id = 1")
    assert _prune(repos, cfg) == {"held": True}
    assert set(old) <= _alive(repos, bid)


def test_invalid_keep_is_disabled(repos, cfg):
    bid, old = _old_batch(repos, 2)
    for keep in (0, 101, True, None):
        assert _prune(repos, cfg, keep=keep) == {"disabled": True}
    assert set(old) <= _alive(repos, bid)


# --- 11·12: 고아 없음 · 감사 -------------------------------------------------------------------------------------------

def test_no_orphans_and_one_outbox_row_per_child_that_finish_clears(repos, cfg):
    bid, old = _old_batch(repos, 3)
    jobs = {r["request_id"]: r["job_id"] for r in repos.db.query(
        "SELECT request_id, job_id FROM data_jobs WHERE request_id IN (:a, :b, :c)",
        {"a": old[0], "b": old[1], "c": old[2]})}
    assert _prune(repos, cfg)["deleted"] == 3
    db = repos.db
    for rid in old:
        jid = jobs[rid]
        for sql, p in (("SELECT COUNT(*) AS n FROM requests WHERE request_id = :x", rid),
                       ("SELECT COUNT(*) AS n FROM results WHERE request_id = :x", rid),
                       ("SELECT COUNT(*) AS n FROM plans WHERE request_id = :x", rid),
                       ("SELECT COUNT(*) AS n FROM data_jobs WHERE request_id = :x", rid),
                       ("SELECT COUNT(*) AS n FROM events WHERE request_id = :x", rid),
                       ("SELECT COUNT(*) AS n FROM state_transitions WHERE entity_id = :x", rid),
                       ("SELECT COUNT(*) AS n FROM state_transitions WHERE entity_id = :x", jid),
                       ("SELECT COUNT(*) AS n FROM scan_report_digests WHERE job_id = :x", jid)):
            assert db.query_one(sql, {"x": p})["n"] == 0, sql
    assert set(PURGED_TABLES) == {"requests", "results", "plans", "data_jobs", "state_transitions", "events",
                                  "scan_report_digests"}
    rows = {r["request_id"]: r for r in db.query("SELECT request_id, jobs, requested_by, artifact_base, kind "
                                                 "FROM request_purges")}
    assert set(rows) == set(old)
    for rid, row in rows.items():
        assert row["requested_by"] == SCHEDULER_ACTOR and row["artifact_base"] == BASE and row["kind"] is None
        assert [j["job_id"] for j in json.loads(row["jobs"])] == [jobs[rid]]
        assert repos.request_purges.finish(rid, [jobs[rid]])
    assert db.query_one("SELECT COUNT(*) AS n FROM request_purges")["n"] == 0


def test_one_summary_audit_row_per_chunk_and_no_per_request_rows(repos, cfg):
    bid, old = _old_batch(repos, 3)
    sid = repos.batch_schedules.get_by_batch(bid)["schedule_id"]
    top = max(repos.requests.get(r)["commit_order"] for r in old)
    _prune(repos, cfg)
    rows = _audits(repos.db, "batch", "prune_rounds")
    assert len(rows) == 1 and rows[0]["target_key"] == bid and rows[0]["actor"] == SCHEDULER_ACTOR
    a = json.loads(rows[0]["before_state"])
    assert a["deleted_count"] == 3 and a["job_count"] == 3 and a["states"] == {"Succeeded": 3}
    assert a["schedule_id"] == sid and a["keep_rounds"] == 5 and a["rounds_recorded"] == 5 and a["cut_round_seq"] == 1
    floor1 = repos.db.query_one("SELECT floor_order FROM batch_rounds WHERE batch_id = :b AND round_seq = 1",
                                {"b": bid})["floor_order"]
    assert a["upto_order"] == floor1 == top
    assert {c["request_id"] for c in a["children"]} == set(old)
    assert all(c["run_as_root"] is True and len(c["jobs"]) == 1 and c["jobs"][0]["tool"] == "dscan"
               for c in a["children"])
    assert a["truncated"] is False
    assert _audits(repos.db, "request", "delete") == []


def test_prune_audit_folds_into_32k():
    kids = [{"request_id": f"{i:032x}", "state": "Succeeded", "created_at": "x" * 900, "run_as_root": True,
             "jobs": [{"job_id": f"{i:032x}", "state": "Succeeded", "tool": "dscan" * 100}]} for i in range(100)]
    summary = {"batch_id": "b" * 32, "deleted_count": 100, "children": kids, "truncated": False}
    text = RequestPurgesRepository._prune_bounded(summary)
    out = json.loads(text)
    assert len(text.encode()) <= SNAPSHOT_MAX_BYTES and out["truncated"] is True
    assert out["children"] == [k["request_id"] for k in kids]       # 둘째 단계: id 목록
    huge = {**summary, "schedule_id": "s" * 40000}
    text = RequestPurgesRepository._prune_bounded(huge)
    assert len(text.encode()) <= SNAPSHOT_MAX_BYTES and json.loads(text)["truncated"] is True


# --- 13·14·15·17: 스케줄 칸 · 경합 · 격리 -----------------------------------------------------------------------------

def test_schedule_counters_accumulate_without_touching_updated_at_and_fire_still_wins(repos, cfg):
    from dms.batch_scheduler import BatchScheduler
    bid, old = _old_batch(repos, 2)
    s0 = repos.batch_schedules.get_by_batch(bid)
    assert s0["pruned_at"] is None and s0["pruned_total"] is None
    _prune(repos, cfg)
    s1 = repos.batch_schedules.get_by_batch(bid)
    assert s1["pruned_total"] == 2 and s1["pruned_at"] is not None
    assert s1["updated_at"] == s0["updated_at"] and s1["next_run_at"] == s0["next_run_at"]
    # 정리 직후 회차 -- 스케줄 CAS(updated_at·next_run_at)가 이긴다
    repos.db.execute("UPDATE batch_schedules SET next_run_at = '2026-11-16T17:00:00Z' WHERE batch_id = :b", {"b": bid})
    repos.db.execute("UPDATE batches SET status = 'Completed' WHERE batch_id = :b", {"b": bid})

    class _S:
        allow_privileged_requesters = True
        privileged_requesters = frozenset({"ops"})
        preview_ttl_seconds = 86400
    assert BatchScheduler(repos, settings=_S, now="2026-11-16T17:00:05Z").run_once()[0][1] == "fired"
    # 스케줄 회차도 회차다(기록 6) -- r_1 의 자식 1 이 더 정리되고 누적된다
    _prune(repos, cfg)
    assert repos.batch_schedules.get_by_batch(bid)["pruned_total"] == 3


def test_schedule_deleted_between_targets_and_chunk_is_schedule_gone(repos, cfg):
    bid, old = _old_batch(repos, 2)
    t = repos.batch_schedules.prune_targets(keep=5)[0]
    repos.batch_schedules.delete(t["schedule_id"], actor="ops")
    res = repos.request_purges.prune_round_children(bid, upto_order=t["upto"], limit=CHUNK, actor=SCHEDULER_ACTOR,
                                                    artifact_base=BASE, quiet_seconds=60)
    assert res["schedule_gone"] is True and res["deleted"] == []
    assert set(old) <= _alive(repos, bid)
    assert _audits(repos.db, "batch", "prune_rounds") == []


def test_one_failing_batch_is_isolated_and_backed_off(repos, cfg, monkeypatch):
    a, old_a = _old_batch(repos, 2)
    b, old_b = _old_batch(repos, 2)
    real = repos.request_purges.prune_round_children

    def flaky(batch_id, **kw):
        if batch_id == a:
            raise RuntimeError("boom")
        return real(batch_id, **kw)
    monkeypatch.setattr(repos.request_purges, "prune_round_children", flaky)
    assert _prune(repos, cfg)["deleted"] == 2
    assert not set(old_b) & _alive(repos, b) and set(old_a) <= _alive(repos, a)
    assert a in batch_round_prune._BACKOFF
    _prune(repos, cfg)                                  # 백오프 중 -- 다시 부르지 않고 이벤트도 다시 남기지 않는다
    ev = _events(repos.db, "batch_round_prune_error")
    assert len(ev) == 1 and ev[0]["severity"] == "error"


def test_concurrent_rescan_after_targets_only_prunes_the_old_cut(repos, cfg):
    bid, old = _old_batch(repos, 2)
    t = repos.batch_schedules.prune_targets(keep=5)[0]
    newer = _round(repos, bid)                          # cut 을 읽은 뒤 새 회차 -- 옛 cut 은 그대로 유효(덜 지울 뿐)
    res = repos.request_purges.prune_round_children(bid, upto_order=t["upto"], limit=CHUNK, actor=SCHEDULER_ACTOR,
                                                    artifact_base=BASE, quiet_seconds=60)
    assert sorted(res["deleted"]) == sorted(old)
    assert set(newer) <= _alive(repos, bid)


def test_preview_and_targets_use_the_same_cut(repos, cfg):
    bid, old = _old_batch(repos, 4)
    t = repos.batch_schedules.prune_targets(keep=5)[0]
    cut = repos.batch_schedules._round_cut(bid, keep=5)
    assert {k: t[k] for k in cut} == cut
    assert repos.batch_schedules.prune_preview(bid, keep=5) == {"rounds_recorded": 5, "would_prune": 4,
                                                                "would_prune_capped": False}


def test_loop_is_wired_only_with_the_purge_runner(db, settings):
    from dms.controller import build_loops
    from dms.purge_runner import StubPurgeRunner
    with_runner = build_loops(settings, Repositories(db), purge_runner=StubPurgeRunner())
    assert ("batch-round-prune", 30) in [(lp.name, lp.interval_seconds) for lp in with_runner]
    assert "batch-round-prune" not in [lp.name for lp in build_loops(settings, Repositories(db))]


# --- 16: PG 동시성(DMS_TEST_DB=postgresql 에서만) ----------------------------------------------------------------------

pg_only = pytest.mark.skipif(os.environ.get("DMS_TEST_DB") != "postgresql", reason="PG 동시성 -- 운영 방언에서만")


def _second(db):
    """같은 스키마를 보는 두 번째 커넥션(lock_timeout 2초 -- 교착·무한 대기를 실패로 드러낸다)."""
    url = os.environ["DMS_TEST_PG_URL"]
    schema = db.query_one("SELECT current_schema() AS s")["s"]
    other = Database.connect(f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{schema}")
    other.execute("SET lock_timeout = '2s'")
    return other


def _run_in_thread(fn):
    box = {}

    def body():
        try:
            box["result"] = fn()
        except BaseException as exc:      # noqa: BLE001 -- 스레드 예외를 본 스레드로 옮긴다
            box["error"] = exc
    t = threading.Thread(target=body)
    t.start()
    return t, box


@pg_only
def test_pg_prune_holding_requests_then_batch_delete_waits_without_deadlock(repos, cfg):
    repos.db.execute("SET lock_timeout = '2s'")
    bid, old = _old_batch(repos, 3)
    total = len(_alive(repos, bid))
    other = _second(repos.db)
    try:
        purges = RequestPurgesRepository(repos.db)
        orig = purges._prune_lock
        started = threading.Event()

        def slow_lock(rids):
            out = orig(rids)
            started.set()
            time.sleep(0.6)                       # requests·data_jobs 를 쥔 채 -- 배치 단위 삭제가 기다린다
            return out
        purges._prune_lock = slow_lock
        t = repos.batch_schedules.prune_targets(keep=5)[0]
        th, box = _run_in_thread(lambda: purges.prune_round_children(
            bid, upto_order=t["upto"], limit=CHUNK, actor=SCHEDULER_ACTOR, artifact_base=BASE, quiet_seconds=60))
        assert started.wait(5)
        r = RequestPurgesRepository(other).delete_batch(bid, expected_request_count=total, actor="ops",
                                                        artifact_base=BASE, quiet_seconds=60, max_children=5000)
        th.join(10)
        assert "error" not in box, box.get("error")
        assert len(box["result"]["deleted"]) == 3
        assert r["deleted"] is False and r["reason"] == "batch_changed"     # 확인 창의 수가 줄었다 -- 무변화
    finally:
        other._conn.close()


@pg_only
def test_pg_batch_delete_first_then_prune_finds_nothing(repos, cfg):
    repos.db.execute("SET lock_timeout = '2s'")
    bid, old = _old_batch(repos, 3)
    total = len(_alive(repos, bid))
    t = repos.batch_schedules.prune_targets(keep=5)[0]
    other = _second(repos.db)
    try:
        deleter = RequestPurgesRepository(other)
        orig = deleter._purge_locked
        started = threading.Event()

        def slow_purge(*a, **kw):
            started.set()
            time.sleep(0.6)                       # 요청·잡을 잠근 채
            return orig(*a, **kw)
        deleter._purge_locked = slow_purge
        th, box = _run_in_thread(lambda: deleter.delete_batch(
            bid, expected_request_count=total, actor="ops", artifact_base=BASE, quiet_seconds=60, max_children=5000))
        assert started.wait(5)
        res = RequestPurgesRepository(repos.db).prune_round_children(
            bid, upto_order=t["upto"], limit=CHUNK, actor=SCHEDULER_ACTOR, artifact_base=BASE, quiet_seconds=60)
        th.join(10)
        assert "error" not in box, box.get("error")
        assert box["result"]["deleted"] is True
        assert res["deleted"] == [] and res["lost"] is False
    finally:
        other._conn.close()


@pg_only
def test_pg_fire_waits_for_a_prune_holding_the_schedule_then_wins(repos, cfg):
    repos.db.execute("SET lock_timeout = '2s'")
    bid, old = _old_batch(repos, 2)
    repos.db.execute("UPDATE batch_schedules SET next_run_at = '2026-11-16T17:00:00Z' WHERE batch_id = :b", {"b": bid})
    t = repos.batch_schedules.prune_targets(keep=5)[0]
    other = _second(repos.db)
    try:
        purges = RequestPurgesRepository(repos.db)
        started = threading.Event()
        real = RequestPurgesRepository._prune_bounded

        def slow_bounded(summary):
            started.set()                         # 스케줄 행 FOR UPDATE 뒤 -- 회차가 기다린다
            time.sleep(0.6)
            return real(summary)
        purges._prune_bounded = slow_bounded
        th, box = _run_in_thread(lambda: purges.prune_round_children(
            bid, upto_order=t["upto"], limit=CHUNK, actor=SCHEDULER_ACTOR, artifact_base=BASE, quiet_seconds=60))
        assert started.wait(5)
        from dms.repositories.batch_schedules import BatchSchedulesRepository
        sched = BatchSchedulesRepository(other)
        row = sched.get_by_batch(bid)
        fired = sched.fire(row, next_run_at="2026-11-17T17:00:00Z", now="2026-11-16T17:00:05Z",
                           verify=lambda b, i: "fp", armed_env_listed=True, requester_env_listed=True)
        th.join(10)
        assert "error" not in box, box.get("error")
        assert len(box["result"]["deleted"]) == 2
        assert fired is not None                  # 정리는 updated_at 을 밀지 않는다 -- CAS 가 이긴다
    finally:
        other._conn.close()


@pg_only
def test_pg_two_prunes_delete_each_child_once(repos, cfg):
    repos.db.execute("SET lock_timeout = '2s'")
    bid, old = _old_batch(repos, 6)
    t = repos.batch_schedules.prune_targets(keep=5)[0]
    other = _second(repos.db)
    try:
        a, b = RequestPurgesRepository(repos.db), RequestPurgesRepository(other)
        go = threading.Barrier(2)

        def run(repo):
            go.wait(5)
            return repo.prune_round_children(bid, upto_order=t["upto"], limit=CHUNK, actor=SCHEDULER_ACTOR,
                                             artifact_base=BASE, quiet_seconds=60)
        t1, box1 = _run_in_thread(lambda: run(a))
        t2, box2 = _run_in_thread(lambda: run(b))
        t1.join(10)
        t2.join(10)
        assert "error" not in box1 and "error" not in box2, (box1.get("error"), box2.get("error"))
        deleted = box1["result"]["deleted"] + box2["result"]["deleted"]
        assert sorted(deleted) == sorted(old)
        assert repos.db.query_one("SELECT COUNT(*) AS n FROM request_purges")["n"] == 6
        assert repos.batch_schedules.get_by_batch(bid)["pruned_total"] == 6
    finally:
        other._conn.close()
