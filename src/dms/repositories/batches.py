import uuid
from ..db import Database, dump_json, load_json, utc_now_iso

_ACTIVE = ("Previewing", "Running")

class BatchesRepository:
    def __init__(self, db: Database):
        self._db = db

    def create(self, *, operation, requester_id, actor, max_concurrency, options,
               note, items, status, priority=None, node_count=None,
               procs_per_node=None, owner_username=None, auth_method=None,
               name=None) -> str:
        # priority/node_count/procs_per_node NULL = 미지정(정책 기본) — null≠0
        # (0은 유효값이 아님).
        # owner_username NULL = 비특권 현행. auth_method 기본 None(모름) — 라우트가
        # 늘 실값을 명시하고, 빠뜨린 새 호출자의 배치는 orchestrator 가 token 으로
        # 접는다(requests.create 의 "token" 기본과 같은 fail-closed 방향).
        # name NULL = 이름 없음 — 라우트가 trim·빈값 접기를 끝낸 값만 넘긴다.
        bid = uuid.uuid4().hex
        now = utc_now_iso()
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO batches (batch_id, operation, requester_id, actor, status,
                       max_concurrency, options, note, item_count, succeeded_count,
                       failed_count, created_at, updated_at, priority, node_count,
                       procs_per_node, owner_username, auth_method, name)
                   VALUES (:id,:op,:req,:actor,:st,:mc,:opt,:note,:n,0,0,:now,:now,:pri,:nc,
                       :ppn,:own,:auth,:name)""",
                {"id": bid, "op": operation, "req": requester_id, "actor": actor,
                 "st": status, "mc": max_concurrency, "opt": dump_json(options),
                 "note": note, "n": len(items), "now": now,
                 "pri": priority, "nc": node_count, "ppn": procs_per_node,
                 "own": owner_username, "auth": auth_method, "name": name})
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

    def list_items_detail(self, batch_id):
        """배치 상세 화면용 items + 자식 요청 조인 필드. list_items 와 별도인 이유:
        orchestrator 루프(5s 틱)는 이 조인이 필요 없다 — 화면 경로만 넓힌다.
        - request_state: 자식 요청의 현재 상태(LEFT JOIN — 미 materialize 는 NULL).
        - files_count: 자식 잡의 처리 파일 수(data_jobs 가 원천). 요청당 잡이
          여럿일 수 있어(취소 경로 등) 최신 잡 하나를 스칼라 서브쿼리로 고른다.
          NULL = 모름(잡 없음/미기록) — 0(파일 없음)과 다르다(null≠0).
        - completed_at: results.completed_at — 종단 요청만 행이 있다(finalize 가
          전이와 원자적으로 남긴다). 비종단은 NULL. updated_at("마지막 전이")을
          완료 시각으로 쓰면 진행 중 요청에 거짓 완료 시각이 찍힌다
          (RecentRequestsSection 의 같은 취지 결정 미러).
        배치 1개의 items 라 LEFT JOIN·서브쿼리 비용은 무리 없다."""
        rows = self._db.query(
            """SELECT bi.*, r.state AS request_state,
                      (SELECT d.files_count FROM data_jobs d
                        WHERE d.request_id = bi.request_id
                        ORDER BY d.created_at DESC, d.job_id DESC LIMIT 1)
                          AS files_count,
                      res.completed_at AS completed_at
                 FROM batch_items bi
                 LEFT JOIN requests r ON r.request_id = bi.request_id
                 LEFT JOIN results res ON res.request_id = bi.request_id
                WHERE bi.batch_id = :b ORDER BY bi.seq""", {"b": batch_id})
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

    def update_meta(self, batch_id, **fields):
        """메타데이터 부분 갱신(name/note): **넘어온 키만** 만진다 — 키 부재는
        무접촉이고, 명시적 None 은 NULL 로 지운다(호출자가 빈 문자열→None 접기를
        끝낸 값만 넘긴다). items·실행 제어 컬럼은 여기로 못 들어온다 — 라우트가
        메타 두 키만 추려 넘기는 계약(즉시 실행 모델, routes_batches 주석)."""
        if not fields:
            return
        fields["updated_at"] = utc_now_iso()
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        self._db.execute(f"UPDATE batches SET {sets} WHERE batch_id = :b",
                         {**fields, "b": batch_id})

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
