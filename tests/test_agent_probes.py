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


# cilium 이 붙은 실제 노드 모양(dms-w3 실측 축약): 물리 eth0 + 오버레이 터널
# cilium_vxlan + 파드 veth 호스트쪽 lxc_*. 이름 폭이 6칸을 넘으면 왼쪽 여백이
# 사라지는 것까지 실물 그대로다 -- 파서가 name.strip() 에 기대는 지점이다.
# 여기 이름들은 이 테스트베드의 우연일 뿐이라, 판별이 이름을 안 본다는 것은 아래
# calico/flannel/다중 NIC 형상이 따로 못박는다.
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

# 커널이 /proc/net/dev 를 찍는 서식 그대로(net/core/net-procfs.c 의 seq_printf):
# 이름은 %6s 라 6칸 우측 정렬이고 길면 그대로 밀려 왼쪽 여백이 사라진다. 위
# NETDEV_CNI 는 실제 캡처처럼 여백이 고르지 않은 판, 아래 생성기는 커널 서식 판 --
# 두 폭 모두에서 파서가 같은 값을 내야 한다. 이름에 콜론은 없다(파서가 partition(":")).
_NETDEV_FMT = ("%6s: %7d %7d %4d %4d %4d %5d %10d %9d "
               "%8d %7d %4d %4d %4d %5d %7d %10d")
_NETDEV_HEAD = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|"
    "bytes    packets errs drop fifo colls carrier compressed\n")


def _netdev(rows):
    """(이름, rx_bytes, tx_bytes) 목록 -> /proc/net/dev 텍스트. rx 8칸 + tx 8칸."""
    body = "".join(
        _NETDEV_FMT % (name, rx, 1, 0, 0, 0, 0, 0, 0, tx, 1, 0, 0, 0, 0, 0, 0) + "\n"
        for name, rx, tx in rows)
    return _NETDEV_HEAD + body


# 같은 숫자(물리 1000/2000, 가상 400/600 + 70/90)를 이름만 바꿔 심는다 -- 값이 같으니
# 결과가 갈리면 원인은 오직 이름/등록 위치다.
NETDEV_CALICO = _netdev([("lo", 999999, 999999), ("ens192", 1000, 2000),
                         ("cali3f7a1b2c4d5", 400, 600), ("tunl0", 70, 90)])
NETDEV_FLANNEL = _netdev([("lo", 999999, 999999), ("eno1", 1000, 2000),
                          ("bond0", 400, 600), ("flannel.1", 70, 90),
                          ("cni0", 30, 50), ("veth1a2b3c", 7, 9)])
NETDEV_MULTI_NIC = _netdev([("lo", 999999, 999999), ("ens192", 1000, 2000),
                            ("ens224", 300, 500), ("br0", 400, 600)])


def _virtual_net_dir(tmp_path, names, subdir="virtual-net"):
    """커널이 가상 인터페이스를 등록하는 /sys/devices/virtual/net/<name> 모사."""
    path = tmp_path / subdir
    path.mkdir()
    for name in names:
        (path / name).mkdir()
    return str(path)


def _net_of(netdev_text, **kwargs):
    files = {"/proc/loadavg": LOADAVG, "/proc/meminfo": MEMINFO,
             "/host/proc/1/net/dev": netdev_text}
    out = probe_os_metrics([], read_text=lambda p: files[p], statvfs=lambda p: None,
                           net_dev_path="/host/proc/1/net/dev", **kwargs)
    return out["network_rx_bytes"], out["network_tx_bytes"]


def _net(**kwargs):
    return _net_of(NETDEV_CNI, **kwargs)


def test_probe_os_metrics_sums_only_physical_interfaces_when_configured(tmp_path):
    # 판별은 이름이 아니라 커널 등록 위치다(설계 §2.6): /proc/net/dev 에 있는데
    # /sys/devices/virtual/net/ 에 없는 것만 물리다. 여기서는 eth0 만 남아야 한다 --
    # cilium_vxlan 은 eth0 과 이중 계상이고 lxc_* 는 파드 veth 라 노드 처리량이 아니다.
    rx, tx = _net(virtual_net_path=_virtual_net_dir(
        tmp_path, ["lo", "cilium_vxlan", "lxc_abc"]))
    assert (rx, tx) == (1000, 2000)


def test_physical_detection_is_name_independent_calico_shape(tmp_path):
    # calico: 물리가 ens192 고 가상은 cali*/tunl0 -- eth0 이라는 이름은 아예 없다.
    # 접두 블록리스트였다면 CNI 마다 목록을 새로 알아야 했을 자리다.
    assert _net_of(NETDEV_CALICO, virtual_net_path=_virtual_net_dir(
        tmp_path, ["lo", "cali3f7a1b2c4d5", "tunl0"])) == (1000, 2000)


def test_physical_detection_is_name_independent_flannel_and_bonded_shape(tmp_path):
    # flannel + 본딩: 물리는 본드 슬레이브 eno1 이고, bond0 은 사람 눈에 "진짜 NIC"
    # 같지만 본딩은 커널 가상 드라이버라 /sys/devices/virtual/net/bond0 로 등록된다
    # (로컬 실측: 브리지 dmsbr0/docker0/virbr0 도 같은 자리에 있다). 이름 감각으로
    # 골랐다면 정확히 여기서 틀린다. 게다가 bond0 의 바이트는 슬레이브로도 나가므로
    # 둘 다 더하면 이중 계상이다 -- 등록 위치를 따르면 슬레이브만 한 번 세어 맞는다.
    assert _net_of(NETDEV_FLANNEL, virtual_net_path=_virtual_net_dir(
        tmp_path, ["lo", "bond0", "flannel.1", "cni0", "veth1a2b3c"])) == (1000, 2000)


def test_multiple_physical_nics_are_summed_together(tmp_path):
    # 물리가 하나라는 가정도 테스트베드 편향이다 -- 이중화/스토리지 전용 NIC 가 흔하다.
    assert _net_of(NETDEV_MULTI_NIC, virtual_net_path=_virtual_net_dir(
        tmp_path, ["lo", "br0"])) == (1000 + 300, 2000 + 500)


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


def test_unset_virtual_net_path_cannot_exclude_host_nic_via_pod_sysfs_collision(tmp_path):
    """함정 못박기: 마운트가 없으면 **다른 네임스페이스의 집합으로 거르게** 된다.

    파드 안에도 /sys/devices/virtual/net/ 이 있지만 거기 든 것은 **파드 netns 의**
    가상 인터페이스다. 기본값이 그 경로였다면 마운트 없는 배포에서 그 집합으로
    **호스트의** 인터페이스 목록을 거른다 -- 애초에 비교 대상이 아닌 두 집합이라,
    이름이 겹치는 호스트 인터페이스는 **무엇이든** 가상으로 오판돼 빠진다. 고치려던
    것보다 나쁜 값이다(설계 §2.6). 그래서 기본은 필터 없음이어야 한다.

    여기서는 그 충돌이 가장 흔한 사례인 eth0 으로 재현한다(CNI 가 파드 인터페이스를
    관례적으로 eth0 으로 만들고, VM/클라우드 호스트의 물리 NIC 도 흔히 eth0 이다).
    이름이 본질이 아니라는 것은 아래 ens192 판이 따로 못박는다. 첫 단언이 계약이고,
    둘째는 "그 디렉터리를 실제로 읽히면 물리가 빠진다"는 전제를 고정해 첫 단언이
    공허하지 않음을 보인다.
    """
    pod_sysfs = _virtual_net_dir(tmp_path, ["lo", "eth0"])   # 파드 안에서 본 모습
    assert _net() == (1470, 2690)                            # 미설정 -> eth0 살아 있다
    assert _net(virtual_net_path=pod_sysfs) == (400 + 70, 600 + 90)


def test_pod_sysfs_collision_hits_any_host_nic_name_not_only_eth0(tmp_path):
    """같은 사고가 eth0 이 아닌 이름에서도 똑같이 난다 -- 규칙은 이름이 아니라 충돌이다.

    호스트 물리가 ens192 인 calico 노드에서, 파드 쪽 집합에 어쩌다 ens192 가 있으면
    (멀티어스 등으로 파드 인터페이스 이름은 배포마다 다르다) 그 물리 NIC 가 제외된다.
    'eth0 일 때만 위험하다'로 읽고 기본값을 되살리는 회귀를 막는다.
    """
    pod_sysfs = _virtual_net_dir(tmp_path, ["lo", "ens192"])
    assert _net_of(NETDEV_CALICO) == (1470, 2690)            # 미설정 -> ens192 살아 있다
    assert _net_of(NETDEV_CALICO, virtual_net_path=pod_sysfs) == (400 + 70, 600 + 90)


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
    # cpuinfo 도 같은 fail-soft: 못 읽으면 0 이 아니라 None(모름)
    assert out["cpu_count"] is None


# x86 /proc/cpuinfo 실물 축약(테스트베드 워커 2코어 형상): 코어마다 "processor" 로
# 시작하는 키-값 블록이 반복된다. loadavg/meminfo 처럼 네트워크 네임스페이스와
# 무관한 호스트 값이라 파드 안에서 기본 경로 그대로 읽는다.
CPUINFO = """\
processor\t: 0
vendor_id\t: GenuineIntel
model name\t: Intel(R) Xeon(R) CPU E5-2680
processor\t: 1
vendor_id\t: GenuineIntel
model name\t: Intel(R) Xeon(R) CPU E5-2680
"""


def test_probe_os_metrics_counts_cpus_from_cpuinfo():
    # 대시보드 load 차트의 상한(코어 수) 데이터 -- "processor" 키 라인 수가 곧
    # 논리 CPU 수다(ARM 계열도 processor 라인 자체는 공통).
    files = {"/proc/loadavg": LOADAVG, "/proc/meminfo": MEMINFO,
             "/proc/net/dev": NETDEV, "/proc/cpuinfo": CPUINFO}
    out = probe_os_metrics([], read_text=lambda p: files[p], statvfs=lambda p: None)
    assert out["cpu_count"] == 2


def test_probe_os_metrics_cpuinfo_without_processor_lines_is_unknown():
    # 0 코어는 존재할 수 없는 값이다 -- processor 라인이 하나도 없으면 서식이
    # 예상 밖인 것이므로 0(거짓 정상값)이 아니라 None(모름)으로 낸다.
    files = {"/proc/loadavg": LOADAVG, "/proc/meminfo": MEMINFO,
             "/proc/net/dev": NETDEV, "/proc/cpuinfo": "flags\t: fpu vme\n"}
    out = probe_os_metrics([], read_text=lambda p: files[p], statvfs=lambda p: None)
    assert out["cpu_count"] is None


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


def test_probe_artifact_base_reports_exists_and_writable():
    # 슬라이스 18(설계 §2.4b): exists 는 hostPath type: Directory 기동 가능 여부의
    # 직접 신호이고, writable 은 에이전트 프로세스 uid 기준의 정직한 한계 표기다.
    from dms.agent.probes import probe_artifact_base
    r = probe_artifact_base("/base", isdir=lambda p: True,
                            access=lambda p, mode: False)
    assert r == {"path": "/base", "exists": True, "writable": False}
    r = probe_artifact_base("/nope", isdir=lambda p: False,
                            access=lambda p, mode: True)
    # 존재하지 않으면 writable 도 False 다(없는 디렉터리에 쓸 수 없다)
    assert r == {"path": "/nope", "exists": False, "writable": False}
    assert probe_artifact_base(None) is None
    assert probe_artifact_base("") is None
