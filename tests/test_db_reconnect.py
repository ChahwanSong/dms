"""슬라이스 22: Database 재연결(설계 §2.1/§2.2).

실 PG 하니스가 없는 저장소다(tests/test_migrations.py 의 _FakeDb 선례). 게다가
venv 에 psycopg 자체가 없다(ModuleNotFoundError 실측) -- 죽음 판정의 isinstance
게이트를 통과시키려면 psycopg 예외 계층의 페이크 **모듈**을 sys.modules 에
주입해 db.py 의 lazy `import psycopg` 를 가로채야 한다(선택이 아니라 필수).
커넥션 대역은 "전송 실패 + closed=True"(pgconn status BAD)를 스크립트한다 --
이번 사건 계열(이미 죽은 커넥션에 전송 시도 -> 즉시 실패)의 최소 재현이다.
sqlite 는 실 DB 로 "재연결이 **없는지**"를 고정한다(§2.1: OperationalError 가
문법 오류를 포함해 죽음 신호로 쓸 수 없고, in-process 라 죽음 모드도 없다)."""
import sqlite3
import sys
import types

import pytest

from dms.db import Database


# psycopg 3 예외 계층의 최소 대역(3.3.4 실증 §1-3: Operational/Programming 은
# DatabaseError 하위, InterfaceError 는 Error 직속). DiskFull 도 OperationalError
# 하위라는 함정이 "클래스 + closed 이중 게이트"의 존재 이유다.
class FakePsycopgError(Exception):
    pass


class FakeInterfaceError(FakePsycopgError):
    pass


class FakeDatabaseError(FakePsycopgError):
    pass


class FakeOperationalError(FakeDatabaseError):
    pass


class FakeProgrammingError(FakeDatabaseError):
    pass


def _fake_psycopg_module():
    mod = types.ModuleType("psycopg")
    mod.Error = FakePsycopgError
    mod.InterfaceError = FakeInterfaceError
    mod.DatabaseError = FakeDatabaseError
    mod.OperationalError = FakeOperationalError
    mod.ProgrammingError = FakeProgrammingError
    return mod


class _Cursor:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return self._rows


class _FakePgConn:
    """psycopg Connection 대역. fail_on 의 부분문자열을 담은 SQL 을 만나면 그
    항목을 소진하고 예외를 던진 뒤 closed=True 로 넘어간다(mark_closed=False 면
    DiskFull 처럼 "살아있는 커넥션의 서버 오류"가 된다). executed 가 재시도
    횟수의 증거, raised 가 원 예외 보존(identity) 검증의 증거다."""

    def __init__(self, fail_on=(), exc=None, mark_closed=True, rows=()):
        self.executed = []
        self.raised = []
        self.closed = False
        self.close_calls = 0
        self.fail_on = list(fail_on)
        self._exc = exc or (lambda: FakeOperationalError("connection lost"))
        self._mark_closed = mark_closed
        self._rows = rows

    def execute(self, sql, params=None):
        self.executed.append(sql)
        for i, mark in enumerate(self.fail_on):
            if mark in sql:
                del self.fail_on[i]
                if self._mark_closed:
                    self.closed = True
                error = self._exc()
                self.raised.append(error)
                raise error
        return _Cursor(self._rows)

    def close(self):
        self.close_calls += 1
        self.closed = True


def _pg_db(monkeypatch, conn, next_conns=()):
    """직접 생성 + _url 부여 + _open 을 대역 팩토리로 치환한 postgresql Database.
    재연결이 일어나면 next_conns 에서 차례로 새 커넥션을 꺼내 쓴다."""
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg_module())
    db = Database(conn, "postgresql")
    db._url = "postgresql://test-only"
    pool = iter(next_conns)
    monkeypatch.setattr(Database, "_open",
                        staticmethod(lambda url: (next(pool), "postgresql")))
    return db


# ---- §2.2 단독 문장: 재연결 + 정확히 1회 재시도 ----

def test_connect_stores_the_url_for_reconnection(tmp_path):
    # 현행 connect() 는 URL 을 버려서 재연결 자체가 불가능했다(§1-1) -- 보관이
    # 이 슬라이스 전체의 선행 조건이다.
    url = f"sqlite:///{tmp_path}/t.db"
    assert Database.connect(url)._url == url


def test_dead_connection_standalone_execute_reconnects_and_retries_once(monkeypatch):
    old = _FakePgConn(fail_on=["INSERT"])
    new = _FakePgConn()
    db = _pg_db(monkeypatch, old, [new])
    db.execute("INSERT INTO t (a) VALUES (:a)", {"a": 1})
    assert old.executed == ["INSERT INTO t (a) VALUES (%(a)s)"]   # 원 시도 1회
    assert new.executed == ["INSERT INTO t (a) VALUES (%(a)s)"]   # 같은 문장 재시도 1회
    assert old.close_calls == 1          # 구 커넥션은 close 시도된다
    assert db.reconnect_count == 1
    assert db.last_reconnect_at is not None


def test_query_reconnects_through_the_same_single_helper(monkeypatch):
    # execute/query 가 _run 한 곳으로 모였는지(§2.2) -- 한쪽만 재연결되면 다른
    # 쪽이 사건을 재현한다.
    old = _FakePgConn(fail_on=["SELECT"])
    new = _FakePgConn(rows=[{"x": 1}])
    db = _pg_db(monkeypatch, old, [new])
    assert db.query("SELECT 1 AS x") == [{"x": 1}]
    assert db.reconnect_count == 1


def test_interface_error_with_closed_connection_also_counts_as_death(monkeypatch):
    # InterfaceError 는 Error 직속(§1-3) -- Operational 만 잡으면 이 절반을 놓친다.
    old = _FakePgConn(fail_on=["SELECT"],
                      exc=lambda: FakeInterfaceError("connection is closed"))
    new = _FakePgConn(rows=[{"x": 1}])
    db = _pg_db(monkeypatch, old, [new])
    assert db.query("SELECT 1 AS x") == [{"x": 1}]
    assert db.reconnect_count == 1


def test_retry_failure_propagates_and_stops_at_exactly_two_attempts(monkeypatch):
    # 재시도 1회·백오프 없음(§2.2): RLock 이 전 스레드를 직렬화하므로 락 안
    # 대기는 API 전체 정지다. 지속 장애는 readyz 503 + 자기 종료(§2.4)의 몫이다.
    old = _FakePgConn(fail_on=["SELECT"])
    new = _FakePgConn(fail_on=["SELECT"])   # 재연결 후에도 죽는 지속 장애
    db = _pg_db(monkeypatch, old, [new])
    with pytest.raises(FakeOperationalError):
        db.query("SELECT 1 AS x")
    assert len(old.executed) == 1 and len(new.executed) == 1   # 1+1, 루프 없음
    assert db.reconnect_count == 1   # 재연결은 성공했고, 재시도 실패가 그대로 전파됐다


def test_operational_error_on_a_live_connection_is_not_retried(monkeypatch):
    # DiskFull·ConnectionTimeout·AdminShutdown 은 전부 OperationalError 하위다
    # (§1-3) -- closed 게이트가 없으면 디스크 가득참까지 재시도한다.
    conn = _FakePgConn(fail_on=["INSERT"], mark_closed=False)
    db = _pg_db(monkeypatch, conn, [])
    with pytest.raises(FakeOperationalError):
        db.execute("INSERT INTO t (a) VALUES (1)")
    assert len(conn.executed) == 1
    assert db.reconnect_count == 0


def test_programming_error_is_not_retried_even_when_closed(monkeypatch):
    # 문법 오류는 ProgrammingError 계열(§1-3) -- 클래스 게이트가 거른다. closed
    # 여도 마찬가지: 죽음 판정은 두 게이트의 교집합이다.
    conn = _FakePgConn(fail_on=["SELEC"],
                       exc=lambda: FakeProgrammingError("syntax error"))
    db = _pg_db(monkeypatch, conn, [])
    with pytest.raises(FakeProgrammingError):
        db.execute("SELEC 1")
    assert db.reconnect_count == 0


def test_directly_constructed_database_never_reconnects(monkeypatch):
    # connect() 를 거치지 않은 인스턴스(_url=None, 테스트 더블 관례)는 재연결
    # 재료가 없다 -- 죽음 처리 없이 원 예외 전파가 계약이다.
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg_module())
    conn = _FakePgConn(fail_on=["SELECT"])
    db = Database(conn, "postgresql")
    with pytest.raises(FakeOperationalError):
        db.query("SELECT 1")
    assert db.reconnect_count == 0


def test_reconnect_failure_propagates_the_original_exception_chained(monkeypatch):
    # §4: DB 가 완전히 죽어 재연결도 안 되면 -- 원 예외가 주인공이고 재연결
    # 실패는 __cause__ 로만 남는다. readyz 는 어느 쪽이든 503 으로 정직하다.
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg_module())
    conn = _FakePgConn(fail_on=["SELECT"])
    db = Database(conn, "postgresql")
    db._url = "postgresql://test-only"

    def down(url):
        raise RuntimeError("pg still down")

    monkeypatch.setattr(Database, "_open", staticmethod(down))
    with pytest.raises(FakeOperationalError) as e:
        db.query("SELECT 1")
    assert e.value is conn.raised[-1]                     # 원 예외 보존
    assert isinstance(e.value.__cause__, RuntimeError)    # 재연결 실패는 chain 으로
    assert db.reconnect_count == 0


def test_reconnect_leaves_a_stderr_line(monkeypatch, capsys):
    # §2.6: 조용한 재연결 금지 -- "DB 가 자주 끊긴다"는 상류 문제가 숨는다.
    # kubectl logs 가 1차 창구다.
    old = _FakePgConn(fail_on=["SELECT"])
    db = _pg_db(monkeypatch, old, [_FakePgConn()])
    db.query("SELECT 1 AS x")
    err = capsys.readouterr().err
    assert "db reconnected dialect=postgresql cause=FakeOperationalError" in err
    assert "count=1" in err


def test_on_reconnect_hook_fires_once_and_hook_errors_are_harmless(monkeypatch):
    # §2.6 훅(Task 3 이 record_event 를 단다). record_event 는 예외를 안 올리는
    # 계약이지만, 훅이 어긴다 해도 재연결 성공을 뒤집으면 안 된다(관측 < 복구).
    old = _FakePgConn(fail_on=["INSERT"])
    db = _pg_db(monkeypatch, old, [_FakePgConn()])
    calls = []

    def hook():
        calls.append(1)
        raise RuntimeError("hook boom")

    db.on_reconnect = hook
    db.execute("INSERT INTO t (a) VALUES (1)")
    assert calls == [1]
    assert db.reconnect_count == 1


# ---- §2.1 sqlite: 재연결 없음(완전 무변경)이 계약 ----

def test_sqlite_closed_connection_error_propagates_without_reconnect(tmp_path):
    # sqlite 죽음 재현 경로: conn.close() 후 사용 -> ProgrammingError("Cannot
    # operate on a closed database", 3.45.1 실증 §1-4). 이는 코드 버그다 --
    # 재연결로 숨기지 않고 전파한다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    db._conn.close()
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT 1")
    assert db.reconnect_count == 0


def test_sqlite_syntax_error_propagates_without_reconnect(tmp_path):
    # sqlite3.OperationalError 는 문법 오류를 포함한다(§1-4) -- 죽음 신호로 쓸
    # 수 없다. 재시도 없이 그대로 전파가 계약이다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    with pytest.raises(sqlite3.OperationalError):
        db.execute("SELEC 1")
    assert db.reconnect_count == 0
