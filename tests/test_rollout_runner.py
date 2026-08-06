import pytest
from dms.execution import ExecutionError
from dms.execution_volcano import KubernetesClient
from dms.rollout_runner import RolloutRunner, StubRolloutRunner, image_patch_body


def test_patch_body_is_strategic_merge_shape():
    # 컨테이너 name이 patchMergeKey다 -- 이 모양이 아니면 JSON merge처럼
    # containers 배열 전체가 교체돼 env/volumeMounts가 날아간다(설계 §4)
    assert image_patch_body("api", "pkg-01:5000/dms:d23") == {
        "spec": {"template": {"spec": {"containers": [
            {"name": "api", "image": "pkg-01:5000/dms:d23"}]}}}}


class _FakeWorkloads:
    """WorkloadClient 페어 -- get_workload는 계약대로 '정규화된' dict를 돌려준다."""
    def __init__(self):
        self.patched = []
        self.objects = {}
        self.fail_patch = None
        self.fail_get = None

    def patch_workload(self, kind, name, namespace, body):
        if self.fail_patch:
            raise self.fail_patch
        self.patched.append((kind, name, namespace, body))

    def get_workload(self, kind, name, namespace):
        if self.fail_get:
            raise self.fail_get
        return self.objects.get((kind, name))

    def list_pod_briefs(self, namespace, label_selector):
        return [{"name": "p1", "node": "dms-w1", "images": {"api": "i1"},
                 "phase": "Running", "waiting_reason": None}]


def test_runner_patches_via_client():
    k8s = _FakeWorkloads()
    RolloutRunner(k8s, namespace="dms").patch_image(
        kind="Deployment", name="dms-api", container="api", image="img:t")
    kind, name, ns, body = k8s.patched[0]
    assert (kind, name, ns) == ("Deployment", "dms-api", "dms")
    assert body == image_patch_body("api", "img:t")


def test_patch_failure_becomes_execution_error():
    k8s = _FakeWorkloads()
    k8s.fail_patch = RuntimeError("boom")
    with pytest.raises(ExecutionError) as e:
        RolloutRunner(k8s, namespace="dms").patch_image(
            kind="Deployment", name="dms-api", container="api", image="i")
    assert e.value.reason_code == "patch_failed"


def test_observe_passes_through_normalized_dict_and_none():
    k8s = _FakeWorkloads()
    k8s.objects[("DaemonSet", "dms-agent")] = {"kind": "DaemonSet", "generation": 1}
    r = RolloutRunner(k8s, namespace="dms")
    assert r.observe(kind="DaemonSet", name="dms-agent")["generation"] == 1
    assert r.observe(kind="DaemonSet", name="gone") is None


def test_observe_failure_becomes_execution_error():
    k8s = _FakeWorkloads()
    k8s.fail_get = RuntimeError("apiserver down")
    with pytest.raises(ExecutionError) as e:
        RolloutRunner(k8s, namespace="dms").observe(kind="Deployment", name="dms-api")
    assert e.value.reason_code == "observe_failed"


def test_pod_briefs_is_best_effort():
    class _Boom(_FakeWorkloads):
        def list_pod_briefs(self, namespace, label_selector):
            raise RuntimeError("nope")
    r = RolloutRunner(_Boom(), namespace="dms")
    assert r.pod_briefs(selector="app.kubernetes.io/name=dms-api") == []
    ok = RolloutRunner(_FakeWorkloads(), namespace="dms")
    assert ok.pod_briefs(selector="x")[0]["images"] == {"api": "i1"}


def test_stub_runner_converges_on_patched_image():
    stub = StubRolloutRunner()
    stub.patch_image(kind="Deployment", name="dms-api", container="api", image="i2")
    obs = stub.observe(kind="Deployment", name="dms-api")
    assert obs["images"] == {"api": "i2"}
    assert obs["observed_generation"] >= obs["generation"]
    assert stub.observe(kind="Deployment", name="never-patched") is None
    assert stub.patched == [("Deployment", "dms-api", "api", "i2")]
    assert stub.pod_briefs(selector="x") == []


# ---- KubernetesClient 확장: _ensure를 스텁하고 fake apps/core를 주입한다
#      (tests/test_k8s_read_pod_log.py와 같은 패턴 -- .venv에 kubernetes가 없다) ----

class _ApiError(Exception):
    """kubernetes.client.ApiException 흉내 -- 클라이언트는 status 속성만 본다."""
    def __init__(self, status):
        self.status = status
        super().__init__(f"status={status}")


class _Obj:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


class _FakeApps:
    def __init__(self):
        self.calls = []
        self.raise_on_read = None
        self.deployment = {
            "metadata": {"generation": 1},
            "spec": {"replicas": 1, "template": {"spec": {"containers": [
                {"name": "api", "image": "i"}]}}},
            "status": {"observed_generation": 1, "replicas": 1,
                       "updated_replicas": 1, "ready_replicas": 1,
                       "conditions": []}}

    def patch_namespaced_deployment(self, name, namespace, body, **kwargs):
        self.calls.append(("patch_deploy", name, namespace, body, kwargs))

    def patch_namespaced_daemon_set(self, name, namespace, body, **kwargs):
        self.calls.append(("patch_ds", name, namespace, body, kwargs))

    def read_namespaced_deployment(self, name, namespace):
        if self.raise_on_read:
            raise self.raise_on_read
        return _Obj(self.deployment)

    def read_namespaced_daemon_set(self, name, namespace):
        if self.raise_on_read:
            raise self.raise_on_read
        return _Obj({"metadata": {"generation": 1}, "status": {}})


def _client(apps):
    c = KubernetesClient("dms")
    c._ensure = lambda: None          # in-cluster config 로드를 건너뛴다
    c._apps = apps
    return c


def test_patch_workload_sends_explicit_strategic_content_type():
    apps = _FakeApps()
    _client(apps).patch_workload("Deployment", "dms-api", "dms",
                                 image_patch_body("api", "i2"))
    op, name, ns, body, kwargs = apps.calls[0]
    assert (op, name, ns) == ("patch_deploy", "dms-api", "dms")
    # 클라이언트 내부 기본값에 기대지 않는다 -- 명시하지 않으면 배열 교체 사고가 난다
    assert kwargs["_content_type"] == "application/strategic-merge-patch+json"


def test_patch_workload_routes_daemonset_to_daemonset_api():
    apps = _FakeApps()
    _client(apps).patch_workload("DaemonSet", "dms-agent", "dms", {})
    assert apps.calls[0][0] == "patch_ds"


def test_get_workload_normalizes_to_dict_payload():
    got = _client(_FakeApps()).get_workload("Deployment", "dms-api", "dms")
    # snake_case to_dict() 페이로드가 정규화 키로 돌아온다
    assert got["observed_generation"] == 1 and got["images"] == {"api": "i"}


def test_get_workload_404_is_none_and_403_reraises(caplog):
    apps = _FakeApps()
    apps.raise_on_read = _ApiError(404)
    assert _client(apps).get_workload("Deployment", "dms-api", "dms") is None
    apps.raise_on_read = _ApiError(403)
    with caplog.at_level("ERROR"):
        with pytest.raises(_ApiError):
            _client(apps).get_workload("Deployment", "dms-api", "dms")
    # RBAC 거부가 "없다"와 똑같이 보이면 안 된다 -- 로그로 구분(설계 §4)
    assert "403" in caplog.text


def test_unsupported_kind_is_rejected():
    with pytest.raises(ValueError):
        _client(_FakeApps()).patch_workload("StatefulSet", "x", "dms", {})
