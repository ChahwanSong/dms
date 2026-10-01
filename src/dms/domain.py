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
# chown 은 **숫자 uid/gid 만**(2026-10-01 사용자 결정). 이름은 잡 컨테이너에서 LDAP 으로 풀리지 않는다 -- 잡
# 이미지에 LDAP NSS 가 없어 dsync/nsync 의 getpwnam/getgrnam 이 컨테이너 자체 파일만 본다:
#   - LDAP 사용자 이름은 못 찾아 도구가 종료(미리보기가 사유 없는 preview_failed),
#   - users·staff 같은 이름은 데비안 기본 gid(100·50)로 풀려 미리보기는 통과하고 실행에서 실패/무시,
#   - root 실행은 실행 신원 이름을 uid 0 으로 /etc/passwd 에 덧붙이므로(execution_manifests 의 passwd 줄)
#     그 이름을 chown 에 쓰면 목적지가 **조용히 root 소유**가 된다.
# 숫자 상한 10자리는 uid_t 32비트 커버, 빈 파트 규칙(":gid" 허용, "user:" 거부)은 그대로. 이름 모양
# (_CHOWN_NAMED_RE 에만 맞음)은 형식 오류(invalid_option)와 구분해 chown_name_not_supported 로 알린다 --
# 무엇을 고치면 되는지(숫자로) 사유가 말하게. 판정은 chown_problem 하나: 제출 검증(validate_options)·
# 배치 자식 생성(build_data_payload 경유)·stepper 제출 관문(_build_spec)이 모두 이걸 쓴다.
# frontend/src/features/jobs/optionRules.ts 의 CHOWN_RE 가 _CHOWN_RE 의 미러다(발산 금지).
_CHOWN_NUM = r"[0-9]{1,10}"
_CHOWN_RE = re.compile(rf"({_CHOWN_NUM})?(:{_CHOWN_NUM})?$")
_CHOWN_NAME_OR_NUM = r"(?:[A-Za-z_][A-Za-z0-9._-]{0,63}|[0-9]{1,10})"
_CHOWN_NAMED_RE = re.compile(rf"({_CHOWN_NAME_OR_NUM})?(:{_CHOWN_NAME_OR_NUM})?$")


def chown_problem(value) -> "str | None":
    """chown 옵션 값 -> None(숫자 uid:gid 정상) 또는 사유 코드. 이름이 섞이면 chown_name_not_supported,
    그 밖의 모양(빈 값·비문자열·잘못된 구분)은 invalid_option."""
    if not isinstance(value, str) or not value:
        return "invalid_option"
    if _CHOWN_RE.fullmatch(value):
        return None
    if _CHOWN_NAMED_RE.fullmatch(value):
        return "chown_name_not_supported"
    return "invalid_option"

_BOOL = ("bool",)
_OPTION_SPECS: dict[Operation, dict[str, tuple]] = {
    # dscan(포크 1b93d54)이 실제 지원하는 플래그만 노출한다: --batch-files <N>,
    # --broken-limit <N>, --verbose, --quiet.
    # (이전의 summary_only/follow_symlinks/one_file_system/max_depth는 dscan에
    #  대응 플래그가 없어 수락돼도 무효였으므로 제거 — unknown_option으로 거부된다.
    #  top_k도 같은 길: 신버전 dscan이 top-K 수집 기능 자체를 삭제했다(스트리밍
    #  재작성, mpifileutils 커밋 a0ef9a7→1b93d54) — unknown_option으로 거부된다.)
    # 실측(dscan.c:1283-1293): 두 값 옵션 다 0 허용 — batch_files 0 = 배칭 비활성,
    # broken_limit 0 = 파손 경로 표본 미보관(broken_paths_total 총계는 항상 정확).
    # 도구 파싱(parse_uint64)은 uint64 전체를 받으므로 상한은 DMS 위생 상한이다:
    # batch_files 10억(진행 회계 단위 — 리포트를 키우지 않는다), broken_limit
    # 10,000(경로 문자열이 리포트에 그대로 실린다 — stats 읽기 상한 256 KiB 위생).
    Operation.SCAN: {
        "batch_files": ("int", 0, 1_000_000_000),
        "broken_limit": ("int", 0, 10_000),
        "verbose": _BOOL, "quiet": _BOOL,
    },
    Operation.SYNC: {
        "delete": _BOOL, "contents": _BOOL, "direct": _BOOL,
        "open_noatime": _BOOL, "quiet": _BOOL,
        # batch_files 상한 1,000만(사용자 조정 2026-08-16): 대규모 sync 에서 100만
        # 단위 배치가 좁았다. 도구 파싱(parse_uint64)은 uint64 전체를 받으므로 이
        # 상한은 도구 제약이 아니라 DMS 위생 상한이다.
        #
        # 하한 0(2026-09-17): 서버가 기본값(_OPTION_DEFAULTS, 생략 = 100만)을 박기
        # 시작하면서 "키 생략 = 배칭 안 함" 표현이 사라졌다 -- 배칭을 끄는 유일한
        # 표현은 이제 dsync 의미 그대로 **0 명시**(mfu_flist_copy.c:3361 기본값)다.
        # 예전엔 하한 1 + 키 생략으로 표현했다(표현이 둘이면 요약·화면이 갈린다는
        # 이유였고, 이제는 명시 0 하나뿐이라 같은 원칙이 유지된다).
        "batch_files": ("int", 0, 10_000_000),
        "bufsize": ("int", 4096, 1_073_741_824),
        "chmod": ("chmod",), "chown": ("chown",),
    },
    Operation.RM: {"recursive": _BOOL, "stat": _BOOL, "lite": _BOOL, "quiet": _BOOL},
}

# 서버 기본값(사용자 결정 2026-09-17): 키가 **생략**되면 여기 값이 옵션에 박혀 잡에
# 실린다 -- 포탈 프리필(optionRules.SYNC_INT_FIELDS/SCAN_INT_FIELDS)과 같은 값이라
# 폼을 거치든 API 를 직접 치든 같은 동작이다. 값의 「왜」:
#   sync  batch_files 1,000,000 -- 도구 기본(0 = 배칭 안 함)과 **다른** 정책 결정
#         (대규모 sync 메모리 안정성). 끄려면 0 을 명시한다(하한 0).
#         bufsize 4,194,304 -- 도구 기본(MFU_BUFFER_SIZE 4 MiB)과 같은 값의 명시.
#   scan  batch_files 1,000,000 · broken_limit 100 -- 둘 다 dscan 기본과 같은 값의
#         명시(dscan.c:1283-1293). 요청 상세·리포트에 어떤 값으로 돌았는지 남는다.
# 검증(_OPTION_SPECS) **뒤에** 채운다 -- 기본값 자체가 스펙 범위 안임을 아래
# 테스트(test_domain_option_defaults)가 고정한다. rm 은 기본값이 없다(recursive 는
# 동의 게이트라 기본으로 박으면 안 된다 -- validate_rm_target).
_OPTION_DEFAULTS: dict[Operation, dict] = {
    Operation.SCAN: {"batch_files": 1_000_000, "broken_limit": 100},
    Operation.SYNC: {"batch_files": 1_000_000, "bufsize": 4_194_304},
    Operation.RM: {},
}


def option_defaults(operation) -> dict:
    return dict(_OPTION_DEFAULTS[Operation(operation)])


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
            problem = chown_problem(value)
            if problem == "chown_name_not_supported":
                raise DomainValidationError("chown_name_not_supported",
                                            f"chown must be numeric uid:gid, got {value!r}")
            if problem is not None:
                raise DomainValidationError("invalid_option", f"bad chown {value!r}")
        out[key] = value
    if Operation(operation) is Operation.RM and out.get("stat") and out.get("lite"):
        raise DomainValidationError("invalid_option", "stat and lite are mutually exclusive")
    if Operation(operation) is Operation.SCAN and out.get("verbose") and out.get("quiet"):
        raise DomainValidationError("invalid_option", "verbose and quiet are mutually exclusive")
    for key, value in _OPTION_DEFAULTS[Operation(operation)].items():
        out.setdefault(key, value)
    return out


def option_fingerprint(options: dict) -> str:
    payload = json.dumps(options or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def build_resource_key(operation, *, storage=None, source_storage=None,
                       destination_storage=None, source=None, destination=None,
                       target=None, fingerprint: str) -> str:
    """충돌 판정 키. 파괴적 op(sync·rm)는 **옵션 지문을 넣지 않는다**(슬라이스 36):
    같은 대상 데이터를 쓰는 두 sync 는 옵션(chown·bufsize 등)이 달라도 같은 자원을
    놓고 경쟁한다 -- 지문이 키를 갈라놓으면 동시 실행돼 서로의 쓰기를 간섭한다.
    scan 은 비파괴(읽기 전용)라 옵션이 다른 동시 실행이 무해하고, 결과 리포트도
    옵션에 따라 다르므로 지문을 유지한다(동일 스캔의 중복만 막는다)."""
    op = Operation(operation)
    if op is Operation.SYNC:
        return f"data.sync:{source_storage}:{source}:{destination_storage}:{destination}"
    if op is Operation.RM:
        return f"data.rm:{storage}:{target}"
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
                   node_count: int | None = None,
                   procs_per_node: int | None = None) -> None:
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
    # procs_per_node 도 node_count 와 같은 규칙 — 상한 1024 는 위생값일 뿐이고
    # 실제 상한은 planner 가 min(정책 procs_per_node, 요청값) 으로 캡한다.
    if procs_per_node is not None and (
            not isinstance(procs_per_node, int) or isinstance(procs_per_node, bool)
            or not 1 <= procs_per_node <= 1024):
        raise DomainValidationError("invalid_procs_per_node", repr(procs_per_node))
