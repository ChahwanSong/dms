"""종단 잡이 남긴 preflight Pod 과 (실패한) vcjob 을 지운다. 베어 Pod 에는 TTL 이 없고, Volcano Job 의
ttlSecondsAfterFinished 는 Completed 만 치운다 -- Aborted(PodFailed → AbortJob) vcjob 과 그 Error 파드는 테스트베드에서
61일째 남아 있었다(2026-10-16 사용자 보고 「몇분 후에 k8s 을 직접 확인해보니 아직 ... pod 들이 보여」 조사).

**vcjob/ 참조**(2026-10-16): Succeeded 가 **아닌** 종단 잡의 것만 지운다 -- Completed vcjob 은 Volcano TTL 의 몫이다.
그 창은 max(after_seconds, vcjob_ttl_seconds) 다: 운영자가 DMS_VCJOB_TTL_SECONDS 를 늘려 launcher 로그를 오래 두려 하면
실패 잡도 그만큼 둔다(실패 진단 로그는 어차피 DB 에 박제된다 -- stepper._archive_diag). TTL 0 = 「vcjob 을 지우지
않는다」(_apply_ttl 이 ttlSecondsAfterFinished 를 빼는 값) -- 그때는 실패 vcjob 도 두고 Pod 만 지운다.

**완료 표시**(pods_gc_at): 한 잡의 정리가 끝나면 찍어 질의에서 빠진다. 예전엔 표시가 없어 오래된 순 LIMIT 200 이 매 틱
같은 앞쪽 묶음만 다시 지우고(404 무시) 그 뒤의 잡에 영영 닿지 못했다(테스트베드 8일 된 preflight Pod 82개). 지우기가
하나라도 실패한 잡은 찍지 않고 **RETRY_BACKOFF_SECONDS 뒤에** 다시 본다(인스턴스 메모리 -- 컨트롤러가 하나를 계속 쓴다,
재시작하면 곧바로 다시). 실 어댑터는 실패를 전부 ExecutionError 로 접어 일시·영구를 가를 수 없다(검증 지적) -- 그래서
가르지 않고, 실패한 잡은 그 사이 묶음에서 건너뛰고 그만큼 더 읽어 뒤의 잡에 닿는다(독 행이 앞자리를 차지하지 않게).
배포 직후 첫 패스는 표시 없는 옛 종단 잡을 한 번 훑는다(틱당 limit 건).

**빌드도 같은 완료 표시**(builds.pods_gc_at, 2026-10-26): 빌드·프로브 파드 둘 다 지워지면(404 = 이미 없음 = 성공) 찍고,
하나라도 실패하면 찍지 않고 같은 RETRY_BACKOFF_SECONDS 뒤에 다시(잡과 따로 기억 -- build_id 와 job_id 는 둘 다 uuid hex 라 한
사전에 섞으면 구분이 흐려진다). 예전엔 표시가 없어 종단 빌드가 limit 을 넘으면 앞쪽 200건만 되풀이했다.

**종단 잡만** 대상으로 한다 — 비종단 잡의 파드를 지우면 stepper 가 그것을 실패로 오인한다.
빌드 파드·프리플라이트 프로브 파드도 같은 이유로 **종단 빌드만** 대상이다 --
비종단 빌드의 빌드 파드가 사라지면 BuildRunner.poll 이 객체 없음을 FAILED 로
오인하고, 프로브 파드가 사라지면 BuildWatcher pending 루프가 멀쩡한 빌드를
build_preflight_failed 로 오기록한다."""
import logging
import time

from .build_runner import BUILD_REF_PREFIX
from .db import iso_epoch
from .domain import DataJobState
from .repositories.builds import build_pod_name, build_probe_pod_name

logger = logging.getLogger(__name__)

RETRY_BACKOFF_SECONDS = 3600
# 재시도 대기 중인 잡 기억 상한 -- 넘치면 가장 먼저 다시 볼 것부터 잊는다(그 잡은 곧바로 다시 시도될 뿐).
_RETRY_MAX = 1000


class PodGarbageCollector:
    def __init__(self, repos, execution_adapter, *, after_seconds: int, limit: int = 200,
                build_runner=None, vcjob_ttl_seconds: int = 86400):
        self._repos = repos
        self._exec = execution_adapter
        self._after = after_seconds
        self._limit = limit
        self._build_runner = build_runner
        self._vcjob_ttl = vcjob_ttl_seconds        # 0 = vcjob 을 지우지 않는다(모듈 docstring)
        self._retry_at = {}                        # job_id -> 다시 볼 epoch 초(모듈 docstring 「완료 표시」)
        self._build_retry_at = {}                  # build_id -> 다시 볼 epoch 초(같은 규칙, 빌드 전용)

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
        now = iso_epoch(now_iso) if now_iso else time.time()
        waiting = {j for j, t in self._retry_at.items() if t > now}
        vc_after = max(self._after, self._vcjob_ttl) if self._vcjob_ttl > 0 else None
        # 재시도 대기 중인 잡은 건너뛰므로 그만큼 더 읽는다 -- 실패한 잡이 limit 칸을 차지해 뒤의 잡이 굶지 않게.
        jobs = self._repos.data_jobs.terminal_jobs_older_than(
            self._after, limit=self._limit + len(waiting), now_iso=now_iso, vcjob_after_seconds=vc_after)
        done = 0
        for job in jobs:
            jid = job["job_id"]
            if jid in waiting:
                continue
            if done >= self._limit:
                break
            done += 1
            failed = False
            for ref in self._targets(job):
                try:
                    self._exec.terminate(ref)
                    deleted += 1
                except Exception as exc:      # ref 별 격리 -- 나머지 ref·잡은 계속(일시·영구를 가를 수 없다)
                    failed = True
                    logger.warning("pod gc failed ref=%s: %s", ref, exc)
            if failed:
                _back_off(self._retry_at, jid, now)
            else:
                self._retry_at.pop(jid, None)
                self._repos.data_jobs.mark_pods_gc(jid, now_iso=now_iso)

        # 종단 빌드가 남긴 빌드 파드 + 프리플라이트 프로브 파드(슬라이스 21)를
        # 같은 창(after_seconds)으로 수거한다. 비종단 빌드는 절대 건드리지 않는다:
        # 빌드 파드가 사라지면 poll 이 FAILED 로 읽고, 프로브 파드가 사라지면
        # 워처 pending 루프가 프로브 실패로 오인해 멀쩡한 빌드를
        # build_preflight_failed 로 죽인다. terminate 는 ref 별로 격리한다 --
        # 한 파드의 실패가 나머지 수거를 막으면 안 된다(잡 파드 GC 와 같은 계약).
        # 완료 표시(builds.pods_gc_at)와 재시도 대기는 잡과 같은 규칙이다(모듈 docstring) -- 표시한 빌드는 질의에서 빠지고,
        # 대기 중인 빌드는 건너뛰는 만큼 더 읽어 뒤의 빌드가 굶지 않는다.
        if self._build_runner is not None:
            b_waiting = {b for b, t in self._build_retry_at.items() if t > now}
            b_done = 0
            for build in self._repos.builds.terminal_older_than(
                    self._after, limit=self._limit + len(b_waiting), now_iso=now_iso):
                bid = build["build_id"]
                if bid in b_waiting:
                    continue
                if b_done >= self._limit:
                    break
                b_done += 1
                failed = False
                for pod in (build_pod_name(bid), build_probe_pod_name(bid)):
                    ref = f"{BUILD_REF_PREFIX}/{pod}"
                    try:
                        self._build_runner.terminate(ref)
                        deleted += 1
                    except Exception as exc:
                        failed = True
                        logger.warning("build pod gc failed ref=%s: %s", ref, exc)
                if failed:
                    _back_off(self._build_retry_at, bid, now)
                else:
                    self._build_retry_at.pop(bid, None)
                    self._repos.builds.mark_pods_gc(bid, now_iso=now_iso)
        return {"deleted": deleted}


def _back_off(retry_at: dict, key: str, now: float) -> None:
    """key 를 RETRY_BACKOFF_SECONDS 뒤에 다시 보게 기억한다. 상한(_RETRY_MAX)을 넘으면 가장 먼저 다시 볼 것부터 잊는다
    (그것은 곧바로 다시 시도될 뿐이다)."""
    retry_at[key] = now + RETRY_BACKOFF_SECONDS
    if len(retry_at) > _RETRY_MAX:
        del retry_at[min(retry_at, key=retry_at.get)]
