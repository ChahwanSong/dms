"""노드 프로브. 시스템 접근(파일/명령)은 전부 파라미터 주입 — 순수 로직만 이 모듈에 둔다."""
import os

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
