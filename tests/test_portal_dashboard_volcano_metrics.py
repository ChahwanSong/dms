"""BFF _volcano_metrics — windowed throughput/latency + top offenders from the per-job
Volcano snapshot."""

from __future__ import annotations

from datetime import datetime, timezone

from portal.backend.routers.dashboard import _volcano_metrics

NOW = datetime(2026, 6, 28, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def _iso(ago_s: float) -> str:
    return datetime.fromtimestamp(NOW - ago_s, tz=timezone.utc).isoformat()


def _job(name: str, **kw):
    base = {
        "name": name, "queue": "dms-data", "phase": "Completed",
        "created_at": None, "pod_created_at": None, "scheduled_at": None,
        "started_at": None, "finished_at": None,
        "running": 0, "pending": 0, "succeeded": 0, "failed": 0,
        "req_cpu_cores": 0.0, "req_mem_bytes": 0, "req_pods": 0,
        "latencies": {
            "job_to_pod_s": None, "pod_to_sched_s": None,
            "sched_to_start_s": None, "run_s": None,
        },
    }
    base.update(kw)
    return base


def test_throughput_and_latency_windows():
    jobs = [
        _job("a", phase="Completed", finished_at=_iso(600), created_at=_iso(800),
             latencies={"job_to_pod_s": 2.0, "pod_to_sched_s": 4.0,
                        "sched_to_start_s": 3.0, "run_s": 100.0}),
        _job("b", phase="Failed", finished_at=_iso(7200), created_at=_iso(7400),
             latencies={"job_to_pod_s": 6.0, "pod_to_sched_s": 8.0,
                        "sched_to_start_s": 5.0, "run_s": 300.0}),
    ]
    out = _volcano_metrics(jobs, NOW)

    w1 = out["windows"]["1h"]
    assert w1["throughput"] == {"completed": 1, "succeeded": 1, "failed": 0}
    assert w1["latency"]["run_s"]["n"] == 1
    assert w1["latency"]["run_s"]["mean"] == 100.0

    w6 = out["windows"]["6h"]
    assert w6["throughput"] == {"completed": 2, "succeeded": 1, "failed": 1}
    assert w6["latency"]["run_s"]["n"] == 2
    assert w6["latency"]["run_s"]["mean"] == 200.0   # (100+300)/2
    assert w6["latency"]["run_s"]["p50"] == 200.0    # interp([100,300], 50)
    # the new scheduled->start stage (image pull/create) flows through aggregation
    assert w6["latency"]["sched_to_start_s"]["n"] == 2
    assert w6["latency"]["sched_to_start_s"]["mean"] == 4.0   # (3+5)/2


def test_top_offenders():
    jobs = [
        _job("pend-old", phase="Pending", created_at=_iso(3600)),
        _job("pend-new", phase="Pending", created_at=_iso(10)),
        _job("big", finished_at=_iso(60), created_at=_iso(120),
             req_cpu_cores=8.0, req_mem_bytes=999, req_pods=4),
        _job("small", finished_at=_iso(60), created_at=_iso(120),
             req_cpu_cores=1.0, req_mem_bytes=1, req_pods=1),
    ]
    top = _volcano_metrics(jobs, NOW)["top"]
    assert [p["name"] for p in top["longest_pending"]][:2] == ["pend-old", "pend-new"]
    assert top["longest_pending"][0]["pending_s"] >= 3590
    assert "most_failed" not in top
    assert top["most_resources"][0]["name"] == "big"
    assert top["most_resources"][0]["cpu_cores"] == 8.0
