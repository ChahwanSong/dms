from __future__ import annotations

from datetime import UTC, datetime
import json
from urllib.parse import urlparse

from .db import Database


OPERATIONAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    requester_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    operation TEXT NOT NULL,
    resource_kind TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    payload_summary TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL,
    commit_order INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_requests_commit_order ON requests(commit_order);
CREATE INDEX IF NOT EXISTS idx_requests_resource ON requests(resource_kind, resource_key, commit_order);
CREATE INDEX IF NOT EXISTS idx_requests_requester_commit_order
    ON requests(requester_id, commit_order DESC);
CREATE INDEX IF NOT EXISTS idx_requests_resource_operation_order
    ON requests(resource_kind, resource_key, operation, commit_order DESC);
CREATE INDEX IF NOT EXISTS idx_requests_status_order ON requests(status, commit_order DESC);
CREATE INDEX IF NOT EXISTS idx_requests_operation_order ON requests(operation, commit_order DESC);

CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    status TEXT NOT NULL,
    worker_role TEXT NOT NULL,
    operation_kind TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    desired_state TEXT NOT NULL,
    precondition TEXT NOT NULL,
    execution_metadata TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(request_id) REFERENCES requests(request_id)
);
CREATE INDEX IF NOT EXISTS idx_plans_status_role ON plans(status, worker_role, created_at);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    executor_id TEXT NOT NULL,
    worker_role TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(request_id) REFERENCES requests(request_id),
    FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
);
CREATE INDEX IF NOT EXISTS idx_runs_state_lease ON runs(state, lease_expires_at);
-- Dashboard run lists / history scans order by updated_at DESC, so index it for an
-- index scan instead of a full sort once runs accumulate (thousands/day).
CREATE INDEX IF NOT EXISTS idx_runs_updated_at ON runs(updated_at);

CREATE TABLE IF NOT EXISTS results (
    result_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    plan_id TEXT,
    run_id TEXT,
    terminal_status TEXT NOT NULL,
    error_category TEXT,
    message TEXT NOT NULL,
    verification_summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(request_id) REFERENCES requests(request_id)
);
CREATE INDEX IF NOT EXISTS idx_results_request ON results(request_id);

CREATE TABLE IF NOT EXISTS state_transitions (
    transition_id TEXT PRIMARY KEY,
    request_id TEXT,
    plan_id TEXT,
    run_id TEXT,
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_state_transitions_request ON state_transitions(request_id, created_at);

CREATE TABLE IF NOT EXISTS resources (
    resource_id TEXT PRIMARY KEY,
    resource_kind TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    desired_state TEXT NOT NULL,
    applied_state TEXT NOT NULL,
    observed_state TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_resources_kind_key ON resources(resource_kind, resource_key);

CREATE TABLE IF NOT EXISTS storage_mappings (
    storage_name TEXT PRIMARY KEY,
    backend_template TEXT NOT NULL,
    cluster_name TEXT,
    storage_class_name TEXT,
    version INTEGER NOT NULL,
    sanity_status TEXT NOT NULL,
    sanity_result TEXT,
    sanity_checked_at TEXT,
    readiness TEXT,
    disabled_at TEXT,
    disabled_reason TEXT,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_storage_class_mapping
    ON storage_mappings(cluster_name, storage_class_name);

CREATE TABLE IF NOT EXISTS default_quota_policies (
    policy_id TEXT PRIMARY KEY,
    resource_kind TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    quota TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_default_quota_policies
    ON default_quota_policies(resource_kind, resource_type);

-- identity_mappings was removed: DM resolves POSIX identity by READ-ONLY LDAP lookup
-- at preflight time (no cached/admission-gated mapping table). The only DM-side
-- identity state is the denylist below (per-user/group instant kill-switch + block).
DROP INDEX IF EXISTS uq_identity_mapping;
DROP TABLE IF EXISTS identity_mappings;
CREATE TABLE IF NOT EXISTS dm_identity_denylist (
    subject_type TEXT NOT NULL,   -- requester | owner | group
    subject TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    PRIMARY KEY (subject_type, subject)
);

CREATE TABLE IF NOT EXISTS agent_reports (
    report_id TEXT PRIMARY KEY,
    cluster_name TEXT NOT NULL,
    node_name TEXT NOT NULL,
    node_uid TEXT NOT NULL,
    worker_role TEXT NOT NULL,
    report_json TEXT NOT NULL,
    capability_summary TEXT,
    freshness_status TEXT NOT NULL,
    reported_at TEXT NOT NULL,
    received_at TEXT,
    stale_at TEXT,
    schema_version TEXT,
    validation_status TEXT,
    validation_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_reports_node
    ON agent_reports(cluster_name, node_name, reported_at);
-- Supports latest-per-node reads (one row per cluster/node/role): the node-health
-- and action-required paths dedup to the newest report per node via a single
-- ROW_NUMBER() window partitioned by (cluster, node, role) ordered by reported_at,
-- NOT a per-row self/anti-join. This index matches that partition+order so the
-- window runs as an index scan (no full sort) even when the table holds hundreds of
-- thousands of rows. (Supersedes the earlier (node, role, reported_at) index — and
-- a short-lived report_id variant — so drop both.)
DROP INDEX IF EXISTS idx_agent_reports_latest;
DROP INDEX IF EXISTS idx_agent_reports_latest_id;
CREATE INDEX IF NOT EXISTS idx_agent_reports_latest_v2
    ON agent_reports(cluster_name, node_name, worker_role, reported_at);
-- reported_at range scans: the node-metrics dashboard window query and the age-based
-- retention prune both filter/order on reported_at alone. The composite indexes above
-- are NOT usable for a bare reported_at predicate, so add a dedicated single-column
-- index (the table grows to millions of rows at 100+ nodes reporting ~1/min).
CREATE INDEX IF NOT EXISTS idx_agent_reports_reported_at
    ON agent_reports(reported_at);

-- Denormalized current-state row per node identity (cluster_name, node_name,
-- worker_role): exactly ONE row per node, holding its LATEST agent report. Node-health
-- reads (latest-per-node listing, stale-node detection) hit only THIS table, so they are
-- O(#nodes) regardless of how deep agent_reports history grows (millions of rows, age-
-- pruned by `dms retention`). It is written transactionally alongside every agent_reports
-- INSERT (see ingest_agent_report) and backfilled from existing history at migrate time.
CREATE TABLE IF NOT EXISTS agent_node_current (
    cluster_name TEXT NOT NULL,
    node_name TEXT NOT NULL,
    worker_role TEXT NOT NULL,
    node_uid TEXT,
    report_id TEXT NOT NULL,
    report_json TEXT NOT NULL,
    capability_summary TEXT,
    reported_at TEXT NOT NULL,
    received_at TEXT,
    schema_version TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (cluster_name, node_name, worker_role)
);

CREATE TABLE IF NOT EXISTS data_jobs (
    job_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    storage_name TEXT NOT NULL,
    source TEXT,
    destination TEXT,
    target TEXT,
    priority INTEGER NOT NULL,
    selected_tool TEXT,
    worker_pool TEXT NOT NULL,
    state TEXT NOT NULL,
    artifact_uri TEXT,
    normalized_target TEXT,
    preflight_result TEXT,
    volcano_job_ref TEXT,
    result_summary TEXT,
    log_uri TEXT,
    preview_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(request_id) REFERENCES requests(request_id)
);
CREATE INDEX IF NOT EXISTS idx_data_jobs_request ON data_jobs(request_id);
-- Dashboard job list orders by updated_at DESC, so index it for an index scan
-- instead of a full sort once jobs accumulate (~10k/day).
CREATE INDEX IF NOT EXISTS idx_data_jobs_updated_at ON data_jobs(updated_at);
-- Scoped action-required counts/filters select jobs by (operation, state) — backs the
-- cheap COUNT(*) / filtered list that the work-summary "action required" tile uses.
CREATE INDEX IF NOT EXISTS idx_data_jobs_operation_state ON data_jobs(operation, state);

CREATE TABLE IF NOT EXISTS data_management_policies (
    operation TEXT PRIMARY KEY,
    default_worker_nodes INTEGER,
    default_source_nodes INTEGER,
    default_destination_nodes INTEGER,
    max_worker_nodes INTEGER,
    max_source_nodes INTEGER,
    max_destination_nodes INTEGER,
    default_processes_per_node INTEGER NOT NULL,
    max_processes_per_node INTEGER NOT NULL,
    default_queue TEXT,
    default_priority_class TEXT,
    default_timeout_seconds INTEGER,
    enabled INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);

CREATE TABLE IF NOT EXISTS control_mutations (
    mutation_id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    mutation_kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    mutation_class TEXT,
    operation TEXT,
    target_key TEXT,
    status TEXT,
    result_summary TEXT,
    before_state TEXT,
    after_state TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_acks (
    fingerprint TEXT PRIMARY KEY,   -- issue_type|key (stable per action-required item)
    issue_type TEXT,
    reason TEXT,
    acked_by TEXT,
    acked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dms_control_state (
    singleton_id TEXT PRIMARY KEY,
    maintenance_mode INTEGER NOT NULL,
    drain_mode INTEGER NOT NULL,
    scheduling_blocked INTEGER NOT NULL,
    reason TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL
);

-- On-demand identity probe targets: the DM worker registers a data-job requester's
-- POSIX username here at identity-resolve time; node agents receive the recent set
-- in the agent-report POST response and probe those names (in addition to the
-- static DMS_AGENT_IDENTITY_USERS baseline) on their next cycle. Rows expire by
-- last_requested_at (DMS_DM_IDENTITY_PROBE_TARGET_TTL_SECONDS) and are pruned on read.
CREATE TABLE IF NOT EXISTS dm_identity_probe_targets (
    username TEXT PRIMARY KEY,
    last_requested_at TEXT NOT NULL
);
"""


OBSERVABILITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnostic_events (
    event_id TEXT PRIMARY KEY,
    correlation_id TEXT,
    component TEXT NOT NULL,
    severity TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diagnostic_events_correlation
    ON diagnostic_events(correlation_id, created_at);
"""


def migrate_operational(database: Database) -> None:
    # pooled=False: schema/index DDL on large tables must NOT be killed by the
    # pooled statement_timeout, and migrations run once at startup (no pool needed).
    with database.connect(pooled=False) as connection:
        connection.executescript(OPERATIONAL_SCHEMA)
        _ensure_operational_phase3_columns(connection, database)
        _ensure_operational_phase19_columns(connection, database)
        _backfill_filesystem_managed_root(connection)
        _backfill_agent_node_current(connection)
        _record_migration(connection, "operational-0001-phase1")
        _record_migration(connection, "operational-0002-phase2-identity")
        _record_migration(connection, "operational-0003-phase3-inventory")
        _record_migration(connection, "operational-0019-data-management-scan")
        _record_migration(connection, "operational-0022-data-management-policies")
        _record_migration(connection, "operational-0023-managed-root-backfill")
        _record_migration(connection, "operational-0024-agent-node-current")
        _record_migration(connection, "operational-0025-scale-indexes")


def migrate_observability(database: Database) -> None:
    # pooled=False: see migrate_operational.
    with database.connect(pooled=False) as connection:
        connection.executescript(OBSERVABILITY_SCHEMA)
        _record_migration(connection, "observability-0001-phase1")


def migrate_all(operational: Database, observability: Database) -> None:
    migrate_operational(operational)
    migrate_observability(observability)


def _record_migration(connection, version: str) -> None:
    existing = connection.execute(
        "SELECT version FROM schema_migrations WHERE version = ?",
        (version,),
    ).fetchone()
    if existing:
        return
    connection.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, CURRENT_TIMESTAMP)",
        (version,),
    )


def _ensure_operational_phase3_columns(connection, database: Database) -> None:
    for table, columns in {
        "agent_reports": {
            "capability_summary": "TEXT",
            "received_at": "TEXT",
            "stale_at": "TEXT",
            "schema_version": "TEXT",
            "validation_status": "TEXT",
            "validation_error": "TEXT",
        },
        "storage_mappings": {
            "sanity_result": "TEXT",
            "sanity_checked_at": "TEXT",
            "readiness": "TEXT",
            "disabled_at": "TEXT",
            "disabled_reason": "TEXT",
            "updated_by": "TEXT",
        },
        "control_mutations": {
            "mutation_class": "TEXT",
            "operation": "TEXT",
            "target_key": "TEXT",
            "status": "TEXT",
            "result_summary": "TEXT",
            "before_state": "TEXT",
            "after_state": "TEXT",
        },
    }.items():
        for column, definition in columns.items():
            if not _column_exists(connection, database, table, column):
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_operational_phase19_columns(connection, database: Database) -> None:
    for column, definition in {
        "normalized_target": "TEXT",
        "preflight_result": "TEXT",
        "volcano_job_ref": "TEXT",
        "result_summary": "TEXT",
        "log_uri": "TEXT",
    }.items():
        if not _column_exists(connection, database, "data_jobs", column):
            connection.execute(f"ALTER TABLE data_jobs ADD COLUMN {column} {definition}")


def _backfill_filesystem_managed_root(connection) -> None:
    """Promote the historical implicit ``{mount_path}/dms`` default to an explicit
    ``managed_root`` on filesystem storage mappings registered before managed_root
    became mandatory.

    Idempotent: rows that already declare ``managed_root``, are non-filesystem, or
    lack a ``mount_path`` (cannot derive) are left untouched -- the latter fall to the
    registration validator / runtime guard on next write.
    """
    rows = connection.execute(
        "SELECT storage_name, backend_template FROM storage_mappings"
    ).fetchall()
    for row in rows:
        raw = row["backend_template"]
        if not raw:
            continue
        try:
            template = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(template, dict):
            continue
        if template.get("backend_type") not in ("cephfs", "wekafs", "gpfs"):
            continue
        if template.get("managed_root"):
            continue
        mount_path = template.get("mount_path")
        if not mount_path:
            continue
        template["managed_root"] = f"{mount_path.rstrip('/')}/dms"
        connection.execute(
            "UPDATE storage_mappings SET backend_template = ? WHERE storage_name = ?",
            (json.dumps(template), row["storage_name"]),
        )


def _backfill_agent_node_current(connection) -> None:
    """Seed ``agent_node_current`` with the latest report per (cluster, node, role) from
    the existing ``agent_reports`` history, so node-health reads work the instant the
    denormalized table exists (before any new report is ingested).

    Idempotent: the UPSERT updates an existing current row ONLY when the backfilled
    report is newer (``excluded.reported_at >= agent_node_current.reported_at``), so
    re-running migrate — or running it after live ingest has already written fresher
    current rows — never regresses a row. Runs unpooled (see ``migrate_operational``) so
    it is not statement-timeout-killed even over millions of history rows.

    GATED on an EMPTY current table: the heavy ``GROUP BY`` over (potentially millions of)
    agent_reports rows runs ONLY on the first migrate or after a manual truncate
    (self-healing). On every normal boot — every API pod, every CLI — the table is already
    populated (kept current transactionally by the ingest UPSERT), so this O(1) probe
    short-circuits the backfill to a no-op instead of re-scanning the whole history table.
    """
    probe = connection.execute(
        "SELECT 1 FROM agent_node_current LIMIT 1"
    ).fetchone()
    if probe is not None:
        return
    rows = connection.execute(
        """
        SELECT ar.cluster_name, ar.node_name, ar.worker_role, ar.node_uid,
               ar.report_id, ar.report_json, ar.capability_summary,
               ar.reported_at, ar.received_at, ar.schema_version
        FROM agent_reports ar
        JOIN (
            SELECT cluster_name, node_name, worker_role, max(reported_at) AS mx
            FROM agent_reports
            GROUP BY cluster_name, node_name, worker_role
        ) g
          ON g.cluster_name = ar.cluster_name
         AND g.node_name = ar.node_name
         AND g.worker_role = ar.worker_role
         AND ar.reported_at = g.mx
        """
    ).fetchall()
    now = datetime.now(UTC).isoformat()
    for row in rows:
        record = {key: row[key] for key in row.keys()}
        connection.execute(
            """
            INSERT INTO agent_node_current (
                cluster_name, node_name, worker_role, node_uid, report_id,
                report_json, capability_summary, reported_at, received_at,
                schema_version, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cluster_name, node_name, worker_role) DO UPDATE SET
                node_uid = excluded.node_uid,
                report_id = excluded.report_id,
                report_json = excluded.report_json,
                capability_summary = excluded.capability_summary,
                reported_at = excluded.reported_at,
                received_at = excluded.received_at,
                schema_version = excluded.schema_version,
                updated_at = excluded.updated_at
            WHERE excluded.reported_at >= agent_node_current.reported_at
            """,
            (
                record["cluster_name"],
                record["node_name"],
                record["worker_role"],
                record["node_uid"],
                record["report_id"],
                record["report_json"],
                record["capability_summary"],
                record["reported_at"],
                record["received_at"],
                record["schema_version"],
                now,
            ),
        )


def _column_exists(connection, database: Database, table: str, column: str) -> bool:
    scheme = urlparse(database.url).scheme
    if scheme == "sqlite":
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)
    if scheme in {"postgresql", "postgres"}:
        row = connection.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = ?
              AND column_name = ?
              AND table_schema = ANY (current_schemas(false))
            LIMIT 1
            """,
            (table, column),
        ).fetchone()
        return row is not None
    return False
