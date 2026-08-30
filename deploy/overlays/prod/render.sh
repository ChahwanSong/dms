#!/bin/sh
# values.env 를 overlays/prod 템플릿에 넣어 ../.prod-rendered/ 를 만든다(2026-08-30).
# 목적: 에이전트가 파일 4개를 손으로 고치는 대신 값 파일 하나만 채우고 명령 하나로
# 렌더하게 해 설치 토큰을 줄인다. 검증 실패는 "어느 키가 비었나"를 한 줄로 알린다.
#
# 렌더 대상이 prod 의 형제 깊이(overlays/.prod-rendered)인 이유: kustomization 의
# `resources: - ../../k8s` 상대참조가 그대로 유효해야 base(테스트베드 매니페스트)를
# 다시 가리킨다. prod 안(overlays/prod/.rendered)에 두면 ../../k8s 가 어긋난다.
#
# 성공 시 마지막 줄에 `RENDERED: <경로>` 를 찍는다(install.sh 가 파싱).
set -eu
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)     # deploy/overlays/prod
OUT="$HERE/../.prod-rendered"                    # deploy/overlays/.prod-rendered
VALS="${VALUES_ENV:-$HERE/values.env}"
[ -f "$VALS" ] || { echo "FAIL: values.env 없음 — 'cp $HERE/values.env.example $HERE/values.env' 후 값을 채우세요"; exit 2; }
# shellcheck disable=SC1090
. "$VALS"

KEYS="REGISTRY DMS_TAG DMS_AGENT_TAG MFU_TAG SHARED_FS LDAP_HOST LDAP_USER_BASE \
LDAP_GROUP_BASE LDAP_BIND_DN EMAIL_DOMAIN LOCAL_ADMIN PORTAL_DOMAIN PORTAL_VIP"
bad=""
for k in $KEYS; do
  v=$(eval "printf '%s' \"\${$k:-}\"")
  case "$v" in ""|REPLACE_*) bad="$bad $k";; esac
done
[ -z "$bad" ] || { echo "FAIL: values.env 미치환/빈 값 -->$bad"; exit 2; }

rm -rf "$OUT"; mkdir -p "$OUT"
cp "$HERE/kustomization.yaml" "$HERE/patch-config.yaml" \
   "$HERE/patch-ingress.yaml" "$HERE/patch-metallb.yaml" "$OUT/"
# 긴 토큰(REPLACE_DMS_AGENT_TAG)을 짧은 토큰(REPLACE_DMS_TAG)보다 먼저 치환한다
# (여기선 서로 접두관계가 아니지만 방어적으로). 구분자는 | -- 값에 / : , 가 있어도 안전.
for f in "$OUT"/kustomization.yaml "$OUT"/patch-config.yaml \
         "$OUT"/patch-ingress.yaml "$OUT"/patch-metallb.yaml; do
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
    -e "s|REPLACE_PORTAL_VIP|$PORTAL_VIP|g" \
    "$f"
done

# 잔여 토큰 그물: 값은 다 채웠어도 템플릿에 새 REPLACE_ 가 생기면(스키마 변경)
# 여기서 잡아 apply 전에 시끄럽게 실패시킨다. 주석 줄(# 로 시작)은 제외한다 --
# 문서/주석이 "REPLACE_" 를 문자 그대로 언급해도 실제 미치환 토큰은 아니다.
leftover=$(grep -rn "REPLACE_" "$OUT" 2>/dev/null | grep -v ':[[:space:]]*#' || true)
if [ -n "$leftover" ]; then
  echo "FAIL: 렌더 후에도 REPLACE_ 토큰 남음(템플릿에 새 자리표시자?):"
  printf '%s\n' "$leftover" | sed 's/^/  /'
  exit 2
fi
echo "RENDERED: $OUT"
