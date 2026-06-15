from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any
from uuid import uuid4

from ._base import *  # noqa: F401,F403
from ._base import (  # noqa: F401  (underscore helpers are not picked up by import *)
    _agent_capability_summary,
    _filesystem_block_state,
    _kubernetes_quota_block_state,
    _parse_iso,
    _storage_names_in_payload,
)


class ResourcesMixin:
    """Managed resource rows (filesystem + k8s namespace quota)."""


    def upsert_resource(
        self,
        *,
        resource_kind: str,
        resource_key: str,
        desired_state: dict[str, Any],
        applied_state: dict[str, Any],
        observed_state: dict[str, Any],
        status: str,
    ) -> str:
        now = iso_now()
        resource_id = f"{resource_kind}:{resource_key}"
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT version FROM resources
                WHERE resource_kind = ? AND resource_key = ?
                """,
                (resource_kind, resource_key),
            ).fetchone()
            if existing:
                version = int(existing["version"]) + 1
                connection.execute(
                    """
                    UPDATE resources
                    SET desired_state = ?, applied_state = ?, observed_state = ?,
                        version = ?, status = ?, updated_at = ?
                    WHERE resource_kind = ? AND resource_key = ?
                    """,
                    (
                        json_dumps(desired_state),
                        json_dumps(applied_state),
                        json_dumps(observed_state),
                        version,
                        status,
                        now,
                        resource_kind,
                        resource_key,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO resources (
                        resource_id, resource_kind, resource_key, desired_state,
                        applied_state, observed_state, version, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resource_id,
                        resource_kind,
                        resource_key,
                        json_dumps(desired_state),
                        json_dumps(applied_state),
                        json_dumps(observed_state),
                        1,
                        status,
                        now,
                    ),
                )
        return resource_id


    def list_resources(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM resources ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        resources = rows_to_dicts(rows)
        for resource in resources:
            resource["desired_state"] = json_loads(resource["desired_state"]) or {}
            resource["applied_state"] = json_loads(resource["applied_state"]) or {}
            resource["observed_state"] = json_loads(resource["observed_state"]) or {}
        return resources


    def list_filesystem_resources(
        self,
        *,
        storage_name: str | None = None,
        requester_id: str | None = None,
        resource_type: str | None = None,
        status: list[str] | tuple[str, ...] | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM resources
                WHERE resource_kind = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (ResourceKind.FILESYSTEM.value, limit),
            ).fetchall()
        resources = rows_to_dicts(rows)
        filtered: list[dict[str, Any]] = []
        allowed_status = set(status or [])
        for resource in resources:
            resource["desired_state"] = json_loads(resource["desired_state"]) or {}
            resource["applied_state"] = json_loads(resource["applied_state"]) or {}
            resource["observed_state"] = json_loads(resource["observed_state"]) or {}
            desired = resource["desired_state"]
            if storage_name and desired.get("storage_name") != storage_name:
                continue
            if requester_id and desired.get("requester_id") != requester_id:
                continue
            if resource_type and desired.get("resource_type") != resource_type:
                continue
            if allowed_status and resource.get("status") not in allowed_status:
                continue
            filtered.append(resource)
        return filtered


    def list_filesystem_resources_expiring(
        self,
        *,
        storage_name: str | None = None,
        status: str = "expired",
        before: str | None = None,
        within_seconds: int | None = None,
        include_blocked: bool = False,
        resource_type: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        if status not in {"expired", "expiring", "all"}:
            raise ValueError("status must be one of: expired, expiring, all")
        basis = _parse_iso(before) if before else utcnow()
        window_end = (
            basis + timedelta(seconds=within_seconds)
            if within_seconds is not None
            else None
        )
        rows = self.list_filesystem_resources(
            storage_name=storage_name,
            resource_type=resource_type,
            limit=max(limit * 4, limit),
        )
        matches: list[dict[str, Any]] = []
        for resource in rows:
            if resource.get("status") == "Deleted":
                continue
            desired = resource["desired_state"]
            applied = resource["applied_state"]
            expires_at = desired.get("expires_at") or applied.get("expires_at")
            if not expires_at:
                continue
            expires = _parse_iso(str(expires_at))
            expired = expires <= basis
            expiring = bool(window_end and basis < expires <= window_end)
            if status == "expired" and not expired:
                continue
            if status == "expiring" and not expiring:
                continue
            if status == "all" and not (expired or expiring):
                continue
            block_state = _filesystem_block_state(resource)
            if block_state.get("blocked") and not include_blocked:
                continue
            seconds_overdue = (
                int((basis - expires).total_seconds()) if expired else None
            )
            matches.append(
                {
                    **resource,
                    "storage_name": desired.get("storage_name"),
                    "directory_name": desired.get("directory_name"),
                    "resource_type": desired.get("resource_type") or "user",
                    "expires_at": expires.isoformat(),
                    "expired": expired,
                    "expiring": expiring,
                    "seconds_overdue": seconds_overdue,
                    "block_state": block_state or {"blocked": False},
                }
            )
        return matches[:limit]


    def list_kubernetes_namespace_quota_resources(
        self,
        *,
        cluster_name: str | None = None,
        namespace_name: str | None = None,
        requester_id: str | None = None,
        resource_type: str | None = None,
        status: list[str] | tuple[str, ...] | None = None,
        storage_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM resources
                WHERE resource_kind = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value, limit),
            ).fetchall()
        resources = rows_to_dicts(rows)
        filtered: list[dict[str, Any]] = []
        allowed_status = set(status or [])
        for resource in resources:
            resource["desired_state"] = json_loads(resource["desired_state"]) or {}
            resource["applied_state"] = json_loads(resource["applied_state"]) or {}
            resource["observed_state"] = json_loads(resource["observed_state"]) or {}
            desired = resource["desired_state"]
            if cluster_name and desired.get("cluster_name") != cluster_name:
                continue
            if namespace_name and desired.get("namespace_name") != namespace_name:
                continue
            if requester_id and desired.get("requester_id") != requester_id:
                continue
            if resource_type and desired.get("resource_type") != resource_type:
                continue
            if allowed_status and resource.get("status") not in allowed_status:
                continue
            if storage_name:
                entry_storage_names = {
                    entry.get("storage_name")
                    for entry in desired.get("storage_class_quotas") or []
                    if isinstance(entry, dict)
                }
                if storage_name not in entry_storage_names:
                    continue
            filtered.append(resource)
        return filtered


    def list_kubernetes_namespace_quota_resources_expiring(
        self,
        *,
        cluster_name: str | None = None,
        namespace_name: str | None = None,
        status: str = "expired",
        before: str | None = None,
        within_seconds: int | None = None,
        include_blocked: bool = False,
        resource_type: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        if status not in {"expired", "expiring", "all"}:
            raise ValueError("status must be one of: expired, expiring, all")
        basis = _parse_iso(before) if before else utcnow()
        window_end = (
            basis + timedelta(seconds=within_seconds)
            if within_seconds is not None
            else None
        )
        rows = self.list_kubernetes_namespace_quota_resources(
            cluster_name=cluster_name,
            namespace_name=namespace_name,
            resource_type=resource_type,
            limit=max(limit * 4, limit),
        )
        matches: list[dict[str, Any]] = []
        for resource in rows:
            if resource.get("status") == "Deleted":
                continue
            desired = resource["desired_state"]
            applied = resource["applied_state"]
            expires_at = desired.get("expires_at") or applied.get("expires_at")
            if not expires_at:
                continue
            expires = _parse_iso(str(expires_at))
            expired = expires <= basis
            expiring = bool(window_end and basis < expires <= window_end)
            if status == "expired" and not expired:
                continue
            if status == "expiring" and not expiring:
                continue
            if status == "all" and not (expired or expiring):
                continue
            block_state = _kubernetes_quota_block_state(resource)
            if block_state.get("blocked") and not include_blocked:
                continue
            seconds_overdue = (
                int((basis - expires).total_seconds()) if expired else None
            )
            matches.append(
                {
                    **resource,
                    "cluster_name": desired.get("cluster_name"),
                    "namespace_name": desired.get("namespace_name"),
                    "resource_type": desired.get("resource_type") or "user",
                    "resource_quota_name": desired.get(
                        "resource_quota_name", "dms-storage-quota"
                    ),
                    "desired_hard": desired.get("resource_quota_hard") or {},
                    "expires_at": expires.isoformat(),
                    "expired": expired,
                    "expiring": expiring,
                    "seconds_overdue": seconds_overdue,
                    "block_state": block_state or {"blocked": False},
                }
            )
        return matches[:limit]


    def get_resource(
        self, resource_kind: str, resource_key: str
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM resources
                WHERE resource_kind = ? AND resource_key = ?
                """,
                (resource_kind, resource_key),
            ).fetchone()
        resource = row_to_dict(row) if row else None
        if not resource:
            return None
        resource["desired_state"] = json_loads(resource["desired_state"]) or {}
        resource["applied_state"] = json_loads(resource["applied_state"]) or {}
        resource["observed_state"] = json_loads(resource["observed_state"]) or {}
        return resource
