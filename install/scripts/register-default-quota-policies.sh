#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
사용법:
  register-default-quota-policies.sh <default-quota-policies.json>

JSON file 형식:
  {"default_quota_policies": [ ... ]}

환경변수:
  DMS_API_URL  필수
  DMS_TOKEN    DMS_AUTH_SHARED_TOKEN이 설정되어 있으면 필수
  DMS_ACTOR    선택, dev/test actor header가 필요할 때만 설정. 운영 mTLS profile에서는 unset
  DMS_CLIENT_CERT  운영 mTLS profile에서는 필수, client certificate path
  DMS_CLIENT_KEY   운영 mTLS profile에서는 필수, client private key path
  DMS_CA_CERT      운영 mTLS profile에서는 필수, DMS API server CA path
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

jq -c '.default_quota_policies[]' "$file" | while IFS= read -r item; do
  policy="$(jq -r '.resource_kind + ":" + .resource_type' <<<"$item")"
  echo "default quota policy 등록 중: $policy"
  curl -fsS -X POST "${api_url%/}/api/v1/resource-management/default-quota-policies" \
    "${curl_args[@]}" \
    "${headers[@]}" \
    --data "$item"
  echo
done
