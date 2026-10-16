"""종단 잡이 남긴 preflight Pod 과 (실패한) vcjob 을 지운다. 베어 Pod 에는 TTL 이 없고, Volcano Job 의
ttlSecondsAfterFinished 는 Completed 만 치운다 -- Aborted(PodFailed → AbortJob) vcjob 과 그 Error 파드는 테스트베드에서
61일째 남아 있었다(2026-10-16 사용자 보고 「몇분 후에 k8s 을 직접 확인해보니 아직 ... pod 들이 보여」 조사).

**vcjob/ 참조**(2026-10-16): Succeeded 가 **아닌** 종단 잡의 것만 지운다 -- Completed vcjob 은 Volcano TTL 의 몫이다.
그 창은 max(after_seconds, vcjob_ttl_seconds) 다: 운영자가 DMS_VCJOB_TTL_SECONDS 를 늘려 launcher 로그를 오래 두려 하면
실패 잡도 그만큼 둔다(실패 진단 로그는 어차피 DB 에 박제된다 -- stepper._archive_diag). TTL 0 = 「vcjob 을 지우지
않는다」(_apply_ttl 이 ttlSecondsAfterFinished 를 빼는 값) -- 그때는 실패 vcjob 도 두고 Pod 만 지운다.

**완료 표시**(pods_gc_at): 한 잡의 정리가 끝나면 찍어 질의에서 빠진다. 예전엔 표시가 없어 오래된 순 LIMIT 200 이 매 틱
같은 앞쪽 묶음만 다시 지우고(404 무시) 그 뒤의 잡에 영영 닿지 못했다(테스트베드 8일 된 preflight Pod 82개). 일시 실패
(ExecutionError -- API 서버 장애 등)가 하나라도 있으면 찍지 않고 다음 틱에 다시 본다. 그 밖의 예외(변조된 ref 등 다시 해도
같은 결과)는 기록하고 찍는다 -- 독 행이 묶음 앞자리를 영영 차지하지 않게. 배포 직후 첫 패스는 표시 없는 옛 종단 잡을 한
번 훑는다(틱당 limit 건).

**종단 잡만** 대상으로 한다 — 비종단 잡의 파드를 지우면 stepper 가 그것을 실패로 오인한다.
빌드 파드·프리플라이트 프로브 파드도 같은 이유로 **종단 빌드만** 대상이다 --
비종단 빌드의 빌드 파드가 사라지면 BuildRunner.poll 이 객체 없음을 FAILED 로
오인하고, 프로브 파드가 사라지면 BuildWatcher pending 루프가 멀쩡한 빌드를
build_preflight_failed 로 오기록한다."""
import logging

from .build_runner import BUILD_REF_PREFIX
from .domain import DataJobState
from .execution import ExecutionError
from .repositories.builds import build_pod_name, build_probe_pod_name

logger = logging.getLogger(__name__)


class PodGarbageCollector:
    def __init__(self, repos, execution_adapter, *, after_seconds: int, limit: int = 200,
                build_runner=None, vcjob_ttl_seconds: int = 86400):
        self._repos = repos
        self._exec = execution_adapter
        self._after = after_seconds
        self._limit = limit
        self._build_runner = build_runner
        self._vcjob_ttl = vcjob_ttl_seconds        # 0 = vcjob 을 지우지 않는다(모듈 docstring)

    def _targets(self, job) -> list[str]:
        out = []
        for ref in (job.get("phase_refs") or {}).values():
            ref = str(ref) if ref else ""
            if ref.startswith(("pod/", "pods/")):
                out.append(ref)
            elif (ref.startswith("vcjob/") and self._vcjob_ttl > 0
                  and job["state"] != DataJobState.SUCCEEDED.value):
                out.append(ref)
        return out

    def run_once(self, *, now_iso: str | None = None) -> dict:
        deleted = 0
        vc_after = max(self._after, self._vcjob_ttl) if self._vcjob_ttl > 0 else None
        jobs = self._repos.data_jobs.terminal_jobs_older_than(
            self._after, limit=self._limit, now_iso=now_iso, vcjob_after_seconds=vc_after)
        for job in jobs:
            transient = False
            for ref in self._targets(job):
                try:
                    self._exec.terminate(ref)
                    deleted += 1
                except ExecutionError as exc:
                    transient = True          # 다음 틱에 다시(완료 표시 안 함)
                    logger.warning("pod gc failed ref=%s: %s", ref, exc)
                except Exception as exc:      # 다시 해도 같은 결과(변조된 ref 등) -- 표시하고 넘어간다
                    logger.warning("pod gc skipped ref=%s: %s", ref, exc)
            if not transient:
                self._repos.data_jobs.mark_pods_gc(job["job_id"], now_iso=now_iso)

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
