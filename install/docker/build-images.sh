#!/usr/bin/env bash
#
# Build the DMS + Portal container images from the repo root — works BOTH with
# direct internet and behind a proxy-only network, from a SINGLE set of Dockerfiles
# (no proxy-variant Dockerfiles to drift out of sync).
#
#   Direct internet:
#     REGISTRY=registry.example.internal TAG=v1 ./install/docker/build-images.sh
#
#   Proxy-only network (proxy reachable at, e.g., 127.0.0.1:7227):
#     REGISTRY=registry.example.internal TAG=v1 \
#       PROXY=http://127.0.0.1:7227 ./install/docker/build-images.sh
#
# How the proxy mode works (and why it's safe at runtime):
#   * The proxy is injected only as Docker's PREDEFINED proxy build-args
#     (http_proxy/https_proxy/no_proxy, + npm_config_proxy for the SPA build).
#     These are build-time env for the RUN steps (apt/curl/git/pip/npm all honor
#     them) but are NOT persisted into the final image and NOT shown in
#     `docker history` — so the running containers carry NO proxy env and never
#     route their web server / node-to-node calls through the proxy.
#   * `--network=host` is added so a HOST-LOCAL proxy (127.0.0.1:PORT) is reachable
#     from inside the build — inside a build container 127.0.0.1 is the container's
#     own loopback, not the host, so without host networking the proxy is unreachable.
#
# Env knobs:
#   REGISTRY   image registry/prefix          (default: registry.example.internal)
#   TAG        image tag                       (default: dev)
#   PROXY      proxy URL, empty = direct       (e.g. http://127.0.0.1:7227)
#   NO_PROXY   comma hosts to bypass proxy     (default: localhost,127.0.0.1,::1,<registry host>)
#   IMAGES     which to build, space-separated (default: "mpifileutils dms agent portal")
#   PUSH       "1" to docker push each image   (default: 0)
#   NO_CACHE   "1" to add --no-cache           (default: 0)
#
set -euo pipefail

REGISTRY="${REGISTRY:-registry.example.internal}"
TAG="${TAG:-dev}"
PROXY="${PROXY:-}"
NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1,${REGISTRY%%:*}}"
IMAGES="${IMAGES:-mpifileutils dms agent portal}"
PUSH="${PUSH:-0}"
NO_CACHE="${NO_CACHE:-0}"

# run from the repo root (build context) regardless of where the script is called from
cd "$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"

MFU="$REGISTRY/dms-mpifileutils:$TAG"
DMS="$REGISTRY/dms:$TAG"
AGENT="$REGISTRY/dms-agent:$TAG"
PORTAL="$REGISTRY/dms-portal:$TAG"

# common flags (predefined proxy args never warn as "unused"); npm_config_* is only
# passed to the Portal build so the other Dockerfiles don't warn about an unused arg.
common=()
npm_only=()
[[ "$NO_CACHE" == "1" ]] && common+=(--no-cache)
if [[ -n "$PROXY" ]]; then
  echo ">> proxy mode: $PROXY  (host network; build-time only, no runtime leak)"
  common+=(
    --network=host
    --build-arg "http_proxy=$PROXY"   --build-arg "https_proxy=$PROXY"
    --build-arg "HTTP_PROXY=$PROXY"   --build-arg "HTTPS_PROXY=$PROXY"
    --build-arg "no_proxy=$NO_PROXY"  --build-arg "NO_PROXY=$NO_PROXY"
  )
  npm_only+=(--build-arg "npm_config_proxy=$PROXY" --build-arg "npm_config_https_proxy=$PROXY")
else
  echo ">> direct mode (no proxy)"
fi

push() { [[ "$PUSH" == "1" ]] && { echo "==> push $1"; docker push "$1"; }; return 0; }

for img in $IMAGES; do
  echo; echo "==================== build: $img ===================="
  case "$img" in
    mpifileutils)
      docker build "${common[@]}" -f install/docker/Dockerfile.mpifileutils -t "$MFU" .
      push "$MFU" ;;
    dms)
      docker build "${common[@]}" -f install/docker/Dockerfile -t "$DMS" .
      push "$DMS" ;;
    agent)  # no network of its own — FROM the already-built dms + mfu images, then COPY
      docker build "${common[@]}" -f install/docker/Dockerfile.agent \
        --build-arg "DMS_IMAGE=$DMS" --build-arg "MFU_IMAGE=$MFU" -t "$AGENT" .
      push "$AGENT" ;;
    portal)
      docker build "${common[@]}" "${npm_only[@]}" -f src/portal/deploy/Dockerfile -t "$PORTAL" .
      push "$PORTAL" ;;
    *) echo "unknown image '$img' (want: mpifileutils dms agent portal)" >&2; exit 2 ;;
  esac
done

echo; echo ">> done: ${IMAGES}  (tag=$TAG${PROXY:+, via proxy})"
