"""Planner worker-pool derivation: one placement path for every backend.

DMS treats each filesystem as a plain POSIX mount, so there is no per-backend placement
branch -- GPFS, WekaFS and CephFS all seed the same agent-inventory pool, and the DM
worker replaces it wholesale with its own candidate lists when it claims the job.

(Previously tests/test_backend_data_worker_pool.py, which exercised the GPFS/WekaFS
adapters in dms.backend_registry. Those adapters emitted placement hints -- mount_path,
filesystem_name, data_network, tool_candidates -- that nothing ever read, and were
removed along with the module. Before that it was tests/test_gpfs_backend.py and
tests/test_weka_backend.py, which were RM-only.)
"""

from __future__ import annotations

import pytest

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
            "backend_type": "gpfs",
            "filesystem_name": "gpfs0",
            "mount_path": "/gpfs/gpfs0",
            "managed_root": "/gpfs/gpfs0/dms",
            "csi_driver": "spectrumscale.csi.ibm.com",
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
            "backend_type": "wekafs",
            "filesystem_name": "pvs_weka",
            "mount_path": "/pvs_weka",
            "managed_root": "/pvs_weka/dms",
            "csi_driver": "csi.weka.io",
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
    Planner(repository).run_once()
    job = repository.get_data_job_by_request(request_id)
    assert job is not None
    return job


@pytest.mark.parametrize(
    "mapping_factory, storage_name",
    [
        (_gpfs_mapping, "gpfs-a"),
        (_weka_mapping, "weka-a"),
        (_cephfs_mapping, "cephfs-a"),
    ],
)
def test_every_backend_plans_to_agent_inventory(
    repository, mapping_factory, storage_name
):
    _register(repository, mapping_factory())

    job = _plan_scan(repository, storage_name)

    pool = job["worker_pool"]
    assert pool["selection"] == "agent-inventory"
    assert pool["required_mounts"] == [storage_name]
    assert pool["readiness"]["data_management"] == "Ready"
    assert pool["candidates"] == [{"cluster_name": "cluster-a", "node_name": "dm-1"}]


def test_no_per_backend_placement_hints_are_emitted(repository):
    """GPFS/WekaFS used to stamp mount_path, filesystem_name, data_network and
    tool_candidates onto the pool. Nothing ever read them; they must not come back."""
    _register(repository, _gpfs_mapping())

    pool = _plan_scan(repository, "gpfs-a")["worker_pool"]

    for stale_hint in (
        "backend_type",
        "mount_path",
        "filesystem_name",
        "data_network",
        "tool_candidates",
        "requires_posix_identity",
    ):
        assert stale_hint not in pool


def test_worker_pool_floor_for_unregistered_storage(repository):
    """Defensive floor. The DM readiness gate rejects unregistered storage before
    planning, so this shape is never persisted in practice."""
    assert Planner(repository)._worker_pool("nope") == {
        "selection": "agent-inventory",
        "required_mounts": ["nope"],
        "candidates": [],
    }
