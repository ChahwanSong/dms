"""잡 도구 출력에서 개수·바이트·상세(summary v2)를 뽑는 순수 파서(슬라이스 15 설계 §3, 2026-10-27 d192 §1).

계약: 어떤 함수도 예외를 던지지 않는다(설계 §4 fail-soft: 파싱이 잡을 죽이는 경로는 없다) -- 개수는 모르면 None,
상세(parse_tool_detail)는 찾은 키만 싣는다. parse_scan_counts만 파일을 읽는다(읽기 전용) -- 나머지는 문자열 입력만 받는
순수 함수다.

핵심 개수 (files, bytes)의 뜻은 도구·단계마다 다르다(d192). 예전 docstring 의 「nsync = Planned 합계 -- 같은 트리에서
dsync Items와 일치」는 틀렸다(포탈 괴리 [20]): nsync 합은 계획된 **변경** 수라 타입 교체는 두 번(remove+copy) 세고 삭제·
속성 갱신이 들어가고 특수 파일은 빠진다. dsync 미리보기는 소스 **전체** walk 수, 실행은 만든 항목 수다 -- 서로 비교할 수 없다.

  nsync 미리보기 = Planned 합(skipped-dst-only 제외) = 변경 예정 / planned-volume
  nsync 실행     = Completed 합 = 실제로 반영된 것(실패·차단·미시도는 Planned 에만 있다) / Completed copied-bytes
                   (Completed 줄이 없으면 files 는 모름, bytes 는 마지막 copied-volume 으로 물러난다)
  dsync 미리보기 = 소스 walk 항목 수(첫 walk 줄) / null
  dsync 실행     = 최종 블록 Items − mknod 실패가 아닌 「Failed to copy」 줄 수 / 최종 블록 Data (N bytes)
                   (최종 블록 = 바로 앞 줄이 패딩 없는 "Seconds: X" 인 Items -- 없으면 둘 다 모름. 중단(aborted)이면 둘 다 모름)
  drm 미리보기   = walk 항목 수(= 지울 대상) / null
  drm 실행       = Removed N items(포크 7a3189c 부터 실제로 지운 수 -- 시도 수가 아니다) / null
  dscan          = 리포트 summary.total_entries / 히스토그램 bytes 합

summary v2 상세 키(parse_tool_detail -- 찾은 것만, 없음 = 모름 또는 해당 없음):

  공통          failure_samples([{op, path, errno}] ≤10 -- path 만 도구 문자열, 256자), aborted(stderr 의 MPI 중단 흔적만)
  nsync         planned·done·copied_bytes(실행)·planned_bytes(미리보기)·type_changes·type_change_needs_delete·failures·
                type_change_refused(= failures.by_op["replace-dir"])·skipped_special·not_attempted·stopped_early·stop_cause·
                scan_errors(양쪽 역할 합)·source_scan_errors(그중 소스 쪽 -- 「Source scan had N error(s): --delete is off」,
                --delete 가 실제로 꺼졌을 때만 찍힌다)·delete_disabled·metadata_ignored·metadata_failed·noatime_fallbacks
  dsync 미리보기 source_items·skipped_special·scan_incomplete·delete_disabled·tool_errors
  dsync 실행    copied_items·copy_failed(「Failed to copy」 줄 수 그대로)·create_failed(mkdir()·symlink()·mknod() 실패 줄 수)·
                copied_bytes·removed·remove_failed·type_change_refused·skipped_special·scan_incomplete(소스 walk 오류가 있었다 --
                개수는 찍지 않는다, 참일 때만)·delete_disabled·tool_errors
  drm           target_items(미리보기)·removed·remove_failed(실행)·tool_errors
  내부(_ 머리)  사유 판정 근거 -- 러너가 summary 에 싣지 않는다(_type_change_dir_refused·_remove_errnos, dsync 실행)

줄 머리 고정(d192): 요약 줄 정규식은 전부 MFU 로그 접두사 "[YYYY-MM-DDTHH:MM:SS] "(오류 줄은 그 뒤 "[rank] [file:line]
ERROR: ")에 붙여 MULTILINE 으로 줄 머리부터 맞춘다 -- 앵커가 없으면 경로 텍스트 한가운데의 같은 문구가 요약 행세를 했다.
요약 블록은 항목별 줄보다 뒤에 찍히므로 마지막 매치를 쓴다. nsync 는 경로의 제어 문자·\\·:·= 를 \\xHH 로 찍어 이름으로 줄을
위조할 수 없지만 dsync·drm 은 경로를 날것으로 찍는다(이름에 줄바꿈이 들어갈 수 있다 -- validate_relative_path 도 파일시스템도
막지 않는다). 그 위조는 줄마다의 규칙이 막는다: walk 횟수 상한(아래), drm Removed 는 마지막 매치(drm 은 요약 뒤에 경로를
찍지 않는다), dsync Removed 는 한 번만(넘치면 모름), Remove failed 는 마지막 Removed **뒤**의 첫 줄만. 남는 위험: dsync·drm
의 항목 실패 줄(failure_samples·errno)은 이름에 심은 가짜 줄로 **더해질** 수 있다(진짜 줄을 지우지는 못한다) -- 러너의 사유
규칙이 이를 어디까지 믿는지는 runner._derive_reason 이 적는다."""
import json
import re

# MFU_LOG 접두사(mfu_util.h): 모든 줄 = "[ts] ", ERROR 줄 = "[ts] [rank] [file:line] ERROR: ". WARN·INFO 는 수준 단어가 없다.
_TS = r"^\[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\] "
_ERR = _TS + r"\[\d+\] \[[^\]\n]*\] ERROR: "
# 승격 컬럼(files_count·bytes_count)은 BIGINT 다 -- 그 위의 수는 실제 개수가 아니다(병적 입력·위조), 모름으로 접는다.
_INT64_MAX = 2 ** 63 - 1

# 최종 요약의 "Items: N"만(d192 리뷰 contract-31·portal-jobs-12: 중단된 dsync 가 복사 계획을 「복사한 항목」으로 보였다). dsync 는
# "Items: N" 을 두 번 찍는다: 복사 **계획**(mfu_flist_print_summary -- "Copying to <dst>" 뒤, 다음 줄이 "Data: X (Y per file)")과
# 최종 블록(mfu_flist_copy.c -- "Started:"·"Completed:"·"Seconds: X"·"Items: N"·"  Directories/Files/Links"·"Data: X (N bytes)").
# 예전엔 「마지막 매치 = 최종 블록」이라 믿었는데, 복사 중에 중단·강제 종료되면 최종 블록이 없어 계획 줄이 마지막이 됐다(재현:
# direct+bufsize MPI_Abort 출력 -- 계획 수가 files·「반영한 항목」·통계 합계로 들어갔다). 그래서 바로 앞 줄이 패딩 없는 "Seconds: X" 인
# Items 만 받는다: walk 단계 비교 블록은 "Seconds   :"·"Items     :"(콜론 앞 패딩)라 배제되고, 계획 줄 앞은 "Copying to" 다.
# --batch-files 의 배치별 진행 블록("Current:"·"Seconds:"·"Items:"·"Data: (N bytes)" -- 누적값)도 같은 모양이고, 최종 블록이
# 그 뒤라 마지막 매치가 최종이다. [ \t]* 인 이유: \s* 는 줄바꿈을 삼켜 다음 줄 끝의 $ 에 붙는다.
_SYNC_FINAL_ITEMS = re.compile(_TS + r"Seconds: \d+(?:\.\d+)?[ \t]*\n" + _TS + r"Items: (\d+)[ \t]*$", re.MULTILINE)
# 같은 블록의 "Data: X (N bytes)" -- Items 바로 뒤(하위 줄 "  Directories/Files/Links: N" 최대 3개를 건너) 에서만 본다.
# "Copy data: X (N bytes)" 는 복사 **시도** 합이라(실측: Copy data 6 bytes, 최종 Data 3 bytes -- 열지 못한 파일 몫) 바이트 출처가
# 아니다. "(N bytes in S seconds)"(Rate)는 괄호가 바로 닫히지 않아 배제, 줄 끝 앵커라 경로에 박힌 "(999 bytes)" 도 행세 못 한다.
_SYNC_FINAL_DATA = re.compile(r"\n(?:" + _TS + r"  [A-Za-z]+: \d+[ \t]*\n){0,3}" + _TS
                              + r"Data: [^\n]*\((\d+) bytes\)[ \t]*$", re.MULTILINE)
# dsync --dryrun(미리보기)은 복사 요약("Items: N"·"(N bytes)")을 찍지 않는다 -- 소스·
# 목적지 **walk** 요약만 남긴다(실측 2026-09-17: 소스 "Walked 2 items in 0.004 seconds
# (...)" 뒤에 목적지 "Walked 0 items ..."). 그래서 미리보기 files/bytes 가 항상 null
# 이었고 지문(summary 해시)이 사실상 상수였다. 최종 walk 줄("seconds", 진행 줄의
# "secs ..."는 배제)의 **첫** 매치 = 소스 항목 수. 바이트는 walk 가 찍지 않아 null.
# 줄 머리를 MFU INFO 로그 접두사에 고정한다(2026-10-12 리뷰): 앵커가 없으면 walk
# 요약보다 **먼저** 찍히는 "[ts] Walking <대상 경로>" 줄이나 walk 오류 줄("[ts] [rank] [file:line] ERROR: ... '<경로>'")의
# 경로 텍스트에 이 문구가 들어 있을 때 그 숫자가 첫 매치가 됐다(경로는 공백·괄호를 허용한다). 항목 목록
# (mfu_flist_print)은 walk 줄 **뒤**에 오므로 첫 매치를 앞지르지 못한다.
_SYNC_WALKED = re.compile(_TS + r"Walked (\d+) items in [\d.]+ seconds \(", re.MULTILINE)
# 그래도 경로 이름에는 줄바꿈이 들어갈 수 있다 -- 대상 트리 안의 이름에
# "\n[ts] Walked 1 items in 0.0 seconds (" 를 넣으면 「Walking」·walk 오류 줄(진짜 요약 **앞**)이나 drm 목록(진짜 요약 **뒤**)에
# 줄 머리부터 맞는 가짜 줄이 생긴다(2026-10-12 보안 검토 -- 컨펌 창에 「삭제 대상 1개」, 실제 삭제 25만). 가짜 줄은 **더할** 수만
# 있고 진짜 줄을 지울 수는 없으므로 「walk 횟수보다 많이 보이면 모름」으로 막는다: drm 은 경로 하나를 한 번(정확히 1),
# dsync 는 소스·목적지 두 번(DMS 는 link-dest 를 싣지 않는다 -- 2 이하). 넘치면 null(모름, 지어내지 않는다).
_DRM_WALKS = 1
_DSYNC_MAX_WALKS = 2
# 삭제 요약(mfu_flist_unlink 끝, 포크 7a3189c): "Removed N items in S seconds (" -- N 은 실제로 지운 수다(예전 stock 은 시도 수).
# 진행 줄 "Removed N items (x%) in ..." 은 "items" 뒤가 " (" 라 배제된다. drm 은 이 줄 뒤에 경로를 찍지 않는다(오류 줄은 전부
# 그 앞) -- 마지막 매치가 진짜다. dsync 는 unlink 를 한 번만 부르는데(--delete·타입 교체 제거를 한 목록으로) 그 **뒤**에 복사
# 단계 오류 줄(날 경로)이 오므로 마지막 매치를 믿을 수 없다 -- 한 줄보다 많이 보이면 모름(_DSYNC_MAX_REMOVED).
_RM_ITEMS = re.compile(_TS + r"Removed (\d+) items in ", re.MULTILINE)
_DSYNC_MAX_REMOVED = 1
# 남은 항목이 있으면 바로 다음에 rank 0 이 찍는다(ERROR 수준 -- 비 0 종료). 마지막 Removed **뒤**의 첫 줄만 받는다: 그 앞의
# 같은 모양은 unlink 오류 줄 경로에 심은 위조다.
_RM_FAILED = re.compile(_ERR + r"Remove failed for (\d+) of (\d+) items\s*$", re.MULTILINE)
# drm --dryrun(미리보기)도 "Removed N items" 를 찍지 않는다(2026-10-12 실측: walk 요약 뒤에 지울 항목 목록
# "Type=DIR File=..." 만) -- drm.c 가 dryrun 이면 mfu_flist_unlink 대신 mfu_flist_print 만 부르고, 그 목록은 앞 10개·뒤
# 10개만 찍고 중간을 "<snip>" 으로 자른다(mfu_flist.c, --stat 이면 ls 형식) -- 줄 수로는 셀 수 없다. 그래서 rm 미리보기
# files 가 늘 null(컨펌 창 「삭제 대상 모름」)이었다. walk 요약이 유일한 개수 출처이고, dsync 미리보기와 같은 정규식이다.

# nsync는 stock mpifileutils가 아니라 역할 기반(src/dst rank) 별도 도구라 Items:/
# (N bytes)를 전혀 찍지 않는다 -- 실측 출력과 결정 근거는 설계 부록 A.
# "Planned actions:" 는 실행·미리보기 양쪽에 같은 형식으로 나오는 **계획**(마지막 배치까지 -- 중간에 멈추면 거기까지)이고,
# 포크 7a3189c 부터 실행에만 바로 뒤에 "Completed actions: ... copied-bytes=N"(실제로 반영된 것)이 붙는다. 진행 줄의
# actions= 는 배치 병합으로 계획과 어긋나 쓰지 않는다. 줄 전체(끝까지)를 잡아 쌍 파싱에 넘긴다.
_NSYNC_PLANNED_LINE = re.compile(_TS + r"Planned actions:(.*)$", re.MULTILINE)
_NSYNC_COMPLETED_LINE = re.compile(_TS + r"Completed actions:(.*)$", re.MULTILINE)
# "이름=정수" 쌍 -- 액션 이름에는 하이픈이 들어간다(symlink-update, skipped-dst-only).
_NSYNC_ACTION_PAIR = re.compile(r"([A-Za-z][\w-]*)=(\d+)")
# 건드리지 않은 항목이라 처리량이 아니다(설계 부록 A). 화이트리스트가 아니라 이
# 블랙리스트로 합산하므로 nsync가 새 액션 종류를 추가해도 파서 수정 없이 따라간다.
_NSYNC_SKIP_KEYS = frozenset({"skipped-dst-only"})
# Completed 줄의 바이트는 액션이 아니다 -- 합에서 빼고 copied_bytes 로 따로 싣는다.
_NSYNC_COPIED_BYTES_KEY = "copied-bytes"
# 액션 종류 상한(지금 6개) -- 쌍 파싱이 블랙리스트라도 summary 키 수는 묶는다.
_NSYNC_MAX_KINDS = 16
# 바이트의 사람용 표기(진행 줄): 미리보기 planned-volume=, 실행 copied-volume=(Completed 가 없을 때의 물러남).
# 값 형식은 "<실수> <단위>"(예: 50.000 B, 1.234 GiB) -- 원시 정수 바이트는 Completed copied-bytes 에만 있다.
_NSYNC_PLANNED_VOLUME = re.compile(_TS + r"Progress [^\n]*?planned-volume=(\d+(?:\.\d+)?)\s*([A-Za-z]+)", re.MULTILINE)
_NSYNC_COPIED_VOLUME = re.compile(_TS + r"Progress [^\n]*?copied-volume=(\d+(?:\.\d+)?)\s*([A-Za-z]+)", re.MULTILINE)
_NSYNC_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2,
                "GiB": 1024 ** 3, "TiB": 1024 ** 4}

# ---- summary v2 상세(parse_tool_detail) -- 줄 모양은 포크 7a3189c nsync.c·dsync.c·mfu_flist_remove.c·mfu_flist_copy.c ----
_NSYNC_TYPE_CHANGES = re.compile(_TS + r"Type changes: to-directory (\d+), from-directory (\d+)\s*$", re.MULTILINE)
_NSYNC_TYPE_NEEDS_DELETE = re.compile(
    _TS + r"A directory that is replaced by a non-directory must be empty unless --delete is given", re.MULTILINE)
# nsync 는 꼬리가 더 길다(", the destination entry at that name is left as it is") -- 앞부분만 맞춘다(dsync 와 공용).
_SKIPPED_SPECIAL = re.compile(_TS + r"Skipped (\d+) special file\(s\) in the source", re.MULTILINE)
# "ERROR: Failed items: X (copy a, replace-dir b); listed below: Y" -- 종류 이름은 nsync_fail_op_names(소문자·하이픈).
_NSYNC_FAILED_ITEMS = re.compile(_ERR + r"Failed items: (\d+) \(([^)\n]*)\); listed below: (\d+)", re.MULTILINE)
_NSYNC_OP_COUNT = re.compile(r"([a-z][a-z-]{0,31}) (\d+)")
# 목록 줄(경로순 정렬, 요약 블록 직전)과 즉시 줄(랭크당 처음 16건). 경로는 nsync 가 이스케이프해 「`: 」를 품지 못한다.
_NSYNC_FAILED_LISTED = re.compile(_TS + r"  failed ([a-z][a-z-]{0,31}) `(.*)`: [^`\n]*\(errno (-?\d+)\)\s*$",
                                  re.MULTILINE)
_NSYNC_FAILED_NOW = re.compile(_TS + r"Failed ([a-z][a-z-]{0,31}) `(.*)`: [^`\n]*\(errno (-?\d+)\)\s*$", re.MULTILINE)
_NSYNC_NOT_ATTEMPTED = re.compile(_TS + r"Not attempted after a stop: (\d+) action\(s\)", re.MULTILINE)
_NSYNC_STOPPED_EARLY_RC = re.compile(_ERR + r"Execution stopped early: (\d+) action\(s\) not attempted", re.MULTILINE)
_NSYNC_STOPPED_COVER = re.compile(
    _TS + r"Planned and diff counts above cover batches 1-\d+ of \d+ only \(stopped early\)", re.MULTILINE)
_NSYNC_STOPPING = re.compile(_TS + r"Stopping after batch \d+/\d+([^\n]*)$", re.MULTILINE)
# 스캔 오류는 두 역할(src·dst rank) 합이다(nsync_scan_role_path_filtered 가 양쪽에서 NSYNC_FAIL_SCAN). --delete 를 끄고 아래
# 줄을 찍는 것은 **소스** 역할 오류뿐이다(nsync.c 9335-9341 스풀 모드·9428-9436 단일 배치 -- 둘 다 실행 전) -- 그래서 N 을
# source_scan_errors 로 따로 싣는다(d192 리뷰: 목적지 쪽 읽기 오류가 「소스를 못 읽어 삭제하지 않았다」로 보였다, 삭제는 돌았다).
_NSYNC_SCAN_ERRORS = re.compile(_ERR + r"Encountered (\d+) scan error\(s\)", re.MULTILINE)
_NSYNC_DELETE_OFF = re.compile(_TS + r"Source scan had (\d+) error\(s\): --delete is off", re.MULTILINE)
_NSYNC_META_IGNORED = re.compile(_TS + r"Metadata best-effort applied with (\d+) ignored", re.MULTILINE)
_NSYNC_META_FAILED = re.compile(_ERR + r"Metadata apply had (\d+) hard failure\(s\)", re.MULTILINE)
_NSYNC_NOATIME = re.compile(_TS + r"--open-noatime: (\d+) source file\(s\) not owned", re.MULTILINE)
# 멈춘 원인(stop_cause): 「Stopping after batch ...: ... reached --max-errors N」 이 있으면 그것, 아니면 실패 errno 로 가른다.
_ERRNO_FULL = frozenset({28, 122})        # ENOSPC, EDQUOT
_ERRNO_READ_ONLY = 30                     # EROFS

_DSYNC_COPY_FAILED = re.compile(_ERR + r"Failed to copy `", re.MULTILINE)
# 같은 줄의 나머지("src' to `dst'") -- mknod 실패 경로와 맞춰 보려고(_dsync_copied_items).
_DSYNC_COPY_FAILED_REST = re.compile(_ERR + r"Failed to copy `([^\n]*)$", re.MULTILINE)
# 만들기 실패(mfu_flist_copy.c 1029·1202·1273): "Create `p' mkdir() failed (errno=…", "Create `p' symlink() failed, (errno=…",
# "File `p' mknod() failed (errno=…". dsync 출력에서 "Create `"·"File `" 로 시작하는 ERROR 줄은 이 셋뿐이라 머리만 센다 -- 이름의
# 줄바꿈이 줄을 쪼개도 머리 조각은 남는다(꼬리까지 맞추면 그 항목이 세지지 않는다). 최종 Items 는 이 셋을 이미 뺀 수다(total_dirs·
# total_links·total_files 증가가 실패 return 뒤에 있다) -- copied_items 에서 다시 빼지 않는다.
_DSYNC_CREATE_FAILED = re.compile(_ERR + r"(?:Create|File) `", re.MULTILINE)
_DSYNC_MKNOD_FAILED = re.compile(_ERR + r"File `(.*)' mknod\(\) failed \(errno=", re.MULTILINE)
_DSYNC_TYPE_REFUSED = re.compile(_ERR + r"(\d+) destination item\(s\) changed type but could not be removed",
                                 re.MULTILINE)
# 막힌 타입 교체 중 「비어 있지 않은 디렉토리 + --delete 꺼짐」 줄(dsync.c 1724 -- 이 꼬리는 남은 것이 디렉토리이고 delete 가 꺼졌을
# 때만 붙는다). 경로는 dsync_escape_path 를 거쳐 제어 문자·\ 가 \xHH 라 이 줄 자체는 이름으로 위조되지 않는다. 줄 끝까지 고정한다:
# 꼬리 문구를 이름에 넣은 비 디렉토리 생존자 줄("…`/d/x' could not be removed (still a directory), … --delete; y' could not be
# removed (still a non-directory); source item …")은 끝이 달라 맞지 않는다.
_DSYNC_TYPE_REFUSED_DIR = re.compile(
    _ERR + r"Type change not applied: `(.*)' could not be removed \(still a directory\), a non-empty directory is replaced "
    r"only with --delete; source item not copied or linked[ \t]*$", re.MULTILINE)
# dsync 의 walk 오류 요약. 이 줄은 --delete 가 켜져 있을 때만 찍힌다(dsync.c 3880-3887 -- delete 를 끄면서) -- delete 없는 실행의
# walk 오류는 요약 줄이 없다. walk 오류는 종료 코드를 바꾸지 않는다(walk_rc 는 delete 만 끈다).
_DSYNC_WALK_ERRORS = re.compile(_ERR + r"Errors detected walking source path(, delete option disabled)?", re.MULTILINE)
_ERR_LINE = re.compile(_ERR, re.MULTILINE)
# 제거 단계(mfu_flist_remove.c remove_type) 실패 연산 -- 복사 단계의 unlink 는 #if 0 이라 dsync 출력의 이 셋은 전부 제거 단계다.
_REMOVE_OPS = frozenset({"unlink", "rmdir", "remove"})
# dsync·drm 의 항목 실패 줄: 「`경로'」 하나와 「(errno=N 이유)」 꼬리를 가진 ERROR 줄(mfu_flist_copy.c·mfu_flist_remove.c --
# "Failed to unlink `p' (errno=13 ...)", "Write error when copying from `a' to `b' (errno=28 ...)", "Create `p' mkdir()
# failed (errno=..."). 머리 = 첫 백틱 앞, 경로 = 마지막 「'」까지(날 경로는 「'」를 품을 수 있다), 꼬리에는 백틱이 없다.
# walk 오류("Failed to stat: '<경로>'")는 작은따옴표라 빠진다 -- 목적지가 아직 없는 정상 상황도 거기 있다.
_MFU_ERRNO_LINE = re.compile(_ERR + r"([^`\n]*)`(.*)'([^`\n]*?)\(errno=(\d+)[ )][^\n]*$", re.MULTILINE)
# 머리 → 연산 이름(포탈 opLabel 이 한국어로 바꾼다; 모르는 이름은 원문). 순서 = 우선순위(긴 접두 먼저).
_MFU_FAILED_TO_OPS = (
    ("open input", "open"), ("open output", "open"), ("open directory", "opendir"),
    ("change ownership", "chown"), ("change permissions", "chmod"), ("change timestamps", "utime"),
    ("changeback timestamps", "utime"), ("chmod", "chmod"), ("read link", "readlink"),
    ("get list of extended", "xattr"), ("get value", "xattr"), ("set value", "xattr"),
    ("truncate", "truncate"), ("unlink", "unlink"), ("rmdir", "rmdir"), ("remove", "remove"), ("copy", "copy"),
)
_FAILURE_SAMPLES_MAX = 10
_FAILURE_PATH_MAX = 256
# mpirun 이 MPI_Abort 를 알리는 배너, mfu_abort 의 "ABORT: rank X on HOST: ..."(mfu_util.c fprintf(stderr), 접두사 없음) -- 둘 다
# **stderr 에만** 온다(러너는 두 흐름을 따로 받는다, 2>&1 없음 -- 실측: direct+bufsize 중단에서 stdout 에는 ABORT 글자가 없었다).
# 예전의 stdout 줄 머리 검사는 진짜 중단을 한 번도 잡지 못하고 위조 통로만 열었다(d192 리뷰: drm·dsync 는 경로를 날것으로 찍어
# 이름 "a\nABORT: x" 하나가 부분 rm 을 tool_aborted 로 바꿨다) -- 그래서 stdout 은 보지 않는다.
_ABORT_STDERR = re.compile(r"MPI_ABORT was invoked|(?:^| )ABORT: ", re.MULTILINE)


def _to_int(text) -> "int | None":
    """정수 문자열 -> 0..INT64 정수, 아니면 None. int() 는 4300자 넘는 숫자에 ValueError 다 -- 계약상 예외 금지."""
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= _INT64_MAX else None


def _last_int(pattern: "re.Pattern[str]", text: str) -> "int | None":
    matches = pattern.findall(text or "")
    return _to_int(matches[-1]) if matches else None


def _last_match(pattern: "re.Pattern[str]", text: str):
    last = None
    for last in pattern.finditer(text or ""):
        pass
    return last


def _sync_final_block(text: str):
    """dsync 최종 블록 -> (그 Items 줄의 시작 위치, Items, 같은 블록의 Data 바이트). 블록(바로 앞 줄이 "Seconds: X" 인 Items)이
    없으면 (None, None, None) -- 복사 계획 "Items:" 만 있는 출력(중단·강제 종료) 포함."""
    m = _last_match(_SYNC_FINAL_ITEMS, text)
    if m is None:
        return None, None, None
    data = _SYNC_FINAL_DATA.match(text, m.end())
    return m.start(), _to_int(m.group(1)), (_to_int(data.group(1)) if data is not None else None)


def parse_sync_counts(stdout: str) -> "tuple[int | None, int | None]":
    """dsync stdout -> (최종 블록 Items, 같은 블록의 Data 바이트). 최종 블록이 없으면 둘 다 None(복사 계획 줄로 지어내지 않는다).
    Items 는 도구가 센 항목 수 그대로(복사 실패 차감 전) -- 실행의 files 는 parse_dsync_counts 가 실패를 뺀 값이다."""
    _, items, nbytes = _sync_final_block(stdout or "")
    return items, nbytes


def _dsync_source_items(stdout: str) -> "int | None":
    matches = _SYNC_WALKED.findall(stdout or "")
    # walk 횟수보다 많으면 이름에 심은 가짜 줄이 섞였다 -- 어느 것이 소스인지 믿을 수 없다(위 주석).
    return _to_int(matches[0]) if 0 < len(matches) <= _DSYNC_MAX_WALKS else None


def parse_sync_dryrun_counts(stdout: str) -> "tuple[int | None, int | None]":
    """dsync --dryrun stdout -> (소스 walk 항목 수, None). 복사 요약이 있으면(도구가
    언젠가 dry-run 요약을 찍게 되면) 그쪽이 우선 -- 실행 파서와 같은 값 체계."""
    files, nbytes = parse_sync_counts(stdout)
    if files is None:
        files = _dsync_source_items(stdout)
    return files, nbytes


def parse_dsync_counts(stdout: str, aborted: bool = False) -> "tuple[int | None, int | None]":
    """dsync 실행 stdout -> (만든 항목 수, 최종 블록 Data 바이트). aborted(MPI 중단)면 (None, None).

    최종 Items 는 inode 를 만든 항목 수라 데이터 복사(열기·쓰기)에 실패한 파일도 들어 있다(실측: Items: 2, 「Failed to copy」 1 --
    실제로 생긴 파일은 1). 그 실패 줄을 빼야 「반영한 항목」이다(d192 -- 통계 sync_items_total 의 출처). 단 mknod 부터 실패한
    파일은 Items 에 없는데(total_files 증가 전에 return) 복사 단계에서 「Failed to copy」 가 한 번 더 찍힌다 -- 그 줄까지 빼면
    두 번 빠진다(d192 리뷰 runner-6: Items 8, mknod 실패 3 → 5 로 과소 보고). 그래서 목적지 경로가 mknod 실패 경로인 줄은 빼지
    않는다(_dsync_copied_items). 중단이면 최종 블록이 있어도 모름이다(d192 D6 -- 끝나지 않은 실행의 수를 「반영」으로 보이지 않는다)."""
    if aborted:
        return None, None
    text = stdout or ""
    at, items, nbytes = _sync_final_block(text)
    return _dsync_copied_items(text, items, at), nbytes


def _dsync_copied_items(out: str, items, before: int) -> "int | None":
    """최종 Items − 목적지가 mknod 실패 경로가 아닌 「Failed to copy」 줄 수(바닥 0). before = 최종 블록 위치 -- 그 뒤의 줄은
    그 블록의 Items 에 들 수 없다(배치 진행 블록에서 멈춘 출력·블록 뒤 날 경로에 심은 줄).

    mknod 경로 집합도 날 경로라 이름에 심은 가짜 줄로 늘 수 있다 -- 그러면 덜 빼서 최대 「Failed to copy」 줄 수만큼 크게 보이지만
    최종 Items 를 넘지는 않는다(남는 위험, 모듈 docstring)."""
    if items is None or before is None:
        return None
    head = out[:before]
    mknod = {m.group(1) for m in _DSYNC_MKNOD_FAILED.finditer(head)}
    failed = 0
    for m in _DSYNC_COPY_FAILED_REST.finditer(head):
        if not (mknod and _copy_dst_in(m.group(1), mknod)):
            failed += 1
    return max(0, items - failed)


def _copy_dst_in(rest: str, paths) -> bool:
    """「src' to `dst'」(날 경로 -- 이름에 "' to `" 가 들어 있을 수 있다)의 목적지가 paths 중 하나인가. 가르는 자리를 모르니
    가능한 분할을 전부 본다(그중 하나라도 mknod 실패 경로면 그 파일은 Items 에 없었다)."""
    body = rest.rstrip(" \t")
    if not body.endswith("'"):
        return False                      # 이름의 줄바꿈으로 쪼개진 줄 -- 목적지를 모른다(뺀다, 과대 보고하지 않는다)
    body = body[:-1]
    i = body.find("' to `")
    while i >= 0:
        if body[i + 6:] in paths:
            return True
        i = body.find("' to `", i + 1)
    return False


def parse_nsync_counts(stdout: str, dryrun: bool = False) -> "tuple[int | None, int | None]":
    """nsync stdout -> (files, bytes). 매치 없으면 해당 값 None.

    dryrun(미리보기) = (Planned 합 -- skipped-dst-only 제외, 마지막 planned-volume),
    실행 = (Completed 합 -- copied-bytes 제외, Completed copied-bytes 또는 없으면 마지막 copied-volume).
    실행에 Completed 줄이 없으면 files 는 모름이다 -- 계획 수를 「반영한 수」로 지어내지 않는다(포탈 괴리 [2]·[4])."""
    if dryrun:
        line = _last_match(_NSYNC_PLANNED_LINE, stdout)
        files = _pairs_sum(line.group(1), _NSYNC_SKIP_KEYS) if line is not None else None
        return files, _nsync_volume(stdout, _NSYNC_PLANNED_VOLUME)
    line = _last_match(_NSYNC_COMPLETED_LINE, stdout)
    files = nbytes = None
    if line is not None:
        files = _pairs_sum(line.group(1), frozenset({_NSYNC_COPIED_BYTES_KEY}))
        pairs = _NSYNC_ACTION_PAIR.findall(line.group(1))
        nbytes = next((_to_int(v) for k, v in pairs if k == _NSYNC_COPIED_BYTES_KEY), None)
    if nbytes is None:
        nbytes = _nsync_volume(stdout, _NSYNC_COPIED_VOLUME)
    return files, nbytes


def _pairs_sum(text: str, skip: frozenset) -> "int | None":
    # 마지막 줄이 최종이다(다른 파서와 같은 규칙). skip 키만 빼고 전부 더한다 -- 값 하나라도 수가 아니면 모름.
    total = 0
    for k, v in _NSYNC_ACTION_PAIR.findall(text):
        if k in skip:
            continue
        n = _to_int(v)
        if n is None:
            return None
        total += n
    return total if total <= _INT64_MAX else None


def _nsync_volume(stdout: str, pattern: "re.Pattern[str]") -> "int | None":
    matches = pattern.findall(stdout or "")
    if not matches:
        return None
    number, unit = matches[-1]
    factor = _NSYNC_UNITS.get(unit)
    if factor is None:
        # 모르는 단위면 그 값이 정답 후보였던 것이므로 None이다 -- 이전(오래된)
        # 매치로 물러나면 총량이 조용히 과소 보고된다.
        return None
    try:
        # 절사가 아니라 반올림: 절사는 항상 낮은 쪽으로 편향돼 잡이 쌓일수록
        # bytes_total이 체계적으로 줄어든다. 반올림 오차는 ±0.5 표시단위로 대칭이고
        # 소수 3자리 표기라 GiB 규모에서도 ≈0.05% 수준이다(설계 부록 A).
        value = round(float(number) * factor)
    except (ValueError, OverflowError):  # 자릿수 폭주 -> inf -> round 예외. 계약상 예외 금지
        return None
    return value if value <= _INT64_MAX else None


def _removed_line(stdout: str, max_lines: "int | None"):
    """마지막 「Removed N items in」 매치(없거나 max_lines 를 넘으면 None)."""
    matches = list(_RM_ITEMS.finditer(stdout or ""))
    if not matches or (max_lines is not None and len(matches) > max_lines):
        return None
    return matches[-1]


def _remove_failed_after(stdout: str, removed) -> "int | None":
    """마지막 Removed 줄 **뒤**의 첫 「Remove failed for F of T items」 -> F(F ≤ T 일 때만). 앞의 같은 모양은 위조다."""
    if removed is None:
        return None
    m = _RM_FAILED.search(stdout or "", removed.end())
    if m is None:
        return None
    failed, total = _to_int(m.group(1)), _to_int(m.group(2))
    return failed if failed is not None and total is not None and failed <= total else None


def parse_rm_counts(stdout: str) -> "tuple[int | None, None]":
    """drm 실행 stdout -> (실제로 지운 항목 수, None). drm은 바이트를 보고하지 않는다(설계 §1). 마지막 앵커 매치 --
    drm 은 요약 뒤에 경로를 찍지 않아 그 앞의 가짜 줄(unlink 오류 경로에 심은 것)은 마지막이 될 수 없다."""
    m = _removed_line(stdout, None)
    return (_to_int(m.group(1)) if m is not None else None), None


def parse_rm_dryrun_counts(stdout: str) -> "tuple[int | None, None]":
    """drm --dryrun stdout -> (지울 항목 수, None) = walk 요약의 첫 최종 줄(drm 은 경로 하나를 한 번 walk 한다 -- 진행 줄
    "secs ..." 는 배제). 삭제 요약("Removed N items")은 보지 않는다: dry-run 은 mfu_flist_unlink 를 부르지 않아 그 줄이
    절대 나오지 않고(drm.c, mfu_flist_remove.c), 앵커 없는 옛 정규식은 목록에 찍힌 파일 이름("Removed 1 items.txt")만
    잡아 walk 수를 덮어썼다(2026-10-12 리뷰 -- 「삭제 대상 1개」인데 실행은 25만 개).

    walk 개수 = 지울 개수인 근거: DMS 는 drm 에 regex 필터(--match/--exclude/--name)를 싣지 않는다(rm 옵션은
    recursive·stat·lite·quiet -- domain._OPTION_SPECS, execution_manifests._RM_BOOL_FLAGS) -- 그래서 walk 한 목록 전체가
    지울 대상이고(drm.c: 필터가 없으면 srclist = flist), 실행의 "Removed N" 과 같은 값이다(대상 경로 자신과 하위
    디렉터리·파일·링크 합계 -- 실측 Walked 6 → Removed 6). 필터 옵션을 싣게 되면 이 등식이 깨진다 -- 그때는 이 파서를
    다시 봐라. 실행 단계에는 쓰지 않는다: 실행의 개수는 실제로 지운 수여야 한다(walk 수로 지어내지 않는다).

    0 의 뜻: walk 는 대상이 있으면 대상 자신을 센다(빈 디렉터리도 1 -- mfu_flist_walk.c walk_create) -- 0 은 대상의
    lstat 이 실패했다는 뜻(경로 없음·권한)이고 drm 은 그래도 rc 0 으로 끝난다(drm.c 가 walk 결과를 버린다). 값은 사실
    그대로 0(지울 것이 없다)으로 두고, 포탈이 rm 의 0 을 「대상 경로를 찾지 못함」으로 따로 말한다."""
    matches = _SYNC_WALKED.findall(stdout or "")
    # drm 은 정확히 한 번 walk 한다 -- 둘 이상이면 경로 이름에 심은 가짜 줄이 섞였다(위 _DRM_WALKS 주석), 모름.
    return (_to_int(matches[0]) if len(matches) == _DRM_WALKS else None), None


def parse_aborted(stderr: str) -> bool:
    """도구가 MPI_Abort 로 끝났는가(mpirun 배너 또는 mfu_abort 줄) -- **stderr 만** 본다(_ABORT_STDERR 주석: 진짜 흔적은
    stderr 에만 오고, stdout 은 dsync·drm 의 날 경로라 위조 통로다). quiet 에서도 남는 유일한 흔적이다."""
    return _ABORT_STDERR.search(stderr if isinstance(stderr, str) else "") is not None


def parse_tool_detail(tool, stdout, stderr="", dryrun=False) -> "dict | None":
    """도구 출력 -> summary v2 상세 키(d192 spec §1). 찾은 키만 싣는다(없음 = 모름 또는 해당 없음, 0 으로 뭉개지 않는다).

    키 목록은 모듈 docstring 의 상세 키 표다. 참·거짓 키는 참일 때만 싣는다. 밑줄로 시작하는 키는 사유 판정용 내부 근거라
    러너가 summary 에 싣지 않는다. 핵심 3키(returncode·files·bytes)와 머리 키(detail_version·tool·dryrun·quiet)·reason 은
    러너(_build_summary)의 몫이다.

    예외는 던지지 않는다: 내부에서 무엇이든 터지면 None(상세를 계산하지 못했다 -- 러너는 핵심 3키만 쓴다)."""
    try:
        out = stdout if isinstance(stdout, str) else ""
        aborted = parse_aborted(stderr)
        detail = {}
        if tool == "nsync":
            _nsync_detail(out, bool(dryrun), detail)
        elif tool == "dsync":
            _dsync_detail(out, bool(dryrun), detail, aborted)
        elif tool == "drm":
            _drm_detail(out, bool(dryrun), detail)
        if aborted:
            detail["aborted"] = True
        return detail
    except Exception:  # noqa: BLE001 -- fail-soft 가 계약이다(설계 §4)
        return None


def _action_counts(text: str) -> "dict | None":
    """「copy=3 mkdir=1 symlink-update=0 ...」 -> {"copy": 3, "mkdir": 1, "symlink_update": 0, ...}(하이픈 -> 밑줄)."""
    out = {}
    for k, v in _NSYNC_ACTION_PAIR.findall(text):
        n = _to_int(v)
        if n is None or len(k) > 32:
            return None
        out[k.replace("-", "_")] = n
        if len(out) > _NSYNC_MAX_KINDS:
            return None
    return out or None


def _set_int(detail: dict, key: str, value) -> None:
    if value is not None:
        detail[key] = value


def _nsync_failure_lines(out: str):
    """(목록 줄 매치들, 모든 실패 errno 집합) -- errno 는 목록 + 즉시 줄 전부(샘플 10개 밖까지)."""
    listed = list(_NSYNC_FAILED_LISTED.finditer(out))
    errnos = set()
    for m in [*listed, *_NSYNC_FAILED_NOW.finditer(out)]:
        n = _to_int(m.group(3))
        if n is not None and n > 0:
            errnos.add(n)
    return listed, errnos


def _sample(op: str, path: str, errno_text: str) -> dict:
    n = _to_int(errno_text)
    # errno 0 은 nsync 의 「unknown error」 -- 이유 모름(null)이다.
    return {"op": op, "path": path[:_FAILURE_PATH_MAX], "errno": n if n is not None and n > 0 else None}


def _nsync_detail(out: str, dryrun: bool, d: dict) -> None:
    planned_line = _last_match(_NSYNC_PLANNED_LINE, out)
    if planned_line is not None:
        planned = _action_counts(planned_line.group(1))
        if planned is not None:
            d["planned"] = planned
    if dryrun:
        _set_int(d, "planned_bytes", _nsync_volume(out, _NSYNC_PLANNED_VOLUME))
    else:
        done_line = _last_match(_NSYNC_COMPLETED_LINE, out)
        done = _action_counts(done_line.group(1)) if done_line is not None else None
        if done is not None:
            copied = done.pop(_NSYNC_COPIED_BYTES_KEY.replace("-", "_"), None)
            d["done"] = done
            _set_int(d, "copied_bytes", copied)
    tc = _last_match(_NSYNC_TYPE_CHANGES, out)
    if tc is not None:
        to_dir, from_dir = _to_int(tc.group(1)), _to_int(tc.group(2))
        if to_dir is not None and from_dir is not None:
            d["type_changes"] = {"to_dir": to_dir, "from_dir": from_dir}
    if _NSYNC_TYPE_NEEDS_DELETE.search(out) is not None:
        d["type_change_needs_delete"] = True
    _set_int(d, "skipped_special", _last_int(_SKIPPED_SPECIAL, out))

    failed_items = _last_match(_NSYNC_FAILED_ITEMS, out)
    failures_total = None
    if failed_items is not None:
        failures_total = _to_int(failed_items.group(1))
        by_op = {}
        for op, n in _NSYNC_OP_COUNT.findall(failed_items.group(2)):
            count = _to_int(n)
            if count is not None:
                by_op[op] = count
        if failures_total is not None:
            d["failures"] = {"total": failures_total, "by_op": by_op}
            # 비어 있지 않은 디렉토리를 --delete 없이 파일·링크로 바꾸지 않은 수(NSYNC_FAIL_REPLACE).
            _set_int(d, "type_change_refused", by_op.get("replace-dir"))
    listed, errnos = _nsync_failure_lines(out)
    samples = [_sample(m.group(1), m.group(2), m.group(3)) for m in listed[:_FAILURE_SAMPLES_MAX]]
    if samples:
        d["failure_samples"] = samples

    not_attempted = _last_int(_NSYNC_NOT_ATTEMPTED, out)
    if not_attempted is None:
        not_attempted = _last_int(_NSYNC_STOPPED_EARLY_RC, out)
    _set_int(d, "not_attempted", not_attempted)
    stopping = [m.group(1) for m in _NSYNC_STOPPING.finditer(out)]
    stopped = bool(stopping) or _NSYNC_STOPPED_COVER.search(out) is not None
    if stopped:
        d["stopped_early"] = True
    if stopped or (not_attempted is not None and not_attempted > 0):
        cause = None
        if any("reached --max-errors" in s for s in stopping):
            cause = "max_errors"
        elif errnos & _ERRNO_FULL:
            cause = "destination_full"
        elif _ERRNO_READ_ONLY in errnos:
            cause = "destination_read_only"
        elif (failures_total is not None and failures_total > 0
              and not any("full or read-only" in s for s in stopping)):
            # 배치 안에서 멈춘 경우(배치 1개 실행 -- 「Stopping after batch」 줄이 없다): 원인은 가득 참·읽기 전용이거나
            # --max-errors 몫이다(nsync_batch_stop_now). 앞의 둘이 실패 errno 에 없으면 남는 것은 max-errors 다.
            cause = "max_errors"
        if cause is not None:
            d["stop_cause"] = cause

    _set_int(d, "scan_errors", _last_int(_NSYNC_SCAN_ERRORS, out))
    if _NSYNC_DELETE_OFF.search(out) is not None:
        d["delete_disabled"] = True
        _set_int(d, "source_scan_errors", _last_int(_NSYNC_DELETE_OFF, out))
    _set_int(d, "metadata_ignored", _last_int(_NSYNC_META_IGNORED, out))
    _set_int(d, "metadata_failed", _last_int(_NSYNC_META_FAILED, out))
    _set_int(d, "noatime_fallbacks", _last_int(_NSYNC_NOATIME, out))


def _mfu_op(head: str, mid: str) -> str:
    """dsync·drm 오류 줄 머리(첫 백틱 앞)·가운데(경로 뒤) -> 짧은 ASCII 연산 이름."""
    if head.startswith("Failed to "):
        verb = head[len("Failed to "):]
        for prefix, op in _MFU_FAILED_TO_OPS:
            if verb.startswith(prefix):
                return op
        word = verb.split(" ", 1)[0].rstrip(":").lower()
        return word if re.fullmatch(r"[a-z]{1,16}", word) else "other"
    if head.startswith("Write error"):
        return "write"
    if head.startswith("Read error"):
        return "read"
    if head.startswith("Create "):
        return "symlink" if "symlink()" in mid else "mkdir"
    if head.startswith("File ") and "mknod()" in mid:
        return "mknod"
    if head.startswith("Couldn't seek"):
        return "seek"
    if head.startswith("Type change not applied"):
        return "type-change"
    if head.startswith("mfu_file_lstat()"):
        return "stat"
    if head.startswith("mfu_file_open()"):
        return "open"
    return "other"


def _mfu_failures(out: str):
    """dsync·drm 항목 실패 줄 -> (샘플 ≤10 -- 출력 순서, 제거 단계 errno 정렬 목록 -- 샘플 상한 밖까지 전부, 모르는 errno 는 0).

    제거 단계 errno 는 dsync 타입 교체 사유(runner._derive_reason 규칙 5)의 근거다: 가짜 줄은 errno 39 를 **더할** 수만 있고
    진짜 errno 1·13 줄을 지우지 못하므로 「전부 39·17」은 위조로 만들어지지 않는다(이름의 줄바꿈이 제 줄을 쪼개는 경우는
    _type_change_dir_refused 의 \\ 검사가 막는다 -- _dsync_detail)."""
    samples = []
    remove_errnos = set()
    for m in _MFU_ERRNO_LINE.finditer(out):
        head, path, mid = m.group(1), m.group(2), m.group(3)
        op = _mfu_op(head, mid)
        if op in _REMOVE_OPS:
            n = _to_int(m.group(4))
            remove_errnos.add(n if n is not None else 0)
        if len(samples) >= _FAILURE_SAMPLES_MAX:
            continue
        if head.endswith("copying from "):
            # 「from `소스' to `목적지'」 -- 항목을 가리키는 소스 경로만(탐욕 매치가 두 경로를 한 덩어리로 잡는다).
            path = path.rsplit("' to `", 1)[0]
        samples.append(_sample(op, path, m.group(4)))
    return samples, sorted(remove_errnos)


def _dsync_detail(out: str, dryrun: bool, d: dict, aborted: bool = False) -> None:
    if dryrun:
        _set_int(d, "source_items", _dsync_source_items(out))
    else:
        at, items, nbytes = _sync_final_block(out)
        d["copy_failed"] = len(_DSYNC_COPY_FAILED.findall(out))
        d["create_failed"] = len(_DSYNC_CREATE_FAILED.findall(out))
        if not aborted:
            # 중단이면 최종 블록이 있어도 「반영한 수」로 보이지 않는다(parse_dsync_counts 와 같은 규칙 -- files·bytes 의 출처).
            _set_int(d, "copied_items", _dsync_copied_items(out, items, at))
            _set_int(d, "copied_bytes", nbytes)
        removed = _removed_line(out, _DSYNC_MAX_REMOVED)
        if removed is not None:
            _set_int(d, "removed", _to_int(removed.group(1)))
            _set_int(d, "remove_failed", _remove_failed_after(out, removed))
        refused = _last_int(_DSYNC_TYPE_REFUSED, out)
        _set_int(d, "type_change_refused", refused)
        if refused is not None:
            # 내부 근거(summary 에 싣지 않는다): 「비어 있지 않은 디렉토리 + delete 꺼짐」 줄 수. 경로에 \ 가 있으면(= 이름에 제어
            # 문자·\ 가 있었다, dsync_escape_path) 세지 않는다 -- 그런 이름은 날 경로인 제 rmdir 실패 줄을 쪼개 errno 를 숨길 수
            # 있다. 세지 않으면 수가 어긋나 일반 항목 실패로 떨어진다(안전한 쪽).
            d["_type_change_dir_refused"] = sum(1 for m in _DSYNC_TYPE_REFUSED_DIR.finditer(out) if "\\" not in m.group(1))
    _set_int(d, "skipped_special", _last_int(_SKIPPED_SPECIAL, out))
    walk_errors = _DSYNC_WALK_ERRORS.search(out)
    if walk_errors is not None:
        # 개수를 찍지 않는다 -- 지어낸 「1」 대신 참·거짓(d192 리뷰: 소스 40곳을 못 읽어도 「소스 1곳」으로 보였다).
        d["scan_incomplete"] = True
        if walk_errors.group(1):
            d["delete_disabled"] = True
    samples, remove_errnos = _mfu_failures(out)
    if samples:
        d["failure_samples"] = samples
    if not dryrun and "_type_change_dir_refused" in d:
        d["_remove_errnos"] = remove_errnos
    d["tool_errors"] = len(_ERR_LINE.findall(out))


def _drm_detail(out: str, dryrun: bool, d: dict) -> None:
    if dryrun:
        _set_int(d, "target_items", parse_rm_dryrun_counts(out)[0])
    else:
        removed = _removed_line(out, None)
        if removed is not None:
            _set_int(d, "removed", _to_int(removed.group(1)))
            _set_int(d, "remove_failed", _remove_failed_after(out, removed))
    samples, _ = _mfu_failures(out)
    if samples:
        d["failure_samples"] = samples
    d["tool_errors"] = len(_ERR_LINE.findall(out))


def parse_scan_counts(report_path: str) -> "tuple[int | None, int | None]":
    """dscan-report.json -> (summary.total_entries, 총 바이트=실 사용량).

    리포트에 총 바이트 필드는 없지만 time_histograms 의 각 히스토그램(atime/
    mtime/ctime)은 **버킷별 bytes** 를 담고, 파일마다 타임스탬프가 정확히 한
    버킷에 떨어지므로 한 히스토그램의 bytes 합 == 스캔 트리의 파일 크기 합이다
    (2026-08-23 실측: atime/mtime/ctime 세 합이 모두 일치). dscan 스키마를
    바꾸지 않고 실 사용량을 얻는 경로라 mtime -> atime -> ctime 순으로 첫
    "온전한" 히스토그램을 쓴다 -- 버킷 하나라도 bytes 가 수상하면(비정수·bool·
    음수) 그 히스토그램은 총량 출처로 부적격이라 다음 후보로 넘어간다(부분합은
    nsync 미지 단위와 같은 이유로 금지: 조용한 과소 보고). 파일 없음/JSON 깨짐/
    키 없음/타입 이상 -> 해당 값 None. bool·음수 배제는 승격 경로 _as_count와
    같은 원칙 -- 여기서 먼저 걸러 summary 자체를 깨끗하게 유지한다."""
    try:
        # encoding 명시: 로케일 기본 인코딩(비 UTF-8 컨테이너)에서 한글 경로가
        # 든 리포트가 UnicodeDecodeError로 터지면 fail-soft가 삼켜 조용히 NULL이 된다.
        # errors="replace"(2026-10-20 프로덕션): dscan 은 파일 이름 바이트를 그대로 JSON 에 쓴다(0x80 이상을
        # 이스케이프하지 않는다) -- CP949 이름 하나가 broken_paths 에 들면 strict 디코드가 터져 files/bytes 가
        # 조용히 null 이었다. 쓰는 값은 숫자뿐이고 replace 는 ASCII(따옴표·괄호)를 삼키지 않아 JSON 구조가 남는다.
        with open(report_path, encoding="utf-8", errors="replace") as f:
            report = json.load(f)
    except Exception:  # noqa: BLE001 -- 계약이 "절대 예외 없음"(설계 §4)이라 문자 그대로 지킨다
        return None, None
    if not isinstance(report, dict):
        return None, None
    summary = report.get("summary")
    entries = summary.get("total_entries") if isinstance(summary, dict) else None
    if isinstance(entries, bool) or not isinstance(entries, int) or entries < 0:
        entries = None
    return entries, _scan_total_bytes(report.get("time_histograms"))


def _scan_total_bytes(time_histograms) -> "int | None":
    """time_histograms -> 첫 온전한 히스토그램의 bytes 합. 없으면 None."""
    if not isinstance(time_histograms, dict):
        return None
    for key in ("mtime", "atime", "ctime"):
        buckets = time_histograms.get(key)
        if not isinstance(buckets, list) or not buckets:
            continue
        total = 0
        for bucket in buckets:
            value = bucket.get("bytes") if isinstance(bucket, dict) else None
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                total = None
                break
            total += value
        if total is not None:
            return total
    return None
