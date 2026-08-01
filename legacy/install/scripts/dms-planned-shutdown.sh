#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
사용법:
  dms-planned-shutdown.sh [--reason <reason>] [--timeout-seconds <n>] [--poll-seconds <n>] [--dry-run] [--skip-scale-down]

설명:
  DMS API로 drain mode에 진입하고 active run이 사라질 때까지 기다린 뒤,
  control cluster의 worker Deployment를 0 replica로 낮춘다.
  Kubernetes node drain/reboot는 이 script가 수행하지 않는다.

환경변수:
  DMS_API_URL              필수, 예: https://dms.example.internal
  DMS_TOKEN                DMS_AUTH_SHARED_TOKEN 사용 시 필수
  DMS_ACTOR                dev/test actor header가 필요할 때만 설정
  DMS_CLIENT_CERT          운영 mTLS client certificate path
  DMS_CLIENT_KEY           운영 mTLS client private key path
  DMS_CA_CERT              DMS API CA path
  DMS_NAMESPACE            기본값: dms
  DMS_KUBECTL_CONTEXT      선택, kubectl context
  DMS_WORKER_DEPLOYMENTS   기본값: "dms-dm-worker"
USAGE
}

reason="planned DMS shutdown"
timeout_seconds=900
poll_seconds=10
dry_run=false
skip_scale_down=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reason)
      reason="${2:?--reason 값이 필요합니다}"
      shift 2
      ;;
    --timeout-seconds)
      timeout_seconds="${2:?--timeout-seconds 값이 필요합니다}"
      shift 2
      ;;
    --poll-seconds)
      poll_seconds="${2:?--poll-seconds 값이 필요합니다}"
      shift 2
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --skip-scale-down)
      skip_scale_down=true
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
if [[ "$dry_run" != "true" && "$skip_scale_down" != "true" ]]; then
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

echo "DMS drain mode에 진입합니다: $reason"
body="$(jq -n --arg reason "$reason" '{reason: $reason}')"
curl_dms POST "/api/v1/operations/control-state:begin-drain" "$body" | jq .

deadline=$((SECONDS + timeout_seconds))
status_json=""
while true; do
  status_json="$(curl_dms GET "/api/v1/operations/drain-status")"
  ready="$(jq -r '.ready_for_shutdown' <<<"$status_json")"
  active="$(jq -r '.active_runs.count' <<<"$status_json")"
  echo "drain 상태: ready_for_shutdown=$ready active_runs=$active"
  if [[ "$ready" == "true" ]]; then
    break
  fi
  if (( SECONDS >= deadline )); then
    echo "drain 대기 시간이 초과되었습니다. 현재 상태:" >&2
    jq . <<<"$status_json" >&2
    exit 3
  fi
  sleep "$poll_seconds"
done

echo "work summary:"
curl_dms GET "/api/v1/operations/work-summary" | jq .

if [[ "$dry_run" == "true" || "$skip_scale_down" == "true" ]]; then
  echo "dry-run 또는 --skip-scale-down 상태이므로 Deployment scale down은 수행하지 않습니다"
  exit 0
fi

for deployment in $worker_deployments; do
  echo "Deployment scale down: $deployment -> 0"
  kubectl "${kubectl_args[@]}" scale deployment "$deployment" --replicas=0
done

for deployment in $worker_deployments; do
  kubectl "${kubectl_args[@]}" rollout status deployment "$deployment" --timeout=120s
done

echo "DMS planned shutdown 준비가 완료되었습니다. 이후 Kubernetes node drain/reboot는 운영자가 별도로 수행합니다."
