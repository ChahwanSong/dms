#!/bin/sh
# DMS 프로덕션 배포 프리플라이트 점검(2026-08-20). READ-ONLY -- kubectl get/describe
# 만 쓰고 클러스터를 절대 변경하지 않는다. 앞선 검토(Volcano/MetalLB/ingress-nginx
# 가 이미 설치된 경우의 확인 항목)를 자동 점검해 PASS/WARN/FAIL 로 보고한다.
#
# 사용:  sh deploy/preflight-cluster.sh
# 전제:  대상 클러스터를 가리키는 kubeconfig (kubectl 이 현재 컨텍스트로 붙는 곳).
#
# 판정: [PASS] 그대로 진행 가능 · [WARN] 조정/확인 필요(치명 아님) · [FAIL] 막힘
#       [INFO] 참고 · [MANUAL] 스크립트로 못 보는 항목(노드 접근 필요 등)
set -u

# --- 채워두면 더 정확히 검사(비워도 동작) ---
DMS_NS="${DMS_NS:-dms}"
PORTAL_VIP="${PORTAL_VIP:-}"          # 예: 203.0.113.10 (정하면 충돌·존 검사 강화)
INGRESS_NS="${INGRESS_NS:-ingress-nginx}"

pass=0; warn=0; fail=0
P(){ printf '  [PASS] %s\n' "$1"; pass=$((pass+1)); }
W(){ printf '  [WARN] %s\n' "$1"; warn=$((warn+1)); }
F(){ printf '  [FAIL] %s\n' "$1"; fail=$((fail+1)); }
I(){ printf '  [INFO] %s\n' "$1"; }
M(){ printf '  [MANUAL] %s\n' "$1"; }
H(){ printf '\n== %s ==\n' "$1"; }
has_kind(){ kubectl get "$1" >/dev/null 2>&1; }        # CRD/리소스 종류 존재?
crd(){ kubectl get crd "$1" >/dev/null 2>&1; }

kubectl version >/dev/null 2>&1 || { echo "kubectl 이 클러스터에 붙지 못했습니다 -- kubeconfig 확인"; exit 2; }

H "0. 클러스터 / 컨텍스트"
CTX=$(kubectl config current-context 2>/dev/null || echo "?")
I "current-context: $CTX"
SVER=$(kubectl version -o json 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin)['serverVersion']['gitVersion'])" 2>/dev/null || echo "?")
I "server version: $SVER  (이미지 kubectl 은 v1.34.x)"
case "$SVER" in v1.3[0-9]*|v1.4*) P "k8s 버전 호환 범위";; *) W "k8s 버전 $SVER -- 1.30+ 권장, CRD apiVersion 확인 필요";; esac
I "CNI: 코드가 cilium/calico 인터페이스를 인식하므로 CNI 종류 제약 없음(Cilium OK)"

H "1. Volcano (잡 gang 스케줄러)"
if crd jobs.batch.volcano.sh && crd queues.scheduling.volcano.sh; then
  P "Volcano CRD 존재 (jobs.batch.volcano.sh, queues.scheduling.volcano.sh)"
  VVER=$(kubectl get deploy -A -l app.kubernetes.io/name=volcano-scheduler -o jsonpath='{.items[0].spec.template.spec.containers[0].image}' 2>/dev/null)
  [ -n "$VVER" ] && I "scheduler image: $VVER (테스트베드 검증 v1.15.0)"
  # 코드가 쓰는 apiVersion 이 CRD 에 있는지
  kubectl get crd jobs.batch.volcano.sh -o jsonpath='{.spec.versions[*].name}' 2>/dev/null | grep -qw v1alpha1 \
    && P "batch.volcano.sh/v1alpha1 지원(코드가 제출하는 버전)" \
    || W "jobs.batch.volcano.sh 에 v1alpha1 이 안 보임 -- Volcano 버전 확인"
  # 우리 리소스(05-…yaml)가 만들 것들 -- 이름 충돌 검사
  kubectl get queue dms-data >/dev/null 2>&1 \
    && I "Queue 'dms-data' 이미 존재 -- 05-…yaml apply 가 덮어씀(우리 리소스면 OK)" \
    || P "Queue 'dms-data' 없음 -- 05-…yaml 이 생성"
else
  F "Volcano 미설치 -- 설치 필요(잡이 영원히 Pending). CRD jobs.batch.volcano.sh 없음"
fi
for pc in dms-low dms-mid dms-high dms-build; do
  kubectl get priorityclass "$pc" >/dev/null 2>&1 && W "PriorityClass '$pc' 이미 존재 -- 05-…yaml 이 덮어씀(값 확인)" || :
done
[ "$warn" -eq 0 ] || true

H "2. MetalLB (포탈 VIP)"
if crd ipaddresspools.metallb.io; then
  P "MetalLB CRD 존재 (metallb.io/v1beta1)"
  N_L2=$(kubectl -n metallb-system get l2advertisements.metallb.io --no-headers 2>/dev/null | wc -l | tr -d ' ')
  N_BGP=$(kubectl -n metallb-system get bgpadvertisements.metallb.io --no-headers 2>/dev/null | wc -l | tr -d ' ')
  I "기존 L2Advertisement ${N_L2}개 · BGPAdvertisement ${N_BGP}개"
  if [ "$N_L2" -gt 0 ]; then
    P "L2 모드 가능(L2Advertisement 존재) -- 우리 47-…yaml 은 L2"
  elif [ "$N_BGP" -gt 0 ]; then
    W "BGP만 구성됨 -- 우리 47-…yaml 은 L2Advertisement 라 무효. BGPAdvertisement 로 바꿔야(검증본 있음)"
  else
    I "광고 리소스 없음 -- 우리 47-…yaml 이 L2Advertisement 를 만든다(L2 모드 전제)"
  fi
  # blanket L2Advertisement(pool 제한 없음) -- 우리 공인존 제약을 무력화
  BLANKET=$(kubectl -n metallb-system get l2advertisements.metallb.io -o json 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
n=[i['metadata']['name'] for i in d.get('items',[]) if not i.get('spec',{}).get('ipAddressPools')]
print(' '.join(n))" 2>/dev/null)
  [ -n "$BLANKET" ] && W "pool 제한 없는 L2Advertisement 존재($BLANKET) -- 우리 공인존 제약(nodeSelector)이 무력화됨. 이 광고에 ipAddressPools 를 지정하거나 우리 풀을 그 범위서 뺄 것" \
                    || P "blanket L2Advertisement 없음 -- 공인존 제약이 유효"
  # 우리 풀 이름 / VIP 충돌
  kubectl -n metallb-system get ipaddresspool dms-public-pool >/dev/null 2>&1 \
    && I "IPAddressPool 'dms-public-pool' 이미 존재 -- 47-…yaml 이 덮어씀" \
    || P "IPAddressPool 'dms-public-pool' 없음 -- 47-…yaml 이 생성"
  if [ -n "$PORTAL_VIP" ]; then
    kubectl -n metallb-system get ipaddresspools.metallb.io -o json 2>/dev/null | grep -q "$PORTAL_VIP" \
      && W "VIP $PORTAL_VIP 가 기존 풀에 이미 있음 -- 중복 소유 확인" \
      || P "VIP $PORTAL_VIP 가 기존 풀과 겹치지 않음"
  else
    I "PORTAL_VIP 미설정 -- VIP 충돌 검사 생략(PORTAL_VIP=… 로 재실행하면 검사)"
  fi
else
  F "MetalLB 미설치 -- 설치 필요(LoadBalancer 서비스에 IP 안 붙음)"
fi

H "3. ingress-nginx (포탈 HTTPS)"
if kubectl get ingressclass nginx >/dev/null 2>&1; then
  P "IngressClass 'nginx' 존재"
  CTRL=$(kubectl -n "$INGRESS_NS" get deploy -l app.kubernetes.io/component=controller -o name 2>/dev/null | head -1)
  if [ -n "$CTRL" ]; then
    I "controller: $INGRESS_NS/$CTRL"
    MGR=$(kubectl -n "$INGRESS_NS" get "$CTRL" -o jsonpath='{.metadata.labels.app\.kubernetes\.io/managed-by}' 2>/dev/null)
    [ -n "$MGR" ] && W "컨트롤러가 '$MGR' 로 관리됨 -- --default-ssl-certificate arg·svc 어노테이션을 그쪽(values)에 반영해야 sync 때 revert 안 됨" \
                  || I "컨트롤러 관리 주체 라벨 없음(수동 설치로 보임) -- kubectl 로 직접 패치 가능"
    kubectl -n "$INGRESS_NS" get "$CTRL" -o jsonpath='{.spec.template.spec.containers[0].args}' 2>/dev/null | grep -q default-ssl-certificate \
      && P "--default-ssl-certificate 이미 설정됨(IP·SNI 없는 https 접속 지원)" \
      || W "--default-ssl-certificate 미설정 -- IP 직접 https 접속에 필요. 도메인 접속만 쓰면 생략 가능"
  else
    W "controller Deployment 를 $INGRESS_NS 에서 못 찾음 -- INGRESS_NS=… 로 네임스페이스 지정해 재실행"
  fi
  # 공유 ingress 위험: 우리 catch-all(host 없는 규칙)이 다른 앱 트래픽을 가로챌 수
  TOTAL_ING=$(kubectl get ingress -A --no-headers 2>/dev/null | wc -l | tr -d ' ')
  CATCHALL=$(kubectl get ingress -A -o json 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
out=[]
for i in d.get('items',[]):
    for r in i.get('spec',{}).get('rules',[]):
        if not r.get('host'):
            out.append(i['metadata']['namespace']+'/'+i['metadata']['name'])
print(' '.join(sorted(set(out))))" 2>/dev/null)
  I "클러스터 전체 Ingress ${TOTAL_ING}개"
  if [ "$TOTAL_ING" -gt 0 ]; then
    W "이미 다른 Ingress 가 있음 = 공유 ingress일 수 있음. 우리 46-…yaml 의 catch-all(host 없는 규칙)이 다른 앱의 미매칭 트래픽을 가로챌 수 있다 -- 공유면 host 기반으로 바꿀 것(patch-ingress 에 rules host 추가)"
    [ -n "$CATCHALL" ] && I "현재 host 없는(catch-all) Ingress: $CATCHALL"
  else
    P "다른 Ingress 없음 -- 전용 ingress로 보임(catch-all 안전)"
  fi
else
  F "IngressClass 'nginx' 없음 -- ingress-nginx 설치/클래스명 확인"
fi

H "4. Pod Security Admission (잡 파드 root 실행)"
if kubectl get ns "$DMS_NS" >/dev/null 2>&1; then
  ENF=$(kubectl get ns "$DMS_NS" -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}' 2>/dev/null)
  case "$ENF" in
    privileged) P "ns '$DMS_NS' enforce=privileged (잡 파드 root 실행 허용)";;
    "") W "ns '$DMS_NS' 존재하나 PSA enforce 라벨 없음 -- 00-namespace.yaml 이 privileged 로 설정(클러스터 기본 PSA 가 restricted 면 그 전에 잡 파드 거부)";;
    *) F "ns '$DMS_NS' enforce=$ENF -- 잡 파드가 root 로 떠서 거부됨. privileged 필요(사내 보안정책 확인 포인트)";;
  esac
else
  I "ns '$DMS_NS' 아직 없음 -- 00-namespace.yaml 이 enforce=privileged 로 생성. 사내 정책이 privileged ns 를 허용하는지 확인"
fi

H "5. 공인 존 노드 라벨 / 스토리지 (부분 자동)"
NPUB=$(kubectl get nodes -l network-zone=public --no-headers 2>/dev/null | wc -l | tr -d ' ')
[ "$NPUB" -gt 0 ] && P "network-zone=public 노드 ${NPUB}개" \
                  || W "network-zone=public 라벨 노드 없음 -- 공인 라우팅 닿는 노드에 라벨 필요(VIP 광고 자격). kubectl label node <노드> network-zone=public"
M "공유 FS 마운트: 컨트롤러/잡 노드에 스토리지가 물려 있고 <base>/dms/artifacts, 각 managed_root 가 mkdir 됐는지는 노드에서 직접 확인(스크립트 밖)"
M "base hostPath 경로(/cephfs 등)가 사내 실제 마운트 경로와 같은지 -- 다르면 base 수정/hostPath 패치 필요"

H "요약"
printf '  PASS %d · WARN %d · FAIL %d\n' "$pass" "$warn" "$fail"
if [ "$fail" -gt 0 ]; then echo "  => FAIL 항목을 먼저 해소해야 배포 가능"; exit 1
elif [ "$warn" -gt 0 ]; then echo "  => 배포 가능하나 WARN 항목을 확인/조정 권장"; exit 0
else echo "  => 프리플라이트 통과"; exit 0; fi
