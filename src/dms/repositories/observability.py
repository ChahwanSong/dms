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
        # 잘라야 한다면 오래된 쪽을 버린다 -- 장애 진단에 필요한 것은 최신이다.
        # DESC로 최신 limit건을 뽑은 뒤, 화면이 읽기 자연스러운 시간 오름차순으로
        # 되돌린다(reversed) -- 호출자는 여전히 오름차순 리스트를 받는다.
        rows = self._db.query(
            """SELECT id, request_id, component, severity, event_type, message,
                      payload, at
               FROM events WHERE request_id = :r ORDER BY id DESC LIMIT :n""",
            {"r": request_id, "n": limit})
        out = []
        for row in reversed(rows):
            e = dict(row)
            e["payload"] = load_json(e.get("payload"))
            out.append(e)
        return out

    def prune_events(self, cutoff: str, batch_size: int = 5000) -> int:
        # agents.py의 prune_reports와 같은 패턴: 배치가 남는 한 계속 돈다. 틱당
        # 배치 1개만 지우고 리턴하면(예전 구현), 유입량이 batch_size /
        # retention_interval_seconds(기본 5000/3600 ≈ 1.4행/초)를 한 번이라도
        # 넘는 순간(배포 초기 누적분, 다섯 배선 지점 중 하나가 폭주하는 장애 등)
        # purge가 영원히 못 따라잡는다 -- 이 테이블에 증가 제한을 두겠다는
        # 목적 자체가 무너진다. 배치마다 독립 트랜잭션으로 커밋해 한 트랜잭션이
        # 테이블을 오래 잠그지 않게 한다.
        total = 0
        while True:
            with self._db.transaction():
                rows = self._db.query(
                    "SELECT id FROM events WHERE at < :c ORDER BY id ASC LIMIT :n",
                    {"c": cutoff, "n": batch_size})
                if not rows:
                    return total
                placeholders = ", ".join(f":i{k}" for k in range(len(rows)))
                params = {f"i{k}": row["id"] for k, row in enumerate(rows)}
                self._db.execute(
                    f"DELETE FROM events WHERE id IN ({placeholders})", params)
                total += len(rows)
