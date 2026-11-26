"""러너 출력 스트리밍(dms_job_runner.stream -- 2026-11-26 sync 진행률 설계 §1·§2) 시험. 번호는 설계 §7.1 표.

R1 디코드 동치(실제 자식 프로세스) · R3 원자 교체·속성 · R4 심링크 함정 · R5 SIGKILL 때 부분 출력 · R7 넘침 모드 · R8 로그 상한·
메모리 · R9 쓰기 백로그·진행 우선 · R10 리더 join 상한·종료 · R11 7b·9 회귀 · R12 주입 경계 · R14 fail-soft 주입 · R15 조기 종료.
(R2 스로틀은 tests/test_job_runner_progress.py, R6 파서 입력 동치는 표본이 있는 tests/test_job_runner_tool_detail.py·
test_job_runner_runner.py 에 덧붙였다 -- 시험 파일끼리 import 하지 않는다.)"""
import errno
import functools
import json
import os
import random
import signal
import stat
import subprocess
import sys
import textwrap
import threading
import time
import tracemalloc
from pathlib import Path

import pytest

from dms.artifact_files import NAME_RE
from dms_job_runner import parsers, progress, runner, stream
from dms_job_runner.runner import _build_summary, run_job

SRC = str(Path(__file__).resolve().parent.parent / "src")
TS = "[2026-11-26T08:29:09] "
ERR = TS + "[0] [/m/src/common/mfu_flist_copy.c:2256] ERROR: "


def _py(code, *args):
    return [sys.executable, "-c", code, *args]


class _PipePopen:
    """가짜 Popen -- 실제 파이프(os.pipe)로 정해진 바이트를 조각조각 흘린다(리더 경로는 진짜, 프로세스는 없다)."""

    def __init__(self, out=b"", err=b"", rc=0, chunk=4096):
        self._out, self._err, self._rc, self._chunk = out, err, rc, chunk
        self.calls = []

    def __call__(self, command, stdout=None, stderr=None, bufsize=None):
        self.calls.append((command, stdout, stderr, bufsize))
        r1, w1 = os.pipe()
        r2, w2 = os.pipe()
        self.stdout = os.fdopen(r1, "rb", buffering=0)
        self.stderr = os.fdopen(r2, "rb", buffering=0)
        self._thread = threading.Thread(target=self._feed, args=(w1, w2), daemon=True)
        self._thread.start()
        return self

    def _feed(self, w1, w2):
        for data, w in ((self._out, w1), (self._err, w2)):
            for at in range(0, len(data), self._chunk):
                os.write(w, data[at:at + self._chunk])
            os.close(w)

    def wait(self, timeout=None):
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise subprocess.TimeoutExpired("fake", timeout)
        return self._rc


def _streaming(out_text, err_text="", rc=0, *, sink=None, tracker=None, feeds=None, chunk=4096):
    fake = _PipePopen(out_text.encode("utf-8"), err_text.encode("utf-8"), rc, chunk)
    return stream.run_streaming(["mpirun"], sink=sink or stream.NullSink(), tracker=tracker, popen=fake, feeds=feeds)


def _compat(out_text, err_text="", rc=0, *, sink=None, tracker=None, feeds=None):
    class R:
        returncode, stdout, stderr = rc, out_text, err_text
    return stream.compat(lambda cmd: R(), feeds=feeds)(["mpirun"], sink=sink or stream.NullSink(), tracker=tracker)


@pytest.fixture(autouse=True)
def _no_threads_left_behind():
    # 이 파일의 시험이 남긴 리더·쓰기 스레드가 다른 시험(main 의 exit_process)을 os._exit 로 몰지 않게.
    yield
    deadline = time.monotonic() + 5
    while stream.threads_left() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not stream.threads_left()


# ---- R1 스트리밍 디코드 동치 --------------------------------------------------------------------------------------------------

_CHILD = textwrap.dedent("""
    import os, random, sys, time
    out, err, seed = open(sys.argv[1], "rb").read(), open(sys.argv[2], "rb").read(), int(sys.argv[3])
    rnd = random.Random(seed)
    for data, fd in ((out, 1), (err, 2)):
        at = 0
        while at < len(data):
            n = rnd.choice((1, 2, 3, 5, 7, 64, 4096, 70000))
            os.write(fd, data[at:at + n])
            at += n
    sys.exit(int(sys.argv[4]))
""")


def _payload(rnd, big_line):
    pieces = [b"ascii line\n", b"\xb4\xd9\xbf\xee\xb7\xce\xb5\xe5\n", b"crlf\r\n", b"lone\rcr\n", b"\r", b"\n",
              "가나다\n".encode(), b"\xea\xb0", b"\x80tail\n", b"\xc2\x85nel\n", b"\xe2\x80\xa8ls\n", b"\x0cff\x0bvt\n",
              b"\x1c\x1d\x1e\n", b"\xff\xfe broken\n", "\U0001F600 비BMP\n".encode(), b"\xf0\x9f", b"\x98\x80\n",
              b"\xe2\x80", b"\r\n"]
    data = b"".join(rnd.choice(pieces) for _ in range(400))
    if big_line:
        data += b"[2026-11-26T00:00:00] " + b"x" * (1024 * 1024 + 7) + b"\r\n"
    return data + b"end without newline \xe2\x80"


@pytest.mark.parametrize("seed,read_size,big", [(1, "random", False), (2, "random", False), (3, 65536, True),
                                                (4, 65536, False)])
def test_streaming_decode_equals_subprocess_run_text_mode(tmp_path, seed, read_size, big):
    rnd = random.Random(seed)
    out_path, err_path = tmp_path / "o", tmp_path / "e"
    out_path.write_bytes(_payload(rnd, big))
    err_path.write_bytes(_payload(rnd, False))
    cmd = _py(_CHILD, str(out_path), str(err_path), str(seed), "3")
    want = runner._subprocess_run(cmd)
    sizes = random.Random(seed + 100)
    size = (lambda: sizes.randint(1, 7)) if read_size == "random" else read_size
    sink = stream.ArtifactWriter(str(tmp_path / "art"))
    sink.start()
    cap = stream.run_streaming(cmd, sink=sink, tracker=None, read_size=size)
    assert cap.returncode == want.returncode == 3
    assert cap.stdout == want.stdout and cap.stderr == want.stderr
    assert cap.overflow is None
    # 로그 파일은 같은 텍스트의 UTF-8(상한 안 -- 그대로)이다.
    assert (tmp_path / "art" / "stdout.log").read_bytes() == want.stdout.encode("utf-8")
    assert (tmp_path / "art" / "stderr.log").read_bytes() == want.stderr.encode("utf-8")


def test_popen_is_raw_unbuffered_pipes():
    fake = _PipePopen(b"x\n", b"", 0)
    stream.run_streaming(["mpirun", "-x"], sink=stream.NullSink(), tracker=None, popen=fake)
    command, out, err, bufsize = fake.calls[0]
    assert command == ["mpirun", "-x"] and out is subprocess.PIPE and err is subprocess.PIPE and bufsize == 0


def test_compat_and_streaming_agree_and_preserve_the_text():
    text = "".join(f"{TS}line {i} 가\n" for i in range(5000)) + "no newline"
    a, b = _compat(text, "err\n", 2), _streaming(text, "err\n", 2, chunk=777)
    assert a == b
    assert a.stdout == text and a.stderr == "err\n" and a.returncode == 2 and a.overflow is None
    assert a.stdout_echo == text and a.stderr_echo == "err\n"


# ---- R3 원자 교체·속성 ------------------------------------------------------------------------------------------------------

def test_progress_file_is_replaced_atomically_with_safe_attributes(tmp_path):
    art = tmp_path / "art"
    w = stream.ArtifactWriter(str(art))
    w.start()
    seen, stop = [], threading.Event()

    def reader():
        while not stop.is_set():
            try:
                seen.append(json.loads((art / "progress.json").read_bytes()))
            except FileNotFoundError:
                pass

    t = threading.Thread(target=reader)
    t.start()
    for i in range(300):
        w.set_progress(json.dumps({"v": 1, "n": i, "pad": "x" * 2000}).encode())
        time.sleep(0.001)
    w.close(timeout=5)
    stop.set()
    t.join()
    assert seen                                                   # 늘 파싱된다(반쪽 JSON 을 본 적이 없다 -- 위 loads)
    st = os.lstat(art / "progress.json")
    assert stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o644
    assert st.st_uid == os.geteuid()
    assert json.loads((art / "progress.json").read_bytes())["n"] == 299      # 마지막 슬롯이 남는다
    assert not (art / stream.PROGRESS_TMP).exists()
    assert NAME_RE.fullmatch(stream.PROGRESS_TMP) is None          # 임시 파일은 목록·API 에 뜰 수 없는 이름


def test_log_files_are_fresh_0644_inodes(tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    old = art / "stdout.log"
    old.write_text("stale")
    old.chmod(0o600)
    os.link(old, tmp_path / "hard")                                # 미리 놓인 하드링크(nlink 2)
    old_umask = os.umask(0o077)                                    # umask 와 무관하게 0644(fchmod)
    try:
        cap = _streaming("hello\n", sink=_open(art))
    finally:
        os.umask(old_umask)
    assert cap.stdout == "hello\n"
    st = os.lstat(art / "stdout.log")
    assert st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o644 and (art / "stdout.log").read_text() == "hello\n"
    assert (tmp_path / "hard").read_text() == "stale"             # 옛 inode 는 건드리지 않았다


def _open(path, **kw):
    w = stream.ArtifactWriter(str(path), **kw)
    w.start()
    return w


# ---- R4 심링크 함정 ---------------------------------------------------------------------------------------------------------

def test_pre_placed_symlinks_are_replaced_not_followed(tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    outside = {n: tmp_path / f"outside-{n}" for n in ("stdout.log", "progress.json", stream.PROGRESS_TMP)}
    for name, target in outside.items():
        target.write_text("keep")
        (art / name).symlink_to(target)
    sink = _open(art)
    tracker = progress.Tracker("dsync", clock=lambda: 0.0)
    stream.publish(sink, tracker, phase="workers")
    _streaming(TS + "Walking source path\n", sink=sink, tracker=tracker)
    assert all(t.read_text() == "keep" for t in outside.values())
    assert not os.path.islink(art / "stdout.log") and (art / "stdout.log").read_text() == TS + "Walking source path\n"
    assert not os.path.islink(art / "progress.json")
    snap = json.loads((art / "progress.json").read_bytes())
    assert snap["ended"] is True and snap["phase"] == "walk_src"


def test_progress_path_that_is_a_directory_turns_off_only_progress(tmp_path):
    art = tmp_path / "art"
    (art / "progress.json").mkdir(parents=True)
    sink = _open(art)
    tracker = progress.Tracker("dsync", clock=lambda: 0.0)
    cap = _streaming(TS + "Copying data.\n", sink=sink, tracker=tracker)
    assert cap.returncode == 0 and (art / "progress.json").is_dir()
    assert (art / "stdout.log").read_text() == TS + "Copying data.\n"
    assert not (art / stream.PROGRESS_TMP).exists()


def _fake_run(stdout, rc=0, stderr=""):
    class R:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def run(cmd):
        if cmd[0] == "getent":
            return R(0, "10.0.0.1   w\n")
        if cmd[0] in ("ssh", "sh", "chown"):
            return R(0)
        return R(rc, stdout, stderr)
    return run


def _env(tool, art, argv=("/c/m/a", "/c/m/b")):
    return {"DMS_JR_TOOL": tool, "DMS_JR_PROCESS_COUNT": "1", "DMS_JR_PROCESSES_PER_NODE": "1", "DMS_JR_UID": "10001",
            "DMS_JR_GID": "10000", "DMS_JR_USERNAME": "alice", "DMS_JR_ARTIFACT_DIR": str(art),
            "DMS_JR_SOURCE_NODES": json.dumps(["w"]), "DMS_JR_DEST_NODES": json.dumps(["w"]),
            "DMS_JR_ARGV": json.dumps(list(argv))}


class _Writes:
    def __init__(self):
        self.writes, self.order = {}, []

    def write_text(self, path, content, *, append=False):
        self.writes[path] = content
        self.order.append(path)


def _after_mpirun(out):
    lines = out.splitlines()
    ready = max(i for i, line in enumerate(lines) if line.startswith("DMS_JR_WORKER_READY"))
    return lines[ready + 1:]


DSYNC_OK = (f"{TS}Walking source path\n{TS}Walked 2 items in 0.001 seconds (2.0 items/sec)\n"
            f"{TS}Walking destination path\n{TS}Walked 0 items in 0.001 seconds (0.0 items/sec)\n"
            f"{TS}Copying items to destination\n{TS}Creating 1 files.\n{TS}Copying data.\n"
            f"{TS}Seconds: 0.049\n{TS}Items: 2\n{TS}  Directories: 1\n{TS}  Files: 1\n{TS}Data: 5.000 B (5 bytes)\n"
            f"{TS}Updating timestamps on newly copied files\n{TS}Completed sync\n")


def test_phase_dir_that_cannot_be_opened_turns_off_all_recording_but_not_the_job(tmp_path, capsys):
    blocker = tmp_path / "art"
    blocker.write_text("a file where the phase directory should be")
    rec = _Writes()
    rc = run_job(_env("dsync", blocker), run=_fake_run(DSYNC_OK), write_text=rec.write_text, read_text=lambda p: "",
                 sleep=lambda s: None, wait_hostfile=lambda role=None: (["w"], "/h"),
                 open_sink=stream.ArtifactWriter.open)
    assert rc == 0
    assert json.loads(rec.writes[f"{blocker}/summary.json"])["files"] == 2
    assert _after_mpirun(capsys.readouterr().out) == []           # sink 는 launcher stdout 에 아무것도 찍지 않는다(T5)
    assert blocker.read_text().startswith("a file")


# ---- R5 SIGKILL 때 부분 출력 ----------------------------------------------------------------------------------------------------

_KILL_PARENT = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, sys.argv[1])
    from dms_job_runner import progress, stream
    sink = stream.ArtifactWriter.open(sys.argv[2])
    tracker = progress.Tracker("dsync", clock=time.monotonic)
    stream.publish(sink, tracker, phase="workers")
    child = ("import sys, time\\n"
             "for i in range(50):\\n"
             "    sys.stdout.write('[2026-11-26T08:29:09] Walking source path\\\\n' if i == 0 else\\n"
             "                     '[2026-11-26T08:29:09] Copying data.\\\\n' if i == 1 else f'line {i}\\\\n')\\n"
             "    sys.stdout.flush()\\n"
             "time.sleep(60)\\n")
    stream.run_streaming([sys.executable, "-c", child], sink=sink, tracker=tracker)
    open(sys.argv[2] + "/summary.json", "w").write("{}")
""")


def test_partial_output_survives_sigkill(tmp_path):
    art = tmp_path / "art"
    proc = subprocess.Popen(_py(_KILL_PARENT, SRC, str(art)), start_new_session=True)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                log = (art / "stdout.log").read_text()
                snap = json.loads((art / "progress.json").read_bytes())
            except (FileNotFoundError, ValueError):
                log, snap = "", {}
            if "line 49\n" in log and snap.get("phase") == "copy":
                break
            time.sleep(0.1)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    log = (art / "stdout.log").read_text()
    assert log.startswith(TS + "Walking source path\n" + TS + "Copying data.\nline 2\n") and log.endswith("line 49\n")
    snap = json.loads((art / "progress.json").read_bytes())
    assert snap["phase"] == "copy" and snap["ended"] is False      # 어디서 멈췄는지 남는다
    assert not (art / "summary.json").exists()


# ---- R7 넘침 모드 -----------------------------------------------------------------------------------------------------------

def _small_feeds(head=4096, keep_half=1024, err_head=2048):
    return (stream.stdout_feed(head_max=head, keep_half=keep_half), stream.stderr_feed(head_max=err_head))


def _run_overflow(tool, out, rc=0, err="", argv=("/c/m/a", "/c/m/b"), feeds=None, art="/art"):
    rec = _Writes()
    got = run_job(_env(tool, art, argv), run=_fake_run(out, rc, err), write_text=rec.write_text,
                  read_text=lambda p: "", sleep=lambda s: None, wait_hostfile=lambda role=None: (["w"], "/h"),
                  run_tool=stream.compat(_fake_run(out, rc, err), feeds=feeds or _small_feeds()))
    return got, json.loads(rec.writes[f"{art}/summary.json"]), rec


def _full(tool, out, rc=0, err="", dryrun=False):
    return _build_summary(tool, out, rc, "/x", dryrun=dryrun, stderr=err)


_WALKS = (f"{TS}Walking source path\n{TS}Walked 3000 items in 0.1 seconds (1.0 items/sec)\n"
          f"{TS}Walking destination path\n{TS}Walked 0 items in 0.001 seconds (0.0 items/sec)\n"
          f"{TS}Copying items to destination\n")
_FINAL = (f"{TS}Seconds: 9.000\n{TS}Items: 3000\n{TS}  Directories: 1\n{TS}  Files: 2999\n"
          f"{TS}Data: 1.000 MiB (1048576 bytes)\n{TS}Updating timestamps on newly copied files\n{TS}Completed sync\n")


def _copy_fail(i, errno_text="13 Permission denied"):
    return f"{ERR}Failed to copy `/s/f{i}' to `/d/f{i}' (errno={errno_text})\n"


def test_overflow_dsync_execution_keeps_exact_counters_and_drops_positional_counts():
    fails = "".join(_copy_fail(i) for i in range(2000))
    creates = "".join(f"{ERR}Create `/d/dir{i}' mkdir() failed (errno=13 Permission denied)\n" for i in range(30))
    out = _WALKS + fails + creates + _FINAL
    rc, s, _ = _run_overflow("dsync", out, rc=1)
    full = _full("dsync", out, 1)
    assert rc == 1
    assert s["output_truncated"] is True and s["files"] is None and "copied_items" not in s
    assert (s["copy_failed"], s["create_failed"], s["tool_errors"]) == (2000, 30, 2030)
    assert (s["copy_failed"], s["create_failed"], s["tool_errors"]) == (
        full["copy_failed"], full["create_failed"], full["tool_errors"])
    assert s["bytes"] == s["copied_bytes"] == 1048576 == full["bytes"]
    assert s["failure_samples"] == full["failure_samples"]          # 출력 전체의 첫 10개
    assert s["reason"] == "sync_items_failed"


def test_overflow_fake_walk_line_stays_unknown_like_the_full_text():
    fails = "".join(_copy_fail(i) for i in range(500))
    fake = f"{ERR}Failed to copy `/s/x\n{TS}Walked 1 items in 0.0 seconds (1.0 items/sec)\n' to `/d/x' (errno=5 x)\n"
    out = _WALKS + fails + fake + _FINAL
    _, s, _ = _run_overflow("dsync", out, rc=1)
    assert _full("dsync", out, 1)["bytes"] is None and s["bytes"] is None
    pre = _WALKS + fails + fake
    _, p, _ = _run_overflow("dsync", pre, argv=("--dryrun", "/c/m/a", "/c/m/b"))
    assert _full("dsync", pre, 0, dryrun=True).get("source_items") is None and "source_items" not in p


def test_overflow_drm_counts_come_from_the_tail():
    fails = "".join(f"{ERR}Failed to unlink `/t/f{i}' (errno=13 Permission denied)\n" for i in range(1500))
    out = (f"{TS}Walking /t\n{TS}Walked 2000 items in 0.1 seconds (1.0 items/sec)\n{TS}Removing 2000 items\n" + fails
           + f"{TS}Removed 500 items in 2.000 seconds (250.000 items/sec)\n"
           + f"{TS}[0] [mfu_flist_remove.c:9] ERROR: Remove failed for 1500 of 2000 items\n")
    rc, s, _ = _run_overflow("drm", out, rc=1, argv=("/t",))
    full = _full("drm", out, 1)
    assert (s["files"], s["removed"], s["remove_failed"]) == (500, 500, 1500) == (
        full["files"], full["removed"], full["remove_failed"])
    assert s["tool_errors"] == full["tool_errors"] == 1501 and s["output_truncated"] is True
    assert s["reason"] == "rm_items_failed"


def test_overflow_head_full_of_item_warnings_still_reports_destination_full():
    warns = "".join(f"{TS}Original file exists, skip the creation: `/d/w{i}'\n" for i in range(400))
    enospc = f"{ERR}Write error when copying from `/s/big' to `/d/big' (errno=28 No space left on device)\n"
    out = _WALKS + warns + enospc + _FINAL
    rc, s, rec = _run_overflow("dsync", out, rc=1)
    assert s["reason"] == "destination_full" and s["bytes"] == 1048576
    assert s["failure_samples"][0]["errno"] == 28
    # 넘친 구간의 항목 WARN 줄(백틱 인용)은 파서 입력에 남지 않는다.
    feeds = _small_feeds()
    cap = _compat(out, rc=1, feeds=feeds)
    assert cap.overflow["truncated"] and "skip the creation" not in cap.stdout[len(cap.overflow["head_text"]):]


def test_overflow_dsync_preview_keeps_the_fingerprint_counts_exact():
    walk_errs = "".join(f"{TS}[0] [mfu_flist_walk.c:1] ERROR: Failed to stat: '/s/p{i}' (errno=13 x)\n" for i in range(800))
    out = (f"{TS}Walking source path\n{TS}Walked 9000 items in 0.1 seconds (1.0 items/sec)\n" + walk_errs
           + f"{TS}Walking destination path\n{TS}Walked 0 items in 0.001 seconds (0.0 items/sec)\n{TS}Completed sync\n")
    _, s, _ = _run_overflow("dsync", out, argv=("--dryrun", "/c/m/a", "/c/m/b"))
    full = _full("dsync", out, 0, dryrun=True)
    assert s["files"] == s["source_items"] == 9000 == full["source_items"]
    assert s["tool_errors"] == full["tool_errors"] == 800 and s["output_truncated"] is True


def test_overflow_whitelisted_error_line_is_not_counted_twice():
    fails = "".join(_copy_fail(i) for i in range(400))
    out = (_WALKS + fails + f"{TS}Removed 3 items in 0.100 seconds (30.0 items/sec)\n"
           + f"{TS}[0] [mfu_flist_remove.c:9] ERROR: Remove failed for 2 of 5 items\n" + _FINAL)
    _, s, _ = _run_overflow("dsync", out, rc=1)
    full = _full("dsync", out, 1)
    assert s["tool_errors"] == full["tool_errors"] == 401 and s["remove_failed"] == 2


def test_overflow2_ring_drops_lines_and_only_robust_values_remain():
    progress_lines = "".join(f"{TS}Copied {i}.000 MiB (1%) in 1.000 secs (1.000 MiB/s) 9 secs left ...\n"
                             for i in range(600))
    fails = "".join(_copy_fail(i, "28 No space left on device") for i in range(20))
    out = _WALKS + fails + progress_lines + _FINAL
    feeds = _small_feeds()
    cap = _compat(out, rc=1, feeds=feeds)
    assert cap.overflow["keep_gap"] > 0
    assert cap.stdout.endswith(_FINAL)                            # 끝 요약은 끝 링에 늘 남는다
    _, s, _ = _run_overflow("dsync", out, rc=1)
    full = _full("dsync", out, 1)
    assert (s["files"], s["bytes"]) == (None, None)
    assert set(s) == {"returncode", "files", "bytes", "detail_version", "tool", "dryrun", "copy_failed",
                      "create_failed", "tool_errors", "failure_samples", "output_truncated", "reason"}
    assert s["copy_failed"] == full["copy_failed"] == 20 and s["reason"] == "destination_full"


def test_overflow_nsync_keeps_core_counts_only():
    lines = "".join(f"{TS}  failed copy `/d/f{i}`: x (errno 13)\n" for i in range(800))
    out = (lines + f"{TS}[0] [nsync.c:1] ERROR: Failed items: 800 (copy 800); listed below: 800\n"
           + f"{TS}Planned actions: copy=900 mkdir=1 remove=0 skipped-dst-only=0\n"
           + f"{TS}Completed actions: copy=100 mkdir=1 remove=0 copied-bytes=4096\n")
    _, s, _ = _run_overflow("nsync", out, rc=1)
    assert (s["files"], s["bytes"]) == (101, 4096)
    assert s["output_truncated"] is True and "failure_samples" not in s and "failures" not in s
    assert "reason" not in s and "stop_cause" not in s


def test_overflow_one_huge_line_without_newline_stays_bounded():
    feed = stream.stdout_feed(head_max=4096, keep_half=1024, line_max=65536)
    tracemalloc.start()
    try:
        feed.push(ERR + "Failed to copy `")
        block = "y" * 65536
        for _ in range(160):                                      # 10 MiB, 개행 없음
            feed.push(block)
        feed.close()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024
    assert feed.truncated and feed.counters == {"tool_errors": 1, "copy_failed": 1, "create_failed": 0}
    assert feed.text() == "" and feed.keep_gap == 0 and feed.errno_lines == []


_ALPHABET = ["a", "b", " ", "`", "'", "\n", "\n", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029",
             "가", "\U0001F600", ERR, ERR + "Failed to copy `", ERR + "Create `", ERR + "File `", TS,
             TS + "Walking source path", "(errno=28 x)"]


@pytest.mark.parametrize("seed", range(40))
def test_overflow_counters_match_multiline_findall_on_random_output(seed):
    rnd = random.Random(seed)
    text = "".join(rnd.choice(_ALPHABET) for _ in range(rnd.randint(50, 3000)))
    feeds_a = (stream.stdout_feed(head_max=rnd.choice((16, 64, 512)), keep_half=128, line_max=rnd.choice((8, 64, 4096))),
               stream.stderr_feed(head_max=64))
    feeds_b = (stream.stdout_feed(head_max=feeds_a[0]._head_max, keep_half=128, line_max=feeds_a[0]._line_max),
               stream.stderr_feed(head_max=64))
    a = _compat(text, feeds=feeds_a)
    b = _streaming(text, feeds=feeds_b, chunk=rnd.randint(1, 50))
    assert a == b
    if a.overflow is None:
        assert a.stdout == text
        return
    head = a.overflow["head_text"]
    assert text.startswith(head) and (head == "" or head.endswith("\n"))
    exact = {k: n + a.overflow["counters"][k] for k, n in parsers.count_lines(head).items()}
    if feeds_a[0]._line_max >= max(len(x) for x in text.split("\n")):
        assert exact == parsers.count_lines(text)
    # 파서 입력은 원문의 부분 수열(머리 + 고른 줄)이다.
    rest = text[len(head):].split("\n")
    kept = a.stdout[len(head):].split("\n")[:-1]
    it = iter(rest)
    assert all(any(k == r for r in it) for k in kept)


# ---- R8 로그 상한·메모리 -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,limit", [("stdout.log", (65536, 16384)), ("stderr.log", (8192, 2048))])
def test_log_file_keeps_head_and_tail_with_an_exact_gap_marker(tmp_path, name, limit):
    head_max, tail_max = limit
    lines = [f"{i:06d} " + ("가" if i % 7 == 0 else "a") * (i % 90) + "\n" for i in range(4000)]
    full = "".join(lines).encode()
    sink = _open(tmp_path / "art", limits={name: limit})
    sink.begin_logs()
    rnd = random.Random(5)
    at = 0
    text = "".join(lines)
    while at < len(text):
        n = rnd.randint(1, 3000)
        sink.append(name, text[at:at + n])
        at += n
    sink.close(timeout=5)
    data = (tmp_path / "art" / name).read_bytes()
    gap = data.index(b"DMS_JR_LOG_GAP reason=size")
    head, rest = data[:gap], data[gap:]
    if not full.startswith(head):
        # 머리가 줄 한가운데서 끝났으면(쓴 조각의 경계) 표식 앞에 줄바꿈 하나를 넣어 표식을 제 줄에 둔다.
        assert head.endswith(b"\n") and full.startswith(head[:-1])
        head = head[:-1]
    marker, tail = rest.split(b"\n", 1)[0], rest.split(b"\n", 1)[1]
    assert len(head) <= head_max and len(tail) <= tail_max
    assert len(data) <= head_max + tail_max + len(marker) + 2
    assert full.startswith(head) and full.endswith(tail)
    assert tail.startswith(b"0") and (full[len(full) - len(tail) - 1:len(full) - len(tail)] == b"\n")   # 줄 경계
    omitted = full[len(head):len(full) - len(tail)]
    fields = dict(kv.split(b"=") for kv in marker.split(b" ")[2:])
    assert int(fields[b"omitted_bytes"]) == len(omitted) and int(fields[b"omitted_lines"]) == omitted.count(b"\n")
    assert (int(fields[b"head"]), int(fields[b"tail"])) == limit
    data.decode("utf-8")                                          # strict UTF-8 로 읽힌다


def test_log_under_the_cap_has_no_marker():
    sink = stream.LogSink(100, 50)
    assert sink.write(b"a" * 60 + b"\n") == b"a" * 60 + b"\n"
    assert sink.write(b"b" * 30 + b"\n") == b"b" * 30 + b"\n"
    assert sink.write(b"c" * 20 + b"\n") == b""                    # 머리는 줄 경계에서 멈춘다
    assert sink.finish() == b"c" * 20 + b"\n"                      # 빠진 것 없음 -- 표식 없이 이어 붙인다


def test_log_head_cut_without_a_newline_respects_utf8_boundaries():
    sink = stream.LogSink(10, 4)
    out = sink.write("가나다라마".encode())                        # 15 바이트, 개행 없음
    assert out == "가나다".encode()
    out.decode("utf-8")
    tail = sink.finish()
    assert tail.startswith(b"\nDMS_JR_LOG_GAP reason=size")      # 머리가 줄 한가운데서 끝났다 -- 표식은 제 줄에


def test_memory_stays_bounded_on_a_large_mixed_flood():
    # 축소 상한(파서 머리 1 MiB·keep 256 KiB·로그 머리 1 MiB·끝 256 KiB)에서 50 MiB 혼합 출력(ASCII 오류 줄·한글·비 BMP 이름).
    caps = {"head": 1 << 20, "keep": 256 << 10, "log_head": 1 << 20, "log_tail": 256 << 10}
    out, log = stream.stdout_feed(head_max=caps["head"], keep_half=caps["keep"]), stream.LogSink(caps["log_head"],
                                                                                              caps["log_tail"])
    names = ("ascii", "한글이름", "\U0001F600비BMP")
    line_tpl = ERR + "Failed to copy `/s/{n}{i}' to `/d/{n}{i}' (errno=28 No space left on device) " + "p" * 900 + "\n"
    tracemalloc.start()
    try:
        produced = 0
        i = 0
        while produced < 50 * (1 << 20):
            block = "".join(line_tpl.format(n=names[(i + k) % 3], i=i + k) for k in range(60))
            i += 60
            produced += len(block)
            out.push(block)
            log.write(block.encode())
        out.close()
        log.finish()
        out.text()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert out.truncated and out.counters["copy_failed"] > 40000
    assert peak <= 4 * sum(caps.values()) + 2 * (1 << 20)


# ---- R9 쓰기 백로그·진행 우선 ---------------------------------------------------------------------------------------------------

def test_stalled_writer_never_blocks_the_reader_and_counts_what_it_dropped(tmp_path):
    release = threading.Event()

    def stuck_write(fd, data):
        release.wait()
        return os.write(fd, data)

    w = _open(tmp_path / "art", queue_max=64 * 1024, flush_bytes=1024, write=stuck_write)
    w.begin_logs()
    blocks = [f"block {i:05d} ".ljust(4095, "z") + "\n" for i in range(200)]
    t0 = time.monotonic()
    for b in blocks[:150]:
        w.append("stdout.log", b)
    assert time.monotonic() - t0 < 1.0                           # 리더는 막히지 않는다
    t0 = time.monotonic()
    w.close(timeout=0.3)
    assert time.monotonic() - t0 < 2.0                            # close 상한
    release.set()
    w._thread.join(5)
    data = (tmp_path / "art" / "stdout.log").read_text()
    kept = [b for b in blocks[:150] if b in data]
    dropped = [b for b in blocks[:150] if b not in data]
    assert dropped and kept
    marker = [ln for ln in data.split("\n") if ln.startswith("DMS_JR_LOG_GAP reason=backlog")]
    assert marker == [f"DMS_JR_LOG_GAP reason=backlog omitted_lines={len(dropped)} "
                      f"omitted_chars={sum(len(b) for b in dropped)}"]


def test_progress_slot_is_written_before_a_long_log_queue(tmp_path):
    order = []

    def slow_write(fd, data):
        time.sleep(0.02)
        order.append(bytes(data[:1]))
        return os.write(fd, data)

    w = _open(tmp_path / "art", flush_bytes=1, budget_seconds=0.1, write=slow_write)
    w.begin_logs()
    for i in range(60):
        w.append("stdout.log", f"L{i}\n")
    time.sleep(0.05)
    w.set_progress(b'{"v":1}')
    w.close(timeout=10)
    assert b"{" in order and order.index(b"{") < len(order) - 1
    assert order.index(b"{") < order.count(b"L")                  # 로그 큐가 다 비기 전에 진행이 쓰였다
    assert json.loads((tmp_path / "art" / "progress.json").read_bytes()) == {"v": 1}


def test_writer_os_errors_turn_off_only_that_output(tmp_path, capsys):
    def enospc(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    sink = _open(tmp_path / "art", write=enospc)
    tracker = progress.Tracker("dsync", clock=lambda: 0.0)
    cap = _streaming(DSYNC_OK, sink=sink, tracker=tracker)
    assert cap.returncode == 0 and _build_summary("dsync", cap.stdout, 0, "/x")["files"] == 2
    assert capsys.readouterr() == ("", "")


# ---- R10 리더 join 상한·종료 -----------------------------------------------------------------------------------------------------

_HOLDER = textwrap.dedent("""
    import subprocess, sys, time
    sys.path.insert(0, sys.argv[1])
    from dms_job_runner import stream
    # mpirun 역할 자식: 파이프를 물려받은 손자를 남기고 rc 3 으로 곧바로 끝난다.
    child = "import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)']); sys.exit(3)"
    cap = stream.run_streaming([sys.executable, "-c", child], sink=stream.NullSink(), tracker=None, join_seconds=1.0)
    sys.stdout.write(f"rc={cap.returncode} left={stream.threads_left()}\\n")
    stream.exit_process(cap.returncode)
""")


def test_reader_join_is_capped_and_the_process_exits_with_the_tool_rc(tmp_path):
    t0 = time.monotonic()
    proc = subprocess.Popen(_py(_HOLDER, SRC), stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=6)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    assert proc.returncode == 3 and time.monotonic() - t0 < 6
    assert out.decode().strip() == "rc=3 left=True"
    assert b"Fatal Python error" not in err and b"could not acquire lock" not in err


def test_exit_process_uses_sys_exit_when_no_thread_is_left(monkeypatch):
    monkeypatch.setattr(stream, "_THREADS", [])
    with pytest.raises(SystemExit) as e:
        stream.exit_process(7)
    assert e.value.code == 7


# ---- R11 7b·9 회귀 -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(30))
def test_stripped_line_generator_matches_the_old_split(seed):
    rnd = random.Random(seed)
    alphabet = ["a", " ", "\t", "\n", "\r", "\x0b", "\u2028", "DMS_EXEC_REASON=x"]
    a = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 40)))
    b = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 40)))
    assert list(runner._stripped_lines(a, b)) == [ln.strip() for ln in f"{a}\n{b}".split("\n")]


def test_failure_tail_uses_the_echo_ring_in_overflow_mode(capsys):
    fails = "".join(_copy_fail(i) for i in range(3000))
    out = _WALKS + fails + _FINAL
    rc, s, _ = _run_overflow("dsync", out, rc=1)
    after = _after_mpirun(capsys.readouterr().out)
    assert rc == 1 and after[0] == "DMS_EXEC_REASON=sync_items_failed"
    assert after[1].startswith("DMS_JR_TOOL_OUTPUT (stdout.log 마지막 ")
    assert after[-1] == "  | " + f"{TS}Completed sync"            # 마지막 줄까지 연속한 꼬리
    assert any("/s/f2999'" in line for line in after)


# ---- R12 주입 경계 ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("tool,out", [("dsync", DSYNC_OK),
                                      ("nsync", f"{TS}Role assignment mode=map src_ranks=1 dst_ranks=1\n"
                                                f"{TS}Planned actions: copy=1 skipped-dst-only=0\n"
                                                f"{TS}Completed actions: copy=1 copied-bytes=3\n"
                                                f"{TS}Execution completed successfully\n")])
def test_compat_path_touches_no_filesystem_besides_write_text(monkeypatch, tool, out):
    def forbidden(*a, **k):
        raise AssertionError(f"파일시스템 직접 접근: {a!r}")
    monkeypatch.setattr(os, "makedirs", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    rec = _Writes()
    rc = run_job(_env(tool, "/art"), run=_fake_run(out), write_text=rec.write_text, read_text=lambda p: "",
                 sleep=lambda s: None, wait_hostfile=lambda role=None: (["w"], "/h"))
    monkeypatch.undo()
    assert rc == 0 and not os.path.exists("/art")
    snap = json.loads(rec.writes["/art/progress.json"])
    assert snap["ended"] is True and snap["tool"] == tool and snap["phase"] == "done"
    assert rec.writes["/art/stdout.log"] == out
    order = [p.rsplit("/", 1)[1] for p in rec.order if p.startswith("/art/") and p.rsplit("/", 1)[1] in (
        "stdout.log", "stderr.log", "progress.json", "summary.json")]
    assert order == ["stdout.log", "stderr.log", "progress.json", "summary.json"]


def test_scan_and_rm_jobs_write_no_progress_file():
    for tool, argv in (("drm", ("/t",)), ("dscan", ("--directory", "/t", "--output", "$DMS_SCAN_REPORT"))):
        rec = _Writes()
        run_job(_env(tool, "/art", argv), run=_fake_run("ok\n"), write_text=rec.write_text, read_text=lambda p: "",
                sleep=lambda s: None, wait_hostfile=lambda role=None: (["w"], "/h"))
        assert "/art/progress.json" not in rec.writes and rec.writes["/art/stdout.log"] == "ok\n"


def test_open_sink_factory_failure_falls_back_to_a_null_sink():
    rec = _Writes()

    def broken(path):
        raise RuntimeError("no threads")
    rc = run_job(_env("dsync", "/art"), run=_fake_run(DSYNC_OK), write_text=rec.write_text, read_text=lambda p: "",
                 sleep=lambda s: None, wait_hostfile=lambda role=None: (["w"], "/h"), open_sink=broken)
    assert rc == 0 and set(p.rsplit("/", 1)[1] for p in rec.writes if p.startswith("/art/")) == {
        "mpi-hostfile", "rank.sh", "summary.json"}


def test_run_job_with_the_real_streaming_runner_end_to_end(tmp_path, capsys):
    art = tmp_path / "art"
    fake = _PipePopen(DSYNC_OK.encode(), b"", 0, chunk=13)
    rec = _Writes()
    rc = run_job(_env("dsync", art), run=_fake_run("unused"), write_text=rec.write_text, read_text=lambda p: "",
                 sleep=lambda s: None, wait_hostfile=lambda role=None: (["w"], "/h"),
                 run_tool=functools.partial(stream.run_streaming, popen=fake), open_sink=stream.ArtifactWriter.open)
    assert rc == 0
    assert "mpirun" in " ".join(fake.calls[0][0])
    assert (art / "stdout.log").read_text() == DSYNC_OK and (art / "stderr.log").read_text() == ""
    snap = json.loads((art / "progress.json").read_bytes())
    assert (snap["phase"], snap["ended"], snap["walk"]) == ("done", True, {"src_items": 2, "dst_items": 0})
    assert json.loads(rec.writes[f"{art}/summary.json"])["files"] == 2
    assert _after_mpirun(capsys.readouterr().out) == []


# ---- R14 fail-soft 주입 ------------------------------------------------------------------------------------------------------

_BIG_CHILD = ("import sys\n"
              "sys.stdout.write('[2026-11-26T08:29:09] Walking source path\\n')\n"
              "line = '[2026-11-26T08:29:09] Walked 7 items in 0.1 secs (1.0 items/sec) ...' + ' ' * 0 + '\\n'\n"
              "pad = 'p' * 1000 + '\\n'\n"
              "for i in range(10000):\n"
              "    sys.stdout.write(pad)\n"
              "sys.stdout.write('[2026-11-26T08:29:09] Copying data.\\n')\n"
              "sys.exit(4)\n")


def test_tracker_exception_stops_only_progress(tmp_path, monkeypatch):
    calls = {"n": 0}
    real = progress.Tracker.observe

    def flaky(self, line, now=None):
        calls["n"] += 1
        if calls["n"] > 3:
            raise RuntimeError("tracker bug")
        return real(self, line, now)
    monkeypatch.setattr(progress.Tracker, "observe", flaky)
    sink = _open(tmp_path / "art")
    tracker = progress.Tracker("dsync", clock=time.monotonic)
    cap = stream.run_streaming(_py(_BIG_CHILD), sink=sink, tracker=tracker)
    assert cap.returncode == 4 and len(cap.stdout) > 10_000_000 and cap.stdout.endswith("Copying data.\n")
    snap = json.loads((tmp_path / "art" / "progress.json").read_bytes())
    assert snap["phase"] == "start" and snap["ended"] is False     # 마지막 스냅숏에서 멈췄다(끝 스냅숏 없음)


def test_parser_feed_exception_folds_the_summary_to_core_keys(capsys):
    class Boom(stream.LineFeed):
        def push(self, text):
            raise MemoryError("simulated")
    feeds = (Boom(head_max=1 << 20), stream.stderr_feed())
    rec = _Writes()
    rc = run_job(_env("dsync", "/art"), run=_fake_run(DSYNC_OK), write_text=rec.write_text, read_text=lambda p: "",
                 sleep=lambda s: None, wait_hostfile=lambda role=None: (["w"], "/h"),
                 run_tool=stream.compat(_fake_run(DSYNC_OK), feeds=feeds))
    assert rc == 0
    assert json.loads(rec.writes["/art/summary.json"]) == {"returncode": 0, "files": None, "bytes": None}
    assert rec.writes["/art/stdout.log"] == DSYNC_OK                 # 로그·진행은 계속 쓴다
    assert _after_mpirun(capsys.readouterr().out) == []


def test_stderr_feed_exception_leaves_aborted_unknown():
    class Boom(stream.LineFeed):
        def push(self, text):
            raise RuntimeError("x")
    feeds = (stream.stdout_feed(), Boom(head_max=1 << 20))
    cap = _compat(DSYNC_OK, "MPI_ABORT was invoked on rank 0\n", 1, feeds=feeds)
    s = _build_summary("dsync", cap.stdout, 1, "/x", stderr=cap.stderr, overflow=cap.overflow)
    assert "aborted" not in s and s["files"] == 2


def test_stderr_overflow_still_sees_an_abort_past_the_head():
    err = "".join(f"noise {i}\n" for i in range(1000)) + "MPI_ABORT was invoked on rank 0\n"
    feeds = (stream.stdout_feed(), stream.stderr_feed(head_max=512))
    cap = _compat(DSYNC_OK, err, 1, feeds=feeds)
    assert cap.overflow == {"truncated": False, "keep_gap": 0, "parse_lost": False, "head_text": None,
                            "counters": {}, "errno_lines": [], "aborted_seen": True}
    s = _build_summary("dsync", cap.stdout, 1, "/x", stderr=cap.stderr, overflow=cap.overflow)
    assert s["aborted"] is True and s["reason"] == "tool_aborted" and s["files"] is None
    assert "output_truncated" not in s


# ---- R15 조기 종료 ----------------------------------------------------------------------------------------------------------

def test_early_exit_with_the_real_sink_leaves_only_the_workers_snapshot(tmp_path, capsys):
    art = tmp_path / "art"
    sinks = []

    def open_sink(path):
        sinks.append(stream.ArtifactWriter.open(path))
        return sinks[-1]

    def never_ready(cmd):
        if cmd[0] == "getent":
            class R:
                returncode, stdout, stderr = 2, "", ""
            return R()
        return _fake_run("")(cmd)

    def write_text(path, content, *, append=False):
        if path.startswith(str(tmp_path)):                        # /etc/passwd 물질화 등 시험 밖 경로는 쓰지 않는다
            runner._write_text(path, content, append=append)

    try:
        rc = run_job({**_env("dsync", art), "DMS_JR_WORKER_READY_TIMEOUT_SECONDS": "2"}, run=never_ready,
                     write_text=write_text, read_text=lambda p: "", sleep=lambda s: None,
                     wait_hostfile=lambda role=None: (["w"], "/h"), open_sink=open_sink)
    finally:
        for sink in sinks:
            sink.close(timeout=5)                                 # run_job 은 조기 종료에서 닫지 않는다(시험 정리)
    assert rc == 1
    assert not (art / "stdout.log").exists()                      # 로그 파일은 mpirun 시작 때만 만든다
    assert "DMS_JR_WORKER_UNREACHABLE" in (art / "stderr.log").read_text()
    snap = json.loads((art / "progress.json").read_bytes())
    assert (snap["phase"], snap["ended"], snap["lines"]) == ("workers", False, 0)
    assert json.loads((art / "summary.json").read_text()) == {"returncode": None, "files": None, "bytes": None}


def test_early_exit_compat_writes_no_stdout_log():
    rec = _Writes()

    def never_ready(cmd):
        class R:
            returncode, stdout, stderr = (2, "", "") if cmd[0] == "getent" else (0, "", "")
        return R()
    rc = run_job({**_env("dsync", "/art"), "DMS_JR_WORKER_READY_TIMEOUT_SECONDS": "2"}, run=never_ready,
                 write_text=rec.write_text, read_text=lambda p: "", sleep=lambda s: None,
                 wait_hostfile=lambda role=None: (["w"], "/h"))
    assert rc == 1 and "/art/stdout.log" not in rec.writes and "/art/progress.json" not in rec.writes
