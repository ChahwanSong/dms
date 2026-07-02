"""Portal persistence (PostgreSQL via psycopg3 async).

The portal's OWN store, isolated in a dedicated schema (``settings.db_schema``,
default ``portal``) of PORTAL_DB_URL. On the testbed this is the DMS Postgres
(``dms`` db) with a ``portal`` schema — ``dms_app`` lacks CREATE DATABASE, so a
schema is used; switch PORTAL_DB_URL to a dedicated ``dms_portal`` db once an
admin creates one. Holds:
  - operator_users : id/password login store (seeded from PORTAL_OPERATOR_USERS)
  - backup_batches  : a registered list of sync requests (data-backup feature)
  - backup_requests : the individual sync requests of a batch (up to a few thousand)
  - scan_batches    : a registered list of scan requests (data-scan feature)
  - scan_requests   : the individual scan requests of a batch (up to a few thousand)

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
            priority text NOT NULL DEFAULT 'Low',
            node_count int,
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
        # speeds the LEFT JOIN + GROUP BY in list_batches (join on batch_id, id PK).
        f"CREATE INDEX IF NOT EXISTS backup_requests_batch_id "
        f"ON {s}.backup_requests(batch_id, id)",
        # speeds list_batches' ORDER BY b.created_at DESC.
        f"CREATE INDEX IF NOT EXISTS backup_batches_created_at "
        f"ON {s}.backup_batches(created_at)",
        # data-scan feature: a scan batch is a SIMPLER backup batch — read-only DMS
        # scans have no preview/confirm, so a request carries a single storage+path
        # (no src/dst), no fingerprint and no preview column. Batch lifecycle is
        # draft -> scanning -> done (or cancelled).
        f"""CREATE TABLE IF NOT EXISTS {s}.scan_batches (
            id text PRIMARY KEY,
            name text NOT NULL,
            status text NOT NULL DEFAULT 'draft',
            options jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            requester_id text NOT NULL DEFAULT 'root',
            priority text NOT NULL DEFAULT 'Low',
            node_count int,
            created_by text,
            note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE IF NOT EXISTS {s}.scan_requests (
            id bigserial PRIMARY KEY,
            batch_id text NOT NULL REFERENCES {s}.scan_batches(id) ON DELETE CASCADE,
            storage text NOT NULL,
            path text NOT NULL,
            state text NOT NULL DEFAULT 'registered',
            dms_request_id text,
            dms_job_id text,
            result jsonb,
            error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"CREATE INDEX IF NOT EXISTS scan_requests_batch_state "
        f"ON {s}.scan_requests(batch_id, state)",
        # speeds the LEFT JOIN + GROUP BY in list_scan_batches (join on batch_id, id PK).
        f"CREATE INDEX IF NOT EXISTS scan_requests_batch_id "
        f"ON {s}.scan_requests(batch_id, id)",
        # speeds list_scan_batches' ORDER BY b.created_at DESC.
        f"CREATE INDEX IF NOT EXISTS scan_batches_created_at "
        f"ON {s}.scan_batches(created_at)",
        # action-required 숨김(acknowledge) layer: operator-side dismiss of obsolete
        # "조치 필요" items (DMS has no native ack). Keyed by a stable fingerprint.
        f"""CREATE TABLE IF NOT EXISTS {s}.attention_dismissals (
            fingerprint text PRIMARY KEY,
            issue_type text,
            label text,
            reason text,
            kind text NOT NULL DEFAULT 'dismissed',
            job_id text,
            request_id text,
            status text,
            item_at text,
            archived boolean NOT NULL DEFAULT false,
            dismissed_by text,
            dismissed_at timestamptz NOT NULL DEFAULT now()
        )""",
        # operator account management: disable (is_active=false blocks login) +
        # audit (who created it, when it changed). Added via ALTER so pre-existing
        # operator_users tables migrate in place.
        f"ALTER TABLE {s}.operator_users ADD COLUMN IF NOT EXISTS is_active "
        f"boolean NOT NULL DEFAULT true",
        f"ALTER TABLE {s}.operator_users ADD COLUMN IF NOT EXISTS created_by text",
        f"ALTER TABLE {s}.operator_users ADD COLUMN IF NOT EXISTS updated_at "
        f"timestamptz NOT NULL DEFAULT now()",
        # migration for pre-existing DBs: add the per-batch priority column.
        f"ALTER TABLE {s}.backup_batches ADD COLUMN IF NOT EXISTS priority text "
        f"NOT NULL DEFAULT 'Low'",
        f"ALTER TABLE {s}.backup_batches ADD COLUMN IF NOT EXISTS node_count int",
        # acknowledge layer: kind distinguishes 'ack' (운영자가 확인·수동 처리함) from
        # 'dismissed' (해당없음/숨김); job_id/request_id/status are captured so a hidden
        # item can still be DMS-deleted (terminal data job) or abandoned (stuck request).
        f"ALTER TABLE {s}.attention_dismissals ADD COLUMN IF NOT EXISTS kind text "
        f"NOT NULL DEFAULT 'dismissed'",
        f"ALTER TABLE {s}.attention_dismissals ADD COLUMN IF NOT EXISTS job_id text",
        f"ALTER TABLE {s}.attention_dismissals ADD COLUMN IF NOT EXISTS request_id text",
        f"ALTER TABLE {s}.attention_dismissals ADD COLUMN IF NOT EXISTS status text",
        # the action-required item's OWN report/updated time (captured at dismiss),
        # so 처리 내역 shows the report time like 현재 조치/과거 이력 — not the ack time.
        f"ALTER TABLE {s}.attention_dismissals ADD COLUMN IF NOT EXISTS item_at text",
        # 'archived' = 정리됨: still hidden from 조치 필요 (stays in dismissed_fingerprints)
        # but dropped from the 처리 내역 list, so "이전 정리" removes it from view WITHOUT
        # un-hiding it (un-hiding would resurface terminated jobs in 과거 작업 이력).
        f"ALTER TABLE {s}.attention_dismissals ADD COLUMN IF NOT EXISTS archived "
        f"boolean NOT NULL DEFAULT false",
        # keep the dismiss layer's queries O(screen), not O(table), as it accrues:
        # 처리 내역 list = (archived,dismissed_at) newest-first; the 액티비티/워커 hide
        # filter = request_id = ANY(...) lookup.
        f"CREATE INDEX IF NOT EXISTS attention_dismissals_active "
        f"ON {s}.attention_dismissals(archived, dismissed_at DESC)",
        f"CREATE INDEX IF NOT EXISTS attention_dismissals_request_id "
        f"ON {s}.attention_dismissals(request_id) WHERE request_id IS NOT NULL",
        # migration: the FK was auto-named backup_jobs_batch_id_fkey when the table
        # was first created as backup_jobs (before the backup_jobs->backup_requests
        # rename, which doesn't rename constraints). Rename it to match the table.
        f"""DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE table_schema = '{schema}'
              AND table_name = 'backup_requests'
              AND constraint_name = 'backup_jobs_batch_id_fkey'
          ) THEN
            ALTER TABLE {s}.backup_requests
              RENAME CONSTRAINT backup_jobs_batch_id_fkey
              TO backup_requests_batch_id_fkey;
          END IF;
        END $$;""",
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

    # --- operator login + account management ----------------------------

    async def operator_auth_record(self, username: str) -> dict[str, Any] | None:
        """Login record: hash + active flag. A disabled (is_active=false) account
        must not be able to log in."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT password_hash, is_active FROM operator_users WHERE username=%s",
                (username,),
            )
            return await cur.fetchone()

    async def create_operator(
        self, username: str, password: str, *, created_by: str
    ) -> bool:
        """Insert a new operator (PBKDF2-hashed). Returns False if the username
        already exists (caller maps to 409). Used by the token-gated login-screen
        '계정 만들기' flow."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO operator_users(username, password_hash, created_by) "
                "VALUES (%s,%s,%s) ON CONFLICT (username) DO NOTHING",
                (username, hash_password(password), created_by),
            )
            return cur.rowcount > 0

    async def set_operator_password(self, username: str, password: str) -> int:
        """Set (reset) an operator's password. Returns rows affected (0 => the
        username doesn't exist → caller maps to 404). Used by the token-gated
        login-screen '비밀번호 재설정' flow."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE operator_users SET password_hash=%s, updated_at=now() "
                "WHERE username=%s",
                (hash_password(password), username),
            )
            return cur.rowcount

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
        priority: str = "Low",
        node_count: int | None = None,
    ) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO backup_batches"
                "(id,name,status,delete_enabled,options,requester_id,priority,node_count,created_by,note) "
                "VALUES (%s,%s,'draft',%s,%s,%s,%s,%s,%s,%s)",
                (
                    batch_id,
                    name,
                    delete_enabled,
                    Jsonb(options),
                    requester_id,
                    priority,
                    node_count,
                    created_by,
                    note,
                ),
            )

    async def list_batches(self) -> list[dict[str, Any]]:
        # Single pass: LEFT JOIN + conditional aggregates instead of four
        # correlated subqueries per row. GROUP BY b.id (PK) lets us SELECT b.*.
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """SELECT b.*,
                    count(j.id) AS request_count,
                    count(*) FILTER (WHERE j.state='succeeded') AS succeeded_count,
                    count(*) FILTER (WHERE j.state IN ('failed','preview_failed')) AS failed_count,
                    count(*) FILTER (WHERE j.state='cancelled') AS cancelled_count
                   FROM backup_batches b
                   LEFT JOIN backup_requests j ON j.batch_id=b.id
                   GROUP BY b.id ORDER BY b.created_at DESC"""
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

    async def hold_unselected_registered(
        self, batch_id: str, keep_ids: list[int]
    ) -> int:
        """Selective preview: park the registered requests NOT in keep_ids as 'held'
        so the orchestrator previews only the selected ones. Returns rows held."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE backup_requests SET state='held', updated_at=now() "
                "WHERE batch_id=%s AND state='registered' AND NOT (id = ANY(%s))",
                (batch_id, keep_ids),
            )
            return cur.rowcount

    async def release_held(self, batch_id: str) -> int:
        """Return any 'held' requests to 'registered' (after a selective preview run,
        or to self-heal a crashed one). Returns rows released."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE backup_requests SET state='registered', updated_at=now() "
                "WHERE batch_id=%s AND state='held'",
                (batch_id,),
            )
            return cur.rowcount

    async def update_batch(self, batch_id: str, **fields: Any) -> None:
        """Update whitelisted draft-batch columns (name/note/delete_enabled/options).
        Only the keys present in `fields` are changed; `options` is stored as jsonb."""
        allowed = ("name", "note", "delete_enabled", "options", "priority", "node_count")
        cols: list[str] = []
        params: list[Any] = []
        for key in allowed:
            if key in fields:
                cols.append(f"{key}=%s")
                params.append(Jsonb(fields[key]) if key == "options" else fields[key])
        if not cols:
            return
        params.append(batch_id)
        async with self.pool.connection() as conn:
            await conn.execute(
                f"UPDATE backup_batches SET {', '.join(cols)}, updated_at=now() WHERE id=%s",
                params,
            )

    async def delete_batch(self, batch_id: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("DELETE FROM backup_batches WHERE id=%s", (batch_id,))

    async def batch_statuses(self, batch_ids: list[str]) -> dict[str, str]:
        """Map the given batch ids -> status (missing ids are simply absent).
        Lets a bulk caller partition ids into not_found / active / deletable."""
        if not batch_ids:
            return {}
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, status FROM backup_batches WHERE id = ANY(%s)",
                (batch_ids,),
            )
            return {r["id"]: r["status"] for r in await cur.fetchall()}

    async def delete_batches(self, batch_ids: list[str]) -> list[str]:
        """Bulk-delete the given batches, skipping any still in flight
        (previewing/running). Returns the ids actually deleted (RETURNING). FK
        ON DELETE CASCADE removes each batch's requests."""
        if not batch_ids:
            return []
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM backup_batches WHERE id = ANY(%s) "
                "AND status NOT IN ('previewing','running') RETURNING id",
                (batch_ids,),
            )
            return [r["id"] for r in await cur.fetchall()]

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

    async def replace_requests(self, batch_id: str, rows: list[dict[str, str]]) -> int:
        """Atomically replace ALL of a (draft) batch's requests with `rows`. Backs
        the inline request-table editor, where the table is the full desired set."""
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM backup_requests WHERE batch_id=%s", (batch_id,)
                )
                if rows:
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

    async def get_request(self, request_id: int) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM backup_requests WHERE id=%s", (request_id,)
            )
            return await cur.fetchone()

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

    async def cancel_requests(
        self, batch_id: str, request_ids: list[int] | None = None
    ) -> list[str]:
        """Cancel non-terminal requests of a batch; return the dms_job_ids that were
        in flight (so the caller can cancel them in DMS too). request_ids=None cancels
        all non-terminal; otherwise only the given ids."""
        clause = "" if request_ids is None else " AND id = ANY(%s)"
        params: list[Any] = [batch_id] + ([] if request_ids is None else [request_ids])
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE backup_requests SET state='cancelled', updated_at=now() "
                "WHERE batch_id=%s AND state NOT IN "
                "('succeeded','failed','cancelled','preview_failed')" + clause +
                " RETURNING dms_job_id",
                params,
            )
            return [r["dms_job_id"] for r in await cur.fetchall() if r["dms_job_id"]]

    async def delete_requests(self, batch_id: str, request_ids: list[int]) -> int:
        """Delete the given requests from a batch, skipping any that are in flight
        (preview_pending/approved/running). Returns how many were deleted."""
        if not request_ids:
            return 0
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM backup_requests WHERE batch_id=%s AND id = ANY(%s) "
                "AND state NOT IN ('preview_pending','approved','running')",
                (batch_id, request_ids),
            )
            return cur.rowcount

    async def approve_requests(
        self, batch_id: str, request_ids: list[int] | None
    ) -> int:
        """Mark preview_ready requests as 'approved' (orchestrator confirms only
        approved). request_ids=None approves all preview_ready; otherwise just the
        given ids that are still preview_ready. Returns how many were approved."""
        clause = "" if request_ids is None else " AND id = ANY(%s)"
        params: list[Any] = [batch_id] + ([] if request_ids is None else [request_ids])
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE backup_requests SET state='approved', updated_at=now() "
                f"WHERE batch_id=%s AND state='preview_ready'{clause}",
                params,
            )
            return cur.rowcount

    async def exclude_preview_ready(self, batch_id: str) -> int:
        """Close a batch: drop still-undecided preview_ready requests to cancelled."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE backup_requests SET state='cancelled', updated_at=now() "
                "WHERE batch_id=%s AND state='preview_ready'",
                (batch_id,),
            )
            return cur.rowcount

    async def cancel_request(
        self, batch_id: str, request_id: int
    ) -> tuple[bool, str | None]:
        """Cancel a single non-terminal request. Returns (changed, dms_job_id) so
        the caller can best-effort cancel the live DMS job."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE backup_requests SET state='cancelled', updated_at=now() "
                "WHERE id=%s AND batch_id=%s AND state NOT IN "
                "('succeeded','failed','cancelled','preview_failed') "
                "RETURNING dms_job_id",
                (request_id, batch_id),
            )
            row = await cur.fetchone()
            return (False, None) if row is None else (True, row["dms_job_id"])

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

    async def edit_request_paths(self, request_id: int, row: dict[str, str]) -> None:
        """Edit a request's paths and reset it to 'registered', clearing preview/
        job state so the orchestrator re-previews it (used by post-preview edit)."""
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE backup_requests SET src_storage=%s, src_path=%s, dst_storage=%s, "
                "dst_path=%s, state='registered', dms_job_id=NULL, dms_request_id=NULL, "
                "fingerprint=NULL, preview=NULL, result=NULL, error=NULL, updated_at=now() "
                "WHERE id=%s",
                (row["src_storage"], row["src_path"], row["dst_storage"], row["dst_path"], request_id),
            )

    # --- scan batches ---------------------------------------------------

    async def create_scan_batch(
        self,
        *,
        batch_id: str,
        name: str,
        options: dict[str, Any],
        requester_id: str,
        created_by: str | None,
        note: str | None,
        priority: str = "Low",
        node_count: int | None = None,
    ) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO scan_batches"
                "(id,name,status,options,requester_id,priority,node_count,created_by,note) "
                "VALUES (%s,%s,'draft',%s,%s,%s,%s,%s,%s)",
                (
                    batch_id,
                    name,
                    Jsonb(options),
                    requester_id,
                    priority,
                    node_count,
                    created_by,
                    note,
                ),
            )

    async def list_scan_batches(self) -> list[dict[str, Any]]:
        # Single pass: LEFT JOIN + conditional aggregates instead of four
        # correlated subqueries per row. GROUP BY b.id (PK) lets us SELECT b.*.
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """SELECT b.*,
                    count(j.id) AS request_count,
                    count(*) FILTER (WHERE j.state='succeeded') AS succeeded_count,
                    count(*) FILTER (WHERE j.state='failed') AS failed_count,
                    count(*) FILTER (WHERE j.state='cancelled') AS cancelled_count
                   FROM scan_batches b
                   LEFT JOIN scan_requests j ON j.batch_id=b.id
                   GROUP BY b.id ORDER BY b.created_at DESC"""
            )
            return await cur.fetchall()

    async def get_scan_batch(self, batch_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM scan_batches WHERE id=%s", (batch_id,)
            )
            return await cur.fetchone()

    async def scan_batch_state_counts(self, batch_id: str) -> dict[str, int]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT state, count(*) AS n FROM scan_requests WHERE batch_id=%s "
                "GROUP BY state",
                (batch_id,),
            )
            return {r["state"]: r["n"] for r in await cur.fetchall()}

    async def scan_result_totals(self, batch_id: str) -> dict[str, int]:
        """Aggregate scan outcomes across succeeded requests (for the batch summary):
        files/dirs/bytes scanned and errors encountered."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT "
                "coalesce(sum((result->>'file_count')::bigint),0) AS file_count, "
                "coalesce(sum((result->>'directory_count')::bigint),0) AS directory_count, "
                "coalesce(sum((result->>'total_bytes')::bigint),0) AS total_bytes, "
                "coalesce(sum((result->>'error_count')::bigint),0) AS error_count "
                "FROM scan_requests WHERE batch_id=%s AND state='succeeded'",
                (batch_id,),
            )
            row = await cur.fetchone()
            return {
                "file_count": int(row["file_count"]),
                "directory_count": int(row["directory_count"]),
                "total_bytes": int(row["total_bytes"]),
                "error_count": int(row["error_count"]),
            }

    async def set_scan_batch_status(self, batch_id: str, status: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE scan_batches SET status=%s, updated_at=now() WHERE id=%s",
                (status, batch_id),
            )

    async def update_scan_batch(self, batch_id: str, **fields: Any) -> None:
        """Update whitelisted scan-batch columns (name/note/options/priority/
        node_count). Only the keys present in `fields` are changed; `options` is
        stored as jsonb."""
        allowed = ("name", "note", "options", "priority", "node_count")
        cols: list[str] = []
        params: list[Any] = []
        for key in allowed:
            if key in fields:
                cols.append(f"{key}=%s")
                params.append(Jsonb(fields[key]) if key == "options" else fields[key])
        if not cols:
            return
        params.append(batch_id)
        async with self.pool.connection() as conn:
            await conn.execute(
                f"UPDATE scan_batches SET {', '.join(cols)}, updated_at=now() WHERE id=%s",
                params,
            )

    async def delete_scan_batch(self, batch_id: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("DELETE FROM scan_batches WHERE id=%s", (batch_id,))

    async def scan_batch_statuses(self, batch_ids: list[str]) -> dict[str, str]:
        """Map the given scan-batch ids -> status (missing ids are simply absent).
        Lets a bulk caller partition ids into not_found / active / deletable."""
        if not batch_ids:
            return {}
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, status FROM scan_batches WHERE id = ANY(%s)",
                (batch_ids,),
            )
            return {r["id"]: r["status"] for r in await cur.fetchall()}

    async def delete_scan_batches(self, batch_ids: list[str]) -> list[str]:
        """Bulk-delete the given scan batches, skipping any still in flight
        (scanning). Returns the ids actually deleted (RETURNING). FK ON DELETE
        CASCADE removes each batch's requests."""
        if not batch_ids:
            return []
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM scan_batches WHERE id = ANY(%s) "
                "AND status NOT IN ('scanning') RETURNING id",
                (batch_ids,),
            )
            return [r["id"] for r in await cur.fetchall()]

    async def active_scan_batches(self) -> list[dict[str, Any]]:
        """Batches the scan orchestrator must drive (the single 'scanning' phase)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM scan_batches WHERE status='scanning'"
            )
            return await cur.fetchall()

    async def hold_unselected_registered_scan(
        self, batch_id: str, keep_ids: list[int]
    ) -> int:
        """Selective run: park the registered requests NOT in keep_ids as 'held'
        so the orchestrator scans only the selected ones. Returns rows held."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE scan_requests SET state='held', updated_at=now() "
                "WHERE batch_id=%s AND state='registered' AND NOT (id = ANY(%s))",
                (batch_id, keep_ids),
            )
            return cur.rowcount

    async def release_held_scan(self, batch_id: str) -> int:
        """Return any 'held' requests to 'registered' (after a selective run, or to
        self-heal a crashed one). Returns rows released."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE scan_requests SET state='registered', updated_at=now() "
                "WHERE batch_id=%s AND state='held'",
                (batch_id,),
            )
            return cur.rowcount

    # --- scan requests --------------------------------------------------

    async def add_scan_requests(self, batch_id: str, rows: list[dict[str, str]]) -> int:
        """Bulk-insert registered scan requests. rows: storage/path."""
        if not rows:
            return 0
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO scan_requests(batch_id,storage,path,state) "
                    "VALUES (%s,%s,%s,'registered')",
                    [(batch_id, r["storage"], r["path"]) for r in rows],
                )
        return len(rows)

    async def replace_scan_requests(
        self, batch_id: str, rows: list[dict[str, str]]
    ) -> int:
        """Atomically replace ALL of a batch's scan requests with `rows`. Backs the
        inline request-table editor, where the table is the full desired set."""
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM scan_requests WHERE batch_id=%s", (batch_id,)
                )
                if rows:
                    async with conn.cursor() as cur:
                        await cur.executemany(
                            "INSERT INTO scan_requests(batch_id,storage,path,state) "
                            "VALUES (%s,%s,%s,'registered')",
                            [(batch_id, r["storage"], r["path"]) for r in rows],
                        )
        return len(rows)

    async def list_scan_requests(
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
                f"SELECT * FROM scan_requests {clause} ORDER BY id LIMIT %s OFFSET %s",
                params,
            )
            return await cur.fetchall()

    async def get_scan_request(self, request_id: int) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM scan_requests WHERE id=%s", (request_id,)
            )
            return await cur.fetchone()

    async def scan_requests_in_states(
        self, batch_id: str, states: list[str]
    ) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM scan_requests WHERE batch_id=%s AND state = ANY(%s) "
                "ORDER BY id",
                (batch_id, states),
            )
            return await cur.fetchall()

    async def cancel_scan_requests(
        self, batch_id: str, request_ids: list[int] | None = None
    ) -> list[str]:
        """Cancel non-terminal scan requests of a batch; return the dms_job_ids that
        were in flight (so the caller can cancel them in DMS too). request_ids=None
        cancels all non-terminal; otherwise only the given ids."""
        clause = "" if request_ids is None else " AND id = ANY(%s)"
        params: list[Any] = [batch_id] + ([] if request_ids is None else [request_ids])
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE scan_requests SET state='cancelled', updated_at=now() "
                "WHERE batch_id=%s AND state NOT IN ('succeeded','failed','cancelled')"
                + clause
                + " RETURNING dms_job_id",
                params,
            )
            return [r["dms_job_id"] for r in await cur.fetchall() if r["dms_job_id"]]

    async def delete_scan_requests(self, batch_id: str, request_ids: list[int]) -> int:
        """Delete the given scan requests from a batch, skipping any that are in
        flight (running). Returns how many were deleted."""
        if not request_ids:
            return 0
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM scan_requests WHERE batch_id=%s AND id = ANY(%s) "
                "AND state NOT IN ('running')",
                (batch_id, request_ids),
            )
            return cur.rowcount

    async def cancel_scan_request(
        self, batch_id: str, request_id: int
    ) -> tuple[bool, str | None]:
        """Cancel a single non-terminal scan request. Returns (changed, dms_job_id)
        so the caller can best-effort cancel the live DMS job."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE scan_requests SET state='cancelled', updated_at=now() "
                "WHERE id=%s AND batch_id=%s AND state NOT IN "
                "('succeeded','failed','cancelled') "
                "RETURNING dms_job_id",
                (request_id, batch_id),
            )
            row = await cur.fetchone()
            return (False, None) if row is None else (True, row["dms_job_id"])

    async def update_scan_request(self, request_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols: list[str] = []
        params: list[Any] = []
        for k, v in fields.items():
            cols.append(f"{k}=%s")
            params.append(Jsonb(v) if k == "result" else v)
        params.append(request_id)
        async with self.pool.connection() as conn:
            await conn.execute(
                f"UPDATE scan_requests SET {', '.join(cols)}, updated_at=now() WHERE id=%s",
                params,
            )

    async def edit_scan_request_path(self, request_id: int, row: dict[str, str]) -> None:
        """Edit a scan request's storage/path and reset it to 'registered', clearing
        job state/result so the orchestrator re-scans it."""
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE scan_requests SET storage=%s, path=%s, state='registered', "
                "dms_job_id=NULL, dms_request_id=NULL, result=NULL, error=NULL, "
                "updated_at=now() WHERE id=%s",
                (row["storage"], row["path"], request_id),
            )

    async def reset_scan_requests(
        self,
        batch_id: str,
        *,
        request_ids: list[int] | None = None,
        failed_only: bool = False,
        all_terminal: bool = False,
    ) -> int:
        """Reset fixable scan requests to 'registered' (clearing job state/result) for
        a re-run. `all_terminal` (rescan) targets every terminal request; `failed_only`
        targets failed ones; otherwise the given ids in a re-runnable state (scan can
        re-run succeeded). Returns how many were reset."""
        where = ["batch_id=%s"]
        params: list[Any] = [batch_id]
        if all_terminal:
            where.append("state IN ('succeeded','failed','cancelled')")
        elif failed_only:
            where.append("state IN ('failed')")
        else:
            where.append("id = ANY(%s)")
            params.append(request_ids or [])
            where.append("state IN ('registered','failed','cancelled','succeeded')")
        sql = (
            "UPDATE scan_requests SET state='registered', dms_job_id=NULL, "
            "dms_request_id=NULL, result=NULL, error=NULL, updated_at=now() "
            "WHERE " + " AND ".join(where)
        )
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return cur.rowcount

    # --- attention dismissals (조치 필요 숨김/acknowledge) ----------------

    async def dismissed_fingerprints(
        self, subset: list[str] | None = None
    ) -> set[str]:
        """Dismissed fingerprints. Pass `subset` (the fingerprints currently on screen)
        to fetch ONLY the dismissed ones among them (WHERE fingerprint = ANY, PK lookup)
        — O(screen) instead of O(table), so an accruing dismiss history never slows the
        polled 조치 필요 filter. subset=None returns all (kept for callers that need it)."""
        async with self.pool.connection() as conn:
            if subset is not None:
                if not subset:
                    return set()
                cur = await conn.execute(
                    "SELECT fingerprint FROM attention_dismissals WHERE fingerprint = ANY(%s)",
                    (list(subset),),
                )
            else:
                cur = await conn.execute("SELECT fingerprint FROM attention_dismissals")
            return {r["fingerprint"] for r in await cur.fetchall()}

    async def hidden_request_ids(self, subset: list[str] | None = None) -> set[str]:
        """request_ids hidden via the 조치 필요 dismiss/ack layer, so 액티비티 요청 목록
        + 워커 실행 현황 hide the SAME requests (consistency). Matches ONLY on request_id
        (exact 1:1 with the row) — deliberately NOT resource_key: a resource has many
        lifecycle requests and the operator dismissed one alert, not the whole history.
        Pass `subset` (request_ids currently on screen) to fetch only the hidden ones
        among them (WHERE request_id = ANY, indexed) — O(screen) not O(table). Includes
        archived rows (archived stays hidden from 조치 필요)."""
        async with self.pool.connection() as conn:
            if subset is not None:
                ids = [i for i in subset if i]
                if not ids:
                    return set()
                cur = await conn.execute(
                    "SELECT request_id FROM attention_dismissals WHERE request_id = ANY(%s)",
                    (ids,),
                )
            else:
                cur = await conn.execute(
                    "SELECT request_id FROM attention_dismissals WHERE request_id IS NOT NULL"
                )
            return {r["request_id"] for r in await cur.fetchall() if r["request_id"]}

    async def all_dismissed_fingerprints(
        self, *, before: str | None = None
    ) -> list[str]:
        """All non-archived 처리 내역 fingerprints (fingerprints only — for whole-list
        bulk ops like '모두 복원'/'이전 영구숨김' that must cover the ENTIRE set, not just
        the loaded page). `before` (ISO) limits to rows dismissed at/before that time.
        Fetched only on an explicit bulk action, never per panel load."""
        q = "SELECT fingerprint FROM attention_dismissals WHERE archived = false"
        params: list[Any] = []
        if before:
            q += " AND dismissed_at <= %s"
            params.append(before)
        async with self.pool.connection() as conn:
            cur = await conn.execute(q, tuple(params))
            return [r["fingerprint"] for r in await cur.fetchall()]

    async def count_dismissals(self) -> int:
        """Cheap total of non-archived 처리 내역 rows for the badge/'더 보기' — a COUNT
        over the (archived, dismissed_at) index, so the panel can show/size the list
        WITHOUT transferring rows (rows are fetched lazily, one page at a time)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM attention_dismissals WHERE archived = false"
            )
            row = await cur.fetchone()
            return int(row["n"]) if row else 0

    async def list_dismissals(
        self, *, limit: int = 50, offset: int = 0, order: str = "desc"
    ) -> list[dict[str, Any]]:
        # archived('영구숨김'된) rows stay hidden from 조치 필요 but drop out of the
        # 처리 내역 list. Paginated (LIMIT/OFFSET over idx_attention_dismissals_active)
        # so the forever-accruing 처리 내역 loads a screenful at a time, never all at once.
        if limit <= 0:
            return []
        direction = "ASC" if str(order).lower() == "asc" else "DESC"
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM attention_dismissals WHERE archived = false "
                f"ORDER BY dismissed_at {direction} LIMIT %s OFFSET %s",
                (limit, max(0, offset)),
            )
            return await cur.fetchall()

    async def count_archived_dismissals(self) -> int:
        """Cheap total of '영구숨김'된 rows for the badge/'더 보기' — a COUNT over the
        (archived, dismissed_at) index, so the panel can show/size the restore bin
        WITHOUT transferring any rows (rows are fetched lazily, one page at a time)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM attention_dismissals WHERE archived = true"
            )
            row = await cur.fetchone()
            return int(row["n"]) if row else 0

    async def list_archived_dismissals(
        self, *, limit: int = 50, offset: int = 0, order: str = "desc"
    ) -> list[dict[str, Any]]:
        """One page of '영구숨김'된 rows (archived=true) — hidden from 조치 필요/액티비티
        AND dropped from the 처리 내역 list, listed here so they can be restored
        (unarchive). Paginated (LIMIT/OFFSET over the (archived, dismissed_at) index)
        so an unbounded, forever-accruing archive is loaded a screenful at a time,
        never all at once."""
        if limit <= 0:
            return []
        direction = "ASC" if str(order).lower() == "asc" else "DESC"
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM attention_dismissals WHERE archived = true "
                f"ORDER BY dismissed_at {direction} LIMIT %s OFFSET %s",
                (limit, max(0, offset)),
            )
            return await cur.fetchall()

    async def unarchive_dismissals(self, fingerprints: list[str]) -> int:
        """'처리내역으로 복원': clear the archived flag — the row returns to the 처리 내역
        list (still dismissed/hidden from 조치 필요; the operator can then fully restore
        it there). Inverse of archive_dismissals."""
        if not fingerprints:
            return 0
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE attention_dismissals SET archived = false "
                "WHERE fingerprint = ANY(%s)",
                (fingerprints,),
            )
            return cur.rowcount

    async def archive_dismissals(
        self, fingerprints: list[str], *, archived_by: str = "operator"
    ) -> int:
        """'영구숨김': flag records archived — they stay in dismissed_fingerprints
        (so the item never resurfaces in 조치 필요/이력) but leave the 처리 내역 list.
        Upserts an archived stub for a fingerprint with no local row (e.g. a DMS
        server-side ack surfaced by the merge but never mirrored here), so archiving
        such a synthesized row sticks instead of reappearing on the next poll."""
        if not fingerprints:
            return 0
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO attention_dismissals "
                    "(fingerprint, kind, archived, dismissed_by) "
                    "VALUES (%s,'ack',true,%s) "
                    "ON CONFLICT (fingerprint) DO UPDATE SET archived = true",
                    [(f, archived_by) for f in fingerprints],
                )
        return len(fingerprints)

    async def add_dismissals(
        self, items: list[dict[str, Any]], dismissed_by: str
    ) -> int:
        """Upsert dismissals (one row per fingerprint). Re-dismissing refreshes
        who/when/reason AND the kind (so 숨김→확인(ack) re-marking just upserts).
        kind defaults to 'dismissed'; job_id/request_id/status are captured so the
        hidden item can later be DMS-deleted/abandoned from the 숨김 항목 list."""
        if not items:
            return 0
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO attention_dismissals"
                    "(fingerprint,issue_type,label,reason,kind,job_id,request_id,status,item_at,dismissed_by) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (fingerprint) DO UPDATE SET "
                    "issue_type=excluded.issue_type, label=excluded.label, "
                    "reason=excluded.reason, kind=excluded.kind, job_id=excluded.job_id, "
                    "request_id=excluded.request_id, status=excluded.status, "
                    "item_at=excluded.item_at, "
                    "dismissed_by=excluded.dismissed_by, dismissed_at=now()",
                    [
                        (
                            i["fingerprint"],
                            i.get("issue_type"),
                            i.get("label"),
                            i.get("reason"),
                            i.get("kind") or "dismissed",
                            i.get("job_id"),
                            i.get("request_id"),
                            i.get("status"),
                            i.get("item_at"),
                            dismissed_by,
                        )
                        for i in items
                    ],
                )
        return len(items)

    async def remove_dismissals(self, fingerprints: list[str]) -> int:
        """Un-dismiss (원위치): the items reappear in 조치 필요 on the next poll."""
        if not fingerprints:
            return 0
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM attention_dismissals WHERE fingerprint = ANY(%s)",
                (fingerprints,),
            )
            return cur.rowcount

    async def reset_requests(
        self,
        batch_id: str,
        *,
        request_ids: list[int] | None = None,
        failed_only: bool = False,
        all_terminal: bool = False,
    ) -> list[str | None]:
        """Reset fixable requests to 'registered' (clearing preview/job state) for
        re-preview. `all_terminal` (full re-run) targets EVERY terminal request
        (succeeded/failed/preview_failed/cancelled); `failed_only` targets
        failed/preview_failed; otherwise the given ids, skipping in-flight/succeeded.
        Returns one entry per reset row = its prior `dms_job_id` (None when it had
        none) — so len() is the reset count AND the caller can best-effort cancel the
        live DMS preview jobs it just orphaned (prevents a re-submit Conflict against
        the still-non-terminal prior job for the same resource_key)."""
        where = ["batch_id=%s"]
        params: list[Any] = [batch_id]
        if all_terminal:
            where.append("state IN ('succeeded','failed','preview_failed','cancelled')")
        elif failed_only:
            where.append("state IN ('failed','preview_failed')")
        else:
            where.append("id = ANY(%s)")
            params.append(request_ids or [])
            where.append(
                "state IN ('registered','preview_ready','preview_failed','failed','cancelled')"
            )
        # Capture the PRIOR dms_job_id in a data-modifying CTE: a plain
        # "UPDATE ... SET dms_job_id=NULL ... RETURNING dms_job_id" returns the NEW
        # (already-nulled) value, so the caller could never cancel the orphaned jobs.
        sql = (
            "WITH target AS (SELECT id, dms_job_id FROM backup_requests WHERE "
            + " AND ".join(where) + "), "
            "upd AS (UPDATE backup_requests SET state='registered', dms_job_id=NULL, "
            "dms_request_id=NULL, fingerprint=NULL, preview=NULL, result=NULL, "
            "error=NULL, updated_at=now() WHERE id IN (SELECT id FROM target)) "
            "SELECT dms_job_id FROM target"
        )
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return [r["dms_job_id"] for r in await cur.fetchall()]
