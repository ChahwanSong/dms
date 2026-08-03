#!/usr/bin/env bash
#
# Build the DMS container images (mpifileutils/dms/agent) and push them to the
# testbed's insecure registry. RUN THIS ON pkg-01 (10.10.10.30) -- that's the
# node with podman and internet access for the mpifileutils/kubectl fetches.
# Build context is always the repo root, regardless of caller's cwd.
#
#   REGISTRY=pkg-01:5000 TAG=dev ./deploy/docker/build-and-push.sh
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
  || { echo "!! podman not found -- this script must run on pkg-01" >&2; exit 2; }

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
echo ">> if TAG != dev, update DMS_JOB_IMAGE in deploy/k8s/20-config.yaml and the"
echo "   image: fields in 40-api.yaml / 41-controller.yaml / 30-migrate-job.yaml /"
echo "   50-agent-daemonset.yaml to match."
