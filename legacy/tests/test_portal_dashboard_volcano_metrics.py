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


def test_offenders_pending_and_running():
    jobs = [
        _job("pend-old", phase="Pending", created_at=_iso(3600)),
        _job("pend-new", phase="Pending", created_at=_iso(10)),
        _job("long-run", phase="Completed", created_at=_iso(420),
             started_at=_iso(400), finished_at=_iso(100), requester="bob", tool="dsync"),
        _job("short-run", phase="Completed", created_at=_iso(180),
             started_at=_iso(160), finished_at=_iso(100)),
        _job("active", phase="Running", created_at=_iso(60), started_at=_iso(50)),
    ]
    top = _volcano_metrics(jobs, NOW)["top"]

    # longest pending = still-queued jobs by wait time
    assert [p["name"] for p in top["longest_pending"]][:2] == ["pend-old", "pend-new"]
    assert top["longest_pending"][0]["pending_s"] >= 3590

    # longest running = run duration (completed) / elapsed (still running), desc
    run = top["longest_running"]
    assert [r["name"] for r in run] == ["long-run", "short-run", "active"]
    assert run[0]["running_s"] == 300.0          # finished(-100) - started(-400)
    assert run[0]["requester"] == "bob"          # detail field flows through
    assert run[2]["active"] is True              # still-running job flagged
    assert run[0]["active"] is False

    assert "most_resources" not in top and "most_failed" not in top
