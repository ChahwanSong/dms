from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from dms.adapters import (
    StubFilesystemBackendAdapter,
    StubKubernetesNamespaceQuotaAdapter,
)
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import LifecycleState, OperationKind, ResourceKind, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.query import OperationalQueryService
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import RMWorkerRuntime

API_HEADERS = {"x-dms-actor": "api-client"}


def test_phase15_filesystem_create_requires_expires_at(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_filesystem_mapping(repository)
    request_id = create_request(
        repository,
        operation=OperationKind.FILESYSTEM_CREATE,
        resource_kind=ResourceKind.FILESYSTEM,
        resource_key="cephfs-a:project-alpha",
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
        },
    )

    assert Planner(repository).run_once() == 1

    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    assert _reasons(result) == {"expires_at_required"}


def test_phase15_filesystem_update_preserves_or_changes_expiry(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_filesystem_mapping(repository)
    seed_filesystem(repository, expires_at="2099-01-01T00:00:00+00:00")
    update_id = create_request(
        repository,
        operation=OperationKind.FILESYSTEM_UPDATE,
        resource_kind=ResourceKind.FILESYSTEM,
        resource_key="cephfs-a:project-alpha",
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "expires_at": "2099-02-01T00:00:00Z",
            "reason": "extend expiry",
        },
    )

    assert Planner(repository).run_once() == 1
    assert run_rm_worker(repository, observability) == 1

    resource = repository.get_resource(
        ResourceKind.FILESYSTEM.value, "cephfs-a:project-alpha"
    )
    assert repository.get_request(update_id)["status"] == LifecycleState.SUCCEEDED.value
    assert resource["desired_state"]["expires_at"] == "2099-02-01T00:00:00+00:00"

    quota_update_id = create_request(
        repository,
        operation=OperationKind.FILESYSTEM_UPDATE,
        resource_kind=ResourceKind.FILESYSTEM,
        resource_key="cephfs-a:project-alpha",
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "quota": {"capacity_bytes": 2048, "file_count": 200},
        },
    )

    assert Planner(repository).run_once() == 1
    assert run_rm_worker(repository, observability) == 1

    resource = repository.get_resource(
        ResourceKind.FILESYSTEM.value, "cephfs-a:project-alpha"
    )
    assert (
        repository.get_request(quota_update_id)["status"]
        == LifecycleState.SUCCEEDED.value
    )
    assert resource["desired_state"]["expires_at"] == "2099-02-01T00:00:00+00:00"


def test_phase15_filesystem_import_defaults_and_rejects_unsupported_expiry_fields(
    tmp_path,
):
    repository, _ = repository_pair(tmp_path)
    register_filesystem_mapping(repository)
    request_id = create_request(
        repository,
        operation=OperationKind.FILESYSTEM_IMPORT,
        resource_kind=ResourceKind.FILESYSTEM,
        resource_key="cephfs-a:project-alpha",
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "access_policy": {
                "mode": "adopt_existing_group",
                "expected_group": "dms-team-a",
                "expected_mode": "0770",
                "users": ["alice", "bob"],
            },
        },
    )

    assert Planner(repository).run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert "expires_at" in plan["desired_state"]

    rejected_id = create_request(
        repository,
        operation=OperationKind.FILESYSTEM_IMPORT,
        resource_kind=ResourceKind.FILESYSTEM,
        resource_key="cephfs-a:project-beta",
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-beta",
            "expiry_at": "2099-01-01T00:00:00Z",
            "access_policy": {
                "mode": "adopt_existing_group",
                "expected_group": "dms-team-a",
                "users": ["alice", "bob"],
            },
        },
    )

    assert Planner(repository).run_once() == 1

    [result] = repository.get_results(rejected_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    assert "expires_at_field_unsupported" in _reasons(result)


def test_phase15_kubernetes_create_update_import_and_expiring_lifecycle(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_kubernetes_mapping(repository)

    missing_id = create_request(
        repository,
        operation=OperationKind.K8S_QUOTA_CREATE,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA,
        resource_key="cluster-b:team-a",
        payload=kubernetes_payload(namespace_name="team-a", include_expires=False),
    )
    assert Planner(repository).run_once() == 1
    [missing_result] = repository.get_results(missing_id)
    assert _reasons(missing_result) == {"expires_at_required"}

    create_id = create_request(
        repository,
        operation=OperationKind.K8S_QUOTA_CREATE,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA,
        resource_key="cluster-b:team-a",
        payload=kubernetes_payload(
            namespace_name="team-a", expires_at="2099-01-01T00:00:00Z"
        ),
    )
    assert Planner(repository).run_once() == 1
    assert run_rm_worker(repository, observability) == 1
    resource = repository.get_resource(
        ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value, "cluster-b:team-a"
    )
    assert repository.get_request(create_id)["status"] == LifecycleState.SUCCEEDED.value
    assert resource["desired_state"]["expires_at"] == "2099-01-01T00:00:00+00:00"

    update_id = create_request(
        repository,
        operation=OperationKind.K8S_QUOTA_UPDATE,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA,
        resource_key="cluster-b:team-a",
        payload={
            "cluster_name": "cluster-b",
            "namespace_name": "team-a",
            "quota": {"requests_storage_bytes": 2 * 1024**3, "pvc_count": 4},
            "storage_class_quotas": [{"storage_name": "longhorn-b"}],
        },
    )
    assert Planner(repository).run_once() == 1
    plan = repository.get_plan_by_request(update_id)
    assert plan["desired_state"]["expires_at"] == "2099-01-01T00:00:00+00:00"

    import_id = create_request(
        repository,
        operation=OperationKind.K8S_QUOTA_IMPORT,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA,
        resource_key="cluster-b:team-import",
        payload={
            "cluster_name": "cluster-b",
            "namespace_name": "team-import",
            "resource_quota_name": "dms-storage-quota",
            "storage_class_quotas": [{"storage_name": "longhorn-b"}],
        },
    )
    assert Planner(repository).run_once() == 1
    import_plan = repository.get_plan_by_request(import_id)
    assert import_plan is not None
    assert import_plan["desired_state"]["expires_at"]

    seed_kubernetes_quota(
        repository, namespace_name="expired", expires_at="2000-01-01T00:00:00Z"
    )
    client = client_for(tmp_path, repository, observability)
    response = client.get(
        "/api/v1/operations/kubernetes/namespace-quotas/expiring",
        headers=API_HEADERS,
        params={"before": "2000-01-02T00:00:00Z"},
    )
    assert response.status_code == 200
    assert {item["resource_key"] for item in response.json()} >= {"cluster-b:expired"}

    issues = OperationalQueryService(repository, observability).action_required()
    assert "kubernetes_quota_expired_unblocked" in {
        issue["issue_type"] for issue in issues
    }


def test_phase15_kubernetes_expiration_sweep_blocks_user_and_skips_system(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_kubernetes_mapping(repository)
    seed_kubernetes_quota(
        repository, namespace_name="expired", expires_at="2000-01-01T00:00:00Z"
    )
    seed_kubernetes_quota(
        repository,
        namespace_name="system-expired",
        resource_type="system",
        expires_at="2000-01-01T00:00:00Z",
    )
    request_id = create_request(
        repository,
        operation=OperationKind.K8S_QUOTA_EXPIRATION_SWEEP,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA,
        resource_key="kubernetes-namespace-quota-expiration-sweep",
        payload={
            "scope": {"cluster_name": "cluster-b"},
            "expired_before": "2000-01-02T00:00:00Z",
            "dry_run": False,
            "max_targets": 10,
            "reason": "expiry sweep",
        },
    )

    assert Planner(repository).run_once() == 1
    assert run_rm_worker(repository, observability) == 1

    [result] = repository.get_results(request_id)
    summary = result["verification_summary"]
    assert summary["blocked_count"] == 1
    assert summary["skipped_count"] == 1
    target_results = {
        (target["resource_key"], target["result"], target.get("reason"))
        for target in summary["targets"]
    }
    assert target_results == {
        ("cluster-b:expired", "blocked", None),
        ("cluster-b:system-expired", "skipped", "resource_type_not_auto_blocked"),
    }
    blocked = repository.get_resource(
        ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value, "cluster-b:expired"
    )
    assert blocked["status"] == LifecycleState.BLOCKED.value
    assert (
        blocked["desired_state"]["block_state"]["restore_hard"]["requests.storage"]
        == "1Gi"
    )


def repository_pair(tmp_path) -> tuple[DmsRepository, ObservabilityRepository]:
    operational = Database(f"sqlite:///{tmp_path / 'operational.db'}")
    observability_db = Database(f"sqlite:///{tmp_path / 'observability.db'}")
    migrate_all(operational, observability_db)
    return DmsRepository(operational), ObservabilityRepository(observability_db)


def client_for(
    tmp_path, repository: DmsRepository, observability: ObservabilityRepository
) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'operational.db'}",
        observability_database_url=f"sqlite:///{tmp_path / 'observability.db'}",
    )
    return TestClient(create_app(settings, repository, observability))


def register_filesystem_mapping(repository: DmsRepository) -> None:
    _register_mapping(
        repository,
        storage_name="cephfs-a",
        backend_type="cephfs",
        cluster_name="cluster-a",
        storage_class_name="testbed-cephfs",
    )


def register_kubernetes_mapping(repository: DmsRepository) -> None:
    _register_mapping(
        repository,
        storage_name="longhorn-b",
        backend_type="longhorn",
        cluster_name="cluster-b",
        storage_class_name="testbed-longhorn",
    )


def _register_mapping(
    repository: DmsRepository,
    *,
    storage_name: str,
    backend_type: str,
    cluster_name: str,
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
            backend_template={
                "backend_type": backend_type,
                "csi_driver": "driver.test",
            },
            cluster_name=cluster_name,
            storage_class_name=storage_class_name,
            sanity_status="Ready",
        ),
        actor="admin",
        sanity_result={"status": "Ready", "readiness": readiness, "checks": []},
        readiness=readiness,
    )


def create_request(
    repository: DmsRepository,
    *,
    operation: OperationKind,
    resource_kind: ResourceKind,
    resource_key: str,
    payload: dict[str, Any],
) -> str:
    return repository.create_request(
        requester_id="portal:phase15",
        actor="api-client",
        operation=operation.value,
        resource_kind=resource_kind.value,
        resource_key=resource_key,
        payload=payload,
    )


def run_rm_worker(
    repository: DmsRepository, observability: ObservabilityRepository
) -> int:
    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-phase15",
    )
    return worker.run_once()


def seed_filesystem(repository: DmsRepository, *, expires_at: str) -> None:
    desired = {
        "storage_name": "cephfs-a",
        "directory_name": "project-alpha",
        "users": ["alice", "bob"],
        "access_group": "dms-grp-project-alpha",
        "mode": "0770",
        "resource_type": "user",
        "expires_at": expires_at,
    }
    repository.upsert_resource(
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="cephfs-a:project-alpha",
        desired_state=desired,
        applied_state={"expires_at": expires_at},
        observed_state={},
        status=LifecycleState.SUCCEEDED.value,
    )


def kubernetes_payload(
    *,
    namespace_name: str,
    expires_at: str | None = None,
    include_expires: bool = True,
) -> dict[str, Any]:
    payload = {
        "cluster_name": "cluster-b",
        "namespace_name": namespace_name,
        "resource_type": "user",
        "allow_namespace_create": True,
        "quota": {"requests_storage_bytes": 1024**3, "pvc_count": 2},
        "storage_class_quotas": [{"storage_name": "longhorn-b"}],
    }
    if include_expires:
        payload["expires_at"] = expires_at or "2099-01-01T00:00:00Z"
    return payload


def seed_kubernetes_quota(
    repository: DmsRepository,
    *,
    namespace_name: str,
    expires_at: str,
    resource_type: str = "user",
) -> None:
    desired = {
        "cluster_name": "cluster-b",
        "namespace_name": namespace_name,
        "resource_type": resource_type,
        "quota": {"requests_storage_bytes": 1024**3, "pvc_count": 2},
        "storage_class_quotas": [
            {
                "storage_name": "longhorn-b",
                "storage_class_name": "testbed-longhorn",
                "cluster_name": "cluster-b",
            }
        ],
        "resource_kind": ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        "resource_key": f"cluster-b:{namespace_name}",
        "resource_quota_name": "dms-storage-quota",
        "resource_quota_hard": {
            "requests.storage": "1Gi",
            "persistentvolumeclaims": "2",
        },
        "expires_at": expires_at,
    }
    repository.upsert_resource(
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key=f"cluster-b:{namespace_name}",
        desired_state=desired,
        applied_state={"expires_at": expires_at},
        observed_state={
            "resource_quota": {
                "exists": True,
                "spec_hard": desired["resource_quota_hard"],
            }
        },
        status=LifecycleState.SUCCEEDED.value,
    )


def _reasons(result: dict[str, Any]) -> set[str]:
    return {issue["reason"] for issue in result["verification_summary"]["issues"]}
