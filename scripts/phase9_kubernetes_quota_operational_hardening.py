from __future__ import annotations

import json
from pathlib import Path
import sys
from uuid import uuid4

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dms.adapters import KubernetesNamespaceQuotaLiveAdapter  # noqa: E402
from dms.api import create_app  # noqa: E402
from dms.config import Settings  # noqa: E402
from scripts.phase6_kubernetes_multi_storage_quota import (  # noqa: E402
    API_HEADERS,
    CEPHFS,
    LONGHORN,
    LONGHORN_STATIC,
    assert_equal,
    assert_storage_class,
    assert_true,
    create_non_dms_quota,
    delete_namespace,
    get_inventory,
    mask_url,
    patch_resource_quota_hard,
    read_resource_quota,
    rm_worker_for,
    run_planner_worker,
    sc_key,
    sc_pvc_key,
    submit_quota_request,
    upsert_mapping,
    apply_pvc,
    wait_resource_quota_used,
)
from scripts.phase8_agent_daemonset_live import wait_for_phase8_reports  # noqa: E402


def main() -> int:
    settings = Settings.from_env()
    assert_true(
        settings.database_url.startswith("postgresql://"),
        "Phase 9 live verification must use operational PostgreSQL",
    )
    assert_true(settings.observability_is_separate, "observability DB must be separate")

    app = create_app(settings)
    services = app.state.services
    client = TestClient(app)
    live_adapter = KubernetesNamespaceQuotaLiveAdapter.from_settings(settings)
    token = uuid4().hex[:8]

    reports = wait_for_phase8_reports(client)
    inventory = get_inventory(client)
    for target in (CEPHFS, LONGHORN, LONGHORN_STATIC):
        assert_storage_class(
            inventory, target.cluster_name, target.storage_class_name, target.provisioner
        )
        mapping = upsert_mapping(client, target)
        readiness = mapping["mapping"]["readiness"]
        assert_equal(
            readiness["resource_management"],
            "Ready",
            f"{target.storage_name} RM readiness from real Agent",
        )

    rm_worker = rm_worker_for(services, live_adapter, settings, f"phase9-rm-{token}")
    longhorn_summary = verify_longhorn_phase9(
        token=token,
        client=client,
        services=services,
        rm_worker=rm_worker,
        live_adapter=live_adapter,
    )
    cephfs_summary = verify_cephfs_phase9(
        token=token,
        client=client,
        services=services,
        rm_worker=rm_worker,
        live_adapter=live_adapter,
    )

    summary = {
        "status": "ok",
        "operational_database_url": mask_url(settings.database_url),
        "observability_database_url": mask_url(settings.observability_database_url),
        "phase8_reports": reports,
        "targets": [longhorn_summary, cephfs_summary],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def verify_longhorn_phase9(
    *,
    token: str,
    client: TestClient,
    services,
    rm_worker,
    live_adapter: KubernetesNamespaceQuotaLiveAdapter,
) -> dict:
    namespace_name = f"dms-phase9-longhorn-{token}"
    custom_hard = {
        "requests.storage": "1Gi",
        "persistentvolumeclaims": "8",
        sc_key(LONGHORN): "512Mi",
        sc_pvc_key(LONGHORN): "4",
        sc_key(LONGHORN_STATIC): "256Mi",
        sc_pvc_key(LONGHORN_STATIC): "4",
    }
    default_hard = {
        "requests.storage": "2Gi",
        "persistentvolumeclaims": "12",
        sc_key(LONGHORN): "1Gi",
        sc_pvc_key(LONGHORN): "6",
        sc_key(LONGHORN_STATIC): "512Mi",
        sc_pvc_key(LONGHORN_STATIC): "6",
    }
    try:
        create_id = submit_quota_request(
            client,
            "POST",
            "/api/v1/resource-management/kubernetes/namespace-quotas",
            requester_id="portal:phase9:longhorn",
            payload={
                "cluster_name": "cluster-b",
                "namespace_name": namespace_name,
                "allow_namespace_create": True,
                "resource_type": "user",
                "quota": {"requests_storage_bytes": 1024**3, "pvc_count": 8},
                "storage_class_quotas": [
                    {
                        "storage_name": LONGHORN.storage_name,
                        "requests_storage_bytes": 512 * 1024**2,
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
        run_planner_worker(services.repository, rm_worker, create_id, "phase9 create")
        assert_equal(
            read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
            custom_hard,
            "phase9 create hard",
        )

        policy = upsert_default_policy(
            client,
            resource_type="user",
            quota={
                "requests_storage_bytes": 2 * 1024**3,
                "pvc_count": 12,
                "storage_class_quotas": [
                    {
                        "storage_name": LONGHORN.storage_name,
                        "requests_storage_bytes": 1024**3,
                        "pvc_count": 6,
                    },
                    {
                        "storage_name": LONGHORN_STATIC.storage_name,
                        "requests_storage_bytes": 512 * 1024**2,
                        "pvc_count": 6,
                    },
                ],
            },
        )
        reset_id = submit_quota_request(
            client,
            "PATCH",
            f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}",
            requester_id="portal:phase9:longhorn",
            payload={"reset_quota_to_default": True, "resource_type": "user"},
        )
        run_planner_worker(services.repository, rm_worker, reset_id, "phase9 reset")
        assert_equal(
            read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
            default_hard,
            "phase9 reset hard",
        )

        block_id = submit_quota_request(
            client,
            "POST",
            f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}:block",
            requester_id="portal:phase9:longhorn",
            payload={"block": True, "block_mode": "quota-zero", "reason": "phase9"},
        )
        run_planner_worker(services.repository, rm_worker, block_id, "phase9 block")
        blocked_hard = read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"][
            "hard"
        ]
        assert_equal(blocked_hard, {key: "0" for key in default_hard}, "phase9 block hard")
        blocked_reset_id = submit_quota_request(
            client,
            "PATCH",
            f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}",
            requester_id="portal:phase9:longhorn",
            payload={"reset_quota_to_default": True, "resource_type": "user"},
        )
        run_planner_worker(
            services.repository, rm_worker, blocked_reset_id, "phase9 blocked reset"
        )
        assert_equal(
            read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
            {key: "0" for key in default_hard},
            "phase9 blocked reset keeps zero",
        )
        unblock_id = submit_quota_request(
            client,
            "POST",
            f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}:block",
            requester_id="portal:phase9:longhorn",
            payload={"block": False, "reason": "phase9 restore"},
        )
        run_planner_worker(services.repository, rm_worker, unblock_id, "phase9 unblock")
        assert_equal(
            read_resource_quota(live_adapter, LONGHORN, namespace_name)["spec"]["hard"],
            default_hard,
            "phase9 unblock restores default",
        )

        drift_hard = {**default_hard, sc_key(LONGHORN): "1536Mi"}
        patch_resource_quota_hard(live_adapter, LONGHORN, namespace_name, drift_hard)
        audit_id = audit_quota(
            client,
            namespace_name,
            include_non_dms=True,
            include_usage_pressure=True,
        )
        run_planner_worker(services.repository, rm_worker, audit_id, "phase9 drift audit")
        assert_action_required(
            client,
            resource_key=f"cluster-b:{namespace_name}",
            expected={"kubernetes_quota_drifted"},
        )

        reset_again_id = submit_quota_request(
            client,
            "PATCH",
            f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}",
            requester_id="portal:phase9:longhorn",
            payload={"reset_quota_to_default": True, "resource_type": "user"},
        )
        run_planner_worker(
            services.repository, rm_worker, reset_again_id, "phase9 reset drift repair"
        )
        clean_audit_id = audit_quota(client, namespace_name, include_non_dms=True)
        run_planner_worker(services.repository, rm_worker, clean_audit_id, "phase9 clean audit")
        assert_no_action_required(
            client,
            resource_key=f"cluster-b:{namespace_name}",
            forbidden={"kubernetes_quota_drifted"},
        )

        apply_pvc(live_adapter, LONGHORN, namespace_name, "phase9-lh-16mi", "16Mi")
        wait_resource_quota_used(
            live_adapter,
            LONGHORN,
            namespace_name,
            {sc_key(LONGHORN): "16Mi", sc_pvc_key(LONGHORN): "1"},
        )
        pressure_audit_id = audit_quota(
            client,
            namespace_name,
            include_usage_pressure=True,
            usage_thresholds={"warning_percent": 0.1, "critical_percent": 90},
        )
        run_planner_worker(
            services.repository, rm_worker, pressure_audit_id, "phase9 pressure audit"
        )
        assert_action_required(
            client,
            resource_key=f"cluster-b:{namespace_name}",
            expected={"quota_usage_warning"},
        )

        create_non_dms_quota(
            live_adapter, LONGHORN, namespace_name, {"requests.storage": "512Mi"}
        )
        effective_audit_id = audit_quota(client, namespace_name, include_non_dms=True)
        run_planner_worker(
            services.repository, rm_worker, effective_audit_id, "phase9 effective audit"
        )
        assert_action_required(
            client,
            resource_key=f"cluster-b:{namespace_name}",
            expected={"non_dms_quota_more_restrictive"},
        )

        live_adapter._kubectl(  # noqa: SLF001
            "cluster-b",
            [
                "-n",
                namespace_name,
                "annotate",
                "resourcequota",
                "dms-storage-quota",
                "dms.io/resource-key-",
                "--overwrite",
            ],
        )
        metadata_audit_id = audit_quota(client, namespace_name)
        run_planner_worker(
            services.repository, rm_worker, metadata_audit_id, "phase9 metadata audit"
        )
        assert_action_required(
            client,
            resource_key=f"cluster-b:{namespace_name}",
            expected={"kubernetes_quota_metadata_drift"},
        )

        return {
            "target": "longhorn-multi",
            "namespace": namespace_name,
            "default_policy_id": policy["policy_id"],
            "create_request_id": create_id,
            "reset_request_id": reset_id,
            "audit_request_ids": [audit_id, clean_audit_id, pressure_audit_id],
        }
    finally:
        delete_namespace(live_adapter, LONGHORN, namespace_name)


def verify_cephfs_phase9(
    *,
    token: str,
    client: TestClient,
    services,
    rm_worker,
    live_adapter: KubernetesNamespaceQuotaLiveAdapter,
) -> dict:
    namespace_name = f"dms-phase9-cephfs-{token}"
    default_hard = {
        "requests.storage": "256Mi",
        "persistentvolumeclaims": "4",
        sc_key(CEPHFS): "256Mi",
        sc_pvc_key(CEPHFS): "4",
    }
    try:
        create_id = submit_quota_request(
            client,
            "POST",
            "/api/v1/resource-management/kubernetes/namespace-quotas",
            requester_id="portal:phase9:cephfs",
            payload={
                "cluster_name": "cluster-a",
                "namespace_name": namespace_name,
                "allow_namespace_create": True,
                "resource_type": "ceph-user",
                "quota": {"requests_storage_bytes": 128 * 1024**2, "pvc_count": 2},
                "storage_class_quotas": [
                    {
                        "storage_name": CEPHFS.storage_name,
                        "requests_storage_bytes": 128 * 1024**2,
                        "pvc_count": 2,
                    }
                ],
            },
        )
        run_planner_worker(services.repository, rm_worker, create_id, "phase9 cephfs create")
        policy = upsert_default_policy(
            client,
            resource_type="ceph-user",
            quota={
                "requests_storage_bytes": 256 * 1024**2,
                "pvc_count": 4,
                "storage_class_quotas": [
                    {
                        "storage_name": CEPHFS.storage_name,
                        "requests_storage_bytes": 256 * 1024**2,
                        "pvc_count": 4,
                    }
                ],
            },
        )
        reset_id = submit_quota_request(
            client,
            "PATCH",
            f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/{namespace_name}",
            requester_id="portal:phase9:cephfs",
            payload={"reset_quota_to_default": True, "resource_type": "ceph-user"},
        )
        run_planner_worker(services.repository, rm_worker, reset_id, "phase9 cephfs reset")
        assert_equal(
            read_resource_quota(live_adapter, CEPHFS, namespace_name)["spec"]["hard"],
            default_hard,
            "phase9 cephfs reset hard",
        )
        audit_id = submit_quota_request(
            client,
            "POST",
            "/api/v1/resource-management/kubernetes/namespace-quotas:audit",
            requester_id="portal:phase9:cephfs",
            payload={
                "scope": {"cluster_name": "cluster-a", "namespace_name": namespace_name},
                "include_non_dms": True,
                "include_usage_pressure": True,
            },
        )
        run_planner_worker(services.repository, rm_worker, audit_id, "phase9 cephfs audit")
        assert_no_action_required(
            client,
            resource_key=f"cluster-a:{namespace_name}",
            forbidden={"kubernetes_quota_drifted", "kubernetes_quota_missing"},
        )
        return {
            "target": "cephfs",
            "namespace": namespace_name,
            "default_policy_id": policy["policy_id"],
            "create_request_id": create_id,
            "reset_request_id": reset_id,
            "audit_request_id": audit_id,
        }
    finally:
        delete_namespace(live_adapter, CEPHFS, namespace_name)


def upsert_default_policy(client: TestClient, *, resource_type: str, quota: dict) -> dict:
    response = client.post(
        "/api/v1/resource-management/default-quota-policies",
        headers=API_HEADERS,
        json={
            "resource_kind": "kubernetes_namespace_quota",
            "resource_type": resource_type,
            "quota": quota,
        },
    )
    assert_equal(response.status_code, 200, f"default policy {resource_type}")
    return response.json()


def audit_quota(
    client: TestClient,
    namespace_name: str,
    *,
    include_non_dms: bool = False,
    include_usage_pressure: bool = False,
    usage_thresholds: dict | None = None,
) -> str:
    payload = {
        "scope": {"cluster_name": "cluster-b", "namespace_name": namespace_name},
        "include_non_dms": include_non_dms,
        "include_usage_pressure": include_usage_pressure,
        "record_action_required": True,
    }
    if usage_thresholds:
        payload["usage_thresholds"] = usage_thresholds
    return submit_quota_request(
        client,
        "POST",
        "/api/v1/resource-management/kubernetes/namespace-quotas:audit",
        requester_id="portal:phase9:longhorn",
        payload=payload,
    )


def assert_action_required(
    client: TestClient, *, resource_key: str, expected: set[str]
) -> None:
    response = client.get("/api/v1/operations/action-required", headers=API_HEADERS)
    assert_equal(response.status_code, 200, "action-required query")
    issues = [
        issue for issue in response.json() if issue.get("resource_key") == resource_key
    ]
    issue_types = {issue["issue_type"] for issue in issues}
    missing = expected - issue_types
    assert_true(not missing, f"missing action-required issues: {sorted(missing)}")


def assert_no_action_required(
    client: TestClient, *, resource_key: str, forbidden: set[str]
) -> None:
    response = client.get("/api/v1/operations/action-required", headers=API_HEADERS)
    assert_equal(response.status_code, 200, "action-required query")
    issues = [
        issue for issue in response.json() if issue.get("resource_key") == resource_key
    ]
    issue_types = {issue["issue_type"] for issue in issues}
    present = forbidden & issue_types
    assert_true(not present, f"unexpected action-required issues: {sorted(present)}")


if __name__ == "__main__":
    raise SystemExit(main())
