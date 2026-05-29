from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
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
    delete_namespace,
    get_inventory,
    mask_url,
    read_resource_quota,
    rm_worker_for,
    run_planner_worker,
    sc_key,
    sc_pvc_key,
    submit_quota_request,
    upsert_mapping,
)


def main() -> int:
    settings = Settings.from_env()
    assert_true(
        settings.database_url.startswith("postgresql://"),
        "Phase 8 live verification must use operational PostgreSQL",
    )
    assert_true(settings.observability_is_separate, "observability DB must be separate")

    app = create_app(settings)
    services = app.state.services
    client = TestClient(app)
    live_adapter = KubernetesNamespaceQuotaLiveAdapter.from_settings(settings)
    token = uuid4().hex[:8]

    reports = wait_for_phase8_reports(client)
    mismatch = verify_identity_mismatch(client, services.observability)
    inventory = get_inventory(client)
    mapping_summary = verify_storage_mappings(client, inventory)
    quota_summary = verify_quota_subset(
        token=token,
        client=client,
        services=services,
        live_adapter=live_adapter,
        settings=settings,
    )
    stale_summary = verify_stale_handling(client, services.repository)
    action_required = client.get(
        "/api/v1/operations/action-required", headers=API_HEADERS
    ).json()

    summary = {
        "status": "ok",
        "operational_database_url": mask_url(settings.database_url),
        "observability_database_url": mask_url(settings.observability_database_url),
        "phase8_reports": reports,
        "identity_mismatch": mismatch,
        "storage_mappings": mapping_summary,
        "quota_subset": quota_summary,
        "stale_handling": stale_summary,
        "action_required_issue_types": sorted(
            {issue["issue_type"] for issue in action_required}
        ),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def wait_for_phase8_reports(client: TestClient, *, timeout_seconds: int = 180) -> dict:
    required = {
        ("cluster-a", "RM"),
        ("cluster-a", "DM"),
        ("cluster-b", "RM"),
    }
    deadline = time.time() + timeout_seconds
    latest: dict[tuple[str, str], dict] = {}
    while time.time() < deadline:
        response = client.get("/api/v1/operations/agent-reports", headers=API_HEADERS)
        assert_equal(response.status_code, 200, "agent report query")
        for report in response.json():
            payload = report["report"]
            if payload.get("schema_version") != "phase8.v1":
                continue
            if report["freshness_status"] != "Fresh":
                continue
            key = (report["cluster_name"], report["worker_role"])
            latest.setdefault(key, report)
        if required.issubset(latest):
            return {
                f"{cluster}:{role}": {
                    "report_id": report["report_id"],
                    "node_name": report["node_name"],
                    "reported_at": report["reported_at"],
                }
                for (cluster, role), report in sorted(latest.items())
                if (cluster, role) in required
            }
        time.sleep(5)
    raise AssertionError(f"missing fresh Phase 8 Agent reports: {sorted(required - set(latest))}")


def verify_identity_mismatch(client: TestClient, observability) -> dict:
    response = client.post(
        "/api/v1/agent/reports",
        json={
            "schema_version": "phase8.v1",
            "cluster_name": "cluster-a",
            "node_name": "phase8-mismatch",
            "node_uid": "phase8-mismatch",
            "worker_role": "RM",
            "mounts": [],
            "csi": [],
            "tools": [],
            "credentials": [],
            "networks": [],
            "identity_evidence": {"source": "phase8-live"},
        },
        headers={"x-dms-actor": "node:cluster-a:not-phase8-mismatch"},
    )
    assert_equal(response.status_code, 403, "identity mismatch rejected")
    assert_true(
        any(
            event["event_type"] == "agent_node_identity_mismatch"
            for event in observability.list_events(limit=50)
        ),
        "identity mismatch observability event",
    )
    return {"status_code": response.status_code}


def verify_storage_mappings(client: TestClient, inventory: dict) -> list[dict]:
    summaries = []
    for target in (CEPHFS, LONGHORN, LONGHORN_STATIC):
        assert_storage_class(
            inventory, target.cluster_name, target.storage_class_name, target.provisioner
        )
        mapping = upsert_mapping(client, target)
        sanity = mapping["mapping"]["sanity_result"]
        assert_equal(
            sanity["readiness"]["resource_management"],
            "Ready",
            f"{target.storage_name} RM readiness from real Agent report",
        )
        summaries.append(
            {
                "storage_name": target.storage_name,
                "storage_class_name": target.storage_class_name,
                "status": mapping["status"],
                "readiness": sanity["readiness"],
                "rm_candidate_count": len(sanity["agent_observed"]["rm_candidates"]),
                "dm_candidate_count": len(sanity["agent_observed"]["dm_candidates"]),
            }
        )
    return summaries


def verify_stale_handling(client: TestClient, repository) -> dict:
    marked = repository.mark_stale_agent_reports(stale_seconds=-1)
    assert_true(marked >= 3, "real Phase 8 Agent reports marked stale")
    stale_response = client.get(
        "/api/v1/operations/agent-reports?freshness=stale", headers=API_HEADERS
    )
    assert_equal(stale_response.status_code, 200, "stale agent report query")
    stale_reports = stale_response.json()
    assert_true(
        any(report["report"].get("schema_version") == "phase8.v1" for report in stale_reports),
        "stale Phase 8 Agent report returned",
    )
    action_required = client.get(
        "/api/v1/operations/action-required", headers=API_HEADERS
    ).json()
    assert_true(
        "agent_report_stale" in {issue["issue_type"] for issue in action_required},
        "stale Agent report exposed as action-required",
    )
    return {"marked_stale": marked, "stale_report_count": len(stale_reports)}


def verify_quota_subset(
    *,
    token: str,
    client: TestClient,
    services,
    live_adapter: KubernetesNamespaceQuotaLiveAdapter,
    settings: Settings,
) -> list[dict]:
    rm_worker = rm_worker_for(services, live_adapter, settings, f"phase8-rm-{token}")
    return [
        verify_cephfs_quota(token, client, services, rm_worker, live_adapter),
        verify_longhorn_quota(token, client, services, rm_worker, live_adapter),
    ]


def verify_cephfs_quota(
    token: str, client: TestClient, services, rm_worker, live_adapter
) -> dict:
    namespace_name = f"dms-phase8-cephfs-{token}"
    try:
        create_id = submit_quota_request(
            client,
            "POST",
            "/api/v1/resource-management/kubernetes/namespace-quotas",
            requester_id="portal:phase8:cephfs",
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
                        "pvc_count": 2,
                    }
                ],
            },
        )
        run_planner_worker(services.repository, rm_worker, create_id, "phase8 cephfs create")
        quota = read_resource_quota(live_adapter, CEPHFS, namespace_name)
        assert_true(quota["exists"], "phase8 CephFS ResourceQuota exists")
        check_id = submit_quota_request(
            client,
            "POST",
            f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/{namespace_name}:check",
            requester_id="portal:phase8:cephfs",
            payload={},
        )
        run_planner_worker(services.repository, rm_worker, check_id, "phase8 cephfs check")
        return {"target": "cephfs", "namespace": namespace_name, "create_request_id": create_id}
    finally:
        cleanup_quota(client, services, rm_worker, live_adapter, CEPHFS, namespace_name)


def verify_longhorn_quota(
    token: str, client: TestClient, services, rm_worker, live_adapter
) -> dict:
    namespace_name = f"dms-phase8-longhorn-{token}"
    try:
        create_id = submit_quota_request(
            client,
            "POST",
            "/api/v1/resource-management/kubernetes/namespace-quotas",
            requester_id="portal:phase8:longhorn",
            payload={
                "cluster_name": "cluster-b",
                "namespace_name": namespace_name,
                "allow_namespace_create": True,
                "resource_type": "user",
                "quota": {"requests_storage_bytes": 256 * 1024**2, "pvc_count": 4},
                "storage_class_quotas": [
                    {
                        "storage_name": LONGHORN.storage_name,
                        "requests_storage_bytes": 128 * 1024**2,
                        "pvc_count": 2,
                    },
                    {
                        "storage_name": LONGHORN_STATIC.storage_name,
                        "requests_storage_bytes": 64 * 1024**2,
                        "pvc_count": 1,
                    },
                ],
            },
        )
        run_planner_worker(services.repository, rm_worker, create_id, "phase8 longhorn create")
        quota = read_resource_quota(live_adapter, LONGHORN, namespace_name)
        assert_true(quota["exists"], "phase8 Longhorn ResourceQuota exists")
        hard = quota["spec"]["hard"]
        assert_equal(hard[sc_key(LONGHORN)], "128Mi", "phase8 longhorn quota hard")
        assert_equal(hard[sc_pvc_key(LONGHORN_STATIC)], "1", "phase8 static pvc hard")
        check_id = submit_quota_request(
            client,
            "POST",
            f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}:check",
            requester_id="portal:phase8:longhorn",
            payload={},
        )
        run_planner_worker(services.repository, rm_worker, check_id, "phase8 longhorn check")
        return {"target": "longhorn", "namespace": namespace_name, "create_request_id": create_id}
    finally:
        cleanup_quota(client, services, rm_worker, live_adapter, LONGHORN, namespace_name)


def cleanup_quota(client: TestClient, services, rm_worker, live_adapter, target, namespace: str) -> None:
    try:
        delete_id = submit_quota_request(
            client,
            "DELETE",
            f"/api/v1/resource-management/kubernetes/namespace-quotas/{target.cluster_name}/{namespace}",
            requester_id="portal:phase8:cleanup",
            payload={},
        )
        run_planner_worker(services.repository, rm_worker, delete_id, f"phase8 {target.name} delete")
    finally:
        delete_namespace(live_adapter, target, namespace)


if __name__ == "__main__":
    raise SystemExit(main())
