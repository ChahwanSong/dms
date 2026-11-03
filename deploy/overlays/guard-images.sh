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
    echo "[guard] 거부: $DIR 은 컨텍스트 '$EXPECT' 전용인데 '$CTX'($CTX_FROM) 를 겨눴다 -- 엉뚱한 클러스터에 apply 하게 된다." >&2
    echo "[guard] 맞는 짝: sh $0 $DIR --context $EXPECT && kubectl --context $EXPECT apply -k $DIR" >&2
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
want=$(printf '%s\n' "$rendered" | awk '
  /^kind: (Deployment|DaemonSet)$/ {kind=$2; name=""; img=""}
  /^  name: / && kind!="" && name=="" {name=$2}
  /^      containers:$/ && kind!="" {inc=1; next}
  inc==1 && /image: / {sub(/^.*image: */,""); img=$0; print kind"/"name" "img; kind=""; inc=0}
')
[ -n "$want" ] || { echo "[guard] 렌더 산출물에 워크로드가 없다" >&2; exit 2; }

changes=""
while read -r ref img; do
  kind=${ref%%/*}; name=${ref#*/}
  case "$kind" in Deployment) k=deploy;; DaemonSet) k=ds;; *) continue;; esac
  live=$(kubectl "$@" -n "$NS" get "$k" "$name" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
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

where=""; next="kubectl apply -k $DIR"
if [ -n "$CTX" ]; then where=" (context $CTX)"; next="kubectl --context $CTX apply -k $DIR"; fi
if [ -n "$changes" ]; then
  if [ "$ALLOW" != 1 ]; then
    echo "[guard] 거부: 이 apply 는 라이브 워크로드 이미지를 바꾼다(포탈 릴리스를 되돌릴 수 있음)$where:$changes" >&2
    echo "[guard] 의도한 변경이면 ALLOW_IMAGE_CHANGE=1 로 다시. 아니면 오버레이 태그(newTag / values.env DMS_TAG)를 라이브에 맞춰라." >&2
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
    exit 5
  fi
  echo "[guard] hostPath 변경 허용(ALLOW_HOSTPATH_CHANGE)$where:$hp_changes"
fi
[ -n "$changes$hp_changes" ] || echo "[guard] 이미지·hostPath 변경 없음 -- apply 안전$where"
echo "[guard] 다음: $next"
exit 0
