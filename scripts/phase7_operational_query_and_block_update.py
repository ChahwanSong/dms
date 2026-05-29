from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dms.adapters import KubernetesNamespaceQuotaLiveAdapter  # noqa: E402
from dms.api import create_app  # noqa: E402
from dms.config import Settings  # noqa: E402
from dms.domain import LifecycleState  # noqa: E402
from dms.planner import Planner  # noqa: E402
from scripts.phase6_kubernetes_multi_storage_quota import (  # noqa: E402
    API_HEADERS,
    CEPHFS,
    LONGHORN,
    LONGHORN_STATIC,
    StorageTarget,
    apply_pvc,
    apply_rejected_pvc,
    assert_equal,
    assert_storage_class,
    assert_true,
    create_non_dms_quota,
    delete_namespace,
    get_inventory,
    mask_url,
    patch_resource_quota_hard,
    read_resource_quota,
    resource_for_request,
    rm_worker_for,
    run_planner_worker,
    sc_key,
    sc_pvc_key,
    select_node,
    submit_phase6_reports,
    submit_quota_request,
    upsert_mapping,
    wait_resource_quota_used,
)


def main() -> int:
    settings = Settings.from_env()
    assert_true(
        settings.database_url.startswith("postgresql://"),
        "Phase 7 live verification must use operational PostgreSQL",
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

    request_query = verify_requester_query(client, services.repository, token)
    longhorn = verify_longhorn_query_and_block_update(
        token=token,
        client=client,
        services=services,
        live_adapter=live_adapter,
        inventory=inventory,
        settings=settings,
    )
    cephfs = verify_cephfs_quota_query(
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
        "request_query": request_query,
        "targets": [longhorn, cephfs],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def verify_requester_query(client: TestClient, repository, token: str) -> dict:
    created: list[str] = []
    for index in range(3):
        phase7_a = repository.create_request(
            requester_id="portal:phase7-a",
            actor="api-client",
            operation="phase7.synthetic",
            resource_kind="filesystem",
            resource_key=f"phase7-a:{token}:{index}",
            payload={"token": token, "index": index},
        )
        phase7_b = repository.create_request(
            requester_id="portal:phase7-b",
            actor="api-client",
            operation="phase7.synthetic",
            resource_kind="filesystem",
            resource_key=f"phase7-b:{token}:{index}",
            payload={"token": token, "index": index},
        )
        created.append(phase7_a)
        for request_id in (phase7_a, phase7_b):
            repository.complete_result(
                request_id=request_id,
                plan_id=None,
                run_id=None,
                terminal_status=LifecycleState.SUCCEEDED,
                message="phase7 requester query seed",
                verification_summary={"backend_side_effect": False},
                actor="phase7-script",
            )

    missing = client.get("/api/v1/operations/requests", headers=API_HEADERS)
    assert_equal(missing.status_code, 422, "requester_id required")
    response = client.get(
        "/api/v1/operations/requests?requester_id=portal:phase7-a&limit=2",
        headers=API_HEADERS,
    )
    assert_equal(response.status_code, 200, "requester query")
    requests = response.json()
    assert_equal(len(requests), 2, "requester query limit")
    assert_equal(
        [request["requester_id"] for request in requests],
        ["portal:phase7-a", "portal:phase7-a"],
        "requester query filter",
    )
    assert_equal(
        [request["resource_key"] for request in requests],
        [f"phase7-a:{token}:2", f"phase7-a:{token}:1"],
        "requester query order",
    )
    return {
        "requester_id": "portal:phase7-a",
        "created_request_ids": created,
        "limited_resource_keys": [request["resource_key"] for request in requests],
        "missing_requester_status": missing.status_code,
    }


def verify_longhorn_query_and_block_update(
    *,
    token: str,
    client: TestClient,
    services,
    live_adapter: KubernetesNamespaceQuotaLiveAdapter,
    inventory: dict,
    settings: Settings,
) -> dict:
    namespace_name = f"dms-phase7-longhorn-{token}"
    for target in (LONGHORN, LONGHORN_STATIC):
        assert_storage_class(
            inventory, target.cluster_name, target.storage_class_name, target.provisioner
        )
        mapping = upsert_mapping(client, target)
        assert_equal(mapping["status"], "Ready", f"{target.name} mapping ready")

    rm_worker = rm_worker_for(services, live_adapter, settings, "phase7-rm-longhorn")
    create_hard = {
        "requests.storage": "512Mi",
        "persistentvolumeclaims": "6",
        sc_key(LONGHORN): "256Mi",
        sc_pvc_key(LONGHORN): "3",
        sc_key(LONGHORN_STATIC): "128Mi",
        sc_pvc_key(LONGHORN_STATIC): "2",
    }
    updated_hard = {
        "requests.storage": "768Mi",
        "persistentvolumeclaims": "8",
        sc_key(LONGHORN): "384Mi",
        sc_pvc_key(LONGHORN): "4",
        sc_key(LONGHORN_STATIC): "256Mi",
        sc_pvc_key(LONGHORN_STATIC): "4",
    }
    blocked_update_hard = {
        "requests.storage": "1Gi",
        "persistentvolumeclaims": "10",
        sc_key(LONGHORN): "512Mi",
        sc_pvc_key(LONGHORN): "5",
        sc_key(LONGHORN_STATIC): "384Mi",
        sc_pvc_key(LONGHORN_STATIC): "5",
    }

    create_id = submit_quota_request(
        client,
        "POST",
        "/api/v1/resource-management/kubernetes/namespace-quotas",
        requester_id="portal:phase7-a",
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
    run_planner_worker(services.repository, rm_worker, create_id, "phase7 multi create")
    assert_equal(
        read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
        create_hard,
        "phase7 create hard",
    )

    apply_pvc(live_adapter, LONGHORN, namespace_name, "phase7-lh-64mi", "64Mi")
    apply_pvc(live_adapter, LONGHORN_STATIC, namespace_name, "phase7-static-64mi", "64Mi")
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

    sync_used_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}:sync",
        requester_id="portal:phase7-a",
        payload={"accept_live_state": True, "reason": "phase7 refresh used"},
    )
    run_planner_worker(services.repository, rm_worker, sync_used_id, "phase7 sync used")

    query = quota_query(client, "cluster-b", namespace_name, include_non_dms=True)
    assert_equal(query["diff"]["status"], "Consistent", "phase7 quota query consistent")
    assert_true(query["live"]["usage_summary"], "phase7 quota query usage summary")

    update_id = submit_quota_request(
        client,
        "PATCH",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}",
        requester_id="portal:phase7-a",
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
        },
    )
    run_planner_worker(services.repository, rm_worker, update_id, "phase7 multi update")
    assert_equal(
        read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
        updated_hard,
        "phase7 update hard",
    )

    block_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}:block",
        requester_id="portal:phase7-a",
        payload={"block": True, "block_mode": "quota-zero", "reason": "phase7 block"},
    )
    run_planner_worker(services.repository, rm_worker, block_id, "phase7 block")
    assert_equal(
        read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
        {key: "0" for key in updated_hard},
        "phase7 block hard zero",
    )

    blocked_update_id = submit_quota_request(
        client,
        "PATCH",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}",
        requester_id="portal:phase7-a",
        payload={
            "quota": {"requests_storage_bytes": 1024**3, "pvc_count": 10},
            "storage_class_quotas": [
                {
                    "storage_name": LONGHORN.storage_name,
                    "requests_storage_bytes": 512 * 1024**2,
                    "pvc_count": 5,
                },
                {
                    "storage_name": LONGHORN_STATIC.storage_name,
                    "requests_storage_bytes": 384 * 1024**2,
                    "pvc_count": 5,
                },
            ],
        },
    )
    run_planner_worker(
        services.repository, rm_worker, blocked_update_id, "phase7 blocked update"
    )
    assert_equal(
        read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
        {key: "0" for key in blocked_update_hard},
        "phase7 blocked update keeps live hard zero",
    )
    blocked_query = quota_query(client, "cluster-b", namespace_name)
    assert_equal(
        blocked_query["db"]["desired_state"]["block_state"]["restore_hard"],
        blocked_update_hard,
        "phase7 blocked update restore target",
    )

    blocked_decrease_id = submit_quota_request(
        client,
        "PATCH",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}",
        requester_id="portal:phase7-a",
        payload={
            "quota": {"requests_storage_bytes": 512 * 1024**2, "pvc_count": 10},
            "storage_class_quotas": [
                {
                    "storage_name": LONGHORN.storage_name,
                    "requests_storage_bytes": 512 * 1024**2,
                    "pvc_count": 5,
                },
                {
                    "storage_name": LONGHORN_STATIC.storage_name,
                    "requests_storage_bytes": 32 * 1024**2,
                    "pvc_count": 5,
                },
            ],
        },
    )
    assert_equal(Planner(services.repository).run_once(), 1, "phase7 blocked decrease planner")
    assert_true(
        services.repository.get_plan_by_request(blocked_decrease_id) is None,
        "phase7 blocked decrease no plan",
    )
    [decrease_result] = services.repository.get_results(blocked_decrease_id)
    assert_equal(
        decrease_result["terminal_status"],
        LifecycleState.REJECTED.value,
        "phase7 blocked decrease rejected",
    )

    blocked_pvc = apply_rejected_pvc(
        live_adapter, LONGHORN, namespace_name, "phase7-lh-blocked-1mi", "1Mi"
    )
    assert_true(blocked_pvc["rejected"], "phase7 blocked PVC rejected")

    unblock_id = submit_quota_request(
        client,
        "POST",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}:block",
        requester_id="portal:phase7-a",
        payload={"block": False, "reason": "phase7 unblock"},
    )
    run_planner_worker(services.repository, rm_worker, unblock_id, "phase7 unblock")
    assert_equal(
        read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
        blocked_update_hard,
        "phase7 unblock restores blocked update target",
    )

    drift_hard = {**blocked_update_hard, sc_key(LONGHORN): "640Mi"}
    patch_resource_quota_hard(live_adapter, LONGHORN, namespace_name, drift_hard)
    create_non_dms_quota(
        live_adapter,
        LONGHORN,
        namespace_name,
        {sc_key(LONGHORN): "128Mi"},
    )
    drift_query = quota_query(client, "cluster-b", namespace_name, include_non_dms=True)
    assert_equal(drift_query["diff"]["status"], "Drifted", "phase7 drift query")
    assert_true(
        any(
            warning.get("type") == "non_dms_quota_more_restrictive"
            for warning in drift_query["effective_quota_warnings"]
        ),
        "phase7 effective warning",
    )

    delete_id = submit_quota_request(
        client,
        "DELETE",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}",
        requester_id="portal:phase7-a",
        payload={"reason": "phase7 delete dms quota only"},
    )
    run_planner_worker(services.repository, rm_worker, delete_id, "phase7 delete")
    missing_query = quota_query(client, "cluster-b", namespace_name, include_non_dms=True)
    assert_equal(missing_query["diff"]["status"], "Missing", "phase7 missing query")
    non_dms = read_resource_quota(
        live_adapter, LONGHORN, namespace_name, check=False, name="phase6-non-dms-quota"
    )
    assert_true(non_dms["exists"], "phase7 non-DMS quota preserved")

    cleanup = os.getenv("DMS_PHASE7_CLEANUP", "true").lower() != "false"
    if cleanup:
        delete_namespace(live_adapter, LONGHORN, namespace_name)

    return {
        "target": "longhorn-query-blocked-update",
        "cluster_name": "cluster-b",
        "namespace_name": namespace_name,
        "create_request_id": create_id,
        "sync_request_id": sync_used_id,
        "update_request_id": update_id,
        "block_request_id": block_id,
        "blocked_update_request_id": blocked_update_id,
        "blocked_decrease_request_id": blocked_decrease_id,
        "blocked_decrease_status": decrease_result["terminal_status"],
        "unblock_request_id": unblock_id,
        "delete_request_id": delete_id,
        "initial_query_status": query["diff"]["status"],
        "blocked_query_restore_hard": blocked_query["db"]["desired_state"]["block_state"][
            "restore_hard"
        ],
        "unblocked_hard": blocked_update_hard,
        "drift_query_status": drift_query["diff"]["status"],
        "effective_warning_types": [
            warning["type"] for warning in drift_query["effective_quota_warnings"]
        ],
        "missing_query_status": missing_query["diff"]["status"],
        "non_dms_quota_preserved": non_dms["exists"],
        "cleanup_namespace_requested": cleanup,
    }


def verify_cephfs_quota_query(
    *,
    token: str,
    client: TestClient,
    services,
    live_adapter: KubernetesNamespaceQuotaLiveAdapter,
    inventory: dict,
    settings: Settings,
) -> dict:
    namespace_name = f"dms-phase7-cephfs-{token}"
    assert_storage_class(inventory, "cluster-a", CEPHFS.storage_class_name, CEPHFS.provisioner)
    mapping = upsert_mapping(client, CEPHFS)
    assert_equal(mapping["status"], "Ready", "phase7 cephfs mapping ready")
    rm_worker = rm_worker_for(services, live_adapter, settings, "phase7-rm-cephfs")

    create_id = submit_quota_request(
        client,
        "POST",
        "/api/v1/resource-management/kubernetes/namespace-quotas",
        requester_id="portal:phase7-a",
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
    run_planner_worker(services.repository, rm_worker, create_id, "phase7 cephfs create")
    query = quota_query(client, "cluster-a", namespace_name)
    assert_equal(query["diff"]["status"], "Consistent", "phase7 cephfs query")

    delete_id = submit_quota_request(
        client,
        "DELETE",
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/{namespace_name}",
        requester_id="portal:phase7-a",
        payload={"reason": "phase7 cephfs cleanup"},
    )
    run_planner_worker(services.repository, rm_worker, delete_id, "phase7 cephfs delete")
    cleanup = os.getenv("DMS_PHASE7_CLEANUP", "true").lower() != "false"
    if cleanup:
        delete_namespace(live_adapter, CEPHFS, namespace_name)

    return {
        "target": "cephfs-quota-query",
        "cluster_name": "cluster-a",
        "namespace_name": namespace_name,
        "create_request_id": create_id,
        "delete_request_id": delete_id,
        "query_status": query["diff"]["status"],
        "cleanup_namespace_requested": cleanup,
    }


def quota_query(
    client: TestClient,
    cluster_name: str,
    namespace_name: str,
    *,
    include_non_dms: bool = False,
) -> dict:
    response = client.get(
        "/api/v1/operations/kubernetes/namespace-quotas/"
        f"{cluster_name}/{namespace_name}"
        f"?include_non_dms={'true' if include_non_dms else 'false'}",
        headers=API_HEADERS,
    )
    assert_equal(response.status_code, 200, f"quota query {cluster_name}/{namespace_name}")
    return response.json()


if __name__ == "__main__":
    raise SystemExit(main())
