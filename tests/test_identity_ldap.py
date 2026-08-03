import pytest
from types import SimpleNamespace
from dms.identity import IdentityUnavailable, ResolvedIdentity
from dms.identity_ldap import LdapIdentityResolver, build_ldap_resolver, _escape_filter


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


def _settings(**kw):
    base = dict(ldap_uri="", ldap_user_base="", ldap_group_base="",
                ldap_bind_dn="", ldap_bind_pw="")
    base.update(kw)
    return SimpleNamespace(**base)


def test_build_resolver_fail_closed_when_half_configured():
    assert build_ldap_resolver(_settings()) is None
    assert build_ldap_resolver(_settings(ldap_uri="ldap://x:389")) is None  # base 없음
    assert build_ldap_resolver(_settings(ldap_uri="ldap://x:389",
        ldap_user_base="ou=People")) is None  # group_base 없음


def test_build_resolver_when_fully_configured():
    r = build_ldap_resolver(_settings(ldap_uri="ldap://x:389",
        ldap_user_base="ou=People,dc=dms,dc=local",
        ldap_group_base="ou=Groups,dc=dms,dc=local"))
    assert r is not None and hasattr(r, "resolve")


def test_escape_filter():
    assert _escape_filter("a)b(c*d\\e") == r"a\29b\28c\2ad\5ce"


def test_already_unavailable_not_double_wrapped():
    # connect가 IdentityUnavailable을 던지면 그대로 재전파(이중 래핑 없음)
    def connect():
        raise IdentityUnavailable("upstream")
    r = LdapIdentityResolver(connect=connect, user_base="ou=People", group_base="ou=Groups")
    with pytest.raises(IdentityUnavailable) as e:
        r.resolve("alice")
    assert "upstream" in str(e.value)


def test_injection_attempt_is_escaped():
    # username에 필터 메타문자가 있어도 uid= 필터에 이스케이프돼 전달
    captured = []

    class _Conn:
        entries = []
        def search(self, base, filt, attributes=None):
            captured.append(filt)
            return False
    r = LdapIdentityResolver(connect=lambda: _Conn(),
        user_base="ou=People", group_base="ou=Groups")
    r.resolve("evil)(uid=*")
    assert "*" not in captured[0] and ")" not in captured[0].replace("(uid=", "").rstrip(")")
    assert r"\2a" in captured[0]  # * 이스케이프됨
