#!/usr/bin/env bash
set -euo pipefail

api_url="${DMS_API_URL:?DMS_API_URL 값이 필요합니다}"
actor="${DMS_ACTOR:-operator}"
token="${DMS_TOKEN:-}"

headers=(-H "x-dms-actor: $actor")
if [[ -n "$token" ]]; then
  headers+=(-H "authorization: Bearer $token")
fi

check() {
  local name="$1"
  local path="$2"
  echo "== $name =="
  curl -fsS "${api_url%/}${path}" "${headers[@]}"
  echo
}

check "health 확인" "/healthz"
check "inventory 확인" "/api/v1/operations/inventory"
check "storage mapping 확인" "/api/v1/operations/storage-mappings"
check "agent health 확인" "/api/v1/operations/worker-agent-health"
check "action required 확인" "/api/v1/operations/action-required"

echo "설치 검증 query가 완료되었습니다"
