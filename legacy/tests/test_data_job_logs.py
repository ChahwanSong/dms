"""Read-only data-job launcher-log tail route.

GET /api/v1/operations/data-jobs/{job_id}/logs resolves the data_jobs row ->
its volcano_job_ref -> the volcano adapter's tail_logs(). It must:
- 404 for an unknown job id,
- return available=False (without calling the adapter) when no job ref is recorded yet,
- forward the adapter's {available, pods, logs, note} payload (plus job_id) when a ref exists,
- prefer the execution-phase ref over preview,
- clamp/validate tail (gt=0, le=5000).

Mirrors the existing operations-route test fixtures (SQLite, create_app, x-dms-actor header).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState
from dms.migrations import migrate_all
from dms.repositories import DmsRepository, ObservabilityRepository


API_HEADERS = {"x-dms-actor": "api-client"}


class _FakeVolcano:
    """Records tail_logs calls and returns a canned launcher-log payload."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def tail_logs(self, job_ref: str, *, tail_lines: int = 400) -> dict[str, Any]:
        self.calls.append((job_ref, tail_lines))
        return {
            "available": True,
            "pods": [
                {
                    "name": "launcher-abc",
                    "node_name": "node-1",
                    "role": "launcher",
                    "phase": "Running",
                }
            ],
            "logs": "line1\nline2\n",
            "note": "launcher pod launcher-abc",
        }


def _seed_job(repo: DmsRepository, *, volcano_job_ref: dict[str, Any] | None) -> str:
    request_id = repo.create_request(
        requester_id="alice",
        actor="api-client",
        operation="data.sync",
        resource_kind="data_job",
        resource_key="cephfs-a:data.sync",
        payload={"storage_name": "cephfs-a"},
    )
    return repo.create_data_job(
        request_id=request_id,
        operation="data.sync",
        storage_name="cephfs-a",
        source="src",
        destination="dst",
        target=None,
        priority=100,
        worker_pool={},
        state=DataJobState.RUNNING,
        volcano_job_ref=volcano_job_ref,
    )


@pytest.fixture()
def env(tmp_path):
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
    fake = _FakeVolcano()
    app.state.services.volcano_adapter = fake
    return app, repository, fake


def test_logs_404_for_unknown_job(env):
    app, _repo, fake = env
    client = TestClient(app)
    resp = client.get(
        "/api/v1/operations/data-jobs/job-missing/logs", headers=API_HEADERS
    )
    assert resp.status_code == 404
    assert fake.calls == []


def test_logs_unavailable_when_no_job_ref(env):
    app, repo, fake = env
    job_id = _seed_job(repo, volcano_job_ref=None)
    client = TestClient(app)
    resp = client.get(
        f"/api/v1/operations/data-jobs/{job_id}/logs", headers=API_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["available"] is False
    assert body["pods"] == []
    assert body["logs"] == ""
    assert "not yet scheduled" in body["note"]
    # The adapter must not be touched when there is nothing scheduled.
    assert fake.calls == []


def test_logs_payload_forwarded_when_ref_present(env):
    app, repo, fake = env
    job_id = _seed_job(
        repo, volcano_job_ref={"execution": {"job_ref": "volcano://dms/run-1"}}
    )
    client = TestClient(app)
    resp = client.get(
        f"/api/v1/operations/data-jobs/{job_id}/logs", headers=API_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["available"] is True
    assert body["logs"] == "line1\nline2\n"
    assert body["pods"][0]["role"] == "launcher"
    assert body["note"] == "launcher pod launcher-abc"
    # Default tail forwarded, and the execution-phase ref was selected.
    assert fake.calls == [("volcano://dms/run-1", 400)]


def test_logs_prefers_execution_ref_over_preview(env):
    app, repo, fake = env
    job_id = _seed_job(
        repo,
        volcano_job_ref={
            "preview": {"job_ref": "volcano://dms/preview-1"},
            "execution": {"job_ref": "volcano://dms/exec-1"},
        },
    )
    client = TestClient(app)
    resp = client.get(
        f"/api/v1/operations/data-jobs/{job_id}/logs?tail=50", headers=API_HEADERS
    )
    assert resp.status_code == 200
    assert fake.calls == [("volcano://dms/exec-1", 50)]


def test_logs_flat_ref_shape(env):
    app, repo, fake = env
    job_id = _seed_job(repo, volcano_job_ref={"job_ref": "mpijob://dms/flat-1"})
    client = TestClient(app)
    resp = client.get(
        f"/api/v1/operations/data-jobs/{job_id}/logs", headers=API_HEADERS
    )
    assert resp.status_code == 200
    assert fake.calls == [("mpijob://dms/flat-1", 400)]


def test_logs_tail_validation(env):
    app, repo, fake = env
    job_id = _seed_job(
        repo, volcano_job_ref={"execution": {"job_ref": "volcano://dms/run-1"}}
    )
    client = TestClient(app)

    # over the cap -> 422, adapter untouched
    assert (
        client.get(
            f"/api/v1/operations/data-jobs/{job_id}/logs?tail=99999",
            headers=API_HEADERS,
        ).status_code
        == 422
    )
    # zero / negative -> 422
    assert (
        client.get(
            f"/api/v1/operations/data-jobs/{job_id}/logs?tail=0", headers=API_HEADERS
        ).status_code
        == 422
    )
    assert fake.calls == []

    # exactly the cap is accepted and forwarded
    resp = client.get(
        f"/api/v1/operations/data-jobs/{job_id}/logs?tail=5000", headers=API_HEADERS
    )
    assert resp.status_code == 200
    assert fake.calls == [("volcano://dms/run-1", 5000)]


def test_stub_adapter_tail_logs_unavailable():
    from dms.adapters.volcano import StubVolcanoAdapter

    result = StubVolcanoAdapter().tail_logs("volcano://dms/whatever", tail_lines=10)
    assert result["available"] is False
    assert result["pods"] == []
    assert result["logs"] == ""
    assert isinstance(result["note"], str) and result["note"]
