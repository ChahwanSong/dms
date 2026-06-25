from __future__ import annotations

from dms.agent_daemon import probe_os_metrics


def _write_proc(d):
    (d / "loadavg").write_text("0.50 0.40 0.30 1/231 1234\n")
    (d / "meminfo").write_text(
        "MemTotal:        2000000 kB\nMemFree:          400000 kB\n"
        "MemAvailable:     800000 kB\nBuffers:           10000 kB\n"
    )
    (d / "stat").write_text("cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 100 0 100 800\n")
    return d


def test_probe_parses_load_and_memory(tmp_path):
    proc = _write_proc(tmp_path)
    m = probe_os_metrics(proc_path=str(proc))
    assert m["load"] == {"load1": 0.5, "load5": 0.4, "load15": 0.3}
    # used = total - available = 2,000,000 - 800,000 = 1,200,000 → 60%
    assert m["memory"]["total_kb"] == 2_000_000
    assert m["memory"]["available_kb"] == 800_000
    assert m["memory"]["used_pct"] == 60.0


def test_probe_cpu_omitted_when_stat_static(tmp_path):
    # two samples of an unchanging /proc/stat → no delta → cpu omitted (not a crash)
    proc = _write_proc(tmp_path)
    m = probe_os_metrics(proc_path=str(proc))
    assert "cpu" not in m


def test_probe_disk_from_host_root(tmp_path):
    m = probe_os_metrics(proc_path=str(_write_proc(tmp_path)), host_root=str(tmp_path))
    assert "disk" in m
    assert m["disk"]["used_pct"] is not None
    assert m["disk"]["total_gb"] > 0


def test_probe_disk_skipped_without_host_root(tmp_path):
    m = probe_os_metrics(proc_path=str(_write_proc(tmp_path)))
    assert "disk" not in m


def test_probe_fail_soft_on_missing_proc(tmp_path):
    missing = tmp_path / "nope"
    # no proc files, no host_root → every metric fails soft → empty dict, no exception
    assert probe_os_metrics(proc_path=str(missing)) == {}
