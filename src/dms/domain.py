from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
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
    # Actual POSIX identity the job runs as. Mirrors RM's filesystem owner_username:
    # requester_id is a free-form logical id and is the DEFAULT for owner_username.
    owner_username: str | None = None
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

    @field_validator("owner_username")
    @classmethod
    def _validate_owner_username(cls, value: str | None) -> str | None:
        # owner_username is the real POSIX identity the data job runs as (via runuser).
        # Validate it as a POSIX username at the API boundary -- like RM does for filesystem
        # owner overrides -- so whitespace/shell-metachar/control values can never flow into
        # the worker's runuser/chown invocations. (When omitted, it defaults to requester_id,
        # which may be a free-form logical id; an invalid default simply fails closed at the
        # LDAP lookup with `ldap_identity_not_found`.)
        if value is None:
            return None
        candidate = value.strip()
        if not candidate:
            raise ValueError("owner_username must not be blank when provided")
        if len(candidate) > 64 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9._-]*", candidate):
            raise ValueError(
                "owner_username must be a POSIX username matching [A-Za-z_][A-Za-z0-9._-]* (max 64 chars)"
            )
        return candidate

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


class DmIdentityDenylistBody(BaseModel):
    """Body for adding a DM identity denylist entry. The subject + subject_type come
    from the path; only the reason is in the body."""

    reason: str | None = None


DM_DENYLIST_SUBJECT_TYPES = ("requester", "owner", "group")


# Filesystem backends that manage a directory subtree under their mount point.
# Values mirror backends/{cephfs,weka,gpfs}.py *_BACKEND_TYPE; kept as literals here
# to avoid a domain -> backends import dependency.
_FILESYSTEM_BACKEND_TYPES = ("cephfs", "wekafs", "gpfs")


def managed_root_path_suffix(mount_path: str, managed_root: str) -> str:
    """``managed_root`` expressed relative to ``mount_path`` (e.g. ``/cephfs`` +
    ``/cephfs/dms`` -> ``"dms"``). Empty string when they are equal. Raises ValueError
    if managed_root is not under mount_path."""
    mount_norm = os.path.normpath(mount_path)
    root_norm = os.path.normpath(managed_root)
    if os.path.commonpath([mount_norm, root_norm]) != mount_norm:
        raise ValueError("managed_root must be under mount_path")
    suffix = os.path.relpath(root_norm, mount_norm)
    return "" if suffix == "." else suffix


def validate_filesystem_managed_root(backend_template: dict[str, Any]) -> None:
    """Validate a filesystem storage mapping's backend_template at registration.

    Filesystem mappings must declare ``mount_path`` and an explicit ``managed_root``
    under it. ``managed_root`` is the security/isolation boundary (and the DM path base
    when ``DMS_DM_PATH_BASE=managed_root``), so it must be explicit -- the historical
    implicit ``{mount_path}/dms`` default is no longer accepted. GPFS additionally
    requires an explicit ``filesystem_name`` (the GPFS device its mm* fileset/quota
    commands target). Non-filesystem backends (CSI, kubernetes namespace quota) have no
    managed_root and are skipped.
    """
    backend_type = backend_template.get("backend_type")
    if backend_type not in _FILESYSTEM_BACKEND_TYPES:
        return
    mount_path = backend_template.get("mount_path")
    managed_root = backend_template.get("managed_root")
    if not mount_path:
        raise ValueError(f"{backend_type} storage mapping requires mount_path")
    if not managed_root:
        raise ValueError(
            f"{backend_type} storage mapping requires an explicit managed_root"
        )
    managed_root_path_suffix(mount_path, managed_root)  # validates under mount_path
    # GPFS mm* commands target a named filesystem (the GPFS device), so filesystem_name
    # is required and is not silently defaulted to storage_name.
    if backend_type == "gpfs" and not backend_template.get("filesystem_name"):
        raise ValueError("gpfs storage mapping requires filesystem_name")


# Per-mapping Kubernetes ResourceQuota mutation transport. Mirrors how filesystem
# mappings carry command_runner/ssh_host: a k8s/CSI mapping may pin HOW DMS applies
# quota mutations to its cluster, overriding the global DMS_KUBERNETES_MUTATION_MODE.
_KUBERNETES_MUTATION_MODES = ("kubectl", "ssh-kubectl")
# control_host is interpolated into ``ssh <host> kubectl ...``. Restrict it to a bare
# hostname/IPv4 with an alphanumeric first character so it cannot be parsed by ssh as an
# option (e.g. a leading '-' -> '-oProxyCommand=...' injection) or carry a user@/metachar.
_CONTROL_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_kubernetes_mutation_template(backend_template: dict[str, Any]) -> None:
    """Validate the optional per-mapping Kubernetes mutation settings on a mapping.

    A storage mapping may carry, in its ``backend_template``:
      - ``mutation_mode``: ``"kubectl"`` (run kubectl locally with the cluster's
        kubeconfig) or ``"ssh-kubectl"`` (ssh to ``control_host`` then run kubectl there).
      - ``control_host``: the SSH host the rm-worker connects to; **required** when
        ``mutation_mode == "ssh-kubectl"``, and conversely **rejected when set without an
        explicit mutation_mode** (it is ignored under the default ``kubectl`` mode). A bare
        host, no whitespace.
    Both are optional and, when absent, fall back to the global settings
    (``DMS_KUBERNETES_MUTATION_MODE`` -- which now defaults to ``kubectl`` --
    / ``DMS_CLUSTER_CONTROL_HOSTS_JSON``). They are validated whenever present so a typo
    fails closed at registration (422) rather than at quota-apply time.
    """
    mode = backend_template.get("mutation_mode")
    control_host = backend_template.get("control_host")
    if mode is None and control_host is None:
        return
    if mode is not None and (
        not isinstance(mode, str) or mode not in _KUBERNETES_MUTATION_MODES
    ):
        raise ValueError(
            "mutation_mode must be one of " + ", ".join(_KUBERNETES_MUTATION_MODES)
        )
    if control_host is not None and (
        not isinstance(control_host, str) or not _CONTROL_HOST_RE.match(control_host)
    ):
        raise ValueError(
            "control_host must be a bare hostname or IPv4 address "
            "(alphanumeric start; letters, digits, '.', '_', '-' only) -- "
            f"got {control_host!r}"
        )
    if mode == "ssh-kubectl" and not control_host:
        raise ValueError("mutation_mode 'ssh-kubectl' requires control_host")
    # control_host is only consumed by ssh-kubectl. With the default mutation mode now
    # 'kubectl' (which ignores control_host), a control_host pinned WITHOUT an explicit
    # mutation_mode would be a silent no-op -- reject it so the mapping's intent is
    # unambiguous regardless of the global default.
    if control_host is not None and mode is None:
        raise ValueError(
            "control_host requires an explicit mutation_mode='ssh-kubectl' "
            "(it is ignored under the default 'kubectl' mutation mode)"
        )


def managed_root_for_mapping(mapping: dict[str, Any]) -> tuple[str, str] | None:
    """``(mount_path, managed_root)`` for a filesystem storage mapping, or ``None`` if it
    cannot be determined (non-filesystem backend, or missing mount_path/managed_root).
    managed_root is mandatory at registration, so ``None`` here is a fail-closed signal
    for the caller (planner rejects the job)."""
    template = mapping.get("backend_template") or {}
    if template.get("backend_type") not in _FILESYSTEM_BACKEND_TYPES:
        return None
    mount_path = template.get("mount_path")
    managed_root = template.get("managed_root")
    if not mount_path or not managed_root:
        return None
    return str(mount_path), str(managed_root)


def apply_managed_root_suffix(path: str, suffix: str) -> str:
    """Prepend the managed_root ``suffix`` to a storage-relative ``path`` and
    re-canonicalize. Empty suffix returns the canonicalized path unchanged."""
    if not suffix:
        return _canonical_relative_path(path)
    return _canonical_relative_path(f"{suffix}/{path}")


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
    "chmod": str,
    "chown": str,
}

# --chmod token: optional D (directories) / F (files) prefix + 1-4 octal digits
# (value <= 07777). A bare token (no prefix) sets both dirs and files.
_SYNC_CHMOD_TOKEN_RE = re.compile(r"^[DF]?[0-7]{1,4}$")
# --chown USER / GROUP component: POSIX name or numeric id.
_SYNC_CHOWN_PART_RE = re.compile(r"^[A-Za-z0-9._-]+$")

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
    # POSIX identity the job runs as: explicit owner_username, else the requester_id.
    payload["owner_username"] = request.owner_username or request.requester_id
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
    if "chmod" in options:
        _validate_sync_chmod_spec(options["chmod"])
    if "chown" in options:
        _validate_sync_chown_spec(options["chown"])


def _validate_sync_chmod_spec(spec: str) -> None:
    """Mirror the mpifileutils dsync/nsync ``--chmod`` grammar.

    Comma-separated octal tokens, each optionally prefixed ``D`` (directories)
    or ``F`` (files); a bare token applies to both. At most one of each kind,
    and a bare token cannot be combined with ``D``/``F`` tokens. The tool
    applies these bits to the *destination*; ownership/permission semantics
    still depend on the POSIX identity the job runs as (see install/4.dms-dm-api.md).
    """
    if not spec:
        raise ValueError("sync option chmod must not be empty")
    n_bare = n_dir = n_file = 0
    for tok in spec.split(","):
        if not _SYNC_CHMOD_TOKEN_RE.match(tok):
            raise ValueError(
                f"sync option chmod has invalid token '{tok}' "
                "(expected [D|F]<1-4 octal digits>)"
            )
        if tok[0] == "D":
            n_dir += 1
        elif tok[0] == "F":
            n_file += 1
        else:
            n_bare += 1
    if n_bare > 1 or n_dir > 1 or n_file > 1 or (n_bare and (n_dir or n_file)):
        raise ValueError(f"sync option chmod has conflicting/duplicate tokens: '{spec}'")


def _validate_sync_chown_spec(spec: str) -> None:
    """Mirror the mpifileutils dsync/nsync ``--chown`` grammar.

    ``USER``, ``:GROUP``, or ``USER:GROUP`` (names or numeric ids). No
    whitespace, at most one ``:``, and a trailing empty group (``USER:``) is
    rejected. Names are resolved by the tool at run time; here we only enforce
    structure. Setting an arbitrary owner needs privilege at run time, so a
    non-privileged requester's ``--chown`` will fail mid-job (see docs).
    """
    if not spec or spec == ":":
        raise ValueError("sync option chown must not be empty")
    if any(ch.isspace() for ch in spec):
        raise ValueError("sync option chown must not contain whitespace")
    if spec.count(":") > 1:
        raise ValueError("sync option chown must contain at most one ':'")
    if ":" in spec:
        user, group = spec.split(":", 1)
        if not group:
            raise ValueError(
                "sync option chown has an empty group (use ':GROUP' or 'USER:GROUP')"
            )
        if user and not _SYNC_CHOWN_PART_RE.match(user):
            raise ValueError(f"sync option chown has invalid user '{user}'")
        if not _SYNC_CHOWN_PART_RE.match(group):
            raise ValueError(f"sync option chown has invalid group '{group}'")
    elif not _SYNC_CHOWN_PART_RE.match(spec):
        raise ValueError(f"sync option chown has invalid user '{spec}'")


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
