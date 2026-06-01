#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
사용법:
  register-identity-mappings.sh <identity-mappings.json>

JSON file 형식:
  {"identity_mappings": [ ... ]}

환경변수:
  DMS_API_URL  필수
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

jq -c '.identity_mappings[]' "$file" | while IFS= read -r item; do
  provider="$(jq -r '.identity_provider | @uri' <<<"$item")"
  requester_id="$(jq -r '.requester_id | @uri' <<<"$item")"
  label="$(jq -r '.identity_provider + ":" + .requester_id' <<<"$item")"
  echo "identity mapping 등록 중: $label"
  curl -fsS -X PUT "${api_url%/}/api/v1/identity-mappings/${provider}/${requester_id}" \
    "${headers[@]}" \
    --data "$item"
  echo
done
