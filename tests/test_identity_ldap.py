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

    def search(self, base, filt, attributes=None, **kwargs):
        if self._broken:
            raise RuntimeError("ldap down")
        # ldap3 처럼 성공 결과 코드(0)·빈 컨트롤 -- 페이징 쿠키 없음 = 한 쪽으로 끝.
        self.result = {"result": 0, "description": "success", "controls": {}}
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
        connect=lambda deadline=None: _FakeConn(users, groups, broken=broken),
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
    def connect(deadline=None):
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

    def search(self, base, filt, attributes=None, **kwargs):
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
    r = LdapIdentityResolver(connect=lambda deadline=None: conn,
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
    r = LdapIdentityResolver(connect=lambda deadline=None: conn,
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
    r = LdapIdentityResolver(connect=lambda deadline=None: None,
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
        def search(self, base, filt, attributes=None, **kwargs):
            captured.append(filt)
            return False
    r = LdapIdentityResolver(connect=lambda deadline=None: _Conn(),
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

    infos = []

    class _Server:
        def __init__(self, uri, tls=None, connect_timeout=None, get_info=None):
            servers.append((uri, connect_timeout))
            infos.append(get_info)
            self.uri = uri

    class _Conn:
        def __init__(self, server, user=None, password=None, auto_bind=None, receive_timeout=None):
            conns.append((server.uri, receive_timeout))
            if server.uri == "ldap://down":
                raise ldap3.core.exceptions.LDAPSocketOpenError("socket open failed")
            self.entries, self.unbound = [], False

        def search(self, base, filt, attributes=None, **kwargs):
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
    resolver = build_ldap_resolver(settings)
    assert resolver.resolve("nobody") is None
    assert servers == [("ldap://down", 2.5), ("ldap://up", 2.5)]
    # receive_timeout 은 정수(ldap3 가 SO_RCVTIMEO 로 pack -- 실수면 실 LDAP 에서 모든 연결이 실패했다), 올림.
    assert conns == [("ldap://down", 3), ("ldap://up", 3)]
    assert all(isinstance(t, int) for _, t in conns)
    # bind 뒤 서버 정보(rootDSE·subschema 검색 2회)를 읽지 않는다 -- 한 URI 시도 = 3T 라는 틱 시간 불변식의
    # 전제(identity.LDAP_TICK_BUDGET_SECONDS). 기본값(SCHEMA)으로 되돌아가면 여기서 빨간불.
    assert infos == [ldap3.NONE, ldap3.NONE]
    # 시작 URI 기억(sticky): 다음 resolve 는 방금 붙은 up 부터 -- 죽은 down 의 타임아웃을 다시 내지 않는다.
    servers.clear()
    assert resolver.resolve("nobody") is None
    assert servers == [("ldap://up", 2.5)]
    # 다른 리졸버(= 다른 프로세스)는 처음부터.
    servers.clear()
    assert build_ldap_resolver(settings).resolve("nobody") is None
    assert [u for u, _t in servers] == ["ldap://down", "ldap://up"]


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
        r = LdapIdentityResolver(connect=lambda deadline=None: conn, user_base="ou=u", group_base="ou=g",
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
    r = LdapIdentityResolver(connect=lambda deadline=None: conn, user_base="ou=u", group_base="ou=g",
                             group_member_attr="memberUid")
    assert r.resolve("alice").groups == ("proj-a", "projA", "users")


# --- 보조 그룹(2026-10-07, D14): posixGroup gidNumber·fail-closed·페이징·resolve 마감 --------------------------

from dms.identity import IdentityLookupInvalid
from dms.identity_ldap import _MAX_GROUP_PAGES, _PAGED_RESULTS_OID, _RESOLVE_DEADLINE_SECONDS


def _pg(cn, gid, classes=("top", "posixGroup")):
    # 단일값 objectClass 는 ldap3 처럼 스칼라 그대로(.value 가 str) -- 목록으로 펼치지 않는다.
    attrs = {"cn": cn, "objectClass": classes if isinstance(classes, str) else list(classes)}
    if gid is not _ABSENT:
        attrs["gidNumber"] = gid
    return attrs


_ABSENT = object()


class _PagedConn:
    """ldap3.Connection 흉내(새 경로): 사용자 검색은 지정 엔트리(중복 가능)를, 그룹 검색은 쪽(page) 목록을
    paged_cookie 로 넘긴다. 결과 코드·controls 쿠키를 ldap3 의 conn.result 모양으로 싣는다. clock 이 있으면
    그룹 검색마다 advance 초씩 전진한다(마감 테스트)."""
    def __init__(self, *, users=({"uidNumber": 10001, "gidNumber": 10000},), pages=((),),
                 user_result=0, group_result=0, endless=False, clock=None, advance=0.0):
        self._users, self._pages = list(users), [list(p) for p in pages]
        self._user_result, self._group_result = user_result, group_result
        self._endless, self._clock, self._advance = endless, clock, advance
        self.calls = []
        self.entries = []
        self.result = None

    def search(self, base, filt, attributes=None, **kwargs):
        self.calls.append({"filter": filt, "attributes": attributes, **kwargs})
        if filt.startswith("(uid="):
            self.entries = [_FakeEntry(a) for a in self._users]
            self.result = {"result": self._user_result, "description": "x", "controls": {}}
            return bool(self.entries)
        if self._clock is not None:
            self._clock[0] += self._advance
        cookie = kwargs.get("paged_cookie")
        idx = 0 if cookie is None else int(cookie.decode())
        page = self._pages[min(idx, len(self._pages) - 1)]
        self.entries = [_FakeEntry(a) for a in page]
        nxt = idx + 1
        more = self._endless or nxt < len(self._pages)
        self.result = {"result": self._group_result, "description": "x",
                       "controls": {_PAGED_RESULTS_OID: {"value": {
                           "cookie": str(nxt).encode() if more else b"", "size": 0}}}}
        return bool(self.entries)


def _paged_resolver(conn, **kw):
    return LdapIdentityResolver(connect=lambda deadline=None: conn, user_base="ou=u", group_base="ou=g",
                                group_member_attr="memberUid", **kw)


def test_group_search_requests_gid_and_objectclass_with_paging():
    conn = _PagedConn(pages=([_pg("proj", 20001)],))
    out = _paged_resolver(conn).resolve("alice")
    group_call = conn.calls[1]
    assert group_call["filter"] == "(memberUid=alice)"          # 필터 문자열은 그대로(denylist 의미 보존)
    assert group_call["attributes"] == ["cn", "gidNumber", "objectClass"]
    assert group_call["paged_size"] == 500 and group_call["paged_cookie"] is None
    assert out.group_gids == (20001,) and out.groups == ("proj",)


def test_group_gids_only_from_posixgroup_entries():
    conn = _PagedConn(pages=([_pg("proj", 20001),
                              _pg("samba", 20002, classes=("sambaGroupMapping", "groupOfUniqueNames")),
                              _pg("PG", "20003", classes="PosixGroup"),           # 대소문자 무시·문자열 gid
                              _pg("bytesy", b"20004", classes=(b"posixGroup",))],))
    out = _paged_resolver(conn).resolve("alice")
    assert out.group_gids == (20001, 20003, 20004)
    assert out.groups == ("PG", "bytesy", "proj", "samba")        # 이름은 클래스와 무관하게 유지(denylist)


def test_malformed_gidnumber_skipped_name_kept():
    conn = _PagedConn(pages=([_pg("nogid", _ABSENT), _pg("multi", [1, 2]), _pg("abc", "abc"),
                              _pg("boolish", True), _pg("ok", 20001)],))
    out = _paged_resolver(conn).resolve("alice")
    assert out.group_gids == (20001,)
    assert out.groups == ("abc", "boolish", "multi", "nogid", "ok")


def test_negative_and_huge_gid_passed_raw():
    # 범위 판정은 identity.valid_supplementary_gids 몫 -- 리졸버는 원시값을 넘겨 화면에 '제외'로 보이게 한다.
    conn = _PagedConn(pages=([_pg("neg", "-5"), _pg("huge", 4294967295), _pg("ok", 10000)],))
    assert _paged_resolver(conn).resolve("alice").group_gids == (-5, 10000, 4294967295)


def test_duplicate_user_entries_is_lookup_invalid():
    conn = _PagedConn(users=({"uidNumber": 10001, "gidNumber": 10000},
                             {"uidNumber": 10009, "gidNumber": 10000}))
    with pytest.raises(IdentityLookupInvalid) as e:
        _paged_resolver(conn).resolve("alice")
    assert "duplicate" in str(e.value)
    assert len(conn.calls) == 1                                    # 그룹 검색까지 가지 않는다


@pytest.mark.parametrize("code", [4, 11])
@pytest.mark.parametrize("where", ["user", "group"])
def test_size_and_admin_limit_are_lookup_invalid(code, where):
    conn = _PagedConn(pages=([_pg("proj", 20001)],),
                      **({"user_result": code} if where == "user" else {"group_result": code}))
    with pytest.raises(IdentityLookupInvalid):
        _paged_resolver(conn).resolve("alice")


@pytest.mark.parametrize("code", [3, 32, 51, 52])
@pytest.mark.parametrize("where", ["user", "group"])
def test_other_nonzero_results_are_plain_unavailable(code, where):
    # 서버·설정 전역(timeLimit·noSuchObject(베이스 오구성)·busy·unavailable) -- 서킷을 여는 plain IdentityUnavailable.
    conn = _PagedConn(pages=([_pg("proj", 20001)],),
                      **({"user_result": code} if where == "user" else {"group_result": code}))
    with pytest.raises(IdentityUnavailable) as e:
        _paged_resolver(conn).resolve("alice")
    assert not isinstance(e.value, IdentityLookupInvalid)


def test_group_search_follows_paged_cookie():
    conn = _PagedConn(pages=([_pg("a", 20001)], [_pg("b", 20002)]))
    out = _paged_resolver(conn).resolve("alice")
    assert out.group_gids == (20001, 20002) and out.groups == ("a", "b")
    assert [c.get("paged_cookie") for c in conn.calls[1:]] == [None, b"1"]


def test_group_search_page_cap_is_lookup_invalid():
    conn = _PagedConn(pages=([_pg("a", 20001)],), endless=True)
    with pytest.raises(IdentityLookupInvalid):
        _paged_resolver(conn).resolve("alice")
    assert len(conn.calls) == 1 + _MAX_GROUP_PAGES                 # 상한까지만 묻고 부분 결과로 판정하지 않는다


def test_resolve_deadline_stops_before_next_page():
    clock = [100.0]
    conn = _PagedConn(pages=([_pg("a", 20001)], [_pg("b", 20002)]), clock=clock, advance=11.0)
    r = _paged_resolver(conn, monotonic=lambda: clock[0])
    with pytest.raises(IdentityUnavailable) as e:
        r.resolve("alice")                                         # 자체 마감 10s -- 첫 쪽 뒤 11s 전진
    assert not isinstance(e.value, IdentityLookupInvalid)          # 느림 = 장애(서킷 개방)
    assert len(conn.calls) == 2                                    # 두 번째 쪽은 시작하지 않는다
    # 호출자 deadline 이 자체 마감보다 이르면 그쪽이 이긴다(틱 예산의 남은 몫).
    clock[0] = 100.0
    conn2 = _PagedConn(pages=([_pg("a", 20001)], [_pg("b", 20002)]), clock=clock, advance=3.0)
    with pytest.raises(IdentityUnavailable):
        _paged_resolver(conn2, monotonic=lambda: clock[0]).resolve("alice", deadline=102.0)
    assert len(conn2.calls) == 2
    # 마감 안이면 그대로 끝난다.
    clock[0] = 100.0
    conn3 = _PagedConn(pages=([_pg("a", 20001)], [_pg("b", 20002)]), clock=clock, advance=1.0)
    assert _paged_resolver(conn3, monotonic=lambda: clock[0]).resolve(
        "alice", deadline=100.0 + _RESOLVE_DEADLINE_SECONDS * 5).group_gids == (20001, 20002)


def test_resolve_passes_its_limit_to_connect():
    seen = []
    conn = _PagedConn()

    def connect(deadline=None):
        seen.append(deadline)
        return conn
    r = LdapIdentityResolver(connect=connect, user_base="ou=u", group_base="ou=g",
                             group_member_attr="memberUid", monotonic=lambda: 50.0)
    r.resolve("alice")
    r.resolve("alice", deadline=53.0)
    r.resolve("alice", deadline=500.0)
    assert seen == [50.0 + _RESOLVE_DEADLINE_SECONDS, 53.0, 50.0 + _RESOLVE_DEADLINE_SECONDS]


def test_connect_first_stops_at_deadline():
    clock = [0.0]
    tried = []

    def open_one(uri):
        tried.append(uri)
        clock[0] += 6.0
        raise OSError("connect timed out")
    with pytest.raises(IdentityUnavailable) as e:
        connect_first(["ldap://a", "ldap://b", "ldap://c"], open_one, deadline=5.0,
                      monotonic=lambda: clock[0])
    assert tried == ["ldap://a"]                                   # 남은 URI 는 시도하지 않는다
    assert "deadline exceeded" in str(e.value)
    # 이미 마감이 지났으면 첫 URI 도 시도하지 않는다.
    tried.clear()
    with pytest.raises(IdentityUnavailable):
        connect_first(["ldap://a"], open_one, deadline=5.0, monotonic=lambda: 9.0)
    assert tried == []


# ---- 2026-10-08 리뷰: 누구의 마감이 멈췄나(IdentityDeadlineExceeded)·시작 URI 기억(sticky)·스키마 없는 값 ----

from dms.identity import IdentityDeadlineExceeded, LDAP_RESOLVE_DEADLINE_SECONDS, tick_resolve_deadline


class _TimedConn:
    """연결 뒤 검색마다 clock 을 step 초 전진시키는 건강한 LDAP(사용자 1건·posixGroup 1건)."""
    def __init__(self, clock, step=0.05):
        self._clock, self._step = clock, step
        self.entries, self.result = [], {"result": 0}

    def search(self, base, filt, attributes=None, **kwargs):
        self._clock[0] += self._step
        if filt.startswith("(uid="):
            self.entries = [_FakeEntry({"uidNumber": 10001, "gidNumber": 10000})]
        else:
            self.entries = [_FakeEntry({"cn": "proj", "gidNumber": 20001, "objectClass": ["posixGroup"]})]
        self.result = {"result": 0}
        return True


def _failover_resolver(clock, uris, dead, *, rotation=None, t=5.0, tried=None):
    """실 LdapIdentityResolver + 실 connect_first + 가짜 시계. dead 의 URI 는 타임아웃형(연결에서 t 초 뒤 실패)."""
    tried = [] if tried is None else tried

    def open_one(uri):
        tried.append(uri)
        if uri in dead:
            clock[0] += t
            raise OSError("timed out")
        clock[0] += 0.05
        return _TimedConn(clock)
    return LdapIdentityResolver(
        connect=lambda deadline=None: connect_first(uris, open_one, deadline=deadline,
                                                    monotonic=lambda: clock[0], rotation=rotation),
        user_base="ou=u", group_base="ou=g", group_member_attr="memberUid", monotonic=lambda: clock[0])


def test_caller_deadline_stop_is_deadline_exceeded_own_deadline_is_unavailable():
    # 호출자 deadline(틱 예산의 남은 몫)이 묶고 그 시각에 멈췄다 = 예산 소진(IdentityDeadlineExceeded).
    clock = [100.0]
    conn = _PagedConn(pages=([_pg("a", 20001)], [_pg("b", 20002)]), clock=clock, advance=3.0)
    with pytest.raises(IdentityDeadlineExceeded):
        _paged_resolver(conn, monotonic=lambda: clock[0]).resolve("alice", deadline=102.0)
    # 자체 마감(10s)에 걸린 중단은 진짜 판정 -- plain IdentityUnavailable(하위 클래스가 아니다).
    clock[0] = 100.0
    conn2 = _PagedConn(pages=([_pg("a", 20001)], [_pg("b", 20002)]), clock=clock, advance=11.0)
    with pytest.raises(IdentityUnavailable) as e:
        _paged_resolver(conn2, monotonic=lambda: clock[0]).resolve("alice")
    assert type(e.value) is IdentityUnavailable
    # 호출자 deadline 이 자체 마감보다 늦으면 자체 마감이 묶는다 -- 역시 진짜 판정.
    clock[0] = 100.0
    conn3 = _PagedConn(pages=([_pg("a", 20001)], [_pg("b", 20002)]), clock=clock, advance=11.0)
    with pytest.raises(IdentityUnavailable) as e:
        _paged_resolver(conn3, monotonic=lambda: clock[0]).resolve("alice", deadline=500.0)
    assert type(e.value) is IdentityUnavailable


def test_connect_stage_stop_on_caller_deadline_is_deadline_exceeded():
    # 앞 URI 의 타임아웃(5s)이 남은 몫(3s)을 넘겨 뒤 URI 를 못 가 봤다 -- LDAP 판정이 아니다.
    clock, tried = [0.0], []
    r = _failover_resolver(clock, ["ldap://a", "ldap://b"], {"ldap://a"}, tried=tried)
    with pytest.raises(IdentityDeadlineExceeded) as e:
        r.resolve("alice", deadline=3.0)
    assert tried == ["ldap://a"] and "deadline exceeded" in str(e.value)
    # 같은 모양이라도 자체 마감(10s)이 멈췄으면 진짜 판정: a·b 가 죽어(5s+5s) c 를 못 가 봤다.
    clock[0], tried[:] = 0.0, []
    r = _failover_resolver(clock, ["ldap://a", "ldap://b", "ldap://c"], {"ldap://a", "ldap://b"}, tried=tried)
    with pytest.raises(IdentityUnavailable) as e:
        r.resolve("alice")
    assert type(e.value) is IdentityUnavailable and tried == ["ldap://a", "ldap://b"]
    # 모든 URI 를 시도하고 다 실패하면 deadline 이 지났어도 진짜 판정(장애).
    clock[0], tried[:] = 0.0, []
    r = _failover_resolver(clock, ["ldap://a"], {"ldap://a"}, tried=tried)
    with pytest.raises(IdentityUnavailable) as e:
        r.resolve("alice", deadline=3.0)
    assert type(e.value) is IdentityUnavailable and tried == ["ldap://a"]


def test_sticky_start_reaches_third_uri_when_two_front_uris_are_dead():
    # G4: p1·r3 가 타임아웃형으로 죽으면 5s + 5s 로 자체 마감 10s 에 닿아 ldaps 를 시도조차 못 했다(매 resolve).
    # 기억된 시작 위치로 첫 resolve 가 못 가 본 URI 에서 다음 resolve 가 시작해 반드시 닿는다.
    clock, tried, rotation = [0.0], [], {"start": 0}
    uris = ["ldap://p1", "ldap://r3", "ldap://ldaps"]
    r = _failover_resolver(clock, uris, {"ldap://p1", "ldap://r3"}, rotation=rotation, tried=tried)
    with pytest.raises(IdentityUnavailable):
        r.resolve("alice")                                          # 첫 resolve 는 자체 마감 -- 진짜 판정
    assert tried == ["ldap://p1", "ldap://r3"] and rotation["start"] == 2
    tried.clear()
    assert r.resolve("alice").uid == 10001                          # 다음은 ldaps 부터 -- 성공
    assert tried == ["ldap://ldaps"] and rotation["start"] == 2
    tried.clear()
    assert r.resolve("alice").uid == 10001                          # 붙은 URI 를 계속 쓴다(앞쪽 타임아웃 없음)
    assert tried == ["ldap://ldaps"]


def test_sticky_start_when_timeout_exceeds_own_deadline():
    # G4: DMS_LDAP_TIMEOUT_SECONDS ≥ 10 이면 첫 URI 하나만 시도돼 페일오버가 완전히 꺼졌다.
    clock, tried, rotation = [0.0], [], {"start": 0}
    r = _failover_resolver(clock, ["ldap://a", "ldap://b"], {"ldap://a"}, rotation=rotation, t=10.0, tried=tried)
    with pytest.raises(IdentityUnavailable):
        r.resolve("alice")
    assert tried == ["ldap://a"] and rotation["start"] == 1
    tried.clear()
    assert r.resolve("alice").uid == 10001 and tried == ["ldap://b"]


def test_sticky_start_wraps_around_and_keeps_position_when_all_fail():
    tried, rotation = [], {"start": 2}

    def open_one(uri):
        tried.append(uri)
        if uri != "ldap://a":
            raise OSError("refused")
        return "conn"
    assert connect_first(["ldap://a", "ldap://b", "ldap://c"], open_one, rotation=rotation) == "conn"
    assert tried == ["ldap://c", "ldap://a"] and rotation["start"] == 0   # c 부터 돌아 a 에서 붙는다

    def all_down(uri):
        raise OSError("refused")
    with pytest.raises(IdentityUnavailable):
        connect_first(["ldap://a", "ldap://b"], all_down, rotation=rotation)
    assert rotation["start"] == 0                                 # 전부 실패면 위치를 그대로 둔다


def test_tick_resolve_deadline_only_when_budget_binds():
    # 틱 첫 resolve(남은 몫 = 예산 = 자체 마감)는 deadline 을 넘기지 않는다 -- 실시간 ε 차이로 첫 resolve 까지 예산
    # 소진(Pending)으로 접혀 진짜 장애가 영영 판정되지 않는 일이 없게.
    assert tick_resolve_deadline(100.0, LDAP_RESOLVE_DEADLINE_SECONDS) is None
    assert tick_resolve_deadline(100.0, LDAP_RESOLVE_DEADLINE_SECONDS + 5) is None
    assert tick_resolve_deadline(100.0, 3.5) == 103.5


def test_schemaless_ldap3_values_resolve_like_schema_values():
    # get_info=NONE 이면 ldap3 는 스키마 없이 값을 str 로 준다(uidNumber·gidNumber·objectClass) -- 리졸버가 그대로
    # 해석해야 한다(실 ldap3 MOCK_SYNC, 스키마 없는 Server).
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import ldap3
    server = ldap3.Server("fake", get_info=ldap3.NONE)
    conn = ldap3.Connection(server, user="cn=admin,dc=x", password="pw", client_strategy=ldap3.MOCK_SYNC)
    conn.strategy.add_entry("cn=admin,dc=x", {"userPassword": "pw", "sn": "admin"})
    conn.strategy.add_entry("uid=alice,ou=people,dc=x", {
        "objectClass": ["posixAccount", "inetOrgPerson"], "uid": "alice", "cn": "alice", "sn": "a",
        "uidNumber": 10001, "gidNumber": 10000, "homeDirectory": "/h"})
    for i in range(3):
        conn.strategy.add_entry(f"cn=g{i},ou=groups,dc=x", {
            "objectClass": ["groupOfUniqueNames", "posixGroup"], "cn": f"g{i}",
            "gidNumber": 20000 + i, "uniqueMember": "uid=alice,ou=people,dc=x"})
    conn.strategy.add_entry("cn=app,ou=groups,dc=x", {
        "objectClass": ["groupOfUniqueNames"], "cn": "app", "uniqueMember": "uid=alice,ou=people,dc=x"})
    conn.bind()
    out = LdapIdentityResolver(connect=lambda deadline=None: conn, user_base="ou=people,dc=x",
                               group_base="ou=groups,dc=x").resolve("alice")
    assert (out.uid, out.gid) == (10001, 10000)
    assert out.group_gids == (20000, 20001, 20002)                 # posixGroup 만(app 은 이름만)
    assert out.groups == ("app", "g0", "g1", "g2")


def test_ldap3_missing_attribute_keyerror_tolerated():
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from ldap3.core.exceptions import LDAPKeyError

    class _StrictEntry(_FakeEntry):
        def __getitem__(self, key):
            if key not in self._attrs:
                raise LDAPKeyError(f"key '{key}' not found")
            return _FakeAttr(self._attrs[key])

    class _Conn(_PagedConn):
        def search(self, base, filt, attributes=None, **kwargs):
            found = super().search(base, filt, attributes, **kwargs)
            if not filt.startswith("(uid="):
                self.entries = [_StrictEntry({"cn": "appgroup"}),
                                _StrictEntry({"cn": "proj", "gidNumber": 20001,
                                              "objectClass": ["posixGroup"]})]
            return found
    out = _paged_resolver(_Conn()).resolve("alice")
    assert out.groups == ("appgroup", "proj") and out.group_gids == (20001,)


def test_rotation_leaves_a_uri_that_binds_but_fails_searches(monkeypatch):
    # 2026-10-08 검증: 연결 성공만 기억하던 sticky 는, 한 번 페일오버한 뒤 그 URI 가 "연결·bind 는 되는데 검색이 실패"하는
    # 상태(과부하 레플리카의 receive 타임아웃·busy)가 되면 1차가 살아나도 계속 그 URI 로만 갔다. 이제 붙은 URI 의 검색이
    # 전송 오류로 실패하면 다음 resolve 는 그다음 URI 부터 돈다.
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import ldap3
    tried = []
    state = {"a_down": True}

    class _Server:
        def __init__(self, uri, tls=None, connect_timeout=None, get_info=None):
            self.uri = uri

    class _Conn:
        def __init__(self, server, user=None, password=None, auto_bind=None, receive_timeout=None):
            tried.append(server.uri)
            if server.uri == "ldap://a" and state["a_down"]:
                raise OSError("a down")
            self.uri, self.entries, self.result = server.uri, [], {"result": 0, "description": "success"}

        def search(self, base, filt, attributes=None, **kwargs):
            if self.uri == "ldap://b":
                raise TimeoutError("receive timeout")      # b 는 bind 는 되지만 검색이 막힌다
            self.entries = []
            return False

        def unbind(self):
            pass
    monkeypatch.setattr(ldap3, "Server", _Server)
    monkeypatch.setattr(ldap3, "Connection", _Conn)
    settings = SimpleNamespace(ldap_uri="ldap://a/, ldap://b/", ldap_user_base="ou=u,dc=x",
                               ldap_group_base="ou=g,dc=x", ldap_use_start_tls=False,
                               ldap_bind_dn="", ldap_bind_pw="", ldap_group_member_attr="uniqueMember",
                               ldap_timeout_seconds=2)
    resolver = build_ldap_resolver(settings)
    with pytest.raises(IdentityUnavailable):
        resolver.resolve("nobody")                 # a 연결 실패 → b 연결 성공 → b 검색 실패
    assert tried == ["ldap://a", "ldap://b"]
    state["a_down"] = False                        # a 복구
    tried.clear()
    assert resolver.resolve("nobody") is None      # b 에 붙박이지 않고 a 부터
    assert tried == ["ldap://a"]


def test_search_failure_hook_is_not_called_for_per_user_data_problems():
    # 중복 사용자 엔트리(IdentityLookupInvalid)는 서버 탓이 아니다 -- 시작 위치를 옮기지 않는다.
    from dms.identity import IdentityLookupInvalid
    calls = []
    dup = _FakeConn({"alice": (10001, 10000)}, {"alice": []})

    def search(base, filt, attributes=None, **kw):
        dup.entries = [_FakeEntry({"uidNumber": 10001, "gidNumber": 10000})] * 2
        return True
    dup.search = search

    def connect(deadline=None):
        return dup
    connect.search_failed = lambda: calls.append(1)
    r = LdapIdentityResolver(connect=connect, user_base="ou=u", group_base="ou=g", group_member_attr="memberUid")
    with pytest.raises(IdentityLookupInvalid):
        r.resolve("alice")
    assert calls == []
