#!/bin/sh
# 호환 진입점(2026-08-30 → 2026-11-03 ④): 렌더러는 deploy/render.sh 하나다 — 같은 렌더러가 prod·testbed-k8s 앱 오버레이와 플랫폼 층
# (PriorityClass·클러스터 내 레지스트리, 이후 ⑤ db)을 렌더한다(설계 v6 9 「render.sh 일반화」). 이 파일은 옛 명령
# `sh deploy/overlays/prod/render.sh [--context <ctx>]`(VALUES_ENV·KUBE_CONTEXT 포함)을 그대로 받아 `sh deploy/render.sh prod` 로 넘긴다.
# 결과는 deploy/.rendered/prod/{app,platform/...}(예전의 deploy/overlays/.prod-rendered/ 가 아니다), 마지막 줄은 그대로
# `RENDERED: <app 디렉터리>`(install.sh 가 파싱).
set -eu
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)     # deploy/overlays/prod
exec sh "$HERE/../../render.sh" prod "$@"
