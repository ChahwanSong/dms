"""Privileged (root) DM execution: requester_id=root runs as uid/gid 0.

Default OFF (identical to no-feature behavior). When DMS_DM_ALLOW_ROOT_REQUESTER is
on, a request whose effective owner_username/requester_id is a privileged requester
(default {"root"}) resolves to a SYNTHESIZED root identity (uid/gid 0), bypassing the
LDAP lookup and the uid floor. The caller is authorized at the API edge: mTLS-verified
operator only, optional operator allowlist, confined to DMS_DM_PRIVILEGED_SCOPES. The
denylist still applies as a kill-switch.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from dms.adapters import StubIdentityLookupAdapter
from dms.adapters.volcano import (
    _container_security_context,
    _pod_security_context,
)
from dms.workers.dm import _identity_ready
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.migrations import migrate_all
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import DMWorkerRuntime


def _request(requester_id: str, owner_username: str | None = None) -> dict:
    payload_summary: dict = {}
    if owner_username is not None:
        payload_summary["owner_username"] = owner_username
    return {"requester_id": requester_id, "payload_summary": payload_summary}


def _worker(tmp_path, *, allow_root=False, requesters=frozenset({"root"}), lookup=None):
    operational_url = f"sqlite:///{tmp_path / 'op.db'}"
    observability_url = f"sqlite:///{tmp_path / 'obs.db'}"
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    return DMWorkerRuntime(
        repository=DmsRepository(operational),
        observability=ObservabilityRepository(observability_db),
        volcano_adapter=None,
        worker_id="dm-test",
        identity_lookup=lookup,
        identity_provider="ldap",
        allow_root_requester=allow_root,
        privileged_requesters=requesters,
    )


# --------------------------------------------------------------------------- #
# Worker identity synthesis (uid/gid 0), bypassing LDAP + floor
# --------------------------------------------------------------------------- #
def test_root_resolves_to_uid0_when_enabled(tmp_path):
    mapping = _worker(tmp_path, allow_root=True)._resolve_identity(_request("root"))
    assert mapping["status"] == "Resolved"
    assert mapping["privileged"] is True
    assert mapping["uid"] == 0 and mapping["gid"] == 0
    assert mapping["identity_provider"] == "privileged"


def test_root_rejected_when_disabled(tmp_path):
    # feature OFF -> no synthesis; falls through to the LDAP path (here unconfigured).
    mapping = _worker(tmp_path, allow_root=False)._resolve_identity(_request("root"))
    assert mapping["status"] == "Rejected"
    assert mapping["reason"] == "ldap_not_configured"


def test_root_denylist_killswitch_beats_privilege(tmp_path):
    worker = _worker(tmp_path, allow_root=True)
    worker.repository.add_dm_denylist_entry(
        subject_type="requester", subject="root", reason="kill", actor="op"
    )
    mapping = worker._resolve_identity(_request("root"))
    assert mapping["status"] == "Rejected"
    assert mapping["reason"] == "identity_denied"


def test_owner_username_root_overrides_logical_requester(tmp_path):
    mapping = _worker(tmp_path, allow_root=True)._resolve_identity(
        _request("portal:ops", owner_username="root")
    )
    assert mapping["status"] == "Resolved"
    assert mapping["privileged"] is True
    assert mapping["uid"] == 0


def test_non_privileged_user_still_uses_ldap_when_flag_on(tmp_path):
    # flag on, but a normal requester is NOT synthesized -> still LDAP (here: not found).
    worker = _worker(tmp_path, allow_root=True, lookup=StubIdentityLookupAdapter(mappings={}))
    mapping = worker._resolve_identity(_request("alice"))
    assert mapping["status"] == "Rejected"
    assert mapping["reason"] == "ldap_identity_not_found"


# --------------------------------------------------------------------------- #
# Candidate selection: a privileged (root) identity needs no agent identity evidence
# (root is universally resolvable), so it must not be filtered out -> no_ready_*_candidate.
# --------------------------------------------------------------------------- #
def test_identity_ready_privileged_bypasses_agent_evidence():
    # privileged synthesized identity: ready even with empty agent evidence
    assert _identity_ready({}, "root", privileged=True) is True
    # non-privileged: still requires the node to report the user
    assert _identity_ready({}, "root") is False
    assert _identity_ready(
        {"users": [{"username": "alice", "uid": 10000}]}, "alice"
    ) is True


# --------------------------------------------------------------------------- #
# Pod/container securityContext: uid 0 must drop runAsNonRoot
# --------------------------------------------------------------------------- #
def test_security_context_root_drops_run_as_nonroot():
    pre = {"identity_mapping": {"uid": 0, "gid": 0}}
    container = _container_security_context(pre)
    assert container["runAsUser"] == 0
    assert "runAsNonRoot" not in container
    assert container["allowPrivilegeEscalation"] is False
    assert _pod_security_context(pre) == {"fsGroup": 0}


def test_security_context_normal_user_keeps_run_as_nonroot():
    pre = {"identity_mapping": {"uid": 10000, "gid": 10000}}
    container = _container_security_context(pre)
    assert container["runAsNonRoot"] is True
    assert container["runAsUser"] == 10000
    assert _pod_security_context(pre) == {"fsGroup": 10000, "runAsNonRoot": True}


# --------------------------------------------------------------------------- #
# API edge gate: requester_id=root honored only for an mTLS-verified operator
# --------------------------------------------------------------------------- #
_MTLS_OP = {"ssl-client-verify": "SUCCESS", "ssl-client-subject-dn": "CN=operator,O=dms"}


def _client(tmp_path, *, allow_root, mtls=True, scopes=frozenset(), operators=frozenset()):
    operational_url = f"sqlite:///{tmp_path / 'op.db'}"
    observability_url = f"sqlite:///{tmp_path / 'obs.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
        require_mtls_header=mtls,
        require_mtls_verified_header=mtls,
        dm_allow_root_requester=allow_root,
        dm_privileged_scopes=scopes,
        dm_privileged_operators=operators,
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    app = create_app(
        settings,
        DmsRepository(operational),
        ObservabilityRepository(observability_db),
        identity_lookup=None,
    )
    return TestClient(app)


def _scan_body(requester_id, storage="cephfs-ops", path="ops/job1"):
    return {"requester_id": requester_id, "target": {"storage_name": storage, "path": path}}


def test_gate_root_allowed_for_mtls_operator(tmp_path):
    client = _client(tmp_path, allow_root=True, mtls=True)
    r = client.post("/api/v1/data-management/scan", headers=_MTLS_OP, json=_scan_body("root"))
    assert r.status_code == 202


def test_gate_root_denied_without_verified_mtls(tmp_path):
    # mTLS off -> actor is the plaintext x-dms-actor header (no `mtls:` prefix) -> 403.
    client = _client(tmp_path, allow_root=True, mtls=False)
    r = client.post(
        "/api/v1/data-management/scan",
        headers={"x-dms-actor": "operator"},
        json=_scan_body("root"),
    )
    assert r.status_code == 403


def test_gate_root_denied_when_feature_off(tmp_path):
    # flag OFF -> gate is a no-op; request persists (202) and the worker rejects root later.
    client = _client(tmp_path, allow_root=False, mtls=True)
    r = client.post("/api/v1/data-management/scan", headers=_MTLS_OP, json=_scan_body("root"))
    assert r.status_code == 202


def test_gate_normal_user_unaffected(tmp_path):
    client = _client(tmp_path, allow_root=True, mtls=True)
    r = client.post("/api/v1/data-management/scan", headers=_MTLS_OP, json=_scan_body("alice"))
    assert r.status_code == 202


def test_gate_scope_allowlist_enforced(tmp_path):
    client = _client(tmp_path, allow_root=True, mtls=True, scopes=frozenset({"cephfs-ops:ops"}))
    inside = client.post(
        "/api/v1/data-management/scan",
        headers=_MTLS_OP,
        json=_scan_body("root", storage="cephfs-ops", path="ops/x"),
    )
    assert inside.status_code == 202
    outside = client.post(
        "/api/v1/data-management/scan",
        headers=_MTLS_OP,
        json=_scan_body("root", storage="cephfs-ops", path="userdata/x"),
    )
    assert outside.status_code == 403


def test_gate_operator_allowlist_enforced(tmp_path):
    client = _client(
        tmp_path, allow_root=True, mtls=True,
        operators=frozenset({"mtls:CN=other,O=dms"}),
    )
    r = client.post("/api/v1/data-management/scan", headers=_MTLS_OP, json=_scan_body("root"))
    assert r.status_code == 403  # CN=operator is not on the allowlist
