from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class LifecycleState(StrEnum):
    RECEIVED = "Received"
    PERSISTED = "Persisted"
    PLANNING = "Planning"
    PLANNED = "Planned"
    CLAIMED = "Claimed"
    RUNNING = "Running"
    APPLYING = "Applying"
    BLOCKED = "Blocked"
    VERIFYING = "Verifying"
    STALE_CLAIM = "StaleClaim"
    RECOVERY_NEEDED = "RecoveryNeeded"
    AUTHENTICATION_REJECTED = "AuthenticationRejected"
    AUTHORIZATION_FAILED = "AuthorizationFailed"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    TIMED_OUT = "TimedOut"
    CANCELLED = "Cancelled"
    CONFLICT = "Conflict"
    REJECTED = "Rejected"
    VERIFICATION_FAILED = "VerificationFailed"
    UNKNOWN_AFTER_SIDE_EFFECT = "UnknownAfterSideEffect"
    BACKEND_APPLY_FAILED = "BackendApplyFailed"


TERMINAL_LIFECYCLE_STATES = {
    LifecycleState.AUTHENTICATION_REJECTED.value,
    LifecycleState.AUTHORIZATION_FAILED.value,
    LifecycleState.SUCCEEDED.value,
    LifecycleState.FAILED.value,
    LifecycleState.TIMED_OUT.value,
    LifecycleState.CANCELLED.value,
    LifecycleState.CONFLICT.value,
    LifecycleState.REJECTED.value,
    LifecycleState.BACKEND_APPLY_FAILED.value,
}


class DataJobState(StrEnum):
    PENDING = "Pending"
    AUTHORIZATION_FAILED = "AuthorizationFailed"
    PREFLIGHT_RUNNING = "PreflightRunning"
    PREFLIGHT_FAILED = "PreflightFailed"
    PREVIEW_RUNNING = "PreviewRunning"
    PREVIEW_SUCCEEDED = "PreviewSucceeded"
    PREVIEW_EXPIRED = "PreviewExpired"
    CONFIRM_PENDING = "ConfirmPending"
    CONFIRMED = "Confirmed"
    SCHEDULED = "Scheduled"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    TIMED_OUT = "TimedOut"


class WorkerRole(StrEnum):
    RM = "RM"
    DM = "DM"


class ResourceKind(StrEnum):
    FILESYSTEM = "filesystem"
    KUBERNETES_NAMESPACE_QUOTA = "kubernetes_namespace_quota"
    DATA_JOB = "data_job"
    STORAGE_MAPPING = "storage_mapping"
    DEFAULT_QUOTA_POLICY = "default_quota_policy"
    IDENTITY_MAPPING = "identity_mapping"


class OperationKind(StrEnum):
    FILESYSTEM_CREATE = "filesystem.create"
    FILESYSTEM_UPDATE = "filesystem.update"
    FILESYSTEM_BLOCK = "filesystem.block"
    FILESYSTEM_INITIALIZE = "filesystem.initialize"
    FILESYSTEM_DELETE = "filesystem.delete"
    FILESYSTEM_ASSIGN_QUOTA = "filesystem.assign_quota"
    FILESYSTEM_IMPORT = "filesystem.import"
    FILESYSTEM_CHECK = "filesystem.consistency_check"
    FILESYSTEM_EXPIRATION_SWEEP = "filesystem.expiration_sweep"
    K8S_QUOTA_CREATE = "kubernetes.namespace_quota.create"
    K8S_QUOTA_UPDATE = "kubernetes.namespace_quota.update"
    K8S_QUOTA_BLOCK = "kubernetes.namespace_quota.block"
    K8S_QUOTA_DELETE = "kubernetes.namespace_quota.delete"
    K8S_QUOTA_SYNC = "kubernetes.namespace_quota.sync"
    K8S_QUOTA_CHECK = "kubernetes.namespace_quota.consistency_check"
    K8S_QUOTA_AUDIT = "kubernetes.namespace_quota.audit"
    DATA_SYNC = "data.sync"
    DATA_RM = "data.rm"
    DATA_SCAN = "data.scan"
    DATA_CANCEL = "data.cancel"
    IDENTITY_UPSERT = "identity.upsert"
    IDENTITY_REFRESH = "identity.refresh"
    IDENTITY_DISABLE = "identity.disable"


class IdentityMappingStatus(StrEnum):
    ACTIVE = "Active"
    DISABLED = "Disabled"
    NEEDS_REVIEW = "NeedsReview"
    STALE = "Stale"


class StorageMappingSanityStatus(StrEnum):
    READY = "Ready"
    DEGRADED = "Degraded"
    UNKNOWN = "Unknown"
    FAILED = "Failed"


class ResourcePayload(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class RequestEnvelope(BaseModel):
    requester_id: str
    operation: OperationKind
    resource_kind: ResourceKind
    resource_key: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("requester_id")
    @classmethod
    def requester_id_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("requester_id is required")
        return value


class DataJobRequest(BaseModel):
    requester_id: str
    storage_name: str
    target_path: str | None = None
    source_path: str | None = None
    destination_path: str | None = None
    priority: int = 100
    options: dict[str, Any] = Field(default_factory=dict)


class AgentReport(BaseModel):
    schema_version: str = "phase3.v1"
    reported_at: str | None = None
    cluster_name: str
    node_name: str
    node_uid: str
    worker_role: WorkerRole
    mounts: list[dict[str, Any]] = Field(default_factory=list)
    csi: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[Any] = Field(default_factory=list)
    credentials: list[Any] = Field(default_factory=list)
    networks: list[dict[str, Any]] = Field(default_factory=list)
    identity_evidence: dict[str, Any] = Field(default_factory=dict)


class IdentityMappingInput(BaseModel):
    requester_id: str
    identity_provider: str
    posix_username: str
    expected_uid: int | None = None
    expected_primary_gid: int | None = None
    expected_groups: list[str] = Field(default_factory=list)
    uid: int | None = None
    gid: int | None = None
    groups: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_phase1_identity_fields(self) -> "IdentityMappingInput":
        if self.expected_uid is None and self.uid is not None:
            self.expected_uid = self.uid
        if self.expected_primary_gid is None and self.gid is not None:
            self.expected_primary_gid = self.gid
        if not self.expected_groups and self.groups:
            self.expected_groups = list(self.groups)
        return self


class StorageMappingInput(BaseModel):
    storage_name: str
    backend_template: dict[str, Any]
    cluster_name: str | None = None
    storage_class_name: str | None = None
    version: int = 1
    sanity_status: str = "Unknown"


class DefaultQuotaPolicyInput(BaseModel):
    resource_kind: ResourceKind
    resource_type: str
    quota: dict[str, Any]


_BASENAME = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_storage_root_basename(field_name: str, value: str) -> None:
    if (
        value in {"", ".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or value.startswith("-")
        or not _BASENAME.match(value)
    ):
        raise ValueError(f"{field_name} must be a storage-root basename")


@dataclass(frozen=True)
class FilesystemResourceKey:
    storage_name: str
    directory_name: str

    def __post_init__(self) -> None:
        for field_name, value in {
            "storage_name": self.storage_name,
            "directory_name": self.directory_name,
        }.items():
            validate_storage_root_basename(field_name, value)

    def as_string(self) -> str:
        return f"{self.storage_name}:{self.directory_name}"


@dataclass(frozen=True)
class KubernetesNamespaceQuotaKey:
    cluster_name: str
    namespace_name: str

    def __post_init__(self) -> None:
        for field_name, value in {
            "cluster_name": self.cluster_name,
            "namespace_name": self.namespace_name,
        }.items():
            if value in {"", ".", ".."} or "/" in value:
                raise ValueError(f"{field_name} must not contain path separators")

    def as_string(self) -> str:
        return f"{self.cluster_name}:{self.namespace_name}"


def reject_unsafe_relative_path(path: str) -> None:
    if path.startswith("/") or "\x00" in path:
        raise ValueError("path must be storage-relative")
    parts = [part for part in path.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError("path traversal is not allowed")


def validate_data_job_paths(request: DataJobRequest, operation: OperationKind) -> None:
    raw_options = request.options.get("raw_options") or request.options.get("command_line")
    if raw_options:
        raise ValueError("raw command-line option strings are not accepted")
    if operation == OperationKind.DATA_SYNC:
        required = [request.source_path, request.destination_path]
    else:
        required = [request.target_path]
    for path in required:
        if path is None:
            raise ValueError("required storage-relative path is missing")
        reject_unsafe_relative_path(path)
