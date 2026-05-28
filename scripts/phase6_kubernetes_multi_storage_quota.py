from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi.testclient import TestClient

from dms.adapters import KubernetesNamespaceQuotaLiveAdapter, StubFilesystemBackendAdapter
from dms.api import create_app
from dms.config import Settings
from dms.domain import LifecycleState
from dms.planner import Planner
from dms.workers import RMWorkerRuntime


API_HEADERS = {"x-dms-actor": "api-client"}


@dataclass(frozen=True)
class StorageTarget:
    name: str
    cluster_name: str
    control_host: str
    storage_name: str
    storage_class_name: str
    provisioner: str
    access_modes: list[str]
    preferred_nodes: tuple[str, ...]


CEPHFS = StorageTarget(
    name="cephfs",
    cluster_name="cluster-a",
    control_host="c1-control",
    storage_name="cephfs-a",
    storage_class_name="testbed-cephfs",
    provisioner="rook-ceph.cephfs.csi.ceph.com",
    access_modes=["ReadWriteMany"],
    preferred_nodes=("c1-worker", "c1-control"),
)
LONGHORN = StorageTarget(
    name="longhorn",
    cluster_name="cluster-b",
    control_host="c2-control",
    storage_name="longhorn-b",
    storage_class_name="testbed-longhorn",
    provisioner="driver.longhorn.io",
    access_modes=["ReadWriteOnce"],
    preferred_nodes=("c2-worker", "c2-control"),
)
LONGHORN_STATIC = StorageTarget(
    name="longhorn-static",
    cluster_name="cluster-b",
    control_host="c2-control",
    storage_name="longhorn-static-b",
    storage_class_name="longhorn-static",
    provisioner="driver.longhorn.io",
    access_modes=["ReadWriteOnce"],
    preferred_nodes=("c2-worker", "c2-control"),
)


def main() -> int:
    settings = Settings.from_env()
    assert_true(
        settings.database_url.startswith("postgresql://"),
        "Phase 6 live verification must use operational PostgreSQL",
    )
    assert_true(settings.observability_is_separate, "observability DB must be separate")

    app = create_app(settings)
    services = app.state.services
    client = TestClient(app)
    live_adapter = KubernetesNamespaceQuotaLiveAdapter.from_settings(settings)
    token = uuid4().hex[:8]

    inventory = get_inventory(client)
    c1_node = select_node(inventory, "cluster-a", preferred=CEPHFS.preferred_nodes)
    c2_node = select_node(inventory, "cluster-b", preferred=LONGHORN.preferred_nodes)
    submit_phase6_reports(client, c1_node, c2_node)

    multi_summary = verify_longhorn_multi(
        token=token,
        client=client,
        services=services,
        live_adapter=live_adapter,
        inventory=inventory,
        settings=settings,
    )
    cephfs_summary = verify_cephfs_regression(
        token=token,
        client=client,
        services=services,
        live_adapter=live_adapter,
        inventory=inventory,
        settings=settings,
    )

    summary = {
        "status": "ok",
        "operational_database_url": mask_url(settings.database_url),
        "observability_database_url": mask_url(settings.observability_database_url),
        "targets": [multi_summary, cephfs_summary],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def verify_longhorn_multi(
    *,
    token: str,
    client: TestClient,
    services,
    live_adapter: KubernetesNamespaceQuotaLiveAdapter,
    inventory: dict,
    settings: Settings,
) -> dict:
    namespace_name = f"dms-phase6-longhorn-multi-{token}"
    for target in (LONGHORN, LONGHORN_STATIC):
        assert_storage_class(
            inventory, target.cluster_name, target.storage_class_name, target.provisioner
        )
        mapping = upsert_mapping(client, target)
        assert_equal(mapping["status"], "Ready", f"{target.name} mapping ready")

    rm_worker = rm_worker_for(services, live_adapter, settings, "phase6-rm-longhorn-multi")
    create_hard = {
        "requests.storage": "512Mi",
        "persistentvolumeclaims": "6",
        sc_key(LONGHORN): "256Mi",
        sc_pvc_key(LONGHORN): "3",
        sc_key(LONGHORN_STATIC): "128Mi",
        sc_pvc_key(LONGHORN_STATIC): "2",
    }
    update_hard = {
        "requests.storage": "768Mi",
        "persistentvolumeclaims": "8",
        sc_key(LONGHORN): "384Mi",
        sc_pvc_key(LONGHORN): "4",
        sc_key(LONGHORN_STATIC): "256Mi",
        sc_pvc_key(LONGHORN_STATIC): "4",
    }
    drift_hard = {**update_hard, sc_key(LONGHORN): "512Mi"}

    create_id = submit_quota_request(
        client,
        "POST",
        "/api/v1/resource-management/kubernetes/namespace-quotas",
        requester_id="portal:phase6:longhorn-multi",
        payload={
            "cluster_name": "cluster-b",
            "namespace_name": namespace_name,
            "allow_namespace_create": True,
            "resource_type": "user",
            "quota": {"requests_storage_bytes": 512 * 1024**2, "pvc_count": 6},
            "storage_class_quotas": [
                {
                    "storage_name": LONGHORN.storage_name,
                    "requests_storage_bytes": 256 * 1024**2,
                    "pvc_count": 3,
                },
                {
                    "storage_name": LONGHORN_STATIC.storage_name,
                    "requests_storage_bytes": 128 * 1024**2,
                    "pvc_count": 2,
                },
            ],
        },
    )
    run_planner_worker(services.repository, rm_worker, create_id, "multi create")
    assert_equal(
        read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
        create_hard,
        "multi create hard",
    )

    longhorn_pvc = apply_pvc(live_adapter, LONGHORN, namespace_name, "phase6-lh-64mi", "64Mi")
    static_pvc = apply_pvc(
        live_adapter, LONGHORN_STATIC, namespace_name, "phase6-static-64mi", "64Mi"
    )
    assert_equal(longhorn_pvc["status"].get("phase"), "Bound", "longhorn PVC Bound")
    assert_equal(static_pvc["status"].get("phase"), "Bound", "longhorn-static PVC Bound")
    wait_resource_quota_used(
        live_adapter,
        LONGHORN,
        namespace_name,
        {
            sc_key(LONGHORN): "64Mi",
            sc_key(LONGHORN_STATIC): "64Mi",
            sc_pvc_key(LONGHORN): "1",
            sc_pvc_key(LONGHORN_STATIC): "1",
        },
    )

    static_over = apply_rejected_pvc(
        live_adapter, LONGHORN_STATIC, namespace_name, "phase6-static-over-96mi", "96Mi"
    )
    assert_true(static_over["rejected"], "longhorn-static over-quota PVC rejected")

    update_id = submit_quota_request(
        client,
        "PATCH",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}",
        requester_id="portal:phase6:longhorn-multi",
        payload={
            "quota": {"requests_storage_bytes": 768 * 1024**2, "pvc_count": 8},
            "storage_class_quotas": [
                {
                    "storage_name": LONGHORN.storage_name,
                    "requests_storage_bytes": 384 * 1024**2,
                    "pvc_count": 4,
                },
                {
                    "storage_name": LONGHORN_STATIC.storage_name,
                    "requests_storage_bytes": 256 * 1024**2,
                    "pvc_count": 4,
                },
            ],
            "memo": "phase6 multi quota increase",
        },
    )
    run_planner_worker(services.repository, rm_worker, update_id, "multi update")
    assert_equal(
        read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
        update_hard,
        "multi update hard",
    )
    apply_pvc(live_adapter, LONGHORN_STATIC, namespace_name, "phase6-static-96mi", "96Mi")
    apply_pvc(live_adapter, LONGHORN, namespace_name, "phase6-lh-192mi", "192Mi")
    wait_resource_quota_used(
        live_adapter,
        LONGHORN,
        namespace_name,
        {
            sc_key(LONGHORN): "256Mi",
            sc_key(LONGHORN_STATIC): "160Mi",
            sc_pvc_key(LONGHORN): "2",
            sc_pvc_key(LONGHORN_STATIC): "2",
        },
    )

    sync_before_decrease_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}:sync",
        requester_id="portal:phase6:longhorn-multi",
        payload={"accept_live_state": True, "reason": "refresh used before decrease"},
    )
    run_planner_worker(
        services.repository, rm_worker, sync_before_decrease_id, "multi sync before decrease"
    )
    decrease_id = submit_quota_request(
        client,
        "PATCH",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}",
        requester_id="portal:phase6:longhorn-multi",
        payload={
            "quota": {"requests_storage_bytes": 768 * 1024**2, "pvc_count": 8},
            "storage_class_quotas": [
                {
                    "storage_name": LONGHORN.storage_name,
                    "requests_storage_bytes": 384 * 1024**2,
                    "pvc_count": 4,
                },
                {
                    "storage_name": LONGHORN_STATIC.storage_name,
                    "requests_storage_bytes": 128 * 1024**2,
                    "pvc_count": 4,
                },
            ],
        },
    )
    assert_equal(Planner(services.repository).run_once(), 1, "multi decrease planner")
    assert_true(
        services.repository.get_plan_by_request(decrease_id) is None,
        "multi decrease created no plan",
    )
    [decrease_result] = services.repository.get_results(decrease_id)
    assert_equal(
        decrease_result["terminal_status"],
        LifecycleState.REJECTED.value,
        "multi decrease rejected",
    )
    assert_equal(
        read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
        update_hard,
        "multi decrease no side effect",
    )

    block_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}:block",
        requester_id="portal:phase6:longhorn-multi",
        payload={"block": True, "block_mode": "quota-zero", "reason": "phase6 block"},
    )
    run_planner_worker(services.repository, rm_worker, block_id, "multi block")
    assert_equal(
        read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
        {key: "0" for key in update_hard},
        "multi block hard",
    )
    blocked_longhorn = apply_rejected_pvc(
        live_adapter, LONGHORN, namespace_name, "phase6-lh-blocked-1mi", "1Mi"
    )
    blocked_static = apply_rejected_pvc(
        live_adapter, LONGHORN_STATIC, namespace_name, "phase6-static-blocked-1mi", "1Mi"
    )
    assert_true(blocked_longhorn["rejected"], "longhorn blocked PVC rejected")
    assert_true(blocked_static["rejected"], "longhorn-static blocked PVC rejected")

    unblock_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}:block",
        requester_id="portal:phase6:longhorn-multi",
        payload={"block": False, "reason": "phase6 unblock"},
    )
    run_planner_worker(services.repository, rm_worker, unblock_id, "multi unblock")
    assert_equal(
        read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
        update_hard,
        "multi unblock hard",
    )

    patch_resource_quota_hard(live_adapter, LONGHORN, namespace_name, drift_hard)
    create_non_dms_quota(
        live_adapter,
        LONGHORN,
        namespace_name,
        {sc_key(LONGHORN): "128Mi"},
    )
    check_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}:check",
        requester_id="portal:phase6:longhorn-multi",
        payload={
            "scope": "single",
            "include_live_resourcequota": True,
            "include_effective_quota": True,
        },
    )
    run_planner_worker(services.repository, rm_worker, check_id, "multi check")
    [check_result] = services.repository.get_results(check_id)
    check_summary = check_result["verification_summary"]
    assert_equal(check_summary["consistency_status"], "Drifted", "multi drift status")
    assert_true(
        any(issue.get("key") == sc_key(LONGHORN) for issue in check_summary["issues"]),
        "multi drift issue is keyed",
    )
    assert_true(
        any(
            warning.get("type") == "non_dms_quota_more_restrictive"
            for warning in check_summary["effective_quota_warnings"]
        ),
        "multi effective quota warning",
    )

    sync_after_drift_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}:sync",
        requester_id="portal:phase6:longhorn-multi",
        payload={
            "accept_live_state": True,
            "include_effective_quota": True,
            "reason": "accept drifted multi live state",
        },
    )
    run_planner_worker(services.repository, rm_worker, sync_after_drift_id, "multi sync")
    synced_resource = resource_for_request(services.repository, sync_after_drift_id)
    assert_equal(
        synced_resource["desired_state"]["resource_quota_hard"],
        drift_hard,
        "multi sync hard",
    )
    assert_equal(
        synced_resource["desired_state"]["storage_class_quotas"][0][
            "requests_storage_bytes"
        ],
        512 * 1024**2,
        "multi sync storage class bytes",
    )

    delete_id = submit_quota_request(
        client,
        "DELETE",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}",
        requester_id="portal:phase6:longhorn-multi",
        payload={"reason": "phase6 delete dms quota only"},
    )
    run_planner_worker(services.repository, rm_worker, delete_id, "multi delete")
    deleted_quota = read_resource_quota(
        live_adapter, LONGHORN, namespace_name, check=False, name="dms-storage-quota"
    )
    assert_true(not deleted_quota["exists"], "multi dms quota deleted")
    non_dms = read_resource_quota(
        live_adapter, LONGHORN, namespace_name, check=False, name="phase6-non-dms-quota"
    )
    assert_true(non_dms["exists"], "multi non-DMS quota preserved")

    cleanup = os.getenv("DMS_PHASE6_CLEANUP", "true").lower() != "false"
    if cleanup:
        delete_namespace(live_adapter, LONGHORN, namespace_name)

    resource = resource_for_request(services.repository, delete_id)
    return {
        "target": "longhorn-multi-storageclass",
        "cluster_name": "cluster-b",
        "namespace_name": namespace_name,
        "storage_names": [LONGHORN.storage_name, LONGHORN_STATIC.storage_name],
        "storage_class_names": [
            LONGHORN.storage_class_name,
            LONGHORN_STATIC.storage_class_name,
        ],
        "create_request_id": create_id,
        "update_request_id": update_id,
        "block_request_id": block_id,
        "unblock_request_id": unblock_id,
        "check_request_id": check_id,
        "sync_request_id": sync_after_drift_id,
        "delete_request_id": delete_id,
        "decrease_guard_status": decrease_result["terminal_status"],
        "blocked_pvc_rejected": blocked_longhorn["rejected"] and blocked_static["rejected"],
        "drift_check_status": check_summary["consistency_status"],
        "effective_warning_types": [
            warning["type"] for warning in check_summary["effective_quota_warnings"]
        ],
        "synced_resource_quota_hard": synced_resource["desired_state"][
            "resource_quota_hard"
        ],
        "delete_resource_status": resource["status"],
        "non_dms_quota_preserved": non_dms["exists"],
        "cleanup_namespace_requested": cleanup,
    }


def verify_cephfs_regression(
    *,
    token: str,
    client: TestClient,
    services,
    live_adapter: KubernetesNamespaceQuotaLiveAdapter,
    inventory: dict,
    settings: Settings,
) -> dict:
    namespace_name = f"dms-phase6-cephfs-regression-{token}"
    assert_storage_class(inventory, "cluster-a", CEPHFS.storage_class_name, CEPHFS.provisioner)
    mapping = upsert_mapping(client, CEPHFS)
    assert_equal(mapping["status"], "Ready", "cephfs mapping ready")
    rm_worker = rm_worker_for(services, live_adapter, settings, "phase6-rm-cephfs")

    create_id = submit_quota_request(
        client,
        "POST",
        "/api/v1/resource-management/kubernetes/namespace-quotas",
        requester_id="portal:phase6:cephfs",
        payload={
            "cluster_name": "cluster-a",
            "namespace_name": namespace_name,
            "allow_namespace_create": True,
            "resource_type": "user",
            "quota": {"requests_storage_bytes": 128 * 1024**2, "pvc_count": 2},
            "storage_class_quotas": [
                {
                    "storage_name": CEPHFS.storage_name,
                    "requests_storage_bytes": 128 * 1024**2,
                }
            ],
        },
    )
    run_planner_worker(services.repository, rm_worker, create_id, "cephfs create")
    apply_pvc(live_adapter, CEPHFS, namespace_name, "phase6-cephfs-64mi", "64Mi")

    update_id = submit_quota_request(
        client,
        "PATCH",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/{namespace_name}",
        requester_id="portal:phase6:cephfs",
        payload={
            "quota": {"requests_storage_bytes": 256 * 1024**2, "pvc_count": 4},
            "storage_class_quotas": [
                {
                    "storage_name": CEPHFS.storage_name,
                    "requests_storage_bytes": 256 * 1024**2,
                }
            ],
        },
    )
    run_planner_worker(services.repository, rm_worker, update_id, "cephfs update")

    check_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/{namespace_name}:check",
        requester_id="portal:phase6:cephfs",
        payload={"scope": "single", "include_live_resourcequota": True},
    )
    run_planner_worker(services.repository, rm_worker, check_id, "cephfs check")
    [check_result] = services.repository.get_results(check_id)
    assert_equal(
        check_result["verification_summary"]["consistency_status"],
        "Consistent",
        "cephfs regression check",
    )

    delete_id = submit_quota_request(
        client,
        "DELETE",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/{namespace_name}",
        requester_id="portal:phase6:cephfs",
        payload={"reason": "phase6 cephfs regression cleanup"},
    )
    run_planner_worker(services.repository, rm_worker, delete_id, "cephfs delete")
    cleanup = os.getenv("DMS_PHASE6_CLEANUP", "true").lower() != "false"
    if cleanup:
        delete_namespace(live_adapter, CEPHFS, namespace_name)
    resource = resource_for_request(services.repository, delete_id)
    return {
        "target": "cephfs-single-storageclass-regression",
        "cluster_name": "cluster-a",
        "namespace_name": namespace_name,
        "storage_name": CEPHFS.storage_name,
        "storage_class_name": CEPHFS.storage_class_name,
        "create_request_id": create_id,
        "update_request_id": update_id,
        "check_request_id": check_id,
        "delete_request_id": delete_id,
        "check_status": check_result["verification_summary"]["consistency_status"],
        "delete_resource_status": resource["status"],
        "cleanup_namespace_requested": cleanup,
    }


def rm_worker_for(
    services,
    live_adapter: KubernetesNamespaceQuotaLiveAdapter,
    settings: Settings,
    worker_id: str,
) -> RMWorkerRuntime:
    return RMWorkerRuntime(
        repository=services.repository,
        observability=services.observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=live_adapter,
        worker_id=worker_id,
        lease_seconds=settings.worker_lease_seconds,
    )


def submit_phase6_reports(client: TestClient, c1_node: dict, c2_node: dict) -> None:
    submit_report(
        client,
        agent_report(
            cluster_name="cluster-a",
            node_name=c1_node["name"],
            node_uid=c1_node["uid"],
            worker_role="RM",
            mounts=[mount(CEPHFS.storage_name)],
            csi=[csi(CEPHFS.provisioner, CEPHFS.storage_class_name)],
        ),
    )
    submit_report(
        client,
        agent_report(
            cluster_name="cluster-b",
            node_name=c2_node["name"],
            node_uid=c2_node["uid"],
            worker_role="RM",
            mounts=[mount(LONGHORN.storage_name), mount(LONGHORN_STATIC.storage_name)],
            csi=[
                csi(
                    LONGHORN.provisioner,
                    LONGHORN.storage_class_name,
                    LONGHORN_STATIC.storage_class_name,
                )
            ],
        ),
    )
    submit_report(
        client,
        agent_report(
            cluster_name="cluster-a",
            node_name=c1_node["name"],
            node_uid=c1_node["uid"],
            worker_role="DM",
            mounts=[
                mount(CEPHFS.storage_name),
                mount(LONGHORN.storage_name),
                mount(LONGHORN_STATIC.storage_name),
            ],
            csi=[
                csi(CEPHFS.provisioner, CEPHFS.storage_class_name),
                csi(
                    LONGHORN.provisioner,
                    LONGHORN.storage_class_name,
                    LONGHORN_STATIC.storage_class_name,
                ),
            ],
        ),
    )


def submit_quota_request(
    client: TestClient,
    method: str,
    path: str,
    *,
    requester_id: str,
    payload: dict,
) -> str:
    response = client.request(
        method,
        path,
        json={"requester_id": requester_id, "payload": payload},
        headers=API_HEADERS,
    )
    assert_equal(response.status_code, 202, f"{method} {path}")
    return response.json()["request_id"]


def run_planner_worker(
    repository, rm_worker: RMWorkerRuntime, request_id: str, label: str
) -> None:
    assert_equal(Planner(repository).run_once(), 1, f"{label} planner")
    assert_true(repository.get_plan_by_request(request_id) is not None, f"{label} plan")
    assert_equal(rm_worker.run_once(), 1, f"{label} worker")
    assert_equal(
        repository.get_request(request_id)["status"],
        LifecycleState.SUCCEEDED.value,
        f"{label} request succeeded",
    )


def get_inventory(client: TestClient) -> dict:
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


def select_node(inventory: dict, cluster_name: str, *, preferred: tuple[str, ...]) -> dict:
    nodes = inventory["clusters"][cluster_name]["nodes"]
    by_name = {node["name"]: node for node in nodes}
    for name in preferred:
        if name in by_name:
            return {"name": by_name[name]["name"], "uid": by_name[name]["uid"]}
    assert_true(nodes, f"{cluster_name} node list")
    return {"name": nodes[0]["name"], "uid": nodes[0]["uid"]}


def submit_report(client: TestClient, report: dict) -> None:
    response = client.post(
        "/api/v1/agent/reports",
        json=report,
        headers={"x-dms-actor": f"node:{report['cluster_name']}:{report['node_name']}"},
    )
    assert_equal(response.status_code, 200, f"agent report {report['worker_role']}")


def agent_report(
    *,
    cluster_name: str,
    node_name: str,
    node_uid: str,
    worker_role: str,
    mounts: list[dict],
    csi: list[dict],
) -> dict:
    return {
        "schema_version": "phase6.v1",
        "reported_at": None,
        "cluster_name": cluster_name,
        "node_name": node_name,
        "node_uid": node_uid,
        "worker_role": worker_role,
        "mounts": mounts,
        "csi": csi,
        "tools": [],
        "credentials": [{"name": "testbed", "healthy": True}],
        "networks": [{"name": "storage-net", "reachable": True}],
        "identity_evidence": {"source": "phase6-live"},
    }


def mount(storage_name: str) -> dict:
    return {
        "storage_name": storage_name,
        "mount_path": f"/mnt/dms/{storage_name}",
        "filesystem_type": "posix",
        "readable": True,
        "writable": True,
    }


def csi(driver: str, *storage_class_names: str) -> dict:
    return {
        "driver": driver,
        "storage_classes": list(storage_class_names),
        "node_plugin_ready": True,
    }


def upsert_mapping(client: TestClient, target: StorageTarget) -> dict:
    response = client.post(
        "/api/v1/resource-management/storage-mappings",
        json={
            "storage_name": target.storage_name,
            "backend_template": {
                "backend_type": target.name,
                "csi_driver": target.provisioner,
            },
            "cluster_name": target.cluster_name,
            "storage_class_name": target.storage_class_name,
        },
        headers=API_HEADERS,
    )
    assert_equal(response.status_code, 200, f"{target.name} mapping upsert")
    return response.json()


def read_resource_quota(
    adapter: KubernetesNamespaceQuotaLiveAdapter,
    target: StorageTarget,
    namespace_name: str,
    *,
    check: bool = True,
    name: str = "dms-storage-quota",
) -> dict:
    completed = adapter._kubectl(  # noqa: SLF001
        target.cluster_name,
        ["-n", namespace_name, "get", "resourcequota", name, "-o", "json"],
        check=check,
    )
    if completed.returncode != 0:
        return {"exists": False, "name": name, "stderr": completed.stderr.strip()}
    payload = json.loads(completed.stdout)
    payload["exists"] = True
    return payload


def apply_pvc(
    adapter: KubernetesNamespaceQuotaLiveAdapter,
    target: StorageTarget,
    namespace_name: str,
    name: str,
    storage_request: str,
) -> dict:
    manifest = pvc_manifest(target, name, storage_request)
    adapter._kubectl(  # noqa: SLF001
        target.cluster_name,
        ["-n", namespace_name, "apply", "-f", "-"],
        input_text=json.dumps(manifest, sort_keys=True),
    )
    return wait_pvc_bound(adapter, target, namespace_name, name)


def apply_rejected_pvc(
    adapter: KubernetesNamespaceQuotaLiveAdapter,
    target: StorageTarget,
    namespace_name: str,
    name: str,
    storage_request: str,
) -> dict:
    manifest = pvc_manifest(target, name, storage_request)
    completed = adapter._kubectl(  # noqa: SLF001
        target.cluster_name,
        ["-n", namespace_name, "apply", "-f", "-"],
        input_text=json.dumps(manifest, sort_keys=True),
        check=False,
    )
    stderr = completed.stderr.strip()
    return {
        "name": name,
        "request": storage_request,
        "returncode": completed.returncode,
        "rejected": completed.returncode != 0
        and ("exceeded quota" in stderr.lower() or "forbidden" in stderr.lower()),
        "stderr": stderr,
    }


def wait_pvc_bound(
    adapter: KubernetesNamespaceQuotaLiveAdapter,
    target: StorageTarget,
    namespace_name: str,
    pvc_name: str,
) -> dict:
    deadline = time.monotonic() + 180
    last_payload: dict | None = None
    while time.monotonic() < deadline:
        completed = adapter._kubectl(  # noqa: SLF001
            target.cluster_name,
            ["-n", namespace_name, "get", "pvc", pvc_name, "-o", "json"],
            check=False,
        )
        if completed.returncode == 0:
            last_payload = json.loads(completed.stdout)
            if last_payload.get("status", {}).get("phase") == "Bound":
                return last_payload
        time.sleep(5)
    raise AssertionError(f"PVC {namespace_name}/{pvc_name} did not become Bound: {last_payload}")


def wait_resource_quota_used(
    adapter: KubernetesNamespaceQuotaLiveAdapter,
    target: StorageTarget,
    namespace_name: str,
    expected: dict[str, str],
) -> dict[str, str]:
    deadline = time.monotonic() + 90
    used: dict[str, str] = {}
    while time.monotonic() < deadline:
        quota = read_resource_quota(adapter, target, namespace_name)
        used = quota.get("status", {}).get("used") or {}
        if all(str(used.get(key)) == value for key, value in expected.items()):
            return used
        time.sleep(3)
    raise AssertionError(f"ResourceQuota used did not match {expected!r}: {used!r}")


def pvc_manifest(target: StorageTarget, name: str, storage_request: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": name,
            "labels": {"app.kubernetes.io/managed-by": "dms-phase6-verification"},
        },
        "spec": {
            "accessModes": target.access_modes,
            "storageClassName": target.storage_class_name,
            "resources": {"requests": {"storage": storage_request}},
        },
    }


def patch_resource_quota_hard(
    adapter: KubernetesNamespaceQuotaLiveAdapter,
    target: StorageTarget,
    namespace_name: str,
    hard: dict[str, str],
) -> None:
    adapter._kubectl(  # noqa: SLF001
        target.cluster_name,
        [
            "-n",
            namespace_name,
            "patch",
            "resourcequota",
            "dms-storage-quota",
            "--type=merge",
            "-p",
            json.dumps({"spec": {"hard": hard}}, sort_keys=True),
        ],
    )


def create_non_dms_quota(
    adapter: KubernetesNamespaceQuotaLiveAdapter,
    target: StorageTarget,
    namespace_name: str,
    hard: dict[str, str],
) -> None:
    manifest = {
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": {"name": "phase6-non-dms-quota", "namespace": namespace_name},
        "spec": {"hard": hard},
    }
    adapter._kubectl(  # noqa: SLF001
        target.cluster_name,
        ["-n", namespace_name, "apply", "-f", "-"],
        input_text=json.dumps(manifest, sort_keys=True),
    )


def delete_namespace(
    adapter: KubernetesNamespaceQuotaLiveAdapter,
    target: StorageTarget,
    namespace_name: str,
) -> None:
    adapter._kubectl(  # noqa: SLF001
        target.cluster_name,
        ["delete", "namespace", namespace_name, "--ignore-not-found", "--wait=false"],
        check=False,
    )


def sc_key(target: StorageTarget) -> str:
    return f"{target.storage_class_name}.storageclass.storage.k8s.io/requests.storage"


def sc_pvc_key(target: StorageTarget) -> str:
    return f"{target.storage_class_name}.storageclass.storage.k8s.io/persistentvolumeclaims"


def resource_for_request(repository, request_id: str) -> dict:
    request = repository.get_request(request_id)
    resource = repository.get_resource(request["resource_kind"], request["resource_key"])
    if not resource:
        raise AssertionError(f"resource not found for request {request_id}")
    return resource


def assert_true(condition: object, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def assert_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def mask_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.password:
        netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    return value


if __name__ == "__main__":
    raise SystemExit(main())
