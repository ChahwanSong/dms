#!/bin/sh
# 인터넷 노드의 이미지 반입(S5 push) — deploy/images.lock → skopeo copy (2026-11-03 ⑦, 설계 v6 7-5 ③·8-1 S5).
#
#   sh deploy/push-images.sh --registry <IP:PORT> [--print | --run] [--only <이름>]... [--skip <이름>]... [--lock <파일>]
#                            [--dest-tls-verify <false|true>]
#
#   --registry   레지스트리 host:port — in-cluster 면 values.env 의 <REGISTRY_CLUSTER_IP>:<REGISTRY_PORT>, external 사이트면 그 REGISTRY
#                (lock 전부를 사이트 레지스트리로 — deploy/README §2f-3·§2g-3). 경로 접두·스킴은 받지 않는다(DMS 계약: host:port 만).
#   --print(기본) 실행할 skopeo 명령만 출력한다(네트워크·skopeo 없이 — install.sh 가 S4 뒤에 멈추며 보여 준다). 출력은 그대로 붙여 넣을
#                수 있는 sh 다(안내는 # 주석).
#   --run        인터넷 노드에서 실제로 push 한다. 줄마다 레지스트리의 그 태그가 이미 lock digest 면 건너뛰고(멱등 — 끊겼으면 다시 돌린다),
#                아니면 skopeo copy 뒤 digest 를 다시 읽어 lock 과 같은지 본다. 한 줄이 실패해도 나머지는 계속하고 끝에 FAIL.
#   --only/--skip  DEST(<이름>:<태그>) 또는 그 <이름>. 여러 번 줄 수 있다. 어느 줄에도 맞지 않으면 사용법 오류(오타가 조용히 0개가 되지
#                않게).
#   --dest-tls-verify  기본 false — 클러스터 내 레지스트리는 평문 HTTP(설계 7-2). TLS 인 사이트 레지스트리면 true.
#
# 명령 모양(설계 7-5 ③):
#   skopeo copy --preserve-digests --dest-tls-verify=false docker://<SOURCE 의 저장소>@<DIGEST> docker://<REG>/<DEST>
#   - 소스는 SOURCE 의 태그가 아니라 lock 의 linux/amd64 **매니페스트** digest 로 집는다 — 그 매니페스트 하나만 복사된다(태그는 움직일
#     수 있다; index digest 를 적으면 레지스트리에 index 가 없어 manifest unknown — lock 검사가 막는다).
#   - --preserve-digests: 매니페스트를 바꿔야 하면(압축 변환 등) 복사를 실패시킨다 — 레지스트리 digest == lock digest 가 S5 대조와
#     렌더(<REG>/<DEST>@<DIGEST>)의 전제다.
#   - DEST 는 늘 태그를 붙인다(GC --delete-untagged 가 태그 없는 digest 를 지운다).
#   - 레지스트리 자기 이미지(registry:<태그>)도 같은 줄로 push 한다(업그레이드·새 마스터·S6 pull 프로브용). 레지스트리가 뜨기 전 마스터에
#     넣는 첫 반입은 oci-archive 경로다(설계 7-5 ②, deploy/README §2e-4 2) — S0-5) — 이 스크립트 몫이 아니다.
#
# 프록시(설계 7-5): 상류 pull 은 env HTTPS_PROXY(·HTTP_PROXY)를 타고, 레지스트리 push 는 타면 안 된다(프록시는 ClusterIP 에 닿지 않는다 —
#   포탈 빌드와 같은 규칙, deploy/README §8 3b-1)). skopeo(Go)는 NO_PROXY(비었으면 no_proxy)로 가른다. 프록시 env 가 있는데
#   NO_PROXY 가 레지스트리 호스트를 덮지 않으면 --run 은 아무것도 하지 않고 멈추고(exit 1), --print 는 stderr 에 경고한다. 덮는지는
#   Go(golang.org/x/net/http/httpproxy)의 규칙대로 본다: `*`, 정확한 IP(·:포트), CIDR, 도메인 접미(foo.com·.foo.com·*.foo.com),
#   localhost·127/8 은 늘 제외.
#
# lock 은 sh 소비자 규칙으로 읽는다(grep -v '^#' | read -r dest src dg — deploy/images.lock 머리 주석). python3 이 있으면 먼저
# `images-lock.py check`(형식 전부)를 돌리고, 없어도 skopeo 에 넘기는 세 칸의 모양(칸 수·문자·digest)은 여기서 다시 본다.
#
# 종료: 0 PASS(--print 는 lock·인자가 맞으면 늘 0) · 1 FAIL(lock 형식, --run 의 NO_PROXY·push·확인 실패) · 2 사용법·환경(인자, --run 에
#       skopeo·sha256sum 없음). --run 의 마지막 줄 `RESULT: <PASS|FAIL>`.
# 다음 단계: 설치 호스트에서 install.sh --stage S5 — port-forward 로 `python3 deploy/images-lock.py compare-registry` 대조(없거나 다르면 FAIL).
set -u

HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)
usage() {
  echo "usage: $0 --registry <host:port> [--print|--run] [--only <이름>]... [--skip <이름>]... [--lock <파일>] [--dest-tls-verify <false|true>]" >&2
  exit 2
}

REG=""
MODE=print
LOCK="$HERE/images.lock"
# --print 머리에 보이는 lock 위치 — 설치기 S5 가 이 출력을 「인터넷 노드에서」 블록에 싣는다. 설치 호스트의 절대 경로는 인터넷 노드에 없다 —
# --lock 을 주지 않았으면 저장소 상대 경로로(같은 커밋의 저장소 루트에서 돌린다는 전제, 2026-11-03 ⑦ 리뷰 2).
LOCK_SHOW=deploy/images.lock
TLS=false
ONLY=""
SKIP=""
EXTRA=""                         # --print 머리의 --run 안내에 다시 싣는 선택 인자
_name_ok() {                     # --only/--skip 값: lock 의 DEST 문자(공백 없음 — 목록을 공백으로 가른다)
  case "$1" in
    ""|*[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:/-]*) return 1 ;;
  esac
}
while [ $# -gt 0 ]; do
  case "$1" in
    --registry) [ $# -ge 2 ] || usage; REG=$2; shift ;;
    --registry=*) REG=${1#*=} ;;
    --print) MODE=print ;;
    --run) MODE=run ;;
    --lock) [ $# -ge 2 ] || usage; LOCK=$2; LOCK_SHOW=$2; shift ;;
    --lock=*) LOCK=${1#*=}; LOCK_SHOW=$LOCK ;;
    --only|--skip)
      [ $# -ge 2 ] && _name_ok "$2" || usage
      if [ "$1" = --only ]; then ONLY="$ONLY $2"; else SKIP="$SKIP $2"; fi
      EXTRA="$EXTRA $1 $2"; shift ;;
    --dest-tls-verify) [ $# -ge 2 ] || usage; TLS=$2; shift ;;
    --dest-tls-verify=*) TLS=${1#*=} ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
  shift
done
case "$TLS" in
  true|false) ;;
  *) echo "FAIL: --dest-tls-verify 는 true|false (기본 false — 클러스터 내 레지스트리는 평문 HTTP)" >&2; exit 2 ;;
esac
[ "$TLS" = false ] || EXTRA="$EXTRA --dest-tls-verify true"
# 레지스트리: host[:port] 만(스킴·경로 없이 — 포탈 태그 조회·빌드 push 와 같은 계약, deploy/README §2e). IPv6 리터럴은 받지 않는다.
case "$REG" in
  "") echo "FAIL: --registry <host:port> 가 필요하다(in-cluster 면 <REGISTRY_CLUSTER_IP>:<REGISTRY_PORT>)" >&2; exit 2 ;;
  *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.:-]*|:*|*:|*:*:*|.*|-*)
    echo "FAIL: --registry '$REG' 는 host:port 모양이 아니다(스킴·경로·사용자 없이 — 예: 10.96.0.50:5000)" >&2; exit 2 ;;
esac
REG_HOST=${REG%:*}
case "$REG" in *:*) REG_PORT=${REG##*:} ;; *) REG_PORT=443 ;; esac
case "$REG_PORT" in *[!0123456789]*) echo "FAIL: --registry '$REG' 의 포트가 숫자가 아니다" >&2; exit 2 ;; esac
[ -f "$LOCK" ] && [ -r "$LOCK" ] || { echo "FAIL: lock 을 읽을 수 없다: $LOCK" >&2; exit 2; }

# --- lock ---------------------------------------------------------------------------------------------------------------
if command -v python3 >/dev/null 2>&1 && [ -f "$HERE/images-lock.py" ]; then
  if ! out=$(python3 "$HERE/images-lock.py" check --lock "$LOCK" 2>&1); then
    printf '%s\n' "$out" | grep -v '^  OK ' >&2
    echo "FAIL: lock 형식 검사(images-lock.py check) 실패 — 아무것도 push 하지 않는다" >&2
    exit 1
  fi
fi
ENTRIES=$(grep -v '^#' "$LOCK" || true)
SEL=""                           # 고른 줄("dest src dg" — 줄마다)
N=0
MATCHED=" "
bad=0
_hit() {                         # $1 = 이름 목록, $2 = dest → 맞은 이름을 MATCHED 에 더하고 0
  _r=1
  for _n in $1; do
    if [ "$_n" = "$2" ] || [ "$_n" = "${2%:*}" ]; then
      MATCHED="$MATCHED$_n "
      _r=0
    fi
  done
  return $_r
}
while read -r dest src dg extra; do
  [ -n "$dest" ] || continue
  why=""
  [ -z "$extra" ] && [ -n "$dg" ] || why="칸이 셋이 아니다(DEST SOURCE DIGEST)"
  case "$dest$src$dg" in
    *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:/@-]*) why="${why:-허용 밖 문자(CR·비 ASCII 등)}" ;;
  esac
  case "$src" in */*:*) ;; *) why="${why:-SOURCE 에 호스트·태그가 없다}" ;; esac
  case "${src%:*}$dest" in *@*) why="${why:-DEST·SOURCE 에 digest(@)가 있다}" ;; esac
  case "$dest" in *:*) ;; *) why="${why:-DEST 에 태그가 없다}" ;; esac
  hex=${dg#sha256:}
  if [ "$hex" = "$dg" ] || [ ${#hex} -ne 64 ]; then why="${why:-DIGEST 가 sha256:<64 hex> 가 아니다}"; fi
  case "$hex" in *[!0123456789abcdef]*) why="${why:-DIGEST 가 sha256:<64 소문자 hex> 가 아니다}" ;; esac
  if [ -n "$why" ]; then
    echo "FAIL: lock 줄 '$dest …' — $why" >&2
    bad=1
    continue
  fi
  keep=1                         # 두 목록을 늘 다 본다 — --only 로 빠진 줄에 맞는 --skip 이름도 「맞음」으로 센다
  if [ -n "${ONLY# }" ] && ! _hit "$ONLY" "$dest"; then keep=0; fi
  if [ -n "${SKIP# }" ] && _hit "$SKIP" "$dest"; then keep=0; fi
  [ "$keep" = 1 ] || continue
  SEL="$SEL$dest $src $dg
"
  N=$((N + 1))
done <<EOF
$ENTRIES
EOF
[ "$bad" = 0 ] || { echo "FAIL: lock 형식 — 아무것도 push 하지 않는다" >&2; exit 1; }
for _n in $ONLY $SKIP; do
  case "$MATCHED" in
    *" $_n "*) ;;
    *) echo "FAIL: '$_n' 가 lock 의 어느 줄에도 맞지 않는다(--only/--skip 은 DEST 또는 그 이름): $LOCK" >&2; exit 2 ;;
  esac
done
[ "$N" -gt 0 ] || { echo "FAIL: push 할 줄이 없다($LOCK${ONLY:+, --only$ONLY}${SKIP:+, --skip$SKIP})" >&2; exit 1; }

# --- 프록시: push 가 프록시를 타지 않는가 ------------------------------------------------------------------------------------
PROXY_SET=0
[ -z "${HTTPS_PROXY:-}${https_proxy:-}${HTTP_PROXY:-}${http_proxy:-}" ] || PROXY_SET=1
if [ -n "${NO_PROXY:-}" ]; then NP_LIST=$NO_PROXY; else NP_LIST=${no_proxy:-}; fi     # Go: NO_PROXY 가 비어 있지 않으면 그것만
_no_proxy_covers() {             # Go httpproxy 의 useProxy 를 거꾸로 — 0 이면 이 호스트:포트는 프록시를 타지 않는다
  NPC_HOST=$REG_HOST NPC_PORT=$REG_PORT NPC_LIST=$NP_LIST awk '
    function ip2n(s,   a, k, i, v) {
      k = split(s, a, ".")
      if (k != 4) return -1
      v = 0
      for (i = 1; i <= 4; i++) {
        if (a[i] !~ /^[0-9]+$/ || a[i] + 0 > 255) return -1
        v = v * 256 + a[i]
      }
      return v
    }
    BEGIN {
      host = tolower(ENVIRON["NPC_HOST"]); port = ENVIRON["NPC_PORT"]
      if (host == "localhost") exit 0
      hn = ip2n(host)
      if (hn >= 0 && int(hn / 16777216) == 127) exit 0
      n = split(ENVIRON["NPC_LIST"], ents, ",")
      for (i = 1; i <= n; i++) {
        e = tolower(ents[i]); gsub(/^[ \t]+/, "", e); gsub(/[ \t]+$/, "", e)
        if (e == "") continue
        if (e == "*") exit 0
        if (index(e, "/")) {
          split(e, c, "/"); nn = ip2n(c[1]); bits = c[2]
          if (nn >= 0 && hn >= 0 && bits ~ /^[0-9]+$/ && bits + 0 <= 32) {
            d = 2 ^ (32 - bits)
            if (int(hn / d) == int(nn / d)) exit 0
          }
          continue
        }
        eh = e; ep = ""
        if (split(e, hp, ":") == 2) { eh = hp[1]; ep = hp[2] }
        if (eh == "") continue
        if (ip2n(eh) >= 0) {
          if (hn >= 0 && ip2n(eh) == hn && (ep == "" || ep == port)) exit 0
          continue
        }
        if (substr(eh, 1, 2) == "*.") eh = substr(eh, 2)
        mh = 0
        if (substr(eh, 1, 1) != ".") { mh = 1; eh = "." eh }
        if ((length(host) >= length(eh) && substr(host, length(host) - length(eh) + 1) == eh) || (mh && host == substr(eh, 2)))
          if (ep == "" || ep == port) exit 0
      }
      exit 1
    }'
}
PROXY_BAD=0
if [ "$PROXY_SET" = 1 ] && ! _no_proxy_covers; then PROXY_BAD=1; fi
proxy_hint() {
  echo "  프록시 env(HTTPS_PROXY/HTTP_PROXY)가 있는데 NO_PROXY 가 레지스트리 $REG_HOST 를 덮지 않는다 — push 가 프록시로 가서 실패한다." >&2
  echo "  상류 pull 은 프록시를 그대로 쓰고 push 만 빼려면: export NO_PROXY=\"\${NO_PROXY:+\$NO_PROXY,}$REG_HOST\" (no_proxy 만 쓰는 셸이면 그것도)" >&2
}

# --- --print ---------------------------------------------------------------------------------------------------------------
if [ "$MODE" = print ]; then
  if [ "$PROXY_BAD" = 1 ]; then
    echo "WARN:" >&2
    proxy_hint
  fi
  echo "# DMS 이미지 반입(S5) — images.lock → $REG ($N줄, 설계 v6 7-5 ③). 인터넷 노드의 DMS 저장소(설치와 같은 커밋) 루트에서 실행한다. lock: $LOCK_SHOW"
  echo "#   같은 일을 멱등으로(이미 있으면 건너뜀·push 뒤 digest 확인): sh deploy/push-images.sh --registry $REG --run$EXTRA"
  echo "#   프록시를 거치는 노드면 push 가 프록시를 타지 않게: export NO_PROXY=\"\${NO_PROXY:+\$NO_PROXY,}$REG_HOST\""
  echo "#   끝나면 설치 호스트에서 install.sh --stage S5(레지스트리 digest 를 lock 과 대조 — 없거나 다르면 FAIL)."
  printf '%s' "$SEL" | while read -r dest src dg; do
    echo "skopeo copy --preserve-digests --dest-tls-verify=$TLS docker://${src%:*}@$dg docker://$REG/$dest"
  done
  exit 0
fi

# --- --run -----------------------------------------------------------------------------------------------------------------
command -v skopeo >/dev/null 2>&1 || { echo "FAIL: skopeo 가 없다 — 인터넷 노드에 설치(RHEL AppStream, 설계 7-5) 후 다시" >&2; exit 2; }
command -v sha256sum >/dev/null 2>&1 || { echo "FAIL: sha256sum 이 없다(coreutils)" >&2; exit 2; }
echo "== DMS 이미지 반입(S5): images.lock → $REG ($N줄) =="
if [ "$PROXY_BAD" = 1 ]; then
  proxy_hint
  echo "  FAIL    NO_PROXY — 아무것도 push 하지 않았다"
  echo "RESULT: FAIL"
  exit 1
fi
TMPD=$(mktemp -d) || { echo "FAIL: 임시 디렉터리를 만들지 못했다(mktemp)" >&2; exit 2; }
trap 'rm -rf "$TMPD"' EXIT
trap 'exit 130' INT TERM
say() { printf '  %-7s %s\n' "$1" "$2"; }
short() { printf '%s…' "$(printf '%s' "$1" | cut -c1-19)"; }
# 레지스트리의 태그 digest — 본문 바이트의 sha256(skopeo inspect --raw 는 받은 그대로 쓴다; $(…) 는 끝 개행을 지우므로 파일로 받는다).
# 없거나 닿지 않으면 빈 문자열.
reg_digest() {
  if skopeo inspect --raw --tls-verify="$TLS" "docker://$REG/$1" >"$TMPD/m" 2>"$TMPD/e" </dev/null; then
    printf 'sha256:%s' "$(sha256sum "$TMPD/m" | cut -d' ' -f1)"
  fi
}
fails=0
while read -r dest src dg; do
  [ -n "$dest" ] || continue
  have=$(reg_digest "$dest")
  if [ "$have" = "$dg" ]; then
    say SKIP "$dest — 이미 lock digest($(short "$dg"))"
    continue
  fi
  if [ -n "$have" ]; then
    say INFO "$dest — 지금 태그는 다른 digest($(short "$have")) — lock 으로 덮어쓴다"
  fi
  say PUSH "$dest ← ${src%:*}@$(short "$dg")"
  skopeo copy --preserve-digests --dest-tls-verify="$TLS" "docker://${src%:*}@$dg" "docker://$REG/$dest" </dev/null
  rc=$?
  if [ "$rc" != 0 ]; then
    say FAIL "$dest — skopeo copy 실패(exit $rc). 힌트: 상류는 프록시·사내 CA, 레지스트리는 인터넷 노드 → $REG 경로(ClusterIP — kube-proxy)·NO_PROXY·--dest-tls-verify=$TLS"
    fails=$((fails + 1))
    continue
  fi
  got=$(reg_digest "$dest")
  if [ "$got" = "$dg" ]; then
    say OK "$dest — push 뒤 레지스트리 digest == lock"
  else
    say FAIL "$dest — push 뒤 레지스트리 digest(${got:-읽지 못함})가 lock($dg)과 다르다 — 같은 태그에 다른 push 가 겹쳤는지 본다"
    fails=$((fails + 1))
  fi
done <<EOF
$SEL
EOF
if [ "$fails" = 0 ]; then
  echo "  다음: 설치 호스트에서 install.sh --stage S5(port-forward 로 lock 대조)"
  echo "RESULT: PASS"
  exit 0
fi
echo "  $fails줄 실패 — 고친 뒤 다시 돌린다(이미 올라간 줄은 건너뛴다)"
echo "RESULT: FAIL"
exit 1
