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

# live 태그 자동 해석(2026-10-06): values.env 의 DMS_TAG/DMS_AGENT_TAG/MFU_TAG 를 'live' 로
# 두면 지금 클러스터에서 돌고 있는 이미지 태그를 읽어 채운다 -- 포탈 빌드·릴리스로 이미지를
# 올린 뒤 values.env 를 손대지 않아도, 설정 변경 등으로 재-apply 할 때 가드(guard-images.sh)가
# 통과하고 라이브가 옛 태그로 되돌아가지 않는다(포탈 릴리스는 values.env 를 안 읽으므로 평소엔
# 이 파일을 손댈 일이 없다 -- 'live' 는 그 한 번의 재-apply 수작업마저 없앤다). 명시 태그(dNN)를
# 쓰면 종전과 같다. 첫 설치는 라이브 워크로드가 없어 'live' 를 못 쓴다(아래 에러로 안내).
# 해석 결과는 stderr 로만 알린다 -- stdout 의 마지막 `RENDERED:` 줄 파싱(install.sh)을 흐리지 않게.
NS="${DMS_NS:-dms}"
# 라이브 이미지의 태그를 echo 한다(실패는 stderr + return 1; 서브셸이라 exit 가 부모를 못 끊으므로
# 호출측이 '|| exit 2' 로 받는다). eval 을 쓰지 않는다 -- 변수명·값이 전부 코드로만 흐르고, 외부에서
# 온 값(kubectl 이 준 이미지 문자열)은 case·파라미터 확장·따옴표 echo 로만 다룬다(명령으로 재파싱
# 되는 경로가 없다; 적대적 태그 주입 실측으로 확인). 태그당 if 블록이라 간접 변수 접근도 필요 없다.
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
