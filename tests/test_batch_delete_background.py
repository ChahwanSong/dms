"""배치 단위 삭제 상한 50000 + 동기/백그라운드(청크) 삭제(2026-11-19 사용자 결정 「배치 단위 삭제 상한 기본 50000개로」 --
repositories/request_purges.py 모듈 docstring 「동기 경로 / 백그라운드 경로」, batch_deleter.py). dms-k8s 14ee820c·cc52a7e3 의
tests/test_batch_delete_background.py 이식 -- 이 라인 차이: 스케줄 무장 콜백이 (지문, 스토리지 바인딩) 짝을 돌려준다, 기록 삭제의
종단 CAS 는 require_terminal=True, 정리 아웃박스에 kind 열이 없다(결과 파일 자동 만료 없음), 스케줄 판정이 스토리지 바인딩을 본다.

여기서 고정하는 것: 설정 키 두 개, 경계(49999·50000·50001 · 항목 10000/10001), 동기/백그라운드 갈림과 호출 예산, 판정 패리티
(동기 delete_batch = A단계 batch_delete_check = B단계 묘비), 일찍 CAS, 묘비 효과·멱등, 청크(감사·레인·커서·재판정), 중단·재개,
마무리, 묘비 뒤 되살림 경로 409·원자 가드, due() 레인, 목록 요약·미리보기·응답 모양, 미등록 스토리지 정리, 컨트롤러 루프."""
import dataclasses
import json
import threading
import uuid

import pytest

from dms.batch_deleter import BatchDeleter
from dms.config import Settings, SettingsError
from dms.controller import build_loops
from dms.repositories import Repositories
from dms.repositories.request_purges import (DELETE_CHUNK, MAX_DELETE_CALL_CHILDREN, RequestPurgesRepository)

OLD = "2026-01-01T00:00:00Z"
NOW = "2026-06-01T00:00:00Z"
PATH = "/api/admin/requests:delete"
PREVIEW = "/api/admin/requests:delete-preview"
VALID = {"DMS_DATABASE_URL": "sqlite:///tmp/dms.db", "DMS_SHARED_TOKEN": "tok",
         "DMS_ADMIN_TOKEN": "adm", "DMS_SESSION_SECRET": "sess"}
_ORDER = [10_000_000]


@pytest.fixture
def repos(db):
    return Repositories(db)


def _batch(repos, *, n_items=1, status="Completed", name="큰 배치", operation="scan", storage="s1"):
    items = ([{"storage": storage, "target": f"t{i}"} for i in range(n_items)] if n_items else
             [{"storage": storage, "target": "t0"}])
    bid = repos.batches.create(operation=operation, requester_id="opadm", actor="opadm", max_concurrency=1, options={},
                               note=None, items=items, status=status, name=name, auth_method="session")
    if n_items == 0:
        repos.db.execute("DELETE FROM batch_items WHERE batch_id = :b", {"b": bid})
        repos.db.execute("UPDATE batches SET item_count = 0 WHERE batch_id = :b", {"b": bid})
    return bid


def _bulk(db, bid, n, *, with_job=True, state="Succeeded", job_state="Succeeded", updated=OLD, storage="s1",
          operation="scan"):
    """자식 n 개를 500행 묶음 INSERT 로(5만 자식도 수 초). commit_order 는 전역 단조 증가."""
    out = []
    for i in range(0, n, 500):
        k = min(500, n - i)
        rids = [uuid.uuid4().hex for _ in range(k)]
        orders = []
        for _ in range(k):
            _ORDER[0] += 1
            orders.append(_ORDER[0])
        params, rows = {}, []
        for j, (rid, o) in enumerate(zip(rids, orders)):
            params.update({f"r{j}": rid, f"o{j}": o})
            rows.append(f"(:r{j}, :o{j}, :op, 'opadm', 'opadm', :r{j}, :pl, :st, :at, :at, :b, 'session')")
        db.execute("INSERT INTO requests (request_id, commit_order, operation, requester_id, actor, resource_key, "
                   "payload, state, created_at, updated_at, batch_id, auth_method) VALUES " + ", ".join(rows),
                   {**params, "op": operation, "pl": json.dumps({"storage": storage, "target": "x"}), "st": state,
                    "at": updated, "b": bid})
        jids = [uuid.uuid4().hex for _ in range(k)]
        if with_job:
            jp, jrows = {}, []
            for j, (jid, rid) in enumerate(zip(jids, rids)):
                jp.update({f"j{j}": jid, f"r{j}": rid})
                jrows.append(f"(:j{j}, :r{j}, :op, '{{}}', 'mid', :js, :sn, :j{j}, :at, :at)")
            db.execute("INSERT INTO data_jobs (job_id, request_id, operation, options, priority, state, storage_name, "
                       "target, created_at, updated_at) VALUES " + ", ".join(jrows),
                       {**jp, "op": operation, "js": job_state, "sn": storage, "at": updated})
        out.extend(zip(rids, jids if with_job else [None] * k))
    return out


def _count(db, sql, **p):
    return int(db.query_one(sql, p)["n"])


def _kids(db, bid):
    return _count(db, "SELECT COUNT(*) AS n FROM requests WHERE batch_id = :b", b=bid)


def _auto(repos, bid, expected=None, **kw):
    kw.setdefault("actor", "opadm")
    kw.setdefault("artifact_base", "/base")
    kw.setdefault("quiet_seconds", 60)
    kw.setdefault("cap", 50000)
    kw.setdefault("sync_max", 500)
    kw.setdefault("allow_sync", True)
    if expected is None:
        expected = _kids(repos.db, bid)
    return repos.request_purges.delete_batch_auto(bid, expected_request_count=expected, **kw)


def _drain(repos, settings, *, ticks=200):
    """BatchDeleter 를 묘비가 다 사라질 때까지(틱 예산 무제한)."""
    for _ in range(ticks):
        if not repos.db.query("SELECT batch_id FROM batch_deletions"):
            return
        repos.db.execute("UPDATE batch_deletions SET next_attempt_at = :t", {"t": OLD})
        BatchDeleter(repos, settings=settings, budget_seconds=60).run_once()
    raise AssertionError("background delete did not finish")


# ---- 1. 설정 키 ----

def test_config_defaults_overrides_and_bounds():
    s = Settings.from_env(VALID)
    assert (s.batch_delete_max_children, s.batch_delete_sync_max_children) == (50000, 500)
    s = Settings.from_env({**VALID, "DMS_BATCH_DELETE_MAX_CHILDREN": "20000", "DMS_BATCH_DELETE_SYNC_MAX_CHILDREN": "0"})
    assert (s.batch_delete_max_children, s.batch_delete_sync_max_children) == (20000, 0)
    for key, bad in (("DMS_BATCH_DELETE_MAX_CHILDREN", "0"), ("DMS_BATCH_DELETE_MAX_CHILDREN", "-1"),
                     ("DMS_BATCH_DELETE_MAX_CHILDREN", "50001"), ("DMS_BATCH_DELETE_SYNC_MAX_CHILDREN", "-1"),
                     ("DMS_BATCH_DELETE_SYNC_MAX_CHILDREN", "1001"), ("DMS_BATCH_DELETE_SYNC_MAX_CHILDREN", "x")):
        with pytest.raises(SettingsError) as e:
            Settings.from_env({**VALID, key: bad})
        assert key in str(e.value)


def test_base_config_carries_the_code_defaults():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "deploy/k8s/20-config.yaml").read_text()
    assert 'DMS_BATCH_DELETE_MAX_CHILDREN: "50000"' in text
    assert 'DMS_BATCH_DELETE_SYNC_MAX_CHILDREN: "500"' in text


# ---- 3. 경계 ----

def test_boundary_49999_50000_50001_at_the_default_cap(repos, db):
    a = _batch(repos)
    _bulk(db, a, 49999, with_job=False)
    assert repos.request_purges.batch_delete_check(a, cap=50000, quiet_seconds=60)["reason"] is None
    _bulk(db, a, 1, with_job=False)
    r = _auto(repos, a, 50000)
    assert r == {"deleted": False, "deleting": True, "already": False, "request_count": 50000}
    d = db.query_one("SELECT * FROM batch_deletions WHERE batch_id = :b", {"b": a})
    assert (d["expected_request_count"], d["prior_status"], d["dangling"], d["item_count"]) == (50000, "Completed", 0, 1)
    assert d["max_commit_order"] == db.query_one("SELECT MAX(commit_order) AS m FROM requests WHERE batch_id = :b",
                                                 {"b": a})["m"]
    assert repos.batches.get(a)["status"] == "Deleting"
    b = _batch(repos)
    _bulk(db, b, 50001, with_job=False)
    audit_before = _count(db, "SELECT COUNT(*) AS n FROM audit_log")
    assert _auto(repos, b, 50001) == {"deleted": False, "reason": "batch_delete_too_large", "request_id": None,
                                      "limit": 50000, "count": None, "count_at_least": 50001}
    assert repos.batches.get(b)["status"] == "Completed" and _kids(db, b) == 50001
    assert _count(db, "SELECT COUNT(*) AS n FROM audit_log") == audit_before
    assert db.query_one("SELECT 1 AS x FROM batch_deletions WHERE batch_id = :b", {"b": b}) is None
    # 미리보기(같은 판정 함수)도 상한을 넘으면 다시 세지 않고 하한만 싣는다(2026-11-19 커밋 보안 리뷰 — 한 문장의 락 보유를 cap + 1 창으로 묶는다).
    chk = repos.request_purges.batch_delete_check(b, cap=50000, quiet_seconds=60)
    assert (chk["reason"], chk["count"], chk["count_at_least"], chk["limit"]) == ("batch_delete_too_large", None, 50001, 50000)


def test_lower_cap_setting_and_item_cap(repos, db):
    a = _batch(repos)
    _bulk(db, a, 21, with_job=False)
    assert _auto(repos, a, 21, cap=20)["reason"] == "batch_delete_too_large"
    assert _auto(repos, a, 21, cap=21, sync_max=0)["deleting"] is True
    items = _batch(repos, n_items=11)
    _bulk(db, items, 1, with_job=False)
    for allow_sync in (True, False):
        r = _auto(repos, items, 1, max_items=10, allow_sync=allow_sync, sync_max=500 if allow_sync else 0)
        assert r == {"deleted": False, "reason": "batch_delete_too_many_items", "request_id": None,
                     "limit": 10, "count": 11, "count_at_least": None}
    assert _auto(repos, items, 1, max_items=11)["deleted"] is True


# ---- 4. 동기/백그라운드 갈림 ----

def test_sync_threshold_and_mode(repos, db):
    small = _batch(repos)
    _bulk(db, small, 5)
    r = _auto(repos, small, 5, sync_max=5)
    assert r["deleted"] is True and r["mode"] == "sync" and len(r["request_ids"]) == 5
    big = _batch(repos)
    _bulk(db, big, 6)
    assert _auto(repos, big, 6, sync_max=5)["deleting"] is True
    zero = _batch(repos)
    _bulk(db, zero, 1)
    assert _auto(repos, zero, 1, sync_max=0)["deleting"] is True          # 0 = 늘 백그라운드
    # 확인 창 수를 작게 속여 동기 경로로 보내도 실제 수가 문턱을 넘으면 CAS 가 막는다.
    liar = _batch(repos)
    _bulk(db, liar, 7)
    assert _auto(repos, liar, 3, sync_max=5)["reason"] == "batch_changed" and _kids(db, liar) == 7


def test_early_cas_does_not_lock_children(repos, db):
    bid = _batch(repos)
    _bulk(db, bid, 30, with_job=False)
    seen = []

    class Spy:
        def __init__(self, inner):
            self._inner = inner
            self.dialect = inner.dialect

        def __getattr__(self, name):
            fn = getattr(self._inner, name)
            if name in ("query", "query_one", "execute", "execute_count"):
                def wrap(sql, *a, **k):
                    seen.append(sql)
                    return fn(sql, *a, **k)
                return wrap
            return fn
    r = RequestPurgesRepository(Spy(db)).delete_batch(bid, expected_request_count=3, actor="o", artifact_base=None,
                                                     quiet_seconds=60, max_children=50000)
    assert r["reason"] == "batch_changed"
    assert not any(s.lstrip().startswith("SELECT *") and "FROM requests" in s for s in seen)
    # 최대 commit_order CAS
    top = db.query_one("SELECT MAX(commit_order) AS m FROM requests WHERE batch_id = :b", {"b": bid})["m"]
    assert _auto(repos, bid, 30, expected_max_commit_order=top - 1)["reason"] == "batch_changed"
    assert _auto(repos, bid, 30, expected_max_commit_order=top, sync_max=0)["deleting"] is True


def test_route_sync_budget_call_budget_and_response_shape(client, db, session_admin):
    repos = client.app.state.repos
    client.app.state.settings = dataclasses.replace(client.app.state.settings, batch_delete_sync_max_children=4)
    a, b, c = (_batch(repos, name=x) for x in "abc")
    for x in (a, b, c):
        _bulk(db, x, 3)
    # 동기 합 예산 = 2 × 4 = 8 → 셋째 배치(3)는 거절하지 않고 백그라운드로.
    r = client.post(PATH, json={"batches": [{"batch_id": x, "expected_request_count": 3} for x in (a, b, c)]}).json()
    assert [d["batch_id"] for d in r["deleted_batches"]] == [a, b]
    assert r["deleting_batches"] == [{"batch_id": c, "request_count": 3, "already": False}]
    assert r["skipped_batches"] == [] and r["deleted"] == [] and r["skipped"] == []
    # 같은 배치를 다시 누르면 멱등(already)
    r = client.post(PATH, json={"batches": [{"batch_id": c, "expected_request_count": 999}]}).json()
    assert r["deleting_batches"] == [{"batch_id": c, "request_count": 3, "already": True}]
    assert _count(db, "SELECT COUNT(*) AS n FROM audit_log WHERE operation = 'delete_start'") == 1
    # batches 키가 없으면 배치 키도 없다(옛 호출자)
    assert "deleting_batches" not in client.post(PATH, json={"request_ids": ["x" * 32]}).json()
    # strict
    for bad in (True, "1", -1, 1.0):
        assert client.post(PATH, json={"batches": [{"batch_id": a, "expected_request_count": 1,
                                                    "expected_max_commit_order": bad}]}).status_code == 422


def test_call_budget_constant_and_first_batch_rule():
    from dms.repositories.request_purges import within_call_budget
    assert MAX_DELETE_CALL_CHILDREN == 100000
    assert within_call_budget([("a", 50000), ("b", 50000), ("c", 1)]) == ([("a", 50000), ("b", 50000)], [("c", 1)])
    assert within_call_budget([("a", 100001)]) == ([("a", 100001)], [])


# ---- 5. 판정 패리티 ----

def _parity_cases(repos, db):
    out = {}

    def kid_of(bid):
        return db.query_one("SELECT request_id FROM requests WHERE batch_id = :b ORDER BY request_id LIMIT 1",
                            {"b": bid})["request_id"]
    for st in ("Running", "PreviewReady", "Cancelling", "Weird"):
        bid = _batch(repos, status=st)
        _bulk(db, bid, 2)
        out[f"batch_{st}"] = (bid, "batch_not_deletable", None)
    bid = _batch(repos)
    _bulk(db, bid, 2)
    _bulk(db, bid, 1, state="Planned")
    rid = db.query_one("SELECT request_id FROM requests WHERE batch_id = :b AND state = 'Planned'", {"b": bid})
    out["child_live"] = (bid, "request_not_deletable", rid["request_id"])
    bid = _batch(repos)
    _bulk(db, bid, 2)
    rid = kid_of(bid)
    db.execute("UPDATE requests SET state = 'Weird' WHERE request_id = :r", {"r": rid})      # 모르는 상태 = 비종단
    out["child_unknown_state"] = (bid, "request_not_deletable", rid)
    bid = _batch(repos)
    _bulk(db, bid, 2)
    other = _batch(repos, name="other")
    rid = kid_of(bid)
    db.execute("UPDATE batch_items SET request_id = :r WHERE batch_id = :o", {"r": rid, "o": other})
    out["shared"] = (bid, "batch_child_shared", rid)
    bid = _batch(repos)
    _bulk(db, bid, 2)
    rid = kid_of(bid)
    db.execute("UPDATE data_jobs SET state = 'Running' WHERE request_id = :r", {"r": rid})
    out["job_live"] = (bid, "request_job_active", rid)
    bid = _batch(repos)
    _bulk(db, bid, 2)
    rid = kid_of(bid)
    db.execute("UPDATE data_jobs SET state = 'Weird' WHERE request_id = :r", {"r": rid})
    out["job_unknown_state"] = (bid, "request_job_active", rid)
    bid = _batch(repos)
    _bulk(db, bid, 2)
    rid = kid_of(bid)
    db.execute("UPDATE requests SET updated_at = :t WHERE request_id = :r", {"t": NOW, "r": rid})
    out["child_quiet"] = (bid, "request_recently_finished", rid)
    bid = _batch(repos)
    _bulk(db, bid, 2)
    rid = kid_of(bid)
    db.execute("UPDATE data_jobs SET updated_at = :t WHERE request_id = :r", {"t": "2999-01-01T00:00:00Z", "r": rid})
    out["job_quiet"] = (bid, "request_recently_finished", rid)
    out["gone"] = ("e" * 32, "batch_not_found", None)
    return out


def test_sync_a_and_b_stage_verdicts_agree(repos, db):
    cases = _parity_cases(repos, db)
    rp = repos.request_purges
    for name, (bid, reason, culprit) in cases.items():
        n = _kids(db, bid)
        sync = rp.delete_batch(bid, expected_request_count=n, actor="o", artifact_base=None, quiet_seconds=60,
                               max_children=50000, now=NOW)
        a = rp.batch_delete_check(bid, cap=50000, quiet_seconds=60, expected_request_count=n, now=NOW)
        assert (sync["reason"], sync["request_id"]) == (reason, culprit), name
        assert (a["reason"], a["request_id"]) == (reason, culprit), name
        if reason in ("batch_not_deletable", "batch_not_found", "request_not_deletable", "request_recently_finished"):
            # B단계가 다시 보는 판정(잡·다른 배치 참조는 A단계 몫 -- 청크가 잠근 행으로 다시 본다).
            b = rp._begin_batch_delete(bid, expected_request_count=n, expected_max_commit_order=None, actor="o",
                                       cap=50000, max_items=10000, quiet_seconds=60, sync_threshold=500, now=NOW,
                                       scan_storage=None)
            if name == "job_quiet":
                continue
            assert (b["reason"], b["request_id"]) == (reason, culprit), name
        assert db.query_one("SELECT 1 AS x FROM batch_deletions WHERE batch_id = :b", {"b": bid}) is None, name


def test_scan_purity_parity(repos, db):
    rp = repos.request_purges
    cases = []
    bid = _batch(repos, operation="rm")
    _bulk(db, bid, 1, storage="old")
    cases.append((bid, None))
    bid = _batch(repos, storage="other")
    _bulk(db, bid, 1, storage="old")
    cases.append((bid, db.query_one("SELECT request_id FROM batch_items WHERE batch_id = :b", {"b": bid})["request_id"]))
    bid = _batch(repos, storage="old")
    (rid, _), = _bulk(db, bid, 1, storage="mixed")
    cases.append((bid, rid))
    for bid, culprit in cases:
        n = _kids(db, bid)
        sync = rp.delete_batch(bid, expected_request_count=n, actor="o", artifact_base=None, quiet_seconds=60,
                               max_children=50000, now=NOW, scan_storage="old")
        a = rp.batch_delete_check(bid, cap=50000, quiet_seconds=60, expected_request_count=n, now=NOW,
                                  scan_storage="old")
        assert sync["reason"] == a["reason"] == "storage_history_batch_mixed"
        assert sync["request_id"] == a["request_id"] == culprit


# ---- 7. 묘비 효과 ----

def test_tombstone_effects_and_idempotency(repos, db):
    bid = _batch(repos)
    _bulk(db, bid, 3)
    sid = repos.batch_schedules.create(batch_id=bid, kind="daily", spec={"hour": 2, "minute": 0}, enabled=True,
                                       next_run_at=NOW, actor="ops", arm=lambda b, i: (None, None))
    repos.batch_schedules._insert_run(repos.batch_schedules.get(sid), outcome="skipped", reason_code="x", now=NOW,
                                      batch_status="Completed")
    db.execute("INSERT INTO batch_rounds (batch_id, round_seq, started_at, floor_order, via, actor, requeued) "
               "VALUES (:b, 1, :t, NULL, 'rescan', 'ops', 0)", {"b": bid, "t": OLD})
    assert _auto(repos, bid, 3, sync_max=0)["deleting"] is True
    for t in ("batch_rounds", "batch_schedules", "batch_schedule_runs"):
        assert _count(db, f"SELECT COUNT(*) AS n FROM {t} WHERE batch_id = :b", b=bid) == 0, t
    ops = [r["operation"] for r in db.query(
        "SELECT mutation_class || ':' || operation AS operation FROM audit_log WHERE target_key = :b ORDER BY id",
        {"b": bid})]
    assert ops[-2:] == ["batch_schedule:delete", "batch:delete_start"]
    start = json.loads(db.query_one("SELECT before_state FROM audit_log WHERE operation = 'delete_start'")["before_state"])
    assert (start["prior_status"], start["expected_request_count"], start["dangling"], start["cap"]) == \
        ("Completed", 3, False, 50000)
    assert _kids(db, bid) == 3 and repos.batches.get(bid) is not None            # 자식·배치 행은 그대로
    n_audit = _count(db, "SELECT COUNT(*) AS n FROM audit_log")
    assert _auto(repos, bid, 3, sync_max=0) == {"deleted": False, "deleting": True, "already": True,
                                                "request_count": 3}
    assert _count(db, "SELECT COUNT(*) AS n FROM audit_log") == n_audit


def test_dangling_group_tombstone(repos, db):
    bid = uuid.uuid4().hex
    _bulk(db, bid, 4)
    r = _auto(repos, bid, 4, sync_max=0)
    assert r["deleting"] is True
    assert db.query_one("SELECT dangling, prior_status FROM batch_deletions WHERE batch_id = :b", {"b": bid}) == \
        {"dangling": 1, "prior_status": None}


def test_concurrent_dangling_tombstones_have_one_winner(db, tmp_path):
    # PG 동시성(실제 행 잠금) -- 이 라인의 conftest 엔 PG 하네스가 없어 sqlite 갈래에선 건너뛴다(tests/test_batch_round_prune.py 의
    # _pg_or_skip·_second 와 같은 방식: sqlite 접속을 같은 URL → 같은 PG 스키마로 돌리는 플러그인으로 돌린다).
    from dms.db import Database
    if db.dialect != "postgresql":
        pytest.skip("PG 동시성 -- 운영 방언에서만(sqlite 는 한 커넥션 RLock 이 직렬화한다)")
    conns = [Database.connect(f"sqlite:///{tmp_path}/test.db") for _ in range(2)]   # conftest db 와 같은 URL(같은 스키마)
    for c in conns:
        assert c.dialect == "postgresql"
        c.execute("SET lock_timeout = '5s'")
    bid = uuid.uuid4().hex
    _bulk(db, bid, 3)
    barrier, out = threading.Barrier(2), []

    def go(c):
        barrier.wait()
        out.append(RequestPurgesRepository(c).delete_batch_auto(
            bid, expected_request_count=3, actor="o", artifact_base=None, quiet_seconds=60, cap=50000, sync_max=0,
            allow_sync=False))
    ts = [threading.Thread(target=go, args=(c,)) for c in conns]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    for c in conns:
        c._conn.close()
    assert sum(1 for r in out if r.get("deleting") and r.get("already") is False) == 1
    assert _count(db, "SELECT COUNT(*) AS n FROM audit_log WHERE operation = 'delete_start'") == 1


# ---- 8·9·10. 청크·재개·마무리 ----

def test_chunks_audit_lane_and_finish(repos, db, settings):
    bid = _batch(repos, n_items=3)
    _bulk(db, bid, 250)
    assert _auto(repos, bid, 250, sync_max=0)["deleting"] is True
    rp = repos.request_purges
    res = rp.delete_batch_chunk(bid, artifact_base="/base", quiet_seconds=60)
    assert len(res["deleted"]) == DELETE_CHUNK
    d = db.query_one("SELECT * FROM batch_deletions WHERE batch_id = :b", {"b": bid})
    assert (d["deleted_request_count"], d["chunks"], d["last_error"]) == (100, 1, None)
    assert d["scan_after"] == res["scan_next"]
    rows = db.query("SELECT purge_group, requested_by FROM request_purges")
    assert len(rows) == 100 and {(r["purge_group"], r["requested_by"]) for r in rows} == {(bid, "opadm")}
    chunk = db.query_one("SELECT actor, before_state FROM audit_log WHERE operation = 'delete_chunk'")
    body = json.loads(chunk["before_state"])
    assert chunk["actor"] == "opadm" and body["executor"] == "system:batch-delete" and body["chunk_seq"] == 1
    assert body["deleted_count"] == 100 and len(chunk["before_state"].encode()) <= 32 * 1024
    assert _count(db, "SELECT COUNT(*) AS n FROM audit_log WHERE mutation_class = 'request' AND operation = 'delete'") == 0
    _drain(repos, settings)
    assert _kids(db, bid) == 0 and repos.batches.get(bid) is None
    for t in ("batch_items", "batch_deletions", "batch_rounds", "batch_schedules"):
        assert _count(db, f"SELECT COUNT(*) AS n FROM {t} WHERE batch_id = :b", b=bid) == 0
    assert _count(db, "SELECT COUNT(*) AS n FROM request_purges") == 250
    assert _count(db, "SELECT COUNT(*) AS n FROM data_jobs") == 0
    ops = [r["operation"] for r in db.query("SELECT operation FROM audit_log WHERE target_key = :b ORDER BY id",
                                            {"b": bid})]
    assert ops == ["delete_start", "delete_chunk", "delete_chunk", "delete_chunk", "delete"]
    final = json.loads(db.query_one("SELECT before_state FROM audit_log WHERE operation = 'delete'")["before_state"])
    assert final["chunked"] is True and final["batch"]["status"] == "Completed" and final["child_count"] == 250
    assert final["deleted_request_count"] == 250 and final["chunks"] == 3 and final["item_count"] == 3


def test_tampered_children_are_left_and_block_with_backoff(repos, db, settings):
    bid = _batch(repos)
    kids = _bulk(db, bid, 5)
    assert _auto(repos, bid, 5, sync_max=0)["deleting"] is True
    late, = _bulk(db, bid, 1)                                   # 묘비 뒤 끼어든 자식(최대 commit_order 초과)
    db.execute("UPDATE requests SET state = 'Planned' WHERE request_id = :r", {"r": kids[0][0]})
    other = _batch(repos, name="o")
    db.execute("UPDATE batch_items SET request_id = :r WHERE batch_id = :o", {"r": kids[1][0], "o": other})
    _drain_once = BatchDeleter(repos, settings=settings, budget_seconds=60)
    _drain_once.run_once()
    left = {r["request_id"] for r in db.query("SELECT request_id FROM requests WHERE batch_id = :b", {"b": bid})}
    assert left == {kids[0][0], kids[1][0], late[0]}
    d = db.query_one("SELECT * FROM batch_deletions WHERE batch_id = :b", {"b": bid})
    assert d["deleted_request_count"] == 3 and d["attempts"] == 1 and d["last_error"] is not None
    assert d["next_attempt_at"] > d["updated_at"] or d["next_attempt_at"] != d["updated_at"]
    ev = db.query("SELECT event_type, request_id FROM events WHERE component = 'batch-delete'")
    assert ev and all(e["request_id"] is None for e in ev)
    # 같은 사유로 다시 막히면 이벤트를 더 남기지 않는다
    n_ev = len(ev)
    db.execute("UPDATE batch_deletions SET next_attempt_at = :t", {"t": OLD})
    BatchDeleter(repos, settings=settings, budget_seconds=60).run_once()
    assert len(db.query("SELECT 1 AS x FROM events WHERE component = 'batch-delete'")) == n_ev
    assert db.query_one("SELECT attempts FROM batch_deletions WHERE batch_id = :b", {"b": bid})["attempts"] == 2


def test_backoff_schedule(repos, db):
    bid = _batch(repos)
    _bulk(db, bid, 1)
    _auto(repos, bid, 1, sync_max=0)
    rp = repos.request_purges
    delays = []
    for _ in range(8):
        rp.defer_batch_delete(bid, reason_code="request_job_active", now=NOW)
        nxt = db.query_one("SELECT next_attempt_at FROM batch_deletions WHERE batch_id = :b", {"b": bid})["next_attempt_at"]
        from dms.db import iso_epoch
        delays.append(int(iso_epoch(nxt) - iso_epoch(NOW)))
    assert delays == [15, 30, 60, 120, 240, 480, 900, 900]


def test_chunk_rolls_back_on_failure_and_a_new_instance_resumes(repos, db, settings, monkeypatch):
    bid = _batch(repos)
    _bulk(db, bid, 150)
    _auto(repos, bid, 150, sync_max=0)
    rp = repos.request_purges
    real = rp._enqueue
    calls = [0]

    def boom(*a, **k):
        calls[0] += 1
        if calls[0] == 50:
            raise RuntimeError("disk full")
        return real(*a, **k)
    monkeypatch.setattr(rp, "_enqueue", boom)
    with pytest.raises(RuntimeError):
        rp.delete_batch_chunk(bid, artifact_base=None, quiet_seconds=60)
    assert _kids(db, bid) == 150 and _count(db, "SELECT COUNT(*) AS n FROM request_purges") == 0
    d = db.query_one("SELECT deleted_request_count, chunks, scan_after FROM batch_deletions WHERE batch_id = :b",
                     {"b": bid})
    assert d == {"deleted_request_count": 0, "chunks": 0, "scan_after": None}
    monkeypatch.setattr(rp, "_enqueue", real)
    BatchDeleter(repos, settings=settings, budget_seconds=60).run_once()
    _drain(repos, settings)
    assert _kids(db, bid) == 0 and repos.batches.get(bid) is None
    assert _count(db, "SELECT COUNT(*) AS n FROM audit_log WHERE target_key = :b AND operation = 'delete_chunk'", b=bid) == 2


def test_finish_remaining_and_tampered_status(repos, db):
    bid = _batch(repos)
    _bulk(db, bid, 2)
    _auto(repos, bid, 2, sync_max=0)
    rp = repos.request_purges
    assert rp.finish_batch_delete(bid) == {"remaining": True}
    db.execute("UPDATE batches SET status = 'Completed' WHERE batch_id = :b", {"b": bid})
    db.execute("DELETE FROM requests WHERE batch_id = :b", {"b": bid})
    assert rp.finish_batch_delete(bid) == {"blocked": "batch_not_deletable", "request_id": None}
    assert repos.batches.get(bid) is not None and _count(db, "SELECT COUNT(*) AS n FROM batch_items WHERE batch_id = :b",
                                                         b=bid) == 1


# ---- 11. 되살림 경로 ----

def _session(client, name="opadm", role="admin"):
    client.app.state.repos.accounts.create(name, "p", role, actor="t")
    assert client.post("/api/auth/login", json={"username": name, "password": "p"}).status_code == 200


@pytest.fixture
def session_admin(client):
    _session(client)


def test_routes_refuse_deleting_batches(client, db, session_admin):
    repos = client.app.state.repos
    client.app.state.settings = dataclasses.replace(client.app.state.settings, allow_privileged_requesters=True,
                                                     privileged_requesters=frozenset({"opadm"}))
    bid = _batch(repos)
    _bulk(db, bid, 2)
    _auto(repos, bid, 2, sync_max=0)
    b = f"/api/admin/batches/{bid}"
    calls = [("post", f"{b}/items", {"storage": "s1", "target": "z"}), ("put", f"{b}/items/0", {"storage": "s1", "target": "z"}),
             ("delete", f"{b}/items/0", None), ("put", f"{b}/items", {"items": [{"storage": "s1", "target": "z"}]}),
             ("post", f"{b}/items:rerun", {"seqs": [0]}), ("post", f"{b}:rerun-failed", None),
             ("patch", b, {"name": "x"}), ("patch", f"{b}/execution", {"max_concurrency": 2}),
             ("post", f"{b}:rescan", None), ("delete", b, None), ("post", f"{b}:auto-confirm-off", None)]
    for method, url, body in calls:
        r = getattr(client, method)(url, **({"json": body} if body is not None else {}))
        assert (r.status_code, r.json().get("detail")) == (409, "batch_deleting"), (method, url, r.text)
    for url in (f"{b}:confirm", f"{b}:cancel"):
        assert client.post(url, json={"preview_round": 0}).status_code == 409
    r = client.post("/api/admin/batch-schedules", json={"batch_id": bid, "kind": "daily", "spec": {"hour": 1, "minute": 0}})
    assert (r.status_code, r.json().get("detail")) == (409, "batch_deleting"), r.text
    detail = client.get(b, params={"view": "summary"}).json()
    assert detail["status"] == "Deleting" and detail["deleting"]["expected_request_count"] == 2


def test_repository_atomic_guards(repos, db):
    bid = _batch(repos)
    _bulk(db, bid, 1)
    _auto(repos, bid, 1, sync_max=0)
    bt = repos.batches
    assert bt.add_item(bid, {"storage": "s1", "target": "q"}) == "Deleting"
    assert bt.update_item_payload(bid, 0, {"storage": "s1", "target": "q"}, only_queued=False) == "Deleting"
    assert bt.delete_item(bid, 0, only_queued=False) == "Deleting"
    assert bt.replace_items(bid, [{"storage": "s1", "target": "q"}]) == "Deleting"
    assert bt.update_meta(bid, name="x") == 0
    # 선택·실패분 재실행도 같은 sentinel(이식 리뷰 -- 라우트가 Completed 를 읽은 뒤 묘비가 서면 항목만 Queued 로 바뀌었다).
    db.execute("UPDATE batch_items SET status = 'Failed' WHERE batch_id = :b", {"b": bid})
    assert bt.reset_items_to_queued(bid, [0]) == "Deleting"
    assert bt.reset_failed_items(bid) == "Deleting"
    assert bt.list_items(bid)[0]["status"] == "Failed"
    # 이 라인의 기록 삭제는 라우트가 종단 CAS(require_terminal)로 부른다 -- Deleting 은 그 CAS 에서 진다.
    assert bt.delete(bid, require_terminal=True, actor="o") is False
    assert bt.rescan_if_terminal(bid, actor="o") is None
    from dms.domain import DomainValidationError
    with pytest.raises(DomainValidationError) as e:
        repos.batch_schedules.create(batch_id=bid, kind="daily", spec={"hour": 1, "minute": 0}, enabled=False,
                                     next_run_at=None, actor="o", arm=lambda b, i: (None, None))
    assert e.value.reason_code == "batch_deleting"
    assert bt.get(bid)["status"] == "Deleting" and bt.get(bid)["item_count"] == 1


@pytest.mark.parametrize("route,body", [("items:rerun", {"seqs": [0]}), (":rerun-failed", None)])
def test_rerun_routes_lose_to_a_tombstone_committed_after_their_read(client, db, session_admin, monkeypatch, route, body):
    # 라우트의 _reject_frozen 은 Completed 를 봤는데 그 뒤 묘비가 섰다 -- 저장소 원자 가드가 409 batch_deleting 으로 접는다.
    repos = client.app.state.repos
    client.app.state.settings = dataclasses.replace(client.app.state.settings, allow_privileged_requesters=True,
                                                     privileged_requesters=frozenset({"opadm"}))
    bid = _batch(repos)
    db.execute("UPDATE batch_items SET status = 'Failed' WHERE batch_id = :b", {"b": bid})
    stale = repos.batches.get(bid)
    db.execute("UPDATE batches SET status = 'Deleting' WHERE batch_id = :b", {"b": bid})
    real_get = repos.batches.get
    monkeypatch.setattr(repos.batches, "get", lambda b: stale if b == bid else real_get(b))
    path = f"/api/admin/batches/{bid}/{route}" if route.startswith("items") else f"/api/admin/batches/{bid}{route}"
    r = client.post(path, **({"json": body} if body is not None else {}))
    assert (r.status_code, r.json().get("detail")) == (409, "batch_deleting"), r.text
    assert repos.batches.list_items(bid)[0]["status"] == "Failed"


def test_deleting_batch_does_not_hold_its_storage(client, db, session_admin):
    # 이 라인의 스토리지 사용 중 가드(활성 배치 포함)는 묘비 배치를 세지 않는다 -- 되살릴 수 없고 청크 삭제는 경로를 읽지 않는다.
    # 세면 큰 배치를 지우는 동안(유지보수·drain 엔 루프가 쉰다) 경로 변경·삭제·재등록이 근거 없이 막혔다(이식 리뷰).
    repos = client.app.state.repos
    repos.storages.create(storage_name="s1", mount_path="/mnt/s1", managed_root="/mnt/s1/m", backend_type="cephfs", actor="t")
    bid = _batch(repos)
    _bulk(db, bid, 2)
    _auto(repos, bid, 2, sync_max=0)
    assert repos.batches.get(bid)["status"] == "Deleting"
    assert repos.batches.active_referencing_storage("s1") is False
    r = client.put("/api/admin/storages/s1", json={"mount_path": "/mnt/s1", "managed_root": "/mnt/s1/other",
                                                   "backend_type": "cephfs", "enabled": True})
    assert r.status_code == 200, r.text
    assert client.delete("/api/admin/storages/s1").status_code == 200
    r = client.post("/api/admin/storages", json={"storage_name": "s1", "mount_path": "/mnt/s1",
                                                 "managed_root": "/mnt/s1/m", "backend_type": "cephfs"})
    assert r.status_code == 201, r.text


def test_orchestrator_and_scheduler_leave_deleting_batches_alone(repos, db, settings):
    from dms.batch_orchestrator import BatchOrchestrator
    from dms.batch_scheduler import schedule_hold
    bid = _batch(repos)
    _bulk(db, bid, 1)
    _auto(repos, bid, 1, sync_max=0)
    BatchOrchestrator(repos, settings=settings).run_once()
    assert repos.batches.get(bid)["status"] == "Deleting"
    batch = repos.batches.get(bid)
    # 묘비가 스케줄을 지우므로 보통은 오지 않는 그물 -- 다른 배치의 실제 스케줄 행으로 판정만 본다.
    other = _batch(repos, name="other")
    sid = repos.batch_schedules.create(batch_id=other, kind="daily", spec={"hour": 2, "minute": 0}, enabled=True,
                                       next_run_at=NOW, actor="ops", arm=lambda b, i: (None, None))
    row = {**repos.batch_schedules.get(sid), "batch_id": bid}
    for name in ("ops", "opadm"):
        repos.accounts.create(name, "p", "admin", actor="t")
    repos.storages.create(storage_name="s1", mount_path="/mnt/s1", managed_root="/mnt/s1/m", backend_type="cephfs",
                          actor="t")
    repos.storages.set_status("s1", "Ready")
    priv = dataclasses.replace(settings, allow_privileged_requesters=True,
                               privileged_requesters=frozenset({"ops", "opadm"}))
    verdict = schedule_hold(repos, priv, row, batch, repos.batches.list_items(bid), control={}, now=NOW)
    assert verdict.reason_code == "batch_not_rescannable"


# ---- 13. due() 레인 ----

def test_due_alternates_lanes(repos, db):
    rows = []
    for group, n in ((None, 3), ("g1", 3), ("g2", 2)):
        for i in range(n):
            rid = uuid.uuid4().hex
            at = f"2026-01-0{i + 1}T00:00:00Z"
            db.execute("INSERT INTO request_purges (request_id, jobs, artifact_base, stage, outcomes, attempts, last_error, "
                       "next_attempt_at, requested_by, requested_at, updated_at, purge_group) "
                       "VALUES (:r, '[]', NULL, 'k8s', NULL, 0, NULL, :at, 'o', :at, :at, :g)",
                       {"r": rid, "at": at, "g": group})
            rows.append((group, i, rid))
    got = repos.request_purges.due(NOW, limit=8)
    lane = {rid: (g, i) for g, i, rid in rows}
    ranks = [lane[r["request_id"]][1] for r in got]
    assert ranks == sorted(ranks)                          # 레인마다 1등 → 2등 → …
    assert {lane[r["request_id"]][0] for r in got[:3]} == {None, "g1", "g2"}
    assert len(repos.request_purges.due(NOW, limit=2)) == 2
    assert all("lane_rank" not in r for r in got)


# ---- 14·15. 목록 요약·미리보기 ----

def test_list_summaries_deleting_and_with_scans(repos, db):
    bid = _batch(repos)
    _bulk(db, bid, 3)
    _auto(repos, bid, 3, sync_max=0)
    s = repos.batches.list_summaries([bid], cap=5000)[bid]
    assert s["batch_deleting"]["expected"] == 3 and s["batch_deleting"]["deleted"] == 0
    assert s["batch_max_commit_order"] is not None
    s = repos.batches.list_summaries([bid], cap=5000, with_scans=False)[bid]
    assert s["batch_succeeded_scan_count"] is None and s["batch_request_count"] == 3


def test_preview_shape_verdicts_and_limits(client, db):
    repos = client.app.state.repos
    client.app.state.settings = dataclasses.replace(client.app.state.settings, batch_delete_max_children=10,
                                                     batch_delete_sync_max_children=3)
    small, big, over, live = (_batch(repos, name=x) for x in ("small", "big", "over", "live"))
    _bulk(db, small, 2)
    _bulk(db, big, 5)
    _bulk(db, over, 12)
    _bulk(db, live, 1, state="Planned")
    deleting = _batch(repos, name="deleting")
    _bulk(db, deleting, 1)
    _auto(repos, deleting, 1, sync_max=0)
    r = client.post(PREVIEW, json={"batch_ids": [small, big, over, live, deleting, "f" * 32, "bad"]},
                    headers={"Authorization": "Bearer tok-shared"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["limits"] == {"requests_per_call": 200, "batches_per_call": 10, "batch_request_count": 10,
                              "batch_item_count": 10000, "call_request_count": 100000, "sync_request_count": 3,
                              "list_count_cap": 10}
    v = {row["batch_id"]: row for row in body["batches"]}
    assert v[small]["verdict"] == "sync" and v[small]["request_count"] == 2 and v[small]["max_commit_order"] is not None
    assert v[big]["verdict"] == "async"
    # 상한을 넘으면 정확한 수(12)를 다시 세지 않고 하한 cap + 1 만 싣는다(2026-11-19 커밋 보안 리뷰 — 락 보유를 cap + 1 창으로 묶는다).
    assert (v[over]["verdict"], v[over]["reason"], v[over]["limit"], v[over]["count"], v[over]["count_at_least"]) == \
        ("blocked", "batch_delete_too_large", 10, None, 11)
    assert v[live]["verdict"] == "blocked" and v[live]["reason"] == "request_not_deletable"
    assert v[deleting]["verdict"] == "deleting" and v[deleting]["deleting"]["expected_request_count"] == 1
    assert v["f" * 32]["verdict"] == "gone" and v["bad"]["verdict"] == "gone"
    many = client.post(PREVIEW, json={"batch_ids": [uuid.uuid4().hex for _ in range(11)]},
                       headers={"Authorization": "Bearer tok-shared"})
    assert (many.status_code, many.json()["detail"]) == (422, "delete_batch_selection_too_large")


def test_preview_counts_summaries_one_batch_per_call(client, db, monkeypatch):
    # 미리보기의 목록 요약은 배치마다 따로(2026-11-19 검증 지적): 고른 배치 전부를 한 UNION ALL 문장으로 세면 5만 자식 배치 10개에서
    # 그 한 문장이 API RLock 을 로컬 ~1.7초 쥐었다(tests/perf_cap50k.py preview 가 문장 시간을 잰다).
    from dms.repositories.batches import BatchesRepository
    repos = client.app.state.repos
    bids = [_batch(repos, name=f"b{i}") for i in range(3)]
    for b in bids:
        _bulk(db, b, 2)
    calls = []
    real = BatchesRepository.list_summaries

    def spy(self, batch_ids, **kw):
        calls.append(list(batch_ids))
        return real(self, batch_ids, **kw)
    monkeypatch.setattr(BatchesRepository, "list_summaries", spy)
    r = client.post(PREVIEW, json={"batch_ids": bids}, headers={"Authorization": "Bearer tok-shared"})
    assert r.status_code == 200, r.text
    assert calls == [[b] for b in bids]
    assert all(row["live_request_count"] == 0 and row["succeeded_scan_count"] == 2 for row in r.json()["batches"])


def test_request_purges_status_carries_limits_and_deletions(client, db):
    repos = client.app.state.repos
    bid = _batch(repos, name="지우는 중")
    _bulk(db, bid, 2)
    _auto(repos, bid, 2, sync_max=0)
    body = client.get("/api/admin/request-purges", headers={"Authorization": "Bearer tok-shared"}).json()
    assert body["limits"]["batch_request_count"] == 50000 and body["limits"]["list_count_cap"] == 5000
    assert body["batch_deletions"][0]["batch_id"] == bid and body["batch_deletions"][0]["name"] == "지우는 중"
    assert body["batch_deletions"][0]["expected_request_count"] == 2


def test_request_list_carries_settings_cap_and_deleting(client, db, session_admin):
    repos = client.app.state.repos
    bid = _batch(repos)
    _bulk(db, bid, 2)
    _auto(repos, bid, 2, sync_max=0)
    rows = [r for r in client.get("/api/user/requests").json() if r["batch_id"] == bid]
    assert rows and rows[0]["batch_request_count_limit"] == 50000
    assert rows[0]["batch_deleting"]["expected"] == 2 and rows[0]["batch_status"] == "Deleting"


# ---- 17. 미등록 스토리지 정리 ----

def test_unregistered_purge_goes_background_and_stops_the_round_loop(client, db, session_admin):
    repos = client.app.state.repos
    client.app.state.settings = dataclasses.replace(client.app.state.settings, batch_delete_sync_max_children=0)
    bid = _batch(repos, storage="gone")
    _bulk(db, bid, 3, storage="gone")
    r = client.post("/api/admin/usage/unregistered-storages:purge", json={"storage_name": "gone"}).json()
    assert r["deleting_batches"] == [{"batch_id": bid, "request_count": 3, "already": False}]
    assert r["remaining_untried"] == {"requests": 0, "batches": 0}
    assert db.query_one("SELECT scan_storage FROM batch_deletions WHERE batch_id = :b", {"b": bid})["scan_storage"] == "gone"


# ---- 18. 컨트롤러 ----

def test_loop_is_assembled_unconditionally(db, settings):
    names = [lp.name for lp in build_loops(settings, Repositories(db))]
    assert "batch-delete" in names


@pytest.mark.parametrize("column", ["maintenance", "drain"])
def test_loop_holds_in_maintenance(repos, db, settings, column):
    bid = _batch(repos)
    _bulk(db, bid, 2)
    _auto(repos, bid, 2, sync_max=0)
    db.execute(f"UPDATE control_state SET {column} = 1 WHERE id = 1")
    assert BatchDeleter(repos, settings=settings).run_once() == {"held": True}
    assert _kids(db, bid) == 2


def test_loop_round_robins_within_the_tick_budget(repos, db, settings):
    a, b = _batch(repos, name="a"), _batch(repos, name="b")
    _bulk(db, a, 300)
    _bulk(db, b, 300)
    _auto(repos, a, 300, sync_max=0)
    _auto(repos, b, 300, sync_max=0)
    ticks = iter(range(0, 1000))
    # 가짜 시계: 호출마다 0.6초 -- 예산 2초면 청크 두세 개만 돈다.
    r = BatchDeleter(repos, settings=settings, clock=lambda: next(ticks) * 0.6).run_once()
    assert r["chunks"] in (2, 3)
    left = {x: _kids(db, x) for x in (a, b)}
    assert left[a] < 300 and left[b] < 300                 # 둘 다 진행(독차지 없음)


def test_loop_exception_backs_off_with_an_event(repos, db, settings, monkeypatch):
    bid = _batch(repos)
    _bulk(db, bid, 2)
    _auto(repos, bid, 2, sync_max=0)

    def boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(repos.request_purges, "delete_batch_chunk", boom)
    BatchDeleter(repos, settings=settings).run_once()
    d = db.query_one("SELECT attempts, last_error FROM batch_deletions WHERE batch_id = :b", {"b": bid})
    assert d == {"attempts": 1, "last_error": "batch_delete_failed"}
    assert db.query("SELECT event_type FROM events WHERE component = 'batch-delete'") == \
        [{"event_type": "batch_delete_error"}]


# ---- 19. 스케줄 화면 ----

def test_schedule_child_count_limit_follows_the_setting(client, db):
    client.app.state.settings = dataclasses.replace(client.app.state.settings, batch_delete_max_children=1234)
    r = client.post("/api/admin/batch-schedules:preview", json={"kind": "daily", "spec": {"hour": 1, "minute": 0}},
                    headers={"Authorization": "Bearer tok-shared"})
    assert r.status_code == 200, r.text
    assert r.json()["child_count_limit"] == 1234


def test_picks_refused_under_lock_back_off(repos, db, settings):
    # 고르기 술어가 보지 않는 판정(순수성 -- 미등록 스토리지 정리)으로 고른 자식이 전부 막히면 같은 자식을 매 틱 다시 고르지 않게
    # 백오프한다(사유·문제 자식은 묘비 행에).
    bid = _batch(repos, storage="gone")
    _bulk(db, bid, 3, storage="gone")
    assert _auto(repos, bid, 3, sync_max=0, scan_storage="gone")["deleting"] is True
    db.execute("UPDATE data_jobs SET storage_name = 'other' WHERE request_id IN "
               "(SELECT request_id FROM requests WHERE batch_id = :b)", {"b": bid})
    BatchDeleter(repos, settings=settings, budget_seconds=60).run_once()
    d = db.query_one("SELECT attempts, last_error, blocked_request_id FROM batch_deletions WHERE batch_id = :b", {"b": bid})
    assert d["attempts"] == 1 and d["last_error"] == "storage_history_batch_mixed" and d["blocked_request_id"] is not None
    assert _kids(db, bid) == 3


def test_unpickable_children_over_the_scan_budget_block_with_backoff(repos, db, settings):
    # 고를 수 없는 자식이 훑기 예산(DELETE_PICK_SCAN_BUDGET)보다 많아도 바퀴가 끝나면 막힘으로 표면화한다(2026-11-19 검증 지적):
    # 예전엔 「처음부터 → 예산 소진(커서) → 끝까지 → 되감기」가 끝없이 돌아 blocked·백오프·last_error·경고 이벤트가 없었다.
    from dms.repositories import request_purges as rp_mod
    n = rp_mod.DELETE_PICK_SCAN_BUDGET + 1
    bid = _batch(repos)
    _bulk(db, bid, n)
    assert _auto(repos, bid, n, sync_max=0)["deleting"] is True
    db.execute("UPDATE data_jobs SET state = 'Running' WHERE request_id IN "
               "(SELECT request_id FROM requests WHERE batch_id = :b)", {"b": bid})
    rp = repos.request_purges
    first = rp.delete_batch_chunk(bid, artifact_base="/base", quiet_seconds=60, now=NOW)
    assert first["deleted"] == [] and first["scan_next"] is not None      # 예산 소진 -- 커서만
    second = rp.delete_batch_chunk(bid, artifact_base="/base", quiet_seconds=60, now=NOW)
    assert second.get("blocked") == "request_job_active" and second["request_id"] is not None, second
    assert db.query_one("SELECT scan_after FROM batch_deletions WHERE batch_id = :b", {"b": bid})["scan_after"] is None
    # 루프 한 틱이 같은 결과로 백오프·사유·경고 이벤트를 남긴다(같은 틱 안에서 예산 소진 → 바퀴 끝 막힘).
    db.execute("UPDATE batch_deletions SET scan_after = NULL, attempts = 0, last_error = NULL, next_attempt_at = :t",
               {"t": OLD})
    BatchDeleter(repos, settings=settings, budget_seconds=60).run_once()
    d = db.query_one("SELECT * FROM batch_deletions WHERE batch_id = :b", {"b": bid})
    assert d["attempts"] == 1 and d["last_error"] == "request_job_active" and d["blocked_request_id"] is not None
    assert d["next_attempt_at"] > d["updated_at"]
    ev = db.query("SELECT event_type FROM events WHERE component = 'batch-delete'")
    assert [e["event_type"] for e in ev] == ["batch_delete_blocked"]
    assert _kids(db, bid) == n                                            # 아무것도 지우지 않았다(fail-closed)


def test_cursor_lap_end_blocks_instead_of_rewinding_silently(repos, db, monkeypatch):
    # 커서부터 끝까지 훑어 고를 것이 없으면(앞쪽은 이 바퀴에 지나왔다) 되감기만 하지 않고 그 자리에서 막힘을 판정한다.
    from dms.repositories import request_purges as rp_mod
    monkeypatch.setattr(rp_mod, "DELETE_PICK_WINDOW", 4)
    monkeypatch.setattr(rp_mod, "DELETE_PICK_SCAN_BUDGET", 8)
    bid = _batch(repos)
    kids = _bulk(db, bid, 20)
    assert _auto(repos, bid, 20, sync_max=0)["deleting"] is True
    db.execute("UPDATE data_jobs SET state = 'Running' WHERE request_id IN "
               "(SELECT request_id FROM requests WHERE batch_id = :b)", {"b": bid})
    rp = repos.request_purges
    seen = [rp.delete_batch_chunk(bid, artifact_base="/base", quiet_seconds=60, now=NOW) for _ in range(3)]
    assert [s.get("scan_next") is not None for s in seen[:2]] == [True, True]
    assert seen[2] == {"blocked": "request_job_active", "request_id": kids[0][0]}


def test_commit_order_zero_child_is_picked_and_the_delete_finishes(repos, db, settings):
    # 커서가 없을 때 하한(commit_order > 0)이 commit_order 0 자식을 늘 빼던 구멍(2026-11-19 검증 지적) -- 하한 없이 고른다.
    bid = _batch(repos)
    kids = _bulk(db, bid, 3)
    assert _auto(repos, bid, 3, sync_max=0)["deleting"] is True
    db.execute("UPDATE requests SET commit_order = 0 WHERE request_id = :r", {"r": kids[1][0]})
    _drain(repos, settings, ticks=5)
    assert _kids(db, bid) == 0
    assert db.query_one("SELECT 1 AS x FROM batches WHERE batch_id = :b", {"b": bid}) is None


def test_negative_commit_order_child_blocks_as_batch_changed(repos, db, settings):
    bid = _batch(repos)
    kids = _bulk(db, bid, 2)
    assert _auto(repos, bid, 2, sync_max=0)["deleting"] is True
    db.execute("UPDATE requests SET commit_order = -5 WHERE request_id = :r", {"r": kids[0][0]})
    BatchDeleter(repos, settings=settings, budget_seconds=60).run_once()
    d = db.query_one("SELECT * FROM batch_deletions WHERE batch_id = :b", {"b": bid})
    assert d["attempts"] == 1 and d["last_error"] == "batch_changed" and d["blocked_request_id"] == kids[0][0]
    assert {r["request_id"] for r in db.query("SELECT request_id FROM requests WHERE batch_id = :b", {"b": bid})} \
        == {kids[0][0]}


def test_predicate_mismatch_surfaces_as_blocked(repos, db, monkeypatch):
    # 재판정은 지울 수 있다는데 고르기 술어가 늘 못 고르는 자식(술어 어긋남)은 조용히 돌지 않고 막힘(조용한 창 사유)이다.
    bid = _batch(repos)
    kids = _bulk(db, bid, 2)
    assert _auto(repos, bid, 2, sync_max=0)["deleting"] is True
    db.execute("UPDATE data_jobs SET state = 'Running' WHERE request_id IN "
               "(SELECT request_id FROM requests WHERE batch_id = :b)", {"b": bid})
    rp = repos.request_purges
    monkeypatch.setattr(RequestPurgesRepository, "_judge_locked", lambda self, *a, **k: None)
    res = rp.delete_batch_chunk(bid, artifact_base="/base", quiet_seconds=60, now=NOW)
    assert res == {"blocked": "request_recently_finished", "request_id": kids[0][0]}
    # 경합(고르기 뒤 풀림)은 막힘이 아니다 -- 다음 틱이 처음부터 고른다.
    monkeypatch.setattr(RequestPurgesRepository, "_pick_ok", lambda self, *a, **k: True)
    res = rp.delete_batch_chunk(bid, artifact_base="/base", quiet_seconds=60, now=NOW)
    assert res["deleted"] == [] and res["scan_next"] is None and "blocked" not in res
