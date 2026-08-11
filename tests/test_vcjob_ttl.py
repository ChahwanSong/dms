"""Task 4: Volcano Job의 ttlSecondsAfterFinished. 세 층 검증:
1. config.py -- DMS_VCJOB_TTL_SECONDS 환경변수 → Settings.vcjob_ttl_seconds (기본 86400).
2. stepper._build_spec -- 설정값을 JobSpec.ttl_seconds에 싣는다.
3. execution_manifests의 두 Volcano Job 빌더 -- **Job.spec**(task 템플릿이 아니라)에
   ttlSecondsAfterFinished를 조건부로 넣는다. v1.15.0 CRD의 Job.spec 허용 필드 목록에
   ttlSecondsAfterFinished는 포함돼 있다 -- activeDeadlineSeconds가 거기서 pruning되는
   것과는 다르다(그건 허용 목록에 없다 -- execution_manifests._apply_task_deadlines 참고).
4. preflight Pod은 이 필드를 아예 받지 않는다(Pod에는 존재하지 않는 필드 -- Task 5의
   GC 루프가 preflight Pod 회수를 담당한다).
"""
from dms.config import Settings
from dms.domain import RequestState
from dms.execution import JobSpec, StubExecutionAdapter
from dms.execution_manifests import build_preflight_pod, build_volcano_job
from dms.repositories import Repositories
from dms.stepper import JobStepper

VALID_ENV = {
    "DMS_DATABASE_URL": "sqlite:///tmp/dms.db",
    "DMS_SHARED_TOKEN": "tok-abc",
    "DMS_ADMIN_TOKEN": "adm-xyz",
    "DMS_SESSION_SECRET": "sess-123",
}


# --- Layer 1: config.py -----------------------------------------------------------

def test_config_vcjob_ttl_seconds_defaults_to_86400():
    s = Settings.from_env(VALID_ENV)
    assert s.vcjob_ttl_seconds == 86400


def test_config_vcjob_ttl_seconds_reads_env():
    s = Settings.from_env({**VALID_ENV, "DMS_VCJOB_TTL_SECONDS": "1800"})
    assert s.vcjob_ttl_seconds == 1800


# --- Layer 2: stepper._build_spec --------------------------------------------------

class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 3600


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


def _seed_storage(repos, name):
    # 슬라이스 24: _abs 의 결측 폴백(상대경로 반환)이 fail-closed 로 바뀌어
    # (stepper.StorageMissingAtStep) 스텝 가능한 잡은 실제 storage 행이 필요하다.
    if repos.storages.get(name) is None:
        repos.storages.create(storage_name=name, mount_path=f"/{name}",
                              managed_root=f"/{name}/dms", backend_type="cephfs",
                              actor="test")


def _scan_job(repos):
    _seed_storage(repos, "s1")
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


def test_build_spec_carries_configured_vcjob_ttl_seconds(db):
    repos = Repositories(db)
    _, jid = _scan_job(repos)
    job = repos.data_jobs.get_job(jid)
    stepper = _stepper(repos, StubExecutionAdapter())
    for phase in ("preflight", "preview", "exec_preflight", "execution"):
        assert stepper._build_spec(job, phase, dryrun=False).ttl_seconds == 3600, phase


# --- Layer 3: manifest builders -----------------------------------------------------

_VOL = [{"name": "cephfs", "hostPath": {"path": "/cephfs"}, "mountPath": "/cephfs"}]


def _spec(**kw):
    base = dict(job_id="j1", phase="execution", operation="scan", tool="dscan",
                dryrun=False, identity={"uid": 10001}, paths={"target": "/cephfs/data"},
                options={}, candidates={"primary": ["n1"]}, process_count=8,
                queue="dms-data", priority_class="dms-mid",
                artifact_base="file:///cephfs/dms/artifacts")
    base.update(kw)
    return JobSpec(**base)


def _nsync_spec(**kw):
    return _spec(operation="sync", tool="nsync",
                 candidates={"source": ["dms-w1"], "destination": ["dms-w4"]},
                 paths={"source": "/cephfs-third/a", "source_storage": "cephfs-third",
                        "destination": "/cephfs-secondary/b",
                        "destination_storage": "cephfs-secondary"}, **kw)


def test_build_volcano_job_sets_ttl_on_job_spec_not_task_template():
    spec = _spec(candidates={"primary": ["dms-w1"]}, ttl_seconds=3600)
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    assert m["spec"]["ttlSecondsAfterFinished"] == 3600
    for task in m["spec"]["tasks"]:
        assert "ttlSecondsAfterFinished" not in task["template"]["spec"], task["name"]


def test_build_volcano_job_omits_ttl_key_when_none():
    spec = _spec(candidates={"primary": ["dms-w1"]})
    assert spec.ttl_seconds is None
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    assert "ttlSecondsAfterFinished" not in m["spec"]


def test_build_volcano_job_omits_ttl_key_when_zero():
    spec = _spec(candidates={"primary": ["dms-w1"]}, ttl_seconds=0)
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    assert "ttlSecondsAfterFinished" not in m["spec"]


def test_build_nsync_job_sets_ttl_on_job_spec():
    m = build_volcano_job(_nsync_spec(ttl_seconds=7200), job_image="i",
                          namespace="dms", volumes=_VOL)
    assert m["spec"]["ttlSecondsAfterFinished"] == 7200
    for task in m["spec"]["tasks"]:
        assert "ttlSecondsAfterFinished" not in task["template"]["spec"], task["name"]


def test_build_nsync_job_omits_ttl_key_when_none():
    m = build_volcano_job(_nsync_spec(), job_image="i", namespace="dms", volumes=_VOL)
    assert "ttlSecondsAfterFinished" not in m["spec"]


def test_preflight_pod_never_gets_ttl_field():
    # Pod에는 ttlSecondsAfterFinished가 존재하지 않는 필드다 -- 넣으면 안 된다.
    spec = _spec(phase="preflight", ttl_seconds=3600)
    m = build_preflight_pod(spec, job_image="i", namespace="dms", volumes=_VOL,
                            node="dms-w1")
    assert "ttlSecondsAfterFinished" not in m["spec"]
