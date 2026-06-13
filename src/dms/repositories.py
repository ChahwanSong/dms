from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any
from uuid import uuid4

from .db import Database
from .domain import (
    DataJobState,
    DataManagementPolicyInput,
    IdentityMappingInput,
    IdentityMappingStatus,
    LifecycleState,
    ResourceKind,
    StorageMappingInput,
    TERMINAL_LIFECYCLE_STATES,
    WorkerRole,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utcnow().isoformat()


def iso_at(seconds: int) -> str:
    return (utcnow() + timedelta(seconds=seconds)).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def json_loads(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)


TERMINAL_DATA_JOB_STATES = {
    DataJobState.AUTHORIZATION_FAILED.value,
    DataJobState.PREFLIGHT_FAILED.value,
    DataJobState.PREVIEW_EXPIRED.value,
    DataJobState.SUCCEEDED.value,
    DataJobState.FAILED.value,
    DataJobState.CANCELLED.value,
    DataJobState.TIMED_OUT.value,
}

DEFAULT_REQUEST_LIST_LIMIT = 1000

LOGGER = logging.getLogger(__name__)

ACTIVE_PLAN_STATES = (
    LifecycleState.PLANNED.value,
    LifecycleState.CLAIMED.value,
    LifecycleState.RUNNING.value,
    LifecycleState.APPLYING.value,
    LifecycleState.VERIFYING.value,
    LifecycleState.BLOCKED.value,
)
ACTIVE_RUN_STATES = (
    LifecycleState.CLAIMED.value,
    LifecycleState.RUNNING.value,
    LifecycleState.APPLYING.value,
    LifecycleState.VERIFYING.value,
)
ATTENTION_RUN_STATES = (
    LifecycleState.BLOCKED.value,
    LifecycleState.STALE_CLAIM.value,
    LifecycleState.RECOVERY_NEEDED.value,
    LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value,
    LifecycleState.BACKEND_APPLY_FAILED.value,
)


class SchedulingBlocked(RuntimeError):
    pass


def row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [row_to_dict(row) for row in rows]


@dataclass
class ObservabilityRepository:
    database: Database

    def record_event(
        self,
        *,
        component: str,
        severity: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> str:
        event_id = new_id("evt")
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO diagnostic_events (
                    event_id, correlation_id, component, severity, event_type,
                    message, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    correlation_id,
                    component,
                    severity,
                    event_type,
                    message,
                    json_dumps(payload or {}),
                    iso_now(),
                ),
            )
        return event_id

    def safe_record_event(
        self,
        *,
        component: str,
        severity: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> str | None:
        try:
            return self.record_event(
                component=component,
                severity=severity,
                event_type=event_type,
                message=message,
                payload=payload,
                correlation_id=correlation_id,
            )
        except Exception:  # noqa: BLE001 - diagnostic writes must not alter lifecycle.
            LOGGER.warning(
                "observability event write failed: component=%s event_type=%s",
                component,
                event_type,
                exc_info=True,
            )
            return None

    def list_events(
        self, *, correlation_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if correlation_id:
                rows = connection.execute(
                    """
                    SELECT * FROM diagnostic_events
                    WHERE correlation_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (correlation_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM diagnostic_events
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        events = rows_to_dicts(rows)
        for event in events:
            event["payload"] = json_loads(event["payload"]) or {}
        return events


@dataclass
class DmsRepository:
    database: Database

    def create_request(
        self,
        *,
        requester_id: str,
        actor: str,
        operation: str,
        resource_kind: str,
        resource_key: str,
        payload: dict[str, Any],
    ) -> str:
        request_id = new_id("req")
        now = iso_now()
        with self.database.connect() as connection:
            next_order = self._next_commit_order(connection)
            connection.execute(
                """
                INSERT INTO requests (
                    request_id, requester_id, actor, operation, resource_kind,
                    resource_key, payload_summary, requested_at, status, commit_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    requester_id,
                    actor,
                    operation,
                    resource_kind,
                    resource_key,
                    json_dumps(payload),
                    now,
                    LifecycleState.PERSISTED.value,
                    next_order,
                ),
            )
            self._insert_transition(
                connection,
                request_id=request_id,
                plan_id=None,
                run_id=None,
                from_state=None,
                to_state=LifecycleState.RECEIVED.value,
                reason="request accepted by frontend",
                actor=actor,
                created_at=now,
            )
            self._insert_transition(
                connection,
                request_id=request_id,
                plan_id=None,
                run_id=None,
                from_state=LifecycleState.RECEIVED.value,
                to_state=LifecycleState.PERSISTED.value,
                reason="request persisted before backend side effect",
                actor=actor,
                created_at=now,
            )
        return request_id

    def record_authorization_failed(self, request_id: str, message: str) -> str:
        result_id = new_id("res")
        now = iso_now()
        with self.database.connect() as connection:
            request = self._get_request(connection, request_id)
            connection.execute(
                "UPDATE requests SET status = ? WHERE request_id = ?",
                (LifecycleState.AUTHORIZATION_FAILED.value, request_id),
            )
            connection.execute(
                """
                INSERT INTO results (
                    result_id, request_id, plan_id, run_id, terminal_status,
                    error_category, message, verification_summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    request_id,
                    None,
                    None,
                    LifecycleState.AUTHORIZATION_FAILED.value,
                    "authorization",
                    message,
                    json_dumps({"backend_side_effect": False}),
                    now,
                ),
            )
            self._insert_transition(
                connection,
                request_id=request_id,
                plan_id=None,
                run_id=None,
                from_state=request.get("status"),
                to_state=LifecycleState.AUTHORIZATION_FAILED.value,
                reason=message,
                actor="authorization-policy",
                created_at=now,
            )
        return result_id

    def list_plannable_requests(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM requests
                WHERE status = ?
                ORDER BY commit_order ASC
                LIMIT ?
                """,
                (LifecycleState.PERSISTED.value, limit),
            ).fetchall()
        return [self._decode_request(row_to_dict(row)) for row in rows]

    def get_request(self, request_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            request = self._get_request(connection, request_id)
        return self._decode_request(request)

    def list_requests(
        self,
        *,
        requester_id: str,
        limit: int = DEFAULT_REQUEST_LIST_LIMIT,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        if not requester_id.strip():
            raise ValueError("requester_id is required")
        where = ["requester_id = ?"]
        params: list[Any] = [requester_id]
        if since is not None:
            where.append("requested_at >= ?")
            params.append(since)
        if until is not None:
            where.append("requested_at < ?")
            params.append(until)
        params.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM requests
                WHERE {' AND '.join(where)}
                ORDER BY commit_order DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._decode_request(row_to_dict(row)) for row in rows]

    def list_requests_for_resource(
        self,
        *,
        resource_kind: str,
        resource_key: str,
        operations: tuple[str, ...] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        where = ["resource_kind = ?", "resource_key = ?"]
        params: list[Any] = [resource_kind, resource_key]
        if operations:
            placeholders = ",".join(["?"] * len(operations))
            where.append(f"operation IN ({placeholders})")
            params.extend(operations)
        params.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM requests
                WHERE {' AND '.join(where)}
                ORDER BY commit_order DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._decode_request(row_to_dict(row)) for row in rows]

    def update_request_status(
        self,
        request_id: str,
        status: LifecycleState | str,
        *,
        reason: str,
        actor: str,
    ) -> None:
        to_state = status.value if isinstance(status, LifecycleState) else status
        now = iso_now()
        with self.database.connect() as connection:
            request = self._get_request(connection, request_id)
            connection.execute(
                "UPDATE requests SET status = ? WHERE request_id = ?",
                (to_state, request_id),
            )
            self._insert_transition(
                connection,
                request_id=request_id,
                plan_id=None,
                run_id=None,
                from_state=request.get("status"),
                to_state=to_state,
                reason=reason,
                actor=actor,
                created_at=now,
            )

    def find_prior_active_request(
        self, request: dict[str, Any]
    ) -> dict[str, Any] | None:
        terminal = tuple(TERMINAL_LIFECYCLE_STATES)
        placeholders = ",".join(["?"] * len(terminal))
        with self.database.connect() as connection:
            row = connection.execute(
                f"""
                SELECT * FROM requests
                WHERE resource_kind = ?
                  AND resource_key = ?
                  AND commit_order < ?
                  AND status NOT IN ({placeholders})
                ORDER BY commit_order ASC
                LIMIT 1
                """,
                (
                    request["resource_kind"],
                    request["resource_key"],
                    request["commit_order"],
                    *terminal,
                ),
            ).fetchone()
        return self._decode_request(row_to_dict(row)) if row else None

    def create_plan(
        self,
        *,
        request_id: str,
        worker_role: WorkerRole,
        operation_kind: str,
        resource_key: str,
        desired_state: dict[str, Any],
        precondition: dict[str, Any],
        execution_metadata: dict[str, Any],
    ) -> str:
        plan_id = new_id("plan")
        now = iso_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO plans (
                    plan_id, request_id, status, worker_role, operation_kind,
                    resource_key, desired_state, precondition, execution_metadata,
                    attempt_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    request_id,
                    LifecycleState.PLANNED.value,
                    worker_role.value,
                    operation_kind,
                    resource_key,
                    json_dumps(desired_state),
                    json_dumps(precondition),
                    json_dumps(execution_metadata),
                    0,
                    now,
                    now,
                ),
            )
            request = self._get_request(connection, request_id)
            connection.execute(
                "UPDATE requests SET status = ? WHERE request_id = ?",
                (LifecycleState.PLANNED.value, request_id),
            )
            self._insert_transition(
                connection,
                request_id=request_id,
                plan_id=plan_id,
                run_id=None,
                from_state=request.get("status"),
                to_state=LifecycleState.PLANNED.value,
                reason="planner created executable plan",
                actor="planner",
                created_at=now,
            )
        return plan_id

    def list_claimable_plans(
        self, worker_role: WorkerRole, limit: int = 10
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM plans
                WHERE worker_role = ?
                  AND status = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (worker_role.value, LifecycleState.PLANNED.value, limit),
            ).fetchall()
        return [self._decode_plan(row_to_dict(row)) for row in rows]

    def list_active_plans(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        worker_role: str | WorkerRole | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        selected_statuses = statuses or ACTIVE_PLAN_STATES
        where = [f"plans.status IN ({','.join(['?'] * len(selected_statuses))})"]
        params: list[Any] = list(selected_statuses)
        if worker_role:
            role = (
                worker_role.value
                if isinstance(worker_role, WorkerRole)
                else worker_role
            )
            where.append("plans.worker_role = ?")
            params.append(role)
        params.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    plans.*,
                    requests.requester_id,
                    requests.actor AS request_actor,
                    requests.resource_kind,
                    requests.status AS request_status
                FROM plans
                JOIN requests ON requests.request_id = plans.request_id
                WHERE {' AND '.join(where)}
                ORDER BY plans.created_at ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._decode_plan(row_to_dict(row)) for row in rows]

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        return self._decode_plan(row_to_dict(row))

    def get_plan_by_request(self, request_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM plans WHERE request_id = ? ORDER BY created_at DESC LIMIT 1",
                (request_id,),
            ).fetchone()
        return self._decode_plan(row_to_dict(row)) if row else None

    def update_plan_status(
        self,
        plan_id: str,
        status: LifecycleState | str,
        *,
        reason: str,
        actor: str,
    ) -> None:
        to_state = status.value if isinstance(status, LifecycleState) else status
        now = iso_now()
        with self.database.connect() as connection:
            plan = self._get_plan(connection, plan_id)
            connection.execute(
                "UPDATE plans SET status = ?, updated_at = ? WHERE plan_id = ?",
                (to_state, now, plan_id),
            )
            self._insert_transition(
                connection,
                request_id=plan["request_id"],
                plan_id=plan_id,
                run_id=None,
                from_state=plan.get("status"),
                to_state=to_state,
                reason=reason,
                actor=actor,
                created_at=now,
            )

    def update_plan_metadata(self, plan_id: str, metadata: dict[str, Any]) -> None:
        now = iso_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE plans
                SET execution_metadata = ?, updated_at = ?
                WHERE plan_id = ?
                """,
                (json_dumps(metadata), now, plan_id),
            )

    def claim_plan(
        self,
        *,
        plan_id: str,
        worker_id: str,
        executor_id: str,
        lease_seconds: int,
    ) -> str:
        run_id = new_id("run")
        now = iso_now()
        with self.database.connect() as connection:
            control_state = row_to_dict(self._ensure_control_state(connection))
            if self._control_state_blocks_scheduling(control_state):
                raise SchedulingBlocked("DMS scheduling is blocked")
            plan = self._get_plan(connection, plan_id)
            if plan["status"] != LifecycleState.PLANNED.value:
                raise RuntimeError(f"plan is not claimable: {plan_id}")
            cursor = connection.execute(
                """
                UPDATE plans
                SET status = ?, attempt_count = attempt_count + 1, updated_at = ?
                WHERE plan_id = ?
                  AND status = ?
                """,
                (
                    LifecycleState.CLAIMED.value,
                    now,
                    plan_id,
                    LifecycleState.PLANNED.value,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"plan is not claimable: {plan_id}")
            connection.execute(
                "UPDATE requests SET status = ? WHERE request_id = ?",
                (LifecycleState.CLAIMED.value, plan["request_id"]),
            )
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, request_id, plan_id, worker_id, executor_id,
                    worker_role, lease_expires_at, heartbeat_at, state,
                    started_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    plan["request_id"],
                    plan_id,
                    worker_id,
                    executor_id,
                    plan["worker_role"],
                    iso_at(lease_seconds),
                    now,
                    LifecycleState.CLAIMED.value,
                    now,
                    now,
                ),
            )
            self._insert_transition(
                connection,
                request_id=plan["request_id"],
                plan_id=plan_id,
                run_id=run_id,
                from_state=LifecycleState.PLANNED.value,
                to_state=LifecycleState.CLAIMED.value,
                reason=f"claimed by {worker_id}",
                actor=worker_id,
                created_at=now,
            )
        return run_id

    def update_run_state(
        self,
        run_id: str,
        state: LifecycleState | str,
        *,
        reason: str,
        actor: str,
        update_request_and_plan: bool = True,
    ) -> None:
        to_state = state.value if isinstance(state, LifecycleState) else state
        now = iso_now()
        with self.database.connect() as connection:
            run = self._get_run(connection, run_id)
            connection.execute(
                """
                UPDATE runs
                SET state = ?, heartbeat_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (to_state, now, now, run_id),
            )
            if update_request_and_plan:
                connection.execute(
                    "UPDATE plans SET status = ?, updated_at = ? WHERE plan_id = ?",
                    (to_state, now, run["plan_id"]),
                )
                connection.execute(
                    "UPDATE requests SET status = ? WHERE request_id = ?",
                    (to_state, run["request_id"]),
                )
            self._insert_transition(
                connection,
                request_id=run["request_id"],
                plan_id=run["plan_id"],
                run_id=run_id,
                from_state=run.get("state"),
                to_state=to_state,
                reason=reason,
                actor=actor,
                created_at=now,
            )

    def heartbeat_run(self, run_id: str, lease_seconds: int) -> None:
        now = iso_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (now, iso_at(lease_seconds), now, run_id),
            )

    def mark_stale_runs(self, *, actor: str = "recovery-monitor") -> int:
        now = iso_now()
        active_states = (
            LifecycleState.CLAIMED.value,
            LifecycleState.RUNNING.value,
            LifecycleState.APPLYING.value,
            LifecycleState.VERIFYING.value,
        )
        placeholders = ",".join(["?"] * len(active_states))
        count = 0
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM runs
                WHERE state IN ({placeholders})
                  AND lease_expires_at < ?
                """,
                (*active_states, now),
            ).fetchall()
            for row in rows:
                run = row_to_dict(row)
                target_state = (
                    LifecycleState.STALE_CLAIM.value
                    if run["state"] == LifecycleState.CLAIMED.value
                    else LifecycleState.RECOVERY_NEEDED.value
                )
                connection.execute(
                    "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                    (target_state, now, run["run_id"]),
                )
                connection.execute(
                    "UPDATE plans SET status = ?, updated_at = ? WHERE plan_id = ?",
                    (target_state, now, run["plan_id"]),
                )
                connection.execute(
                    "UPDATE requests SET status = ? WHERE request_id = ?",
                    (target_state, run["request_id"]),
                )
                self._insert_transition(
                    connection,
                    request_id=run["request_id"],
                    plan_id=run["plan_id"],
                    run_id=run["run_id"],
                    from_state=run["state"],
                    to_state=target_state,
                    reason="worker lease expired",
                    actor=actor,
                    created_at=now,
                )
                count += 1
        return count

    def complete_result(
        self,
        *,
        request_id: str,
        plan_id: str | None,
        run_id: str | None,
        terminal_status: LifecycleState | str,
        message: str,
        verification_summary: dict[str, Any],
        error_category: str | None = None,
        actor: str,
    ) -> str:
        status = (
            terminal_status.value
            if isinstance(terminal_status, LifecycleState)
            else terminal_status
        )
        result_id = new_id("res")
        now = iso_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO results (
                    result_id, request_id, plan_id, run_id, terminal_status,
                    error_category, message, verification_summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    request_id,
                    plan_id,
                    run_id,
                    status,
                    error_category,
                    message,
                    json_dumps(verification_summary),
                    now,
                ),
            )
            if plan_id:
                connection.execute(
                    "UPDATE plans SET status = ?, updated_at = ? WHERE plan_id = ?",
                    (status, now, plan_id),
                )
            if run_id:
                connection.execute(
                    "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                    (status, now, run_id),
                )
            connection.execute(
                "UPDATE requests SET status = ? WHERE request_id = ?",
                (status, request_id),
            )
            self._insert_transition(
                connection,
                request_id=request_id,
                plan_id=plan_id,
                run_id=run_id,
                from_state=None,
                to_state=status,
                reason=message,
                actor=actor,
                created_at=now,
            )
        return result_id

    def get_results(self, request_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM results WHERE request_id = ? ORDER BY created_at ASC",
                (request_id,),
            ).fetchall()
        results = rows_to_dicts(rows)
        for result in results:
            result["verification_summary"] = (
                json_loads(result["verification_summary"]) or {}
            )
        return results

    def list_results_for_operations(
        self, *, operations: tuple[str, ...], limit: int = 1000
    ) -> list[dict[str, Any]]:
        if not operations:
            return []
        placeholders = ",".join(["?"] * len(operations))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    results.*,
                    requests.operation,
                    requests.resource_kind,
                    requests.resource_key,
                    requests.requester_id,
                    requests.actor,
                    requests.payload_summary,
                    requests.commit_order,
                    requests.status AS request_status
                FROM results
                JOIN requests ON requests.request_id = results.request_id
                WHERE requests.operation IN ({placeholders})
                ORDER BY requests.commit_order DESC, results.created_at DESC
                LIMIT ?
                """,
                (*operations, limit),
            ).fetchall()
        results = rows_to_dicts(rows)
        for result in results:
            result["verification_summary"] = (
                json_loads(result["verification_summary"]) or {}
            )
            result["payload_summary"] = json_loads(result["payload_summary"]) or {}
        return results

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

    def get_storage_mapping_by_cluster_storage_class(
        self, cluster_name: str, storage_class_name: str
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM storage_mappings
                WHERE cluster_name = ? AND storage_class_name = ?
                """,
                (cluster_name, storage_class_name),
            ).fetchone()
        return self._decode_storage_mapping(row_to_dict(row)) if row else None

    def list_storage_mappings(
        self,
        limit: int = 100,
        *,
        cluster_name: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if cluster_name is not None:
                rows = connection.execute(
                    "SELECT * FROM storage_mappings WHERE cluster_name = ? ORDER BY updated_at DESC LIMIT ?",
                    (cluster_name, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM storage_mappings ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
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

    def upsert_default_quota_policy(
        self,
        *,
        resource_kind: str,
        resource_type: str,
        quota: dict[str, Any],
        actor: str,
    ) -> str:
        policy_id = f"{resource_kind}:{resource_type}"
        now = iso_now()
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT policy_id FROM default_quota_policies
                WHERE resource_kind = ? AND resource_type = ?
                """,
                (resource_kind, resource_type),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE default_quota_policies
                    SET quota = ?, updated_at = ?
                    WHERE resource_kind = ? AND resource_type = ?
                    """,
                    (json_dumps(quota), now, resource_kind, resource_type),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO default_quota_policies (
                        policy_id, resource_kind, resource_type, quota, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (policy_id, resource_kind, resource_type, json_dumps(quota), now),
                )
            self._insert_control_mutation(
                connection,
                actor=actor,
                mutation_kind="default_quota_policy.upsert",
                payload={
                    "resource_kind": resource_kind,
                    "resource_type": resource_type,
                    "quota": quota,
                },
                created_at=now,
            )
        return policy_id

    def get_default_quota_policy(
        self, *, resource_kind: str, resource_type: str
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM default_quota_policies
                WHERE resource_kind = ? AND resource_type = ?
                """,
                (resource_kind, resource_type),
            ).fetchone()
        policy = row_to_dict(row) if row else None
        if not policy:
            return None
        policy["quota"] = json_loads(policy["quota"]) or {}
        return policy

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

    def create_data_job(
        self,
        *,
        request_id: str,
        operation: str,
        storage_name: str,
        source: str | None,
        destination: str | None,
        target: str | None,
        priority: int,
        worker_pool: dict[str, Any],
        selected_tool: str | None = None,
        state: DataJobState = DataJobState.PENDING,
        normalized_target: dict[str, Any] | None = None,
        preflight_result: dict[str, Any] | None = None,
        volcano_job_ref: dict[str, Any] | None = None,
        result_summary: dict[str, Any] | None = None,
        artifact_uri: str | None = None,
        log_uri: str | None = None,
    ) -> str:
        job_id = new_id("job")
        now = iso_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO data_jobs (
                    job_id, request_id, operation, storage_name, source,
                    destination, target, priority, selected_tool, worker_pool,
                    state, artifact_uri, normalized_target, preflight_result,
                    volcano_job_ref, result_summary, log_uri, preview_expires_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    request_id,
                    operation,
                    storage_name,
                    source,
                    destination,
                    target,
                    priority,
                    selected_tool,
                    json_dumps(worker_pool),
                    state.value,
                    artifact_uri,
                    json_dumps(normalized_target or {}),
                    json_dumps(preflight_result or {}),
                    json_dumps(volcano_job_ref or {}),
                    json_dumps(result_summary or {}),
                    log_uri,
                    None,
                    now,
                    now,
                ),
            )
        return job_id

    def get_data_job(self, job_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM data_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._decode_data_job(row_to_dict(row))

    def get_data_job_by_request(self, request_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM data_jobs WHERE request_id = ? ORDER BY created_at DESC LIMIT 1",
                (request_id,),
            ).fetchone()
        return self._decode_data_job(row_to_dict(row)) if row else None

    def update_data_job(
        self,
        job_id: str,
        *,
        state: DataJobState | str | None = None,
        selected_tool: str | None = None,
        worker_pool: dict[str, Any] | None = None,
        artifact_uri: str | None = None,
        normalized_target: dict[str, Any] | None = None,
        preflight_result: dict[str, Any] | None = None,
        volcano_job_ref: dict[str, Any] | None = None,
        result_summary: dict[str, Any] | None = None,
        log_uri: str | None = None,
        preview_expires_at: str | None = None,
    ) -> None:
        job = self.get_data_job(job_id)
        now = iso_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE data_jobs
                SET state = ?, selected_tool = ?, worker_pool = ?, artifact_uri = ?,
                    normalized_target = ?, preflight_result = ?, volcano_job_ref = ?,
                    result_summary = ?, log_uri = ?, preview_expires_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    (state.value if isinstance(state, DataJobState) else state)
                    or job["state"],
                    (
                        selected_tool
                        if selected_tool is not None
                        else job["selected_tool"]
                    ),
                    json_dumps(
                        worker_pool if worker_pool is not None else job["worker_pool"]
                    ),
                    artifact_uri if artifact_uri is not None else job["artifact_uri"],
                    json_dumps(
                        normalized_target
                        if normalized_target is not None
                        else job.get("normalized_target") or {}
                    ),
                    json_dumps(
                        preflight_result
                        if preflight_result is not None
                        else job.get("preflight_result") or {}
                    ),
                    json_dumps(
                        volcano_job_ref
                        if volcano_job_ref is not None
                        else job.get("volcano_job_ref") or {}
                    ),
                    json_dumps(
                        result_summary
                        if result_summary is not None
                        else job.get("result_summary") or {}
                    ),
                    log_uri if log_uri is not None else job.get("log_uri"),
                    (
                        preview_expires_at
                        if preview_expires_at is not None
                        else job["preview_expires_at"]
                    ),
                    now,
                    job_id,
                ),
            )

    def list_data_jobs(
        self,
        limit: int = 100,
        *,
        requester_id: str | None = None,
        operation: str | None = None,
        storage_name: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        params: list[Any] = []
        if requester_id:
            filters.append("requests.requester_id = ?")
            params.append(requester_id)
        if operation:
            filters.append("data_jobs.operation = ?")
            params.append(operation)
        if storage_name:
            filters.append("data_jobs.storage_name = ?")
            params.append(storage_name)
        if state:
            filters.append("data_jobs.state = ?")
            params.append(state)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    data_jobs.*,
                    requests.requester_id,
                    requests.actor AS request_actor,
                    requests.resource_key,
                    requests.payload_summary
                FROM data_jobs
                JOIN requests ON requests.request_id = data_jobs.request_id
                {where}
                ORDER BY data_jobs.updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._decode_data_job(row_to_dict(row)) for row in rows]

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

    def list_runs(
        self, *, states: tuple[str, ...] | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if states:
                placeholders = ",".join(["?"] * len(states))
                rows = connection.execute(
                    f"""
                    SELECT * FROM runs
                    WHERE state IN ({placeholders})
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (*states, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return rows_to_dicts(rows)

    def list_active_runs(
        self,
        *,
        states: tuple[str, ...] | None = None,
        worker_role: str | WorkerRole | None = None,
        worker_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        selected_states = states or ACTIVE_RUN_STATES
        where = [f"runs.state IN ({','.join(['?'] * len(selected_states))})"]
        params: list[Any] = list(selected_states)
        if worker_role:
            role = (
                worker_role.value
                if isinstance(worker_role, WorkerRole)
                else worker_role
            )
            where.append("runs.worker_role = ?")
            params.append(role)
        if worker_id:
            where.append("runs.worker_id = ?")
            params.append(worker_id)
        params.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    runs.*,
                    plans.status AS plan_status,
                    plans.operation_kind,
                    plans.resource_key,
                    plans.execution_metadata,
                    requests.resource_kind,
                    requests.requester_id,
                    requests.status AS request_status
                FROM runs
                JOIN plans ON plans.plan_id = runs.plan_id
                JOIN requests ON requests.request_id = runs.request_id
                WHERE {' AND '.join(where)}
                ORDER BY runs.updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        decoded = rows_to_dicts(rows)
        for run in decoded:
            run["execution_metadata"] = json_loads(run.get("execution_metadata")) or {}
        return decoded

    def list_state_transitions(self, request_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM state_transitions
                WHERE request_id = ?
                ORDER BY created_at ASC
                """,
                (request_id,),
            ).fetchall()
        return rows_to_dicts(rows)

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

    def _next_commit_order(self, connection: Any) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(commit_order), 0) + 1 AS n FROM requests"
        ).fetchone()
        return int(row["n"])

    def _get_request(self, connection: Any, request_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"request not found: {request_id}")
        return row_to_dict(row)

    def _get_plan(self, connection: Any, plan_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"plan not found: {plan_id}")
        return row_to_dict(row)

    def _get_run(self, connection: Any, run_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"run not found: {run_id}")
        return row_to_dict(row)

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

    def _insert_transition(
        self,
        connection: Any,
        *,
        request_id: str | None,
        plan_id: str | None,
        run_id: str | None,
        from_state: str | None,
        to_state: str,
        reason: str,
        actor: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO state_transitions (
                transition_id, request_id, plan_id, run_id, from_state,
                to_state, reason, actor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("trn"),
                request_id,
                plan_id,
                run_id,
                from_state,
                to_state,
                reason,
                actor,
                created_at,
            ),
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

    def _decode_request(self, request: dict[str, Any]) -> dict[str, Any]:
        if request:
            request["payload_summary"] = json_loads(request["payload_summary"]) or {}
        return request

    def _decode_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        if plan:
            plan["desired_state"] = json_loads(plan["desired_state"]) or {}
            plan["precondition"] = json_loads(plan["precondition"]) or {}
            plan["execution_metadata"] = json_loads(plan["execution_metadata"]) or {}
        return plan

    def _decode_storage_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        if mapping:
            mapping["backend_template"] = json_loads(mapping["backend_template"]) or {}
            mapping["sanity_result"] = json_loads(mapping.get("sanity_result")) or {}
            mapping["readiness"] = json_loads(mapping.get("readiness")) or {}
        return mapping

    def _decode_agent_report(self, report: dict[str, Any]) -> dict[str, Any]:
        if report:
            report["report"] = json_loads(report.pop("report_json")) or {}
            report["capability_summary"] = (
                json_loads(report.get("capability_summary")) or {}
            )
        return report

    def _decode_identity_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        if mapping:
            mapping["groups"] = json_loads(mapping.pop("groups_json")) or []
            mapping["ldap_source_metadata"] = (
                json_loads(mapping.get("ldap_source_metadata")) or {}
            )
        return mapping

    def _decode_data_job(self, job: dict[str, Any]) -> dict[str, Any]:
        if job:
            job["worker_pool"] = json_loads(job["worker_pool"]) or {}
            for key in (
                "normalized_target",
                "preflight_result",
                "volcano_job_ref",
                "result_summary",
            ):
                if key in job:
                    job[key] = json_loads(job.get(key)) or {}
            if "payload_summary" in job:
                job["payload_summary"] = json_loads(job.get("payload_summary")) or {}
            if (
                not job.get("normalized_target")
                and job.get("storage_name")
                and job.get("target")
            ):
                job["normalized_target"] = {
                    "storage_name": job["storage_name"],
                    "path": job["target"],
                }
        return job

    @staticmethod
    def _decode_data_management_policy(policy: dict[str, Any]) -> dict[str, Any]:
        if policy:
            policy["enabled"] = bool(policy.get("enabled"))
        return policy


def _parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, UTC)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _filesystem_block_state(resource: dict[str, Any]) -> dict[str, Any]:
    for section in ("desired_state", "applied_state", "observed_state"):
        state = resource.get(section) or {}
        block_state = state.get("block_state")
        if isinstance(block_state, dict) and block_state:
            return dict(block_state)
    return {"blocked": resource.get("status") == LifecycleState.BLOCKED.value}


def _kubernetes_quota_block_state(resource: dict[str, Any]) -> dict[str, Any]:
    for section in ("desired_state", "applied_state", "observed_state"):
        state = resource.get(section) or {}
        block_state = state.get("block_state")
        if isinstance(block_state, dict) and block_state:
            return dict(block_state)
    return {"blocked": resource.get("status") == LifecycleState.BLOCKED.value}


def _agent_capability_summary(report: dict[str, Any]) -> dict[str, Any]:
    tools = []
    for tool in report.get("tools", []):
        if isinstance(tool, str):
            tools.append(tool)
        elif isinstance(tool, dict) and tool.get("name"):
            tools.append(tool["name"])
    return {
        "mounts": [
            mount.get("storage_name")
            for mount in report.get("mounts", [])
            if mount.get("storage_name")
        ],
        "csi_drivers": [
            csi.get("driver") for csi in report.get("csi", []) if csi.get("driver")
        ],
        "tools": tools,
        "credential_count": len(report.get("credentials", [])),
        "network_count": len(report.get("networks", [])),
    }


def _storage_names_in_payload(payload: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    if payload.get("storage_name"):
        names.add(str(payload["storage_name"]))
    for key in ("target", "source", "destination"):
        value = payload.get(key)
        if isinstance(value, dict) and value.get("storage_name"):
            names.add(str(value["storage_name"]))
    for quota in payload.get("storage_class_quotas") or []:
        if isinstance(quota, dict) and quota.get("storage_name"):
            names.add(str(quota["storage_name"]))
    for key in ("source_storage_name", "destination_storage_name"):
        if payload.get(key):
            names.add(str(payload[key]))
    return names
