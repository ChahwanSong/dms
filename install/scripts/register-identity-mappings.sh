#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  register-identity-mappings.sh <identity-mappings.json>

The JSON file can be:
  {"identity_mappings": [ ... ]}

Environment:
  DMS_API_URL  required
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

jq -c '.identity_mappings[]' "$file" | while IFS= read -r item; do
  provider="$(jq -r '.identity_provider | @uri' <<<"$item")"
  requester_id="$(jq -r '.requester_id | @uri' <<<"$item")"
  label="$(jq -r '.identity_provider + ":" + .requester_id' <<<"$item")"
  echo "registering identity mapping: $label"
  curl -fsS -X PUT "${api_url%/}/api/v1/identity-mappings/${provider}/${requester_id}" \
    "${headers[@]}" \
    --data "$item"
  echo
done
