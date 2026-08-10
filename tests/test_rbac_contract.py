"""10-rbac.yaml 계약 테스트(슬라이스 17 설계 §5). RBAC 를 붙잡는 테스트가 지금까지
0건이라 규칙을 잘못 써도 배포 전까지 아무 신호가 없었다(설계 §1-10) -- 그리고 큐
읽기의 실패 모드는 유난히 조용하다: 403 은 화면에서 "알 수 없음"일 뿐 아무것도
죽지 않는다.

두 함정을 못박는다:
- resourceNames 는 list 에 적용되지 않는다(10-rbac.yaml 이 두 번 적어 둔 함정) --
  podgroups list 규칙에 붙이면 모든 list 가 조용히 403 이 되고, Queue 를 list 로
  열면 resourceNames 가 무력화되어 클러스터의 모든 큐가 열린다.
- ClusterRole 은 바인딩 없이 무효다 -- Binding 까지 함께 계약이다.

문서 분리·kind/name 매칭은 manifest_tags 의 승격 파서를 쓰고, rules 파싱만 이
파일 전용이다: 규칙 값이 전부 더블쿼트 flow 시퀀스(JSON 호환)라 json.loads 로
충분하다."""
import json
from pathlib import Path

from dms.manifest_tags import workload_doc

RBAC = Path(__file__).resolve().parent.parent / "deploy" / "k8s" / "10-rbac.yaml"


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _top_block(doc, key):
    """최상위 key 의 몸통(더 깊게 들여쓴 연속 구간). 없으면 시끄럽게 실패한다."""
    at = next((i for i, line in enumerate(doc)
               if _indent(line) == 0 and line.strip().startswith(f"{key}:")), None)
    assert at is not None, f"{key}: 최상위 키가 없다"
    body = []
    for line in doc[at + 1:]:
        if _indent(line) == 0:
            break
        body.append(line)
    return body


def _rules(doc):
    """rules: 블록 -> [{apiGroups, resources, resourceNames?, verbs}]."""
    rules = []
    for line in _top_block(doc, "rules"):
        stripped = line.strip()
        if stripped.startswith("- "):
            rules.append({})
            stripped = stripped[2:]
        key, _, value = stripped.partition(":")
        rules[-1][key.strip()] = json.loads(value.strip())
    return rules


def _find_rule(rules, api_group, resource):
    matched = [r for r in rules if api_group in r.get("apiGroups", [])
               and resource in r.get("resources", [])]
    assert len(matched) == 1, (api_group, resource, matched)
    return matched[0]


def test_api_role_lists_podgroups_without_resource_names():
    doc = workload_doc(RBAC, "Role", "dms-api")
    assert doc is not None, "Role dms-api 문서가 없다"
    rule = _find_rule(_rules(doc), "scheduling.volcano.sh", "podgroups")
    assert set(rule["verbs"]) == {"get", "list"}
    # resourceNames 는 list 에 적용되지 않는다 -- 여기 붙는 순간 모든 list 가
    # 조용히 403 이 되어 화면이 영구 "알 수 없음"이 된다.
    assert "resourceNames" not in rule


def test_queue_clusterrole_is_named_get_only():
    doc = workload_doc(RBAC, "ClusterRole", "dms-api-queue-readonly")
    assert doc is not None, "ClusterRole dms-api-queue-readonly 문서가 없다"
    rule = _find_rule(_rules(doc), "scheduling.volcano.sh", "queues")
    # 이름 지정 GET 만(설계 §2.1 최소 표면): list 를 열면 resourceNames 가
    # 무력화되어 클러스터의 모든 큐가 열린다.
    assert rule["verbs"] == ["get"]
    assert rule["resourceNames"] == ["dms-data"]


def test_queue_clusterrole_bound_to_api_service_account():
    doc = workload_doc(RBAC, "ClusterRoleBinding", "dms-api-queue-readonly")
    assert doc is not None, "ClusterRoleBinding 이 없다 -- ClusterRole 은 바인딩 없이 무효다"
    role_ref = [line.strip() for line in _top_block(doc, "roleRef")]
    assert "kind: ClusterRole" in role_ref
    assert "name: dms-api-queue-readonly" in role_ref
    subjects = [line.strip() for line in _top_block(doc, "subjects")]
    assert "- kind: ServiceAccount" in subjects
    assert "name: dms-api" in subjects
    assert "namespace: dms" in subjects


def test_controller_role_untouched_by_queue_visibility():
    # 큐를 읽는 소비자는 api 라우트 하나뿐이다 -- controller 에 권한이 새면 최소
    # 표면 원칙이 깨진다. "실수로 양쪽에 붙이는" 리뷰 누락을 여기서 잡는다.
    doc = workload_doc(RBAC, "Role", "dms-controller")
    assert doc is not None
    grants = [r for r in _rules(doc)
              if "scheduling.volcano.sh" in r.get("apiGroups", [])]
    assert grants == []
