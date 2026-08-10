import os
from dms.agent.probes import parse_mountinfo, probe_mounts, probe_identities, probe_os_metrics, probe_tools

MOUNTINFO = """\
22 1 0:20 / / rw,relatime - ext4 /dev/root rw
40 22 0:33 / /mnt/ceph rw,relatime - ceph 10.0.0.1:/ rw
41 22 0:34 / /mnt/with\\040space rw - ext4 /dev/sdb rw
"""

STORAGES = [
    {"storage_name": "ceph-a", "mount_path": "/mnt/ceph"},
    {"storage_name": "gone", "mount_path": "/mnt/gone"},
]


def test_parse_mountinfo_extracts_mountpoints_and_unescapes():
    points = parse_mountinfo(MOUNTINFO)
    assert "/mnt/ceph" in points
    assert "/mnt/with space" in points
    assert "/" in points


def test_probe_mounts_ready_and_missing():
    def isdir(path):
        return path == "/mnt/ceph"

    def access(path, mode):
        return path == "/mnt/ceph"

    out = probe_mounts(STORAGES, mountinfo_text=MOUNTINFO, isdir=isdir, access=access)
    ready = out[0]
    assert ready["storage_name"] == "ceph-a" and ready["status"] == "Ready"
    assert ready["is_mountpoint"] and ready["readable"] and ready["writable"]
    missing = out[1]
    assert missing["status"] == "Missing" and missing["reason"] == "missing_mount_path"


def test_probe_mounts_not_a_mountpoint_and_not_readable():
    out = probe_mounts(
        [{"storage_name": "s", "mount_path": "/plain/dir"}],
        mountinfo_text=MOUNTINFO, isdir=lambda p: True, access=lambda p, m: True)
    assert out[0]["status"] == "Missing" and out[0]["reason"] == "not_a_mountpoint"

    def no_read(path, mode):
        return mode != os.R_OK

    out = probe_mounts(
        [{"storage_name": "s", "mount_path": "/mnt/ceph"}],
        mountinfo_text=MOUNTINFO, isdir=lambda p: True, access=no_read)
    assert out[0]["status"] == "Missing" and out[0]["reason"] == "not_readable"


def test_backslash_escape_is_not_double_interpreted():
    # \134 처리 순서가 바뀌면 "\134040"이 스페이스로 이중 해석된다 — 현재 순서 고정
    line = "50 22 0:35 / /mnt/bs\\134040dir rw - ext4 /dev/sdc rw\n"
    points = parse_mountinfo(line)
    assert "/mnt/bs\\040dir" in points  # 리터럴 백슬래시 + '040dir' (스페이스 아님)


def test_short_and_blank_lines_are_skipped():
    text = "\n1 2 0:3\nmalformed\n22 1 0:20 / /ok rw - ext4 /dev/root rw\n"
    assert parse_mountinfo(text) == {"/ok"}


LOADAVG = "1.50 0.75 0.30 2/345 6789\n"
MEMINFO = "MemTotal:       16384000 kB\nMemFree:  1000 kB\nMemAvailable:    8192000 kB\n"
NETDEV = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  999999    100    0    0    0     0          0         0   999999    100    0    0    0     0       0          0
  eth0: 1000    10    0    0    0     0          0         0   2000    20    0    0    0     0       0          0
  eth1: 500    5    0    0    0     0          0         0   700    7    0    0    0     0       0          0
"""


# CNI 가 붙은 실제 노드 모양(dms-w3 실측 축약): 물리 eth0 + 오버레이 터널
# cilium_vxlan + 파드 veth 호스트쪽 lxc_*. 이름 폭이 6칸을 넘으면 왼쪽 여백이
# 사라지는 것까지 실물 그대로다 -- 파서가 name.strip() 에 기대는 지점이다.
NETDEV_CNI = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 999999     100    0    0    0     0          0         0   999999     100    0    0    0     0       0          0
  eth0:   1000      10    0    0    0     0          0         0     2000      20    0    0    0     0       0          0
cilium_vxlan:    400   4    0    0    0     0          0        0      600       6    0    0    0     0       0          0
lxc_abc:  70          7    0    0    0     0          0        0       90       9    0    0    0     0       0          0
"""
FILES_CNI = {"/proc/loadavg": LOADAVG, "/proc/meminfo": MEMINFO,
             "/host/proc/1/net/dev": NETDEV_CNI}


def _virtual_net_dir(tmp_path, names):
    """커널이 가상 인터페이스를 등록하는 /sys/devices/virtual/net/<name> 모사."""
    path = tmp_path / "virtual-net"
    path.mkdir()
    for name in names:
        (path / name).mkdir()
    return str(path)


def _net(**kwargs):
    out = probe_os_metrics([], read_text=lambda p: FILES_CNI[p],
                           statvfs=lambda p: None,
                           net_dev_path="/host/proc/1/net/dev", **kwargs)
    return out["network_rx_bytes"], out["network_tx_bytes"]


def test_probe_os_metrics_sums_only_physical_interfaces_when_configured(tmp_path):
    # 판별은 이름이 아니라 커널 등록 위치다(설계 §2.6): /proc/net/dev 에 있는데
    # /sys/devices/virtual/net/ 에 없는 것만 물리다. 여기서는 eth0 만 남아야 한다 --
    # cilium_vxlan 은 eth0 과 이중 계상이고 lxc_* 는 파드 veth 라 노드 처리량이 아니다.
    rx, tx = _net(virtual_net_path=_virtual_net_dir(
        tmp_path, ["lo", "cilium_vxlan", "lxc_abc"]))
    assert (rx, tx) == (1000, 2000)


def test_probe_os_metrics_without_virtual_net_path_sums_all_but_lo():
    # 미설정이면 기존 동작 그대로 -- 필터는 명시적으로 설정된 경우에만 켠다.
    assert _net() == (1000 + 400 + 70, 2000 + 600 + 90)


def test_probe_os_metrics_unreadable_virtual_net_path_falls_back_to_all_but_lo(tmp_path):
    # 설정됐지만 마운트가 없거나 못 읽는 배포에서도 지표는 살아 있어야 한다 --
    # None 이나 0 으로 무너지면 대시보드가 "네트워크 없음"으로 거짓말을 한다.
    missing = str(tmp_path / "nope")
    assert _net(virtual_net_path=missing) == (1470, 2690)
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("")
    assert _net(virtual_net_path=str(not_a_dir)) == (1470, 2690)


def test_unset_virtual_net_path_cannot_exclude_host_eth0_via_pod_sysfs(tmp_path):
    """함정 못박기: 파드 자신의 sysfs 에는 파드 인터페이스인 eth0 이 들어 있다.

    기본값이 /sys/devices/virtual/net 이었다면 마운트 없는 배포에서 파드의 sysfs 를
    읽어 **호스트의 물리 eth0 을 가상으로 오판해 제외**한다 -- 고치려던 것보다 나쁜
    값이다(설계 §2.6). 그래서 기본은 필터 없음이어야 한다. 아래 첫 단언이 그 계약이고,
    둘째는 "그런 디렉터리를 실제로 읽히면 eth0 이 빠진다"는 전제를 함께 고정해
    첫 단언이 공허하지 않음을 보인다.
    """
    pod_sysfs = _virtual_net_dir(tmp_path, ["lo", "eth0"])   # 파드 안에서 본 모습
    assert _net() == (1470, 2690)                            # 미설정 -> eth0 살아 있다
    assert _net(virtual_net_path=pod_sysfs) == (400 + 70, 600 + 90)


def test_probe_tools_found_and_missing():
    def which(name):
        return f"/opt/bin/{name}" if name != "nsync" else None

    class Proc:
        stdout = "dsync 0.12-dms\nextra"
        stderr = ""

    out = probe_tools(["dsync", "nsync"], which=which, run=lambda *a, **k: Proc())
    assert out[0] == {"name": "dsync", "status": "Ready", "path": "/opt/bin/dsync",
                      "version": "dsync 0.12-dms", "reason": None}
    assert out[1]["status"] == "Missing" and out[1]["reason"] == "tool_not_found"


def test_probe_tools_version_failure_is_soft():
    def boom(*a, **k):
        raise OSError("exec failed")

    out = probe_tools(["drm"], which=lambda n: "/opt/bin/drm", run=boom)
    assert out[0]["status"] == "Ready" and out[0]["version"] is None
    assert out[0]["reason"].startswith("version_probe_failed:")


def test_probe_identities():
    class Pw:
        pw_uid, pw_gid = 1000, 1000

    class Gr:
        def __init__(self, name, members):
            self.gr_name, self.gr_mem = name, members

    def getpwnam(name):
        if name == "alice":
            return Pw()
        raise KeyError(name)

    out = probe_identities(["alice", "ghost"], getpwnam=getpwnam,
                           getgrall=lambda: [Gr("dev", ["alice"]), Gr("ops", [])])
    assert out[0] == {"username": "alice", "status": "Ready", "uid": 1000,
                      "gid": 1000, "groups": ["dev"]}
    assert out[1]["status"] == "Missing" and out[1]["reason"] == "user_not_found"


def test_probe_os_metrics_with_failures_are_soft():
    files = {"/proc/loadavg": LOADAVG, "/proc/meminfo": MEMINFO, "/proc/net/dev": NETDEV}

    def read_text(path):
        return files[path]

    class Vfs:
        f_frsize, f_blocks, f_bavail = 4096, 1000, 250

    out = probe_os_metrics([{"storage_name": "s", "mount_path": "/mnt/ceph"}],
                           read_text=read_text, statvfs=lambda p: Vfs())
    assert out["load1"] == 1.50 and out["load15"] == 0.30
    assert out["memory_total_kb"] == 16384000
    assert out["memory_available_kb"] == 8192000
    assert out["disks"] == [{"storage_name": "s", "total_bytes": 4096000,
                             "used_bytes": 3072000}]
    assert out["network_rx_bytes"] == 1500 and out["network_tx_bytes"] == 2700

    def broken(path):
        raise OSError("no proc")

    out = probe_os_metrics([], read_text=broken, statvfs=lambda p: Vfs())
    assert out["load1"] is None and out["memory_total_kb"] is None
    assert out["network_rx_bytes"] is None and out["disks"] == []


def test_probe_os_metrics_reads_injected_net_dev_path():
    # /proc/net/* 는 netns 범위라 파드 기본 경로는 veth 값이다(설계 §2.5) --
    # DaemonSet 이 마운트하는 호스트 netns 경로가 실제로 읽혀야 한다. files 에 기본
    # 경로를 안 넣어, 주입이 무시되면 KeyError -> fail-soft None 으로 단언이 깨진다.
    files = {"/proc/loadavg": LOADAVG, "/proc/meminfo": MEMINFO,
             "/host/proc/1/net/dev": NETDEV}
    out = probe_os_metrics([], read_text=lambda p: files[p],
                           statvfs=lambda p: None,
                           net_dev_path="/host/proc/1/net/dev")
    assert out["network_rx_bytes"] == 1500 and out["network_tx_bytes"] == 2700
