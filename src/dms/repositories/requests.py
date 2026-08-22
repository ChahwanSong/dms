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
               payload: dict, priority: str, batch_id=None,
               auth_method="token") -> str:
        # auth_method 기본값이 "token" 인 이유(슬라이스 19, 설계 §2.2-2): 이 값을
        # 빠뜨린 호출자는 특권 승격을 못 얻는다 -- 기본이 "session" 이면 새 생성
        # 지점이 하나 생길 때마다 조용히 uid 0 경로가 열린다. fail-closed 가 기본.
        request_id = uuid.uuid4().hex
        now = utc_now_iso()
        with self._db.transaction():
            row = self._db.query_one("SELECT COALESCE(MAX(commit_order), 0) AS m FROM requests")
            order = row["m"] + 1
            self._db.execute(
                """INSERT INTO requests (request_id, commit_order, operation, requester_id,
                       actor, resource_key, priority, payload, state, created_at, updated_at,
                       batch_id, auth_method)
                   VALUES (:id, :o, :op, :req, :actor, :key, :pri, :payload, :state, :now, :now,
                       :bid, :auth)""",
                {"id": request_id, "o": order, "op": operation, "req": requester_id,
                 "actor": actor, "key": resource_key, "pri": priority,
                 "payload": dump_json(payload), "state": RequestState.PENDING.value,
                 "now": now, "bid": batch_id, "auth": auth_method},
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

    def list(self, requester_id=None, *, operation=None, state=None,
             before=None, limit: int = 50) -> list[dict]:
        """요청 목록(commit_order DESC). 필터·커서(슬라이스 39): operation·state·
        requester_id 는 AND 로 좁히고, before(commit_order)면 그보다 오래된 것만
        -- 무한 스크롤이 마지막 행의 commit_order 를 before 로 넘겨 다음 쪽을
        받는다. commit_order 는 단조 증가라 페이지 경계가 안정적이다(offset 과
        달리 새 행이 끼어도 중복·누락이 없다)."""
        where = []
        params: dict = {"n": limit}
        if requester_id is not None:
            where.append("requester_id = :req"); params["req"] = requester_id
        if operation is not None:
            where.append("operation = :op"); params["op"] = operation
        if state is not None:
            where.append("state = :st"); params["st"] = state
        if before is not None:
            where.append("commit_order < :before"); params["before"] = before
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self._db.query(
            f"SELECT * FROM requests{clause} ORDER BY commit_order DESC LIMIT :n",
            params)
        for row in rows:
            row["payload"] = load_json(row["payload"])
        return rows

    def set_state(self, request_id, to_state: RequestState, *, reason_code=None, actor):
        with self._db.transaction():
            self._apply_state(request_id, to_state, reason_code=reason_code,
                              actor=actor)

    def _apply_state(self, request_id, to_state, *, reason_code, actor):
        """상태 전이의 문장 몸통(현재 상태 읽기 + UPDATE + 전이 이력) --
        트랜잭션은 **호출자가 소유한다**. db.transaction() 은 중첩을 모른다
        (BEGIN 이 무조건 -- db.py): sqlite 는 중첩 BEGIN 에서 즉사하고, PG
        (autocommit)는 경고만 낸 채 안쪽 COMMIT 이 바깥 트랜잭션을 조기 커밋해
        **조용히** 비원자가 된다. 그래서 set_state 를 다른 트랜잭션 안에서
        재사용하려면 경계(누가 BEGIN 하나)와 몸통(무슨 문장인가)을 분리하는
        수밖에 없다(슬라이스 27 -- finalize_from_job 이 두 번째 소유자다)."""
        now = utc_now_iso()
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

    def result(self, request_id) -> dict | None:
        """이 요청의 종단 결과 1행(results). 없으면 None.

        None 을 "사유 없음"으로 단정하면 안 된다 — 두 가지 다른 사실이 겹친다:
        (a) 아직 종단이 아니다(정상), (b) 종단인데 결과 행이 없다(슬라이스 27/30
        이전, 전이와 results 가 별도 커밋이던 시절 사이 크래시가 남긴 영구 결손 —
        finalize_from_job docstring). 그래서 화면 경로는 (b)에 대비해 전이 이력의
        사유로 폴백한다(routes_requests.get_request).
        summary 는 TEXT(JSON) 컬럼이라 get()의 payload 관례대로 파싱해서 준다."""
        row = self._db.query_one("SELECT * FROM results WHERE request_id = :id",
                                 {"id": request_id})
        if row:
            row["summary"] = load_json(row["summary"])
        return row

    def transitions(self, request_id) -> list[dict]:
        return self._db.query(
            """SELECT * FROM state_transitions
               WHERE entity_kind = 'request' AND entity_id = :id ORDER BY id""",
            {"id": request_id})

    def last_reason_code(self, request_id) -> "str | None":
        """그 요청이 종단으로 간 진짜 이유. 배치 항목이 상태값(Cancelled/Rejected/Failed)
        대신 이것을 들고 있어야 「사유」 열이 바로 옆 StatusPill과 중복되지 않고 운영자에게
        '왜'를 알려준다. 사유 없이 종단화된 전이(예: 사유 없는 취소)는 None을 돌려준다 —
        상태값을 사유인 양 채워 넣느니 비우는 편이 낫다.
        `reason_code IS NOT NULL` 필터를 두지 않는다: 그걸 두면 마지막 비-Pending 전이가
        사유 없이 끝났을 때 그보다 오래된(중간 전이의) 사유를 되살려 보여주게 된다 —
        "마지막 전이의 사유"가 아니라 "마지막으로 사유가 붙은 전이"가 되어버린다."""
        row = self._db.query_one(
            """SELECT reason_code FROM state_transitions
               WHERE entity_kind = 'request' AND entity_id = :id
                 AND to_state <> 'Pending'
               ORDER BY id DESC LIMIT 1""",
            {"id": request_id})
        return row["reason_code"] if row else None

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
            # idempotent -- 읽기 후 조기 반환이라 트랜잭션 밖이다. 안에 넣으면
            # 고아 스윕이 매 틱 재호출하는 no-op 마다 빈 BEGIN/COMMIT 이 열린다.
            return
        # 슬라이스 27(BACKLOG §2.1, 슬라이스 24 실증 관찰): 전이와 results INSERT
        # 를 한 트랜잭션으로 묶는다. 별도 커밋이던 시절엔 사이 크래시가 "요청은
        # 종단인데 results 행이 없다"를 만들었다 -- 종단 요청은 고아 스윕의 시야
        # 밖이라 그 결손은 영구였다. 원자화 후엔 둘 다 롤백돼 요청이 비종단으로
        # 남고 다음 틱 finalize 재시도가 완주한다. 덤: "results 는 있는데 요청은
        # 비종단"도 구조적으로 불가능해져, 재시도가 results PK 중복
        # (UniqueViolation -- 슬라이스 24 관측)에 걸릴 창도 함께 닫힌다.
        with self._db.transaction():
            self._apply_state(request_id, target, reason_code=reason_code,
                              actor=actor)
            self.record_result(request_id, target, reason_code=reason_code,
                               summary=summary)

    def set_state_with_result(self, request_id, to_state: RequestState, *,
                              reason_code, actor):
        """planner 의 종단 판정(Rejected/Conflict) 전용 -- 전이와 results INSERT
        를 한 트랜잭션으로 묶는다(슬라이스 30, BACKLOG §2.4 -- finalize_from_job
        과 동일 처방). 별도 커밋이면 사이 크래시가 "종단인데 results 없음"을
        만들고, 종단 요청은 고아 스윕(terminal_jobs_with_live_request) 시야 밖이라
        결손이 영구다. 원자화 후엔 둘 다 롤백 -> 요청이 Pending 으로 남아 다음 틱
        list_pending 재계획이 완주한다. finalize 와 달리 멱등 가드가 없는 이유:
        호출자는 list_pending 이 고른 Pending 요청만 넘기고 스윕류의 종단 후
        재호출 경로가 없다 -- 가드를 흉내 내면 없는 경로를 있는 척하는 것이다."""
        with self._db.transaction():
            self._apply_state(request_id, to_state, reason_code=reason_code,
                              actor=actor)
            self.record_result(request_id, to_state, reason_code=reason_code)

    def has_active_for_requester(self, requester_id) -> bool:
        """이 requester 소유의 비종단(진행 중) 요청이 하나라도 있으면 True. 잡 신원은
        plan 시점에 구워져(설계 §1-6) 삭제가 소급되지 않으므로, 소유자 삭제 전에
        진행 중 요청을 막아 '소유자 없는 잡'을 예방한다. TERMINAL_REQUEST_STATES 밖의
        상태를 NOT IN 으로 센다(find_active 와 같은 placeholder 관례)."""
        terminal = tuple(s.value for s in TERMINAL_REQUEST_STATES)
        placeholders = ", ".join(f":t{i}" for i in range(len(terminal)))
        params = {f"t{i}": v for i, v in enumerate(terminal)}
        params["req"] = requester_id
        row = self._db.query_one(
            f"""SELECT 1 AS x FROM requests
                WHERE requester_id = :req AND state NOT IN ({placeholders})
                LIMIT 1""", params)
        return row is not None
