from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from urllib.parse import urlparse


class DatabaseError(RuntimeError):
    pass


class UnsupportedDatabaseUrl(DatabaseError):
    pass


@dataclass(frozen=True)
class Database:
    """Small DB-API wrapper.

    Phase 1 keeps SQL portable enough for SQLite tests and PostgreSQL
    deployments. PostgreSQL connections use psycopg when that optional extra is
    installed.
    """

    url: str

    @contextmanager
    def connect(self) -> Iterator[Any]:
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
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise DatabaseError(
                    "PostgreSQL URLs require installing the postgres extra: "
                    "pip install 'dms[postgres]'"
                ) from exc
            connection = psycopg.connect(self.url, row_factory=dict_row)
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
        statements = [statement.strip() for statement in script.split(";")]
        for statement in statements:
            if statement:
                self.execute(statement)
