import pytest
from types import SimpleNamespace
from dms.identity import IdentityUnavailable, ResolvedIdentity
from dms.identity_ldap import (LdapIdentityResolver, build_ldap_resolver,
                               _escape_filter, _parse_uris)


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
    # rfc2307 경로를 고정하는 픽스처 -- 기본값은 uniqueMember(rfc2307bis)로
    # 승격됐으므로(2026-08-23) memberUid 는 명시적으로 지정한다.
    return LdapIdentityResolver(
        connect=lambda: _FakeConn(users, groups, broken=broken),
        user_base="ou=People,dc=dms,dc=local",
        group_base="ou=Groups,dc=dms,dc=local",
        group_member_attr="memberUid")


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


# ── rfc2307bis: 그룹 멤버십을 사용자 DN 으로 검색(uniqueMember/member) ──

class _BisConn:
    """user 검색은 entry_dn 있는 엔트리를, 그룹 검색은 필터를 캡처해 응답."""
    def __init__(self, dn, groups_by_dn):
        self._dn = dn
        self._groups = groups_by_dn  # {필터에 든 DN 문자열: [cn,...]}
        self.filters = []
        self.entries = []

    def search(self, base, filt, attributes=None):
        self.filters.append(filt)
        if filt.startswith("(uid="):
            entry = _FakeEntry({"uidNumber": 20001, "gidNumber": 20000})
            entry.entry_dn = self._dn
            self.entries = [entry]
        else:
            dn = filt.split("=", 1)[1].rstrip(")")
            self.entries = [_FakeEntry({"cn": cn})
                            for cn in self._groups.get(dn, [])]
        return bool(self.entries)


def test_rfc2307bis_group_search_uses_user_dn():
    dn = "uid=alice,ou=People,dc=dms,dc=local"
    conn = _BisConn(dn, {dn: ["bisgroup"]})
    r = LdapIdentityResolver(connect=lambda: conn,
        user_base="ou=People,dc=dms,dc=local",
        group_base="ou=Groups,dc=dms,dc=local",
        group_member_attr="uniqueMember")
    out = r.resolve("alice")
    assert out == ResolvedIdentity("alice", 20001, 20000, ("bisgroup",), False)
    # 그룹 필터가 uid 가 아니라 **사용자 전체 DN** 으로 나갔는지
    assert conn.filters[1] == f"(uniqueMember={dn})"


def test_rfc2307bis_dn_with_metachars_is_escaped():
    dn = r"uid=we(ird,ou=Peo*ple,dc=dms,dc=local"
    conn = _BisConn(dn, {})
    r = LdapIdentityResolver(connect=lambda: conn,
        user_base="ou=People", group_base="ou=Groups",
        group_member_attr="member")
    out = r.resolve("weird")
    assert out.groups == ()
    assert r"\28" in conn.filters[1] and r"\2a" in conn.filters[1]


def test_member_uid_mode_never_touches_entry_dn():
    # memberUid(rfc2307) 경로는 entry_dn 이 없는 엔트리로도 동작해야 한다 --
    # rfc2307bis 지원이 기존 rfc2307 경로에 새 요구를 얹지 않았음을 고정.
    r = _resolver({"alice": (10001, 10000)}, {"alice": ["dmsusers"]})
    assert r.resolve("alice").groups == ("dmsusers",)


def test_constructor_default_is_unique_member():
    # 2026-08-23 사용자 결정: 기본값이 곧 프로덕션 sssd.conf 값(rfc2307bis).
    r = LdapIdentityResolver(connect=lambda: None,
                             user_base="ou=People", group_base="ou=Groups")
    assert r._group_member_attr == "uniqueMember"


def test_parse_uris_sssd_style():
    # sssd ldap_uri 관례: 콤마 목록 + 후행 '/'
    assert _parse_uris("ldap://ldap_p1/, ldap://ldap_r3/, ldap://ldaps/") == \
        ["ldap://ldap_p1", "ldap://ldap_r3", "ldap://ldaps"]
    assert _parse_uris("ldap://10.10.10.30:389") == ["ldap://10.10.10.30:389"]
    assert _parse_uris(" ldap://a , , ldap://b ") == ["ldap://a", "ldap://b"]


def test_build_resolver_passes_group_member_attr():
    r = build_ldap_resolver(_settings(ldap_uri="ldap://x:389",
        ldap_user_base="ou=People,dc=dms,dc=local",
        ldap_group_base="ou=Groups,dc=dms,dc=local",
        ldap_group_member_attr="uniqueMember"))
    assert r._group_member_attr == "uniqueMember"


def test_build_resolver_defaults_to_unique_member():
    # 속성 결측 폴백도 프로덕션 기본(uniqueMember)과 같은 방향.
    r = build_ldap_resolver(_settings(ldap_uri="ldap://x:389",
        ldap_user_base="ou=People,dc=dms,dc=local",
        ldap_group_base="ou=Groups,dc=dms,dc=local"))
    assert r._group_member_attr == "uniqueMember"


def test_build_resolver_member_uid_opt_down():
    r = build_ldap_resolver(_settings(ldap_uri="ldap://x:389",
        ldap_user_base="ou=People,dc=dms,dc=local",
        ldap_group_base="ou=Groups,dc=dms,dc=local",
        ldap_group_member_attr="memberUid"))
    assert r._group_member_attr == "memberUid"


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
