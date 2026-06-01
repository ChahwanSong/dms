#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  register-default-quota-policies.sh <default-quota-policies.json>

The JSON file can be:
  {"default_quota_policies": [ ... ]}

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

jq -c '.default_quota_policies[]' "$file" | while IFS= read -r item; do
  policy="$(jq -r '.resource_kind + ":" + .resource_type' <<<"$item")"
  echo "registering default quota policy: $policy"
  curl -fsS -X POST "${api_url%/}/api/v1/resource-management/default-quota-policies" \
    "${headers[@]}" \
    --data "$item"
  echo
done
