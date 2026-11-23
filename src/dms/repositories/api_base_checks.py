"""API 파드별 artifact base 점검 기록(2026-11-23, api/base_monitor.py).

행 = 파드(instance = HOSTNAME) 하나의 마지막 점검. 모니터가 회차마다 upsert 하고 24시간 지난 행을 지운다. 감사는 남기지
않는다(주기 관측이지 운영자 변경이 아니다 -- control.set_artifact_base_check 와 같은 원칙). 쓰기는 best-effort 라 호출자가
예외를 삼킨다 -- 이 기록 때문에 점검·readyz 가 실패하면 안 된다.
"""
from ..db import Database, iso_plus, utc_now_iso

ROW_RETENTION_SECONDS = 24 * 3600
_COLUMNS = ("instance", "node_name", "uri", "ok", "reason", "posture", "fstype", "gate_state",
            "started_at", "checked_at", "first_ok_at", "ready_at")


def _hydrate(row: dict) -> dict:
    out = dict(row)
    # ok 는 NULL(아직) / 0 / 1 -- truthy 로 접지 않는다(null ≠ 실패).
    out["ok"] = None if row["ok"] is None else bool(row["ok"])
    return out


class ApiBaseChecksRepository:
    def __init__(self, db: Database):
        self._db = db

    def upsert(self, row: dict) -> None:
        """instance 키 upsert(sqlite 3.24+·PG 공용 ON CONFLICT -- node_exclusions 선례). 없는 키는 NULL."""
        params = {c: row.get(c) for c in _COLUMNS}
        ok = params["ok"]
        params["ok"] = None if ok is None else (1 if ok else 0)
        cols = ", ".join(_COLUMNS)
        values = ", ".join(f":{c}" for c in _COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS if c != "instance")
        self._db.execute(
            f"INSERT INTO api_base_checks ({cols}) VALUES ({values}) "
            f"ON CONFLICT (instance) DO UPDATE SET {updates}", params)

    def list_recent(self, *, now_iso: "str | None" = None,
                    within_seconds: int = ROW_RETENTION_SECONDS) -> list[dict]:
        """마지막 활동(checked_at, 없으면 started_at)이 within_seconds 안인 행. 정렬은 호출자가 한다."""
        cutoff = iso_plus(now_iso or utc_now_iso(), -within_seconds)
        rows = self._db.query(
            """SELECT * FROM api_base_checks
               WHERE COALESCE(checked_at, started_at) >= :cutoff ORDER BY instance""",
            {"cutoff": cutoff})
        return [_hydrate(r) for r in rows]

    def prune(self, *, now_iso: "str | None" = None,
              older_than_seconds: int = ROW_RETENTION_SECONDS) -> int:
        cutoff = iso_plus(now_iso or utc_now_iso(), -older_than_seconds)
        return self._db.execute_count(
            "DELETE FROM api_base_checks WHERE COALESCE(checked_at, started_at) < :cutoff",
            {"cutoff": cutoff})
