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


def test_present_targets_matches_target_still_present_per_row(repos, db):
    # 묶음판(정리 루프의 전역 단계가 틱마다 대기열 전부를 거른다 -- 2026-10-11 검증 지적: 행마다 두 문장이면 큰 배치
    # 삭제의 대기열에서 틱이 행 수에 비례해 느려졌다). 행마다의 판정과 같아야 한다.
    rows = [{"request_id": rid, "job_ids": [jid]} for rid, jid in _queued(repos, n=4)]
    p = repos.request_purges
    assert p.present_targets(rows) == set()
    # 0: 요청이 되살아남, 1: 잡 하나가 되살아남, 2: 잡 목록 모름(깨진 행 -- 요청만 본다), 3: 그대로
    db.execute("""INSERT INTO requests (request_id, commit_order, operation, requester_id, actor, resource_key,
                      payload, state, created_at, updated_at) VALUES (:r, 999, 'scan', 'a', 'a', 'k', '{}',
                      'Succeeded', :at, :at)""", {"r": rows[0]["request_id"], "at": OLD})
    db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, options, priority, state, created_at,
                      updated_at) VALUES (:j, 'other', 'scan', '{}', 'mid', 'Succeeded', :at, :at)""",
               {"j": rows[1]["job_ids"][0], "at": OLD})
    rows[2]["job_ids"] = None
    expected = {r["request_id"] for r in rows if p.target_still_present(r["request_id"], r["job_ids"])}
    assert p.present_targets(rows) == expected == {rows[0]["request_id"], rows[1]["request_id"]}
    assert p.present_targets([]) == set()


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
    # PURGED_TABLES 가 장식이 되지 않게 -- 공용 몸통(_purge_locked: delete_terminal·delete_batch 가 같이 쓴다) 소스에
    # 테이블마다 DELETE 문이 실제로 있고, 두 진입점이 그 몸통을 부른다. 배치 단위 삭제는 배치 항목·배치 행도 지운다.
    import inspect
    src = inspect.getsource(RequestPurgesRepository._purge_locked)
    for t in PURGED_TABLES:
        assert f"DELETE FROM {t} " in src, t
    for entry in (RequestPurgesRepository.delete_terminal, RequestPurgesRepository.delete_batch):
        assert "self._purge_locked(" in inspect.getsource(entry), entry.__name__
    batch_src = inspect.getsource(RequestPurgesRepository.delete_batch)
    assert "DELETE FROM batch_items " in batch_src and "DELETE FROM batches " in batch_src


# ---- 배치 단위 삭제(delete_batch, 2026-10-10) ----

import uuid                                                    # noqa: E402

from dms.repositories.request_purges import _TERMINAL_BATCH  # noqa: E402


def _scan_batch(repos, *, n_items=2, status="Completed", name="성장 모니터링", note="메모"):
    return repos.batches.create(
        operation="scan", requester_id="admin", actor="admin", max_concurrency=2, options={"x": 1}, note=note,
        items=[{"storage": "s1", "target": f"t{i}"} for i in range(n_items)], status=status, name=name,
        auth_method="session", owner_username="alice")


def _batch_child(repos, bid, *, seq=None, target="t", job_state=DataJobState.SUCCEEDED, age=True):
    """배치 자식 1개(종단 요청 + 종단 잡 + 결과·이벤트·digest·phase_refs). seq 를 주면 그 항목이 이 자식을 가리킨다
    (재스캔 뒤의 최신 자식). 주지 않으면 어떤 항목도 가리키지 않는 옛 자식(재스캔 이력)이다."""
    rid = repos.requests.create(
        operation="scan", requester_id="admin", actor="admin", resource_key=f"data.scan:s1:{target}",
        payload={"storage": "s1", "target": target, "run_as_root": True, "owner_username": "alice"},
        priority="mid", auth_method="session", batch_id=bid)
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1", target=target, options={},
        tool="dscan", precondition={}, actor="planner",
        worker_pool={"identity": {"username": "alice", "uid": 0, "gid": 0, "privileged": True}})
    repos.data_jobs.set_phase_ref(jid, "execution", f"vcjob/dms-scan-execution-{jid[:12]}")
    repos.data_jobs.set_job_state(jid, job_state, actor="stepper")
    repos.requests.finalize_from_job(rid, job_state, actor="stepper")
    repos.scan_digests.put(jid, {"summary": {"total_files": 1}})
    repos.observability.record_event(component="stepper", severity="info", event_type="x", request_id=rid)
    if seq is not None:
        repos.db.execute("UPDATE batch_items SET status = 'Succeeded', request_id = :r WHERE batch_id = :b AND seq = :s",
                         {"r": rid, "b": bid, "s": seq})
    if age:
        _age(repos.db, rid)
    return rid, jid


def _rescanned_batch(repos, **kw):
    """종단 scan 배치(항목 2개) + 재스캔 이력: 자식 4개 중 2개(옛 회차)는 어떤 항목도 가리키지 않는다(재실행이 항목의
    request_id 를 NULL 로 되돌린 뒤 새 자식을 묶는다 -- 옛 자식은 batch_id 로만 찾는다)."""
    bid = _scan_batch(repos, **kw)
    old = [_batch_child(repos, bid, target=f"t{i}") for i in range(2)]
    new = [_batch_child(repos, bid, seq=i, target=f"t{i}") for i in range(2)]
    return bid, old + new


def _child_count(db, bid):
    return db.query_one("SELECT COUNT(*) AS n FROM requests WHERE batch_id = :b", {"b": bid})["n"]


def _delete_batch(repos, bid, **kw):
    kw.setdefault("actor", "opadm")
    kw.setdefault("artifact_base", BASE)
    kw.setdefault("quiet_seconds", 60)
    kw.setdefault("max_children", 1000)
    if "expected_request_count" not in kw:
        kw["expected_request_count"] = _child_count(repos.db, bid)
    return repos.request_purges.delete_batch(bid, **kw)


def _batch_rows(db, bid):
    one = lambda sql: db.query_one(sql, {"b": bid})["n"]      # noqa: E731
    return {"batches": one("SELECT COUNT(*) AS n FROM batches WHERE batch_id = :b"),
            "batch_items": one("SELECT COUNT(*) AS n FROM batch_items WHERE batch_id = :b"),
            "requests": one("SELECT COUNT(*) AS n FROM requests WHERE batch_id = :b")}


def _state_of(db, bid, children):
    """배치 + 자식 전부의 행 수 -- 「무변화」 단언용."""
    return {"batch": _batch_rows(db, bid),
            "children": {rid: _counts(db, rid, [jid], _plan_ids(db, rid)) for rid, jid in children},
            "outbox": db.query_one("SELECT COUNT(*) AS n FROM request_purges")["n"],
            "audit": db.query_one("SELECT COUNT(*) AS n FROM audit_log WHERE operation = 'delete'")["n"]}


def test_batch_delete_removes_every_child_and_the_batch_and_keeps_neighbours(repos, db):
    bid, children = _rescanned_batch(repos)
    plan_ids = {rid: _plan_ids(db, rid) for rid, _ in children}
    assert all(set(_counts(db, rid, [jid], plan_ids[rid]).values()) != {0} for rid, jid in children)
    # 이웃: 다른 배치의 자식과 단건 요청 -- 지워지면 안 된다.
    nbid = _scan_batch(repos, n_items=1, name="이웃")
    nchild = _batch_child(repos, nbid, seq=0, target="t0")
    single = _finished(repos)
    neighbour = {"batch": _batch_rows(db, nbid),
                 "child": _counts(db, nchild[0], [nchild[1]], _plan_ids(db, nchild[0])),
                 "single": _counts(db, single[0], [single[1]], _plan_ids(db, single[0]))}

    r = _delete_batch(repos, bid)

    assert r == {"deleted": True, "request_ids": sorted(rid for rid, _ in children),
                 "job_ids": [jid for _, jid in sorted(children)], "dangling": False}
    for rid, jid in children:
        assert set(_counts(db, rid, [jid], plan_ids[rid]).values()) == {0}, rid
    assert _batch_rows(db, bid) == {"batches": 0, "batch_items": 0, "requests": 0}
    assert {"batch": _batch_rows(db, nbid),
            "child": _counts(db, nchild[0], [nchild[1]], _plan_ids(db, nchild[0])),
            "single": _counts(db, single[0], [single[1]], _plan_ids(db, single[0]))} == neighbour


def test_batch_delete_writes_outbox_per_child_and_audits_children_and_batch(repos, db):
    bid, children = _rescanned_batch(repos)
    batch_row = repos.batches.get(bid)
    _delete_batch(repos, bid, now="2026-10-10T12:00:00Z")
    for rid, jid in children:
        row = repos.request_purges.get(rid)
        assert (row["stage"], row["artifact_base"], row["requested_by"]) == ("k8s", BASE, "opadm")
        assert row["jobs"] == [{"job_id": jid, "phase_refs": {"execution": f"vcjob/dms-scan-execution-{jid[:12]}"},
                                "artifact_uri": None}]
    assert repos.request_purges.pending_count() == 4
    child_audits = db.query("SELECT * FROM audit_log WHERE mutation_class = 'request' ORDER BY target_key")
    assert [(a["operation"], a["target_key"], a["actor"]) for a in child_audits] == [
        ("delete", rid, "opadm") for rid in sorted(rid for rid, _ in children)]
    for a in child_audits:
        snap = json.loads(a["before_state"])
        assert snap["request"]["batch_id"] == bid                  # 자식 감사가 소속 배치를 스스로 말한다
        assert snap["request"]["payload"]["run_as_root"] is True
    (ba,) = db.query("SELECT * FROM audit_log WHERE mutation_class = 'batch'")
    assert (ba["operation"], ba["target_key"], ba["actor"], ba["after_state"], ba["at"]) == (
        "delete", bid, "opadm", None, "2026-10-10T12:00:00Z")
    snap = json.loads(ba["before_state"])
    assert set(snap) == {"batch", "dangling", "items", "item_count", "items_kept", "child_count", "child_request_ids",
                         "truncated"}
    assert (snap["item_count"], snap["items_kept"]) == (2, 2)
    assert set(snap["batch"]) == {
        "batch_id", "name", "note", "operation", "status", "requester_id", "actor", "owner_username",
        "auth_method", "options", "max_concurrency", "priority", "node_count", "procs_per_node", "item_count",
        "succeeded_count", "failed_count", "preview_round", "created_at", "updated_at"}
    assert (snap["batch"]["name"], snap["batch"]["note"], snap["batch"]["options"]) == ("성장 모니터링", "메모", {"x": 1})
    assert snap["batch"]["owner_username"] == "alice" and snap["batch"]["auth_method"] == "session"
    assert snap["batch"]["updated_at"] == batch_row["updated_at"]
    assert snap["dangling"] is False and snap["truncated"] is False
    assert [set(it) for it in snap["items"]] == [{"seq", "status", "request_id", "reason_code", "payload"}] * 2
    assert [it["payload"] for it in snap["items"]] == [{"storage": "s1", "target": "t0"},
                                                       {"storage": "s1", "target": "t1"}]
    assert snap["child_count"] == 4 and sorted(snap["child_request_ids"]) == sorted(rid for rid, _ in children)


def test_single_delete_snapshot_has_null_batch_id(repos, db):
    rid, _ = _finished(repos)
    _delete(repos, rid)
    snap = json.loads(db.query_one("SELECT before_state FROM audit_log WHERE target_key = :r",
                                   {"r": rid})["before_state"])
    assert "batch_id" in snap["request"] and snap["request"]["batch_id"] is None


@pytest.mark.parametrize("status", ["Previewing", "PreviewReady", "Running", "Weird"])
def test_batch_delete_refuses_a_batch_that_is_not_terminal(repos, db, status):
    bid, children = _rescanned_batch(repos)
    db.execute("UPDATE batches SET status = :s WHERE batch_id = :b", {"s": status, "b": bid})
    before = _state_of(db, bid, children)
    assert _delete_batch(repos, bid) == {"deleted": False, "reason": "batch_not_deletable", "request_id": None}
    assert _state_of(db, bid, children) == before


def test_batch_delete_refuses_a_cancelled_batch_with_a_pending_child(repos, db):
    # probe 2 모양: cancel 경합으로 배치는 Cancelled 인데 자식은 Pending -- 배치 상태만 보면 살아 있는 자식을 지운다.
    bid, children = _rescanned_batch(repos, status="Cancelled")
    pending = repos.requests.create(operation="scan", requester_id="admin", actor="admin", resource_key="k-p",
                                    payload={"storage": "s1", "target": "tp"}, priority="mid", batch_id=bid)
    _age(db, pending)
    before = _state_of(db, bid, children)
    assert _delete_batch(repos, bid) == {"deleted": False, "reason": "request_not_deletable", "request_id": pending}
    assert _state_of(db, bid, children) == before and repos.requests.get(pending) is not None


def test_batch_delete_refuses_a_terminal_child_with_an_active_job(repos, db):
    bid, children = _rescanned_batch(repos)
    rid, jid = children[2]
    db.execute("UPDATE data_jobs SET state = 'Executing' WHERE job_id = :j", {"j": jid})
    before = _state_of(db, bid, children)
    assert _delete_batch(repos, bid) == {"deleted": False, "reason": "request_job_active", "request_id": rid}
    assert _state_of(db, bid, children) == before


def test_batch_delete_quiet_window_applies_to_children_and_jobs_only(repos, db):
    now = "2026-10-10T12:00:00Z"
    bid, children = _rescanned_batch(repos)
    rid, jid = children[1]
    db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j", {"t": iso_plus(now, -30), "j": jid})
    # 배치 행의 갱신(이름 바꾸기)은 창에 들지 않는다 -- 「방금 이름 바꾼 배치는 삭제 불가」 금지.
    db.execute("UPDATE batches SET updated_at = :t WHERE batch_id = :b", {"t": now, "b": bid})
    assert _delete_batch(repos, bid, now=now) == {"deleted": False, "reason": "request_recently_finished",
                                                  "request_id": rid}
    assert _delete_batch(repos, bid, now=now, quiet_seconds=0)["deleted"] is True


def test_batch_delete_rejects_a_stale_child_count(repos, db):
    bid, children = _rescanned_batch(repos)
    before = _state_of(db, bid, children)
    for expected in (3, 5):
        assert _delete_batch(repos, bid, expected_request_count=expected) == {
            "deleted": False, "reason": "batch_changed", "request_id": None}
    assert _state_of(db, bid, children) == before


def test_batch_delete_refuses_more_children_than_the_cap(repos, db):
    bid = _scan_batch(repos, n_items=1)
    children = [_batch_child(repos, bid, target=f"t{i}") for i in range(3)]
    before = _state_of(db, bid, children)
    assert _delete_batch(repos, bid, max_children=2) == {
        "deleted": False, "reason": "batch_delete_too_large", "request_id": None}
    assert _state_of(db, bid, children) == before
    assert _delete_batch(repos, bid, max_children=3)["deleted"] is True


def test_batch_delete_removes_a_dangling_group(repos, db):
    # 배치 화면의 「배치 삭제」(기록만)가 남긴 자식 묶음 + 고아 항목(배치 GET 과 INSERT 사이에 낀 add_item 의 잔재).
    bid, children = _rescanned_batch(repos)
    repos.batches.delete(bid)
    db.execute("""INSERT INTO batch_items (batch_id, seq, payload, status, request_id, created_at, updated_at)
                  VALUES (:b, 7, '{"storage": "s1", "target": "z"}', 'Queued', NULL, :at, :at)""",
               {"b": bid, "at": OLD})
    r = _delete_batch(repos, bid)
    assert r["deleted"] is True and r["dangling"] is True
    assert r["request_ids"] == sorted(rid for rid, _ in children)
    assert _batch_rows(db, bid) == {"batches": 0, "batch_items": 0, "requests": 0}
    snap = json.loads(db.query_one("SELECT before_state FROM audit_log WHERE mutation_class = 'batch'")
                      ["before_state"])
    assert snap["batch"] is None and snap["dangling"] is True and snap["child_count"] == 4
    assert [it["seq"] for it in snap["items"]] == [7]


def test_batch_delete_with_neither_batch_nor_children_is_not_found(repos):
    assert _delete_batch(repos, "e" * 32, expected_request_count=0) == {
        "deleted": False, "reason": "batch_not_found", "request_id": None}


def test_terminal_batch_without_children_is_deletable(repos, db):
    # 자식을 만들기 전에 끝난 배치(전 항목 Rejected·취소) -- 배치 행과 항목만 지운다.
    bid = _scan_batch(repos, n_items=2, status="Cancelled")
    r = _delete_batch(repos, bid, expected_request_count=0)
    assert r == {"deleted": True, "request_ids": [], "job_ids": [], "dangling": False}
    assert _batch_rows(db, bid) == {"batches": 0, "batch_items": 0, "requests": 0}


def test_batch_delete_refuses_a_child_that_another_batch_points_at(repos, db):
    bid, children = _rescanned_batch(repos)
    other = _scan_batch(repos, n_items=1, name="다른 배치")
    rid = children[0][0]
    db.execute("UPDATE batch_items SET request_id = :r WHERE batch_id = :b", {"r": rid, "b": other})
    before = _state_of(db, bid, children)
    # 배치 단위로 고른 사람에게 단건 사유(「배치 단위로 선택해 삭제하세요」)를 돌려주지 않는다(2026-10-11 검증 지적).
    assert _delete_batch(repos, bid) == {"deleted": False, "reason": "batch_child_shared", "request_id": rid}
    assert _state_of(db, bid, children) == before
    # 처방대로 그 항목을 가진 배치를 먼저 지우면(그 배치는 자식이 없다 -- 항목만) 이 배치도 지워진다.
    assert _delete_batch(repos, other, expected_request_count=0)["deleted"] is True
    assert _delete_batch(repos, bid)["deleted"] is True


def test_batch_delete_refuses_more_items_than_the_cap(repos, db):
    # 자식은 적어도 항목이 많은 배치(큰 CSV 를 일찍 취소) -- 항목 전부를 잠그고 감사에 직렬화하고 지우는 트랜잭션도
    # RLock 을 쥔다. 항목 상한도 batch_delete_too_large(2026-10-11 검증 지적).
    bid = _scan_batch(repos, n_items=4, status="Cancelled")
    children = [_batch_child(repos, bid, seq=0, target="t0")]
    before = _state_of(db, bid, children)
    assert _delete_batch(repos, bid, max_items=3) == {
        "deleted": False, "reason": "batch_delete_too_large", "request_id": None}
    assert _state_of(db, bid, children) == before
    assert _delete_batch(repos, bid, max_items=4)["deleted"] is True


def test_items_left_without_batch_or_children_are_deletable(repos, db):
    # 배치 행도 자식도 없이 항목만 남은 묶음(옛 add_item·replace_items 경합, 롤링 업데이트 겹침의 옛 파드 add_item --
    # 2026-10-11 검증 지적). 예전엔 batch_not_found 라 영영 지울 수 없었고, 그 항목이 단건 요청을 가리키면 그 요청의
    # 단건 삭제까지 batch_child_not_deletable 로 막혔다.
    single = _finished(repos)
    orphan = "f" * 32
    db.execute("""INSERT INTO batch_items (batch_id, seq, payload, status, request_id, created_at, updated_at)
                  VALUES (:b, 0, '{"storage": "s1", "target": "z"}', 'Queued', NULL, :at, :at),
                         (:b, 1, '{"storage": "s1", "target": "y"}', 'Succeeded', :r, :at, :at)""",
               {"b": orphan, "at": OLD, "r": single[0]})
    assert _delete(repos, single[0])["reason"] == "batch_child_not_deletable"
    assert _delete_batch(repos, orphan, expected_request_count=1) == {
        "deleted": False, "reason": "batch_changed", "request_id": None}     # 자식은 0 -- 수가 곧 버전
    r = _delete_batch(repos, orphan, expected_request_count=0)
    assert r == {"deleted": True, "request_ids": [], "job_ids": [], "dangling": True}
    assert _batch_rows(db, orphan) == {"batches": 0, "batch_items": 0, "requests": 0}
    snap = json.loads(db.query_one("SELECT before_state FROM audit_log WHERE mutation_class = 'batch'")
                      ["before_state"])
    assert snap["batch"] is None and snap["dangling"] is True and snap["child_count"] == 0
    assert [(it["seq"], it["request_id"]) for it in snap["items"]] == [(0, None), (1, single[0])]
    # 그 항목이 막던 단건 요청은 이제 단건으로 지워진다.
    assert _delete(repos, single[0])["deleted"] is True


class _GhostChild:
    """유령 자식 재확인(14단계) 직전에 같은 batch_id 의 자식을 INSERT 하는 DB 프록시 -- PG READ COMMITTED 에서 잠금 뒤
    커밋된 동시 INSERT(FOR UPDATE 가 보지 못한 행) 흉내."""

    def __init__(self, db, batch_id):
        self._db, self._bid = db, batch_id
        self.ghost = None

    def __getattr__(self, name):
        return getattr(self._db, name)

    def query_one(self, sql, params=None):
        if self.ghost is None and sql.startswith("SELECT 1 AS x FROM requests WHERE batch_id"):
            self.ghost = uuid.uuid4().hex
            self._db.execute("""INSERT INTO requests (request_id, commit_order, operation, requester_id, actor,
                                    resource_key, payload, state, created_at, updated_at, batch_id)
                                VALUES (:r, 99999, 'scan', 'a', 'a', 'k', '{}', 'Pending', :at, :at, :b)""",
                             {"r": self.ghost, "at": OLD, "b": self._bid})
        return self._db.query_one(sql, params)


def test_batch_delete_rolls_back_everything_when_a_ghost_child_appears(repos, db):
    bid, children = _rescanned_batch(repos)
    before = _state_of(db, bid, children)
    proxy = _GhostChild(db, bid)
    r = RequestPurgesRepository(proxy).delete_batch(bid, expected_request_count=4, actor="opadm",
                                                    artifact_base=BASE, quiet_seconds=60, max_children=1000)
    assert r == {"deleted": False, "reason": "batch_changed", "request_id": None}
    assert proxy.ghost is not None
    assert _state_of(db, bid, children) == before                      # 자식·항목·배치·아웃박스·감사 전부 그대로
    assert repos.requests.get(proxy.ghost) is None                     # 흉내 낸 INSERT 도 함께 롤백


def test_batch_delete_cas_on_the_batch_row_rolls_back(repos, db, monkeypatch):
    # 판정 뒤 배치 행 CAS 전에 다른 쪽이 배치를 되살린 경합(sqlite 에는 행 잠금이 없다 -- 같은 커넥션 UPDATE 로 흉내).
    bid, children = _rescanned_batch(repos)
    before = _state_of(db, bid, children)
    real = db.execute_count

    def racing(sql, params=None):
        if sql.startswith("DELETE FROM batches"):
            real("UPDATE batches SET status = 'Running' WHERE batch_id = :b", {"b": bid})
        return real(sql, params)
    monkeypatch.setattr(db, "execute_count", racing)
    assert _delete_batch(repos, bid) == {"deleted": False, "reason": "batch_changed", "request_id": None}
    monkeypatch.undo()
    assert _state_of(db, bid, children) == before
    assert repos.batches.get(bid)["status"] == "Completed"


def test_batch_audit_failure_rolls_back_everything(repos, db, monkeypatch):
    bid, children = _rescanned_batch(repos)
    before = _state_of(db, bid, children)
    real = db.execute

    def boom(sql, params=None):
        if "VALUES ('batch', 'delete'" in sql:
            raise RuntimeError("audit insert failed")
        return real(sql, params)
    monkeypatch.setattr(db, "execute", boom)
    with pytest.raises(RuntimeError):
        _delete_batch(repos, bid)
    monkeypatch.undo()
    assert _state_of(db, bid, children) == before


class _PgSpyAll(_PgSpy):
    """_PgSpy + 다행 조회도 기록(배치 삭제는 항목·자식·잡을 query 로 잠근다)."""

    def query(self, sql, params=None):
        self.sql.append(sql)
        return self._db.query(sql.replace(" FOR UPDATE", ""), params)


def test_batch_delete_lock_order_on_postgres(repos, db):
    # 잠금 순서 batch_items → batches → requests → data_jobs: 오케스트레이터 _record_terminal·reject_queued_item
    # (batch_items → batches)과 planner·단건 삭제(requests → data_jobs)를 둘 다 지키는 방향. 역순이면 교착.
    import re
    bid, _ = _rescanned_batch(repos)
    spy = _PgSpyAll(db)
    assert RequestPurgesRepository(spy).delete_batch(bid, expected_request_count=4, actor="opadm", artifact_base=BASE,
                                                     quiet_seconds=60, max_children=1000)["deleted"] is True
    locked = [re.search(r"FROM (\w+)", q).group(1) for q in spy.sql if q.rstrip().endswith("FOR UPDATE")]
    order = list(dict.fromkeys(locked))
    assert order[:4] == ["batch_items", "batches", "requests", "data_jobs"], locked
    assert set(order[4:]) <= {"request_purges"}                       # 아웃박스 행(_enqueue)만 뒤에 온다


class _Counting:
    def __init__(self, db):
        self._db, self.n, self.sql = db, 0, []

    def __getattr__(self, name):
        return getattr(self._db, name)

    def _count(self, fn, sql, params):
        self.n += 1
        self.sql.append(sql)
        return fn(sql, params)

    def query(self, sql, params=None):
        return self._count(self._db.query, sql, params)

    def query_one(self, sql, params=None):
        return self._count(self._db.query_one, sql, params)

    def execute(self, sql, params=None):
        return self._count(self._db.execute, sql, params)

    def execute_count(self, sql, params=None):
        return self._count(self._db.execute_count, sql, params)


def _bulk_children(db, bid, n, *, with_job=True):
    """자식 n 개를 한 트랜잭션에 직접 INSERT(repo 헬퍼는 자식 하나에 트랜잭션 열 개라 수백 개면 느리다)."""
    out = []
    with db.transaction():
        for i in range(n):
            rid = uuid.uuid4().hex
            db.execute("""INSERT INTO requests (request_id, commit_order, operation, requester_id, actor,
                              resource_key, payload, state, created_at, updated_at, batch_id, auth_method)
                          VALUES (:r, :o, 'scan', 'admin', 'admin', :k, '{}', 'Succeeded', :at, :at, :b, 'session')""",
                       {"r": rid, "o": 100000 + i, "k": f"k{i}", "at": OLD, "b": bid})
            jid = None
            if with_job:
                jid = uuid.uuid4().hex
                db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, options, priority, state,
                                  storage_name, target, created_at, updated_at)
                              VALUES (:j, :r, 'scan', '{}', 'mid', 'Succeeded', 's1', :t, :at, :at)""",
                           {"j": jid, "r": rid, "t": f"t{i}", "at": OLD})
            out.append((rid, jid))
    return out


def test_batch_delete_statement_budget(repos, db):
    # 성능 회귀를 시간이 아니라 결정적으로 잡는다 -- 트랜잭션 동안 API 전체가 멈추므로(단일 커넥션 RLock) 자식당 문장
    # 수가 늘면 상한(1000) 근처 배치가 readiness 를 넘긴다. 자식 200개(잡 각 1개) ≤ 6×200 + 60: 자식마다 감사 스냅숏
    # 읽기 3(결과·요청 전이·잡 전이) + events DELETE 1 + 아웃박스 INSERT 1 + 감사 INSERT 1. 아웃박스의 기존 행 조회는
    # 묶음마다 한 번이다(자식마다 읽으면 PG 일반 계획이 아웃박스를 자식마다 훑었다 -- 2026-10-10 검증 지적).
    bid = _scan_batch(repos, n_items=1)
    children = _bulk_children(db, bid, 200)
    counting = _Counting(db)
    r = RequestPurgesRepository(counting).delete_batch(bid, expected_request_count=200, actor="opadm",
                                                       artifact_base=BASE, quiet_seconds=60, max_children=1000)
    assert r["deleted"] is True and len(r["request_ids"]) == 200 and len(r["job_ids"]) == 200
    assert counting.n <= 6 * 200 + 60, counting.n
    outbox_reads = [sql for sql in counting.sql if "FROM request_purges" in sql and sql.lstrip().startswith("SELECT")]
    assert len(outbox_reads) == 1, outbox_reads
    assert _batch_rows(db, bid) == {"batches": 0, "batch_items": 0, "requests": 0}
    assert db.query_one("SELECT COUNT(*) AS n FROM data_jobs")["n"] == 0
    assert repos.request_purges.pending_count() == len(children)


class _RowCounting(_Counting):
    """_Counting + 조회가 돌려준 행 수 합(rows)."""

    def __init__(self, db):
        super().__init__(db)
        self.rows = 0

    def query(self, sql, params=None):
        out = super().query(sql, params)
        self.rows += len(out)
        return out

    def query_one(self, sql, params=None):
        out = super().query_one(sql, params)
        self.rows += 0 if out is None else 1
        return out


def test_refusing_an_oversized_batch_reads_at_most_cap_plus_one_rows(repos, db):
    # 2026-10-11 검증 지적: 상한 판정이 자식·항목을 **전부** 잠그고 읽은 뒤였다 -- 자식 2만 배치를 거절하는 데만 2만 행을
    # 읽고 잠가(PG 74ms) 그동안 API 전체(RLock)가 멈췄다. 지금은 상한 + 1 까지만 본다(delete_batch docstring 「상한 판정」).
    bid = _scan_batch(repos, n_items=1)
    _bulk_children(db, bid, 60, with_job=False)
    counting = _RowCounting(db)
    r = RequestPurgesRepository(counting).delete_batch(bid, expected_request_count=60, actor="opadm",
                                                       artifact_base=BASE, quiet_seconds=60, max_children=10)
    assert r == {"deleted": False, "reason": "batch_delete_too_large", "request_id": None}
    assert counting.rows <= 1 + 1 + 1, (counting.rows, counting.sql)   # 항목 1 + 배치 1 + 세기 1 -- 자식 행은 읽지 않는다
    assert not any(sql.lstrip().startswith("SELECT *") and "FROM requests" in sql for sql in counting.sql)
    # 세기는 범위 표기 + 인덱스 순서(requests.batch_match) -- `batch_id = :b LIMIT` 면 PG 가 큰 배치에서 Seq Scan + LIMIT 를
    # 골라 그 배치의 물리 위치 앞의 행 전부를 읽었다(이 판정이 막으려는 바로 그 큰 배치에서).
    (head,) = [sql for sql in counting.sql if "COUNT(*)" in sql]
    assert "batch_id >= :b AND batch_id <= :b" in head and "ORDER BY batch_id, commit_order LIMIT :nc" in head
    # 항목 상한도 같다: 항목 50개 배치를 상한 5 로 거절하며 항목은 6행까지만 읽는다.
    big = _scan_batch(repos, n_items=50, status="Cancelled")
    counting = _RowCounting(db)
    r = RequestPurgesRepository(counting).delete_batch(big, expected_request_count=0, actor="opadm",
                                                       artifact_base=BASE, quiet_seconds=60, max_children=10,
                                                       max_items=5)
    assert r == {"deleted": False, "reason": "batch_delete_too_large", "request_id": None}
    assert counting.rows <= 6 + 1 + 1, (counting.rows, counting.sql)
    # 상한 안이면 전부 잠그고 지운다(LIMIT 에 닿지 않는다 -- 의미 그대로).
    assert _delete_batch(repos, big, expected_request_count=0, max_items=50)["deleted"] is True
    assert _delete_batch(repos, bid, max_children=60)["deleted"] is True


def test_active_oversized_batch_still_says_not_deletable_first(repos, db):
    # 판정 순서는 그대로다(배치 상태 → … → 상한) -- 상한 세기를 앞당겨도 진행 중인 큰 배치는 「진행 중」이 먼저다.
    bid = _scan_batch(repos, n_items=1, status="Running")
    _bulk_children(db, bid, 5, with_job=False)
    assert _delete_batch(repos, bid, max_children=2) == {
        "deleted": False, "reason": "batch_not_deletable", "request_id": None}


def test_batch_audit_is_capped_and_keeps_the_child_count(repos, db):
    bid = repos.batches.create(
        operation="scan", requester_id="admin", actor="admin", max_concurrency=2, options={"x": "o" * 5000},
        note="n" * 100000, items=[{"storage": "s1", "target": f"t{i}" + "p" * 3000} for i in range(20)],
        status="Completed", name="큰 배치", auth_method="session")
    _bulk_children(db, bid, 2000, with_job=False)
    r = _delete_batch(repos, bid, max_children=5000)
    assert r["deleted"] is True and len(r["request_ids"]) == 2000
    text = db.query_one("SELECT before_state FROM audit_log WHERE mutation_class = 'batch'")["before_state"]
    assert len(text.encode("utf-8")) <= SNAPSHOT_MAX_BYTES
    snap = json.loads(text)
    assert snap["truncated"] is True and snap["child_count"] == 2000
    assert 0 < len(snap["child_request_ids"]) < 2000
    assert set(snap["child_request_ids"]) <= set(r["request_ids"])
    assert snap["batch"]["name"] == "큰 배치" and len(snap["batch"]["note"]) == 500    # 배치 고유 정보가 우선이다
    # 항목 payload(20 × 3 KB)는 자식 id 없이도 넘친다 -- 그다음 단계(payload 만 비움)로 항목 20개를 남기고, 남는 자리를
    # 자식 id 로 채운다(자식 id 는 자식 감사 행에도 있는 가장 중복된 정보라 먼저 줄인다).
    # 줄인 모양은 payload 와 값이 null 인 키를 뺀다(대기 항목은 {seq, status} 만 -- 키 없음 = null, truncated 가 말한다).
    assert snap["items"] == [{"seq": i, "status": "Queued"} for i in range(20)]
    assert (snap["item_count"], snap["items_kept"]) == (20, 20)


def test_batch_audit_trims_redundant_child_ids_before_the_items(repos):
    # 2026-10-10 검증 지적: 자식이 ~895개를 넘으면 id 목록만으로 32 KiB 를 넘어, 예전 순서(항목을 먼저 비움)는 작은 항목
    # 2개(대상·사유 -- 배치와 함께 지워져 다른 어디에도 없다)를 통째로 버리고 id 894개를 남겼다.
    batch = dict.fromkeys(("batch_id", "name", "note", "operation", "status", "requester_id", "actor", "owner_username",
                           "auth_method", "options", "max_concurrency", "priority", "node_count", "procs_per_node",
                           "item_count", "succeeded_count", "failed_count", "preview_round", "created_at", "updated_at"))
    batch.update(batch_id="b" * 32, name="성장 모니터링", options="{}")
    items = [{"seq": i, "status": "Rejected" if i else "Succeeded", "request_id": None,
              "reason_code": "path_outside_storage" if i else None,
              "payload": json.dumps({"storage": "s1", "target": f"/t{i}"})} for i in range(2)]
    rids = [uuid.uuid4().hex for _ in range(900)]
    text = RequestPurgesRepository._batch_bounded(batch, items, rids, dangling=False)
    assert len(text.encode("utf-8")) <= SNAPSHOT_MAX_BYTES
    snap = json.loads(text)
    assert snap["truncated"] is True and snap["child_count"] == 900
    assert snap["items"] == [{"seq": 0, "status": "Succeeded", "request_id": None, "reason_code": None,
                              "payload": {"storage": "s1", "target": "/t0"}},
                             {"seq": 1, "status": "Rejected", "request_id": None,
                              "reason_code": "path_outside_storage", "payload": {"storage": "s1", "target": "/t1"}}]
    assert snap["batch"]["name"] == "성장 모니터링"
    assert 800 < len(snap["child_request_ids"]) < 900 and snap["child_request_ids"] == rids[:len(snap["child_request_ids"])]
    # 들어가면 아무것도 줄이지 않는다.
    small = json.loads(RequestPurgesRepository._batch_bounded(batch, items, rids[:800], dangling=False))
    assert small["truncated"] is False and small["child_request_ids"] == rids[:800] and len(small["items"]) == 2


def test_batch_audit_keeps_as_many_items_as_fit_childless_first(repos):
    # 2026-10-11 검증 지적: 예전 단계는 「payload 비움 → {seq,status,request_id} → 항목 비움」이라 경로 40자 안팎의 항목
    # ~600개부터 항목이 통째로 사라지고(큰 CSV 배치의 Rejected·Queued 사유·대상이 감사에서 증발) 중복 정보인 자식 id 만
    # 남았다. 이제 들어가는 만큼 항목을 남기고 -- 자식 없는 항목(사유·대상이 여기뿐) 먼저 -- 몇 개를 남겼는지 적는다.
    batch = dict.fromkeys(_BATCH_KEYS_FOR_TEST)
    batch.update(batch_id="b" * 32, name="큰 CSV", options="{}", status="Cancelled")
    rids = [uuid.uuid4().hex for _ in range(10)]
    items = [{"seq": i, "status": "Succeeded" if i < 10 else ("Rejected" if i % 2 else "Queued"),
              "request_id": rids[i] if i < 10 else None,
              "reason_code": "path_outside_storage" if i >= 10 and i % 2 else None,
              "payload": json.dumps({"storage": "s1", "target": f"/mgmt_storage/proj/area{i:06d}/deep/path"})}
             for i in range(600)]
    text = RequestPurgesRepository._batch_bounded(batch, items, rids, dangling=False)
    assert len(text.encode("utf-8")) <= SNAPSHOT_MAX_BYTES
    snap = json.loads(text)
    assert snap["truncated"] is True and snap["item_count"] == 600 and snap["child_count"] == 10
    kept = snap["items"]
    # 예전엔 0개. 이제 줄인 모양으로 거의 다 들어가고, 모자란 자리는 자식 있는 항목(대상이 자식 감사 행에 있다)이 낸다.
    assert snap["items_kept"] == len(kept) and 590 <= len(kept) < 600
    seqs = [it["seq"] for it in kept]
    assert seqs == sorted(seqs) and set(range(10, 600)) <= set(seqs)
    by_seq = {it["seq"]: it for it in kept}
    assert by_seq[11] == {"seq": 11, "status": "Rejected", "reason_code": "path_outside_storage"}
    assert by_seq[12] == {"seq": 12, "status": "Queued"}
    assert all(by_seq[s] == {"seq": s, "status": "Succeeded", "request_id": rids[s]} for s in seqs if s < 10)
    # 그래도 넘치면 들어가는 만큼만 -- 자식 없는 항목 먼저(자식 있는 항목 seq 0~9 는 자리가 남을 때만), 출력은 seq 순.
    items = items + [{**it, "seq": it["seq"] + 590} for it in items[10:]]
    snap = json.loads(RequestPurgesRepository._batch_bounded(batch, items, rids, dangling=False))
    kept = snap["items"]
    assert snap["truncated"] is True and snap["item_count"] == len(items) == 1190
    assert snap["items_kept"] == len(kept) and 500 < len(kept) < 1180
    assert all("request_id" not in it for it in kept)
    seqs = [it["seq"] for it in kept]
    assert seqs == sorted(seqs) and seqs[0] == 10
    # 중복인 자식 id 는 항목 다음 순위 -- 항목 하나가 더 들어가지 못한 자투리에만(id 는 항목보다 짧다).
    assert len(snap["child_request_ids"]) < len(rids)
    # 자식 없는 항목이 다 들어가고 자리가 남으면 자식 있는 항목도 seq 순으로 담는다.
    many = [uuid.uuid4().hex for _ in range(100)]
    mixed = [{"seq": i, "status": "Succeeded" if i < 100 else "Rejected", "request_id": many[i] if i < 100 else None,
              "reason_code": None if i < 100 else "path_outside_storage",
              "payload": json.dumps({"storage": "s1", "target": f"/t{i}"})} for i in range(500)]
    snap = json.loads(RequestPurgesRepository._batch_bounded(batch, mixed, many, dangling=False))
    assert snap["truncated"] is True and 400 < snap["items_kept"] < 500
    seqs = [it["seq"] for it in snap["items"]]
    assert seqs == sorted(seqs) and set(range(100, 500)) <= set(seqs)
    assert set(seqs) - set(range(100, 500)) == set(range(snap["items_kept"] - 400))


def test_batch_audit_drops_child_item_payloads_before_childless_ones(repos):
    # 2단계: 자식이 있는 항목의 대상은 그 자식의 감사 행 payload 에 있다 -- 먼저 비운다. 자식 없는 항목의 대상은 남긴다.
    batch = dict.fromkeys(_BATCH_KEYS_FOR_TEST)
    batch.update(batch_id="b" * 32, name="섞인 배치", options="{}", status="Completed")
    rids = [uuid.uuid4().hex for _ in range(20)]
    items = [{"seq": i, "status": "Succeeded" if i < 20 else "Rejected", "request_id": rids[i] if i < 20 else None,
              "reason_code": None if i < 20 else "path_outside_storage",
              "payload": json.dumps({"storage": "s1", "target": f"/t{i}" + "p" * 1500})} for i in range(25)]
    snap = json.loads(RequestPurgesRepository._batch_bounded(batch, items, rids, dangling=False))
    assert snap["truncated"] is True and snap["items_kept"] == 25
    assert snap["items"][:20] == [{"seq": i, "status": "Succeeded", "request_id": rids[i]} for i in range(20)]
    assert [it["payload"]["target"][:4] for it in snap["items"][20:]] == ["/t20", "/t21", "/t22", "/t23", "/t24"]
    assert snap["child_request_ids"] == rids                    # 남는 자리에 자식 id


_BATCH_KEYS_FOR_TEST = ("batch_id", "name", "note", "operation", "status", "requester_id", "actor", "owner_username",
                        "auth_method", "options", "max_concurrency", "priority", "node_count", "procs_per_node",
                        "item_count", "succeeded_count", "failed_count", "preview_round", "created_at", "updated_at")


def test_batch_audit_folds_a_tampered_huge_batch_row(repos):
    huge = {"batch_id": "b" * 32, "name": "N" * 100000, "note": None, "operation": "scan", "status": "Completed",
            "requester_id": "admin", "actor": "admin", "owner_username": None, "auth_method": "session",
            "options": "{" * 50000, "max_concurrency": 1, "priority": None, "node_count": None,
            "procs_per_node": None, "item_count": 0, "succeeded_count": 0, "failed_count": 0,
            "preview_round": 0, "created_at": OLD, "updated_at": OLD}
    text = RequestPurgesRepository._batch_bounded(huge, [], ["r" * 32] * 3, dangling=False)
    assert len(text.encode("utf-8")) <= SNAPSHOT_MAX_BYTES
    snap = json.loads(text)
    assert snap["truncated"] is True and snap["child_count"] == 3 and snap["child_request_ids"] == ["r" * 32] * 3
    assert snap["batch"]["batch_id"] == "b" * 32 and snap["batch"]["name"] == "N" * 1000


def test_terminal_batch_set_mirrors_the_route():
    from dms.api import routes_batches
    assert _TERMINAL_BATCH == routes_batches._TERMINAL_BATCH
