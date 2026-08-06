from dms.build_manifests import build_build_pod

BID = "0123456789abcdef0123456789abcdef"


def _pod(images=("dms",)):
    return build_build_pod(build_id=BID, repo_url="https://example/r.git",
                           git_ref="main", images=list(images), node="dms-w1",
                           namespace="dms", registry="pkg-01:5000",
                           builder_image="quay.io/buildah/stable:latest")


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
