from types import SimpleNamespace

import pytest
from dms.execution import ExecutionError
from dms.execution_volcano import ROLLOUT_REQUEST_TIMEOUT_SECONDS, KubernetesClient
from dms.rollout_runner import RolloutRunner, StubRolloutRunner, image_patch_body
from dms.rollout_status import (assess_daemonset, assess_deployment,
                                normalize_daemonset, normalize_deployment)


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


def test_stub_observations_are_a_faithful_pair_for_the_real_assessors():
    # 스텁은 클러스터 없이 도는 페어다 -- 돌려주는 dict 가 normalize_* 의 키 집합에서
    # 드리프트하거나 assess_* 가 "applied" 를 못 주면, Task 4 의 스텁 경로가 조용히
    # "progressing" 에 갇혀 롤아웃이 영원히 끝나지 않는다. 수동 확인으로는 이 페어가
    # 고정되지 않으므로 실제 판정 함수에 통과시켜 못 박는다.
    stub = StubRolloutRunner()
    stub.patch_image(kind="Deployment", name="dms-api", container="api", image="i2")
    stub.patch_image(kind="DaemonSet", name="dms-agent", container="agent", image="i2")
    deploy = stub.observe(kind="Deployment", name="dms-api")
    daemon = stub.observe(kind="DaemonSet", name="dms-agent")
    assert assess_deployment(deploy) == ("applied", None)
    assert assess_daemonset(daemon) == ("applied", None)
    # 판정 함수가 읽는 키만이 아니라 정규화 계약 전체와 모양이 같아야 한다.
    assert set(deploy) == set(normalize_deployment({}))
    assert set(daemon) == set(normalize_daemonset({}))


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
        # number_unavailable 은 0 이면 실 apiserver 가 필드를 아예 빼고 준다 --
        # 페어도 같은 모양으로 둬서 정규화의 unset 처리를 함께 태운다.
        self.daemon_set = {
            "metadata": {"generation": 2},
            "spec": {"template": {"spec": {"containers": [
                {"name": "agent", "image": "img:d23"}]}}},
            "status": {"observed_generation": 2, "desired_number_scheduled": 3,
                       "updated_number_scheduled": 3, "number_ready": 3,
                       "number_misscheduled": 0}}

    def patch_namespaced_deployment(self, name, namespace, body, **kwargs):
        self.calls.append(("patch_deploy", name, namespace, body, kwargs))

    def patch_namespaced_daemon_set(self, name, namespace, body, **kwargs):
        self.calls.append(("patch_ds", name, namespace, body, kwargs))

    def read_namespaced_deployment(self, name, namespace, **kwargs):
        self.calls.append(("read_deploy", name, namespace, None, kwargs))
        if self.raise_on_read:
            raise self.raise_on_read
        return _Obj(self.deployment)

    def read_namespaced_daemon_set(self, name, namespace, **kwargs):
        self.calls.append(("read_ds", name, namespace, None, kwargs))
        if self.raise_on_read:
            raise self.raise_on_read
        return _Obj(self.daemon_set)


def _pod(name, node, containers, phase, waiting_reasons):
    """실 클라이언트 파드 객체 흉내 -- list_pod_briefs 는 dict 가 아니라 속성으로 읽는다."""
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        spec=SimpleNamespace(
            node_name=node,
            containers=[SimpleNamespace(name=n, image=i) for n, i in containers]),
        status=SimpleNamespace(
            phase=phase,
            container_statuses=[
                SimpleNamespace(state=SimpleNamespace(
                    waiting=SimpleNamespace(reason=r) if r else None))
                for r in waiting_reasons]))


class _FakeCore:
    def __init__(self, pods=()):
        self.pods = list(pods)
        self.calls = []

    def list_namespaced_pod(self, namespace, **kwargs):
        self.calls.append((namespace, kwargs))
        return SimpleNamespace(items=self.pods)


def _client(apps, core=None):
    c = KubernetesClient("dms")
    c._ensure = lambda: None          # in-cluster config 로드를 건너뛴다
    c._apps = apps
    c._core = core
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


def test_get_workload_daemonset_normalizes_to_dict_payload():
    got = _client(_FakeApps()).get_workload("DaemonSet", "dms-agent", "dms")
    # DaemonSet 은 patch 라우팅만이 아니라 읽기 경로도 정규화 계약을 지켜야 한다 --
    # normalize_daemonset 이 아니라 normalize_deployment 로 잘못 라우팅되면
    # number_ready 가 통째로 사라져 영원히 progressing 이 된다.
    assert got["kind"] == "DaemonSet"
    assert got["observed_generation"] == 2 and got["number_ready"] == 3
    assert got["number_unavailable"] == 0        # unset -> 0
    assert got["images"] == {"agent": "img:d23"}


def test_unsupported_kind_is_rejected():
    with pytest.raises(ValueError):
        _client(_FakeApps()).patch_workload("StatefulSet", "x", "dms", {})


def test_unsupported_kind_is_rejected_before_any_api_work():
    # 프로그래밍 오류지 API 오류가 아니다 -- try 안에서 터지면 _log_forbidden 을 거쳐
    # API 실패처럼 새어 나간다. 검증이 먼저면 config 로드조차 시도하지 않는다:
    # 아래 클라이언트는 _ensure 를 스텁하지 않았으므로 검증이 뒤에 있으면
    # ModuleNotFoundError(kubernetes 미설치)로 죽는다.
    c = KubernetesClient("dms")
    with pytest.raises(ValueError):
        c.patch_workload("StatefulSet", "x", "dms", {})
    with pytest.raises(ValueError):
        c.get_workload("StatefulSet", "x", "dms")


def test_ensure_creates_apps_under_the_same_core_guard():
    # 설계 §4: _ensure 는 `if self._core is None:` 한 가드 안에서 core/custom/apps 를
    # 함께 만든다. `if self._apps is None:` 별도 가드가 생기면 _core 만 주입한 테스트가
    # 실 in-cluster config 를 로드하러 나간다 -- 그 회귀를 여기서 잡는다.
    # 올바른 형태에서는 _ensure 가 통째로 no-op 이라 _apps 가 None 인 채 남고,
    # apps 경로는 AttributeError 로만 죽는다.
    c = KubernetesClient("dms")
    c._core = _FakeCore()             # _ensure 를 스텁하지 않는다 -- 실 _ensure 를 태운다
    with pytest.raises(AttributeError):
        c.get_workload("Deployment", "dms-api", "dms")
    assert c._apps is None            # _ensure 가 apps 를 따로 만들지 않았다는 증거


def test_rollout_calls_carry_a_request_timeout():
    # 이 저장소의 리스는 갱신되지 않는다(controller.run_all_once) -- apiserver 가 멈추면
    # urllib3 기본값(무제한)으로 틱이 무한 블록되고, 리스가 만료돼 다른 파드가 같은
    # 루프에 진입한다. 롤아웃은 자기 자신을 재시작하는 경로라 특히 나쁘다.
    apps, core = _FakeApps(), _FakeCore()
    c = _client(apps, core=core)
    c.patch_workload("Deployment", "dms-api", "dms", image_patch_body("api", "i2"))
    c.patch_workload("DaemonSet", "dms-agent", "dms", {})
    c.get_workload("Deployment", "dms-api", "dms")
    c.get_workload("DaemonSet", "dms-agent", "dms")
    c.list_pod_briefs("dms", "app=dms-api")
    seen = [kwargs for _, _, _, _, kwargs in apps.calls] + [kw for _, kw in core.calls]
    assert len(seen) == 5
    assert all(kw.get("_request_timeout") == ROLLOUT_REQUEST_TIMEOUT_SECONDS
               for kw in seen)
    # 틱 하나가 최대 2회 호출(observe + pod_briefs)이므로 리스 하한 30초보다 넉넉히
    # 작아야 한 틱이 리스 안에서 끝난다.
    assert 0 < ROLLOUT_REQUEST_TIMEOUT_SECONDS * 2 < 30


def test_list_pod_briefs_keeps_every_waiting_reason():
    # 마지막 waiting 컨테이너의 사유만 남기면 보고되는 원인이 컨테이너 순서에 좌우된다.
    # 진단 전용 채널이므로 정보를 버리지 않고 전부(중복 제거해) 모은다.
    core = _FakeCore([
        _pod("p1", "dms-w1", [("api", "i1"), ("side", "i2")], "Pending",
             ["ImagePullBackOff", "CrashLoopBackOff"]),
        _pod("p2", "dms-w2", [("api", "i1")], "Running", [None]),
        _pod("p3", "dms-w3", [("a", "i"), ("b", "i")], "Pending",
             ["ImagePullBackOff", "ImagePullBackOff"]),
    ])
    briefs = _client(_FakeApps(), core=core).list_pod_briefs("dms", "app=dms-api")
    assert core.calls[0][1]["label_selector"] == "app=dms-api"
    assert briefs[0]["waiting_reason"] == "ImagePullBackOff,CrashLoopBackOff"
    assert briefs[0]["node"] == "dms-w1"
    assert briefs[0]["images"] == {"api": "i1", "side": "i2"}
    assert briefs[1]["waiting_reason"] is None and briefs[1]["phase"] == "Running"
    assert briefs[2]["waiting_reason"] == "ImagePullBackOff"   # 중복은 한 번만
