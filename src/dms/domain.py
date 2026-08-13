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
# chown 파트는 「이름 또는 숫자 uid/gid」다. 숫자를 허용하는 근거: dsync --chown 은
# 숫자를 받는다 — 특권 판정에 따른 자동 주입(execution_manifests._auto_chown)이
# 이미 uid:gid 숫자를 넣는 것이 증명. 숫자 상한 10자리는 uid_t 32비트(최대 10자리)
# 커버. 이름 규칙(문자 시작·최대 64자)과 빈 파트 규칙(":gid" 허용, "user:" 거부)은
# 기존 정규식 의미 그대로 — 숫자 확장이 경계를 넓히지 않는다.
# frontend/src/features/jobs/optionRules.ts 의 CHOWN_RE 가 이 정규식의 미러다(발산 금지).
_CHOWN_PART = r"(?:[A-Za-z_][A-Za-z0-9._-]{0,63}|[0-9]{1,10})"
_CHOWN_RE = re.compile(rf"({_CHOWN_PART})?(:{_CHOWN_PART})?$")

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


def build_data_payload(operation, *, storage=None, target=None, source_storage=None,
                       source=None, destination_storage=None, destination=None,
                       options: dict) -> tuple[dict, str]:
    op = Operation(operation)
    opts = validate_options(op, options)
    fp = option_fingerprint(opts)
    if op is Operation.SYNC:
        src, dst = validate_sync_paths(source or "", destination or "")
        if not source_storage or not destination_storage:
            raise DomainValidationError("missing_storage")
        payload = {"source_storage": source_storage, "source": src,
                   "destination_storage": destination_storage, "destination": dst,
                   "options": opts}
        key = build_resource_key(op, source_storage=source_storage, source=src,
                                 destination_storage=destination_storage,
                                 destination=dst, fingerprint=fp)
        return payload, key
    if op is Operation.RM:
        if not storage:
            raise DomainValidationError("missing_storage")
        tgt = validate_rm_target(target or "", opts)
        return ({"storage": storage, "target": tgt, "options": opts},
                build_resource_key(op, storage=storage, target=tgt, fingerprint=fp))
    # scan
    if not storage:
        raise DomainValidationError("missing_storage")
    tgt = validate_relative_path(target or "")
    return ({"storage": storage, "target": tgt, "options": opts},
            build_resource_key(op, storage=storage, target=tgt, fingerprint=fp))


_OP_POLICY = {"scan": "scan", "rm": "rm", "sync": "dsync"}


def resolve_priority(repos, operation: str, requested: str | None) -> str:
    # 클라이언트가 명시하면 그 값이 이긴다. 생략하면 정책의 기본값, 그것도 없으면 mid.
    # sync는 제출 시점에 도구(dsync/nsync)가 정해지지 않으므로 dsync 정책을 대표로 읽는다.
    if requested is not None:
        return requested
    policy = repos.control.get_policy(_OP_POLICY.get(operation, ""))
    return (policy or {}).get("default_priority") or "mid"


def validate_batch(operation, max_concurrency, items, *,
                   priority: str | None = None,
                   node_count: int | None = None) -> None:
    if operation not in (Operation.SCAN.value, Operation.SYNC.value):
        raise DomainValidationError("invalid_batch_operation", operation)
    # 상한 64: 임의 위생값(거대값이면 orchestrator 가 전 item 을 한 틱에 materialize).
    if not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool) \
            or not 1 <= max_concurrency <= 64:
        raise DomainValidationError("invalid_max_concurrency")
    if not items:
        raise DomainValidationError("empty_batch")
    # 단일 스토리지 강제: legacy 운영 관례 — 한 배치는 한 스토리지 대상이 정상이고,
    # 행별 혼합은 오입력(CSV 열 밀림 등) 신호다. 누락 storage(None)는 이후 item 별
    # build_data_payload 의 missing_storage 가 잡는다 — 여기서는 종류 수만 본다.
    if operation == Operation.SYNC.value:
        pairs = {((i or {}).get("source_storage"), (i or {}).get("destination_storage"))
                 for i in items}
        if len(pairs) > 1:
            raise DomainValidationError("batch_storage_mixed", f"{sorted(map(str, pairs))}")
    else:
        storages = {(i or {}).get("storage") for i in items}
        if len(storages) > 1:
            raise DomainValidationError("batch_storage_mixed", f"{sorted(map(str, storages))}")
    if priority is not None and priority not in PRIORITIES:
        raise DomainValidationError("invalid_priority", priority)
    # node_count 상한 1024 는 API 위생 상한일 뿐 — 실제 상한은 planner 가
    # min(정책 max_nodes, 요청값) 으로 캡한다(요청은 정책을 줄일 수만 있다).
    if node_count is not None and (
            not isinstance(node_count, int) or isinstance(node_count, bool)
            or not 1 <= node_count <= 1024):
        raise DomainValidationError("invalid_node_count", repr(node_count))
