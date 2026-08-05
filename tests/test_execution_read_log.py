import pytest
from dms.execution import ExecutionError, StubExecutionAdapter
from dms.execution_volcano import VolcanoExecutionAdapter


class _FakeK8s:
    def __init__(self):
        self._logs = {}
        self._fail_pods = set()

    def read_pod_log(self, name, namespace):
        if name in self._fail_pods:
            raise RuntimeError("pod not found")
        return self._logs.get(name, "")

    def set_log(self, name, text):
        self._logs[name] = text

    def fail_log(self, name):
        self._fail_pods.add(name)


def _adapter(k8s):
    return VolcanoExecutionAdapter(
        k8s, job_image="reg/img:1", namespace="dms",
        storages_lookup=lambda n: {"mount_path": "/cephfs", "managed_root": "/cephfs/dms"},
        read_text=lambda path: None,
        artifact_base="file:///cephfs/dms/artifacts")


def test_read_log_single_pod_ref():
    k8s = _FakeK8s()
    k8s.set_log("p1", "hello log")
    a = _adapter(k8s)
    assert a.read_log("pod/p1") == [("p1", "hello log")]


def test_read_log_dual_pods_ref():
    k8s = _FakeK8s()
    k8s.set_log("p1", "log1")
    k8s.set_log("p2", "log2")
    a = _adapter(k8s)
    assert a.read_log("pods/p1,p2") == [("p1", "log1"), ("p2", "log2")]


def test_read_log_missing_pod_yields_none():
    k8s = _FakeK8s()
    k8s.set_log("p1", "log1")
    k8s.fail_log("p2")
    a = _adapter(k8s)
    assert a.read_log("pods/p1,p2") == [("p1", "log1"), ("p2", None)]


def test_read_log_rejects_vcjob_ref():
    a = _adapter(_FakeK8s())
    with pytest.raises(ExecutionError) as exc_info:
        a.read_log("vcjob/j1")
    assert exc_info.value.reason_code == "log_not_available"


def test_stub_adapter_read_log():
    a = StubExecutionAdapter()
    assert a.read_log("pod/p1") == [("pod/p1", "")]
    a.set_log("pod/p1", [("p1", "custom log")])
    assert a.read_log("pod/p1") == [("p1", "custom log")]
