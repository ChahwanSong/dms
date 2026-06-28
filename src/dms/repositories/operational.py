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


class OperationalMixin:
    """Operational queries, control state, scheduling, agent reports."""


    def active_work_for_storage(self, storage_name: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM requests ORDER BY commit_order ASC"
            ).fetchall()
            for row in rows:
                request = self._decode_request(row_to_dict(row))
                if request["status"] in TERMINAL_LIFECYCLE_STATES:
                    continue
                if storage_name in _storage_names_in_payload(
                    request["payload_summary"]
                ):
                    return {
                        "kind": "request",
                        "id": request["request_id"],
                        "status": request["status"],
                    }
            rows = connection.execute(
                "SELECT * FROM data_jobs ORDER BY updated_at ASC"
            ).fetchall()
            for row in rows:
                job = self._decode_data_job(row_to_dict(row))
                if job["state"] in TERMINAL_DATA_JOB_STATES:
                    continue
                if job["storage_name"] == storage_name:
                    return {
                        "kind": "data_job",
                        "id": job["job_id"],
                        "state": job["state"],
                    }
        return None


    def active_work_for_resource(
        self, *, resource_kind: str, resource_key: str
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM requests
                WHERE resource_kind = ? AND resource_key = ?
                ORDER BY commit_order ASC
                """,
                (resource_kind, resource_key),
            ).fetchall()
        for row in rows:
            request = self._decode_request(row_to_dict(row))
            if request["status"] in TERMINAL_LIFECYCLE_STATES:
                continue
            return {
                "kind": "request",
                "id": request["request_id"],
                "status": request["status"],
            }
        return None


    def ingest_agent_report(self, report: dict[str, Any]) -> str:
        report_id = new_id("agent")
        now = iso_now()
        reported_at = report.get("reported_at") or now
        schema_version = report.get("schema_version") or "phase3.v1"
        capability_summary = _agent_capability_summary(report)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_reports (
                    report_id, cluster_name, node_name, node_uid, worker_role,
                    report_json, capability_summary, freshness_status, reported_at,
                    received_at, stale_at, schema_version, validation_status,
                    validation_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    report["cluster_name"],
                    report["node_name"],
                    report["node_uid"],
                    report["worker_role"],
                    json_dumps(report),
                    json_dumps(capability_summary),
                    "Fresh",
                    reported_at,
                    now,
                    None,
                    schema_version,
                    "Accepted",
                    None,
                ),
            )
        return report_id


    def list_agent_reports(
        self,
        *,
        freshness: str | None = None,
        stale_seconds: int | None = None,
        update_stale: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if stale_seconds is not None and update_stale:
            self.mark_stale_agent_reports(stale_seconds=stale_seconds)
        filters: list[str] = []
        params: list[Any] = []
        if freshness:
            filters.append("freshness_status = ?")
            params.append(freshness)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM agent_reports
                {where}
                ORDER BY reported_at DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [self._decode_agent_report(row_to_dict(row)) for row in rows]


    def list_agent_metric_samples(
        self, *, since_iso: str, limit: int = 20000
    ) -> list[dict[str, Any]]:
        """Flat per-report OS-metric samples (cpu/mem/load/disk) reported since
        ``since_iso``, oldest→newest, for the node-metrics dashboard time-series.

        Parses ``report_json`` in Python so it stays portable across SQLite/Postgres
        (no JSON SQL functions). The newest ``limit`` rows in the window are kept; any
        excess (oldest) is dropped."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT cluster_name, node_name, worker_role, reported_at, report_json
                FROM agent_reports
                WHERE reported_at >= ?
                ORDER BY reported_at DESC
                LIMIT ?
                """,
                (since_iso, limit),
            ).fetchall()
        samples: list[dict[str, Any]] = []
        for row in rows:
            record = row_to_dict(row)
            report = json_loads(record.get("report_json")) or {}
            os_metrics = report.get("os_metrics") or {}
            cpu = os_metrics.get("cpu") or {}
            memory = os_metrics.get("memory") or {}
            load = os_metrics.get("load") or {}
            disk = os_metrics.get("disk") or {}
            samples.append(
                {
                    "cluster_name": record.get("cluster_name"),
                    "node_name": record.get("node_name"),
                    "worker_role": record.get("worker_role"),
                    "reported_at": record.get("reported_at"),
                    "cpu_percent": cpu.get("percent"),
                    "cpu_cores": cpu.get("cores"),
                    "mem_used_pct": memory.get("used_pct"),
                    "mem_total_kb": memory.get("total_kb"),
                    "load1": load.get("load1"),
                    "disk_used_pct": disk.get("used_pct"),
                }
            )
        samples.reverse()  # chronological order for the time-series
        return samples


    def mark_stale_agent_reports(self, *, stale_seconds: int) -> int:
        now = utcnow()
        now_iso = now.isoformat()
        count = 0
        with self.database.connect() as connection:
            rows = connection.execute("""
                SELECT report_id, reported_at, received_at
                FROM agent_reports
                WHERE freshness_status = 'Fresh'
                """).fetchall()
            for row in rows:
                report = row_to_dict(row)
                basis = _parse_iso(
                    report.get("reported_at") or report.get("received_at")
                )
                if (now - basis).total_seconds() <= stale_seconds:
                    continue
                connection.execute(
                    """
                    UPDATE agent_reports
                    SET freshness_status = 'Stale', stale_at = ?
                    WHERE report_id = ?
                    """,
                    (now_iso, report["report_id"]),
                )
                count += 1
        return count


    def list_action_required(self, limit: int = 100) -> list[dict[str, Any]]:
        actionable = (
            LifecycleState.BLOCKED.value,
            LifecycleState.STALE_CLAIM.value,
            LifecycleState.RECOVERY_NEEDED.value,
            LifecycleState.VERIFICATION_FAILED.value,
            LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value,
            LifecycleState.BACKEND_APPLY_FAILED.value,
        )
        placeholders = ",".join(["?"] * len(actionable))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM requests
                WHERE status IN ({placeholders})
                ORDER BY commit_order ASC
                LIMIT ?
                """,
                (*actionable, limit),
            ).fetchall()
        return [self._decode_request(row_to_dict(row)) for row in rows]


    def control_state(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = self._ensure_control_state(connection)
        return self._decode_control_state(row_to_dict(row))


    def scheduling_blocked(self) -> bool:
        return self._control_state_blocks_scheduling(self.control_state())


    def update_control_state(
        self,
        *,
        maintenance_mode: bool,
        drain_mode: bool,
        scheduling_blocked: bool,
        reason: str,
        actor: str,
        mutation_kind: str,
        payload: dict[str, Any] | None = None,
        result_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = iso_now()
        with self.database.connect() as connection:
            before = self._decode_control_state(
                row_to_dict(self._ensure_control_state(connection))
            )
            connection.execute(
                """
                UPDATE dms_control_state
                SET maintenance_mode = ?, drain_mode = ?, scheduling_blocked = ?,
                    reason = ?, changed_by = ?, changed_at = ?
                WHERE singleton_id = 'default'
                """,
                (
                    1 if maintenance_mode else 0,
                    1 if drain_mode else 0,
                    1 if scheduling_blocked else 0,
                    reason,
                    actor,
                    now,
                ),
            )
            after = self._decode_control_state(
                row_to_dict(self._ensure_control_state(connection))
            )
            self._insert_control_mutation(
                connection,
                actor=actor,
                mutation_kind=mutation_kind,
                payload=payload or {"reason": reason},
                mutation_class="control_state",
                operation=mutation_kind.split(".")[-1],
                target_key="default",
                status="Succeeded",
                result_summary=result_summary or {},
                before_state=before,
                after_state=after,
                created_at=now,
            )
        return after


    def record_control_mutation(
        self,
        *,
        actor: str,
        mutation_kind: str,
        payload: dict[str, Any],
        status: str = "Succeeded",
        result_summary: dict[str, Any] | None = None,
    ) -> str:
        now = iso_now()
        with self.database.connect() as connection:
            return self._insert_control_mutation(
                connection,
                actor=actor,
                mutation_kind=mutation_kind,
                payload=payload,
                mutation_class="control",
                operation=mutation_kind.split(".")[-1],
                target_key="default",
                status=status,
                result_summary=result_summary or {},
                before_state={},
                after_state={},
                created_at=now,
            )


    def list_control_mutations(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM control_mutations ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        mutations = rows_to_dicts(rows)
        for mutation in mutations:
            mutation["payload"] = json_loads(mutation["payload"]) or {}
            mutation["result_summary"] = (
                json_loads(mutation.get("result_summary")) or {}
            )
            mutation["before_state"] = json_loads(mutation.get("before_state")) or {}
            mutation["after_state"] = json_loads(mutation.get("after_state")) or {}
        return mutations


    def _ensure_control_state(self, connection: Any) -> Any:
        row = connection.execute(
            "SELECT * FROM dms_control_state WHERE singleton_id = 'default'",
        ).fetchone()
        if not row:
            now = iso_now()
            connection.execute(
                """
                INSERT INTO dms_control_state (
                    singleton_id, maintenance_mode, drain_mode,
                    scheduling_blocked, reason, changed_by, changed_at
                ) VALUES ('default', 0, 0, 0, '', 'system', ?)
                """,
                (now,),
            )
            row = connection.execute(
                "SELECT * FROM dms_control_state WHERE singleton_id = 'default'",
            ).fetchone()
        return row


    @staticmethod
    def _decode_control_state(state: dict[str, Any]) -> dict[str, Any]:
        return {
            **state,
            "maintenance_mode": bool(state.get("maintenance_mode")),
            "drain_mode": bool(state.get("drain_mode")),
            "scheduling_blocked": bool(state.get("scheduling_blocked")),
        }


    @staticmethod
    def _control_state_blocks_scheduling(state: dict[str, Any]) -> bool:
        return bool(
            state.get("maintenance_mode")
            or state.get("drain_mode")
            or state.get("scheduling_blocked")
        )


    def _insert_control_mutation(
        self,
        connection: Any,
        *,
        actor: str,
        mutation_kind: str,
        payload: dict[str, Any],
        created_at: str,
        mutation_class: str | None = None,
        operation: str | None = None,
        target_key: str | None = None,
        status: str | None = None,
        result_summary: dict[str, Any] | None = None,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
    ) -> str:
        mutation_id = new_id("ctl")
        connection.execute(
            """
            INSERT INTO control_mutations (
                mutation_id, actor, mutation_kind, payload, mutation_class,
                operation, target_key, status, result_summary, before_state,
                after_state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mutation_id,
                actor,
                mutation_kind,
                json_dumps(payload),
                mutation_class,
                operation,
                target_key,
                status,
                json_dumps(result_summary or {}),
                json_dumps(before_state or {}),
                json_dumps(after_state or {}),
                created_at,
            ),
        )
        return mutation_id


    def _decode_agent_report(self, report: dict[str, Any]) -> dict[str, Any]:
        if report:
            report["report"] = json_loads(report.pop("report_json")) or {}
            report["capability_summary"] = (
                json_loads(report.get("capability_summary")) or {}
            )
        return report
