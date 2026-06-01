#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  register-storage-mappings.sh <storage-mappings.json>

The JSON file can be:
  {"storage_mappings": [ ... ]}

Environment:
  DMS_API_URL  required, for example https://dms.example.internal
  DMS_TOKEN    required when DMS_AUTH_SHARED_TOKEN is configured
  DMS_ACTOR    default: installer
USAGE
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }

file="$1"
api_url="${DMS_API_URL:?DMS_API_URL is required}"
actor="${DMS_ACTOR:-installer}"
token="${DMS_TOKEN:-}"

headers=(-H "x-dms-actor: $actor" -H "content-type: application/json")
if [[ -n "$token" ]]; then
  headers+=(-H "authorization: Bearer $token")
fi

jq -c '.storage_mappings[]' "$file" | while IFS= read -r item; do
  storage_name="$(jq -r '.storage_name' <<<"$item")"
  echo "registering storage mapping: $storage_name"
  curl -fsS -X POST "${api_url%/}/api/v1/resource-management/storage-mappings" \
    "${headers[@]}" \
    --data "$item"
  echo
done
