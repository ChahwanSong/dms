#!/usr/bin/env bash
#
# Build the DMS container images (mpifileutils/dms/agent) and push them to an
# insecure (http) registry. Run it on the build (internet) node -- the node with
# podman and internet access for the mpifileutils/kubectl fetches (testbed:
# pkg-01, 10.10.10.30; prod: the internet node of design v6 7-5, first bootstrap).
# Build context is always the repo root, regardless of caller's cwd.
#
#   REGISTRY=pkg-01:5000 TAG=dev ./deploy/docker/build-and-push.sh          # testbed example
#   REGISTRY=<IP>:<PORT> TAG=<values.env DMS_TAG> ./deploy/docker/build-and-push.sh
#   (TAG = values.env DMS_TAG = DMS_AGENT_TAG = MFU_TAG; never edit the base manifests' image: -- they are placeholders)
#
# Env knobs:
#   REGISTRY   image registry/prefix           (default: pkg-01:5000)
#   TAG        image tag                       (default: dev)
#   IMAGES     which to build, space-separated (default: "mpifileutils dms agent")
#   PUSH       "1" to podman push each image    (default: 1)
#   NO_CACHE   "1" to add --no-cache            (default: 0)
#
# The "agent" image is built FROM the already-built dms + mpifileutils images
# (no network of its own -- see Dockerfile.agent), so mpifileutils and dms
# must be built (in this same run, or a prior one under the same tag) before
# agent.
set -euo pipefail

REGISTRY="${REGISTRY:-pkg-01:5000}"
TAG="${TAG:-dev}"
IMAGES="${IMAGES:-mpifileutils dms agent}"
PUSH="${PUSH:-1}"
NO_CACHE="${NO_CACHE:-0}"

command -v podman >/dev/null 2>&1 \
  || { echo "!! podman not found -- run this on the build (internet) node (testbed: pkg-01)" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

MFU="$REGISTRY/dms-mpifileutils:$TAG"
DMS="$REGISTRY/dms:$TAG"
AGENT="$REGISTRY/dms-agent:$TAG"

common=()
[[ "$NO_CACHE" == "1" ]] && common+=(--no-cache)

push() {
  [[ "$PUSH" == "1" ]] || return 0
  echo "==> push $1 (insecure registry, --tls-verify=false)"
  podman push --tls-verify=false "$1"
}

for img in $IMAGES; do
  echo
  echo "==================== build: $img ===================="
  case "$img" in
    mpifileutils)
      podman build "${common[@]}" -f deploy/docker/Dockerfile.mpifileutils -t "$MFU" .
      push "$MFU"
      ;;
    dms)
      podman build "${common[@]}" -f deploy/docker/Dockerfile.dms -t "$DMS" .
      push "$DMS"
      ;;
    agent)
      podman build "${common[@]}" -f deploy/docker/Dockerfile.agent \
        --build-arg "DMS_IMAGE=$DMS" --build-arg "MFU_IMAGE=$MFU" -t "$AGENT" .
      push "$AGENT"
      ;;
    *)
      echo "unknown image '$img' (want: mpifileutils dms agent)" >&2
      exit 2
      ;;
  esac
done

echo
echo ">> done: ${IMAGES}  (registry=$REGISTRY tag=$TAG push=$PUSH)"
# 2026-11-03 ⑦ 리뷰 1: 예전 안내는 base 매니페스트(deploy/k8s/*)의 image:·DMS_JOB_IMAGE 를 고치라고 했다 — base 는 사이트 중립 자리표시자라
# 고치면 오버레이 치환이 조용히 빠진다. 태그는 오버레이(values.env 의 DMS_TAG·DMS_AGENT_TAG·MFU_TAG / 테스트베드 newTag)가 넣는다.
echo ">> tag: values.env 의 DMS_TAG·DMS_AGENT_TAG·MFU_TAG 를 '$TAG' 와 같게 둔다(테스트베드는 오버레이 newTag)."
echo "   base 매니페스트(deploy/k8s/*)는 자리표시자다 -- 고치지 않는다. 설치기면 이어서: sh deploy/install.sh --context <ctx> --site <site> --stage S5"
