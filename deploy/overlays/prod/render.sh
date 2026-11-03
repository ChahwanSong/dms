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
# --context(2026-10-26): 'live' 태그를 읽을 클러스터(guard-images.sh 와 같은 문법 — --context <ctx> / --context=<ctx> /
# KUBE_CONTEXT, 플래그가 이긴다). 명시 태그만이면 kubectl 을 부르지 않는다(컨텍스트 존재 확인도 'live' 일 때만).
CTX="${KUBE_CONTEXT:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --context) [ $# -ge 2 ] && [ -n "$2" ] || { echo "usage: $0 [--context <ctx>]" >&2; exit 2; }; CTX="$2"; shift ;;
    --context=*) CTX="${1#--context=}"; [ -n "$CTX" ] || { echo "usage: $0 [--context <ctx>]" >&2; exit 2; } ;;
    *) echo "usage: $0 [--context <ctx>]" >&2; exit 2 ;;
  esac
  shift
done
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)     # deploy/overlays/prod
OUT="$HERE/../.prod-rendered"                    # deploy/overlays/.prod-rendered
VALS="${VALUES_ENV:-$HERE/values.env}"
[ -f "$VALS" ] || { echo "FAIL: values.env 없음 — 'cp $HERE/values.env.example $HERE/values.env' 후 값을 채우세요"; exit 2; }
# 옛 키 SHARED_FS(2026-11-03 ③ 에 SHARED_DIR 로 바뀜)는 values.env 에서 온 것만 본다 -- 호출한 셸의 환경 변수로 잘못 실패하지 않게.
# SHARED_DIR 도 values.env 에서 온 것만 쓴다(2026-11-03 리뷰): 프로브(shared-storage-probe.py)가 env SHARED_DIR 를 입력으로 받아
# 운영자 셸에 export 돼 있을 수 있다 -- 그대로 두면 SHARED_DIR 줄이 빠진 values.env 가 「미치환」 FAIL 대신 셸 값으로 렌더돼
# hostPath·base·allowlist 가 사이트 원천(values.env)에 없는 경로가 된다.
unset SHARED_FS SHARED_DIR
# shellcheck disable=SC1090
. "$VALS"

# 공용 디렉터리(2026-11-03 ③, 설계 v6 3-2·9 「③ 공유 FS 세부」). 옛 SHARED_FS(상대 이름, cephfs → file:///cephfs/dms/artifacts)
# 는 SHARED_DIR(절대 경로)로 바뀌었다. 남아 있으면 어느 쪽이 맞는지 추측하지 않고 멈춘다 -- 조용히 무시하면 SHARED_DIR 를 아직
# 안 채운 옛 values.env 가 다른 경로(또는 예시 값)로 렌더된다. 같은 base 를 유지하려면 SHARED_DIR=/<옛 값>.
# 안내가 「그 값을 그대로 쓰면 된다」로 끝나지 않는 이유(2026-11-03 리뷰): 옛 배치는 allowlist·api/controller hostPath 가 SHARED_FS
# 최상위 **전체**였다 -- (1) 포탈에서 그 아래 아무 경로나 base 로 저장(source=db)했을 수 있고, 새 배치에서 그 경로는 api·controller
# 에 안 보인다(잡 파드는 DB base 를 그대로 붙여 쓰므로 잡은 돌고 컨트롤러만 summary 를 못 읽는다). (2) ssc 계열 배치는 같은 최상위를
# 데이터 스토리지(mount_path)로도 등록했다 -- 그 최상위를 SHARED_DIR 로 두면 상태 디렉터리 <SHARED_DIR>/dms 를 그 스토리지가 덮어
# 잡 컨테이너에 보이고, 등록 가드는 경로를 바꿀 때만 걸려 드러나지 않는다(포탈 「아티팩트 경로」 화면이 경고로 싣는다).
if [ "${SHARED_FS+set}" = set ]; then
  _old=${SHARED_FS#/}
  echo "FAIL: values.env 의 SHARED_FS 는 더 쓰지 않습니다(2026-11-03) — 'SHARED_FS=$SHARED_FS' 줄을 지우고 SHARED_DIR=/${_old:-<공용 디렉터리>} 처럼 절대 경로로 쓰세요(아티팩트 base 는 그대로 <SHARED_DIR>/dms/artifacts, api/controller hostPath·allowlist 는 공용 디렉터리 전체에서 그 경로 하나로 좁아집니다)"
  echo "  그 값을 쓰기 전에 확인: (1) 포탈 「아티팩트 경로」 화면이 'DB 설정'이고 값이 file:///${_old:-<공용 디렉터리>}/dms/artifacts 가 아니면, 새 배치에서 그 경로는 api·controller 에 보이지 않습니다 — 산출물을 그 경로로 옮기고 base 를 다시 저장(force)한 뒤 적용하세요"
  echo "  (2) 등록 스토리지의 마운트 경로·관리 루트가 /${_old:-<공용 디렉터리>}/dms 를 덮으면(예: 마운트 경로 /${_old:-<공용 디렉터리>}) 그 스토리지를 쓰는 잡에 DMS 상태가 보입니다 — SHARED_DIR 는 그 스토리지 밖의 별도 마운트포인트로 고르세요(같은 파일시스템의 하위 디렉터리로는 피할 수 없습니다; 적용 뒤 「아티팩트 경로」 화면이 겹치는 스토리지를 경고로 보입니다)"
  echo "  (3) 이 렌더를 적용하면 api/controller hostPath 가 바뀌어 guard-images.sh 가 exit 5 로 멈춥니다 — 전환 런북(deploy/README.md: 노드 마운트·S0·shared-storage-probe.py·복사)을 마친 뒤 ALLOW_HOSTPATH_CHANGE=1 로 적용하세요"
  exit 2
fi
# SHARED_DIR 형식(설계 v6 3-2 -- shared-storage-probe.py 와 같은 규칙): 절대 경로, '/' 아님, 정규화(// · /./ · /../ · 끝 / 없음),
# 문자 ^/[A-Za-z0-9._/-]+$. 문자 규칙은 아래 sed 의 '|' 구분자·'&'(치환 특수 문자)·YAML 에서 안전한 것만 남긴다. 범위식(A-Z)은
# 로케일에 따라 다른 글자를 포함할 수 있어 글자를 나열한다. 빈 값·REPLACE_* 는 아래 KEYS 검사가 「미치환」으로 알린다.
_shared_dir_problem() {
  case "$1" in
    /*) ;;
    *) echo "절대 경로가 아님"; return ;;
  esac
  case "$1" in
    *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-]*)
      echo "허용 밖 문자(영문·숫자·. _ / - 만)"; return ;;
  esac
  case "$1" in
    */) echo "'/' 자체이거나 끝에 / 가 있음"; return ;;
    *//*|*/./*|*/../*|*/.|*/..) echo "정규화되지 않은 경로(// · /./ · /../)"; return ;;
  esac
}
case "${SHARED_DIR:-}" in
  ""|REPLACE_*) ;;
  *) _why=$(_shared_dir_problem "$SHARED_DIR")
     [ -z "$_why" ] || { echo "FAIL: values.env 의 SHARED_DIR='$SHARED_DIR' — $_why (예: SHARED_DIR=/srv/dms_shared)"; exit 2; } ;;
esac

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
  if [ -n "$CTX" ]; then
    command kubectl config get-contexts "$CTX" >/dev/null 2>&1 \
      || { echo "FAIL: 컨텍스트 '$CTX' 가 kubeconfig 에 없습니다" >&2; return 1; }
    _img=$(kubectl --context "$CTX" -n "$NS" get "$@" 2>/dev/null || true)
  else
    _img=$(kubectl -n "$NS" get "$@" 2>/dev/null || true)
  fi
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

# dms-k8s: k8s 신원 모드 전용(2026-10-26 LDAP 코드 삭제) -- LDAP 키 없음. 옛 values.env 의 LDAP_*·IDENTITY_SUPPLEMENTARY_GROUPS
# 는 쓰지 않는다(아래 알림 한 줄, stderr — stdout 의 마지막 `RENDERED:` 파싱을 흐리지 않게).
KEYS="REGISTRY DMS_TAG DMS_AGENT_TAG MFU_TAG SHARED_DIR EMAIL_DOMAIN LOCAL_ADMIN PORTAL_DOMAIN PORTAL_VIP"
bad=""
for k in $KEYS; do
  v=$(eval "printf '%s' \"\${$k:-}\"")
  case "$v" in ""|REPLACE_*) bad="$bad $k";; esac
done
[ -z "$bad" ] || { echo "FAIL: values.env 미치환/빈 값 -->$bad"; exit 2; }
if [ -n "${LDAP_HOST:-}${LDAP_USER_BASE:-}${LDAP_GROUP_BASE:-}${LDAP_BIND_DN:-}${IDENTITY_SUPPLEMENTARY_GROUPS:-}" ]; then
  echo "note: values.env 의 LDAP_*·IDENTITY_SUPPLEMENTARY_GROUPS 는 dms-k8s(k8s 신원 모드 전용)에서 쓰지 않는다 — 무시함" >&2
fi

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
    -e "s|REPLACE_SHARED_DIR|$SHARED_DIR|g" \
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
