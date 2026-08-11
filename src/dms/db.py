"""단일 DB 접근 계층. SQL은 named param(:name)으로 쓰고 방언 차이는 여기서 흡수한다."""
import json
import re
import sqlite3
import sys
import threading
import time
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
        # 재연결(슬라이스 22)의 선행 조건 -- connect() 만 채운다. 직접 생성된
        # 인스턴스(테스트 더블 관례)는 None = 죽음 처리 자체를 하지 않는다.
        self._url: str | None = None
        # 트랜잭션 깊이. > 0 이면 _run 은 재연결도 재시도도 하지 않는다(§2.3):
        # 트랜잭션 중간 재연결은 서버가 커넥션 소멸과 함께 폐기한 앞 문장들 위에
        # 뒷문장만 새 커넥션에 다시 적용하는 것 -- 부분 적용을 "만들어내는" 동작이다.
        self._txn_depth = 0
        # 관측(§2.6): /readyz 200 본문과 stderr 로그가 읽는다.
        self.reconnect_count = 0
        self.last_reconnect_at: str | None = None
        # 재연결 성공 직후 1회 불리는 훅 -- wiring 이 record_event 를 단다(§2.6).
        self.on_reconnect = None
        self._in_reconnect_hook = False

    @classmethod
    def connect(cls, url: str) -> "Database":
        conn, dialect = cls._open(url)
        db = cls(conn, dialect)
        db._url = url  # 재연결의 유일한 재료 -- 기존 코드는 URL 을 버렸다(§1-1)
        return db

    @staticmethod
    def _open(url: str):
        """URL -> (conn, dialect). connect() 와 _reconnect() 가 **같은 분기**를
        쓴다 -- 갈라지면 재연결된 커넥션만 row_factory/PRAGMA 를 잃는다."""
        if url.startswith("sqlite:///"):
            path = url[len("sqlite:///"):]
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.isolation_level = None  # 명시적 트랜잭션 제어
            conn.execute("PRAGMA foreign_keys = ON")
            return conn, "sqlite"
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            import psycopg
            from psycopg.rows import dict_row
            return (psycopg.connect(url, row_factory=dict_row, autocommit=True),
                    "postgresql")
        raise ValueError(f"unsupported database url: {url}")

    def _adapt(self, sql: str) -> str:
        if self.dialect == "postgresql":
            return _NAMED.sub(r"%(\1)s", sql)
        return sql

    def _connection_is_dead(self, exc) -> bool:
        """죽음 판정(§2.1) -- postgresql 은 예외 클래스 + closed **이중 게이트**.

        클래스만 보면 DiskFull·ConnectionTimeout·AdminShutdown(전부
        OperationalError 하위, psycopg 3.3.4 실증)까지 재시도하고, closed 만
        보면 잡을 예외 범위가 없다 -- 교집합이 정확하다. 문법 오류는
        ProgrammingError 계열이라 첫 게이트에서 걸러진다. sqlite 는
        OperationalError 가 문법 오류·no-such-table 을 포함하고(3.45.1 실증)
        in-process 라 죽음 모드 자체가 없다 -- 항상 False(기존 동작 완전 무변경).
        _url 이 없으면(직접 생성) 재연결 재료가 없다 -- 역시 False."""
        if self.dialect != "postgresql" or self._url is None:
            return False
        import psycopg  # 게이트 통과 시점에만 필요 -- sqlite 경로는 영원히 안 든다
        if not isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
            return False
        return bool(getattr(self._conn, "closed", False))

    def _run(self, sql: str, params: dict | None):
        """execute/query 의 단일 실행 지점(§2.2). 죽음이면 재연결 후 같은 문장을
        **정확히 1회** 재시도한다(백오프 없음): RLock 이 전 스레드를 직렬화하므로
        락 안 대기는 API 전체 정지다. 지속 장애는 재시도로 이길 문제가 아니라
        readyz 503 과 자기 종료(§2.4)의 몫이다. 트랜잭션 안(_txn_depth > 0)은
        재시도 금지(§2.3)."""
        with self._lock:
            adapted = self._adapt(sql)
            try:
                return self._conn.execute(adapted, params or {})
            except Exception as exc:
                if self._txn_depth > 0 or not self._connection_is_dead(exc):
                    raise
                self._reconnect(cause=exc)
                return self._conn.execute(adapted, params or {})

    def _reconnect(self, cause) -> None:
        """구 커넥션 close(실패 무시) 후 _open 재수행. 실패는 재연결 예외가 아니라
        **원 예외(cause)를** chain 해 전파한다(§4) -- 원 오류가 가려지면 진단이
        흐려지고, readyz 는 어느 쪽이든 503 으로 정직하다."""
        started = time.monotonic()
        try:
            self._conn.close()
        except Exception:
            pass  # 이미 죽은 커넥션 -- close 실패에 정보가 없다
        try:
            self._conn = type(self)._open(self._url)[0]
        except Exception as exc:
            print(f"db reconnect failed dialect={self.dialect} "
                  f"cause={type(cause).__name__}: {exc}", file=sys.stderr)
            raise cause from exc
        self.reconnect_count += 1
        self.last_reconnect_at = utc_now_iso()
        # 조용한 재연결 금지(§2.6): 재연결이 조용하면 "DB 가 자주 끊긴다"는
        # 상류 문제가 숨는다. kubectl logs 가 1차 창구다.
        print(f"db reconnected dialect={self.dialect} "
              f"cause={type(cause).__name__} "
              f"elapsed_ms={int((time.monotonic() - started) * 1000)} "
              f"count={self.reconnect_count}", file=sys.stderr)
        if self.on_reconnect is not None and not self._in_reconnect_hook:
            self._in_reconnect_hook = True  # 훅의 INSERT 중 또 죽어도 연쇄는 1단
            try:
                self.on_reconnect()
            except Exception as exc:  # 훅은 관측이다 -- 재연결 성공을 못 뒤집는다
                print(f"db reconnect hook failed: {exc}", file=sys.stderr)
            finally:
                self._in_reconnect_hook = False

    def execute(self, sql: str, params: dict | None = None) -> None:
        self._run(sql, params)

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        with self._lock:  # fetchall 까지 같은 락 -- 커서는 커넥션과 한 몸이다
            cur = self._run(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params: dict | None = None) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self):
        # 경계 의미론(BEGIN 재시도·죽음 시 ROLLBACK 생략)은 Task 2 -- 여기서는
        # 깊이 계수만 유지해 "트랜잭션 안 문장은 재시도 금지"(§2.3)를 먼저 세운다.
        with self._lock:
            self._conn.execute("BEGIN")
            self._txn_depth += 1
            try:
                yield self
            except BaseException:
                self._txn_depth -= 1
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._txn_depth -= 1
                self._conn.execute("COMMIT")


def dump_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def load_json(text):
    return json.loads(text) if text else None
