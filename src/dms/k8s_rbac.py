"""k8s RoleBinding → DMS 사용자 권한 맵핑 규칙(dms-k8s 설계 D5, 2026-10-25 사용자 지시 그대로). 순수 함수.

인식 규칙(셋 다 만족해야 한다):
  - roleRef.kind == "ClusterRole"
  - roleRef.name ∈ {kubeflow-admin, kubeflow-edit}
  - 바인딩 이름이 `^(?P<id>.+)-clusterrole-(?P<level>admin|edit)$`
그러면 dms_user = id 의 '-' 를 '.' 로 바꾼 값(junwoo-song-clusterrole-admin → junwoo.song), access = level. admin·edit 는
둘 다 그 namespace 의 모든 PVC 읽기·쓰기다(구분은 표시용).

이 규칙 밖의 바인딩(namespaceAdmin·default-editor·kubeflow-view·SA 바인딩·이름은 맞지만 roleRef 가 다른 것)은 「인식하지
않은 바인딩」 -- 저장·표시만 하고 권한을 주지 않는다. 그 판단(소유자·이메일 subject 기반 ID 를 인정할지)은 사용자 결정
대기다(설계 §10).

이름 변환은 손실이다(KFAM 은 '.'·'_'·'@' 를 모두 '-' 로 접는다) -- 그래서 인식된 바인딩마다 subject_mismatch 를 계산해 화면이
경고한다: User subject 의 이름 또는 이메일 로컬파트(@ 앞)가 파생 ID 와 같은 것이 하나도 없으면 1, 하나라도 같으면 0, User
subject 가 아예 없으면 None(해당 없음 -- null ≠ 0). 비교는 소문자로 한다(k8s 이름은 소문자, DMS 계정도 소문자).
맵핑 ID 는 DMS 계정 username 과 **정확히** 같아야 권한이 된다(계정 존재 여부는 조회 시점에 따로 본다).
"""
import re

# roleRef.name → 인식 여부(값은 표시용 이름). 새 ClusterRole 을 인정하려면 여기 한 줄 + 사용자 결정.
RECOGNIZED_CLUSTER_ROLES = frozenset({"kubeflow-admin", "kubeflow-edit"})
_NAME_RE = re.compile(r"^(?P<id>.+)-clusterrole-(?P<level>admin|edit)$")
ACCESS_LEVELS = ("admin", "edit")


def dms_user_from_binding_name(name) -> "str | None":
    """바인딩 이름에서 파생 ID 를 뽑는다(이름 규칙만 -- roleRef 는 보지 않는다). 규칙 밖이면 None."""
    if not isinstance(name, str):
        return None
    m = _NAME_RE.match(name)
    if m is None:
        return None
    return m.group("id").replace("-", ".")


def _subject_ids(subjects) -> "list[str] | None":
    """User subject 마다 비교 후보(이름 전체, 이메일이면 로컬파트도) -- User 가 없으면 None."""
    users = [s for s in (subjects or []) if isinstance(s, dict) and s.get("kind") == "User"
             and isinstance(s.get("name"), str) and s.get("name")]
    if not users:
        return None
    out = []
    for s in users:
        name = s["name"].strip().lower()
        out.append(name)
        if "@" in name:
            out.append(name.split("@", 1)[0])
    return out


def derive_binding(binding: dict) -> dict:
    """투영된 RoleBinding(§4.1: namespace, name, role_kind, role_name, subjects) → 권한 칸 셋.

    {"dms_user": str|None, "access": "admin"|"edit"|None, "subject_mismatch": 0|1|None}
    인식하지 않은 바인딩은 세 칸 모두 None 이다."""
    none = {"dms_user": None, "access": None, "subject_mismatch": None}
    if binding.get("role_kind") != "ClusterRole" or binding.get("role_name") not in RECOGNIZED_CLUSTER_ROLES:
        return none
    name = binding.get("name")
    if not isinstance(name, str):
        return none
    m = _NAME_RE.match(name)
    if m is None:
        return none
    dms_user = m.group("id").replace("-", ".")
    candidates = _subject_ids(binding.get("subjects"))
    if candidates is None:
        mismatch = None
    else:
        mismatch = 0 if dms_user.lower() in candidates else 1
    return {"dms_user": dms_user, "access": m.group("level"), "subject_mismatch": mismatch}


def is_recognized(binding: dict) -> bool:
    return derive_binding(binding)["dms_user"] is not None
