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
    vcjob_ttl_seconds = 86400


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
    # rm 대상 봉쇄의 기준(2026-10-21) -- target 을 조합한 바로 그 값이 함께 실린다(대상 하나 연산 공통, 단건·배치 자식 같은 경로).
    assert spec.paths["managed_root"] == "/cephfs/dms"


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


def _pv_job(repos, key, operation, payload, **job):
    rid = repos.requests.create(operation=operation, requester_id="alice", actor="alice",
        resource_key=key, payload=payload, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    return repos.data_jobs.create_job(rid, plan_id, operation=operation, priority="mid",
        options=job.pop("options", {}),
        worker_pool={"identity": {"uid": 10001, "gid": 10000, "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["dms-w1"]}, "process_count": 8, "queue": "dms-data",
            "priority_class": "dms-mid"},
        precondition={}, actor="planner", **job)


def test_pv_containment_roots_follow_payload_blocks(db):
    """PV 주소 쪽만 paths["contain"] 에 PV 루트 절대경로(2026-10-26 봉쇄) -- 직접 경로 쪽은 키가 없다. 블록 root 의 '..' 가
    managed_root 밖으로 나가면 빈 루트(봉쇄가 막는다), PV 주소 rm 은 봉쇄 기준(containment_root)이 PV 루트로 좁혀진다."""
    repos = Repositories(db)
    repos.storages.create(storage_name="pv-gpfs", mount_path="/k8s_pv/gpfs", managed_root="/k8s_pv/gpfs",
                          backend_type="gpfs", actor="admin")
    repos.storages.create(storage_name="cephfs-dms", mount_path="/cephfs", managed_root="/cephfs/dms",
                          backend_type="cephfs", actor="admin")
    vol = "pvc-52c6/pvc-52c6-data"
    blk = {"cluster": "c", "namespace": "team-a", "pvc": "models", "volume": "pvc-52c6", "root": vol, "path": "x"}
    # sync: 소스는 PV, 목적지는 직접 경로
    _pv_job(repos, "k1", "sync", {"source_storage": "pv-gpfs", "source": f"{vol}/x", "source_pv": blk,
            "destination_storage": "cephfs-dms", "destination": "team/b"},
            source_storage="pv-gpfs", source=f"{vol}/x", destination_storage="cephfs-dms", destination="team/b",
            tool="dsync")
    # scan: 블록 root 가 managed_root 밖으로 정규화된다(변조 행)
    _pv_job(repos, "k2", "scan", {"storage": "pv-gpfs", "target": f"{vol}/x", "pv": {**blk, "root": "a/../../.."}},
            storage_name="pv-gpfs", target=f"{vol}/x", tool="dscan")
    # rm: PV 안 하위 경로
    _pv_job(repos, "k3", "rm", {"storage": "pv-gpfs", "target": f"{vol}/x", "pv": blk},
            storage_name="pv-gpfs", target=f"{vol}/x", tool="drm", options={"recursive": True})
    adapter = StubExecutionAdapter()
    JobStepper(repos, adapter, settings=_Settings()).run_once()
    specs = {s.operation: s for s in adapter.submitted_specs()}
    assert specs["sync"].paths["contain"] == {"source": "/k8s_pv/gpfs/pvc-52c6/pvc-52c6-data"}
    assert specs["scan"].paths["contain"] == {"target": ""}
    assert specs["rm"].paths["contain"] == {"target": "/k8s_pv/gpfs/pvc-52c6/pvc-52c6-data"}
    assert specs["rm"].paths["containment_root"] == "/k8s_pv/gpfs/pvc-52c6/pvc-52c6-data"
    assert specs["rm"].paths["managed_root"] == "/k8s_pv/gpfs"
