"""data_jobs + plans 저장소: planner가 emit하고 stepper(3b)가 전진시키는 잡 레코드."""
import uuid
from ..db import Database, dump_json, load_json, utc_now_iso
from ..domain import DataJobState, RequestState

_JSON_COLUMNS = ("options", "worker_pool", "precondition", "result_summary",
                 "volcano_job_ref")


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
        with self._db.transaction():
            current = self._db.query_one(
                "SELECT state FROM data_jobs WHERE job_id = :j", {"j": job_id})
            if current is None:
                raise KeyError(job_id)
            self._db.execute(
                """UPDATE data_jobs SET state = :s, reason_code = :rc, updated_at = :now
                   WHERE job_id = :j""",
                {"s": to_state.value, "rc": reason_code, "now": now, "j": job_id})
            self._record_transition("data_job", job_id, DataJobState(current["state"]),
                                    to_state, reason_code, actor, now)

    def job_transitions(self, job_id):
        return self._db.query(
            """SELECT * FROM state_transitions
               WHERE entity_kind = 'data_job' AND entity_id = :j ORDER BY id""",
            {"j": job_id})
