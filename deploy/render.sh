#!/bin/sh
# 사이트 렌더러(2026-08-30 overlays/prod/render.sh → 2026-11-03 ④ 일반화 — 설계 v6 9 「③ 공유 FS 세부」(render.sh 일반화)·④·⑤·8-2
# 「층 배치」). 같은 렌더러가 앱 오버레이와 플랫폼 층(platform/{priorityclass,registry,db-*,gateway})·애드온 층(addons/{cnpg,gateway-api,
# envoy-gateway} — ⑥ 2026-11-03)을 렌더한다.
#
#   sh deploy/render.sh <site> [--context <ctx>]      # site = deploy/overlays/<site>(kustomization.yaml 이 있는 디렉터리 이름)
#
# 입력: values.env 하나(deploy/overlays/<site>/values.env — VALUES_ENV 로 바꿀 수 있다). 셸 환경의 같은 이름 변수는 읽지 않는다(아래 unset).
# 출력: deploy/.rendered/<site>/ (git 밖 — .gitignore)
#   app/                      앱 오버레이 → `kubectl --context <ctx> apply -k`(client-side — 포탈 릴리스가 strategic-merge 로 image 를 바꾼다).
#                             템플릿 오버레이(prod — REPLACE_* 토큰)는 값을 넣고, 고정 오버레이(testbed-k8s)는 그대로 복사한다. 둘 다
#                             resources 의 ../../k8s 를 이 깊이(../../../k8s)로 고쳐 같은 base 를 가리킨다.
#   platform/priorityclass/   PriorityClass dms-addon-critical → SSA(--server-side --field-manager=dms-install)
#   platform/registry/        클러스터 내 레지스트리(REGISTRY_CLUSTER_IP 가 있을 때만) → SSA. base(deploy/platform/registry)를 가리키고 사이트
#                             값(이미지 호스트·clusterIP·포트·hostPath·센티널 기대값·선택 requests)만 덮는다.
#   addons/cnpg/              CNPG 오퍼레이터(설계 6-1 A 경로, ⑤) → SSA. base(deploy/addons/cnpg/<CNPG_VERSION> — 상류 원본 + DMS 패치 층)를
#                             가리키고 이미지·OPERATOR_IMAGE_NAME·POSTGRES_IMAGE_NAME 을 lock digest 로 덮는다(S9).
#   platform/db-volumes/      SC dms-pg-shared + 정적 hostPath PV ×5(<SHARED_DIR>/dms/pgdata/dms-pgdata-0N, 용량 = PG_STORAGE_SIZE) → SSA(S10 첫째)
#   platform/db-cluster/      CNPG Cluster dms-pg(imageName = lock digest, storage.size = PV 용량, 선택 자원·배치) → SSA(S10, 사전 검사 뒤)
#   platform/db-backup/       pg_dump CronJob(이미지·hostPath <SHARED_DIR>/dms/backup/pg·보존 개수) → SSA(S10, 경로 대조 뒤)
#   platform/db-probe/        일회용 경로 프로브 DaemonSet — deploy/pg-path-probe.py 가 띄우고 지운다(S10 사전 검사·경로 대조)
#   addons/gateway-api/       Gateway API CRD v1.6.x standard(⑥, S8 첫째) → SSA. base = deploy/addons/gateway-api/<GATEWAY_API_VERSION>(상류 원본 + 라벨)
#   addons/envoy-gateway/     Envoy Gateway 컨트롤러(⑥, S8 둘째) → SSA → rollout. base = deploy/addons/envoy-gateway/<ENVOY_GATEWAY_VERSION>(상류 원본
#                             + DMS 패치 층) + 이미지(컨트롤러·certgen) lock digest + EG 설정(shutdown-manager·기본 Envoy 이미지)의 사이트 사본
#   platform/gateway/         포탈 입구(⑥, S8 셋째): GatewayClass·Gateway·HTTPRoute·CTP·BTP·EnvoyProxy → SSA. base(deploy/platform/gateway)를
#                             가리키고 Envoy·shutdown-manager 이미지·PROXY 모드·인증서 이름·선택 Envoy requests 만 덮는다(설계 4-3)
#   render.env                해석된 값(REGISTRY·앱 태그·애드온 레지스트리·이미지 ref 등 — install.sh 의 in-cluster 게이트와 런북이 읽는다)
#   (kube-context 가 있는 오버레이는 그 파일을 각 층 디렉터리에 복사한다 — guard-images.sh 가 같은 컨텍스트 고정을 본다)
# 성공하면 층마다 `PLATFORM: <디렉터리>`(db 층은 S10 순서)·`GATEWAY: <디렉터리>`(S8 순서 — 위 셋, ⑥)·`ADDON: <디렉터리>`·`PROBE: <디렉터리>` 를,
# 마지막 줄에 `RENDERED: <app 디렉터리>` 를 찍는다(install.sh 가 파싱). 실패는 한 줄 사유 + exit 2 이고 결과 디렉터리를 바꾸지 않는다(임시
# 디렉터리에 렌더한 뒤 끝에서만 바꿔 넣는다).
#
# DB(CNPG, 2026-11-03 ⑤ — 설계 v6 6·8-1 S9/S10). 버전은 deploy/versions.env(커밋 — CNPG_VERSION·CNPG_OPERATOR_IMAGE·PG_IMAGE), 이미지 digest 는
# deploy/images.lock(그 DEST 의 linux/amd64 매니페스트 digest)이 원천이다 — 이미지 ref = <애드온 레지스트리>/<DEST>@<digest>. 애드온 레지스트리 =
# 클러스터 내 레지스트리(REGISTRY_CLUSTER_IP 가 있으면 <IP>:<PORT> — S5 가 lock 이미지를 그리로 push 한다; 앱이 아직 external 이어도, 테스트베드
# T4 병행), 없으면 REGISTRY. DB 층은 늘 렌더한다(설계 ⑤: PG 는 늘 공용 경로 PV — 로컬 디스크 선택지 없음, v4 의 PG_STORAGE 키는 없앴다).
#   PG_STORAGE_SIZE    (선택, 기본 20Gi) Cluster storage.size == PV capacity — 한 값이 두 곳에 들어간다(명목값, hostPath 는 강제하지 않는다)
#   PG_CPU_REQUEST·PG_MEMORY·PG_SHARED_BUFFERS  (선택) 기본 1 / 4Gi(requests == 메모리 limit) / PG_MEMORY 의 1/4(둘 다 없으면 base 1GB) — 실효
#                      메모리의 절반 이상인 shared_buffers 는 FAIL. 테스트베드 100m / 256Mi / 64MB(설계 10)
#   PG_PLACEMENT       (선택) control-plane(기본 — 마스터마다 1개, #27) | all-nodes(테스트베드만 — nodeSelector 를 빼 cp1·w1·w2 에 하나씩)
#   PG_BACKUP_KEEP     (선택, 기본 336 = 14일 × 매시간) pg_dump 보존 개수
#
# 레지스트리(설계 v6 7, values 키):
#   REGISTRY_MODE        in-cluster(DMS 가 띄우는 레지스트리를 앱이 쓴다) | external(사이트 레지스트리 REGISTRY). 없으면 external(옛 values.env).
#   REGISTRY_CLUSTER_IP  클러스터 내 레지스트리 Service 의 고정 ClusterIP(IPv4). 있으면 platform/registry 를 렌더한다 — external 이어도
#                        (병행 기간: 레지스트리를 먼저 띄우고 앱은 나중에 옮긴다, 테스트베드 T4).
#   REGISTRY_PORT        Service 포트(기본 5000).
#   REGISTRY             앱이 쓰는 host:port(경로 접두 불가 — 7-2). in-cluster 면 <REGISTRY_CLUSTER_IP>:<REGISTRY_PORT> 와 같아야 하고, 비우면
#                        그 값으로 채운다 — 「같은 주소가 여섯 곳」(7-4: REGISTRY·newName·DMS_JOB_IMAGE·DMS_BUILD_REGISTRY·DMS_BUILD_BUILDER_IMAGE
#                        + DB control_state.job_image)의 원천이 이 한 값이다. 렌더 결과에서 다섯 곳이 같은지 다시 본다.
#   REGISTRY_TLS_VERIFY  in-cluster 면 false(평문 HTTP). install.sh 의 push 가 쓴다.
#   SHARED_DIR_ID        S0 가 쓴 <SHARED_DIR>/dms/.dms-shared-id 값 = 레지스트리 센티널(.dms-registry-id) 기대값(7-3). 레지스트리를 렌더할 때 필수.
#   REGISTRY_CPU_REQUEST·REGISTRY_MEMORY_REQUEST  (선택) 레지스트리 requests(기본 100m/128Mi — 테스트베드는 50m/64Mi, 설계 10).
#
# 입구(Envoy Gateway, 2026-11-03 ⑥ — 설계 v6 #13·#14·#15·4-3·8-1 S8). 버전은 deploy/versions.env(GATEWAY_API_VERSION·ENVOY_GATEWAY_VERSION·
# ENVOY_GATEWAY_IMAGE·ENVOY_PROXY_IMAGE), 이미지 digest 는 deploy/images.lock — CNPG 와 같은 애드온 레지스트리에서 받는다. 입구 층은 늘 렌더한다
# (prod 의 유일한 입구 — 앱 오버레이가 Ingress·MetalLB 풀을 지운다; testbed-k8s 는 T8 동안 Ingress 와 병행). values(전부 선택):
#   GATEWAY_PROXY_PROTOCOL  required(기본 — PROXY v2 헤더 없는 연결을 끊는다) | optional(**컷오버 중에만** — PROXY 없는 연결도 받는다, 설계 4-3).
#                           ClientTrafficPolicy proxyProtocol.optional 한 칸이 이 값에서 나온다(deploy/README §2g 컷오버).
#   PORTAL_TLS_SECRET       Gateway 리스너 인증서 Secret(ns dms, kubernetes.io/tls). 기본 dms-portal-tls(설계 #14).
#   GATEWAY_ENVOY_CPU_REQUEST·GATEWAY_ENVOY_MEMORY_REQUEST  Envoy requests(기본 100m/256Mi, limits 1/2Gi 는 base — 테스트베드 50m/128Mi, 설계 10).
#                           limits 를 넘으면 FAIL(EG 가 만드는 DaemonSet 을 apiserver 가 거절한다 — SSA 는 통과해 늦게 드러난다).
set -eu
usage() { echo "usage: $0 <site> [--context <ctx>]" >&2; exit 2; }
fail() { echo "FAIL: $*"; exit 2; }
# --context(2026-10-26): 'live' 태그를 읽을 클러스터(guard-images.sh 와 같은 문법 — --context <ctx> / --context=<ctx> /
# KUBE_CONTEXT, 플래그가 이긴다). 명시 태그만이면 kubectl 을 부르지 않는다(컨텍스트 존재 확인도 'live' 일 때만).
CTX="${KUBE_CONTEXT:-}"
SITE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --context) [ $# -ge 2 ] && [ -n "$2" ] || usage; CTX="$2"; shift ;;
    --context=*) CTX="${1#--context=}"; [ -n "$CTX" ] || usage ;;
    -*) usage ;;
    *) [ -z "$SITE" ] || usage; SITE=$1 ;;
  esac
  shift
done
[ -n "$SITE" ] || usage
case "$SITE" in *[!abcdefghijklmnopqrstuvwxyz0123456789-]*|-*) usage ;; esac
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)     # deploy
OV="$HERE/overlays/$SITE"
[ -f "$OV/kustomization.yaml" ] || fail "오버레이 없음: $OV/kustomization.yaml (사이트 = deploy/overlays 아래 디렉터리 이름)"
OUT="$HERE/.rendered/$SITE"
VALS="${VALUES_ENV:-$OV/values.env}"
if [ ! -f "$VALS" ]; then
  if [ -f "$OV/values.env.example" ]; then
    fail "values.env 없음 — 'cp $OV/values.env.example $OV/values.env' 후 값을 채우세요"
  fi
  fail "values.env 없음: $VALS"
fi
# 옛 키 SHARED_FS(2026-11-03 ③ 에 SHARED_DIR 로 바뀜)는 values.env 에서 온 것만 본다 -- 호출한 셸의 환경 변수로 잘못 실패하지 않게.
# SHARED_DIR 도 values.env 에서 온 것만 쓴다(2026-11-03 리뷰): 프로브(shared-storage-probe.py)가 env SHARED_DIR 를 입력으로 받아
# 운영자 셸에 export 돼 있을 수 있다 -- 그대로 두면 SHARED_DIR 줄이 빠진 values.env 가 「미치환」 FAIL 대신 셸 값으로 렌더돼
# hostPath·base·allowlist 가 사이트 원천(values.env)에 없는 경로가 된다. 레지스트리 키(④)도 같은 이유로 values.env 만 원천이다.
# 템플릿 키(태그·도메인·VIP — 2026-11-03 ④ 리뷰)도 같다: prod README 1단계가 `PORTAL_VIP=… sh preflight-cluster.sh` 처럼 같은 이름을
# env 로 쓰게 하므로 셸에 남아 있을 수 있다 — 그대로 두면 줄이 빠진 values.env 가 「미치환」 FAIL 대신 셸 값으로 렌더된다. 옛 LDAP 키도
# (아래 「무시함」 알림이 values.env 에서 온 것만 말하게). 의도된 env 입력은 VALUES_ENV·KUBE_CONTEXT·DMS_NS 뿐이다.
unset SHARED_FS SHARED_DIR SHARED_DIR_ID REGISTRY REGISTRY_MODE REGISTRY_CLUSTER_IP REGISTRY_PORT REGISTRY_TLS_VERIFY \
      REGISTRY_CPU_REQUEST REGISTRY_MEMORY_REQUEST \
      PG_STORAGE_SIZE PG_CPU_REQUEST PG_MEMORY PG_SHARED_BUFFERS PG_PLACEMENT PG_BACKUP_KEEP \
      CNPG_VERSION CNPG_OPERATOR_IMAGE PG_IMAGE \
      GATEWAY_PROXY_PROTOCOL PORTAL_TLS_SECRET GATEWAY_ENVOY_CPU_REQUEST GATEWAY_ENVOY_MEMORY_REQUEST \
      GATEWAY_API_VERSION ENVOY_GATEWAY_VERSION ENVOY_GATEWAY_IMAGE ENVOY_PROXY_IMAGE \
      DMS_TAG DMS_AGENT_TAG MFU_TAG EMAIL_DOMAIN LOCAL_ADMIN PORTAL_DOMAIN PORTAL_VIP \
      LDAP_HOST LDAP_USER_BASE LDAP_GROUP_BASE LDAP_BIND_DN IDENTITY_SUPPLEMENTARY_GROUPS
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

# 템플릿 오버레이(값을 넣는 REPLACE_* 토큰이 코드 줄에 있음 — prod) vs 고정 오버레이(그대로 쓰는 커밋된 값 — testbed-k8s).
TEMPLATE=0
if grep -h -v '^[[:space:]]*#' "$OV"/*.yaml | grep -q 'REPLACE_'; then TEMPLATE=1; fi

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
if [ "$TEMPLATE" = 1 ]; then
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
fi

# --- 레지스트리(2026-11-03 ④, 설계 v6 7) -----------------------------------------------------------------------------------
_set() { case "${1:-}" in ""|REPLACE_*) return 1 ;; esac; return 0; }
case "${REGISTRY_MODE:-}" in
  "") REGISTRY_MODE=external
      echo "note: values.env 에 REGISTRY_MODE 가 없다 — external(사이트 레지스트리 REGISTRY)로 본다. 클러스터 내 레지스트리는 REGISTRY_MODE=in-cluster(deploy/README §2e)" >&2 ;;
  in-cluster|external) ;;
  *) fail "values.env 의 REGISTRY_MODE='$REGISTRY_MODE' — in-cluster 또는 external" ;;
esac
_set "${REGISTRY_PORT:-}" || REGISTRY_PORT=5000
RCIP=""
if _set "${REGISTRY_CLUSTER_IP:-}"; then RCIP=$REGISTRY_CLUSTER_IP; fi
# in-cluster 에서 REGISTRY 를 비우면 레지스트리 주소로 채운다(같은 주소의 원천을 하나로). 채웠으면 아래에서 같은지 본다.
if [ "$REGISTRY_MODE" = in-cluster ] && ! _set "${REGISTRY:-}" && [ -n "$RCIP" ]; then
  REGISTRY="$RCIP:$REGISTRY_PORT"
  echo "info: REGISTRY = 클러스터 내 레지스트리 주소 $REGISTRY" >&2
fi

# dms-k8s: k8s 신원 모드 전용(2026-10-26 LDAP 코드 삭제) -- LDAP 키 없음. 옛 values.env 의 LDAP_*·IDENTITY_SUPPLEMENTARY_GROUPS
# 는 쓰지 않는다(아래 알림 한 줄, stderr — stdout 의 마지막 `RENDERED:` 파싱을 흐리지 않게).
# 템플릿 오버레이(prod)의 필수 키 -- 템플릿의 REPLACE_<키> 와 같은 집합(어긋나면 아래 잔여 토큰 그물이 잡는다). 예외 둘(2026-11-03 ⑥):
# PORTAL_DOMAIN·PORTAL_VIP 는 prod 가 Ingress·MetalLB 풀을 지워 템플릿에 더는 없지만 사이트 사실(포탈 FQDN·Avi VIP-A — 인증서 SAN 과 S12
# `curl --resolve` 의 대상)이라 필수로 두고 render.env 로만 낸다(Gateway 리스너는 hostname 이 없다 — 설계 #14).
KEYS="REGISTRY DMS_TAG DMS_AGENT_TAG MFU_TAG SHARED_DIR EMAIL_DOMAIN LOCAL_ADMIN PORTAL_DOMAIN PORTAL_VIP"
# 고정 오버레이(testbed-k8s)는 값이 오버레이에 커밋돼 있다 -- values.env 는 그 기록이고 렌더러가 아래에서 둘이 같은지 본다.
[ "$TEMPLATE" = 1 ] || KEYS="REGISTRY SHARED_DIR"
[ "$REGISTRY_MODE" = in-cluster ] && KEYS="$KEYS REGISTRY_CLUSTER_IP"
if [ "$REGISTRY_MODE" = in-cluster ] || [ -n "$RCIP" ]; then KEYS="$KEYS SHARED_DIR_ID"; fi
# in-cluster 에서 REGISTRY 를 비웠으면 원천은 REGISTRY_CLUSTER_IP 다 — REGISTRY 를 미치환으로 지목하지 않는다(2026-11-03 ④ 리뷰 2: 비워 두라던
# 값을 채우게 만들고, 채우면 다음 렌더가 「레지스트리 주소와 다르다」로 또 막힌다). 위에서 RCIP 로 채웠으면 이미 값이 있다.
if [ "$REGISTRY_MODE" = in-cluster ] && ! _set "${REGISTRY:-}"; then KEYS=${KEYS#REGISTRY }; fi
bad=""
for k in $KEYS; do
  v=$(eval "printf '%s' \"\${$k:-}\"")
  case "$v" in ""|REPLACE_*) bad="$bad $k";; esac
done
if [ -n "$bad" ]; then
  echo "FAIL: values.env 미치환/빈 값 -->$bad"
  # 값을 어디서 얻는지(2026-11-03 ④ 리뷰 2) — 사람이 옮겨 적는 두 키만. 나머지는 values.env.example 주석에 있다.
  for k in $bad; do
    case "$k" in
      REGISTRY_CLUSTER_IP) echo "  REGISTRY_CLUSTER_IP — ServiceCIDR 안의 미사용 IP(예: 10.96.0.50). 고르는 법·확인 명령은 values.env.example 주석(deploy/README §2e-1)" ;;
      SHARED_DIR_ID) echo "  SHARED_DIR_ID — S0 출력의 「dms/.dms-shared-id = …」(이미 있었으면 「이미 있음(= …)」), 또는 아무 노드에서 sudo cat <SHARED_DIR>/dms/.dms-shared-id" ;;
    esac
  done
  exit 2
fi
if [ -n "${LDAP_HOST:-}${LDAP_USER_BASE:-}${LDAP_GROUP_BASE:-}${LDAP_BIND_DN:-}${IDENTITY_SUPPLEMENTARY_GROUPS:-}" ]; then
  echo "note: values.env 의 LDAP_*·IDENTITY_SUPPLEMENTARY_GROUPS 는 dms-k8s(k8s 신원 모드 전용)에서 쓰지 않는다 — 무시함" >&2
fi

# 형식. 글자를 나열한다(범위식은 로케일 따라 다른 글자를 품는다). 아래 값은 sed 의 '|' 구분자·YAML·sh 에 그대로 들어간다.
_ipv4_problem() {
  case "$1" in ""|*[!0123456789.]*) echo "IPv4 점 표기가 아님"; return ;; esac
  _rest=$1; _n=0; _first=""
  while :; do
    _oct=${_rest%%.*}
    case "$_oct" in ""|0?*) echo "옥텟이 비었거나 0 으로 시작함"; return ;; esac
    [ "${#_oct}" -le 3 ] && [ "$_oct" -le 255 ] || { echo "옥텟이 255 를 넘음"; return; }
    [ -n "$_first" ] || _first=$_oct
    _n=$((_n + 1))
    case "$_rest" in *.*) _rest=${_rest#*.} ;; *) break ;; esac
  done
  [ "$_n" = 4 ] || { echo "옥텟이 4개가 아님"; return; }
  if [ "$_first" = 0 ] || [ "$_first" = 127 ] || [ "$_first" -ge 224 ]; then echo "Service 주소로 쓸 수 없는 대역(0·127·224 이상)"; fi
}
if [ -n "$RCIP" ]; then
  _why=$(_ipv4_problem "$RCIP")
  # 렌더러는 형식만 본다. ServiceCIDR 안·미사용·하단 정적 대역은 지금 손으로 확인한다(values.env.example 주석의 명령 — S1 자동 검사는 ⑦).
  [ -z "$_why" ] || fail "values.env 의 REGISTRY_CLUSTER_IP='$RCIP' — $_why (예: 10.96.0.50 — ServiceCIDR 안·미사용·하단 정적 대역인지는 values.env.example 주석의 명령으로 손으로 확인)"
fi
case "$REGISTRY_PORT" in
  ""|0*|*[!0123456789]*) fail "values.env 의 REGISTRY_PORT='$REGISTRY_PORT' — 1..65535 숫자(기본 5000)" ;;
esac
[ "${#REGISTRY_PORT}" -le 5 ] && [ "$REGISTRY_PORT" -le 65535 ] || fail "values.env 의 REGISTRY_PORT='$REGISTRY_PORT' — 1..65535"
# REGISTRY 는 host:port 만(경로 접두 불가 — registry.py·manifest_tags 가 첫 조각만 호스트로 본다, 설계 7-2).
case "$REGISTRY" in
  *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.:-]*|:*|*:|*:*:*)
    fail "values.env 의 REGISTRY='$REGISTRY' — host 또는 host:port 만(경로·스킴 없이, 예: registry.corp.example:5000)" ;;
esac
if [ "$REGISTRY_MODE" = in-cluster ] && [ "$REGISTRY" != "$RCIP:$REGISTRY_PORT" ]; then
  fail "REGISTRY_MODE=in-cluster 인데 REGISTRY='$REGISTRY' 가 레지스트리 주소 $RCIP:$REGISTRY_PORT 와 다르다 — REGISTRY 를 비우거나 같은 값으로(같은 주소가 여섯 곳에 들어간다, 설계 v6 7-4)"
fi
case "${REGISTRY_TLS_VERIFY:-}" in
  ""|false) ;;
  true) [ "$REGISTRY_MODE" != in-cluster ] || fail "REGISTRY_MODE=in-cluster 는 평문 HTTP 다 — REGISTRY_TLS_VERIFY=false" ;;
  *) fail "values.env 의 REGISTRY_TLS_VERIFY='$REGISTRY_TLS_VERIFY' — true 또는 false" ;;
esac
[ "$REGISTRY_MODE" != in-cluster ] || REGISTRY_TLS_VERIFY=false
if [ -n "$RCIP" ]; then
  case "$SHARED_DIR_ID" in
    *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]*)
      fail "values.env 의 SHARED_DIR_ID='$SHARED_DIR_ID' — S0 의 <SHARED_DIR>/dms/.dms-shared-id 값(영문·숫자·. _ - 만)" ;;
  esac
  [ "${#SHARED_DIR_ID}" -le 128 ] || fail "values.env 의 SHARED_DIR_ID 가 128자를 넘는다"
  # (선택) requests: CPU = <숫자>[m], 메모리 = <숫자>Ki|Mi|Gi(앞자리 0 없음).
  if [ -n "${REGISTRY_CPU_REQUEST:-}" ]; then
    _num=${REGISTRY_CPU_REQUEST%m}
    case "$_num" in ""|0*|*[!0123456789]*) fail "values.env 의 REGISTRY_CPU_REQUEST='$REGISTRY_CPU_REQUEST' — 예: 50m" ;; esac
  fi
  if [ -n "${REGISTRY_MEMORY_REQUEST:-}" ]; then
    case "$REGISTRY_MEMORY_REQUEST" in
      *Ki|*Mi|*Gi) _num=${REGISTRY_MEMORY_REQUEST%??} ;;
      *) _num="" ;;
    esac
    case "$_num" in ""|0*|*[!0123456789]*) fail "values.env 의 REGISTRY_MEMORY_REQUEST='$REGISTRY_MEMORY_REQUEST' — 예: 64Mi(Ki·Mi·Gi)" ;; esac
  fi
fi
LAYERS="priorityclass"
[ -z "$RCIP" ] || LAYERS="$LAYERS registry"
for layer in $LAYERS; do
  [ -f "$HERE/platform/$layer/kustomization.yaml" ] || fail "플랫폼 base 없음: $HERE/platform/$layer/kustomization.yaml"
done

# --- DB(CNPG) 값(2026-11-03 ⑤, 설계 v6 6·8-1 S9/S10 — 머리 주석 「DB」) ------------------------------------------------------------
# 버전·digest 의 원천은 커밋된 두 파일이다(values.env 가 아니다 — 사이트마다 다르면 안 되는 값). versions.env 는 sh 로 . 하지 않고 `KEY=값` 줄만
# 읽는다(같은 키가 둘이면 FAIL — 어느 쪽인지 추측하지 않는다).
VERS="$HERE/versions.env"
LOCKF="$HERE/images.lock"
[ -f "$VERS" ] || fail "버전 고정 파일 없음: $VERS (설계 6-5 — 커밋된 deploy/versions.env)"
[ -f "$LOCKF" ] || fail "이미지 lock 없음: $LOCKF"
_ver() {
  _n=$(grep -c "^$1=" "$VERS" || true)
  [ "$_n" = 1 ] || { echo "FAIL: $VERS 에 $1= 줄이 정확히 하나가 아니다($_n개)"; return 1; }
  sed -n "s/^$1=//p" "$VERS"
}
CNPG_VERSION=$(_ver CNPG_VERSION) || { echo "$CNPG_VERSION"; exit 2; }
CNPG_OPERATOR_IMAGE=$(_ver CNPG_OPERATOR_IMAGE) || { echo "$CNPG_OPERATOR_IMAGE"; exit 2; }
PG_IMAGE=$(_ver PG_IMAGE) || { echo "$PG_IMAGE"; exit 2; }
case "$CNPG_VERSION" in
  ""|.*|*.|*..*|*[!0123456789.]*) fail "$VERS 의 CNPG_VERSION='$CNPG_VERSION' — 예: 1.30.1" ;;
esac
for _img in "$CNPG_OPERATOR_IMAGE" "$PG_IMAGE"; do
  case "$_img" in
    *[!abcdefghijklmnopqrstuvwxyz0123456789._:/-]*|*:*:*|:*|*:|*/|/*) fail "$VERS 의 이미지 '$_img' — lock 의 DEST(<이름>:<태그>, 호스트 없이)" ;;
    *:*) ;;
    *) fail "$VERS 의 이미지 '$_img' 에 태그가 없다 — lock 의 DEST(<이름>:<태그>)" ;;
  esac
done
[ "${CNPG_OPERATOR_IMAGE##*:}" = "$CNPG_VERSION" ] \
  || fail "$VERS 의 CNPG_OPERATOR_IMAGE='$CNPG_OPERATOR_IMAGE' 태그가 CNPG_VERSION='$CNPG_VERSION' 과 다르다(한 커밋에서 같이 올린다)"
CNPG_BASE="$HERE/addons/cnpg/$CNPG_VERSION"
[ -f "$CNPG_BASE/kustomization.yaml" ] || fail "애드온 base 없음: $CNPG_BASE/kustomization.yaml (CNPG_VERSION=$CNPG_VERSION — deploy/addons/cnpg/<v>)"
grep -q "^      dms.io/addon-version: \"$CNPG_VERSION\"\$" "$CNPG_BASE/kustomization.yaml" \
  || fail "$CNPG_BASE/kustomization.yaml 의 라벨 dms.io/addon-version 이 CNPG_VERSION='$CNPG_VERSION' 이 아니다"
for layer in db/volumes db/cluster db/backup db/probe; do
  [ -f "$HERE/platform/$layer/kustomization.yaml" ] || fail "플랫폼 base 없음: $HERE/platform/$layer/kustomization.yaml"
done
# lock 에서 DEST 의 digest — 첫 칸이 #(주석)인 줄은 건너뛰고(sh 소비자와 같은 규칙), 정확히 한 줄이어야 한다.
_lock_digest() {
  _dg=$(awk -v d="$1" '$1 !~ /^#/ && $1 == d { print $3; n++ } END { exit (n == 1 ? 0 : 1) }' "$LOCKF") \
    || { echo "FAIL: $LOCKF 에 DEST '$1' 줄이 정확히 하나가 아니다 — versions.env 와 lock 을 한 커밋에서(images-lock.py resolve)"; return 1; }
  case "$_dg" in
    sha256:????????????????????????????????????????????????????????????????) ;;
    *) echo "FAIL: $LOCKF 의 '$1' digest '$_dg' 가 sha256:<64 hex> 가 아니다"; return 1 ;;
  esac
  case "${_dg#sha256:}" in *[!0123456789abcdef]*) echo "FAIL: $LOCKF 의 '$1' digest '$_dg' 가 소문자 hex 가 아니다"; return 1 ;; esac
  printf '%s' "$_dg"
}
OP_DG=$(_lock_digest "$CNPG_OPERATOR_IMAGE") || { echo "$OP_DG"; exit 2; }
PG_DG=$(_lock_digest "$PG_IMAGE") || { echo "$PG_DG"; exit 2; }
if [ -n "$RCIP" ]; then ADDON_REGISTRY="$RCIP:$REGISTRY_PORT"; else ADDON_REGISTRY=$REGISTRY; fi
OP_REF="$ADDON_REGISTRY/$CNPG_OPERATOR_IMAGE@$OP_DG"
PG_REF="$ADDON_REGISTRY/$PG_IMAGE@$PG_DG"
# 사이트 값(values.env, 전부 선택). 형식은 글자를 나열해 본다(아래 값은 YAML·JSON 패치에 그대로 들어간다).
_qty() {   # $1=키 $2=값 $3=허용 단위(공백 구분) $4=예
  for _u in $3; do
    case "$2" in
      *"$_u")
        _num=${2%"$_u"}
        case "$_num" in ""|0*|*[!0123456789]*) ;; *) return 0 ;; esac ;;
    esac
  done
  fail "values.env 의 $1='$2' — 예: $4($3)"
}
_set "${PG_STORAGE_SIZE:-}" || PG_STORAGE_SIZE=20Gi
_qty PG_STORAGE_SIZE "$PG_STORAGE_SIZE" "Mi Gi Ti" 20Gi
if _set "${PG_CPU_REQUEST:-}"; then
  _num=${PG_CPU_REQUEST%m}
  case "$_num" in ""|0*|*[!0123456789]*) fail "values.env 의 PG_CPU_REQUEST='$PG_CPU_REQUEST' — 예: 1 또는 100m" ;; esac
else PG_CPU_REQUEST=""; fi
if _set "${PG_MEMORY:-}"; then _qty PG_MEMORY "$PG_MEMORY" "Mi Gi" 4Gi; else PG_MEMORY=""; fi
if _set "${PG_SHARED_BUFFERS:-}"; then _qty PG_SHARED_BUFFERS "$PG_SHARED_BUFFERS" "kB MB GB" 1GB; else PG_SHARED_BUFFERS=""; fi
# shared_buffers 는 메모리 limit 안에서 채워진다(2026-11-03 ⑤ 리뷰 2): PG_MEMORY 만 줄이고 base 의 1GB 를 남기면 부하 때 cgroup OOM 으로 인스턴스가
# 재기동을 반복한다(렌더는 통과했다). 그래서 PG_SHARED_BUFFERS 가 없고 PG_MEMORY 가 있으면 그 1/4(base 의 4Gi → 1GB 와 같은 비율 — PG 검토 5-2)을
# 쓰고, 실효 메모리(PG_MEMORY, 없으면 base 의 4Gi)의 절반 이상이면 멈춘다. PG 의 kB·MB·GB 는 1024 배수라 Ki·Mi·Gi 와 같은 단위로 센다.
_kib() {   # $1 = 위에서 형식을 본 수량 → KiB. 9자리를 넘으면 실패(셸 산술 범위)
  case "$1" in
    *Gi|*GB) _n=${1%??}; _m=1048576 ;;
    *Mi|*MB) _n=${1%??}; _m=1024 ;;
    *kB) _n=${1%??}; _m=1 ;;
    *) return 1 ;;
  esac
  [ "${#_n}" -le 9 ] || return 1
  echo $((_n * _m))
}
PG_MEM_EFF=${PG_MEMORY:-4Gi}
_mem_k=$(_kib "$PG_MEM_EFF") || fail "values.env 의 PG_MEMORY='$PG_MEMORY' 가 너무 크다(9자리 이하)"
if [ -z "$PG_SHARED_BUFFERS" ] && [ -n "$PG_MEMORY" ]; then
  _sb_k=$((_mem_k / 4))
  if [ $((_sb_k % 1048576)) = 0 ]; then PG_SHARED_BUFFERS="$((_sb_k / 1048576))GB"
  elif [ $((_sb_k % 1024)) = 0 ]; then PG_SHARED_BUFFERS="$((_sb_k / 1024))MB"
  else PG_SHARED_BUFFERS="${_sb_k}kB"; fi
  echo "info: PG_SHARED_BUFFERS = PG_MEMORY($PG_MEMORY)/4 = $PG_SHARED_BUFFERS" >&2
fi
if [ -n "$PG_SHARED_BUFFERS" ]; then
  _sb_k=$(_kib "$PG_SHARED_BUFFERS") || fail "values.env 의 PG_SHARED_BUFFERS='$PG_SHARED_BUFFERS' 가 너무 크다(9자리 이하)"
  [ "$((_sb_k * 2))" -lt "$_mem_k" ] \
    || fail "values.env 의 PG_SHARED_BUFFERS='$PG_SHARED_BUFFERS' 가 메모리 limit $PG_MEM_EFF(PG_MEMORY)의 절반 이상이다 — shared_buffers 는 그 limit 안에서 채워져 부하 때 cgroup OOM 으로 재기동을 반복한다(비우면 PG_MEMORY/4)"
fi
_set "${PG_PLACEMENT:-}" || PG_PLACEMENT=control-plane
case "$PG_PLACEMENT" in
  control-plane|all-nodes) ;;
  *) fail "values.env 의 PG_PLACEMENT='$PG_PLACEMENT' — control-plane(기본, 마스터마다 1개) 또는 all-nodes(테스트베드)" ;;
esac
_set "${PG_BACKUP_KEEP:-}" || PG_BACKUP_KEEP=336
case "$PG_BACKUP_KEEP" in ""|0*|*[!0123456789]*) fail "values.env 의 PG_BACKUP_KEEP='$PG_BACKUP_KEEP' — 양의 정수(기본 336 = 14일 × 매시간)" ;; esac
[ "${#PG_BACKUP_KEEP}" -le 5 ] || fail "values.env 의 PG_BACKUP_KEEP='$PG_BACKUP_KEEP' 가 너무 크다(5자리 이하)"
DB_LAYERS="db-volumes db-cluster db-backup"

# --- 입구(Envoy Gateway) 값(2026-11-03 ⑥, 설계 v6 #13·#14·4-3·8-1 S8 — 머리 주석 「입구」) -------------------------------------------------------
# 버전·digest 의 원천은 CNPG 와 같은 두 커밋 파일이다. 디렉터리 이름 = 상류 태그(v 접두 — 이미지 태그도 v1.9.3 이라 태그 == 버전 검사가 그대로 선다).
GATEWAY_API_VERSION=$(_ver GATEWAY_API_VERSION) || { echo "$GATEWAY_API_VERSION"; exit 2; }
ENVOY_GATEWAY_VERSION=$(_ver ENVOY_GATEWAY_VERSION) || { echo "$ENVOY_GATEWAY_VERSION"; exit 2; }
ENVOY_GATEWAY_IMAGE=$(_ver ENVOY_GATEWAY_IMAGE) || { echo "$ENVOY_GATEWAY_IMAGE"; exit 2; }
ENVOY_PROXY_IMAGE=$(_ver ENVOY_PROXY_IMAGE) || { echo "$ENVOY_PROXY_IMAGE"; exit 2; }
for _v in "GATEWAY_API_VERSION=$GATEWAY_API_VERSION" "ENVOY_GATEWAY_VERSION=$ENVOY_GATEWAY_VERSION"; do
  _n=${_v#*=v}
  case "$_v" in *=v[0123456789]*) ;; *) fail "$VERS 의 ${_v%%=*}='${_v#*=}' — 상류 태그(예: v1.9.3)" ;; esac
  case "$_n" in ""|.*|*.|*..*|*[!0123456789.]*) fail "$VERS 의 ${_v%%=*}='${_v#*=}' — 상류 태그(예: v1.9.3)" ;; esac
done
for _img in "$ENVOY_GATEWAY_IMAGE" "$ENVOY_PROXY_IMAGE"; do
  case "$_img" in
    *[!abcdefghijklmnopqrstuvwxyz0123456789._:/-]*|*:*:*|:*|*:|*/|/*) fail "$VERS 의 이미지 '$_img' — lock 의 DEST(<이름>:<태그>, 호스트 없이)" ;;
    *:*) ;;
    *) fail "$VERS 의 이미지 '$_img' 에 태그가 없다 — lock 의 DEST(<이름>:<태그>)" ;;
  esac
done
[ "${ENVOY_GATEWAY_IMAGE##*:}" = "$ENVOY_GATEWAY_VERSION" ] \
  || fail "$VERS 의 ENVOY_GATEWAY_IMAGE='$ENVOY_GATEWAY_IMAGE' 태그가 ENVOY_GATEWAY_VERSION='$ENVOY_GATEWAY_VERSION' 과 다르다(한 커밋에서 같이 올린다)"
GWAPI_BASE="$HERE/addons/gateway-api/$GATEWAY_API_VERSION"
EG_BASE="$HERE/addons/envoy-gateway/$ENVOY_GATEWAY_VERSION"
GW_BASE="$HERE/platform/gateway"
for _b in "$GWAPI_BASE=$GATEWAY_API_VERSION" "$EG_BASE=$ENVOY_GATEWAY_VERSION"; do
  [ -f "${_b%=*}/kustomization.yaml" ] || fail "애드온 base 없음: ${_b%=*}/kustomization.yaml (deploy/versions.env 의 버전 — deploy/addons/<애드온>/<v>)"
  grep -q "^      dms.io/addon-version: \"${_b##*=}\"\$" "${_b%=*}/kustomization.yaml" \
    || fail "${_b%=*}/kustomization.yaml 의 라벨 dms.io/addon-version 이 '${_b##*=}' 가 아니다"
done
[ -f "$GW_BASE/kustomization.yaml" ] || fail "플랫폼 base 없음: $GW_BASE/kustomization.yaml"
# 이미지 자리표시자는 글자 그대로 바꾼다(EG 설정 문자열 안에도 있다 — kustomize images 가 못 닿는다). base 의 자리표시자 태그가 versions.env 와
# 다르면 치환이 조용히 무동작이 되므로 여기서 멈춘다(설정·EnvoyProxy 두 파일 — 업그레이드 때 같이 바꾼다).
for _f in "$EG_BASE/patch-config.yaml" "$GW_BASE/envoyproxy.yaml"; do
  for _img in "$ENVOY_GATEWAY_IMAGE" "$ENVOY_PROXY_IMAGE"; do
    grep -qF "image: set-by-overlay.invalid/$_img" "$_f" \
      || fail "$_f 에 자리표시자 'set-by-overlay.invalid/$_img' 가 없다 — versions.env 와 base 의 태그를 한 커밋에서 맞춘다"
  done
done
EG_DG=$(_lock_digest "$ENVOY_GATEWAY_IMAGE") || { echo "$EG_DG"; exit 2; }
ENVOY_DG=$(_lock_digest "$ENVOY_PROXY_IMAGE") || { echo "$ENVOY_DG"; exit 2; }
EG_REF="$ADDON_REGISTRY/$ENVOY_GATEWAY_IMAGE@$EG_DG"
ENVOY_REF="$ADDON_REGISTRY/$ENVOY_PROXY_IMAGE@$ENVOY_DG"
_set "${GATEWAY_PROXY_PROTOCOL:-}" || GATEWAY_PROXY_PROTOCOL=required
case "$GATEWAY_PROXY_PROTOCOL" in
  required) PROXY_OPTIONAL=false ;;
  optional) PROXY_OPTIONAL=true
            echo "note: GATEWAY_PROXY_PROTOCOL=optional — PROXY 헤더 없는 연결도 받는다(원 IP 자리에 직접 피어). **컷오버 중에만** 쓰고 끝나면 required 로 되돌린다(설계 4-3, deploy/README §2g)" >&2 ;;
  *) fail "values.env 의 GATEWAY_PROXY_PROTOCOL='$GATEWAY_PROXY_PROTOCOL' — required(기본) 또는 optional(컷오버 중에만)" ;;
esac
_set "${PORTAL_TLS_SECRET:-}" || PORTAL_TLS_SECRET=dms-portal-tls
# Secret 이름 = DNS-1123 subdomain(소문자·숫자·. -, 점으로 나눈 라벨마다 처음·끝은 영숫자, 253자 이하).
case "$PORTAL_TLS_SECRET" in
  *[!abcdefghijklmnopqrstuvwxyz0123456789.-]*|[.-]*|*[.-]|*..*|*.-*|*-.*) fail "values.env 의 PORTAL_TLS_SECRET='$PORTAL_TLS_SECRET' — Secret 이름(소문자·숫자·. -, 예: dms-portal-tls)" ;;
esac
[ "${#PORTAL_TLS_SECRET}" -le 253 ] || fail "values.env 의 PORTAL_TLS_SECRET 이 253자를 넘는다"
# Envoy requests 는 base 의 limits(cpu 1·memory 2Gi — platform/gateway/envoyproxy.yaml, 설계 #14) 이하여야 한다(2026-11-03 ⑥ 리뷰 1): EnvoyProxy
# CRD 에는 requests ≤ limits 규칙이 없어 SSA 는 통과하고, EG 가 만드는 DaemonSet 을 apiserver 가 거절해 Gateway 가 뜨지 않는다(첫 설치 — 마스터
# :443 이 빈다) 또는 옛 spec 에 머문다(재적용) — 원인은 EG 로그에만 남는다. 레지스트리·PG 와 달리 server dry-run 이 못 잡으니 여기서 멈춘다.
# base limits 를 바꾸면 이 두 상한도 같이 바꾼다(tests/test_gateway_contract.py 가 base limits 를 1/2Gi 로 고정한다).
if _set "${GATEWAY_ENVOY_CPU_REQUEST:-}"; then
  _num=${GATEWAY_ENVOY_CPU_REQUEST%m}
  case "$_num" in ""|0*|*[!0123456789]*) fail "values.env 의 GATEWAY_ENVOY_CPU_REQUEST='$GATEWAY_ENVOY_CPU_REQUEST' — 예: 100m 또는 1" ;; esac
  [ "${#_num}" -le 9 ] || fail "values.env 의 GATEWAY_ENVOY_CPU_REQUEST='$GATEWAY_ENVOY_CPU_REQUEST' 가 너무 크다(9자리 이하)"
  case "$GATEWAY_ENVOY_CPU_REQUEST" in *m) _mcpu=$_num ;; *) _mcpu=$((_num * 1000)) ;; esac
  [ "$_mcpu" -le 1000 ] \
    || fail "values.env 의 GATEWAY_ENVOY_CPU_REQUEST='$GATEWAY_ENVOY_CPU_REQUEST' 가 base limits(cpu 1 — 설계 #14)를 넘는다 — EG 가 만드는 DaemonSet 을 apiserver 가 거절해 Gateway 가 뜨지 않는다"
else GATEWAY_ENVOY_CPU_REQUEST=""; fi
if _set "${GATEWAY_ENVOY_MEMORY_REQUEST:-}"; then
  _qty GATEWAY_ENVOY_MEMORY_REQUEST "$GATEWAY_ENVOY_MEMORY_REQUEST" "Mi Gi" 256Mi
  _env_k=$(_kib "$GATEWAY_ENVOY_MEMORY_REQUEST") || fail "values.env 의 GATEWAY_ENVOY_MEMORY_REQUEST='$GATEWAY_ENVOY_MEMORY_REQUEST' 가 너무 크다(9자리 이하)"
  [ "$_env_k" -le 2097152 ] \
    || fail "values.env 의 GATEWAY_ENVOY_MEMORY_REQUEST='$GATEWAY_ENVOY_MEMORY_REQUEST' 가 base limits(memory 2Gi — 설계 #14)를 넘는다 — EG 가 만드는 DaemonSet 을 apiserver 가 거절해 Gateway 가 뜨지 않는다"
else GATEWAY_ENVOY_MEMORY_REQUEST=""; fi
GATEWAY_LAYERS="addons/gateway-api addons/envoy-gateway platform/gateway"

# --- 렌더(임시 디렉터리 — 끝에서만 OUT 을 바꾼다) ----------------------------------------------------------------------------
mkdir -p "$HERE/.rendered"
TMP="$HERE/.rendered/.$SITE.tmp.$$"
trap 'rm -rf "$TMP"' EXIT
trap 'exit 2' INT TERM HUP
rm -rf "$TMP"; mkdir -p "$TMP/app"
for f in "$OV"/*.yaml; do cp "$f" "$TMP/app/"; done
[ ! -f "$OV/kube-context" ] || cp "$OV/kube-context" "$TMP/app/"
# 긴 토큰(REPLACE_DMS_AGENT_TAG)을 짧은 토큰(REPLACE_DMS_TAG)보다 먼저 치환한다
# (여기선 서로 접두관계가 아니지만 방어적으로). 구분자는 | -- 값에 / : , 가 있어도 안전.
if [ "$TEMPLATE" = 1 ]; then
  for f in "$TMP"/app/*.yaml; do
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
fi
# base 참조를 이 깊이로: deploy/overlays/<site>/ 의 ../../k8s == deploy/.rendered/<site>/app/ 의 ../../../k8s.
sed -i 's|^\([[:space:]]*-[[:space:]]*\)\.\./\.\./k8s[[:space:]]*$|\1../../../k8s|' "$TMP/app/kustomization.yaml"
grep -q '^[[:space:]]*-[[:space:]]*\.\./\.\./\.\./k8s$' "$TMP/app/kustomization.yaml" \
  || fail "$OV/kustomization.yaml 의 resources 에 '- ../../k8s' 가 없다(오버레이 모양이 바뀌었다 — 렌더러를 같이 고친다)"

# 잔여 토큰 그물: 값은 다 채웠어도 템플릿에 새 REPLACE_ 가 생기면(스키마 변경)
# 여기서 잡아 apply 전에 시끄럽게 실패시킨다. 주석 줄(# 로 시작)은 제외한다 --
# 문서/주석이 "REPLACE_" 를 문자 그대로 언급해도 실제 미치환 토큰은 아니다.
leftover=$(grep -rn "REPLACE_" "$TMP/app" 2>/dev/null | grep -v ':[[:space:]]*#' || true)
if [ -n "$leftover" ]; then
  echo "FAIL: 렌더 후에도 REPLACE_ 토큰 남음(템플릿에 새 자리표시자?):"
  printf '%s\n' "$leftover" | sed "s|$TMP/app/|  |"
  exit 2
fi

# 렌더 결과 대조(값의 원천 하나 — 템플릿은 구성상 같고, 고정 오버레이는 values.env 기록과 어긋나면 여기서 멈춘다).
_cfg() { sed -n "s/^  $1:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$TMP/app/patch-config.yaml" | head -n 1; }
[ -f "$TMP/app/patch-config.yaml" ] || fail "$OV/patch-config.yaml 이 없다 — 레지스트리·아티팩트 값을 덮는 패치가 필요하다"
want_base="file://$SHARED_DIR/dms/artifacts"
got_base=$(_cfg DMS_ARTIFACT_BASE_URI)
[ "$got_base" = "$want_base" ] \
  || fail "$OV/patch-config.yaml 의 DMS_ARTIFACT_BASE_URI='$got_base' 가 values.env 의 SHARED_DIR 로 낸 '$want_base' 와 다르다 — 둘을 같이 고친다"
names=$(sed -n 's/^[[:space:]]*newName:[[:space:]]*\([^[:space:]]*\).*/\1/p' "$TMP/app/kustomization.yaml")
[ -n "$names" ] || fail "$OV/kustomization.yaml 에 images.newName 이 없다"
for n in $names; do
  [ "${n%%/*}" = "$REGISTRY" ] || fail "오버레이 이미지 newName '$n' 의 레지스트리가 REGISTRY='$REGISTRY' 와 다르다(같은 주소가 여섯 곳 — 설계 v6 7-4)"
done
job_image=$(_cfg DMS_JOB_IMAGE); build_registry=$(_cfg DMS_BUILD_REGISTRY); builder=$(_cfg DMS_BUILD_BUILDER_IMAGE)
for pair in "DMS_JOB_IMAGE=$job_image" "DMS_BUILD_REGISTRY=$build_registry" "DMS_BUILD_BUILDER_IMAGE=$builder"; do
  [ -n "${pair#*=}" ] || fail "$OV/patch-config.yaml 이 ${pair%%=*} 를 덮지 않는다 — base 의 값(다른 사이트 레지스트리)이 배포된다"
done
[ "${job_image%%/*}" = "$REGISTRY" ] || fail "DMS_JOB_IMAGE='$job_image' 의 레지스트리가 REGISTRY='$REGISTRY' 와 다르다"
[ "$build_registry" = "$REGISTRY" ] || fail "DMS_BUILD_REGISTRY='$build_registry' 가 REGISTRY='$REGISTRY' 와 다르다"
if [ "${builder%%/*}" != "$REGISTRY" ]; then
  # external 에서는 빌더 미러만 다른 레지스트리일 수 있다(테스트베드: 공용 :5000 미러). in-cluster 는 노드가 그 레지스트리만 믿으므로 같아야 한다.
  [ "$REGISTRY_MODE" != in-cluster ] || fail "DMS_BUILD_BUILDER_IMAGE='$builder' 의 레지스트리가 REGISTRY='$REGISTRY' 와 다르다(in-cluster — images.lock 의 buildah 를 그 레지스트리에 push)"
  echo "note: DMS_BUILD_BUILDER_IMAGE='$builder' 는 REGISTRY 와 다른 레지스트리다(external — 빌더 미러)" >&2
fi

# --- 플랫폼 층 --------------------------------------------------------------------------------------------------------------
HEAD="# deploy/render.sh $SITE 가 만든 파일 — 손으로 고치지 않는다(deploy/overlays/$SITE/values.env 를 고치고 다시 렌더)."
mkdir -p "$TMP/platform/priorityclass"
cat > "$TMP/platform/priorityclass/kustomization.yaml" <<EOF
$HEAD
# 적용: kubectl --context <ctx> apply --server-side --field-manager=dms-install -k <이 디렉터리>
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../../platform/priorityclass
EOF
if [ -n "$RCIP" ]; then
  mkdir -p "$TMP/platform/registry"
  cat > "$TMP/platform/registry/kustomization.yaml" <<EOF
$HEAD
# 적용: kubectl --context <ctx> apply --server-side --field-manager=dms-install -k <이 디렉터리>(Secret dms-registry-http 는 먼저 — deploy/README §2e)
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../../platform/registry
# 레지스트리 자기 이미지 = <REGISTRY_CLUSTER_IP>:<REGISTRY_PORT>/registry:<base 의 태그>(태그 ref — 마스터에 반입·고정한 이름, 설계 7-5 ②).
images:
  - name: set-by-overlay.invalid/registry
    newName: $RCIP:$REGISTRY_PORT/registry
configMapGenerator:
  - name: dms-registry-config
    namespace: dms
    behavior: merge
    literals:
      - expected-registry-id=$SHARED_DIR_ID
    options:
      disableNameSuffixHash: true
patches:
  - path: site-patch.yaml
  - target:
      kind: Service
      name: dms-registry
    patch: |-
      - op: replace
        path: /spec/clusterIP
        value: $RCIP
      - op: replace
        path: /spec/ports/0/port
        value: $REGISTRY_PORT
EOF
  # 설정 체크섬(2026-11-03 ④ 리뷰 2): Distribution 은 config.yml 을, init 은 expected-registry-id(configMapKeyRef env)를 **기동 때만**
  # 읽는다. ConfigMap 은 이름이 고정이라(GC 런북용 — 해시 접미 없음) 설정만 바뀐 SSA 재적용은 파드를 다시 띄우지 않고 옛 설정으로 계속
  # 돈다 — 그 차이는 다음 우연한 재시작 때 드러난다. 두 입력의 sha256 을 파드 템플릿 annotation 에 넣어 설정이 바뀌면 롤링 교체되게
  # 한다(maxSurge 1 — 공백 없음; 빌드·push 가 없을 때 적용). 손으로 고친 ConfigMap(GC 길 B)을 SSA 로 되돌릴 때는 렌더값이 그대로라
  # 이 값도 그대로다 — 그때는 rollout restart(deploy/README §2e-5).
  command -v sha256sum >/dev/null 2>&1 || fail "sha256sum 이 없다(coreutils) — 레지스트리 설정 체크섬에 쓴다"
  CFG_SUM=$({ cat "$HERE/platform/registry/config.yml"; printf 'expected-registry-id=%s\n' "$SHARED_DIR_ID"; } | sha256sum | cut -c1-64)
  case "$CFG_SUM" in *[!0123456789abcdef]*|"") fail "레지스트리 설정 체크섬을 만들지 못했다" ;; esac
  [ "${#CFG_SUM}" = 64 ] || fail "레지스트리 설정 체크섬을 만들지 못했다"
  {
    echo "$HEAD"
    echo "# 저장소 = <SHARED_DIR>/dms/registry(S0 가 65532:65532 0750 + 센티널로 만든다), type Directory 그대로."
    echo "# dms.io/config-sha256 = sha256(config.yml + expected-registry-id) — 설정이 바뀌면 파드가 새로 뜬다(기동 때만 읽힌다)."
    echo "apiVersion: apps/v1"
    echo "kind: Deployment"
    echo "metadata:"
    echo "  name: dms-registry"
    echo "  namespace: dms"
    echo "spec:"
    echo "  template:"
    echo "    metadata:"
    echo "      annotations:"
    echo "        dms.io/config-sha256: \"$CFG_SUM\""
    echo "    spec:"
    if [ -n "${REGISTRY_CPU_REQUEST:-}${REGISTRY_MEMORY_REQUEST:-}" ]; then
      echo "      containers:"
      echo "        - name: registry"
      echo "          resources:"
      echo "            requests:"
      [ -z "${REGISTRY_CPU_REQUEST:-}" ] || echo "              cpu: $REGISTRY_CPU_REQUEST"
      [ -z "${REGISTRY_MEMORY_REQUEST:-}" ] || echo "              memory: $REGISTRY_MEMORY_REQUEST"
    fi
    echo "      volumes:"
    echo "        - name: registry-data"
    echo "          hostPath:"
    echo "            path: $SHARED_DIR/dms/registry"
    echo "            type: Directory"
  } > "$TMP/platform/registry/site-patch.yaml"
fi

# --- 애드온 층: CNPG 오퍼레이터(2026-11-03 ⑤, 설계 6-1 airgap 함정 1·3) -------------------------------------------------------------
# image 와 env OPERATOR_IMAGE_NAME 을 **한 변수(OP_REF)에서** 쓴다 — 둘이 어긋나면 PG 인스턴스 파드의 bootstrap init 이 다른 이미지(또는 ghcr.io)를
# 받으러 간다. POSTGRES_IMAGE_NAME = Cluster imageName 과 같은 PG_REF(imageName 을 생략한 Cluster 도 ghcr.io 로 가지 않게).
mkdir -p "$TMP/addons/cnpg"
cat > "$TMP/addons/cnpg/kustomization.yaml" <<EOF
$HEAD
# 적용(S9): kubectl --context <ctx> apply --server-side --field-manager=dms-install -k <이 디렉터리> → rollout status → Cluster CR server dry-run
# (deploy/README §2f-3). base = deploy/addons/cnpg/$CNPG_VERSION(상류 원본 + DMS 패치 층).
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../../addons/cnpg/$CNPG_VERSION
patches:
  - path: site-patch.yaml
EOF
cat > "$TMP/addons/cnpg/site-patch.yaml" <<EOF
$HEAD
# 이미지 = <애드온 레지스트리>/<lock DEST>@<amd64 매니페스트 digest>(deploy/versions.env + deploy/images.lock).
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cnpg-controller-manager
  namespace: cnpg-system
spec:
  template:
    spec:
      containers:
        - name: manager
          image: $OP_REF
          env:
            - name: OPERATOR_IMAGE_NAME
              value: $OP_REF
            - name: POSTGRES_IMAGE_NAME
              value: $PG_REF
EOF

# --- 입구 층(2026-11-03 ⑥, 설계 4-3·8-1 S8 — 순서: gateway-api → envoy-gateway(rollout) → platform/gateway) ----------------------------------------
mkdir -p "$TMP/addons/gateway-api" "$TMP/addons/envoy-gateway" "$TMP/platform/gateway"
cat > "$TMP/addons/gateway-api/kustomization.yaml" <<EOF
$HEAD
# 적용(S8 첫째): kubectl --context <ctx> apply --server-side --field-manager=dms-install -k <이 디렉터리> → CRD Established 대기(deploy/README §2g).
# base = deploy/addons/gateway-api/$GATEWAY_API_VERSION(상류 standard 채널 원본 + 라벨). 사이트 값이 없다.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../../addons/gateway-api/$GATEWAY_API_VERSION
EOF
cat > "$TMP/addons/envoy-gateway/kustomization.yaml" <<EOF
$HEAD
# 적용(S8 둘째): kubectl --context <ctx> apply --server-side --field-manager=dms-install -k <이 디렉터리> → rollout status deploy/envoy-gateway
# (ns envoy-gateway-system) → certgen Job 완료(deploy/README §2g). base = deploy/addons/envoy-gateway/$ENVOY_GATEWAY_VERSION(상류 원본 + DMS 패치 층).
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../../addons/envoy-gateway/$ENVOY_GATEWAY_VERSION
patches:
  - path: site-patch.yaml
  - path: site-config.yaml
EOF
cat > "$TMP/addons/envoy-gateway/site-patch.yaml" <<EOF
$HEAD
# 이미지 = <애드온 레지스트리>/<lock DEST>@<amd64 매니페스트 digest>(deploy/versions.env + deploy/images.lock) — 컨트롤러와 certgen Job 이 같은 이미지다.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: envoy-gateway
  namespace: envoy-gateway-system
spec:
  template:
    spec:
      containers:
        - name: envoy-gateway
          image: $EG_REF
---
apiVersion: batch/v1
kind: Job
metadata:
  name: eg-gateway-helm-certgen
  namespace: envoy-gateway-system
spec:
  template:
    spec:
      containers:
        - name: envoy-gateway-certgen
          image: $EG_REF
EOF
# EG 설정(ConfigMap)은 base 의 patch-config.yaml 을 원천으로 두 자리표시자만 글자 그대로 바꾼 사본이다(설정 문자열 안 — kustomize images 가 못
# 닿는다). 점은 정규식 메타라 이스케이프한다. 바꾼 뒤 자리표시자가 남으면 멈춘다(base 와 versions.env 를 위에서 대조했지만 그물 한 겹 더).
_re() { printf '%s' "$1" | sed 's/[.]/\\./g'; }
{
  echo "$HEAD"
  echo "# EnvoyGateway 설정 = deploy/addons/envoy-gateway/$ENVOY_GATEWAY_VERSION/patch-config.yaml 의 이미지 두 줄을 lock digest ref 로 바꾼 것(기동 때만"
  echo "# 읽힌다 — 바뀌면 kubectl --context <ctx> -n envoy-gateway-system rollout restart deploy/envoy-gateway, deploy/README §2g)."
  sed -e '/^#/d' \
      -e "s|set-by-overlay\\.invalid/$(_re "$ENVOY_GATEWAY_IMAGE")\$|$EG_REF|" \
      -e "s|set-by-overlay\\.invalid/$(_re "$ENVOY_PROXY_IMAGE")\$|$ENVOY_REF|" \
      "$EG_BASE/patch-config.yaml"
} > "$TMP/addons/envoy-gateway/site-config.yaml"
! grep -q 'set-by-overlay' "$TMP/addons/envoy-gateway/site-config.yaml" \
  || fail "EG 설정 사본에 자리표시자가 남았다 — $EG_BASE/patch-config.yaml 의 이미지 줄 모양(\`image: set-by-overlay.invalid/<DEST>\`)을 확인"
# platform/gateway: JSON 패치(EnvoyProxy·정책은 CRD 라 전략적 머지의 목록 병합 키가 없다 — 경로로 정확히 바꾼다). shutdown-manager 는 패치 목록의
# 둘째 항목이다 — test op 가 이름을 확인해 base 의 순서가 바뀌면 렌더(kustomize)가 실패한다.
{
  echo "$HEAD"
  echo "# 적용(S8 셋째, addons/envoy-gateway 가 Ready 된 뒤): kubectl --context <ctx> apply --server-side --field-manager=dms-install -k <이 디렉터리>"
  echo "# → Gateway dms-portal Programmed 대기 → **마스터마다** \`ss -ltnp\` 에 :443(Programmed 는 bind 실패를 못 잡는다 — deploy/README §2g). PROXY 모드 = $GATEWAY_PROXY_PROTOCOL."
  echo "apiVersion: kustomize.config.k8s.io/v1beta1"
  echo "kind: Kustomization"
  echo "resources:"
  echo "  - ../../../../platform/gateway"
  echo "patches:"
  echo "  - target:"
  echo "      group: gateway.envoyproxy.io"
  echo "      kind: EnvoyProxy"
  echo "      name: dms-portal"
  echo "    patch: |-"
  echo "      - op: replace"
  echo "        path: /spec/provider/kubernetes/envoyDaemonSet/container/image"
  echo "        value: $ENVOY_REF"
  echo "      - op: test"
  echo "        path: /spec/provider/kubernetes/envoyDaemonSet/patch/value/spec/template/spec/containers/1/name"
  echo "        value: shutdown-manager"
  echo "      - op: replace"
  echo "        path: /spec/provider/kubernetes/envoyDaemonSet/patch/value/spec/template/spec/containers/1/image"
  echo "        value: $EG_REF"
  if [ -n "$GATEWAY_ENVOY_CPU_REQUEST" ]; then
    echo "      - op: replace"
    echo "        path: /spec/provider/kubernetes/envoyDaemonSet/container/resources/requests/cpu"
    echo "        value: \"$GATEWAY_ENVOY_CPU_REQUEST\""
  fi
  if [ -n "$GATEWAY_ENVOY_MEMORY_REQUEST" ]; then
    echo "      - op: replace"
    echo "        path: /spec/provider/kubernetes/envoyDaemonSet/container/resources/requests/memory"
    echo "        value: $GATEWAY_ENVOY_MEMORY_REQUEST"
  fi
  echo "  - target:"
  echo "      group: gateway.envoyproxy.io"
  echo "      kind: ClientTrafficPolicy"
  echo "      name: dms-portal"
  echo "    patch: |-"
  echo "      - op: replace"
  echo "        path: /spec/proxyProtocol/optional"
  echo "        value: $PROXY_OPTIONAL"
  echo "  - target:"
  echo "      group: gateway.networking.k8s.io"
  echo "      kind: Gateway"
  echo "      name: dms-portal"
  echo "    patch: |-"
  echo "      - op: test"
  echo "        path: /spec/listeners/0/name"
  echo "        value: https"
  echo "      - op: replace"
  echo "        path: /spec/listeners/0/tls/certificateRefs/0/name"
  echo "        value: $PORTAL_TLS_SECRET"
} > "$TMP/platform/gateway/kustomization.yaml"

# --- DB 층(2026-11-03 ⑤, 설계 6-1·6-2·6-4·8-1 S10) — base(deploy/platform/db/*)를 가리키는 kustomization + 사이트 값 ----------------------
PGDATA_DIR="$SHARED_DIR/dms/pgdata"
mkdir -p "$TMP/platform/db-volumes" "$TMP/platform/db-cluster" "$TMP/platform/db-backup" "$TMP/platform/db-probe"
cat > "$TMP/platform/db-volumes/kustomization.yaml" <<EOF
$HEAD
# 적용(S10 첫째): python3 deploy/pg-path-probe.py … --mode pv-check(이미 있는 PV 의 경로·용량·SC 대조 — 용량·SC 는 SSA 가 조용히 바꾸므로
# 적용 전에) → SSA → … --mode precheck(사전 검사) → db-cluster.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../../platform/db/volumes
patches:
  - path: site-patch.yaml
EOF
{
  echo "$HEAD"
  echo "# PV 다섯 개의 경로(<SHARED_DIR>/dms/pgdata/dms-pgdata-0N — S0 가 26:26 0700 + .dms-pv-id 로 만든다)와 용량(= Cluster storage.size)."
  for n in 01 02 03 04 05; do
    [ "$n" = 01 ] || echo "---"
    echo "apiVersion: v1"
    echo "kind: PersistentVolume"
    echo "metadata:"
    echo "  name: dms-pgdata-$n"
    echo "spec:"
    echo "  capacity:"
    echo "    storage: $PG_STORAGE_SIZE"
    echo "  hostPath:"
    echo "    path: $PGDATA_DIR/dms-pgdata-$n"
  done
} > "$TMP/platform/db-volumes/site-patch.yaml"
{
  echo "$HEAD"
  echo "# 적용(S10): SSA --dry-run=server(웹훅 9443 경로) → SSA → 900초 폴링(readyInstances ≥ 1·dms-pg-rw Endpoints·currentPrimary Ready —"
  echo "# wait --for=condition=Ready 가 아니다) → python3 deploy/pg-path-probe.py … --mode contrast(경로 대조) → db-backup. deploy/README §2f-4."
  echo "apiVersion: kustomize.config.k8s.io/v1beta1"
  echo "kind: Kustomization"
  echo "resources:"
  echo "  - ../../../../platform/db/cluster"
  echo "patches:"
  echo "  - target:"
  echo "      group: postgresql.cnpg.io"
  echo "      kind: Cluster"
  echo "      name: dms-pg"
  echo "    patch: |-"
  echo "      - op: replace"
  echo "        path: /spec/imageName"
  echo "        value: $PG_REF"
  echo "      - op: replace"
  echo "        path: /spec/storage/size"
  echo "        value: $PG_STORAGE_SIZE"
  if [ -n "$PG_CPU_REQUEST" ]; then
    echo "      - op: replace"
    echo "        path: /spec/resources/requests/cpu"
    echo "        value: \"$PG_CPU_REQUEST\""
  fi
  if [ -n "$PG_MEMORY" ]; then
    # 메모리는 requests == limits(설계 2-1 — limit 만 있고 CPU limit 은 없다).
    echo "      - op: replace"
    echo "        path: /spec/resources/requests/memory"
    echo "        value: $PG_MEMORY"
    echo "      - op: replace"
    echo "        path: /spec/resources/limits/memory"
    echo "        value: $PG_MEMORY"
  fi
  if [ -n "$PG_SHARED_BUFFERS" ]; then
    echo "      - op: replace"
    echo "        path: /spec/postgresql/parameters/shared_buffers"
    echo "        value: $PG_SHARED_BUFFERS"
  fi
  if [ "$PG_PLACEMENT" = all-nodes ]; then
    # 테스트베드만(설계 10 T6 — cp1 하나뿐인 control-plane): nodeSelector 를 빼 모든 노드에 하나씩. toleration(cp1 taint)·required
    # anti-affinity 는 그대로 둔다 — 노드마다 1개는 지킨다. prod 는 늘 control-plane(마스터마다 1개, #27).
    echo "      - op: remove"
    echo "        path: /spec/affinity/nodeSelector"
  fi
} > "$TMP/platform/db-cluster/kustomization.yaml"
cat > "$TMP/platform/db-backup/kustomization.yaml" <<EOF
$HEAD
# 적용(S10, 경로 대조 뒤): SSA → kubectl --context <ctx> -n dms create job --from=cronjob/dms-pg-dump dms-pg-dump-first(첫 덤프).
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../../platform/db/backup
patches:
  - path: site-patch.yaml
EOF
cat > "$TMP/platform/db-backup/site-patch.yaml" <<EOF
$HEAD
# 덤프 위치 = <SHARED_DIR>/dms/backup/pg(S0 가 26:26 0700 + .dms-backup-id 로 만든다), 보존 = PG_BACKUP_KEEP 개.
apiVersion: batch/v1
kind: CronJob
metadata:
  name: dms-pg-dump
  namespace: dms
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: pg-dump
              image: $PG_REF
              env:
                - name: DMS_BACKUP_KEEP
                  value: "$PG_BACKUP_KEEP"
          volumes:
            - name: backup
              hostPath:
                path: $SHARED_DIR/dms/backup/pg
EOF
cat > "$TMP/platform/db-probe/kustomization.yaml" <<EOF
$HEAD
# 일회용(S10 사전 검사·경로 대조): python3 deploy/pg-path-probe.py --context <ctx> --rendered <이 디렉터리> --mode precheck|contrast
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../../platform/db/probe
patches:
  - path: site-patch.yaml
EOF
{
  echo "$HEAD"
  echo "apiVersion: apps/v1"
  echo "kind: DaemonSet"
  echo "metadata:"
  echo "  name: dms-pg-path-probe"
  echo "  namespace: dms"
  echo "spec:"
  echo "  template:"
  echo "    spec:"
  echo "      containers:"
  echo "        - name: probe"
  echo "          image: $PG_REF"
  echo "      volumes:"
  for n in 01 02 03 04 05; do
    echo "        - name: pv$n"
    echo "          hostPath:"
    echo "            path: $PGDATA_DIR/dms-pgdata-$n"
  done
} > "$TMP/platform/db-probe/site-patch.yaml"

if [ -f "$OV/kube-context" ]; then
  for layer in $LAYERS $DB_LAYERS db-probe; do cp "$OV/kube-context" "$TMP/platform/$layer/"; done
  cp "$OV/kube-context" "$TMP/addons/cnpg/"
  for layer in $GATEWAY_LAYERS; do cp "$OV/kube-context" "$TMP/$layer/"; done
fi
{
  echo "# deploy/render.sh $SITE 의 해석값 — 손으로 고치지 않는다(설치기·런북이 읽는다; sh 로 . 해도 된다)."
  echo "SITE=$SITE"
  echo "REGISTRY_MODE=$REGISTRY_MODE"
  echo "REGISTRY=$REGISTRY"
  echo "REGISTRY_CLUSTER_IP=$RCIP"
  echo "REGISTRY_PORT=$REGISTRY_PORT"
  echo "REGISTRY_TLS_VERIFY=${REGISTRY_TLS_VERIFY:-}"
  echo "SHARED_DIR=$SHARED_DIR"
  # 앱 이미지 태그의 해석값('live' 면 클러스터에서 읽은 태그) — install.sh 의 in-cluster 게이트가 레지스트리에 있는지 본다(④ 리뷰 2).
  # 고정 오버레이(testbed-k8s)는 태그가 오버레이에 있어 비어 있다.
  echo "DMS_TAG=${DMS_TAG:-}"
  echo "DMS_AGENT_TAG=${DMS_AGENT_TAG:-}"
  echo "MFU_TAG=${MFU_TAG:-}"
  echo "PLATFORM_LAYERS=\"$LAYERS\""
  # DB(CNPG, ⑤) — 설치 단계별: 애드온(S9) · DB 층(S10 순서) · 일회용 경로 프로브. 이미지 ref 는 lock digest 를 담은 전체 값이다.
  echo "ADDON_LAYERS=\"cnpg\""
  echo "DB_LAYERS=\"$DB_LAYERS\""
  echo "DB_PROBE=db-probe"
  echo "ADDON_REGISTRY=$ADDON_REGISTRY"
  echo "CNPG_VERSION=$CNPG_VERSION"
  echo "CNPG_OPERATOR_REF=$OP_REF"
  echo "PG_IMAGE_REF=$PG_REF"
  echo "PG_STORAGE_SIZE=$PG_STORAGE_SIZE"
  echo "PG_PLACEMENT=$PG_PLACEMENT"
  echo "PG_BACKUP_KEEP=$PG_BACKUP_KEEP"
  # 입구(⑥) — S8 순서의 층(렌더 디렉터리 기준 상대 경로)·버전·이미지 ref·사이트 값. PORTAL_DOMAIN·PORTAL_VIP 는 템플릿 오버레이(prod)만 있다
  # (S12 `curl --resolve <PORTAL_DOMAIN>:443:<PORTAL_VIP>` 의 대상 — 인증서 SAN 도 이 둘).
  echo "GATEWAY_LAYERS=\"$GATEWAY_LAYERS\""
  echo "GATEWAY_API_VERSION=$GATEWAY_API_VERSION"
  echo "ENVOY_GATEWAY_VERSION=$ENVOY_GATEWAY_VERSION"
  echo "ENVOY_GATEWAY_REF=$EG_REF"
  echo "ENVOY_PROXY_REF=$ENVOY_REF"
  echo "GATEWAY_PROXY_PROTOCOL=$GATEWAY_PROXY_PROTOCOL"
  echo "PORTAL_TLS_SECRET=$PORTAL_TLS_SECRET"
  echo "PORTAL_DOMAIN=${PORTAL_DOMAIN:-}"
  echo "PORTAL_VIP=${PORTAL_VIP:-}"
} > "$TMP/render.env"

rm -rf "$OUT"
mv "$TMP" "$OUT"
trap - EXIT
for layer in $LAYERS; do echo "PLATFORM: $OUT/platform/$layer"; done
for layer in $GATEWAY_LAYERS; do echo "GATEWAY: $OUT/$layer"; done
echo "ADDON: $OUT/addons/cnpg"
for layer in $DB_LAYERS; do echo "PLATFORM: $OUT/platform/$layer"; done
echo "PROBE: $OUT/platform/db-probe"
echo "RENDERED: $OUT/app"
