"""DmsRepository.list_agent_metric_samples — per-node OS-metric time-series for the
node-workload dashboard. Verifies the time window filter, os_metrics extraction, and
chronological ordering against the agent_reports history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dms.db import Database
from dms.domain import WorkerRole
from dms.migrations import migrate_all
from dms.repositories import DmsRepository


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _report(node: str, reported_at: str, cpu, mem, *, os_metrics=True) -> dict:
    rep = {
        "schema_version": "phase9.v1",
        "reported_at": reported_at,
        "cluster_name": "cluster-a",
        "node_name": node,
        "node_uid": f"uid-{node}",
        "worker_role": WorkerRole.DM.value,
        "mounts": [],
        "csi": [],
        "tools": [],
    }
    if os_metrics:
        rep["os_metrics"] = {
            "cpu": {"percent": cpu, "cores": 8},
            "memory": {"used_pct": mem, "total_kb": 32_000_000},
            "load": {"load1": 1.5},
            "disk": {"used_pct": 55.0, "total_gb": 200.0},
        }
    return rep


def _repo(tmp_path) -> DmsRepository:
    op = Database(f"sqlite:///{tmp_path / 'op.db'}")
    obs = Database(f"sqlite:///{tmp_path / 'obs.db'}")
    migrate_all(op, obs)
    return DmsRepository(op)


def test_metric_samples_window_and_fields(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    repo.ingest_agent_report(_report("w1", _iso(now - timedelta(minutes=2)), 40.0, 60.0))
    repo.ingest_agent_report(_report("w1", _iso(now - timedelta(minutes=1)), 42.0, 61.0))
    repo.ingest_agent_report(_report("w2", _iso(now - timedelta(minutes=1)), 78.0, 55.0))
    repo.ingest_agent_report(_report("w1", _iso(now - timedelta(hours=10)), 99.0, 99.0))

    samples = repo.list_agent_metric_samples(since_iso=_iso(now - timedelta(hours=6)))

    # the 10h-old report is outside the 6h window
    assert len(samples) == 3
    # oldest -> newest
    times = [s["reported_at"] for s in samples]
    assert times == sorted(times)
    # extracted fields for w1's latest
    w1 = [s for s in samples if s["node_name"] == "w1"]
    assert len(w1) == 2
    latest = max(w1, key=lambda s: s["reported_at"])
    assert latest["cpu_percent"] == 42.0
    assert latest["cpu_cores"] == 8
    assert latest["mem_used_pct"] == 61.0
    assert latest["mem_total_kb"] == 32_000_000
    assert latest["load1"] == 1.5
    assert latest["disk_used_pct"] == 55.0
    assert latest["worker_role"] == "DM"


def test_metric_samples_missing_os_metrics_yields_nulls(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    repo.ingest_agent_report(
        _report("w3", _iso(now - timedelta(minutes=1)), 0, 0, os_metrics=False)
    )
    samples = repo.list_agent_metric_samples(since_iso=_iso(now - timedelta(hours=1)))
    assert len(samples) == 1
    assert samples[0]["node_name"] == "w3"
    assert samples[0]["cpu_percent"] is None
    assert samples[0]["mem_used_pct"] is None
