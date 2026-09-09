"""Tests for controller job-stepper loop integration."""
from dms.controller import build_loops, run_all_once
from dms.execution import StubExecutionAdapter
from dms.domain import RequestState
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
    vcjob_ttl_seconds = 86400
    pod_gc_after_seconds = 3600
    pod_gc_interval_seconds = 600


def _seed_storage(repos, name):
    # 슬라이스 24: _abs 의 결측 폴백(상대경로 반환)이 fail-closed 로 바뀌어
    # (stepper.StorageMissingAtStep) 스텝 가능한 잡은 실제 storage 행이 필요하다.
    if repos.storages.get(name) is None:
        repos.storages.create(storage_name=name, mount_path=f"/{name}",
                              managed_root=f"/{name}/dms", backend_type="cephfs",
                              actor="test")


def test_stepper_loop_registered_second(db):
    loops = build_loops(_Settings(), Repositories(db))
    assert [l.name for l in loops] == [
        "planner", "job-stepper", "storage-reconciler", "retention",
        "batch-orchestrator", "pod-gc", "artifact-base-check"]
    assert loops[1].interval_seconds == 5


def test_stepper_loop_advances_pending_job(db):
    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    _seed_storage(repos, "s1")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={"identity": {"uid": 1, "gid": 1, "username": "alice"},
                     "candidates": {"primary": ["n1"]},
                     "process_count": 8, "queue": "dms-data",
                     "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")  # job-stepper 루프가 Pending → Preflight
    assert repos.data_jobs.get_job(jid)["state"] == "Preflight"


def test_expired_preview_sweep_finalizes_request(db):
    from dms.domain import DataJobState, RequestState
    from dms.execution import StubExecutionAdapter
    repos = Repositories(db)
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="k", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    _seed_storage(repos, "src")
    _seed_storage(repos, "dst")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync", worker_pool={}, precondition={}, actor="planner")
    # ConfirmPending + 과거 만료 시각
    repos.data_jobs.set_preview(jid, fingerprint="sha256:abc",
        expires_at="2000-01-01T00:00:00Z", artifact_uri=None)
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    # controller stepper 루프 1회
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")
    # 잡은 PreviewExpired, 요청은 Rejected + results 행
    assert repos.data_jobs.get_job(jid)["state"] == "PreviewExpired"
    assert repos.requests.get(rid)["state"] == "Rejected"
    result = db.query_one("SELECT terminal_state, reason_code FROM results WHERE request_id = :r",
                          {"r": rid})
    assert result["terminal_state"] == "Rejected" and result["reason_code"] == "preview_expired"
    # resource_key 잠금 해제 확인: 동일 key 새 요청은 find_active가 비터미널로 잡지 않음
    assert repos.requests.find_active("k") is None


def test_stepper_rejects_jobs_without_a_usable_identity(db):
    """신원 가드(stepper.IdentityMissingAtStep, 2026-09-09): 어댑터가 uid/gid 부재를
    0 으로 기본값 처리하기 전에 종단시킨다 -- DB 가 신뢰 경계라 unknown_tool 과
    같은 층이다. uid 0 자체는 privileged 플래그와 짝이 맞으면 정당하다."""
    from dms.stepper import identity_problem
    assert identity_problem({"uid": 1, "gid": 1, "username": "alice"}) is None
    assert identity_problem({"uid": 0, "gid": 0, "username": "root",
                             "privileged": True}) is None
    assert identity_problem(None) == "identity_missing"
    assert identity_problem({}) == "uid_missing"
    assert identity_problem({"uid": True, "gid": 1, "username": "a"}) == "uid_missing"
    assert identity_problem({"uid": 1, "gid": "1", "username": "a"}) == "gid_missing"
    assert identity_problem({"uid": 1, "gid": 1}) == "username_missing"
    assert identity_problem({"uid": -1, "gid": 1, "username": "a"}) == "uid_negative"
    assert identity_problem({"uid": 1, "gid": -5, "username": "a"}) == "gid_negative"
    assert identity_problem({"uid": 0, "gid": 0, "username": "x"}) == "privileged_flag_mismatch"
    assert identity_problem({"uid": 5, "gid": 5, "username": "x",
                             "privileged": True}) == "privileged_flag_mismatch"

    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k-noident", payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    _seed_storage(repos, "s1")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={"candidates": {"primary": ["n1"]}, "process_count": 1,
                     "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    adapter = StubExecutionAdapter()
    loops = build_loops(_Settings(), repos, execution_adapter=adapter)
    run_all_once(loops, repos, holder="h1")
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Rejected"
    assert job["phase_refs"] in (None, {})          # 제출 자체가 없었다
    result = db.query_one("SELECT reason_code FROM results WHERE request_id = :r", {"r": rid})
    assert result["reason_code"] == "identity_missing_at_step"
    events = db.query("SELECT event_type, message FROM events WHERE event_type = :t",
                      {"t": "identity_missing_at_step"})
    assert len(events) == 1 and "identity_missing" in events[0]["message"]

    # 비-dict worker_pool(변조 행)도 step_error 루프가 아니라 같은 fail-closed 경로
    rid2 = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k-badwp", payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid2, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid2, RequestState.RUNNING, actor="planner")
    plan2 = repos.data_jobs.create_plan(rid2, actor="planner")
    jid2 = repos.data_jobs.create_job(rid2, plan2, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool=["not", "a", "dict"], precondition={}, actor="planner")
    run_all_once(loops, repos, holder="h1")
    assert repos.data_jobs.get_job(jid2)["state"] == "Rejected"
