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



def _rm_job(repos, preview_summary, key="k-rm"):
    if repos.storages.get("cephfs-dms") is None:
        repos.storages.create(storage_name="cephfs-dms", mount_path="/cephfs",
                              managed_root="/cephfs/dms", backend_type="cephfs", actor="admin")
    rid = repos.requests.create(operation="rm", requester_id="alice", actor="alice", resource_key=key,
                                payload={"storage": "cephfs-dms", "target": "team/link", "options": {"recursive": True}},
                                priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="rm", priority="mid", storage_name="cephfs-dms",
                                     target="team/link", options={"recursive": True}, tool="drm",
                                     worker_pool={"identity": {"uid": 10001, "gid": 10000, "username": "alice",
                                                               "groups": [], "privileged": False},
                                                  "candidates": {"primary": ["dms-w1"]}, "process_count": 1,
                                                  "queue": "dms-data", "priority_class": "dms-mid"},
                                     precondition={}, actor="planner")
    if preview_summary is not None:
        repos.data_jobs.set_preview(jid, fingerprint="sha256:x", expires_at="2099-01-01T00:00:00Z", artifact_uri="x",
                                    summary=preview_summary)
    return jid


def test_rm_execution_spec_carries_the_previewed_target_kind(db):
    # 2026-10-28 rm 미리보기 종류 묶기: 실행 단계 spec 에만, 미리보기 summary 의 target_kind(러너 lstat)를 싣는다 -- 러너가
    # drm 직전 종류와 다르면 거부한다. 다른 단계·없는 값·범위 밖 값(변조 행)은 싣지 않는다.
    repos = Repositories(db)
    stepper = JobStepper(repos, StubExecutionAdapter(), settings=_Settings())
    jid = _rm_job(repos, {"returncode": 0, "files": 1, "bytes": None, "detail_version": 2, "tool": "drm",
                          "dryrun": True, "target_kind": "link"})
    job = repos.data_jobs.get_job(jid)
    assert stepper._build_spec(job, "execution", False).paths["expect_target_kind"] == "link"
    for phase in ("preflight", "preview", "exec_preflight"):
        assert "expect_target_kind" not in stepper._build_spec(job, phase, phase == "preview").paths


def test_rm_execution_spec_without_a_known_previewed_kind_does_not_bind(db):
    repos = Repositories(db)
    stepper = JobStepper(repos, StubExecutionAdapter(), settings=_Settings())
    for i, summary in enumerate((None, {"returncode": 0, "files": 1, "bytes": None},
                                 {"returncode": 0, "files": 1, "bytes": None, "target_kind": "rm -rf"})):
        jid = _rm_job(repos, summary, key=f"k-rm-{i}")
        assert "expect_target_kind" not in stepper._build_spec(repos.data_jobs.get_job(jid), "execution", False).paths
