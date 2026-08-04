from __future__ import annotations

import uuid
from ..db import Database, dump_json, load_json, utc_now_iso
from ..domain import DataJobState, RequestState, TERMINAL_REQUEST_STATES


class RequestsRepository:
    _JOB_TO_REQUEST = {
        DataJobState.SUCCEEDED: RequestState.SUCCEEDED,
        DataJobState.FAILED: RequestState.FAILED,
        DataJobState.TIMED_OUT: RequestState.FAILED,
        DataJobState.CANCELLED: RequestState.CANCELLED,
        DataJobState.REJECTED: RequestState.REJECTED,
        DataJobState.PREVIEW_EXPIRED: RequestState.REJECTED,
    }

    def __init__(self, db: Database):
        self._db = db

    def create(self, *, operation, requester_id, actor, resource_key,
               payload: dict, priority: str, batch_id=None) -> str:
        request_id = uuid.uuid4().hex
        now = utc_now_iso()
        with self._db.transaction():
            row = self._db.query_one("SELECT COALESCE(MAX(commit_order), 0) AS m FROM requests")
            order = row["m"] + 1
            self._db.execute(
                """INSERT INTO requests (request_id, commit_order, operation, requester_id,
                       actor, resource_key, priority, payload, state, created_at, updated_at,
                       batch_id)
                   VALUES (:id, :o, :op, :req, :actor, :key, :pri, :payload, :state, :now, :now,
                       :bid)""",
                {"id": request_id, "o": order, "op": operation, "req": requester_id,
                 "actor": actor, "key": resource_key, "pri": priority,
                 "payload": dump_json(payload), "state": RequestState.PENDING.value,
                 "now": now, "bid": batch_id},
            )
            self._record_transition(request_id, None, RequestState.PENDING, None, actor, now)
        return request_id

    def _record_transition(self, request_id, from_state, to_state, reason_code, actor, at):
        self._db.execute(
            """INSERT INTO state_transitions (entity_kind, entity_id, from_state,
                   to_state, reason_code, actor, at)
               VALUES ('request', :id, :f, :t, :r, :actor, :at)""",
            {"id": request_id,
             "f": from_state.value if from_state is not None else None,
             "t": to_state.value, "r": reason_code, "actor": actor, "at": at},
        )

    def get(self, request_id) -> dict | None:
        row = self._db.query_one("SELECT * FROM requests WHERE request_id = :id",
                                 {"id": request_id})
        if row:
            row["payload"] = load_json(row["payload"])
        return row

    def list(self, requester_id=None, limit: int = 50) -> list[dict]:
        if requester_id is None:
            rows = self._db.query(
                "SELECT * FROM requests ORDER BY commit_order DESC LIMIT :n", {"n": limit})
        else:
            rows = self._db.query(
                """SELECT * FROM requests WHERE requester_id = :req
                   ORDER BY commit_order DESC LIMIT :n""",
                {"req": requester_id, "n": limit})
        for row in rows:
            row["payload"] = load_json(row["payload"])
        return rows

    def set_state(self, request_id, to_state: RequestState, *, reason_code=None, actor):
        now = utc_now_iso()
        with self._db.transaction():
            current = self._db.query_one(
                "SELECT state FROM requests WHERE request_id = :id", {"id": request_id})
            if current is None:
                raise KeyError(request_id)
            self._db.execute(
                "UPDATE requests SET state = :s, updated_at = :now WHERE request_id = :id",
                {"s": to_state.value, "now": now, "id": request_id})
            self._record_transition(request_id, RequestState(current["state"]),
                                    to_state, reason_code, actor, now)

    def list_pending(self, limit: int = 50) -> list[dict]:
        rows = self._db.query(
            """SELECT request_id FROM requests WHERE state = :s
               ORDER BY commit_order LIMIT :n""",
            {"s": RequestState.PENDING.value, "n": limit})
        return rows

    def find_active(self, resource_key) -> dict | None:
        terminal = tuple(s.value for s in TERMINAL_REQUEST_STATES)
        placeholders = ", ".join(f":t{i}" for i in range(len(terminal)))
        params = {f"t{i}": v for i, v in enumerate(terminal)}
        params["key"] = resource_key
        return self._db.query_one(
            f"""SELECT * FROM requests WHERE resource_key = :key
                AND state NOT IN ({placeholders})
                ORDER BY commit_order LIMIT 1""", params)

    def active_referencing_storage(self, storage_name) -> bool:
        terminal = tuple(s.value for s in TERMINAL_REQUEST_STATES)
        placeholders = ", ".join(f":t{i}" for i in range(len(terminal)))
        params = {f"t{i}": v for i, v in enumerate(terminal)}
        rows = self._db.query(
            f"SELECT payload FROM requests WHERE state NOT IN ({placeholders})", params)
        for r in rows:
            p = load_json(r["payload"])
            if storage_name in (p.get("storage"), p.get("source_storage"),
                                p.get("destination_storage")):
                return True
        return False

    def record_result(self, request_id, terminal_state, *, reason_code=None,
                      message=None, summary=None):
        self._db.execute(
            """INSERT INTO results (request_id, terminal_state, reason_code, message,
                   summary, completed_at)
               VALUES (:id, :s, :r, :m, :sum, :now)""",
            {"id": request_id, "s": RequestState(terminal_state).value, "r": reason_code,
             "m": message, "sum": dump_json(summary) if summary is not None else None,
             "now": utc_now_iso()})

    def transitions(self, request_id) -> list[dict]:
        return self._db.query(
            """SELECT * FROM state_transitions
               WHERE entity_kind = 'request' AND entity_id = :id ORDER BY id""",
            {"id": request_id})

    def finalize_from_job(self, request_id, job_state, *, reason_code=None,
                          summary=None, actor):
        target = self._JOB_TO_REQUEST.get(DataJobState(job_state))
        if target is None:
            raise ValueError(f"non-terminal job state: {job_state}")
        current = self._db.query_one(
            "SELECT state FROM requests WHERE request_id = :id", {"id": request_id})
        if current is None:
            raise KeyError(request_id)
        if RequestState(current["state"]) in TERMINAL_REQUEST_STATES:
            return  # idempotent
        self.set_state(request_id, target, reason_code=reason_code, actor=actor)
        self.record_result(request_id, target, reason_code=reason_code, summary=summary)
