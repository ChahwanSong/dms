from dms.execution import ExecStatus, StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def _scan_job(repos):
    from dms.domain import RequestState
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={"tool": "dscan", "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


def test_pending_to_preflight_submits(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    adapter.script(f"stub-preflight-{jid}", [ExecStatus.RUNNING])  # preflight 아직 안 끝남
    _stepper(repos, adapter).run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Preflight"
    assert job["phase_refs"]["preflight"] == f"stub-preflight-{jid}"
    assert adapter.submitted_specs()[0].phase == "preflight"
    assert adapter.submitted_specs()[0].dryrun is False


def test_full_scan_lifecycle_to_succeeded(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()  # 모든 poll 기본 Succeeded
    adapter.set_summary(f"stub-execution-{jid}", {"files": 42, "bytes": 1000})
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight (submit)
    assert repos.data_jobs.get_job(jid)["state"] == "Preflight"
    stepper.run_once()   # Preflight poll Succeeded → Running (exec submit)
    assert repos.data_jobs.get_job(jid)["state"] == "Running"
    stepper.run_once()   # Running poll Succeeded → Succeeded
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded"
    assert job["result_summary"] == {"files": 42, "bytes": 1000}
    assert repos.requests.get(rid)["state"] == "Succeeded"


class _NullSummaryAdapter(StubExecutionAdapter):
    """SUCCEEDED vcjob이지만 controller가 artifact_base를 못 읽어 summary.json이
    없는 배포 오구성을 흉내. 실제 job-runner는 summary.json을 항상 쓰므로
    read_summary()가 None을 반환하는 상황은 정상 잡에서는 발생하지 않는다."""
    def read_summary(self, ref: str):
        return None


def test_execution_succeeded_with_none_summary_is_visible(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = _NullSummaryAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()  # Pending → Preflight
    stepper.run_once()  # Preflight poll Succeeded → Running (exec submit)
    stepper.run_once()  # Running poll Succeeded, read_summary() -> None
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded"
    assert job["result_summary"] == {"summary_unavailable": True}
    req = repos.requests.get(rid)
    assert req["state"] == "Succeeded"
    result = db.query_one("SELECT summary FROM results WHERE request_id = :r", {"r": rid})
    from dms.db import load_json
    assert load_json(result["summary"]) == {"summary_unavailable": True}
    # 배선 회귀 가드: 코드 경로는 타지만 events 테이블은 검사 안 하던 구멍.
    events = repos.observability.events_for_request(rid)
    assert len(events) == 1
    assert events[0]["component"] == "stepper"
    assert events[0]["event_type"] == "summary_unreadable"
    assert events[0]["severity"] == "warning"


def test_preflight_failure_rejects(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()  # → Preflight
    adapter.script(f"stub-preflight-{jid}", [ExecStatus.FAILED])
    stepper.run_once()  # Preflight poll Failed → Rejected
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert repos.requests.get(rid)["state"] == "Rejected"


def test_execution_failure(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()  # Preflight
    stepper.run_once()  # Running (preflight succeeded)
    adapter.script(f"stub-execution-{jid}", [ExecStatus.FAILED])
    stepper.run_once()  # Running poll Failed → Failed
    assert repos.data_jobs.get_job(jid)["state"] == "Failed"
    assert repos.requests.get(rid)["state"] == "Failed"


class _TerminateRecordingAdapter(StubExecutionAdapter):
    def __init__(self):
        super().__init__()
        self.terminated = []

    def terminate(self, ref):
        self.terminated.append(ref)
        super().terminate(ref)


def test_job_cancelled_between_claim_and_step_reclaims_submitted_ref(db):
    """claim_steppable의 스냅샷에는 잠금이 없다(커넥션이 autocommit이라
    FOR UPDATE SKIP LOCKED가 즉시 풀린다). 그 창에서 취소된 잡도 _step_one이 그대로
    제출해 버리고 뒤따르는 상태 기록은 종단 가드가 삼키므로, 클러스터에 아무도 못 치우는
    고아가 남는다(cancel_job은 종단 잡에 409, terminate_job은 no-op). 제출 직후 상태를
    한 번 더 읽어 즉시 회수해야 한다."""
    from dms.domain import DataJobState
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = _TerminateRecordingAdapter()
    stepper = _stepper(repos, adapter)
    job = [j for j in repos.data_jobs.claim_steppable() if j["job_id"] == jid][0]
    repos.data_jobs.set_job_state(jid, DataJobState.CANCELLED,
                                  reason_code="cancelled_by_user", actor="alice")

    result = stepper._step_one(job)      # 낡은 스냅샷으로 계속 진행한다

    assert adapter.terminated == [f"stub-preflight-{jid}"]
    assert result == "Cancelled"
    assert repos.data_jobs.get_job(jid)["state"] == "Cancelled"


def test_unexpected_exception_records_step_error_event(db, monkeypatch):
    # 배선 회귀 가드: run_once의 항목별 except Exception이 stderr만 찍고 끝나면
    # 이 실패는 events 테이블에 안 남는다 -- 직접 검사한다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()

    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(repos.storages, "get", _boom)  # _build_spec의 _abs()가 부른다

    result = _stepper(repos, adapter).run_once()
    assert result[jid] == "error:RuntimeError"
    events = repos.observability.events_for_request(rid)
    assert len(events) == 1
    assert events[0]["component"] == "stepper"
    assert events[0]["event_type"] == "step_error"
    assert events[0]["severity"] == "error"
    assert "RuntimeError" in events[0]["message"] and "boom" in events[0]["message"]


def test_terminate_failure_during_reclaim_records_terminate_failed_event(db):
    """test_job_cancelled_between_claim_and_step_reclaims_submitted_ref와 같은 경쟁
    시나리오지만, best-effort terminate() 자체가 ExecutionError로 실패하는 경우 --
    고아 리소스가 남았을 수 있으니 진단 채널(events)에 남아야 한다."""
    from dms.domain import DataJobState
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    ref = f"stub-preflight-{jid}"
    adapter.fail_terminate(ref)
    stepper = _stepper(repos, adapter)
    job = [j for j in repos.data_jobs.claim_steppable() if j["job_id"] == jid][0]
    repos.data_jobs.set_job_state(jid, DataJobState.CANCELLED,
                                  reason_code="cancelled_by_user", actor="alice")

    result = stepper._step_one(job)      # 낡은 스냅샷으로 계속 진행한다

    assert result == "Cancelled"
    events = repos.observability.events_for_request(rid)
    assert len(events) == 1
    assert events[0]["component"] == "stepper"
    assert events[0]["event_type"] == "terminate_failed"
    assert events[0]["severity"] == "warning"
    assert events[0]["payload"] == {"ref": ref}


def test_drain_stops_stepping(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    repos.control.set_control_state(maintenance=False, drain=True, reason="x", actor="admin")
    result = _stepper(repos, StubExecutionAdapter()).run_once()
    assert result == {}
    assert repos.data_jobs.get_job(jid)["state"] == "Pending"


def test_execution_submit_records_anchor_but_preflight_does_not(db):
    # 슬라이스 20 설계 §2.2: 앵커는 execution vcjob 제출 직후다. preflight 제출
    # (틱 1)에서 남으면 preflight 파드 수행 시간이 "스케줄 대기"에 통째로 섞인다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    adapter.script(f"stub-preflight-{jid}",
                   [ExecStatus.RUNNING, ExecStatus.SUCCEEDED])
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight (preflight 제출 -- 앵커 아님)
    assert repos.data_jobs.get_job(jid)["exec_submitted_at"] is None
    stepper.run_once()   # preflight poll RUNNING -- 아직 execution 제출 전
    assert repos.data_jobs.get_job(jid)["exec_submitted_at"] is None
    stepper.run_once()   # preflight SUCCEEDED → execution 제출 + 앵커
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Running"
    assert job["exec_submitted_at"] is not None
    assert job["sched_wait_seconds"] is None     # 관측 전 -- 기록은 Task 3
