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


def test_backslash_escape_is_not_double_interpreted():
    # \134 처리 순서가 바뀌면 "\134040"이 스페이스로 이중 해석된다 — 현재 순서 고정
    line = "50 22 0:35 / /mnt/bs\\134040dir rw - ext4 /dev/sdc rw\n"
    points = parse_mountinfo(line)
    assert "/mnt/bs\\040dir" in points  # 리터럴 백슬래시 + '040dir' (스페이스 아님)


def test_short_and_blank_lines_are_skipped():
    text = "\n1 2 0:3\nmalformed\n22 1 0:20 / /ok rw - ext4 /dev/root rw\n"
    assert parse_mountinfo(text) == {"/ok"}
