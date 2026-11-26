"""sync 진행률(2026-11-26 설계 §3) -- 도구 stdout 의 진행·단계 줄을 읽어 <phase>/progress.json 스냅숏(스키마 v1)을 만든다.

진행 값은 **표시 전용**이다(설계 D8): 확인 게이트·지문·통계·사유 어디에도 쓰지 않는다. dsync 는 경로를 날것으로 찍어 이름에
줄바꿈을 넣으면 진행 줄을 위조할 수 있다 -- 흔들리는 것은 그 사람 자기 잡의 화면 표시뿐이다(summary·사유는 parsers 가 따로
정한다).

스냅숏 계약(frontend/src/lib/progressContract.json 이 단일 원천 -- 이 패키지는 dms 를 import 하지 않는 독립 패키지라 상수를
중복 정의하고 tests/test_job_runner_progress.py 가 계약 파일과 잇는다):
  - 키 없음 = 모름. 0 으로 채우지 않는다. 하위 객체(step·overall·walk·batch·scan)는 모르면 키째 뺀다(빈 객체를 쓰지 않는다).
  - 도구가 낸 「secs left」(남은 시간)는 어떤 키에도, 원문으로도 담지 않는다(사용자 결정 -- 남은 시간은 보이지 않는다). 도구
    원문 줄도 담지 않는다(칩으로 누구나 여는 파일이고, 같은 원문은 stdout.log 에 있다).
  - 초 값은 since_line_s ≤ phase_since_s ≤ elapsed_s(소수 1자리), since_line_s 는 인식한 줄이 있을 때만(lines > 0).
  - 직렬화 상한 MAX_BYTES -- 넘으면 그 회차를 건너뛴다(모든 필드가 수치·짧은 열거라 실제로는 넘지 않는다).

줄 인식(설계 §3.3 정규식 표): 줄 **전체** fullmatch, 머리는 MFU 접두 "[YYYY-MM-DDTHH:MM:SS] ". 오류 줄("[ts] [rank] [file:line]
ERROR: ")은 첫 분기에서 버리고, 본문 첫 단어로 후보 정규식 1~3개만 본다(오류 폭주에서도 줄당 µs). LINE_MAX 를 넘는 줄은 진행 줄이
아니다. 도구 표는 둘로 나뉜다 -- dsync 표는 dsync 잡에만, nsync 표는 nsync 잡에만(계약 tools), nsync 의 실행 진행 줄은 실행에만·
계획 진행 줄은 미리보기에만(계약 mode). 단계(phase) 값은 PHASES 안이다: 러너가 붙이는 workers(run_job 맨 앞)·start(mpirun 직전)
와 도구 머리 줄에서 오는 것. 끝 스냅숏은 ended:true 이고 phase 는 마지막 인식 단계 그대로다.

쓰기 시점(Tracker.due -- 호출자 stream 이 메인 tick 0.5초마다 묻는다): 바뀐 것이 있고 마지막 쓰기 뒤 THROTTLE_SECONDS 가
지났으면, 바뀐 것이 없어도 HEARTBEAT_SECONDS 가 지났으면 쓴다. workers·start·끝 스냅숏은 스로틀 없이 바로 쓴다(stream.publish).
크기가 고정이고 이력을 쌓지 않아 디렉터리가 커져도 파일 크기·쓰기 횟수가 늘지 않는다(사용자 요구 -- 큰 디렉터리 효율화).

동시성: Tracker 는 잠금을 갖지 않는다 -- 리더 스레드(feed)와 메인 tick(due·snapshot_bytes)이 같은 stream 잠금(feed_lock)을
쥐고 부른다(stream 모듈 docstring T4)."""
import json
import math
import re
import time

SCHEMA_VERSION = 1
FILE_NAME = "progress.json"
MAX_BYTES = 4096
# 계약 snapshot.phases 와 같은 목록·순서(시험이 잇는다).
PHASES = ("workers", "start", "walk_src", "walk_dst", "compare", "compare_contents", "delete", "copy_prep", "mkdir",
          "create", "copy", "metadata", "metadata_dirs", "timestamps", "done", "scan", "batches", "finishing")
THROTTLE_SECONDS = 3.0
HEARTBEAT_SECONDS = 30.0
# 진행·단계 줄은 짧다(가장 긴 nsync 실행 진행 줄도 300자 남짓) -- 넘는 줄은 보지 않는다(오류 폭주 비용 상한).
LINE_MAX = 4096
TOOLS = ("dsync", "nsync")

_INT_MAX = 2 ** 63 - 1
_FLOAT_MAX = 1e18
_UNITS = {"B": 1, "KiB": 2 ** 10, "MiB": 2 ** 20, "GiB": 2 ** 30, "TiB": 2 ** 40, "PiB": 2 ** 50, "EiB": 2 ** 60}
_TS_LEN = 22                     # "[2026-11-26T08:28:59] "

_TS = r"\[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\] "
_N = r"(\d{1,20})"
_F = r"(\d{1,20}(?:\.\d{1,6})?)"
_U = r"(B|KiB|MiB|GiB|TiB|PiB|EiB)"
_BW = r"(B|KiB|MiB|GiB|TiB|PiB|EiB)/s"
# 남은 시간 꼬리는 맞추되 수치는 버린다(그룹 없음 -- done 여부는 줄 끝으로 본다).
_LEFT = r"(?:-?\d{1,20} secs left \.\.\.|done)"

# ---- 설계 §3.3 정규식 표(본문 -- 머리 _TS 는 compile 때 붙인다) --------------------------------------------------------
_DSYNC = {
    "H_WALK_SRC": r"Walking source path",
    "H_WALK_DST": r"Walking destination path",
    "H_COMPARE": r"Comparing file sizes and modification times of " + _N + r" items",
    "H_COMPARE_CONTENTS": r"Comparing file contents of " + _N + r" items",
    "H_DELETE": r"Deleting items from destination",
    "H_REMOVING": r"Removing " + _N + r" items",
    "H_COPY_PREP": r"Copying items to destination",
    "H_MKDIR": r"Creating " + _N + r" directories",
    "H_CREATE": r"Creating " + _N + r" files\.",
    "H_COPY": r"Copying data\.",
    "H_META": r"(?:Setting ownership, permissions, and timestamps|Fixing permissions)\.",
    "H_META_DIRS": r"(?:Setting ownership, permissions, and timestamps|Fixing permissions) on directories\.",
    "H_TIMESTAMPS": r"Updating timestamps on newly copied files",
    "H_DONE": r"Completed sync",
    "WALK_PROG": r"Walked " + _N + r" items in " + _F + r" secs \(" + _F + r" items/sec\) \.\.\.",
    "WALK_DONE": r"Walked " + _N + r" items in " + _F + r" seconds \(" + _F + r" items/sec\)",
    "MKDIR_PROG": r"Created " + _N + r" directories \(" + _N + r"%\) in " + _F + r" secs \(" + _F + r" dirs/sec\) " + _LEFT,
    "CREATE_PROG": r"Created " + _N + r" items \(" + _N + r"%\) in " + _F + r" secs \(" + _F + r" items/sec\) " + _LEFT,
    "COPY_PROG": r"Copied " + _F + " " + _U + r" \(" + _N + r"%\) in " + _F + r" secs \(" + _F + " " + _BW + r"\) " + _LEFT,
    "COMPARE_PROG": (r"Compared " + _F + " " + _U + r" \(" + _N + r"%\) in " + _F + r" secs \(" + _F + " " + _BW + r"\) "
                     + _LEFT),
    "REMOVE_PROG": (r"Removed " + _N + r" items \(" + _F + r"%\) in " + _F + r" secs \(" + _F
                    + r" items/sec\) (?:-?\d{1,20} secs remaining \.\.\.|done)"),
    "REMOVE_DONE": r"Removed " + _N + r" items in " + _F + r" seconds \(" + _F + r" items/sec\)",
    "META_PROG": r"Updated " + _N + r" items in " + _F + r" secs \(" + _F + r" items/sec\) (?:\.\.\.|done)",
    "META_DONE": r"Updated " + _N + r" items in " + _F + r" seconds \(" + _F + r" items/sec\)",
    "OVERALL": r"Copied " + _N + r" of " + _N + r" items \(" + _F + r"%\)",
}
_NSYNC = {
    "NS_ROLE": r"Role assignment mode=[a-z]{1,16} src_ranks=" + _N + r" dst_ranks=" + _N,
    "NS_BATCH_MODE": (r"Batch mode enabled: batch-files=" + _N + r" batch-count=" + _N + r" src-items=" + _N
                      + r" dst-items=" + _N),
    "NS_PROG_ON": r"Progress logging enabled on console log rank \d{1,9} \(per batch\)",
    "NS_PROG_EXEC": (r"Progress " + _F + r"% batch " + _N + "/" + _N + r" actions=" + _N + r" copied-files=" + _N
                     + r" copied-volume=" + _F + " " + _U + r" recent\(actions=" + _N + r" files=" + _N + r" volume="
                     + _F + " " + _U + r", " + _F + r" files/s, " + _F + " " + _BW + r" over " + _F + r" s\) avg\("
                     + _F + r" files/s, " + _F + " " + _BW + r"\)"),
    "NS_PROG_DRY": (r"Progress " + _F + r"% batch " + _N + "/" + _N + r" planned-actions=" + _N
                    + r" planned-copy-files=" + _N + r" planned-volume=" + _F + " " + _U),
    # 미리보기는 「planner dryrun complete」를 찍는다(2026-11-26 실측 M4) -- 실행과 같은 마무리 단계다.
    "NS_PHASE5": r"nsync Phase 5 planner(?:\+execute| dryrun) complete",
    "NS_DONE": (r"(?:Execution completed successfully|Dryrun enabled: planner generated actions but no filesystem "
                r"changes were made\.)"),
}
# 단계 머리 줄 → phase(step 을 비운다).
_HEAD_PHASE = {
    "H_WALK_SRC": "walk_src", "H_WALK_DST": "walk_dst", "H_COMPARE": "compare", "H_COMPARE_CONTENTS": "compare_contents",
    "H_DELETE": "delete", "H_COPY_PREP": "copy_prep", "H_MKDIR": "mkdir", "H_CREATE": "create", "H_COPY": "copy",
    "H_META": "metadata", "H_META_DIRS": "metadata_dirs", "H_TIMESTAMPS": "timestamps", "H_DONE": "done",
    "NS_ROLE": "scan", "NS_PROG_ON": "batches", "NS_PHASE5": "finishing", "NS_DONE": "done",
}
# 스냅숏 하위 객체의 키 순서(계약 fields 순서).
_ORDER = (
    ("step", ("items", "total", "percent", "bytes", "rate_bps", "rate_items", "done")),
    ("overall", ("items", "total", "percent")),
    ("walk", ("src_items", "dst_items")),
    ("batch", ("done", "count", "percent", "actions", "copied_files", "copied_bytes", "avg_files_per_s", "avg_bps",
               "planned_actions", "planned_copy_files", "planned_bytes")),
    ("scan", ("batch_files", "batch_count", "src_items", "dst_items")),
)


def _int(text):
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= _INT_MAX else None


def _float(text, hi=_FLOAT_MAX):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and 0 <= value <= hi else None


def _pct_int(text):
    value = _int(text)
    return value if value is not None and value <= 100 else None


def _bytes(number, unit):
    """사람 단위 → 근사 바이트(round(값 × 단위) -- parsers._nsync_volume 과 같은 규칙, 소수 3자리라 GiB 규모 약 0.05%)."""
    factor = _UNITS.get(unit)
    value = _float(number, hi=math.inf)
    if factor is None or value is None:
        return None
    try:
        out = round(value * factor)
    except (OverflowError, ValueError):
        return None
    return out if 0 <= out <= _INT_MAX else None


def _put(obj, key, value):
    """값이 범위 밖(None)이면 그 키를 뺀다 -- 앞선 줄의 낡은 값을 남기지 않는다(모름)."""
    if value is None:
        obj.pop(key, None)
    else:
        obj[key] = value


def _ended_with_done(line):
    return line.endswith(" done")


# ---- 줄 처리기(호출 시점에 이미 fullmatch 됐다) ---------------------------------------------------------------------------

def _h_total(t, m, line):
    _put(t._step, "total", _int(m.group(1)))


def _walk_prog(t, m, line):
    _put(t._step, "items", _int(m.group(1)))


def _walk_done(t, m, line):
    # 완료 줄은 직전 머리 줄의 쪽이다 -- walk 머리 밖(start 등)의 완료 줄은 어느 쪽인지 몰라 담지 않는다.
    key = {"walk_src": "src_items", "walk_dst": "dst_items"}.get(t._phase)
    if key is not None:
        _put(t._walk, key, _int(m.group(1)))


def _items_prog(t, m, line):      # MKDIR_PROG·CREATE_PROG
    s = t._step
    _put(s, "items", _int(m.group(1)))
    _put(s, "percent", _pct_int(m.group(2)))
    _put(s, "rate_items", _float(m.group(4)))
    s["done"] = _ended_with_done(line)


def _bytes_prog(t, m, line):      # COPY_PROG·COMPARE_PROG
    s = t._step
    _put(s, "percent", _pct_int(m.group(3)))
    _put(s, "bytes", _bytes(m.group(1), m.group(2)))
    _put(s, "rate_bps", _bytes(m.group(5), m.group(6)))
    s["done"] = _ended_with_done(line)


def _remove_prog(t, m, line):
    s = t._step
    _put(s, "items", _int(m.group(1)))
    _put(s, "percent", _float(m.group(2), hi=100))
    _put(s, "rate_items", _float(m.group(4)))
    s["done"] = _ended_with_done(line)


def _rate_prog(t, m, line):       # META_PROG
    s = t._step
    _put(s, "items", _int(m.group(1)))
    _put(s, "rate_items", _float(m.group(3)))
    s["done"] = _ended_with_done(line)


def _rate_done(t, m, line):       # META_DONE·REMOVE_DONE
    s = t._step
    _put(s, "items", _int(m.group(1)))
    _put(s, "rate_items", _float(m.group(3)))
    s["done"] = True


def _overall(t, m, line):
    o = {}
    _put(o, "items", _int(m.group(1)))
    _put(o, "total", _int(m.group(2)))
    _put(o, "percent", _float(m.group(3), hi=100))
    t._overall = o


def _batch_mode(t, m, line):
    sc = {}
    _put(sc, "batch_files", _int(m.group(1)))
    _put(sc, "batch_count", _int(m.group(2)))
    _put(sc, "src_items", _int(m.group(3)))
    _put(sc, "dst_items", _int(m.group(4)))
    t._scan = sc
    _put(t._batch, "count", _int(m.group(2)))


def _prog_on(t, m, line):
    t._batch["done"] = 0


def _batch_common(t, m):
    b = t._batch
    _put(b, "done", _int(m.group(2)))
    _put(b, "count", _int(m.group(3)))
    _put(b, "percent", _float(m.group(1), hi=100))
    return b


def _prog_exec(t, m, line):
    b = _batch_common(t, m)
    _put(b, "actions", _int(m.group(4)))
    _put(b, "copied_files", _int(m.group(5)))
    _put(b, "copied_bytes", _bytes(m.group(6), m.group(7)))
    _put(b, "avg_files_per_s", _float(m.group(16)))
    _put(b, "avg_bps", _bytes(m.group(17), m.group(18)))


def _prog_dry(t, m, line):
    b = _batch_common(t, m)
    _put(b, "planned_actions", _int(m.group(4)))
    _put(b, "planned_copy_files", _int(m.group(5)))
    _put(b, "planned_bytes", _bytes(m.group(6), m.group(7)))


_HANDLERS = {
    "H_COMPARE": _h_total, "H_COMPARE_CONTENTS": _h_total, "H_REMOVING": _h_total, "H_MKDIR": _h_total,
    "H_CREATE": _h_total,
    "WALK_PROG": _walk_prog, "WALK_DONE": _walk_done, "MKDIR_PROG": _items_prog, "CREATE_PROG": _items_prog,
    "COPY_PROG": _bytes_prog, "COMPARE_PROG": _bytes_prog, "REMOVE_PROG": _remove_prog, "REMOVE_DONE": _rate_done,
    "META_PROG": _rate_prog, "META_DONE": _rate_done, "OVERALL": _overall,
    "NS_BATCH_MODE": _batch_mode, "NS_PROG_ON": _prog_on, "NS_PROG_EXEC": _prog_exec, "NS_PROG_DRY": _prog_dry,
}


def _first_words(body):
    """본문 정규식이 받을 수 있는 첫 단어들 -- 「(?:A …|B …)」 로 시작하면 갈래마다 하나."""
    if body.startswith("(?:"):
        return tuple(re.match(r"[A-Za-z]+", alt).group(0) for alt in body[3:body.index(")")].split("|"))
    return (re.match(r"[A-Za-z]+", body).group(0),)


def _table(patterns, skip=()):
    """{본문 첫 단어: [(이름, 줄 전체 정규식), ...]} -- 첫 단어로 후보를 1~3개로 줄인다."""
    out = {}
    for name, body in patterns.items():
        if name in skip:
            continue
        rx = re.compile(_TS + body)
        for word in _first_words(body):
            out.setdefault(word, []).append((name, rx))
    return out


# (도구, 미리보기) → 표. nsync 의 실행·계획 진행 줄은 그 모드에서만(계약 mode -- 실행 스냅숏에 planned_* 가 섞이지 않는다).
_TABLES = {
    ("dsync", False): _table(_DSYNC),
    ("dsync", True): _table(_DSYNC),
    ("nsync", False): _table(_NSYNC, skip=("NS_PROG_DRY",)),
    ("nsync", True): _table(_NSYNC, skip=("NS_PROG_EXEC",)),
}


def phases_for(tool):
    """그 도구의 Tracker 가 낼 수 있는 phase 전부(러너가 붙이는 workers·start 포함) -- 계약 phases_by_tool 과 시험이 잇는다."""
    patterns = _DSYNC if tool == "dsync" else _NSYNC
    heads = {_HEAD_PHASE[name] for name in patterns if name in _HEAD_PHASE}
    return ("workers", "start", *sorted(heads, key=PHASES.index))


class Tracker:
    """한 단계(미리보기·실행) 동안의 진행 상태. 모든 메서드는 호출자가 쥔 잠금 안에서 불린다(모듈 docstring).

    now 를 생략하면 생성 때 받은 clock 을 읽는다(run_job 의 시계 -- main 은 time.monotonic, 시험은 가상 시계)."""

    def __init__(self, tool, *, dryrun=False, quiet=False, clock=time.monotonic):
        if tool not in TOOLS:
            raise ValueError(f"진행률은 sync 도구만: {tool!r}")
        self.tool = tool
        self.dryrun = bool(dryrun)
        self.quiet = bool(quiet)
        self._clock = clock
        self._table = _TABLES[(tool, self.dryrun)]
        now = clock()
        self._t0 = now
        self._phase = "workers"
        self._phase_t = now
        self._lines = 0
        self._last_line_t = None
        self._ended = False
        self._step, self._overall, self._walk, self._batch, self._scan = {}, {}, {}, {}, {}
        self._dirty = True
        self._last_write = None
        self._parts = []
        self._plen = 0
        self._overlong = False

    def _now(self, now):
        return self._clock() if now is None else now

    # ---- 상태 바꾸기 ----------------------------------------------------------------------------------------------------
    def set_phase(self, phase, now=None):
        """러너가 붙이는 단계(workers·start)와 도구 머리 줄의 단계. 단계가 바뀌면 단계 수치(step)를 비운다."""
        self._phase = phase
        self._phase_t = self._now(now)
        self._step = {}
        self._dirty = True

    def end(self, now=None):
        """도구가 끝났다 -- 끝 스냅숏(ended:true). phase 는 마지막 인식 단계 그대로다."""
        self._ended = True
        self._dirty = True

    def feed(self, text, now=None):
        """디코드한 stdout 조각 하나 -- "\\n" 로만 줄을 나눈다(파서의 MULTILINE 과 같은 경계, splitlines 아님). 걸친 줄은 다음
        조각까지 들고 있되 LINE_MAX 를 넘으면 버린다(진행 줄이 아니다 -- 버퍼가 개행 없는 거대 줄로 자라지 않는다)."""
        if not text:
            return
        now = self._now(now)
        start = 0
        while True:
            nl = text.find("\n", start)
            if nl < 0:
                self._part(text, start, len(text))
                return
            self._part(text, start, nl)
            line = None if self._overlong else "".join(self._parts)
            self._parts, self._plen, self._overlong = [], 0, False
            if line is not None:
                self.observe(line, now)
            start = nl + 1

    def finish(self, now=None):
        """개행 없이 끝난 마지막 줄도 한 줄로 본다(파서의 MULTILINE 도 그 줄을 본다)."""
        if self._parts and not self._overlong:
            self.observe("".join(self._parts), self._now(now))
        self._parts, self._plen, self._overlong = [], 0, False

    def _part(self, text, a, b):
        if self._overlong or b <= a:
            return
        if self._plen + (b - a) > LINE_MAX:
            self._parts, self._plen, self._overlong = [], 0, True
            return
        self._parts.append(text[a:b])
        self._plen += b - a

    def observe(self, line, now=None):
        """줄 하나(개행 없음) -- 진행·단계 줄로 인식했으면 True. 인식한 줄은 lines·since_line_s 를 갱신한다."""
        if len(line) > LINE_MAX or len(line) <= _TS_LEN or line[0] != "[" or line[20:_TS_LEN] != "] ":
            return False
        if line[_TS_LEN] == "[":            # "[ts] [rank] [file:line] ERROR|DBG" -- 진행 줄이 아니다
            return False
        sp = line.find(" ", _TS_LEN)
        candidates = self._table.get(line[_TS_LEN:] if sp < 0 else line[_TS_LEN:sp])
        if not candidates:
            return False
        for name, rx in candidates:
            m = rx.fullmatch(line)
            if m is None:
                continue
            now = self._now(now)
            phase = _HEAD_PHASE.get(name)
            if phase is not None:
                self.set_phase(phase, now)
            handler = _HANDLERS.get(name)
            if handler is not None:
                handler(self, m, line)
            self._lines += 1
            self._last_line_t = now
            self._dirty = True
            return True
        return False

    # ---- 쓰기 ------------------------------------------------------------------------------------------------------------
    def due(self, now=None):
        """메인 tick 이 묻는다: 지금 스냅숏을 쓸 때인가(스로틀·심장박동 -- 모듈 docstring)."""
        if self._last_write is None:
            return True
        gap = self._now(now) - self._last_write
        return (self._dirty and gap >= THROTTLE_SECONDS) or gap >= HEARTBEAT_SECONDS

    def snapshot(self, now=None):
        now = self._now(now)
        elapsed = min(_FLOAT_MAX, max(0.0, now - self._t0))
        in_phase = min(elapsed, max(0.0, now - self._phase_t))
        snap = {"v": SCHEMA_VERSION, "tool": self.tool, "dryrun": self.dryrun, "quiet": self.quiet,
                "phase": self._phase, "phase_since_s": round(in_phase, 1), "elapsed_s": round(elapsed, 1)}
        if self._last_line_t is not None and self._lines > 0:
            snap["since_line_s"] = round(min(in_phase, max(0.0, now - self._last_line_t)), 1)
        snap["lines"] = min(self._lines, _INT_MAX)
        snap["ended"] = self._ended
        for name, keys in _ORDER:
            obj = getattr(self, f"_{name}")
            sub = {k: obj[k] for k in keys if k in obj}
            if sub:
                snap[name] = sub
        return snap

    def snapshot_bytes(self, now=None):
        """직렬화한 스냅숏(ASCII JSON) -- 쓴 것으로 표시한다(스로틀 기준). MAX_BYTES 를 넘으면 None(그 회차를 건너뛴다)."""
        now = self._now(now)
        data = json.dumps(self.snapshot(now), separators=(",", ":"), allow_nan=False).encode("ascii")
        self._last_write = now
        self._dirty = False
        return data if len(data) <= MAX_BYTES else None
