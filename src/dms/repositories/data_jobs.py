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


class DataJobsMixin:
    """Data-management job persistence."""


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
        if row is None:
            raise RecordNotFound(f"data job not found: {job_id}")
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


    def delete_data_job(self, job_id: str, *, actor: str = "system") -> bool:
        """Delete a single ``data_jobs`` row; return ``True`` if a row was removed.

        Only the ``data_jobs`` row is deleted. ``data_jobs.request_id`` is an *outgoing*
        FK to ``requests`` and no table references ``data_jobs``, so removing the child
        row never violates referential integrity -- the parent request/plan/runs/results
        audit history is intentionally preserved. The terminal-state guard lives at the
        API boundary; this method itself is unconditional. The delete and its audit
        control-mutation share one connection so they commit atomically (mirrors
        ``delete_storage_mapping``).
        """
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM data_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not existing:
                return False
            before = self._decode_data_job(row_to_dict(existing))
            connection.execute(
                "DELETE FROM data_jobs WHERE job_id = ?",
                (job_id,),
            )
            self._insert_control_mutation(
                connection,
                actor=actor,
                mutation_kind="data_job.delete",
                payload={"job_id": job_id},
                mutation_class="data_job",
                operation="delete",
                target_key=job_id,
                status="Succeeded",
                result_summary={
                    "job_id": job_id,
                    "state": before.get("state"),
                    "operation": before.get("operation"),
                },
                before_state=before,
                after_state=None,
                created_at=iso_now(),
            )
        return True


    def data_job_summary(
        self,
        *,
        storage_name: str | None = None,
        operation: str | None = None,
    ) -> dict[str, Any]:
        filters: list[str] = []
        params: list[Any] = []
        if storage_name:
            filters.append("storage_name = ?")
            params.append(storage_name)
        if operation:
            filters.append("operation = ?")
            params.append(operation)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.database.connect() as connection:
            state_rows = connection.execute(
                f"SELECT state, COUNT(*) AS n FROM data_jobs {where} GROUP BY state",
                tuple(params),
            ).fetchall()
            op_rows = connection.execute(
                f"SELECT operation, COUNT(*) AS n FROM data_jobs {where} GROUP BY operation",
                tuple(params),
            ).fetchall()
        by_state = {row_to_dict(r)["state"]: row_to_dict(r)["n"] for r in state_rows}
        by_operation = {
            row_to_dict(r)["operation"]: row_to_dict(r)["n"] for r in op_rows
        }
        active_total = sum(
            n for state, n in by_state.items()
            if state not in TERMINAL_DATA_JOB_STATES
        )
        return {
            "total": sum(by_state.values()),
            "active_total": active_total,
            "by_state": by_state,
            "by_operation": by_operation,
        }


    def expire_stale_preview_jobs(self, *, actor: str = "recovery-monitor") -> int:
        """Terminalize ConfirmPending data jobs whose preview has EXPIRED.

        A sync/rm preview parks the job in ConfirmPending until the requester confirms.
        DMS only checks preview expiry when a confirm is *attempted* (dm.confirm_data_job)
        — so a preview that is never confirmed lingers ConfirmPending forever, and its run
        stays Blocked ("확인 대기") even though confirming would now be rejected. This
        proactive sweep applies the SAME terminalization the confirm path does: mark the
        job PreviewExpired and complete the request as Rejected. That moves the plan out
        of Blocked, so the existing preview-run reaper then closes the dangling run.

        Only jobs with a non-null preview_expires_at in the past are swept (a job without
        an expiry set is left alone). Returns how many were expired.
        """
        now = datetime.now(UTC)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT job_id, request_id, preview_expires_at
                FROM data_jobs
                WHERE state = ? AND preview_expires_at IS NOT NULL
                """,
                (DataJobState.CONFIRM_PENDING.value,),
            ).fetchall()
        expired = []
        for row in rows:
            job = row_to_dict(row)
            parsed = _parse_iso(job.get("preview_expires_at"))
            if parsed is not None and parsed <= now:
                expired.append(job)
        for job in expired:
            plan = self.get_plan_by_request(job["request_id"])
            if not plan:
                continue
            self.update_data_job(job["job_id"], state=DataJobState.PREVIEW_EXPIRED)
            self.complete_result(
                request_id=job["request_id"],
                plan_id=plan["plan_id"],
                run_id=None,
                terminal_status=LifecycleState.REJECTED,
                message="data job preview expired before confirm (recovery sweep)",
                verification_summary={
                    "backend_side_effect": False,
                    "reason": "data_job_preview_expired",
                },
                error_category="data_management_confirm",
                actor=actor,
            )
        return len(expired)


    def list_data_jobs(
        self,
        limit: int = 100,
        *,
        requester_id: str | None = None,
        operation: str | None = None,
        storage_name: str | None = None,
        state: str | None = None,
        operations: tuple[str, ...] | None = None,
        states: tuple[str, ...] | None = None,
        updated_since: str | None = None,
        offset: int = 0,
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
        # Set filters (operations/states) scope the action-required scan to matching
        # rows at the SQL level (backed by idx_data_jobs_operation_state) so the listed
        # items align with count_data_jobs — instead of scanning the newest N jobs of any
        # kind and filtering in Python (which silently drops matches once the table grows
        # past the window).
        if operations:
            placeholders = ",".join(["?"] * len(operations))
            filters.append(f"data_jobs.operation IN ({placeholders})")
            params.extend(operations)
        if states:
            placeholders = ",".join(["?"] * len(states))
            filters.append(f"data_jobs.state IN ({placeholders})")
            params.extend(states)
        # Recency window for the action-required alarm: surface only jobs updated within
        # the window. The row is NOT deleted (operational history is preserved); older
        # terminal jobs simply stop alarming. Keeps the alarm bounded without touching
        # the data_jobs table.
        if updated_since:
            filters.append("data_jobs.updated_at >= ?")
            params.append(updated_since)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.append(limit)
        params.append(offset)
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
                LIMIT ? OFFSET ?
                """,
                tuple(params),
            ).fetchall()
        return [self._decode_data_job(row_to_dict(row)) for row in rows]


    def count_data_jobs(
        self,
        *,
        states: tuple[str, ...] | None = None,
        operations: tuple[str, ...] | None = None,
        updated_since: str | None = None,
    ) -> int:
        """Exact COUNT(*) of data jobs, optionally restricted to state/operation sets.
        Uses the SAME predicate as the action-required data-job scan (and the same
        idx_data_jobs_operation_state index), so the work-summary count matches the
        listed items without decoding thousands of job rows per dashboard poll.

        Mirrors ``list_data_jobs``' ``FROM data_jobs JOIN requests`` exactly: the listed
        scan INNER JOINs requests (so a data_job whose request row is missing is silently
        dropped from the list), and this count applies the identical join so it can never
        drift ABOVE the listed items even if an orphan data_job ever existed."""
        filters: list[str] = []
        params: list[Any] = []
        if states:
            placeholders = ",".join(["?"] * len(states))
            filters.append(f"data_jobs.state IN ({placeholders})")
            params.extend(states)
        if operations:
            placeholders = ",".join(["?"] * len(operations))
            filters.append(f"data_jobs.operation IN ({placeholders})")
            params.extend(operations)
        if updated_since:
            filters.append("data_jobs.updated_at >= ?")
            params.append(updated_since)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.database.connect() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS n
                FROM data_jobs
                JOIN requests ON requests.request_id = data_jobs.request_id
                {where}
                """,
                tuple(params),
            ).fetchone()
        return int(row_to_dict(row)["n"])

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
