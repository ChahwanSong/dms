"""I5: "record_event은 업무 트랜잭션에 참여하지 않는다"는 이 슬라이스의 핵심
불변식이다. sqlite에서는 이게 깨져도 증상이 안 보인다 -- 실패한 문장이 트랜잭션을
중단시키지 않고, record_event가 예외를 삼키기 때문이다. 진짜 실패 양상은
PostgreSQL 전용이다: `current transaction is aborted` -> 뒤따르는 COMMIT이
ROLLBACK으로 격하 -> 진단 기록 하나가 상태 전이 전체를 조용히 되돌린다.

그래서 sqlite 위에서도 확인할 수 있는 것 -- "events INSERT가 열린
`self._db.transaction()` 블록 밖에서 실행됐는가" -- 을 스파이로 관찰한다.
다섯 지점(planner의 plan_error, stepper의 step_error/terminate_failed/
summary_unreadable, data_jobs의 terminal_guard_skip) 전부를 각자의 실제 호출
경로로 재현해서 검사한다.
"""
from contextlib import contextmanager

import pytest
from dms.db import Database
from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, ExecutionError, StubExecutionAdapter
from dms.identity import StubIdentityResolver
from dms.migrations import migrate
from dms.planner import Planner
from dms.repositories import Repositories
from dms.stepper import JobStepper


class _TxTrackingDB:
    """record_event이 열린 트랜잭션 안에서 불리지 않는지 관찰하는 스파이.

    Database.transaction()은 BEGIN/COMMIT/ROLLBACK을 self._conn에 직접 쏘고
    self.execute()를 거치지 않는다(db.py 참고) -- execute()만 감싸서는 트랜잭션
    경계를 볼 수 없다. 그래서 transaction()과 execute() 양쪽을 감싸 깊이를
    추적한다. 실제 BEGIN/COMMIT/ROLLBACK과 실제 INSERT는 전부 내부 Database에
    그대로 위임한다 -- 이 스파이는 관찰만 하고 동작을 바꾸지 않는다.
    """

    def __init__(self, inner: Database):
        self._inner = inner
        self.depth = 0
        self.violations: list[str] = []

    @property
    def dialect(self):
        return self._inner.dialect

    def execute(self, sql: str, params: dict | None = None) -> None:
        if self.depth > 0 and "INSERT INTO events" in sql:
            self.violations.append(sql)
        self._inner.execute(sql, params)

    def query(self, sql: str, params: dict | None = None):
        return self._inner.query(sql, params)

    def query_one(self, sql: str, params: dict | None = None):
        return self._inner.query_one(sql, params)

    @contextmanager
    def transaction(self):
        self.depth += 1
        try:
            with self._inner.transaction():
                yield self
        finally:
            self.depth -= 1


@pytest.fixture
def spy(tmp_path):
    real_db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(real_db)
    return _TxTrackingDB(real_db)


class _PlannerSettings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


class _StepperSettings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def _scan_job(repos):
    rid = repos.requests.create(
        operation="scan", requester_id="alice", actor="alice", resource_key="k",
        payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1", target="a",
        options={}, tool="dscan",
        worker_pool={"tool": "dscan",
                     "identity": {"uid": 10001, "gid": 10000, "username": "alice",
                                  "groups": [], "privileged": False},
                     "candidates": {"primary": ["n1"]}, "process_count": 8,
                     "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def test_plan_error_event_is_recorded_outside_any_open_transaction(spy, monkeypatch):
    repos = Repositories(spy)
    rid = repos.requests.create(
        operation="scan", requester_id="alice", actor="alice", resource_key="k",
        payload={"storage": "s1", "target": "a", "options": {}, "owner_username": None},
        priority="mid")

    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(repos.accounts, "get", _boom)

    Planner(repos, StubIdentityResolver({}), settings=_PlannerSettings()).run_once(
        now_iso="2026-08-02T10:00:00Z")

    events = repos.observability.events_for_request(rid)
    assert len(events) == 1 and events[0]["event_type"] == "plan_error"
    assert spy.violations == []


def test_step_error_event_is_recorded_outside_any_open_transaction(spy, monkeypatch):
    repos = Repositories(spy)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()

    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(repos.storages, "get", _boom)  # _build_spec의 _abs()가 부른다

    JobStepper(repos, adapter, settings=_StepperSettings()).run_once()

    events = repos.observability.events_for_request(rid)
    assert len(events) == 1 and events[0]["event_type"] == "step_error"
    assert spy.violations == []


def test_terminate_failed_event_is_recorded_outside_any_open_transaction(spy):
    repos = Repositories(spy)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    ref = f"stub-preflight-{jid}"
    adapter.fail_terminate(ref)
    stepper = JobStepper(repos, adapter, settings=_StepperSettings())
    job = [j for j in repos.data_jobs.claim_steppable() if j["job_id"] == jid][0]
    repos.data_jobs.set_job_state(jid, DataJobState.CANCELLED,
                                  reason_code="cancelled_by_user", actor="alice")

    stepper._step_one(job)  # 낡은 스냅샷으로 계속 진행 -- best-effort terminate가 실패

    events = repos.observability.events_for_request(rid)
    assert len(events) == 1 and events[0]["event_type"] == "terminate_failed"
    assert spy.violations == []


def test_terminal_guard_skip_event_is_recorded_outside_any_open_transaction(spy):
    repos = Repositories(spy)
    rid, jid = _scan_job(repos)
    repos.data_jobs.set_job_state(jid, DataJobState.CANCELLED,
                                  reason_code="cancelled_by_user", actor="alice")

    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="stepper")  # 종단 가드 트립

    events = repos.observability.events_for_request(rid)
    assert len(events) == 1 and events[0]["event_type"] == "terminal_guard_skip"
    assert spy.violations == []


class _NullSummaryAdapter(StubExecutionAdapter):
    def read_summary(self, ref: str):
        return None


def test_summary_unreadable_event_is_recorded_outside_any_open_transaction(spy):
    repos = Repositories(spy)
    rid, jid = _scan_job(repos)
    adapter = _NullSummaryAdapter()
    stepper = JobStepper(repos, adapter, settings=_StepperSettings())
    stepper.run_once()  # Pending -> Preflight
    stepper.run_once()  # Preflight poll Succeeded -> Running (exec submit)
    stepper.run_once()  # Running poll Succeeded, read_summary() -> None

    events = repos.observability.events_for_request(rid)
    assert len(events) == 1 and events[0]["event_type"] == "summary_unreadable"
    assert spy.violations == []
