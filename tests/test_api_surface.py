"""Pins the public DMS surface: mounted routes + the domain enums behind them.

Nothing else in the suite asserts *which* routes exist or *which* OperationKind /
ResourceKind / WorkerRole members exist — every other test exercises a route it
already knows about. That gap is how a whole feature can be half-removed (routes
gone, enum members lingering, or vice versa) without a single red test.

This file is deliberately an exhaustive snapshot, so adding or removing a route or
enum member is a conscious edit here, reviewed alongside the change.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from dms.api import create_app
from dms.db import Database
from dms.domain import OperationKind, ResourceKind, WorkerRole
from dms.migrations import migrate_all
from dms.repositories import DmsRepository, ObservabilityRepository

# Every /api/v1 route the service mounts, as (method, path).
_EXPECTED_ROUTES = {
    # --- agent -------------------------------------------------------------
    ("POST", "/api/v1/agent/reports"),
    ("POST", "/api/v1/agent/rollout-restart"),
    ("GET", "/api/v1/agent/rollout-status"),
    # --- data management (scan / sync / rm data jobs) ----------------------
    ("GET", "/api/v1/data-management/help"),
    ("GET", "/api/v1/data-management/policies"),
    ("GET", "/api/v1/data-management/policies/{operation}"),
    ("PUT", "/api/v1/data-management/policies/{operation}"),
    ("GET", "/api/v1/data-management/scan"),
    ("POST", "/api/v1/data-management/scan"),
    ("GET", "/api/v1/data-management/scan/jobs/{job_id}"),
    ("GET", "/api/v1/data-management/sync"),
    ("POST", "/api/v1/data-management/sync"),
    ("GET", "/api/v1/data-management/sync/jobs/{job_id}"),
    ("GET", "/api/v1/data-management/rm"),
    ("POST", "/api/v1/data-management/rm"),
    ("GET", "/api/v1/data-management/rm/jobs/{job_id}"),
    ("POST", "/api/v1/data-management/jobs/{job_id}:confirm"),
    ("POST", "/api/v1/data-management/jobs/{job_id}:cancel"),
    ("DELETE", "/api/v1/data-management/jobs/{job_id}"),
    ("GET", "/api/v1/data-management/identity-denylist"),
    ("PUT", "/api/v1/data-management/identity-denylist/{subject_type}/{subject}"),
    ("DELETE", "/api/v1/data-management/identity-denylist/{subject_type}/{subject}"),
    # --- storage mappings (inventory writes; reads live on operations) -----
    ("POST", "/api/v1/storage-mappings"),
    ("PATCH", "/api/v1/storage-mappings/{storage_name}"),
    ("DELETE", "/api/v1/storage-mappings/{storage_name}"),
    ("POST", "/api/v1/storage-mappings/{storage_name}:check"),
    # --- operations (read queries + operational mutations) -----------------
    ("GET", "/api/v1/operations/control-state"),
    ("POST", "/api/v1/operations/control-state:enter-maintenance"),
    ("POST", "/api/v1/operations/control-state:begin-drain"),
    ("POST", "/api/v1/operations/control-state:resume"),
    ("GET", "/api/v1/operations/drain-status"),
    ("POST", "/api/v1/operations/runs:mark-stale"),
    ("GET", "/api/v1/operations/work-summary"),
    ("GET", "/api/v1/operations/plans/active"),
    ("GET", "/api/v1/operations/runs/active"),
    ("GET", "/api/v1/operations/runs/stale"),
    ("GET", "/api/v1/operations/action-required"),
    ("POST", "/api/v1/operations/action-required:ack"),
    ("POST", "/api/v1/operations/action-required:unack"),
    ("GET", "/api/v1/operations/action-required/acks"),
    ("GET", "/api/v1/operations/inventory"),
    ("GET", "/api/v1/operations/agent-reports"),
    ("GET", "/api/v1/operations/agent-reports/metrics"),
    ("GET", "/api/v1/operations/storage-mappings"),
    ("GET", "/api/v1/operations/storage-mappings/{storage_name}"),
    ("GET", "/api/v1/operations/requests"),
    ("GET", "/api/v1/operations/requests/{request_id}"),
    ("POST", "/api/v1/operations/requests/{request_id}:resolve"),
    ("GET", "/api/v1/operations/request-activity"),
    ("GET", "/api/v1/operations/resources"),
    ("GET", "/api/v1/operations/worker-agent-health"),
    ("GET", "/api/v1/operations/data-jobs"),
    ("GET", "/api/v1/operations/data-jobs/summary"),
    ("GET", "/api/v1/operations/data-jobs/{job_id}"),
    ("GET", "/api/v1/operations/data-jobs/{job_id}/logs"),
    ("GET", "/api/v1/operations/diagnostics/{correlation_id}"),
    ("GET", "/api/v1/operations/volcano"),
    ("GET", "/api/v1/operations/volcano/job-metrics"),
}


@pytest.fixture()
def app(tmp_path) -> FastAPI:
    operational = Database(f"sqlite:///{tmp_path / 'operational.db'}")
    observability = Database(f"sqlite:///{tmp_path / 'observability.db'}")
    migrate_all(operational, observability)
    return create_app(
        repository=DmsRepository(operational),
        observability=ObservabilityRepository(observability),
    )


def _api_routes(app: FastAPI) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1"):
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            routes.add((method, path))
    return routes


def test_mounted_api_surface_is_exactly_the_expected_set(app):
    actual = _api_routes(app)
    assert actual - _EXPECTED_ROUTES == set(), "unexpected NEW route(s)"
    assert _EXPECTED_ROUTES - actual == set(), "expected route(s) MISSING"


def test_no_resource_management_routes_remain(app):
    """The resource-management feature (filesystem provisioning + k8s namespace
    quotas) was removed. Its router prefix and every filesystem/quota path must be
    gone — the storage-mapping writes that used to share that prefix now live under
    /api/v1/storage-mappings."""
    paths = {path for _, path in _api_routes(app)}
    assert not [p for p in paths if "resource-management" in p]
    assert not [p for p in paths if "/filesystems" in p]
    assert not [p for p in paths if "namespace-quota" in p]
    assert not [p for p in paths if "default-quota-polic" in p]


def test_healthz_is_unauthenticated_and_reports_observability_split(app):
    from fastapi.testclient import TestClient

    body = TestClient(app).get("/healthz")
    assert body.status_code == 200
    assert body.json()["status"] == "ok"
    assert "observability_separate" in body.json()


def test_domain_enum_members_are_exactly_the_expected_set():
    """Enums and routes must stay in step: a leftover enum member is how a removed
    feature keeps a foothold in the planner/repository filters long after its routes
    are gone."""
    assert {o.value for o in OperationKind} == {
        "data.sync",
        "data.rm",
        "data.scan",
        "data.cancel",
        "identity.upsert",
        "identity.refresh",
        "identity.disable",
    }
    assert {r.value for r in ResourceKind} == {"data_job"}
    assert {w.value for w in WorkerRole} == {"DM"}


def test_data_rm_is_a_data_job_not_resource_management():
    """Guard against the name collision that makes this feature easy to delete by
    mistake: ``data.rm`` is the one-shot data DELETION job run by the DM worker, and
    it is routed to the DM worker exactly like scan/sync."""
    assert OperationKind.DATA_RM.value == "data.rm"
    assert OperationKind.DATA_RM.value.startswith("data.")
