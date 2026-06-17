#!/usr/bin/env bash
set -euo pipefail

# DM identity denylist (kill-switch + admission block) bulk-apply helper.
#
# identity_mappings was REMOVED. DM resolves the requester's POSIX identity by a
# READ-ONLY LDAP lookup at preflight time; there is NO mapping registration step.
# The denylist is normally EMPTY (default = allow all) and is operated per entry as
# an instant kill-switch. This script is only for seeding a known block list (e.g.
# offboarded accounts) from a JSON file -- it is optional.

usage() {
  cat >&2 <<'USAGE'
사용법:
  apply-identity-denylist.sh <identity-denylist.json>

JSON file 형식:
  {"entries": [ {"subject_type": "requester|owner|group", "subject": "...", "reason": "..."} ]}

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

jq -c '.entries[]' "$file" | while IFS= read -r item; do
  subject_type="$(jq -r '.subject_type | @uri' <<<"$item")"
  subject="$(jq -r '.subject | @uri' <<<"$item")"
  label="$(jq -r '.subject_type + "/" + .subject' <<<"$item")"
  reason="$(jq -c '{reason}' <<<"$item")"
  echo "identity denylist 적용 중: $label"
  curl -fsS -X PUT "${api_url%/}/api/v1/data-management/identity-denylist/${subject_type}/${subject}" \
    "${curl_args[@]}" \
    "${headers[@]}" \
    --data "$reason"
  echo
done
