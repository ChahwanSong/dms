import uuid
from ..db import Database, dump_json, load_json, utc_now_iso

_ACTIVE = ("Previewing", "Running")
# batch_orchestrator._ITEM_TERMINAL 의 거울 -- 배치 완료 CAS(complete_if_all_terminal)의 판정.
_ITEM_TERMINAL = ("Succeeded", "Failed", "Rejected", "Cancelled")
_NO_QUEUED_ITEM = "NOT EXISTS (SELECT 1 FROM batch_items WHERE batch_id = :b AND status = 'Queued')"
# 종단 배치에서 바꿀 수 있는 실행 설정(update_execution_settings). owner_username·operation·items·auth_method 는
# 밖이다 -- 실행 신원·특권 재료와 대상은 생성 시점 사실이다(항목은 항목 편집 라우트 몫).
_SETTINGS_COLUMNS = ("max_concurrency", "priority", "node_count", "procs_per_node", "options")

def _hydrate(row):
    row["options"] = load_json(row["options"])
    # 확인 회차 NULL = 업그레이드 전 행·아직 확인 대기가 된 적 없는 배치 = 0회차(모름이 아니라 정의된 시작값이다 --
    # mark_preview_ready 와 confirm 의 SQL 도 COALESCE(preview_round, 0) 로 같은 규칙을 쓴다).
    if row.get("preview_round") is None:
        row["preview_round"] = 0
    return row


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
        return None if row is None else _hydrate(row)

    def list(self, limit=100):
        rows = self._db.query("SELECT * FROM batches ORDER BY created_at DESC LIMIT :n",
                              {"n": limit})
        return [_hydrate(r) for r in rows]

    def list_awaiting_confirm(self):
        """확인 대기(PreviewReady) 배치. orchestrator 가 **기록만** 하러 돈다(list_active 는 그대로 "굴리는" 상태 --
        라우트가 그 뜻으로 쓴다): 이미 확인돼 실행 중이던 자식이 확인 대기 사이에 끝나면 그 결과를 항목에 남기고,
        전부 끝났으면 완료한다. 새 자식 생성·컨펌·상태 변경은 하지 않는다."""
        rows = self._db.query("SELECT * FROM batches WHERE status = 'PreviewReady' ORDER BY created_at")
        return [_hydrate(r) for r in rows]

    def list_active(self):
        rows = self._db.query(
            "SELECT * FROM batches WHERE status = :a OR status = :b ORDER BY created_at",
            {"a": _ACTIVE[0], "b": _ACTIVE[1]})
        return [_hydrate(r) for r in rows]

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
        배치 1개의 items 라 LEFT JOIN·서브쿼리 비용은 무리 없다.
        미리보기 조인(2026-10-07, 배치 확인 대화상자): 자식 잡(가장 최근 1개)의 상태·미리보기 요약·만료 시각.
        - job_state: 잡 상태(ConfirmPending = 확인을 기다리는 미리보기 완료 항목). 잡 없음 = NULL.
        - preview_summary: 미리보기 summary 사본(files/bytes/returncode) -- 운영자가 무엇을 확인하는지 보여 준다.
          NULL = 모름(미리보기 전·구형 행). 값 안의 null(예: dsync dryrun 의 bytes)도 그대로 둔다(null≠0).
        - preview_expires_at: 이 시각이 지나면 그 항목은 실행되지 않고 거부된다(stepper expire_previews)."""
        rows = self._db.query(
            """SELECT bi.*, r.state AS request_state,
                      (SELECT d.files_count FROM data_jobs d
                        WHERE d.request_id = bi.request_id
                        ORDER BY d.created_at DESC, d.job_id DESC LIMIT 1)
                          AS files_count,
                      (SELECT d.state FROM data_jobs d
                        WHERE d.request_id = bi.request_id
                        ORDER BY d.created_at DESC, d.job_id DESC LIMIT 1)
                          AS job_state,
                      (SELECT d.preview_summary FROM data_jobs d
                        WHERE d.request_id = bi.request_id
                        ORDER BY d.created_at DESC, d.job_id DESC LIMIT 1)
                          AS preview_summary,
                      (SELECT d.preview_expires_at FROM data_jobs d
                        WHERE d.request_id = bi.request_id
                        ORDER BY d.created_at DESC, d.job_id DESC LIMIT 1)
                          AS preview_expires_at,
                      res.completed_at AS completed_at
                 FROM batch_items bi
                 LEFT JOIN requests r ON r.request_id = bi.request_id
                 LEFT JOIN results res ON res.request_id = bi.request_id
                WHERE bi.batch_id = :b ORDER BY bi.seq""", {"b": batch_id})
        for r in rows:
            r["payload"] = load_json(r["payload"])
            r["preview_summary"] = (load_json(r["preview_summary"])
                                    if r.get("preview_summary") is not None else None)
        return rows

    def get_item(self, batch_id, seq):
        row = self._db.query_one(
            "SELECT * FROM batch_items WHERE batch_id = :b AND seq = :s",
            {"b": batch_id, "s": seq})
        if row is not None:
            row["payload"] = load_json(row["payload"])
        return row

    def _recount(self, batch_id):
        """카운터 절대값 재계산(항목 편집·삭제·추가 경로 전용). bump(증분) 대신
        절대값인 이유: 편집·삭제는 임의 상태의 행을 리셋·제거하므로 증분 유지가
        상태별 감산 분기(Succeeded→succeeded-1, Failed|Rejected→failed-1,
        Cancelled→무접촉)를 여기서 또 복제해야 한다 — 분기 복제는 드리프트
        원천이고, 행이 진실이므로 세는 것이 정직하다(reset_all_items 의 "절대값이
        진실" 결정과 같은 방향). 집합 정의는 _record_terminal/bump 경로의 불변식
        그대로: succeeded = Succeeded, failed = Failed|Rejected(Cancelled 는
        어느 쪽도 아니다)."""
        self._db.execute(
            """UPDATE batches SET
                   item_count = (SELECT COUNT(*) FROM batch_items WHERE batch_id = :b),
                   succeeded_count = (SELECT COUNT(*) FROM batch_items
                                       WHERE batch_id = :b AND status = 'Succeeded'),
                   failed_count = (SELECT COUNT(*) FROM batch_items
                                    WHERE batch_id = :b AND status IN ('Failed','Rejected')),
                   updated_at = :now
                 WHERE batch_id = :b""",
            {"b": batch_id, "now": utc_now_iso()})

    def update_item_payload(self, batch_id, seq, payload, *, only_queued: bool) -> bool:
        """항목 편집 = payload 교체 + **Queued 리셋**(request_id/reason_code NULL).
        종단 항목의 payload 만 바꾸면 "이 payload 가 그 결과를 냈다"는 거짓 기록이
        된다(실행 기록 위조 금지) — 편집된 항목은 항상 미실행으로 되돌린다(리셋
        모양은 reset_item_to_queued 와 동일).
        only_queued=True(활성 배치): WHERE status='Queued' 원자 가드 — orchestrator
        materialize(5s 틱)와의 경합에서 영향 행 0 이면 False(호출자가 409).
        DB 가 신뢰 경계라 가드는 SQL 한 문장에 둔다 — 읽고 나서 쓰면 그 사이가
        경합 창이다."""
        guard = " AND status = 'Queued'" if only_queued else ""
        with self._db.transaction():
            n = self._db.execute_count(
                f"""UPDATE batch_items SET payload = :p, status = 'Queued',
                        request_id = NULL, reason_code = NULL, updated_at = :now
                      WHERE batch_id = :b AND seq = :s{guard}""",
                {"p": dump_json(payload), "now": utc_now_iso(),
                 "b": batch_id, "s": seq})
            if n == 0:
                return False
            self._recount(batch_id)
        return True

    def delete_item(self, batch_id, seq, *, only_queued: bool) -> bool:
        """항목 삭제. seq 는 재부여하지 않는다(구멍 유지) — seq 는 항목 식별자라
        재부여하면 남은 항목이 삭제된 항목의 이력(요청 링크·사유)을 사칭한다.
        orchestrator 는 seq 연속성을 가정하지 않는다(목록 길이·상태만 본다 —
        test_orchestrator_tolerates_seq_gap_from_deleted_item 이 고정).
        only_queued 원자 가드는 update_item_payload 와 같은 이유."""
        guard = " AND status = 'Queued'" if only_queued else ""
        with self._db.transaction():
            n = self._db.execute_count(
                f"DELETE FROM batch_items WHERE batch_id = :b AND seq = :s{guard}",
                {"b": batch_id, "s": seq})
            if n == 0:
                return False
            self._recount(batch_id)
        return True

    def add_item(self, batch_id, payload) -> int:
        """항목 추가: seq = MAX(seq)+1(빈 배치는 0). COUNT 가 아닌 이유: 중간
        삭제로 구멍이 있으면 COUNT 는 살아있는 꼬리 seq 와 PK 충돌하거나 구멍을
        재사용해 "seq = 등록 순서" 의미가 흐려진다(releases.seq 의 MAX+1 관례와
        같은 결정). MAX 조회와 INSERT 는 한 트랜잭션 — 동시 추가가 같은 seq 를
        받으면 PK(batch_id, seq) 충돌로 한쪽이 죽는 fail-closed."""
        now = utc_now_iso()
        with self._db.transaction():
            row = self._db.query_one(
                "SELECT COALESCE(MAX(seq) + 1, 0) AS next_seq FROM batch_items "
                "WHERE batch_id = :b", {"b": batch_id})
            seq = row["next_seq"]
            self._db.execute(
                """INSERT INTO batch_items (batch_id, seq, payload, status, request_id,
                       reason_code, created_at, updated_at)
                   VALUES (:b,:s,:p,'Queued',NULL,NULL,:now,:now)""",
                {"b": batch_id, "s": seq, "p": dump_json(payload), "now": now})
            self._recount(batch_id)
        return seq

    def replace_items(self, batch_id, items) -> None:
        """항목 전량 교체(CSV 재업로드 동선): 기존 batch_items 전량 DELETE + 신규
        INSERT(seq 0..n-1 재부여) + 카운터 절대값 재계산 — 한 트랜잭션. 단건
        삭제(delete_item)는 seq 구멍을 유지하지만(남은 항목의 이력 사칭 방지)
        여기는 **전량 신규**라 지킬 이력이 없다 — 0..n-1 재부여가 "CSV 행 순서 =
        seq" 라는 생성(create)과 같은 계약을 복원한다. 전량 Queued 신규 ⇒ 종단
        항목 0 ⇒ _recount 가 succeeded/failed 를 0 으로 되돌린다(절대값 정직 —
        감산 분기 없음). 종단 배치 한정 가드는 라우트 몫(batch_items_not_replaceable).
        배치 status 는 무접촉 — 교체가 곧 실행은 아니다(라우트 주석)."""
        now = utc_now_iso()
        with self._db.transaction():
            self._db.execute("DELETE FROM batch_items WHERE batch_id = :b",
                             {"b": batch_id})
            for seq, item in enumerate(items):
                self._db.execute(
                    """INSERT INTO batch_items (batch_id, seq, payload, status, request_id,
                           reason_code, created_at, updated_at)
                       VALUES (:b,:s,:p,'Queued',NULL,NULL,:now,:now)""",
                    {"b": batch_id, "s": seq, "p": dump_json(item), "now": now})
            self._recount(batch_id)

    def _touch_item(self, batch_id, seq, **fields):
        fields["updated_at"] = utc_now_iso()
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        params = {**fields, "b": batch_id, "s": seq}
        self._db.execute(f"UPDATE batch_items SET {sets} WHERE batch_id = :b AND seq = :s", params)

    def set_item_materialized(self, batch_id, seq, request_id) -> bool:
        """Queued 항목 → Materialized(Queued 가드만). orchestrator 는 payload 까지 보는 claim_queued_item 을 쓴다 --
        이건 payload 비교가 필요 없는 호출자(테스트 픽스처 등)용이다."""
        return self._db.execute_count(
            """UPDATE batch_items SET status = 'Materialized', request_id = :rid, updated_at = :now
                  WHERE batch_id = :b AND seq = :s AND status = 'Queued'""",
            {"rid": request_id, "now": utc_now_iso(), "b": batch_id, "s": seq}) == 1

    def claim_queued_item(self, batch_id, seq, request_id, *, payload_raw) -> bool:
        """Queued 항목을 자식 요청에 묶는다(→ Materialized) -- **호출자 트랜잭션 안에서**, 자식 요청 INSERT 와
        같은 트랜잭션(orchestrator._materialize). 가드: 아직 Queued 이고 payload 가 orchestrator 가 자식을 만든
        그 값(payload_raw = 같은 트랜잭션에서 읽은 저장 문자열)일 때만. 예전엔 무가드 갱신이라
        스냅샷 뒤 관리자가 항목을 고치거나 지운 것을 덮었다 -- 화면엔 새 경로, 실제 자식은 옛 경로라 운영자가
        본 것과 다른 미리보기를 확인하게 된다(2026-10-07 리뷰). 0 행이면 False -- 호출자가 롤백한다."""
        return self._db.execute_count(
            """UPDATE batch_items SET status = 'Materialized', request_id = :rid, updated_at = :now
                  WHERE batch_id = :b AND seq = :s AND status = 'Queued' AND payload = :p""",
            {"rid": request_id, "now": utc_now_iso(), "b": batch_id, "s": seq,
             "p": payload_raw}) == 1

    def queued_item_payload_raw(self, batch_id, seq) -> str | None:
        """Queued 항목의 저장 payload 문자열(claim_queued_item 의 비교값). Queued 가 아니거나 없으면 None."""
        row = self._db.query_one(
            "SELECT payload FROM batch_items WHERE batch_id = :b AND seq = :s AND status = 'Queued'",
            {"b": batch_id, "s": seq})
        return None if row is None else row["payload"]

    def set_item_status(self, batch_id, seq, status, *, reason_code=None):
        self._touch_item(batch_id, seq, status=status, reason_code=reason_code)

    def reject_queued_item(self, batch_id, seq, *, reason_code, expected_payload) -> bool:
        """자식을 만들기 전에 거부된 항목(orchestrator _materialize 의 재검증 실패) -> Rejected + 실패 1.
        **아직 Queued 이고 payload 가 거부 판정에 쓴 그 값일 때만**(원자 가드): orchestrator 가 읽은 목록 뒤에 관리자가
        항목을 지웠으면 0행, 고쳤으면(편집은 Queued 를 유지한다) 저장값이 달라 0행 -- 집계도 하지 않고 다음 틱이 새
        payload 로 다시 판정한다. 무조건 bump 하면 지워진 항목 몫이 failed_count 에 남았고(2026-10-01 리뷰), payload
        를 안 보면 고친 항목이 옛 경로의 사유로 거부됐다(2026-10-07 리뷰 -- claim_queued_item 과 같은 가드). UPDATE 에도
        payload 를 거는 이유: Postgres READ COMMITTED 에서 SELECT 와 UPDATE 사이에 편집이 커밋되면 UPDATE 가 새 행
        버전을 다시 평가해 0 행이 된다."""
        changed = 0
        with self._db.transaction():
            row = self._db.query_one(
                "SELECT payload FROM batch_items WHERE batch_id = :b AND seq = :s AND status = 'Queued'",
                {"b": batch_id, "s": seq})
            if row is None or load_json(row["payload"]) != expected_payload:
                return False
            changed = self._db.execute_count(
                """UPDATE batch_items SET status = 'Rejected', reason_code = :r, updated_at = :now
                   WHERE batch_id = :b AND seq = :s AND status = 'Queued' AND payload = :p""",
                {"r": reason_code, "now": utc_now_iso(), "b": batch_id, "s": seq, "p": row["payload"]})
            if changed == 1:
                self.bump_counts(batch_id, failed=1)
        return changed == 1

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

    def update_execution_settings(self, batch_id, fields, *, before, actor) -> bool:
        """종단 배치의 실행 설정(_SETTINGS_COLUMNS) 부분 갱신 + 감사 행, 한 트랜잭션.

        가드는 SQL 한 문장(WHERE status IN 종단)이다 -- 라우트가 읽은 뒤 :rescan·항목 추가가 배치를 다시 돌리기
        시작하면 영향 행 0 = False(호출자 409). 읽고 나서 쓰면 그 사이가 경합 창이고, 이미 materialize 된 자식은
        배치 행을 다시 읽지 않아 "일부 자식은 옛값, 일부는 새값"이 된다(patch_batch docstring 의 이유).
        감사: 메타(name/note) 수정과 달리 audit_log 에 남긴다(mutation_class 'batch') -- 옵션(delete·chown)·노드
        수는 root 로 도는 다음 재실행이 무엇을 하는지를 바꾸므로 누가 무엇을 바꿨는지가 남아야 한다. before/after 는
        바뀐 키만 담는다(호출자가 실제로 달라진 키만 넘긴다)."""
        unknown = set(fields) - set(_SETTINGS_COLUMNS)
        if unknown or not fields:
            # 컬럼명은 SQL 문자열로 조립되므로 허용 목록 밖 키는 여기서 끊는다(호출자 버그 = 조용한 무시 금지).
            raise ValueError(f"bad execution settings fields: {sorted(unknown) or 'empty'}")
        params = {k: (dump_json(v) if k == "options" else v) for k, v in fields.items()}
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        now = utc_now_iso()
        with self._db.transaction():
            n = self._db.execute_count(
                f"""UPDATE batches SET {sets}, updated_at = :now
                      WHERE batch_id = :b AND status IN ('Completed', 'Cancelled')""",
                {**params, "now": now, "b": batch_id})
            if n == 0:
                return False
            self._db.execute(
                """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                       before_state, after_state, at)
                   VALUES ('batch', 'execution_settings', :key, :actor, :b, :a, :at)""",
                {"key": batch_id, "actor": actor, "b": dump_json(before),
                 "a": dump_json(fields), "at": now})
        return True

    def delete(self, batch_id):
        """배치 삭제: batches 행 + batch_items 행만, 한 트랜잭션. 자식
        requests/data_jobs/results 는 **보존**한다 — 실행 감사 이력이고,
        requests.batch_id 는 역사적 표식으로 남는다(배치 역참조가 404 가 되는
        것은 수용 — 화면 소비처 실측상 요청→배치 링크는 없다). 종단 배치 한정
        가드는 라우트 몫(batch_not_deletable)."""
        with self._db.transaction():
            self._db.execute("DELETE FROM batch_items WHERE batch_id = :b",
                             {"b": batch_id})
            self._db.execute("DELETE FROM batches WHERE batch_id = :b",
                             {"b": batch_id})

    def set_status(self, batch_id, status):
        self._db.execute(
            "UPDATE batches SET status = :s, updated_at = :now WHERE batch_id = :b",
            {"s": status, "now": utc_now_iso(), "b": batch_id})

    def set_status_if(self, batch_id, status, *, from_states) -> bool:
        """배치 상태 CAS: 지금 상태가 from_states 중 하나일 때만 status 로. 라우트는 요청 앞에서 읽은 배치 행으로
        판단하는데, 그 사이 취소·확인·orchestrator 전이가 끼면 무조건 쓰기는 그것을 덮는다(취소된 배치 부활 등)."""
        if not from_states:
            return False
        names = [f"f{i}" for i in range(len(from_states))]
        return self._db.execute_count(
            f"UPDATE batches SET status = :s, updated_at = :now "
            f"WHERE batch_id = :b AND status IN ({', '.join(':' + n for n in names)})",
            {"s": status, "now": utc_now_iso(), "b": batch_id,
             **dict(zip(names, from_states))}) == 1

    def reopen_if_queued(self, batch_id) -> bool:
        """PreviewReady 인데 Queued 항목이 있으면 Previewing 으로(한 문장). 확인(confirm)이 Queued 때문에 거절된 배치가
        확인 대기에 영영 갇히지 않게 하는 자가 치유 -- 정상 경로(라우트의 Previewing 복귀)를 놓친 경우에만 쓸모가 있다."""
        return self._db.execute_count(
            """UPDATE batches SET status = 'Previewing', updated_at = :now
                  WHERE batch_id = :b AND status = 'PreviewReady'
                    AND EXISTS (SELECT 1 FROM batch_items WHERE batch_id = :b AND status = 'Queued')""",
            {"now": utc_now_iso(), "b": batch_id}) == 1

    def mark_preview_ready(self, batch_id) -> bool:
        """Previewing → PreviewReady, 단 **Queued 항목이 하나도 없을 때만**(한 문장). orchestrator 는 틱 스냅샷으로
        "전원 미리보기 끝" 을 판정하는데, 그 뒤 API 가 항목을 추가·재실행했으면 미리보기 안 된 Queued 가 있는 채로
        확인 대기가 되고, 확인하면 그 항목이 사람 확인 없이 root 로 돈다(2026-10-07 리뷰). 0 행이면 다음 틱이 판정한다."""
        return self._db.execute_count(
            f"""UPDATE batches SET status = 'PreviewReady', preview_round = COALESCE(preview_round, 0) + 1,
                       updated_at = :now
                  WHERE batch_id = :b AND status = 'Previewing' AND {_NO_QUEUED_ITEM}""",
            {"now": utc_now_iso(), "b": batch_id}) == 1

    def complete_if_all_terminal(self, batch_id, *, from_status) -> bool:
        """from_status → Completed, 단 **모든 항목이 종단일 때만**(한 문장). 스냅샷 뒤 추가된 Queued 항목을 둔 채
        완료로 덮으면 그 항목은 아무도 집지 않는다(종단 배치는 루프 밖)."""
        ph = ", ".join(f"'{s}'" for s in _ITEM_TERMINAL)
        return self._db.execute_count(
            f"""UPDATE batches SET status = 'Completed', updated_at = :now
                  WHERE batch_id = :b AND status = :cur
                    AND NOT EXISTS (SELECT 1 FROM batch_items
                                     WHERE batch_id = :b AND status NOT IN ({ph}))""",
            {"now": utc_now_iso(), "b": batch_id, "cur": from_status}) == 1

    def confirm(self, batch_id, *, actor, summary, expected_round) -> bool:
        """sync 배치 확인(PreviewReady → Running) + 감사 행, 한 트랜잭션(2026-10-07).

        가드는 SQL 한 문장(WHERE status = 'PreviewReady')이다 -- 라우트가 읽은 뒤 그 사이 항목 추가·재실행으로 배치가
        Previewing 으로 되돌아갔으면 영향 행 0 = False(호출자 409). 읽고 나서 set_status 로 쓰면 운영자가 본 것과 다른
        배치(사람이 보지 않은 새 항목 포함)를 확인하게 된다. 감사: 확인이 곧 root 실행 시작인데 자식 전이 actor 는
        batch-orchestrator 뿐이라, 이 행이 없으면 누가 확인했는지가 어디에도 남지 않는다(실행 설정 변경과 같은
        mutation_class 'batch'). after_state 에 확인 시점의 항목 수·미리보기 완료 수·옵션을 남긴다.
        Queued 항목이 있으면 확인하지 않는다(같은 문장) -- 미리보기 안 된 항목이 확인에 묻어 root 로 도는 것을 막는
        마지막 둑이다(mark_preview_ready 와 라우트의 Previewing 복귀가 앞 둑). 확인 회차(expected_round)도 같은 문장에서
        본다 -- 운영자가 대화상자를 연 뒤 배치가 Previewing 을 거쳐 다시 확인 대기가 됐으면(ABA) 다른 회차라 0 행이다.

        확인 도장(같은 트랜잭션): 지금 ConfirmPending 인 자식 잡에 confirmed_fingerprint = preview_fingerprint 를 찍는다.
        orchestrator 는 Running 에서 **도장이 지금 미리보기와 같은 자식만** 실행한다 -- 도장 없는 미리보기(업그레이드 전
        옛 코드가 Running 중에 만든 것, 다시 미리보기해 지문이 바뀐 것)가 보이면 배치를 Previewing 으로 되돌린다. 상태
        수준의 둑만으로는 "운영자가 본 미리보기" 와 "본 적 없는 미리보기" 를 구별할 수 없었다(2026-10-07 리뷰)."""
        now = utc_now_iso()
        with self._db.transaction():
            n = self._db.execute_count(
                f"""UPDATE batches SET status = 'Running', updated_at = :now
                      WHERE batch_id = :b AND status = 'PreviewReady' AND COALESCE(preview_round, 0) = :r
                        AND {_NO_QUEUED_ITEM}""",
                {"now": now, "b": batch_id, "r": expected_round})
            if n == 0:
                return False
            stamped = self._db.execute_count(
                """UPDATE data_jobs SET confirmed_fingerprint = preview_fingerprint, updated_at = :now
                      WHERE state = 'ConfirmPending' AND preview_fingerprint IS NOT NULL
                        AND request_id IN (SELECT request_id FROM batch_items
                                            WHERE batch_id = :b AND status = 'Materialized'
                                              AND request_id IS NOT NULL)""",
                {"now": now, "b": batch_id})
            summary = {**summary, "preview_round": expected_round, "stamped": stamped}
            self._db.execute(
                """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                       before_state, after_state, at)
                   VALUES ('batch', 'confirm', :key, :actor, :b, :a, :at)""",
                {"key": batch_id, "actor": actor, "b": dump_json({"status": "PreviewReady"}),
                 "a": dump_json({"status": "Running", **summary}), "at": now})
        return True

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

    # 반환 annotation 이 문자열인 이유: 클래스 본문 스코프에서 `list` 는 이 클래스의
    # list() 메서드라, 따옴표 없이 쓰면 def 시점에 'function' object is not
    # subscriptable 로 죽는다(실측).
    def reset_items_to_queued(self, batch_id, seqs) -> "list[int]":
        """선택 재실행: 호출자가 준 seq 목록 중 **종단 항목만** Queued 로 되돌리고
        실제로 되돌린 seq 를 돌려준다(부분 성공 모델 — 나머지의 사유 분류는
        라우트 몫).
        reset_all_items/reset_failed_items 와 달리 대상이 **필터가 아니라 목록**이라
        종단 판정을 SELECT 로 미리 읽지 않고 UPDATE 의 WHERE 에 실어 원자적으로
        건다 — 읽고 나서 쓰면 그 사이 orchestrator(5s 틱)가 항목을 materialize/
        종단화할 수 있다(update_item_payload 의 only_queued 가드와 같은 이유,
        방향만 반대다). 영향 행 0 = 없는 seq 이거나 비종단 — 호출자가 판별한다.
        카운터는 bump(감산)가 아니라 _recount(절대값): 되돌린 항목이 Succeeded /
        Failed|Rejected / Cancelled 중 무엇이었는지에 따라 감산 분기가 갈리는데,
        그 분기를 여기서 또 복제하면 드리프트 원천이다(_recount docstring 의
        "행이 진실" 결정 재사용). 전체 리셋(reset_all_items)의 0 리셋도 못 쓴다 —
        고르지 않은 종단 항목의 카운트는 살아 있어야 한다.
        되돌린 것이 하나도 없으면 _recount 도 건너뛴다: 카운터를 안 흔드는 것이
        무접촉의 정의고, updated_at 만 튀는 것도 거짓 변경 기록이다."""
        done = []
        with self._db.transaction():
            for seq in seqs:
                n = self._db.execute_count(
                    # IN 목록 = batch_orchestrator._ITEM_TERMINAL 과 같은 집합
                    """UPDATE batch_items SET status = 'Queued', request_id = NULL,
                           reason_code = NULL, updated_at = :now
                         WHERE batch_id = :b AND seq = :s
                           AND status IN ('Succeeded','Failed','Rejected','Cancelled')""",
                    {"now": utc_now_iso(), "b": batch_id, "s": seq})
                if n > 0:
                    done.append(seq)
            if len(done) > 0:
                self._recount(batch_id)
        return done

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
