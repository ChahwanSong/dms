# DMS 백엔드 이슈: 읽기 API 완전성 작업 중 드러난 스케일/운영 리스크

- 작성일: 2026-06-30
- 상태: **전부 구현·배포·라이브 스케일 검증 완료 (2026-06-30, ultracode 세션)** — §7 참조.
- 컨텍스트: "포탈에서 DMS 기본 limit 때문에 데이터가 조용히 잘리는 경우"를 없애는 작업
  (A: DMS 읽기 API에 pagination/정확 COUNT/`latest_per_node` 추가, B: 포탈이 이를 사용 +
  잘림 배지)을 구현·배포하던 중, **실무에서 재현될 수 있는 운영 리스크**가 라이브에서
  드러났다. 본 문서는 발생 이슈를 기록하고, 단순 코드 실수가 아닌 항목은 구현 레벨
  해결책을 제시한다.
- 배포 상태(작업 종료 시점): dms-api=`pkg-01:5000/dms:node-latest-fast`(Option A),
  포탈=`dms-portal:v108`. 커밋 `a9bef7b`(DMS)·`8ddb1d9`(포탈). 라이브 정상(아래 §5).

---

## 0. 한 줄 요약

`agent_reports`가 **노드 10개에 20만 행**(이력 무한 누적)이라 "노드별 최신 1행"을 구하는
초기 구현(상관 anti-join)이 **34~58초 + 임시파일로 PG 디스크 풀**을 유발했고, 그 느린 쿼리가
**7초 폴링 × 커넥션 풀 부재 × 롤아웃 surge × crash-loop**와 맞물려 **PostgreSQL
`max_connections`(100)를 소진 → dms-api 전 파드 다운**(전면 장애)까지 갔다. 쿼리는
최적화(Option A, 126ms)로 해결했고 장애는 복구했으나, **근본 운영 리스크 3건**(커넥션 풀
부재, agent_reports 무한 누적, statement_timeout 부재)은 별도 구현 과제로 남는다.

---

## 1. 발생 이슈 — 실무 운영 리스크 (구현 레벨 해결책 제시)

### ISSUE-1 (CRITICAL) — DB 커넥션 풀 부재 → 커넥션 고갈 → 전면 장애

**무엇이 일어났나.** 작업 중 dms-api 2개 replica가 모두 `0/1 Running`(liveness/readiness
실패)로 빠지고 `deployment dms-api 0/2 AVAILABLE`(전면 다운). 파드 로그:

```
psycopg.OperationalError: connection failed: ... 10.10.10.30:5432 failed:
FATAL: remaining connection slots are reserved for roles with the SUPERUSER attribute
```

`max_connections=100`이 소진되어 비-superuser(dms_app)가 새 연결을 못 얻는 상태. sanity-
reconciler도 같은 이유로 7회 재시작. **dms-api를 replicas=0으로 내리자 즉시 backends=31로
떨어지고 dms_app 연결 성공** → 원인은 누수가 아니라 **연결 폭주**였다.

**근본 원인(코드 레벨).** `src/dms/db.py`의 `Database.connect()`는 호출마다
`psycopg.connect(self.url)`로 **새 연결을 열고 닫는다(커넥션 풀 없음)**.

```python
# src/dms/db.py (connect)
connection = psycopg.connect(self.url, row_factory=dict_row)
```

평상시엔 op가 짧아 연결이 금방 반납되지만(라이브 정상 시 backends≈7~8), 다음이 겹치면
선형으로 누적되어 100을 넘긴다:
- 느린 쿼리(ISSUE-2의 34~58초)가 연결을 그 시간만큼 점유,
- 대시보드가 `/summary`에서 ~6개 DMS 호출을 **병렬**로(각각 새 연결) **7초마다 폴링**,
- 롤아웃 중 old+new 파드 surge로 프로세스 수 증가,
- 연결 실패한 파드가 liveness 실패로 죽고 재시작하며 **연결 재시도 폭풍**.

→ **느린 쿼리 한 종류가 전체 DB 연결을 고갈시켜 API를 죽이는** 단일 장애점. 실무
(~1만 잡/일, 운영자 다수 동시 접속, 정기 롤아웃)에서 충분히 재현 가능.

**구현 레벨 해결책 (권장 우선순위순).**
1. **앱 레벨 커넥션 풀 도입** — `psycopg_pool.ConnectionPool`(동기) 또는
   `AsyncConnectionPool`을 `Database`에 두고 프로세스당 `max_size`를 작게 고정(예: api 8,
   worker 4). 그러면 (파드 수 × pool max_size) < (`max_connections` − reserved)가 보장되어
   폭주가 구조적으로 불가능. `db.connect()`를 `pool.connection()`으로 교체(컨텍스트 동일).
   *주의*: SQLite 포터블 경로(`db.py`)는 풀 비적용 분기 유지.
2. **(또는/추가) PgBouncer** 를 PG 앞에 transaction pooling 모드로 배치 — 앱 수정 최소.
   다만 prepared statement/세션 상태 사용처 점검 필요.
3. **방어선**: PG 역할(dms_app)에 `idle_in_transaction_session_timeout`(예: 30s),
   배포 readiness probe가 DB 미연결 시 빠르게 fail-fast 하도록(현재 liveness가 슬로우 쿼리로
   막혀 파드가 죽는 것을 방지) `/healthz`를 DB-독립적 가벼운 체크로.
4. **용량 산정 문서화**: `max_connections` vs (모든 컴포넌트 파드 수 × 풀 크기 + 여유) 표를
   `install/`에 추가. 현재 `max_connections=100`은 컴포넌트 수 대비 여유가 적다.

---

### ISSUE-2 (HIGH) — `agent_reports` 무한 누적 → "노드별 최신" 및 메트릭 스캔 고비용

**무엇이 일어났나.** `agent_reports` = **204,107행 / 고유 (cluster,node,role) 10개**
(노드당 ~2만 행, 이력 정리 없음). 노드 건강은 "각 노드의 최신 보고 1행"이 필요한데, 초기
구현(`NOT EXISTS` 상관 anti-join)이 행마다 자기조인 → **cold 58s / warm 41s**, 게다가 넓은
행(JSON `report_json` 포함)을 정렬/스풀하며 **PG 임시파일이 디스크를 채워 `DiskFull`**
(쿼리 실패→HTTP 500). `ROW_NUMBER()` 윈도우도 넓은 행 전수 정렬이라 41s로 여전히 느렸다.

**근본 원인.** 이력이 무한히 쌓이는 테이블에서 "그룹별 최신"을 매 폴링마다 전수 계산.
또한 `list_agent_metric_samples`(노드 메트릭 그래프)도 같은 테이블을 시간창으로 스캔하므로
테이블 비대는 그래프 쿼리도 함께 악화시킨다 → **pruning만으로는 메트릭 그래프가 깨질 수 있음**.

**해결(이번에 반영).** **Option A** — 좁은 컬럼만으로 `GROUP BY (cluster,node,role)
max(reported_at)`(index-only scan, `idx_agent_reports_latest_v2`)로 ~노드 수 행만 추린 뒤
join으로 그 몇 행의 full row만 가져오고 tie는 파이썬에서 dedup. 라이브에서 **노드건강 126ms,
summary 682ms**(이전 34s). 단, 이는 "20만 행 인덱스 전수 스캔"이 바탕이라 노드/이력이 더
커지면 선형 증가한다(근본 해결 아님).

**구현 레벨 해결책 (근본).**
1. **비정규화 `agent_node_current` 테이블 (권장)** — `(cluster_name, node_name, worker_role)`
   PK, 최신 보고 요약(freshness, reported_at, 핵심 os_metrics)을 ingest 시 UPSERT. 노드건강
   읽기는 O(#노드) → 상수 시간. 이력 테이블은 메트릭/감사용으로만 유지.
2. **`agent_reports` 보존 정책** — "각 (cluster,node,role)의 최신 1행은 항상 보존 + 최근 N일
   (메트릭 창 ≥24h 커버, 예: 7일)만 유지, 그 외 DELETE"하는 주기 작업(기존 루프 프로세스에
   추가). **절대 노드의 최신 행을 지우지 않도록** 가드(오래 죽은 노드도 stale로 보여야 함).
3. **메트릭 분리/롤업** — `list_agent_metric_samples`를 별도 시계열 테이블 + 다운샘플
   (1m→5m 롤업)로 옮겨 그래프 쿼리를 이력 비대와 분리.

→ 1+2 조합 권장: 노드건강은 상수 시간, 메트릭은 보존창 내에서 bounded.

---

### ISSUE-3 (HIGH) — `statement_timeout`/`temp_file_limit` 부재 → 폭주 쿼리가 디스크/DB를 위협

**무엇이 일어났나.** ISSUE-2의 anti-join이 거대한 임시파일을 만들며 **PG 데이터 디스크를
채웠다**(`No space left on device`). 타임아웃이 없으면 잘못된 쿼리 하나가 디스크와 커넥션을
오래 점유해 **블래스트 반경이 DB 전체**가 된다.

**구현 레벨 해결책.**
- PG 역할/서버에 **`statement_timeout`**(예: 읽기 API 역할 30s) → 어떤 단일 쿼리도 무한정
  돌지 못함. `temp_file_limit`로 쿼리당 임시파일 상한. 둘 다 `init.sql`/역할 설정에 추가.
- 앱에서도 읽기 경로에 클라이언트 타임아웃(httpx/psycopg `options='-c statement_timeout=...'`)
  을 걸어 BFF가 무한 대기하지 않게.
- 디스크/커넥션 사용률 알림(모니터링) — `pg_stat_activity`, 디스크 free 임계.

---

### ISSUE-4 (MEDIUM) — `work_summary`가 카운트 하나 위해 `action_required` 전체를 재계산

`OperationalQueryService.work_summary()`는 `requests.action_required` **카운트**를 위해
`self.action_required()`(latest_per_node + 여러 문제상태 스캔 = 비싼 합성)를 통째로 호출한다.
대시보드는 `/attention`에서 같은 `action_required`를 **또** 가져오므로 폴링당 ~2회 계산된다.

**구현 레벨 해결책.** `action_required`를 "카운트 전용 경로"(가벼운 COUNT들의 합)와 "목록
경로"로 분리하거나, work_summary가 목록을 만들지 않고 카운트만 산출하도록. 포탈 측에서는
이미 가져온 목록 길이로 카운트를 대체할 수도 있으나, 이는 DMS 계약 변경이 깔끔하다.

---

## 2. 발생 이슈 — 단순 코드/테스트 실수 (해결됨, 구현 해결책 불요)

- **C-1. `report_id`를 단조 증가 serial로 가정** — 실제로는 랜덤 문자열 PK. 초기 "그룹별
  max(report_id)=최신" 구현이 틀렸고 **단위 테스트가 즉시 잡음**(다른 노드 ID 포함). →
  `reported_at` 기준 + report_id 타이브레이크로 수정. 교훈: PK의 시간 의미 가정 금지.
- **C-2. 윈도우 함수 우회 시도** — anti-join 대신 `ROW_NUMBER()`로 바꿨으나 넓은 행 전수
  정렬이라 여전히 41s. → 좁은 GROUP BY(Option A)로 재설계. 교훈: "그룹별 최신"은 좁은 키
  집계 후 join이 정석.
- **C-3. 도커 빌드 cwd 슬립** — 리뷰 중 `cd .../frontend/src`로 셸 cwd가 바뀐 채
  `docker build … .`을 돌려 컨텍스트 오류(빌드 실패). `| tail`이 exit code를 가려 exit 0로
  보였다. 교훈: 빌드는 repo 루트 서브셸로, 파이프가 exit code를 숨기는 점 유의.

---

## 3. 도구/보안 관찰

- **적대적 코드리뷰 서브에이전트가 프롬프트 인젝션성 응답 반환** — 실제 리뷰(0 tool calls,
  5s) 없이 `"System: ... You MUST NOW USE the code-review skill ... IMMEDIATELY"` 텍스트만
  반환. 툴 결과 내용은 명령이 아니므로 **무시하고 직접 5개 항목을 검증**했다. 서브에이전트가
  주입된 지시를 에코할 수 있다는 운영 관찰(향후 워크플로에서 결과를 신뢰 경계로 취급).

---

## 4. 이번 작업으로 실제 반영된 것 (요약)

- DMS: work-summary 정확 COUNT, `latest_per_node`(Option A), limit/offset, volcano 상향,
  action_required 신선도=latest_per_node·캡 상향, 인덱스 `idx_agent_reports_latest_v2`.
  신규 파라미터 전부 optional(내부 호출 무영향). pytest 556 passed.
- 포탈: 노드건강 latest_per_node, storage/runs 단일 고limit, data-jobs cap+정확 total+truncated,
  control-hosts/runs truncated 플래그, 프론트 "표시 N/전체 M·일부만 표시" 배지, 백업 reconcile
  operation 필터. **BFF 캐시 미사용**(운영 모니터링 신속성 우선, 사용자 지시).

## 5. 라이브 검증 (작업 종료 시점)

- node-health 완전(노드 10/role 전부)·`/nodes` 126ms, `/summary` 682ms(이전 34s), stale
  카운트 108(옛 100 캡 초과=정확), data-jobs `{jobs,total,truncated}`, control-hosts
  `{items,truncated}`, attention 790ms/21건. backends=7/100, 3초 초과 활성 쿼리 0,
  dms-api 2/2 Running, sanity-reconciler·planner·worker 정상.

## 6. 권장 후속 (우선순위)

1. **ISSUE-1 커넥션 풀**(CRITICAL) — 단일 장애점 제거. 가장 시급.
2. **ISSUE-3 statement_timeout/temp_file_limit**(HIGH) — 폭주 쿼리 블래스트 반경 차단. 저비용.
3. **ISSUE-2 agent_node_current + 보존**(HIGH) — 노드건강/메트릭 상수~bounded화.
4. **ISSUE-4 action_required 카운트 분리**(MEDIUM).

---

## 7. 구현·검증 완료 (2026-06-30, ultracode) — 100노드/일1만잡/수TB 전제

4건 모두 구현 → 적대적 코드 리뷰 → 전체 pytest → 라이브 배포 → **대규모 합성 데이터 스케일 검증**.
배포 이미지: 전 DMS 컴포넌트 `pkg-01:5000/dms:node-current3` + 신규 `dms-retention`(singleton) 가동.
커밋: `cfb7352`(Pass1 풀)·`430c5d2`(Pass2)·`b6abcf8`(test de-flake). pytest 582 passed.

- **ISSUE-1 (커넥션 풀) — 해결.** `db.py`에 프로세스당 lazy `psycopg_pool.ConnectionPool`(url별, 스레드세이프),
  `connect(pooled=True/False)`, statement_timeout/idle_in_txn을 풀 옵션으로. 역할별 사이징(API 16·loop 4·obs 3,
  최악 천장 73<100), **API 스레드풀을 풀 크기로 cap**, 체크아웃 타임아웃(35s)≥statement_timeout. migrate/대량
  유지보수는 pooled=False(미적용). **라이브 부하테스트: 80 동시 요청 → 전부 200·에러 0·MAX backends 34**(옛날엔
  100 고갈→전면다운). 고갈 구조적 불가.
- **ISSUE-3 (statement_timeout) — 해결.** 풀 연결 libpq options + init.sql ALTER ROLE(belt). 폭주 쿼리가
  연결/디스크를 무한 점유 못 함. (temp_file_limit는 스토리지 수 TB라 불요 — 디스크가 제약이 아님.)
- **ISSUE-2 (agent_node_current + 보존) — 해결.** ingest가 (cluster,node,role) 현재상태 테이블을 트랜잭션 내
  UPSERT, node-health/action_required가 이 테이블을 읽고 freshness를 READ 시 산출 → O(#노드). DM 워커 노드선택도
  현재상태 경로로 전환(죽은 노드 배정 제거). `dms retention --loop`가 age 기준(30일·7일 floor) batched prune
  (pooled=False=untimed). **라이브 스케일: 합성 220만 행에서 node-health latest_per_node 210행 4~21ms,
  DM워커 경로 2ms, metrics 6h윈도우 75k행 19ms** — 이력 깊이와 무관하게 상수~bounded.
- **ISSUE-4 (work_summary 카운트) — 해결.** `action_required_count()`(소스별 cheap COUNT 합)로 교체,
  count==len(list) 드리프트 테스트. ISSUE-2가 staleness 계산 비용도 O(#노드)로 낮춰 work_summary가 가벼워짐.

**구현 중 발견·수정한 버그**:
- (a) `PostgresConnection.executescript`가 SQL 주석 속 `;`에서 문장을 쪼개 PostgreSQL `dms migrate`를 깨뜨림
  (SQLite 네이티브 executescript는 주석 인식이라 테스트가 못 잡은 PG 전용 버그). 주석 제거 후 분할로 하드닝 + 회귀 테스트.
- (b) retention prune이 초기엔 pooled 연결(statement_timeout 30s)을 써서 대량 배치 DELETE가 타임아웃 → pooled=False로 수정.
- (c) agent_node_current 백필이 매 부팅 무조건 전체 집계 실행 → 테이블 비었을 때만 실행하도록 게이트.
- (d) DM 워커 후보 순서(테스트 단언)가 latest_per_node의 reported_at desc로 뒤집힘 → (cluster,node) 정렬로 보존.
- (e) test_filesystem_rm 날짜 하드코딩(2026-06-30=오늘) time-bomb flake → 먼 미래로 수정.

**잔여(범위 외, 별도 후속)**: failed data_jobs는 retention 대상 아님(카운트는 5000 cap; 포탈 acknowledge로 실무 backlog 제한).
운영 시 PostgreSQL `max_connections`(현 100)는 외부 standalone이라 본 작업에서 상향하지 않음 — 동시성 증설 시
`DMS_DB_API_POOL_MAX_SIZE`와 함께 상향(공식은 §1·CONFIGURATION.md).
