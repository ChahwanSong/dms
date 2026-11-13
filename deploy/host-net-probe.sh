#!/bin/sh
# 마스터 호스트 netns 프로브(2026-11-03 ⑦, 설계 v6 4-3 「443 바인드」 — S8 마스터 전부 sysctl ≤ 리스너 포트 재확인(1024 미만일 때만)·
# S8/S12 마스터마다 :<리스너 포트>(--port, 기본 443); 기록은 docs/CHANGELOG.md 「⑦ 설치기」).
#
#   sh deploy/host-net-probe.sh --context <ctx> --image <ref> (--sysctl | --listen) [--port 443] [--nodes a,b] [--namespace dms] [--timeout 120]
#
#   --port    Envoy 리스너 포트(기본 443 — render.env 의 GATEWAY_LISTEN_PORT, 2026-11-14). --sysctl 은 「ip_unprivileged_port_start ≤ 포트」,
#             --listen 은 「:포트 LISTEN」을 본다. 1024 이상이면 sysctl 은 볼 필요가 없다(설치기가 건너뛴다).
#   --sysctl  노드마다 호스트 netns 의 net.ipv4.ip_unprivileged_port_start ≤ --port(기본 443) 인가(S8 — platform/gateway 적용 전). Envoy 는
#             비 root(65532)라 이 값이 포트보다 크면 그 마스터만 리스너 bind 에 실패한다(설계 4-3 (i), deploy/README §2g-2).
#   --listen  노드마다 호스트 netns 에 :<--port> LISTEN 소켓이 있는가(S8 Programmed 뒤·S12). 왜 따로 보는가(2026-11-03 ⑥ 리뷰 1): sysctl 이 빠진
#             마스터에서도 Envoy 는 리스너 bind 실패를 NACK 만 하고 Ready, Gateway 는 Programmed(EG 는 DaemonSet 가용 수만 본다) — 그 마스터만
#             조용히 빠지고 Avi 헬스가 거른다. 고치는 길: sysctl(2g-2) → 그 노드의 Envoy 파드 삭제(EG 는 같은 설정을 다시 내려 주지 않는다).
#   --nodes   기본 = control-plane 노드(라벨 node-role.kubernetes.io/control-plane — platform/gateway 의 Envoy DaemonSet 이 뜨는 곳).
#
# 어떻게: 노드마다 일회용 Pod 하나(ns dms — PSA privileged(S2), hostNetwork, nodeName 고정, toleration Exists, restartPolicy Never,
# activeDeadlineSeconds, 비 root 65534·cap 없음·읽기 전용 루트 — 쓰기 없음). hostNetwork 파드는 호스트 netns 를 쓰므로 /proc/sys/net 과
# /proc/net/tcp{,6} 가 그 노드 호스트의 값이다 — ssh 없이 잰다. 이미지는 sh·cat·grep 이 있는 것(설치기는 dms 이미지 — S5 가 레지스트리에 있는지
# 확인했고 S11 이 마스터에 사전 pull 한다). 끝나면(실패·Ctrl-C·SIGTERM 에도) 지운다. 시작할 때 라벨 app.kubernetes.io/name=dms-hostnet-probe 로
# 남은 옛 프로브도 지운다.
#
# 종료: 0 PASS · 1 FAIL(노드 하나라도) · 4 모름(파드가 뜨지 않음·이미지 없음·시간 초과·값을 못 읽음 — FAIL 없음, 모름 ≠ 실패) · 2 사용법·환경
# (클러스터를 건드리기 전). 노드마다 `NODE <이름> <PASS|FAIL|UNKNOWN> <근거>`, 마지막 줄 `RESULT: <PASS|FAIL|UNKNOWN>`. kubectl 은 모든 호출에
# --context 를 붙인다(필수 — current-context 에 기대지 않는다).
set -u
usage() {
  echo "usage: $0 --context <ctx> --image <ref> (--sysctl | --listen) [--port 443] [--nodes a,b] [--namespace dms] [--timeout 120]" >&2
  exit 2
}
CTX="" IMG="" MODE="" NODES="" NS=dms TIMEOUT=120 PORT=443
while [ $# -gt 0 ]; do
  case "$1" in
    --context) [ $# -ge 2 ] && [ -n "$2" ] || usage; CTX=$2; shift ;;
    --context=*) CTX=${1#*=}; [ -n "$CTX" ] || usage ;;
    --image) [ $# -ge 2 ] && [ -n "$2" ] || usage; IMG=$2; shift ;;
    --nodes) [ $# -ge 2 ] && [ -n "$2" ] || usage; NODES=$2; shift ;;
    --namespace) [ $# -ge 2 ] && [ -n "$2" ] || usage; NS=$2; shift ;;
    --timeout) [ $# -ge 2 ] || usage; TIMEOUT=$2; shift ;;
    --port) [ $# -ge 2 ] || usage; PORT=$2; shift ;;
    --sysctl) MODE=sysctl ;;
    --listen) MODE=listen ;;
    *) usage ;;
  esac
  shift
done
[ -n "$CTX" ] && [ -n "$IMG" ] && [ -n "$MODE" ] || usage
case "$TIMEOUT" in ""|*[!0-9]*) usage ;; esac
case "$PORT" in ""|0*|*[!0-9]*) usage ;; esac
[ "${#PORT}" -le 5 ] && [ "$PORT" -le 65535 ] || usage
PORT_HEX=$(printf '%04X' "$PORT")      # /proc/net/tcp 의 포트 표기(대문자 16진 4자리 — 443 = 01BB)
# 이미지·노드 이름은 아래 JSON 에 그대로 들어간다 — 따옴표·역슬래시·공백이 없는 모양만 받는다.
case "$IMG" in *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:/@-]*) usage ;; esac
case "$NODES" in *[!abcdefghijklmnopqrstuvwxyz0123456789.,-]*) usage ;; esac
command kubectl config get-contexts "$CTX" >/dev/null 2>&1 || { echo "컨텍스트 '$CTX' 가 kubeconfig 에 없습니다" >&2; exit 2; }
kc() { command kubectl --context "$CTX" "$@"; }
LABEL=app.kubernetes.io/name=dms-hostnet-probe

if [ -z "$NODES" ]; then
  NODES=$(kc get nodes -l node-role.kubernetes.io/control-plane -o 'jsonpath={range .items[*]}{.metadata.name}{","}{end}' 2>/dev/null)
  NODES=${NODES%,}
fi
if [ -z "$NODES" ]; then
  echo "control-plane 노드를 찾지 못했다(라벨 node-role.kubernetes.io/control-plane) — --nodes a,b 로 준다" >&2
  echo "RESULT: UNKNOWN"
  exit 4
fi

NONCE=$(od -An -tx1 -N4 /dev/urandom 2>/dev/null | tr -d ' \n')
[ -n "$NONCE" ] || NONCE=$$
cleanup() { kc -n "$NS" delete pod -l "$LABEL" --ignore-not-found --wait=false >/dev/null 2>&1 || \
  echo "프로브 파드를 지우지 못했다 — kubectl --context $CTX -n $NS delete pod -l $LABEL" >&2; }
trap cleanup EXIT
trap 'exit 130' INT TERM
cleanup     # 옛 프로브(중단된 실행)를 먼저 지운다 — 옛 결과를 읽지 않게

# 파드 안 스크립트 — 따옴표(")·역슬래시 없이(JSON 문자열에 그대로 넣는다). /proc/net/tcp{,6}: 「sl local rem st」, LISTEN = 0A, 443 = 01BB.
if [ "$MODE" = sysctl ]; then
  SCRIPT="echo VALUE=\$(cat /proc/sys/net/ipv4/ip_unprivileged_port_start)"
else
  SCRIPT="echo VALUE=\$(cat /proc/net/tcp /proc/net/tcp6 2>/dev/null | grep -cE '^ *[0-9]+: [0-9A-F]+:$PORT_HEX [0-9A-F]+:[0-9A-F]+ 0A ')"
fi
DEADLINE=$((TIMEOUT + 30))
i=0
for node in $(printf '%s' "$NODES" | tr ',' ' '); do
  i=$((i + 1))
  printf '{"apiVersion":"v1","kind":"Pod","metadata":{"name":"dms-hostnet-probe-%s-%s","namespace":"%s","labels":{"app.kubernetes.io/name":"dms-hostnet-probe","app.kubernetes.io/part-of":"dms","dms.io/probe-nonce":"%s"}},"spec":{"nodeName":"%s","hostNetwork":true,"dnsPolicy":"ClusterFirstWithHostNet","restartPolicy":"Never","activeDeadlineSeconds":%s,"terminationGracePeriodSeconds":0,"automountServiceAccountToken":false,"tolerations":[{"operator":"Exists"}],"securityContext":{"runAsNonRoot":true,"runAsUser":65534,"runAsGroup":65534},"containers":[{"name":"probe","image":"%s","imagePullPolicy":"IfNotPresent","command":["sh","-c","%s"],"securityContext":{"allowPrivilegeEscalation":false,"readOnlyRootFilesystem":true,"capabilities":{"drop":["ALL"]}},"resources":{"requests":{"cpu":"10m","memory":"16Mi"},"limits":{"cpu":"100m","memory":"64Mi"}}}]}}' \
    "$NONCE" "$i" "$NS" "$NONCE" "$node" "$DEADLINE" "$IMG" "$SCRIPT" | kc create -f - >/dev/null 2>&1 \
    || echo "NODE $node UNKNOWN 프로브 파드를 만들지 못했다(ns $NS·PSA·권한)"
done

# 모든 파드가 끝나거나(Succeeded·Failed) 이미지 문제로 멈출 때까지 기다린다.
end=$(( $(date +%s) + TIMEOUT ))
SEL="$LABEL,dms.io/probe-nonce=$NONCE"
while :; do
  st=$(kc -n "$NS" get pod -l "$SEL" -o 'jsonpath={range .items[*]}{.status.phase}/{.status.containerStatuses[0].state.waiting.reason}{" "}{end}' 2>/dev/null)
  pending=0
  for s in $st; do
    case "$s" in Succeeded/*|Failed/*|*/ErrImagePull|*/ImagePullBackOff|*/ErrImageNeverPull|*/InvalidImageName) ;; *) pending=1 ;; esac
  done
  [ "$pending" = 1 ] || break
  [ "$(date +%s)" -lt "$end" ] || break
  sleep 2
done

fail=0 unk=0 pass=0
rows=$(kc -n "$NS" get pod -l "$SEL" -o 'jsonpath={range .items[*]}{.metadata.name} {.spec.nodeName} {.status.phase} {.status.containerStatuses[0].state.waiting.reason}{"\n"}{end}' 2>/dev/null)
seen=""
while read -r pod node phase why; do
  [ -n "$pod" ] || continue
  seen="$seen,$node"
  val=$(kc -n "$NS" logs "$pod" 2>/dev/null | sed -n 's/^VALUE=\([0-9][0-9]*\)$/\1/p' | head -n 1)
  if [ -z "$val" ]; then
    echo "NODE $node UNKNOWN 값을 읽지 못했다(phase ${phase:-?}${why:+ · $why}) — 이미지가 그 노드에 없거나 레지스트리 신뢰(S6)·시간 초과(${TIMEOUT}초)"
    unk=$((unk + 1)); continue
  fi
  if [ "$MODE" = sysctl ]; then
    if [ "$val" -le "$PORT" ]; then echo "NODE $node PASS ip_unprivileged_port_start=$val"; pass=$((pass + 1))
    else echo "NODE $node FAIL ip_unprivileged_port_start=$val > $PORT — /etc/sysctl.d/90-dms-envoy.conf + sysctl --system(deploy/README §2g-2, S0-5)"; fail=$((fail + 1)); fi
  else
    if [ "$val" -ge 1 ]; then echo "NODE $node PASS :$PORT LISTEN(${val}개)"; pass=$((pass + 1))
    else echo "NODE $node FAIL :$PORT LISTEN 없음 — sysctl(§2g-2)을 고친 뒤 그 노드의 Envoy 파드를 지운다(EG 가 같은 설정을 다시 내려 주지 않는다)"; fail=$((fail + 1)); fi
  fi
done <<EOF
$rows
EOF
for node in $(printf '%s' "$NODES" | tr ',' ' '); do
  case "$seen," in *",$node,"*) ;; *) echo "NODE $node UNKNOWN 프로브 파드가 없다"; unk=$((unk + 1)) ;; esac
done
if [ "$fail" -gt 0 ]; then echo "RESULT: FAIL"; exit 1; fi
if [ "$unk" -gt 0 ]; then echo "RESULT: UNKNOWN"; exit 4; fi
echo "RESULT: PASS"
exit 0
