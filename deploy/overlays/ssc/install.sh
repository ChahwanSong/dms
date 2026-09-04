#!/bin/sh
# DMS SSC 설치 — 웹 노드 hostNetwork 엣지 모드, 원커맨드·멱등(2026-08-30).
# overlays/prod/install.sh 의 SSC 판: MetalLB 게이트와 address-pool 어노테이션이
# 없고, 대신 "public IP 웹 노드"가 실제로 배선됐는지(라벨 + ingress-nginx 가 그
# 노드에 hostNetwork 로 고정) 를 apply 전에 한 줄 사유로 검증한다.
#
# 선행(이 스크립트 밖, README 참조):
#   1) kubectl label node <WEB_NODE> dms.io/web-node=true
#   2) ingress-nginx 를 hostNetwork 로 설치(deploy/addons/ingress-nginx/) — 웹 노드 고정
#   3) dms-secrets · dms-portal-tls(SAN 에 PORTAL_PUBLIC_IP 포함) 생성
#   4) values.env 채움
#
# 사용:  sh deploy/overlays/ssc/install.sh --dry-run   →   sh .../install.sh
set -eu
NS="${DMS_NS:-dms}"
INGRESS_NS="${INGRESS_NS:-ingress-nginx}"
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)      # deploy/overlays/ssc
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
step(){ printf '\n== %s ==\n' "$1"; }
ok(){ printf '  [OK] %s\n' "$1"; }
warn(){ printf '  [WARN] %s\n' "$1"; }
die(){ printf '  [FAIL] %s\n' "$1" >&2; exit 1; }

# WEB_NODE 를 값 파일에서 읽는다(엣지 게이트에 필요).
VALS="${VALUES_ENV:-$HERE/values.env}"
[ -f "$VALS" ] || die "values.env 없음 — cp values.env.example values.env 후 채우세요"
# shellcheck disable=SC1090
. "$VALS"

step "1. 게이트 (apply 전 치명 오류 차단 — MetalLB 없음, hostNetwork 엣지)"
kubectl version >/dev/null 2>&1 || die "kubectl 이 클러스터에 못 붙음 — kubeconfig 확인"
kubectl get crd jobs.batch.volcano.sh >/dev/null 2>&1 \
  || die "Volcano 미설치 (jobs.batch.volcano.sh CRD 없음)"
kubectl get ingressclass nginx >/dev/null 2>&1 \
  || die "ingress-nginx 미설치 (IngressClass nginx 없음)"
ok "애드온: Volcano · ingress-nginx  (MetalLB 는 이 모드에서 불필요)"

# --- 웹 노드(public IP 소유) 배선 검증 ---
case "${WEB_NODE:-}" in ""|REPLACE_*) die "values.env 의 WEB_NODE 가 비었음 — public IP 노드 이름을 넣으세요";; esac
kubectl get node "$WEB_NODE" >/dev/null 2>&1 || die "노드 '$WEB_NODE' 없음 — WEB_NODE 확인"
LBL=$(kubectl get node "$WEB_NODE" -o jsonpath='{.metadata.labels.dms\.io/web-node}' 2>/dev/null || true)
[ "$LBL" = "true" ] || die "노드 '$WEB_NODE' 에 라벨 dms.io/web-node=true 없음 — kubectl label node $WEB_NODE dms.io/web-node=true"
ok "웹 노드 '$WEB_NODE' 라벨 dms.io/web-node=true"

# ingress-nginx 컨트롤러가 그 노드에 hostNetwork 로 떠 있는가 (엣지의 핵심)
CPOD=$(kubectl -n "$INGRESS_NS" get pods -l app.kubernetes.io/component=controller \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -n "$CPOD" ] || die "ingress-nginx 컨트롤러 파드를 $INGRESS_NS 에서 못 찾음 — INGRESS_NS 확인/애드온 설치"
CNODE=$(kubectl -n "$INGRESS_NS" get pod "$CPOD" -o jsonpath='{.spec.nodeName}' 2>/dev/null || true)
CHOSTNET=$(kubectl -n "$INGRESS_NS" get pod "$CPOD" -o jsonpath='{.spec.hostNetwork}' 2>/dev/null || true)
[ "$CHOSTNET" = "true" ] || die "ingress-nginx 가 hostNetwork 가 아님 — deploy/addons/ingress-nginx/ 로 hostNetwork 설치(엣지 모드 필수)"
[ "$CNODE" = "$WEB_NODE" ] || die "ingress-nginx 컨트롤러가 웹 노드가 아니라 '$CNODE' 에 떠 있음 — nodeSelector dms.io/web-node=true 확인"
ok "ingress-nginx: hostNetwork · 웹 노드 '$WEB_NODE' 에 고정"
kubectl -n "$INGRESS_NS" get pod "$CPOD" -o jsonpath='{.spec.containers[0].args}' 2>/dev/null \
  | grep -q default-ssl-certificate \
  && ok "--default-ssl-certificate 설정됨(IP 직접 https 지원)" \
  || warn "--default-ssl-certificate 미설정 — IP 직접 https 접속에 필요(도메인만 쓰면 생략 가능)"
# https 리슨 포트가 방화벽이 연 포트(PORTAL_PORT)와 같은가. hostNetwork 라
# containerPort == 호스트 포트. 다르면 사용자가 그 포트로 못 닿는다(표준 443 기본).
PPORT="${PORTAL_PORT:-443}"
HPORT=$(kubectl -n "$INGRESS_NS" get pod "$CPOD" \
  -o jsonpath='{.spec.containers[0].ports[?(@.name=="https")].containerPort}' 2>/dev/null || true)
if [ -n "$HPORT" ] && [ "$HPORT" != "$PPORT" ]; then
  warn "ingress-nginx https 리슨 포트($HPORT) != PORTAL_PORT($PPORT) — 애드온 values 의 containerPort.https 를 $PPORT 로 맞추세요"
else
  ok "https 리슨 포트 = ${HPORT:-$PPORT} (사용자 접속: https://${PORTAL_PUBLIC_IP:-<IP>}:$PPORT)"
fi

# --- 시크릿 · TLS (prod 와 동일) ---
kubectl -n "$NS" get secret dms-secrets >/dev/null 2>&1 \
  || die "dms-secrets 없음 — README §Secret 로 먼저 생성"
SEC=$(kubectl -n "$NS" get secret dms-secrets \
  -o go-template='{{range $k,$v := .data}}{{$k}}={{$v|base64decode}}{{"\n"}}{{end}}' 2>/dev/null || true)
for k in DMS_DATABASE_URL DMS_SHARED_TOKEN DMS_ADMIN_TOKEN DMS_SESSION_SECRET DMS_LDAP_BIND_PW; do
  printf '%s\n' "$SEC" | grep -q "^$k=" || die "dms-secrets 에 $k 없음"
done
printf '%s\n' "$SEC" | grep -Eq 'CHANGE_ME|REPLACE_WITH_' \
  && die "dms-secrets 에 자리표시자(CHANGE_ME/REPLACE_WITH_) 남음 — 실제 값 주입"
ok "dms-secrets: 필수 키 존재 · 자리표시자 없음"
kubectl -n "$NS" get secret dms-portal-tls >/dev/null 2>&1 \
  || die "dms-portal-tls(TLS) 없음 — SAN 에 PORTAL_PUBLIC_IP 포함해 생성(README)"
ok "dms-portal-tls 존재"

step "2. 렌더 (values.env → .ssc-rendered)"
R=$(sh "$HERE/render.sh") || { printf '%s\n' "$R" >&2; die "render 실패(위 사유)"; }
DIR=$(printf '%s\n' "$R" | sed -n 's/^RENDERED: //p')
[ -n "$DIR" ] || die "렌더 디렉터리 확인 불가"
ok "렌더 완료: $DIR"
# 안전망: 렌더 산출물에 MetalLB 종류가 남아 있으면(삭제 패치 실패) 즉시 중단 --
# SSC 클러스터엔 MetalLB CRD 가 없어 apply 가 깨진다.
if kubectl kustomize "$DIR" 2>/dev/null | grep -Eq '^kind:[[:space:]]*(IPAddressPool|L2Advertisement)'; then
  die "렌더 산출물에 MetalLB 리소스가 남음 — kustomization 의 \$patch: delete 확인"
fi
ok "MetalLB 리소스 없음(엣지 모드 렌더 정상)"

if [ "$DRY" = 1 ]; then
  step "3. DRY-RUN (서버측 검증 — 변경 없음)"
  kubectl apply -k "$DIR" --dry-run=server >/dev/null \
    && ok "서버측 apply 검증 통과" || die "서버측 검증 실패"
  printf '\nDRY-RUN 통과 — 실제 설치는 --dry-run 없이 재실행.\n'
  exit 0
fi

step "3. 적용 (kubectl apply -k)"
kubectl -n "$NS" delete job dms-migrate --ignore-not-found >/dev/null 2>&1 || true
kubectl apply -k "$DIR"

step "4. migrate 완료 대기"
kubectl -n "$NS" wait --for=condition=complete job/dms-migrate --timeout=180s

step "5. 롤아웃 대기"
kubectl -n "$NS" rollout status deploy/dms-api --timeout=180s
kubectl -n "$NS" rollout status deploy/dms-controller --timeout=180s
kubectl -n "$NS" rollout status ds/dms-agent --timeout=180s

# MetalLB address-pool 어노테이션 단계 없음 — 노출은 웹 노드 hostNetwork 가 한다.
printf '\n설치 완료. 사용자 접속: https://%s:%s\n검증: PORTAL_PUBLIC_IP=%s PORTAL_PORT=%s PORTAL_DOMAIN=%s CACERT=<사내CA> sh deploy/overlays/ssc/verify.sh\n' \
  "${PORTAL_PUBLIC_IP:-<IP>}" "${PORTAL_PORT:-443}" "${PORTAL_PUBLIC_IP:-<IP>}" "${PORTAL_PORT:-443}" "${PORTAL_DOMAIN:-<도메인>}"
