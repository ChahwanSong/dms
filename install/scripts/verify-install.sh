#!/usr/bin/env bash
set -euo pipefail

api_url="${DMS_API_URL:?DMS_API_URL is required}"
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

check "health" "/healthz"
check "inventory" "/api/v1/operations/inventory"
check "storage mappings" "/api/v1/operations/storage-mappings"
check "agent health" "/api/v1/operations/worker-agent-health"
check "action required" "/api/v1/operations/action-required"

echo "install verification queries completed"
