"""도메인 모델: 상태머신(스펙 §4), 검증 규칙, 옵션 allowlist. 이 모듈은 DB를 모른다."""
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
