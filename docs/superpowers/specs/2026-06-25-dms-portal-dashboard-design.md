# DMS Portal — 종합 운영 대시보드 설계

- 날짜: 2026-06-25
- 상태: 승인됨 (구현 대기)
- 범위: Portal 운영자 콘솔에 **읽기 전용** 종합 대시보드 탭 추가 + DMS에 read-only 카운트 엔드포인트 1개 추가
- 접근: **접근 2** — 기존 DMS `operations/*` API 최대 활용 + 정확한 잡/큐 카운트를 위한 작은 신규 엔드포인트 1개

## 1. 목표 / 비목표

**목표 (v1):**
- DMS 운영 상태를 한 화면에서 모니터링: **스케줄러 상태 · 큐/작업 현황 · worker node 상태 · 데이터 잡 배치 현황 · 조치 필요 이슈**.
- 운영자 콘솔의 새 탭. 자동 폴링으로 준실시간 갱신.
- 포탈 규약 준수: BFF(`dms_client.py`)만 DMS와 통신, 프론트는 BFF만 호출.

**비목표 (이번 단계 제외, 후속 단계로 분리):**
- **노드 OS 자원 메트릭(CPU/메모리/디스크/load)** — DMS agent 프로브 + AgentReport 스키마 + DaemonSet 확장이 필요. v1은 **DMS 레벨 노드 건강**(Fresh/Stale, mounts/tools/credentials/role)만.
- **스케줄러 제어 액션(drain/maintenance/resume)** — `control-state:*` POST 액션. v1은 읽기 전용, 액션은 후속 단계.
- 시계열/throughput 메트릭, 알림(alerting), 그래프 차트.

## 2. 아키텍처

```
[브라우저 SPA: interfaces/operator/dashboard/]
        │  /api/operator/dashboard/*
        ▼
[Portal BFF: routers/dashboard.py]
        │  병렬 fan-in (summary) / 프록시 (드릴다운)
        ▼
[dms_client.py]  →  DMS  GET /api/v1/operations/*
```

- **신규 DB·env 없음.** 데이터 백업과 달리 대시보드는 순수 읽기 프록시/집계라 포탈 영속 저장소가 필요 없다.
- BFF는 기존 `get_dms_client` 의존성과 `DmsClient`를 재사용한다. DMS 호출 메서드만 추가.
- 운영자 role 게이트(`require_role(ROLE_OPERATOR)`)로 전체 라우터 보호.

## 3. 패널 ↔ 데이터 소스 매핑

`★` 표시 1개만 신규 DMS 엔드포인트, 나머지는 모두 기존.

| # | 패널 | DMS 엔드포인트 | 핵심 필드 |
|---|------|----------------|-----------|
| 1 | 스케줄러 카드 | `GET /operations/control-state` | maintenance_mode · drain_mode · scheduling_blocked · reason · changed_at |
| 2 | 큐/작업 카드 | `GET /operations/work-summary` | plans{total_active,by_status,by_worker_role} · runs{total_active,by_state,by_worker_id,lease_expiring_soon,stale_or_recovery} · requests{action_required} |
| 3 | 노드 카드 + 워커 노드 테이블 | `GET /operations/agent-reports?freshness=` | cluster_name · node_name · worker_role · freshness_status(Fresh/Stale) · capability_summary{mounts,tools,csi_drivers,credential_count} · reported_at |
| 4 | 데이터 잡 카드 | ★`GET /operations/data-jobs/summary` | by_state · by_operation · total · active_total |
| 5 | 스케줄러 활동 테이블 | `GET /operations/runs/active` + `GET /operations/runs/stale` | run_id · worker_id · worker_role · state · lease_seconds_remaining · lease_expiring_soon · resource_key |
| 6 | 데이터 잡 테이블 | `GET /operations/data-jobs?state=&operation=&storage_name=&limit=` | job_id · operation · storage_name · state · selected_tool · updated_at |
| 7 | 조치 필요 패널 | `GET /operations/action-required` | issue_type + 컨텍스트 필드 |

라이브 확인(2026-06-25, 테스트베드): work-summary(runs.stale_or_recovery=72, requests.action_required=112), control-state(전부 false=정상), agent-reports(100건; 예: dms-w2/DM/Fresh, mounts=[cephfs-dms,cephfs-third,cephfs-secondary], tools=[dsync,nsync,drm,dscan,kubectl]).

## 4. 신규 DMS 엔드포인트 (★ 유일한 백엔드 변경)

데이터 잡을 **상태/operation별로 정확히 카운트**하기 위한 read-only 집계. 기존 `data-jobs` 리스트는 `limit`(최대 1000)에 걸려 대규모에서 카운트가 부정확하므로, 서버측 `GROUP BY` 집계를 추가한다.

**HTTP**
```
GET /api/v1/operations/data-jobs/summary
  ?storage_name=<opt>&operation=<opt>
→ 200
{
  "total": 1234,
  "active_total": 11,                 // 비종료(non-terminal) 상태 합
  "by_state": {                       // DataJobState 별 카운트
    "Pending": 5, "PreflightRunning": 0, "PreviewRunning": 0,
    "ConfirmPending": 1, "Running": 2, "Succeeded": 1200, "Failed": 26, ...
  },
  "by_operation": { "data.scan": 800, "data.sync": 400, "data.rm": 34 }
}
```

**구현 위치 (기존 패턴 준수):**
- `repositories/data_jobs.py` — `data_job_summary(storage_name=None, operation=None) -> dict`:
  `SELECT state, COUNT(*) ... [WHERE storage_name=? AND operation=?] GROUP BY state` 와 `... GROUP BY operation` 두 쿼리. SQLite/Postgres 모두 호환(다른 리포 쿼리와 동일 `Database` 래퍼 사용).
- `query.py` — 서비스 메서드로 노출(`active_total`은 `domain.py`의 비종료 DataJobState 집합으로 계산).
- `api/routers/operations.py` — 라우트 추가. **주의:** `/data-jobs/{job_id}` 보다 **먼저** 선언해 `summary`가 path-param으로 매칭되지 않게 한다(FastAPI 선언 순서 매칭). 충돌이 우려되면 경로를 `/operations/data-job-summary`로 대체 가능.
- 테스트 — `tests/`에 SQLite 기반 카운트 검증(상태/operation 혼합 시드 → 집계 일치, 필터 동작).

부수 변경 없음(쓰기/마이그레이션 없음). 인증/authz는 다른 operations 엔드포인트와 동일.

## 5. BFF (Portal) 설계

신규 `src/portal/backend/routers/dashboard.py`, prefix `/api/operator/dashboard`, 전체 `require_role(ROLE_OPERATOR)`.

| 라우트 | 동작 | DMS 호출 |
|--------|------|----------|
| `GET /summary` | 상단 카드용 **병렬 fan-in** 단일 payload | control-state · work-summary · agent-reports(카운트 집계) · data-jobs/summary 를 `asyncio.gather`로 동시 호출 |
| `GET /nodes?freshness=` | 워커 노드 테이블 | agent-reports 프록시(필터 전달) |
| `GET /runs` | 스케줄러 활동 테이블 | runs/active + runs/stale (BFF에서 합쳐 반환) |
| `GET /jobs?state=&operation=&storage_name=&limit=` | 데이터 잡 테이블 | data-jobs 프록시(필터 전달) |
| `GET /attention` | 조치 필요 패널 | action-required 프록시 |

- `/summary`의 fan-in은 일부 DMS 호출 실패 시 **부분 실패 허용**: 실패한 섹션은 `null` + `error` 표기, 나머지는 정상 표시(대시보드가 한 패널 오류로 전체가 깨지지 않도록).
- `dms_client.py`에 read-only 메서드 추가: `get_control_state`, `get_work_summary`, `list_agent_reports`, `get_data_job_summary`, `list_active_runs`, `list_stale_runs`, `list_action_required`, `list_data_jobs`(데이터 백업용 `list_data_jobs`와 시그니처 정리해 재사용). 모두 `actor` 전달.
- 인증: 기존 테스트베드 프로파일(bearer + `x-dms-actor`) 그대로. 대시보드는 privileged 불필요(일반 operator actor).

## 6. 프론트엔드 설계

- `interfaces/operator/dashboard/` 신규. `OperatorApp.tsx` 네비에 "종합 대시보드" 탭 추가(Section 타입 확장).
- 컴포넌트 분리(각 단일 책임):
  - `Dashboard.tsx` — 레이아웃 + 자동폴링 훅(기본 ~7초, 일시정지/수동새로고침). `/summary`를 폴링하고 드릴다운 테이블은 각자 로드.
  - `StatusCards.tsx` — 스케줄러·큐·노드·데이터잡 카드(4개). 색상은 기존 `san-*`/`ok-num`/`err-num` 재사용.
  - `NodesTable.tsx` — agent-reports(freshness 필터, mounts/tools 요약 셀).
  - `RunsTable.tsx` — runs/active+stale(lease 남음, stale 강조).
  - `JobsTable.tsx` — data-jobs(state/operation/storage 필터, 페이지네이션).
  - `AttentionPanel.tsx` — action-required(issue_type별 뱃지).
  - `helpers.ts` — 상태→라벨/색 매핑, 시간/마운트 요약 포매터.
- `api.ts`에 `operatorApi.dashboard = { summary, nodes, runs, jobs, attention }` + 타입 추가.
- 모바일: 기존 반응형 패턴(`data-label` 카드화, 단일 컬럼) 재사용.

## 7. 자동 새로고침 / UX 기본값

- `/summary` 카드: 자동 폴링 7초(일시정지 토글 + 수동 새로고침). 탭 비활성 시 폴링 정지(가시성 기반) 고려.
- 드릴다운 테이블: 수동 새로고침 + 필터 변경 시 재조회. 잡 테이블은 페이지네이션(예: 100/페이지, 더 보기).
- 상단에 마지막 갱신 시각 표시.

## 8. 테스트 전략

- **DMS**: `data_job_summary` 리포 쿼리 단위 테스트(SQLite 시드 → by_state/by_operation/active_total 일치, storage/operation 필터, 빈 결과). 라우트 스모크(200 + 스키마).
- **BFF**: `/summary` fan-in이 DMS 클라 stub로 합성됨 + 부분 실패 시 섹션 null 처리. 각 프록시 라우트 role 게이트(operator 200 / user 403).
- **프론트**: `npm run build`(tsc+vite) 통과 = 타입/번들 검증. Playwright로 라이브 스모크(탭 진입, 카드/테이블 렌더, 폴링 갱신).
- **라이브 검증**: 테스트베드에 실제 잡/노드/run 존재 → 카운트·노드 Fresh/Stale·action-required(112)·stale(72)가 화면에 반영되는지 확인.

## 9. 단계화 (Phasing)

- **v1 (이번 스펙)**: 읽기 전용, DMS 레벨 노드 건강, 7개 패널, ★data-jobs/summary 1개 추가.
- **후속 A — OS 메트릭**: DMS agent 프로브에 CPU/메모리/디스크 수집 추가 → AgentReport 스키마 확장 → 노드 카드/테이블에 메트릭 열. (별도 스펙: agent + 스키마 + DaemonSet 변경.)
- **후속 B — 스케줄러 제어**: control-state begin-drain/enter-maintenance/resume 버튼(확인 다이얼로그 + 감사). 읽기 대시보드 위에 액션 레이어.
- **후속 C(선택)**: queue depth by operation_kind, worker-summary, MPI/Volcano 실시간 pod 상태 enumerate.

## 10. 리스크 / 메모

- **경로 충돌**: `/data-jobs/summary` vs `/data-jobs/{job_id}` — 선언 순서로 해결(§4). 안전책으로 `/data-job-summary` 대안.
- **카운트 범위**: 종료(Succeeded 등) 상태 all-time 카운트는 수가 클 수 있으나 COUNT 쿼리는 저렴. 필요 시 후속에서 `since` 윈도우 추가.
- **work-summary는 RM/DM 라이프사이클(plans/runs)** 집계라 DM 데이터 잡(scan/sync/rm)과는 다른 축 — 그래서 데이터 잡은 별도 ★summary로 본다. 두 축을 카드에서 명확히 라벨링.
- 포탈 규약: 이번엔 사용자가 DMS 추가를 허가했으므로 ★1개를 DMS에 추가하되, 표면은 read-only 집계로 최소화.
