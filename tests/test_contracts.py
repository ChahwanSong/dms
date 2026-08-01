from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from dms.adapters import (
    IdentityLookupResult,
    StubIdentityLookupAdapter,
    StubVolcanoAdapter,
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
    reject_unsafe_relative_path,
)
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import DMWorkerRuntime


@pytest.fixture()
def harness(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
        worker_lease_seconds=300,
        dm_kubernetes_mode="stub",
        dm_policy_default_worker_nodes=1,
        dm_policy_default_processes_per_node=1,
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    observability = ObservabilityRepository(observability_db)
    app = create_app(settings, repository, observability)
    return {
        "settings": settings,
        "repository": repository,
        "observability": observability,
        "client": TestClient(app),
        "operational_path": tmp_path / "operational.db",
        "observability_path": tmp_path / "observability.db",
    }


def scan_body(target_name: str = "alpha") -> dict:
    return {
        "requester_id": "user-1",
        "storage_name": "weka-a",
        "target_path": f"project/{target_name}",
    }


def ready_storage_sanity(storage_name: str = "weka-a") -> dict:
    return {
        "storage_name": storage_name,
        "status": "Ready",
        "checked_at": "2026-05-27T00:00:00+00:00",
        "kubernetes_observed": {
            "cluster_name": "cluster-a",
            "storage_class_name": "weka-sc",
            "storage_class_exists": True,
            "provisioner": "weka.csi.dms.test",
        },
        "agent_observed": {
            "fresh_reports": 2,
            "stale_reports": 0,
            "dm_readiness": "Ready",
            "dm_candidates": [{"cluster_name": "cluster-a", "node_name": "dm-1"}],
        },
        "readiness": {
            "data_management": "Ready",
            "inventory": "Ready",
        },
        "checks": [],
        "warnings": [],
        "errors": [],
    }


def register_ready_storage_mapping(repository: DmsRepository) -> None:
    sanity = ready_storage_sanity()
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name="weka-a",
            backend_template={"backend_type": "wekafs"},
            cluster_name="cluster-a",
            storage_class_name="weka-sc",
        ),
        actor="admin",
        sanity_result=sanity,
        readiness=sanity["readiness"],
    )


def ingest_ready_dm_report(repository: DmsRepository) -> None:
    repository.ingest_agent_report(
        {
            "schema_version": "phase19.v1",
            # Freshness is computed ON READ against the staleness window, so a
            # "ready" node must have reported recently — use now, not a fixed past.
            "reported_at": datetime.now(timezone.utc).isoformat(),
            "cluster_name": "cluster-a",
            "node_name": "dm-1",
            "node_uid": "uid-dm-1",
            "worker_role": "DM",
            "mounts": [
                {
                    "storage_name": "weka-a",
                    "mount_path": "/mnt/dms/weka-a",
                    "status": "Ready",
                    "readable": True,
                }
            ],
            "tools": [{"name": "dscan", "status": "Ready"}],
            "credentials": [{"name": "kubernetes-service-account", "status": "Ready"}],
            "networks": [{"name": "storage-net", "status": "Ready"}],
            "identity_evidence": {
                "source": "contract-test",
                "users": [
                    {
                        "username": "user-1",
                        "status": "Ready",
                        "uid": 10000,
                        "gid": 10000,
                        "groups": ["dms-users"],
                    }
                ],
            },
            "csi": [],
        }
    )


def identity_lookup() -> StubIdentityLookupAdapter:
    return StubIdentityLookupAdapter(
        mappings={
            ("ldap", "user-1"): IdentityLookupResult(
                provider="ldap",
                posix_username="user-1",
                uid=10000,
                primary_gid=10000,
                groups=["dms-users"],
                user_dn="uid=user-1,ou=people,dc=example,dc=internal",
                source_metadata={"adapter": "contract-test"},
            )
        }
    )


def submit_scan(client: TestClient, target_name: str = "alpha"):
    return client.post(
        "/api/v1/data-management/scan",
        json=scan_body(target_name),
        headers={"x-dms-actor": "api-client"},
    )


def test_authentication_failure_only_writes_observability_event(harness):
    response = harness["client"].post(
        "/api/v1/data-management/scan",
        json=scan_body(),
    )

    assert response.status_code == 401
    assert harness["repository"].list_requests(requester_id="user-1") == []
    events = harness["observability"].list_events()
    assert events[0]["event_type"] == "authentication_rejected"


def test_authorization_failure_is_operational_terminal_without_plan(harness):
    response = harness["client"].post(
        "/api/v1/data-management/scan",
        json=scan_body(),
        headers={"x-dms-actor": "blocked"},
    )

    assert response.status_code == 403
    [request] = harness["repository"].list_requests(requester_id="user-1")
    assert request["status"] == LifecycleState.AUTHORIZATION_FAILED.value
    assert harness["repository"].get_plan_by_request(request["request_id"]) is None
    [result] = harness["repository"].get_results(request["request_id"])
    assert result["terminal_status"] == LifecycleState.AUTHORIZATION_FAILED.value
    assert result["verification_summary"]["backend_side_effect"] is False


def test_operations_requests_default_returns_latest_1000(harness):
    for index in range(1001):
        harness["repository"].create_request(
            requester_id="user-1",
            actor="api-client",
            operation=OperationKind.DATA_SCAN.value,
            resource_kind=ResourceKind.DATA_JOB.value,
            resource_key=f"weka-a:request-{index}",
            payload={"index": index},
        )

    response = harness["client"].get(
        "/api/v1/operations/requests?requester_id=user-1",
        headers={"x-dms-actor": "api-client"},
    )

    assert response.status_code == 200
    requests = response.json()
    assert len(requests) == 1000
    assert requests[0]["resource_key"] == "weka-a:request-1000"
    assert requests[-1]["resource_key"] == "weka-a:request-1"


def test_operations_requests_requires_requester_id(harness):
    response = harness["client"].get(
        "/api/v1/operations/requests",
        headers={"x-dms-actor": "api-client"},
    )

    assert response.status_code == 422


def test_operations_requests_filters_by_requester_and_limit(harness):
    for index in range(3):
        harness["repository"].create_request(
            requester_id="alice",
            actor="api-client",
            operation=OperationKind.DATA_SCAN.value,
            resource_kind=ResourceKind.DATA_JOB.value,
            resource_key=f"weka-a:alice-{index}",
            payload={"index": index},
        )
        harness["repository"].create_request(
            requester_id="bob",
            actor="api-client",
            operation=OperationKind.DATA_SCAN.value,
            resource_kind=ResourceKind.DATA_JOB.value,
            resource_key=f"weka-a:bob-{index}",
            payload={"index": index},
        )

    response = harness["client"].get(
        "/api/v1/operations/requests?requester_id=alice&limit=2",
        headers={"x-dms-actor": "api-client"},
    )

    assert response.status_code == 200
    requests = response.json()
    assert [item["requester_id"] for item in requests] == ["alice", "alice"]
    assert [item["resource_key"] for item in requests] == [
        "weka-a:alice-2",
        "weka-a:alice-1",
    ]


def test_request_activity_lists_all_and_filters(harness):
    repo = harness["repository"]
    # Two distinct (operation, resource_kind) groups so the filter assertions below
    # actually discriminate. data_job is the only kind a live request can carry, so the
    # other group uses a legacy RM-era value -- exactly what an upgraded database still
    # holds. Rows are written straight to the repository, so no planner/worker semantics
    # are implied.
    for index in range(3):
        repo.create_request(
            requester_id="alice", actor="api-client",
            operation=OperationKind.IDENTITY_UPSERT.value,
            resource_kind="filesystem",
            resource_key=f"weka-a:alice-{index}", payload={},
        )
        repo.create_request(
            requester_id="bob", actor="api-client", operation="data.sync",
            resource_kind="data_job", resource_key=f"cephfs:bob-{index}", payload={},
        )
    client = harness["client"]
    hdr = {"x-dms-actor": "api-client"}
    # ALL requests, no requester_id required (unlike /operations/requests)
    all_resp = client.get("/api/v1/operations/request-activity", headers=hdr)
    assert all_resp.status_code == 200
    assert len(all_resp.json()) == 6
    # filter by operation
    op_resp = client.get("/api/v1/operations/request-activity?operation=data.sync", headers=hdr)
    assert [r["operation"] for r in op_resp.json()] == ["data.sync"] * 3
    # filter by resource_kind
    rk_resp = client.get("/api/v1/operations/request-activity?resource_kind=data_job", headers=hdr)
    assert len(rk_resp.json()) == 3 and all(r["resource_kind"] == "data_job" for r in rk_resp.json())
    # requester + limit still work
    lim_resp = client.get("/api/v1/operations/request-activity?requester_id=alice&limit=2", headers=hdr)
    assert len(lim_resp.json()) == 2 and all(r["requester_id"] == "alice" for r in lim_resp.json())

    # --- server-side free-text search: requester OR target, case-insensitive, spans
    #     resource_key + serialized payload, LIKE metachars escaped ---
    repo.create_request(
        requester_id="carol", actor="api-client", operation="data.sync",
        resource_kind="data_job", resource_key="cephfs:carol-9",
        payload={"destination": {"path": "special/deep/target-path"}},
    )
    # match by requester
    s_req = client.get("/api/v1/operations/request-activity?search=alice", headers=hdr)
    assert len(s_req.json()) == 3 and all(r["requester_id"] == "alice" for r in s_req.json())
    # match by target via resource_key, case-insensitive
    s_key = client.get("/api/v1/operations/request-activity?search=BOB-1", headers=hdr)
    assert [r["resource_key"] for r in s_key.json()] == ["cephfs:bob-1"]
    # match by target via serialized payload (destination path) — not just resource_key
    s_pay = client.get("/api/v1/operations/request-activity?search=target-path", headers=hdr)
    assert [r["requester_id"] for r in s_pay.json()] == ["carol"]
    # search + offset paginate the matched set (3 'cephfs:bob' rows → page 2 has 1)
    s_pg = client.get(
        "/api/v1/operations/request-activity?search=cephfs:bob&limit=2&offset=2", headers=hdr
    )
    assert len(s_pg.json()) == 1
    # '_' is escaped → literal, not a wildcard: no resource_key contains 'bob_'
    s_lit = client.get("/api/v1/operations/request-activity?search=bob_", headers=hdr)
    assert s_lit.json() == []


def test_request_and_plan_are_persisted_before_backend_side_effect(harness):
    register_ready_storage_mapping(harness["repository"])
    ingest_ready_dm_report(harness["repository"])
    response = submit_scan(harness["client"])
    request_id = response.json()["request_id"]
    volcano = StubVolcanoAdapter()
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        identity_lookup=identity_lookup(),
        volcano_adapter=volcano,
        worker_id="dm-c1",
    )

    request = harness["repository"].get_request(request_id)
    assert request["status"] == LifecycleState.PERSISTED.value
    assert volcano.calls == []

    assert Planner(harness["repository"], settings=harness["settings"]).run_once() == 1
    assert harness["repository"].get_plan_by_request(request_id)["status"] == "Planned"
    assert volcano.calls == []

    assert worker.run_once() == 1
    job_id = harness["repository"].get_data_job_by_request(request_id)["job_id"]
    assert ("create_job", job_id) in volcano.calls
    assert harness["repository"].get_request(request_id)["status"] == "Succeeded"
    assert (
        harness["repository"].get_data_job_by_request(request_id)["state"]
        == DataJobState.SUCCEEDED.value
    )


def test_planner_rejects_later_same_resource_request_until_prior_terminal(harness):
    register_ready_storage_mapping(harness["repository"])
    first = submit_scan(harness["client"], "same").json()["request_id"]
    second = submit_scan(harness["client"], "same").json()["request_id"]

    planner = Planner(harness["repository"], settings=harness["settings"])
    assert planner.run_once(limit=10) == 2

    assert harness["repository"].get_plan_by_request(first)["status"] == "Planned"
    second_results = harness["repository"].get_results(second)
    assert second_results[0]["terminal_status"] == LifecycleState.CONFLICT.value
    assert second_results[0]["verification_summary"]["backend_side_effect"] is False


def test_expired_worker_claim_becomes_stale_claim(harness):
    register_ready_storage_mapping(harness["repository"])
    request_id = submit_scan(harness["client"], "stale").json()["request_id"]
    Planner(harness["repository"], settings=harness["settings"]).run_once()
    plan = harness["repository"].get_plan_by_request(request_id)
    run_id = harness["repository"].claim_plan(
        plan_id=plan["plan_id"],
        worker_id="dm-c1",
        executor_id="dm-c1",
        lease_seconds=-1,
    )

    assert harness["repository"].mark_stale_runs() == 1
    [run] = harness["repository"].list_runs()
    assert run["run_id"] == run_id
    assert run["state"] == LifecycleState.STALE_CLAIM.value
    assert harness["repository"].get_request(request_id)["status"] == "StaleClaim"


def test_plan_claim_is_atomic_under_competing_workers(harness):
    register_ready_storage_mapping(harness["repository"])
    request_id = submit_scan(harness["client"], "atomic-claim").json()["request_id"]
    Planner(harness["repository"], settings=harness["settings"]).run_once()
    plan = harness["repository"].get_plan_by_request(request_id)
    barrier = threading.Barrier(8)
    successes: list[str] = []
    failures: list[str] = []

    def claim(worker_id: str) -> None:
        barrier.wait()
        try:
            successes.append(
                harness["repository"].claim_plan(
                    plan_id=plan["plan_id"],
                    worker_id=worker_id,
                    executor_id=worker_id,
                    lease_seconds=300,
                )
            )
        except RuntimeError as exc:
            failures.append(str(exc))

    threads = [threading.Thread(target=claim, args=(f"dm-{idx}",)) for idx in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(successes) == 1
    assert len(failures) == 7
    assert len(harness["repository"].list_runs()) == 1
    assert harness["repository"].get_plan(plan["plan_id"])["attempt_count"] == 1


def test_observability_database_can_be_separate_from_operational_database(harness):
    harness["observability"].record_event(
        component="test",
        severity="INFO",
        event_type="separation_check",
        message="ok",
    )

    operational_tables = _sqlite_tables(harness["operational_path"])
    observability_tables = _sqlite_tables(harness["observability_path"])
    assert "requests" in operational_tables
    assert "diagnostic_events" not in operational_tables
    assert "diagnostic_events" in observability_tables
    assert "operational-0001-phase1" in _migration_versions(harness["operational_path"])
    assert "observability-0001-phase1" in _migration_versions(harness["observability_path"])


def test_resource_key_and_storage_mapping_uniqueness(harness):
    # Resource keys embed a storage-relative path; traversal must be rejected before
    # a key is ever built.
    with pytest.raises(ValueError):
        reject_unsafe_relative_path("../escape")

    repository = harness["repository"]
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name="weka-a",
            backend_template={"backend_type": "wekafs"},
            cluster_name="c1",
            storage_class_name="weka-sc",
        ),
        actor="admin",
    )
    with pytest.raises(sqlite3.IntegrityError):
        repository.upsert_storage_mapping(
            StorageMappingInput(
                storage_name="weka-b",
                backend_template={"backend_type": "wekafs"},
                cluster_name="c1",
                storage_class_name="weka-sc",
            ),
            actor="admin",
        )


def test_data_sync_rm_accept_phase20_preview_guarded_requests(harness):
    register_ready_storage_mapping(harness["repository"])
    sync_response = harness["client"].post(
        "/api/v1/data-management/sync",
        json={
            "requester_id": "user-1",
            "storage_name": "weka-a",
            "source_path": "src",
            "destination_path": "dst",
        },
        headers={"x-dms-actor": "api-client"},
    )
    rm_response = harness["client"].post(
        "/api/v1/data-management/rm",
        json={
            "requester_id": "user-1",
            "storage_name": "weka-a",
            "target_path": "target",
            "options": {"recursive": True},
        },
        headers={"x-dms-actor": "api-client"},
    )

    assert sync_response.status_code == 202
    assert sync_response.json()["operation"] == "data.sync"
    assert sync_response.json()["source"] == {"storage_name": "weka-a", "path": "src"}
    assert sync_response.json()["destination"] == {
        "storage_name": "weka-a",
        "path": "dst",
    }
    assert rm_response.status_code == 202
    assert rm_response.json()["operation"] == "data.rm"
    assert rm_response.json()["target"] == {"storage_name": "weka-a", "path": "target"}
    assert len(harness["repository"].list_requests(requester_id="user-1")) == 2


def test_data_management_rejects_raw_options_and_path_traversal(harness):
    rm_response = harness["client"].post(
        "/api/v1/data-management/rm",
        json={
            "requester_id": "user-1",
            "storage_name": "weka-a",
            "target_path": "target",
            "options": {"raw_options": "--danger"},
        },
        headers={"x-dms-actor": "api-client"},
    )
    traversal_response = harness["client"].post(
        "/api/v1/data-management/scan",
        json={
            "requester_id": "user-1",
            "storage_name": "weka-a",
            "target_path": "../target",
        },
        headers={"x-dms-actor": "api-client"},
    )

    assert rm_response.status_code == 422
    assert traversal_response.status_code == 422


def _sqlite_tables(path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def _migration_versions(path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}
