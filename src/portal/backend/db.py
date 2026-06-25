"""Portal persistence (PostgreSQL via psycopg3 async).

The portal's OWN store, isolated in a dedicated schema (``settings.db_schema``,
default ``portal``) of PORTAL_DB_URL. On the testbed this is the DMS Postgres
(``dms`` db) with a ``portal`` schema — ``dms_app`` lacks CREATE DATABASE, so a
schema is used; switch PORTAL_DB_URL to a dedicated ``dms_portal`` db once an
admin creates one. Holds:
  - operator_users : id/password login store (seeded from PORTAL_OPERATOR_USERS)
  - backup_batches  : a registered list of sync requests (data-backup feature)
  - backup_requests : the individual sync requests of a batch (up to a few thousand)

Schema + tables are created on startup (idempotent). Passwords are salted PBKDF2
hashes (stdlib), never plaintext.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .config import Settings

# --- password hashing (stdlib PBKDF2) ---------------------------------------

_PBKDF2_ITERS = 240_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$", 3)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), int(iters)
    )
    return hmac.compare_digest(dk.hex(), hash_hex)


# --- terminal / phase state sets --------------------------------------------

REQUEST_TERMINAL = {"succeeded", "failed", "cancelled"}
REQUEST_PREVIEW_DONE = {"preview_ready", "preview_failed", "cancelled"}


def _ddl(schema: str) -> list[str]:
    s = f'"{schema}"'
    return [
        f"CREATE SCHEMA IF NOT EXISTS {s}",
        f"""CREATE TABLE IF NOT EXISTS {s}.operator_users (
            username text PRIMARY KEY,
            password_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE IF NOT EXISTS {s}.backup_batches (
            id text PRIMARY KEY,
            name text NOT NULL,
            status text NOT NULL DEFAULT 'draft',
            delete_enabled boolean NOT NULL DEFAULT false,
            options jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            requester_id text NOT NULL DEFAULT 'root',
            created_by text,
            note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE IF NOT EXISTS {s}.backup_requests (
            id bigserial PRIMARY KEY,
            batch_id text NOT NULL REFERENCES {s}.backup_batches(id) ON DELETE CASCADE,
            src_storage text NOT NULL,
            src_path text NOT NULL,
            dst_storage text NOT NULL,
            dst_path text NOT NULL,
            state text NOT NULL DEFAULT 'registered',
            dms_request_id text,
            dms_job_id text,
            fingerprint text,
            preview jsonb,
            result jsonb,
            error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"CREATE INDEX IF NOT EXISTS backup_requests_batch_state "
        f"ON {s}.backup_requests(batch_id, state)",
    ]


class Database:
    """Async Postgres pool + schema bootstrap. search_path points at the portal schema."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self.schema = settings.db_schema
        self.pool: AsyncConnectionPool | None = None

    @property
    def configured(self) -> bool:
        return self.pool is not None

    async def open(self) -> None:
        if not self._settings.db_configured:
            return
        self.pool = AsyncConnectionPool(
            conninfo=self._settings.db_url,
            min_size=1,
            max_size=10,
            open=False,
            kwargs={
                "row_factory": dict_row,
                "autocommit": True,
                "options": f"-c search_path={self.schema},public",
            },
        )
        await self.pool.open()
        await self._bootstrap()

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def _bootstrap(self) -> None:
        async with self.pool.connection() as conn:
            for stmt in _ddl(self.schema):
                await conn.execute(stmt)
        await self._seed_operator_users()

    async def _seed_operator_users(self) -> None:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM operator_users")
            row = await cur.fetchone()
            if row and row["n"]:
                return
            for username, password in self._settings.operator_users.items():
                await conn.execute(
                    "INSERT INTO operator_users(username, password_hash) VALUES (%s,%s) "
                    "ON CONFLICT (username) DO NOTHING",
                    (username, hash_password(password)),
                )

    # --- operator login -------------------------------------------------

    async def operator_password_hash(self, username: str) -> str | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT password_hash FROM operator_users WHERE username=%s", (username,)
            )
            row = await cur.fetchone()
            return row["password_hash"] if row else None

    # --- backup batches -------------------------------------------------

    async def create_batch(
        self,
        *,
        batch_id: str,
        name: str,
        delete_enabled: bool,
        options: dict[str, Any],
        requester_id: str,
        created_by: str | None,
        note: str | None,
    ) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO backup_batches"
                "(id,name,status,delete_enabled,options,requester_id,created_by,note) "
                "VALUES (%s,%s,'draft',%s,%s,%s,%s,%s)",
                (
                    batch_id,
                    name,
                    delete_enabled,
                    Jsonb(options),
                    requester_id,
                    created_by,
                    note,
                ),
            )

    async def list_batches(self) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """SELECT b.*,
                    (SELECT count(*) FROM backup_requests j WHERE j.batch_id=b.id) AS request_count,
                    (SELECT count(*) FROM backup_requests j WHERE j.batch_id=b.id
                        AND j.state='succeeded') AS succeeded_count,
                    (SELECT count(*) FROM backup_requests j WHERE j.batch_id=b.id
                        AND j.state IN ('failed','preview_failed')) AS failed_count
                   FROM backup_batches b ORDER BY b.created_at DESC"""
            )
            return await cur.fetchall()

    async def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM backup_batches WHERE id=%s", (batch_id,)
            )
            return await cur.fetchone()

    async def batch_state_counts(self, batch_id: str) -> dict[str, int]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT state, count(*) AS n FROM backup_requests WHERE batch_id=%s "
                "GROUP BY state",
                (batch_id,),
            )
            return {r["state"]: r["n"] for r in await cur.fetchall()}

    async def preview_totals(self, batch_id: str) -> dict[str, int]:
        """Aggregate files/bytes across preview_ready jobs (for batch review)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT coalesce(sum((preview->>'files')::bigint),0) AS files, "
                "coalesce(sum((preview->>'bytes')::bigint),0) AS bytes "
                "FROM backup_requests WHERE batch_id=%s AND state='preview_ready'",
                (batch_id,),
            )
            row = await cur.fetchone()
            return {"files": int(row["files"]), "bytes": int(row["bytes"])}

    async def set_batch_status(self, batch_id: str, status: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE backup_batches SET status=%s, updated_at=now() WHERE id=%s",
                (status, batch_id),
            )

    async def delete_batch(self, batch_id: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("DELETE FROM backup_batches WHERE id=%s", (batch_id,))

    async def active_batches(self) -> list[dict[str, Any]]:
        """Batches the orchestrator must drive (previewing / running)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM backup_batches WHERE status IN ('previewing','running')"
            )
            return await cur.fetchall()

    # --- backup requests ------------------------------------------------

    async def add_requests(self, batch_id: str, rows: list[dict[str, str]]) -> int:
        """Bulk-insert registered requests. rows: src_storage/src_path/dst_storage/dst_path."""
        if not rows:
            return 0
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO backup_requests"
                    "(batch_id,src_storage,src_path,dst_storage,dst_path,state) "
                    "VALUES (%s,%s,%s,%s,%s,'registered')",
                    [
                        (
                            batch_id,
                            r["src_storage"],
                            r["src_path"],
                            r["dst_storage"],
                            r["dst_path"],
                        )
                        for r in rows
                    ],
                )
        return len(rows)

    async def list_requests(
        self,
        batch_id: str,
        *,
        state: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clause = "WHERE batch_id=%s" + (" AND state=%s" if state else "")
        params: list[Any] = [batch_id] + ([state] if state else []) + [limit, offset]
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT * FROM backup_requests {clause} ORDER BY id LIMIT %s OFFSET %s",
                params,
            )
            return await cur.fetchall()

    async def claim_requests(
        self, batch_id: str, from_state: str, to_state: str, limit: int
    ) -> list[dict[str, Any]]:
        """Atomically move up to `limit` requests from from_state -> to_state, returning them."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE backup_requests SET state=%s, updated_at=now() WHERE id IN "
                "(SELECT id FROM backup_requests WHERE batch_id=%s AND state=%s "
                " ORDER BY id FOR UPDATE SKIP LOCKED LIMIT %s) RETURNING *",
                (to_state, batch_id, from_state, limit),
            )
            return await cur.fetchall()

    async def requests_in_states(
        self, batch_id: str, states: list[str]
    ) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM backup_requests WHERE batch_id=%s AND state = ANY(%s) "
                "ORDER BY id",
                (batch_id, states),
            )
            return await cur.fetchall()

    async def cancel_requests(self, batch_id: str) -> list[str]:
        """Cancel all non-terminal requests of a batch; return the dms_job_ids that
        were in flight (so the caller can cancel them in DMS too)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE backup_requests SET state='cancelled', updated_at=now() "
                "WHERE batch_id=%s AND state NOT IN "
                "('succeeded','failed','cancelled','preview_failed') "
                "RETURNING dms_job_id",
                (batch_id,),
            )
            return [r["dms_job_id"] for r in await cur.fetchall() if r["dms_job_id"]]

    async def update_request(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = []
        params: list[Any] = []
        for k, v in fields.items():
            cols.append(f"{k}=%s")
            params.append(Jsonb(v) if k in ("preview", "result") else v)
        params.append(job_id)
        async with self.pool.connection() as conn:
            await conn.execute(
                f"UPDATE backup_requests SET {', '.join(cols)}, updated_at=now() WHERE id=%s",
                params,
            )
