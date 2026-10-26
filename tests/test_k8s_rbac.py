"""k8s RoleBinding → DMS 사용자 권한 맵핑(dms-k8s 설계 D5). 사용자 예시 바인딩 목록 그대로 + KFAM 꼴 + 인식하지 않는
바인딩(namespaceAdmin·default-editor·kubeflow-view·roleRef 불일치)."""
import json
from pathlib import Path

import pytest

from dms.agent.k8s_fetch import project_role_binding
from dms.k8s_rbac import derive_binding, dms_user_from_binding_name, is_recognized

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "k8s"


def _rb(name, role_name="kubeflow-admin", role_kind="ClusterRole", subjects=None, namespace="ns1"):
    return {"namespace": namespace, "name": name, "role_kind": role_kind, "role_name": role_name,
            "subjects": subjects if subjects is not None else []}


def _user(name):
    return {"kind": "User", "name": name, "namespace": None}


# 사용자 지시 원문의 예시: `rolebindings -A` NAME 열이 <user>-clusterrole-admin|edit, ROLE 이 ClusterRole/kubeflow-admin|edit.
@pytest.mark.parametrize("name,role,expected_user,expected_access", [
    ("junwoo-song-clusterrole-admin", "kubeflow-admin", "junwoo.song", "admin"),
    ("junwoo-song-clusterrole-edit", "kubeflow-edit", "junwoo.song", "edit"),
    ("cocoa-song-clusterrole-edit", "kubeflow-edit", "cocoa.song", "edit"),
    ("alice-clusterrole-admin", "kubeflow-admin", "alice", "admin"),
    ("kim-jh-lee-clusterrole-edit", "kubeflow-edit", "kim.jh.lee", "edit"),
    # 이름의 level 과 roleRef 가 엇갈려도 access 는 이름의 level(둘 다 rw -- 구분은 표시용)
    ("alice-clusterrole-edit", "kubeflow-admin", "alice", "edit"),
])
def test_user_examples_are_recognized(name, role, expected_user, expected_access):
    d = derive_binding(_rb(name, role))
    assert d["dms_user"] == expected_user and d["access"] == expected_access
    assert d["subject_mismatch"] is None          # User subject 가 없으면 해당 없음(null ≠ 0)
    assert dms_user_from_binding_name(name) == expected_user


def test_subject_mismatch_compares_name_and_email_local_part():
    ok = derive_binding(_rb("junwoo-song-clusterrole-admin", subjects=[_user("junwoo.song@example.com")]))
    assert ok == {"dms_user": "junwoo.song", "access": "admin", "subject_mismatch": 0}
    bare = derive_binding(_rb("junwoo-song-clusterrole-admin", subjects=[_user("junwoo.song")]))
    assert bare["subject_mismatch"] == 0
    upper = derive_binding(_rb("junwoo-song-clusterrole-admin", subjects=[_user("Junwoo.Song@Example.com")]))
    assert upper["subject_mismatch"] == 0           # 소문자로 비교
    other = derive_binding(_rb("junwoo-song-clusterrole-admin", subjects=[_user("someone@example.com")]))
    assert other["subject_mismatch"] == 1 and other["dms_user"] == "junwoo.song"   # 권한은 이름 규칙 그대로 + 경고
    # 하이픈 ID 는 역변환이 틀린다(junwoo-song → junwoo.song): subject 가 junwoo-song 이면 불일치로 드러난다
    hyphen = derive_binding(_rb("junwoo-song-clusterrole-edit", "kubeflow-edit", subjects=[_user("junwoo-song@x.io")]))
    assert hyphen["subject_mismatch"] == 1
    # 여럿 중 하나라도 같으면 일치, SA subject 는 비교 대상이 아니다
    mixed = derive_binding(_rb("alice-clusterrole-edit", "kubeflow-edit",
                               subjects=[{"kind": "ServiceAccount", "name": "alice", "namespace": "ns1"},
                                         _user("bob@x.io"), _user("alice@x.io")]))
    assert mixed["subject_mismatch"] == 0
    sa_only = derive_binding(_rb("alice-clusterrole-edit", "kubeflow-edit",
                                 subjects=[{"kind": "ServiceAccount", "name": "alice", "namespace": "ns1"}]))
    assert sa_only["subject_mismatch"] is None


def test_kfam_style_name_is_recognized_with_a_wrong_id_and_a_mismatch_warning():
    # Kubeflow KFAM 표준 이름(user-<이메일 정규화>-clusterrole-edit) -- 이름 규칙상 인식되지만 파생 ID 가 계정과 다르다.
    d = derive_binding(_rb("user-yo-hong-example-com-clusterrole-edit", "kubeflow-edit",
                           subjects=[_user("yo.hong@example.com")]))
    assert d == {"dms_user": "user.yo.hong.example.com", "access": "edit", "subject_mismatch": 1}


@pytest.mark.parametrize("binding", [
    _rb("namespaceAdmin", "kubeflow-admin", subjects=[_user("owner@example.com")]),          # 프로필 소유자
    _rb("default-editor", "kubeflow-edit",
        subjects=[{"kind": "ServiceAccount", "name": "default-editor", "namespace": "ns1"}]),  # SA
    _rb("bob-clusterrole-view", "kubeflow-view", subjects=[_user("bob@example.com")]),        # view
    _rb("bob-clusterrole-edit", "edit", subjects=[_user("bob@example.com")]),                 # roleRef 가 기본 edit
    _rb("bob-clusterrole-edit", "kubeflow-edit", role_kind="Role"),                           # Role(namespace 범위)
    _rb("bob-clusterrole-admin-extra", "kubeflow-admin"),                                     # 접미가 정확하지 않다
    _rb("-clusterrole-admin", "kubeflow-admin"),                                              # 빈 ID
    _rb("aip-serving-srv", "kubeflow-edit"),
])
def test_bindings_outside_the_rule_are_unrecognized(binding):
    assert derive_binding(binding) == {"dms_user": None, "access": None, "subject_mismatch": None}
    assert is_recognized(binding) is False


def test_synthetic_fixture_rolebindings_end_to_end():
    # 투영(에이전트 §4.1) → 맵핑. 테스트베드 §8 픽스처의 바인딩 구성 그대로.
    raw = json.loads((FIXTURES / "synthetic-rolebindings.json").read_text())
    derived = {(p["namespace"], p["name"]): derive_binding(p)
               for p in (project_role_binding(item) for item in raw["items"])}
    recognized = {k: (v["dms_user"], v["access"], v["subject_mismatch"]) for k, v in derived.items() if v["dms_user"]}
    assert recognized == {
        ("team-a", "junwoo-song-clusterrole-admin"): ("junwoo.song", "admin", 0),
        ("team-a", "alice-clusterrole-edit"): ("alice", "edit", 0),
        ("team-b", "alice-clusterrole-admin"): ("alice", "admin", 0),
        ("team-b", "cocoa-song-clusterrole-edit"): ("cocoa.song", "edit", 0),
        ("team-a", "user-yo-hong-example-com-clusterrole-edit"): ("user.yo.hong.example.com", "edit", 1),
    }
    unrecognized = sorted(k[1] for k, v in derived.items() if not v["dms_user"])
    assert unrecognized == ["bob-clusterrole-edit", "bob-clusterrole-view", "default-editor", "namespaceAdmin"]


# --- namespace 소유자(namespaceAdmin) -- 2026-10-26 사용자 결정 (a) ----------------------------------------------

def test_namespace_admin_owner_maps_to_the_email_local_part_as_admin_when_the_domain_matches():
    owner = _rb("namespaceAdmin", "kubeflow-admin", subjects=[_user("Yo.Hong@Example.com")])
    assert derive_binding(owner, email_domain="example.com") == {"dms_user": "yo.hong", "access": "admin",
                                                                 "subject_mismatch": 0}
    assert derive_binding(owner, email_domain="@EXAMPLE.com")["dms_user"] == "yo.hong"     # 앞 @·대소문자 관용
    assert is_recognized(owner, email_domain="example.com") is True
    # 도메인을 모르면(설정 없는 호출) 인식하지 않는다 -- fail-closed(종전 동작)
    assert derive_binding(owner)["dms_user"] is None


@pytest.mark.parametrize("binding", [
    _rb("namespaceAdmin", "kubeflow-admin", subjects=[_user("yo.hong@other.com")]),            # 다른 도메인 -- 남의 사람
    _rb("namespaceAdmin", "kubeflow-admin", subjects=[_user("yo.hong")]),                      # 이메일이 아니다
    _rb("namespaceAdmin", "kubeflow-admin", subjects=[_user("a@b@example.com")]),              # @ 둘
    _rb("namespaceAdmin", "kubeflow-admin", subjects=[_user("1234@example.com")]),             # 숫자만 아이디(거부 규칙)
    _rb("namespaceAdmin", "kubeflow-admin", subjects=[_user("Bad Name@example.com")]),         # 계정 아이디 규칙 밖
    _rb("namespaceAdmin", "kubeflow-admin",
        subjects=[_user("a@example.com"), _user("b@example.com")]),                            # User 둘 -- 한 칸에 못 담는다
    _rb("namespaceAdmin", "kubeflow-admin",
        subjects=[{"kind": "ServiceAccount", "name": "x", "namespace": "ns1"}]),               # SA
    _rb("namespaceAdmin", "kubeflow-edit", subjects=[_user("yo.hong@example.com")]),           # roleRef 가 admin 이 아니다
    _rb("namespaceAdmin", "kubeflow-admin", role_kind="Role", subjects=[_user("yo.hong@example.com")]),
    _rb("namespaceadmin", "kubeflow-admin", subjects=[_user("yo.hong@example.com")]),          # 이름은 정확히
])
def test_namespace_admin_outside_the_rule_stays_unrecognized(binding):
    assert derive_binding(binding, email_domain="example.com") == {"dms_user": None, "access": None,
                                                                   "subject_mismatch": None}


def test_view_bindings_stay_unrecognized_even_with_the_email_domain():
    # kubeflow-view(읽기)는 권한으로 보지 않는다(2026-10-26 사용자 결정 (b))
    assert derive_binding(_rb("bob-clusterrole-view", "kubeflow-view", subjects=[_user("bob@example.com")]),
                          email_domain="example.com")["dms_user"] is None
