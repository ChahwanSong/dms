"""BackendAdapterRegistry.data_worker_pool: per-backend DM worker-pool derivation.

GPFS and WekaFS mappings carry backend-specific placement hints (mount_path,
filesystem_name, data_network) that the planner stamps onto the data_jobs row so the
DM worker can pick nodes and build the MPI job. Everything else falls back to plain
agent-inventory selection.

(Previously covered inside tests/test_gpfs_backend.py and tests/test_weka_backend.py,
which were RM-only and removed with the resource-management feature.)
"""

from __future__ import annotations

import pytest

from dms.backend_registry import BackendAdapterRegistry
from dms.backends.gpfs import GPFS_BACKEND_TYPE, GPFS_CSI_DRIVER
from dms.backends.weka import WEKAFS_BACKEND_TYPE, WEKAFS_CSI_DRIVER
from dms.db import Database
from dms.domain import OperationKind, ResourceKind, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository


@pytest.fixture()
def repository(tmp_path):
    operational = Database(f"sqlite:///{tmp_path / 'operational.db'}")
    observability_db = Database(f"sqlite:///{tmp_path / 'observability.db'}")
    migrate_all(operational, observability_db)
    ObservabilityRepository(observability_db)
    return DmsRepository(operational)


def _ready_sanity(storage_name: str) -> dict:
    return {
        "storage_name": storage_name,
        "status": "Ready",
        "checked_at": "2026-07-31T00:00:00+00:00",
        "kubernetes_observed": {
            "cluster_name": "cluster-a",
            "storage_class_name": None,
            "storage_class_exists": False,
            "provisioner": None,
        },
        "agent_observed": {
            "fresh_reports": 1,
            "stale_reports": 0,
            "dm_readiness": "Ready",
            "dm_candidates": [{"cluster_name": "cluster-a", "node_name": "dm-1"}],
        },
        "readiness": {"data_management": "Ready", "inventory": "Ready"},
        "checks": [],
        "warnings": [],
        "errors": [],
    }


def _register(repository: DmsRepository, mapping: StorageMappingInput) -> None:
    sanity = _ready_sanity(mapping.storage_name)
    repository.upsert_storage_mapping(
        mapping,
        actor="admin",
        sanity_result=sanity,
        readiness=sanity["readiness"],
    )


def _gpfs_mapping() -> StorageMappingInput:
    return StorageMappingInput(
        storage_name="gpfs-a",
        backend_template={
            "backend_type": GPFS_BACKEND_TYPE,
            "filesystem_name": "gpfs0",
            "mount_path": "/gpfs/gpfs0",
            "managed_root": "/gpfs/gpfs0/dms",
            "csi_driver": GPFS_CSI_DRIVER,
            "data_network": "storage-net-a",
        },
        cluster_name="cluster-a",
        storage_class_name="gpfs-csi",
        sanity_status="Ready",
    )


def _weka_mapping() -> StorageMappingInput:
    return StorageMappingInput(
        storage_name="weka-a",
        backend_template={
            "backend_type": WEKAFS_BACKEND_TYPE,
            "filesystem_name": "pvs_weka",
            "mount_path": "/pvs_weka",
            "managed_root": "/pvs_weka/dms",
            "csi_driver": WEKAFS_CSI_DRIVER,
            "data_network": "storage-net-w",
        },
        cluster_name="cluster-a",
        storage_class_name=None,
        sanity_status="Ready",
    )


def _cephfs_mapping() -> StorageMappingInput:
    return StorageMappingInput(
        storage_name="cephfs-a",
        backend_template={
            "backend_type": "cephfs",
            "mount_path": "/cephfs",
            "managed_root": "/cephfs/dms",
        },
        cluster_name="cluster-a",
        storage_class_name=None,
        sanity_status="Ready",
    )


def _plan_scan(repository: DmsRepository, storage_name: str) -> dict:
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.DATA_SCAN.value,
        resource_kind=ResourceKind.DATA_JOB.value,
        resource_key=f"{storage_name}:data.scan:project-alpha",
        payload={
            "storage_name": storage_name,
            "target_path": "project-alpha",
            "priority": 100,
        },
    )
    Planner(
        repository,
        backend_registry=BackendAdapterRegistry(repository),
    ).run_once()
    job = repository.get_data_job_by_request(request_id)
    assert job is not None
    return job


def test_gpfs_data_management_planning_records_gpfs_worker_pool(repository):
    _register(repository, _gpfs_mapping())

    job = _plan_scan(repository, "gpfs-a")

    assert job["worker_pool"]["backend_type"] == GPFS_BACKEND_TYPE
    assert job["worker_pool"]["required_mounts"] == ["gpfs-a"]
    assert job["worker_pool"]["mount_path"] == "/gpfs/gpfs0"
    assert job["worker_pool"]["filesystem_name"] == "gpfs0"
    assert job["worker_pool"]["data_network"] == "storage-net-a"
    assert "dscan" in job["worker_pool"]["tool_candidates"]


def test_weka_data_management_planning_records_weka_worker_pool(repository):
    _register(repository, _weka_mapping())

    job = _plan_scan(repository, "weka-a")

    assert job["worker_pool"]["backend_type"] == WEKAFS_BACKEND_TYPE
    assert job["worker_pool"]["required_mounts"] == ["weka-a"]
    assert job["worker_pool"]["mount_path"] == "/pvs_weka"
    assert job["worker_pool"]["filesystem_name"] == "pvs_weka"
    assert job["worker_pool"]["data_network"] == "storage-net-w"
    assert "dscan" in job["worker_pool"]["tool_candidates"]


def test_backend_without_a_dedicated_adapter_falls_back_to_agent_inventory(repository):
    """CephFS (and any unrecognised backend) has no placement hints of its own — the
    DM worker selects purely from the agent inventory."""
    _register(repository, _cephfs_mapping())

    job = _plan_scan(repository, "cephfs-a")

    assert job["worker_pool"]["selection"] == "agent-inventory"
    assert job["worker_pool"]["required_mounts"] == ["cephfs-a"]
    assert "backend_type" not in job["worker_pool"]


def test_data_worker_pool_for_unregistered_storage_is_agent_inventory(repository):
    pool = BackendAdapterRegistry(repository).data_worker_pool("nope")

    assert pool == {
        "selection": "agent-inventory",
        "required_mounts": ["nope"],
        "candidates": [],
    }
