# 슬라이스 20 — Volcano 대기 이력 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 끝난 잡의 Volcano 스케줄링 대기를 이력으로 남긴다 — `data_jobs.sched_wait_seconds` = **execution vcjob 제출 → 스테퍼의 첫 RUNNING 관측**(초). 슬라이스 17 의 라이브 PodGroup 뷰는 잡 종료와 함께 사라지는 공백을 메우되, PodGroup 을 읽지 않고(RBAC 변경 0, k8s 호출 추가 0) 스테퍼가 이미 하는 vcjob phase 관측을 재사용한다.

**Architecture:** 앵커는 `_submit_execution` 이 execution vcjob 제출 직후 `data_jobs.exec_submitted_at` 에 write-once 로 남긴다(설계 §2.2 의 "전이 행의 at" 을 명시적 컬럼 계약으로 바꾼 플랜 결정 — 아래 D1). `_poll_execution` 이 execution ref 폴링에서 처음 RUNNING 을 보면 `record_sched_wait` 가 `now - exec_submitted_at` 을 `WHERE sched_wait_seconds IS NULL` 술어로 기록한다 — preview/preflight 폴링 경로는 이 훅을 지나지 않는다. 집계는 submit_wait 의 4중 0-가드(선독 `is None` / SQL `IS NOT NULL·IS NULL` / 히스토그램 `v is None or v < 0` / 라우트 `len()`)를 그대로 복제하고, 버킷은 `SUBMIT_WAIT_BUCKETS` 를 재사용해 두 대기를 같은 축으로 비교한다(설계 §2.7). **백필은 없다**(설계 §2.5: 원천이 DB 어디에도 없다) — 과거 잡은 NULL 로 남고 화면이 제외 건수로 정직하게 드러낸다. 스텁 백엔드(기본값)에선 poll 이 RUNNING 을 주지 않아 정직한 no-op 이다(설계 §4).

**Tech Stack:** Python 3.11 표준 라이브러리(컬럼 2 + 저장소 메서드 2 + 스테퍼 훅 2줄 + 집계 2쿼리 + 라우트 3줄), React 18 + Vitest(차트 1 + 캡션 1), 신규 의존성·신규 테이블·RBAC 변경·k8s 호출 없음.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-10-dms-sched-wait-slice20-design.md`. 플랜과 충돌하면 **설계가 이긴다**(단, 앵커 저장 위치는 아래 D1 이 실측 근거로 확정한 플랜 결정이다 — 설계 §2.2 의 측정 **정의** 자체는 그대로다).
- **새 pip/npm 의존성 금지.**
- **새 DB 테이블 금지** — `tests/test_migrations.py:173` 이 `len(ALL_TABLES) == 20` 을 고정한다. 상태는 기존 `data_jobs` 에 컬럼 2개(`exec_submitted_at`, `sched_wait_seconds`)로 얹는다.
- **컬럼은 CREATE TABLE 과 `_ensure_columns` 양쪽에** 넣는다. 한쪽만 넣으면 기배포 DB 에서만 컬럼이 없다(슬라이스 14 가 실 500 으로 배운 교훈). 커버링 인덱스는 컬럼 보강 **이후**에 생성한다(`idx_data_jobs_created` 의 순서 규칙, `migrations.py:322-329`).
- **preview vcjob 은 측정하지 않는다**(설계 §2.2): sync/rm 은 vcjob 이 둘(preview + execution)이라 단일 컬럼에 섞으면 두 대기가 오염된다. 앵커는 `_submit_execution` 에서만, 기록은 `_poll_execution` 에서만.
- **write-once 는 SQL 술어로 강제한다**: `UPDATE ... WHERE job_id = :j AND sched_wait_seconds IS NULL`(앵커도 `AND exec_submitted_at IS NULL`). 이미 기록된 잡은 클레임 스냅샷 선독으로 UPDATE 자체를 건너뛴다(매 틱 0행 UPDATE 반복 금지, 설계 §2.3).
- **0 초는 정상값이다**(설계 §2.4): truthy 검사(`if not v`, `COALESCE(...,0)=0`, `len([w for w in ws if w])`) 금지. submit_wait 의 0-가드 4곳(`repositories/data_jobs.py` 선독 / `repositories/metrics.py` 술어 / `metrics_series.py` 히스토그램 / `api/routes_metrics.py` counted)을 sched_wait 경로에 전부 복제하고, 각 계층에 0 생존 테스트 + **실제로 깨뜨려 RED 를 확인하는 뮤테이션 스텝**을 둔다(슬라이스 17 의 교훈: 테스트가 무언가를 붙잡는다는 주장은 RED 를 볼 때까지 추측이다).
- **백필 금지**(설계 §2.5): sched_wait 의 원천(Volcano 가 잡을 Running 으로 올린 시각)은 `state_transitions` 에도 없다. `_backfill_submit_wait` 를 복제하지 않는다 — "마이그레이션 후 과거 잡이 NULL 로 남는다"를 붙잡는 테스트를 둔다.
- **`StubExecutionAdapter` 의 기본 동작(즉시 SUCCEEDED)을 바꾸지 않는다** — 기존 998 테스트가 그 동작에 의존한다. RUNNING 은 기존 `script()` 헬퍼(`execution.py:88-89`)로 흘린다(실측: 확장 불필요).
- **`set_job_state` 의 UPDATE 에 sched_wait 을 끼워 넣지 않는다**(설계 §2.3): 그 UPDATE 는 항상 `submit_wait_seconds` 를 쓰므로(`repositories/data_jobs.py:162-167`) 같은 자리에 넣으면 매 전이 선독-보존을 지켜야 하고 한 곳만 놓쳐도 NULL 로 덮인다. 별도 UPDATE 다.
- **클러스터 접근(kubectl) 금지** — 실증은 「플랜 이후」 절의 별도 ops.
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- 백엔드 테스트(이 워크트리 전용, `.venv` 가 워크트리에 없다):
  `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest <대상> -q`
  전체 스위트는 **포그라운드**로 Bash timeout 600000ms. **기준선 998 passed.**
- 프론트: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx vitest run`(**기준선 215 passed / 48 files**), 타입체크 `npx tsc -b`.
- 주석은 **한국어**로 "왜"를 적는다.
- **origin 으로 push 금지.** 커밋만 한다.

## 실측 고정값 (코드 직접 확인)

| 항목 | 값 |
|---|---|
| execution 제출 지점 | `_submit_execution`(`stepper.py:160-173`): `adapter.submit` → `set_phase_ref(jid, "execution", ref)`(:168) → `_reclaim_if_terminal`(:169) → `set_job_state(jid, running_state)`(:172). 호출자는 둘뿐: scan 은 `_poll_preflight`(:155)에서 `running_state=RUNNING`, sync/rm 은 `_poll_or_submit_execution`(:270)에서 `EXECUTING` |
| 자기 전이 실재 | `set_job_state` 는 from==to 를 억제하지 않는다(`repositories/data_jobs.py:145-169` — 가드는 종단뿐 :138-144, else 분기가 무조건 `_record_transition` :168-169). **sync/rm 의 execution 제출은 Executing→Executing 자기 전이 행을 실제로 남긴다** — 설계 §2.2 의 전제 성립을 코드로 확인함(D1 참고) |
| Executing 을 쓰는 3곳 | `stepper.py:270`(자기 전이의 유일한 생산자), `api/routes_jobs.py:67`(ConfirmPending 아니면 409 — :52-53 가드), `batch_orchestrator.py:118-119`(`_confirm_child`, previewed 항목만) — 자기 전이 유일성은 이 세 모듈의 가드에 걸친 교차 불변식이다 |
| execution 폴링 지점 | `_poll_execution`(`stepper.py:175-202`)만 `phase_refs["execution"]` 을 폴링한다. 진입 경로 둘: state Running(:123-124), `_poll_or_submit_execution` 의 `"execution" in refs` 분기(:248-249). preview 는 `_poll_preview`(:220-243, `phase_refs["preview"]`), preflight 는 `_poll_preflight`(:146-158) — 서로 다른 함수 |
| ExecStatus 접기 | `_VCJOB_PHASE`(`execution_volcano.py:15-20`): `Inqueue→PENDING`, `Running→RUNNING`, `Completing/Aborting/Terminating/Restarting→RUNNING`. vcjob poll 분기 :184-191 — Completing 도 RUNNING 으로 접히므로 이 값은 근사다(설계 §2.2 "정직한 오차") |
| 클레임 스냅샷 | `claim_steppable`(`repositories/data_jobs.py:191-201`): Pending/Preflight/PreviewRunning/Executing/Running 만, `SELECT *` 라 새 컬럼 선독이 공짜(설계 §1-7). 종단 잡은 다시 안 본다 |
| set_job_state UPDATE | `repositories/data_jobs.py:162-167` 이 **항상** `submit_wait_seconds` 를 쓴다 — sched_wait 을 여기 끼우면 매 전이 선독-보존 필요(별도 UPDATE 를 강제하는 설계 §2.3 의 근거) |
| 잡 단위 오류 격리 | `run_once` 의 try/except(`stepper.py:30-43`) — 기록 UPDATE 실패는 그 잡의 스텝 에러로 격리되고 다음 틱이 재시도(설계 §4) |
| 마이그레이션 좌표 | CREATE TABLE data_jobs `migrations.py:129-180`(submit_wait :178, created_at :179), `_ensure_columns` :403-427(submit_wait 항목 :417), 인덱스는 보강 이후 :322-330, `_backfill_submit_wait` :430-457(**복제 금지** — 설계 §2.5) |
| submit_wait 0-가드 4곳 | 기록 선독 `is None` + 음수→0 + ValueError→NULL(`repositories/data_jobs.py:151-161`) / 집계 `IS NOT NULL`·`IS NULL`(`repositories/metrics.py:119-126`) / 히스토그램 `v is None or v < 0`(`metrics_series.py:129-133`) / 라우트 `counted = len()` + truthy 금지 주석(`api/routes_metrics.py:63-70`) |
| 버킷 | `SUBMIT_WAIT_BUCKETS`/`SUBMIT_WAIT_OVERFLOW`(`metrics_series.py:120-122`), `duration_histogram(seconds, *, buckets, overflow)`(:125-141) — 파라미터화 완료, 재사용에 새 코드 0 |
| 스텁 어댑터 | `StubExecutionAdapter.poll`(`execution.py:67-73`): `script()` 큐가 있으면 pop, 없으면 즉시 SUCCEEDED. `script(ref, [ExecStatus.RUNNING, ...])`(:88-89)로 RUNNING 을 흘릴 수 있다 — **스텁 확장 불필요**, 기본 동작 불변 |
| 틱·기본 백엔드 | 스테퍼 5s(`config.py:110`), 기본 실행 백엔드 stub(`config.py:120`, env 기본 :178) — 로컬·CI 는 RUNNING 미관측 = 전부 excluded 가 정상(설계 §4) |
| 시각 | `utc_now_iso` 1초 해상도(`db.py:12-13`), `iso_epoch` 엄격 포맷·실패 시 ValueError(`db.py:22-27`) — data_jobs.py 는 이미 둘 다 import(:3) |
| metrics_jobs 라우트 | `api/routes_metrics.py:53-73` — submit_wait 접기 :63-70(`stats.pop` → counted=len → histogram), `SUBMIT_WAIT_BUCKETS` import 완료(:11-13) |
| job_stats 반환 | `repositories/metrics.py:112-126` 2쿼리 + :150-151 반환 키(`submit_wait_seconds`/`submit_wait_excluded`) — sched 는 같은 모양으로 복제 |
| 프론트 | `JobStatsSection.tsx:92-112` 분포 grid(`md:grid-cols-3`, 3칸: 처리량/수행시간/제출 대기) + 캡션 :104-110. `types.ts:175-191` `JobMetrics`(submit 3필드 :187-189). 테스트 STATS `JobStatsSection.test.tsx:13-37`(submit 키 :31-35), `renderSection` :39-44. `BarChart` 는 `role="img" aria-label={label}`(`BarChart.tsx:23-24`) |
| 테스트 헬퍼 | `tests/test_repo_data_jobs.py` `_repos`/`_mk_request`(:5-13)·`_mk_job`(:141-146), `tests/test_stepper_scan.py` `_scan_job`/`_stepper`(:15-33), `tests/test_stepper_sync.py` `_sync_job`(:16-31)·confirm 흐름 선례(:77-95), `tests/test_repo_metrics.py` `_seed_job`(:10-35, `wait=` 파라미터 보유), `tests/test_api_metrics.py` `ADMIN`(:4)·`_seed_job`(:17-32, rid 반환)·submit_wait 선례(:407-437), conftest `db`(migrate 완료)·`client` |

## 플랜 확정 사항 (실측 근거 결정)

**D1 — 앵커는 전이 행이 아니라 `exec_submitted_at` 컬럼에 남긴다.** 설계 §2.2 원문은 "`_submit_execution` 이 제출 직후 남기는 전이 행의 `at`"(scan: Preflight→Running, sync/rm: Executing→Executing 자기 전이)이다. 실측으로 그 전제가 성립함을 확인했다(자기 전이가 실제로 남는다 — 위 표). 전이 행을 특정하는 SQL 도 확정 가능하다:

```sql
SELECT at FROM state_transitions
 WHERE entity_kind = 'data_job' AND entity_id = :j
   AND ((from_state = 'Preflight' AND to_state = 'Running')       -- scan
        OR (from_state = 'Executing' AND to_state = 'Executing')) -- sync/rm
 ORDER BY id LIMIT 1
```

그러나 이 SQL 의 유일성은 (a) `set_job_state` 가 자기 전이를 영원히 억제하지 않는다는 **비계약적 우연**(설계 §1-6 도 "관측"으로만 적었다)과 (b) Executing 을 쓰는 다른 두 곳(`routes_jobs.py:52` 의 409 가드, `batch_orchestrator._confirm_child` 의 previewed 필터)이 자기 전이를 절대 만들지 않는다는 **세 모듈 교차 불변식**에 얹혀 있다. 어느 쪽이 깨져도 sync/rm 의 측정이 **조용히** 틀어진다 — 설계 §2.1 이 PodGroup 이름 유도를 기각한 것과 같은 실패 모드다. 대안(앵커 컬럼)은 컬럼이 하나 늘지만: 앵커가 명시적 계약이 되고, 클레임 스냅샷(`SELECT *`)에서 선독이 공짜라 첫 RUNNING 틱에 **추가 SELECT 0**, write-once 를 sched_wait 과 같은 SQL 술어 패턴으로 강제한다. 측정값은 동일하다 — 앵커는 `set_phase_ref`(:168)와 `set_job_state`(:172) 사이, 같은 틱에 기록되며 1초 해상도(`utc_now_iso`)에서 전이 행의 `at` 과 같은 시각이다. **설계의 측정 정의(execution vcjob 제출 → 첫 RUNNING 관측)는 그대로이고, 바뀌는 것은 앵커의 저장 위치뿐이다 → 앵커 컬럼을 채택한다.** (새 테이블 금지 제약에서 컬럼 추가는 허용.)

**D2 — 관측 지점은 `_poll_execution` 의 poll 직후 `status == RUNNING` 분기 하나다.** 이 함수만 execution ref 를 폴링한다(진입 경로 둘: `stepper.py:123-124`, :248-249). preview(`_poll_preview`)·preflight(`_poll_preflight`) 폴링은 별도 함수라 훅이 새지 않는다 — 그래도 "preview 폴링은 기록하지 않는다"를 앵커를 인위로 심은 가드 테스트 + 뮤테이션 RED 로 못박는다(Task 3).

**D3 — 스텁 확장 불필요.** `adapter.script(f"stub-execution-{jid}", [ExecStatus.RUNNING, ...])` 로 RUNNING 을 결정적으로 흘릴 수 있다(`execution.py:67-73, :88-89`). 기본 동작(즉시 SUCCEEDED)은 건드리지 않는다.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/migrations.py` (수정) | `exec_submitted_at TEXT` + `sched_wait_seconds BIGINT` — CREATE TABLE 과 `_ensure_columns` 양쪽 + `idx_data_jobs_created_sched (created_at, sched_wait_seconds)`(보강 이후). **백필 없음** |
| `src/dms/repositories/data_jobs.py` (수정) | `mark_exec_submitted`(앵커 write-once) + `record_sched_wait`(첫 RUNNING 기록 — 스냅샷 선독 + `IS NULL` 술어, 0 보존·음수→0·깨진 시각→NULL) |
| `src/dms/stepper.py` (수정) | `_submit_execution` 이 앵커 기록(제출 직후), `_poll_execution` 이 RUNNING 관측 시 기록 |
| `src/dms/repositories/metrics.py` (수정) | `job_stats` 에 sched_wait 2쿼리(`IS NOT NULL`/`IS NULL`) + 반환 키 2개 |
| `src/dms/api/routes_metrics.py` (수정) | `metrics_jobs` 에 `sched_wait_histogram`/`sched_wait_counted`/`sched_wait_excluded`(버킷 재사용) |
| `src/dms/metrics_series.py` (무수정) | `SUBMIT_WAIT_BUCKETS`·`duration_histogram` 재사용 — 새 코드 0, 테스트만 추가 |
| `frontend/src/lib/types.ts` (수정) | `JobMetrics` 에 sched 3필드 |
| `frontend/src/features/dashboard/JobStatsSection.tsx` (수정) | 「스케줄 대기(Volcano) 분포」 + 집계/제외 캡션(분포 grid 2×2 재배치) |
| `tests/test_migrations.py` (수정) | CREATE/ALTER 양쪽 + 인덱스 + 백필 부재 |
| `tests/test_repo_data_jobs.py` (수정) | 앵커·기록 write-once, 0 생존, 선독 스킵, 깨진 시각 |
| `tests/test_stepper_scan.py` / `tests/test_stepper_sync.py` (수정) | 앵커 위치, 첫 RUNNING 기록, Running 미도달 NULL, preview 미기록 |
| `tests/test_repo_metrics.py` / `tests/test_metrics_series.py` / `tests/test_api_metrics.py` (수정) | 집계·히스토그램·라우트 각 계층의 NULL 제외 + 0 생존 |
| `frontend/src/features/dashboard/JobStatsSection.test.tsx` (수정) | 두 대기 라벨 구분 + 집계/제외 캡션 |

---

### Task 1: 마이그레이션 — 컬럼 2개(이중 경로) + 커버링 인덱스, 백필 없음

**Files:**
- Modify: `src/dms/migrations.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: `_ensure_columns` 튜플 규약(`migrations.py:403-427`), "컬럼 보강 후 인덱스" 순서 규칙(:322-330), `_declared_type`/`_column_exists` 테스트 헬퍼(`tests/test_migrations.py:14-16`, `migrations.py:369-377`).
- Produces (Task 2~5 가 이 이름을 그대로 쓴다):
  - `data_jobs.exec_submitted_at TEXT`(NULL 허용) — execution vcjob 제출 직후의 앵커 시각(ISO-8601 UTC). write-once(Task 2).
  - `data_jobs.sched_wait_seconds BIGINT`(NULL 허용) — 제출 → 첫 RUNNING 관측(초). write-once(Task 3). NULL = 관측 없음(과거 잡/Running 미도달/한 틱 완료/스텁 백엔드).
  - `idx_data_jobs_created_sched ON data_jobs (created_at, sched_wait_seconds)` — Task 4 의 집계 2쿼리 커버링.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_migrations.py` 파일 끝에 추가:

```python
def test_sched_wait_columns_and_covering_index_on_fresh_db(tmp_path):
    # CREATE 경로(신규 DB). 슬라이스 20: submit_wait_seconds 선례의 이중 경로
    # 규약 그대로 -- 선언형(BIGINT/TEXT)이 CREATE 와 ALTER 두 경로에서 같아야
    # 한다(다르면 기배포 DB 만 다른 타입으로 굳는다). 인덱스는 집계 2쿼리
    # (repositories/metrics.py)의 커버링이다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    assert _declared_type(db, "data_jobs", "exec_submitted_at") == "TEXT"
    assert _declared_type(db, "data_jobs", "sched_wait_seconds") == "BIGINT"
    rows = db.query("SELECT name FROM sqlite_master WHERE type = 'index'")
    assert "idx_data_jobs_created_sched" in {r["name"] for r in rows}


def test_migrate_adds_sched_wait_columns_without_backfill(db):
    # ALTER 경로(기배포 DB) + **백필 부재 자체가 계약이다**(설계 §2.5): sched_wait
    # 의 원천(Volcano 가 잡을 Running 으로 올린 시각)은 state_transitions 어디에도
    # 없다 -- 전이 행이 온전히 남아 있어도 과거 잡은 NULL 이어야 한다. 나중에
    # 누군가 _backfill_submit_wait 를 본떠 전이 행에서 값을 "지어내는" 백필을
    # 추가하면 이 테스트가 잡는다(NULL = excluded 로 화면에 표면화되는 것이 정직).
    db.execute("DROP TABLE data_jobs")
    db.execute("""CREATE TABLE data_jobs (job_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL, operation TEXT NOT NULL, tool TEXT,
        storage_name TEXT, source_storage TEXT, destination_storage TEXT,
        source TEXT, destination TEXT, target TEXT, options TEXT NOT NULL,
        priority TEXT NOT NULL, state TEXT NOT NULL, reason_code TEXT,
        preview_fingerprint TEXT, preview_expires_at TEXT, volcano_job_ref TEXT,
        artifact_uri TEXT, result_summary TEXT, files_count BIGINT,
        bytes_count BIGINT, worker_pool TEXT, precondition TEXT,
        confirmed_fingerprint TEXT, phase_refs TEXT, submit_wait_seconds BIGINT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, options,
        priority, state, created_at, updated_at)
        VALUES ('j-old', 'r1', 'scan', '{}', 'mid', 'Succeeded',
                '2026-08-01T00:00:00Z', '2026-08-01T01:00:00Z')""")
    # 과거 잡의 전이 이력(제출 시점의 Preflight→Running 포함)이 전부 남아 있어도
    # -- Running "전이 시각"은 DMS 상태 기계의 시각이지 Volcano 스케줄 시각이
    # 아니다. 여기서 소급하면 없는 사실을 지어내는 것이다.
    for from_s, to_s, at in ((None, "Pending", "2026-08-01T00:00:00Z"),
                             ("Pending", "Preflight", "2026-08-01T00:01:00Z"),
                             ("Preflight", "Running", "2026-08-01T00:02:00Z"),
                             ("Running", "Succeeded", "2026-08-01T01:00:00Z")):
        db.execute("""INSERT INTO state_transitions (entity_kind, entity_id,
            from_state, to_state, actor, at)
            VALUES ('data_job', 'j-old', :f, :t, 'stepper', :at)""",
                   {"f": from_s, "t": to_s, "at": at})
    from dms.migrations import _column_exists, migrate
    migrate(db)
    assert _column_exists(db, "data_jobs", "exec_submitted_at")
    assert _column_exists(db, "data_jobs", "sched_wait_seconds")
    row = db.query_one("""SELECT exec_submitted_at, sched_wait_seconds
                          FROM data_jobs WHERE job_id = 'j-old'""")
    assert row["exec_submitted_at"] is None    # 앵커도 소급하지 않는다
    assert row["sched_wait_seconds"] is None   # 과거 잡은 전부 NULL(excluded 로 표면화)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_migrations.py -q`
Expected: **2 failed** — `test_sched_wait_columns_and_covering_index_on_fresh_db` 는 `_declared_type(db, "data_jobs", "exec_submitted_at")` 의 `next()` 소진으로 `StopIteration`, `test_migrate_adds_sched_wait_columns_without_backfill` 은 `assert _column_exists(db, "data_jobs", "exec_submitted_at")` 에서 `AssertionError`. 기존 테스트는 전부 PASS 유지.

- [ ] **Step 3: migrations.py 를 고친다**

**(1)** CREATE TABLE data_jobs 의 `submit_wait_seconds BIGINT,`(:178) 줄과 `created_at TEXT NOT NULL,`(:179) 줄 사이에 삽입:

```sql
            submit_wait_seconds BIGINT,
            -- 슬라이스 20(설계 §2.2): 스케줄 대기의 앵커 -- _submit_execution 이
            -- execution vcjob 제출 직후 write-once 로 남기는 시각(ISO-8601 UTC).
            -- 전이 행(scan: Preflight→Running, sync/rm: Executing→Executing 자기
            -- 전이)의 at 과 같은 틱의 값이지만, 전이 해석(세 모듈 교차 불변식)에
            -- 의존하지 않는 명시적 계약으로 컬럼에 둔다(플랜 D1). preview/preflight
            -- 제출은 이 앵커를 남기지 않는다 -- sync/rm 은 vcjob 이 둘이라 단일
            -- 컬럼에 두 대기를 섞으면 안 된다.
            exec_submitted_at TEXT,
            -- 스케줄 대기(슬라이스 20 설계 §2.2): exec_submitted_at -> 스테퍼가
            -- 처음 RUNNING 을 관측한 틱까지의 초. submit_wait_seconds(DMS 내부
            -- 픽업 지연)와 **다른 것**을 잰다 -- 이 값이 Volcano 큐 대기의
            -- **근사**다(스테퍼 틱 5s + vcjob status 갱신 지연이 더해지고,
            -- Completing 등도 RUNNING 으로 접힌다 -- execution_volcano._VCJOB_PHASE).
            -- _poll_execution 의 첫 RUNNING 관측에서 별도 UPDATE + IS NULL 술어로
            -- 한 번만 쓴다(write-once). NULL = 관측 없음(마이그레이션 이전 잡/
            -- Running 미도달 실패/한 틱 완료/스텁 백엔드) -- 백필은 불가능하다:
            -- 원천이 DB 어디에도 없다(설계 §2.5). 집계는 NULL 을 제외하고 제외
            -- 건수를 표면화한다. 0 은 정상값(같은 틱 스케줄)이다. BIGINT 는
            -- submit_wait_seconds 와 같은 규약(두 경로 동일 선언형).
            sched_wait_seconds BIGINT,
            created_at TEXT NOT NULL,
```

**(2)** `_ensure_columns` 튜플의 `("data_jobs", "submit_wait_seconds", "BIGINT"),`(:417) 아래에 추가:

```python
        # 슬라이스 20 스케줄 대기 -- submit_wait 과 같은 이중 경로 규약(슬라이스 14
        # 의 실 500 교훈: CREATE 만 고치면 기배포 DB 에서만 컬럼이 없다).
        ("data_jobs", "exec_submitted_at", "TEXT"),
        ("data_jobs", "sched_wait_seconds", "BIGINT"),
```

**(3)** `_apply_migrations` 의 `_backfill_submit_wait(db)` 호출(:330) 바로 아래에 추가:

```python
    # 슬라이스 20: sched_wait_seconds 도 CREATE(신규) 또는 _ensure_columns(구형)로
    # 보강된 뒤에만 존재하므로 인덱스는 그 이후다(idx_data_jobs_created 와 같은
    # 순서 규칙). (created_at, sched_wait_seconds) 커버링: job_stats 의 sched_wait
    # 2쿼리가 테이블을 건드리지 않는 인덱스 온리 레인지 스캔이 된다.
    # **백필 호출은 없다**(설계 §2.5): submit_wait 과 달리 원천(Volcano 가 Running
    # 으로 올린 시각)이 state_transitions 에도 없다 -- 지어내는 대신 과거 잡을
    # NULL 로 두고 화면이 제외 건수로 표면화한다(tests 가 이 부재를 계약으로 고정).
    db.execute("CREATE INDEX IF NOT EXISTS idx_data_jobs_created_sched"
               " ON data_jobs (created_at, sched_wait_seconds)")
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_migrations.py tests/test_repo_data_jobs.py -q`
Expected: 전부 PASS (repo 테스트는 `SELECT *` 경로가 새 컬럼을 그냥 실어 나를 뿐 — 회귀 확인용)

- [ ] **Step 5: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add src/dms/migrations.py tests/test_migrations.py
git commit -m "feat(db): 슬라이스 20 — exec_submitted_at·sched_wait_seconds 컬럼(이중 경로) + 커버링 인덱스, 백필 없음"
```

---

### Task 2: 앵커 기록 — `mark_exec_submitted` + `_submit_execution` 훅

**Files:**
- Modify: `src/dms/repositories/data_jobs.py`, `src/dms/stepper.py`
- Test: `tests/test_repo_data_jobs.py`, `tests/test_stepper_scan.py`, `tests/test_stepper_sync.py`

**Interfaces:**
- Consumes (Task 1): `data_jobs.exec_submitted_at`.
- Produces (Task 3 이 이 앵커를 읽는다):
  - `DataJobsRepository.mark_exec_submitted(job_id) -> None` — `UPDATE data_jobs SET exec_submitted_at = :now WHERE job_id = :j AND exec_submitted_at IS NULL`. 호출 지점은 `_submit_execution` 의 `set_phase_ref` 직후 **한 곳**.
  - preflight/preview/exec_preflight 제출 경로는 앵커를 남기지 않는다(설계 §2.2).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_repo_data_jobs.py` 파일 끝에 추가:

```python
def test_mark_exec_submitted_is_write_once(db):
    # 슬라이스 20 설계 §2.2: 앵커는 "첫 execution 제출" 시각이다. 재호출(크래시 후
    # 재제출 등)이 앵커를 뒤로 밀면 sched_wait 이 실제보다 짧게 측정된다 --
    # write-once 는 호출자 선의가 아니라 SQL 술어(IS NULL)가 강제한다.
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    assert repos.data_jobs.get_job(job_id)["exec_submitted_at"] is None
    repos.data_jobs.mark_exec_submitted(job_id)
    assert repos.data_jobs.get_job(job_id)["exec_submitted_at"] is not None
    # 이미 기록된 앵커를 식별 가능한 값으로 바꿔 두고 재호출 -- 술어가 막는다
    db.execute("UPDATE data_jobs SET exec_submitted_at = '2020-01-01T00:00:00Z' "
               "WHERE job_id = :j", {"j": job_id})
    repos.data_jobs.mark_exec_submitted(job_id)
    assert (repos.data_jobs.get_job(job_id)["exec_submitted_at"]
            == "2020-01-01T00:00:00Z")
```

**(2)** `tests/test_stepper_scan.py` 파일 끝에 추가:

```python
def test_execution_submit_records_anchor_but_preflight_does_not(db):
    # 슬라이스 20 설계 §2.2: 앵커는 execution vcjob 제출 직후다. preflight 제출
    # (틱 1)에서 남으면 preflight 파드 수행 시간이 "스케줄 대기"에 통째로 섞인다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    adapter.script(f"stub-preflight-{jid}",
                   [ExecStatus.RUNNING, ExecStatus.SUCCEEDED])
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight (preflight 제출 -- 앵커 아님)
    assert repos.data_jobs.get_job(jid)["exec_submitted_at"] is None
    stepper.run_once()   # preflight poll RUNNING -- 아직 execution 제출 전
    assert repos.data_jobs.get_job(jid)["exec_submitted_at"] is None
    stepper.run_once()   # preflight SUCCEEDED → execution 제출 + 앵커
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Running"
    assert job["exec_submitted_at"] is not None
    assert job["sched_wait_seconds"] is None     # 관측 전 -- 기록은 Task 3
```

**(3)** `tests/test_stepper_sync.py` 파일 끝에 추가:

```python
def test_sync_anchor_only_at_execution_submit_not_preview(db):
    # sync 는 vcjob 이 둘(preview + execution)이다(설계 §2.2) -- preview 제출이나
    # exec_preflight(재검증 파드) 제출에서 앵커가 남으면 단일 컬럼에 두 대기가
    # 섞여 값의 의미가 무너진다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3})
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight
    stepper.run_once()   # Preflight → PreviewRunning (preview vcjob 제출)
    assert repos.data_jobs.get_job(jid)["exec_submitted_at"] is None
    stepper.run_once()   # PreviewRunning → ConfirmPending
    fp = repos.data_jobs.get_job(jid)["preview_fingerprint"]
    repos.data_jobs.set_confirmed(jid, fp)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    stepper.run_once()   # Executing, ref 없음 → exec_preflight 제출(파드 -- 앵커 아님)
    assert repos.data_jobs.get_job(jid)["exec_submitted_at"] is None
    stepper.run_once()   # exec_preflight SUCCEEDED → execution vcjob 제출 + 앵커
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Executing"
    assert job["exec_submitted_at"] is not None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_data_jobs.py tests/test_stepper_scan.py tests/test_stepper_sync.py -q`
Expected: **3 failed** — repo 테스트는 `AttributeError: 'DataJobsRepository' object has no attribute 'mark_exec_submitted'`, 스테퍼 2건은 마지막 `assert job["exec_submitted_at"] is not None` 에서 `assert None is not None`(앞쪽 `is None` 단언들은 통과 — 아직 아무도 안 쓰므로). 기존 테스트는 PASS 유지.

- [ ] **Step 3: 저장소 메서드를 구현한다**

`src/dms/repositories/data_jobs.py` — `set_phase_ref` 메서드(:203-212) **아래**에 추가:

```python
    def mark_exec_submitted(self, job_id) -> None:
        """execution vcjob 제출 직후의 앵커 시각(슬라이스 20 설계 §2.2, 플랜 D1).
        write-once 는 SQL 술어(IS NULL)가 강제한다 -- 재제출·중복 호출이 앵커를
        뒤로 밀면 sched_wait 이 실제보다 짧게 측정된다. preview/preflight 제출
        경로는 이 메서드를 부르지 않는다(단일 컬럼에 두 vcjob 의 대기를 섞지
        않는다). updated_at 은 건드리지 않는다: 같은 틱의 뒤따르는 set_job_state
        가 어차피 같은 초의 시각을 찍고, 앵커 기록이 클레임 순서(ORDER BY
        updated_at)·GC 나이 계산에 끼어들 이유가 없다."""
        self._db.execute(
            """UPDATE data_jobs SET exec_submitted_at = :now
               WHERE job_id = :j AND exec_submitted_at IS NULL""",
            {"now": utc_now_iso(), "j": job_id})
```

- [ ] **Step 4: 스테퍼에 훅을 건다**

`src/dms/stepper.py` — `_submit_execution`(:160-173) 을 다음으로 교체(변경은 `mark_exec_submitted` 한 줄 + 주석):

```python
    def _submit_execution(self, job, running_state):
        jid = job["job_id"]
        try:
            ref = self._exec.submit(self._build_spec(job, "execution", dryrun=False))
        except ExecutionError as exc:
            self._finalize(job, DataJobState.FAILED,
                           reason_code=f"execution_submit_failed:{exc.reason_code}")
            return "Failed"
        self._repos.data_jobs.set_phase_ref(jid, "execution", ref)
        # 슬라이스 20(설계 §2.2, 플랜 D1): 스케줄 대기의 앵커 = "execution vcjob
        # 제출 직후". 전이 행(Preflight→Running/Executing→Executing)의 at 을 나중에
        # 해석하는 대신 여기서 컬럼에 직접 남긴다 -- 세 모듈 교차 불변식(자기 전이
        # 유일성)에 측정이 얹히지 않고, write-once 는 SQL 술어가 강제한다.
        # preview(_submit_preview)/preflight 제출은 앵커를 남기지 않는다.
        self._repos.data_jobs.mark_exec_submitted(jid)
        reclaimed = self._reclaim_if_terminal(job, ref)
        if reclaimed is not None:
            return reclaimed
        self._repos.data_jobs.set_job_state(jid, running_state, actor="stepper")
        return running_state.value
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_data_jobs.py tests/test_stepper_scan.py tests/test_stepper_sync.py tests/test_controller_stepper.py tests/test_stepper_enrich.py tests/test_stepper_artifact_uri.py -q`
Expected: 전부 PASS (기존 스테퍼 테스트는 앵커 컬럼을 단언하지 않아 무영향)

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add src/dms/repositories/data_jobs.py src/dms/stepper.py tests/test_repo_data_jobs.py tests/test_stepper_scan.py tests/test_stepper_sync.py
git commit -m "feat(stepper): execution vcjob 제출 앵커 exec_submitted_at — write-once(SQL 술어), preview 제출은 미기록"
```

---

### Task 3: 첫 RUNNING 관측 기록 — `record_sched_wait` + `_poll_execution` 훅

**Files:**
- Modify: `src/dms/repositories/data_jobs.py`, `src/dms/stepper.py`
- Test: `tests/test_repo_data_jobs.py`, `tests/test_stepper_scan.py`, `tests/test_stepper_sync.py`

**Interfaces:**
- Consumes (Task 1·2): `exec_submitted_at`(앵커), `sched_wait_seconds`(컬럼), `claim_steppable` 의 `SELECT *` 스냅샷(선독 공짜), `db.iso_epoch`/`utc_now_iso`(data_jobs.py:3 에 import 완료).
- Produces (Task 4 가 이 컬럼 값을 집계한다):
  - `DataJobsRepository.record_sched_wait(job: dict) -> None` — `job` 은 클레임 스냅샷. 선독 `job.get("sched_wait_seconds") is not None` 이면 즉시 반환(UPDATE 생략), 앵커 없으면 반환(NULL 유지), 음수는 0 으로 접고 깨진 시각은 NULL 유지. 기록은 `UPDATE ... SET sched_wait_seconds = :w WHERE job_id = :j AND sched_wait_seconds IS NULL`.
  - `_poll_execution` 이 `status == ExecStatus.RUNNING` 일 때만 호출(execution ref 폴링 전용 — D2).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_repo_data_jobs.py` 파일 끝에 추가:

```python
def test_record_sched_wait_zero_survives_and_predicate_blocks_overwrite(db):
    # 0 은 결측이 아니라 "같은 틱 안에 스케줄됨"이라는 가장 건강한 값이다(설계
    # §2.4). 미래 앵커(시계 스큐)를 심어 max(0, 음수) -> 0 이 되는 **결정적**
    # 경로로 0 을 만든다 -- 기록 계층에 truthy 검사(`if wait:` 따위)가 끼면
    # 여기서 NULL 로 새는 것이 잡힌다. 이어서 낡은 스냅샷(선독 통과)으로 재호출해
    # SQL 술어(IS NULL)가 최종 방어선임을 고정한다: 값이 0 이어도 "기록됨"이라
    # 덮이면 안 된다(술어가 IS NULL 아닌 falsy 로 쓰였으면 여기서 덮인다).
    from dms.db import iso_plus, utc_now_iso
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    db.execute("UPDATE data_jobs SET exec_submitted_at = :t WHERE job_id = :j",
               {"t": iso_plus(utc_now_iso(), 3600), "j": job_id})
    repos.data_jobs.record_sched_wait(repos.data_jobs.get_job(job_id))
    assert repos.data_jobs.get_job(job_id)["sched_wait_seconds"] == 0  # NULL 아님
    db.execute("UPDATE data_jobs SET exec_submitted_at = '2020-01-01T00:00:00Z' "
               "WHERE job_id = :j", {"j": job_id})
    stale = dict(repos.data_jobs.get_job(job_id))
    stale["sched_wait_seconds"] = None            # 선독을 일부러 통과시킨다
    repos.data_jobs.record_sched_wait(stale)
    assert repos.data_jobs.get_job(job_id)["sched_wait_seconds"] == 0


def test_record_sched_wait_skips_update_when_snapshot_has_value(db, monkeypatch):
    # 클레임 스냅샷 선독(설계 §2.3): 이미 기록된 잡은 매 틱 0행 UPDATE 를 반복하지
    # 않는다 -- RUNNING 이 오래 지속되는 잡이 폴링마다 DB 쓰기를 만들면 안 된다.
    # 0 도 "기록됨"이다: 선독이 truthy(`if job[...]`)로 쓰이면 0 스냅샷에서
    # UPDATE 가 나가 여기서 잡힌다.
    repos = _repos(db)
    executed = []
    orig = db.execute
    monkeypatch.setattr(
        db, "execute",
        lambda sql, params=None: (executed.append(sql), orig(sql, params))[1])
    repos.data_jobs.record_sched_wait(
        {"job_id": "j-x", "sched_wait_seconds": 7,
         "exec_submitted_at": "2026-01-01T00:00:00Z"})
    repos.data_jobs.record_sched_wait(
        {"job_id": "j-x", "sched_wait_seconds": 0,
         "exec_submitted_at": "2026-01-01T00:00:00Z"})
    assert executed == []


def test_record_sched_wait_without_anchor_or_broken_anchor_stays_null(db):
    # 앵커가 없으면(마이그레이션 이전 잡 등) 값을 지어내지 않는다(설계 §2.5).
    # 깨진 시각은 iso_epoch ValueError -> NULL 유지 -- set_job_state 의
    # submit_wait 강등 선례(data_jobs.py)와 같은 규칙(설계 §4).
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    repos.data_jobs.record_sched_wait(repos.data_jobs.get_job(job_id))  # 앵커 None
    assert repos.data_jobs.get_job(job_id)["sched_wait_seconds"] is None
    db.execute("UPDATE data_jobs SET exec_submitted_at = 'not-a-time' "
               "WHERE job_id = :j", {"j": job_id})
    repos.data_jobs.record_sched_wait(repos.data_jobs.get_job(job_id))
    assert repos.data_jobs.get_job(job_id)["sched_wait_seconds"] is None
```

**(2)** `tests/test_stepper_scan.py` 파일 끝에 추가:

```python
def test_first_running_observation_records_sched_wait_once(db):
    # 설계 §2.3: 첫 RUNNING 관측(execution ref 폴링)이 기록하고, 이후 관측은
    # 덮지 않는다. 앵커를 -120s 로 밀어 값(≈120)까지 함께 고정한다.
    from dms.db import iso_plus, utc_now_iso
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    adapter.script(f"stub-execution-{jid}",
                   [ExecStatus.RUNNING, ExecStatus.RUNNING, ExecStatus.SUCCEEDED])
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight
    stepper.run_once()   # Preflight SUCCEEDED → Running (execution 제출 + 앵커)
    db.execute("UPDATE data_jobs SET exec_submitted_at = :t WHERE job_id = :j",
               {"t": iso_plus(utc_now_iso(), -120), "j": jid})
    stepper.run_once()   # execution poll RUNNING -- 첫 관측: 기록
    wait = repos.data_jobs.get_job(jid)["sched_wait_seconds"]
    assert wait is not None and 120 <= wait <= 122   # 1초 해상도 + 실행 지연 여유
    # 두 번째 RUNNING 관측 전에 앵커를 더 밀어도 값이 그대로여야 "재계산 없음"이
    # 증명된다(write-once -- 선독 + IS NULL 술어).
    db.execute("UPDATE data_jobs SET exec_submitted_at = '2020-01-01T00:00:00Z' "
               "WHERE job_id = :j", {"j": jid})
    stepper.run_once()   # 두 번째 RUNNING 관측
    assert repos.data_jobs.get_job(jid)["sched_wait_seconds"] == wait
    stepper.run_once()   # SUCCEEDED → 종단 -- 값은 이력으로 남는다(이 슬라이스의 목적)
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded"
    assert job["sched_wait_seconds"] == wait


def test_job_finishing_without_running_stays_null(db):
    # 스텁 기본(즉시 SUCCEEDED) = 한 틱 완료 잡: RUNNING 을 관측할 기회가 없어
    # 구조적으로 NULL 이다(설계 §2.6). 스텁 백엔드(로컬·CI 기본값)에서 sched_wait
    # 가 "정직한 no-op"(설계 §4)이라는 계약이자, 스텁 기본 동작을 바꾸지 않았다는
    # 증거이기도 하다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    stepper = _stepper(repos, StubExecutionAdapter())
    stepper.run_once(); stepper.run_once(); stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded"
    assert job["exec_submitted_at"] is not None   # 앵커는 있다 -- 관측이 없었을 뿐
    assert job["sched_wait_seconds"] is None


def test_job_failing_without_running_stays_null(db):
    # Running 미도달 실패 -- 값을 지어내지 않는다(설계 §2.6). NULL 은 excluded 로
    # 집계돼 분포를 오염시키지 않는다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    adapter.script(f"stub-execution-{jid}", [ExecStatus.FAILED])
    stepper = _stepper(repos, adapter)
    stepper.run_once(); stepper.run_once(); stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Failed"
    assert job["sched_wait_seconds"] is None
```

**(3)** `tests/test_stepper_sync.py` 파일 끝에 추가:

```python
def test_preview_polling_never_records_sched_wait(db):
    # preview vcjob 폴링은 기록 경로가 아니다(설계 §2.2 -- 단일 컬럼에 두 vcjob
    # 대기를 섞지 않는다). 앵커를 **인위로 심어** "기록 가능한 상태"를 만들어
    # 둔다: 안 그러면 앵커 부재 덕에 잘못된 훅(_poll_preview 에 기록)도 우연히
    # 초록이 되는 약한 테스트가 된다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    adapter.script(f"stub-preview-{jid}", [ExecStatus.RUNNING])
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight
    stepper.run_once()   # Preflight → PreviewRunning (preview 제출)
    db.execute("UPDATE data_jobs SET exec_submitted_at = '2026-01-01T00:00:00Z' "
               "WHERE job_id = :j", {"j": jid})
    stepper.run_once()   # preview poll RUNNING -- 기록하면 안 된다
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "PreviewRunning"
    assert job["sched_wait_seconds"] is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_data_jobs.py tests/test_stepper_scan.py tests/test_stepper_sync.py -q`
Expected: repo 3건이 `AttributeError: 'DataJobsRepository' object has no attribute 'record_sched_wait'` 로 FAIL, `test_first_running_observation_records_sched_wait_once` 가 `assert wait is not None`(= `assert None is not None`)로 FAIL. `test_job_finishing_without_running_stays_null`/`test_job_failing_without_running_stays_null`/`test_preview_polling_never_records_sched_wait` 는 **처음부터 GREEN**(아직 아무도 기록하지 않으므로) — 이 세 가드는 Step 6 의 뮤테이션으로 이빨을 확인한다.

- [ ] **Step 3: 저장소 메서드를 구현한다**

`src/dms/repositories/data_jobs.py` — Task 2 의 `mark_exec_submitted` **아래**에 추가:

```python
    def record_sched_wait(self, job) -> None:
        """execution vcjob 의 첫 RUNNING 관측에서 스케줄 대기를 기록(슬라이스 20
        설계 §2.3). job 은 claim_steppable 의 SELECT * 스냅샷이다 --
        sched_wait_seconds 선독으로 이미 기록된 잡은 UPDATE 자체를 건너뛴다(매 틱
        0행 UPDATE 반복 방지). 진짜 write-once 강제는 SQL 술어(IS NULL)다: 스냅샷이
        낡아 선독을 통과해도 덮어쓰지 못한다. set_job_state 의 UPDATE 에 끼우지
        않는 이유(설계 §2.3): 그 UPDATE 는 항상 submit_wait_seconds 를 쓰므로 같은
        자리에 넣으면 매 전이 선독-보존을 지켜야 하고 한 곳만 놓쳐도 NULL 로
        덮인다.

        0 은 정상값(같은 틱 안에 스케줄됨 -- 설계 §2.4)이라 비교는 전부 is None
        이다(truthy 금지). 음수(시계 스큐)는 submit_wait 규칙 그대로 0 으로 접고
        (1초 해상도 세계에서 0 이 정직하다), 시각이 깨졌으면(iso_epoch ValueError)
        지어내지 않고 NULL 로 남긴다 -- 둘 다 set_job_state 의 강등 선례와 같은
        규칙(설계 §4). 앵커가 없는 잡(마이그레이션 이전)은 NULL 유지 -- excluded
        로 집계에 표면화된다(설계 §2.5)."""
        if job.get("sched_wait_seconds") is not None:
            return
        anchor = job.get("exec_submitted_at")
        if anchor is None:
            return
        try:
            wait = max(0, int(iso_epoch(utc_now_iso()) - iso_epoch(anchor)))
        except (TypeError, ValueError):
            return
        self._db.execute(
            """UPDATE data_jobs SET sched_wait_seconds = :w
               WHERE job_id = :j AND sched_wait_seconds IS NULL""",
            {"w": wait, "j": job["job_id"]})
```

- [ ] **Step 4: 스테퍼에 관측 훅을 건다**

`src/dms/stepper.py` — `_poll_execution` 의 머리(:175-179, 교체 범위는 `def _poll_execution` 부터 `return job["state"]` 까지)를 다음으로 교체(이하 SUCCEEDED/실패 분기는 기존 그대로):

```python
    def _poll_execution(self, job):
        ref = (job["phase_refs"] or {}).get("execution")
        status = self._exec.poll(ref)
        if status == ExecStatus.RUNNING:
            # 슬라이스 20(설계 §2.3, 플랜 D2): execution vcjob 의 첫 RUNNING 관측
            # -- 스케줄 대기를 write-once 기록한다. execution ref 를 폴링하는
            # 함수는 여기뿐이라(preview 는 _poll_preview, preflight 는
            # _poll_preflight) preview 대기가 섞일 경로가 없다. 이미 기록된 잡은
            # 스냅샷 선독으로 no-op. 기록 실패는 run_once 의 잡 단위 try/except 로
            # 격리되고 다음 틱의 RUNNING 관측이 재시도한다(설계 §4). Completing
            # 등도 RUNNING 으로 접히므로(_VCJOB_PHASE) 이 값은 근사다(설계 §2.2).
            self._repos.data_jobs.record_sched_wait(job)
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return job["state"]
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_data_jobs.py tests/test_stepper_scan.py tests/test_stepper_sync.py -q`
Expected: 전부 PASS

- [ ] **Step 6: 가드 테스트의 이빨을 확인한다(뮤테이션 RED — 반드시 되돌린다)**

**(1)** `src/dms/stepper.py` 의 `_poll_preview`(:220-243)에서 진행 중 분기

```python
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return "PreviewRunning"
```

를 **임시로**

```python
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            self._repos.data_jobs.record_sched_wait(job)
            return "PreviewRunning"
```

로 바꾸고 Run: `... -m pytest tests/test_stepper_sync.py::test_preview_polling_never_records_sched_wait -q`
Expected: **FAIL** — `assert job["sched_wait_seconds"] is None` 에서 큰 정수(2026-01-01 앵커와의 차)가 나온다. 확인 후 **원복**.

**(2)** `record_sched_wait` 의 선독 `if job.get("sched_wait_seconds") is not None:` 을 **임시로** `if job.get("sched_wait_seconds"):` 로 바꾸고 Run: `... -m pytest tests/test_repo_data_jobs.py::test_record_sched_wait_skips_update_when_snapshot_has_value -q`
Expected: **FAIL** — 0 스냅샷 호출에서 UPDATE 가 나가 `assert executed == []` 가 깨진다. 확인 후 **원복**.

원복 후 Run: `... -m pytest tests/test_repo_data_jobs.py tests/test_stepper_scan.py tests/test_stepper_sync.py -q` → 전부 PASS.

- [ ] **Step 7: 전체 백엔드 회귀를 확인한다**

Run(포그라운드, Bash timeout 600000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest -q`
Expected: 전부 PASS — 기준선 998 + Task 1 의 2 + Task 2 의 3 + Task 3 의 7 = **1010 passed**(스테퍼·마이그레이션은 공용 경로라 회귀를 여기서 잡는다)

- [ ] **Step 8: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add src/dms/repositories/data_jobs.py src/dms/stepper.py tests/test_repo_data_jobs.py tests/test_stepper_scan.py tests/test_stepper_sync.py
git commit -m "feat(stepper): 첫 RUNNING 관측에서 sched_wait_seconds 기록 — write-once·preview 미기록·0 보존"
```

---

### Task 4: 집계 — `job_stats` 에 sched_wait 2쿼리(+히스토그램 계층 0-가드 확인)

**Files:**
- Modify: `src/dms/repositories/metrics.py`
- Test: `tests/test_repo_metrics.py`, `tests/test_metrics_series.py`

**Interfaces:**
- Consumes (Task 1·3): `data_jobs.sched_wait_seconds` + `idx_data_jobs_created_sched`, `SUBMIT_WAIT_BUCKETS`/`SUBMIT_WAIT_OVERFLOW`/`duration_histogram`(`metrics_series.py:120-141` — 코드 무변경 재사용, 설계 §2.7).
- Produces (Task 5 가 이 키를 그대로 쓴다):
  - `job_stats` 반환에 `"sched_wait_seconds": list[int]`(NULL 제외 원자료)와 `"sched_wait_excluded": int`(창 안 NULL 건수) 추가 — submit_wait 페어(:150-151)와 같은 모양.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_repo_metrics.py` — `_seed_job` 헬퍼(:10-35)를 다음으로 교체(파라미터 `sched` 추가 — 기본값 유지라 기존 호출부 무영향):

```python
def _seed_job(db, repos, *, created_at, state="Succeeded", tool="dscan",
              storage="s1", dest_storage=None, requester="alice",
              reason_code=None, updated_at=None, files=None, nbytes=None,
              wait=None, sched=None):
    """data_jobs 한 행을 원하는 상태·시각으로 심는다. set_job_state는 updated_at을
    현재 시각으로 찍으므로 창(window) 테스트가 불가능하다 -- 정상 경로로 만들고
    시각·상태만 UPDATE로 덮는다. wait 는 submit_wait_seconds, sched 는
    sched_wait_seconds(둘 다 기본 NULL -- 기록 없음/진행 중과 같은 모양)."""
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
               updated_at = :u, files_count = :f, bytes_count = :b,
               submit_wait_seconds = :w, sched_wait_seconds = :sw
           WHERE job_id = :j""",
        {"st": state, "rc": reason_code, "c": created_at,
         "u": updated_at or created_at, "f": files, "b": nbytes, "w": wait,
         "sw": sched, "j": job_id})
    return job_id
```

파일 끝에 테스트 추가:

```python
def test_job_stats_sched_wait_excludes_null_and_surfaces_the_gap(db, repos):
    # NULL(과거 잡·Running 미도달·한 틱 완료·스텁 백엔드)을 0 으로 세면 분포가
    # 통째로 왜곡된다 -- 제외하되 제외 건수를 함께 낸다(설계 §2.5·§2.6: 백필이
    # 없으므로 도입 직후 화면은 "집계 0건 · 제외 N건"이 정상이고, 그 수가 보여야
    # 공백이 숨지 않는다).
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", sched=5)
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", sched=45)
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z")            # NULL
    _seed_job(db, repos, created_at="2026-07-01T00:00:00Z", sched=999) # 창 밖
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert sorted(stats["sched_wait_seconds"]) == [5, 45]
    assert stats["sched_wait_excluded"] == 1


def test_job_stats_sched_wait_zero_is_counted_not_excluded(db, repos):
    # 0 = 같은 틱 안에 스케줄된 가장 건강한 잡(설계 §2.4). 집계 술어가
    # IS NOT NULL / IS NULL 대신 falsy 검사(> 0, COALESCE(...,0) <> 0)로 퇴행하면
    # 여기서 잡힌다 -- submit_wait 의 동명 테스트와 같은 계약.
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", sched=0)
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", sched=7)
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z")            # NULL
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert sorted(stats["sched_wait_seconds"]) == [0, 7]   # 0 이 원자료에 남는다
    assert stats["sched_wait_excluded"] == 1               # NULL 만 제외 -- 0 은 아니다
```

**(2)** `tests/test_metrics_series.py` 파일 끝에 추가:

```python
def test_sched_wait_reuses_submit_buckets_and_zero_lands_in_first_bucket():
    # 슬라이스 20 은 새 버킷을 짓지 않고 SUBMIT_WAIT_BUCKETS 를 재사용한다(설계
    # §2.7 -- 두 대기 분포를 같은 축으로 나란히 비교, 실분포는 실증 후 조정).
    # 0(같은 틱 스케줄)이 첫 버킷에 남아야 한다: duration_histogram 의 가드가
    # `v is None or v < 0` 에서 `if not v` 류로 퇴행하면 여기서 잡힌다.
    from dms.metrics_series import (SUBMIT_WAIT_BUCKETS, SUBMIT_WAIT_OVERFLOW,
                                    duration_histogram)
    hist = duration_histogram([0, 12], buckets=SUBMIT_WAIT_BUCKETS,
                              overflow=SUBMIT_WAIT_OVERFLOW)
    counts = {b["bucket"]: b["count"] for b in hist}
    assert counts["<10s"] == 1      # 0 이 산다
    assert counts["10-30s"] == 1
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_metrics.py tests/test_metrics_series.py -q`
Expected: repo 2건이 `KeyError: 'sched_wait_seconds'` 로 FAIL. metrics_series 신규 1건은 **처음부터 GREEN**(가드가 이미 있다 — `metrics_series.py:129-133`) — Step 5 의 뮤테이션으로 이빨을 확인한다. 기존 테스트는 PASS 유지.

- [ ] **Step 3: metrics.py 를 고친다**

`src/dms/repositories/metrics.py` — `job_stats` 의 `excluded = ...` 쿼리(:123-126) 바로 아래에 추가:

```python
        # 스케줄 대기(슬라이스 20 설계 §2.4): submit_wait 페어와 같은 모양의
        # 2쿼리. 술어는 IS NOT NULL / IS NULL 이다 -- COALESCE(...,0) = 0 같은
        # falsy 검사로 쓰면 0(같은 틱 스케줄이라는 정상값)이 미기록으로 새 나간다.
        # 둘 다 idx_data_jobs_created_sched (created_at, sched_wait_seconds) 가
        # 커버한다 -- 테이블을 건드리지 않는 인덱스 온리 레인지 스캔.
        # excluded 에는 과거 잡(백필 없음 -- 설계 §2.5)·Running 미도달·한 틱 완료·
        # 스텁 백엔드가 모두 들어간다 -- 이 수를 내야 화면이 공백을 숨기지 못한다.
        sched_waits = self._db.query(
            """SELECT sched_wait_seconds AS w FROM data_jobs
               WHERE created_at BETWEEN :s AND :e
                 AND sched_wait_seconds IS NOT NULL""", params)
        sched_excluded = self._db.query_one(
            """SELECT COUNT(*) AS c FROM data_jobs
               WHERE created_at BETWEEN :s AND :e
                 AND sched_wait_seconds IS NULL""", params)
```

반환 dict 의 `"submit_wait_excluded": excluded["c"],`(:151) 아래에 추가:

```python
            "sched_wait_seconds": [row["w"] for row in sched_waits],
            "sched_wait_excluded": sched_excluded["c"],
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_metrics.py tests/test_metrics_series.py -q`
Expected: 전부 PASS

- [ ] **Step 5: 0-가드의 이빨을 확인한다(뮤테이션 RED — 반드시 되돌린다)**

**(1)** `metrics.py` 의 `AND sched_wait_seconds IS NOT NULL` 을 **임시로** `AND sched_wait_seconds > 0` 으로 바꾸고 Run: `... -m pytest tests/test_repo_metrics.py::test_job_stats_sched_wait_zero_is_counted_not_excluded -q`
Expected: **FAIL** — `assert sorted(stats["sched_wait_seconds"]) == [0, 7]` 가 `[7] == [0, 7]` 로 깨진다. 확인 후 **원복**.

**(2)** `metrics_series.py:132` 의 `if v is None or v < 0:` 을 **임시로** `if not v:` 로 바꾸고 Run: `... -m pytest tests/test_metrics_series.py::test_sched_wait_reuses_submit_buckets_and_zero_lands_in_first_bucket -q`
Expected: **FAIL** — `assert counts["<10s"] == 1` 이 `assert 0 == 1` 로 깨진다. 확인 후 **원복**(이 파일은 커밋 대상이 아니다 — `git status` 로 무변경 확인).

원복 후 Run: `... -m pytest tests/test_repo_metrics.py tests/test_metrics_series.py -q` → 전부 PASS.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add src/dms/repositories/metrics.py tests/test_repo_metrics.py tests/test_metrics_series.py
git commit -m "feat(metrics): job_stats 에 스케줄 대기 집계 — NULL 제외·제외 건수 표면화(0 은 정상값)"
```

---

### Task 5: 라우트 — `metrics_jobs` 에 sched_wait 분포(버킷 재사용)

**Files:**
- Modify: `src/dms/api/routes_metrics.py`
- Test: `tests/test_api_metrics.py`

**Interfaces:**
- Consumes (Task 4): `job_stats` 의 `sched_wait_seconds`/`sched_wait_excluded`, `SUBMIT_WAIT_BUCKETS`/`SUBMIT_WAIT_OVERFLOW`/`duration_histogram`(routes_metrics.py:11-13 에 import 완료 — import 변경 없음).
- Produces (Task 6 프론트가 이 키를 그대로 쓴다):
  - `GET /api/admin/metrics/jobs` 응답에 `sched_wait_histogram`(6버킷 — submit 과 같은 축)·`sched_wait_counted`·`sched_wait_excluded` 추가, `sched_wait_seconds` 원자료는 응답에서 제거(duration/submit 과 같은 규칙).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_metrics.py` 파일 끝에 추가:

```python
def test_metrics_jobs_sched_wait_distribution_and_counts(client, db):
    repos = Repositories(db)
    now = utc_now_iso()
    rid = _seed_job(db, repos, created_at=iso_plus(now, -3600))
    _seed_job(db, repos, created_at=iso_plus(now, -1800))     # NULL 유지(관측 없음)
    db.execute("UPDATE data_jobs SET sched_wait_seconds = 12 WHERE request_id = :r",
               {"r": rid})
    body = client.get("/api/admin/metrics/jobs?window=24", headers=ADMIN).json()
    hist = {b["bucket"]: b["count"] for b in body["sched_wait_histogram"]}
    # submit_wait 과 같은 축(설계 §2.7) -- 두 분포가 나란히 비교 가능해야 한다
    assert list(hist) == ["<10s", "10-30s", "30-60s", "1-5m", "5-30m", ">30m"]
    assert hist["10-30s"] == 1
    assert body["sched_wait_counted"] == 1
    assert body["sched_wait_excluded"] == 1   # 관측 없는 잡의 제외를 표면화(설계 §2.5)
    assert "sched_wait_seconds" not in body   # 원자료는 내보내지 않는다(duration 규칙)


def test_metrics_jobs_sched_wait_zero_counts_toward_the_total(client, db):
    # 라우트 층의 falsy 함정(submit_wait 선례 그대로): counted 를
    # `len([w for w in ws if w])` 로 세거나 원자료를 truthy 로 거르면 0(같은 틱
    # 스케줄 = 가장 건강한 잡)이 사라져 counted 가 조용히 줄고 첫 버킷이 빈다.
    repos = Repositories(db)
    now = utc_now_iso()
    rid = _seed_job(db, repos, created_at=iso_plus(now, -3600))
    db.execute("UPDATE data_jobs SET sched_wait_seconds = 0 WHERE request_id = :r",
               {"r": rid})
    body = client.get("/api/admin/metrics/jobs?window=24", headers=ADMIN).json()
    hist = {b["bucket"]: b["count"] for b in body["sched_wait_histogram"]}
    assert hist["<10s"] == 1
    assert body["sched_wait_counted"] == 1
    assert body["sched_wait_excluded"] == 0
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_metrics.py -q`
Expected: 신규 2건이 `KeyError: 'sched_wait_histogram'` 으로 FAIL. 기존 테스트는 PASS 유지. (주의: Task 4 이후 `job_stats` 가 `sched_wait_seconds` 키를 반환하는데 라우트가 pop 하지 않으므로, 구현 전에는 `"sched_wait_seconds" not in body` 단언도 함께 깨진다 — 정확히 라우트가 고칠 지점이다.)

- [ ] **Step 3: 라우트를 구현한다**

`src/dms/api/routes_metrics.py` — `metrics_jobs` 의 submit_wait 접기 블록(:67-70, `stats["submit_wait_histogram"] = ...` 줄) 바로 아래에 추가:

```python
    # 스케줄 대기 분포(슬라이스 20): submit_wait 과 같은 접기 -- 원자료 대신 분포만
    # 싣고, counted 는 len() 그대로다(truthy 필터를 끼우면 0 = 같은 틱 스케줄이라는
    # 가장 건강한 잡이 집계 건수에서 사라진다). 버킷은 SUBMIT_WAIT_BUCKETS 재사용
    # (설계 §2.7 -- 두 대기를 같은 축으로 나란히 비교, 실분포 확인 후 후속 조정).
    # excluded 에는 과거 잡(백필 없음)·Running 미도달·한 틱 완료·스텁 백엔드가
    # 모두 들어간다 -- 도입 직후 "집계 0건 · 제외 N건"이 정상 표시다(설계 §4).
    sched_waits = stats.pop("sched_wait_seconds")
    stats["sched_wait_counted"] = len(sched_waits)
    stats["sched_wait_histogram"] = duration_histogram(
        sched_waits, buckets=SUBMIT_WAIT_BUCKETS, overflow=SUBMIT_WAIT_OVERFLOW)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_metrics.py -q`
Expected: 전부 PASS

- [ ] **Step 5: 0-가드의 이빨을 확인한다(뮤테이션 RED — 반드시 되돌린다)**

`stats["sched_wait_counted"] = len(sched_waits)` 를 **임시로** `stats["sched_wait_counted"] = len([w for w in sched_waits if w])` 로 바꾸고 Run: `... -m pytest tests/test_api_metrics.py::test_metrics_jobs_sched_wait_zero_counts_toward_the_total -q`
Expected: **FAIL** — `assert body["sched_wait_counted"] == 1` 이 `assert 0 == 1` 로 깨진다. 확인 후 **원복**하고 재실행 → PASS.

- [ ] **Step 6: 전체 백엔드 회귀를 확인한다**

Run(포그라운드, Bash timeout 600000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest -q`
Expected: 전부 PASS — 기준선 998 + 누적 신규 17(마이그레이션 2, repo 4, 스테퍼 5, 집계 2, 히스토그램 1, 라우트 2 — 정확 수는 실행이 확정한다) = **1015 passed**

- [ ] **Step 7: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add src/dms/api/routes_metrics.py tests/test_api_metrics.py
git commit -m "feat(api): metrics/jobs 에 스케줄 대기 분포 — SUBMIT_WAIT_BUCKETS 재사용·counted=len()"
```

---

### Task 6: 프론트 — 「스케줄 대기(Volcano)」 분포 + 집계/제외 캡션

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/features/dashboard/JobStatsSection.tsx`
- Test: `frontend/src/features/dashboard/JobStatsSection.test.tsx`

**Interfaces:**
- Consumes (Task 5): `/api/admin/metrics/jobs` 의 `sched_wait_histogram`/`sched_wait_counted`/`sched_wait_excluded`.
- Produces: 「잡 통계」에 「스케줄 대기(Volcano) 분포」 — 「제출 대기 분포」 옆(2×2 grid 아래 행에 나란히, 설계 §3) + "집계 N건 · 제외(기록 없음) N건" 캡션 + 근사 오차 명시(설계 §2.2). 라이브 `QueueSection` 은 무변경 — 라이브 스냅샷과 이력 창 집계는 어긋나는 것이 정상이고 기존 라벨이 이미 구분한다(설계 §3).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/features/dashboard/JobStatsSection.test.tsx` — `STATS` 상수의 `submit_wait_counted: 3, submit_wait_excluded: 1,`(:35) 줄 아래에 삽입:

```tsx
  sched_wait_histogram: [
    { bucket: "<10s", count: 1 }, { bucket: "10-30s", count: 1 },
    { bucket: "30-60s", count: 0 }, { bucket: "1-5m", count: 0 },
    { bucket: "5-30m", count: 0 }, { bucket: ">30m", count: 0 }],
  sched_wait_counted: 2, sched_wait_excluded: 4,
```

파일 끝에 테스트 추가:

```tsx
test("스케줄 대기(Volcano) 분포가 제출 대기와 구분돼 나온다", async () => {
  renderSection();
  const chart = await screen.findByRole("img", { name: "스케줄 대기(Volcano) 분포" });
  expect(chart.querySelectorAll("rect")).toHaveLength(6);
  // 두 대기의 라벨이 한 화면에서 구분된다 -- getByText 는 유일 매치를 강제하므로
  // 라벨이 같은 문자열로 뭉치면 여기서 터진다(설계 §3: 「제출 대기」 옆에
  // 나란히, 서로 다른 이름으로 -- 슬라이스 17 이 queue_wait 라벨을 정정한 교훈).
  expect(screen.getByText("제출 대기 분포")).toBeInTheDocument();
  expect(screen.getByText("스케줄 대기(Volcano) 분포")).toBeInTheDocument();
  // 집계/제외 캡션(설계 §2.5: 백필이 없으므로 도입 직후 "제외 N건"이 정상 표시
  // -- 숨으면 "데이터 없음"처럼 보인다) + 근사 오차 명시(설계 §2.2).
  expect(screen.getByText(/집계 2건 · 제외\(기록 없음\) 4건/)).toBeInTheDocument();
  expect(screen.getByText(/Volcano 큐 대기의 근사/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx vitest run src/features/dashboard/JobStatsSection.test.tsx`
Expected: 신규 1건 FAIL — `findByRole` 타임아웃: `Unable to find an accessible element with the role "img" and name "스케줄 대기(Volcano) 분포"`. 기존 4건은 PASS 유지.

- [ ] **Step 3: 타입과 카드를 구현한다**

**(1)** `frontend/src/lib/types.ts` — `JobMetrics` 의 `submit_wait_excluded: number;`(:189) 줄 아래에 추가:

```ts
  // 스케줄 대기(슬라이스 20): execution vcjob 제출(exec_submitted_at) -> 스테퍼가
  // 처음 RUNNING 을 관측한 틱. 제출 대기(DMS 픽업 지연)와 다른 것을 재며,
  // Volcano 큐 대기의 **근사**다(스테퍼 틱 5s + vcjob status 갱신 지연 포함 --
  // 설계 §2.2). excluded = NULL(과거 잡: 백필 없음 §2.5, Running 미도달/한 틱
  // 완료 §2.6, 스텁 백엔드 §4) 제외 건수 -- 0 과 절대 같지 않다.
  sched_wait_histogram: { bucket: string; count: number }[];
  sched_wait_counted: number;
  sched_wait_excluded: number;
```

**(2)** `frontend/src/features/dashboard/JobStatsSection.tsx` — `submitWaits` 정의(:81-82) 아래에 추가:

```tsx
  const schedWaits = asArray<{ bucket: string; count: number }>(d?.sched_wait_histogram)
    .map((b) => ({ label: b.bucket, value: b.count }));
```

**(3)** 같은 파일 — 분포 grid 전체(:92-112, `<div className="grid md:grid-cols-3 gap-4 mt-3">` 부터 그 닫는 `</div>` 까지)를 다음으로 교체(바로 뒤의 Breakdown grid `md:grid-cols-3`(:113)은 건드리지 않는다):

```tsx
      {/* 2×2 배치(슬라이스 20): 아래 행에 두 "대기" 분포가 나란히 온다 --
          「제출 대기」(created_at→첫 비-Pending, DMS 픽업 지연)와 「스케줄
          대기(Volcano)」(execution 제출→첫 RUNNING 관측)는 다른 것을 잰다(설계
          §2.2). 4열로 눌러 넣으면 md 폭에서 차트가 읽히지 않아 2열 줄바꿈을
          택했다. */}
      <div className="grid md:grid-cols-2 gap-4 mt-3">
        <div>
          <h3 className="font-medium mb-2 text-sm">처리량</h3>
          <BarChart data={throughput} label="처리량" />
        </div>
        <div>
          <h3 className="font-medium mb-2 text-sm">수행시간 분포</h3>
          <BarChart data={durations} label="수행시간 분포" />
        </div>
        <div>
          <h3 className="font-medium mb-2 text-sm">제출 대기 분포</h3>
          <BarChart data={submitWaits} label="제출 대기 분포" />
          {/* 수행시간(created_at -> updated_at)은 이 대기를 포함한 전체 수명이다 --
              나란히 놓인 두 분포의 포함 관계를 화면에 명시한다(설계 §2.4). 제외
              건수(NULL)는 백필 공백 -- 숨기지 않는다(설계 §3). 한 개의 템플릿
              리터럴 = 한 개의 텍스트 노드(getByText 가 통으로 찾도록). */}
          <p className="text-muted text-xs mt-1">
            {`집계 ${d?.submit_wait_counted ?? 0}건 · 제외(기록 없음) ${d?.submit_wait_excluded ?? 0}건 — 수행시간 분포는 이 대기를 포함합니다`}
          </p>
        </div>
        <div>
          <h3 className="font-medium mb-2 text-sm">스케줄 대기(Volcano) 분포</h3>
          <BarChart data={schedWaits} label="스케줄 대기(Volcano) 분포" />
          {/* 제출 대기와 같은 캡션 패턴(집계/제외 -- 설계 §3) + 근사 오차 명시
              (설계 §2.2: 스테퍼 틱 5s + vcjob status 지연이 더해진 근사이지
              PodGroup 이 보고하는 값이 아니다). 제외(기록 없음)에는 과거 잡(백필
              없음 §2.5)·Running 미도달·한 틱 완료·스텁 백엔드가 모두 들어간다 --
              도입 직후 "집계 0건 · 제외 N건"이 정상이며, 이 수가 보여야 화면이
              "데이터 없음"으로 거짓말하지 않는다(설계 §2.6). */}
          <p className="text-muted text-xs mt-1">
            {`집계 ${d?.sched_wait_counted ?? 0}건 · 제외(기록 없음) ${d?.sched_wait_excluded ?? 0}건 — 제출 대기(DMS 픽업 지연)와 달리 Volcano 큐 대기의 근사입니다(스테퍼 틱 5초 오차)`}
          </p>
        </div>
      </div>
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx vitest run` 그리고 `npx tsc -b`
Expected: 전체 PASS — 기준선 215 + 신규 1 = **216 passed / 48 files**, 타입 에러 0

- [ ] **Step 5: 라벨 구분 테스트의 이빨을 확인한다(뮤테이션 RED — 반드시 되돌린다)**

`JobStatsSection.tsx` 의 새 h3 텍스트 `스케줄 대기(Volcano) 분포` 를 **임시로** `제출 대기 분포` 로 바꾸고 Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx vitest run src/features/dashboard/JobStatsSection.test.tsx`
Expected: **FAIL** — `getByText("제출 대기 분포")` 가 `Found multiple elements with the text: 제출 대기 분포` 로 터진다(두 라벨이 뭉치면 잡힌다는 증명). 확인 후 **원복**하고 재실행 → PASS.

- [ ] **Step 6: 최종 회귀(백엔드+프론트)를 확인한다**

Run(포그라운드, Bash timeout 600000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest -q`
Expected: **1015 passed**(998 + 17)
Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx vitest run && npx tsc -b`
Expected: **216 passed / 48 files**, 타입 에러 0

- [ ] **Step 7: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add frontend/src/lib/types.ts frontend/src/features/dashboard/JobStatsSection.tsx frontend/src/features/dashboard/JobStatsSection.test.tsx
git commit -m "feat(portal): 잡 통계에 스케줄 대기(Volcano) 분포 — 제출 대기와 구분 라벨 + 집계/제외 캡션"
```

---

## 플랜 이후: 배포·실증 (설계 §6 — 별도 ops, 플랜 밖)

플랜 실행(6태스크 커밋)이 끝나면 컨트롤러가 테스트베드에서 수행한다 — 플랜 태스크가 아니고 이 워크트리에서는 kubectl 을 쓰지 않는다. 에이전트·RBAC·매니페스트는 이번에 안 바뀌었으므로 `dms` 이미지만 범프한다(이미지는 `install/docker/Dockerfile.testbed` 로 빌드 — deploy/Dockerfile 은 kubectl 이 없다). initContainer 의 migrate 가 컬럼 2개와 인덱스를 ALTER 경로로 보강한다.

1. (§6-3 선행) 배포 직후, 새 잡 제출 **전**: `/api/admin/metrics/jobs` 의 `sched_wait_counted == 0` 이고 과거 잡이 **전부** `sched_wait_excluded` 로 잡히는지 — 화면이 "집계 0건 · 제외 N건"으로 나오고 "데이터 없음"처럼 보이지 않는지(백필 부재의 정직한 표시).
2. (§6-1) 실 sync 잡에서 sched_wait 가 기록되는지 — **0 이 아닌 값**(큐가 붐빌 때: 동시 제출 여러 건)과 **0**(한가할 때: 같은 틱 스케줄) 양쪽. DB 직접 확인: `SELECT job_id, exec_submitted_at, sched_wait_seconds FROM data_jobs ORDER BY created_at DESC LIMIT 10`.
3. (§6-2) 0초 기록이 집계에 살아남는지 — 같은 틱 스케줄 잡이 counted 에 포함되고 히스토그램 첫 버킷(`<10s`)에 나타나는 것을 화면에서 확인(falsy 검사 잔존의 최종 검증).
4. (§6-4) write-once — 같은 잡이 여러 틱 RUNNING 으로 관측돼도 `sched_wait_seconds` 가 변하지 않는지(폴링 중 DB 를 두 번 읽어 비교).
5. (§6-5) 실패한 잡(Running 미도달 — 예: 존재하지 않는 경로로 preflight 통과 후 즉시 실패하는 잡)이 NULL 로 남고 counted 를 오염시키지 않는지.
6. 라이브 `QueueSection`(PodGroup)과 이력 분포가 어긋나는 것을 확인하고 — 그것이 정상임을(라이브 = 무윈도 스냅샷, 이력 = 창 집계) 라벨이 설명하는지.

## Self-Review

**1. 설계 커버리지**

| 설계 절 | 담당 |
|---|---|
| §1 실측 전제(스테퍼가 이미 vcjob 을 GET, DB state ≠ 실행 시작, 자기 전이 실재, claim SELECT *, 0-가드 5곳, stub 기본, iso_epoch ValueError) | 실측 고정값 표 — 전부 이 워크트리에서 파일을 열어 재확인한 좌표다 |
| §2.1 PodGroup 을 읽지 않는다(RBAC 변경 0, k8s 호출 추가 0) | 어떤 태스크도 k8s·RBAC·queue_reader 를 건드리지 않는다 — 관측은 기존 `_poll_execution` 의 poll 결과 재사용 |
| §2.2 정의(execution 제출 → 첫 RUNNING 관측), preview 미측정, 근사 오차 명시 | Task 2(앵커 — D1 로 저장 위치만 컬럼으로 확정)·Task 3(관측+preview 가드)·Task 6(캡션)·컬럼 주석(Task 1) |
| §2.3 write-once 별도 UPDATE + IS NULL 술어 + 스냅샷 선독 스킵 | Task 3(`record_sched_wait` + 스킵 테스트 + 뮤테이션 RED) — `set_job_state` 의 UPDATE 는 무변경 |
| §2.4 0 은 정상값 — 가드 4곳 복제 | Task 3(기록: is None 선독·max(0,·)·ValueError→NULL) / Task 4(집계: IS NOT NULL·IS NULL + 히스토그램 가드 확인) / Task 5(라우트: len()) — 각 계층에 0 생존 테스트 + 뮤테이션 RED 스텝 |
| §2.5 백필 불가 — excluded 표면화 | Task 1(백필 부재를 계약으로 고정하는 테스트) + Task 4/5(excluded) + Task 6(캡션) + 실증 1 |
| §2.6 관측 못 하는 잡 = 구조적 NULL | Task 3(한 틱 완료·Running 미도달 테스트) |
| §2.7 SUBMIT_WAIT_BUCKETS 재사용 | Task 4(테스트)·Task 5(라우트) — 새 버킷 0, metrics_series 코드 변경 0 |
| §3 화면(라벨 「스케줄 대기(Volcano)」, 제출 대기 옆, 집계/제외 캡션, 라이브와 어긋남 정상) | Task 6 — QueueSection 무변경 |
| §4 오류 처리(깨진 시각 NULL, 음수→0, 잡 단위 격리, 스텁 no-op) | Task 3(테스트 3건 + `_poll_execution` 주석) |
| §5 테스트 목록 전항목 | write-once(T2·T3), 0 생존(T3·T4·T5), NULL/excluded(T3·T4), preview 미기록(T3+뮤테이션), 마이그레이션 양쪽+인덱스+백필 부재(T1), 스텁 script RUNNING/기본 무기록(T3), 프론트 라벨 구분+캡션(T6) |
| §6 실증 | 플랜 이후 절(관례 — 플랜 태스크 아님, kubectl 은 ops) |
| §7 하지 않는 것 | PodGroup 샘플링·RBAC 확장·preview 측정·conditions 해석·백필·runs 부활·경보 — 어떤 태스크도 건드리지 않는다. `test_rbac_contract.py:92-99` 의 컨트롤러 무권한 계약 유지(RBAC 파일 무변경) |

**2. 플레이스홀더 점검** — "TBD"/"적절히 처리"/코드 없는 테스트 지시 없음. 모든 코드 스텝에 실제 전문 코드 블록이 있고, 반복되는 캡션·쿼리도 그대로 반복 수록했다. 태스크 간 참조는 Interfaces 의 정확한 시그니처로만 한다.

**3. 타입·철자 일관성** — `exec_submitted_at`/`sched_wait_seconds` 는 CREATE(T1)·`_ensure_columns`(T1)·저장소(T2·T3)·테스트 시드(T4)·라우트 pop(T5) 전부 동일 철자. `mark_exec_submitted`/`record_sched_wait` 는 T2/T3 정의 → 스테퍼 호출이 같은 이름. API 키는 일관되게 `sched_wait_*`(histogram/counted/excluded)이고 T6 의 `JobMetrics`·JSX·테스트 STATS 가 그대로 쓴다. 인덱스명 `idx_data_jobs_created_sched` 는 T1 생성·테스트 단언 동일. `SUBMIT_WAIT_BUCKETS`/`SUBMIT_WAIT_OVERFLOW` 는 기존 정의(:120-122)를 T4 테스트·T5 라우트가 같은 이름으로 쓴다(라우트는 이미 import 됨 — import 변경 0).

**4. RED 규율** — 각 태스크의 실패 확인 스텝에 기대 실패 메시지를 명시했고(StopIteration/AttributeError/`assert None is not None`/KeyError/findByRole 타임아웃), **처음부터 GREEN 인 가드 테스트 4건**(preview 미기록, 선독 스킵의 truthy, 히스토그램 0, 라벨 구분)은 각각 뮤테이션 RED 스텝(T3-6, T4-5, T5-5, T6-5)으로 이빨을 실증한 뒤 원복한다 — "붙잡는다는 주장은 RED 를 볼 때까지 추측"(슬라이스 17 교훈).

**알려진 위험:**
- **D1 이 설계 §2.2 의 앵커 저장 위치를 바꾼다**: 전이 행 대신 컬럼. 측정 정의·write-once·preview 배제는 그대로이고, 근거(세 모듈 교차 불변식 회피·클레임 선독 공짜·추가 SELECT 0)를 D1 에 실측으로 적었다. 설계 소유자가 원문 유지를 원하면 D1 의 SQL 로 Task 2·3 만 교체하면 된다(나머지 태스크는 무관).
- **타이밍 여유 `120 <= wait <= 122`**(T3): 1초 해상도 + 틱 사이 초 경계 통과를 허용하는 submit_wait 테스트 선례(`test_repo_data_jobs.py:210`) 그대로다. 극단적으로 느린 CI 에서 122 를 넘으면 여유를 넓히되 하한 120 은 유지할 것(값 자체의 검증이 목적).
- **`monkeypatch.setattr(db, "execute", ...)`**(T3 스킵 테스트): `Database` 인스턴스 속성 교체 — 표준 monkeypatch 사용이고 테스트 종료 시 자동 원복되지만, 람다 시그니처는 `execute(sql, params=None)`(db.py:57) 와 일치해야 한다(플랜 코드가 이미 일치).
- **grid 2×2 재배치**(T6): 기존 테스트는 grid 열 수를 단언하지 않아(내용 텍스트만) 무영향 — 단 스크린샷 기반 검증을 하는 외부 절차가 있다면 배치 변화를 알릴 것.
- **`_seed_job`(test_repo_metrics) 시그니처 확장**: 기본값 유지라 기존 호출부 무영향. 같은 이름의 헬퍼가 `test_api_metrics.py` 에도 따로 있다(그쪽은 확장하지 않고 직접 UPDATE — submit_wait 선례와 동일) — 두 파일을 혼동하지 말 것.
- **Completing→RUNNING 접힘**: 틱 사이에 끝나가는 잡의 관측이 늦게 잡혀 값이 약간 커질 수 있다 — 설계 §2.2 가 "정직한 오차"로 수용했고 컬럼 주석·화면 캡션이 "근사"를 명시한다.
- **테스트 수 기대치(1015/216)는 근사** — 정확 수는 실행이 확정한다. 기준선(998/215)에서 줄어드는 일이 없어야 한다는 것이 진짜 계약이다.
