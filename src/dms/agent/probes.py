"""노드 프로브. 시스템 접근(파일/명령)은 전부 파라미터 주입 — 순수 로직만 이 모듈에 둔다."""
import grp
import os
import pwd
import shutil
import subprocess

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


def probe_tools(names, *, which=shutil.which, run=subprocess.run):
    results = []
    for name in names:
        path = which(name)
        if not path:
            results.append({"name": name, "status": "Missing", "path": None,
                            "version": None, "reason": "tool_not_found"})
            continue
        version, reason = None, None
        try:
            proc = run([path, "--version"], capture_output=True, text=True, timeout=5)
            first_line = (proc.stdout or proc.stderr or "").splitlines()
            version = first_line[0].strip() if first_line else None
        except Exception as exc:  # fail-soft: 버전 실패가 도구 존재를 부정하지 않는다
            reason = f"version_probe_failed:{type(exc).__name__}"
        results.append({"name": name, "status": "Ready", "path": path,
                        "version": version, "reason": reason})
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


def probe_os_metrics(storages, *, read_text, statvfs=os.statvfs):
    metrics = {"load1": None, "load5": None, "load15": None,
               "memory_total_kb": None, "memory_available_kb": None,
               "disks": [], "network_rx_bytes": None, "network_tx_bytes": None}
    try:
        parts = read_text("/proc/loadavg").split()
        metrics["load1"], metrics["load5"], metrics["load15"] = (
            float(parts[0]), float(parts[1]), float(parts[2]))
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
        rx = tx = 0
        for line in read_text("/proc/net/dev").splitlines()[2:]:
            name, _, rest = line.partition(":")
            if not rest or name.strip() == "lo":
                continue
            fields = rest.split()
            rx += int(fields[0])
            tx += int(fields[8])
        metrics["network_rx_bytes"], metrics["network_tx_bytes"] = rx, tx
    except Exception:
        pass
    return metrics
