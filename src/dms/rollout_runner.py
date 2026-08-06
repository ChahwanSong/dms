"""롤아웃의 k8s I/O. 기존 K8sClient(create/get/delete/read_pod_log)를 확장하지
않는다 -- 네 개의 기존 테스트 페어가 그 계약을 구조적으로 구현하고 있고, 그중
apps/v1 동사가 필요한 것은 하나도 없다(설계 §4). 좁은 Protocol을 여기 따로 두고,
같은 구체 클래스 KubernetesClient가 구조적 타이핑으로 둘 다 만족한다 --
BuildRunner가 러너 수준에서 분리하되 클라이언트는 공유한 것과 같은 방식이다."""
import logging
from typing import Protocol

from .execution import ExecutionError

logger = logging.getLogger(__name__)


class WorkloadClient(Protocol):
    def patch_workload(self, kind: str, name: str, namespace: str,
                       body: dict) -> None: ...
    def get_workload(self, kind: str, name: str, namespace: str) -> "dict | None":
        """정규화된 상태 dict(rollout_status.normalize_*) 또는 404면 None."""
        ...


def image_patch_body(container: str, image: str) -> dict:
    # strategic merge patch: containers는 name을 patchMergeKey로 병합된다.
    # JSON merge patch는 배열 전체를 교체해 env/volumeMounts를 날린다(설계 §4).
    return {"spec": {"template": {"spec": {"containers": [
        {"name": container, "image": image}]}}}}


class RolloutRunner:
    def __init__(self, k8s, *, namespace):
        self._k8s = k8s
        self._ns = namespace

    def patch_image(self, *, kind, name, container, image) -> None:
        try:
            self._k8s.patch_workload(kind, name, self._ns,
                                     image_patch_body(container, image))
        except Exception as exc:
            raise ExecutionError("patch_failed", str(exc)[:200]) from exc

    def observe(self, *, kind, name):
        try:
            return self._k8s.get_workload(kind, name, self._ns)
        except Exception as exc:
            raise ExecutionError("observe_failed", str(exc)[:200]) from exc

    def pod_briefs(self, *, selector) -> list[dict]:
        # best-effort 진단 채널(DaemonSet 타임아웃의 멈춘 노드 사유, 설계 §3) --
        # 이것이 실패해도 롤아웃 판정을 막으면 안 된다. 소비자가 RolloutWatcher
        # 하나뿐이라 Protocol로 선언하지 않는다(WorkloadClient는 두 메서드 유지).
        try:
            return self._k8s.list_pod_briefs(self._ns, selector)
        except Exception as exc:
            logger.warning("pod briefs failed selector=%s: %s", selector, exc)
            return []


class StubRolloutRunner:
    """클러스터가 없을 때(execution_backend != "volcano") 쓰는 결정적 페어.
    patch를 기록하고, observe는 패치된 이미지로 즉시 수렴한 정규화 dict를 준다."""
    def __init__(self):
        self.patched = []        # (kind, name, container, image)
        self._images = {}        # (kind, name) -> {container: image}

    def patch_image(self, *, kind, name, container, image) -> None:
        self.patched.append((kind, name, container, image))
        self._images.setdefault((kind, name), {})[container] = image

    def observe(self, *, kind, name):
        images = self._images.get((kind, name))
        if images is None:
            return None
        if kind == "DaemonSet":
            return {"kind": "DaemonSet", "generation": 1, "observed_generation": 1,
                    "desired_number_scheduled": 1, "updated_number_scheduled": 1,
                    "number_ready": 1, "number_unavailable": 0,
                    "number_misscheduled": 0, "images": dict(images)}
        return {"kind": "Deployment", "generation": 1, "observed_generation": 1,
                "replicas": 1, "status_replicas": 1, "updated_replicas": 1,
                "ready_replicas": 1, "conditions": [], "images": dict(images)}

    def pod_briefs(self, *, selector) -> list[dict]:
        return []
