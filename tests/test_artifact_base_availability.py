"""2026-11-23 base 가용성 판정 코어(artifact_base.py): availability_check·recent_job_ids·posture_problem·covering_fstype·
BaseCheckRunner·NodeNameResolver·controller_check_once 의 잡 판정 + 노드, 그리고 사유 코드 그물."""
import json
import os
import threading
import time
from pathlib import Path

import pytest

from dms.artifact_base import (ARTIFACT_BASE_CHECK_REASONS, ARTIFACT_BASE_UNAVAILABLE, BaseCheckRunner,
                               NodeNameResolver, RECENT_JOB_WINDOW, api_view_check, availability_check,
                               controller_check_once, covering_fstype, is_timeout, normalize_api_check,
                               posture_problem, recent_job_ids)
from dms.domain import DataJobState, RequestState
from dms.repositories import Repositories

REASON_CODES_FILE = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "reasonCodes.json"
J1, J2, J3 = "a" * 32, "b" * 32, "c" * 32


def _base(tmp_path, name="base"):
    base = tmp_path / name
    base.mkdir()
    base.chmod(0o755)
    return base


# ---- 사유 코드 그물 ----------------------------------------------------------------------------------------------------

def test_check_reasons_are_in_the_checked_in_list():
    # return·dict 로 흐르는 점검 사유는 AST 그물(detail=/reason_code=) 밖이다 -- 여기서 따로 고정한다(preflight 마커와 같은 방식).
    known = set(json.loads(REASON_CODES_FILE.read_text()))
    assert sorted((ARTIFACT_BASE_CHECK_REASONS | {ARTIFACT_BASE_UNAVAILABLE}) - known) == []


# ---- availability_check -----------------------------------------------------------------------------------------------

def test_availability_missing_and_not_directory(tmp_path):
    assert availability_check(str(tmp_path / "nope"), []) == {
        "ok": False, "reason": "artifact_base_missing", "jobs_checked": 0, "jobs_found": None}
    f = tmp_path / "file"
    f.write_text("x")
    assert availability_check(str(f), [])["reason"] == "artifact_base_not_directory"


@pytest.mark.skipif(os.geteuid() == 0, reason="root 는 mode 000 디렉터리도 연다(cap 이 있으면)")
def test_availability_unreadable_directory(tmp_path):
    base = _base(tmp_path)
    base.chmod(0o000)
    try:
        assert availability_check(str(base), [J1])["reason"] == "artifact_base_unreadable"
    finally:
        base.chmod(0o755)


def test_availability_without_recent_jobs_skips_the_job_judgement(tmp_path):
    # 대상 잡이 0건이면 판정 생략 -- 모름 ≠ 실패(새 설치·force 변경 직후). 빈 디렉터리도 ok.
    base = _base(tmp_path)
    assert availability_check(str(base), []) == {"ok": True, "reason": None, "jobs_checked": 0, "jobs_found": None}


def test_availability_jobs_missing_is_the_incident_signature(tmp_path):
    # 미마운트 노드의 빈 로컬 디렉터리: DB 에 최근 잡이 있는데 그 디렉터리가 하나도 없다.
    base = _base(tmp_path)
    assert availability_check(str(base), [J1, J2]) == {
        "ok": False, "reason": "artifact_base_jobs_missing", "jobs_checked": 2, "jobs_found": 0}


def test_availability_one_job_dir_is_enough(tmp_path):
    base = _base(tmp_path)
    (base / J2).mkdir()
    assert availability_check(str(base), [J1, J2, J3]) == {
        "ok": True, "reason": None, "jobs_checked": 3, "jobs_found": 1}


def test_availability_does_not_follow_symlinks_or_count_files(tmp_path):
    # fstatat(AT_SYMLINK_NOFOLLOW) + S_ISDIR: 심링크·정규 파일은 「있음」이 아니다.
    base = _base(tmp_path)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (base / J1).symlink_to(target)
    (base / J2).write_text("not a dir")
    assert availability_check(str(base), [J1, J2])["reason"] == "artifact_base_jobs_missing"


def test_availability_ignores_tampered_job_ids(tmp_path):
    # DB 가 신뢰 경계 -- JOB_ID_RE 밖의 id 는 fstatat 하지 않는다(../ 로 base 밖을 보지 않게). 남는 게 없으면 판정 생략.
    base = _base(tmp_path)
    assert availability_check(str(base), ["../etc", "x" * 32, None])["jobs_found"] is None


def test_availability_writes_nothing(tmp_path):
    base = _base(tmp_path)
    before = sorted(os.listdir(base))
    availability_check(str(base), [J1])
    api_view_check(str(base), [J1], ())
    assert sorted(os.listdir(base)) == before


# ---- recent_job_ids ---------------------------------------------------------------------------------------------------

def _job(repos, *, state=DataJobState.SUCCEEDED, artifact_uri=None, updated_at=None):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
                                resource_key=f"k{time.monotonic_ns()}", payload={"storage": "s1", "target": "t"},
                                priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid", storage_name="s1",
                                     target="t", options={}, tool="dscan", worker_pool={}, precondition={},
                                     actor="planner")
    if artifact_uri is not None:
        repos.data_jobs.set_artifact(jid, artifact_uri=artifact_uri.replace("{jid}", jid), result_summary={})
    repos.data_jobs.set_job_state(jid, state, actor="stepper")
    if updated_at is not None:
        repos.db.execute("UPDATE data_jobs SET updated_at = :u WHERE job_id = :j", {"u": updated_at, "j": jid})
    return jid


def test_recent_job_ids_exact_base_match_newest_first(db):
    repos = Repositories(db)
    old = _job(repos, artifact_uri="file:///b/x/{jid}", updated_at="2026-01-01T00:00:00Z")
    new = _job(repos, artifact_uri="file:///b/x/{jid}", updated_at="2026-02-01T00:00:00Z")
    # LIKE 함정: '_' 가 와일드카드면 file:///b/x_ 아래 잡이 섞인다 -- 정확 일치라 안 섞인다
    _job(repos, artifact_uri="file:///b/x_/{jid}", updated_at="2026-03-01T00:00:00Z")
    _job(repos, artifact_uri="file:///b/x/{jid}/nested", updated_at="2026-03-02T00:00:00Z")
    _job(repos, state=DataJobState.FAILED, artifact_uri="file:///b/x/{jid}", updated_at="2026-03-03T00:00:00Z")
    _job(repos, updated_at="2026-03-04T00:00:00Z")                         # artifact_uri NULL
    assert recent_job_ids(repos, "file:///b/x") == [new, old]
    assert recent_job_ids(repos, "file:///b/x", limit=1) == [new]
    assert recent_job_ids(db, "file:///b/x") == [new, old]                 # Database 도 받는다


def test_identity_job_ids_only_on_the_real_execution_backend(db):
    # stub 백엔드(개발·e2e)는 잡을 성공시키고 artifact_uri 를 남기지만 <base>/<job_id> 를 만들지 않는다 -- 거기서 교차
    # 확인하면 멀쩡한 base 가 jobs_missing 이 돼 아티팩트 열람이 전부 503 이 된다(2026-11-23 리뷰).
    from types import SimpleNamespace
    from dms.artifact_base import identity_job_ids
    repos = Repositories(db)
    jid = _job(repos, artifact_uri="file:///b/x/{jid}")
    assert identity_job_ids(repos, SimpleNamespace(execution_backend="stub"), "file:///b/x") == []
    assert identity_job_ids(repos, SimpleNamespace(execution_backend="volcano"), "file:///b/x") == [jid]
    assert identity_job_ids(repos, SimpleNamespace(), "file:///b/x") == [jid]    # 필드 없는 설정 스텁 = volcano


def test_identity_job_ids_skip_jobs_created_before_the_last_base_change(db):
    # 2026-11-23 리뷰: force 변경 때 진행 중이던 잡은 옛 base 에 쓰지만 확정 시점의 새 base 로 artifact_uri 가 남는다 --
    # 그 잡을 표본에 넣으면 멀쩡한 새 base 가 jobs_missing 이다. 마지막 변경(감사 set 행) 뒤에 만든 잡만 쓴다.
    from types import SimpleNamespace
    from dms.artifact_base import identity_job_ids
    repos = Repositories(db)
    before = _job(repos, artifact_uri="file:///b/x/{jid}")
    repos.db.execute("UPDATE data_jobs SET created_at = '2026-01-01T00:00:00Z', updated_at = '2026-01-01T00:00:00Z'"
                     " WHERE job_id = :j", {"j": before})
    repos.control.set_artifact_base("file:///b/x", actor="admin", forced=True, affected_jobs=1)
    repos.db.execute("UPDATE audit_log SET at = '2026-02-01T00:00:00Z' WHERE mutation_class = 'artifact_base'")
    after = _job(repos, artifact_uri="file:///b/x/{jid}")
    vol = SimpleNamespace(execution_backend="volcano")
    assert identity_job_ids(repos, vol, "file:///b/x") == [after]
    assert recent_job_ids(repos, "file:///b/x") == [after, before]          # since 없이 부르면 그대로(하위 호환)


def test_recent_job_ids_window_bounds_the_scan(db):
    # force 로 base 를 바꾼 직후: 최근 성공 잡 창 안에 현재 base 의 잡이 없으면 판정 생략(빈 목록)이지 전체를 훑지 않는다.
    repos = Repositories(db)
    _job(repos, artifact_uri="file:///new/{jid}", updated_at="2020-01-01T00:00:00Z")
    for i in range(RECENT_JOB_WINDOW):
        db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, options, priority, state, artifact_uri,
                          created_at, updated_at)
                      VALUES (:j, 'r', 'scan', '{}', 'mid', 'Succeeded', :u, :t, :t)""",
                   {"j": f"{i:032x}", "u": f"file:///old/{i:032x}", "t": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z"})
    assert recent_job_ids(repos, "file:///new") == []


def test_recent_job_ids_reads_the_state_index_without_a_sort(db):
    # data_jobs 가 수만 행이어도 비용 일정 -- idx_data_jobs_state(state, updated_at) 를 순서대로 읽는다(임시 정렬 B-tree 없음).
    captured = []
    orig = db.query

    def spy(sql, params=None):
        if "FROM data_jobs WHERE state" in sql:
            captured.append((sql, params))
        return orig(sql, params)

    db.query = spy
    recent_job_ids(db, "file:///b")
    db.query = orig
    ((sql, params),) = captured
    plan = " | ".join(r["detail"] for r in db.query("EXPLAIN QUERY PLAN " + sql, params))
    assert "idx_data_jobs_state" in plan and "TEMP B-TREE" not in plan, plan


# ---- posture / fstype -------------------------------------------------------------------------------------------------

def test_posture_problem_reads_mode_without_writing(tmp_path):
    base = _base(tmp_path)
    assert posture_problem(str(base), ()) is None
    base.chmod(0o775)
    assert posture_problem(str(base), ()) == "artifact_base_group_writable"
    base.chmod(0o757)
    assert posture_problem(str(base), ()) == "artifact_base_world_writable"
    base.chmod(0o755)
    assert posture_problem(str(base), (str(tmp_path / "other"),)) == "artifact_base_outside_allowlist"
    assert posture_problem(str(tmp_path / "missing"), ()) is None        # 존재는 availability 몫
    assert not list(base.glob(".dms-base-check-*"))


def test_covering_fstype_from_mountinfo(tmp_path):
    mi = tmp_path / "mountinfo"
    mi.write_text("22 1 0:21 / / rw,relatime - overlay overlay rw\n"
                  "30 22 0:40 / /cephfs rw,relatime - ceph 10.0.0.1:/ rw\n"
                  "31 22 8:1 /local /cephfs/local rw - ext4 /dev/sda1 rw\n")
    assert covering_fstype("/cephfs/dms/artifacts", mountinfo_path=str(mi)) == "ceph"
    assert covering_fstype("/cephfs/local/x", mountinfo_path=str(mi)) == "ext4"
    assert covering_fstype("/srv", mountinfo_path=str(mi)) == "overlay"
    assert covering_fstype("/srv", mountinfo_path=str(tmp_path / "missing")) is None


def test_mountinfo_parser_moved_without_changing_agent_probes():
    # 중립 모듈로 옮겼어도 에이전트 쪽 이름은 같은 객체다(동작 불변 -- test_agent_host_root 가 동작을 고정).
    from dms import mountinfo
    from dms.agent import probes
    assert probes.parse_mount_table is mountinfo.parse_mount_table
    assert probes.parse_mountinfo is mountinfo.parse_mountinfo
    assert probes._covering_mount is mountinfo.covering_mount


def test_api_view_check_shape_and_local_fs(tmp_path):
    base = _base(tmp_path)
    out = api_view_check(str(base), [], ())
    assert out["availability"]["ok"] is True and out["posture"] is None
    assert set(out) == {"availability", "posture", "fstype", "local_fs"}


# ---- BaseCheckRunner --------------------------------------------------------------------------------------------------

def _runner_threads():
    return [t for t in threading.enumerate() if t.name == "dms-base-check" and t.is_alive()]


def test_runner_single_flight_joins_the_inflight_call():
    # 동시 호출은 진행 중인 하나에 합류한다 -- 서로를 timeout 으로 오진하지 않고, fn 은 한 번만 돈다.
    runner = BaseCheckRunner()
    gate, calls, results = threading.Event(), [], []

    def fn():
        calls.append(1)
        gate.wait(5)
        return {"ok": True}

    threads = [threading.Thread(target=lambda: results.append(runner.run("k", fn, timeout=5)))
               for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(5)
    assert results == [{"ok": True}] * 5
    assert calls == [1]


def test_runner_stuck_call_times_out_without_spawning_threads():
    runner = BaseCheckRunner()
    release = threading.Event()
    before = len(_runner_threads())
    first = runner.run("k", lambda: release.wait(10), timeout=0.1)
    assert is_timeout(first) and first["reason"] == "artifact_base_check_timeout" and first["stuck_since"]
    assert runner.stuck_since("k", timeout=0.1) is not None and runner.busy("k")
    t0 = time.monotonic()
    again = [runner.run("k", lambda: {"ok": True}, timeout=0.1) for _ in range(20)]
    assert all(is_timeout(r) for r in again)
    assert time.monotonic() - t0 < 0.5                     # 즉시 -- 멈춘 호출을 다시 기다리지 않는다
    assert len(_runner_threads()) - before == 1            # 키당 멈춘 스레드는 하나뿐
    release.set()
    time.sleep(0.2)
    assert runner.stuck_since("k") is None and not runner.busy("k")
    # 풀린 뒤에는 정상 결과(캐시 없이)
    assert runner.run("k", lambda: {"ok": True}, timeout=1, cache_seconds=0) == {"ok": True}


def test_runner_caches_briefly_and_cache_zero_rechecks():
    clock = [100.0]
    runner = BaseCheckRunner(clock=lambda: clock[0])
    n = [0]

    def fn():
        n[0] += 1
        return {"n": n[0]}

    assert runner.peek("k") is None                        # 아직 결과 없음 -- peek 은 점검을 시작하지 않는다
    assert runner.run("k", fn) == {"n": 1}
    assert runner.run("k", fn) == {"n": 1}                 # 3초 캐시
    assert runner.peek("k") == {"n": 1} and runner.peek("k", cache_seconds=0) is None
    assert runner.run("k", fn, cache_seconds=0) == {"n": 2}
    clock[0] += 10
    assert runner.peek("k") is None
    assert runner.run("k", fn) == {"n": 3}
    assert n[0] == 3


def test_runner_reraises_fn_errors_instead_of_masking_them():
    runner = BaseCheckRunner()

    def boom():
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        runner.run("k", boom)
    assert runner.run("k", lambda: {"ok": True}) == {"ok": True}   # 실패는 캐시되지 않는다


def test_runner_thread_start_failure_does_not_wedge_the_key(monkeypatch):
    # 2026-11-23 리뷰: 스레드 생성 실패(pids 한도 등)로 등록만 남으면 done 이 영영 안 서서 그 키가 「멈춤」으로 굳고
    # 아티팩트 라우트가 재시작까지 503 이었다 -- 실패하면 등록을 되돌리고, 다음 호출은 새 점검을 띄운다.
    import threading
    runner = BaseCheckRunner()
    real_start = threading.Thread.start
    failed = []

    def flaky_start(self):
        if self.name == "dms-base-check" and not failed:
            failed.append(1)
            raise RuntimeError("can't start new thread")
        return real_start(self)

    monkeypatch.setattr(threading.Thread, "start", flaky_start)
    with pytest.raises(RuntimeError):
        runner.run("k", lambda: {"ok": True})
    assert runner.stuck_since("k", timeout=0) is None
    assert runner.run("k", lambda: {"ok": True}) == {"ok": True}


def test_normalize_api_check_turns_timeout_into_availability_failure():
    out = normalize_api_check({"ok": False, "reason": "artifact_base_check_timeout", "timed_out": True,
                               "stuck_since": "2026-11-23T00:00:00Z"})
    assert out["availability"] == {"ok": False, "reason": "artifact_base_check_timeout",
                                   "jobs_checked": 0, "jobs_found": None}
    assert out["posture"] is None and out["stuck_since"] == "2026-11-23T00:00:00Z"


# ---- NodeNameResolver -------------------------------------------------------------------------------------------------

class _Briefs:
    def __init__(self, briefs):
        self.briefs = briefs
        self.calls = 0

    def pod_briefs(self, *, selector):
        self.calls += 1
        return self.briefs


def test_node_name_env_first_then_own_pod_lookup_cached():
    r = _Briefs([{"name": "other", "node": "n9"}, {"name": "dms-api-1", "node": "n2"}])
    assert NodeNameResolver(r, "sel", environ={"DMS_NODE_NAME": "n1"}).get() == "n1"
    res = NodeNameResolver(r, "sel", environ={}, hostname="dms-api-1")
    assert res.get() == "n2" and res.get() == "n2"
    assert r.calls == 1                                    # 성공은 영구 캐시


def test_node_name_failure_is_none_and_retried_after_ttl():
    clock = [0.0]
    r = _Briefs([])
    res = NodeNameResolver(r, "sel", environ={}, hostname="h", clock=lambda: clock[0], ttl_seconds=60)
    assert res.get() is None and res.get() is None
    assert r.calls == 1
    clock[0] += 61
    r.briefs = [{"name": "h", "node": "n3"}]
    assert res.get() == "n3"


# ---- controller_check_once --------------------------------------------------------------------------------------------

class _CtlSettings:
    artifact_base_uri = None
    artifact_base_allowed_prefixes = ()


def test_controller_check_reports_jobs_missing_and_node(db, tmp_path):
    # 쓰기 왕복은 빈 로컬 디렉터리에서도 통과한다 -- 최근 잡 디렉터리 판정이 그 사실을 드러낸다(표시 전용).
    repos = Repositories(db)
    base = _base(tmp_path)
    settings = _CtlSettings()
    settings.artifact_base_uri = f"file://{base}"
    jid = _job(repos, artifact_uri=f"file://{base}/{{jid}}")
    out = controller_check_once(repos, settings, runner=BaseCheckRunner(), node_name="w7")
    assert out == {"uri": f"file://{base}", "ok": False, "reason": "artifact_base_jobs_missing"}
    row = repos.control.control_state()
    assert (row["artifact_base_check_ok"], row["artifact_base_check_reason"], row["artifact_base_check_node"]) == (
        0, "artifact_base_jobs_missing", "w7")
    (base / jid).mkdir()
    out = controller_check_once(repos, settings, runner=BaseCheckRunner())
    assert out["ok"] is True
    assert repos.control.control_state()["artifact_base_check_node"] is None      # 모름은 NULL


def test_controller_check_timeout_does_not_hang_the_loop(db, tmp_path, monkeypatch):
    # 컨트롤러는 모든 루프를 한 스레드로 돈다 -- 멈춘 마운트의 점검은 runner 마감에서 끊고 사유로 남긴다.
    import dms.artifact_base as ab
    repos = Repositories(db)
    base = _base(tmp_path)
    settings = _CtlSettings()
    settings.artifact_base_uri = f"file://{base}"
    release = threading.Event()
    monkeypatch.setattr(ab, "roundtrip_artifact_base", lambda path: release.wait(10) and None)
    monkeypatch.setattr(ab, "CHECK_TIMEOUT_SECONDS", 0.1)
    runner = BaseCheckRunner()
    original = runner.run
    runner.run = lambda key, fn, **kw: original(key, fn, **{**kw, "timeout": 0.1})
    try:
        out = controller_check_once(repos, settings, runner=runner)
        assert out["reason"] == "artifact_base_check_timeout"
        assert repos.control.control_state()["artifact_base_check_ok"] == 0
    finally:
        release.set()
