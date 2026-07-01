"""Read-API completeness + low-overhead changes for the operator dashboard.

Covers:
- latest_per_node agent-report reads (one newest row per node/role, server-side dedup),
- limit/offset pagination on the list endpoints (no overlap / no gaps),
- exact COUNT(*) work-summary totals that never saturate at a list cap,
- action_required still surfacing issues when a constituent table exceeds the old cap,
- the new optional query params on the HTTP routes (back-compatible defaults).

All against SQLite, mirroring the existing repository/route test fixtures.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import (
    DataJobState,
    LifecycleState,
    OperationKind,
    ResourceKind,
    StorageMappingInput,
    WorkerRole,
)
from dms.migrations import migrate_all
from dms.query import OperationalQueryService, _action_fingerprint
from dms.repositories import (
    ATTENTION_RUN_STATES,
    DmsRepository,
    ObservabilityRepository,
)


API_HEADERS = {"x-dms-actor": "api-client"}
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


def _bulk_insert(repo: DmsRepository, table: str, columns: list[str], rows: list[tuple]) -> None:
    placeholders = ",".join(["?"] * len(columns))
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    with repo.database.connect() as connection:
        connection.executemany(sql, rows)


def _bulk_active_plans(repo: DmsRepository, n: int) -> str:
    request_id = _seed_request(repo)
    columns = [
        "plan_id", "request_id", "status", "worker_role", "operation_kind",
        "resource_key", "desired_state", "precondition", "execution_metadata",
        "attempt_count", "created_at", "updated_at",
    ]
    rows = [
        (
            f"plan_{i}", request_id, LifecycleState.PLANNED.value, "RM",
            OperationKind.DATA_SYNC.value, "cephfs-a:proj", "{}", "{}", "{}",
            0, _TS, _TS,
        )
        for i in range(n)
    ]
    _bulk_insert(repo, "plans", columns, rows)
    return request_id


def _bulk_attention_runs(repo: DmsRepository, n: int) -> None:
    request_id = _seed_request(repo)
    # Parent plan exists only to satisfy the runs FK; give it a TERMINAL status so
    # it does not perturb active-plan counts in tests that combine plans + runs.
    plan_id = "plan_parent"
    _bulk_insert(
        repo,
        "plans",
        [
            "plan_id", "request_id", "status", "worker_role", "operation_kind",
            "resource_key", "desired_state", "precondition", "execution_metadata",
            "attempt_count", "created_at", "updated_at",
        ],
        [(
            plan_id, request_id, LifecycleState.SUCCEEDED.value, "RM",
            OperationKind.DATA_SYNC.value, "cephfs-a:proj", "{}", "{}", "{}",
            0, _TS, _TS,
        )],
    )
    columns = [
        "run_id", "request_id", "plan_id", "worker_id", "executor_id",
        "worker_role", "lease_expires_at", "heartbeat_at", "state",
        "started_at", "updated_at",
    ]
    rows = [
        (
            f"run_{i}", request_id, plan_id, "w1", "e1", "RM", _TS, _TS,
            LifecycleState.RECOVERY_NEEDED.value, _TS, _TS,
        )
        for i in range(n)
    ]
    _bulk_insert(repo, "runs", columns, rows)


def _bulk_failed_data_jobs(repo: DmsRepository, n: int) -> None:
    request_id = _seed_request(repo)
    columns = [
        "job_id", "request_id", "operation", "storage_name", "source",
        "destination", "target", "priority", "selected_tool", "worker_pool",
        "state", "artifact_uri", "normalized_target", "preflight_result",
        "volcano_job_ref", "result_summary", "log_uri", "preview_expires_at",
        "created_at", "updated_at",
    ]
    rows = [
        (
            f"job_{i}", request_id, OperationKind.DATA_SYNC.value, "cephfs-a",
            None, None, "proj", 100, "dsync", "{}", DataJobState.FAILED.value,
            None, "{}", "{}", "{}", "{}", None, None, _TS, _TS,
        )
        for i in range(n)
    ]
    _bulk_insert(repo, "data_jobs", columns, rows)


def _register_mapping(repo: DmsRepository, storage_name: str) -> None:
    repo.upsert_storage_mapping(
        StorageMappingInput(
            storage_name=storage_name,
            backend_template={"backend_type": "cephfs"},
            cluster_name="cluster-a",
            storage_class_name=f"{storage_name}-sc",
            sanity_status="Ready",
        ),
        actor="admin",
    )


# --------------------------------------------------------------------------- #
# (a) latest_per_node
# --------------------------------------------------------------------------- #
def test_latest_per_node_returns_one_newest_row_per_node_role(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    # w1/RM: three reports, newest at -1m
    repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(minutes=5)))
    repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(minutes=3)))
    newest_w1_rm = repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(minutes=1)))
    # w1/DM: a different role for the same node is a distinct identity
    newest_w1_dm = repo.ingest_agent_report(_rep("w1", "DM", now - timedelta(minutes=2)))
    # w2/RM: two reports
    repo.ingest_agent_report(_rep("w2", "RM", now - timedelta(minutes=10)))
    newest_w2_rm = repo.ingest_agent_report(_rep("w2", "RM", now - timedelta(minutes=4)))

    latest = repo.list_agent_reports(latest_per_node=True)

    # exactly one row per (node, role)
    keys = sorted((r["node_name"], r["worker_role"]) for r in latest)
    assert keys == [("w1", "DM"), ("w1", "RM"), ("w2", "RM")]
    assert len(latest) == 3
    # and each is the NEWEST report for its node/role
    assert {r["report_id"] for r in latest} == {
        newest_w1_rm,
        newest_w1_dm,
        newest_w2_rm,
    }


def test_latest_per_node_breaks_reported_at_ties_deterministically(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    same = now - timedelta(minutes=1)
    # two reports with the SAME reported_at for one node/role
    id_a = repo.ingest_agent_report(_rep("w1", "RM", same))
    id_b = repo.ingest_agent_report(_rep("w1", "RM", same))

    latest = repo.list_agent_reports(latest_per_node=True)

    # still exactly one row (tie broken by report_id, the unique PK)
    assert len(latest) == 1
    assert latest[0]["report_id"] in {id_a, id_b}


def test_latest_per_node_respects_freshness_filter(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    # w1/RM: an old report (will go Stale) then a recent one — w1's CURRENT report
    # is Fresh (a newer Fresh report resolves the older Stale one).
    repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(hours=2)))
    recent = repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(seconds=5)))
    # w2/RM: only an old report — w2's CURRENT (and only) report is Stale.
    repo.ingest_agent_report(_rep("w2", "RM", now - timedelta(hours=2)))
    repo.mark_stale_agent_reports(stale_seconds=300)

    # no freshness filter -> one latest row per node: w1 (Fresh), w2 (Stale)
    latest = {
        (r["node_name"], r["freshness_status"])
        for r in repo.list_agent_reports(latest_per_node=True)
    }
    assert latest == {("w1", "Fresh"), ("w2", "Stale")}

    # freshness="Fresh" -> nodes whose CURRENT report is Fresh (w1 only)
    fresh = repo.list_agent_reports(latest_per_node=True, freshness="Fresh")
    assert [r["report_id"] for r in fresh] == [recent]

    # freshness="Stale" -> nodes whose CURRENT report is Stale (w2 only); w1 is NOT
    # returned because its latest report is Fresh (the stale one is superseded).
    stale = repo.list_agent_reports(latest_per_node=True, freshness="Stale")
    assert len(stale) == 1
    assert stale[0]["node_name"] == "w2"
    assert stale[0]["freshness_status"] == "Stale"


# --------------------------------------------------------------------------- #
# (b) limit / offset pagination
# --------------------------------------------------------------------------- #
def test_agent_reports_limit_offset_paginate_without_overlap_or_gaps(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    ids = [
        repo.ingest_agent_report(_rep(f"n{i}", "RM", now - timedelta(minutes=i)))
        for i in range(10)
    ]

    page1 = repo.list_agent_reports(limit=4, offset=0)
    page2 = repo.list_agent_reports(limit=4, offset=4)
    page3 = repo.list_agent_reports(limit=4, offset=8)
    combined = page1 + page2 + page3
    seen = [r["report_id"] for r in combined]

    assert [len(page1), len(page2), len(page3)] == [4, 4, 2]
    assert len(seen) == len(set(seen)) == 10  # no overlap, no dup
    assert set(seen) == set(ids)  # no gaps — every row reachable
    times = [r["reported_at"] for r in combined]
    assert times == sorted(times, reverse=True)  # global DESC order preserved


def test_data_jobs_offset_paginates(tmp_path):
    repo = _repo(tmp_path)
    _bulk_failed_data_jobs(repo, 6)
    full = repo.list_data_jobs(limit=100)
    assert len(full) == 6
    tail = repo.list_data_jobs(limit=100, offset=4)
    assert len(tail) == 2
    assert [j["job_id"] for j in tail] == [j["job_id"] for j in full[4:]]


def test_storage_mappings_limit_offset(tmp_path):
    repo = _repo(tmp_path)
    for name in ("s1", "s2", "s3"):
        _register_mapping(repo, name)
    assert len(repo.list_storage_mappings(limit=2)) == 2
    assert len(repo.list_storage_mappings(limit=2, offset=2)) == 1
    assert len(repo.list_storage_mappings(limit=10000)) == 3


# --------------------------------------------------------------------------- #
# (c) exact COUNT(*) totals (no saturation beyond the old 1000 list cap)
# --------------------------------------------------------------------------- #
def test_count_active_plans_exact_beyond_list_cap(tmp_path):
    repo = _repo(tmp_path)
    _bulk_active_plans(repo, 1500)
    # the list path still saturates at its cap; the COUNT path is exact
    assert len(repo.list_active_plans(limit=1000)) == 1000
    assert repo.count_active_plans() == 1500


def test_count_runs_exact_beyond_list_cap(tmp_path):
    repo = _repo(tmp_path)
    _bulk_attention_runs(repo, 1500)
    assert len(repo.list_runs(states=ATTENTION_RUN_STATES, limit=1000)) == 1000
    assert repo.count_runs(states=ATTENTION_RUN_STATES) == 1500


def test_count_methods_match_list_where_clauses(tmp_path):
    repo = _repo(tmp_path)
    # drive a couple of plans/runs through the real lifecycle into mixed states
    req_a = _seed_request(repo)
    plan_a = repo.create_plan(
        request_id=req_a, worker_role=WorkerRole.RM,
        operation_kind=OperationKind.DATA_SYNC.value, resource_key="cephfs-a:proj",
        desired_state={}, precondition={}, execution_metadata={},
    )
    run_a = repo.claim_plan(
        plan_id=plan_a, worker_id="w1", executor_id="e1", lease_seconds=300
    )
    repo.update_run_state(run_a, LifecycleState.RUNNING, reason="go", actor="w1")

    req_b = _seed_request(repo)
    plan_b = repo.create_plan(
        request_id=req_b, worker_role=WorkerRole.DM,
        operation_kind=OperationKind.DATA_SCAN.value, resource_key="cephfs-a:proj",
        desired_state={}, precondition={}, execution_metadata={},
    )  # stays PLANNED (active plan, no run yet)

    # counts equal the (uncapped) list cardinalities, with identical WHERE clauses
    assert repo.count_active_plans() == len(repo.list_active_plans(limit=1000))
    assert repo.count_active_runs() == len(repo.list_active_runs(limit=1000))
    assert repo.count_active_runs(worker_role="RM") == len(
        repo.list_active_runs(worker_role="RM", limit=1000)
    )
    assert repo.count_runs() == len(repo.list_runs(limit=1000))
    assert repo.count_runs(states=ATTENTION_RUN_STATES) == len(
        repo.list_runs(states=ATTENTION_RUN_STATES, limit=1000)
    )


def test_work_summary_totals_use_exact_counts(tmp_path):
    service = _query_service(tmp_path)
    repo = service.repository
    _bulk_active_plans(repo, 1200)
    _bulk_attention_runs(repo, 1100)

    summary = service.work_summary()

    assert summary["plans"]["total_active"] == 1200
    assert summary["runs"]["stale_or_recovery"] == 1100


# --------------------------------------------------------------------------- #
# (d) action_required still complete when a constituent table exceeds old caps
# --------------------------------------------------------------------------- #
def test_action_required_surfaces_data_jobs_beyond_old_cap(tmp_path):
    service = _query_service(tmp_path)
    _bulk_failed_data_jobs(service.repository, 1001)

    issues = service.action_required()
    data_job_issues = [
        i for i in issues if i.get("resource_kind") == ResourceKind.DATA_JOB.value
    ]
    # old cap was 1000 -> would have silently dropped one; raised cap surfaces all
    assert len(data_job_issues) == 1001


_DJ_COLUMNS = [
    "job_id", "request_id", "operation", "storage_name", "source", "destination",
    "target", "priority", "selected_tool", "worker_pool", "state", "artifact_uri",
    "normalized_target", "preflight_result", "volcano_job_ref", "result_summary",
    "log_uri", "preview_expires_at", "created_at", "updated_at",
]


def _dj_row(job_id, request_id, state, updated_at):
    return (
        job_id, request_id, OperationKind.DATA_SYNC.value, "cephfs-a", None, None,
        "proj", 100, "dsync", "{}", state, None, "{}", "{}", "{}", "{}", None, None,
        updated_at, updated_at,
    )


def test_data_job_attention_window_and_cancelled_excluded(tmp_path):
    # (A′) recency window + (B) Cancelled excluded — records preserved, alarm bounded.
    op = Database(f"sqlite:///{tmp_path / 'op.db'}")
    obs = Database(f"sqlite:///{tmp_path / 'obs.db'}")
    migrate_all(op, obs)
    repo = DmsRepository(op)
    service = OperationalQueryService(
        repo, ObservabilityRepository(obs), data_job_attention_window_seconds=3600,
    )
    req = _seed_request(repo)
    now = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    _bulk_insert(repo, "data_jobs", _DJ_COLUMNS, [
        _dj_row("job_recent_fail", req, DataJobState.FAILED.value, now),
        _dj_row("job_old_fail", req, DataJobState.FAILED.value, old),        # outside window
        _dj_row("job_recent_cancel", req, DataJobState.CANCELLED.value, now),  # B: not an alarm
    ])
    issues = service.action_required()
    dj_ids = {
        i["job_id"] for i in issues if i.get("resource_kind") == ResourceKind.DATA_JOB.value
    }
    assert dj_ids == {"job_recent_fail"}
    # all 3 rows still exist (history preserved) — only the alarm is filtered
    assert repo.count_data_jobs() == 3
    # count stays consistent with the (windowed, cancelled-excluded) list
    assert service.action_required_count() == len(issues)


def test_blocked_request_not_action_required(tmp_path):
    # (B) Blocked (awaiting confirm) is not a global action item; other stuck states are.
    service = _query_service(tmp_path)  # window disabled (dataclass default)
    repo = service.repository
    blocked = _seed_request(repo)
    stuck = _seed_request(repo)
    with repo.database.connect() as conn:
        conn.execute("UPDATE requests SET status = ? WHERE request_id = ?",
                     (LifecycleState.BLOCKED.value, blocked))
        conn.execute("UPDATE requests SET status = ? WHERE request_id = ?",
                     (LifecycleState.RECOVERY_NEEDED.value, stuck))
    req_ids = {r.get("request_id") for r in repo.list_action_required()}
    assert blocked not in req_ids     # Blocked no longer alarms
    assert stuck in req_ids           # genuine stuck state still does
    assert repo.get_request(blocked)["status"] == LifecycleState.BLOCKED.value  # record intact


def test_action_ack_hides_item_preserves_record_and_count(tmp_path):
    # (D) server-side ACK: mark an item handled → excluded from action_required for all
    # clients; the underlying record is untouched; count stays consistent; unack restores.
    service = _query_service(tmp_path)
    repo = service.repository
    req = _seed_request(repo)
    with repo.database.connect() as conn:
        conn.execute("UPDATE requests SET status = ? WHERE request_id = ?",
                     (LifecycleState.RECOVERY_NEEDED.value, req))
    before = service.action_required()
    item = next(i for i in before if i.get("issue_type") == "request_attention")
    fp = _action_fingerprint(item)
    assert service.action_required_count() == len(before)

    # ack it
    assert repo.add_action_acks([{"fingerprint": fp, "issue_type": "request_attention"}], acked_by="op") == 1
    after = service.action_required()
    assert fp not in {_action_fingerprint(i) for i in after}          # hidden
    assert service.action_required_count() == len(after)              # count consistent (materialized path)
    assert repo.get_request(req)["status"] == LifecycleState.RECOVERY_NEEDED.value  # record intact
    assert [a["fingerprint"] for a in repo.list_action_acks()] == [fp]

    # unack -> reappears
    assert repo.remove_action_acks([fp]) == 1
    assert fp in {_action_fingerprint(i) for i in service.action_required()}
    assert service.action_required_count() == len(before)


def test_action_required_uses_latest_per_node_for_staleness(tmp_path):
    service = _query_service(tmp_path)
    repo = service.repository
    now = datetime.now(timezone.utc)
    # w1/RM: old report + a recent one -> latest is Fresh -> NOT an action item
    repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(hours=2)))
    repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(seconds=5)))
    # w2/DM: only an old report -> latest is Stale -> IS an action item
    repo.ingest_agent_report(_rep("w2", "DM", now - timedelta(hours=3)))
    repo.mark_stale_agent_reports(stale_seconds=300)

    stale = [i for i in service.action_required() if i["issue_type"] == "agent_report_stale"]

    assert len(stale) == 1
    assert (stale[0]["node_name"], stale[0]["worker_role"]) == ("w2", "DM")


# --------------------------------------------------------------------------- #
# HTTP routes: new optional params, back-compatible defaults
# --------------------------------------------------------------------------- #
class _RecordingVolcano:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def volcano_job_metrics(self, *, limit: int = 300) -> dict[str, Any]:
        self.calls.append(limit)
        return {"jobs": [], "error": None}


@pytest.fixture()
def client(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'op.db'}"
    observability_url = f"sqlite:///{tmp_path / 'obs.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    observability = ObservabilityRepository(observability_db)
    app = create_app(settings, repository, observability)
    return app, repository


def test_route_agent_reports_latest_per_node(client):
    app, repo = client
    now = datetime.now(timezone.utc)
    repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(minutes=5)))
    repo.ingest_agent_report(_rep("w1", "RM", now - timedelta(minutes=1)))
    repo.ingest_agent_report(_rep("w2", "DM", now - timedelta(minutes=2)))
    tc = TestClient(app)

    resp = tc.get(
        "/api/v1/operations/agent-reports?latest_per_node=true", headers=API_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert sorted((r["node_name"], r["worker_role"]) for r in body) == [
        ("w1", "RM"),
        ("w2", "DM"),
    ]

    # default (no param) keeps the full history view
    resp_all = tc.get("/api/v1/operations/agent-reports", headers=API_HEADERS)
    assert resp_all.status_code == 200
    assert len(resp_all.json()) == 3


def test_route_storage_mappings_limit_offset(client):
    app, repo = client
    for name in ("s1", "s2", "s3"):
        _register_mapping(repo, name)
    tc = TestClient(app)
    resp = tc.get("/api/v1/operations/storage-mappings?limit=2", headers=API_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    resp2 = tc.get(
        "/api/v1/operations/storage-mappings?limit=2&offset=2", headers=API_HEADERS
    )
    assert len(resp2.json()) == 1


def test_route_data_jobs_offset_and_stale_runs_limit(client):
    app, repo = client
    _bulk_failed_data_jobs(repo, 5)
    tc = TestClient(app)
    resp = tc.get("/api/v1/operations/data-jobs?limit=100&offset=3", headers=API_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    # runs/stale now accepts limit/offset (default behavior preserved)
    resp_stale = tc.get("/api/v1/operations/runs/stale?limit=2000", headers=API_HEADERS)
    assert resp_stale.status_code == 200
    assert resp_stale.json() == []


def test_route_work_summary_exact_totals(client):
    app, repo = client
    _bulk_active_plans(repo, 1050)
    tc = TestClient(app)
    resp = tc.get("/api/v1/operations/work-summary", headers=API_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["plans"]["total_active"] == 1050


def test_route_volcano_job_metrics_default_and_clamp(client):
    app, _repo_ = client
    recorder = _RecordingVolcano()
    app.state.services.volcano_adapter = recorder
    tc = TestClient(app)

    assert tc.get("/api/v1/operations/volcano/job-metrics", headers=API_HEADERS).status_code == 200
    assert tc.get(
        "/api/v1/operations/volcano/job-metrics?limit=99999", headers=API_HEADERS
    ).status_code == 200

    # default raised 300 -> 1000; max clamp raised 1000 -> 2000
    assert recorder.calls == [1000, 2000]
