"""컨트롤러 k8s-inventory 루프(dms-k8s 설계 §4.5, 2026-10-25) -- DB 전용(외부 k8s API 를 부르지 않는다).

한 틱(예산 TICK_BUDGET_SECONDS -- 루프 리스 max(10*3, 30)=30초 안):
  (c) 기한(업로드 유예 포함)이 지난 claimed run → expired(k8s_sync_expired) + 클러스터 요약·primary 대체 예약.
  (a) 클러스터마다 seq 가 가장 큰 received run 하나만 적용하고 더 오래된 received 는 superseded(payload 비움).
  (b) 적용 = repositories.k8s.K8sRepository.apply_run(diff 적용 + run applied + 클러스터 last_success·연속 실패 0 을 한
      트랜잭션). 예외면 롤백 후 별도 트랜잭션으로 failed(k8s_sync_apply_failed) -- 스냅숏은 이전 값 그대로.
  (d) 지운 클러스터의 스냅숏 행을 묶음 단위로 정리.
이벤트는 상태가 바뀔 때만(정상 → 실패: k8s_sync_failing, 실패 → 정상: k8s_sync_recovered), 커밋 뒤에(record_event 는 업무
트랜잭션 밖 단독 INSERT). 받는 쪽(API 라우트의 실패 보고)도 같은 함수(record_transition)로 남긴다.
"""
import sys
import time

from .db import iso_plus, utc_now_iso

TICK_BUDGET_SECONDS = 10.0
COMPONENT = "k8s-sync"


def record_failure_transition(repos, *, cluster, previous_failures, reason, detail=None) -> None:
    """정상(연속 실패 0) → 실패로 바뀐 순간에만 경고 이벤트. previous_failures None(설정이 바뀐 뒤의 옛 run 등)은 무시."""
    if previous_failures == 0:
        repos.observability.record_event(
            component=COMPONENT, severity="warning", event_type="k8s_sync_failing",
            message=f"{cluster}: {reason}" + (f" -- {detail}" if detail else ""),
            payload={"cluster": cluster, "reason": reason})


def run_inventory_once(repos, settings, *, now=None, budget_seconds=TICK_BUDGET_SECONDS,
                       clock=time.monotonic) -> dict:
    started = clock()
    now = now or utc_now_iso()
    k8s = repos.k8s
    stats = {"expired": 0, "applied": 0, "superseded": 0, "discarded": 0, "failed": 0, "cleaned": 0}
    for item in k8s.expire_overdue(now):
        stats["expired"] += 1
        record_failure_transition(repos, cluster=item["cluster"], previous_failures=item["previous_failures"],
                                  reason="k8s_sync_expired", detail=f"node {item['node_name']}")
    for cluster, runs in k8s.received_runs().items():
        if clock() - started > budget_seconds:
            break
        newest = runs[-1]
        for old in runs[:-1]:
            if k8s.supersede(old["run_id"]):
                stats["superseded"] += 1
        try:
            outcome = k8s.apply_run(newest["run_id"], now=now)
        except Exception as exc:  # noqa: BLE001 -- 한 클러스터의 적용 실패가 루프를 멈추지 않는다
            print(f"k8s-inventory apply failed cluster={cluster}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed = k8s.fail_apply(newest["run_id"], error=f"{type(exc).__name__}: {exc}", now=now)
            stats["failed"] += 1
            if failed is not None:
                record_failure_transition(repos, cluster=cluster, previous_failures=failed["previous_failures"],
                                          reason="k8s_sync_apply_failed", detail=type(exc).__name__)
            continue
        kind = outcome["outcome"]
        if kind in stats:
            stats[kind] += 1
        if kind == "applied" and (outcome.get("previous_failures") or 0) > 0:
            repos.observability.record_event(
                component=COMPONENT, severity="info", event_type="k8s_sync_recovered",
                message=f"{cluster}: synchronized again after {outcome['previous_failures']} failure(s)",
                payload={"cluster": cluster})
    if clock() - started <= budget_seconds:
        stats["cleaned"] = k8s.cleanup_orphans()
    return stats


def prune_runs_once(repos, *, retention_days: int, now_iso=None) -> int:
    """보존 정리(retention 루프): 보존일보다 오래된 종단 run. 클러스터별 최근 50건·마지막 성공 run 은 남긴다."""
    now = now_iso or utc_now_iso()
    return repos.k8s.prune_runs(iso_plus(now, -retention_days * 86400))
