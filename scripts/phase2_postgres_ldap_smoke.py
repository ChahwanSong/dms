from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi.testclient import TestClient

from dms.adapters import (
    StubFilesystemBackendAdapter,
    StubKubernetesNamespaceQuotaAdapter,
    StubVolcanoAdapter,
)
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState, LifecycleState, StorageMappingInput
from dms.planner import Planner
from dms.workers import DMWorkerRuntime, RMWorkerRuntime


def main() -> int:
    settings = Settings.from_env()
    app = create_app(settings)
    services = app.state.services
    client = TestClient(app)
    headers = {"x-dms-actor": "api-client"}
    token = uuid4().hex[:8]

    assert_true(settings.observability_is_separate, "observability DB must be separate")
    assert_true(_table_exists(settings.database_url, "requests"), "operational requests table")
    assert_true(
        not _table_exists(settings.database_url, "diagnostic_events"),
        "operational DB must not contain diagnostic_events",
    )
    assert_true(
        _table_exists(settings.observability_database_url, "diagnostic_events"),
        "observability diagnostic_events table",
    )

    before_requests = len(services.repository.list_requests(limit=1000))
    auth_failure = client.post(
        "/api/v1/resource-management/filesystems",
        json=_filesystem_body(token, "auth"),
    )
    assert_equal(auth_failure.status_code, 401, "auth failure status")
    assert_equal(
        len(services.repository.list_requests(limit=1000)),
        before_requests,
        "auth failure must not create operational request",
    )
    assert_true(
        any(event["event_type"] == "authentication_rejected" for event in services.observability.list_events()),
        "auth failure diagnostic event",
    )

    authz_failure = client.post(
        "/api/v1/resource-management/filesystems",
        json=_filesystem_body(token, "authz"),
        headers={"x-dms-actor": "blocked"},
    )
    assert_equal(authz_failure.status_code, 403, "authz failure status")
    authz_request_id = authz_failure.json()["detail"]["request_id"]
    authz_request = services.repository.get_request(authz_request_id)
    assert_equal(
        authz_request["status"],
        LifecycleState.AUTHORIZATION_FAILED.value,
        "authz failure terminal state",
    )
    assert_true(
        services.repository.get_plan_by_request(authz_request_id) is None,
        "authz failure must not create plan",
    )

    _register_ready_storage_mapping(services.repository)

    fs_response = client.post(
        "/api/v1/resource-management/filesystems",
        json=_filesystem_body(token, "rm"),
        headers=headers,
    )
    assert_equal(fs_response.status_code, 202, "filesystem create submit")
    fs_request_id = fs_response.json()["request_id"]
    assert_equal(Planner(services.repository).run_once(), 1, "RM planner run")
    rm_worker = RMWorkerRuntime(
        repository=services.repository,
        observability=services.observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="phase2-rm",
        lease_seconds=settings.worker_lease_seconds,
    )
    assert_equal(rm_worker.run_once(), 1, "RM worker run")
    assert_equal(
        services.repository.get_request(fs_request_id)["status"],
        LifecycleState.SUCCEEDED.value,
        "filesystem request succeeded",
    )

    data_response = client.post(
        "/api/v1/data-management/scan",
        json={
            "requester_id": "portal:alice",
            "storage_name": "weka-a",
            "target_path": f"phase2-scan-{token}",
        },
        headers=headers,
    )
    assert_equal(data_response.status_code, 202, "data scan submit")
    data_request_id = data_response.json()["request_id"]
    assert_equal(Planner(services.repository).run_once(), 1, "DM planner run")
    dm_worker = DMWorkerRuntime(
        repository=services.repository,
        observability=services.observability,
        volcano_adapter=StubVolcanoAdapter(),
        worker_id="phase2-dm",
        lease_seconds=settings.worker_lease_seconds,
        preview_ttl_seconds=settings.preview_ttl_seconds,
    )
    assert_equal(dm_worker.run_once(), 1, "DM worker run")
    data_job = services.repository.get_data_job_by_request(data_request_id)
    assert_equal(data_job["state"], DataJobState.SUCCEEDED.value, "data scan succeeded")

    history = client.get(f"/api/v1/operations/requests/{fs_request_id}", headers=headers)
    assert_equal(history.status_code, 200, "request history query")
    assert_true(history.json()["results"], "request history contains results")

    alice = _put_identity(
        client,
        headers,
        requester_id=f"portal:alice:{token}",
        posix_username="alice",
        expected_uid=10000,
        expected_gid=10000,
        expected_groups=["developers"],
    )
    assert_equal(alice["status"], "Active", "alice mapping active")
    alice_mapping = alice["mapping"]
    assert_equal(alice_mapping["uid"], 10000, "alice LDAP uid")
    assert_equal(alice_mapping["gid"], 10000, "alice LDAP primary gid")
    assert_true("developers" in alice_mapping["groups"], "alice developers group")
    assert_true(
        alice_mapping["ldap_source_metadata"].get("adapter") == "ldap3-direct",
        "alice mapping uses direct LDAP adapter",
    )

    bob = _put_identity(
        client,
        headers,
        requester_id=f"portal:bob:{token}",
        posix_username="bob",
        expected_uid=10001,
        expected_gid=10000,
        expected_groups=["developers"],
    )
    assert_equal(bob["status"], "Active", "bob mapping active")

    mismatch = _put_identity(
        client,
        headers,
        requester_id=f"portal:mismatch:{token}",
        posix_username="alice",
        expected_uid=99999,
        expected_gid=10000,
        expected_groups=["developers"],
    )
    assert_equal(mismatch["status"], "NeedsReview", "mismatch is NeedsReview")

    missing = client.put(
        f"/api/v1/identity-mappings/ldap-main/portal:missing:{token}",
        headers=headers,
        json={
            "requester_id": f"portal:missing:{token}",
            "identity_provider": "ldap-main",
            "posix_username": f"missing-{token}",
        },
    )
    assert_equal(missing.status_code, 404, "missing LDAP user rejected")

    refreshed = client.post(
        f"/api/v1/identity-mappings/ldap-main/portal:alice:{token}:refresh",
        headers=headers,
    )
    assert_equal(refreshed.status_code, 200, "alice refresh status")
    assert_equal(refreshed.json()["status"], "Active", "alice refresh remains active")
    assert_true(refreshed.json()["mapping"]["verified_at"], "alice verified_at present")

    disabled = client.post(
        f"/api/v1/identity-mappings/ldap-main/portal:bob:{token}:disable",
        headers=headers,
        json={"reason": "phase2 smoke"},
    )
    assert_equal(disabled.status_code, 200, "bob disable")
    disabled_refresh = client.post(
        f"/api/v1/identity-mappings/ldap-main/portal:bob:{token}:refresh",
        headers=headers,
    )
    assert_equal(disabled_refresh.status_code, 200, "disabled refresh")
    assert_equal(
        disabled_refresh.json()["status"],
        "Disabled",
        "disabled refresh must not reactivate",
    )

    failed = client.get("/api/v1/identity-mappings?failed=true", headers=headers)
    assert_equal(failed.status_code, 200, "failed identity query")
    failed_ids = {item["requester_id"] for item in failed.json()}
    assert_true(f"portal:mismatch:{token}" in failed_ids, "failed query includes mismatch")
    assert_true(f"portal:bob:{token}" in failed_ids, "failed query includes disabled")

    summary = {
        "status": "ok",
        "operational_database_url": _mask_url(settings.database_url),
        "observability_database_url": _mask_url(settings.observability_database_url),
        "ldap_uri": settings.ldap_uri,
        "token": token,
        "filesystem_request_id": fs_request_id,
        "data_request_id": data_request_id,
        "alice_mapping_status": alice["status"],
        "bob_mapping_status_after_disable_refresh": disabled_refresh.json()["status"],
        "mismatch_mapping_status": mismatch["status"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _put_identity(
    client: TestClient,
    headers: dict[str, str],
    *,
    requester_id: str,
    posix_username: str,
    expected_uid: int,
    expected_gid: int,
    expected_groups: list[str],
) -> dict:
    response = client.put(
        f"/api/v1/identity-mappings/ldap-main/{requester_id}",
        headers=headers,
        json={
            "requester_id": requester_id,
            "identity_provider": "ldap-main",
            "posix_username": posix_username,
            "expected_uid": expected_uid,
            "expected_primary_gid": expected_gid,
            "expected_groups": expected_groups,
        },
    )
    assert_equal(response.status_code, 200, f"identity upsert {requester_id}")
    return response.json()


def _filesystem_body(token: str, suffix: str) -> dict:
    return {
        "requester_id": "portal:alice",
        "payload": {
            "storage_name": "weka-a",
            "directory_name": f"phase2-{suffix}-{token}",
            "resource_type": "user",
            "quota": {"capacity_bytes": 1024, "file_count": 1000},
        },
    }


def _register_ready_storage_mapping(repository) -> None:
    sanity = {
        "storage_name": "weka-a",
        "status": "Ready",
        "checked_at": "2026-05-27T00:00:00+00:00",
        "kubernetes_observed": {
            "cluster_name": "cluster-a",
            "storage_class_name": "weka-sc",
            "storage_class_exists": True,
            "provisioner": "weka.csi.dms.test",
        },
        "agent_observed": {
            "fresh_reports": 2,
            "stale_reports": 0,
            "rm_readiness": "Ready",
            "dm_readiness": "Ready",
            "rm_candidates": [{"cluster_name": "cluster-a", "node_name": "rm-phase2"}],
            "dm_candidates": [{"cluster_name": "cluster-a", "node_name": "dm-phase2"}],
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
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name="weka-a",
            backend_template={"backend_type": "weka"},
            cluster_name="cluster-a",
            storage_class_name="weka-sc",
        ),
        actor="phase2-smoke",
        sanity_result=sanity,
        readiness=sanity["readiness"],
    )


def _table_exists(database_url: str, table_name: str) -> bool:
    scheme = urlsplit(database_url).scheme
    with Database(database_url).connect() as connection:
        if scheme == "sqlite":
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = ?
                  AND table_schema = ANY (current_schemas(false))
                LIMIT 1
                """,
                (table_name,),
            ).fetchone()
    return row is not None


def _mask_url(url: str) -> str:
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    credentials, host = parts.netloc.rsplit("@", 1)
    user = credentials.split(":", 1)[0]
    return urlunsplit((parts.scheme, f"{user}:***@{host}", parts.path, parts.query, parts.fragment))


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_true(value, label: str) -> None:
    if not value:
        raise AssertionError(label)


if __name__ == "__main__":
    raise SystemExit(main())
