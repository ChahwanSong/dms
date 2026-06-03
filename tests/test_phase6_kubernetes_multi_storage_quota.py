from __future__ import annotations

from typing import Any

import pytest

from dms.adapters import (
    effective_resource_quota_warnings,
    kubernetes_resource_quota_hard_issues,
    render_kubernetes_resource_quota_hard,
    _sync_desired_from_resource_quota_hard,
)
from dms.db import Database
from dms.domain import LifecycleState, OperationKind, ResourceKind, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository


LONGHORN = "longhorn-b"
LONGHORN_CLASS = "testbed-longhorn"
STATIC = "longhorn-static-b"
STATIC_CLASS = "longhorn-static"
CEPHFS = "cephfs-b"
CEPHFS_CLASS = "testbed-cephfs"
GPFS = "gpfs-b"
GPFS_CLASS = "gpfs-csi"
WEKA = "weka-b"
WEKA_CLASS = "weka-csi"
RESOURCE_KEY = "cluster-b:phase6-quota"


@pytest.fixture()
def repository(tmp_path):
    operational = Database(f"sqlite:///{tmp_path / 'operational.db'}")
    observability = Database(f"sqlite:///{tmp_path / 'observability.db'}")
    migrate_all(operational, observability)
    return DmsRepository(operational)


def test_phase6_renderer_creates_multi_storageclass_hard_keys():
    hard = render_kubernetes_resource_quota_hard(
        {
            "quota": {"requests_storage_bytes": 1024**3, "pvc_count": 20},
            "storage_class_quotas": [
                {
                    "storage_name": LONGHORN,
                    "storage_class_name": LONGHORN_CLASS,
                    "requests_storage_bytes": 512 * 1024**2,
                    "pvc_count": 10,
                },
                {
                    "storage_name": STATIC,
                    "storage_class_name": STATIC_CLASS,
                    "requests_storage_bytes": 256 * 1024**2,
                    "pvc_count": 4,
                },
            ],
        }
    )

    assert hard == multi_hard(storage="1Gi", pvc_count="20")


def test_phase17_planner_renders_mixed_backend_storageclass_quota(repository):
    register_mapping(
        repository,
        CEPHFS,
        CEPHFS_CLASS,
        backend_type="cephfs",
        csi_driver="rook-ceph.cephfs.csi.ceph.com",
    )
    register_mapping(
        repository,
        GPFS,
        GPFS_CLASS,
        backend_type="gpfs",
        csi_driver="spectrumscale.csi.ibm.com",
    )
    register_mapping(
        repository,
        WEKA,
        WEKA_CLASS,
        backend_type="weka",
        csi_driver="csi.weka.io",
    )
    request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_CREATE,
        {
            "quota": {"requests_storage_bytes": 4 * 1024**4, "pvc_count": 60},
            "storage_class_quotas": [
                {
                    "storage_name": CEPHFS,
                    "requests_storage_bytes": 512 * 1024**3,
                    "pvc_count": 10,
                },
                {
                    "storage_name": GPFS,
                    "requests_storage_bytes": 1024**4,
                    "pvc_count": 20,
                },
                {
                    "storage_name": WEKA,
                    "requests_storage_bytes": 2 * 1024**4,
                    "pvc_count": 30,
                },
            ],
        },
    )

    assert Planner(repository).run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert plan["execution_metadata"]["planner"] == "phase6"
    assert plan["desired_state"]["storage_class_quotas"] == [
        {
            "storage_name": CEPHFS,
            "requests_storage_bytes": 512 * 1024**3,
            "pvc_count": 10,
            "storage_class_name": CEPHFS_CLASS,
            "cluster_name": "cluster-b",
        },
        {
            "storage_name": GPFS,
            "requests_storage_bytes": 1024**4,
            "pvc_count": 20,
            "storage_class_name": GPFS_CLASS,
            "cluster_name": "cluster-b",
        },
        {
            "storage_name": WEKA,
            "requests_storage_bytes": 2 * 1024**4,
            "pvc_count": 30,
            "storage_class_name": WEKA_CLASS,
            "cluster_name": "cluster-b",
        },
    ]
    assert plan["desired_state"]["resource_quota_hard"] == {
        "requests.storage": "4096Gi",
        "persistentvolumeclaims": "60",
        sc_key(CEPHFS_CLASS, "requests.storage"): "512Gi",
        sc_key(CEPHFS_CLASS, "persistentvolumeclaims"): "10",
        sc_key(GPFS_CLASS, "requests.storage"): "1024Gi",
        sc_key(GPFS_CLASS, "persistentvolumeclaims"): "20",
        sc_key(WEKA_CLASS, "requests.storage"): "2048Gi",
        sc_key(WEKA_CLASS, "persistentvolumeclaims"): "30",
    }


def test_phase6_planner_rejects_duplicate_storage_name(repository):
    register_mapping(repository, LONGHORN, LONGHORN_CLASS)
    request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_CREATE,
        {
            "storage_class_quotas": [
                {
                    "storage_name": LONGHORN,
                    "requests_storage_bytes": 64 * 1024**2,
                },
                {
                    "storage_name": LONGHORN,
                    "requests_storage_bytes": 32 * 1024**2,
                },
            ]
        },
    )

    assert Planner(repository).run_once() == 1

    assert repository.get_plan_by_request(request_id) is None
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    assert result["verification_summary"]["issues"][0]["reason"] == "duplicate_storage_name"


def test_phase6_planner_rejects_cross_cluster_mapping(repository):
    register_mapping(repository, "cephfs-a", "testbed-cephfs", cluster_name="cluster-a")
    request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_CREATE,
        {
            "storage_class_quotas": [
                {
                    "storage_name": "cephfs-a",
                    "requests_storage_bytes": 64 * 1024**2,
                }
            ]
        },
    )

    assert Planner(repository).run_once() == 1

    assert repository.get_plan_by_request(request_id) is None
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    assert result["verification_summary"]["issues"][0]["reason"] == (
        "storage_mapping_cluster_mismatch"
    )


def test_phase6_decrease_guard_checks_storageclass_pvc_count(repository):
    register_mapping(repository, LONGHORN, LONGHORN_CLASS)
    register_mapping(repository, STATIC, STATIC_CLASS)
    seed_multi_resource(
        repository,
        observed_used={
            "requests.storage": "384Mi",
            "persistentvolumeclaims": "8",
            sc_key(STATIC_CLASS, "persistentvolumeclaims"): "5",
        },
    )
    request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_UPDATE,
        {
            "quota": {"requests_storage_bytes": 2 * 1024**3, "pvc_count": 30},
            "storage_class_quotas": [
                {
                    "storage_name": LONGHORN,
                    "requests_storage_bytes": 1024**3,
                    "pvc_count": 16,
                },
                {
                    "storage_name": STATIC,
                    "requests_storage_bytes": 512 * 1024**2,
                    "pvc_count": 4,
                },
            ],
        },
    )

    assert Planner(repository).run_once() == 1

    assert repository.get_plan_by_request(request_id) is None
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    assert result["verification_summary"]["issues"][0]["resource"] == sc_key(
        STATIC_CLASS, "persistentvolumeclaims"
    )


def test_phase6_block_zeros_all_storageclass_keys(repository):
    register_mapping(repository, LONGHORN, LONGHORN_CLASS)
    register_mapping(repository, STATIC, STATIC_CLASS)
    seed_multi_resource(repository)
    request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_BLOCK,
        {"block": True, "block_mode": "quota-zero", "reason": "test"},
    )

    assert Planner(repository).run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert plan["desired_state"]["resource_quota_hard"] == {
        key: "0" for key in multi_hard()
    }
    assert plan["desired_state"]["block_state"]["restore_hard"] == multi_hard()


def test_phase6_sync_updates_each_storageclass_entry_from_live_hard():
    desired = {
        "quota": {"requests_storage_bytes": 1024**3, "pvc_count": 20},
        "storage_class_quotas": [
            {"storage_name": LONGHORN, "storage_class_name": LONGHORN_CLASS},
            {"storage_name": STATIC, "storage_class_name": STATIC_CLASS},
        ],
    }
    hard = multi_hard(storage="2Gi", pvc_count="30")

    warnings = _sync_desired_from_resource_quota_hard(desired, hard)

    assert warnings == []
    assert desired["quota"] == {"requests_storage_bytes": 2 * 1024**3, "pvc_count": 30}
    assert desired["storage_class_quotas"] == [
        {
            "storage_name": LONGHORN,
            "storage_class_name": LONGHORN_CLASS,
            "requests_storage_bytes": 512 * 1024**2,
            "pvc_count": 10,
        },
        {
            "storage_name": STATIC,
            "storage_class_name": STATIC_CLASS,
            "requests_storage_bytes": 256 * 1024**2,
            "pvc_count": 4,
        },
    ]


def test_phase6_check_diff_and_effective_quota_warning_are_keyed():
    issues = kubernetes_resource_quota_hard_issues(
        desired_hard=multi_hard(),
        live_hard={**multi_hard(), sc_key(LONGHORN_CLASS, "requests.storage"): "768Mi"},
    )
    warnings = effective_resource_quota_warnings(
        resource_quotas=[
            {
                "name": "dms-storage-quota",
                "spec_hard": multi_hard(),
            },
            {
                "name": "team-admin-quota",
                "spec_hard": {sc_key(LONGHORN_CLASS, "requests.storage"): "128Mi"},
            },
        ],
        dms_hard=multi_hard(),
    )

    assert issues == [
        {
            "field": "spec.hard",
            "key": sc_key(LONGHORN_CLASS, "requests.storage"),
            "reason": "hard_limit_drifted",
            "desired": "512Mi",
            "live": "768Mi",
        }
    ]
    assert warnings == [
        {
            "type": "non_dms_quota_more_restrictive",
            "resource_quota_name": "team-admin-quota",
            "key": sc_key(LONGHORN_CLASS, "requests.storage"),
            "dms_hard": "512Mi",
            "non_dms_hard": "128Mi",
        }
    ]


def register_mapping(
    repository: DmsRepository,
    storage_name: str,
    storage_class_name: str,
    *,
    cluster_name: str = "cluster-b",
    backend_type: str = "longhorn",
    csi_driver: str = "driver.longhorn.io",
) -> None:
    readiness = {
        "resource_management": "Ready",
        "data_management": "Ready",
        "inventory": "Ready",
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name=storage_name,
            backend_template={"backend_type": backend_type, "csi_driver": csi_driver},
            cluster_name=cluster_name,
            storage_class_name=storage_class_name,
            sanity_status="Ready",
        ),
        actor="admin",
        sanity_result={
            "storage_name": storage_name,
            "status": "Ready",
            "readiness": readiness,
            "kubernetes_observed": {
                "cluster_name": cluster_name,
                "storage_class_name": storage_class_name,
                "storage_class_exists": True,
                "provisioner": csi_driver,
            },
            "agent_observed": {
                "rm_readiness": "Ready",
                "dm_readiness": "Ready",
                "rm_candidates": [{"cluster_name": cluster_name, "node_name": "worker"}],
                "dm_candidates": [{"cluster_name": cluster_name, "node_name": "worker"}],
            },
            "checks": [],
            "warnings": [],
            "errors": [],
        },
        readiness=readiness,
    )


def seed_multi_resource(
    repository: DmsRepository, observed_used: dict[str, str] | None = None
) -> None:
    desired = {
        "cluster_name": "cluster-b",
        "namespace_name": "phase6-quota",
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
        "resource_quota_hard": multi_hard(),
        "expires_at": "2099-01-01T00:00:00Z",
    }
    repository.upsert_resource(
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key=RESOURCE_KEY,
        desired_state=desired,
        applied_state={"resource_quota": {"spec_hard": multi_hard()}},
        observed_state={
            "resource_quota": {
                "exists": True,
                "spec_hard": multi_hard(),
                "status_hard": multi_hard(),
                "status_used": observed_used
                or {"requests.storage": "0", "persistentvolumeclaims": "0"},
            }
        },
        status=LifecycleState.SUCCEEDED.value,
    )


def create_request(
    repository: DmsRepository, operation: OperationKind, payload: dict[str, Any]
) -> str:
    merged = {
        "cluster_name": "cluster-b",
        "namespace_name": "phase6-quota",
        "quota": {"requests_storage_bytes": 1024**3, "pvc_count": 20},
        **payload,
    }
    if operation == OperationKind.K8S_QUOTA_CREATE:
        merged.setdefault("expires_at", "2099-01-01T00:00:00Z")
    return repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=operation.value,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key=RESOURCE_KEY,
        payload=merged,
    )


def multi_hard(*, storage: str = "1Gi", pvc_count: str = "20") -> dict[str, str]:
    return {
        "requests.storage": storage,
        "persistentvolumeclaims": pvc_count,
        sc_key(LONGHORN_CLASS, "requests.storage"): "512Mi",
        sc_key(LONGHORN_CLASS, "persistentvolumeclaims"): "10",
        sc_key(STATIC_CLASS, "requests.storage"): "256Mi",
        sc_key(STATIC_CLASS, "persistentvolumeclaims"): "4",
    }


def sc_key(storage_class_name: str, resource_name: str) -> str:
    return f"{storage_class_name}.storageclass.storage.k8s.io/{resource_name}"
