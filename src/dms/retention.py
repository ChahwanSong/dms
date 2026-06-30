"""Age-based retention for the ``agent_reports`` history table.

At 100+ worker nodes each reporting roughly once a minute, ``agent_reports`` grows by
hundreds of thousands of rows per day into the millions. Node-health reads now go through
``agent_node_current`` (the denormalized latest report per node, written transactionally
on every ingest), so the history table is needed only for the node-metrics time-series
window — which makes it safe to trim purely by age.

This mirrors ``sanity_reconciler.py``: a single ``*_once`` entrypoint driven by the
``dms retention --loop`` CLI command, fault-isolated, writing a heartbeat each cycle for a
k8s liveness probe. It is an OPTIMISATION (bounding table size / index bloat), not a
correctness dependency: if it stops, reads keep working (just over a larger table) until
it is restarted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .repositories import DmsRepository

RETENTION_COMPONENT = "agent-report-retention"


def prune_agent_reports_once(
    repository: DmsRepository,
    *,
    retention_seconds: int,
    batch_size: int = 5000,
    heartbeat_path: str | None = None,
    observability: Any | None = None,
) -> int:
    """Prune agent_reports older than ``retention_seconds`` in one pass. Returns the
    number of rows deleted. Fault-isolated: a failure is recorded (best-effort) and the
    heartbeat is still written so the liveness probe reflects that the loop ran."""
    cutoff = (datetime.now(UTC) - timedelta(seconds=retention_seconds)).isoformat()
    deleted = 0
    error: str | None = None
    try:
        deleted = repository.prune_agent_reports(
            older_than_iso=cutoff, batch_size=batch_size
        )
    except Exception as exc:  # noqa: BLE001 - long-running loop must survive one failure.
        error = str(exc)
        if observability is not None:
            observability.safe_record_event(
                component=RETENTION_COMPONENT,
                severity="WARN",
                event_type="agent_report_retention_failed",
                message="agent report retention prune failed",
                payload={"cutoff": cutoff, "error": error},
            )
    else:
        if deleted and observability is not None:
            observability.safe_record_event(
                component=RETENTION_COMPONENT,
                severity="INFO",
                event_type="agent_report_retention_pruned",
                message=f"pruned {deleted} agent reports older than {cutoff}",
                payload={"cutoff": cutoff, "deleted": deleted},
            )
    _write_heartbeat(heartbeat_path, deleted=deleted, cutoff=cutoff, error=error)
    return deleted


def _write_heartbeat(
    path: str | None, *, deleted: int, cutoff: str, error: str | None = None
) -> None:
    """Write a liveness heartbeat each cycle. A k8s liveness probe reads its mtime/age;
    if the loop hangs the heartbeat goes stale and k8s restarts the pod."""
    if not path:
        return
    try:
        Path(path).write_text(
            json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "deleted": deleted,
                    "cutoff": cutoff,
                    "error": error,
                }
            )
        )
    except OSError:
        pass
