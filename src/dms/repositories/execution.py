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


class ExecutionMixin:
    """Plan / run / result execution-pipeline persistence."""


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


    def count_active_plans(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        worker_role: str | WorkerRole | None = None,
    ) -> int:
        """Exact COUNT(*) of active plans using the SAME WHERE clause as
        ``list_active_plans`` (no row transfer, no list-limit saturation). The
        plans→requests join in the list variant is 1:1 via FK, so counting the
        ``plans`` table alone yields the identical cardinality far more cheaply."""
        selected_statuses = statuses or ACTIVE_PLAN_STATES
        where = [f"status IN ({','.join(['?'] * len(selected_statuses))})"]
        params: list[Any] = list(selected_statuses)
        if worker_role:
            role = (
                worker_role.value
                if isinstance(worker_role, WorkerRole)
                else worker_role
            )
            where.append("worker_role = ?")
            params.append(role)
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS n FROM plans WHERE {' AND '.join(where)}",
                tuple(params),
            ).fetchone()
        return int(row_to_dict(row)["n"])


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


    def close_superseded_preview_runs(
        self,
        *,
        actor: str = "recovery-monitor",
        plan_id: str | None = None,
        reason: str | None = None,
    ) -> int:
        """Terminalize orphaned DM preview runs.

        A DM data job parks its run in ``Blocked`` right after a successful preview
        ("waiting for user confirm"; see dm.py). ``Blocked`` is the ONLY run state
        the DM preview uses — RM never blocks a run — so a run in ``Blocked`` is
        always a parked preview run. When the job is confirmed (plan → Planned → a
        NEW run executes) or cancelled, that preview run is superseded but was never
        terminalized, so it lingers in ``ATTENTION_RUN_STATES`` forever and inflates
        the "stale/recovery" alarm. Close it (→ Succeeded) so it leaves the attention
        set; the plan/request are already correct and are NOT touched here.

        - ``plan_id`` set → close that plan's parked preview run immediately (inline
          on confirm/cancel), regardless of the plan's current status.
        - ``plan_id`` None → sweep: close every parked preview run whose plan has
          LEFT ``Blocked`` (plan.status != Blocked, or the plan is gone). A run that
          is legitimately still awaiting confirm has plan.status == Blocked and is
          preserved.
        """
        now = iso_now()
        blocked = LifecycleState.BLOCKED.value
        with self.database.connect() as connection:
            if plan_id is not None:
                rows = connection.execute(
                    "SELECT run_id, plan_id, request_id, state FROM runs "
                    "WHERE plan_id = ? AND state = ?",
                    (plan_id, blocked),
                ).fetchall()
                default_reason = "preview run superseded (data job confirmed/cancelled)"
            else:
                rows = connection.execute(
                    """
                    SELECT r.run_id, r.plan_id, r.request_id, r.state
                    FROM runs r
                    LEFT JOIN plans p ON p.plan_id = r.plan_id
                    WHERE r.state = ?
                      AND (p.status IS NULL OR p.status != ?)
                    """,
                    (blocked, blocked),
                ).fetchall()
                default_reason = (
                    "preview run superseded (plan left blocked); closed by recovery sweep"
                )
            for row in rows:
                run = row_to_dict(row)
                connection.execute(
                    "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                    (LifecycleState.SUCCEEDED.value, now, run["run_id"]),
                )
                self._insert_transition(
                    connection,
                    request_id=run["request_id"],
                    plan_id=run["plan_id"],
                    run_id=run["run_id"],
                    from_state=run["state"],
                    to_state=LifecycleState.SUCCEEDED.value,
                    reason=reason or default_reason,
                    actor=actor,
                    created_at=now,
                )
            return len(rows)


    def close_orphaned_stuck_runs(self, *, actor: str = "recovery-monitor") -> int:
        """Terminalize stuck runs whose OWNING REQUEST has already concluded.

        ``mark_stale_runs`` flags a run StaleClaim/RecoveryNeeded/UnknownAfterSideEffect
        when its worker lease expires mid-execution — a recovery candidate. But if the
        request has meanwhile reached a terminal state (Cancelled/Failed/Succeeded/…) —
        cancelled by the operator, failed via another path, or manually resolved — there
        is nothing left to recover, yet the run lingers in ATTENTION_RUN_STATES forever
        (inflating stale/recovery and the 액티비티 정체·복구 count while never appearing
        in the request-centric action_required). Close such orphan runs to MATCH their
        request's terminal status. The request/plan are untouched; the run record + an
        audit transition are preserved (history kept, only the dangling run is closed).

        A run whose request is still OPEN (non-terminal) is left alone — that is a
        genuine recovery candidate.
        """
        now = iso_now()
        stuck_states = (
            LifecycleState.STALE_CLAIM.value,
            LifecycleState.RECOVERY_NEEDED.value,
            LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value,
        )
        # Terminal request states EXCLUDING any that are themselves an attention run
        # state (BackendApplyFailed is both terminal AND in ATTENTION_RUN_STATES): a
        # BackendApplyFailed request is still actionable in 조치 필요, and mirroring the
        # run to it would leave the run in the attention set — so we don't sweep those.
        # The remaining set (Cancelled/Failed/Succeeded/TimedOut/…) is safely non-attention.
        terminal_states = tuple(
            sorted(set(TERMINAL_LIFECYCLE_STATES) - set(ATTENTION_RUN_STATES))
        )
        run_ph = ",".join(["?"] * len(stuck_states))
        term_ph = ",".join(["?"] * len(terminal_states))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT r.run_id, r.plan_id, r.request_id, r.state,
                       req.status AS request_status
                FROM runs r
                JOIN requests req ON req.request_id = r.request_id
                WHERE r.state IN ({run_ph})
                  AND req.status IN ({term_ph})
                """,
                (*stuck_states, *terminal_states),
            ).fetchall()
            for row in rows:
                run = row_to_dict(row)
                connection.execute(
                    "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                    (run["request_status"], now, run["run_id"]),
                )
                self._insert_transition(
                    connection,
                    request_id=run["request_id"],
                    plan_id=run["plan_id"],
                    run_id=run["run_id"],
                    from_state=run["state"],
                    to_state=run["request_status"],
                    reason=(
                        "stuck run orphaned (request already terminal); "
                        "closed to match request by recovery sweep"
                    ),
                    actor=actor,
                    created_at=now,
                )
            return len(rows)


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


    def list_runs(
        self,
        *,
        states: tuple[str, ...] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        # LEFT JOIN plans/requests so attention/stale runs carry the same
        # operator-meaningful fields as active runs (operation_kind, requester_id,
        # resource_key, request_status) — a raw ``SELECT * FROM runs`` left these
        # null, which is why the 스케줄러 활동 panel couldn't say WHAT each run was.
        select = """
            SELECT
                runs.*,
                plans.status AS plan_status,
                plans.operation_kind,
                plans.resource_key,
                requests.resource_kind,
                requests.requester_id,
                requests.status AS request_status
            FROM runs
            LEFT JOIN plans ON plans.plan_id = runs.plan_id
            LEFT JOIN requests ON requests.request_id = runs.request_id
        """
        with self.database.connect() as connection:
            if states:
                placeholders = ",".join(["?"] * len(states))
                rows = connection.execute(
                    f"""{select}
                    WHERE runs.state IN ({placeholders})
                    ORDER BY runs.updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (*states, limit, offset),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"{select} ORDER BY runs.updated_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        return rows_to_dicts(rows)


    def count_runs(self, *, states: tuple[str, ...] | None = None) -> int:
        """Exact COUNT(*) of runs (optionally restricted to ``states``) using the
        SAME WHERE clause as ``list_runs`` — so attention/stale totals never
        saturate at a list limit. No rows are transferred."""
        with self.database.connect() as connection:
            if states:
                placeholders = ",".join(["?"] * len(states))
                row = connection.execute(
                    f"SELECT COUNT(*) AS n FROM runs WHERE state IN ({placeholders})",
                    tuple(states),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) AS n FROM runs"
                ).fetchone()
        return int(row_to_dict(row)["n"])


    def list_active_runs(
        self,
        *,
        states: tuple[str, ...] | None = None,
        worker_role: str | WorkerRole | None = None,
        worker_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
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
        params.append(offset)
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
                LIMIT ? OFFSET ?
                """,
                tuple(params),
            ).fetchall()
        decoded = rows_to_dicts(rows)
        for run in decoded:
            run["execution_metadata"] = json_loads(run.get("execution_metadata")) or {}
        return decoded


    def count_active_runs(
        self,
        *,
        states: tuple[str, ...] | None = None,
        worker_role: str | WorkerRole | None = None,
        worker_id: str | None = None,
    ) -> int:
        """Exact COUNT(*) of active runs using the SAME WHERE clause as
        ``list_active_runs``. The plans/requests joins in the list variant are 1:1
        via FK, so counting the ``runs`` table alone is equivalent and cheaper."""
        selected_states = states or ACTIVE_RUN_STATES
        where = [f"state IN ({','.join(['?'] * len(selected_states))})"]
        params: list[Any] = list(selected_states)
        if worker_role:
            role = (
                worker_role.value
                if isinstance(worker_role, WorkerRole)
                else worker_role
            )
            where.append("worker_role = ?")
            params.append(role)
        if worker_id:
            where.append("worker_id = ?")
            params.append(worker_id)
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS n FROM runs WHERE {' AND '.join(where)}",
                tuple(params),
            ).fetchone()
        return int(row_to_dict(row)["n"])


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


    def _decode_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        if plan:
            plan["desired_state"] = json_loads(plan["desired_state"]) or {}
            plan["precondition"] = json_loads(plan["precondition"]) or {}
            plan["execution_metadata"] = json_loads(plan["execution_metadata"]) or {}
        return plan
