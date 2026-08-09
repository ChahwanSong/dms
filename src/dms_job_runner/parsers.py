"""mpifileutils 출력에서 항목수·바이트수를 뽑는 순수 파서(슬라이스 15 설계 §3).

세 함수 모두 (files, bytes) 튜플을 돌려주고, 실패는 해당 값 None -- 예외를 던지지
않는다(설계 §4 fail-soft: 파싱이 잡을 죽이는 경로는 없다). files의 의미는
"항목(items)"으로 통일한다(설계 §2.2): dsync/nsync = 최종 Items(디렉터리+파일+링크),
drm = Removed N items, dscan = summary.total_entries -- drm이 파일/디렉터리 구분
없이 items만 내므로 3도구 교차 일관성이 가장 좋다. parse_scan_counts만 파일을
읽는다(읽기 전용) -- 나머지는 문자열 입력만 받는 순수 함수다."""
import json
import re

# 최종 요약의 "Items: N"만 -- walk 단계의 "Items     : 0"(콜론 앞 패딩)은 형태가
# 달라 자연 배제된다. 복사 단계 중간 요약과 최종 블록이 둘 다 매치되므로
# 마지막 매치(=최종 블록)를 쓴다(설계 §1: 마지막 매치가 최종 블록이다).
_SYNC_ITEMS = re.compile(r"Items: (\d+)\s*$", re.MULTILINE)
# "(50 bytes)"만 -- "(50 bytes in 0.015 seconds)"류는 "bytes" 뒤에 닫는 괄호가
# 바로 오지 않아 배제된다. Copy data:와 최종 Data:가 둘 다 매치 -> 마지막
# (=최종 Data)이 남는다.
_SYNC_BYTES = re.compile(r"\((\d+) bytes\)")
_RM_ITEMS = re.compile(r"Removed (\d+) items")


def _last_int(pattern: "re.Pattern[str]", text: str) -> "int | None":
    matches = pattern.findall(text or "")
    return int(matches[-1]) if matches else None


def parse_sync_counts(stdout: str) -> "tuple[int | None, int | None]":
    """dsync/nsync stdout -> (최종 items, 최종 bytes). 매치 없으면 해당 값 None."""
    return _last_int(_SYNC_ITEMS, stdout), _last_int(_SYNC_BYTES, stdout)


def parse_rm_counts(stdout: str) -> "tuple[int | None, None]":
    """drm stdout -> (removed items, None). drm은 바이트를 보고하지 않는다(설계 §1)."""
    return _last_int(_RM_ITEMS, stdout), None


def parse_scan_counts(report_path: str) -> "tuple[int | None, None]":
    """dscan-report.json -> (summary.total_entries, None). 총 바이트는 리포트에
    없다(크기 히스토그램뿐 -- 설계 §8, 스키마 확장은 별도 슬라이스). 파일 없음/
    JSON 깨짐/키 없음/타입 이상 -> None. bool·음수 배제는 승격 경로 _as_count와
    같은 원칙 -- 여기서 먼저 걸러 summary 자체를 깨끗하게 유지한다."""
    try:
        with open(report_path) as f:
            report = json.load(f)
    except (OSError, ValueError):
        return None, None
    summary = report.get("summary") if isinstance(report, dict) else None
    entries = summary.get("total_entries") if isinstance(summary, dict) else None
    if isinstance(entries, bool) or not isinstance(entries, int) or entries < 0:
        return None, None
    return entries, None
