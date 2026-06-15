from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ..adapters import (
    kubernetes_resource_quota_value_to_base_units,
    render_kubernetes_resource_quota_hard,
    zero_kubernetes_resource_quota_hard,
)
from ..backend_registry import BackendAdapterRegistry
from ..domain import (
    LifecycleState,
    OperationKind,
    ResourceKind,
    WorkerRole,
    validate_storage_root_basename,
)
from ..repositories import DmsRepository, utcnow

RM_OPERATIONS = {
    OperationKind.FILESYSTEM_CREATE.value,
    OperationKind.FILESYSTEM_UPDATE.value,
    OperationKind.FILESYSTEM_BLOCK.value,
    OperationKind.FILESYSTEM_INITIALIZE.value,
    OperationKind.FILESYSTEM_DELETE.value,
    OperationKind.FILESYSTEM_ASSIGN_QUOTA.value,
    OperationKind.FILESYSTEM_IMPORT.value,
    OperationKind.FILESYSTEM_CHECK.value,
    OperationKind.FILESYSTEM_SYNC.value,
    OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value,
    OperationKind.K8S_QUOTA_CREATE.value,
    OperationKind.K8S_QUOTA_UPDATE.value,
    OperationKind.K8S_QUOTA_BLOCK.value,
    OperationKind.K8S_QUOTA_DELETE.value,
    OperationKind.K8S_QUOTA_SYNC.value,
    OperationKind.K8S_QUOTA_CHECK.value,
    OperationKind.K8S_QUOTA_AUDIT.value,
    OperationKind.K8S_QUOTA_IMPORT.value,
    OperationKind.K8S_QUOTA_EXPIRATION_SWEEP.value,
}

FILESYSTEM_RM_OPERATIONS = {
    OperationKind.FILESYSTEM_CREATE.value,
    OperationKind.FILESYSTEM_UPDATE.value,
    OperationKind.FILESYSTEM_BLOCK.value,
    OperationKind.FILESYSTEM_INITIALIZE.value,
    OperationKind.FILESYSTEM_DELETE.value,
    OperationKind.FILESYSTEM_ASSIGN_QUOTA.value,
    OperationKind.FILESYSTEM_IMPORT.value,
    OperationKind.FILESYSTEM_CHECK.value,
    OperationKind.FILESYSTEM_SYNC.value,
    OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value,
}

FILESYSTEM_CREATE_UNSUPPORTED_PAYLOAD_FIELDS = {
    "acl",
    "capacity_bytes",
    "file_count",
    "rename",
    "block",
    "check",
    "sync",
    "storage_class_quotas",
    "resource_quota_hard",
    "reset_quota_to_default",
}

FILESYSTEM_BLOCK_UNSUPPORTED_PAYLOAD_FIELDS = {
    "acl",
    "capacity_bytes",
    "file_count",
    "quota",
    "rename",
    "check",
    "sync",
    "storage_class_quotas",
    "resource_quota_hard",
    "reset_quota_to_default",
}

FILESYSTEM_UPDATE_ALLOWED_PAYLOAD_FIELDS = {
    "storage_name",
    "directory_name",
    "quota",
    "expires_at",
    "resource_type",
    "owner_username",
    "reason",
}

FILESYSTEM_CHECK_ALLOWED_PAYLOAD_FIELDS = {
    "storage_name",
    "directory_name",
    "include_quota",
    "include_permission",
    "record_action_required",
    "reason",
}

FILESYSTEM_SYNC_ALLOWED_PAYLOAD_FIELDS = {
    "storage_name",
    "directory_name",
    "source",
    "include_quota",
    "reason",
}

MAX_FILESYSTEM_QUOTA_CAPACITY_BYTES = 1024**4
MAX_FILESYSTEM_QUOTA_FILE_COUNT = 10_000_000
EXPIRY_UNSUPPORTED_PAYLOAD_FIELDS = {"expiry_at", "clear_expires_at"}
EXPIRY_IMPORT_DEFAULT_DAYS = 365

DM_OPERATIONS = {
    OperationKind.DATA_SCAN.value,
    OperationKind.DATA_SYNC.value,
    OperationKind.DATA_RM.value,
}




def _observed_quota_used(observed_state: dict[str, Any]) -> dict[str, Any]:
    resource_quota = observed_state.get("resource_quota") or {}
    if resource_quota.get("status_used"):
        return resource_quota["status_used"]
    verification = observed_state.get("pvc_admission_verification") or {}
    after_allowed = verification.get("resource_quota_status_after_allowed_pvc") or {}
    return after_allowed.get("used") or {}


def _filesystem_restore_state(resource: dict[str, Any]) -> dict[str, Any]:
    for section in ("desired_state", "observed_state", "applied_state"):
        state = resource.get(section) or {}
        block_state = state.get("block_state") or {}
        restore = block_state.get("restore") or block_state.get("restore_state")
        if isinstance(restore, dict) and restore:
            return restore
    return {}


def _append_basename_issue(
    issues: list[dict[str, Any]], field_name: str, value: Any
) -> None:
    if not isinstance(value, str) or not value:
        issues.append({"reason": f"{field_name}_missing"})
        return
    if len(value) > 128:
        issues.append(
            {
                "reason": f"{field_name}_too_long",
                "max_length": 128,
                "value_length": len(value),
            }
        )
        return
    try:
        validate_storage_root_basename(field_name, value)
    except ValueError as exc:
        issues.append(
            {
                "reason": f"{field_name}_invalid",
                "message": str(exc),
            }
        )


def _validate_string_list(
    issues: list[dict[str, Any]],
    field_name: str,
    value: Any,
    *,
    required: bool,
) -> list[str] | None:
    if value is None:
        if required:
            issues.append({"reason": f"{field_name}_required"})
        return None
    if not isinstance(value, list):
        issues.append({"reason": f"{field_name}_must_be_list"})
        return None
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            issues.append({"reason": f"{field_name}_entries_must_be_non_empty_strings"})
            return None
        normalized.append(item.strip())
    if len(set(normalized)) != len(normalized):
        issues.append({"reason": f"{field_name}_entries_must_be_unique"})
        return None
    return normalized


def _unsupported_payload_issues(
    payload: dict[str, Any],
    allowed_fields: set[str],
    reason: str,
) -> list[dict[str, Any]]:
    unsupported = sorted(field for field in payload if field not in allowed_fields)
    return [{"reason": reason, "fields": unsupported}] if unsupported else []


def _append_boolean_payload_issues(
    issues: list[dict[str, Any]],
    payload: dict[str, Any],
    fields: tuple[str, ...],
) -> None:
    for field in fields:
        if field in payload and not isinstance(payload[field], bool):
            issues.append(
                {"reason": f"{field}_boolean_required", "value": payload[field]}
            )


def _append_expiry_issues(
    issues: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    operation: str,
    existing_desired: dict[str, Any],
    resource_kind: str,
) -> None:
    supported_operations = {
        OperationKind.FILESYSTEM_CREATE.value,
        OperationKind.FILESYSTEM_UPDATE.value,
        OperationKind.FILESYSTEM_IMPORT.value,
        OperationKind.K8S_QUOTA_CREATE.value,
        OperationKind.K8S_QUOTA_UPDATE.value,
        OperationKind.K8S_QUOTA_IMPORT.value,
    }
    if operation not in supported_operations:
        return
    unsupported = sorted(
        field for field in EXPIRY_UNSUPPORTED_PAYLOAD_FIELDS if field in payload
    )
    if unsupported:
        issues.append({"reason": "expires_at_field_unsupported", "fields": unsupported})
    create_operations = {
        OperationKind.FILESYSTEM_CREATE.value,
        OperationKind.K8S_QUOTA_CREATE.value,
    }
    import_operations = {
        OperationKind.FILESYSTEM_IMPORT.value,
        OperationKind.K8S_QUOTA_IMPORT.value,
    }
    if "expires_at" not in payload:
        if operation in create_operations:
            issues.append({"reason": "expires_at_required"})
        elif (
            operation not in import_operations
            and existing_desired
            and not existing_desired.get("expires_at")
        ):
            # Only an EXISTING resource that genuinely lacks expires_at requires one on
            # update. A missing resource (existing_desired == {}) must not emit a
            # spurious expires_at_required — resource_missing already covers it.
            issues.append({"reason": "expires_at_required"})
        return
    expires_at = payload.get("expires_at")
    if expires_at is None:
        issues.append({"reason": "expires_at_required"})
        return
    normalized, reason = _normalize_future_expires_at(expires_at)
    if normalized is None:
        issues.append(
            {
                "reason": reason or "expires_at_invalid",
                "expires_at": expires_at,
                "resource_kind": resource_kind,
            }
        )


def _default_expires_at() -> str:
    return (utcnow() + timedelta(days=EXPIRY_IMPORT_DEFAULT_DAYS)).isoformat()


def _normalize_expires_at_or_none(value: Any) -> str | None:
    normalized, _ = _normalize_future_expires_at(value)
    return normalized


def _normalize_future_expires_at(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or not value.strip():
        return None, "expires_at_invalid"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, "expires_at_invalid"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, "expires_at_timezone_required"
    expires = parsed.astimezone(UTC)
    if expires <= utcnow():
        return None, "expires_at_not_future"
    return expires.isoformat(), None


def _filesystem_quota_issues(quota: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not isinstance(quota, dict):
        return [{"reason": "filesystem_quota_must_be_object"}]
    allowed = {"capacity_bytes", "file_count"}
    unsupported = sorted(field for field in quota if field not in allowed)
    if unsupported:
        issues.append(
            {
                "reason": "filesystem_quota_fields_unsupported",
                "fields": unsupported,
            }
        )
    if not any(field in quota for field in allowed):
        issues.append({"reason": "filesystem_quota_required"})
    for field in sorted(allowed):
        if field not in quota:
            continue
        value = quota[field]
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            issues.append(
                {"reason": f"filesystem_quota_{field}_invalid", "value": value}
            )
            continue
        if parsed <= 0:
            issues.append(
                {"reason": f"filesystem_quota_{field}_invalid", "value": value}
            )
            continue
        if field == "capacity_bytes" and parsed > MAX_FILESYSTEM_QUOTA_CAPACITY_BYTES:
            issues.append(
                {
                    "reason": "filesystem_quota_capacity_bytes_too_large",
                    "value": value,
                    "max": MAX_FILESYSTEM_QUOTA_CAPACITY_BYTES,
                }
            )
        if field == "file_count" and parsed > MAX_FILESYSTEM_QUOTA_FILE_COUNT:
            issues.append(
                {
                    "reason": "filesystem_quota_file_count_too_large",
                    "value": value,
                    "max": MAX_FILESYSTEM_QUOTA_FILE_COUNT,
                }
            )
    return issues


def _normalized_filesystem_quota(quota: dict[str, Any]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for field in ("capacity_bytes", "file_count"):
        if field in quota:
            normalized[field] = int(quota[field])
    return normalized
