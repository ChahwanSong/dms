"""data_jobs + plans 저장소: planner가 emit하고 stepper(3b)가 전진시키는 잡 레코드."""
import uuid
from ..db import Database, dump_json, iso_plus, load_json, utc_now_iso
from ..domain import (DataJobState, RequestState, TERMINAL_DATA_JOB_STATES,
                      TERMINAL_REQUEST_STATES)
from .observability import ObservabilityRepository

_JSON_COLUMNS = ("options", "worker_pool", "precondition", "result_summary",
                 "volcano_job_ref", "phase_refs")

# set_job_state는 stepper.py(actor="stepper")와 batch_orchestrator.py
# (actor="batch-orchestrator") 말고도 API 라우트(actor=identity.actor, 즉 실제
# 사용자명)에서도 불린다. 종단 가드 이벤트의 component에 사용자명을 그대로 쓰면
# 사용자 수만큼 값이 늘어나(고카디널리티) 슬라이스 14 대시보드가 component를
# 그룹핑 키로 못 쓴다(설계 §5). stepper/batch-orchestrator는 고정된 소수 리터럴이라
# 그대로 어휘에 넣고, 그 외(=API 호출자)는 전부 "api"로 접는다 -- 실제 발신자는
# message에 남기므로 정보 손실은 없다.
_KNOWN_COMPONENTS = frozenset({"stepper", "batch-orchestrator"})


def _guard_component(actor: str) -> str:
    return actor if actor in _KNOWN_COMPONENTS else "api"


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

    def create_plan(self, request_id, *, actor) -> str:
        plan_id = uuid.uuid4().hex
        now = utc_now_iso()
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO plans (plan_id, request_id, job_id, state,
                       created_at, updated_at)
                   VALUES (:p, :r, NULL, 'Planned', :now, :now)""",
                {"p": plan_id, "r": request_id, "now": now})
            self._record_transition("plan", plan_id, None,
                                    RequestState.PLANNED, None, actor, now)
        return plan_id

    def create_job(self, request_id, plan_id, *, operation, priority,
                   storage_name=None, source_storage=None, destination_storage=None,
                   source=None, destination=None, target=None, options: dict, tool,
                   worker_pool: dict, precondition: dict, actor) -> str:
        job_id = uuid.uuid4().hex
        now = utc_now_iso()
        with self._db.transaction():
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
            """SELECT * FROM data_jobs
               WHERE operation = 'scan' AND state = :s AND storage_name = :sn
               ORDER BY updated_at DESC, job_id DESC LIMIT :n""",
            {"s": DataJobState.SUCCEEDED.value, "sn": storage_name, "n": limit})
        return [self._hydrate(r) for r in rows]

    def list_jobs(self, *, request_id=None, limit=50):
        if request_id is None:
            rows = self._db.query(
                "SELECT * FROM data_jobs ORDER BY created_at DESC, job_id DESC LIMIT :n",
                {"n": limit})
        else:
            rows = self._db.query(
                """SELECT * FROM data_jobs WHERE request_id = :r
                   ORDER BY created_at DESC, job_id DESC LIMIT :n""",
                {"r": request_id, "n": limit})
        return [self._hydrate(r) for r in rows]

    def set_job_state(self, job_id, to_state: DataJobState, *, reason_code=None, actor):
        now = utc_now_iso()
        guard_tripped = False
        with self._db.transaction():
            current = self._db.query_one(
                "SELECT state, request_id FROM data_jobs WHERE job_id = :j", {"j": job_id})
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
                self._db.execute(
                    """UPDATE data_jobs SET state = :s, reason_code = :rc, updated_at = :now
                       WHERE job_id = :j""",
                    {"s": to_state.value, "rc": reason_code, "now": now, "j": job_id})
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
            f"""SELECT * FROM data_jobs WHERE state IN ({placeholders})
                ORDER BY updated_at LIMIT :n{suffix}""", params)
        return [self._hydrate(r) for r in rows]

    def set_phase_ref(self, job_id, phase, ref):
        now = utc_now_iso()
        with self._db.transaction():
            row = self._db.query_one(
                "SELECT phase_refs FROM data_jobs WHERE job_id = :j", {"j": job_id})
            refs = load_json(row["phase_refs"]) or {}
            refs[phase] = ref
            self._db.execute(
                "UPDATE data_jobs SET phase_refs = :p, updated_at = :now WHERE job_id = :j",
                {"p": dump_json(refs), "now": now, "j": job_id})

    def set_preview(self, job_id, *, fingerprint, expires_at, artifact_uri):
        self._db.execute(
            """UPDATE data_jobs SET preview_fingerprint = :f, preview_expires_at = :e,
                   artifact_uri = COALESCE(:a, artifact_uri), updated_at = :now
               WHERE job_id = :j""",
            {"f": fingerprint, "e": expires_at, "a": artifact_uri,
             "now": utc_now_iso(), "j": job_id})

    def set_confirmed(self, job_id, fingerprint):
        self._db.execute(
            "UPDATE data_jobs SET confirmed_fingerprint = :f, updated_at = :now WHERE job_id = :j",
            {"f": fingerprint, "now": utc_now_iso(), "j": job_id})

    def set_artifact(self, job_id, *, artifact_uri, result_summary):
        self._db.execute(
            """UPDATE data_jobs SET artifact_uri = COALESCE(:a, artifact_uri),
                   result_summary = :s, updated_at = :now WHERE job_id = :j""",
            {"a": artifact_uri,
             "s": dump_json(result_summary) if result_summary is not None else None,
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
                                  now_iso: str | None = None) -> list[dict]:
        """종단 상태이고 updated_at이 (now - after_seconds)보다 오래된 잡을
        **오래된 순**으로 최대 limit건. GC는 반드시 오래된 것부터 비워야 한다 —
        대상이 limit을 넘으면(예: 컨트롤러가 오래 멈췄다 돌아온 경우) 최신순으로
        자르면 가장 오래된, 가장 급한 잡이 윈도우 밖으로 영영 밀려나 파드가
        누수된다. updated_at은 이 잡에 대해 **단조 비감소**다(뒤로 가지 않는다) —
        set_phase_ref/set_preview/set_confirmed/set_artifact는 종단 잡에도 가드 없이
        updated_at을 찍을 수 있지만, 모든 갱신이 시각을 **앞으로만** 미므로 이
        나이 필터는 GC를 늦출 뿐 일찍 발동시키지는 않는다. Pod GC(preflight pod
        정리)가 진행 중인 잡을 절대 건드리지 않도록 state 필터를 두는 게 이
        메서드의 핵심."""
        threshold = iso_plus(now_iso or utc_now_iso(), -after_seconds)
        states = tuple(s.value for s in TERMINAL_DATA_JOB_STATES)
        placeholders = ", ".join(f":s{i}" for i in range(len(states)))
        params = {f"s{i}": v for i, v in enumerate(states)}
        params["threshold"] = threshold
        params["n"] = limit
        rows = self._db.query(
            f"""SELECT * FROM data_jobs WHERE state IN ({placeholders})
                   AND updated_at < :threshold
                ORDER BY updated_at ASC, job_id ASC LIMIT :n""", params)
        return [self._hydrate(r) for r in rows]

    def terminal_jobs_with_live_request(self):
        """잡은 터미널인데 그 request가 아직 비터미널인 (job_id, request_id, state) 목록.
        컨트롤러 크래시 등으로 finalize_from_job이 누락된 고아를 찾아 복구하기 위함."""
        job_terminal = tuple(s.value for s in TERMINAL_DATA_JOB_STATES)
        req_terminal = tuple(s.value for s in TERMINAL_REQUEST_STATES)
        jt = ", ".join(f":jt{i}" for i in range(len(job_terminal)))
        rt = ", ".join(f":rt{i}" for i in range(len(req_terminal)))
        params = {f"jt{i}": v for i, v in enumerate(job_terminal)}
        params.update({f"rt{i}": v for i, v in enumerate(req_terminal)})
        return self._db.query(
            f"""SELECT d.job_id, d.request_id, d.state FROM data_jobs d
                JOIN requests r ON r.request_id = d.request_id
                WHERE d.state IN ({jt}) AND r.state NOT IN ({rt})""", params)
