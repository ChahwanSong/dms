#!/usr/bin/env bash
set -euo pipefail

api_url="${DMS_API_URL:?DMS_API_URL 값이 필요합니다}"
actor="${DMS_ACTOR:-}"
token="${DMS_TOKEN:-}"

# 운영 mTLS profile에서는 DMS_CLIENT_CERT, DMS_CLIENT_KEY, DMS_CA_CERT,
# DMS_TOKEN을 설정하고 DMS_ACTOR는 unset 상태로 둔다.
headers=()
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

check() {
  local name="$1"
  local path="$2"
  echo "== $name =="
  curl -fsS "${api_url%/}${path}" "${curl_args[@]}" "${headers[@]}"
  echo
}

check "health 확인" "/healthz"
check "control state 확인" "/api/v1/operations/control-state"
check "inventory 확인" "/api/v1/operations/inventory"
check "storage mapping 확인" "/api/v1/operations/storage-mappings"
check "work summary 확인" "/api/v1/operations/work-summary"
check "active plan 확인" "/api/v1/operations/plans/active"
check "active run 확인" "/api/v1/operations/runs/active"
check "drain status 확인" "/api/v1/operations/drain-status"
check "stale/recovery run 확인" "/api/v1/operations/runs/stale"
check "agent health 확인" "/api/v1/operations/worker-agent-health"
check "action required 확인" "/api/v1/operations/action-required"

echo "설치 검증 query가 완료되었습니다"
