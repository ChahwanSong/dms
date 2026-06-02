from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dms.adapters import (
    IdentityLookupResult,
    StaticKubernetesReadOnlyInventoryAdapter,
    StubIdentityLookupAdapter,
    StubKubernetesNamespaceQuotaAdapter,
)
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState, OperationKind, ResourceKind, WorkerRole
from dms.migrations import migrate_all
from dms.repositories import DmsRepository, ObservabilityRepository


MTLS_SUBJECT = "CN=portal,O=example"
MTLS_ACTOR = f"mtls:{MTLS_SUBJECT}"
MTLS_DMS_HEADERS = {
    "x-dms-client-cert-subject": MTLS_SUBJECT,
    "x-dms-client-cert-verify": "SUCCESS",
}
MTLS_NGINX_HEADERS = {
    "ssl-client-subject-dn": MTLS_SUBJECT,
    "ssl-client-verify": "SUCCESS",
}


class FailingObservabilityRepository(ObservabilityRepository):
    def record_event(self, **_: Any) -> str:
        raise RuntimeError("observability database unavailable")


def test_existing_dev_actor_header_still_works_when_mtls_not_required(tmp_path):
    harness = _harness(tmp_path)

    response = harness["client"].post(
        "/api/v1/resource-management/filesystems",
        json=_filesystem_body(),
        headers={"x-dms-actor": "api-client"},
    )

    assert response.status_code == 202
    [request] = harness["repository"].list_requests(requester_id="user-1")
    assert request["actor"] == "api-client"


def test_shared_token_accepts_correct_bearer_token(tmp_path):
    harness = _harness(tmp_path, auth_shared_token="secret-token")

    response = harness["client"].get(
        "/api/v1/operations/action-required",
        headers={
            "authorization": "Bearer secret-token",
            "x-dms-actor": "api-client",
        },
    )

    assert response.status_code == 200


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "bearer secret-token"])
def test_shared_token_rejects_missing_or_wrong_bearer_token(tmp_path, authorization):
    harness = _harness(tmp_path, auth_shared_token="secret-token")
    headers = {"x-dms-actor": "api-client"}
    if authorization is not None:
        headers["authorization"] = authorization

    response = harness["client"].get(
        "/api/v1/operations/action-required",
        headers=headers,
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"


def test_mtls_required_without_evidence_rejects_without_operational_request(tmp_path):
    harness = _harness(tmp_path, require_mtls_header=True)

    response = harness["client"].post(
        "/api/v1/resource-management/filesystems",
        json=_filesystem_body(),
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "mtls_subject_required"
    assert harness["repository"].list_requests(requester_id="user-1") == []
    [event] = harness["observability"].list_events()
    assert event["event_type"] == "authentication_rejected"
    assert event["message"] == "mtls_subject_required"


def test_dms_edge_mtls_headers_derive_actor(tmp_path):
    harness = _harness(
        tmp_path,
        auth_shared_token="secret-token",
        require_mtls_header=True,
        require_mtls_verified_header=True,
    )

    response = harness["client"].post(
        "/api/v1/resource-management/filesystems",
        json=_filesystem_body(),
        headers={
            **MTLS_DMS_HEADERS,
            "authorization": "Bearer secret-token",
        },
    )

    assert response.status_code == 202
    [request] = harness["repository"].list_requests(requester_id="user-1")
    assert request["actor"] == MTLS_ACTOR


def test_ingress_nginx_mtls_headers_derive_actor(tmp_path):
    harness = _harness(tmp_path, require_mtls_header=True)

    response = harness["client"].get(
        "/api/v1/operations/action-required",
        headers=MTLS_NGINX_HEADERS,
    )

    assert response.status_code == 200


@pytest.mark.parametrize("verify", ["FAILED", "NONE", "", "PENDING"])
def test_mtls_verify_failure_rejects_when_verified_header_required(tmp_path, verify):
    harness = _harness(
        tmp_path,
        require_mtls_header=True,
        require_mtls_verified_header=True,
    )

    response = harness["client"].get(
        "/api/v1/operations/action-required",
        headers={
            "x-dms-client-cert-subject": MTLS_SUBJECT,
            "x-dms-client-cert-verify": verify,
        },
    )

    assert response.status_code == 401
    expected = "mtls_verify_required" if verify == "" else "mtls_verify_failed"
    assert response.json()["detail"] == expected


def test_mtls_verify_missing_rejects_when_verified_header_required(tmp_path):
    harness = _harness(
        tmp_path,
        require_mtls_header=True,
        require_mtls_verified_header=True,
    )

    response = harness["client"].get(
        "/api/v1/operations/action-required",
        headers={"x-dms-client-cert-subject": MTLS_SUBJECT},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "mtls_verify_required"


def test_conflicting_mtls_header_families_reject(tmp_path):
    harness = _harness(tmp_path, require_mtls_header=True)

    response = harness["client"].get(
        "/api/v1/operations/action-required",
        headers={
            **MTLS_DMS_HEADERS,
            "ssl-client-subject-dn": "CN=other,O=example",
            "ssl-client-verify": "SUCCESS",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "mtls_evidence_conflict"


def test_mtls_required_ignores_default_actor(tmp_path):
    harness = _harness(
        tmp_path,
        default_actor="fallback-actor",
        require_mtls_header=True,
    )

    response = harness["client"].get("/api/v1/operations/action-required")

    assert response.status_code == 401
    assert response.json()["detail"] == "mtls_subject_required"


def test_mtls_required_rejects_mismatched_x_dms_actor(tmp_path):
    harness = _harness(tmp_path, require_mtls_header=True)

    response = harness["client"].get(
        "/api/v1/operations/action-required",
        headers={**MTLS_DMS_HEADERS, "x-dms-actor": "api-client"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "actor_evidence_conflict"


def test_mtls_required_accepts_matching_x_dms_actor(tmp_path):
    harness = _harness(tmp_path, require_mtls_header=True)

    response = harness["client"].get(
        "/api/v1/operations/action-required",
        headers={**MTLS_DMS_HEADERS, "x-dms-actor": MTLS_ACTOR},
    )

    assert response.status_code == 200


def test_mtls_auth_rejection_survives_observability_write_failure(tmp_path, caplog):
    harness = _harness(
        tmp_path,
        require_mtls_header=True,
        observability_repository=FailingObservabilityRepository,
    )

    with caplog.at_level(logging.WARNING, logger="dms.repositories"):
        response = harness["client"].get("/api/v1/operations/action-required")

    assert response.status_code == 401
    assert "observability event write failed" in caplog.text


def test_healthz_remains_unauthenticated(tmp_path):
    harness = _harness(
        tmp_path,
        auth_shared_token="secret-token",
        require_mtls_header=True,
        require_mtls_verified_header=True,
    )

    response = harness["client"].get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_agent_report_endpoint_uses_phase16_auth_boundary(tmp_path):
    harness = _harness(
        tmp_path,
        auth_shared_token="secret-token",
        require_mtls_header=True,
        require_mtls_verified_header=True,
    )

    no_mtls = harness["client"].post(
        "/api/v1/agent/reports",
        json=_agent_report(),
        headers={"authorization": "Bearer secret-token"},
    )
    with_mtls = harness["client"].post(
        "/api/v1/agent/reports",
        json=_agent_report(),
        headers={
            **MTLS_DMS_HEADERS,
            "authorization": "Bearer secret-token",
        },
    )

    assert no_mtls.status_code == 401
    assert no_mtls.json()["detail"] == "mtls_subject_required"
    assert with_mtls.status_code == 403
    assert with_mtls.json()["detail"] == "node identity mismatch"


def test_operational_query_endpoint_uses_phase16_auth_boundary(tmp_path):
    harness = _harness(
        tmp_path,
        auth_shared_token="secret-token",
        require_mtls_header=True,
        require_mtls_verified_header=True,
    )

    missing = harness["client"].get(
        "/api/v1/operations/action-required",
        headers={"authorization": "Bearer secret-token"},
    )
    allowed = harness["client"].get(
        "/api/v1/operations/action-required",
        headers={
            **MTLS_NGINX_HEADERS,
            "authorization": "Bearer secret-token",
        },
    )

    assert missing.status_code == 401
    assert allowed.status_code == 200


def test_all_protected_api_endpoints_reach_handler_with_mtls_auth(tmp_path):
    harness = _harness(
        tmp_path,
        auth_shared_token="secret-token",
        require_mtls_header=True,
        require_mtls_verified_header=True,
    )
    repository = harness["repository"]
    seeded_request_id = repository.create_request(
        requester_id="phase16-all",
        actor=MTLS_ACTOR,
        operation=OperationKind.FILESYSTEM_CHECK.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="cephfs-a:seeded",
        payload={"storage_name": "cephfs-a", "directory_name": "seeded"},
    )
    confirm_job_id = _seed_data_job(repository, state=DataJobState.CONFIRM_PENDING)
    cancel_job_id = _seed_data_job(repository, state=DataJobState.PENDING)
    headers = {**MTLS_DMS_HEADERS, "authorization": "Bearer secret-token"}

    cases: list[tuple[str, str, dict[str, Any] | None, set[int]]] = [
        (
            "POST",
            "/api/v1/resource-management/storage-mappings",
            {
                "storage_name": "phase16-mtls-storage",
                "backend_template": {"backend_type": "cephfs"},
            },
            {200},
        ),
        ("POST", "/api/v1/resource-management/storage-mappings/phase16-mtls-storage:check", None, {200}),
        (
            "POST",
            "/api/v1/resource-management/default-quota-policies",
            {
                "resource_kind": ResourceKind.FILESYSTEM.value,
                "resource_type": "user",
                "quota": {"capacity_bytes": 1024},
            },
            {200},
        ),
        (
            "PUT",
            "/api/v1/identity-mappings/ldap-main/phase16-user",
            {
                "requester_id": "phase16-user",
                "identity_provider": "ldap-main",
                "posix_username": "alice",
                "expected_uid": 10000,
                "expected_primary_gid": 10000,
            },
            {200},
        ),
        ("POST", "/api/v1/identity-mappings/ldap-main/phase16-user:refresh", None, {200}),
        ("POST", "/api/v1/identity-mappings/ldap-main/phase16-user:disable", {"reason": "phase16"}, {200}),
        ("GET", "/api/v1/identity-mappings", None, {200}),
        (
            "POST",
            "/api/v1/resource-management/requests",
            {
                "requester_id": "phase16-all",
                "operation": OperationKind.FILESYSTEM_CHECK.value,
                "resource_kind": ResourceKind.FILESYSTEM.value,
                "resource_key": "cephfs-a:generic",
                "payload": {"storage_name": "cephfs-a", "directory_name": "generic"},
            },
            {202},
        ),
        ("POST", "/api/v1/resource-management/filesystems", _filesystem_body(), {202}),
        ("PATCH", "/api/v1/resource-management/filesystems/cephfs-a/fs-update", _mutating_body({"expires_at": "2099-01-01T00:00:00Z"}), {202}),
        ("POST", "/api/v1/resource-management/filesystems/cephfs-a/fs-block:block", _mutating_body({"block": True}), {202}),
        ("POST", "/api/v1/resource-management/filesystems/cephfs-a/fs-init:initialize", _mutating_body({"quota": {"capacity_bytes": 1024}}), {202}),
        ("DELETE", "/api/v1/resource-management/filesystems/cephfs-a/fs-delete", _mutating_body({}), {202}),
        ("POST", "/api/v1/resource-management/filesystems/cephfs-a/fs-assign:assign-quota", _mutating_body({"quota": {"capacity_bytes": 1024}}), {202}),
        ("POST", "/api/v1/resource-management/filesystems/cephfs-a/fs-import:import", _mutating_body({"expires_at": "2099-01-01T00:00:00Z"}), {202}),
        ("POST", "/api/v1/resource-management/filesystems/cephfs-a/fs-check:check", _mutating_body({}), {202}),
        ("POST", "/api/v1/resource-management/filesystems/cephfs-a/fs-sync:sync", _mutating_body({}), {202}),
        ("POST", "/api/v1/resource-management/filesystems:expiration-sweep", _mutating_body({"dry_run": True}), {202}),
        (
            "POST",
            "/api/v1/resource-management/kubernetes/namespace-quotas",
            _mutating_body(_kubernetes_quota_payload("phase16-create")),
            {202},
        ),
        ("PATCH", "/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/phase16-update", _mutating_body({"expires_at": "2099-01-01T00:00:00Z"}), {202}),
        ("POST", "/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/phase16-block:block", _mutating_body({"block": True}), {202}),
        ("DELETE", "/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/phase16-delete", _mutating_body({}), {202}),
        ("POST", "/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/phase16-sync:sync", _mutating_body({}), {202}),
        ("POST", "/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/phase16-import:import", _mutating_body({"expires_at": "2099-01-01T00:00:00Z"}), {202}),
        ("POST", "/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/phase16-check:check", _mutating_body({}), {202}),
        ("POST", "/api/v1/resource-management/kubernetes/namespace-quotas:expiration-sweep", _mutating_body({"dry_run": True}), {202}),
        ("POST", "/api/v1/resource-management/kubernetes/namespace-quotas:audit", _mutating_body({"scope": {"cluster_name": "cluster-a"}}), {202}),
        ("POST", "/api/v1/data-management/sync", _data_job_body(source_path="src", destination_path="dst"), {202}),
        ("POST", "/api/v1/data-management/rm", _data_job_body(target_path="target"), {202}),
        ("POST", "/api/v1/data-management/scan", _data_job_body(target_path="target"), {202}),
        ("POST", f"/api/v1/data-management/jobs/{confirm_job_id}:confirm", None, {200}),
        ("POST", f"/api/v1/data-management/jobs/{cancel_job_id}:cancel", None, {200}),
        ("POST", "/api/v1/agent/reports", _agent_report(), {403}),
        ("GET", "/api/v1/operations/action-required", None, {200}),
        ("GET", "/api/v1/operations/inventory", None, {200}),
        ("GET", "/api/v1/operations/agent-reports", None, {200}),
        ("GET", "/api/v1/operations/storage-mappings", None, {200}),
        ("GET", "/api/v1/operations/storage-mappings/phase16-mtls-storage", None, {200}),
        ("GET", "/api/v1/operations/requests?requester_id=phase16-all&limit=20", None, {200}),
        ("GET", f"/api/v1/operations/requests/{seeded_request_id}", None, {200}),
        ("GET", "/api/v1/operations/resources", None, {200}),
        ("GET", "/api/v1/operations/filesystems/expiring?status=expired", None, {200}),
        ("GET", "/api/v1/operations/kubernetes/namespace-quotas/cluster-a/phase16-create", None, {200}),
        ("GET", "/api/v1/operations/kubernetes/namespace-quotas/expiring?status=expired", None, {200}),
        ("GET", "/api/v1/operations/runs/stale", None, {200}),
        ("GET", "/api/v1/operations/worker-agent-health", None, {200}),
        ("GET", "/api/v1/operations/identity-issues", None, {200}),
        ("GET", "/api/v1/operations/data-jobs", None, {200}),
        ("GET", f"/api/v1/operations/data-jobs/{cancel_job_id}", None, {200}),
        ("GET", f"/api/v1/operations/diagnostics/{seeded_request_id}", None, {200}),
    ]

    for method, path, body, expected_statuses in cases:
        response = harness["client"].request(method, path, json=body, headers=headers)
        assert response.status_code in expected_statuses, (
            f"{method} {path} returned {response.status_code}: {response.text}"
        )
        assert response.status_code != 401, f"{method} {path} was blocked by auth"

    auth_rejections = [
        event
        for event in harness["observability"].list_events()
        if event["event_type"] == "authentication_rejected"
    ]
    assert auth_rejections == []


def test_data_management_help_is_currently_unauthenticated(tmp_path):
    harness = _harness(
        tmp_path,
        auth_shared_token="secret-token",
        require_mtls_header=True,
        require_mtls_verified_header=True,
    )

    response = harness["client"].get("/api/v1/data-management/help")

    assert response.status_code == 200


def test_settings_from_env_rejects_verified_without_required_mtls(monkeypatch):
    monkeypatch.setenv("DMS_REQUIRE_MTLS_HEADER", "false")
    monkeypatch.setenv("DMS_REQUIRE_MTLS_VERIFIED_HEADER", "true")

    with pytest.raises(ValueError, match="DMS_REQUIRE_MTLS_VERIFIED_HEADER"):
        Settings.from_env()


def test_settings_from_env_rejects_default_actor_with_required_mtls(monkeypatch):
    monkeypatch.setenv("DMS_REQUIRE_MTLS_HEADER", "true")
    monkeypatch.setenv("DMS_DEFAULT_ACTOR", "fallback-actor")

    with pytest.raises(ValueError, match="DMS_DEFAULT_ACTOR"):
        Settings.from_env()


def test_settings_from_env_accepts_empty_default_actor_with_required_mtls(monkeypatch):
    monkeypatch.setenv("DMS_REQUIRE_MTLS_HEADER", "true")
    monkeypatch.setenv("DMS_REQUIRE_MTLS_VERIFIED_HEADER", "true")
    monkeypatch.setenv("DMS_DEFAULT_ACTOR", "")

    settings = Settings.from_env()

    assert settings.require_mtls_header is True
    assert settings.require_mtls_verified_header is True
    assert settings.default_actor == ""


def _harness(
    tmp_path,
    *,
    auth_shared_token: str | None = None,
    default_actor: str | None = None,
    require_mtls_header: bool = False,
    require_mtls_verified_header: bool = False,
    observability_repository: type[ObservabilityRepository] = ObservabilityRepository,
) -> dict[str, Any]:
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
        auth_shared_token=auth_shared_token,
        default_actor=default_actor,
        require_mtls_header=require_mtls_header,
        require_mtls_verified_header=require_mtls_verified_header,
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    observability = observability_repository(observability_db)
    lookup = StubIdentityLookupAdapter(
        mappings={
            ("ldap-main", "alice"): IdentityLookupResult(
                provider="ldap-main",
                posix_username="alice",
                uid=10000,
                primary_gid=10000,
                groups=["dms-users"],
                user_dn="uid=alice,ou=people,dc=example,dc=internal",
                source_metadata={"adapter": "phase16-test"},
            )
        }
    )
    app = create_app(
        settings,
        repository,
        observability,
        identity_lookup=lookup,
        kubernetes_inventory=StaticKubernetesReadOnlyInventoryAdapter({"clusters": {}}),
        kubernetes_quota=StubKubernetesNamespaceQuotaAdapter(),
    )
    return {
        "settings": settings,
        "repository": repository,
        "observability": observability,
        "client": TestClient(app),
    }


def _filesystem_body() -> dict[str, Any]:
    return {
        "requester_id": "user-1",
        "payload": {
            "storage_name": "cephfs-a",
            "directory_name": "phase16-auth",
            "resource_type": "user",
            "users": ["alice", "bob"],
            "expires_at": "2099-01-01T00:00:00Z",
        },
    }


def _agent_report() -> dict[str, Any]:
    return {
        "schema_version": "phase8.v1",
        "reported_at": "2026-06-02T00:00:00+00:00",
        "cluster_name": "cluster-a",
        "node_name": "c1-worker",
        "node_uid": "uid-c1-worker",
        "worker_role": "RM",
        "mounts": [],
        "csi": [],
        "tools": [],
        "credentials": [],
        "networks": [],
        "identity_evidence": {"source": "phase16-test"},
    }


def _mutating_body(payload: dict[str, Any]) -> dict[str, Any]:
    return {"requester_id": "phase16-all", "payload": payload}


def _kubernetes_quota_payload(namespace_name: str) -> dict[str, Any]:
    return {
        "cluster_name": "cluster-a",
        "namespace_name": namespace_name,
        "storage_class_quotas": [{"storage_name": "phase16-mtls-storage"}],
        "quota": {"requests_storage_bytes": 1024, "pvc_count": 1},
        "expires_at": "2099-01-01T00:00:00Z",
    }


def _data_job_body(
    *,
    source_path: str | None = None,
    destination_path: str | None = None,
    target_path: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "requester_id": "phase16-all",
        "storage_name": "cephfs-a",
    }
    if source_path is not None:
        body["source_path"] = source_path
    if destination_path is not None:
        body["destination_path"] = destination_path
    if target_path is not None:
        body["target_path"] = target_path
    return body


def _seed_data_job(repository: DmsRepository, *, state: DataJobState) -> str:
    request_id = repository.create_request(
        requester_id="phase16-all",
        actor=MTLS_ACTOR,
        operation=OperationKind.DATA_SYNC.value,
        resource_kind=ResourceKind.DATA_JOB.value,
        resource_key="cephfs-a:data.sync:src:dst:",
        payload={
            "storage_name": "cephfs-a",
            "source_path": "src",
            "destination_path": "dst",
            "priority": 100,
        },
    )
    job_id = repository.create_data_job(
        request_id=request_id,
        operation=OperationKind.DATA_SYNC.value,
        storage_name="cephfs-a",
        source="src",
        destination="dst",
        target=None,
        priority=100,
        worker_pool={},
        state=state,
    )
    repository.create_plan(
        request_id=request_id,
        worker_role=WorkerRole.DM,
        operation_kind=OperationKind.DATA_SYNC.value,
        resource_key="cephfs-a:data.sync:src:dst:",
        desired_state={"storage_name": "cephfs-a"},
        precondition={"job_id": job_id},
        execution_metadata={"job_id": job_id, "phase": "preview"},
    )
    return job_id
