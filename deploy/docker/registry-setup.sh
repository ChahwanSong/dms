#!/usr/bin/env bash
#
# Testbed registry helper (2026-10-21: two testbeds shared the pkg-01 registry VM;
# since 2026-11-04 only the ssc testbed uses it).
#
# The REGISTRY ITSELF is no longer created here. This script used to start a podman
# `dms-registry` with a named volume and deletes disabled -- a different thing from
# what actually runs on pkg-01 (docker `registry:2`, bind mount, deletes enabled; the
# portal's image-management DELETE answers 405 without that). The live shape is now
# owned by the testbed repo (~/dms-dev/testbed, `registry_server` role / `make registry`):
#
#   name          port   data dir                  cluster (kube context)
#   registry      5000   /opt/dms-registry         ssc testbed  (dms)    + buildah:stable / add-on mirrors
#   docker run -d --restart=always -e REGISTRY_STORAGE_DELETE_ENABLED=true
#         -v <data dir>:/var/lib/registry -p <port>:5000 registry:2
#
# `registry` therefore refuses (exit 2) and points to the testbed repo instead of
# creating a mismatched registry.
#
# The k8s testbed (context dmsk8s) uses no pkg-01 registry: since 2026-11-03 (design v6 bundle 4,
# T4 16, d19) all its images -- dms, dms-agent, dms-mpifileutils, builder buildah:stable -- live in
# the in-cluster registry 10.112.0.50:5000 (deploy/platform/registry). Its old host registry
# `registry-k8s` (:5001, /opt/dms-registry-k8s, tags up to d18) was deleted 2026-11-04 (user
# decision: no rollback to it).
#
# What stays here is the node-side trust helper:
#
#   nodes step -- write /etc/containers/registries.conf.d/99-pkg-01-insecure.conf on
#   every node of ONE cluster (insecure = true per registry) and restart CRI-O. The
#   cluster is chosen explicitly -- there is no default context any more (two clusters
#   share the namespace name, so an implicit context is how you configure the wrong one):
#
#     ./deploy/docker/registry-setup.sh nodes --context dms      # ssc: pkg-01:5000
#     # or an explicit node list (no kubectl needed):
#     NODES="dms-w1 dms-w2 dms-w3" REGISTRIES="pkg-01:5000" ./deploy/docker/registry-setup.sh nodes
#
#   The testbed repo's crio role writes the same file with the same content on
#   provisioning; this helper is for fixing a node by hand without a full play.
#   `--context dmsk8s` has no default any more and refuses (exit 2) unless REGISTRIES is
#   given: that cluster's trust is owned by the testbed repo's crio role -- crio_dms_registry
#   -> 50-dms-registry.conf (the in-cluster registry); crio_insecure_registries is [] there
#   (group_vars/dmsk8s.yml), so the role REMOVES this 99-pkg-01-insecure.conf. Apply just that:
#   cd ~/dms-dev/testbed && make baseline ANSIBLE='ansible-playbook --limit dmsk8s --tags crio_trust'
#
# Env knobs (nodes step):
#   KUBE_CONTEXT  same as --context (the flag wins)
#   NODES         space-separated node names (default: `kubectl --context <ctx> get nodes`)
#   REGISTRIES    space-separated host:port list to trust (default by context:
#                 dms -> "pkg-01:5000"; dmsk8s refuses, see above;
#                 any other context needs REGISTRIES explicitly)
#   SSH_USER      ssh user for each node                  (default: current user)
#   SSH_OPTS      extra ssh options, e.g. "-i ~/dms-dev/testbed/files/id_ed25519"
set -euo pipefail

CONF_NAME="99-pkg-01-insecure.conf"

usage() {
  echo "usage: $0 nodes [--context <kube-context>]    (registry step: see the testbed repo, header)" >&2
  exit 2
}

registry_superseded() {
  cat >&2 <<'EOF'
!! the registry is not created by this script any more (it built a podman registry
!! with deletes disabled -- not what runs on pkg-01). Use the testbed repo:
!!     cd ~/dms-dev/testbed && make registry        # registry_server role
!! live shape: docker registry:2, restart=always, REGISTRY_STORAGE_DELETE_ENABLED=true,
!!   registry     :5000  /opt/dms-registry       (ssc testbed, context dms)
!! (the k8s testbed, context dmsk8s, uses its in-cluster registry 10.112.0.50:5000 --
!!  deploy/platform/registry; its old pkg-01:5001 registry was deleted 2026-11-04)
EOF
  exit 2
}

dmsk8s_no_default() {
  cat >&2 <<'EOF'
!! context dmsk8s has no default trust list any more: since 2026-11-03 all its images
!! (dms, dms-agent, dms-mpifileutils, builder buildah:stable) come from the in-cluster registry
!! 10.112.0.50:5000 (deploy/platform/registry), and its old pkg-01:5001 registry was deleted
!! 2026-11-04. Its node trust is owned by the testbed repo's crio role only
!! (group_vars/dmsk8s.yml: crio_dms_registry -> 50-dms-registry.conf; crio_insecure_registries
!!  is [] there, so the role REMOVES 99-pkg-01-insecure.conf). Apply just the trust drop-ins:
!!     cd ~/dms-dev/testbed && make baseline ANSIBLE='ansible-playbook --limit dmsk8s --tags crio_trust'
!! To write 99-pkg-01-insecure.conf by hand anyway, give the list: REGISTRIES="host:port ..."
EOF
  exit 2
}

default_registries() {
  case "$1" in
    dms) echo "pkg-01:5000" ;;
    *) echo "" ;;
  esac
}

configure_nodes() {
  local ctx="${KUBE_CONTEXT:-}"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --context) [[ $# -ge 2 && -n "$2" ]] || usage; ctx="$2"; shift ;;
      --context=*) ctx="${1#--context=}"; [[ -n "$ctx" ]] || usage ;;
      *) usage ;;
    esac
    shift
  done
  # Before any kubectl call: the dmsk8s trust belongs to the testbed IaC (see the header).
  [[ "$ctx" != dmsk8s || -n "${REGISTRIES:-}" ]] || dmsk8s_no_default

  local nodes="${NODES:-}"
  if [[ -z "$nodes" ]]; then
    [[ -n "$ctx" ]] || { echo "!! pass --context <kube-context> (e.g. dms; dmsk8s needs REGISTRIES) or NODES=\"...\" -- no default cluster" >&2; exit 2; }
    command -v kubectl >/dev/null 2>&1 \
      || { echo "!! kubectl not found -- set NODES=\"node1 node2 ...\"" >&2; exit 2; }
    nodes="$(kubectl --context "$ctx" get nodes -o jsonpath='{.items[*].metadata.name}')"
  fi
  [[ -n "$nodes" ]] || { echo "!! no nodes to configure" >&2; exit 2; }

  local registries="${REGISTRIES:-}"
  [[ -n "$registries" ]] || registries="$(default_registries "$ctx")"
  [[ -n "$registries" ]] \
    || { echo "!! no default registry list for context '${ctx:-<none>}' -- set REGISTRIES=\"host:port ...\"" >&2; exit 2; }

  local conf="" reg
  for reg in $registries; do
    conf+="[[registry]]
location = \"${reg}\"
insecure = true
"
  done

  local node target
  for node in $nodes; do
    target="$node"
    [[ -n "${SSH_USER:-}" ]] && target="${SSH_USER}@${node}"
    echo ">> configuring $node (${registries})"
    # shellcheck disable=SC2029,SC2086  -- $conf is expanded client-side; SSH_OPTS is a word list
    printf '%s' "$conf" | ssh ${SSH_OPTS:-} "$target" \
      "sudo tee /etc/containers/registries.conf.d/${CONF_NAME} >/dev/null && sudo systemctl restart crio"
  done
  echo ">> done. verify on any node with (one per registry):"
  for reg in $registries; do echo "     sudo crictl pull ${reg}/<repo>:<tag>"; done
}

case "${1:-}" in
  registry|all) registry_superseded ;;
  nodes) shift; configure_nodes "$@" ;;
  *) usage ;;
esac
