from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from dms.adapters import (
    AdapterResult,
    StubFilesystemBackendAdapter,
    kubernetes_quantity_from_bytes,
    render_kubernetes_resource_quota_hard,
)
from dms.db import Database
from dms.domain import LifecycleState, OperationKind, ResourceKind, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import RMWorkerRuntime


LONGHORN_STORAGE_NAME = "longhorn-b"
LONGHORN_STORAGE_CLASS = "testbed-longhorn"


@pytest.fixture()
def repository_pair(tmp_path):
    operational = Database(f"sqlite:///{tmp_path / 'operational.db'}")
    observability_db = Database(f"sqlite:///{tmp_path / 'observability.db'}")
    migrate_all(operational, observability_db)
    return DmsRepository(operational), ObservabilityRepository(observability_db)


@dataclass
class RecordingKubernetesQuotaAdapter:
    calls: list[tuple[str, str]] = field(default_factory=list)

    def read_namespace(self, cluster_name: str, namespace_name: str) -> dict[str, Any]:
        return {"cluster_name": cluster_name, "namespace_name": namespace_name}

    def create_namespace(self, plan: dict[str, Any]) -> AdapterResult:
        return self.apply_resource_quota(plan)

    def apply_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append(("apply_resource_quota", plan["plan_id"]))
        desired = plan["desired_state"]
        hard = desired["resource_quota_hard"]
        observed_quota = {
            "exists": True,
            "name": "dms-storage-quota",
            "namespace": desired["namespace_name"],
            "uid": "rq-test-uid",
            "resource_version": "42",
            "spec_hard": hard,
            "status_hard": hard,
            "status_used": {"persistentvolumeclaims": "0", "requests.storage": "0"},
        }
        return AdapterResult(
            applied_state={
                "adapter": "recording-kubernetes-quota",
                "manifest": {"spec": {"hard": hard}},
            },
            observed_state={
                "adapter": "recording-kubernetes-quota",
                "verified": True,
                "resource_quota": observed_quota,
            },
            message="Kubernetes ResourceQuota live apply completed",
        )

    def delete_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        raise NotImplementedError

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult:
        raise NotImplementedError


def test_kubernetes_resource_quota_hard_rendering():
    hard = render_kubernetes_resource_quota_hard(
        {
            "quota": {"requests_storage_bytes": 128 * 1024**2, "pvc_count": 2},
            "storage_class_quotas": [
                {
                    "storage_name": LONGHORN_STORAGE_NAME,
                    "storage_class_name": LONGHORN_STORAGE_CLASS,
                }
            ],
        }
    )

    assert hard == {
        "requests.storage": "128Mi",
        "persistentvolumeclaims": "2",
        "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "128Mi",
    }
    assert kubernetes_quantity_from_bytes(1024**3) == "1Gi"


def test_planner_derives_storage_class_and_resource_quota_hard(repository_pair):
    repository, _ = repository_pair
    register_longhorn_mapping(repository)
    request_id = create_quota_request(repository)

    assert Planner(repository).run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    desired = plan["desired_state"]
    assert desired["resource_quota_name"] == "dms-storage-quota"
    assert desired["storage_class_quotas"] == [
        {
            "storage_name": LONGHORN_STORAGE_NAME,
            "storage_class_name": LONGHORN_STORAGE_CLASS,
            "cluster_name": "cluster-b",
        }
    ]
    assert desired["resource_quota_hard"] == {
        "requests.storage": "128Mi",
        "persistentvolumeclaims": "2",
        "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "128Mi",
    }
    assert plan["execution_metadata"]["planner"] == "phase4"
    assert plan["execution_metadata"]["kubernetes_backend"]["cluster_name"] == "cluster-b"


def test_rm_worker_persists_resourcequota_observed_state(repository_pair):
    repository, observability = repository_pair
    register_longhorn_mapping(repository)
    request_id = create_quota_request(repository)
    Planner(repository).run_once()
    adapter = RecordingKubernetesQuotaAdapter()
    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=adapter,
        worker_id="rm-cluster-b",
    )

    assert worker.run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert adapter.calls == [("apply_resource_quota", plan["plan_id"])]
    [resource] = repository.list_resources()
    assert resource["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value
    assert resource["observed_state"]["resource_quota"]["uid"] == "rq-test-uid"
    assert resource["observed_state"]["resource_quota"]["status_hard"][
        "requests.storage"
    ] == "128Mi"
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.SUCCEEDED.value
    assert result["verification_summary"]["resource_quota"]["spec_hard"][
        "persistentvolumeclaims"
    ] == "2"
    event_types = {event["event_type"] for event in observability.list_events()}
    assert "kubernetes_resourcequota_apply_started" in event_types
    assert "kubernetes_resourcequota_apply_completed" in event_types


def test_planner_accepts_multiple_storage_class_quotas(repository_pair):
    repository, _ = repository_pair
    register_longhorn_mapping(repository)
    register_longhorn_static_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.K8S_QUOTA_CREATE.value,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key="cluster-b:phase4-quota",
        payload={
            "cluster_name": "cluster-b",
            "namespace_name": "phase4-quota",
            "quota": {"requests_storage_bytes": 128 * 1024**2, "pvc_count": 2},
            "storage_class_quotas": [
                {
                    "storage_name": "longhorn-b",
                    "requests_storage_bytes": 64 * 1024**2,
                    "pvc_count": 1,
                },
                {
                    "storage_name": "longhorn-static-b",
                    "requests_storage_bytes": 32 * 1024**2,
                    "pvc_count": 1,
                },
            ],
        },
    )

    assert Planner(repository).run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert plan["execution_metadata"]["planner"] == "phase6"
    assert plan["desired_state"]["resource_quota_hard"] == {
        "requests.storage": "128Mi",
        "persistentvolumeclaims": "2",
        "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "64Mi",
        "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "1",
        "longhorn-static.storageclass.storage.k8s.io/requests.storage": "32Mi",
        "longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims": "1",
    }


def register_longhorn_mapping(repository: DmsRepository) -> None:
    readiness = {
        "resource_management": "Ready",
        "data_management": "Ready",
        "inventory": "Ready",
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name=LONGHORN_STORAGE_NAME,
            backend_template={
                "backend_type": "longhorn",
                "csi_driver": "driver.longhorn.io",
            },
            cluster_name="cluster-b",
            storage_class_name=LONGHORN_STORAGE_CLASS,
            sanity_status="Ready",
        ),
        actor="admin",
        sanity_result={
            "storage_name": LONGHORN_STORAGE_NAME,
            "status": "Ready",
            "readiness": readiness,
            "kubernetes_observed": {
                "cluster_name": "cluster-b",
                "storage_class_name": LONGHORN_STORAGE_CLASS,
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


def register_longhorn_static_mapping(repository: DmsRepository) -> None:
    readiness = {
        "resource_management": "Ready",
        "data_management": "Ready",
        "inventory": "Ready",
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name="longhorn-static-b",
            backend_template={
                "backend_type": "longhorn",
                "csi_driver": "driver.longhorn.io",
            },
            cluster_name="cluster-b",
            storage_class_name="longhorn-static",
            sanity_status="Ready",
        ),
        actor="admin",
        sanity_result={
            "storage_name": "longhorn-static-b",
            "status": "Ready",
            "readiness": readiness,
            "kubernetes_observed": {
                "cluster_name": "cluster-b",
                "storage_class_name": "longhorn-static",
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


def create_quota_request(repository: DmsRepository) -> str:
    return repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.K8S_QUOTA_CREATE.value,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key="cluster-b:phase4-quota",
        payload={
            "cluster_name": "cluster-b",
            "namespace_name": "phase4-quota",
            "allow_namespace_create": True,
            "quota": {"requests_storage_bytes": 128 * 1024**2, "pvc_count": 2},
            "storage_class_quotas": [{"storage_name": LONGHORN_STORAGE_NAME}],
        },
    )
