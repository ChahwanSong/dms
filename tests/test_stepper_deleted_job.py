"""요청 삭제 선행 보강(2026-10-08, 작업 삭제 스펙 §6.1) -- stepper 의 낡은 claim 스냅숏.

claim_steppable 은 잠금 없는 스냅숏이다. 그 뒤 요청이 취소·삭제되면 stepper 는 그대로 Pod/vcjob 을 제출하는데, 예전엔
set_phase_ref 가 없는 행에서 TypeError 로 끝나 회수(_reclaim_if_terminal)에 닿지 못했고, 닿아도 스냅숏의 비종단
상태로 폴백해 계속 진행했다 -- 요청 없는 실제 rm/sync 가 고아로 돈다. 여기서는 네 제출 경로 전부가 행 부재를 보고
방금 만든 ref 를 즉시 회수하고, 고아 이벤트를 request_id=NULL 로 남기는지 고정한다."""
import pytest

from dms.domain import DataJobState, RequestState
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


class _Recorder(StubExecutionAdapter):
    def __init__(self):
        super().__init__()
        self.terminated = []

    def terminate(self, ref):
        self.terminated.append(ref)
        super().terminate(ref)


def _seed_storage(repos, name):
    if repos.storages.get(name) is None:
        repos.storages.create(storage_name=name, mount_path=f"/{name}",
                              managed_root=f"/{name}/dms", backend_type="cephfs",
                              actor="test")


_WP = {"identity": {"uid": 10001, "gid": 10000, "username": "alice", "groups": [],
                    "privileged": False},
       "candidates": {"primary": ["n1"]}, "process_count": 8,
       "queue": "dms-data", "priority_class": "dms-mid"}


def _job(repos, op):
    if op == "scan":
        _seed_storage(repos, "s1")
        payload = {"storage": "s1", "target": "a"}
        cols = {"storage_name": "s1", "target": "a", "tool": "dscan"}
    else:
        _seed_storage(repos, "src")
        _seed_storage(repos, "dst")
        payload = {"source_storage": "src", "source": "a",
                   "destination_storage": "dst", "destination": "b"}
        cols = {"source_storage": "src", "source": "a", "destination_storage": "dst",
                "destination": "b", "tool": "dsync"}
    rid = repos.requests.create(operation=op, requester_id="alice", actor="alice",
                                resource_key=f"k-{op}", payload=payload, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation=op, priority="mid", options={},
                                     worker_pool={"tool": cols["tool"], **_WP},
                                     precondition={}, actor="planner", **cols)
    return rid, jid


def _delete_request(db, rid):
    """요청 삭제 트랜잭션의 DB 효과를 흉내(행 전부 제거). 정리 아웃박스는 이 보강의 관심사가 아니다."""
    jids = [r["job_id"] for r in db.query("SELECT job_id FROM data_jobs WHERE request_id = :r", {"r": rid})]
    pids = [r["plan_id"] for r in db.query("SELECT plan_id FROM plans WHERE request_id = :r", {"r": rid})]
    with db.transaction():
        for kind, ids in (("data_job", jids), ("plan", pids), ("request", [rid])):
            for i in ids:
                db.execute("DELETE FROM state_transitions WHERE entity_kind = :k AND entity_id = :i",
                           {"k": kind, "i": i})
        for table in ("data_jobs", "plans", "results", "events", "requests"):
            db.execute(f"DELETE FROM {table} WHERE request_id = :r", {"r": rid})


def _snapshot(repos, jid):
    return [j for j in repos.data_jobs.claim_steppable() if j["job_id"] == jid][0]


def _orphan_events(db):
    return db.query("SELECT * FROM events WHERE event_type = 'submitted_for_deleted_job'")


def _advance_to(repos, adapter, stepper, rid, jid, phase):
    """phase 를 제출하기 직전 상태로 만든다(그 제출은 다음 _step_one 이 한다)."""
    if phase == "preflight":
        return
    stepper.run_once()                                   # Pending → Preflight
    if phase in ("execution", "preview"):
        return                                           # Preflight poll Succeeded → 다음 제출
    # exec_preflight: sync 를 ConfirmPending 까지 보낸 뒤 confirm 을 흉내 낸다(test_stepper_sync 관례).
    stepper.run_once()                                   # → PreviewRunning
    stepper.run_once()                                   # → ConfirmPending
    repos.data_jobs.set_confirmed(jid, repos.data_jobs.get_job(jid)["preview_fingerprint"])
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")


@pytest.mark.parametrize("op, phase", [("scan", "preflight"), ("scan", "execution"),
                                       ("sync", "preview"), ("sync", "exec_preflight")])
def test_submit_after_row_deleted_reclaims_the_ref(db, op, phase):
    repos = Repositories(db)
    rid, jid = _job(repos, op)
    adapter = _Recorder()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3})
    stepper = JobStepper(repos, adapter, settings=_Settings())
    _advance_to(repos, adapter, stepper, rid, jid, phase)
    job = _snapshot(repos, jid)                          # 잠금 없는 스냅숏
    _delete_request(db, rid)                             # 그 사이 취소 + 삭제

    result = stepper._step_one(job)                      # 낡은 스냅숏으로 제출까지 간다 -- TypeError 없음

    ref = f"stub-{phase}-{jid}"
    assert [s.phase for s in adapter.submitted_specs()][-1] == phase
    assert result == "gone"
    assert adapter.terminated == [ref]
    [ev] = _orphan_events(db)
    assert ev["request_id"] is None                      # 지워진 id 로 쓰면 고아 이벤트다
    assert ev["severity"] == "warning" and ev["component"] == "stepper"
    from dms.db import load_json
    assert load_json(ev["payload"]) == {"job_id": jid, "request_id": rid, "ref": ref,
                                        "terminate_error": None}
    # 행을 되살리지 않는다(set_phase_ref 의 UPDATE 도, 상태 전이도 없다)
    assert db.query("SELECT 1 AS x FROM data_jobs WHERE job_id = :j", {"j": jid}) == []
    assert db.query("SELECT 1 AS x FROM state_transitions WHERE entity_id IN (:j, :r)",
                    {"j": jid, "r": rid}) == []
    assert db.query("SELECT 1 AS x FROM events WHERE request_id = :r", {"r": rid}) == []


def test_terminate_failure_on_deleted_job_is_still_recorded(db):
    repos = Repositories(db)
    rid, jid = _job(repos, "scan")
    adapter = StubExecutionAdapter()
    ref = f"stub-preflight-{jid}"
    adapter.fail_terminate(ref)
    stepper = JobStepper(repos, adapter, settings=_Settings())
    job = _snapshot(repos, jid)
    _delete_request(db, rid)

    assert stepper._step_one(job) == "gone"

    [ev] = _orphan_events(db)
    from dms.db import load_json
    assert ev["request_id"] is None and ev["severity"] == "error"   # 요청 없는 잡이 돌고 있을 수 있다
    assert load_json(ev["payload"])["terminate_error"] == "terminate_failed"


def test_reclaim_if_terminal_with_missing_row_terminates_instead_of_snapshot_fallback(db):
    # set_phase_ref 는 성공했는데 그 직후(get_job 전) 행이 사라진 좁은 창 -- 예전엔 (current or job) 로 스냅숏의
    # 비종단 상태를 읽어 계속 진행했다(set_job_state KeyError → step_error, ref 는 고아).
    repos = Repositories(db)
    rid, jid = _job(repos, "scan")
    adapter = _Recorder()
    stepper = JobStepper(repos, adapter, settings=_Settings())
    real = repos.data_jobs.set_phase_ref

    def set_then_delete(job_id, phase, ref):
        ok = real(job_id, phase, ref)
        _delete_request(db, rid)
        return ok
    repos.data_jobs.set_phase_ref = set_then_delete

    assert stepper._step_one(_snapshot(repos, jid)) == "gone"

    assert adapter.terminated == [f"stub-preflight-{jid}"]
    assert len(_orphan_events(db)) == 1


def test_set_phase_ref_reports_missing_row(db):
    repos = Repositories(db)
    rid, jid = _job(repos, "scan")
    assert repos.data_jobs.set_phase_ref(jid, "preflight", "pod/p1") is True
    assert repos.data_jobs.get_job(jid)["phase_refs"] == {"preflight": "pod/p1"}
    assert repos.data_jobs.set_phase_ref("0" * 32, "preflight", "pod/p2") is False
    assert db.query("SELECT 1 AS x FROM data_jobs WHERE job_id = :j", {"j": "0" * 32}) == []


def test_step_error_for_deleted_job_is_recorded_without_request_id(db):
    # 낡은 스냅숏의 Preflight 잡이 실패 판정까지 간 뒤 종단 전이(set_job_state)가 KeyError -- step_error 는 남되
    # 지워진 request_id 를 달지 않는다(id 는 payload 에).
    repos = Repositories(db)
    rid, jid = _job(repos, "scan")
    adapter = StubExecutionAdapter()
    stepper = JobStepper(repos, adapter, settings=_Settings())
    stepper.run_once()                                   # → Preflight
    job = _snapshot(repos, jid)
    adapter.script(f"stub-preflight-{jid}", [ExecStatus.FAILED])
    _delete_request(db, rid)
    repos.data_jobs.claim_steppable = lambda **_: [job]  # run_once 가 낡은 스냅숏을 집게 한다

    result = stepper.run_once()

    assert result[jid] == "error:KeyError"
    [ev] = db.query("SELECT * FROM events WHERE event_type = 'step_error'")
    from dms.db import load_json
    assert ev["request_id"] is None
    assert load_json(ev["payload"]) == {"job_id": jid, "request_id": rid, "job_deleted": True}


def test_cancel_race_without_delete_is_unchanged(db):
    # 행이 남아 있는 기존 경합(claim 뒤 취소)은 그대로 -- 종단 상태를 돌려주고 고아 이벤트를 쓰지 않는다.
    repos = Repositories(db)
    rid, jid = _job(repos, "scan")
    adapter = _Recorder()
    stepper = JobStepper(repos, adapter, settings=_Settings())
    job = _snapshot(repos, jid)
    repos.data_jobs.set_job_state(jid, DataJobState.CANCELLED, reason_code="cancelled_by_user",
                                  actor="alice")

    assert stepper._step_one(job) == "Cancelled"
    assert adapter.terminated == [f"stub-preflight-{jid}"]
    assert _orphan_events(db) == []
    assert repos.data_jobs.get_job(jid)["phase_refs"] == {"preflight": f"stub-preflight-{jid}"}
