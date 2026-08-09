# 슬라이스 15 — runner files/bytes 파싱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** dms-job-runner가 mpifileutils 출력(dsync/nsync/drm stdout, dscan 리포트)에서 항목수·바이트수를 파싱해 `summary.json`을 항상 `{"returncode", "files", "bytes"}` 3키로 쓰고 — 이미 배포된(d24) `set_artifact`가 `data_jobs.files_count/bytes_count`를 채워 대시보드에 실값이 뜬다.

**Architecture:** 파싱은 summary의 생산자인 runner(잡 이미지)에서 한다(설계 §2.1) — 변경이 잡 이미지 하나에 갇히고 제어면 재빌드·로직 분산이 없다. `src/dms_job_runner/parsers.py`(신규)의 순수 함수 3종(sync/rm/scan)을 테스트베드 실 캡처 출력을 픽스처로 TDD하고, runner의 `_summary_from_stdout`(사문이 된 "마지막 줄 JSON" 계약)을 도구별 디스패치 `_build_summary`로 교체한다. 전면 fail-soft(설계 §4) — 파싱이 잡을 죽이는 경로는 없다. 제어면 변경은 대시보드 라벨 1건(`처리 항목/바이트`, 설계 §5)뿐이다.

**Tech Stack:** Python 3.11 — 표준 라이브러리 `re`/`json`만, pytest. 프론트는 React 18 + Vitest(라벨 1건 갱신).

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-10-dms-runner-counts-slice15-design.md`. 충돌하면 설계가 이긴다.
- **summary.json은 항상 정확히 `{"returncode": <int>, "files": <int|null>, "bytes": <int|null>}` 3키다**(설계 §2.3). 키를 빼먹거나 더하지 않는다 — 모르는 값은 null.
- **files의 의미는 "항목(items)"으로 통일한다**(설계 §2.2): dsync/nsync = 최종 `Items: N`(디렉터리+파일+링크 전체), drm = `Removed N items`, dscan = `summary.total_entries`.
- **파싱은 절대 예외를 내지 않는다**(전면 fail-soft, 설계 §4). returncode는 항상 보존하고, 실패한 값만 null로 강등한다. `_build_summary`는 어떤 예외도 삼킨다.
- **`src/dms/repositories/data_jobs.py`는 건드리지 않는다** — `set_artifact`/`_as_count`(bool·비int·음수 거부)는 d24로 배포된 정확한 2차 방어다. 시그니처·동작 불변.
- **새 pip/npm 의존성 금지.** 파서는 표준 라이브러리 `re`/`json`만 쓴다.
- **parsers.py는 순수 함수다** — `parse_scan_counts`만 파일시스템을 읽고(읽기 전용), 나머지 둘은 문자열 입력만. 쓰기·네트워크·DB 접근 금지.
- 기존 runner 테스트의 "마지막 줄 JSON" 계약 단언은 새 계약으로 **교체**한다(설계 §2.3) — 병존시키지 않는다.
- 백엔드 테스트: `.venv/bin/python -m pytest` (`python`은 PATH에 없다). 전체 스위트는 **포그라운드**로 Bash `timeout` 400000ms. 백그라운드+Monitor 조합 금지.
- 프론트: `cd frontend && npx vitest run`, 타입체크 `npx tsc -b`.
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- **origin으로 push 금지.** 커밋만 한다.
- 주석은 한국어로 "왜"를 적는다.

## 실측 고정값 (코드·테스트베드 실 아티팩트에서 직접 확인)

| 항목 | 값 |
|---|---|
| 교체 대상 | `_summary_from_stdout(stdout, returncode)`(runner.py:106) — stdout 마지막 줄 JSON 디코드, 실패 시 `{"returncode": rc}`. 호출은 runner.py:83, `summary.json` 기록은 :84. 다른 소비자 없음(grep 확인) |
| runner가 이미 아는 것 | tool = env `DMS_JR_TOOL`(runner.py:23), artifact_dir = env `DMS_JR_ARTIFACT_DIR`(runner.py:19) |
| dscan 리포트 경로 | `{artifact_dir}/dscan-report.json`(runner.py:57 — argv의 `$DMS_SCAN_REPORT` 치환에 쓰는 바로 그 경로) |
| 승격 경로(변경 금지) | `set_artifact`(data_jobs.py:208)가 `result_summary.get("files")`/`.get("bytes")`를 `_as_count`(data_jobs.py:25-31)로 승격 — bool·비int·음수는 NULL |
| 기존 runner 테스트 | `tests/test_job_runner_runner.py` 10건 — `_Recorder` 주입 패턴. 옛 JSON 계약 단언 2곳: stdout `'{"files": 5}'` → summary `{"files": 5}`, non-JSON rc=3 → `{"returncode": 3}`. 그 외 8건은 filler stdout `'{"files": 1}'`/`'{"files": 5}'`만 쓰고 summary는 단언 안 함 |
| 프론트 라벨 | `JobStatsSection.tsx:127` `처리 파일/바이트`, 단언 테스트 `JobStatsSection.test.tsx:56` |
| 실 dsync stdout(잡 60d24700) | walk 단계 패딩 `Items     : 0`, 복사 단계 중간 `Items: 10` + `Data: 50.000 B (7.000 B per file)`, `Copy data: 50.000 B (50 bytes)`, `Copy rate: 3.284 KiB/s (50 bytes in 0.015 seconds)`, 최종 블록 `Items: 10` / `Data: 50.000 B (50 bytes)` / `Rate: 0.991 KiB/s (050 bytes in 0.049 seconds)` 공존 — Task 1 픽스처에 전문 수록 |
| 실 drm stdout | `Removed 1 items` — `Walked N items` 줄과 혼재. bytes 미보고 |
| 실 dscan-report.json | top-level `directory`/`generated_at_epoch`/`top_k`/`thresholds`/`summary`/`file_size_histogram`; `summary` = `{total_entries: 10, total_files: 7, total_directories: 3, total_symlinks: 0, total_other: 0}`. **총 바이트는 없다**(크기 히스토그램뿐) |
| 정규식 사전 검증 | 설계 §3 정규식을 실 캡처에 실행해 확인: items → `['10','10']`(패딩 배제), bytes → `['50','50']`(rate 줄 배제), 패딩 단독·rate 단독 매치 0건, drm → `['1']` |

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms_job_runner/parsers.py` (신규) | mpifileutils 출력 순수 파서 3종 — `parse_sync_counts`/`parse_rm_counts`/`parse_scan_counts` |
| `tests/test_job_runner_parsers.py` (신규) | 실 캡처 픽스처로 파서 단위 테스트 |
| `src/dms_job_runner/runner.py` (수정) | `_summary_from_stdout` → `_build_summary(tool, stdout, returncode, artifact_dir)` 디스패치 |
| `tests/test_job_runner_runner.py` (수정) | 옛 JSON 계약 테스트를 새 3키 계약으로 교체 |
| `frontend/src/features/dashboard/JobStatsSection.tsx` (수정) | 라벨 `처리 파일/바이트` → `처리 항목/바이트` |
| `frontend/src/features/dashboard/JobStatsSection.test.tsx` (수정) | 라벨 단언 갱신 |

---

### Task 1: mpifileutils 출력 파서 3종 (parsers.py)

**Files:**
- Create: `src/dms_job_runner/parsers.py`
- Test: `tests/test_job_runner_parsers.py` (신규)

**Interfaces:**
- Consumes: 표준 라이브러리 `re`/`json`만. `parse_scan_counts`만 파일을 읽는다(읽기 전용).
- Produces (Task 2가 이 이름·시그니처를 그대로 쓴다):
  - `parse_sync_counts(stdout: str) -> tuple[int | None, int | None]` — dsync/nsync stdout에서 (최종 `Items: N`, 최종 `(N bytes)`). 각각 **마지막** 매치. 매치 없으면 해당 값 None.
  - `parse_rm_counts(stdout: str) -> tuple[int | None, None]` — drm stdout에서 (`Removed N items`의 마지막 매치, None). bytes는 항상 None.
  - `parse_scan_counts(report_path: str) -> tuple[int | None, None]` — dscan-report.json의 `summary.total_entries`가 비음수 int면 그 값, 아니면 None. bytes는 항상 None.
  - 세 함수 모두 예외를 던지지 않는다(fail-soft).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_job_runner_parsers.py`:

```python
"""parsers.py 단위 테스트. 픽스처는 테스트베드 실 잡의 캡처 출력을 그대로 고정한다
(잡 60d24700 dsync stdout, drm stdout, dscan-report.json 실 스키마) -- 파서가
"실제 mpifileutils가 찍는 것"을 파싱함을 픽스처 수준에서 보증한다(설계 §6)."""
import json

from dms_job_runner.parsers import (parse_rm_counts, parse_scan_counts,
                                    parse_sync_counts)

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


def test_sync_empty_and_irrelevant_stdout():
    # --quiet 억제·조기 실패 등으로 요약이 없으면 값만 null(설계 §4 fail-soft)
    assert parse_sync_counts("") == (None, None)
    assert parse_sync_counts("no summary here\nCompleted sync\n") == (None, None)


# ---- parse_rm_counts ----

def test_rm_real_capture_removed_items_bytes_always_none():
    # "Removing 1 items"(진행 줄)와 "Walked 1 items"는 배제, "Removed 1 items"만
    assert parse_rm_counts(DRM_STDOUT) == (1, None)


def test_rm_last_match_wins():
    out = "Removed 3 items\nRemoved 7 items in 0.1 seconds\n"
    assert parse_rm_counts(out) == (7, None)


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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_job_runner_parsers.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms_job_runner.parsers'`

- [ ] **Step 3: parsers.py를 구현한다**

`src/dms_job_runner/parsers.py`:

```python
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_job_runner_parsers.py -q`
Expected: PASS (12 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/dms_job_runner/parsers.py tests/test_job_runner_parsers.py
git commit -m "feat(job-runner): mpifileutils 출력 파서 3종 — sync/rm/scan 항목·바이트"
```

---

### Task 2: runner 디스패치 — `_build_summary` + 새 계약 테스트 교체

**Files:**
- Modify: `src/dms_job_runner/runner.py` (호출부 :83-84, `_summary_from_stdout` 정의 :106-113)
- Modify: `tests/test_job_runner_runner.py`

**Interfaces:**
- Consumes (Task 1): `parse_sync_counts(stdout: str) -> tuple[int | None, int | None]`, `parse_rm_counts(stdout: str) -> tuple[int | None, None]`, `parse_scan_counts(report_path: str) -> tuple[int | None, None]` — `from .parsers import ...`
- Produces:
  - `_build_summary(tool, stdout, returncode, artifact_dir) -> dict` — 항상 정확히 `{"returncode": <int>, "files": <int|null>, "bytes": <int|null>}` 3키. 디스패치: dsync·nsync → `parse_sync_counts`, drm → `parse_rm_counts`, dscan → `parse_scan_counts(f"{artifact_dir}/dscan-report.json")`, 미지 도구 → (None, None). 예외를 던지지 않는다.
  - summary.json의 이 모양을 배포된 `set_artifact`(제어면, 변경 금지)가 그대로 소비한다. preview/execution 두 단계 공통 — runner에 phase 분기가 없으므로 자동으로 동일 적용된다(설계 §3).
  - 테스트 픽스처 `DSYNC_STDOUT`/`DRM_STDOUT`/`DSCAN_REPORT`는 Task 1 테스트 파일과 **의도적으로 중복** 정의한다 — 테스트 파일끼리 import로 결합하지 않는다.

- [ ] **Step 1: 옛 JSON 계약 테스트를 새 계약의 실패하는 테스트로 교체한다**

`tests/test_job_runner_runner.py`에 다섯 가지 수정을 한다.

**(1)** `_R` 클래스 정의(`class _R:` 블록) 바로 아래에 픽스처와 헬퍼를 추가한다 (`import json`은 이미 있다):

```python
# 실측 캡처 픽스처 -- tests/test_job_runner_parsers.py와 의도적으로 중복(테스트
# 파일끼리 import로 결합하지 않는다). 내용은 잡 60d24700 dsync stdout,
# drm stdout, dscan-report.json 실 스키마 그대로.
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

DRM_STDOUT = """\
[2026-08-04T02:31:08] Walked 1 items in 0.001 seconds (1035.197 items/sec)
[2026-08-04T02:31:08] Removing 1 items
[2026-08-04T02:31:08] Removed 1 items (0.482 items/sec) in 2.077 seconds
"""

DSCAN_REPORT = {
    "directory": "/cephfs/dms/smoke-src",
    "generated_at_epoch": 1754273652,
    "top_k": 10,
    "thresholds": {},
    "summary": {"total_entries": 10, "total_files": 7, "total_directories": 3,
                "total_symlinks": 0, "total_other": 0},
    "file_size_histogram": [],
}


def _summary(rec):
    path = [p for p in rec.writes if p.endswith("summary.json")][0]
    return json.loads(rec.writes[path])


def _run(rec, env, wait_hostfile=None):
    return run_job(env, run=rec.run, write_text=rec.write_text,
                   read_text=rec.read_text, sleep=lambda s: None,
                   wait_hostfile=wait_hostfile
                   or (lambda: (["dms-w1"], "/tmp/hostfile")),
                   make_executable=rec.make_executable)
```

**(2)** `test_run_job_materializes_identity_and_runs_mpirun`에서 두 곳을 고친다.

`rec = _Recorder(rc=0, stdout='{"files": 5}')` →

```python
    rec = _Recorder(rc=0, stdout="tool output")
```

마지막 단언 3줄(`summary_writes` 이하)을 →

```python
    # summary.json은 항상 3키 계약(설계 §2.3) -- dscan인데 리포트가 없으니
    # files/bytes는 fail-soft로 null, returncode만 실린다
    assert _summary(rec) == {"returncode": 0, "files": None, "bytes": None}
```

**(3)** `test_run_job_nonjson_stdout_writes_returncode_summary` 함수 전체(3키 계약으로 대체됨)를 삭제하고 그 자리에 새 계약 테스트 6건을 넣는다:

```python
def test_run_job_dsync_summary_parses_final_items_and_bytes():
    rec = _Recorder(rc=0, stdout=DSYNC_STDOUT)
    rc = _run(rec, _env(DMS_JR_TOOL="dsync", DMS_JR_OPERATION="sync"))
    assert rc == 0
    # 실 캡처에는 중간·최종 요약이 공존한다 -- 최종 블록의 10/50이 잡혀야 한다
    # (순서 규칙 자체는 파서 단위 테스트가 값을 달리해 고정한다)
    assert _summary(rec) == {"returncode": 0, "files": 10, "bytes": 50}


def test_run_job_drm_summary_parses_removed_items():
    rec = _Recorder(rc=0, stdout=DRM_STDOUT)
    rc = _run(rec, _env(DMS_JR_TOOL="drm", DMS_JR_OPERATION="rm"))
    assert rc == 0
    # drm은 바이트를 보고하지 않는다(설계 §1) -- bytes는 null이 정답이다
    assert _summary(rec) == {"returncode": 0, "files": 1, "bytes": None}


def test_run_job_dscan_summary_reads_report(tmp_path):
    # dscan의 구조화 수치는 stdout이 아니라 {artifact_dir}/dscan-report.json에 있다
    (tmp_path / "dscan-report.json").write_text(json.dumps(DSCAN_REPORT))
    rec = _Recorder(rc=0, stdout="human readable listing\n")
    rc = _run(rec, _env(DMS_JR_ARTIFACT_DIR=str(tmp_path)))
    assert rc == 0
    assert _summary(rec) == {"returncode": 0, "files": 10, "bytes": None}


def test_run_job_dscan_summary_fail_soft_when_report_missing(tmp_path):
    # 도구가 실패해 리포트가 없어도 파싱은 잡을 죽이지 않는다(설계 §4) --
    # returncode는 보존되고 files/bytes만 null로 강등된다
    rec = _Recorder(rc=3, stdout="some non-json output")
    rc = _run(rec, _env(DMS_JR_ARTIFACT_DIR=str(tmp_path)))
    assert rc == 3
    assert _summary(rec) == {"returncode": 3, "files": None, "bytes": None}


def test_run_job_unknown_tool_summary_is_nulls():
    # 미지 도구는 파싱 규칙이 없다 -- 출력이 있어도 (None, None)으로 강등(설계 §3)
    rec = _Recorder(rc=0, stdout=DSYNC_STDOUT)
    rc = _run(rec, _env(DMS_JR_TOOL="dcp"))
    assert rc == 0
    assert _summary(rec) == {"returncode": 0, "files": None, "bytes": None}


def test_run_job_preview_phase_uses_same_summary_contract():
    # preview도 동일 계약(설계 §3): set_preview는 files/bytes를 무시하지만 dryrun
    # 예상치로 정보 가치가 있고, phase 분기가 없어 runner가 단순해진다
    rec = _Recorder(rc=0, stdout=DSYNC_STDOUT)
    rc = _run(rec, _env(DMS_JR_TOOL="dsync", DMS_JR_PHASE="preview"))
    assert rc == 0
    assert _summary(rec) == {"returncode": 0, "files": 10, "bytes": 50}
```

**(4)** 파일 맨 끝(기존 nsync 테스트들 뒤)에 nsync 디스패치 테스트를 추가한다:

```python
def test_run_job_nsync_summary_uses_sync_parser():
    # nsync 실 출력은 미확인(설계 §4의 명시적 가정) -- 여기서는 디스패치가
    # parse_sync_counts로 가는 것만 고정한다. 형식이 다르면 fail-soft로 null일 뿐이다.
    rec = _Recorder(rc=0, stdout=DSYNC_STDOUT)
    rc = _run(rec, _nsync_env(), wait_hostfile=_nsync_wait_hostfile([]))
    assert rc == 0
    assert _summary(rec) == {"returncode": 0, "files": 10, "bytes": 50}
```

**(5)** 남은 테스트들의 filler stdout에서 옛 JSON 계약의 흔적을 지운다 — 파일 전체에서 `'{"files": 5}'`와 `'{"files": 1}'`을 모두 `"ok"`로 치환한다(Edit replace_all 2회: `stdout='{"files": 5}'` → `stdout="ok"`, `stdout='{"files": 1}'` → `stdout="ok"`). 이 테스트들은 summary를 단언하지 않으므로 동작 변화가 없다 — 사문 계약이 픽스처로 남는 것만 막는다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_job_runner_runner.py -q`
Expected: FAIL — 새 계약 테스트 8건(materializes 포함)이 `{"returncode": 0}` 또는 `{"files": 5}` 등 옛 summary 모양과 불일치로 실패. 나머지(SSH/hostfile/rank.sh 계열)는 PASS 유지.

- [ ] **Step 3: runner.py를 교체한다**

**(1)** import에 파서를 추가한다 — `from .commands import (...)` 블록 아래:

```python
from .parsers import parse_rm_counts, parse_scan_counts, parse_sync_counts
```

**(2)** 호출부(runner.py:82-84)를 교체한다.

기존:

```python
    # 8. summary
    summary = _summary_from_stdout(proc.stdout, proc.returncode)
    write_text(f"{artifact_dir}/summary.json", json.dumps(summary))
```

교체:

```python
    # 8. summary — 도구별 파싱(설계 §3). preview/execution 공통: phase 분기가
    #    없어 preview의 files/bytes도 dryrun 예상치로 실린다(set_preview는 무시).
    summary = _build_summary(tool, proc.stdout, proc.returncode, artifact_dir)
    write_text(f"{artifact_dir}/summary.json", json.dumps(summary))
```

**(3)** `_summary_from_stdout` 정의(runner.py:106-113) 전체를 교체한다.

기존:

```python
def _summary_from_stdout(stdout, returncode):
    last = (stdout or "").strip().splitlines()
    if last:
        try:
            return json.loads(last[-1])
        except (ValueError, TypeError):
            pass
    return {"returncode": returncode}
```

교체:

```python
def _build_summary(tool, stdout, returncode, artifact_dir):
    """summary.json은 항상 정확히 3키 {"returncode", "files", "bytes"} -- 모르면
    null(설계 §2.3). 기존 "마지막 줄 JSON" 계약은 실 mpifileutils가 JSON을 찍지
    않아 사문이라 제거했다. 파싱이 잡을 죽이는 경로는 없다(설계 §4): 어떤 예외든
    삼키고 returncode만 보존한 채 files/bytes를 null로 강등한다 -- 제어면
    _as_count(bool·비int·음수 거부)가 2차 방어로 이미 배포되어 있다(d24)."""
    files = nbytes = None
    try:
        if tool in ("dsync", "nsync"):
            files, nbytes = parse_sync_counts(stdout or "")
        elif tool == "drm":
            files, nbytes = parse_rm_counts(stdout or "")
        elif tool == "dscan":
            files, nbytes = parse_scan_counts(f"{artifact_dir}/dscan-report.json")
        # 그 외 도구: 파싱 규칙이 없다 -- (None, None) 그대로 둔다
    except Exception:  # noqa: BLE001 -- fail-soft가 계약이다(설계 §4)
        files = nbytes = None
    return {"returncode": returncode, "files": files, "bytes": nbytes}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_job_runner_runner.py tests/test_job_runner_parsers.py -q`
Expected: PASS (runner 16 tests = 기존 9 + 신규 7, parsers 12 tests)

- [ ] **Step 5: 전체 백엔드 스위트로 회귀를 확인한다**

Run: `.venv/bin/python -m pytest -q` (포그라운드, Bash timeout 400000ms)
Expected: 전부 PASS — 특히 stepper/set_artifact 계열은 무변경이어야 한다(`src/dms/` 미수정)

- [ ] **Step 6: 커밋**

```bash
git add src/dms_job_runner/runner.py tests/test_job_runner_runner.py
git commit -m "feat(job-runner): summary.json 도구별 파싱 디스패치 — 3키 계약(returncode/files/bytes)"
```

---

### Task 3: 대시보드 라벨 — 처리 파일 → 처리 항목

**Files:**
- Modify: `frontend/src/features/dashboard/JobStatsSection.tsx:127`
- Modify: `frontend/src/features/dashboard/JobStatsSection.test.tsx:56`

**Interfaces:**
- Consumes: 없음 — 문자열 1건 교체(설계 §5). files의 의미가 "파일"이 아니라 "항목(items: 디렉터리+파일+링크)"이므로 라벨을 의미에 맞춘다(설계 §2.2). 이것만이 제어면(dms 이미지) 변경이다.
- Produces: 라벨 텍스트 `처리 항목/바이트` — API·타입·다른 컴포넌트 변경 없음.

- [ ] **Step 1: 단언을 먼저 고쳐 실패하는 테스트를 만든다**

`frontend/src/features/dashboard/JobStatsSection.test.tsx`의

```tsx
  const row = await screen.findByText("처리 파일/바이트");
```

를

```tsx
  const row = await screen.findByText("처리 항목/바이트");
```

로 교체한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/features/dashboard/JobStatsSection.test.tsx`
Expected: FAIL — `findByText("처리 항목/바이트")` 타임아웃(현 라벨은 `처리 파일/바이트`)

- [ ] **Step 3: 라벨을 고친다**

`frontend/src/features/dashboard/JobStatsSection.tsx`의

```tsx
        <span className="font-medium">처리 파일/바이트</span>{" "}
```

를

```tsx
        <span className="font-medium">처리 항목/바이트</span>{" "}
```

로 교체한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run` 그리고 `npx tsc -b`
Expected: 전체 PASS, 타입 에러 0

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/features/dashboard/JobStatsSection.tsx frontend/src/features/dashboard/JobStatsSection.test.tsx
git commit -m "feat(portal): 대시보드 라벨 처리 파일 -> 처리 항목 (items 의미 정합)"
```

---

## 플랜 이후: 배포·실증 (설계 §7 — 별도 ops, 플랜 밖)

플랜 실행(3태스크 커밋)이 끝나면 컨트롤러가 테스트베드에서 수행한다 — 플랜 태스크가 아니다 (슬라이스 12~14와 동일 관례):

1. `IMAGES=mpifileutils TAG=job4` 빌드/푸시 → `20-config.yaml`의 `DMS_JOB_IMAGE` job3→job4 반영·apply → controller rollout restart(envFrom 재주입).
2. 프론트 라벨 포함 `dms:d25` 빌드 → api/controller set image.
3. 신규 잡 실증: sync·scan·rm 각 1건 제출 → 완료 후 `data_jobs.files_count/bytes_count` 채워짐 확인(sync는 둘 다, scan·rm은 files만·bytes null), `metrics/jobs`의 `files_total/bytes_total`이 null에서 실값으로, 대시보드 "처리 항목/바이트" 표시 확인.

기존 잡 43건의 소급 백필은 하지 않는다(설계 §8 — 신규 잡부터 채워진다).

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 태스크 |
|---|---|
| §1 실측 전제(dsync 중간/최종 공존, 패딩 Items, drm Removed, dscan 리포트 스키마) | Task 1·2 픽스처가 실 캡처를 그대로 고정 |
| §2.1 파싱은 runner에서 (제어면 아님) | Task 1·2 — `src/dms/` 무변경, 잡 이미지 코드만 |
| §2.2 files = 항목(items) 통일 | Task 1 파서 의미 + Task 3 라벨 |
| §2.3 마지막 줄 JSON 계약 제거, 항상 3키 | Task 2 — `_build_summary` 교체 + 옛 테스트 교체·filler 제거 |
| §3 parsers.py 3함수 시그니처·정규식 | Task 1 (사전 검증: 실측 고정값 표) |
| §3 runner 디스패치(dsync·nsync/drm/dscan/미지), preview 동일 적용 | Task 2 (unknown-tool·preview 테스트 포함) |
| §4 전면 fail-soft, returncode 보존, nsync 미확인 가정 | Task 1(파서 None) + Task 2(try/except, dscan 리포트 없음·nsync 디스패치 테스트) |
| §5 UI 라벨 `처리 항목/바이트` + vitest 1건 | Task 3 |
| §6 실 캡처 픽스처·파서 단위·runner 통합·승격 경로 기존 커버 | Task 1·2 (test_repo_data_jobs.py 무변경 — 이미 커버) |
| §7 배포·실증 | 플랜 이후 절 — 별도 ops (관례) |
| §8 하지 않는 것(백필·프리뷰 DB 승격·추가 지표·dscan 총바이트·버전 교체) | 어떤 태스크도 건드리지 않음 |

**2. 플레이스홀더 점검** — "TBD"/"적절히 처리"/코드 없는 테스트 지시 없음. 모든 코드 단계에 실제 코드가 있고, 픽스처는 실 캡처 전문이다. Task 2의 픽스처 중복은 "Task 1과 동일" 지시가 아니라 전문을 다시 실었다(구현자가 태스크를 독립적으로 읽어도 완결).

**3. 타입 일관성** — Task 1이 내는 `parse_sync_counts/parse_rm_counts/parse_scan_counts`의 이름·시그니처를 Task 2의 import·`_build_summary`가 그대로 쓴다. `_build_summary(tool, stdout, returncode, artifact_dir)`의 인자 순서는 호출부와 정의가 일치한다. 테스트 헬퍼 `_summary(rec)`는 8곳(교체된 materializes 포함), `_run(rec, env, wait_hostfile=None)`은 신규 7곳에서 쓰며, 기존 `_env(**kw)`/`_nsync_env`/`_nsync_wait_hostfile`은 실측 확인된 실제 이름이다.

**알려진 위험:**
- **실측 캡처의 순서 증명 한계**: 잡 60d24700의 중간·최종 요약 값이 동일(10/50)해 실 픽스처만으로는 "마지막 매치가 이긴다"를 증명할 수 없다 — 값을 달리한 합성 변형 픽스처(`DSYNC_STDOUT_MID_DIFFERS`)를 추가해 순서 규칙을 별도로 고정했다. 실 픽스처는 패딩·rate 줄 배제와 전체 파이프라인을 증명한다.
- **nsync 출력 형식 미확인**(설계 §4가 명시한 가정): 디스패치가 `parse_sync_counts`로 가는 것만 고정하고, 형식이 다르면 fail-soft로 null이 된다. 실증(플랜 이후 §7)에서 실값 여부가 드러난다.
- **drm 픽스처는 재구성**: dsync는 캡처 전문이지만 drm은 실측 요지(`Removed 1 items`가 `Walked`/`Removing` 줄과 혼재)를 재구성한 것이다. 파서가 보는 `Removed N items` 문구 자체는 실측 그대로다.
- `test_execution.py`의 stub 어댑터(`read_summary` 기본값 `{"files": 0, "bytes": 0}`)는 개발용 가짜 실행 경로라 이 슬라이스와 무관 — 건드리지 않는다.
