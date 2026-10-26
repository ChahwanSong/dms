"""controller 숙주: 재시작 가능한 run_once 루프들을 리더 리스 아래에서 반복 실행."""
import sys
import time
from dataclasses import dataclass
from typing import Callable

from .artifact_base import controller_check_once
from .artifact_expiry import INTERVAL_SECONDS as ARTIFACT_EXPIRY_INTERVAL_SECONDS, ArtifactExpirer
from .batch_orchestrator import BatchOrchestrator
from .build_watcher import BuildWatcher
from .config import Settings
from .db import utc_now_iso
from .domain import DataJobState
from .execution import StubExecutionAdapter
from .k8s_inventory import prune_runs_once, run_inventory_once
from .planner import Planner
from .pod_gc import PodGarbageCollector
from .reconciler import reconcile_storages_once
from .repositories import Repositories
from .request_purger import RequestPurger
from .retention import prune_agent_reports_once, prune_events_once
from .rollout_watcher import RolloutWatcher
from .stepper import JobStepper


@dataclass
class Loop:
    name: str
    interval_seconds: int
    fn: Callable[[], object]


def build_loops(settings: Settings, repos: Repositories, *,
                execution_adapter=None, build_runner=None,
                rollout_runner=None, purge_runner=None) -> list[Loop]:
    adapter = execution_adapter if execution_adapter is not None else StubExecutionAdapter()
    pod_gc = PodGarbageCollector(repos, adapter, after_seconds=settings.pod_gc_after_seconds,
                                 build_runner=build_runner, vcjob_ttl_seconds=settings.vcjob_ttl_seconds)

    def _stepper_step():
        JobStepper(repos, adapter, settings=settings).run_once()
        for job_id in repos.data_jobs.expire_previews(now_iso=utc_now_iso()):
            job = repos.data_jobs.get_job(job_id)
            repos.requests.finalize_from_job(
                job["request_id"], DataJobState.PREVIEW_EXPIRED,
                reason_code="preview_expired", actor="stepper")
        # 고아 복구: 잡은 터미널인데 request가 크래시 등으로 비터미널로 남은 경우.
        # finalize_from_job은 idempotent(이미 터미널이면 no-op)라 안전하게 재호출
        # 가능. 슬라이스 24 §2.3: 스윕은 오래된순 200건 상한(리포지토리 몫)이고,
        # 독 행 하나가 나머지 199건을 영구히 굶기지 않도록 행 단위로 격리한다 --
        # 실패 행은 이벤트로 남고 다음 틱에 멱등 재시도된다. 0건 스윕은 정상값이라
        # 아무것도 기록하지 않는다(0 을 실패로 위장하지 않는다).
        for orphan in repos.data_jobs.terminal_jobs_with_live_request():
            try:
                job = repos.data_jobs.get_job(orphan["job_id"])
                repos.requests.finalize_from_job(
                    orphan["request_id"], DataJobState(orphan["state"]),
                    reason_code="orphan_recovery",
                    summary=(job or {}).get("result_summary"), actor="stepper")
            except Exception as exc:
                repos.observability.record_event(
                    component="stepper", severity="error",
                    event_type="orphan_recovery_failed",
                    message=f"{type(exc).__name__}: {exc}"[:500],
                    request_id=orphan["request_id"])

    def _retention_step():
        # events는 보존일로 지운다(state_transitions 는 시간으로 지우지 않고 요청 삭제 때만 함께 지워진다 --
        # repositories/request_purges.py) -- 같은 성격의 배치 삭제이니 agent_reports 정리와 같은 루프에 묶는다
        # (새 루프를 만들지 않는다).
        prune_agent_reports_once(repos, retention_days=settings.agent_report_retention_days)
        prune_events_once(repos, retention_days=settings.event_retention_days)
        # k8s 동기화 이력(2026-10-25): 보존일보다 오래된 종단 run. 클러스터별 최근 50건·마지막 성공 run 은 남긴다.
        prune_runs_once(repos, retention_days=settings.k8s_sync_run_retention_days)

    loops = [
        Loop("planner", settings.planner_interval_seconds,
             lambda: Planner(repos, settings=settings).run_once()),
        Loop("job-stepper", settings.stepper_interval_seconds, _stepper_step),
        Loop("storage-reconciler", settings.reconcile_interval_seconds,
             lambda: reconcile_storages_once(
                 repos, stale_seconds=settings.agent_report_stale_seconds)),
        Loop("retention", settings.retention_interval_seconds, _retention_step),
        Loop("batch-orchestrator", settings.batch_orchestrator_interval_seconds,
             lambda: BatchOrchestrator(repos, settings=settings, execution_adapter=adapter).run_once()),
        Loop("pod-gc", settings.pod_gc_interval_seconds, pod_gc.run_once),
        # 슬라이스 18(설계 §2.4c): 컨트롤러 관점의 아티팩트 base 검증. 간격은 새
        # 설정 키를 만들지 않고 storage-reconciler 와 같은 값을 쓴다 -- 같은
        # "증거 신선도" 계열이고 운영자가 따로 튜닝할 값이 아니다.
        Loop("artifact-base-check", settings.reconcile_interval_seconds,
             lambda: controller_check_once(repos, settings)),
        # k8s 인벤토리(2026-10-25 dms-k8s 설계 §4.5, k8s_inventory.py): 에이전트가 올린 동기화 결과의 적용·기한 만료·지운
        # 클러스터 정리. DB 전용(외부 API 를 부르지 않는다) -- 틱 예산 10초 < 리스 max(10*3, 30)=30초.
        Loop("k8s-inventory", settings.k8s_inventory_interval_seconds,
             lambda: run_inventory_once(repos, settings)),
    ]
    # build_runner가 없으면(stub조차 배선되지 않은 기존 호출자) 루프를 아예 넣지 않는다 --
    # None을 넘겨 매 틱마다 AttributeError로 죽게 두지 않기 위해서다.
    if build_runner is not None:
        loops.append(Loop("build-watcher", settings.build_watcher_interval_seconds,
                          lambda: BuildWatcher(
                              repos, build_runner,
                              timeout_seconds=settings.build_timeout_seconds,
                              # 슬라이스 21 §2.5: 프로브 대기 상한 -- 이게 없으면
                              # Pending 빌드가 프로브 영구 오류 시 갇힌다.
                              preflight_timeout_seconds=(
                                  settings.build_preflight_timeout_seconds),
                          ).run_once()))
    # rollout_runner가 없으면(기존 호출자) 루프를 아예 넣지 않는다 --
    # build-watcher와 같은 하위호환 규칙이다.
    if rollout_runner is not None:
        loops.append(Loop("rollout-watcher", settings.rollout_interval_seconds,
                          lambda: RolloutWatcher(
                              repos, rollout_runner,
                              timeout_seconds=settings.rollout_timeout_seconds
                          ).run_once()))
    # 요청 삭제 정리(2026-10-08, request_purger.py): 삭제한 요청의 남은 파드·결과 파일. purge_runner 가 없으면
    # (기존 호출자) 넣지 않는다 -- build-watcher 와 같은 하위호환 규칙. 틱 예산 20s < 리스 max(15*3, 30)=45s.
    if purge_runner is not None:
        loops.append(Loop("request-purge", settings.request_purge_interval_seconds,
                          lambda: RequestPurger(repos, purge_runner, settings=settings).run_once()))
        # 결과 파일 자동 만료(2026-10-26, artifact_expiry.py): 고르고 표시하고 정리 아웃박스에 맡기기만 한다 -- 실제 정리는
        # 위 request-purge 루프다. 그래서 같은 조건(purge_runner)에서만 넣는다(정리 루프 없이 표시만 찍으면 열람은 410 인데
        # 파일은 영원히 남는다). DB 전용 -- 리스 max(300*3, 30)=900s.
        loops.append(Loop("artifact-expiry", ARTIFACT_EXPIRY_INTERVAL_SECONDS,
                          lambda: ArtifactExpirer(repos, settings=settings).run_once()))
    return loops


def run_all_once(loops: list[Loop], repos: Repositories, holder: str) -> dict[str, str]:
    results: dict[str, str] = {}
    for loop in loops:
        # 슬라이스 22 §2.5 규약: 리스 획득은 **의도적으로** per-loop try 밖이다.
        # 여기의 DB 죽음(transaction 의 BEGIN)은 재연결 + 1회 재시도(db.py §2.3)
        # 로 같은 틱에서 복구되고, 재연결조차 실패하는 지속 장애면 예외가 그대로
        # 전파돼 프로세스가 죽는다 -- 컨트롤러엔 HTTP 헬스가 없으므로 이
        # crash-restart 가 api 자기 종료(§2.4)의 동등물이다. try 안으로 옮기는
        # 리팩터링 금지: 옮기면 지속 장애가 "error 결과로 접혀 조용히 도는
        # 정지"가 된다(test_persistent_lease_death_still_crashes_the_controller
        # 가 이 규약의 집행부다).
        acquired = repos.control.try_acquire_lease(
            f"loop:{loop.name}", holder,
            lease_seconds=max(loop.interval_seconds * 3, 30))
        if not acquired:
            results[loop.name] = "skipped_lease"
            continue
        try:
            loop.fn()
            results[loop.name] = "ok"
        except Exception as exc:  # 한 루프의 실패가 다른 루프를 죽이지 않는다
            print(f"loop {loop.name} failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            results[loop.name] = f"error:{type(exc).__name__}"
    return results


def run_forever(settings: Settings, repos: Repositories, holder: str,
                *, sleep=time.sleep, execution_adapter=None,
                build_runner=None, rollout_runner=None, purge_runner=None) -> None:
    loops = build_loops(settings, repos,
                        execution_adapter=execution_adapter, build_runner=build_runner,
                        rollout_runner=rollout_runner, purge_runner=purge_runner)
    next_due = {loop.name: 0.0 for loop in loops}
    while True:
        now = time.monotonic()
        for loop in loops:
            if now < next_due[loop.name]:
                continue
            run_all_once([loop], repos, holder)
            next_due[loop.name] = now + loop.interval_seconds
        sleep(1)
