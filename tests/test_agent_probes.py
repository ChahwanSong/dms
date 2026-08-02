import os
from dms.agent.probes import parse_mountinfo, probe_mounts

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
