# DMS 포탈 슬라이스 5 (잡 관측성) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 자기 잡의 실패 원인을 포탈에서만 진단할 수 있다 — 전이 타임라인·수행시간·결과 요약과 함께 preflight 파드 로그와 아티팩트(stdout/stderr/summary/dscan-report)를 열람한다.

**Architecture:** 백엔드 신규는 4건 — (1) 실행 어댑터 포트에 `read_log` 추가, (2) 아티팩트 목록·본문 API(경로를 검증된 조각으로 조립 + realpath 심링크 봉쇄 + 크기 상한/tail), (3) preflight 파드 로그 API, (4) stepper가 성공 경로에서도 `artifact_uri`를 기록. 프론트는 `RequestDetail`을 확장하고 지연 로드 뷰어를 붙인다.

**Tech Stack:** Python 3.11 / FastAPI / pytest · React 18 + Vite 5 + TS + Tailwind + Radix + TanStack Query v5 + Vitest · Testing Library · MSW 2

## Global Constraints

- 설계 문서 `docs/superpowers/specs/2026-08-05-dms-portal-job-observability-slice5-design.md`가 상위 규칙이다. 충돌 시 `2026-08-02-dms-clean-slate-design.md`가 이긴다.
- **경로 탈출은 "정규화 후 검사"가 아니라 "구성으로 불가능하게" 만든다.** phase는 허용 목록, name은 `^[A-Za-z0-9._-]+$`, job_id는 `^[0-9a-f]{32}$`. 경로는 검증된 조각으로만 조립한다. 그 위에 realpath 심링크 봉쇄를 **추가로** 건다.
- 아티팩트 레이아웃은 2단 고정: `<artifact_base>/<job_id>/<phase>/<file>`. 허용 phase = `preflight`, `preview`, `execution`.
- 소유권은 `routes_jobs.py`의 `_owned_job`을 **재사용**한다(본인 잡 또는 관리자). 권한 없으면 `404 job_not_found` — 존재 여부를 숨긴다.
- 크기 상한 **256 * 1024 바이트**. 초과 시 **끝부분**만 반환하고 `truncated: true`. `?tail=N`은 마지막 N줄, N 최대 5000.
- 아티팩트 base 디렉터리가 없거나 못 읽으면 목록은 **빈 배열**, 본문은 404 — 절대 500을 내지 않는다.
- 어댑터 포트는 `read_log`만 추가하고 `submit`/`poll`/`read_summary`/`terminate`는 건드리지 않는다.
- `vcjob/` ref의 로그는 이 슬라이스 범위 밖 → `409 log_not_available`.
- 한국어 UI 문자열. 이모지 금지. 왼쪽 점 상태 뱃지 금지.
- 커밋은 태스크 단위, 각 태스크는 테스트 GREEN 상태로 끝난다.
- 백엔드 테스트는 venv로 실행한다: `.venv/bin/python -m pytest`. 프론트는 `frontend/`에서 `npm test`, `npx tsc -b`.

---

### Task 1: 실행 어댑터 포트에 `read_log` 추가

**Files:**
- Modify: `src/dms/execution.py` (`ExecutionAdapter` Protocol + `StubExecutionAdapter`)
- Modify: `src/dms/execution_volcano.py` (`VolcanoExecutionAdapter`)
- Test: `tests/test_execution_read_log.py` (신규)

**Interfaces:**
- Consumes: `KubernetesClient.read_pod_log(name, namespace)` (`execution_volcano.py:242`), ref 형식 `pod/<n>` · `pods/<n1>,<n2>` · `vcjob/<n>`
- Produces: `ExecutionAdapter.read_log(ref) -> list[tuple[str, str | None]]` — `(pod_name, log_or_None)` 목록. Task 3(로그 API)이 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_execution_read_log.py`. 기존 `tests/`에서 `VolcanoExecutionAdapter`를 생성하는 테스트(예: `test_execution_volcano*.py`)를 먼저 열어 **fake k8s 클라이언트와 어댑터 생성자 인자를 그대로 따른다**. 아래는 검증할 동작이다:

```python
def test_read_log_single_pod_ref(...):
    # ref "pod/p1" -> [("p1", "<로그 본문>")]

def test_read_log_dual_pods_ref(...):
    # ref "pods/p1,p2" -> [("p1", ...), ("p2", ...)] (순서 유지)

def test_read_log_missing_pod_yields_none(...):
    # read_pod_log가 예외를 던지면 그 항목만 (name, None), 나머지는 정상 반환

def test_read_log_rejects_vcjob_ref(...):
    # ref "vcjob/j1" -> ExecutionError("log_not_available")

def test_stub_adapter_read_log(...):
    # StubExecutionAdapter.read_log가 script된 값을 반환, 기본은 [(ref, "")]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_execution_read_log.py -v`
Expected: FAIL — `AttributeError: 'VolcanoExecutionAdapter' object has no attribute 'read_log'`

- [ ] **Step 3: 포트와 stub에 추가한다**

`src/dms/execution.py`의 `ExecutionAdapter` Protocol에 한 줄 추가:

```python
    def read_log(self, ref: str) -> "list[tuple[str, str | None]]": ...
```

`StubExecutionAdapter`에 추가(테스트 헬퍼 관례는 기존 `script`/`set_summary`를 따른다):

```python
    def read_log(self, ref: str):
        return self._logs.get(ref, [(ref, "")])

    def set_log(self, ref, entries):
        self._logs[ref] = list(entries)
```

`__init__`에 `self._logs = {}`를 추가한다.

- [ ] **Step 4: Volcano 어댑터에 구현한다**

`src/dms/execution_volcano.py`의 `VolcanoExecutionAdapter`에 추가한다. `poll`이 ref를 파싱하는 방식(`prefix, name = ref.split("/", 1)`)을 그대로 따른다:

```python
    def read_log(self, ref):
        # preflight는 파드 ref라 직접 읽는다. vcjob(launcher)은 파드 이름이 잡 이름과
        # 달라 라벨 조회가 필요하고 잡 종료 후 사라진다 — 이 슬라이스 범위 밖이므로
        # 명시적으로 거절하고, 호출자는 아티팩트 stdout/stderr로 유도한다.
        prefix, name = ref.split("/", 1)
        if prefix not in ("pod", "pods"):
            raise ExecutionError("log_not_available", prefix)
        out = []
        for pod in name.split(","):
            try:
                out.append((pod, self._k8s.read_pod_log(pod, self._namespace)))
            except Exception:
                # 파드가 이미 GC됐거나 아직 로그가 없다 — 그 항목만 비우고 계속한다.
                out.append((pod, None))
        return out
```

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_execution_read_log.py -v`
Expected: PASS

- [ ] **Step 6: 회귀 확인**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 전부 통과 (기준선 377 + 신규)

- [ ] **Step 7: 커밋**

```bash
git add src/dms/execution.py src/dms/execution_volcano.py tests/test_execution_read_log.py
git commit -m "feat(execution): add read_log to the adapter port (pod refs only)"
```

---

### Task 2: 아티팩트 목록·본문 API

**Files:**
- Create: `src/dms/api/artifacts.py` (순수 헬퍼 — 검증·조립·읽기)
- Create: `src/dms/api/routes_artifacts.py` (라우트)
- Modify: `src/dms/api/app.py` (라우터 등록)
- Test: `tests/test_artifacts_paths.py` (신규, 헬퍼 단위 테스트)
- Test: `tests/test_api_artifacts.py` (신규, API 테스트)

**Interfaces:**
- Consumes: `_owned_job` (`src/dms/api/routes_jobs.py`), `settings.artifact_base_uri` (`file://...` 스킴 포함)
- Produces: `GET /api/user/jobs/{job_id}/artifacts`, `GET /api/user/jobs/{job_id}/artifacts/{phase}/{name}`. Task 5의 훅이 쓴다.

- [ ] **Step 1: 헬퍼의 실패하는 테스트를 쓴다**

`tests/test_artifacts_paths.py`:

```python
import os
import pytest
from dms.api.artifacts import (ArtifactError, MAX_BYTES, artifact_dir,
                               list_artifacts, read_artifact, resolve_artifact_path)

JOB = "0" * 32


def test_rejects_bad_job_id():
    with pytest.raises(ArtifactError) as e:
        resolve_artifact_path("/base", "../etc", "execution", "stdout.log")
    assert e.value.reason_code == "invalid_job_id"


def test_rejects_unknown_phase():
    with pytest.raises(ArtifactError) as e:
        resolve_artifact_path("/base", JOB, "etc", "stdout.log")
    assert e.value.reason_code == "invalid_phase"


@pytest.mark.parametrize("name", ["../x", "a/b", "..", "", "x\x00y", "/abs"])
def test_rejects_bad_names(name):
    with pytest.raises(ArtifactError) as e:
        resolve_artifact_path("/base", JOB, "execution", name)
    assert e.value.reason_code == "invalid_artifact_name"


def test_builds_expected_path():
    p = resolve_artifact_path("/base", JOB, "execution", "stdout.log")
    assert p == f"/base/{JOB}/execution/stdout.log"


def test_symlink_escaping_base_is_forbidden(tmp_path):
    base = tmp_path / "base"
    (base / JOB / "execution").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    os.symlink(outside, base / JOB / "execution" / "stdout.log")
    with pytest.raises(ArtifactError) as e:
        read_artifact(str(base), JOB, "execution", "stdout.log")
    assert e.value.reason_code == "artifact_forbidden"


def test_list_is_empty_when_base_missing(tmp_path):
    assert list_artifacts(str(tmp_path / "nope"), JOB) == []


def test_list_returns_phase_and_name(tmp_path):
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("hello")
    rows = list_artifacts(str(tmp_path), JOB)
    assert [(r["phase"], r["name"], r["size"]) for r in rows] == [("execution", "stdout.log", 5)]


def test_read_truncates_large_file_from_the_end(tmp_path):
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("A" * (MAX_BYTES + 100) + "TAIL")
    out = read_artifact(str(tmp_path), JOB, "execution", "stdout.log")
    assert out["truncated"] is True
    assert out["content"].endswith("TAIL")
    assert len(out["content"].encode()) <= MAX_BYTES


def test_read_tail_lines(tmp_path):
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("\n".join(f"line{i}" for i in range(100)))
    out = read_artifact(str(tmp_path), JOB, "execution", "stdout.log", tail=3)
    assert out["content"].splitlines() == ["line97", "line98", "line99"]


def test_read_missing_file(tmp_path):
    with pytest.raises(ArtifactError) as e:
        read_artifact(str(tmp_path), JOB, "execution", "nope.log")
    assert e.value.reason_code == "artifact_not_found"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_artifacts_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.api.artifacts'`

- [ ] **Step 3: 헬퍼를 구현한다**

`src/dms/api/artifacts.py` (신규). DB나 FastAPI를 모르는 순수 모듈로 둔다:

```python
"""아티팩트 경로 검증·조립·읽기. 경로 탈출은 '정규화 후 검사'가 아니라 '구성으로 불가능하게'
만든다 — 조각을 각각 화이트리스트로 검증하고 그것만으로 경로를 조립한 뒤, 심링크 대비로
realpath 봉쇄를 추가로 건다 (상위 스펙 §5)."""
import os
import re

PHASES = ("preflight", "preview", "execution")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_BYTES = 256 * 1024
MAX_TAIL_LINES = 5000


class ArtifactError(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(reason_code)


def strip_scheme(base_uri: str) -> str:
    return base_uri[len("file://"):] if base_uri.startswith("file://") else base_uri


def artifact_dir(base: str, job_id: str) -> str:
    if not JOB_ID_RE.match(job_id or ""):
        raise ArtifactError("invalid_job_id", job_id or "")
    return os.path.join(base, job_id)


def resolve_artifact_path(base: str, job_id: str, phase: str, name: str) -> str:
    root = artifact_dir(base, job_id)
    if phase not in PHASES:
        raise ArtifactError("invalid_phase", phase or "")
    if not NAME_RE.match(name or ""):
        raise ArtifactError("invalid_artifact_name", name or "")
    return os.path.join(root, phase, name)


def _assert_contained(base: str, job_id: str, path: str) -> None:
    root = os.path.realpath(artifact_dir(base, job_id))
    real = os.path.realpath(path)
    if real != root and not real.startswith(root + os.sep):
        raise ArtifactError("artifact_forbidden", name_of(path))


def name_of(path: str) -> str:
    return os.path.basename(path)


def list_artifacts(base: str, job_id: str) -> list[dict]:
    root = artifact_dir(base, job_id)
    out = []
    for phase in PHASES:
        d = os.path.join(root, phase)
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            if not NAME_RE.match(name):
                continue
            p = os.path.join(d, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if not os.path.isfile(p):
                continue
            out.append({"phase": phase, "name": name, "size": st.st_size,
                        "modified_at": int(st.st_mtime)})
    return out


def read_artifact(base: str, job_id: str, phase: str, name: str,
                  tail: int | None = None) -> dict:
    path = resolve_artifact_path(base, job_id, phase, name)
    if not os.path.isfile(path):
        raise ArtifactError("artifact_not_found", name)
    _assert_contained(base, job_id, path)
    size = os.path.getsize(path)
    truncated = False
    with open(path, "rb") as f:
        if size > MAX_BYTES:
            f.seek(size - MAX_BYTES)
            truncated = True
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    if tail is not None:
        lines = text.splitlines()
        capped = min(max(tail, 1), MAX_TAIL_LINES)
        if len(lines) > capped:
            truncated = True
            lines = lines[-capped:]
        text = "\n".join(lines)
    return {"phase": phase, "name": name, "size": size,
            "truncated": truncated, "content": text}
```

- [ ] **Step 4: 헬퍼 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_artifacts_paths.py -v`
Expected: PASS

- [ ] **Step 5: API 테스트를 쓴다**

`tests/test_api_artifacts.py`. 잡을 만드는 방법은 기존 `tests/test_api_jobs.py`를 열어 그 픽스처/헬퍼를 그대로 재사용한다(요청 생성 → planner로 잡 생성, 또는 리포지토리 직접 삽입 — 그 파일이 하는 방식을 따를 것). 검증할 것:

- 목록: 잡의 아티팩트 디렉터리에 파일을 만들어 두고 `GET .../artifacts`가 phase·name·size를 반환.
- 본문: `GET .../artifacts/execution/stdout.log`가 내용을 반환.
- 잘못된 phase → 422 `invalid_phase`; 슬래시 포함 name → 404 또는 422(라우트 매칭에 따라 — 실제로 어떤 코드가 나오는지 확인하고 그 값을 단언하되, 결코 파일을 반환하지 않음을 반드시 단언).
- `..` 시도 → 파일을 반환하지 않음.
- 타인 잡 → 404 `job_not_found`; 관리자는 200.
- 아티팩트 base 디렉터리 부재 → 목록 200 빈 배열, 본문 404.

- [ ] **Step 6: 라우트를 구현한다**

`src/dms/api/routes_artifacts.py` (신규). `routes_jobs.py`의 스타일을 따른다:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from .artifacts import ArtifactError, list_artifacts, read_artifact, strip_scheme
from .auth import Identity, require_user
from .routes_jobs import _owned_job

router = APIRouter()


def _base(request: Request) -> str:
    return strip_scheme(request.app.state.settings.artifact_base_uri)


@router.get("/api/user/jobs/{job_id}/artifacts")
def list_job_artifacts(job_id: str, request: Request,
                       identity: Identity = Depends(require_user)):
    _owned_job(request, job_id, identity)
    try:
        return list_artifacts(_base(request), job_id)
    except ArtifactError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)


@router.get("/api/user/jobs/{job_id}/artifacts/{phase}/{name}")
def get_job_artifact(job_id: str, phase: str, name: str, request: Request,
                     tail: int | None = Query(default=None, ge=1),
                     identity: Identity = Depends(require_user)):
    _owned_job(request, job_id, identity)
    try:
        return read_artifact(_base(request), job_id, phase, name, tail=tail)
    except ArtifactError as e:
        status = {"artifact_not_found": 404, "artifact_forbidden": 403}.get(
            e.reason_code, 422)
        raise HTTPException(status_code=status, detail=e.reason_code)
```

`src/dms/api/app.py`에 다른 라우터들과 같은 방식으로 `artifacts_router`를 등록한다. **주의: SPA 캐치올(`@app.get("/{full_path:path}")`) 마운트보다 앞에 등록해야 한다** — 기존 라우터 등록 블록 안에 넣으면 된다.

- [ ] **Step 7: API 테스트 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_api_artifacts.py -v`
Expected: PASS
Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 전부 통과

- [ ] **Step 8: 커밋**

```bash
git add src/dms/api/artifacts.py src/dms/api/routes_artifacts.py src/dms/api/app.py tests/test_artifacts_paths.py tests/test_api_artifacts.py
git commit -m "feat(api): artifact listing and read with constructed-path containment"
```

---

### Task 3: preflight 파드 로그 API

**Files:**
- Modify: `src/dms/api/routes_artifacts.py` (로그 라우트 추가)
- Test: `tests/test_api_job_logs.py` (신규)

**Interfaces:**
- Consumes: `ExecutionAdapter.read_log` (Task 1), `request.app.state.execution_adapter`, `job["phase_refs"]`
- Produces: `GET /api/user/jobs/{job_id}/logs?phase=preflight`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_job_logs.py`. `tests/test_api_artifacts.py`가 잡을 만드는 방식을 그대로 쓴다. 검증:

- `phase_refs = {"preflight": "pod/p1"}`인 잡 → 200, `entries`가 `[{"pod": "p1", "log": ...}]`.
- `phase_refs`에 그 phase가 없으면 → 404 `log_ref_not_found`.
- `vcjob/...` ref → 409 `log_not_available`.
- 허용 밖 phase → 422 `invalid_phase`.
- 타인 잡 → 404 `job_not_found`.

테스트용 어댑터는 `StubExecutionAdapter`의 `set_log`(Task 1)로 스크립트한다. `client` 픽스처가 어댑터를 주입하는 방식은 `tests/test_api_jobs.py`의 cancel 테스트가 `execution_adapter`를 다루는 방식을 보고 그대로 따른다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_job_logs.py -v`
Expected: FAIL (404 — 라우트 없음)

- [ ] **Step 3: 라우트를 구현한다**

`src/dms/api/routes_artifacts.py`에 추가:

```python
from ..execution import ExecutionError
from .artifacts import MAX_BYTES, PHASES


@router.get("/api/user/jobs/{job_id}/logs")
def get_job_logs(job_id: str, request: Request, phase: str = Query(default="preflight"),
                 tail: int | None = Query(default=None, ge=1),
                 identity: Identity = Depends(require_user)):
    job = _owned_job(request, job_id, identity)
    if phase not in PHASES:
        raise HTTPException(status_code=422, detail="invalid_phase")
    ref = (job["phase_refs"] or {}).get(phase)
    if not ref:
        raise HTTPException(status_code=404, detail="log_ref_not_found")
    try:
        entries = request.app.state.execution_adapter.read_log(ref)
    except ExecutionError as e:
        raise HTTPException(status_code=409, detail=e.reason_code)
    out = []
    for pod, log in entries:
        if log is not None:
            if tail is not None:
                log = "\n".join(log.splitlines()[-tail:])
            if len(log.encode()) > MAX_BYTES:
                log = log.encode()[-MAX_BYTES:].decode("utf-8", errors="replace")
        out.append({"pod": pod, "log": log})
    return {"phase": phase, "ref": ref, "entries": out}
```

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_api_job_logs.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add src/dms/api/routes_artifacts.py tests/test_api_job_logs.py
git commit -m "feat(api): preflight pod log endpoint"
```

---

### Task 4: stepper가 성공 경로에서도 artifact_uri를 기록

**Files:**
- Modify: `src/dms/stepper.py` (`_poll_execution`의 SUCCEEDED 분기)
- Test: `tests/test_stepper_artifact_uri.py` (신규)

**Interfaces:**
- Consumes: `settings.artifact_base_uri`, `data_jobs.set_artifact(job_id, artifact_uri=, result_summary=)`
- Produces: 성공한 잡의 `artifact_uri`가 `f"{artifact_base_uri}/{job_id}"`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_stepper_artifact_uri.py`. 기존 stepper 테스트(`tests/test_stepper*.py`)에서 잡을 실행 완료까지 전진시키는 헬퍼를 그대로 재사용한다. 검증: scan 잡(preview 게이트 없음)이 Succeeded가 된 뒤 `get_job(jid)["artifact_uri"]`가 `f"{settings.artifact_base_uri}/{jid}"`와 같다. (교정 전에는 None이라 FAIL한다.)

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_stepper_artifact_uri.py -v`
Expected: FAIL — `assert None == 'file:///.../<jid>'`

- [ ] **Step 3: 구현한다**

`src/dms/stepper.py`의 `_poll_execution` SUCCEEDED 분기에서

```python
            self._repos.data_jobs.set_artifact(job["job_id"], artifact_uri=None,
                                               result_summary=summary)
```

를 다음으로 바꾼다:

```python
            # 성공 경로도 URI를 남긴다 — preview를 거치지 않는 scan 잡은 여기서만
            # 기록되고, 없으면 포탈이 아티팩트를 가리킬 수 없다.
            self._repos.data_jobs.set_artifact(
                job["job_id"],
                artifact_uri=f"{self._settings.artifact_base_uri}/{job['job_id']}",
                result_summary=summary)
```

(`set_artifact`는 `COALESCE`라 preview에서 이미 기록된 sync 잡도 같은 값으로 유지된다.)

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_stepper_artifact_uri.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add src/dms/stepper.py tests/test_stepper_artifact_uri.py
git commit -m "fix(stepper): record artifact_uri on the execution success path"
```

---

### Task 5: 프론트 타입 · reason 코드 · 훅

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/features/jobs/useArtifacts.ts`
- Test: `frontend/src/features/jobs/useArtifacts.test.tsx` (신규)

**Interfaces:**
- Consumes: `apiGet` (`frontend/src/lib/api.ts`), Task 2·3의 엔드포인트
- Produces: `ArtifactEntry`·`ArtifactFile`·`JobLogs` 타입, `useArtifacts`·`useArtifactFile`·`useJobLogs`. Task 6·7이 쓴다.

- [ ] **Step 1: 타입을 추가한다**

`frontend/src/lib/types.ts`에 기존 스타일(간결한 다중 필드 한 줄)로 추가:

```ts
export interface ArtifactEntry { phase: string; name: string; size: number; modified_at: number }
export interface ArtifactFile {
  phase: string; name: string; size: number; truncated: boolean; content: string;
}
export interface JobLogs {
  phase: string; ref: string; entries: { pod: string; log: string | null }[];
}
```

또한 `DataJob`에 `artifact_uri: string | null;`을 추가한다(백엔드가 이미 내려주지만 타입에 없다).

- [ ] **Step 2: reason 코드를 추가한다**

`frontend/src/lib/api.ts`의 `REASON_MESSAGES`에:

```ts
  artifact_not_found: "아티팩트를 찾을 수 없습니다",
  artifact_forbidden: "허용되지 않은 아티팩트 경로입니다",
  invalid_phase: "알 수 없는 실행 단계입니다",
  invalid_artifact_name: "아티팩트 이름이 올바르지 않습니다",
  log_ref_not_found: "이 단계의 로그 참조가 없습니다",
  log_not_available: "이 단계는 파드 로그를 제공하지 않습니다 — 아티팩트를 확인하세요",
```

- [ ] **Step 3: 훅을 만든다**

`frontend/src/features/jobs/useArtifacts.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { ArtifactEntry, ArtifactFile, JobLogs } from "../../lib/types";

export const useArtifacts = (jobId: string) =>
  useQuery({ queryKey: ["artifacts", jobId],
             queryFn: () => apiGet<ArtifactEntry[]>(`/api/user/jobs/${jobId}/artifacts`) });

export const useArtifactFile = (jobId: string, phase: string, name: string, enabled: boolean) =>
  useQuery({
    queryKey: ["artifact", jobId, phase, name],
    queryFn: () => apiGet<ArtifactFile>(`/api/user/jobs/${jobId}/artifacts/${phase}/${name}`),
    enabled,
  });

export const useJobLogs = (jobId: string, phase: string, enabled: boolean) =>
  useQuery({
    queryKey: ["joblogs", jobId, phase],
    queryFn: () => apiGet<JobLogs>(`/api/user/jobs/${jobId}/logs?phase=${phase}`),
    enabled,
  });
```

- [ ] **Step 4: 훅 테스트를 쓴다**

`frontend/src/features/jobs/useArtifacts.test.tsx` — `frontend/src/features/policies/usePolicies.test.tsx`의 구조(renderHook + MSW + QueryClientProvider, `retry: false`)를 따른다. 검증:

1. `useArtifacts`가 목록을 반환한다.
2. `useArtifactFile`이 `enabled: false`면 **요청을 보내지 않는다**(MSW 핸들러에 카운터를 두고 0인지 단언).
3. `enabled: true`면 올바른 URL로 요청하고 본문을 반환한다.

- [ ] **Step 5: 테스트·타입체크**

Run(from `frontend/`): `npx vitest run src/features/jobs/useArtifacts.test.tsx && npx tsc -b`
Expected: PASS, tsc 0

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/features/jobs/useArtifacts.ts frontend/src/features/jobs/useArtifacts.test.tsx
git commit -m "feat(portal): artifact/log types, reason codes, and hooks"
```

---

### Task 6: RequestDetail 강화 — 타임라인 · 수행시간 · 요약 · 로딩/에러

**Files:**
- Modify: `frontend/src/features/jobs/RequestDetail.tsx`
- Create: `frontend/src/features/jobs/Timeline.tsx`
- Test: `frontend/src/features/jobs/RequestDetail.test.tsx` (신규)

**Interfaces:**
- Consumes: `useRequest`·`useRequestJobs`·`useCancelJob` (`frontend/src/features/jobs/useJobs.ts`), `Transition`·`RequestDetail`·`DataJob` 타입, `Card`·`StatusPill`·`Button`, `ConfirmDialog`
- Produces: `Timeline` 컴포넌트(Task 7이 재사용하지 않지만 파일 분리로 RequestDetail을 작게 유지)

- [ ] **Step 1: Timeline 컴포넌트를 만든다**

`frontend/src/features/jobs/Timeline.tsx`:

```tsx
import type { Transition } from "../../lib/types";
export function Timeline({ transitions }: { transitions: Transition[] }) {
  if (!transitions.length) return <p className="text-muted text-sm">전이 이력이 없습니다</p>;
  return (
    <ol className="space-y-1 text-sm">
      {transitions.map((t, i) => (
        <li key={i} className="flex flex-wrap gap-2">
          <span className="text-muted tabular-nums">{t.at}</span>
          <span>{t.from_state ?? "—"} → {t.to_state}</span>
          {t.reason_code && <span className="text-bad">{t.reason_code}</span>}
          {t.actor && <span className="text-muted">({t.actor})</span>}
        </li>
      ))}
    </ol>
  );
}
```

- [ ] **Step 2: RequestDetail을 확장한다**

`frontend/src/features/jobs/RequestDetail.tsx`를 다음 요건으로 고친다(기존 confirm/cancel 동작은 그대로 유지):

- `req.isLoading || jobs.isLoading`이면 "불러오는 중…", `req.isError`면 `(req.error as ApiError).message`.
- 요청 카드에 요청자(`requester_id`)와 **수행시간**을 추가한다. 수행시간 헬퍼를 파일 안에 둔다:

```tsx
function durationText(from?: string, to?: string): string {
  if (!from || !to) return "—";
  const ms = new Date(to).getTime() - new Date(from).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  return s % 60 === 0 ? `${m}분` : `${m}분 ${s % 60}초`;
}
```

시작은 `req.data.created_at`, 끝은 마지막 전이(`transitions.at(-1)?.at`) — 전이가 없으면 `updated_at`.
- 요청 전이 타임라인(`<Timeline transitions={req.data.transitions} />`)을 카드로 넣는다.
- 잡 카드에 `result_summary`가 있으면 키·값 목록으로 렌더한다(객체가 아니면 문자열 그대로).
- 잡 카드에 잡 전이 타임라인도 넣는다.

- [ ] **Step 3: 테스트를 쓴다**

`frontend/src/features/jobs/RequestDetail.test.tsx` — MSW로 `/api/user/requests/:id`와 `/api/user/requests/:id/jobs`를 스텁하고, `MemoryRouter` + 라우트 파라미터로 렌더한다(기존 `frontend/src/app/router.test.tsx`가 파라미터 있는 화면을 렌더하는 방식을 참고). 최소 4개:

1. 로딩 상태가 먼저 보인다.
2. 요청 전이 타임라인이 순서대로 렌더되고 실패 전이의 `reason_code`가 보인다.
3. 수행시간이 계산돼 보인다(예: created_at→마지막 전이가 90초면 "1분 30초").
4. 잡의 `result_summary` 키·값이 보인다.

- [ ] **Step 4: 테스트·타입체크**

Run(from `frontend/`): `npx vitest run src/features/jobs && npx tsc -b`
Expected: PASS, tsc 0

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/features/jobs/RequestDetail.tsx frontend/src/features/jobs/Timeline.tsx frontend/src/features/jobs/RequestDetail.test.tsx
git commit -m "feat(portal): request detail timeline, duration, and result summary"
```

---

### Task 7: 아티팩트·로그 뷰어

**Files:**
- Create: `frontend/src/features/jobs/JobViewer.tsx`
- Modify: `frontend/src/features/jobs/RequestDetail.tsx` (잡 카드에 뷰어 삽입)
- Test: `frontend/src/features/jobs/JobViewer.test.tsx` (신규)

**Interfaces:**
- Consumes: `useArtifacts`·`useArtifactFile`·`useJobLogs` (Task 5), `Button`, `ApiError`
- Produces: `JobViewer({ jobId })`

- [ ] **Step 1: 뷰어를 만든다**

`frontend/src/features/jobs/JobViewer.tsx` 요건:

- `useArtifacts(jobId)`로 파일 목록을 얻는다. 로딩 중이면 "불러오는 중…", 에러면 메시지.
- 탭 버튼 목록 = 아티팩트 파일 각각(`{phase}/{name}` 라벨) + 맨 끝에 "preflight 로그".
- 선택된 탭이 없으면 본문을 **요청하지 않는다**(`enabled: false`). 탭을 누르면 그때 조회한다.
- 아티팩트 탭: `useArtifactFile(jobId, phase, name, selected)`. `truncated`면 "뒷부분만 표시" 배지를 본문 위에 보여준다. 본문은 `<pre className="overflow-x-auto text-xs whitespace-pre-wrap">`.
- 로그 탭: `useJobLogs(jobId, "preflight", selected)`. 각 엔트리를 파드 이름 + `<pre>`로. `log`가 null이면 "파드 로그를 더 이상 조회할 수 없습니다". 에러 코드가 `log_not_available`/`log_ref_not_found`면 그 한국어 메시지를 그대로 보여준다(`ApiError.message`).
- 아티팩트가 하나도 없고 로그도 없으면 "표시할 아티팩트가 없습니다".

- [ ] **Step 2: RequestDetail에 끼운다**

각 잡 카드 안, 전이 타임라인 아래에 `<JobViewer jobId={j.job_id} />`를 렌더한다.

- [ ] **Step 3: 테스트를 쓴다**

`frontend/src/features/jobs/JobViewer.test.tsx`, 최소 4개:

1. 아티팩트 목록이 탭으로 렌더된다(`execution/stdout.log` 등).
2. **탭을 누르기 전에는 본문 요청이 가지 않는다** — 본문 핸들러 호출 카운터가 0.
3. 탭을 누르면 본문이 표시되고, `truncated: true`면 배지가 보인다.
4. 로그 탭에서 409 `log_not_available`이면 한국어 안내가 보인다.

- [ ] **Step 4: 전체 프론트 테스트·타입체크**

Run(from `frontend/`): `npm test && npx tsc -b`
Expected: 전부 PASS, tsc 0

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/features/jobs/JobViewer.tsx frontend/src/features/jobs/JobViewer.test.tsx frontend/src/features/jobs/RequestDetail.tsx
git commit -m "feat(portal): artifact and pod-log viewer on request detail"
```
