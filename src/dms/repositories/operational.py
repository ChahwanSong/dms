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


# Request statuses that surface as "action required" (request_attention). Shared by the
# list (list_action_required) and the cheap COUNT(*) (count_action_required_requests) so
# the dashboard count can never drift from the listed items.
# (B) BLOCKED is intentionally NOT here: a Blocked request is a preview awaiting the
# operator's confirm (ConfirmPending) — a normal pending state tracked in the data
# backup/scan tabs, not a global action item. The remaining statuses are genuine
# stuck/failed conditions needing attention.
ACTION_REQUIRED_REQUEST_STATUSES = (
    LifecycleState.STALE_CLAIM.value,
    LifecycleState.RECOVERY_NEEDED.value,
    LifecycleState.VERIFICATION_FAILED.value,
    LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value,
    LifecycleState.BACKEND_APPLY_FAILED.value,
)

# Fallback freshness window for latest-per-node reads when no threshold is supplied
# (callers normally pass settings.agent_report_stale_seconds, whose default is 300).
_DEFAULT_AGENT_STALE_SECONDS = 300


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
        report_json = json_dumps(report)
        capability_json = json_dumps(capability_summary)
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
                    report_json,
                    capability_json,
                    "Fresh",
                    reported_at,
                    now,
                    None,
                    schema_version,
                    "Accepted",
                    None,
                ),
            )
            # Dual-write the denormalized current-state row in the SAME transaction so a
            # node-health read never has to scan agent_reports history. The ON CONFLICT
            # WHERE guard makes a DELAYED / out-of-order report (one whose reported_at is
            # OLDER than the row already there) a no-op, so the current row always holds
            # the newest report for the node. Portable: SQLite and PostgreSQL both accept
            # ON CONFLICT ... DO UPDATE ... WHERE referencing the target row + excluded.
            connection.execute(
                """
                INSERT INTO agent_node_current (
                    cluster_name, node_name, worker_role, node_uid, report_id,
                    report_json, capability_summary, reported_at, received_at,
                    schema_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cluster_name, node_name, worker_role) DO UPDATE SET
                    node_uid = excluded.node_uid,
                    report_id = excluded.report_id,
                    report_json = excluded.report_json,
                    capability_summary = excluded.capability_summary,
                    reported_at = excluded.reported_at,
                    received_at = excluded.received_at,
                    schema_version = excluded.schema_version,
                    updated_at = excluded.updated_at
                WHERE excluded.reported_at >= agent_node_current.reported_at
                """,
                (
                    report["cluster_name"],
                    report["node_name"],
                    report["worker_role"],
                    report["node_uid"],
                    report_id,
                    report_json,
                    capability_json,
                    reported_at,
                    now,
                    schema_version,
                    now,
                ),
            )
        return report_id

    # --- on-demand identity probe targets ------------------------------------
    # DM worker → register at identity-resolve time; agent-report POST response →
    # list (recent window). Agents merge these names into their per-cycle NSS probe
    # set, so a requester outside the static DMS_AGENT_IDENTITY_USERS list still
    # gains node identity evidence without operator list maintenance.

    def register_identity_probe_target(self, username: str) -> None:
        """Upsert (username, now) — refreshes the TTL window on every request."""
        now = iso_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO dm_identity_probe_targets (username, last_requested_at)
                VALUES (?, ?)
                ON CONFLICT (username) DO UPDATE SET
                    last_requested_at = excluded.last_requested_at
                """,
                (username, now),
            )

    def list_identity_probe_targets(
        self, *, ttl_seconds: int, limit: int = 100
    ) -> list[str]:
        """The most-recently-requested usernames within the TTL window, bounded.
        Expired rows are pruned on read so the table stays tiny.

        Ordered by last_requested_at DESC (NOT alphabetically): when more than `limit`
        distinct requesters are active at once — plausible against a large directory —
        an alphabetical cap would starve a just-submitted user whose name sorts late
        (never probed -> no evidence -> job times out). Recency ordering guarantees the
        newest requesters are always in the probed set. username is a stable tie-break."""
        cutoff = (datetime.now(UTC) - timedelta(seconds=max(0, ttl_seconds))).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM dm_identity_probe_targets WHERE last_requested_at < ?",
                (cutoff,),
            )
            rows = connection.execute(
                """
                SELECT username FROM dm_identity_probe_targets
                WHERE last_requested_at >= ?
                ORDER BY last_requested_at DESC, username ASC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
        return [row_to_dict(row)["username"] for row in rows]

    def list_agent_reports(
        self,
        *,
        freshness: str | None = None,
        stale_seconds: int | None = None,
        update_stale: bool = False,
        latest_per_node: bool = False,
        limit: int = 100,
        offset: int = 0,
        cluster_name: str | None = None,
    ) -> list[dict[str, Any]]:
        if latest_per_node:
            # Freshness is computed ON READ from agent_node_current (O(#nodes)), so the
            # whole-table Fresh->Stale UPDATE sweep is neither needed nor run on this
            # path — even when update_stale=True (the route passes it for the history
            # path). This is what keeps node-health cheap at millions of history rows.
            return self._list_latest_agent_reports_per_node(
                freshness=freshness,
                stale_seconds=stale_seconds,
                limit=limit,
                offset=offset,
                cluster_name=cluster_name,
            )
        if stale_seconds is not None and update_stale:
            self.mark_stale_agent_reports(stale_seconds=stale_seconds)
        filters: list[str] = []
        params: list[Any] = []
        if freshness:
            filters.append("freshness_status = ?")
            params.append(freshness)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.database.connect() as connection:
            # Deterministic TOTAL order: reported_at DESC primary, then received_at ASC
            # (ingest order) so same-reported_at ties keep insertion order, then
            # report_id as a final tiebreak. Explicit so the result is stable regardless
            # of which index the planner picks for the sort (idx_agent_reports_reported_at
            # walked in reverse would otherwise flip same-timestamp ties) — downstream
            # candidate selection (inventory / DM worker) depends on this order.
            rows = connection.execute(
                f"""
                SELECT * FROM agent_reports
                {where}
                ORDER BY reported_at DESC, received_at ASC, report_id ASC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
        return [self._decode_agent_report(row_to_dict(row)) for row in rows]


    def _list_latest_agent_reports_per_node(
        self,
        *,
        freshness: str | None,
        stale_seconds: int | None = None,
        limit: int,
        offset: int,
        cluster_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the single most-recent report per (cluster_name, node_name,
        worker_role) for node-health reads, served entirely from agent_node_current.

        agent_node_current holds exactly ONE row per node identity (the latest report,
        kept current by the transactional UPSERT in ``ingest_agent_report``), so this is
        a flat ``SELECT * FROM agent_node_current`` — O(#nodes) — with NO scan of the
        agent_reports history (which grows to millions of rows and is age-pruned). The
        decoded shape is identical to a decoded agent_report so callers (the API route,
        action_required, the portal) are unchanged.

        Freshness is computed ON READ: a node is Stale when ``now - reported_at`` exceeds
        ``stale_seconds`` (falling back to the default window when none is supplied),
        else Fresh. A ``freshness`` filter, if given, is applied to that computed value —
        i.e. "nodes whose CURRENT report has that freshness", exactly what stale-node
        callers want (a newer Fresh report supersedes an older Stale one). Portable plain
        SQL; an optional ``cluster_name`` narrows the scan."""
        threshold = (
            stale_seconds if stale_seconds is not None else _DEFAULT_AGENT_STALE_SECONDS
        )
        params: list[Any] = []
        where = ""
        if cluster_name:
            where = "WHERE cluster_name = ?"
            params.append(cluster_name)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM agent_node_current {where}",
                tuple(params),
            ).fetchall()
        now = utcnow()
        out = [
            self._decode_agent_node_current(
                row_to_dict(row), now=now, stale_seconds=threshold
            )
            for row in rows
        ]
        if freshness:
            out = [r for r in out if r.get("freshness_status") == freshness]
        out.sort(key=lambda r: r.get("reported_at") or "", reverse=True)
        return out[offset : offset + limit]

    @staticmethod
    def _compute_agent_freshness(
        reported_at: str | None,
        received_at: str | None,
        *,
        now: datetime,
        stale_seconds: int,
    ) -> str:
        basis = _parse_iso(reported_at or received_at)
        if (now - basis).total_seconds() > stale_seconds:
            return "Stale"
        return "Fresh"

    def _decode_agent_node_current(
        self, row: dict[str, Any], *, now: datetime, stale_seconds: int
    ) -> dict[str, Any]:
        """Decode an agent_node_current row into the SAME shape a decoded agent_report
        yields (so consumers are agnostic to the source table), with freshness_status
        computed on read."""
        reported_at = row.get("reported_at")
        received_at = row.get("received_at")
        freshness = self._compute_agent_freshness(
            reported_at, received_at, now=now, stale_seconds=stale_seconds
        )
        # stale_at is the deterministic instant the report CROSSED the staleness threshold
        # (reported_at + stale_seconds), not the (arbitrary) read time — so it is stable
        # across reads and semantically "when this node went stale".
        stale_at = (
            (_parse_iso(reported_at or received_at) + timedelta(seconds=stale_seconds))
            .isoformat()
            if freshness == "Stale"
            else None
        )
        return {
            "report_id": row.get("report_id"),
            "cluster_name": row.get("cluster_name"),
            "node_name": row.get("node_name"),
            "node_uid": row.get("node_uid"),
            "worker_role": row.get("worker_role"),
            "report": json_loads(row.get("report_json")) or {},
            "capability_summary": json_loads(row.get("capability_summary")) or {},
            "freshness_status": freshness,
            "reported_at": reported_at,
            "received_at": received_at,
            "stale_at": stale_at,
            "schema_version": row.get("schema_version"),
            "validation_status": "Accepted",
            "validation_error": None,
        }

    def prune_agent_reports(
        self, *, older_than_iso: str, batch_size: int = 5000
    ) -> int:
        """Delete agent_reports rows whose ``reported_at`` predates ``older_than_iso``,
        in batches (each its OWN connection/transaction, committed between batches) so a
        huge backlog never holds one long lock and the path stays portable — SQLite has
        no ``DELETE ... LIMIT`` so we select a bounded id chunk, then delete it by id.

        SAFE because node-health now reads agent_node_current (current state preserved
        independently of history), so even a node that has gone silent keeps showing its
        last report after old history rows are pruned. Retention is pure age-based
        trimming. Returns the total number of rows deleted."""
        if batch_size <= 0:
            batch_size = 5000
        total = 0
        while True:
            # Bulk maintenance: use an UNPOOLED connection (pooled=False) so the
            # per-batch DELETE — which touches several indexes per row — is NOT
            # subject to the pool's statement_timeout and cannot be cancelled
            # mid-batch (which would otherwise stall the prune loop). Each batch is
            # its own connection/transaction, committed on block exit, so locks are
            # held only briefly and a huge backlog drains incrementally.
            with self.database.connect(pooled=False) as connection:
                rows = connection.execute(
                    """
                    SELECT report_id FROM agent_reports
                    WHERE reported_at < ?
                    ORDER BY reported_at
                    LIMIT ?
                    """,
                    (older_than_iso, batch_size),
                ).fetchall()
                ids = [row_to_dict(row)["report_id"] for row in rows]
                if not ids:
                    break
                placeholders = ",".join(["?"] * len(ids))
                connection.execute(
                    f"DELETE FROM agent_reports WHERE report_id IN ({placeholders})",
                    tuple(ids),
                )
            total += len(ids)
            if len(ids) < batch_size:
                break
        return total


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
        placeholders = ",".join(["?"] * len(ACTION_REQUIRED_REQUEST_STATUSES))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM requests
                WHERE status IN ({placeholders})
                ORDER BY commit_order ASC
                LIMIT ?
                """,
                (*ACTION_REQUIRED_REQUEST_STATUSES, limit),
            ).fetchall()
        return [self._decode_request(row_to_dict(row)) for row in rows]

    def count_action_required_requests(self) -> int:
        """Exact COUNT(*) of requests in the action-required statuses, using the SAME
        status set as ``list_action_required`` so the work-summary count never drifts
        from the listed items. No rows are decoded."""
        placeholders = ",".join(["?"] * len(ACTION_REQUIRED_REQUEST_STATUSES))
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS n FROM requests WHERE status IN ({placeholders})",
                ACTION_REQUIRED_REQUEST_STATUSES,
            ).fetchone()
        return int(row_to_dict(row)["n"])

    # --- action-required acknowledge (server-side, record-preserving) ---------
    # An ack marks an action-required item handled by fingerprint; action_required()
    # filters acked items out (across ALL clients). The underlying request/data_job
    # row is untouched — only the alarm is suppressed.

    def add_action_acks(
        self, items: list[dict[str, Any]], *, acked_by: str = "operator"
    ) -> int:
        rows = [i for i in items if i.get("fingerprint")]
        if not rows:
            return 0
        now = iso_now()
        with self.database.connect() as connection:
            for i in rows:
                connection.execute(
                    """
                    INSERT INTO action_acks (fingerprint, issue_type, reason, acked_by, acked_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (fingerprint) DO UPDATE SET
                        issue_type = excluded.issue_type,
                        reason = excluded.reason,
                        acked_by = excluded.acked_by,
                        acked_at = excluded.acked_at
                    """,
                    (i["fingerprint"], i.get("issue_type"), i.get("reason"), acked_by, now),
                )
        return len(rows)

    def remove_action_acks(self, fingerprints: list[str]) -> int:
        fps = [f for f in fingerprints if f]
        if not fps:
            return 0
        placeholders = ",".join(["?"] * len(fps))
        with self.database.connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM action_acks WHERE fingerprint IN ({placeholders})",
                tuple(fps),
            )
            return cursor.rowcount if cursor.rowcount is not None else len(fps)

    def action_ack_fingerprints(self) -> set[str]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT fingerprint FROM action_acks").fetchall()
        return {row_to_dict(row)["fingerprint"] for row in rows}

    def list_action_acks(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM action_acks ORDER BY acked_at DESC"
            ).fetchall()
        return [row_to_dict(row) for row in rows]


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
