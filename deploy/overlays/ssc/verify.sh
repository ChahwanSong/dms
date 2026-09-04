#!/bin/sh
# DMS SSC 설치 후 검증 — PASS/FAIL 한 줄씩(2026-08-30, 웹 노드 hostNetwork 엣지).
# prod/verify 와 달리 포탈 접근을 MetalLB VIP 가 아니라 **public IP(웹 노드)** 로
# 확인하고, ingress-nginx 가 그 노드에 hostNetwork 로 떠 있는지 본다.
#
# 사용:  PORTAL_PUBLIC_IP=203.0.113.10 PORTAL_DOMAIN=dms.ssc.example \
#        CACERT=/path/ca.crt sh deploy/overlays/ssc/verify.sh
#   PORTAL_PUBLIC_IP 없으면 HTTPS 검사는 건너뛴다(클러스터 워크로드만 검증).
#   CACERT 있으면 인증서 체인까지 검증하고, 없으면 -k 로 도달성만 확인한다(사내
#   PKI 인증서는 CACERT 없이는 검증 실패라 도달성만 보는 게 옳다).
set -u
NS="${DMS_NS:-dms}"
INGRESS_NS="${INGRESS_NS:-ingress-nginx}"
PUBIP="${PORTAL_PUBLIC_IP:-}"
DOMAIN="${PORTAL_DOMAIN:-}"
PORT="${PORTAL_PORT:-443}"     # ingress-nginx https 리슨 포트(표준 443 기본; 비표준이면 값 파일과 동일하게)
CACERT="${CACERT:-}"
p=0; f=0
P(){ printf '  [PASS] %s\n' "$1"; p=$((p+1)); }
F(){ printf '  [FAIL] %s\n' "$1"; f=$((f+1)); }
I(){ printf '  [INFO] %s\n' "$1"; }
H(){ printf '\n== %s ==\n' "$1"; }

H "1. 마이그레이션 / 워크로드"
mig=$(kubectl -n "$NS" get job dms-migrate -o jsonpath='{.status.succeeded}' 2>/dev/null || echo 0)
[ "${mig:-0}" = "1" ] && P "migrate 완료" || F "migrate 미완료 (succeeded=${mig:-0})"
for d in dms-api dms-controller; do
  ready=$(kubectl -n "$NS" get deploy "$d" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
  want=$(kubectl -n "$NS" get deploy "$d" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0)
  [ -n "${ready:-}" ] && [ "${ready:-0}" = "${want:-0}" ] && [ "${want:-0}" != "0" ] \
    && P "$d 롤아웃 ${ready}/${want}" || F "$d 롤아웃 ${ready:-0}/${want:-0}"
done
ar=$(kubectl -n "$NS" get ds dms-agent -o jsonpath='{.status.numberReady}' 2>/dev/null || echo 0)
aw=$(kubectl -n "$NS" get ds dms-agent -o jsonpath='{.status.desiredNumberScheduled}' 2>/dev/null || echo 0)
[ "${ar:-0}" = "${aw:-0}" ] && [ "${aw:-0}" != "0" ] && P "dms-agent ${ar}/${aw}" || F "dms-agent ${ar:-0}/${aw:-0}"

H "2. 엣지: ingress-nginx hostNetwork · 웹 노드 고정"
CPOD=$(kubectl -n "$INGRESS_NS" get pods -l app.kubernetes.io/component=controller \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -n "$CPOD" ]; then
  hn=$(kubectl -n "$INGRESS_NS" get pod "$CPOD" -o jsonpath='{.spec.hostNetwork}' 2>/dev/null || true)
  nd=$(kubectl -n "$INGRESS_NS" get pod "$CPOD" -o jsonpath='{.spec.nodeName}' 2>/dev/null || true)
  [ "$hn" = "true" ] && P "ingress-nginx hostNetwork=true" || F "ingress-nginx hostNetwork 아님 ($hn)"
  I "ingress-nginx 파드 노드: $nd"
else
  F "ingress-nginx 컨트롤러 파드를 못 찾음 ($INGRESS_NS)"
fi

H "3. 포탈 (public IP:$PORT · readyz 는 DB 까지 확인)"
# CACERT 있으면 인증서 체인까지 검증, 없으면 -k 로 도달성만 확인한다(사내 PKI 를
# 시스템 신뢰저장소가 모르므로, CACERT 없이 검증하면 건강한 포탈도 거짓 FAIL 이
# 난다 -- 리뷰 LOW-2). curl 은 실패해도 -w 로 000 을 찍으므로 별도 || echo 000 은
# 이중 000 을 만든다(LOW-1) -- 캡처 후 빈 값만 000 으로 보정한다.
if [ -n "$CACERT" ]; then cc="--cacert $CACERT"
else cc="-k"; I "CACERT 미설정 — 인증서 검증 생략(-k), 도달성만 확인"; fi
hc(){ c=$(curl -s $cc -m5 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null); printf '%s' "${c:-000}"; }
if [ -n "$PUBIP" ]; then
  # SNI 없는 IP 직접 접속 — 컨트롤러 --default-ssl-certificate 가 인증서를 준다.
  # 포트는 https 리슨 포트($PORT, 표준 443 기본). DMS 는 HTTPS 전용.
  code=$(hc "https://$PUBIP:$PORT/readyz")
  [ "$code" = "200" ] && P "https://$PUBIP:$PORT/readyz 200 (DB 도달)" \
                      || F "https://$PUBIP:$PORT/readyz $code"
else
  I "PORTAL_PUBLIC_IP 미설정 — IP 접속 검증 건너뜀"
fi
if [ -n "$DOMAIN" ]; then
  # 도메인 접속(SNI 매칭). CACERT 로 검증하려면 DNS 가 PUBIP 로 풀려야 한다.
  dcode=$(curl -s $cc --resolve "$DOMAIN:$PORT:${PUBIP:-127.0.0.1}" -m5 -o /dev/null \
    -w '%{http_code}' "https://$DOMAIN:$PORT/readyz" 2>/dev/null); dcode=${dcode:-000}
  [ "$dcode" = "200" ] && P "https://$DOMAIN:$PORT/readyz 200 (SNI)" \
                       || I "domain readyz $dcode (DNS/SAN 확인)"
fi

printf '\n== 요약 ==  PASS=%d  FAIL=%d\n' "$p" "$f"
[ "$f" -eq 0 ]
