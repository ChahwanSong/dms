"""잡 도구 출력에서 항목수·바이트수를 뽑는 순수 파서(슬라이스 15 설계 §3).

네 함수 모두 (files, bytes) 튜플을 돌려주고, 실패는 해당 값 None -- 예외를 던지지
않는다(설계 §4 fail-soft: 파싱이 잡을 죽이는 경로는 없다). files의 의미는
"항목(items)"으로 통일한다(설계 §2.2): dsync = 최종 Items(디렉터리+파일+링크),
nsync = Planned actions 합계(설계 부록 A -- 같은 트리에서 dsync Items와 일치),
drm = Removed N items, dscan = summary.total_entries -- drm이 파일/디렉터리 구분
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
_RM_ITEMS = re.compile(r"Removed (\d+) items")

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


def parse_scan_counts(report_path: str) -> "tuple[int | None, None]":
    """dscan-report.json -> (summary.total_entries, None). 총 바이트는 리포트에
    없다(크기 히스토그램뿐 -- 설계 §8, 스키마 확장은 별도 슬라이스). 파일 없음/
    JSON 깨짐/키 없음/타입 이상 -> None. bool·음수 배제는 승격 경로 _as_count와
    같은 원칙 -- 여기서 먼저 걸러 summary 자체를 깨끗하게 유지한다."""
    try:
        # encoding 명시: 로케일 기본 인코딩(비 UTF-8 컨테이너)에서 한글 경로가
        # 든 리포트가 UnicodeDecodeError로 터지면 fail-soft가 삼켜 조용히 NULL이 된다.
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
    except Exception:  # noqa: BLE001 -- 계약이 "절대 예외 없음"(설계 §4)이라 문자 그대로 지킨다
        return None, None
    summary = report.get("summary") if isinstance(report, dict) else None
    entries = summary.get("total_entries") if isinstance(summary, dict) else None
    if isinstance(entries, bool) or not isinstance(entries, int) or entries < 0:
        return None, None
    return entries, None
