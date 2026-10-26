#!/bin/sh
# DMS 설치 후 검증 — PASS/FAIL 한 줄씩(2026-08-30, preflight-cluster.sh 문법 미러).
# 설치 에이전트가 kubectl 을 여러 번 탐색하는 대신 한 번 돌려 컴팩트한 판정을
# 읽게 한다. rc 는 FAIL 0 일 때만 0.
#
# 사용:  PORTAL_DOMAIN=dms.corp.example CACERT=/path/ca.crt sh deploy/verify.sh [--context <ctx>]
#   PORTAL_DOMAIN 없으면 HTTPS 검사는 건너뛴다(클러스터 워크로드만 검증).
set -u
# --context(2026-10-26): 대상 클러스터를 현재 컨텍스트 대신 이름으로 고른다(guard-images.sh 와 같은 문법 — --context <ctx> /
# --context=<ctx> / 환경변수 KUBE_CONTEXT, 플래그가 이긴다). 두 테스트베드가 같은 네임스페이스라 current-context 에 기대지 않는다.
CTX="${KUBE_CONTEXT:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --context) [ $# -ge 2 ] && [ -n "$2" ] || { echo "usage: $0 [--context <ctx>]" >&2; exit 2; }; CTX="$2"; shift ;;
    --context=*) CTX="${1#--context=}"; [ -n "$CTX" ] || { echo "usage: $0 [--context <ctx>]" >&2; exit 2; } ;;
    *) echo "usage: $0 [--context <ctx>]" >&2; exit 2 ;;
  esac
  shift
done
if [ -n "$CTX" ]; then
  command kubectl config get-contexts "$CTX" >/dev/null 2>&1 \
    || { echo "컨텍스트 '$CTX' 가 kubeconfig 에 없습니다" >&2; exit 2; }
  # 이 스크립트의 모든 kubectl 호출이 그 컨텍스트로 간다(호출 지점은 그대로).
  kubectl() { command kubectl --context "$CTX" "$@"; }
fi

NS="${DMS_NS:-dms}"
PORTAL="${PORTAL_DOMAIN:-}"
CACERT="${CACERT:-}"
p=0; f=0
P(){ printf '  [PASS] %s\n' "$1"; p=$((p+1)); }
F(){ printf '  [FAIL] %s\n' "$1"; f=$((f+1)); }
I(){ printf '  [INFO] %s\n' "$1"; }
H(){ printf '\n== %s ==\n' "$1"; }

H "1. 마이그레이션 / 워크로드"
mig=$(kubectl -n "$NS" get job dms-migrate -o jsonpath='{.status.succeeded}' 2>/dev/null || echo 0)
[ "${mig:-0}" = "1" ] && P "migrate 완료" || F "migrate 미완료 (succeeded=${mig:-0})"
for d in dms-api dms-controller; do
  ready=$(kubectl -n "$NS" get deploy "$d" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
  want=$(kubectl -n "$NS" get deploy "$d" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0)
  [ -n "${ready:-}" ] && [ "${ready:-0}" = "${want:-0}" ] && [ "${want:-0}" != "0" ] \
    && P "$d 롤아웃 ${ready}/${want}" || F "$d 롤아웃 ${ready:-0}/${want:-0}"
done
ar=$(kubectl -n "$NS" get ds dms-agent -o jsonpath='{.status.numberReady}' 2>/dev/null || echo 0)
aw=$(kubectl -n "$NS" get ds dms-agent -o jsonpath='{.status.desiredNumberScheduled}' 2>/dev/null || echo 0)
[ -n "${ar:-}" ] && [ "${ar:-0}" = "${aw:-0}" ] && [ "${aw:-0}" != "0" ] \
  && P "dms-agent ${ar}/${aw}" || F "dms-agent ${ar:-0}/${aw:-0}"

H "2. 이미지 드리프트 (live == manifest)"
# live 워크로드 이미지가 배포 매니페스트 태그와 같은지 -- 다르면 롤아웃이
# 반쪽만 됐거나 apply 가 덜 됐다는 신호(포탈 대시보드 드리프트 배지와 같은 사실).
drift=0
for kind_name in "deploy/dms-api" "deploy/dms-controller" "daemonset/dms-agent"; do
  img=$(kubectl -n "$NS" get "$kind_name" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "?")
  I "$kind_name → $img"
done

H "3. 포탈 (HTTPS · readyz 는 DB 까지 확인)"
if [ -n "$PORTAL" ]; then
  cc=""; [ -n "$CACERT" ] && cc="--cacert $CACERT"
  # /readyz 는 DB 쿼리까지 하는 실검사다. /healthz·임의 경로는 SPA 폴백으로 200 이
  # 떠 거짓 통과가 되므로(구 README §6 의 함정) readyz 로 검증한다.
  code=$(curl -s $cc -m5 -o /dev/null -w '%{http_code}' "https://$PORTAL/readyz" 2>/dev/null || echo 000)
  [ "$code" = "200" ] && P "readyz 200 (DB 도달)" || F "readyz $code (PORTAL_DOMAIN/CACERT/DB 확인)"
  rc=$(curl -s $cc -m5 -o /dev/null -w '%{http_code}' "http://$PORTAL/readyz" 2>/dev/null || echo 000)
  case "$rc" in 301|302|307|308) P "http→https 리다이렉트 ($rc)";; *) I "http readyz $rc (리다이렉트 미확인)";; esac
else
  I "PORTAL_DOMAIN 미설정 — HTTPS 검증 건너뜀 (포트포워드로 /readyz 를 직접 확인 가능)"
fi

printf '\n== 요약 ==  PASS=%d  FAIL=%d\n' "$p" "$f"
[ "$f" -eq 0 ]
