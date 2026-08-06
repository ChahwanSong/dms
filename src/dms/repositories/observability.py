"""진단 이벤트. state_transitions 가 담지 못하는 것 -- **일어나지 않은 전이** -- 만 기록한다.

계약이 하나 있다: record_event 는 절대 예외를 올리지 않는다. 이것은 진단 채널이고,
진단 기록 실패가 상태 전이를 롤백하거나 컨트롤러 루프 틱을 죽이면 본말이 전도된다."""
import logging

from ..db import Database, dump_json, load_json, utc_now_iso

logger = logging.getLogger(__name__)


class ObservabilityRepository:
    def __init__(self, db: Database):
        self._db = db

    def record_event(self, *, component, severity, event_type, message=None,
                     payload=None, request_id=None) -> None:
        # 업무 트랜잭션 밖에서 단독 INSERT 한다 -- 호출자의 트랜잭션에 참여하면
        # 진단 실패가 업무 변경을 되돌린다.
        try:
            self._db.execute(
                """INSERT INTO events (request_id, component, severity, event_type,
                       message, payload, at)
                   VALUES (:r, :c, :s, :t, :m, :p, :at)""",
                {"r": request_id, "c": component, "s": severity, "t": event_type,
                 "m": message, "p": dump_json(payload) if payload is not None else None,
                 "at": utc_now_iso()})
        except Exception as exc:
            logger.warning("record_event failed type=%s: %s", event_type, exc)

    def events_for_request(self, request_id: str, limit: int = 100) -> list[dict]:
        rows = self._db.query(
            """SELECT id, request_id, component, severity, event_type, message,
                      payload, at
               FROM events WHERE request_id = :r ORDER BY id ASC LIMIT :n""",
            {"r": request_id, "n": limit})
        out = []
        for row in rows:
            e = dict(row)
            e["payload"] = load_json(e.get("payload"))
            out.append(e)
        return out

    def prune_events(self, cutoff: str, batch_size: int = 5000) -> int:
        rows = self._db.query(
            "SELECT id FROM events WHERE at < :c ORDER BY id ASC LIMIT :n",
            {"c": cutoff, "n": batch_size})
        if not rows:
            return 0
        ids = {f"i{n}": r["id"] for n, r in enumerate(rows)}
        placeholders = ", ".join(f":{k}" for k in ids)
        self._db.execute(f"DELETE FROM events WHERE id IN ({placeholders})", ids)
        return len(rows)
