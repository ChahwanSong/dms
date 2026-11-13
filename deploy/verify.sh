#!/bin/sh
# DMS 설치 후 검증 — PASS/WARN/FAIL 한 줄씩(2026-08-30, preflight-cluster.sh 문법 미러 → 2026-11-03 ⑦ 설계 v6 8-1 S12 로 넓힘).
# 설치 에이전트가 kubectl 을 여러 번 탐색하는 대신 한 번 돌려 컴팩트한 판정을 읽게 한다. rc 는 FAIL 0 일 때만 0. install.sh 의 S12 가 부른다.
#
# 사용:  sh deploy/verify.sh --context <ctx> [--rendered deploy/.rendered/<site>] [--no-probes]
#        env PORTAL_DOMAIN(포탈 FQDN) · PORTAL_VIP(Avi VIP-A — 있으면 `curl --resolve <FQDN>:443:<VIP>`) · CACERT(사내 CA)
#   --context 는 필수다(2026-11-03 ⑦ — --context <ctx> / --context=<ctx> / env KUBE_CONTEXT, 플래그가 이긴다; 둘 다 없으면 exit 2).
#   --rendered 를 주면 render.env(PORTAL_DOMAIN·PORTAL_VIP·레지스트리·GATEWAY_LISTEN_PORT)를 읽고, PG 경로 대조(pg-path-probe.py contrast — S10 과 같은 검사)와
#   애드온 감지 요약(install-detect.py detect)을 더 한다.
#   --no-probes 는 파드를 띄우는 검사(PG 경로 대조 DaemonSet·마스터 리스너 포트 일회용 파드)를 건너뛴다 — 순수 읽기(get·logs·서비스 프록시 GET)만.
#   PORTAL_DOMAIN 이 없으면 VIP 경유 HTTPS 검사는 건너뛴다(앱·DB 의 readyz 는 apiserver 서비스 프록시로 늘 본다).
# 검사(S12): 마이그레이션·워크로드 · 이미지 · CNPG(readyInstances·dms-pg-rw) · Available 여분 PV · PG 경로 대조 · CronJob 마지막 성공 ·
#   Gateway Programmed · **마스터마다 리스너 포트 LISTEN**(render.env GATEWAY_LISTEN_PORT, 없으면 443 —
#   host-net-probe.sh --port; Programmed 는 bind 실패를 못 잡는다) · 레지스트리 Ready·태그 · 애드온 요약 ·
#   readyz(apiserver 서비스 프록시 = 앱·DB 판정 · `--resolve <FQDN>:443:<VIP>` = 입구 — 응답 없음(000)은 Avi VS-443 미개통일 수 있어 WARN, 다른 코드는
#   FAIL — 2026-11-03 ⑦ 리뷰 1) · 「80 불가 = 정상」. FAIL 이 있고 api·controller 로그에 EACCES·Permission denied 가 보이면 SELinux 노드 프로브(S2b)부터
#   다시 돌리라고 안내한다(설계 2-3 — enforcing 이면 DMS 사유가 엉뚱하게 나온다).
set -u
CTX="${KUBE_CONTEXT:-}"
RDIR=""
PROBES=1
usage() { echo "usage: $0 --context <ctx> [--rendered <렌더 디렉터리>] [--no-probes]" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --context) [ $# -ge 2 ] && [ -n "$2" ] || usage; CTX="$2"; shift ;;
    --context=*) CTX="${1#--context=}"; [ -n "$CTX" ] || usage ;;
    --rendered) [ $# -ge 2 ] && [ -n "$2" ] || usage; RDIR="$2"; shift ;;
    --rendered=*) RDIR="${1#*=}" ;;
    --no-probes) PROBES=0 ;;
    *) usage ;;
  esac
  shift
done
[ -n "$CTX" ] || { echo "verify: --context <ctx> 가 필요하다(current-context 에 기대지 않는다 — 2026-11-03 ⑦)" >&2; usage; }
command kubectl config get-contexts "$CTX" >/dev/null 2>&1 \
  || { echo "컨텍스트 '$CTX' 가 kubeconfig 에 없습니다" >&2; exit 2; }
# 이 스크립트의 모든 kubectl 호출이 그 컨텍스트로 간다(호출 지점은 그대로).
kubectl() { command kubectl --context "$CTX" "$@"; }
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)

NS=dms
RENV=""
[ -z "$RDIR" ] || { RENV="$RDIR/render.env"; [ -f "$RENV" ] || { echo "render.env 없음: $RENV" >&2; exit 2; }; }
_rv() { [ -n "$RENV" ] && sed -n "s/^$1=//p" "$RENV" | head -n 1 | tr -d '"'; }
PORTAL="${PORTAL_DOMAIN:-$(_rv PORTAL_DOMAIN)}"
VIP="${PORTAL_VIP:-$(_rv PORTAL_VIP)}"
CACERT="${CACERT:-}"
p=0; w=0; f=0
P(){ printf '  [PASS] %s\n' "$1"; p=$((p+1)); }
W(){ printf '  [WARN] %s\n' "$1"; w=$((w+1)); }
F(){ printf '  [FAIL] %s\n' "$1"; f=$((f+1)); }
I(){ printf '  [INFO] %s\n' "$1"; }
H(){ printf '\n== %s ==\n' "$1"; }
py_ok() { command -v python3 >/dev/null 2>&1 && [ -n "$RDIR" ]; }

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

H "2. 이미지 (live)"
for kind_name in "deploy/dms-api" "deploy/dms-controller" "daemonset/dms-agent"; do
  img=$(kubectl -n "$NS" get "$kind_name" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "?")
  I "$kind_name → ${img:-?}"
done
API_IMG=$(kubectl -n "$NS" get deploy dms-api -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)

H "3. DB (CloudNativePG — 설계 6·S10)"
want=$(kubectl -n "$NS" get clusters.postgresql.cnpg.io dms-pg -o jsonpath='{.spec.instances}' 2>/dev/null)
if [ -n "$want" ]; then
  rdy=$(kubectl -n "$NS" get clusters.postgresql.cnpg.io dms-pg -o jsonpath='{.status.readyInstances}' 2>/dev/null)
  phase=$(kubectl -n "$NS" get clusters.postgresql.cnpg.io dms-pg -o jsonpath='{.status.phase}' 2>/dev/null)
  if [ "${rdy:-0}" = "$want" ]; then P "Cluster dms-pg readyInstances ${rdy}/${want} ($phase)"
  elif [ "${rdy:-0}" -ge 1 ] 2>/dev/null; then W "Cluster dms-pg readyInstances ${rdy}/${want} ($phase) — 쓰기는 된다(모름 ≠ 실패)"
  else F "Cluster dms-pg readyInstances 0 ($phase) — kubectl --context $CTX -n $NS describe cluster dms-pg"; fi
  eps=$(kubectl -n "$NS" get endpoints dms-pg-rw -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null)
  [ -n "$eps" ] && P "dms-pg-rw Endpoints $eps" || F "dms-pg-rw Endpoints 없음(primary 없음)"
  pvs=$(kubectl get pv -o 'jsonpath={range .items[*]}{.metadata.name} {.status.phase}{"\n"}{end}' 2>/dev/null | grep '^dms-pgdata-')
  avail=$(printf '%s\n' "$pvs" | grep -c ' Available$')
  rel=$(printf '%s\n' "$pvs" | grep ' Released$' | cut -d' ' -f1 | tr '\n' ' ')
  [ "${avail:-0}" -ge 1 ] && P "Available 여분 PV ${avail}개" || W "Available 여분 PV 0개 — 인스턴스 재생성이 Pending 이 된다(데이터 위험 없음, Released 재활용 런북 deploy/README §2f-5)"
  [ -z "$rel" ] || W "Released PV: $rel— 건드리지 않았다(재활용 런북 §2f-5)"
  last=$(kubectl -n "$NS" get cronjob dms-pg-dump -o jsonpath='{.status.lastSuccessfulTime}' 2>/dev/null)
  if kubectl -n "$NS" get cronjob dms-pg-dump >/dev/null 2>&1; then
    [ -n "$last" ] && P "CronJob dms-pg-dump 마지막 성공 $last" || W "CronJob dms-pg-dump 에 성공 기록이 아직 없다(첫 정시 전이면 정상 — install.sh S10 의 첫 덤프 Job 을 본다)"
  else F "CronJob dms-pg-dump 없음(S10)"; fi
  if [ "$PROBES" = 0 ]; then I "PG 경로 대조 건너뜀(--no-probes)"
  elif py_ok; then
    out=$(python3 "$HERE/pg-path-probe.py" --context "$CTX" --namespace "$NS" --rendered "$RDIR/platform/db-probe" --mode contrast 2>&1)
    prc=$?
    printf '%s\n' "$out" | grep -E '^(RESULT|FAIL|WARN|UNKNOWN)' | head -n 8 | sed 's/^/    /'
    case "$prc" in
      0) P "PG 경로 대조(contrast — S10 과 같은 검사)" ;;
      3) W "PG 경로 대조 WARN(위 줄)" ;;
      1) F "PG 경로 대조 FAIL — PG 가 공용 경로가 아닌 곳에 쓴다(위 줄)" ;;
      *) W "PG 경로 대조 모름(rc=$prc — 초기화 중인 PVC·프로브 미기동)" ;;
    esac
  else I "PG 경로 대조는 --rendered 를 줄 때만(python3 deploy/pg-path-probe.py --mode contrast)"; fi
else
  F "Cluster dms-pg 없음(CNPG — S9·S10)"
fi

H "4. 레지스트리 (클러스터 내 — 설계 7)"
RCIP=$(_rv REGISTRY_CLUSTER_IP)
if kubectl -n "$NS" get svc dms-registry >/dev/null 2>&1 || [ -n "$RCIP" ]; then
  rr=$(kubectl -n "$NS" get deploy dms-registry -o jsonpath='{.status.readyReplicas}' 2>/dev/null)
  [ "${rr:-0}" -ge 1 ] 2>/dev/null && P "dms-registry Ready(${rr})" || F "dms-registry Ready 아님 — deploy/README §2e-3 표(FailedMount·센티널)"
  svcip=$(kubectl -n "$NS" get svc dms-registry -o jsonpath='{.spec.clusterIP}' 2>/dev/null)
  if [ -n "$RCIP" ]; then [ "$svcip" = "$RCIP" ] && P "Service clusterIP $svcip == REGISTRY_CLUSTER_IP" || F "Service clusterIP ${svcip:-없음} != REGISTRY_CLUSTER_IP $RCIP"; fi
  # 태그 — apiserver 서비스 프록시(읽기 GET, port-forward 없이). 라이브 dms 이미지 태그가 레지스트리에 있어야 새 노드·재스케줄이 받는다.
  tags=$(kubectl get --raw "/api/v1/namespaces/$NS/services/http:dms-registry:http/proxy/v2/dms/tags/list" 2>/dev/null)
  live_tag=${API_IMG##*:}
  case "$tags" in
    *"\"$live_tag\""*) P "레지스트리에 dms:$live_tag 있음(라이브 dms-api 태그)" ;;
    *'"tags"'*) F "레지스트리 dms 태그 목록에 라이브 태그 $live_tag 가 없다 — 새 노드·재스케줄이 pull 하지 못한다" ;;
    *) W "레지스트리 태그를 읽지 못했다(apiserver 서비스 프록시) — install.sh --only S5 로 대조" ;;
  esac
else
  I "클러스터 내 레지스트리 없음(external 사이트)"
fi

H "5. 입구 (Envoy Gateway — 설계 4-3)"
prog=$(kubectl -n "$NS" get gateway dms-portal -o 'jsonpath={.status.conditions[?(@.type=="Programmed")].status}' 2>/dev/null)
if [ "$prog" = True ]; then
  P "Gateway dms-portal Programmed"
  # 리스너 포트 = render.env 의 GATEWAY_LISTEN_PORT(없으면 443 — 옛 렌더·--rendered 없이 부른 경우, 2026-11-14).
  GWPORT=""; [ -z "${RENV:-}" ] || GWPORT=$(sed -n "s/^GATEWAY_LISTEN_PORT=//p" "$RENV" | head -n 1 | tr -d '"')
  case "$GWPORT" in ""|*[!0-9]*) GWPORT=443 ;; esac
  if [ "$PROBES" = 0 ]; then I "마스터 :$GWPORT 건너뜀(--no-probes) — ssh <마스터> 'sudo ss -ltnp \"( sport = :$GWPORT )\"'"
  elif [ -n "$API_IMG" ]; then
    out=$(sh "$HERE/host-net-probe.sh" --context "$CTX" --namespace "$NS" --image "$API_IMG" --port "$GWPORT" --listen 2>&1)
    hrc=$?
    printf '%s\n' "$out" | sed 's/^/    /'
    case "$hrc" in
      0) P "마스터마다 :$GWPORT LISTEN" ;;
      1) F "마스터 :$GWPORT LISTEN 없음 — sysctl(deploy/README §2g-2) 고친 뒤 그 노드의 Envoy 파드 삭제" ;;
      *) W "마스터 :$GWPORT 을 재지 못했다(rc=$hrc) — ssh <마스터> 'sudo ss -ltnp \"( sport = :$GWPORT )\"'" ;;
    esac
  fi
elif [ -n "$prog" ]; then
  F "Gateway dms-portal Programmed=$prog — kubectl --context $CTX -n $NS describe gateway dms-portal"
else
  F "Gateway dms-portal 없음(S8)"
fi

H "6. 포탈 (readyz 는 DB 까지 확인)"
# 앱·DB 판정은 apiserver 서비스 프록시(읽기 GET — VIP·Avi·DNS·CA 와 무관)로 한다. 첫 설치에서는 Avi VS-443 이 S13 몫이라 VIP 가 아직 닿지 않는데,
# VIP 경유 readyz 하나로 FAIL 을 내면 S12 가 늘 멈추고 S13 체크리스트가 나오지 않았다(2026-11-03 ⑦ 리뷰 1 — dmsk8s 실측: 서비스 프록시 200).
# /readyz 는 DB 쿼리까지 하는 실검사다(/healthz·임의 경로는 SPA 폴백으로 200 이 떠 거짓 통과가 되므로 readyz).
if svc_ready=$(kubectl get --raw "/api/v1/namespaces/$NS/services/http:dms-api:http/proxy/readyz" 2>&1); then
  case "$svc_ready" in
    *'"ok"'*) P "readyz ok(apiserver 서비스 프록시 dms-api — 앱·DB 도달)" ;;
    *) F "readyz 가 ok 가 아니다(apiserver 서비스 프록시: $(printf '%s' "$svc_ready" | tr '\n' ' ' | cut -c1-160))" ;;
  esac
else
  F "readyz 실패(apiserver 서비스 프록시 dms-api:http/readyz — $(printf '%s' "$svc_ready" | tr '\n' ' ' | cut -c1-160)) — DB(3절)·kubectl --context $CTX -n $NS logs deploy/dms-api"
fi
if [ -n "$PORTAL" ]; then
  set --
  [ -z "$CACERT" ] || set -- "$@" --cacert "$CACERT"
  [ -z "$VIP" ] || set -- "$@" --resolve "$PORTAL:443:$VIP"
  code=$(curl --noproxy '*' -s "$@" -m5 -o /dev/null -w '%{http_code}' "https://$PORTAL/readyz" 2>/dev/null || true)
  case "$code" in
    200) P "readyz 200 via https://$PORTAL${VIP:+ (--resolve $PORTAL:443:$VIP)} — 입구(Avi·Envoy·TLS)까지" ;;
    000|"") W "VIP 경유 readyz 응답 없음(연결·TLS 실패) — Avi VS-443 미개통이면 정상(S13 — 개통 뒤 install.sh --only S12). 개통했으면 PORTAL_VIP·CACERT(사내 CA)·Avi 풀 헬스(멤버 포트 = GATEWAY_LISTEN_PORT)·PROXY 모드(LB 가 PROXY v2 를 못 붙이면 GATEWAY_PROXY_PROTOCOL=off) 확인" ;;
    *) F "VIP 경유 readyz ${code} — 입구는 닿는데 앱이 ${code} 로 답한다(Envoy·HTTPRoute — deploy/README §2g-3 표)" ;;
  esac
  set --
  [ -z "$VIP" ] || set -- --resolve "$PORTAL:80:$VIP"
  rc=$(curl --noproxy '*' -s "$@" -m5 -o /dev/null -w '%{http_code}' "http://$PORTAL/" 2>/dev/null || true)
  case "$rc" in
    000|"") P "80 불가 = 정상(80 리스너·80 VS 없음 — 안내는 https://$PORTAL 하나)" ;;
    *) I "http://$PORTAL/ 가 $rc 로 답한다 — 옛 입구(ingress) 병행 중이면 정상, 아니면 80 을 누가 받는지 확인" ;;
  esac
else
  I "PORTAL_DOMAIN 미설정 — HTTPS 검증 건너뜀(PORTAL_DOMAIN=<FQDN> PORTAL_VIP=<VIP-A> 로 다시)"
fi

if py_ok; then
  H "7. 애드온 요약 (install-detect.py detect)"
  out=$(python3 "$HERE/install-detect.py" --context "$CTX" --namespace "$NS" detect --rendered "$RDIR" 2>&1)   # env DMS_NS 를 읽지 않게(⑦ 리뷰 1)
  drc=$?
  printf '%s\n' "$out" | grep -E '^(DETECT|RESULT):' | sed 's/^/    /'
  case "$drc" in
    0) P "애드온 판정 PASS" ;;
    3) W "애드온 판정 WARN(C!·D<·D>·U — 남이 설치한 애드온의 EOL·지원 범위는 소유자에게)" ;;
    1) F "애드온 판정 FAIL(python3 deploy/install-detect.py --context $CTX detect --rendered $RDIR 로 사유)" ;;
    *) W "애드온 판정 모름(rc=$drc)" ;;
  esac
fi

if [ "$f" -gt 0 ]; then
  logs=$( { kubectl -n "$NS" logs deploy/dms-api --tail=300 2>/dev/null; kubectl -n "$NS" logs deploy/dms-controller --tail=300 2>/dev/null; } \
    | grep -ciE 'EACCES|Permission denied')
  if [ "${logs:-0}" -gt 0 ]; then
    # --site 를 함께(install.sh 의 기본 사이트는 prod — 다른 사이트에서 그대로 붙여 넣으면 prod 를 렌더해 envfail 이었다, ⑦ 리뷰 1).
    site=$(_rv SITE); site=${site:-<site>}
    printf '\n  [HINT] api·controller 로그에 EACCES·Permission denied 가 %s줄 — SELinux enforcing 노드일 수 있다(설계 2-3). 먼저:\n' "$logs"
    printf '         sh deploy/install.sh --context %s --site %s --only S2b   (RHEL 계열 DMS 노드: getenforce · grep ^SELINUX= /etc/selinux/config)\n' "$CTX" "$site"
  fi
fi

printf '\n== 요약 ==  PASS=%d  WARN=%d  FAIL=%d\n' "$p" "$w" "$f"
[ "$f" -eq 0 ]
