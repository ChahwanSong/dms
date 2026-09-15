"""노드 프로브. 시스템 접근(파일/명령)은 전부 파라미터 주입 — 순수 로직만 이 모듈에 둔다."""
import grp
import os
import pwd
import shutil

_OCTAL_ESCAPES = {"\\040": " ", "\\011": "\t", "\\012": "\n", "\\134": "\\"}


def _unescape(field: str) -> str:
    for escaped, char in _OCTAL_ESCAPES.items():
        field = field.replace(escaped, char)
    return field


def parse_mountinfo(text: str) -> set[str]:
    points: set[str] = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) > 4:
            points.add(_unescape(fields[4]))
    return points


def parse_mount_table(text: str) -> dict:
    """mountinfo → {mount_point: {"opts", "fstype", "sb_opts"}}. 같은 지점에 여러
    마운트(overmount)면 나중 줄(위에 쌓인 것)이 이긴다 -- 커널이 그 순서로 나열한다.
    옵션은 writable 판정(_mount_rw)에 쓴다: 6열은 per-mount 옵션(바인드 ro 등),
    '-' 뒤 세 번째는 슈퍼블록 옵션(fs 전체 ro)이다."""
    table: dict = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6:
            continue
        fstype = sb_opts = ""
        if "-" in fields[6:]:
            sep = fields.index("-", 6)
            rest = fields[sep + 1:]
            fstype = rest[0] if rest else ""
            sb_opts = rest[2] if len(rest) > 2 else ""
        table[_unescape(fields[4])] = {"opts": fields[5], "fstype": fstype,
                                       "sb_opts": sb_opts}
    return table


def _mount_rw(entry) -> bool:
    return ("ro" not in entry["opts"].split(",")
            and "ro" not in entry["sb_opts"].split(","))


def _covering_mount(table, path):
    """path 를 덮는 가장 깊은 마운트 지점(없으면 None). 아티팩트 base 처럼 마운트
    지점 아래 하위 디렉터리의 쓰기 가능 여부는 그 마운트의 옵션이 정한다."""
    best = None
    for point in table:
        if path == point or path.startswith(point.rstrip("/") + "/"):
            if best is None or len(point) > len(best):
                best = point
    return best


def host_path(host_root: str, path: str) -> str:
    """호스트 경로 → 에이전트 컨테이너 안 프로브 경로. host_root 가 비면 그대로
    (레거시: 스토리지마다 hostPath 를 같은 경로에 붙이던 매니페스트)."""
    if not host_root:
        return path
    return host_root.rstrip("/") + "/" + path.lstrip("/")


def probe_mounts(storages, *, mountinfo_text, isdir=os.path.isdir, access=os.access,
                 host_root="", self_mountinfo_text=""):
    """스토리지 마운트 프로브. 마운트포인트 여부는 **호스트** PID 1 의 mountinfo 로,
    존재·접근은 컨테이너 안 경로로 본다 -- host_root(방안 A, 2026-09-16)가 있으면
    그 접두로 번역해 보고, 없으면 종전처럼 같은 경로를 직접 본다.

    host_root 모드의 writable 은 os.access(W_OK) 가 아니라 호스트 mountinfo 의
    마운트 옵션(rw/ro)이다: 에이전트는 root 라 W_OK 는 사실상 "마운트가 rw 인가"
    만 답하고, 호스트 루트 바인드 아래에선 경로가 최상위(readOnly, 호스트 루트 fs)
    인지 HostToContainer 로 전파된 하위 마운트(호스트 옵션 유지, 보통 rw)인지에
    따라 값이 갈린다 -- ro 쪽이면 placement(require_writable)가 sync 목적지 노드를
    전부 배제한다. mountinfo 옵션은 어느 쪽이든 같은 답을 주고 의미는 종전과 같다.

    self_mountinfo_text(에이전트 자신의 /proc/self/mountinfo)가 오면 전파 자가
    진단: 호스트엔 마운트포인트인데 host_root 아래에서 안 보이면 HostToContainer
    전파가 안 된 것(호스트 `/` 가 shared 가 아니거나 파드가 마운트보다 먼저 떴는데
    전파 없음) -- `propagation_stale` 로 Missing. 못 읽어서 빈 문자열이면 검사를
    건너뛴다(거짓 stale 금지). host_root 자체가 없으면 전부 `host_root_missing`."""
    points = parse_mountinfo(mountinfo_text)
    table = parse_mount_table(mountinfo_text) if host_root else {}
    self_points = parse_mountinfo(self_mountinfo_text) if (host_root and self_mountinfo_text) else None
    root_missing = bool(host_root) and not isdir(host_root)
    results = []
    for storage in storages:
        path = storage["mount_path"]
        probe = host_path(host_root, path)
        exists = (not root_missing) and bool(isdir(probe))
        is_mountpoint = path in points
        readable = exists and bool(access(probe, os.R_OK)) and bool(access(probe, os.X_OK))
        if host_root:
            entry = table.get(path)
            writable = exists and is_mountpoint and entry is not None and _mount_rw(entry)
        else:
            writable = exists and bool(access(probe, os.W_OK))
        propagated = (self_points is None) or (host_path(host_root, path) in self_points)
        if root_missing:
            status, reason = "Missing", "host_root_missing"
        elif not exists:
            status, reason = "Missing", "missing_mount_path"
        elif not is_mountpoint:
            status, reason = "Missing", "not_a_mountpoint"
        elif not propagated:
            status, reason = "Missing", "propagation_stale"
        elif not readable:
            status, reason = "Missing", "not_readable"
        else:
            status, reason = "Ready", None
        results.append({
            "storage_name": storage["storage_name"], "mount_path": path,
            "exists": exists, "is_mountpoint": is_mountpoint,
            "readable": readable, "writable": writable,
            "status": status, "reason": reason,
        })
    return results


def probe_tools(names, *, which=shutil.which):
    """노드 도구 프로브는 **존재 확인만** 한다(2026-08-30 사용자 결정). 도구는
    노드가 아니라 잡 파드(dms-mpifileutils 이미지, MPI 런타임 포함)에서 실행되고,
    에이전트 이미지엔 MPI 런타임(libmpi.so.40)이 없어 `--version` 은 노드에서
    항상 실패한다 -- 성공할 수 없는 실행을 시도해 크립틱한 사유를 만드는 대신,
    which 로 바이너리 존재만 본다(버전 개념 자체를 뺀다).

    status 값("Ready"/"Missing")은 표시용이 아니라 계약이다: placement._tool_ready
    가 노드 적격성 게이트로 이 값을 읽으므로 문자열을 그대로 유지한다(화면은
    "설치됨/없음"으로 relabel 하지만 와이어 값은 안 바꾼다)."""
    results = []
    for name in names:
        path = which(name)
        results.append({
            "name": name,
            "status": "Ready" if path else "Missing",
            "path": path,
            "reason": None if path else "tool_not_found",
        })
    return results


def probe_identities(usernames, *, getpwnam=pwd.getpwnam, getgrall=grp.getgrall):
    try:
        all_groups = list(getgrall())
    except Exception:
        all_groups = []
    results = []
    for username in usernames:
        try:
            entry = getpwnam(username)
        except KeyError:
            results.append({"username": username, "status": "Missing",
                            "uid": None, "gid": None, "groups": [],
                            "reason": "user_not_found"})
            continue
        groups = sorted(g.gr_name for g in all_groups if username in g.gr_mem)
        results.append({"username": username, "status": "Ready",
                        "uid": entry.pw_uid, "gid": entry.pw_gid, "groups": groups})
    return results


def probe_artifact_base(path, *, isdir=os.path.isdir, access=os.access,
                        host_root="", mountinfo_text=""):
    """아티팩트 base 프로브(슬라이스 18 설계 §2.4b). **mounts 배열에 섞지 않는다**:
    reconciler 가 mounts 를 storage_name 기준으로 storages.status 에 매핑하므로
    섞으면 스토리지 판정이 오염된다 -- 리포트 최상위의 별도 필드로만 나른다
    (build_report 참고).

    exists 가 핵심 신호다: 잡 파드 hostPath 는 type: Directory 강제라
    (execution_manifests) 디렉터리가 없는 노드에서는 파드가 기동 자체를 실패한다.
    writable 은 **에이전트 프로세스 uid** 의 W_OK 지 잡 파드 요청자 uid 가 아니다
    -- 정직한 한계로 화면이 문구로 표기한다. probe_mounts 의 status 판정이
    writable 을 반영하지 않는 것과 같은 이유로, 소비자는 status 같은 요약이 아니라
    이 두 필드를 직접 본다."""
    if not path:
        return None    # 서버가 아직 대상을 내리지 않았다(부트스트랩) -- 모름
    probe = host_path(host_root, path)
    exists = bool(isdir(probe))
    if host_root:
        # 호스트 루트는 ro 바인드라 W_OK 는 항상 거짓 -- base 를 덮는 마운트의
        # 옵션(rw/ro)이 답이다(probe_mounts 와 같은 이유). 덮는 마운트를 못 찾으면
        # ("/" 조차 없는 mountinfo) 모른다고 지어내지 않고 False.
        table = parse_mount_table(mountinfo_text)
        cover = _covering_mount(table, path)
        writable = exists and cover is not None and _mount_rw(table[cover])
    else:
        writable = exists and bool(access(probe, os.W_OK))
    return {"path": path, "exists": exists, "writable": writable}


def probe_os_metrics(storages, *, read_text, statvfs=os.statvfs,
                     net_dev_path="/proc/net/dev", virtual_net_path="", host_root=""):
    metrics = {"load1": None, "load5": None, "load15": None, "cpu_count": None,
               "memory_total_kb": None, "memory_available_kb": None,
               "disks": [], "network_rx_bytes": None, "network_tx_bytes": None}
    try:
        parts = read_text("/proc/loadavg").split()
        metrics["load1"], metrics["load5"], metrics["load15"] = (
            float(parts[0]), float(parts[1]), float(parts[2]))
    except Exception:
        pass
    try:
        # 대시보드 load 차트의 상한(코어 수). "processor" 키 라인 수 = 논리 CPU 수.
        # /proc/cpuinfo 는 loadavg/meminfo 처럼 netns 와 무관한 호스트 값이라 파드
        # 안에서 기본 경로 그대로 읽는다(추가 마운트 불필요). 라인이 0개면 서식이
        # 예상 밖인 것 -- 0 코어는 존재할 수 없으니 None(모름)을 유지한다.
        count = sum(1 for line in read_text("/proc/cpuinfo").splitlines()
                    if line.partition(":")[0].strip() == "processor")
        if count > 0:
            metrics["cpu_count"] = count
    except Exception:
        pass
    try:
        for line in read_text("/proc/meminfo").splitlines():
            if line.startswith("MemTotal:"):
                metrics["memory_total_kb"] = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                metrics["memory_available_kb"] = int(line.split()[1])
    except Exception:
        pass
    for storage in storages:
        try:
            vfs = statvfs(host_path(host_root, storage["mount_path"]))
            total = vfs.f_frsize * vfs.f_blocks
            used = vfs.f_frsize * (vfs.f_blocks - vfs.f_bavail)
            metrics["disks"].append({"storage_name": storage["storage_name"],
                                     "total_bytes": total, "used_bytes": used})
        except Exception:
            continue
    try:
        # 커널은 가상 인터페이스를 /sys/devices/virtual/net/<name> 아래 등록한다 --
        # /proc/net/dev 에는 있는데 거기엔 없는 것이 물리 NIC 다(설계 §2.6). 이름
        # 접두 블록리스트(lxc*/cilium_*/cali*/flannel*/...)는 CNI 마다 달라 조용히
        # 틀리므로 쓰지 않는다.
        virtual, filtering = set(), False
        if virtual_net_path:
            try:
                virtual = set(os.listdir(virtual_net_path))
                filtering = True
            except OSError:
                # 설정됐는데 못 읽으면(마운트 누락 등) 필터를 켜지 않는다. 여기서
                # 지표를 None/0 으로 떨구거나 빈 집합으로 필터하는 척하는 것보다,
                # 덜 정밀하더라도 기존 값(lo 제외 전량 합)을 유지하는 편이 낫다 --
                # 지표를 잃는 쪽이 더 나쁜 실패다(설계 §2.6, §4 fail-soft).
                filtering = False
        rx = tx = 0
        # /proc/net/* 는 네트워크 네임스페이스 범위다 -- 파드 안에서 기본 경로를
        # 읽으면 veth 값이 나온다. loadavg/meminfo 는 네임스페이스되지 않아 이미
        # 호스트 값이라 그대로 두고(설계 §2.5), 네트워크만 DaemonSet 이 마운트한
        # PID 1 경로(/host/proc/1/net/dev)를 주입받는다 -- mountinfo 와 같은 관례.
        for line in read_text(net_dev_path).splitlines()[2:]:
            name, _, rest = line.partition(":")
            name = name.strip()
            # lo 제외는 두 경로 모두에 남긴다: 필터 시엔 lo 도 가상이라 중복이지만
            # 무해하고, 필터가 꺼진 배포에서는 이것만이 기존 동작을 지킨다.
            if not rest or name == "lo":
                continue
            if filtering and name in virtual:
                continue
            fields = rest.split()
            rx += int(fields[0])
            tx += int(fields[8])
        metrics["network_rx_bytes"], metrics["network_tx_bytes"] = rx, tx
    except Exception:
        pass
    return metrics
