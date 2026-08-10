"""큐 리더(슬라이스 17 설계 §2.1·§4)의 계약.

핵심은 세 상태의 비뭉개짐이다: 403(예외 전파)·404/CRD 부재(None)·빈 목록([])이
각각 다른 결과로 나와야 한다. 여기서 한 번 접히면 어떤 상위 계층도 되살릴 수 없다.
KubernetesClient 쪽은 test_k8s_read_pod_log.py 와 같은 인스턴스 페이크 주입으로
kwargs(_request_timeout)와 좌표를 못박는다 -- .venv 에 kubernetes 가 없어 실제
ApiException 타입은 만들 수 없고, 클라이언트가 보는 것도 status 속성뿐이다."""
import pytest

from dms.execution_volcano import (KubernetesClient,
                                   ROLLOUT_REQUEST_TIMEOUT_SECONDS)
from dms.queue_reader import DMS_QUEUE, StubQueueReader, VolcanoQueueReader


class _FakeK8s:
    """리더가 쓰는 두 메서드만 가진 페어(K8sClient 와 같은 구조적 타이핑 관례)."""
    def __init__(self, queue=None, podgroups=None):
        self.queue = queue
        self.podgroups = podgroups

    def get_queue(self, name):
        self.requested = name
        return self.queue

    def list_podgroups(self, namespace):
        self.listed = namespace
        return self.podgroups


def test_read_queue_extracts_only_name_and_state():
    # Queue 에서 읽는 것은 state 하나다(설계 §2.1) -- phase 카운터는 omitempty
    # (키 부재=0)인 데다 PodGroup 으로 유도되므로 여기서 읽지 않는다.
    k8s = _FakeK8s(queue={"metadata": {"name": "dms-data"},
                          "status": {"state": "Open", "running": 2},
                          "spec": {"weight": 1}})
    reader = VolcanoQueueReader(k8s, namespace="dms")
    assert reader.read_queue() == {"name": "dms-data", "state": "Open"}
    assert k8s.requested == DMS_QUEUE


def test_read_queue_unknown_stays_none():
    assert VolcanoQueueReader(_FakeK8s(queue=None),
                              namespace="dms").read_queue() is None


def test_read_queue_without_status_has_null_state():
    # 막 생성된 큐는 status 가 없을 수 있다 -- state 만 null 로 강등, 죽지 않는다
    k8s = _FakeK8s(queue={"metadata": {"name": "dms-data"}})
    assert VolcanoQueueReader(k8s, namespace="dms").read_queue() == {
        "name": "dms-data", "state": None}


def test_read_podgroups_filters_by_queue_and_normalizes():
    # PodGroup 에는 DMS 라벨이 없고 이름 접미(-<uid>)는 문서화된 계약이 아니다 --
    # 네임스페이스 list 후 spec.queue 필터가 유일하게 안전한 경로다(설계 §2.1).
    k8s = _FakeK8s(podgroups={"items": [
        {"metadata": {"name": "job-a-uid1",
                      "creationTimestamp": "2026-08-10T00:00:00Z"},
         "spec": {"queue": "dms-data", "minMember": 3},
         "status": {"phase": "Pending"}},
        {"metadata": {"name": "other-uid2"},
         "spec": {"queue": "default", "minMember": 1},
         "status": {"phase": "Running"}},                # 다른 큐 -- 제외
        {"metadata": {"name": "job-c-uid3"},
         "spec": {"queue": "dms-data"}},                 # status/시각 없음 -- null 강등
    ]})
    reader = VolcanoQueueReader(k8s, namespace="dms")
    assert reader.read_podgroups() == [
        {"name": "job-a-uid1", "phase": "Pending", "min_member": 3,
         "created_at": "2026-08-10T00:00:00Z"},
        {"name": "job-c-uid3", "phase": None, "min_member": None,
         "created_at": None},
    ]
    assert k8s.listed == "dms"


def test_read_podgroups_distinguishes_absent_from_empty():
    # None(CRD 부재)과 [](빈 큐)는 다른 결과여야 한다(설계 §4).
    assert VolcanoQueueReader(_FakeK8s(podgroups=None),
                              namespace="dms").read_podgroups() is None
    assert VolcanoQueueReader(_FakeK8s(podgroups={"items": []}),
                              namespace="dms").read_podgroups() == []


def test_stub_pair_is_deterministic_without_cluster():
    # 기본 백엔드가 stub 이다(config.py) -- 이 페어가 없으면 모든 로컬·CI 에서
    # /api/admin/metrics/queue 가 500 이다(설계 §2.5).
    stub = StubQueueReader()
    assert stub.read_queue() == {"name": DMS_QUEUE, "state": "Open"}
    assert stub.read_podgroups() == []


# ---- KubernetesClient.get_queue / list_podgroups ----

class _FakeCustom:
    def __init__(self, *, fail_status=None, result=None):
        self.calls = []
        self._fail = fail_status
        self._result = result

    def _maybe_fail(self):
        if self._fail is not None:
            exc = RuntimeError("api error")
            exc.status = self._fail   # ApiException 덕타이핑(get_workload:380 관례)
            raise exc

    def get_cluster_custom_object(self, group, version, plural, name, **kw):
        self.calls.append((group, version, plural, name, kw))
        self._maybe_fail()
        return self._result

    def list_namespaced_custom_object(self, group, version, namespace, plural, **kw):
        self.calls.append((group, version, namespace, plural, kw))
        self._maybe_fail()
        return self._result


def _k8s(custom):
    c = KubernetesClient("dms")
    c._custom = custom
    c._ensure = lambda: None          # in-cluster config 로드를 건너뛴다
    return c


def test_get_queue_passes_request_timeout_and_coordinates():
    custom = _FakeCustom(result={"status": {"state": "Open"}})
    assert _k8s(custom).get_queue("dms-data") == {"status": {"state": "Open"}}
    group, version, plural, name, kw = custom.calls[0]
    assert (group, version, plural, name) == (
        "scheduling.volcano.sh", "v1beta1", "queues", "dms-data")
    # urllib3 기본은 무제한 -- 이 kwarg 가 빠지면 apiserver 멈춤이 5초 폴링의
    # 스레드풀을 고갈시킨다(설계 §1-8). _preload_content 를 못박은
    # test_k8s_read_pod_log 와 같은 방식으로 kwarg 자체를 고정한다.
    assert kw.get("_request_timeout") == ROLLOUT_REQUEST_TIMEOUT_SECONDS


def test_list_podgroups_passes_request_timeout_and_coordinates():
    custom = _FakeCustom(result={"items": []})
    assert _k8s(custom).list_podgroups("dms") == {"items": []}
    group, version, namespace, plural, kw = custom.calls[0]
    assert (group, version, namespace, plural) == (
        "scheduling.volcano.sh", "v1beta1", "dms", "podgroups")
    assert kw.get("_request_timeout") == ROLLOUT_REQUEST_TIMEOUT_SECONDS


def test_404_folds_to_none_but_403_raises():
    # 404 = CRD/오브젝트 부재 -> None(화면 "알 수 없음"). 403 은 올려서 라우트가
    # 로그와 함께 그 축만 강등한다 -- 어느 쪽도 빈 결과로 접히면 안 된다(설계 §4).
    assert _k8s(_FakeCustom(fail_status=404)).get_queue("dms-data") is None
    assert _k8s(_FakeCustom(fail_status=404)).list_podgroups("dms") is None
    with pytest.raises(RuntimeError):
        _k8s(_FakeCustom(fail_status=403)).get_queue("dms-data")
    with pytest.raises(RuntimeError):
        _k8s(_FakeCustom(fail_status=403)).list_podgroups("dms")
