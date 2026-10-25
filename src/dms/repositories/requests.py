from __future__ import annotations

import uuid
from ..db import Database, dump_json, load_json, utc_now_iso
from ..domain import DataJobState, RequestState, TERMINAL_REQUEST_STATES
from .text_search import BATCH_ID_PREFIX_RE, contains_pattern, prefix_pattern


def batch_match(param: str) -> str:
    """「batch_id 가 :param 과 같다」의 범위 표기(`batch_id >= :p AND batch_id <= :p` -- 결정적 정렬 규칙에서 둘 다 참 ⇔
    같다). 정렬 키 `batch_id, commit_order` 와 짝으로 쓰면 idx_requests_batch_order 만 그 순서를 낼 수 있어 PG 가 배치 크기·
    테이블 크기와 무관하게 그 인덱스를 LIMIT 만큼만 읽는다 -- `batch_id = :p` 면 PG 가 batch_id 정렬 키를 상수로 지우고,
    큰 배치(테이블의 몇 %)에서 자식이 commit_order·물리 위치 전체에 고르게 퍼졌다고 가정해 UNIQUE(commit_order) 역방향
    스캔이나 Seq Scan + LIMIT 를 골랐다. 실제 자식은 한데 몰려 있어 그 배치보다 새로운(또는 앞에 놓인) 행 전부를 방문했다
    (PG16, 32만 행·자식 2만 배치: 목록 23ms·LIMIT 세기 6ms → 둘 다 0.3ms 미만, 2026-10-11 검증 지적). 쓰는 곳:
    RequestsRepository.list(?batch_id=), BatchesRepository.list_summaries(배치 요약), request_purges.delete_batch(상한 세기)."""
    return f"batch_id >= :{param} AND batch_id <= :{param}"


# 화면 「대상」 열(pathSummary: scan·rm = 「스토리지:경로」, sync = 「출발:경로 → 도착:경로」)과 같은 문자열을 resource_key 에서
# 꺼내는 SQL 식(2026-10-25 작업 목록 경로 검색). resource_key = `data.<op>:<스토리지:경로…>` 이고 scan 은 끝에 `:<옵션 지문
# sha256 hex 64자>` 가 붙는다(domain.build_resource_key). 원문에 그대로 LIKE 를 걸면 접두 때문에 「data」·「sync」·「scan」이
# 그 연산 전체에, 지문 때문에 숫자·hex 짧은 검색어가 무관한 scan 행에 걸린다(32만 행 실측: '%data%' 전부, hex 4자 99건 --
# 이 식으로는 0건). payload(JSON 텍스트)는 키 이름·옵션 값까지 걸려 더 나쁘다('%100%' 가 28만 행). sqlite·PG 공용 함수만
# 쓰고(substr·length 는 양쪽 모두 문자 단위 -- 한글 안전), 모양이 다른 키(구형·테스트 픽스처)는 원문 그대로 본다.
# sync 는 「출발:경로:도착:경로」라 화살표가 없다 -- 검색어의 「→」는 구분자로 지운다(text_search.search_tokens).
_TARGET_TEXT_SQL = (
    "CASE WHEN substr(resource_key, 1, length(operation) + 6) <> 'data.' || operation || ':' THEN resource_key"
    " WHEN operation = 'scan' AND length(resource_key) > length(operation) + 71"
    " AND substr(resource_key, length(resource_key) - 64, 1) = ':'"
    " THEN substr(resource_key, length(operation) + 7, length(resource_key) - length(operation) - 71)"
    " ELSE substr(resource_key, length(operation) + 7) END")


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
        with self._db.transaction():
            return self.insert_pending(
                operation=operation, requester_id=requester_id, actor=actor,
                resource_key=resource_key, payload=payload, priority=priority,
                batch_id=batch_id, auth_method=auth_method)

    def insert_pending(self, *, operation, requester_id, actor, resource_key,
                       payload: dict, priority: str, batch_id=None,
                       auth_method="token") -> str:
        """create 의 본문 -- **호출자 트랜잭션 안에서만** 부른다(Database.transaction 은 중첩이 없다). 배치
        orchestrator 가 자식 요청 INSERT 와 항목 claim 을 한 트랜잭션으로 묶으려고 나눴다(claim 실패 = 롤백)."""
        request_id = uuid.uuid4().hex
        now = utc_now_iso()
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

    def list(self, requester_id=None, *, requester_like=None, batch_like=None, path_like=None,
             operation=None, state=None, batch_id=None,
             before=None, limit: int = 50) -> list[dict]:
        """요청 목록(commit_order DESC). 필터·커서(슬라이스 39): operation·state·
        requester_id·batch_id(2026-10-11 -- 한 배치의 작업만) 는
        AND 로 좁히고, before(commit_order)면 그보다 오래된 것만
        -- 무한 스크롤이 마지막 행의 commit_order 를 before 로 넘겨 다음 쪽을
        받는다. commit_order 는 **현존 행 기준** 단조 증가라(MAX+1 -- 요청 삭제
        (2026-10-08)로 최신 행이 지워지면 다음 제출이 그 번호를 다시 받는다, UNIQUE
        위반 없음) 페이지 경계가 안정적이다(offset 과 달리 새 행이 끼어도 중복·누락이
        없다).

        batch_id 필터의 모양(2026-10-11 검증 지적): `batch_id = :bid ORDER BY commit_order DESC` 로 쓰면 PG 가 큰 배치
        (테이블의 몇 %)에서 UNIQUE(commit_order) 역방향 스캔 + batch_id 필터를 고른다 -- 오래된 배치면 그보다 새로운 요청
        전부(32만 행 중 25만)를 방문했다. 그래서 batch_match(범위 표기) + `ORDER BY batch_id DESC, commit_order DESC` 로
        쓴다(이유는 batch_match docstring): idx_requests_batch_order 를 LIMIT 만큼만 거꾸로 읽는다(PG16 실측: 자식 2만 배치
        23ms → 0.02ms, 일반 계획(prepare)도 같다). sqlite 도 같은 인덱스로 정렬 없이 읽는다.

        검색(2026-10-25, 「전체 작업」 검색칸): requester_like·batch_like·path_like 는 text_search.search_tokens 의 토큰
        목록이다(빈 목록·None = 조건 없음). 토큰마다 대소문자를 무시한 **부분** 일치이고, 토큰끼리·칸끼리 모두 AND 다 --
        행을 좁히기만 한다. requester_like = 요청자 아이디. path_like = 화면 「대상」 표기(_TARGET_TEXT_SQL -- 스토리지 이름·
        sync 출발·도착 양쪽 포함). batch_like = 배치 이름(batches.name, 조회 시점의 서브쿼리 -- 이름은 PATCH 로 바뀐다)
        또는 6자 이상 hex 토큰이면 batch_id 앞부분(화면이 이름 없는 배치·기록 없는 묶음을 id 앞 12자로 보인다). 배치 행이
        없는 단건은 batch_like 가 있으면 빠진다.

        **격리는 requester_id 의 정확 일치가 집행한다**(비관리자 = 라우트가 자기 자신으로 강제). 부분 일치를 이 kwarg 로
        넣지 마라 -- 강제된 값까지 LIKE 를 타면 'al' 사용자가 'alice'·'sal' 의 작업을 본다. requester_like·batch_like 는
        관리자 검색 전용이다(라우트가 비관리자에겐 넘기지 않는다 -- batch_like 는 응답에 없는 배치 이름을 캐 보는 오라클이
        된다). path_like 는 자기 행 안에서만 좁히므로 누구에게나 열려 있다.

        비용: 앞에 와일드카드가 붙는 LIKE 는 인덱스를 못 탄다. 맞는 행이 흔하면 commit_order 역방향 스캔이 LIMIT 에서
        멈추지만(PG16 32만 행 0.6ms), 드물거나 없으면 범위 전체를 한 번 훑는다(같은 규모 0.25~0.34s -- 단일 커넥션
        RLock 을 그동안 잡는다). 배치 이름 조건의 모양(COALESCE)은 아래 반복문 주석. 그래서 화면은 검색어가 있는 동안
        폴링을 늦춘다(useJobs SEARCH_REFETCH_MS). 정확
        requester_id(비관리자)·batch_id 와 함께 걸리면 그 인덱스 범위로 상한이 준다. 규모가 커져 느려지면
        pg_trgm 인덱스가 후속(BACKLOG)."""
        where = []
        params: dict = {"n": limit}
        order = "commit_order DESC"
        if requester_id is not None:
            where.append("requester_id = :req"); params["req"] = requester_id
        # 패턴도 컬럼과 **같은** SQL lower() 로 낮춘다(text_search 모듈 docstring -- 파이썬 lower 와 DB lower 는 다르다).
        for i, tok in enumerate(requester_like or ()):
            where.append(rf"lower(requester_id) LIKE lower(:rq{i}) ESCAPE '\'"); params[f"rq{i}"] = contains_pattern(tok)
        for i, tok in enumerate(path_like or ()):
            where.append(rf"lower({_TARGET_TEXT_SQL}) LIKE lower(:pq{i}) ESCAPE '\'")
            params[f"pq{i}"] = contains_pattern(tok)
        for i, tok in enumerate(batch_like or ()):
            # COALESCE 로 감싼다(2026-10-25 리뷰, PG16 32만 행 실측): 맨 IN 이면 PG 가 semi-join 으로 끌어올리고
            # lower(name) LIKE 를 1행으로 잘못 추정해 Materialize 중첩 루프(훑은 행 × 일치한 배치 수)를 골랐다 -- 이름이
            # 겹치는 배치 100~300개에서 0.94~2.75s 동안 단일 커넥션 RLock 을 잡았다. COALESCE 안의 IN 은 끌어올려지지
            # 않아 hashed SubPlan(행마다 해시 조회, 50ms 이하)이 된다. 행마다 batches 를 묻는 스칼라 서브쿼리는 batches 가
            # 작으면 행마다 Seq Scan 을 골라 더 느리다(1.85s) -- 쓰지 마라. batch_id NULL(단건)은 IN 이 NULL → FALSE 로
            # 빠진다(전과 같다).
            cond = (rf"COALESCE(batch_id IN (SELECT batch_id FROM batches"
                    rf" WHERE lower(name) LIKE lower(:bq{i}) ESCAPE '\'), FALSE)")
            params[f"bq{i}"] = contains_pattern(tok)
            low = tok.lower()   # 배치 id 는 소문자 hex(uuid4().hex) -- 접두는 파이썬에서 낮춰도 같다(ASCII)
            if BATCH_ID_PREFIX_RE.fullmatch(low):
                cond = rf"({cond} OR batch_id LIKE :bp{i} ESCAPE '\')"
                params[f"bp{i}"] = prefix_pattern(low)
            where.append(cond)
        if operation is not None:
            where.append("operation = :op"); params["op"] = operation
        if state is not None:
            where.append("state = :st"); params["st"] = state
        if batch_id is not None:
            where.append(batch_match("bid")); params["bid"] = batch_id
            order = "batch_id DESC, commit_order DESC"
        if before is not None:
            where.append("commit_order < :before"); params["before"] = before
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self._db.query(
            f"SELECT * FROM requests{clause} ORDER BY {order} LIMIT :n",
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
        수밖에 없다(슬라이스 27 -- finalize_from_job 이 두 번째 소유자다).

        요청 삭제(2026-10-08)와의 경합: 읽기를 PG 행 잠금(FOR UPDATE)으로 하고 UPDATE 영향 행 수를 확인한다 -- 잠금
        없이 읽은 뒤 삭제 트랜잭션이 커밋되면 UPDATE 는 0행인데 전이·results INSERT 가 커밋돼 지운 요청의 고아 행이
        영구히 남았다(results 는 PK 충돌로도 막히지 않는다 -- 삭제가 옛 결과 행을 이미 지웠다, 2026-10-09 검증 지적).
        0행이면 KeyError -- 호출자 트랜잭션 전체(전이·results)가 롤백된다(행 없음과 같은 신호)."""
        now = utc_now_iso()
        lock = " FOR UPDATE" if self._db.dialect == "postgresql" else ""
        current = self._db.query_one(
            f"SELECT state FROM requests WHERE request_id = :id{lock}", {"id": request_id})
        if current is None:
            raise KeyError(request_id)
        moved = self._db.execute_count(
            "UPDATE requests SET state = :s, updated_at = :now WHERE request_id = :id",
            {"s": to_state.value, "now": now, "id": request_id})
        if moved != 1:
            raise KeyError(request_id)
        self._record_transition(request_id, RequestState(current["state"]),
                                to_state, reason_code, actor, now)

    def mark_planned_if_pending(self, request_id, *, actor) -> bool:
        """조건부 Pending→Planned(planner 멱등 분기 전용, 2026-10-08). 영향 0 = 그 사이 취소·삭제됐다 -- 되살리지
        않고 False. set_state 는 종단 가드가 없어(_apply_state) 취소된 요청을 Planned 로 덮는다. 정상 emit 은
        DataJobsRepository.create_plan_and_job 이 같은 CAS 를 plan·job INSERT 와 한 트랜잭션으로 한다."""
        now = utc_now_iso()
        with self._db.transaction():
            moved = self._db.execute_count(
                """UPDATE requests SET state = :planned, updated_at = :now
                   WHERE request_id = :id AND state = :pending""",
                {"planned": RequestState.PLANNED.value, "pending": RequestState.PENDING.value,
                 "now": now, "id": request_id})
            if moved != 1:
                return False
            self._record_transition(request_id, RequestState.PENDING, RequestState.PLANNED,
                                    None, actor, now)
        return True

    def cancel_if_pending(self, request_id, *, reason_code, actor) -> bool:
        """잡이 아직 없는 요청의 취소(2026-10-12): **아직 Pending 일 때만** Cancelled + 전이 + results 를 한 트랜잭션으로.
        planner 의 잡 생성(DataJobsRepository.create_plan_and_job -- 같은 행의 Pending→Planned CAS)과 직렬화된다: 영향 0 =
        planner 가 먼저 잡을 만들었거나(Planned) 이미 끝났다 -- 아무것도 쓰지 않고 False(호출자가 다시 읽어 잡 경로로 간다).
        mark_planned_if_pending 의 거울이다."""
        now = utc_now_iso()
        with self._db.transaction():
            moved = self._db.execute_count(
                """UPDATE requests SET state = :cancelled, updated_at = :now
                   WHERE request_id = :id AND state = :pending""",
                {"cancelled": RequestState.CANCELLED.value, "pending": RequestState.PENDING.value,
                 "now": now, "id": request_id})
            if moved != 1:
                return False
            self._record_transition(request_id, RequestState.PENDING, RequestState.CANCELLED,
                                    reason_code, actor, now)
            self.record_result(request_id, RequestState.CANCELLED, reason_code=reason_code)
        return True

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
            # 잠근 행으로 다시 본다(2026-10-16 검증 지적): 위 읽기는 잠금 없이라, 두 호출자(배치 취소 라우트와 컨트롤러 그물,
            # stepper 와 취소)가 함께 통과하면 둘째가 전이·results 를 한 번 더 써 results PK 충돌(UniqueViolation)로 실패했다.
            lock = " FOR UPDATE" if self._db.dialect == "postgresql" else ""
            locked = self._db.query_one(
                f"SELECT state FROM requests WHERE request_id = :id{lock}", {"id": request_id})
            if locked is None:
                raise KeyError(request_id)
            if RequestState(locked["state"]) in TERMINAL_REQUEST_STATES:
                return
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
