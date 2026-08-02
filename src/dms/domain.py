"""도메인 모델: 상태머신(스펙 §4), 검증 규칙, 옵션 allowlist. 이 모듈은 DB를 모른다."""
import posixpath
import re

from enum import StrEnum


class RequestState(StrEnum):
    PENDING = "Pending"
    PLANNED = "Planned"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    REJECTED = "Rejected"
    CONFLICT = "Conflict"
    CANCELLED = "Cancelled"


TERMINAL_REQUEST_STATES = frozenset({
    RequestState.SUCCEEDED, RequestState.FAILED, RequestState.REJECTED,
    RequestState.CONFLICT, RequestState.CANCELLED,
})


class DataJobState(StrEnum):
    PENDING = "Pending"
    PREFLIGHT = "Preflight"
    PREVIEW_RUNNING = "PreviewRunning"
    CONFIRM_PENDING = "ConfirmPending"
    EXECUTING = "Executing"
    RUNNING = "Running"           # scan 실행 단계
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    TIMED_OUT = "TimedOut"
    CANCELLED = "Cancelled"
    REJECTED = "Rejected"
    PREVIEW_EXPIRED = "PreviewExpired"


TERMINAL_DATA_JOB_STATES = frozenset({
    DataJobState.SUCCEEDED, DataJobState.FAILED, DataJobState.TIMED_OUT,
    DataJobState.CANCELLED, DataJobState.REJECTED, DataJobState.PREVIEW_EXPIRED,
})


class Operation(StrEnum):
    SCAN = "scan"
    SYNC = "sync"
    RM = "rm"


class Tool(StrEnum):
    DSCAN = "dscan"
    DSYNC = "dsync"
    NSYNC = "nsync"
    DRM = "drm"


PRIORITIES = ("low", "mid", "high")
PRIORITY_CLASS = {"low": "dms-low", "mid": "dms-mid", "high": "dms-high"}

ROLE_USER = "user"
ROLE_ADMIN = "admin"


class DomainValidationError(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


_USERNAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9._-]{0,63}$")


def validate_relative_path(path: str) -> str:
    if not path or path.startswith("/") or "\x00" in path:
        raise DomainValidationError("unsafe_path", repr(path))
    if any(part == ".." for part in path.split("/")):
        raise DomainValidationError("unsafe_path", repr(path))
    normalized = posixpath.normpath(path)
    if normalized == ".":
        raise DomainValidationError("unsafe_path", repr(path))
    return normalized


def validate_sync_paths(source: str, destination: str) -> tuple[str, str]:
    src = validate_relative_path(source)
    dst = validate_relative_path(destination)
    if dst == src or dst.startswith(src + "/"):
        raise DomainValidationError("sync_destination_inside_source", f"{src} -> {dst}")
    return src, dst


def validate_rm_target(target: str, options: dict) -> str:
    if target in ("", "."):
        raise DomainValidationError("rm_root_forbidden", "managed_root itself")
    normalized = validate_relative_path(target)
    if options.get("recursive") is not True:
        raise DomainValidationError("rm_recursive_required", "options.recursive must be true")
    return normalized


def validate_owner_username(username: str) -> str:
    if not _USERNAME_RE.fullmatch(username):
        raise DomainValidationError("invalid_owner_username", repr(username))
    return username
