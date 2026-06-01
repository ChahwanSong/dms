from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from dms.adapters import StubFilesystemBackendAdapter, StubKubernetesNamespaceQuotaAdapter
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import LifecycleState, OperationKind, ResourceKind, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import RMWorkerRuntime


LONGHORN = "longhorn-b"
LONGHORN_CLASS = "testbed-longhorn"
STATIC = "longhorn-static-b"
STATIC_CLASS = "longhorn-static"
RESOURCE_KEY = "cluster-b:phase9-quota"
API_HEADERS = {"x-dms-actor": "api-client"}


def test_phase9_default_quota_reset_renders_policy_hard(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_mapping(repository, LONGHORN, LONGHORN_CLASS)
    register_mapping(repository, STATIC, STATIC_CLASS)
    seed_multi_resource(repository)
    repository.upsert_default_quota_policy(
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_type="user",
        quota=default_policy_quota(),
        actor="admin",
    )

    request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_UPDATE,
        {"reset_quota_to_default": True, "resource_type": "user"},
    )

    assert Planner(repository).run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert plan["desired_state"]["default_quota_policy_id"] == (
        f"{ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value}:user"
    )
    assert plan["desired_state"]["quota"] == {
        "requests_storage_bytes": 2 * 1024**3,
        "pvc_count": 30,
    }
    assert plan["desired_state"]["resource_quota_hard"] == default_hard()


def test_phase9_default_quota_reset_rejects_missing_policy(tmp_path):
    repository, _ = repository_pair(tmp_path)
    seed_multi_resource(repository)

    request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_UPDATE,
        {"reset_quota_to_default": True, "resource_type": "user"},
    )

    assert Planner(repository).run_once() == 1

    assert repository.get_plan_by_request(request_id) is None
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    assert result["verification_summary"]["issues"] == [
        {
            "reason": "default_quota_policy_missing",
            "resource_kind": ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
            "resource_type": "user",
        }
    ]


def test_phase9_blocked_default_reset_updates_restore_target(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_mapping(repository, LONGHORN, LONGHORN_CLASS)
    register_mapping(repository, STATIC, STATIC_CLASS)
    seed_multi_resource(repository, blocked=True)
    repository.upsert_default_quota_policy(
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_type="user",
        quota=default_policy_quota(),
        actor="admin",
    )

    request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_UPDATE,
        {"reset_quota_to_default": True, "resource_type": "user"},
    )

    assert Planner(repository).run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert plan["desired_state"]["resource_quota_hard"] == {
        key: "0" for key in default_hard()
    }
    assert plan["desired_state"]["block_state"]["restore_hard"] == default_hard()
    assert plan["desired_state"]["block_state"]["updated_while_blocked"] is True


def test_phase9_audit_records_action_required_and_clean_audit_resolves(tmp_path):
    repository, observability = repository_pair(tmp_path)
    seed_multi_resource(repository)
    adapter = StubKubernetesNamespaceQuotaAdapter()
    adapter.resource_quotas[("cluster-b", "phase9-quota", "dms-storage-quota")] = (
        live_quota(
            {
                **multi_hard(),
                sc_key(LONGHORN_CLASS, "requests.storage"): "768Mi",
            },
            status_used={"requests.storage": "900Mi"},
        )
    )
    adapter.resource_quota_lists[("cluster-b", "phase9-quota")] = [
        adapter.resource_quotas[("cluster-b", "phase9-quota", "dms-storage-quota")],
        {
            "exists": True,
            "cluster_name": "cluster-b",
            "namespace": "phase9-quota",
            "name": "team-admin-quota",
            "spec_hard": {"requests.storage": "512Mi"},
            "status_hard": {},
            "status_used": {},
        },
    ]
    client = client_for(tmp_path, repository, observability, adapter)

    response = client.post(
        "/api/v1/resource-management/kubernetes/namespace-quotas:audit",
        headers=API_HEADERS,
        json={
            "requester_id": "portal:ops",
            "payload": {
                "scope": {"cluster_name": "cluster-b", "namespace_name": "phase9-quota"},
                "include_non_dms": True,
                "include_usage_pressure": True,
                "usage_thresholds": {"warning_percent": 80, "critical_percent": 95},
                "record_action_required": True,
            },
        },
    )
    assert response.status_code == 202
    request_id = response.json()["request_id"]
    Planner(repository).run_once()
    run_worker(repository, observability, adapter)

    [result] = repository.get_results(request_id)
    summary = result["verification_summary"]
    assert summary["audit_status"] == "ActionRequired"
    target = summary["targets"][0]
    assert {issue["issue_type"] for issue in target["issues"]} == {
        "kubernetes_quota_drifted"
    }
    assert target["usage_pressure"][0]["issue_type"] == "quota_usage_warning"
    assert target["effective_quota_warnings"][0]["type"] == (
        "non_dms_quota_more_restrictive"
    )

    action_required = client.get(
        "/api/v1/operations/action-required", headers=API_HEADERS
    ).json()
    issue_types = {issue["issue_type"] for issue in action_required}
    assert "kubernetes_quota_drifted" in issue_types
    assert "quota_usage_warning" in issue_types
    assert "non_dms_quota_more_restrictive" in issue_types

    adapter.resource_quotas[("cluster-b", "phase9-quota", "dms-storage-quota")] = (
        live_quota(multi_hard(), status_used={"requests.storage": "0"})
    )
    adapter.resource_quota_lists[("cluster-b", "phase9-quota")] = [
        adapter.resource_quotas[("cluster-b", "phase9-quota", "dms-storage-quota")]
    ]
    clean_response = client.post(
        "/api/v1/resource-management/kubernetes/namespace-quotas:audit",
        headers=API_HEADERS,
        json={
            "requester_id": "portal:ops",
            "payload": {
                "scope": {"cluster_name": "cluster-b", "namespace_name": "phase9-quota"},
                "include_non_dms": True,
                "include_usage_pressure": True,
            },
        },
    )
    assert clean_response.status_code == 202
    Planner(repository).run_once()
    run_worker(repository, observability, adapter)

    resolved = client.get("/api/v1/operations/action-required", headers=API_HEADERS).json()
    resolved_types = {issue["issue_type"] for issue in resolved}
    assert "kubernetes_quota_drifted" not in resolved_types
    assert "quota_usage_warning" not in resolved_types
    assert "non_dms_quota_more_restrictive" not in resolved_types


def test_phase9_audit_detects_metadata_drift(tmp_path):
    repository, observability = repository_pair(tmp_path)
    seed_multi_resource(repository)
    adapter = StubKubernetesNamespaceQuotaAdapter()
    quota = live_quota(multi_hard())
    quota["annotations"] = {}
    adapter.resource_quotas[("cluster-b", "phase9-quota", "dms-storage-quota")] = quota

    request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_AUDIT,
        {
            "scope": {"cluster_name": "cluster-b", "namespace_name": "phase9-quota"},
            "include_usage_pressure": False,
        },
    )
    Planner(repository).run_once()
    run_worker(repository, observability, adapter)

    [result] = repository.get_results(request_id)
    target = result["verification_summary"]["targets"][0]
    assert target["issues"] == [
        {
            "issue_type": "kubernetes_quota_metadata_drift",
            "field": "metadata.annotations.dms.io/resource-key",
            "reason": "resource_key_mismatch",
            "desired": RESOURCE_KEY,
            "live": None,
        }
    ]


def repository_pair(tmp_path) -> tuple[DmsRepository, ObservabilityRepository]:
    operational = Database(f"sqlite:///{tmp_path / 'operational.db'}")
    observability_db = Database(f"sqlite:///{tmp_path / 'observability.db'}")
    migrate_all(operational, observability_db)
    return DmsRepository(operational), ObservabilityRepository(observability_db)


def client_for(
    tmp_path,
    repository: DmsRepository,
    observability: ObservabilityRepository,
    adapter: StubKubernetesNamespaceQuotaAdapter,
) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'operational.db'}",
        observability_database_url=f"sqlite:///{tmp_path / 'observability.db'}",
    )
    app = create_app(
        settings,
        repository,
        observability,
        kubernetes_quota=adapter,
    )
    return TestClient(app)


def register_mapping(
    repository: DmsRepository,
    storage_name: str,
    storage_class_name: str,
) -> None:
    readiness = {
        "resource_management": "Ready",
        "data_management": "Ready",
        "inventory": "Ready",
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name=storage_name,
            backend_template={"backend_type": "longhorn", "csi_driver": "driver.longhorn.io"},
            cluster_name="cluster-b",
            storage_class_name=storage_class_name,
            sanity_status="Ready",
        ),
        actor="admin",
        sanity_result={
            "storage_name": storage_name,
            "status": "Ready",
            "readiness": readiness,
            "kubernetes_observed": {
                "cluster_name": "cluster-b",
                "storage_class_name": storage_class_name,
                "storage_class_exists": True,
                "provisioner": "driver.longhorn.io",
            },
            "agent_observed": {
                "rm_readiness": "Ready",
                "dm_readiness": "Ready",
                "rm_candidates": [{"cluster_name": "cluster-b", "node_name": "worker"}],
                "dm_candidates": [{"cluster_name": "cluster-b", "node_name": "worker"}],
            },
            "checks": [],
            "warnings": [],
            "errors": [],
        },
        readiness=readiness,
    )


def seed_multi_resource(
    repository: DmsRepository,
    *,
    blocked: bool = False,
) -> None:
    hard = {key: "0" for key in multi_hard()} if blocked else multi_hard()
    desired = {
        "cluster_name": "cluster-b",
        "namespace_name": "phase9-quota",
        "requester_id": "alice",
        "resource_type": "user",
        "quota": {"requests_storage_bytes": 1024**3, "pvc_count": 20},
        "storage_class_quotas": [
            {
                "storage_name": LONGHORN,
                "storage_class_name": LONGHORN_CLASS,
                "cluster_name": "cluster-b",
                "requests_storage_bytes": 512 * 1024**2,
                "pvc_count": 10,
            },
            {
                "storage_name": STATIC,
                "storage_class_name": STATIC_CLASS,
                "cluster_name": "cluster-b",
                "requests_storage_bytes": 256 * 1024**2,
                "pvc_count": 4,
            },
        ],
        "resource_kind": ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        "resource_key": RESOURCE_KEY,
        "resource_quota_name": "dms-storage-quota",
        "resource_quota_hard": hard,
        "expires_at": "2099-01-01T00:00:00Z",
    }
    if blocked:
        desired["block"] = True
        desired["block_state"] = {
            "blocked": True,
            "block_mode": "quota-zero",
            "restore_hard": multi_hard(),
            "reason": "test",
        }
    repository.upsert_resource(
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key=RESOURCE_KEY,
        desired_state=desired,
        applied_state={"resource_quota": {"spec_hard": hard}},
        observed_state={
            "resource_quota": {
                "exists": True,
                "spec_hard": hard,
                "status_hard": hard,
                "status_used": {"requests.storage": "0", "persistentvolumeclaims": "0"},
            }
        },
        status=LifecycleState.SUCCEEDED.value,
    )


def create_request(
    repository: DmsRepository, operation: OperationKind, payload: dict[str, Any]
) -> str:
    if operation == OperationKind.K8S_QUOTA_AUDIT:
        merged = payload
        resource_key = RESOURCE_KEY
    else:
        merged = {"cluster_name": "cluster-b", "namespace_name": "phase9-quota", **payload}
        if operation == OperationKind.K8S_QUOTA_CREATE:
            merged.setdefault("expires_at", "2099-01-01T00:00:00Z")
        resource_key = RESOURCE_KEY
    return repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=operation.value,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key=resource_key,
        payload=merged,
    )


def run_worker(
    repository: DmsRepository,
    observability: ObservabilityRepository,
    adapter: StubKubernetesNamespaceQuotaAdapter,
) -> None:
    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=adapter,
        worker_id="rm-cluster-b",
    )
    assert worker.run_once() == 1


def live_quota(
    hard: dict[str, str], *, status_used: dict[str, str] | None = None
) -> dict[str, Any]:
    return {
        "exists": True,
        "cluster_name": "cluster-b",
        "namespace": "phase9-quota",
        "name": "dms-storage-quota",
        "resource_version": "12345",
        "labels": {
            "app.kubernetes.io/managed-by": "dms",
            "dms.io/resource-kind": "kubernetes-namespace-quota",
        },
        "annotations": {"dms.io/resource-key": RESOURCE_KEY},
        "spec_hard": hard,
        "status_hard": hard,
        "status_used": status_used or {"requests.storage": "0", "persistentvolumeclaims": "0"},
    }


def multi_hard() -> dict[str, str]:
    return {
        "requests.storage": "1Gi",
        "persistentvolumeclaims": "20",
        sc_key(LONGHORN_CLASS, "requests.storage"): "512Mi",
        sc_key(LONGHORN_CLASS, "persistentvolumeclaims"): "10",
        sc_key(STATIC_CLASS, "requests.storage"): "256Mi",
        sc_key(STATIC_CLASS, "persistentvolumeclaims"): "4",
    }


def default_policy_quota() -> dict[str, Any]:
    return {
        "requests_storage_bytes": 2 * 1024**3,
        "pvc_count": 30,
        "storage_class_quotas": [
            {
                "storage_name": LONGHORN,
                "requests_storage_bytes": 1024**3,
                "pvc_count": 16,
            },
            {
                "storage_name": STATIC,
                "requests_storage_bytes": 512 * 1024**2,
                "pvc_count": 8,
            },
        ],
    }


def default_hard() -> dict[str, str]:
    return {
        "requests.storage": "2Gi",
        "persistentvolumeclaims": "30",
        sc_key(LONGHORN_CLASS, "requests.storage"): "1Gi",
        sc_key(LONGHORN_CLASS, "persistentvolumeclaims"): "16",
        sc_key(STATIC_CLASS, "requests.storage"): "512Mi",
        sc_key(STATIC_CLASS, "persistentvolumeclaims"): "8",
    }


def sc_key(storage_class_name: str, resource_name: str) -> str:
    return f"{storage_class_name}.storageclass.storage.k8s.io/{resource_name}"
