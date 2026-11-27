"""sync 진행률 Tracker(dms_job_runner.progress -- 2026-11-26 설계 §3) 시험: 정규식 표(P1), 실제 로그 재생(P2), 크기·원문 없음(P3),
스로틀·심장박동(R2), 계약 상수·phase 목록(R13), 계약 예 스냅숏 재현.

실제 로그는 tests/fixtures/progress/ 에 있다 -- 2026-11-26 dmsk8s 진행률 실측(저장소 밖 실증 산출물)의 발췌 그대로:
  dsync-execution-stdout.log  DMS dsync 실행 stdout.log(1 rank, 100,105 항목 1.381 GiB, 22분 52초, 189줄)
  dsync-preview-stdout.log    같은 잡의 미리보기 stdout.log
  m1-nsync-batch1M.log        수동 nsync --batch-files 1000000(배치 1개) -- 줄 앞에 수신 시각(KST ms) + O/E 접두
  m2-nsync-batch20k.log       수동 nsync --batch-files 20000(배치 6개)
  m4-nsync-dryrun-batch20k.log 수동 nsync --dryrun --batch-files 20000
  m5-dsync-batch250.log       수동 dsync --batch-files 250(배치 모드 -- 단계 되풀이·OVERALL 배치마다)
계약(frontend/src/lib/progressContract.json)의 예 스냅숏은 같은 로그를 설계 §3.2 전이 규칙으로 흘려 만들었다 -- 아래
test_tracker_reproduces_every_contract_example 이 이 구현으로 같은 값이 나오는지 본다(초 값까지)."""
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

from dms_job_runner import progress
from dms_job_runner.progress import Tracker

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures" / "progress"
CONTRACT = json.loads((ROOT / "frontend" / "src" / "lib" / "progressContract.json").read_text(encoding="utf-8"))
SNAP = CONTRACT["snapshot"]
FIELDS = SNAP["fields"]
EXAMPLES = CONTRACT["examples"]["snapshots"]


def _hms(text):
    h, m, rest = text.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def _dms_lines(name):
    """DMS stdout.log -- (줄 머리 시각 초, 줄)."""
    return [(_hms(line[12:20]), line) for line in (FIX / name).read_text(encoding="utf-8").splitlines()]


def _manual_lines(name):
    """수동 로그 -- 수신 시각(KST, ms) 접두를 떼고 UTC 초로(접두 없는 줄은 버린다)."""
    out = []
    for raw in (FIX / name).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(\d\d:\d\d:\d\d\.\d{3}) [OE] ", raw)
        if m:
            out.append((_hms(m.group(1)) - 9 * 3600, raw[m.end():]))
    return out


def _run(tool, lines, *, t0, start, dryrun=False, quiet=False, upto=None):
    t = Tracker(tool, dryrun=dryrun, quiet=quiet, clock=lambda: t0)
    t.set_phase("start", start)
    for i, (ts, line) in enumerate(lines, 1):
        t.feed(line + "\n", ts)
        if upto is not None and i == upto:
            break
    return t


def _line_no(lines, needle):
    return next(i for i, (_, line) in enumerate(lines, 1) if needle in line)


# ---- 계약 모양 검사(tests/test_job_progress_contract.py 와 같은 규칙 -- 시험 파일끼리 import 하지 않는다) -----------------

def _value_problems(desc, value, where, snap):
    t = desc["type"]
    if t == "int" and type(value) is not int:
        return [f"{where}: int 아님 {value!r}"]
    if t == "float" and (type(value) not in (int, float) or not math.isfinite(value)):
        return [f"{where}: float 아님 {value!r}"]
    if t == "bool" and type(value) is not bool:
        return [f"{where}: bool 아님 {value!r}"]
    if t == "str" and not (isinstance(value, str) and re.fullmatch(desc["pattern"], value)):
        return [f"{where}: pattern 밖 {value!r}"]
    if t == "enum" and value not in desc["values"]:
        return [f"{where}: 열거 밖 {value!r}"]
    out = []
    if t in ("int", "float"):
        if "const" in desc:
            if value != desc["const"]:
                out.append(f"{where}: const 아님")
        elif not desc["min"] <= value <= desc["max"]:
            out.append(f"{where}: 범위 밖 {value!r}")
    if t == "obj":
        if not isinstance(value, dict) or not value:
            return [f"{where}: 빈 객체"]
        for k, v in value.items():
            sub = desc["fields"].get(k)
            out += [f"{where}.{k}: 계약 밖 키"] if sub is None else _value_problems(sub, v, f"{where}.{k}", snap)
    if "tools" in desc and snap.get("tool") not in desc["tools"]:
        out.append(f"{where}: {desc['tools']} 전용")
    if "mode" in desc and snap.get("dryrun") is not (desc["mode"] == "preview"):
        out.append(f"{where}: {desc['mode']} 전용")
    return out


def _problems(snap):
    out = []
    for k, v in snap.items():
        out += [f"{k}: 계약 밖 키"] if k not in FIELDS else _value_problems(FIELDS[k], v, k, snap)
    out += [f"{k}: required 없음" for k, d in FIELDS.items() if d.get("required") and k not in snap]
    out += [f"runner_always {k} 없음" for k in SNAP["runner_always"] if k not in snap]
    by_tool = SNAP["phases_by_tool"]
    if snap.get("phase") not in by_tool["common"] + by_tool.get(snap.get("tool"), []):
        out.append(f"phase {snap.get('phase')!r}")
    present = [snap[k] for k in SNAP["invariants"]["seconds_order"] if k in snap]
    if present != sorted(present):
        out.append("seconds_order")
    if ("since_line_s" in snap) != (snap.get("lines", 0) > 0):
        out.append("since_line_iff_lines")
    compact = json.dumps(snap, separators=(",", ":"))
    if len(compact.encode()) > SNAP["max_bytes"]:
        out.append("max_bytes")
    out += [f"금지 키 {k}" for k in SNAP["forbidden_keys"] if f'"{k}":' in compact]
    out += [f"금지 글자 {s}" for s in SNAP["forbidden_substrings"] if s in compact.lower()]
    return out


# ---- R13 계약 상수 ---------------------------------------------------------------------------------------------------------

def test_constants_match_the_contract_and_the_api_module():
    from dms import job_progress
    assert progress.SCHEMA_VERSION == job_progress.SCHEMA_VERSION == CONTRACT["schema_version"]
    assert progress.FILE_NAME == job_progress.FILE_NAME == CONTRACT["file_name"]
    assert progress.MAX_BYTES == SNAP["max_bytes"]
    assert list(progress.PHASES) == SNAP["phases"]
    assert list(progress.TOOLS) == FIELDS["tool"]["values"]


@pytest.mark.parametrize("tool", ["dsync", "nsync"])
def test_tracker_phases_stay_inside_the_contract_list_for_that_tool(tool):
    by_tool = SNAP["phases_by_tool"]
    assert set(progress.phases_for(tool)) == set(by_tool["common"] + by_tool[tool])


def test_runner_package_does_not_import_dms():
    # 잡 이미지에 단독 설치되는 독립 패키지(ALLOWED_TOOLS 와 같은 원칙) -- 상수는 중복 정의, 이 시험층이 잇는다.
    code = ("import sys; import dms_job_runner.runner, dms_job_runner.stream, dms_job_runner.progress; "
            "print(sorted(m for m in sys.modules if m == 'dms' or m.startswith('dms.')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}).stdout
    assert out.strip() == "[]"


# ---- 계약 예 스냅숏 재현 ---------------------------------------------------------------------------------------------------

def _contract_snapshots():
    exe = _dms_lines("dsync-execution-stdout.log")
    t0, start = _hms("08:28:57.7"), _hms("08:28:58.9")
    got = {"workers": Tracker("dsync", clock=lambda: t0).snapshot(t0)}
    n = _line_no(exe, "Created 38308 items")
    got["dsync_create"] = _run("dsync", exe, t0=t0, start=start, upto=n).snapshot(exe[n - 1][0] + 1.5)
    pre = _dms_lines("dsync-preview-stdout.log")
    n = next(i for i, (_, ln) in enumerate(pre, 1) if "secs (" in ln and ln.endswith("..."))
    got["dsync_walk_src"] = _run("dsync", pre, t0=_hms("08:28:37.6"), start=_hms("08:28:38.8"), dryrun=True,
                                 upto=n).snapshot(pre[n - 1][0] + 0.3)
    # §3.1 예: 실행 로그 복사 줄에 배치 모드 배치 경계(합성 -- 계약 sources 와 같은 줄)를 앞에 얹는다.
    copy = _line_no(exe, "Copied 1.003 GiB (73%)")
    mkdir = next(i for i, (_, ln) in enumerate(exe, 1) if ln.endswith("Creating 103 directories"))
    batch = ["08:28:59 Creating 39897 files.", "08:29:09 Copying data.",
             "08:29:20 Setting ownership, permissions, and timestamps.",
             "08:29:24 Updated 39897 items in 4.012 seconds (9944.417 items/sec)",
             "08:29:24 Copied 40000 of 100105 items (39.958%)", "08:29:24 Creating 40000 files.",
             "08:29:25 Copying data."]
    syn = exe[:mkdir] + [(_hms(b[:8]), f"[2026-11-26T{b[:8]}] {b[9:]}") for b in batch] + [exe[copy - 1]]
    got["dsync_copy_overall"] = _run("dsync", syn, t0=t0, start=start).snapshot(exe[copy - 1][0] + 3.2)
    ended = _run("dsync", exe, t0=t0, start=start)
    ended.end()
    got["dsync_ended"] = ended.snapshot(exe[-1][0] + 0.6)
    m2 = _manual_lines("m2-nsync-batch20k.log")
    nt0 = m2[0][0] - 1.1
    n = _line_no(m2, "Role assignment")
    got["nsync_scan"] = _run("nsync", m2, t0=nt0, start=m2[0][0], upto=n).snapshot(m2[n - 1][0] + 0.4)
    n = _line_no(m2, "batch 1/6")
    got["nsync_batches_exec"] = _run("nsync", m2, t0=nt0, start=m2[0][0], upto=n).snapshot(m2[n - 1][0] + 0.4)
    m4 = _manual_lines("m4-nsync-dryrun-batch20k.log")
    n = _line_no(m4, "batch 3/6")
    got["nsync_batches_dry"] = _run("nsync", m4, t0=m4[0][0] - 1.1, start=m4[0][0], dryrun=True,
                                    upto=n).snapshot(m4[n - 1][0] + 0.1)
    quiet = Tracker("dsync", quiet=True, clock=lambda: t0)
    quiet.set_phase("start", start)
    got["quiet_start"] = quiet.snapshot(start + 300.0)
    return got


def test_tracker_reproduces_every_contract_example():
    # 계약 예는 같은 로그·같은 시각에서 설계 §3.2 규칙으로 만든 값이다 -- 구현이 같은 스냅숏을 내야 포탈 시험(msw 가 예를
    # 그대로 돌려준다)이 실제 러너 출력을 대표한다.
    got = _contract_snapshots()
    assert set(got) == set(EXAMPLES)
    for name, want in EXAMPLES.items():
        assert got[name] == want, name
        assert list(got[name]) == list(want), name                  # 키 순서까지(직렬화 모양)


# ---- P1 정규식 표 ----------------------------------------------------------------------------------------------------------

TS = "[2026-11-26T08:29:09] "
# (본문, 도구, 미리보기, 기대 phase, 기대 하위 객체 일부) -- 실제 로그 원문, 로그에 없는 줄은 포크 소스 문구(설계 §3.3 표).
POSITIVE = [
    ("Walking source path", "dsync", False, "walk_src", {}),
    ("Walking destination path", "dsync", False, "walk_dst", {}),
    ("Comparing file sizes and modification times of 2048 items", "dsync", False, "compare", {"step": {"total": 2048}}),
    ("Comparing file contents of 77 items", "dsync", False, "compare_contents", {"step": {"total": 77}}),
    ("Deleting items from destination", "dsync", False, "delete", {}),
    ("Removing 5 items", "dsync", False, "start", {"step": {"total": 5}}),
    ("Copying items to destination", "dsync", False, "copy_prep", {}),
    ("Creating 103 directories", "dsync", False, "mkdir", {"step": {"total": 103}}),
    ("Creating 100002 files.", "dsync", False, "create", {"step": {"total": 100002}}),
    ("Copying data.", "dsync", False, "copy", {}),
    ("Setting ownership, permissions, and timestamps.", "dsync", False, "metadata", {}),
    ("Fixing permissions.", "dsync", False, "metadata", {}),
    ("Setting ownership, permissions, and timestamps on directories.", "dsync", False, "metadata_dirs", {}),
    ("Fixing permissions on directories.", "dsync", False, "metadata_dirs", {}),
    ("Updating timestamps on newly copied files", "dsync", False, "timestamps", {}),
    ("Completed sync", "dsync", False, "done", {}),
    ("Walked 100105 items in 0.253 secs (395973.064 items/sec) ...", "dsync", False, "start",
     {"step": {"items": 100105}}),
    # 완료 줄은 walk 머리 줄 뒤에만 쪽을 안다(test_walk_done_belongs_to_the_preceding_head) -- start 에서는 담지 않는다.
    ("Walked 100105 items in 0.254 seconds (393699.242 items/sec)", "dsync", False, "start", {}),
    ("Created 50 directories (48%) in 1.500 secs (33.333 dirs/sec) 2 secs left ...", "dsync", False, "start",
     {"step": {"items": 50, "percent": 48, "rate_items": 33.333, "done": False}}),
    ("Created 38308 items (38%) in 10.007 secs (3828.142 items/sec) 16 secs left ...", "dsync", False, "start",
     {"step": {"items": 38308, "percent": 38, "rate_items": 3828.142, "done": False}}),
    ("Created 100002 items (100%) in 25.603 secs (3905.835 items/sec) done", "dsync", False, "start",
     {"step": {"items": 100002, "percent": 100, "rate_items": 3905.835, "done": True}}),
    ("Copied 608.000 MiB (43%) in 10.006 secs (60.767 MiB/s) 13 secs left ...", "dsync", False, "start",
     {"step": {"percent": 43, "bytes": 637534208, "rate_bps": 63718818, "done": False}}),
    ("Copied 1.381 GiB (100%) in 1306.687 secs (1.083 MiB/s) done", "dsync", False, "start",
     {"step": {"percent": 100, "bytes": 1482837459, "rate_bps": 1135608, "done": True}}),
    ("Copied 0.000 B (0%) in 3.000 secs (0.000 B/s) -1 secs left ...", "dsync", False, "start",
     {"step": {"percent": 0, "bytes": 0, "rate_bps": 0, "done": False}}),
    ("Compared 1.000 GiB (50%) in 4.000 secs (256.000 MiB/s) 4 secs left ...", "dsync", False, "start",
     {"step": {"percent": 50, "bytes": 1073741824, "rate_bps": 268435456, "done": False}}),
    ("Removed 120 items (12.000%) in 1.000 secs (120.000 items/sec) 7 secs remaining ...", "dsync", False, "start",
     {"step": {"items": 120, "percent": 12.0, "rate_items": 120.0, "done": False}}),
    ("Removed 1000 items in 8.000 seconds (125.000 items/sec)", "dsync", False, "start",
     {"step": {"items": 1000, "rate_items": 125.0, "done": True}}),
    ("Updated 25471 items in 10.000 secs (2546.976 items/sec) ...", "dsync", False, "start",
     {"step": {"items": 25471, "rate_items": 2546.976, "done": False}}),
    ("Updated 100002 items in 37.555 seconds (2662.823 items/sec)", "dsync", False, "start",
     {"step": {"items": 100002, "rate_items": 2662.823, "done": True}}),
    ("Copied 100105 of 100105 items (100.000%)", "dsync", False, "start",
     {"overall": {"items": 100105, "total": 100105, "percent": 100.0}}),
    ("Role assignment mode=map src_ranks=1 dst_ranks=1", "nsync", False, "scan", {}),
    ("Batch mode enabled: batch-files=20000 batch-count=6 src-items=100105 dst-items=0", "nsync", False, "start",
     {"scan": {"batch_files": 20000, "batch_count": 6, "src_items": 100105, "dst_items": 0}, "batch": {"count": 6}}),
    ("Progress logging enabled on console log rank 0 (per batch)", "nsync", False, "batches", {"batch": {"done": 0}}),
    ("Progress 16.7% batch 1/6 actions=16560 copied-files=16548 copied-volume=576.637 MiB recent(actions=16560 "
     "files=16548 volume=576.637 MiB, 510.37 files/s, 17.785 MiB/s over 32.423 s) avg(510.37 files/s, 17.785 MiB/s)",
     "nsync", False, "start",
     {"batch": {"done": 1, "count": 6, "percent": 16.7, "actions": 16560, "copied_files": 16548,
                "copied_bytes": 604647719, "avg_files_per_s": 510.37, "avg_bps": 18648924}}),
    ("Progress 16.7% batch 1/6 planned-actions=16561 planned-copy-files=16548 planned-volume=576.637 MiB",
     "nsync", True, "start",
     {"batch": {"done": 1, "count": 6, "percent": 16.7, "planned_actions": 16561, "planned_copy_files": 16548,
                "planned_bytes": 604647719}}),
    ("nsync Phase 5 planner+execute complete", "nsync", False, "finishing", {}),
    ("nsync Phase 5 planner dryrun complete", "nsync", True, "finishing", {}),
    ("Execution completed successfully", "nsync", False, "done", {}),
    ("Dryrun enabled: planner generated actions but no filesystem changes were made.", "nsync", True, "done", {}),
]


@pytest.mark.parametrize("body,tool,dryrun,phase,expect", POSITIVE, ids=[p[0][:40] for p in POSITIVE])
def test_progress_line_table(body, tool, dryrun, phase, expect):
    t = Tracker(tool, dryrun=dryrun, clock=lambda: 0.0)
    t.set_phase("start", 0.0)
    assert t.observe(TS + body, 1.0) is True
    snap = t.snapshot(2.0)
    assert snap["phase"] == phase and snap["lines"] == 1 and snap["since_line_s"] == 1.0
    for name, sub in expect.items():
        assert {k: snap[name].get(k) for k in sub} == sub, name
    assert _problems(snap) == []


def test_walk_done_belongs_to_the_preceding_head():
    t = Tracker("dsync", clock=lambda: 0.0)
    for body in ("Walked 9 items in 0.1 seconds (90.0 items/sec)", "Walking source path",
                 "Walked 7 items in 0.1 seconds (70.0 items/sec)", "Walking destination path",
                 "Walked 0 items in 0.000 seconds (0.000 items/sec)"):
        assert t.observe(TS + body, 1.0)
    assert t.snapshot(1.0)["walk"] == {"src_items": 7, "dst_items": 0}


def test_every_table_pattern_has_a_positive_example():
    names = {name for table in progress._TABLES.values() for cands in table.values() for name, _ in cands}
    matched = set()
    for body, tool, dryrun, _, _ in POSITIVE:
        for name, rx in progress._TABLES[(tool, dryrun)].get(body.split(" ", 1)[0], []):
            if rx.fullmatch(TS + body):
                matched.add(name)
    assert names == matched


NEGATIVE = [
    "[2026-11-26T08:28:59] Walking /cephfs/managed/dms-progress-proof-1126/src",       # drm·dsync 경로 줄
    "[2026-11-26T08:28:59] Copying to /cephfs-secondary/dms-progress-proof-1126/dst",
    "[2026-11-26T08:28:59] Items: 100105",                                               # 계획 블록
    "[2026-11-26T08:28:59] Data: 1.381 GiB (14.485 KiB per file)",
    "[2026-11-26T08:28:59] Completed updating timestamps",
    "[2026-11-26T08:29:09] [0] [mfu_flist_copy.c:2256] ERROR: Failed to copy `/s/a' to `/d/a' (errno=28 x)",
    "[2026-11-26T08:29:09] [0] [dsync.c:1] ERROR: Copied 1 of 2 items (50.000%)",        # 오류 줄 머리는 첫 분기에서 버린다
    "[2026-11-26T08:29:09] Original directory exists, skip the creation: `/d/x'",       # 항목 WARN 줄
    "[2026-11-26T08:29:09] Copied 1.000 ZiB (50%) in 1.000 secs (1.000 ZiB/s) done",     # 미지 단위
    "[2026-11-26T08:29:09] Copied 1 of 2 items (50.000%) ",                              # 줄 끝 공백 -- 전체 일치 아님
    "2026-11-26T08:29:09 Copied 1 of 2 items (50.000%)",
    "[2026-11-26T08:29:09]Copied 1 of 2 items (50.000%)",
    "  [2026-11-26T08:29:09] Copied 1 of 2 items (50.000%)",
    "[2026-11-26T08:29:09] Copied 1 of 2 items (50.000%)\x0b",
    "[2026-11-26T08:29:09] Copied " + "9" * 21 + " of 2 items (50.000%)",                # 수 자릿수 상한
    "[2026-11-26T08:29:09] Walking source path" + " " * 5000,                            # 4 KiB 넘는 줄
]


@pytest.mark.parametrize("line", NEGATIVE)
def test_non_progress_lines_are_ignored(line):
    t = Tracker("dsync", clock=lambda: 0.0)
    assert t.observe(line, 1.0) is False
    assert t.snapshot(1.0)["lines"] == 0 and "since_line_s" not in t.snapshot(1.0)


def test_lines_of_the_other_tool_and_mode_are_ignored():
    # 계약 tools·mode -- dsync 스냅숏에 batch 가, 실행 스냅숏에 planned_* 가 섞이지 않는다.
    dry = TS + "Progress 16.7% batch 1/6 planned-actions=1 planned-copy-files=1 planned-volume=1.000 MiB"
    assert Tracker("nsync", dryrun=False, clock=lambda: 0.0).observe(dry, 1.0) is False
    assert Tracker("dsync", clock=lambda: 0.0).observe(TS + "Role assignment mode=map src_ranks=1 dst_ranks=1") is False
    assert Tracker("nsync", clock=lambda: 0.0).observe(TS + "Completed sync") is False
    with pytest.raises(ValueError):
        Tracker("drm", clock=lambda: 0.0)


def test_out_of_range_values_drop_only_that_field():
    t = Tracker("dsync", clock=lambda: 0.0)
    t.observe(TS + "Created 5 items (150%) in 1.000 secs (5.000 items/sec) done", 1.0)    # % 100 초과
    assert t.snapshot(1.0)["step"] == {"items": 5, "rate_items": 5.0, "done": True}
    t.observe(TS + "Copied 99999999999999999999.000 EiB (5%) in 1.000 secs (1.000 MiB/s) done", 2.0)   # 2^63 초과
    assert t.snapshot(2.0)["step"] == {"items": 5, "percent": 5, "rate_items": 5.0, "rate_bps": 1048576, "done": True}
    t.observe(TS + "Created 99999999999999999999 items (1%) in 1.000 secs (5.000 items/sec) done", 3.0)
    assert "items" not in t.snapshot(3.0)["step"]                                          # 낡은 값을 남기지 않는다


def test_feed_splits_only_on_newline_and_handles_chunk_boundaries():
    t = Tracker("dsync", clock=lambda: 0.0)
    line = TS + "Walking source path"
    for ch in line + "\n" + TS + "Walking destination path\x0b\n" + TS + "Completed sync":
        t.feed(ch, 1.0)
    assert t.snapshot(1.0)["lines"] == 1                     # \x0b 가 붙은 줄은 전체 일치가 아니다(splitlines 로 자르지 않는다)
    t.finish(1.0)                                            # 개행 없이 끝난 마지막 줄도 본다
    assert t.snapshot(1.0)["phase"] == "done" and t.snapshot(1.0)["lines"] == 2


def test_overlong_partial_line_is_dropped_without_growing():
    t = Tracker("dsync", clock=lambda: 0.0)
    for _ in range(100):
        t.feed("x" * 1000, 1.0)                              # 개행 없는 100 KB
    assert t._plen == 0 and t._parts == []
    t.feed("\n" + TS + "Copying data.\n", 1.0)
    assert t.snapshot(1.0)["phase"] == "copy" and t.snapshot(1.0)["lines"] == 1


# ---- P2 전체 로그 재생 ------------------------------------------------------------------------------------------------------

def _replay(tool, lines, *, dryrun=False, every=True):
    """줄마다 스냅숏을 모은다(모두 계약을 만족해야 한다)."""
    t0 = lines[0][0] - 2.0
    t = Tracker(tool, dryrun=dryrun, clock=lambda: t0)
    snaps = [t.snapshot(t0)]
    t.set_phase("start", t0 + 1.0)
    snaps.append(t.snapshot(t0 + 1.0))
    for ts, line in lines:
        t.feed(line + "\n", ts)
        if every:
            snaps.append(t.snapshot(ts + 0.5))
    t.end()
    snaps.append(t.snapshot(lines[-1][0] + 1.0))
    return t, snaps


def test_replay_dsync_execution_log():
    lines = _dms_lines("dsync-execution-stdout.log")
    assert len(lines) == 189
    _, snaps = _replay("dsync", lines)
    for s in snaps:
        assert _problems(s) == [], s
    last = snaps[-1]
    assert (last["phase"], last["ended"]) == ("done", True)
    assert last["overall"] == {"items": 100105, "total": 100105, "percent": 100.0}
    assert last["walk"] == {"src_items": 100105, "dst_items": 0}
    assert [s["phase"] for s in snaps if s["phase"] not in ("workers", "start")][0] == "walk_src"
    seen = [s["phase"] for s in snaps]
    assert [p for i, p in enumerate(seen) if i == 0 or seen[i - 1] != p] == [
        "workers", "start", "walk_src", "walk_dst", "copy_prep", "mkdir", "create", "copy", "metadata",
        "metadata_dirs", "timestamps", "done"]


def test_replay_dsync_preview_log():
    _, snaps = _replay("dsync", _dms_lines("dsync-preview-stdout.log"), dryrun=True)
    assert all(_problems(s) == [] for s in snaps)
    assert snaps[-1]["phase"] == "done" and snaps[-1]["walk"] == {"src_items": 100105, "dst_items": 0}


def test_replay_nsync_execution_batches():
    _, snaps = _replay("nsync", _manual_lines("m2-nsync-batch20k.log"))
    assert all(_problems(s) == [] for s in snaps)
    last = snaps[-1]
    assert last["phase"] == "done" and last["batch"]["done"] == 6 and last["batch"]["count"] == 6
    assert last["batch"]["copied_files"] == 100002 and last["batch"]["percent"] == 100.0
    assert "planned_actions" not in last["batch"]
    assert [s["batch"]["done"] for s in snaps if s.get("batch", {}).get("done") is not None].count(0) >= 1


def test_replay_nsync_single_batch_has_no_numbers_until_the_end():
    # 사용자 결정: nsync 는 배치 단위 진행 줄 그대로 -- 배치가 1개면 끝날 때 한 번 보인다.
    lines = _manual_lines("m1-nsync-batch1M.log")
    _, snaps = _replay("nsync", lines)
    before = [s for s in snaps if s["phase"] == "batches" and s["batch"].get("done") == 0]
    assert before and all("copied_files" not in s["batch"] for s in before)
    assert snaps[-1]["batch"]["done"] == 1 and snaps[-1]["batch"]["count"] == 1


def test_replay_nsync_dryrun_plans():
    _, snaps = _replay("nsync", _manual_lines("m4-nsync-dryrun-batch20k.log"), dryrun=True)
    assert all(_problems(s) == [] for s in snaps)
    last = snaps[-1]
    assert last["batch"] == {"done": 6, "count": 6, "percent": 100.0, "planned_actions": 100105,
                             "planned_copy_files": 100002, "planned_bytes": 1482837459}
    assert last["phase"] == "done"
    assert "finishing" in [s["phase"] for s in snaps]


def test_replay_dsync_batch_mode_updates_overall_per_batch():
    _, snaps = _replay("dsync", _manual_lines("m5-dsync-batch250.log"))
    assert all(_problems(s) == [] for s in snaps)
    overall = [s["overall"]["items"] for s in snaps if "overall" in s]
    assert sorted(set(overall)) == [250, 500, 750, 1000, 1001]
    assert snaps[-1]["overall"] == {"items": 1001, "total": 1001, "percent": 100.0}
    # 배치마다 생성 → 복사 → 속성이 되풀이된다(단계가 되돌아가는 것이 정상).
    seq = [s["phase"] for s in snaps]
    assert sum(1 for i in range(1, len(seq)) if seq[i] == "create" and seq[i - 1] != "create") == 5


# ---- P3 크기·원문 없음 --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,tool,dryrun,manual", [
    ("dsync-execution-stdout.log", "dsync", False, False), ("dsync-preview-stdout.log", "dsync", True, False),
    ("m1-nsync-batch1M.log", "nsync", False, True), ("m2-nsync-batch20k.log", "nsync", False, True),
    ("m4-nsync-dryrun-batch20k.log", "nsync", True, True), ("m5-dsync-batch250.log", "dsync", False, True)])
def test_every_replayed_snapshot_is_small_and_carries_no_tool_text(name, tool, dryrun, manual):
    t, snaps = _replay(tool, (_manual_lines if manual else _dms_lines)(name), dryrun=dryrun)
    for s in snaps:
        data = json.dumps(s, separators=(",", ":"))
        assert len(data.encode()) <= progress.MAX_BYTES
        assert "line" not in s and "left" not in data.lower() and "remaining" not in data.lower()
    data = t.snapshot_bytes()
    assert data.isascii() and json.loads(data)["ended"] is True


# ---- R2 스로틀·심장박동 --------------------------------------------------------------------------------------------------------

def test_changes_within_the_throttle_window_merge_into_one_write():
    now = {"t": 0.0}
    t = Tracker("dsync", clock=lambda: now["t"])
    assert t.due() is True
    t.snapshot_bytes()                                       # workers(스로틀 없이 -- publish)
    for i in range(5):                                       # 3초 안에 다섯 번 바뀐다
        now["t"] = 0.5 * (i + 1)
        t.feed(TS + f"Walked {i} items in 0.1 secs (1.0 items/sec) ...\n")
        assert t.due() is False
    now["t"] = 3.0
    assert t.due() is True
    t.snapshot_bytes()
    assert t.due() is False


def test_heartbeat_without_changes():
    now = {"t": 100.0}
    t = Tracker("dsync", clock=lambda: now["t"])
    t.snapshot_bytes()
    now["t"] = 129.9
    assert t.due() is False
    now["t"] = 130.0
    assert t.due() is True
    first = json.loads(t.snapshot_bytes())
    now["t"] = 160.0
    assert json.loads(t.snapshot_bytes())["elapsed_s"] == first["elapsed_s"] + 30.0


def test_twenty_two_minute_log_replays_with_a_bounded_number_of_writes():
    # 메인 tick(0.5초)이 due 를 묻는 모양 그대로 22분 실증 로그를 줄 머리 시각으로 재생 -- 쓰기 횟수는 진행 줄 간격(10초)을
    # 따른다(3초 스로틀 + 30초 심장박동). 디렉터리 크기와 무관하게 고정 크기·고정 빈도다.
    lines = _dms_lines("dsync-execution-stdout.log")
    now = {"t": lines[0][0] - 2.0}
    t = Tracker("dsync", clock=lambda: now["t"])
    writes = [t.snapshot_bytes()]                            # workers
    t.set_phase("start")
    writes.append(t.snapshot_bytes())                        # start
    i = 0
    while i < len(lines):
        now["t"] += 0.5
        while i < len(lines) and lines[i][0] <= now["t"]:
            t.feed(lines[i][1] + "\n")
            i += 1
        if t.due():
            writes.append(t.snapshot_bytes())
    t.end()
    writes.append(t.snapshot_bytes())
    assert 100 <= len(writes) <= 150
    assert all(w is not None and len(w) <= progress.MAX_BYTES for w in writes)


def test_snapshot_larger_than_the_cap_is_skipped(monkeypatch):
    t = Tracker("dsync", clock=lambda: 0.0)
    monkeypatch.setattr(progress, "MAX_BYTES", 50)
    assert t.snapshot_bytes() is None
    assert t.due() is False                                  # 건너뛴 회차도 쓴 것으로 센다(매 tick 재시도하지 않는다)


def test_seconds_invariants_hold_even_with_an_odd_clock():
    t = Tracker("dsync", clock=lambda: 10.0)
    t.observe(TS + "Walking source path", 12.0)
    s = t.snapshot(11.0)                                     # 시계가 뒤로 -- 음수·순서 위반이 없다
    assert _problems(s) == []
    assert s["since_line_s"] <= s["phase_since_s"] <= s["elapsed_s"]
