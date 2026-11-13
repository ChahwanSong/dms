#!/bin/sh
# DMS 프로덕션 배포 프리플라이트 점검(2026-08-20 → 2026-11-03 ⑦ 설계 v6 8-1 「S1 점검 항목」). READ-ONLY -- kubectl get 만 쓰고
# 클러스터를 절대 변경하지 않는다. install.sh 의 S1 이 부르고(그때는 감지 표를 install.sh 가 따로 보인다 — PREFLIGHT_SKIP_DETECT=1), 운영자가
# 따로 돌려도 된다.
#
# 사용:  sh deploy/preflight-cluster.sh --context <ctx> [--render-env deploy/.rendered/<site>/render.env]
#        --context 는 필수다(2026-11-03 ⑦ — --context <ctx> / --context=<ctx> / env KUBE_CONTEXT, 플래그가 이긴다; 둘 다 없으면 exit 2).
#        두 테스트베드가 같은 네임스페이스라 current-context 에 기대지 않는다.
#        --render-env 를 주면 SHARED_DIR·REGISTRY_CLUSTER_IP·REGISTRY_PORT·REGISTRY_MODE·REGISTRY·REGISTRY_TLS_VERIFY·GATEWAY_LISTEN_PORT·GATEWAY_PROXY_PROTOCOL 를 거기서 읽는다(없으면 같은 이름의 env —
#        REGISTRY·REGISTRY_TLS_VERIFY 는 external 사이트의 노드 셸 체크리스트용, 2026-11-03 ⑦ 리뷰 2).
#
# 판정: [PASS] 그대로 진행 가능 · [WARN] 조정/확인 필요(치명 아님) · [FAIL] 막힘
#       [INFO] 참고 · [MANUAL] 스크립트로 못 보는 항목(노드 셸 — 설치자가 명령을 돌려 결과를 확인한다)
# 종료: 0 FAIL 없음 · 1 FAIL 하나라도 · 2 사용법·클러스터에 못 붙음.
set -u
CTX="${KUBE_CONTEXT:-}"
CTX_FROM=KUBE_CONTEXT
RENDER_ENV=""
usage() { echo "usage: $0 --context <ctx> [--render-env <render.env>]" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --context) [ $# -ge 2 ] && [ -n "$2" ] || usage; CTX="$2"; CTX_FROM=--context; shift ;;
    --context=*) CTX="${1#--context=}"; CTX_FROM=--context; [ -n "$CTX" ] || usage ;;
    --render-env) [ $# -ge 2 ] && [ -n "$2" ] || usage; RENDER_ENV="$2"; shift ;;
    --render-env=*) RENDER_ENV="${1#*=}" ;;
    *) usage ;;
  esac
  shift
done
[ -n "$CTX" ] || { echo "preflight: --context <ctx> 가 필요하다(current-context 에 기대지 않는다 — 2026-11-03 ⑦)" >&2; usage; }
command kubectl config get-contexts "$CTX" >/dev/null 2>&1 \
  || { echo "컨텍스트 '$CTX' 가 kubeconfig 에 없습니다" >&2; exit 2; }
# 이 스크립트의 모든 kubectl 호출이 그 컨텍스트로 간다(호출 지점은 그대로).
kubectl() { command kubectl --context "$CTX" "$@"; }
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)

# 렌더 값(sh 로 . 하지 않는다 — 줄만 읽는다)
_rv() { [ -n "$RENDER_ENV" ] && [ -f "$RENDER_ENV" ] && sed -n "s/^$1=//p" "$RENDER_ENV" | head -n 1 | tr -d '"'; }
if [ -n "$RENDER_ENV" ]; then
  [ -f "$RENDER_ENV" ] || { echo "render.env 없음: $RENDER_ENV" >&2; exit 2; }
  SHARED_DIR=$(_rv SHARED_DIR); REGISTRY_CLUSTER_IP=$(_rv REGISTRY_CLUSTER_IP)
  REGISTRY_PORT=$(_rv REGISTRY_PORT); REGISTRY_MODE=$(_rv REGISTRY_MODE); REGISTRY=$(_rv REGISTRY)
  REGISTRY_TLS_VERIFY=$(_rv REGISTRY_TLS_VERIFY)
  GATEWAY_LISTEN_PORT=$(_rv GATEWAY_LISTEN_PORT); GATEWAY_PROXY_PROTOCOL=$(_rv GATEWAY_PROXY_PROTOCOL)
fi
# 입구 리스너(마스터 호스트) 포트·PROXY 모드(2026-11-14 — render.env, 없으면 같은 이름의 env, 그것도 없으면 443·required).
GWPORT="${GATEWAY_LISTEN_PORT:-443}"; case "$GWPORT" in ""|*[!0123456789]*) GWPORT=443 ;; esac
GWPROXY="${GATEWAY_PROXY_PROTOCOL:-required}"
SHARED_DIR="${SHARED_DIR:-}"; REGISTRY_CLUSTER_IP="${REGISTRY_CLUSTER_IP:-}"
REGISTRY_PORT="${REGISTRY_PORT:-5000}"; REGISTRY_MODE="${REGISTRY_MODE:-}"; REGISTRY="${REGISTRY:-}"
REGISTRY_TLS_VERIFY="${REGISTRY_TLS_VERIFY:-}"
DMS_NS=dms

pass=0; warn=0; fail=0
P(){ printf '  [PASS] %s\n' "$1"; pass=$((pass+1)); }
W(){ printf '  [WARN] %s\n' "$1"; warn=$((warn+1)); }
F(){ printf '  [FAIL] %s\n' "$1"; fail=$((fail+1)); }
I(){ printf '  [INFO] %s\n' "$1"; }
M(){ printf '  [MANUAL] %s\n' "$1"; }
H(){ printf '\n== %s ==\n' "$1"; }
crd(){ kubectl get crd "$1" >/dev/null 2>&1; }

kubectl version >/dev/null 2>&1 || { echo "kubectl 이 클러스터에 붙지 못했습니다 -- kubeconfig·--context 확인"; exit 2; }

H "0. 클러스터 / 컨텍스트"
I "context: $CTX ($CTX_FROM)"
SVER=$(kubectl version -o json 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin)['serverVersion']['gitVersion'])" 2>/dev/null || echo "?")
I "server version: $SVER"
# 설계 #4·S1: prod 가 쓰는 minor 를 따른다 — 1.34~1.36(CNPG 1.30·EG v1.9·Volcano v1.16 고정의 지원 범위). 밖이면 고정 애드온을 보증할 수 없다.
case "$SVER" in
  v1.34.*|v1.35.*|v1.36.*) P "K8s minor 1.34~1.36 안($SVER)" ;;
  "?"|"") W "K8s 버전을 읽지 못했다 — 1.34~1.36 이어야 한다(설계 #4)" ;;
  *) F "K8s $SVER — 1.34~1.36 밖이다(설계 #4 — CNPG 1.30·EG v1.9·Volcano v1.16 지원 범위). 애드온 고정(deploy/versions.env)을 그 minor 에 맞춘 뒤" ;;
esac
NCP=$(kubectl get nodes -l node-role.kubernetes.io/control-plane --no-headers 2>/dev/null | wc -l | tr -d ' ')
case "$NCP" in
  3) P "control-plane 노드 3대" ;;
  0) F "control-plane 노드를 찾지 못했다(라벨 node-role.kubernetes.io/control-plane — 레지스트리·PG·Envoy·api 배치의 기준)" ;;
  *) W "control-plane 노드 ${NCP}대 — prod 는 3대(PG anti-affinity required·Envoy·레지스트리 이동). 테스트베드 1대는 정상" ;;
esac
TAINTS=$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o 'jsonpath={range .items[*]}{.metadata.name}:{range .spec.taints[*]}{.key}={.effect},{end} {end}' 2>/dev/null)
[ -n "$TAINTS" ] && I "control-plane taint: $TAINTS(배치는 node-role.kubernetes.io/control-plane:NoSchedule toleration — 다른 키면 patch-placement·EnvoyProxy·Cluster 를 맞춘다, K3)"
I "CNI: 코드가 cilium/calico 인터페이스를 인식하므로 CNI 종류 제약 없음(Cilium OK — KPR 없음 + kube-proxy 전제, 설계 7-4)"

H "1. Volcano (잡 gang 스케줄러)"
if crd jobs.batch.volcano.sh && crd queues.scheduling.volcano.sh; then
  P "Volcano CRD 존재 (jobs.batch.volcano.sh, queues.scheduling.volcano.sh)"
  # 2026-11-03 ⑦ 버그 수정: 상류 매니페스트의 스케줄러 라벨은 app=volcano-scheduler 다(옛 app.kubernetes.io/name=volcano-scheduler 는 상류에 없어 늘 빈 결과).
  VVER=$(kubectl get deploy -A -l app=volcano-scheduler -o jsonpath='{.items[0].spec.template.spec.containers[0].image}' 2>/dev/null)
  if [ -n "$VVER" ]; then I "scheduler image: $VVER (≥ v1.15, K8s 1.36 이면 v1.16 — 테스트베드 v1.15.0)"
  else W "volcano-scheduler Deployment 를 라벨 app=volcano-scheduler 로 찾지 못했다 — install-detect.py 가 이름·이미지로도 찾는다(아래 감지 요약)"; fi
  kubectl get crd jobs.batch.volcano.sh -o jsonpath='{.spec.versions[*].name}' 2>/dev/null | grep -qw v1alpha1 \
    && P "batch.volcano.sh/v1alpha1 지원(코드가 제출하는 버전)" \
    || W "jobs.batch.volcano.sh 에 v1alpha1 이 안 보임 -- Volcano 버전 확인"
  kubectl get queue dms-data >/dev/null 2>&1 \
    && I "Queue 'dms-data' 이미 존재 -- 05-…yaml apply 가 덮어씀(우리 리소스면 OK)" \
    || P "Queue 'dms-data' 없음 -- 05-…yaml 이 생성"
else
  F "Volcano 미설치 -- 잡이 영원히 Pending. DMS 설치기의 Volcano 층(v1.16.0)은 아직 동봉하지 않는다(docs/BACKLOG.md 「⑦ 설치기 후속」) — 클러스터 운영자에게 ≥ v1.15(K8s 1.36 이면 v1.16) 설치 요청"
fi
for pc in dms-low dms-mid dms-high dms-build; do
  kubectl get priorityclass "$pc" >/dev/null 2>&1 && I "PriorityClass '$pc' 이미 존재 -- 05-…yaml 이 덮어씀(값 확인)" || :
done

H "2. 입구 (Envoy Gateway — MetalLB·ingress-nginx 는 필요 없다)"
I "prod 입구는 LB VS-443(Avi L4 — PROXY 모드 $GWPROXY) → 마스터 :$GWPORT 의 Envoy(설계 #13, 2026-11-03 ⑥; 포트·모드는 values.env GATEWAY_LISTEN_PORT·GATEWAY_PROXY_PROTOCOL) — MetalLB·ingress-nginx 는 확인하지 않는다(있어도 무관)"
# 마스터에 hostPort <리스너 포트> 를 쓰는 다른 파드(2026-11-14 OA 실측 — RKE2 Traefik 의 hostPort 443): hostPort 는 호스트 소켓이 아니라 CNI 의
# DNAT 규칙이라 `ss` 에는 안 보이는데, 그 포트로 온 패킷은 Envoy 보다 먼저 그 파드로 넘어간다. hostNetwork 파드는 hostPort 가 자동으로 채워질
# 뿐이라 뺀다(DMS 자기 Envoy 포함). 못 읽으면 모름(WARN) — install.sh S8 이 SSA 전에 다시 본다.
CPN=$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o 'jsonpath={range .items[*]}{.metadata.name}{","}{end}' 2>/dev/null | tr ',' ' ')
if HPODS=$(kubectl get pods -A -o 'jsonpath={range .items[*]}{.metadata.namespace}/{.metadata.name}{"\t"}{.spec.nodeName}{"\t"}{.spec.hostNetwork}{"\t"}{range .spec.containers[*]}{range .ports[*]}{.hostPort}{" "}{end}{end}{"\n"}{end}' 2>/dev/null); then
  HHITS=$(printf '%s\n' "$HPODS" | awk -F '\t' -v port="$GWPORT" -v cps=" $CPN " '$3 != "true" && index(cps, " " $2 " ") { n = split($4, a, " "); for (i = 1; i <= n; i++) if (a[i] == port) { print $1 "@" $2; break } }' | sort -u | tr '\n' ' ')
  if [ -n "$HHITS" ]; then
    F "마스터에 hostPort $GWPORT 를 쓰는 다른 파드가 있다: ${HHITS}— CNI 가 그 포트의 패킷을 Envoy 보다 먼저 그 파드로 넘긴다(Envoy 는 LISTEN 으로 보여도 트래픽을 못 받는다). values.env 의 GATEWAY_LISTEN_PORT 를 비어 있는 포트(예: 10443)로 바꾸고 LB 풀 멤버 포트·방화벽을 그 포트로 맞춘다(deploy/README §2g-1·§2g-4)"
  else
    P "마스터에 hostPort $GWPORT 를 쓰는 다른 파드 없음(Envoy 리스너 포트)"
  fi
else
  W "마스터의 hostPort $GWPORT 충돌을 확인하지 못했다(파드 목록 조회 실패) — kubectl --context $CTX get pods -A -o yaml | grep -n 'hostPort: $GWPORT'"
fi
CORE=$(kubectl -n kube-system get configmap coredns -o jsonpath='{.data.Corefile}' 2>/dev/null)
case "$CORE" in
  *"kubernetes cluster.local"*) P "클러스터 도메인 cluster.local(EG xDS 주소 envoy-gateway.envoy-gateway-system.svc.cluster.local)" ;;
  "") I "CoreDNS 설정을 읽지 못했다 — 클러스터 도메인이 cluster.local 인지 손으로(deploy/README §2g-2)" ;;
  *) W "클러스터 도메인이 cluster.local 이 아닌 것 같다 — EG 컨트롤러 env KUBERNETES_CLUSTER_DOMAIN 을 맞춰야 xDS 가 붙는다(deploy/README §2g-2)" ;;
esac
GC=$(kubectl get gatewayclass dms -o jsonpath='{.spec.controllerName}' 2>/dev/null)
[ -z "$GC" ] || I "GatewayClass 'dms' 이미 있음(controllerName $GC) — 감지 요약의 gateway 항목 참고"

H "3. Pod Security Admission"
if kubectl get ns "$DMS_NS" >/dev/null 2>&1; then
  ENF=$(kubectl get ns "$DMS_NS" -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}' 2>/dev/null)
  case "$ENF" in
    privileged) P "ns '$DMS_NS' enforce=privileged (잡 파드 root·hostPath·S2b privileged 프로브 허용)";;
    "") W "ns '$DMS_NS' 존재하나 PSA enforce 라벨 없음 -- S2 가 00-namespace.yaml 로 privileged 를 붙인다(클러스터 기본 PSA 가 restricted 면 그 전에 잡 파드 거부)";;
    *) F "ns '$DMS_NS' enforce=$ENF -- 잡 파드가 root·hostPath 로 떠서 거부됨. privileged 필요(사내 보안정책 확인 포인트)";;
  esac
else
  I "ns '$DMS_NS' 아직 없음 -- S2 가 00-namespace.yaml 로 enforce=privileged 로 생성. 사내 정책이 privileged ns 를 허용하는지 확인"
fi
if kubectl get ns envoy-gateway-system >/dev/null 2>&1; then
  EGE=$(kubectl get ns envoy-gateway-system -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}' 2>/dev/null)
  case "$EGE" in
    privileged|"") I "ns envoy-gateway-system enforce=${EGE:-없음} — Envoy 가 hostNetwork 라 privileged 가 필요하다(DMS 가 설치하면 라벨을 붙인다)" ;;
    *) W "ns envoy-gateway-system enforce=$EGE — Envoy(hostNetwork)가 거부된다(설계 4-3, S1 승인)" ;;
  esac
fi

H "4. Admission 정책 (hostPath PV·hostNetwork·privileged 파드를 막는가 — K11)"
VAPS=$(kubectl get validatingadmissionpolicies -o 'jsonpath={range .items[*]}{.metadata.name}{" "}{end}' 2>/dev/null)
[ -z "$VAPS" ] || I "ValidatingAdmissionPolicy: $VAPS(DMS 의 dms-pg-shared-pvc-namespace 밖의 것이 hostPath·hostNetwork·privileged 를 막는지 확인)"
VWCS=$(kubectl get validatingwebhookconfigurations -o 'jsonpath={range .items[*]}{.metadata.name}{" "}{end}' 2>/dev/null)
[ -z "$VWCS" ] || I "ValidatingWebhookConfiguration: $VWCS"
if crd clusterpolicies.kyverno.io || crd constrainttemplates.templates.gatekeeper.sh; then
  W "정책 엔진(Kyverno·Gatekeeper)이 있다 — DMS 는 hostPath PV(PG)·hostNetwork(Envoy·프로브)·privileged(S2b 프로브)·root 잡 파드를 쓴다. ns dms·envoy-gateway-system 예외를 확인(K11)"
else
  P "정책 엔진(Kyverno·Gatekeeper) CRD 없음"
fi
M "(선택) VAP 로 SC dms-pg-shared 의 PVC 를 ns dms 로 제한 — DMS 가 기본으로 싣는다(platform/db-volumes, deploy/README §2f-1)"

H "5. 공용 디렉터리 · 레지스트리 값 (render.env)"
_sd_problem() {   # render.sh·shared-storage-probe.py 와 같은 규칙(설계 3-2)
  case "$1" in /*) ;; *) echo "절대 경로가 아님"; return ;; esac
  case "$1" in *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-]*) echo "허용 밖 문자(영문·숫자·. _ / - 만)"; return ;; esac
  case "$1" in */) echo "'/' 자체이거나 끝에 / 가 있음"; return ;; *//*|*/./*|*/../*|*/.|*/..) echo "정규화되지 않은 경로"; return ;; esac
}
if [ -n "$SHARED_DIR" ]; then
  why=$(_sd_problem "$SHARED_DIR")
  [ -z "$why" ] && P "SHARED_DIR=$SHARED_DIR 형식(절대·정규화·^/[A-Za-z0-9._/-]+$, '/' 아님)" || F "SHARED_DIR='$SHARED_DIR' — $why"
else
  I "SHARED_DIR 를 모른다(--render-env 또는 env SHARED_DIR) — 형식 검사 생략"
fi
if [ -n "$REGISTRY_CLUSTER_IP" ]; then
  # ServiceCIDR 안·하단 정적 대역(kube-apiserver 는 정적 대역 = min(max(16, 크기/16), 256) 개를 동적 할당에서 뒤로 미룬다 — KEP-3070).
  CIDRS=$(kubectl get servicecidr -o 'jsonpath={range .items[*]}{range .spec.cidrs[*]}{@}{" "}{end}{end}' 2>/dev/null)
  if [ -z "$CIDRS" ]; then
    W "ServiceCIDR 를 읽지 못했다(networking.k8s.io ServiceCIDR — K8s 1.33+) — REGISTRY_CLUSTER_IP $REGISTRY_CLUSTER_IP 가 --service-cluster-ip-range 안·하단 정적 대역인지 손으로"
  else
    VERDICT=$(python3 - "$REGISTRY_CLUSTER_IP" $CIDRS <<'PYEOF' 2>/dev/null
import ipaddress, sys
ip = ipaddress.ip_address(sys.argv[1])
for c in sys.argv[2:]:
    try:
        net = ipaddress.ip_network(c, strict=False)
    except ValueError:
        continue
    if ip.version == net.version and ip in net:
        band = min(max(16, net.num_addresses // 16), 256)
        off = int(ip) - int(net.network_address)
        print("IN %s %d %d" % (c, off, band))
        break
else:
    print("OUT")
PYEOF
)
    case "$VERDICT" in
      IN*) set -- $VERDICT
           if [ "$3" -ge 1 ] && [ "$3" -lt "$4" ]; then P "REGISTRY_CLUSTER_IP $REGISTRY_CLUSTER_IP 가 ServiceCIDR $2 의 하단 정적 대역(오프셋 $3 < $4)"
           else W "REGISTRY_CLUSTER_IP $REGISTRY_CLUSTER_IP 가 ServiceCIDR $2 안이지만 하단 정적 대역 밖(오프셋 $3, 대역 1..$(( $4 - 1 ))) — 동적 할당과 부딪칠 수 있다(설계 7-4)"; fi ;;
      OUT) F "REGISTRY_CLUSTER_IP $REGISTRY_CLUSTER_IP 가 ServiceCIDR($CIDRS) 밖이다 — Service 를 만들 수 없다" ;;
      *) W "REGISTRY_CLUSTER_IP 대역 판정을 못 했다(python3)" ;;
    esac
  fi
  # 미사용(IPAddress 객체 — networking.k8s.io/v1, K8s 1.33+): 있으면 parentRef 가 DMS 레지스트리여야 한다.
  PARENT=$(kubectl get ipaddresses.networking.k8s.io "$REGISTRY_CLUSTER_IP" -o 'jsonpath={.spec.parentRef.namespace}/{.spec.parentRef.name}' 2>/dev/null)
  case "$PARENT" in
    "") OWNER=$(kubectl get svc -A -o 'jsonpath={range .items[*]}{.spec.clusterIP} {.metadata.namespace}/{.metadata.name}{"\n"}{end}' 2>/dev/null \
               | awk -v ip="$REGISTRY_CLUSTER_IP" '$1 == ip {print $2; exit}')
        case "$OWNER" in
          "") P "REGISTRY_CLUSTER_IP $REGISTRY_CLUSTER_IP 미사용" ;;
          "$DMS_NS/dms-registry") P "REGISTRY_CLUSTER_IP $REGISTRY_CLUSTER_IP = Service $OWNER(DMS 레지스트리 — 재실행)" ;;
          *) F "REGISTRY_CLUSTER_IP $REGISTRY_CLUSTER_IP 를 Service $OWNER 가 쓴다 — 다른 정적 대역 주소로(아직 아무 데도 박히기 전이면)" ;;
        esac ;;
    "$DMS_NS/dms-registry") P "REGISTRY_CLUSTER_IP $REGISTRY_CLUSTER_IP = Service $PARENT(DMS 레지스트리 — 재실행)" ;;
    *) F "REGISTRY_CLUSTER_IP $REGISTRY_CLUSTER_IP 를 $PARENT 가 쓴다(IPAddress parentRef) — 다른 정적 대역 주소로" ;;
  esac
elif [ "$REGISTRY_MODE" = in-cluster ]; then
  F "REGISTRY_MODE=in-cluster 인데 REGISTRY_CLUSTER_IP 가 없다"
else
  I "REGISTRY_CLUSTER_IP 없음 — 사이트 레지스트리(external). lock 이미지(애드온)도 그 REGISTRY 로 push 한다(install.sh S5)"
fi

if [ "${PREFLIGHT_SKIP_DETECT:-0}" != 1 ]; then
  H "6. 애드온 감지 요약 (install-detect.py detect — 설계 8-2, 읽기 전용)"
  if command -v python3 >/dev/null 2>&1 && [ -f "$HERE/install-detect.py" ]; then
    RD=""; [ -z "$RENDER_ENV" ] || RD=$(dirname "$RENDER_ENV")
    # --namespace 를 명시한다 — env DMS_NS 를 읽으면 엉뚱한 ns 를 감지한다(2026-11-03 ⑦ 리뷰 1).
    if [ -n "$RD" ]; then OUT=$(python3 "$HERE/install-detect.py" --context "$CTX" --namespace "$DMS_NS" detect --rendered "$RD" 2>&1)
    else OUT=$(python3 "$HERE/install-detect.py" --context "$CTX" --namespace "$DMS_NS" detect 2>&1); fi
    DRC=$?
    printf '%s\n' "$OUT" | grep -E '^(DETECT|RESULT):' | sed 's/^/    /'
    case "$DRC" in
      0) P "애드온 감지 PASS" ;;
      3) W "애드온 감지 WARN(C!·D<·D>·U·채택) — python3 deploy/install-detect.py --context $CTX detect 로 사유 확인" ;;
      1) F "애드온 감지 FAIL(P·X·never) — python3 deploy/install-detect.py --context $CTX detect 로 사유 확인(P 는 install.sh --adopt <x> 로만 채택)" ;;
      4) W "애드온 감지 모름(읽기 실패 — 권한)" ;;
      *) I "애드온 감지를 돌리지 못했다(rc=$DRC)" ;;
    esac
  else
    W "python3 이 없다 — 설치기(install.sh)·감지·프로브가 python3(RHEL 9 = 3.9)을 쓴다(설계 S0-7)"
  fi
fi

H "7. 설치 호스트"
for t in python3 curl; do
  command -v "$t" >/dev/null 2>&1 && P "$t 있음" || F "$t 없음 — install.sh 가 쓴다(설계 S0-7: kubectl·curl·python3, 인터넷 노드는 skopeo·podman)"
done
if [ -n "${HTTPS_PROXY:-}${https_proxy:-}${HTTP_PROXY:-}${http_proxy:-}" ]; then
  W "설치 호스트에 프록시 env 가 있다 — kubectl(apiserver)·port-forward 가 프록시를 타지 않게 NO_PROXY 에 apiserver 주소를 둔다(install.sh 의 curl 은 프록시를 쓰지 않는다 — 127.0.0.1 port-forward)"
else
  I "설치 호스트 프록시 env 없음"
fi

H "8. 노드 셸 체크리스트 (설치자가 노드에서 돌려 확인 — 설계 8-1 S1)"
SD=${SHARED_DIR:-<SHARED_DIR>}
RIP=${REGISTRY_CLUSTER_IP:-<REGISTRY_CLUSTER_IP>}
RTAG=$(grep -v '^#' "$HERE/images.lock" 2>/dev/null | awk '$1 ~ /^registry:/ {print $1; exit}')
RTAG=${RTAG:-registry:<태그>}
M "RHEL 계열 DMS 노드 전부: getenforce; grep ^SELINUX= /etc/selinux/config   # Enforcing 이 아니고 config 도 enforcing 이 아님 — S2b 프로브 결과와 대조(설계 2-3)"
M "전 노드: findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS -T $SD   # TARGET 이 / 가 아님(공용 스토리지 마운트, G1·G2 — S0·S7)"
# external 사이트(REGISTRY_CLUSTER_IP 없음 — 사이트 레지스트리)에는 클러스터 내 레지스트리도 마스터 고정 이미지도 없다 — 자리표시자
# <REGISTRY_CLUSTER_IP> 줄 대신 노드가 그 REGISTRY 를 믿는지·런타임 NO_PROXY 가 그 호스트를 덮는지를 보인다(2026-11-03 ⑦ 리뷰 2).
if [ -z "$REGISTRY_CLUSTER_IP" ] && [ "$REGISTRY_MODE" != in-cluster ] && { [ "$REGISTRY_MODE" = external ] || [ -n "$REGISTRY" ]; }; then
  EREG=${REGISTRY:-<REGISTRY>}
  if [ "$REGISTRY_TLS_VERIFY" = true ]; then
    M "전 노드: 사이트 레지스트리 $EREG(TLS)의 CA 를 노드가 믿는가 — /etc/containers/certs.d/$EREG/ca.crt 또는 시스템 신뢰(update-ca-trust) — 그 뒤 systemctl reload crio"
  else
    M "전 노드: grep -rA2 '$EREG' /etc/containers/registries.conf /etc/containers/registries.conf.d/   # 평문이면 [[registry]] location=\"$EREG\" insecure=true — 그 뒤 systemctl reload crio"
  fi
  M "전 노드: sudo crio config 2>/dev/null | grep -E '^[[:space:]]*signature_policy'   # 서명 정책(reject 기본이면 $EREG 허용)"
  M "전 노드: sudo systemctl show crio --property=Environment | tr ' ' '\\n' | grep -i proxy   # 런타임 프록시가 있으면 NO_PROXY 에 ${EREG%:*}(설계 7-4)"
else
  M "전 노드: cat /etc/containers/registries.conf.d/50-dms-registry.conf   # [[registry]] location=\"$RIP:$REGISTRY_PORT\" insecure=true(S0-4) — 그 뒤 systemctl reload crio"
  M "전 노드: sudo crio config 2>/dev/null | grep -E '^[[:space:]]*(pinned_images|signature_policy)'   # 서명 정책(reject 기본이면 $RIP:$REGISTRY_PORT 허용), 마스터는 pinned_images 에 \"$RIP:$REGISTRY_PORT/registry:*\"(99-dms-pinned.conf)"
  M "마스터 전부: sudo crictl inspecti $RIP:$REGISTRY_PORT/$RTAG | grep -i pinned   # \"pinned\": true — 반입·고정한 레지스트리 자기 이미지(S0-5, kubelet GC 제외)"
  M "전 노드: sudo systemctl show crio --property=Environment | tr ' ' '\\n' | grep -i proxy   # 런타임 프록시가 있으면 NO_PROXY 에 $RIP(설계 7-4)"
fi
if [ "$GWPORT" -lt 1024 ]; then
  M "마스터 전부: sysctl -n net.ipv4.ip_unprivileged_port_start   # $GWPORT 이하(/etc/sysctl.d/90-dms-envoy.conf, S0-5 — install.sh S8 이 다시 잰다)"
else
  I "리스너 포트 $GWPORT ≥ 1024 — 마스터 sysctl ip_unprivileged_port_start(S0-5)는 필요 없다"
fi
M "마스터 전부(설치 전): ss -ltnH '( sport = :$GWPORT or sport = :19000 or sport = :19001 or sport = :19002 or sport = :19003 )'   # 비어 있어야 한다(K6)"
M "전 노드: sysctl fs.protected_hardlinks fs.protected_symlinks   # 둘 다 1(G11)"
M "공용 디렉터리 S0: 한 노드에서 root 로 sh deploy/shared-dir-init.sh $SD (이미 있으면 검사만) — 그 출력의 .dms-shared-id 가 values.env 의 SHARED_DIR_ID"
M "각 스토리지 managed_root 가 mkdir 됐는지는 노드에서 직접 확인(스크립트 밖). $SD/dms 와 그 상위·하위는 스토리지로 등록할 수 없다"

H "요약"
printf '  PASS %d · WARN %d · FAIL %d\n' "$pass" "$warn" "$fail"
if [ "$fail" -gt 0 ]; then echo "  => FAIL 항목을 먼저 해소해야 배포 가능"; exit 1
elif [ "$warn" -gt 0 ]; then echo "  => 배포 가능하나 WARN 항목을 확인/조정 권장"; exit 0
else echo "  => 프리플라이트 통과"; exit 0; fi
