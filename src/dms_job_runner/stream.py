"""mpirun 출력 스트리밍(2026-11-26 sync 진행률 설계 §1·§2) -- 도구가 도는 **동안** stdout.log·stderr.log 를 이어 쓰고, sync 는
progress.json(스냅숏 하나를 덮어쓴다)을 갱신하며, 파서 입력은 메모리 상한 안에서 지금과 같은 문자열로 모은다.

예전(subprocess.run(capture_output))에는 끝난 순간 세 파일이 한꺼번에 생겼다(22분 동안 stdout.log 404), 시간초과·취소의
SIGKILL 은 메모리의 출력을 전부 잃었고, launcher 메모리와 stdout.log 는 출력 크기만큼 무제한이었다(목적지가 가득 찬 1,000만
파일 복사 = 오류 줄 약 2.5 GB).

구성:
  run_streaming  -- main 의 run_tool. Popen + 리더 스레드 2개(stdout·stderr) + 메인 tick(0.5초 -- 진행 스냅숏) → ToolCapture.
  compat(run)    -- 주입 run 을 그대로 쓰는 run_tool(시험 22곳). 결과 문자열을 64 Ki 문자 조각으로 같은 장치에 흘린다.
  LineFeed       -- 파서 입력. 결합 메모리(문자 수 × 문자 폭) ≤ head_max 이면 원문 그대로(파서 무변경 -- 결과가 지금과 같다).
                    넘치면 앞부분(완결 줄만) + 넘친 구간의 요약 후보 줄(parsers.keep_line, 앞 4 MiB + 끝 4 MiB 링) +
                    정확한 줄 카운터(parsers.LINE_COUNTERS) + 실패 표본 후보 + echo 링. 줄 경계는 "\\n" 하나뿐이다(splitlines 는
                    \\x0b·\\x85·U+2028 등에서도 잘라 파서의 MULTILINE 과 어긋난다).
  LogSink        -- 로그 파일 크기 정책(UTF-8 바이트): 앞 head_max(실시간) + 끝 tail_max(메모리 링, 끝날 때 붙임) + 그 사이
                    「DMS_JR_LOG_GAP reason=size …」 한 줄. 끝 링은 줄 경계에서 시작한다. 진행 줄은 솎지 않는다(dsync 10초
                    간격 약 100 B -- 1주 6 MB). 폭주의 원인은 항목별 줄이다.
  ArtifactWriter -- 실 파일 쓰기(main 이 open_sink 로 넘긴다). 쓰기 스레드 W 하나가 모든 파일 I/O 를 맡는다.
  TextSink       -- compat 의 sink(run_job 의 write_text 로 끝에 한 번씩 -- 파일시스템을 직접 만지지 않는다).
  NullSink       -- 아무것도 하지 않는다(팩토리 실패의 2차 방어).

불변(설계 §1.3 -- 리뷰 기준):
  T1 daemon  -- 리더·쓰기 스레드는 daemon 이다. 비 daemon 이면 손자(ssh 등)가 파이프를 쥔 경우 sys.exit 가 그 스레드를 영원히
                기다려 launcher 가 끝나지 않고, vcjob 이 execution_timeout 까지 갱을 쥔 채 끝난 sync 가 시간초과로 판정된다.
  T2 배수     -- 리더는 EOF 까지 os.read 를 멈추지 않는다. 소비자(LineFeed·Tracker·sink.append) 예외는 각각 삼키고 그
                소비자만 끈다: LineFeed(out) → summary 핵심 3키(parse_lost), Tracker → 진행 기록만 멈춤, LineFeed(err) → aborted 모름.
  T3 원시 fd  -- Popen(bufsize=0) 의 fd 를 os.read 로 읽고 io.IncrementalNewlineDecoder(utf-8 replace, translate=True) 로
                디코드한다 -- TextIOWrapper 가 쓰는 조합이고 communicate() 텍스트 모드(decode(replace) 뒤 \\r\\n·\\r → \\n)와
                결과가 같다(청크 경계의 멀티바이트·\\r\\n 포함). 버퍼드 객체의 잠금을 쥐지 않아 종료 때 막힌 daemon 리더가
                「could not acquire lock … at interpreter shutdown」 치명 오류로 rc 를 덮어쓸 수 없다.
  T4 잠금     -- 스트림마다 잠금 하나. 리더는 블록 하나를 소비자에 넘기는 동안만(CPU 만, I/O 없음), 메인 tick 은 스냅숏을
                bytes 로 직렬화해 꺼내는 동안만 쥔다. 끝에서 stopped 를 세운 뒤에는 리더가 블록을 버리기만 한다.
  T5 fail-soft -- 메인 tick·sink 호출·쓰기 스레드의 예외는 전부 삼킨다(쓰기 실패는 그 출력만 끈다). 이 모듈은 launcher
                stdout/stderr(= 파드 로그)에 **아무것도 찍지 않는다** -- 스테퍼가 파드 로그에서 사유 마커를 읽고, 도구 출력은
                파드 로그로 흘리지 않는다(실패 꼬리 발췌만 runner 가 정한 모양으로).
  T6 끝내기   -- rc = proc.wait() → 리더 join(JOIN_SECONDS) → stopped → 끝 스냅숏(ended) → sink.close(CLOSE_SECONDS). 스레드가
                남았으면 exit_process 가 표준 스트림을 flush 하고 os._exit(rc) 한다(아니면 sys.exit -- 시험의 SystemExit 그대로).
  T7 리더는 파일을 만지지 않는다 -- CephFS base 가 멈추면 쓰기 스레드만 선다. 큐(WRITER_QUEUE_MAX, 메모리 측정)가 차면 블록을
                버리고 수만 센다(자리가 나면 「DMS_JR_LOG_GAP reason=backlog …」 를 먼저 넣는다) -- 도구가 우리 I/O 때문에 멈추지
                않는다. 리더가 닫는 것은 EOF 뒤 자기 파이프뿐이다 -- 정수 fd 가 아니라 파이프 파일 객체를 쥐어, 막힌 채 남은
                리더의 fd 가 run_streaming 반환 뒤 GC 로 닫혀 다른 파일에 재사용되지 않게 한다(_read_loop).

파일 쓰기(ARCHITECTURE §7 「root 제어면」 4 -- 요청자 소유 <phase> 에 경로로 open("w") 금지): 쓰기 스레드가 기동 직후(run_job
맨 앞, chown 전) phase 디렉터리를 O_DIRECTORY|O_NOFOLLOW dirfd 로 한 번 열고, 로그는 unlink + O_CREAT|O_EXCL|O_NOFOLLOW 새
inode(fchmod 0644), progress.json 은 임시 파일(".progress.json.tmp~" -- "~" 가 NAME_RE 밖이라 목록·API 에 뜨지 않는다)을
O_EXCL|O_NOFOLLOW 로 만들어 쓰고 rename 한다(독자는 반쪽 JSON 을 보지 않는다, 미리 놓인 심링크는 링크째 덮인다). 어떤
OSError 든 그 출력의 기록만 끄고 잡은 계속된다. fsync 는 하지 않는다(독자에게 필요한 것은 rename 원자성뿐).

진행 쓰기 우선: 쓰기 스레드는 진행 슬롯(최신 스냅숏 하나)이 바뀌었으면 로그 큐보다 **먼저** 쓰고, 로그 큐는 한 바퀴
WRITER_LOG_BUDGET_SECONDS 안에서만 비운다(FLUSH_SECONDS·FLUSH_BYTES 마다 os.write) -- 오류 폭주와 느린 CephFS 가 겹쳐도
진행 스냅숏이 로그 뒤에 줄 서지 않는다.

SIGTERM 처리기는 두지 않는다(설계 D9 -- PID 1 신호 의미 유지): SIGKILL 때 잃는 것은 마지막 1초 이내 로그·쓰기 큐 잔량과 넘침
모드의 끝 링이다(앞부분과 progress.json 은 남는다)."""
import codecs
import collections
import io
import os
import re
import subprocess
import sys
import threading
import time

from . import parsers
from .progress import FILE_NAME as PROGRESS_FILE_NAME

READ_SIZE = 64 * 1024
PARSE_HEAD_MAX = 64 * 1024 * 1024
PARSE_KEEP_HALF = 4 * 1024 * 1024
PARSE_LINE_MAX = 64 * 1024
KEEP_LINE_MAX = 4 * 1024
ECHO_LINES = 256
# runner.TOOL_OUTPUT_LINE_MAX 와 같은 값(꼬리 발췌가 어차피 자르는 길이) -- 넘침 모드 echo 링의 줄 상한.
ECHO_LINE_MAX = 512
ECHO_SRC_MAX = 4 * 1024 * 1024
ERR_PARSE_HEAD_MAX = 8 * 1024 * 1024
LOG_HEAD_MAX = 64 * 1024 * 1024
LOG_TAIL_MAX = 16 * 1024 * 1024
ERR_LOG_HEAD_MAX = 8 * 1024 * 1024
ERR_LOG_TAIL_MAX = 2 * 1024 * 1024
WRITER_QUEUE_MAX = 16 * 1024 * 1024
FLUSH_SECONDS = 1.0
FLUSH_BYTES = 64 * 1024
WRITER_LOG_BUDGET_SECONDS = 0.5
TICK_SECONDS = 0.5
JOIN_SECONDS = 30.0
CLOSE_SECONDS = 30.0
COMPAT_CHUNK = 64 * 1024
STDOUT_LOG = "stdout.log"
STDERR_LOG = "stderr.log"
LOG_LIMITS = {STDOUT_LOG: (LOG_HEAD_MAX, LOG_TAIL_MAX), STDERR_LOG: (ERR_LOG_HEAD_MAX, ERR_LOG_TAIL_MAX)}
PROGRESS_TMP = ".progress.json.tmp~"
GAP_MARKER = "DMS_JR_LOG_GAP"

ToolCapture = collections.namedtuple("ToolCapture", "returncode stdout stderr stdout_echo stderr_echo overflow")

_THREADS = []
_THREADS_LOCK = threading.Lock()


def _register(thread):
    with _THREADS_LOCK:
        _THREADS[:] = [t for t in _THREADS if t.is_alive()]
        _THREADS.append(thread)


def threads_left():
    """이 모듈이 띄운 리더·쓰기 스레드가 아직 살아 있는가(T6)."""
    with _THREADS_LOCK:
        return any(t.is_alive() for t in _THREADS)


def exit_process(rc):
    """main 의 끝(T6). 남은 스레드가 없으면 지금처럼 sys.exit(rc). 있으면(손자가 파이프를 쥔 리더, 멈춘 마운트의 쓰기 스레드,
    조기 종료로 닫지 않은 sink) 표준 스트림을 flush 한 뒤 os._exit(rc) -- 인터프리터 종료가 그 스레드에 걸리지 않게."""
    if threads_left():
        for f in (sys.stdout, sys.stderr):
            try:
                f.flush()
            except Exception:  # noqa: BLE001 -- 종료 경로는 rc 를 지킨다
                pass
        os._exit(rc if isinstance(rc, int) else 1)
    sys.exit(rc)


def _width(text):
    """결합 str 의 문자당 바이트(CPython 1·2·4) -- 메모리 상한을 문자 수가 아니라 메모리로 잰다."""
    if text.isascii():
        return 1
    top = max(text)
    return 1 if top <= "\xff" else 2 if top <= "￿" else 4


def _quiet(fn, *args):
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 -- T5
        return None


# ---- 파서 입력 -------------------------------------------------------------------------------------------------------------

_WIDE2 = re.compile("[^\x00-\xff]")
_WIDE4 = re.compile("[^\x00-￿]")


def _first_wide(blocks, widths, need, rx):
    """need 폭 이상인 첫 문자의 오프셋(없으면 전체 길이)."""
    pos = 0
    for block, width in zip(blocks, widths):
        if width >= need:
            m = rx.search(block)
            if m is not None:
                return pos + m.start()
        pos += len(block)
    return pos


def _head_limit(blocks, widths, head_max):
    """머리가 될 수 있는 가장 긴 접두(문자 수) -- 접두 **자신의** 메모리(길이 × 그 접두의 최대 폭)가 head_max 이하인 것. 받은
    조각의 경계와 무관하게 텍스트만으로 정해진다(compat 와 스트리밍이 같은 머리를 고른다)."""
    pos2 = _first_wide(blocks, widths, 2, _WIDE2)
    pos4 = _first_wide(blocks, widths, 4, _WIDE4)
    best = min(head_max, pos2)
    if head_max // 2 > pos2:
        best = max(best, min(head_max // 2, pos4))
    if head_max // 4 > pos4:
        best = max(best, head_max // 4)
    return best


def _cut_blocks(blocks, limit):
    """블록 목록에서 문자 오프셋 limit 안에서 끝나는 마지막 "\\n" 바로 뒤로 자른다 → (머리 블록들, 나머지 블록들). 하나의
    큰 join 없이(머리 상한만큼의 사본을 두 벌 쥐지 않는다)."""
    starts, pos = [], 0
    for b in blocks:
        starts.append(pos)
        pos += len(b)
    i = len(blocks) - 1
    while i >= 0 and starts[i] >= limit:
        i -= 1
    while i >= 0:
        j = blocks[i].rfind("\n", 0, max(0, limit - starts[i]))
        if j >= 0:
            return blocks[:i] + [blocks[i][:j + 1]], [blocks[i][j + 1:]] + blocks[i + 1:]
        i -= 1
    return [], list(blocks)


class LineFeed:
    """한 스트림의 파서 입력(모듈 docstring). 메서드는 호출자(_Stream)가 쥔 잠금 안에서만 불린다."""

    def __init__(self, *, head_max, keep_half=PARSE_KEEP_HALF, line_max=PARSE_LINE_MAX, keep_line_max=KEEP_LINE_MAX,
                 echo_lines=ECHO_LINES, echo_src_max=ECHO_SRC_MAX, keep=None, counters=(), errno_samples=0,
                 abort=False):
        self._head_max = head_max
        self._keep_half = keep_half
        self._line_max = line_max
        self._keep_line_max = keep_line_max
        self._echo_src_max = echo_src_max
        self._keep = keep
        self._counter_rx = tuple(counters)
        self._errno_max = errno_samples
        self._abort = abort
        self._blocks = []
        self._widths = []
        self._chars = 0
        self._width = 1
        self.truncated = False
        self._head = ""
        self._parts = []
        self._plen = 0
        self.counters = {name: 0 for name, _ in self._counter_rx}
        self.errno_lines = []
        self.aborted_seen = False
        self._echo = collections.deque(maxlen=echo_lines)
        self._keep_front = []
        self._front_mem = 0
        self._keep_tail = collections.deque()
        self._tail_mem = 0
        self.keep_gap = 0
        self._closed = False
        self._text = None

    def push(self, text):
        if not text:
            return
        if self.truncated:
            self._overflow(text)
            return
        block_width = _width(text)
        width = max(self._width, block_width)
        chars = self._chars + len(text)
        self._blocks.append(text)
        self._widths.append(block_width)
        if chars * width <= self._head_max:
            self._chars, self._width = chars, width
            return
        # 넘침 전환: 머리는 상한 안에서 끝나는 마지막 완결 줄까지(걸친 줄은 넣지 않는다 -- _head_limit). 한 번 join 하고
        # 블록을 놓는다.
        head, rest = _cut_blocks(self._blocks, _head_limit(self._blocks, self._widths, self._head_max))
        self._blocks, self._widths, self._chars = [], [], 0
        self._head = "".join(head)
        del head
        self.truncated = True
        self._seed_echo(self._head)
        for piece in rest:
            self._overflow(piece)

    def _seed_echo(self, head):
        """넘침 전환 때 머리의 마지막 줄들(최대 echo 링 길이, 줄마다 ECHO_LINE_MAX 자)로 echo 링을 먼저 채운다 -- 링에는 넘친
        구간의 줄만 들어가므로, 상한을 조금만 넘긴 출력의 실패 꼬리(runner._tool_failure_report)가 넘친 몇 줄로 줄지 않게
        (2026-11-26 리뷰). 머리는 완결 줄로 끝난다(_cut_blocks) -- 뒤에서부터 "\\n" 을 찾는다(머리 전체를 나누지 않는다)."""
        if not head:
            return
        lines = []
        pos = len(head) - 1 if head.endswith("\n") else len(head)
        while pos >= 0 and len(lines) < self._echo.maxlen:
            start = head.rfind("\n", 0, pos) + 1
            lines.append(head[start:min(pos, start + ECHO_LINE_MAX)])
            pos = start - 1
        self._echo.extend(reversed(lines))

    def _overflow(self, text):
        start = 0
        while True:
            nl = text.find("\n", start)
            if nl < 0:
                self._part(text, start, len(text))
                return
            self._part(text, start, nl)
            line, cut = "".join(self._parts), self._plen > self._line_max
            self._parts, self._plen = [], 0
            self._consume(line, cut)
            start = nl + 1

    def _part(self, text, a, b):
        if b <= a:
            return
        room = self._line_max - self._plen
        if room > 0:
            self._parts.append(text[a:min(b, a + room)])
        self._plen += b - a

    def _consume(self, line, cut):
        """넘친 구간의 완결 줄 하나. cut = line_max 를 넘어 앞부분만 남았다(카운터는 남은 머리로 센다)."""
        for name, rx in self._counter_rx:
            if rx.match(line) is not None:
                self.counters[name] += 1
        if (self._errno_max and not cut and len(self.errno_lines) < self._errno_max
                and parsers.errno_line(line)):
            self.errno_lines.append(line)
        if self._abort and not self.aborted_seen and parsers.abort_line(line):
            self.aborted_seen = True
        self._echo.append(line[:ECHO_LINE_MAX])
        if self._keep is not None and not cut and len(line) <= self._keep_line_max and self._keep(line):
            mem = (len(line) + 1) * _width(line)
            if self._front_mem < self._keep_half:
                self._keep_front.append(line)
                self._front_mem += mem
                return
            self._keep_tail.append((line, mem))
            self._tail_mem += mem
            while self._tail_mem > self._keep_half and self._keep_tail:
                _, old = self._keep_tail.popleft()
                self._tail_mem -= old
                self.keep_gap += 1

    def close(self):
        """개행 없이 끝난 마지막 줄도 한 줄로 센다(MULTILINE findall 도 그 줄을 센다)."""
        if self._closed:
            return
        self._closed = True
        if self.truncated and self._plen > 0:
            line, cut = "".join(self._parts), self._plen > self._line_max
            self._parts, self._plen = [], 0
            self._consume(line, cut)

    def text(self):
        """파서 입력. 넘침 전 = 원문 그대로(join 뒤 블록을 그 결과 하나로 바꿔 사본 두 벌을 오래 쥐지 않는다). 넘침 = 머리 +
        keep 앞 + keep 끝(원문의 부분 수열)."""
        if not self.truncated:
            if len(self._blocks) > 1:
                self._blocks = ["".join(self._blocks)]
                self._widths = [self._width]
            return self._blocks[0] if self._blocks else ""
        if self._text is None:
            keep = "".join(f"{ln}\n" for ln in self._keep_front) + "".join(f"{ln}\n" for ln, _ in self._keep_tail)
            self._text = self._head + keep
        return self._text

    def head_text(self):
        """넘침 때 머리만(runner._overflow_exact 의 머리 개수·_overflow_detail 의 표본 몫). 넘치지 않았으면 원문 전체."""
        return self._head if self.truncated else self.text()

    def echo_text(self):
        """실패 꼬리 발췌(runner._tool_failure_report)의 원천. 넘침 전 = 원문의 마지막 echo_src_max(줄 경계부터), 넘침 = echo 링
        (전환 때 머리 끝 줄로 먼저 채운다 -- _seed_echo, 출력 전체의 마지막 echo_lines 줄)."""
        if self.truncated:
            return "\n".join(self._echo)
        text = self.text()
        if len(text) <= self._echo_src_max:
            return text
        at = text.find("\n", len(text) - self._echo_src_max)
        return text[at + 1:] if at >= 0 else text[-self._echo_src_max:]


def stdout_feed(**limits):
    """stdout 파서 입력(카운터·실패 표본·keep)."""
    return LineFeed(head_max=limits.pop("head_max", PARSE_HEAD_MAX), keep=parsers.keep_line,
                    counters=parsers.LINE_COUNTERS, errno_samples=10, **limits)


def stderr_feed(**limits):
    """stderr 파서 입력(중단 흔적만 -- 파서가 stderr 에서 쓰는 것은 parse_aborted 하나다)."""
    return LineFeed(head_max=limits.pop("head_max", ERR_PARSE_HEAD_MAX), abort=True, **limits)


# ---- 로그 파일 크기 정책 ------------------------------------------------------------------------------------------------------

def _utf8_boundary(data, at):
    while at > 0 and (data[at] & 0xC0) == 0x80:
        at -= 1
    return at


class LogSink:
    """앞 head_max 바이트(지금 쓸 몫을 돌려준다) + 끝 tail_max 링 + 사이 GAP 한 줄(finish). 상태만 갖고 I/O 는 하지 않는다."""

    def __init__(self, head_max, tail_max):
        self.head_max = head_max
        self.tail_max = tail_max
        self.head_used = 0
        self.head_full = False
        self._head_nl = True
        self._ring = collections.deque()
        self._ring_bytes = 0
        self._omit_nl = True
        self.omitted_bytes = 0
        self.omitted_lines = 0

    def write(self, data):
        if not data:
            return b""
        if not self.head_full:
            room = self.head_max - self.head_used
            if len(data) <= room:
                self.head_used += len(data)
                self._head_nl = data.endswith(b"\n")
                return data
            cut = data.rfind(b"\n", 0, room) + 1
            if cut == 0 and (not self._head_nl or self.head_used == 0):
                # 상한 안에 줄 끝이 없다: 머리가 이미 줄 한가운데(또는 첫 줄부터 상한보다 길다)면 상한까지 채운다(문자 경계).
                # 줄 경계에서 끝났으면 이 조각은 넣지 않는다(머리는 완결 줄로 끝난다).
                cut = _utf8_boundary(data, room)
            head = data[:cut]
            self.head_used += len(head)
            self.head_full = True
            if head:
                self._head_nl = head.endswith(b"\n")
            self._ring_add(data[cut:])
            return head
        self._ring_add(data)
        return b""

    def _ring_add(self, data):
        if not data:
            return
        self._ring.append(data)
        self._ring_bytes += len(data)
        while self._ring and self._ring_bytes - len(self._ring[0]) >= self.tail_max:
            self._omit(self._ring.popleft())

    def _omit(self, chunk):
        self._ring_bytes -= len(chunk)
        self.omitted_bytes += len(chunk)
        self.omitted_lines += chunk.count(b"\n")
        self._omit_nl = chunk.endswith(b"\n")

    def finish(self):
        """머리 뒤에 붙일 바이트: 빠진 것이 없으면 링 그대로, 있으면 GAP 표식 + 줄 경계에서 시작하는 끝 링."""
        tail = b"".join(self._ring)
        self._ring.clear()
        self._ring_bytes = 0
        if self.omitted_bytes == 0 and len(tail) <= self.tail_max:
            return tail
        if len(tail) > self.tail_max:
            drop = len(tail) - self.tail_max
            self.omitted_bytes += drop
            self.omitted_lines += tail.count(b"\n", 0, drop)
            self._omit_nl = tail[drop - 1:drop] == b"\n"
            tail = tail[drop:]
        if not self._omit_nl:
            # 끝 링이 줄 한가운데서 시작한다 -- 첫 부분 줄을 버린다(그만큼 omitted 에 더한다).
            nl = tail.find(b"\n")
            cut = len(tail) if nl < 0 else nl + 1
            self.omitted_bytes += cut
            self.omitted_lines += 1 if nl >= 0 else 0
            tail = tail[cut:]
        marker = (f"{GAP_MARKER} reason=size omitted_lines={self.omitted_lines} omitted_bytes={self.omitted_bytes} "
                  f"head={self.head_max} tail={self.tail_max}\n").encode("ascii")
        return (b"" if self._head_nl else b"\n") + marker + tail


# ---- sink ------------------------------------------------------------------------------------------------------------------

class NullSink:
    """아무것도 하지 않는다(open_sink 팩토리가 예외를 낸 경우 -- 잡은 계속, 진행·로그 기록만 없다)."""

    def set_progress(self, data):
        pass

    def append(self, name, text):
        pass

    def begin_logs(self):
        pass

    def close(self):
        pass


class TextSink:
    """compat sink -- 메모리에 LogSink 규칙으로 모았다가 close 에서 write_text 로 stdout.log → stderr.log → progress.json 을
    한 번씩 쓴다(각 호출의 예외는 삼킨다). run_job 의 write_text 말고는 파일시스템을 만지지 않는다(기존 시험의 "/art" 호환)."""

    def __init__(self, write_text, artifact_dir, *, limits=None):
        self._write_text = write_text
        self._dir = artifact_dir
        self._logs = {name: (LogSink(*lim), []) for name, lim in (limits or LOG_LIMITS).items()}
        self._progress = None
        self._begun = False
        self._closed = False

    def set_progress(self, data):
        self._progress = data

    def begin_logs(self):
        self._begun = True

    def append(self, name, text):
        if not self._begun or self._closed or not text:
            return
        try:
            sink, parts = self._logs[name]
            head = sink.write(text.encode("utf-8", "replace"))
            if head:
                parts.append(head)
        except Exception:  # noqa: BLE001 -- T5
            pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._begun:
            for name, (sink, parts) in self._logs.items():
                try:
                    parts.append(sink.finish())
                    self._write_text(f"{self._dir}/{name}", b"".join(parts).decode("utf-8", "replace"))
                except Exception:  # noqa: BLE001 -- T5
                    pass
        if self._progress is not None:
            try:
                self._write_text(f"{self._dir}/{PROGRESS_FILE_NAME}", self._progress.decode("ascii"))
            except Exception:  # noqa: BLE001 -- T5
                pass


_O_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _write_all(fd, data, write=os.write):
    view = memoryview(data)
    while view:
        n = write(fd, view)
        view = view[n:]


def create_excl(dfd, name):
    """dirfd 안에 name 을 새 inode 로 만든다(미리 놓인 파일·심링크는 지운다 -- 심링크는 링크 자체만). fd 를 돌려준다."""
    try:
        os.unlink(name, dir_fd=dfd)
    except FileNotFoundError:
        pass
    fd = os.open(name, _O_NEW, 0o644, dir_fd=dfd)
    try:
        os.fchmod(fd, 0o644)
    except BaseException:
        os.close(fd)
        raise
    return fd


def replace_atomic(dfd, name, data, *, tmp=PROGRESS_TMP, write=os.write):
    """임시 파일(O_EXCL|O_NOFOLLOW, EEXIST 면 지우고 1회 재시도) → 쓰기 → fchmod 0644 → rename. 실패하면 임시 파일을 지우고
    예외를 올린다(호출자가 그 출력의 기록을 끈다)."""
    try:
        fd = os.open(tmp, _O_NEW, 0o644, dir_fd=dfd)
    except FileExistsError:
        os.unlink(tmp, dir_fd=dfd)
        fd = os.open(tmp, _O_NEW, 0o644, dir_fd=dfd)
    try:
        try:
            _write_all(fd, data, write)
            os.fchmod(fd, 0o644)
        finally:
            os.close(fd)
        os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        raise


class _LogFile:
    def __init__(self, sink):
        self.sink = sink
        self.fd = None
        self.pending = []
        self.pending_bytes = 0


class ArtifactWriter:
    """실 sink -- 쓰기 스레드 W 하나가 phase 디렉터리의 stdout.log·stderr.log·progress.json 을 쓴다(모듈 docstring).

    호출자(메인·리더 스레드)가 부르는 메서드(set_progress·append·begin_logs·close)는 잠깐의 잠금만 쥐고 I/O 를 하지 않으며
    예외를 내지 않는다."""

    @staticmethod
    def open(artifact_dir):
        """main 의 open_sink 팩토리 -- 예외 없음(스레드를 못 띄우면 NullSink). 디렉터리 만들기·열기는 쓰기 스레드가 한다(멈춘
        마운트가 run_job 본문을 세우지 않게)."""
        try:
            writer = ArtifactWriter(artifact_dir)
            writer.start()
            return writer
        except Exception:  # noqa: BLE001 -- 팩토리 계약(설계 D10)
            return NullSink()

    def __init__(self, artifact_dir, *, limits=None, queue_max=WRITER_QUEUE_MAX, flush_seconds=FLUSH_SECONDS,
                 flush_bytes=FLUSH_BYTES, budget_seconds=WRITER_LOG_BUDGET_SECONDS, close_seconds=CLOSE_SECONDS,
                 write=None, clock=time.monotonic):
        self._dir = artifact_dir
        self._limits = dict(limits or LOG_LIMITS)
        self._queue_max = queue_max
        self._flush_seconds = flush_seconds
        self._flush_bytes = flush_bytes
        self._budget = budget_seconds
        self._close_seconds = close_seconds
        self._write = write or os.write
        self._clock = clock
        self._cond = threading.Condition()
        # 호출자 쪽 상태(_cond 안에서만)
        self._progress = None
        self._progress_seq = 0
        self._queue = collections.deque()
        self._queue_mem = 0
        self._dropped = {}
        self._last_nl = {}
        self._begun = False
        self._closing = False
        self._dead = False
        # 쓰기 스레드 쪽 상태(W 만 만진다)
        self._dfd = None
        self._files = None
        self._progress_on = True
        self._thread = None

    def start(self):
        thread = threading.Thread(target=self._main, name="dms-jr-writer", daemon=True)
        _register(thread)
        thread.start()
        self._thread = thread

    # ---- 호출자 쪽 ------------------------------------------------------------------------------------------------------
    def set_progress(self, data):
        try:
            with self._cond:
                if self._dead or data is None:
                    return
                self._progress = data
                self._progress_seq += 1
                self._cond.notify_all()
        except Exception:  # noqa: BLE001 -- T5
            pass

    def begin_logs(self):
        try:
            with self._cond:
                self._begun = True
                self._cond.notify_all()
        except Exception:  # noqa: BLE001 -- T5
            pass

    def append(self, name, text):
        if not text:
            return
        try:
            with self._cond:
                if self._dead or self._closing or not self._begun:
                    return
                mem = len(text) * _width(text)
                if self._queue_mem + mem > self._queue_max:
                    # T7: 쓰기가 밀렸다 -- 리더를 세우지 않고 버린 양만 센다.
                    dropped = self._dropped.setdefault(name, [0, 0])
                    dropped[0] += text.count("\n")
                    dropped[1] += len(text)
                    return
                self._enqueue_gap(name)
                self._queue.append((name, text, mem))
                self._queue_mem += mem
                self._last_nl[name] = text.endswith("\n")
        except Exception:  # noqa: BLE001 -- T5
            pass

    def _enqueue_gap(self, name):
        dropped = self._dropped.pop(name, None)
        if dropped is None:
            return
        marker = (("" if self._last_nl.get(name, True) else "\n")
                  + f"{GAP_MARKER} reason=backlog omitted_lines={dropped[0]} omitted_chars={dropped[1]}\n")
        self._queue.append((name, marker, len(marker)))
        self._queue_mem += len(marker)
        self._last_nl[name] = True

    def close(self, timeout=None):
        """남은 큐 → 끝 링 + GAP → 마지막 progress 를 쓰게 하고 기다린다(최대 timeout). 죽은 쓰기 스레드는 기다리지 않는다."""
        try:
            with self._cond:
                if not self._dead:
                    for name in list(self._dropped):
                        self._enqueue_gap(name)
                self._closing = True
                self._cond.notify_all()
            thread = self._thread
            if thread is not None and thread.is_alive():
                thread.join(self._close_seconds if timeout is None else timeout)
        except Exception:  # noqa: BLE001 -- T5
            pass

    # ---- 쓰기 스레드 W --------------------------------------------------------------------------------------------------
    def _main(self):
        try:
            try:
                os.makedirs(self._dir, exist_ok=True)
                self._dfd = os.open(self._dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                                    | getattr(os, "O_CLOEXEC", 0))
            except BaseException:
                self._die()
                return
            self._loop()
        except BaseException:
            self._die()
        finally:
            self._release()

    def _die(self):
        with self._cond:
            self._dead = True
            self._queue.clear()
            self._queue_mem = 0

    def _release(self):
        for lf in (self._files or {}).values():
            if lf.fd is not None:
                _quiet(os.close, lf.fd)
                lf.fd = None
        if self._dfd is not None:
            _quiet(os.close, self._dfd)
            self._dfd = None

    def _loop(self):
        written = 0
        last_flush = self._clock()
        while True:
            with self._cond:
                if self._progress_seq == written and not self._queue and not self._closing:
                    self._cond.wait(TICK_SECONDS)
                seq, data, begun, closing = self._progress_seq, self._progress, self._begun, self._closing
            if seq != written:
                written = seq
                self._write_progress(data)
            if begun and self._files is None:
                self._open_logs()
            deadline = self._clock() + self._budget
            while True:
                with self._cond:
                    if not self._queue or (self._progress_seq != written and not closing):
                        break
                    name, text, mem = self._queue.popleft()
                    self._queue_mem -= mem
                self._log(name, text)
                if not closing and self._clock() >= deadline:
                    break
            now = self._clock()
            if closing or now - last_flush >= self._flush_seconds:
                self._flush_all()
                last_flush = now
            if closing:
                with self._cond:
                    empty = not self._queue
                    seq, data = self._progress_seq, self._progress
                if empty:
                    self._finish_logs()
                    if seq != written:
                        self._write_progress(data)
                    return

    def _write_progress(self, data):
        if not self._progress_on or data is None:
            return
        try:
            replace_atomic(self._dfd, PROGRESS_FILE_NAME, data, write=self._write)
        except Exception:  # noqa: BLE001 -- 진행 기록만 끈다(디렉터리·EISDIR·ENOSPC 등)
            self._progress_on = False

    def _open_logs(self):
        self._files = {}
        for name, (head, tail) in self._limits.items():
            lf = _LogFile(LogSink(head, tail))
            try:
                lf.fd = create_excl(self._dfd, name)
            except Exception:  # noqa: BLE001 -- 그 로그만 끈다
                lf.fd = None
            self._files[name] = lf

    def _log(self, name, text):
        if self._files is None:
            # 큐에 든 블록은 begin_logs 뒤에만 생긴다(append·_enqueue_gap 가 _begun 을 본다) -- 그러니 여기서 열어도 된다. _loop 의
            # begun 스냅숏과 큐 pop 은 한 잠금이 아니라, 그 틈에 begin_logs + append 가 끼면 로그가 열리기 전에 첫 블록을 꺼낸다
            # (2026-11-26 리뷰 -- 예전엔 그 블록을 조용히 버렸다).
            self._open_logs()
        lf = self._files.get(name)
        if lf is None or lf.fd is None:
            return
        try:
            head = lf.sink.write(text.encode("utf-8", "replace"))
            if head:
                lf.pending.append(head)
                lf.pending_bytes += len(head)
            if lf.pending_bytes >= self._flush_bytes:
                self._flush(lf)
        except Exception:  # noqa: BLE001 -- 그 로그만 끈다
            self._drop(lf)

    def _flush(self, lf):
        if lf.fd is None or not lf.pending:
            return
        data = b"".join(lf.pending)
        lf.pending, lf.pending_bytes = [], 0
        try:
            _write_all(lf.fd, data, self._write)
        except Exception:  # noqa: BLE001 -- 그 로그만 끈다
            self._drop(lf)

    def _flush_all(self):
        for lf in (self._files or {}).values():
            self._flush(lf)

    def _drop(self, lf):
        if lf.fd is not None:
            _quiet(os.close, lf.fd)
        lf.fd = None
        lf.pending, lf.pending_bytes = [], 0

    def _finish_logs(self):
        for lf in (self._files or {}).values():
            if lf.fd is None:
                continue
            try:
                tail = lf.sink.finish()
                if tail:
                    lf.pending.append(tail)
                self._flush(lf)
            except Exception:  # noqa: BLE001
                self._drop(lf)
            if lf.fd is not None:
                _quiet(os.close, lf.fd)
                lf.fd = None


# ---- 진행 스냅숏 -----------------------------------------------------------------------------------------------------------

def publish(sink, tracker, *, phase=None, ended=False):
    """스로틀 없이 지금 스냅숏을 쓴다(run_job 맨 앞 workers, mpirun 직전 start, 끝 ended). tracker None(sync 아닌 도구)이면
    아무것도 하지 않는다. 예외는 삼킨다(T5)."""
    if tracker is None:
        return
    try:
        if phase is not None:
            tracker.set_phase(phase)
        if ended:
            tracker.end()
        data = tracker.snapshot_bytes()
        if data is not None:
            sink.set_progress(data)
    except Exception:  # noqa: BLE001 -- T5
        pass


# ---- 스트림(리더 쪽 상태) ----------------------------------------------------------------------------------------------------

class _Stream:
    def __init__(self, name, feed, sink, tracker):
        self.name = name
        self.feed = feed
        self.sink = sink
        self.tracker = tracker
        self.lock = threading.Lock()
        self.stopped = False
        self.feed_ok = True
        self.tracker_ok = tracker is not None

    def deliver(self, text):
        """디코드한 블록 하나를 소비자에게(T2·T4) -- 각 소비자의 예외는 그 소비자만 끈다."""
        with self.lock:
            if self.stopped:
                return
            if self.feed_ok:
                try:
                    self.feed.push(text)
                except BaseException:  # noqa: BLE001 -- 리더 스레드: MemoryError·버그
                    self.feed_ok = False
            if self.tracker_ok:
                try:
                    self.tracker.feed(text)
                except BaseException:  # noqa: BLE001
                    self.tracker_ok = False
            try:
                self.sink.append(self.name, text)
            except BaseException:  # noqa: BLE001
                pass

    def tick(self):
        """메인 tick: 쓸 때가 됐으면 스냅숏 bytes 를 꺼내 진행 슬롯에(T4 -- 직렬화만 잠금 안)."""
        if not self.tracker_ok:
            return
        try:
            with self.lock:
                if self.stopped or not self.tracker.due():
                    return
                data = self.tracker.snapshot_bytes()
            if data is not None:
                self.sink.set_progress(data)
        except Exception:  # noqa: BLE001 -- T5
            self.tracker_ok = False

    def stop(self):
        with self.lock:
            self.stopped = True
            if self.feed_ok:
                try:
                    self.feed.close()
                except BaseException:  # noqa: BLE001
                    self.feed_ok = False
            if self.tracker_ok:
                try:
                    self.tracker.finish()
                except BaseException:  # noqa: BLE001
                    self.tracker_ok = False


def _read_loop(source, stream, read_size):
    """리더 스레드(T2·T3): EOF(또는 OSError)까지 원시 fd 를 읽어 증분 디코드한다. 아티팩트 파일을 만지지 않는다(T7 -- 닫는
    것은 자기 파이프뿐이다).

    source 는 파이프의 파일 객체(Popen(bufsize=0) 의 FileIO)다 -- 정수 fd 만 넘기면 run_streaming 이 반환한 뒤 Popen 과 함께 그
    FileIO 가 GC 로 닫히고, 아직 막혀 있던 리더의 다음 os.read 가 같은 번호로 다시 열린 다른 파일을 겨냥한다(2026-11-26 리뷰
    실측). 이 프레임(과 Thread 의 args)이 객체를 쥐고 있어 리더가 끝날 때까지 fd 가 살아 있다. 끝나면 리더가 그 파이프를 직접
    닫는다(마지막 read 뒤라 그 fd 를 읽는 쪽이 없다 -- run_streaming 의 닫기와 겹쳐도 close 는 멱등이다). 정수 fd 도 받는다
    (시험·도구 -- 그때는 닫지 않는다)."""
    fd = source if isinstance(source, int) else source.fileno()
    decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder("utf-8")(errors="replace"), translate=True)
    try:
        while True:
            try:
                raw = os.read(fd, read_size() if callable(read_size) else read_size)
            except BaseException:  # noqa: BLE001 -- 더 읽을 수 없다 = EOF
                raw = b""
            final = not raw
            try:
                text = decoder.decode(raw, final=final)
            except BaseException:  # noqa: BLE001
                text = ""
            if text:
                stream.deliver(text)
            if final:
                return
    finally:
        if not isinstance(source, int):
            _quiet(source.close)


def _finish(returncode, out, err, sink, tracker):
    out.stop()
    err.stop()
    if out.tracker_ok:
        publish(sink, tracker, ended=True)
    _quiet(sink.close)
    return _capture(returncode, out, err)


def _capture(returncode, out, err):
    def safe(stream, method):
        if not stream.feed_ok:
            return ""
        try:
            return getattr(stream.feed, method)()
        except Exception:  # noqa: BLE001
            stream.feed_ok = False
            return ""

    stdout, stdout_echo = safe(out, "text"), safe(out, "echo_text")
    stderr, stderr_echo = safe(err, "text"), safe(err, "echo_text")
    out_cut = out.feed_ok and out.feed.truncated
    err_abort = err.feed_ok and err.feed.truncated and err.feed.aborted_seen
    overflow = None
    if not out.feed_ok or out_cut or err_abort:
        overflow = {"truncated": bool(out_cut), "keep_gap": out.feed.keep_gap if out_cut else 0,
                    "parse_lost": not out.feed_ok, "head_text": out.feed.head_text() if out_cut else None,
                    "counters": dict(out.feed.counters) if out_cut else {},
                    "errno_lines": list(out.feed.errno_lines) if out_cut else [], "aborted_seen": bool(err_abort)}
    return ToolCapture(returncode, stdout, stderr, stdout_echo, stderr_echo, overflow)


def run_streaming(command, *, sink, tracker, clock=None, popen=None, read_size=READ_SIZE, tick_seconds=TICK_SECONDS,
                  join_seconds=JOIN_SECONDS, feeds=None):
    """main 의 run_tool -- mpirun 을 Popen 으로 돌리며 출력을 스트리밍한다(모듈 docstring) → ToolCapture.

    returncode 는 proc.wait() 그대로(신호로 죽으면 음수 -- subprocess.run 과 같은 뜻). clock 은 run_job 의 시계(Tracker 가
    이미 쥐고 있다 -- 계약상 받아 둔다). feeds = (stdout LineFeed, stderr LineFeed) 주입(시험의 축소 상한)."""
    out_feed, err_feed = feeds if feeds is not None else (stdout_feed(), stderr_feed())
    out = _Stream(STDOUT_LOG, out_feed, sink, tracker)
    err = _Stream(STDERR_LOG, err_feed, sink, None)
    # 로그 파일은 mpirun 시작 때 만든다 -- 조기 종료 경로(워커 미준비·봉쇄)에 빈 stdout.log 칩이 생기지 않게.
    _quiet(sink.begin_logs)
    publish(sink, tracker, phase="start")
    proc = (popen or subprocess.Popen)(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    readers = []
    for fileobj, stream in ((proc.stdout, out), (proc.stderr, err)):
        # 정수 fd 가 아니라 파일 객체를 넘긴다(_read_loop docstring -- 리더가 끝날 때까지 FileIO 가 GC 로 닫히지 않게).
        thread = threading.Thread(target=_read_loop, args=(fileobj, stream, read_size),
                                  name=f"dms-jr-{stream.name}", daemon=True)
        _register(thread)
        thread.start()
        readers.append((thread, fileobj))
    while True:
        try:
            returncode = proc.wait(timeout=tick_seconds)
            break
        except subprocess.TimeoutExpired:
            out.tick()
    for thread, _ in readers:
        thread.join(join_seconds)
    for thread, fileobj in readers:
        # 리더가 끝났을 때만 닫는다 -- 막힌 os.read 아래에서 fd 를 닫으면 그 번호가 다른 파일에 재사용될 수 있다. 살아 있는 리더는
        # 자기 파일 객체를 쥐고 있어(_read_loop) 이 함수가 반환해 Popen 이 사라져도 GC 가 그 fd 를 닫지 않는다.
        if not thread.is_alive():
            _quiet(fileobj.close)
    return _finish(returncode, out, err, sink, tracker)


def _chunks(text, size=COMPAT_CHUNK):
    for at in range(0, len(text), size):
        yield text[at:at + size]


def compat(run, *, feeds=None):
    """주입 run(시험의 가짜 실행)을 그대로 쓰는 run_tool -- mpirun 은 여전히 run 으로 돌고(시험이 rec.ran 에서 명령을 찾는다),
    결과 문자열을 64 Ki 문자 조각으로 run_streaming 과 **같은** LineFeed·Tracker·sink 에 흘린다."""
    def run_tool(command, *, sink, tracker, clock=None):
        out_feed, err_feed = feeds if feeds is not None else (stdout_feed(), stderr_feed())
        out = _Stream(STDOUT_LOG, out_feed, sink, tracker)
        err = _Stream(STDERR_LOG, err_feed, sink, None)
        _quiet(sink.begin_logs)
        publish(sink, tracker, phase="start")
        proc = run(command)
        stdout = getattr(proc, "stdout", None) or ""
        stderr = getattr(proc, "stderr", None) or ""
        for chunk in _chunks(stdout):
            out.deliver(chunk)
        for chunk in _chunks(stderr):
            err.deliver(chunk)
        return _finish(proc.returncode, out, err, sink, tracker)
    return run_tool
