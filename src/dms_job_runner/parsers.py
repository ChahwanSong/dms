"""잡 도구 출력에서 항목수·바이트수를 뽑는 순수 파서(슬라이스 15 설계 §3).

네 함수 모두 (files, bytes) 튜플을 돌려주고, 실패는 해당 값 None -- 예외를 던지지
않는다(설계 §4 fail-soft: 파싱이 잡을 죽이는 경로는 없다). files의 의미는
"항목(items)"으로 통일한다(설계 §2.2): dsync = 최종 Items(디렉터리+파일+링크),
nsync = Planned actions 합계(설계 부록 A -- 같은 트리에서 dsync Items와 일치),
drm = Removed N items(미리보기는 walk 항목 수 -- parse_rm_dryrun_counts), dscan = summary.total_entries -- drm이 파일/디렉터리 구분
없이 items만 내므로 도구 교차 일관성이 가장 좋다. parse_scan_counts만 파일을
읽는다(읽기 전용) -- 나머지는 문자열 입력만 받는 순수 함수다."""
import json
import re

# 최종 요약의 "Items: N"만 -- walk 단계의 "Items     : 0"(콜론 앞 패딩)은 형태가
# 달라 자연 배제된다. 복사 단계 중간 요약과 최종 블록이 둘 다 매치되므로
# 마지막 매치(=최종 블록)를 쓴다(설계 §1: 마지막 매치가 최종 블록이다).
_SYNC_ITEMS = re.compile(r"Items: (\d+)\s*$", re.MULTILINE)
# "(50 bytes)"만 -- "(50 bytes in 0.015 seconds)"류는 "bytes" 뒤에 닫는 괄호가
# 바로 오지 않아 배제된다. Copy data:와 최종 Data:가 둘 다 매치 -> 마지막
# (=최종 Data)이 남는다. 줄 끝 앵커: 요약 줄은 항상 이 괄호로 끝난다 --
# 앵커가 없으면 경로에 박힌 "(999 bytes)"(예: /backup (999 bytes)/f.bin)가
# 총량 행세를 할 수 있다.
_SYNC_BYTES = re.compile(r"\((\d+) bytes\)\s*$", re.MULTILINE)
# dsync --dryrun(미리보기)은 복사 요약("Items: N"·"(N bytes)")을 찍지 않는다 -- 소스·
# 목적지 **walk** 요약만 남긴다(실측 2026-09-17: 소스 "Walked 2 items in 0.004 seconds
# (...)" 뒤에 목적지 "Walked 0 items ..."). 그래서 미리보기 files/bytes 가 항상 null
# 이었고 지문(summary 해시)이 사실상 상수였다. 최종 walk 줄("seconds", 진행 줄의
# "secs ..."는 배제)의 **첫** 매치 = 소스 항목 수. 바이트는 walk 가 찍지 않아 null.
# 줄 머리를 MFU INFO 로그 접두사("[YYYY-MM-DDTHH:MM:SS] ", mfu_util.h)에 고정한다(2026-10-12 리뷰): 앵커가 없으면 walk
# 요약보다 **먼저** 찍히는 "[ts] Walking <대상 경로>" 줄이나 walk 오류 줄("[ts] [rank] [file:line] ERROR: ... '<경로>'")의
# 경로 텍스트에 이 문구가 들어 있을 때 그 숫자가 첫 매치가 됐다(경로는 공백·괄호를 허용한다). 항목 목록
# (mfu_flist_print)은 walk 줄 **뒤**에 오므로 첫 매치를 앞지르지 못한다.
_SYNC_WALKED = re.compile(r"^\[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\] Walked (\d+) items in [\d.]+ seconds \(",
                          re.MULTILINE)
# 그래도 경로 이름에는 줄바꿈이 들어갈 수 있다(validate_relative_path 도, 파일시스템도 막지 않는다) -- 대상 트리 안의 이름에
# "\n[ts] Walked 1 items in 0.0 seconds (" 를 넣으면 「Walking」·walk 오류 줄(진짜 요약 **앞**)이나 drm 목록(진짜 요약 **뒤**)에
# 줄 머리부터 맞는 가짜 줄이 생긴다(2026-10-12 보안 검토 -- 컨펌 창에 「삭제 대상 1개」, 실제 삭제 25만). 가짜 줄은 **더할** 수만
# 있고 진짜 줄을 지울 수는 없으므로 「walk 횟수보다 많이 보이면 모름」으로 막는다: drm 은 경로 하나를 한 번(정확히 1),
# dsync 는 소스·목적지 두 번(DMS 는 link-dest 를 싣지 않는다 -- 2 이하). 넘치면 null(모름, 지어내지 않는다).
_DRM_WALKS = 1
_DSYNC_MAX_WALKS = 2
_RM_ITEMS = re.compile(r"Removed (\d+) items")
# drm --dryrun(미리보기)도 "Removed N items" 를 찍지 않는다(2026-10-12 실측: walk 요약 뒤에 지울 항목 목록
# "Type=DIR File=..." 만) -- drm.c 가 dryrun 이면 mfu_flist_unlink 대신 mfu_flist_print 만 부르고, 그 목록은 앞 10개·뒤
# 10개만 찍고 중간을 "<snip>" 으로 자른다(mfu_flist.c, --stat 이면 ls 형식) -- 줄 수로는 셀 수 없다. 그래서 rm 미리보기
# files 가 늘 null(컨펌 창 「삭제 대상 모름」)이었다. walk 요약이 유일한 개수 출처이고, dsync 미리보기와 같은 정규식이다.

# nsync는 stock mpifileutils가 아니라 역할 기반(src/dst rank) 별도 도구라 Items:/
# (N bytes)를 전혀 찍지 않는다 -- 실측 출력과 결정 근거는 설계 부록 A.
# items는 "Planned actions:" 줄에서 뽑는다: 실행·프리뷰 양쪽에 같은 형식으로 나오고
# 합이 같은 트리의 dsync Items:와 일치한다. 실행 진행 줄의 actions=9는 planned 10과
# 어긋나(배치 병합) 쓰지 않는다. 줄 전체(끝까지)를 잡아 쌍 파싱에 넘긴다.
_NSYNC_PLANNED_LINE = re.compile(r"Planned actions:(.*)$", re.MULTILINE)
# "이름=정수" 쌍 -- 액션 이름에는 하이픈이 들어간다(symlink-update, skipped-dst-only).
_NSYNC_ACTION_PAIR = re.compile(r"([A-Za-z][\w-]*)=(\d+)")
# 건드리지 않은 항목이라 처리량이 아니다(설계 부록 A). 화이트리스트가 아니라 이
# 블랙리스트로 합산하므로 nsync가 새 액션 종류를 추가해도 파서 수정 없이 따라간다.
_NSYNC_SKIP_KEYS = frozenset({"skipped-dst-only"})
# bytes는 진행 줄의 사람용 표기가 유일한 출처다(원시 정수 바이트를 안 찍는다).
# 프리뷰는 planned-volume=, 실행은 copied-volume= -- runner에 phase 분기가 없으니
# 둘 다 받는다. 값 형식은 "<실수> <단위>"(예: 50.000 B, 1.234 GiB).
_NSYNC_VOLUME = re.compile(r"(?:planned|copied)-volume=(\d+(?:\.\d+)?)\s*([A-Za-z]+)")
_NSYNC_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2,
                "GiB": 1024 ** 3, "TiB": 1024 ** 4}


def _last_int(pattern: "re.Pattern[str]", text: str) -> "int | None":
    matches = pattern.findall(text or "")
    return int(matches[-1]) if matches else None


def parse_sync_counts(stdout: str) -> "tuple[int | None, int | None]":
    """dsync/nsync stdout -> (최종 items, 최종 bytes). 매치 없으면 해당 값 None."""
    return _last_int(_SYNC_ITEMS, stdout), _last_int(_SYNC_BYTES, stdout)


def parse_sync_dryrun_counts(stdout: str) -> "tuple[int | None, int | None]":
    """dsync --dryrun stdout -> (소스 walk 항목 수, None). 복사 요약이 있으면(도구가
    언젠가 dry-run 요약을 찍게 되면) 그쪽이 우선 -- 실행 파서와 같은 값 체계."""
    files, nbytes = parse_sync_counts(stdout)
    if files is None:
        matches = _SYNC_WALKED.findall(stdout or "")
        # walk 횟수보다 많으면 이름에 심은 가짜 줄이 섞였다 -- 어느 것이 소스인지 믿을 수 없다(위 주석).
        files = int(matches[0]) if 0 < len(matches) <= _DSYNC_MAX_WALKS else None
    return files, nbytes


def parse_nsync_counts(stdout: str) -> "tuple[int | None, int | None]":
    """nsync stdout -> (planned actions 합계, 볼륨 바이트). 매치 없으면 해당 값 None.

    실행/프리뷰 공통 형식이라 phase 분기가 필요 없다(설계 부록 A)."""
    return _nsync_items(stdout), _nsync_bytes(stdout)


def _nsync_items(stdout: str) -> "int | None":
    lines = _NSYNC_PLANNED_LINE.findall(stdout or "")
    if not lines:
        return None
    try:
        # 마지막 줄이 최종 계획이다(다른 파서와 같은 규칙). skip 키만 빼고 전부 더한다.
        return sum(int(v) for k, v in _NSYNC_ACTION_PAIR.findall(lines[-1])
                   if k not in _NSYNC_SKIP_KEYS)
    except ValueError:  # int() 문자열 변환 상한(4300자) 등 병적 입력 -- 계약상 예외 금지
        return None


def _nsync_bytes(stdout: str) -> "int | None":
    matches = _NSYNC_VOLUME.findall(stdout or "")
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
        return round(float(number) * factor)
    except (ValueError, OverflowError):  # 자릿수 폭주 -> inf -> round 예외. 계약상 예외 금지
        return None


def parse_rm_counts(stdout: str) -> "tuple[int | None, None]":
    """drm stdout -> (removed items, None). drm은 바이트를 보고하지 않는다(설계 §1)."""
    return _last_int(_RM_ITEMS, stdout), None


def parse_rm_dryrun_counts(stdout: str) -> "tuple[int | None, None]":
    """drm --dryrun stdout -> (지울 항목 수, None) = walk 요약의 첫 최종 줄(drm 은 경로 하나를 한 번 walk 한다 -- 진행 줄
    "secs ..." 는 배제). 삭제 요약("Removed N items")은 보지 않는다: dry-run 은 mfu_flist_unlink 를 부르지 않아 그 줄이
    절대 나오지 않고(drm.c, mfu_flist_remove.c), 앵커 없는 _RM_ITEMS 는 목록에 찍힌 파일 이름("Removed 1 items.txt")만
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
    return (int(matches[0]) if len(matches) == _DRM_WALKS else None), None


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
        with open(report_path, encoding="utf-8") as f:
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
