from __future__ import annotations

from fastapi.testclient import TestClient

from dms.adapters import IdentityLookupResult, StubIdentityLookupAdapter
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.migrations import migrate_all
from dms.repositories import DmsRepository, ObservabilityRepository


def _identity(provider: str, username: str, uid: int, gid: int, groups: list[str]):
    return IdentityLookupResult(
        provider=provider,
        posix_username=username,
        uid=uid,
        primary_gid=gid,
        groups=groups,
        user_dn=f"uid={username},ou=people,dc=testbed,dc=local",
        source_metadata={
            "adapter": "test-direct-ldap",
            "read_only": True,
            "user_dn": f"uid={username},ou=people,dc=testbed,dc=local",
        },
    )


def _harness(tmp_path, lookup: StubIdentityLookupAdapter | None = None):
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
        worker_lease_seconds=300,
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    observability = ObservabilityRepository(observability_db)
    app = create_app(settings, repository, observability, identity_lookup=lookup)
    return TestClient(app), repository


def test_identity_mapping_uses_lookup_result_not_request_payload(tmp_path):
    lookup = StubIdentityLookupAdapter(
        mappings={
            ("ldap-main", "alice"): _identity(
                "ldap-main", "alice", 10000, 10000, ["developers", "k8s-admins"]
            )
        }
    )
    client, repository = _harness(tmp_path, lookup)

    response = client.put(
        "/api/v1/identity-mappings/ldap-main/portal:alice",
        headers={"x-dms-actor": "api-client"},
        json={
            "requester_id": "portal:alice",
            "identity_provider": "ldap-main",
            "posix_username": "alice",
            "expected_uid": 10000,
            "expected_primary_gid": 10000,
            "expected_groups": ["developers"],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "Active"
    mapping = repository.get_identity_mapping("portal:alice", "ldap-main")
    assert mapping["uid"] == 10000
    assert mapping["gid"] == 10000
    assert mapping["groups"] == ["developers", "k8s-admins"]
    assert mapping["verification_result"] == "matched"
    assert mapping["ldap_source_metadata"]["read_only"] is True


def test_identity_mapping_mismatch_is_needs_review_and_queryable(tmp_path):
    lookup = StubIdentityLookupAdapter(
        mappings={
            ("ldap-main", "alice"): _identity(
                "ldap-main", "alice", 10000, 10000, ["developers"]
            )
        }
    )
    client, repository = _harness(tmp_path, lookup)

    response = client.put(
        "/api/v1/identity-mappings/ldap-main/portal:alice",
        headers={"x-dms-actor": "api-client"},
        json={
            "requester_id": "portal:alice",
            "identity_provider": "ldap-main",
            "posix_username": "alice",
            "expected_uid": 99999,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "NeedsReview"
    mapping = repository.get_identity_mapping("portal:alice", "ldap-main")
    assert mapping["status"] == "NeedsReview"
    assert "expected uid 99999" in mapping["mismatch_reason"]

    failed = client.get(
        "/api/v1/identity-mappings?failed=true",
        headers={"x-dms-actor": "api-client"},
    )
    assert failed.status_code == 200
    assert [item["requester_id"] for item in failed.json()] == ["portal:alice"]


def test_identity_mapping_refresh_marks_drift_stale_without_overwriting(tmp_path):
    lookup = StubIdentityLookupAdapter(
        mappings={
            ("ldap-main", "bob"): _identity("ldap-main", "bob", 10001, 10000, ["developers"])
        }
    )
    client, repository = _harness(tmp_path, lookup)
    body = {
        "requester_id": "portal:bob",
        "identity_provider": "ldap-main",
        "posix_username": "bob",
    }
    assert client.put(
        "/api/v1/identity-mappings/ldap-main/portal:bob",
        headers={"x-dms-actor": "api-client"},
        json=body,
    ).status_code == 200

    lookup.mappings[("ldap-main", "bob")] = _identity(
        "ldap-main", "bob", 20001, 10000, ["developers"]
    )
    response = client.post(
        "/api/v1/identity-mappings/ldap-main/portal:bob:refresh",
        headers={"x-dms-actor": "api-client"},
    )

    assert response.status_code == 200
    mapping = repository.get_identity_mapping("portal:bob", "ldap-main")
    assert mapping["status"] == "Stale"
    assert mapping["uid"] == 10001
    assert "uid changed from 10001 to 20001" in mapping["mismatch_reason"]


def test_disabled_identity_mapping_is_not_reactivated_by_refresh(tmp_path):
    lookup = StubIdentityLookupAdapter(
        mappings={
            ("ldap-main", "bob"): _identity("ldap-main", "bob", 10001, 10000, ["developers"])
        }
    )
    client, repository = _harness(tmp_path, lookup)
    body = {
        "requester_id": "portal:bob",
        "identity_provider": "ldap-main",
        "posix_username": "bob",
    }
    client.put(
        "/api/v1/identity-mappings/ldap-main/portal:bob",
        headers={"x-dms-actor": "api-client"},
        json=body,
    )

    disabled = client.post(
        "/api/v1/identity-mappings/ldap-main/portal:bob:disable",
        headers={"x-dms-actor": "api-client"},
        json={"reason": "operator request"},
    )
    assert disabled.status_code == 200
    refreshed = client.post(
        "/api/v1/identity-mappings/ldap-main/portal:bob:refresh",
        headers={"x-dms-actor": "api-client"},
    )

    assert refreshed.status_code == 200
    mapping = repository.get_identity_mapping("portal:bob", "ldap-main")
    assert mapping["status"] == "Disabled"
    assert mapping["disabled_reason"] == "operator request"


def test_identity_mapping_requires_configured_direct_lookup(tmp_path):
    client, _repository = _harness(tmp_path, lookup=None)

    response = client.put(
        "/api/v1/identity-mappings/ldap-main/portal:alice",
        headers={"x-dms-actor": "api-client"},
        json={
            "requester_id": "portal:alice",
            "identity_provider": "ldap-main",
            "posix_username": "alice",
        },
    )

    assert response.status_code == 503
    assert "direct LDAP identity lookup" in response.json()["detail"]
