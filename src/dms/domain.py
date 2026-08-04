"""도메인 모델: 상태머신(스펙 §4), 검증 규칙, 옵션 allowlist. 이 모듈은 DB를 모른다."""
import hashlib
import json
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


_CHMOD_ITEM_RE = re.compile(r"[DF]?[0-7]{1,4}$")
_CHOWN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9._-]{0,63})?(:[A-Za-z_][A-Za-z0-9._-]{0,63})?$")

_BOOL = ("bool",)
_OPTION_SPECS: dict[Operation, dict[str, tuple]] = {
    # dscan(포크)이 실제 지원하는 플래그만 노출한다: --top-k <N>, --verbose, --quiet.
    # (이전의 summary_only/follow_symlinks/one_file_system/max_depth는 dscan에
    #  대응 플래그가 없어 수락돼도 무효였으므로 제거 — unknown_option으로 거부된다.)
    Operation.SCAN: {
        "top_k": ("int", 1, 1_000_000),
        "verbose": _BOOL, "quiet": _BOOL,
    },
    Operation.SYNC: {
        "delete": _BOOL, "contents": _BOOL, "direct": _BOOL,
        "open_noatime": _BOOL, "quiet": _BOOL,
        "batch_files": ("int", 1, 1_000_000),
        "bufsize": ("int", 4096, 1_073_741_824),
        "chmod": ("chmod",), "chown": ("chown",),
    },
    Operation.RM: {"recursive": _BOOL, "stat": _BOOL, "lite": _BOOL, "quiet": _BOOL},
}


def validate_options(operation: Operation, options: dict) -> dict:
    spec = _OPTION_SPECS[Operation(operation)]
    out: dict = {}
    for key, value in (options or {}).items():
        rule = spec.get(key)
        if rule is None:
            raise DomainValidationError("unknown_option", key)
        kind = rule[0]
        if kind == "bool":
            if not isinstance(value, bool):
                raise DomainValidationError("invalid_option", f"{key} must be bool")
        elif kind == "int":
            lo, hi = rule[1], rule[2]
            if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
                raise DomainValidationError("invalid_option", f"{key} must be int {lo}..{hi}")
        elif kind == "chmod":
            if not isinstance(value, str) or not all(
                    _CHMOD_ITEM_RE.fullmatch(p) for p in value.split(",")):
                raise DomainValidationError("invalid_option", f"bad chmod {value!r}")
        elif kind == "chown":
            if not isinstance(value, str) or not value or not _CHOWN_RE.fullmatch(value):
                raise DomainValidationError("invalid_option", f"bad chown {value!r}")
        out[key] = value
    if Operation(operation) is Operation.RM and out.get("stat") and out.get("lite"):
        raise DomainValidationError("invalid_option", "stat and lite are mutually exclusive")
    if Operation(operation) is Operation.SCAN and out.get("verbose") and out.get("quiet"):
        raise DomainValidationError("invalid_option", "verbose and quiet are mutually exclusive")
    return out


def option_fingerprint(options: dict) -> str:
    payload = json.dumps(options or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def build_resource_key(operation, *, storage=None, source_storage=None,
                       destination_storage=None, source=None, destination=None,
                       target=None, fingerprint: str) -> str:
    op = Operation(operation)
    if op is Operation.SYNC:
        return f"data.sync:{source_storage}:{source}:{destination_storage}:{destination}:{fingerprint}"
    return f"data.{op.value}:{storage}:{target}:{fingerprint}"
