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


class IdentityMixin:
    """Identity-mapping persistence (Data Management)."""


    def upsert_identity_mapping(
        self,
        data: IdentityMappingInput,
        *,
        uid: int | None = None,
        gid: int | None = None,
        groups: list[str] | None = None,
        status: IdentityMappingStatus = IdentityMappingStatus.ACTIVE,
        ldap_lookup_at: str | None = None,
        verification_result: str | None = None,
        mismatch_reason: str | None = None,
        source_metadata: dict[str, Any] | None = None,
    ) -> str:
        mapping_id = f"{data.identity_provider}:{data.requester_id}"
        now = iso_now()
        stored_uid = uid if uid is not None else data.uid
        stored_gid = gid if gid is not None else data.gid
        stored_groups = groups if groups is not None else data.groups
        if stored_uid is None or stored_gid is None:
            raise ValueError("identity mapping requires LDAP-resolved uid and gid")
        verified_at = now if status == IdentityMappingStatus.ACTIVE else None
        stale_at = now if status == IdentityMappingStatus.STALE else None
        ldap_lookup_at = ldap_lookup_at or now
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT mapping_id, status FROM identity_mappings
                WHERE requester_id = ? AND identity_provider = ?
                """,
                (data.requester_id, data.identity_provider),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE identity_mappings
                    SET posix_username = ?, uid = ?, gid = ?, groups_json = ?,
                        status = ?, ldap_lookup_at = ?, verified_at = ?,
                        stale_at = ?, disabled_at = NULL,
                        verification_result = ?, mismatch_reason = ?,
                        disabled_reason = NULL, ldap_source_metadata = ?,
                        updated_at = ?
                    WHERE requester_id = ? AND identity_provider = ?
                    """,
                    (
                        data.posix_username,
                        stored_uid,
                        stored_gid,
                        json_dumps(stored_groups),
                        status.value,
                        ldap_lookup_at,
                        verified_at,
                        stale_at,
                        verification_result,
                        mismatch_reason,
                        json_dumps(source_metadata or {}),
                        now,
                        data.requester_id,
                        data.identity_provider,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO identity_mappings (
                        mapping_id, requester_id, identity_provider, posix_username,
                        uid, gid, groups_json, status, ldap_lookup_at, verified_at,
                        stale_at, disabled_at, verification_result, mismatch_reason,
                        disabled_reason, ldap_source_metadata, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mapping_id,
                        data.requester_id,
                        data.identity_provider,
                        data.posix_username,
                        stored_uid,
                        stored_gid,
                        json_dumps(stored_groups),
                        status.value,
                        ldap_lookup_at,
                        verified_at,
                        stale_at,
                        None,
                        verification_result,
                        mismatch_reason,
                        None,
                        json_dumps(source_metadata or {}),
                        now,
                    ),
                )
        return mapping_id


    def get_identity_mapping(
        self, requester_id: str, provider: str
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM identity_mappings
                WHERE requester_id = ? AND identity_provider = ?
                """,
                (requester_id, provider),
            ).fetchone()
        return self._decode_identity_mapping(row_to_dict(row)) if row else None


    def verify_identity_mapping(
        self,
        *,
        requester_id: str,
        provider: str,
        source_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = iso_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE identity_mappings
                SET status = ?, ldap_lookup_at = ?, verified_at = ?,
                    stale_at = NULL, verification_result = ?,
                    mismatch_reason = NULL, ldap_source_metadata = ?,
                    updated_at = ?
                WHERE requester_id = ? AND identity_provider = ?
                """,
                (
                    IdentityMappingStatus.ACTIVE.value,
                    now,
                    now,
                    "matched",
                    json_dumps(source_metadata or {}),
                    now,
                    requester_id,
                    provider,
                ),
            )
        mapping = self.get_identity_mapping(requester_id, provider)
        if not mapping:
            raise KeyError(f"identity mapping not found: {provider}/{requester_id}")
        return mapping


    def mark_identity_mapping_stale(
        self,
        *,
        requester_id: str,
        provider: str,
        mismatch_reason: str,
        verification_result: str = "mismatch",
        source_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = iso_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE identity_mappings
                SET status = ?, ldap_lookup_at = ?, stale_at = ?,
                    verification_result = ?, mismatch_reason = ?,
                    ldap_source_metadata = ?, updated_at = ?
                WHERE requester_id = ? AND identity_provider = ?
                """,
                (
                    IdentityMappingStatus.STALE.value,
                    now,
                    now,
                    verification_result,
                    mismatch_reason,
                    json_dumps(source_metadata or {}),
                    now,
                    requester_id,
                    provider,
                ),
            )
        mapping = self.get_identity_mapping(requester_id, provider)
        if not mapping:
            raise KeyError(f"identity mapping not found: {provider}/{requester_id}")
        return mapping


    def disable_identity_mapping(
        self, requester_id: str, provider: str, reason: str | None = None
    ) -> None:
        now = iso_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE identity_mappings
                SET status = ?, disabled_at = ?, disabled_reason = ?, updated_at = ?
                WHERE requester_id = ? AND identity_provider = ?
                """,
                (
                    IdentityMappingStatus.DISABLED.value,
                    now,
                    reason,
                    now,
                    requester_id,
                    provider,
                ),
            )


    def list_all_identity_mappings_for_sync(
        self,
        *,
        identity_provider: str | None = None,
        exclude_status: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """sync 용: limit 없이 전체 조회, Disabled 제외 옵션."""
        filters: list[str] = []
        params: list[Any] = []
        if identity_provider:
            filters.append("identity_provider = ?")
            params.append(identity_provider)
        if exclude_status:
            placeholders = ",".join("?" for _ in exclude_status)
            filters.append(f"status NOT IN ({placeholders})")
            params.extend(exclude_status)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM identity_mappings {where} ORDER BY requester_id",
                params,
            ).fetchall()
        return [self._decode_identity_mapping(row_to_dict(row)) for row in rows]


    def bulk_update_ldap_sync(
        self,
        updates: list[dict[str, Any]],
    ) -> int:
        """uid/gid/groups/status를 단일 트랜잭션으로 일괄 업데이트.

        updates: list of {requester_id, identity_provider, uid, gid, groups,
                          status, verification_result, mismatch_reason, source_metadata}
        Returns: 업데이트된 row 수
        """
        now = iso_now()
        rows = [
            (
                u["uid"],
                u["gid"],
                json_dumps(u["groups"]),
                u["status"],
                now,
                now if u["status"] == IdentityMappingStatus.ACTIVE.value else None,
                now if u["status"] == IdentityMappingStatus.STALE.value else None,
                u.get("verification_result", "matched"),
                u.get("mismatch_reason"),
                json_dumps(u.get("source_metadata") or {}),
                now,
                u["requester_id"],
                u["identity_provider"],
            )
            for u in updates
        ]
        with self.database.connect() as connection:
            connection.executemany(
                """
                UPDATE identity_mappings
                SET uid = ?, gid = ?, groups_json = ?, status = ?,
                    ldap_lookup_at = ?, verified_at = ?, stale_at = ?,
                    verification_result = ?, mismatch_reason = ?,
                    ldap_source_metadata = ?, updated_at = ?
                WHERE requester_id = ? AND identity_provider = ?
                """,
                rows,
            )
        return len(rows)


    def list_identity_mappings(
        self,
        *,
        requester_id: str | None = None,
        identity_provider: str | None = None,
        status: str | None = None,
        failed: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        params: list[Any] = []
        if requester_id:
            filters.append("requester_id = ?")
            params.append(requester_id)
        if identity_provider:
            filters.append("identity_provider = ?")
            params.append(identity_provider)
        if status:
            filters.append("status = ?")
            params.append(status)
        if failed:
            filters.append("status IN (?, ?, ?)")
            params.extend(
                [
                    IdentityMappingStatus.DISABLED.value,
                    IdentityMappingStatus.NEEDS_REVIEW.value,
                    IdentityMappingStatus.STALE.value,
                ]
            )
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM identity_mappings {where} ORDER BY updated_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._decode_identity_mapping(row_to_dict(row)) for row in rows]


    def _decode_identity_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        if mapping:
            mapping["groups"] = json_loads(mapping.pop("groups_json")) or []
            mapping["ldap_source_metadata"] = (
                json_loads(mapping.get("ldap_source_metadata")) or {}
            )
        return mapping
