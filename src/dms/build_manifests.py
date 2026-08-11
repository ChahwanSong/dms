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
  ref="$DMS_BUILD_REGISTRY/$img:$DMS_BUILD_TAG"
  echo "=== building $ref ==="
  case "$img" in
    dms-mpifileutils)
      buildah bud -f deploy/docker/Dockerfile.mpifileutils -t "$ref" . ;;
    dms)
      buildah bud -f deploy/docker/Dockerfile.dms -t "$ref" . ;;
    dms-agent)
      # dms-agent는 dms/dms-mpifileutils를 FROM한다(Dockerfile.agent). --build-arg
      # 없이 buildah를 돌리면 Dockerfile의 ARG 기본값(:dev, 손으로 만든 옛 이미지)이
      # 조용히 쓰여 엉뚱한 베이스에서 "성공"한다 -- 이 빌드가 push할 태그로 명시
      # 고정한다. 같은 빌드 안에 앞의 둘이 있거나 그 태그가 이미 레지스트리에
      # 있어야 하고, 없으면 buildah가 pull에 실패해 시끄럽게 죽는다(의도된 동작).
      buildah bud -f deploy/docker/Dockerfile.agent \
        --build-arg "DMS_IMAGE=$DMS_BUILD_REGISTRY/dms:$DMS_BUILD_TAG" \
        --build-arg "MFU_IMAGE=$DMS_BUILD_REGISTRY/dms-mpifileutils:$DMS_BUILD_TAG" \
        -t "$ref" . ;;
    *) echo "DMS_BUILD_REASON=unknown_image:$img"; exit 1 ;;
  esac
  buildah push --tls-verify=false "$ref"
  echo "=== pushed $ref ==="
done
echo DMS_BUILD_OK
"""

# §2.4: buildah 저장소 emptyDir sizeLimit(GiB). 실측 여유(dms-w1 fs 21.78GB -
# eviction 임계 15% ≈ 6.07GB → 15.7GB)보다 작아야, 레이어 폭주 시 노드 압박
# eviction(같은 노드 파드 전체가 축출 후보)이 아니라 sizeLimit 축출(빌드 파드만)이
# 먼저 온다. 프리플라이트 프로브의 디스크 공식(§2.5)도 이 상수를 공유한다 --
# 한쪽만 바꾸면 "프리플라이트는 통과했는데 빌드가 노드를 위협"하는 갈라짐이 생긴다.
# 10Gi 는 현 노드 여유 공식을 통과하는 최대 봉투이며 아직 미측정치다 -- 실증(§6-2)
# 이 du 로 실제 피크를 재서 재보정한다.
BUILD_SIZELIMIT_GIB = 10
# 프리플라이트 디스크 공식의 여유 마진(GiB) -- 빌드 중 같은 노드 다른 파드의
# 로그·쓰기층 몫. eph limits = sizeLimit + 마진(12Gi)으로도 쓰인다.
BUILD_DISK_MARGIN_GIB = 2


def build_build_pod(*, build_id, repo_url, git_ref, images, node, namespace,
                    registry, builder_image, timeout_seconds) -> dict:
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
            "activeDeadlineSeconds": timeout_seconds,
            "nodeSelector": {"kubernetes.io/hostname": node},
            # §2.3: 값 10 < dms-low 50 -- kubelet 축출 랭킹 ②priority 에서 빌드가
            # 어떤 데이터 잡보다 먼저 죽는다. 미적용 클러스터에서는 admission
            # 거절이므로 05-volcano-queue-priorityclass.yaml 을 먼저 apply 할 것.
            "priorityClassName": "dms-build",
            "containers": [{
                "name": "build", "image": builder_image,
                "command": ["sh", "-c", _SCRIPT],
                "env": [{"name": k, "value": v} for k, v in env.items()],
                "securityContext": {"privileged": True},
                # §2.2 실측 역산 봉투 -- requests 로 스케줄러 Fit(노드 여유 검사)을
                # 사고, limits 가 노드를 지킨다:
                # - cpu 1000m: 최혼잡 워커(w2 425m)에서도 675m ≤ 1800m 라 스케줄이
                #   막히지 않고, 빌드가 날뛰어도 잡+제어면 몫 800m 이 남는다.
                # - memory requests 128Mi 를 일부러 작게: 실압박에서 빌드가 항상
                #   "requests 초과" 축출 그룹에 들어 dms-build(10)가 방향을 가른다.
                # - memory limit 1Gi: 노드 eviction 전에 빌드가 먼저, 혼자
                #   OOM-kill 되는 의도된 1차 방어(M8 재현 방지). npm(vite) 빌드가
                #   1Gi 안에서 도는지는 실증(§6-2)이 확정한다.
                # - eph limits 12Gi ≥ sizeLimit 10Gi: limits 는 emptyDir 포함
                #   파드 단위 집행 -- sizeLimit 이 레이어 상한, limits 가 로그·
                #   쓰기층 오버플로 캐치다(§2.4 3중 방어의 ②③).
                "resources": {
                    "requests": {"cpu": "250m", "memory": "128Mi",
                                 "ephemeral-storage": f"{BUILD_SIZELIMIT_GIB}Gi"},
                    "limits": {"cpu": "1000m", "memory": "1Gi",
                               "ephemeral-storage":
                                   f"{BUILD_SIZELIMIT_GIB + BUILD_DISK_MARGIN_GIB}Gi"},
                },
                "volumeMounts": [{"name": "containers", "mountPath": "/var/lib/containers"}],
            }],
            # emptyDir 이므로 kubelet ephemeral-storage 회계 안이다(§1-2). 수치
            # 근거는 위 BUILD_SIZELIMIT_GIB 주석.
            "volumes": [{"name": "containers",
                        "emptyDir": {"sizeLimit": f"{BUILD_SIZELIMIT_GIB}Gi"}}],
        },
    }
