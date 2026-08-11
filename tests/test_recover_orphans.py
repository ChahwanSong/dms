from dms.domain import DataJobState, RequestState
from dms.execution import StubExecutionAdapter
from dms.repositories import Repositories


class _Settings:
    agent_report_stale_seconds = 300
    reconcile_interval_seconds = 30
    retention_interval_seconds = 3600
    planner_interval_seconds = 10
    stepper_interval_seconds = 5
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    agent_report_retention_days = 30
    event_retention_days = 30
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    batch_orchestrator_interval_seconds = 5
    pod_gc_after_seconds = 3600
    pod_gc_interval_seconds = 600


def _orphan(repos, key="k"):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key=key, payload={"storage": "s", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s", target="a", options={}, tool="dscan",
        worker_pool={}, precondition={}, actor="planner")
    # 잡만 터미널(크래시 흉내), 요청은 Planned로 남음
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    return rid, jid


def test_terminal_jobs_with_live_request(db):
    repos = Repositories(db)
    rid, jid = _orphan(repos)
    orphans = repos.data_jobs.terminal_jobs_with_live_request()
    assert [(o["job_id"], o["request_id"]) for o in orphans] == [(jid, rid)]


def test_orphan_recovery_via_controller(db):
    from dms.controller import build_loops, run_all_once
    repos = Repositories(db)
    rid, jid = _orphan(repos)
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")  # job-stepper 스텝이 고아 복구
    assert repos.requests.get(rid)["state"] == "Succeeded"
    assert repos.data_jobs.terminal_jobs_with_live_request() == []


def test_orphan_recovery_propagates_job_result_summary(db):
    """SUCCEEDED로 result_summary를 이미 가진 채 request finalize 직전 크래시한
    시나리오: 복구된 request의 결과 summary가 잡의 result_summary와 같아야 한다
    (Task 8이 겨냥한 데이터 유실 방지 시나리오)."""
    from dms.controller import build_loops, run_all_once
    repos = Repositories(db)
    rid, jid = _orphan(repos)
    repos.data_jobs.set_artifact(jid, artifact_uri=None,
                                 result_summary={"files": 7, "bytes": 999})
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")
    assert repos.requests.get(rid)["state"] == "Succeeded"
    result = db.query_one("SELECT summary FROM results WHERE request_id = :r", {"r": rid})
    from dms.db import load_json
    assert load_json(result["summary"]) == {"files": 7, "bytes": 999}


# ---- 슬라이스 24 §2.3: 스윕 상한 + 행 단위 격리 ----

def test_sweep_is_bounded_to_200_and_still_makes_progress(db):
    """preview 만료 직후 크래시(§1-11: expire_previews 는 한 호출로 N 건 종단,
    finalize 는 행별 후속) 같은 대량 고아에서 무제한 스윕은 단일 스레드 컨트롤러
    (§1-10)의 한 틱을 통째로 먹어 planner·stepper·pod-gc 가 전부 그 뒤에 선다.
    상한 200(같은 파일 terminal_jobs_older_than 선례 미러)이어도, finalize 가
    멱등이고 처리된 행이 술어에서 빠지므로 두 틱이면 201건이 전부 복구된다."""
    from dms.controller import build_loops, run_all_once
    repos = Repositories(db)
    for i in range(201):
        _orphan(repos, key=f"k{i}")
    assert len(repos.data_jobs.terminal_jobs_with_live_request(limit=1000)) == 201
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")
    assert len(repos.data_jobs.terminal_jobs_with_live_request(limit=1000)) == 1
    run_all_once(loops, repos, holder="h1")
    assert repos.data_jobs.terminal_jobs_with_live_request(limit=1000) == []


def test_sweep_returns_oldest_first_under_the_limit(db):
    # 잘리는 상황에서 최신순이면 가장 오래된(가장 급한) 고아가 윈도우 밖으로
    # 영영 밀린다 -- terminal_jobs_older_than 과 같은 이유, 같은 정렬.
    repos = Repositories(db)
    pairs = [_orphan(repos, key=f"k{i}") for i in range(3)]
    for i, (_rid, jid) in enumerate(pairs):
        db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j",
                   {"t": f"2026-01-0{i + 1}T00:00:00Z", "j": jid})
    rows = repos.data_jobs.terminal_jobs_with_live_request(limit=2)
    assert [r["job_id"] for r in rows] == [pairs[0][1], pairs[1][1]]


def test_poison_row_does_not_starve_the_rest_and_leaves_an_event(db):
    """행 단위 try/except 가 없으면 첫 예외가 나머지 전부를 다음 틱으로 민다
    (§1-9) -- 그리고 독 행이 영구 독이면 나머지가 **영구히** 복구되지 않는다.
    격리 후에는: 독 행만 남고(다음 틱 멱등 재시도), 이벤트가 남는다."""
    from dms.controller import build_loops, run_all_once
    repos = Repositories(db)
    poison_rid, poison_jid = _orphan(repos, key="k-poison")
    healthy_rid, _healthy_jid = _orphan(repos, key="k-healthy")
    # 독 행을 더 오래된 행으로 -- 스윕이 먼저 만나 실패해야 격리가 증명된다.
    db.execute("UPDATE data_jobs SET updated_at = '2020-01-01T00:00:00Z' "
               "WHERE job_id = :j", {"j": poison_jid})
    original = repos.requests.finalize_from_job
    state = {"raised": False}

    def flaky(request_id, *args, **kwargs):
        if request_id == poison_rid and not state["raised"]:
            state["raised"] = True          # 1회성 독 -- 다음 틱 재시도가 성공해야 한다
            raise RuntimeError("poison row")
        return original(request_id, *args, **kwargs)

    repos.requests.finalize_from_job = flaky
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")
    assert repos.requests.get(healthy_rid)["state"] == "Succeeded"   # 독이 못 막았다
    assert repos.requests.get(poison_rid)["state"] == "Planned"      # 실패 행은 남았다
    events = repos.observability.events_for_request(poison_rid)
    assert [e["event_type"] for e in events] == ["orphan_recovery_failed"]
    assert "RuntimeError" in events[0]["message"]
    run_all_once(loops, repos, holder="h1")                          # 다음 틱 멱등 재시도
    assert repos.requests.get(poison_rid)["state"] == "Succeeded"


def test_zero_orphan_sweep_records_nothing(db):
    # 0건 스윕은 정상값이다(설계 §2.3) -- "고아 없음"을 이벤트로 남기면 매 틱
    # 노이즈가 쌓여 진짜 실패가 묻힌다.
    from dms.controller import build_loops, run_all_once
    repos = Repositories(db)
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")
    row = db.query_one("SELECT COUNT(*) AS c FROM events "
                       "WHERE event_type = 'orphan_recovery_failed'")
    assert row["c"] == 0
