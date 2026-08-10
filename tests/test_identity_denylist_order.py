import pytest
from dms.identity import IdentityRejected, ResolvedIdentity, StubIdentityResolver, resolve_job_identity
from dms.repositories import Repositories


def test_group_denylist_blocks_privileged_requester(db):
    repos = Repositories(db)
    repos.control.deny("group", "wheel", "정책 위반", "admin")
    root_ish = ResolvedIdentity("root-ish", 1000, 1000, ("wheel",), False)
    resolver = StubIdentityResolver({"root-ish": root_ish})
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(repos.control, resolver,
                             requester_id="admin", owner_username="root-ish",
                             allow_privileged=True, privileged_requesters=("admin",))
    assert e.value.reason_code == "identity_denied"


def test_privileged_path_needs_no_resolver_when_no_group_denies(db):
    repos = Repositories(db)
    repos.control.deny("requester", "mallory", None, "admin")
    identity = resolve_job_identity(repos.control, None,
                                    requester_id="admin", owner_username="alice",
                                    allow_privileged=True, privileged_requesters=("admin",),
                                    session_authenticated=True)
    assert identity.privileged is True
    assert identity.uid == 0


def test_has_group_denies(db):
    repos = Repositories(db)
    assert repos.control.has_group_denies() is False
    repos.control.deny("requester", "bob", None, "admin")
    assert repos.control.has_group_denies() is False
    repos.control.deny("group", "wheel", None, "admin")
    assert repos.control.has_group_denies() is True


def test_group_denylist_privileged_path_fails_closed_without_resolver(db):
    # group 규칙이 등재된 상태에서 resolver가 없으면(LDAP 미설정) 특권 경로도
    # fail-closed 한다 — 그룹을 확인할 수 없으니 통과시키지 않는다.
    repos = Repositories(db)
    repos.control.deny("group", "wheel", None, "admin")
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(repos.control, None,
                             requester_id="admin", owner_username="root-ish",
                             allow_privileged=True, privileged_requesters=("admin",))
    assert e.value.reason_code == "ldap_not_configured"


def test_group_denylist_privileged_path_fails_closed_on_ldap_unavailable(db):
    # group 규칙이 등재된 상태에서 LDAP 조회가 실패하면(IdentityUnavailable) 특권
    # 경로도 fail-closed 한다.
    repos = Repositories(db)
    repos.control.deny("group", "wheel", None, "admin")
    resolver = StubIdentityResolver({}, unavailable=True)
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(repos.control, resolver,
                             requester_id="admin", owner_username="root-ish",
                             allow_privileged=True, privileged_requesters=("admin",))
    assert e.value.reason_code == "ldap_unavailable"


def test_group_denylist_privileged_path_treats_missing_ldap_entry_as_groupless(db):
    # 의도된 계약(리뷰 완료, 스펙 담당자가 별도로 재검토할 사안): group denylist
    # 규칙이 있어도 owner가 LDAP에 없으면(resolve()가 None) groups=[]로 취급되어
    # 이름으로 별도 denylist에 걸리지 않는 한 특권 경로는 root를 발급한다. "LDAP에
    # 없는 owner는 소속 그룹이 없다"는 사실을 그대로 반영한 것으로, 우회가 아니다.
    # 비특권 경로는 같은 조건에서 ldap_identity_not_found로 fail-closed하는 것과
    # 비대칭이라는 점이 리뷰에서 지적됐다 — 이 테스트는 우회를 정당화하는 게 아니라
    # 현재 동작을 눈에 보이게 고정해 향후 회귀를 막기 위한 것이다.
    repos = Repositories(db)
    repos.control.deny("group", "wheel", None, "admin")
    resolver = StubIdentityResolver({})  # "ghost"는 등록되어 있지 않음 → resolve() -> None
    identity = resolve_job_identity(repos.control, resolver,
                                    requester_id="admin", owner_username="ghost",
                                    allow_privileged=True, privileged_requesters=("admin",),
                                    session_authenticated=True)
    assert identity.privileged is True
    assert identity.groups == ()
