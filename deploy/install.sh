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
  # 클러스터 내 레지스트리(2026-11-03 ④, REGISTRY_MODE=in-cluster): 이 호스트는 레지스트리 ClusterIP 에 닿지 않을 수 있고, 반입은
  # 인터넷 노드가 images.lock 으로 직접 push 한다(S5 — deploy/README §2e). 설치기 연결(S4 뒤 정지·--stage S5 재개·lock 대조)은 묶음 ⑦.
  if [ "${REGISTRY_MODE:-external}" = in-cluster ]; then
    info "REGISTRY_MODE=in-cluster — 이 호스트에서 push 하지 않는다(S5: 인터넷 노드가 images.lock·DMS 이미지를 레지스트리 ClusterIP 로 push, deploy/README §2e)"
    return 0
  fi
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
# dms-k8s: k8s 신원 모드 전용(LDAP 없음) -- 시크릿은 4키(바인드 비밀번호 키 없음).
for k in DMS_DATABASE_URL DMS_SHARED_TOKEN DMS_ADMIN_TOKEN DMS_SESSION_SECRET; do
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

step "2. 렌더 (values.env → deploy/.rendered/prod)"
R=$(sh "$HERE/render.sh" prod) || { printf '%s\n' "$R" >&2; die "render 실패(위 사유)"; }
DIR=$(printf '%s\n' "$R" | sed -n 's/^RENDERED: //p')
[ -n "$DIR" ] || die "렌더 디렉터리 확인 불가"
ok "렌더 완료: $DIR"

# 클러스터 내 레지스트리 게이트(2026-11-03 ④): 앱 이미지가 그 레지스트리를 가리키는데 레지스트리가 없거나 주소가 다르면 모든 새 파드가
# ImagePullBackOff 다 — apply 전에 멈춘다. 플랫폼 층 적용(S4: PriorityClass·레지스트리 SSA, http 시크릿)과 S5 push·S6 pull 확인은 아직
# 런북(deploy/README §2e)이다 — 설치기 연결은 묶음 ⑦.
RENV="${DIR%/app}/render.env"
RMODE=$(sed -n 's/^REGISTRY_MODE=//p' "$RENV" 2>/dev/null || true)
if [ "$RMODE" = in-cluster ]; then
  RIP=$(sed -n 's/^REGISTRY_CLUSTER_IP=//p' "$RENV")
  RPORT=$(sed -n 's/^REGISTRY_PORT=//p' "$RENV")
  LIVE_IP=$(kubectl -n "$NS" get svc dms-registry -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)
  [ -n "$LIVE_IP" ] || die "REGISTRY_MODE=in-cluster 인데 Service dms-registry 가 없다 — 먼저 S4(deploy/README §2e: 플랫폼 층 SSA·http 시크릿)와 S5 push"
  [ "$LIVE_IP" = "$RIP" ] || die "Service dms-registry 의 clusterIP $LIVE_IP 가 values.env 의 REGISTRY_CLUSTER_IP $RIP 와 다르다(주소는 바꾸지 않는 계약 — 설계 v6 7-4)"
  READY=$(kubectl -n "$NS" get deploy dms-registry -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)
  [ "${READY:-0}" -ge 1 ] 2>/dev/null || die "dms-registry 가 Ready 가 아니다 — deploy/README §2e 의 S4 확인(센티널 없음·불일치는 init 로그)"
  ok "클러스터 내 레지스트리: $RIP (Ready)"
  # 앱 이미지 태그(2026-11-03 ④ 리뷰 2): Service·Ready 만 보면 S5(DMS 이미지 push)를 빠뜨렸거나 태그가 레지스트리에 없어도 apply 까지 가서
  # migrate Job 이 ImagePullBackOff → 4단계 wait 의 180초 일반 타임아웃으로 끝난다(머리 주석의 「한 줄 사유」에 어긋남). port-forward 는
  # apiserver 를 거치므로 이 호스트가 ClusterIP 에 닿지 않아도 된다. 태그는 render.env 의 해석값('live' 면 클러스터에서 읽은 태그).
  # 애드온 이미지의 lock digest 대조(S5)는 ⑦ 몫이다.
  command -v curl >/dev/null 2>&1 || die "curl 없음 — 클러스터 내 레지스트리의 태그 확인에 쓴다(설치 호스트 도구, 설계 S0-7)"
  PFLOG=$(mktemp) || die "임시 파일을 만들지 못했다(mktemp)"
  kubectl -n "$NS" port-forward svc/dms-registry ":${RPORT:-5000}" >"$PFLOG" 2>&1 &
  PF=$!
  trap 'kill "$PF" 2>/dev/null; rm -f "$PFLOG"' EXIT
  # 무작위 로컬 포트(고정 포트가 이미 쓰이면 엉뚱한 서비스에 묻게 된다) — listen 한 뒤에 찍히는 「Forwarding from 127.0.0.1:<port>」 를 기다린다.
  LPORT=""
  for _i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    LPORT=$(sed -n 's/^Forwarding from 127\.0\.0\.1:\([0-9][0-9]*\) .*/\1/p' "$PFLOG" | head -n 1)
    [ -z "$LPORT" ] || break
    kill -0 "$PF" 2>/dev/null || break
    sleep 1
  done
  [ -n "$LPORT" ] || die "레지스트리 port-forward 가 열리지 않았다 — $(tr '\n' ' ' < "$PFLOG" | cut -c1-200)"
  ACCEPT='application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json'
  missing=""
  for spec in "dms:$(sed -n 's/^DMS_TAG=//p' "$RENV")" "dms-agent:$(sed -n 's/^DMS_AGENT_TAG=//p' "$RENV")" \
              "dms-mpifileutils:$(sed -n 's/^MFU_TAG=//p' "$RENV")"; do
    case "$spec" in *:) missing="$missing ${spec}(태그 없음 — render.env)"; continue ;; esac
    code=$(curl -s -m 10 -o /dev/null -w '%{http_code}' -I -H "Accept: $ACCEPT" \
      "http://127.0.0.1:$LPORT/v2/${spec%%:*}/manifests/${spec#*:}" 2>/dev/null || true)
    if [ "$code" = 200 ]; then ok "레지스트리에 $spec 있음"; else missing="$missing ${spec}(HTTP ${code:-?})"; fi
  done
  kill "$PF" 2>/dev/null || true
  rm -f "$PFLOG"
  trap - EXIT
  [ -z "$missing" ] || die "S5 미완: 클러스터 내 레지스트리에 없는 이미지 —$missing. deploy/README §2e-4 의 S5(DMS 이미지 push)를 마치거나 values.env 의 태그를 레지스트리에 있는 값으로"
fi

if [ "$DRY" = 1 ]; then
  step "3. DRY-RUN (서버측 검증 — 변경 없음)"
  kubectl apply -k "$DIR" --dry-run=server >/dev/null \
    && ok "서버측 apply 검증 통과" || die "서버측 검증 실패"
  printf '\nDRY-RUN 통과 — 실제 설치는 --dry-run 없이 재실행.\n'
  exit 0
fi

# 이미지 변경 가드(2026-09-14): 포탈 릴리스 뒤 values.env 태그를 안 맞춘 채 install.sh 를
# 다시 돌리면 라이브 이미지가 옛 태그로 되돌아간다 -- 렌더 이미지 != 라이브면 거부한다.
# 의도한 이미지 변경(업그레이드)은 ALLOW_IMAGE_CHANGE=1 sh install.sh 로 명시한다.
# hostPath 가드(2026-11-03 ③): 렌더 hostPath(공용 디렉터리 배치 <SHARED_DIR>/dms/...) != 라이브면 exit 5 로 거부한다 -- SHARED_DIR
# 이행·변경은 노드 마운트·S0·프로브·복사 뒤에 ALLOW_HOSTPATH_CHANGE=1 로 명시한다(deploy/README.md 전환 런북).
step "2b. 이미지·hostPath 변경 가드 (렌더 != 라이브면 거부; ALLOW_IMAGE_CHANGE=1 / ALLOW_HOSTPATH_CHANGE=1 로 허용)"
sh "$HERE/overlays/guard-images.sh" "$DIR" || die "가드 거부 -- 위 [guard] 안내대로: 이미지(exit 3)는 values.env 의 DMS_TAG/DMS_AGENT_TAG 를 라이브에 맞추거나 ALLOW_IMAGE_CHANGE=1, hostPath(exit 5)는 공용 디렉터리 전환 런북을 마친 뒤 ALLOW_HOSTPATH_CHANGE=1 로 재실행"

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
