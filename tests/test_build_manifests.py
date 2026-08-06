import re

from dms.build_manifests import build_build_pod

BID = "0123456789abcdef0123456789abcdef"


def _pod(images=("dms",), timeout_seconds=7200):
    return build_build_pod(build_id=BID, repo_url="https://example/r.git",
                           git_ref="main", images=list(images), node="dms-w1",
                           namespace="dms", registry="pkg-01:5000",
                           builder_image="quay.io/buildah/stable:latest",
                           timeout_seconds=timeout_seconds)


def test_pod_identity_and_placement():
    pod = _pod()
    assert pod["kind"] == "Pod" and pod["apiVersion"] == "v1"
    assert pod["metadata"]["name"] == "dms-build-0123456789ab"
    assert pod["metadata"]["namespace"] == "dms"
    assert pod["metadata"]["labels"]["dms.io/build-id"] == BID
    assert pod["metadata"]["labels"]["dms.io/phase"] == "build"
    assert pod["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "dms-w1"}
    assert pod["spec"]["restartPolicy"] == "Never"


def test_container_is_privileged_builder_with_container_storage_volume():
    c = _pod()["spec"]["containers"][0]
    assert c["image"] == "quay.io/buildah/stable:latest"
    assert c["securityContext"]["privileged"] is True
    assert any(m["mountPath"] == "/var/lib/containers" for m in c["volumeMounts"])


def test_values_travel_as_env_not_interpolated_into_the_script():
    c = _pod()["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["DMS_BUILD_REPO"] == "https://example/r.git"
    assert env["DMS_BUILD_REF"] == "main"
    assert env["DMS_BUILD_TAG"] == "b01234567"
    assert env["DMS_BUILD_REGISTRY"] == "pkg-01:5000"
    script = c["command"][2]
    # 값이 스크립트 본문에 박혀 있으면 주입 표면이 된다
    assert "https://example/r.git" not in script
    assert "b01234567" not in script


def test_images_are_forced_into_dependency_order():
    c = _pod(images=["dms-agent", "dms"])["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["DMS_BUILD_IMAGES"] == "dms dms-agent"


def test_script_pushes_insecurely_and_emits_the_commit_marker():
    script = _pod()["spec"]["containers"][0]["command"][2]
    assert "--tls-verify=false" in script     # 레지스트리가 평문 HTTP 다
    assert "DMS_COMMIT_SHA=" in script        # 감시 루프가 찾는 마커
    assert "set -eu" in script                # 중간 실패가 성공으로 보이면 안 된다


def test_containers_volume_has_a_size_limit():
    # M8: buildah 3종 빌드(특히 mpifileutils 소스 컴파일)는 수 GB를 쓴다. sizeLimit
    # 없는 emptyDir는 노드 ephemeral-storage 압박 시 kubelet이 같은 노드의
    # api/controller/agent 파드까지 축출할 수 있다.
    volume = _pod()["spec"]["volumes"][0]
    assert volume["name"] == "containers"
    assert volume["emptyDir"]["sizeLimit"]


def test_pod_has_active_deadline_seconds():
    # C2(a): 파드가 스케줄된 뒤엔 kubelet이 activeDeadlineSeconds로 죽인다 --
    # 이게 없으면 buildah가 네트워크에서 멈춰도 영원히 Running으로 남는다.
    assert _pod(timeout_seconds=7200)["spec"]["activeDeadlineSeconds"] == 7200
    assert _pod(timeout_seconds=42)["spec"]["activeDeadlineSeconds"] == 42


def test_dms_agent_case_pins_from_images_to_the_same_build_tag():
    # C1: dms-agent는 dms/dms-mpifileutils를 FROM한다(Dockerfile.agent). --build-arg
    # 없이는 buildah가 Dockerfile의 기본값(:dev, 손으로 만든 옛 이미지)을 조용히
    # 집어 엉뚱한 베이스에서 "성공"한다. 같은 빌드가 쓸 태그($DMS_BUILD_TAG)로
    # 명시적으로 고정해야 한다 -- _SCRIPT는 이미지 선택과 무관하게 항상 3개
    # case 분기를 전부 담은 하나의 상수이므로(런타임에 DMS_BUILD_IMAGES로 순회),
    # 여기서는 dms-agent case 분기 자체의 구조를 검사한다.
    script = _pod()["spec"]["containers"][0]["command"][2]
    block = re.search(r"dms-agent\)(.*?);;", script, re.S).group(1)
    assert '--build-arg "DMS_IMAGE=$DMS_BUILD_REGISTRY/dms:$DMS_BUILD_TAG"' in block
    assert '--build-arg "MFU_IMAGE=$DMS_BUILD_REGISTRY/dms-mpifileutils:$DMS_BUILD_TAG"' in block


def test_dms_solo_build_case_has_no_build_arg():
    # dms/dms-mpifileutils는 아무것도 FROM하지 않으므로 build-arg가 붙으면 안 된다.
    script = _pod()["spec"]["containers"][0]["command"][2]
    dms_block = re.search(r"dms\)(.*?);;", script, re.S).group(1)
    mfu_block = re.search(r"dms-mpifileutils\)(.*?);;", script, re.S).group(1)
    assert "--build-arg" not in dms_block
    assert "--build-arg" not in mfu_block
