#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
사용법:
  register-storage-mappings.sh <storage-mappings.json>

JSON file 형식:
  {"storage_mappings": [ ... ]}

환경변수:
  DMS_API_URL  필수, 예: https://dms.example.internal
  DMS_TOKEN    DMS_AUTH_SHARED_TOKEN이 설정되어 있으면 필수
  DMS_ACTOR    기본값: installer
USAGE
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

command -v jq >/dev/null || { echo "jq가 필요합니다" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl이 필요합니다" >&2; exit 1; }

file="$1"
api_url="${DMS_API_URL:?DMS_API_URL 값이 필요합니다}"
actor="${DMS_ACTOR:-installer}"
token="${DMS_TOKEN:-}"

headers=(-H "x-dms-actor: $actor" -H "content-type: application/json")
if [[ -n "$token" ]]; then
  headers+=(-H "authorization: Bearer $token")
fi

jq -c '.storage_mappings[]' "$file" | while IFS= read -r item; do
  storage_name="$(jq -r '.storage_name' <<<"$item")"
  echo "storage mapping 등록 중: $storage_name"
  curl -fsS -X POST "${api_url%/}/api/v1/resource-management/storage-mappings" \
    "${headers[@]}" \
    --data "$item"
  echo
done
