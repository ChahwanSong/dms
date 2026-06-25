# DMS Portal 종합 운영 대시보드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영자 콘솔에 읽기 전용 종합 대시보드(스케줄러·큐/작업·worker node·데이터 잡·조치필요)를 추가하고, 정확한 잡 카운트를 위한 DMS read-only 엔드포인트 1개를 추가한다.

**Architecture:** DMS에 `GET /operations/data-jobs/summary`(GROUP BY 카운트) 하나만 추가하고, 나머지는 기존 `operations/*` API를 그대로 소비한다. Portal BFF가 여러 DMS 읽기 엔드포인트를 호출(상단 카드는 병렬 fan-in)해 운영자 전용 `/api/operator/dashboard/*`로 노출하고, React SPA가 새 "종합 대시보드" 탭에서 자동 폴링한다. 신규 DB·env 없음.

**Tech Stack:** Python/FastAPI(DMS + BFF), pytest(DMS), httpx async(BFF→DMS), Vite/React/TS SPA. SQLite(테스트)/PostgreSQL 호환.

## Global Constraints

- DMS 백엔드 변경은 **read-only 집계 엔드포인트 1개**(`/operations/data-jobs/summary`)로 한정. 쓰기/마이그레이션/스키마 변경 없음.
- 포탈 규약: DMS와 통신은 `src/portal/backend/dms_client.py`에서만. 프론트는 BFF만 호출.
- 모든 BFF 대시보드 라우트는 `require_role(ROLE_OPERATOR)`로 게이트.
- 포탈은 신규 DB/env 도입 금지(대시보드는 무상태 프록시/집계).
- SQL은 SQLite/PostgreSQL 양쪽 호환(기존 `Database` 래퍼, `?` 플레이스홀더, 표준 `GROUP BY`).
- DMS 테스트는 pytest 통과 필수. 포탈은 유닛 테스트 하니스가 없으므로 `npm run build`(tsc+vite) 통과 + 라이브 Playwright 검증을 게이트로 삼는다.
- v1 범위: 읽기 전용, DMS-레벨 노드 건강. OS 메트릭/스케줄러 제어 액션은 비범위(후속).
- 스펙: `docs/superpowers/specs/2026-06-25-dms-portal-dashboard-design.md`.

---

### Task 1: DMS — `data_job_summary` 리포 집계 + `TERMINAL_DATA_JOB_STATES`

데이터 잡을 상태/operation별로 서버측 `GROUP BY`로 카운트하는 리포 메서드. 종료 상태 집합을 도메인 상수로 정의해 `active_total`(비종료 합)을 계산한다.

**Files:**
- Modify: `src/dms/domain.py` (DataJobState enum 직후, line 67 다음)
- Modify: `src/dms/repositories/_base.py` (`from ..domain import (...)` 블록, line 11~)
- Modify: `src/dms/repositories/data_jobs.py` (`DataJobsMixin`에 메서드 추가, `list_data_jobs` 위)
- Test: `tests/test_data_job_summary.py` (신규)

**Interfaces:**
- Produces:
  - `dms.domain.TERMINAL_DATA_JOB_STATES: frozenset[DataJobState]`
  - `DataJobsMixin.data_job_summary(*, storage_name: str | None = None, operation: str | None = None) -> dict[str, Any]` — 반환 `{"total": int, "active_total": int, "by_state": dict[str,int], "by_operation": dict[str,int]}`. `DmsRepository`가 mixin 상속으로 자동 노출.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_data_job_summary.py`:

```python
from __future__ import annotations

import pytest

from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState
from dms.migrations import migrate_all
from dms.repositories import DmsRepository


@pytest.fixture()
def repository(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    operational = Database(operational_url)
    observability = Database(observability_url)
    migrate_all(operational, observability)
    return DmsRepository(operational)


def _seed(repo, operation, storage_name, state, n=1):
    for _ in range(n):
        repo.create_data_job(
            request_id="req_x",
            operation=operation,
            storage_name=storage_name,
            source=None,
            destination=None,
            target=None,
            priority=100,
            worker_pool={},
            state=state,
        )


def test_data_job_summary_counts_by_state_and_operation(repository):
    _seed(repository, "data.sync", "cephfs-a", DataJobState.RUNNING, 2)
    _seed(repository, "data.sync", "cephfs-a", DataJobState.SUCCEEDED, 3)
    _seed(repository, "data.scan", "cephfs-a", DataJobState.PENDING, 1)
    _seed(repository, "data.rm", "cephfs-b", DataJobState.FAILED, 1)

    summary = repository.data_job_summary()

    assert summary["total"] == 7
    assert summary["by_state"]["Running"] == 2
    assert summary["by_state"]["Succeeded"] == 3
    assert summary["by_state"]["Pending"] == 1
    assert summary["by_state"]["Failed"] == 1
    assert summary["by_operation"]["data.sync"] == 5
    assert summary["by_operation"]["data.scan"] == 1
    assert summary["by_operation"]["data.rm"] == 1
    # active_total = non-terminal: Running(2) + Pending(1) = 3 (Succeeded/Failed terminal)
    assert summary["active_total"] == 3


def test_data_job_summary_filters_by_storage_and_operation(repository):
    _seed(repository, "data.sync", "cephfs-a", DataJobState.RUNNING, 2)
    _seed(repository, "data.scan", "cephfs-b", DataJobState.PENDING, 4)

    only_a = repository.data_job_summary(storage_name="cephfs-a")
    assert only_a["total"] == 2
    assert only_a["by_operation"] == {"data.sync": 2}

    only_scan = repository.data_job_summary(operation="data.scan")
    assert only_scan["total"] == 4
    assert only_scan["by_state"] == {"Pending": 4}


def test_data_job_summary_empty(repository):
    summary = repository.data_job_summary()
    assert summary == {"total": 0, "active_total": 0, "by_state": {}, "by_operation": {}}
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_data_job_summary.py -x -q`
Expected: FAIL — `AttributeError: 'DmsRepository' object has no attribute 'data_job_summary'`

- [ ] **Step 3: 도메인 상수 추가**

`src/dms/domain.py` — `DataJobState` enum 정의 직후(line 67 `TIMED_OUT = "TimedOut"` 다음 줄)에 추가:

```python


TERMINAL_DATA_JOB_STATES: frozenset[DataJobState] = frozenset(
    {
        DataJobState.SUCCEEDED,
        DataJobState.FAILED,
        DataJobState.CANCELLED,
        DataJobState.TIMED_OUT,
        DataJobState.AUTHORIZATION_FAILED,
        DataJobState.PREFLIGHT_FAILED,
        DataJobState.PREVIEW_EXPIRED,
    }
)
```

- [ ] **Step 4: `_base.py` 재노출 목록에 추가**

`src/dms/repositories/_base.py`의 `from ..domain import (` 블록(line 11~)에서 `TERMINAL_LIFECYCLE_STATES` 줄 아래에 추가:

```python
    TERMINAL_DATA_JOB_STATES,
```

(이로써 `from ._base import *`를 쓰는 모든 mixin에서 사용 가능.)

- [ ] **Step 5: `data_job_summary` 메서드 구현**

`src/dms/repositories/data_jobs.py` — `DataJobsMixin` 안, `def list_data_jobs(` 정의 바로 위에 추가:

```python
    def data_job_summary(
        self,
        *,
        storage_name: str | None = None,
        operation: str | None = None,
    ) -> dict[str, Any]:
        filters: list[str] = []
        params: list[Any] = []
        if storage_name:
            filters.append("storage_name = ?")
            params.append(storage_name)
        if operation:
            filters.append("operation = ?")
            params.append(operation)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.database.connect() as connection:
            state_rows = connection.execute(
                f"SELECT state, COUNT(*) AS n FROM data_jobs {where} GROUP BY state",
                tuple(params),
            ).fetchall()
            op_rows = connection.execute(
                f"SELECT operation, COUNT(*) AS n FROM data_jobs {where} GROUP BY operation",
                tuple(params),
            ).fetchall()
        by_state = {row_to_dict(r)["state"]: row_to_dict(r)["n"] for r in state_rows}
        by_operation = {
            row_to_dict(r)["operation"]: row_to_dict(r)["n"] for r in op_rows
        }
        active_total = sum(
            n for state, n in by_state.items()
            if state not in TERMINAL_DATA_JOB_STATES
        )
        return {
            "total": sum(by_state.values()),
            "active_total": active_total,
            "by_state": by_state,
            "by_operation": by_operation,
        }
```

(`row_to_dict`, `Any`는 이미 이 파일에 존재. `TERMINAL_DATA_JOB_STATES`는 Step 4로 `import *`에서 들어옴. StrEnum 멤버는 문자열과 동치/동일 해시이므로 `"Succeeded" not in TERMINAL_DATA_JOB_STATES` 멤버십이 올바르게 동작.)

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/test_data_job_summary.py -x -q`
Expected: PASS (3 passed)

- [ ] **Step 7: 커밋**

```bash
git add src/dms/domain.py src/dms/repositories/_base.py src/dms/repositories/data_jobs.py tests/test_data_job_summary.py
git commit -m "feat(ops): add data_job_summary repo aggregation + TERMINAL_DATA_JOB_STATES"
```

---

### Task 2: DMS — `GET /operations/data-jobs/summary` 라우트

리포 집계를 HTTP로 노출. `/data-jobs/{job_id}`보다 먼저 선언해 경로 충돌을 피한다.

**Files:**
- Modify: `src/dms/api/routers/operations.py` (line 462 `list_data_jobs` 라우트와 line 481 `/data-jobs/{job_id}` 사이)
- Test: `tests/test_data_job_summary.py` (route 케이스 추가)

**Interfaces:**
- Consumes: `DmsRepository.data_job_summary(...)` (Task 1)
- Produces: `GET /api/v1/operations/data-jobs/summary?storage_name=&operation=` → Task 1 반환 dict

- [ ] **Step 1: 실패 테스트 추가**

`tests/test_data_job_summary.py` 상단 import에 추가:

```python
from fastapi.testclient import TestClient

from dms.api import create_app
from dms.repositories import ObservabilityRepository
```

그리고 테스트 추가:

```python
@pytest.fixture()
def client(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'op.db'}"
    observability_url = f"sqlite:///{tmp_path / 'obs.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    observability = ObservabilityRepository(observability_db)
    app = create_app(settings, repository, observability)
    _seed(repository, "data.sync", "cephfs-a", DataJobState.RUNNING, 2)
    _seed(repository, "data.scan", "cephfs-a", DataJobState.SUCCEEDED, 1)
    return TestClient(app)


def test_data_jobs_summary_route(client):
    resp = client.get(
        "/api/v1/operations/data-jobs/summary", headers={"x-dms-actor": "api-client"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["by_state"]["Running"] == 2
    assert body["active_total"] == 2


def test_data_jobs_summary_does_not_shadow_job_id_route(client):
    # /data-jobs/{job_id} must still resolve (a bogus id → not a 'summary' collision)
    resp = client.get(
        "/api/v1/operations/data-jobs/nonexistent-id",
        headers={"x-dms-actor": "api-client"},
    )
    assert resp.status_code != 404 or "summary" not in resp.text
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_data_job_summary.py::test_data_jobs_summary_route -x -q`
Expected: FAIL — 404 (라우트 없음) 또는 `{job_id}`가 "summary"를 잡아 잘못된 응답

- [ ] **Step 3: 라우트 구현**

`src/dms/api/routers/operations.py` — `@router.get("/data-jobs/{job_id}")` (line 481) **바로 위**에 삽입:

```python
    @router.get("/data-jobs/summary")
    def data_job_summary(
        request: Request,
        storage_name: str | None = None,
        operation: str | None = None,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.repository.data_job_summary(
            storage_name=storage_name,
            operation=operation,
        )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_data_job_summary.py -x -q`
Expected: PASS (5 passed)

- [ ] **Step 5: 전체 스위트 회귀 확인**

Run: `pytest -q`
Expected: 기존 + 신규 테스트 모두 PASS (실패 0)

- [ ] **Step 6: 커밋**

```bash
git add src/dms/api/routers/operations.py tests/test_data_job_summary.py
git commit -m "feat(ops): expose GET /operations/data-jobs/summary"
```

---

### Task 3: BFF — dms_client 읽기 메서드 + dashboard 라우터 + 마운트

BFF가 대시보드 데이터를 DMS에서 끌어와 운영자 전용으로 노출. `/summary`는 병렬 fan-in(부분 실패 허용).

**Files:**
- Modify: `src/portal/backend/dms_client.py` (상수 + GET 메서드 추가)
- Create: `src/portal/backend/routers/dashboard.py`
- Modify: `src/portal/backend/app.py` (라우터 마운트)

**Interfaces:**
- Consumes: DMS `GET /operations/{control-state,work-summary,agent-reports,data-jobs/summary,runs/active,runs/stale,action-required,data-jobs}` (+ Task 2의 summary)
- Produces (BFF):
  - `DmsClient.get_control_state(*, actor) -> dict`
  - `DmsClient.get_work_summary(*, actor) -> dict`
  - `DmsClient.list_agent_reports(*, actor, freshness: str | None = None) -> list`
  - `DmsClient.get_data_job_summary(*, actor) -> dict`
  - `DmsClient.list_active_runs(*, actor, limit: int = 200) -> list`
  - `DmsClient.list_stale_runs(*, actor) -> list`
  - `DmsClient.list_action_required(*, actor) -> list`
  - `dashboard_router(settings) -> APIRouter` (prefix `/api/operator/dashboard`)
  - Routes: `GET /summary`, `GET /nodes`, `GET /runs`, `GET /jobs`, `GET /attention`

- [ ] **Step 1: dms_client에 상수 + 읽기 메서드 추가**

`src/portal/backend/dms_client.py` — 상수 블록(line 26~29, `_DATA_JOBS` 옆)에 추가:

```python
_OPS = "/api/v1/operations/storage-mappings"   # (기존)
_OPS_BASE = "/api/v1/operations"
```

`list_data_jobs`(line 161) 아래에 메서드 추가(모두 `_request` 재사용):

```python
    async def get_control_state(self, *, actor: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"{_OPS_BASE}/control-state", actor=actor
        )

    async def get_work_summary(self, *, actor: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"{_OPS_BASE}/work-summary", actor=actor
        )

    async def list_agent_reports(
        self, *, actor: str, freshness: str | None = None
    ) -> list[dict[str, Any]]:
        params = {"freshness": freshness} if freshness else None
        return await self._request(
            "GET", f"{_OPS_BASE}/agent-reports", actor=actor, params=params
        )

    async def get_data_job_summary(self, *, actor: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"{_OPS_BASE}/data-jobs/summary", actor=actor
        )

    async def list_active_runs(
        self, *, actor: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET", f"{_OPS_BASE}/runs/active", actor=actor, params={"limit": limit}
        )

    async def list_stale_runs(self, *, actor: str) -> list[dict[str, Any]]:
        return await self._request(
            "GET", f"{_OPS_BASE}/runs/stale", actor=actor
        )

    async def list_action_required(self, *, actor: str) -> list[dict[str, Any]]:
        return await self._request(
            "GET", f"{_OPS_BASE}/action-required", actor=actor
        )
```

또한 기존 `list_data_jobs`가 필터를 받도록 확장(현재 `limit`만 받음). line 161 메서드를 다음으로 교체:

```python
    async def list_data_jobs(
        self,
        *,
        actor: str,
        limit: int = 500,
        state: str | None = None,
        operation: str | None = None,
        storage_name: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if state:
            params["state"] = state
        if operation:
            params["operation"] = operation
        if storage_name:
            params["storage_name"] = storage_name
        return await self._request(
            "GET", _DATA_JOBS, actor=actor, params=params
        )
```

> 주의: `orchestrator.py`의 `_data_jobs_by_request`가 `list_data_jobs(actor=..., limit=500)`를 호출한다. 키워드 호출이라 시그니처 확장과 호환된다(확인만).

- [ ] **Step 2: dashboard 라우터 작성**

`src/portal/backend/routers/dashboard.py` (신규):

```python
"""Operator dashboard API (role: operator).

Read-only aggregation over DMS operations endpoints. The summary endpoint
fans in several DMS calls in parallel and tolerates partial failure (a failed
section is returned as null + error so one bad panel never breaks the page).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..config import Settings
from ..deps import get_dms_client
from ..dms_client import DmsApiError, DmsClient
from ..security import ROLE_OPERATOR, require_role


def _actor(user: dict[str, Any], settings: Settings) -> str:
    return str(user.get("username") or settings.dms_actor)


async def _section(coro) -> dict[str, Any]:
    """Wrap a DMS call so a failure becomes {data:null, error:...} not a 500."""
    try:
        return {"data": await coro, "error": None}
    except DmsApiError as exc:
        return {"data": None, "error": str(exc.detail)}


def dashboard_router(settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/operator/dashboard",
        tags=["operator-dashboard"],
        dependencies=[Depends(require_role(ROLE_OPERATOR))],
    )

    @router.get("/summary")
    async def summary(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        actor = _actor(user, settings)
        control, work, jobs, reports = await asyncio.gather(
            _section(dms.get_control_state(actor=actor)),
            _section(dms.get_work_summary(actor=actor)),
            _section(dms.get_data_job_summary(actor=actor)),
            _section(dms.list_agent_reports(actor=actor)),
        )
        # node counts derived from agent reports (Fresh/Stale by role)
        nodes = {"fresh": 0, "stale": 0, "by_role": {}}
        if reports["data"]:
            for r in reports["data"]:
                fresh = r.get("freshness_status") == "Fresh"
                nodes["fresh"] += 1 if fresh else 0
                nodes["stale"] += 0 if fresh else 1
                role = r.get("worker_role") or "?"
                slot = nodes["by_role"].setdefault(role, {"fresh": 0, "stale": 0})
                slot["fresh" if fresh else "stale"] += 1
        return {
            "control_state": control,
            "work_summary": work,
            "data_jobs": jobs,
            "nodes": {"data": nodes, "error": reports["error"]},
        }

    @router.get("/nodes")
    async def nodes(
        freshness: str | None = Query(default=None),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> list[dict[str, Any]]:
        return await dms.list_agent_reports(
            actor=_actor(user, settings), freshness=freshness
        )

    @router.get("/runs")
    async def runs(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        actor = _actor(user, settings)
        active, stale = await asyncio.gather(
            _section(dms.list_active_runs(actor=actor)),
            _section(dms.list_stale_runs(actor=actor)),
        )
        return {"active": active, "stale": stale}

    @router.get("/jobs")
    async def jobs(
        state: str | None = Query(default=None),
        operation: str | None = Query(default=None),
        storage_name: str | None = Query(default=None),
        limit: int = Query(default=100, le=1000),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> list[dict[str, Any]]:
        return await dms.list_data_jobs(
            actor=_actor(user, settings),
            limit=limit,
            state=state,
            operation=operation,
            storage_name=storage_name,
        )

    @router.get("/attention")
    async def attention(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> list[dict[str, Any]]:
        return await dms.list_action_required(actor=_actor(user, settings))

    return router
```

- [ ] **Step 3: app.py에 마운트**

`src/portal/backend/app.py`에서 backup_router를 마운트하는 부분을 찾아 동일 패턴으로 추가.

import 구역:

```python
from .routers.dashboard import dashboard_router
```

`app.include_router(backup_router(settings))` 다음 줄:

```python
    app.include_router(dashboard_router(settings))
```

- [ ] **Step 4: import 그래프 + create_app 스모크**

Run:
```bash
cd /home/mason/dms-dev/dms && python -c "
from portal.backend.app import create_app
from portal.backend.config import Settings
app = create_app(Settings(allow_insecure_defaults=True, dms_api_url='http://x'))
paths = sorted(r.path for r in app.routes if getattr(r,'path','').startswith('/api/operator/dashboard'))
print('dashboard routes:', paths)
assert '/api/operator/dashboard/summary' in paths
assert '/api/operator/dashboard/nodes' in paths
assert '/api/operator/dashboard/runs' in paths
assert '/api/operator/dashboard/jobs' in paths
assert '/api/operator/dashboard/attention' in paths
print('OK')
"
```
Expected: `dashboard routes: [...]` 5개 + `OK` (필요 시 `pip install -e '.[portal]' --break-system-packages`).

- [ ] **Step 5: 커밋**

```bash
git add src/portal/backend/dms_client.py src/portal/backend/routers/dashboard.py src/portal/backend/app.py
git commit -m "feat(portal): dashboard BFF router + dms_client read-only methods"
```

---

### Task 4: Frontend — api.ts 타입/클라이언트 + helpers

대시보드 API 타입과 호출부, 상태→라벨/색 매핑 및 포매터.

**Files:**
- Modify: `src/portal/frontend/src/api.ts`
- Create: `src/portal/frontend/src/interfaces/operator/dashboard/helpers.ts`

**Interfaces:**
- Produces:
  - `operatorApi.dashboard = { summary, nodes(freshness?), runs, jobs(opts?), attention }`
  - 타입: `DashboardSummary`, `AgentReport`, `RunRow`, `DashJob`, `AttentionItem`
  - helpers: `RUN_STATE`/`JOB_STATE` 맵, `fmtTime`, `fmtAgo`, `summarizeList`

- [ ] **Step 1: api.ts에 타입 + 클라이언트 추가**

`src/portal/frontend/src/api.ts` 끝(operatorApi 정의 직전)에 타입 추가:

```typescript
// --- dashboard ---------------------------------------------------------

export interface Section<T> { data: T | null; error: string | null; }

export interface DashboardSummary {
  control_state: Section<{
    maintenance_mode: boolean; drain_mode: boolean;
    scheduling_blocked: boolean; reason: string; changed_at?: string;
  }>;
  work_summary: Section<{
    plans: { total_active: number; by_status: Record<string, number> };
    runs: {
      total_active: number; by_state: Record<string, number>;
      by_worker_id: Record<string, number>;
      lease_expiring_soon: number; stale_or_recovery: number;
    };
    requests: { action_required: number };
  }>;
  data_jobs: Section<{
    total: number; active_total: number;
    by_state: Record<string, number>; by_operation: Record<string, number>;
  }>;
  nodes: Section<{
    fresh: number; stale: number;
    by_role: Record<string, { fresh: number; stale: number }>;
  }>;
}

export interface AgentReport {
  report_id: string; cluster_name: string; node_name: string;
  worker_role: string; freshness_status: string; reported_at?: string;
  capability_summary?: {
    mounts?: string[]; tools?: string[]; csi_drivers?: string[];
    credential_count?: number;
  };
}

export interface RunRow {
  run_id: string; worker_id?: string; worker_role?: string; state: string;
  lease_seconds_remaining?: number; lease_expiring_soon?: boolean;
  resource_key?: string;
}

export interface DashJob {
  job_id: string; operation: string; storage_name: string; state: string;
  selected_tool?: string | null; updated_at?: string;
}

export interface AttentionItem { issue_type: string; [k: string]: unknown; }
```

`operatorApi` 객체 안(backup 옆)에 추가:

```typescript
  dashboard: {
    summary: () => request<DashboardSummary>("/api/operator/dashboard/summary"),
    nodes: (freshness?: string) =>
      request<AgentReport[]>(
        `/api/operator/dashboard/nodes${freshness ? `?freshness=${encodeURIComponent(freshness)}` : ""}`,
      ),
    runs: () =>
      request<{ active: Section<RunRow[]>; stale: Section<RunRow[]> }>(
        "/api/operator/dashboard/runs",
      ),
    jobs: (opts?: { state?: string; operation?: string; storage_name?: string; limit?: number }) => {
      const q = new URLSearchParams();
      if (opts?.state) q.set("state", opts.state);
      if (opts?.operation) q.set("operation", opts.operation);
      if (opts?.storage_name) q.set("storage_name", opts.storage_name);
      if (opts?.limit != null) q.set("limit", String(opts.limit));
      const qs = q.toString();
      return request<DashJob[]>(`/api/operator/dashboard/jobs${qs ? `?${qs}` : ""}`);
    },
    attention: () =>
      request<AttentionItem[]>("/api/operator/dashboard/attention"),
  },
```

- [ ] **Step 2: helpers.ts 작성**

`src/portal/frontend/src/interfaces/operator/dashboard/helpers.ts` (신규):

```typescript
// 상태 → 라벨 + 색(class). 기존 san-* / ok-num / err-num 재사용.
export const RUN_STATE: Record<string, string> = {
  Claimed: "san-degraded", Running: "san-degraded", Applying: "san-degraded",
  Verifying: "san-degraded", Blocked: "san-failed", StaleClaim: "san-failed",
  RecoveryNeeded: "san-failed", Succeeded: "san-ready", Failed: "san-failed",
};
export const JOB_STATE: Record<string, string> = {
  Pending: "san-unknown", PreflightRunning: "san-degraded",
  PreviewRunning: "san-degraded", ConfirmPending: "san-degraded",
  Confirmed: "san-degraded", Scheduled: "san-degraded", Running: "san-degraded",
  Succeeded: "san-ready", Failed: "san-failed", Cancelled: "san-failed",
  TimedOut: "san-failed", PreflightFailed: "san-failed",
  PreviewExpired: "san-failed", AuthorizationFailed: "san-failed",
};

export function stateCls(map: Record<string, string>, s?: string): string {
  return map[s || ""] || "san-unknown";
}

export function fmtTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function fmtAgo(iso?: string): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.round(s / 60)}분 전`;
  return `${Math.round(s / 3600)}시간 전`;
}

export function summarize(list?: string[], max = 3): string {
  if (!list || list.length === 0) return "—";
  return list.length <= max
    ? list.join(", ")
    : `${list.slice(0, max).join(", ")} +${list.length - max}`;
}
```

- [ ] **Step 3: 타입체크/빌드**

Run: `cd src/portal/frontend && npm run build`
Expected: 빌드 성공(타입 에러 0).

- [ ] **Step 4: 커밋**

```bash
git add src/portal/frontend/src/api.ts src/portal/frontend/src/interfaces/operator/dashboard/helpers.ts
git commit -m "feat(portal-ui): dashboard api types + helpers"
```

---

### Task 5: Frontend — Dashboard 셸 + StatusCards + 네비 탭

상단 4개 카드 + 자동 폴링 셸 + 운영자 네비에 탭 추가(드릴다운 테이블은 Task 6에서 채움).

**Files:**
- Create: `src/portal/frontend/src/interfaces/operator/dashboard/StatusCards.tsx`
- Create: `src/portal/frontend/src/interfaces/operator/dashboard/Dashboard.tsx`
- Modify: `src/portal/frontend/src/interfaces/operator/OperatorApp.tsx`
- Modify: `src/portal/frontend/src/styles.css`

**Interfaces:**
- Consumes: `operatorApi.dashboard.summary` (Task 4)
- Produces: `Dashboard` 기본 export; `StatusCards` 컴포넌트(props `{ summary: DashboardSummary | null }`)

- [ ] **Step 1: StatusCards.tsx 작성**

```tsx
import { type DashboardSummary } from "../../../api";

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="dash-card">
      <div className="dash-card-title">{title}</div>
      <div className="dash-card-body">{children}</div>
    </div>
  );
}

export default function StatusCards({ summary }: { summary: DashboardSummary | null }) {
  const cs = summary?.control_state.data;
  const ws = summary?.work_summary.data;
  const dj = summary?.data_jobs.data;
  const nd = summary?.nodes.data;
  const schedOk = cs && !cs.maintenance_mode && !cs.drain_mode && !cs.scheduling_blocked;
  return (
    <div className="dash-cards">
      <Card title="스케줄러">
        <div className={`san ${schedOk ? "san-ready" : "san-degraded"}`}>
          {cs ? (schedOk ? "정상" : "차단/점검") : "—"}
        </div>
        <ul className="dash-kv">
          <li>maintenance <b>{cs ? String(cs.maintenance_mode) : "—"}</b></li>
          <li>drain <b>{cs ? String(cs.drain_mode) : "—"}</b></li>
          <li>scheduling <b>{cs ? (cs.scheduling_blocked ? "차단" : "허용") : "—"}</b></li>
        </ul>
      </Card>
      <Card title="큐 / 작업">
        <ul className="dash-kv">
          <li>활성 plan <b>{ws?.plans.total_active ?? "—"}</b></li>
          <li>활성 run <b>{ws?.runs.total_active ?? "—"}</b></li>
          <li>lease 임박 <b className="err-num">{ws?.runs.lease_expiring_soon ?? "—"}</b></li>
          <li>stale/recovery <b className="err-num">{ws?.runs.stale_or_recovery ?? "—"}</b></li>
          <li>주의 필요 <b className="err-num">{ws?.requests.action_required ?? "—"}</b></li>
        </ul>
      </Card>
      <Card title="노드">
        <ul className="dash-kv">
          <li>Fresh <b className="ok-num">{nd?.fresh ?? "—"}</b></li>
          <li>Stale <b className="err-num">{nd?.stale ?? "—"}</b></li>
          {nd && Object.entries(nd.by_role).map(([role, c]) => (
            <li key={role}>{role} <b>{c.fresh}/{c.fresh + c.stale}</b></li>
          ))}
        </ul>
      </Card>
      <Card title="데이터 잡">
        <ul className="dash-kv">
          <li>실행 <b>{dj?.by_state?.Running ?? 0}</b></li>
          <li>대기 <b>{dj?.by_state?.Pending ?? 0}</b></li>
          <li>확인대기 <b>{dj?.by_state?.ConfirmPending ?? 0}</b></li>
          <li>실패 <b className="err-num">{dj?.by_state?.Failed ?? 0}</b></li>
          <li>진행중 합 <b>{dj?.active_total ?? "—"}</b></li>
        </ul>
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: Dashboard.tsx 셸 작성(폴링)**

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { operatorApi, type DashboardSummary } from "../../../api";
import { fmtTime } from "./helpers";
import StatusCards from "./StatusCards";

const POLL_MS = 7000;

export default function Dashboard() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [auto, setAuto] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<string>("");
  const tick = useRef(0);

  const reload = useCallback(async () => {
    try {
      const s = await operatorApi.dashboard.summary();
      setSummary(s);
      setError(null);
      setUpdatedAt(new Date().toISOString());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    if (!auto) return;
    const id = setInterval(reload, POLL_MS);
    return () => clearInterval(id);
  }, [auto, reload, tick.current]);

  return (
    <div className="inventory">
      <div className="inv-head">
        <h2>종합 대시보드</h2>
        <div className="inv-actions">
          <span className="muted small">갱신 {fmtTime(updatedAt)}</span>
          <button className="ghost" onClick={() => setAuto((v) => !v)}>
            {auto ? "자동새로고침 ⏸" : "자동새로고침 ▶"}
          </button>
          <button className="ghost" onClick={reload}>새로고침</button>
        </div>
      </div>
      {error && <div className="banner err">{error}</div>}
      <StatusCards summary={summary} />
      {/* Task 6: NodesTable / RunsTable / JobsTable / AttentionPanel */}
    </div>
  );
}
```

- [ ] **Step 3: OperatorApp에 탭 추가**

`OperatorApp.tsx`:
- import 추가: `import Dashboard from "./dashboard/Dashboard";`
- `type Section`에 `"dashboard"` 추가: `type Section = "dashboard" | "storage" | "backup";`
- `NAV` 배열 맨 앞에 추가: `{ key: "dashboard", label: "종합 대시보드", enabled: true },`
- 초기 섹션을 dashboard로: `useState<Section>("dashboard")`
- 렌더 분기 추가: `{section === "dashboard" && <Dashboard />}`

- [ ] **Step 4: styles.css에 대시보드 스타일 추가**

`src/portal/frontend/src/styles.css` 끝에 추가:

```css
.dash-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.75rem; margin: 0.5rem 0 1rem; }
.dash-card { border: 1px solid var(--border, #2a2f3a); border-radius: 8px; padding: 0.75rem 0.9rem; background: var(--panel, #161a22); }
.dash-card-title { font-size: 0.8rem; color: #9aa4b2; margin-bottom: 0.4rem; }
.dash-kv { list-style: none; margin: 0.3rem 0 0; padding: 0; font-size: 0.85rem; }
.dash-kv li { display: flex; justify-content: space-between; padding: 0.1rem 0; }
.dash-kv b { font-variant-numeric: tabular-nums; }
@media (max-width: 720px) { .dash-cards { grid-template-columns: 1fr 1fr; } }
```

(색 변수는 기존 테마에 맞춰 조정. 기존 `san`, `ok-num`, `err-num`, `banner`, `inv-*`는 재사용.)

- [ ] **Step 5: 빌드**

Run: `cd src/portal/frontend && npm run build`
Expected: 빌드 성공. 대시보드 탭이 카드 4개와 함께 렌더되는 구조.

- [ ] **Step 6: 커밋**

```bash
git add src/portal/frontend/src/interfaces/operator/dashboard/StatusCards.tsx src/portal/frontend/src/interfaces/operator/dashboard/Dashboard.tsx src/portal/frontend/src/interfaces/operator/OperatorApp.tsx src/portal/frontend/src/styles.css
git commit -m "feat(portal-ui): dashboard shell + status cards + nav tab"
```

---

### Task 6: Frontend — 드릴다운 테이블 (노드/런/잡/조치필요)

4개 드릴다운 패널을 Dashboard에 연결.

**Files:**
- Create: `src/portal/frontend/src/interfaces/operator/dashboard/NodesTable.tsx`
- Create: `src/portal/frontend/src/interfaces/operator/dashboard/RunsTable.tsx`
- Create: `src/portal/frontend/src/interfaces/operator/dashboard/JobsTable.tsx`
- Create: `src/portal/frontend/src/interfaces/operator/dashboard/AttentionPanel.tsx`
- Modify: `src/portal/frontend/src/interfaces/operator/dashboard/Dashboard.tsx`

**Interfaces:**
- Consumes: `operatorApi.dashboard.{nodes,runs,jobs,attention}` (Task 4), helpers (Task 4)
- Produces: 각 컴포넌트 기본 export(자체 로드 + 새로고침)

- [ ] **Step 1: NodesTable.tsx**

```tsx
import { useEffect, useState } from "react";
import { operatorApi, type AgentReport } from "../../../api";
import { fmtAgo, summarize } from "./helpers";

export default function NodesTable() {
  const [rows, setRows] = useState<AgentReport[]>([]);
  const [fresh, setFresh] = useState<string>("");
  useEffect(() => {
    operatorApi.dashboard.nodes(fresh || undefined).then(setRows).catch(() => setRows([]));
  }, [fresh]);
  return (
    <div className="dash-section">
      <div className="inv-head"><h3>워커 노드</h3>
        <select value={fresh} onChange={(e) => setFresh(e.target.value)}>
          <option value="">전체</option><option value="Fresh">Fresh</option><option value="Stale">Stale</option>
        </select>
      </div>
      <table className="grid"><thead><tr>
        <th>클러스터</th><th>노드</th><th>역할</th><th>상태</th><th>마운트</th><th>툴</th><th>보고</th>
      </tr></thead><tbody>
        {rows.length === 0 ? <tr><td colSpan={7} className="muted">없음</td></tr> :
          rows.map((r) => (
            <tr key={r.report_id}>
              <td data-label="클러스터">{r.cluster_name}</td>
              <td data-label="노드" className="mono small">{r.node_name}</td>
              <td data-label="역할">{r.worker_role}</td>
              <td data-label="상태"><span className={`san ${r.freshness_status === "Fresh" ? "san-ready" : "san-failed"}`}>{r.freshness_status}</span></td>
              <td data-label="마운트" className="small">{summarize(r.capability_summary?.mounts)}</td>
              <td data-label="툴" className="small">{summarize(r.capability_summary?.tools)}</td>
              <td data-label="보고" className="muted small">{fmtAgo(r.reported_at)}</td>
            </tr>
          ))}
      </tbody></table>
    </div>
  );
}
```

- [ ] **Step 2: RunsTable.tsx**

```tsx
import { useEffect, useState } from "react";
import { operatorApi, type RunRow } from "../../../api";
import { stateCls, RUN_STATE } from "./helpers";

export default function RunsTable() {
  const [active, setActive] = useState<RunRow[]>([]);
  const [stale, setStale] = useState<RunRow[]>([]);
  useEffect(() => {
    operatorApi.dashboard.runs().then((r) => {
      setActive(r.active.data || []);
      setStale(r.stale.data || []);
    }).catch(() => { setActive([]); setStale([]); });
  }, []);
  const rows = [...stale, ...active];
  return (
    <div className="dash-section">
      <h3>스케줄러 활동 {stale.length > 0 && <span className="err-num">(stale {stale.length})</span>}</h3>
      <table className="grid"><thead><tr>
        <th>run</th><th>worker</th><th>역할</th><th>상태</th><th>lease 남음</th><th>리소스</th>
      </tr></thead><tbody>
        {rows.length === 0 ? <tr><td colSpan={6} className="muted">활성 run 없음</td></tr> :
          rows.map((r) => (
            <tr key={r.run_id}>
              <td data-label="run" className="mono small">{r.run_id.slice(0, 12)}…</td>
              <td data-label="worker" className="mono small">{r.worker_id || "—"}</td>
              <td data-label="역할">{r.worker_role || "—"}</td>
              <td data-label="상태"><span className={`san ${stateCls(RUN_STATE, r.state)}`}>{r.state}</span></td>
              <td data-label="lease" className={r.lease_expiring_soon ? "err-num" : ""}>{r.lease_seconds_remaining ?? "—"}</td>
              <td data-label="리소스" className="mono small">{r.resource_key || "—"}</td>
            </tr>
          ))}
      </tbody></table>
    </div>
  );
}
```

- [ ] **Step 3: JobsTable.tsx**

```tsx
import { useEffect, useState } from "react";
import { operatorApi, type DashJob } from "../../../api";
import { stateCls, JOB_STATE, fmtAgo } from "./helpers";

const STATES = ["", "Running", "Pending", "ConfirmPending", "Succeeded", "Failed"];
const OPS = ["", "data.sync", "data.scan", "data.rm"];

export default function JobsTable() {
  const [rows, setRows] = useState<DashJob[]>([]);
  const [state, setState] = useState("");
  const [op, setOp] = useState("");
  useEffect(() => {
    operatorApi.dashboard.jobs({ state: state || undefined, operation: op || undefined, limit: 100 })
      .then(setRows).catch(() => setRows([]));
  }, [state, op]);
  return (
    <div className="dash-section">
      <div className="inv-head"><h3>데이터 잡</h3>
        <div className="inv-actions">
          <select value={state} onChange={(e) => setState(e.target.value)}>
            {STATES.map((s) => <option key={s} value={s}>{s || "모든 상태"}</option>)}
          </select>
          <select value={op} onChange={(e) => setOp(e.target.value)}>
            {OPS.map((o) => <option key={o} value={o}>{o || "모든 op"}</option>)}
          </select>
        </div>
      </div>
      <table className="grid"><thead><tr>
        <th>job</th><th>op</th><th>storage</th><th>상태</th><th>tool</th><th>갱신</th>
      </tr></thead><tbody>
        {rows.length === 0 ? <tr><td colSpan={6} className="muted">없음</td></tr> :
          rows.map((j) => (
            <tr key={j.job_id}>
              <td data-label="job" className="mono small">{j.job_id.slice(0, 12)}…</td>
              <td data-label="op">{j.operation}</td>
              <td data-label="storage" className="small">{j.storage_name}</td>
              <td data-label="상태"><span className={`san ${stateCls(JOB_STATE, j.state)}`}>{j.state}</span></td>
              <td data-label="tool" className="small">{j.selected_tool || "—"}</td>
              <td data-label="갱신" className="muted small">{fmtAgo(j.updated_at)}</td>
            </tr>
          ))}
      </tbody></table>
    </div>
  );
}
```

- [ ] **Step 4: AttentionPanel.tsx**

```tsx
import { useEffect, useState } from "react";
import { operatorApi, type AttentionItem } from "../../../api";

export default function AttentionPanel() {
  const [rows, setRows] = useState<AttentionItem[]>([]);
  useEffect(() => {
    operatorApi.dashboard.attention().then(setRows).catch(() => setRows([]));
  }, []);
  return (
    <div className="dash-section">
      <h3>조치 필요 {rows.length > 0 && <span className="err-num">({rows.length})</span>}</h3>
      {rows.length === 0 ? <p className="muted">없음</p> : (
        <ul className="dash-attention">
          {rows.map((r, i) => (
            <li key={i}><span className="san san-failed">{r.issue_type}</span>
              <span className="mono small"> {JSON.stringify(
                Object.fromEntries(Object.entries(r).filter(([k]) => k !== "issue_type")),
              ).slice(0, 160)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Dashboard.tsx에 패널 연결**

`Dashboard.tsx`의 `{/* Task 6: ... */}` 주석을 다음으로 교체하고 상단 import 추가:

```tsx
import NodesTable from "./NodesTable";
import RunsTable from "./RunsTable";
import JobsTable from "./JobsTable";
import AttentionPanel from "./AttentionPanel";
```
```tsx
      <AttentionPanel />
      <NodesTable />
      <RunsTable />
      <JobsTable />
```

styles.css에 추가:

```css
.dash-section { margin-top: 1.25rem; }
.dash-attention { list-style: none; margin: 0.5rem 0; padding: 0; }
.dash-attention li { padding: 0.25rem 0; display: flex; gap: 0.5rem; align-items: baseline; }
```

- [ ] **Step 6: 빌드**

Run: `cd src/portal/frontend && npm run build`
Expected: 빌드 성공(타입 에러 0).

- [ ] **Step 7: 커밋**

```bash
git add src/portal/frontend/src/interfaces/operator/dashboard/ src/portal/frontend/src/styles.css
git commit -m "feat(portal-ui): dashboard drill-down tables (nodes/runs/jobs/attention)"
```

---

### Task 7: 배포 — DMS 이미지(신규 엔드포인트) + 포탈 v27

**Files:** (코드 변경 없음, 배포 작업)

- [ ] **Step 1: DMS 전체 테스트 + 빌드**

```bash
cd /home/mason/dms-dev/dms && pytest -q
docker build -t pkg-01:5000/dms:dash-jobsummary .
docker push pkg-01:5000/dms:dash-jobsummary
```
Expected: 테스트 PASS, 이미지 push 성공. (Dockerfile 경로는 기존 DMS 빌드 방식 확인 후 사용.)

- [ ] **Step 2: DMS 컴포넌트 롤아웃(api 우선)**

대시보드는 `api` 컴포넌트만 신규 엔드포인트가 필요. 기존 배포 방식대로 set image:

```bash
kubectl --context dms -n dms set image deployment/dms-api dms-api=pkg-01:5000/dms:dash-jobsummary
kubectl --context dms -n dms rollout status deployment/dms-api --timeout=120s
```

- [ ] **Step 3: 신규 엔드포인트 라이브 확인**

```bash
source /data/mgmt_storage/dms-deploy/secrets/dms-env.sh
curl -s -H "Authorization: Bearer $DMS_TOKEN" -H "x-dms-actor: $DMS_ACTOR" \
  "$DMS_API_URL/api/v1/operations/data-jobs/summary" | python3 -m json.tool
```
Expected: `{total, active_total, by_state, by_operation}` JSON.

- [ ] **Step 4: 포탈 v27 빌드/푸시/롤아웃**

```bash
docker build -f src/portal/deploy/Dockerfile -t pkg-01:5000/dms-portal:v27 .
docker push pkg-01:5000/dms-portal:v27
kubectl --context dms -n dms-portal set image deployment/dms-portal portal=pkg-01:5000/dms-portal:v27
kubectl --context dms -n dms-portal rollout status deployment/dms-portal --timeout=120s
```
(portal.yaml의 image 태그도 v27로 갱신.)

- [ ] **Step 5: port-forward 재시작**

```bash
PID=$(ss -tlnHp 'sport = :30090' 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1); [ -n "$PID" ] && kill "$PID"
nohup kubectl --context dms -n dms-portal port-forward --address 0.0.0.0 svc/dms-portal 30090:80 > "$CLAUDE_JOB_DIR/tmp/pf-portal.log" 2>&1 &
sleep 3 && curl -s http://localhost:30090/healthz
```
Expected: `{"status":"ok","dms_configured":true,"db_configured":true}`

---

### Task 8: 라이브 검증 (Playwright + API)

**Files:** (검증)

- [ ] **Step 1: BFF 엔드포인트 직접 확인**

로그인 세션 쿠키로 각 라우트 200 확인(또는 Playwright 네트워크). 최소 `summary`:

```bash
# 브라우저 외 빠른 확인은 Playwright로 로그인 후 fetch. 여기서는 라우트 존재만:
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:30090/api/operator/dashboard/summary
```
Expected: `403`(미인증) — 라우트 존재 확인(로그인 후 200).

- [ ] **Step 2: Playwright 시나리오**

1. `http://localhost:30090` 접속 → 운영자 `admin`/`admin` 로그인.
2. "종합 대시보드" 탭 클릭.
3. 카드 4개(스케줄러/큐/노드/데이터잡)가 값으로 채워짐 확인(노드 Fresh 수, action_required 등 라이브 값).
4. 워커 노드 테이블에 dms-w* 노드들이 Fresh/Stale로 표시.
5. 데이터 잡 테이블 필터(상태/op) 동작.
6. 조치 필요 패널이 action-required 항목 표시.
7. 자동새로고침 ⏸ 토글 동작.

Expected: 전 패널 렌더 + 라이브 데이터 반영. 콘솔 에러 0.

- [ ] **Step 3: 부분 실패 내성 확인(선택)**

DMS 일시 차단 시 `/summary`의 해당 섹션만 `error` 표기되고 나머지 카드는 정상 — 코드상 보장(스모크).

---

### Task 9: 문서화 + 메모리

**Files:**
- Create/Modify: `install/` 포탈 대시보드 문서
- Modify: `install/README.md`(인덱스), `install/5.dms-portal-setup.md`(기능 목록에 대시보드 추가)
- Modify: 메모리 `dms-portal-deploy-state.md`, `MEMORY.md`

- [ ] **Step 1: 운영 문서 작성**

`install/5.dms-portal-setup.md` §0 "현재 기능"에 대시보드 항목 추가, 그리고 대시보드 운영 절(데이터 소스 매핑 표 + ★data-jobs/summary 신규 엔드포인트 + 읽기 전용/후속 단계)을 추가하거나 `install/7.dms-portal-dashboard.md` 신규 작성(스펙 §3 표 재사용). DMS 신규 엔드포인트는 `4.dms-dm-api.md` 또는 operations 문서에 1줄 추가.

- [ ] **Step 2: README 인덱스 갱신**

해당하면 신규 문서 줄 추가.

- [ ] **Step 3: 메모리 갱신**

`dms-portal-deploy-state.md`에 v27 + 종합 대시보드(읽기 전용, data-jobs/summary 추가) 항목 추가. 필요 시 `dms-portal-dashboard` 메모리 신설 + `MEMORY.md` 인덱스 1줄.

- [ ] **Step 4: 커밋**

```bash
git add install/ docs/superpowers/
git commit -m "docs(portal): operations dashboard guide + spec/plan"
```

---

## Self-Review (작성자 점검 결과)

- **스펙 커버리지**: 패널 7개 모두 매핑됨 — 스케줄러 카드/큐 카드/노드 카드(Task 5 StatusCards), 워커노드·스케줄러활동·데이터잡·조치필요 테이블(Task 6), ★data-jobs/summary(Task 1-2), BFF fan-in 부분실패(Task 3), 자동폴링(Task 5), 배포·검증·문서(Task 7-9). 비범위(OS메트릭·제어액션)는 §9 후속으로 명시.
- **플레이스홀더 스캔**: 코드 스텝은 모두 실제 코드 포함. "기존 방식 확인 후 사용"(Task 7 Dockerfile 경로)은 배포 환경 의존 1건으로, 명령 자체는 제시됨.
- **타입 일관성**: `data_job_summary` 반환 키(total/active_total/by_state/by_operation)가 리포→라우트→BFF→api.ts(`DashboardSummary.data_jobs`)→StatusCards까지 일치. `Section<T>`(BFF `_section`의 `{data,error}`)가 api.ts·StatusCards에서 동일하게 사용. `operatorApi.dashboard.{summary,nodes,runs,jobs,attention}`가 Task 4 정의와 Task 5-6 소비에서 일치.
- **누락 스텝**: 각 Task가 독립 테스트/빌드 게이트 + 커밋으로 종료. TDD(DMS) / build+live(포탈) 게이트 일관 적용.
