"""Regression tests for k8s namespace-quota RM fixes (2026-06-15).

K1: a manual :block must set the DB lifecycle status to "Blocked"
    (apply_resource_quota now emits observed_state.resource_status from block_state).
K4: create with a missing cluster_name/namespace_name returns HTTP 422, not 500.
K5: create on an already-existing (non-Deleted) resource is Rejected
    (kubernetes_namespace_quota_resource_already_exists), like import.
K7: create accepts quota OR storage_class_quotas (quota no longer mandatory);
    a request with no hard key at all is Rejected (kubernetes_quota_hard_key_required).
(K2/K3 import issues are intentionally out of scope for this change.)
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from dms.adapters import (
    KubernetesNamespaceQuotaLiveAdapter,
    StubKubernetesNamespaceQuotaAdapter,
)
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import OperationKind, ResourceKind, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository

CLUSTER = "cluster-b"
NS = "k8sfix-ns"
RKEY = f"{CLUSTER}:{NS}"
SC_STORAGE = "longhorn-b"
SC_CLASS = "testbed-longhorn"
HEADERS = {"x-dms-actor": "api-client"}


@pytest.fixture()
def repos(tmp_path):
    operational = Database(f"sqlite:///{tmp_path / 'op.db'}")
    observability = Database(f"sqlite:///{tmp_path / 'obs.db'}")
    migrate_all(operational, observability)
    return DmsRepository(operational), ObservabilityRepository(observability)


def register_mapping(repo: DmsRepository) -> None:
    readiness = {
        "resource_management": "Ready",
        "data_management": "Ready",
        "inventory": "Ready",
    }
    repo.upsert_storage_mapping(
        StorageMappingInput(
            storage_name=SC_STORAGE,
            backend_template={
                "backend_type": "longhorn",
                "csi_driver": "driver.longhorn.io",
            },
            cluster_name=CLUSTER,
            storage_class_name=SC_CLASS,
            sanity_status="Ready",
        ),
        actor="admin",
        sanity_result={
            "storage_name": SC_STORAGE,
            "status": "Ready",
            "readiness": readiness,
            "kubernetes_observed": {
                "cluster_name": CLUSTER,
                "storage_class_name": SC_CLASS,
                "storage_class_exists": True,
                "provisioner": "driver.longhorn.io",
            },
            "agent_observed": {
                "rm_readiness": "Ready",
                "dm_readiness": "Ready",
                "rm_candidates": [{"cluster_name": CLUSTER, "node_name": "worker"}],
                "dm_candidates": [],
            },
            "checks": [],
            "warnings": [],
            "errors": [],
        },
    )


def create_req(
    repo: DmsRepository,
    payload: dict[str, Any],
    operation: OperationKind = OperationKind.K8S_QUOTA_CREATE,
) -> str:
    base: dict[str, Any] = {"cluster_name": CLUSTER, "namespace_name": NS}
    if operation == OperationKind.K8S_QUOTA_CREATE:
        base["expires_at"] = "2099-01-01T00:00:00Z"
    base.update(payload)
    return repo.create_request(
        requester_id="alice",
        actor="api-client",
        operation=operation.value,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key=RKEY,
        payload=base,
    )


def reasons(repo: DmsRepository, request_id: str) -> list[str]:
    out: list[str] = []
    for result in repo.get_results(request_id):
        for issue in (result.get("verification_summary") or {}).get("issues") or []:
            out.append(issue["reason"])
    return out


def seed_resource(repo: DmsRepository, status: str) -> None:
    repo.upsert_resource(
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key=RKEY,
        desired_state={"cluster_name": CLUSTER, "namespace_name": NS},
        applied_state={},
        observed_state={},
        status=status,
    )


# --------------------------------------------------------------------------- #
# K7 — quota OR storage_class_quotas
# --------------------------------------------------------------------------- #
def test_k7_create_allows_storage_class_only(repos):
    repo, _ = repos
    register_mapping(repo)
    rid = create_req(
        repo,
        {
            "quota": None,
            "storage_class_quotas": [
                {
                    "storage_name": SC_STORAGE,
                    "requests_storage_bytes": 512 * 1024**2,
                    "pvc_count": 10,
                }
            ],
        },
    )
    assert Planner(repo).run_once() == 1
    plan = repo.get_plan_by_request(rid)
    assert plan is not None, f"unexpected reject: {reasons(repo, rid)}"
    hard = plan["desired_state"]["resource_quota_hard"]
    assert any("storageclass.storage.k8s.io/requests.storage" in k for k in hard)
    assert "requests.storage" not in hard  # no namespace-wide quota rendered


def test_k7_create_allows_quota_only(repos):
    repo, _ = repos
    rid = create_req(repo, {"quota": {"requests_storage_bytes": 1024**3, "pvc_count": 5}})
    assert Planner(repo).run_once() == 1
    plan = repo.get_plan_by_request(rid)
    assert plan is not None, f"unexpected reject: {reasons(repo, rid)}"
    assert plan["desired_state"]["resource_quota_hard"]["requests.storage"] == "1Gi"


def test_k7_create_rejects_no_hard_key(repos):
    repo, _ = repos
    register_mapping(repo)
    rid = create_req(
        repo,
        {"quota": None, "storage_class_quotas": [{"storage_name": SC_STORAGE}]},
    )
    assert Planner(repo).run_once() == 1
    assert repo.get_plan_by_request(rid) is None
    assert "kubernetes_quota_hard_key_required" in reasons(repo, rid)


# --------------------------------------------------------------------------- #
# K5 — duplicate create rejected
# --------------------------------------------------------------------------- #
def test_k5_create_rejects_existing_resource(repos):
    repo, _ = repos
    seed_resource(repo, status="Succeeded")
    rid = create_req(repo, {"quota": {"requests_storage_bytes": 1024**3, "pvc_count": 5}})
    assert Planner(repo).run_once() == 1
    assert repo.get_plan_by_request(rid) is None
    assert "kubernetes_namespace_quota_resource_already_exists" in reasons(repo, rid)


def test_k5_create_allowed_after_deleted(repos):
    repo, _ = repos
    seed_resource(repo, status="Deleted")
    rid = create_req(repo, {"quota": {"requests_storage_bytes": 1024**3, "pvc_count": 5}})
    assert Planner(repo).run_once() == 1
    assert repo.get_plan_by_request(rid) is not None  # recreate after delete is OK


# --------------------------------------------------------------------------- #
# K4 — missing cluster_name/namespace_name -> 422 (not 500)
# --------------------------------------------------------------------------- #
def _client(tmp_path, repos) -> TestClient:
    repo, obs = repos
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'op.db'}",
        observability_database_url=f"sqlite:///{tmp_path / 'obs.db'}",
    )
    app = create_app(
        settings, repo, obs, kubernetes_quota=StubKubernetesNamespaceQuotaAdapter()
    )
    return TestClient(app)


def test_k4_create_missing_namespace_name_returns_422(repos, tmp_path):
    client = _client(tmp_path, repos)
    resp = client.post(
        "/api/v1/resource-management/kubernetes/namespace-quotas",
        headers=HEADERS,
        json={
            "requester_id": "alice",
            "payload": {
                "cluster_name": CLUSTER,
                "quota": {"requests_storage_bytes": 1024**3, "pvc_count": 1},
                "expires_at": "2099-01-01T00:00:00Z",
            },
        },
    )
    assert resp.status_code == 422


def test_k4_create_missing_cluster_name_returns_422(repos, tmp_path):
    client = _client(tmp_path, repos)
    resp = client.post(
        "/api/v1/resource-management/kubernetes/namespace-quotas",
        headers=HEADERS,
        json={
            "requester_id": "alice",
            "payload": {
                "namespace_name": NS,
                "quota": {"requests_storage_bytes": 1024**3, "pvc_count": 1},
                "expires_at": "2099-01-01T00:00:00Z",
            },
        },
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# K1 — manual :block sets resource_status="Blocked"
# --------------------------------------------------------------------------- #
class _FakeK8sLiveAdapter(KubernetesNamespaceQuotaLiveAdapter):
    """Live adapter with the kubectl-touching methods stubbed out."""

    def _ensure_namespace(self, **_kwargs):
        return {"cluster_name": CLUSTER, "namespace_name": NS, "exists": True}

    def read_resource_quota(self, *_args, **_kwargs):
        return {"exists": False}

    def _resource_quota_manifest(self, **_kwargs):
        return {"kind": "ResourceQuota", "metadata": {}, "spec": {"hard": {}}}

    def _kubectl(self, *_args, **_kwargs):
        return None

    def _read_resource_quota(self, **_kwargs):
        return {"exists": True, "spec_hard": {"requests.storage": "1Gi"}}


def _apply_plan(blocked: bool | None) -> dict[str, Any]:
    desired: dict[str, Any] = {
        "cluster_name": CLUSTER,
        "namespace_name": NS,
        "resource_quota_name": "dms-storage-quota",
        "resource_quota_hard": {"requests.storage": "1Gi"},
    }
    if blocked is not None:
        desired["block_state"] = {"blocked": blocked}
    return {
        "plan_id": "p",
        "request_id": "r",
        "resource_key": RKEY,
        "desired_state": desired,
    }


def test_k1_block_sets_resource_status_blocked():
    result = _FakeK8sLiveAdapter().apply_resource_quota(_apply_plan(True))
    assert result.observed_state["resource_status"] == "Blocked"


def test_k1_unblock_sets_resource_status_succeeded():
    result = _FakeK8sLiveAdapter().apply_resource_quota(_apply_plan(False))
    assert result.observed_state["resource_status"] == "Succeeded"


def test_k1_normal_apply_status_succeeded():
    result = _FakeK8sLiveAdapter().apply_resource_quota(_apply_plan(None))
    assert result.observed_state["resource_status"] == "Succeeded"
