"""Portal persistence (PostgreSQL via psycopg3 async).

The portal's OWN store, isolated in a dedicated schema (``settings.db_schema``,
default ``portal``) of PORTAL_DB_URL. On the testbed this is the DMS Postgres
(``dms`` db) with a ``portal`` schema — ``dms_app`` lacks CREATE DATABASE, so a
schema is used; switch PORTAL_DB_URL to a dedicated ``dms_portal`` db once an
admin creates one. Holds:
  - operator_users : operator id/password login store (seeded from PORTAL_OPERATOR_USERS)
  - user_accounts  : end-user id/password login store (self-service, email-verified)
  - email_verifications : one live 6-digit code per (username, purpose), HMAC-stored
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
        # data-sync feature (데이터 Sync tab): ONE-SHOT data.sync jobs. Unlike backup
        # there is NO batch wrapper and NO re-run — the job IS the top-level unit. It
        # carries the sync config (src/dst, options, delete_enabled) AND the DMS job
        # tracking (state, preview/confirm fingerprint, result) in one row. Lifecycle:
        # registered -> preview_pending -> preview_ready -> (approve) running ->
        # succeeded|failed (preview_failed / cancelled are terminal too).
        f"""CREATE TABLE IF NOT EXISTS {s}.sync_jobs (
            id bigserial PRIMARY KEY,
            src_storage text NOT NULL,
            src_path text NOT NULL,
            dst_storage text NOT NULL,
            dst_path text NOT NULL,
            requester_id text NOT NULL DEFAULT 'root',
            owner_username text,
            options jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            delete_enabled boolean NOT NULL DEFAULT false,
            priority text NOT NULL DEFAULT 'Low',
            node_count int,
            memo text,
            state text NOT NULL DEFAULT 'registered',
            approved boolean NOT NULL DEFAULT false,
            dms_request_id text,
            dms_job_id text,
            fingerprint text,
            preview jsonb,
            result jsonb,
            error text,
            created_by text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )""",
        # the orchestrator polls non-terminal jobs (state = ANY(...)).
        f"CREATE INDEX IF NOT EXISTS sync_jobs_state ON {s}.sync_jobs(state)",
        # the list is newest-first, paginated (infinite scroll).
        f"CREATE INDEX IF NOT EXISTS sync_jobs_created_at "
        f"ON {s}.sync_jobs(created_at DESC)",
        # who created the row: 'operator' (data-Sync tab) or 'user' (end-user
        # self-service Sync). The SAME SyncOrchestrator drives both; origin only
        # scopes the LISTING (users see their own rows; the operator list can
        # label user-submitted ones). Pre-existing rows backfill to 'operator'.
        f"ALTER TABLE {s}.sync_jobs ADD COLUMN IF NOT EXISTS origin text "
        f"NOT NULL DEFAULT 'operator'",
        # speeds the user's own-jobs listing (origin='user' AND created_by=<user>,
        # newest-first) and the operator origin filter.
        f"CREATE INDEX IF NOT EXISTS sync_jobs_origin_creator "
        f"ON {s}.sync_jobs(origin, created_by, id DESC)",
        # data-rm feature (데이터 삭제 tab): ONE-SHOT data.rm (delete) jobs. Same shape
        # as sync_jobs but with a SINGLE target (drm removes one path) and NO delete
        # flag (rm IS the delete). Mutating -> preview(dry-run)/confirm/execute, same
        # lifecycle: registered -> preview_pending -> preview_ready -> (approve)
        # running -> succeeded|failed (preview_failed / cancelled are terminal too).
        f"""CREATE TABLE IF NOT EXISTS {s}.rm_jobs (
            id bigserial PRIMARY KEY,
            target_storage text NOT NULL,
            target_path text NOT NULL,
            requester_id text NOT NULL DEFAULT 'root',
            owner_username text,
            options jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            priority text NOT NULL DEFAULT 'Low',
            node_count int,
            memo text,
            state text NOT NULL DEFAULT 'registered',
            approved boolean NOT NULL DEFAULT false,
            dms_request_id text,
            dms_job_id text,
            fingerprint text,
            preview jsonb,
            result jsonb,
            error text,
            created_by text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"CREATE INDEX IF NOT EXISTS rm_jobs_state ON {s}.rm_jobs(state)",
        f"CREATE INDEX IF NOT EXISTS rm_jobs_created_at "
        f"ON {s}.rm_jobs(created_at DESC)",
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
        # dashboard time-series: DMS exposes only point-in-time work counts (no history
        # endpoint), so the BFF samples them on a fixed cadence and persists one snapshot
        # row per tick here. /dashboard/timeseries serves this for the request/job trend
        # chart. Old rows are pruned to the retention window so the table stays bounded.
        f"""CREATE TABLE IF NOT EXISTS {s}.dashboard_samples (
            id bigserial PRIMARY KEY,
            sampled_at timestamptz NOT NULL DEFAULT now(),
            active_plans int,
            active_runs int,
            active_data_jobs int,
            action_required int,
            data_jobs_total bigint
        )""",
        f"CREATE INDEX IF NOT EXISTS dashboard_samples_sampled_at "
        f"ON {s}.dashboard_samples(sampled_at DESC)",
        # per-node OS-metric history for the 워커 노드 detail tab's 1h–30d graphs. DMS
        # only serves ~24h of node metrics (/agent-reports/metrics clamps to 86400s), so
        # the BFF samples them here too and prunes to the same retention window.
        f"""CREATE TABLE IF NOT EXISTS {s}.node_metric_samples (
            id bigserial PRIMARY KEY,
            sampled_at timestamptz NOT NULL DEFAULT now(),
            cluster_name text,
            node_name text,
            cpu_percent real,
            mem_used_pct real,
            load1 real,
            disk_used_pct real,
            rx_bytes bigint,
            tx_bytes bigint
        )""",
        # migration for the v189 table: network counter columns (bandwidth = derived rate).
        f"ALTER TABLE {s}.node_metric_samples ADD COLUMN IF NOT EXISTS rx_bytes bigint",
        f"ALTER TABLE {s}.node_metric_samples ADD COLUMN IF NOT EXISTS tx_bytes bigint",
        f"CREATE INDEX IF NOT EXISTS node_metric_samples_node_at "
        f"ON {s}.node_metric_samples(node_name, sampled_at)",
        f"CREATE INDEX IF NOT EXISTS node_metric_samples_sampled_at "
        f"ON {s}.node_metric_samples(sampled_at)",
        # end-user 데이터 스캔 (user self-service): the user does NOT run scans — it
        # REGISTERS single (storage, path) items and the BFF live-pulls the operator's
        # latest completed DMS data.scan result for each (matched by storage+path). So
        # this table holds only the user's registration list + memo; scan RESULTS are
        # never persisted here (they come from DMS on read, auto-reflecting new operator
        # batch scans). UNIQUE(username, storage, path) = one registration per target.
        f"""CREATE TABLE IF NOT EXISTS {s}.user_scan_items (
            id bigserial PRIMARY KEY,
            username text NOT NULL,
            storage text NOT NULL,
            path text NOT NULL,
            memo text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (username, storage, path)
        )""",
        f"CREATE INDEX IF NOT EXISTS user_scan_items_user "
        f"ON {s}.user_scan_items(username, id DESC)",
        # dms-voc: 사용자 문의/요청(VOC). 사용자가 등록하고 운영자가 처리한다.
        # status: open(미처리) -> resolved(처리완료, answer/resolved_by/resolved_at 기록).
        # 운영자 reopen 시 open으로 복귀(answer는 초안으로 보존).
        f"""CREATE TABLE IF NOT EXISTS {s}.vocs (
            id bigserial PRIMARY KEY,
            username text NOT NULL,
            category text,
            title text NOT NULL,
            body text NOT NULL,
            status text NOT NULL DEFAULT 'open',
            answer text,
            resolved_by text,
            resolved_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )""",
        # operator tabs (status별 최신순) + 사용자 자신의 목록.
        f"CREATE INDEX IF NOT EXISTS vocs_status ON {s}.vocs(status, id DESC)",
        f"CREATE INDEX IF NOT EXISTS vocs_username ON {s}.vocs(username, id DESC)",
        # End-user accounts. Deliberately a SEPARATE table from operator_users rather
        # than one table with a role column: the role is then decided by "which table
        # matched", so a forgotten `WHERE role=...` filter cannot silently grant
        # operator access. `posix_username` is the DMS execution identity when it
        # differs from the login id (see routers/user_sync.py `_actor`).
        f"""CREATE TABLE IF NOT EXISTS {s}.user_accounts (
            username text PRIMARY KEY,
            password_hash text NOT NULL,
            email text NOT NULL,
            posix_username text,
            is_active boolean NOT NULL DEFAULT true,
            created_by text,
            password_changed_at timestamptz NOT NULL DEFAULT now(),
            last_login_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"CREATE INDEX IF NOT EXISTS user_accounts_created_at "
        f"ON {s}.user_accounts(created_at DESC)",
        # Email verification codes. PRIMARY KEY (username, purpose) makes "at most one
        # live code per id per purpose" a schema invariant: a resend is an UPSERT of
        # this row, so the previous code_hash is overwritten (invalidated) atomically
        # and there is never a window with two valid codes. `code_hash` is
        # HMAC-SHA256(pepper, "purpose|username|code") — the plaintext code is never
        # stored anywhere.
        #
        # sends/window_started_at/last_sent_at bound the send rate. failures/
        # failure_window_started_at are a SEPARATE budget that a resend does NOT reset,
        # which closes the "5 wrong guesses -> resend -> 5 more" loop that an
        # attempts-only counter leaves open.
        f"""CREATE TABLE IF NOT EXISTS {s}.email_verifications (
            username text NOT NULL,
            purpose text NOT NULL,
            email text NOT NULL,
            code_hash text NOT NULL,
            expires_at timestamptz NOT NULL,
            attempts int NOT NULL DEFAULT 0,
            consumed_at timestamptz,
            sends int NOT NULL DEFAULT 1,
            mailed int NOT NULL DEFAULT 0,
            window_started_at timestamptz NOT NULL DEFAULT now(),
            last_sent_at timestamptz NOT NULL DEFAULT now(),
            failures int NOT NULL DEFAULT 0,
            failure_window_started_at timestamptz NOT NULL DEFAULT now(),
            requested_ip text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (username, purpose)
        )""",
        f"CREATE INDEX IF NOT EXISTS email_verifications_expires "
        f"ON {s}.email_verifications(expires_at)",
        # `sends` counts REQUESTS (per-id throttling, incremented even when no mail is
        # sent so the limits — and therefore the responses — cannot reveal whether an
        # account exists). `mailed` counts mails actually dispatched and is what the
        # global relay-abuse cap reads; otherwise probing ids that produce no mail
        # would exhaust the hourly budget and take signup/reset down for everyone.
        f"ALTER TABLE {s}.email_verifications ADD COLUMN IF NOT EXISTS mailed "
        f"int NOT NULL DEFAULT 0",
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

    # --- dashboard time-series samples ----------------------------------

    async def insert_dashboard_sample(self, counts: dict[str, Any]) -> None:
        """Persist one work-count snapshot (sampled_at defaults to now())."""
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO dashboard_samples "
                "(active_plans, active_runs, active_data_jobs, action_required, "
                "data_jobs_total) VALUES (%s,%s,%s,%s,%s)",
                (
                    counts.get("active_plans"),
                    counts.get("active_runs"),
                    counts.get("active_data_jobs"),
                    counts.get("action_required"),
                    counts.get("data_jobs_total"),
                ),
            )

    async def list_dashboard_samples(self, since_iso: str) -> list[dict[str, Any]]:
        """Snapshots at/after since_iso, oldest→newest (for the trend chart)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT sampled_at, active_plans, active_runs, active_data_jobs, "
                "action_required, data_jobs_total FROM dashboard_samples "
                "WHERE sampled_at >= %s ORDER BY sampled_at ASC",
                (since_iso,),
            )
            return await cur.fetchall()

    async def prune_dashboard_samples(self, before_iso: str) -> int:
        """Drop snapshots older than the retention cutoff; returns rows deleted."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM dashboard_samples WHERE sampled_at < %s", (before_iso,)
            )
            return cur.rowcount

    # --- per-node metric samples (워커 노드 detail graphs) ---------------

    async def insert_node_samples(self, rows: list[dict[str, Any]]) -> None:
        """Bulk-insert one OS-metric snapshot per node."""
        if not rows:
            return
        params = [
            (
                r.get("cluster_name"),
                r.get("node_name"),
                r.get("cpu_percent"),
                r.get("mem_used_pct"),
                r.get("load1"),
                r.get("disk_used_pct"),
                r.get("rx_bytes"),
                r.get("tx_bytes"),
            )
            for r in rows
        ]
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO node_metric_samples "
                    "(cluster_name, node_name, cpu_percent, mem_used_pct, load1, "
                    "disk_used_pct, rx_bytes, tx_bytes) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    params,
                )

    async def list_node_samples(self, since_iso: str) -> list[dict[str, Any]]:
        """Node metric snapshots at/after since_iso, oldest→newest."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT sampled_at, cluster_name, node_name, cpu_percent, "
                "mem_used_pct, load1, disk_used_pct, rx_bytes, tx_bytes "
                "FROM node_metric_samples "
                "WHERE sampled_at >= %s ORDER BY sampled_at ASC",
                (since_iso,),
            )
            return await cur.fetchall()

    async def prune_node_samples(self, before_iso: str) -> int:
        """Drop node metric snapshots older than the retention cutoff."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM node_metric_samples WHERE sampled_at < %s", (before_iso,)
            )
            return cur.rowcount

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

    # --- end-user login + account management -----------------------------
    #
    # Kept strictly separate from the operator methods above: nothing here can write
    # to operator_users, so "an email code cannot mint an operator" holds structurally
    # rather than by review. Note these take an ALREADY-HASHED password (unlike
    # create_operator) because the caller hashes off the event loop — PBKDF2 is ~100ms
    # of CPU and these paths are unauthenticated.

    async def user_auth_record(self, username: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT password_hash, is_active, email, posix_username "
                "FROM user_accounts WHERE username=%s",
                (username,),
            )
            return await cur.fetchone()

    async def user_exists(self, username: str) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT 1 AS ok FROM user_accounts WHERE username=%s", (username,)
            )
            return await cur.fetchone() is not None

    async def touch_user_login(self, username: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE user_accounts SET last_login_at=now() WHERE username=%s",
                (username,),
            )

    async def set_user_active(self, username: str, is_active: bool) -> int:
        """Disable/enable an account. There is deliberately no hard delete: removing
        the row would orphan the user's scan items / VOCs and let the next person who
        registers the same id inherit them."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE user_accounts SET is_active=%s, updated_at=now() "
                "WHERE username=%s",
                (is_active, username),
            )
            return cur.rowcount

    async def list_user_accounts(
        self, *, limit: int = 200, offset: int = 0
    ) -> list[dict[str, Any]]:
        """password_hash is never selected."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT username, email, posix_username, is_active, created_by, "
                "created_at, last_login_at FROM user_accounts "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
            return await cur.fetchall()

    async def count_user_accounts(self) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM user_accounts")
            row = await cur.fetchone()
            return int((row or {}).get("n", 0))

    # --- email verification codes ---------------------------------------

    async def issue_verification(
        self,
        *,
        username: str,
        purpose: str,
        email: str,
        code_hash: str,
        ttl_seconds: int,
        cooldown_seconds: int,
        window_seconds: int,
        max_sends: int,
        failure_window_seconds: int,
        max_failures: int,
        will_mail: bool = True,
        requested_ip: str | None = None,
    ) -> dict[str, Any] | None:
        """Mint a code, invalidating any previous one. None => rate limited.

        A single UPSERT does all four jobs atomically: store the new code, overwrite
        (invalidate) the old one, enforce the cooldown + per-window send cap, and
        refuse while the cumulative-failure budget is exhausted. ON CONFLICT takes a
        row lock, so concurrent [인증요청] clicks serialize and the loser falls out of
        the WHERE (0 rows -> None -> 429) instead of racing a read-then-write check.
        """
        async with self.pool.connection() as conn:
            # Opportunistic cleanup. Retention (24h) is deliberately longer than the
            # rate-limit window (1h) so pruning can never become a way to reset a
            # limit. Correctness never depends on it: every verification query also
            # filters on expires_at > now().
            await conn.execute(
                "DELETE FROM email_verifications "
                "WHERE expires_at < now() - interval '24 hours'"
            )
            cur = await conn.execute(
                """
                INSERT INTO email_verifications
                    (username, purpose, email, code_hash, expires_at, requested_ip, mailed)
                VALUES
                    (%(u)s, %(p)s, %(email)s, %(ch)s,
                     now() + make_interval(secs => %(ttl)s), %(ip)s,
                     CASE WHEN %(mail)s THEN 1 ELSE 0 END)
                ON CONFLICT (username, purpose) DO UPDATE SET
                    email = excluded.email,
                    code_hash = excluded.code_hash,
                    expires_at = excluded.expires_at,
                    attempts = 0,
                    consumed_at = NULL,
                    requested_ip = excluded.requested_ip,
                    sends = CASE
                        WHEN email_verifications.window_started_at
                             < now() - make_interval(secs => %(win)s)
                        THEN 1 ELSE email_verifications.sends + 1 END,
                    mailed = CASE
                        WHEN email_verifications.window_started_at
                             < now() - make_interval(secs => %(win)s)
                        THEN (CASE WHEN %(mail)s THEN 1 ELSE 0 END)
                        ELSE email_verifications.mailed
                             + (CASE WHEN %(mail)s THEN 1 ELSE 0 END) END,
                    window_started_at = CASE
                        WHEN email_verifications.window_started_at
                             < now() - make_interval(secs => %(win)s)
                        THEN now() ELSE email_verifications.window_started_at END,
                    failures = CASE
                        WHEN email_verifications.failure_window_started_at
                             < now() - make_interval(secs => %(fwin)s)
                        THEN 0 ELSE email_verifications.failures END,
                    failure_window_started_at = CASE
                        WHEN email_verifications.failure_window_started_at
                             < now() - make_interval(secs => %(fwin)s)
                        THEN now() ELSE email_verifications.failure_window_started_at END,
                    last_sent_at = now(),
                    updated_at = now()
                WHERE email_verifications.last_sent_at
                          < now() - make_interval(secs => %(cd)s)
                  AND (email_verifications.window_started_at
                           < now() - make_interval(secs => %(win)s)
                       OR email_verifications.sends < %(max_sends)s)
                  AND (email_verifications.failure_window_started_at
                           < now() - make_interval(secs => %(fwin)s)
                       OR email_verifications.failures < %(max_failures)s)
                RETURNING expires_at, sends, last_sent_at
                """,
                {
                    "u": username,
                    "p": purpose,
                    "email": email,
                    "ch": code_hash,
                    "ttl": ttl_seconds,
                    "ip": requested_ip,
                    "cd": cooldown_seconds,
                    "win": window_seconds,
                    "max_sends": max_sends,
                    "fwin": failure_window_seconds,
                    "max_failures": max_failures,
                    "mail": will_mail,
                },
            )
            return await cur.fetchone()

    async def discard_verification(self, *, username: str, purpose: str) -> int:
        """Invalidate a just-issued code because delivery failed, so a 'the mail never
        arrived but a code is live' ghost state cannot linger.

        Expires the code but KEEPS the row: the send counters live here, so deleting
        it would hand back a fresh rate-limit budget every time delivery fails — which
        is a state an attacker can induce.
        """
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE email_verifications SET expires_at = now(), updated_at = now() "
                "WHERE username=%s AND purpose=%s AND consumed_at IS NULL",
                (username, purpose),
            )
            return cur.rowcount

    async def global_sends_last_hour(self, window_seconds: int) -> int:
        """Mails actually dispatched in the window — the relay-abuse budget.

        Sums `mailed`, not `sends`: requests that deliberately send nothing (a reset
        for an unknown id) still bump `sends` to keep the per-id limits uniform, and
        counting those here would let anyone exhaust the hourly budget — and thus
        disable signup/reset for every real user — without a single mail going out.
        """
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT coalesce(sum(mailed),0) AS n FROM email_verifications "
                "WHERE window_started_at > now() - make_interval(secs => %s)",
                (window_seconds,),
            )
            row = await cur.fetchone()
            return int((row or {}).get("n", 0))

    async def verification_matches(
        self, *, username: str, purpose: str, code_hash: str, max_attempts: int
    ) -> bool:
        """Cheap read-only check that this code is currently redeemable.

        Purely a filter in front of the ~100ms PBKDF2 the confirm routes have to run
        before they can call consume_* (the hash is a parameter of that statement).
        Without it, anyone could force unbounded password hashing with a junk code.
        This does NOT decide anything: consume_* re-checks the same conditions inside
        its atomic statement, so no TOCTOU window is introduced.
        """
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT 1 AS ok FROM email_verifications "
                "WHERE username=%s AND purpose=%s AND code_hash=%s "
                "  AND consumed_at IS NULL AND expires_at > now() AND attempts < %s",
                (username, purpose, code_hash, max_attempts),
            )
            return await cur.fetchone() is not None

    async def consume_verification_register(
        self, *, username: str, code_hash: str, password_hash: str, max_attempts: int
    ) -> dict[str, Any]:
        """Redeem a signup code and create the account in ONE statement.

        The pool is autocommit, so two statements would not be atomic — a crash
        between them could consume the code without creating the account. Matching
        the code in the WHERE clause (possible because the HMAC is deterministic)
        also removes the read-verify-consume window that would otherwise let one code
        be redeemed twice.
        """
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                WITH v AS (
                    UPDATE email_verifications
                       SET consumed_at = now(), updated_at = now()
                     WHERE username = %(u)s AND purpose = 'register'
                       AND code_hash = %(ch)s AND consumed_at IS NULL
                       AND expires_at > now() AND attempts < %(max)s
                 RETURNING email
                ),
                ins AS (
                    INSERT INTO user_accounts (username, password_hash, email, created_by)
                    SELECT %(u)s, %(ph)s, v.email, 'self-register' FROM v
                    ON CONFLICT (username) DO NOTHING
                 RETURNING username
                )
                SELECT (SELECT count(*) FROM v) AS matched,
                       (SELECT count(*) FROM ins) AS created
                """,
                {"u": username, "ch": code_hash, "ph": password_hash, "max": max_attempts},
            )
            return await cur.fetchone() or {"matched": 0, "created": 0}

    async def consume_verification_reset(
        self, *, username: str, code_hash: str, password_hash: str, max_attempts: int
    ) -> dict[str, Any]:
        """Redeem a reset code and re-key the account in ONE statement.

        `email` is refreshed from the verification row too, so after a domain change
        (gmail.com -> the company domain) a single self-service reset converges the
        stored address with no backfill script. `is_active` is required: a disabled
        account must not be recoverable by its holder.
        """
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                WITH v AS (
                    UPDATE email_verifications
                       SET consumed_at = now(), updated_at = now()
                     WHERE username = %(u)s AND purpose = 'reset'
                       AND code_hash = %(ch)s AND consumed_at IS NULL
                       AND expires_at > now() AND attempts < %(max)s
                 RETURNING email
                ),
                upd AS (
                    UPDATE user_accounts u
                       SET password_hash = %(ph)s,
                           email = v.email,
                           password_changed_at = now(),
                           updated_at = now()
                      FROM v
                     WHERE u.username = %(u)s AND u.is_active
                 RETURNING u.username
                )
                SELECT (SELECT count(*) FROM v) AS matched,
                       (SELECT count(*) FROM upd) AS updated
                """,
                {"u": username, "ch": code_hash, "ph": password_hash, "max": max_attempts},
            )
            return await cur.fetchone() or {"matched": 0, "updated": 0}

    async def bump_verification_failure(
        self, *, username: str, purpose: str, failure_window_seconds: int
    ) -> dict[str, Any]:
        """Count a wrong code. Raises `attempts` (per-code) and `failures` (the budget
        a resend does not reset) in one atomic UPDATE."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                UPDATE email_verifications SET
                    attempts = attempts + 1,
                    failures = CASE
                        WHEN failure_window_started_at
                             < now() - make_interval(secs => %(fwin)s)
                        THEN 1 ELSE failures + 1 END,
                    failure_window_started_at = CASE
                        WHEN failure_window_started_at
                             < now() - make_interval(secs => %(fwin)s)
                        THEN now() ELSE failure_window_started_at END,
                    updated_at = now()
                WHERE username = %(u)s AND purpose = %(p)s
                RETURNING attempts, failures
                """,
                {"u": username, "p": purpose, "fwin": failure_window_seconds},
            )
            return await cur.fetchone() or {"attempts": 0, "failures": 0}

    async def prune_email_verifications(self, before_iso: str) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM email_verifications WHERE expires_at < %s", (before_iso,)
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

    # --- data-sync jobs (데이터 Sync tab: one-shot data.sync) ------------

    async def create_sync_job(
        self,
        *,
        src_storage: str,
        src_path: str,
        dst_storage: str,
        dst_path: str,
        requester_id: str,
        owner_username: str | None,
        options: dict[str, Any],
        delete_enabled: bool,
        priority: str,
        node_count: int | None,
        memo: str | None,
        created_by: str | None,
        origin: str = "operator",
    ) -> dict[str, Any]:
        """Insert a new one-shot sync job ('registered'; the orchestrator submits it to
        DMS on the next cycle). Returns the created row. `origin` is 'operator' (data-Sync
        tab) or 'user' (end-user self-service); it only scopes the LISTING, not the flow."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO sync_jobs"
                "(src_storage,src_path,dst_storage,dst_path,requester_id,owner_username,"
                " options,delete_enabled,priority,node_count,memo,created_by,origin,state) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'registered') RETURNING *",
                (
                    src_storage, src_path, dst_storage, dst_path, requester_id,
                    owner_username, Jsonb(options), delete_enabled, priority,
                    node_count, memo, created_by, origin,
                ),
            )
            return await cur.fetchone()

    async def list_sync_jobs(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        origin: str | None = None,
        created_by: str | None = None,
    ) -> list[dict[str, Any]]:
        """Newest-first page of sync jobs (infinite scroll). Optional origin/created_by
        filters scope the user's own self-service jobs (origin='user' AND created_by=<me>)."""
        where, params = self._sync_scope(origin, created_by)
        params += [limit, offset]
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT * FROM sync_jobs{where} ORDER BY id DESC LIMIT %s OFFSET %s",
                params,
            )
            return await cur.fetchall()

    async def count_sync_jobs(
        self, *, origin: str | None = None, created_by: str | None = None
    ) -> int:
        where, params = self._sync_scope(origin, created_by)
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT count(*) AS n FROM sync_jobs{where}", params
            )
            row = await cur.fetchone()
            return int((row or {}).get("n") or 0)

    @staticmethod
    def _sync_scope(
        origin: str | None, created_by: str | None
    ) -> tuple[str, list[Any]]:
        """Build the WHERE clause + params for the optional origin/created_by scope."""
        clauses: list[str] = []
        params: list[Any] = []
        if origin is not None:
            clauses.append("origin=%s")
            params.append(origin)
        if created_by is not None:
            clauses.append("created_by=%s")
            params.append(created_by)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    async def get_sync_job(self, job_id: int) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM sync_jobs WHERE id=%s", (job_id,))
            return await cur.fetchone()

    async def sync_jobs_in_states(self, states: list[str]) -> list[dict[str, Any]]:
        """Non-terminal jobs for the orchestrator to drive (oldest-first)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM sync_jobs WHERE state = ANY(%s) ORDER BY id",
                (states,),
            )
            return await cur.fetchall()

    async def update_sync_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols: list[str] = []
        params: list[Any] = []
        for k, v in fields.items():
            cols.append(f"{k}=%s")
            params.append(Jsonb(v) if k in ("options", "preview", "result") else v)
        params.append(job_id)
        async with self.pool.connection() as conn:
            await conn.execute(
                f"UPDATE sync_jobs SET {', '.join(cols)}, updated_at=now() WHERE id=%s",
                params,
            )

    async def approve_sync_job(self, job_id: int) -> bool:
        """Flag a preview_ready job approved (the orchestrator then confirms it).
        Returns False if the job isn't in preview_ready (idempotent no-op)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE sync_jobs SET approved=true, updated_at=now() "
                "WHERE id=%s AND state='preview_ready' RETURNING id",
                (job_id,),
            )
            return await cur.fetchone() is not None

    async def delete_sync_job(self, job_id: int) -> dict[str, Any] | None:
        """Delete a sync-job row, returning it (so the caller can also delete the DMS
        data-job record). Returns None if not found."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM sync_jobs WHERE id=%s RETURNING *", (job_id,)
            )
            return await cur.fetchone()

    # --- data-rm jobs (데이터 삭제 tab: one-shot data.rm) ------------------
    # Mirrors the sync-job methods above but with a SINGLE target and no delete flag.

    async def create_rm_job(
        self,
        *,
        target_storage: str,
        target_path: str,
        requester_id: str,
        owner_username: str | None,
        options: dict[str, Any],
        priority: str,
        node_count: int | None,
        memo: str | None,
        created_by: str | None,
    ) -> dict[str, Any]:
        """Insert a new one-shot rm job ('registered'; the orchestrator submits it to
        DMS on the next cycle). Returns the created row."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO rm_jobs"
                "(target_storage,target_path,requester_id,owner_username,"
                " options,priority,node_count,memo,created_by,state) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'registered') RETURNING *",
                (
                    target_storage, target_path, requester_id, owner_username,
                    Jsonb(options), priority, node_count, memo, created_by,
                ),
            )
            return await cur.fetchone()

    async def list_rm_jobs(
        self, *, limit: int = 200, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Newest-first page of rm jobs (infinite scroll)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM rm_jobs ORDER BY id DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
            return await cur.fetchall()

    async def count_rm_jobs(self) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM rm_jobs")
            row = await cur.fetchone()
            return int((row or {}).get("n") or 0)

    async def get_rm_job(self, job_id: int) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM rm_jobs WHERE id=%s", (job_id,))
            return await cur.fetchone()

    async def rm_jobs_in_states(self, states: list[str]) -> list[dict[str, Any]]:
        """Non-terminal jobs for the orchestrator to drive (oldest-first)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM rm_jobs WHERE state = ANY(%s) ORDER BY id",
                (states,),
            )
            return await cur.fetchall()

    async def update_rm_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols: list[str] = []
        params: list[Any] = []
        for k, v in fields.items():
            cols.append(f"{k}=%s")
            params.append(Jsonb(v) if k in ("options", "preview", "result") else v)
        params.append(job_id)
        async with self.pool.connection() as conn:
            await conn.execute(
                f"UPDATE rm_jobs SET {', '.join(cols)}, updated_at=now() WHERE id=%s",
                params,
            )

    async def approve_rm_job(self, job_id: int) -> bool:
        """Flag a preview_ready job approved (the orchestrator then confirms it).
        Returns False if the job isn't in preview_ready (idempotent no-op)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE rm_jobs SET approved=true, updated_at=now() "
                "WHERE id=%s AND state='preview_ready' RETURNING id",
                (job_id,),
            )
            return await cur.fetchone() is not None

    async def delete_rm_job(self, job_id: int) -> dict[str, Any] | None:
        """Delete an rm-job row, returning it (so the caller can also delete the DMS
        data-job record). Returns None if not found."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM rm_jobs WHERE id=%s RETURNING *", (job_id,)
            )
            return await cur.fetchone()

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

    # --- user scan items (end-user 데이터 스캔: registered lookup targets) --------
    # A user's personal list of (storage, path) targets. Results are NOT stored here;
    # the BFF live-pulls the operator's latest DMS scan result per item on read.

    async def create_user_scan_item(
        self, *, username: str, storage: str, path: str, memo: str | None
    ) -> dict[str, Any] | None:
        """Register a scan-lookup item for a user. Returns None if the user already
        registered the same (storage, path) — the UNIQUE constraint makes it idempotent."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO user_scan_items (username,storage,path,memo) "
                "VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (username,storage,path) DO NOTHING RETURNING *",
                (username, storage, path, memo),
            )
            return await cur.fetchone()

    async def list_user_scan_items(self, *, username: str) -> list[dict[str, Any]]:
        """A user's registered scan items, newest-first."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM user_scan_items WHERE username=%s ORDER BY id DESC",
                (username,),
            )
            return await cur.fetchall()

    async def get_user_scan_item(
        self, *, item_id: int, username: str
    ) -> dict[str, Any] | None:
        """A single item, scoped to the owner (so one user can't read another's item)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM user_scan_items WHERE id=%s AND username=%s",
                (item_id, username),
            )
            return await cur.fetchone()

    async def update_user_scan_item_memo(
        self, *, item_id: int, username: str, memo: str | None
    ) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE user_scan_items SET memo=%s, updated_at=now() "
                "WHERE id=%s AND username=%s RETURNING *",
                (memo, item_id, username),
            )
            return await cur.fetchone()

    async def delete_user_scan_item(
        self, *, item_id: int, username: str
    ) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM user_scan_items WHERE id=%s AND username=%s RETURNING *",
                (item_id, username),
            )
            return await cur.fetchone()

    # --- dms-voc (사용자 문의/요청 → 운영자 처리) --------------------------------

    async def create_voc(
        self, *, username: str, category: str | None, title: str, body: str
    ) -> dict[str, Any]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO vocs (username,category,title,body) "
                "VALUES (%s,%s,%s,%s) RETURNING *",
                (username, category, title, body),
            )
            return await cur.fetchone()

    @staticmethod
    def _voc_scope(
        username: str | None,
        status: str | None,
        since_days: int | None,
        search: str | None,
    ) -> tuple[str, list[Any]]:
        """공통 WHERE(사용자/상태/기간/검색). search는 제목·본문·작성자 ILIKE."""
        clauses: list[str] = []
        params: list[Any] = []
        if username is not None:
            clauses.append("username=%s")
            params.append(username)
        if status is not None:
            clauses.append("status=%s")
            params.append(status)
        if since_days is not None:
            clauses.append("created_at >= now() - make_interval(days => %s)")
            params.append(since_days)
        if search:
            esc = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{esc}%"
            clauses.append("(title ILIKE %s OR body ILIKE %s OR username ILIKE %s)")
            params += [like, like, like]
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    async def list_vocs(
        self,
        *,
        username: str | None = None,
        status: str | None = None,
        since_days: int | None = None,
        search: str | None = None,
        order: str = "desc",
        cursor: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """운영자 목록(기간/검색/시간 정렬) 및 사용자 자신의 목록.

        페이징은 offset이 아니라 **keyset 커서**(직전 페이지 마지막 id) — 스크롤 도중
        다른 운영자의 처리/사용자 회수로 행이 빠져도 다음 페이지가 밀리지 않아
        누락·중복이 없다. 정렬은 id 기준 asc/desc(=bigserial이라 요청 시간 순과 동일;
        vocs_status 인덱스를 그대로 탄다). order는 허용값만 리터럴로 주입한다."""
        sql, params = self._voc_list_sql(
            username, status, since_days, search, order, cursor, limit
        )
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchall()

    @staticmethod
    def _voc_list_sql(
        username: str | None,
        status: str | None,
        since_days: int | None,
        search: str | None,
        order: str,
        cursor: int | None,
        limit: int,
    ) -> tuple[str, list[Any]]:
        """list_vocs의 SQL 조립(순수 함수) — 커서 방향/파라미터 순서를 단위 테스트로
        고정하기 위해 분리."""
        direction = "ASC" if order == "asc" else "DESC"
        where, params = Database._voc_scope(username, status, since_days, search)
        if cursor is not None:
            op_ = "<" if direction == "DESC" else ">"
            where = (where + " AND " if where else " WHERE ") + f"id {op_} %s"
            params.append(cursor)
        params.append(limit)
        return f"SELECT * FROM vocs{where} ORDER BY id {direction} LIMIT %s", params

    async def voc_counts(
        self, *, since_days: int | None = None, search: str | None = None
    ) -> dict[str, int]:
        """운영자 서브탭 뱃지용 status별 건수 — 기간/검색 필터가 같이 적용되어
        탭 숫자와 목록이 항상 일치한다."""
        where, params = self._voc_scope(None, None, since_days, search)
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT status, count(*) AS n FROM vocs{where} GROUP BY status", params
            )
            rows = await cur.fetchall()
        out = {"open": 0, "resolved": 0}
        for r in rows:
            # 낯선 status 값이 섞여도(수동 조작 등) 500 대신 그대로 집계에 추가.
            out[str(r["status"])] = out.get(str(r["status"]), 0) + int(r["n"])
        return out

    async def get_voc(self, voc_id: int) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM vocs WHERE id=%s", (voc_id,))
            return await cur.fetchone()

    async def resolve_voc(
        self, *, voc_id: int, answer: str | None, resolved_by: str
    ) -> dict[str, Any] | None:
        """open -> resolved (처리자·시각 기록). 이미 resolved면 None(멱등 아님을 409로)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE vocs SET status='resolved', answer=%s, resolved_by=%s, "
                "resolved_at=now(), updated_at=now() "
                "WHERE id=%s AND status='open' RETURNING *",
                (answer, resolved_by, voc_id),
            )
            return await cur.fetchone()

    async def reopen_voc(self, *, voc_id: int) -> dict[str, Any] | None:
        """resolved -> open 복귀(오처리 되돌리기). answer는 초안으로 남긴다."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE vocs SET status='open', resolved_by=NULL, resolved_at=NULL, "
                "updated_at=now() WHERE id=%s AND status='resolved' RETURNING *",
                (voc_id,),
            )
            return await cur.fetchone()

    async def delete_voc(
        self, *, voc_id: int, username: str | None = None, only_open: bool = False
    ) -> dict[str, Any] | None:
        """운영자(무조건) 또는 사용자 본인(open 상태만) 삭제."""
        clauses = ["id=%s"]
        params: list[Any] = [voc_id]
        if username is not None:
            clauses.append("username=%s")
            params.append(username)
        if only_open:
            clauses.append("status='open'")
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"DELETE FROM vocs WHERE {' AND '.join(clauses)} RETURNING *", params
            )
            return await cur.fetchone()
