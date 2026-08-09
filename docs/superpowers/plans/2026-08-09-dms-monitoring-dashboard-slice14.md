# 슬라이스 14 — 모니터링 대시보드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영자가 포탈 대시보드 한 화면에서 (1) 잡 처리 통계(총계·성공/실패율·처리량·분해·실패 사유·수행시간 분포), (2) 노드 자원 시계열(load/메모리/디스크/네트워크 throughput), (3) 배포 컴포넌트 3종의 이미지·ready·롤아웃 판정을 본다. Prometheus/Grafana 없이 — 데이터 출처는 DB(`agent_reports` 이력, `data_jobs`)와 이미 배선된 k8s `observe()`뿐이다.

**Architecture:** `agent_reports.report`(JSON blob)는 신규 `MetricsRepository`가 기간 조회 후 **앱측 파싱**으로 시계열화하고(dual-dialect라 `json_extract` 금지), `data_jobs`의 typed 컬럼은 **SQL GROUP BY**로 집계한다(설계 §2.2). 누적 네트워크 카운터의 차분과 포인트 조립은 `metrics_series.py` **순수 함수**로 분리해 백엔드가 throughput을 완성해 준다 — 프론트는 카운터 의미를 모른다. metrics API 4종은 admin 게이트 뒤 읽기 전용(뮤테이션 없음 → 감사 로그 없음)이며 전부 fail-soft. 차트는 손수 SVG `Sparkline`/`BarChart` 순수 표현 컴포넌트(설계 §2.1). 인프라 뷰는 슬라이스 13의 `rollout_runner.observe()`와 `assess_*` 판정을 **통과만** 시킨다(설계 §2.4).

**Tech Stack:** Python 3.11 / FastAPI / SQLite+PostgreSQL 양립 SQL, React 18 + Vite + TS + Tailwind + TanStack Query v5 + Vitest/Testing Library/MSW 2. 차트 라이브러리 없음(손수 SVG).

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-09-dms-monitoring-dashboard-slice14-design.md`. 충돌하면 설계가 이긴다.
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- **새 의존성 금지.** 차트는 손수 SVG(`Sparkline`/`BarChart`)다. `npm install`·`pip install` 어느 쪽도 하지 않는다.
- SQL은 `:named` 파라미터만, SQLite/PostgreSQL 양립. **JSON blob의 수치 집계는 앱측 파싱** — `json_extract`/JSON 함수에 기대면 이식성이 깨진다(설계 §2.2). SQL GROUP BY는 typed 컬럼에만. 시각 버킷은 `SUBSTR(created_at, 1, :c)` — ISO-8601 UTC 고정 포맷(`utc_now_iso`)이라 접두 절단이 곧 시간 절단이고 두 방언 모두에서 동작한다.
- 기존 테이블 컬럼 추가는 `CREATE TABLE` 텍스트와 `_ensure_columns` **양쪽**. 신규 `files_count`/`bytes_count`는 typed INTEGER이므로 `_JSON_COLUMNS`에 넣지 **않는다**.
- 노드 메트릭은 **fail-soft** — 리포트 한 건의 JSON 파싱 실패·필드 누락이 시리즈 전체를 죽이면 안 된다(그 샘플만 버린다). `observe()` 실패는 그 컴포넌트만 null 강등(슬라이스 13 규약).
- 네트워크는 부팅 이후 **누적 카운터** — throughput은 **백엔드에서 인접 샘플 차분**으로 계산한다. 카운터가 감소하면(리부팅 리셋) 그 구간은 `null` — 음수 대역폭을 그리지 않는다.
- 기간은 시간 단위(`window=<h>`), **보존 상한(`agent_report_retention_days`=30일 → 720h)으로 클램프**한다. 초과 요청은 422가 아니라 720으로 접는다(설계 §6-2).
- 인프라 뷰는 슬라이스 11~13 엔드포인트(빌드/릴리스/정책)를 **재사용**하고 재구현하지 않는다. 새로 노출하는 것은 `observe()`가 이미 읽는 replica/ready 카운트와 `assess_*` 판정의 **통과**뿐(`GET /api/admin/metrics/infra`). RBAC 변경 없음.
- files/bytes는 **컬럼과 파이프라인만** 놓는다. runner의 mpifileutils 출력 파싱은 **범위 밖**(설계 §5) — 현재 데이터는 대부분 NULL이고, 대시보드는 NULL을 "—"로 우아하게 생략한다.
- **큐 대기의 전역 집계 금지** — `state_transitions` 인덱스가 `(entity_kind, entity_id, id)`라 전기간 집계는 풀스캔이다. 큐 대기는 요청 상세에서만 유도한다(설계 §2.3, Task 8).
- **이 슬라이스는 새 백엔드 사유 코드 리터럴을 0건 추가한다** (`request_not_found` 재사용). 만약 구현 중 새 리터럴이 필요해지면 `frontend/src/lib/reasonCodes.json`과 `REASON_MESSAGES` **양쪽**을 같은 태스크에서 고쳐라 — `tests/test_reason_codes_coverage.py`가 `src/dms/` AST 추출로 대조한다.
- admin 전용: 백엔드 라우터는 `APIRouter(dependencies=[Depends(require_admin)])`(routes_nodes/routes_builds 패턴), 화면은 기존 `/admin/dashboard` 라우트(`RequireRole role="admin"`) 확장 — 새 라우트를 만들지 않는다.
- 프론트는 백엔드 응답을 렌더 전에 방어적으로 정규화한다(`Array.isArray`). UI 문자열은 한국어, 로딩 `불러오는 중…`, null은 `—`. 사유 코드는 `reasonText()` 경유.
- 새 쿼리 키는 `["metrics", ...]`로 묶는다 — 기존 `["nodes"]`(dashboard/useDashboard.ts와 nodes/useNodes.ts가 공유)와 절대 겹치지 않게. 폴링은 개요·인프라만 5s, 시계열은 기간 재조회 위주(설계 §4).
- 순수 표현 컴포넌트(`Sparkline`/`BarChart`)는 **값→path/rect 단언**으로 테스트한다.
- 프론트 테스트는 파일마다 자체 MSW `setupServer` + `listen`/`resetHandlers`/`close`, 핸들러 경로는 상대경로.
- 백엔드 테스트: `.venv/bin/python -m pytest` (`python`은 PATH에 없다). 전체 스위트는 약 4.5분 — **포그라운드**로 Bash `timeout` 400000ms. 백그라운드+Monitor 조합 금지.
- 프론트: `cd frontend && npx vitest run`, 타입체크 `npx tsc -b`.
- **origin으로 push 금지.** 커밋만 한다.
- 주석은 한국어로 "왜"를 적는다.

## 실측 고정값 (코드에서 직접 확인)

| 항목 | 값 |
|---|---|
| 리포트 구조 | `build_report()`(agent/runner.py)가 `{"node_name", "probed_at", "mounts", "tools", "identities", "os"}` — OS 메트릭은 **`os` 키 아래** |
| `probe_os_metrics` 반환 | `load1/load5/load15`, `memory_total_kb/memory_available_kb`, `disks[].{storage_name,total_bytes,used_bytes}`, `network_rx_bytes/network_tx_bytes` — **전부 nullable**(probes.py:92) |
| `agent_reports` | `id {auto_pk}, node_name, report, reported_at` + 인덱스 `(node_name, reported_at)`, `(reported_at)` |
| 기존 이력 조회 | `AgentsRepository.node_reports(node_name, *, limit=200)` — `ORDER BY id DESC`, **기간 필터 없음**(agents.py:45). 시계열 조회는 신규로 만든다 |
| `AgentsRepository.list_nodes` | `agent_nodes`에서 `{"node_name","reported_at","fresh","report"}` — freshness는 `stale_seconds` 기준 읽기 시점 계산 |
| `data_jobs` 컬럼 | job_id, request_id, operation, tool, storage_name, source_storage, destination_storage, source, destination, target, options, priority, state, reason_code, preview_*, volcano_job_ref, artifact_uri, result_summary, worker_pool, precondition, confirmed_fingerprint, phase_refs, created_at, updated_at. 인덱스는 `(state, updated_at)`뿐 — `created_at` 인덱스 없음(현 규모 수십 행, 허용) |
| `_JSON_COLUMNS` | `("options","worker_pool","precondition","result_summary","volcano_job_ref","phase_refs")` — hydrate 시 `load_json` |
| `set_artifact` | `set_artifact(self, job_id, *, artifact_uri, result_summary)` — 유일 호출자는 `stepper._poll_execution`(성공 경로), summary 실측 `{"returncode": 0}` 또는 `{"summary_unavailable": True}` |
| tool 값 | `dscan / dsync / nsync / drm`(domain.ToolName) |
| 종단 잡 상태 | `Succeeded, Failed, TimedOut, Cancelled, Rejected, PreviewExpired`(TERMINAL_DATA_JOB_STATES) |
| requester | `requests.requester_id` 컬럼 — `data_jobs`에는 없어 `JOIN requests ON request_id`로 얻는다 |
| 시각 포맷 | `utc_now_iso()` = `"%Y-%m-%dT%H:%M:%SZ"` 고정, `iso_plus(ts, seconds)` 헬퍼, `load_json`은 falsy 입력에 None·잘못된 JSON에 `json.JSONDecodeError`(ValueError 서브클래스) |
| 보존 설정 | `agent_report_retention_days=30`(config.py:102), `agent_report_stale_seconds`(routes_nodes가 사용) |
| rollout | `ROLLOUT_ORDER=("dms-agent","dms-api","dms-controller")`, `COMPONENTS[c]={"kind","workload","container","repository","selector"}`(repositories/releases.py). `RolloutRunner.observe(*, kind, name)` → **정규화 dict**(`normalize_*` 키) 또는 404면 None, 실패 시 `ExecutionError("observe_failed")`. `app.state.rollout_runner` 배선 완료(app.py:37, api는 observe만 사용) |
| 판정 함수 | `assess_deployment(norm, *, since=None)` / `assess_daemonset(norm)` → `("applied"|"progressing"|"failed", detail)`(rollout_status.py). Deployment는 `replicas/ready_replicas`, DaemonSet은 `desired_number_scheduled/number_ready` 키 |
| events | `ObservabilityRepository.events_for_request(request_id, limit=100)` — 최신 limit건을 시간 **오름차순**으로, payload는 dict 복원. `GET /api/user/requests/{id}` 상세에는 이미 실려 있으나 admin 단독 경로는 없다 |
| admin 라우터 규약 | `APIRouter(dependencies=[Depends(require_admin)])` + `request.app.state.repos/settings`(routes_nodes/routes_builds) |
| api 테스트 픽스처 | conftest `client(db, settings)` — client와 `db` 픽스처가 같은 DB를 공유. admin 헤더 `{"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}`(test_api_nodes.py) |
| repo 테스트 픽스처 | conftest `db`; `Repositories(db)`를 테스트 파일에서 직접 생성(test_repo_data_jobs.py의 `_repos(db)` 패턴) |
| 프론트 현황 | `features/dashboard/`는 요청 목록 즉석 계산 스텁(Dashboard.tsx + useDashboard.ts의 `["nodes"]` 키 + Dashboard.test.tsx 1건). `components/ui/`에 Button/Card/Dialog/MetricTile/StatusPill/Table — 차트 프리미티브 없음 |
| 프론트 규약 | `reasonText()`(lib/api.ts), `asArray` 방어 패턴(NodesList.tsx), `StatusPill({state, variant?})`, `MetricTile({label, value})`, jobState.ts의 `TERMINAL_STATES`에는 **TimedOut이 없다**(요청 화면용 옛 집합 — 잡 통계에 재사용 금지) |
| RequestDetail | `durationText(from?, to?)` 헬퍼 보유, 요약 dl에 요청자/수행시간 행 — 큐 대기 행을 여기 더한다(Task 8) |

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/migrations.py` (수정) | `data_jobs`에 `files_count`/`bytes_count` INTEGER (CREATE TABLE 텍스트 + `_ensure_columns` 양쪽) |
| `src/dms/repositories/data_jobs.py` (수정) | `set_artifact`가 summary의 `"files"`/`"bytes"`를 typed 컬럼으로 승격 |
| `src/dms/repositories/metrics.py` (신규) | `MetricsRepository` — `node_series`(기간 조회+앱측 JSON 파싱, fail-soft) + `job_stats`(SQL GROUP BY 일습) |
| `src/dms/repositories/__init__.py` (수정) | `self.metrics` 등록 |
| `src/dms/metrics_series.py` (신규) | 클램프·버킷·네트워크 차분·포인트 조립·수행시간 히스토그램 **순수 함수** |
| `src/dms/api/routes_metrics.py` (신규) | admin 읽기 전용 4종: `metrics/nodes`, `metrics/jobs`, `metrics/infra`, `requests/{id}/events` |
| `src/dms/api/app.py` (수정) | metrics 라우터 등록 |
| `tests/test_repo_metrics.py`, `tests/test_metrics_series.py`, `tests/test_api_metrics.py` (신규), `tests/test_repo_data_jobs.py` (수정) | 백엔드 테스트 |
| `frontend/src/components/ui/Sparkline.tsx`/`BarChart.tsx` (+각 test) (신규) | 손수 SVG 순수 표현 컴포넌트 |
| `frontend/src/lib/types.ts` (수정) | `NodeMetrics`/`JobMetrics`/`InfraMetrics` 계열 타입 |
| `frontend/src/features/dashboard/useMetrics.ts` (신규) | 훅 3종, 쿼리 키 `["metrics", ...]` |
| `frontend/src/features/dashboard/Dashboard.tsx` (수정) | 개요 KPI를 잡 통계 집계로 전환 + 인프라 카드 + 섹션 조립 |
| `frontend/src/features/dashboard/WindowSelect.tsx` (신규) | 1h/6h/24h/7d 기간 선택 |
| `frontend/src/features/dashboard/NodeMetricsSection.tsx`/`JobStatsSection.tsx` (+각 test) (신규) | 노드 시계열·잡 통계 섹션 |
| `frontend/src/features/dashboard/Dashboard.test.tsx` (수정) | KPI 전환 검증으로 갱신 |
| `frontend/src/features/jobs/RequestDetail.tsx` (+test) (수정) | 큐 대기 유도 표시(설계 §2.3) |

---

### Task 1: files/bytes 컬럼·파이프라인 + MetricsRepository

**Files:**
- Modify: `src/dms/migrations.py`
- Modify: `src/dms/repositories/data_jobs.py`
- Create: `src/dms/repositories/metrics.py`
- Modify: `src/dms/repositories/__init__.py`
- Create: `tests/test_repo_metrics.py`
- Test: `tests/test_repo_data_jobs.py` (기존, 확장)

**Interfaces:**
- Consumes: `Database`(`query`/`query_one`/`execute`), `load_json` from `..db`; `DataJobState`, `TERMINAL_DATA_JOB_STATES` from `..domain`.
- Produces (Task 3이 이 이름을 그대로 쓴다):
  - `data_jobs.files_count INTEGER`, `data_jobs.bytes_count INTEGER` — nullable, `_JSON_COLUMNS` 비포함(typed라 hydrate 불필요)
  - `DataJobsRepository.set_artifact(job_id, *, artifact_uri, result_summary)` — 시그니처 불변. `result_summary`가 dict이고 `"files"`/`"bytes"` 키가 **음이 아닌 int**(bool 제외)면 해당 컬럼에 채우고, 아니면 NULL
  - `MetricsRepository.node_series(node_name, *, start: str, end: str) -> list[dict]` — `[{"reported_at": str, "report": dict}]` 시간 오름차순. JSON 파싱 실패 행은 그 행만 건너뜀(fail-soft)
  - `MetricsRepository.job_stats(*, start: str, end: str, bucket_chars: int = 13) -> dict` — 키:
    - `"by_state"`: `[{"state", "count"}]` state 오름차순
    - `"by_tool"`: `[{"tool": str|None, "count", "succeeded", "failed"}]` count 내림차순
    - `"by_storage"`: `[{"storage": str|None, ...}]` (storage = `COALESCE(storage_name, destination_storage)`)
    - `"by_requester"`: `[{"requester_id": str, ...}]` (`requests` 조인)
    - `"failure_reasons"`: `[{"reason_code", "count"}]` 상위 10
    - `"throughput"`: `[{"bucket": str, "count"}]` bucket 오름차순
    - `"duration_seconds"`: `list[float]` (종단 잡의 `updated_at - created_at`)
    - `"files_total": int|None`, `"bytes_total": int|None` (Succeeded 잡의 SUM)
  - `Repositories.metrics` 등록

- [ ] **Step 1: 실패하는 테스트를 쓴다 — MetricsRepository**

`tests/test_repo_metrics.py`:

```python
import pytest
from dms.repositories import Repositories


@pytest.fixture
def repos(db):
    return Repositories(db)


def _seed_job(db, repos, *, created_at, state="Succeeded", tool="dscan",
              storage="s1", dest_storage=None, requester="alice",
              reason_code=None, updated_at=None, files=None, nbytes=None):
    """data_jobs 한 행을 원하는 상태·시각으로 심는다. set_job_state는 updated_at을
    현재 시각으로 찍으므로 창(window) 테스트가 불가능하다 -- 정상 경로로 만들고
    시각·상태만 UPDATE로 덮는다."""
    rid = repos.requests.create(
        operation="scan", requester_id=requester, actor=requester,
        resource_key=f"k:{created_at}:{tool}:{state}:{requester}", payload={},
        priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name=storage,
        destination_storage=dest_storage, target="a", options={}, tool=tool,
        worker_pool={}, precondition={}, actor="planner")
    db.execute(
        """UPDATE data_jobs SET state = :st, reason_code = :rc, created_at = :c,
               updated_at = :u, files_count = :f, bytes_count = :b
           WHERE job_id = :j""",
        {"st": state, "rc": reason_code, "c": created_at,
         "u": updated_at or created_at, "f": files, "b": nbytes, "j": job_id})
    return job_id


# ---- node_series ----

def test_node_series_is_windowed_inclusive_and_ascending(db, repos):
    for i, at in enumerate(["2026-08-09T00:00:00Z", "2026-08-09T01:00:00Z",
                            "2026-08-09T02:00:00Z"]):
        repos.agents.ingest("n1", {"seq": i, "os": {}}, reported_at=at)
    rows = repos.metrics.node_series(
        "n1", start="2026-08-09T00:30:00Z", end="2026-08-09T02:00:00Z")
    # BETWEEN이라 끝 경계 포함, 시간 오름차순(id tiebreak)
    assert [r["reported_at"] for r in rows] == [
        "2026-08-09T01:00:00Z", "2026-08-09T02:00:00Z"]
    assert rows[0]["report"]["seq"] == 1


def test_node_series_is_scoped_to_the_node(db, repos):
    repos.agents.ingest("n1", {"seq": 1}, reported_at="2026-08-09T00:00:00Z")
    repos.agents.ingest("n2", {"seq": 2}, reported_at="2026-08-09T00:00:00Z")
    rows = repos.metrics.node_series(
        "n1", start="2026-08-09T00:00:00Z", end="2026-08-09T01:00:00Z")
    assert [r["report"]["seq"] for r in rows] == [1]


def test_node_series_skips_only_the_corrupt_row(db, repos):
    # 설계 §3 fail-soft: 손상 리포트 하나가 시리즈 전체를 죽이면 안 된다.
    # 저장 경로는 dump_json을 거치므로 손상은 DB 직접 조작으로만 재현된다.
    repos.agents.ingest("n1", {"seq": 0}, reported_at="2026-08-09T00:00:00Z")
    repos.agents.ingest("n1", {"seq": 1}, reported_at="2026-08-09T01:00:00Z")
    db.execute("UPDATE agent_reports SET report = '{broken' WHERE reported_at = :at",
               {"at": "2026-08-09T00:00:00Z"})
    rows = repos.metrics.node_series(
        "n1", start="2026-08-09T00:00:00Z", end="2026-08-09T02:00:00Z")
    assert [r["report"]["seq"] for r in rows] == [1]


# ---- job_stats ----

def test_job_stats_by_state_tool_and_failure_reasons(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z")
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", state="Failed",
              tool="dsync", reason_code="execution_failed")
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z", state="Failed",
              tool="dsync", reason_code="execution_failed")
    _seed_job(db, repos, created_at="2026-07-01T00:00:00Z", tool="drm")  # 창 밖
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["by_state"] == [{"state": "Failed", "count": 2},
                                 {"state": "Succeeded", "count": 1}]
    assert stats["by_tool"] == [
        {"tool": "dsync", "count": 2, "succeeded": 0, "failed": 2},
        {"tool": "dscan", "count": 1, "succeeded": 1, "failed": 0}]
    assert stats["failure_reasons"] == [
        {"reason_code": "execution_failed", "count": 2}]


def test_job_stats_storage_falls_back_to_destination(db, repos):
    # sync 잡은 storage_name이 NULL이고 도착지가 destination_storage에 있다
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", storage=None,
              dest_storage="s2", tool="nsync")
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["by_storage"] == [
        {"storage": "s2", "count": 1, "succeeded": 1, "failed": 0}]


def test_job_stats_requester_comes_from_requests_join(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", requester="alice")
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", requester="bob",
              state="Failed", reason_code="execution_failed")
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["by_requester"] == [
        {"requester_id": "alice", "count": 1, "succeeded": 1, "failed": 0},
        {"requester_id": "bob", "count": 1, "succeeded": 0, "failed": 1}]


def test_job_stats_throughput_buckets_by_iso_prefix(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:10:00Z")
    _seed_job(db, repos, created_at="2026-08-09T01:50:00Z")
    _seed_job(db, repos, created_at="2026-08-09T02:05:00Z")
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z", bucket_chars=13)
    assert stats["throughput"] == [{"bucket": "2026-08-09T01", "count": 2},
                                   {"bucket": "2026-08-09T02", "count": 1}]
    daily = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z", bucket_chars=10)
    assert daily["throughput"] == [{"bucket": "2026-08-09", "count": 3}]


def test_job_stats_duration_only_from_terminal_jobs(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z",
              updated_at="2026-08-09T01:00:30Z")
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", state="Pending")
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["duration_seconds"] == [30.0]   # 비종단(Pending)은 진행 중 -- 제외


def test_job_stats_files_bytes_sum_only_succeeded_and_null_safe(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", files=10, nbytes=100)
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z")            # NULL
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z", state="Failed",
              reason_code="execution_failed", files=5, nbytes=50)      # 실패분 제외
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert (stats["files_total"], stats["bytes_total"]) == (10, 100)


def test_job_stats_files_bytes_all_null_is_none(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z")
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["files_total"] is None and stats["bytes_total"] is None
```

`tests/test_repo_data_jobs.py` 끝에 추가 (기존 `_repos`/`_mk_request` 헬퍼 사용):

```python
def _mk_job(repos, rid):
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    return repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool="dscan", worker_pool={}, precondition={},
        actor="planner")


def test_set_artifact_promotes_files_bytes_to_columns(db):
    # 설계 §2.3: runner가 언젠가 summary에 files/bytes를 쓰면 마이그레이션 없이
    # typed 컬럼으로 흘러들어 대시보드 SQL 집계에 잡힌다 -- 그 파이프라인의 계약.
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    repos.data_jobs.set_artifact(
        job_id, artifact_uri="file:///a",
        result_summary={"returncode": 0, "files": 12, "bytes": 3456})
    job = repos.data_jobs.get_job(job_id)
    assert (job["files_count"], job["bytes_count"]) == (12, 3456)
    assert job["result_summary"] == {"returncode": 0, "files": 12, "bytes": 3456}


def test_set_artifact_without_counts_leaves_null(db):
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    repos.data_jobs.set_artifact(job_id, artifact_uri=None,
                                 result_summary={"returncode": 0})
    job = repos.data_jobs.get_job(job_id)
    assert (job["files_count"], job["bytes_count"]) == (None, None)


def test_set_artifact_rejects_non_int_counts(db):
    # summary는 신뢰 경계 밖(runner 산출물) -- 문자열·bool·음수가 컬럼을 오염시키면 안 된다
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    repos.data_jobs.set_artifact(
        job_id, artifact_uri=None,
        result_summary={"files": "12", "bytes": True})
    job = repos.data_jobs.get_job(job_id)
    assert (job["files_count"], job["bytes_count"]) == (None, None)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_repo_metrics.py tests/test_repo_data_jobs.py -q`
Expected: FAIL — `AttributeError: ... no attribute 'metrics'`, set_artifact 테스트는 `files_count` 부재/`None != 12`

- [ ] **Step 3: 마이그레이션에 컬럼을 더한다**

`src/dms/migrations.py`의 `data_jobs` CREATE TABLE 텍스트에서 `result_summary TEXT,` 다음에:

```python
            result_summary TEXT,
            -- files/bytes 파이프라인(슬라이스 14 설계 §2.3): set_artifact가
            -- result_summary의 "files"/"bytes"를 typed 컬럼으로 승격한다. runner가
            -- 아직 그 키를 안 써서 당분간 대부분 NULL -- 대시보드는 "—"로 생략한다.
            files_count INTEGER,
            bytes_count INTEGER,
```

`_ensure_columns`의 튜플에 두 줄:

```python
        ("data_jobs", "files_count", "INTEGER"),
        ("data_jobs", "bytes_count", "INTEGER"),
```

- [ ] **Step 4: set_artifact를 확장한다**

`src/dms/repositories/data_jobs.py` — 모듈 레벨 헬퍼(클래스 위)와 `set_artifact` 교체:

```python
def _as_count(value) -> "int | None":
    # result_summary는 신뢰 경계 밖(runner 산출물)이다. bool은 int의 서브클래스라
    # 명시적으로 제외하고, 음수는 계수로서 의미가 없어 버린다 -- 잘못된 값이
    # typed 컬럼으로 새면 SUM 집계 전체가 오염된다.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
```

```python
    def set_artifact(self, job_id, *, artifact_uri, result_summary):
        # files/bytes 승격(설계 §2.3): summary에 키가 있으면 typed 컬럼에 채운다.
        # 지금 runner는 이 키를 안 쓰므로 대부분 NULL -- runner의 mpifileutils
        # 출력 파싱은 이 슬라이스 범위 밖(별도 작업)이다.
        files_count = bytes_count = None
        if isinstance(result_summary, dict):
            files_count = _as_count(result_summary.get("files"))
            bytes_count = _as_count(result_summary.get("bytes"))
        self._db.execute(
            """UPDATE data_jobs SET artifact_uri = COALESCE(:a, artifact_uri),
                   result_summary = :s, files_count = :fc, bytes_count = :bc,
                   updated_at = :now WHERE job_id = :j""",
            {"a": artifact_uri,
             "s": dump_json(result_summary) if result_summary is not None else None,
             "fc": files_count, "bc": bytes_count,
             "now": utc_now_iso(), "j": job_id})
```

- [ ] **Step 5: MetricsRepository를 만든다**

`src/dms/repositories/metrics.py`:

```python
"""대시보드 집계 저장소(읽기 전용). 두 데이터원을 다룬다(설계 §2.2):
agent_reports(JSON blob 시계열 -- 앱측 파싱)와 data_jobs(typed 컬럼 -- SQL GROUP BY).
blob은 dual-dialect(SQLite/PostgreSQL)라 json_extract에 기댈 수 없어 여기서 파싱하고,
GROUP BY는 typed 컬럼에만 건다."""
from datetime import datetime, timezone

from ..db import Database, load_json
from ..domain import DataJobState, TERMINAL_DATA_JOB_STATES

# 실패로 세는 종단 상태 = 종단 전체 - Succeeded. sorted로 고정해 플레이스홀더
# 순서(파라미터 이름)가 실행마다 흔들리지 않게 한다.
_FAILED_STATES = tuple(sorted(
    s.value for s in TERMINAL_DATA_JOB_STATES if s is not DataJobState.SUCCEEDED))
_TERMINAL_STATES = tuple(sorted(s.value for s in TERMINAL_DATA_JOB_STATES))


def _epoch(ts: str) -> float:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).timestamp()


class MetricsRepository:
    def __init__(self, db: Database):
        self._db = db

    def node_series(self, node_name: str, *, start: str, end: str) -> list[dict]:
        """[{"reported_at", "report"}] 시간 오름차순. idx_agent_reports_node
        (node_name, reported_at)가 커버한다. 같은 reported_at이 두 행일 수 있어
        id를 tiebreak으로 둔다(정렬 결정성). JSON이 깨진 행은 그 행만 버린다
        (fail-soft, 설계 §3) -- 한 행이 시리즈 전체를 죽이면 안 된다."""
        rows = self._db.query(
            """SELECT report, reported_at FROM agent_reports
               WHERE node_name = :n AND reported_at BETWEEN :s AND :e
               ORDER BY reported_at ASC, id ASC""",
            {"n": node_name, "s": start, "e": end})
        out = []
        for row in rows:
            try:
                report = load_json(row["report"])
            except ValueError:
                continue
            out.append({"reported_at": row["reported_at"], "report": report})
        return out

    def job_stats(self, *, start: str, end: str, bucket_chars: int = 13) -> dict:
        params = {"s": start, "e": end}
        fail_ph = ", ".join(f":f{i}" for i in range(len(_FAILED_STATES)))
        fail_params = {f"f{i}": v for i, v in enumerate(_FAILED_STATES)}

        def agg(prefix=""):
            # SUM(CASE ...)는 두 방언 공통 -- FILTER (WHERE ...)는 PG 전용이라 안 쓴다.
            return (f"COUNT(*) AS cnt, "
                    f"SUM(CASE WHEN {prefix}state = 'Succeeded' THEN 1 ELSE 0 END) AS ok, "
                    f"SUM(CASE WHEN {prefix}state IN ({fail_ph}) THEN 1 ELSE 0 END) AS bad")

        by_state = self._db.query(
            """SELECT state, COUNT(*) AS cnt FROM data_jobs
               WHERE created_at BETWEEN :s AND :e
               GROUP BY state ORDER BY state""", params)

        by_tool = self._db.query(
            f"""SELECT COALESCE(tool, '') AS k, {agg()} FROM data_jobs
                WHERE created_at BETWEEN :s AND :e
                GROUP BY COALESCE(tool, '') ORDER BY cnt DESC, k ASC""",
            {**params, **fail_params})

        # sync 잡은 storage_name이 NULL이고 도착지가 destination_storage에 있다 --
        # "그 스토리지에서 일어난 일"의 대표로 도착지를 쓴다. NULL 정렬은 방언마다
        # 달라(SQLite ASC는 NULL 먼저, PG는 나중) ''로 접고 앱측에서 None으로 되돌린다.
        by_storage = self._db.query(
            f"""SELECT COALESCE(storage_name, destination_storage, '') AS k, {agg()}
                FROM data_jobs WHERE created_at BETWEEN :s AND :e
                GROUP BY COALESCE(storage_name, destination_storage, '')
                ORDER BY cnt DESC, k ASC""",
            {**params, **fail_params})

        by_requester = self._db.query(
            f"""SELECT r.requester_id AS k, {agg('d.')}
                FROM data_jobs d JOIN requests r ON r.request_id = d.request_id
                WHERE d.created_at BETWEEN :s AND :e
                GROUP BY r.requester_id ORDER BY cnt DESC, k ASC""",
            {**params, **fail_params})

        failure_reasons = self._db.query(
            f"""SELECT reason_code, COUNT(*) AS cnt FROM data_jobs
                WHERE created_at BETWEEN :s AND :e AND reason_code IS NOT NULL
                  AND state IN ({fail_ph})
                GROUP BY reason_code ORDER BY cnt DESC, reason_code ASC LIMIT 10""",
            {**params, **fail_params})

        # ISO-8601 UTC 고정 포맷이라 SUBSTR 접두가 곧 시간 버킷이다(13자=시간, 10자=일)
        throughput = self._db.query(
            """SELECT SUBSTR(created_at, 1, :c) AS bucket, COUNT(*) AS cnt
               FROM data_jobs WHERE created_at BETWEEN :s AND :e
               GROUP BY SUBSTR(created_at, 1, :c) ORDER BY bucket ASC""",
            {**params, "c": bucket_chars})

        # 수행시간은 문자열 시각의 차라 SQL로는 이식성 있게 못 뺀다(julianday는
        # SQLite 전용, EXTRACT(EPOCH)는 PG 전용) -- 행을 가져와 앱측에서 계산한다.
        # 종단 잡만: 비종단의 updated_at은 "지금까지"일 뿐 수행시간이 아니다.
        term_ph = ", ".join(f":t{i}" for i in range(len(_TERMINAL_STATES)))
        term_params = {f"t{i}": v for i, v in enumerate(_TERMINAL_STATES)}
        rows = self._db.query(
            f"""SELECT created_at, updated_at FROM data_jobs
                WHERE created_at BETWEEN :s AND :e AND state IN ({term_ph})
                ORDER BY created_at ASC, job_id ASC""",
            {**params, **term_params})
        duration_seconds = []
        for row in rows:
            try:
                delta = _epoch(row["updated_at"]) - _epoch(row["created_at"])
            except (TypeError, ValueError):
                continue                     # 시각이 깨진 행은 그 행만 버린다
            if delta >= 0:
                duration_seconds.append(delta)

        sums = self._db.query_one(
            """SELECT SUM(files_count) AS files_total, SUM(bytes_count) AS bytes_total
               FROM data_jobs
               WHERE created_at BETWEEN :s AND :e AND state = 'Succeeded'""", params)

        def fold(rows_, key_name):
            # COALESCE로 접은 ''를 None으로 되돌리고 내부 별칭을 응답 이름으로 바꾼다
            return [{key_name: (r["k"] or None), "count": r["cnt"],
                     "succeeded": r["ok"], "failed": r["bad"]} for r in rows_]

        return {
            "by_state": [{"state": r["state"], "count": r["cnt"]} for r in by_state],
            "by_tool": fold(by_tool, "tool"),
            "by_storage": fold(by_storage, "storage"),
            "by_requester": [{"requester_id": r["k"], "count": r["cnt"],
                              "succeeded": r["ok"], "failed": r["bad"]}
                             for r in by_requester],
            "failure_reasons": [{"reason_code": r["reason_code"], "count": r["cnt"]}
                                for r in failure_reasons],
            "throughput": [{"bucket": r["bucket"], "count": r["cnt"]}
                           for r in throughput],
            "duration_seconds": duration_seconds,
            "files_total": sums["files_total"],
            "bytes_total": sums["bytes_total"],
        }
```

`src/dms/repositories/__init__.py`에 `from .metrics import MetricsRepository`와 `self.metrics = MetricsRepository(db)`를 더한다.

- [ ] **Step 6: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_repo_metrics.py tests/test_repo_data_jobs.py tests/test_migrations.py -q`
Expected: PASS (신규 12 tests + 기존 전부)

- [ ] **Step 7: 전체 스위트로 회귀를 확인한다**

Run: `.venv/bin/python -m pytest -q` (포그라운드, timeout 400000ms)
Expected: 전부 PASS — 특히 stepper 계열(`set_artifact` 시그니처 불변 확인)

- [ ] **Step 8: 커밋**

```bash
git add src/dms/migrations.py src/dms/repositories tests/test_repo_metrics.py tests/test_repo_data_jobs.py
git commit -m "feat(metrics): files/bytes 컬럼·파이프라인과 MetricsRepository"
```

---

### Task 2: 시계열 조립·네트워크 차분 순수 함수

**Files:**
- Create: `src/dms/metrics_series.py`
- Create: `tests/test_metrics_series.py`

**Interfaces:**
- Consumes: 표준 라이브러리만 — DB/HTTP 접근 금지(rollout_status.py와 같은 원칙).
- Produces (Task 3이 그대로 쓴다):
  - `clamp_window_hours(window: int | None, *, retention_days: int) -> int` — `[1, retention_days*24]`로 클램프, None이면 24
  - `bucket_chars_for(window_hours: int) -> int` — 48h 이하 13(시간), 그 위 10(일)
  - `build_node_points(samples: list[dict]) -> list[dict]` — 입력은 `MetricsRepository.node_series` 반환(오름차순). 출력 포인트 키: `at, load1, load5, load15, mem_used_pct, net_rx_bps, net_tx_bps, disks[{storage_name, used_pct}]` (수치 전부 `float|None`)
  - `duration_histogram(seconds: list) -> list[dict]` — 고정 6버킷 `[{"bucket": "<1m"|"1-10m"|"10-60m"|"1-6h"|"6-24h"|">24h", "count": int}]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_metrics_series.py`:

```python
from dms.metrics_series import (bucket_chars_for, build_node_points,
                                clamp_window_hours, duration_histogram)


def _sample(at, **os_fields):
    return {"reported_at": at, "report": {"os": os_fields}}


def test_clamp_window_hours():
    assert clamp_window_hours(None, retention_days=30) == 24     # 기본 24h
    assert clamp_window_hours(24, retention_days=30) == 24
    assert clamp_window_hours(1000, retention_days=30) == 720    # 보존 상한(설계 §6-2)
    assert clamp_window_hours(0, retention_days=30) == 1         # 하한 1h


def test_bucket_chars_for():
    assert bucket_chars_for(1) == 13      # "YYYY-MM-DDTHH" -- 시간 버킷
    assert bucket_chars_for(48) == 13
    assert bucket_chars_for(49) == 10     # "YYYY-MM-DD" -- 7일 창을 시간으로 쪼개면
    assert bucket_chars_for(168) == 10    # 막대 168개가 나와 읽히지 않는다


def test_points_carry_load_mem_and_disks():
    pts = build_node_points([_sample(
        "2026-08-09T00:00:00Z", load1=0.5, load5=0.4, load15=0.3,
        memory_total_kb=100, memory_available_kb=25,
        disks=[{"storage_name": "s1", "total_bytes": 200, "used_bytes": 50}])])
    assert pts == [{
        "at": "2026-08-09T00:00:00Z", "load1": 0.5, "load5": 0.4, "load15": 0.3,
        "mem_used_pct": 75.0, "net_rx_bps": None, "net_tx_bps": None,
        "disks": [{"storage_name": "s1", "used_pct": 25.0}]}]


def test_network_throughput_is_adjacent_diff():
    pts = build_node_points([
        _sample("2026-08-09T00:00:00Z", network_rx_bytes=1000, network_tx_bytes=0),
        _sample("2026-08-09T00:01:00Z", network_rx_bytes=7000, network_tx_bytes=600),
    ])
    # 첫 포인트는 이전 샘플이 없어 null, 둘째는 (7000-1000)/60초 = 100 B/s
    assert [p["net_rx_bps"] for p in pts] == [None, 100.0]
    assert [p["net_tx_bps"] for p in pts] == [None, 10.0]


def test_counter_reset_yields_null_not_negative():
    pts = build_node_points([
        _sample("2026-08-09T00:00:00Z", network_rx_bytes=9000, network_tx_bytes=0),
        _sample("2026-08-09T00:01:00Z", network_rx_bytes=100, network_tx_bytes=0),
    ])
    # 감소 = 리부팅 카운터 리셋(설계 §3) -- 음수 대역폭을 그리느니 빈 구간이 정직하다
    assert pts[1]["net_rx_bps"] is None


def test_broken_sample_is_skipped_and_diff_spans_the_gap():
    pts = build_node_points([
        _sample("2026-08-09T00:00:00Z", network_rx_bytes=0),
        {"reported_at": "2026-08-09T00:01:00Z", "report": None},         # os 없음
        {"reported_at": "bad-timestamp", "report": {"os": {}}},          # 시각 불가
        _sample("2026-08-09T00:02:00Z", network_rx_bytes=1200),
    ])
    # 깨진 샘플 둘만 빠지고(설계 §3 fail-soft) 차분은 남은 두 샘플 간격(120초)으로
    assert [p["at"] for p in pts] == ["2026-08-09T00:00:00Z", "2026-08-09T00:02:00Z"]
    assert pts[1]["net_rx_bps"] == 10.0


def test_missing_fields_become_none_not_crash():
    pts = build_node_points([_sample("2026-08-09T00:00:00Z")])
    assert pts[0]["load1"] is None and pts[0]["mem_used_pct"] is None
    assert pts[0]["disks"] == []


def test_bool_and_string_values_are_not_numbers():
    # bool은 int의 서브클래스 -- True가 1.0으로 새면 안 된다
    pts = build_node_points([_sample(
        "2026-08-09T00:00:00Z", load1=True, memory_total_kb="100",
        disks=[{"storage_name": "s1", "total_bytes": 0, "used_bytes": 0},
               "not-a-dict"])])
    assert pts[0]["load1"] is None and pts[0]["mem_used_pct"] is None
    # total 0은 나눗셈 불가 -- used_pct만 null, 항목은 살린다
    assert pts[0]["disks"] == [{"storage_name": "s1", "used_pct": None}]


def test_duration_histogram_fixed_buckets():
    hist = duration_histogram([30, 3599, 100000, -5, None])
    assert hist == [
        {"bucket": "<1m", "count": 1}, {"bucket": "1-10m", "count": 0},
        {"bucket": "10-60m", "count": 1}, {"bucket": "1-6h", "count": 0},
        {"bucket": "6-24h", "count": 0}, {"bucket": ">24h", "count": 1}]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_metrics_series.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.metrics_series'`

- [ ] **Step 3: 구현한다**

`src/dms/metrics_series.py`:

```python
"""시계열 조립 순수 함수 -- DB/HTTP 접근 없음(rollout_status.py와 같은 원칙).

agent_reports의 report blob에서 노드 메트릭 포인트를 만든다. 계약(설계 §3):
- fail-soft: 샘플 하나가 깨져도(비 dict, os 없음, 시각 파싱 불가) 그 샘플만 버린다.
- 네트워크는 부팅 이후 누적 카운터다 -- throughput은 인접 샘플 차분으로 여기서
  계산해 프론트가 카운터 의미를 몰라도 되게 한다(설계 §3). 카운터가 감소하면
  (리부팅 리셋) 그 구간은 null -- 음수 대역폭을 그리는 것보다 빈 구간이 정직하다."""
from datetime import datetime, timezone


def clamp_window_hours(window, *, retention_days: int) -> int:
    """조회 기간(시간)을 [1, 보존 상한]으로 클램프. retention이 그보다 오래된
    agent_reports를 지우므로(기본 30일=720h) 상한 밖 요청은 거절하지 않고 접는다 --
    운영자가 '한 달 치'를 요청했을 때 422보다 720h 데이터가 낫다(설계 §6-2)."""
    hours = 24 if window is None else int(window)
    return max(1, min(hours, retention_days * 24))


def bucket_chars_for(window_hours: int) -> int:
    """처리량 버킷의 SUBSTR 길이. ISO-8601 UTC 고정 포맷(utc_now_iso)이라 접두
    절단이 곧 시간 절단이다: 13자="YYYY-MM-DDTHH"(시간), 10자="YYYY-MM-DD"(일).
    48h 이하만 시간 버킷 -- 7일 창을 시간으로 쪼개면 막대 168개가 나온다."""
    return 13 if window_hours <= 48 else 10


def _epoch(ts) -> float:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).timestamp()


def _num(value):
    # bool은 int의 서브클래스 -- True가 1.0으로 새면 안 된다
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _mem_used_pct(os_block):
    total = _num(os_block.get("memory_total_kb"))
    avail = _num(os_block.get("memory_available_kb"))
    if total is None or avail is None or total <= 0:
        return None
    return round((total - avail) / total * 100, 1)


def _disks(os_block):
    out = []
    for disk in os_block.get("disks") or []:
        if not isinstance(disk, dict) or not isinstance(disk.get("storage_name"), str):
            continue
        total = _num(disk.get("total_bytes"))
        used = _num(disk.get("used_bytes"))
        used_pct = (round(used / total * 100, 1)
                    if total is not None and used is not None and total > 0 else None)
        out.append({"storage_name": disk["storage_name"], "used_pct": used_pct})
    return out


def _rate(prev, cur, dt):
    # 감소 = 카운터 리셋(리부팅)으로 간주 -- 그 구간은 null(설계 §3)
    if prev is None or cur is None or dt <= 0 or cur < prev:
        return None
    return round((cur - prev) / dt, 1)


def build_node_points(samples: list[dict]) -> list[dict]:
    """MetricsRepository.node_series 출력(오름차순) -> 포인트 목록. 샘플 단위 fail-soft."""
    points = []
    prev_t = prev_rx = prev_tx = None
    for sample in samples:
        report = sample.get("report")
        os_block = report.get("os") if isinstance(report, dict) else None
        if not isinstance(os_block, dict):
            continue              # os 증거가 없는 리포트로는 포인트를 만들 수 없다
        try:
            t = _epoch(sample.get("reported_at"))
        except (TypeError, ValueError):
            continue              # 시각이 깨지면 차분의 축 자체가 없다
        rx = _num(os_block.get("network_rx_bytes"))
        tx = _num(os_block.get("network_tx_bytes"))
        net_rx = net_tx = None
        if prev_t is not None:
            dt = t - prev_t
            net_rx = _rate(prev_rx, rx, dt)
            net_tx = _rate(prev_tx, tx, dt)
        points.append({
            "at": sample["reported_at"],
            "load1": _num(os_block.get("load1")),
            "load5": _num(os_block.get("load5")),
            "load15": _num(os_block.get("load15")),
            "mem_used_pct": _mem_used_pct(os_block),
            "net_rx_bps": net_rx,
            "net_tx_bps": net_tx,
            "disks": _disks(os_block),
        })
        # 카운터가 None인 샘플을 지나면 prev도 None이 된다 -- 다음 구간도 null.
        # 마지막 유효 카운터를 기억하는 것보다 단순하고, 빈 구간 하나가 늘 뿐이다.
        prev_t, prev_rx, prev_tx = t, rx, tx
    return points


# (라벨, 상한초). 마지막 ">24h"는 상한 없음. 고정 순서로 내보내 빈 버킷도 0으로
# 남긴다 -- 프론트 막대 폭이 데이터에 따라 출렁이지 않게.
DURATION_BUCKETS = (("<1m", 60), ("1-10m", 600), ("10-60m", 3600),
                    ("1-6h", 21600), ("6-24h", 86400))


def duration_histogram(seconds: list) -> list[dict]:
    counts = [0] * (len(DURATION_BUCKETS) + 1)
    for value in seconds:
        v = _num(value)
        if v is None or v < 0:
            continue
        for i, (_, upper) in enumerate(DURATION_BUCKETS):
            if v < upper:
                counts[i] += 1
                break
        else:
            counts[-1] += 1
    labels = [label for label, _ in DURATION_BUCKETS] + [">24h"]
    return [{"bucket": label, "count": counts[i]} for i, label in enumerate(labels)]
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_metrics_series.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/dms/metrics_series.py tests/test_metrics_series.py
git commit -m "feat(metrics): 시계열 조립·네트워크 차분 순수 함수"
```

---

### Task 3: metrics API (nodes/jobs) + 요청 이벤트 래퍼

**Files:**
- Create: `src/dms/api/routes_metrics.py`
- Modify: `src/dms/api/app.py`
- Create: `tests/test_api_metrics.py`

**Interfaces:**
- Consumes: `repos.metrics.node_series`/`job_stats`, `repos.agents.list_nodes`, `repos.requests.get`, `repos.observability.events_for_request` (Task 1·기존); `clamp_window_hours`/`bucket_chars_for`/`build_node_points`/`duration_histogram` (Task 2); `require_admin`, `iso_plus`/`utc_now_iso`.
- Produces (Task 6·7 프론트가 이 JSON 모양을 그대로 쓴다):
  - `GET /api/admin/metrics/nodes?window=<h>` → `{"window_hours": int, "start": str, "end": str, "nodes": [{"node_name", "reported_at", "fresh", "points": [Task 2 포인트]}]}`
  - `GET /api/admin/metrics/jobs?window=<h>` → `{"window_hours": int, "bucket": "hour"|"day", "by_state", "by_tool", "by_storage", "by_requester", "failure_reasons", "throughput", "duration_histogram", "files_total", "bytes_total"}` (Task 1 키 + `duration_seconds`를 `duration_histogram`으로 변환)
  - `GET /api/admin/requests/{request_id}/events?limit=<n>` → `{"request_id": str, "events": [...]}`; 요청이 없으면 404 `request_not_found`(기존 코드 재사용 — 신규 리터럴 아님)
  - **새 사유 코드 리터럴 0건** — reasonCodes.json/REASON_MESSAGES 변경 없음

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_metrics.py`:

```python
from dms.db import iso_plus, utc_now_iso
from dms.repositories import Repositories

ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def _report(*, load1=0.5, mem_total=100, mem_avail=50, rx=0, tx=0):
    # build_report(agent/runner.py)와 같은 모양 -- os 키 아래에 probe_os_metrics 반환
    return {"mounts": [], "tools": [], "identities": [],
            "os": {"load1": load1, "load5": 0.4, "load15": 0.3,
                   "memory_total_kb": mem_total, "memory_available_kb": mem_avail,
                   "disks": [{"storage_name": "s1", "total_bytes": 100,
                              "used_bytes": 40}],
                   "network_rx_bytes": rx, "network_tx_bytes": tx}}


def _seed_job(db, repos, *, created_at, state="Succeeded", tool="dscan",
              reason_code=None):
    rid = repos.requests.create(
        operation="scan", requester_id="alice", actor="alice",
        resource_key=f"k:{created_at}:{state}", payload={}, priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool=tool, worker_pool={}, precondition={},
        actor="planner")
    db.execute(
        """UPDATE data_jobs SET state = :st, reason_code = :rc,
               created_at = :c, updated_at = :c WHERE job_id = :j""",
        {"st": state, "rc": reason_code, "c": created_at, "j": job_id})
    return rid


def test_metrics_require_admin(client):
    assert client.get("/api/admin/metrics/nodes").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/metrics/nodes").status_code == 403
    assert client.get("/api/admin/metrics/jobs").status_code == 403


def test_metrics_nodes_series_with_backend_computed_throughput(client, db):
    repos = Repositories(db)
    now = utc_now_iso()
    repos.agents.ingest("n1", _report(rx=1000), reported_at=iso_plus(now, -120))
    repos.agents.ingest("n1", _report(rx=7000), reported_at=iso_plus(now, -60))
    body = client.get("/api/admin/metrics/nodes?window=24", headers=ADMIN).json()
    assert body["window_hours"] == 24
    node = body["nodes"][0]
    assert node["node_name"] == "n1" and node["fresh"] is True
    # 프론트는 카운터를 모른다 -- 백엔드가 차분한 B/s가 바로 온다(설계 §3)
    assert [p["net_rx_bps"] for p in node["points"]] == [None, 100.0]
    assert node["points"][0]["mem_used_pct"] == 50.0
    assert node["points"][0]["disks"] == [{"storage_name": "s1", "used_pct": 40.0}]


def test_metrics_nodes_window_clamps_to_retention(client, db):
    Repositories(db).agents.ingest("n1", _report(), reported_at=utc_now_iso())
    body = client.get("/api/admin/metrics/nodes?window=1000", headers=ADMIN).json()
    assert body["window_hours"] == 720           # 30일 보존 상한(설계 §6-2)


def test_metrics_nodes_fail_soft_on_corrupt_report(client, db):
    repos = Repositories(db)
    now = utc_now_iso()
    repos.agents.ingest("n1", _report(), reported_at=iso_plus(now, -120))
    repos.agents.ingest("n1", _report(), reported_at=iso_plus(now, -60))
    db.execute("UPDATE agent_reports SET report = '{broken' WHERE reported_at = :at",
               {"at": iso_plus(now, -120)})
    body = client.get("/api/admin/metrics/nodes?window=24", headers=ADMIN).json()
    assert len(body["nodes"][0]["points"]) == 1  # 손상 행만 빠지고 시리즈는 산다(설계 §6-6)


def test_metrics_jobs_aggregates_and_histogram_shape(client, db):
    repos = Repositories(db)
    now = utc_now_iso()
    _seed_job(db, repos, created_at=iso_plus(now, -3600))
    _seed_job(db, repos, created_at=iso_plus(now, -1800), state="Failed",
              reason_code="execution_failed")
    body = client.get("/api/admin/metrics/jobs?window=24", headers=ADMIN).json()
    assert body["bucket"] == "hour"
    assert {r["state"]: r["count"] for r in body["by_state"]} == {
        "Succeeded": 1, "Failed": 1}
    assert body["failure_reasons"] == [
        {"reason_code": "execution_failed", "count": 1}]
    assert sum(b["count"] for b in body["throughput"]) == 2
    assert [b["bucket"] for b in body["duration_histogram"]] == [
        "<1m", "1-10m", "10-60m", "1-6h", "6-24h", ">24h"]
    assert body["files_total"] is None and body["bytes_total"] is None
    assert "duration_seconds" not in body        # 원자료는 내보내지 않는다


def test_metrics_jobs_day_bucket_beyond_48h(client):
    body = client.get("/api/admin/metrics/jobs?window=168", headers=ADMIN).json()
    assert body["bucket"] == "day" and body["window_hours"] == 168


def test_request_events_wrapper_scoped_to_request(client, db):
    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="alice",
                                actor="alice", resource_key="k:e1", payload={},
                                priority="mid")
    repos.observability.record_event(component="planner", severity="error",
                                     event_type="plan_error", message="boom",
                                     request_id=rid)
    repos.observability.record_event(component="planner", severity="error",
                                     event_type="other", request_id="someone-else")
    body = client.get(f"/api/admin/requests/{rid}/events", headers=ADMIN).json()
    assert body["request_id"] == rid
    assert [e["event_type"] for e in body["events"]] == ["plan_error"]
    assert body["events"][0]["message"] == "boom"


def test_request_events_unknown_request_404(client):
    r = client.get("/api/admin/requests/nope/events", headers=ADMIN)
    assert r.status_code == 404 and r.json()["detail"] == "request_not_found"


def test_request_events_admin_only(client):
    assert client.get("/api/admin/requests/x/events").status_code == 401
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_metrics.py -q`
Expected: FAIL — 전부 404 (라우터 미등록)

- [ ] **Step 3: 라우터를 만든다**

`src/dms/api/routes_metrics.py`:

```python
"""대시보드 메트릭 API(슬라이스 14). 전부 admin 전용 · 읽기 전용 -- 뮤테이션이
없으므로 감사 로그도 없다(설계 §3). 수치 조립은 저장소(앱측 JSON 파싱)와
metrics_series 순수 함수가 하고, 이 계층은 기간 클램프와 응답 조립만 한다."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..db import iso_plus, utc_now_iso
from ..metrics_series import (bucket_chars_for, build_node_points,
                              clamp_window_hours, duration_histogram)
from .auth import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


def _window(request: Request, window: int) -> "tuple[int, str, str]":
    settings = request.app.state.settings
    # 잡 통계도 같은 상한을 쓴다 -- data_jobs는 purge되지 않지만 "대시보드 창은
    # 최대 30일"이라는 하나의 규칙이 두 개의 규칙보다 낫다(설계 §3 기간 규약).
    hours = clamp_window_hours(
        window, retention_days=settings.agent_report_retention_days)
    end = utc_now_iso()
    return hours, iso_plus(end, -hours * 3600), end


@router.get("/api/admin/metrics/nodes")
def metrics_nodes(request: Request, window: int = Query(default=24, ge=1)):
    repos = request.app.state.repos
    settings = request.app.state.settings
    hours, start, end = _window(request, window)
    nodes = []
    # 노드 목록은 agent_nodes(노드당 1행)에서 온다 -- 창에 시계열이 비어도 노드가
    # 목록에서 사라지면 신선도(마지막 리포트 나이) 정보를 잃는다.
    for node in repos.agents.list_nodes(
            stale_seconds=settings.agent_report_stale_seconds):
        samples = repos.metrics.node_series(node["node_name"], start=start, end=end)
        nodes.append({"node_name": node["node_name"],
                      "reported_at": node["reported_at"], "fresh": node["fresh"],
                      "points": build_node_points(samples)})
    return {"window_hours": hours, "start": start, "end": end, "nodes": nodes}


@router.get("/api/admin/metrics/jobs")
def metrics_jobs(request: Request, window: int = Query(default=24, ge=1)):
    repos = request.app.state.repos
    hours, start, end = _window(request, window)
    chars = bucket_chars_for(hours)
    stats = repos.metrics.job_stats(start=start, end=end, bucket_chars=chars)
    # 원자료(초 목록)는 응답에 싣지 않는다 -- 프론트가 필요로 하는 것은 분포뿐이고,
    # 창이 크면 행 수만큼 커진다.
    stats["duration_histogram"] = duration_histogram(stats.pop("duration_seconds"))
    stats["window_hours"] = hours
    stats["bucket"] = "hour" if chars == 13 else "day"
    return stats


@router.get("/api/admin/requests/{request_id}/events")
def request_events(request_id: str, request: Request,
                   limit: int = Query(default=100, ge=1, le=1000)):
    # 설계 §2.5: 새 로직 없는 얇은 래퍼. events_for_request는 요청 상세 응답 안에만
    # 있어 대시보드 드릴다운이 요청 전체를 다시 받아야 했다 -- admin 게이트 뒤로만
    # 단독 노출한다.
    repos = request.app.state.repos
    if repos.requests.get(request_id) is None:
        raise HTTPException(status_code=404, detail="request_not_found")
    return {"request_id": request_id,
            "events": repos.observability.events_for_request(request_id, limit=limit)}
```

`src/dms/api/app.py` — import 블록에 `from .routes_metrics import router as metrics_router`, `app.include_router(releases_router)` 다음 줄에 `app.include_router(metrics_router)`.

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_metrics.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: 전체 스위트로 회귀를 확인한다**

Run: `.venv/bin/python -m pytest -q` (포그라운드, timeout 400000ms)
Expected: 전부 PASS — 특히 `tests/test_reason_codes_coverage.py`(신규 리터럴은 `request_not_found` 재사용뿐이라 그대로 초록이어야 한다)

- [ ] **Step 6: 커밋**

```bash
git add src/dms/api tests/test_api_metrics.py
git commit -m "feat(metrics): admin 메트릭 API(nodes/jobs)와 요청 이벤트 래퍼"
```

---

### Task 4: 인프라 뷰 — observe 카운트·판정 통과

**Files:**
- Modify: `src/dms/api/routes_metrics.py`
- Test: `tests/test_api_metrics.py` (기존, 확장)

**Interfaces:**
- Consumes: `app.state.rollout_runner.observe(*, kind, name)`(정규화 dict|None, 실패 시 `ExecutionError`); `ROLLOUT_ORDER`/`COMPONENTS` from `..repositories.releases`; `assess_deployment`/`assess_daemonset` from `..rollout_status`.
- Produces (Task 6 프론트가 그대로 쓴다):
  - `GET /api/admin/metrics/infra` → `{"components": [{"component", "kind", "workload", "image": str|null, "ready": int|null, "desired": int|null, "verdict": "applied"|"progressing"|"failed"|null, "detail": str|null}]}` — ROLLOUT_ORDER 순서, observe 실패/404는 그 컴포넌트만 null 강등

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_metrics.py` 끝에 추가:

```python
class _FakeObserver:
    """observe만 쓰는 페어 -- 반환 모양은 StubRolloutRunner.observe(rollout_runner.py
    실측)와 같은 정규화 dict다. api는 patch를 절대 부르지 않는다(슬라이스 13 RBAC)."""
    def __init__(self):
        self.fail = set()
        self.objects = {
            ("DaemonSet", "dms-agent"): {
                "kind": "DaemonSet", "generation": 1, "observed_generation": 1,
                "desired_number_scheduled": 5, "updated_number_scheduled": 5,
                "number_ready": 5, "number_unavailable": 0,
                "number_misscheduled": 0,
                "images": {"agent": "pkg-01:5000/dms-agent:dev6"}},
            ("Deployment", "dms-api"): {
                "kind": "Deployment", "generation": 3, "observed_generation": 3,
                "replicas": 1, "status_replicas": 1, "updated_replicas": 1,
                "ready_replicas": 1, "conditions": [],
                "images": {"api": "pkg-01:5000/dms:d23"}},
            ("Deployment", "dms-controller"): {
                "kind": "Deployment", "generation": 3, "observed_generation": 3,
                "replicas": 1, "status_replicas": 1, "updated_replicas": 1,
                "ready_replicas": 0, "conditions": [],
                "images": {"controller": "pkg-01:5000/dms:d23"}},
        }

    def observe(self, *, kind, name):
        if (kind, name) in self.fail:
            from dms.execution import ExecutionError
            raise ExecutionError("observe_failed", "down")
        return self.objects.get((kind, name))


def test_metrics_infra_passes_counts_and_verdict(client):
    client.app.state.rollout_runner = _FakeObserver()
    body = client.get("/api/admin/metrics/infra", headers=ADMIN).json()
    by = {c["component"]: c for c in body["components"]}
    assert [c["component"] for c in body["components"]] == [
        "dms-agent", "dms-api", "dms-controller"]        # ROLLOUT_ORDER 순
    assert by["dms-agent"]["image"] == "pkg-01:5000/dms-agent:dev6"
    assert (by["dms-agent"]["ready"], by["dms-agent"]["desired"]) == (5, 5)
    assert by["dms-agent"]["verdict"] == "applied"
    assert by["dms-api"]["verdict"] == "applied"
    assert by["dms-controller"]["verdict"] == "progressing"   # ready 0/1


def test_metrics_infra_degrades_only_the_failed_component(client):
    runner = _FakeObserver()
    runner.fail.add(("Deployment", "dms-api"))
    client.app.state.rollout_runner = runner
    body = client.get("/api/admin/metrics/infra", headers=ADMIN).json()
    by = {c["component"]: c for c in body["components"]}
    # observe 실패는 그 컴포넌트만 null 강등(슬라이스 13 규약) -- 화면 전체가 죽지 않는다
    assert by["dms-api"]["image"] is None and by["dms-api"]["verdict"] is None
    assert by["dms-agent"]["verdict"] == "applied"


def test_metrics_infra_admin_only(client):
    assert client.get("/api/admin/metrics/infra").status_code == 401
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_metrics.py -q`
Expected: 신규 3건 FAIL (404), 기존 9건 PASS

- [ ] **Step 3: 엔드포인트를 더한다**

`src/dms/api/routes_metrics.py` import에 추가:

```python
from ..execution import ExecutionError
from ..repositories.releases import COMPONENTS, ROLLOUT_ORDER
from ..rollout_status import assess_daemonset, assess_deployment
```

라우터 끝에:

```python
@router.get("/api/admin/metrics/infra")
def metrics_infra(request: Request):
    """컴포넌트 3종의 이미지 + N/N ready + 롤아웃 판정(설계 §2.4). targets가
    이미지만 남기고 버리던 observe()의 카운트와 assess_* 판정을 통과시킨다 --
    레지스트리를 건드리지 않으므로 targets보다 싸고(5s 폴링 가능), 슬라이스 13이
    api Role에 부여한 apps get 권한을 그대로 재사용한다(RBAC 변경 없음)."""
    runner = request.app.state.rollout_runner
    components = []
    for component in ROLLOUT_ORDER:
        spec = COMPONENTS[component]
        entry = {"component": component, "kind": spec["kind"],
                 "workload": spec["workload"], "image": None, "ready": None,
                 "desired": None, "verdict": None, "detail": None}
        try:
            obs = runner.observe(kind=spec["kind"], name=spec["workload"])
        except ExecutionError:
            obs = None    # 읽기 실패는 그 컴포넌트만 null 강등(슬라이스 13 규약)
        if obs is not None:
            entry["image"] = (obs.get("images") or {}).get(spec["container"])
            try:
                if spec["kind"] == "DaemonSet":
                    entry["ready"] = obs.get("number_ready")
                    entry["desired"] = obs.get("desired_number_scheduled")
                    entry["verdict"], entry["detail"] = assess_daemonset(obs)
                else:
                    entry["ready"] = obs.get("ready_replicas")
                    entry["desired"] = obs.get("replicas")
                    entry["verdict"], entry["detail"] = assess_deployment(obs)
            except Exception:
                # 정규화 키가 빠진 비정상 관측(구버전 스텁 등) -- 판정만 포기하고
                # 이미지·카운트는 남긴다. 대시보드 읽기는 전부 fail-soft다(설계 §3).
                entry["verdict"] = entry["detail"] = None
        components.append(entry)
    return {"components": components}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_metrics.py tests/test_api_releases.py -q`
Expected: 전부 PASS (releases targets 쪽 회귀 없음 — 그 파일은 손대지 않았다)

- [ ] **Step 5: 커밋**

```bash
git add src/dms/api/routes_metrics.py tests/test_api_metrics.py
git commit -m "feat(metrics): 인프라 뷰 -- observe 카운트·판정 통과"
```

---

### Task 5: Sparkline/BarChart 손수 SVG 컴포넌트

**Files:**
- Create: `frontend/src/components/ui/Sparkline.tsx`
- Create: `frontend/src/components/ui/Sparkline.test.tsx`
- Create: `frontend/src/components/ui/BarChart.tsx`
- Create: `frontend/src/components/ui/BarChart.test.tsx`

**Interfaces:**
- Produces (Task 7이 그대로 쓴다):
  - `export function sparklinePath(values: (number | null)[], width: number, height: number): string` — 순수 함수, 값→`path d`. `null`은 선을 끊는다(M 재시작), 전부 null이면 `""`
  - `export function Sparkline({ values, width = 120, height = 32, label }: { values: (number | null)[]; width?: number; height?: number; label?: string })` — 값이 없으면 `—`
  - `export interface BarDatum { label: string; value: number }`
  - `export function barRects(data: BarDatum[], width: number, height: number): { x; y; width; height; label; value }[]` — 순수 함수
  - `export function BarChart({ data, width = 240, height = 80, label }: { data: BarDatum[]; width?: number; height?: number; label?: string })` — 빈 데이터면 `—`, 막대마다 `<title>label: value</title>` 툴팁

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/components/ui/Sparkline.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Sparkline, sparklinePath } from "./Sparkline";

describe("sparklinePath", () => {
  it("값을 viewBox 좌표 path로 사상한다", () => {
    expect(sparklinePath([0, 5, 10], 100, 20)).toBe("M0,20L50,10L100,0");
  });
  it("null은 선을 끊는다 -- 0으로 잇지 않는다", () => {
    // 결측/카운터 리셋 구간을 0으로 이으면 "트래픽이 0이었다"는 거짓말이 된다
    expect(sparklinePath([0, null, 10], 100, 20)).toBe("M0,20M100,0");
  });
  it("평평한 시리즈는 중앙선", () => {
    expect(sparklinePath([3, 3], 100, 20)).toBe("M0,10L100,10");
  });
  it("전부 null이면 빈 path", () => {
    expect(sparklinePath([null, null], 100, 20)).toBe("");
  });
});

describe("Sparkline", () => {
  it("path d를 렌더한다", () => {
    const { container } = render(
      <Sparkline values={[0, 5, 10]} width={100} height={20} label="load1" />);
    expect(container.querySelector("path")!.getAttribute("d"))
      .toBe("M0,20L50,10L100,0");
    expect(container.querySelector("svg")!.getAttribute("viewBox"))
      .toBe("0 0 100 20");
  });
  it("값이 없으면 —", () => {
    render(<Sparkline values={[null, null]} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
```

`frontend/src/components/ui/BarChart.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BarChart, barRects } from "./BarChart";

describe("barRects", () => {
  it("값을 최대값 대비 높이로 사상한다", () => {
    expect(barRects([{ label: "a", value: 1 }, { label: "b", value: 4 }], 100, 80))
      .toEqual([
        { x: 5, y: 60, width: 40, height: 20, label: "a", value: 1 },
        { x: 55, y: 0, width: 40, height: 80, label: "b", value: 4 },
      ]);
  });
  it("전부 0이어도 0으로 나누지 않는다", () => {
    const rects = barRects([{ label: "a", value: 0 }], 100, 80);
    expect(rects[0].height).toBe(0);
  });
});

describe("BarChart", () => {
  it("rect와 title 툴팁을 렌더한다", () => {
    const { container } = render(
      <BarChart data={[{ label: "10시", value: 2 }, { label: "11시", value: 4 }]}
                width={100} height={80} />);
    const rects = container.querySelectorAll("rect");
    expect(rects).toHaveLength(2);
    expect(rects[1].getAttribute("height")).toBe("80");
    expect(container.querySelectorAll("title")[0].textContent).toBe("10시: 2");
  });
  it("빈 데이터는 —", () => {
    render(<BarChart data={[]} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/components/ui/Sparkline.test.tsx src/components/ui/BarChart.test.tsx`
Expected: FAIL — `Failed to resolve import "./Sparkline"` / `"./BarChart"`

- [ ] **Step 3: 구현한다**

`frontend/src/components/ui/Sparkline.tsx`:

```tsx
// 손수 SVG 스파크라인(설계 §2.1) -- 차트 라이브러리 하나가 수십 개 트랜지티브
// 의존성을 끌고 오는데 필요한 것은 선 하나다. 값 배열만 받는 순수 표현 컴포넌트라
// 값→path 단언으로 테스트한다.
const r2 = (n: number) => Math.round(n * 100) / 100;

export function sparklinePath(
  values: (number | null)[], width: number, height: number,
): string {
  const nums = values.filter((v): v is number => v !== null && Number.isFinite(v));
  if (nums.length === 0) return "";
  const min = Math.min(...nums);
  const span = Math.max(...nums) - min;
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v === null || !Number.isFinite(v)) {
      // null은 결측/카운터 리셋 구간이다 -- 선을 끊는다. 0으로 이으면
      // "그때 값이 0이었다"는 거짓말이 된다.
      pen = false;
      return;
    }
    const norm = span === 0 ? 0.5 : (v - min) / span; // 평평한 시리즈는 중앙선
    d += `${pen ? "L" : "M"}${r2(i * step)},${r2(height - norm * height)}`;
    pen = true;
  });
  return d;
}

export function Sparkline({ values, width = 120, height = 32, label }: {
  values: (number | null)[]; width?: number; height?: number; label?: string;
}) {
  const d = sparklinePath(values, width, height);
  if (!d) return <span className="text-muted text-xs">—</span>;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-8"
         preserveAspectRatio="none" role="img" aria-label={label}>
      {/* currentColor -- 색은 부모의 text-* 유틸리티가 정한다(라이트/다크 공통) */}
      <path d={d} fill="none" stroke="currentColor" strokeWidth={1.5}
            vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
```

`frontend/src/components/ui/BarChart.tsx`:

```tsx
// 손수 SVG 막대(설계 §2.1). barRects를 순수 함수로 분리해 값→rect 기하를
// 단언으로 테스트한다 -- 렌더 스냅샷보다 회귀를 정확히 잡는다.
const r2 = (n: number) => Math.round(n * 100) / 100;

export interface BarDatum { label: string; value: number }

export function barRects(data: BarDatum[], width: number, height: number) {
  const max = Math.max(...data.map((d) => d.value), 1); // 전부 0이어도 0-나눗셈 없음
  const slot = width / data.length;
  return data.map((d, i) => {
    const h = r2((d.value / max) * height);
    return { x: r2(i * slot + slot * 0.1), y: r2(height - h),
             width: r2(slot * 0.8), height: h, label: d.label, value: d.value };
  });
}

export function BarChart({ data, width = 240, height = 80, label }: {
  data: BarDatum[]; width?: number; height?: number; label?: string;
}) {
  if (data.length === 0) return <span className="text-muted text-xs">—</span>;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full"
         preserveAspectRatio="none" role="img" aria-label={label}>
      {barRects(data, width, height).map((r, i) => (
        <rect key={i} x={r.x} y={r.y} width={r.width} height={r.height}
              fill="currentColor" opacity={0.85}>
          {/* 축 라벨 대신 title 툴팁 -- 스파크라인급 밀도에서 텍스트 축은 겹친다 */}
          <title>{`${r.label}: ${r.value}`}</title>
        </rect>
      ))}
    </svg>
  );
}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run src/components/ui/` 그리고 `npx tsc -b`
Expected: PASS, 타입 에러 0

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/components/ui
git commit -m "feat(portal): 손수 SVG Sparkline/BarChart 컴포넌트"
```

---

### Task 6: 대시보드 개요 — 잡 통계 KPI 전환 + 인프라 카드

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Create: `frontend/src/features/dashboard/useMetrics.ts`
- Modify: `frontend/src/features/dashboard/Dashboard.tsx`
- Modify: `frontend/src/features/dashboard/Dashboard.test.tsx`

**Interfaces:**
- Consumes: Task 3·4 응답 모양; 기존 `useRequests`/`useNodes`/`MetricTile`/`Card`/`StatusPill`.
- Produces (Task 7이 그대로 쓴다):
  - `frontend/src/lib/types.ts`에:
    ```ts
    export interface NodeMetricDisk { storage_name: string; used_pct: number | null }
    export interface NodeMetricPoint {
      at: string; load1: number | null; load5: number | null; load15: number | null;
      mem_used_pct: number | null; net_rx_bps: number | null; net_tx_bps: number | null;
      disks: NodeMetricDisk[];
    }
    export interface NodeMetricSeries {
      node_name: string; reported_at: string; fresh: boolean; points: NodeMetricPoint[];
    }
    export interface NodeMetrics {
      window_hours: number; start: string; end: string; nodes: NodeMetricSeries[];
    }
    export interface StateCount { state: string; count: number }
    export interface BreakdownRow { count: number; succeeded: number; failed: number }
    export interface JobMetrics {
      window_hours: number; bucket: "hour" | "day";
      by_state: StateCount[];
      by_tool: ({ tool: string | null } & BreakdownRow)[];
      by_storage: ({ storage: string | null } & BreakdownRow)[];
      by_requester: ({ requester_id: string } & BreakdownRow)[];
      failure_reasons: { reason_code: string; count: number }[];
      throughput: { bucket: string; count: number }[];
      duration_histogram: { bucket: string; count: number }[];
      files_total: number | null; bytes_total: number | null;
    }
    export interface InfraComponent {
      component: string; kind: string; workload: string;
      image: string | null; ready: number | null; desired: number | null;
      verdict: "applied" | "progressing" | "failed" | null; detail: string | null;
    }
    export interface InfraMetrics { components: InfraComponent[] }
    ```
  - `useMetrics.ts`에: `useNodeMetrics(windowH: number)`(키 `["metrics","nodes",windowH]`, 폴링 없음), `useJobMetrics(windowH: number)`(키 `["metrics","jobs",windowH]`, `refetchInterval: 5000`), `useInfraMetrics()`(키 `["metrics","infra"]`, `refetchInterval: 5000`)
  - `Dashboard.tsx`에 `export function kpiFromStates(byState: StateCount[]): { running: number; pending: number; succeeded: number; failed: number }`

- [ ] **Step 1: 기존 테스트를 갱신해 RED로 만든다**

`frontend/src/features/dashboard/Dashboard.test.tsx` 전체 교체:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { Dashboard } from "./Dashboard";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const JOB_METRICS = {
  window_hours: 24, bucket: "hour",
  by_state: [
    { state: "Executing", count: 3 }, { state: "Pending", count: 2 },
    { state: "Succeeded", count: 20 }, { state: "Failed", count: 4 },
    { state: "TimedOut", count: 1 },
  ],
  by_tool: [], by_storage: [], by_requester: [], failure_reasons: [],
  throughput: [], duration_histogram: [], files_total: null, bytes_total: null,
};

const INFRA = {
  components: [
    { component: "dms-agent", kind: "DaemonSet", workload: "dms-agent",
      image: "pkg-01:5000/dms-agent:dev6", ready: 5, desired: 5,
      verdict: "applied", detail: null },
    { component: "dms-api", kind: "Deployment", workload: "dms-api",
      image: null, ready: null, desired: null, verdict: null, detail: null },
  ],
};

function renderDash(overrides: Record<string, unknown> = {}) {
  server.use(
    http.get("/api/admin/metrics/jobs",
             () => HttpResponse.json(overrides.jobs ?? JOB_METRICS)),
    http.get("/api/admin/metrics/infra", () => HttpResponse.json(INFRA)),
    http.get("/api/admin/metrics/nodes",
             () => HttpResponse.json({ window_hours: 24, start: "", end: "", nodes: [] })),
    http.get("/api/user/requests", () => HttpResponse.json([
      { request_id: "r1", operation: "sync", state: "Executing", priority: "mid",
        created_at: "", updated_at: "", requester_id: "a", resource_key: "k",
        payload: {} }])),
    http.get("/api/admin/nodes", () => HttpResponse.json([
      { node_name: "w1", reported_at: "2026-08-09T00:00:00Z", fresh: true,
        report: {} }])),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><Dashboard /></MemoryRouter>
    </QueryClientProvider>);
}

test("KPI 타일은 요청 목록 즉석 계산이 아니라 잡 통계 집계를 쓴다", async () => {
  // 옛 스텁은 페이지네이션 상한(50건)에 걸려 총계가 거짓이 됐다(설계 §4.1)
  renderDash();
  const running = (await screen.findByText("실행 중")).parentElement!;
  await waitFor(() => expect(running).toHaveTextContent("3"));
  expect(screen.getByText("대기").parentElement).toHaveTextContent("2");
  expect(screen.getByText("성공(24h)").parentElement).toHaveTextContent("20");
  expect(screen.getByText("실패(24h)").parentElement).toHaveTextContent("5"); // Failed+TimedOut
});

test("컴포넌트 카드가 이미지·ready·판정을 보여주고 null은 —", async () => {
  renderDash();
  expect(await screen.findByText("dms-agent")).toBeInTheDocument();
  expect(screen.getByText("pkg-01:5000/dms-agent:dev6")).toBeInTheDocument();
  expect(screen.getByText("5/5")).toBeInTheDocument();
  expect(screen.getByText("applied")).toBeInTheDocument();
  expect(screen.getByText("—/—")).toBeInTheDocument();   // observe 강등된 dms-api
});

test("잡 통계가 비배열로 와도 죽지 않는다", async () => {
  renderDash({ jobs: { by_state: null } });
  const running = (await screen.findByText("실행 중")).parentElement!;
  expect(running).toHaveTextContent("0");
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/features/dashboard/Dashboard.test.tsx`
Expected: FAIL — KPI가 여전히 요청 목록 계산(값 1)이고 컴포넌트 카드가 없다

- [ ] **Step 3: 타입과 훅을 만든다**

`frontend/src/lib/types.ts` 끝에 Interfaces 블록의 타입들을 그대로 추가한다.

`frontend/src/features/dashboard/useMetrics.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { InfraMetrics, JobMetrics, NodeMetrics } from "../../lib/types";

// 쿼리 키는 ["metrics", ...]로 묶는다 -- 기존 ["nodes"]는 dashboard/useDashboard.ts와
// nodes/useNodes.ts가 /api/admin/nodes 캐시로 공유 중이라 절대 겹치면 안 된다.

export const useNodeMetrics = (windowH: number) =>
  useQuery({
    queryKey: ["metrics", "nodes", windowH],
    // 시계열은 기간 재조회 위주(설계 §4) -- 짧은 폴링을 걸지 않는다
    queryFn: () => apiGet<NodeMetrics>(`/api/admin/metrics/nodes?window=${windowH}`),
  });

export const useJobMetrics = (windowH: number) =>
  useQuery({
    queryKey: ["metrics", "jobs", windowH],
    queryFn: () => apiGet<JobMetrics>(`/api/admin/metrics/jobs?window=${windowH}`),
    refetchInterval: 5000,   // 개요 KPI가 이 쿼리를 그대로 쓴다(설계 §4: 개요만 짧게)
  });

export const useInfraMetrics = () =>
  useQuery({
    queryKey: ["metrics", "infra"],
    queryFn: () => apiGet<InfraMetrics>("/api/admin/metrics/infra"),
    refetchInterval: 5000,
  });
```

- [ ] **Step 4: Dashboard 개요를 전환한다**

`frontend/src/features/dashboard/Dashboard.tsx` 교체:

```tsx
import { useRequests } from "../jobs/useJobs";
import { useNodes } from "./useDashboard";
import { useInfraMetrics, useJobMetrics } from "./useMetrics";
import { MetricTile } from "../../components/ui/MetricTile";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import type { PillVariant } from "../../lib/jobState";
import type { StateCount } from "../../lib/types";

// KPI는 잡 상태의 집합 합산이다. 옛 스텁의 요청 목록 즉석 계산은 페이지네이션
// 상한(50건)에 걸려 총계가 거짓이 됐다 -- 백엔드 GROUP BY 집계로 바꾼다(설계 §4.1).
// 집합은 domain.DataJobState 기준: 비종단 중 Pending만 "대기", 나머지가 "실행 중".
const RUNNING_STATES = new Set(
  ["Preflight", "PreviewRunning", "ConfirmPending", "Executing", "Running"]);
const FAILED_STATES = new Set(["Failed", "TimedOut"]);

export function kpiFromStates(byState: StateCount[]) {
  const sum = (pred: (s: string) => boolean) =>
    byState.filter((r) => pred(r.state)).reduce((a, r) => a + r.count, 0);
  return {
    running: sum((s) => RUNNING_STATES.has(s)),
    pending: sum((s) => s === "Pending"),
    succeeded: sum((s) => s === "Succeeded"),
    failed: sum((s) => FAILED_STATES.has(s)),
  };
}

// 판정 배지: 릴리스 화면(releasePillVariant)과 같은 이유로 공용 pillVariant를
// 고치지 않는다 -- applied/progressing은 공용 매핑이 모르는 어휘다.
const VERDICT_VARIANT: Record<string, PillVariant> = {
  applied: "ok", progressing: "busy", failed: "bad",
};

export function Dashboard() {
  const reqs = useRequests();
  const nodes = useNodes();
  const jobsQ = useJobMetrics(24);
  const infraQ = useInfraMetrics();
  // 방어적 정규화 -- 배열 아닌 페이로드 하나가 화면을 죽이면 안 된다
  const byState = Array.isArray(jobsQ.data?.by_state) ? jobsQ.data.by_state : [];
  const kpi = kpiFromStates(byState);
  const components = Array.isArray(infraQ.data?.components)
    ? infraQ.data.components : [];
  const rs = Array.isArray(reqs.data) ? reqs.data : [];
  return (
    <section className="space-y-5">
      <h1 className="text-lg font-semibold">대시보드</h1>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricTile label="실행 중" value={kpi.running} />
        <MetricTile label="대기" value={kpi.pending} />
        <MetricTile label="성공(24h)" value={kpi.succeeded} />
        <MetricTile label="실패(24h)" value={kpi.failed} />
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <Card>
          <h2 className="font-medium mb-3">컴포넌트</h2>
          <ul className="space-y-2 text-sm">
            {components.map((c) => (
              <li key={c.component} className="flex items-center gap-2">
                <span className="shrink-0">{c.component}</span>
                <span className="text-muted text-xs truncate grow">
                  {c.image ?? "—"}
                </span>
                <span className="text-xs tabular-nums shrink-0">
                  {`${c.ready ?? "—"}/${c.desired ?? "—"}`}
                </span>
                <StatusPill state={c.verdict ?? "unknown"}
                            variant={c.verdict ? VERDICT_VARIANT[c.verdict] : "neutral"} />
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <h2 className="font-medium mb-3">최근 작업</h2>
          <ul className="space-y-2 text-sm">
            {rs.slice(0, 6).map((r) => (
              <li key={r.request_id} className="flex items-center justify-between">
                <span>{r.request_id} · {r.operation}</span>
                <StatusPill state={r.state} />
              </li>
            ))}
          </ul>
        </Card>
      </div>
      {/* 노드 상태 카드는 Task 7의 시계열 섹션(NodeMetricsSection)이 대체한다 --
          그때까지 신선도 목록을 유지해 화면 공백을 만들지 않는다 */}
      <Card>
        <h2 className="font-medium mb-3">노드 상태</h2>
        <ul className="space-y-2 text-sm">
          {(nodes.data ?? []).map((n) => (
            <li key={n.node_name} className="flex items-center justify-between">
              <span>{n.node_name}</span>
              <StatusPill state={n.fresh ? "Succeeded" : "Failed"} />
            </li>
          ))}
        </ul>
      </Card>
    </section>
  );
}
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd frontend && npx vitest run` 그리고 `npx tsc -b`
Expected: 전체 PASS, 타입 에러 0

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/lib/types.ts frontend/src/features/dashboard
git commit -m "feat(portal): 대시보드 개요를 잡 통계·인프라 집계로 전환"
```

---

### Task 7: 노드 시계열·잡 통계 섹션

**Files:**
- Create: `frontend/src/features/dashboard/WindowSelect.tsx`
- Create: `frontend/src/features/dashboard/NodeMetricsSection.tsx`
- Create: `frontend/src/features/dashboard/NodeMetricsSection.test.tsx`
- Create: `frontend/src/features/dashboard/JobStatsSection.tsx`
- Create: `frontend/src/features/dashboard/JobStatsSection.test.tsx`
- Modify: `frontend/src/features/dashboard/Dashboard.tsx`

**Interfaces:**
- Consumes: `useNodeMetrics`/`useJobMetrics`(Task 6, 시그니처 `(windowH: number)`), `useNodes` from `./useDashboard`, `Sparkline`/`BarChart`(Task 5), `reasonText` from `../../lib/api`, 타입들(Task 6).
- Produces:
  - `export function WindowSelect({ value, onChange }: { value: number; onChange: (h: number) => void })` — 1h/6h/24h/7d(=1/6/24/168) 버튼
  - `export function NodeMetricsSection()` / `export function JobStatsSection()` — Dashboard가 마운트

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/features/dashboard/NodeMetricsSection.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { NodeMetricsSection } from "./NodeMetricsSection";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const METRICS = {
  window_hours: 24, start: "2026-08-09T00:00:00Z", end: "2026-08-10T00:00:00Z",
  nodes: [{
    node_name: "w1", reported_at: "2026-08-10T00:00:00Z", fresh: true,
    points: [
      { at: "2026-08-09T23:58:00Z", load1: 0.5, load5: 0.4, load15: 0.3,
        mem_used_pct: 50, net_rx_bps: null, net_tx_bps: null,
        disks: [{ storage_name: "s1", used_pct: 40 }] },
      { at: "2026-08-09T23:59:00Z", load1: 0.7, load5: 0.4, load15: 0.3,
        mem_used_pct: 55, net_rx_bps: 100, net_tx_bps: 10,
        disks: [{ storage_name: "s1", used_pct: 41 }] },
    ],
  }],
};

const NODES = [{
  node_name: "w1", reported_at: "2026-08-10T00:00:00Z", fresh: true,
  report: {
    mounts: [{ storage_name: "s1", mount_path: "/s1", status: "Ready" }],
    tools: [{ name: "dsync", status: "Ready" }, { name: "drm", status: "Missing" }],
    identities: [],
  },
}];

function renderSection(calls?: (string | null)[]) {
  server.use(
    http.get("/api/admin/metrics/nodes", ({ request }) => {
      calls?.push(new URL(request.url).searchParams.get("window"));
      return HttpResponse.json(METRICS);
    }),
    http.get("/api/admin/nodes", () => HttpResponse.json(NODES)),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><NodeMetricsSection /></QueryClientProvider>);
}

test("노드별 스파크라인과 신선도를 그린다", async () => {
  const { container } = renderSection();
  expect(await screen.findByText("w1")).toBeInTheDocument();
  expect(screen.getByText(/정상/)).toBeInTheDocument();
  // 값이 있는 시리즈는 path가 실제로 그려진다
  await waitFor(() =>
    expect(container.querySelectorAll("svg path").length).toBeGreaterThan(0));
});

test("기간 버튼이 window 파라미터로 재조회한다", async () => {
  const calls: (string | null)[] = [];
  renderSection(calls);
  await screen.findByText("w1");
  expect(calls[0]).toBe("24");           // 기본 24h
  await userEvent.click(screen.getByRole("button", { name: "1h" }));
  await waitFor(() => expect(calls).toContain("1"));
});

test("드릴다운에 스토리지별 디스크와 증거 스냅샷이 나온다", async () => {
  renderSection();
  await userEvent.click(await screen.findByRole("button", { name: "w1" }));
  expect(await screen.findByText("s1")).toBeInTheDocument();
  expect(screen.getByText(/마운트 1\/1/)).toBeInTheDocument();
  expect(screen.getByText(/도구 1\/2/)).toBeInTheDocument();
});

test("nodes가 비배열이어도 죽지 않는다", async () => {
  server.use(
    http.get("/api/admin/metrics/nodes",
             () => HttpResponse.json({ nodes: null })),
    http.get("/api/admin/nodes", () => HttpResponse.json(NODES)),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><NodeMetricsSection /></QueryClientProvider>);
  expect(await screen.findByText("노드/리소스")).toBeInTheDocument();
});
```

`frontend/src/features/dashboard/JobStatsSection.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { JobStatsSection } from "./JobStatsSection";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const STATS = {
  window_hours: 24, bucket: "hour",
  by_state: [
    { state: "Succeeded", count: 20 }, { state: "Failed", count: 8 },
    { state: "TimedOut", count: 2 }, { state: "Rejected", count: 9 },
    { state: "Cancelled", count: 3 }, { state: "PreviewExpired", count: 1 },
    { state: "Pending", count: 2 },
  ],
  by_tool: [{ tool: "dscan", count: 23, succeeded: 20, failed: 3 }],
  by_storage: [{ storage: "cephfs-a", count: 30, succeeded: 18, failed: 12 }],
  by_requester: [{ requester_id: "alice", count: 30, succeeded: 20, failed: 10 }],
  failure_reasons: [{ reason_code: "execution_failed", count: 2 }],
  throughput: [{ bucket: "2026-08-09T01", count: 2 },
               { bucket: "2026-08-09T02", count: 1 }],
  duration_histogram: [
    { bucket: "<1m", count: 1 }, { bucket: "1-10m", count: 0 },
    { bucket: "10-60m", count: 1 }, { bucket: "1-6h", count: 0 },
    { bucket: "6-24h", count: 0 }, { bucket: ">24h", count: 0 }],
  files_total: null, bytes_total: null,
};

function renderSection(stats: unknown = STATS) {
  server.use(http.get("/api/admin/metrics/jobs", () => HttpResponse.json(stats)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><JobStatsSection /></QueryClientProvider>);
}

test("성공률·처리량·분해 표·실패 사유를 그린다", async () => {
  renderSection();
  // 종단 43건 중 성공 20 = 47%
  expect(await screen.findByText(/47%/)).toBeInTheDocument();
  const chart = screen.getByRole("img", { name: "처리량" });
  expect(chart.querySelectorAll("rect")).toHaveLength(2);
  expect(screen.getByText("dscan")).toBeInTheDocument();
  expect(screen.getByText("cephfs-a")).toBeInTheDocument();
  expect(screen.getByText("alice")).toBeInTheDocument();
  // 사유 코드는 reasonText로 한글화된다(설계 §4.3)
  expect(screen.getByText("실행에 실패했습니다")).toBeInTheDocument();
});

test("files/bytes가 NULL이면 — 로 우아하게 생략한다", async () => {
  renderSection();
  const row = await screen.findByText("처리 파일/바이트");
  expect(row.parentElement).toHaveTextContent("— / —");
});

test("응답이 비배열이어도 죽지 않는다", async () => {
  renderSection({ by_state: null });
  expect(await screen.findByText("잡 통계")).toBeInTheDocument();
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/features/dashboard/`
Expected: 신규 2파일 FAIL — `Failed to resolve import "./NodeMetricsSection"` 등

- [ ] **Step 3: WindowSelect를 만든다**

`frontend/src/features/dashboard/WindowSelect.tsx`:

```tsx
// 설계 §4.2의 기간 선택(1h/6h/24h/7d). 백엔드가 720h로 클램프하므로 프론트는
// 선택지만 제한하면 된다 -- 자유 입력을 받지 않는다.
const WINDOWS = [
  { label: "1h", hours: 1 }, { label: "6h", hours: 6 },
  { label: "24h", hours: 24 }, { label: "7d", hours: 168 },
] as const;

export function WindowSelect({ value, onChange }: {
  value: number; onChange: (h: number) => void;
}) {
  return (
    <div className="flex gap-1">
      {WINDOWS.map((w) => (
        <button key={w.hours} onClick={() => onChange(w.hours)}
                className={`rounded px-2 py-1 text-xs border ${
                  value === w.hours
                    ? "font-semibold border-black/30"
                    : "text-muted border-black/10"}`}>
          {w.label}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: NodeMetricsSection을 만든다**

`frontend/src/features/dashboard/NodeMetricsSection.tsx`:

```tsx
import { useState } from "react";
import { useNodeMetrics } from "./useMetrics";
import { useNodes } from "./useDashboard";
import { WindowSelect } from "./WindowSelect";
import { Card } from "../../components/ui/Card";
import { Sparkline } from "../../components/ui/Sparkline";
import type { NodeMetricPoint, NodeMetricSeries } from "../../lib/types";

// 에이전트 리포트는 스키마 검증 없이 저장된다 -- NodesList.tsx와 같은 방어 관용구
const asArray = <T,>(v: unknown): T[] => (Array.isArray(v) ? v : []);

function ageText(reportedAt: string): string {
  const ms = Date.now() - Date.parse(reportedAt);
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const m = Math.floor(ms / 60000);
  if (m < 1) return "방금";
  if (m < 60) return `${m}분 전`;
  return `${Math.floor(m / 60)}시간 전`;
}

function pick(points: NodeMetricPoint[], f: (p: NodeMetricPoint) => number | null) {
  return points.map(f);
}

// 증거 스냅샷(설계 §4.2): Ready n/전체 요약. 원본 표는 노드 화면(/admin/nodes)에
// 이미 있으므로 여기서는 비율만 -- 상세가 필요하면 그 화면으로 간다.
function readyCount(items: unknown): string {
  const arr = asArray<{ status?: string }>(items);
  return `${arr.filter((i) => i.status === "Ready").length}/${arr.length}`;
}

function Metric({ title, values, label }: {
  title: string; values: (number | null)[]; label: string;
}) {
  return (
    <div>
      <div className="text-muted text-xs">{title}</div>
      <Sparkline values={values} label={label} />
    </div>
  );
}

export function NodeMetricsSection() {
  const [windowH, setWindowH] = useState(24);
  const [open, setOpen] = useState<string | null>(null);
  const metricsQ = useNodeMetrics(windowH);
  const nodesQ = useNodes();
  const series = asArray<NodeMetricSeries>(metricsQ.data?.nodes);
  const reports = new Map(asArray(nodesQ.data).map((n) => [n.node_name, n.report]));
  return (
    <Card>
      <div className="flex items-center justify-between mb-2">
        <h2 className="font-medium">노드/리소스</h2>
        <WindowSelect value={windowH} onChange={setWindowH} />
      </div>
      {metricsQ.isLoading && <p className="text-muted text-sm">불러오는 중…</p>}
      {series.map((n) => {
        const points = asArray<NodeMetricPoint>(n.points);
        const report = reports.get(n.node_name);
        // 스토리지 이름은 포인트마다 다를 수 있다(스토리지 추가/제거) -- 합집합으로 그린다
        const diskNames = [...new Set(points.flatMap(
          (p) => asArray<{ storage_name: string }>(p.disks).map((d) => d.storage_name)))];
        return (
          <div key={n.node_name} className="border-t border-black/5 py-3">
            <div className="flex items-center justify-between">
              <button className="font-medium" onClick={() =>
                setOpen(open === n.node_name ? null : n.node_name)}>
                {n.node_name}
              </button>
              <span className={`text-xs ${n.fresh ? "text-ok" : "text-bad"}`}>
                {n.fresh ? "정상" : "지연"} · {ageText(n.reported_at)}
              </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
              <Metric title="load1" label={`${n.node_name} load1`}
                      values={pick(points, (p) => p.load1)} />
              <Metric title="메모리 사용%" label={`${n.node_name} 메모리`}
                      values={pick(points, (p) => p.mem_used_pct)} />
              <Metric title="수신 B/s" label={`${n.node_name} 수신`}
                      values={pick(points, (p) => p.net_rx_bps)} />
              <Metric title="송신 B/s" label={`${n.node_name} 송신`}
                      values={pick(points, (p) => p.net_tx_bps)} />
            </div>
            {open === n.node_name && (
              <div className="mt-3 space-y-3">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <Metric title="load5" label={`${n.node_name} load5`}
                          values={pick(points, (p) => p.load5)} />
                  <Metric title="load15" label={`${n.node_name} load15`}
                          values={pick(points, (p) => p.load15)} />
                  {diskNames.map((name) => (
                    <div key={name}>
                      <div className="text-muted text-xs">{name} 사용%</div>
                      <Sparkline label={`${n.node_name} ${name} 디스크`}
                                 values={points.map((p) =>
                                   asArray<{ storage_name: string; used_pct: number | null }>(p.disks)
                                     .find((d) => d.storage_name === name)?.used_pct ?? null)} />
                    </div>
                  ))}
                </div>
                {report != null && (
                  <p className="text-muted text-xs">
                    마운트 {readyCount((report as { mounts?: unknown }).mounts)} ·
                    도구 {readyCount((report as { tools?: unknown }).tools)} ·
                    계정 {readyCount((report as { identities?: unknown }).identities)}
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}
    </Card>
  );
}
```

- [ ] **Step 5: JobStatsSection을 만든다**

`frontend/src/features/dashboard/JobStatsSection.tsx`:

```tsx
import { useState } from "react";
import { useJobMetrics } from "./useMetrics";
import { WindowSelect } from "./WindowSelect";
import { Card } from "../../components/ui/Card";
import { Table } from "../../components/ui/Table";
import { BarChart } from "../../components/ui/BarChart";
import { reasonText } from "../../lib/api";
import type { StateCount } from "../../lib/types";

const asArray = <T,>(v: unknown): T[] => (Array.isArray(v) ? v : []);

// 종단 집합은 domain.TERMINAL_DATA_JOB_STATES와 동일한 6종. jobState.ts의
// TERMINAL_STATES는 요청 화면용 옛 집합이라 TimedOut이 빠져 있어 쓰지 않는다.
const TERMINAL = new Set(
  ["Succeeded", "Failed", "TimedOut", "Cancelled", "Rejected", "PreviewExpired"]);

export function successRate(byState: StateCount[]): string {
  const terminal = byState.filter((r) => TERMINAL.has(r.state))
    .reduce((a, r) => a + r.count, 0);
  if (terminal === 0) return "—";
  const ok = byState.find((r) => r.state === "Succeeded")?.count ?? 0;
  return `${Math.round((ok / terminal) * 100)}% (${ok}/${terminal})`;
}

// NodesList.tsx의 humanBytes와 같은 로직의 국소 사본 -- 그쪽은 export하지 않고,
// 이 표시 하나를 위해 공용 모듈을 만드는 것은 이르다.
const BYTE_UNITS: [string, number][] = [
  ["TiB", 1024 ** 4], ["GiB", 1024 ** 3], ["MiB", 1024 ** 2],
];
function humanBytes(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes)) return "—";
  for (const [unit, size] of BYTE_UNITS) {
    if (bytes >= size) return `${(bytes / size).toFixed(1)} ${unit}`;
  }
  return `${bytes} B`;
}

// "2026-08-09T01" -> "01시", "2026-08-09" -> "08-09"
function bucketLabel(bucket: string, kind: "hour" | "day"): string {
  return kind === "hour" ? `${bucket.slice(11, 13)}시` : bucket.slice(5);
}

function Breakdown({ title, rows, nameOf }: {
  title: string;
  rows: { count: number; succeeded: number; failed: number }[];
  nameOf: (r: never) => string | null;
}) {
  if (rows.length === 0) return null;
  return (
    <div>
      <h3 className="font-medium mb-2 text-sm">{title}</h3>
      <Table>
        <thead>
          <tr className="text-muted">
            <th className="py-1">이름</th><th>총</th><th>성공</th><th>실패</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-black/5">
              <td className="py-1">{nameOf(r as never) ?? "—"}</td>
              <td>{r.count}</td><td>{r.succeeded}</td><td>{r.failed}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

export function JobStatsSection() {
  const [windowH, setWindowH] = useState(24);
  const q = useJobMetrics(windowH);
  const d = q.data;
  const byState = asArray<StateCount>(d?.by_state);
  const bucketKind = d?.bucket === "day" ? "day" : "hour";
  const throughput = asArray<{ bucket: string; count: number }>(d?.throughput)
    .map((b) => ({ label: bucketLabel(b.bucket, bucketKind), value: b.count }));
  const durations = asArray<{ bucket: string; count: number }>(d?.duration_histogram)
    .map((b) => ({ label: b.bucket, value: b.count }));
  const reasons = asArray<{ reason_code: string; count: number }>(d?.failure_reasons);
  return (
    <Card>
      <div className="flex items-center justify-between mb-2">
        <h2 className="font-medium">잡 통계</h2>
        <WindowSelect value={windowH} onChange={setWindowH} />
      </div>
      {q.isLoading && <p className="text-muted text-sm">불러오는 중…</p>}
      <p className="text-sm">성공률 {successRate(byState)}</p>
      <div className="grid md:grid-cols-2 gap-4 mt-3">
        <div>
          <h3 className="font-medium mb-2 text-sm">처리량</h3>
          <BarChart data={throughput} label="처리량" />
        </div>
        <div>
          <h3 className="font-medium mb-2 text-sm">수행시간 분포</h3>
          <BarChart data={durations} label="수행시간 분포" />
        </div>
      </div>
      <div className="grid md:grid-cols-3 gap-4 mt-4">
        <Breakdown title="도구별" rows={asArray(d?.by_tool)}
                   nameOf={(r: { tool: string | null }) => r.tool} />
        <Breakdown title="스토리지별" rows={asArray(d?.by_storage)}
                   nameOf={(r: { storage: string | null }) => r.storage} />
        <Breakdown title="사용자별" rows={asArray(d?.by_requester)}
                   nameOf={(r: { requester_id: string }) => r.requester_id} />
      </div>
      {reasons.length > 0 && (
        <div className="mt-4">
          <h3 className="font-medium mb-2 text-sm">실패 사유 상위</h3>
          <Table>
            <thead>
              <tr className="text-muted"><th className="py-1">사유</th><th>건수</th></tr>
            </thead>
            <tbody>
              {reasons.map((r) => (
                <tr key={r.reason_code} className="border-t border-black/5">
                  <td className="py-1">{reasonText(r.reason_code)}</td>
                  <td>{r.count}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      <p className="text-muted text-sm mt-4">
        <span className="font-medium">처리 파일/바이트</span>{" "}
        {d?.files_total ?? "—"} / {humanBytes(d?.bytes_total ?? null)}
      </p>
    </Card>
  );
}
```

`Breakdown`의 `nameOf` 타이핑이 `never` 캐스트 없이 더 깔끔하게 되면(예: 제네릭 `<R extends BreakdownRow>`) 그쪽을 써라 — `npx tsc -b`가 초록인 형태가 정답이다.

- [ ] **Step 6: Dashboard에 섹션을 조립한다**

`frontend/src/features/dashboard/Dashboard.tsx`에서 Task 6이 남겨 둔 「노드 상태」 카드를 제거하고, `useNodes` import도 지운 뒤 그 자리에:

```tsx
      <NodeMetricsSection />
      <JobStatsSection />
```

를 마운트한다 (`import { NodeMetricsSection } from "./NodeMetricsSection";`, `import { JobStatsSection } from "./JobStatsSection";`).

- [ ] **Step 7: 통과를 확인한다**

Run: `cd frontend && npx vitest run` 그리고 `npx tsc -b`
Expected: 전체 PASS, 타입 에러 0. Dashboard.test.tsx의 `/api/admin/metrics/nodes`·`/api/admin/nodes`·`/api/admin/metrics/jobs` 핸들러는 이미 있으므로(Task 6 Step 1) 그대로 초록이어야 한다 — 미처리 요청 경고가 나오면 핸들러 경로를 맞춰라.

- [ ] **Step 8: 커밋**

```bash
git add frontend/src/features/dashboard
git commit -m "feat(portal): 노드 시계열·잡 통계 대시보드 섹션"
```

---

### Task 8: 요청 상세 큐 대기 유도 표시

**Files:**
- Modify: `frontend/src/features/jobs/RequestDetail.tsx`
- Test: `frontend/src/features/jobs/RequestDetail.test.tsx` (기존, 확장)

**Interfaces:**
- Consumes: 기존 `durationText(from?, to?)`(RequestDetail.tsx 파일 내 헬퍼), 요청 상세 응답의 `transitions`(이미 실려 있음 — 백엔드 변경 없음).
- Produces: 요약 dl에 「큐 대기」 행 — `created_at` → 첫 비-Pending 전이 `at`의 차. 전이가 아직 없으면 `—`.

설계 §2.3: 큐 대기는 `state_transitions`의 첫 실행 전이 시각 − `created_at`으로 **요청 상세에서만** 유도한다. 전역 집계는 `(entity_kind, entity_id, id)` 인덱스로 커버되지 않는 풀스캔이라 금지 — 대시보드 통계에 넣지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/features/jobs/RequestDetail.test.tsx` 끝에 추가 (기존 `REQUEST`/`JOBS`/`renderAt` 사용):

```tsx
test("큐 대기를 첫 비-Pending 전이에서 유도해 보여준다", async () => {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({
      ...REQUEST,
      transitions: [
        { from_state: null, to_state: "Pending", at: "2026-08-05T00:00:00Z" },
        { from_state: "Pending", to_state: "Planned", at: "2026-08-05T00:01:30Z" },
        { from_state: "Planned", to_state: "Failed",
          reason_code: "no_eligible_nodes", at: "2026-08-05T00:02:00Z" },
      ],
    })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  expect(await screen.findByText("큐 대기")).toBeInTheDocument();
  expect(screen.getByText("1분 30초")).toBeInTheDocument();
});

test("실행 전이가 아직 없으면 큐 대기는 —", async () => {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({
      ...REQUEST, state: "Pending",
      transitions: [
        { from_state: null, to_state: "Pending", at: "2026-08-05T00:00:00Z" },
      ],
    })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([])),
  );
  renderAt();
  const dt = await screen.findByText("큐 대기");
  expect(dt.nextElementSibling).toHaveTextContent("—");
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/features/jobs/RequestDetail.test.tsx`
Expected: 신규 2건 FAIL — 「큐 대기」 행이 없다

- [ ] **Step 3: 행을 더한다**

`frontend/src/features/jobs/RequestDetail.tsx` — `transitions` 정규화 아래에:

```tsx
  // 큐 대기(슬라이스 14 설계 §2.3): runs 테이블이 죽어 있어 첫 비-Pending 전이
  // (planner가 집어간 시각) − created_at으로 유도한다. 전역 집계는 전기간
  // state_transitions 풀스캔이라 금지 -- 이 화면에서만 계산한다.
  const firstPickup = transitions.find((t) => t.to_state !== "Pending");
```

요약 dl의 「수행시간」 행 위에:

```tsx
          <dt className="text-muted">큐 대기</dt>
          <dd>{durationText(data.created_at, firstPickup?.at)}</dd>
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run` 그리고 `npx tsc -b`
Expected: 전체 PASS, 타입 에러 0

- [ ] **Step 5: 백엔드 전체 스위트 최종 확인**

Run: `.venv/bin/python -m pytest -q` (포그라운드, timeout 400000ms)
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/features/jobs
git commit -m "feat(portal): 요청 상세에 큐 대기 유도 표시"
```

---

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 태스크 |
|---|---|
| §2.1 손수 SVG Sparkline/BarChart, viewBox·반응형·값→path 테스트 | Task 5 (사용은 7) |
| §2.2 typed 컬럼 SQL GROUP BY + JSON blob 앱측 파싱 | Task 1 |
| §2.3 files/bytes 컬럼·set_artifact 파이프라인 (runner 파싱 범위 밖 명시) | Task 1 + Global Constraints |
| §2.3 큐 대기 — 요청 상세에서만 유도, 전역 집계 금지 | Task 8 + Global Constraints |
| §2.4 인프라 뷰: observe replica/판정 통과, 기존 엔드포인트 재사용, RBAC 불변 | Task 4 (소비는 6) |
| §2.5 `events_for_request` 얇은 admin 래퍼 | Task 3 |
| §3 `metrics/nodes` (기간 클램프·fail-soft·백엔드 차분) | Task 1·2·3 |
| §3 `metrics/jobs` (총계·처리량 버킷·분해·사유 상위·수행시간 분포) | Task 1·2·3 |
| §3 `metrics/infra` | Task 4 |
| §3 `requests/{id}/events` | Task 3 |
| §3 네트워크 차분·리셋 시 null | Task 2 (검증은 3) |
| §4.1 개요 — KPI를 집계로 전환 + 컴포넌트 3종 한 줄씩 | Task 6 |
| §4.2 노드/리소스 — 기간 선택, 노드별 Sparkline(load/메모리/디스크/네트워크), 드릴다운, 신선도·증거 스냅샷 | Task 7 |
| §4.3 잡 통계 — 처리량 막대, 성공/실패율, 수행시간 히스토그램, 3분해 표, 실패 사유(`reasonText`), files/bytes "—" | Task 7 |
| §4 규약 — admin 전용, 방어적 정규화, 폴링 5s(개요·인프라만), 쿼리 키 격리, `components/ui/` 배치 | Task 5·6·7 + Global Constraints |
| §5 하지 않는 것 (runner 파싱, Volcano 큐, runs 부활, 전역 큐 대기, Prometheus, 알림) | Global Constraints에 명시 — 어떤 태스크도 건드리지 않음 |
| §6 실증(테스트베드) | 플랜 실행 후 별도 수행 (슬라이스 12·13과 동일 관례) |

**2. 플레이스홀더 점검** — "적절히 처리한다"/"TBD"/코드 없는 "테스트를 작성한다" 없음. 모든 코드 단계에 실제 코드가 있다. 의도적으로 구현자 판단에 맡긴 지점은 Task 7 Step 5의 `Breakdown` `nameOf` 타이핑 방식 하나이며, 합격 기준(`npx tsc -b` 초록)을 명시했다.

**3. 타입 일관성** — `MetricsRepository.node_series`/`job_stats`의 반환 키(Task 1)는 Task 3의 라우트가 그대로 소비하고(`duration_seconds`→`duration_histogram` 변환 포함), Task 2의 `build_node_points` 포인트 키(`at/load1/load5/load15/mem_used_pct/net_rx_bps/net_tx_bps/disks`)는 Task 6의 `NodeMetricPoint` 타입 및 Task 7의 렌더 코드와 1:1이다. Task 4의 infra 응답 키는 `InfraComponent`(Task 6)와 1:1. `useNodeMetrics/useJobMetrics/useInfraMetrics`(Task 6)를 Task 7이 같은 시그니처로 쓰고, `Sparkline`/`BarChart` props(Task 5)를 Task 7이 그대로 쓴다. 테스트 픽스처의 admin 헤더·`client`/`db` 공유·`_repos` 패턴은 실측 고정값 표에서 확인한 실제 이름이다.

**알려진 위험:**
- **설계 §2.4의 문구 중의성**: §2.4는 "`releases/targets`를 확장한다"라고 읽히지만 §3 API 표는 별도 `GET /api/admin/metrics/infra`를 명시한다. 이 플랜은 §3(API 표)을 따랐다 — targets는 레지스트리 조회 때문에 폴링 금지 규약(useReleases.ts 주석)이 걸려 있어, §4의 "개요·인프라 5s 폴링"과 양립하려면 레지스트리 없는 별도 엔드포인트가 유일하게 정합적이다. 기존 targets 소비자·테스트도 무변경으로 남는다.
- `data_jobs.created_at`에 인덱스가 없어 `job_stats`의 GROUP BY들은 테이블 스캔이다. 현 규모(테스트베드 43행)에서는 무해하지만, 잡이 수십만 건이 되면 `(created_at)` 인덱스 추가가 필요하다 — 설계가 명시하지 않아 이번에는 넣지 않았다.
- `by_storage`의 `COALESCE(storage_name, destination_storage)`는 sync 잡을 도착지 스토리지로 세는 해석이다(설계 §3 "스토리지별 분해"의 기준 미명시). 소스 기준 분해가 필요해지면 컬럼만 바꾸면 된다.
- KPI 의미 변화: 개요 타일이 "요청 50건 즉석 계산"에서 "창 내 잡 집계"로 바뀌므로 숫자가 기존 화면과 다르게 보일 수 있다 — 설계 §4.1이 명시적으로 요구한 전환이다.
- `useJobMetrics`의 5s 폴링이 vitest에서 추가 fetch를 만들 수 있으나, 기존 스위트(`useNodes` 5s)와 같은 조건이라 새로운 위험은 아니다.
