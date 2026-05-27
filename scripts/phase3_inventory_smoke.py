from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi.testclient import TestClient

from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import LifecycleState
from dms.planner import Planner


CEPHFS_DRIVER = "rook-ceph.cephfs.csi.ceph.com"
LONGHORN_DRIVER = "driver.longhorn.io"
API_HEADERS = {"x-dms-actor": "api-client"}


def main() -> int:
    settings = Settings.from_env()
    app = create_app(settings)
    services = app.state.services
    client = TestClient(app)
    token = uuid4().hex[:8]

    assert_true(settings.observability_is_separate, "observability DB must be separate")
    assert_true(_table_exists(settings.database_url, "agent_reports"), "agent_reports table")
    assert_true(_table_exists(settings.database_url, "storage_mappings"), "storage_mappings table")

    inventory = _get_inventory(client)
    assert_storage_class(inventory, "cluster-a", "testbed-cephfs", CEPHFS_DRIVER)
    assert_storage_class(inventory, "cluster-b", "testbed-longhorn", LONGHORN_DRIVER)

    c1_node = _select_node(inventory, "cluster-a", preferred=("c1-worker", "c1-control"))
    c2_node = _select_node(inventory, "cluster-b", preferred=("c2-worker", "c2-control"))

    submit_report(
        client,
        agent_report(
            cluster_name="cluster-a",
            node_name=c1_node["name"],
            node_uid=c1_node["uid"],
            worker_role="RM",
            mounts=[mount("cephfs-a")],
            csi=[csi(CEPHFS_DRIVER, "testbed-cephfs")],
        ),
    )
    submit_report(
        client,
        agent_report(
            cluster_name="cluster-a",
            node_name=c1_node["name"],
            node_uid=c1_node["uid"],
            worker_role="DM",
            mounts=[mount("cephfs-a"), mount("longhorn-b")],
            csi=[
                csi(CEPHFS_DRIVER, "testbed-cephfs"),
                csi(LONGHORN_DRIVER, "testbed-longhorn"),
            ],
            tools=[{"name": "dscan", "version": "testbed", "healthy": True}],
        ),
    )
    submit_report(
        client,
        agent_report(
            cluster_name="cluster-b",
            node_name=c2_node["name"],
            node_uid=c2_node["uid"],
            worker_role="RM",
            mounts=[mount("longhorn-b")],
            csi=[csi(LONGHORN_DRIVER, "testbed-longhorn")],
        ),
    )
    stale_report = agent_report(
        cluster_name="cluster-a",
        node_name=c1_node["name"],
        node_uid=c1_node["uid"],
        worker_role="DM",
        mounts=[mount(f"stale-{token}")],
        csi=[csi(CEPHFS_DRIVER, "testbed-cephfs")],
        reported_at="2000-01-01T00:00:00+00:00",
    )
    submit_report(client, stale_report)

    mismatch = client.post(
        "/api/v1/agent/reports",
        json=agent_report(
            cluster_name="cluster-a",
            node_name=c1_node["name"],
            node_uid=c1_node["uid"],
            worker_role="RM",
            mounts=[mount("ignored")],
            csi=[csi(CEPHFS_DRIVER, "testbed-cephfs")],
        ),
        headers={"x-dms-actor": "node:cluster-a:not-the-node"},
    )
    assert_equal(mismatch.status_code, 403, "agent identity mismatch rejected")

    inventory = _get_inventory(client)
    assert_true(
        f"stale-{token}"
        not in inventory["worker_roles"]["DM"]["cluster-a"]["mounts_by_storage_name"],
        "stale report excluded from effective inventory",
    )

    cephfs = upsert_mapping(
        client,
        storage_name="cephfs-a",
        backend_template={
            "backend_type": "cephfs",
            "mount_path": "/not-read-from-api-pod",
        },
        cluster_name="cluster-a",
        storage_class_name="testbed-cephfs",
    )
    assert_equal(cephfs["status"], "Ready", "cluster-a CephFS mapping ready")
    assert_true(
        "/not-read-from-api-pod" not in str(cephfs["mapping"]["sanity_result"]),
        "API local mount path must not affect sanity_result",
    )

    longhorn = upsert_mapping(
        client,
        storage_name="longhorn-b",
        backend_template={"backend_type": "longhorn"},
        cluster_name="cluster-b",
        storage_class_name="testbed-longhorn",
    )
    assert_equal(longhorn["status"], "Ready", "cluster-b Longhorn mapping ready")

    missing = upsert_mapping(
        client,
        storage_name=f"missing-{token}",
        backend_template={"backend_type": "longhorn"},
        cluster_name="cluster-b",
        storage_class_name=f"missing-{token}",
    )
    assert_equal(missing["status"], "Failed", "missing StorageClass mapping failed")
    assert_true(
        "storage_class_missing" in issue_codes(missing["mapping"]["sanity_result"]["errors"]),
        "missing StorageClass error code",
    )

    mismatch_mapping = upsert_mapping(
        client,
        storage_name="longhorn-b",
        backend_template={"backend_type": "cephfs"},
        cluster_name="cluster-b",
        storage_class_name="testbed-longhorn",
    )
    assert_equal(mismatch_mapping["status"], "Failed", "CSI mismatch mapping failed")
    assert_true(
        "csi_driver_mismatch"
        in issue_codes(mismatch_mapping["mapping"]["sanity_result"]["errors"]),
        "CSI mismatch error code",
    )

    failed_request = client.post(
        "/api/v1/data-management/scan",
        json={
            "requester_id": "portal:alice",
            "storage_name": f"missing-{token}",
            "target_path": f"phase3-missing-{token}",
        },
        headers=API_HEADERS,
    )
    assert_equal(failed_request.status_code, 202, "failed mapping scan submit")
    ready_request = client.post(
        "/api/v1/data-management/scan",
        json={
            "requester_id": "portal:alice",
            "storage_name": "cephfs-a",
            "target_path": f"phase3-ready-{token}",
        },
        headers=API_HEADERS,
    )
    assert_equal(ready_request.status_code, 202, "ready mapping scan submit")

    assert_equal(Planner(services.repository).run_once(limit=10), 2, "Planner handles two requests")
    failed_request_id = failed_request.json()["request_id"]
    ready_request_id = ready_request.json()["request_id"]
    assert_equal(
        services.repository.get_request(failed_request_id)["status"],
        LifecycleState.REJECTED.value,
        "failed mapping request rejected",
    )
    assert_true(
        services.repository.get_plan_by_request(failed_request_id) is None,
        "failed mapping must not create plan",
    )
    ready_job = services.repository.get_data_job_by_request(ready_request_id)
    assert_equal(
        ready_job["worker_pool"]["selection"],
        "agent-inventory",
        "ready mapping uses agent inventory worker pool",
    )

    conflict = upsert_mapping(
        client,
        storage_name="cephfs-a",
        backend_template={"backend_type": "cephfs"},
        cluster_name="cluster-a",
        storage_class_name="testbed-cephfs",
        expected_status=409,
    )
    assert_equal(conflict["kind"], "request", "active work blocks mapping update")

    action_required = client.get("/api/v1/operations/action-required", headers=API_HEADERS)
    assert_equal(action_required.status_code, 200, "action-required query")
    issue_types = {issue["issue_type"] for issue in action_required.json()}
    assert_true("storage_mapping_failed" in issue_types, "failed mapping action-required")
    assert_true("storage_class_missing" in issue_types, "missing SC action-required")
    assert_true("csi_driver_mismatch" in issue_types, "CSI mismatch action-required")
    assert_true("agent_report_stale" in issue_types, "stale report action-required")

    agent_reports = client.get(
        "/api/v1/operations/agent-reports?freshness=stale", headers=API_HEADERS
    )
    assert_equal(agent_reports.status_code, 200, "stale agent report query")
    assert_true(agent_reports.json(), "stale agent reports returned")

    summary = {
        "status": "ok",
        "operational_database_url": _mask_url(settings.database_url),
        "observability_database_url": _mask_url(settings.observability_database_url),
        "control_cluster_name": settings.control_cluster_name,
        "kubernetes_inventory_mode": settings.kubernetes_inventory_mode,
        "cluster_a_storage_class": "testbed-cephfs",
        "cluster_b_storage_class": "testbed-longhorn",
        "cephfs_mapping_status": cephfs["status"],
        "longhorn_mapping_status_before_mismatch_check": longhorn["status"],
        "missing_mapping_status": missing["status"],
        "csi_mismatch_status": mismatch_mapping["status"],
        "failed_mapping_request_status": services.repository.get_request(failed_request_id)[
            "status"
        ],
        "ready_scan_job_worker_pool": ready_job["worker_pool"],
        "active_mapping_update_conflict": conflict,
        "action_required_issue_types": sorted(issue_types),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _get_inventory(client: TestClient) -> dict:
    response = client.get("/api/v1/operations/inventory", headers=API_HEADERS)
    assert_equal(response.status_code, 200, "inventory query")
    return response.json()


def assert_storage_class(
    inventory: dict, cluster_name: str, storage_class_name: str, provisioner: str
) -> None:
    classes = inventory["clusters"][cluster_name]["storage_classes"]
    matching = [item for item in classes if item["name"] == storage_class_name]
    assert_true(matching, f"{cluster_name}/{storage_class_name} exists")
    assert_equal(
        matching[0]["provisioner"],
        provisioner,
        f"{cluster_name}/{storage_class_name} provisioner",
    )


def _select_node(inventory: dict, cluster_name: str, *, preferred: tuple[str, ...]) -> dict:
    nodes = inventory["clusters"][cluster_name]["nodes"]
    assert_true(nodes, f"{cluster_name} must have nodes")
    by_name = {node["name"]: node for node in nodes}
    for name in preferred:
        if name in by_name:
            return by_name[name]
    return nodes[0]


def submit_report(client: TestClient, report: dict) -> None:
    response = client.post(
        "/api/v1/agent/reports",
        json=report,
        headers={"x-dms-actor": f"node:{report['cluster_name']}:{report['node_name']}"},
    )
    assert_equal(response.status_code, 200, f"agent report {report['worker_role']}")


def upsert_mapping(
    client: TestClient,
    *,
    storage_name: str,
    backend_template: dict,
    cluster_name: str,
    storage_class_name: str,
    expected_status: int = 200,
) -> dict:
    response = client.post(
        "/api/v1/resource-management/storage-mappings",
        json={
            "storage_name": storage_name,
            "backend_template": backend_template,
            "cluster_name": cluster_name,
            "storage_class_name": storage_class_name,
        },
        headers=API_HEADERS,
    )
    assert_equal(response.status_code, expected_status, f"upsert mapping {storage_name}")
    return response.json() if expected_status < 400 else response.json()["detail"]


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
        "networks": [{"name": "testbed-storage", "reachable": True}],
        "identity_evidence": {"source": "phase3-smoke"},
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


def _table_exists(database_url: str, table_name: str) -> bool:
    scheme = urlsplit(database_url).scheme
    with Database(database_url).connect() as connection:
        if scheme == "sqlite":
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = ?
                  AND table_schema = ANY (current_schemas(false))
                LIMIT 1
                """,
                (table_name,),
            ).fetchone()
    return row is not None


def _mask_url(url: str) -> str:
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    credentials, host = parts.netloc.rsplit("@", 1)
    user = credentials.split(":", 1)[0]
    return urlunsplit((parts.scheme, f"{user}:***@{host}", parts.path, parts.query, parts.fragment))


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_true(value, label: str) -> None:
    if not value:
        raise AssertionError(label)


if __name__ == "__main__":
    raise SystemExit(main())
