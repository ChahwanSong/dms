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
warn(){ printf '  [WARN] %s\n' "$1"; }
die(){ printf '  [FAIL] %s\n' "$1" >&2; exit 1; }

# values.env 를 읽는다(push 단계의 REGISTRY/태그). render.sh 도 같은 파일을 쓴다.
VALS="${VALUES_ENV:-$HERE/overlays/prod/values.env}"
[ -f "$VALS" ] || die "values.env 없음 — cp overlays/prod/values.env.example overlays/prod/values.env 후 채우세요"
# shellcheck disable=SC1090
. "$VALS"

# --- 이미지 push (2026-09-09, 사용자 보고: 초기 구축에 push 단계가 없어 레지스트리가
#     비어 포탈 레지스트리/릴리스 화면이 오류를 보였다) ---
# 이 호스트의 podman/docker 에 있는 4종(dms·dms-agent·dms-mpifileutils·buildah)을
# values.env 의 REGISTRY/태그로 push 한다. **실패는 WARN 이고 설치는 계속된다**:
# 파드 구동은 노드에 반입(import)된 이미지로도 되므로(IfNotPresent) push 실패가
# 파드를 막지 않는다 -- 대신 포탈의 레지스트리/릴리스 화면과 포탈 빌드(FROM 이
# 레지스트리를 pull)는 push 가 있어야 정상이다. 로컬에 이미지가 없으면(반입만 한
# 경우) 그것도 WARN 으로 알리고 건너뛴다.
push_images(){
  tool=""
  command -v podman >/dev/null 2>&1 && tool=podman
  [ -z "$tool" ] && command -v docker >/dev/null 2>&1 && tool=docker
  if [ -z "$tool" ]; then
    warn "podman/docker 없음 — push 생략. 노드에 반입된 이미지면 파드 구동엔 영향 없음; 포탈 레지스트리 화면은 첫 push 전까지 비어 있음"
    return 0
  fi
  tlsv=""
  if [ "$tool" = podman ] && [ "${REGISTRY_TLS_VERIFY:-true}" = false ]; then tlsv="--tls-verify=false"; fi
  for spec in "dms:${DMS_TAG:-}" "dms-agent:${DMS_AGENT_TAG:-}" "dms-mpifileutils:${MFU_TAG:-}" "buildah:stable"; do
    case "$spec" in *:) warn "태그 미설정 — $spec 건너뜀"; continue;; esac
    ref="${REGISTRY:-}/$spec"
    if [ "$tool" = podman ]; then have=$(podman image exists "$ref" >/dev/null 2>&1 && echo 1 || echo 0)
    else have=$(docker image inspect "$ref" >/dev/null 2>&1 && echo 1 || echo 0); fi
    if [ "$have" != 1 ]; then
      warn "로컬 이미지 없음 $ref — push 생략(반입만 한 경우 정상; 파드 구동 무관, 포탈 빌드가 첫 push 를 만든다)"
      continue
    fi
    if [ "$DRY" = 1 ]; then ok "(dry-run) push 대상 $ref"; continue; fi
    if $tool push $tlsv "$ref" >/dev/null 2>&1; then
      ok "push $ref"
    else
      warn "push 실패 $ref — 레지스트리 도달/TLS(REGISTRY_TLS_VERIFY) 확인. 노드에 반입된 이미지면 파드 구동엔 영향 없음(포탈 레지스트리/빌드만 영향)"
    fi
  done
  # 레지스트리 시점 확인(정보): 리포별 태그 존재. 실패해도 설치는 계속.
  for repo in dms dms-agent dms-mpifileutils; do
    tags=$(curl -s -m5 "http://${REGISTRY:-}/v2/$repo/tags/list" 2>/dev/null | tr -d '\n' | cut -c1-120)
    case "$tags" in *'"tags":['*) ok "레지스트리 $repo: $tags";; *) warn "레지스트리 $repo 태그 없음/미도달(${tags:-no response}) — 포탈 레지스트리 화면이 비어 보일 수 있음";; esac
  done
}

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

step "1b. 이미지 push (레지스트리 ${REGISTRY:-?} — 실패해도 설치 계속, WARN 으로 구분)"
push_images

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
