from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator
from urllib.parse import urlparse


class DatabaseError(RuntimeError):
    pass


class UnsupportedDatabaseUrl(DatabaseError):
    pass


@dataclass(frozen=True)
class PoolConfig:
    """Connection-pool sizing + per-session safety timeouts (PostgreSQL only).

    ``statement_timeout`` and ``idle_in_transaction_session_timeout`` are pushed as
    libpq ``options`` on every pooled connection so a single runaway query or a
    leaked open transaction cannot pin a backend forever (the failure mode that
    exhausted ``max_connections`` and took the API down). They are NOT applied to
    unpooled connections (migrations / bulk maintenance) so large ``CREATE INDEX``
    builds are never killed mid-flight.
    """

    min_size: int = 1
    max_size: int = 8
    timeout: float = 10.0
    statement_timeout_ms: int = 30000
    idle_in_txn_timeout_ms: int = 60000


# Process-wide pool registry, keyed by connection URL. The frozen Database(url)
# stays value-like; the heavyweight, long-lived pool is owned here so a process
# creates at most ONE pool per URL no matter how many Database instances or
# concurrent first-connects (sync API handlers run in the anyio threadpool) race.
_POOL_REGISTRY: dict[str, Any] = {}
_POOL_LOCK = threading.Lock()


def _get_pool(url: str, config: PoolConfig) -> Any:
    """Return the process-wide pool for ``url``, creating it once (thread-safe)."""
    pool = _POOL_REGISTRY.get(url)
    if pool is not None:
        return pool
    with _POOL_LOCK:
        # Double-checked locking: another threadpool worker may have created it
        # while we waited on the lock.
        pool = _POOL_REGISTRY.get(url)
        if pool is not None:
            return pool
        try:
            import psycopg_pool
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise DatabaseError(
                "PostgreSQL connection pooling requires the postgres extra: "
                "pip install 'dms[postgres]'"
            ) from exc
        # Clamp so a misconfigured min_size > max_size cannot crash pool creation.
        min_size = max(0, min(config.min_size, config.max_size))
        options = (
            f"-c statement_timeout={config.statement_timeout_ms} "
            f"-c idle_in_transaction_session_timeout={config.idle_in_txn_timeout_ms}"
        )
        pool = psycopg_pool.ConnectionPool(
            url,
            min_size=min_size,
            max_size=config.max_size,
            timeout=config.timeout,
            kwargs={"row_factory": dict_row, "options": options},
            open=False,
        )
        # open=False + explicit open() avoids the implicit-open deprecation warning
        # and, with the default wait=False, does NOT block startup if PG is slow.
        pool.open()
        _POOL_REGISTRY[url] = pool
        return pool


def close_all_pools() -> None:
    """Close every pool this process created. Best-effort shutdown hook."""
    with _POOL_LOCK:
        for pool in _POOL_REGISTRY.values():
            try:
                pool.close()
            except Exception:  # noqa: BLE001 - shutdown must not raise.
                pass
        _POOL_REGISTRY.clear()


@dataclass(frozen=True)
class Database:
    """Small DB-API wrapper.

    Keeps SQL portable enough for SQLite tests and PostgreSQL
    deployments. PostgreSQL connections use psycopg when that optional extra is
    installed, drawn from a bounded process-wide pool (see ``PoolConfig``).
    """

    url: str
    pool_config: PoolConfig = PoolConfig()

    @contextmanager
    def connect(self, *, pooled: bool = True) -> Iterator[Any]:
        """Yield a connection with commit-on-clean-exit / rollback-on-exception.

        ``pooled`` is PostgreSQL-only. When True (default) the connection is checked
        out from the bounded process-wide pool and carries the session timeouts. When
        False a fresh, un-timed connection is opened and closed (used by migrations /
        bulk maintenance whose large statements must not be killed). The SQLite path
        ignores ``pooled`` entirely and is byte-for-byte unchanged from before pooling.
        """
        parsed = urlparse(self.url)
        if parsed.scheme == "sqlite":
            path = self._sqlite_path(parsed)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            return
        if parsed.scheme in {"postgresql", "postgres"}:
            if pooled:
                pool = _get_pool(self.url, self.pool_config)
                # pool.connection() checks a conn out and returns it to the pool on
                # exit. We keep the SAME explicit commit/rollback the repositories
                # rely on around the yield so transaction semantics are byte-for-byte
                # identical to the unpooled code (the pool's own reset on return is a
                # harmless no-op after our explicit commit/rollback).
                with pool.connection() as connection:
                    try:
                        yield PostgresConnection(connection)
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                return
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise DatabaseError(
                    "PostgreSQL URLs require installing the postgres extra: "
                    "pip install 'dms[postgres]'"
                ) from exc
            # Explicitly DISABLE the session timeouts for unpooled connections. The
            # ALTER ROLE belt-and-suspenders defaults (init.sql) would otherwise apply
            # here too and kill a large CREATE INDEX; pooled=False exists precisely so
            # migrations / bulk maintenance run un-timed.
            connection = psycopg.connect(
                self.url,
                row_factory=dict_row,
                options=(
                    "-c statement_timeout=0 "
                    "-c idle_in_transaction_session_timeout=0"
                ),
            )
            try:
                yield PostgresConnection(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            return
        raise UnsupportedDatabaseUrl(f"unsupported database URL: {self.url}")

    @staticmethod
    def _sqlite_path(parsed: Any) -> str:
        if parsed.path in {"", "/:memory:"}:
            return ":memory:"
        if parsed.netloc and parsed.netloc != ".":
            return f"{parsed.netloc}{parsed.path}"
        path = parsed.path
        if path.startswith("/./"):
            path = path[1:]
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        return path


class PostgresConnection:
    """Translate qmark placeholders used by the repository to psycopg."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> Any:
        return self._connection.execute(sql.replace("?", "%s"), parameters)

    def executemany(self, sql: str, parameters_seq: list[Any]) -> Any:
        translated = sql.replace("?", "%s")
        with self._connection.cursor() as cur:
            cur.executemany(translated, parameters_seq)
            return cur

    def executescript(self, script: str) -> None:
        # Strip SQL line comments BEFORE splitting on ';'. A ';' inside a `-- ...`
        # comment would otherwise split a statement and the comment tail would be
        # executed as bogus SQL (this is the PostgreSQL path only — SQLite uses the
        # native, comment-aware sqlite3 executescript). The schema is plain DDL with
        # no '--' inside string literals, so cutting each line at its first '--' is
        # safe and keeps multi-statement migrations robust to commentary.
        cleaned = "\n".join(
            (line if (i := line.find("--")) == -1 else line[:i])
            for line in script.splitlines()
        )
        statements = [statement.strip() for statement in cleaned.split(";")]
        for statement in statements:
            if statement:
                self.execute(statement)
