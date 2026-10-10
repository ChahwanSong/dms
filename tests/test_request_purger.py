"""컨트롤러 request-purge 루프(request_purger.py) -- 단계 진행·대기·실패·크래시 안전성·purge 파드·base 변경.

실 저장소(sqlite) + 실 PurgeRunner + 가짜 k8s(tests/fake_purge_k8s.py -- purge 파드의 rm 을 shutil 로 흉내) + 실 임시
base(artifact_trash 가 진짜 rename 한다). 요청은 실제 삭제 트랜잭션(delete_terminal)으로 지워 아웃박스 행을 만든다."""
import dataclasses
import itertools
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from dms.artifact_trash import TRASH
from dms.controller import build_loops, run_all_once
from dms.db import iso_epoch, iso_plus, utc_now_iso
from dms.domain import DataJobState, RequestState
from dms.purge_runner import PURGE_POD_STUCK_SECONDS, PurgeRunner, StubPurgeRunner, _purge_script
from dms.repositories import Repositories
from dms.request_purger import (ISOLATED_TURN_SECONDS, WAITING_BACKOFF_MAX_SECONDS, WAITING_PODS_STALL_SECONDS,
                                RequestPurger)
from fake_purge_k8s import FakeK8s

OLD = "2026-01-01T00:00:00Z"
INTERVAL = 15


class Clock:
    def __init__(self):
        self.offset = 0

    def __call__(self):
        return iso_plus(utc_now_iso(), self.offset)


def _mkbase(path):
    path.mkdir()
    path.chmod(0o755)
    return path


@pytest.fixture
def env(db, tmp_path, settings):
    base = _mkbase(tmp_path / "artifacts")
    st = dataclasses.replace(settings, artifact_base_uri=f"file://{base}",
                             request_purge_interval_seconds=INTERVAL)
    repos = Repositories(db)
    k8s = FakeK8s()
    runner = PurgeRunner(k8s, namespace="dms", job_image="reg/job:1")
    clock = Clock()
    repos.agents.ingest("dms-w1", {"artifact_base": {"path": str(base), "exists": True, "writable": True}})
    e = SimpleNamespace(db=db, base=base, settings=st, repos=repos, k8s=k8s, runner=runner, clock=clock,
                        tmp=tmp_path)
    e.purger = lambda **kw: RequestPurger(repos, kw.pop("runner", runner), settings=kw.pop("settings", st),
                                          clock=clock, **kw)
    e.tick = lambda **kw: e.purger(**kw).run_once()
    return e


def _artifact(base, jid):
    d = base / jid
    (d / "execution").mkdir(parents=True)
    (d / "execution" / "stdout.log").write_text("out")
    d.chmod(0o755)


def _seed_k8s(k8s, jid):
    vc = f"dms-scan-execution-{jid[:12]}"
    k8s.add_pod(f"dms-preflight-{jid[:12]}-preflight-dms-w1", {"dms.io/job-id": jid}, phase="Succeeded")
    k8s.add_vcjob(vc, {"dms.io/job-id": jid})
    k8s.add_pod(f"{vc}-worker-0", {"dms.io/job-id": jid, "volcano.sh/job-name": vc})
    k8s.add_pod(f"{vc}-launcher-0", {"volcano.sh/job-name": vc}, phase="Failed")
    return vc


def _deleted(e, *, jobs=1, artifact=True, k8s=True, refs=None, uri=None, base=None):
    """종단 요청(잡 jobs 개)을 만들고 실제 삭제 트랜잭션으로 지운다 -> (rid, [jid])."""
    repos = e.repos
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
                                resource_key=f"k-{os.urandom(4).hex()}",
                                payload={"storage": "s1", "target": "t", "run_as_root": True},
                                priority="mid", auth_method="session")
    jids = []
    if jobs:
        repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
        for _ in range(jobs):
            plan_id = repos.data_jobs.create_plan(rid, actor="planner")
            jid = repos.data_jobs.create_job(
                rid, plan_id, operation="scan", priority="mid", storage_name="s1", target="t", options={},
                tool="dscan", precondition={}, actor="planner",
                worker_pool={"identity": {"username": "alice", "uid": 10001, "gid": 10001, "privileged": False},
                             "candidates": {"primary": ["dms-w1"]}})
            for phase, ref in (refs if refs is not None else {
                    "preflight": f"pod/dms-preflight-{jid[:12]}-preflight-dms-w1",
                    "execution": f"vcjob/dms-scan-execution-{jid[:12]}"}).items():
                repos.data_jobs.set_phase_ref(jid, phase, ref.replace("<j12>", jid[:12]).replace("<jid>", jid))
            repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
            e.db.execute("UPDATE data_jobs SET artifact_uri = :u WHERE job_id = :j",
                         {"u": uri.replace("<jid>", jid) if uri else f"file://{e.base}/{jid}", "j": jid})
            if artifact:
                _artifact(e.base, jid)
            if k8s:
                _seed_k8s(e.k8s, jid)
            jids.append(jid)
        repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    else:
        repos.requests.set_state_with_result(rid, RequestState.REJECTED, reason_code="missing_policy",
                                             actor="planner")
    e.db.execute("UPDATE requests SET updated_at = :t WHERE request_id = :r", {"t": OLD, "r": rid})
    e.db.execute("UPDATE data_jobs SET updated_at = :t WHERE request_id = :r", {"t": OLD, "r": rid})
    r = repos.request_purges.delete_terminal(rid, actor="opadm", artifact_base=str(base or e.base),
                                             quiet_seconds=60)
    assert r["deleted"] is True, r
    return rid, jids


def _row(e, rid):
    return e.repos.request_purges.get(rid)


def _events(e, event_type=None):
    rows = e.db.query("SELECT * FROM events WHERE component = 'request-purge' ORDER BY id")
    for r in rows:
        assert r["request_id"] is None, r            # 모든 purge 이벤트는 request_id=NULL
        r["payload"] = json.loads(r["payload"]) if r["payload"] else None
    return [r for r in rows if event_type is None or r["event_type"] == event_type]


def _purge_pods(e):
    return e.k8s.purge_pods()


def _later(e, seconds=INTERVAL + 1):
    e.clock.offset += seconds


def _drive(e, rid, *, ticks=12):
    """파드를 실행해 가며 행이 끝날 때까지 틱을 돈다(상한 ticks)."""
    for _ in range(ticks):
        e.tick()
        if _row(e, rid) is None:
            return
        e.k8s.run_purge_pods(e.base)
        _later(e, 1)
    raise AssertionError(f"not finished: {_row(e, rid)}")


# ---- 정상 흐름 ----

def test_full_flow_k8s_then_files_then_purge_pod_then_finish(env):
    e = env
    rid, (jid,) = _deleted(e)
    vc = f"dms-scan-execution-{jid[:12]}"
    e.k8s.linger.add(("Pod", f"{vc}-launcher-0"))          # launcher 가 한 틱 동안 Terminating

    assert e.tick()[rid] == "k8s"
    row = _row(e, rid)
    assert row["stage"] == "k8s" and row["last_error"] is None and row["attempts"] == 0
    assert (e.base / jid).is_dir() and not (e.base / TRASH).exists()     # 객체가 남았으면 파일 무접촉
    assert not _purge_pods(e)
    assert {n for k, n in e.k8s.deleted if k == "Pod"} >= {f"dms-preflight-{jid[:12]}-preflight-dms-w1",
                                                           f"{vc}-launcher-0"}

    e.k8s.release()
    assert rid not in e.tick()             # 대기(defer) 중 -- 아직 due 아님
    _later(e)
    e.tick()
    row = _row(e, rid)
    assert row["stage"] == "purging"
    assert not (e.base / jid).exists() and (e.base / TRASH / jid).is_dir()
    (pod,) = _purge_pods(e).values()
    cmd = pod["spec"]["containers"][0]["command"]
    assert cmd[4:] == [jid]
    assert pod["spec"]["volumes"][0]["hostPath"]["path"] == str(e.base)
    assert pod["spec"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"][0]["matchExpressions"][0]["values"] == ["dms-w1"]

    e.k8s.run_purge_pods(e.base)
    assert e.tick()[rid] == "purged"
    assert _row(e, rid) is None
    assert not _purge_pods(e)                                 # 끝난 파드는 지운다
    (ev,) = _events(e, "request_purged")
    assert ev["severity"] == "info"
    assert ev["payload"] == {"request_id": rid, "job_ids": [jid], "outcomes": {jid: "deleted"}, "k8s_deleted": 4}
    assert _events(e, "purge_failed") == []


def test_scrub_on_finish_removes_late_rows(env):
    e = env
    rid, (jid,) = _deleted(e, artifact=False, k8s=False)
    # 삭제 뒤 늦게 들어온 행(경합 step_error·digest 재삽입 경로 흉내)
    e.repos.observability.record_event(component="stepper", severity="error", event_type="step_error",
                                       request_id=rid)
    e.db.execute("INSERT INTO scan_report_digests (job_id, digest, created_at) VALUES (:j, '{}', :t)",
                 {"j": jid, "t": OLD})
    e.tick()
    assert _row(e, rid) is None
    assert e.db.query_one("SELECT COUNT(*) AS n FROM events WHERE request_id = :r", {"r": rid})["n"] == 0
    assert e.db.query_one("SELECT COUNT(*) AS n FROM scan_report_digests WHERE job_id = :j", {"j": jid})["n"] == 0


def test_request_without_jobs_finishes_without_touching_the_base(env, tmp_path):
    e = env
    rid, jids = _deleted(e, jobs=0)
    gone = dataclasses.replace(e.settings, artifact_base_uri=f"file://{tmp_path}/nope")
    assert e.tick(settings=gone)[rid] == "purged"
    (ev,) = _events(e, "request_purged")
    assert ev["payload"]["job_ids"] == [] and ev["payload"]["outcomes"] == {}


def test_absent_artifact_dir_is_outcome_absent(env):
    e = env
    rid, (jid,) = _deleted(e, artifact=False, k8s=False)
    e.tick()
    assert _row(e, rid) is None and not _purge_pods(e)
    (ev,) = _events(e, "request_purged")
    assert ev["payload"]["outcomes"] == {jid: "absent"} and ev["payload"]["k8s_deleted"] == 0


def test_multi_job_request(env):
    e = env
    rid, jids = _deleted(e, jobs=2, k8s=False)
    e.tick()
    (pod,) = _purge_pods(e).values()
    assert pod["spec"]["containers"][0]["command"][4:] == sorted(jids)
    _drive(e, rid)
    assert _events(e, "request_purged")[0]["payload"]["outcomes"] == {j: "deleted" for j in jids}


# ---- k8s 단계: 대기·실패·ref 검증 ----

def test_terminating_pods_block_files_and_surface_after_ten_minutes(env):
    e = env
    rid, (jid,) = _deleted(e)
    vc = f"dms-scan-execution-{jid[:12]}"
    e.k8s.linger.add(("Pod", f"{vc}-launcher-0"))
    e.tick()
    first = _row(e, rid)
    assert first["last_error"] is None
    # 대기는 k8s 단계의 첫 스윕부터 잰다(outcomes 의 스탬프 -- requested_at 이 아니다, 2026-10-10).
    assert first["outcomes"]["k8s_waiting_since"] == first["updated_at"]
    # 첫 스윕부터 10분이 지났다 -- 계속 기다리되 지연으로 보인다
    _later(e, WAITING_PODS_STALL_SECONDS + 5)
    e.tick()
    row = _row(e, rid)
    assert row["stage"] == "k8s" and row["last_error"] == "purge_waiting_pods" and row["attempts"] == 0
    assert row["outcomes"]["k8s_waiting_since"] == first["outcomes"]["k8s_waiting_since"]    # 스윕마다 새로 찍지 않는다
    assert (e.base / jid).is_dir()
    # 지연이 된 대기 행은 매 틱 k8s 를 두드리지 않는다 -- 재확인 간격 = 기다린 시간의 1/10(상한 300초, 2026-10-09)
    waited = iso_epoch(e.clock()) - iso_epoch(first["outcomes"]["k8s_waiting_since"])
    assert iso_epoch(row["next_attempt_at"]) - iso_epoch(e.clock()) == min(int(waited) // 10,
                                                                           WAITING_BACKOFF_MAX_SECONDS)
    calls = len(e.k8s.calls)
    _later(e)
    e.tick()
    assert len(e.k8s.calls) == calls                         # 아직 due 가 아니다
    (ev,) = _events(e, "purge_failed")                       # 사유가 바뀔 때만 한 번
    assert ev["severity"] == "warning" and ev["payload"]["reason_code"] == "purge_waiting_pods"
    e.k8s.release()
    _later(e, WAITING_BACKOFF_MAX_SECONDS)
    e.tick()
    assert _row(e, rid)["stage"] == "purging" and _row(e, rid)["last_error"] is None
    assert "k8s_waiting_since" not in _row(e, rid)["outcomes"]          # 단계를 떠나면 지운다


def test_waiting_backoff_is_capped(env):
    e = env
    rid, (jid,) = _deleted(e)
    e.k8s.linger.add(("Pod", f"dms-scan-execution-{jid[:12]}-launcher-0"))
    e.tick()
    _later(e, 86400)                                                     # 하루째 Terminating
    e.tick()
    row = _row(e, rid)
    assert row["last_error"] == "purge_waiting_pods"
    assert iso_epoch(row["next_attempt_at"]) - iso_epoch(e.clock()) == WAITING_BACKOFF_MAX_SECONDS


def test_a_row_that_queued_long_is_not_a_false_stall_on_its_first_sweep(env):
    # 2026-10-10 검증 지적: 큰 배치 삭제는 아웃박스 행을 수천 개 만들고 루프는 틱당 ROW_LIMIT 행만 진행한다. 대기를
    # requested_at 부터 재면 10분 넘게 줄 선 행이 첫 스윕(방금 지운 파드가 아직 Terminating)에 곧장 purge_waiting_pods
    # 로 찍혀 툴바가 「지연 N건 -- 노드 상태를 확인하세요」를 보였다(1000자식 배치에서 260건, 파드는 한 틱 안에 끝났다).
    e = env
    rid, (jid,) = _deleted(e)
    e.k8s.linger.add(("Pod", f"dms-scan-execution-{jid[:12]}-launcher-0"))
    e.db.execute("UPDATE request_purges SET requested_at = :t, next_attempt_at = :t WHERE request_id = :r",
                 {"t": iso_plus(utc_now_iso(), -3600), "r": rid})          # 한 시간 동안 줄을 섰다
    e.tick()
    row = _row(e, rid)
    assert row["stage"] == "k8s" and row["last_error"] is None              # 지연이 아니다 -- 정상 대기
    assert _events(e, "purge_failed") == []
    assert iso_epoch(row["next_attempt_at"]) - iso_epoch(e.clock()) == INTERVAL
    e.k8s.release()
    _later(e)
    e.tick()
    assert _row(e, rid)["stage"] == "purging" and _row(e, rid)["last_error"] is None
    assert e.repos.request_purges.status()["stalled"] == 0


def test_returning_to_k8s_restarts_the_waiting_clock(env):
    # 파일 단계에서 k8s 로 되돌아가면(artifact_reappeared) 대기는 새로 잰다 -- 처음 k8s 단계의 스탬프(또는 변조로 남은
    # 낡은 값)로 재면 되돌아온 첫 스윕이 거짓 지연이 된다.
    e = env
    rid, (jid,) = _deleted(e, k8s=False)
    e.tick()
    assert _row(e, rid)["stage"] == "purging"
    e.db.execute("UPDATE request_purges SET outcomes = :o WHERE request_id = :r",
                 {"o": json.dumps({"k8s_waiting_since": OLD}), "r": rid})      # 낡은 스탬프(변조)
    _artifact(e.base, jid)
    e.tick()
    assert _row(e, rid)["stage"] == "k8s"
    assert "k8s_waiting_since" not in _row(e, rid)["outcomes"]
    _seed_k8s(e.k8s, jid)
    e.k8s.linger.add(("Pod", f"dms-scan-execution-{jid[:12]}-launcher-0"))
    _later(e)
    e.tick()
    row = _row(e, rid)
    assert row["stage"] == "k8s" and row["last_error"] is None, row


def test_a_tampered_waiting_stamp_counts_as_the_first_sweep(env):
    e = env
    rid, (jid,) = _deleted(e)
    e.k8s.linger.add(("Pod", f"dms-scan-execution-{jid[:12]}-launcher-0"))
    e.db.execute("UPDATE request_purges SET outcomes = :o WHERE request_id = :r",
                 {"o": json.dumps({"k8s_waiting_since": "not-a-time"}), "r": rid})
    e.tick()
    row = _row(e, rid)
    assert row["last_error"] is None and row["outcomes"]["k8s_waiting_since"] == row["updated_at"]


def test_rows_stuck_waiting_on_k8s_do_not_starve_newer_deletions(env, monkeypatch):
    # 2026-10-09 검증 지적: due 가 삭제 순(requested_at)이면 죽은 노드의 Terminating 파드를 기다리는 오래된 행이 상한만큼
    # 쌓였을 때 매 틱 그 행들이 상한을 채워, 기다릴 것이 없는 새 삭제가 k8s 단계조차 시작하지 못했다(조용히 -- 새 행은
    # last_error NULL). due 는 재시도 시각 순이라 방금 기다린 행은 뒤로 간다.
    e = env
    import dms.request_purger as rp
    monkeypatch.setattr(rp, "ROW_LIMIT", 2)
    stuck = []
    for i in range(2):
        rid, (jid,) = _deleted(e)
        for name in list(e.k8s.pods):
            if jid[:12] in name:
                e.k8s.linger.add(("Pod", name))
        e.k8s.linger.add(("Job", f"dms-scan-execution-{jid[:12]}"))
        e.db.execute("UPDATE request_purges SET requested_at = :t WHERE request_id = :r",
                     {"t": iso_plus(utc_now_iso(), -3600 + i), "r": rid})
        stuck.append(rid)
    fresh, _ = _deleted(e, artifact=False, k8s=False)                      # 기다릴 것이 없다
    for _ in range(3):
        e.tick()
        _later(e, INTERVAL)
        if _row(e, fresh) is None:
            break
    assert _row(e, fresh) is None, _row(e, fresh)
    assert all(_row(e, r)["stage"] == "k8s" for r in stuck)            # 막힌 행은 계속 기다린다(안전 > 진행)


def test_k8s_error_backs_off(env):
    e = env
    rid, _ = _deleted(e)
    e.k8s.fail["list_pod_briefs"] = RuntimeError("apiserver down")
    assert e.tick()[rid] == "failed:purge_k8s_failed"
    row = _row(e, rid)
    assert row["stage"] == "k8s" and row["attempts"] == 1 and row["last_error"] == "purge_k8s_failed"
    assert row["next_attempt_at"] > e.clock()
    calls = len(e.k8s.calls)
    e.tick()                                                  # 백오프 중 -- 손대지 않는다
    assert len(e.k8s.calls) == calls and _row(e, rid)["attempts"] == 1
    (ev,) = _events(e, "purge_failed")
    assert ev["severity"] == "warning" and ev["payload"]["stage"] == "k8s"
    e.k8s.fail.clear()
    _later(e)
    e.tick()
    assert _row(e, rid)["stage"] == "purging" and _row(e, rid)["attempts"] == 0


def test_backoff_doubles_and_caps(env):
    e = env
    rid, _ = _deleted(e)
    e.k8s.fail["list_pod_briefs"] = RuntimeError("down")
    delays = []
    for _ in range(8):
        now = e.clock()
        e.tick()
        nxt = _row(e, rid)["next_attempt_at"]
        delays.append(int(iso_epoch(nxt) - iso_epoch(now)))
        e.db.execute("UPDATE request_purges SET next_attempt_at = :t WHERE request_id = :r",
                     {"t": e.clock(), "r": rid})
    assert delays[:7] == [15, 30, 60, 120, 240, 480, 900] and delays[7] == 900
    assert len(_events(e, "purge_failed")) == 1


def test_ref_not_named_for_the_job_is_not_terminated(env):
    e = env
    e.k8s.add_pod("dms-api-7d9f8b7c6-abcde", {"app": "dms-api"})
    e.k8s.add_vcjob("dms-scan-execution-ffffffffffff", {"dms.io/job-id": "f" * 32})
    rid, (jid,) = _deleted(e, refs={"preflight": "pod/dms-api-7d9f8b7c6-abcde",
                                    "execution": "vcjob/dms-scan-execution-ffffffffffff"})
    e.tick()
    assert "dms-api-7d9f8b7c6-abcde" in e.k8s.pods and "dms-scan-execution-ffffffffffff" in e.k8s.vcjobs
    assert ("Pod", "dms-api-7d9f8b7c6-abcde") not in e.k8s.deleted
    # 그 잡의 실제 객체는 라벨 스윕이 회수했다
    assert not any(p["labels"].get("dms.io/job-id") == jid for p in e.k8s.pods.values())
    (ev,) = _events(e, "purge_ref_rejected")
    assert ev["severity"] == "warning"
    assert sorted(r["ref"] for r in ev["payload"]["refs"]) == [
        "pod/dms-api-7d9f8b7c6-abcde", "vcjob/dms-scan-execution-ffffffffffff"]


def test_target_still_present_touches_nothing(env):
    e = env
    rid, (jid,) = _deleted(e)
    # DB 복원·변조 흉내: 같은 job_id 의 잡 행이 다시 있다
    other = e.repos.requests.create(operation="scan", requester_id="bob", actor="bob", resource_key="kk",
                                    payload={}, priority="mid", auth_method="session")
    plan = e.repos.data_jobs.create_plan(other, actor="planner")
    new = e.repos.data_jobs.create_job(other, plan, operation="scan", priority="mid", storage_name="s1",
                                       target="t", options={}, tool="dscan", precondition={}, actor="planner",
                                       worker_pool={})
    e.db.execute("UPDATE data_jobs SET job_id = :old WHERE job_id = :new", {"old": jid, "new": new})
    pods_before = dict(e.k8s.pods)
    assert e.tick()[rid] == "failed:purge_target_still_present"
    assert e.k8s.calls == [] and e.k8s.pods == pods_before
    assert (e.base / jid).is_dir() and not (e.base / TRASH).exists()
    row = _row(e, rid)
    assert row["stage"] == "k8s" and row["last_error"] == "purge_target_still_present"
    (ev,) = _events(e, "purge_failed")
    assert ev["severity"] == "error"


def test_global_stage_rechecks_the_whole_backlog_in_bounded_statements(env):
    # 2026-10-11 검증 지적: 전역 단계(_live_rows -- _reap·_settle 에서 틱마다 두 번)가 purging·files 행마다 원 행 재등장을
    # 두 문장씩 확인해, 큰 배치 삭제가 남긴 대기열(파드를 못 띄우는 동안 purging 에 쌓임)에서 틱이 행 수에 비례해
    # 느려졌다(PG 실측 5000행 1.2초). 확인은 묶음 한 번이다 -- 행이 늘어도 문장 수는 그대로다.
    e = env
    counts = []
    for n in (3, 12):
        rows = [_deleted(e, k8s=False) for _ in range(n)]
        for rid, _ in rows:
            e.repos.request_purges.advance(rid, "purging", now=OLD)
        seen = []
        real = e.db.query

        def spy(sql, params=None, _real=real, _seen=seen):
            _seen.append(sql)
            return _real(sql, params)
        e.db.query = spy
        try:
            purging, files = e.purger()._live_rows(str(e.base), report=False)
        finally:
            e.db.query = real
        assert {r["request_id"] for r in purging} >= {rid for rid, _ in rows}
        counts.append(len(seen))
    assert counts[0] == counts[1], counts


@pytest.mark.parametrize("jobs", ['not json', '[{"job_id": "../etc"}]', '[{"job_id": 7}]', '["x"]', '{}'])
def test_malformed_outbox_row_is_purge_row_invalid(env, jobs):
    e = env
    rid, (jid,) = _deleted(e)
    e.db.execute("UPDATE request_purges SET jobs = :j WHERE request_id = :r", {"j": jobs, "r": rid})
    assert e.tick()[rid] == "failed:purge_row_invalid"
    assert e.k8s.calls == [] and (e.base / jid).is_dir()
    (ev,) = _events(e, "purge_failed")
    assert ev["severity"] == "error" and ev["payload"]["reason_code"] == "purge_row_invalid"


def test_unknown_stage_string_is_purge_row_invalid(env):
    e = env
    rid, _ = _deleted(e)
    e.db.execute("UPDATE request_purges SET stage = 'weird' WHERE request_id = :r", {"r": rid})
    assert e.tick()[rid] == "failed:purge_row_invalid"


def test_tampered_purging_row_with_unknown_base_is_not_finished(env):
    e = env
    rid, (jid,) = _deleted(e, k8s=False)
    e.db.execute("UPDATE request_purges SET stage = 'purging', artifact_base = NULL WHERE request_id = :r",
                 {"r": rid})
    e.tick()
    row = _row(e, rid)
    assert row is not None and row["last_error"] == "purge_base_unavailable"
    assert (e.base / jid).is_dir()


# ---- 파일 단계 ----

def test_unknown_base_at_delete_time_is_purge_base_unavailable(env):
    e = env
    rid, (jid,) = _deleted(e, k8s=False)
    e.db.execute("UPDATE request_purges SET artifact_base = NULL WHERE request_id = :r", {"r": rid})
    assert e.tick()[rid] == "failed:purge_base_unavailable"
    assert _row(e, rid)["stage"] == "files" and (e.base / jid).is_dir()


def test_missing_base_is_purge_base_unavailable(env, tmp_path):
    e = env
    rid, _ = _deleted(e, k8s=False, artifact=False, base=tmp_path / "nope")
    gone = dataclasses.replace(e.settings, artifact_base_uri=f"file://{tmp_path}/nope")
    assert e.tick(settings=gone)[rid] == "failed:purge_base_unavailable"


def test_unsafe_base_stops_with_purge_base_unsafe(env):
    e = env
    rid, (jid,) = _deleted(e, k8s=False)
    e.base.chmod(0o775)
    try:
        assert e.tick()[rid] == "failed:purge_base_unsafe"
        assert (e.base / jid).is_dir()
    finally:
        e.base.chmod(0o755)


def test_symlinked_job_entry_is_artifact_dir_unexpected(env, tmp_path):
    e = env
    rid, (jid,) = _deleted(e, k8s=False, artifact=False)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("k")
    os.symlink(victim, e.base / jid)
    assert e.tick()[rid] == "failed:artifact_dir_unexpected"
    assert (victim / "keep").read_text() == "k" and (e.base / jid).is_symlink()


def test_crash_between_detach_and_advance_converges_to_deleted(env, monkeypatch):
    e = env
    rid, (jid,) = _deleted(e, k8s=False)
    purges = e.repos.request_purges
    real = purges.advance
    state = {"boom": True}

    def flaky(request_id, stage, **kw):
        if stage == "purging" and state.pop("boom", False):
            raise RuntimeError("controller killed")
        return real(request_id, stage, **kw)
    monkeypatch.setattr(purges, "advance", flaky)
    assert e.tick()[rid] == "error:RuntimeError"
    row = _row(e, rid)
    assert row["stage"] == "files" and row["last_error"] == "purge_failed"
    assert not (e.base / jid).exists() and (e.base / TRASH / jid).is_dir()     # 옮긴 뒤 죽었다
    _later(e)
    e.tick()
    assert _row(e, rid)["stage"] == "purging"
    _drive(e, rid)
    assert _events(e, "request_purged")[0]["payload"]["outcomes"] == {jid: "deleted"}


def test_pending_trash_entry_is_purged_then_detached(env):
    e = env
    rid, (jid,) = _deleted(e, k8s=False)
    (e.base / TRASH / jid / "older").mkdir(parents=True)     # 같은 이름의 앞 사본이 아직 trash 에
    (e.base / TRASH).chmod(0o700)
    assert e.tick()[rid] == "files"
    assert (e.base / jid).is_dir()
    (pod,) = _purge_pods(e).values()
    assert pod["spec"]["containers"][0]["command"][4:] == [jid]   # files 행의 trash 사본도 싣는다(교착 없음)
    _drive(e, rid)
    assert not (e.base / jid).exists() and not (e.base / TRASH / jid).exists()


def test_partial_pending_trash_keeps_outcomes_of_jobs_already_moved(env):
    e = env
    rid, (ja, jb) = _deleted(e, jobs=2, k8s=False)
    (e.base / TRASH / jb / "older").mkdir(parents=True)       # jb 만 앞 사본이 trash 에
    (e.base / TRASH).chmod(0o700)
    assert e.tick()[rid] == "files"
    assert not (e.base / ja).exists() and (e.base / jb).is_dir()
    assert _row(e, rid)["outcomes"]["jobs"] == {ja: "deleted"}
    (pod,) = _purge_pods(e).values()
    assert pod["spec"]["containers"][0]["command"][4:] == sorted([ja, jb])
    _drive(e, rid)
    assert _events(e, "request_purged")[0]["payload"]["outcomes"] == {ja: "deleted", jb: "deleted"}
    assert _events(e, "purge_failed") == []                   # 파드가 비운 뒤 다시 들어온 사본을 실패로 오인하지 않는다


def test_artifact_uri_below_the_job_dir_is_not_reported(env):
    e = env
    rid, (jid,) = _deleted(e, k8s=False, uri=f"file://<base>/<jid>/execution".replace("<base>", str(env.base)))
    _drive(e, rid)
    assert _events(e, "artifact_left_at_old_base") == []


def test_base_changed_since_delete_leaves_the_old_copy(env, tmp_path):
    e = env
    old = _mkbase(tmp_path / "old-base")
    rid, (jid,) = _deleted(e, k8s=False, artifact=False, base=old, uri=f"file://{old}/<jid>")
    _artifact(old, jid)
    e.tick()
    assert _row(e, rid) is None
    assert (old / jid / "execution" / "stdout.log").exists() and not (old / TRASH).exists()   # 무접촉
    (ev,) = _events(e, "artifact_left_at_old_base")
    assert ev["payload"]["artifact_base"] == str(old) and ev["payload"]["job_ids"] == [jid]
    assert _events(e, "request_purged")[0]["payload"]["outcomes"] == {jid: "left_old_base"}


def test_base_changed_during_purging_finishes_with_left_old_base(env, tmp_path):
    e = env
    rid, (jid,) = _deleted(e, k8s=False)
    e.tick()
    assert _row(e, rid)["stage"] == "purging" and (e.base / TRASH / jid).is_dir()
    new = _mkbase(tmp_path / "new-base")
    e.repos.control.set_artifact_base(f"file://{new}", actor="opadm", forced=True, affected_jobs=1)
    e.tick()
    assert _row(e, rid) is None
    assert (e.base / TRASH / jid).is_dir()                    # 옛 base 의 trash 는 확인·삭제하지 않는다
    (ev,) = _events(e, "artifact_left_at_old_base")
    assert ev["payload"]["in_trash"] is True and ev["payload"]["job_ids"] == [jid]
    assert _events(e, "request_purged")[0]["payload"]["outcomes"] == {jid: "left_old_base"}


def test_artifact_uri_outside_the_base_is_reported(env, tmp_path):
    e = env
    rid, (jid,) = _deleted(e, k8s=False, artifact=False, uri=f"file://{tmp_path}/artifacts-slice18/<jid>")
    e.tick()
    (ev,) = _events(e, "artifact_left_at_old_base")
    assert ev["payload"]["jobs"] == [{"job_id": jid, "artifact_uri": f"file://{tmp_path}/artifacts-slice18/{jid}"}]
    assert _events(e, "request_purged")[0]["payload"]["outcomes"] == {jid: "left_old_base"}


def test_reappeared_job_dir_goes_back_to_k8s(env):
    e = env
    rid, (jid,) = _deleted(e, k8s=False)
    e.tick()
    assert _row(e, rid)["stage"] == "purging"
    _artifact(e.base, jid)                                    # 떼어낸 뒤 누군가 다시 썼다
    e.tick()
    assert _row(e, rid)["stage"] == "k8s"
    assert _events(e, "artifact_reappeared")[0]["payload"]["job_ids"] == [jid]
    _drive(e, rid)
    assert not (e.base / jid).exists() and not (e.base / TRASH / jid).exists()


# ---- purge 파드 ----

def test_only_trash_entries_are_named_and_one_pod_at_a_time(env):
    e = env
    r1, (j1,) = _deleted(e, k8s=False)
    r2, (j2,) = _deleted(e, k8s=False, artifact=False)
    e.tick()
    (p1,) = _purge_pods(e).values()
    assert p1["spec"]["containers"][0]["command"][4:] == [j1]
    assert _row(e, r2) is None                               # 지울 파일이 없던 요청은 바로 끝난다
    r3, (j3,) = _deleted(e, k8s=False)
    e.tick()
    assert _row(e, r3)["stage"] == "purging" and len(_purge_pods(e)) == 1    # 진행 중이면 새로 만들지 않는다
    e.k8s.run_purge_pods(e.base)
    e.tick()
    assert _row(e, r1) is None and not _purge_pods(e)        # 끝난 파드는 지우고 그 틱엔 새로 만들지 않는다
    e.tick()
    (p2,) = _purge_pods(e).values()
    assert p2["spec"]["containers"][0]["command"][4:] == [j3]
    e.k8s.run_purge_pods(e.base)
    e.tick()
    assert _row(e, r3) is None


def test_succeeded_pod_with_leftovers_is_purge_pod_failed(env):
    e = env
    rid, (jid,) = _deleted(e, k8s=False)
    e.tick()
    e.k8s.run_purge_pods(e.base, succeed=True, remove=False)
    e.tick()
    row = _row(e, rid)
    assert row["last_error"] == "purge_pod_failed" and row["attempts"] == 1 and row["stage"] == "purging"
    assert not _purge_pods(e)
    e.tick()
    assert not _purge_pods(e)                                 # 백오프 중엔 새 파드에 싣지 않는다
    _later(e)
    e.tick()
    assert len(_purge_pods(e)) == 1
    e.k8s.run_purge_pods(e.base)
    e.tick()
    assert _row(e, rid) is None
    (ev,) = _events(e, "purge_failed")
    assert ev["payload"]["reason_code"] == "purge_pod_failed"


def _run_pods_with_the_real_script(e):
    """Pending/Running purge 파드를 **실제 purge 스크립트**(경로 상수만 tmp base 로)로 돌린다 -- 종료 코드가 phase."""
    for p in _purge_pods(e).values():
        if p["phase"] not in ("Pending", "Running"):
            continue
        names = p["spec"]["containers"][0]["command"][4:]
        r = subprocess.run(["sh", "-c", _purge_script(str(e.base)), "sh", *names], capture_output=True, text=True,
                           timeout=30)
        p["phase"] = "Succeeded" if r.returncode == 0 else "Failed"


def test_one_unremovable_entry_does_not_stall_the_requests_batched_with_it(env):
    # 2026-10-09 검증 지적: 스크립트가 첫 rm 실패에서 멈추면 정렬상 뒤 이름은 시도조차 안 되고, FS 기반 귀속이 같이
    # 실린 무고한 행까지 purge_pod_failed 로 몰아 같은 백오프로 영영 함께 묶였다. 이제 뒤 이름도 지워지고(그 행은 끝난다),
    # 실패한 행은 다음부터 혼자 간다(새 삭제와 섞이지 않는다).
    if os.geteuid() == 0:
        pytest.skip("root 는 0500 디렉터리 안의 파일도 지운다 -- 지속 실패를 흉내 낼 수 없다")
    e = env
    r1, (j1,) = _deleted(e, k8s=False)
    r2, (j2,) = _deleted(e, k8s=False)
    first, second = sorted([j1, j2])
    poison, victim = (r1, r2) if first == j1 else (r2, r1)
    stuck = e.base / first / "execution" / "sub"
    stuck.mkdir()
    (stuck / "f").write_text("x")
    stuck.chmod(0o500)
    try:
        e.tick()
        (pod,) = _purge_pods(e).values()
        assert pod["spec"]["containers"][0]["command"][4:] == [first, second]
        _run_pods_with_the_real_script(e)
        e.tick()
        assert _row(e, victim) is None                       # 뒤 이름도 지워져 끝났다
        assert not (e.base / TRASH / second).exists()
        row = _row(e, poison)
        # 같은 파드의 뒤 이름은 지워졌는데 이 이름만 남았다 = 그 항목 탓(근거) -- 다음부터 혼자.
        assert row["last_error"] == "purge_entry_failed" and row["attempts"] == 1
        (ev,) = _events(e, "purge_failed")
        assert ev["payload"]["request_id"] == poison           # 귀속은 실제로 남은 행에만
        # 새 삭제가 함께 due 면 새 행 묶음이 먼저 간다(혼자 갈 행이 가장 오래돼도 새 삭제를 세우지 않는다) ...
        r3, (j3,) = _deleted(e, k8s=False)
        e.db.execute("UPDATE request_purges SET requested_at = :t WHERE request_id = :r",
                     {"t": iso_plus(row["requested_at"], 60), "r": r3})
        _later(e)
        e.tick()
        (pod,) = _purge_pods(e).values()
        assert pod["spec"]["containers"][0]["command"][4:] == [j3]
        _run_pods_with_the_real_script(e)
        e.tick()
        assert _row(e, r3) is None and not _purge_pods(e)
        # ... 묶을 행이 없으면 실패 행이 혼자 간다. 혼자 간 파드에서 또 남아도 혼자 남는다(다른 행과 다시 섞지 않는다).
        e.tick()
        (pod,) = _purge_pods(e).values()
        assert pod["spec"]["containers"][0]["command"][4:] == [first]
        _run_pods_with_the_real_script(e)
        e.tick()
        row = _row(e, poison)
        assert row["attempts"] == 2 and row["last_error"] == "purge_entry_failed" and not _purge_pods(e)
    finally:
        for p in (stuck, e.base / TRASH / first / "execution" / "sub"):
            if p.exists():
                p.chmod(0o700)


def test_tampered_row_sharing_a_live_jobs_name_prefix_touches_none_of_its_objects(env):
    # 2026-10-09 검증 지적: 아웃박스 행(DB = 신뢰 경계)의 job_id 가 살아 있는 잡과 앞 12자만 같으면 target_still_present
    # (전체 id 비교)를 통과한다 -- k8s 단계가 이름(job_id[:12])으로 그 잡의 launcher·worker·vcjob 을 지우면 안 된다.
    e = env
    repos = e.repos
    rid = repos.requests.create(operation="sync", requester_id="bob", actor="bob", resource_key="k-live",
                                payload={"run_as_root": True}, priority="mid", auth_method="session")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    live = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid", storage_name="s1",
                                      target="t", options={}, tool="dsync", precondition={}, actor="planner",
                                      worker_pool={})
    vc = f"dms-sync-execution-{live[:12]}"
    e.k8s.add_pod(f"dms-preflight-{live[:12]}-exec-preflight-dms-w1", {"dms.io/job-id": live}, phase="Succeeded")
    e.k8s.add_vcjob(vc, {"dms.io/job-id": live})
    e.k8s.add_pod(f"{vc}-launcher-0", {"volcano.sh/job-name": vc})
    e.k8s.add_pod(f"{vc}-worker-0", {"dms.io/job-id": live, "volcano.sh/job-name": vc})
    fake = live[:12] + ("0" * 20 if live[12:] != "0" * 20 else "1" * 20)
    now = utc_now_iso()
    e.db.execute(
        "INSERT INTO request_purges (request_id, jobs, artifact_base, stage, outcomes, attempts, last_error, "
        "next_attempt_at, requested_by, requested_at, updated_at) VALUES "
        "(:r, :j, :b, 'k8s', NULL, 0, NULL, :n, 'x', :n, :n)",
        {"r": "f" * 32, "b": str(e.base), "n": now,
         "j": json.dumps([{"job_id": fake, "artifact_uri": None,
                           "phase_refs": {"exec_preflight": f"pod/dms-preflight-{live[:12]}-exec-preflight-dms-w1",
                                          "execution": f"vcjob/{vc}"}}])})
    assert not repos.request_purges.target_still_present("f" * 32, [fake])
    pods, vcjobs = set(e.k8s.pods), set(e.k8s.vcjobs)
    e.tick()
    assert e.k8s.deleted == []
    assert set(e.k8s.pods) == pods and set(e.k8s.vcjobs) == vcjobs


def test_failed_pod_backs_off(env):
    e = env
    rid, (jid,) = _deleted(e, k8s=False)
    e.tick()
    e.k8s.run_purge_pods(e.base, succeed=False, remove=False)
    e.tick()
    assert _row(e, rid)["last_error"] == "purge_pod_failed"
    assert (e.base / TRASH / jid).is_dir()


def test_pending_pod_with_a_fatal_wait_is_reaped(env):
    e = env
    rid, (jid,) = _deleted(e, k8s=False)
    e.tick()
    (name,) = _purge_pods(e)
    e.k8s.pods[name]["waiting_reason"] = "ImagePullBackOff"
    e.tick()
    assert name not in e.k8s.pods and _row(e, rid)["last_error"] == "purge_pod_failed"


def _pod_names(pod):
    return pod["spec"]["containers"][0]["command"][4:]


def _end_pod(e, *, removed, phase):
    """진행 중 purge 파드 하나를 끝낸다 -- removed 이름만 trash 에서 지우고 phase 로(데드라인에 끊긴 파드·일부 실패 흉내)."""
    (p,) = [p for p in _purge_pods(e).values() if p["phase"] in ("Pending", "Running")]
    for n in removed:
        (e.base / TRASH / n / "execution" / "stdout.log").unlink()
        (e.base / TRASH / n / "execution").rmdir()
        (e.base / TRASH / n).rmdir()
    p["phase"] = phase


def test_pod_level_failure_keeps_the_rows_batched(env):
    # 2026-10-09 검증 지적: 파드 수준 실패(레지스트리 순단의 ImagePullBackOff·Pending 상한)는 실린 행 전부를
    # purge_pod_failed 로 몰고, 실패 행을 혼자 싣는 규칙이 그 행들을 한 행씩 직렬로(20행 = 파드 21개·42틱) 되돌렸다 --
    # 새 삭제도 그 뒤에 섰다. 항목 탓이라는 근거가 없으니 표시·이벤트는 남기되 다음 파드에 함께 싣는다.
    e = env
    olds = [_deleted(e, k8s=False) for _ in range(20)]
    e.tick()
    (pname,) = _purge_pods(e)
    assert len(_pod_names(_purge_pods(e)[pname])) == 20
    e.k8s.pods[pname]["waiting_reason"] = "ImagePullBackOff"
    _later(e)
    e.tick()
    assert {(_row(e, r)["last_error"], _row(e, r)["attempts"]) for r, _ in olds} == {("purge_pod_failed", 1)}
    assert len(_events(e, "purge_failed")) == 20
    fresh, (fj,) = _deleted(e, k8s=False)
    _later(e)
    e.tick()
    (pod,) = _purge_pods(e).values()
    assert sorted(_pod_names(pod)) == sorted([fj, *(j for _, (j,) in olds)])    # 한 파드에 전부
    e.k8s.run_purge_pods(e.base)
    e.tick()
    assert all(_row(e, r) is None for r in [fresh, *(r for r, _ in olds)])


def test_pod_cut_mid_run_isolates_only_the_entry_it_was_on(env):
    # 데드라인·축출로 끊긴 파드: 앞 이름은 지워졌고, 끊길 때 지우던 이름(거대 트리)과 그 뒤 시도조차 못 한 이름이 남는다.
    # 혼자 가야 할 것은 끊긴 그 이름의 행뿐이다 -- 뒤의 행들은 근거가 없어 묶음으로 남는다(한 행씩 직렬로 돌리지 않는다).
    e = env
    rows = {jid: rid for rid, (jid,) in (_deleted(e, k8s=False) for _ in range(4))}
    e.tick()
    (pod,) = _purge_pods(e).values()
    n0, n1, n2, n3 = _pod_names(pod)
    assert [n0, n1, n2, n3] == sorted(rows)
    _end_pod(e, removed=[n0], phase="Failed")
    e.tick()
    assert _row(e, rows[n0]) is None or _row(e, rows[n0])["last_error"] is None
    assert _row(e, rows[n1])["last_error"] == "purge_entry_failed"
    assert _row(e, rows[n2])["last_error"] == "purge_pod_failed"
    assert _row(e, rows[n3])["last_error"] == "purge_pod_failed"
    _later(e)
    e.tick()
    (pod,) = _purge_pods(e).values()
    assert _pod_names(pod) == [n2, n3]                       # 끊긴 이름 없이 묶어서
    e.k8s.run_purge_pods(e.base)
    e.tick()
    assert _row(e, rows[n2]) is None and _row(e, rows[n3]) is None
    e.tick()
    (pod,) = _purge_pods(e).values()
    assert _pod_names(pod) == [n1]                           # 묶을 행이 없으니 혼자
    e.k8s.run_purge_pods(e.base)
    e.tick()
    assert all(_row(e, r) is None for r in rows.values())


def test_entry_failure_evidence_is_named_entries_before_the_last_removed_and_the_one_after_it():
    # 스크립트는 command 순서대로 지우고 실패해도 다음 이름으로 간다 -- 마지막으로 지워진 이름보다 앞에 남은 이름은
    # 시도했는데 남은 것, 그 바로 뒤 첫 남은 이름은 실패했거나 끊길 때 지우던 것. 그 밖은 근거가 아니다.
    a, b, c, d, x = ("a" * 32, "b" * 32, "c" * 32, "d" * 32, "e" * 32)
    st = lambda left: {j: (False, j in left) for j in (a, b, c, d)}      # noqa: E731
    ev = RequestPurger._entry_failures
    assert ev({"names": [a, b, c, d], "phase": "Failed"}, st({b, d})) == {b, d}       # 실패 후 계속(exit 1)
    assert ev({"names": [a, b, c, d], "phase": "Failed"}, st({b, c, d})) == {b}       # b 에서 끊김
    assert ev({"names": [a, b, c, d], "phase": "Succeeded"}, st({a})) == {a}
    assert ev({"names": [a, b, c, d], "phase": "Failed"}, st({a, b, c, d})) == set()  # 하나도 못 지움 -- 파드 탓
    assert ev({"names": [a, b], "phase": "Pending"}, st({b})) == set()                # 스크립트가 돌지 않았다
    assert ev({"names": None, "phase": "Failed"}, st({b})) == set()                   # 이름 모름
    assert ev({"names": [a, b, c, d], "phase": "Unknown"}, st({b})) == set()          # 노드 연락 끊김 -- 모름
    assert ev({"names": [x, a, b], "phase": "Failed"}, st({a, b})) == set()           # x 는 후보 밖(모름)


def test_an_entry_failed_row_waits_for_no_batch_but_not_forever(env):
    # 혼자 갈 행은 묶어 실을 새 행이 없을 때 간다 -- 다만 due 가 된 지 ISOLATED_TURN_SECONDS 가 지났으면 새 묶음보다
    # 먼저(삭제가 끊임없이 들어와도 영영 밀리지 않게).
    e = env
    r1, (j1,) = _deleted(e, k8s=False)
    e.tick()
    e.k8s.run_purge_pods(e.base, succeed=False, remove=False)
    e.tick()
    assert _row(e, r1)["last_error"] == "purge_pod_failed"   # 하나뿐인 이름이 남음 -- 근거 없음
    e.db.execute("UPDATE request_purges SET last_error = 'purge_entry_failed', next_attempt_at = :t "
                 "WHERE request_id = :r", {"t": iso_plus(e.clock(), -(ISOLATED_TURN_SECONDS + 1)), "r": r1})
    r2, (j2,) = _deleted(e, k8s=False)
    e.tick()
    (pod,) = _purge_pods(e).values()
    assert _pod_names(pod) == [j1]                           # 오래 기다린 혼자 갈 행이 먼저
    e.k8s.run_purge_pods(e.base)
    e.tick()
    e.tick()
    (pod,) = _purge_pods(e).values()
    assert _pod_names(pod) == [j2]
    e.k8s.run_purge_pods(e.base)
    e.tick()
    assert _row(e, r1) is None and _row(e, r2) is None


def _age_pod(e, name, seconds):
    e.k8s.pods[name]["created"] = iso_plus(utc_now_iso(), -seconds)


def test_purge_pod_that_never_finishes_surfaces_as_purge_pod_stuck(env):
    # 2026-10-09 검증 지적: 노드가 죽어 Running 으로 남거나 Terminating 에 갇힌 purge 파드(또는 D 상태 rm)는 영영 끝나지
    # 않는다. 예전엔 「진행 중」으로만 보여 그 뒤의 모든 정리가 last_error 없이(stalled 0, 이벤트 0) 멈췄다.
    e = env
    rid1, (j1,) = _deleted(e, k8s=False)
    e.tick()
    (name,) = _purge_pods(e)
    e.k8s.pods[name]["phase"] = "Running"
    rid2, (j2,) = _deleted(e, k8s=False)                     # 뒤 삭제가 그 파드 뒤에 줄을 선다
    e.tick()                                                  # 아직 데드라인 + 유예 전 -- 기다리기만
    assert _row(e, rid1)["last_error"] is None and _events(e, "purge_failed") == []
    _age_pod(e, name, PURGE_POD_STUCK_SECONDS + 1)
    e.k8s.pods[name]["deleting"] = True                       # taint eviction -- Terminating 에 갇힘
    for _ in range(3):
        _later(e)
        e.tick()
    for rid in (rid1, rid2):
        row = _row(e, rid)
        assert (row["stage"], row["last_error"], row["attempts"]) == ("purging", "purge_pod_stuck", 0)
    status = e.repos.request_purges.status()
    assert (status["pending"], status["stalled"]) == (2, 2)
    evs = _events(e, "purge_failed")
    assert sorted(ev["payload"]["request_id"] for ev in evs) == sorted([rid1, rid2])   # 행마다 한 번(스팸 없음)
    assert all(ev["severity"] == "warning" and ev["payload"]["reason_code"] == "purge_pod_stuck"
               and ev["payload"]["pods"][0]["name"] == name for ev in evs)
    assert set(_purge_pods(e)) == {name}                      # 두 번째 파드를 띄우지 않는다(언제나 1개)
    assert ("Pod", name) not in e.k8s.deleted                 # 막힌 파드는 운영자 판단(지우지 않는다)
    assert (e.base / TRASH / j1).is_dir() and (e.base / TRASH / j2).is_dir()
    # 운영자가 노드를 확인하고 강제 삭제 -- 표시를 거두고 새 파드가 이어서 비운다.
    del e.k8s.pods[name]
    _later(e)
    e.tick()
    assert all(_row(e, r)["last_error"] is None for r in (rid1, rid2))
    (pod,) = _purge_pods(e).values()
    assert sorted(pod["spec"]["containers"][0]["command"][4:]) == sorted([j1, j2])
    e.k8s.run_purge_pods(e.base)
    _later(e)
    res = e.tick()
    assert res[rid1] == "purged" and res[rid2] == "purged"


def test_young_running_purge_pod_is_just_waited_for(env):
    e = env
    rid, _ = _deleted(e, k8s=False)
    e.tick()
    (name,) = _purge_pods(e)
    e.k8s.pods[name]["phase"] = "Running"
    _age_pod(e, name, PURGE_POD_STUCK_SECONDS - 60)
    for _ in range(3):
        _later(e)
        e.tick()
    assert _row(e, rid)["last_error"] is None and _events(e, "purge_failed") == []


def test_no_eligible_node_is_purge_no_node(env):
    e = env
    e.db.execute("DELETE FROM agent_nodes")
    rid, _ = _deleted(e, k8s=False)
    e.tick()
    assert _row(e, rid)["last_error"] == "purge_no_node" and not _purge_pods(e)


@pytest.mark.parametrize("report", [
    {"artifact_base": {"path": "/elsewhere", "exists": True, "writable": True}},
    {"artifact_base": {"exists": True, "writable": True}},                       # 경로 모름
    {"artifact_base": {"path": "<base>", "exists": True, "writable": None}},     # 모름 ≠ 쓰기 가능
    {"artifact_base": {"path": "<base>", "exists": None, "writable": True}},
    {"artifact_base": {"path": "<base>", "exists": True, "writable": False}},
    {"artifact_base": "broken"},
    {},
])
def test_nodes_that_do_not_confirm_this_base_are_not_eligible(env, report):
    e = env
    rep = json.loads(json.dumps(report).replace("<base>", str(e.base)))
    e.repos.agents.ingest("dms-w1", rep)
    rid, _ = _deleted(e, k8s=False)
    e.tick()
    assert _row(e, rid)["last_error"] == "purge_no_node"


def test_excluded_and_stale_nodes_are_left_out(env):
    e = env
    ok = {"artifact_base": {"path": str(e.base), "exists": True, "writable": True}}
    e.repos.agents.ingest("dms-w2", ok)
    e.repos.agents.ingest("dms-w3", ok, reported_at=OLD)           # 신선하지 않음
    e.repos.agents.ingest("dms-w4", {**ok, "k8s_node": {"schedulable": False}})   # cordon
    e.repos.node_exclusions.exclude("dms-w1", reason="disk", actor="opadm")
    rid, _ = _deleted(e, k8s=False)
    e.tick()
    (pod,) = _purge_pods(e).values()
    assert pod["spec"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"][0]["matchExpressions"][0]["values"] == ["dms-w2"]


def test_purge_pod_listing_error_fails_due_purging_rows(env):
    e = env
    rid, _ = _deleted(e, k8s=False)
    e.tick()
    e.k8s.fail["list_pod_briefs"] = RuntimeError("down")
    res = e.tick()
    assert res["_purge_pod"] == "failed:purge_k8s_failed"
    assert _row(e, rid)["last_error"] == "purge_k8s_failed"


# ---- 루프 규칙 ----

def test_drain_is_a_noop(env):
    e = env
    rid, (jid,) = _deleted(e)
    e.repos.control.set_control_state(maintenance=False, drain=True, reason="r", actor="opadm")
    assert e.tick() == {}
    assert e.k8s.calls == [] and _row(e, rid)["stage"] == "k8s" and (e.base / jid).is_dir()


def test_tick_budget_stops_the_row_loop(env):
    e = env
    r1, _ = _deleted(e, k8s=False, artifact=False)
    r2, _ = _deleted(e, k8s=False, artifact=False)
    e.db.execute("UPDATE request_purges SET requested_at = :t WHERE request_id = :r",     # 순서 고정(오래된 순)
                 {"t": iso_plus(utc_now_iso(), -60), "r": r1})
    times = itertools.chain([0, 0, 30], itertools.repeat(30))
    res = e.purger(monotonic=lambda: next(times)).run_once()
    assert res.get("_budget_exhausted") is True
    assert res[r1] == "purged" and r2 not in res
    assert _row(e, r2)["stage"] == "k8s"


def test_purging_rows_do_not_starve_new_deletes(env, monkeypatch):
    e = env
    import dms.request_purger as rp
    monkeypatch.setattr(rp, "ROW_LIMIT", 2)
    waiting = [_deleted(e, k8s=False)[0] for _ in range(2)]
    e.tick()
    assert all(_row(e, r)["stage"] == "purging" for r in waiting)    # 파드를 기다린다(due 그대로)
    fresh, _ = _deleted(e, k8s=False, artifact=False)
    res = e.tick()
    assert res[fresh] == "purged"


def test_stub_runner_end_to_end_for_jobs_without_artifacts(env):
    e = env
    rid, (jid,) = _deleted(e, k8s=False, artifact=False, refs={"execution": "stub-execution-<jid>"})
    stub = StubPurgeRunner()
    e.tick(runner=stub)
    assert _row(e, rid) is None
    assert stub.swept == [(jid, [f"stub-execution-{jid}"])]       # 스텁 ref 는 「그 잡의 ref」로 받는다
    assert _events(e, "purge_ref_rejected") == []


def test_controller_wires_the_loop_only_with_a_runner(env):
    e = env
    names = [(l.name, l.interval_seconds) for l in build_loops(e.settings, e.repos, purge_runner=e.runner)]
    assert ("request-purge", INTERVAL) in names
    assert "request-purge" not in [l.name for l in build_loops(e.settings, e.repos)]
    rid, _ = _deleted(e, jobs=0)
    loops = [l for l in build_loops(e.settings, e.repos, purge_runner=StubPurgeRunner())
             if l.name == "request-purge"]
    assert run_all_once(loops, e.repos, holder="h1") == {"request-purge": "ok"}
    assert _row(e, rid) is None
