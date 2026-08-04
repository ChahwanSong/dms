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


def _spec(phase="execution", op="scan", tool="dscan", cand=None, paths=None, artifact_base=None):
    return JobSpec(job_id="job123456789abc", phase=phase, operation=op, tool=tool,
                   dryrun=(phase == "preview"),
                   identity={"uid": 10001, "gid": 10000, "username": "alice"},
                   paths=paths or {"target": "/cephfs/dms/a", "storage": "cephfs-dms"},
                   options={}, candidates=cand or {"primary": ["dms-w1"]},
                   process_count=8, queue="dms-data", priority_class="dms-mid",
                   artifact_base=artifact_base or "file:///cephfs/dms/artifacts")


def _adapter(k8s, summaries=None):
    summaries = summaries or {}
    return VolcanoExecutionAdapter(
        k8s, job_image="reg/img:1", namespace="dms",
        storages_lookup=lambda n: {"mount_path": "/cephfs", "managed_root": "/cephfs/dms"},
        read_text=lambda path: summaries.get(path),
        artifact_base="file:///cephfs/dms/artifacts")


def test_submit_preflight_creates_pod():
    k8s = _FakeK8s()
    ref = _adapter(k8s).submit(_spec(phase="preflight"))
    assert ref.startswith("pod/")
    assert k8s.created[0]["kind"] == "Pod"


def test_submit_exec_preflight_creates_pod_not_vcjob():
    # the post-confirm re-validation phase must ALSO route to a preflight Pod,
    # not a Volcano Job (a vcjob named with "exec_preflight" underscores is
    # rejected by k8s -> submit 422).
    k8s = _FakeK8s()
    ref = _adapter(k8s).submit(_spec(phase="exec_preflight"))
    assert ref.startswith("pod/")
    assert k8s.created[0]["kind"] == "Pod"
    assert "_" not in k8s.created[0]["metadata"]["name"]


def _nsync_adapter(k8s):
    def storages_lookup(n):
        return {"cephfs-third": {"mount_path": "/cephfs-third", "managed_root": "/cephfs-third"},
                "cephfs-secondary": {"mount_path": "/cephfs-secondary",
                                     "managed_root": "/cephfs-secondary"}}.get(
            n, {"mount_path": "/cephfs", "managed_root": "/cephfs/dms"})
    return VolcanoExecutionAdapter(
        k8s, job_image="reg/img:1", namespace="dms", storages_lookup=storages_lookup,
        read_text=lambda p: None, artifact_base="file:///cephfs/dms/artifacts")


def _nsync_preflight_spec(phase="preflight"):
    return _spec(phase=phase, op="sync", tool="nsync",
                 cand={"source": ["dms-w1", "dms-w2"], "destination": ["dms-w4"]},
                 paths={"source": "/cephfs-third/a", "source_storage": "cephfs-third",
                        "destination": "/cephfs-secondary/b",
                        "destination_storage": "cephfs-secondary"})


def test_submit_nsync_preflight_creates_source_and_dest_pods():
    # nsync preflight는 소스 노드(소스 읽기)와 목적지 노드(목적지 쓰기)에 각각 Pod를
    # 띄운다 -- 한 노드에서는 반대편이 마운트되지 않아 검증 불가.
    k8s = _FakeK8s()
    ref = _nsync_adapter(k8s).submit(_nsync_preflight_spec())
    assert ref.startswith("pods/") and "," in ref
    assert len(k8s.created) == 2
    pods = {p["spec"]["nodeSelector"]["kubernetes.io/hostname"]: p for p in k8s.created}
    assert set(pods) == {"dms-w1", "dms-w4"}          # source[0] + destination[0]
    src, dst = pods["dms-w1"], pods["dms-w4"]
    # role별 볼륨: 소스 Pod엔 목적지 스토리지가 없어야(그 노드에 미마운트), 반대도
    src_mp = {v["hostPath"]["path"] for v in src["spec"]["volumes"]}
    dst_mp = {v["hostPath"]["path"] for v in dst["spec"]["volumes"]}
    assert "/cephfs-third" in src_mp and "/cephfs-secondary" not in src_mp
    assert "/cephfs-secondary" in dst_mp and "/cephfs-third" not in dst_mp
    # 소스 Pod는 소스 읽기만, 목적지 Pod는 목적지 쓰기만 검사
    assert "source_not_readable" in src["spec"]["containers"][0]["command"][2]
    assert "destination_parent_not_writable" in dst["spec"]["containers"][0]["command"][2]
    assert "_" not in src["metadata"]["name"] and "_" not in dst["metadata"]["name"]


def test_poll_nsync_preflight_combines_fail_closed():
    k8s = _FakeK8s()
    a = _nsync_adapter(k8s)
    ref = a.submit(_nsync_preflight_spec())
    n1, n2 = ref.split("/", 1)[1].split(",")
    k8s.set_status("Pod", n1, {"phase": "Running"})
    k8s.set_status("Pod", n2, {"phase": "Succeeded"})
    assert a.poll(ref) == ExecStatus.RUNNING          # 하나라도 미완료면 RUNNING
    k8s.set_status("Pod", n1, {"phase": "Succeeded"})
    assert a.poll(ref) == ExecStatus.SUCCEEDED         # 전부 성공이라야 SUCCEEDED
    k8s.set_status("Pod", n2, {"phase": "Failed"})
    assert a.poll(ref) == ExecStatus.FAILED            # 하나라도 실패면 FAILED(fail-closed)


def test_terminate_nsync_preflight_deletes_both():
    k8s = _FakeK8s()
    a = _nsync_adapter(k8s)
    ref = a.submit(_nsync_preflight_spec())
    a.terminate(ref)
    assert len({name for _, name in k8s.deleted}) == 2


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


def test_read_summary_reconstructs_from_labels():
    """컨트롤러 재시작 흉내: 새 어댑터 인스턴스(_summary_paths 빈 상태)가 같은 k8s의
    오브젝트 라벨(dms.io/job-id, dms.io/phase)에서 summary 경로를 재구성한다."""
    k8s = _FakeK8s()
    summaries = {
        "/cephfs/dms/artifacts/job123456789abc/execution/summary.json": '{"files": 3}'}
    a1 = _adapter(k8s, summaries=summaries)
    ref = a1.submit(_spec(phase="execution"))
    a2 = VolcanoExecutionAdapter(
        k8s, job_image="reg/img:1", namespace="dms",
        storages_lookup=lambda n: {"mount_path": "/cephfs", "managed_root": "/cephfs/dms"},
        read_text=lambda path: summaries.get(path),
        artifact_base="file:///cephfs/dms/artifacts")
    assert ref not in a2._summary_paths
    assert a2.read_summary(ref) == {"files": 3}


def test_read_summary_reconstruction_missing_object_is_none():
    """오브젝트가 사라졌으면(또는 라벨 없으면) 재구성 불가 → None."""
    k8s = _FakeK8s()
    a = VolcanoExecutionAdapter(
        k8s, job_image="reg/img:1", namespace="dms",
        storages_lookup=lambda n: {"mount_path": "/cephfs", "managed_root": "/cephfs/dms"},
        read_text=lambda path: None,
        artifact_base="file:///cephfs/dms/artifacts")
    assert a.read_summary("vcjob/nonexistent") is None


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


def test_poll_vcjob_inqueue_maps_to_pending():
    """Volcano Inqueue 상태는 PENDING으로 매핑."""
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_spec(phase="execution"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Job", name, {"state": {"phase": "Inqueue"}})
    assert a.poll(ref) == ExecStatus.PENDING


def test_volumes_dedup_nested_artifact_under_storage():
    """artifact_base가 storage mount 하위면 중첩 생략."""
    k8s = _FakeK8s()
    a = _adapter(k8s)  # mount_path=/cephfs, artifact=/cephfs/dms/artifacts (nested)
    ref = a.submit(_spec(phase="execution"))
    volumes = k8s.created[0]["spec"]["tasks"][0]["template"]["spec"]["volumes"]
    paths = [v["hostPath"]["path"] for v in volumes]
    assert paths == ["/cephfs"], f"Expected ['/cephfs'], got {paths}"


def test_volumes_include_artifact_base_when_independent():
    """artifact_base가 독립 경로면 별도 마운트."""
    k8s = _FakeK8s()
    def storages_lookup(n):
        return {"mount_path": "/data", "managed_root": "/data/dms"}
    a = VolcanoExecutionAdapter(
        k8s, job_image="reg/img:1", namespace="dms",
        storages_lookup=storages_lookup,
        read_text=lambda path: None,
        artifact_base="file:///cephfs/dms/artifacts")
    spec = _spec(phase="execution", artifact_base="file:///cephfs/dms/artifacts")
    ref = a.submit(spec)
    volumes = k8s.created[0]["spec"]["tasks"][0]["template"]["spec"]["volumes"]
    paths = {v["hostPath"]["path"] for v in volumes}
    assert "/data" in paths, f"/data not in {paths}"
    assert "/cephfs/dms/artifacts" in paths, f"/cephfs/dms/artifacts not in {paths}"


def test_volumes_sync_two_storages():
    """sync 작업은 source+destination 스토리지 모두 마운트."""
    k8s = _FakeK8s()
    def storages_lookup(n):
        if n == "storage-third":
            return {"mount_path": "/cephfs-third", "managed_root": "/cephfs-third/dms"}
        if n == "storage-secondary":
            return {"mount_path": "/cephfs-secondary", "managed_root": "/cephfs-secondary/dms"}
        return {"mount_path": "/cephfs", "managed_root": "/cephfs/dms"}
    a = VolcanoExecutionAdapter(
        k8s, job_image="reg/img:1", namespace="dms",
        storages_lookup=storages_lookup,
        read_text=lambda path: None,
        artifact_base="file:///cephfs/dms/artifacts")
    spec = _spec(phase="execution", op="sync", tool="nsync",
                 cand={"source": ["dms-src1"], "destination": ["dms-dst1"]},
                 paths={"source": "/cephfs-third/a", "source_storage": "storage-third",
                        "destination": "/cephfs-secondary/b", "destination_storage": "storage-secondary"})
    ref = a.submit(spec)
    volumes = k8s.created[0]["spec"]["tasks"][0]["template"]["spec"]["volumes"]
    paths = {v["hostPath"]["path"] for v in volumes}
    assert "/cephfs-third" in paths, f"/cephfs-third not in {paths}"
    assert "/cephfs-secondary" in paths, f"/cephfs-secondary not in {paths}"
