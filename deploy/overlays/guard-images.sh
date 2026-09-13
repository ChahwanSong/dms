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
# 사용: sh deploy/overlays/guard-images.sh <오버레이 디렉터리> [--allow-image-change]
#       (install.sh 가 apply 직전에 부른다; 테스트베드는 README §4/§5 절차)
# 종료: 0 통과 / 3 이미지 변경 거부 / 2 사용법·렌더 실패
set -eu
DIR="${1:-}"; [ -n "$DIR" ] || { echo "usage: $0 <overlay-dir> [--allow-image-change]" >&2; exit 2; }
ALLOW="${ALLOW_IMAGE_CHANGE:-0}"; [ "${2:-}" = "--allow-image-change" ] && ALLOW=1
NS="${DMS_NS:-dms}"

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
  live=$(kubectl -n "$NS" get "$k" "$name" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
  [ -z "$live" ] && continue                      # 첫 설치: 라이브 없음 -> 통과
  if [ "$live" != "$img" ]; then changes="$changes
  $ref: live=$live -> overlay=$img"; fi
done <<EOF
$want
EOF

if [ -n "$changes" ]; then
  if [ "$ALLOW" = 1 ]; then
    echo "[guard] 이미지 변경 허용(ALLOW_IMAGE_CHANGE):$changes"
    exit 0
  fi
  echo "[guard] 거부: 이 apply 는 라이브 워크로드 이미지를 바꾼다(포탈 릴리스를 되돌릴 수 있음):$changes" >&2
  echo "[guard] 의도한 변경이면 ALLOW_IMAGE_CHANGE=1 로 다시. 아니면 오버레이 태그(newTag / values.env DMS_TAG)를 라이브에 맞춰라." >&2
  exit 3
fi
echo "[guard] 이미지 변경 없음 -- apply 안전"
exit 0
