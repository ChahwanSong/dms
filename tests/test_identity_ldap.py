import pytest
from dms.identity import IdentityUnavailable, ResolvedIdentity
from dms.identity_ldap import LdapIdentityResolver


class _FakeEntry:
    def __init__(self, attrs):
        self._attrs = attrs

    def __getitem__(self, key):
        return _FakeAttr(self._attrs[key])


class _FakeAttr:
    def __init__(self, value):
        self.value = value


class _FakeConn:
    """ldap3.Connection 유사: search가 self.entries를 채운다."""
    def __init__(self, users, groups, *, broken=False):
        self._users = users      # {uid: (uidNumber, gidNumber)}
        self._groups = groups    # {uid: [cn,...]}
        self._broken = broken
        self.entries = []

    def search(self, base, filt, attributes=None):
        if self._broken:
            raise RuntimeError("ldap down")
        if "memberUid" in filt:
            uid = filt.split("memberUid=")[1].rstrip(")")
            self.entries = [_FakeEntry({"cn": cn}) for cn in self._groups.get(uid, [])]
        else:
            uid = filt.split("uid=")[1].rstrip(")")
            if uid in self._users:
                un, gn = self._users[uid]
                self.entries = [_FakeEntry({"uidNumber": un, "gidNumber": gn})]
            else:
                self.entries = []
        return bool(self.entries)


def _resolver(users, groups, *, broken=False):
    return LdapIdentityResolver(
        connect=lambda: _FakeConn(users, groups, broken=broken),
        user_base="ou=People,dc=dms,dc=local",
        group_base="ou=Groups,dc=dms,dc=local")


def test_resolve_hit():
    r = _resolver({"alice": (10001, 10000)}, {"alice": ["dmsusers", "eng"]})
    out = r.resolve("alice")
    assert out == ResolvedIdentity("alice", 10001, 10000, ("dmsusers", "eng"), False)


def test_resolve_miss_returns_none():
    r = _resolver({"alice": (10001, 10000)}, {})
    assert r.resolve("ghost") is None


def test_resolve_no_groups():
    r = _resolver({"bob": (10002, 10000)}, {})
    assert r.resolve("bob").groups == ()


def test_broken_connection_raises_unavailable():
    r = _resolver({}, {}, broken=True)
    with pytest.raises(IdentityUnavailable):
        r.resolve("alice")
