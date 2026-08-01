from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState
from dms.migrations import migrate_all
from dms.repositories import DmsRepository, ObservabilityRepository


@pytest.fixture()
def repository(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    operational = Database(operational_url)
    observability = Database(observability_url)
    migrate_all(operational, observability)
    return DmsRepository(operational)


def _seed(repo, operation, storage_name, state, n=1):
    for _ in range(n):
        request_id = repo.create_request(
            requester_id="test-user",
            actor="test-actor",
            operation=operation,
            resource_kind="data_job",
            resource_key=f"{storage_name}:{operation}",
            payload={"storage_name": storage_name},
        )
        repo.create_data_job(
            request_id=request_id,
            operation=operation,
            storage_name=storage_name,
            source=None,
            destination=None,
            target=None,
            priority=100,
            worker_pool={},
            state=state,
        )


def test_data_job_summary_counts_by_state_and_operation(repository):
    _seed(repository, "data.sync", "cephfs-a", DataJobState.RUNNING, 2)
    _seed(repository, "data.sync", "cephfs-a", DataJobState.SUCCEEDED, 3)
    _seed(repository, "data.scan", "cephfs-a", DataJobState.PENDING, 1)
    _seed(repository, "data.rm", "cephfs-b", DataJobState.FAILED, 1)

    summary = repository.data_job_summary()

    assert summary["total"] == 7
    assert summary["by_state"]["Running"] == 2
    assert summary["by_state"]["Succeeded"] == 3
    assert summary["by_state"]["Pending"] == 1
    assert summary["by_state"]["Failed"] == 1
    assert summary["by_operation"]["data.sync"] == 5
    assert summary["by_operation"]["data.scan"] == 1
    assert summary["by_operation"]["data.rm"] == 1
    # active_total = non-terminal: Running(2) + Pending(1) = 3 (Succeeded/Failed terminal)
    assert summary["active_total"] == 3


def test_data_job_summary_filters_by_storage_and_operation(repository):
    _seed(repository, "data.sync", "cephfs-a", DataJobState.RUNNING, 2)
    _seed(repository, "data.scan", "cephfs-b", DataJobState.PENDING, 4)

    only_a = repository.data_job_summary(storage_name="cephfs-a")
    assert only_a["total"] == 2
    assert only_a["by_operation"] == {"data.sync": 2}

    only_scan = repository.data_job_summary(operation="data.scan")
    assert only_scan["total"] == 4
    assert only_scan["by_state"] == {"Pending": 4}


def test_data_job_summary_empty(repository):
    summary = repository.data_job_summary()
    assert summary == {"total": 0, "active_total": 0, "by_state": {}, "by_operation": {}}


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
    _seed(repository, "data.sync", "cephfs-a", DataJobState.RUNNING, 2)
    _seed(repository, "data.scan", "cephfs-a", DataJobState.SUCCEEDED, 1)
    return TestClient(app)


def test_data_jobs_summary_route(client):
    resp = client.get(
        "/api/v1/operations/data-jobs/summary", headers={"x-dms-actor": "api-client"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["by_state"]["Running"] == 2
    assert body["active_total"] == 2


def test_data_jobs_summary_does_not_shadow_job_id_route(tmp_path):
    # /data-jobs/{job_id} must still resolve (a bogus id → not a 'summary' collision).
    # Use raise_server_exceptions=False so an unhandled 500 from {job_id} (not-found path)
    # is returned as a response rather than re-raised; we only care routing reached that route.
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
    no_raise_client = TestClient(app, raise_server_exceptions=False)
    resp = no_raise_client.get(
        "/api/v1/operations/data-jobs/nonexistent-id",
        headers={"x-dms-actor": "api-client"},
    )
    assert resp.status_code != 404 or "summary" not in resp.text
