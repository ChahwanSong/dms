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

# live 태그 자동 해석(2026-10-06, prod/render.sh 와 같은 로직): values.env 의 DMS_TAG/
# DMS_AGENT_TAG/MFU_TAG 를 'live' 로 두면 지금 클러스터에서 도는 이미지 태그를 읽어 채운다 --
# 포탈 빌드·릴리스로 이미지를 올린 뒤 values.env 를 안 고쳐도, 재-apply(설정 변경 등)가 가드를
# 통과하고 라이브를 옛 태그로 되돌리지 않는다. 명시 태그(dNN)는 종전과 같다. 첫 설치는 라이브
# 워크로드가 없어 'live' 를 못 쓴다(아래 에러). 해석 결과는 stderr 로만 알린다(RENDERED: 파싱 보존).
NS="${DMS_NS:-dms}"
# 라이브 이미지의 태그를 echo 한다(실패는 stderr + return 1; 서브셸이라 exit 가 부모를 못 끊으므로
# 호출측이 '|| exit 2' 로 받는다). eval 을 쓰지 않는다 -- 변수명·값이 전부 코드로만 흐르고, 외부에서
# 온 값(kubectl 이 준 이미지 문자열)은 case·파라미터 확장·따옴표 echo 로만 다룬다(명령으로 재파싱
# 되는 경로가 없다). prod/render.sh 와 같은 로직(커밋 보안 리뷰로 eval 제거, 2026-10-06).
_live_tag() {   # $1=사람이 읽는 이름  $2..=kubectl get 인자(이미지 ref 하나를 출력)
  _human=$1; shift
  _img=$(kubectl -n "$NS" get "$@" 2>/dev/null || true)
  case "$_img" in
    "") echo "FAIL: $_human 태그가 'live' 인데 클러스터에서 라이브 이미지를 읽지 못했습니다" >&2
        echo "  (첫 설치면 명시 태그(예: d118)를 쓰세요; 아니면 kubectl 접근과 네임스페이스 '$NS' 를 확인)" >&2
        return 1 ;;
    *set-by-overlay*) echo "FAIL: $_human 라이브가 아직 자리표시자입니다($_img) — 오버레이 미적용(첫 설치) 상태라 'live' 를 쓸 수 없습니다. 명시 태그를 쓰세요" >&2
        return 1 ;;
    *:*) printf '%s' "${_img##*:}" ;;
    *) echo "FAIL: $_human 라이브 이미지 '$_img' 에 태그(:) 가 없습니다" >&2; return 1 ;;
  esac
}
if [ "${DMS_TAG:-}" = "live" ]; then
  DMS_TAG=$(_live_tag "dms(api·controller)" deploy dms-api -o 'jsonpath={.spec.template.spec.containers[0].image}') || exit 2
  echo "live: DMS_TAG=$DMS_TAG" >&2
fi
if [ "${DMS_AGENT_TAG:-}" = "live" ]; then
  DMS_AGENT_TAG=$(_live_tag "dms-agent" ds dms-agent -o 'jsonpath={.spec.template.spec.containers[0].image}') || exit 2
  echo "live: DMS_AGENT_TAG=$DMS_AGENT_TAG" >&2
fi
if [ "${MFU_TAG:-}" = "live" ]; then
  MFU_TAG=$(_live_tag "dms-mpifileutils(잡 이미지)" configmap dms-config -o 'jsonpath={.data.DMS_JOB_IMAGE}') || exit 2
  echo "live: MFU_TAG=$MFU_TAG" >&2
fi

# 매니페스트 템플릿에 실제로 들어가는 토큰만 검증한다(12개; PORTAL_VIP 없음).
KEYS="REGISTRY DMS_TAG DMS_AGENT_TAG MFU_TAG SHARED_FS LDAP_HOST LDAP_USER_BASE \
LDAP_GROUP_BASE LDAP_BIND_DN EMAIL_DOMAIN LOCAL_ADMIN PORTAL_DOMAIN"
bad=""
for k in $KEYS; do
  v=$(eval "printf '%s' \"\${$k:-}\"")
  case "$v" in ""|REPLACE_*) bad="$bad $k";; esac
done
[ -z "$bad" ] || { echo "FAIL: values.env 미치환/빈 값 -->$bad"; exit 2; }

# 선택 키(2026-10-08, prod 오버레이와 같은 규칙): KEYS(필수)에 넣지 않는다 -- 이 키가 생기기 전의 values.env 도 그대로
# 렌더돼야 한다(생략 = 코드 기본과 같은 "true"). 값은 true|false 만: config 의 파서는 true/1 외 전부 꺼짐으로 읽어
# "True"·"yes" 같은 오타가 조용히 기능을 끈다 -- 여기서 시끄럽게 실패시킨다.
IDENTITY_SUPPLEMENTARY_GROUPS="${IDENTITY_SUPPLEMENTARY_GROUPS:-true}"
case "$IDENTITY_SUPPLEMENTARY_GROUPS" in
  true|false) ;;
  *) echo "FAIL: IDENTITY_SUPPLEMENTARY_GROUPS 는 true|false (지금: '$IDENTITY_SUPPLEMENTARY_GROUPS')"; exit 2;;
esac

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
    -e "s|REPLACE_IDENTITY_SUPPLEMENTARY_GROUPS|$IDENTITY_SUPPLEMENTARY_GROUPS|g" \
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
