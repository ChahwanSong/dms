"""Task 5: phase별 타임아웃 집행. 네 층을 검증한다:
1. stepper._build_spec — 정책의 preview/execution_timeout_seconds를 phase별로 spec에 싣는다.
2. execution_manifests의 세 빌더 — activeDeadlineSeconds를 조건부로 매니페스트에 넣는다.
3. execution_volcano의 deadline 판정 — k8s가 activeDeadlineSeconds로 죽였는지 보수적으로 판정한다.
4. stepper._poll_preview — 어댑터가 TIMED_OUT을 반환하면 TimedOut으로 종단한다.
"""
from dms.domain import RequestState
from dms.execution import ExecStatus, JobSpec, StubExecutionAdapter
from dms.execution_manifests import build_preflight_pod, build_volcano_job
from dms.execution_volcano import VolcanoExecutionAdapter, _deadline_exceeded
from dms.repositories import Repositories
from dms.stepper import JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


def _dsync_job(repos):
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="k", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync",
        worker_pool={"tool": "dsync", "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _scan_job(repos):
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


# --- Layer 1: stepper._build_spec ------------------------------------------------

def test_build_spec_preview_phases_use_preview_timeout_execution_uses_execution(db):
    # migrate()가 시드하는 dsync 기본 정책: preview=3600, execution=259200.
    repos = Repositories(db)
    _, jid = _dsync_job(repos)
    job = repos.data_jobs.get_job(jid)
    stepper = _stepper(repos, StubExecutionAdapter())
    for phase in ("preflight", "preview", "exec_preflight"):
        spec = stepper._build_spec(job, phase, dryrun=False)
        assert spec.timeout_seconds == 3600, phase
    spec = stepper._build_spec(job, "execution", dryrun=False)
    assert spec.timeout_seconds == 259200


def test_build_spec_maps_scan_tool_name_to_scan_policy_key(db):
    # job["tool"]은 "dscan"(실행 파일 이름)이지 정책 키 "scan"이 아니다 — placement.py의
    # TOOL_TO_POLICY를 거쳐야 한다. 시드 기본값(scan.preview=NULL)과 다른 값을 넣어,
    # 우연히 None과 일치하는 게 아니라 실제로 매핑이 동작함을 검증한다.
    repos = Repositories(db)
    repos.control.upsert_policy("scan", max_nodes=4, procs_per_node=8, queue="dms-data",
        default_priority="mid", max_priority="high",
        preview_timeout_seconds=900, execution_timeout_seconds=1800,
        enabled=True, actor="admin")
    _, jid = _scan_job(repos)
    job = repos.data_jobs.get_job(jid)
    stepper = _stepper(repos, StubExecutionAdapter())
    assert stepper._build_spec(job, "preflight", dryrun=False).timeout_seconds == 900
    assert stepper._build_spec(job, "execution", dryrun=False).timeout_seconds == 1800


def test_build_spec_no_policy_yields_none_timeout_for_all_phases(db):
    repos = Repositories(db)
    db.execute("DELETE FROM policies WHERE tool = 'dsync'")
    _, jid = _dsync_job(repos)
    job = repos.data_jobs.get_job(jid)
    stepper = _stepper(repos, StubExecutionAdapter())
    for phase in ("preflight", "preview", "exec_preflight", "execution"):
        assert stepper._build_spec(job, phase, dryrun=False).timeout_seconds is None, phase


# --- Layer 2: manifest builders --------------------------------------------------

_VOL = [{"name": "cephfs", "hostPath": {"path": "/cephfs"}, "mountPath": "/cephfs"}]


def _spec(**kw):
    base = dict(job_id="j1", phase="execution", operation="scan", tool="dscan",
                dryrun=False, identity={"uid": 10001}, paths={"target": "/cephfs/data"},
                options={}, candidates={"primary": ["n1"]}, process_count=8,
                queue="dms-data", priority_class="dms-mid",
                artifact_base="file:///cephfs/dms/artifacts")
    base.update(kw)
    return JobSpec(**base)


def test_build_volcano_job_sets_active_deadline_seconds_when_present():
    spec = _spec(operation="scan", tool="dscan", candidates={"primary": ["dms-w1"]},
                 paths={"target": "/cephfs/data"}, timeout_seconds=120)
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    assert m["spec"]["activeDeadlineSeconds"] == 120


def test_build_volcano_job_omits_active_deadline_seconds_when_none():
    spec = _spec(operation="scan", tool="dscan", candidates={"primary": ["dms-w1"]},
                 paths={"target": "/cephfs/data"})
    assert spec.timeout_seconds is None
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    assert "activeDeadlineSeconds" not in m["spec"]


def test_build_nsync_job_sets_active_deadline_seconds_when_present():
    spec = _spec(operation="sync", tool="nsync",
                 candidates={"source": ["dms-w1"], "destination": ["dms-w4"]},
                 paths={"source": "/cephfs-third/a", "source_storage": "cephfs-third",
                        "destination": "/cephfs-secondary/b",
                        "destination_storage": "cephfs-secondary"},
                 timeout_seconds=259200)
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    assert m["spec"]["activeDeadlineSeconds"] == 259200


def test_build_nsync_job_omits_active_deadline_seconds_when_none():
    spec = _spec(operation="sync", tool="nsync",
                 candidates={"source": ["dms-w1"], "destination": ["dms-w4"]},
                 paths={"source": "/cephfs-third/a", "source_storage": "cephfs-third",
                        "destination": "/cephfs-secondary/b",
                        "destination_storage": "cephfs-secondary"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    assert "activeDeadlineSeconds" not in m["spec"]


def test_build_preflight_pod_sets_active_deadline_seconds_when_present():
    spec = _spec(operation="scan", tool="dscan", phase="preflight",
                 paths={"target": "/cephfs/dms/a"}, timeout_seconds=60)
    m = build_preflight_pod(spec, job_image="i", namespace="dms", volumes=_VOL,
                            node="dms-w1")
    assert m["spec"]["activeDeadlineSeconds"] == 60


def test_build_preflight_pod_omits_active_deadline_seconds_when_none():
    spec = _spec(operation="scan", tool="dscan", phase="preflight",
                 paths={"target": "/cephfs/dms/a"})
    m = build_preflight_pod(spec, job_image="i", namespace="dms", volumes=_VOL,
                            node="dms-w1")
    assert "activeDeadlineSeconds" not in m["spec"]


# --- Layer 3: deadline 판정 (순수 함수 + 어댑터 poll 통합) -----------------------

def test_deadline_exceeded_pure_function_is_conservative():
    assert _deadline_exceeded({"status": {"reason": "DeadlineExceeded"}}) is True
    assert _deadline_exceeded(
        {"status": {"conditions": [{"reason": "DeadlineExceeded"}]}}) is True
    # 불확실한/무관한 신호는 전부 False로 유지 (오분류보다 보수적 유지).
    assert _deadline_exceeded({"status": {"phase": "Failed"}}) is False
    assert _deadline_exceeded({"status": {"reason": "Error"}}) is False
    assert _deadline_exceeded({}) is False
    assert _deadline_exceeded({"status": None}) is False


class _FakeK8s:
    def __init__(self):
        self.created = []
        self._objs = {}

    def create(self, manifest):
        self.created.append(manifest)
        self._objs[(manifest["kind"], manifest["metadata"]["name"])] = manifest

    def set_status(self, kind, name, status):
        self._objs.setdefault((kind, name), {"kind": kind})["status"] = status

    def get(self, kind, name, namespace):
        return self._objs.get((kind, name))

    def delete(self, kind, name, namespace):
        self._objs.pop((kind, name), None)

    def read_pod_log(self, name, namespace):
        return ""


def _volcano_spec(phase, op="scan", tool="dscan", cand=None):
    return JobSpec(job_id="job123456789abc", phase=phase, operation=op, tool=tool,
                   dryrun=False, identity={"uid": 10001, "gid": 10000, "username": "alice"},
                   paths={"target": "/cephfs/dms/a", "storage": "cephfs-dms"},
                   options={}, candidates=cand or {"primary": ["dms-w1"]},
                   process_count=8, queue="dms-data", priority_class="dms-mid",
                   artifact_base="file:///cephfs/dms/artifacts")


def _adapter(k8s):
    return VolcanoExecutionAdapter(
        k8s, job_image="reg/img:1", namespace="dms",
        storages_lookup=lambda n: {"mount_path": "/cephfs", "managed_root": "/cephfs/dms"},
        read_text=lambda path: None, artifact_base="file:///cephfs/dms/artifacts")


def test_poll_pod_deadline_exceeded_maps_to_timed_out():
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_volcano_spec("preflight"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Pod", name, {"phase": "Failed", "reason": "DeadlineExceeded"})
    assert a.poll(ref) == ExecStatus.TIMED_OUT


def test_poll_pod_failed_without_deadline_reason_stays_failed():
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_volcano_spec("preflight"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Pod", name, {"phase": "Failed", "reason": "Error"})
    assert a.poll(ref) == ExecStatus.FAILED


def test_poll_vcjob_deadline_exceeded_maps_to_timed_out():
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_volcano_spec("execution"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Job", name,
                   {"state": {"phase": "Failed"}, "reason": "DeadlineExceeded"})
    assert a.poll(ref) == ExecStatus.TIMED_OUT


def test_poll_vcjob_failed_without_deadline_reason_stays_failed():
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_volcano_spec("execution"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Job", name, {"state": {"phase": "Aborted"}})
    assert a.poll(ref) == ExecStatus.FAILED


def test_poll_composite_nsync_preflight_deadline_exceeded_propagates_timed_out():
    # nsync preflight는 src+dst 두 Pod로 결합 폴링된다("pods/" ref). 한쪽이
    # DeadlineExceeded면 결합 결과도 TIMED_OUT이어야지, FAILED도 SUCCEEDED도 아니라고
    # 조용히 RUNNING으로 떨어져 잡이 영영 멈춰 있으면 안 된다.
    k8s = _FakeK8s()

    def storages_lookup(n):
        return {"cephfs-third": {"mount_path": "/cephfs-third", "managed_root": "/cephfs-third"},
                "cephfs-secondary": {"mount_path": "/cephfs-secondary",
                                     "managed_root": "/cephfs-secondary"}}.get(
            n, {"mount_path": "/cephfs", "managed_root": "/cephfs/dms"})
    a = VolcanoExecutionAdapter(
        k8s, job_image="reg/img:1", namespace="dms", storages_lookup=storages_lookup,
        read_text=lambda p: None, artifact_base="file:///cephfs/dms/artifacts")
    spec = JobSpec(job_id="job123456789abc", phase="preflight", operation="sync",
                   tool="nsync", dryrun=False,
                   identity={"uid": 10001, "gid": 10000, "username": "alice"},
                   paths={"source": "/cephfs-third/a", "source_storage": "cephfs-third",
                          "destination": "/cephfs-secondary/b",
                          "destination_storage": "cephfs-secondary"},
                   options={}, candidates={"source": ["dms-w1"], "destination": ["dms-w4"]},
                   process_count=8, queue="dms-data", priority_class="dms-mid",
                   artifact_base="file:///cephfs/dms/artifacts")
    ref = a.submit(spec)
    n1, n2 = ref.split("/", 1)[1].split(",")
    k8s.set_status("Pod", n1, {"phase": "Running"})
    k8s.set_status("Pod", n2, {"phase": "Failed", "reason": "DeadlineExceeded"})
    assert a.poll(ref) == ExecStatus.TIMED_OUT


# --- Layer 4: stepper._poll_preview ----------------------------------------------

def test_preview_timed_out_finalizes_job_as_timed_out(db):
    repos = Repositories(db)
    rid, jid = _dsync_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()  # Pending → Preflight
    stepper.run_once()  # Preflight succeeded → PreviewRunning (preview submit)
    assert repos.data_jobs.get_job(jid)["state"] == "PreviewRunning"
    adapter.script(f"stub-preview-{jid}", [ExecStatus.TIMED_OUT])
    stepper.run_once()  # PreviewRunning poll TIMED_OUT → TimedOut
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "TimedOut"
    assert job["reason_code"] == "preview_timed_out"
    assert repos.requests.get(rid)["state"] == "Failed"


def test_preview_plain_failure_still_finalizes_as_failed(db):
    # 회귀 방지: TIMED_OUT이 아닌 실패는 여전히 preview_failed/Failed로 종단해야 한다.
    repos = Repositories(db)
    rid, jid = _dsync_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    stepper.run_once()
    adapter.script(f"stub-preview-{jid}", [ExecStatus.FAILED])
    stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Failed"
    assert job["reason_code"] == "preview_failed"
