"""빌드 파드의 k8s I/O. 실행 어댑터(ExecutionAdapter)와 프로토콜을 공유하지 않는다 --
빌드는 JobSpec 도 큐도 아티팩트도 없어서 그 계약에 억지로 끼우면 빈 dict 만 늘어난다."""
import logging

from .build_manifests import build_build_pod
from .execution import ExecStatus, ExecutionError
from .repositories.builds import build_pod_name

logger = logging.getLogger(__name__)

_PREFIX = "buildpod"
_POD_PHASE = {"Pending": ExecStatus.PENDING, "Running": ExecStatus.RUNNING,
              "Succeeded": ExecStatus.SUCCEEDED, "Failed": ExecStatus.FAILED,
              "Unknown": ExecStatus.FAILED}


def _name(ref: str) -> str:
    prefix, _, name = ref.partition("/")
    if prefix != _PREFIX or not name:
        raise ExecutionError("invalid_build_ref", ref[:200])
    return name


class BuildRunner:
    def __init__(self, k8s, *, namespace, registry, builder_image):
        self._k8s = k8s
        self._ns = namespace
        self._registry = registry
        self._builder_image = builder_image

    def submit(self, build) -> str:
        try:
            manifest = build_build_pod(
                build_id=build["build_id"], repo_url=build["repo_url"],
                git_ref=build["git_ref"], images=build["images"],
                node=build["node_name"], namespace=self._ns,
                registry=self._registry, builder_image=self._builder_image)
        except Exception as exc:
            raise ExecutionError("submit_failed", str(exc)[:200]) from exc
        name = manifest["metadata"]["name"]
        ref = f"{_PREFIX}/{name}"
        try:
            self._k8s.create(manifest)
        except Exception as exc:
            # 파드 이름이 build_id에서 결정적으로 나온다 -- create가 실패해도
            # (예: AlreadyExists) 같은 이름의 파드가 이미 떠 있다면 그건 이전
            # 시도가 만든 이 빌드 자신의 파드다. 재시도 시나리오: submit 성공
            # 직후 mark_running 전에 프로세스가 죽으면 다음 틱이 같은
            # build_id로 다시 submit한다 -- k8s.delete가 404를 삼켜 멱등한
            # 것과 같은 계약으로 존재를 확인해 성공 취급해야, 잘 도는 빌드를
            # Failed로 오기록하지 않는다.
            try:
                exists = self._k8s.get("Pod", name, self._ns) is not None
            except Exception:
                exists = False  # 존재 확인 자체가 실패하면 원래 실패로 폴백
            if not exists:
                raise ExecutionError("submit_failed", str(exc)[:200]) from exc
        return ref

    def poll(self, ref) -> ExecStatus:
        try:
            obj = self._k8s.get("Pod", _name(ref), self._ns)
        except ExecutionError:
            raise
        except Exception as exc:
            raise ExecutionError("poll_failed", str(exc)[:200]) from exc
        if obj is None:
            return ExecStatus.FAILED
        return _POD_PHASE.get((obj.get("status") or {}).get("phase"), ExecStatus.FAILED)

    def read_log(self, ref):
        try:
            return self._k8s.read_pod_log(_name(ref), self._ns)
        except ExecutionError:
            raise
        except Exception as exc:
            logger.warning("build log read failed ref=%s: %s", ref, exc)
            return None

    def terminate(self, ref) -> None:
        try:
            self._k8s.delete("Pod", _name(ref), self._ns)
        except ExecutionError:
            raise
        except Exception as exc:
            raise ExecutionError("terminate_failed", str(exc)[:200]) from exc


class StubBuildRunner:
    """클러스터가 없을 때(execution_backend != "volcano") 쓰는 결정적 페어."""
    def __init__(self):
        self._log = {}

    def submit(self, build) -> str:
        ref = f"{_PREFIX}/{build_pod_name(build['build_id'])}"
        self._log[ref] = "DMS_COMMIT_SHA=stubcommit\nDMS_BUILD_OK\n"
        return ref

    def poll(self, ref) -> ExecStatus:
        _name(ref)
        return ExecStatus.SUCCEEDED

    def read_log(self, ref):
        return self._log.get(ref, "")

    def terminate(self, ref) -> None:
        _name(ref)
        self._log.pop(ref, None)
