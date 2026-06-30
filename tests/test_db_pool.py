"""Tests for the PostgreSQL connection-pool / statement_timeout wiring in db.py.

The full suite runs on SQLite, so these tests deliberately exercise the
postgres code paths with fakes (no live PostgreSQL): they assert that
``connect(pooled=...)`` selects the right path, that the pool is created exactly
once per URL, that transaction semantics (commit on clean exit / rollback on
exception) are preserved, and that the SQLite path is unaffected.
"""

from __future__ import annotations

from contextlib import contextmanager

import psycopg
import psycopg_pool
import pytest

import dms.db as dbmod
from dms.config import Settings
from dms.db import Database, PoolConfig


class FakeConn:
    def __init__(self) -> None:
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1

    def close(self) -> None:
        self.closed += 1


class FakePool:
    instances: list["FakePool"] = []

    def __init__(self, conninfo, *, min_size, max_size, timeout, kwargs, open) -> None:
        self.conninfo = conninfo
        self.min_size = min_size
        self.max_size = max_size
        self.timeout = timeout
        self.kwargs = kwargs
        self.open_flag = open
        self.opened = False
        self.closed = False
        self.checkouts = 0
        self._conn = FakeConn()
        FakePool.instances.append(self)

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    @contextmanager
    def connection(self):
        self.checkouts += 1
        yield self._conn


@pytest.fixture(autouse=True)
def _reset_pool_state(monkeypatch):
    dbmod.close_all_pools()
    FakePool.instances.clear()
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", FakePool)
    yield
    dbmod._POOL_REGISTRY.clear()
    FakePool.instances.clear()


# --- SQLite path is unchanged by the pool work --------------------------------


@pytest.mark.parametrize("pooled", [True, False])
def test_sqlite_path_unaffected(tmp_path, pooled):
    db = Database(f"sqlite:///{tmp_path / 'x.db'}")
    with db.connect(pooled=pooled) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES (?)", ("hello",))
    # committed on clean exit -> visible in a fresh connection
    with db.connect() as conn:
        row = conn.execute("SELECT v FROM t").fetchone()
    assert row["v"] == "hello"
    # no pool is ever created for sqlite
    assert FakePool.instances == []


def test_sqlite_rollback_on_exception(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'y.db'}")
    with db.connect() as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    with pytest.raises(RuntimeError):
        with db.connect() as conn:
            conn.execute("INSERT INTO t (v) VALUES (?)", ("nope",))
            raise RuntimeError("boom")
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM t").fetchone()["n"]
    assert count == 0


# --- pooled=True: pool path, created once, txn semantics ----------------------


def test_pooled_true_creates_pool_once_per_url():
    db = Database(
        "postgresql://u:p@h:5432/d",
        pool_config=PoolConfig(min_size=1, max_size=4, statement_timeout_ms=30000),
    )
    with db.connect() as conn:
        assert isinstance(conn, dbmod.PostgresConnection)
    with db.connect() as conn:
        assert isinstance(conn, dbmod.PostgresConnection)

    assert len(FakePool.instances) == 1
    pool = FakePool.instances[0]
    assert pool.checkouts == 2
    assert pool.opened is True
    assert pool.open_flag is False  # open=False then explicit open()
    assert pool.max_size == 4
    # statement_timeout / idle timeout pushed as libpq options
    assert "statement_timeout=30000" in pool.kwargs["options"]
    assert "idle_in_transaction_session_timeout" in pool.kwargs["options"]
    # commit on clean exit (called on the raw pooled connection)
    assert pool._conn.committed == 2
    assert pool._conn.rolled_back == 0
    # pooled connection is NOT closed (returned to the pool)
    assert pool._conn.closed == 0


def test_pooled_true_commit_and_rollback():
    db = Database("postgresql://u:p@h:5432/commitdb", pool_config=PoolConfig())
    with db.connect():
        pass
    pool = FakePool.instances[0]
    assert pool._conn.committed == 1
    assert pool._conn.rolled_back == 0

    with pytest.raises(RuntimeError):
        with db.connect():
            raise RuntimeError("boom")
    # rolled back on exception, no extra commit
    assert pool._conn.committed == 1
    assert pool._conn.rolled_back == 1


def test_separate_urls_get_separate_pools():
    a = Database("postgresql://u:p@h:5432/a")
    b = Database("postgresql://u:p@h:5432/b")
    with a.connect():
        pass
    with b.connect():
        pass
    assert len(FakePool.instances) == 2
    assert {p.conninfo for p in FakePool.instances} == {
        "postgresql://u:p@h:5432/a",
        "postgresql://u:p@h:5432/b",
    }


# --- pooled=False: raw connection, no pool, timeouts disabled -----------------


def test_pooled_false_uses_raw_connect_not_pool(monkeypatch):
    captured: dict[str, object] = {}
    fake = FakeConn()

    def fake_connect(url, *, row_factory, options):
        captured["url"] = url
        captured["options"] = options
        return fake

    monkeypatch.setattr(psycopg, "connect", fake_connect)

    db = Database("postgresql://u:p@h:5432/migr")
    with db.connect(pooled=False) as conn:
        assert isinstance(conn, dbmod.PostgresConnection)

    # raw psycopg.connect was used, NOT the pool
    assert FakePool.instances == []
    assert captured["url"] == "postgresql://u:p@h:5432/migr"
    # statement_timeout explicitly DISABLED so big CREATE INDEX is not killed
    assert "statement_timeout=0" in captured["options"]
    assert "idle_in_transaction_session_timeout=0" in captured["options"]
    # commit on clean exit + explicit close (raw connection, not pooled)
    assert fake.committed == 1
    assert fake.closed == 1


def test_pooled_false_rolls_back_and_closes_on_exception(monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr(
        psycopg,
        "connect",
        lambda url, *, row_factory, options: fake,
    )
    db = Database("postgresql://u:p@h:5432/migr2")
    with pytest.raises(RuntimeError):
        with db.connect(pooled=False):
            raise RuntimeError("boom")
    assert fake.committed == 0
    assert fake.rolled_back == 1
    assert fake.closed == 1


# --- Settings parse the new pool env vars -------------------------------------


def test_settings_pool_defaults(monkeypatch):
    for var in (
        "DMS_DB_POOL_MIN_SIZE",
        "DMS_DB_POOL_MAX_SIZE",
        "DMS_DB_OBSERVABILITY_POOL_MAX_SIZE",
        "DMS_DB_POOL_TIMEOUT_SECONDS",
        "DMS_DB_STATEMENT_TIMEOUT_MS",
        "DMS_DB_IDLE_IN_TXN_TIMEOUT_MS",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DMS_DATABASE_URL", "sqlite:///./t.db")
    settings = Settings.from_env()
    assert settings.db_pool_min_size == 1
    assert settings.db_pool_max_size == 4
    assert settings.db_api_pool_max_size == 16
    assert settings.db_observability_pool_max_size == 3
    # checkout wait must be >= statement_timeout (30s) so a waiter outlasts one slow query
    assert settings.db_pool_timeout_seconds == 35.0
    assert settings.db_pool_timeout_seconds * 1000 >= settings.db_statement_timeout_ms
    assert settings.db_statement_timeout_ms == 30000
    assert settings.db_idle_in_txn_timeout_ms == 60000

    op = settings.operational_pool_config()  # worker (default): small pool
    api_op = settings.operational_pool_config(role="api")  # concurrent: larger pool
    obs = settings.observability_pool_config()
    assert op.max_size == 4
    assert api_op.max_size == 16
    assert obs.max_size == 3
    # worst-case ceiling: api*2*(16+3) + 5 loops*(4+3) = 38 + 35 = 73 < max_connections(100)-reserved(3)
    assert 2 * (api_op.max_size + obs.max_size) + 5 * (op.max_size + obs.max_size) < 97


def test_settings_pool_env_overrides(monkeypatch):
    monkeypatch.setenv("DMS_DATABASE_URL", "sqlite:///./t.db")
    monkeypatch.setenv("DMS_DB_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("DMS_DB_POOL_MAX_SIZE", "12")
    monkeypatch.setenv("DMS_DB_API_POOL_MAX_SIZE", "40")
    monkeypatch.setenv("DMS_DB_OBSERVABILITY_POOL_MAX_SIZE", "4")
    monkeypatch.setenv("DMS_DB_POOL_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("DMS_DB_STATEMENT_TIMEOUT_MS", "45000")
    monkeypatch.setenv("DMS_DB_IDLE_IN_TXN_TIMEOUT_MS", "90000")
    settings = Settings.from_env()

    op = settings.operational_pool_config()
    assert (op.min_size, op.max_size, op.timeout) == (2, 12, 7.5)
    assert op.statement_timeout_ms == 45000
    assert op.idle_in_txn_timeout_ms == 90000
    # role="api" picks db_api_pool_max_size
    assert settings.operational_pool_config(role="api").max_size == 40

    obs = settings.observability_pool_config()
    # obs min_size is clamped to <= obs max_size
    assert obs.min_size == 2
    assert obs.max_size == 4


def test_observability_min_size_clamped_below_max(monkeypatch):
    monkeypatch.setenv("DMS_DATABASE_URL", "sqlite:///./t.db")
    monkeypatch.setenv("DMS_DB_POOL_MIN_SIZE", "5")
    monkeypatch.setenv("DMS_DB_OBSERVABILITY_POOL_MAX_SIZE", "3")
    settings = Settings.from_env()
    obs = settings.observability_pool_config()
    assert obs.min_size == 3  # clamped from 5 down to max_size
    assert obs.max_size == 3


def test_postgres_executescript_ignores_semicolons_in_comments():
    """A ';' inside a SQL line comment must NOT split a statement (the PostgreSQL
    executescript path). Regression: a `-- ...; ...` comment in the migration schema
    previously executed the comment tail as bogus SQL and broke `dms migrate` on
    PostgreSQL (SQLite's native executescript is comment-aware, so the test suite
    missed it). This exercises the PG path directly with a fake connection."""
    from dms.db import PostgresConnection

    executed: list[str] = []

    class FakeConn:
        def execute(self, sql, parameters=()):
            executed.append(sql)
            return None

    PostgresConnection(FakeConn()).executescript(
        "-- a comment with a semicolon; index it so that this must NOT run\n"
        "CREATE TABLE t (a TEXT);\n"
        "CREATE INDEX idx ON t(a);  -- trailing comment; also ignored\n"
    )
    assert any("CREATE TABLE t" in s for s in executed)
    assert any("CREATE INDEX idx" in s for s in executed)
    # the comment tails must never reach the DB
    assert not any("index it so that" in s for s in executed)
    assert not any("also ignored" in s for s in executed)
