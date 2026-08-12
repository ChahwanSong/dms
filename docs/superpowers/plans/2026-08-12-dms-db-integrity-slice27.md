# 슬라이스 27 — DB 정합성 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DB 정합성의 두 구멍을 닫는다. **항목 A** — 死物 테이블 `runs`(전 소스·테스트에서 읽기/쓰기 0건, 슬라이스 17 이 부활을 명시 배제) 를 제거한다: CREATE 목록·`ALL_TABLES` 에서 삭제 + CREATE 실행 루프 **뒤**에 `DROP TABLE IF EXISTS runs`(기배포 DB 경로). `len(ALL_TABLES)` 는 20 → **19** — 이 저장소 **최초의 파괴적 마이그레이션**이다. **항목 B** — `finalize_from_job` 을 원자화한다: `set_state`(전이+state 갱신)와 `record_result`(results INSERT)가 별도 커밋이라 그 사이 크래시하면 "요청은 종단인데 results 행이 없다"(슬라이스 24 실증에서 실제 관측, BACKLOG §2.1) — 종단 요청은 고아 스윕(`terminal_jobs_with_live_request`)의 시야 밖이라 그 결손은 **영구**였다. 두 쓰기를 한 트랜잭션으로 묶어 크래시 시 둘 다 롤백 → 요청이 비종단으로 남아 다음 틱 재시도가 완주한다. 새 pip/npm 의존성 0, 새 테이블 0, 새 컬럼 0, 새 사유 코드 0, 프론트 diff 0.

**Architecture:** 항목 A 는 `migrations.py` 단독(스키마 선언 + 정리 절) — DROP 은 CREATE 실행 루프 뒤가 계약이다(부활 CREATE 가 실수로 되돌아와도 "생성 후 삭제"는 '없음'으로 수렴하고, 역순이면 매 migrate 가 부활시킨다). CREATE 문 삭제 후엔 이 순서가 기능 테스트로 구분되지 않으므로 **소스 순서 계약 테스트**(슬라이스 25 `test_diag_logs_is_declared...` 선례)로 고정한다. 항목 B 는 `repositories/requests.py` 단독 — **함정 실측**: `db.transaction()` 은 중첩을 모른다(`BEGIN` 이 무조건, `db.py:180`; `_txn_depth` 는 재연결 재시도 억제용이지 중첩 지원이 아니다). `set_state` 가 자체 트랜잭션(`requests.py:78`)을 열므로 162-163 을 그대로 `with transaction():` 으로 감싸면 **sqlite(전 테스트)는 중첩 BEGIN 에서 즉사하고, PG(autocommit)는 경고만 낸 채 안쪽 COMMIT 이 바깥 트랜잭션을 조기 커밋해 조용히 비원자가 된다**. 따라서 전이의 문장 몸통을 트랜잭션 없는 `_apply_state` 로 추출하고, `set_state`(단독 경계)와 `finalize_from_job`(전이+results 합동 경계)이 각자 경계를 소유한다. 멱등 가드(이미 종단이면 return)는 읽기 후 조기 반환이라 트랜잭션 **밖** — 안에 넣으면 고아 스윕이 매 틱 재호출하는 no-op 마다 빈 BEGIN/COMMIT 이 열린다.

**Tech Stack:** Python 3.11 표준 라이브러리만(신규 import 는 테스트의 `inspect` 뿐). 프론트·e2e 무접촉(백엔드 계약 무변경 — /api 응답 형태 그대로). DB 는 테이블 1개 삭제 — `tests/test_migrations.py` 의 `len(ALL_TABLES) == 20` 단언은 **2곳**(`:173`, `:457`) 모두 19 로 갱신한다(과제 지시는 1곳으로 표기했으나 실측 2곳 — 아래 「전제 재확인」).

## Global Constraints

- **설계 문서 없음** — 이 슬라이스의 「왜」는 BACKLOG(§2.1 finalize 비원자 관찰, `runs` 死物 항목)와 이 플랜의 「전제 재확인」이 담는다. 플랜과 코드 실측이 충돌하면 실측이 이긴다.
- **새 pip/npm 의존성 금지. 새 테이블·새 컬럼·새 사유 코드 0.** `frontend/` 는 어떤 파일도 건드리지 않는다(Task 3 이 `git diff` 로 실측 확인). 신규 파일 0 — 수정 4파일뿐이라 `git add` 자체가 없다.
- **커밋은 pathspec 으로 한정한다**: 항상 `git commit -m "..." -- <경로들>`. `git add -A`·`git add .`·`git commit -a` **금지**(워크트리 공유 중 인덱스 섞임 사고). 커밋 메시지 말미에 반드시:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq`
- **뮤테이션 원복에 `git checkout` 금지** — 뮤테이션 전 `cp <파일> /tmp/slice27-<파일명>.bak` 으로 사본을 뜨고, 확인 후 `cp` 로 되돌린다.
- **origin push 금지, 브랜치 변경 금지**(현재 `worktree-dms-slice22plus`, HEAD 70561a8 = origin/main), **플랜 태스크에서 `deploy/k8s` 무접촉**(d38 범프는 「플랜 이후: 배포·실증」의 첫 단계). `docs/` 아래는 이 플랜 파일 외 생성·수정 금지(실증 후 BACKLOG 갱신은 플랜 밖 관례).
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- 백엔드 테스트 명령(이 워크트리 전용, `.venv` 는 워크트리 밖 공용):
  `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest <대상> -q`
  전체 스위트는 **포그라운드**, Bash timeout 900000ms. **기준선 1254 passed(~434s).**
- 프론트는 **확인만**: `cd frontend && npx vitest run && npx tsc -b` — diff 0 이므로 현행 기준선 그대로 초록이어야 한다. e2e 도 확인만: `npm run test:e2e` **9 passed** 유지(Task 3 게이트).
- 주석은 **한국어**로 「왜」를 적는다.

## 전제 재확인 (2026-08-12, 코드 직접 실측)

과제가 제시한 실측 사실 전부를 코드로 재확인했다 — **결론 요지는 전부 유지**되고, 정정 2건 + 추가 발견 3건이다.

| 전제 | 재확인 결과 |
|---|---|
| 1. `runs` CREATE 는 `migrations.py:108-115`, `ALL_TABLES`(`:4-12`) 3번째, 전 소스·테스트 사용 0건 | ✓ 전부 유지. `grep -rn '\bruns\b' src/ tests/` 히트는 `migrations.py:5`(목록)·`:108`(CREATE) 정확히 2건 — INSERT/SELECT/UPDATE/DELETE/JOIN 0건 재확인. 슬라이스 17 부활 배제는 BACKLOG `:90`·`:783` |
| 2. `tests/test_migrations.py` 가 `len(ALL_TABLES) == 20` 단언 | **정정: 단언은 2곳이다** — `test_migrate_is_idempotent`(`:173`)와 슬라이스 25 가 추가한 `test_all_tables_is_still_twenty_tables_not_columns`(`:454-457`). 둘 다 19 로 갱신하고 후자는 **이름도** `..._nineteen_...` 으로 바꾼다(이름이 수를 거짓말하면 안 된다) |
| 3. `finalize_from_job` 구조: 멱등 가드 `:160-161`, `set_state`+`record_result` `:162-163`, `record_result` 는 단일 INSERT | ✓ 유지. 가드 읽기는 `:156-158`, 종단 조기 반환 `:160-161`, 두 쓰기 `:162-163`, `record_result` 단일 INSERT `:119-127` |
| 4. `set_state` 의 내부 트랜잭션 · `db.py` `_txn_depth` | **최대 정정 — naive wrap 은 불가능하다.** `set_state` 는 `:78` 에서 `with self._db.transaction():` 을 연다. `db.transaction()`(`db.py:172-209`)은 **비중첩**: `BEGIN` 이 무조건(`:180`)이고 `_txn_depth`(`:49-52`, `:123`)는 "트랜잭션 중 재연결·재시도 금지" 게이트일 뿐 중첩(SAVEPOINT) 지원이 아니다. 실측: sqlite 중첩 BEGIN 은 `OperationalError: cannot start a transaction within a transaction`(테스트 전멸), PG autocommit 은 경고만 내고 **안쪽 COMMIT 이 바깥 트랜잭션을 조기 커밋** — 감싼 것처럼 보이는데 record_result 는 트랜잭션 밖에서 돈다(조용한 비원자, 최악의 형태). → 경계/몸통 분리(`_apply_state` 추출)로 구현한다(Task 2) |
| 5. finalize 호출처의 기존 트랜잭션 여부 | **신규 실측**: 호출처 7곳(`stepper.py:160`, `controller.py:39·51`, `routes_jobs.py:59·85`, `routes_requests.py:158·172`, `routes_batches.py:129`) 전부 트랜잭션 밖 — finalize 가 새로 여는 트랜잭션이 어느 호출처에서도 중첩되지 않는다. `batch_orchestrator.py:44` 의 트랜잭션은 batches 저장소만 만진다 |
| 6. "원자화하면 UniqueViolation 재시도 창도 닫히는가" | **닫힌다.** 원자화 후 크래시는 전부-또는-전무라 "results 는 있는데 요청은 비종단"(재시도가 results PK 중복에 걸리는 유일한 상태)이 **구조적으로 불가능**해진다. 단 수동 DB 조작으로 그 상태를 만들면(슬라이스 24 재현이 그랬다) 거동이 바뀐다 — 열린 질문 2 |
| 7. 배포 태그 | ✓ 실측: 제어면 `dms` 5곳 전부 **d37**(`30-migrate-job.yaml:25`, `40-api.yaml:67·84`, `41-controller.yaml:35·52`), `DMS_JOB_IMAGE`(`20-config.yaml:22`)·`dms-agent`(`50-agent-daemonset.yaml:72`)는 d35 — 이 슬라이스는 **제어면만 d38**, 러너·에이전트 무접촉 |

**추가 발견(과제 지시에 없던 것):**

- **`migrations.py:1` 모듈 docstring 이 "20개 테이블"을 박제하고 있다** — 함께 19 로 갱신한다(Task 1). 문서가 수를 거짓말하면 다음 파괴적 마이그레이션 때 또 헤맨다.
- **planner 에 동종 비원자 쌍이 2곳 더 있다**: `planner.py:73-77`(`_reject`)과 `:130-135`(conflict 경로)도 `set_state` + `record_result` 를 별도 커밋으로 친다. 이번 슬라이스는 "두 항목만"이라 무접촉 — 단 Task 2 의 `_apply_state` 추출로 후속 원자화가 각각 2줄이 된다(열린 질문 1).
- **DROP 순서의 소스 계약은 CREATE 리터럴 위치 비교로는 부족하다** — `stmts` 리스트 **정의**와 실행 **루프** 사이에 DROP 을 끼우면 텍스트로는 CREATE 문자열들보다 뒤인데 실행은 앞이다. 계약은 `for stmt in stmts` 루프 위치 대비로 건다(Task 1 Step 1 의 테스트 주석에 근거 명시).
- BACKLOG `:97` 의 "`migrations.py:69`가 만들지만"은 낡은 라인 표기다(현행 `:108`) — 실증 후 BACKLOG 갱신 때 함께 정정한다(플랜 밖).

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/migrations.py` (수정) | Task 1: `runs` CREATE 삭제 + `ALL_TABLES` 에서 삭제 + CREATE 루프 뒤 `DROP TABLE IF EXISTS runs` + 모듈 docstring 19 |
| `tests/test_migrations.py` (수정) | Task 1: 기존 DB DROP 경로·신규 DB 부재·소스 순서 계약 + `len == 19` 2곳 갱신 |
| `src/dms/repositories/requests.py` (수정) | Task 2: `_apply_state` 추출(경계/몸통 분리) + `finalize_from_job` 원자화(가드는 밖) |
| `tests/test_repo_requests_finalize.py` (수정) | Task 2: 크래시 주입 전부-또는-전무 계약 + 멱등 조기 반환 무트랜잭션 계약 |

---

### Task 0: 기준선 확인 (커밋 없음)

**Files:** 없음(검증만)

**Interfaces:** 이후 모든 태스크의 판정 기준(기준선 초록)을 만든다.

- [ ] **Step 1: 백엔드 기준선**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: `1254 passed` (약 434s). 여기 빨강이면 이 슬라이스 밖의 문제다 — 진행 전에 보고.

- [ ] **Step 2: 프론트·e2e 기준선 (확인만 — 이 슬라이스는 무접촉)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b && npm run test:e2e`
Expected: vitest 전 파일 초록, tsc 무출력 exit 0, e2e `9 passed`. 수치를 기록해 둔다 — Task 3 에서 동일해야 한다(diff 0 의 증거).

---

### Task 1: 항목 A — 死物 `runs` 제거 (최초의 파괴적 마이그레이션)

**Files:**
- Modify: `src/dms/migrations.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces: `runs` 부재 — 신규 DB(CREATE 목록에서 삭제)와 기배포 DB(`DROP TABLE IF EXISTS`) 두 경로가 같은 결과로 수렴한다. `ALL_TABLES` 19개.
- **함정 명시 2건**: ① 빈 DB 경로와 기존 DB 경로는 **다른 코드**다(슬라이스 25 교훈의 삭제판 — 그때는 CREATE/`_ensure_columns` 양쪽 추가, 이번엔 목록 삭제/DROP 양쪽 제거). CREATE 만 지우면 기배포 DB(실 테스트베드 포함)에는 `runs` 가 영원히 남는다. ② CREATE 문을 지운 뒤에는 DROP 의 **위치**가 기능 테스트로 구분되지 않는다(어디 있든 지금은 runs 가 없다) — 순서 계약의 실질은 미래(부활 CREATE 가 머지·롤백 실수로 되돌아왔을 때 "생성 후 삭제"만이 '없음'으로 수렴)라, 소스 순서 계약 테스트만이 이빨이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_migrations.py` — 파일 끝에 추가:

```python
def test_migrate_drops_runs_from_existing_db(db):
    # 슬라이스 27: 기배포 DB 경로. 빈 DB 는 CREATE 목록에서 빠진 것으로 끝나지만
    # 기존 DB 는 이미 만들어진 runs 를 DROP 이 지워야 한다 -- 두 경로는 다른
    # 코드다(슬라이스 25 diag_logs 의 양쪽 규약과 같은 교훈, 방향만 삭제).
    # IF NOT EXISTS 인 이유: RED 단계(현행 코드)에선 db 픽스처의 migrate 가 이미
    # runs 를 만들어 두었고, GREEN 단계에선 없다 -- 양 단계 모두에서 "구형 DB 에
    # runs 가 있는" 전제를 성립시킨 뒤 재-migrate 로 삭제를 판정한다.
    db.execute("""CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, request_id TEXT NOT NULL,
        state TEXT NOT NULL, detail TEXT, started_at TEXT NOT NULL,
        finished_at TEXT)""")
    migrate(db)
    assert "runs" not in _table_names(db)


def test_fresh_migrate_never_creates_runs(tmp_path):
    # 신규 DB 경로: CREATE 목록에서 빠졌으니 애초에 안 생긴다(DROP IF EXISTS 는
    # 여기서 no-op -- 멱등). ALL_TABLES 부재도 함께 고정한다 --
    # test_migrate_creates_all_tables 는 부분집합 검사라 "목록에서 빼먹음"은
    # 잡아도 "목록에 남아 있음"은 못 잡는다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    assert "runs" not in _table_names(db)
    assert "runs" not in ALL_TABLES


def test_drop_runs_executes_after_the_create_loop():
    # CREATE 문 삭제 후엔 DROP 의 위치가 기능 테스트로는 구분되지 않는다(어디
    # 있든 지금은 runs 가 없다). 순서 계약의 실질은 미래다: 부활 CREATE 가
    # 실수(머지·롤백)로 되돌아와도 "생성 후 삭제"는 '없음'으로 수렴하고, 역순이면
    # 매 migrate 가 부활시킨다 -- 제거 결정이 조용히 뒤집힌다.
    # CREATE 리터럴 위치 비교로는 부족하다: stmts 리스트 **정의**와 실행 **루프**
    # 사이에 DROP 을 끼우면 텍스트로는 CREATE 들보다 뒤인데 실행은 앞이다.
    # 그래서 실행 루프(for stmt in stmts) 대비 위치를 계약으로 건다(소스 계약
    # 테스트는 슬라이스 25 test_diag_logs_is_declared... 와 같은 계열의 타협).
    import inspect

    from dms import migrations
    src = inspect.getsource(migrations._apply_migrations)
    assert "DROP TABLE IF EXISTS runs" in src
    assert src.index("for stmt in stmts") < src.index("DROP TABLE IF EXISTS runs")
```

그리고 기존 단언 2곳을 갱신한다:

**(1)** `test_migrate_is_idempotent`(`:173`)의 `assert len(ALL_TABLES) == 20` 을:

```python
    # 슬라이스 27: 死物 runs 제거(20 -> 19) -- 이 저장소 최초의 파괴적 마이그레이션.
    assert len(ALL_TABLES) == 19
```

**(2)** 슬라이스 25 의 `test_all_tables_is_still_twenty_tables_not_columns`(`:454-457`)를 **이름째** 교체:

```python
def test_all_tables_is_nineteen_tables_not_columns():
    # len(ALL_TABLES) 는 **테이블** 수 계약이다(슬라이스 25 가 20 으로 박제,
    # 슬라이스 27 의 runs 제거로 19). 늘든 줄든 명시 결정 없이는 여기가 먼저
    # 빨개진다 -- "새 테이블 0·삭제는 결정된 것만"의 그물.
    assert len(ALL_TABLES) == 19
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_migrations.py -q`
Expected: FAIL 5건 / PASS 나머지 — `test_migrate_drops_runs_...` 는 `assert "runs" not in _table_names` 에서, `test_fresh_migrate_never_creates_runs` 는 첫 단언에서, `test_drop_runs_executes_...` 는 `assert "DROP TABLE IF EXISTS runs" in src` 에서, `len == 19` 2건은 현행 20 이라서.

- [ ] **Step 3: migrations.py 를 고친다**

**(1)** 모듈 docstring(`:1`):

```python
"""전체 스키마. CREATE TABLE IF NOT EXISTS 선언 스크립트 — 스펙 §4 도메인 모델의 테이블 19개(슬라이스 27 에서 死物 runs 제거)."""
```

**(2)** `ALL_TABLES`(`:4-12`)에서 `"runs"` 삭제:

```python
ALL_TABLES = (
    "requests", "plans", "results", "state_transitions",
    "data_jobs", "storages", "policies",
    ...  # 나머지 줄 무변경
)
```

**(3)** CREATE 문 삭제 — `:108-115` 의 `"""CREATE TABLE IF NOT EXISTS runs (...)"""` 블록(요소 전체)을 stmts 리스트에서 제거.

**(4)** CREATE 실행 루프(`for stmt in stmts: db.execute(stmt)`, `:367-368`) **바로 아래**, `_ensure_columns(db)` 호출 **앞**에 추가:

```python
    # --- 死物 정리(슬라이스 27) --- 이 저장소 최초의 파괴적 마이그레이션.
    # runs 는 초기 스키마가 만들었지만 전 소스·테스트에서 읽기/쓰기 0건인
    # 死物이고, 슬라이스 17 이 부활을 명시 배제하고 data_jobs 파생 컬럼을 택했다
    # (BACKLOG). 신규 DB 는 CREATE 목록에서 빠져 애초에 안 생기고, 기배포 DB 는
    # 여기서 지운다 -- IF EXISTS 라 두 경로 모두 멱등이다. 반드시 CREATE 실행
    # 루프 **뒤**에 둔다: 부활 CREATE 가 실수(머지·롤백)로 되돌아와도 "생성 후
    # 삭제"는 '없음'으로 수렴하지만, 역순이면 매 migrate 가 부활시킨다 --
    # tests/test_migrations.py 의 소스 순서 계약이 이 위치를 고정한다.
    # 데이터 손실 없음: INSERT 경로가 애초에 없었고, 배포 절차가 삭제 전
    # 행 수 0 을 실측 재확인한다(플랜 「배포·실증」 0단계).
    db.execute("DROP TABLE IF EXISTS runs")
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_migrations.py tests/test_migrations_batch.py tests/test_migrations_policy_seed.py -q`
Expected: 전부 PASS — `test_migrate_creates_all_tables`(ALL_TABLES 순회)와 idempotent(2회 migrate — DROP IF EXISTS 재실행 무해) 초록이 다른 19 테이블 무영향 + 멱등의 1차 보증이다. 광역 무영향은 Task 3 전체 스위트가 마감한다(conftest 의 `db` 픽스처가 매 테스트 migrate 를 돌므로, runs 제거가 어딘가에 영향을 준다면 전체 스위트가 잡는다).

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

`cp src/dms/migrations.py /tmp/slice27-migrations.py.bak` 후: `db.execute("DROP TABLE IF EXISTS runs")` 를 `stmts = [` 리스트 정의 **앞**(함수 첫 줄 근처)으로 이동 → `test_drop_runs_executes_after_the_create_loop` 만 RED 가 맞다. **이때 행동 테스트(drops/fresh 2건)는 초록으로 남는 것을 함께 관찰한다** — CREATE 가 없는 현행에선 순서가 행동으로 안 잡히고 소스 계약만이 이빨이라는 함정의 실증이다. `cp /tmp/slice27-migrations.py.bak src/dms/migrations.py` 로 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
feat(migrations): 死物 runs 테이블 제거 — CREATE 목록·ALL_TABLES(20→19) 삭제 + CREATE 루프 뒤 DROP IF EXISTS(기배포 DB 경로, 최초의 파괴적 마이그레이션)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- src/dms/migrations.py tests/test_migrations.py
```

---

### Task 2: 항목 B — `finalize_from_job` 원자화 (경계/몸통 분리)

**Files:**
- Modify: `src/dms/repositories/requests.py`
- Modify: `tests/test_repo_requests_finalize.py`

**Interfaces:**
- Produces:
  - `_apply_state(request_id, to_state, *, reason_code, actor)` — 상태 전이의 문장 몸통(현재 상태 읽기 + UPDATE + 전이 이력 INSERT). **트랜잭션은 호출자가 소유한다.**
  - `set_state` — 동작 무변경: `with self._db.transaction(): self._apply_state(...)`.
  - `finalize_from_job` — 멱등 가드(트랜잭션 밖, 읽기 후 조기 반환) 통과 후 `with self._db.transaction():` 안에서 `_apply_state` + `record_result`. 크래시 시 전부-또는-전무.
- Consumes: `db.transaction()`(비중첩 — 「전제 재확인」 4), `record_result`(단일 INSERT 라 호출자 트랜잭션에 그대로 편승).
- **왜 naive wrap 이 아닌가(계약으로 주석에 박는다)**: `db.transaction()` 은 `BEGIN` 무조건이라 `set_state` 를 안에서 부르면 sqlite 는 즉사, PG 는 안쪽 COMMIT 이 바깥을 조기 커밋해 **조용히** 비원자(record_result 가 트랜잭션 밖에서 돈다 — 고치는 척만 하는 최악의 형태). planner 의 동종 쌍 2곳(`:73-77`, `:130-135`)은 범위 밖이지만 이 추출로 후속이 각각 2줄이 된다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_repo_requests_finalize.py` — 파일 끝에 추가:

```python
def test_finalize_crash_between_writes_rolls_back_both(db):
    """슬라이스 24 실증의 실 결함(BACKLOG §2.1): set_state 는 커밋됐는데
    record_result 가 터지면 요청은 종단, results 는 없음 -- 종단 요청은 고아
    스윕(terminal_jobs_with_live_request)의 시야 밖이라 결손이 영구다.
    원자화 후엔 전부-또는-전무: 크래시 주입 시 상태·전이 이력·results 셋 다
    남지 않아야 하고, 요청이 비종단으로 남아 다음 틱 재시도가 완주해야 한다."""
    repos = Repositories(db)
    rid = _req(repos)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("crash before record_result")

    repos.requests.record_result = _boom      # 인스턴스 속성이 메서드를 가린다
    with pytest.raises(RuntimeError):
        repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    del repos.requests.record_result          # 원복 -- 클래스 메서드가 되살아난다
    # 전부-또는-전무: 상태도, 전이 이력도, results 도 남지 않았다.
    assert repos.requests.get(rid)["state"] == "Running"
    assert db.query("SELECT request_id FROM results WHERE request_id = :r",
                    {"r": rid}) == []
    assert all(t["to_state"] != "Succeeded" for t in repos.requests.transitions(rid))
    # 다음 틱 재시도: 비종단이라 멱등 가드에 안 걸리고 정상 완주한다.
    repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    assert repos.requests.get(rid)["state"] == "Succeeded"
    assert len(db.query("SELECT request_id FROM results WHERE request_id = :r",
                        {"r": rid})) == 1


def test_finalize_idempotent_return_opens_no_transaction(db):
    # 멱등 가드(이미 종단이면 return)는 읽기 후 조기 반환이다 -- 트랜잭션 안에
    # 넣으면 고아 스윕이 매 틱 재호출하는 no-op 마다 빈 BEGIN/COMMIT 이 열린다.
    # 가드가 트랜잭션 밖이라는 사실 자체를 계약으로 고정한다(현행도 그렇다 --
    # 이 테스트는 원자화가 가드를 안으로 끌고 들어가는 회귀를 막는 그물이다).
    repos = Repositories(db)
    rid = _req(repos)
    repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")

    def _no_txn():
        raise AssertionError("멱등 조기 반환 경로가 트랜잭션을 열었다")

    db.transaction = _no_txn                  # 인스턴스 속성이 메서드를 가린다
    try:
        # 두 번째 finalize 는 no-op 이어야 하고, 트랜잭션을 열면 위 AssertionError.
        repos.requests.finalize_from_job(rid, DataJobState.FAILED, actor="stepper")
    finally:
        del db.transaction
    assert repos.requests.get(rid)["state"] == "Succeeded"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_requests_finalize.py -q`
Expected: `test_finalize_crash_between_writes_rolls_back_both` 1건 FAIL — `assert ... == "Running"` 에서 실제값 `"Succeeded"`(set_state 가 이미 커밋된, 슬라이스 24 가 관측한 바로 그 부분 적용). `test_finalize_idempotent_return_opens_no_transaction` 은 **즉시 PASS 가 맞다**(현행도 가드가 트랜잭션 밖 — 회귀 방지 그물). 기존 3테스트 PASS.

- [ ] **Step 3: requests.py 를 고친다**

`set_state`(`:76-87`)와 `finalize_from_job`(`:151-163`)을 다음으로 교체(+ `_apply_state` 신설 — `set_state` 바로 아래에 둔다):

```python
    def set_state(self, request_id, to_state: RequestState, *, reason_code=None, actor):
        with self._db.transaction():
            self._apply_state(request_id, to_state, reason_code=reason_code,
                              actor=actor)

    def _apply_state(self, request_id, to_state, *, reason_code, actor):
        """상태 전이의 문장 몸통(현재 상태 읽기 + UPDATE + 전이 이력) --
        트랜잭션은 **호출자가 소유한다**. db.transaction() 은 중첩을 모른다
        (BEGIN 이 무조건 -- db.py): sqlite 는 중첩 BEGIN 에서 즉사하고, PG
        (autocommit)는 경고만 낸 채 안쪽 COMMIT 이 바깥 트랜잭션을 조기 커밋해
        **조용히** 비원자가 된다. 그래서 set_state 를 다른 트랜잭션 안에서
        재사용하려면 경계(누가 BEGIN 하나)와 몸통(무슨 문장인가)을 분리하는
        수밖에 없다(슬라이스 27 -- finalize_from_job 이 두 번째 소유자다)."""
        now = utc_now_iso()
        current = self._db.query_one(
            "SELECT state FROM requests WHERE request_id = :id", {"id": request_id})
        if current is None:
            raise KeyError(request_id)
        self._db.execute(
            "UPDATE requests SET state = :s, updated_at = :now WHERE request_id = :id",
            {"s": to_state.value, "now": now, "id": request_id})
        self._record_transition(request_id, RequestState(current["state"]),
                                to_state, reason_code, actor, now)
```

```python
    def finalize_from_job(self, request_id, job_state, *, reason_code=None,
                          summary=None, actor):
        target = self._JOB_TO_REQUEST.get(DataJobState(job_state))
        if target is None:
            raise ValueError(f"non-terminal job state: {job_state}")
        current = self._db.query_one(
            "SELECT state FROM requests WHERE request_id = :id", {"id": request_id})
        if current is None:
            raise KeyError(request_id)
        if RequestState(current["state"]) in TERMINAL_REQUEST_STATES:
            # idempotent -- 읽기 후 조기 반환이라 트랜잭션 밖이다. 안에 넣으면
            # 고아 스윕이 매 틱 재호출하는 no-op 마다 빈 BEGIN/COMMIT 이 열린다.
            return
        # 슬라이스 27(BACKLOG §2.1, 슬라이스 24 실증 관찰): 전이와 results INSERT
        # 를 한 트랜잭션으로 묶는다. 별도 커밋이던 시절엔 사이 크래시가 "요청은
        # 종단인데 results 행이 없다"를 만들었다 -- 종단 요청은 고아 스윕의 시야
        # 밖이라 그 결손은 영구였다. 원자화 후엔 둘 다 롤백돼 요청이 비종단으로
        # 남고 다음 틱 finalize 재시도가 완주한다. 덤: "results 는 있는데 요청은
        # 비종단"도 구조적으로 불가능해져, 재시도가 results PK 중복
        # (UniqueViolation -- 슬라이스 24 관측)에 걸릴 창도 함께 닫힌다.
        with self._db.transaction():
            self._apply_state(request_id, target, reason_code=reason_code,
                              actor=actor)
            self.record_result(request_id, target, reason_code=reason_code,
                               summary=summary)
```

(`record_result` 는 무변경 — 단일 INSERT 라 finalize 트랜잭션 안에선 편승하고, planner 의 기존 단독 호출 2곳에선 지금처럼 autocommit 으로 돈다.)

- [ ] **Step 4: 통과를 확인한다 (finalize 소비자 광역 회귀)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_requests_finalize.py tests/test_repo_requests.py tests/test_recover_orphans.py tests/test_api_jobs.py tests/test_api_requests.py tests/test_api_batches.py tests/test_stepper_scan.py tests/test_stepper_sync.py tests/test_controller.py tests/test_controller_stepper.py -q`
Expected: 전부 PASS — recover_orphans(고아 스윕 재호출·독 행 격리)·api_jobs/requests(취소 경로)·api_batches(배치 취소)·stepper(정상 종단)가 호출처 7곳의 무회귀 안전망이다. 특히 `test_poison_row_does_not_starve_the_rest_...`(finalize 를 인스턴스 속성으로 갈아끼우는 선례) 초록 = 시그니처·멱등 계약 유지 보증.

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

`cp src/dms/repositories/requests.py /tmp/slice27-requests.py.bak` 후: `finalize_from_job` 의 `with self._db.transaction():` 줄을 지우고 두 호출을 디덴트(감싸기 제거 — 원자화 이전과 등가) → `test_finalize_crash_between_writes_rolls_back_both` 가 `assert ... == "Running"` 에서 RED(실제값 `"Succeeded"`, results 0행 — 슬라이스 24 관측의 정확한 재현). 기존 `test_finalize_maps_states` 등은 초록으로 남는 것도 관찰한다(정상 경로만 보는 테스트는 이 결함을 영원히 못 잡는다는 증거). `cp /tmp/slice27-requests.py.bak src/dms/repositories/requests.py` 로 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
feat(requests): finalize_from_job 원자화 — 전이+results 한 트랜잭션(_apply_state 추출로 중첩 회피), 멱등 가드는 트랜잭션 밖

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- src/dms/repositories/requests.py tests/test_repo_requests_finalize.py
```

---

### Task 3: 마감 검증 — 전체 스위트 + 프론트·e2e 확인 + 불변 조항 (커밋 없음)

**Files:** 없음(검증만)

- [ ] **Step 1: 백엔드 전체 스위트**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: **약 1259 passed**(기준선 1254 + 신규 5: T1 3 + T2 2 — 근사치다. 수가 다르면 신규 수를 다시 세되 **failed 0 이 본질**이다).

- [ ] **Step 2: 프론트·e2e (무접촉 확인)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b && npm run test:e2e`
Expected: Task 0 Step 2 와 **동일 수치** 초록, e2e `9 passed`. 빨개지면 이 슬라이스가 원인일 수 없다(diff 0) — 환경 문제로 판단하되 진행 전에 보고.

- [ ] **Step 3: 계약·불변 조항 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && git status --porcelain && git log --oneline -4 && git diff HEAD~2 --stat -- frontend deploy/k8s docs`
Expected: 작업 트리 clean(커밋 2건 외 잔여물 없음), `frontend`·`deploy/k8s` **diff 0**, `docs` 는 이 플랜 파일뿐(플랜 파일이 커밋 전이라면 status 에만 보인다), `legacy/` 무변경. 커밋 2건의 대상이 정확히 4파일(migrations.py·test_migrations.py·requests.py·test_repo_requests_finalize.py)인지 확인.

---

## 플랜 이후: 배포·실증 (별도 ops, 플랜 태스크 밖)

플랜 실행이 끝나면 배포자가 테스트베드에서 수행한다(슬라이스 12~25 관례). **매니페스트-우선**: 태그를 먼저 bump→커밋하고 그 커밋에서 빌드한다. 현재 태그 제어면 **d37** → 이 슬라이스는 **`dms` 만 d38**(러너 `DMS_JOB_IMAGE`·에이전트는 d35 유지 — 무접촉이라 올릴 이유가 없다). **스키마가 바뀌므로(DROP TABLE) migrate Job 재실행이 필수다**(슬라이스 16 교훈: `set image` 는 migrate 를 재실행하지 않는다). 빌드는 클러스터 내 `build_build_pod`(pkg-01 SSH 불가, 슬라이스 24·25 실적), DB 조작·확인은 **API 파드 안 python**(`kubectl -n dms exec deploy/dms-api -c api -- python`). 되돌릴 수 있는 조작만, 원복까지.

**0. 삭제 전 실측 — runs 행 수 0 확인 (migrate 재실행 전, 구 d37 파드에서)**

```bash
kubectl -n dms exec deploy/dms-api -c api -- python -c "
import os
from dms.db import Database
db = Database.connect(os.environ['DMS_DATABASE_URL'])
print(db.query_one('SELECT COUNT(*) AS n FROM runs'))"
# 기대: {'n': 0}. 0 이 아니면 **진행 중단·보고** -- 미지의 쓰기 경로가 있다는
# 뜻이라 "데이터 손실 없음" 전제가 깨진다(제거 결정 재확인이 먼저다).
```

**1. 태그 범프 커밋 + 클러스터 내 빌드 + migrate + apply**

```bash
# (a) 매니페스트 범프 -- 5곳: 30-migrate-job.yaml:25 / 40-api.yaml:67,84 /
#     41-controller.yaml:35,52 의 dms d37→d38. 20-config.yaml(DMS_JOB_IMAGE)·
#     50-agent-daemonset.yaml 은 d35 유지.
git commit -m "deploy(k8s): 제어면 d38 (슬라이스 27 DB 정합성 — runs 제거·finalize 원자화)" -- deploy/k8s
# main 병합·push 후 그 커밋에서(빌드 파드는 GitHub 에서 clone 한다 -- 배포자 몫):

# (b) 빌드 파드 -- 슬라이스 25 플랜의 build_build_pod 스니펫 그대로, 태그만 d38:
#     images=["dms"], DMS_BUILD_TAG="d38", node 는 GET /api/admin/control-state 의
#     build_node_name. 로그에서 DMS_COMMIT_SHA=<범프 커밋> 과 DMS_BUILD_OK 확인 후
#     빌드 파드 삭제.

# (c) **migrate 먼저** -- Job 은 immutable 이라 delete 후 재적용(DROP 은 여기서 실행된다):
kubectl -n dms delete job dms-migrate --ignore-not-found
kubectl apply -f deploy/k8s/30-migrate-job.yaml
kubectl -n dms wait --for=condition=complete job/dms-migrate --timeout=300s

# (d) 제어면 apply + 수렴(initContainer 의 migrate 재실행이 이중 안전망):
kubectl apply -f deploy/k8s/40-api.yaml -f deploy/k8s/41-controller.yaml
kubectl -n dms rollout status deploy/dms-api deploy/dms-controller
```

**2. 기존 DB 경로 검증 — runs 부재 (핵심 실증: 빈 DB 경로와 다른 코드)**

```bash
kubectl -n dms exec deploy/dms-api -c api -- python -c "
import os
from dms.db import Database
db = Database.connect(os.environ['DMS_DATABASE_URL'])
print('runs:', db.query(\"SELECT table_name FROM information_schema.tables\"
                        \" WHERE table_name = 'runs'\"))
print('domain tables:', db.query_one(
    \"SELECT COUNT(*) AS n FROM information_schema.tables\"
    \" WHERE table_name IN ('requests','plans','results','state_transitions',\"
    \"'data_jobs','storages','policies','identity_denylist',\"
    \"'identity_probe_targets','agent_reports','agent_nodes','accounts',\"
    \"'user_scan_paths','builds','releases','component_leases','control_state',\"
    \"'audit_log','events')\"))"
# 기대: runs: [] / domain tables: {'n': 19}. runs 가 비어 있지 않으면 migrate 가
# 안 먹은 것 -- **진행 금지**(30-migrate-job 로그 확인이 먼저다).
```

**3. finalize 원자화 무회귀 — 실 잡 1건 정상 종단**

```bash
# (a) 포탈에서 작은 scan 1건 제출 -> Succeeded 종단까지 관찰.
# (b) 요청 종단 상태와 results 행이 **함께** 존재하는지(같은 트랜잭션의 산물):
kubectl -n dms exec deploy/dms-api -c api -- python -c "
import os
from dms.db import Database
db = Database.connect(os.environ['DMS_DATABASE_URL'])
print(db.query_one(
    \"SELECT r.state, res.terminal_state FROM requests r\"
    \" LEFT JOIN results res ON res.request_id = r.request_id\"
    \" WHERE r.request_id = '<rid>'\"))"
# 기대: {'state': 'Succeeded', 'terminal_state': 'Succeeded'} -- terminal_state 가
# None 이면 바로 이 슬라이스가 닫은 결함이 재발한 것이다.
# (c) 취소 경로도 한 번: 진행 중 잡 취소 -> Cancelled + results 행 동시 확인.
# (d) 크래시 창의 실 재현은 결정적 유도 수단이 없어(컨트롤러를 정확히 두 문장
#     사이에서 죽여야 한다) 단위 테스트가 계약을 고정하고, 라이브는 정상·취소
#     경로 무회귀로 갈음한다 -- 정직하게 기록한다.
```

**4. 19 테이블 무영향 스모크 + 롤백 내성 메모**

```bash
# (a) 포탈 주요 화면(요청 목록·잡 상세·대시보드·관리자 스토리지/정책) 로드 무오류,
#     events 에 새 error 이벤트가 없는지 확인.
# (b) 롤백 내성(메모만, 실행 안 함): d37 이미지는 CREATE runs 를 갖고 있어 롤백
#     migrate 가 빈 runs 를 되살린다 -- 死物·빈 테이블이라 무해하고, d38 재적용이
#     다시 지운다(DROP IF EXISTS 멱등). 롤백이 실제로 일어나면 이 사실을 기록할 것.
```

실증 통과 후 `docs/superpowers/BACKLOG.md` 갱신(슬라이스 27 완료 기록 + §2.1 finalize 항목·runs 死物 항목 해소 + `:97` 의 낡은 `migrations.py:69` 표기 정정)을 별도 커밋으로 — 플랜 밖 관례.

---

## Self-Review

**1. 과제 커버리지**

| 과제 항목 | 담당 |
|---|---|
| A: CREATE 삭제 + ALL_TABLES 삭제 + DROP IF EXISTS(멱등) | Task 1 Step 3 |
| A: DROP 은 CREATE 뒤(순서 계약) | Task 1 소스 순서 계약 테스트 + 뮤테이션 — 단 계약 기준을 "CREATE 리터럴 위치"가 아니라 "실행 루프 위치"로 강화(전제 재확인의 추가 발견: 리스트 정의/실행 루프 사이 함정) |
| A 검증 (1) 신규 DB 부재 / (2) 기존 DB 삭제 / (3) len 19 / (4) 19 테이블 무영향 | (1) `test_fresh_migrate_never_creates_runs` (2) `test_migrate_drops_runs_from_existing_db` + 배포 §2 실측 (3) 2곳 갱신(실측 정정) (4) Task 1 Step 4 + Task 3 전체 스위트 + 배포 §4 스모크 |
| B: 162-163 원자화, 가드는 밖 | Task 2 — 단 naive wrap 은 불가(중첩 함정 실측)라 `_apply_state` 경계/몸통 분리로 구현. 가드-밖은 `test_finalize_idempotent_return_opens_no_transaction` 이 계약으로 고정 |
| B 함정: set_state 내부 트랜잭션·`_txn_depth`·UniqueViolation 창 판단 | 「전제 재확인」 4·6 — 중첩 불가 실측(sqlite 즉사/PG 조용한 비원자), `_txn_depth` 는 재연결 게이트일 뿐. UniqueViolation 창은 닫힌다(불가능 상태화), 수동 조작 예외는 열린 질문 2 |
| B 검증: 크래시 주입 → 둘 다 롤백 → 재시도 가능, 정상 무회귀 | `test_finalize_crash_between_writes_rolls_back_both`(롤백 3단언 + 재시도 완주) + Task 2 Step 4 광역 회귀 + 배포 §3 |
| 실증·배포(d38 제어면만·migrate 재실행·기존 DB 검증·행 수 0 선확인) | 「플랜 이후」 §0~4 |

**2. 뮤테이션(이빨) 매트릭스** — T1: DROP 을 실행 루프 앞으로 이동 → 소스 순서 계약만 RED(행동 테스트는 초록으로 남음을 함께 관찰 — 소스 계약이 유일한 이빨이라는 함정의 실증). T2: `with transaction():` 감싸기 제거 → 크래시 주입 테스트가 "Running" 단언에서 RED(슬라이스 24 관측의 정확한 재현), 정상 경로 테스트는 초록(기존 그물로는 못 잡는다는 증거). 과제 지시의 뮤테이션 2건에 1:1 대응, 각 Task 1건.

**3. 타입·이름 일관성** — `_apply_state(request_id, to_state, *, reason_code, actor)` 는 정의처(Task 2 Step 3)·`set_state`·`finalize_from_job` 동일 철자, keyword-only 는 기존 `set_state` 관례 유지. 테스트 이름 `test_migrate_drops_runs_from_existing_db`/`test_fresh_migrate_never_creates_runs`/`test_drop_runs_executes_after_the_create_loop`/`test_all_tables_is_nineteen_tables_not_columns`/`test_finalize_crash_between_writes_rolls_back_both`/`test_finalize_idempotent_return_opens_no_transaction` 는 각 Step 1 과 뮤테이션 절 동일 철자. 픽스처 `db`·헬퍼 `_req`·`_table_names` 는 기존 파일의 것을 재사용(신설 0).

**알려진 위험 / 판단:**
- **소스 순서 계약은 텍스트 검사다** — CREATE 삭제 후 순서가 행동으로 구분 불가능하다는 실측이 근거(슬라이스 25 의 CREATE 블록 계약과 같은 계열의 타협). 기준을 실행 루프 위치로 잡아 리스트 정의/루프 사이 함정까지 막았다.
- **`_apply_state` 추출은 `set_state` 의 재사용 표면을 넓힌다** — 트랜잭션 없는 몸통을 실수로 단독 호출하면 autocommit 으로 문장별 커밋된다. 밑줄 프라이빗 + docstring "호출자가 트랜잭션을 소유한다"로 계약을 박고, 현행 호출자는 정확히 2곳(set_state·finalize)이다.
- **구 이미지(d37) 롤백이 runs 를 되살린다** — CREATE IF NOT EXISTS 를 아직 갖고 있어서다. 빈 死物이라 무해하고 d38 재적용이 재삭제(멱등). 배포 §4 에 메모.
- **크래시 창의 라이브 재현은 하지 않는다** — 두 문장 사이에서 컨트롤러를 결정적으로 죽일 수단이 없다. 단위 테스트(크래시 주입)가 계약을 고정하고 라이브는 정상·취소 경로 무회귀로 갈음 — 슬라이스 25 열린 질문 2 와 같은 정직한 처리.
- **전체 수치 기대(≈1259)는 근사 명시** — 어긋나면 재계산하되 failed 0 이 판정 기준.

## 결정이 필요한 열린 질문

1. **planner 의 동종 비원자 쌍 2곳**(`planner.py:73-77` `_reject`, `:130-135` conflict)은 이번 범위 밖("두 항목만")에 남는다. `_apply_state` 추출로 후속 원자화가 각각 2줄이 됐다 — 같은 크래시 창(거부·충돌 종단인데 results 없음)이 실재하므로 다음 DB 정합성 슬라이스 후보다.
2. **수동 DB 조작으로 "results 존재 + 요청 비종단"을 만들면**(슬라이스 24 재현이 그랬다) 원자화 후 거동이 바뀐다: 이전엔 첫 재시도에서 set_state 만 커밋돼 1회 시끄럽고 종단으로 수렴했지만, 이후엔 고아 스윕이 매 틱 UniqueViolation 롤백 → `orphan_recovery_failed` 이벤트가 틱마다 쌓인다(행 단위 격리가 나머지 행을 보호하고, 조작을 되돌리면 멎는다). fail-closed 라 수용한다. `record_result` 의 ON CONFLICT 멱등화는 낡은 results 를 조용히 존치시키는 트레이드오프라 보류.
3. **`ALL_TABLES` 는 전 테이블의 목록이 아니다** — `batches`·`batch_items`·`schema_migrations` 는 CREATE 되지만 목록 밖이다(실측). "테이블 수 계약"의 그물이 이들을 못 보므로, 목록의 의미(스펙 §4 도메인 모델 한정)를 docstring 에 더 명확히 박을지는 위생 슬라이스 감이다.
