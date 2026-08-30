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


def probe_mounts(storages, *, mountinfo_text, isdir=os.path.isdir, access=os.access):
    points = parse_mountinfo(mountinfo_text)
    results = []
    for storage in storages:
        path = storage["mount_path"]
        exists = bool(isdir(path))
        is_mountpoint = path in points
        readable = exists and bool(access(path, os.R_OK)) and bool(access(path, os.X_OK))
        writable = exists and bool(access(path, os.W_OK))
        if not exists:
            status, reason = "Missing", "missing_mount_path"
        elif not is_mountpoint:
            status, reason = "Missing", "not_a_mountpoint"
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


def probe_artifact_base(path, *, isdir=os.path.isdir, access=os.access):
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
    exists = bool(isdir(path))
    return {"path": path, "exists": exists,
            "writable": exists and bool(access(path, os.W_OK))}


def probe_os_metrics(storages, *, read_text, statvfs=os.statvfs,
                     net_dev_path="/proc/net/dev", virtual_net_path=""):
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
            vfs = statvfs(storage["mount_path"])
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
