"""배치 스케줄 회차 보존 정리(2026-11-16 사용자 결정 「배치 회차가 계속 누적되는걸 원하지 않아 최근 10회차만」 -- dms-k8s 라인의
같은 기능(9166b8e2·0ec5f33b, 기본 5)을 옮겼다: batch_round_prune.py, BatchSchedulesRepository.prune_targets/_round_cut,
RequestPurgesRepository.prune_round_children, BatchesRepository._rescan_locked 의 회차 경계).

회차 = BatchesRepository._rescan_locked 한 번(여기선 주로 사람 :rescan 경로 rescan_if_terminal 로 회차를 기록한다 -- 스케줄 회차도
같은 몸통이다, 아래 「회차 경계」 절이 via schedule 을 본다). 기록 이전 자식(첫 실행·배포 전 회차)은 가장 오래된 회차 하나다.
보존 수는 기본값(10 -- conftest settings 는 Settings 기본)을 쓴다. 이 라인엔 PG 테스트 하네스가 없어 잠금 순서는 아래 PG 흉내
스파이(문장 순서)로 고정하고, dms-k8s 의 PG 동시성 테스트(test_pg_*, 실 행 잠금)는 dialect 가 postgresql 일 때만 돈다(맨 아래 절 --
sqlite 스위트에선 건너뛴다)."""
import dataclasses
import json
import uuid

import pytest

from dms import batch_round_prune
from dms.batch_round_prune import BACKLOG_MAX, CHUNK, BatchRoundPruner
from dms.batch_scheduler import SCHEDULER_ACTOR, BatchScheduler, storage_binding_digest
from dms.domain import DataJobState, RequestState
from dms.repositories import Repositories
from dms.repositories import batch_schedules as bs_mod
from dms.repositories.request_purges import PURGED_TABLES, SNAPSHOT_MAX_BYTES, RequestPurgesRepository

SCAN_ITEM = {"storage": "s1", "target": "a"}
OLD = "2026-01-01T00:00:00Z"
BASE = "/cephfs/dms/artifacts"
K = 10                                     # 이 라인의 기본 보존 회차 수(DMS_BATCH_SCHEDULE_KEEP_ROUNDS 기본값)
SLOT = "2026-11-16T17:00:00Z"
NOW = "2026-11-16T17:00:05Z"
NEXT = "2026-11-17T17:00:00Z"


class _S:
    # BatchScheduler 가 읽는 설정만(test_batch_scheduler_tick 의 _S 와 같은 모양).
    allow_privileged_requesters = True
    privileged_requesters = frozenset({"ops"})
    preview_ttl_seconds = 86400


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
                      backend_type="cephfs", actor="t")
    return r


@pytest.fixture
def cfg(settings):
    assert settings.batch_schedule_keep_rounds == K            # 기본값 그대로(사용자 결정 10)
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
    둔다. with_job=False·request_state=None 이면 요청은 Pending(비종단). payload 는 실제 배치 자식 모양 -- orchestrator 는
    run_as_root 를 싣지 않는다(identity.privilege_policy: 실려 있으면 변조)."""
    rid = repos.requests.create(operation="scan", requester_id="ops", actor="batch-orchestrator",
                                resource_key=f"k-{uuid.uuid4().hex[:8]}",
                                payload=dict(SCAN_ITEM), priority="mid", batch_id=bid,
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
    # 무장 콜백은 라우트의 arm 과 같은 반환 모양(동의 지문 | None, 스토리지 경로 바인딩) -- 바인딩이 있어야 회차가 돈다.
    return repos.batch_schedules.create(batch_id=bid, kind="daily", spec={"hour": 2, "minute": 0}, enabled=enabled,
                                        next_run_at="2030-01-01T00:00:00Z" if enabled else None, actor="ops",
                                        arm=lambda b, i: (None, storage_binding_digest(repos, i)))


def _prune(repos, cfg, keep=None):
    s = cfg if keep is None else dataclasses.replace(cfg, batch_schedule_keep_rounds=keep)
    return BatchRoundPruner(repos, settings=s).run_once()


def _alive(repos, bid):
    return {r["request_id"] for r in repos.db.query("SELECT request_id FROM requests WHERE batch_id = :b",
                                                    {"b": bid})}


def _audits(db, mc, op):
    return db.query("SELECT target_key, actor, before_state FROM audit_log WHERE mutation_class = :m AND operation = :o "
                    "ORDER BY id", {"m": mc, "o": op})


def _events(db, event_type):
    return db.query("SELECT severity, message, payload FROM events WHERE event_type = :t", {"t": event_type})


def _rounds(db, bid):
    return db.query("SELECT round_seq, started_at, floor_order, via, actor, requeued FROM batch_rounds "
                    "WHERE batch_id = :b ORDER BY round_seq", {"b": bid})


# --- 회차 경계(batch_rounds -- _rescan_locked 한 곳) --------------------------------------------------------------------

def test_rescan_records_one_round_boundary_with_via_actor_floor_and_requeued(repos):
    bid = _batch(repos, n=2)
    # 자식이 없을 때 -- floor NULL(그보다 오래된 자식이 없다)
    assert repos.batches.rescan_if_terminal(bid, actor="alice")["requeued"] == 2
    r = _rounds(repos.db, bid)
    assert [(x["round_seq"], x["floor_order"], x["via"], x["actor"], x["requeued"]) for x in r] == \
        [(1, None, "rescan", "alice", 2)]
    # 자식이 생긴 뒤의 회차 -- floor = 그 순간 이 배치 자식의 MAX(commit_order)
    kids = _kids(repos, bid, 3)
    top = max(repos.requests.get(k)["commit_order"] for k in kids)
    repos.db.execute("UPDATE batches SET status = 'Completed' WHERE batch_id = :b", {"b": bid})
    repos.db.execute("UPDATE batch_items SET status = 'Succeeded' WHERE batch_id = :b", {"b": bid})
    other = _batch(repos)                                      # 다른 배치의 자식은 경계에 섞이지 않는다
    _kids(repos, other, 2)
    assert repos.batches.rescan_if_terminal(bid, actor="bob") is not None
    r = _rounds(repos.db, bid)
    assert [(x["round_seq"], x["floor_order"], x["actor"]) for x in r] == [(1, None, "alice"), (2, top, "bob")]


def test_losing_rescan_leaves_no_round_and_bad_via_is_refused(repos):
    bid = _batch(repos)
    repos.db.execute("UPDATE batches SET status = 'Running' WHERE batch_id = :b", {"b": bid})
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


def test_history_cap_is_at_least_twice_the_largest_keep():
    # 경계 계산은 최신 keep 행만 있으면 되고, 설정 상한은 100 -- 보존 상한이 그보다 작으면 큰 K 에서 경계를 계산하지 못한다.
    from dms.repositories.batches import BATCH_ROUND_HISTORY_CAP
    assert BATCH_ROUND_HISTORY_CAP >= 2 * 100
    assert bs_mod._keep_or_none(100) == 100 and bs_mod._keep_or_none(101) is None


def test_fire_records_the_round_as_schedule(repos):
    bid = _batch(repos)
    _kids(repos, bid, 2)
    sid = _schedule(repos, bid)
    repos.db.execute("UPDATE batch_schedules SET next_run_at = :t WHERE schedule_id = :s", {"t": SLOT, "s": sid})
    assert BatchScheduler(repos, settings=_S, now=NOW).run_once() == [(sid, "fired")]
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


# --- 경계 계산 · 보존 수 ------------------------------------------------------------------------------------------------

def test_default_keeps_exactly_ten_rounds_and_the_eleventh_prunes_the_first(repos, cfg):
    bid = _batch(repos)
    first = _kids(repos, bid, 2)                       # 첫 실행(기록 이전 회차)
    kept = []
    for _ in range(K - 1):
        kept += _round(repos, bid)                     # 기록 K-1 = K회차
    _schedule(repos, bid)
    assert _prune(repos, cfg)["deleted"] == 0
    assert _alive(repos, bid) == set(first + kept)
    kept += _round(repos, bid)                         # 기록 K = K+1회차
    assert _prune(repos, cfg)["deleted"] == 2
    assert _alive(repos, bid) == set(kept) and len(kept) == 2 * K


def test_pre_recording_rows_are_one_round_pruned_only_when_k_rounds_are_recorded(repos, cfg):
    bid = _batch(repos)
    old = _kids(repos, bid, 8)                         # 배포 전 회차들 흉내(기록 없음)
    _schedule(repos, bid)
    r1 = _round(repos, bid)
    for _ in range(K - 2):
        _round(repos, bid)
    assert _prune(repos, cfg)["deleted"] == 0          # 기록 K-1 < K -- 배포 직후 대량 삭제 없음
    _round(repos, bid)                                 # 기록 K -- 옛 자식 8 한꺼번에
    assert _prune(repos, cfg)["deleted"] == 8
    assert not set(old) & _alive(repos, bid) and set(r1) <= _alive(repos, bid)
    _round(repos, bid)                                 # 기록 K+1 -- r_1 자식만
    assert _prune(repos, cfg)["deleted"] == 2
    assert not set(r1) & _alive(repos, bid)
    assert len(_alive(repos, bid)) == 2 * K


def test_rerun_children_belong_to_their_round(repos, cfg):
    bid = _batch(repos)
    _schedule(repos, bid)
    r1 = _round(repos, bid)
    r1_rerun = _kid(repos, bid)                        # r_1 안의 실패분 재실행 자식(회차를 만들지 않는다)
    r2 = _round(repos, bid)
    r2_rerun = _kid(repos, bid)
    for _ in range(K - 1):
        _round(repos, bid)                             # 기록 K+1 -- 보존 r_2..r_{K+1}
    assert _prune(repos, cfg)["deleted"] == 3
    alive = _alive(repos, bid)
    assert not (set(r1) | {r1_rerun}) & alive
    assert set(r2) | {r2_rerun} <= alive


def test_keep_setting_is_honoured(repos, cfg):
    # 설정(DMS_BATCH_SCHEDULE_KEEP_ROUNDS)을 바꾸면 그 수로 계산한다 -- 같은 배치에서 K=10 이면 0, K=3 이면 앞 회차들이 정리된다.
    bid = _batch(repos)
    first = _kids(repos, bid, 2)
    rounds = [_round(repos, bid) for _ in range(4)]    # 기록 4
    _schedule(repos, bid)
    assert _prune(repos, cfg)["deleted"] == 0
    assert _prune(repos, cfg, keep=3)["deleted"] == 4  # 보존 r_2..r_4 → 첫 실행 2 + r_1 2
    assert _alive(repos, bid) == set(rounds[1] + rounds[2] + rounds[3])
    assert not set(first) & _alive(repos, bid)


# --- 보호 ----------------------------------------------------------------------------------------------------------

def test_unfinished_and_recent_children_are_skipped_without_blocking_progress(repos, cfg):
    bid = _batch(repos)
    pending = _kid(repos, bid, with_job=False)                                 # (a) 요청 비종단
    running = _kid(repos, bid, job_state=DataJobState.RUNNING)                 # (b) 요청 종단·잡 비종단
    recent = _kid(repos, bid, age=False)                                       # (c) 조용한 창 안
    ok = _kids(repos, bid, 3)
    _schedule(repos, bid)
    for _ in range(K):
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
    for _ in range(K):
        _round(repos, bid)
    assert _prune(repos, cfg)["deleted"] == 0
    assert _prune(repos, cfg)["deleted"] == 0
    ev = _events(repos.db, "batch_round_prune_blocked")
    assert len(ev) == 1 and ev[0]["severity"] == "warning" and bid in ev[0]["message"]
    assert stuck in _alive(repos, bid)


def test_pick_scans_in_bounded_windows_and_resumes_past_a_blocked_prefix(repos, cfg, monkeypatch):
    """고르기는 창 단위로 호출당 예산(PRUNE_PICK_SCAN_BUDGET)까지만 훑고, 다 못 훑으면 커서(_SCAN_FROM)로 다음 틱이 이어서 본다
    (한 문장에 판정을 두면 PG16 이 경계 이하 전부에 판정을 돌려 청크 비용이 대기량에 비례했다 -- dms-k8s 검증 지적). 막힌 자식이
    예산보다 많이 앞에 있어도 진행이 멈추지 않고(커서를 버리면 매 틱 앞의 막힌 자식만 보다 끝난다 -- 아래 틱 2 가 깨진다), 막힘
    경고는 경계 끝까지 훑고도 고를 것이 없을 때만 낸다."""
    from dms.repositories import request_purges as rp_mod
    monkeypatch.setattr(rp_mod, "PRUNE_PICK_WINDOW", 3)
    monkeypatch.setattr(rp_mod, "PRUNE_PICK_SCAN_BUDGET", 6)
    bid = _batch(repos)
    blocked = [_kid(repos, bid, with_job=False) for _ in range(10)]     # 앞자리 10개 -- 요청 비종단(지울 수 없음)
    ok = _kids(repos, bid, 5)
    _schedule(repos, bid)
    for _ in range(K):
        _round(repos, bid)                              # 기록 K -- 기록 이전 15개가 경계 이하
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
    _schedule(repos, bid)
    for _ in range(K):
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


def _real_round(repos, bid):
    """실제 모양의 회차(리뷰 2026-11-17): 전체 재실행 → Queued 항목마다 자식 1개를 만들어 항목이 가리키게(Materialized) → 자식 종단 →
    항목 Succeeded. 위 _round 는 항목이 자식을 가리키지 않는 단순화라, 「항목이 지금 가리키는 자식」이 회차 경계와 함께 맞물리는
    실제 흐름을 따로 본다. {seq: 자식} 를 돌려준다."""
    assert repos.batches.rescan_if_terminal(bid, actor="ops") is not None
    return _materialize_queued(repos, bid)


def _materialize_queued(repos, bid):
    made = {}
    for it in repos.batches.list_items(bid):
        if it["status"] != "Queued":
            continue
        rid = _kid(repos, bid)
        assert repos.batches.set_item_materialized(bid, it["seq"], rid)
        assert repos.batches.finish_item_if_materialized(bid, it["seq"], "Succeeded")
        made[it["seq"]] = rid
    repos.db.execute("UPDATE batches SET status = 'Completed' WHERE batch_id = :b", {"b": bid})
    return made


def test_realistic_rounds_keep_current_round_reruns_added_items_and_item_references(repos, cfg):
    """실제 흐름: 항목이 최신 회차 자식을 가리키고, 지금 회차 안의 실패분 재실행(rerun-failed)·항목 추가는 회차를 만들지 않고 그 회차에
    붙는다. 정리는 보존 회차 밖(가장 오래된 회차)만 지우고 -- 지금 회차의 재실행으로 밀려난 옛 자식(항목이 더는 가리키지 않음)도,
    추가 항목의 자식도, 항목이 가리키는 자식도 남긴다. 그 뒤 회차들이 지나 그 회차가 보존 밖이 되면 그때 함께 정리된다."""
    bid = _batch(repos, n=2)
    _schedule(repos, bid)
    rounds = [_real_round(repos, bid) for _ in range(K)]          # 기록 K -- 첫 회차 경계는 자식 없음(NULL) → 아직 정리 없음
    assert _prune(repos, cfg)["deleted"] == 0
    current = _real_round(repos, bid)                               # 기록 K+1 -- r_1 이 보존 밖
    # 지금 회차 안: 항목 1 실패 → 실패분 재실행(항목은 새 자식을 가리키고 옛 자식은 버려진다) + 항목 추가
    repos.db.execute("UPDATE batch_items SET status = 'Failed' WHERE batch_id = :b AND seq = 1", {"b": bid})
    assert repos.batches.reset_failed_items(bid) == 1
    repos.db.execute("UPDATE batches SET status = 'Running' WHERE batch_id = :b", {"b": bid})
    assert repos.batches.add_item(bid, {**SCAN_ITEM, "target": "added"}) == 2
    extra = _materialize_queued(repos, bid)                         # seq 1 의 재실행 자식 + seq 2(추가 항목)의 자식
    assert set(extra) == {1, 2}
    res = _prune(repos, cfg)
    assert res["deleted"] == 2                                      # r_1 의 자식 둘만
    alive = _alive(repos, bid)
    assert not set(rounds[0].values()) & alive
    kept = {r for rnd in rounds[1:] for r in rnd.values()} | set(current.values()) | set(extra.values())
    assert alive == kept
    pointed = {it["request_id"] for it in repos.batches.list_items(bid)}
    assert pointed == {current[0], extra[1], extra[2]} and pointed <= alive
    assert current[1] in alive and current[1] not in pointed        # 지금 회차에서 밀려난 자식 -- 회차 안이라 남는다
    for _ in range(K - 1):
        _real_round(repos, bid)                                     # 지금 회차가 가장 오래된 보존 회차가 될 때까지 -- 아직 남는다
    _prune(repos, cfg)
    assert {current[1], extra[1], extra[2]} <= _alive(repos, bid)
    _real_round(repos, bid)                                         # 이제 보존 밖 -- 재실행·추가 항목 자식까지 그 회차째 정리
    _prune(repos, cfg)
    assert not ({*current.values(), *extra.values()} & _alive(repos, bid))
    assert {it["request_id"] for it in repos.batches.list_items(bid)} <= _alive(repos, bid)
    assert len(_alive(repos, bid)) == 3 * K                          # 항목 3 × 보존 K회차


# --- 지금 회차·닻(commit_order 재사용 방지) ------------------------------------------------------------------------------

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
    for _ in range(K):
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
    보존 회차 자식처럼 보이지 않게 된다."""
    real = bs_mod._cut_from

    def no_anchor(row):
        out = real(row)
        return None if out is None else {**out, "upto": out["cut"]}
    monkeypatch.setattr(bs_mod, "_cut_from", no_anchor)
    bid = _batch(repos)
    order, floor = _reuse_scenario(repos, cfg, bid)
    assert order <= floor


# --- 청크·대기 상한·공평 순환 -------------------------------------------------------------------------------------------

def _old_batch(repos, n_old, *, kept_kids=1):
    bid = _batch(repos)
    old = _kids(repos, bid, n_old)
    _schedule(repos, bid)
    for _ in range(K):
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
    repos.db.execute("UPDATE request_purges SET requested_by = 'alice'")
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


# --- 대상 범위 · 유지보수 --------------------------------------------------------------------------------------------------

def test_only_batches_with_a_schedule_row_are_pruned(repos, cfg):
    plain = _batch(repos)
    _kids(repos, plain, 3)
    for _ in range(K + 2):
        _round(repos, plain)
    assert _prune(repos, cfg)["deleted"] == 0           # 스케줄 없음(사람 :rescan 만 하는 배치는 정리하지 않는다)
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
    for keep in (0, 101, True, "10"):
        assert _prune(repos, cfg, keep=keep) == {"disabled": True}
    assert set(old) <= _alive(repos, bid)
    # 저장소 쪽 거울도 아무것도 계산하지 않는다(fail-closed)
    assert repos.batch_schedules.prune_targets(keep=0) == [] and repos.batch_schedules.prune_targets(keep=True) == []


# --- 고아 없음 · 감사 --------------------------------------------------------------------------------------------------

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
    rows = {r["request_id"]: r for r in db.query("SELECT request_id, jobs, requested_by, artifact_base "
                                                 "FROM request_purges")}
    assert set(rows) == set(old)
    for rid, row in rows.items():
        assert row["requested_by"] == SCHEDULER_ACTOR and row["artifact_base"] == BASE
        assert [j["job_id"] for j in json.loads(row["jobs"])] == [jobs[rid]]
        assert repos.request_purges.finish(rid, [jobs[rid]])
    assert db.query_one("SELECT COUNT(*) AS n FROM request_purges")["n"] == 0


def test_unknown_artifact_base_is_stored_as_null(repos, cfg):
    # 작업 삭제 라우트와 같은 모양: base 를 모르면 None(정리 루프가 파일 단계를 보류한다) -- 빈 문자열을 넘기지 않는다.
    bid, old = _old_batch(repos, 1)
    assert _prune(repos, dataclasses.replace(cfg, artifact_base_uri=""))["deleted"] == 1
    assert repos.db.query_one("SELECT artifact_base FROM request_purges")["artifact_base"] is None


def test_one_summary_audit_row_per_chunk_and_no_per_request_rows(repos, cfg):
    bid, old = _old_batch(repos, 3)
    sid = repos.batch_schedules.get_by_batch(bid)["schedule_id"]
    top = max(repos.requests.get(r)["commit_order"] for r in old)
    # 자식 payload 에 orchestrator 가 얼리는 값(_materialize 의 owner_username, sync 옵션의 chown) 흉내: 하나는 둘 다, 하나는 chown
    # 만, 하나는 둘 다 없음(비특권 scan 배치의 정상 모양 -- 키 부재)
    owned, chowned, bare = sorted(old)
    for rid, extra in ((owned, {"owner_username": "alice", "options": {"chown": "1000:1000", "delete": False}}),
                       (chowned, {"options": {"chown": "0:0"}})):
        repos.db.execute("UPDATE requests SET payload = :p WHERE request_id = :r",
                         {"p": json.dumps({**SCAN_ITEM, **extra}), "r": rid})
    _prune(repos, cfg)
    rows = _audits(repos.db, "batch", "prune_rounds")
    assert len(rows) == 1 and rows[0]["target_key"] == bid and rows[0]["actor"] == SCHEDULER_ACTOR
    a = json.loads(rows[0]["before_state"])
    assert a["deleted_count"] == 3 and a["job_count"] == 3 and a["states"] == {"Succeeded": 3}
    assert a["schedule_id"] == sid and a["keep_rounds"] == K and a["rounds_recorded"] == K and a["cut_round_seq"] == 1
    floor1 = repos.db.query_one("SELECT floor_order FROM batch_rounds WHERE batch_id = :b AND round_seq = 1",
                                {"b": bid})["floor_order"]
    assert a["upto_order"] == floor1 == top
    assert {c["request_id"] for c in a["children"]} == set(old)
    assert all(len(c["jobs"]) == 1 and c["jobs"][0]["tool"] == "dscan" for c in a["children"])
    # 실행 근거 칸은 run_as_root 가 아니다(배치 자식 payload 엔 그 키가 없다 -- dms-k8s d29 실증에서 늘 null 이었다)
    assert all("run_as_root" not in c for c in a["children"])
    by = {c["request_id"]: c for c in a["children"]}
    assert by[owned]["owner_username"] == "alice" and by[owned]["chown"] == "1000:1000"
    assert by[chowned]["owner_username"] is None and by[chowned]["chown"] == "0:0"
    assert by[bare]["owner_username"] is None and by[bare]["chown"] is None
    assert a["truncated"] is False
    assert _audits(repos.db, "request", "delete") == []


def test_prune_summary_clips_and_drops_tampered_owner_fields():
    """변조된 payload(거대한 owner_username·chown, dict 아닌 options, 깨진 JSON)가 정리 감사를 거대하게 만들거나 500 으로 끊지
    않는다."""
    reqs = [{"request_id": "a" * 32, "state": "Succeeded", "created_at": "t",
             "payload": json.dumps({"owner_username": "u" * 5000, "options": {"chown": "c" * 5000}})},
            {"request_id": "b" * 32, "state": "Succeeded", "created_at": "t",
             "payload": json.dumps({"owner_username": 1, "options": "0:0"})},
            {"request_id": "c" * 32, "state": "Succeeded", "created_at": "t",
             "payload": json.dumps({"options": {"chown": 7}})},
            {"request_id": "d" * 32, "state": "Succeeded", "created_at": "t", "payload": "{broken"}]
    kids = {c["request_id"]: c for c in RequestPurgesRepository._prune_summary(reqs, {}, [])["children"]}
    assert kids["a" * 32]["owner_username"] == "u" * 64 and kids["a" * 32]["chown"] == "c" * 64
    assert kids["b" * 32]["owner_username"] is None and kids["b" * 32]["chown"] is None
    assert kids["c" * 32]["chown"] is None
    assert kids["d" * 32]["owner_username"] is None and kids["d" * 32]["chown"] is None


def test_prune_audit_folds_into_32k():
    # 첫 단계(잡을 뺀다)면 들어가는 경우 -- 자식 기록의 owner_username·chown 은 남는다
    kids = [{"request_id": f"{i:032x}", "state": "Succeeded", "created_at": "2026-11-16T12:34:53Z",
             "owner_username": "alice", "chown": "0:0",
             "jobs": [{"job_id": f"{i:032x}", "state": "Succeeded", "tool": "dscan" * 100}]} for i in range(100)]
    summary = {"batch_id": "b" * 32, "deleted_count": 100, "children": kids, "truncated": False}
    text = RequestPurgesRepository._prune_bounded(summary)
    out = json.loads(text)
    assert len(text.encode()) <= SNAPSHOT_MAX_BYTES and out["truncated"] is True
    assert out["children"] == [{k: v for k, v in c.items() if k != "jobs"} for c in kids]
    assert out["children"][0]["owner_username"] == "alice" and out["children"][0]["chown"] == "0:0"
    # 둘째 단계(id 목록)까지 가는 경우
    kids = [{**c, "created_at": "x" * 900} for c in kids]
    summary = {**summary, "children": kids}
    text = RequestPurgesRepository._prune_bounded(summary)
    out = json.loads(text)
    assert len(text.encode()) <= SNAPSHOT_MAX_BYTES and out["truncated"] is True
    assert out["children"] == [k["request_id"] for k in kids]       # 둘째 단계: id 목록
    huge = {**summary, "schedule_id": "s" * 40000}
    text = RequestPurgesRepository._prune_bounded(huge)
    assert len(text.encode()) <= SNAPSHOT_MAX_BYTES and json.loads(text)["truncated"] is True


# --- 스케줄 칸 · 경합 · 격리 ----------------------------------------------------------------------------------------------

def test_schedule_counters_accumulate_without_touching_updated_at_and_fire_still_wins(repos, cfg):
    bid, old = _old_batch(repos, 2)
    s0 = repos.batch_schedules.get_by_batch(bid)
    assert s0["pruned_at"] is None and s0["pruned_total"] is None
    _prune(repos, cfg)
    s1 = repos.batch_schedules.get_by_batch(bid)
    assert s1["pruned_total"] == 2 and s1["pruned_at"] is not None
    assert s1["updated_at"] == s0["updated_at"] and s1["next_run_at"] == s0["next_run_at"]
    # 정리 직후 회차 -- 스케줄 CAS(updated_at·next_run_at)가 이긴다
    repos.db.execute("UPDATE batch_schedules SET next_run_at = :t WHERE batch_id = :b", {"t": SLOT, "b": bid})
    repos.db.execute("UPDATE batches SET status = 'Completed' WHERE batch_id = :b", {"b": bid})
    assert BatchScheduler(repos, settings=_S, now=NOW).run_once()[0][1] == "fired"
    # 스케줄 회차도 회차다(기록 K+1) -- r_1 의 자식 1 이 더 정리되고 누적된다
    _prune(repos, cfg)
    assert repos.batch_schedules.get_by_batch(bid)["pruned_total"] == 3


def test_schedule_deleted_between_targets_and_chunk_is_schedule_gone(repos, cfg):
    bid, old = _old_batch(repos, 2)
    t = repos.batch_schedules.prune_targets(keep=K)[0]
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
    t = repos.batch_schedules.prune_targets(keep=K)[0]
    newer = _round(repos, bid)                          # cut 을 읽은 뒤 새 회차 -- 옛 cut 은 그대로 유효(덜 지울 뿐)
    res = repos.request_purges.prune_round_children(bid, upto_order=t["upto"], limit=CHUNK, actor=SCHEDULER_ACTOR,
                                                    artifact_base=BASE, quiet_seconds=60)
    assert sorted(res["deleted"]) == sorted(old)
    assert set(newer) <= _alive(repos, bid)


def test_cas_loss_rolls_back_and_keeps_the_cursor(repos, cfg, monkeypatch):
    # 마지막 CAS DELETE 가 지면(잠금 없는 sqlite 경로·잠금 누락 이중 가드) 전부 롤백 -- 감사·카운터도 없고 커서는 그대로다.
    bid, old = _old_batch(repos, 2)
    t = repos.batch_schedules.prune_targets(keep=K)[0]
    purges = repos.request_purges
    real = purges._purge_locked

    def losing(reqs, jobs_by_rid, **kw):
        repos.db.execute("UPDATE requests SET state = 'Pending' WHERE request_id = :r", {"r": reqs[0]["request_id"]})
        return real(reqs, jobs_by_rid, **kw)
    monkeypatch.setattr(purges, "_purge_locked", losing)
    res = purges.prune_round_children(bid, upto_order=t["upto"], limit=CHUNK, actor=SCHEDULER_ACTOR,
                                      artifact_base=BASE, quiet_seconds=60, scan_after=None)
    assert res["lost"] is True and res["deleted"] == [] and res["scan_next"] is None
    assert set(old) <= _alive(repos, bid) and _audits(repos.db, "batch", "prune_rounds") == []
    assert repos.batch_schedules.get_by_batch(bid)["pruned_total"] is None
    assert repos.db.query_one("SELECT COUNT(*) AS n FROM request_purges")["n"] == 0


def test_preview_and_targets_use_the_same_cut(repos, cfg):
    bid, old = _old_batch(repos, 4)
    t = repos.batch_schedules.prune_targets(keep=K)[0]
    cut = repos.batch_schedules._round_cut(bid, keep=K)
    assert {k: t[k] for k in cut} == cut
    assert repos.batch_schedules.prune_preview(bid, keep=K) == {"rounds_recorded": K, "would_prune": 4,
                                                                "would_prune_capped": False}


def test_loop_is_wired_only_with_the_purge_runner(db, settings):
    from dms.controller import build_loops
    from dms.purge_runner import StubPurgeRunner
    with_runner = build_loops(settings, Repositories(db), purge_runner=StubPurgeRunner())
    assert ("batch-round-prune", 30) in [(lp.name, lp.interval_seconds) for lp in with_runner]
    assert "batch-round-prune" not in [lp.name for lp in build_loops(settings, Repositories(db))]


# --- 잠금 순서(PG 흉내 -- 문장 순서만 본다; 이 라인엔 PG 동시성 하네스가 없다) -----------------------------------------------

class _PgSpy:
    """dialect 만 postgresql 로 보이는 sqlite 프록시 -- 모든 문장을 기록하고 FOR UPDATE·FOR SHARE 를 떼어 실행한다."""
    dialect = "postgresql"

    def __init__(self, db):
        self._db = db
        self.sql = []

    def __getattr__(self, name):
        return getattr(self._db, name)

    def _run(self, fn, sql, params):
        self.sql.append(" ".join(sql.split()))
        return fn(sql.replace(" FOR UPDATE", "").replace(" FOR SHARE", ""), params)

    def query(self, sql, params=None):
        return self._run(self._db.query, sql, params)

    def query_one(self, sql, params=None):
        return self._run(self._db.query_one, sql, params)

    def execute(self, sql, params=None):
        return self._run(self._db.execute, sql, params)

    def execute_count(self, sql, params=None):
        return self._run(self._db.execute_count, sql, params)


def _first(sql_list, pred):
    return next(i for i, q in enumerate(sql_list) if pred(q))


def test_rescan_and_fire_take_round_boundaries_between_batch_and_schedule(repos):
    """항목 → 배치 → 회차 경계 → 스케줄 → 회차 이력(batches.BATCH_DELETE_TABLES 순서) -- 사람 :rescan 과 스케줄 발사가 같은
    방향이라 배치 삭제 두 경로(같은 순서로 지운다)와 PG 에서 서로 상대 행을 기다리지 않는다."""
    from dms.repositories.batch_schedules import BatchSchedulesRepository
    from dms.repositories.batches import BatchesRepository
    bid = _batch(repos)
    spy = _PgSpy(repos.db)
    assert BatchesRepository(spy).rescan_if_terminal(bid, actor="ops") is not None
    items = _first(spy.sql, lambda q: q.startswith("SELECT seq FROM batch_items") and q.endswith("FOR UPDATE"))
    batch = _first(spy.sql, lambda q: q.startswith("SELECT * FROM batches") and q.endswith("FOR UPDATE"))
    rounds = _first(spy.sql, lambda q: q.startswith("INSERT INTO batch_rounds"))
    assert items < batch < rounds
    fired = _batch(repos)
    sid = _schedule(repos, fired)
    spy = _PgSpy(repos.db)
    row = BatchSchedulesRepository(spy).get(sid)
    assert BatchSchedulesRepository(spy).fire(row, next_run_at=NEXT, now=NOW, verify=lambda b, i: "fp",
                                              armed_env_listed=True, requester_env_listed=True) is not None
    batch = _first(spy.sql, lambda q: q.startswith("SELECT * FROM batches") and q.endswith("FOR UPDATE"))
    rounds = _first(spy.sql, lambda q: q.startswith("INSERT INTO batch_rounds"))
    sched = _first(spy.sql, lambda q: q.startswith("UPDATE batch_schedules"))
    runs = _first(spy.sql, lambda q: q.startswith("INSERT INTO batch_schedule_runs"))
    assert batch < rounds < sched < runs


def test_prune_chunk_locks_requests_then_jobs_then_schedule_last(repos):
    """회차 보존 정리 한 청크(prune_round_children)의 잠금 순서: requests(request_id 순) → data_jobs → batch_schedules(맨 끝 --
    배치 단위 삭제 꼬리와 같은 방향). batch_items·batches·batch_rounds 는 잠그지도 쓰지도 않는다."""
    import re
    bid = _batch(repos)
    kids = _kids(repos, bid, 3)
    _schedule(repos, bid)
    upto = max(repos.requests.get(k)["commit_order"] for k in kids)
    spy = _PgSpy(repos.db)
    res = RequestPurgesRepository(spy).prune_round_children(bid, upto_order=upto, limit=10, actor=SCHEDULER_ACTOR,
                                                            artifact_base=None, quiet_seconds=60)
    assert sorted(res["deleted"]) == sorted(kids)
    locks = [re.search(r"FROM (\w+)", q).group(1) for q in spy.sql if q.endswith("FOR UPDATE")]
    assert locks[:3] == ["requests", "data_jobs", "batch_schedules"], locks
    touched = {re.match(r"(?:UPDATE|DELETE FROM|INSERT INTO) (\w+)", q).group(1)
               for q in spy.sql if re.match(r"(?:UPDATE|DELETE FROM|INSERT INTO) ", q)}
    assert not touched & {"batch_items", "batches", "batch_rounds"}
    assert "batch_schedules" in touched


# --- PG 동시성(실제 행 잠금 -- PostgreSQL 일 때만) ----------------------------------------------------------------------------
# 이 라인의 conftest 에는 PG 하네스가 없어 sqlite 갈래에선 건너뛴다(sqlite 는 커넥션 하나·RLock 이 직렬화해 잠금 경합 자체가 없다).
# 리뷰(2026-11-17)는 sqlite 접속을 같은 URL → 같은 PG 스키마로 돌리는 pytest 플러그인으로 PG16 에서 돌렸다 -- 두 번째 커넥션은 같은
# sqlite URL 로 연다(_second). dms-k8s 라인의 같은 넷(test_pg_*)을 옮기고, 스케줄 삭제·배치 기록 삭제 경합을 더했다.

def _pg_or_skip(repos):
    if repos.db.dialect != "postgresql":
        pytest.skip("PG 동시성 -- 운영 방언에서만(sqlite 는 한 커넥션 RLock 이 직렬화한다)")
    repos.db.execute("SET lock_timeout = '2s'")      # 교착·무한 대기를 실패로 드러낸다


def _second(tmp_path):
    from dms.db import Database
    other = Database.connect(f"sqlite:///{tmp_path}/test.db")     # conftest db 와 같은 URL(PG 플러그인 = 같은 스키마)
    assert other.dialect == "postgresql"
    other.execute("SET lock_timeout = '2s'")
    return other


def _in_thread(fn):
    import threading
    box = {}

    def body():
        try:
            box["result"] = fn()
        except BaseException as exc:                  # noqa: BLE001 -- 스레드 예외를 본 스레드로 옮긴다
            box["error"] = exc
    t = threading.Thread(target=body)
    t.start()
    return t, box


def _slow_after(real, event, seconds=0.6):
    """real 을 부른 **뒤** 신호를 켜고 잠금을 쥔 채 잠깐 멈춘다(그 사이 다른 커넥션이 같은 행을 기다리게)."""
    import time

    def inner(*a, **kw):
        out = real(*a, **kw)
        event.set()
        time.sleep(seconds)
        return out
    return inner


def test_pg_prune_holding_requests_then_batch_delete_waits_without_deadlock(repos, cfg, tmp_path):
    import threading
    _pg_or_skip(repos)
    bid, old = _old_batch(repos, 3)
    total = len(_alive(repos, bid))
    other = _second(tmp_path)
    purges = RequestPurgesRepository(repos.db)
    started = threading.Event()
    purges._prune_lock = _slow_after(purges._prune_lock, started)   # requests·data_jobs 를 쥔 채 -- 배치 단위 삭제가 기다린다
    t = repos.batch_schedules.prune_targets(keep=K)[0]
    th, box = _in_thread(lambda: purges.prune_round_children(
        bid, upto_order=t["upto"], limit=CHUNK, actor=SCHEDULER_ACTOR, artifact_base=BASE, quiet_seconds=60))
    assert started.wait(5)
    r = RequestPurgesRepository(other).delete_batch(bid, expected_request_count=total, actor="ops",
                                                    artifact_base=BASE, quiet_seconds=60, max_children=1000)
    th.join(10)
    assert "error" not in box, box.get("error")
    assert sorted(box["result"]["deleted"]) == sorted(old)
    assert r["deleted"] is False and r["reason"] == "batch_changed"     # 확인 창의 수가 줄었다 -- 아무것도 지우지 않았다
    assert repos.db.query_one("SELECT COUNT(*) AS n FROM batch_rounds WHERE batch_id = :b", {"b": bid})["n"] == K


def test_pg_batch_delete_first_then_prune_finds_nothing(repos, cfg, tmp_path):
    import threading
    import time
    _pg_or_skip(repos)
    bid, old = _old_batch(repos, 3)
    total = len(_alive(repos, bid))
    t = repos.batch_schedules.prune_targets(keep=K)[0]
    other = _second(tmp_path)
    deleter = RequestPurgesRepository(other)
    started = threading.Event()
    real = deleter._purge_locked

    def slow_purge(*a, **kw):
        started.set()
        time.sleep(0.6)                                # 항목·배치·요청·잡을 잠근 채
        return real(*a, **kw)
    deleter._purge_locked = slow_purge
    th, box = _in_thread(lambda: deleter.delete_batch(
        bid, expected_request_count=total, actor="ops", artifact_base=BASE, quiet_seconds=60, max_children=1000))
    assert started.wait(5)
    res = RequestPurgesRepository(repos.db).prune_round_children(
        bid, upto_order=t["upto"], limit=CHUNK, actor=SCHEDULER_ACTOR, artifact_base=BASE, quiet_seconds=60)
    th.join(10)
    assert "error" not in box, box.get("error")
    assert box["result"]["deleted"] is True
    assert res["deleted"] == [] and res["lost"] is False
    assert _audits(repos.db, "batch", "prune_rounds") == []
    for table in ("batch_rounds", "batch_schedules", "batch_schedule_runs"):
        assert repos.db.query_one(f"SELECT COUNT(*) AS n FROM {table} WHERE batch_id = :b", {"b": bid})["n"] == 0


def test_pg_fire_waits_for_a_prune_holding_the_schedule_then_wins(repos, cfg, tmp_path):
    import threading
    from dms.repositories.batch_schedules import BatchSchedulesRepository
    _pg_or_skip(repos)
    bid, old = _old_batch(repos, 2)
    repos.db.execute("UPDATE batch_schedules SET next_run_at = :t WHERE batch_id = :b", {"t": SLOT, "b": bid})
    t = repos.batch_schedules.prune_targets(keep=K)[0]
    other = _second(tmp_path)
    purges = RequestPurgesRepository(repos.db)
    started = threading.Event()
    # 감사 요약을 만들 때 = 스케줄 행 FOR UPDATE 뒤(정리 청크의 맨 끝 잠금) -- 회차 발사가 그 행을 기다린다
    purges._prune_bounded = _slow_after(RequestPurgesRepository._prune_bounded, started)
    th, box = _in_thread(lambda: purges.prune_round_children(
        bid, upto_order=t["upto"], limit=CHUNK, actor=SCHEDULER_ACTOR, artifact_base=BASE, quiet_seconds=60))
    assert started.wait(5)
    sched = BatchSchedulesRepository(other)
    row = sched.get_by_batch(bid)
    fired = sched.fire(row, next_run_at=NEXT, now=NOW, verify=lambda b, i: "fp",
                       armed_env_listed=True, requester_env_listed=True)
    th.join(10)
    assert "error" not in box, box.get("error")
    assert sorted(box["result"]["deleted"]) == sorted(old)
    assert fired is not None                           # 정리는 updated_at 을 밀지 않는다 -- 발사 CAS 가 이긴다
    s = repos.batch_schedules.get_by_batch(bid)
    assert s["pruned_total"] == 2 and s["last_outcome"] == "fired"
    assert [x["via"] for x in _rounds(repos.db, bid)][-1] == "schedule"


def test_pg_two_prunes_delete_each_child_once(repos, cfg, tmp_path):
    import threading
    _pg_or_skip(repos)
    bid, old = _old_batch(repos, 6)
    t = repos.batch_schedules.prune_targets(keep=K)[0]
    other = _second(tmp_path)
    a, b = RequestPurgesRepository(repos.db), RequestPurgesRepository(other)
    go = threading.Barrier(2)

    def run(repo):
        go.wait(5)
        return repo.prune_round_children(bid, upto_order=t["upto"], limit=CHUNK, actor=SCHEDULER_ACTOR,
                                         artifact_base=BASE, quiet_seconds=60)
    t1, box1 = _in_thread(lambda: run(a))
    t2, box2 = _in_thread(lambda: run(b))
    t1.join(10)
    t2.join(10)
    assert "error" not in box1 and "error" not in box2, (box1.get("error"), box2.get("error"))
    deleted = box1["result"]["deleted"] + box2["result"]["deleted"]
    assert sorted(deleted) == sorted(old)
    assert repos.db.query_one("SELECT COUNT(*) AS n FROM request_purges")["n"] == 6
    assert repos.batch_schedules.get_by_batch(bid)["pruned_total"] == 6
    assert sum(json.loads(r["before_state"])["deleted_count"]
               for r in _audits(repos.db, "batch", "prune_rounds")) == 6


@pytest.mark.parametrize("how", ["schedule_delete", "batch_record_delete"])
def test_pg_schedule_removed_while_prune_holds_requests_deletes_nothing(repos, cfg, tmp_path, how):
    """정리가 요청·잡을 쥔 사이 스케줄이 사라지면(스케줄 DELETE · 배치 기록 삭제 -- 둘 다 스케줄 행을 잠그고 지운다) 정리는 맨 끝의
    스케줄 행 FOR UPDATE 에서 기다렸다가 행이 없음을 보고 아무것도 지우지 않는다(schedule_gone -- 스케줄을 지우면 정리도 멈춘다).
    교착 없음: 두 삭제 경로는 요청을 잠그지 않는다(배치 기록 삭제는 자식을 남긴다)."""
    import threading
    from dms.repositories.batch_schedules import BatchSchedulesRepository
    from dms.repositories.batches import BatchesRepository
    _pg_or_skip(repos)
    bid, old = _old_batch(repos, 3)
    t = repos.batch_schedules.prune_targets(keep=K)[0]
    other = _second(tmp_path)
    purges = RequestPurgesRepository(repos.db)
    started = threading.Event()
    purges._prune_lock = _slow_after(purges._prune_lock, started, 1.0)
    th, box = _in_thread(lambda: purges.prune_round_children(
        bid, upto_order=t["upto"], limit=CHUNK, actor=SCHEDULER_ACTOR, artifact_base=BASE, quiet_seconds=60))
    assert started.wait(5)
    if how == "schedule_delete":
        BatchSchedulesRepository(other).delete(t["schedule_id"], actor="ops")
    else:
        BatchesRepository(other).delete(bid, actor="ops")
    th.join(10)
    assert "error" not in box, box.get("error")
    res = box["result"]
    assert res["schedule_gone"] is True and res["deleted"] == []
    assert set(old) <= _alive(repos, bid)
    assert _audits(repos.db, "batch", "prune_rounds") == []
    assert repos.db.query_one("SELECT COUNT(*) AS n FROM request_purges")["n"] == 0
