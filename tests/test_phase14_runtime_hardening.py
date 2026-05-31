from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from dms.adapters import (
    BackendPreconditionError,
    KubernetesNamespaceQuotaLiveAdapter,
    StubFilesystemBackendAdapter,
    StubKubernetesNamespaceQuotaAdapter,
)
from dms.backend_registry import BackendAdapterRegistry
from dms.backends.gpfs import (
    GPFS_BACKEND_TYPE,
    GPFS_CSI_DRIVER,
    GpfsFilesystemBackendAdapter,
    GpfsKubernetesNamespaceQuotaAdapter,
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


class FailingObservabilityRepository(ObservabilityRepository):
    def record_event(self, **_: Any) -> str:
        raise RuntimeError("observability database unavailable")


def test_auth_rejection_survives_observability_write_failure(tmp_path):
    settings, repository, observability = _repositories(tmp_path)
    client = TestClient(create_app(settings, repository, observability))

    response = client.post(
        "/api/v1/resource-management/filesystems",
        json=_filesystem_body("auth-failure"),
    )

    assert response.status_code == 401
    assert repository.list_requests(requester_id="user-1") == []


def test_worker_success_survives_observability_write_failure(tmp_path):
    _, repository, observability = _repositories(tmp_path)
    _register_mapping(repository, storage_name="cephfs-a", backend_type="cephfs")
    request_id = repository.create_request(
        requester_id="user-1",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="cephfs-a:phase14-success",
        payload=_filesystem_payload("cephfs-a", "phase14-success"),
    )
    Planner(repository).run_once()
    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-phase14",
    )

    assert worker.run_once() == 1
    assert repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    results = repository.get_results(request_id)
    assert [result["terminal_status"] for result in results] == [
        LifecycleState.SUCCEEDED.value
    ]


def test_unsupported_filesystem_backend_fails_closed_and_is_action_required(tmp_path):
    settings, repository, observability = _repositories(tmp_path)
    _register_mapping(repository, storage_name="typo-a", backend_type="cephfss")
    request_id = repository.create_request(
        requester_id="user-1",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="typo-a:phase14-unsupported",
        payload=_filesystem_payload("typo-a", "phase14-unsupported"),
    )
    Planner(repository).run_once()
    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-phase14",
        backend_registry=BackendAdapterRegistry.with_live_defaults(repository, settings),
    )

    assert worker.run_once() == 1
    request = repository.get_request(request_id)
    assert request["status"] == LifecycleState.BACKEND_APPLY_FAILED.value
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.BACKEND_APPLY_FAILED.value
    assert result["error_category"] == "backend_precondition"
    assert result["verification_summary"]["backend_side_effect"] is False
    assert result["verification_summary"]["issues"][0]["issue_type"] == "unsupported_backend"
    issues = OperationalQueryService(repository, observability).action_required()
    assert any(
        issue["issue_type"] == "request_attention"
        and issue["request_id"] == request_id
        and issue["status"] == LifecycleState.BACKEND_APPLY_FAILED.value
        for issue in issues
    )


def test_live_registry_uses_live_kubernetes_adapter_for_generic_csi_backend(tmp_path):
    settings, repository, _ = _repositories(tmp_path)
    _register_mapping(
        repository,
        storage_name="longhorn-b",
        backend_type="longhorn",
        cluster_name="cluster-b",
        storage_class_name="testbed-longhorn",
    )
    registry = BackendAdapterRegistry.with_live_defaults(repository, settings)
    plan = _kubernetes_quota_plan("longhorn-b", "testbed-longhorn")

    adapter = registry.kubernetes_for_plan(plan)

    assert isinstance(adapter, KubernetesNamespaceQuotaLiveAdapter)


def test_rm_worker_does_not_select_filesystem_adapter_for_kubernetes_quota(tmp_path):
    settings, repository, observability = _repositories(tmp_path)
    _register_mapping(
        repository,
        storage_name="longhorn-b",
        backend_type="longhorn",
        cluster_name="cluster-b",
        storage_class_name="testbed-longhorn",
    )
    request_id = repository.create_request(
        requester_id="user-1",
        actor="api-client",
        operation=OperationKind.K8S_QUOTA_CREATE.value,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key="cluster-b:alice",
        payload={
            "cluster_name": "cluster-b",
            "namespace_name": "alice",
            "storage_class_quotas": [{"storage_name": "longhorn-b"}],
            "quota": {"requests_storage_bytes": 1024**3, "pvc_count": 2},
        },
    )
    Planner(repository).run_once()
    kubernetes_adapter = StubKubernetesNamespaceQuotaAdapter()
    registry = BackendAdapterRegistry(
        repository=repository,
        default_kubernetes_adapter=kubernetes_adapter,
        settings=settings,
        enforce_supported_backends=True,
    )
    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-phase14",
        backend_registry=registry,
    )

    assert worker.run_once() == 1
    assert repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    assert kubernetes_adapter.calls == [
        ("apply_resource_quota", repository.get_plan_by_request(request_id)["plan_id"])
    ]


def test_live_registry_keeps_gpfs_filesystem_and_kubernetes_paths_separate(tmp_path):
    settings, repository, _ = _repositories(tmp_path)
    _register_gpfs_mapping(repository)
    registry = BackendAdapterRegistry.with_live_defaults(repository, settings)
    filesystem_plan = {
        "plan_id": "plan-gpfs-fs",
        "resource_key": "gpfs-a:project-alpha",
        "desired_state": {"storage_name": "gpfs-a", "directory_name": "project-alpha"},
    }
    kubernetes_plan = _kubernetes_quota_plan("gpfs-a", "gpfs-csi")

    assert isinstance(registry.filesystem_for_plan(filesystem_plan), GpfsFilesystemBackendAdapter)
    assert isinstance(
        registry.kubernetes_for_plan(kubernetes_plan),
        GpfsKubernetesNamespaceQuotaAdapter,
    )


def test_live_registry_rejects_unknown_kubernetes_quota_backend(tmp_path):
    settings, repository, _ = _repositories(tmp_path)
    _register_mapping(
        repository,
        storage_name="mystery-b",
        backend_type="mysteryfs",
        cluster_name="cluster-b",
        storage_class_name="mystery-sc",
    )
    registry = BackendAdapterRegistry.with_live_defaults(repository, settings)

    try:
        registry.kubernetes_for_plan(_kubernetes_quota_plan("mystery-b", "mystery-sc"))
    except BackendPreconditionError as exc:
        assert "unsupported kubernetes namespace quota backend type" in str(exc)
    else:
        raise AssertionError("unknown Kubernetes quota backend did not fail closed")


def _repositories(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
        worker_lease_seconds=300,
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    return (
        settings,
        DmsRepository(operational),
        FailingObservabilityRepository(observability_db),
    )


def _filesystem_body(directory_name: str) -> dict[str, Any]:
    return {"requester_id": "user-1", "payload": _filesystem_payload("cephfs-a", directory_name)}


def _filesystem_payload(storage_name: str, directory_name: str) -> dict[str, Any]:
    return {
        "storage_name": storage_name,
        "directory_name": directory_name,
        "resource_type": "user",
        "users": ["alice", "bob"],
    }


def _register_mapping(
    repository: DmsRepository,
    *,
    storage_name: str,
    backend_type: str,
    cluster_name: str = "cluster-a",
    storage_class_name: str = "testbed-cephfs",
) -> None:
    sanity = {
        "storage_name": storage_name,
        "status": "Ready",
        "checked_at": "2026-05-31T00:00:00+00:00",
        "kubernetes_observed": {
            "cluster_name": cluster_name,
            "storage_class_name": storage_class_name,
            "storage_class_exists": True,
            "provisioner": f"{backend_type}.csi.dms.test",
        },
        "agent_observed": {
            "fresh_reports": 1,
            "stale_reports": 0,
            "rm_readiness": "Ready",
            "dm_readiness": "Ready",
            "rm_candidates": [{"cluster_name": cluster_name, "node_name": "rm-1"}],
            "dm_candidates": [{"cluster_name": cluster_name, "node_name": "dm-1"}],
        },
        "readiness": {
            "resource_management": "Ready",
            "data_management": "Ready",
            "inventory": "Ready",
        },
        "checks": [],
        "warnings": [],
        "errors": [],
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name=storage_name,
            backend_template={
                "backend_type": backend_type,
                "mount_path": "/mnt/testbed-cephfs",
                "managed_root": "/mnt/testbed-cephfs/dms",
            },
            cluster_name=cluster_name,
            storage_class_name=storage_class_name,
            sanity_status="Ready",
        ),
        actor="admin",
        sanity_result=sanity,
        readiness=sanity["readiness"],
    )


def _register_gpfs_mapping(repository: DmsRepository) -> None:
    _register_mapping(
        repository,
        storage_name="gpfs-a",
        backend_type=GPFS_BACKEND_TYPE,
        cluster_name="cluster-a",
        storage_class_name="gpfs-csi",
    )
    mapping = repository.get_storage_mapping("gpfs-a")
    backend_template = {
        **mapping["backend_template"],
        "filesystem_name": "gpfs0",
        "mount_path": "/gpfs/gpfs0",
        "fileset_root": "/gpfs/gpfs0/dms",
        "quota_scope": "fileset",
        "fileset_name_template": "dms-{directory_name}",
        "csi_driver": GPFS_CSI_DRIVER,
        "data_network": "storage-net-a",
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name="gpfs-a",
            backend_template=backend_template,
            cluster_name="cluster-a",
            storage_class_name="gpfs-csi",
            sanity_status="Ready",
        ),
        actor="admin",
        sanity_result=mapping["sanity_result"],
        readiness=mapping["readiness"],
    )


def _kubernetes_quota_plan(storage_name: str, storage_class_name: str) -> dict[str, Any]:
    return {
        "plan_id": f"plan-{storage_name}",
        "resource_key": "cluster-b:alice",
        "desired_state": {
            "cluster_name": "cluster-b",
            "namespace_name": "alice",
            "storage_class_quotas": [
                {
                    "storage_name": storage_name,
                    "storage_class_name": storage_class_name,
                    "requests_storage_bytes": 1024**3,
                    "pvc_count": 2,
                }
            ],
            "quota": {"requests_storage_bytes": 1024**3, "pvc_count": 2},
        },
    }
