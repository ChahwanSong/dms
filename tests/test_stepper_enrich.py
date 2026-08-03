from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///cephfs/dms/artifacts"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


def _scan_job(repos):
    repos.storages.create(storage_name="cephfs-dms", mount_path="/cephfs",
                          managed_root="/cephfs/dms", backend_type="cephfs",
                          actor="admin")
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "cephfs-dms", "target": "team/data"},
        priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="cephfs-dms", target="team/data", options={}, tool="dscan",
        worker_pool={"identity": {"uid": 10001, "gid": 10000, "username": "alice",
            "groups": [], "privileged": False}, "candidates": {"primary": ["dms-w1"]},
            "process_count": 8, "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def test_build_spec_uses_absolute_paths(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    JobStepper(repos, adapter, settings=_Settings()).run_once()  # preflight submit
    spec = adapter.submitted_specs()[0]
    assert spec.paths["target"] == "/cephfs/dms/team/data"  # managed_root + rel


def test_sync_recheck_preflight_before_execution(db):
    repos = Repositories(db)
    repos.storages.create(storage_name="src", mount_path="/cephfs-third",
        managed_root="/cephfs-third", backend_type="cephfs", actor="admin")
    repos.storages.create(storage_name="dst", mount_path="/cephfs-secondary",
        managed_root="/cephfs-secondary", backend_type="cephfs", actor="admin")
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="k2", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync",
        worker_pool={"identity": {"uid": 10001, "gid": 10000, "username": "alice",
            "groups": [], "privileged": False}, "candidates": {"primary": ["dms-w1"]},
            "process_count": 8, "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3})
    stepper = JobStepper(repos, adapter, settings=_Settings())
    stepper.run_once()  # preflight
    stepper.run_once()  # preview submit
    stepper.run_once()  # preview succeeded → ConfirmPending
    fp = repos.data_jobs.get_job(jid)["preview_fingerprint"]
    repos.data_jobs.set_confirmed(jid, fp)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    stepper.run_once()  # Executing: exec_preflight submit (재검증)
    assert repos.data_jobs.get_job(jid)["state"] == "Executing"
    assert [s for s in adapter.submitted_specs() if s.phase == "preflight"]  # 재검증 preflight
    stepper.run_once()  # exec_preflight succeeded → execution submit
    stepper.run_once()  # execution succeeded → Succeeded
    assert repos.data_jobs.get_job(jid)["state"] == "Succeeded"
    # 절대경로 확인
    exec_spec = [s for s in adapter.submitted_specs() if s.phase == "execution"][0]
    assert exec_spec.paths["source"] == "/cephfs-third/a"
    assert exec_spec.paths["destination"] == "/cephfs-secondary/b"
