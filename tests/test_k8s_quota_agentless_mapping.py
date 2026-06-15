"""Kubernetes namespace quota is agentless (kubectl to the target API server), so its
storage mapping must NOT require RM-agent ``resource_management`` readiness. A CSI mapping
whose StorageClass is visible (sanity passes) but has no fresh RM agent on the target
cluster (readiness "Missing", sanity "Degraded") must still be plannable for a namespace
quota. Filesystem RM keeps requiring RM-agent readiness. A mapping whose StorageClass is
missing/driver-mismatched (sanity "Failed") must still be rejected.

Regression for the ddz26 Ceph-CSI registration: ceph-rbd/ceph-cephfs live on a cluster with
no DMS RM agents, so resource_management readiness can never be "Ready" there.
"""

from __future__ import annotations

from typing import Any

import pytest

from dms.db import Database
from dms.domain import (
    LifecycleState,
    OperationKind,
    ResourceKind,
    StorageMappingInput,
)
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository

CLUSTER = "ddz26"
CSI_STORAGE_NAME = "ceph-rbd-ddz26"
CSI_STORAGE_CLASS = "ceph-rbd"
CSI_DRIVER = "rbd.csi.ceph.com"


@pytest.fixture()
def repository(tmp_path) -> DmsRepository:
    operational = Database(f"sqlite:///{tmp_path / 'operational.db'}")
    observability_db = Database(f"sqlite:///{tmp_path / 'observability.db'}")
    migrate_all(operational, observability_db)
    # ObservabilityRepository is constructed for parity with production wiring.
    ObservabilityRepository(observability_db)
    return DmsRepository(operational)


def _register_mapping(
    repository: DmsRepository,
    *,
    storage_name: str,
    storage_class_name: str,
    sanity_status: str,
    rm_readiness: str,
    errors: list[dict[str, Any]] | None = None,
    storage_class_exists: bool = True,
) -> None:
    readiness = {
        "resource_management": rm_readiness,
        "data_management": "Missing",
        "inventory": "Ready",
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name=storage_name,
            backend_template={"backend_type": "ceph-csi", "csi_driver": CSI_DRIVER},
            cluster_name=CLUSTER,
            storage_class_name=storage_class_name,
            sanity_status=sanity_status,
        ),
        actor="admin",
        sanity_result={
            "storage_name": storage_name,
            "status": sanity_status,
            "readiness": readiness,
            "kubernetes_observed": {
                "cluster_name": CLUSTER,
                "storage_class_name": storage_class_name,
                "storage_class_exists": storage_class_exists,
                "provisioner": CSI_DRIVER if storage_class_exists else None,
            },
            "errors": errors or [],
            "warnings": [],
        },
        readiness=readiness,
    )


def _create_quota_request(repository: DmsRepository, *, namespace: str) -> str:
    return repository.create_request(
        requester_id="cocoa.song",
        actor="api-client",
        operation=OperationKind.K8S_QUOTA_CREATE.value,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key=f"{CLUSTER}:{namespace}",
        payload={
            "cluster_name": CLUSTER,
            "namespace_name": namespace,
            "expires_at": "2099-01-01T00:00:00Z",
            "quota": {"requests_storage_bytes": 100 * 1024**3, "pvc_count": 20},
            "storage_class_quotas": [{"storage_name": CSI_STORAGE_NAME}],
        },
    )


def test_agentless_csi_mapping_is_plannable_for_namespace_quota(repository):
    # StorageClass visible (sanity passed cluster/SC/driver) but no RM agent on the
    # target cluster -> Degraded + resource_management "Missing". Must NOT be rejected.
    _register_mapping(
        repository,
        storage_name=CSI_STORAGE_NAME,
        storage_class_name=CSI_STORAGE_CLASS,
        sanity_status="Degraded",
        rm_readiness="Missing",
    )
    request_id = _create_quota_request(repository, namespace="team-alpha")

    assert Planner(repository).run_once() == 1

    request = repository.get_request(request_id)
    assert request["status"] != LifecycleState.REJECTED.value
    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert plan["worker_role"] == "RM"


def test_failed_sanity_still_rejects_namespace_quota(repository):
    # StorageClass missing / cluster not wired -> sanity "Failed". Still must reject:
    # we can't apply a ResourceQuota for a StorageClass the cluster doesn't expose.
    _register_mapping(
        repository,
        storage_name=CSI_STORAGE_NAME,
        storage_class_name=CSI_STORAGE_CLASS,
        sanity_status="Failed",
        rm_readiness="Missing",
        errors=[{"code": "storage_class_missing", "message": "StorageClass not found"}],
        storage_class_exists=False,
    )
    request_id = _create_quota_request(repository, namespace="team-beta")

    assert Planner(repository).run_once() == 1

    request = repository.get_request(request_id)
    assert request["status"] == LifecycleState.REJECTED.value
    assert repository.get_plan_by_request(request_id) is None
