"""빌드 파드 매니페스트. 순수 함수 -- k8s 클라이언트에 접근하지 않는다."""
from .repositories.builds import BUILD_IMAGES, build_pod_name, build_tag

# 레지스트리가 평문 HTTP 라 pull 도 insecure 로 열어야 한다: dms-agent 이미지가
# `FROM pkg-01:5000/...` 를 하기 때문이다. push 만 --tls-verify=false 로는 부족하다.
_SCRIPT = r"""
set -eu
mkdir -p /etc/containers/registries.conf.d
printf '[[registry]]\nlocation = "%s"\ninsecure = true\n' "$DMS_BUILD_REGISTRY" \
  > /etc/containers/registries.conf.d/dms-insecure.conf

git clone --depth 1 --branch "$DMS_BUILD_REF" "$DMS_BUILD_REPO" /src
cd /src
echo "DMS_COMMIT_SHA=$(git rev-parse HEAD)"

for img in $DMS_BUILD_IMAGES; do
  case "$img" in
    dms-mpifileutils) dockerfile=deploy/docker/Dockerfile.mpifileutils ;;
    dms)              dockerfile=deploy/docker/Dockerfile.dms ;;
    dms-agent)        dockerfile=deploy/docker/Dockerfile.agent ;;
    *) echo "DMS_BUILD_REASON=unknown_image:$img"; exit 1 ;;
  esac
  ref="$DMS_BUILD_REGISTRY/$img:$DMS_BUILD_TAG"
  echo "=== building $ref ==="
  buildah bud -f "$dockerfile" -t "$ref" .
  buildah push --tls-verify=false "$ref"
  echo "=== pushed $ref ==="
done
echo DMS_BUILD_OK
"""


def build_build_pod(*, build_id, repo_url, git_ref, images, node, namespace,
                    registry, builder_image) -> dict:
    ordered = [i for i in BUILD_IMAGES if i in set(images)]
    env = {
        "DMS_BUILD_REPO": repo_url,
        "DMS_BUILD_REF": git_ref,
        "DMS_BUILD_TAG": build_tag(build_id),
        "DMS_BUILD_REGISTRY": registry,
        "DMS_BUILD_IMAGES": " ".join(ordered),
    }
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": build_pod_name(build_id), "namespace": namespace,
                     "labels": {"dms.io/build-id": build_id,
                                "dms.io/phase": "build"}},
        "spec": {
            "restartPolicy": "Never",
            "nodeSelector": {"kubernetes.io/hostname": node},
            "containers": [{
                "name": "build", "image": builder_image,
                "command": ["sh", "-c", _SCRIPT],
                "env": [{"name": k, "value": v} for k, v in env.items()],
                "securityContext": {"privileged": True},
                "volumeMounts": [{"name": "containers", "mountPath": "/var/lib/containers"}],
            }],
            "volumes": [{"name": "containers", "emptyDir": {}}],
        },
    }
