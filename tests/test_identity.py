"""실행 신원 판정(identity.py -- dms-k8s 는 k8s 신원 모드 전용, 2026-10-26 LDAP 코드 삭제).

통과하면 늘 root(uid 0·gid 0)이고, root 근거가 없거나 자격이 없으면 privileged_not_authorized 로 거부한다(예전엔 「실행 신원의
LDAP uid/gid」로 낮췄다 -- LDAP 이 없어 낮출 신원이 없다). denylist 는 이름 규칙만 본다(그룹 규칙은 평가하지 않는다).
"""
import pytest
from dms.identity import (
    PRIVILEGE_IF_ELIGIBLE, PRIVILEGE_NEVER, PRIVILEGE_REQUESTED, IdentityRejected, ResolvedIdentity,
    owner_override_allowed, privilege_eligible, resolve_job_identity, root_allowed)
from dms.repositories.control import ControlRepository

LISTED = frozenset({"ops"})


def test_resolved_identity_is_frozen():
    ident = ResolvedIdentity("alice", 0, 0, (), True)
    assert ident.uid == 0 and ident.groups == ()
    with pytest.raises(Exception):
        ident.uid = 1  # frozen


def test_identity_rejected_carries_reason():
    err = IdentityRejected("identity_denied", "mallory")
    assert err.reason_code == "identity_denied" and "mallory" in str(err)


def _resolve(db, *, requester="ops", owner=None, privilege=PRIVILEGE_REQUESTED, session=True, allow=True):
    return resolve_job_identity(ControlRepository(db), requester_id=requester, owner_username=owner,
                                allow_privileged=allow, privileged_requesters=LISTED,
                                session_authenticated=session, privilege=privilege)


def _reason(fn):
    with pytest.raises(IdentityRejected) as e:
        fn()
    return e.value.reason_code


def test_requested_session_request_is_root_for_anyone(db):
    # REQUESTED(제출이 늘 싣는다 -- 설계 D2): 세션 요청이면 목록 밖 사용자도 root -- 권한 판정은 k8s 맵핑(k8s_access) 몫이다.
    for who in ("ops", "alice"):
        out = _resolve(db, requester=who)
        assert (out.username, out.uid, out.gid, out.groups, out.privileged) == (who, 0, 0, (), True)


def test_owner_username_is_the_run_identity_name(db):
    out = _resolve(db, owner="alice")
    assert (out.username, out.uid, out.privileged) == ("alice", 0, True)
    assert _resolve(db, owner=" alice ").username == "alice"       # strip(이름 모양 422 는 API·planner 몫)


def test_requested_needs_a_session_and_the_privilege_switch(db):
    # 공유 토큰(모든 노드 에이전트 보유)은 root 를 못 얻는다 -- 기본값도 fail-closed(session_authenticated=False).
    assert _reason(lambda: _resolve(db, session=False)) == "privileged_not_authorized"
    assert _reason(lambda: _resolve(db, allow=False)) == "privileged_not_authorized"
    with pytest.raises(IdentityRejected):
        resolve_job_identity(ControlRepository(db), requester_id="ops", owner_username=None, allow_privileged=True,
                             privileged_requesters=LISTED, privilege=PRIVILEGE_REQUESTED)


def test_batch_children_need_the_privileged_list(db):
    # IF_ELIGIBLE(배치 자식): 자격(허용·세션·목록) 있으면 root, 없으면 거부 -- 목록에서 빠진 생성자의 자식을 낮추지 않는다.
    assert _resolve(db, privilege=PRIVILEGE_IF_ELIGIBLE).privileged is True
    assert _reason(lambda: _resolve(db, requester="gone", privilege=PRIVILEGE_IF_ELIGIBLE)) \
        == "privileged_not_authorized"
    assert _reason(lambda: _resolve(db, privilege=PRIVILEGE_IF_ELIGIBLE, session=False)) \
        == "privileged_not_authorized"


def test_no_root_basis_is_refused_not_downgraded(db):
    # PRIVILEGE_NEVER(run_as_root 없는 행 -- DB 직접 삽입·옛 행): 자격이 있어도 거부.
    assert _reason(lambda: _resolve(db, privilege=PRIVILEGE_NEVER)) == "privileged_not_authorized"


def test_denylist_is_the_kill_switch_before_eligibility(db):
    control = ControlRepository(db)
    control.deny("requester", "mallory", None, "admin")
    control.deny("owner", "victim", None, "admin")
    assert _reason(lambda: _resolve(db, requester="mallory")) == "identity_denied"
    assert _reason(lambda: _resolve(db, owner="victim")) == "identity_denied"
    # 근거 없는(NEVER)·자격 없는 배치 자식도 denylist 가 먼저다 -- 막힌 사용자는 이유가 「막힘」으로 보인다.
    assert _reason(lambda: _resolve(db, requester="mallory", privilege=PRIVILEGE_NEVER)) == "identity_denied"
    # 단 REQUESTED 의 root 허용(세션) 판정은 그보다 먼저다(종전 순서 -- 토큰 요청은 사유가 privileged_not_authorized).
    assert _reason(lambda: _resolve(db, requester="mallory", session=False)) == "privileged_not_authorized"


def test_group_rules_are_not_evaluated(db):
    # LDAP 그룹이 없다 -- 모드 전환 전에 남은 group 행이 아무것도 막지 않는다(예전엔 리졸버 없음 ldap_not_configured 로
    # 모든 잡을 막았다).
    ControlRepository(db).deny("group", "dmsusers", None, "admin")
    assert _resolve(db, requester="alice").privileged is True


def test_root_allowed_and_privilege_eligible_predicates():
    assert root_allowed(allow_privileged=True, session_authenticated=True) is True
    assert root_allowed(allow_privileged=True, session_authenticated=False) is False
    assert root_allowed(allow_privileged=False, session_authenticated=True) is False
    kw = dict(allow_privileged=True, privileged_requesters=LISTED)
    assert privilege_eligible(requester_id="ops", session_authenticated=True, **kw) is True
    assert privilege_eligible(requester_id="alice", session_authenticated=True, **kw) is False
    assert privilege_eligible(requester_id="ops", session_authenticated=False, **kw) is False


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
