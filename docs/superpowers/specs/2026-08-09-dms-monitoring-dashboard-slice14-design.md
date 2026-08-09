# 슬라이스 14 — 모니터링 대시보드 설계

상위 스펙 §9(모니터링)를 구현한다. 별도 모니터링 스택(Prometheus/Grafana)을 세우지
않고, **포탈 대시보드가 그 역할을 대신한다.** 데이터 출처는 DB(에이전트 리포트 이력
포함)와 k8s API다.

지금 `frontend/src/features/dashboard/` 는 현재 요청 목록에서 즉석 계산한 카운트 타일 +
노드 신선도 목록의 **스텁**이다 — 이력도, 시계열도, 차트도 없다.

---

## 1. 실측으로 확인한 전제

| 사실 | 확인 |
|---|---|
| `agent_reports` 가 **이미 시계열** — `(node_name, reported_at)` 인덱스, 노드당 ~8200행/6일 | 실 DB |
| 리포트 payload 의 `os`: `load1/5/15`, `memory_total_kb`/`available_kb`, `disks[].{used,total}_bytes`, `network_rx/tx_bytes` | `agent/probes.py:92` |
| **CPU 사용률·코어 수는 없다** — "CPU"는 load average 뿐 | probes |
| 네트워크는 **부팅 이후 누적 카운터** — throughput 은 인접 샘플 차분 | probes |
| 보존 30일(기본), retention 이 오래된 `agent_reports` 삭제 | `config.py:102` |
| 기존 이력 조회는 **limit 만** — 기간 필터가 없다 | `agents.py:45` |
| `data_jobs`: tool/storage_name/state/reason_code/created_at/updated_at 로 대부분 통계 가능 | 실 DB (43행, 상태·도구 분포 고름) |
| **`runs` 테이블(started_at/finished_at)은 선언만 되고 아무도 안 쓴다** — 큐 대기·실행시간의 자연스러운 집은 죽어 있다 | grep: `INSERT INTO runs` 0건 |
| **`result_summary` 에 files/bytes 가 없다** — `{"returncode": 0}` 만. runner 가 mpifileutils 출력을 파싱하지 않는다 | 실 DB |
| **차트 라이브러리 없음** — `<svg>`/`<canvas>` 도 저장소에 0건 | `package.json` |
| 인프라 사실 다수가 **이미 엔드포인트로 노출** (releases/targets, builds, releases 이력, policies) | 슬라이스 11~13 |
| `events_for_request` 는 구현됐으나 **라우팅 안 됨** | `observability.py` |

---

## 2. 핵심 결정

### 2.1 시계열은 **손수 SVG** 로 그린다

새 의존성을 추가하지 않는다. 차트 라이브러리 하나가 수십 개 트랜지티브 의존성을 끌고
오는데, 이 대시보드가 필요로 하는 것은 **선 그래프와 막대**뿐이다. 재사용 가능한
`Sparkline`(선)과 `BarChart`(막대) 컴포넌트를 `components/ui/` 에 손수 SVG 로 만든다 —
`viewBox` 스케일링, 반응형, 라이트/다크 모두. 값 배열 + 라벨을 받는 순수 표현 컴포넌트라
단위 테스트가 쉽다.

### 2.2 집계는 **SQL GROUP BY(가능한 것) + 앱측 JSON 파싱(불가피한 것)** 으로 나눈다

`data_jobs` 의 typed 컬럼(state/tool/storage_name/reason_code/시각)은 **SQL 로 집계**한다 —
성공/실패율, 기간별 처리량, 도구·스토리지·사용자별 분해, 실패 사유 상위, 전체 수행시간.

`agent_reports.report`(JSON blob)의 수치 시계열은 **앱측에서 `load_json` 후 파싱**한다 —
컬럼이 아니라 blob 이고 dual-dialect(SQLite/PostgreSQL)라 `json_extract`/JSON 함수에
의존하면 이식성이 깨진다. 조사가 확인: 순수 SQL AVG/SUM 이 불가능하다.

### 2.3 files/bytes 와 큐 대기시간 — **얻을 수 있는 것만, 정직하게**

두 지표는 지금 재료가 없다. 각각을 이렇게 다룬다:

- **files/bytes**: `data_jobs` 에 `files_count`/`bytes_count` 컬럼을 더하고, `set_artifact`
  시점에 `result_summary` 에 그 키가 있으면 채운다(없으면 NULL). **runner 가 아직 그 값을
  안 쓰므로 지금은 대부분 NULL** — 대시보드는 NULL 을 "—"로 우아하게 생략하고, runner 가
  나중에 파싱을 추가하면 자동으로 채워진다. 컬럼과 파이프라인을 지금 놓아 두는 것이,
  나중에 마이그레이션 없이 켜지게 하는 값싼 준비다. **runner 수정은 이 슬라이스 범위 밖**
  (별도 작업) — 그 사실을 명시한다.

- **큐 대기시간**: `runs` 테이블은 죽었고 되살리는 것은 큰 변경이다. 대신 큐 대기 =
  `state_transitions` 의 첫 실행 전이 시각 − `created_at` 으로 **유도**한다. 이 인덱스가
  `(entity_kind, entity_id, id)` 라 요청별 조회는 싸지만 전기간 집계는 풀스캔이다 —
  그래서 **큐 대기는 "요청 상세"의 값으로만 노출하고, 대시보드의 전역 집계 통계에는
  넣지 않는다**(전역은 전체 수행시간 분포로 충분하다). 잘못된 지표를 위해 스키마를
  뒤흔드느니 정확한 부분집합을 낸다.

### 2.4 인프라 뷰는 **기존 엔드포인트를 재사용**한다

슬라이스 11~13 이 이미 낸 것을 다시 만들지 않는다: 빌드 이력(`GET /api/admin/builds`),
릴리스 이력·현재(`GET /api/admin/releases`), 컴포넌트 현재 이미지(`releases/targets`),
정책의 큐/우선순위(`GET /api/admin/policies`). 대시보드 프론트가 이것들을 **소비만** 한다.

**딱 하나 새로 노출**: `releases/targets` 가 `observe()` 로 이미 읽는 replica/ready 카운트와
`assess_*` 판정을 **버리고 이미지만 준다**. 그것을 통과시키도록 확장한다 — 컴포넌트별
"이미지 + N/N ready + 롤아웃 판정"이 인프라 뷰의 핵심이다. 이미 배선된 `observe()` 심과
슬라이스 13에서 부여한 apps get 권한을 재사용하므로 RBAC 변경이 없다.

**범위 밖**: 라이브 Volcano 큐 점유율(코드·RBAC 없음, CRD 읽기 + Role 변경 필요),
"전체 워크로드 나열"(RBAC 이 세 워크로드로 `resourceNames` 좁혀져 있다 — 설계대로).

### 2.5 요청 상관(correlation) — 이미 만든 것을 라우팅한다

`events_for_request`(슬라이스 12)가 구현됐으나 요청 상세 응답 안에만 있다. 대시보드의
진단 드릴다운을 위해 얇은 `GET /api/admin/requests/{id}/events` 래퍼 하나를 추가한다 —
새 로직 없이 기존 저장소 메서드를 admin 게이트 뒤에 노출.

---

## 3. 데이터 API (백엔드)

전부 admin 전용, 읽기 전용(뮤테이션 없음 → 감사 로그 없음).

| 메서드 | 경로 | 내용 |
|---|---|---|
| GET | `/api/admin/metrics/nodes?window=<h>` | 노드별 시계열: reported_at 축, load1/5/15·mem used%·disk used%·net throughput. 기간은 시간 단위, 보존(30일=720h) 상한 |
| GET | `/api/admin/metrics/jobs?window=<h>` | 잡 통계 집계: 총계·성공/실패율, 기간 버킷별 처리량, 도구·스토리지·사용자별 분해, 실패 사유 상위, 수행시간 분포(버킷) |
| GET | `/api/admin/metrics/infra` | 컴포넌트 3종의 이미지 + ready 카운트 + 롤아웃 판정 (`observe` 재사용) |
| GET | `/api/admin/requests/{id}/events` | 요청 진단 이벤트 (`events_for_request` 래퍼) |

**모든 읽기는 fail-soft.** 노드 메트릭 파싱이 한 리포트에서 깨져도 그 샘플만 건너뛰고
나머지 시리즈는 산다. `observe` 가 실패하면 그 컴포넌트만 `null` 로 강등(슬라이스 13 규약).

### 저장소

- `MetricsRepository`(신규): `node_series(node, start, end)`, `job_stats(start, end)`.
  - `node_series`: `SELECT report, reported_at FROM agent_reports WHERE node_name=:n AND reported_at BETWEEN :s AND :e ORDER BY reported_at` — 복합 인덱스가 커버. 앱측에서 JSON 파싱해 시리즈로.
  - `job_stats`: `data_jobs` 에 대한 GROUP BY 여러 개(상태, 도구, 스토리지, 사유, 시각 버킷). requester 는 `requests` 조인.
- `data_jobs` 에 `files_count`/`bytes_count` INTEGER 컬럼 추가(CREATE TABLE + `_ensure_columns`), `set_artifact` 가 summary 에서 채움.

### 네트워크 throughput — 차분

네트워크 카운터는 누적이라 throughput = `(rx[i] - rx[i-1]) / (t[i] - t[i-1])`. **카운터
리셋(리부팅)** 을 다뤄야 한다: 감소하면 그 구간은 `null`(리셋으로 간주, 음수 throughput 을
그리지 않는다). 이 차분은 `node_series` 를 소비하는 곳(백엔드 집계 또는 프론트)에서 하되,
**백엔드에서 계산해 프론트에 throughput 을 바로 준다** — 프론트가 카운터 의미를 몰라도
되게.

---

## 4. 포탈 화면 — 대시보드 확장

기존 `/admin/dashboard` 를 확장한다(새 라우트를 만들지 않는다 — 이미 사이드바에 "대시보드"가
있다). 세 개의 탭 또는 세로 섹션:

### 4.1 개요 (기존 유지 + 강화)
KPI 타일(실행/대기/성공/실패)은 유지하되, **잡 통계 API 의 집계**로 바꾼다(현재는 요청
목록에서 즉석 계산 — 페이지네이션에 취약). 컴포넌트 3종의 이미지·ready·판정을 한 줄씩.

### 4.2 노드/리소스 (신규 — 시계열)
기간 선택(1h/6h/24h/7d, 30일 상한). 노드별로 `Sparkline`: load, 메모리 사용%, 디스크
사용%(스토리지별), 네트워크 throughput. 노드 드릴다운. 에이전트 신선도(마지막 리포트 나이),
마운트/도구/identity 증거 상태를 스냅샷 패널로.

### 4.3 잡 통계 (신규)
기간 선택. 처리량 막대(기간 버킷), 성공/실패율, 수행시간 분포 히스토그램, 도구별·스토리지별·
사용자별 분해 표, 실패 사유 상위 표(`reasonText()` 로 한글화). files/bytes 는 있으면 표시,
없으면 "—".

### 규약 (슬라이스 12·13 확립)
- admin 전용(`RequireRole role="admin"`), 사이드바 링크는 이미 있음.
- 사유 코드는 `reasonText()`, 새 코드 있으면 `REASON_MESSAGES`+`reasonCodes.json` 양쪽.
- 백엔드 응답 방어적 정규화(`Array.isArray`). 빈/누락 시리즈에 강건.
- 진행형이 아니므로 폴링은 개요·인프라만 짧게(5s), 시계열은 기간 재조회 위주.
- 쿼리 키를 기존 `["nodes"]` 와 겹치지 않게(현재 dashboard 가 그 키를 쓴다).
- 새 SVG 컴포넌트는 `components/ui/`, 테스트는 값→경로(path d) 단언.

---

## 5. 이 슬라이스에서 하지 않는 것

- runner 의 mpifileutils 출력 파싱(files/bytes 실제 채움) — 컬럼·파이프라인만 놓고 별도 작업.
- 라이브 Volcano 큐 점유율(CRD 읽기 + RBAC).
- `runs` 테이블 되살리기 — 큐 대기는 요청 상세에서 state_transitions 로 유도.
- 전역 큐 대기 집계(풀스캔).
- Prometheus/Grafana 배포.
- 알림/경보(대시보드는 표시만).

---

## 6. 실증 (테스트베드)

실 클러스터에 노드당 8천여 개 리포트, data_jobs 43건(상태·도구 분포 고름)이 이미 있다.

1. `GET /api/admin/metrics/nodes?window=24` 가 5노드의 load/mem/disk/net 시계열을
   reported_at 축으로 주는지. 네트워크가 차분된 throughput 인지(누적 아님).
2. 기간 상한: `window=1000`(>720h)이 30일로 클램프되는지.
3. `GET /api/admin/metrics/jobs` 가 상태 분포(성공 20·실패 10·거절 9·취소 3·만료 1)와
   도구 분포(dscan 23·dsync 11·nsync 8·drm 1)를 실제 집계로 주는지.
4. 실패 사유 상위가 `reason_code` 로 집계되는지.
5. `GET /api/admin/metrics/infra` 가 세 컴포넌트의 d23 이미지 + 1/1 ready + 판정을 주는지.
6. 파싱 불가한 리포트가 섞여도 시리즈가 죽지 않는지(fail-soft) — 하나를 손상시켜 확인.
7. 포탈 대시보드가 Sparkline/BarChart 를 실제로 그리는지(브라우저 아닌 API+렌더 확인).
8. files/bytes 가 NULL 인 현재 데이터에서 "—"로 우아하게 나오는지.
