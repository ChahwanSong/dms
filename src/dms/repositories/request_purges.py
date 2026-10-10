"""요청 삭제(2026-10-08): DB 즉시 삭제 트랜잭션 + 정리 아웃박스(request_purges).

- 왜 한 트랜잭션인가(설계 D1): 종단 요청 삭제는 API 가 원 행을 **한 커밋으로** 지운다. 그래서 목록·상세·잡·로그·
  아티팩트 열람·사용량·지표의 모든 읽기 경로가 그 즉시 일관된다 -- 읽기 경로마다 「삭제 중」 필터를 둘 필요가 없다
  (그런 경로가 열 곳이 넘고, 하나만 빠져도 화면이 거짓을 보인다).
- DB 밖 잔재(k8s Pod·vcjob, 아티팩트 `<base>/<job_id>`)를 찾는 열쇠(job_id·phase_refs·삭제 시점 base)는 원 행과 함께
  사라지지 않게 **같은 트랜잭션에서** request_purges 행으로 옮긴다. 컨트롤러 request-purge 루프가 그 행을 보고 멱등하게
  정리한 뒤 finish 로 지운다(행 = 정리 대기). API 는 FS·k8s 를 전혀 만지지 않는다(설계 D2 -- 비 root 잡의 phase 디렉터리는
  cap 없는 제어면이 지울 수 없고, api 에는 pods RBAC 가 없다).
- 감사(설계 D10): 요청마다 audit_log('request', 'delete', request_id) 1행. 지우는 행들이 가진 감사 가치(누가 언제
  컨펌·취소했나, root 실행 여부·실행 신원)를 상한 있는 스냅숏(_snapshot)으로 영구 테이블에 남긴다 -- events 는 30일
  prune 대상이고 삭제 대상과 같은 키라 감사 장소가 될 수 없다.
- 이벤트는 쓰지 않는다: 이벤트는 업무 트랜잭션 밖 단독 INSERT 가 규약이고(observability.py), 성공 기록은 감사 행이다.
  정리 단계의 이벤트(request_id=NULL)는 컨트롤러 루프가 남긴다.

**배치 단위 삭제**(delete_batch, 2026-10-10): 배치 자식은 개별로는 지우지 않는다(delete_terminal 의
batch_child_not_deletable). 대신 배치 하나 = 자식 요청 **전부**(`requests.batch_id = :b` -- 항목의 request_id 가 아니다:
재실행은 항목의 request_id 를 NULL 로 되돌리므로 옛 자식은 batch_id 로만 찾는다) + batch_items + batches 행을 **한
트랜잭션**으로 지운다 -- 전부 아니면 전무. 자식을 하나씩 커밋하면 그 사이 :rescan·재실행·항목 추가가 배치를 되살려
"일부만 지워진 살아 있는 배치" 가 남는다. 배치 행이 없는 자식 묶음(배치 기록만 지운 BatchesRepository.delete 의 잔재,
dangling)도 같은 메서드로 묶음째 지운다. 잠금 순서는 batch_items → batches → requests(request_id 순) → data_jobs --
오케스트레이터 _record_terminal·reject_queued_item(batch_items → batches)과 planner·요청 삭제(requests → data_jobs)를 둘 다
지키는 방향이다. 역순으로 함께 잠그는 경로를 새로 만들면 교착이다. 감사는 자식마다 기존 ('request','delete') 행(스냅숏에
batch_id) + 배치마다 ('batch','delete') 행 1개(_batch_bounded -- 배치 이름·옵션·실행 신원·항목은 다른 어디에도 남지 않는다).

**삭제 목록이 곧 일관성의 전부다** -- 저장소 전체에 FK 가 0건이라, request_id·job_id 를 담는 테이블을 새로 만들고 여기
(PURGED_TABLES·_purge_locked·finish)에 넣지 않으면 그 행은 조용히 고아가 된다. tests/test_repo_request_purges.py 가
스키마의 request_id/job_id/entity_id 컬럼 전수를 열거해 PURGED_TABLES ∪ PURGE_EXEMPT_TABLES 와 대조한다.
"""
from ..db import Database, dump_json, iso_plus, load_json, utc_now_iso
from ..domain import TERMINAL_DATA_JOB_STATES, TERMINAL_REQUEST_STATES
from .requests import batch_match

# 요청을 지울 때 그 요청·잡·plan 을 가리키는 행을 지우는 테이블(_purge_locked -- delete_terminal·delete_batch 공용 몸통 --
# 이 전부 DELETE 한다).
PURGED_TABLES = ("requests", "results", "plans", "data_jobs", "state_transitions", "events",
                 "scan_report_digests")
# 요청·잡 id 를 담지만 지우지 않는 테이블 -- 명시 허용 목록:
#   batch_items    단건 삭제(delete_terminal)는 항목이 가리키는 요청을 거부한다(batch_child_not_deletable). 배치 단위
#                  삭제(delete_batch)는 batch_id 로 배치째(자식 요청과 함께) 지운다 -- request_id 로 지우는 행이 아니다.
#   audit_log      감사는 보존한다(target_key 문자열). 삭제 기록 자신도 여기 남는다.
#   request_purges 이 저장소의 아웃박스 -- 정리가 끝나면 finish 가 지운다.
PURGE_EXEMPT_TABLES = ("batch_items", "audit_log", "request_purges")

# 상태 문자열 집합 비교 -- DB 가 신뢰 경계라 열거형 변환(ValueError)을 거치지 않는다. 집합 밖 = 비종단(fail-closed).
_TERMINAL_REQ = frozenset(s.value for s in TERMINAL_REQUEST_STATES)
_TERMINAL_JOB = frozenset(s.value for s in TERMINAL_DATA_JOB_STATES)
# 지울 수 있는 배치 상태 = routes_batches._TERMINAL_BATCH 의 거울(테스트가 동일성을 고정한다). 집합 밖(Previewing·
# PreviewReady·Running·모르는 값) = 거부.
_TERMINAL_BATCH = ("Completed", "Cancelled")

# 배치 단위 삭제(delete_batch) 한 트랜잭션의 크기 상한. API 는 프로세스 하나·커넥션 하나라 트랜잭션 동안 DB RLock 이
# /readyz 를 포함한 모든 요청을 멈춘다(db.py) -- 배치 하나 = 트랜잭션 하나의 크기를 묶어야 readiness(10초 주기, 1초
# 타임아웃)를 넘기지 않는다.
#   자식: 1000개(잡 각 1개) ≈ 문장 6000개 ≈ 0.3-1초(로컬 PG 실측 ~0.33초, 테스트베드 PG 왕복 0.06-0.2ms -- 문장 수 상한은
#         test_batch_delete_statement_budget 이 고정한다).
#   항목: 자식이 적어도 항목은 많을 수 있다(큰 CSV 배치를 일찍 취소 -- validate_batch 엔 항목 수 상한이 없다). 항목은
#         전부 잠그고(1단계) 감사 스냅숏 단계마다 직렬화하고 전부 DELETE 한다 -- 자식 500 기준 항목 1만 ≈ 0.2초, 5만 ≈
#         0.5초(PG 실측, 2026-10-11 검증 지적). 그래서 항목도 묶는다.
# 넘는 배치는 batch_delete_too_large(청크 삭제는 BACKLOG). 목록 API 의 배치 요약(BatchesRepository.list_summaries)도 자식
# 수를 이 상한 + 1 에서 멈춘다. 지운 뒤의 파일·파드 정리는 컨트롤러가 비동기로 한다 -- 자식 1000개면 30분 안팎
# (deploy/README 「배치 단위 삭제」).
MAX_BATCH_DELETE_CHILDREN = 1000
MAX_BATCH_DELETE_ITEMS = 10000

STAGES = ("k8s", "files", "purging")
BACKOFF_CAP_SECONDS = 900
SNAPSHOT_MAX_BYTES = 32 * 1024
_TRANSITIONS_MAX = 30
_MESSAGE_MAX = 500
_RAW_MAX = 1000
# IN 목록 한 번의 자리표시자 수 상한(scan_digests._CHUNK 와 같은 값).
_CHUNK = 500

# 다행 조회는 diag_logs(행당 최대 64KB)를 싣지 않는다(data_jobs._ROW_COLUMNS_SANS_DIAG 와 같은 이유) -- 스냅숏에도
# 넣지 않으므로 읽을 이유가 없다.
_JOB_COLUMNS = ("job_id, request_id, tool, operation, state, reason_code, storage_name, source_storage, "
                "destination_storage, source, destination, target, worker_pool, files_count, bytes_count, "
                "artifact_uri, phase_refs, created_at, updated_at")
_ROW_COLUMNS = ("request_id, jobs, artifact_base, stage, outcomes, attempts, last_error, next_attempt_at, "
                "requested_by, requested_at, updated_at")
# 배치 감사 스냅숏의 배치 행 키(_batch_bounded). 변조로 거대해진 값을 접을 때 남기는 골격 키.
_BATCH_KEYS = ("batch_id", "name", "note", "operation", "status", "requester_id", "actor", "owner_username",
               "auth_method", "options", "max_concurrency", "priority", "node_count", "procs_per_node",
               "item_count", "succeeded_count", "failed_count", "preview_round", "created_at", "updated_at")
_BATCH_SKELETON_KEYS = ("batch_id", "name", "operation", "status", "requester_id", "actor", "owner_username",
                        "auth_method", "created_at")
_ID_MAX = 64


class _Lost(Exception):
    """마지막 CAS DELETE(또는 배치 삭제의 배치 행 CAS·유령 자식 재확인)가 졌다 -- 트랜잭션을 롤백시키려고 던진다(밖으로
    새지 않는다)."""


def _skip(*, reason_code) -> dict:
    # reason_code= 키워드 리터럴로만 부른다 -- AST 추출기(tests/test_reason_codes_coverage.py)가 그 자리만 본다.
    return {"deleted": False, "reason": reason_code}


def _batch_skip(*, reason_code, request_id=None) -> dict:
    """delete_batch 의 거부 -- request_id 는 문제가 된 자식(없으면 None). 키는 늘 있다(화면이 「배치 X: …(작업 abc…)」
    로 붙인다). 단건 _skip 과 나눈 이유: 단건 반환 모양({"deleted","reason"})은 기존 계약이라 키를 늘리지 않는다."""
    return {"deleted": False, "reason": reason_code, "request_id": request_id}


def _clip(value):
    # 감사 골격용: 변조로 거대해진 문자열 값을 원문 앞부분으로 접는다(증거 보존 + 상한).
    return value[:_RAW_MAX] if isinstance(value, str) else value


def _parse(text):
    """DB 의 JSON 텍스트 -- 깨졌으면 None(신뢰 경계: 변조된 한 행이 삭제 트랜잭션 전체를 500 으로 만들지 않게).
    RecursionError: 깊게 중첩된 변조 JSON(`[[[[…`)은 디코더가 재귀 한도에서 던진다 -- 깨진 것과 같다."""
    try:
        return load_json(text)
    except (ValueError, TypeError, RecursionError):
        return None


def _parse_or_raw(text):
    """감사용: 깨진 JSON 은 버리지 않고 원문 앞부분을 남긴다(증거 보존). 정상이면 그대로."""
    try:
        return load_json(text)
    except (ValueError, TypeError, RecursionError):
        return {"unparsed": str(text)[:_RAW_MAX]}


def _lean_item(item) -> dict:
    """배치 감사의 줄인 항목 모양(_batch_bounded 2~4단계): payload 를 빼고, 값이 null 인 request_id·reason_code 키도 뺀다
    -- 대기·취소 항목이 {"seq","status"} 만 남아 32 KiB 안에 몇 배 더 들어간다. 키가 없음 = null(줄였다는 사실은
    truncated 가 말한다). seq·status 는 늘 둔다."""
    out = {"seq": item["seq"], "status": item["status"]}
    for k in ("request_id", "reason_code"):
        if item[k] is not None:
            out[k] = item[k]
    return out


def _fits(snapshot) -> "str | None":
    text = dump_json(snapshot)
    return text if len(text.encode("utf-8")) <= SNAPSHOT_MAX_BYTES else None


class RequestPurgesRepository:
    def __init__(self, db: Database):
        self._db = db

    def _lock(self) -> str:
        # claim_steppable·set_phase_ref 관례: PG 만 행 잠금(sqlite 는 단일 커넥션 RLock 이 이미 직렬화한다).
        return " FOR UPDATE" if self._db.dialect == "postgresql" else ""

    def _in(self, sql_template: str, ids, extra: "dict | None" = None) -> int:
        """IN 목록 문장을 _CHUNK 묶음으로 실행하고 영향 행 수 합을 돌려준다. 빈 목록은 no-op(IN () 은 문법 오류)."""
        ids = list(ids)
        total = 0
        for i in range(0, len(ids), _CHUNK):
            names = {f"i{n}": v for n, v in enumerate(ids[i:i + _CHUNK])}
            params = {**(extra or {}), **names}
            total += self._db.execute_count(
                sql_template.format(", ".join(":" + k for k in names)), params)
        return total

    def _query_in(self, sql_template: str, ids, extra: "dict | None" = None) -> list[dict]:
        """_in 의 조회판 -- 묶음마다 SELECT 하고 행을 이어 붙인다(묶음 안의 순서만 SQL 이 정한다). 빈 목록은 []."""
        ids = list(ids)
        rows: list[dict] = []
        for i in range(0, len(ids), _CHUNK):
            names = {f"i{n}": v for n, v in enumerate(ids[i:i + _CHUNK])}
            rows.extend(self._db.query(
                sql_template.format(", ".join(":" + k for k in names)), {**(extra or {}), **names}))
        return rows

    # ---- 삭제(API) ----

    def delete_terminal(self, request_id, *, actor, artifact_base, quiet_seconds, now=None) -> dict:
        """종단 요청 1건을 한 트랜잭션으로 지운다. 반환 {"deleted": True, "job_ids": [...]} 또는
        {"deleted": False, "reason": <사유 코드>}(판정 순서 = 화면 문구 순서, 첫 해당).

        게이트(전부 트랜잭션 안에서 잠근 행으로 판정):
          request_not_found          행 없음(동시에 다른 관리자가 먼저 지운 경우 포함)
          batch_child_not_deletable  batch_id 가 NULL 이 아니거나("" 포함 -- truthy 검사 금지) batch_items 가 가리킴.
                                     배치 자식을 지우면 오케스트레이터가 영구 정체하고 배치 취소가 깨진다(설계 §6.4)
          request_not_deletable      요청 비종단, 또는 마지막 CAS DELETE 패배
          request_job_active         요청은 종단인데 비종단 잡이 있다(취소↔planner 경합 잔재·고아 화해 창). 잡이 아직
                                     돌거나 곧 제출될 수 있어 지우면 실행이 고아가 된다
          request_recently_finished  요청·잡의 마지막 갱신이 조용한 창(quiet_seconds) 안 -- 오래된 stepper·planner
                                     스냅숏과 종료 중 파드에 대한 심층 방어(구조적 보강은 set_phase_ref·
                                     create_plan_and_job 이 한다)

        트랜잭션을 이 메서드가 소유한다(Database.transaction 은 중첩되지 않는다) -- 안에서 다른 저장소의 트랜잭션 소유
        메서드를 부르지 않고 원시 SQL 만 쓴다. 게이트 뒤의 삭제·아웃박스·감사 몸통은 트랜잭션을 열지 않는 집합 코어
        _purge_locked 로 뽑아 배치 단위 삭제(delete_batch)와 같이 쓴다 -- 삭제 문장을 두 벌로 두면 PURGED_TABLES 와
        어긋난다. 잠금 순서는 requests → data_jobs: 다른 경로는 두 테이블을 한 트랜잭션에서 함께 잠그지 않거나(stepper
        set_job_state·set_phase_ref 는 data_jobs 만, finalize_from_job 은 requests 만) 같은 방향이라(planner
        create_plan_and_job) 교착이 없다.

        artifact_base 는 삭제 시점 strip_scheme(resolve_artifact_base) -- None 은 「모름」(정리 루프가 파일 단계를
        보류한다). 빈 문자열을 넘기지 말 것(라우트가 None 으로 접는다)."""
        now = now or utc_now_iso()
        lock = self._lock()
        try:
            with self._db.transaction():
                req = self._db.query_one(
                    f"SELECT * FROM requests WHERE request_id = :r{lock}", {"r": request_id})
                if req is None:
                    return _skip(reason_code="request_not_found")
                if req["batch_id"] is not None:
                    return _skip(reason_code="batch_child_not_deletable")
                if self._db.query_one(
                        "SELECT 1 AS x FROM batch_items WHERE request_id = :r LIMIT 1", {"r": request_id}):
                    # batch_id 는 NULL 인데 항목이 가리킨다 = 비정상 DB -- 지우지 않는다(fail-closed).
                    return _skip(reason_code="batch_child_not_deletable")
                if req["state"] not in _TERMINAL_REQ:
                    return _skip(reason_code="request_not_deletable")
                jobs = self._db.query(
                    f"SELECT {_JOB_COLUMNS} FROM data_jobs WHERE request_id = :r "
                    f"ORDER BY created_at, job_id{lock}", {"r": request_id})
                if any(j["state"] not in _TERMINAL_JOB for j in jobs):
                    return _skip(reason_code="request_job_active")
                if self._recently_touched(req, jobs, now=now, quiet_seconds=quiet_seconds):
                    return _skip(reason_code="request_recently_finished")
                job_ids = self._purge_locked([req], {request_id: jobs}, actor=actor,
                                             artifact_base=artifact_base, now=now, batch_id=None)
        except _Lost:
            # 경합 패배 -- 조용한 성공으로 보고하지 않는다(그 사이 상태가 바뀌었거나 다른 관리자가 지웠다).
            return _skip(reason_code="request_not_deletable")
        return {"deleted": True, "job_ids": job_ids}

    def _purge_locked(self, reqs, jobs_by_rid, *, actor, artifact_base, now, batch_id) -> list[str]:
        """reqs(이미 잠그고 게이트를 통과한 요청 행들, request_id 순)와 그 잡들을 지운다. 반환 = 지운 job_id 전부(reqs
        순서, 요청 안에선 jobs_by_rid 의 순서). batch_id None = 단건 경로(CAS 에 batch_id IS NULL), 문자열 = 배치 경로
        (CAS 에 batch_id = :bid -- 그 배치의 자식만).

        **트랜잭션을 열지 않는다** -- delete_terminal·delete_batch 가 소유한다(Database.transaction 은 중첩되지 않는다).
        순서: 감사 스냅숏(지우기 전에 읽는다) → 집합 DELETE(_in 묶음) → 자식마다 events(감사의 events_deleted 를 정확히
        남기려고 자식마다) → 마지막 CAS DELETE(합이 len(reqs) 가 아니면 _Lost -- 잠금이 없는 sqlite 경로와 잠금 누락에
        대한 이중 가드, 진 쪽은 전부 롤백) → 자식마다 정리 아웃박스 행 + 감사 행."""
        rids = [r["request_id"] for r in reqs]
        plan_ids = [r["plan_id"] for r in self._query_in(
            "SELECT plan_id FROM plans WHERE request_id IN ({})", rids)]
        job_ids = [j["job_id"] for rid in rids for j in jobs_by_rid.get(rid, [])]
        snapshots = {r["request_id"]: self._snapshot(r, jobs_by_rid.get(r["request_id"], [])) for r in reqs}
        for kind, ids in (("data_job", job_ids), ("plan", plan_ids), ("request", rids)):
            self._in("DELETE FROM state_transitions WHERE entity_kind = :k AND entity_id IN ({})",
                     ids, {"k": kind})
        self._in("DELETE FROM scan_report_digests WHERE job_id IN ({})", job_ids)
        self._in("DELETE FROM data_jobs WHERE request_id IN ({})", rids)
        self._in("DELETE FROM plans WHERE request_id IN ({})", rids)
        self._in("DELETE FROM results WHERE request_id IN ({})", rids)
        for rid in rids:
            snapshots[rid]["events_deleted"] = self._db.execute_count(
                "DELETE FROM events WHERE request_id = :r", {"r": rid})
        states = sorted(_TERMINAL_REQ)
        params = {f"s{i}": s for i, s in enumerate(states)}
        if batch_id is None:
            owner = "batch_id IS NULL"
        else:
            owner = "batch_id = :bid"
            params["bid"] = batch_id
        removed = self._in(
            "DELETE FROM requests WHERE request_id IN ({}) AND " + owner + " AND state IN ("
            + ", ".join(f":s{i}" for i in range(len(states))) + ")", rids, params)
        if removed != len(reqs):
            raise _Lost()
        # 이미 있는 아웃박스 행(DB 복원으로 되살아난 요청을 다시 지움 -- _enqueue)은 **묶음마다 한 번** 읽는다. 자식마다
        # 한 번씩 읽으면 PG 가 그 문장을 자동 준비(prepare)한 뒤 대개 비어 있는 테이블의 통계로 고른 일반 계획(Seq Scan)을
        # 재사용해, 같은 트랜잭션이 방금 넣은 행과 아직 정리되지 않은 이전 삭제의 행 전부를 자식마다 훑었다 -- 1000자식
        # 배치 연속 10건에서 아웃박스 조회만 57ms → 448ms 로 늘어 RLock 을 쥐는 시간을 키웠다(2026-10-10 검증 지적).
        old_rows = {r["request_id"]: r for r in self._query_in(
            "SELECT request_id, jobs FROM request_purges WHERE request_id IN ({})" + self._lock(), rids)}
        for req in reqs:
            rid = req["request_id"]
            jobs = jobs_by_rid.get(rid, [])
            self._enqueue(rid, [self._outbox_job(j) for j in jobs], old=old_rows.get(rid),
                          artifact_base=artifact_base, actor=actor, now=now)
            self._db.execute(
                """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                       before_state, after_state, at)
                   VALUES ('request', 'delete', :r, :actor, :b, NULL, :at)""",
                {"r": rid, "actor": actor, "b": self._bounded(snapshots[rid], req, jobs), "at": now})
        return job_ids

    def delete_batch(self, batch_id, *, expected_request_count, actor, artifact_base, quiet_seconds,
                     max_children, max_items=MAX_BATCH_DELETE_ITEMS, now=None) -> dict:
        """배치 하나(또는 배치 행이 없는 자식 묶음 -- dangling)를 한 트랜잭션으로 지운다(모듈 docstring 「배치 단위
        삭제」). 반환 {"deleted": True, "request_ids": [...], "job_ids": [...], "dangling": bool} 또는
        {"deleted": False, "reason": <사유>, "request_id": <문제 자식 id | None>}.

        판정(전부 트랜잭션 안에서 잠근 행으로, 첫 해당이 사유 -- 순서가 곧 화면 문구 순서):
          batch_not_deletable        배치 행이 있는데 상태가 Completed/Cancelled 밖(Previewing·PreviewReady·Running·
                                     모르는 값)
          batch_not_found            배치 행도 자식도 남은 항목도 없다
          batch_delete_too_large     자식 > max_children 또는 항목 > max_items -- 트랜잭션 동안 API 단일 커넥션 RLock 이
                                     API 전체를 멈추므로 한 트랜잭션의 크기를 묶는다(모듈 상수 MAX_BATCH_DELETE_*)
          batch_changed              자식 수 != expected_request_count(확인 창이 본 수 -- 자식은 늘어나기만 하므로 수가 곧
                                     버전이다), 또는 마지막 CAS·유령 자식 재확인 패배
          batch_child_shared         자식을 **다른** 배치의 항목이 가리킨다(비정상 DB -- fail-closed). 단건 경로의
                                     batch_child_not_deletable(「배치 단위로 선택해 삭제하세요」)을 쓰지 않는다 -- 이미
                                     배치 단위로 고른 사람에게 같은 말을 돌려주게 된다(2026-10-11 검증 지적). 처방은 그
                                     항목을 가진 다른 배치를 먼저 지우는 것이다
          request_not_deletable      비종단 자식(배치는 종단인데 자식이 살아 있는 cancel 경합 잔재 등)
          request_job_active         종단 자식의 비종단 잡
          request_recently_finished  자식·잡의 갱신이 조용한 창 안. batches.updated_at 에는 걸지 않는다 -- 이름·메모만
                                     고쳐도 바뀌어 「방금 이름 바꾼 배치는 삭제 불가」가 된다
        자식 수준 판정(마지막 셋)은 범주마다 자식 전체를 먼저 보고 다음 범주로 넘어간다. 문제 자식 id 를 함께 돌려준다.

        **배치 행도 자식도 없이 항목만 남은 묶음**(옛 add_item·replace_items 경합의 잔재, 롤링 업데이트 겹침 동안 옛 API
        파드의 add_item -- 2026-10-11 검증 지적)도 지운다: 예전엔 batch_not_found 라 그 항목을 지울 길이 없었고, 그 항목이
        단건 요청을 가리키면 그 요청의 단건 삭제까지 batch_child_not_deletable 로 영영 막혔다. 자식이 0 이므로
        expected_request_count 는 0 이어야 하고, 감사 행(dangling, child_count 0)이 지운 항목을 남긴다.

        **상한 판정은 상한 + 1 행까지만 읽고 한다**(2026-10-11 검증 지적): 예전엔 항목·자식을 전부 잠그고 읽은 뒤에야
        batch_delete_too_large 를 냈다 -- 자식 2만 배치를 거절하는 데만 2만 행을 읽고 잠가(PG 74ms) 그동안 API 전체(RLock)와
        그 자식들을 만지는 planner·finalize 가 기다렸다. 그래서 항목은 `ORDER BY seq LIMIT max_items + 1` 로 잠그고(PK
        (batch_id, seq) 순서 그대로), 자식은 전부 잠그기 **전에** 인덱스만 읽는 `LIMIT max_children + 1` 세기로 거른다.
        상한 안의 배치는 LIMIT 에 닿지 않으니 전부 잠긴다(의미 그대로). 세기와 잠금 사이에 자식이 늘어 상한을 넘는 경합은
        잠금 조회의 같은 LIMIT 와 그 뒤의 len 검사가 다시 잡는다. 판정 순서(배치 상태 → 없음 → 상한 → …)는 그대로다 --
        상한을 넘으면 자식·항목이 0 이 아니라 batch_not_found 일 수 없다.

        잠금 순서 batch_items → batches → requests → data_jobs(모듈 docstring). 항목을 먼저 쥐므로 그 사이 오케스트레이터
        _materialize 의 claim UPDATE 는 기다렸다가 0행(지운 항목) → _ClaimLost 로 자식 INSERT 까지 롤백된다 -- 지운 배치의
        자식은 다시 생기지 않는다. 마지막의 유령 재확인(SELECT … WHERE batch_id = :b)은 PG READ COMMITTED 의 FOR UPDATE 가
        동시에 INSERT 된 행을 보지 못하는 틈을 문장마다 새 스냅숏으로 막는다. 종단 배치에 Queued 항목이 남은 것은 정상
        모양이라(항목 수정·CSV 교체는 종단 배치를 되살리지 않는다) 항목 상태 게이트는 두지 않는다 -- 그런 항목엔 자식이
        없다."""
        now = now or utc_now_iso()
        lock = self._lock()
        try:
            with self._db.transaction():
                # 상한 + 1 까지만 -- 넘으면 아래에서 batch_delete_too_large(위 docstring 「상한 판정」).
                items = self._db.query(
                    "SELECT seq, status, request_id, reason_code, payload, created_at, updated_at "
                    f"FROM batch_items WHERE batch_id = :b ORDER BY seq LIMIT :ni{lock}",
                    {"b": batch_id, "ni": int(max_items) + 1})
                batch = self._db.query_one(f"SELECT * FROM batches WHERE batch_id = :b{lock}", {"b": batch_id})
                if batch is not None and batch["status"] not in _TERMINAL_BATCH:
                    return _batch_skip(reason_code="batch_not_deletable")
                # 자식 수를 상한 + 1 까지만 센다(인덱스만, 잠그지 않는다) -- 넘는 배치의 자식 전부를 읽고 잠그지 않게.
                # 모양은 batch_match + 인덱스 순서(requests.batch_match docstring) -- `batch_id = :b LIMIT` 면 PG 가 큰 배치에서
                # Seq Scan + LIMIT 를 골라 그 배치의 물리 위치 앞의 행 전부를 읽는다.
                head = self._db.query_one(
                    f"SELECT COUNT(*) AS n FROM (SELECT 1 AS x FROM requests WHERE {batch_match('b')} "
                    "ORDER BY batch_id, commit_order LIMIT :nc) c",
                    {"b": batch_id, "nc": int(max_children) + 1})
                if int(head["n"]) > max_children or len(items) > max_items:
                    return _batch_skip(reason_code="batch_delete_too_large")
                reqs = self._db.query(
                    # 범위 표기(batch_match) -- 등식이면 PG 일반 계획(prepare 5회 뒤)이 큰 배치에서 pkey 전체를 훑는다.
                    f"SELECT * FROM requests WHERE {batch_match('b')} ORDER BY request_id LIMIT :nc{lock}",
                    {"b": batch_id, "nc": int(max_children) + 1})
                if batch is None and len(reqs) == 0 and len(items) == 0:
                    return _batch_skip(reason_code="batch_not_found")
                if len(reqs) > max_children or len(items) > max_items:
                    return _batch_skip(reason_code="batch_delete_too_large")
                if len(reqs) != expected_request_count:
                    return _batch_skip(reason_code="batch_changed")
                rids = [r["request_id"] for r in reqs]
                foreign = self._query_in(
                    "SELECT request_id FROM batch_items WHERE batch_id <> :b AND request_id IN ({}) LIMIT 1",
                    rids, {"b": batch_id})
                if foreign:
                    return _batch_skip(reason_code="batch_child_shared", request_id=foreign[0]["request_id"])
                for r in reqs:
                    if r["state"] not in _TERMINAL_REQ:
                        return _batch_skip(reason_code="request_not_deletable", request_id=r["request_id"])
                jobs_by_rid: dict[str, list] = {}
                for j in self._query_in(
                        f"SELECT {_JOB_COLUMNS} FROM data_jobs WHERE request_id IN ({{}}) "
                        f"ORDER BY request_id, created_at, job_id{lock}", rids):
                    jobs_by_rid.setdefault(j["request_id"], []).append(j)
                for r in reqs:
                    if any(j["state"] not in _TERMINAL_JOB for j in jobs_by_rid.get(r["request_id"], [])):
                        return _batch_skip(reason_code="request_job_active", request_id=r["request_id"])
                for r in reqs:
                    if self._recently_touched(r, jobs_by_rid.get(r["request_id"], []), now=now,
                                              quiet_seconds=quiet_seconds):
                        return _batch_skip(reason_code="request_recently_finished", request_id=r["request_id"])
                job_ids = self._purge_locked(reqs, jobs_by_rid, actor=actor, artifact_base=artifact_base,
                                             now=now, batch_id=batch_id)
                self._db.execute("DELETE FROM batch_items WHERE batch_id = :b", {"b": batch_id})
                if batch is not None and self._db.execute_count(
                        "DELETE FROM batches WHERE batch_id = :b AND status IN ('Completed', 'Cancelled')",
                        {"b": batch_id}) != 1:
                    raise _Lost()
                if self._db.query_one(f"SELECT 1 AS x FROM requests WHERE {batch_match('b')} LIMIT 1", {"b": batch_id}):
                    raise _Lost()                     # 유령 자식 -- 사용자가 보지 않은 자식은 지우지 않는다
                self._db.execute(
                    """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                           before_state, after_state, at)
                       VALUES ('batch', 'delete', :b, :actor, :before, NULL, :at)""",
                    {"b": batch_id, "actor": actor, "at": now,
                     "before": self._batch_bounded(batch, items, rids, dangling=batch is None)})
        except _Lost:
            # 그 사이 상태가 바뀌었거나 다른 관리자가 지웠다 -- 조용한 성공이 아니다. 다시 시도하면 된다.
            return _batch_skip(reason_code="batch_changed")
        return {"deleted": True, "request_ids": rids, "job_ids": job_ids, "dangling": batch is None}

    def _enqueue(self, request_id, out_jobs, *, old, artifact_base, actor, now) -> None:
        """아웃박스 행 INSERT -- 호출자 트랜잭션 안. 같은 request_id 의 행이 이미 있으면(지운 요청이 DB 복원으로 되살아나
        purge_target_still_present 로 멈춰 있던 것을 다시 지움) PK 충돌로 삭제 전체가 500 이 되지 않게 그 행을 새 삭제로
        갱신한다: 잡 목록은 합집합(옛 행만 아는 잡의 열쇠를 잃지 않는다), 단계·실패 이력은 처음부터, base 는 지금 값.
        old = 호출자가 같은 트랜잭션에서 (PG 는 잠가) 읽은 기존 행({"jobs"}) 또는 None(행 없음) -- _purge_locked 가 묶음째
        한 번에 읽는다."""
        if old is None:
            self._db.execute(
                f"""INSERT INTO request_purges ({_ROW_COLUMNS})
                    VALUES (:r, :jobs, :base, 'k8s', NULL, 0, NULL, :now, :by, :now, :now)""",
                {"r": request_id, "jobs": dump_json(out_jobs), "base": artifact_base, "now": now, "by": actor})
            return
        seen = {j["job_id"] for j in out_jobs}
        prior = _parse(old["jobs"])
        merged = out_jobs + [j for j in (prior if isinstance(prior, list) else [])
                             if isinstance(j, dict) and isinstance(j.get("job_id"), str)
                             and j["job_id"] not in seen]
        self._db.execute(
            """UPDATE request_purges SET jobs = :jobs, artifact_base = :base, stage = 'k8s', outcomes = NULL,
                   attempts = 0, last_error = NULL, next_attempt_at = :now, requested_by = :by,
                   requested_at = :now, updated_at = :now
               WHERE request_id = :r""",
            {"r": request_id, "jobs": dump_json(merged), "base": artifact_base, "now": now, "by": actor})

    @staticmethod
    def _recently_touched(req, jobs, *, now, quiet_seconds) -> bool:
        stamps = [req["updated_at"], *(j["updated_at"] for j in jobs)]
        if any(not isinstance(s, str) for s in stamps):
            return True                     # 모름 = 지우지 않는다(신뢰 경계, fail-closed)
        return max(stamps) > iso_plus(now, -int(quiet_seconds))

    @staticmethod
    def _outbox_job(job) -> dict:
        refs = _parse(job["phase_refs"])
        uri = job["artifact_uri"]
        return {"job_id": job["job_id"],
                "phase_refs": refs if isinstance(refs, dict) else None,
                "artifact_uri": uri if isinstance(uri, str) else None}

    def _transitions(self, kind, entity_id) -> "tuple[list, bool]":
        # 최신 _TRANSITIONS_MAX 건(컨펌·취소 같은 사람 행위는 뒤쪽 전이다)을 시간 오름차순으로.
        rows = self._db.query(
            """SELECT from_state, to_state, reason_code, actor, at FROM state_transitions
               WHERE entity_kind = :k AND entity_id = :id ORDER BY id DESC LIMIT :n""",
            {"k": kind, "id": entity_id, "n": _TRANSITIONS_MAX + 1})
        return list(reversed(rows[:_TRANSITIONS_MAX])), len(rows) > _TRANSITIONS_MAX

    def _snapshot(self, req, jobs) -> dict:
        """감사 before_state(설계 §3.3). 넣지 않는 것: diag_logs(최대 64KB)·preview_summary·result_summary 원문·
        worker_pool 의 candidates·rejections -- 크기와 경로 노출. payload 는 그대로라 run_as_root·owner_username 이
        보존된다(root 실행 여부의 감사 근거)."""
        truncated = False
        result = self._db.query_one(
            "SELECT terminal_state, reason_code, message, completed_at FROM results WHERE request_id = :r",
            {"r": req["request_id"]})
        if result is not None and isinstance(result["message"], str):
            result["message"] = result["message"][:_MESSAGE_MAX]
        req_transitions, cut = self._transitions("request", req["request_id"])
        truncated = truncated or cut
        out_jobs = []
        for j in jobs:
            wp = _parse(j["worker_pool"])
            ident = wp.get("identity") if isinstance(wp, dict) else None
            identity = ({k: ident.get(k) for k in ("uid", "gid", "privileged", "username")}
                        if isinstance(ident, dict) else None)
            job_transitions, cut = self._transitions("data_job", j["job_id"])
            truncated = truncated or cut
            out_jobs.append({
                "job_id": j["job_id"], "tool": j["tool"], "operation": j["operation"], "state": j["state"],
                "reason_code": j["reason_code"], "storage_name": j["storage_name"],
                "source_storage": j["source_storage"], "destination_storage": j["destination_storage"],
                "source": j["source"], "destination": j["destination"], "target": j["target"],
                "identity": identity, "files_count": j["files_count"], "bytes_count": j["bytes_count"],
                "artifact_uri": j["artifact_uri"], "phase_refs": _parse_or_raw(j["phase_refs"]),
                "created_at": j["created_at"], "updated_at": j["updated_at"],
                "transitions": job_transitions})
        # batch_id: 배치 단위 삭제의 자식이면 소속 배치(단건 경로는 늘 None) -- 배치 행도 함께 지워지므로 자식 감사가
        # 어느 배치였는지를 스스로 말해야 한다(배치 감사 행 ('batch','delete',batch_id) 와 잇는 열쇠).
        request = {k: req.get(k) for k in ("request_id", "operation", "requester_id", "actor", "auth_method",
                                            "resource_key", "priority", "state", "commit_order", "batch_id",
                                            "created_at", "updated_at")}
        request["payload"] = _parse_or_raw(req["payload"])
        return {"request": request, "result": result, "transitions": req_transitions, "jobs": out_jobs,
                "events_deleted": None, "truncated": truncated}

    @staticmethod
    def _bounded(snapshot, req, jobs) -> str:
        """32 KiB 상한(설계 §3.3). 넘으면 잡 전이 → 요청 전이 순으로 비우고 truncated, 그래도 넘으면(변조된 거대
        payload 등) 신원·상태만 남긴 골격으로 접는다. 감사 행이 실패해 삭제 전체가 막히는 일은 없게 한다."""
        text = _fits(snapshot)
        if text is not None:
            return text
        snapshot["truncated"] = True
        for job in snapshot["jobs"]:
            job["transitions"] = []
        text = _fits(snapshot)
        if text is not None:
            return text
        snapshot["transitions"] = []
        text = _fits(snapshot)
        if text is not None:
            return text
        payload = snapshot["request"].get("payload")
        payload = payload if isinstance(payload, dict) else {}
        skeleton = {
            "request": {**{k: snapshot["request"].get(k) for k in (
                "request_id", "operation", "requester_id", "actor", "auth_method", "state", "batch_id",
                "created_at", "updated_at")},
                "run_as_root": payload.get("run_as_root"), "owner_username": payload.get("owner_username")},
            "result": None,
            "transitions": [],
            "jobs": [{k: j.get(k) for k in ("job_id", "tool", "operation", "state", "identity")}
                     for j in snapshot["jobs"]],
            "events_deleted": snapshot["events_deleted"],
            "truncated": True,
        }
        text = _fits(skeleton)
        if text is not None:
            return text
        # 최후: id 만(값 자체가 거대한 변조 행). 잡 id 는 들어가는 만큼.
        ids, minimal = [], {"request": {"request_id": str(req["request_id"])[:_RAW_MAX]},
                            "job_ids": [], "truncated": True}
        for j in jobs:
            ids.append(str(j["job_id"])[:64])
            if len(dump_json({**minimal, "job_ids": ids}).encode("utf-8")) > SNAPSHOT_MAX_BYTES:
                ids.pop()
                break
        return dump_json({**minimal, "job_ids": ids})

    @staticmethod
    def _batch_bounded(batch, items, rids, *, dangling) -> str:
        """배치 감사 before_state('batch','delete' 행) -- 32 KiB 상한. 자식 각자의 감사 행이 그 자식의 전부(소속 batch_id·
        payload 포함)를 담으므로 여기선 배치 고유 정보(이름·메모·옵션·실행 신원·항목 -- 배치 행과 함께 지워져 다른 어디에도
        남지 않는다)가 우선이고, 항목 중에서도 **자식이 없는 항목**(Rejected·Queued 등 -- 대상·거부 사유가 여기뿐이다)이
        우선이다. item_count(전체 항목 수)·items_kept(남긴 항목 수)·child_count 는 늘 남는다.

        넘치면(truncated) 아래 순서로 줄인다 -- 단계마다 남는 자리만큼 **자식 id**(가장 중복된 정보: 자식 감사 행마다
        batch_id 가 있다)를 마지막에 채운다:
          1. 항목 전부 그대로
          2. 자식이 있는 항목만 줄인 모양(_lean_item -- payload 를 빼고 값이 null 인 키도 뺀 {seq, status[, request_id]
             [, reason_code]}). 대상은 그 자식의 감사 행에 있다 -- 자식 없는 항목의 대상은 남긴다
          3. 모든 항목을 줄인 모양(거부 사유는 남는다)
          4. 3단계 모양의 항목을 **들어가는 만큼만**: 자식 없는 항목 먼저, 그다음 자식 있는 항목(각각 seq 순). 출력은
             seq 순. 예전엔 3단계 다음이 「항목 비움」이라 경로가 40자 안팎인 항목 ~600개부터 항목이 통째로 사라지고
             중복인 자식 id 만 남았다(2026-10-11 검증 지적)
          5. 배치 행 자체가 거대하면(변조) 골격 키만 원문 앞부분으로 두고 1~4 를 다시
        감사가 삭제를 막는 일은 없다(단건 _bounded 와 같은 원칙). options·payload 는 _parse_or_raw -- 깨진 JSON 도 원문
        일부를 남긴다."""
        b = None
        if batch is not None:
            b = {k: batch.get(k) for k in _BATCH_KEYS}
            if isinstance(b["note"], str):
                b["note"] = b["note"][:_MESSAGE_MAX]
            b["options"] = _parse_or_raw(batch.get("options"))
        full = [{"seq": it["seq"], "status": it["status"], "request_id": it["request_id"],
                 "reason_code": it["reason_code"], "payload": _parse_or_raw(it["payload"])} for it in items]
        snap = {"batch": b, "dangling": dangling, "items": full, "item_count": len(full), "items_kept": len(full),
                "child_count": len(rids), "child_request_ids": list(rids), "truncated": False}
        text = _fits(snap)
        if text is not None:
            return text
        snap["truncated"] = True
        lean = [_lean_item(it) for it in full]
        ladder = (full,
                  [it if it["request_id"] is None else _lean_item(it) for it in full],
                  lean)
        heads = [b] if b is None else [b, {k: _clip(b.get(k)) for k in _BATCH_SKELETON_KEYS}]
        for head in heads:
            snap["batch"] = head
            for level in ladder:
                snap["items"], snap["items_kept"] = level, len(level)
                text = RequestPurgesRepository._with_ids_that_fit(snap, rids)
                if text is not None:
                    return text
            text = RequestPurgesRepository._with_items_that_fit(snap, lean, rids)
            if text is not None:
                return text
        return dump_json({"batch": None, "dangling": dangling, "items": [], "item_count": len(full),
                          "items_kept": 0, "child_count": len(rids), "child_request_ids": [], "truncated": True})

    @staticmethod
    def _with_items_that_fit(snap, lean, rids) -> "str | None":
        """_batch_bounded 4단계: lean 항목을 자식 없는 것 먼저(그다음 자식 있는 것, 각각 seq 순) 들어가는 만큼 담고(출력은
        seq 순), 남는 자리에 자식 id 를 채운 직렬화. 항목 하나 없이도 넘치면 None. 크기는 한 번 직렬화한 값에 항목마다의
        바이트를 더해 센다(_with_ids_that_fit 과 같은 이유) -- 마지막 _fits 가 계산을 검증한다."""
        snap["items"], snap["items_kept"], snap["child_request_ids"] = [], 0, []
        if _fits(snap) is None:
            return None
        size = len(dump_json(snap).encode("utf-8"))
        order = [it for it in lean if "request_id" not in it] + [it for it in lean if "request_id" in it]
        kept: list[dict] = []
        for it in order:
            add = len(dump_json(it).encode("utf-8")) + (2 if kept else 0)        # ", " 구분자
            if size + add > SNAPSHOT_MAX_BYTES:
                break
            kept.append(it)
            size += add
        while True:
            chosen = sorted(kept, key=lambda it: (not isinstance(it["seq"], int), it["seq"]
                                                  if isinstance(it["seq"], int) else str(it["seq"])))
            snap["items"], snap["items_kept"] = chosen, len(chosen)
            text = RequestPurgesRepository._with_ids_that_fit(snap, rids)
            if text is not None or not kept:
                return text
            kept.pop()

    @staticmethod
    def _with_ids_that_fit(snap, rids) -> "str | None":
        """snap(자식 id 를 뺀 나머지는 고정)에 자식 id 를 들어가는 만큼 채운 직렬화. id 가 하나도 없이도 넘치면 None.
        한 번 직렬화한 크기에 id 마다의 바이트를 더한다(id 마다 전체를 다시 직렬화하면 자식 1000개에서 수십 MB 를
        직렬화한다) -- 마지막 _fits 가 계산을 검증한다."""
        snap["child_request_ids"] = []
        if _fits(snap) is None:
            return None
        size = len(dump_json(snap).encode("utf-8"))
        kept: list[str] = []
        for rid in rids:
            rid = str(rid)[:_ID_MAX]
            add = len(dump_json(rid).encode("utf-8")) + (2 if kept else 0)        # ", " 구분자
            if size + add > SNAPSHOT_MAX_BYTES:
                break
            kept.append(rid)
            size += add
        snap["child_request_ids"] = kept
        text = _fits(snap)
        while text is None and kept:
            kept.pop()
            text = _fits(snap)
        return text

    # ---- 아웃박스(컨트롤러 request-purge 루프 전용) ----

    @staticmethod
    def _hydrate(row) -> "dict | None":
        """jobs·outcomes 를 디코드한다. jobs 가 깨졌거나 목록이 아니면 jobs·job_ids = None(모름 -- 루프는 진행하지
        않는다). job_ids 는 문자열 job_id 만(형식 검사는 FS 를 만지는 쪽 -- artifact_trash -- 이 다시 한다)."""
        if row is None:
            return None
        jobs = _parse(row["jobs"])
        if isinstance(jobs, list):
            row["jobs"] = jobs
            row["job_ids"] = [j["job_id"] for j in jobs
                              if isinstance(j, dict) and isinstance(j.get("job_id"), str)]
        else:
            row["jobs"] = None
            row["job_ids"] = None
        outcomes = _parse(row["outcomes"])
        row["outcomes"] = outcomes if isinstance(outcomes, dict) else {}
        return row

    def get(self, request_id) -> "dict | None":
        return self._hydrate(self._db.query_one(
            f"SELECT {_ROW_COLUMNS} FROM request_purges WHERE request_id = :r", {"r": request_id}))

    def due(self, now=None, *, limit: int = 20, skip_stages=()) -> list[dict]:
        """재시도 시각이 된 행 -- **재시도 시각 순**(같으면 오래된 삭제부터). 삭제 순이면 k8s 단계에서 오래 기다리는
        행(죽은 노드의 Terminating 파드)이 상한(limit)만큼 쌓였을 때 그 행들이 매 틱 가장 오래된 due 라 새 삭제가
        k8s 단계조차 시작하지 못한다(2026-10-09 검증 지적) -- 대기 행은 defer 로 재시도 시각이 뒤로 밀리므로 이 순서가
        돌림차례를 만든다. 새 삭제의 재시도 시각 = 삭제 시각. skip_stages: 루프가 따로 도는 단계(request_purger 는
        'purging' 을 전역 purge 파드 단계에서 다룬다 -- 파드를 기다리는 purging 행들이 상한을 채우지 않게 SQL 에서
        뺀다. 모르는 단계 문자열(변조)은 빠지지 않는다 -- 루프가 보고 purge_row_invalid 로 표면화)."""
        skip = list(skip_stages)
        params = {"now": now or utc_now_iso(), "n": limit, **{f"s{i}": s for i, s in enumerate(skip)}}
        not_in = (" AND stage NOT IN (" + ", ".join(f":s{i}" for i in range(len(skip))) + ")") if skip else ""
        rows = self._db.query(
            f"""SELECT {_ROW_COLUMNS} FROM request_purges WHERE next_attempt_at <= :now{not_in}
                ORDER BY next_attempt_at, requested_at, request_id LIMIT :n""", params)
        return [self._hydrate(r) for r in rows]

    def in_stage(self, stage) -> list[dict]:
        rows = self._db.query(
            f"SELECT {_ROW_COLUMNS} FROM request_purges WHERE stage = :s ORDER BY requested_at, request_id",
            {"s": stage})
        return [self._hydrate(r) for r in rows]

    def advance(self, request_id, stage, *, outcomes: "dict | None" = None, now=None) -> bool:
        """다음 단계로. 진행은 실패 이력을 지운다(attempts 0·last_error NULL·즉시 재시도). outcomes None 은 기존 값
        유지(정보용 -- 진행 판정은 FS·k8s 실상태에서 한다)."""
        if stage not in STAGES:
            raise ValueError(f"unknown purge stage: {stage!r}")
        now = now or utc_now_iso()
        return self._db.execute_count(
            """UPDATE request_purges SET stage = :s, attempts = 0, last_error = NULL,
                   next_attempt_at = :now, updated_at = :now,
                   outcomes = COALESCE(:o, outcomes)
               WHERE request_id = :r""",
            {"s": stage, "now": now, "o": dump_json(outcomes) if outcomes is not None else None,
             "r": request_id}) == 1

    def defer(self, request_id, *, seconds, reason_code=None, outcomes: "dict | None" = None, now=None) -> bool:
        """실패가 아닌 대기(예: 파드 종료 대기) -- attempts 는 그대로. last_error 는 지금의 지연 사유로 바꾼다(None =
        지연 표면화 없음 -- 앞선 실패가 풀려 대기만 남았다는 뜻이라 지운다). outcomes None 은 기존 값 유지(advance 와
        같은 규칙 -- k8s 단계가 여러 틱에 걸쳐 지운 객체 수를 누적한다). 반환 = last_error 가 바뀌었나(호출자가 바뀔
        때만 이벤트를 남겨 스팸을 막는다)."""
        now = now or utc_now_iso()
        with self._db.transaction():
            row = self._db.query_one(
                f"SELECT last_error FROM request_purges WHERE request_id = :r{self._lock()}", {"r": request_id})
            if row is None:
                return False
            self._db.execute(
                """UPDATE request_purges SET next_attempt_at = :next, last_error = :e, updated_at = :now,
                       outcomes = COALESCE(:o, outcomes)
                   WHERE request_id = :r""",
                {"next": iso_plus(now, max(int(seconds), 0)), "e": reason_code, "now": now, "r": request_id,
                 "o": dump_json(outcomes) if outcomes is not None else None})
        return row["last_error"] != reason_code

    def fail(self, request_id, *, reason_code, interval, now=None) -> bool:
        """실패 백오프: attempts+1, last_error, next_attempt_at = now + min(interval × 2^min(attempts, 6), 900).
        포기 상태는 없다 -- 지연(stalled)으로 표면화하고 계속 재시도한다(조용한 포기 금지). 반환 = last_error 가
        바뀌었나(purge_failed 이벤트는 바뀔 때만)."""
        now = now or utc_now_iso()
        with self._db.transaction():
            row = self._db.query_one(
                f"SELECT attempts, last_error FROM request_purges WHERE request_id = :r{self._lock()}",
                {"r": request_id})
            if row is None:
                return False
            attempts = row["attempts"] if isinstance(row["attempts"], int) and row["attempts"] >= 0 else 0
            delay = min(max(int(interval), 1) * 2 ** min(attempts, 6), BACKOFF_CAP_SECONDS)
            self._db.execute(
                """UPDATE request_purges SET attempts = :a, last_error = :e, next_attempt_at = :next,
                       updated_at = :now WHERE request_id = :r""",
                {"a": attempts + 1, "e": reason_code, "next": iso_plus(now, delay), "now": now,
                 "r": request_id})
        return row["last_error"] != reason_code

    def target_still_present(self, request_id, job_ids) -> bool:
        """지운 요청·잡 id 가 requests/data_jobs 에 다시 있나(DB 복원·변조). 참이면 루프는 아무것도 지우지 않는다 --
        살아 있는 행의 파드·아티팩트를 아웃박스가 지우게 두지 않는다."""
        if self._db.query_one("SELECT 1 AS x FROM requests WHERE request_id = :r", {"r": request_id}):
            return True
        ids = list(job_ids or [])
        for i in range(0, len(ids), _CHUNK):
            names = {f"i{n}": v for n, v in enumerate(ids[i:i + _CHUNK])}
            if self._db.query_one(
                    "SELECT 1 AS x FROM data_jobs WHERE job_id IN ("
                    + ", ".join(":" + k for k in names) + ") LIMIT 1", names):
                return True
        return False

    def present_targets(self, rows) -> set:
        """target_still_present 의 묶음판 -- rows({request_id, job_ids}) 중 원 요청이나 잡 하나라도 requests/data_jobs
        에 다시 있는 행의 request_id 집합. job_ids None(깨진 행)은 요청만 본다(target_still_present 와 같다).
        정리 루프의 전역 단계(_live_rows)는 틱마다 두 번 purging·files 행 **전부**를 거르는데, 행마다 두 문장이면 큰 배치
        삭제가 남긴 수천 행의 대기열에서 틱이 행 수에 비례해 느려졌다(PG 실측 5000행 1.2초 -- 그중 70%가 이 확인,
        2026-10-11 검증 지적: 컨트롤러는 루프를 한 프로세스에서 차례로 돌려 planner·stepper 틱까지 밀린다). 묶음(_CHUNK)
        마다 한 문장이다."""
        rows = list(rows)
        rids = [r["request_id"] for r in rows]
        owner = {}
        for r in rows:
            for jid in r.get("job_ids") or []:
                owner.setdefault(jid, set()).add(r["request_id"])
        present = {x["request_id"] for x in self._query_in(
            "SELECT request_id FROM requests WHERE request_id IN ({})", list(dict.fromkeys(rids)))}
        for x in self._query_in("SELECT job_id FROM data_jobs WHERE job_id IN ({})", list(owner)):
            present |= owner[x["job_id"]]
        return present

    def finish(self, request_id, job_ids) -> bool:
        """정리 완료: 늦게 들어온 행을 한 번 더 지우고(최종 scrub) 아웃박스 행을 지운다 -- 한 트랜잭션.
        늦은 INSERT 경로: 종단 가드 스킵·경합 step_error 이벤트, 진단 로그 손상 이벤트, 사용량 digest 재삽입, 늦은
        전이·결과(results -- 경합한 종단 전이의 백스톱). 반환 = 아웃박스 행을 지웠나. 그 사이 원 행이 되살아났으면(target_still_present) 아무것도 지우지 않고
        False -- 살아 있는 요청의 이벤트·전이를 scrub 하지 않는다."""
        ids = list(job_ids or [])
        with self._db.transaction():
            if self.target_still_present(request_id, ids):
                return False
            self._db.execute("DELETE FROM events WHERE request_id = :r", {"r": request_id})
            # results: 삭제 트랜잭션과 경합한 종단 전이(finalize_from_job·set_state_with_result)가 남길 수 있던 고아 --
            # _apply_state 의 잠금·영향 행 확인이 막지만, 지표(plan_rejected)가 results 만 읽으니 백스톱으로 한 번 더.
            self._db.execute("DELETE FROM results WHERE request_id = :r", {"r": request_id})
            self._in("DELETE FROM scan_report_digests WHERE job_id IN ({})", ids)
            self._in("DELETE FROM state_transitions WHERE entity_kind = 'data_job' AND entity_id IN ({})", ids)
            self._db.execute(
                "DELETE FROM state_transitions WHERE entity_kind = 'request' AND entity_id = :r",
                {"r": request_id})
            removed = self._db.execute_count(
                "DELETE FROM request_purges WHERE request_id = :r", {"r": request_id})
        return removed == 1

    def pending_count(self) -> int:
        return self._db.query_one("SELECT COUNT(*) AS n FROM request_purges")["n"]

    def status(self, *, limit: int = 50) -> dict:
        """GET /api/admin/request-purges 의 원천 -- 정리의 유일한 운영 표면(전역 이벤트 뷰어가 없다). stalled =
        last_error 가 있는 행(실패 백오프 중이거나 오래 대기 중). items 는 오래된 순."""
        agg = self._db.query_one(
            """SELECT COUNT(*) AS pending,
                      SUM(CASE WHEN last_error IS NOT NULL THEN 1 ELSE 0 END) AS stalled,
                      MIN(requested_at) AS oldest FROM request_purges""")
        items = self._db.query(
            """SELECT request_id, stage, attempts, last_error, requested_at, requested_by, next_attempt_at
               FROM request_purges ORDER BY requested_at, request_id LIMIT :n""", {"n": limit})
        # SUM 은 행 0개면 NULL -- 그때의 지연 건수는 0 이 맞다(행이 없다는 사실이지 모름이 아니다).
        stalled = 0 if agg["stalled"] is None else int(agg["stalled"])
        return {"pending": agg["pending"], "stalled": stalled,
                "oldest_requested_at": agg["oldest"], "items": items}
