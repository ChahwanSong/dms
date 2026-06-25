from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dms.adapters import StaticKubernetesReadOnlyInventoryAdapter
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import LifecycleState
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository


CEPHFS_DRIVER = "rook-ceph.cephfs.csi.ceph.com"
GPFS_DRIVER = "spectrumscale.csi.ibm.com"
LONGHORN_DRIVER = "driver.longhorn.io"
API_HEADERS = {"x-dms-actor": "api-client"}


def test_agent_inventory_and_storage_mapping_sanity(harness):
    client: TestClient = harness["client"]
    submit_core_reports(client)

    mismatch = client.post(
        "/api/v1/agent/reports",
        json=agent_report(
            cluster_name="cluster-a",
            node_name="c1-worker",
            node_uid="uid-a",
            worker_role="RM",
            mounts=[mount("cephfs-a")],
            csi=[csi(CEPHFS_DRIVER, "testbed-cephfs")],
        ),
        headers={"x-dms-actor": "node:cluster-a:not-c1-worker"},
    )
    assert mismatch.status_code == 403
    assert any(
        event["event_type"] == "agent_node_identity_mismatch"
        for event in harness["observability"].list_events()
    )
    reports = client.get("/api/v1/operations/agent-reports", headers=API_HEADERS).json()
    assert all(
        report["node_name"] != "c1-worker"
        or report["worker_role"] != "RM"
        for report in reports
    )

    inventory = client.get("/api/v1/operations/inventory", headers=API_HEADERS).json()
    assert "testbed-cephfs" in {
        item["name"] for item in inventory["clusters"]["cluster-a"]["storage_classes"]
    }
    assert "cephfs-a" in inventory["worker_roles"]["RM"]["cluster-a"][
        "mounts_by_storage_name"
    ]
    assert "longhorn-b" in inventory["worker_roles"]["DM"]["cluster-a"][
        "mounts_by_storage_name"
    ]

    ready = upsert_mapping(
        client,
        storage_name="cephfs-a",
        backend_type="cephfs",
        cluster_name="cluster-a",
        storage_class_name="testbed-cephfs",
        backend_template={
            "backend_type": "cephfs",
            "mount_path": "/this/path/is/not-observed-by-api-pod",
            "managed_root": "/this/path/is/not-observed-by-api-pod/dms",
        },
    )
    assert ready["status"] == "Ready"
    sanity = ready["mapping"]["sanity_result"]
    assert sanity["readiness"]["resource_management"] == "Ready"
    assert sanity["readiness"]["data_management"] == "Ready"
    assert "/this/path/is/not-observed-by-api-pod" not in str(sanity)

    longhorn = upsert_mapping(
        client,
        storage_name="longhorn-b",
        backend_type="longhorn",
        cluster_name="cluster-b",
        storage_class_name="testbed-longhorn",
    )
    assert longhorn["status"] == "Ready"

    missing = upsert_mapping(
        client,
        storage_name="missing-longhorn",
        backend_type="longhorn",
        cluster_name="cluster-b",
        storage_class_name="does-not-exist",
    )
    assert missing["status"] == "Failed"
    assert issue_codes(missing["mapping"]["sanity_result"]["errors"]) == {
        "storage_class_missing"
    }

    mismatch = upsert_mapping(
        client,
        storage_name="longhorn-b",
        backend_type="cephfs",
        cluster_name="cluster-b",
        storage_class_name="testbed-longhorn",
    )
    assert mismatch["status"] == "Failed"
    assert "csi_driver_mismatch" in issue_codes(
        mismatch["mapping"]["sanity_result"]["errors"]
    )

    action_required = client.get(
        "/api/v1/operations/action-required", headers=API_HEADERS
    ).json()
    assert {
        issue["issue_type"] for issue in action_required
    } >= {"storage_mapping_failed", "storage_class_missing", "csi_driver_mismatch"}


def test_stale_reports_are_excluded_from_effective_inventory(harness):
    client: TestClient = harness["client"]
    old_report = agent_report(
        cluster_name="cluster-a",
        node_name="old-worker",
        node_uid="uid-old",
        worker_role="DM",
        mounts=[mount("old-storage")],
        csi=[csi(CEPHFS_DRIVER, "testbed-cephfs")],
        reported_at="2000-01-01T00:00:00+00:00",
    )
    response = client.post(
        "/api/v1/agent/reports",
        json=old_report,
        headers={"x-dms-actor": "node:cluster-a:old-worker"},
    )
    assert response.status_code == 200

    inventory = client.get("/api/v1/operations/inventory", headers=API_HEADERS).json()
    assert inventory["clusters"]["cluster-a"]["agent_reports"]["fresh"] == []
    assert len(inventory["clusters"]["cluster-a"]["agent_reports"]["stale"]) == 1
    assert (
        "old-storage"
        not in inventory["worker_roles"]["DM"]
        .get("cluster-a", {})
        .get("mounts_by_storage_name", {})
    )

    stale_reports = client.get(
        "/api/v1/operations/agent-reports?freshness=stale", headers=API_HEADERS
    ).json()
    assert stale_reports[0]["freshness_status"] == "Stale"

    action_required = client.get(
        "/api/v1/operations/action-required", headers=API_HEADERS
    ).json()
    assert "agent_report_stale" in {issue["issue_type"] for issue in action_required}


def test_gpfs_storage_mapping_uses_default_csi_driver(harness):
    client: TestClient = harness["client"]
    reports = [
        agent_report(
            cluster_name="cluster-a",
            node_name="c1-rm-gpfs",
            node_uid="uid-c1-rm-gpfs",
            worker_role="RM",
            mounts=[],
            csi=[csi(GPFS_DRIVER, "gpfs-csi")],
        ),
        agent_report(
            cluster_name="cluster-a",
            node_name="c1-dm-gpfs",
            node_uid="uid-c1-dm-gpfs",
            worker_role="DM",
            mounts=[],
            csi=[csi(GPFS_DRIVER, "gpfs-csi")],
        ),
    ]
    for report in reports:
        response = client.post(
            "/api/v1/agent/reports",
            json=report,
            headers={
                "x-dms-actor": f"node:{report['cluster_name']}:{report['node_name']}"
            },
        )
        assert response.status_code == 200

    gpfs = upsert_mapping(
        client,
        storage_name="gpfs-a",
        backend_type="gpfs",
        cluster_name="cluster-a",
        storage_class_name="gpfs-csi",
    )

    assert gpfs["status"] == "Ready"
    sanity = gpfs["mapping"]["sanity_result"]
    assert "csi_driver_mismatch" not in issue_codes(sanity["errors"])
    assert sanity["readiness"]["resource_management"] == "Ready"
    assert sanity["readiness"]["data_management"] == "Ready"


def test_planner_rejects_failed_mapping_and_uses_ready_agent_pool(harness):
    client: TestClient = harness["client"]
    repository: DmsRepository = harness["repository"]
    submit_core_reports(client)

    upsert_mapping(
        client,
        storage_name="cephfs-a",
        backend_type="cephfs",
        cluster_name="cluster-a",
        storage_class_name="testbed-cephfs",
    )
    upsert_mapping(
        client,
        storage_name="missing-longhorn",
        backend_type="longhorn",
        cluster_name="cluster-b",
        storage_class_name="does-not-exist",
    )

    failed = client.post(
        "/api/v1/data-management/scan",
        json={
            "requester_id": "user-1",
            "storage_name": "missing-longhorn",
            "target_path": "dataset",
        },
        headers=API_HEADERS,
    )
    ready = client.post(
        "/api/v1/data-management/scan",
        json={
            "requester_id": "user-1",
            "storage_name": "cephfs-a",
            "target_path": "dataset",
        },
        headers=API_HEADERS,
    )

    assert Planner(repository).run_once(limit=10) == 2

    failed_request = repository.get_request(failed.json()["request_id"])
    assert failed_request["status"] == LifecycleState.REJECTED.value
    assert repository.get_plan_by_request(failed_request["request_id"]) is None
    [failed_result] = repository.get_results(failed_request["request_id"])
    assert failed_result["verification_summary"]["backend_side_effect"] is False

    ready_job = repository.get_data_job_by_request(ready.json()["request_id"])
    assert ready_job["worker_pool"]["selection"] == "agent-inventory"
    assert ready_job["worker_pool"]["readiness"]["data_management"] == "Ready"
    assert ready_job["worker_pool"]["candidates"]


def test_storage_mapping_upsert_conflicts_with_active_work(harness):
    client: TestClient = harness["client"]
    repository: DmsRepository = harness["repository"]
    submit_core_reports(client)
    upsert_mapping(
        client,
        storage_name="cephfs-a",
        backend_type="cephfs",
        cluster_name="cluster-a",
        storage_class_name="testbed-cephfs",
    )

    request = client.post(
        "/api/v1/resource-management/filesystems",
        json={
            "requester_id": "user-1",
            "payload": {
                "storage_name": "cephfs-a",
                "directory_name": "alpha",
                "resource_type": "user",
                "users": ["alice", "bob"],
            },
        },
        headers=API_HEADERS,
    )
    assert request.status_code == 202

    conflict = upsert_mapping(
        client,
        storage_name="cephfs-a",
        backend_type="cephfs",
        cluster_name="cluster-a",
        storage_class_name="testbed-cephfs",
        expected_status=409,
    )
    assert conflict["kind"] == "request"

    [mutation] = repository.list_control_mutations(limit=1)
    assert mutation["mutation_kind"] == "storage_mapping.upsert"
    assert mutation["status"] == "Conflict"
    assert mutation["target_key"] == "cephfs-a"


def upsert_mapping(
    client: TestClient,
    *,
    storage_name: str,
    backend_type: str,
    cluster_name: str,
    storage_class_name: str,
    backend_template: dict | None = None,
    expected_status: int = 200,
) -> dict:
    response = client.post(
        "/api/v1/resource-management/storage-mappings",
        json={
            "storage_name": storage_name,
            "backend_template": backend_template
            or (
                {
                    "backend_type": backend_type,
                    "mount_path": f"/mnt/{storage_name}",
                    "managed_root": f"/mnt/{storage_name}/dms",
                    # GPFS requires an explicit filesystem_name at registration.
                    **({"filesystem_name": storage_name} if backend_type == "gpfs" else {}),
                }
                if backend_type in ("cephfs", "wekafs", "gpfs")
                else {"backend_type": backend_type}
            ),
            "cluster_name": cluster_name,
            "storage_class_name": storage_class_name,
        },
        headers=API_HEADERS,
    )
    assert response.status_code == expected_status
    return response.json() if expected_status < 400 else response.json()["detail"]


def submit_core_reports(client: TestClient) -> None:
    reports = [
        agent_report(
            cluster_name="cluster-a",
            node_name="c1-rm",
            node_uid="uid-c1-rm",
            worker_role="RM",
            mounts=[mount("cephfs-a")],
            csi=[csi(CEPHFS_DRIVER, "testbed-cephfs")],
        ),
        agent_report(
            cluster_name="cluster-a",
            node_name="c1-dm",
            node_uid="uid-c1-dm",
            worker_role="DM",
            mounts=[mount("cephfs-a"), mount("longhorn-b")],
            csi=[
                csi(CEPHFS_DRIVER, "testbed-cephfs"),
                csi(LONGHORN_DRIVER, "testbed-longhorn"),
            ],
            tools=[{"name": "dscan", "healthy": True}],
        ),
        agent_report(
            cluster_name="cluster-b",
            node_name="c2-rm",
            node_uid="uid-c2-rm",
            worker_role="RM",
            mounts=[mount("longhorn-b")],
            csi=[csi(LONGHORN_DRIVER, "testbed-longhorn")],
        ),
    ]
    for report in reports:
        response = client.post(
            "/api/v1/agent/reports",
            json=report,
            headers={
                "x-dms-actor": f"node:{report['cluster_name']}:{report['node_name']}"
            },
        )
        assert response.status_code == 200


def agent_report(
    *,
    cluster_name: str,
    node_name: str,
    node_uid: str,
    worker_role: str,
    mounts: list[dict],
    csi: list[dict],
    tools: list[dict] | None = None,
    reported_at: str | None = None,
) -> dict:
    return {
        "schema_version": "phase3.v1",
        "reported_at": reported_at,
        "cluster_name": cluster_name,
        "node_name": node_name,
        "node_uid": node_uid,
        "worker_role": worker_role,
        "mounts": mounts,
        "csi": csi,
        "tools": tools or [],
        "credentials": [{"name": "testbed", "healthy": True}],
        "networks": [{"name": "storage-net", "reachable": True}],
        "identity_evidence": {"source": "test"},
    }


def mount(storage_name: str) -> dict:
    return {
        "storage_name": storage_name,
        "mount_path": f"/mnt/dms/{storage_name}",
        "filesystem_type": "posix",
        "readable": True,
        "writable": True,
    }


def csi(driver: str, storage_class_name: str) -> dict:
    return {
        "driver": driver,
        "storage_classes": [storage_class_name],
        "node_plugin_ready": True,
    }


def issue_codes(issues: list[dict]) -> set[str]:
    return {issue["code"] for issue in issues}


def static_inventory() -> dict:
    return {
        "clusters": {
            "cluster-a": {
                "nodes": [{"name": "c1-rm", "uid": "uid-c1-rm"}],
                "storage_classes": [
                    {
                        "name": "testbed-cephfs",
                        "provisioner": CEPHFS_DRIVER,
                        "parameters": {},
                    },
                    {
                        "name": "gpfs-csi",
                        "provisioner": GPFS_DRIVER,
                        "parameters": {},
                    }
                ],
                "csi_drivers": [{"name": CEPHFS_DRIVER}, {"name": GPFS_DRIVER}],
            },
            "cluster-b": {
                "nodes": [{"name": "c2-rm", "uid": "uid-c2-rm"}],
                "storage_classes": [
                    {
                        "name": "testbed-longhorn",
                        "provisioner": LONGHORN_DRIVER,
                        "parameters": {},
                    }
                ],
                "csi_drivers": [{"name": LONGHORN_DRIVER}],
            },
        }
    }


@pytest.fixture()
def harness(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
        agent_report_stale_seconds=1,
        control_cluster_name="cluster-a",
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    observability = ObservabilityRepository(observability_db)
    app = create_app(
        settings,
        repository,
        observability,
        kubernetes_inventory=StaticKubernetesReadOnlyInventoryAdapter(static_inventory()),
    )
    return {
        "repository": repository,
        "observability": observability,
        "client": TestClient(app),
    }


def test_filesystem_mapping_requires_explicit_managed_root(harness):
    """Filesystem storage mappings must declare managed_root at registration (no implicit
    {mount_path}/dms default). Non-filesystem (CSI) backends are exempt."""
    client: TestClient = harness["client"]
    detail = upsert_mapping(
        client,
        storage_name="needs-managed-root",
        backend_type="cephfs",
        cluster_name="cluster-a",
        storage_class_name="testbed-cephfs",
        backend_template={"backend_type": "cephfs", "mount_path": "/mnt/needs-root"},
        expected_status=422,
    )
    assert "managed_root" in str(detail)
