"""Pass-2 scale fixes (targeting 100+ nodes / millions of agent_reports rows).

Covers:
- agent_node_current UPSERT keeps the NEWEST report per node identity, and an
  out-of-order OLDER report is ignored (the WHERE guard);
- latest_per_node reads agent_node_current with freshness computed ON READ
  (a silent node shows Stale; a freshly-reporting node shows Fresh) at a threaded
  staleness threshold;
- prune_agent_reports deletes only rows older than the cutoff, batched, and leaves
  node-health complete afterwards (agent_node_current is independent of history);
- action_required_count() == len(action_required()) for the same DB state (the
  dashboard tile never drifts from the panel);
- the new scale indexes + agent_node_current table exist after migration;
- the retention once-entrypoint computes the cutoff and prunes.

All against SQLite, mirroring the existing repository/query test fixtures.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState, LifecycleState, OperationKind, ResourceKind
from dms.migrations import migrate_all
from dms.query import OperationalQueryService
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.retention import prune_agent_reports_once


_TS = "2026-01-01T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
def _repo(tmp_path) -> DmsRepository:
    op = Database(f"sqlite:///{tmp_path / 'op.db'}")
    obs = Database(f"sqlite:///{tmp_path / 'obs.db'}")
    migrate_all(op, obs)
    return DmsRepository(op)


def _query_service(tmp_path) -> OperationalQueryService:
    op = Database(f"sqlite:///{tmp_path / 'op.db'}")
    obs = Database(f"sqlite:///{tmp_path / 'obs.db'}")
    migrate_all(op, obs)
    return OperationalQueryService(DmsRepository(op), ObservabilityRepository(obs))


def _rep(node: str, role: str, dt: datetime, *, cluster: str = "cluster-a") -> dict[str, Any]:
    return {
        "schema_version": "test.v1",
        "reported_at": dt.isoformat(),
        "cluster_name": cluster,
        "node_name": node,
        "node_uid": f"uid-{node}-{role}",
        "worker_role": role,
        "mounts": [],
        "csi": [],
        "tools": [],
    }


def _seed_request(repo: DmsRepository) -> str:
    return repo.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.DATA_SYNC.value,
        resource_kind=ResourceKind.DATA_JOB.value,
        resource_key="cephfs-a:proj",
        payload={"storage_name": "cephfs-a"},
    )


def _set_request_status(repo: DmsRepository, request_id: str, status: str) -> None:
    with repo.database.connect() as connection:
        connection.execute(
            "UPDATE requests SET status = ? WHERE request_id = ?", (status, request_id)
        )


def _bulk_failed_data_jobs(repo: DmsRepository, n: int, *, operation: str | None = None) -> None:
    request_id = _seed_request(repo)
    columns = [
        "job_id", "request_id", "operation", "storage_name", "source",
        "destination", "target", "priority", "selected_tool", "worker_pool",
        "state", "artifact_uri", "normalized_target", "preflight_result",
        "volcano_job_ref", "result_summary", "log_uri", "preview_expires_at",
        "created_at", "updated_at",
    ]
    op = operation or OperationKind.DATA_SYNC.value
    rows = [
        (
            f"job_{op}_{i}", request_id, op, "cephfs-a",
            None, None, "proj", 100, "dsync", "{}", DataJobState.FAILED.value,
            None, "{}", "{}", "{}", "{}", None, None, _TS, _TS,
        )
        for i in range(n)
    ]
    placeholders = ",".join(["?"] * len(columns))
    with repo.database.connect() as connection:
        connection.executemany(
            f"INSERT INTO data_jobs ({','.join(columns)}) VALUES ({placeholders})", rows
        )


# --------------------------------------------------------------------------- #
# (A) agent_node_current UPSERT — newest-wins + out-of-order guard
# --------------------------------------------------------------------------- #
def test_ingest_upserts_current_row_newest_wins(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(minutes=5)))
    newest = repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(minutes=1)))

    with repo.database.connect() as connection:
        rows = connection.execute("SELECT * FROM agent_node_current").fetchall()
    # exactly one current row per (cluster, node, role), holding the NEWEST report
    assert len(rows) == 1
    row = {k: rows[0][k] for k in rows[0].keys()}
    assert row["report_id"] == newest
    assert row["reported_at"] == (now - timedelta(minutes=1)).isoformat()


def test_ingest_out_of_order_older_report_is_ignored(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    newest = repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(minutes=1)))
    # a delayed/out-of-order report with an OLDER reported_at must NOT overwrite the row
    repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(minutes=30)))

    [report] = repo.list_agent_reports(latest_per_node=True)
    assert report["report_id"] == newest
    assert report["reported_at"] == (now - timedelta(minutes=1)).isoformat()
    # but the OLDER report is still appended to the history table (audit trail intact)
    assert len(repo.list_agent_reports(limit=100)) == 2


# --------------------------------------------------------------------------- #
# (B) latest_per_node freshness computed ON READ at a threaded threshold
# --------------------------------------------------------------------------- #
def test_latest_per_node_freshness_on_read(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    # w1: reported just now -> Fresh; w2: last reported 2h ago -> Stale (no mark sweep run)
    repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(seconds=5)))
    repo.ingest_agent_report(_rep("w2", "DM", now - timedelta(hours=2)))

    fresh_view = {
        (r["node_name"], r["freshness_status"])
        for r in repo.list_agent_reports(latest_per_node=True, stale_seconds=300)
    }
    assert fresh_view == {("w1", "Fresh"), ("w2", "Stale")}

    # a tighter threshold flips w1 to Stale too (freshness is computed, not stored)
    tight = {
        (r["node_name"], r["freshness_status"])
        for r in repo.list_agent_reports(latest_per_node=True, stale_seconds=1)
    }
    assert tight == {("w1", "Stale"), ("w2", "Stale")}

    # freshness filter applies to the computed value
    stale = repo.list_agent_reports(
        latest_per_node=True, freshness="Stale", stale_seconds=300
    )
    assert [(r["node_name"], r["worker_role"]) for r in stale] == [("w2", "DM")]


def test_latest_per_node_stale_at_is_transition_time(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    reported = now - timedelta(hours=2)
    repo.ingest_agent_report(_rep("w-stale", "RM", reported))
    repo.ingest_agent_report(_rep("w-fresh", "DM", now - timedelta(seconds=5)))

    by_node = {
        r["node_name"]: r
        for r in repo.list_agent_reports(latest_per_node=True, stale_seconds=300)
    }
    # Stale node: stale_at is the DETERMINISTIC instant it crossed the threshold
    # (reported_at + stale_seconds), not the arbitrary read time.
    assert by_node["w-stale"]["freshness_status"] == "Stale"
    assert (
        by_node["w-stale"]["stale_at"]
        == (reported + timedelta(seconds=300)).isoformat()
    )
    # Fresh node has no stale_at.
    assert by_node["w-fresh"]["freshness_status"] == "Fresh"
    assert by_node["w-fresh"]["stale_at"] is None


# --------------------------------------------------------------------------- #
# (C) prune — age-based, batched, node-health preserved
# --------------------------------------------------------------------------- #
def test_prune_deletes_only_old_rows_batched_and_keeps_node_health(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    # 6 OLD reports for a node that has since gone silent (>40d), plus 1 recent for w-live
    for i in range(6):
        repo.ingest_agent_report(_rep("w-old", "RM", now - timedelta(days=40, minutes=i)))
    repo.ingest_agent_report(_rep("w-live", "DM", now - timedelta(minutes=2)))
    assert len(repo.list_agent_reports(limit=100)) == 7

    cutoff = (now - timedelta(days=10)).isoformat()
    deleted = repo.prune_agent_reports(older_than_iso=cutoff, batch_size=2)

    assert deleted == 6  # only the 6 old rows; batching (size 2) deletes them all
    remaining = repo.list_agent_reports(limit=100)
    assert len(remaining) == 1
    assert remaining[0]["node_name"] == "w-live"

    # node-health is STILL complete after prune: the silent w-old node still appears
    # (its current row in agent_node_current is independent of pruned history).
    health = {
        (r["node_name"], r["worker_role"])
        for r in repo.list_agent_reports(latest_per_node=True, stale_seconds=300)
    }
    assert health == {("w-old", "RM"), ("w-live", "DM")}


def test_prune_noop_when_nothing_old(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(minutes=1)))
    deleted = repo.prune_agent_reports(
        older_than_iso=(now - timedelta(days=30)).isoformat()
    )
    assert deleted == 0
    assert len(repo.list_agent_reports(limit=100)) == 1


def test_retention_once_prunes_and_returns_count(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    repo.ingest_agent_report(_rep("w-old", "RM", now - timedelta(days=40)))
    repo.ingest_agent_report(_rep("w-new", "RM", now - timedelta(minutes=1)))

    deleted = prune_agent_reports_once(
        repo, retention_seconds=7 * 24 * 60 * 60, batch_size=1000
    )
    assert deleted == 1
    assert {r["node_name"] for r in repo.list_agent_reports(limit=100)} == {"w-new"}


# --------------------------------------------------------------------------- #
# (D) action_required_count() == len(action_required()) invariant
# --------------------------------------------------------------------------- #
def test_action_required_count_matches_list_length(tmp_path):
    service = _query_service(tmp_path)
    repo = service.repository
    now = datetime.now(timezone.utc)

    # request_attention source: one BLOCKED request
    blocked = _seed_request(repo)
    _set_request_status(repo, blocked, LifecycleState.BLOCKED.value)

    # storage-mapping source: one Failed mapping
    from dms.domain import StorageMappingInput

    repo.upsert_storage_mapping(
        StorageMappingInput(
            storage_name="bad-fs",
            backend_template={"backend_type": "cephfs"},
            cluster_name="cluster-a",
            storage_class_name="bad-fs-sc",
            sanity_status="Failed",
        ),
        actor="admin",
    )

    # agent-stale source: one silent node (Stale) + one fresh node (NOT an item)
    repo.ingest_agent_report(_rep("w-stale", "RM", now - timedelta(hours=3)))
    repo.ingest_agent_report(_rep("w-fresh", "DM", now - timedelta(seconds=5)))

    # data-management source: a few failed scan/sync/rm jobs (mixed operations)
    _bulk_failed_data_jobs(repo, 2, operation=OperationKind.DATA_SYNC.value)
    _bulk_failed_data_jobs(repo, 3, operation=OperationKind.DATA_RM.value)

    issues = service.action_required()
    count = service.action_required_count()

    assert count == len(issues)
    # sanity: every expected source contributed
    kinds = {i["issue_type"] for i in issues}
    assert {"request_attention", "storage_mapping_failed", "agent_report_stale"} <= kinds
    assert sum(1 for i in issues if i["issue_type"] == "agent_report_stale") == 1
    # the 5 failed data jobs each surface as one data_management issue (issue_type
    # prefixed data_job_*); the BLOCKED request_attention issue also carries
    # resource_kind=data_job, so filter on the data-job issue_type prefix instead.
    assert sum(1 for i in issues if i["issue_type"].startswith("data_job")) == 5


def test_action_required_count_matches_when_empty(tmp_path):
    service = _query_service(tmp_path)
    assert service.action_required_count() == len(service.action_required()) == 0


def test_work_summary_action_required_uses_count(tmp_path):
    service = _query_service(tmp_path)
    repo = service.repository
    now = datetime.now(timezone.utc)
    repo.ingest_agent_report(_rep("w-stale", "RM", now - timedelta(hours=3)))
    _bulk_failed_data_jobs(repo, 4, operation=OperationKind.DATA_SCAN.value)

    summary = service.work_summary()
    assert summary["requests"]["action_required"] == len(service.action_required())
    # 1 stale node + 4 failed scan jobs
    assert summary["requests"]["action_required"] == 5


# --------------------------------------------------------------------------- #
# (E) schema: table + scale indexes present after migration
# --------------------------------------------------------------------------- #
def test_scale_schema_objects_present(tmp_path):
    repo = _repo(tmp_path)
    with repo.database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
    assert "agent_node_current" in tables
    assert {
        "idx_agent_reports_reported_at",
        "idx_data_jobs_updated_at",
        "idx_data_jobs_operation_state",
        "idx_runs_updated_at",
    } <= indexes


def test_migrate_is_idempotent_and_backfills_current(tmp_path):
    op = Database(f"sqlite:///{tmp_path / 'op.db'}")
    obs = Database(f"sqlite:///{tmp_path / 'obs.db'}")
    migrate_all(op, obs)
    repo = DmsRepository(op)
    now = datetime.now(timezone.utc)
    # seed history, then DROP the current row to simulate a pre-existing DB whose
    # current table must be backfilled from history on the next migrate.
    repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(minutes=5)))
    newest = repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(minutes=1)))
    with op.connect() as connection:
        connection.execute("DELETE FROM agent_node_current")
    assert repo.list_agent_reports(latest_per_node=True) == []

    # EMPTY current table -> re-running migrate self-heals by backfilling current from
    # the latest history row (idempotent + the empty-guard's re-backfill path).
    migrate_all(op, obs)
    [report] = repo.list_agent_reports(latest_per_node=True)
    assert report["report_id"] == newest

    # NON-EMPTY current table -> the heavy GROUP BY backfill is GATED OFF. Stamp the
    # current row with a sentinel whose reported_at is OLDER than the newest history row:
    # if the backfill still ran it WOULD overwrite the sentinel (the newest history row's
    # reported_at satisfies the UPSERT's >= guard). Because the empty-guard skips it on a
    # populated table, the sentinel survives — proving the full-history scan does not run
    # on a normal boot.
    with op.connect() as connection:
        connection.execute(
            "UPDATE agent_node_current SET report_id = ?, reported_at = ?",
            ("SENTINEL", (now - timedelta(hours=1)).isoformat()),
        )
    migrate_all(op, obs)
    [report] = repo.list_agent_reports(latest_per_node=True)
    assert report["report_id"] == "SENTINEL"


def test_settings_retention_floor(monkeypatch):
    monkeypatch.setenv("DMS_AGENT_REPORT_RETENTION_SECONDS", "3600")  # 1h, below floor
    settings = Settings.from_env()
    # floored to >= 7 days so it can never sit under the 72h metrics window
    assert settings.agent_report_retention_seconds == 7 * 24 * 60 * 60


# --------------------------------------------------------------------------- #
# (F) retention CLI interval falls back to the setting when --interval omitted
# --------------------------------------------------------------------------- #
def test_retention_cli_interval_defaults_to_setting(tmp_path, monkeypatch):
    from dms import cli

    monkeypatch.setenv("DMS_DATABASE_URL", f"sqlite:///{tmp_path / 'op.db'}")
    monkeypatch.setenv(
        "DMS_OBSERVABILITY_DATABASE_URL", f"sqlite:///{tmp_path / 'obs.db'}"
    )
    monkeypatch.setenv("DMS_AGENT_REPORT_RETENTION_INTERVAL_SECONDS", "1234")

    captured: dict[str, Any] = {}

    def fake_loop(runner, *, loop, interval):
        captured["loop"] = loop
        captured["interval"] = interval
        return 0

    monkeypatch.setattr(cli, "_run_once_or_loop", fake_loop)

    # --interval omitted -> falls back to DMS_AGENT_REPORT_RETENTION_INTERVAL_SECONDS
    assert cli.main(["retention", "--loop"]) == 0
    assert captured["interval"] == 1234.0

    # --interval given explicitly still wins over the setting
    assert cli.main(["retention", "--loop", "--interval", "7"]) == 0
    assert captured["interval"] == 7.0
