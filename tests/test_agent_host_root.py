"""에이전트 호스트 루트 프로브(방안 A, 2026-09-16).

DaemonSet 이 호스트 `/` 를 읽기 전용·HostToContainer 로 /host/root 에 한 번만 붙이고
프로브가 mount_path 를 그 접두로 번역한다 -- 스토리지마다 hostPath 를 손으로 나열하던
관행(등록만 하면 Missing) 을 없앤다. 함정 두 개를 여기서 고정한다:
  1. writable 은 mountinfo 옵션(rw/ro)에서 -- ro 바인드 아래 W_OK 는 항상 거짓이라
     access 를 쓰면 placement(require_writable) 가 sync 목적지를 전부 배제한다.
  2. 전파 자가 진단 -- 호스트엔 마운트포인트인데 /host/root 아래에 안 보이면
     propagation_stale(호스트 `/` 가 shared 가 아니거나 전파 없음)."""
import errno
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
    assert base == {"path": "/mgmt_storage/dms/artifacts", "exists": True, "writable": True,
                    "mount_point": "/mgmt_storage", "fstype": "ceph"}
    base = probe_artifact_base("/archive/dms", isdir=_isdir_base, access=_access_ro_bind,
                               host_root=ROOT, mountinfo_text=HOST_MI)
    assert base["writable"] is False        # 덮는 마운트 /archive 가 ro
    assert (base["mount_point"], base["fstype"]) == ("/archive", "nfs4")
    base = probe_artifact_base("/nowhere/x", isdir=lambda p: False, access=_access_ro_bind,
                               host_root=ROOT, mountinfo_text=HOST_MI)
    # 없는 경로여도 덮는 마운트('/')는 정보로 싣는다 -- 판정은 exists 가 먼저다(routes_mounts._base_cell)
    assert base == {"path": "/nowhere/x", "exists": False, "writable": False,
                    "mount_point": "/", "fstype": "ext4"}


def test_artifact_base_reports_covering_shared_mount_and_fstype():
    # 2026-11-27(dms-k8s d14ee402 이식): base 가 공유 마운트(nfs4) 아래면 그 지점·종류를 싣는다
    mi = HOST_MI + "50 22 0:40 / /dms_shared rw,relatime shared:9 - nfs4 10.10.10.1:/data/dms_shared rw\n"
    base = probe_artifact_base("/dms_shared/dms/artifacts",
                               isdir=lambda p: p == "/host/root/dms_shared/dms/artifacts",
                               access=_access_ro_bind, host_root=ROOT, mountinfo_text=mi)
    assert base == {"path": "/dms_shared/dms/artifacts", "exists": True, "writable": True,
                    "mount_point": "/dms_shared", "fstype": "nfs4"}


def test_artifact_base_local_directory_without_shared_mount_reports_root():
    # 2026-11-23 사고의 모양: 공유 마운트가 빠지고 같은 경로의 로컬 디렉터리만 남은 노드 -- exists·writable 은 참이라
    # 정상으로 보이지만 덮는 마운트가 '/'(루트 fs) 다. 서버는 이것을 missing(artifact_base_local_dir)으로 판정한다.
    base = probe_artifact_base("/dms_shared/dms/artifacts",
                               isdir=lambda p: p == "/host/root/dms_shared/dms/artifacts",
                               access=_access_ro_bind, host_root=ROOT, mountinfo_text=HOST_MI)
    assert base == {"path": "/dms_shared/dms/artifacts", "exists": True, "writable": True,
                    "mount_point": "/", "fstype": "ext4"}


def test_artifact_base_without_any_covering_mount_is_unknown():
    # '/' 조차 없는 mountinfo -- 덮는 마운트를 모르면 지어내지 않는다(둘 다 None, writable 은 종전대로 False)
    mi = "40 22 0:33 / /mgmt_storage rw,relatime shared:2 - ceph 10.0.0.1:/ rw\n"
    base = probe_artifact_base("/dms_shared/dms/artifacts", isdir=lambda p: True,
                               access=_access_ro_bind, host_root=ROOT, mountinfo_text=mi)
    assert base == {"path": "/dms_shared/dms/artifacts", "exists": True, "writable": False,
                    "mount_point": None, "fstype": None}


def test_artifact_base_fstype_missing_in_mountinfo_is_none():
    # '-' 구분자가 없는 줄(fstype 칸 없음)은 빈 문자열 대신 None(모름) -- 서버가 빈 이름을 보이지 않게
    mi = "22 1 0:20 / / rw,relatime shared:1\n"
    base = probe_artifact_base("/x", isdir=lambda p: True, access=_access_ro_bind,
                               host_root=ROOT, mountinfo_text=mi)
    assert (base["mount_point"], base["fstype"]) == ("/", None)


def _isdir_base(p):
    return p in {"/host/root/mgmt_storage/dms/artifacts", "/host/root/archive/dms"}


# 2026-11-27 리뷰: 심링크 base 접두(allowlist 가 「/data → /cephfs/x」를 허용)는 덮는 마운트를 **host_root 안에서 푼 경로**로 찾는다
# -- 글자 그대로 찾으면 '/' 아래로 보여 모든 노드가 「로컬 디렉터리」(문제)가 됐다.
SYM_MI = """\
22 1 0:20 / / rw,relatime shared:1 - ext4 /dev/root rw
40 22 0:33 / /cephfs rw,relatime shared:2 - ceph 10.0.0.1:/ rw
"""


def _sym_root(tmp_path):
    root = tmp_path / "root"
    (root / "cephfs" / "x" / "dms" / "artifacts").mkdir(parents=True)
    (root / "opt").mkdir()
    return root


def test_artifact_base_relative_symlink_resolves_inside_host_root(tmp_path):
    root = _sym_root(tmp_path)
    (root / "data").symlink_to("cephfs/x")                    # 상대 대상: 지금 디렉터리(/)에서 푼다
    (root / "opt" / "base").symlink_to("../cephfs/x")          # '..' 를 거치는 상대 대상
    for path in ("/data/dms/artifacts", "/opt/base/dms/artifacts"):
        base = probe_artifact_base(path, host_root=str(root), mountinfo_text=SYM_MI)
        assert base == {"path": path, "exists": True, "writable": True, "mount_point": "/cephfs", "fstype": "ceph"}


def test_artifact_base_absolute_symlink_is_rerooted_under_host_root(tmp_path):
    root = _sym_root(tmp_path)
    (root / "data").symlink_to("/cephfs/x")                   # 호스트 절대 대상 -- 컨테이너 루트가 아니라 host_root 아래로
    base = probe_artifact_base("/data/dms/artifacts", host_root=str(root), mountinfo_text=SYM_MI)
    assert (base["mount_point"], base["fstype"]) == ("/cephfs", "ceph")
    from dms.agent.probes import resolve_in_host_root
    assert resolve_in_host_root(str(root), "/data/dms/artifacts") == "/cephfs/x/dms/artifacts"
    # 없는 조각은 남은 조각과 함께 글자 그대로('..' 만 접는다) -- 없는 base 여도 덮는 마운트는 정보로 싣는다
    assert resolve_in_host_root(str(root), "/data/nope/../dms/z") == "/cephfs/x/dms/z"
    assert resolve_in_host_root(str(root), "/nowhere/x") == "/nowhere/x"


def test_artifact_base_symlink_loop_or_unreadable_link_reports_no_cover(tmp_path):
    root = _sym_root(tmp_path)
    (root / "l1").symlink_to("l2")
    (root / "l2").symlink_to("/l1")
    base = probe_artifact_base("/l1/dms", host_root=str(root), mountinfo_text=SYM_MI)
    # 고리면 덮는 마운트를 모른다 -- 로컬 디렉터리 판정을 하지 않게 None(지어내지 않는다)
    assert base == {"path": "/l1/dms", "exists": False, "writable": False, "mount_point": None, "fstype": None}
    from dms.agent.probes import resolve_in_host_root
    (root / "a").symlink_to("b")
    (root / "b").symlink_to("cephfs")
    assert resolve_in_host_root(str(root), "/a/x", max_hops=2) == "/cephfs/x"
    assert resolve_in_host_root(str(root), "/a/x", max_hops=1) is None      # 상한을 넘으면 못 푼 것
    # EINVAL(심링크 아님)·ENOENT·ENOTDIR 밖의 오류(끊긴 마운트의 ENOTCONN 등)도 못 푼 것 -- writable 은 글자 그대로의 덮는 마운트로

    def broken(p):
        raise OSError(errno.ENOTCONN, "Transport endpoint is not connected")
    base = probe_artifact_base("/cephfs/x/dms/artifacts", isdir=lambda p: True, host_root=str(root),
                               mountinfo_text=SYM_MI, readlink=broken)
    assert base == {"path": "/cephfs/x/dms/artifacts", "exists": True, "writable": True, "mount_point": None,
                    "fstype": None}


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
