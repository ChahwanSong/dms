import uuid
from ..db import Database, dump_json, load_json, utc_now_iso

_ACTIVE = ("Previewing", "Running")

class BatchesRepository:
    def __init__(self, db: Database):
        self._db = db

    def create(self, *, operation, requester_id, actor, max_concurrency, options,
               note, items, status, priority=None, node_count=None) -> str:
        # priority/node_count NULL = 미지정(정책 기본) — null≠0 (0은 유효값이 아님).
        bid = uuid.uuid4().hex
        now = utc_now_iso()
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO batches (batch_id, operation, requester_id, actor, status,
                       max_concurrency, options, note, item_count, succeeded_count,
                       failed_count, created_at, updated_at, priority, node_count)
                   VALUES (:id,:op,:req,:actor,:st,:mc,:opt,:note,:n,0,0,:now,:now,:pri,:nc)""",
                {"id": bid, "op": operation, "req": requester_id, "actor": actor,
                 "st": status, "mc": max_concurrency, "opt": dump_json(options),
                 "note": note, "n": len(items), "now": now,
                 "pri": priority, "nc": node_count})
            for seq, item in enumerate(items):
                self._db.execute(
                    """INSERT INTO batch_items (batch_id, seq, payload, status, request_id,
                           reason_code, created_at, updated_at)
                       VALUES (:b,:s,:p,'Queued',NULL,NULL,:now,:now)""",
                    {"b": bid, "s": seq, "p": dump_json(item), "now": now})
        return bid

    def get(self, batch_id):
        row = self._db.query_one("SELECT * FROM batches WHERE batch_id = :b", {"b": batch_id})
        if row is not None:
            row["options"] = load_json(row["options"])
        return row

    def list(self, limit=100):
        rows = self._db.query("SELECT * FROM batches ORDER BY created_at DESC LIMIT :n",
                              {"n": limit})
        for r in rows:
            r["options"] = load_json(r["options"])
        return rows

    def list_active(self):
        rows = self._db.query(
            "SELECT * FROM batches WHERE status = :a OR status = :b ORDER BY created_at",
            {"a": _ACTIVE[0], "b": _ACTIVE[1]})
        for r in rows:
            r["options"] = load_json(r["options"])
        return rows

    def list_items(self, batch_id):
        rows = self._db.query(
            "SELECT * FROM batch_items WHERE batch_id = :b ORDER BY seq", {"b": batch_id})
        for r in rows:
            r["payload"] = load_json(r["payload"])
        return rows

    def _touch_item(self, batch_id, seq, **fields):
        fields["updated_at"] = utc_now_iso()
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        params = {**fields, "b": batch_id, "s": seq}
        self._db.execute(f"UPDATE batch_items SET {sets} WHERE batch_id = :b AND seq = :s", params)

    def set_item_materialized(self, batch_id, seq, request_id):
        self._touch_item(batch_id, seq, status="Materialized", request_id=request_id)

    def set_item_status(self, batch_id, seq, status, *, reason_code=None):
        self._touch_item(batch_id, seq, status=status, reason_code=reason_code)

    def reset_item_to_queued(self, batch_id, seq):
        self._touch_item(batch_id, seq, status="Queued", request_id=None, reason_code=None)

    def set_status(self, batch_id, status):
        self._db.execute(
            "UPDATE batches SET status = :s, updated_at = :now WHERE batch_id = :b",
            {"s": status, "now": utc_now_iso(), "b": batch_id})

    def bump_counts(self, batch_id, *, succeeded=0, failed=0):
        self._db.execute(
            """UPDATE batches SET succeeded_count = succeeded_count + :s,
                   failed_count = failed_count + :f, updated_at = :now WHERE batch_id = :b""",
            {"s": succeeded, "f": failed, "now": utc_now_iso(), "b": batch_id})

    def reset_all_items(self, batch_id) -> int:
        # 전체 재실행(:rescan): 종단 item 전부를 Queued 로 되돌린다. 성공 item 도
        # 포함하는 이유는 성장 모니터링(같은 대상 재스캔) 유스케이스. 비종단
        # (Queued/Materialized) item 은 무접촉 — 활성 자식과의 충돌은 라우트의
        # 종단 배치 가드가 막지만 repo 층에서도 종단만 만진다(이중 방어).
        with self._db.transaction():
            rows = self._db.query(
                # IN 목록 = batch_orchestrator._ITEM_TERMINAL 과 같은 집합
                "SELECT seq FROM batch_items WHERE batch_id = :b AND status IN "
                "('Succeeded','Failed','Rejected','Cancelled')", {"b": batch_id})
            for r in rows:
                self.reset_item_to_queued(batch_id, r["seq"])
            # 카운터는 감산이 아니라 0 리셋 — 전체 재시작이라 절대값이 진실이다.
            self._db.execute(
                """UPDATE batches SET succeeded_count = 0, failed_count = 0,
                       updated_at = :now WHERE batch_id = :b""",
                {"now": utc_now_iso(), "b": batch_id})
        return len(rows)

    def reset_failed_items(self, batch_id) -> int:
        with self._db.transaction():
            rows = self._db.query(
                "SELECT seq FROM batch_items WHERE batch_id = :b AND (status = 'Failed' OR status = 'Rejected')",
                {"b": batch_id})
            for r in rows:
                self.reset_item_to_queued(batch_id, r["seq"])
            if rows:
                self.bump_counts(batch_id, failed=-len(rows))
        return len(rows)
