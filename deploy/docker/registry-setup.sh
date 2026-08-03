#!/usr/bin/env bash
#
# Stand up the testbed's insecure container registry on pkg-01, and configure
# every dms cluster node's CRI-O to trust it as insecure. Two independent
# steps -- run each where it applies (see usage below).
#
#   registry step (run ON pkg-01, 10.10.10.30 -- needs podman):
#     ./deploy/docker/registry-setup.sh registry
#
#   nodes step (run from anywhere with ssh access to the k8s nodes; auto-
#   discovers node names via `kubectl --context dms get nodes` if NODES is
#   unset):
#     ./deploy/docker/registry-setup.sh nodes
#     # or explicit inventory:
#     NODES="w1 w2 w3 w4 w5" SSH_USER=ubuntu ./deploy/docker/registry-setup.sh nodes
#
#   both, in order:
#     ./deploy/docker/registry-setup.sh all
#
# Env knobs:
#   REGISTRY_HOST  hostname the registry is reachable at from cluster nodes
#                  (default: pkg-01)
#   REGISTRY_PORT  registry port                              (default: 5000)
#   REGISTRY_NAME  podman container name for the registry      (default: dms-registry)
#   NODES          space-separated node names for the "nodes" step
#                  (default: discovered via `kubectl --context dms get nodes`)
#   SSH_USER       ssh user to use against each node            (default: current user)
set -euo pipefail

REGISTRY_HOST="${REGISTRY_HOST:-pkg-01}"
REGISTRY_PORT="${REGISTRY_PORT:-5000}"
REGISTRY_NAME="${REGISTRY_NAME:-dms-registry}"
CONF_NAME="99-pkg-01-insecure.conf"

start_registry() {
  command -v podman >/dev/null 2>&1 \
    || { echo "!! podman not found -- run the 'registry' step on pkg-01" >&2; exit 2; }

  if podman container exists "$REGISTRY_NAME" 2>/dev/null; then
    echo ">> $REGISTRY_NAME already exists -- (re)starting"
    podman start "$REGISTRY_NAME" >/dev/null
  else
    echo ">> creating $REGISTRY_NAME on :$REGISTRY_PORT"
    podman volume create dms-registry-data >/dev/null 2>&1 || true
    podman run -d --name "$REGISTRY_NAME" --restart=always \
      -p "${REGISTRY_PORT}:5000" \
      -v dms-registry-data:/var/lib/registry \
      docker.io/library/registry:2
  fi
  echo ">> registry up: http://${REGISTRY_HOST}:${REGISTRY_PORT}/v2/_catalog"
  echo ">> smoke test: curl -s http://localhost:${REGISTRY_PORT}/v2/_catalog"
}

configure_nodes() {
  local nodes="${NODES:-}"
  if [[ -z "$nodes" ]]; then
    command -v kubectl >/dev/null 2>&1 \
      || { echo "!! set NODES=\"node1 node2 ...\" or make kubectl (context dms) available" >&2; exit 2; }
    nodes="$(kubectl --context dms get nodes -o jsonpath='{.items[*].metadata.name}')"
  fi
  [[ -n "$nodes" ]] || { echo "!! no nodes to configure" >&2; exit 2; }

  local conf
  conf=$(cat <<EOF
[[registry]]
location = "${REGISTRY_HOST}:${REGISTRY_PORT}"
insecure = true
EOF
)
  for node in $nodes; do
    local target="$node"
    [[ -n "${SSH_USER:-}" ]] && target="${SSH_USER}@${node}"
    echo ">> configuring $node"
    # shellcheck disable=SC2029  -- $conf is intentionally expanded client-side
    ssh "$target" "echo '$conf' | sudo tee /etc/containers/registries.conf.d/${CONF_NAME} >/dev/null \
      && sudo systemctl restart crio"
  done
  echo ">> done. verify on any node with:"
  echo "     crictl pull ${REGISTRY_HOST}:${REGISTRY_PORT}/dms:dev"
}

case "${1:-}" in
  registry) start_registry ;;
  nodes) configure_nodes ;;
  all) start_registry; configure_nodes ;;
  *)
    echo "usage: $0 {registry|nodes|all}" >&2
    exit 2
    ;;
esac
