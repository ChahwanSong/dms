"""controller 숙주: 재시작 가능한 run_once 루프들을 리더 리스 아래에서 반복 실행."""
import sys
import time
from dataclasses import dataclass
from typing import Callable

from .config import Settings
from .reconciler import reconcile_storages_once
from .repositories import Repositories
from .retention import prune_agent_reports_once


@dataclass
class Loop:
    name: str
    interval_seconds: int
    fn: Callable[[], object]


def build_loops(settings: Settings, repos: Repositories) -> list[Loop]:
    return [
        Loop("storage-reconciler", settings.reconcile_interval_seconds,
             lambda: reconcile_storages_once(
                 repos, stale_seconds=settings.agent_report_stale_seconds)),
        Loop("retention", settings.retention_interval_seconds,
             lambda: prune_agent_reports_once(
                 repos, retention_days=settings.agent_report_retention_days)),
    ]


def run_all_once(loops: list[Loop], repos: Repositories, holder: str) -> dict[str, str]:
    results: dict[str, str] = {}
    for loop in loops:
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
                *, sleep=time.sleep) -> None:
    loops = build_loops(settings, repos)
    next_due = {loop.name: 0.0 for loop in loops}
    while True:
        now = time.monotonic()
        for loop in loops:
            if now < next_due[loop.name]:
                continue
            run_all_once([loop], repos, holder)
            next_due[loop.name] = now + loop.interval_seconds
        sleep(1)
