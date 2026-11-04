"""Task 5: phase별 타임아웃 집행. 네 층을 검증한다:
1. stepper._build_spec — 정책의 preview/execution_timeout_seconds를 미리보기·실행 phase에 싣고, 사전 점검·실행 전 재점검은
   정책과 무관한 고정 시한(PREFLIGHT_DEADLINE_SECONDS, 2026-11-04 결함 #5)이다.
2. execution_manifests의 세 빌더 — activeDeadlineSeconds를 조건부로 매니페스트에 넣는다.
3. execution_volcano의 deadline 판정 — k8s가 activeDeadlineSeconds로 죽였는지 보수적으로 판정한다. 끝난 vcjob 은 라이브
   모양({phase: Aborted}, 사유 없음)이라 task 파드의 DeadlineExceeded 로 판정한다(2026-11-04 결함 #4).
4. stepper — 어댑터가 TIMED_OUT을 반환하면 단계별 사유로 TimedOut 종단한다(미리보기·실행·사전 점검·실행 전 재점검).
"""
import json

import pytest

from dms.domain import RequestState
from dms.execution import ExecStatus, ExecutionError, JobSpec, StubExecutionAdapter
from dms.execution_manifests import build_preflight_pod, build_volcano_job
from dms.execution_volcano import (VolcanoExecutionAdapter, _deadline_exceeded,
                                   _vcjob_deadline_exceeded)
from dms.repositories import Repositories
from dms.stepper import PREFLIGHT_DEADLINE_SECONDS, JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


def _seed_storage(repos, name):
    # 슬라이스 24: _abs 의 결측 폴백(상대경로 반환)이 fail-closed 로 바뀌어
    # (stepper.StorageMissingAtStep) 스텝 가능한 잡은 실제 storage 행이 필요하다.
    if repos.storages.get(name) is None:
        repos.storages.create(storage_name=name, mount_path=f"/{name}",
                              managed_root=f"/{name}/dms", backend_type="cephfs",
                              actor="test")


def _dsync_job(repos):
    _seed_storage(repos, "src")
    _seed_storage(repos, "dst")
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="k", payload={"run_as_root": True, "source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync",
        worker_pool={"tool": "dsync", "identity": {"uid": 0, "gid": 0,
            "username": "alice", "groups": [], "privileged": True},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _scan_job(repos):
    _seed_storage(repos, "s1")
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"run_as_root": True, "storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={"tool": "dscan", "identity": {"uid": 0, "gid": 0,
            "username": "alice", "groups": [], "privileged": True},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


# --- Layer 1: stepper._build_spec ------------------------------------------------

def test_build_spec_preview_uses_preview_timeout_execution_uses_execution_checks_use_fixed_deadline(db):
    # migrate()가 시드하는 dsync 기본 정책: preview=43200(12h), execution=86400(24h). 정책의 두 값은 미리보기·실행 vcjob 에만
    # 걸리고, 사전 점검·실행 전 재점검 파드는 정책과 무관한 고정 시한이다(2026-11-04 결함 #5 -- 예전엔 preview 값을 같이 썼다).
    repos = Repositories(db)
    _, jid = _dsync_job(repos)
    job = repos.data_jobs.get_job(jid)
    stepper = _stepper(repos, StubExecutionAdapter())
    assert stepper._build_spec(job, "preview", dryrun=True).timeout_seconds == 43200
    for phase in ("preflight", "exec_preflight"):
        assert stepper._build_spec(job, phase, dryrun=False).timeout_seconds == PREFLIGHT_DEADLINE_SECONDS == 600, phase
    assert stepper._build_spec(job, "execution", dryrun=False).timeout_seconds == 86400


def test_build_spec_check_deadline_ignores_a_tiny_preview_policy(db):
    # 라이브 PO-03·03b 재현 조건: rm·dsync 정책 preview_timeout_seconds 1초 -- 점검 파드가 1초에 죽어 미리보기 vcjob 이 서지도
    # 못했다. 이제 점검은 고정 시한, 미리보기만 1초다.
    repos = Repositories(db)
    repos.control.upsert_policy("dsync", max_nodes=4, procs_per_node=8, queue="dms-data",
        default_priority="mid", max_priority="high",
        preview_timeout_seconds=1, execution_timeout_seconds=1,
        enabled=True, actor="admin")
    _, jid = _dsync_job(repos)
    job = repos.data_jobs.get_job(jid)
    stepper = _stepper(repos, StubExecutionAdapter())
    for phase in ("preflight", "exec_preflight"):
        assert stepper._build_spec(job, phase, dryrun=False).timeout_seconds == PREFLIGHT_DEADLINE_SECONDS, phase
    assert stepper._build_spec(job, "preview", dryrun=True).timeout_seconds == 1
    assert stepper._build_spec(job, "execution", dryrun=False).timeout_seconds == 1


def test_build_spec_maps_scan_tool_name_to_scan_policy_key(db):
    # job["tool"]은 "dscan"(실행 파일 이름)이지 정책 키 "scan"이 아니다 — placement.py의
    # TOOL_TO_POLICY를 거쳐야 한다. 시드 기본값(43200/86400)과 다른 값을 일부러 넣어,
    # 우연히 기본값과 일치하는 게 아니라 실제로 매핑이 동작함을 검증한다(실행 값). scan 에는 미리보기가 없어 정책의 미리보기
    # 값은 어느 단계에도 쓰이지 않는다 -- 사전 점검은 고정 시한(2026-11-04 결함 #5; 예전엔 이 값 900 이 점검 시한이었다).
    repos = Repositories(db)
    repos.control.upsert_policy("scan", max_nodes=4, procs_per_node=8, queue="dms-data",
        default_priority="mid", max_priority="high",
        preview_timeout_seconds=900, execution_timeout_seconds=1800,
        enabled=True, actor="admin")
    _, jid = _scan_job(repos)
    job = repos.data_jobs.get_job(jid)
    stepper = _stepper(repos, StubExecutionAdapter())
    assert stepper._build_spec(job, "preflight", dryrun=False).timeout_seconds == PREFLIGHT_DEADLINE_SECONDS
    assert stepper._build_spec(job, "execution", dryrun=False).timeout_seconds == 1800


def test_build_spec_no_policy_yields_none_timeout_for_vcjob_phases(db):
    # 정책 행이 지워진 운영 조작 -- 미리보기·실행은 종전대로 타임아웃 없음, 점검 파드는 정책과 무관하니 고정 시한 그대로.
    repos = Repositories(db)
    db.execute("DELETE FROM policies WHERE tool = 'dsync'")
    _, jid = _dsync_job(repos)
    job = repos.data_jobs.get_job(jid)
    stepper = _stepper(repos, StubExecutionAdapter())
    for phase in ("preview", "execution"):
        assert stepper._build_spec(job, phase, dryrun=False).timeout_seconds is None, phase
    for phase in ("preflight", "exec_preflight"):
        assert stepper._build_spec(job, phase, dryrun=False).timeout_seconds == PREFLIGHT_DEADLINE_SECONDS, phase


# --- Layer 2: manifest builders --------------------------------------------------

_VOL = [{"name": "cephfs", "hostPath": {"path": "/cephfs"}, "mountPath": "/cephfs"}]
_NSYNC_ROLE_VOL = {"source": _VOL, "destination": _VOL}


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
                        "destination_storage": "cephfs-secondary", "source_root": "/", "destination_root": "/"}, **kw)


def _deadlines(manifest):
    """(task name -> template.spec.activeDeadlineSeconds) — 없으면 키 자체가 없다."""
    return {t["name"]: t["template"]["spec"].get("activeDeadlineSeconds")
            for t in manifest["spec"]["tasks"]}


def test_build_volcano_job_sets_active_deadline_on_every_task_pod_spec():
    # activeDeadlineSeconds는 Volcano Job.spec의 필드가 아니다 — v1.15.0 CRD에 없고
    # preserve-unknown-fields도 아니라 API 서버가 조용히 prune한다(create는 성공하고
    # 데드라인만 사라진다). 실제 PodSpec 필드인 tasks[i].template.spec에 있어야 한다.
    spec = _spec(operation="scan", tool="dscan", candidates={"primary": ["dms-w1"]},
                 paths={"target": "/cephfs/data"}, timeout_seconds=120)
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    assert _deadlines(m) == {"launcher": 120, "worker": 120}
    assert "activeDeadlineSeconds" not in m["spec"]


def test_build_volcano_job_omits_active_deadline_seconds_when_none():
    spec = _spec(operation="scan", tool="dscan", candidates={"primary": ["dms-w1"]},
                 paths={"target": "/cephfs/data"})
    assert spec.timeout_seconds is None
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    assert "activeDeadlineSeconds" not in m["spec"]
    for task in m["spec"]["tasks"]:
        assert "activeDeadlineSeconds" not in task["template"]["spec"], task["name"]


def test_build_nsync_job_sets_active_deadline_on_every_task_pod_spec():
    m = build_volcano_job(_nsync_spec(timeout_seconds=259200), job_image="i",
                          namespace="dms", volumes=_VOL, role_volumes=_NSYNC_ROLE_VOL)
    assert _deadlines(m) == {"launcher": 259200, "source-worker": 259200,
                             "destination-worker": 259200}
    assert "activeDeadlineSeconds" not in m["spec"]


def test_build_nsync_job_omits_active_deadline_seconds_when_none():
    m = build_volcano_job(_nsync_spec(), job_image="i", namespace="dms", volumes=_VOL,
                          role_volumes=_NSYNC_ROLE_VOL)
    assert "activeDeadlineSeconds" not in m["spec"]
    for task in m["spec"]["tasks"]:
        assert "activeDeadlineSeconds" not in task["template"]["spec"], task["name"]


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
    # 파드: kubelet이 status.phase=Failed + status.reason=DeadlineExceeded를 쓴다.
    assert _deadline_exceeded({"status": {"reason": "DeadlineExceeded"}}) is True
    assert _deadline_exceeded(
        {"status": {"conditions": [{"reason": "DeadlineExceeded"}]}}) is True
    # 불확실한/무관한 신호는 전부 False로 유지 (오분류보다 보수적 유지).
    assert _deadline_exceeded({"status": {"phase": "Failed"}}) is False
    assert _deadline_exceeded({"status": {"reason": "Error"}}) is False
    assert _deadline_exceeded({}) is False
    assert _deadline_exceeded({"status": None}) is False


def test_vcjob_deadline_exceeded_reads_status_state_not_status_reason():
    # Volcano Job의 reason/message는 status.state 안에만 있다 — v1.15.0 CRD의
    # status.conditions[] 항목은 {lastTransitionTime, status}뿐이라 reason이 아예 없고,
    # 최상위 status.reason도 Volcano가 쓰지 않는다. (라이브 AbortJob 은 state 에 사유를 남기지 않는다 -- 그 모양은 아래
    # test_poll_vcjob_aborted_* 가 파드로 판정한다. 이 순수 함수는 사유를 싣는 모양을 위해 남는다.)
    assert _vcjob_deadline_exceeded(
        {"status": {"state": {"phase": "Failed", "reason": "DeadlineExceeded"}}}) is True
    assert _vcjob_deadline_exceeded(
        {"status": {"state": {"phase": "Failed",
                              "message": "pod dms-x failed: DeadlineExceeded"}}}) is True
    # 평범한 실패/무관한 신호는 False → FAILED 유지 (보수적 기본값).
    assert _vcjob_deadline_exceeded(
        {"status": {"state": {"phase": "Failed", "reason": "PodFailed"}}}) is False
    assert _vcjob_deadline_exceeded({"status": {"state": {"phase": "Failed"}}}) is False
    assert _vcjob_deadline_exceeded({"status": {"state": None}}) is False
    assert _vcjob_deadline_exceeded({}) is False


class _FakeK8s:
    """apiserver 흉내. Volcano 가 vcjob task 로 만드는 파드는 add_task_pod 로 넣는다(라벨 volcano.sh/job-name -- 실 Volcano 가
    붙이는 것, read_log·시한 판정이 이 라벨로 찾는다). 나열은 "k=v,k2" 꼴 셀렉터만(실 셀렉터의 부분집합)."""

    def __init__(self):
        self.created = []
        self._objs = {}
        self.pod_lists = []           # list_pod_briefs 셀렉터 기록
        self.fail_list = None         # list_pod_briefs 가 던질 예외

    def create(self, manifest):
        self.created.append(manifest)
        self._objs[(manifest["kind"], manifest["metadata"]["name"])] = manifest

    def set_status(self, kind, name, status):
        self._objs.setdefault((kind, name), {"kind": kind})["status"] = status

    def add_task_pod(self, vcjob, pod, phase, reason=None, task="worker"):
        status = {"phase": phase}
        if reason is not None:
            status["reason"] = reason
            if reason == "DeadlineExceeded":
                status["message"] = "Pod was active on the node longer than the specified deadline"   # kubelet 문구
        self._objs[("Pod", pod)] = {"kind": "Pod", "metadata": {"name": pod, "labels": {
            "volcano.sh/job-name": vcjob, "volcano.sh/task-spec": task}}, "status": status}

    def get(self, kind, name, namespace):
        return self._objs.get((kind, name))

    def delete(self, kind, name, namespace):
        self._objs.pop((kind, name), None)

    def read_pod_log(self, name, namespace):
        return ""

    def _select(self, kind, selector):
        out = []
        for (k, _name), obj in self._objs.items():
            labels = (obj.get("metadata") or {}).get("labels") or {}
            if k == kind and all((labels.get(t.split("=", 1)[0]) == t.split("=", 1)[1]) if "=" in t else (t in labels)
                                 for t in selector.split(",")):
                out.append(obj)
        return out

    def list_pod_briefs(self, namespace, label_selector):
        # 실 KubernetesClient.list_pod_briefs 와 같은 키(reason = 파드 status.reason, 없으면 None).
        self.pod_lists.append((namespace, label_selector))
        if self.fail_list is not None:
            raise self.fail_list
        return [{"name": o["metadata"]["name"], "node": None, "images": {},
                 "phase": (o.get("status") or {}).get("phase") or "",
                 "reason": (o.get("status") or {}).get("reason"),
                 "waiting_reason": None, "deleting": False}
                for o in self._select("Pod", label_selector)]

    def list_vcjob_briefs(self, namespace, label_selector):
        return [{"name": o["metadata"]["name"], "deleting": False, "uid": None}
                for o in self._select("Job", label_selector)]


# 라이브 AbortJob 의 vcjob 상태 모양(2026-11-04 기능 점검 PO-04 -- status.state 에 phase 만, reason·message 없음). 시각·카운터는
# 예시값이다.
_ABORTED = {"state": {"phase": "Aborted", "lastTransitionTime": "2026-11-04T00:00:00Z"},
            "minAvailable": 2, "failed": 1, "running": 0, "pending": 0}


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


@pytest.mark.parametrize("phase", ["execution", "preview"])
def test_poll_vcjob_aborted_with_a_deadline_exceeded_task_pod_is_timed_out(phase):
    # 라이브 모양(2026-11-04 결함 #4): 워커 파드가 activeDeadlineSeconds 로 죽으면(kubelet: Failed + DeadlineExceeded) 정책
    # PodFailed→AbortJob 이 vcjob 을 {phase: Aborted}(사유 없음)로 끝낸다. Running 이던 launcher 는 abort 가 지우고(아래엔
    # 없다), Failed 파드는 남는다(Volcano PodRetainPhaseSoft). 예전 판정은 state 만 봐서 FAILED 였다.
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_volcano_spec(phase))
    name = ref.split("/", 1)[1]
    k8s.set_status("Job", name, dict(_ABORTED))
    k8s.add_task_pod(name, f"{name}-worker-0", "Failed", "DeadlineExceeded")
    k8s.add_task_pod("dms-scan-execution-otherjob0000", "other-worker-0", "Succeeded")   # 남의 vcjob 파드는 안 본다
    assert a.poll(ref) == ExecStatus.TIMED_OUT
    assert k8s.pod_lists == [("dms", f"volcano.sh/job-name={name}")]       # read_log 와 같은 Volcano 라벨


def test_poll_vcjob_failed_phase_with_a_deadline_exceeded_task_pod_is_timed_out():
    # vcjob phase Failed(사유 없음)도 끝난 상태다 -- 같은 파드 판정.
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_volcano_spec("execution"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Job", name, {"state": {"phase": "Failed"}})
    k8s.add_task_pod(name, f"{name}-launcher-0", "Failed", "DeadlineExceeded", task="launcher")
    assert a.poll(ref) == ExecStatus.TIMED_OUT


def test_poll_vcjob_state_reason_shape_is_still_timed_out_without_pods():
    # state 에 사유를 싣는 모양(slice7 이 가정한 모양)은 파드를 보지 않고도 그대로 TIMED_OUT.
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_volcano_spec("execution"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Job", name, {"state": {"phase": "Failed",
                                           "reason": "DeadlineExceeded"}})
    assert a.poll(ref) == ExecStatus.TIMED_OUT
    assert k8s.pod_lists == []


def test_poll_vcjob_aborted_with_ordinary_pod_failure_stays_failed():
    # 평범한 실패(도구 비0 종료 -- reason 없음 또는 Error)로 abort 된 vcjob 은 종전대로 FAILED.
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_volcano_spec("execution"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Job", name, dict(_ABORTED))
    k8s.add_task_pod(name, f"{name}-worker-0", "Failed")
    k8s.add_task_pod(name, f"{name}-worker-1", "Failed", "Error")
    k8s.add_task_pod(name, f"{name}-launcher-0", "Succeeded", task="launcher")
    assert a.poll(ref) == ExecStatus.FAILED


def test_poll_vcjob_aborted_with_no_pods_left_stays_failed():
    # 파드가 이미 없다(지워짐) -- 시한 증거가 없으니 보수적으로 FAILED(slice7 결정: 불확실하면 FAILED).
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_volcano_spec("execution"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Job", name, dict(_ABORTED))
    assert a.poll(ref) == ExecStatus.FAILED
    assert k8s.pod_lists == [("dms", f"volcano.sh/job-name={name}")]


def test_poll_vcjob_ordinary_failed_without_deadline_reason_stays_failed():
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_volcano_spec("execution"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Job", name, {"state": {"phase": "Failed", "reason": "PodFailed",
                                           "message": "pod dms-x failed"}})
    assert a.poll(ref) == ExecStatus.FAILED


def test_poll_vcjob_pod_list_failure_is_poll_failed_not_a_verdict():
    # 파드를 못 읽으면 「시한 아님(FAILED)」으로 접지 않는다(모름 ≠ 실패) -- poll_failed 로 던져 stepper 의 step_error 뒤 다음
    # 틱이 다시 읽는다(같은 poll 의 vcjob GET 실패와 같은 길).
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_volcano_spec("execution"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Job", name, dict(_ABORTED))
    k8s.add_task_pod(name, f"{name}-worker-0", "Failed", "DeadlineExceeded")
    k8s.fail_list = RuntimeError("apiserver unavailable")
    with pytest.raises(ExecutionError) as ei:
        a.poll(ref)
    assert ei.value.reason_code == "poll_failed"
    k8s.fail_list = None
    assert a.poll(ref) == ExecStatus.TIMED_OUT


@pytest.mark.parametrize("vc_phase, expected", [("Running", ExecStatus.RUNNING), ("Aborting", ExecStatus.RUNNING),
                                                ("Completed", ExecStatus.SUCCEEDED), ("Pending", ExecStatus.PENDING)])
def test_poll_vcjob_not_ended_does_not_read_pods(vc_phase, expected):
    # 끝나지 않은(또는 성공한) vcjob 은 파드를 보지 않는다 -- Aborting 중 DeadlineExceeded 파드가 있어도 아직 RUNNING.
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_volcano_spec("execution"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Job", name, {"state": {"phase": vc_phase}})
    k8s.add_task_pod(name, f"{name}-worker-0", "Failed", "DeadlineExceeded")
    assert a.poll(ref) == expected
    assert k8s.pod_lists == []


def test_poll_vcjob_name_outside_label_syntax_is_not_used_as_a_selector():
    # DB 신뢰 경계: 변조된 ref 의 이름(쉼표 -- 셀렉터를 넓힌다)으로 셀렉터를 만들지 않는다. 시한 증거 없음 = FAILED.
    k8s = _FakeK8s()
    a = _adapter(k8s)
    bad = "dms-x,volcano.sh/task-spec"
    k8s.set_status("Job", bad, dict(_ABORTED))
    assert a.poll(f"vcjob/{bad}") == ExecStatus.FAILED
    assert k8s.pod_lists == []


def test_real_client_pod_briefs_carry_the_pod_status_reason():
    # 실 KubernetesClient.list_pod_briefs 가 파드 status.reason 을 "reason" 으로 싣는다(위 판정이 읽는 키). 실 V1PodStatus 는
    # 사유가 없으면 None 이다 -- 속성이 아예 없는 객체도 같은 None.
    from types import SimpleNamespace

    from dms.execution_volcano import KubernetesClient

    def pod(name, phase, **status):
        return SimpleNamespace(metadata=SimpleNamespace(name=name, deletion_timestamp=None),
                               spec=SimpleNamespace(node_name="w1", containers=[]),
                               status=SimpleNamespace(phase=phase, container_statuses=[], **status))

    class Core:
        def list_namespaced_pod(self, namespace, label_selector, _request_timeout):
            return SimpleNamespace(items=[pod("a", "Failed", reason="DeadlineExceeded"), pod("b", "Failed", reason=None),
                                          pod("c", "Running")])

    client = KubernetesClient("dms")
    client._core = Core()
    briefs = client.list_pod_briefs("dms", "volcano.sh/job-name=x")
    assert [(b["name"], b["phase"], b["reason"]) for b in briefs] == [
        ("a", "Failed", "DeadlineExceeded"), ("b", "Failed", None), ("c", "Running", None)]


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
                          "destination_storage": "cephfs-secondary", "source_root": "/", "destination_root": "/"},
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


def test_preflight_timed_out_status_ends_timed_out_via_stub(db):
    # stepper 층만: 사전 점검 poll 이 TIMED_OUT 이면 TimedOut preflight_timed_out(스텁으로 단계 결과만 준다). 복합 preflight
    # (nsync src+dst)도 어댑터가 「하나라도 TIMED_OUT」으로 결합해(test_poll_composite_*) 같은 분기를 탄다.
    repos = Repositories(db)
    _, jid = _dsync_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()  # Pending → Preflight
    adapter.script(f"stub-preflight-{jid}", [ExecStatus.TIMED_OUT])
    assert stepper.run_once()[jid] == "TimedOut"
    job = repos.data_jobs.get_job(jid)
    assert (job["state"], job["reason_code"]) == ("TimedOut", "preflight_timed_out")


# --- Layer 4b: stepper + 실 어댑터(VolcanoExecutionAdapter) -- 라이브 모양으로 끝 상태까지 -------------------------
# 2026-11-04 결함 #4·#5: 스텁 어댑터는 TIMED_OUT 을 직접 돌려줘 「어댑터가 라이브 모양에서 TIMED_OUT 을 내는가」를 못 본다.
# 여기선 apiserver 흉내(_FakeK8s)에 라이브 모양(vcjob {phase: Aborted} + task 파드 DeadlineExceeded, 점검 파드 DeadlineExceeded)을
# 두고 stepper 가 TimedOut 과 단계별 사유에 닿는지 본다.

def _rm_job(repos):
    _seed_storage(repos, "s1")
    rid = repos.requests.create(operation="rm", requester_id="alice", actor="alice", resource_key="k",
                                payload={"run_as_root": True, "storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation="rm", priority="mid", storage_name="s1", target="a", options={}, tool="drm",
        worker_pool={"tool": "drm", "identity": {"uid": 0, "gid": 0, "username": "alice", "groups": [],
                                                  "privileged": True},
                     "candidates": {"primary": ["n1"]}, "process_count": 8, "queue": "dms-data",
                     "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _tiny_rm_policy(repos):
    # 라이브 PO-03·PO-04 의 조건: rm 정책 미리보기·실행 타임아웃 1초.
    repos.control.upsert_policy("rm", max_nodes=4, procs_per_node=8, queue="dms-data",
        default_priority="mid", max_priority="high",
        preview_timeout_seconds=1, execution_timeout_seconds=1, enabled=True, actor="admin")


def _live(repos, k8s):
    adapter = VolcanoExecutionAdapter(
        k8s, job_image="reg/dms-job:d1", namespace="dms", storages_lookup=lambda n: repos.storages.get(n),
        read_text=lambda p: json.dumps({"returncode": 0, "files": 3, "bytes": None}),
        artifact_base="file:///art")
    return lambda: _stepper(repos, adapter).run_once()


def _made(k8s, kind, marker):
    [m] = [m for m in k8s.created if m["kind"] == kind and marker in m["metadata"]["name"]]
    return m


def _diag_phase(db, jid):
    raw = db.query_one("SELECT diag_logs FROM data_jobs WHERE job_id = :j", {"j": jid})["diag_logs"]
    return (json.loads(raw) if isinstance(raw, str) else raw)["phase"]


_DEADLINE_POD = {"phase": "Failed", "reason": "DeadlineExceeded",
                 "message": "Pod was active on the node longer than the specified deadline"}


def _to_preview_running(k8s, jid, tick):
    assert tick()[jid] == "Preflight"
    k8s.set_status("Pod", _made(k8s, "Pod", "-preflight-")["metadata"]["name"], {"phase": "Succeeded"})
    assert tick()[jid] == "PreviewRunning"
    return _made(k8s, "Job", "-preview-")["metadata"]["name"]


def _to_confirmed(db, k8s, jid, tick):
    preview = _to_preview_running(k8s, jid, tick)
    k8s.set_status("Job", preview, {"state": {"phase": "Completed"}})
    assert tick()[jid] == "ConfirmPending"
    db.execute("UPDATE data_jobs SET state = 'Executing' WHERE job_id = :j", {"j": jid})   # 컨펌
    assert tick()[jid] == "Executing"                                                       # exec_preflight 제출
    return _made(k8s, "Pod", "-exec-preflight-")["metadata"]["name"]


def _to_execution(db, k8s, jid, tick):
    k8s.set_status("Pod", _to_confirmed(db, k8s, jid, tick), {"phase": "Succeeded"})
    assert tick()[jid] == "Executing"                                                       # 실행 vcjob 제출
    return _made(k8s, "Job", "-execution-")["metadata"]["name"]


def test_live_tiny_preview_policy_no_longer_kills_the_preflight_pod(db):
    # PO-03·03b: 미리보기 타임아웃 1초가 점검 파드의 시한이 돼 Rejected preflight_failed 였다. 이제 점검 파드는 고정 시한,
    # 미리보기 vcjob 의 task 만 정책값이다.
    repos = Repositories(db)
    _tiny_rm_policy(repos)
    _, jid = _rm_job(repos)
    k8s = _FakeK8s()
    preview = _to_preview_running(k8s, jid, _live(repos, k8s))
    assert _made(k8s, "Pod", "-preflight-")["spec"]["activeDeadlineSeconds"] == PREFLIGHT_DEADLINE_SECONDS
    assert {t["template"]["spec"]["activeDeadlineSeconds"]
            for t in _made(k8s, "Job", preview)["spec"]["tasks"]} == {1}


def test_live_preview_deadline_ends_timed_out_preview_timed_out(db):
    repos = Repositories(db)
    _tiny_rm_policy(repos)
    rid, jid = _rm_job(repos)
    k8s = _FakeK8s()
    tick = _live(repos, k8s)
    preview = _to_preview_running(k8s, jid, tick)
    k8s.set_status("Job", preview, dict(_ABORTED))
    k8s.add_task_pod(preview, f"{preview}-worker-0", "Failed", "DeadlineExceeded")
    assert tick()[jid] == "TimedOut"
    job = repos.data_jobs.get_job(jid)
    assert (job["state"], job["reason_code"]) == ("TimedOut", "preview_timed_out")
    assert repos.requests.get(rid)["state"] == "Failed"
    assert _diag_phase(db, jid) == "preview"
    assert not [m for m in k8s.created if "-execution-" in m["metadata"]["name"]]


def test_live_execution_deadline_ends_timed_out(db):
    # PO-04 재현: rm ConfirmPending 에서 실행 타임아웃 1초 → 컨펌 → 워커 DeadlineExceeded → vcjob Aborted(사유 없음). 예전엔
    # Failed execution_failed. 실행 시간 초과의 사유 코드는 종전 그대로 execution_failed 다(상태가 TimedOut).
    repos = Repositories(db)
    _tiny_rm_policy(repos)
    rid, jid = _rm_job(repos)
    k8s = _FakeK8s()
    tick = _live(repos, k8s)
    execution = _to_execution(db, k8s, jid, tick)
    assert _made(k8s, "Pod", "-exec-preflight-")["spec"]["activeDeadlineSeconds"] == PREFLIGHT_DEADLINE_SECONDS
    assert {t["template"]["spec"]["activeDeadlineSeconds"]
            for t in _made(k8s, "Job", execution)["spec"]["tasks"]} == {1}
    k8s.set_status("Job", execution, dict(_ABORTED))
    k8s.add_task_pod(execution, f"{execution}-worker-0", "Failed", "DeadlineExceeded")
    assert tick()[jid] == "TimedOut"
    job = repos.data_jobs.get_job(jid)
    assert (job["state"], job["reason_code"]) == ("TimedOut", "execution_failed")
    assert repos.requests.get(rid)["state"] == "Failed"
    assert _diag_phase(db, jid) == "execution"


def test_live_execution_aborted_by_an_ordinary_pod_failure_is_still_failed(db):
    repos = Repositories(db)
    _, jid = _rm_job(repos)
    k8s = _FakeK8s()
    tick = _live(repos, k8s)
    execution = _to_execution(db, k8s, jid, tick)
    k8s.set_status("Job", execution, dict(_ABORTED))
    k8s.add_task_pod(execution, f"{execution}-worker-0", "Failed")
    assert tick()[jid] == "Failed"
    job = repos.data_jobs.get_job(jid)
    assert (job["state"], job["reason_code"]) == ("Failed", "execution_failed")


def test_live_pod_list_failure_keeps_the_job_and_the_next_tick_classifies(db):
    # 파드를 못 읽은 틱은 판정하지 않는다(모름 ≠ 실패) -- step_error 로 남고 잡은 Executing 그대로, 다음 틱이 TimedOut.
    repos = Repositories(db)
    rid, jid = _rm_job(repos)
    k8s = _FakeK8s()
    tick = _live(repos, k8s)
    execution = _to_execution(db, k8s, jid, tick)
    k8s.set_status("Job", execution, dict(_ABORTED))
    k8s.add_task_pod(execution, f"{execution}-worker-0", "Failed", "DeadlineExceeded")
    k8s.fail_list = RuntimeError("apiserver unavailable")
    assert tick()[jid] == "error:ExecutionError"
    job = repos.data_jobs.get_job(jid)
    assert (job["state"], job["reason_code"]) == ("Executing", None)
    assert [e["event_type"] for e in repos.observability.events_for_request(rid)].count("step_error") == 1
    k8s.fail_list = None
    assert tick()[jid] == "TimedOut"
    assert repos.data_jobs.get_job(jid)["state"] == "TimedOut"


def test_live_preflight_deadline_ends_timed_out_preflight_timed_out(db):
    # 점검 파드가 고정 시한을 넘겼다 -- 예전엔 TIMED_OUT 분기가 없어 Rejected preflight_failed(「로그에서 사유를 보라」)였다.
    repos = Repositories(db)
    rid, jid = _rm_job(repos)
    k8s = _FakeK8s()
    tick = _live(repos, k8s)
    assert tick()[jid] == "Preflight"
    k8s.set_status("Pod", _made(k8s, "Pod", "-preflight-")["metadata"]["name"], dict(_DEADLINE_POD))
    assert tick()[jid] == "TimedOut"
    job = repos.data_jobs.get_job(jid)
    assert (job["state"], job["reason_code"]) == ("TimedOut", "preflight_timed_out")
    assert repos.requests.get(rid)["state"] == "Failed"
    assert _diag_phase(db, jid) == "preflight"
    assert not [m for m in k8s.created if m["kind"] == "Job"]                  # 미리보기 vcjob 은 없다


def test_live_exec_preflight_deadline_ends_timed_out_execution_recheck_timed_out(db):
    repos = Repositories(db)
    rid, jid = _rm_job(repos)
    k8s = _FakeK8s()
    tick = _live(repos, k8s)
    k8s.set_status("Pod", _to_confirmed(db, k8s, jid, tick), dict(_DEADLINE_POD))
    assert tick()[jid] == "TimedOut"
    job = repos.data_jobs.get_job(jid)
    assert (job["state"], job["reason_code"]) == ("TimedOut", "execution_recheck_timed_out")
    assert repos.requests.get(rid)["state"] == "Failed"
    assert _diag_phase(db, jid) == "exec_preflight"
    assert not [m for m in k8s.created if "-execution-" in m["metadata"]["name"]]   # 실행 vcjob 은 없다


def test_live_preflight_ordinary_failure_is_still_rejected_preflight_failed(db):
    # 회귀 방지: 시한이 아닌 점검 실패(마커 없음)는 종전대로 Rejected preflight_failed.
    repos = Repositories(db)
    _, jid = _rm_job(repos)
    k8s = _FakeK8s()
    tick = _live(repos, k8s)
    assert tick()[jid] == "Preflight"
    k8s.set_status("Pod", _made(k8s, "Pod", "-preflight-")["metadata"]["name"], {"phase": "Failed", "reason": "Error"})
    assert tick()[jid] == "Rejected"
    job = repos.data_jobs.get_job(jid)
    assert (job["state"], job["reason_code"]) == ("Rejected", "preflight_failed")
