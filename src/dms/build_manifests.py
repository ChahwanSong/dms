"""빌드 파드 매니페스트. 순수 함수 -- k8s 클라이언트에 접근하지 않는다."""
from urllib.parse import urlsplit

from .repositories.builds import (BUILD_IMAGES, build_pod_name,
                                  build_probe_pod_name, build_tag)

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


def repo_host(repo_url: str) -> str | None:
    """repo_url 에서 egress 프로브 대상 호스트를 뽑는다. 파싱 불가면 None.

    라우트(제출 시 422 invalid_repo_url)와 프로브 매니페스트가 **같은 함수**를
    쓴다 -- 두 곳이 다르게 파싱하면 "제출은 통과했는데 프로브를 못 만드는" 창이
    생긴다. scp 형(git@host:path)은 urlsplit 이 호스트를 못 뽑아 None 이다 --
    지원 확대가 아니라 명시 거절이 목적이다(테스트베드는 https 만 쓴다)."""
    try:
        return urlsplit(repo_url or "").hostname
    except ValueError:
        # 잘못된 IPv6 브래킷 등 urlsplit 자체가 던지는 경우 -- 파싱 불가와 동치.
        return None


# 프리플라이트 프로브 스크립트(§2.5). 실행 preflight 의 마커 관례를 그대로 따른다
# (execution_manifests._preflight_script: 실패 = DMS_PREFLIGHT_REASON=<code> +
# exit 1, 성공 = DMS_PREFLIGHT_OK) -- 워처가 같은 파서 계열로 읽는다. 대상
# 호스트·수치는 전부 env 로 나른다(빌드 스크립트와 같은 인젝션 회피 원칙: 값이
# 본문에 박히면 repo_url 이 코드가 된다). 이미지는 job_image(python:3.11-slim
# 기반 -- Dockerfile.mpifileutils:81 -- 이라 python3 보장, 워커 캐시 존재, pull 도
# pkg-01 만 필요) -- 프로브 기동 자체가 인터넷과 무관해야 "인터넷만 없는 노드"를
# 정확히 판별한다.
_PROBE_SCRIPT = r"""
import os
import socket
import sys


def reachable(host, port):
    # TCP 연결만 본다(각 5s): 운영 모델의 질문이 "인터넷이 열렸는가"라는 이진
    # 질문이기 때문이다. 선별 개방(예: github 만)이면 여기를 통과하고 npm 에서
    # 죽는다 -- 그건 기존대로 build_failed + 로그의 몫이다(설계 §2.5 정직한 한계).
    try:
        with socket.create_connection((host, port), timeout=5.0):
            return True
    except OSError:
        return False


def fail(reason, detail):
    # 마커보다 detail 을 먼저 찍는다 -- 로그 꼬리 박제(64KB)에서 마커가 잘리는
    # 것보다 detail 이 잘리는 편이 낫다(마커가 없으면 build_preflight_failed 로
    # 접혀 사유가 뭉개진다).
    print(detail)
    print("DMS_PREFLIGHT_REASON=" + reason)
    sys.exit(1)


egress_hosts = os.environ["DMS_PF_EGRESS_HOSTS"].split()
unreachable = [h for h in egress_hosts if not reachable(h, 443)]
if unreachable:
    # 실패 호스트 전부를 로그로 -- "어느 호스트가 막혔나"가 운영자의 첫 질문이다.
    fail("build_node_no_egress", "unreachable_443=" + ",".join(unreachable))

registry = os.environ["DMS_PF_REGISTRY"]
reg_host, _, reg_port = registry.partition(":")
if not reachable(reg_host, int(reg_port or "443")):
    fail("build_registry_unreachable", "unreachable_registry=" + registry)

# 노드 fs 여유 검사: 컨테이너 overlay 의 "/" 는 노드 fs 를 그대로 보고한다
# (nodefs=imagefs 동일 실측). 0.15 는 kubelet evictionHard(imagefs 15%, 2026-08-11
# configz 실측)의 미러 상수다 -- kubelet 설정이 바뀌면 여기도 같이 갱신할 것.
# NEED_BYTES = sizeLimit(10Gi) + 마진(2Gi) -- build_manifests 상수에서 온다.
st = os.statvfs("/")
avail = st.f_bavail * st.f_frsize
total = st.f_blocks * st.f_frsize
need = int(0.15 * total) + int(os.environ["DMS_PF_NEED_BYTES"])
if avail < need:
    fail("build_node_disk_low",
         "avail_bytes=%d need_bytes=%d total_bytes=%d" % (avail, need, total))
print("disk avail_bytes=%d need_bytes=%d" % (avail, need))
print("DMS_PREFLIGHT_OK")
"""

# 고정 egress 대상(§2.5-①): quay.io 는 빌더 이미지(kubelet 이 pull), docker.io
# 베이스 이미지(node:20-bookworm-slim -- Dockerfile.dms:13, python:3.11-slim-bookworm
# -- Dockerfile.dms:24 / Dockerfile.mpifileutils:81, debian:bookworm --
# Dockerfile.mpifileutils:17)는 registry-1.docker.io 에서 온다.
_PROBE_STATIC_HOSTS = ("quay.io", "registry-1.docker.io")


def build_probe_pod(*, build_id, repo_url, node, namespace, registry, job_image,
                    timeout_seconds) -> dict:
    host = repo_host(repo_url)
    if not host:
        # 라우트가 제출 시점에 invalid_repo_url 로 거른다 -- 여기 도달은 검증 전에
        # 만들어진 구형 Pending 행뿐이고, BuildRunner 가 submit_failed 로 접는다.
        raise ValueError(f"cannot parse repo host from {repo_url!r}")
    hosts = [host] + [h for h in _PROBE_STATIC_HOSTS if h != host]
    env = {
        "DMS_PF_EGRESS_HOSTS": " ".join(hosts),
        "DMS_PF_REGISTRY": registry,
        # 빌드 파드 봉투와 같은 상수(§2.4) -- 프리플라이트가 통과한 노드에서
        # sizeLimit 이 반드시 담길 수 있어야 두 방어가 한 공식이 된다.
        "DMS_PF_NEED_BYTES": str((BUILD_SIZELIMIT_GIB + BUILD_DISK_MARGIN_GIB)
                                 * 1024 ** 3),
    }
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": build_probe_pod_name(build_id), "namespace": namespace,
                     "labels": {"dms.io/build-id": build_id,
                                "dms.io/phase": "build-preflight"}},
        "spec": {
            "restartPolicy": "Never",
            # 프로브 자체의 벽시계 상한 -- 워처의 프리플라이트 타임아웃과 같은 값.
            # 단 activeDeadlineSeconds 는 스케줄 후에만 발화하므로(§1-9) 영구
            # Pending 프로브는 워처의 created_at 기반 회수만 잡는다.
            "activeDeadlineSeconds": timeout_seconds,
            "nodeSelector": {"kubernetes.io/hostname": node},
            # 빌드와 같은 클래스(§2.3): 프로브도 데이터 잡보다 먼저 죽고 아무도
            # 선점하지 않는다. 미적용 클러스터에서는 admission 거절 -- 배포 순서 참고.
            "priorityClassName": "dms-build",
            "containers": [{
                "name": "preflight", "image": job_image,
                "command": ["python3", "-c", _PROBE_SCRIPT],
                "env": [{"name": k, "value": v} for k, v in env.items()],
                # 작은 봉투: 소켓 3~4개와 statvfs 뿐이다 -- 프로브가 노드에
                # 유의미한 압박을 만들면 검사가 검사 대상을 오염시킨다.
                "resources": {"requests": {"cpu": "50m", "memory": "32Mi"},
                              "limits": {"cpu": "200m", "memory": "128Mi"}},
            }],
        },
    }


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
