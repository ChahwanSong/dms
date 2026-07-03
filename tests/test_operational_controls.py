from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dms.adapters import (
    StubFilesystemBackendAdapter,
    StubKubernetesNamespaceQuotaAdapter,
)
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import (
    DataJobState,
    LifecycleState,
    OperationKind,
    ResourceKind,
    StorageMappingInput,
)
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository, SchedulingBlocked
from dms.workers import RMWorkerRuntime, RunHeartbeat

AUTH_HEADERS = {"x-dms-actor": "api-client"}


@pytest.fixture()
def harness(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
        worker_lease_seconds=2,
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    observability = ObservabilityRepository(observability_db)
    app = create_app(settings, repository, observability)
    return {
        "client": TestClient(app),
        "repository": repository,
        "observability": observability,
    }


def test_maintenance_blocks_mutating_requests_but_allows_queries(harness):
    client: TestClient = harness["client"]
    repository: DmsRepository = harness["repository"]

    response = client.post(
        "/api/v1/operations/control-state:enter-maintenance",
        json={"reason": "source update"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["control_state"]["scheduling_blocked"] is True
    blocked = client.post(
        "/api/v1/resource-management/filesystems",
        json=_filesystem_body("blocked-during-maintenance"),
        headers=AUTH_HEADERS,
    )
    assert blocked.status_code == 409
    assert repository.list_requests(requester_id="user-1") == []

    assert (
        client.get("/api/v1/operations/control-state", headers=AUTH_HEADERS).status_code
        == 200
    )
    summary = client.get("/api/v1/operations/work-summary", headers=AUTH_HEADERS)
    assert summary.status_code == 200
    assert summary.json()["plans"]["total_active"] == 0
    [mutation] = repository.list_control_mutations(limit=1)
    assert mutation["mutation_kind"] == "control.enter_maintenance"


def test_drain_blocks_worker_claim_until_resume(harness):
    client: TestClient = harness["client"]
    repository: DmsRepository = harness["repository"]
    observability: ObservabilityRepository = harness["observability"]
    _register_ready_storage_mapping(repository)
    request_id = _create_filesystem_request(repository, "drain-claim")
    Planner(repository).run_once()
    plan = repository.get_plan_by_request(request_id)

    client.post(
        "/api/v1/operations/control-state:begin-drain",
        json={"reason": "planned shutdown"},
        headers=AUTH_HEADERS,
    )
    worker = _rm_worker(repository, observability)

    assert worker.run_once() == 0
    assert (
        repository.get_plan(plan["plan_id"])["status"] == LifecycleState.PLANNED.value
    )

    resume = client.post(
        "/api/v1/operations/control-state:resume",
        json={"reason": "shutdown cancelled"},
        headers=AUTH_HEADERS,
    )
    assert resume.status_code == 200
    assert worker.run_once() == 1
    assert (
        repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    )


def test_claim_plan_transaction_refuses_when_scheduling_blocked(harness):
    client: TestClient = harness["client"]
    repository: DmsRepository = harness["repository"]
    _register_ready_storage_mapping(repository)
    request_id = _create_filesystem_request(repository, "claim-race")
    Planner(repository).run_once()
    plan = repository.get_plan_by_request(request_id)

    client.post(
        "/api/v1/operations/control-state:enter-maintenance",
        json={"reason": "race guard"},
        headers=AUTH_HEADERS,
    )

    with pytest.raises(SchedulingBlocked):
        repository.claim_plan(
            plan_id=plan["plan_id"],
            worker_id="rm-worker",
            executor_id="rm-worker",
            lease_seconds=30,
        )


def test_run_heartbeat_extends_lease(harness):
    repository: DmsRepository = harness["repository"]
    observability: ObservabilityRepository = harness["observability"]
    _register_ready_storage_mapping(repository)
    request_id = _create_filesystem_request(repository, "heartbeat")
    Planner(repository).run_once()
    plan = repository.get_plan_by_request(request_id)
    run_id = repository.claim_plan(
        plan_id=plan["plan_id"],
        worker_id="rm-worker",
        executor_id="rm-worker",
        lease_seconds=1,
    )
    before = repository.list_active_runs(limit=1)[0]["lease_expires_at"]

    with RunHeartbeat(
        repository=repository,
        observability=observability,
        run_id=run_id,
        worker_id="rm-worker",
        lease_seconds=1,
        interval_seconds=0.05,
    ):
        time.sleep(0.16)

    after = repository.list_active_runs(limit=1)[0]["lease_expires_at"]
    assert after > before


def test_mark_stale_runs_classifies_claimed_and_active_side_effect_states(harness):
    repository: DmsRepository = harness["repository"]
    _register_ready_storage_mapping(repository)
    claimed_request_id = _create_filesystem_request(repository, "stale-claimed")
    applying_request_id = _create_filesystem_request(repository, "stale-applying")
    Planner(repository).run_once(limit=10)
    claimed_plan = repository.get_plan_by_request(claimed_request_id)
    applying_plan = repository.get_plan_by_request(applying_request_id)
    claimed_run_id = repository.claim_plan(
        plan_id=claimed_plan["plan_id"],
        worker_id="rm-worker",
        executor_id="rm-worker",
        lease_seconds=1,
    )
    applying_run_id = repository.claim_plan(
        plan_id=applying_plan["plan_id"],
        worker_id="rm-worker",
        executor_id="rm-worker",
        lease_seconds=1,
    )
    repository.update_run_state(
        applying_run_id,
        LifecycleState.APPLYING,
        reason="test active side effect state",
        actor="rm-worker",
    )
    _expire_runs(repository, claimed_run_id, applying_run_id)

    assert repository.mark_stale_runs(actor="test") == 2
    runs = {run["run_id"]: run for run in repository.list_runs(limit=10)}
    assert runs[claimed_run_id]["state"] == LifecycleState.STALE_CLAIM.value
    assert runs[applying_run_id]["state"] == LifecycleState.RECOVERY_NEEDED.value
    assert (
        repository.get_request(claimed_request_id)["status"]
        == LifecycleState.STALE_CLAIM.value
    )
    assert (
        repository.get_request(applying_request_id)["status"]
        == LifecycleState.RECOVERY_NEEDED.value
    )


def _park_blocked_run(repository: DmsRepository, directory_name: str) -> tuple[str, str]:
    """Create request→plan→claimed run, then park it in Blocked like a DM preview.
    Returns (plan_id, run_id). update_run_state cascades plan+request to Blocked too."""
    request_id = _create_filesystem_request(repository, directory_name)
    Planner(repository).run_once(limit=10)
    plan = repository.get_plan_by_request(request_id)
    run_id = repository.claim_plan(
        plan_id=plan["plan_id"], worker_id="dm-worker",
        executor_id="dm-worker", lease_seconds=30,
    )
    repository.update_run_state(
        run_id, LifecycleState.BLOCKED,
        reason="preview succeeded; waiting for user confirm in data_jobs",
        actor="dm-worker",
    )
    return plan["plan_id"], run_id


def test_close_superseded_preview_runs_sweep(harness):
    repository: DmsRepository = harness["repository"]
    _register_ready_storage_mapping(repository)
    p1, run_await = _park_blocked_run(repository, "preview-await")
    p2, run_superseded = _park_blocked_run(repository, "preview-superseded")

    # both plans still Blocked (legitimately awaiting confirm) → sweep closes nothing
    assert repository.close_superseded_preview_runs(actor="test") == 0
    runs = {r["run_id"]: r for r in repository.list_runs(limit=10)}
    assert runs[run_await]["state"] == LifecycleState.BLOCKED.value
    assert runs[run_superseded]["state"] == LifecycleState.BLOCKED.value

    # confirm p2 → plan leaves Blocked; its preview run is now orphaned
    repository.update_plan_status(
        p2, LifecycleState.PLANNED, reason="data job confirmed", actor="test"
    )
    # sweep closes ONLY the orphaned run; the still-awaiting one is preserved
    assert repository.close_superseded_preview_runs(actor="test") == 1
    runs = {r["run_id"]: r for r in repository.list_runs(limit=10)}
    assert runs[run_superseded]["state"] == LifecycleState.SUCCEEDED.value
    assert runs[run_await]["state"] == LifecycleState.BLOCKED.value
    # idempotent
    assert repository.close_superseded_preview_runs(actor="test") == 0


def _stuck_run(
    repository: DmsRepository,
    directory_name: str,
    run_state: LifecycleState,
    request_status: LifecycleState | None = None,
) -> tuple[str, str, str]:
    """request→plan→claimed run, then drive the run to `run_state` (cascades request+
    plan too), and optionally override the REQUEST status. Returns (request_id, plan_id,
    run_id). request_status=None leaves it equal to run_state (open attention)."""
    request_id = _create_filesystem_request(repository, directory_name)
    Planner(repository).run_once(limit=10)
    plan = repository.get_plan_by_request(request_id)
    run_id = repository.claim_plan(
        plan_id=plan["plan_id"], worker_id="w", executor_id="w", lease_seconds=30
    )
    repository.update_run_state(run_id, run_state, reason="stuck", actor="w")
    if request_status is not None:
        repository.update_request_status(
            request_id, request_status, reason="test terminal", actor="w"
        )
    return request_id, plan["plan_id"], run_id


def test_close_orphaned_stuck_runs_various_cases(harness):
    repository: DmsRepository = harness["repository"]
    _register_ready_storage_mapping(repository)
    # orphans: stuck run whose REQUEST is terminal → closed, run state MIRRORS request
    _, _, r_stale = _stuck_run(
        repository, "o-stale", LifecycleState.STALE_CLAIM, LifecycleState.CANCELLED
    )
    _, _, r_recov = _stuck_run(
        repository, "o-recov", LifecycleState.RECOVERY_NEEDED, LifecycleState.FAILED
    )
    _, _, r_unknown = _stuck_run(
        repository, "o-unknown", LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT,
        LifecycleState.SUCCEEDED,
    )
    # NOT orphan: stuck run whose request is still OPEN (RecoveryNeeded) → preserved
    _, _, r_open = _stuck_run(repository, "open-recov", LifecycleState.RECOVERY_NEEDED)
    # NOT swept: request is BackendApplyFailed — terminal BUT also an attention run state
    # and still actionable (resolvable) in 조치 필요, so the sweep must leave it alone.
    _, _, r_baf = _stuck_run(
        repository, "baf", LifecycleState.RECOVERY_NEEDED, LifecycleState.BACKEND_APPLY_FAILED
    )
    # NOT targeted: an ACTIVE-state run (Running) even with a terminal request — the
    # sweep only touches the stuck attention states, never in-flight runs.
    _, _, r_active = _stuck_run(
        repository, "active-run", LifecycleState.RUNNING, LifecycleState.CANCELLED
    )

    assert repository.close_orphaned_stuck_runs(actor="test") == 3
    runs = {r["run_id"]: r for r in repository.list_runs(limit=50)}
    assert runs[r_stale]["state"] == LifecycleState.CANCELLED.value    # mirrors request
    assert runs[r_recov]["state"] == LifecycleState.FAILED.value
    assert runs[r_unknown]["state"] == LifecycleState.SUCCEEDED.value
    assert runs[r_open]["state"] == LifecycleState.RECOVERY_NEEDED.value  # preserved
    assert runs[r_baf]["state"] == LifecycleState.RECOVERY_NEEDED.value   # BackendApplyFailed req → left
    assert runs[r_active]["state"] == LifecycleState.RUNNING.value        # untouched
    # request/plan of an orphan are NOT changed by the sweep (already terminal)
    assert repository.get_request(runs[r_recov]["request_id"])["status"] == LifecycleState.FAILED.value
    # idempotent
    assert repository.close_orphaned_stuck_runs(actor="test") == 0


def _confirm_pending_job(
    repository: DmsRepository,
    directory_name: str,
    *,
    preview_expires_at: str | None,
    state: DataJobState = DataJobState.CONFIRM_PENDING,
) -> tuple[str, str, str]:
    """request→plan + a data job in `state` with the given preview_expires_at.
    Returns (request_id, plan_id, job_id)."""
    request_id = _create_filesystem_request(repository, directory_name)
    Planner(repository).run_once(limit=10)
    plan = repository.get_plan_by_request(request_id)
    job_id = repository.create_data_job(
        request_id=request_id, operation="data.sync", storage_name="cephfs-a",
        source="/src", destination="/dst", target=None, priority=1, worker_pool={},
    )
    repository.update_data_job(job_id, state=state, preview_expires_at=preview_expires_at)
    return request_id, plan["plan_id"], job_id


def test_expire_stale_preview_jobs_various_cases(harness):
    repository: DmsRepository = harness["repository"]
    _register_ready_storage_mapping(repository)
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()

    r_exp, _, j_exp = _confirm_pending_job(repository, "prev-expired", preview_expires_at=past)
    r_fut, _, j_fut = _confirm_pending_job(repository, "prev-future", preview_expires_at=future)
    r_null, _, j_null = _confirm_pending_job(repository, "prev-null", preview_expires_at=None)
    # a non-ConfirmPending job with a past expiry must NOT be swept
    r_run, _, j_run = _confirm_pending_job(
        repository, "prev-running", preview_expires_at=past, state=DataJobState.RUNNING
    )

    assert repository.expire_stale_preview_jobs(actor="test") == 1
    # expired one: job → PreviewExpired, request → Rejected (terminal)
    assert repository.get_data_job(j_exp)["state"] == DataJobState.PREVIEW_EXPIRED.value
    assert repository.get_request(r_exp)["status"] == LifecycleState.REJECTED.value
    # future / null / non-ConfirmPending are untouched
    assert repository.get_data_job(j_fut)["state"] == DataJobState.CONFIRM_PENDING.value
    assert repository.get_data_job(j_null)["state"] == DataJobState.CONFIRM_PENDING.value
    assert repository.get_data_job(j_run)["state"] == DataJobState.RUNNING.value
    # idempotent
    assert repository.expire_stale_preview_jobs(actor="test") == 0


def test_close_superseded_preview_runs_inline_by_plan(harness):
    repository: DmsRepository = harness["repository"]
    _register_ready_storage_mapping(repository)
    p1, run_id = _park_blocked_run(repository, "inline-close")
    # inline (plan-scoped, e.g. on confirm/cancel) closes the parked run immediately,
    # regardless of the plan's current status.
    assert repository.close_superseded_preview_runs(actor="test", plan_id=p1) == 1
    runs = {r["run_id"]: r for r in repository.list_runs(limit=10)}
    assert runs[run_id]["state"] == LifecycleState.SUCCEEDED.value
    # a Blocked run does NOT get reaped by mark_stale_runs (only active-state leases)
    assert repository.close_superseded_preview_runs(actor="test", plan_id=p1) == 0


def test_operational_queries_and_resume_blockers(harness):
    client: TestClient = harness["client"]
    repository: DmsRepository = harness["repository"]
    _register_ready_storage_mapping(repository)
    request_id = _create_filesystem_request(repository, "active-query")
    Planner(repository).run_once()
    plan = repository.get_plan_by_request(request_id)
    run_id = repository.claim_plan(
        plan_id=plan["plan_id"],
        worker_id="rm-worker",
        executor_id="rm-worker",
        lease_seconds=30,
    )
    repository.update_run_state(
        run_id,
        LifecycleState.APPLYING,
        reason="test active query",
        actor="rm-worker",
    )

    work_summary = client.get("/api/v1/operations/work-summary", headers=AUTH_HEADERS)
    active_runs = client.get("/api/v1/operations/runs/active", headers=AUTH_HEADERS)
    drain_status = client.get("/api/v1/operations/drain-status", headers=AUTH_HEADERS)
    assert work_summary.json()["runs"]["by_state"] == {"Applying": 1}
    assert active_runs.json()[0]["run_id"] == run_id
    assert drain_status.json()["ready_for_shutdown"] is False

    _expire_runs(repository, run_id)
    assert (
        client.post("/api/v1/operations/runs:mark-stale", headers=AUTH_HEADERS).json()[
            "marked"
        ]
        == 1
    )
    client.post(
        "/api/v1/operations/control-state:begin-drain",
        json={"reason": "resume blocked"},
        headers=AUTH_HEADERS,
    )
    resume = client.post(
        "/api/v1/operations/control-state:resume",
        json={"reason": "should fail"},
        headers=AUTH_HEADERS,
    )
    assert resume.status_code == 409
    forced = client.post(
        "/api/v1/operations/control-state:resume",
        json={"reason": "operator accepted recovery", "force": True},
        headers=AUTH_HEADERS,
    )
    assert forced.status_code == 200
    [mutation] = repository.list_control_mutations(limit=1)
    assert mutation["mutation_kind"] == "control.resume"
    assert mutation["payload"]["force"] is True


def _filesystem_body(directory_name: str) -> dict[str, Any]:
    return {
        "requester_id": "user-1",
        "payload": _filesystem_payload(directory_name),
    }


def _filesystem_payload(directory_name: str) -> dict[str, Any]:
    return {
        "storage_name": "cephfs-a",
        "directory_name": directory_name,
        "resource_type": "user",
        "users": ["alice", "bob"],
        "expires_at": "2099-01-01T00:00:00Z",
    }


def _create_filesystem_request(repository: DmsRepository, directory_name: str) -> str:
    return repository.create_request(
        requester_id="user-1",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key=f"cephfs-a:{directory_name}",
        payload=_filesystem_payload(directory_name),
    )


def _register_ready_storage_mapping(repository: DmsRepository) -> None:
    sanity = {
        "storage_name": "cephfs-a",
        "status": "Ready",
        "checked_at": "2026-06-03T00:00:00+00:00",
        "readiness": {
            "resource_management": "Ready",
            "data_management": "Ready",
            "inventory": "Ready",
        },
        "checks": [],
        "warnings": [],
        "errors": [],
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name="cephfs-a",
            backend_template={"backend_type": "cephfs"},
            cluster_name="cluster-a",
            storage_class_name="cephfs-sc",
        ),
        actor="admin",
        sanity_result=sanity,
        readiness=sanity["readiness"],
    )


def _rm_worker(
    repository: DmsRepository,
    observability: ObservabilityRepository,
) -> RMWorkerRuntime:
    return RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-worker",
        lease_seconds=2,
    )


def test_list_requests_filters_by_date_range(harness):
    repository: DmsRepository = harness["repository"]
    _register_ready_storage_mapping(repository)
    rid_old = _create_filesystem_request(repository, "fs-old")
    rid_mid = _create_filesystem_request(repository, "fs-mid")
    rid_new = _create_filesystem_request(repository, "fs-new")
    _stamp_requested_at(repository, rid_old, "2025-04-30T23:59:59+00:00")
    _stamp_requested_at(repository, rid_mid, "2025-05-15T12:00:00+00:00")
    _stamp_requested_at(repository, rid_new, "2025-06-01T00:00:00+00:00")

    client: TestClient = harness["client"]

    full = client.get(
        "/api/v1/operations/requests",
        params={"requester_id": "user-1"},
        headers=AUTH_HEADERS,
    ).json()
    assert {r["request_id"] for r in full} == {rid_old, rid_mid, rid_new}

    # Date-only `until` widens to next-day 00:00 so the boundary day is inclusive.
    ranged = client.get(
        "/api/v1/operations/requests",
        params={"requester_id": "user-1", "since": "2025-05-01", "until": "2025-05-31"},
        headers=AUTH_HEADERS,
    ).json()
    assert [r["request_id"] for r in ranged] == [rid_mid]

    iso_filtered = client.get(
        "/api/v1/operations/requests",
        params={
            "requester_id": "user-1",
            "since": "2025-05-15T00:00:00Z",
            "until": "2025-05-15T13:00:00Z",
        },
        headers=AUTH_HEADERS,
    ).json()
    assert [r["request_id"] for r in iso_filtered] == [rid_mid]

    bad = client.get(
        "/api/v1/operations/requests",
        params={"requester_id": "user-1", "since": "garbage"},
        headers=AUTH_HEADERS,
    )
    assert bad.status_code == 422


def _stamp_requested_at(
    repository: DmsRepository, request_id: str, requested_at: str
) -> None:
    with repository.database.connect() as connection:
        connection.execute(
            "UPDATE requests SET requested_at = ? WHERE request_id = ?",
            (requested_at, request_id),
        )


def _expire_runs(repository: DmsRepository, *run_ids: str) -> None:
    placeholders = ",".join(["?"] * len(run_ids))
    with repository.database.connect() as connection:
        connection.execute(
            f"""
            UPDATE runs
            SET lease_expires_at = '2000-01-01T00:00:00+00:00'
            WHERE run_id IN ({placeholders})
            """,
            tuple(run_ids),
        )


# ---- operator abandon of STUCK requests (resolve_stuck_request + :resolve endpoint) ----
# A stuck request (StaleClaim/RecoveryNeeded/…) is a dead-end (workers only re-claim
# Planned plans) and blocks every new request for its resource with Conflict. abandon
# terminalizes it (request + plan + run) so the resource unblocks.

_RESOLVE = "/api/v1/resource-management/requests/{}:resolve"


def test_resolve_stuck_request_recovery_needed_terminalizes_request_plan_run(harness):
    repository: DmsRepository = harness["repository"]
    _register_ready_storage_mapping(repository)
    req, plan, run = _stuck_run(repository, "ab-recov", LifecycleState.RECOVERY_NEEDED)

    repository.resolve_stuck_request(
        req, terminal_status=LifecycleState.FAILED,
        reason="op: stuck", actor="op", backend_unverified=True,
    )

    assert repository.get_request(req)["status"] == LifecycleState.FAILED.value
    assert repository.get_plan(plan)["status"] == LifecycleState.FAILED.value
    runs = {r["run_id"]: r for r in repository.list_runs(limit=50)}
    assert runs[run]["state"] == LifecycleState.FAILED.value
    result = repository.get_results(req)[-1]
    assert result["terminal_status"] == LifecycleState.FAILED.value
    assert result["verification_summary"]["manual_resolution"] is True
    # RecoveryNeeded abandon may have a partial, unverified side effect
    assert result["verification_summary"]["backend_state_verified"] is False


def test_resolve_stuck_request_stale_claim_to_cancelled_verified(harness):
    repository: DmsRepository = harness["repository"]
    _register_ready_storage_mapping(repository)
    req, plan, run = _stuck_run(repository, "ab-stale", LifecycleState.STALE_CLAIM)

    # StaleClaim never ran a side effect -> Cancelled, backend verified (nothing applied)
    repository.resolve_stuck_request(
        req, terminal_status=LifecycleState.CANCELLED,
        reason="op: stale", actor="op", backend_unverified=False,
    )

    assert repository.get_request(req)["status"] == LifecycleState.CANCELLED.value
    assert repository.get_plan(plan)["status"] == LifecycleState.CANCELLED.value
    runs = {r["run_id"]: r for r in repository.list_runs(limit=50)}
    assert runs[run]["state"] == LifecycleState.CANCELLED.value
    assert repository.get_results(req)[-1]["verification_summary"]["backend_state_verified"] is True


def test_resolve_endpoint_abandon_unblocks_conflict(harness):
    """The whole point: abandoning a stuck request lets a NEW request for the same
    resource plan again instead of Conflicting."""
    client: TestClient = harness["client"]
    repository: DmsRepository = harness["repository"]
    _register_ready_storage_mapping(repository)

    # A: stuck RecoveryNeeded request for a resource.
    req_a, _, _ = _stuck_run(repository, "conflictdir", LifecycleState.RECOVERY_NEEDED)
    # B: a new request for the SAME resource -> planner Conflicts it (prior active A).
    req_b = _create_filesystem_request(repository, "conflictdir")
    Planner(repository).run_once(limit=10)
    assert repository.get_request(req_b)["status"] == LifecycleState.CONFLICT.value

    # operator abandons A via the endpoint.
    resp = client.post(
        _RESOLVE.format(req_a),
        json={"resolution": "abandon", "reason": "stuck — abandoning"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["resolved_to"] == LifecycleState.FAILED.value
    assert resp.json()["backend_state_verified"] is False
    assert repository.get_request(req_a)["status"] == LifecycleState.FAILED.value

    # C: a fresh request for the same resource now plans (no active prior) — UNBLOCKED.
    req_c = _create_filesystem_request(repository, "conflictdir")
    Planner(repository).run_once(limit=10)
    assert repository.get_request(req_c)["status"] != LifecycleState.CONFLICT.value


def test_resolve_endpoint_rejects_active_state_and_missing_reason(harness):
    client: TestClient = harness["client"]
    repository: DmsRepository = harness["repository"]
    _register_ready_storage_mapping(repository)

    # a Running request is actively leased, NOT stuck -> 409 (not resolvable).
    req_run, _, _ = _stuck_run(repository, "active", LifecycleState.RUNNING)
    r = client.post(
        _RESOLVE.format(req_run),
        json={"resolution": "abandon", "reason": "x"}, headers=AUTH_HEADERS,
    )
    assert r.status_code == 409

    # reason is required even for a valid stuck state -> 422.
    req_stuck, _, _ = _stuck_run(repository, "needreason", LifecycleState.RECOVERY_NEEDED)
    r2 = client.post(
        _RESOLVE.format(req_stuck),
        json={"resolution": "abandon", "reason": "  "}, headers=AUTH_HEADERS,
    )
    assert r2.status_code == 422

    # a non-existent request -> 404 (not a 500 from the KeyError getter).
    r3 = client.post(
        _RESOLVE.format("req_doesnotexist000000"),
        json={"resolution": "abandon", "reason": "x"}, headers=AUTH_HEADERS,
    )
    assert r3.status_code == 404


def test_resolve_endpoint_verification_failed_abandon_to_failed(harness):
    """VerificationFailed surfaces in 조치 필요 (ACTION_REQUIRED_REQUEST_STATUSES) and
    must be abandonable too — its side effect applied but verification failed, so the
    backend state is unverified."""
    client: TestClient = harness["client"]
    repository: DmsRepository = harness["repository"]
    _register_ready_storage_mapping(repository)
    req, _, _ = _stuck_run(repository, "vf", LifecycleState.VERIFICATION_FAILED)
    resp = client.post(
        _RESOLVE.format(req),
        json={"resolution": "abandon", "reason": "verify failed — abandoning"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["resolved_to"] == LifecycleState.FAILED.value
    assert resp.json()["backend_state_verified"] is False
    assert repository.get_request(req)["status"] == LifecycleState.FAILED.value
