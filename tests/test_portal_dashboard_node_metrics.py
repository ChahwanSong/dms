"""BFF _node_metrics — group flat agent metric samples into per-node time-series.

A node emits repeated reports for the same host and can produce more than one sample
in a minute; the helper buckets per minute (latest sample wins) and exposes cpu/mem
series + a current snapshot."""

from __future__ import annotations

from portal.backend.routers.dashboard import _node_metrics


def _s(node, reported_at, cpu, mem, cores=8):
    # every agent metric sample carries the single worker role, DM
    return {
        "cluster_name": "c", "node_name": node, "worker_role": "DM",
        "reported_at": reported_at, "cpu_percent": cpu, "mem_used_pct": mem,
        "cpu_cores": cores, "mem_total_kb": 1000, "load1": 1.0, "disk_used_pct": 50.0,
    }


def test_node_metrics_buckets_per_minute_and_current():
    samples = [
        _s("w1", "2026-06-28T06:00:10+00:00", 40.0, 60.0),
        _s("w1", "2026-06-28T06:00:40+00:00", 44.0, 62.0),  # same minute, later
        _s("w1", "2026-06-28T06:01:10+00:00", 50.0, 65.0),
        _s("w2", "2026-06-28T06:01:00+00:00", 10.0, 20.0, cores=4),
    ]
    nodes = _node_metrics(samples)
    assert [n["node_name"] for n in nodes] == ["w1", "w2"]  # sorted

    w1 = nodes[0]
    # w1's 3 samples collapse to 2 points: the 06:00 bucket keeps the later
    # (06:00:40) sample, the 06:01 bucket = 06:01:10
    assert len(w1["cpu_series"]) == 2
    assert [p["v"] for p in w1["cpu_series"]] == [44.0, 50.0]
    assert [p["v"] for p in w1["mem_series"]] == [62.0, 65.0]
    assert w1["current"]["cpu_percent"] == 50.0
    assert w1["current"]["reported_at"] == "2026-06-28T06:01:10+00:00"
    assert w1["current"]["cpu_cores"] == 8

    w2 = nodes[1]
    assert len(w2["cpu_series"]) == 1
    assert w2["current"]["cpu_percent"] == 10.0
    assert w2["current"]["cpu_cores"] == 4


def test_node_metrics_empty():
    assert _node_metrics([]) == []
    assert _node_metrics(None) == []
