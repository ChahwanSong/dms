import pytest
from dms.identity import (
    GID_MAX, MAX_SUPPLEMENTARY_GROUPS, PRIVILEGE_IF_ELIGIBLE, PRIVILEGE_NEVER, PRIVILEGE_REQUESTED,
    SUPP_APPLIED, SUPP_DISABLED, SUPP_NONE, SUPP_OVER_LIMIT, SUPP_PRIVILEGED,
    IdentityLookupInvalid, IdentityRejected, IdentityUnavailable, ResolvedIdentity,
    StubIdentityResolver, check_chown_group, limit_supplementary_gids, owner_override_allowed,
    resolve_job_identity, supplementary_gids_problem, valid_supplementary_gids)
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
    # 스위치(supplementary_groups) 생략 = 꺼짐 -- 상태가 disabled 로 달라져 dataclass 동등 비교 대신 필드로 본다.
    assert (out.username, out.uid, out.gid, out.groups, out.privileged) == \
        ("alice", 10001, 10000, ("dmsusers",), False)
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
    # session_authenticated 를 명시한다: 기본값이 fail-closed(False)라 특권을 원하는
    # 테스트는 그 조건을 스스로 말해야 한다(슬라이스 19).
    control = _control(db)
    out = resolve_job_identity(control, None, requester_id="ops",
                               owner_username="victim", allow_privileged=True,
                               privileged_requesters=frozenset({"ops"}),
                               session_authenticated=True,
                               privilege=PRIVILEGE_REQUESTED)
    assert out.privileged and out.uid == 0 and out.gid == 0


def test_privileged_gates_on_requester_not_owner(db):
    control = _control(db)
    # requester는 allowlist에 없고, owner_username만 allowlist 멤버 → root 금지.
    # 명시적 root 요청이면 조용히 낮추지 않고 거부한다(2026-09-30).
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, None, requester_id="mallory",
                             owner_username="ops", allow_privileged=True,
                             privileged_requesters=frozenset({"ops"}),
                             session_authenticated=True,
                             privilege=PRIVILEGE_REQUESTED)
    assert e.value.reason_code == "privileged_not_authorized"
    # 기본 정책(root 미요청)이면 LDAP 경로 -- resolver None 이라 ldap_not_configured.
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, None, requester_id="mallory",
                             owner_username="ops", allow_privileged=True,
                             privileged_requesters=frozenset({"ops"}),
                             session_authenticated=True)
    assert e.value.reason_code == "ldap_not_configured"  # 특권 우회 안 됨 → resolver None 경로
    # requester가 allowlist에 있으면 root
    out = resolve_job_identity(control, None, requester_id="ops",
                               owner_username="victim", allow_privileged=True,
                               privileged_requesters=frozenset({"ops"}),
                               session_authenticated=True,
                               privilege=PRIVILEGE_REQUESTED)
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
                               session_authenticated=True,
                               privilege=PRIVILEGE_REQUESTED)
    assert out.privileged and out.uid == 0
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, None, requester_id="ops",
                             owner_username="victim", allow_privileged=True,
                             privileged_requesters=frozenset({"ops"}),
                             session_authenticated=False,
                               privilege=PRIVILEGE_IF_ELIGIBLE)
    # 특권을 안 쓰므로 resolver=None 인 LDAP 경로로 떨어진다.
    assert e.value.reason_code == "ldap_not_configured"


def test_non_privileged_uid_zero_is_rejected_at_plan_time(db):
    # 디렉터리가 uidNumber=0 을 돌려주는 비특권 요청(2026-09-09 리뷰): stepper 의
    # privileged_flag_mismatch(변조 행 백스톱)보다 먼저, 계획 시점에 정확한 사유로.
    control = _control(db)
    rooty = ResolvedIdentity("rooty", 0, 0, (), False)
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, StubIdentityResolver({"rooty": rooty}),
                             requester_id="rooty", owner_username=None,
                             allow_privileged=False, privileged_requesters=frozenset())
    assert e.value.reason_code == "identity_root_without_privilege"
    assert control.probe_targets(ttl_seconds=3600) == []   # 프로브 등록 전에 거부


# --- 보조 그룹(2026-10-07, LDAP posixGroup gidNumber 인정) -------------------------------------------------


def test_valid_supplementary_gids_table():
    # D3: 범위 필터 없음(0·65534 인정), 기술적 무효값(음수·k8s MaxInt32 초과)만 '제외', 주 gid·bool·str 은 조용히 버림.
    raw = (0, 65534, 2147483647, 2147483648, 4294967294, 4294967295, -1, 10000, 65534, 0, True, "10")
    valid, excluded = valid_supplementary_gids(reversed(raw), primary_gid=10000)
    assert valid == (0, 65534, 2147483647)
    assert excluded == (-1, 2147483648, 4294967294, 4294967295)
    assert 10000 not in valid + excluded
    assert not any(type(g) is bool for g in valid + excluded)
    assert "10" not in valid + excluded
    assert valid_supplementary_gids(None, primary_gid=1) == ((), ())


def test_limit_256_applied_257_over_limit_0_none():
    full = tuple(range(1, MAX_SUPPLEMENTARY_GROUPS + 1))
    assert limit_supplementary_gids(full) == (full, SUPP_APPLIED)
    assert limit_supplementary_gids(tuple(range(1, MAX_SUPPLEMENTARY_GROUPS + 2))) == ((), SUPP_OVER_LIMIT)
    assert limit_supplementary_gids(()) == ((), SUPP_NONE)


@pytest.mark.parametrize("ident, problem", [
    ({}, None),                                                   # 빈 identity(빌더 테스트) 통과
    ({"gid": 10000}, None),                                       # 키 부재 = [](배포 전 잡)
    ({"gid": 10000, "supplementary_gids": None}, None),
    ({"gid": 10000, "supplementary_gids": [0, 20001]}, None),     # 0 허용(D3)
    ({"gid": 10000, "supplementary_gids": "10010"}, "supplementary_gids_not_list"),
    ({"gid": 10000, "supplementary_gids": (10010,)}, "supplementary_gids_not_list"),
    ({"gid": 10000, "supplementary_gids": [True]}, "supplementary_gid_invalid"),
    ({"gid": 10000, "supplementary_gids": ["10010"]}, "supplementary_gid_invalid"),
    ({"gid": 10000, "supplementary_gids": [-1]}, "supplementary_gid_invalid"),
    ({"gid": 10000, "supplementary_gids": [GID_MAX + 1]}, "supplementary_gid_invalid"),
    ({"gid": 10000, "supplementary_gids": [10000]}, "supplementary_gid_is_primary"),
    ({"gid": 10000, "supplementary_gids": [20002, 20001]}, "supplementary_gids_unsorted"),
    ({"gid": 10000, "supplementary_gids": [20001, 20001]}, "supplementary_gids_unsorted"),
    # 길이가 원소보다 먼저: 원소가 문자열이어도 too_many(위조된 거대 목록을 순회하지 않는다).
    ({"gid": 10000, "supplementary_gids": ["x"] * (MAX_SUPPLEMENTARY_GROUPS + 1)},
     "supplementary_gids_too_many"),
    ({"gid": 0, "privileged": True, "supplementary_gids": [20001]}, "supplementary_gids_on_privileged"),
])
def test_supplementary_gids_problem_shape_table(ident, problem):
    assert supplementary_gids_problem(ident) == problem


@pytest.mark.parametrize("ident, problem", [
    ({"gid": 10000, "supplementary_gids": [10010]}, None),        # 상태 키 부재(배포 전 잡) -- 결속 검사 안 함
    ({"gid": 10000, "supplementary_gids": [10010], "supplementary_gids_status": SUPP_APPLIED}, None),
    ({"gid": 10000, "supplementary_gids": [], "supplementary_gids_status": SUPP_NONE}, None),
    ({"gid": 10000, "supplementary_gids": [], "supplementary_gids_status": SUPP_OVER_LIMIT}, None),
    ({"gid": 10000, "supplementary_gids": [], "supplementary_gids_status": SUPP_DISABLED}, None),
    ({"gid": 0, "privileged": True, "supplementary_gids": [],
      "supplementary_gids_status": SUPP_PRIVILEGED}, None),
    ({"gid": 10000, "supplementary_gids": [10010], "supplementary_gids_status": SUPP_NONE},
     "supplementary_gids_status_mismatch"),
    ({"gid": 10000, "supplementary_gids": [], "supplementary_gids_status": SUPP_APPLIED},
     "supplementary_gids_status_mismatch"),
    ({"gid": 0, "privileged": True, "supplementary_gids": [], "supplementary_gids_status": SUPP_NONE},
     "supplementary_gids_status_mismatch"),
    ({"gid": 10000, "supplementary_gids": [], "supplementary_gids_status": SUPP_PRIVILEGED},
     "supplementary_gids_status_mismatch"),
    ({"gid": 10000, "supplementary_gids": [], "supplementary_gids_status": "bogus"},
     "supplementary_gids_status_mismatch"),
    # 비해시 값(리스트)이 frozenset 멤버십에서 TypeError 를 내지 않고 mismatch 로.
    ({"gid": 10000, "supplementary_gids": [], "supplementary_gids_status": ["applied"]},
     "supplementary_gids_status_mismatch"),
    ({"gid": 10000, "supplementary_gids_status": None}, "supplementary_gids_status_mismatch"),
])
def test_status_binding_table(ident, problem):
    assert supplementary_gids_problem(ident) == problem


BOB_G = ResolvedIdentity("bob", 10002, 10000, ("dmsusers", "proj"), False, group_gids=(10000, 20001, 20002))


def test_resolve_applies_groups_when_enabled(db):
    out = resolve_job_identity(_control(db), StubIdentityResolver({"bob": BOB_G}), requester_id="bob",
                               owner_username=None, allow_privileged=False,
                               privileged_requesters=frozenset(), supplementary_groups=True)
    assert out.supplementary_gids == (20001, 20002)          # 주 gid 10000 은 중복으로 빠진다
    assert out.supplementary_gids_status == SUPP_APPLIED
    assert out.supplementary_gids_found == 2 and out.supplementary_gids_excluded == ()
    assert out.group_gids == ()                                # 리졸버 원시값은 출력에 싣지 않는다
    assert out.groups == ("dmsusers", "proj")                  # denylist 이름 의미 불변


def test_switch_omitted_is_disabled(db):
    out = resolve_job_identity(_control(db), StubIdentityResolver({"bob": BOB_G}), requester_id="bob",
                               owner_username=None, allow_privileged=False,
                               privileged_requesters=frozenset())
    assert out.supplementary_gids == () and out.supplementary_gids_status == SUPP_DISABLED
    assert out.supplementary_gids_found is None                # 모름(보지 않았다) ≠ 0


def test_no_groups_is_none_found_zero(db):
    out = resolve_job_identity(_control(db), StubIdentityResolver({"alice": ALICE}), requester_id="alice",
                               owner_username=None, allow_privileged=False,
                               privileged_requesters=frozenset(), supplementary_groups=True)
    assert out.supplementary_gids == () and out.supplementary_gids_status == SUPP_NONE
    assert out.supplementary_gids_found == 0                   # 0 은 정상값("없음 확정")


def test_excluded_and_over_limit_status(db):
    many = ResolvedIdentity("carol", 10003, 10000, (), False,
                            group_gids=tuple(range(30000, 30000 + MAX_SUPPLEMENTARY_GROUPS + 1)) + (-5,))
    out = resolve_job_identity(_control(db), StubIdentityResolver({"carol": many}), requester_id="carol",
                               owner_username=None, allow_privileged=False,
                               privileged_requesters=frozenset(), supplementary_groups=True)
    assert out.supplementary_gids == () and out.supplementary_gids_status == SUPP_OVER_LIMIT
    assert out.supplementary_gids_found == MAX_SUPPLEMENTARY_GROUPS + 1
    assert out.supplementary_gids_excluded == (-5,)


def test_privileged_never_carries_groups(db):
    class _Boom:
        def resolve(self, username, *, deadline=None):
            raise AssertionError("특권 경로는 denylist 그룹 규칙이 없으면 LDAP 를 부르지 않는다")
    out = resolve_job_identity(_control(db), _Boom(), requester_id="ops", owner_username=None,
                               allow_privileged=True, privileged_requesters=frozenset({"ops"}),
                               session_authenticated=True, privilege=PRIVILEGE_REQUESTED,
                               supplementary_groups=True)
    assert out.privileged and out.supplementary_gids == ()
    assert out.supplementary_gids_status == SUPP_PRIVILEGED and out.supplementary_gids_found is None


def test_primary_gid_zero_without_privilege_rejected(db):
    control = _control(db)
    rootgrp = ResolvedIdentity("dave", 10004, 0, (), False)
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, StubIdentityResolver({"dave": rootgrp}), requester_id="dave",
                             owner_username=None, allow_privileged=False, privileged_requesters=frozenset())
    assert e.value.reason_code == "identity_root_group_without_privilege"
    assert control.probe_targets(ttl_seconds=3600) == []
    # uid 0 이고 gid 0 이면 uid 거부가 먼저(더 정확한 사유).
    rooty = ResolvedIdentity("rooty", 0, 0, (), False)
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, StubIdentityResolver({"rooty": rooty}), requester_id="rooty",
                             owner_username=None, allow_privileged=False, privileged_requesters=frozenset())
    assert e.value.reason_code == "identity_root_without_privilege"


def test_supplementary_gid_zero_is_allowed(db):
    # D3: 보조 gid 0 은 인정(사용자 승인 위험) -- 주 gid 0 과 다르다.
    ident = ResolvedIdentity("erin", 10005, 10000, (), False, group_gids=(0, 20001))
    out = resolve_job_identity(_control(db), StubIdentityResolver({"erin": ident}), requester_id="erin",
                               owner_username=None, allow_privileged=False,
                               privileged_requesters=frozenset(), supplementary_groups=True)
    assert out.supplementary_gids == (0, 20001) and out.supplementary_gids_status == SUPP_APPLIED


@pytest.mark.parametrize("owner, admin, allow, listed, expected", [
    (None, False, False, False, True),
    ("alice", False, False, False, True),         # 자신
    (" alice", False, False, False, False),       # 원문 비교 -- API 의 403 유지
    ("", False, False, False, False),
    (123, False, False, False, False),
    (["alice"], False, False, False, False),
    ("bob", True, True, True, True),              # 자격 관리자
    ("bob", True, True, False, False),            # 관리자지만 목록 밖
    ("bob", True, False, True, False),            # allow_privileged 꺼짐
    ("bob", False, True, True, False),            # 목록에 있지만 관리자 아님
    (123, True, True, True, False),               # 비문자열은 자격과 무관하게 거부
])
def test_owner_override_allowed_matrix(owner, admin, allow, listed, expected):
    assert owner_override_allowed(
        owner_username=owner, requester_id="alice", requester_is_admin=admin,
        allow_privileged=allow,
        privileged_requesters=frozenset({"alice"}) if listed else frozenset()) is expected


def test_check_chown_group():
    applied = ResolvedIdentity("bob", 10002, 10000, (), False, supplementary_gids=(20001,),
                               supplementary_gids_status=SUPP_APPLIED)
    check_chown_group("10002:10000", identity=applied)       # 주 gid
    check_chown_group(":20001", identity=applied)            # 적용된 보조 gid
    with pytest.raises(IdentityRejected) as e:
        check_chown_group("10002:30000", identity=applied)
    assert e.value.reason_code == "chown_group_not_member"
    # 대상 아님: uid 만·이름·형식 오류(그 거부는 chown_problem 경로)
    for value in ("10002", "bob:proj", "1:2:3", "", None, 5):
        check_chown_group(value, identity=applied)
    # root 는 검사 안 함
    root = ResolvedIdentity("ops", 0, 0, (), True, supplementary_gids_status=SUPP_PRIVILEGED)
    check_chown_group("0:30000", identity=root)
    # over_limit·disabled·none 이면 적용 목록이 비어 주 gid 만
    for status in (SUPP_OVER_LIMIT, SUPP_DISABLED, SUPP_NONE):
        ident = ResolvedIdentity("bob", 10002, 10000, (), False, supplementary_gids_status=status)
        check_chown_group(":10000", identity=ident)
        with pytest.raises(IdentityRejected):
            check_chown_group(":20001", identity=ident)


def test_stub_resolver_accepts_deadline():
    r = StubIdentityResolver({"alice": ALICE})
    assert r.resolve("alice", deadline=0.0) is ALICE         # stub 은 마감을 무시한다
    with pytest.raises(IdentityUnavailable):
        StubIdentityResolver({}, unavailable=True).resolve("alice", deadline=1.0)


def test_lookup_invalid_is_unavailable_subclass(db):
    # 사용자별 데이터 오류도 계획 시점엔 ldap_unavailable(기존 의미) -- 서킷만 다르게 다룬다(planner·stepper).
    assert issubclass(IdentityLookupInvalid, IdentityUnavailable)

    class _Dup:
        def resolve(self, username, *, deadline=None):
            raise IdentityLookupInvalid("duplicate user entries: 2")
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(_control(db), _Dup(), requester_id="alice", owner_username=None,
                             allow_privileged=False, privileged_requesters=frozenset())
    assert e.value.reason_code == "ldap_unavailable"
