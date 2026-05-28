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
from dms.domain import LifecycleState, OperationKind
from dms.planner import Planner
from dms.workers import RMWorkerRuntime


API_HEADERS = {"x-dms-actor": "api-client"}


@dataclass(frozen=True)
class Target:
    name: str
    cluster_name: str
    control_host: str
    storage_name: str
    storage_class_name: str
    provisioner: str
    namespace_prefix: str
    access_modes: list[str]
    preferred_nodes: tuple[str, ...]


TARGETS = (
    Target(
        name="cephfs",
        cluster_name="cluster-a",
        control_host="c1-control",
        storage_name="cephfs-a",
        storage_class_name="testbed-cephfs",
        provisioner="rook-ceph.cephfs.csi.ceph.com",
        namespace_prefix="dms-phase5-cephfs",
        access_modes=["ReadWriteMany"],
        preferred_nodes=("c1-worker", "c1-control"),
    ),
    Target(
        name="longhorn",
        cluster_name="cluster-b",
        control_host="c2-control",
        storage_name="longhorn-b",
        storage_class_name="testbed-longhorn",
        provisioner="driver.longhorn.io",
        namespace_prefix="dms-phase5-longhorn",
        access_modes=["ReadWriteOnce"],
        preferred_nodes=("c2-worker", "c2-control"),
    ),
)


def main() -> int:
    settings = Settings.from_env()
    assert_true(
        settings.database_url.startswith("postgresql://"),
        "Phase 5 live verification must use operational PostgreSQL",
    )
    assert_true(settings.observability_is_separate, "observability DB must be separate")

    app = create_app(settings)
    services = app.state.services
    client = TestClient(app)
    live_adapter = KubernetesNamespaceQuotaLiveAdapter.from_settings(settings)
    token = uuid4().hex[:8]

    inventory = get_inventory(client)
    c1_node = select_node(inventory, "cluster-a", preferred=("c1-worker", "c1-control"))
    c2_node = select_node(inventory, "cluster-b", preferred=("c2-worker", "c2-control"))
    submit_phase5_reports(client, c1_node, c2_node)

    target_summaries = []
    for target in TARGETS:
        target_summaries.append(
            verify_target(
                target=target,
                token=token,
                client=client,
                services=services,
                live_adapter=live_adapter,
                inventory=inventory,
                settings=settings,
            )
        )

    summary = {
        "status": "ok",
        "operational_database_url": mask_url(settings.database_url),
        "observability_database_url": mask_url(settings.observability_database_url),
        "targets": target_summaries,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def verify_target(
    *,
    target: Target,
    token: str,
    client: TestClient,
    services,
    live_adapter: KubernetesNamespaceQuotaLiveAdapter,
    inventory: dict,
    settings: Settings,
) -> dict:
    namespace_name = f"{target.namespace_prefix}-{token}"
    assert_storage_class(
        inventory, target.cluster_name, target.storage_class_name, target.provisioner
    )
    mapping = upsert_mapping(client, target)
    assert_equal(mapping["status"], "Ready", f"{target.name} mapping ready")

    rm_worker = RMWorkerRuntime(
        repository=services.repository,
        observability=services.observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=live_adapter,
        worker_id=f"phase5-rm-{target.name}",
        lease_seconds=settings.worker_lease_seconds,
    )

    create_id = submit_quota_request(
        client,
        "POST",
        "/api/v1/resource-management/kubernetes/namespace-quotas",
        requester_id=f"portal:phase5:{target.name}",
        payload={
            "cluster_name": target.cluster_name,
            "namespace_name": namespace_name,
            "allow_namespace_create": True,
            "resource_type": "user",
            "quota": {"requests_storage_bytes": 128 * 1024**2, "pvc_count": 2},
            "storage_class_quotas": [{"storage_name": target.storage_name}],
        },
    )
    run_planner_worker(services.repository, rm_worker, create_id, "create")
    created_quota = read_resource_quota(live_adapter, target, namespace_name)
    assert_hard(
        created_quota["spec"]["hard"],
        target,
        storage="128Mi",
        pvc_count="2",
        label=f"{target.name} create hard",
    )

    allowed = apply_pvc(live_adapter, target, namespace_name, "phase5-allowed-64mi", "64Mi")
    assert_equal(allowed["status"].get("phase"), "Bound", f"{target.name} 64Mi PVC Bound")

    update_id = submit_quota_request(
        client,
        "PATCH",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/{target.cluster_name}/{namespace_name}",
        requester_id=f"portal:phase5:{target.name}",
        payload={
            "resource_type": "user",
            "quota": {"requests_storage_bytes": 256 * 1024**2, "pvc_count": 4},
            "storage_class_quotas": [{"storage_name": target.storage_name}],
            "memo": "phase5 quota increase",
        },
    )
    run_planner_worker(services.repository, rm_worker, update_id, "update")
    updated_quota = read_resource_quota(live_adapter, target, namespace_name)
    assert_hard(
        updated_quota["spec"]["hard"],
        target,
        storage="256Mi",
        pvc_count="4",
        label=f"{target.name} update hard",
    )

    larger = apply_pvc(live_adapter, target, namespace_name, "phase5-larger-192mi", "192Mi")
    assert_equal(larger["status"].get("phase"), "Bound", f"{target.name} 192Mi PVC Bound")

    sync_before_decrease_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/{target.cluster_name}/{namespace_name}:sync",
        requester_id=f"portal:phase5:{target.name}",
        payload={"accept_live_state": True, "reason": "refresh used before decrease guard"},
    )
    run_planner_worker(
        services.repository, rm_worker, sync_before_decrease_id, "sync before decrease"
    )

    decrease_id = submit_quota_request(
        client,
        "PATCH",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/{target.cluster_name}/{namespace_name}",
        requester_id=f"portal:phase5:{target.name}",
        payload={
            "quota": {"requests_storage_bytes": 32 * 1024**2, "pvc_count": 4},
            "storage_class_quotas": [{"storage_name": target.storage_name}],
        },
    )
    assert_equal(Planner(services.repository).run_once(), 1, f"{target.name} decrease planner")
    assert_true(
        services.repository.get_plan_by_request(decrease_id) is None,
        f"{target.name} decrease guard created no plan",
    )
    [decrease_result] = services.repository.get_results(decrease_id)
    assert_equal(
        decrease_result["terminal_status"],
        LifecycleState.REJECTED.value,
        f"{target.name} decrease rejected",
    )
    quota_after_decrease_reject = read_resource_quota(live_adapter, target, namespace_name)
    assert_hard(
        quota_after_decrease_reject["spec"]["hard"],
        target,
        storage="256Mi",
        pvc_count="4",
        label=f"{target.name} decrease reject no side effect",
    )

    block_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/{target.cluster_name}/{namespace_name}:block",
        requester_id=f"portal:phase5:{target.name}",
        payload={"block": True, "block_mode": "quota-zero", "reason": "phase5 block"},
    )
    run_planner_worker(services.repository, rm_worker, block_id, "block")
    blocked_quota = read_resource_quota(live_adapter, target, namespace_name)
    assert_hard(
        blocked_quota["spec"]["hard"],
        target,
        storage="0",
        pvc_count="0",
        label=f"{target.name} block hard",
    )
    blocked_pvc = apply_rejected_pvc(
        live_adapter, target, namespace_name, "phase5-blocked-1mi", "1Mi"
    )
    assert_true(blocked_pvc["rejected"], f"{target.name} blocked PVC rejected")

    unblock_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/{target.cluster_name}/{namespace_name}:block",
        requester_id=f"portal:phase5:{target.name}",
        payload={"block": False, "reason": "phase5 unblock"},
    )
    run_planner_worker(services.repository, rm_worker, unblock_id, "unblock")
    unblocked_quota = read_resource_quota(live_adapter, target, namespace_name)
    assert_hard(
        unblocked_quota["spec"]["hard"],
        target,
        storage="256Mi",
        pvc_count="4",
        label=f"{target.name} unblock hard",
    )

    drift_hard = {
        "requests.storage": "384Mi",
        "persistentvolumeclaims": "5",
        storage_class_key(target): "384Mi",
    }
    patch_resource_quota_hard(live_adapter, target, namespace_name, drift_hard)
    check_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/{target.cluster_name}/{namespace_name}:check",
        requester_id=f"portal:phase5:{target.name}",
        payload={"scope": "single", "include_live_resourcequota": True},
    )
    run_planner_worker(services.repository, rm_worker, check_id, "check")
    [check_result] = services.repository.get_results(check_id)
    assert_equal(
        check_result["verification_summary"]["consistency_status"],
        "Drifted",
        f"{target.name} drift detected",
    )

    sync_after_drift_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/{target.cluster_name}/{namespace_name}:sync",
        requester_id=f"portal:phase5:{target.name}",
        payload={"accept_live_state": True, "reason": "accept drifted live state"},
    )
    run_planner_worker(services.repository, rm_worker, sync_after_drift_id, "sync after drift")
    synced_resource = resource_for_request(services.repository, sync_after_drift_id)
    assert_equal(
        synced_resource["desired_state"]["resource_quota_hard"],
        drift_hard,
        f"{target.name} sync desired state",
    )

    create_non_dms_quota(live_adapter, target, namespace_name)
    delete_id = submit_quota_request(
        client,
        "DELETE",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/{target.cluster_name}/{namespace_name}",
        requester_id=f"portal:phase5:{target.name}",
        payload={"reason": "phase5 delete dms quota only"},
    )
    run_planner_worker(services.repository, rm_worker, delete_id, "delete")
    deleted_quota = read_resource_quota(
        live_adapter, target, namespace_name, check=False, name="dms-storage-quota"
    )
    assert_true(not deleted_quota["exists"], f"{target.name} dms-storage-quota deleted")
    non_dms = read_resource_quota(
        live_adapter, target, namespace_name, check=False, name="phase5-non-dms-quota"
    )
    assert_true(non_dms["exists"], f"{target.name} non-DMS quota preserved")

    cleanup = os.getenv("DMS_PHASE5_CLEANUP", "true").lower() != "false"
    if cleanup:
        live_adapter._kubectl(  # noqa: SLF001
            target.cluster_name,
            ["delete", "namespace", namespace_name, "--ignore-not-found", "--wait=false"],
            check=False,
        )

    events = services.observability.list_events(limit=200)
    target_events = [
        event["event_type"]
        for event in events
        if event["payload"].get("namespace_name") == namespace_name
        or namespace_name in json.dumps(event["payload"], sort_keys=True)
    ]
    resource = resource_for_request(services.repository, delete_id)
    return {
        "target": target.name,
        "cluster_name": target.cluster_name,
        "namespace_name": namespace_name,
        "storage_name": target.storage_name,
        "storage_class_name": target.storage_class_name,
        "provisioner": target.provisioner,
        "create_request_id": create_id,
        "update_request_id": update_id,
        "block_request_id": block_id,
        "unblock_request_id": unblock_id,
        "check_request_id": check_id,
        "sync_request_id": sync_after_drift_id,
        "delete_request_id": delete_id,
        "decrease_guard_status": decrease_result["terminal_status"],
        "blocked_pvc_rejected": blocked_pvc["rejected"],
        "drift_check_status": check_result["verification_summary"]["consistency_status"],
        "synced_resource_quota_hard": synced_resource["desired_state"]["resource_quota_hard"],
        "delete_resource_status": resource["status"],
        "non_dms_quota_preserved": non_dms["exists"],
        "cleanup_namespace_requested": cleanup,
        "observability_event_types": target_events,
    }


def submit_phase5_reports(client: TestClient, c1_node: dict, c2_node: dict) -> None:
    for target, node in ((TARGETS[0], c1_node), (TARGETS[1], c2_node)):
        submit_report(
            client,
            agent_report(
                cluster_name=target.cluster_name,
                node_name=node["name"],
                node_uid=node["uid"],
                worker_role="RM",
                mounts=[mount(target.storage_name)],
                csi=[csi(target.provisioner, target.storage_class_name)],
            ),
        )
    for target in TARGETS:
        submit_report(
            client,
            agent_report(
                cluster_name="cluster-a",
                node_name=c1_node["name"],
                node_uid=c1_node["uid"],
                worker_role="DM",
                mounts=[mount(target.storage_name)],
                csi=[csi(target.provisioner, target.storage_class_name)],
                tools=[{"name": "dscan", "version": "testbed", "healthy": True}],
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
    tools: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": "phase5.v1",
        "reported_at": None,
        "cluster_name": cluster_name,
        "node_name": node_name,
        "node_uid": node_uid,
        "worker_role": worker_role,
        "mounts": mounts,
        "csi": csi,
        "tools": tools or [],
        "credentials": [{"name": "testbed", "healthy": True}],
        "networks": [{"name": "storage-net", "reachable": True}],
        "identity_evidence": {"source": "phase5-live"},
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


def upsert_mapping(client: TestClient, target: Target) -> dict:
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
    target: Target,
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
    target: Target,
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
    target: Target,
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
    target: Target,
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


def pvc_manifest(target: Target, name: str, storage_request: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": name,
            "labels": {"app.kubernetes.io/managed-by": "dms-phase5-verification"},
        },
        "spec": {
            "accessModes": target.access_modes,
            "storageClassName": target.storage_class_name,
            "resources": {"requests": {"storage": storage_request}},
        },
    }


def patch_resource_quota_hard(
    adapter: KubernetesNamespaceQuotaLiveAdapter,
    target: Target,
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
    adapter: KubernetesNamespaceQuotaLiveAdapter, target: Target, namespace_name: str
) -> None:
    manifest = {
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": {"name": "phase5-non-dms-quota", "namespace": namespace_name},
        "spec": {"hard": {"configmaps": "100"}},
    }
    adapter._kubectl(  # noqa: SLF001
        target.cluster_name,
        ["-n", namespace_name, "apply", "-f", "-"],
        input_text=json.dumps(manifest, sort_keys=True),
    )


def assert_hard(
    hard: dict[str, str],
    target: Target,
    *,
    storage: str,
    pvc_count: str,
    label: str,
) -> None:
    assert_equal(hard.get("requests.storage"), storage, f"{label} requests.storage")
    assert_equal(hard.get("persistentvolumeclaims"), pvc_count, f"{label} pvc count")
    assert_equal(hard.get(storage_class_key(target)), storage, f"{label} sc storage")


def storage_class_key(target: Target) -> str:
    return f"{target.storage_class_name}.storageclass.storage.k8s.io/requests.storage"


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
