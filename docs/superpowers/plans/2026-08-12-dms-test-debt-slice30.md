# 슬라이스 30 — 테스트 부채 마감 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 마지막 위생 슬라이스. §2.4·§2.5 의 테스트 부채 후보 6건을 실측으로 심사해 유효한 것만 닫는다. **테스트 4건** — ① 전 테이블·전 인덱스 전수 열거 그물(`ALL_TABLES` 사각지대 = `batches`·`batch_items`·`schema_migrations` 3개 + `idx_requests_batch` 포함 인덱스 16개 무단언), ② 마이그레이션 이중 경로(CREATE/`_ensure_columns`)의 **일반** 회귀 그물(v1 기준선 등식 + 선언형 패리티 — 미래 컬럼이 규율을 빼먹으면 여기가 빨개진다), ③ `KubernetesClient` lazy-init 이중검사 잠금의 결정적 단위 테스트(전체 pragma 유지, 로직만), ④ 슬라이스 15 잔여(`text or ""` None 입력 그물 + `test_execution_volcano` 픽스처의 사문 summary 모양 현행화). **코드 2건** — ⑤ `information_schema` 쿼리 2곳을 `current_schema()` 로 한정(다중 스키마 오판 → ALTER 건너뜀 → 슬라이스 14 실 500 재현 경로 봉쇄), ⑥ planner 비원자 쌍 2곳(`_reject`·conflict) 원자화 — finalize(슬라이스 27, 실증 5/5)와 동일 처방: `_apply_state` + `record_result` 를 한 트랜잭션으로 묶는 레포 메서드 신설. 새 pip/npm 의존성 0, 새 테이블 0, 새 컬럼 0, 새 사유 코드 0, 스키마 DDL 무변경, 프론트 diff 0.

**Architecture:** 테스트 4건은 전부 기존 파일 확장이며 소스 무접촉이다. 전수 그물(Task 1·2)은 sqlite 실 산출물(`sqlite_master`/PRAGMA)을 기대 집합과 **양방향 등식**으로 대조한다 — 부분집합 검사(`test_migrate_creates_all_tables`)와 `len==19` 는 "목록 안"만 지키고 "목록 밖 3개"는 못 보는 그물이었다(슬라이스 27 발견). `ALL_TABLES` 는 19 그대로 둔다(사용처 실측: 정의 1곳 + `tests/test_migrations.py` 뿐 — 도메인 모델 계약으로 유지하고, 전수는 테스트 쪽 상수가 진다). Task 2 의 v1 기준선은 **역사적 사실이라 불변**이고, 등식 `현재 전체 컬럼 = v1 ∪ ensure 목록` 은 (a) CREATE 에만 넣은 미래 컬럼(정확히 슬라이스 14 의 실 500)과 (b) 명시 결정 없는 파괴적 컬럼 삭제를 둘 다 잡는다. Task 3 은 `kubernetes` 패키지가 venv 에 **없다**(실측 `ModuleNotFoundError`)는 사실을 역이용한다 — `_ensure` 안의 `import kubernetes` 는 `sys.modules` 를 먼저 보므로 `monkeypatch.setitem` 대역이 새 의존성 0 으로 잡히고, 경쟁 창(락 대기 중 타 스레드 완주 / 부분 초기화 실패)은 가짜 락·1회 실패 주입으로 **결정적으로** 재현한다(스레드 경주 없이). 코드 2건 중 Task 5 는 introspection 쿼리의 술어만 바꾼다(DDL 무변경 — 단일 스키마 현 배포에서 거동 동일). Task 6 은 슬라이스 27 이 추출해 둔 `_apply_state`(무트랜잭션 몸통) 덕에 레포 메서드 하나로 끝난다 — planner 는 `_db` 를 모르므로 트랜잭션 경계는 레포가 소유한다(`finalize_from_job` 과 같은 층위). finalize 와 달리 멱등 가드는 두지 않는다: 호출자는 `list_pending` 이 고른 Pending 요청뿐이고 스윕류 재호출 경로가 없다.

**Tech Stack:** Python 3.11 표준 라이브러리만(테스트 신규 import 는 `inspect`·`re`·`types.SimpleNamespace`·`sys` — 전부 stdlib). 프론트·e2e 무접촉(백엔드 계약 무변경 — /api 응답 형태 그대로, `frontend/` diff 0). DB 스키마 무변경.

## Global Constraints

- **설계 문서 없음** — 이 슬라이스의 「왜」는 BACKLOG §2.4(planner 비원자 쌍, ALL_TABLES 사각지대)·§2.5(KubernetesClient no-cover, ALTER 경로 갭, 슬라이스 15 잔여, idx_requests_batch)와 이 플랜의 「전제 재확인」이 담는다. 플랜과 코드 실측이 충돌하면 실측이 이긴다.
- **새 pip/npm 의존성 금지. 새 테이블·새 컬럼·새 사유 코드 0. 스키마 DDL 무변경.** `frontend/` 는 어떤 파일도 건드리지 않는다(Task 7 이 `git diff` 로 실측 확인). 신규 파일 0 — 수정 9파일뿐이라 `git add` 자체가 없다.
- **기존 그물 존중**: `len(ALL_TABLES) == 19` 단언 2곳(`test_migrations.py:174`·`:458`)과 `test_migrate_creates_all_tables` 는 **그대로 둔다** — 도메인 모델 계약(스펙 §4)과 전수 계약은 역할이 다르다. null/실패 구분 유지(파서의 `(None, None)` 은 "모름"이지 0 이 아니다). 0·빈문자열 truthy 검사 금지.
- **커밋은 pathspec 으로 한정한다**: 항상 `git commit -m "..." -- <경로들>`. `git add -A`·`git add .`·`git commit -a` **금지**(워크트리 공유 중 인덱스 섞임 사고 — BACKLOG §2.6). 커밋 메시지 말미에 반드시:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq`
- **뮤테이션 원복에 `git checkout` 금지** — 뮤테이션 전 `cp <파일> /tmp/slice30-<파일명>.bak` 으로 사본을 뜨고, 확인 후 `cp` 로 되돌린다.
- **origin push 금지, 브랜치 변경 금지**(현재 `worktree-dms-slice22plus`, HEAD 70561a8 = origin/main), **플랜 태스크에서 `deploy/k8s` 무접촉·이미지 태그 변경 금지**(d41 범프는 「플랜 이후: 배포·실증」의 첫 단계). `docs/` 아래는 이 플랜 파일 외 생성·수정 금지(실증 후 BACKLOG 갱신은 플랜 밖 관례).
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- 백엔드 테스트 명령(이 워크트리 전용, `.venv` 는 워크트리 밖 공용):
  `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest <대상> -q`
  전체 스위트는 **포그라운드**, Bash timeout 900000ms. **기준선 1266 passed.**
- 프론트는 **확인만**: `cd frontend && npx vitest run && npx tsc -b` — diff 0 이므로 현행 기준선 그대로 초록이어야 한다.
- 주석은 **한국어**로 「왜」를 적는다.

## 전제 재확인 (2026-08-12, 코드 직접 실측)

후보 6건 전부를 코드로 심사했다 — **6건 모두 (부분) 유효**하되, 항목 2·3 은 범위를 좁혀서만 유효하다. 뺀 부분은 명시한다.

| 후보 | 실측 결과 → 판정 |
|---|---|
| 1. ALL_TABLES 사각지대 | ✓ 유효. `ALL_TABLES` 19개(`migrations.py:4-12`), 실 CREATE 는 22개 — 차집합 정확히 `{batches, batch_items, schema_migrations}` (신규 sqlite migrate 로 실측). 사용처는 정의 1곳 + `test_migrations.py` 뿐 → **소스 무변경**, 전수는 테스트 등식으로. **함정 실측**: `sqlite_master` 에 `sqlite_sequence`(AUTOINCREMENT 내부 테이블)가 migrate 직후 이미 존재한다 — `sqlite_` 접두 제외 필수. → Task 1 |
| 2. KubernetesClient no-cover | **부분 유효**. `execution_volcano.py:299` pragma·`:311` Lock·`:313-327` `_ensure` 이중검사 실측 확인. `kubernetes` 는 venv 에 없다(실측) → sys.modules 대역 주입이 유일하고 충분한 경로. lazy-init 로직(부분 실패 게이트·fast path·락 대기 후 재검사)은 **결정적으로** 테스트 가능 → 그 3개만 커버. **실 k8s API 경로(create/get/delete/list/patch)는 실증 대상 유지 — 범위에서 뺀다**(대역으로 감싸 봐야 대역을 테스트하는 것). pragma 주석은 그대로(소스 무접촉). → Task 3 |
| 3. 마이그레이션 ALTER 경로 | **갭 재정의 후 유효**. 컬럼별 ALTER 테스트는 이미 전부 있다(슬라이스 14~27 산물: worker_pool·phase_refs·files/bytes 형·submit/sched_wait·artifact_base 5컬럼·auth_method·diag_logs·releases.progress — `test_migrations.py` 실측). **남은 갭 (i)**: 미래 컬럼이 CREATE 에만 추가되고 `_ensure_columns` 를 빼먹는 **일반** 회귀(슬라이스 14 실 500 의 정확한 계열)를 잡는 그물이 없다 → v1 기준선 등식으로 신설(Task 2). **갭 (ii)**: `_column_exists` PG 분기(information_schema)가 무테스트 — 항목 5c 와 한 몸(Task 5 가 겸장). **갭 (iii)**: 실 PG `ALTER COLUMN TYPE` 실행 경로 — sqlite 재현 불가는 `test_migrations.py:32-36` 기록 그대로고, `_FakeDb` 분기 테스트(슬라이스 15)가 닿는 데까지는 이미 커버. **PG 하니스 신설은 과잉 — 의도적 잔존으로 뺀다**(배포 실증이 실 경로를 지난다) |
| 4. idx_requests_batch 미단언 | ✓ 유효. 인덱스명 단언은 `idx_data_jobs_created`·`_sched` 2건뿐(전 테스트 grep 실측) — `idx_requests_batch` 포함 나머지 14개 전부 무단언. 개별 추가는 두더지잡기라 **인덱스 16개 전수 등식**으로(실측 목록 확보). → Task 1 |
| 5. 슬라이스 15 잔여 3건 | 전부 유효. (a) `parsers.py:45·62·74`·`commands.py:10` 의 `or ""` 는 None 을 삼키겠다는 약속인데 None 입력 테스트 0건(빈 문자열 테스트만 실측 존재) → 그물 추가. (b) `test_execution_volcano.py:174·190` 픽스처 `{"files": 3}` — 실 계약은 정확히 3키 `{"returncode","files","bytes"}`(`runner.py:136` `_build_summary` 실측) → 현행화(등식 유지로 pass-through 계약 고정. **이빨이 얇은 항목임을 명시** — 사문 계약 박제 제거가 주목적). (c) `migrations.py:437-441`(`_column_exists`)·`:459-463`(`_widen_count_columns`)의 information_schema 쿼리가 `table_schema` 미필터 실측 — 다른 스키마의 동명 테이블·컬럼(예: 백업 복원 `backup.data_jobs`)을 "이미 있다"로 오판하면 ALTER 를 건너뛰어 **라이브만 컬럼이 없는** 슬라이스 14 실 500 이 재현된다. 실패 방향이 fail-open 이라 고친다(코드). → Task 4·5 |
| 6. planner 비원자 쌍 2곳 | ✓ 유효, **넣는다**. `planner.py:73-77`(`_reject`)·`:130-135`(conflict) 실측 — `record_result` 단독 호출은 src 전체에서 정확히 이 2곳뿐(grep). 결함 계열은 finalize 와 동일: 사이 크래시 → "Rejected/Conflict 인데 results 없음", 종단이라 고아 스윕 시야 밖 → **영구 결손**. 처방은 슬라이스 27 이 실증(5/5)한 그대로이고 `_apply_state` 가 이미 있어 위험이 낮다. 마지막 위생 슬라이스에서 알려진 영구 결손 창을 남기는 것이 더 비싸다. → Task 6 |

**추가 실측(과제 지시에 없던 것):**

- **전수 실측값 확보**: 신규 migrate 직후 테이블 22(+`sqlite_sequence`) / 인덱스 16(이름 전체 목록은 Task 1 스니펫에 박제) / `_ensure_columns` 튜플 23개 / 진화 테이블 5개(v1 기준선은 Task 2 스니펫에 박제, `현재 컬럼 − ensure = v1` 등식으로 검산 완료) / 선언형 패리티 23컬럼 전부 현행 일치(GREEN 그물로 즉시 성립).
- **배포 태그 실측**: 제어면 `dms` 5곳 전부 **d40**(`30-migrate-job.yaml:25`, `40-api.yaml:67·84`, `41-controller.yaml:35·52`), 에이전트·러너는 d35. 이 슬라이스의 코드 변경(migrations·requests·planner)은 전부 제어면 → **제어면만 d41**. `dms_job_runner` 는 테스트만 추가라 러너 이미지 무접촉.
- **planner 크래시는 전파되지 않는다**: `run_once` 의 요청별 `try/except`(`planner.py:63-70`)가 삼키고 `plan_error` 이벤트를 남긴다 — Task 6 planner 수준 테스트는 `pytest.raises` 가 아니라 "삼켜진 뒤 Pending 잔존 + 다음 틱 완주"를 단언해야 한다(레포 수준 테스트만 `raises`).

## 파일 구조

| 파일 | 책임 |
|---|---|
| `tests/test_migrations.py` (수정) | Task 1: 테이블·인덱스 전수 등식 2건 / Task 2: v1 등식·선언형 패리티 2건 / Task 5: `_FakeDb` query 기록 + current_schema 계약 2건 |
| `tests/test_execution_volcano.py` (수정) | Task 3: `_ensure` 결정적 테스트 3건 / Task 4: summary 픽스처 3키 현행화 |
| `tests/test_job_runner_parsers.py` (수정) | Task 4: 파서 None 입력 그물 |
| `tests/test_job_runner_commands.py` (수정) | Task 4: `parse_hostfile(None)` 그물 |
| `src/dms/migrations.py` (수정) | Task 5: information_schema 쿼리 2곳 `current_schema()` 한정 |
| `src/dms/repositories/requests.py` (수정) | Task 6: `set_state_with_result` 신설(원자 경계 소유) |
| `src/dms/planner.py` (수정) | Task 6: `_reject`·conflict 경로를 원자 메서드로 교체 |
| `tests/test_repo_requests_finalize.py` (수정) | Task 6: 레포 수준 크래시·재시도 계약 |
| `tests/test_planner.py` (수정) | Task 6: planner 수준 크래시 격리·다음 틱 완주 2건 |

---

### Task 0: 기준선 확인 (커밋 없음)

**Files:** 없음(검증만)

- [ ] **Step 1: 백엔드 기준선**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: `1266 passed`. 여기 빨강이면 이 슬라이스 밖의 문제다 — 진행 전에 보고.

- [ ] **Step 2: 프론트 기준선 (확인만 — 이 슬라이스는 무접촉)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: 전 파일 초록, tsc 무출력 exit 0. 수치를 기록해 둔다 — Task 7 에서 동일해야 한다(diff 0 의 증거).

---

### Task 1: 전수 열거 그물 — 테이블 22·인덱스 16 (항목 1+4, 테스트만)

**Files:**
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces: 실 CREATE 산출물 전체(테이블·인덱스)와 기대 집합의 **양방향 등식**. 미결정 신설도, 실수 삭제도 여기서 먼저 빨개진다.
- **함정 2건**: ① `sqlite_master` 에는 내부 산물이 낀다 — 테이블 `sqlite_sequence`(AUTOINCREMENT), 인덱스 `sqlite_autoindex_*`(PK/UNIQUE). `sqlite_` 접두 제외가 없으면 신규 테스트가 영원히 빨갛다(실측 확인). ② 이 테스트는 그물 항목이라 즉시 GREEN 이 맞다 — RED 는 Step 3 뮤테이션이 진다(과제 지시: "테스트만 추가하는 Task 는 뮤테이션이 곧 RED").

- [ ] **Step 1: 테스트를 쓴다**

`tests/test_migrations.py` — 파일 끝에 추가:

```python
def test_migrate_creates_exactly_the_expected_tables(tmp_path):
    # 슬라이스 30(BACKLOG §2.4, 슬라이스 27 발견): ALL_TABLES(19)는 스펙 §4 도메인
    # 모델 한정이라 batches·batch_items·schema_migrations 3개가 목록 밖이다(실 DB
    # 22 테이블). len==19 그물과 부분집합 검사(test_migrate_creates_all_tables)는
    # "목록 안"만 지켜, 그 셋이 실수로 지워져도 못 잡는다. 실제 CREATE 산출물
    # 전체를 기대 집합과 **양방향 등식**으로 대조한다: 왼쪽만 크면 미결정 새
    # 테이블, 오른쪽만 크면 실수 삭제 -- 어느 쪽도 명시 결정 없이는 통과 못 한다
    # (runs 제거가 그랬듯, 파괴적 변경은 결정 + 테스트 갱신이 함께 가야 한다).
    # sqlite_sequence 는 AUTOINCREMENT 가 만드는 sqlite 내부 테이블이라 제외한다
    # (실측: 신규 migrate 직후 sqlite_master 에 이미 있다).
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    actual = {n for n in _table_names(db) if not n.startswith("sqlite_")}
    assert actual == set(ALL_TABLES) | {"batches", "batch_items",
                                        "schema_migrations"}


def test_migrate_creates_exactly_the_expected_indexes(tmp_path):
    # 슬라이스 30(BACKLOG §2.5 슬라이스 1~4 부채): idx_requests_batch 가 CREATE
    # 되는데 어떤 테스트도 단언하지 않았다 -- 인덱스명 단언은 idx_data_jobs_created
    # 계열 2건뿐(전 테스트 실측). 인덱스는 지워져도 기능 테스트가 전부 초록인 채
    # (풀스캔) 성능만 조용히 침몰하는 부류라 존재 단언이 유일한 그물이고, 개별
    # 이름 추가는 두더지잡기라 테이블 전수와 같은 등식으로 16개 전부를 고정한다.
    # sqlite_autoindex_* 는 PK/UNIQUE 의 내부 산물이라 제외한다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    rows = db.query("SELECT name FROM sqlite_master WHERE type = 'index'")
    actual = {r["name"] for r in rows if not r["name"].startswith("sqlite_")}
    assert actual == {
        "idx_requests_resource", "idx_requests_requester", "idx_requests_state",
        "idx_requests_batch", "idx_batches_status", "idx_batch_items_status",
        "idx_transitions_entity", "idx_data_jobs_state", "idx_data_jobs_created",
        "idx_data_jobs_created_sched", "idx_agent_reports_node",
        "idx_agent_reports_at", "idx_releases_component", "idx_audit_target",
        "idx_events_request", "idx_events_at",
    }
```

- [ ] **Step 2: 즉시 GREEN 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_migrations.py -q`
Expected: 전부 PASS(신규 2 포함). 신규가 빨갛다면 기대 집합 오기 — 실측(위 전제 재확인)과 대조해 수정하되, **실 DB 를 기대에 맞추는 방향(마이그레이션 수정)은 이 태스크가 아니다**(소스 무접촉).

- [ ] **Step 3: 뮤테이션으로 이빨 확인 후 원복 (이것이 이 태스크의 RED)**

`cp src/dms/migrations.py /tmp/slice30-migrations.py.bak` 후 두 건:
(a) `batch_items` CREATE 블록(요소 전체)을 stmts 에서 삭제 → `test_migrate_creates_exactly_the_expected_tables` 와 `idx_batch_items_status` 탓에 인덱스 등식도 RED. **이때 기존 그물(len==19 2곳·creates_all_tables·전 배치 테스트 제외 migrations 계열)이 초록으로 남는 것을 함께 관찰한다** — 바로 그 사각지대의 실증이다(배치 기능 테스트는 별개로 빨개질 수 있으나 migrations 그물만 본다).
(b) 원복 후 `db.execute("CREATE INDEX IF NOT EXISTS idx_requests_batch ...")` 줄(`:376`) 삭제 → 인덱스 등식만 RED, 다른 전 테스트 초록 — "인덱스는 조용히 죽는다"의 실증.
`cp /tmp/slice30-migrations.py.bak src/dms/migrations.py` 로 원복, Step 2 재확인.

- [ ] **Step 4: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
test(migrations): 테이블 22·인덱스 16 전수 등식 그물 — ALL_TABLES 사각지대(batches·batch_items·schema_migrations)와 idx_requests_batch 무단언을 마감

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- tests/test_migrations.py
```

---

### Task 2: 마이그레이션 이중 경로 **일반** 그물 — v1 기준선 등식 + 선언형 패리티 (항목 3, 테스트만)

**Files:**
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces: "CREATE(신규 DB)와 `_ensure_columns`(기배포 DB) 양쪽 선언" 규약(슬라이스 14 실 500 교훈)의 **미래형** 그물. 지금까지는 컬럼이 태어날 때마다 손으로 짠 구형 재생성 테스트가 붙었다(전부 실측 존재) — 그 규율을 빼먹은 미래 컬럼은 어떤 기존 테스트도 못 잡는다.
- 등식 `현재 전체 컬럼 = v1 ∪ ensure 목록` 의 성질(실측 검산 완료): CREATE 에만 추가 → 왼쪽만 커져 RED. CREATE+ensure 양쪽에 제대로 추가 → 양변에 같이 들어와 **v1 무수정으로 GREEN**(그물이 정상 진화를 방해하지 않는다). v1 컬럼 삭제 → 오른쪽만 커져 RED(파괴적 변경은 runs 처럼 명시 결정으로만).
- **함정**: `_ensure_columns` 의 목록은 함수 안 리터럴이라 import 로 닿을 수 없다 — 소스 정규식 추출(슬라이스 25 `test_diag_logs_is_declared...`·27 순서 계약과 같은 계열의 타협)로 뽑되, **추출 자체의 자기 검증**(개수 23)을 함께 단언한다. 0 개 매치로 등식이 공허하게 통과하는 것을 막는다.

- [ ] **Step 1: 테스트를 쓴다**

`tests/test_migrations.py` — 파일 끝에 추가:

```python
def _ensure_pairs():
    # _ensure_columns 의 (table, column, type) 목록은 함수 안 리터럴이라 import 로
    # 닿을 수 없다 -- 소스 정규식으로 뽑는다(모듈 상수 승격은 소스 무접촉 원칙
    # 밖: 소스 계약 추출은 슬라이스 25/27 계열의 타협이다).
    import inspect
    import re

    from dms import migrations
    src = inspect.getsource(migrations._ensure_columns)
    return re.findall(r'\("(\w+)", "(\w+)", "(\w+)"\)', src)


# 초기 배포(v1) 스키마의 컬럼 -- **역사적 사실이라 불변이다**(이 파일의 구형 재생성
# 픽스처들과 현재 소스에서 실측·검산). 이후 태어난 모든 컬럼은 CREATE(신규 DB)와
# _ensure_columns(기배포 DB) 양쪽에 있어야 한다는 이중 경로 규약(슬라이스 14 실
# 500 교훈)의 기준선. 여기 컬럼을 추가하는 것은 "그 컬럼은 ensure 없이도 모든
# 배포에 존재한다"는 주장이므로, 새 컬럼을 넣으려고 이 집합을 늘리면 안 된다.
_V1_COLUMNS = {
    "requests": {"request_id", "commit_order", "operation", "requester_id",
                 "actor", "resource_key", "priority", "payload", "state",
                 "created_at", "updated_at"},
    "data_jobs": {"job_id", "request_id", "operation", "tool", "storage_name",
                  "source_storage", "destination_storage", "source",
                  "destination", "target", "options", "priority", "state",
                  "reason_code", "preview_fingerprint", "preview_expires_at",
                  "volcano_job_ref", "artifact_uri", "result_summary",
                  "created_at", "updated_at"},
    "control_state": {"id", "maintenance", "drain", "reason", "changed_by",
                      "changed_at"},
    "builds": {"build_id", "repo_url", "git_ref", "commit_sha", "images",
               "node_name", "state", "reason_code", "log_uri", "created_at",
               "finished_at"},
    "releases": {"id", "component", "image", "tag", "digest", "state", "actor",
                 "applied_at"},
}


def test_every_post_v1_column_rides_both_migration_paths(tmp_path):
    # 일반 그물(BACKLOG §2.5 "ALTER 경로 일반 회귀 커버리지 갭" -- 슬라이스 14 가
    # 파킹했고 그 파킹이 실제 프로덕션 500 을 냈다): 컬럼별 구형 재생성 테스트는
    # 이미 태어난 컬럼만 지킨다 -- CREATE 에만 넣고 _ensure_columns 를 잊는 미래
    # 컬럼은 신규 DB(테스트 전부)에선 멀쩡하고 기배포 DB(라이브)에서만 없다.
    # 등식: 현재 전체 컬럼 = v1 ∪ ensure 목록. 정상 진화(양쪽 추가)는 v1 무수정
    # 으로 통과하고, 규율 위반과 무결정 삭제만 빨개진다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    pairs = _ensure_pairs()
    assert len(pairs) == 23      # 추출 자기 검증 -- 0 매치면 등식이 공허해진다
    for table, v1 in _V1_COLUMNS.items():
        cols = {r["name"] for r in db.query(f"PRAGMA table_info({table})")}
        ensured = {c for t, c, _ in pairs if t == table}
        assert cols == v1 | ensured, table


def test_ensure_columns_types_match_create_declarations(tmp_path):
    # 선언형 패리티의 일반화: files_count 계열(BIGINT/int4 실 사고)과 diag_logs 만
    # 컬럼별 형 단언이 있었다. 신규 DB 의 선언형은 CREATE 산출물이므로 ensure
    # 목록의 형과 전 컬럼 대조하면 두 경로가 같은 형으로 수렴함이 고정된다 --
    # 다르면 기배포 DB 만 다른 형으로 굳는다(int4 천장 사고의 일반형).
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    for table, column, coltype in _ensure_pairs():
        assert _declared_type(db, table, column) == coltype, (table, column)
```

- [ ] **Step 2: 즉시 GREEN 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_migrations.py -q`
Expected: 전부 PASS. v1 기준선이 틀렸다면(등식 RED) `현재 PRAGMA 컬럼 − ensure 컬럼` 으로 재실측해 v1 을 고친다 — 정의상 그 차집합이 v1 이다(위 전제 재확인에서 5테이블 전부 검산 완료).

- [ ] **Step 3: 뮤테이션으로 이빨 확인 후 원복 (이것이 이 태스크의 RED — 미래 사고의 시뮬레이션)**

`cp src/dms/migrations.py /tmp/slice30-migrations.py.bak` 후: CREATE `data_jobs` 블록의 `diag_logs TEXT,` 아래에 가짜 미래 컬럼 `future_col TEXT,` 를 **CREATE 에만** 추가(ensure 목록은 그대로) → `test_every_post_v1_column_rides_both_migration_paths` 만 RED(`data_jobs` 등식: 왼쪽에 future_col, 오른쪽에 없음). **기존 전 테스트가 초록으로 남는 것을 함께 관찰한다** — 신규 DB 만 보는 기존 그물이 이 사고 계열을 영원히 못 잡는다는 실증(슬라이스 14 가 라이브에서 겪은 그대로). 원복(`cp`), Step 2 재확인.

- [ ] **Step 4: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
test(migrations): 이중 경로 일반 그물 — v1 기준선 등식(CREATE 에만 넣은 미래 컬럼을 잡는다) + ensure 선언형 패리티 전 컬럼화

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- tests/test_migrations.py
```

---

### Task 3: `KubernetesClient` lazy-init 이중검사의 결정적 테스트 (항목 2, 테스트만 — pragma 유지)

**Files:**
- Modify: `tests/test_execution_volcano.py`

**Interfaces:**
- Produces: `_ensure`(`execution_volcano.py:313-327`)의 세 계약 — ① 부분 초기화 실패 시 게이트(`_core`)가 닫힌 채 남아 재시도가 가능하다("`_core` 는 마지막에" 순서의 행동적 의미), ② 두 번째 호출은 fast path(재초기화 없음), ③ 락 대기 중 타 스레드가 완주했으면 안쪽 재검사가 재초기화를 막는다.
- **왜 스레드 경주가 아닌가**: 진짜 경쟁 창은 락 대기 타이밍이라 스레드로는 비결정적(flaky)이다. ①은 1회 실패 주입, ③은 `__enter__` 에서 완주 상태를 만드는 가짜 락으로 — 창 자체를 결정적으로 재현한다.
- **왜 sys.modules 대역인가**: `kubernetes` 는 venv 에 없다(실측 `ModuleNotFoundError`). `_ensure` 안의 `import kubernetes` 는 sys.modules 를 먼저 보므로 `monkeypatch.setitem` 대역이 새 의존성 0 으로 잡힌다. 클래스 pragma(`# pragma: no cover`)는 그대로 둔다 — 커버리지 표기는 실 k8s API 경로(실증 대상 유지)의 것이고, 테스트는 표기와 무관하게 로직을 검증한다.

- [ ] **Step 1: 테스트를 쓴다**

`tests/test_execution_volcano.py` — 파일 끝에 추가(파일 기존 import 에 `pytest` 가 없으면 추가):

```python
def _fake_kubernetes(monkeypatch, *, fail_apps_once=False):
    # kubernetes 는 venv 에 없다(실측) -- _ensure 안의 `import kubernetes` 가
    # sys.modules 를 먼저 보는 것을 이용해 대역을 꽂는다(새 의존성 0).
    import sys
    from types import SimpleNamespace

    state = {"load_calls": 0, "fail_apps": fail_apps_once}

    def load_incluster_config():
        state["load_calls"] += 1

    def apps():
        if state["fail_apps"]:
            state["fail_apps"] = False       # 1회성 -- 일시 장애의 재현
            raise RuntimeError("apps init failed")
        return "apps"

    fake = SimpleNamespace(
        config=SimpleNamespace(load_incluster_config=load_incluster_config),
        client=SimpleNamespace(CoreV1Api=lambda: "core",
                               CustomObjectsApi=lambda: "custom",
                               AppsV1Api=apps))
    monkeypatch.setitem(sys.modules, "kubernetes", fake)
    return state


def test_k8s_client_partial_init_failure_keeps_the_gate_closed(monkeypatch):
    # "_core 는 마지막에"(execution_volcano._ensure 주석)의 행동적 의미: 세 핸들
    # 중 하나라도 못 만들면 게이트(_core)가 닫힌 채 남아야 다음 호출이 재시도한다.
    # _core 를 먼저 대입하는 리팩터가 들어오면 게이트가 반쯤 초기화된 채 열려,
    # 이후 _apps 사용처(예: 롤아웃 observe)가 None 으로 터진다 -- 슬라이스 14 가
    # 이중검사를 넣은 바로 그 창의 순서 짝이다.
    from dms.execution_volcano import KubernetesClient
    state = _fake_kubernetes(monkeypatch, fail_apps_once=True)
    c = KubernetesClient("dms")
    with pytest.raises(RuntimeError):
        c._ensure()
    assert c._core is None                   # 게이트는 닫힌 채여야 한다
    c._ensure()                              # 일시 장애가 걷히면 재시도가 완주한다
    assert (c._core, c._custom, c._apps) == ("core", "custom", "apps")
    assert state["load_calls"] == 2          # 실패 1 + 성공 1 -- 재시도의 증거


def test_k8s_client_second_ensure_is_a_fast_path(monkeypatch):
    # 바깥 검사(락 없는 조기 반환): 초기화 후의 매 호출이 락을 잡으면 안 된다 --
    # 폴링 경로(틱마다 observe)가 전부 이 앞을 지난다.
    from dms.execution_volcano import KubernetesClient
    state = _fake_kubernetes(monkeypatch)
    c = KubernetesClient("dms")
    c._ensure()
    c._ensure()
    assert state["load_calls"] == 1


def test_k8s_client_recheck_after_lock_wait_skips_reinit(monkeypatch):
    # 이중검사의 **안쪽** 검사를 결정적으로 재현한다: "락 대기 중 다른 스레드가
    # 초기화를 끝낸" 상황을, __enter__ 에서 핸들을 채우는 가짜 락으로 흉내 낸다
    # (실 스레드 경주는 타이밍 비결정 -- flaky 를 만들지 않는다). 안쪽 검사가
    # 없으면 두 번째 진입자가 이미 쓰이고 있는 핸들 셋을 통째로 갈아치운다.
    from dms.execution_volcano import KubernetesClient
    state = _fake_kubernetes(monkeypatch)
    c = KubernetesClient("dms")

    class _LockThatLosesTheRace:
        def __enter__(self):
            c._core, c._custom, c._apps = "core", "custom", "apps"

        def __exit__(self, *args):
            return False

    c._init_lock = _LockThatLosesTheRace()
    c._ensure()
    assert state["load_calls"] == 0          # 재초기화 없음 -- 안쪽 검사가 이빨
    assert (c._core, c._custom, c._apps) == ("core", "custom", "apps")
```

- [ ] **Step 2: 즉시 GREEN 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_execution_volcano.py -q`
Expected: 전부 PASS(신규 3 포함) — 현행 `_ensure` 가 세 계약을 이미 지킨다(그물 항목).

- [ ] **Step 3: 뮤테이션으로 이빨 확인 후 원복 (이것이 이 태스크의 RED)**

`cp src/dms/execution_volcano.py /tmp/slice30-execution_volcano.py.bak` 후: `_ensure` 의 `core = kubernetes.client.CoreV1Api()` / `self._core = core` 짝을 `self._core = kubernetes.client.CoreV1Api()` 한 줄(초기화 블록 **첫** 대입)로 바꾸고 마지막 `self._core = core` 줄 삭제 → `test_k8s_client_partial_init_failure_keeps_the_gate_closed` 가 `assert c._core is None` 에서 RED(부분 실패 후 게이트가 열려 있고, 이어지는 재시도 검증도 fast path 조기 반환 탓에 `_apps` None 으로 무너진다). 다른 2건은 초록 — 순서 계약은 부분 실패 시나리오만이 구분한다는 실증. (보너스 관찰, 선택: 원복 후 안쪽 `if self._core is not None: return` 을 지우면 `recheck` 테스트만 RED.) `cp` 원복, Step 2 재확인.

- [ ] **Step 4: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
test(execution_volcano): KubernetesClient lazy-init 이중검사를 결정적으로 커버 — 부분 실패 게이트·fast path·락 대기 후 재검사(스레드 경주 없이, sys.modules 대역)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- tests/test_execution_volcano.py
```

---

### Task 4: 슬라이스 15 잔여 — None 입력 그물 + summary 픽스처 현행화 (항목 5a·5b, 테스트만)

**Files:**
- Modify: `tests/test_job_runner_parsers.py`
- Modify: `tests/test_job_runner_commands.py`
- Modify: `tests/test_execution_volcano.py`

**Interfaces:**
- Produces: ① `or ""` 반쪽 약속의 검증 — 파서 계약은 "절대 예외 없음"(fail-soft, `parsers.py` docstring)이고 구현은 `stdout or ""` 로 None 까지 삼키겠다고 약속해 왔는데, 기존 테스트는 빈 문자열만 본다(실측: `parse_sync_counts("")` 등). None 과 "" 는 다른 입력이다 — truthy 로 뭉개는 검사가 아니라 **두 입력 각각**의 계약을 고정한다. ② `read_summary` 픽스처의 사문 계약 제거 — 실 summary 는 정확히 3키 `{"returncode","files","bytes"}`(`runner.py:135-136`)인데 픽스처가 슬라이스 15 이전의 `{"files": 3}` 를 박제하고 있다(`test_execution_volcano.py:174·190`). 등식 단언을 유지한 채 3키로 바꿔 pass-through(키 필터·강제 변환 없음) 계약을 실 모양으로 고정한다. **이빨이 얇은 항목임을 자인한다** — 주목적은 다음 독자가 죽은 계약을 배우지 않게 하는 것.

- [ ] **Step 1: 테스트를 쓴다**

`tests/test_job_runner_parsers.py` — 파일 끝에 추가:

```python
def test_parsers_accept_none_stdout():
    # 계약은 "절대 예외 없음"(fail-soft, 이 모듈 docstring)이고 구현은
    # `stdout or ""` 로 None 까지 삼키겠다고 반쪽 약속해 왔다(슬라이스 15) --
    # 검증은 빈 문자열뿐이었다(BACKLOG §2.5). None 은 "" 와 다른 입력이다:
    # subprocess 캡처가 어긋난 병적 경로에서 파서가 TypeError 로 잡을 죽이면
    # 계약 위반이고, 그 예외는 stepper _finalize 앞이라 잡이 Executing 에 박힌다.
    assert parse_sync_counts(None) == (None, None)
    assert parse_nsync_counts(None) == (None, None)
    assert parse_rm_counts(None) == (None, None)
```

(파일 상단 import 에 `parse_nsync_counts` 가 이미 있는지 확인 — 없으면 추가.)

`tests/test_job_runner_commands.py` — 파일 끝에 추가:

```python
def test_parse_hostfile_none_is_empty():
    # hostfile 미물질화(Volcano ssh 플러그인 지연) 경로에서 read 가 None 을 넘겨도
    # 빈 목록이어야 한다 -- `text or ""`(commands.py) 반쪽 약속의 검증. 파서
    # 계열(test_parsers_accept_none_stdout)과 같은 그물.
    assert parse_hostfile(None) == []
```

`tests/test_execution_volcano.py` — 픽스처 현행화(테스트 2곳 수정, 신규 아님):
`test_read_summary_reads_artifact`(`:170-176`)와 `test_read_summary_reconstructs_from_labels`(`:185-199`)에서 `'{"files": 3}'` → `'{"returncode": 0, "files": 3, "bytes": 50}'`, 단언 `== {"files": 3}` → `== {"returncode": 0, "files": 3, "bytes": 50}` (각 파일 내 전 등장). 두 테스트 중 한쪽에 주석 추가:

```python
    # 픽스처는 실 summary 계약과 같은 모양이어야 한다: runner _build_summary 는
    # 항상 정확히 3키 {"returncode","files","bytes"} 를 쓴다(슬라이스 15 §2.3).
    # 옛 {"files": 3} 은 사문 계약의 박제였다(BACKLOG §2.5). 등식 단언을 유지해
    # read_summary 가 키를 거르거나 변환하지 않는 pass-through 임도 함께 고정한다.
```

- [ ] **Step 2: 즉시 GREEN 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_job_runner_parsers.py tests/test_job_runner_commands.py tests/test_execution_volcano.py -q`
Expected: 전부 PASS(신규 2 포함) — `or ""` 가 이미 있어 그물 항목이고, read_summary 는 shape 무관 pass-through 라 픽스처 교체가 무해하다.

- [ ] **Step 3: 뮤테이션으로 이빨 확인 후 원복 (이것이 이 태스크의 RED)**

`cp src/dms_job_runner/parsers.py /tmp/slice30-parsers.py.bak` 후: `_last_int` 의 `pattern.findall(text or "")` 를 `pattern.findall(text)` 로 → `test_parsers_accept_none_stdout` 가 `parse_sync_counts(None)` 의 TypeError 로 RED("" 테스트는 초록으로 남는다 — None 과 "" 가 다른 그물임의 실증). 원복. 같은 요령으로 `cp src/dms_job_runner/commands.py /tmp/slice30-commands.py.bak` 후 `(text or "").splitlines()` → `text.splitlines()` → `test_parse_hostfile_none_is_empty` RED. 원복, Step 2 재확인. (픽스처 현행화 쪽은 뮤테이션 무의미 — "이빨 얇음"을 위에서 자인했다.)

- [ ] **Step 4: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
test(runner+volcano): 슬라이스 15 잔여 — `or ""` None 입력 그물(파서 3종·parse_hostfile) + read_summary 픽스처를 실 3키 계약 모양으로 현행화

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- tests/test_job_runner_parsers.py tests/test_job_runner_commands.py tests/test_execution_volcano.py
```

---

### Task 5: information_schema 쿼리를 `current_schema()` 로 한정 (항목 5c, 코드 — TDD)

**Files:**
- Modify: `src/dms/migrations.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces: `_column_exists` PG 분기(`:437-441`)와 `_widen_count_columns`(`:459-463`)의 information_schema 조회가 `table_schema = current_schema()` 로 한정된다. 파라미터·반환·DDL 무변경.
- **왜**: 현행 쿼리는 DB 의 **모든** 스키마에서 같은 (table, column)을 찾는다. 다른 스키마에 동명 테이블이 생기면(예: 운영자가 백업을 `backup.data_jobs` 로 복원) `_column_exists` 가 "이미 있다"로 오판 → ALTER 건너뜀 → **라이브만 컬럼이 없는** 슬라이스 14 실 500 의 재현 경로이고, `_widen_count_columns` 는 남의 스키마 int4 를 보고 public 에 매 배포 불필요한 ACCESS EXCLUSIVE ALTER 를 치거나(소음), 남의 bigint 를 보고 진짜 int4 를 영영 안 넓힌다(침묵 실패). 비정규화 `CREATE TABLE`/`ALTER TABLE` 이 떨어지는 곳이 정확히 `current_schema()`(search_path 선두)이므로 그 스키마만 본다. `'public'` 하드코드가 아닌 이유: search_path 를 바꾼 배포에서 또 틀린다. sqlite 분기(PRAGMA)는 무접촉.
- **검증 수단의 한계 자인**: 실 PG 하니스가 없어 다중 스키마 거동 자체는 재현 불가 — `_FakeDb` 에 query 기록을 추가해 **술어가 나가는지**를 계약으로 고정한다(어드바이저리 락 `calls` 기록과 같은 계열 타협, `test_migrations.py:42-44` 주석 선례).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_migrations.py` — `_FakeDb.__init__` 에 `self.queries = []` 추가, `query` 를 기록형으로:

```python
    def query(self, sql, params=None):
        # (sql, params) 기록: information_schema 조회가 current_schema() 로
        # 한정되는지(슬라이스 30)는 반환값으로 구분할 수 없다 -- 대역은 스키마
        # 개념이 없어서다. 락 키를 params 로 못박은 calls 기록과 같은 타협.
        self.queries.append((sql, params))
        return [{"data_type": self._data_type}]
```

파일 끝에 추가:

```python
def test_column_exists_pg_scopes_to_current_schema():
    # 미필터 쿼리는 DB 의 모든 스키마에서 (table, column)을 찾는다 -- 다른
    # 스키마의 동명 테이블(백업 복원 등)이 "이미 있다"로 오판되면 ALTER 를
    # 건너뛰어 라이브만 컬럼이 없는 슬라이스 14 실 500 이 재현된다(BACKLOG §2.5
    # "단일 스키마 배포에서만 안전"). 비정규화 DDL 이 떨어지는 current_schema()
    # 만 본다 -- 'public' 하드코드는 search_path 를 바꾼 배포에서 또 틀린다.
    from dms.migrations import _column_exists
    fake = _FakeDb("postgresql", "bigint")
    assert _column_exists(fake, "data_jobs", "diag_logs") is True
    sql, params = fake.queries[-1]
    assert "table_schema = current_schema()" in sql
    assert params == {"t": "data_jobs", "c": "diag_logs"}


def test_widen_count_columns_scopes_to_current_schema():
    # _column_exists 와 같은 이유의 쌍둥이: 남의 스키마 int4 를 보면 매 배포
    # 불필요한 ACCESS EXCLUSIVE ALTER, 남의 bigint 를 보면 진짜 int4 가 영영
    # 안 넓혀진다(침묵 실패 -- 더 나쁜 방향).
    from dms.migrations import _widen_count_columns
    fake = _FakeDb("postgresql", "bigint")
    _widen_count_columns(fake)
    assert len(fake.queries) == 2            # files_count·bytes_count 각 1회
    assert all("table_schema = current_schema()" in sql
               for sql, _ in fake.queries)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_migrations.py -q`
Expected: 신규 2건 FAIL — 둘 다 `"table_schema = current_schema()" in sql` 단언에서(현행 쿼리에 술어가 없다). 기존 전부 PASS(query 기록 추가는 무해 — 기존 테스트는 `executed`/`calls` 만 본다).

- [ ] **Step 3: migrations.py 를 고친다**

`_column_exists` 의 PG 분기 쿼리를:

```python
    # 슬라이스 30(BACKLOG §2.5 슬라이스 15 잔여): information_schema.columns 는
    # DB 전체 스키마를 본다 -- 다른 스키마의 동명 테이블·컬럼(예: 백업 복원
    # backup.data_jobs)을 "이미 있다"로 오판하면 ALTER 를 건너뛰어, 라이브만
    # 컬럼이 없는 슬라이스 14 실 500 이 재현된다. 비정규화 CREATE/ALTER 가
    # 떨어지는 곳이 current_schema()(search_path 선두)이므로 거기만 본다.
    rows = db.query(
        """SELECT 1 AS x FROM information_schema.columns
           WHERE table_schema = current_schema()
             AND table_name = :t AND column_name = :c""",
        {"t": table, "c": column})
```

`_widen_count_columns` 의 data_type 조회를(기존 멱등성 주석은 유지한 채 쿼리만):

```python
        rows = db.query(
            """SELECT data_type FROM information_schema.columns
               WHERE table_schema = current_schema()
                 AND table_name = 'data_jobs' AND column_name = :c""",
            {"c": column})
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_migrations.py tests/test_migrations_batch.py tests/test_migrations_policy_seed.py -q`
Expected: 전부 PASS — sqlite 실경로 테스트들(PRAGMA 분기)이 무영향의 1차 보증, `_FakeDb` 계열이 PG 분기 계약을 고정한다.

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

`cp src/dms/migrations.py /tmp/slice30-migrations.py.bak` 후: `_column_exists` 쪽 술어만 `table_schema = 'public'` 으로 교체 → `test_column_exists_pg_scopes_to_current_schema` 만 RED(하드코드는 current_schema 계약 위반 — search_path 배포에서 틀리는 바로 그 형태), widen 쪽 테스트는 초록으로 남는다(두 쿼리가 **각자** 계약을 가져야 한다는 실증). `cp` 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
fix(migrations): information_schema 조회 2곳을 current_schema() 로 한정 — 타 스키마 동명 테이블 오판(ALTER 건너뜀 → 라이브 전용 결손)의 경로 봉쇄

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- src/dms/migrations.py tests/test_migrations.py
```

---

### Task 6: planner 비원자 쌍 2곳 원자화 (항목 6, 코드 — TDD)

**Files:**
- Modify: `src/dms/repositories/requests.py`
- Modify: `src/dms/planner.py`
- Modify: `tests/test_repo_requests_finalize.py`
- Modify: `tests/test_planner.py`

**Interfaces:**
- Produces:
  - `RequestsRepository.set_state_with_result(request_id, to_state, *, reason_code, actor)` — `with self._db.transaction():` 안에서 `_apply_state` + `record_result`. 크래시 시 전부-또는-전무 → 요청이 Pending 으로 남아 다음 틱 `list_pending` 재계획이 완주한다.
  - `planner._reject`·conflict 경로가 그 메서드 단일 호출로 교체된다(별도 커밋 쌍 소멸 — `record_result` 단독 호출은 src 에서 0 이 된다).
- Consumes: `_apply_state`(슬라이스 27 추출 — 트랜잭션은 호출자 소유), `db.transaction()` 비중첩(슬라이스 27 플랜 「전제 재확인」 4 — naive wrap 불가의 근거 그대로. `_apply_state` 경유라 이 함정을 다시 밟지 않는다).
- **finalize 와의 차이(계약으로 주석에 박는다)**: 멱등 가드 없음 — 호출자는 `list_pending` 이 고른 Pending 요청뿐이고, 고아 스윕처럼 종단 후 재호출하는 경로가 없다. 가드를 흉내 내면 없는 경로를 있는 척하는 것이다.
- **planner 수준 RED 의 함정**: `run_once` 는 요청별 예외를 삼킨다(`planner.py:63-70`) — planner 테스트는 `pytest.raises` 가 아니라 "삼켜진 뒤 상태 잔존"을 단언한다(레포 수준만 `raises`).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_planner.py` — 파일 끝에 추가:

```python
def test_reject_crash_between_writes_leaves_request_pending(db):
    """비원자 쌍(BACKLOG §2.4, 슬라이스 27 발견): set_state(REJECTED) 커밋 후
    record_result 전에 죽으면 요청은 Rejected 인데 results 가 없다 -- Rejected 는
    종단이라 고아 스윕 시야 밖, 결손이 영구다(finalize 의 슬라이스 24 실증과
    동일 계열). 원자화 후엔 둘 다 롤백 -> Pending 잔존, run_once 의 요청별 예외
    격리가 plan_error 로 남기고 다음 틱 재계획이 완주한다."""
    repos = Repositories(db)
    rid = _scan_request(repos)      # 스토리지 미시드 -> storage_missing 거부 경로

    def _boom(*_args, **_kwargs):
        raise RuntimeError("crash before record_result")

    repos.requests.record_result = _boom     # 인스턴스 속성이 메서드를 가린다
    _planner(repos).run_once(now_iso=NOW)    # 요청별 try/except 가 삼킨다(무전파)
    del repos.requests.record_result         # 원복 -- 클래스 메서드가 되살아난다
    # 전부-또는-전무: Pending 그대로, Rejected 전이도 results 도 없다.
    assert repos.requests.get(rid)["state"] == "Pending"
    assert all(t["to_state"] != "Rejected" for t in repos.requests.transitions(rid))
    assert db.query("SELECT request_id FROM results WHERE request_id = :r",
                    {"r": rid}) == []
    # 다음 틱: 정상 완주 -- 거부 상태와 results 행이 함께 남는다.
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:storage_missing"
    assert repos.requests.get(rid)["state"] == "Rejected"
    row = db.query_one("SELECT terminal_state, reason_code FROM results"
                       " WHERE request_id = :r", {"r": rid})
    assert (row["terminal_state"], row["reason_code"]) == ("Rejected",
                                                           "storage_missing")


def test_conflict_crash_between_writes_leaves_request_pending(db):
    # 비원자 쌍의 두 번째(conflict 경로) -- _reject 와 같은 계약. 첫 요청은 정상
    # 계획(planned 경로는 record_result 를 안 부른다), 둘째가 conflict 에서 크래시.
    repos = Repositories(db)
    _seed_storage(repos)
    _seed_policy(repos)
    _seed_report(repos)
    first = _scan_request(repos)
    second = _scan_request(repos)   # 같은 resource_key -- 뒤가 conflict

    def _boom(*_args, **_kwargs):
        raise RuntimeError("crash before record_result")

    repos.requests.record_result = _boom
    _planner(repos).run_once(now_iso=NOW)
    del repos.requests.record_result
    assert repos.requests.get(first)["state"] == "Planned"     # 격리: 옆은 무사
    assert repos.requests.get(second)["state"] == "Pending"
    assert db.query("SELECT request_id FROM results WHERE request_id = :r",
                    {"r": second}) == []
    assert _planner(repos).run_once(now_iso=NOW)[second] == "conflict"
    assert repos.requests.get(second)["state"] == "Conflict"
    row = db.query_one("SELECT terminal_state, reason_code FROM results"
                       " WHERE request_id = :r", {"r": second})
    assert (row["terminal_state"], row["reason_code"]) == ("Conflict",
                                                           "resource_conflict")
```

`tests/test_repo_requests_finalize.py` — 파일 끝에 추가:

```python
def test_set_state_with_result_is_atomic_and_retryable(db):
    # planner 크래시 테스트의 하부 메커니즘을 레포 수준에서 직접 고정한다
    # (finalize 크래시 테스트와 같은 골격 -- 같은 결함 계열의 같은 처방).
    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="alice",
                                actor="alice", resource_key="k2", payload={},
                                priority="mid")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("crash before record_result")

    repos.requests.record_result = _boom
    with pytest.raises(RuntimeError):
        repos.requests.set_state_with_result(
            rid, RequestState.REJECTED, reason_code="storage_missing",
            actor="planner")
    del repos.requests.record_result
    assert repos.requests.get(rid)["state"] == "Pending"
    assert all(t["to_state"] != "Rejected" for t in repos.requests.transitions(rid))
    assert db.query("SELECT request_id FROM results WHERE request_id = :r",
                    {"r": rid}) == []
    repos.requests.set_state_with_result(
        rid, RequestState.REJECTED, reason_code="storage_missing", actor="planner")
    assert repos.requests.get(rid)["state"] == "Rejected"
    assert len(db.query("SELECT request_id FROM results WHERE request_id = :r",
                        {"r": rid})) == 1
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_planner.py tests/test_repo_requests_finalize.py -q`
Expected: 신규 3건 FAIL — planner 2건은 `assert ... == "Pending"` 에서 실제값 `"Rejected"`/`"Conflict"`(set_state 가 이미 커밋된 **원 결함의 정확한 재현**), 레포 1건은 `set_state_with_result` 부재의 AttributeError. 기존 전부 PASS.

- [ ] **Step 3: 코드를 고친다**

`src/dms/repositories/requests.py` — `finalize_from_job` 바로 아래에 신설:

```python
    def set_state_with_result(self, request_id, to_state: RequestState, *,
                              reason_code, actor):
        """planner 의 종단 판정(Rejected/Conflict) 전용 -- 전이와 results INSERT
        를 한 트랜잭션으로 묶는다(슬라이스 30, BACKLOG §2.4 -- finalize_from_job
        과 동일 처방). 별도 커밋이면 사이 크래시가 "종단인데 results 없음"을
        만들고, 종단 요청은 고아 스윕(terminal_jobs_with_live_request) 시야 밖이라
        결손이 영구다. 원자화 후엔 둘 다 롤백 -> 요청이 Pending 으로 남아 다음 틱
        list_pending 재계획이 완주한다. finalize 와 달리 멱등 가드가 없는 이유:
        호출자는 list_pending 이 고른 Pending 요청만 넘기고 스윕류의 종단 후
        재호출 경로가 없다 -- 가드를 흉내 내면 없는 경로를 있는 척하는 것이다."""
        with self._db.transaction():
            self._apply_state(request_id, to_state, reason_code=reason_code,
                              actor=actor)
            self.record_result(request_id, to_state, reason_code=reason_code)
```

`src/dms/planner.py` — `_reject`(`:73-78`) 교체:

```python
    def _reject(self, rid, reason):
        # 슬라이스 30: set_state + record_result 별도 커밋(비원자 쌍)을 레포의
        # 원자 메서드로 -- 사이 크래시가 만들던 "Rejected 인데 results 없음"(고아
        # 스윕 시야 밖의 영구 결손)이 전부-또는-전무가 된다. finalize(슬라이스
        # 27, 실증 5/5)와 동일 처방.
        self._repos.requests.set_state_with_result(
            rid, RequestState.REJECTED, reason_code=reason, actor="planner")
        return f"rejected:{reason}"
```

conflict 경로(`:130-136`)의 `set_state`+`record_result` 쌍 교체:

```python
        if prior is not None and prior["commit_order"] < req["commit_order"]:
            # 슬라이스 30: _reject 와 같은 원자화(비원자 쌍의 두 번째).
            self._repos.requests.set_state_with_result(
                rid, RequestState.CONFLICT, reason_code="resource_conflict",
                actor="planner")
            return "conflict"
```

- [ ] **Step 4: 통과를 확인한다 (planner 소비자 광역 회귀)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_planner.py tests/test_repo_requests_finalize.py tests/test_repo_requests.py tests/test_controller_planner.py tests/test_controller.py tests/test_api_requests.py tests/test_batch_orchestrator_scan.py tests/test_batch_orchestrator_sync.py -q`
Expected: 전부 PASS — 기존 거부·conflict 테스트들(`rejected:*` 반환값·상태 단언)이 시그니처·거동 무회귀의 안전망이다.

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

`cp src/dms/repositories/requests.py /tmp/slice30-requests.py.bak` 후: `set_state_with_result` 의 `with self._db.transaction():` 줄을 지우고 두 호출을 디덴트(원자화 이전과 등가) → 신규 3건 전부 `"Pending"` 단언에서 RED(실제 `"Rejected"`/`"Conflict"` + results 0행 — 원 결함의 정확한 재현), **기존 정상 경로 테스트는 초록으로 남는 것을 함께 관찰한다**(정상 경로만 보는 그물로는 영원히 못 잡는 결함이라는 증거 — finalize 뮤테이션과 동일 구도). `cp` 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
feat(planner+repo): 거부·충돌 종단 원자화 — set_state_with_result(전이+results 한 트랜잭션, _apply_state 재사용)로 비원자 쌍 2곳 소멸

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- src/dms/repositories/requests.py src/dms/planner.py tests/test_repo_requests_finalize.py tests/test_planner.py
```

---

### Task 7: 마감 검증 — 전체 스위트 + 프론트 확인 + 불변 조항 (커밋 없음)

**Files:** 없음(검증만)

- [ ] **Step 1: 백엔드 전체 스위트**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: **약 1282 passed**(기준선 1266 + 신규 16: T1 2 + T2 2 + T3 3 + T4 4 + T5 2 + T6 3 — 근사치다. 수가 다르면 신규 수를 다시 세되 **failed 0 이 본질**이다).

- [ ] **Step 2: 프론트 (무접촉 확인)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: Task 0 Step 2 와 **동일 수치** 초록. 빨개지면 이 슬라이스가 원인일 수 없다(diff 0) — 환경 문제로 판단하되 진행 전에 보고.

- [ ] **Step 3: 계약·불변 조항 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && git status --porcelain && git log --oneline -7 && git diff HEAD~6 --stat -- frontend deploy/k8s docs legacy`
Expected: 작업 트리 clean, 커밋 6건(T1~T6), `frontend`·`deploy/k8s`·`legacy` **diff 0**, `docs` 는 이 플랜 파일뿐. 커밋 대상이 정확히 9파일(소스 3: migrations.py·requests.py·planner.py / 테스트 6: test_migrations·test_execution_volcano·test_job_runner_parsers·test_job_runner_commands·test_repo_requests_finalize·test_planner)인지 확인. 스키마 DDL 무변경(신규 테이블·컬럼·인덱스 0)도 `git diff` 로 재확인.

---

## 플랜 이후: 배포·실증 (별도 ops, 플랜 태스크 밖)

**테스트만인 태스크(T1~T4)의 실증은 스위트 초록 자체다** — 배포로 얻을 추가 증거가 없다. **코드 2건(T5·T6)은 제어면 재배포가 필요하다**: migrations·requests·planner 는 전부 api/controller/migrate 이미지 경로다. 관례(슬라이스 12~27)대로 **매니페스트-우선**: 태그 bump→커밋→그 커밋에서 클러스터 내 빌드. 현재 제어면 **d40** → **`dms` 만 d41**(5곳: `30-migrate-job.yaml:25`, `40-api.yaml:67·84`, `41-controller.yaml:35·52`). 에이전트·러너(`DMS_JOB_IMAGE`)는 d35 유지 — `dms_job_runner` 소스 무변경이라 올릴 이유가 없다. **스키마 DDL 무변경이므로 파괴적 migrate 절차(행 수 선확인 등)는 불요** — 단 migrate Job 재실행은 관례대로 수행한다(T5 의 새 introspection 쿼리가 실 PG 를 지나는 것 자체가 실증이다).

**1. 태그 범프 커밋 + 클러스터 내 빌드 + migrate + apply**

```bash
# (a) 5곳 d40→d41 후:
git commit -m "deploy(k8s): 제어면 d41 (슬라이스 30 테스트 부채 마감 — planner 원자화·information_schema 스키마 한정)" -- deploy/k8s
# main 병합·push 후 그 커밋에서 빌드(빌드 파드는 GitHub 에서 clone -- 배포자 몫):
# (b) 빌드 파드 -- 슬라이스 25 플랜의 build_build_pod 스니펫 그대로, images=["dms"],
#     DMS_BUILD_TAG="d41". 로그에서 DMS_COMMIT_SHA=<범프 커밋>·DMS_BUILD_OK 확인 후 삭제.
# (c) migrate Job 재실행(Job 은 immutable -- delete 후 재적용):
kubectl -n dms delete job dms-migrate --ignore-not-found
kubectl apply -f deploy/k8s/30-migrate-job.yaml
kubectl -n dms wait --for=condition=complete job/dms-migrate --timeout=300s
# (d) 제어면 apply + 수렴:
kubectl apply -f deploy/k8s/40-api.yaml -f deploy/k8s/41-controller.yaml
kubectl -n dms rollout status deploy/dms-api deploy/dms-controller
```

**2. T5 실증 — current_schema() 한정 쿼리의 실 PG 무회귀**

migrate Job 완주(위 (c))와 파드 기동(initContainer migrate)이 곧 실증이다: 기존 컬럼이 전부 "있다"로 판정되어 `_ensure_columns` 가 no-op 으로 지나가야 한다. 술어가 잘못됐다면(current_schema 오판) 여기서 중복 ALTER 42701 로 죽거나 무한 ALTER 소음이 난다. 추가 확인:

```bash
kubectl -n dms exec deploy/dms-api -c api -- python -c "
import os
from dms.db import Database
from dms.migrations import _column_exists
db = Database.connect(os.environ['DMS_DATABASE_URL'])
print('diag_logs:', _column_exists(db, 'data_jobs', 'diag_logs'))
print('ghost:', _column_exists(db, 'data_jobs', 'no_such_column'))"
# 기대: diag_logs: True / ghost: False -- 한정 후에도 참·거짓 양방향이 옳다.
# (다중 스키마 시나리오 자체는 라이브에 없는 형상이라 재현하지 않는다 -- 정직 기록.)
```

**3. T6 실증 — 거부·충돌 종단의 상태+results 동시 존재 (정상 경로 무회귀)**

```bash
# (a) 거부 유발: 관리자 세션으로 존재하지 않는 스토리지를 겨눈 scan 요청 1건 제출
#     (API 직접 호출) -> planner 틱 후 Rejected 확인.
# (b) 같은 resource_key 로 두 건 연속 제출 -> 둘째가 Conflict.
# (c) 각 요청에 대해 상태와 results 가 **함께** 존재하는지(같은 트랜잭션의 산물):
kubectl -n dms exec deploy/dms-api -c api -- python -c "
import os
from dms.db import Database
db = Database.connect(os.environ['DMS_DATABASE_URL'])
print(db.query(
    \"SELECT r.request_id, r.state, res.terminal_state, res.reason_code\"
    \" FROM requests r LEFT JOIN results res ON res.request_id = r.request_id\"
    \" WHERE r.request_id IN ('<rid_a>', '<rid_b>')\"))"
# 기대: Rejected/storage_missing, Conflict/resource_conflict -- terminal_state 가
# None 이면 이 슬라이스가 닫은 결함의 재발이다.
# (d) 크래시 창의 라이브 재현은 하지 않는다: planner 를 정확히 두 문장 사이에서
#     죽일 결정적 수단이 없다 -- 단위 테스트(크래시 주입)가 계약을 고정하고
#     라이브는 정상 경로 무회귀로 갈음한다(슬라이스 27 finalize 와 동일한 정직 처리).
```

**4. 스모크 + 롤백 내성 메모**

```bash
# (a) 포탈 주요 화면(요청 목록·잡 상세·대시보드) 로드 무오류, events 에 새 error
#     이벤트 없음 확인. 작은 scan 1건 정상 종단(planned 경로 무회귀).
# (b) 롤백 내성(메모만): d40 롤백은 비원자 쌍과 미필터 쿼리로 되돌아갈 뿐 스키마
#     양립 문제가 없다(DDL 무변경) -- 자유로운 왕복이 가능하다.
```

실증 통과 후 `docs/superpowers/BACKLOG.md` 갱신(슬라이스 30 완료 기록 + §2.4 planner 쌍·ALL_TABLES 사각지대 해소 + §2.5 KubernetesClient(로직분)·ALTER 갭(일반 그물)·슬라이스 15 잔여·idx_requests_batch 해소 + 의도적 잔존 명시: 실 k8s API 경로/실 PG 하니스/CI 강제)을 별도 커밋으로 — 플랜 밖 관례.

---

## Self-Review

**1. 과제 커버리지 (후보 6건 전수 심사)**

| 후보 | 판정 | 담당 |
|---|---|---|
| 1. ALL_TABLES 사각지대 | 유효(테스트) — ALL_TABLES 는 19 유지(사용처 실측: 테스트뿐 → 소스 무접촉이 최소), 전수는 등식 테스트가 진다 | Task 1 |
| 2. KubernetesClient no-cover | **부분 유효**(테스트) — lazy-init 3계약만 결정적으로. 실 k8s API 경로는 **실증 대상 유지로 뺌**, pragma 존치 | Task 3 |
| 3. ALTER 경로 갭 | **갭 재정의 후 유효**(테스트) — 컬럼별 테스트는 전부 기존재(중복 안 만듦), 남은 갭은 미래 컬럼 일반 그물(신설)과 `_column_exists` PG 분기(Task 5 겸장). 실 PG ALTER TYPE 은 **하니스 없음 — 의도적 잔존으로 뺌** | Task 2·5 |
| 4. idx_requests_batch | 유효(테스트) — 개별 대신 16개 전수 등식으로 계열 마감 | Task 1 |
| 5. 슬라이스 15 잔여 3건 | 전부 유효 — (a)(b) 테스트(단 (b)는 이빨 얇음 자인), (c) 코드 | Task 4·5 |
| 6. planner 비원자 쌍 | 유효(코드) — **넣는다**: 영구 결손 창 + 실증된 처방 + 마지막 위생 슬라이스라는 3중 근거 | Task 6 |

**2. 뮤테이션(이빨) 매트릭스** — T1: batch_items CREATE 삭제/idx_requests_batch 줄 삭제 → 신규 등식만 RED, 기존 그물 초록(사각지대 실증). T2: CREATE 에만 가짜 컬럼 추가 → 일반 그물만 RED(슬라이스 14 사고의 시뮬레이션). T3: `_core` 첫 대입 이동 → 부분 실패 게이트 테스트 RED. T4: `or ""` 제거 → None 그물 RED(빈 문자열 그물은 초록 — 두 입력이 다른 계약). T5: `'public'` 하드코드 → current_schema 계약 RED. T6: 트랜잭션 감싸기 제거 → 크래시 주입 3건 RED(원 결함 재현), 정상 경로 초록. 각 Task 뮤테이션 1건 이상, 테스트-only Task 는 뮤테이션이 곧 RED(과제 지시 준수).

**3. 타입·이름 일관성** — `set_state_with_result(request_id, to_state, *, reason_code, actor)` 는 정의처(T6 Step 3)·planner 2 호출처·테스트 동일 철자(keyword-only 는 `set_state` 관례). 테스트 이름 6+α 건은 각 Step 1 과 뮤테이션 절 동일 철자. 헬퍼 재사용: `_table_names`·`_declared_type`·`_FakeDb`(test_migrations), `_scan_request`·`_seed_storage`·`_seed_policy`·`_seed_report`·`_planner`·`NOW`(test_planner), `_req` 골격(test_repo_requests_finalize — 단 신규 테스트는 Pending 에서 시작하므로 `create` 직접 호출). 도메인 값 실측: `RequestState.CONFLICT.value == "Conflict"`, `REJECTED.value == "Rejected"`.

**알려진 위험 / 판단:**
- **전수 등식은 유지비를 만든다** — 새 테이블·인덱스·컬럼마다 테스트 갱신이 강제된다. 그것이 목적이다(명시 결정 없는 스키마 진화를 막는 그물) — runs 제거·`len==19` 계보의 확장.
- **v1 기준선은 하드코드된 역사다** — 코드로 검증 불가능한(그래서 그물이 되는) 앵커. 등식 검산은 실측 완료. 기준선을 늘리는 수정은 "그 컬럼이 ensure 없이 모든 배포에 존재한다"는 주장이라 리뷰에서 걸리도록 주석에 박았다.
- **Task 3 은 프라이빗(`_ensure`·`_init_lock`)을 직접 만진다** — 공개 API(create/get 등)는 실 k8s 없이는 대역 놀음이라, 로직 검증엔 이 결합이 최소다. 리팩터로 이름이 바뀌면 테스트가 함께 바뀌어야 함을 수용한다(pragma 유지와 같은 타협의 값).
- **Task 5 의 계약은 SQL 문자열 검사다** — 실 PG 다중 스키마 재현이 불가한 환경에서의 정직한 최소(어드바이저리 락 키 검증 선례). 실 무회귀는 배포 §2 가 확인한다.
- **Task 6 의 `set_state_with_result` 는 세 번째 트랜잭션 소유자다** — `_apply_state` 의 호출자가 3곳(set_state·finalize·신설)이 된다. docstring 계약("호출자가 경계 소유")은 슬라이스 27 이 이미 박아 뒀고, 신설 메서드도 같은 형태를 따른다.
- **전체 수치 기대(≈1282)는 근사 명시** — 어긋나면 재계산하되 failed 0 이 판정 기준.

## 결정이 필요한 열린 질문

1. **`ALL_TABLES` 라는 이름은 이제 명백한 절반-거짓이다**(전 테이블이 아니라 도메인 테이블). 사용처가 테스트뿐이라 `DOMAIN_TABLES` 개명 비용은 낮지만, 이 슬라이스는 "소스 무접촉이 최소"를 택했다 — 개명은 다음 스키마 슬라이스가 있다면 그때.
2. **`record_result` 의 공개 표면** — T6 후 src 내 단독 호출 0(레포 내부 2곳뿐). 밑줄 프라이빗화는 API 정리 감이지만 테스트 호출처 정리가 함께 필요해 보류.
3. **CI 기술적 강제 부재(§2.5)** — 이 슬라이스도 손대지 않는다(수기 게이트 유지, `deploy/README` 명문화가 현행 최선). 의도적 잔존.
4. **실 PG 테스트 하니스** — ALTER TYPE 실경로·다중 스키마 재현의 근본 해법이지만 인프라 신설이라 위생 슬라이스 범위 밖. 의도적 잔존(배포 실증이 실 경로를 지난다).
