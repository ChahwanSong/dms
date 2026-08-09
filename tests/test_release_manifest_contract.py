"""I3: COMPONENTS 표가 deploy/k8s 매니페스트와 실제로 일치하는지 대조한다.

기존 test_components_carry_real_container_names 는 문자열 리터럴에 대고 단언할
뿐이라 매니페스트가 바뀌어도 아무것도 빨간불이 되지 않는다. 이 어긋남은 조용하다:
strategic merge patch 에서 containers 의 patchMergeKey 는 name 이라, COMPONENTS
의 컨테이너 이름이 매니페스트와 다르면 patch 가 **실패하지 않고** name/image 만
가진 엉터리 둘째 컨테이너를 추가한다. 결과는 영원히 Ready 가 안 되는 파드,
600초 PDE 대기, 그리고 YAML 재적용 전까지 오염된 워크로드다(설계 §1 표의 함정).

PyYAML 은 이 저장소의 의존성이 아니다(런타임도 테스트도 쓰지 않는다). 파서 하나를
들이는 대신 매니페스트가 실제로 쓰는 YAML 부분집합만 읽는다 -- 블록 매핑/시퀀스,
주석, 다중 문서. 앵커/블록 스칼라/복합 키는 이 파일들에 없다. 추출이 빗나가면
조용히 통과하는 대신 아래 단언이 대상을 못 찾아 빨간불이 되도록, 모든 조회에
"찾았는가"를 먼저 단언한다.
"""
from pathlib import Path

import pytest
from dms.repositories.releases import COMPONENTS, ROLLOUT_ORDER

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFESTS = {
    "dms-api": REPO_ROOT / "deploy" / "k8s" / "40-api.yaml",
    "dms-controller": REPO_ROOT / "deploy" / "k8s" / "41-controller.yaml",
    "dms-agent": REPO_ROOT / "deploy" / "k8s" / "50-agent-daemonset.yaml",
}


def _strip_comment(line: str) -> str:
    """따옴표 밖의 ' #' 이후를 버린다. 값 안의 '#'(없지만)을 지우지 않기 위해서다."""
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _documents(path: Path) -> "list[list[str]]":
    docs, cur = [], []
    for raw in path.read_text().splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if line.strip() == "---":
            docs.append(cur)
            cur = []
        else:
            cur.append(line)
    docs.append(cur)
    return [d for d in docs if d]


def _block(lines: "list[str]", start: int) -> "list[str]":
    """lines[start] 보다 깊게 들여쓴 연속 구간(그 키의 몸통)."""
    base = _indent(lines[start])
    body = []
    for line in lines[start + 1:]:
        if _indent(line) <= base:
            break
        body.append(line)
    return body


def _find(lines: "list[str]", key: str, indent: "int | None" = None) -> "int | None":
    for idx, line in enumerate(lines):
        if line.strip().startswith(f"{key}:") and (indent is None
                                                   or _indent(line) == indent):
            return idx
    return None


def _value(line: str) -> str:
    return line.split(":", 1)[1].strip()


def _workload_doc(path: Path, kind: str, name: str) -> "list[str] | None":
    for doc in _documents(path):
        kind_at = _find(doc, "kind", indent=0)
        if kind_at is None or _value(doc[kind_at]) != kind:
            continue
        meta_at = _find(doc, "metadata", indent=0)
        if meta_at is None:
            continue
        meta = _block(doc, meta_at)
        name_at = _find(meta, "name")
        if name_at is not None and _value(meta[name_at]) == name:
            return doc
    return None


def _match_labels(doc: "list[str]") -> dict:
    spec_at = _find(doc, "spec", indent=0)
    assert spec_at is not None, "워크로드 문서에 최상위 spec 이 없다"
    spec = _block(doc, spec_at)
    sel_at = _find(spec, "selector", indent=_indent(spec[0]))
    assert sel_at is not None, "spec.selector 를 못 찾았다"
    ml_at = _find(_block(spec, sel_at), "matchLabels")
    assert ml_at is not None, "spec.selector.matchLabels 를 못 찾았다"
    labels = _block(_block(spec, sel_at), ml_at)
    return {line.split(":", 1)[0].strip(): _value(line) for line in labels}


def _container_names(doc: "list[str]") -> "list[str]":
    # initContainers 는 'containers:' 로 시작하지 않으므로 걸리지 않는다. volumes 는
    # containers 와 같은 깊이라 _block 이 거기서 멈춘다.
    at = _find(doc, "containers")
    assert at is not None, "pod template 의 containers 를 못 찾았다"
    body = _block(doc, at)
    assert body, "containers 가 비어 있다"
    item_indent = min(_indent(line) for line in body)
    return [_value(line.strip()[2:]) for line in body
            if _indent(line) == item_indent and line.strip().startswith("- name:")]


@pytest.mark.parametrize("component", ROLLOUT_ORDER)
def test_components_match_the_deployed_manifests(component):
    spec = COMPONENTS[component]
    path = MANIFESTS[component]
    assert path.is_file(), f"{path} 가 없다"

    doc = _workload_doc(path, spec["kind"], spec["workload"])
    assert doc is not None, (
        f"{path.name} 에 kind={spec['kind']} name={spec['workload']} 문서가 없다 -- "
        f"COMPONENTS['{component}'] 의 kind/workload 가 매니페스트와 어긋났다")

    names = _container_names(doc)
    assert spec["container"] in names, (
        f"{path.name} 의 컨테이너는 {names} 인데 COMPONENTS['{component}']['container'] 는 "
        f"'{spec['container']}' 다. strategic merge patch 는 이 어긋남에 실패하지 않고 "
        f"엉터리 컨테이너를 하나 더 붙인다 -- 워크로드가 오염된다")

    key, _, value = spec["selector"].partition("=")
    assert _match_labels(doc) == {key: value}, (
        f"COMPONENTS['{component}']['selector'] 가 {path.name} 의 "
        f"spec.selector.matchLabels 와 다르다 -- 타임아웃 진단(pod_briefs)이 "
        f"엉뚱한 파드를 보거나 아무것도 못 본다")


def test_every_rollout_component_has_a_manifest():
    # ROLLOUT_ORDER 에만 컴포넌트를 더하고 매니페스트 매핑을 빠뜨리면 위 파라미터라이즈가
    # KeyError 로 죽는 대신 여기서 명시적으로 걸린다.
    assert set(MANIFESTS) == set(ROLLOUT_ORDER) == set(COMPONENTS)
