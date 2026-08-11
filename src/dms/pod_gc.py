"""종단 잡이 남긴 preflight Pod 을 지운다. Volcano Job 은 ttlSecondsAfterFinished 가
처리하지만 베어 Pod 에는 TTL 이 없다.

**종단 잡만** 대상으로 한다 — 비종단 잡의 파드를 지우면 stepper 가 그것을 실패로 오인한다.
빌드 파드·프리플라이트 프로브 파드도 같은 이유로 **종단 빌드만** 대상이다 --
비종단 빌드의 빌드 파드가 사라지면 BuildRunner.poll 이 객체 없음을 FAILED 로
오인하고, 프로브 파드가 사라지면 BuildWatcher pending 루프가 멀쩡한 빌드를
build_preflight_failed 로 오기록한다."""
import logging

from .build_runner import BUILD_REF_PREFIX
from .repositories.builds import build_pod_name, build_probe_pod_name

logger = logging.getLogger(__name__)


class PodGarbageCollector:
    def __init__(self, repos, execution_adapter, *, after_seconds: int, limit: int = 200,
                build_runner=None):
        self._repos = repos
        self._exec = execution_adapter
        self._after = after_seconds
        self._limit = limit
        self._build_runner = build_runner

    def run_once(self, *, now_iso: str | None = None) -> dict:
        deleted = 0
        for job in self._repos.data_jobs.terminal_jobs_older_than(
                self._after, limit=self._limit, now_iso=now_iso):
            for ref in (job.get("phase_refs") or {}).values():
                if not ref or not str(ref).startswith(("pod/", "pods/")):
                    continue
                try:
                    self._exec.terminate(ref)
                    deleted += 1
                except Exception as exc:
                    logger.warning("pod gc failed ref=%s: %s", ref, exc)

        # 종단 빌드가 남긴 빌드 파드 + 프리플라이트 프로브 파드(슬라이스 21)를
        # 같은 창(after_seconds)으로 수거한다. 비종단 빌드는 절대 건드리지 않는다:
        # 빌드 파드가 사라지면 poll 이 FAILED 로 읽고, 프로브 파드가 사라지면
        # 워처 pending 루프가 프로브 실패로 오인해 멀쩡한 빌드를
        # build_preflight_failed 로 죽인다. terminate 는 ref 별로 격리한다 --
        # 한 파드의 실패가 나머지 수거를 막으면 안 된다(잡 파드 GC 와 같은 계약).
        if self._build_runner is not None:
            for build in self._repos.builds.terminal_older_than(
                    self._after, limit=self._limit, now_iso=now_iso):
                for pod in (build_pod_name(build["build_id"]),
                            build_probe_pod_name(build["build_id"])):
                    ref = f"{BUILD_REF_PREFIX}/{pod}"
                    try:
                        self._build_runner.terminate(ref)
                        deleted += 1
                    except Exception as exc:
                        logger.warning("build pod gc failed ref=%s: %s", ref, exc)
        return {"deleted": deleted}
