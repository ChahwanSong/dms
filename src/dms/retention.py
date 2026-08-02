"""retention: agent_reports 이력을 보존 기간 밖에서 배치 삭제. correctness가 아니라 최적화."""
from .db import iso_plus, utc_now_iso
from .repositories import Repositories


def prune_agent_reports_once(repos: Repositories, *, retention_days: int,
                             now_iso: str | None = None,
                             batch_size: int = 5000) -> int:
    now = now_iso or utc_now_iso()
    cutoff = iso_plus(now, -retention_days * 86400)
    return repos.agents.prune_reports(cutoff, batch_size=batch_size)
