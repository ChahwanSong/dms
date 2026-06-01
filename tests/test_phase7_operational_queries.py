from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import LifecycleState, OperationKind, ResourceKind, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository


LONGHORN = "longhorn-b"
LONGHORN_CLASS = "testbed-longhorn"
STATIC = "longhorn-static-b"
STATIC_CLASS = "longhorn-static"
RESOURCE_KEY = "cluster-b:phase7-quota"
API_HEADERS = {"x-dms-actor": "api-client"}


@pytest.fixture()
def harness(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    observability = ObservabilityRepository(observability_db)
    adapter = FakeQuotaAdapter()
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
    )
    app = create_app(
        settings,
        repository,
        observability,
        kubernetes_quota=adapter,
    )
    return {
        "repository": repository,
        "observability": observability,
        "adapter": adapter,
        "client": TestClient(app),
    }


@dataclass
class FakeQuotaAdapter:
    resource_quotas: dict[tuple[str, str, str], dict[str, Any]] = field(
        default_factory=dict
    )
    resource_quota_lists: dict[tuple[str, str], list[dict[str, Any]]] = field(
        default_factory=dict
    )

    def read_namespace(self, cluster_name: str, namespace_name: str) -> dict[str, Any]:
        return {"cluster_name": cluster_name, "namespace_name": namespace_name, "exists": True}

    def read_resource_quota(
        self,
        cluster_name: str,
        namespace_name: str,
        resource_quota_name: str = "dms-storage-quota",
    ) -> dict[str, Any]:
        return self.resource_quotas.get(
            (cluster_name, namespace_name, resource_quota_name),
            {
                "exists": False,
                "cluster_name": cluster_name,
                "namespace": namespace_name,
                "name": resource_quota_name,
            },
        )

    def list_resource_quotas(
        self, cluster_name: str, namespace_name: str
    ) -> list[dict[str, Any]]:
        return self.resource_quota_lists.get((cluster_name, namespace_name), [])


def test_phase7_quota_query_returns_db_live_diff_and_effective_warning(harness):
    repository = harness["repository"]
    adapter = harness["adapter"]
    seed_multi_resource(repository)
    live_quota = live_resource_quota(multi_hard())
    adapter.resource_quotas[("cluster-b", "phase7-quota", "dms-storage-quota")] = live_quota
    adapter.resource_quota_lists[("cluster-b", "phase7-quota")] = [
        live_quota,
        {
            "exists": True,
            "cluster_name": "cluster-b",
            "namespace": "phase7-quota",
            "name": "team-admin-quota",
            "spec_hard": {sc_key(LONGHORN_CLASS, "requests.storage"): "128Mi"},
            "status_hard": {},
            "status_used": {},
        },
    ]

    response = harness["client"].get(
        "/api/v1/operations/kubernetes/namespace-quotas/cluster-b/phase7-quota"
        "?include_non_dms=true",
        headers=API_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["db"]["exists"] is True
    assert body["live"]["exists"] is True
    assert body["diff"] == {"status": "Consistent", "issues": []}
    assert body["effective_quota_warnings"] == [
        {
            "type": "non_dms_quota_more_restrictive",
            "resource_quota_name": "team-admin-quota",
            "key": sc_key(LONGHORN_CLASS, "requests.storage"),
            "dms_hard": "512Mi",
            "non_dms_hard": "128Mi",
        }
    ]
    assert body["live"]["usage_summary"]["requests.storage"]["percent_used"] == 25.0


def test_phase7_quota_query_reports_drift_and_missing(harness):
    repository = harness["repository"]
    adapter = harness["adapter"]
    seed_multi_resource(repository)
    drifted = live_resource_quota(
        {**multi_hard(), sc_key(LONGHORN_CLASS, "requests.storage"): "768Mi"}
    )
    adapter.resource_quotas[("cluster-b", "phase7-quota", "dms-storage-quota")] = drifted

    drift_response = harness["client"].get(
        "/api/v1/operations/kubernetes/namespace-quotas/cluster-b/phase7-quota",
        headers=API_HEADERS,
    )
    assert drift_response.status_code == 200
    drift_body = drift_response.json()
    assert drift_body["diff"]["status"] == "Drifted"
    assert drift_body["diff"]["issues"] == [
        {
            "field": "spec.hard",
            "key": sc_key(LONGHORN_CLASS, "requests.storage"),
            "reason": "hard_limit_drifted",
            "desired": "512Mi",
            "live": "768Mi",
        }
    ]

    adapter.resource_quotas.clear()
    missing_response = harness["client"].get(
        "/api/v1/operations/kubernetes/namespace-quotas/cluster-b/phase7-quota",
        headers=API_HEADERS,
    )
    assert missing_response.status_code == 200
    assert missing_response.json()["diff"]["status"] == "Missing"


def test_phase7_quota_query_db_source_does_not_require_live_adapter(harness):
    seed_multi_resource(harness["repository"])

    response = harness["client"].get(
        "/api/v1/operations/kubernetes/namespace-quotas/cluster-b/phase7-quota"
        "?source=db",
        headers=API_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["db"]["exists"] is True
    assert body["live"] == {"queried": False, "exists": False}
    assert body["diff"] == {"status": "DbOnly", "issues": []}


def test_phase7_blocked_update_updates_restore_hard_but_keeps_live_hard_zero(harness):
    repository = harness["repository"]
    register_mapping(repository, LONGHORN, LONGHORN_CLASS)
    register_mapping(repository, STATIC, STATIC_CLASS)
    seed_multi_resource(repository, blocked=True)
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
                    "pvc_count": 8,
                },
            ],
        },
    )

    assert Planner(repository).run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert plan["desired_state"]["resource_quota_hard"] == {
        key: "0" for key in updated_hard()
    }
    assert plan["desired_state"]["block_state"]["restore_hard"] == updated_hard()
    assert plan["desired_state"]["block_state"]["updated_while_blocked"] is True


def test_phase7_blocked_update_decrease_guard_uses_restore_hard(harness):
    repository = harness["repository"]
    register_mapping(repository, LONGHORN, LONGHORN_CLASS)
    register_mapping(repository, STATIC, STATIC_CLASS)
    seed_multi_resource(
        repository,
        blocked=True,
        observed_used={sc_key(STATIC_CLASS, "requests.storage"): "192Mi"},
    )
    request_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_UPDATE,
        {
            "quota": {"requests_storage_bytes": 1024**3, "pvc_count": 20},
            "storage_class_quotas": [
                {
                    "storage_name": LONGHORN,
                    "requests_storage_bytes": 512 * 1024**2,
                    "pvc_count": 10,
                },
                {
                    "storage_name": STATIC,
                    "requests_storage_bytes": 128 * 1024**2,
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
        STATIC_CLASS, "requests.storage"
    )


def test_phase7_unblock_restores_latest_blocked_update_target(harness):
    repository = harness["repository"]
    register_mapping(repository, LONGHORN, LONGHORN_CLASS)
    register_mapping(repository, STATIC, STATIC_CLASS)
    seed_multi_resource(repository, blocked=True)
    update_id = create_request(
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
                    "pvc_count": 8,
                },
            ],
        },
    )
    Planner(repository).run_once()
    update_plan = repository.get_plan_by_request(update_id)
    repository.complete_result(
        request_id=update_id,
        plan_id=update_plan["plan_id"],
        run_id=None,
        terminal_status=LifecycleState.SUCCEEDED,
        message="recorded blocked update",
        verification_summary={"backend_side_effect": True},
        actor="test",
    )
    repository.upsert_resource(
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key=RESOURCE_KEY,
        desired_state=update_plan["desired_state"],
        applied_state={"resource_quota": {"spec_hard": update_plan["desired_state"]["resource_quota_hard"]}},
        observed_state={
            "resource_quota": {
                "exists": True,
                "spec_hard": update_plan["desired_state"]["resource_quota_hard"],
                "status_hard": update_plan["desired_state"]["resource_quota_hard"],
                "status_used": {"requests.storage": "0", "persistentvolumeclaims": "0"},
            }
        },
        status=LifecycleState.SUCCEEDED.value,
    )

    unblock_id = create_request(
        repository,
        OperationKind.K8S_QUOTA_BLOCK,
        {"block": False, "reason": "restore latest"},
    )
    Planner(repository).run_once()

    unblock_plan = repository.get_plan_by_request(unblock_id)
    assert unblock_plan["desired_state"]["resource_quota_hard"] == updated_hard()


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
    observed_used: dict[str, str] | None = None,
) -> None:
    hard = {key: "0" for key in multi_hard()} if blocked else multi_hard()
    desired = {
        "cluster_name": "cluster-b",
        "namespace_name": "phase7-quota",
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
        "namespace_name": "phase7-quota",
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


def live_resource_quota(hard: dict[str, str]) -> dict[str, Any]:
    return {
        "exists": True,
        "cluster_name": "cluster-b",
        "namespace": "phase7-quota",
        "name": "dms-storage-quota",
        "resource_version": "12345",
        "labels": {"app.kubernetes.io/managed-by": "dms"},
        "annotations": {},
        "spec_hard": hard,
        "status_hard": hard,
        "status_used": {
            "requests.storage": "256Mi",
            "persistentvolumeclaims": "2",
            sc_key(LONGHORN_CLASS, "requests.storage"): "128Mi",
            sc_key(STATIC_CLASS, "requests.storage"): "128Mi",
        },
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


def updated_hard() -> dict[str, str]:
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
