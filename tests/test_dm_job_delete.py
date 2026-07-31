"""DELETE /api/v1/data-management/jobs/{job_id}: prune TERMINAL data_job records.

Terminal jobs accumulate forever in action_required(); this endpoint lets an operator
remove a finished job. Deletion is allowed ONLY in a terminal state (in-flight -> 409,
missing -> 404), removes the row from list_data_jobs()/action_required(), records an
audit control-mutation, and leaves the parent request history intact (no FK cascade).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository, RecordNotFound


HEADERS = {"x-dms-actor": "api-client"}


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
    _register_ready_storage_mapping(repository)
    return {
        "client": TestClient(app),
        "repository": repository,
        "observability": observability,
    }


def _register_ready_storage_mapping(repository: DmsRepository) -> None:
    sanity = {
        "storage_name": "cephfs-a",
        "status": "Ready",
        "agent_observed": {
            "dm_candidates": [{"cluster_name": "cluster-a", "node_name": "dm-1"}],
        },
        "readiness": {"data_management": "Ready", "inventory": "Ready"},
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


def _create_planned_scan_job(harness) -> str:
    """POST a scan and plan it -> returns the job_id of a freshly-created (Pending) job."""
    response = harness["client"].post(
        "/api/v1/data-management/scan",
        json={
            "requester_id": "portal:alice",
            "target": {"storage_name": "cephfs-a", "path": "project/input"},
            "options": {"summary_only": True},
        },
        headers=HEADERS,
    )
    assert response.status_code == 202
    assert Planner(harness["repository"]).run_once() == 1
    job = harness["repository"].get_data_job_by_request(response.json()["request_id"])
    assert job["state"] == DataJobState.PENDING.value
    return job["job_id"]


def test_delete_terminal_job_removes_it_everywhere(harness):
    repo = harness["repository"]
    job_id = _create_planned_scan_job(harness)
    # Drive it to a terminal failure -- this is exactly what accumulates in action_required.
    repo.update_data_job(job_id, state=DataJobState.FAILED)

    before = harness["client"].get(
        "/api/v1/operations/action-required", headers=HEADERS
    )
    assert any(issue.get("job_id") == job_id for issue in before.json())

    response = harness["client"].delete(
        f"/api/v1/data-management/jobs/{job_id}", headers=HEADERS
    )
    assert response.status_code == 200
    assert response.json() == {"job_id": job_id, "status": "deleted"}

    # Row is gone from the store and from list_data_jobs(). Addressing it by id now
    # raises RecordNotFound (which the API maps to 404) rather than returning an empty
    # dict that callers would go on to subscript.
    with pytest.raises(RecordNotFound):
        repo.get_data_job(job_id)
    assert all(job["job_id"] != job_id for job in repo.list_data_jobs())

    # ...and therefore no longer reported by action_required().
    after = harness["client"].get(
        "/api/v1/operations/action-required", headers=HEADERS
    )
    assert all(issue.get("job_id") != job_id for issue in after.json())

    # Deletion is audited as a control mutation (actor = authenticated actor).
    mutation = next(
        m
        for m in repo.list_control_mutations()
        if m["mutation_kind"] == "data_job.delete" and m["target_key"] == job_id
    )
    assert mutation["actor"] == "api-client"
    assert mutation["before_state"]["state"] == DataJobState.FAILED.value


def test_delete_terminal_job_preserves_parent_request(harness):
    """Deleting a data_job must not cascade into the request/plan history."""
    repo = harness["repository"]
    job_id = _create_planned_scan_job(harness)
    request_id = repo.get_data_job(job_id)["request_id"]
    repo.update_data_job(job_id, state=DataJobState.SUCCEEDED)

    response = harness["client"].delete(
        f"/api/v1/data-management/jobs/{job_id}", headers=HEADERS
    )
    assert response.status_code == 200
    # The parent request row is intact (no FK cascade).
    assert repo.get_request(request_id)["request_id"] == request_id


def test_delete_in_flight_job_conflicts(harness):
    repo = harness["repository"]
    job_id = _create_planned_scan_job(harness)  # Pending == in-flight

    response = harness["client"].delete(
        f"/api/v1/data-management/jobs/{job_id}", headers=HEADERS
    )
    assert response.status_code == 409
    assert "terminal" in response.json()["detail"]
    # The job is untouched.
    assert repo.get_data_job(job_id)["job_id"] == job_id


def test_delete_missing_job_returns_404(harness):
    response = harness["client"].delete(
        "/api/v1/data-management/jobs/job_does_not_exist", headers=HEADERS
    )
    assert response.status_code == 404
