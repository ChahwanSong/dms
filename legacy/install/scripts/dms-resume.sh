#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
사용법:
  dms-resume.sh [--reason <reason>] [--force] [--replicas <n>] [--skip-scale-up]

설명:
  DMS control state를 resume으로 되돌리고, 선택적으로 worker Deployment replica를 복구한다.
  force 없이 resume하면 RecoveryNeeded/UnknownAfterSideEffect/BackendApplyFailed가 남아 있을 때 API가 409를 반환한다.

환경변수:
  DMS_API_URL              필수
  DMS_TOKEN                DMS_AUTH_SHARED_TOKEN 사용 시 필수
  DMS_ACTOR                dev/test actor header가 필요할 때만 설정
  DMS_CLIENT_CERT          운영 mTLS client certificate path
  DMS_CLIENT_KEY           운영 mTLS client private key path
  DMS_CA_CERT              DMS API CA path
  DMS_NAMESPACE            기본값: dms
  DMS_KUBECTL_CONTEXT      선택, kubectl context
  DMS_WORKER_DEPLOYMENTS   기본값: "dms-dm-worker". 공백으로 구분해 여러 Deployment를 넣을 수 있다.
USAGE
}

reason="DMS resume"
force=false
replicas=1
skip_scale_up=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reason)
      reason="${2:?--reason 값이 필요합니다}"
      shift 2
      ;;
    --force)
      force=true
      shift
      ;;
    --replicas)
      replicas="${2:?--replicas 값이 필요합니다}"
      shift 2
      ;;
    --skip-scale-up)
      skip_scale_up=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

command -v curl >/dev/null || { echo "curl이 필요합니다" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq가 필요합니다" >&2; exit 1; }
if [[ "$skip_scale_up" != "true" ]]; then
  command -v kubectl >/dev/null || { echo "kubectl이 필요합니다" >&2; exit 1; }
fi

api_url="${DMS_API_URL:?DMS_API_URL 값이 필요합니다}"
namespace="${DMS_NAMESPACE:-dms}"
worker_deployments="${DMS_WORKER_DEPLOYMENTS:-dms-dm-worker}"
actor="${DMS_ACTOR:-}"
token="${DMS_TOKEN:-}"

headers=(-H "content-type: application/json")
curl_args=()
if [[ -n "$actor" ]]; then
  headers+=(-H "x-dms-actor: $actor")
fi
if [[ -n "$token" ]]; then
  headers+=(-H "authorization: Bearer $token")
fi
if [[ -n "${DMS_CLIENT_CERT:-}" ]]; then
  curl_args+=(--cert "$DMS_CLIENT_CERT")
fi
if [[ -n "${DMS_CLIENT_KEY:-}" ]]; then
  curl_args+=(--key "$DMS_CLIENT_KEY")
fi
if [[ -n "${DMS_CA_CERT:-}" ]]; then
  curl_args+=(--cacert "$DMS_CA_CERT")
fi

kubectl_args=(-n "$namespace")
if [[ -n "${DMS_KUBECTL_CONTEXT:-}" ]]; then
  kubectl_args=(--context "$DMS_KUBECTL_CONTEXT" "${kubectl_args[@]}")
fi

curl_dms() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  if [[ -n "$body" ]]; then
    curl -fsS -X "$method" "${api_url%/}${path}" "${curl_args[@]}" "${headers[@]}" --data "$body"
  else
    curl -fsS -X "$method" "${api_url%/}${path}" "${curl_args[@]}" "${headers[@]}"
  fi
}

body="$(jq -n --arg reason "$reason" --argjson force "$force" '{reason: $reason, force: $force}')"
echo "DMS control state를 resume합니다"
curl_dms POST "/api/v1/operations/control-state:resume" "$body" | jq .

if [[ "$skip_scale_up" == "true" ]]; then
  echo "--skip-scale-up 상태이므로 Deployment scale up은 수행하지 않습니다"
else
  for deployment in $worker_deployments; do
    echo "Deployment scale up: $deployment -> $replicas"
    kubectl "${kubectl_args[@]}" scale deployment "$deployment" --replicas="$replicas"
  done

  for deployment in $worker_deployments; do
    kubectl "${kubectl_args[@]}" rollout status deployment "$deployment" --timeout=180s
  done
fi

echo "resume 후 work summary:"
curl_dms GET "/api/v1/operations/work-summary" | jq .
