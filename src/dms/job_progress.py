"""sync 진행률 읽기 쪽 상수와 sanitize(2026-11-26 설계 §4.2) -- GET /api/user/jobs/{job_id}/progress 가 쓴다.

api/ 밖(중립)에 두는 이유는 strip_scheme·artifact_files 와 같다(FastAPI 계층을 끌어오지 않는다). 러너는 잡 이미지의 독립
패키지(dms_job_runner.progress)라 같은 상수를 따로 정의하고, 계약 파일 frontend/src/lib/progressContract.json 이 셋(러너·이
모듈·포탈 타입)의 단일 원천이다 -- 런타임에는 읽지 않고 시험(tests/test_api_job_progress.py)이 잇는다.

progress.json 은 요청자 소유 <phase> 디렉터리 안의 파일이다(러너가 root 로 새 inode 를 쓰지만, 요청자가 바꿔치기할 수 있다).
그래서 내용을 믿지 않는다:
  - 상한(READ_MAX_BYTES) 초과·비 UTF-8·깨진 JSON·깊은 중첩(RecursionError)·객체 아님·required(v·tool·phase) 틀림 → None
    (라우트는 200 available:false -- 「모름」이지 오류가 아니다).
  - 허용 키만 남긴다(계약 snapshot.fields). 범위·타입이 틀린 **그 필드만** 뺀다(전체 거부 아님). 하위 객체는 한 단계 깊이,
    비면 키째 뺀다. 정수는 int(bool 아님) 0..2^63-1, 실수는 유한 0..상한(정수 표기 허용), percent 0..100.
  - 남은 정규화는 타입 검사뿐이라 예외 경로가 없다. 멱등이다(sanitize(계약 예) == 계약 예).
진행 값은 표시 전용이다 -- 확인 게이트·지문·통계·사유 어디에도 쓰지 않는다."""
import json
import math
import re

SCHEMA_VERSION = 1
FILE_NAME = "progress.json"
PHASES = ("preview", "execution")
READ_MAX_BYTES = 16 * 1024

_INT_MAX = 2 ** 63 - 1
_FLOAT_MAX = 1e18


def _int(**kw):
    return {"type": "int", "min": 0, "max": _INT_MAX, **kw}


def _float(hi=_FLOAT_MAX):
    return {"type": "float", "min": 0, "max": hi}


_BOOL = {"type": "bool"}
_PCT = _float(100)

# 계약 snapshot.fields 의 type·범위·required·const·values·pattern(doc·tools·mode 는 러너 출력 모양이라 여기서 보지 않는다).
FIELDS = {
    "v": {"type": "int", "const": SCHEMA_VERSION, "required": True},
    "tool": {"type": "enum", "values": ("dsync", "nsync"), "required": True},
    "dryrun": _BOOL,
    "quiet": _BOOL,
    "phase": {"type": "str", "pattern": r"^[a-z][a-z_]{0,23}$", "required": True},
    "phase_since_s": _float(),
    "elapsed_s": _float(),
    "since_line_s": _float(),
    "lines": _int(),
    "ended": _BOOL,
    "step": {"type": "obj", "fields": {"items": _int(), "total": _int(), "percent": _PCT, "bytes": _int(),
                                       "rate_bps": _int(), "rate_items": _float(), "done": _BOOL}},
    "overall": {"type": "obj", "fields": {"items": _int(), "total": _int(), "percent": _PCT}},
    "walk": {"type": "obj", "fields": {"src_items": _int(), "dst_items": _int()}},
    "batch": {"type": "obj", "fields": {"done": _int(), "count": _int(), "percent": _PCT, "actions": _int(),
                                        "copied_files": _int(), "copied_bytes": _int(), "avg_files_per_s": _float(),
                                        "avg_bps": _int(), "planned_actions": _int(), "planned_copy_files": _int(),
                                        "planned_bytes": _int()}},
    "scan": {"type": "obj", "fields": {"batch_files": _int(), "batch_count": _int(), "src_items": _int(),
                                       "dst_items": _int()}},
}

_DROP = object()


def _clean(spec, value):
    """서술자 하나와 값 하나 -- 맞으면 값(하위 객체는 걸러낸 사본), 틀리면 _DROP."""
    t = spec["type"]
    if t == "int":
        if type(value) is not int:                      # bool 은 int 의 하위형이라 type() 으로 가른다
            return _DROP
        if "const" in spec:
            return value if value == spec["const"] else _DROP
        return value if spec["min"] <= value <= spec["max"] else _DROP
    if t == "float":
        if type(value) not in (int, float) or not math.isfinite(value):
            return _DROP
        return value if spec["min"] <= value <= spec["max"] else _DROP
    if t == "bool":
        return value if type(value) is bool else _DROP
    if t == "enum":
        return value if isinstance(value, str) and value in spec["values"] else _DROP
    if t == "str":
        return value if isinstance(value, str) and re.fullmatch(spec["pattern"], value) else _DROP
    if t == "obj":
        if not isinstance(value, dict):
            return _DROP
        out = {}
        for key, sub in spec["fields"].items():
            if key in value:
                cleaned = _clean(sub, value[key])
                if cleaned is not _DROP:
                    out[key] = cleaned
        return out or _DROP
    return _DROP


def sanitize(raw):
    """progress.json 바이트 → 걸러낸 스냅숏 dict, 모양이 틀리면 None(모듈 docstring). 예외를 내지 않는다."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > READ_MAX_BYTES:
        return None
    try:
        doc = json.loads(bytes(raw).decode("utf-8"))
    except (ValueError, RecursionError):
        # UnicodeDecodeError·JSONDecodeError 는 ValueError, 16 KiB 안의 깊은 중첩("[[[[…")은 RecursionError(RuntimeError
        # 계열)다 -- 놓치면 라우트가 500 이 된다.
        return None
    if not isinstance(doc, dict):
        return None
    out = {}
    for key, spec in FIELDS.items():
        cleaned = _clean(spec, doc[key]) if key in doc else _DROP
        if cleaned is _DROP:
            if spec.get("required"):
                return None
            continue
        out[key] = cleaned
    return out
