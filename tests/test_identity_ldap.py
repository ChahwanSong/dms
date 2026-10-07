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


# --- 연결 강화(2026-10-07): 타임아웃·순차 페일오버·unbind·다중값 cn ------------------------------------

from dms.identity_ldap import connect_first


def test_connect_first_falls_over_to_the_next_uri_in_order():
    tried = []

    def open_one(uri):
        tried.append(uri)
        if uri == "ldap://a":
            raise OSError("connection refused")
        return f"conn:{uri}"
    assert connect_first(["ldap://a", "ldap://b", "ldap://c"], open_one) == "conn:ldap://b"
    assert tried == ["ldap://a", "ldap://b"]


def test_connect_first_raises_unavailable_with_every_reason_when_all_fail():
    def open_one(uri):
        raise TimeoutError(f"timed out {uri}")
    with pytest.raises(IdentityUnavailable) as e:
        connect_first(["ldap://a", "ldap://b"], open_one)
    assert "ldap://a" in str(e.value) and "ldap://b" in str(e.value)


def test_connect_first_retries_a_previously_failed_uri_on_the_next_call():
    # 예전 ServerPool(exhaust=True)은 한 번 실패한 서버를 영구히 건너뛰어, LDAP 가 돌아와도 connect() 가 끝나지
    # 않았다. 순차 시도는 호출마다 처음부터 다시 본다.
    state = {"down": True}

    def open_one(uri):
        if state["down"]:
            raise OSError("down")
        return "conn"
    with pytest.raises(IdentityUnavailable):
        connect_first(["ldap://a"], open_one)
    state["down"] = False
    assert connect_first(["ldap://a"], open_one) == "conn"


def test_build_resolver_passes_timeouts_and_tries_uris_one_by_one(monkeypatch):
    import warnings
    with warnings.catch_warnings():       # pyasn1 의 tagMap DeprecationWarning(설정상 오류) -- 첫 import 만 낸다
        warnings.simplefilter("ignore")
        import ldap3
        import ldap3.core.exceptions
    servers, conns = [], []

    class _Server:
        def __init__(self, uri, tls=None, connect_timeout=None):
            servers.append((uri, connect_timeout))
            self.uri = uri

    class _Conn:
        def __init__(self, server, user=None, password=None, auto_bind=None, receive_timeout=None):
            conns.append((server.uri, receive_timeout))
            if server.uri == "ldap://down":
                raise ldap3.core.exceptions.LDAPSocketOpenError("socket open failed")
            self.entries, self.unbound = [], False

        def search(self, base, filt, attributes=None):
            self.entries = []
            return False

        def unbind(self):
            self.unbound = True
    monkeypatch.setattr(ldap3, "Server", _Server)
    monkeypatch.setattr(ldap3, "Connection", _Conn)
    monkeypatch.setattr(ldap3, "ServerPool", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ServerPool")))
    settings = SimpleNamespace(ldap_uri="ldap://down/, ldap://up/", ldap_user_base="ou=u,dc=x",
                               ldap_group_base="ou=g,dc=x", ldap_use_start_tls=False,
                               ldap_bind_dn="", ldap_bind_pw="", ldap_group_member_attr="uniqueMember",
                               ldap_timeout_seconds=2.5)
    assert build_ldap_resolver(settings).resolve("nobody") is None
    assert servers == [("ldap://down", 2.5), ("ldap://up", 2.5)]
    # receive_timeout 은 정수(ldap3 가 SO_RCVTIMEO 로 pack -- 실수면 실 LDAP 에서 모든 연결이 실패했다), 올림.
    assert conns == [("ldap://down", 3), ("ldap://up", 3)]
    assert all(isinstance(t, int) for _, t in conns)


class _UnbindConn(_FakeConn):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.unbound = 0

    def unbind(self):
        self.unbound += 1


def test_connection_is_unbound_after_success_miss_and_failure():
    for users, broken, expect in (({"alice": (10001, 10000)}, False, "hit"), ({}, False, "miss"),
                                  ({}, True, "fail")):
        conn = _UnbindConn(users, {"alice": []}, broken=broken)
        r = LdapIdentityResolver(connect=lambda: conn, user_base="ou=u", group_base="ou=g",
                                 group_member_attr="memberUid")
        if expect == "fail":
            with pytest.raises(IdentityUnavailable):
                r.resolve("alice")
        else:
            r.resolve("alice")
        assert conn.unbound == 1, expect


def test_multi_valued_group_cn_is_flattened():
    # 다중값 cn(예: ["proj-a", "projA"])이 리스트 그대로 흘러 정렬 TypeError·denylist AttributeError 로 요청이
    # 매 틱 plan_error 를 남기며 Pending 에 갇혔다 -- 값을 모두 이름으로 펼친다(denylist 거부가 느는 안전한 쪽).
    conn = _FakeConn({"alice": (10001, 10000)},
                     {"alice": [["proj-a", "projA"], "users", None]})
    r = LdapIdentityResolver(connect=lambda: conn, user_base="ou=u", group_base="ou=g",
                             group_member_attr="memberUid")
    assert r.resolve("alice").groups == ("proj-a", "projA", "users")
