"""진단 이벤트. state_transitions 가 담지 못하는 것 -- **일어나지 않은 전이** -- 만 기록한다.

계약이 하나 있다: record_event 는 절대 예외를 올리지 않는다. 이것은 진단 채널이고,
진단 기록 실패가 상태 전이를 롤백하거나 컨트롤러 루프 틱을 죽이면 본말이 전도된다.
예외는 record_event_strict 하나다 -- 이벤트가 곧 카운터인 곳(stepper 의 LDAP 재확인 보류 횟수) 전용이다."""
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
            self._insert(component=component, severity=severity, event_type=event_type,
                         message=message, payload=payload, request_id=request_id)
        except Exception as exc:
            logger.warning("record_event failed type=%s: %s", event_type, exc)

    def record_event_strict(self, *, component, severity, event_type, message=None,
                            payload=None, request_id=None) -> None:
        """record_event 와 같은 단독 INSERT 지만 실패하면 예외를 올린다. **카운터로 쓰는 이벤트 전용**(현재 stepper 의
        identity_recheck_deferred 하나) -- 모듈 계약('record_event 는 절대 예외를 올리지 않는다')은 진단 채널 얘기고,
        카운터 기록이 조용히 사라지면 D2 의 '재시도 3번만'이 '무한 재시도'로 바뀐다. 호출자가 fail-closed 로 처리한다."""
        self._insert(component=component, severity=severity, event_type=event_type,
                     message=message, payload=payload, request_id=request_id)

    def _insert(self, *, component, severity, event_type, message, payload, request_id):
        self._db.execute(
            """INSERT INTO events (request_id, component, severity, event_type,
                   message, payload, at)
               VALUES (:r, :c, :s, :t, :m, :p, :at)""",
            {"r": request_id, "c": component, "s": severity, "t": event_type,
             "m": message, "p": dump_json(payload) if payload is not None else None,
             "at": utc_now_iso()})

    def events_of_types(self, request_id, event_types, *, limit: int = 50) -> list[dict]:
        """request_id + event_type IN (...) 의 최신 limit 건을 시간 오름차순으로(payload 디코드). idx_events_request
        (request_id, id)로 좁힌다. events_for_request(최근 100건 전부)를 쓰지 않는 이유: 같은 요청에 다른 이벤트가
        많이 쌓이면 D2 카운터(stepper._recheck_history)가 창 밖으로 밀려나 재시도 횟수가 0 으로 돌아간다 -- '3번만'이
        무한 재시도가 된다. 빈 event_types 는 빈 목록(IN () 은 SQL 문법 오류)."""
        types = list(event_types)
        if not types:
            return []
        placeholders = ", ".join(f":t{i}" for i in range(len(types)))
        params = {f"t{i}": t for i, t in enumerate(types)}
        params.update({"r": request_id, "n": limit})
        rows = self._db.query(
            f"""SELECT id, request_id, component, severity, event_type, message,
                       payload, at
                FROM events WHERE request_id = :r AND event_type IN ({placeholders})
                ORDER BY id DESC LIMIT :n""", params)
        out = []
        for row in reversed(rows):
            e = dict(row)
            e["payload"] = load_json(e.get("payload"))
            out.append(e)
        return out

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
        #
        # PG 는 지울 행을 먼저 `FOR UPDATE SKIP LOCKED` 로 잠근다(2026-10-11 검증 지적): 요청 삭제(특히 배치 단위
        # 삭제 -- 자식마다 events 를 request_id 순으로 지운다)가 같은 오래된 이벤트를 다른 순서로 잠그고 있으면, 잠금
        # 없이 DELETE 하던 예전엔 두 트랜잭션이 서로의 행을 기다려 PG 가 한쪽을 deadlock 으로 끊었다(삭제가 지면 배치
        # 삭제 전체가 batch_delete_failed, 지는 동안 API RLock 이 deadlock_timeout 만큼 멈춤). 이제 prune 은 남이 쥔
        # 행을 건너뛰고(다음 틱에 지운다 -- 삭제가 이기면 어차피 사라진다) 자기가 잠근 행만 지우므로 **아무것도 기다리지
        # 않는다** -- 대기 그래프에 들어가지 않으니 교착의 한쪽이 될 수 없다. sqlite 는 단일 커넥션 RLock 이 이미
        # 직렬화한다(FOR UPDATE 문법 없음).
        lock = " FOR UPDATE SKIP LOCKED" if self._db.dialect == "postgresql" else ""
        total = 0
        while True:
            with self._db.transaction():
                rows = self._db.query(
                    f"SELECT id FROM events WHERE at < :c ORDER BY id ASC LIMIT :n{lock}",
                    {"c": cutoff, "n": batch_size})
                if not rows:
                    return total
                placeholders = ", ".join(f":i{k}" for k in range(len(rows)))
                params = {f"i{k}": row["id"] for k, row in enumerate(rows)}
                self._db.execute(
                    f"DELETE FROM events WHERE id IN ({placeholders})", params)
                total += len(rows)
