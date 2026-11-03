#!/bin/sh
# 오버레이 apply 가드(2026-09-14): 렌더된 워크로드 이미지가 **라이브와 다르면 거부**한다.
# 명시 플래그(ALLOW_IMAGE_CHANGE=1 또는 --allow-image-change)가 있을 때만 통과.
#
# 왜: 포탈 릴리스는 라이브 워크로드만 바꾸고 오버레이 파일(newTag / values.env DMS_TAG)은
# 그대로다. 그 뒤 설정 변경 등으로 `kubectl apply -k` 를 다시 돌리면 오버레이의 옛 태그가
# 라이브를 **조용히 되돌린다**(테스트베드 d130→d129 실증, 프로덕션 실사고의 잔여 형태).
# 이 가드는 "apply -k 는 이미지를 암묵적으로 바꾸지 않는다"를 강제한다 -- 이미지를 바꾸려는
# apply 는 운영자가 플래그로 의도를 밝혀야 한다. 첫 설치(라이브 워크로드 없음)는 통과.
#
# 컨텍스트(2026-10-21, 테스트베드가 둘이 되며): 두 클러스터가 같은 네임스페이스(dms)를 쓰므로 엉뚱한 클러스터에
# apply 하는 것이 새 사고 유형이다. 오버레이 디렉터리에 `kube-context` 파일(첫 비주석 줄 = 컨텍스트 이름)이 있으면
# 그 오버레이는 그 컨텍스트 전용이다 -- 겨눈 컨텍스트(--context / KUBE_CONTEXT, 없으면 현재 컨텍스트)가 다르면
# 거부(exit 4). 예: dmsk8s 전용 오버레이를 dms 에, dms 전용을 dmsk8s 에. 파일이 없는 오버레이(prod·ssc 사이트)는
# 종전 그대로다(--context 를 주면 그 컨텍스트로 라이브를 읽을 뿐; 환경변수 KUBE_CONTEXT 만 있고 현재 컨텍스트와 다르면
# 부르는 install.sh 의 apply 대상과 어긋나므로 거부). 가드는 뒤따르는 apply 명령을 통제하지 못한다 --
# 통과 메시지가 출력하는 `kubectl --context <ctx> apply -k <dir>` 을 그대로 쓴다.
#
# hostPath(2026-11-03 ③ 리뷰): 렌더된 Deployment 의 hostPath 경로 집합이 라이브와 다르면 **같은 방식으로 거부**한다(exit 5,
# ALLOW_HOSTPATH_CHANGE=1 또는 --allow-hostpath-change 로만 통과). 공용 디렉터리(SHARED_DIR) 전환은 api·controller 의
# hostPath(<SHARED_DIR>/dms/artifacts, type Directory)를 옮긴다 -- 그 경로가 아직 없는 클러스터에 태그만 올린 apply 가 섞이면
# 새 파드는 FailedMount 로 멈추고(롤링 maxUnavailable 0 이라 옛 파드는 산다), 옛 파드가 재시작하면 옛 마운트 + 새 ConfigMap
# (새 base·allowlist)으로 떠 summary 를 못 읽는다(잡이 summary_unavailable). 이미지 되돌림과 같은 「apply -k 가 암묵적으로
# 배치를 바꾸는」 사고라 같은 가드에 둔다. 경로를 옮기는 apply 는 전환 런북(deploy/README.md)을 따른 뒤에만 플래그로 연다.
#
# 플랫폼 층(2026-11-03 ④): 렌더된 레지스트리 층(deploy/.rendered/<site>/platform/registry)에도 같은 가드를 건다 — 워크로드 추출이
# Deployment 일반이라 그대로 동작한다. hostPath(<SHARED_DIR>/dms/registry)가 바뀌면 exit 5(레지스트리가 빈 저장소로, 또는 센티널 불일치로
# 뜬다), 이미지(<IP>:<PORT>/registry:<태그> — 태그 ref 예외, 설계 7-5)가 바뀌면 exit 3 이고 설계 7-6 업그레이드 순서를 함께 알린다(새
# 이미지를 마스터 전부에 먼저 pull·고정하지 않으면 다른 마스터로 옮겨진 파드가 뜨지 못한다). 렌더 결과가 DMS 소유 층(라벨
# app.kubernetes.io/managed-by: dms-install)이면 통과 메시지의 다음 명령이 SSA(--server-side --field-manager=dms-install)다 — 앱 오버레이는
# 지금처럼 client-side apply(포탈 릴리스가 strategic-merge 로 image 를 바꾼다, 설계 8-2). 컨텍스트 거부(exit 4)의 「맞는 짝」 안내도 같은
# 구분을 따르되, 렌더 전이라 디렉터리 배치(부모가 platform·addons)로 가른다. 워크로드가 없는 층(priorityclass)은 가드 대상이 아니다(exit 2).
#
# 사용: sh deploy/overlays/guard-images.sh <오버레이 디렉터리> [--allow-image-change] [--allow-hostpath-change] [--context <ctx>]
#       KUBE_CONTEXT=<ctx> 환경변수도 같다(인자가 이긴다).
#       (install.sh 가 apply 직전에 부른다; 테스트베드는 deploy/README.md 의 테스트베드 적용 절차 — 브랜치마다
#        싣는 테스트베드 오버레이가 다르다: main·dms-ssc = testbed(dms), dms-k8s = testbed-k8s(dmsk8s))
# 종료: 0 통과 / 2 사용법·렌더 실패·없는 컨텍스트 / 3 이미지 변경 거부 / 4 컨텍스트 불일치 거부 / 5 hostPath 변경 거부
#       (이미지·hostPath 가 둘 다 바뀌면 3 이 먼저다 -- 각각의 플래그를 다 줘야 통과)
set -eu
usage() { echo "usage: $0 <overlay-dir> [--allow-image-change] [--allow-hostpath-change] [--context <ctx>]" >&2; exit 2; }
DIR="${1:-}"; [ -n "$DIR" ] || usage
case "$DIR" in -*) usage;; esac
shift
ALLOW="${ALLOW_IMAGE_CHANGE:-0}"
ALLOW_HP="${ALLOW_HOSTPATH_CHANGE:-0}"
CTX="${KUBE_CONTEXT:-}"; CTX_FROM="KUBE_CONTEXT"; [ -n "$CTX" ] || CTX_FROM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --allow-image-change) ALLOW=1 ;;
    --allow-hostpath-change) ALLOW_HP=1 ;;
    --context) [ $# -ge 2 ] && [ -n "$2" ] || usage; CTX="$2"; CTX_FROM="--context"; shift ;;
    --context=*) CTX="${1#--context=}"; [ -n "$CTX" ] || usage; CTX_FROM="--context" ;;
    *) usage ;;
  esac
  shift
done
NS="${DMS_NS:-dms}"

# 오버레이가 고정한 컨텍스트(kube-context 파일). 비어 있으면 설정 오류로 본다(조용히 "고정 없음"이 되지 않게).
EXPECT=""
if [ -f "$DIR/kube-context" ]; then
  EXPECT=$(sed -e 's/#.*$//' -e 's/[[:space:]]//g' "$DIR/kube-context" | grep -v '^$' | head -n 1 || true)
  [ -n "$EXPECT" ] || { echo "[guard] $DIR/kube-context 에 컨텍스트 이름이 없다" >&2; exit 2; }
  if [ -z "$CTX" ]; then
    CTX=$(kubectl config current-context 2>/dev/null || true)
    CTX_FROM="현재 컨텍스트"
    [ -n "$CTX" ] || { echo "[guard] 현재 컨텍스트가 없다 -- --context $EXPECT 로 다시" >&2; exit 4; }
  fi
  if [ "$CTX" != "$EXPECT" ]; then
    # 안내의 apply 모양(2026-11-03 ④ 리뷰): 여기서는 아직 렌더(kubectl kustomize)하지 않는다(거부는 아무것도 읽기 전) -- 그래서 아래
    # managed-by 라벨 대신 디렉터리 배치로 고른다: 부모 디렉터리가 platform·addons(DMS 소유 층 — deploy/platform/<층>,
    # deploy/.rendered/<site>/platform/<층>)면 SSA, 그 밖(앱 오버레이)은 client-side.
    _ssa=apply
    case "$(basename "$(dirname "${DIR%/}")")" in platform|addons) _ssa="apply --server-side --field-manager=dms-install" ;; esac
    echo "[guard] 거부: $DIR 은 컨텍스트 '$EXPECT' 전용인데 '$CTX'($CTX_FROM) 를 겨눴다 -- 엉뚱한 클러스터에 apply 하게 된다." >&2
    echo "[guard] 맞는 짝: sh $0 $DIR --context $EXPECT && kubectl --context $EXPECT $_ssa -k $DIR" >&2
    exit 4
  fi
elif [ "$CTX_FROM" = "KUBE_CONTEXT" ]; then
  # 고정 없는 오버레이 + 환경변수 컨텍스트: 부르는 스크립트(install.sh)는 현재 컨텍스트로 apply 하므로, 환경에 남은
  # KUBE_CONTEXT 가 다른 클러스터를 가리키면 가드가 엉뚱한 클러스터의 라이브를 보고 통과시킨다 -- 모호하면 거부한다.
  CUR=$(kubectl config current-context 2>/dev/null || true)
  if [ "$CTX" != "$CUR" ]; then
    echo "[guard] 거부: KUBE_CONTEXT='$CTX' 가 현재 컨텍스트 '${CUR:-없음}' 와 다르다 -- 고정(kube-context) 없는 오버레이라 apply 가 어느 클러스터로 갈지 모호하다." >&2
    echo "[guard] 의도한 것이면 --context $CTX 로 명시하고 apply 에도 같은 --context 를 쓴다." >&2
    exit 4
  fi
fi
if [ -n "$CTX" ]; then
  # 오타 컨텍스트로 라이브 조회가 전부 실패하면 "첫 설치" 로 보여 통과한다 -- kubeconfig 에서 먼저 확인한다.
  kubectl config get-contexts "$CTX" >/dev/null 2>&1 \
    || { echo "[guard] kubeconfig 에 컨텍스트 '$CTX' 가 없다" >&2; exit 2; }
  set -- --context "$CTX"
else
  set --
fi

rendered=$(kubectl kustomize "$DIR") || { echo "[guard] kustomize 실패: $DIR" >&2; exit 2; }
# 렌더 산출물에서 워크로드별 컨테이너 이미지(첫 컨테이너 = 본 컨테이너)를 뽑는다.
# 네임스페이스(2026-11-03 ⑤): 워크로드의 metadata.namespace 를 같이 뽑아 그 네임스페이스에서 라이브를 읽는다 — CNPG 오퍼레이터
# (addons/cnpg — cnpg-system)를 DMS_NS(dms)에서 찾으면 「첫 설치」로 보여 이미지 교체(= CNPG 업그레이드)가 조용히 통과한다. 없으면 DMS_NS.
want=$(printf '%s\n' "$rendered" | awk '
  /^kind: (Deployment|DaemonSet)$/ {kind=$2; name=""; ns=""; img=""}
  /^  name: / && kind!="" && name=="" {name=$2}
  /^  namespace: / && kind!="" && ns=="" {ns=$2}
  /^      containers:$/ && kind!="" {inc=1; next}
  inc==1 && /image: / {sub(/^.*image: */,""); img=$0; print kind"/"name" "img" "ns; kind=""; inc=0}
')
[ -n "$want" ] || { echo "[guard] 렌더 산출물에 워크로드가 없다" >&2; exit 2; }

changes=""
while read -r ref img wns; do
  kind=${ref%%/*}; name=${ref#*/}
  case "$kind" in Deployment) k=deploy;; DaemonSet) k=ds;; *) continue;; esac
  live=$(kubectl "$@" -n "${wns:-$NS}" get "$k" "$name" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
  [ -z "$live" ] && continue                      # 첫 설치: 라이브 없음 -> 통과
  if [ "$live" != "$img" ]; then changes="$changes
  $ref: live=$live -> overlay=$img"; fi
done <<EOF
$want
EOF

# Deployment 별 hostPath 경로(2026-11-03 ③ 리뷰, 헤더 「hostPath」). kustomize 출력은 키를 정렬해 volumes 원소가
# `      - hostPath:` 다음 줄 `          path: <경로>` 모양이다. 줄마다 "<이름> <경로>". 경로가 없는 Deployment 는 줄을 내지 않는다.
want_hp=$(printf '%s\n' "$rendered" | awk '
  /^---/ {kind=""; name=""; hp=0}
  /^kind: / {kind=$2; name=""; hp=0}
  /^  name: / && kind=="Deployment" && name=="" {name=$2}
  kind=="Deployment" && /^      - hostPath:$/ {hp=1; next}
  hp==1 && /^          path: / {sub(/^ *path: */,""); print name" "$0; hp=0; next}
  hp==1 && !/^          / {hp=0}
')
hp_changes=""
for name in $(printf '%s\n' "$want_hp" | awk 'NF {print $1}' | sort -u); do
  want_paths=$(printf '%s\n' "$want_hp" | awk -v n="$name" '$1==n {print $2}' | sort | tr '\n' ' ')
  # 라이브가 없으면(첫 설치·새 워크로드) 비교하지 않는다. 있으면 hostPath 볼륨 경로만 본다(다른 볼륨은 빈 줄).
  live_raw=$(kubectl "$@" -n "$NS" get deploy "$name" \
    -o jsonpath='{range .spec.template.spec.volumes[*]}{.hostPath.path}{"\n"}{end}' 2>/dev/null) || continue
  live_paths=$(printf '%s\n' "$live_raw" | grep -v '^$' | sort | tr '\n' ' ' || true)
  if [ "$live_paths" != "$want_paths" ]; then hp_changes="$hp_changes
  Deployment/$name hostPath: live=[ ${live_paths}] -> overlay=[ ${want_paths}]"; fi
done

# DMS 소유 층(platform·addons — 라벨 managed-by dms-install)은 SSA 로 적용한다(설계 8-2). 앱 오버레이에는 이 라벨이 없다.
apply="apply"
case "$rendered" in *"app.kubernetes.io/managed-by: dms-install"*) apply="apply --server-side --field-manager=dms-install" ;; esac
where=""; next="kubectl $apply -k $DIR"
if [ -n "$CTX" ]; then where=" (context $CTX)"; next="kubectl --context $CTX $apply -k $DIR"; fi
if [ -n "$changes" ]; then
  if [ "$ALLOW" != 1 ]; then
    echo "[guard] 거부: 이 apply 는 라이브 워크로드 이미지를 바꾼다(포탈 릴리스를 되돌릴 수 있음)$where:$changes" >&2
    echo "[guard] 의도한 변경이면 ALLOW_IMAGE_CHANGE=1 로 다시. 아니면 오버레이 태그(newTag / values.env DMS_TAG)를 라이브에 맞춰라." >&2
    case "$changes" in *"Deployment/dms-registry:"*)
      echo "[guard] dms-registry 이미지 교체(설계 v6 7-6 업그레이드): 인터넷 노드가 새 이미지를 레지스트리에 push → 마스터 전부에서 crictl pull 하고" >&2
      echo "[guard] 고정 목록(/etc/crio/crio.conf.d/99-dms-pinned.conf)이 새 태그를 덮는지 확인 → 그 뒤에 ALLOW_IMAGE_CHANGE=1. 빌드·push 가 없을 때 한다." >&2 ;;
    esac
    case "$changes" in *"Deployment/cnpg-controller-manager:"*)
      # 2026-11-03 ⑤: 오퍼레이터 이미지 = OPERATOR_IMAGE_NAME(PG 인스턴스 파드의 bootstrap 이미지)이라 바뀌면 인스턴스가 롤링(+ switchover)된다.
      echo "[guard] CNPG 오퍼레이터 교체 = CNPG 업그레이드(설계 v6 6-5, deploy/README §2f-6): minor 는 하나씩, 테스트베드 리허설·직전 덤프 뒤, 새 이미지를" >&2
      echo "[guard] 레지스트리에 push(S5) → ALLOW_IMAGE_CHANGE=1 로 SSA → 오퍼레이터 rollout → PG 인스턴스 롤링·switchover 완료 대기(그동안 수 초 500)." >&2 ;;
    esac
    exit 3
  fi
  echo "[guard] 이미지 변경 허용(ALLOW_IMAGE_CHANGE)$where:$changes"
fi
if [ -n "$hp_changes" ]; then
  if [ "$ALLOW_HP" != 1 ]; then
    echo "[guard] 거부: 이 apply 는 라이브 Deployment 의 hostPath(공용 디렉터리 배치)를 바꾼다$where:$hp_changes" >&2
    echo "[guard] 새 경로가 노드에 없으면 새 파드는 FailedMount 로 멈추고, 옛 파드가 재시작하면 옛 마운트 + 새 설정(base·allowlist)으로 떠" >&2
    echo "[guard] 결과 파일을 못 읽는다. 공용 디렉터리 전환 런북(deploy/README.md -- 노드 마운트·S0·프로브·복사)을 마친 뒤 ALLOW_HOSTPATH_CHANGE=1 로 다시." >&2
    echo "[guard] 의도하지 않았다면 오버레이의 SHARED_DIR(values.env·DMS_ARTIFACT_BASE_URI)를 라이브 배치에 맞춰라." >&2
    case "$hp_changes" in *"Deployment/dms-registry hostPath"*)
      # 순서(2026-11-03 ④ 리뷰 2): 센티널을 복사에서 뺀다 — 새 경로의 S0 는 새 무작위 id 를 쓰고 values 의 SHARED_DIR_ID 도 그 값이
      # 되는데, 옛 registry/ 를 통째로 rsync 하면 .dms-registry-id 가 옛 값으로 덮여 「센티널 불일치」로 기동을 거부한다.
      echo "[guard] dms-registry 저장소를 옮기면 레지스트리는 새 경로의 센티널을 검사하고(없거나 다르면 기동 거부) 옛 이미지는 새 경로에 없다 --" >&2
      echo "[guard] 빌드·push 가 없을 때, 앱 재적용보다 먼저: 1) 새 경로에 S0(sh deploy/shared-dir-init.sh <새 SHARED_DIR>)" >&2
      echo "[guard] 2) rsync -aH --numeric-ids --exclude=/.dms-registry-id <옛 SHARED_DIR>/dms/registry/ <새 SHARED_DIR>/dms/registry/ (센티널은 새 S0 값 유지, 65532 소유 보존)" >&2
      echo "[guard] 3) values.env 의 SHARED_DIR_ID = 새 <SHARED_DIR>/dms/.dms-shared-id  4) sh deploy/render.sh <site> --context <ctx>" >&2
      echo "[guard] 5) ALLOW_HOSTPATH_CHANGE=1 로 이 가드를 통과한 뒤 SSA 적용 → rollout status (deploy/README §2e-5 「저장소 이동」)." >&2 ;;
    esac
    exit 5
  fi
  echo "[guard] hostPath 변경 허용(ALLOW_HOSTPATH_CHANGE)$where:$hp_changes"
fi
[ -n "$changes$hp_changes" ] || echo "[guard] 이미지·hostPath 변경 없음 -- apply 안전$where"
echo "[guard] 다음: $next"
exit 0
