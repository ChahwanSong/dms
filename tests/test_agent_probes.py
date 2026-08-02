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
