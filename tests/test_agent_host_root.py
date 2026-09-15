"""에이전트 호스트 루트 프로브(방안 A, 2026-09-16).

DaemonSet 이 호스트 `/` 를 읽기 전용·HostToContainer 로 /host/root 에 한 번만 붙이고
프로브가 mount_path 를 그 접두로 번역한다 -- 스토리지마다 hostPath 를 손으로 나열하던
관행(등록만 하면 Missing) 을 없앤다. 함정 두 개를 여기서 고정한다:
  1. writable 은 mountinfo 옵션(rw/ro)에서 -- ro 바인드 아래 W_OK 는 항상 거짓이라
     access 를 쓰면 placement(require_writable) 가 sync 목적지를 전부 배제한다.
  2. 전파 자가 진단 -- 호스트엔 마운트포인트인데 /host/root 아래에 안 보이면
     propagation_stale(호스트 `/` 가 shared 가 아니거나 전파 없음)."""
import os

from dms.agent.probes import (host_path, parse_mount_table, probe_artifact_base,
                              probe_mounts, probe_os_metrics)
from dms.agent.runner import build_report
from dms.config import AgentSettings

HOST_MI = """\
22 1 0:20 / / rw,relatime shared:1 - ext4 /dev/root rw
40 22 0:33 / /mgmt_storage rw,relatime shared:2 - ceph 10.0.0.1:/ rw
41 22 0:34 / /archive ro,relatime shared:3 - nfs4 nas:/a rw
42 22 0:35 / /sbro rw,relatime shared:4 - xfs /dev/sdb ro
"""
# 에이전트 자신의 mountinfo: 호스트 `/` 가 /host/root 로 rbind 돼 하위 마운트가 따라옴
SELF_MI = """\
90 80 0:20 / /host/root ro,relatime - ext4 /dev/root rw
91 90 0:33 / /host/root/mgmt_storage rw,relatime - ceph 10.0.0.1:/ rw
92 90 0:34 / /host/root/archive ro,relatime - nfs4 nas:/a rw
93 90 0:35 / /host/root/sbro rw,relatime - xfs /dev/sdb ro
"""
ROOT = "/host/root"
S = [{"storage_name": "pvs", "mount_path": "/mgmt_storage"},
     {"storage_name": "arch", "mount_path": "/archive"},
     {"storage_name": "sbro", "mount_path": "/sbro"},
     {"storage_name": "plain", "mount_path": "/plain"},
     {"storage_name": "gone", "mount_path": "/gone"}]
EXIST = {"/host/root", "/host/root/mgmt_storage", "/host/root/archive",
         "/host/root/sbro", "/host/root/plain"}


def _isdir(p):
    return p in EXIST


def _access_ro_bind(p, mode):
    # 읽기 전용 바인드 아래: 존재하면 R/X 는 참, W 는 항상 거짓
    return p in EXIST and mode != os.W_OK


def test_host_path_translation():
    assert host_path("", "/cephfs") == "/cephfs"
    assert host_path("/host/root", "/cephfs") == "/host/root/cephfs"
    assert host_path("/host/root/", "/a/b") == "/host/root/a/b"


def test_parse_mount_table_reads_per_mount_and_superblock_options():
    t = parse_mount_table(HOST_MI)
    assert t["/mgmt_storage"] == {"opts": "rw,relatime", "fstype": "ceph", "sb_opts": "rw"}
    assert t["/archive"]["opts"] == "ro,relatime"
    assert t["/sbro"]["sb_opts"] == "ro"


def test_probe_mounts_translates_paths_and_derives_writable_from_mountinfo():
    out = {m["storage_name"]: m for m in probe_mounts(
        S, mountinfo_text=HOST_MI, isdir=_isdir, access=_access_ro_bind,
        host_root=ROOT, self_mountinfo_text=SELF_MI)}
    # 사고 경로: 매니페스트에 hostPath 없이도 /host/root/mgmt_storage 로 Ready
    assert out["pvs"]["status"] == "Ready" and out["pvs"]["reason"] is None
    assert out["pvs"]["writable"] is True          # W_OK 가 아니라 rw 옵션
    assert out["arch"]["status"] == "Ready" and out["arch"]["writable"] is False   # per-mount ro
    assert out["sbro"]["status"] == "Ready" and out["sbro"]["writable"] is False   # superblock ro
    assert out["plain"]["status"] == "Missing" and out["plain"]["reason"] == "not_a_mountpoint"
    assert out["gone"]["status"] == "Missing" and out["gone"]["reason"] == "missing_mount_path"
    # 보고 경로는 번역 전 호스트 경로 그대로(리컨실러·화면 계약)
    assert out["pvs"]["mount_path"] == "/mgmt_storage"


def test_propagation_stale_when_host_has_mount_but_container_does_not():
    stale_self = "90 80 0:20 / /host/root ro,relatime - ext4 /dev/root rw\n"
    out = probe_mounts([S[0]], mountinfo_text=HOST_MI, isdir=_isdir, access=_access_ro_bind,
                       host_root=ROOT, self_mountinfo_text=stale_self)
    assert out[0]["status"] == "Missing" and out[0]["reason"] == "propagation_stale"
    # 자기 mountinfo 를 못 읽었으면(빈 문자열) 검사를 건너뛴다 -- 거짓 stale 금지
    out = probe_mounts([S[0]], mountinfo_text=HOST_MI, isdir=_isdir, access=_access_ro_bind,
                       host_root=ROOT, self_mountinfo_text="")
    assert out[0]["status"] == "Ready"


def test_host_root_missing_fails_loud_for_every_storage():
    out = probe_mounts(S[:2], mountinfo_text=HOST_MI, isdir=lambda p: False,
                       access=lambda p, m: True, host_root=ROOT, self_mountinfo_text=SELF_MI)
    assert [m["reason"] for m in out] == ["host_root_missing", "host_root_missing"]
    assert all(m["status"] == "Missing" and m["exists"] is False for m in out)


def test_legacy_direct_mode_is_unchanged_without_host_root():
    def access(p, mode):
        return p == "/mgmt_storage"
    out = probe_mounts([S[0]], mountinfo_text=HOST_MI, isdir=lambda p: p == "/mgmt_storage",
                       access=access)
    assert out[0]["status"] == "Ready" and out[0]["writable"] is True


def test_artifact_base_writable_from_covering_mount_options():
    base = probe_artifact_base("/mgmt_storage/dms/artifacts", isdir=_isdir_base,
                               access=_access_ro_bind, host_root=ROOT, mountinfo_text=HOST_MI)
    assert base == {"path": "/mgmt_storage/dms/artifacts", "exists": True, "writable": True}
    base = probe_artifact_base("/archive/dms", isdir=_isdir_base, access=_access_ro_bind,
                               host_root=ROOT, mountinfo_text=HOST_MI)
    assert base["writable"] is False        # 덮는 마운트 /archive 가 ro
    base = probe_artifact_base("/nowhere/x", isdir=lambda p: False, access=_access_ro_bind,
                               host_root=ROOT, mountinfo_text=HOST_MI)
    assert base == {"path": "/nowhere/x", "exists": False, "writable": False}


def _isdir_base(p):
    return p in {"/host/root/mgmt_storage/dms/artifacts", "/host/root/archive/dms"}


def test_os_metrics_statvfs_uses_translated_path():
    asked = []

    class Vfs:
        f_frsize = 4096
        f_blocks = 100
        f_bavail = 40

    def statvfs(p):
        asked.append(p)
        return Vfs()
    out = probe_os_metrics([S[0]], read_text=lambda p: "", statvfs=statvfs, host_root=ROOT)
    assert asked == ["/host/root/mgmt_storage"]
    assert out["disks"][0]["storage_name"] == "pvs"


def test_build_report_threads_host_root_and_marks_probe_mode():
    seen = {}

    def mounts_fn(storages, **kw):
        seen["mounts"] = kw
        return []

    def os_fn(storages, **kw):
        seen["os"] = kw
        return {}

    def ab_fn(path, **kw):
        seen["ab"] = kw
        return None
    r = build_report("n1", S, [], mountinfo_text=HOST_MI, mounts_fn=mounts_fn,
                     tools_fn=lambda n, **k: [], identities_fn=lambda u, **k: [],
                     os_fn=os_fn, artifact_base_fn=ab_fn, artifact_base_path="/x",
                     host_root=ROOT, self_mountinfo_text=SELF_MI)
    assert r["probe_mode"] == "host_root"
    assert seen["mounts"]["host_root"] == ROOT and seen["mounts"]["self_mountinfo_text"] == SELF_MI
    assert seen["os"]["host_root"] == ROOT
    assert seen["ab"] == {"host_root": ROOT, "mountinfo_text": HOST_MI}
    r = build_report("n1", [], [], mountinfo_text="", mounts_fn=mounts_fn,
                     tools_fn=lambda n, **k: [], identities_fn=lambda u, **k: [],
                     os_fn=os_fn, artifact_base_fn=ab_fn)
    assert r["probe_mode"] == "direct" and seen["mounts"]["host_root"] == ""


def test_agent_settings_reads_host_root_and_strips_trailing_slash():
    env = {"DMS_AGENT_API_URL": "http://api", "DMS_SHARED_TOKEN": "t"}
    assert AgentSettings.from_env(env).host_root == ""
    assert AgentSettings.from_env({**env, "DMS_AGENT_HOST_ROOT": "/host/root/"}).host_root == "/host/root"
