from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any
from uuid import uuid4

from ..db import Database
from ..domain import (
    DataJobState,
    DataManagementPolicyInput,
    LifecycleState,
    ResourceKind,
    StorageMappingInput,
    TERMINAL_DATA_JOB_STATES,
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


class RecordNotFound(KeyError):
    """A record addressed by id does not exist.

    Subclasses KeyError so existing ``except KeyError`` handlers keep working, but
    gives the API a type it can map to 404 without swallowing genuine KeyErrors from
    a bug in the handler itself."""


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




def _parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, UTC)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
    }


def _storage_names_in_payload(payload: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    if payload.get("storage_name"):
        names.add(str(payload["storage_name"]))
    for key in ("target", "source", "destination"):
        value = payload.get(key)
        if isinstance(value, dict) and value.get("storage_name"):
            names.add(str(value["storage_name"]))
    for key in ("source_storage_name", "destination_storage_name"):
        if payload.get(key):
            names.add(str(payload[key]))
    return names
