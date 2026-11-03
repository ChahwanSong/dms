#!/bin/sh
# 공용 디렉터리 S0 초기화(2026-11-03 ③, 설계 v6 3-1 · 8-1 「S0 세부」 3 · 9 「③ 공유 FS 세부」 · T3 「가드 음성 대조」).
#
#   sh deploy/shared-dir-init.sh [--check] <SHARED_DIR>        # 한 노드에서 root 로(공용 스토리지라 한 번이면 모든 노드에 보인다)
#
# DMS 상태(<SHARED_DIR>/dms)의 트리·소유·모드와 센티널을 만든다. 사이트가 모든 마스터·워커의 같은 경로에 미리 마운트한 공용
# 스토리지(종류 무관)가 전제다 -- DMS 는 마운트·프로비저닝하지 않고, 이 스크립트는 **SHARED_DIR 와 그 상위를 만들지 않는다**
# (mkdir -p·install -d 금지). 왜: 마운트가 빠진 노드에서 경로를 만들면 그 노드의 hostPath `type: Directory` 검사가 빈 로컬
# 디렉터리로 통과해 DMS 가 조용히 로컬 디스크에 쓴다(설계 #29·#31 -- 그래서 hostPath 는 늘 <SHARED_DIR>/dms 하위를 가리킨다).
#
# 가드(하나라도 어긋나면 아무것도 만들지 않고 멈춘다):
#   - SHARED_DIR 형식(render.sh·shared-storage-probe.py 와 같은 규칙): 절대 경로, '/' 아님, 정규화, 문자 ^/[A-Za-z0-9._/-]+$
#   - root(uid 0) -- 소유(26·65532)를 맞춘다
#   - [ -d SHARED_DIR ] 이고 `findmnt -n -o TARGET -T SHARED_DIR` 가 '/' 가 아님(포함 마운트가 루트 FS 면 「마운트 없음」)
#     SOURCE·FSTYPE·OPTIONS 는 보고만 한다(FS 종류로 판정하지 않는다 -- 동작 확인은 S7 프로브 몫).
#
# 트리(설계 3-1; 위에서 아래 순서로, 한 단계씩 mkdir):
#   dms                          root:root 0750   (이미 있으면 0750·0700 허용 -- 기존 규칙 「750/700, 711·755 금지」)
#   dms/artifacts                root:root 0755   아티팩트 base(이미 있으면 0755·0711 허용 -- 제어면 3홉 규칙)
#   dms/pgdata                   root:root 0700
#   dms/pgdata/.retired          root:root 0700   Released PV 재활용 보관(6-2)
#   dms/pgdata/dms-pgdata-01..05 26:26     0700   정적 hostPath PV(hostPath 는 fsGroup 을 적용하지 않아 미리 맞춘다)
#   dms/backup                   root:root 0750
#   dms/backup/pg                26:26     0700   pg_dump
#   dms/registry                 65532:65532 0750 레지스트리
# 센티널(root:root 0644, 한 줄):
#   dms/.dms-shared-id                       무작위 32 hex(공용 스토리지의 정체 -- S7 ① 가 모든 노드에서 같은지 본다)
#   dms/registry/.dms-registry-id            = .dms-shared-id(레지스트리 init 은 검사만 한다, 7-3)
#   dms/pgdata/dms-pgdata-0N/.dms-pv-id      = PV 이름(dms-pgdata-0N)
#   dms/backup/pg/.dms-backup-id             = dms-backup-pg(2026-11-03 ⑤ — pg_dump CronJob 이 덤프 전에 검사만 한다: 마운트 빠진 워커의
#                                              같은 이름 로컬 디렉터리에 덤프가 쌓이지 않게. 이미 S0 를 돌린 트리는 다시 돌리면 이것만 만든다)
#
# **이미 있으면 검사만 하고 고치지 않는다**(설계 S0 세부 3): 공용 경로라 통째 chmod·chown 이 전 노드에 번지는 함정이 있다
# (testbed roles/nfs_client). 어긋나면 FAIL 로 무엇이 다른지 알리고 운영자가 확인해 손으로 고친다. 심링크는 따라가지 않는다
# (트리 항목·센티널이 심링크면 FAIL -- 상태 디렉터리를 공용 스토리지 밖으로 돌릴 수 있다).
# --check: 아무것도 만들지 않고 검사만(없는 것도 FAIL). 설치기(묶음 ⑦)·운영자가 S0 결과를 다시 확인할 때.
#
# 종료: 0 PASS(만들었거나 이미 맞음) · 1 FAIL(가드 거부·모양 어긋남·--check 에서 없음) · 2 사용법·환경(인자·형식·root 아님)
#       · 4 모름(findmnt 가 없거나 답하지 않음 -- 마운트를 확인 못 했으니 만들지 않는다). 마지막 줄은 `RESULT: <PASS|FAIL|UNKNOWN>`.
# 다음 단계: python3 deploy/shared-storage-probe.py --context <ctx> --shared-dir <SHARED_DIR> --image <dms 이미지>(S7 -- 모든 노드).
set -u

usage() { echo "usage: $0 [--check] <SHARED_DIR>" >&2; exit 2; }
CHECK=0
SD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    -h|--help) usage ;;
    -*) usage ;;
    *) [ -z "$SD" ] || usage; SD=$1 ;;
  esac
  shift
done
[ -n "$SD" ] || usage

fails=0
say() { printf '  %-7s %s\n' "$1" "$2"; }
fail() { say FAIL "$1"; fails=$((fails + 1)); }

# 형식(render.sh 의 _shared_dir_problem 과 같은 규칙 -- 범위식 대신 글자를 나열한다: 로케일 따라 범위가 다른 글자를 품는다).
_shared_dir_problem() {
  case "$1" in
    /*) ;;
    *) echo "절대 경로가 아님"; return ;;
  esac
  case "$1" in
    *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-]*)
      echo "허용 밖 문자(영문·숫자·. _ / - 만)"; return ;;
  esac
  case "$1" in
    */) echo "'/' 자체이거나 끝에 / 가 있음"; return ;;
    *//*|*/./*|*/../*|*/.|*/..) echo "정규화되지 않은 경로(// · /./ · /../)"; return ;;
  esac
}
why=$(_shared_dir_problem "$SD")
[ -z "$why" ] || { echo "FAIL: SHARED_DIR='$SD' — $why (예: /srv/dms_shared)" >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo "FAIL: root 로 실행해야 한다(소유 26·65532 를 맞춘다)" >&2; exit 2; }

echo "== 공용 디렉터리 S0 초기화: $SD ($([ "$CHECK" = 1 ] && echo '검사만' || echo '없으면 만듦')) =="

# --- 가드: 마운트 확인(만들기 전에) ------------------------------------------------------------------------------------
if [ ! -d "$SD" ]; then
  fail "SHARED_DIR $SD 가 없다 — 사이트가 만들고 공용 스토리지를 마운트한다(이 스크립트는 SHARED_DIR·상위를 만들지 않는다)"
  echo "RESULT: FAIL"; exit 1
fi
command -v findmnt >/dev/null 2>&1 || { say UNKNOWN "findmnt 가 없다(util-linux) — 마운트를 확인 못 해 만들지 않는다"; echo "RESULT: UNKNOWN"; exit 4; }
target=$(findmnt -n -o TARGET -T "$SD" 2>/dev/null) || target=""
if [ -z "$target" ]; then
  say UNKNOWN "findmnt -T $SD 가 답하지 않았다 — 마운트를 확인 못 해 만들지 않는다"
  echo "RESULT: UNKNOWN"; exit 4
fi
if [ "$target" = / ]; then
  fail "마운트 없음 — $SD 를 담은 마운트가 '/' 다(공용 스토리지가 이 노드에 마운트되지 않았다; /etc/fstab 확인). 아무것도 만들지 않았다"
  echo "RESULT: FAIL"; exit 1
fi
say INFO "포함 마운트: $(findmnt -n -o TARGET,SOURCE,FSTYPE,OPTIONS -T "$SD" 2>/dev/null | head -n 1) (보고만)"

# --- 트리 ------------------------------------------------------------------------------------------------------------
# 항목: <상대 경로> <uid> <gid> <허용 모드(콤마; 첫째 = 만들 때 모드)>. 부모가 앞에 온다.
TREE="dms 0 0 750,700
dms/artifacts 0 0 755,711
dms/pgdata 0 0 700
dms/pgdata/.retired 0 0 700
dms/pgdata/dms-pgdata-01 26 26 700
dms/pgdata/dms-pgdata-02 26 26 700
dms/pgdata/dms-pgdata-03 26 26 700
dms/pgdata/dms-pgdata-04 26 26 700
dms/pgdata/dms-pgdata-05 26 26 700
dms/backup 0 0 750
dms/backup/pg 26 26 700
dms/registry 65532 65532 750"
BAD=" "        # 어긋난(또는 못 만든) 항목 -- 그 아래는 손대지 않는다

_under_bad() {   # $1 = 상대 경로 -- 조상 중 BAD 가 있으면 0
  _p=$1
  while :; do
    case "$_p" in */*) _p=${_p%/*} ;; *) return 1 ;; esac
    case "$BAD" in *" $_p "*) return 0 ;; esac
  done
}

while read -r rel uid gid modes; do
  [ -n "$rel" ] || continue
  p="$SD/$rel"
  if _under_bad "$rel"; then
    BAD="$BAD$rel "; continue
  fi
  want=${modes%%,*}
  if [ -L "$p" ]; then
    fail "$rel 이 심링크다 — 따라가지 않고 고치지 않는다(상태 디렉터리를 공용 스토리지 밖으로 돌릴 수 있다)"
    BAD="$BAD$rel "
  elif [ -e "$p" ]; then
    if [ ! -d "$p" ]; then
      fail "$rel 이 디렉터리가 아니다 — 고치지 않는다"; BAD="$BAD$rel "; continue
    fi
    got=$(stat -c '%u %g %a' "$p" 2>/dev/null) || got="? ? ?"
    g_uid=${got%% *}; _rest=${got#* }; g_gid=${_rest%% *}; g_mode=${_rest#* }
    ok=0
    if [ "$g_uid" = "$uid" ] && [ "$g_gid" = "$gid" ]; then
      case ",$modes," in *",$g_mode,"*) ok=1 ;; esac
    fi
    if [ "$ok" = 1 ]; then
      say OK "$rel 이미 있음($g_uid:$g_gid 0$g_mode)"
    else
      fail "$rel 모양이 다르다: 기대 $uid:$gid 0$(echo "$modes" | sed 's/,/·0/g') / 실제 $g_uid:$g_gid 0$g_mode — 고치지 않는다(공용 경로라 chmod·chown 이 전 노드에 번진다; 확인 뒤 손으로)"
      BAD="$BAD$rel "
    fi
  elif [ "$CHECK" = 1 ]; then
    fail "$rel 이 없다(--check — 만들지 않음)"; BAD="$BAD$rel "
  else
    # 한 단계씩(부모는 앞 항목이 만들었거나 있었다). mkdir -p 를 쓰지 않는다 -- 빠진 부모를 만들지 않게. chmod 의 앞 0 두 개는
    # 부모에서 물려받은 setgid 를 지운다(GNU chmod 는 4자리 숫자 모드로는 디렉터리의 setuid·setgid 를 남긴다 -- 그러면 다시 돌릴 때
    # 「모양이 다르다」가 된다).
    if mkdir -m "0$want" "$p" && chown "$uid:$gid" "$p" && chmod "00$want" "$p"; then
      say MADE "$rel ($uid:$gid 0$want)"
    else
      fail "$rel 을 만들지 못했다(위 오류 — root 특권을 주지 않는 export(root_squash)면 chown 이 거부된다; S7 ③ 참고)"
      BAD="$BAD$rel "
    fi
  fi
done <<EOF
$TREE
EOF

# --- 센티널 ----------------------------------------------------------------------------------------------------------
# $1 = 상대 경로, $2 = 기대 값(빈 값 = 무엇이든 형식만 맞으면). 있으면 값만 대조(고치지 않음), 없으면 만든다(--check 면 FAIL).
# 만들기는 noclobber(set -C)로 -- 동시에 두 노드에서 돌려도 하나만 쓰고 나머지는 대조로 넘어간다.
_sentinel() {
  _rel=$1; _want=$2; _f="$SD/$_rel"
  if _under_bad "$_rel"; then return 1; fi
  if [ -L "$_f" ]; then fail "$_rel 이 심링크다 — 따라가지 않는다"; return 1; fi
  if [ ! -e "$_f" ]; then
    if [ "$CHECK" = 1 ]; then fail "$_rel 이 없다(--check — 만들지 않음)"; return 1; fi
    if (umask 022; set -C; printf '%s\n' "$_want" > "$_f") 2>/dev/null && chown 0:0 "$_f" && chmod 0644 "$_f"; then
      say MADE "$_rel = $_want"; return 0
    fi
    [ -e "$_f" ] && [ ! -L "$_f" ] || { fail "$_rel 을 쓰지 못했다"; return 1; }
  fi
  if [ ! -f "$_f" ]; then fail "$_rel 이 일반 파일이 아니다"; return 1; fi
  _got=$(head -n 1 "$_f" 2>/dev/null) || _got=""
  if [ "$_got" = "$_want" ]; then say OK "$_rel 이미 있음(= $_got)"; return 0; fi
  fail "$_rel 값이 다르다: 기대 '$_want' / 실제 '$_got' — 고치지 않는다(다른 스토리지·이전 설치의 흔적인지 확인)"
  return 1
}

ID_FILE="$SD/dms/.dms-shared-id"
shared_id=""
if _under_bad dms/.dms-shared-id; then
  :
elif [ -L "$ID_FILE" ]; then
  fail "dms/.dms-shared-id 가 심링크다 — 따라가지 않는다"
elif [ -e "$ID_FILE" ]; then
  if [ -f "$ID_FILE" ]; then
    shared_id=$(head -n 1 "$ID_FILE" 2>/dev/null) || shared_id=""
    case "$shared_id" in
      ""|*[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]*)
        fail "dms/.dms-shared-id 값 '$shared_id' 이 비었거나 형식 밖이다(영문·숫자·. _ - 만) — 고치지 않는다"; shared_id="" ;;
      *) say OK "dms/.dms-shared-id 이미 있음(= $shared_id)" ;;
    esac
  else
    fail "dms/.dms-shared-id 가 일반 파일이 아니다"
  fi
elif [ -e "$SD/dms/registry/.dms-registry-id" ] || [ -L "$SD/dms/registry/.dms-registry-id" ]; then
  # 레지스트리 센티널만 있는 어긋난 상태 -- 그 값을 정체로 추측하지 않는다(다른 스토리지·이전 설치의 흔적일 수 있다).
  fail "dms/.dms-shared-id 는 없는데 dms/registry/.dms-registry-id 는 있다 — 추측하지 않는다(확인 뒤 손으로)"
elif [ "$CHECK" = 1 ]; then
  fail "dms/.dms-shared-id 가 없다(--check — 만들지 않음)"
else
  new_id=$(od -An -tx1 -N16 /dev/urandom 2>/dev/null | tr -d ' \n')
  case "$new_id" in
    ????????????????????????????????) ;;
    *) fail "무작위 값을 만들지 못했다(/dev/urandom)"; new_id="" ;;
  esac
  if [ -n "$new_id" ] && _sentinel dms/.dms-shared-id "$new_id"; then
    shared_id=$(head -n 1 "$ID_FILE" 2>/dev/null) || shared_id=""
  fi
fi
if [ -n "$shared_id" ]; then
  _sentinel dms/registry/.dms-registry-id "$shared_id"
fi
for n in 01 02 03 04 05; do
  _sentinel "dms/pgdata/dms-pgdata-$n/.dms-pv-id" "dms-pgdata-$n"
done
_sentinel dms/backup/pg/.dms-backup-id dms-backup-pg

if [ "$fails" -gt 0 ]; then
  echo "RESULT: FAIL"; exit 1
fi
echo "다음: python3 deploy/shared-storage-probe.py --context <ctx> --shared-dir $SD --image <dms 이미지> (S7 — 모든 노드에서 같은 스토리지인지)"
echo "RESULT: PASS"
