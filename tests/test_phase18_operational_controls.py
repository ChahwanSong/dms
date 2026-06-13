from __future__ import annotations

import time
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
from dms.domain import LifecycleState, OperationKind, ResourceKind, StorageMappingInput
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
