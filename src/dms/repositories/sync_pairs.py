"""사용자 sync 허용 스토리지 쌍(2026-09-30 사용자 결정).

"사용자들에 대해서 sync 가능한 스토리지 쌍 정책 -- 기본 전부 불가에 허용 쌍을 추가하는 방식".
- 방향이 있다: (소스 -> 목적지). A->B 를 허용해도 B->A 는 따로 허용해야 한다(데이터 이관은 대개
  한 방향이고, 반대 방향은 되돌림이라 같은 권한으로 보지 않는다). 같은 스토리지 안의 sync(A->A)도
  한 쌍이다 -- "전부 불가" 에 예외가 없다.
- 적용 대상은 **비관리자 sync** 뿐이다: 관리자·배치(관리자 전용 화면)는 제한이 없다.
- 강제 지점: 제출(routes_requests.submit) · 계획(planner) · 컨펌(routes_jobs.confirm_job) 이 모두
  sync_pair_allowed 하나로 판정한다(화면 필터는 표시일 뿐). 쌍이 빠지면 대기·컨펌 대기 중인 사용자
  sync 도 거기서 멈춘다(storage_admin_only 와 같은 규칙).
- 스토리지가 삭제되면 그 스토리지를 참조하는 쌍도 같은 트랜잭션에서 지운다(StoragesRepository.delete)
  -- 같은 이름으로 다시 등록된 스토리지가 옛 허용을 물려받지 않게.
"""
from ..db import Database, dump_json, utc_now_iso
from ..domain import DomainValidationError


def sync_pair_allowed(repos, source_storage, destination_storage) -> bool:
    """비관리자 sync 의 허용 판정 -- 제출·계획·컨펌이 공유하는 단일 규칙."""
    if not source_storage or not destination_storage:
        return False
    return repos.sync_pairs.get(source_storage, destination_storage) is not None


class SyncPairsRepository:
    def __init__(self, db: Database):
        self._db = db

    def _audit(self, operation, key, before, after, actor):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                   before_state, after_state, at)
               VALUES ('sync_pair', :op, :key, :actor, :b, :a, :at)""",
            {"op": operation, "key": key, "actor": actor,
             "b": dump_json(before) if before else None,
             "a": dump_json(after) if after else None, "at": utc_now_iso()})

    def list(self):
        return self._db.query(
            "SELECT * FROM sync_pairs ORDER BY source_storage, destination_storage")

    def get(self, source_storage, destination_storage):
        return self._db.query_one(
            """SELECT * FROM sync_pairs
               WHERE source_storage = :s AND destination_storage = :d""",
            {"s": source_storage, "d": destination_storage})

    def add(self, source_storage, destination_storage, *, actor):
        """허용 쌍 추가 -- 이미 있으면 그대로 돌려준다(멱등, 감사 기록 없음).
        반환: (row, created). 존재하는 스토리지끼리만(없으면 DomainValidationError storage_missing):
        없는 이름의 허용이 남아 있다가 같은 이름으로 등록되는 스토리지에 조용히 물려지지 않게. 확인을
        삽입과 **같은 트랜잭션**에서 한다 -- 밖에서 보면 확인과 삽입 사이에 끝난 스토리지 삭제
        (StoragesRepository.delete 의 쌍 정리)를 비껴간 쌍이 남는다."""
        with self._db.transaction():
            for name in (source_storage, destination_storage):
                if self._db.query_one("SELECT 1 AS one FROM storages WHERE storage_name = :n",
                                      {"n": name}) is None:
                    raise DomainValidationError("storage_missing", name)
            existing = self.get(source_storage, destination_storage)
            if existing is not None:
                return existing, False
            self._db.execute(
                """INSERT INTO sync_pairs (source_storage, destination_storage,
                       created_at, created_by)
                   VALUES (:s, :d, :at, :by)""",
                {"s": source_storage, "d": destination_storage,
                 "at": utc_now_iso(), "by": actor})
            row = self.get(source_storage, destination_storage)
            self._audit("add", f"{source_storage}->{destination_storage}", None, row, actor)
        return row, True

    def remove(self, source_storage, destination_storage, *, actor) -> bool:
        with self._db.transaction():
            before = self.get(source_storage, destination_storage)
            if before is None:
                return False
            self._db.execute(
                """DELETE FROM sync_pairs
                   WHERE source_storage = :s AND destination_storage = :d""",
                {"s": source_storage, "d": destination_storage})
            self._audit("remove", f"{source_storage}->{destination_storage}", before, None, actor)
        return True
