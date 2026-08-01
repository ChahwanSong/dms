#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
사용법:
  dms-startup-recovery-check.sh [--allow-action-required]

설명:
  DMS API 재기동 후 stale/recovery run을 갱신하고 control state,
  drain status, work summary, action-required, worker-agent-health를 조회한다.
  RecoveryNeeded/UnknownAfterSideEffect/BackendApplyFailed가 있으면 실패한다.

환경변수:
  DMS_API_URL      필수
  DMS_TOKEN        DMS_AUTH_SHARED_TOKEN 사용 시 필수
  DMS_ACTOR        dev/test actor header가 필요할 때만 설정
  DMS_CLIENT_CERT  운영 mTLS client certificate path
  DMS_CLIENT_KEY   운영 mTLS client private key path
  DMS_CA_CERT      DMS API CA path
USAGE
}

allow_action_required=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-action-required)
      allow_action_required=true
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

api_url="${DMS_API_URL:?DMS_API_URL 값이 필요합니다}"
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

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

echo "expired lease run을 stale/recovery 상태로 갱신합니다"
curl_dms POST "/api/v1/operations/runs:mark-stale" | tee "$tmpdir/mark-stale.json" | jq .

echo "control state:"
curl_dms GET "/api/v1/operations/control-state" | tee "$tmpdir/control-state.json" | jq .

echo "drain status:"
curl_dms GET "/api/v1/operations/drain-status" | tee "$tmpdir/drain-status.json" | jq .

echo "work summary:"
curl_dms GET "/api/v1/operations/work-summary" | tee "$tmpdir/work-summary.json" | jq .

echo "stale/recovery runs:"
curl_dms GET "/api/v1/operations/runs/stale" | tee "$tmpdir/runs-stale.json" | jq .

echo "action-required:"
curl_dms GET "/api/v1/operations/action-required" | tee "$tmpdir/action-required.json" | jq .

echo "worker-agent-health:"
curl_dms GET "/api/v1/operations/worker-agent-health" | tee "$tmpdir/worker-agent-health.json" | jq .

hard_blockers="$(jq '[.[] | select(.state == "RecoveryNeeded" or .state == "UnknownAfterSideEffect" or .state == "BackendApplyFailed")] | length' "$tmpdir/runs-stale.json")"
action_required_count="$(jq 'length' "$tmpdir/action-required.json")"

if (( hard_blockers > 0 )); then
  echo "resume 전에 운영자 확인이 필요한 recovery blocker가 있습니다: $hard_blockers" >&2
  exit 3
fi

if (( action_required_count > 0 )) && [[ "$allow_action_required" != "true" ]]; then
  echo "action-required 항목이 있습니다: $action_required_count" >&2
  echo "운영자가 확인했고 resume을 진행해야 한다면 --allow-action-required를 사용하십시오" >&2
  exit 4
fi

echo "startup recovery check가 통과했습니다"
