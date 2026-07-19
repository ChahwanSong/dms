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


# --- network counters (node NIC totals via host PID 1's netns) ---------------

_NETDEV_HEADER = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes"
    "    packets errs drop fifo colls carrier compressed\n"
)


def _netdev(rows: list[tuple[str, int, int]]) -> str:
    lines = [
        f"{name}: {rx} 10 0 0 0 0 0 0 {tx} 10 0 0 0 0 0 0\n" for name, rx, tx in rows
    ]
    return _NETDEV_HEADER + "".join(lines)


def test_network_prefers_host_pid1_netns(tmp_path):
    # /proc/net is a self/net symlink, so the plain path sees the POD's netns; the
    # probe must prefer /proc/1/net/dev (host init = node root netns) when present.
    _write_proc(tmp_path)
    host1 = tmp_path / "proc" / "1" / "net"
    host1.mkdir(parents=True)
    (host1 / "dev").write_text(
        _netdev([("lo", 999, 999), ("eth0", 1000, 500), ("eno2", 30, 20),
                 ("cilium_vxlan", 7777, 7777), ("lxcabc", 5555, 5555)])
    )
    pod = tmp_path / "proc" / "net"
    pod.mkdir(parents=True)
    (pod / "dev").write_text(_netdev([("eth0", 1, 2)]))  # pod-netns decoy
    m = probe_os_metrics(proc_path=str(tmp_path), host_root=str(tmp_path))
    # physical eth0+eno2 summed; lo/cilium/lxc skipped; pod decoy ignored.
    assert m["network"] == {"rx_bytes": 1030, "tx_bytes": 520}


def test_network_falls_back_to_plain_proc(tmp_path):
    # no /proc/1/net/dev through the host mount → degrade to the plain path
    # (agent's own netns) rather than dropping the metric entirely.
    _write_proc(tmp_path)
    pod = tmp_path / "proc" / "net"
    pod.mkdir(parents=True)
    (pod / "dev").write_text(_netdev([("eth0", 42, 24), ("veth1", 9, 9)]))
    m = probe_os_metrics(proc_path=str(tmp_path), host_root=str(tmp_path))
    assert m["network"] == {"rx_bytes": 42, "tx_bytes": 24}


def test_network_skipped_without_host_root(tmp_path):
    m = probe_os_metrics(proc_path=str(_write_proc(tmp_path)))
    assert "network" not in m
