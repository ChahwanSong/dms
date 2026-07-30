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

