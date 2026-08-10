"""I3: COMPONENTS 표가 deploy/k8s 매니페스트와 실제로 일치하는지 대조한다.

기존 test_components_carry_real_container_names 는 문자열 리터럴에 대고 단언할
뿐이라 매니페스트가 바뀌어도 아무것도 빨간불이 되지 않는다. 이 어긋남은 조용하다:
strategic merge patch 에서 containers 의 patchMergeKey 는 name 이라, COMPONENTS
의 컨테이너 이름이 매니페스트와 다르면 patch 가 **실패하지 않고** name/image 만
가진 엉터리 둘째 컨테이너를 추가한다. 결과는 영원히 Ready 가 안 되는 파드,
600초 PDE 대기, 그리고 YAML 재적용 전까지 오염된 워크로드다(설계 §1 표의 함정).

파서는 슬라이스 16에서 src/dms/manifest_tags.py 로 승격됐다(api 가 런타임에도 같은
파서로 동봉 매니페스트를 읽는다 -- 드리프트 배지). 여기서는 승격본을 import 해 쓰되,
추출이 빗나가면 조용히 통과하는 대신 assert 가 빨간불이 되는 성질(match_labels/
container_names)은 그대로다.
"""
from pathlib import Path

import pytest
from dms.manifest_tags import container_names, match_labels, workload_doc
from dms.repositories.releases import COMPONENTS, ROLLOUT_ORDER

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFESTS = {
    "dms-api": REPO_ROOT / "deploy" / "k8s" / "40-api.yaml",
    "dms-controller": REPO_ROOT / "deploy" / "k8s" / "41-controller.yaml",
    "dms-agent": REPO_ROOT / "deploy" / "k8s" / "50-agent-daemonset.yaml",
}


@pytest.mark.parametrize("component", ROLLOUT_ORDER)
def test_components_match_the_deployed_manifests(component):
    spec = COMPONENTS[component]
    path = MANIFESTS[component]
    assert path.is_file(), f"{path} 가 없다"

    doc = workload_doc(path, spec["kind"], spec["workload"])
    assert doc is not None, (
        f"{path.name} 에 kind={spec['kind']} name={spec['workload']} 문서가 없다 -- "
        f"COMPONENTS['{component}'] 의 kind/workload 가 매니페스트와 어긋났다")

    names = container_names(doc)
    assert spec["container"] in names, (
        f"{path.name} 의 컨테이너는 {names} 인데 COMPONENTS['{component}']['container'] 는 "
        f"'{spec['container']}' 다. strategic merge patch 는 이 어긋남에 실패하지 않고 "
        f"엉터리 컨테이너를 하나 더 붙인다 -- 워크로드가 오염된다")

    key, _, value = spec["selector"].partition("=")
    assert match_labels(doc) == {key: value}, (
        f"COMPONENTS['{component}']['selector'] 가 {path.name} 의 "
        f"spec.selector.matchLabels 와 다르다 -- 타임아웃 진단(pod_briefs)이 "
        f"엉뚱한 파드를 보거나 아무것도 못 본다")


def test_every_rollout_component_has_a_manifest():
    # ROLLOUT_ORDER 에만 컴포넌트를 더하고 매니페스트 매핑을 빠뜨리면 위 파라미터라이즈가
    # KeyError 로 죽는 대신 여기서 명시적으로 걸린다.
    assert set(MANIFESTS) == set(ROLLOUT_ORDER) == set(COMPONENTS)
