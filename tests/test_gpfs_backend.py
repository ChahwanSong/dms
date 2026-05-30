from __future__ import annotations

import pytest

from dms.adapters import StubFilesystemBackendAdapter, StubKubernetesNamespaceQuotaAdapter
from dms.backend_registry import BackendAdapterRegistry
from dms.backends.gpfs import GPFS_BACKEND_TYPE, GPFS_CSI_DRIVER, GpfsQuotaStrategy
from dms.db import Database
from dms.domain import LifecycleState, OperationKind, ResourceKind, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import RMWorkerRuntime


@pytest.fixture()
def repository_pair(tmp_path):
    operational = Database(f"sqlite:///{tmp_path / 'operational.db'}")
    observability_db = Database(f"sqlite:///{tmp_path / 'observability.db'}")
    migrate_all(operational, observability_db)
    return DmsRepository(operational), ObservabilityRepository(observability_db)


def gpfs_mapping() -> StorageMappingInput:
    return StorageMappingInput(
        storage_name="gpfs-a",
        backend_template={
            "backend_type": GPFS_BACKEND_TYPE,
            "filesystem_name": "gpfs0",
            "mount_path": "/gpfs/gpfs0",
            "fileset_root": "/gpfs/gpfs0/dms",
            "quota_scope": "fileset",
            "csi_driver": GPFS_CSI_DRIVER,
            "data_network": "storage-net-a",
        },
        cluster_name="cluster-a",
        storage_class_name="gpfs-csi",
        sanity_status="Ready",
    )


def gpfs_ready_sanity() -> dict:
    return {
        "storage_name": "gpfs-a",
        "status": "Ready",
        "checked_at": "2026-05-27T00:00:00+00:00",
        "kubernetes_observed": {
            "cluster_name": "cluster-a",
            "storage_class_name": "gpfs-csi",
            "storage_class_exists": True,
            "provisioner": GPFS_CSI_DRIVER,
        },
        "agent_observed": {
            "fresh_reports": 2,
            "stale_reports": 0,
            "rm_readiness": "Ready",
            "dm_readiness": "Ready",
            "rm_candidates": [{"cluster_name": "cluster-a", "node_name": "rm-gpfs"}],
            "dm_candidates": [{"cluster_name": "cluster-a", "node_name": "dm-gpfs"}],
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


def register_gpfs_mapping(repository: DmsRepository) -> None:
    sanity = gpfs_ready_sanity()
    repository.upsert_storage_mapping(
        gpfs_mapping(),
        actor="admin",
        sanity_result=sanity,
        readiness=sanity["readiness"],
    )


def test_gpfs_quota_strategy_renders_filesystem_quota_shape():
    rendered = GpfsQuotaStrategy().render_quota(
        {"capacity_bytes": 10**12, "file_count": 5_000_000}
    )

    assert rendered["backend_type"] == GPFS_BACKEND_TYPE
    assert rendered["quota_scope"] == "fileset"
    assert rendered["hard_limit_bytes"] == 10**12
    assert rendered["hard_file_limit"] == 5_000_000
    assert rendered["side_effect"] == "not-executed-phase1"


def test_gpfs_filesystem_resource_management_uses_gpfs_adapter(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="gpfs-a:project-alpha",
        payload={
            "storage_name": "gpfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
        },
    )
    registry = BackendAdapterRegistry.with_phase1_defaults(repository)
    Planner(repository, backend_registry=registry).run_once()
    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-gpfs",
        backend_registry=registry,
    )

    assert worker.run_once() == 1
    [resource] = repository.list_resources()
    assert repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    assert resource["observed_state"]["adapter"] == "gpfs-filesystem-stub"
    assert resource["observed_state"]["backend"]["filesystem_name"] == "gpfs0"
    assert resource["observed_state"]["quota"]["backend_type"] == GPFS_BACKEND_TYPE


def test_gpfs_kubernetes_namespace_quota_uses_gpfs_csi_mapping(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.K8S_QUOTA_CREATE.value,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key="cluster-a:alice",
        payload={
            "cluster_name": "cluster-a",
            "namespace_name": "alice",
            "storage_class_quotas": [{"storage_name": "gpfs-a"}],
            "quota": {"requests_storage_bytes": 4 * 10**12, "pvc_count": 20},
        },
    )
    registry = BackendAdapterRegistry.with_phase1_defaults(repository)
    Planner(repository, backend_registry=registry).run_once()
    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-gpfs",
        backend_registry=registry,
    )

    assert worker.run_once() == 1
    [resource] = repository.list_resources()
    assert repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    assert resource["observed_state"]["adapter"] == "gpfs-kubernetes-quota-stub"
    assert resource["observed_state"]["backend"]["csi_driver"] == GPFS_CSI_DRIVER
    assert resource["observed_state"]["backend"]["storage_class_name"] == "gpfs-csi"
    assert resource["observed_state"]["hard"]["requests.storage"] == 4 * 10**12


def test_gpfs_data_management_planning_records_gpfs_worker_pool(repository_pair):
    repository, _ = repository_pair
    register_gpfs_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.DATA_SCAN.value,
        resource_kind=ResourceKind.DATA_JOB.value,
        resource_key="gpfs-a:data.scan:project-alpha",
        payload={
            "storage_name": "gpfs-a",
            "target_path": "project-alpha",
            "priority": 100,
        },
    )

    Planner(
        repository,
        backend_registry=BackendAdapterRegistry.with_phase1_defaults(repository),
    ).run_once()

    job = repository.get_data_job_by_request(request_id)
    assert job is not None
    assert job["worker_pool"]["backend_type"] == GPFS_BACKEND_TYPE
    assert job["worker_pool"]["required_mounts"] == ["gpfs-a"]
    assert job["worker_pool"]["mount_path"] == "/gpfs/gpfs0"
    assert "dscan" in job["worker_pool"]["tool_candidates"]
