from __future__ import annotations

import re
import uuid
from ..db import Database, dump_json, like_contains, load_json, utc_now_iso
from ..domain import DataJobState, RequestState, TERMINAL_REQUEST_STATES


def batch_match(param: str) -> str:
    """「batch_id 가 :param 과 같다」의 범위 표기(`batch_id >= :p AND batch_id <= :p` -- 결정적 정렬 규칙에서 둘 다 참 ⇔
    같다). 정렬 키 `batch_id, commit_order` 와 짝으로 쓰면 idx_requests_batch_order 만 그 순서를 낼 수 있어 PG 가 배치 크기·
    테이블 크기와 무관하게 그 인덱스를 LIMIT 만큼만 읽는다 -- `batch_id = :p` 면 PG 가 batch_id 정렬 키를 상수로 지우고,
    큰 배치(테이블의 몇 %)에서 자식이 commit_order·물리 위치 전체에 고르게 퍼졌다고 가정해 UNIQUE(commit_order) 역방향
    스캔이나 Seq Scan + LIMIT 를 골랐다. 실제 자식은 한데 몰려 있어 그 배치보다 새로운(또는 앞에 놓인) 행 전부를 방문했다
    (PG16, 32만 행·자식 2만 배치: 목록 23ms·LIMIT 세기 6ms → 둘 다 0.3ms 미만, 2026-10-11 검증 지적). 쓰는 곳:
    RequestsRepository.list(?batch_id=), BatchesRepository.list_summaries(배치 요약), request_purges.delete_batch(상한 세기)."""
    return f"batch_id >= :{param} AND batch_id <= :{param}"


# 작업 목록 검색(2026-10-25)의 상한·모양 -- 쓰는 곳은 RequestsRepository.list(search_terms=) 와 그 라우트.
# 검색어 토큰 수 상한(절대경로 펼침 뒤): 토큰마다 OR 갈래가 셋(16진이면 다섯)씩 붙고, 드문 검색어는 갈래 전부를 표 전체에 건다.
MAX_SEARCH_TERMS = 8
# 요청·배치 id 접두 매치를 켜는 토큰 모양(소문자로 접은 뒤) -- 16진 4~32자. 접두만 본다: 32자리 16진 id 의 부분 문자열은
# '2024'·'01' 같은 숫자 검색어가 아무 행에나 걸리는 잡음이다(설계 조사 실측). 배치 id 접두는 배치 기록이 지워진 묶음
# (화면 「기록 없음 <id 12자>」 -- 이름이 없다)을 그 12자로 찾는 유일한 길이다.
_HEX_ID_TOKEN = re.compile(r"[0-9a-f]{4,32}")
# resource_key 에서 화면 「대상」 열이 보여 주는 몸통만 -- 'data.<op>:' 머리(len(op)+6자)를 떼고, scan 이면 꼬리
# ':<옵션 지문 64자리 16진>'(65자)도 뗀다(키 모양은 domain.build_resource_key, 계약 테스트가 고정한다). 원문 키에
# 걸면 머리 때문에 경로 단어 'data'·'sync'·'scan' 이 모든 행에, 지문 때문에 숫자 검색어가 scan 행에 아무렇게나 걸린다.
# payload JSON 원문은 키 이름(options·storage·target …)이 다 걸려 더 나쁘고, json 함수는 방언마다 달라 쓰지 않는다
# (metrics 의 양 방언 규칙). SUBSTR 길이를 CASE 로 감싸는 이유: DB 는 신뢰 경계라 키가 짧은 변조 행이 올 수 있는데,
# PG 는 음수 길이 SUBSTR 에서 오류를 던진다(목록 전체가 500) -- 감싼 식은 PG16·sqlite 둘 다 오류도 엉뚱한 값도 없다.
_KEY_BODY = ("CASE WHEN operation = 'scan' AND LENGTH(resource_key) > LENGTH(operation) + 71"
             " THEN SUBSTR(resource_key, LENGTH(operation) + 7, LENGTH(resource_key) - LENGTH(operation) - 71)"
             " ELSE SUBSTR(resource_key, LENGTH(operation) + 7) END")


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

    def list(self, requester_id=None, *, operation=None, state=None, batch_id=None,
             before=None, limit: int = 50, requester_like=None, search_terms=None) -> list[dict]:
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

        부분 일치 검색(2026-10-25 사용자 요청 「일부 단어라도 포함하거나 매치되면 그것도 보여주도록」) -- 둘 다 WHERE 에만
        붙어 정렬·커서는 그대로다(검색 중에도 before 쪽 넘김에 중복·누락이 없다). 둘 다 대소문자를 접은 부분 문자열
        (`LOWER(col) LIKE 접은 패턴 ESCAPE '\\'` -- PG 의 LIKE 는 대소문자를 가리고 sqlite 는 ASCII 를 접어 테스트가 차이를
        못 본다; 와일드카드는 db.like_escape 로 글자 그대로. 패턴은 파이썬이 접고, sqlite 의 LOWER 는 ASCII 만 접어
        비 ASCII 대문자(Ä 등)는 PG 에서만 맞는다 -- 한글은 대소문자가 없어 두 방언이 같다):
          - requester_like: 요청자 id 부분 문자열. **requester_id(정확히 일치)와 별개의 인자다** -- 비관리자 격리는
            requester_id 가 지고, 그 자리에 LIKE 가 끼면 alice 가 alice2 의 행을, j_kim 이 jxkim 의 행을 본다.
          - search_terms: 토큰 목록(소문자로 접어 중복을 빼고 MAX_SEARCH_TERMS 개까지). 토큰 **하나라도** 어느 칸에든
            걸리면 그 행을 낸다(토큰끼리 OR -- 단어를 더하면 넓어진다). 칸 = 요청자 id, resource_key 의 몸통(_KEY_BODY
            -- 화면 「대상」 열 글자: 스토리지 이름·상대경로), 배치 이름(batches.name -- requests 엔 이름 칸이 없다),
            그리고 16진 4~32자 토큰이면 요청 id·배치 id **접두**(_HEX_ID_TOKEN).
        두 인자 모두 None 이면 SQL 이 검색 전과 바이트 단위로 같다(배치 필터의 계획 테스트가 그 모양을 고정한다).

        비용(설계 조사, PG16 32만 행·웜 캐시): '%x%' 는 btree 를 못 타서 commit_order 역방향 스캔에 필터로 걸린다 --
        흔한 검색어는 LIMIT 만큼 찾으면 멈춰 1ms 미만이지만, 드물거나 없는 검색어는 표 전체를 훑는다(약 0.4초). 그동안
        단일 연결 DB 잠금을 쥐어 다른 API 가 기다린다. 그래서 포탈이 입력을 디바운스하고 검색 중엔 폴링을 늦춘다
        (useJobs.useInfiniteRequests). 비관리자는 requester_id 정확 일치가 idx_requests_requester 로 자기 행만 훑어 짧다."""
        where = []
        params: dict = {"n": limit}
        order = "commit_order DESC"
        if requester_id is not None:
            where.append("requester_id = :req"); params["req"] = requester_id
        if operation is not None:
            where.append("operation = :op"); params["op"] = operation
        if state is not None:
            where.append("state = :st"); params["st"] = state
        if batch_id is not None:
            where.append(batch_match("bid")); params["bid"] = batch_id
            order = "batch_id DESC, commit_order DESC"
        if before is not None:
            where.append("commit_order < :before"); params["before"] = before
        if requester_like is not None:
            where.append(r"LOWER(requester_id) LIKE :req_like ESCAPE '\'")
            params["req_like"] = like_contains(requester_like.lower())
        if search_terms is not None:
            terms = list(dict.fromkeys(t.lower() for t in search_terms))[:MAX_SEARCH_TERMS]
            groups = []
            for i, term in enumerate(terms):
                q = f"q{i}"
                params[q] = like_contains(term)
                arms = [rf"LOWER(requester_id) LIKE :{q} ESCAPE '\'",
                        rf"LOWER({_KEY_BODY}) LIKE :{q} ESCAPE '\'",
                        rf"batch_id IN (SELECT batch_id FROM batches WHERE LOWER(name) LIKE :{q} ESCAPE '\')"]
                if _HEX_ID_TOKEN.fullmatch(term):
                    # 16진 글자뿐이라 와일드카드가 끼지 않는다 -- 이스케이프 없이 접두 패턴이다.
                    h = f"h{i}"
                    params[h] = term + "%"
                    arms += [f"request_id LIKE :{h}", f"batch_id LIKE :{h}"]
                groups.append(" OR ".join(arms))
            if groups:
                where.append("(" + " OR ".join(groups) + ")")   # 토큰끼리도 OR -- 하나라도 맞으면
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
