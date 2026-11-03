#!/bin/sh
# DMS 설치기 — 단계(S1~S13)·감지 상태 기계·멱등(2026-08-30 원커맨드 → 2026-11-03 ⑦ 설계 v6 8-1·8-2 단계 설치기로 다시 씀).
#
#   sh deploy/install.sh --context <ctx> [--site prod] [--plan]
#   sh deploy/install.sh --context <ctx> [--site prod] [--stage <S>] [--only <S>[,<S>…]] [--upgrade-addon <x>]… [--adopt <x>]…
#
#   --context <ctx>  **필수**(2026-11-03 ⑦): 모든 kubectl·도우미에 그대로 붙인다. current-context·env KUBE_CONTEXT 에 기대지 않는다(두
#                    테스트베드가 같은 네임스페이스 — 엉뚱한 클러스터에 설치하는 것이 가장 비싼 사고다). 렌더의 kube-context(고정 오버레이)와
#                    다르면 exit 2.
#   --site <site>    deploy/overlays/<site>(기본 prod). values 는 deploy/overlays/<site>/values.env(env VALUES_ENV 로 바꾼다 — render.sh 규칙).
#   --plan           = S1(읽기 전용 점검) + S2(확인만 — 만들지 않는다) + S3(모든 층 렌더·server dry-run·애드온 판정 표). **클러스터를 바꾸지 않는다.**
#   --stage <S>      S 부터 끝까지(재개 — 예: S4 뒤 멈춘 설치를 인터넷 노드 push 뒤 `--stage S5` 로).
#   --only <S>[,…]   그 단계만(설치 순서대로). 이미 끝난 단계를 다시 돌리면 no-op 이거나 검증이다(아래 「멱등」).
#   --upgrade-addon <x>  D<(DMS 가 설치한 낮은 버전)를 올린다 / D= 를 다시 적용한다(설계 6-5 — 점검 창에서). x(감지 항목, 쉼표 가능) =
#                    priorityclass·registry·gateway-api·envoy-gateway·gateway·cnpg·volcano·db — 그 층의 이미지 가드(exit 3)를 그 층에서만 연다.
#   --adopt <x>      P(부분 설치·이름 충돌) 가운데 채택할 수 있는 모양만 dms-install 소유로(SSA --force-conflicts).
#   env ADDON_INSTALL=never  서드파티 애드온이 없으면(A) 설치하지 않고 FAIL + 「클러스터 운영자에게 요청」(기본 auto).
#   env ALLOW_IMAGE_CHANGE=1 / ALLOW_HOSTPATH_CHANGE=1  guard-images.sh 의 exit 3·5 를 연다(의도한 앱 이미지·hostPath 변경 — 전역이라 모든 층의
#                    가드를 연다. 애드온·플랫폼 층의 이미지 교체는 --upgrade-addon <x> 로 그 층만, ⑦ 리뷰 2).
#   env PORTAL_DOMAIN·PORTAL_VIP·CACERT  S12 의 `curl --resolve` 대상(없으면 render.env 의 값 — prod 는 values.env 의 PORTAL_DOMAIN·PORTAL_VIP).
#   env VERIFY_NO_PROBES=1  S12 에서 파드를 띄우는 검사(PG 경로 대조·마스터 :443)를 건너뛴다(라이브 사이트를 순수 읽기로 다시 볼 때).
#   env SELINUX_PROBE_IMAGE=<ref>  S2b 프로브 이미지(모든 노드에 이미 있는 셸 포함 이미지 — 기본은 Cilium agent 이미지를 찾는다).
#   env 시간(초): DB_WAIT_SECONDS(900 — S10 폴링·CNPG 업그레이드 뒤 healthy) · ROLLOUT_TIMEOUT(180) · MIGRATE_TIMEOUT(180) · DUMP_TIMEOUT(600) ·
#       CNPG_ROLLOUT_TIMEOUT(300) · CERTGEN_TIMEOUT(120) · WEBHOOK_RETRIES(6 — S9 Cluster CR dry-run 의 웹훅 재시도) · POLL_INTERVAL. PYTHON(python3).
#
# 단계(설계 8-1 — 순서의 이유는 설계 「순서의 이유」):
#   S1  preflight(preflight-cluster.sh — K8s minor 1.34~1.36·SHARED_DIR 형식·REGISTRY_CLUSTER_IP(ServiceCIDR·미사용·하단 정적 대역)·PSA·admission·
#       노드 셸 체크리스트) + 감지(install-detect.py detect — 표). FAIL·P·X 면 중단.
#   S2  deploy/k8s/00-namespace.yaml(PSA privileged) → dms-pg-app(pg-secrets ensure-app) → dms-secrets(없으면 init-secrets) → 4키·자리표시자·
#       pg-secrets check(설계 6-3) → PORTAL_TLS_SECRET(render.env) 확인(없으면 WARN — 쓰는 곳인 S8 이 멈춘다, ⑦ 리뷰 1). 값은 출력하지 않는다.
#   S2b SELinux 노드 프로브(selinux-probe.py — DMS 노드, privileged 임시 DS). FAIL·모름이면 중단.
#   S3  계획(install-detect.py plan — 층별 SSA server dry-run). FAIL 이면 중단.
#   S4  PriorityClass dms-addon-critical SSA → (in-cluster, 적용할 때만) guard → http 시크릿(없을 때만·출력 없음) → platform/registry SSA → rollout
#       → port-forward `/v2/` 200. external(REGISTRY_CLUSTER_IP 없음)이면 레지스트리는 건너뛴다.
#   S5  lock 대조(images-lock.py compare-registry — port-forward) + DMS 앱 이미지(dms·dms-agent·dms-mpifileutils) 태그 확인. 없거나 다르면 push 명령
#       (push-images.sh --print)을 보이고 — S4 에서 이어 왔으면 **PAUSE(exit 10, 인터넷 노드 push 뒤 `--stage S5`)**, 아니면 FAIL.
#   S6  pull 프로브(pull-probe.py — 노드마다 실제 pull).            S7  공용 스토리지 프로브(shared-storage-probe.py — 3 WARN 은 계속, 1·4 중단).
#   S8  Gateway API → EG(감지대로 — DMS 소유인데 적용하지 않으면 Ready·xDS 시크릿 확인만) → TLS 시크릿 → 마스터 sysctl ≤ 443(host-net-probe.sh)
#       → platform/gateway → Programmed → 마스터마다 :443.
#   S9  CNPG(감지대로: A 면 ns 먼저(DMS 라벨) → SSA → rollout) → Cluster CR server dry-run(9443 경로 — 「failed calling webhook」이면 9443 사유).
#   S9b Volcano(감지대로 — C 면 건드리지 않는다) → DMS 모양 vcjob server dry-run(install-detect.py vcjob).
#   S10 pv-check → db-volumes → precheck → db-cluster → 900초 폴링(readyInstances ≥ 1·dms-pg-rw Endpoints·currentPrimary Ready — 전부 Ready 를
#       기다리되 시간 초과에 ≥ 1 이면 WARN, 0 이면 FAIL) → 경로 대조(contrast) → db-backup → 첫 덤프(Complete·Failed 를 함께 본다).
#   S11 시크릿 게이트 → 앱 이미지 확인 → guard-images(3·4·5) → migrate Job 지움 → `apply -k`(client-side — 포탈 릴리스가 strategic-merge 로
#       image 를 바꾼다) → migrate 완료·롤아웃 대기 → 설정(dms-config·dms-secrets 의 resourceVersion)이 바뀌었는데 파드가 그대로면 rollout restart
#       → 마스터 `dms` 이미지 사전 pull(pull-probe.py --nodes <마스터>, 실패는 WARN).
#   S12 verify.sh(--rendered) — readyz(apiserver 서비스 프록시 = 판정, --resolve VIP = Avi 미개통이면 WARN)·CNPG·여분 PV·경로 대조·CronJob·
#       Gateway·마스터 :443·레지스트리·애드온 요약·「80 불가 = 정상」. FAIL 이어도 S13 체크리스트를 보이고 멈춘다.
#   S13 사후 체크리스트(출력만 — 첫 관리자 부트스트랩·Avi·DNS·포탈 등록).
#
# 재개 안내(PAUSE·모름·FAIL 줄의 명령)는 이번 실행의 --upgrade-addon·--adopt 와 판정을 바꾸는 env(ADDON_INSTALL·ALLOW_*·SELINUX_PROBE_IMAGE·
# VALUES_ENV·S12 의 CACERT·PORTAL_DOMAIN·PORTAL_VIP·VERIFY_NO_PROBES)를 그대로 싣는다(⑦ 리뷰 1·2). --plan 이 보이는 다시 확인 명령은 늘 --plan 이다
# (변경하는 --stage 를 권하지 않는다 — ⑦ 리뷰 2). env DMS_NS 는 읽지 않는다(ns dms 고정 — 도우미에도 --namespace dms).
#
# 렌더: 매 실행(--plan·--only 포함) 처음에 deploy/render.sh <site> 로 deploy/.rendered/<site> 를 통째로 다시 만든다 — 그 아래를 손으로 고친
# 값은 설치기로 적용되지 않는다. 사이트 값은 values.env 키로만(키가 없으면 docs/BACKLOG.md 「⑦ 설치기 후속」, 2026-11-03 ⑦ 리뷰 2).
#
# 멱등(2026-11-03 ⑦): 끝난 단계를 다시 돌려도 바뀌는 것이 없다 — SSA 는 같은 값이면 no-op, 서드파티 D=·C 는 적용하지 않는다(C 는 dry-run 도 안 함),
# 시크릿·http 시크릿은 없을 때만 만들고, 첫 덤프는 성공한 덤프가 있으면 건너뛰고, 앱 apply 는 guard 가 이미지·hostPath 되돌림을 막고, rollout
# restart 는 설정이 실제로 바뀐 경우에만 한다. S4 뒤 멈춤도 레지스트리에 이미 lock 이 다 있으면 멈추지 않는다.
#
# 층의 소유(설계 8-2): addons·platform 층은 SSA(field manager `dms-install`), 앱 오버레이는 client-side apply. **C(남이 설치한 호환 애드온)는
# 다시 설치하지 않는다** — 그 층에는 apply 도 dry-run 도 부르지 않는다.
#
# 출력: 단계마다 `== S<n> … ==` 와 [OK]/[INFO]/[WARN]/[FAIL] 줄, 끝에 `STAGE: <S> <PASS|WARN|SKIP|PAUSE|FAIL|UNKNOWN>`. 마지막 줄 `RESULT: …`.
#   도우미 출력은 4칸 들여 쓴다 — 0열의 `==`·`STAGE:`·`RESULT:` 는 설치기 자신의 줄뿐이다(도우미의 RESULT 를 설치 결과로 읽지 않게). preflight(S1)·
#   verify(S12)의 [WARN] 줄은 그 단계의 WARN 이다(rc 0 이어도 — ⑦ 리뷰 2).
# 종료: 0 완료(WARN 포함) · 1 FAIL · 2 사용법·환경(클러스터를 바꾸기 전) · 4 모름(UNKNOWN — 확인 뒤 같은 단계부터) · 10 PAUSE(S4 뒤 push 대기).
#   --help 는 이 머리 주석을 보이고 0.
# 전제: 설치 호스트에 kubectl(클러스터 minor ±1)·python3(RHEL 9 = 3.9)·curl(설계 S0-7). 이 스크립트는 POSIX sh(dash -n).
set -u
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)      # deploy
PY=${PYTHON:-python3}
NS=dms                                            # 플랫폼 층·00-namespace.yaml 이 ns dms 를 박아 쓴다(옛 DMS_NS 는 없앴다 — 2026-11-03 ⑦)
# 옛 install.sh 가 쓰던 env DMS_NS 가 셸에 남아 있으면 도우미(install-detect.py·render.sh 'live'·guard-images.sh)가 그 값을 읽어 엉뚱한 ns 를
# 감지한다(Cluster 없음 → A → major 업그레이드 차단을 건너뛴 SSA, 2026-11-03 ⑦ 리뷰 1). 자식에게 넘기지 않는다 — 도우미에는 --namespace 도 준다.
if [ -n "${DMS_NS:-}" ] && [ "$DMS_NS" != "$NS" ]; then echo "install.sh: env DMS_NS=$DMS_NS 는 무시한다 — ns $NS 고정(2026-11-03 ⑦)" >&2; fi
unset DMS_NS
FM=dms-install
ALL_STAGES="S1 S2 S2b S3 S4 S5 S6 S7 S8 S9 S9b S10 S11 S12 S13"

usage() {
  cat >&2 <<'EOF'
usage: sh deploy/install.sh --context <ctx> [--site <site>] [--plan]
       sh deploy/install.sh --context <ctx> [--site <site>] [--stage <S>] [--only <S>[,<S>...]] [--upgrade-addon <x>]... [--adopt <x>]...
  단계: S1 S2 S2b S3 S4 S5 S6 S7 S8 S9 S9b S10 S11 S12 S13  (--plan = S1·S2(확인만)·S3, 클러스터를 바꾸지 않는다)
  자세히(env·--upgrade-addon 이름·종료 코드): sh deploy/install.sh --help
EOF
  exit 2
}
# --help 는 사용법 오류가 아니다 — 머리 주석(첫 줄 다음부터 첫 비주석 줄 전까지)을 stdout 에 보이고 0(2026-11-03 ⑦ 리뷰 2: 래퍼·사람이 부르면
# rc 2·세 줄뿐이라 env·종료 코드·--upgrade-addon 이름을 알 길이 없었다).
show_help() { awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; exit 0; }

CTX="" SITE=prod PLAN=0 START="" ONLY="" ONLY_SET=0 DETECT_ARGS=""
_addon_ok() { case "$1" in ""|*[!abcdefghijklmnopqrstuvwxyz,-]*) return 1 ;; esac; return 0; }
# --stage·--only 의 빈 값은 사용법 오류다 — 래퍼가 빈 변수를 넘기면 부분 실행 의도가 전체 설치(변경)나 「아무것도 안 하고 PASS」가 됐다
# (2026-11-03 ⑦ 리뷰 1: `--only ""` = S1~S13 전부, `--only ,` = RESULT PASS).
_nonblank() { case "$1" in *[!\ ,]*) return 0 ;; esac; return 1; }
while [ $# -gt 0 ]; do
  case "$1" in
    --context) [ $# -ge 2 ] && [ -n "$2" ] || usage; CTX=$2; shift ;;
    --context=*) CTX=${1#*=}; [ -n "$CTX" ] || usage ;;
    --site) [ $# -ge 2 ] && [ -n "$2" ] || usage; SITE=$2; shift ;;
    --site=*) SITE=${1#*=} ;;
    --plan) PLAN=1 ;;
    --stage) [ $# -ge 2 ] && [ -n "$2" ] || usage; START=$2; shift ;;
    --stage=*) START=${1#*=}; [ -n "$START" ] || usage ;;
    --only) [ $# -ge 2 ] && _nonblank "$2" || usage; ONLY="$ONLY $(printf '%s' "$2" | tr ',' ' ')"; ONLY_SET=1; shift ;;
    --only=*) _nonblank "${1#*=}" || usage; ONLY="$ONLY $(printf '%s' "${1#*=}" | tr ',' ' ')"; ONLY_SET=1 ;;
    --upgrade-addon|--adopt) [ $# -ge 2 ] && _addon_ok "$2" || usage; DETECT_ARGS="$DETECT_ARGS $1 $2"; shift ;;
    --dry-run) echo "install.sh: --dry-run 은 없어졌다(2026-11-03 ⑦) — 변경 없는 계획은 --plan" >&2; usage ;;
    -h|--help) show_help ;;
    *) usage ;;
  esac
  shift
done
[ -n "$CTX" ] || { echo "install.sh: --context <ctx> 가 필요하다(current-context·KUBE_CONTEXT 에 기대지 않는다 — 2026-11-03 ⑦)" >&2; usage; }
case "$SITE" in *[!abcdefghijklmnopqrstuvwxyz0123456789-]*|-*|"") usage ;; esac
_known_stage() { case " $ALL_STAGES " in *" $1 "*) return 0 ;; esac; return 1; }
if [ "$PLAN" = 1 ] && { [ -n "$START" ] || [ "$ONLY_SET" = 1 ]; }; then echo "install.sh: --plan 은 --stage·--only 와 함께 쓰지 않는다" >&2; usage; fi
if [ -n "$START" ] && [ "$ONLY_SET" = 1 ]; then echo "install.sh: --stage 와 --only 는 함께 쓰지 않는다" >&2; usage; fi
[ -z "$START" ] || _known_stage "$START" || { echo "install.sh: 모르는 단계 '$START'" >&2; usage; }
for s in $ONLY; do _known_stage "$s" || { echo "install.sh: 모르는 단계 '$s'" >&2; usage; }; done

# 실행할 단계(설치 순서). --only 는 순서를 설치 순서로 맞춘다(인자 순서를 따르지 않는다).
RUN=""
if [ "$PLAN" = 1 ]; then RUN="S1 S2 S3"
elif [ "$ONLY_SET" = 1 ]; then
  for s in $ALL_STAGES; do case " $ONLY " in *" $s "*) RUN="$RUN $s" ;; esac; done
elif [ -n "$START" ]; then
  on=0; for s in $ALL_STAGES; do [ "$s" = "$START" ] && on=1; [ "$on" = 1 ] && RUN="$RUN $s"; done
else RUN=$ALL_STAGES; fi
_nonblank "$RUN" || usage       # 돌 단계가 하나도 없으면 PASS 가 아니라 사용법 오류

# --- 출력·판정 -------------------------------------------------------------------------------------------------------------
WARNS=0 STAGE_WARN=0 STAGE_SKIP=0 STAGE_FAIL="" CUR="" SOFT=0 SOFT_FAILS=0 SOFT_UNKNOWN=0
step() { printf '\n== %s ==\n' "$1"; }
ok() { printf '  [OK] %s\n' "$1"; }
info() { printf '  [INFO] %s\n' "$1"; }
warn() { printf '  [WARN] %s\n' "$1"; WARNS=$((WARNS + 1)); STAGE_WARN=1; }
finish() {   # $1 = RESULT, $2 = 종료 코드. 진행 중인 단계가 있으면 그 단계의 STAGE 줄을 먼저 낸다.
  [ -z "$CUR" ] || printf 'STAGE: %s %s\n' "$CUR" "$1"
  printf 'RESULT: %s\n' "$1"
  exit "$2"
}
# --plan 의 S1·S2 는 읽기만 하므로 FAIL·모름에서 멈추지 않고 끝까지 보인다(SOFT — 마지막 RESULT 가 FAIL·UNKNOWN). 그 밖에는 바로 멈춘다.
die() {
  printf '  [FAIL] %s\n' "$1" >&2
  if [ "$SOFT" = 1 ]; then SOFT_FAILS=$((SOFT_FAILS + 1)); STAGE_FAIL=FAIL; return 0; fi
  finish FAIL 1
}
unknown() {
  printf '  [UNKNOWN] %s\n' "$1" >&2
  if [ "$SOFT" = 1 ]; then SOFT_UNKNOWN=$((SOFT_UNKNOWN + 1)); [ -n "$STAGE_FAIL" ] || STAGE_FAIL=UNKNOWN; return 0; fi
  finish UNKNOWN 4
}
envfail() { printf '  [FAIL] %s\n' "$1" >&2; finish FAIL 2; }
# 재개 명령(PAUSE·모름·FAIL 안내) = 이번 실행과 **같은 플래그·env** + --stage. 플래그(--upgrade-addon·--adopt)를 빼면 재개한 S5~ 가
# 업그레이드를 조용히 건너뛰고 RESULT WARN·rc 0 으로 끝났다(2026-11-03 ⑦ 리뷰 1 — 운영자는 업그레이드가 됐다고 오해한다).
# 판정을 바꾸는 env 만 앞에 붙인다(값은 비밀이 아니다 — 경로·호스트·이미지 ref·스위치). S12 의 CACERT·PORTAL_DOMAIN·PORTAL_VIP·
# VERIFY_NO_PROBES 도 싣는다 — 빠진 재개 줄을 붙이면 CACERT 없는 VIP readyz 가 TLS 실패(000)로 WARN 이 되어 입구 문제를 「Avi 미개통」으로
# 읽게 했다(2026-11-03 ⑦ 리뷰 2).
_envp() {
  for _v in ADDON_INSTALL ALLOW_IMAGE_CHANGE ALLOW_HOSTPATH_CHANGE SELINUX_PROBE_IMAGE VALUES_ENV CACERT PORTAL_DOMAIN PORTAL_VIP VERIFY_NO_PROBES; do
    eval "_x=\${$_v:-}"
    [ -n "$_x" ] || continue
    case "$_x" in *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/:@=,+-]*) printf "%s='%s' " "$_v" "$_x" ;; *) printf '%s=%s ' "$_v" "$_x" ;; esac
  done
}
base_cmd() { printf '%ssh deploy/install.sh --context %s --site %s' "$(_envp)" "$CTX" "$SITE"; }
# --plan(읽기만)이 보이는 「확인한 뒤 다시」는 --plan 이다 — `--stage <S>` 는 S~S13 을 실제로 적용하는 설치라 계획을 돌리던 운영자를 변경
# 실행으로 이끌었다(2026-11-03 ⑦ 리뷰 2). 실제 설치 명령은 계획 끝 줄이 FAIL·모름이 없을 때만 보인다.
again() {
  if [ "$PLAN" = 1 ]; then printf '%s --plan%s' "$(base_cmd)" "$DETECT_ARGS"; return; fi
  printf '%s --stage %s%s' "$(base_cmd)" "${1:-$CUR}" "$DETECT_ARGS"
}
# 이번 실행에 --upgrade-addon 으로 준 항목인가($1 — 쉼표 목록 포함). 감지 D= 는 플래그가 있어도 action 이 reapply 라(서드파티는 플래그일 때만,
# 플랫폼은 늘) action 만으로는 「의도한 교체」를 가를 수 없다(2026-11-03 ⑦ 리뷰 2).
_upgrading() {
  _want=$1
  # shellcheck disable=SC2086
  set -- $DETECT_ARGS
  while [ $# -ge 2 ]; do
    if [ "$1" = --upgrade-addon ]; then case ",$2," in *",$_want,"*) return 0 ;; esac; fi
    shift 2
  done
  return 1
}
# 그 층의 가드를 이미지 교체 허용으로 부를까: --upgrade-addon 의 upgrade(D<)와 같은 버전 재적용(D= reapply — lock digest 만 바뀐 경우)만.
allow_image() { [ "$2" = upgrade ] || { [ "$2" = reapply ] && _upgrading "$1"; }; }
# 도우미 출력을 4칸 들여 흘려 보내고(0열에는 설치기 자신의 ==·STAGE·RESULT 만 — 도우미의 `RESULT:` 를 설치 결과로 읽지 않게, ⑦ 리뷰 2) 원본은
# $1 파일에 남긴다(WARN 세기). 종료 코드는 그 명령의 것(POSIX sh 에는 pipefail 이 없어 파일로 넘긴다). stderr 도 같은 흐름(순서 유지).
helper() {   # $1 = 원본 출력 파일, $2… = 명령
  _hout=$1; shift
  rm -f "$WORK/helper.rc"
  { "$@" 2>&1; echo $? > "$WORK/helper.rc"; } | tee "$_hout" | sed 's/^/    /'
  _hrc=$(cat "$WORK/helper.rc" 2>/dev/null)
  return "${_hrc:-2}"
}
# 도우미 출력의 [WARN] 판정 줄 수(preflight·verify — 둘 다 rc 0 이어도 WARN 을 낸다). 들여 쓴 하위 줄은 세지 않는다.
warn_lines() { grep -c '^  \[WARN\]' "$1" 2>/dev/null || true; }
# 도우미(python·sh)의 종료 코드 → 판정. 규약(S7·pg-path-probe·프로브 공통): 0 PASS · 3 WARN · 1 FAIL · 4 모름 · 2 사용법·환경.
judge() {   # $1 = rc, $2 = 무엇
  case "$1" in
    0) ok "$2 PASS" ;;
    3) warn "$2 WARN(위 사유) — 설치는 계속한다" ;;
    1) die "$2 FAIL(위 사유)" ;;
    4) unknown "$2 모름(위 사유 — 모름 ≠ 실패). 확인한 뒤: $(again)" ;;
    *) envfail "$2 사용법·환경 오류(rc=$1, 위 사유)" ;;
  esac
}

# --- kubectl: 모든 호출에 --context(설계 8-1 「C = kube-context, 모든 kubectl 에 --context C」) ------------------------------------
kc() { command kubectl --context "$CTX" "$@"; }
kcn() { command kubectl --context "$CTX" -n "$NS" "$@"; }
command -v kubectl >/dev/null 2>&1 || { echo "install.sh: kubectl 이 없다(설치 호스트 도구 — 설계 S0-7)" >&2; exit 2; }
command kubectl config get-contexts "$CTX" >/dev/null 2>&1 || { echo "install.sh: 컨텍스트 '$CTX' 가 kubeconfig 에 없다" >&2; exit 2; }
# 도우미도 같은 컨텍스트(명시 --context 를 넘기지만, env 기본값을 읽는 도우미가 엉뚱한 값을 쓰지 않게).
KUBE_CONTEXT=$CTX; export KUBE_CONTEXT
# python 도우미의 진행 줄이 파이프(helper 의 들여쓰기) 뒤에서 몇 분씩 묶이지 않게(stdout 이 tty 가 아니면 블록 버퍼 — ⑦ 리뷰 2).
PYTHONUNBUFFERED=1; export PYTHONUNBUFFERED
command -v "$PY" >/dev/null 2>&1 || { echo "install.sh: $PY 가 없다(RHEL 9 = python3.9 — 설계 S0-7)" >&2; exit 2; }

WORK=$(mktemp -d 2>/dev/null) || { echo "install.sh: 임시 디렉터리를 만들지 못했다(mktemp)" >&2; exit 2; }
PF_PID=""
cleanup() { pf_close; rm -rf "$WORK"; }
trap cleanup EXIT
trap 'exit 130' INT TERM
DET="$WORK/detect.out"

# --- port-forward(레지스트리 — apiserver 를 거치므로 설치 호스트가 ClusterIP 에 닿지 않아도 된다) ---------------------------------
pf_close() { if [ -n "$PF_PID" ]; then kill "$PF_PID" 2>/dev/null; wait "$PF_PID" 2>/dev/null; PF_PID=""; fi; }
pf_open() {   # $1 = 서비스 포트 → LPORT(무작위 로컬 포트 — 고정 포트가 이미 쓰이면 엉뚱한 서비스에 묻는다). listen 한 뒤에 찍히는 줄을 기다린다.
  [ -z "$PF_PID" ] || return 0
  : > "$WORK/pf.log"
  # 함수(kcn)를 & 로 돌리면 $! 가 서브셸이라 kill 해도 kubectl 이 남는다(2026-11-03 ⑦ 실측) — 서브셸에서 exec 해 $! 가 kubectl 이 되게.
  ( exec kubectl --context "$CTX" -n "$NS" port-forward svc/dms-registry ":$1" ) > "$WORK/pf.log" 2>&1 &
  PF_PID=$!
  LPORT=""
  _i=0
  while [ "$_i" -lt 15 ]; do
    LPORT=$(sed -n 's/^Forwarding from 127\.0\.0\.1:\([0-9][0-9]*\) .*/\1/p' "$WORK/pf.log" | head -n 1)
    [ -z "$LPORT" ] || return 0
    kill -0 "$PF_PID" 2>/dev/null || break
    sleep 1; _i=$((_i + 1))
  done
  pf_close
  return 1
}
# curl 은 env 프록시를 쓰지 않는다 — 127.0.0.1(port-forward)·사내 레지스트리를 HTTP_PROXY 로 보내면 닿지 않는다.
http_code() { curl --noproxy '*' -s -m 10 -o /dev/null -w '%{http_code}' "$@" 2>/dev/null || true; }

# --- 렌더 ----------------------------------------------------------------------------------------------------------------------
RDIR="" RENV=""
rv() { sed -n "s/^$1=//p" "$RENV" | head -n 1 | tr -d '"'; }
do_render() {
  step "렌더 (deploy/render.sh $SITE --context $CTX → deploy/.rendered/$SITE)"
  if ! out=$(sh "$HERE/render.sh" "$SITE" --context "$CTX"); then
    printf '%s\n' "$out" >&2
    envfail "렌더 실패(위 사유 — values.env 를 고치고 다시)"
  fi
  RDIR=$(printf '%s\n' "$out" | sed -n 's/^RENDERED: //p' | tail -n 1)
  RDIR=${RDIR%/app}
  RENV="$RDIR/render.env"
  [ -n "$RDIR" ] && [ -f "$RENV" ] || envfail "렌더 결과(RENDERED:·render.env)를 찾지 못했다"
  if [ -f "$RDIR/app/kube-context" ]; then
    pin=$(sed -e 's/#.*$//' -e 's/[[:space:]]//g' "$RDIR/app/kube-context" | grep -v '^$' | head -n 1)
    [ "$pin" = "$CTX" ] || envfail "오버레이 $SITE 는 컨텍스트 '$pin' 전용이다(kube-context) — --context $CTX 와 다르다(다른 클러스터의 렌더)"
  fi
  RMODE=$(rv REGISTRY_MODE); RCIP=$(rv REGISTRY_CLUSTER_IP); RPORT=$(rv REGISTRY_PORT); RPORT=${RPORT:-5000}
  REGISTRY=$(rv REGISTRY); ADDON_REG=$(rv ADDON_REGISTRY); SHARED_DIR=$(rv SHARED_DIR)
  ok "렌더 $RDIR (REGISTRY_MODE=$RMODE · REGISTRY=$REGISTRY · 애드온 레지스트리=$ADDON_REG · SHARED_DIR=$SHARED_DIR)"
}
# 앱 렌더가 쓰는 DMS 이미지(REGISTRY 아래 dms·dms-agent·잡 이미지) — 로컬 `kubectl kustomize`(클러스터를 보지 않는다).
app_images() {
  kc kustomize "$RDIR/app" 2>/dev/null \
    | sed -n -e 's/^[[:space:]]*image:[[:space:]]*//p' -e 's/^[[:space:]]*DMS_JOB_IMAGE:[[:space:]]*//p' \
    | tr -d "\"'" | grep "^$REGISTRY/" | sort -u
}
dms_image() { app_images | grep "^$REGISTRY/dms:" | head -n 1; }

# --- 감지(install-detect.py — 읽기 전용) ----------------------------------------------------------------------------------------
DETECTED=0
det() {   # $1 = 항목, $2 = code|action|apply|level
  case "$2" in code) _f=3 ;; action) _f=4 ;; apply) _f=5 ;; *) _f=6 ;; esac
  awk -v i="$1" -v f="$_f" '$1 == "DETECT:" && $2 == i { print $f; exit }' "$DET"
}
detect_gate() {   # $1 = 감지 rc — 설계 8-1 S1 「FAIL·P·X 면 중단」(--adopt 로 채택하는 P 는 WARN)
  case "$1" in
    0) ok "감지 PASS" ;;
    3) warn "감지 WARN(위 표 — 설치는 계속한다)" ;;
    1) die "감지 FAIL — 위 표의 FAIL·P·X 를 먼저 해소한다(P 는 --adopt <x> 로만 채택, 설계 8-2)" ;;
    4) unknown "감지 모름(읽기 실패 — 권한·연결). 확인한 뒤: $(again)" ;;
    *) envfail "install-detect.py 사용법·환경 오류(rc=$1)" ;;
  esac
  volcano_layer_gate
}
volcano_layer_gate() {
  # Volcano 층은 아직 동봉하지 않는다(docs/BACKLOG.md ⑦) — 설치해야 하는 사이트는 그 전에 멈춘다(S9b 에서 늦게 알지 않게).
  case "$(det volcano action)" in
    install|upgrade|adopt|reapply)
      [ -d "$RDIR/addons/volcano" ] || die "Volcano 를 DMS 가 설치해야 하는데(감지 $(det volcano code)) 렌더에 addons/volcano 층이 없다 — Volcano 층은 아직 동봉하지 않는다(docs/BACKLOG.md ⑦). 클러스터 운영자에게 Volcano ≥ v1.15(K8s 1.36 이면 v1.16) 설치를 요청한 뒤 다시: $(again S1)" ;;
  esac
}
need_detect() {
  [ "$DETECTED" = 0 ] || return 0
  info "감지: python3 deploy/install-detect.py --context $CTX --namespace $NS detect --rendered $RDIR$DETECT_ARGS (읽기 전용)"
  # shellcheck disable=SC2086
  "$PY" "$HERE/install-detect.py" --context "$CTX" --namespace "$NS" detect --rendered "$RDIR" $DETECT_ARGS > "$DET"
  rc=$?
  # 기계 줄(DETECT·PLAN·RESULT)은 det() 가 파일에서 읽는다 — 사람에게는 같은 내용의 표만(⑦ 리뷰 2: 같은 표가 두 번, 도우미 RESULT 가 섞였다).
  human "$DET"
  DETECTED=1
  detect_gate "$rc"
}
human() { grep -vE '^(DETECT|PLAN|RESULT):' "$1" | sed 's/^/    /'; }
applies() { [ "$(det "$1" apply)" = 1 ]; }

# --- SSA(addons·platform 층 — 설계 8-2) ----------------------------------------------------------------------------------------
ssa() {   # $1 = 층(렌더 디렉터리 기준), $2 = 감지 action(adopt 면 --force-conflicts)
  _layer=$1 _act=${2:-}
  [ -d "$RDIR/$_layer" ] || die "렌더 층이 없다: $RDIR/$_layer"
  if [ "$_act" = adopt ]; then
    kc apply --server-side --field-manager="$FM" --force-conflicts -k "$RDIR/$_layer" > "$WORK/ssa.out" 2> "$WORK/ssa.err"
  else
    kc apply --server-side --field-manager="$FM" -k "$RDIR/$_layer" > "$WORK/ssa.out" 2> "$WORK/ssa.err"
  fi
  _rc=$?
  if [ "$_rc" -ne 0 ]; then
    sed 's/^/    /' "$WORK/ssa.err" | tail -n 15 >&2
    if grep -q 'already allocated' "$WORK/ssa.err"; then
      die "$_layer SSA 실패 — clusterIP 충돌: REGISTRY_CLUSTER_IP $RCIP 를 다른 Service 가 쓴다(kubectl --context $CTX get ipaddresses.networking.k8s.io $RCIP 의 PARENTREF, deploy/README §2e-1)"
    fi
    if grep -qi 'conflict' "$WORK/ssa.err"; then
      die "$_layer SSA 실패 — 다른 field manager 가 가진 필드다(남이 관리하는 객체 — 채택은 --adopt 로만, 설계 8-2)"
    fi
    die "$_layer SSA 실패(위 오류)"
  fi
  ok "SSA $_layer (field manager $FM$([ "$_act" = adopt ] && printf ' · --force-conflicts'))"
}
guard() {   # $1 = 렌더 층(Deployment 가 있는 층) — 이미지(3)·컨텍스트(4)·hostPath(5) 변경 가드. $2 = allow-image(--upgrade-addon 의 의도한 교체)
  if [ "${2:-}" = allow-image ]; then
    sh "$HERE/overlays/guard-images.sh" "$RDIR/$1" --context "$CTX" --allow-image-change
  else
    sh "$HERE/overlays/guard-images.sh" "$RDIR/$1" --context "$CTX"
  fi
  _g=$?
  case "$_g" in
    0) ok "가드 통과: $1" ;;
    # 애드온·플랫폼 층에 전역 ALLOW_IMAGE_CHANGE=1 을 권하면 _envp 가 그 값을 이후 모든 재개 줄에 실어 S11 의 앱 가드(포탈 릴리스 되돌림 방지)까지
    # 열린 채 남는다 — 층별로 안내한다(2026-11-03 ⑦ 리뷰 2, prod README 「업그레이드」·deploy/README §2e-5 와 같은 규칙).
    3) case "$1" in
         app) die "가드 거부(이미지 변경) — app: values.env 의 DMS_TAG·DMS_AGENT_TAG·MFU_TAG 를 라이브에 맞추거나 'live'(포탈 릴리스를 되돌리지 않게). 의도한 앱 이미지 변경이면 ALLOW_IMAGE_CHANGE=1 $(again)" ;;
         *) _x=$(guard_addon "$1")
            die "가드 거부(이미지 변경) — $1: 라이브 이미지가 렌더(lock digest·버전)와 다르다. 의도한 교체면 그 층에서만 연다: $(base_cmd) --only $CUR$DETECT_ARGS --upgrade-addon $_x$([ "$_x" = registry ] && printf '(마스터 전부 crictl pull 먼저 — deploy/README §2e-5)'). 전역 ALLOW_IMAGE_CHANGE=1 은 S11 의 앱 가드까지 열므로 쓰지 않는다" ;;
       esac ;;
    4) envfail "가드 거부(컨텍스트) — $1 은 다른 클러스터 전용이다" ;;
    5) die "가드 거부(hostPath 변경) — 공용 디렉터리 전환 런북(deploy/README §2d-4)을 마친 뒤 ALLOW_HOSTPATH_CHANGE=1 로 다시" ;;
    *) die "가드 실패(rc=$_g) — $1" ;;
  esac
}
guard_addon() {   # $1 = 렌더 층 → --upgrade-addon 이름(install-detect 항목)
  case "$1" in
    platform/registry) echo registry ;; addons/envoy-gateway) echo envoy-gateway ;; addons/cnpg) echo cnpg ;;
    addons/volcano) echo volcano ;; *) echo "${1##*/}" ;;
  esac
}
job_state() {   # $1 = Job 이름 → Complete | Failed | Running | None(없음). 1.31+ 의 SuccessCriteriaMet·FailureTarget 앞섬은 단어로 맞춘다.
  _js=$(kcn get job "$1" -o 'jsonpath={range .status.conditions[?(@.status=="True")]}{.type} {end}' 2>/dev/null) || { echo None; return; }
  case " $_js" in *" Complete "*) echo Complete ;; *" Failed "*) echo Failed ;; *) echo Running ;; esac
}
wait_job() {   # $1 = Job, $2 = 초 → Complete 면 0. `wait --for=condition=complete` 는 Failed 에서도 시간 초과까지 기다린다(README §2f-4) — 둘 다 본다.
  _end=$(( $(date +%s) + $2 ))
  while :; do
    case "$(job_state "$1")" in
      Complete) return 0 ;;
      Failed) return 1 ;;
      None) return 3 ;;
    esac
    [ "$(date +%s)" -lt "$_end" ] || return 2
    sleep "${POLL_INTERVAL:-5}"
  done
}

# --- 레지스트리 대조(S5·S11) -----------------------------------------------------------------------------------------------------
REG_URL=""
reg_url() {   # $1 = host:port → REG_URL. 클러스터 내 레지스트리면 port-forward(설치 호스트가 ClusterIP 에 닿지 않아도 된다). $(…) 안에서 부르지 않는다.
  if [ -n "$RCIP" ] && [ "$1" = "$RCIP:$RPORT" ]; then
    pf_open "$RPORT" || die "레지스트리 port-forward 가 열리지 않았다 — $(tr '\n' ' ' < "$WORK/pf.log" | cut -c1-200)"
    REG_URL="http://127.0.0.1:$LPORT"
  elif [ "$(reg_tls "$1")" = true ]; then REG_URL="https://$1"
  else REG_URL="http://$1"; fi
}
# 그 레지스트리가 TLS 인가 — S5 대조(https)와 인터넷 노드 push(--dest-tls-verify)가 같은 규칙을 쓴다(2026-11-03 ⑦ 리뷰 2: push 안내가 TLS 사이트
# 레지스트리에도 --dest-tls-verify=false 였다). 클러스터 내 레지스트리는 늘 평문, 그 밖은 values.env REGISTRY_TLS_VERIFY=true 일 때만(빈 값 = 기본
# false — values.env.example·push-images.sh 의 기본과 같게; 전에는 빈 값이면 대조만 https 였다).
reg_tls() {   # $1 = host:port → true|false
  if [ -n "$RCIP" ] && [ "$1" = "$RCIP:$RPORT" ]; then echo false
  elif [ "$(rv REGISTRY_TLS_VERIFY)" = true ]; then echo true
  else echo false; fi
}
ACCEPT='application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json'
APP_MISSING=""
check_app_images() {   # DMS 3종(상류가 없다 — lock 밖)이 REGISTRY 에 있는가 → APP_MISSING
  APP_MISSING=""
  _imgs=$(app_images)
  [ -n "$_imgs" ] || { APP_MISSING=" (앱 렌더에서 $REGISTRY 아래 이미지를 찾지 못했다)"; return; }
  reg_url "$REGISTRY"
  _url=$REG_URL
  for _img in $_imgs; do
    _ref=${_img#"$REGISTRY"/}
    case "$_ref" in *@*) info "digest ref 는 태그 확인 밖: $_img"; continue ;; *:*) ;; *) APP_MISSING="$APP_MISSING $_img(태그 없음)"; continue ;; esac
    _code=$(http_code -I -H "Accept: $ACCEPT" "$_url/v2/${_ref%%:*}/manifests/${_ref##*:}")
    if [ "$_code" = 200 ]; then ok "레지스트리에 ${_ref} 있음"; else APP_MISSING="$APP_MISSING ${_ref}(HTTP ${_code:-?})"; fi
  done
}
_lock_has() { grep -v '^#' "$HERE/images.lock" | awk '{print $1}' | grep -q "^$1:"; }
lock_skips() {   # lock 줄 가운데 이 사이트가 받지 않는 것(남의 애드온·레지스트리 없음) → push·대조에서 뺀다
  _s=""
  if [ -z "$RCIP" ] && _lock_has registry; then _s="$_s --skip registry"; fi
  case "$(det cnpg code)" in C|C!|U) _lock_has cloudnative-pg && _s="$_s --skip cloudnative-pg" ;; esac
  case "$(det volcano code)" in
    A|"D="|"D<"|"D>"|P) ;;
    *) for _v in vc-scheduler vc-controller-manager vc-webhook-manager; do _lock_has "$_v" && _s="$_s --skip $_v"; done ;;
  esac
  printf '%s' "$_s"
}

# =============================================================================================================================
stage_S1() {
  info "preflight: sh deploy/preflight-cluster.sh --context $CTX --render-env $RENV (읽기 전용)"
  helper "$WORK/preflight.out" env PREFLIGHT_SKIP_DETECT=1 sh "$HERE/preflight-cluster.sh" --context "$CTX" --render-env "$RENV"
  _rc=$?
  # preflight 는 WARN 이 있어도 rc 0 이다 — 그 줄(정책 엔진·envoy-gateway-system PSA·cluster.local 아님·하단 정적 대역 밖·ServiceCIDR 모름 등 뒤
  # 단계 실패를 예고하는 것)이 STAGE·RESULT 에서 빠지지 않게 센다(2026-11-03 ⑦ 리뷰 2 — dmsk8s --plan 이 「WARN 1」인데 RESULT PASS 였다).
  _n=$(warn_lines "$WORK/preflight.out")
  case $_rc in
    0) if [ "${_n:-0}" -gt 0 ]; then warn "preflight WARN ${_n}건(위 [WARN] 줄 — 설치는 계속한다)"; else ok "preflight 통과"; fi ;;
    1) die "preflight FAIL — 위 [FAIL] 항목을 먼저 해소한다" ;;
    *) envfail "preflight 가 클러스터에 붙지 못했다(위 사유)" ;;
  esac
  need_detect
}

stage_S2() {
  if [ "$PLAN" = 1 ]; then
    if kc get ns "$NS" >/dev/null 2>&1; then ok "ns $NS 있음"; else info "ns $NS 없음 — S2 가 deploy/k8s/00-namespace.yaml 로 만든다"; fi
    if kcn get secret dms-pg-app >/dev/null 2>&1; then ok "dms-pg-app 있음"; else info "dms-pg-app 없음 — S2 가 만든다(pg-secrets.py ensure-app)"; fi
    if kcn get secret dms-secrets >/dev/null 2>&1; then secrets_gate; else info "dms-secrets 없음 — S2 가 만든다(pg-secrets.py init-secrets)"; fi
    tls_gate plan
    return
  fi
  kc apply -f "$HERE/k8s/00-namespace.yaml" > "$WORK/ns.out" 2>&1 || { cat "$WORK/ns.out" >&2; die "00-namespace.yaml 적용 실패"; }
  ok "ns $NS(PSA privileged — deploy/k8s/00-namespace.yaml)"
  helper "$WORK/pgs.out" "$PY" "$HERE/pg-secrets.py" --context "$CTX" --namespace "$NS" ensure-app || die "dms-pg-app(pg-secrets.py ensure-app) 실패"
  if ! kcn get secret dms-secrets >/dev/null 2>&1; then
    helper "$WORK/pgs.out" "$PY" "$HERE/pg-secrets.py" --context "$CTX" --namespace "$NS" init-secrets || die "dms-secrets(pg-secrets.py init-secrets) 실패"
  fi
  secrets_gate
  # 포탈 인증서는 S8(platform/gateway)만 쓴다 — 여기서는 알리기만 하고 S8 이 SSA 직전에 멈춘다. 사내 CA 발급을 기다리는 동안에도 S2b~S7(레지스트리·
  # push·프로브)을 먼저 끝내 둘 수 있게(설계 8-1 S2 판정 칸 「—」, 2026-11-03 ⑦ 리뷰 1).
  tls_gate warn
}
secrets_gate() {
  # 4키·자리표시자(고전적 CrashLoop 원인) — 값은 이 셸 변수 밖으로 나가지 않고(출력·argv 없음) 확인 뒤 지운다.
  # 읽기 실패(권한·연결)는 「키 없음」이 아니라 모름이다(모름 ≠ 실패 — 2026-11-03 ⑦ 리뷰 1). stderr 는 오류 문구뿐이라 보여 줘도 된다.
  if ! SEC=$(kcn get secret dms-secrets -o go-template='{{range $k,$v := .data}}{{$k}}={{$v|base64decode}}{{"\n"}}{{end}}' 2> "$WORK/sec.err"); then
    SEC=""
    sed 's/^/    /' "$WORK/sec.err" | tail -n 3 >&2
    unknown "dms-secrets 를 읽지 못했다(권한·연결 — 「키 없음」이 아니라 모름이다). 확인한 뒤: $(again)"
    return 0
  fi
  _miss=""
  for k in DMS_DATABASE_URL DMS_SHARED_TOKEN DMS_ADMIN_TOKEN DMS_SESSION_SECRET; do
    printf '%s\n' "$SEC" | grep -q "^$k=" || _miss="$_miss $k"
  done
  _ph=0
  printf '%s\n' "$SEC" | grep -Eq 'CHANGE_ME|REPLACE_WITH_' && _ph=1
  SEC=""
  # --plan(SOFT)에서는 die 가 돌아온다 — 뒤의 [OK] 줄이 FAIL 과 함께 찍히지 않게 바로 끝낸다.
  [ -z "$_miss" ] || { die "dms-secrets 에 키가 없다:$_miss (dms-k8s 는 4키 — deploy/README §2f-2)"; return 0; }
  [ "$_ph" = 0 ] || { die "dms-secrets 에 자리표시자(CHANGE_ME/REPLACE_WITH_)가 남았다 — 실제 값 주입"; return 0; }
  ok "dms-secrets: 4키·자리표시자 없음(값은 출력하지 않는다)"
  # 설계 6-3 install 게이트: DMS_DATABASE_URL 이 dms-pg-rw·DB·사용자·dms-pg-app 비밀번호·필수 파라미터와 맞는가(메모리에서만).
  helper "$WORK/pgs.out" "$PY" "$HERE/pg-secrets.py" --context "$CTX" --namespace "$NS" check \
    || die "DMS_DATABASE_URL 이 CNPG(dms-pg)·dms-pg-app 과 어긋난다 — 외부 DB 에서 옮기는 사이트면 deploy/README §2f-2 「이관 순서」(pg-secrets.py set-url), 새 사이트면 dms-secrets 를 지우고 S2 를 다시(init-secrets)"
}
tls_gate() {   # 포탈 인증서(Gateway 리스너) — render.env 의 PORTAL_TLS_SECRET(⑥, 옛 dms-portal-tls 고정 게이트를 대체).
  # $1 = plan|warn(S2 — 알리기만) · 없음(S8 — platform/gateway SSA 직전에 멈춘다)
  _tls=$(rv PORTAL_TLS_SECRET); _tls=${_tls:-dms-portal-tls}
  _type=$(kcn get secret "$_tls" -o 'jsonpath={.type}' 2>/dev/null || true)
  if [ "$_type" = kubernetes.io/tls ]; then ok "TLS 시크릿 $_tls(kubernetes.io/tls)"; return; fi
  _how="kubectl --context $CTX -n $NS create secret tls $_tls --cert=<tls.crt> --key=<tls.key>  (SAN: PORTAL_DOMAIN + PORTAL_VIP)"
  if [ -n "$_type" ]; then
    [ -n "${1:-}" ] && { warn "TLS 시크릿 $_tls 의 type 이 $_type — kubernetes.io/tls 여야 Gateway 리스너가 쓴다(S8 이 멈춘다 — 그 전에 다시 만든다)"; return; }
    die "TLS 시크릿 $_tls 의 type 이 $_type 다 — kubernetes.io/tls 로 다시 만든다: $_how ; $(again S8)"
  fi
  [ -n "${1:-}" ] && { warn "TLS 시크릿 $_tls 가 아직 없다 — S8(입구) 전에 만든다(없으면 S8 이 멈춘다)$([ "$1" = plan ] && echo '. ns 를 만든 뒤'): $_how"; return; }
  die "TLS 시크릿 $_tls 가 없다 — 만든 뒤 다시: $_how ; $(again S8)"
}

stage_S2b() {
  # 프로브 이미지 = 모든 노드에 이미 있는 셸 포함 이미지(pull 없음). 기본은 Cilium agent 이미지를 찾아 쓴다 — Cilium 이 없는 클러스터는
  # SELINUX_PROBE_IMAGE 로 준다(2026-11-03 ⑦).
  if [ -n "${SELINUX_PROBE_IMAGE:-}" ]; then
    helper "$WORK/selinux.out" "$PY" "$HERE/selinux-probe.py" --context "$CTX" --namespace "$NS" --image "$SELINUX_PROBE_IMAGE"
  else
    helper "$WORK/selinux.out" "$PY" "$HERE/selinux-probe.py" --context "$CTX" --namespace "$NS"
  fi
  _rc=$?
  [ "$_rc" -ne 1 ] || die "SELinux 노드 프로브 FAIL — RHEL 계열 DMS 노드는 enforcing 이 아니어야 한다(위 안내, 설계 2-3). 고친 뒤: $(again S2b)"
  # 프로브의 사용법 오류(rc 2)는 대개 「Cilium 이 없어 기본 이미지를 못 찾음」이다 — 프로브는 자기 플래그(--image)를 말하므로 설치기의 길을 같이 보인다
  # (2026-11-03 ⑦ 리뷰 1).
  if [ "$_rc" = 2 ] && [ -z "${SELINUX_PROBE_IMAGE:-}" ]; then
    info "Cilium 이 없는 클러스터면 프로브 이미지를 env 로 준다(모든 DMS 노드에 이미 있는 셸 포함 이미지 — 예: CNI agent 이미지, pull 하지 않는다): SELINUX_PROBE_IMAGE=<ref> $(again S2b)"
  fi
  judge "$_rc" "SELinux 노드 프로브(S2b)"
}

stage_S3() {
  info "계획: python3 deploy/install-detect.py --context $CTX --namespace $NS plan --rendered $RDIR$DETECT_ARGS (server dry-run — 변경 없음)"
  # shellcheck disable=SC2086
  "$PY" "$HERE/install-detect.py" --context "$CTX" --namespace "$NS" plan --rendered "$RDIR" $DETECT_ARGS > "$DET"
  _rc=$?
  DETECTED=1
  if [ "$_rc" = 0 ] && [ "$CUR_HAS_S1" = 1 ]; then
    sed -n '/^== 층별 server dry-run/,$p' "$DET" > "$WORK/plan.layers"      # 감지 표는 S1 이 보였다
    human "$WORK/plan.layers"
  else
    human "$DET"
  fi
  case "$_rc" in
    0) ok "계획 PASS" ;;
    3) warn "계획 WARN(위 표)" ;;
    1) die "계획 FAIL — 위 표의 FAIL 층·항목(설계 8-2 — 최종 판정은 DMS 가 만들 CR 의 server dry-run)" ;;
    4) unknown "계획 모름(읽기 실패). 확인한 뒤: $(again S3)" ;;
    *) envfail "install-detect.py 사용법·환경 오류(rc=$_rc)" ;;
  esac
  # S1 이 이번 실행에서 이미 이 게이트를 봤다 — 다시 부르면 --plan 에서 같은 FAIL 이 두 번, dry-run PASS 인 S3 가 FAIL 로 찍히고 S3(SOFT 아님)가
  # 거기서 끝나 「계획 끝」 줄도 빠졌다(2026-11-03 ⑦ 리뷰 2). S3 만 돌릴 때(--only S3·--stage S3)는 여기서 본다.
  [ "$CUR_HAS_S1" = 1 ] || volcano_layer_gate
}

stage_S4() {
  need_detect
  _pc=$(det priorityclass action)
  if applies priorityclass; then ssa platform/priorityclass "$_pc"
  else info "PriorityClass dms-addon-critical: $(det priorityclass code) $_pc — 적용하지 않는다"; fi
  if [ "$(det registry code)" = - ] || [ -z "$RCIP" ]; then
    info "클러스터 내 레지스트리 없음(REGISTRY_MODE=$RMODE, REGISTRY_CLUSTER_IP 없음) — 레지스트리 단계 건너뜀(이미지는 사이트 레지스트리 $REGISTRY)"
    S4_RAN=1
    return
  fi
  # 가드는 이 층을 **적용할 때만**(2026-11-03 ⑦ 리뷰 1): 적용하지 않을 D</D>(keep) 때문에 평소 재실행이 멈추면, 넘기려고 준 전역
  # ALLOW_IMAGE_CHANGE=1 이 S11 의 앱 가드(포탈 릴리스 되돌림 방지)까지 연다. --upgrade-addon registry(upgrade)는 의도한 교체라 이 층에서만 연다.
  _act=$(det registry action)
  if applies registry; then
    if allow_image registry "$_act"; then
      info "레지스트리 업그레이드·재적용(--upgrade-addon registry, 설계 7-6): 새 registry 이미지를 S5 로 push 하고 마스터 전부 crictl pull·고정 목록(99-dms-pinned.conf) 확인을 먼저 끝냈어야 한다 — 빌드·push 가 없을 때(deploy/README §2e-5)"
      guard platform/registry allow-image
    else guard platform/registry; fi
  fi
  if kcn get secret dms-registry-http >/dev/null 2>&1; then ok "dms-registry-http 있음(그대로 둔다)"
  else
    # 값은 출력하지 않고 argv 에도 싣지 않는다(stdin) — README §2e-3 과 같은 줄.
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' \
      | kcn create secret generic dms-registry-http --from-file=http-secret=/dev/stdin >/dev/null 2>&1 \
      || die "dms-registry-http 를 만들지 못했다"
    ok "dms-registry-http 만들었다(무작위 — 출력하지 않음)"
  fi
  if applies registry; then ssa platform/registry "$_act"
  else info "레지스트리: $(det registry code) $_act — 적용하지 않는다(확인만 — 가드도 부르지 않는다)"; fi
  if ! kcn rollout status deploy/dms-registry --timeout="${ROLLOUT_TIMEOUT:-180}s" > "$WORK/ro.out" 2>&1; then
    sed 's/^/    /' "$WORK/ro.out" | tail -n 3 >&2
    # 설계 8-1 S4 판정 문구 「마스터 X 에 공용 디렉터리 없음」 — replicas 1 이라 그 파드의 노드가 원인 노드다. 이벤트는 객체·노드와 함께
    # (FailedScheduling 은 메시지에 registry 라는 말이 없다 — 객체 이름으로 거른다, 2026-11-03 ⑦ 리뷰 1).
    _rnode=$(kcn get pod -l app.kubernetes.io/name=dms-registry -o 'jsonpath={range .items[*]}{.spec.nodeName}{" "}{end}' 2>/dev/null)
    _rnode=$(printf '%s' "$_rnode" | tr -s ' ' | sed 's/ $//')
    kcn get events --field-selector involvedObject.kind=Pod \
      -o 'custom-columns=OBJ:.involvedObject.name,NODE:.source.host,REASON:.reason,MSG:.message' --no-headers 2>/dev/null \
      | grep '^dms-registry' | grep -E 'Failed|BackOff' | tail -n 4 | cut -c1-300 | sed 's/^/    /' >&2
    kcn logs deploy/dms-registry -c sentinel-check --tail=5 2>/dev/null | sed 's/^/    init: /' >&2
    die "dms-registry 가 Ready 가 아니다(파드 노드: ${_rnode:-배치 안 됨}) — 「FailedMount … hostPath type check」= 마스터 ${_rnode:-<노드>} 에 공용 디렉터리 없음(마운트·S0), 「ErrImagePull·ImagePullBackOff …/registry:<태그>」= 그 마스터에 S0-5 반입·고정 없음(그 노드에서 crictl images --digests · deploy/README §2e-4 의 2단계), 「FailedScheduling」= 마스터 자리·taint·PriorityClass, init 「센티널 없음」= S0 미실행·로컬 디렉터리, 「센티널 불일치」= values.env SHARED_DIR_ID(deploy/README §2e-3 표)"
  fi
  ok "dms-registry Ready"
  pf_open "$RPORT" || die "레지스트리 port-forward 가 열리지 않았다 — $(tr '\n' ' ' < "$WORK/pf.log" | cut -c1-200)"
  _code=""
  _i=0
  while [ "$_i" -lt 10 ]; do
    _code=$(http_code "http://127.0.0.1:$LPORT/v2/")
    [ "$_code" = 000 ] || [ -z "$_code" ] || break
    sleep 1; _i=$((_i + 1))
  done
  pf_close
  [ "$_code" = 200 ] || die "레지스트리 /v2/ 가 200 이 아니다(HTTP ${_code:-?}) — 익명 레지스트리여야 한다(설계 7-2)"
  ok "레지스트리 $RCIP:$RPORT /v2/ 200(port-forward)"
  S4_RAN=1
}

stage_S5() {
  need_detect
  _skips=$(lock_skips)
  _lockreg=${ADDON_REG:-$REGISTRY}
  reg_url "$_lockreg"
  _url=$REG_URL
  info "lock 대조: python3 deploy/images-lock.py compare-registry --registry-url $_url$_skips"
  # shellcheck disable=SC2086
  helper "$WORK/lock.out" "$PY" "$HERE/images-lock.py" compare-registry --registry-url "$_url" $_skips
  _lrc=$?
  [ "$_lrc" -ne 4 ] || { pf_close; unknown "레지스트리 대조 모름(닿지 않음·익명 아님 — 위 사유). 확인한 뒤: $(again S5)"; }
  [ "$_lrc" = 0 ] || [ "$_lrc" = 1 ] || { pf_close; envfail "images-lock.py 사용법·환경 오류(rc=$_lrc)"; }
  check_app_images
  pf_close
  if [ "$_lrc" = 0 ] && [ -z "$APP_MISSING" ]; then ok "S5: lock 이미지 전부 lock digest · DMS 이미지 태그 있음"; return; fi
  echo
  # 인터넷 노드에는 설치 호스트의 경로가 없다 — 같은 커밋의 저장소 루트에서 돌린다는 전제를 머리에 둔다(push-images.sh 의 lock 표시도 상대 경로).
  echo "  ---- 인터넷 노드에서 — 설치 호스트와 같은 커밋의 DMS 저장소 루트에서(레지스트리로 직접 push — 프록시를 거치면 NO_PROXY 에 레지스트리 호스트, 설계 7-5 ③) ----"
  # lock 대조가 PASS 면 lock 블록(skopeo 6줄 — 멱등 건너뜀이 없는 날 줄)을 다시 보이지 않는다 — DMS 이미지만 없을 때가 흔하다(재설치·포탈 빌드
  # 뒤, 2026-11-03 ⑦ 리뷰 2).
  if [ "$_lrc" != 0 ]; then
    _ltls=""
    [ "$(reg_tls "$_lockreg")" = false ] || _ltls="--dest-tls-verify true"
    # shellcheck disable=SC2086
    sh "$HERE/push-images.sh" --registry "$_lockreg" --print $_ltls $_skips | sed 's/^/    /'
  else
    echo "    # lock 이미지는 모두 $_lockreg 에 lock digest 로 있다 — DMS 이미지만"
  fi
  if [ -n "$APP_MISSING" ]; then
    echo "    # DMS 이미지(상류 없음 — lock 밖):$APP_MISSING"
    # 태그는 렌더(values.env 의 DMS_TAG·DMS_AGENT_TAG·MFU_TAG)가 정한 실제 값이다 — 운영자가 추론하지 않게(2026-11-03 ⑦ 리뷰 1).
    _imgs=$(app_images)
    _t_dms=$(printf '%s\n' "$_imgs" | sed -n "s|^$REGISTRY/dms:||p" | head -n 1)
    _t_agent=$(printf '%s\n' "$_imgs" | sed -n "s|^$REGISTRY/dms-agent:||p" | head -n 1)
    _t_mfu=$(printf '%s\n' "$_imgs" | sed -n "s|^$REGISTRY/dms-mpifileutils:||p" | head -n 1)
    if [ -n "$_t_dms" ] && [ "$_t_dms" = "$_t_agent" ] && [ "$_t_dms" = "$_t_mfu" ]; then
      echo "    #   처음이면 같은 노드에서 최초 부트스트랩 빌드·push: REGISTRY=$REGISTRY TAG=$_t_dms ./deploy/docker/build-and-push.sh (deploy/README §1)"
    else
      echo "    #   세 태그가 다르다(dms ${_t_dms:-?} · dms-agent ${_t_agent:-?} · dms-mpifileutils ${_t_mfu:-?}). agent 는 같은 TAG 의 dms·dms-mpifileutils"
      echo "    #   위에서 빌드되므로 첫 부트스트랩은 values.env 의 DMS_TAG·DMS_AGENT_TAG·MFU_TAG 를 같게 두고 다시 렌더한 뒤"
      echo "    #   REGISTRY=$REGISTRY TAG=<그 태그> ./deploy/docker/build-and-push.sh (deploy/README §1)"
    fi
    echo "    #   옛 레지스트리에 있으면: skopeo copy --preserve-digests --src-tls-verify=false --dest-tls-verify=$(reg_tls "$REGISTRY") docker://<옛 레지스트리>/<이름>:<태그> docker://$REGISTRY/<이름>:<태그>"
  fi
  echo "  ---- push 가 끝나면 설치 호스트에서(같은 플래그·env — 빼면 --upgrade-addon·--adopt 가 조용히 빠진다):"
  echo "    $(again S5)"
  _why="lock 대조 $([ "$_lrc" = 0 ] && echo PASS || echo FAIL)${APP_MISSING:+ · DMS 이미지 없음:$APP_MISSING}"
  if [ "${S4_RAN:-0}" = 1 ]; then
    printf '  [PAUSE] S4 끝 — 레지스트리에 이미지가 아직 없다(%s). 위 push 뒤 위 명령(--stage S5)으로 재개한다(설계 8-1 S5)\n' "$_why"
    finish PAUSE 10
  fi
  die "S5 FAIL — $_why(없거나 다르면 FAIL, 설계 8-1 S5). 위 push 뒤 다시"
}

stage_S6() {
  if [ -n "$RCIP" ]; then
    helper "$WORK/pull.out" "$PY" "$HERE/pull-probe.py" --context "$CTX" --namespace "$NS" --render-env "$RENV"
  else
    _img=$(dms_image)
    [ -n "$_img" ] || die "앱 렌더에서 dms 이미지를 찾지 못했다"
    helper "$WORK/pull.out" "$PY" "$HERE/pull-probe.py" --context "$CTX" --namespace "$NS" --image "$_img"
  fi
  judge $? "pull 프로브(S6 — 노드마다 실제 pull)"
}

stage_S7() {
  _img=$(dms_image)
  [ -n "$_img" ] || die "앱 렌더에서 dms 이미지를 찾지 못했다"
  [ -n "$SHARED_DIR" ] || envfail "render.env 에 SHARED_DIR 가 없다"
  helper "$WORK/s7.out" "$PY" "$HERE/shared-storage-probe.py" --context "$CTX" --namespace "$NS" --shared-dir "$SHARED_DIR" --image "$_img"
  judge $? "공용 스토리지 프로브(S7 — ①~⑥, ② 는 WARN)"
}

stage_S8() {
  need_detect
  _a=$(det gateway-api action)
  if applies gateway-api; then
    ssa addons/gateway-api "$_a"
    kc wait --for=condition=Established crd/gatewayclasses.gateway.networking.k8s.io crd/gateways.gateway.networking.k8s.io \
      crd/httproutes.gateway.networking.k8s.io --timeout=60s >/dev/null 2>&1 || die "Gateway API CRD 가 Established 가 아니다"
    ok "Gateway API CRD Established"
  else info "Gateway API: $(det gateway-api code) $_a — 건드리지 않는다(설계 8-2)"; fi
  _e=$(det envoy-gateway action)
  if applies envoy-gateway; then
    # Job spec 은 바꿀 수 없다(§2g-6) — 업그레이드면 지우고, 앞선 실행이 남긴 실패한 certgen 도 지워 다시 돌게 한다(재실행 복구, ⑦ 리뷰 1).
    if [ "$_e" = upgrade ] || [ "$(eg_certgen_state)" = Failed ]; then
      kc -n envoy-gateway-system delete job eg-gateway-helm-certgen --ignore-not-found >/dev/null 2>&1
    fi
    # --upgrade-addon envoy-gateway 의 D=(같은 버전·lock digest 만 바뀜)는 reapply 다 — 그것도 의도한 교체라 이 층에서 연다(⑦ 리뷰 2).
    if allow_image envoy-gateway "$_e"; then guard addons/envoy-gateway allow-image
    else guard addons/envoy-gateway; fi
    # 첫 설치면 ns 먼저 — server dry-run·SSA 가 묶음 안의 Namespace 를 저장하기 전에 그 안 객체를 보지 않게(kubernetes#83562, ⑤ 함정).
    own_ns envoy-gateway-system
    ssa addons/envoy-gateway "$_e"
    wait_job_ns_eg
    kc -n envoy-gateway-system rollout status deploy/envoy-gateway --timeout="${ROLLOUT_TIMEOUT:-180}s" >/dev/null 2>&1 \
      || die "EG 컨트롤러 rollout 시간 초과 — kubectl --context $CTX -n envoy-gateway-system describe rs(PriorityClass·이미지·자리, deploy/README §2g-3 표)"
    ok "Envoy Gateway 컨트롤러 Ready"
  else
    info "Envoy Gateway: $(det envoy-gateway code) $_e — 건드리지 않는다(설계 8-2 — C 면 platform/gateway 만)"
    # DMS 가 설치한 EG(D=·D<·D>)는 적용하지 않아도 읽기 전용으로 확인한다 — 앞선 실행이 SSA 뒤 certgen·rollout 에서 멈췄으면 재실행 감지는 D=(none)라
    # 확인 없이 지나가 「Programmed 아님」으로만 드러났다(2026-11-03 ⑦ 리뷰 1). C 는 남의 것이라 보지 않는다(Programmed·:443 이 최종 판정).
    case "$(det envoy-gateway code)" in
      "D="|"D<"|"D>")
        if ! kc -n envoy-gateway-system rollout status deploy/envoy-gateway --timeout="${ROLLOUT_TIMEOUT:-180}s" >/dev/null 2>&1 \
          || ! kc -n envoy-gateway-system get secret envoy-gateway envoy >/dev/null 2>&1; then
          die "Envoy Gateway(DMS 설치)가 덜 설치됐다(컨트롤러 Ready·xDS 시크릿 envoy-gateway·envoy 중 하나가 없다 — 앞선 실행이 certgen·rollout 에서 멈춤). 다시 적용(certgen 재실행): $(base_cmd) --only S8 --upgrade-addon envoy-gateway"
        fi
        ok "Envoy Gateway(DMS 설치) 컨트롤러 Ready·xDS 시크릿 있음(확인만)" ;;
    esac
  fi
  _g=$(det gateway action)
  if applies gateway; then
    tls_gate
    master_probe --sysctl "마스터 sysctl ip_unprivileged_port_start ≤ 443(S0-5)"
    ssa platform/gateway "$_g"
  else info "platform/gateway: $(det gateway code) $_g — 적용하지 않는다(확인만)"; fi
  kcn wait --for=condition=Programmed gateway/dms-portal --timeout="${ROLLOUT_TIMEOUT:-180}s" >/dev/null 2>&1 \
    || die "Gateway dms-portal 이 Programmed 가 아니다 — kubectl --context $CTX -n $NS describe gateway dms-portal(deploy/README §2g-3 표)"
  ok "Gateway dms-portal Programmed"
  master_probe --listen "마스터마다 :443 LISTEN(Programmed 는 bind 실패를 못 잡는다 — §2g-2)"
}
eg_certgen_state() {
  _st=$(kc -n envoy-gateway-system get job eg-gateway-helm-certgen -o 'jsonpath={range .status.conditions[?(@.status=="True")]}{.type} {end}' 2>/dev/null) \
    || { echo None; return; }
  case " $_st" in *" Complete "*) echo Complete ;; *" Failed "*) echo Failed ;; *) echo Running ;; esac
}
own_ns() {   # $1 = 애드온 네임스페이스 — 없으면 DMS 라벨(managed-by=dms-install)을 붙여 SSA 로 만든다.
  # 라벨 없이 만들고 SSA 전에 끊기면(Ctrl-C·apiserver 일시 오류) 다음 실행의 감지가 그 빈 ns 를 「남이 쓴다」(P)로 보고 S1 에서 멈췄다
  # (2026-11-03 ⑦ 리뷰 1). 같은 field manager 라 뒤이은 층 SSA 가 라벨(PSA 등)을 덧붙여도 충돌이 없다.
  kc get ns "$1" >/dev/null 2>&1 && return 0
  printf 'apiVersion: v1\nkind: Namespace\nmetadata:\n  name: %s\n  labels:\n    app.kubernetes.io/managed-by: %s\n' "$1" "$FM" \
    | kc apply --server-side --field-manager="$FM" -f - >/dev/null 2>"$WORK/ns.err" \
    || { sed 's/^/    /' "$WORK/ns.err" | tail -n 3 >&2; die "ns $1 을 만들지 못했다"; }
  ok "ns $1 만들었다(DMS 라벨 — 층 SSA 가 나머지 라벨을 붙인다)"
}
wait_job_ns_eg() {   # certgen Job — 끝나고 30초 뒤 TTL 로 지워진다(그 뒤면 None — 시크릿으로 본다)
  _end=$(( $(date +%s) + ${CERTGEN_TIMEOUT:-120} ))
  while :; do
    _st=$(kc -n envoy-gateway-system get job eg-gateway-helm-certgen -o 'jsonpath={range .status.conditions[?(@.status=="True")]}{.type} {end}' 2>/dev/null) || _st=None
    case " $_st" in
      *" Complete "*|" None") break ;;
      *" Failed "*) die "EG certgen Job 실패 — kubectl --context $CTX -n envoy-gateway-system logs job/eg-gateway-helm-certgen" ;;
    esac
    [ "$(date +%s)" -lt "$_end" ] || die "EG certgen Job 이 끝나지 않았다(${CERTGEN_TIMEOUT:-120}초)"
    sleep "${POLL_INTERVAL:-5}"
  done
  kc -n envoy-gateway-system get secret envoy-gateway envoy >/dev/null 2>&1 || die "certgen 이 만든 xDS mTLS 시크릿(envoy-gateway·envoy)이 없다"
  ok "EG certgen 끝(xDS 시크릿 있음)"
}
master_probe() {   # $1 = --sysctl|--listen, $2 = 무엇 — host-net-probe.sh(마스터 호스트 netns, dms 이미지)
  _img=$(dms_image)
  [ -n "$_img" ] || die "앱 렌더에서 dms 이미지를 찾지 못했다(마스터 프로브용)"
  sh "$HERE/host-net-probe.sh" --context "$CTX" --namespace "$NS" --image "$_img" "$1" > "$WORK/hnp.out" 2>&1
  _rc=$?
  sed 's/^/    /' "$WORK/hnp.out"
  judge "$_rc" "$2"
}

stage_S9() {
  need_detect
  _c=$(det cnpg action)
  if applies cnpg; then
    _has_cluster=0
    kcn get clusters.postgresql.cnpg.io dms-pg >/dev/null 2>&1 && _has_cluster=1
    # --upgrade-addon cnpg 는 의도한 이미지 교체다 — 가드는 Cluster 유무와 상관없이 이 층에서 연다(Cluster 가 없는 사이트 — S10 전에 멈춘 설치·
    # 재구축 — 에서 따로 ALLOW_IMAGE_CHANGE=1 을 요구하지 않게, 2026-11-03 ⑦ 리뷰 1). 직전 덤프·healthy 대기만 Cluster 가 있을 때.
    if [ "$_c" = upgrade ]; then
      if [ "$_has_cluster" = 1 ]; then run_dump "dms-pg-dump-pre-upgrade-$(date +%Y%m%d%H%M%S)" "업그레이드 직전 덤프(설계 6-5)"
      else info "Cluster dms-pg 없음 — 업그레이드 직전 덤프·healthy 대기는 건너뛴다"; fi
      guard addons/cnpg allow-image
    elif allow_image cnpg "$_c"; then guard addons/cnpg allow-image     # --upgrade-addon cnpg 의 D= 재적용(⑦ 리뷰 2)
    else guard addons/cnpg; fi
    own_ns cnpg-system
    ssa addons/cnpg "$_c"
    kc -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout="${CNPG_ROLLOUT_TIMEOUT:-300}s" >/dev/null 2>&1 \
      || die "CNPG 오퍼레이터 rollout 시간 초과 — kubectl --context $CTX -n cnpg-system describe rs(PriorityClass·이미지·워커 자리, deploy/README §2f-3 표)"
    ok "CNPG 오퍼레이터 Ready"
    if [ "$_c" = upgrade ] && [ "$_has_cluster" = 1 ]; then wait_cluster_healthy; fi
  else
    info "CNPG: $(det cnpg code) $_c — 건드리지 않는다(설계 8-2 — 어디에든 있으면 두 번째를 설치하지 않는다)"
    # DMS 가 설치한 오퍼레이터(D=·D<·D>)는 적용하지 않아도 Ready 인지 읽기로 본다(앞선 실행이 rollout 에서 멈춘 경우 — EG 와 같은 이유, ⑦ 리뷰 1).
    case "$(det cnpg code)" in
      "D="|"D<"|"D>")
        kc -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout="${CNPG_ROLLOUT_TIMEOUT:-300}s" >/dev/null 2>&1 \
          || die "CNPG 오퍼레이터(DMS 설치)가 Ready 가 아니다 — kubectl --context $CTX -n cnpg-system describe rs(PriorityClass·이미지·워커 자리, deploy/README §2f-3 표). 층을 다시 적용하려면: $(base_cmd) --only S9 --upgrade-addon cnpg"
        ok "CNPG 오퍼레이터(DMS 설치) Ready(확인만)" ;;
    esac
  fi
  # 9443 경로 실증: DMS 가 만들 Cluster CR 의 server dry-run(웹훅이 실제로 불린다). 오퍼레이터가 막 떴으면 웹훅 인증서가 늦을 수 있어 몇 번 다시.
  # --adopt db 면 S10 의 SSA 와 같게 --force-conflicts(남이 만든 dms-pg 의 필드 충돌로 채택 경로가 여기서 멈추지 않게 — ⑦ 리뷰 1).
  _force=""
  [ "$(det db action)" != adopt ] || _force=--force-conflicts
  _n=0
  while :; do
    # shellcheck disable=SC2086
    kc apply --server-side --field-manager="$FM" $_force --dry-run=server -k "$RDIR/platform/db-cluster" > "$WORK/dry.out" 2> "$WORK/dry.err" && break
    _n=$((_n + 1))
    if grep -q 'failed calling webhook' "$WORK/dry.err" && [ "$_n" -lt "${WEBHOOK_RETRIES:-6}" ]; then sleep "${POLL_INTERVAL:-10}"; continue; fi
    sed 's/^/    /' "$WORK/dry.err" | tail -n 5 >&2
    grep -q 'failed calling webhook' "$WORK/dry.err" \
      && die "Cluster CR server dry-run 실패 — 「failed calling webhook」: apiserver → 워커 파드 9443(CNPG 웹훅 — 방화벽·NetworkPolicy·CNI, 설계 #7). 오퍼레이터 파드가 Ready 인지 먼저"
    die "Cluster CR server dry-run 실패(위 오류)"
  done
  ok "Cluster CR server dry-run 통과(웹훅 9443 경로)"
}
wait_cluster_healthy() {   # CNPG 업그레이드 뒤 인스턴스 매니저 교체·switchover 완료 대기(설계 6-5) — 그동안 수 초간 500
  _end=$(( $(date +%s) + ${DB_WAIT_SECONDS:-900} ))
  while :; do
    _ph=$(kcn get clusters.postgresql.cnpg.io dms-pg -o 'jsonpath={.status.phase}' 2>/dev/null)
    [ "$_ph" = "Cluster in healthy state" ] && { ok "Cluster dms-pg healthy(업그레이드 뒤)"; return; }
    [ "$(date +%s)" -lt "$_end" ] || { warn "Cluster dms-pg 가 ${DB_WAIT_SECONDS:-900}초 안에 healthy 로 돌아오지 않았다(phase: ${_ph:-?}) — kubectl --context $CTX -n $NS describe cluster dms-pg"; return; }
    sleep "${POLL_INTERVAL:-10}"
  done
}

stage_S9b() {
  need_detect
  _v=$(det volcano action)
  if applies volcano; then
    [ -d "$RDIR/addons/volcano" ] || die "Volcano 층(addons/volcano)이 없다 — 아직 동봉하지 않는다(docs/BACKLOG.md ⑦)"
    if allow_image volcano "$_v"; then guard addons/volcano allow-image; else guard addons/volcano; fi
    ssa addons/volcano "$_v"
    for _d in volcano-scheduler volcano-controllers volcano-admission; do
      kc -n volcano-system rollout status "deploy/$_d" --timeout="${ROLLOUT_TIMEOUT:-180}s" >/dev/null 2>&1 || die "Volcano $_d rollout 시간 초과"
    done
    ok "Volcano Ready"
  else info "Volcano: $(det volcano code) $_v — 건드리지 않는다(설계 8-2 — C 면 다시 적용하지 않는다)"; fi
  "$PY" "$HERE/install-detect.py" --context "$CTX" --namespace "$NS" vcjob --rendered "$RDIR" > "$WORK/vcjob.out"
  _rc=$?
  # 사람에게는 volcano 줄·그 사유·vcjob 층 줄만(기계 줄 PLAN·RESULT 는 0열 RESULT 와 섞이지 않게 뺀다 — ⑦ 리뷰 2).
  awk '/^  \[volcano\]/ { f = 1; print; next } f && /^      / { print; next } { f = 0 } /^  vcjob / { print }' "$WORK/vcjob.out" \
    | sed 's/^/    /'
  judge "$_rc" "DMS 모양 vcjob server dry-run(S9b — scheduler·queue·웹훅 8443)"
}

stage_S10() {
  need_detect
  _db=$(det db action)
  _probe="$RDIR/platform/db-probe"
  helper "$WORK/pgpath.out" "$PY" "$HERE/pg-path-probe.py" --context "$CTX" --namespace "$NS" --rendered "$_probe" --mode pv-check
  judge $? "SC·PV 값 대조(pv-check — 적용 전: 용량·SC 는 SSA 가 Bound PV 까지 조용히 바꾼다)"
  if applies db; then ssa platform/db-volumes "$_db"
  else info "DB 층: $(det db code) $_db — 적용하지 않는다(확인만)"; fi
  helper "$WORK/pgpath.out" "$PY" "$HERE/pg-path-probe.py" --context "$CTX" --namespace "$NS" --rendered "$_probe" --mode precheck
  judge $? "PV 사전 검사(precheck — 26:26 0700·.dms-pv-id·옛 PGDATA)"
  kcn get secret dms-pg-app >/dev/null 2>&1 || die "dms-pg-app 이 없다 — S2 먼저($(again S2))"
  if applies db; then ssa platform/db-cluster "$_db"; fi
  pg_poll
  helper "$WORK/pgpath.out" "$PY" "$HERE/pg-path-probe.py" --context "$CTX" --namespace "$NS" --rendered "$_probe" --mode contrast
  judge $? "경로 대조(contrast — PG 가 공용 경로에 쓰는가)"
  if applies db; then ssa platform/db-backup "$_db"; fi
  first_dump
}
pg_poll() {   # 설계 S10: readyInstances ≥ 1 · dms-pg-rw Endpoints · currentPrimary Ready. `wait --for=condition=Ready` 는 쓰지 않는다(전부 healthy 일 때만).
  _want=$(kcn get clusters.postgresql.cnpg.io dms-pg -o 'jsonpath={.spec.instances}' 2>/dev/null); _want=${_want:-3}
  _end=$(( $(date +%s) + ${DB_WAIT_SECONDS:-900} ))
  _last=0
  while :; do
    _ready=$(kcn get clusters.postgresql.cnpg.io dms-pg -o 'jsonpath={.status.readyInstances}' 2>/dev/null); _ready=${_ready:-0}
    _prim=$(kcn get clusters.postgresql.cnpg.io dms-pg -o 'jsonpath={.status.currentPrimary}' 2>/dev/null)
    _eps=$(kcn get endpoints dms-pg-rw -o 'jsonpath={.subsets[*].addresses[*].ip}' 2>/dev/null)
    _pr=""
    [ -z "$_prim" ] || _pr=$(kcn get pod "$_prim" -o 'jsonpath={.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
    _up=0
    if [ "$_ready" -ge 1 ] 2>/dev/null && [ -n "$_eps" ] && [ "$_pr" = True ]; then _up=1; fi
    if [ "$_up" = 1 ] && [ "$_ready" -ge "$_want" ] 2>/dev/null; then ok "DB 사용 가능 — readyInstances $_ready/$_want · primary $_prim Ready · dms-pg-rw $_eps"; return; fi
    _now=$(date +%s)
    if [ "$_now" -ge "$_end" ]; then
      if [ "$_up" = 1 ]; then
        warn "readyInstances $_ready/$_want(${DB_WAIT_SECONDS:-900}초) — DB 는 쓸 수 있다. 나머지는 join 중일 수 있다(모름 ≠ 실패; Available 여분 PV·인스턴스 이벤트 확인)"
        return
      fi
      die "DB 가 ${DB_WAIT_SECONDS:-900}초 안에 쓸 수 있게 되지 않았다(readyInstances ${_ready}, primary ${_prim:--}, rw ${_eps:--}) — kubectl --context $CTX -n $NS describe cluster dms-pg · 인스턴스 파드 이벤트(deploy/README §2f-4 표)"
    fi
    if [ $((_now - _last)) -ge 60 ]; then
      info "기다림 — readyInstances ${_ready}/${_want} primary ${_prim:--} rw ${_eps:--} primaryReady ${_pr:--}"
      _last=$_now
    fi
    sleep "${POLL_INTERVAL:-10}"
  done
}
run_dump() {   # $1 = Job 이름, $2 = 무엇 — CronJob dms-pg-dump 에서 일회 Job
  kcn create job --from=cronjob/dms-pg-dump "$1" >/dev/null 2>&1 || die "$2: Job $1 을 만들지 못했다(CronJob dms-pg-dump 가 있는가 — S10)"
  wait_job "$1" "${DUMP_TIMEOUT:-600}"
  case $? in
    0) ok "$2 끝(Job $1)" ;;
    1) kcn logs "job/$1" --tail=3 2>/dev/null | sed 's/^/    /' >&2
       die "$2 실패(Job $1 — 마지막 줄이 사유: 「센티널 없음」= 그 워커의 backup/pg 마운트·S0, password authentication = dms-pg-app 불일치, deploy/README §2f-4 표)" ;;
    *) die "$2 이(가) ${DUMP_TIMEOUT:-600}초 안에 끝나지 않았다(kubectl --context $CTX -n $NS describe job/$1)" ;;
  esac
}
first_dump() {
  _lst=$(kcn get cronjob dms-pg-dump -o 'jsonpath={.status.lastSuccessfulTime}' 2>/dev/null)
  if [ -n "$_lst" ]; then ok "덤프 이미 성공(CronJob 마지막 성공 $_lst) — 첫 덤프 건너뜀"; return; fi
  case "$(job_state dms-pg-dump-install)" in
    Complete) ok "첫 덤프 이미 끝(Job dms-pg-dump-install)"; return ;;
    Failed) kcn delete job dms-pg-dump-install --ignore-not-found >/dev/null 2>&1 ;;
    Running) wait_job dms-pg-dump-install "${DUMP_TIMEOUT:-600}" && { ok "첫 덤프 끝(Job dms-pg-dump-install)"; return; }
             die "첫 덤프(Job dms-pg-dump-install)가 끝나지 않았거나 실패했다 — kubectl --context $CTX -n $NS logs job/dms-pg-dump-install" ;;
  esac
  run_dump dms-pg-dump-install "첫 덤프"
}

stage_S11() {
  secrets_gate
  if [ -n "$RCIP" ] && [ "$REGISTRY" = "$RCIP:$RPORT" ]; then
    check_app_images
    pf_close
    [ -z "$APP_MISSING" ] || die "S5 미완: 클러스터 내 레지스트리에 없는 DMS 이미지 —$APP_MISSING($(again S5))"
  fi
  guard app
  _cfg_before=$(config_rev)
  _gens="deploy/dms-api=$(gen deploy/dms-api) deploy/dms-controller=$(gen deploy/dms-controller) ds/dms-agent=$(gen ds/dms-agent)"
  # migrate Job 은 spec 불변이라 이미지가 바뀌면 apply 가 못 고친다 — 다시 돌도록 먼저 지운다(멱등, 없으면 무시).
  kcn delete job dms-migrate --ignore-not-found >/dev/null 2>&1
  kc apply -k "$RDIR/app" > "$WORK/app.out" 2>&1 || { tail -n 15 "$WORK/app.out" >&2; die "앱 apply -k 실패(위 오류)"; }
  ok "앱 apply -k $RDIR/app(client-side)"
  wait_job dms-migrate "${MIGRATE_TIMEOUT:-180}"
  case $? in
    0) ok "migrate 완료" ;;
    1) kcn logs job/dms-migrate --tail=5 2>/dev/null | sed 's/^/    /' >&2; die "migrate 실패(위 로그 — DB 연결·URL 은 S2 게이트, DB Ready 는 S10)" ;;
    *) die "migrate 가 ${MIGRATE_TIMEOUT:-180}초 안에 끝나지 않았다(kubectl --context $CTX -n $NS describe job/dms-migrate — 이미지 pull·DB)" ;;
  esac
  for _w in deploy/dms-api deploy/dms-controller ds/dms-agent; do
    kcn rollout status "$_w" --timeout="${ROLLOUT_TIMEOUT:-180}s" >/dev/null 2>&1 || die "$_w rollout 시간 초과(kubectl --context $CTX -n $NS describe $_w)"
  done
  ok "dms-api·dms-controller·dms-agent 롤아웃 끝"
  # 설정 해시(설계 S11): dms-config·dms-secrets 는 envFrom·KeyRef 라 바뀌어도 파드가 그대로다. 그 둘의 resourceVersion(값이 아니다 — 비밀을 다루지
  # 않는다)을 워크로드 어노테이션에 남겨 두고, 바뀌었는데 이번 apply 가 파드를 바꾸지 않았으면(generation 그대로) rollout restart.
  _cfg=$(config_rev)
  _restart=""
  for _pair in $_gens; do
    _w=${_pair%%=*} _g0=${_pair#*=}
    _g1=$(gen "$_w")
    _prev=$(kcn get "$_w" -o 'jsonpath={.metadata.annotations.dms\.io/install-config-rev}' 2>/dev/null)
    [ -n "$_g0" ] || continue                       # 이번에 처음 만든 워크로드 — 새 설정으로 떴다
    [ "$_g0" = "$_g1" ] || continue                 # 템플릿이 바뀌어 이미 새 파드로 돌았다
    if [ "${_prev:-$_cfg_before}" != "$_cfg" ]; then _restart="$_restart $_w"; fi
  done
  if [ -n "$_restart" ]; then
    # shellcheck disable=SC2086
    kcn rollout restart $_restart >/dev/null 2>&1 || die "rollout restart 실패:$_restart"
    for _w in $_restart; do kcn rollout status "$_w" --timeout="${ROLLOUT_TIMEOUT:-180}s" >/dev/null 2>&1 || die "$_w 재시작 rollout 시간 초과"; done
    ok "설정이 바뀌어 다시 띄웠다:$_restart"
  else info "설정 변화 없음(또는 이미 새 파드) — 재시작하지 않는다"; fi
  for _w in deploy/dms-api deploy/dms-controller ds/dms-agent; do
    kcn annotate "$_w" "dms.io/install-config-rev=$_cfg" --overwrite >/dev/null 2>&1 || warn "$_w 에 설정 표지를 남기지 못했다(다음 실행이 재시작 여부를 덜 정확히 안다)"
  done
  # 마스터 사전 pull(설계 6-3 — dms-api 가 있던 마스터가 죽으면 새 마스터에 dms 이미지가 캐시돼 있어야 1.5~2.5분에 복구).
  _img=$(kcn get deploy dms-api -o 'jsonpath={.spec.template.spec.containers[0].image}' 2>/dev/null)
  _masters=$(kc get nodes -l node-role.kubernetes.io/control-plane -o 'jsonpath={range .items[*]}{.metadata.name}{","}{end}' 2>/dev/null)
  _masters=${_masters%,}
  if [ -n "$_img" ] && [ -n "$_masters" ]; then
    if "$PY" "$HERE/pull-probe.py" --context "$CTX" --namespace "$NS" --image "$_img" --nodes "$_masters" > "$WORK/prepull.out" 2>&1; then
      ok "마스터 사전 pull: $_img → $_masters"
    else
      sed 's/^/    /' "$WORK/prepull.out" | tail -n 6
      warn "마스터 사전 pull 실패 — 앱은 떴다. 장애조치 때 그 마스터에서 pull 이 늦을 수 있다(레지스트리 신뢰·S6)"
    fi
  else warn "마스터 사전 pull 을 건너뛰었다(dms-api 이미지·control-plane 노드를 읽지 못함)"; fi
}
gen() { kcn get "$1" -o 'jsonpath={.metadata.generation}' 2>/dev/null; }
config_rev() {
  printf 'cm=%s,secret=%s' "$(kcn get configmap dms-config -o 'jsonpath={.metadata.resourceVersion}' 2>/dev/null)" \
    "$(kcn get secret dms-secrets -o 'jsonpath={.metadata.resourceVersion}' 2>/dev/null)"
}

stage_S12() {
  # VERIFY_NO_PROBES=1 이면 파드를 띄우는 검사(PG 경로 대조·마스터 :443)를 건너뛴다 — 라이브 사이트에서 순수 읽기로 다시 볼 때.
  if [ "${VERIFY_NO_PROBES:-0}" = 1 ]; then set -- --no-probes; else set --; fi
  helper "$WORK/verify.out" env PORTAL_DOMAIN="${PORTAL_DOMAIN:-$(rv PORTAL_DOMAIN)}" PORTAL_VIP="${PORTAL_VIP:-$(rv PORTAL_VIP)}" \
    CACERT="${CACERT:-}" sh "$HERE/verify.sh" --context "$CTX" --rendered "$RDIR" "$@"
  _rc=$?
  # verify 도 WARN 이 있어도 rc 0 이다 — VIP 경유 readyz 무응답(Avi 미개통)·여분 PV 0 같은 줄이 PASS 요약에 묻히지 않게(⑦ 리뷰 2).
  _n=$(warn_lines "$WORK/verify.out")
  case $_rc in
    0) if [ "${_n:-0}" -gt 0 ]; then warn "verify WARN ${_n}건(위 [WARN] 줄 — 설치는 끝났다)"; else ok "verify PASS"; fi ;;
    *) # 사후 체크리스트(S13 — Avi·DNS·첫 관리자 등)는 S12 가 FAIL 이어도 보인다: 고칠 것이 그 목록에 있을 수 있다(2026-11-03 ⑦ 리뷰 1).
       printf '\n'; stage_S13
       die "verify FAIL — 위 [FAIL] 줄(EACCES 힌트가 보이면 SELinux 노드 프로브부터: $(base_cmd) --only S2b). 고친 뒤: $(base_cmd) --only S12" ;;
  esac
}

stage_S13() {
  # 첫 관리자: 계정 0개인 새 사이트에서 포탈 항목(「k8s 연동」·「컨트롤 상태」)을 시작하려면 먼저 있어야 한다. x-admin-token 부트스트랩만 평문
  # 비밀번호를 받는다(routes_auth.create_admin_account). 토큰은 파일(0600)로만 curl 에 넘긴다(출력·argv 없음), 비밀번호는 셸 내장 printf 로
  # stdin 에. port-forward 라 Avi VS-443·DNS 전에도 된다(2026-11-03 ⑦ 리뷰 1).
  cat <<EOF
  사후 체크리스트(운영자 — 설계 8-1 S13, 이 스크립트는 확인하지 않는다):
    [ ] 첫 관리자(이름 = values.env 의 LOCAL_ADMIN — 특권 목록이라 root 자격·전체 PV 권한, 이름이 다르면 일반 관리자). 한 터미널에서
          kubectl --context $CTX -n $NS port-forward svc/dms-api 18080:http
        다른 터미널(POSIX sh — 비밀번호에 " 와 \\ 는 쓰지 않는다):
          umask 077; H=\$(mktemp)
          kubectl --context $CTX -n $NS get secret dms-secrets -o jsonpath='{.data.DMS_ADMIN_TOKEN}' | base64 -d | sed 's/^/x-admin-token: /' > "\$H"
          stty -echo; printf 'password: '; read -r PW; stty echo; echo
          printf '{"username":"%s","password":"%s"}' '<LOCAL_ADMIN>' "\$PW" | curl --noproxy '*' -sS -H @"\$H" -H 'content-type: application/json' --data-binary @- http://127.0.0.1:18080/api/admin/accounts
          rm -f "\$H"; unset PW
        201 이면 포탈에 로그인한다(그 뒤 자동화는 봉인 로그인 — deploy/README §10-7). 이미 있으면 409 account_exists.
    [ ] 공용 스토리지 밖 사본: pg_dump(<SHARED_DIR>/dms/backup/pg)·dms-secrets·dms-pg-app(암호화) — 보관처(설계 6-4, deploy/README §2f-5)
    [ ] 현장 빌드 DMS 태그의 레지스트리 밖 사본(skopeo sync --dest dir — 설계 7-6 「백업」)
    [ ] DNS(포탈 FQDN → VIP-A)·Avi VS-443(설치 전 사이트 준비 7 — deploy/README §2g-4) 풀 마스터 3/3 헬스 up 확인. 그 뒤 VIP 경유 검증:
          $([ -n "${CACERT:-}" ] || printf "CACERT=<사내CA> ")$(base_cmd) --only S12      (FQDN·VIP 는 render.env — values.env 의 PORTAL_DOMAIN·PORTAL_VIP)
    [ ] 포탈 「k8s 연동」: 대상 클러스터·호출 노드·스토리지·StorageClass 등록(deploy/README §13-9)
    [ ] 포탈 「컨트롤 상태」: build_node_name·build_source_path·빌드 프록시 = 인터넷 노드(설계 7-5)
    [ ] 레지스트리 주소를 바꾸거나 DB 를 이관했으면 잡 이미지 재릴리스(control_state.job_image — 설계 7-4)
    [ ] Released PV 재활용 런북·마스터 운영 규칙(deploy/README §2f-5·§2f-7)
EOF
}

# =============================================================================================================================
title() {
  case "$1" in
    S1) echo "preflight·감지(읽기 전용)" ;; S2) echo "값·시크릿$([ "$PLAN" = 1 ] && echo '(확인만)')" ;; S2b) echo "SELinux 노드 프로브" ;;
    S3) echo "계획(server dry-run — 변경 없음)" ;; S4) echo "플랫폼·레지스트리" ;; S5) echo "push 대조(lock·DMS 이미지)" ;;
    S6) echo "pull 프로브" ;; S7) echo "공용 스토리지 프로브" ;; S8) echo "Gateway API·Envoy Gateway·입구" ;; S9) echo "CNPG" ;;
    S9b) echo "Volcano" ;; S10) echo "DB 층" ;; S11) echo "DMS 앱" ;; S12) echo "verify" ;; S13) echo "사후" ;;
  esac
}
printf 'install.sh — context %s · site %s · 단계:%s%s\n' "$CTX" "$SITE" "$(printf ' %s' $RUN)" "$([ "$PLAN" = 1 ] && echo ' (--plan: 클러스터를 바꾸지 않는다)')"
kc version >/dev/null 2>&1 || { echo "install.sh: 컨텍스트 '$CTX' 의 클러스터에 붙지 못했다(kubectl --context $CTX version)" >&2; exit 2; }
do_render
CUR_HAS_S1=0
case " $RUN " in *" S1 "*) CUR_HAS_S1=1 ;; esac
S4_RAN=0
for s in $RUN; do
  CUR=$s STAGE_WARN=0 STAGE_SKIP=0 STAGE_FAIL=""
  SOFT=0
  [ "$PLAN" = 1 ] && [ "$s" != S3 ] && SOFT=1
  step "$s $(title "$s")"
  "stage_$s"
  if [ -n "$STAGE_FAIL" ]; then r=$STAGE_FAIL; elif [ "$STAGE_SKIP" = 1 ]; then r=SKIP; elif [ "$STAGE_WARN" = 1 ]; then r=WARN; else r=PASS; fi
  printf 'STAGE: %s %s\n' "$s" "$r"
  CUR=""
done
if [ "$PLAN" = 1 ]; then
  # 실제 설치 명령은 FAIL·모름이 없는 계획 뒤에만, 계획과 같은 플래그·env 로(⑦ 리뷰 2 — 전에는 FAIL·모름에도 「실제 설치」를 권했고 --adopt·
  # ADDON_INSTALL·VALUES_ENV 를 뺐다: 그대로 돌리면 P 인 cnpg 에서 S1 이 FAIL 하거나 다른 values.env 로 렌더했다).
  if [ "$SOFT_FAILS" = 0 ] && [ "$SOFT_UNKNOWN" = 0 ]; then
    printf '\n계획 끝(변경 없음). 실제 설치: %s%s\n' "$(base_cmd)" "$DETECT_ARGS"
  else
    printf '\n계획 끝(변경 없음) — 위 FAIL·모름을 해소한 뒤 계획부터 다시: %s --plan%s\n' "$(base_cmd)" "$DETECT_ARGS"
  fi
  [ "$SOFT_FAILS" = 0 ] || { printf 'RESULT: FAIL\n'; exit 1; }
  [ "$SOFT_UNKNOWN" = 0 ] || { printf 'RESULT: UNKNOWN\n'; exit 4; }
fi
if [ "$WARNS" -gt 0 ]; then printf 'RESULT: WARN\n'; else printf 'RESULT: PASS\n'; fi
exit 0
