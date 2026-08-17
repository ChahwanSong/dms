"""빌드 파드 매니페스트. 순수 함수 -- k8s 클라이언트에 접근하지 않는다."""
from .repositories.builds import BUILD_IMAGES, build_pod_name, build_probe_pod_name

# 로컬 소스 빌드(슬라이스 33): git clone 대신 빌드 노드의 소스 경로(hostPath, ro)에서
# tar 스냅샷을 떠 /src 를 만든다. 스냅샷을 뜨는 이유: 마운트에서 직접 빌드하면 빌드
# 중 개발자가 파일을 고칠 때 컨텍스트가 중간에 갈라진다 -- 시작 시점 한 번의 복사로
# 창을 수 초로 좁힌다(원자적이진 않다 -- 정직한 한계).
#
# tar 제외 목록은 전송량 최적화 + .claude 재귀 방지("워크트리가 저장소 안에 있다")일
# 뿐이고, 이미지 내용의 밀폐성은 저장소의 .dockerignore 가 단일 게이트로 지킨다 --
# 목록을 여기 늘려 두 번째 진실을 만들지 않는다.
#
# 커밋 SHA: 복사본이 아니라 **마운트에서** 읽는다(.git 은 복사하지 않으므로).
# - safe.directory: 소스는 개발자(비 root) 소유인데 파드는 root 라 git 이
#   dubious ownership 으로 거부한다. 이 설정은 보호 구성(global/system)에서만
#   읽히므로 -c 로는 안 되고 --global 로 넣는다(컨테이너 안 HOME 이라 부작용 없음).
# - GIT_OPTIONAL_LOCKS=0: ro 마운트라 index.lock 을 만들 수 없다 -- 선택적 잠금을
#   끄면 status 가 인덱스 갱신 없이 동작한다.
# - 워크트리 경로가 지정된 경우 .git 파일의 gitdir: 절대경로가 마운트 밖을 가리키면
#   rev-parse 가 실패한다 -- unknown 으로 정직하게 접는다(지어내지 않는다, 설계 §4).
# - dirty 판정 실패(예: 권한)와 "깨끗함"을 구분하지 않는 건 의도다: 접미사가 없는
#   SHA 는 "그 커밋일 공산이 크다"이지 증명이 아니다.
#
# 레지스트리가 평문 HTTP 라 pull 도 insecure 로 열어야 한다: dms-agent 이미지가
# `FROM pkg-01:5000/...` 를 하기 때문이다. push 만 --tls-verify=false 로는 부족하다.
_SCRIPT = r"""
set -eu
mkdir -p /etc/containers/registries.conf.d
printf '[[registry]]\nlocation = "%s"\ninsecure = true\n' "$DMS_BUILD_REGISTRY" \
  > /etc/containers/registries.conf.d/dms-insecure.conf

git config --global --add safe.directory '*'
sha=$(GIT_OPTIONAL_LOCKS=0 git -C "$DMS_BUILD_SRC" rev-parse HEAD 2>/dev/null || echo unknown)
if [ "$sha" != unknown ] && \
   [ -n "$(GIT_OPTIONAL_LOCKS=0 git -C "$DMS_BUILD_SRC" status --porcelain 2>/dev/null | head -1)" ]; then
  sha="$sha-dirty"
fi
echo "DMS_COMMIT_SHA=$sha"

mkdir -p /src
(cd "$DMS_BUILD_SRC" && tar -cf - \
  --exclude=./.git --exclude=./.claude --exclude=./legacy \
  --exclude=./.venv --exclude=./frontend/node_modules --exclude=./frontend/dist \
  .) | (cd /src && tar -xf -)
cd /src

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


# 소스 검사를 맨 앞에 둔다(가장 싸고 가장 구체적인 실패). Dockerfile.dms 를
# 센티널로 쓴다: 경로가 "존재하지만 DMS 저장소가 아닌" 오설정(예: 상위 디렉토리를
# 지정)이 여기서 잡힌다 -- isdir 만 보면 buildah 깊숙한 곳에서 no such Dockerfile
# 로 죽어 사유가 뭉개진다. 경로 자체가 노드에 없으면 hostPath 자동 생성(빈 디렉토리,
# 프로브 매니페스트 주석 참고)으로 마운트는 되고 이 검사가 잡는다.
src = os.environ["DMS_PF_SRC"]
if not os.path.isfile(os.path.join(src, "deploy", "docker", "Dockerfile.dms")):
    fail("build_source_unavailable", "src=" + src)

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
# Dockerfile.mpifileutils:17)는 registry-1.docker.io 에서 온다. 소스가 로컬이 된
# 뒤에도 이 둘은 남는다 -- 베이스 이미지·npm/pip 다운로드는 여전히 인터넷이다.
_PROBE_STATIC_HOSTS = ("quay.io", "registry-1.docker.io")


def build_probe_pod(*, build_id, source_path, node, namespace, registry, job_image,
                    timeout_seconds) -> dict:
    env = {
        "DMS_PF_EGRESS_HOSTS": " ".join(_PROBE_STATIC_HOSTS),
        "DMS_PF_REGISTRY": registry,
        "DMS_PF_SRC": source_path,
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
                "volumeMounts": [{"name": "src", "mountPath": source_path,
                                  "readOnly": True}],
            }],
            # 프로브의 hostPath 는 type 을 **비운다**(검사 없음): 경로가 노드에
            # 없으면 kubelet 이 빈 디렉토리를 만들어서라도 파드를 띄운다 -- 오타
            # 경로가 "Dockerfile 없음"으로 명확히 잡히게(build_source_unavailable)
            # 하기 위해서다. type: Directory 면 FailedMount 로 파드가 영영
            # Pending 이라 180s 뒤 build_preflight_timeout 으로 뭉개진다. 대가는
            # 오타 경로에 빈 디렉토리 하나가 남는 것 -- 사유의 선명함이 더 크다.
            "volumes": [{"name": "src", "hostPath": {"path": source_path}}],
        },
    }


def build_build_pod(*, build_id, source_path, tag, images, node, namespace,
                    registry, builder_image, timeout_seconds) -> dict:
    ordered = [i for i in BUILD_IMAGES if i in set(images)]
    # tag 는 호출자(BuildRunner)가 effective_tag() 로 확정해 넘긴다 -- 여기서
    # 재계산하면 "화면의 태그"와 "push 태그"가 갈라지는 두 번째 진실이 생긴다.
    env = {
        "DMS_BUILD_SRC": source_path,
        "DMS_BUILD_TAG": tag,
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
                "volumeMounts": [
                    {"name": "containers", "mountPath": "/var/lib/containers"},
                    # 소스는 **호스트와 같은 절대경로**에 ro 마운트한다: 경로가
                    # 저장소 루트면 그 안의 .git 이 그대로 보이고, 워크트리 경로가
                    # 지정된 경우에도 gitdir: 절대경로 해석이 성립할 여지를 남긴다
                    # (마운트 밖을 가리키면 SHA 가 unknown 으로 접힌다 -- 스크립트
                    # 주석). ro 는 경계다: 특권 파드가 개발자의 작업 트리를 쓰기로
                    # 오염시키는 길을 볼륨 단에서 막는다.
                    {"name": "src", "mountPath": source_path, "readOnly": True},
                ],
            }],
            # emptyDir 이므로 kubelet ephemeral-storage 회계 안이다(§1-2). 수치
            # 근거는 위 BUILD_SIZELIMIT_GIB 주석. src 의 type: Directory 는
            # 프로브와 다른 선택이다 -- 프로브가 존재를 이미 검증했으므로 여기서
            # 없으면 그건 오설정이 아니라 사고(마운트 소실)라 시끄럽게 죽는 게 맞다.
            "volumes": [{"name": "containers",
                         "emptyDir": {"sizeLimit": f"{BUILD_SIZELIMIT_GIB}Gi"}},
                        {"name": "src",
                         "hostPath": {"path": source_path, "type": "Directory"}}],
        },
    }
