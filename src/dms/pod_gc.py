"""종단 잡이 남긴 preflight Pod 을 지운다. Volcano Job 은 ttlSecondsAfterFinished 가
처리하지만 베어 Pod 에는 TTL 이 없다.

**종단 잡만** 대상으로 한다 — 비종단 잡의 파드를 지우면 stepper 가 그것을 실패로 오인한다."""
import logging

logger = logging.getLogger(__name__)


class PodGarbageCollector:
    def __init__(self, repos, execution_adapter, *, after_seconds: int, limit: int = 200):
        self._repos = repos
        self._exec = execution_adapter
        self._after = after_seconds
        self._limit = limit

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
        return {"deleted": deleted}
