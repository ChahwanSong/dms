"""DM identity: direct read-only LDAP lookup + DM-side denylist.

`identity_mappings` was removed. DM no longer caches an admission-gated mapping
table; instead it resolves the requester's POSIX identity by a READ-ONLY LDAP
lookup at preflight time (no cache -> LDAP down means the job fails closed). The
only DM-side identity state is a denylist: an instant per-requester / per-owner /
per-group kill-switch and admission block (default empty = allow all).

owner_username mirrors RM's filesystem owner model: requester_id is a free-form
logical id and is the DEFAULT for owner_username, but an explicit owner_username
(the real POSIX identity) overrides it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dms.adapters import IdentityLookupResult, StubIdentityLookupAdapter
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobRequest
from dms.migrations import migrate_all
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import DMWorkerRuntime

PROVIDER = "ldap-main"


def _identity(username: str, uid: int, gid: int, groups: list[str]) -> IdentityLookupResult:
    return IdentityLookupResult(
        provider=PROVIDER,
        posix_username=username,
        uid=uid,
        primary_gid=gid,
        groups=groups,
        user_dn=f"uid={username},ou=people,dc=testbed,dc=local",
        source_metadata={"adapter": "test-direct-ldap", "read_only": True},
    )


def _alice_lookup() -> StubIdentityLookupAdapter:
    return StubIdentityLookupAdapter(
        mappings={
            (PROVIDER, "alice"): _identity(
                "alice", 10000, 10000, ["developers", "dms-users"]
            )
        }
    )


class _RaisingLookup:
    """Direct-LDAP adapter stand-in whose lookup always fails (LDAP unreachable)."""

    def lookup(self, provider: str, posix_username: str) -> IdentityLookupResult | None:
        raise RuntimeError("ldap connection refused")


def _harness(tmp_path, lookup=None):
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
    worker = DMWorkerRuntime(
        repository=repository,
        observability=observability,
        volcano_adapter=None,
        worker_id="dm-test",
        identity_lookup=lookup,
        identity_provider=PROVIDER,
    )
    return {
        "client": TestClient(app),
        "repository": repository,
        "observability": observability,
        "worker": worker,
    }


def _request(requester_id: str, owner_username: str | None = None) -> dict:
    payload_summary: dict = {}
    if owner_username is not None:
        payload_summary["owner_username"] = owner_username
    return {"requester_id": requester_id, "payload_summary": payload_summary}


# --------------------------------------------------------------------------- #
# DM-side identity denylist API (the only persisted DM identity state)
# --------------------------------------------------------------------------- #
_DENYLIST = "/api/v1/data-management/identity-denylist"
_HEADERS = {"x-dms-actor": "operator"}


def test_denylist_add_list_remove(tmp_path):
    client = _harness(tmp_path)["client"]

    added = client.put(
        f"{_DENYLIST}/requester/portal:eve",
        headers=_HEADERS,
        json={"reason": "offboarded"},
    )
    assert added.status_code == 200
    assert added.json()["status"] == "Denied"

    listed = client.get(_DENYLIST, headers=_HEADERS)
    assert listed.status_code == 200
    assert [(e["subject_type"], e["subject"]) for e in listed.json()] == [
        ("requester", "portal:eve")
    ]
    assert listed.json()[0]["reason"] == "offboarded"

    removed = client.delete(f"{_DENYLIST}/requester/portal:eve", headers=_HEADERS)
    assert removed.status_code == 200
    assert removed.json()["status"] == "Allowed"
    assert client.get(_DENYLIST, headers=_HEADERS).json() == []


def test_denylist_remove_missing_is_404(tmp_path):
    removed = _harness(tmp_path)["client"].delete(
        f"{_DENYLIST}/requester/ghost", headers=_HEADERS
    )
    assert removed.status_code == 404


def test_denylist_rejects_unknown_subject_type(tmp_path):
    response = _harness(tmp_path)["client"].put(
        f"{_DENYLIST}/banana/whoever", headers=_HEADERS, json={"reason": "x"}
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Preflight identity resolution via direct read-only LDAP lookup
# --------------------------------------------------------------------------- #
def test_resolve_identity_owner_defaults_to_requester(tmp_path):
    worker = _harness(tmp_path, _alice_lookup())["worker"]
    mapping = worker._resolve_identity(_request("alice"))
    assert mapping["status"] == "Resolved"
    assert mapping["requester_id"] == "alice"
    assert mapping["posix_username"] == "alice"
    assert mapping["uid"] == 10000
    assert mapping["gid"] == 10000
    assert mapping["identity_provider"] == PROVIDER
    assert mapping["groups"] == ["developers", "dms-users"]
    assert mapping["user_dn"].startswith("uid=alice")


def test_resolve_identity_owner_username_overrides_requester(tmp_path):
    worker = _harness(tmp_path, _alice_lookup())["worker"]
    # requester_id is a free-form logical id; the real POSIX identity is alice.
    mapping = worker._resolve_identity(
        _request("portal:team-batch", owner_username="alice")
    )
    assert mapping["status"] == "Resolved"
    assert mapping["requester_id"] == "portal:team-batch"
    assert mapping["owner_username"] == "alice"
    assert mapping["posix_username"] == "alice"
    assert mapping["uid"] == 10000


def test_resolve_identity_user_not_found(tmp_path):
    worker = _harness(tmp_path, _alice_lookup())["worker"]
    mapping = worker._resolve_identity(_request("nobody"))
    assert mapping["status"] == "Rejected"
    assert mapping["reason"] == "ldap_identity_not_found"


def test_resolve_identity_ldap_not_configured(tmp_path):
    worker = _harness(tmp_path, lookup=None)["worker"]
    mapping = worker._resolve_identity(_request("alice"))
    assert mapping["status"] == "Rejected"
    assert mapping["reason"] == "ldap_not_configured"


def test_resolve_identity_ldap_unavailable_fails_closed(tmp_path):
    # No TTL cache: any LDAP/transport failure must reject (fail closed), not serve stale.
    worker = _harness(tmp_path, _RaisingLookup())["worker"]
    mapping = worker._resolve_identity(_request("alice"))
    assert mapping["status"] == "Rejected"
    assert mapping["reason"] == "ldap_unavailable"
    assert "ldap connection refused" in mapping["error"]


def test_resolve_identity_denied_requester_is_killswitch(tmp_path):
    h = _harness(tmp_path, _alice_lookup())
    h["repository"].add_dm_denylist_entry(
        subject_type="requester", subject="alice", reason="kill-switch", actor="operator"
    )
    mapping = h["worker"]._resolve_identity(_request("alice"))
    assert mapping["status"] == "Rejected"
    assert mapping["reason"] == "identity_denied"
    assert mapping["denylist"]["subject_type"] == "requester"


def test_resolve_identity_denied_owner(tmp_path):
    h = _harness(tmp_path, _alice_lookup())
    h["repository"].add_dm_denylist_entry(
        subject_type="owner", subject="alice", reason="kill-switch", actor="operator"
    )
    mapping = h["worker"]._resolve_identity(
        _request("portal:logical", owner_username="alice")
    )
    assert mapping["status"] == "Rejected"
    assert mapping["reason"] == "identity_denied"
    assert mapping["denylist"]["subject_type"] == "owner"


def test_resolve_identity_denied_group_after_lookup(tmp_path):
    h = _harness(tmp_path, _alice_lookup())
    # The group is only known AFTER LDAP resolves alice -> [developers, dms-users],
    # so a group denylist is enforced post-lookup.
    h["repository"].add_dm_denylist_entry(
        subject_type="group", subject="developers", reason="quarantine", actor="operator"
    )
    mapping = h["worker"]._resolve_identity(_request("alice"))
    assert mapping["status"] == "Rejected"
    assert mapping["reason"] == "identity_denied"
    assert mapping["denylist"]["subject"] == "developers"


# --------------------------------------------------------------------------- #
# Hardening: uid/gid floor, case-insensitive kill-switch, owner_username charset
# --------------------------------------------------------------------------- #
def test_resolve_identity_rejects_system_uid_root(tmp_path):
    # A data job runs as the resolved user via runuser in a ROOT MPI pod; an LDAP entry
    # mapping to uid 0 (root) must be refused, else the file op would run as root.
    lookup = StubIdentityLookupAdapter(
        mappings={(PROVIDER, "svc"): _identity("svc", 0, 0, ["wheel"])}
    )
    mapping = _harness(tmp_path, lookup)["worker"]._resolve_identity(_request("svc"))
    assert mapping["status"] == "Rejected"
    assert mapping["reason"] == "uid_below_floor"
    assert mapping["uid"] == 0


def test_resolve_identity_rejects_below_floor_uid(tmp_path):
    lookup = StubIdentityLookupAdapter(
        mappings={(PROVIDER, "daemonish"): _identity("daemonish", 500, 500, ["svc"])}
    )
    mapping = _harness(tmp_path, lookup)["worker"]._resolve_identity(_request("daemonish"))
    assert mapping["status"] == "Rejected"
    assert mapping["reason"] == "uid_below_floor"


def test_denylist_owner_match_is_case_insensitive(tmp_path):
    # kill-switch must not be bypassable by casing: deny `alice`, request owner `Alice`.
    h = _harness(tmp_path, _alice_lookup())
    h["repository"].add_dm_denylist_entry(
        subject_type="owner", subject="alice", reason="kill-switch", actor="operator"
    )
    mapping = h["worker"]._resolve_identity(
        _request("portal:logical", owner_username="Alice")
    )
    assert mapping["status"] == "Rejected"
    assert mapping["reason"] == "identity_denied"


def test_denylist_match_ignores_surrounding_whitespace(tmp_path):
    h = _harness(tmp_path, _alice_lookup())
    h["repository"].add_dm_denylist_entry(
        subject_type="requester", subject="  alice  ", reason="x", actor="operator"
    )
    # stored stripped -> "alice"; a requester with stray spaces still matches.
    mapping = h["worker"]._resolve_identity(_request(" alice "))
    assert mapping["status"] == "Rejected"
    assert mapping["reason"] == "identity_denied"


def test_owner_username_must_be_posix_username():
    # explicit owner_username is validated at the API boundary (no whitespace/metachars).
    DataJobRequest(requester_id="portal:team", owner_username="cocoa.song")  # ok
    for bad in ["alice; rm -rf /", "has space", "bad/slash", "$(whoami)", ""]:
        with pytest.raises(ValidationError):
            DataJobRequest(requester_id="portal:team", owner_username=bad)
