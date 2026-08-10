# 슬라이스 20 — Volcano 대기 이력 설계

슬라이스 17 이 라이브 PodGroup 대기 현황을 붙였지만 PodGroup 은 잡 종료와 함께
삭제된다 — 끝난 잡의 스케줄링 대기는 사후에 알 수 없다. 이 슬라이스는 그 공백을
`data_jobs.sched_wait_seconds` 로 메운다. 슬라이스 17 설계 §7 이 이미 예고한 후속이다:
"샘플링을 컨트롤러로 옮겨 `sched_wait_seconds` 를 같은 컬럼 패턴으로 남기는 **후속
슬라이스**가 필요하다"(`slice17-design.md:165-166`). `migrations.py:169-171` 의 컬럼
주석도 같은 문장을 남겼다: "진짜 큐 대기는 살아 있는 PodGroup 라이브 뷰에만 있고
… 그것을 이력으로 남기려면 후속 슬라이스가 필요하다(설계 §7)."

## 1. 실측으로 확인한 전제

1. **스테퍼는 이미 매 틱 vcjob 을 GET 한다.** `_poll_execution` 이 execution ref 를
   폴링하고(`stepper.py:175-179`), Volcano 어댑터는 vcjob 을 GET 해
   `status.state.phase` 를 `_VCJOB_PHASE` 로 접는다(`execution_volcano.py:184-191`):
   `Inqueue→PENDING`, `Running→RUNNING`(`:15-20`). **필요한 관측을 이미 하고 있다.**
2. **`data_jobs.state` 는 실행 시작이 아니다.** `_submit_execution` 은 adapter.submit →
   set_phase_ref → set_job_state 순이고(`stepper.py:160-173`), 상태는 scan 이
   Running(`:155`), sync/rm 이 Executing(`:270`)이 된다 — **vcjob 이 아직 Inqueue 여도**.
   DB 상태를 "실행 시작"으로 읽고 측정을 끝내면 진짜 큐 대기를 통째로 놓친다.
   submit_wait 이 굳이 "픽업 지연"으로 이름을 정정한 이유가 정확히 이것이다.
3. **PodGroup 이름 `<vcjob>-<uid>` 는 계약이 아니고 DMS 라벨도 없다** — 슬라이스 17 이
   목록+필터로 우회한 이유로 이미 문서화됨(`queue_reader.py:19-21`,
   `10-rbac.yaml:127-130`). 이름 유도는 Volcano 버전 업에 조용히 깨진다.
4. **컨트롤러 Role 에 `scheduling.volcano.sh` 규칙이 전무하다**(`10-rbac.yaml:22-65`).
   그리고 `tests/test_rbac_contract.py:92-99` 가 그 **무권한을 계약으로 고정**해 뒀다
   (`grants == []`). podgroups 를 읽으려면 이 계약부터 깨야 한다.
5. **`resourceNames` 는 list 에 적용되지 않는다** — 이 저장소가 두 번 적어 둔 함정
   (`10-rbac.yaml:129-130`, `test_rbac_contract.py:7-9`). 403 은 화면이 없는
   컨트롤러에서 더 조용하다(같은 파일 `:1-4` 가 이미 지적).
6. **`data_jobs` 의 시간 컬럼은 4개뿐이다**: created_at/updated_at(`migrations.py:179-180`),
   preview_expires_at(`:145`), submit_wait_seconds(`:178`). submitted_at·started_at·
   finished_at 은 없다. 상태별 시각은 `state_transitions` 가 전이마다 `at` 으로
   남긴다(`:119-127`). `set_job_state` 는 from==to 를 억제하지 않으므로
   (`repositories/data_jobs.py:145-169` — 종단 가드뿐), **sync/rm 의 실행 제출도
   Executing→Executing 자기 전이 행을 남긴다.**
7. **`claim_steppable` 은 Pending/Preflight/PreviewRunning/Executing/Running 만
   클레임한다**(`repositories/data_jobs.py:191-201`) — 종단 잡은 스테퍼가 다시 보지
   않는다. `SELECT *` 스냅샷이라 클레임 행에서 컬럼 선독이 공짜다.
8. **submit_wait 의 0-가드는 정확히 5곳이다**: 기록 시 `is None` 선독
   (`repositories/data_jobs.py:151-154`), 집계 SQL 의 `IS NOT NULL`/`IS NULL` 술어
   (`repositories/metrics.py:119-126`), 히스토그램의 `v is None or v < 0`
   (`metrics_series.py:130-133`), 라우트의 `counted = len()`(truthy 필터 금지 주석
   포함, `api/routes_metrics.py:64-68`), 백필의 `WHERE … IS NULL`(`migrations.py:446`).
9. **vcjob 은 `ttlSecondsAfterFinished` 로 한동안 남는다**(`execution_manifests.py:214-220`)
   — 그러나 PodGroup 은 아니다(슬라이스 17 §1-1 실측). "vcjob 이 GET 되니 PodGroup 도
   있겠지"는 성립하지 않는다.
10. **기본 실행 백엔드는 stub 이고**(`config.py:120,178`) `StubExecutionAdapter.poll`
    은 스크립트가 없으면 즉시 SUCCEEDED 를 준다(`execution.py:67-73`) — RUNNING 을
    거치지 않는다. 틱은 스테퍼 5s/플래너 10s(`config.py:108,110`), 컨트롤러 리스는
    `max(interval*3, 30)`(`controller.py:92-94`).
11. **`db.iso_epoch` 는 `%Y-%m-%dT%H:%M:%SZ` 엄격 포맷, 실패 시 ValueError**
    (`db.py:22-27`). 항목 단위 try/except 강등 선례가 이미 있다
    (`api/routes_metrics.py:210-214`, `repositories/data_jobs.py:160-161`).

## 2. 핵심 결정

### 2.1 PodGroup 을 읽지 않는다 — 원안을 뒤집는다

백로그의 원안(슬라이스 17 §7)은 "컨트롤러가 PodGroup 을 샘플링"이었다. 정찰이
세 가지로 기각했다: 이름 유도는 비계약이라 조용히 깨지고(§1-3), 컨트롤러 무권한이
**계약 테스트로 고정**돼 있으며(§1-4), podgroups 규칙에 `resourceNames` 를 붙이면
모든 list 가 조용히 403 이 된다(§1-5) — 컨트롤러에는 그 403 을 보여줄 화면조차 없다.

대신 **스테퍼가 이미 하고 있는 vcjob phase 관측**(§1-1)을 쓴다. 결과: 추가 k8s 호출
0, **RBAC 변경 0**, 계약 테스트 개정 0, 비계약 이름 규칙 의존 0.

### 2.2 정의 — 이름이 재는 것과 맞아야 한다

`data_jobs.sched_wait_seconds` = **execution vcjob 제출 → 첫 Running 관측**(초).

- **시작점**은 `_submit_execution` 이 제출 직후 같은 틱에 남기는 전이 행의 `at` 이다:
  scan 은 Preflight→Running, sync/rm 은 Executing→Executing 자기 전이(§1-6) —
  둘 다 `stepper.py:172` 한 곳이 쓰므로 잡당 유일하다. **끝점**은 execution ref
  폴링이 처음 `RUNNING` 을 돌려준 틱의 현재 시각.
- **preview vcjob 은 측정하지 않는다.** sync/rm 은 preview 와 execution 두 vcjob 을
  만들므로(`stepper.py:204-218`, `:160-173`) 단일 컬럼이면 두 대기가 섞인다. 컬럼
  주석에 못박는다.
- **`submit_wait_seconds` 와 다른 것을 잰다.** submit_wait 은 created_at → 첫 비-Pending
  전이(= DMS 내부 픽업 지연, `migrations.py:166-177`), sched_wait 은 제출된 vcjob 이
  Volcano 큐에서 기다린 시간이다. 슬라이스 17 이 `queue_wait_seconds` →
  `submit_wait_seconds` 로 이름을 정정한 교훈(슬라이스 17 §2.4) 그대로, 두 컬럼의
  차이를 스키마 주석과 화면 라벨 양쪽에 명시한다.
- **정직한 오차**: 스테퍼 틱(5s) + Volcano 의 vcjob status 갱신 지연이 그대로
  더해진다. 게다가 `_VCJOB_PHASE` 는 Completing 등도 RUNNING 으로 접으므로
  (`execution_volcano.py:17-20`) 틱 사이에 실행이 끝나가는 잡은 관측이 늦게 잡힌다.
  이 값은 Volcano 큐 대기의 **근사**이지 PodGroup 이 보고하는 값이 아니다 — 화면
  캡션과 컬럼 주석에 적는다.

### 2.3 기록 — write-once 는 별도 UPDATE 로 강제한다

첫 RUNNING 관측 틱에서 **별도 UPDATE + `WHERE sched_wait_seconds IS NULL`** 을 친다.
`set_job_state` 의 UPDATE 에 끼워 넣지 않는다: 그 UPDATE 는 **항상**
`submit_wait_seconds` 컬럼을 쓰므로(`repositories/data_jobs.py:162-167`) 같은 자리에
sched_wait 을 넣으면 선독-보존 패턴을 매 전이마다 지켜야 하고, 한 곳이라도 놓치면
모든 상태 전이가 값을 NULL 로 덮는다. SQL 술어가 write-once 를 DB 계층에서 강제하면
호출자가 몇 번 부르든 안전하다. 클레임 스냅샷의 `sched_wait_seconds` 선독(§1-7)으로
이미 기록된 잡은 UPDATE 자체를 건너뛴다 — 매 틱 0행 UPDATE 를 반복하지 않는다.

컬럼·인덱스는 `submit_wait_seconds` 선례의 **이중 경로**를 그대로 따른다: 신규 DB 는
CREATE TABLE 안에(`migrations.py:178` 옆), 기배포 DB 는 `_ensure_columns` 의 ALTER 로
(`:417` 선례). 양쪽에 넣지 않으면 라이브에서만 컬럼이 없다 — 슬라이스 14 의 실 500
교훈. 타입은 BIGINT(두 경로 동일 선언형 규약, `:411-412`). 집계용 커버링 인덱스
`(created_at, sched_wait_seconds)` 는 컬럼 보강 **이후** 생성한다
(`idx_data_jobs_created` 의 순서 규칙, `:322-329`).

### 2.4 0초는 정상값이다 — 가드를 전부 복제한다

1초 해상도에서 0 은 "같은 틱 안에 스케줄됨"이라는 **가장 건강한 값**이다. truthy 검사
하나가 끼면 그 잡들이 집계에서 사라진다. submit_wait 이 가드를 심은 5곳(§1-8) 중
백필을 제외한 **4곳을 sched_wait 경로에 복제**한다:

- 기록: 선독 `is None` + SQL `IS NULL`(§2.3) — `data_jobs.py:151-154` 선례.
- 집계: `IS NOT NULL`/`IS NULL` 술어 — `metrics.py:119-126` 선례. `COALESCE(…,0)=0`
  류의 falsy 검사 금지.
- 히스토그램: `duration_histogram` 재사용 — `v is None or v < 0` 가드가 이미 있다
  (`metrics_series.py:130-133`). 새 코드 0.
- 라우트: `counted = len(…)` — `routes_metrics.py:64-68` 선례 그대로.

다섯 번째(백필의 `IS NULL`, `migrations.py:446`)는 복제 대상이 아니다 — §2.5.

### 2.5 백필은 불가능하다 — 지어내지 않는다

`_backfill_submit_wait`(`migrations.py:430-457`)를 복제하고 싶어지지만 **명시적으로
기각한다**: submit_wait 의 원천(`state_transitions` 의 Pending 이탈 시각)은 DB 에
있었지만, sched_wait 의 원천(Volcano 가 잡을 Running 으로 올린 시각)은 어디에도 없다.
PodGroup 은 이미 삭제됐고 `state_transitions` 에 Volcano 스케줄 시각이 없다.
**과거 잡은 전부 NULL 이 맞다.** 대신 `excluded`(기록 없음) 건수를 화면에 표면화한다
— 안 하면 도입 직후 화면이 "데이터 없음"처럼 보이거나, 반대로 공백이 숨는다.

### 2.6 관측 못 하는 잡 — 화면이 거짓말하지 않게

한 스테퍼 틱 안에 스케줄되고 끝난 짧은 잡(첫 폴링이 곧장 SUCCEEDED), 그리고 Running
을 거치지 않고 실패한 잡은 **구조적으로 NULL** 이다. 이들도 §2.5 의 excluded 로
잡히므로 집계를 오염시키지 않고, 화면은 "제외 N건"으로 정직하게 드러낸다.

### 2.7 버킷은 `SUBMIT_WAIT_BUCKETS` 를 재사용한다

우선 `SUBMIT_WAIT_BUCKETS`/`SUBMIT_WAIT_OVERFLOW`(`metrics_series.py:120-122`)를 그대로
써서 제출 대기와 스케줄 대기 **두 분포를 같은 축으로 나란히** 비교 가능하게 한다.
스케줄 대기의 실분포는 아직 모른다 — 실증(§6)에서 분포를 본 뒤 필요하면 후속으로
경계를 조정한다. 근거 없는 새 버킷을 지금 짓는 것보다, 비교 가능성을 먼저 얻고
데이터로 조정하는 편이 슬라이스 17 의 버킷 산정 방식(`:116-119` 주석)과도 일관된다.

## 3. 화면

`JobStatsSection` 의 submit_wait 패턴을 그대로 따른다: `job_stats` 가 원자료+excluded
반환(`repositories/metrics.py:150-151` 선례) → 라우트가 히스토그램+counted 로 접고
원자료는 응답에 싣지 않음(`routes_metrics.py:63-70`) → 프론트가 "집계 N건 ·
제외(기록 없음) N건" 캡션(`JobStatsSection.tsx:101-110`).

- 라벨은 **「스케줄 대기(Volcano)」** — 「제출 대기」(DMS 픽업 지연) 옆에 나란히 두고
  캡션에 두 값의 차이와 근사 오차(§2.2)를 명시한다.
- 라이브 `QueueSection` 의 숫자와 이력 분포는 **어긋날 수 있고 그래야 정상**이다 —
  라이브는 무윈도 현재 스냅샷, 이력은 창 집계다. KPI 타일과 큐 카드가 이미 같은
  구분을 라벨로 해결했다(`QueueSection.tsx:70-73`).

## 4. 오류 처리

- 전이 행 시각이 깨졌으면(`iso_epoch` ValueError, §1-11) **NULL 로 남긴다** — 값을
  지어내지 않는다(`data_jobs.py:160-161` 선례). 음수(시계 스큐)는 submit_wait 규칙
  그대로 0 으로 접는다(`:156-159` — 1초 해상도 세계에서 0 이 정직하다).
- 기록 UPDATE 실패는 그 잡의 스텝 에러로 격리된다(`stepper.py:32-42` 의 잡 단위
  try/except) — 다음 틱의 RUNNING 관측이 재시도하므로 별도 복구 경로가 필요 없다.
- **스텁 백엔드(기본값)에서는 정직한 no-op 이다**: stub poll 은 RUNNING 을 주지
  않으므로(§1-10) 아무것도 기록되지 않고, 모든 잡이 excluded 로 집계된다. 로컬·CI
  화면은 "집계 0건 · 제외 N건"이 되며 이것이 올바른 표시다.

## 5. 테스트

- write-once: 첫 RUNNING 관측이 기록하고, 이후 관측·재클레임이 덮어쓰지 않는지
  (`WHERE IS NULL` 이 실제로 0행 갱신인지).
- **값 0 이 기록·집계·counted 에 살아남는지** — 같은 초 관측을 만들어 truthy 필터
  부재를 단언한다(submit_wait 테스트 선례).
- Running 미도달 실패 잡·한 틱 완료 잡이 NULL 로 남고 excluded 로 집계되는지.
- preview 단계 폴링이 기록하지 **않는지**(execution ref 에만 반응).
- 마이그레이션: CREATE 경로와 `_ensure_columns` ALTER 경로 양쪽, 인덱스 생성,
  그리고 **백필이 없다는 것 자체**(마이그레이션 후 과거 잡이 NULL 유지).
- 스텁 어댑터 경로: 스크립트로 RUNNING 을 흘리면 기록되고, 기본(즉시 SUCCEEDED)
  이면 기록되지 않는지.
- 프론트: 캡션에 집계/제외 건수가 함께 나오고, 두 대기 라벨이 구분되는지.

## 6. 실증 (테스트베드)

1. 실 sync 잡에서 sched_wait 가 실제로 기록되는지 — **0 이 아닌 값과 0 인 값 양쪽**
   (큐가 붐빌 때와 한가할 때).
2. **0초 기록이 집계에 살아남는지** — 같은 틱 스케줄 잡이 counted 에 포함되고
   히스토그램 첫 버킷에 나타나는 것을 화면에서 확인(falsy 검사 잔존 검증).
3. 배포 직후 과거 잡이 **전부 excluded** 로 잡히고 화면이 그 수를 정직하게
   표시하는지("데이터 없음"으로 보이지 않는지).
4. write-once: 같은 잡이 여러 틱 관측돼도 값이 바뀌지 않는지(DB 직접 확인).
5. 실패한 잡(Running 미도달)이 NULL 로 남고 집계를 오염시키지 않는지.

## 7. 이 슬라이스에서 하지 않는 것

- **PodGroup 샘플링·이름 유도**(§2.1 — 원안 기각. 비계약 이름 + 컨트롤러 무권한 계약).
- **컨트롤러 RBAC 확장** — `test_rbac_contract.py:92-99` 의 계약을 유지한다.
- **preview vcjob 대기 측정**(§2.2 — 단일 컬럼에 두 대기를 섞지 않는다).
- PodGroup `status.conditions` 로 "왜 대기 중인가" 얻기 — 슬라이스 17 §7 이 이미
  금지한 항목(문자열 미검증) 그대로 유지.
- **과거 잡 백필**(§2.5 — 원천 데이터가 존재하지 않는다).
- `runs` 테이블 부활(슬라이스 17 §7 결정 유지).
- 대기 시간 경보/알림.
