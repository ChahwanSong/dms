"""요청 삭제 저장소(repositories/request_purges.py, 2026-10-08) -- 원자성·게이트·감사 스냅숏·아웃박스 조작.

삭제 목록이 곧 일관성의 전부다(FK 0건) -- test_request_delete_covers_every_reference_table 이 스키마 전수 열거로
"요청·잡 id 를 담는 새 테이블이 삭제 목록 밖" 을 잡는다."""
import json

import pytest

from dms.db import iso_plus, utc_now_iso
from dms.domain import DataJobState, RequestState
from dms.migrations import migrate
from dms.repositories import Repositories
from dms.repositories.request_purges import (
    BACKOFF_CAP_SECONDS, PURGE_EXEMPT_TABLES, PURGED_TABLES, SNAPSHOT_MAX_BYTES, RequestPurgesRepository)

BASE = "/cephfs/dms/artifacts"
OLD = "2026-01-01T00:00:00Z"          # 조용한 창 밖으로 밀어 둔 갱신 시각


@pytest.fixture
def repos(db):
    return Repositories(db)


def _age(db, rid):
    """요청·잡의 updated_at 을 과거로 -- 조용한 창(기본 60초) 밖. 창 자체는 test_quiet_window_* 가 본다."""
    db.execute("UPDATE requests SET updated_at = :t WHERE request_id = :r", {"t": OLD, "r": rid})
    db.execute("UPDATE data_jobs SET updated_at = :t WHERE request_id = :r", {"t": OLD, "r": rid})


def _finished(repos, *, op="scan", job_state=DataJobState.SUCCEEDED, requester="alice", resource_key="k",
              payload=None, worker_pool=None, with_job=True, age=True):
    """종단 요청 + (선택) 종단 잡 + 결과·이벤트·digest·phase_refs 를 가진 완전한 행 묶음."""
    rid = repos.requests.create(
        operation=op, requester_id=requester, actor=requester, resource_key=resource_key,
        payload=payload if payload is not None else {"storage": "s1", "target": "t", "run_as_root": True},
        priority="mid", auth_method="session")
    jid = None
    if with_job:
        repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
        plan_id = repos.data_jobs.create_plan(rid, actor="planner")
        jid = repos.data_jobs.create_job(
            rid, plan_id, operation=op, priority="mid", storage_name="s1", target="t", options={},
            tool="dscan", precondition={}, actor="planner",
            worker_pool=worker_pool if worker_pool is not None else {
                "identity": {"username": "alice", "uid": 10001, "gid": 10001, "privileged": False,
                             "groups": ["g"], "supplementary_gids": [10010]},
                "candidates": {"primary": ["dms-w1"]}, "rejections": {"dms-w2": "x"}})
        repos.data_jobs.set_phase_ref(jid, "execution", f"vcjob/dms-scan-execution-{jid[:12]}")
        repos.data_jobs.set_job_state(jid, job_state, actor="stepper")
        repos.requests.finalize_from_job(rid, job_state, actor="stepper")
        repos.scan_digests.put(jid, {"summary": {"total_files": 1}})
    else:
        repos.requests.set_state_with_result(rid, RequestState.REJECTED, reason_code="missing_policy",
                                             actor="planner")
    repos.observability.record_event(component="stepper", severity="info", event_type="x", request_id=rid)
    if age:
        _age(repos.db, rid)
    return rid, jid


def _counts(db, rid, jids, plan_ids):
    """그 요청을 가리키는 행 수 -- 7개 테이블(전이는 세 종류)."""
    def n(sql, params):
        return db.query_one(sql, params)["n"]
    out = {
        "requests": n("SELECT COUNT(*) AS n FROM requests WHERE request_id = :r", {"r": rid}),
        "results": n("SELECT COUNT(*) AS n FROM results WHERE request_id = :r", {"r": rid}),
        "plans": n("SELECT COUNT(*) AS n FROM plans WHERE request_id = :r", {"r": rid}),
        "data_jobs": n("SELECT COUNT(*) AS n FROM data_jobs WHERE request_id = :r", {"r": rid}),
        "events": n("SELECT COUNT(*) AS n FROM events WHERE request_id = :r", {"r": rid}),
        "transitions_request": n("SELECT COUNT(*) AS n FROM state_transitions WHERE entity_kind = 'request'"
                                 " AND entity_id = :r", {"r": rid}),
    }
    out["transitions_job"] = sum(n("SELECT COUNT(*) AS n FROM state_transitions WHERE entity_kind = 'data_job'"
                                   " AND entity_id = :j", {"j": j}) for j in jids)
    out["transitions_plan"] = sum(n("SELECT COUNT(*) AS n FROM state_transitions WHERE entity_kind = 'plan'"
                                    " AND entity_id = :p", {"p": p}) for p in plan_ids)
    out["digests"] = sum(n("SELECT COUNT(*) AS n FROM scan_report_digests WHERE job_id = :j", {"j": j})
                         for j in jids)
    return out


def _plan_ids(db, rid):
    return [r["plan_id"] for r in db.query("SELECT plan_id FROM plans WHERE request_id = :r", {"r": rid})]


def _delete(repos, rid, **kw):
    kw.setdefault("actor", "opadm")
    kw.setdefault("artifact_base", BASE)
    kw.setdefault("quiet_seconds", 60)
    return repos.request_purges.delete_terminal(rid, **kw)


# ---- 삭제 트랜잭션 ----

def test_delete_removes_every_reference_and_keeps_the_neighbour(repos, db):
    rid, jid = _finished(repos)
    # job_id 가 NULL 인 plan 도 request_id 로 지워진다(planner 가 plan 만 만들고 끝난 경로 -- job_id 로는 못 찾는다).
    stray_plan = repos.data_jobs.create_plan(rid, actor="planner")
    plan_ids = _plan_ids(db, rid)
    assert stray_plan in plan_ids and len(plan_ids) == 2
    # 이웃: 같은 resource_key·같은 요청자 -- 지워지면 안 된다.
    nrid, njid = _finished(repos)
    before = _counts(db, rid, [jid], plan_ids)
    neighbour = _counts(db, nrid, [njid], _plan_ids(db, nrid))
    assert all(v > 0 for v in before.values()), before

    r = _delete(repos, rid)

    assert r == {"deleted": True, "job_ids": [jid]}
    assert set(_counts(db, rid, [jid], plan_ids).values()) == {0}
    assert _counts(db, nrid, [njid], _plan_ids(db, nrid)) == neighbour


def test_delete_writes_outbox_row_with_refs_and_base(repos, db):
    rid, jid = _finished(repos)
    db.execute("UPDATE data_jobs SET artifact_uri = :u WHERE job_id = :j",
               {"u": f"file://{BASE}/{jid}/execution", "j": jid})
    _delete(repos, rid, now="2026-10-08T12:00:00Z")
    row = repos.request_purges.get(rid)
    assert row["stage"] == "k8s" and row["attempts"] == 0 and row["last_error"] is None
    assert row["artifact_base"] == BASE
    assert row["requested_by"] == "opadm"
    assert row["requested_at"] == row["next_attempt_at"] == "2026-10-08T12:00:00Z"
    assert row["jobs"] == [{"job_id": jid, "phase_refs": {"execution": f"vcjob/dms-scan-execution-{jid[:12]}"},
                            "artifact_uri": f"file://{BASE}/{jid}/execution"}]
    assert row["job_ids"] == [jid] and row["outcomes"] == {}
    assert repos.request_purges.pending_count() == 1


def test_delete_unknown_base_is_stored_as_null(repos):
    rid, _ = _finished(repos)
    _delete(repos, rid, artifact_base=None)
    assert repos.request_purges.get(rid)["artifact_base"] is None


def test_delete_audits_a_bounded_snapshot(repos, db):
    rid, jid = _finished(repos)
    db.execute("UPDATE data_jobs SET diag_logs = :d, preview_summary = :p, result_summary = :s WHERE job_id = :j",
               {"d": json.dumps({"entries": [{"log": "SECRET-LOG"}]}), "p": json.dumps({"x": "PREVIEW"}),
                "s": json.dumps({"files": 3, "path": "/SECRET/PATH"}), "j": jid})
    _delete(repos, rid)
    rows = db.query("SELECT * FROM audit_log WHERE mutation_class = 'request'")
    assert len(rows) == 1
    a = rows[0]
    assert (a["operation"], a["target_key"], a["actor"], a["after_state"]) == ("delete", rid, "opadm", None)
    snap = json.loads(a["before_state"])
    assert set(snap) == {"request", "result", "transitions", "jobs", "events_deleted", "truncated"}
    assert snap["truncated"] is False and snap["events_deleted"] == 1
    assert snap["request"]["request_id"] == rid and snap["request"]["auth_method"] == "session"
    assert snap["request"]["payload"]["run_as_root"] is True            # root 실행 여부가 감사에 남는다
    assert snap["result"]["terminal_state"] == "Succeeded"
    assert [t["to_state"] for t in snap["transitions"]] == ["Pending", "Planned", "Succeeded"]
    assert {t["actor"] for t in snap["transitions"]} == {"alice", "planner", "stepper"}
    (job,) = snap["jobs"]
    assert job["identity"] == {"uid": 10001, "gid": 10001, "privileged": False, "username": "alice"}
    assert job["phase_refs"] == {"execution": f"vcjob/dms-scan-execution-{jid[:12]}"}
    assert [t["to_state"] for t in job["transitions"]] == ["Pending", "Succeeded"]
    text = a["before_state"]
    # 크기·경로 노출: 박제 로그·미리보기·실행 요약·후보 노드는 싣지 않는다.
    for leaked in ("SECRET-LOG", "PREVIEW", "/SECRET/PATH", "candidates", "rejections", "diag_logs", "dms-w2"):
        assert leaked not in text, leaked


def test_snapshot_drops_transitions_first_when_over_the_cap(repos, db):
    rid, jid = _finished(repos)
    for i in range(29):                       # 각 엔티티 전이 30건 이내 + 큰 사유 문자열로 32KiB 를 넘긴다
        for kind, eid in (("request", rid), ("data_job", jid)):
            db.execute("""INSERT INTO state_transitions (entity_kind, entity_id, from_state, to_state, reason_code,
                              actor, at) VALUES (:k, :e, 'A', 'B', :rc, 'x', :at)""",
                       {"k": kind, "e": eid, "rc": "r" * 700, "at": OLD})
    _delete(repos, rid)
    text = db.query_one("SELECT before_state FROM audit_log WHERE target_key = :r", {"r": rid})["before_state"]
    assert len(text.encode()) <= SNAPSHOT_MAX_BYTES
    snap = json.loads(text)
    assert snap["truncated"] is True
    assert snap["jobs"][0]["transitions"] == []           # 잡 전이부터 비운다
    assert snap["request"]["payload"]["run_as_root"] is True


def test_snapshot_keeps_only_the_latest_30_transitions(repos, db):
    rid, _ = _finished(repos)
    for i in range(40):
        db.execute("""INSERT INTO state_transitions (entity_kind, entity_id, from_state, to_state, actor, at)
                      VALUES ('request', :e, 'A', :t, 'x', :at)""", {"e": rid, "t": f"S{i:02d}", "at": OLD})
    _delete(repos, rid)
    snap = json.loads(db.query_one("SELECT before_state FROM audit_log WHERE target_key = :r",
                                   {"r": rid})["before_state"])
    assert len(snap["transitions"]) == 30 and snap["truncated"] is True
    assert snap["transitions"][-1]["to_state"] == "S39"           # 최신(사람 행위가 있는 뒤쪽)을 남긴다


def test_snapshot_folds_a_huge_payload_to_a_skeleton(repos, db):
    rid, jid = _finished(repos, payload={"storage": "s1", "target": "t" * 40000, "run_as_root": True,
                                         "owner_username": "bob"})
    assert _delete(repos, rid)["deleted"] is True                  # 감사가 삭제를 막지 않는다
    text = db.query_one("SELECT before_state FROM audit_log WHERE target_key = :r", {"r": rid})["before_state"]
    assert len(text.encode()) <= SNAPSHOT_MAX_BYTES
    snap = json.loads(text)
    assert snap["truncated"] is True
    assert snap["request"]["run_as_root"] is True and snap["request"]["owner_username"] == "bob"
    assert snap["jobs"][0]["job_id"] == jid


def test_corrupt_json_columns_do_not_block_delete(repos, db):
    rid, jid = _finished(repos)
    db.execute("UPDATE data_jobs SET phase_refs = '{broken', worker_pool = 'nope' WHERE job_id = :j", {"j": jid})
    db.execute("UPDATE requests SET payload = '[[[' WHERE request_id = :r", {"r": rid})
    assert _delete(repos, rid) == {"deleted": True, "job_ids": [jid]}
    assert repos.request_purges.get(rid)["jobs"][0]["phase_refs"] is None    # 라벨 스윕이 남은 객체를 찾는다
    snap = json.loads(db.query_one("SELECT before_state FROM audit_log WHERE target_key = :r",
                                   {"r": rid})["before_state"])
    assert snap["request"]["payload"] == {"unparsed": "[[["}                 # 깨진 원문도 증거로 남긴다
    assert snap["jobs"][0]["identity"] is None


def test_request_without_jobs_is_deletable(repos, db):
    rid, _ = _finished(repos, with_job=False)
    assert _delete(repos, rid) == {"deleted": True, "job_ids": []}
    assert repos.request_purges.get(rid)["jobs"] == []
    assert db.query_one("SELECT COUNT(*) AS n FROM results WHERE request_id = :r", {"r": rid})["n"] == 0


def test_audit_failure_rolls_back_everything(repos, db, monkeypatch):
    rid, jid = _finished(repos)
    plan_ids = _plan_ids(db, rid)
    before = _counts(db, rid, [jid], plan_ids)
    real = db.execute

    def boom(sql, params=None):
        if sql.lstrip().startswith("INSERT INTO audit_log"):
            raise RuntimeError("audit insert failed")
        return real(sql, params)
    monkeypatch.setattr(db, "execute", boom)
    with pytest.raises(RuntimeError):
        _delete(repos, rid)
    monkeypatch.undo()
    assert _counts(db, rid, [jid], plan_ids) == before
    assert repos.request_purges.pending_count() == 0


def test_cas_loss_rolls_back_and_reports_not_deletable(repos, db, monkeypatch):
    # sqlite 에는 행 잠금이 없다 -- 판정 뒤 CAS 전에 다른 쪽이 상태를 바꾼 경합을 같은 커넥션의 UPDATE 로 흉내 낸다.
    rid, jid = _finished(repos)
    plan_ids = _plan_ids(db, rid)
    before = _counts(db, rid, [jid], plan_ids)
    real = db.execute_count

    def racing(sql, params=None):
        if sql.lstrip().startswith("DELETE FROM requests"):
            real("UPDATE requests SET state = 'Running' WHERE request_id = :r", {"r": rid})
        return real(sql, params)
    monkeypatch.setattr(db, "execute_count", racing)
    assert _delete(repos, rid) == {"deleted": False, "reason": "request_not_deletable"}
    monkeypatch.undo()
    assert _counts(db, rid, [jid], plan_ids) == before
    assert repos.requests.get(rid)["state"] == "Succeeded"          # 흉내 낸 UPDATE 도 함께 롤백
    assert repos.request_purges.pending_count() == 0
    assert db.query_one("SELECT COUNT(*) AS n FROM audit_log WHERE mutation_class = 'request'")["n"] == 0


def test_redelete_after_restore_refreshes_the_outbox_row(repos, db):
    # 지운 요청이 DB 복원으로 되살아나(정리 루프는 purge_target_still_present 로 멈춤) 관리자가 다시 지우는 경로 --
    # 아웃박스 PK 충돌로 500 이 되지 않고, 옛 행만 아는 잡의 열쇠를 잃지 않으며, 단계·실패 이력은 처음부터다.
    rid, jid = _finished(repos)
    _delete(repos, rid, now="2026-10-08T12:00:00Z")
    repos.request_purges.fail(rid, reason_code="purge_target_still_present", interval=15)
    db.execute("""INSERT INTO requests (request_id, commit_order, operation, requester_id, actor, resource_key,
                      payload, state, created_at, updated_at) VALUES (:r, 999, 'scan', 'a', 'a', 'k', '{}',
                      'Failed', :at, :at)""", {"r": rid, "at": OLD})
    assert _delete(repos, rid, now="2026-10-08T13:00:00Z", actor="opadm2") == {"deleted": True, "job_ids": []}
    row = repos.request_purges.get(rid)
    assert (row["stage"], row["attempts"], row["last_error"]) == ("k8s", 0, None)
    assert (row["requested_by"], row["requested_at"]) == ("opadm2", "2026-10-08T13:00:00Z")
    assert row["job_ids"] == [jid]                                       # 옛 행의 잡 열쇠가 남는다
    assert repos.request_purges.pending_count() == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM audit_log WHERE target_key = :r", {"r": rid})["n"] == 2


def test_second_delete_is_not_found(repos):
    rid, _ = _finished(repos)
    assert _delete(repos, rid)["deleted"] is True
    assert _delete(repos, rid) == {"deleted": False, "reason": "request_not_found"}


# ---- 게이트 ----

def test_gate_not_found(repos):
    assert _delete(repos, "f" * 32) == {"deleted": False, "reason": "request_not_found"}


@pytest.mark.parametrize("batch_id", ["b1", ""])
def test_gate_batch_child_even_with_empty_string_batch_id(repos, db, batch_id):
    rid, _ = _finished(repos)
    db.execute("UPDATE requests SET batch_id = :b WHERE request_id = :r", {"b": batch_id, "r": rid})
    assert _delete(repos, rid) == {"deleted": False, "reason": "batch_child_not_deletable"}
    assert repos.requests.get(rid) is not None


def test_gate_batch_items_reference_without_batch_id(repos, db):
    rid, _ = _finished(repos)
    db.execute("""INSERT INTO batch_items (batch_id, seq, payload, status, request_id, created_at, updated_at)
                  VALUES ('b1', 1, '{}', 'Succeeded', :r, :at, :at)""", {"r": rid, "at": OLD})
    assert _delete(repos, rid) == {"deleted": False, "reason": "batch_child_not_deletable"}


def test_gate_batch_child_wins_over_nonterminal(repos, db):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice", resource_key="k",
                                payload={}, priority="mid", batch_id="b1")
    assert _delete(repos, rid)["reason"] == "batch_child_not_deletable"


@pytest.mark.parametrize("state", [RequestState.PENDING, RequestState.PLANNED, RequestState.RUNNING])
def test_gate_nonterminal_request(repos, state):
    rid = repos.requests.create(operation="rm", requester_id="alice", actor="alice", resource_key="k",
                                payload={}, priority="mid")
    if state != RequestState.PENDING:
        repos.requests.set_state(rid, state, actor="planner")
    _age(repos.db, rid)
    assert _delete(repos, rid) == {"deleted": False, "reason": "request_not_deletable"}


def test_gate_confirm_pending_request(repos):
    rid = repos.requests.create(operation="rm", requester_id="alice", actor="alice", resource_key="k",
                                payload={}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="rm", priority="mid", options={}, tool="drm",
                                     worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    _age(repos.db, rid)
    assert _delete(repos, rid)["reason"] == "request_not_deletable"


@pytest.mark.parametrize("job_state", ["Executing", "Pending", "SomethingUnknown"])
def test_gate_terminal_request_with_active_job(repos, db, job_state):
    rid, jid = _finished(repos)
    db.execute("UPDATE data_jobs SET state = :s WHERE job_id = :j", {"s": job_state, "j": jid})
    assert _delete(repos, rid) == {"deleted": False, "reason": "request_job_active"}
    assert repos.data_jobs.get_job(jid) is not None


def test_quiet_window_on_request_and_on_job(repos, db):
    now = "2026-10-08T12:00:00Z"
    rid, jid = _finished(repos)
    db.execute("UPDATE requests SET updated_at = :t WHERE request_id = :r", {"t": iso_plus(now, -30), "r": rid})
    assert _delete(repos, rid, now=now)["reason"] == "request_recently_finished"
    db.execute("UPDATE requests SET updated_at = :t WHERE request_id = :r", {"t": iso_plus(now, -61), "r": rid})
    db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j", {"t": iso_plus(now, -59), "j": jid})
    assert _delete(repos, rid, now=now)["reason"] == "request_recently_finished"     # 잡의 갱신도 창에 든다
    db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j", {"t": iso_plus(now, -60), "j": jid})
    assert _delete(repos, rid, now=now)["deleted"] is True                            # 경계 = 창 밖


def test_quiet_zero_allows_just_finished(repos):
    rid, _ = _finished(repos, age=False)
    assert _delete(repos, rid, quiet_seconds=0)["deleted"] is True


# ---- 아웃박스 조작(컨트롤러 전용) ----

def _queued(repos, n=1, *, now="2026-10-08T12:00:00Z"):
    out = []
    for _ in range(n):
        rid, jid = _finished(repos)
        _delete(repos, rid, now=now)
        out.append((rid, jid))
    return out


def test_due_respects_next_attempt_and_order(repos):
    (r1, _), (r2, _) = _queued(repos, 2)
    p = repos.request_purges
    assert [r["request_id"] for r in p.due("2026-10-08T12:00:00Z")] == sorted([r1, r2])  # 같은 시각 = id 순
    p.fail(r1, reason_code="purge_k8s_failed", interval=15, now="2026-10-08T12:00:00Z")
    assert [r["request_id"] for r in p.due("2026-10-08T12:00:10Z")] == [r2]
    assert {r["request_id"] for r in p.due("2026-10-08T12:00:15Z")} == {r1, r2}
    assert len(p.due("2026-10-08T12:00:15Z", limit=1)) == 1


def test_due_is_in_retry_order_so_waiting_rows_go_to_the_back(repos, db):
    # 2026-10-09 검증 지적: 삭제 순이면 오래 기다리는 행(k8s 단계 Terminating 대기)이 상한을 채워 새 삭제를 굶긴다.
    (old_rid, _), = _queued(repos, now="2026-10-08T11:00:00Z")
    (new_rid, _), = _queued(repos, now="2026-10-08T12:00:00Z")
    p = repos.request_purges
    p.defer(old_rid, seconds=15, now="2026-10-08T12:00:00Z")           # 방금 기다린 오래된 행
    assert [r["request_id"] for r in p.due("2026-10-08T12:00:15Z", limit=1)] == [new_rid]
    assert [r["request_id"] for r in p.due("2026-10-08T12:00:15Z")] == [new_rid, old_rid]


def test_fail_backs_off_exponentially_up_to_the_cap_and_reports_change(repos):
    ((rid, _),) = _queued(repos)
    p = repos.request_purges
    now = "2026-10-08T12:00:00Z"
    assert p.fail(rid, reason_code="purge_k8s_failed", interval=15, now=now) is True
    assert p.get(rid)["next_attempt_at"] == iso_plus(now, 15)
    assert p.fail(rid, reason_code="purge_k8s_failed", interval=15, now=now) is False   # 같은 사유 = 이벤트 없음
    assert p.get(rid)["next_attempt_at"] == iso_plus(now, 30)
    for _ in range(10):
        p.fail(rid, reason_code="purge_k8s_failed", interval=15, now=now)
    row = p.get(rid)
    assert row["attempts"] == 12 and row["next_attempt_at"] == iso_plus(now, BACKOFF_CAP_SECONDS)
    assert p.fail(rid, reason_code="purge_no_node", interval=15, now=now) is True
    assert p.fail("0" * 32, reason_code="purge_failed", interval=15) is False           # 없는 행


def test_advance_resets_failure_and_keeps_outcomes_unless_given(repos):
    ((rid, jid),) = _queued(repos)
    p = repos.request_purges
    p.fail(rid, reason_code="purge_k8s_failed", interval=15)
    assert p.advance(rid, "files", now="2026-10-08T13:00:00Z") is True
    row = p.get(rid)
    assert (row["stage"], row["attempts"], row["last_error"], row["next_attempt_at"]) == (
        "files", 0, None, "2026-10-08T13:00:00Z")
    p.advance(rid, "purging", outcomes={jid: "deleted"})
    p.advance(rid, "purging")
    assert p.get(rid)["outcomes"] == {jid: "deleted"}
    assert [r["request_id"] for r in p.in_stage("purging")] == [rid]
    with pytest.raises(ValueError):
        p.advance(rid, "done")


def test_defer_keeps_attempts_and_surfaces_or_clears_the_delay(repos):
    ((rid, _),) = _queued(repos)
    p = repos.request_purges
    p.fail(rid, reason_code="purge_k8s_failed", interval=15)
    now = "2026-10-08T12:00:00Z"
    assert p.defer(rid, seconds=15, now=now) is True                   # 실패가 풀려 대기만 남음 = 지연 표면화 해제
    row = p.get(rid)
    assert (row["attempts"], row["last_error"], row["next_attempt_at"]) == (1, None, iso_plus(now, 15))
    assert p.defer(rid, seconds=15, reason_code="purge_waiting_pods", now=now) is True
    assert p.defer(rid, seconds=15, reason_code="purge_waiting_pods", now=now) is False
    assert p.get(rid)["last_error"] == "purge_waiting_pods"


def test_target_still_present(repos, db):
    ((rid, jid),) = _queued(repos)
    p = repos.request_purges
    assert p.target_still_present(rid, [jid]) is False
    db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, options, priority, state, created_at,
                      updated_at) VALUES (:j, 'other', 'scan', '{}', 'mid', 'Succeeded', :at, :at)""",
               {"j": jid, "at": OLD})
    assert p.target_still_present(rid, [jid]) is True
    db.execute("DELETE FROM data_jobs WHERE job_id = :j", {"j": jid})
    db.execute("""INSERT INTO requests (request_id, commit_order, operation, requester_id, actor, resource_key,
                      payload, state, created_at, updated_at) VALUES (:r, 999, 'scan', 'a', 'a', 'k', '{}',
                      'Succeeded', :at, :at)""", {"r": rid, "at": OLD})
    assert p.target_still_present(rid, []) is True


def test_finish_scrubs_late_rows_and_removes_the_outbox_row(repos, db):
    ((rid, jid),) = _queued(repos)
    nrid, njid = _finished(repos)
    # 늦은 INSERT: 종단 가드 스킵 이벤트·사용량 digest(잡 없이 직접)·늦은 전이.
    repos.observability.record_event(component="stepper", severity="warning", event_type="late", request_id=rid)
    db.execute("INSERT INTO scan_report_digests (job_id, digest, created_at) VALUES (:j, '{}', :at)",
               {"j": jid, "at": OLD})
    for kind, eid in (("data_job", jid), ("request", rid)):
        db.execute("""INSERT INTO state_transitions (entity_kind, entity_id, from_state, to_state, actor, at)
                      VALUES (:k, :e, 'A', 'B', 'stepper', :at)""", {"k": kind, "e": eid, "at": OLD})
    # 삭제와 경합한 종단 전이가 남길 수 있던 결과 행(백스톱 -- 지표 plan_rejected 가 results 만 읽는다)
    db.execute("""INSERT INTO results (request_id, terminal_state, reason_code, completed_at)
                  VALUES (:r, 'Rejected', 'missing_policy', :at)""", {"r": rid, "at": OLD})
    neighbour = _counts(db, nrid, [njid], _plan_ids(db, nrid))
    assert repos.request_purges.finish(rid, [jid]) is True
    assert set(_counts(db, rid, [jid], []).values()) == {0}
    assert repos.request_purges.get(rid) is None
    assert _counts(db, nrid, [njid], _plan_ids(db, nrid)) == neighbour
    assert repos.request_purges.finish(rid, [jid]) is False                  # 이미 끝남


def test_finish_does_nothing_when_the_target_came_back(repos, db):
    ((rid, jid),) = _queued(repos)
    db.execute("""INSERT INTO requests (request_id, commit_order, operation, requester_id, actor, resource_key,
                      payload, state, created_at, updated_at) VALUES (:r, 999, 'scan', 'a', 'a', 'k', '{}',
                      'Succeeded', :at, :at)""", {"r": rid, "at": OLD})
    repos.observability.record_event(component="x", severity="info", event_type="live", request_id=rid)
    assert repos.request_purges.finish(rid, [jid]) is False
    assert repos.request_purges.get(rid) is not None
    assert db.query_one("SELECT COUNT(*) AS n FROM events WHERE request_id = :r", {"r": rid})["n"] == 1


def test_status_counts_pending_and_stalled_oldest_first(repos):
    p = repos.request_purges
    assert p.status() == {"pending": 0, "stalled": 0, "oldest_requested_at": None, "items": []}
    (r1, _), = _queued(repos, now="2026-10-08T11:00:00Z")
    (r2, _), = _queued(repos, now="2026-10-08T12:00:00Z")
    p.fail(r2, reason_code="purge_no_node", interval=15)
    s = p.status()
    assert (s["pending"], s["stalled"], s["oldest_requested_at"]) == (2, 1, "2026-10-08T11:00:00Z")
    assert [i["request_id"] for i in s["items"]] == [r1, r2]
    assert set(s["items"][0]) == {"request_id", "stage", "attempts", "last_error", "requested_at",
                                  "requested_by", "next_attempt_at"}
    assert s["items"][1]["last_error"] == "purge_no_node"
    assert len(p.status(limit=1)["items"]) == 1


def test_hydrate_marks_unreadable_jobs_as_unknown(repos, db):
    ((rid, _),) = _queued(repos)
    db.execute("UPDATE request_purges SET jobs = 'not json', outcomes = '[1]' WHERE request_id = :r", {"r": rid})
    row = repos.request_purges.get(rid)
    assert row["jobs"] is None and row["job_ids"] is None and row["outcomes"] == {}


# ---- 삭제와 경합한 상태 쓰기(2026-10-09 검증 지적) ----
# 잠금 없이 상태를 읽은 쓰기가 삭제 커밋 뒤에 UPDATE 하면 0행인데도 전이·results INSERT 가 커밋돼 지운 요청의 고아 행이
# 영구히 남았다. 이제 읽기는 PG 행 잠금, UPDATE 는 영향 행 수 확인 -- 0행이면 KeyError 로 트랜잭션 전체가 롤백된다.

class _VanishAfterRead:
    """쓰기 트랜잭션 **안에서** 상태를 읽은 직후 그 행을 지우는 DB 프록시 -- 읽기와 UPDATE 사이에 끼어든 삭제 흉내
    (PG 에선 잠금 없이 읽은 뒤 삭제가 커밋된 창). 트랜잭션 밖 읽기(finalize_from_job 의 멱등 확인)는 건드리지 않는다
    -- 거기서 지우면 옛 코드도 「행 없음」 KeyError 로 통과해 회귀 그물이 아니다."""

    def __init__(self, db, table, key, value):
        self._db, self._table, self._key, self._value = db, table, key, value
        self.armed = True

    def __getattr__(self, name):
        return getattr(self._db, name)

    def query_one(self, sql, params=None):
        row = self._db.query_one(sql, params)
        if (self.armed and self._db._txn_depth > 0 and sql.lstrip().startswith("SELECT state")
                and f"FROM {self._table}" in sql):
            self.armed = False
            self._db.execute(f"DELETE FROM {self._table} WHERE {self._key} = :v", {"v": self._value})
        return row


def _running(repos, db):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice", resource_key="k-race",
                                payload={"storage": "s1", "target": "t"}, priority="mid", auth_method="session")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid", storage_name="s1", target="t",
                                     options={}, tool="dscan", precondition={}, actor="planner",
                                     worker_pool={"identity": {"uid": 1, "gid": 1}})
    repos.data_jobs.set_job_state(jid, DataJobState.RUNNING, actor="stepper")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="stepper")
    return rid, jid


def _n(db, sql, params):
    return db.query_one(sql, params)["n"]


def test_request_state_write_racing_a_delete_commits_nothing(repos, db):
    from dms.repositories.requests import RequestsRepository
    rid, _ = _running(repos, db)
    before = _n(db, "SELECT COUNT(*) AS n FROM state_transitions WHERE entity_kind = 'request' AND entity_id = :r",
                {"r": rid})
    proxy = _VanishAfterRead(db, "requests", "request_id", rid)
    with pytest.raises(KeyError):
        RequestsRepository(proxy).finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    assert proxy.armed is False                                   # 트랜잭션 안 읽기 뒤에 끼어들었다
    # 롤백 -- 결과 행도 전이도 남지 않는다(예전: results 1행 + 전이 1행이 커밋돼 영구 고아)
    assert _n(db, "SELECT COUNT(*) AS n FROM results WHERE request_id = :r", {"r": rid}) == 0
    assert _n(db, "SELECT COUNT(*) AS n FROM state_transitions WHERE entity_kind = 'request' AND entity_id = :r",
              {"r": rid}) == before
    proxy = _VanishAfterRead(db, "requests", "request_id", rid)
    with pytest.raises(KeyError):
        RequestsRepository(proxy).set_state_with_result(rid, RequestState.REJECTED, reason_code="missing_policy", actor="planner")
    assert _n(db, "SELECT COUNT(*) AS n FROM results WHERE request_id = :r", {"r": rid}) == 0


def test_job_state_write_racing_a_delete_commits_nothing(repos, db):
    from dms.repositories.data_jobs import DataJobsRepository
    _, jid = _running(repos, db)
    before = _n(db, "SELECT COUNT(*) AS n FROM state_transitions WHERE entity_kind = 'data_job' AND entity_id = :j",
                {"j": jid})
    proxy = _VanishAfterRead(db, "data_jobs", "job_id", jid)
    with pytest.raises(KeyError):
        DataJobsRepository(proxy).set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    assert proxy.armed is False
    assert _n(db, "SELECT COUNT(*) AS n FROM state_transitions WHERE entity_kind = 'data_job' AND entity_id = :j",
              {"j": jid}) == before


class _PgSpy:
    """dialect 만 postgresql 로 보이는 sqlite 프록시 -- 문장을 기록하고 FOR UPDATE 를 떼어 실행한다."""
    dialect = "postgresql"

    def __init__(self, db):
        self._db = db
        self.sql = []

    def __getattr__(self, name):
        return getattr(self._db, name)

    def query_one(self, sql, params=None):
        self.sql.append(sql)
        return self._db.query_one(sql.replace(" FOR UPDATE", ""), params)


def test_state_writes_lock_the_row_on_postgres(repos, db):
    from dms.repositories.data_jobs import DataJobsRepository
    from dms.repositories.requests import RequestsRepository
    rid, jid = _running(repos, db)
    spy = _PgSpy(db)
    DataJobsRepository(spy).set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    RequestsRepository(spy).finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    reads = [q for q in spy.sql if q.lstrip().startswith("SELECT state")]
    assert any("FROM data_jobs" in q and q.rstrip().endswith("FOR UPDATE") for q in reads), reads
    assert any("FROM requests" in q and q.rstrip().endswith("FOR UPDATE") for q in reads), reads


# ---- §1 불변식: request_id/job_id 를 담는 테이블은 전부 삭제 목록이거나 명시 허용 목록이다 ----

def test_request_delete_covers_every_reference_table(tmp_path):
    from dms.db import Database
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    tables = [r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
              if not r["name"].startswith("sqlite_")]
    referencing = set()
    for t in tables:
        cols = {c["name"] for c in db.query(f"PRAGMA table_info({t})")}
        if cols & {"request_id", "job_id", "entity_id"}:
            referencing.add(t)
    # 새 테이블이 요청·잡 id 를 담으면 여기서 빨개진다 -- 삭제 목록(PURGED_TABLES + delete_terminal·finish 의 DELETE)에
    # 넣거나, 지우지 않는 이유를 PURGE_EXEMPT_TABLES 에 적어라. FK 가 0건이라 빠뜨리면 조용히 고아가 된다.
    assert referencing - set(PURGED_TABLES) - set(PURGE_EXEMPT_TABLES) == set()
    assert set(PURGED_TABLES) <= referencing | {"requests"}
    assert not set(PURGED_TABLES) & set(PURGE_EXEMPT_TABLES)


def test_every_purged_table_has_a_delete_statement():
    # PURGED_TABLES 가 장식이 되지 않게 -- delete_terminal 소스에 테이블마다 DELETE 문이 실제로 있다.
    import inspect
    src = inspect.getsource(RequestPurgesRepository.delete_terminal)
    for t in PURGED_TABLES:
        assert f"DELETE FROM {t} " in src, t
