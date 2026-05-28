from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from dms.adapters import (
    AdapterResult,
    StubFilesystemBackendAdapter,
    _sync_desired_from_resource_quota_hard,
)
from dms.db import Database
from dms.domain import LifecycleState, OperationKind, ResourceKind, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import RMWorkerRuntime


STORAGE_NAME = "longhorn-b"
STORAGE_CLASS = "testbed-longhorn"
RESOURCE_KEY = "cluster-b:phase5-quota"


@pytest.fixture()
def repository_pair(tmp_path):
    operational = Database(f"sqlite:///{tmp_path / 'operational.db'}")
    observability_db = Database(f"sqlite:///{tmp_path / 'observability.db'}")
    migrate_all(operational, observability_db)
    return DmsRepository(operational), ObservabilityRepository(observability_db)


@dataclass
class Phase5RecordingKubernetesAdapter:
    calls: list[tuple[str, str]] = field(default_factory=list)
    synced_hard: dict[str, str] = field(
        default_factory=lambda: {
            "requests.storage": "384Mi",
            "persistentvolumeclaims": "5",
            f"{STORAGE_CLASS}.storageclass.storage.k8s.io/requests.storage": "384Mi",
        }
    )

    def read_namespace(self, cluster_name: str, namespace_name: str) -> dict[str, Any]:
        return {"cluster_name": cluster_name, "namespace_name": namespace_name, "exists": True}

    def create_namespace(self, plan: dict[str, Any]) -> AdapterResult:
        return self.apply_resource_quota(plan)

    def apply_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append(("apply_resource_quota", plan["plan_id"]))
        hard = plan["desired_state"]["resource_quota_hard"]
        return AdapterResult(
            applied_state={
                "adapter": "phase5-recording",
                "operation": "resourcequota.apply",
                "hard": hard,
            },
            observed_state={
                "adapter": "phase5-recording",
                "verified": True,
                "resource_quota": {
                    "exists": True,
                    "spec_hard": hard,
                    "status_hard": hard,
                    "status_used": {"requests.storage": "64Mi", "persistentvolumeclaims": "1"},
                },
            },
            message="recorded apply",
        )

    def delete_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append(("delete_resource_quota", plan["plan_id"]))
        return AdapterResult(
            applied_state={"operation": "resourcequota.delete"},
            observed_state={
                "verified": True,
                "deleted": True,
                "resource_quota": {"exists": False},
            },
            message="recorded delete",
        )

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append(("sync_live_state", plan["plan_id"]))
        synced_desired = dict(plan["desired_state"])
        _sync_desired_from_resource_quota_hard(synced_desired, self.synced_hard)
        return AdapterResult(
            applied_state={
                "operation": "resourcequota.sync",
                "backend_side_effect": False,
                "synced_desired_state": synced_desired,
            },
            observed_state={
                "verified": True,
                "synced": True,
                "resource_quota": {
                    "exists": True,
                    "spec_hard": self.synced_hard,
                    "status_hard": self.synced_hard,
                    "status_used": {"requests.storage": "256Mi", "persistentvolumeclaims": "2"},
                },
            },
            message="recorded sync",
        )

    def check_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append(("check_resource_quota", plan["plan_id"]))
        return AdapterResult(
            applied_state={"operation": "resourcequota.check", "backend_side_effect": False},
            observed_state={
                "verified": False,
                "backend_side_effect": False,
                "resource_status": "Drifted",
                "consistency_status": "Drifted",
                "issues": [{"field": "spec.hard", "reason": "hard_limits_drifted"}],
            },
            message="recorded drift",
        )


def test_phase5_planner_allows_quota_increase(repository_pair):
    repository, _ = repository_pair
    register_mapping(repository)
    seed_resource(repository)
    request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_UPDATE,
        {
            "quota": {"requests_storage_bytes": 256 * 1024**2, "pvc_count": 4},
            "storage_class_quotas": [{"storage_name": STORAGE_NAME}],
        },
    )

    assert Planner(repository).run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert plan["desired_state"]["resource_quota_hard"] == {
        "requests.storage": "256Mi",
        "persistentvolumeclaims": "4",
        f"{STORAGE_CLASS}.storageclass.storage.k8s.io/requests.storage": "256Mi",
    }
    assert plan["execution_metadata"]["planner"] == "phase5"


def test_phase5_planner_rejects_decrease_below_observed_used(repository_pair):
    repository, _ = repository_pair
    register_mapping(repository)
    seed_resource(
        repository,
        observed_used={
            "requests.storage": "64Mi",
            "persistentvolumeclaims": "1",
            f"{STORAGE_CLASS}.storageclass.storage.k8s.io/requests.storage": "64Mi",
        },
    )
    request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_UPDATE,
        {
            "quota": {"requests_storage_bytes": 32 * 1024**2, "pvc_count": 4},
            "storage_class_quotas": [{"storage_name": STORAGE_NAME}],
        },
    )

    assert Planner(repository).run_once() == 1

    assert repository.get_plan_by_request(request_id) is None
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    assert result["verification_summary"]["backend_side_effect"] is False
    assert result["verification_summary"]["issues"][0]["reason"] == (
        "quota_decrease_below_live_used"
    )


def test_phase5_block_and_unblock_restore_hard_limits(repository_pair):
    repository, observability = repository_pair
    register_mapping(repository)
    seed_resource(repository)
    adapter = Phase5RecordingKubernetesAdapter()

    block_request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_BLOCK,
        {"block": True, "block_mode": "quota-zero", "reason": "test"},
    )
    Planner(repository).run_once()
    block_plan = repository.get_plan_by_request(block_request_id)
    assert block_plan["desired_state"]["resource_quota_hard"] == {
        "requests.storage": "0",
        "persistentvolumeclaims": "0",
        f"{STORAGE_CLASS}.storageclass.storage.k8s.io/requests.storage": "0",
    }
    assert block_plan["desired_state"]["block_state"]["restore_hard"][
        "requests.storage"
    ] == "128Mi"
    run_worker(repository, observability, adapter)

    unblock_request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_BLOCK,
        {"block": False, "reason": "restore"},
    )
    Planner(repository).run_once()
    unblock_plan = repository.get_plan_by_request(unblock_request_id)
    assert unblock_plan["desired_state"]["resource_quota_hard"]["requests.storage"] == "128Mi"
    assert unblock_plan["desired_state"]["block_state"]["blocked"] is False


def test_phase5_worker_dispatches_check_sync_and_delete(repository_pair):
    repository, observability = repository_pair
    register_mapping(repository)
    seed_resource(repository)
    adapter = Phase5RecordingKubernetesAdapter()

    check_id = create_request(repository, OperationKind.K8S_QUOTA_CHECK, {})
    Planner(repository).run_once()
    run_worker(repository, observability, adapter)
    assert repository.get_resource(
        ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value, RESOURCE_KEY
    )["status"] == "Drifted"

    sync_id = create_request(repository, OperationKind.K8S_QUOTA_SYNC, {})
    Planner(repository).run_once()
    run_worker(repository, observability, adapter)
    synced = repository.get_resource(ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value, RESOURCE_KEY)
    assert synced["desired_state"]["resource_quota_hard"]["requests.storage"] == "384Mi"
    assert synced["desired_state"]["quota"]["requests_storage_bytes"] == 384 * 1024**2
    assert synced["desired_state"]["quota"]["pvc_count"] == 5
    [storage_class_quota] = synced["desired_state"]["storage_class_quotas"]
    assert storage_class_quota["requests_storage_bytes"] == 384 * 1024**2

    delete_id = create_request(repository, OperationKind.K8S_QUOTA_DELETE, {})
    Planner(repository).run_once()
    delete_plan = repository.get_plan_by_request(delete_id)
    assert delete_plan["desired_state"]["resource_quota_hard"]["requests.storage"] == "384Mi"
    run_worker(repository, observability, adapter)
    deleted = repository.get_resource(ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value, RESOURCE_KEY)
    assert deleted["status"] == "Deleted"
    assert deleted["desired_state"]["resource_quota_hard"]["requests.storage"] == "384Mi"

    assert adapter.calls == [
        ("check_resource_quota", repository.get_plan_by_request(check_id)["plan_id"]),
        ("sync_live_state", repository.get_plan_by_request(sync_id)["plan_id"]),
        ("delete_resource_quota", delete_plan["plan_id"]),
    ]
    event_types = {event["event_type"] for event in observability.list_events(limit=20)}
    assert "kubernetes_resourcequota_consistency_check_completed" in event_types
    assert "kubernetes_resourcequota_sync_completed" in event_types
    assert "kubernetes_resourcequota_delete_completed" in event_types


def register_mapping(repository: DmsRepository) -> None:
    readiness = {
        "resource_management": "Ready",
        "data_management": "Ready",
        "inventory": "Ready",
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name=STORAGE_NAME,
            backend_template={"backend_type": "longhorn", "csi_driver": "driver.longhorn.io"},
            cluster_name="cluster-b",
            storage_class_name=STORAGE_CLASS,
            sanity_status="Ready",
        ),
        actor="admin",
        sanity_result={
            "storage_name": STORAGE_NAME,
            "status": "Ready",
            "readiness": readiness,
            "kubernetes_observed": {
                "cluster_name": "cluster-b",
                "storage_class_name": STORAGE_CLASS,
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


def seed_resource(
    repository: DmsRepository, observed_used: dict[str, str] | None = None
) -> None:
    hard = {
        "requests.storage": "128Mi",
        "persistentvolumeclaims": "2",
        f"{STORAGE_CLASS}.storageclass.storage.k8s.io/requests.storage": "128Mi",
    }
    desired = {
        "cluster_name": "cluster-b",
        "namespace_name": "phase5-quota",
        "resource_type": "user",
        "quota": {"requests_storage_bytes": 128 * 1024**2, "pvc_count": 2},
        "storage_class_quotas": [
            {
                "storage_name": STORAGE_NAME,
                "storage_class_name": STORAGE_CLASS,
                "cluster_name": "cluster-b",
            }
        ],
        "resource_kind": ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        "resource_key": RESOURCE_KEY,
        "resource_quota_name": "dms-storage-quota",
        "resource_quota_hard": hard,
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
                "status_used": observed_used
                or {"requests.storage": "0", "persistentvolumeclaims": "0"},
            }
        },
        status=LifecycleState.SUCCEEDED.value,
    )


def create_request(
    repository: DmsRepository, operation: OperationKind, payload: dict[str, Any]
) -> str:
    merged = {"cluster_name": "cluster-b", "namespace_name": "phase5-quota", **payload}
    return repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=operation.value,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key=RESOURCE_KEY,
        payload=merged,
    )


def run_worker(
    repository: DmsRepository,
    observability: ObservabilityRepository,
    adapter: Phase5RecordingKubernetesAdapter,
) -> None:
    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=adapter,
        worker_id="rm-cluster-b",
    )
    assert worker.run_once() == 1
