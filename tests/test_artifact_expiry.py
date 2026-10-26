"""결과 파일 자동 만료(2026-10-26 사용자 결정 「파일만 만료하고 행은 남김, N 기본 1500일」).

- 고르기·표시(artifact_expiry.ArtifactExpirer + request_purges.expire_artifacts): 경계(정확히 N일은 아직), 0 = 끔, 비종단
  무접촉, 이미 만료한 잡 건너뜀, 아웃박스 행이 있는 요청 건너뜀, 대기열 상한, drain.
- 정리(같은 길 -- request_purger + 실 PurgeRunner + 가짜 k8s + 실 임시 base): 파일만 지우고 행은 남는다, 요청 삭제와 같은
  purge 파드를 함께 쓴다, 옛 base 는 무접촉, DB 복원(표시 사라짐)이면 무접촉, 만료 중 요청 삭제는 삭제 행으로 바뀐다.
- 요청 삭제 표면(status)·잠금 수(pending_count)와의 경계.
PG 갈래(DMS_TEST_DB=postgresql)로도 같은 테스트가 돈다(FOR UPDATE OF d SKIP LOCKED 문법은 그 갈래에서만 실행된다)."""
import dataclasses
import json
import os
from types import SimpleNamespace

import pytest

from dms.artifact_expiry import BACKLOG_MAX, INTERVAL_SECONDS, ArtifactExpirer
from dms.artifact_trash import TRASH
from dms.config import Settings, SettingsError
from dms.controller import build_loops, run_all_once
from dms.db import iso_plus, utc_now_iso
from dms.domain import DataJobState, RequestState
from dms.purge_runner import PurgeRunner, StubPurgeRunner
from dms.repositories import Repositories
from dms.repositories.request_purges import EXPIRY_KIND
from dms.request_purger import RequestPurger
from fake_purge_k8s import FakeK8s

DAY = 86400
NOW = "2031-06-01T00:00:00Z"
INTERVAL = 15


def _mk(repos, db, *, state=DataJobState.SUCCEEDED, finalize=True, updated_at=None, uri=None, requester="alice"):
    """요청 1건 + 잡 1개. state 가 종단이고 finalize 면 요청도 종단. updated_at 으로 종단 시각을 옮긴다."""
    rid = repos.requests.create(operation="scan", requester_id=requester, actor=requester,
                                resource_key=f"k-{os.urandom(4).hex()}",
                                payload={"storage": "s1", "target": "t", "run_as_root": True},
                                priority="mid", auth_method="session")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1", target="t", options={},
        tool="dscan", precondition={}, actor="planner",
        worker_pool={"identity": {"username": requester, "uid": 0, "gid": 0, "privileged": True},
                     "candidates": {"primary": ["dms-w1"]}})
    repos.data_jobs.set_phase_ref(jid, "execution", f"vcjob/dms-scan-execution-{jid[:12]}")
    repos.data_jobs.set_job_state(jid, state, actor="stepper")
    if finalize and state in (DataJobState.SUCCEEDED, DataJobState.FAILED, DataJobState.CANCELLED,
                              DataJobState.REJECTED, DataJobState.TIMED_OUT, DataJobState.PREVIEW_EXPIRED):
        repos.requests.finalize_from_job(rid, state, actor="stepper",
                                         summary={"returncode": 0, "files": 3, "bytes": None})
    if uri is not None:
        db.execute("UPDATE data_jobs SET artifact_uri = :u WHERE job_id = :j", {"u": uri, "j": jid})
    if updated_at is not None:
        db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j", {"t": updated_at, "j": jid})
        db.execute("UPDATE requests SET updated_at = :t WHERE request_id = :r", {"t": updated_at, "r": rid})
    return rid, jid


def _job(db, jid):
    return db.query_one("SELECT * FROM data_jobs WHERE job_id = :j", {"j": jid})


def _outbox(db, rid):
    return db.query_one("SELECT * FROM request_purges WHERE request_id = :r", {"r": rid})


def _expirer(repos, settings, *, now=NOW, days=None):
    st = settings if days is None else dataclasses.replace(settings, artifact_retention_days=days)
    return ArtifactExpirer(repos, settings=st, clock=lambda: now)


# ---- 설정 ----

def test_retention_setting_default_override_and_rejects():
    valid = {"DMS_DATABASE_URL": "sqlite:///x", "DMS_SHARED_TOKEN": "t", "DMS_ADMIN_TOKEN": "a",
             "DMS_SESSION_SECRET": "s"}
    assert Settings.from_env(valid).artifact_retention_days == 1500
    assert Settings.from_env({**valid, "DMS_ARTIFACT_RETENTION_DAYS": "0"}).artifact_retention_days == 0
    assert Settings.from_env({**valid, "DMS_ARTIFACT_RETENTION_DAYS": "30"}).artifact_retention_days == 30
    assert Settings(database_url="u", shared_token="t", admin_token="a",
                    session_secret="s").artifact_retention_days == 1500
    for bad in ("-1", "forever"):
        with pytest.raises(SettingsError) as e:
            Settings.from_env({**valid, "DMS_ARTIFACT_RETENTION_DAYS": bad})
        assert "DMS_ARTIFACT_RETENTION_DAYS" in str(e.value)


# ---- 고르기·표시 ----

def test_boundary_exactly_n_days_is_not_yet_one_second_more_is(db, settings):
    repos = Repositories(db)
    cutoff = iso_plus(NOW, -1500 * DAY)
    r_edge, j_edge = _mk(repos, db, updated_at=cutoff)                     # 정확히 1500일
    r_old, j_old = _mk(repos, db, updated_at=iso_plus(cutoff, -1))         # 1500일 + 1초
    out = _expirer(repos, settings).run_once()
    assert out == {"backlog": 0, "queued": 1}
    assert _job(db, j_edge)["artifacts_expired_at"] is None and _outbox(db, r_edge) is None
    assert _job(db, j_old)["artifacts_expired_at"] == NOW
    row = _outbox(db, r_old)
    assert (row["kind"], row["stage"], row["artifact_base"], row["requested_by"]) == (
        EXPIRY_KIND, "k8s", "/artifacts/dms", "artifact-expiry")
    jobs = json.loads(row["jobs"])
    assert jobs == [{"job_id": j_old, "phase_refs": {"execution": f"vcjob/dms-scan-execution-{j_old[:12]}"},
                     "artifact_uri": None}]
    # 한 시간 뒤(경계를 넘음) 다음 틱이 나머지를 맡는다.
    assert _expirer(repos, settings, now=iso_plus(NOW, 3600)).run_once()["queued"] == 1
    assert _job(db, j_edge)["artifacts_expired_at"] == iso_plus(NOW, 3600)


def test_retention_zero_disables(db, settings):
    repos = Repositories(db)
    _, jid = _mk(repos, db, updated_at="2000-01-01T00:00:00Z")
    assert _expirer(repos, settings, days=0).run_once() == {"disabled": True}
    assert _job(db, jid)["artifacts_expired_at"] is None
    assert db.query_one("SELECT COUNT(*) AS n FROM request_purges")["n"] == 0


def test_non_terminal_jobs_and_requests_are_never_selected(db, settings):
    repos = Repositories(db)
    old = "2000-01-01T00:00:00Z"
    _, running = _mk(repos, db, state=DataJobState.RUNNING, updated_at=old)
    _, live_req = _mk(repos, db, state=DataJobState.SUCCEEDED, finalize=False, updated_at=old)   # 요청은 Planned
    _, confirm = _mk(repos, db, state=DataJobState.CONFIRM_PENDING, updated_at=old)
    assert _expirer(repos, settings).run_once()["queued"] == 0
    for jid in (running, live_req, confirm):
        assert _job(db, jid)["artifacts_expired_at"] is None
    # 상태 문자열 변조(집합 밖) = 비종단(fail-closed)
    _, weird = _mk(repos, db, updated_at=old)
    db.execute("UPDATE data_jobs SET state = 'Bogus' WHERE job_id = :j", {"j": weird})
    assert _expirer(repos, settings).run_once()["queued"] == 0


def test_every_terminal_state_expires_and_marks_are_not_redone(db, settings):
    repos = Repositories(db)
    old = "2000-01-01T00:00:00Z"
    jids = [_mk(repos, db, state=s, updated_at=old)[1]
            for s in (DataJobState.SUCCEEDED, DataJobState.FAILED, DataJobState.CANCELLED, DataJobState.REJECTED,
                      DataJobState.TIMED_OUT, DataJobState.PREVIEW_EXPIRED)]
    assert _expirer(repos, settings).run_once()["queued"] == 6
    stamps = {j: _job(db, j)["artifacts_expired_at"] for j in jids}
    assert set(stamps.values()) == {NOW}
    # 정리가 끝나 아웃박스 행이 사라져도(finish_expiry) 이미 만료한 잡은 다시 고르지 않는다 -- 표시는 그대로.
    for row in db.query("SELECT request_id, jobs FROM request_purges"):
        assert repos.request_purges.finish_expiry(row["request_id"], [j["job_id"] for j in json.loads(row["jobs"])])
    assert _expirer(repos, settings, now=iso_plus(NOW, 999)).run_once() == {"backlog": 0, "queued": 0}
    assert {j: _job(db, j)["artifacts_expired_at"] for j in jids} == stamps


def test_request_with_an_outbox_row_is_skipped(db, settings):
    repos = Repositories(db)
    old = "2000-01-01T00:00:00Z"
    rid, jid = _mk(repos, db, updated_at=old)
    # 삭제 정리 중(DB 복원으로 되살아난 요청 -- purge_target_still_present 로 멈춘 행)
    db.execute("""INSERT INTO request_purges (request_id, jobs, artifact_base, stage, outcomes, attempts, last_error,
                      next_attempt_at, requested_by, requested_at, updated_at)
                  VALUES (:r, '[]', '/b', 'k8s', NULL, 0, NULL, :t, 'opadm', :t, :t)""", {"r": rid, "t": NOW})
    assert _expirer(repos, settings).run_once()["queued"] == 0
    assert _job(db, jid)["artifacts_expired_at"] is None
    assert _outbox(db, rid)["kind"] is None                       # 삭제 행은 그대로


def test_backlog_cap_bounds_queued_rows(db, settings):
    repos = Repositories(db)
    old = "2000-01-01T00:00:00Z"
    for i in range(BACKLOG_MAX + 2):
        _mk(repos, db, updated_at=iso_plus(old, i))
    assert _expirer(repos, settings).run_once() == {"backlog": 0, "queued": BACKLOG_MAX}
    assert _expirer(repos, settings).run_once() == {"backlog": BACKLOG_MAX, "queued": 0}
    # 오래된 순이다 -- 남은 2건은 가장 최근 두 잡.
    left = db.query("SELECT updated_at FROM data_jobs WHERE artifacts_expired_at IS NULL ORDER BY updated_at")
    assert [r["updated_at"] for r in left] == [iso_plus(old, BACKLOG_MAX), iso_plus(old, BACKLOG_MAX + 1)]
    # 10건이 끝나면 그 빈자리만큼(남은 2건 전부).
    for row in db.query("SELECT request_id, jobs FROM request_purges ORDER BY requested_at, request_id LIMIT 10"):
        repos.request_purges.finish_expiry(row["request_id"], [j["job_id"] for j in json.loads(row["jobs"])])
    assert _expirer(repos, settings).run_once() == {"backlog": BACKLOG_MAX - 10, "queued": 2}


def test_drain_does_nothing(db, settings):
    repos = Repositories(db)
    _, jid = _mk(repos, db, updated_at="2000-01-01T00:00:00Z")
    db.execute("UPDATE control_state SET drain = 1 WHERE id = 1")
    assert _expirer(repos, settings).run_once() == {}
    assert _job(db, jid)["artifacts_expired_at"] is None


def test_expire_artifacts_rejects_nonpositive_limit(db):
    repos = Repositories(db)
    _mk(repos, db, updated_at="2000-01-01T00:00:00Z")
    for limit in (0, -1, True, None):
        assert repos.request_purges.expire_artifacts(cutoff=NOW, artifact_base="/b", limit=limit) == []
    assert db.query_one("SELECT COUNT(*) AS n FROM request_purges")["n"] == 0


def test_invalid_expiry_targets_is_fail_closed(db, settings):
    repos = Repositories(db)
    old = "2000-01-01T00:00:00Z"
    (r1, j1), (r2, j2) = _mk(repos, db, updated_at=old), _mk(repos, db, updated_at=old)
    _expirer(repos, settings).run_once()
    p = repos.request_purges
    assert p.invalid_expiry_targets([{"request_id": r1, "job_ids": [j1]}, {"request_id": r2, "job_ids": [j2]}]) == set()
    assert p.invalid_expiry_targets([]) == set()
    assert p.invalid_expiry_targets([{"request_id": r1, "job_ids": []}]) == {r1}           # 잡 없음
    assert p.invalid_expiry_targets([{"request_id": r1, "job_ids": None}]) == {r1}         # 깨진 jobs
    assert p.invalid_expiry_targets([{"request_id": r1, "job_ids": [j2]}]) == {r1}         # 남의 요청 잡
    assert p.invalid_expiry_targets([{"request_id": r1, "job_ids": ["f" * 32]}]) == {r1}   # 없는 잡
    db.execute("UPDATE data_jobs SET artifacts_expired_at = NULL WHERE job_id = :j", {"j": j1})   # DB 복원
    db.execute("UPDATE data_jobs SET state = 'Running' WHERE job_id = :j", {"j": j2})             # 변조
    assert p.invalid_expiry_targets([{"request_id": r1, "job_ids": [j1]},
                                     {"request_id": r2, "job_ids": [j2]}]) == {r1, r2}
    # 무효면 끝내지 않는다(행 유지).
    assert p.finish_expiry(r1, [j1]) is False and _outbox(db, r1) is not None


def test_deletion_status_excludes_expiry_rows_and_lock_count_does_not_double(db, settings):
    repos = Repositories(db)
    _mk(repos, db, updated_at="2000-01-01T00:00:00Z")
    _expirer(repos, settings).run_once()
    p = repos.request_purges
    assert p.status()["pending"] == 0 and p.status()["items"] == []        # 삭제 툴바 표면엔 안 보인다
    assert p.pending_count() == 1 and p.pending_count(include_expiry=False) == 0
    st = p.expiry_status()
    assert (st["pending"], st["stalled"], st["last_error"], st["expired_jobs"]) == (1, 0, None, 1)
    assert st["oldest_requested_at"] == NOW
    # 모르는 kind(변조)는 삭제 표면에 남는다 -- 어디서도 안 보이면 안 된다.
    db.execute("UPDATE request_purges SET kind = 'bogus', last_error = 'purge_row_invalid'")
    assert p.status()["pending"] == 1 and p.status()["stalled"] == 1
    assert p.expiry_status()["pending"] == 0


def test_pg_selection_skips_jobs_locked_by_another_transaction(db, settings):
    """PG 갈래 전용: 고르기는 남이 쥔 잡 잠금을 **기다리지 않는다**(FOR UPDATE OF d SKIP LOCKED -- 요청 삭제가 requests →
    data_jobs 로 잠그는 동안 기다리면 배치 삭제와 잠금 순환이 생길 수 있다). lock_timeout 2초 -- 기다리는 구현이면
    매달리지 않고 오류로 빨개진다."""
    if db.dialect != "postgresql":
        pytest.skip("PostgreSQL 갈래 전용(DMS_TEST_DB=postgresql)")
    import psycopg
    repos = Repositories(db)
    old = "2000-01-01T00:00:00Z"
    (r_locked, j_locked), (r_free, j_free) = _mk(repos, db, updated_at=old), _mk(repos, db, updated_at=iso_plus(old, 1))
    schema = db.query_one("SELECT current_schema() AS s")["s"]
    url = os.environ["DMS_TEST_PG_URL"]
    other = psycopg.connect(url, options=f"-csearch_path={schema}")
    try:
        other.execute("SELECT job_id FROM data_jobs WHERE job_id = %s FOR UPDATE", (j_locked,))   # 열린 트랜잭션
        db.execute("SET lock_timeout = '2s'")
        assert _expirer(repos, settings).run_once()["queued"] == 1
        assert _job(db, j_free)["artifacts_expired_at"] == NOW
        assert _job(db, j_locked)["artifacts_expired_at"] is None and _outbox(db, r_locked) is None
    finally:
        other.rollback()
        other.close()
    # 잠금이 풀린 다음 틱이 나머지를 맡는다.
    assert _expirer(repos, settings).run_once()["queued"] == 1
    assert _job(db, j_locked)["artifacts_expired_at"] == NOW


# ---- 정리(같은 길) ----

class Clock:
    def __init__(self):
        self.offset = 0

    def __call__(self):
        return iso_plus(utc_now_iso(), self.offset)


@pytest.fixture
def env(db, tmp_path, settings):
    base = tmp_path / "artifacts"
    base.mkdir()
    base.chmod(0o755)
    st = dataclasses.replace(settings, artifact_base_uri=f"file://{base}", request_purge_interval_seconds=INTERVAL)
    repos = Repositories(db)
    k8s = FakeK8s()
    runner = PurgeRunner(k8s, namespace="dms", job_image="reg/job:1")
    clock = Clock()
    repos.agents.ingest("dms-w1", {"artifact_base": {"path": str(base), "exists": True, "writable": True}})
    e = SimpleNamespace(db=db, base=base, settings=st, repos=repos, k8s=k8s, runner=runner, clock=clock, tmp=tmp_path)
    e.tick = lambda: RequestPurger(repos, runner, settings=st, clock=clock).run_once()
    e.expire = lambda: ArtifactExpirer(repos, settings=st, clock=clock).run_once()
    return e


def _old():
    return iso_plus(utc_now_iso(), -1501 * DAY)


def _with_files(e, *, k8s=True, uri_base=None):
    """종단 뒤 1501일 지난 잡 + 실제 결과 파일(<base>/<job_id>/execution/…) + (선택) 남은 k8s 객체."""
    where = uri_base or e.base
    rid, jid = _mk(e.repos, e.db, updated_at=_old())
    e.db.execute("UPDATE data_jobs SET artifact_uri = :u WHERE job_id = :j", {"u": f"file://{where}/{jid}", "j": jid})
    d = where / jid / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("out")
    (d / "summary.json").write_text("{}")
    (where / jid).chmod(0o755)
    e.repos.observability.record_event(component="stepper", severity="info", event_type="x", request_id=rid)
    if k8s:
        vc = f"dms-scan-execution-{jid[:12]}"
        e.k8s.add_vcjob(vc, {"dms.io/job-id": jid})
        e.k8s.add_pod(f"{vc}-worker-0", {"dms.io/job-id": jid, "volcano.sh/job-name": vc}, phase="Succeeded")
    return rid, jid


def _drive(e, rid, *, ticks=12):
    for _ in range(ticks):
        e.tick()
        if _outbox(e.db, rid) is None:
            return
        e.k8s.run_purge_pods(e.base)
        e.clock.offset += 1
    raise AssertionError(f"not finished: {_outbox(e.db, rid)}")


def _counts(db, rid, jid):
    def n(sql, p):
        return db.query_one(sql, p)["n"]
    return {"requests": n("SELECT COUNT(*) AS n FROM requests WHERE request_id = :r", {"r": rid}),
            "data_jobs": n("SELECT COUNT(*) AS n FROM data_jobs WHERE job_id = :j", {"j": jid}),
            "results": n("SELECT COUNT(*) AS n FROM results WHERE request_id = :r", {"r": rid}),
            "plans": n("SELECT COUNT(*) AS n FROM plans WHERE request_id = :r", {"r": rid}),
            "transitions": n("SELECT COUNT(*) AS n FROM state_transitions WHERE entity_id IN (:r, :j)",
                             {"r": rid, "j": jid})}


def test_full_flow_removes_only_files_and_keeps_every_row(env):
    e = env
    rid, jid = _with_files(e)
    before = _counts(e.db, rid, jid)
    summary_before = _job(e.db, jid)["result_summary"]
    assert e.expire() == {"backlog": 0, "queued": 1}
    assert (e.base / jid).is_dir()                         # 고르기·표시는 FS 무접촉
    _drive(e, rid)
    assert not (e.base / jid).exists() and not (e.base / TRASH / jid).exists()
    assert _outbox(e.db, rid) is None
    assert _counts(e.db, rid, jid) == before                # 행은 하나도 지우지 않는다
    job = _job(e.db, jid)
    assert job["state"] == "Succeeded" and job["result_summary"] == summary_before
    assert job["artifacts_expired_at"] is not None
    assert {n for k, n in e.k8s.deleted if k == "Pod"} >= {f"dms-scan-execution-{jid[:12]}-worker-0"}
    # 요청에 단 완료 이벤트 1건(요청이 살아 있다), 요청 삭제 이벤트는 없다, 요청의 기존 이벤트는 scrub 하지 않는다.
    ev = e.db.query("SELECT * FROM events WHERE event_type = 'artifacts_expired'")
    assert len(ev) == 1 and ev[0]["request_id"] == rid and ev[0]["component"] == "artifact-expiry"
    assert json.loads(ev[0]["payload"])["outcomes"] == {jid: "deleted"}
    assert e.db.query("SELECT * FROM events WHERE event_type = 'request_purged'") == []
    assert e.db.query_one("SELECT COUNT(*) AS n FROM events WHERE request_id = :r AND event_type = 'x'",
                          {"r": rid})["n"] == 1
    # 끝난 행은 다시 고르지 않는다.
    assert e.expire() == {"backlog": 0, "queued": 0}


def test_expiry_and_deletion_share_the_single_purge_pod(env):
    e = env
    r_exp, j_exp = _with_files(e, k8s=False)
    r_del, j_del = _with_files(e, k8s=False)
    e.expire()                                               # 둘 다 오래됐다 -- 둘 다 만료 대기
    # 하나는 그 사이 관리자가 지운다(만료 행 → 요청 삭제 행으로 바뀐다)
    assert e.repos.request_purges.delete_terminal(r_del, actor="opadm", artifact_base=str(e.base),
                                                  quiet_seconds=60)["deleted"] is True
    assert _outbox(e.db, r_del)["kind"] is None and _outbox(e.db, r_exp)["kind"] == EXPIRY_KIND
    e.tick()
    (pod,) = e.k8s.purge_pods().values()                    # 전역 purge 파드 하나에 둘 다
    assert sorted(pod["spec"]["containers"][0]["command"][4:]) == sorted([j_exp, j_del])
    e.k8s.run_purge_pods(e.base)
    e.clock.offset += 1
    e.tick()
    assert _outbox(e.db, r_exp) is None and _outbox(e.db, r_del) is None
    assert not (e.base / j_exp).exists() and not (e.base / j_del).exists()
    assert _job(e.db, j_exp) is not None and _job(e.db, j_del) is None      # 만료 = 행 유지, 삭제 = 행 삭제
    got = {(r["event_type"], r["request_id"]) for r in e.db.query(
        "SELECT event_type, request_id FROM events WHERE event_type IN ('artifacts_expired', 'request_purged')")}
    assert got == {("artifacts_expired", r_exp), ("request_purged", None)}


def test_restored_db_without_the_mark_leaves_files_untouched(env):
    e = env
    rid, jid = _with_files(e)
    e.expire()
    e.db.execute("UPDATE data_jobs SET artifacts_expired_at = NULL WHERE job_id = :j", {"j": jid})   # DB 복원
    assert e.tick()[rid] == "failed:artifact_expiry_target_invalid"
    row = _outbox(e.db, rid)
    assert row["stage"] == "k8s" and row["last_error"] == "artifact_expiry_target_invalid"
    assert (e.base / jid / "execution" / "stdout.log").is_file()             # FS 무접촉
    assert e.k8s.deleted == [] and not e.k8s.purge_pods()                     # k8s 무접촉
    assert e.repos.request_purges.expiry_status()["last_error"] == "artifact_expiry_target_invalid"
    # 표시가 돌아오면(운영자 판단) 백오프 뒤 이어서 끝난다.
    e.db.execute("UPDATE data_jobs SET artifacts_expired_at = :t WHERE job_id = :j", {"t": utc_now_iso(), "j": jid})
    e.clock.offset += 3600
    _drive(e, rid)
    assert not (e.base / jid).exists()


def test_purging_row_whose_mark_disappears_is_not_finished(env):
    e = env
    rid, jid = _with_files(e, k8s=False)
    e.expire()
    e.tick()
    assert _outbox(e.db, rid)["stage"] == "purging"
    e.db.execute("UPDATE data_jobs SET artifacts_expired_at = NULL WHERE job_id = :j", {"j": jid})
    e.k8s.run_purge_pods(e.base)
    e.clock.offset += 1
    e.tick()
    row = _outbox(e.db, rid)
    assert row is not None and row["last_error"] == "artifact_expiry_target_invalid"
    assert e.db.query("SELECT * FROM events WHERE event_type = 'artifacts_expired'") == []


def test_job_written_under_an_old_base_is_left_there(env):
    e = env
    old_base = e.tmp / "old-base"
    old_base.mkdir()
    rid, jid = _with_files(e, k8s=False, uri_base=old_base)
    e.expire()
    _drive(e, rid)
    assert (old_base / jid / "execution" / "stdout.log").is_file()           # 옛 base 는 열지도 지우지도 않는다
    ev = e.db.query("SELECT * FROM events WHERE event_type = 'artifact_left_at_old_base'")
    assert len(ev) == 1 and json.loads(ev[0]["payload"])["jobs"][0]["job_id"] == jid
    (done,) = e.db.query("SELECT payload FROM events WHERE event_type = 'artifacts_expired'")
    assert json.loads(done["payload"])["outcomes"] == {jid: "left_old_base"}
    assert _job(e.db, jid)["artifacts_expired_at"] is not None              # 표시는 남는다(열람은 원래도 지금 base)


def test_deleting_an_expiring_request_turns_the_row_into_a_deletion(env):
    e = env
    rid, jid = _with_files(e)
    e.expire()
    e.tick()                                                                  # 만료 행이 purging 까지
    assert _outbox(e.db, rid)["stage"] == "purging"
    r = e.repos.request_purges.delete_terminal(rid, actor="opadm", artifact_base=str(e.base), quiet_seconds=60)
    assert r["deleted"] is True
    row = _outbox(e.db, rid)
    assert row["kind"] is None and row["stage"] == "k8s" and row["requested_by"] == "opadm"
    assert [j["job_id"] for j in json.loads(row["jobs"])] == [jid]
    _drive(e, rid)
    assert not (e.base / jid).exists() and not (e.base / TRASH / jid).exists()
    assert e.db.query_one("SELECT COUNT(*) AS n FROM events WHERE request_id = :r", {"r": rid})["n"] == 0  # scrub
    assert e.db.query("SELECT * FROM events WHERE event_type = 'request_purged'") != []
    assert e.db.query("SELECT * FROM events WHERE event_type = 'artifacts_expired'") == []


def test_tampered_kind_stops_the_row(env):
    e = env
    rid, jid = _with_files(e)
    e.expire()
    e.db.execute("UPDATE request_purges SET kind = 'wipe' WHERE request_id = :r", {"r": rid})
    assert e.tick()[rid] == "failed:purge_row_invalid"
    assert (e.base / jid).is_dir() and e.k8s.deleted == []


# ---- 배선 ----

def test_loop_is_wired_only_with_the_purge_runner(db, settings):
    with_runner = build_loops(settings, Repositories(db), purge_runner=StubPurgeRunner())
    assert ("artifact-expiry", INTERVAL_SECONDS) in [(l.name, l.interval_seconds) for l in with_runner]
    assert "artifact-expiry" not in [l.name for l in build_loops(settings, Repositories(db))]


def test_loop_tick_actually_marks_through_build_loops(db, settings):
    repos = Repositories(db)
    _, jid = _mk(repos, db, updated_at="2000-01-01T00:00:00Z")
    loops = [l for l in build_loops(settings, repos, purge_runner=StubPurgeRunner()) if l.name == "artifact-expiry"]
    assert run_all_once(loops, repos, holder="h1") == {"artifact-expiry": "ok"}
    assert _job(db, jid)["artifacts_expired_at"] is not None
