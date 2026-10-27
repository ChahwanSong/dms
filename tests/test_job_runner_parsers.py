"""parsers.py 단위 테스트. 픽스처는 테스트베드 실 잡의 캡처 출력을 그대로 고정한다
(잡 60d24700 dsync stdout, dscan-report.json 실 스키마, 잡 abc0b559 nsync stdout -- 설계 부록 A,
포크 7a3189c 의 drm·nsync 실행 출력 -- 2026-10-27 d192) -- 파서가 "실제 도구가 찍는 것"을 파싱함을 픽스처
수준에서 보증한다(설계 §6). 요약 줄은 MFU 접두사 "[YYYY-MM-DDTHH:MM:SS] " 에 줄 머리부터 맞춘다(d192) -- 합성
입력도 실제 접두사를 쓴다(예전의 "[ts] " 는 앵커 밖이라 무엇을 넣어도 None 이 되어 아무것도 시험하지 않는다).
summary v2 상세·사유는 tests/test_job_runner_tool_detail.py 가 본다."""
import json

from dms_job_runner.parsers import (parse_dsync_counts, parse_nsync_counts, parse_rm_counts,
                                    parse_scan_counts, parse_sync_counts, parse_tool_detail)

TS = "[2026-10-27T13:00:00] "

# 실측 dsync stdout(잡 60d24700, 파싱 관련 줄 전체). 같은 stdout에 walk 단계의
# 패딩 요약("Items     : 0"), 복사 단계 중간 요약(Items: 10 + per-file Data),
# Copy data/Copy rate, 최종 블록(Items/Data/Rate)이 전부 공존한다 -- 파서는 이
# 전체에서 최종 블록만 골라내야 한다(설계 §1). d192 부터 최종 블록은 「바로 앞 줄이 패딩 없는 Seconds: X 인 Items」라 그 줄도
# 파싱 관련 줄이다(실제 출력의 최종 블록 머리 -- mfu_flist_copy.c 2795-2803, 값은 같은 블록 Rate 줄의 0.049 초).
DSYNC_STDOUT = """\
[2026-08-04T02:14:12] Walked 10 items in 0.001 seconds (15463.025 items/sec)
[2026-08-04T02:14:12] Started   : Aug-04-2026, 02:14:12
[2026-08-04T02:14:12] Seconds   : 0.000
[2026-08-04T02:14:12] Items     : 0
[2026-08-04T02:14:12] Copying items to destination
[2026-08-04T02:14:12] Items: 10
[2026-08-04T02:14:12]   Directories: 3
[2026-08-04T02:14:12]   Files: 7
[2026-08-04T02:14:12] Data: 50.000 B (7.000 B per file)
[2026-08-04T02:14:12] Copy data: 50.000 B (50 bytes)
[2026-08-04T02:14:12] Copy rate: 3.284 KiB/s (50 bytes in 0.015 seconds)
[2026-08-04T02:14:12] Seconds: 0.049
[2026-08-04T02:14:12] Items: 10
[2026-08-04T02:14:12]   Directories: 3
[2026-08-04T02:14:12]   Files: 7
[2026-08-04T02:14:12]   Links: 0
[2026-08-04T02:14:12] Data: 50.000 B (50 bytes)
[2026-08-04T02:14:12] Rate: 0.991 KiB/s (050 bytes in 0.049 seconds)
[2026-08-04T02:14:12] Completed sync
"""

# 실측 캡처는 중간·최종 값이 같아(10/50) "최종 블록이 이긴다"를 증명하지 못한다.
# 값을 달리한 합성 변형으로 규칙을 고정한다: 복사 계획(Items: 4 + per-file Data)·Copy data(시도 합)는 최종 블록이 아니다.
DSYNC_STDOUT_MID_DIFFERS = """\
[2026-08-04T02:14:12] Copying to /d
[2026-08-04T02:14:12] Items: 4
[2026-08-04T02:14:12] Data: 20.000 B (5.000 B per file)
[2026-08-04T02:14:12] Copy data: 20.000 B (20 bytes)
[2026-08-04T02:14:12] Seconds: 0.020
[2026-08-04T02:14:12] Items: 10
[2026-08-04T02:14:12] Data: 50.000 B (50 bytes)
"""

# 실측 nsync stdout(잡 abc0b559 실행 단계, 설계 부록 A -- Completed 줄 이전 빌드). nsync는 stock mpifileutils가
# 아니라 역할 기반(src/dst rank) 별도 도구라 "Items:"/"(N bytes)"를 전혀 찍지 않는다 --
# 진행 줄의 actions=9는 planned 10과 어긋나므로(배치 병합) 쓰지 않는다. 이 빌드에는 실제로 반영된 수(Completed)가
# 없다 -- 실행 files 는 모름이고, 바이트만 진행 줄의 copied-volume=50.000 B 로 물러난다(d192).
NSYNC_EXEC_STDOUT = """\
[2026-08-10T01:28:39] Progress 100.0% batch 1/1 actions=9 copied-files=7 copied-volume=50.000 B recent(actions=9 files=7 volume=50.000 B, 27.35 files/s, 195.333 B/s over 0.256 s) avg(27.35 files/s, 195.333 B/s)
[2026-08-10T01:28:39] nsync Phase 5 planner+execute complete
[2026-08-10T01:28:39] Requested source: /cephfs-third/nsync-src
[2026-08-10T01:28:39] Requested target: /cephfs-secondary/nsync-dst
[2026-08-10T01:28:39] Options: dryrun=0 batch-files=0 delete=0 contents=0 role-mode=map imbalance-threshold=3.00
[2026-08-10T01:28:39] Role map: 0:src,1:src,2:src,3:src,4:src,5:src,6:dst,7:dst,8:dst,9:dst
[2026-08-10T01:28:39] Metadata diff summary: only-src=10 only-dst=0 common=0 changed=0
[2026-08-10T01:28:39] Planned actions: copy=7 mkdir=3 symlink-update=0 meta-update=0 remove=0 skipped-dst-only=0
[2026-08-10T01:28:39] Execution completed successfully
"""

# 같은 잡의 프리뷰(dryrun) 단계. "Planned actions:" 줄은 실행과 같은 형식이고
# 진행 줄 어휘만 다르다(planned-actions/planned-copy-files/planned-volume). 미리보기 files 는 계획 합(변경 예정)이다.
NSYNC_PREVIEW_STDOUT = """\
[2026-08-10T01:28:19] Progress 100.0% batch 1/1 planned-actions=10 planned-copy-files=7 planned-volume=50.000 B
[2026-08-10T01:28:19] nsync Phase 5 planner dryrun complete
[2026-08-10T01:28:19] Options: dryrun=1 batch-files=0 delete=0 contents=0 role-mode=map imbalance-threshold=3.00
[2026-08-10T01:28:19] Metadata diff summary: only-src=10 only-dst=0 common=0 changed=0
[2026-08-10T01:28:19] Planned actions: copy=7 mkdir=3 symlink-update=0 meta-update=0 remove=0 skipped-dst-only=0
"""

# 실측 nsync 실행(포크 7a3189c, 2026-10-27 samples nsync_exec_items_failed 의 요약 부분): 5개 복사 실패 -- Planned 합은 28,
# 실제로 반영된 Completed 합은 23. 실행 files 는 23 이다(포탈 괴리 [2]·[4] -- 예전엔 28 을 「복사한 파일」로 보였다).
NSYNC_EXEC_D192_STDOUT = """\
[2026-10-27T13:52:40] Progress 100.0% batch 1/1 actions=22 copied-files=20 copied-volume=100.000 B recent(actions=22 files=20 volume=100.000 B, 9086.60 files/s, 44.368 KiB/s over 0.002 s) avg(9086.60 files/s, 44.368 KiB/s)
[2026-10-27T13:52:40] [0] [/release/mpifileutils/src/nsync/nsync.c:897] ERROR: Failed items: 5 (copy 5); listed below: 5
[2026-10-27T13:52:40]   failed copy `ro/r1`: Permission denied (errno 13)
[2026-10-27T13:52:40] Planned actions: copy=25 mkdir=2 symlink-update=0 meta-update=1 remove=0 skipped-dst-only=0
[2026-10-27T13:52:40] Completed actions: copy=20 mkdir=2 symlink-update=0 meta-update=1 remove=0 copied-bytes=100
[2026-10-27T13:52:40] [0] [/release/mpifileutils/src/nsync/nsync.c:9907] ERROR: Execution completed with errors: scan=0 exec=5
"""

# 실측 drm stdout(포크 7a3189c, 2026-10-27 samples drm_exec_ok): "Removed N items in S seconds (" 가 실제로 지운 수다.
# "Removing N items"(시작 줄)·"Walked N items" 와 혼재하고 bytes 는 보고하지 않는다(설계 §1).
DRM_STDOUT = """\
[2026-10-27T13:52:58] Walking /tmp/smp/rm2
[2026-10-27T13:52:58] Walked 3 items in 0.001 secs (4580.733 items/sec) ...
[2026-10-27T13:52:58] Walked 3 items in 0.001 seconds (3221.099 items/sec)
[2026-10-27T13:52:58] Removing 3 items
[2026-10-27T13:52:58] Removed 3 items in 0.001 seconds (4094.183 items/sec)
"""

# 실측 dscan-report.json 스키마(dscan 1b93d54 write_report): top_k·oldest 제거,
# broken_paths_total(정확 총계)·broken_paths_limit·summary.scan_errors 신설.
# 시간 히스토그램은 버킷별 bytes 를 담고 한 히스토그램의 합 == 트리 파일 크기 합
# (2026-08-23 실측, 실 사용량의 출처) — top-level 키를 실 스키마대로 유지해
# 픽스처가 실물을 대표하게 한다.
def _age_buckets(bytes_by_bucket):
    return [{"bucket": f"[{lo}d,{hi}d]", "min_age_days": lo, "max_age_days": hi,
             "bytes": b}
            for (lo, hi), b in zip(((0, 1), (2, 7), (8, 30)), bytes_by_bucket)]


DSCAN_REPORT = {
    "directory": "/cephfs/dms/smoke-src",
    "generated_at_epoch": 1754273652,
    "thresholds": {},
    "summary": {"total_entries": 10, "total_files": 7, "total_directories": 3,
                "total_symlinks": 0, "total_other": 0, "scan_errors": 0},
    "file_size_histogram": [],
    # 세 히스토그램의 합은 항상 같다(파일마다 각 타임스탬프가 정확히 한 버킷).
    "time_histograms": {"atime": _age_buckets([50, 0, 0]),
                        "mtime": _age_buckets([30, 20, 0]),
                        "ctime": _age_buckets([0, 50, 0])},
    "broken_paths_total": 1,
    "broken_paths_limit": 100,
    "broken_paths": [{"path": "/cephfs/dms/smoke-src/dangling",
                      "reasons": ["missing"]}],
}


# ---- parse_sync_counts ----

def test_sync_real_capture_final_items_and_bytes():
    # 최종 블록의 Items: 10 / Data: (50 bytes) -- 중간 요약·rate 줄이 있어도
    assert parse_sync_counts(DSYNC_STDOUT) == (10, 50)


def test_sync_last_match_wins_over_mid_copy_summary():
    assert parse_sync_counts(DSYNC_STDOUT_MID_DIFFERS) == (10, 50)


def test_sync_copy_plan_alone_is_not_the_final_block():
    # d192 리뷰(contract-31·portal-jobs-12): 복사 중에 중단·강제 종료되면 최종 블록이 없다 -- 남은 "Items: N" 은 복사 **계획**
    # 이고 "Copy data" 는 시도 합이다. 예전 「마지막 매치」 규칙은 (4, 20) 을 「반영」으로 냈다.
    plan_only = DSYNC_STDOUT_MID_DIFFERS.split("[2026-08-04T02:14:12] Seconds: ")[0]
    assert parse_sync_counts(plan_only) == (None, None)
    assert parse_dsync_counts(plan_only) == (None, None)
    # 앞 줄이 Seconds 가 아닌 Items 는 최종 블록이 아니다(패딩된 walk 비교 블록 "Seconds   :" 도 아니다).
    assert parse_sync_counts(f"{TS}Items: 7\n{TS}Data: 7.000 B (7 bytes)\n") == (None, None)
    assert parse_sync_counts(f"{TS}Seconds   : 0.000\n{TS}Items: 7\n") == (None, None)
    # 중단(aborted)이면 최종 블록이 있어도 모름(d192 D6)
    assert parse_dsync_counts(DSYNC_STDOUT, aborted=True) == (None, None)


def test_sync_padded_walk_items_is_not_matched():
    # walk 단계 "Items     : 0"(콜론 앞 패딩)은 "Items: " 형태가 아니라 자연 배제 --
    # 이것만 있으면 최종 요약이 없는 것이므로 None(설계 §3)
    assert parse_sync_counts(f"{TS}Seconds   : 0.000\n{TS}Items     : 0\n") == (None, None)
    assert parse_sync_counts(f"{TS}Seconds: 0.001\n{TS}Items: 0\n") == (0, None)   # 대조: 최종 블록 모양은 읽힌다(0 은 정상값)


def test_sync_bytes_in_rate_line_is_not_matched():
    # "(50 bytes in 0.015 seconds)"는 "bytes" 뒤에 닫는 괄호가 바로 오지 않아
    # 배제된다 -- 전송률이 총량으로 새면 안 된다(설계 §3)
    out = f"{TS}Copy rate: 3.284 KiB/s (50 bytes in 0.015 seconds)\n"
    assert parse_sync_counts(out) == (None, None)


def test_sync_bytes_embedded_mid_line_is_not_matched():
    # 요약 줄은 "Data:"/"Copy data:" 로 시작해 "(N bytes)"로 끝난다 -- 다른 줄 끝에 박힌 같은 모양(예: 경로
    # 이름에 든 "(999 bytes)")이 총량 행세를 하면 안 된다.
    assert parse_sync_counts(f"{TS}copying /backup (999 bytes)/f.bin to x\n") == (None, None)
    assert parse_sync_counts(f"{TS}Copying to /backup (999 bytes)\n") == (None, None)


def test_sync_items_with_trailing_text_is_not_matched():
    # "Items: 10 (done)"처럼 숫자 뒤에 내용이 붙으면 요약 줄이 아니다 -- $ 앵커가 배제
    assert parse_sync_counts(f"{TS}Seconds: 0.1\n{TS}Items: 10 (done)\n") == (None, None)
    assert parse_sync_counts(f"{TS}Seconds: 0.1\n{TS}Items: 10\n") == (10, None)       # 대조


def test_sync_summary_must_start_at_the_line_head():
    # d192: 줄 머리 앵커. 경로 텍스트 안의 "Items: 999" 줄 끝 모양(dsync 오류 줄은 경로를 날것으로 찍는다)은 요약이 아니다.
    out = (f"{TS}[0] [mfu_flist_copy.c:2092] ERROR: Failed to open input file `/a Items: 999\n"
           f"{TS}Walking /b/Data: 1 B (999 bytes)\n")
    assert parse_sync_counts(out) == (None, None)


# ---- parse_dsync_counts (2026-10-27 d192) ----

def test_dsync_execution_files_subtract_failed_copies():
    # 최종 Items 는 inode 를 만든 항목 -- 데이터를 열지 못한 파일도 들어 있다. 실제로 만든 항목 = Items − 「Failed to copy」 줄 수.
    out = (f"{TS}[0] [mfu_flist_copy.c:2092] ERROR: Failed to open input file `/s/no' (errno=13 Permission denied)\n"
           f"{TS}[0] [mfu_flist_copy.c:2256] ERROR: Failed to copy `/s/no' to `/d/no'\n"
           f"{TS}Seconds: 0.010\n{TS}Items: 2\n{TS}Data: 3.000 B (3 bytes)\n")
    assert parse_dsync_counts(out) == (1, 3)
    assert parse_sync_counts(out) == (2, 3)                            # 원래 Items 는 그대로(차감 전)
    assert parse_dsync_counts(DSYNC_STDOUT) == (10, 50)                # 실패가 없으면 같다
    # 바닥 0: 실패 줄이 Items 보다 많아도(위조 등) 음수가 되지 않는다
    assert parse_dsync_counts(out.replace(f"{TS}Items: 2\n", f"{TS}Items: 0\n")) == (0, 3)
    assert parse_dsync_counts("") == (None, None)
    # mknod 부터 실패한 파일은 Items 에 없다 -- 그 「Failed to copy」 는 빼지 않는다(d192 리뷰 runner-6, 두 번 빼기).
    mknod = (f"{TS}[0] [mfu_flist_copy.c:1273] ERROR: File `/d/no' mknod() failed (errno=13 Permission denied)\n"
             + out.replace(f"{TS}Items: 2\n", f"{TS}Items: 1\n"))
    assert parse_dsync_counts(mknod) == (1, 3)
    # 날 경로에 "' to `" 가 들어 있어도 목적지를 맞춘다(가능한 분할 전부)
    odd = mknod.replace("/d/no", "/d/n' to `o").replace("/s/no", "/s/n' to `o")
    assert parse_dsync_counts(odd) == (1, 3)
    # 최종 블록 **뒤**의 「Failed to copy」(블록 뒤 날 경로에 심은 줄)는 그 블록의 Items 에 들 수 없다
    late = out + f"{TS}[0] [mfu_flist_copy.c:2256] ERROR: Failed to copy `/s/x' to `/d/x'\n"
    assert parse_dsync_counts(late) == (1, 3)


def test_sync_empty_and_irrelevant_stdout():
    # --quiet 억제·조기 실패 등으로 요약이 없으면 값만 null(설계 §4 fail-soft)
    assert parse_sync_counts("") == (None, None)
    assert parse_sync_counts("no summary here\nCompleted sync\n") == (None, None)


# ---- parse_nsync_counts ----

def test_nsync_execution_files_are_what_took_effect():
    # d192: 실행 files = Completed 합(20+2+0+1+0 = 23), bytes = Completed copied-bytes. Planned 합(28)이 아니다 --
    # 예전 docstring 의 「Planned 합 = 같은 트리의 dsync Items」는 틀렸다(포탈 괴리 [20]).
    assert parse_nsync_counts(NSYNC_EXEC_D192_STDOUT) == (23, 100)
    assert parse_nsync_counts(NSYNC_EXEC_D192_STDOUT, dryrun=True)[0] == 28     # 같은 출력의 계획 합


def test_nsync_execution_without_completed_line_is_unknown():
    # Completed 줄 이전 빌드의 실측: 실제로 반영된 수를 알 수 없다 -- 계획 합(10)을 지어내지 않는다. 바이트는 진행 줄로 물러난다.
    assert parse_nsync_counts(NSYNC_EXEC_STDOUT) == (None, 50)


def test_nsync_preview_counts_the_planned_changes():
    # 미리보기 files = Planned 합(변경 예정 -- 타입 교체는 remove+copy 두 번, 삭제·속성 갱신 포함, 특수 파일 제외).
    # 바이트는 planned-volume=.
    assert parse_nsync_counts(NSYNC_PREVIEW_STDOUT, dryrun=True) == (10, 50)
    # 실행 파서로 읽으면 미리보기 출력에는 반영된 것이 없다 -- 모름
    assert parse_nsync_counts(NSYNC_PREVIEW_STDOUT) == (None, None)


def test_nsync_skipped_dst_only_is_excluded_from_items():
    # skipped-dst-only는 "건드리지 않은 항목"이라 처리량이 아니다(설계 부록 A).
    # 포함하면 2가 아니라 101이 나온다 -- 값을 크게 벌려 배제를 확실히 판별한다.
    out = (f"{TS}Planned actions: copy=2 mkdir=0 symlink-update=0 "
           "meta-update=0 remove=0 skipped-dst-only=99\n")
    assert parse_nsync_counts(out, dryrun=True)[0] == 2


def test_nsync_copied_bytes_is_not_an_action():
    # Completed 줄의 copied-bytes 는 바이트다 -- 항목 합에 섞이면 1 GiB 복사가 「10억 개 반영」이 된다.
    out = f"{TS}Completed actions: copy=2 mkdir=1 symlink-update=0 meta-update=0 remove=0 copied-bytes=1073741824\n"
    assert parse_nsync_counts(out) == (3, 1073741824)


def test_nsync_unknown_future_action_key_is_counted():
    # "이름=정수 쌍을 전부 합하되 skip 키만 뺀다"이므로 nsync가 새 액션 종류를
    # 추가해도 파서 수정 없이 따라간다(설계 부록 A) -- 화이트리스트였다면 1이 된다
    assert parse_nsync_counts(f"{TS}Planned actions: copy=1 newaction=4 skipped-dst-only=0\n", dryrun=True)[0] == 5
    assert parse_nsync_counts(f"{TS}Completed actions: copy=1 newaction=4 copied-bytes=0\n")[0] == 5


def test_nsync_volume_unit_conversion_is_rounded():
    # nsync는 진행 줄에 원시 정수 바이트를 안 찍고 소수 3자리 사람용 표기만 낸다 --
    # 1024 기반 환산 후 반올림(절사가 아니다): 1.234*1024^3 = 1324997410.816
    out = f"{TS}Progress 100.0% batch 1/1 actions=1 copied-volume=1.234 GiB\n"
    assert parse_nsync_counts(out)[1] == 1324997411


def test_nsync_unknown_volume_unit_is_none_not_earlier_match():
    # 마지막 볼륨 표기의 단위를 모르면 그 값이 정답 후보였던 것이므로 None이다 --
    # 이전(오래된) 매치로 조용히 물러나면 총량이 과소 보고된다
    out = (f"{TS}Progress 50.0% batch 1/2 actions=1 copied-volume=50.000 B\n"
           f"{TS}Progress 100.0% batch 2/2 actions=1 copied-volume=7.000 PiB\n")
    assert parse_nsync_counts(out)[1] is None


def test_nsync_volume_must_be_on_a_progress_line():
    # d192 앵커: 진행 줄이 아닌 곳의 같은 표기는 바이트 출처가 아니다.
    assert parse_nsync_counts(f"{TS}Requested target: /x copied-volume=9.000 GiB\n") == (None, None)


def test_nsync_last_match_wins_for_items_and_bytes():
    # 실측 캡처는 진행/최종 값이 같아(10/50) 순서 규칙을 증명하지 못한다 --
    # 값을 달리한 합성 변형으로 "마지막 매치가 최종이다"를 고정한다(설계 §3)
    preview = (f"{TS}Planned actions: copy=1 mkdir=1 skipped-dst-only=0\n"
               f"{TS}Progress 50.0% batch 1/2 planned-volume=1.000 KiB\n"
               f"{TS}Planned actions: copy=7 mkdir=3 skipped-dst-only=0\n"
               f"{TS}Progress 100.0% batch 2/2 planned-volume=2.000 KiB\n")
    assert parse_nsync_counts(preview, dryrun=True) == (10, 2048)
    execution = (f"{TS}Completed actions: copy=1 mkdir=0 copied-bytes=5\n"
                 f"{TS}Completed actions: copy=4 mkdir=2 copied-bytes=70\n")
    assert parse_nsync_counts(execution) == (6, 70)


def test_nsync_empty_and_irrelevant_stdout():
    # 조기 실패 등으로 요약이 없으면 값만 null(설계 §4 fail-soft)
    assert parse_nsync_counts("") == (None, None)
    assert parse_nsync_counts(f"{TS}Execution completed successfully\n") == (None, None)
    # dsync 출력을 잘못 넣어도 nsync 형식이 아니므로 조용히 null이다
    assert parse_nsync_counts(DSYNC_STDOUT) == (None, None)
    assert parse_nsync_counts(DSYNC_STDOUT, dryrun=True) == (None, None)


# ---- parse_rm_counts ----

def test_rm_real_capture_removed_items_bytes_always_none():
    # "Removing 3 items"(시작 줄)와 "Walked 3 items"는 배제, "Removed 3 items in"만
    assert parse_rm_counts(DRM_STDOUT) == (3, None)


def test_rm_last_match_wins():
    out = (f"{TS}Removed 3 items in 0.1 seconds (30.0 items/sec)\n"
           f"{TS}Removed 7 items in 0.1 seconds (70.0 items/sec)\n")
    assert parse_rm_counts(out) == (7, None)


def test_rm_progress_line_alone_is_not_matched():
    # "Removing N items"(시작 줄)·"Removed N items (x%) in ..."(진행 줄)만 있으면 완료 요약이 없는 것 -- 배제되어야 한다.
    # DRM_STDOUT은 진행/완료 값이 같아(3/3) 이 배제를 증명 못하므로 값이 다른 단독 줄로 고정한다.
    assert parse_rm_counts(f"{TS}Removing 100 items\n") == (None, None)
    progress = f"{TS}Removed 50 items (50.00%) in 1.000 secs (50.000 items/sec) 1 secs remaining ...\n"
    assert parse_rm_counts(progress) == (None, None)
    # 줄 머리 앵커(d192): 접두사 없는 줄·줄 한가운데는 요약이 아니다
    assert parse_rm_counts("Removed 7 items in 0.1 seconds (1.0 items/sec)\n") == (None, None)


def test_rm_summary_counts_successes_not_attempts():
    # 포크 7a3189c: 남은 항목이 있으면 "Removed" 는 실제로 지운 수이고 실패는 다음 줄에 따로(실측 drm_exec_partial 요약).
    out = (f"{TS}Removing 9 items\n"
           f"{TS}Removed 4 items in 0.001 seconds (3975.621 items/sec)\n"
           f"{TS}[0] [/release/mpifileutils/src/common/mfu_flist_remove.c:761] ERROR: Remove failed for 5 of 9 items\n")
    assert parse_rm_counts(out) == (4, None)
    detail = parse_tool_detail("drm", out, "", False)
    assert (detail["removed"], detail["remove_failed"]) == (4, 5)


def test_rm_empty_and_walked_only():
    assert parse_rm_counts("") == (None, None)
    assert parse_rm_counts("[ts] Walked 10 items in 0.001 seconds\n") == (None, None)


# ---- parse_rm_dryrun_counts (2026-10-12) ----
# 실측 캡처(테스트베드, 잡 adb1ce59 preview/stdout.log, `drm --dryrun /cephfs/managed/dms_test/suppgrp`): 삭제 요약
# ("Removed N items")이 없고 walk 요약 + 지울 항목 목록(mfu_flist_print)뿐이다. 같은 잡 실행은 "Removed 6 items" --
# walk 6 과 같다. 예전 파서는 이 출력에서 null 이라 rm 미리보기가 늘 「삭제 대상 모름」이었다.
DRM_DRYRUN_STDOUT = """\
[2026-10-07T18:43:25] Walking /cephfs/managed/dms_test/suppgrp
[2026-10-07T18:43:25] Walked 6 items in 0.004 secs (1523.069 items/sec) ...
[2026-10-07T18:43:25] Walked 6 items in 0.006 seconds (1050.294 items/sec)

Type=DIR File=/cephfs/managed/dms_test/suppgrp
Type=DIR File=/cephfs/managed/dms_test/suppgrp/ro
Type=DIR File=/cephfs/managed/dms_test/suppgrp/rw
Type=DIR File=/cephfs/managed/dms_test/suppgrp/rw/alice-revoke-1008b
Type=REG File=/cephfs/managed/dms_test/suppgrp/rw/alice-revoke-1008b/p1.txt
Type=REG File=/cephfs/managed/dms_test/suppgrp/ro/p.txt

"""


def test_rm_dryrun_real_capture_uses_walk_items_and_null_bytes():
    from dms_job_runner.parsers import parse_rm_dryrun_counts
    assert parse_rm_counts(DRM_DRYRUN_STDOUT) == (None, None)          # 실행 파서는 여전히 모름(지어내지 않는다)
    assert parse_rm_dryrun_counts(DRM_DRYRUN_STDOUT) == (6, None)      # walk 6 = 실행의 Removed 6


def test_rm_dryrun_counts_the_walk_not_the_truncated_list():
    # 큰 트리: mfu_flist_print 는 앞 10개·뒤 10개만 찍고 "<snip>" -- 목록 줄 수(20)가 아니라 walk 수다.
    from dms_job_runner.parsers import parse_rm_dryrun_counts
    listing = "\n".join(f"Type=REG File=/t/f{i}" for i in range(10))
    out = ("[2026-10-12T01:00:00] Walked 12345 items in 1.000 seconds (12345.000 items/sec)\n\n"
           f"{listing}\n\n<snip>\n\n{listing}\n\n")
    assert parse_rm_dryrun_counts(out) == (12345, None)


def test_rm_dryrun_ignores_progress_and_quiet_output():
    from dms_job_runner.parsers import parse_rm_dryrun_counts
    # 진행 줄("secs ...")만 있으면 최종 walk 가 아니다 -- 모름
    assert parse_rm_dryrun_counts("[2026-10-12T01:00:00] Walked 5 items in 0.1 secs (50 items/sec) ...\n") == (None, None)
    # quiet(-q) 출력: walk 요약이 통째로 없고 목록만 -- 지어내지 않는다(그래서 미리보기는 --quiet 를 싣지 않는다)
    assert parse_rm_dryrun_counts("\nType=REG File=/t/a\n\n") == (None, None)
    assert parse_rm_dryrun_counts("") == (None, None)
    assert parse_rm_dryrun_counts(None) == (None, None)


def test_rm_dryrun_never_reads_a_removal_summary_from_file_names():
    # 리뷰 재현(2026-10-12): dry-run 은 "Removed N items" 를 절대 찍지 않는다(mfu_flist_unlink 를 부르지 않는다) --
    # 예전 「삭제 요약 우선」 분기는 목록에 찍힌 파일 이름만 잡아 walk 25만을 1 로 덮었다.
    from dms_job_runner.parsers import parse_rm_dryrun_counts
    out = ("[2026-10-12T01:00:00] Walked 250000 items in 20.006 seconds (12496.251 items/sec)\n\n"
           "Type=REG File=/c/u/logs/Removed 1 items.txt\n\n")
    assert parse_rm_dryrun_counts(out) == (250000, None)


def test_walk_count_is_anchored_to_the_log_prefix_not_path_text():
    # 리뷰 재현(2026-10-12): walk 요약보다 먼저 찍히는 "Walking <경로>" 줄과 walk 오류 줄의 경로에 같은 문구가 있으면
    # 그 숫자가 첫 매치였다(경로는 공백·괄호 허용). dsync 미리보기도 같은 정규식이라 함께 막힌다.
    from dms_job_runner.parsers import parse_rm_dryrun_counts, parse_sync_dryrun_counts
    walking = "[2026-10-12T01:00:00] Walking /cephfs/managed/u/Walked 1 items in 0.0 seconds (old)\n"
    error = ("[2026-10-12T01:00:00] [0] [mfu_flist_walk.c:314] ERROR: Failed to open directory with opendir: "
             "'/cephfs/managed/u/Walked 2 items in 1.0 seconds (a)' (errno=13 Permission denied)\n")
    real = "[2026-10-12T01:00:20] Walked 250000 items in 20.006 seconds (12496.251 items/sec)\n"
    assert parse_rm_dryrun_counts(walking + error + real) == (250000, None)
    assert parse_sync_dryrun_counts(walking + error + real) == (250000, None)


def test_newline_in_a_name_cannot_inject_a_walk_count():
    # 2026-10-12 보안 검토: 경로 이름에는 줄바꿈이 들어갈 수 있어, 이름에 "\n[ts] Walked 1 items in 0.0 seconds (" 를
    # 심으면 줄 머리부터 맞는 가짜 줄이 진짜 요약 앞(「Walking」·walk 오류 줄)이나 뒤(drm 목록)에 생긴다. 가짜는 더할 수만
    # 있으므로 walk 횟수보다 많이 보이면 모름 -- drm 은 정확히 1, dsync 는 2 이하.
    from dms_job_runner.parsers import parse_rm_dryrun_counts, parse_sync_dryrun_counts
    fake = "[2026-10-12T01:00:00] Walked 1 items in 0.0 seconds (x)"
    real = "[2026-10-12T01:00:20] Walked 250000 items in 20.006 seconds (12496.251 items/sec)\n"
    before = f"[2026-10-12T01:00:00] Walking /cephfs/managed/u/evil\n{fake}\n"          # 대상 이름에 심은 것
    after = f"\nType=DIR File=/cephfs/managed/u/evil\n{fake}\n\n"                         # 목록의 하위 이름에 심은 것
    assert parse_rm_dryrun_counts(before + real) == (None, None)
    assert parse_rm_dryrun_counts(real + after) == (None, None)
    assert parse_rm_dryrun_counts(real) == (250000, None)                                 # 깨끗하면 그대로
    dst = "[2026-10-12T01:00:21] Walked 0 items in 0.001 seconds (0.000 items/sec)\n"
    assert parse_sync_dryrun_counts(before + real + dst) == (None, None)                  # 소스 앞 주입 → 3개 → 모름
    assert parse_sync_dryrun_counts(real + dst) == (250000, None)


def test_rm_dryrun_zero_means_the_target_was_not_found_not_an_empty_dir():
    # walk 는 대상이 있으면 대상 자신을 센다 -- 빈 디렉터리는 1, 0 은 대상 lstat 실패(경로 없음·권한)다. 값은 사실 그대로
    # 0(지울 것 없음)이고 포탈이 rm 의 0 을 「대상 경로를 찾지 못함」으로 따로 말한다(null 모름과도 다르다).
    from dms_job_runner.parsers import parse_rm_dryrun_counts
    missing = ("[2026-10-12T01:00:00] [0] [mfu_flist_walk.c:402] ERROR: Failed to stat: '/cephfs/managed/x' "
               "(errno=2 No such file or directory)\n"
               "[2026-10-12T01:00:00] Walked 0 items in 0.001 seconds (0.000 items/sec)\n")
    empty_dir = "[2026-10-12T01:00:00] Walked 1 items in 0.001 seconds (1000.000 items/sec)\n\nType=DIR File=/t\n\n"
    assert parse_rm_dryrun_counts(missing) == (0, None)
    assert parse_rm_dryrun_counts(empty_dir) == (1, None)


# ---- parse_scan_counts ----

def _write_report(tmp_path, payload) -> str:
    path = tmp_path / "dscan-report.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return str(path)


def test_scan_real_report_entries_and_total_bytes(tmp_path):
    # 실 사용량 = 첫 온전한 히스토그램(mtime 우선)의 bytes 합 = 50
    assert parse_scan_counts(_write_report(tmp_path, DSCAN_REPORT)) == (10, 50)


def test_scan_bytes_prefers_mtime_histogram(tmp_path):
    # mtime 이 온전하면 atime 과 합이 달라도 mtime 이 출처다(우선순위 고정 --
    # 흔들리면 (3) 사용량 분석의 시계열이 스캔마다 다른 출처로 요동한다).
    report = {**DSCAN_REPORT,
              "time_histograms": {"atime": _age_buckets([999, 0, 0]),
                                  "mtime": _age_buckets([30, 20, 0])}}
    assert parse_scan_counts(_write_report(tmp_path, report)) == (10, 50)


def test_scan_bytes_falls_back_when_histogram_tainted(tmp_path):
    # 버킷 하나라도 bytes 가 수상하면(비int·bool·음수) 그 히스토그램 전체가
    # 부적격 -- 부분합(과소 보고) 대신 다음 후보(atime)로 넘어간다.
    for bad in ("x", True, -1, 1.5, None):
        tainted = _age_buckets([30, 20, 0])
        tainted[1]["bytes"] = bad
        report = {**DSCAN_REPORT,
                  "time_histograms": {"mtime": tainted,
                                      "atime": _age_buckets([50, 0, 0])}}
        assert parse_scan_counts(_write_report(tmp_path, report)) == (10, 50)


def test_scan_bytes_none_when_no_usable_histogram(tmp_path):
    # 전 히스토그램 부재·빈 목록·전부 오염 -> bytes 는 None(모름), entries 는 산다
    for hists in (None, "oops", {}, {"atime": [], "mtime": []},
                  {"mtime": [{"bucket": "[0d,1d]", "bytes": "x"}]}):
        report = {**DSCAN_REPORT, "time_histograms": hists}
        assert parse_scan_counts(_write_report(tmp_path, report)) == (10, None)


def test_scan_entries_and_bytes_decoupled(tmp_path):
    # summary 가 깨져도 히스토그램이 온전하면 bytes 는 산다(반대도 성립) --
    # 한쪽 오염이 다른 쪽 값을 끌고 내려가면 조용한 정보 손실이다.
    report = {**DSCAN_REPORT, "summary": {"total_entries": "10"}}
    assert parse_scan_counts(_write_report(tmp_path, report)) == (None, 50)


def test_scan_missing_report_file(tmp_path):
    # 도구 실패로 리포트가 안 생겨도 파서는 예외 대신 None(설계 §4)
    assert parse_scan_counts(str(tmp_path / "nope.json")) == (None, None)


def test_scan_corrupt_json(tmp_path):
    assert parse_scan_counts(_write_report(tmp_path, "{broken")) == (None, None)


def test_scan_pathological_nesting_does_not_raise(tmp_path):
    # 병적으로 깊게 중첩된 JSON은 RecursionError를 던진다 -- ValueError가 아니라서
    # 좁은 except로는 새어 나간다. 계약은 "절대 예외 없음"(설계 §4)이므로 이것도
    # None으로 강등되어야 한다.
    deep = "[" * 100000 + "]" * 100000
    assert parse_scan_counts(_write_report(tmp_path, deep)) == (None, None)


def test_scan_wrong_shapes_and_types(tmp_path):
    # 승격 경로 _as_count(data_jobs.py)와 같은 원칙: bool은 int의 서브클래스라
    # 명시 배제, 음수는 계수로 무의미 -- 여기서 먼저 거르면 summary가 애초에 깨끗하다
    for payload in ([1, 2],                                   # 리포트가 dict 아님
                    {"summary": "oops"},                      # summary 비 dict
                    {"summary": {}},                          # total_entries 없음
                    {"summary": {"total_entries": "10"}},     # 비 int
                    {"summary": {"total_entries": True}},     # bool
                    {"summary": {"total_entries": -1}}):      # 음수
        assert parse_scan_counts(_write_report(tmp_path, payload)) == (None, None)


def test_parsers_accept_none_stdout():
    # 계약은 "절대 예외 없음"(fail-soft, 이 모듈 docstring)이고 구현은
    # `stdout or ""` 로 None 까지 삼키겠다고 반쪽 약속해 왔다(슬라이스 15) --
    # 검증은 빈 문자열뿐이었다(BACKLOG §2.5). None 은 "" 와 다른 입력이다:
    # subprocess 캡처가 어긋난 병적 경로에서 파서가 TypeError 로 잡을 죽이면
    # 계약 위반이고, 그 예외는 stepper _finalize 앞이라 잡이 Executing 에 박힌다.
    assert parse_sync_counts(None) == (None, None)
    assert parse_dsync_counts(None) == (None, None)
    assert parse_nsync_counts(None) == (None, None)
    assert parse_nsync_counts(None, dryrun=True) == (None, None)
    assert parse_rm_counts(None) == (None, None)
    # 상세 파서도 같다: None 은 빈 출력 -- 상세 키는 그 도구의 「아는 0」(dsync·drm 오류 줄 수)뿐이다.
    assert parse_tool_detail("nsync", None, None, False) == {}
    assert parse_tool_detail("drm", None, None, False) == {"tool_errors": 0}
    assert parse_tool_detail("dscan", None, None, False) == {}


def test_pathological_numbers_do_not_raise_or_overflow():
    # int() 는 4300자 넘는 숫자 문자열에 ValueError -- 계약상 예외 금지. int64 를 넘는 값은 BIGINT 승격 컬럼을 깨므로 모름.
    huge = "9" * 5000
    assert parse_sync_counts(f"{TS}Seconds: 0.1\n{TS}Items: {huge}\n") == (None, None)
    assert parse_rm_counts(f"{TS}Removed {2 ** 63} items in 0.1 seconds (1.0 items/sec)\n") == (None, None)
    assert parse_rm_counts(f"{TS}Removed {2 ** 63 - 1} items in 0.1 seconds (1.0 items/sec)\n") == (2 ** 63 - 1, None)
    assert parse_nsync_counts(f"{TS}Planned actions: copy={huge} mkdir=1\n", dryrun=True) == (None, None)
    assert parse_tool_detail("nsync", f"{TS}Completed actions: copy={huge} copied-bytes=1\n", "", False) == {}



# ---- parse_sync_dryrun_counts (2026-09-17) ----
# 실측 캡처(테스트베드 d135, dsync --dryrun): 복사 요약("Items: N"·"(N bytes)")이 없고
# 소스·목적지 walk 요약만 있다. 진행 줄("secs ...")은 배제, 최종 줄("seconds (")의 첫
# 매치가 소스 항목 수. 목적지가 없으면 stat 실패 + "Walked 0 items" 가 뒤따른다.
DSYNC_DRYRUN_STDOUT = """\
[2026-09-16T23:00:22] Overriding destination ownership: uid=set gid=set
[2026-09-16T23:00:22] Walking source path
[2026-09-16T23:00:22] Walking /cephfs/managed/ldap-e2e/group-shared
[2026-09-16T23:00:22] Walked 2 items in 0.003 secs (780.071 items/sec) ...
[2026-09-16T23:00:22] Walked 2 items in 0.004 seconds (453.712 items/sec)
[2026-09-16T23:00:22] Walking destination path
[2026-09-16T23:00:22] Walking /cephfs/managed/ldap-e2e/dest/def-d135a
[2026-09-16T23:00:22] [0] [mfu_flist_walk.c:516] ERROR: Failed to stat: '/cephfs/managed/ldap-e2e/dest/def-d135a' (errno=2 No such file or directory)
[2026-09-16T23:00:22] Walked 0 items in 0.001 secs (0.000 items/sec) ...
[2026-09-16T23:00:22] Walked 0 items in 0.001 seconds (0.000 items/sec)
[2026-09-16T23:00:22] Started   : Sep-16-2026, 23:00:22
[2026-09-16T23:00:22] Completed : Sep-16-2026, 23:00:22
[2026-09-16T23:00:22] Seconds   : 0.000
[2026-09-16T23:00:22] Items     : 0
[2026-09-16T23:00:22] Item Rate : 0 items in 0.000193 seconds (0.000000 items/sec)
[2026-09-16T23:00:22] Completed updating timestamps
[2026-09-16T23:00:22] Completed sync
"""


def test_sync_dryrun_uses_source_walk_items_and_null_bytes():
    from dms_job_runner.parsers import parse_sync_dryrun_counts
    assert parse_sync_counts(DSYNC_DRYRUN_STDOUT) == (None, None)     # 실행 파서는 여전히 모름
    assert parse_sync_dryrun_counts(DSYNC_DRYRUN_STDOUT) == (2, None)  # 소스 walk = 2, 목적지 0 아님


def test_sync_dryrun_prefers_copy_summary_when_present():
    # 도구가 언젠가 dry-run 에서도 복사 요약을 찍으면 실행 파서와 같은 값을 쓴다.
    from dms_job_runner.parsers import parse_sync_dryrun_counts
    assert parse_sync_dryrun_counts(DSYNC_STDOUT) == (10, 50)


def test_sync_dryrun_without_any_walk_line_is_null():
    from dms_job_runner.parsers import parse_sync_dryrun_counts
    assert parse_sync_dryrun_counts("[ts] Completed sync\n") == (None, None)
    # 진행 줄("secs ...")만 있고 최종 줄이 없으면 매치하지 않는다
    assert parse_sync_dryrun_counts("[2026-10-12T01:00:00] Walked 5 items in 0.1 secs (50 items/sec) ...\n") == (None, None)


def test_scan_counts_survive_non_utf8_report_path(tmp_path):
    # 2026-10-20 프로덕션: dscan 은 이름 바이트를 그대로 JSON 에 쓴다 -- CP949 「다운로드」가 broken_paths 에 들면 strict
    # 디코드가 터져 files/bytes 가 조용히 null 이었다. 쓰는 값은 숫자뿐이라 replace 로 읽어 값을 지킨다.
    hist = [{"bucket": "[0d,1d]", "min_age_days": 0, "max_age_days": 1, "bytes": 123}]
    head = json.dumps({"summary": {"total_entries": 510971}, "time_histograms": {"mtime": hist},
                       "broken_paths_total": 1})[:-1]
    p = tmp_path / "dscan-report.json"
    p.write_bytes(head.encode() + b', "broken_paths": [{"path": "/x/\xb4\xd9\xbf\xee/a.exe",'
                  b' "reasons": ["abnormal_time"]}]}')
    assert parse_scan_counts(str(p)) == (510971, 123)
