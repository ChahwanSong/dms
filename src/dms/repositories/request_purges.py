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

**삭제 목록이 곧 일관성의 전부다** -- 저장소 전체에 FK 가 0건이라, request_id·job_id 를 담는 테이블을 새로 만들고 여기
(PURGED_TABLES·delete_terminal·finish)에 넣지 않으면 그 행은 조용히 고아가 된다. tests/test_repo_request_purges.py 가
스키마의 request_id/job_id/entity_id 컬럼 전수를 열거해 PURGED_TABLES ∪ PURGE_EXEMPT_TABLES 와 대조한다.
"""
from ..db import Database, dump_json, iso_plus, load_json, utc_now_iso
from ..domain import TERMINAL_DATA_JOB_STATES, TERMINAL_REQUEST_STATES

# 요청 하나를 지울 때 그 요청·잡·plan 을 가리키는 행을 지우는 테이블(delete_terminal 이 전부 DELETE 한다).
PURGED_TABLES = ("requests", "results", "plans", "data_jobs", "state_transitions", "events",
                 "scan_report_digests")
# 요청·잡 id 를 담지만 지우지 않는 테이블 -- 명시 허용 목록:
#   batch_items    배치 자식은 삭제 대상이 아니다(게이트가 거부) -- 행을 가리키는 항목이 있으면 지우지 않는다.
#   audit_log      감사는 보존한다(target_key 문자열). 삭제 기록 자신도 여기 남는다.
#   request_purges 이 저장소의 아웃박스 -- 정리가 끝나면 finish 가 지운다.
PURGE_EXEMPT_TABLES = ("batch_items", "audit_log", "request_purges")

# 상태 문자열 집합 비교 -- DB 가 신뢰 경계라 열거형 변환(ValueError)을 거치지 않는다. 집합 밖 = 비종단(fail-closed).
_TERMINAL_REQ = frozenset(s.value for s in TERMINAL_REQUEST_STATES)
_TERMINAL_JOB = frozenset(s.value for s in TERMINAL_DATA_JOB_STATES)

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


class _Lost(Exception):
    """delete_terminal 의 마지막 CAS DELETE 가 졌다 -- 트랜잭션을 롤백시키려고 던진다(밖으로 새지 않는다)."""


def _skip(*, reason_code) -> dict:
    # reason_code= 키워드 리터럴로만 부른다 -- AST 추출기(tests/test_reason_codes_coverage.py)가 그 자리만 본다.
    return {"deleted": False, "reason": reason_code}


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
        메서드를 부르지 않고 원시 SQL 만 쓴다. 잠금 순서는 requests → data_jobs: 다른 경로는 두 테이블을 한
        트랜잭션에서 함께 잠그지 않거나(stepper set_job_state·set_phase_ref 는 data_jobs 만, finalize_from_job 은
        requests 만) 같은 방향이라(planner create_plan_and_job) 교착이 없다.

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
                plan_ids = [r["plan_id"] for r in self._db.query(
                    "SELECT plan_id FROM plans WHERE request_id = :r", {"r": request_id})]
                job_ids = [j["job_id"] for j in jobs]
                snapshot = self._snapshot(req, jobs)            # 지우기 전에 읽는다
                for kind, ids in (("data_job", job_ids), ("plan", plan_ids), ("request", [request_id])):
                    self._in("DELETE FROM state_transitions WHERE entity_kind = :k AND entity_id IN ({})",
                             ids, {"k": kind})
                self._in("DELETE FROM scan_report_digests WHERE job_id IN ({})", job_ids)
                self._db.execute("DELETE FROM data_jobs WHERE request_id = :r", {"r": request_id})
                self._db.execute("DELETE FROM plans WHERE request_id = :r", {"r": request_id})
                self._db.execute("DELETE FROM results WHERE request_id = :r", {"r": request_id})
                snapshot["events_deleted"] = self._db.execute_count(
                    "DELETE FROM events WHERE request_id = :r", {"r": request_id})
                # 마지막 CAS -- 잠금이 없는 sqlite 경로와 잠금 누락에 대한 이중 가드. 진 쪽은 전부 롤백된다.
                states = sorted(_TERMINAL_REQ)
                params = {"r": request_id, **{f"s{i}": s for i, s in enumerate(states)}}
                removed = self._db.execute_count(
                    "DELETE FROM requests WHERE request_id = :r AND batch_id IS NULL AND state IN ("
                    + ", ".join(f":s{i}" for i in range(len(states))) + ")", params)
                if removed != 1:
                    raise _Lost()
                self._enqueue(request_id, [self._outbox_job(j) for j in jobs],
                              artifact_base=artifact_base, actor=actor, now=now)
                self._db.execute(
                    """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                           before_state, after_state, at)
                       VALUES ('request', 'delete', :r, :actor, :b, NULL, :at)""",
                    {"r": request_id, "actor": actor, "b": self._bounded(snapshot, req, jobs), "at": now})
        except _Lost:
            # 경합 패배 -- 조용한 성공으로 보고하지 않는다(그 사이 상태가 바뀌었거나 다른 관리자가 지웠다).
            return _skip(reason_code="request_not_deletable")
        return {"deleted": True, "job_ids": job_ids}

    def _enqueue(self, request_id, out_jobs, *, artifact_base, actor, now) -> None:
        """아웃박스 행 INSERT -- 호출자 트랜잭션 안. 같은 request_id 의 행이 이미 있으면(지운 요청이 DB 복원으로 되살아나
        purge_target_still_present 로 멈춰 있던 것을 다시 지움) PK 충돌로 삭제 전체가 500 이 되지 않게 그 행을 새 삭제로
        갱신한다: 잡 목록은 합집합(옛 행만 아는 잡의 열쇠를 잃지 않는다), 단계·실패 이력은 처음부터, base 는 지금 값."""
        old = self._db.query_one(
            f"SELECT jobs FROM request_purges WHERE request_id = :r{self._lock()}", {"r": request_id})
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
        request = {k: req.get(k) for k in ("request_id", "operation", "requester_id", "actor", "auth_method",
                                            "resource_key", "priority", "state", "commit_order",
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
                "request_id", "operation", "requester_id", "actor", "auth_method", "state",
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
