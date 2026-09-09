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
from dms.manifest_tags import (container_image, container_names,
                               init_container_image, init_container_names,
                               match_labels, workload_doc)
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


@pytest.mark.parametrize("component", ROLLOUT_ORDER)
def test_init_container_declaration_matches_the_manifests(component):
    # 슬라이스 16의 롤아웃 패치는 COMPONENTS['init_container'] 가 있을 때만
    # initContainers 절을 붙인다. 그 표와 매니페스트가 어긋나면 위 컨테이너 이름
    # 어긋남과 **똑같이 조용한** 사고가 난다 -- patchMergeKey(name)에 없는 이름을 주면
    # strategic merge 는 실패하지 않고 {'name': ..., 'image': ...} 만 가진 컨테이너를
    # 새로 만든다. dms-agent 에 그게 생기면 전 노드의 에이전트 파드가 기동에 실패한다.
    #
    # 양방향으로 건다: 선언했으면 매니페스트에 있어야 하고, 선언하지 않았으면
    # 매니페스트에도 없어야 한다. 한쪽만 걸면 반대 방향 드리프트를 놓친다.
    spec = COMPONENTS[component]
    path = MANIFESTS[component]
    doc = workload_doc(path, spec["kind"], spec["workload"])
    assert doc is not None, f"{path.name} 에 워크로드 문서가 없다"

    names = init_container_names(doc)
    declared = spec.get("init_container")

    if declared is None:
        assert names == [], (
            f"COMPONENTS['{component}'] 에는 init_container 가 없는데 {path.name} 에는 "
            f"initContainers {names} 가 있다. 이 워크로드의 initContainer 는 롤아웃 때 "
            f"이미지가 갱신되지 않아 새 파드가 구 이미지로 그것을 실행한다 -- 표에 "
            f"추가하거나 매니페스트에서 빼라")
    else:
        assert declared in names, (
            f"COMPONENTS['{component}']['init_container'] 는 '{declared}' 인데 "
            f"{path.name} 의 initContainers 는 {names} 다. 롤아웃 패치가 이 이름으로 "
            f"strategic merge 를 걸면 병합이 아니라 없던 컨테이너가 새로 생긴다 -- "
            f"워크로드가 오염되고 파드가 영영 Ready 가 안 된다")

        # 그리고 두 이미지는 같은 태그여야 한다. 이 어긋남에는 **어떤 감시 장치도
        # 없다**: 드리프트 배지는 container_image(본 컨테이너)와 라이브
        # observe().images 만 비교하고, 라이브 쪽(rollout_status 의 _images)도
        # spec.template.spec.containers 만 읽는다. 그래서 매니페스트를 손으로 고칠 때
        # containers[api] 만 새 태그로 올리고 initContainers[migrate] 를 구 태그로
        # 두면 live == manifest 라 배지가 뜨지 않고, 그 뒤 뜨는 모든 파드가 구
        # 이미지로 dms migrate 를 돌린 다음 신 앱을 구식 스키마 위에 띄운다 --
        # 슬라이스 14·15에서 실제로 났던 실패다. 그 침묵을 여기서 메운다.
        init_image = init_container_image(doc, declared)
        main_image = container_image(doc, spec["container"])
        assert main_image is not None, (
            f"{path.name} 의 컨테이너 '{spec['container']}' 에서 image 를 못 읽었다 -- "
            f"파서나 매니페스트 모양이 바뀌었다")
        assert init_image == main_image, (
            f"{path.name} 의 initContainer '{declared}' 이미지는 {init_image!r} 인데 "
            f"본 컨테이너 '{spec['container']}' 는 {main_image!r} 다. 드리프트 배지는 "
            f"본 컨테이너만 비교하므로 이 어긋남을 절대 못 잡는다 -- 새 파드가 구 "
            f"이미지로 migrate 한 뒤 신 앱을 구식 스키마 위에 띄운다")


def test_init_container_names_does_not_leak_into_container_names():
    # 두 헬퍼의 분리 자체가 계약이다. container_names 는 반드시 **본 컨테이너만**
    # 반환해야 한다 -- Task 1~3 의 드리프트 배지가 그 반환값으로 라이브 이미지와
    # 비교하므로, initContainer 가 새면 엉뚱한 이미지를 비교해 잘못된 배지가 뜬다.
    path = MANIFESTS["dms-api"]
    doc = workload_doc(path, "Deployment", "dms-api")
    assert container_names(doc) == ["api"]
    assert init_container_names(doc) == ["migrate"]


def test_every_rollout_component_has_a_manifest():
    # ROLLOUT_ORDER 에만 컴포넌트를 더하고 매니페스트 매핑을 빠뜨리면 위 파라미터라이즈가
    # KeyError 로 죽는 대신 여기서 명시적으로 걸린다.
    assert set(MANIFESTS) == set(ROLLOUT_ORDER) == set(COMPONENTS)


# --- 제어면 root 전환(2026-09-09) 계약 ------------------------------------------
# WHY: 운영 아티팩트 base 는 root:root 라 65532 로는 쓰기 왕복(3홉 검증)이 항상
# 실패했다. api/controller 를 uid 0 으로 돌리되 capabilities 는 전부 버리고 이미지
# fs 는 읽기 전용으로 둔다. 이 결정은 매니페스트 5줄이 전부라 계약 테스트가 없으면
# "강화" 한답시고 runAsNonRoot/65532 를 넣거나(운영 base 즉시 회귀) 파드 수준으로
# 옮겨 migrate 까지 root 로 만드는 변경이 조용히 통과한다. 파일시스템 권한이 2차
# 방어가 아니게 되므로(docs/ARCHITECTURE.md 「root 제어면」) 이 모양 자체가 보안
# 불변식의 일부다.
from dms.manifest_tags import (container_security_context, documents,  # noqa: E402
                               init_container_security_context,
                               mentions_security_context, pod_security_context)

ROOT_CONTROL_PLANE = {"dms-api": "api", "dms-controller": "controller"}
_EXPECTED_ROOT_SC = {"runAsUser": "0", "runAsGroup": "0",
                     "allowPrivilegeEscalation": "false",
                     "readOnlyRootFilesystem": "true",
                     "capabilities": {"drop": ["ALL"]}}   # 흐름/블록 시퀀스 모두 list 로 정규화


@pytest.mark.parametrize("component", sorted(ROOT_CONTROL_PLANE))
def test_control_plane_containers_run_as_root_without_capabilities(component):
    spec = COMPONENTS[component]
    doc = workload_doc(MANIFESTS[component], spec["kind"], spec["workload"])
    sc = container_security_context(doc, ROOT_CONTROL_PLANE[component])
    assert sc == _EXPECTED_ROOT_SC, (
        f"{MANIFESTS[component].name} 의 컨테이너 '{ROOT_CONTROL_PLANE[component]}' "
        f"securityContext 가 {sc} 다 -- root(uid 0)·cap drop ALL·readOnlyRootFilesystem "
        f"이 계약이다(운영 base 는 root:root; 이미지 fs 보호는 이 플래그뿐)")


@pytest.mark.parametrize("component", sorted(ROOT_CONTROL_PLANE))
def test_migrate_init_container_and_pod_level_stay_non_root(component):
    # 파드 수준으로 두면 migrate initContainer(DB 전용)까지 root 가 된다.
    spec = COMPONENTS[component]
    doc = workload_doc(MANIFESTS[component], spec["kind"], spec["workload"])
    sc = init_container_security_context(doc, "migrate")
    # 의미 단언: 없거나(이미지 USER 65532 상속), 있어도 비root 여야 한다 -- 비root
    # 강화(runAsNonRoot/readOnlyRootFilesystem)를 막는 테스트가 되면 안 된다.
    assert sc is None or (sc.get("runAsUser") in (None, "65532")
                          and sc.get("runAsNonRoot") != "false"), (
        f"{MANIFESTS[component].name} 의 migrate initContainer securityContext 가 {sc} "
        f"-- migrate 는 이미지 USER(65532) 를 유지해야 한다(root 금지)")
    assert pod_security_context(doc) is None, (
        f"{MANIFESTS[component].name} 에 파드 수준 securityContext 가 있다 -- root 는 "
        f"컨테이너 수준에만 둔다(migrate 가 상속받지 않도록)")


def test_one_shot_migrate_job_has_no_security_context():
    path = REPO_ROOT / "deploy" / "k8s" / "30-migrate-job.yaml"
    assert path.is_file()
    assert not mentions_security_context(path), (
        "30-migrate-job.yaml 에 securityContext 가 생겼다 -- migrate 는 DB 전용이라 "
        "이미지 USER(65532) 그대로 돈다")


def test_agent_daemonset_keeps_root_at_container_level():
    doc = workload_doc(MANIFESTS["dms-agent"], "DaemonSet", "dms-agent")
    sc = container_security_context(doc, "agent")
    assert sc is not None and sc.get("runAsUser") == "0", sc


def test_image_user_stays_non_root_so_root_is_a_manifest_decision_only():
    # Dockerfile 을 root 로 바꾸면 30-migrate-job·디버그 파드까지 root 가 되고 어떤
    # 매니페스트 계약 테스트도 그것을 잡지 못한다 -- root 는 매니페스트만이 진실.
    dockerfile = (REPO_ROOT / "deploy" / "docker" / "Dockerfile.dms").read_text()
    assert "\nUSER 65532:65532\n" in dockerfile


def test_overlays_do_not_patch_control_plane_deployments():
    # 이 파일의 테스트는 base 만 파싱한다 -- 오버레이가 Deployment 를 패치해
    # securityContext 를 바꾸면 여기서는 안 보인다. 그 우회를 별도로 막는다.
    overlays = REPO_ROOT / "deploy" / "overlays"
    offenders = []
    # 파일명이 patch-* 가 아니어도(예: sc.yaml) 오버레이 디렉터리의 모든 YAML 문서를
    # 본다. kustomization.yaml 은 inline patch(`patch: |`)·json6902 `target:` 이
    # 별도 파일 없이 Deployment 를 겨냥할 수 있어 원문에서 'kind: Deployment' 를 찾는다.
    for path in sorted(overlays.glob("*/*.yaml")):
        if path.name == "kustomization.yaml":
            if "kind: Deployment" in path.read_text():
                offenders.append(str(path.relative_to(REPO_ROOT)))
            continue
        for doc in documents(path):
            kinds = [line for line in doc if line.strip().startswith("kind:")
                     and line.split(":", 1)[1].strip() == "Deployment"]
            if kinds:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], (
        f"오버레이가 Deployment 를 패치한다: {offenders} -- dms-api/dms-controller 의 "
        f"securityContext 는 base 가 유일한 진실이어야 한다")
