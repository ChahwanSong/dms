import pytest
from dms.identity import (
    IdentityRejected, IdentityUnavailable, ResolvedIdentity, StubIdentityResolver,
    resolve_job_identity)
from dms.repositories.control import ControlRepository


def test_resolved_identity_is_frozen():
    ident = ResolvedIdentity("alice", 10001, 10000, ("dmsusers",), False)
    assert ident.uid == 10001 and ident.groups == ("dmsusers",)
    with pytest.raises(Exception):
        ident.uid = 0  # frozen


def test_stub_resolver_hit_miss_unavailable():
    ident = ResolvedIdentity("alice", 10001, 10000, ("dmsusers",), False)
    r = StubIdentityResolver({"alice": ident})
    assert r.resolve("alice") is ident
    assert r.resolve("ghost") is None
    down = StubIdentityResolver({}, unavailable=True)
    with pytest.raises(IdentityUnavailable):
        down.resolve("alice")


def test_identity_rejected_carries_reason():
    err = IdentityRejected("identity_denied", "mallory")
    assert err.reason_code == "identity_denied" and "mallory" in str(err)


ALICE = ResolvedIdentity("alice", 10001, 10000, ("dmsusers",), False)


def _control(db):
    return ControlRepository(db)


def test_resolve_normal_registers_probe(db):
    control = _control(db)
    resolver = StubIdentityResolver({"alice": ALICE})
    out = resolve_job_identity(control, resolver, requester_id="alice",
                               owner_username=None, allow_privileged=False,
                               privileged_requesters=frozenset())
    assert out == ALICE and out.privileged is False
    assert control.probe_targets(ttl_seconds=3600) == ["alice"]


def test_owner_username_override(db):
    control = _control(db)
    bob = ResolvedIdentity("bob", 10002, 10000, (), False)
    out = resolve_job_identity(control, StubIdentityResolver({"bob": bob}),
                               requester_id="admin", owner_username="  bob  ",
                               allow_privileged=False, privileged_requesters=frozenset())
    assert out.username == "bob"


def test_denylist_blocks_before_privileged(db):
    control = _control(db)
    control.deny("owner", "root-op", reason="incident", actor="admin")
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, StubIdentityResolver({}),
                             requester_id="root-op", owner_username=None,
                             allow_privileged=True,
                             privileged_requesters=frozenset({"root-op"}))
    assert e.value.reason_code == "identity_denied"


def test_privileged_path_synthesizes_root(db):
    control = _control(db)
    out = resolve_job_identity(control, None, requester_id="ops",
                               owner_username="victim", allow_privileged=True,
                               privileged_requesters=frozenset({"ops"}))
    assert out.privileged and out.uid == 0 and out.gid == 0


def test_privileged_gates_on_requester_not_owner(db):
    control = _control(db)
    # requester는 allowlist에 없고, owner_username만 allowlist 멤버 → root 금지, LDAP 경로로
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, None, requester_id="mallory",
                             owner_username="ops", allow_privileged=True,
                             privileged_requesters=frozenset({"ops"}))
    assert e.value.reason_code == "ldap_not_configured"  # 특권 우회 안 됨 → resolver None 경로
    # requester가 allowlist에 있으면 root
    out = resolve_job_identity(control, None, requester_id="ops",
                               owner_username="victim", allow_privileged=True,
                               privileged_requesters=frozenset({"ops"}))
    assert out.privileged and out.uid == 0


def test_ldap_not_configured_and_unavailable_and_missing(db):
    control = _control(db)
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, None, requester_id="alice", owner_username=None,
                             allow_privileged=False, privileged_requesters=frozenset())
    assert e.value.reason_code == "ldap_not_configured"
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, StubIdentityResolver({}, unavailable=True),
                             requester_id="alice", owner_username=None,
                             allow_privileged=False, privileged_requesters=frozenset())
    assert e.value.reason_code == "ldap_unavailable"
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, StubIdentityResolver({}), requester_id="ghost",
                             owner_username=None, allow_privileged=False,
                             privileged_requesters=frozenset())
    assert e.value.reason_code == "ldap_identity_not_found"


def test_denylist_second_pass_on_groups(db):
    control = _control(db)
    control.deny("group", "dmsusers", reason=None, actor="admin")
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, StubIdentityResolver({"alice": ALICE}),
                             requester_id="alice", owner_username=None,
                             allow_privileged=False, privileged_requesters=frozenset())
    assert e.value.reason_code == "identity_denied"


def test_privileged_requires_session_auth(db):
    # 심층 방어(설계 §2.2-2): 같은 특권 requester 라도 session 이면 root, token 이면
    # 특권을 강제로 끈다. token 경로는 privileged 를 못 얻어 LDAP 경로로 떨어진다.
    control = _control(db)
    out = resolve_job_identity(control, None, requester_id="ops",
                               owner_username="victim", allow_privileged=True,
                               privileged_requesters=frozenset({"ops"}),
                               session_authenticated=True)
    assert out.privileged and out.uid == 0
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, None, requester_id="ops",
                             owner_username="victim", allow_privileged=True,
                             privileged_requesters=frozenset({"ops"}),
                             session_authenticated=False)
    # 특권을 안 쓰므로 resolver=None 인 LDAP 경로로 떨어진다.
    assert e.value.reason_code == "ldap_not_configured"
