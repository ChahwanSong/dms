from __future__ import annotations

import json
import os
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi.testclient import TestClient

from dms.adapters import (
    KubernetesNamespaceQuotaLiveAdapter,
    StubFilesystemBackendAdapter,
)
from dms.api import create_app
from dms.config import Settings
from dms.domain import LifecycleState
from dms.planner import Planner
from dms.workers import RMWorkerRuntime


LONGHORN_DRIVER = "driver.longhorn.io"
LONGHORN_STORAGE_NAME = "longhorn-b"
LONGHORN_STORAGE_CLASS = "testbed-longhorn"
API_HEADERS = {"x-dms-actor": "api-client"}


def main() -> int:
    settings = Settings.from_env()
    assert_true(
        settings.database_url.startswith("postgresql://"),
        "Phase 4 live verification must use operational PostgreSQL",
    )
    assert_true(settings.observability_is_separate, "observability DB must be separate")

    app = create_app(settings)
    services = app.state.services
    client = TestClient(app)
    live_adapter = KubernetesNamespaceQuotaLiveAdapter.from_settings(settings)
    token = uuid4().hex[:8]
    namespace_name = f"dms-phase4-{token}"

    inventory = _get_inventory(client)
    assert_storage_class(inventory, "cluster-b", LONGHORN_STORAGE_CLASS, LONGHORN_DRIVER)
    c1_node = _select_node(inventory, "cluster-a", preferred=("c1-worker", "c1-control"))
    c2_node = _select_node(inventory, "cluster-b", preferred=("c2-worker", "c2-control"))

    submit_report(
        client,
        agent_report(
            cluster_name="cluster-a",
            node_name=c1_node["name"],
            node_uid=c1_node["uid"],
            worker_role="DM",
            mounts=[mount(LONGHORN_STORAGE_NAME)],
            csi=[csi(LONGHORN_DRIVER, LONGHORN_STORAGE_CLASS)],
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
            mounts=[mount(LONGHORN_STORAGE_NAME)],
            csi=[csi(LONGHORN_DRIVER, LONGHORN_STORAGE_CLASS)],
        ),
    )
    submit_report(
        client,
        agent_report(
            cluster_name="cluster-b",
            node_name=c2_node["name"],
            node_uid=c2_node["uid"],
            worker_role="DM",
            mounts=[mount(LONGHORN_STORAGE_NAME)],
            csi=[csi(LONGHORN_DRIVER, LONGHORN_STORAGE_CLASS)],
            tools=[{"name": "dscan", "version": "testbed", "healthy": True}],
        ),
    )

    mapping = upsert_mapping(client)
    assert_equal(mapping["status"], "Ready", "cluster-b Longhorn mapping ready")
    quota_response = client.post(
        "/api/v1/resource-management/kubernetes/namespace-quotas",
        json={
            "requester_id": "portal:phase4",
            "payload": {
                "cluster_name": "cluster-b",
                "namespace_name": namespace_name,
                "allow_namespace_create": True,
                "quota": {
                    "requests_storage_bytes": 128 * 1024**2,
                    "pvc_count": 2,
                },
                "storage_class_quotas": [{"storage_name": LONGHORN_STORAGE_NAME}],
            },
        },
        headers=API_HEADERS,
    )
    assert_equal(quota_response.status_code, 202, "namespace quota submit")
    request_id = quota_response.json()["request_id"]
    assert_equal(Planner(services.repository).run_once(), 1, "Phase 4 planner run")
    plan = services.repository.get_plan_by_request(request_id)
    assert_true(plan is not None, "Phase 4 plan created")

    rm_worker = RMWorkerRuntime(
        repository=services.repository,
        observability=services.observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=live_adapter,
        worker_id="phase4-rm-cluster-b",
        lease_seconds=settings.worker_lease_seconds,
    )
    assert_equal(rm_worker.run_once(), 1, "RM worker live ResourceQuota apply")
    assert_equal(
        services.repository.get_request(request_id)["status"],
        LifecycleState.SUCCEEDED.value,
        "namespace quota request succeeded",
    )

    resource_quota = read_resource_quota(live_adapter, namespace_name)
    assert_equal(
        resource_quota["spec"]["hard"]["requests.storage"],
        "128Mi",
        "ResourceQuota namespace-wide requests.storage",
    )
    assert_equal(
        resource_quota["spec"]["hard"]["persistentvolumeclaims"],
        "2",
        "ResourceQuota namespace-wide PVC count",
    )
    assert_equal(
        resource_quota["spec"]["hard"][
            f"{LONGHORN_STORAGE_CLASS}.storageclass.storage.k8s.io/requests.storage"
        ],
        "128Mi",
        "ResourceQuota StorageClass-specific requests.storage",
    )

    allowed_pvc = apply_allowed_pvc(live_adapter, namespace_name)
    quota_after_pvc = read_resource_quota(live_adapter, namespace_name)
    rejected = apply_over_quota_pvc(live_adapter, namespace_name)
    assert_true(rejected["rejected"], "over-quota PVC must be rejected")

    pvc_verification = {
        "allowed_pvc": {
            "name": "phase4-allowed-64mi",
            "request": "64Mi",
            "phase": allowed_pvc["status"].get("phase"),
        },
        "over_quota_pvc": rejected,
        "resource_quota_status_after_allowed_pvc": {
            "hard": quota_after_pvc.get("status", {}).get("hard") or {},
            "used": quota_after_pvc.get("status", {}).get("used") or {},
        },
    }
    persist_pvc_verification(services.repository, request_id, pvc_verification)
    services.observability.safe_record_event(
        component="phase4-verification",
        severity="INFO",
        event_type="pvc_admission_verification_completed",
        message="PVC admission verification completed",
        payload=pvc_verification,
        correlation_id=request_id,
    )

    cleanup = os.getenv("DMS_PHASE4_CLEANUP", "true").lower() != "false"
    if cleanup:
        live_adapter._kubectl(  # noqa: SLF001 - local verification script uses adapter command.
            "cluster-b",
            ["delete", "namespace", namespace_name, "--ignore-not-found", "--wait=false"],
            check=False,
        )

    [result] = services.repository.get_results(request_id)
    resource = resource_for_request(services.repository, request_id)
    events = services.observability.list_events(correlation_id=request_id, limit=20)
    summary = {
        "status": "ok",
        "operational_database_url": _mask_url(settings.database_url),
        "observability_database_url": _mask_url(settings.observability_database_url),
        "cluster_name": "cluster-b",
        "namespace_name": namespace_name,
        "storage_name": LONGHORN_STORAGE_NAME,
        "storage_class_name": LONGHORN_STORAGE_CLASS,
        "request_id": request_id,
        "plan_id": plan["plan_id"],
        "request_status": services.repository.get_request(request_id)["status"],
        "result_terminal_status": result["terminal_status"],
        "resource_status": resource["status"],
        "resource_quota_name": "dms-storage-quota",
        "resource_quota_spec_hard": resource_quota["spec"]["hard"],
        "resource_quota_status_hard_initial": resource_quota.get("status", {}).get(
            "hard", {}
        ),
        "resource_quota_status_used_after_allowed_pvc": quota_after_pvc.get(
            "status", {}
        ).get("used", {}),
        "pvc_admission_verification": pvc_verification,
        "observability_event_types": [event["event_type"] for event in events],
        "cleanup_namespace_requested": cleanup,
        "manual_recheck_commands": [
            f"ssh c2-control kubectl -n {namespace_name} get resourcequota "
            "dms-storage-quota -o yaml",
            f"ssh c2-control kubectl -n {namespace_name} get pvc",
        ],
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


def _select_node(
    inventory: dict, cluster_name: str, *, preferred: tuple[str, ...]
) -> dict[str, str]:
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
        "schema_version": "phase4.v1",
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
        "identity_evidence": {"source": "phase4-live"},
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


def upsert_mapping(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/resource-management/storage-mappings",
        json={
            "storage_name": LONGHORN_STORAGE_NAME,
            "backend_template": {
                "backend_type": "longhorn",
                "csi_driver": LONGHORN_DRIVER,
            },
            "cluster_name": "cluster-b",
            "storage_class_name": LONGHORN_STORAGE_CLASS,
        },
        headers=API_HEADERS,
    )
    assert_equal(response.status_code, 200, "Longhorn mapping upsert")
    return response.json()


def read_resource_quota(
    adapter: KubernetesNamespaceQuotaLiveAdapter, namespace_name: str
) -> dict:
    completed = adapter._kubectl(  # noqa: SLF001 - local verification script uses adapter command.
        "cluster-b",
        ["-n", namespace_name, "get", "resourcequota", "dms-storage-quota", "-o", "json"],
    )
    return json.loads(completed.stdout)


def apply_allowed_pvc(
    adapter: KubernetesNamespaceQuotaLiveAdapter, namespace_name: str
) -> dict:
    manifest = pvc_manifest("phase4-allowed-64mi", "64Mi")
    adapter._kubectl(  # noqa: SLF001 - local verification script uses adapter command.
        "cluster-b",
        ["-n", namespace_name, "apply", "-f", "-"],
        input_text=json.dumps(manifest, sort_keys=True),
    )
    return wait_pvc_bound(adapter, namespace_name, "phase4-allowed-64mi")


def apply_over_quota_pvc(
    adapter: KubernetesNamespaceQuotaLiveAdapter, namespace_name: str
) -> dict:
    manifest = pvc_manifest("phase4-over-quota-96mi", "96Mi")
    completed = adapter._kubectl(  # noqa: SLF001 - local verification script uses adapter command.
        "cluster-b",
        ["-n", namespace_name, "apply", "-f", "-"],
        input_text=json.dumps(manifest, sort_keys=True),
        check=False,
    )
    stderr = completed.stderr.strip()
    return {
        "name": "phase4-over-quota-96mi",
        "request": "96Mi",
        "returncode": completed.returncode,
        "rejected": completed.returncode != 0 and "exceeded quota" in stderr.lower(),
        "stderr": stderr,
    }


def wait_pvc_bound(
    adapter: KubernetesNamespaceQuotaLiveAdapter, namespace_name: str, pvc_name: str
) -> dict:
    deadline = time.monotonic() + 180
    last_payload: dict | None = None
    while time.monotonic() < deadline:
        completed = adapter._kubectl(  # noqa: SLF001
            "cluster-b",
            ["-n", namespace_name, "get", "pvc", pvc_name, "-o", "json"],
            check=False,
        )
        if completed.returncode == 0:
            last_payload = json.loads(completed.stdout)
            if last_payload.get("status", {}).get("phase") == "Bound":
                return last_payload
        time.sleep(5)
    raise AssertionError(f"PVC {namespace_name}/{pvc_name} did not become Bound: {last_payload}")


def pvc_manifest(name: str, storage_request: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": name,
            "labels": {"app.kubernetes.io/managed-by": "dms-phase4-verification"},
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": LONGHORN_STORAGE_CLASS,
            "resources": {"requests": {"storage": storage_request}},
        },
    }


def persist_pvc_verification(
    repository, request_id: str, pvc_verification: dict[str, object]
) -> None:
    resource = resource_for_request(repository, request_id)
    observed = dict(resource["observed_state"])
    observed["pvc_admission_verification"] = pvc_verification
    repository.upsert_resource(
        resource_kind=resource["resource_kind"],
        resource_key=resource["resource_key"],
        desired_state=resource["desired_state"],
        applied_state=resource["applied_state"],
        observed_state=observed,
        status=resource["status"],
    )


def resource_for_request(repository, request_id: str) -> dict:
    request = repository.get_request(request_id)
    for resource in repository.list_resources(limit=100):
        if (
            resource["resource_kind"] == request["resource_kind"]
            and resource["resource_key"] == request["resource_key"]
        ):
            return resource
    raise AssertionError(f"resource not found for request {request_id}")


def assert_true(condition: object, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def assert_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def _mask_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.password:
        netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    return value


if __name__ == "__main__":
    raise SystemExit(main())
