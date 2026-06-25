from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dms.adapters.volcano import KubernetesVolcanoAdapter, StubVolcanoAdapter
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.migrations import migrate_all
from dms.repositories import DmsRepository, ObservabilityRepository


# ---------------------------------------------------------------------------
# Canned kubectl -o json payloads (items lists only)
# ---------------------------------------------------------------------------

CANNED_QUEUES = [
    {
        "metadata": {"name": "dms-data"},
        "status": {"state": "Open", "running": 2, "pending": 1, "inqueue": 3},
    },
    {
        "metadata": {"name": "default"},
        "status": {"state": "Open", "running": 0, "pending": 0, "inqueue": 0},
    },
]

CANNED_JOBS = [
    {
        "metadata": {"name": "dms-scan-abc", "namespace": "dms"},
        "spec": {"queue": "dms-data", "minAvailable": 2},
        "status": {
            "state": {"phase": "Running"},
            "running": 2,
            "pending": 0,
            "succeeded": 0,
            "failed": 0,
        },
    }
]

CANNED_PODS = [
    {
        "metadata": {"name": "volcano-scheduler-xyz"},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {"ready": True, "restartCount": 0},
                {"ready": True, "restartCount": 1},
            ],
        },
    },
    {
        "metadata": {"name": "volcano-controllers-abc"},
        "status": {
            "phase": "Running",
            "containerStatuses": [{"ready": False, "restartCount": 3}],
        },
    },
]


def _fake_kubectl_get_items(args: list[str], timeout: float = 10.0) -> list[dict]:
    """Dispatch by resource kind (second positional arg)."""
    resource = args[1] if len(args) > 1 else ""
    if resource == "queues.scheduling.volcano.sh":
        return CANNED_QUEUES
    if resource == "vcjob":
        return CANNED_JOBS
    if resource == "pods":
        return CANNED_PODS
    return []


# ---------------------------------------------------------------------------
# 1. Aggregation — full happy path
# ---------------------------------------------------------------------------

def test_kubernetes_adapter_aggregation(monkeypatch):
    settings = Settings(
        database_url="sqlite:///:memory:",
        observability_database_url="sqlite:///:memory:",
    )
    adapter = KubernetesVolcanoAdapter.from_settings(settings)
    monkeypatch.setattr(adapter, "_kubectl_get_items", _fake_kubectl_get_items)

    result = adapter.volcano_status()

    # queues
    assert len(result["queues"]) == 2
    dms_data = next(q for q in result["queues"] if q["name"] == "dms-data")
    assert dms_data["state"] == "Open"
    assert dms_data["running"] == 2
    assert dms_data["pending"] == 1
    assert dms_data["inqueue"] == 3

    # jobs
    assert len(result["jobs"]) == 1
    job = result["jobs"][0]
    assert job["name"] == "dms-scan-abc"
    assert job["namespace"] == "dms"
    assert job["queue"] == "dms-data"
    assert job["phase"] == "Running"
    assert job["running"] == 2
    assert job["min_available"] == 2

    # scheduler pods
    assert len(result["scheduler"]) == 2
    sched = next(p for p in result["scheduler"] if "scheduler" in p["name"])
    assert sched["phase"] == "Running"
    assert sched["ready"] is True
    assert sched["restarts"] == 1

    ctrl = next(p for p in result["scheduler"] if "controllers" in p["name"])
    assert ctrl["ready"] is False
    assert ctrl["restarts"] == 3

    # no errors
    assert result["errors"] == {"queues": None, "jobs": None, "scheduler": None}


# ---------------------------------------------------------------------------
# 2. Section fail-soft — jobs raises, queues/scheduler still populated
# ---------------------------------------------------------------------------

def test_kubernetes_adapter_section_failsoft(monkeypatch):
    settings = Settings(
        database_url="sqlite:///:memory:",
        observability_database_url="sqlite:///:memory:",
    )
    adapter = KubernetesVolcanoAdapter.from_settings(settings)

    def failing_get_items(args: list[str], timeout: float = 10.0) -> list[dict]:
        resource = args[1] if len(args) > 1 else ""
        if resource == "vcjob":
            raise RuntimeError("Forbidden: cannot list vcjob")
        return _fake_kubectl_get_items(args, timeout)

    monkeypatch.setattr(adapter, "_kubectl_get_items", failing_get_items)

    result = adapter.volcano_status()

    # jobs section failed
    assert result["jobs"] == []
    assert "Forbidden" in result["errors"]["jobs"]

    # queues and scheduler still populated
    assert len(result["queues"]) == 2
    assert len(result["scheduler"]) == 2
    assert result["errors"]["queues"] is None
    assert result["errors"]["scheduler"] is None


# ---------------------------------------------------------------------------
# 3. Stub adapter returns the expected empty structure
# ---------------------------------------------------------------------------

def test_stub_adapter_returns_empty_structure():
    adapter = StubVolcanoAdapter()
    result = adapter.volcano_status()
    assert result == {
        "queues": [],
        "jobs": [],
        "scheduler": [],
        "errors": {"queues": None, "jobs": None, "scheduler": None},
    }


# ---------------------------------------------------------------------------
# 4. Route test via TestClient (stub adapter via dm_kubernetes_mode="stub")
# ---------------------------------------------------------------------------

HEADERS = {"x-dms-actor": "api-client"}


@pytest.fixture()
def client(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
        dm_kubernetes_mode="stub",
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    observability = ObservabilityRepository(observability_db)
    app = create_app(settings, repository, observability)
    return TestClient(app)


def test_route_volcano_status_200(client):
    response = client.get("/api/v1/operations/volcano", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) >= {"queues", "jobs", "scheduler", "errors"}
    assert isinstance(body["queues"], list)
    assert isinstance(body["jobs"], list)
    assert isinstance(body["scheduler"], list)
    assert isinstance(body["errors"], dict)
    assert set(body["errors"].keys()) == {"queues", "jobs", "scheduler"}
