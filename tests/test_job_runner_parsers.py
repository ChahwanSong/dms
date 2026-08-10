"""parsers.py 단위 테스트. 픽스처는 테스트베드 실 잡의 캡처 출력을 그대로 고정한다
(잡 60d24700 dsync stdout, drm stdout, dscan-report.json 실 스키마, 잡 abc0b559
nsync stdout -- 설계 부록 A) -- 파서가 "실제 도구가 찍는 것"을 파싱함을 픽스처
수준에서 보증한다(설계 §6)."""
import json

from dms_job_runner.parsers import (parse_nsync_counts, parse_rm_counts,
                                    parse_scan_counts, parse_sync_counts)

# 실측 dsync stdout(잡 60d24700, 파싱 관련 줄 전체). 같은 stdout에 walk 단계의
# 패딩 요약("Items     : 0"), 복사 단계 중간 요약(Items: 10 + per-file Data),
# Copy data/Copy rate, 최종 블록(Items/Data/Rate)이 전부 공존한다 -- 파서는 이
# 전체에서 최종 블록만 골라내야 한다(설계 §1).
DSYNC_STDOUT = """\
[2026-08-04T02:14:12] Walked 10 items in 0.001 seconds (15463.025 items/sec)
[2026-08-04T02:14:12] Started   : Aug-04-2026, 02:14:12
[2026-08-04T02:14:12] Items     : 0
[2026-08-04T02:14:12] Copying items to destination
[2026-08-04T02:14:12] Items: 10
[2026-08-04T02:14:12]   Directories: 3
[2026-08-04T02:14:12]   Files: 7
[2026-08-04T02:14:12] Data: 50.000 B (7.000 B per file)
[2026-08-04T02:14:12] Copy data: 50.000 B (50 bytes)
[2026-08-04T02:14:12] Copy rate: 3.284 KiB/s (50 bytes in 0.015 seconds)
[2026-08-04T02:14:12] Items: 10
[2026-08-04T02:14:12]   Directories: 3
[2026-08-04T02:14:12]   Files: 7
[2026-08-04T02:14:12]   Links: 0
[2026-08-04T02:14:12] Data: 50.000 B (50 bytes)
[2026-08-04T02:14:12] Rate: 0.991 KiB/s (050 bytes in 0.049 seconds)
[2026-08-04T02:14:12] Completed sync
"""

# 실측 캡처는 중간·최종 값이 같아(10/50) "마지막 매치가 이긴다"를 증명하지 못한다.
# 값을 달리한 합성 변형으로 순서 규칙을 고정한다(설계 §3: 마지막 매치 = 최종 블록).
DSYNC_STDOUT_MID_DIFFERS = """\
[2026-08-04T02:14:12] Items: 4
[2026-08-04T02:14:12] Copy data: 20.000 B (20 bytes)
[2026-08-04T02:14:12] Items: 10
[2026-08-04T02:14:12] Data: 50.000 B (50 bytes)
"""

# 실측 nsync stdout(잡 abc0b559 실행 단계, 설계 부록 A). nsync는 stock mpifileutils가
# 아니라 역할 기반(src/dst rank) 별도 도구라 "Items:"/"(N bytes)"를 전혀 찍지 않는다 --
# 진행 줄의 actions=9는 planned 10과 어긋나므로(배치 병합) 쓰지 않고, 사람용 표기
# copied-volume=50.000 B가 바이트의 유일한 출처다(설계 부록 A).
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

# 같은 잡의 프리뷰(dryrun) 단계. "Planned actions:" 줄은 실행과 완전히 동일하고
# 진행 줄 어휘만 다르다(planned-actions/planned-copy-files/planned-volume) --
# runner에 phase 분기가 없으므로 두 단계가 같은 값을 내야 한다(설계 부록 A).
NSYNC_PREVIEW_STDOUT = """\
[2026-08-10T01:28:19] Progress 100.0% batch 1/1 planned-actions=10 planned-copy-files=7 planned-volume=50.000 B
[2026-08-10T01:28:19] nsync Phase 5 planner dryrun complete
[2026-08-10T01:28:19] Options: dryrun=1 batch-files=0 delete=0 contents=0 role-mode=map imbalance-threshold=3.00
[2026-08-10T01:28:19] Metadata diff summary: only-src=10 only-dst=0 common=0 changed=0
[2026-08-10T01:28:19] Planned actions: copy=7 mkdir=3 symlink-update=0 meta-update=0 remove=0 skipped-dst-only=0
"""

# 실측 drm stdout 요지: "Removed N items"가 "Walked N items" 줄과 혼재하고
# bytes는 보고하지 않는다(설계 §1).
DRM_STDOUT = """\
[2026-08-04T02:31:08] Walked 1 items in 0.001 seconds (1035.197 items/sec)
[2026-08-04T02:31:08] Removing 1 items
[2026-08-04T02:31:08] Removed 1 items (0.482 items/sec) in 2.077 seconds
"""

# 실측 dscan-report.json 스키마(잡 8464cdd4). 파서는 summary.total_entries만 읽지만
# top-level 키를 실 스키마대로 유지해 픽스처가 실물을 대표하게 한다.
DSCAN_REPORT = {
    "directory": "/cephfs/dms/smoke-src",
    "generated_at_epoch": 1754273652,
    "top_k": 10,
    "thresholds": {},
    "summary": {"total_entries": 10, "total_files": 7, "total_directories": 3,
                "total_symlinks": 0, "total_other": 0},
    "file_size_histogram": [],
}


# ---- parse_sync_counts ----

def test_sync_real_capture_final_items_and_bytes():
    # 최종 블록의 Items: 10 / Data: (50 bytes) -- 중간 요약·rate 줄이 있어도
    assert parse_sync_counts(DSYNC_STDOUT) == (10, 50)


def test_sync_last_match_wins_over_mid_copy_summary():
    assert parse_sync_counts(DSYNC_STDOUT_MID_DIFFERS) == (10, 50)


def test_sync_padded_walk_items_is_not_matched():
    # walk 단계 "Items     : 0"(콜론 앞 패딩)은 "Items: " 형태가 아니라 자연 배제 --
    # 이것만 있으면 최종 요약이 없는 것이므로 None(설계 §3)
    assert parse_sync_counts("[ts] Items     : 0\n") == (None, None)


def test_sync_bytes_in_rate_line_is_not_matched():
    # "(50 bytes in 0.015 seconds)"는 "bytes" 뒤에 닫는 괄호가 바로 오지 않아
    # 배제된다 -- 전송률이 총량으로 새면 안 된다(설계 §3)
    out = "[ts] Copy rate: 3.284 KiB/s (50 bytes in 0.015 seconds)\n"
    assert parse_sync_counts(out) == (None, None)


def test_sync_bytes_embedded_mid_line_is_not_matched():
    # 요약 줄은 항상 "(N bytes)"로 끝난다 -- 줄 중간에 박힌 같은 모양(예: 경로
    # 이름에 든 "(999 bytes)")이 총량 행세를 하면 안 된다. Items 쪽과 같은 $ 앵커.
    assert parse_sync_counts("[ts] copying /backup (999 bytes)/f.bin to x\n") == (None, None)


def test_sync_items_with_trailing_text_is_not_matched():
    # "Items: 10 (done)"처럼 숫자 뒤에 내용이 붙으면 요약 줄이 아니다 -- $ 앵커가 배제
    assert parse_sync_counts("[ts] Items: 10 (done)\n") == (None, None)


def test_sync_empty_and_irrelevant_stdout():
    # --quiet 억제·조기 실패 등으로 요약이 없으면 값만 null(설계 §4 fail-soft)
    assert parse_sync_counts("") == (None, None)
    assert parse_sync_counts("no summary here\nCompleted sync\n") == (None, None)


# ---- parse_nsync_counts ----

def test_nsync_real_execution_capture_items_and_bytes():
    # "Planned actions:" 합계 copy=7+mkdir=3(+0들) = 10, copied-volume=50.000 B = 50 --
    # 같은 트리를 dsync로 돌린 캡처의 (10, 50)과 정확히 일치한다(설계 부록 A)
    assert parse_nsync_counts(NSYNC_EXEC_STDOUT) == (10, 50)


def test_nsync_preview_capture_matches_execution():
    # runner에는 phase 분기가 없다(설계 §3) -- 프리뷰도 같은 파서를 타므로
    # dryrun 출력에서 같은 값이 나와야 한다. 어휘가 다른 진행 줄
    # (planned-volume=)도 바이트 출처로 인정된다.
    assert parse_nsync_counts(NSYNC_PREVIEW_STDOUT) == (10, 50)


def test_nsync_skipped_dst_only_is_excluded_from_items():
    # skipped-dst-only는 "건드리지 않은 항목"이라 처리량이 아니다(설계 부록 A).
    # 포함하면 2가 아니라 101이 나온다 -- 값을 크게 벌려 배제를 확실히 판별한다.
    out = ("[ts] Planned actions: copy=2 mkdir=0 symlink-update=0 "
           "meta-update=0 remove=0 skipped-dst-only=99\n")
    assert parse_nsync_counts(out)[0] == 2


def test_nsync_unknown_future_action_key_is_counted():
    # "이름=정수 쌍을 전부 합하되 skip 키만 뺀다"이므로 nsync가 새 액션 종류를
    # 추가해도 파서 수정 없이 따라간다(설계 부록 A) -- 화이트리스트였다면 1이 된다
    assert parse_nsync_counts("[ts] Planned actions: copy=1 newaction=4 skipped-dst-only=0\n")[0] == 5


def test_nsync_volume_unit_conversion_is_rounded():
    # nsync는 원시 정수 바이트를 안 찍고 소수 3자리 사람용 표기만 낸다 --
    # 1024 기반 환산 후 반올림(절사가 아니다): 1.234*1024^3 = 1324997410.816
    out = "[ts] Progress 100.0% batch 1/1 actions=1 copied-volume=1.234 GiB\n"
    assert parse_nsync_counts(out)[1] == 1324997411


def test_nsync_unknown_volume_unit_is_none_not_earlier_match():
    # 마지막 볼륨 표기의 단위를 모르면 그 값이 정답 후보였던 것이므로 None이다 --
    # 이전(오래된) 매치로 조용히 물러나면 총량이 과소 보고된다
    out = ("[ts] copied-volume=50.000 B\n"
           "[ts] copied-volume=7.000 PiB\n")
    assert parse_nsync_counts(out)[1] is None


def test_nsync_last_match_wins_for_items_and_bytes():
    # 실측 캡처는 진행/최종 값이 같아(10/50) 순서 규칙을 증명하지 못한다 --
    # 값을 달리한 합성 변형으로 "마지막 매치가 최종이다"를 고정한다(설계 §3)
    out = ("[ts] Planned actions: copy=1 mkdir=1 skipped-dst-only=0\n"
           "[ts] planned-volume=1.000 KiB\n"
           "[ts] Planned actions: copy=7 mkdir=3 skipped-dst-only=0\n"
           "[ts] copied-volume=2.000 KiB\n")
    assert parse_nsync_counts(out) == (10, 2048)


def test_nsync_empty_and_irrelevant_stdout():
    # 조기 실패 등으로 요약이 없으면 값만 null(설계 §4 fail-soft)
    assert parse_nsync_counts("") == (None, None)
    assert parse_nsync_counts("[ts] Execution completed successfully\n") == (None, None)
    # dsync 출력을 잘못 넣어도 nsync 형식이 아니므로 조용히 null이다
    assert parse_nsync_counts(DSYNC_STDOUT) == (None, None)


# ---- parse_rm_counts ----

def test_rm_real_capture_removed_items_bytes_always_none():
    # "Removing 1 items"(진행 줄)와 "Walked 1 items"는 배제, "Removed 1 items"만
    assert parse_rm_counts(DRM_STDOUT) == (1, None)


def test_rm_last_match_wins():
    out = "Removed 3 items\nRemoved 7 items in 0.1 seconds\n"
    assert parse_rm_counts(out) == (7, None)


def test_rm_progress_line_alone_is_not_matched():
    # "Removing N items"(진행 줄)만 있으면 완료 요약이 없는 것 -- 배제되어야 한다.
    # DRM_STDOUT은 진행/완료 값이 같아(1/1) 이 배제를 증명 못하므로 값이 다른
    # 단독 진행 줄로 고정한다(sync의 MID_DIFFERS와 같은 이유).
    assert parse_rm_counts("[ts] Removing 100 items\n") == (None, None)


def test_rm_empty_and_walked_only():
    assert parse_rm_counts("") == (None, None)
    assert parse_rm_counts("[ts] Walked 10 items in 0.001 seconds\n") == (None, None)


# ---- parse_scan_counts ----

def _write_report(tmp_path, payload) -> str:
    path = tmp_path / "dscan-report.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return str(path)


def test_scan_real_report_total_entries(tmp_path):
    assert parse_scan_counts(_write_report(tmp_path, DSCAN_REPORT)) == (10, None)


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
