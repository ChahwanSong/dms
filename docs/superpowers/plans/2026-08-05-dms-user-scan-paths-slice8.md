# DMS 포탈 슬라이스 8 (사용자 scan 경로 + 통계 조회) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 (스토리지, 경로)를 등록해 두면, 그 경로를 커버하는 최신 성공 scan의 **집계 통계**(파일 수·크기 히스토그램·온도 히스토그램)를 포탈에서 조회한다 — 구체 파일 경로는 절대 노출하지 않는다.

**Architecture:** 백엔드 신규는 `user_scan_paths` 리포지토리 + CRUD 라우트 + 커버링 scan 통계 조회. 커버 판정은 **DB 필드만으로** 후보를 좁히는 순수 함수이고, 아티팩트는 매치된 1건만 슬라이스 5의 안전한 읽기 헬퍼로 읽는다. 노출 필드는 화이트리스트로 골라 담는다.

**Tech Stack:** Python 3.11 / FastAPI / pytest · React 18 + Vite 5 + TS + TanStack Query v5 + Vitest · MSW 2

## Global Constraints

- 설계 문서 `docs/superpowers/specs/2026-08-05-dms-user-scan-paths-slice8-design.md`가 상위 규칙이다. 충돌 시 `2026-08-02-dms-clean-slate-design.md`가 이긴다.
- **노출은 화이트리스트다**: `summary`, `file_size_histogram`, `time_histograms`, `generated_at_epoch`만. `oldest`·`broken_paths`·`directory`·`thresholds`·`top_k`는 **절대 포함하지 않는다**. 테스트가 그 부재를 명시 단언한다 — `oldest`는 구체 파일 경로를, `directory`는 절대 마운트 경로를 담는다.
- 모든 `user_scan_paths` 조작은 **`identity.actor`의 행만** 다룬다. 타인 행은 `404 scan_path_not_found`(존재 여부를 숨긴다).
- 등록 경로의 **소유권·존재 검증은 하지 않는다**(상위 스펙 §8 명시). 경로 **형식**만 `validate_relative_path`로 검증한다.
- 서브트리 정확 집계는 불가능하다(dscan 리포트에 분해 없음). 커버 관계를 `exact` 플래그로 **명시**하고 UI가 상위 기준임을 알린다 — 서브트리 통계인 척하지 않는다.
- 아티팩트 읽기는 슬라이스 5의 `src/dms/api/artifacts.py` 헬퍼를 **재사용**한다(자체 파일 접근 코드를 새로 쓰지 않는다).
- 차트 라이브러리를 도입하지 않는다 — 히스토그램은 표로 렌더한다(§9 슬라이스에서 도입).
- 한국어 UI 문자열. 이모지 금지.
- 백엔드 테스트는 `.venv/bin/python -m pytest`(plain `python3`는 이 환경에서 깨져 있다). 프론트는 `frontend/`에서 `npm test`, `npx tsc -b`.
- 커밋은 태스크 단위, 각 태스크는 테스트 GREEN 상태로 끝난다.

---

### Task 1: 커버 판정 순수 함수 + `UserScanPathsRepository`

**Files:**
- Create: `src/dms/repositories/scan_paths.py`
- Modify: `src/dms/repositories/__init__.py` (Repositories에 등록)
- Test: `tests/test_repo_scan_paths.py` (신규)

**Interfaces:**
- Consumes: `Database`(`src/dms/db.py`), `utc_now_iso`
- Produces: `covers(scan_target, registered_path) -> bool`, `UserScanPathsRepository.{list_for,add,get_owned,delete_owned}`. Task 2·3이 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_repo_scan_paths.py`:

```python
import pytest
from dms.domain import DomainValidationError
from dms.repositories import Repositories
from dms.repositories.scan_paths import covers


@pytest.mark.parametrize("target,path,expected", [
    ("team", "team", True),            # 정확 일치
    ("team", "team/sub", True),        # 상위가 커버
    ("team", "team2", False),          # 접두사지만 다른 디렉터리
    ("team/sub", "team", False),       # 하위 스캔은 상위를 커버하지 못함
    ("./team", "team/sub", True),      # 정규화
    ("team/", "team/sub", True),
    ("a/b", "a/b/c/d", True),
    ("a/b", "a/bc", False),
])
def test_covers(target, path, expected):
    assert covers(target, path) is expected


def test_add_and_list_is_per_user(db):
    repos = Repositories(db)
    repos.scan_paths.add("alice", "s1", "team")
    repos.scan_paths.add("bob", "s1", "team")
    assert [r["path"] for r in repos.scan_paths.list_for("alice")] == ["team"]
    assert len(repos.scan_paths.list_for("bob")) == 1


def test_duplicate_raises(db):
    repos = Repositories(db)
    repos.scan_paths.add("alice", "s1", "team")
    with pytest.raises(DomainValidationError) as e:
        repos.scan_paths.add("alice", "s1", "team")
    assert e.value.reason_code == "scan_path_exists"


def test_get_owned_hides_other_users(db):
    repos = Repositories(db)
    rid = repos.scan_paths.add("alice", "s1", "team")
    assert repos.scan_paths.get_owned(rid, "alice") is not None
    assert repos.scan_paths.get_owned(rid, "bob") is None


def test_delete_owned_only(db):
    repos = Repositories(db)
    rid = repos.scan_paths.add("alice", "s1", "team")
    assert repos.scan_paths.delete_owned(rid, "bob") is False
    assert repos.scan_paths.get_owned(rid, "alice") is not None
    assert repos.scan_paths.delete_owned(rid, "alice") is True
    assert repos.scan_paths.get_owned(rid, "alice") is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_repo_scan_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.repositories.scan_paths'`

- [ ] **Step 3: 구현한다**

`src/dms/repositories/scan_paths.py` (신규). 다른 리포지토리(`storages.py`)의 스타일을 따른다:

```python
"""사용자 scan 경로 등록. 커버 판정은 DB 필드(스토리지 상대 경로)만으로 하고,
아티팩트는 매치된 잡 1건만 읽는다 (읽기는 api/artifacts.py 헬퍼가 담당)."""
import posixpath

from ..db import Database, utc_now_iso
from ..domain import DomainValidationError


def covers(scan_target: str, registered_path: str) -> bool:
    """스캔 대상이 등록 경로의 조상-또는-동일인가. 둘 다 스토리지 상대 경로다."""
    t = posixpath.normpath(scan_target or "")
    p = posixpath.normpath(registered_path or "")
    return t == p or p.startswith(t + "/")


class UserScanPathsRepository:
    def __init__(self, db: Database):
        self._db = db

    def list_for(self, username: str) -> list[dict]:
        return self._db.query(
            """SELECT * FROM user_scan_paths WHERE username = :u
               ORDER BY storage_name, path""", {"u": username})

    def add(self, username: str, storage_name: str, path: str) -> int:
        existing = self._db.query_one(
            """SELECT id FROM user_scan_paths
               WHERE username = :u AND storage_name = :s AND path = :p""",
            {"u": username, "s": storage_name, "p": path})
        if existing is not None:
            raise DomainValidationError("scan_path_exists", path)
        self._db.execute(
            """INSERT INTO user_scan_paths (username, storage_name, path, created_at)
               VALUES (:u, :s, :p, :now)""",
            {"u": username, "s": storage_name, "p": path, "now": utc_now_iso()})
        row = self._db.query_one(
            """SELECT id FROM user_scan_paths
               WHERE username = :u AND storage_name = :s AND path = :p""",
            {"u": username, "s": storage_name, "p": path})
        return row["id"]

    def get_owned(self, path_id: int, username: str) -> "dict | None":
        return self._db.query_one(
            "SELECT * FROM user_scan_paths WHERE id = :i AND username = :u",
            {"i": path_id, "u": username})

    def delete_owned(self, path_id: int, username: str) -> bool:
        if self.get_owned(path_id, username) is None:
            return False
        self._db.execute(
            "DELETE FROM user_scan_paths WHERE id = :i AND username = :u",
            {"i": path_id, "u": username})
        return True
```

`src/dms/repositories/__init__.py`의 `Repositories`에 `self.scan_paths = UserScanPathsRepository(db)`를 다른 리포지토리와 같은 방식으로 추가한다(파일을 열어 실제 패턴을 확인할 것).

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_repo_scan_paths.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과 (기준선 493 + 신규)

- [ ] **Step 5: 커밋**

```bash
git add src/dms/repositories/scan_paths.py src/dms/repositories/__init__.py tests/test_repo_scan_paths.py
git commit -m "feat(repo): user scan paths repository and coverage predicate"
```

---

### Task 2: scan 경로 CRUD API

**Files:**
- Create: `src/dms/api/routes_scan_paths.py`
- Modify: `src/dms/api/app.py` (라우터 등록)
- Test: `tests/test_api_scan_paths.py` (신규)

**Interfaces:**
- Consumes: `repos.scan_paths`(Task 1), `repos.storages.list()`, `validate_relative_path`(`src/dms/domain.py`), `require_user`
- Produces: `GET/POST /api/user/scan-paths`, `DELETE /api/user/scan-paths/{id}`. Task 4의 훅이 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_scan_paths.py`. 스토리지를 만드는 방식은 `tests/test_api_user_storages.py`(슬라이스 6)를 열어 그대로 재사용한다. 검증:

1. 비로그인 → 401.
2. 등록 → 201, 목록에 나타남.
3. **다른 사용자의 목록에는 안 나타난다**(사용자 2명으로).
4. 중복 등록 → `409 scan_path_exists`.
5. 절대경로/`..`/빈 경로 → `422 unsafe_path`.
6. 미등록·비활성 스토리지 → `422 storage_missing`.
7. 타인 행 삭제 → `404 scan_path_not_found`; 본인 삭제 → 200 후 목록에서 사라짐.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_scan_paths.py -v`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 3: 라우트를 구현한다**

`src/dms/api/routes_scan_paths.py` (신규). `routes_storages.py`의 사용자 라우터 스타일을 따른다:

```python
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError, validate_relative_path
from .auth import Identity, require_user

router = APIRouter()


class ScanPathBody(BaseModel):
    storage_name: str
    path: str


def _active_storage_names(request: Request) -> set[str]:
    return {r["storage_name"] for r in request.app.state.repos.storages.list()
            if r["enabled"] == 1}


@router.get("/api/user/scan-paths")
def list_scan_paths(request: Request, identity: Identity = Depends(require_user)):
    return request.app.state.repos.scan_paths.list_for(identity.actor)


@router.post("/api/user/scan-paths", status_code=201)
def add_scan_path(body: ScanPathBody, request: Request,
                  identity: Identity = Depends(require_user)):
    # 스펙 §8: 등록 경로의 소유권·존재 검증은 하지 않는다. 형식만 검증한다.
    try:
        path = validate_relative_path(body.path)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    if body.storage_name not in _active_storage_names(request):
        raise HTTPException(status_code=422, detail="storage_missing")
    try:
        pid = request.app.state.repos.scan_paths.add(
            identity.actor, body.storage_name, path)
    except DomainValidationError as e:
        raise HTTPException(status_code=409, detail=e.reason_code)
    return request.app.state.repos.scan_paths.get_owned(pid, identity.actor)


@router.delete("/api/user/scan-paths/{path_id}")
def delete_scan_path(path_id: int, request: Request,
                     identity: Identity = Depends(require_user)):
    if not request.app.state.repos.scan_paths.delete_owned(path_id, identity.actor):
        raise HTTPException(status_code=404, detail="scan_path_not_found")
    return {"deleted": path_id}
```

`src/dms/api/app.py`에 기존 라우터 등록 블록 안에서(**SPA 캐치올보다 앞**) 등록한다.

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_api_scan_paths.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add src/dms/api/routes_scan_paths.py src/dms/api/app.py tests/test_api_scan_paths.py
git commit -m "feat(api): user scan path CRUD"
```

---

### Task 3: 커버링 scan 통계 조회

**Files:**
- Modify: `src/dms/api/routes_scan_paths.py` (stats 라우트)
- Test: `tests/test_api_scan_path_stats.py` (신규)

**Interfaces:**
- Consumes: `covers`(Task 1), `repos.data_jobs`, 슬라이스 5의 `src/dms/api/artifacts.py`(`read_artifact`, `strip_scheme`), `settings.artifact_base_uri`
- Produces: `GET /api/user/scan-paths/{id}/stats`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_scan_path_stats.py`. 성공한 scan 잡과 그 아티팩트를 만드는 방법: `tests/test_api_artifacts.py`(슬라이스 5)가 tmp artifact base와 잡 생성을 어떻게 하는지 열어보고 그대로 재사용한다. 리포트는 실제 구조를 축약해 쓴다:

```python
REPORT = {
    "directory": "/cephfs/dms/team",
    "generated_at_epoch": 1785805962,
    "top_k": 10,
    "thresholds": {"abnormal_size_bytes": 1},
    "summary": {"total_entries": 10, "total_files": 7, "total_directories": 3,
                "total_symlinks": 0, "total_other": 0},
    "file_size_histogram": [{"bucket": "[0,4096]", "lower_inclusive": 0,
                            "upper_inclusive": 4096, "count": 7}],
    "time_histograms": {"atime": [{"bucket": "[0d,1d]", "min_age_days": 0,
                                   "max_age_days": 1, "bytes": 50}],
                        "mtime": [], "ctime": []},
    "oldest": {"atime": [{"path": "/cephfs/dms/team/secret.txt", "type": "file",
                          "size_bytes": 1, "atime": 1, "mtime": 1, "ctime": 1}]},
    "broken_paths": ["/cephfs/dms/team/broken"],
}
```

검증할 것:

1. 정확 일치 scan이 있으면 200, `covered_by == {"target": "team", "exact": True}`.
2. **화이트리스트**: 응답 키가 정확히 `{covered_by, generated_at_epoch, summary, file_size_histogram, time_histograms}`이고, `oldest`·`broken_paths`·`directory`·`thresholds`·`top_k`가 **없다**. 또한 응답 JSON 문자열 전체에 `"secret.txt"`와 `"/cephfs"`가 **등장하지 않는다**(경로 유출 방지의 직접 단언).
3. 상위 디렉터리 scan만 있으면 `exact == False`이고 `target`이 그 상위 경로다.
4. 커버하는 scan이 없으면 `404 no_covering_scan`.
5. 리포트 파일이 없는 잡이 최신이면 **다음 후보로 넘어가** 그 다음 잡의 통계를 반환한다.
6. 타인 경로 → `404 scan_path_not_found`.
7. 다른 스토리지의 scan은 커버하지 않는다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_scan_path_stats.py -v`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 3: 구현한다**

`src/dms/api/routes_scan_paths.py`에 추가한다. **후보를 DB로 좁히고 매치 1건만 읽는다**:

```python
import json

from .artifacts import ArtifactError, read_artifact, strip_scheme
from ..repositories.scan_paths import covers

_STATS_FIELDS = ("summary", "file_size_histogram", "time_histograms",
                 "generated_at_epoch")
_CANDIDATE_LIMIT = 200


@router.get("/api/user/scan-paths/{path_id}/stats")
def scan_path_stats(path_id: int, request: Request,
                    identity: Identity = Depends(require_user)):
    repos = request.app.state.repos
    row = repos.scan_paths.get_owned(path_id, identity.actor)
    if row is None:
        raise HTTPException(status_code=404, detail="scan_path_not_found")
    base = strip_scheme(request.app.state.settings.artifact_base_uri)
    for job in repos.data_jobs.succeeded_scans(row["storage_name"],
                                               limit=_CANDIDATE_LIMIT):
        if not covers(job["target"] or "", row["path"]):
            continue
        try:
            f = read_artifact(base, job["job_id"], "execution", "dscan-report.json")
            report = json.loads(f["content"])
        except (ArtifactError, ValueError):
            continue        # 이 후보는 읽을 수 없다 — 다음 후보로
        # 화이트리스트로 골라 담는다. oldest/broken_paths 는 구체 파일 경로를,
        # directory 는 절대 마운트 경로를 담으므로 절대 흘리지 않는다 (스펙 §8).
        out = {k: report.get(k) for k in _STATS_FIELDS}
        out["covered_by"] = {"target": job["target"],
                             "exact": covers(row["path"], job["target"] or "")}
        return out
    raise HTTPException(status_code=404, detail="no_covering_scan")
```

`exact` 판정: 서로가 서로를 커버하면(양방향) 같은 경로다 — `covers(target, path) and covers(path, target)`. 위 코드는 이미 `covers(target, path)`가 참인 지점이므로 역방향만 확인하면 된다.

**`DataJobsRepository.succeeded_scans(storage_name, *, limit)` 신규**를 `src/dms/repositories/data_jobs.py`에 추가한다 — `operation = 'scan' AND state = 'Succeeded' AND storage_name = :s`를 최신순(`ORDER BY created_at DESC` 또는 기존 정렬 관례)으로 `limit`건. 실제 컬럼명은 파일을 열어 확인할 것.

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_api_scan_path_stats.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add src/dms/api/routes_scan_paths.py src/dms/repositories/data_jobs.py tests/test_api_scan_path_stats.py
git commit -m "feat(api): scan path stats from the covering scan report"
```

---

### Task 4: 프론트 타입 · reason 코드 · 훅

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/features/scanpaths/useScanPaths.ts`
- Test: `frontend/src/features/scanpaths/useScanPaths.test.tsx` (신규)

**Interfaces:**
- Consumes: `apiGet`/`apiSend`, Task 2·3의 엔드포인트
- Produces: `ScanPath`·`ScanPathStats` 타입, `useScanPaths`·`useAddScanPath`·`useDeleteScanPath`·`useScanPathStats`. Task 5가 쓴다.

- [ ] **Step 1: 타입을 추가한다**

`frontend/src/lib/types.ts`에 기존 스타일로:

```ts
export interface ScanPath { id: number; storage_name: string; path: string; created_at: string }
export interface HistogramBucket {
  bucket: string; count?: number; bytes?: number;
  lower_inclusive?: number; upper_inclusive?: number;
  min_age_days?: number; max_age_days?: number;
}
export interface ScanPathStats {
  covered_by: { target: string; exact: boolean };
  generated_at_epoch: number;
  summary: Record<string, number>;
  file_size_histogram: HistogramBucket[];
  time_histograms: Record<string, HistogramBucket[]>;
}
```

- [ ] **Step 2: reason 코드를 추가한다**

`frontend/src/lib/api.ts`의 `REASON_MESSAGES`에 **없는 것만** 추가한다:

```ts
  scan_path_exists: "이미 등록된 경로입니다",
  scan_path_not_found: "등록된 경로를 찾을 수 없습니다",
  no_covering_scan: "아직 이 경로를 커버하는 scan 결과가 없습니다",
```

- [ ] **Step 3: 훅을 만든다**

`frontend/src/features/scanpaths/useScanPaths.ts` — `frontend/src/features/policies/usePolicies.ts`의 구조를 따른다:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { ScanPath, ScanPathStats } from "../../lib/types";

export const useScanPaths = () =>
  useQuery({ queryKey: ["scan-paths"],
             queryFn: () => apiGet<ScanPath[]>("/api/user/scan-paths") });

export const useAddScanPath = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { storage_name: string; path: string }) =>
      apiSend<ScanPath>("POST", "/api/user/scan-paths", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scan-paths"] }),
  });
};

export const useDeleteScanPath = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiSend("DELETE", `/api/user/scan-paths/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scan-paths"] }),
  });
};

export const useScanPathStats = (id: number, enabled: boolean) =>
  useQuery({
    queryKey: ["scan-path-stats", id],
    queryFn: () => apiGet<ScanPathStats>(`/api/user/scan-paths/${id}/stats`),
    enabled,
  });
```

- [ ] **Step 4: 훅 테스트를 쓴다**

`useScanPaths.test.tsx` — `frontend/src/features/jobs/useArtifacts.test.tsx`(슬라이스 5)의 구조를 따른다. 최소 3개: 목록 반환; 등록이 올바른 body로 POST; **`useScanPathStats`가 `enabled: false`면 요청을 보내지 않는다**(핸들러 카운터가 0임을 단언 — `data`가 undefined인 것만으로는 부족하다).

- [ ] **Step 5: 테스트·타입체크**

Run(from `frontend/`): `npx vitest run src/features/scanpaths && npx tsc -b` → PASS, tsc 0

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/features/scanpaths
git commit -m "feat(portal): scan path types, reason codes, and hooks"
```

---

### Task 5: 내 스캔 경로 화면 + 배선

**Files:**
- Create: `frontend/src/features/scanpaths/ScanPaths.tsx`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/app/AppShell.tsx`
- Test: `frontend/src/features/scanpaths/ScanPaths.test.tsx` (신규)
- Modify: `frontend/src/app/router.test.tsx`

**Interfaces:**
- Consumes: Task 4의 훅, `useUserStorages`(`frontend/src/features/storages/useUserStorages.ts`, 슬라이스 6), `Card`·`Table`·`Button`·`Dialog`, `ApiError`
- Produces: `/scan-paths` 화면

- [ ] **Step 1: 화면을 만든다**

`ScanPaths.tsx` 요건:

- 제목 "내 스캔 경로".
- 등록 폼: 스토리지 select(`useUserStorages`, `aria-label="스토리지"`, 슬라이스 6의 `StoragePicker`를 import해 재사용해도 좋다) + 경로 입력(`aria-label="경로"`) + "등록" 버튼. 등록 에러는 `(add.error as ApiError).message` 인라인.
- 목록 `Table`: 스토리지 / 경로 / 등록일 / 작업. 작업 열에 "통계 보기"와 "삭제"(확인 다이얼로그 — `StoragesList.tsx`의 `DeleteButton` 패턴, 닫을 때 `reset()`).
- **선택된 행만** `useScanPathStats(id, true)`로 조회한다. "통계 보기"를 누르기 전에는 요청이 가지 않는다.
- 통계 패널:
  - `covered_by.exact === false`면 상단에 `text-bad`로: `상위 경로 {target} 기준 집계입니다 — 이 경로만의 통계가 아닙니다`
  - 요약: `summary`의 키·값을 `<dl>`로.
  - 크기 히스토그램: `bucket`·`count` 표.
  - 온도 히스토그램: `atime`/`mtime`/`ctime` 각각 `bucket`·`bytes` 표.
  - 에러가 `no_covering_scan`이면 그 한국어 메시지를 그대로 보여준다(관리자가 scan을 실행하면 표시된다는 안내를 한 줄 덧붙인다).
- 로딩·에러 상태를 모두 렌더한다.

- [ ] **Step 2: 배선**

`router.tsx`: `/scan-paths`를 `<RequireRole><AppShell><ScanPaths /></AppShell></RequireRole>`로 추가한다(캐치올 앞). `AppShell.tsx`: 사용자 링크 영역(`/jobs`, `/jobs/new` 옆)에 "내 스캔 경로"를 추가한다 — `isAdmin` 가드를 붙이지 않는다.

- [ ] **Step 3: 테스트를 쓴다**

`ScanPaths.test.tsx`, 최소 5개:

1. 목록이 렌더된다(스토리지·경로).
2. 등록이 올바른 body로 POST되고 목록이 갱신된다.
3. **"통계 보기" 누르기 전에는 stats 요청이 0건**이다(카운터 단언).
4. 누르면 요약·히스토그램이 보이고, `exact: false`면 상위 경로 안내가 보인다.
5. `404 no_covering_scan`이면 한국어 안내가 보인다.

`router.test.tsx`에 `/scan-paths`가 **일반 사용자 세션**에서 렌더되는 케이스를 추가한다(admin 전용이 아님을 증명).

- [ ] **Step 4: 전체 테스트·타입체크**

Run(from `frontend/`): `npm test && npx tsc -b` → 전부 PASS, tsc 0

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/features/scanpaths frontend/src/app/router.tsx frontend/src/app/AppShell.tsx frontend/src/app/router.test.tsx
git commit -m "feat(portal): my scan paths screen with covering-scan stats"
```
