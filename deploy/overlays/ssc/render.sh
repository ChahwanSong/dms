#!/bin/sh
# values.env 를 overlays/ssc 템플릿에 넣어 ../.ssc-rendered/ 를 만든다(2026-08-30).
# overlays/prod/render.sh 의 SSC 판: patch-metallb 가 없고(엣지 모드는 MetalLB 미사용)
# PORTAL_VIP 토큰도 없다. WEB_NODE·PORTAL_PUBLIC_IP 는 매니페스트에 안 들어가고
# install.sh/verify.sh 가 읽으므로 여기 KEYS 검증 대상이 아니다.
#
# 렌더 대상이 ssc 의 형제 깊이(overlays/.ssc-rendered)인 이유: kustomization 의
# `resources: - ../../k8s` 상대참조가 그대로 유효해야 base 를 다시 가리킨다.
# 성공 시 마지막 줄에 `RENDERED: <경로>`.
set -eu
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)     # deploy/overlays/ssc
OUT="$HERE/../.ssc-rendered"                      # deploy/overlays/.ssc-rendered
VALS="${VALUES_ENV:-$HERE/values.env}"
[ -f "$VALS" ] || { echo "FAIL: values.env 없음 — 'cp $HERE/values.env.example $HERE/values.env' 후 값을 채우세요"; exit 2; }
# shellcheck disable=SC1090
. "$VALS"

# 매니페스트 템플릿에 실제로 들어가는 토큰만 검증한다(12개; PORTAL_VIP 없음).
KEYS="REGISTRY DMS_TAG DMS_AGENT_TAG MFU_TAG SHARED_FS LDAP_HOST LDAP_USER_BASE \
LDAP_GROUP_BASE LDAP_BIND_DN EMAIL_DOMAIN LOCAL_ADMIN PORTAL_DOMAIN"
bad=""
for k in $KEYS; do
  v=$(eval "printf '%s' \"\${$k:-}\"")
  case "$v" in ""|REPLACE_*) bad="$bad $k";; esac
done
[ -z "$bad" ] || { echo "FAIL: values.env 미치환/빈 값 -->$bad"; exit 2; }

rm -rf "$OUT"; mkdir -p "$OUT"
cp "$HERE/kustomization.yaml" "$HERE/patch-config.yaml" "$HERE/patch-ingress.yaml" "$OUT/"
for f in "$OUT"/kustomization.yaml "$OUT"/patch-config.yaml "$OUT"/patch-ingress.yaml; do
  sed -i \
    -e "s|REPLACE_REGISTRY|$REGISTRY|g" \
    -e "s|REPLACE_DMS_AGENT_TAG|$DMS_AGENT_TAG|g" \
    -e "s|REPLACE_DMS_TAG|$DMS_TAG|g" \
    -e "s|REPLACE_MFU_TAG|$MFU_TAG|g" \
    -e "s|REPLACE_SHARED_FS|$SHARED_FS|g" \
    -e "s|REPLACE_LDAP_HOST|$LDAP_HOST|g" \
    -e "s|REPLACE_LDAP_USER_BASE|$LDAP_USER_BASE|g" \
    -e "s|REPLACE_LDAP_GROUP_BASE|$LDAP_GROUP_BASE|g" \
    -e "s|REPLACE_LDAP_BIND_DN|$LDAP_BIND_DN|g" \
    -e "s|REPLACE_EMAIL_DOMAIN|$EMAIL_DOMAIN|g" \
    -e "s|REPLACE_LOCAL_ADMIN|$LOCAL_ADMIN|g" \
    -e "s|REPLACE_PORTAL_DOMAIN|$PORTAL_DOMAIN|g" \
    "$f"
done

# 잔여 토큰 그물(주석 줄 # 은 제외 -- 문서가 REPLACE_ 를 언급해도 실토큰 아님).
leftover=$(grep -rn "REPLACE_" "$OUT" 2>/dev/null | grep -v ':[[:space:]]*#' || true)
if [ -n "$leftover" ]; then
  echo "FAIL: 렌더 후에도 REPLACE_ 토큰 남음(템플릿에 새 자리표시자?):"
  printf '%s\n' "$leftover" | sed 's/^/  /'
  exit 2
fi
echo "RENDERED: $OUT"
