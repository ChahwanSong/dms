"""2026-11-23 stepper 요약 확정의 유한 지연(stepper._defer_summary).

summary_unavailable 은 영구 기록이다 -- 컨트롤러 쪽 base 가 일시적으로 안 보일 때(미마운트 노드에서 재시작·마운트 흔들림)
끝난 잡의 요약을 영구 「없음」으로 굳히지 않게, base 수준 판정이 실패인 동안만 확정을 미루고 상한(min(20분, vcjob TTL/2))을
넘으면 지금처럼 확정한다. base 가 보이는데 그 잡의 파일만 없으면 즉시 확정(tests/test_stepper_scan.py)."""
import pytest

from dms import stepper as stepper_mod
from dms.execution import StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import SUMMARY_DEFER_MAX_SECONDS, JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///nonexistent-base-for-deferral"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


class _SwitchAdapter(StubExecutionAdapter):
    def __init__(self):
        super().__init__()
        self.summary = None

    def read_summary(self, ref):
        return self.summary


@pytest.fixture(autouse=True)
def _clean_deferrals():
    stepper_mod._summary_deferrals.clear()
    stepper_mod._summary_defer_warned.clear()
    yield
    stepper_mod._summary_deferrals.clear()
    stepper_mod._summary_defer_warned.clear()


def _scan_job(repos):
    from dms.domain import RequestState
    if repos.storages.get("s1") is None:
        repos.storages.create(storage_name="s1", mount_path="/s1", managed_root="/s1/dms", backend_type="cephfs",
                              actor="test")
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice", resource_key="k",
                                payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid", storage_name="s1", target="a",
        options={}, tool="dscan",
        worker_pool={"tool": "dscan", "identity": {"uid": 10001, "gid": 10000, "username": "alice", "groups": [],
                                                   "privileged": False},
                     "candidates": {"primary": ["n1"]}, "process_count": 8, "queue": "dms-data",
                     "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _run_to_running(repos, adapter, settings, clock):
    JobStepper(repos, adapter, settings=settings, monotonic=lambda: clock[0]).run_once()   # → Preflight
    JobStepper(repos, adapter, settings=settings, monotonic=lambda: clock[0]).run_once()   # → Running


def _tick(repos, adapter, settings, clock):
    JobStepper(repos, adapter, settings=settings, monotonic=lambda: clock[0]).run_once()


def _deferral_events(repos, rid):
    return [e for e in repos.observability.events_for_request(rid)
            if e["event_type"] == "summary_deferred_base_unavailable"]


def test_unreachable_base_defers_then_finalizes_after_the_cap(db, tmp_path):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    settings = _Settings()
    settings.artifact_base_uri = f"file://{tmp_path}/not-mounted"
    adapter, clock = _SwitchAdapter(), [1000.0]
    _run_to_running(repos, adapter, settings, clock)
    _tick(repos, adapter, settings, clock)
    assert repos.data_jobs.get_job(jid)["state"] == "Running"            # 이번 틱은 확정하지 않는다
    clock[0] += 60
    _tick(repos, adapter, settings, clock)
    assert repos.data_jobs.get_job(jid)["state"] == "Running"
    events = _deferral_events(repos, rid)
    assert len(events) == 1                                             # 잡마다 1회
    assert events[0]["payload"]["reason"] == "artifact_base_missing"
    assert events[0]["payload"]["defer_max_seconds"] == SUMMARY_DEFER_MAX_SECONDS
    clock[0] += SUMMARY_DEFER_MAX_SECONDS
    _tick(repos, adapter, settings, clock)
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded" and job["result_summary"] == {"summary_unavailable": True}
    assert jid not in stepper_mod._summary_deferrals


def test_deferred_job_moves_to_the_back_of_the_claim_queue(db, tmp_path):
    # 2026-11-23 리뷰: claim_steppable 은 updated_at 순 10건 -- 미루는 잡이 updated_at 을 안 바꾸면 매 틱 같은 앞자리를
    # 차지해 새 제출·다른 완료 잡이 상한(20분) 동안 굶는다. 미룰 때 touch(D2 보류와 같은 방식)로 큐 뒤로 민다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    settings = _Settings()
    settings.artifact_base_uri = f"file://{tmp_path}/not-mounted"
    adapter, clock = _SwitchAdapter(), [1000.0]
    _run_to_running(repos, adapter, settings, clock)
    repos.db.execute("UPDATE data_jobs SET updated_at = '2026-01-01T00:00:00Z' WHERE job_id = :j", {"j": jid})
    _tick(repos, adapter, settings, clock)
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Running" and job["updated_at"] > "2026-01-01T00:00:00Z"


def test_recovery_within_the_cap_records_the_real_summary(db, tmp_path):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    settings = _Settings()
    settings.artifact_base_uri = f"file://{tmp_path}/flaky"
    adapter, clock = _SwitchAdapter(), [0.0]
    _run_to_running(repos, adapter, settings, clock)
    _tick(repos, adapter, settings, clock)
    assert repos.data_jobs.get_job(jid)["state"] == "Running"
    adapter.summary = {"files": 3, "bytes": 30}                          # 마운트가 돌아왔다
    clock[0] += 30
    _tick(repos, adapter, settings, clock)
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded" and job["result_summary"] == {"files": 3, "bytes": 30}
    assert jid not in stepper_mod._summary_deferrals and jid not in stepper_mod._summary_defer_warned


def test_visible_base_finalizes_immediately(db, tmp_path):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    settings = _Settings()
    settings.artifact_base_uri = f"file://{tmp_path}"
    adapter, clock = _SwitchAdapter(), [0.0]
    _run_to_running(repos, adapter, settings, clock)
    _tick(repos, adapter, settings, clock)
    assert repos.data_jobs.get_job(jid)["result_summary"] == {"summary_unavailable": True}
    assert _deferral_events(repos, rid) == []


@pytest.mark.parametrize("ttl,cap", [(86400, SUMMARY_DEFER_MAX_SECONDS), (600, 300), (0, SUMMARY_DEFER_MAX_SECONDS)])
def test_cap_stays_well_inside_the_vcjob_ttl(db, ttl, cap):
    # GC 뒤 poll 은 FAILED(객체 없음) -- 상한이 TTL 을 넘으면 SUCCEEDED 잡이 실패로 둔갑한다. TTL 0 = GC 없음.
    settings = _Settings()
    settings.vcjob_ttl_seconds = ttl
    assert JobStepper(Repositories(db), StubExecutionAdapter(), settings=settings).summary_defer_cap() == cap
