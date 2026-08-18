"""빌드 파드의 k8s I/O. 실행 어댑터(ExecutionAdapter)와 프로토콜을 공유하지 않는다 --
빌드는 JobSpec 도 큐도 아티팩트도 없어서 그 계약에 억지로 끼우면 빈 dict 만 늘어난다."""
import logging

from .build_manifests import build_build_pod, build_probe_pod
from .execution import ExecStatus, ExecutionError
from .repositories.builds import (build_pod_name, build_probe_pod_name,
                                  effective_tag)

logger = logging.getLogger(__name__)

# I5: 빌드 ref 접두의 유일한 출처 -- build_watcher.py/pod_gc.py/api/routes_builds.py가
# 전부 여기서 import해 쓴다. 각자 리터럴 "buildpod"를 들고 있으면 한 곳만 드리프트해도
# (예: 오타로 "pod"가 되면) ref가 안 맞아 poll/terminate/read_log가 조용히 miss된다.
BUILD_REF_PREFIX = "buildpod"
_POD_PHASE = {"Pending": ExecStatus.PENDING, "Running": ExecStatus.RUNNING,
              "Succeeded": ExecStatus.SUCCEEDED, "Failed": ExecStatus.FAILED,
              "Unknown": ExecStatus.FAILED}


def _name(ref: str) -> str:
    prefix, _, name = ref.partition("/")
    if prefix != BUILD_REF_PREFIX or not name:
        raise ExecutionError("invalid_build_ref", ref[:200])
    return name


class BuildRunner:
    def __init__(self, k8s, *, namespace, registry, builder_image, timeout_seconds,
                 job_image="", preflight_timeout_seconds=180):
        self._k8s = k8s
        self._ns = namespace
        self._registry = registry
        self._builder_image = builder_image
        self._timeout_seconds = timeout_seconds
        # 슬라이스 21 §2.5: 프로브는 builder image 가 아니라 job_image 로 띄운다 --
        # 워커에 캐시돼 있고 pull 도 pkg-01 만 필요해 프로브 기동이 인터넷과
        # 무관하다. 기본값은 기존 생성 호출(테스트 다수) 무변경용이고, 실제 배선
        # (wiring.py)은 resolve 클로저를 넘긴다. str 또는 0-인자 callable 을 받는
        # 계약은 VolcanoExecutionAdapter 의 job_image/artifact_base 와 동일(슬라이스
        # 35) -- 릴리스의 job-image 오버라이드가 프로브 이미지에도 재시작 없이 반영.
        self._job_image_fn = (job_image if callable(job_image)
                              else (lambda: job_image))
        self._preflight_timeout_seconds = preflight_timeout_seconds

    def submit(self, build) -> str:
        try:
            # repo_url 컬럼이 소스 경로를 담는다(repositories.builds.create 주석).
            manifest = build_build_pod(
                build_id=build["build_id"], source_path=build["repo_url"],
                tag=effective_tag(build), images=build["images"],
                node=build["node_name"], namespace=self._ns,
                registry=self._registry, builder_image=self._builder_image,
                timeout_seconds=self._timeout_seconds)
        except Exception as exc:
            raise ExecutionError("submit_failed", str(exc)[:200]) from exc
        name = manifest["metadata"]["name"]
        ref = f"{BUILD_REF_PREFIX}/{name}"
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

    def submit_preflight(self, build) -> str:
        """적합성 프로브 파드(§2.5)를 멱등 제출한다. submit 과 같은 계약: 이름이
        build_id 에서 결정적이라 AlreadyExists 는 이전 틱이 만든 이 빌드 자신의
        프로브다 -- 성공 취급해야 워처의 매 틱 재호출("없음→생성"과 "진행 중→대기"
        를 상태 저장 없이 한 호출로 접는 장치)이 안전하다."""
        try:
            manifest = build_probe_pod(
                build_id=build["build_id"], source_path=build["repo_url"],
                node=build["node_name"], namespace=self._ns,
                registry=self._registry, job_image=self._job_image_fn(),
                timeout_seconds=self._preflight_timeout_seconds)
        except Exception as exc:
            # 프로브 생성 실패는 기존 submit_failed 재사용(§4) -- "preflight:"
            # detail 접두가 빌드 파드 제출 실패와 구분한다(새 코드를 만들지 않는다).
            raise ExecutionError("submit_failed",
                                 f"preflight: {str(exc)[:180]}") from exc
        name = manifest["metadata"]["name"]
        ref = f"{BUILD_REF_PREFIX}/{name}"
        try:
            self._k8s.create(manifest)
        except Exception as exc:
            # submit 의 AlreadyExists 관용 선례 그대로: 존재하면 성공 취급.
            try:
                exists = self._k8s.get("Pod", name, self._ns) is not None
            except Exception:
                exists = False
            if not exists:
                raise ExecutionError("submit_failed",
                                     f"preflight: {str(exc)[:180]}") from exc
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

    def failure_reason(self, ref) -> str:
        """실패한 빌드 파드의 사유를 구분한다(슬라이스 21 설계 §4 의 한계 해소).

        지금까지 OOMKilled(memory limit 1Gi 초과)와 Evicted(emptyDir sizeLimit 초과
        또는 노드 압박)가 전부 파드 phase Failed 로 접혀 `build_failed` 로 뭉개졌다 --
        운영자는 "로그가 급단절된 build_failed 를 만나면 OOM 을 의심하라"는 문서에
        의존해야 했다. 이 둘은 대응이 완전히 다르다: OOM 은 봉투를 키우거나 빌드를
        줄여야 하고, Evicted 는 노드 디스크·메모리를 봐야 한다.

        **구분 재료가 없으면 지어내지 않고 build_failed 로 남긴다** -- 파드가 이미
        사라졌거나(GC) 조회가 실패한 경우도 마찬가지다. 없는 사실을 만드는 것보다
        기존만큼만 아는 편이 낫다."""
        try:
            obj = self._k8s.get("Pod", _name(ref), self._ns)
        except Exception:
            return "build_failed"
        status = (obj or {}).get("status") or {}
        # 축출은 파드 수준 reason 이다(컨테이너가 시작조차 못 했을 수 있다).
        if status.get("reason") == "Evicted":
            return "build_evicted"
        for cs in status.get("containerStatuses") or []:
            term = ((cs.get("state") or {}).get("terminated") or {})
            if term.get("reason") == "OOMKilled":
                return "build_oom_killed"
        return "build_failed"

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
        ref = f"{BUILD_REF_PREFIX}/{build_pod_name(build['build_id'])}"
        self._log[ref] = "DMS_COMMIT_SHA=stubcommit\nDMS_BUILD_OK\n"
        return ref

    def submit_preflight(self, build) -> str:
        # 클러스터 없는 경로도 프리플라이트를 "즉시 통과"로 지나가야 한다(설계 §4:
        # 로컬·CI 가 클러스터 없이 초록) -- poll 은 어떤 ref 든 SUCCEEDED 이므로
        # OK 마커 로그만 심으면 워처가 같은 틱에 빌드 제출로 넘어간다.
        ref = f"{BUILD_REF_PREFIX}/{build_probe_pod_name(build['build_id'])}"
        self._log[ref] = "DMS_PREFLIGHT_OK\n"
        return ref

    def poll(self, ref) -> ExecStatus:
        _name(ref)
        return ExecStatus.SUCCEEDED

    def failure_reason(self, ref) -> str:
        # 스텁은 실패를 만들지 않는다(poll 이 항상 SUCCEEDED) -- 인터페이스만 맞춘다.
        _name(ref)
        return "build_failed"

    def read_log(self, ref):
        return self._log.get(ref, "")

    def terminate(self, ref) -> None:
        _name(ref)
        self._log.pop(ref, None)
