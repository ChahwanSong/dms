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


class RequestsMixin:
    """Request lifecycle and state-transition persistence."""

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

    def search_requests(
        self,
        *,
        requester_id: str | None = None,
        operation: str | None = None,
        resource_kind: str | None = None,
        status: str | None = None,
        search: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = DEFAULT_REQUEST_LIST_LIMIT,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Flexible request listing for the operator activity view: any subset of
        filters (all optional), newest-first (commit_order DESC), paginated. Unlike
        ``list_requests`` this does NOT require a requester_id, so the portal can show
        ALL request activity classified by operation/resource_kind/status.

        ``search`` is a case-insensitive free-text needle matched (LIKE) against the
        requester, request id, resource key, and the serialized payload — so the
        operator can find requests by requester *or* target (path / storage / dest),
        both of which live in resource_key / payload_summary. Server-side so it spans
        ALL history, not just the loaded page."""
        where: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("requester_id", requester_id),
            ("operation", operation),
            ("resource_kind", resource_kind),
            ("status", status),
        ):
            if value:
                where.append(f"{column} = ?")
                params.append(value)
        needle = (search or "").strip().lower()
        if needle:
            # Escape LIKE metacharacters so a path with `_`/`%` matches literally
            # (SQLite + PostgreSQL both honor `ESCAPE`). Matched columns cover both
            # the requester and every representation of the target.
            esc = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{esc}%"
            cols = ("requester_id", "request_id", "resource_key", "payload_summary")
            ors = " OR ".join(f"LOWER({c}) LIKE ? ESCAPE '\\'" for c in cols)
            where.append(f"({ors})")
            params.extend([like] * len(cols))
        if since is not None:
            where.append("requested_at >= ?")
            params.append(since)
        if until is not None:
            where.append("requested_at < ?")
            params.append(until)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.extend([limit, offset])
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM requests
                {clause}
                ORDER BY commit_order DESC
                LIMIT ? OFFSET ?
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


    def _decode_request(self, request: dict[str, Any]) -> dict[str, Any]:
        if request:
            request["payload_summary"] = json_loads(request["payload_summary"]) or {}
        return request
