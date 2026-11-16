"""배치 스케줄 저장소(2026-11-16, 배치 스케줄러 -- 규칙·판정은 dms.batch_scheduler 모듈 docstring).

쓰기는 전부 업무 행과 감사 행(audit_log)을 한 트랜잭션에 남긴다:
  - 사람의 생성·수정·켜기·끄기·삭제: mutation_class 'batch_schedule'(operation create|update|enable|disable|delete),
    target_key = batch_id(배치당 하나라 배치로 찾는다 -- 스케줄을 지우고 다시 만들면 schedule_id 가 바뀐다), before/after 에
    schedule_id 와 규칙. 파괴적 스케줄이면 after 에 acknowledged_irreversible: true + 지문.
  - 회차 발사: ('batch', 'scheduled_rescan', batch_id), actor batch_scheduler.SCHEDULER_ACTOR -- 사람의 :rescan 은 감사 행이
    없지만 스케줄이 시작한 root 실행은 자식 요청의 actor(배치 생성자)만으로는 생성자가 누른 것과 구별되지 않는다. 자동
    일시정지도 ('batch_schedule', 'auto_pause').

발사·기록(fire·record)은 **스케줄 CAS** 를 거친다: `WHERE schedule_id AND enabled = 1 AND next_run_at = 읽은 값 AND
updated_at = 읽은 값` -- 컨트롤러가 둘(롤링 재시작·긴 틱으로 리스가 겹침)이어도 같은 슬롯은 한쪽만 쏘고, 진 쪽은 아무것도
남기지 않는다(fire 는 리셋까지 통째 롤백). 그 사이 사람이 끄거나 고쳤으면(updated_at) 역시 0 행이다.

잠금 순서는 항목 → 배치 → 스케줄(PG FOR UPDATE): 발사·생성·수정·켜기가 BatchesRepository._lock_items_then_batch 로 항목·
배치를 먼저 쥐고 스케줄을 마지막에 쥔다 -- 배치 삭제 두 경로(BatchesRepository.delete, RequestPurgesRepository.delete_batch)와
같은 방향이라 교착이 없다. 끄기·삭제·기록은 스케줄 행만 쥔다. 트랜잭션은 중첩되지 않으므로(db.transaction) 배치 저장소의
「호출자 트랜잭션 안에서만」 몸통(_lock_items_then_batch·_rescan_locked)을 부른다.
"""
import json
import uuid

from ..db import Database, dump_json, utc_now_iso
from ..batch_scheduler import SCHEDULER_ACTOR, next_run_after
from ..domain import DomainValidationError, confirm_gated
# 「대상 스토리지」 판정은 배치 목록 열(B1)과 한 함수다(첫 항목 payload -- 깨진 JSON·dict 아님·문자열 아님은 None).
from .batches import BatchesRepository, _storage_fields

# 스케줄당 남기는 회차 이력 수. 확인 대기 중 매시간 건너뛰는 스케줄도 이력이 끝없이 자라지 않는다.
RUN_HISTORY_CAP = 200


def _load_json_or_none(text):
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _hydrate(row):
    # spec 은 dict 또는 None(깨진 JSON -- 판정이 invalid_batch_schedule 로 일시정지한다). enabled 는 1 만 켜짐(due 질의의
    # enabled = 1 과 같은 규칙 -- 변조 값은 꺼짐으로 읽는다).
    spec = _load_json_or_none(row.get("spec"))
    row["spec"] = spec if isinstance(spec, dict) else None
    row["enabled"] = row.get("enabled") == 1
    return row


def _audit_state(row, **extra) -> dict:
    return {"schedule_id": row["schedule_id"], "batch_id": row["batch_id"], "kind": row["kind"],
            "spec": row["spec"], "enabled": row["enabled"], "next_run_at": row["next_run_at"],
            "armed_by": row["armed_by"], **extra}


def _ack_extra(ack_fingerprint) -> dict:
    # 파괴적 스케줄을 켠 사람이 「되돌릴 수 없음」에 동의했다는 사실은 이 감사 행이 유일한 자리다(자동 확인의 auto_confirm_on
    # 과 같은 결) -- 지문은 회차가 비교하는 값 그대로.
    if ack_fingerprint is None:
        return {}
    return {"acknowledged_irreversible": True, "fingerprint": ack_fingerprint}


class _Lost(Exception):
    """발사 트랜잭션의 CAS·재확인 패배 -- 리셋까지 롤백시키려고 던진다(밖으로 새지 않는다)."""


class BatchSchedulesRepository:
    def __init__(self, db: Database):
        self._db = db
        self._batches = BatchesRepository(db)

    # --- 읽기 ---------------------------------------------------------------------------------------------------------

    def get(self, schedule_id):
        row = self._db.query_one("SELECT * FROM batch_schedules WHERE schedule_id = :s", {"s": schedule_id})
        return None if row is None else _hydrate(row)

    def get_by_batch(self, batch_id):
        row = self._db.query_one("SELECT * FROM batch_schedules WHERE batch_id = :b", {"b": batch_id})
        return None if row is None else _hydrate(row)

    def list(self, schedule_id=None) -> "list[dict]":
        """스케줄 행 + 배치 요약(batch: {batch_id, name, operation, status, item_count, auto_confirm, options, storage,
        source_storage, destination_storage} | None -- 배치 행이 없으면 None). 첫 항목은 MIN(seq) 하나(삭제로 seq 에 구멍이
        있어도 -- ORDER BY seq LIMIT 1). 순서: 만든 순 최신 먼저."""
        where, params = ("WHERE s.schedule_id = :s", {"s": schedule_id}) if schedule_id is not None else ("", {})
        rows = self._db.query(
            f"""SELECT s.*, b.batch_id AS b_batch_id, b.name AS b_name, b.operation AS b_operation,
                       b.status AS b_status, b.item_count AS b_item_count, b.auto_confirm_by AS b_auto_confirm_by,
                       b.options AS b_options,
                       (SELECT bi.payload FROM batch_items bi WHERE bi.batch_id = s.batch_id
                         ORDER BY bi.seq LIMIT 1) AS b_first_payload
                  FROM batch_schedules s LEFT JOIN batches b ON b.batch_id = s.batch_id
                  {where}
                 ORDER BY s.created_at DESC, s.schedule_id""", params)
        out = []
        for r in rows:
            b = {k[2:]: r.pop(k) for k in list(r) if k.startswith("b_")}
            row = _hydrate(r)
            if b["batch_id"] is None:
                row["batch"] = None
            else:
                ic = b["item_count"]
                options = _load_json_or_none(b["options"])
                name = b["name"]
                row["batch"] = {
                    "batch_id": b["batch_id"],
                    "name": name if isinstance(name, str) and name.strip() != "" else None,
                    "operation": b["operation"], "status": b["status"],
                    # bool 은 int 의 하위형 -- 모르는 값은 None, 0 은 정상값(list_summaries 와 같은 규칙).
                    "item_count": ic if isinstance(ic, int) and not isinstance(ic, bool) and ic >= 0 else None,
                    "auto_confirm": b["auto_confirm_by"] is not None,
                    "options": options if isinstance(options, dict) else None,
                    **_storage_fields(b["first_payload"]),
                }
            out.append(row)
        return out

    def summaries_for(self, batch_ids) -> dict:
        """배치 목록·상세 머리(view=summary) 응답의 schedule 필드(routes_batches._with_schedules) -- {batch_id: {schedule_id,
        kind, spec, enabled, next_run_at, last_run_at, last_outcome, last_reason_code, paused_reason_code}}. 스케줄이 없는 배치는
        키가 없다(호출자가 None 으로 읽는다). 목록 한 쪽이 IN 질의 한 번(500개씩 끊는다 -- 목록은 100건이라 보통 한 번)."""
        ids = [b for b in dict.fromkeys(batch_ids) if isinstance(b, str)]
        out: dict = {}
        for i in range(0, len(ids), 500):
            names = {f"i{n}": v for n, v in enumerate(ids[i:i + 500])}
            in_list = ", ".join(":" + k for k in names)
            for r in self._db.query(
                    f"""SELECT schedule_id, batch_id, kind, spec, enabled, next_run_at, last_run_at, last_outcome,
                               last_reason_code, paused_reason_code FROM batch_schedules WHERE batch_id IN ({in_list})""",
                    names):
                row = _hydrate(r)
                out[row.pop("batch_id")] = row
        return out

    def runs(self, schedule_id, *, limit: int) -> "list[dict]":
        return self._db.query(
            "SELECT * FROM batch_schedule_runs WHERE schedule_id = :s ORDER BY run_id DESC LIMIT :n",
            {"s": schedule_id, "n": int(limit)})

    def due(self, now, *, limit: int) -> "list[dict]":
        """지금 판정할 스케줄(켜짐 ∧ 예정 시각 <= now), 예정 시각 순 limit 건 -- (enabled, next_run_at) 인덱스."""
        rows = self._db.query(
            """SELECT * FROM batch_schedules WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= :now
                ORDER BY next_run_at, schedule_id LIMIT :n""", {"now": now, "n": int(limit)})
        return [_hydrate(r) for r in rows]

    # --- 사람의 쓰기(라우트) ----------------------------------------------------------------------------------------------
    # arm(batch, items) -> (ack_fingerprint | None, storage_binding): 잠근 배치 행·항목으로 「이 배치를 스케줄로 돌려도 되나」를
    # 판정하는 콜백(routes_batch_schedules._arm -- batch_scheduler.arm_problem·파괴적 동의). 거부는 DomainValidationError 로
    # 던진다(트랜잭션째 롤백, 라우트가 422). 반환값 = (파괴적 스케줄의 동의 지문 | None, 연산 무관 스토리지 경로 바인딩).

    def _lock_schedule(self, schedule_id):
        lock = " FOR UPDATE" if self._db.dialect == "postgresql" else ""
        row = self._db.query_one(f"SELECT * FROM batch_schedules WHERE schedule_id = :s{lock}", {"s": schedule_id})
        return None if row is None else _hydrate(row)

    def _lock_for_arm(self, schedule_id):
        """수정·켜기의 첫 문장들: 스케줄을 (잠그지 않고) 읽어 batch_id 를 얻고 → 항목 → 배치 → 스케줄 순으로 잠근다(모듈
        docstring 의 잠금 순서). (스케줄, 배치, 항목) -- 스케줄이 없으면 DomainValidationError."""
        peek = self._db.query_one("SELECT batch_id FROM batch_schedules WHERE schedule_id = :s", {"s": schedule_id})
        if peek is None:
            raise DomainValidationError("batch_schedule_not_found", schedule_id)
        batch, items = self._batches._lock_items_then_batch(peek["batch_id"])
        row = self._lock_schedule(schedule_id)
        if row is None:
            raise DomainValidationError("batch_schedule_not_found", schedule_id)
        return row, batch, items

    def _audit(self, operation, row, *, actor, before, after, now, mutation_class="batch_schedule"):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor, before_state, after_state, at)
               VALUES (:mc, :op, :key, :actor, :b, :a, :at)""",
            {"mc": mutation_class, "op": operation, "key": row["batch_id"], "actor": actor,
             "b": None if before is None else dump_json(before),
             "a": None if after is None else dump_json(after), "at": now})

    def create(self, *, batch_id, kind, spec, enabled: bool, next_run_at, actor, arm) -> str:
        """스케줄 생성(배치당 하나). next_run_at = 라우트가 계산한 지금 뒤 첫 슬롯(꺼짐으로 만들면 None). 거부:
        batch_not_found(배치 없음) · batch_schedule_exists(이미 있음) · arm 의 사유."""
        sid = uuid.uuid4().hex
        now = utc_now_iso()
        with self._db.transaction():
            batch, items = self._batches._lock_items_then_batch(batch_id)
            if batch is None:
                raise DomainValidationError("batch_not_found", batch_id)
            # 배치 행 잠금이 직렬화 지점이다 -- 같은 배치의 두 생성은 여기서 줄을 서고 뒤쪽이 앞쪽의 행을 본다(UNIQUE 가 백스톱).
            if self._db.query_one("SELECT 1 AS x FROM batch_schedules WHERE batch_id = :b", {"b": batch_id}):
                raise DomainValidationError("batch_schedule_exists", batch_id)
            ack, binding = arm(batch, items)
            self._db.execute(
                """INSERT INTO batch_schedules (schedule_id, batch_id, kind, spec, enabled, next_run_at, armed_by,
                       ack_fingerprint, storage_binding, created_by, created_at, updated_at)
                   VALUES (:s, :b, :k, :spec, :en, :next, :actor, :ack, :bind, :actor, :now, :now)""",
                {"s": sid, "b": batch_id, "k": kind, "spec": dump_json(spec), "en": 1 if enabled else 0,
                 "next": next_run_at if enabled else None, "actor": actor, "ack": ack, "bind": binding, "now": now})
            row = self._lock_schedule(sid)
            self._audit("create", row, actor=actor, before=None, after=_audit_state(row, **_ack_extra(ack)), now=now)
        return sid

    def update_rule(self, schedule_id, *, kind, spec, next_run_at, actor, arm) -> None:
        """규칙(kind/spec) 변경 = 다시 무장: armed_by = 편집자, 파괴적이면 새 동의·지문. 켜져 있으면 next_run_at(지금 뒤 첫
        슬롯)으로 다시 잡고, 꺼져(일시정지 포함) 있으면 꺼진 채 둔다(켜기는 :enable 몫)."""
        now = utc_now_iso()
        with self._db.transaction():
            row, batch, items = self._lock_for_arm(schedule_id)
            if batch is None:
                raise DomainValidationError("batch_not_found", row["batch_id"])
            ack, binding = arm(batch, items)
            self._db.execute(
                """UPDATE batch_schedules SET kind = :k, spec = :spec, next_run_at = :next, armed_by = :actor,
                       ack_fingerprint = :ack, storage_binding = :bind, updated_at = :now WHERE schedule_id = :s""",
                {"k": kind, "spec": dump_json(spec), "next": next_run_at if row["enabled"] else None,
                 "actor": actor, "ack": ack, "bind": binding, "now": now, "s": schedule_id})
            after = self._lock_schedule(schedule_id)
            self._audit("update", row, actor=actor, before=_audit_state(row),
                        after=_audit_state(after, **_ack_extra(ack)), now=now)

    def enable(self, schedule_id, *, actor, arm) -> bool:
        """켜기(사람 끄기·자동 일시정지에서) = 다시 무장(armed_by = 켠 사람, 파괴적이면 새 동의 + 지문 갱신), 다음 실행 = 지금
        뒤 첫 슬롯(바로 쏘지 않는다), 일시정지 사유를 지운다. 이미 켜져 있으면 False(아무것도 쓰지 않는다 -- 멱등).
        다음 실행은 **잠근 행의 규칙**으로 여기서 계산한다(라우트가 트랜잭션 밖에서 읽은 규칙으로 계산하면, 그 사이 커밋된
        규칙 수정(PATCH -- 꺼진 스케줄은 꺼진 채 규칙만 바뀐다) 뒤에 옛 규칙의 시각이 새 규칙과 함께 저장돼 규칙에 없는 시각에
        한 번 돈다). 깨진 규칙(DB 변조)은 invalid_batch_schedule -- 고치는 길은 규칙 수정이다."""
        now = utc_now_iso()
        with self._db.transaction():
            row, batch, items = self._lock_for_arm(schedule_id)
            if row["enabled"]:
                return False
            if batch is None:
                raise DomainValidationError("batch_not_found", row["batch_id"])
            next_run_at = next_run_after(row["kind"], row["spec"], now)
            ack, binding = arm(batch, items)
            self._db.execute(
                """UPDATE batch_schedules SET enabled = 1, next_run_at = :next, armed_by = :actor, ack_fingerprint = :ack,
                       storage_binding = :bind, paused_reason_code = NULL, updated_at = :now WHERE schedule_id = :s""",
                {"next": next_run_at, "actor": actor, "ack": ack, "bind": binding, "now": now, "s": schedule_id})
            after = self._lock_schedule(schedule_id)
            self._audit("enable", row, actor=actor, before=_audit_state(row, paused_reason_code=row["paused_reason_code"]),
                        after=_audit_state(after, **_ack_extra(ack)), now=now)
        return True

    def disable(self, schedule_id, *, actor) -> bool:
        """사람 끄기 -- 어느 때나(실행을 줄이기만 한다). 일시정지 사유는 비운다(사람이 끈 것). 이미 꺼져 있으면 False(자동
        일시정지의 사유를 사람의 끄기로 지우지 않는다 -- 증거)."""
        now = utc_now_iso()
        with self._db.transaction():
            row = self._lock_schedule(schedule_id)
            if row is None:
                raise DomainValidationError("batch_schedule_not_found", schedule_id)
            if not row["enabled"]:
                return False
            self._db.execute(
                """UPDATE batch_schedules SET enabled = 0, next_run_at = NULL, paused_reason_code = NULL, updated_at = :now
                    WHERE schedule_id = :s""", {"now": now, "s": schedule_id})
            after = self._lock_schedule(schedule_id)
            self._audit("disable", row, actor=actor, before=_audit_state(row), after=_audit_state(after), now=now)
        return True

    def delete(self, schedule_id, *, actor) -> None:
        now = utc_now_iso()
        with self._db.transaction():
            row = self._lock_schedule(schedule_id)
            if row is None:
                raise DomainValidationError("batch_schedule_not_found", schedule_id)
            self._db.execute("DELETE FROM batch_schedule_runs WHERE schedule_id = :s", {"s": schedule_id})
            self._db.execute("DELETE FROM batch_schedules WHERE schedule_id = :s", {"s": schedule_id})
            self._audit("delete", row, actor=actor, before=_audit_state(row), after=None, now=now)

    # --- 회차(컨트롤러) ---------------------------------------------------------------------------------------------------

    _CAS = "schedule_id = :s AND enabled = 1 AND next_run_at = :expected AND updated_at = :upd"

    def _insert_run(self, schedule, *, outcome, reason_code, now, batch_status, requeued=None, prev=None):
        prev = prev or {}
        self._db.execute(
            """INSERT INTO batch_schedule_runs (schedule_id, batch_id, scheduled_for, at, outcome, reason_code,
                   batch_status, requeued, prev_status, prev_item_count, prev_succeeded, prev_failed)
               VALUES (:s, :b, :sf, :at, :o, :rc, :bs, :rq, :ps, :pic, :psu, :pf)""",
            {"s": schedule["schedule_id"], "b": schedule["batch_id"], "sf": schedule["next_run_at"], "at": now,
             "o": outcome, "rc": reason_code, "bs": batch_status, "rq": requeued,
             "ps": prev.get("status"), "pic": prev.get("item_count"), "psu": prev.get("succeeded_count"),
             "pf": prev.get("failed_count")})
        # 최근 RUN_HISTORY_CAP 건만 -- 그보다 오래된 행을 같은 트랜잭션에서 지운다. 서브쿼리가 NULL(행이 상한 이하)이면
        # `run_id <= NULL` 은 거짓이라 아무것도 지우지 않는다(두 방언 같다).
        self._db.execute(
            """DELETE FROM batch_schedule_runs WHERE schedule_id = :s AND run_id <=
                   (SELECT run_id FROM batch_schedule_runs WHERE schedule_id = :s ORDER BY run_id DESC
                     LIMIT 1 OFFSET :cap)""", {"s": schedule["schedule_id"], "cap": RUN_HISTORY_CAP})

    def record(self, schedule, *, outcome, reason_code, next_run_at, now, batch_status) -> bool:
        """쏘지 않은 회차(skipped·missed·paused)를 남긴다 -- 스케줄 CAS 가 첫 쓰기라 지면(False) 아무것도 쓰지 않았다.
        paused = enabled 0 + next_run_at NULL + paused_reason_code(+ 감사 행 auto_pause). 그 밖은 next_run_at 으로 넘긴다."""
        params = {"s": schedule["schedule_id"], "expected": schedule["next_run_at"], "upd": schedule["updated_at"],
                  "now": now, "o": outcome, "rc": reason_code, "next": next_run_at}
        with self._db.transaction():
            if outcome == "paused":
                n = self._db.execute_count(
                    f"""UPDATE batch_schedules SET enabled = 0, next_run_at = NULL, paused_reason_code = :rc,
                            last_run_at = :now, last_outcome = :o, last_reason_code = :rc, updated_at = :now
                         WHERE {self._CAS}""", params)
            else:
                n = self._db.execute_count(
                    f"""UPDATE batch_schedules SET next_run_at = :next, last_run_at = :now, last_outcome = :o,
                            last_reason_code = :rc WHERE {self._CAS}""", params)
            if n != 1:
                return False
            self._insert_run(schedule, outcome=outcome, reason_code=reason_code, now=now, batch_status=batch_status)
            if outcome == "paused":
                self._audit("auto_pause", schedule, actor=SCHEDULER_ACTOR,
                            before=_audit_state(schedule),
                            after=_audit_state({**schedule, "enabled": False, "next_run_at": None},
                                               paused_reason_code=reason_code, scheduled_for=schedule["next_run_at"]),
                            now=now)
        return True

    def fire(self, schedule, *, next_run_at, now, verify, armed_env_listed: bool, requester_env_listed: bool) -> "dict | None":
        """회차 발사 = 배치 「전체 재실행」, 한 트랜잭션(모듈 docstring):
          1) 항목 → 배치 잠금(BatchesRepository._lock_items_then_batch) → 항목이 가리키는 스토리지 공유 잠금(_lock_item_storages)
          2) verify(잠근 배치, 잠근 항목) -> 지문 | None -- 판정(batch_scheduler.batch_hold)을 잠근 행으로 다시 본다. None 이면
             롤백(판정 뒤 CSV 교체·옵션 변경·항목 삭제가 커밋됐다 -- 다음 틱이 새 값으로 판정한다)
          3) 직전 회차 결과 스냅숏(리셋이 카운터를 0 으로 만든다)
          4) _rescan_locked(종단 CAS + 항목 리셋 -- :rescan 과 같은 몸통). 다음 상태 = confirm_gated(잠근 연산) ? Previewing :
             Running. 스케줄러는 확인 경로(confirm·auto_confirm_by·Running 직행)를 건드리지 않는다
          5) 스케줄 CAS: 예정 시각·updated_at 이 읽은 값이고, 켠 사람(armed_by)과 배치 요청자가 **지금** 활성 관리자이며 자격
             (env 목록 -- 설정이라 SQL 밖, 호출자가 넘긴다 -- 이거나 계정 화면 부여 root_granted = 1)이 있고, 유지보수·drain 이
             아닐 때만(batches.confirm 의 동의 EXISTS 절과 같은 모양 -- 판정 뒤 회수된 부여가 이 회차를 막는다)
          6) 이력 행(fired, requeued, prev_*) + 감사 행 + 이력 상한 정리
        하나라도 지면 통째 롤백하고 None. 반환 {"status": 다음 상태, "requeued": 되돌린 항목 수, "fingerprint": 지금 지문}."""
        bid = schedule["batch_id"]
        try:
            with self._db.transaction():
                batch, items = self._batches._lock_items_then_batch(bid)
                if batch is None:
                    raise _Lost()
                # verify 가 읽는 스토리지 경로(바인딩)를 커밋까지 고정 -- 경로 변경과 직렬화(_lock_item_storages docstring).
                self._batches._lock_item_storages(bid)
                fingerprint = verify(batch, items)
                if fingerprint is None:
                    raise _Lost()
                prev = {k: batch.get(k) for k in ("status", "item_count", "succeeded_count", "failed_count")}
                to_status = "Previewing" if confirm_gated(batch["operation"]) else "Running"
                requeued = self._batches._rescan_locked(bid, to_status=to_status)
                if requeued is None:
                    raise _Lost()
                # 스케줄 행은 이 UPDATE 가 마지막에 쥔다(항목 → 배치 → 스케줄).
                n = self._db.execute_count(
                    f"""UPDATE batch_schedules SET next_run_at = :next, last_run_at = :now, last_outcome = 'fired',
                            last_reason_code = NULL
                         WHERE {self._CAS} AND armed_by = :armed
                           AND EXISTS (SELECT 1 FROM accounts WHERE username = :armed AND role = 'admin' AND disabled = 0
                                         AND (:armed_env = 1 OR root_granted = 1))
                           AND EXISTS (SELECT 1 FROM accounts WHERE username = :req AND role = 'admin' AND disabled = 0
                                         AND (:req_env = 1 OR root_granted = 1))
                           AND NOT EXISTS (SELECT 1 FROM control_state WHERE id = 1 AND (maintenance <> 0 OR drain <> 0))""",
                    {"s": schedule["schedule_id"], "expected": schedule["next_run_at"], "upd": schedule["updated_at"],
                     "next": next_run_at, "now": now, "armed": schedule["armed_by"],
                     "armed_env": 1 if armed_env_listed is True else 0, "req": batch.get("requester_id"),
                     "req_env": 1 if requester_env_listed is True else 0})
                if n != 1:
                    raise _Lost()
                self._insert_run(schedule, outcome="fired", reason_code=None, now=now, batch_status=prev["status"],
                                 requeued=requeued, prev=prev)
                self._db.execute(
                    """INSERT INTO audit_log (mutation_class, operation, target_key, actor, before_state, after_state, at)
                       VALUES ('batch', 'scheduled_rescan', :key, :actor, :b, :a, :at)""",
                    {"key": bid, "actor": SCHEDULER_ACTOR, "at": now,
                     "b": dump_json(prev),
                     "a": dump_json({"status": to_status, "schedule_id": schedule["schedule_id"],
                                     "armed_by": schedule["armed_by"], "scheduled_for": schedule["next_run_at"],
                                     "requeued": requeued, "fingerprint": fingerprint,
                                     "next_run_at": next_run_at})})
        except _Lost:
            return None
        return {"status": to_status, "requeued": requeued, "fingerprint": fingerprint}
