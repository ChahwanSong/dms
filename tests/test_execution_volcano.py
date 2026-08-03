import pytest
from dms.execution import ExecStatus, ExecutionError, JobSpec
from dms.execution_volcano import VolcanoExecutionAdapter


class _FakeK8s:
    def __init__(self):
        self.created = []
        self.deleted = []
        self._objs = {}      # (kind, name) -> obj
        self.fail_create = False
        self.fail_delete = False

    def create(self, manifest):
        if self.fail_create:
            raise RuntimeError("boom")
        self.created.append(manifest)
        key = (manifest["kind"], manifest["metadata"]["name"])
        self._objs[key] = manifest

    def set_status(self, kind, name, status):
        self._objs.setdefault((kind, name), {"kind": kind})["status"] = status

    def get(self, kind, name, namespace):
        return self._objs.get((kind, name))

    def delete(self, kind, name, namespace):
        if self.fail_delete:
            raise RuntimeError("boom")
        self.deleted.append((kind, name))
        self._objs.pop((kind, name), None)

    def read_pod_log(self, name, namespace):
        return ""


def _spec(phase="execution", op="scan", tool="dscan", cand=None, paths=None):
    return JobSpec(job_id="job123456789abc", phase=phase, operation=op, tool=tool,
                   dryrun=(phase == "preview"),
                   identity={"uid": 10001, "gid": 10000, "username": "alice"},
                   paths=paths or {"target": "/cephfs/dms/a", "storage": "cephfs-dms"},
                   options={}, candidates=cand or {"primary": ["dms-w1"]},
                   process_count=8, queue="dms-data", priority_class="dms-mid",
                   artifact_base="file:///cephfs/dms/artifacts")


def _adapter(k8s, summaries=None):
    summaries = summaries or {}
    return VolcanoExecutionAdapter(
        k8s, job_image="reg/img:1", namespace="dms",
        storages_lookup=lambda n: {"mount_path": "/cephfs", "managed_root": "/cephfs/dms"},
        read_text=lambda path: summaries.get(path))


def test_submit_preflight_creates_pod():
    k8s = _FakeK8s()
    ref = _adapter(k8s).submit(_spec(phase="preflight"))
    assert ref.startswith("pod/")
    assert k8s.created[0]["kind"] == "Pod"


def test_submit_execution_creates_vcjob():
    k8s = _FakeK8s()
    ref = _adapter(k8s).submit(_spec(phase="execution"))
    assert ref.startswith("vcjob/")
    assert k8s.created[0]["kind"] == "Job"


def test_poll_pod_phase_mapping():
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_spec(phase="preflight"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Pod", name, {"phase": "Running"})
    assert a.poll(ref) == ExecStatus.RUNNING
    k8s.set_status("Pod", name, {"phase": "Succeeded"})
    assert a.poll(ref) == ExecStatus.SUCCEEDED


def test_poll_vcjob_state_mapping():
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_spec(phase="execution"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Job", name, {"state": {"phase": "Completed"}})
    assert a.poll(ref) == ExecStatus.SUCCEEDED
    k8s.set_status("Job", name, {"state": {"phase": "Failed"}})
    assert a.poll(ref) == ExecStatus.FAILED


def test_poll_missing_is_failed():
    a = _adapter(_FakeK8s())
    assert a.poll("vcjob/nonexistent") == ExecStatus.FAILED


def test_read_summary_reads_artifact():
    k8s = _FakeK8s()
    spec = _spec(phase="execution")
    a = _adapter(k8s, summaries={
        "/cephfs/dms/artifacts/job123456789abc/execution/summary.json": '{"files": 3}'})
    ref = a.submit(spec)
    assert a.read_summary(ref) == {"files": 3}


def test_read_summary_missing_is_none():
    a = _adapter(_FakeK8s())
    ref = a.submit(_spec())
    assert a.read_summary(ref) is None


def test_terminate_idempotent_and_error():
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_spec(phase="execution"))
    a.terminate(ref)
    a.terminate(ref)  # 이미 삭제 — 멱등
    assert len(k8s.deleted) >= 1
    k8s.fail_delete = True
    ref2 = _adapter(k8s).submit(_spec(phase="execution", op="rm", tool="drm",
                                      paths={"target": "/cephfs/x", "storage": "cephfs-dms"}))
    with pytest.raises(ExecutionError):
        a.terminate(ref2)


def test_submit_failure_raises():
    k8s = _FakeK8s(); k8s.fail_create = True
    with pytest.raises(ExecutionError):
        _adapter(k8s).submit(_spec())
