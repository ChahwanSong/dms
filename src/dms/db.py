"""단일 DB 접근 계층. SQL은 named param(:name)으로 쓰고 방언 차이는 여기서 흡수한다."""
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

_NAMED = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_plus(ts: str, seconds: int) -> str:
    """ISO-8601 UTC(...Z) 문자열에 초를 더한다(음수 허용)."""
    base = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_epoch(ts: str) -> float:
    """ISO-8601 UTC(...Z) 문자열 -> epoch 초. 시각의 차는 SQL 로 이식성 있게 못
    뺀다(julianday 는 SQLite, EXTRACT(EPOCH)는 PG 전용) -- 전부 파이썬에서 뺀다.
    metrics_series/repositories.metrics 의 사본 _epoch 두 벌을 여기로 승격했다."""
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).timestamp()


class Database:
    def __init__(self, conn, dialect: str):
        self._conn = conn
        self.dialect = dialect
        self._lock = threading.RLock()

    @classmethod
    def connect(cls, url: str) -> "Database":
        if url.startswith("sqlite:///"):
            path = url[len("sqlite:///"):]
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.isolation_level = None  # 명시적 트랜잭션 제어
            conn.execute("PRAGMA foreign_keys = ON")
            return cls(conn, "sqlite")
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            import psycopg
            from psycopg.rows import dict_row
            conn = psycopg.connect(url, row_factory=dict_row, autocommit=True)
            return cls(conn, "postgresql")
        raise ValueError(f"unsupported database url: {url}")

    def _adapt(self, sql: str) -> str:
        if self.dialect == "postgresql":
            return _NAMED.sub(r"%(\1)s", sql)
        return sql

    def execute(self, sql: str, params: dict | None = None) -> None:
        with self._lock:
            self._conn.execute(self._adapt(sql), params or {})

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(self._adapt(sql), params or {})
            return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params: dict | None = None) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self):
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")


def dump_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def load_json(text):
    return json.loads(text) if text else None
