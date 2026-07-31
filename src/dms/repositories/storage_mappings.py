from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any
from uuid import uuid4

from ._base import *  # noqa: F401,F403
from ._base import (  # noqa: F401  (underscore helpers are not picked up by import *)
    _agent_capability_summary,
    _parse_iso,
    _storage_names_in_payload,
)


class StorageMappingsMixin:
    """Storage mapping registration and sanity state."""


    def upsert_storage_mapping(
        self,
        data: StorageMappingInput,
        actor: str,
        *,
        sanity_result: dict[str, Any] | None = None,
        readiness: dict[str, Any] | None = None,
        mutation_status: str = "Succeeded",
    ) -> str:
        now = iso_now()
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM storage_mappings WHERE storage_name = ?",
                (data.storage_name,),
            ).fetchone()
            before = (
                self._decode_storage_mapping(row_to_dict(existing))
                if existing
                else None
            )
            sanity_status = (
                sanity_result.get("status") if sanity_result else data.sanity_status
            )
            sanity_checked_at = (
                sanity_result.get("checked_at") if sanity_result else None
            )
            if existing:
                connection.execute(
                    """
                    UPDATE storage_mappings
                    SET backend_template = ?, cluster_name = ?, storage_class_name = ?,
                        version = ?, sanity_status = ?, sanity_result = ?,
                        sanity_checked_at = ?, readiness = ?, updated_by = ?,
                        updated_at = ?
                    WHERE storage_name = ?
                    """,
                    (
                        json_dumps(data.backend_template),
                        data.cluster_name,
                        data.storage_class_name,
                        data.version,
                        sanity_status,
                        json_dumps(sanity_result or {}),
                        sanity_checked_at,
                        json_dumps(readiness or {}),
                        actor,
                        now,
                        data.storage_name,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO storage_mappings (
                        storage_name, backend_template, cluster_name,
                        storage_class_name, version, sanity_status, sanity_result,
                        sanity_checked_at, readiness, updated_by, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data.storage_name,
                        json_dumps(data.backend_template),
                        data.cluster_name,
                        data.storage_class_name,
                        data.version,
                        sanity_status,
                        json_dumps(sanity_result or {}),
                        sanity_checked_at,
                        json_dumps(readiness or {}),
                        actor,
                        now,
                    ),
                )
            after = {
                "storage_name": data.storage_name,
                "backend_template": data.backend_template,
                "cluster_name": data.cluster_name,
                "storage_class_name": data.storage_class_name,
                "version": data.version,
                "sanity_status": sanity_status,
                "sanity_result": sanity_result or {},
                "readiness": readiness or {},
                "updated_by": actor,
            }
            self._insert_control_mutation(
                connection,
                actor=actor,
                mutation_kind="storage_mapping.upsert",
                payload=data.model_dump(),
                mutation_class="storage_mapping",
                operation="upsert",
                target_key=data.storage_name,
                status=mutation_status,
                result_summary={"sanity_status": sanity_status},
                before_state=before,
                after_state=after,
                created_at=now,
            )
        return data.storage_name


    def update_storage_mapping_sanity(
        self,
        storage_name: str,
        *,
        sanity_result: dict[str, Any],
        readiness: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        now = iso_now()
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM storage_mappings WHERE storage_name = ?",
                (storage_name,),
            ).fetchone()
            if not existing:
                raise KeyError(f"storage mapping not found: {storage_name}")
            before = self._decode_storage_mapping(row_to_dict(existing))
            connection.execute(
                """
                UPDATE storage_mappings
                SET sanity_status = ?, sanity_result = ?, sanity_checked_at = ?,
                    readiness = ?, updated_by = ?, updated_at = ?
                WHERE storage_name = ?
                """,
                (
                    sanity_result["status"],
                    json_dumps(sanity_result),
                    sanity_result.get("checked_at") or now,
                    json_dumps(readiness),
                    actor,
                    now,
                    storage_name,
                ),
            )
            after_row = connection.execute(
                "SELECT * FROM storage_mappings WHERE storage_name = ?",
                (storage_name,),
            ).fetchone()
            after = self._decode_storage_mapping(row_to_dict(after_row))
            self._insert_control_mutation(
                connection,
                actor=actor,
                mutation_kind="storage_mapping.check",
                payload={"storage_name": storage_name},
                mutation_class="storage_mapping",
                operation="check",
                target_key=storage_name,
                status="Succeeded",
                result_summary={"sanity_status": sanity_result["status"]},
                before_state=before,
                after_state=after,
                created_at=now,
            )
        return after


    def record_storage_mapping_conflict(
        self, *, storage_name: str, actor: str, conflict: dict[str, Any]
    ) -> None:
        before = self.get_storage_mapping(storage_name)
        with self.database.connect() as connection:
            self._insert_control_mutation(
                connection,
                actor=actor,
                mutation_kind="storage_mapping.upsert",
                payload={"storage_name": storage_name, "conflict": conflict},
                mutation_class="storage_mapping",
                operation="upsert",
                target_key=storage_name,
                status="Conflict",
                result_summary=conflict,
                before_state=before,
                after_state=None,
                created_at=iso_now(),
            )


    def get_storage_mapping(self, storage_name: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM storage_mappings WHERE storage_name = ?",
                (storage_name,),
            ).fetchone()
        return self._decode_storage_mapping(row_to_dict(row)) if row else None


    def list_storage_mappings(
        self,
        limit: int = 100,
        *,
        cluster_name: str | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if cluster_name is not None:
                rows = connection.execute(
                    "SELECT * FROM storage_mappings WHERE cluster_name = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (cluster_name, limit, offset),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM storage_mappings ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        return [self._decode_storage_mapping(row_to_dict(row)) for row in rows]


    def delete_storage_mapping(self, storage_name: str, actor: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM storage_mappings WHERE storage_name = ?",
                (storage_name,),
            ).fetchone()
            if not existing:
                raise KeyError(f"storage mapping not found: {storage_name}")
            before = self._decode_storage_mapping(row_to_dict(existing))
            connection.execute(
                "DELETE FROM storage_mappings WHERE storage_name = ?",
                (storage_name,),
            )
            self._insert_control_mutation(
                connection,
                actor=actor,
                mutation_kind="storage_mapping.delete",
                payload={"storage_name": storage_name},
                mutation_class="storage_mapping",
                operation="delete",
                target_key=storage_name,
                status="Succeeded",
                result_summary={"storage_name": storage_name},
                before_state=before,
                after_state=None,
                created_at=iso_now(),
            )
        return before


    def _decode_storage_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        if mapping:
            mapping["backend_template"] = json_loads(mapping["backend_template"]) or {}
            mapping["sanity_result"] = json_loads(mapping.get("sanity_result")) or {}
            mapping["readiness"] = json_loads(mapping.get("readiness")) or {}
        return mapping
