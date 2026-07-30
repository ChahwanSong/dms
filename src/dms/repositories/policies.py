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


class PoliciesMixin:
    """Default quota policies and data-management policies."""


    def bootstrap_data_management_policies(
        self, policies: list[dict[str, Any]], *, actor: str = "bootstrap"
    ) -> None:
        for policy in policies:
            operation = str(policy["operation"]).strip().lower()
            if self.get_data_management_policy(operation):
                continue
            self.upsert_data_management_policy(
                DataManagementPolicyInput.model_validate(
                    {**policy, "operation": operation}
                ),
                actor=actor,
                mutation_kind="data_management_policy.bootstrap",
            )


    def upsert_data_management_policy(
        self,
        policy: DataManagementPolicyInput,
        *,
        actor: str,
        mutation_kind: str = "data_management_policy.upsert",
    ) -> str:
        now = iso_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO data_management_policies (
                    operation, default_worker_nodes, default_source_nodes,
                    default_destination_nodes, max_worker_nodes, max_source_nodes,
                    max_destination_nodes, default_processes_per_node,
                    max_processes_per_node, default_queue, default_priority_class,
                    default_timeout_seconds, enabled, updated_at, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation) DO UPDATE SET
                    default_worker_nodes = excluded.default_worker_nodes,
                    default_source_nodes = excluded.default_source_nodes,
                    default_destination_nodes = excluded.default_destination_nodes,
                    max_worker_nodes = excluded.max_worker_nodes,
                    max_source_nodes = excluded.max_source_nodes,
                    max_destination_nodes = excluded.max_destination_nodes,
                    default_processes_per_node = excluded.default_processes_per_node,
                    max_processes_per_node = excluded.max_processes_per_node,
                    default_queue = excluded.default_queue,
                    default_priority_class = excluded.default_priority_class,
                    default_timeout_seconds = excluded.default_timeout_seconds,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (
                    policy.operation,
                    policy.default_worker_nodes,
                    policy.default_source_nodes,
                    policy.default_destination_nodes,
                    policy.max_worker_nodes,
                    policy.max_source_nodes,
                    policy.max_destination_nodes,
                    policy.default_processes_per_node,
                    policy.max_processes_per_node,
                    policy.default_queue,
                    policy.default_priority_class,
                    policy.default_timeout_seconds,
                    1 if policy.enabled else 0,
                    now,
                    actor,
                ),
            )
            self._insert_control_mutation(
                connection,
                actor=actor,
                mutation_kind=mutation_kind,
                payload=policy.model_dump(mode="json"),
                mutation_class="data_management_policy",
                operation=policy.operation,
                target_key=policy.operation,
                status="stored",
                created_at=now,
            )
        return policy.operation


    def get_data_management_policy(self, operation: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM data_management_policies WHERE operation = ?",
                (operation,),
            ).fetchone()
        return self._decode_data_management_policy(row_to_dict(row)) if row else None


    def list_data_management_policies(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM data_management_policies ORDER BY operation"
            ).fetchall()
        return [self._decode_data_management_policy(row_to_dict(row)) for row in rows]


    @staticmethod
    def _decode_data_management_policy(policy: dict[str, Any]) -> dict[str, Any]:
        if policy:
            policy["enabled"] = bool(policy.get("enabled"))
        return policy
