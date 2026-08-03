import pytest
from dms.identity import (
    IdentityRejected, IdentityUnavailable, ResolvedIdentity, StubIdentityResolver)


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
