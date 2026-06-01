#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  create-serviceaccount-kubeconfig.sh <cluster-name> <output-kubeconfig>

Run this while kubectl is pointed at the target cluster after applying:
  install/kubernetes/target-cluster-rbac.yaml

Environment:
  DMS_REMOTE_NAMESPACE       default: dms
  DMS_REMOTE_SERVICE_ACCOUNT default: dms-remote
  DMS_TOKEN_DURATION         default: 8760h
USAGE
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

cluster_name="$1"
output="$2"
namespace="${DMS_REMOTE_NAMESPACE:-dms}"
service_account="${DMS_REMOTE_SERVICE_ACCOUNT:-dms-remote}"
duration="${DMS_TOKEN_DURATION:-8760h}"

server="$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
ca_data="$(kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
token="$(kubectl -n "$namespace" create token "$service_account" --duration="$duration")"

kubectl config --kubeconfig "$output" set-cluster "$cluster_name" \
  --server="$server" \
  --certificate-authority-data="$ca_data" >/dev/null
kubectl config --kubeconfig "$output" set-credentials "dms-${cluster_name}" \
  --token="$token" >/dev/null
kubectl config --kubeconfig "$output" set-context "$cluster_name" \
  --cluster="$cluster_name" \
  --user="dms-${cluster_name}" >/dev/null
kubectl config --kubeconfig "$output" use-context "$cluster_name" >/dev/null

chmod 0600 "$output"
echo "wrote $output for cluster $cluster_name"
