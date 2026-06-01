#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
사용법:
  create-serviceaccount-kubeconfig.sh <cluster-name> <output-kubeconfig>

다음을 적용한 뒤 kubectl이 target cluster를 가리키는 상태에서 실행한다.
  install/kubernetes/target-cluster-rbac.yaml

환경변수:
  DMS_REMOTE_NAMESPACE       기본값: dms
  DMS_REMOTE_SERVICE_ACCOUNT 기본값: dms-remote
  DMS_TOKEN_DURATION         기본값: 8760h
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
echo "$cluster_name cluster용 kubeconfig를 $output 경로에 작성했습니다"
