from __future__ import annotations

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

CREATE TABLE IF NOT EXISTS identity_mappings (
    mapping_id TEXT PRIMARY KEY,
    requester_id TEXT NOT NULL,
    identity_provider TEXT NOT NULL,
    posix_username TEXT NOT NULL,
    uid INTEGER NOT NULL,
    gid INTEGER NOT NULL,
    groups_json TEXT NOT NULL,
    status TEXT NOT NULL,
    ldap_lookup_at TEXT,
    verified_at TEXT,
    stale_at TEXT,
    disabled_at TEXT,
    verification_result TEXT,
    mismatch_reason TEXT,
    disabled_reason TEXT,
    ldap_source_metadata TEXT,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_mapping
    ON identity_mappings(requester_id, identity_provider);

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

CREATE TABLE IF NOT EXISTS dms_control_state (
    singleton_id TEXT PRIMARY KEY,
    maintenance_mode INTEGER NOT NULL,
    drain_mode INTEGER NOT NULL,
    scheduling_blocked INTEGER NOT NULL,
    reason TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL
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
    with database.connect() as connection:
        connection.executescript(OPERATIONAL_SCHEMA)
        _ensure_operational_phase2_columns(connection, database)
        _ensure_operational_phase3_columns(connection, database)
        _ensure_operational_phase19_columns(connection, database)
        _record_migration(connection, "operational-0001-phase1")
        _record_migration(connection, "operational-0002-phase2-identity")
        _record_migration(connection, "operational-0003-phase3-inventory")
        _record_migration(connection, "operational-0019-data-management-scan")


def migrate_observability(database: Database) -> None:
    with database.connect() as connection:
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


def _ensure_operational_phase2_columns(connection, database: Database) -> None:
    for column, definition in {
        "ldap_lookup_at": "TEXT",
        "verification_result": "TEXT",
        "mismatch_reason": "TEXT",
        "disabled_reason": "TEXT",
        "ldap_source_metadata": "TEXT",
    }.items():
        if not _column_exists(connection, database, "identity_mappings", column):
            connection.execute(f"ALTER TABLE identity_mappings ADD COLUMN {column} {definition}")


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
