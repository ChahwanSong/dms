import pytest
from dms.build_runner import BuildRunner, StubBuildRunner
from dms.execution import ExecStatus, ExecutionError


class _FakeK8s:
    def __init__(self):
        self.created = []
        self.deleted = []
        self._objs = {}
        self.fail_create = False
        self.log = "hello"

    def create(self, manifest):
        if self.fail_create:
            raise RuntimeError("boom")
        self.created.append(manifest)
        self._objs[(manifest["kind"], manifest["metadata"]["name"])] = manifest

    def set_status(self, name, status):
        self._objs.setdefault(("Pod", name), {"kind": "Pod"})["status"] = status

    def get(self, kind, name, namespace):
        return self._objs.get((kind, name))

    def delete(self, kind, name, namespace):
        self.deleted.append((kind, name))
        self._objs.pop((kind, name), None)

    def read_pod_log(self, name, namespace):
        return self.log


BUILD = {"build_id": "0123456789abcdef0123456789abcdef", "repo_url": "u",
         "git_ref": "main", "images": ["dms"], "node_name": "dms-w1"}


def _runner(k8s):
    return BuildRunner(k8s, namespace="dms", registry="pkg-01:5000",
                       builder_image="quay.io/buildah/stable:latest")


def test_submit_creates_pod_and_returns_buildpod_ref():
    k8s = _FakeK8s()
    ref = _runner(k8s).submit(BUILD)
    assert ref == "buildpod/dms-build-0123456789ab"
    assert k8s.created[0]["kind"] == "Pod"


def test_submit_failure_becomes_execution_error():
    k8s = _FakeK8s()
    k8s.fail_create = True
    with pytest.raises(ExecutionError) as e:
        _runner(k8s).submit(BUILD)
    assert e.value.reason_code == "submit_failed"


@pytest.mark.parametrize("phase,expected", [
    ("Pending", ExecStatus.PENDING), ("Running", ExecStatus.RUNNING),
    ("Succeeded", ExecStatus.SUCCEEDED), ("Failed", ExecStatus.FAILED),
    ("Unknown", ExecStatus.FAILED)])
def test_poll_maps_pod_phase(phase, expected):
    k8s = _FakeK8s()
    ref = _runner(k8s).submit(BUILD)
    k8s.set_status("dms-build-0123456789ab", {"phase": phase})
    assert _runner(k8s).poll(ref) == expected


def test_poll_treats_missing_pod_as_failed():
    # 어댑터 규약과 같다: 사라진 객체는 '모름'이 아니라 실패다
    k8s = _FakeK8s()
    assert _runner(k8s).poll("buildpod/gone") == ExecStatus.FAILED


def test_read_log_returns_text_and_none_when_unavailable():
    k8s = _FakeK8s()
    ref = _runner(k8s).submit(BUILD)
    assert _runner(k8s).read_log(ref) == "hello"

    class _Boom(_FakeK8s):
        def read_pod_log(self, name, namespace):
            raise RuntimeError("gone")
    assert _runner(_Boom()).read_log(ref) is None


def test_terminate_is_idempotent():
    k8s = _FakeK8s()
    ref = _runner(k8s).submit(BUILD)
    r = _runner(k8s)
    r.terminate(ref)
    r.terminate(ref)
    assert k8s.deleted[0] == ("Pod", "dms-build-0123456789ab")


def test_non_buildpod_ref_is_rejected():
    with pytest.raises(ExecutionError) as e:
        _runner(_FakeK8s()).poll("vcjob/whatever")
    assert e.value.reason_code == "invalid_build_ref"


def test_stub_runner_runs_without_a_cluster():
    stub = StubBuildRunner()
    ref = stub.submit(BUILD)
    assert ref.startswith("buildpod/")
    assert stub.poll(ref) == ExecStatus.SUCCEEDED
    assert stub.read_log(ref) is not None
    stub.terminate(ref)
