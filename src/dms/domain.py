from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    FILESYSTEM_SYNC = "filesystem.sync"
    FILESYSTEM_EXPIRATION_SWEEP = "filesystem.expiration_sweep"
    K8S_QUOTA_CREATE = "kubernetes.namespace_quota.create"
    K8S_QUOTA_UPDATE = "kubernetes.namespace_quota.update"
    K8S_QUOTA_BLOCK = "kubernetes.namespace_quota.block"
    K8S_QUOTA_DELETE = "kubernetes.namespace_quota.delete"
    K8S_QUOTA_SYNC = "kubernetes.namespace_quota.sync"
    K8S_QUOTA_CHECK = "kubernetes.namespace_quota.consistency_check"
    K8S_QUOTA_AUDIT = "kubernetes.namespace_quota.audit"
    K8S_QUOTA_IMPORT = "kubernetes.namespace_quota.import"
    K8S_QUOTA_EXPIRATION_SWEEP = "kubernetes.namespace_quota.expiration_sweep"
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


class DataPathTarget(BaseModel):
    storage_name: str
    path: str

    @field_validator("storage_name", "path")
    @classmethod
    def value_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value is required")
        return value


class DataJobResources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_count: int | None = None
    source_node_count: int | None = None
    destination_node_count: int | None = None
    processes_per_node: int | None = None

    @field_validator(
        "node_count",
        "source_node_count",
        "destination_node_count",
        "processes_per_node",
    )
    @classmethod
    def positive_int_or_none(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or value < 1:
            raise ValueError("resource counts must be positive integers")
        return value

    @model_validator(mode="after")
    def validate_role_shape(self) -> "DataJobResources":
        role_fields = self.source_node_count is not None or self.destination_node_count is not None
        if role_fields and self.node_count is not None:
            raise ValueError("node_count is mutually exclusive with source/destination node counts")
        if (self.source_node_count is None) != (self.destination_node_count is None):
            raise ValueError("source_node_count and destination_node_count must be provided together")
        return self


class DataJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requester_id: str
    storage_name: str | None = None
    target: DataPathTarget | None = None
    source: DataPathTarget | None = None
    destination: DataPathTarget | None = None
    target_path: str | None = None
    source_path: str | None = None
    destination_path: str | None = None
    priority: int | str = 100
    options: dict[str, Any] = Field(default_factory=dict)
    resources: DataJobResources | None = None
    memo: str | None = None

    @model_validator(mode="after")
    def normalize_compatibility_fields(self) -> "DataJobRequest":
        if (
            self.target is not None
            and self.storage_name is not None
            and self.storage_name != self.target.storage_name
        ):
            raise ValueError("storage_name does not match target.storage_name")
        if (
            self.target is not None
            and self.target_path is not None
            and self.target_path != self.target.path
        ):
            raise ValueError("target_path does not match target.path")
        if self.target is not None:
            self.storage_name = self.target.storage_name
            self.target_path = self.target.path
        if self.source is not None:
            if self.source_path is not None and self.source_path != self.source.path:
                raise ValueError("source_path does not match source.path")
            self.source_path = self.source.path
        if self.destination is not None:
            if self.destination_path is not None and self.destination_path != self.destination.path:
                raise ValueError("destination_path does not match destination.path")
            self.destination_path = self.destination.path
        if self.storage_name is not None:
            if self.source is not None and self.source.storage_name != self.storage_name:
                raise ValueError("storage_name does not match source.storage_name")
            if (
                self.destination is not None
                and self.destination.storage_name != self.storage_name
            ):
                raise ValueError("storage_name does not match destination.storage_name")
            if self.source is None and self.source_path is not None:
                self.source = DataPathTarget(
                    storage_name=self.storage_name, path=self.source_path
                )
            if self.destination is None and self.destination_path is not None:
                self.destination = DataPathTarget(
                    storage_name=self.storage_name, path=self.destination_path
                )
        return self


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


class DataManagementPolicyInput(BaseModel):
    operation: str
    default_worker_nodes: int | None = None
    default_source_nodes: int | None = None
    default_destination_nodes: int | None = None
    max_worker_nodes: int | None = None
    max_source_nodes: int | None = None
    max_destination_nodes: int | None = None
    default_processes_per_node: int = 3
    max_processes_per_node: int = 10
    default_queue: str | None = None
    default_priority_class: str | None = None
    default_timeout_seconds: int | None = None
    enabled: bool = True

    @field_validator(
        "default_worker_nodes",
        "default_source_nodes",
        "default_destination_nodes",
        "max_worker_nodes",
        "max_source_nodes",
        "max_destination_nodes",
        "default_processes_per_node",
        "max_processes_per_node",
        "default_timeout_seconds",
    )
    @classmethod
    def positive_int_or_none(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or value < 1:
            raise ValueError("policy numeric values must be positive integers")
        return value

    @field_validator("operation")
    @classmethod
    def supported_operation(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"scan", "rm", "dsync", "nsync"}:
            raise ValueError("operation must be one of: scan, rm, dsync, nsync")
        return normalized

    @model_validator(mode="after")
    def validate_policy_shape(self) -> "DataManagementPolicyInput":
        if self.max_processes_per_node < self.default_processes_per_node:
            raise ValueError("max_processes_per_node must be >= default_processes_per_node")
        if self.operation == "nsync":
            for name in (
                "default_source_nodes",
                "default_destination_nodes",
                "max_source_nodes",
                "max_destination_nodes",
            ):
                if getattr(self, name) is None:
                    raise ValueError(f"{name} is required for nsync policy")
            if self.default_worker_nodes is not None or self.max_worker_nodes is not None:
                raise ValueError("nsync policy uses source/destination node counts")
            if self.max_source_nodes < self.default_source_nodes:
                raise ValueError("max_source_nodes must be >= default_source_nodes")
            if self.max_destination_nodes < self.default_destination_nodes:
                raise ValueError("max_destination_nodes must be >= default_destination_nodes")
            return self
        if self.default_worker_nodes is None or self.max_worker_nodes is None:
            raise ValueError("default_worker_nodes and max_worker_nodes are required")
        if self.default_source_nodes is not None or self.default_destination_nodes is not None:
            raise ValueError("source/destination node counts are only valid for nsync")
        if self.max_source_nodes is not None or self.max_destination_nodes is not None:
            raise ValueError("source/destination max counts are only valid for nsync")
        if self.max_worker_nodes < self.default_worker_nodes:
            raise ValueError("max_worker_nodes must be >= default_worker_nodes")
        return self


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


DATA_SCAN_OPTION_TYPES: dict[str, type | tuple[type, ...]] = {
    "summary_only": bool,
    "max_depth": int,
    "follow_symlinks": bool,
    "one_file_system": bool,
}

DATA_SYNC_OPTION_TYPES: dict[str, type | tuple[type, ...]] = {
    "delete": bool,
    "batch_files": int,
    "contents": bool,
    "direct": bool,
    "open_noatime": bool,
    "bufsize": int,
    "quiet": bool,
}

DATA_RM_OPTION_TYPES: dict[str, type | tuple[type, ...]] = {
    "recursive": bool,
    "stat": bool,
    "lite": bool,
    "quiet": bool,
}

DATA_PRIORITY_LABELS = {"High": 200, "Mid": 100, "Low": 50}


def normalized_data_job_priority(priority: int | str) -> dict[str, Any]:
    if isinstance(priority, int):
        return {
            "input": priority,
            "label": _priority_label_for_value(priority),
            "value": priority,
        }
    label = priority.strip()
    for known_label, value in DATA_PRIORITY_LABELS.items():
        if label.lower() == known_label.lower():
            return {"input": priority, "label": known_label, "value": value}
    raise ValueError("priority must be High, Mid, Low, or an integer")


def normalized_data_job_target(request: DataJobRequest) -> dict[str, str]:
    storage_name = request.storage_name
    path = request.target_path
    if request.target is not None:
        storage_name = request.target.storage_name
        path = request.target.path
    if storage_name is None or not storage_name.strip():
        raise ValueError("storage_name is required")
    if path is None or not path.strip():
        raise ValueError("required storage-relative path is missing")
    reject_unsafe_relative_path(path)
    return {"storage_name": storage_name, "path": _canonical_relative_path(path)}


def normalized_data_job_sync_paths(
    request: DataJobRequest,
) -> tuple[dict[str, str], dict[str, str]]:
    source = request.source
    destination = request.destination
    if source is None and request.storage_name and request.source_path:
        source = DataPathTarget(storage_name=request.storage_name, path=request.source_path)
    if destination is None and request.storage_name and request.destination_path:
        destination = DataPathTarget(
            storage_name=request.storage_name, path=request.destination_path
        )
    if source is None or destination is None:
        raise ValueError("source and destination are required")
    reject_unsafe_relative_path(source.path)
    reject_unsafe_relative_path(destination.path)
    normalized_source = {
        "storage_name": source.storage_name,
        "path": _canonical_relative_path(source.path),
    }
    normalized_destination = {
        "storage_name": destination.storage_name,
        "path": _canonical_relative_path(destination.path),
    }
    if normalized_source == normalized_destination:
        raise ValueError("sync destination must differ from source")
    if (
        normalized_source["storage_name"] == normalized_destination["storage_name"]
        and _path_is_descendant_or_self(
            normalized_destination["path"], normalized_source["path"]
        )
    ):
        raise ValueError("sync destination must not be the source or under the source")
    return normalized_source, normalized_destination


def normalized_data_job_payload(
    request: DataJobRequest, operation: OperationKind
) -> dict[str, Any]:
    priority = normalized_data_job_priority(request.priority)
    payload = request.model_dump(mode="json", exclude_none=True)
    payload["priority"] = priority["value"]
    payload["priority_label"] = priority["label"]
    payload["priority_input"] = priority["input"]
    if operation == OperationKind.DATA_SCAN:
        target = normalized_data_job_target(request)
        payload["storage_name"] = target["storage_name"]
        payload["target_path"] = target["path"]
        payload["target"] = target
    if operation == OperationKind.DATA_RM:
        target = normalized_data_job_target(request)
        if target["path"] in {"", "."}:
            raise ValueError("rm target must not be the storage root")
        payload["storage_name"] = target["storage_name"]
        payload["target_path"] = target["path"]
        payload["target"] = target
    if operation == OperationKind.DATA_SYNC:
        source, destination = normalized_data_job_sync_paths(request)
        payload["source"] = source
        payload["destination"] = destination
        payload["source_path"] = source["path"]
        payload["destination_path"] = destination["path"]
        payload["source_storage_name"] = source["storage_name"]
        payload["destination_storage_name"] = destination["storage_name"]
        payload["storage_name"] = source["storage_name"]
    if operation in {OperationKind.DATA_SYNC, OperationKind.DATA_RM}:
        payload["option_fingerprint"] = data_job_option_fingerprint(payload.get("options") or {})
    return payload


def validate_data_job_paths(request: DataJobRequest, operation: OperationKind) -> None:
    raw_options = request.options.get("raw_options") or request.options.get("command_line")
    if raw_options:
        raise ValueError("raw command-line option strings are not accepted")
    _validate_data_job_resources(request.resources, operation)
    normalized_data_job_priority(request.priority)
    if operation == OperationKind.DATA_SCAN:
        normalized_data_job_target(request)
        _validate_data_scan_options(request.options)
        return
    if operation == OperationKind.DATA_SYNC:
        normalized_data_job_sync_paths(request)
        _validate_data_sync_options(request.options)
        return
    if operation == OperationKind.DATA_RM:
        target = normalized_data_job_target(request)
        if target["path"] in {"", "."}:
            raise ValueError("rm target must not be the storage root")
        _validate_data_rm_options(request.options)
        return
    raise ValueError(f"unsupported data operation: {operation}")


def _validate_data_job_resources(
    resources: DataJobResources | None, operation: OperationKind
) -> None:
    if resources is None:
        return
    if operation in {OperationKind.DATA_SCAN, OperationKind.DATA_RM}:
        if resources.source_node_count is not None or resources.destination_node_count is not None:
            raise ValueError("source/destination resource counts are only valid for sync")
    if operation == OperationKind.DATA_SYNC:
        return


def _validate_data_scan_options(options: dict[str, Any]) -> None:
    for key, value in options.items():
        if key not in DATA_SCAN_OPTION_TYPES:
            raise ValueError(f"unsupported scan option: {key}")
        expected = DATA_SCAN_OPTION_TYPES[key]
        if expected is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"scan option {key} must be an integer")
            if value < 0:
                raise ValueError(f"scan option {key} must be non-negative")
            continue
        if not isinstance(value, expected):
            raise ValueError(f"scan option {key} has invalid type")


def _validate_data_sync_options(options: dict[str, Any]) -> None:
    _validate_typed_options(options, DATA_SYNC_OPTION_TYPES, "sync")
    _validate_positive_bounded_int(options, "batch_files", minimum=1, maximum=1_000_000)
    _validate_positive_bounded_int(options, "bufsize", minimum=4096, maximum=1024**3)


def _validate_data_rm_options(options: dict[str, Any]) -> None:
    _validate_typed_options(options, DATA_RM_OPTION_TYPES, "rm")
    if options.get("recursive") is not True:
        raise ValueError("rm directory requests require recursive=true")
    if options.get("stat") and options.get("lite"):
        raise ValueError("rm options stat and lite are mutually exclusive")


def _validate_typed_options(
    options: dict[str, Any],
    allowed: dict[str, type | tuple[type, ...]],
    operation: str,
) -> None:
    for key, value in options.items():
        if key not in allowed:
            raise ValueError(f"unsupported {operation} option: {key}")
        expected = allowed[key]
        if expected is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{operation} option {key} must be an integer")
            continue
        if not isinstance(value, expected):
            raise ValueError(f"{operation} option {key} has invalid type")


def _validate_positive_bounded_int(
    options: dict[str, Any], key: str, *, minimum: int, maximum: int
) -> None:
    if key not in options:
        return
    value = options[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"option {key} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"option {key} must be between {minimum} and {maximum}")


def data_job_option_fingerprint(options: dict[str, Any]) -> str:
    payload = json.dumps(options or {}, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_relative_path(path: str) -> str:
    parts = [part for part in path.split("/") if part not in {"", "."}]
    return "/".join(parts) or "."


def _path_is_descendant_or_self(candidate: str, parent: str) -> bool:
    candidate = _canonical_relative_path(candidate)
    parent = _canonical_relative_path(parent)
    return candidate == parent or candidate.startswith(f"{parent}/")


def _priority_label_for_value(value: int) -> str:
    if value >= DATA_PRIORITY_LABELS["High"]:
        return "High"
    if value <= DATA_PRIORITY_LABELS["Low"]:
        return "Low"
    return "Mid"
