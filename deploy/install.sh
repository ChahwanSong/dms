#!/bin/sh
# DMS 프로덕션 설치 — 원커맨드·멱등(2026-08-30). 설치 에이전트의 토큰을 줄이는 게
# 목적이다: apply 전에 치명 오류(시크릿·TLS·애드온·미치환 값)를 **한 줄 사유로**
# 걸러 파드 CrashLoop→로그 트롤링→재시도 루프를 없애고, 통과하면 렌더→apply→
# 대기까지 한 번에 한다. 성공/실패가 컴팩트한 단계 마커로 나와 대형 출력 파싱이
# 필요 없다.
#
# 사용:
#   sh deploy/install.sh --dry-run   # 게이트+렌더+서버측 dry-run(변경 없음)
#   sh deploy/install.sh             # 실제 설치
# 전제: overlays/prod/values.env 채움 + dms-secrets·dms-portal-tls 생성
#       (overlays/prod/README.md). NS/INGRESS_NS 로 네임스페이스 조정 가능.
set -eu
NS="${DMS_NS:-dms}"
INGRESS_NS="${INGRESS_NS:-ingress-nginx}"
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)      # deploy
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
step(){ printf '\n== %s ==\n' "$1"; }
ok(){ printf '  [OK] %s\n' "$1"; }
info(){ printf '  [INFO] %s\n' "$1"; }
die(){ printf '  [FAIL] %s\n' "$1" >&2; exit 1; }

step "1. 게이트 (apply 전 치명 오류 차단)"
kubectl version >/dev/null 2>&1 || die "kubectl 이 클러스터에 못 붙음 — kubeconfig 확인"
# 애드온(오버레이가 설치하지 않는 선행 컴포넌트)
kubectl get crd jobs.batch.volcano.sh >/dev/null 2>&1 \
  || die "Volcano 미설치 (jobs.batch.volcano.sh CRD 없음)"
kubectl get ns metallb-system >/dev/null 2>&1 \
  || die "MetalLB 미설치 (metallb-system 네임스페이스 없음)"
kubectl get ingressclass nginx >/dev/null 2>&1 \
  || die "ingress-nginx 미설치 (IngressClass nginx 없음)"
ok "애드온: Volcano · MetalLB · ingress-nginx"
# 시크릿 — 고전적 CrashLoop 원인(placeholder 로 뜨면 제어면 전체가 죽는다)
kubectl -n "$NS" get secret dms-secrets >/dev/null 2>&1 \
  || die "dms-secrets 없음 — overlays/prod/README.md §Secret 로 먼저 생성"
SEC=$(kubectl -n "$NS" get secret dms-secrets \
  -o go-template='{{range $k,$v := .data}}{{$k}}={{$v|base64decode}}{{"\n"}}{{end}}' 2>/dev/null || true)
for k in DMS_DATABASE_URL DMS_SHARED_TOKEN DMS_ADMIN_TOKEN DMS_SESSION_SECRET DMS_LDAP_BIND_PW; do
  printf '%s\n' "$SEC" | grep -q "^$k=" || die "dms-secrets 에 $k 없음"
done
printf '%s\n' "$SEC" | grep -Eq 'CHANGE_ME|REPLACE_WITH_' \
  && die "dms-secrets 에 자리표시자(CHANGE_ME/REPLACE_WITH_) 남음 — 실제 값 주입"
ok "dms-secrets: 필수 키 존재 · 자리표시자 없음"
# TLS(ingress default-ssl-certificate)
kubectl -n "$NS" get secret dms-portal-tls >/dev/null 2>&1 \
  || die "dms-portal-tls(TLS) 없음 — overlays/prod/README.md §TLS 로 먼저 생성"
ok "dms-portal-tls 존재"

step "2. 렌더 (values.env → .prod-rendered)"
R=$(sh "$HERE/overlays/prod/render.sh") || { printf '%s\n' "$R" >&2; die "render 실패(위 사유)"; }
DIR=$(printf '%s\n' "$R" | sed -n 's/^RENDERED: //p')
[ -n "$DIR" ] || die "렌더 디렉터리 확인 불가"
ok "렌더 완료: $DIR"

if [ "$DRY" = 1 ]; then
  step "3. DRY-RUN (서버측 검증 — 변경 없음)"
  kubectl apply -k "$DIR" --dry-run=server >/dev/null \
    && ok "서버측 apply 검증 통과" || die "서버측 검증 실패"
  printf '\nDRY-RUN 통과 — 실제 설치는 --dry-run 없이 재실행.\n'
  exit 0
fi

step "3. 적용 (kubectl apply -k)"
# migrate Job 은 spec 불변이라 이미지 태그가 바뀌면 apply 가 못 고친다 — 재실행
# 시 새 이미지로 다시 돌도록 먼저 지운다(멱등, 없으면 무시).
kubectl -n "$NS" delete job dms-migrate --ignore-not-found >/dev/null 2>&1 || true
kubectl apply -k "$DIR"

step "4. migrate 완료 대기"
kubectl -n "$NS" wait --for=condition=complete job/dms-migrate --timeout=180s

step "5. 롤아웃 대기"
kubectl -n "$NS" rollout status deploy/dms-api --timeout=180s
kubectl -n "$NS" rollout status deploy/dms-controller --timeout=180s
kubectl -n "$NS" rollout status ds/dms-agent --timeout=180s

step "6. ingress 풀 어노테이션 (멱등)"
kubectl -n "$INGRESS_NS" annotate svc ingress-nginx-controller \
  metallb.io/address-pool=dms-public-pool --overwrite >/dev/null
ok "ingress-nginx-controller → dms-public-pool"

printf '\n설치 완료. 검증:  PORTAL_DOMAIN=<도메인> CACERT=<사내CA> sh deploy/verify.sh\n'
