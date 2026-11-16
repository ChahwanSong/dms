#!/bin/sh
# 입구 프로브 — 클러스터의 Traefik 이 DMS Ingress 를 서빙하는가(2026-11-16 설계 D6: Envoy Gateway 폐기·Traefik 하나. 옛 host-net-probe.sh 의
# 출력·종료 관례를 따른다; install.sh S8·verify.sh 5절이 부른다, 운영자가 따로 돌려도 된다).
#
#   sh deploy/ingress-probe.sh --context <ctx> --domain <fqdn> [--nodes ip,…] [--port 443] [--timeout 30] [--cacert <CA 파일>] [--tls-only]
#
# 대상 노드: 기본 = Traefik 파드의 hostIP(Running — install-detect.py 항목 traefik 과 같은 기준: 컨테이너 이미지 이름에 traefik; RKE2 rke2-traefik
# 은 모든 노드의 DaemonSet + hostPort 443). 파드를 못 읽거나 하나도 없으면 노드 InternalIP 전부. --nodes 를 주면 그 IP 만(파드·노드를 읽지 않는다
# — curl --resolve 는 숫자 주소만 받는다).
# 노드마다 설치 호스트에서 :<port>(기본 443 — Traefik hostPort, LB VS-443 의 풀 멤버 포트)로 직접:
#   1) 인증서 — openssl s_client -servername <fqdn>: 주체 CN 또는 SAN 에 <fqdn>(와일드카드 한 단계 포함)이 있어야 한다. 「TRAEFIK DEFAULT CERT」면
#      FAIL — Traefik 에 SNI <fqdn> 의 인증서가 없다(Ingress 의 tls 시크릿·ns·class: Traefik 은 class 가 맞는 Ingress 의 tls 시크릿만 읽는다).
#      인증서를 못 보면(openssl 없음 / 있는데 s_client 가 빈 출력 — 시간 초과·핸드셰이크: 근거를 가른다) 신원은 모름 — --cacert 가 없으면
#      readyz 200 이어도 UNKNOWN 이다.
#   2) readyz — curl --noproxy '*' --resolve <fqdn>:<port>:<노드> https://<fqdn>/readyz(사내 프록시를 타지 않는다; --cacert 가 없으면 -k —
#      인증서 신원은 1)이 본다, 있으면 curl 이 체인까지 검증한다). 200 = PASS · 404 = FAIL(Traefik 이 Ingress 를 안 집었다 — ingressClassName·
#      router.entrypoints·ns, 또는 백엔드 dms-api 없음) · 그 밖 코드 = FAIL(라우터는 있는데 백엔드가 그 코드) · 연결 실패(000) = UNKNOWN.
#   --tls-only  1)만 판정한다(첫 설치 — dms-api 가 아직 없으면 Traefik 이 라우터를 만들지 않아 readyz 가 404 다; 코드는 근거로만 적는다).
#               install.sh S8 이 dms-api Endpoints 가 비었을 때 준다. Traefik 은 Ingress 의 tls 를 백엔드와 상관없이 읽는다.
#   --timeout   FAIL 인 노드를 다시 재는 총 시간(초, 기본 30 — Traefik 이 막 적용된 Ingress·시크릿을 집는 데 몇 초 걸린다). 0 = 한 번. 연결
#               실패(UNKNOWN)는 다시 재지 않는다.
# 설치 호스트에서 노드 :443 에 못 닿으면(방화벽 — LB SE 만 허용하는 사이트) UNKNOWN 이다(모름 ≠ 실패). 설치 호스트는 PROXY 헤더 없이 붙는데,
# Traefik 은 신뢰 출발지(proxyProtocol.trustedIPs) 밖의 연결은 헤더를 무시하고 없어도 받는다 — 그래서 노드 직접 확인이 된다.
#
# 종료: 0 PASS · 1 FAIL(노드 하나라도) · 4 UNKNOWN(FAIL 없음 — 모름 ≠ 실패) · 2 사용법·환경(클러스터를 건드리기 전 — 컨텍스트 없음·curl 없음).
# 노드마다 `NODE <이름|ip> <PASS|FAIL|UNKNOWN> <근거>`, 마지막 줄 `RESULT: <PASS|FAIL|UNKNOWN>`. kubectl 은 늘 --context 를 붙이고 get 만 부른다
# (클러스터를 바꾸지 않는다 — 파드도 띄우지 않는다). 도구: curl·(있으면) openssl·timeout — 설치 호스트(RHEL 9) 기본. POSIX sh(dash -n).
set -u
usage() {
  echo "usage: $0 --context <ctx> --domain <fqdn> [--nodes ip,...] [--port 443] [--timeout 30] [--cacert <CA 파일>] [--tls-only]" >&2
  exit 2
}
CTX="" DOMAIN="" NODES="" PORT=443 TIMEOUT=30 CACERT="" TLS_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --context) [ $# -ge 2 ] && [ -n "$2" ] || usage; CTX=$2; shift ;;
    --context=*) CTX=${1#*=}; [ -n "$CTX" ] || usage ;;
    --domain) [ $# -ge 2 ] && [ -n "$2" ] || usage; DOMAIN=$2; shift ;;
    --domain=*) DOMAIN=${1#*=} ;;
    --nodes) [ $# -ge 2 ] && [ -n "$2" ] || usage; NODES=$2; shift ;;
    --port) [ $# -ge 2 ] || usage; PORT=$2; shift ;;
    --timeout) [ $# -ge 2 ] || usage; TIMEOUT=$2; shift ;;
    --cacert) [ $# -ge 2 ] && [ -n "$2" ] || usage; CACERT=$2; shift ;;
    --tls-only) TLS_ONLY=1 ;;
    *) usage ;;
  esac
  shift
done
[ -n "$CTX" ] && [ -n "$DOMAIN" ] || usage
DOMAIN=$(printf '%s' "$DOMAIN" | tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')
# 호스트 이름만(와일드카드·포트·IP·경로 없음 — render.sh 의 PORTAL_DOMAIN 규칙). curl --resolve·SNI 에 그대로 들어간다.
case "$DOMAIN" in ""|*[!abcdefghijklmnopqrstuvwxyz0123456789.-]*|.*|*.|*..*|-*) usage ;; esac
case "$DOMAIN" in *[!0123456789.]*) ;; *) usage ;; esac     # IPv4 리터럴은 SNI 가 아니다(Traefik 은 IP 접속에 기본 인증서)
case "$TIMEOUT" in ""|*[!0123456789]*) usage ;; esac
case "$PORT" in ""|0*|*[!0123456789]*) usage ;; esac
[ "${#PORT}" -le 5 ] && [ "$PORT" -le 65535 ] || usage
case "$NODES" in *[!0123456789abcdefABCDEF.:,]*) usage ;; esac
[ -z "$CACERT" ] || [ -r "$CACERT" ] || { echo "CA 파일을 읽지 못했다: $CACERT" >&2; exit 2; }
command -v curl >/dev/null 2>&1 || { echo "curl 이 없다(설치 호스트 도구 — 설계 S0-7)" >&2; exit 2; }
command kubectl config get-contexts "$CTX" >/dev/null 2>&1 || { echo "컨텍스트 '$CTX' 가 kubeconfig 에 없습니다" >&2; exit 2; }
kc() { command kubectl --context "$CTX" "$@"; }
set -f      # 인증서 이름(*.example.com)을 for 로 돌 때 셸이 파일 이름으로 펼치지 않게

# --- 대상 노드 ----------------------------------------------------------------------------------------------------------------------
TARGETS=""     # 줄마다 「<표시 이름> <IP>」
if [ -n "$NODES" ]; then
  TARGETS=$(printf '%s' "$NODES" | tr ',' '\n' | awk 'NF { print $1, $1 }')
else
  if PODS=$(kc get pods -A -o 'jsonpath={range .items[*]}{.spec.nodeName}{"\t"}{.status.hostIP}{"\t"}{.status.phase}{"\t"}{range .spec.containers[*]}{.image}{" "}{end}{"\n"}{end}' 2>/dev/null); then
    # 이미지 이름 = 저장소 경로의 마지막 조각(태그·digest 뗌)에 traefik — 레지스트리 호스트 이름에 traefik 이 든 남의 이미지를 잡지 않는다.
    TARGETS=$(printf '%s\n' "$PODS" | awk -F '\t' '$3 == "Running" && $2 != "" {
        n = split($4, a, " ")
        for (i = 1; i <= n; i++) { im = a[i]; sub(/@.*/, "", im); sub(/.*\//, "", im); sub(/:.*/, "", im)
                                   if (index(tolower(im), "traefik")) { print ($1 != "" ? $1 : $2), $2; break } } }' | sort -u)
  fi
  if [ -z "$TARGETS" ]; then
    echo "INFO Traefik 파드(이미지 이름에 traefik — Running)를 찾지 못했다 — 노드 InternalIP 전부를 본다(Traefik 이 없는 노드는 연결 실패 = UNKNOWN)"
    TARGETS=$(kc get nodes -o 'jsonpath={range .items[*]}{.metadata.name}{" "}{.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}' 2>/dev/null \
      | awk 'NF >= 2 { print $1, $2 }')
  fi
fi
if [ -z "$TARGETS" ]; then
  echo "노드를 찾지 못했다(Traefik 파드·노드 InternalIP) — --nodes ip,… 로 준다" >&2
  echo "RESULT: UNKNOWN"
  exit 4
fi
echo "INFO SNI·Host $DOMAIN · 노드 :$PORT · $([ "$TLS_ONLY" = 1 ] && echo '인증서만(--tls-only)' || echo 'readyz·인증서')$([ -n "$CACERT" ] && echo " · CA $CACERT")"

# --- 한 노드 ------------------------------------------------------------------------------------------------------------------------
HAVE_OPENSSL=0
command -v openssl >/dev/null 2>&1 && HAVE_OPENSSL=1
URL_PORT=""
[ "$PORT" = 443 ] || URL_PORT=":$PORT"
_to() { if command -v timeout >/dev/null 2>&1; then timeout "$@"; else shift; "$@"; fi; }
_hostport() { case "$1" in *:*) printf '[%s]:%s' "$1" "$PORT" ;; *) printf '%s:%s' "$1" "$PORT" ;; esac; }
_raddr() { case "$1" in *:*) printf '[%s]' "$1" ;; *) printf '%s' "$1" ;; esac; }
cert_names() {   # stdin = openssl x509 -text → 소문자 이름(주체 CN·SAN DNS), 줄마다
  awk '/Subject:/ { s = $0; if (match(s, /CN *= *[^,\/]+/)) { cn = substr(s, RSTART, RLENGTH); sub(/^CN *= */, "", cn); gsub(/[ \t]+$/, "", cn); print tolower(cn) } }
       /DNS:/ { n = split($0, a, ","); for (i = 1; i <= n; i++) { x = a[i]; if (x ~ /DNS:/) { sub(/.*DNS:/, "", x); gsub(/[ \t]/, "", x); print tolower(x) } } }'
}
name_matches() {   # $1 = 인증서 이름 → DOMAIN 과 맞는가(정확히, 또는 와일드카드 한 단계 — *.corp.example 은 a.corp.example 만)
  case "$1" in
    "$DOMAIN") return 0 ;;
    "*."*) [ "${DOMAIN#*.}" = "${1#\*.}" ] && [ "${DOMAIN%%.*}" != "$DOMAIN" ] && return 0 ;;
  esac
  return 1
}
probe_once() {   # $1 = IP → V(PASS|FAIL|UNKNOWN) · WHY
  _ip=$1 _names=""
  if [ "$HAVE_OPENSSL" = 0 ]; then CERT=unknown
  else
    _cert=$(_to 8 openssl s_client -connect "$(_hostport "$_ip")" -servername "$DOMAIN" </dev/null 2>/dev/null | openssl x509 -noout -text 2>/dev/null)
    if [ -z "$_cert" ]; then CERT=none
    elif printf '%s\n' "$_cert" | grep -q 'TRAEFIK DEFAULT CERT'; then CERT=default
    else
      CERT=other
      _names=$(printf '%s\n' "$_cert" | cert_names | sort -u | tr '\n' ' ')
      for _n in $_names; do
        if name_matches "$_n"; then CERT=ok; break; fi
      done
    fi
  fi
  set -- --noproxy '*' -s -o /dev/null -w '%{http_code}' -m 5 --resolve "$DOMAIN:$PORT:$(_raddr "$_ip")"
  if [ -n "$CACERT" ]; then set -- "$@" --cacert "$CACERT"; else set -- "$@" -k; fi
  _code=$(curl "$@" "https://$DOMAIN$URL_PORT/readyz" </dev/null 2>/dev/null)
  _crc=$?
  case "$_code" in ""|*[!0123456789]*) _code=000 ;; esac
  _names=${_names% }
  case "$CERT" in
    default) V=FAIL; WHY="TRAEFIK DEFAULT CERT — Traefik 에 SNI $DOMAIN 의 인증서가 없다(Ingress 의 tls 시크릿·ns·ingressClassName: Traefik 은 class 가 맞는 Ingress 의 tls 만 읽는다) · readyz $_code"; return ;;
    other) V=FAIL; WHY="인증서 주체/SAN 에 $DOMAIN 이 없다(${_names:-이름 없음}) — PORTAL_TLS_SECRET 의 인증서·SNI · readyz $_code"; return ;;
    none) if [ "$_code" = 000 ]; then
            V=UNKNOWN; WHY="연결 실패(설치 호스트 → 노드 :$PORT — 방화벽이거나 그 노드에 Traefik 이 없다; curl rc $_crc)"; return
          fi
          # openssl 은 있는데 s_client 가 인증서를 내지 못했다(8초 시간 초과·핸드셰이크 실패·x509 파싱 실패) — curl 은 닿았으니 연결 문제는
          # 아니다. 「openssl 없음」(unknown)과 근거를 가른다(운영자가 openssl 설치로 오독하지 않게). 판정은 둘 다 모름이다.
          CERT=noread ;;
  esac
  if [ "$_code" = 000 ]; then
    case "$_crc" in
      51|58|60|77|83) if [ -n "$CACERT" ]; then
                        V=FAIL; WHY="TLS 검증 실패(curl rc $_crc — --cacert 의 CA 가 이 인증서를 서명하지 않았거나 중간 인증서가 빠졌다)$([ "$CERT" = ok ] && printf ' · 인증서 이름 OK(%s)' "$_names")"; return
                      fi ;;
    esac
    V=UNKNOWN; WHY="readyz 연결 실패(curl rc $_crc)$([ "$CERT" = ok ] && printf ' · 인증서 OK(%s)' "$_names")"; return
  fi
  # 인증서 신원: openssl 로 본 이름이 맞거나(ok), openssl 이 없어도 --cacert 로 curl 이 체인·호스트 이름을 검증해 응답을 받았으면 OK.
  _cert_ok=0 _cw=""
  if [ "$CERT" = ok ]; then _cert_ok=1; _cw="인증서 OK($_names)"
  elif [ -n "$CACERT" ]; then _cert_ok=1; _cw="인증서 OK(--cacert 로 curl 이 검증)"
  elif [ "$CERT" = noread ]; then _cw="인증서를 읽지 못했다(openssl s_client 응답 없음 — 시간 초과·핸드셰이크; --cacert 를 주면 curl 이 검증한다)"
  else _cw="인증서를 확인하지 못했다(openssl 없음 — --cacert 를 주면 curl 이 검증한다)"; fi
  if [ "$TLS_ONLY" = 1 ]; then
    if [ "$_cert_ok" = 1 ]; then V=PASS; WHY="$_cw · readyz $_code(--tls-only — 판정 밖, 앱 전)"; else V=UNKNOWN; WHY="$_cw · readyz $_code"; fi
    return
  fi
  case "$_code" in
    200) if [ "$_cert_ok" = 1 ]; then V=PASS; WHY="readyz 200 · $_cw"; else V=UNKNOWN; WHY="readyz 200 · $_cw"; fi ;;
    404) V=FAIL; WHY="readyz 404 — Traefik 이 Ingress 를 안 집었다(ingressClassName·router.entrypoints·ns) 또는 백엔드 dms-api 가 없다 · $_cw" ;;
    *) V=FAIL; WHY="readyz $_code — 라우터는 있는데 백엔드(dms-api)가 $_code 로 답한다 · $_cw" ;;
  esac
}

# --- 노드마다 ----------------------------------------------------------------------------------------------------------------------
DEADLINE=$(( $(date +%s) + TIMEOUT ))
pass=0 fail=0 unk=0
while read -r label ip; do
  [ -n "${ip:-}" ] || continue
  while :; do
    probe_once "$ip"
    # FAIL 만 다시 잰다 — Traefik 이 막 적용된 Ingress·시크릿을 집는 동안(몇 초)의 404·기본 인증서. 연결 실패는 기다려도 그대로다.
    [ "$V" = FAIL ] || break
    [ "$(date +%s)" -lt "$DEADLINE" ] || break
    sleep "${PROBE_INTERVAL:-2}"
  done
  echo "NODE $label $V $ip:$PORT $WHY"
  case "$V" in PASS) pass=$((pass + 1)) ;; FAIL) fail=$((fail + 1)) ;; *) unk=$((unk + 1)) ;; esac
done <<EOF
$TARGETS
EOF
if [ "$fail" -gt 0 ]; then echo "RESULT: FAIL"; exit 1; fi
if [ "$unk" -gt 0 ]; then echo "RESULT: UNKNOWN"; exit 4; fi
echo "RESULT: PASS"
exit 0
