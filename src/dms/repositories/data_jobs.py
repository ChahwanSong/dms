"""data_jobs + plans 저장소: planner가 emit하고 stepper(3b)가 전진시키는 잡 레코드."""
import uuid
from ..db import Database, dump_json, iso_epoch, iso_plus, like_contains, load_json, utc_now_iso
from ..domain import (DataJobState, RequestState, TERMINAL_DATA_JOB_STATES,
                      TERMINAL_REQUEST_STATES)
from .observability import ObservabilityRepository

_JSON_COLUMNS = ("options", "worker_pool", "precondition", "result_summary",
                 "preview_summary", "volcano_job_ref", "phase_refs")

# set_job_state는 stepper.py(actor="stepper")와 batch_orchestrator.py
# (actor="batch-orchestrator") 말고도 API 라우트(actor=identity.actor, 즉 실제
# 사용자명)에서도 불린다. 종단 가드 이벤트의 component에 사용자명을 그대로 쓰면
# 사용자 수만큼 값이 늘어나(고카디널리티) 슬라이스 14 대시보드가 component를
# 그룹핑 키로 못 쓴다(설계 §5). stepper/batch-orchestrator는 고정된 소수 리터럴이라
# 그대로 어휘에 넣고, 그 외(=API 호출자)는 전부 "api"로 접는다 -- 실제 발신자는
# message에 남기므로 정보 손실은 없다.
_KNOWN_COMPONENTS = frozenset({"stepper", "batch-orchestrator"})

# 슬라이스 25 §2.2: 다행 조회가 diag_logs(행당 최대 64KB)를 절대 싣지 않기 위한
# 명시 컬럼 목록 -- builds.list 의 I2 선례(5초 폴링 x 50행 x 64KB = 3.2MB 왕복).
# get_job(단행)만 SELECT * 로 diag_logs 를 포함한다(/logs 박제 폴백이 그걸 읽는다).
# 새 컬럼을 추가할 때는 migrations 양쪽 + 이 목록까지 세 곳이다 --
# tests/test_repo_diag_logs.py 의 컬럼 패리티 계약이 누락을 잡는다.
_ROW_COLUMNS_SANS_DIAG = (
    "job_id, request_id, operation, tool, storage_name, source_storage, "
    "destination_storage, source, destination, target, options, priority, state, "
    "reason_code, preview_fingerprint, preview_expires_at, preview_summary, volcano_job_ref, "
    "artifact_uri, result_summary, files_count, bytes_count, worker_pool, "
    "precondition, confirmed_fingerprint, phase_refs, submit_wait_seconds, "
    "exec_submitted_at, sched_wait_seconds, pods_gc_at, created_at, updated_at")


# 사용량 분석의 IN 목록 한 문장 상한(recent_scans_for_targets·jobs_by_ids) -- scan_digests._CHUNK 와 같은 값. 라우트도
# 이 크기 묶음으로 부르지만, 저장소가 다시 자른다(호출자가 묶음을 잊어도 한 문장에 수천 자리표시자가 실리지 않게).
_IN_CHUNK = 500


def _guard_component(actor: str) -> str:
    return actor if actor in _KNOWN_COMPONENTS else "api"


class _PlanLost(Exception):
    """create_plan_and_job 의 조건부 Pending→Planned 가 졌다 -- 트랜잭션을 롤백시키려고 던진다(밖으로 새지 않는다)."""


def _as_count(value) -> "int | None":
    # result_summary는 신뢰 경계 밖(runner 산출물)이다. bool은 int의 서브클래스라
    # 명시적으로 제외하고, 음수는 계수로서 의미가 없어 버린다 -- 잘못된 값이
    # typed 컬럼으로 새면 SUM 집계 전체가 오염된다.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class DataJobsRepository:
    def __init__(self, db: Database):
        self._db = db

    def _record_transition(self, entity_kind, entity_id, from_state, to_state,
                           reason_code, actor, at):
        self._db.execute(
            """INSERT INTO state_transitions (entity_kind, entity_id, from_state,
                   to_state, reason_code, actor, at)
               VALUES (:k, :id, :f, :t, :r, :actor, :at)""",
            {"k": entity_kind, "id": entity_id,
             "f": from_state.value if from_state is not None else None,
             "t": to_state.value, "r": reason_code, "actor": actor, "at": at})

    def _insert_plan(self, plan_id, request_id, *, actor, now):
        """plan 행 + 전이의 문장 몸통 -- 트랜잭션은 호출자가 소유한다(Database.transaction 은 중첩이 없다)."""
        self._db.execute(
            """INSERT INTO plans (plan_id, request_id, job_id, state,
                   created_at, updated_at)
               VALUES (:p, :r, NULL, 'Planned', :now, :now)""",
            {"p": plan_id, "r": request_id, "now": now})
        self._record_transition("plan", plan_id, None,
                                RequestState.PLANNED, None, actor, now)

    def _insert_job(self, job_id, request_id, plan_id, *, operation, priority, storage_name,
                    source_storage, destination_storage, source, destination, target, options,
                    tool, worker_pool, precondition, actor, now):
        """job 행 + plans.job_id + 전이의 문장 몸통 -- 트랜잭션은 호출자가 소유한다."""
        self._db.execute(
            """INSERT INTO data_jobs (job_id, request_id, operation, tool,
                   storage_name, source_storage, destination_storage, source,
                   destination, target, options, priority, state, worker_pool,
                   precondition, created_at, updated_at)
               VALUES (:j, :r, :op, :tool, :sn, :ss, :ds, :src, :dst, :tgt,
                   :opts, :pri, :state, :wp, :pre, :now, :now)""",
            {"j": job_id, "r": request_id, "op": operation, "tool": tool,
             "sn": storage_name, "ss": source_storage, "ds": destination_storage,
             "src": source, "dst": destination, "tgt": target,
             "opts": dump_json(options), "pri": priority,
             "state": DataJobState.PENDING.value, "wp": dump_json(worker_pool),
             "pre": dump_json(precondition), "now": now})
        self._db.execute(
            "UPDATE plans SET job_id = :j, updated_at = :now WHERE plan_id = :p",
            {"j": job_id, "now": now, "p": plan_id})
        self._record_transition("data_job", job_id, None,
                                DataJobState.PENDING, None, actor, now)

    def create_plan(self, request_id, *, actor) -> str:
        # 테스트 픽스처용 단독 경로(무검증 INSERT). planner 는 create_plan_and_job 만 쓴다.
        plan_id = uuid.uuid4().hex
        now = utc_now_iso()
        with self._db.transaction():
            self._insert_plan(plan_id, request_id, actor=actor, now=now)
        return plan_id

    def create_job(self, request_id, plan_id, *, operation, priority,
                   storage_name=None, source_storage=None, destination_storage=None,
                   source=None, destination=None, target=None, options: dict, tool,
                   worker_pool: dict, precondition: dict, actor) -> str:
        # 테스트 픽스처용 단독 경로(무검증 INSERT -- 요청 상태를 보지 않는다). planner 는 create_plan_and_job 만 쓴다.
        job_id = uuid.uuid4().hex
        now = utc_now_iso()
        with self._db.transaction():
            self._insert_job(job_id, request_id, plan_id, operation=operation, priority=priority,
                             storage_name=storage_name, source_storage=source_storage,
                             destination_storage=destination_storage, source=source,
                             destination=destination, target=target, options=options, tool=tool,
                             worker_pool=worker_pool, precondition=precondition, actor=actor, now=now)
        return job_id

    def create_plan_and_job(self, request_id, *, operation, priority,
                            storage_name=None, source_storage=None, destination_storage=None,
                            source=None, destination=None, target=None, options: dict, tool,
                            worker_pool: dict, precondition: dict, actor) -> "str | None":
        """planner 의 emit -- plan·job INSERT 와 요청 Pending→Planned 를 **한 트랜잭션 + CAS** 로 묶는다(2026-10-08,
        요청 삭제 선행 보강). 반환 = job_id, 또는 None(요청이 없거나 더는 Pending 이 아니다 -- 아무것도 남기지 않았다).

        왜: planner 는 Pending 요청을 읽은 뒤 LDAP 해석(틱 예산까지)을 거쳐 emit 한다. 그 사이 사용자가 취소하면
        예전의 create_plan + create_job(무검증 INSERT) + set_state(PLANNED)(_apply_state 에 종단 가드 없음)는
        Cancelled → Planned 로 요청을 **되살리고**(results PK 가 이미 있어 영구 Planned + 매 틱 orphan_recovery 실패),
        관리자가 그 사이 요청을 지웠으면 요청 없는 Pending 잡을 남겨 stepper 가 **실행**했다. 여기서는 요청 행을 먼저
        잠그고(PG FOR UPDATE -- 요청 삭제 트랜잭션과 같은 requests → data_jobs 순서라 교착이 없다) 조건부 UPDATE 의
        영향이 1 이 아니면 전부 롤백한다. 전이 순서·actor 는 예전 세 호출과 같다(plan → data_job → request)."""
        plan_id, job_id = uuid.uuid4().hex, uuid.uuid4().hex
        now = utc_now_iso()
        lock = " FOR UPDATE" if self._db.dialect == "postgresql" else ""
        try:
            with self._db.transaction():
                req = self._db.query_one(
                    f"SELECT state FROM requests WHERE request_id = :r{lock}", {"r": request_id})
                if req is None or req["state"] != RequestState.PENDING.value:
                    return None                  # 읽기만 한 트랜잭션의 커밋 -- 무해
                self._insert_plan(plan_id, request_id, actor=actor, now=now)
                self._insert_job(job_id, request_id, plan_id, operation=operation, priority=priority,
                                 storage_name=storage_name, source_storage=source_storage,
                                 destination_storage=destination_storage, source=source,
                                 destination=destination, target=target, options=options, tool=tool,
                                 worker_pool=worker_pool, precondition=precondition, actor=actor, now=now)
                moved = self._db.execute_count(
                    """UPDATE requests SET state = :planned, updated_at = :now
                       WHERE request_id = :r AND state = :pending""",
                    {"planned": RequestState.PLANNED.value, "pending": RequestState.PENDING.value,
                     "now": now, "r": request_id})
                if moved != 1:
                    raise _PlanLost()            # sqlite(잠금 없음)·잠금 누락 대비 이중 가드 -- 전부 롤백
                self._record_transition("request", request_id, RequestState.PENDING,
                                        RequestState.PLANNED, None, actor, now)
        except _PlanLost:
            return None
        return job_id

    def _hydrate(self, row):
        if row is None:
            return None
        for col in _JSON_COLUMNS:
            if col in row:
                row[col] = load_json(row[col])
        return row

    def get_job(self, job_id):
        return self._hydrate(self._db.query_one(
            "SELECT * FROM data_jobs WHERE job_id = :j", {"j": job_id}))

    def succeeded_scans(self, storage_name: str, *, limit: int = 50) -> list[dict]:
        """storage_name에서 성공(Succeeded)한 scan 잡을 **완료 최신순**으로 최대 limit건.
        커버링 scan 조회(Task 3)가 후보를 DB 필드만으로 좁히는 데 쓴다 —
        target(스토리지 상대 경로)만 보고, 리포트는 매치된 1건만 나중에 읽는다.

        정렬은 created_at이 아니라 updated_at이다: 통계의 신선도는 **언제 끝났는지**로
        정해진다(리포트는 성공 시점의 파일시스템 상태다). 큰 스캔이 먼저 제출되고 늦게
        끝나면 created_at 순서는 오래된 리포트를 최신인 척 앞세운다. Succeeded 전이에서
        set_job_state가 updated_at을 찍고, idx_data_jobs_state (state, updated_at)이
        이 정렬을 그대로 받아준다."""
        rows = self._db.query(
            f"""SELECT {_ROW_COLUMNS_SANS_DIAG} FROM data_jobs
                WHERE operation = 'scan' AND state = :s AND storage_name = :sn
                ORDER BY updated_at DESC, job_id DESC LIMIT :n""",
            {"s": DataJobState.SUCCEEDED.value, "sn": storage_name, "n": limit})
        return [self._hydrate(r) for r in rows]

    @staticmethod
    def _scan_target_where(params: dict, *, q=None, storage=None, path=None) -> str:
        """사용량 분석 타깃 필터(목록·내보내기·최근 잡 조회가 같은 한 벌). q = 경로·스토리지 어느 쪽이든 부분 문자열
        (구 단일 검색), storage = 스토리지 **정확히 일치**, path = 경로 부분 문자열 -- storage 와 path 는 함께 걸린다
        (2026-10-02 사용자 요청: "두 개 다 필터"). LIKE 와일드카드는 리터럴로 이스케이프한다(검색어 '100%' 가 전부
        매치로 새면 검색이 거짓말이 된다). storage 는 None 일 때만 「필터 없음」이다 -- 빈 문자열도 이름이다(2026-10-20
        리뷰: 사용량 탭 요약이 변조·구형 행의 빈 이름을 탭으로 내는데 truthy 검사가 그 탭을 전 스토리지로 접었다)."""
        params["s"] = DataJobState.SUCCEEDED.value
        where = "operation = 'scan' AND state = :s AND target IS NOT NULL"
        # 이스케이프는 db.like_contains 한 벌(작업 목록 검색과 공용) -- 대소문자는 접지 않는다(종전 동작 그대로).
        if q:
            where += r" AND (target LIKE :q ESCAPE '\' OR storage_name LIKE :q ESCAPE '\')"
            params["q"] = like_contains(q)
        if storage is not None:
            where += " AND storage_name = :sn"
            params["sn"] = storage
        if path:
            where += r" AND target LIKE :p ESCAPE '\'"
            params["p"] = like_contains(path)
        return where

    def scan_target_heads(self, *, q: "str | None" = None, storage: "str | None" = None,
                          path: "str | None" = None, order: str = "desc",
                          limit: "int | None" = 100, sort: str = "last_scan") -> list[dict]:
        """성공 scan 이 있는 (storage, target) 목록 — **전 요청자 통합**(사용량
        분석, 2026-08-23 사용자 요청: "요청자에 관계없이 모든 작업"). 필터는
        _scan_target_where. 정렬은 최근 스캔 기준 — 기본 내림차순(화면의 기본 질문이
        "요즘 뭘 스캔했나"), order="asc" 면 오래된 것부터(2026-10-02: 오래 안 본
        타깃 찾기). 정렬은 반드시 SQL 에서 한다 -- limit 뒤에 화면이 뒤집으면 "최근 N개
        중 가장 오래된 것"이 되어 진짜 오래된 타깃이 안 보인다. limit=None 은 전부.
        first_scan_at = 최초 성공 스캔 완료 시각. sort="scans" 면 스캔 횟수 기준(2026-10-19 목록 열
        정렬 -- 동률은 최근 스캔 늦은 것부터). 실 사용량·파일 수·hot 비율 정렬은 SQL 열이 아니라 리포트 요약 값이라
        라우트가 한다(usage_metrics).

        행 = 타깃의 **머리**(2026-10-20 스토리지별 분할·창 1만): storage_name·target·scan_count·first_scan_at·
        last_scan_at(옛 GROUP BY 와 같은 값 -- COUNT/MIN OVER, 최신 행의 updated_at = MAX) + 그 최신 성공 scan 의
        job_id·request_id·bytes_count. 최신 판정은 succeeded_scans_for_target 과 같은 근거(updated_at DESC, job_id
        DESC). 왜 한 문장인가: 지표 정렬은 창(최대 1만 타깃)마다 최신 잡의 리포트 요약을 봐야 하는데, 옛 구조는 창의 쌍
        전부를 OR 300쌍 묶음으로 다시 물어(1만이면 34문장, 문장마다 성공 잡 전체를 훑음) 전체 잡 행을 메모리에 세웠다
        -- 머리에 job_id 가 있으면 라우트가 캐시를 job_id 로 바로 묻고 전체 행은 반환할 쪽만 읽는다. 필터가 target·
        storage_name 에만 걸리므로 파티션(같은 storage·target 의 잡 전부)은 필터에 잘리지 않는다 -- 최신 판정이 필터와
        무관하다."""
        params: dict = {}
        where = self._scan_target_where(params, q=q, storage=storage, path=path)
        direction = "ASC" if order == "asc" else "DESC"
        order_by = (f"scan_count {direction}, last_scan_at DESC" if sort == "scans"
                    else f"last_scan_at {direction}")
        tail = ""
        if limit is not None:
            tail = " LIMIT :n"
            params["n"] = limit
        return self._db.query(
            f"""SELECT storage_name, target, scan_count, first_scan_at, last_scan_at,
                       job_id, request_id, bytes_count
                FROM (SELECT storage_name, target, job_id, request_id, bytes_count,
                             updated_at AS last_scan_at,
                             ROW_NUMBER() OVER (PARTITION BY storage_name, target
                                                ORDER BY updated_at DESC, job_id DESC) AS rn,
                             COUNT(*) OVER (PARTITION BY storage_name, target) AS scan_count,
                             MIN(updated_at) OVER (PARTITION BY storage_name, target) AS first_scan_at
                      FROM data_jobs WHERE {where}) heads
                WHERE rn = 1
                ORDER BY {order_by}, storage_name, target{tail}""",
            params)

    def usage_scan_request_ids(self, request_ids) -> set:
        """이 요청들 중 사용량 분석의 지점이 되는 잡(성공 scan -- _scan_target_where 의 기본 조건 그대로)을 가진 것.
        요청 목록의 has_succeeded_scan(작업 삭제 확인 창의 사용량 경고, 2026-10-09)이 쓴다 -- 요청 **상태**로 판정하면
        취소 경합(취소가 잡 목록을 읽은 뒤 stepper 가 잡을 Succeeded 로 끝냄 → 잡 Succeeded·요청 Cancelled)의 지점을
        놓친다. 사용량 분석은 잡 상태를 본다. 묶음(500)마다 한 번, idx_data_jobs_request 를 탄다."""
        ids = [r for r in dict.fromkeys(request_ids) if isinstance(r, str)]
        found = set()
        for i in range(0, len(ids), 500):
            chunk = {f"i{n}": v for n, v in enumerate(ids[i:i + 500])}
            params = dict(chunk)
            where = self._scan_target_where(params)
            rows = self._db.query(
                f"""SELECT DISTINCT request_id FROM data_jobs
                    WHERE {where} AND request_id IN ({", ".join(":" + k for k in chunk)})""",
                params)
            found.update(r["request_id"] for r in rows)
        return found

    def scan_storage_names(self) -> list[str]:
        """성공 scan 기록이 있는 storage_name 전부(사용량 분석 스토리지 필터의 선택지 -- 등록이 지워진 이름 포함)."""
        rows = self._db.query(
            """SELECT DISTINCT storage_name FROM data_jobs
               WHERE operation = 'scan' AND state = :s AND target IS NOT NULL
                     AND storage_name IS NOT NULL
               ORDER BY storage_name""",
            {"s": DataJobState.SUCCEEDED.value})
        return [r["storage_name"] for r in rows]

    def unregistered_scan_storages(self) -> list[dict]:
        """사용량 분석에 남은 **미등록** 스토리지(등록 목록 storages 에 없는 이름 -- 삭제된 스토리지의 이력)와 그 규모
        (2026-10-12 사용자 요청: 「미등록 스토리지 항목들은 스토리지 항목별로 전체 clean up」). 기준은 사용량 분석과 같다
        (_scan_target_where 의 기본 조건 = 성공 scan) -- 이 목록에 있다 = 사용량 화면에 그 이름의 행이 있다. 행:
        storage_name, target_count(대상 경로 수), succeeded_scan_count, last_scan_at. 등록 여부는 질의 시점의 storages
        행이 판정한다(화면의 「(미등록)」 표시와 같은 정확 일치)."""
        params: dict = {}
        where = self._scan_target_where(params)
        return self._db.query(
            f"""SELECT storage_name, COUNT(DISTINCT target) AS target_count,
                       COUNT(*) AS succeeded_scan_count, MAX(updated_at) AS last_scan_at
                FROM data_jobs
                WHERE {where} AND storage_name IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM storages s WHERE s.storage_name = data_jobs.storage_name)
                GROUP BY storage_name ORDER BY storage_name""",
            params)

    def scan_storage_summary(self) -> list[dict]:
        """성공 scan 기록이 있는 스토리지 이름마다 사용량 분석 규모(2026-10-20 스토리지 탭 요약): storage_name,
        target_count(대상 경로 수), succeeded_scan_count, last_scan_at. 기준은 목록과 같다(_scan_target_where 의 기본
        조건). unregistered_scan_storages 에서 「등록 목록에 없는」 조건만 뺀 같은 집계다 -- 등록 여부와 설정은 라우트가
        붙인다. GROUP BY 한 번(필요한 열이 idx_data_jobs_scan_target 에 다 있어 계획기가 인덱스만 읽을 수 있다 -- 고르는 건
        계획기 몫이다; PG 실측 6.5만 잡 64ms→9ms)."""
        params: dict = {}
        where = self._scan_target_where(params)
        return self._db.query(
            f"""SELECT storage_name, COUNT(DISTINCT target) AS target_count,
                       COUNT(*) AS succeeded_scan_count, MAX(updated_at) AS last_scan_at
                FROM data_jobs WHERE {where} AND storage_name IS NOT NULL
                GROUP BY storage_name ORDER BY storage_name""",
            params)

    def recent_scans_for_targets(self, storage_name: "str | None", targets, *, per_target: int = 1) -> dict:
        """한 스토리지의 고른 타깃들 -> target -> 그 타깃의 최근 성공 scan 잡 행 목록(최신순, 최대 per_target 개).
        사용량 분석 목록(반환 행의 latest)과 내보내기(묶음마다 최신 + 직전 1건의 증감)가 쓴다. 최신 판정은
        succeeded_scans_for_target 과 같은 근거(updated_at DESC, job_id DESC).

        왜 스토리지 하나 + `target IN (…)` 인가(2026-10-20): 옛 조회는 (storage, target) 쌍을 OR 300쌍 묶음으로 물어
        묶음마다 성공 잡 전체를 훑었다(1만 타깃이면 34문장 -- sqlite 실측 목록 15초 중 14초). 스토리지를 고정하면
        idx_data_jobs_scan_target(storage_name, state, operation, target, updated_at)의 접두로 바로 내려간다. 여러
        스토리지에 걸친 묶음(storage 없는 옛 탭의 「전체」)은 호출자가 스토리지별로 나눠 부른다. storage_name None 은
        빈 결과다 -- SQL 의 `= NULL` 은 아무것도 맞지 않는다(옛 쌍 조회와 같은 판정: storage_name 이 NULL 인 변조·구형
        행은 latest 가 없다). 빈 targets 도 빈 결과(문장 없음). 한 문장의 자리표시자는 _IN_CHUNK 개 이하로 자른다."""
        names = [t for t in dict.fromkeys(targets) if t is not None]
        if storage_name is None or not names:
            return {}
        out: dict = {}
        for i in range(0, len(names), _IN_CHUNK):
            chunk = {f"t{n}": v for n, v in enumerate(names[i:i + _IN_CHUNK])}
            params: dict = {"k": per_target, **chunk}
            # 빈 이름이면 빈 이름의 잡만(_scan_target_where 의 storage 는 None 만 「필터 없음」).
            where = self._scan_target_where(params, storage=storage_name)
            rows = self._db.query(
                f"""SELECT * FROM (
                        SELECT {_ROW_COLUMNS_SANS_DIAG},
                               ROW_NUMBER() OVER (PARTITION BY target
                                                  ORDER BY updated_at DESC, job_id DESC) AS rn
                        FROM data_jobs
                        WHERE {where} AND target IN ({", ".join(":" + k for k in chunk)})) ranked
                    WHERE rn <= :k ORDER BY target, rn""",
                params)
            for r in rows:
                r.pop("rn", None)
                job = self._hydrate(r)
                out.setdefault(job["target"], []).append(job)
        return out

    def jobs_by_ids(self, job_ids) -> dict:
        """job_id -> 잡 행(diag_logs 제외 -- _ROW_COLUMNS_SANS_DIAG). 없는 잡은 결과에 없다. 사용량 분석 지표 정렬이 캐시에
        없는 최신 잡만 전체 행(리포트를 열 요청자 uid -- worker_pool.identity)으로 읽는 데 쓴다. _IN_CHUNK 개씩."""
        ids = [j for j in dict.fromkeys(job_ids) if j is not None]
        out: dict = {}
        for i in range(0, len(ids), _IN_CHUNK):
            chunk = {f"j{n}": v for n, v in enumerate(ids[i:i + _IN_CHUNK])}
            rows = self._db.query(
                f"""SELECT {_ROW_COLUMNS_SANS_DIAG} FROM data_jobs
                    WHERE job_id IN ({", ".join(":" + k for k in chunk)})""",
                chunk)
            for r in rows:
                out[r["job_id"]] = self._hydrate(r)
        return out

    def succeeded_scans_for_target(self, storage_name: str, target: str,
                                   *, limit: int = 30) -> list[dict]:
        """한 (storage, target)의 성공 scan 이력 — 완료 최신순(succeeded_scans 와
        같은 updated_at 근거). 사용량 분석의 시계열 원천: 호출자가 각 잡의
        dscan-report.json 을 읽어 포인트로 투영한다. target 은 저장 시점에 이미
        정규화된 값이라 문자열 동등으로 족하다(covers() 류 포함 관계를 쓰면
        부모·자식 경로의 이력이 한 시계열로 섞인다)."""
        rows = self._db.query(
            f"""SELECT {_ROW_COLUMNS_SANS_DIAG} FROM data_jobs
                WHERE operation = 'scan' AND state = :s AND storage_name = :sn
                      AND target = :t
                ORDER BY updated_at DESC, job_id DESC LIMIT :n""",
            {"s": DataJobState.SUCCEEDED.value, "sn": storage_name,
             "t": target, "n": limit})
        return [self._hydrate(r) for r in rows]

    def list_jobs(self, *, request_id=None, limit=50):
        if request_id is None:
            rows = self._db.query(
                f"""SELECT {_ROW_COLUMNS_SANS_DIAG} FROM data_jobs
                    ORDER BY created_at DESC, job_id DESC LIMIT :n""",
                {"n": limit})
        else:
            rows = self._db.query(
                f"""SELECT {_ROW_COLUMNS_SANS_DIAG} FROM data_jobs WHERE request_id = :r
                    ORDER BY created_at DESC, job_id DESC LIMIT :n""",
                {"r": request_id, "n": limit})
        return [self._hydrate(r) for r in rows]

    def set_job_state(self, job_id, to_state: DataJobState, *, reason_code=None, actor):
        now = utc_now_iso()
        guard_tripped = False
        # 요청 삭제(2026-10-08)와의 경합: PG 행 잠금으로 읽고 UPDATE 영향 행 수를 확인한다 -- 잠금 없이 읽은 뒤 삭제가
        # 커밋되면 0행 UPDATE 위에 전이만 커밋돼 지운 잡의 고아 전이가 남는다(RequestsRepository._apply_state 와 같은
        # 처방). 0행이면 KeyError -- 트랜잭션이 롤백된다(행 없음과 같은 신호).
        lock = " FOR UPDATE" if self._db.dialect == "postgresql" else ""
        with self._db.transaction():
            current = self._db.query_one(
                f"""SELECT state, request_id, created_at, submit_wait_seconds
                   FROM data_jobs WHERE job_id = :j{lock}""", {"j": job_id})
            if current is None:
                raise KeyError(job_id)
            if DataJobState(current["state"]) in TERMINAL_DATA_JOB_STATES:
                # 종단 잡은 되돌리지 않는다. 취소 직후 늦게 도착한 stepper 틱이
                # Cancelled 를 덮어쓰고 고아 Volcano Job 을 만드는 경쟁을 막는다.
                # 예외 대신 조용히 무시한다 — 취소는 정상 동작이고 stepper 루프를
                # 한 잡 때문에 죽여선 안 된다. 일어나지 않은 전이는 state_transitions에
                # 기록하지 않는다 -- 진단 이벤트는 트랜잭션 밖에서(아래) 남긴다.
                guard_tripped = True
            else:
                # 제출 대기(슬라이스 17 설계 §2.3): Pending -> 첫 비-Pending 엣지에서만
                # 계산해 한 번 쓴다(write-once). IS NULL 가드는 이중 안전장치다 --
                # 상태 기계상 Pending 재진입은 없지만, 생겨도 덮어쓰지 않는다.
                # 재는 것은 created_at -> **이 호출 시각**이라 스테퍼 틱 간격이
                # 포함된다 -- DMS 내부 픽업 지연이지 Volcano 큐 대기가 아니다(§2.4).
                submit_wait = current["submit_wait_seconds"]
                if (submit_wait is None
                        and current["state"] == DataJobState.PENDING.value
                        and to_state is not DataJobState.PENDING):
                    try:
                        # 시계 스큐(다른 프로세스가 쓴 created_at)로 음수가 나올 수
                        # 있다 -- 1초 해상도 세계에서 0 으로 접는 것이 정직하다.
                        submit_wait = max(
                            0, int(iso_epoch(now) - iso_epoch(current["created_at"])))
                    except (TypeError, ValueError):
                        submit_wait = None  # 시각이 깨졌으면 지어내지 않는다(NULL)
                moved = self._db.execute_count(
                    """UPDATE data_jobs SET state = :s, reason_code = :rc,
                           updated_at = :now, submit_wait_seconds = :sw
                       WHERE job_id = :j""",
                    {"s": to_state.value, "rc": reason_code, "now": now,
                     "sw": submit_wait, "j": job_id})
                if moved != 1:
                    raise KeyError(job_id)
                self._record_transition("data_job", job_id, DataJobState(current["state"]),
                                        to_state, reason_code, actor, now)
        if guard_tripped:
            # self._db만 있고 repos가 없다 -- 저장소 규약(__init__(self, db))을 지키려고
            # 협력자를 주입받는 대신 여기서 즉석으로 만든다. ObservabilityRepository는
            # db 핸들 하나만 감싸는 얇은 래퍼라 생성 비용이 없고, 위 with-transaction
            # 블록 밖에서 호출하므로 진단 INSERT가 업무 트랜잭션에 섞이지 않는다.
            # component는 _guard_component()로 정규화한다(위 설명) -- actor를 그대로
            # 쓰면 API 호출자(사용자명)가 그대로 새어 고카디널리티가 된다. 실제
            # 발신자는 message에 남긴다.
            ObservabilityRepository(self._db).record_event(
                component=_guard_component(actor), severity="info",
                event_type="terminal_guard_skip",
                message=(f"actor={actor} job_id={job_id} state={current['state']} "
                         f"attempted_to={to_state.value}"),
                request_id=current["request_id"])

    def job_transitions(self, job_id):
        return self._db.query(
            """SELECT * FROM state_transitions
               WHERE entity_kind = 'data_job' AND entity_id = :j ORDER BY id""",
            {"j": job_id})

    _STEPPABLE_STATES = ("Pending", "Preflight", "PreviewRunning", "Executing", "Running")

    def claim_steppable(self, *, limit: int = 10):
        placeholders = ", ".join(f":s{i}" for i in range(len(self._STEPPABLE_STATES)))
        params = {f"s{i}": v for i, v in enumerate(self._STEPPABLE_STATES)}
        params["n"] = limit
        suffix = " FOR UPDATE SKIP LOCKED" if self._db.dialect == "postgresql" else ""
        rows = self._db.query(
            f"""SELECT {_ROW_COLUMNS_SANS_DIAG} FROM data_jobs
                WHERE state IN ({placeholders})
                ORDER BY updated_at LIMIT :n{suffix}""", params)
        return [self._hydrate(r) for r in rows]

    # 비종단 = 지금 노드 CPU 를 예약했거나(실행·미리보기 vcjob) 곧 예약할(Pending·ConfirmPending 등) 잡.
    _ACTIVE_STATES = tuple(s.value for s in DataJobState if s not in TERMINAL_DATA_JOB_STATES)

    def active_worker_pools(self, *, limit: int = 2000) -> list:
        """비종단 잡의 worker_pool 만(다른 컬럼·diag_logs 없음) 최근 갱신순 최대 limit 건(2026-10-16 리뷰). planner 의 노드
        선택 순서(placement.dms_node_load)가 노드별 DMS 예약 코어를 센다. idx_data_jobs_state(state, updated_at) 로 읽어
        종단 잡이 쌓여도 비용이 늘지 않는다(비종단 수는 배치 max_concurrency 가 묶는다). 상한을 넘으면 최근분만 센다 --
        순서를 정하는 어림값일 뿐 배정의 정확성은 stepper·빌더가 지킨다. 깨진 JSON 행은 건너뛴다(DB 신뢰 경계)."""
        placeholders = ", ".join(f":s{i}" for i in range(len(self._ACTIVE_STATES)))
        params = {f"s{i}": v for i, v in enumerate(self._ACTIVE_STATES)}
        params["n"] = limit
        rows = self._db.query(
            f"""SELECT worker_pool FROM data_jobs WHERE state IN ({placeholders})
                ORDER BY updated_at DESC, job_id DESC LIMIT :n""", params)
        out = []
        for r in rows:
            try:
                out.append(load_json(r["worker_pool"]))
            except (ValueError, TypeError):
                continue
        return out

    def set_phase_ref(self, job_id, phase, ref) -> bool:
        """제출한 단계의 ref 를 phase_refs 에 합친다. 반환 False = 잡 행이 없다(2026-10-08, 요청 삭제 선행 보강).

        stepper 는 잠금 없는 claim 스냅숏으로 제출하므로, 그 사이 관리자가 요청을 지우면 방금 만든 Pod/vcjob 의 ref 를
        남길 행이 없다 -- 예전엔 row["phase_refs"] 의 TypeError 로 끝나 회수 경로(_reclaim_if_terminal)에 닿지 못했고 실제
        rm/sync 가 고아로 돌았다. 호출자(stepper)는 False 면 그 ref 를 즉시 terminate 한다. PG 는 행을 FOR UPDATE 로 잠가
        요청 삭제 트랜잭션(requests → data_jobs FOR UPDATE)과 직렬화한다: 삭제가 먼저면 여기서 행이 없고, 여기가 먼저면
        삭제가 새 ref 와 새 updated_at(조용한 창)을 본다."""
        now = utc_now_iso()
        lock = " FOR UPDATE" if self._db.dialect == "postgresql" else ""
        with self._db.transaction():
            row = self._db.query_one(
                f"SELECT phase_refs FROM data_jobs WHERE job_id = :j{lock}", {"j": job_id})
            if row is None:
                return False
            refs = load_json(row["phase_refs"]) or {}
            refs[phase] = ref
            changed = self._db.execute_count(
                "UPDATE data_jobs SET phase_refs = :p, updated_at = :now WHERE job_id = :j",
                {"p": dump_json(refs), "now": now, "j": job_id})
        return changed == 1

    def touch(self, job_id, *, expected_state) -> bool:
        """LDAP 재확인 보류(stepper D2, 2026-10-07): 상태·전이는 그대로 두고 updated_at 만 now 로 민다 --
        claim_steppable(ORDER BY updated_at LIMIT n)의 큐 뒤로 가서 보류 중인 잡이 매 틱 앞자리를 차지해 다른 잡을
        굶기지 않게 한다. 조건부 UPDATE(state = expected_state)라 그 사이 취소·종단된 잡은 no-op 이다. state_transitions
        행을 남기지 않는다 -- 일어나지 않은 전이다(보류 사실은 stepper 가 events 로 남긴다). 비종단 잡의 updated_at
        소비자(metrics 수행시간·pod-gc·retention)는 종단 잡만 보므로 무해하다."""
        return self._db.execute_count(
            "UPDATE data_jobs SET updated_at = :now WHERE job_id = :j AND state = :s",
            {"now": utc_now_iso(), "j": job_id, "s": expected_state}) == 1

    def set_worker_pool(self, job_id, worker_pool: dict, *, expected_state) -> bool:
        """stepper 의 노드 CPU 재클램프(2026-10-16, stepper._clamp_to_node_cpu)가 줄인 worker_pool 을 다시 쓴다. 조건부
        UPDATE(state = expected_state)라 그 사이 취소·종단된 잡은 no-op(False). updated_at 은 건드리지 않는다
        (mark_exec_submitted 선례 -- 같은 틱의 뒤따르는 전이가 시각을 찍고, 재클램프가 클레임 순서에 끼어들 이유가 없다).
        state_transitions 행도 남기지 않는다(전이가 아니다 -- 사실은 stepper 가 events 로 남긴다)."""
        return self._db.execute_count(
            "UPDATE data_jobs SET worker_pool = :wp WHERE job_id = :j AND state = :s",
            {"wp": dump_json(worker_pool), "j": job_id, "s": expected_state}) == 1

    def mark_exec_submitted(self, job_id) -> None:
        """execution vcjob 제출 직후의 앵커 시각(슬라이스 20 설계 §2.2, 플랜 D1).
        write-once 는 SQL 술어(IS NULL)가 강제한다 -- 재제출·중복 호출이 앵커를
        뒤로 밀면 sched_wait 이 실제보다 짧게 측정된다. preview/preflight 제출
        경로는 이 메서드를 부르지 않는다(단일 컬럼에 두 vcjob 의 대기를 섞지
        않는다). updated_at 은 건드리지 않는다: 같은 틱의 뒤따르는 set_job_state
        가 어차피 같은 초의 시각을 찍고, 앵커 기록이 클레임 순서(ORDER BY
        updated_at)·GC 나이 계산에 끼어들 이유가 없다."""
        self._db.execute(
            """UPDATE data_jobs SET exec_submitted_at = :now
               WHERE job_id = :j AND exec_submitted_at IS NULL""",
            {"now": utc_now_iso(), "j": job_id})

    def archive_diag_logs(self, job_id, *, phase, entries) -> None:
        """실패 종단 진단 로그 박제(슬라이스 25 §2.2). write-once 는 SQL 술어
        (diag_logs IS NULL)가 강제한다 -- 박제 후 크래시하면 다음 틱의 finalize
        재시도가 다시 부르지만 첫 사본은 불변이다(mark_exec_submitted 선례).
        updated_at 은 건드리지 않는다: 같은 틱의 뒤따르는 set_job_state 가 어차피
        시각을 찍고, 박제가 클레임 순서(ORDER BY updated_at)·GC 나이에 끼어들
        이유가 없다(같은 선례). 상한(파드당 16KB·항목 4)은 호출자(stepper)의
        몫이다 -- 이 계층은 저장만 한다.

        entries 는 그대로 직렬화한다: 항목의 log 이 None 이면 JSON null 로 남고
        ""(빈 로그)는 빈 문자열로 남는다 -- 모름과 정상값을 뭉개지 않는다."""
        payload = dump_json({"phase": phase, "at": utc_now_iso(),
                             "entries": entries})
        self._db.execute(
            """UPDATE data_jobs SET diag_logs = :d
               WHERE job_id = :j AND diag_logs IS NULL""",
            {"d": payload, "j": job_id})

    def record_sched_wait(self, job) -> None:
        """execution vcjob 의 첫 RUNNING 관측에서 스케줄 대기를 기록(슬라이스 20
        설계 §2.3). job 은 claim_steppable 의 행 스냅샷이다(슬라이스 25 부터
        SELECT * 가 아니라 _ROW_COLUMNS_SANS_DIAG -- 여기가 읽는
        sched_wait_seconds/exec_submitted_at 은 그 목록에 있다) --
        sched_wait_seconds 선독으로 이미 기록된 잡은 UPDATE 자체를 건너뛴다(매 틱
        0행 UPDATE 반복 방지). 진짜 write-once 강제는 SQL 술어(IS NULL)다: 스냅샷이
        낡아 선독을 통과해도 덮어쓰지 못한다. set_job_state 의 UPDATE 에 끼우지
        않는 이유(설계 §2.3): 그 UPDATE 는 항상 submit_wait_seconds 를 쓰므로 같은
        자리에 넣으면 매 전이 선독-보존을 지켜야 하고 한 곳만 놓쳐도 NULL 로
        덮인다.

        0 은 정상값(같은 틱 안에 스케줄됨 -- 설계 §2.4)이라 비교는 전부 is None
        이다(truthy 금지). 음수(시계 스큐)는 submit_wait 규칙 그대로 0 으로 접고
        (1초 해상도 세계에서 0 이 정직하다), 시각이 깨졌으면(iso_epoch ValueError)
        지어내지 않고 NULL 로 남긴다 -- 둘 다 set_job_state 의 강등 선례와 같은
        규칙(설계 §4). 앵커가 없는 잡(마이그레이션 이전)은 NULL 유지 -- excluded
        로 집계에 표면화된다(설계 §2.5)."""
        if job.get("sched_wait_seconds") is not None:
            return
        anchor = job.get("exec_submitted_at")
        if anchor is None:
            return
        try:
            wait = max(0, int(iso_epoch(utc_now_iso()) - iso_epoch(anchor)))
        except (TypeError, ValueError):
            return
        self._db.execute(
            """UPDATE data_jobs SET sched_wait_seconds = :w
               WHERE job_id = :j AND sched_wait_seconds IS NULL""",
            {"w": wait, "j": job["job_id"]})

    def set_preview(self, job_id, *, fingerprint, expires_at, artifact_uri, summary=None):
        # summary = 미리보기 summary.json 사본(2026-09-17). 지문이 이 객체의 해시라
        # 둘을 같은 UPDATE 로 남긴다 -- 따로 쓰면 크래시 창에서 지문만 있고 요약이
        # 없는 행이 생긴다. None 은 "모름"(구 호출자·읽기 실패)이라 NULL 그대로.
        self._db.execute(
            """UPDATE data_jobs SET preview_fingerprint = :f, preview_expires_at = :e,
                   preview_summary = :s,
                   artifact_uri = COALESCE(:a, artifact_uri), updated_at = :now
               WHERE job_id = :j""",
            {"f": fingerprint, "e": expires_at, "a": artifact_uri,
             "s": dump_json(summary) if summary is not None else None,
             "now": utc_now_iso(), "j": job_id})

    def set_confirmed(self, job_id, fingerprint):
        self._db.execute(
            "UPDATE data_jobs SET confirmed_fingerprint = :f, updated_at = :now WHERE job_id = :j",
            {"f": fingerprint, "now": utc_now_iso(), "j": job_id})

    def set_artifact(self, job_id, *, artifact_uri, result_summary):
        # files/bytes 승격(설계 §2.3): summary에 키가 있으면 typed 컬럼에 채운다.
        # 슬라이스 15부터 runner가 mpifileutils 출력을 파싱해 이 키를 실제로 채운다.
        # files는 "항목(items)" 의미로 통일했고(dsync/nsync Items, drm Removed N
        # items, dscan summary.total_entries). bytes 의 의미는 도구별로 다르다
        # (2026-08-23): sync 는 "옮긴 바이트", scan 은 "관측한 트리의 총 용량"
        # (실 사용량 -- 시간 히스토그램 합, parsers.parse_scan_counts), rm 은
        # 도구가 미보고라 NULL 이다. 그래서 처리량 지표(metrics job_stats)는
        # scan 을 SUM 에서 제외한다 -- 파싱 실패는 여전히 fail-soft NULL.
        files_count = bytes_count = None
        if isinstance(result_summary, dict):
            files_count = _as_count(result_summary.get("files"))
            bytes_count = _as_count(result_summary.get("bytes"))
        self._db.execute(
            """UPDATE data_jobs SET artifact_uri = COALESCE(:a, artifact_uri),
                   result_summary = :s, files_count = :fc, bytes_count = :bc,
                   updated_at = :now WHERE job_id = :j""",
            {"a": artifact_uri,
             "s": dump_json(result_summary) if result_summary is not None else None,
             "fc": files_count, "bc": bytes_count,
             "now": utc_now_iso(), "j": job_id})

    def list_confirmable(self, job_id):
        return self.get_job(job_id)

    def expire_previews(self, *, now_iso):
        rows = self._db.query(
            """SELECT job_id FROM data_jobs
               WHERE state = :s AND preview_expires_at IS NOT NULL
                 AND preview_expires_at < :now""",
            {"s": DataJobState.CONFIRM_PENDING.value, "now": now_iso})
        expired = []
        for row in rows:
            self.set_job_state(row["job_id"], DataJobState.PREVIEW_EXPIRED,
                               reason_code="preview_expired", actor="stepper")
            expired.append(row["job_id"])
        return expired

    def terminal_jobs_older_than(self, after_seconds: int, *, limit: int = 200,
                                  now_iso: str | None = None, vcjob_after_seconds: int | None = None) -> list[dict]:
        """종단 상태이고 updated_at이 (now - after_seconds)보다 오래된 잡을
        **오래된 순**으로 최대 limit건. GC는 반드시 오래된 것부터 비워야 한다 —
        대상이 limit을 넘으면(예: 컨트롤러가 오래 멈췄다 돌아온 경우) 최신순으로
        자르면 가장 오래된, 가장 급한 잡이 윈도우 밖으로 영영 밀려나 파드가
        누수된다. updated_at은 이 잡에 대해 **단조 비감소**다(뒤로 가지 않는다) —
        set_phase_ref/set_preview/set_confirmed/set_artifact는 종단 잡에도 가드 없이
        updated_at을 찍을 수 있지만, 모든 갱신이 시각을 **앞으로만** 미므로 이
        나이 필터는 GC를 늦출 뿐 일찍 발동시키지는 않는다. Pod GC(preflight pod
        정리)가 진행 중인 잡을 절대 건드리지 않도록 state 필터를 두는 게 이
        메서드의 핵심.

        **이미 치운 잡은 빠진다**(pods_gc_at IS NULL, 2026-10-16): 예전엔 표시가 없어 매 틱 같은 앞쪽 묶음만 다시 지웠다
        (테스트베드 8일 된 preflight Pod 82개). vcjob_after_seconds(None = 기다리지 않음): Succeeded 가 아닌 잡 중 vcjob/
        참조가 있는 것은 이만큼 지나야 나온다 -- pod_gc 가 그 vcjob 도 지우므로 운영자가 늘린 Volcano TTL(로그 보존)보다
        먼저 지우지 않게(pod_gc 모듈 docstring)."""
        now = now_iso or utc_now_iso()
        threshold = iso_plus(now, -after_seconds)
        states = tuple(s.value for s in TERMINAL_DATA_JOB_STATES)
        placeholders = ", ".join(f":s{i}" for i in range(len(states)))
        params = {f"s{i}": v for i, v in enumerate(states)}
        params["threshold"] = threshold
        params["n"] = limit
        tail = ""
        if vcjob_after_seconds is not None and vcjob_after_seconds > after_seconds:
            tail = (" AND (state = :ok OR phase_refs IS NULL OR phase_refs NOT LIKE :vc"
                    " OR updated_at < :vthreshold)")
            params.update(ok=DataJobState.SUCCEEDED.value, vc='%"vcjob/%',
                          vthreshold=iso_plus(now, -vcjob_after_seconds))
        rows = self._db.query(
            f"""SELECT {_ROW_COLUMNS_SANS_DIAG} FROM data_jobs
                WHERE pods_gc_at IS NULL AND state IN ({placeholders}) AND updated_at < :threshold{tail}
                ORDER BY updated_at ASC, job_id ASC LIMIT :n""", params)
        return [self._hydrate(r) for r in rows]

    def mark_pods_gc(self, job_id, *, now_iso: str | None = None) -> None:
        """pod_gc 가 이 잡의 파드·vcjob 정리를 마쳤다(다시 보지 않는다). updated_at 은 건드리지 않는다 -- 그 축은 다른 집계·
        GC 창이 쓴다."""
        self._db.execute("UPDATE data_jobs SET pods_gc_at = :t WHERE job_id = :j",
                         {"t": now_iso or utc_now_iso(), "j": job_id})

    def terminal_jobs_with_live_request(self, *, limit: int = 200):
        """잡은 터미널인데 그 request가 아직 비터미널인 (job_id, request_id, state)
        목록. 컨트롤러 크래시 등으로 finalize_from_job이 누락된 고아 복구용.

        슬라이스 24 §2.3: 오래된순 + LIMIT 200 -- 같은 파일
        terminal_jobs_older_than 의 선례 미러다. 무제한 전량 JOIN 은 preview 만료
        직후 크래시(expire_previews 가 한 호출로 N 건을 종단시키고 finalize 는
        행별 후속) 같은 대량 고아에서 단일 스레드 컨트롤러의 한 틱을 통째로
        먹는다. finalize 가 멱등이고 처리된 행은 술어(request 비종단)에서 빠지므로
        틱마다 200건씩 반드시 전진한다 -- 오래된순이라 잘려도 가장 급한 행이
        먼저다."""
        job_terminal = tuple(s.value for s in TERMINAL_DATA_JOB_STATES)
        req_terminal = tuple(s.value for s in TERMINAL_REQUEST_STATES)
        jt = ", ".join(f":jt{i}" for i in range(len(job_terminal)))
        rt = ", ".join(f":rt{i}" for i in range(len(req_terminal)))
        params = {f"jt{i}": v for i, v in enumerate(job_terminal)}
        params.update({f"rt{i}": v for i, v in enumerate(req_terminal)})
        params["n"] = limit
        return self._db.query(
            f"""SELECT d.job_id, d.request_id, d.state FROM data_jobs d
                JOIN requests r ON r.request_id = d.request_id
                WHERE d.state IN ({jt}) AND r.state NOT IN ({rt})
                ORDER BY d.updated_at ASC, d.job_id ASC LIMIT :n""", params)
