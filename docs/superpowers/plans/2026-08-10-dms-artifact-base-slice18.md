# 슬라이스 18 — 아티팩트 경로 설정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `DMS_ARTIFACT_BASE_URI`(아티팩트 base)를 포탈에서 설정 가능하게 만든다 — 저장은 `control_state` 싱글톤(새 테이블 금지), 해석은 단일 함수 `resolve_artifact_base`(DB 우선, NULL이면 env), 변경은 잡 1건이라도 있으면 409 잠금(+명시적 force), 검증은 3홉(API 즉석 실파일 왕복 / 에이전트 노드별 exists·writable / 컨트롤러 자기 관점 주기 기록)이다. 용도는 **설치·초기 구성 전용**이며 운영 중 이전은 목표가 아니다(설계 §2.3 잠금이 그 경계다).

**Architecture:** 아래에서 위로 쌓는다. (1) `control_state`에 컬럼 6종 + **전용 UPDATE 메서드 2개**(기존 `set_control_state`의 「인자 생략이 `build_node_name`을 조용히 NULL로 지우는」 함정을 복제하지 않기 위해 — 설계 §2.1), (2) 신규 중립 모듈 `src/dms/artifact_base.py`에 `strip_scheme` 승격 + `resolve_artifact_base` + 정규화 + 실파일 왕복 검증 + 컨트롤러 검증 루프 본체를 모으고 소비자 4곳(stepper 3사용처·어댑터·읽기 라우트 2곳)을 전부 이 모듈만 통과하게 재배선, (3) 스킴 제거를 `strip_scheme`(접두사 전용) 한 계열로 통일(전체 치환 4곳 제거 — 설계 §2.2), (4) admin 라우트 3종(GET/PUT/validate), (5) 에이전트 프로브는 리포트 **최상위 별도 필드** `artifact_base`로 나른다 — `mounts` 배열에 섞으면 리콘사일러가 유령 스토리지를 만든다(이 슬라이스 최대 함정), (6) 컨트롤러 `artifact-base-check` 루프, (7) 새 화면 `/admin/artifact-base`.

**Tech Stack:** Python 3.11 표준 라이브러리(FastAPI 라우트 3건, os 파일 왕복), React 18 + TanStack Query + Vitest/msw, SQLite/PostgreSQL 양립 마이그레이션(ALTER ADD COLUMN). 새 의존성 없음.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-10-dms-artifact-base-slice18-design.md`. 플랜과 충돌하면 **설계가 이긴다**.
- **새 pip/npm 의존성 금지.**
- **새 DB 테이블 금지** — `tests/test_migrations.py:173`이 `len(ALL_TABLES) == 20`을 고정한다. 상태는 전부 `control_state` 싱글톤 행의 새 컬럼에 둔다.
- **컬럼은 CREATE TABLE과 `_ensure_columns` 양쪽에** 넣는다. 한쪽만 넣으면 기배포 DB에서만 컬럼이 없다(슬라이스 14가 실 500으로 배운 교훈 — `migrations.py:415-416` 주석에 기록된 그대로).
- **에이전트 프로브 결과를 `mounts` 배열에 절대 섞지 않는다** — `reconciler.py:14-17`이 `mounts`를 순회하며 `storage_name` 기준으로 `storages.status`에 매핑한다. base 항목이 섞이면 유령 스토리지가 생기거나 리콘사일이 오염된다. 리포트 **최상위 별도 필드** `artifact_base`만 쓴다.
- **잠금 카운트는 `SELECT COUNT(*) FROM data_jobs` 전체다** — `artifact_uri IS NOT NULL`로 좁히지 않는다(설계 §2.3: 실패·타임아웃·취소·Rejected 잡은 그 컬럼이 NULL이지만 디스크의 stdout/stderr가 진단의 유일한 사본이다).
- **`set_control_state`에 얹지 않는다** — 그 UPDATE는 `build_node_name = :bn`을 무조건 쓴다(`repositories/control.py:105`). base는 해당 컬럼만 만지는 전용 UPDATE로 분리한다(설계 §2.1).
- **스킴 제거는 `strip_scheme`(접두사만) 한 계열로 통일** — `str.replace("file://", "")` 전체 치환 4곳(`execution_volcano.py:100,155,223`, `execution_manifests.py:174`)을 전부 제거한다(설계 §2.2).
- **null(모름)과 실패를 뭉개지 않는다**(설계 §4) — 에이전트 미보고·컨트롤러 미기록은 「확인 대기 중」이지 실패가 아니다. 화면·API 응답이 `pending`을 별도 상태로 구분한다.
- **새 reason_code는 `frontend/src/lib/reasonCodes.json`과 `frontend/src/lib/api.ts`의 `REASON_MESSAGES`를 같은 커밋에서 갱신**한다 — 백엔드 `tests/test_reason_codes_coverage.py`(src 리터럴 ⊆ json)와 프론트 `reasonCodes.test.ts`(json ⊆ REASON_MESSAGES, 죽은 키 금지)가 양방향으로 건다.
- **클러스터 접근(kubectl) 금지** — 실증은 「플랜 이후」 절에서 별도 수행.
- 아티팩트 파일의 이동/복사/GC 코드를 만들지 않는다(설계 §7, §1-10).
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- 백엔드 테스트 명령(이 워크트리 전용, `.venv`가 워크트리에 없다):
  `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest <대상> -q`
  전체 스위트는 **포그라운드**로 Bash timeout 600000ms. **기준선 998 passed.**
- 프론트: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx vitest run`(**기준선 215 passed / 48 files**), 타입체크 `npx tsc -b`.
- 주석은 **한국어**로 「왜」를 적는다.
- **origin으로 push 금지.** 커밋만 한다.

## 실측 고정값 (코드 직접 확인)

| 항목 | 값 |
|---|---|
| Settings 동결 | `@dataclass(frozen=True)`(`config.py:94`), 기본값 `file:///artifacts/dms`(`config.py:112`, from_env `:166-167`), 테스트베드 값 `file:///cephfs/dms/artifacts`(`deploy/k8s/20-config.yaml:27`). `from_env` 호출은 `cli.py:21`(agent)·`:31` 두 곳뿐 — 런타임 재읽기 경로 없음. `app.state.settings`(`api/app.py:32`)와 컨트롤러 루프가 같은 인스턴스 캡처 |
| base 소비 지점 | ① `stepper._build_spec`(`stepper.py:82`) **+ artifact_uri 기록 2곳**(`stepper.py:195`, `:233` — 설계 §1-2는 4곳이라 했지만 stepper 내부 사용처는 실측 3곳이다), ② `VolcanoExecutionAdapter` 생성자 캡처(`execution_volcano.py:71,77`, 조립 `wiring.py:22-26`), ③ `routes_artifacts._base`(`routes_artifacts.py:11-12`), ④ scan-path 통계(`routes_scan_paths.py:126`) |
| JobStepper 재생성 | 매 틱 새로 만든다(`controller.py:34-35`), 정책도 매 틱 DB 재조회(`stepper.py:67-69`) — resolve의 틱당 1쿼리는 기존 패턴과 동일 비용 |
| `set_control_state` 함정 | UPDATE가 `build_node_name = :bn`을 **무조건** 쓴다(`repositories/control.py:99-111`, `:bn`은 `:105`). 현재는 라우트가 항상 넘겨(`routes_control.py:37-39`) 잠복 — 같은 UPDATE에 컬럼을 얹으면 함정이 복제된다 |
| `_audit` 관례 | `repositories/control.py:12-19` — mutation_class/operation/target/before/after/actor, `audit_entries` `:159-161` |
| control_state 스키마 | CREATE `migrations.py:283-290`, 시드 `:337-341`, `_ensure_columns`의 `("control_state", "build_node_name", "TEXT")` `:419`. ConfigMap 이관 금지 근거는 `20-config.yaml:87-88`이 build_node_name으로 명문화 |
| 테이블 수 고정 | `tests/test_migrations.py:173` `assert len(ALL_TABLES) == 20` — 새 테이블 금지 |
| 전체 치환 4곳 | `execution_volcano.py:100`(`_volumes`), `:155`(`submit`의 summary 경로), `:222-223`(`_reconstruct_summary_path`), `execution_manifests.py:174`(`_artifact_dir`). `strip_scheme`(접두사만)은 `api/artifacts.py:46-47`, 호출 `routes_artifacts.py:12`·`routes_scan_paths.py:126` |
| 어댑터 생성자 캡처 | `execution_volcano.py:77` `self._artifact_base = artifact_base`, `_reconstruct_summary_path`가 그것을 씀(`:222-223`) — 컨트롤러 재시작 후 in-flight 잡 summary 유실 경로(설계 §1-7) |
| 리콘사일러 | `reconciler.py:14-17`이 fresh 리포트의 `report["mounts"]`를 순회, `mount.get("storage_name")` 매칭으로 `storages.status` 재계산(`:30` set_status) — **mounts에 섞인 항목은 전부 스토리지 판정 입력이 된다** |
| 에이전트 하달 경로 | 프로브 대상은 리포트 **응답**으로 내려간다(`routes_agent.py:22-31`: storages + identity_probe_targets + report_interval_seconds). 에이전트 env는 명시 나열이고 **envFrom 아님**(`50-agent-daemonset.yaml:5-12` 머리 주석이 명문화) — 에이전트가 base를 알 유일한 경로는 이 응답이다 |
| 에이전트 측 | `agent/runner.py:18-35` `build_report`(mounts/tools/identities/os 4키), `:48-51` run_once 리포트 조립, `:63-70` 응답 필드 타입 검증 후 state 교체, `:79-80` 초기 state `{"storages": [], "probe_targets": [], "interval": ...}`. `probe_mounts`는 `writable` 계산(`agent/probes.py:34`)하지만 `status` 판정에 미반영(`:35-42`) |
| 리포트 저장 | `repositories/agents.py:9-22` `ingest`가 리포트를 JSON 통짜(TEXT)로 `agent_reports`+`agent_nodes`에 저장 — **리포트에 키를 추가해도 스키마 변경 불필요**. `agent_nodes`/`agent_reports` 스키마는 `migrations.py:215-225`(node_name/report/reported_at뿐) |
| 마운트 경계 | API·컨트롤러 Deployment는 `/cephfs` hostPath(type: Directory)만 마운트(`40-api.yaml:122-126`, `41-controller.yaml:71-75`). 컨트롤러 마운트 의존은 `41-controller.yaml:7-14` 주석이 명문화. 잡 파드 hostPath도 `type: Directory` 강제(`execution_manifests.py:167-169`) — 노드에 디렉터리 존재가 파드 기동 가능의 직접 신호 |
| 조용한 실패 | `wiring.py:15-20` `read_text`가 OSError를 None으로 흡수, stepper는 SUCCEEDED 유지 + `summary_unavailable` 경고만(`stepper.py:182-190`) |
| artifact_uri 기록 | 성공 execution(`stepper.py:193-196`)과 성공 preview(`stepper.py:233-235`)뿐 — 실패·타임아웃·취소·Rejected는 NULL. `set_artifact`/`set_preview`의 `COALESCE(:a, artifact_uri)`(`data_jobs.py:238`, `:217`) |
| reason_code 계약 | `tests/test_reason_codes_coverage.py:102-114`가 src의 `detail=`/`reason_code=`/`DomainValidationError("...")` 리터럴 ⊆ `frontend/src/lib/reasonCodes.json`을 강제. 프론트 `reasonCodes.test.ts`는 json ⊆ `REASON_MESSAGES`(`api.ts:1-137`) + 죽은 키 금지. `request()`는 dict detail을 `http_<status>`로 접는다(`api.ts:177-184`) — **409 detail은 문자열 코드 하나여야 한다** |
| 컨트롤러 루프 목록 | `tests/test_controller.py:7-16`이 기본 6루프의 (이름, 간격) **정확한 리스트**를 고정 — 루프 추가 시 이 테스트를 같이 고친다. `build_loops`는 `controller.py:29-86` |
| 테스트 관례 | conftest `db`(SQLite+migrate)·`settings`(실 Settings)·`client`(`conftest.py:8-24`). stepper 테스트는 순수 `_Settings` 클래스 + 실 Repositories(`test_stepper_scan.py:5-12,32-33`). `StubExecutionAdapter`는 ref `stub-{phase}-{job_id}`, poll 기본 Succeeded, `submitted_specs()`(`execution.py:59-104`) |
| 어댑터 테스트 호출부 | `VolcanoExecutionAdapter(... artifact_base="file:///...")` 고정 문자열 주입이 tests 10여 곳(`test_execution_volcano.py:49,80,193,205,258` 등) — 생성자에서 str|callable 겸용 수용으로 호출부 무변경 유지 |
| 프론트 관례 | admin 페이지 라우트 `router.tsx:62`(`/admin/control`) + nav `AppShell.tsx:23`. 훅 관례 `useControlState.ts`/`usePolicies.ts`(useQuery+useMutation+invalidate), msw 테스트 관례 `ControlStatePage.test.tsx`/`PoliciesList.test.tsx`(setupServer/resetHandlers/QueryClientProvider). `Dialog`는 radix 래퍼(trigger/title/open/onOpenChange — `components/ui/Dialog.tsx`). 색 토큰 `text-ok`/`text-bad`/`text-muted` 존재 확인 |
| 기준선 | 백엔드 998 passed, 프론트 215 passed / 48 files |

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/migrations.py` (수정) | control_state에 `artifact_base_uri` + 검증 4컬럼 — CREATE와 `_ensure_columns` 양쪽 |
| `src/dms/repositories/control.py` (수정) | `set_artifact_base`(전용 UPDATE+감사) / `set_artifact_base_check`(검증 컬럼 전용 UPDATE) |
| `src/dms/artifact_base.py` (신규) | `strip_scheme` 승격 + `resolve_artifact_base` + `normalize_artifact_base` + `roundtrip_artifact_base` + `controller_check_once` |
| `src/dms/api/artifacts.py` (수정) | `strip_scheme` 재수출(기존 임포트 경로 유지) |
| `src/dms/stepper.py` (수정) | base 사용 3곳을 `_artifact_base()`(resolve 경유)로 |
| `src/dms/execution_volcano.py` (수정) | 어댑터 base 호출 시점 해석(str\|callable) + 전체 치환 3곳 제거 |
| `src/dms/execution_manifests.py` (수정) | `_artifact_dir` 전체 치환 1곳 제거 |
| `src/dms/wiring.py` (수정) | 어댑터에 resolve 클로저 주입 |
| `src/dms/api/routes_artifacts.py`, `routes_scan_paths.py` (수정) | 읽기 라우트 2곳 resolve 경유 |
| `src/dms/api/routes_artifact_base.py` (신규) | GET/PUT/validate — 정규화→잠금→즉석 검증→저장+감사, 3홉 응답 |
| `src/dms/api/app.py` (수정) | 라우터 등록 |
| `src/dms/api/routes_agent.py` (수정) | 리포트 응답에 `artifact_base_path` 하달 |
| `src/dms/agent/probes.py` (수정) | `probe_artifact_base` — mounts와 분리된 결과 |
| `src/dms/agent/runner.py` (수정) | 리포트 최상위 `artifact_base` 필드 + state 전파 |
| `src/dms/controller.py` (수정) | `artifact-base-check` 루프 등록 |
| `tests/test_artifact_base_resolve.py` (신규) | resolve + 소비자 4곳 재배선 계약 |
| `tests/test_strip_scheme_unification.py` (신규) | 접두사 전용 통일의 행동 + grep 수준 고정 |
| `tests/test_artifact_base_validation.py` (신규) | 정규화·실파일 왕복·컨트롤러 검증 |
| `tests/test_api_artifact_base.py` (신규) | 라우트 3종 — 잠금·force·감사·3홉 pending 구분 |
| `tests/test_repo_control.py`, `test_migrations.py`, `test_agent_probes.py`, `test_agent_runner.py`, `test_api_agent.py`, `test_reconciler.py`, `test_controller.py` (수정) | 각 계층 추가 계약 |
| `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts` (수정) | 새 사유 코드 7종 + 한국어 매핑 |
| `frontend/src/lib/types.ts` (수정) | `ArtifactBaseInfo`/`ArtifactBaseNodeCheck`/`ArtifactBaseControllerCheck` |
| `frontend/src/features/control/useArtifactBase.ts` (신규) | 조회(10s 폴링)/validate/PUT 훅 |
| `frontend/src/features/control/ArtifactBasePage.tsx` (+test, 신규) | 현재 값 + 3홉 패널 + 변경 폼 + 잠금 다이얼로그 |
| `frontend/src/app/router.tsx`, `frontend/src/app/AppShell.tsx` (수정) | `/admin/artifact-base` 라우트 + 네비게이션 진입점 |

---

### Task 1: control_state 컬럼 + 전용 UPDATE 리포지토리 메서드

**Files:**
- Modify: `src/dms/migrations.py`
- Modify: `src/dms/repositories/control.py`
- Test: `tests/test_migrations.py`, `tests/test_repo_control.py`

**Interfaces:**
- Consumes: `_audit` 관례(`control.py:12-19`), `_ensure_columns` 튜플(`migrations.py:406-425`), control_state CREATE(`migrations.py:283-290`).
- Produces (이후 Task가 이 이름·모양을 그대로 쓴다):
  - 컬럼: `control_state.artifact_base_uri TEXT`(NULL=미설정→env), `artifact_base_check_uri TEXT`, `artifact_base_check_ok INTEGER`, `artifact_base_check_reason TEXT`, `artifact_base_check_at TEXT`.
  - `ControlRepository.set_artifact_base(uri, *, actor, forced=False, affected_jobs=0)` — `artifact_base_uri` 컬럼만 만지는 UPDATE + 감사(mutation_class `"artifact_base"`, after에 `{artifact_base_uri, forced, affected_jobs}`).
  - `ControlRepository.set_artifact_base_check(*, uri, ok, reason, now_iso=None)` — 검증 4컬럼만 만지는 UPDATE, 감사 없음.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_repo_control.py` — 파일 머리 import에 `import json` 추가(현재 `import pytest`뿐):

```python
import json

import pytest
from dms.domain import DomainValidationError
from dms.repositories.control import ControlRepository
```

파일 끝에 테스트 3건 추가:

```python
def test_set_artifact_base_touches_only_its_column(db):
    # 설계 §2.1: set_control_state 의 UPDATE 는 build_node_name = :bn 을 무조건
    # 쓴다(control.py:105) -- 인자를 생략한 호출이 기존 값을 조용히 NULL 로 지우는
    # 함정이다(지금은 라우트가 항상 넘겨 잠복). 같은 UPDATE 에 컬럼을 얹으면 그
    # 함정이 복제되므로, 전용 UPDATE 는 자기 컬럼 밖을 만질 수 없어야 한다.
    repo = ControlRepository(db)
    repo.set_control_state(maintenance=True, drain=False, reason="r",
                           build_node_name=None, actor="admin")
    repo.set_artifact_base("file:///new/base", actor="admin")
    st = repo.control_state()
    assert st["artifact_base_uri"] == "file:///new/base"
    assert st["maintenance"] == 1 and st["reason"] == "r"   # 다른 컬럼 무변경
    # 반대 방향: set_control_state 재호출이 artifact_base_uri 를 지우지 않는다
    repo.set_control_state(maintenance=False, drain=False, reason=None, actor="admin")
    assert repo.control_state()["artifact_base_uri"] == "file:///new/base"


def test_set_artifact_base_audits_forced_and_affected_jobs(db):
    # force 통과는 감사에 반드시 남는다(설계 §2.3) -- "이 잡들의 아티팩트·로그
    # 열람이 깨진다"는 사실이 나중에 추적 가능해야 한다.
    repo = ControlRepository(db)
    repo.set_artifact_base("file:///new/base", actor="ops", forced=True,
                           affected_jobs=3)
    entry = repo.audit_entries(limit=1)[0]
    assert entry["mutation_class"] == "artifact_base"
    assert entry["actor"] == "ops"
    after = json.loads(entry["after_state"])
    assert after == {"artifact_base_uri": "file:///new/base", "forced": True,
                     "affected_jobs": 3}


def test_set_artifact_base_check_writes_check_columns_only(db):
    # 컨트롤러 관점 검증(설계 §2.4c)은 주기 기록이다 -- 운영자 변경 표시
    # (changed_by/changed_at)나 감사 로그를 오염시키면 안 된다.
    repo = ControlRepository(db)
    repo.set_artifact_base_check(uri="file:///x", ok=False,
                                 reason="artifact_base_missing",
                                 now_iso="2026-08-10T00:00:00Z")
    st = repo.control_state()
    assert st["artifact_base_check_uri"] == "file:///x"
    assert st["artifact_base_check_ok"] == 0
    assert st["artifact_base_check_reason"] == "artifact_base_missing"
    assert st["artifact_base_check_at"] == "2026-08-10T00:00:00Z"
    assert st["changed_by"] is None
    assert repo.audit_entries(limit=10) == []   # 감사 없음(주기 기록)
```

`tests/test_migrations.py` — 파일 끝에 추가:

```python
def test_migrate_adds_artifact_base_columns_to_existing_control_state(db):
    # 구형 control_state 흉내(슬라이스 18): CREATE 경로와 _ensure_columns ALTER
    # 경로가 같은 스키마로 수렴해야 한다 -- 한쪽만 넣으면 기배포 DB 에서만 컬럼이
    # 없다(슬라이스 14 실 500 교훈, migrations.py 의 _ensure_columns 주석).
    db.execute("DROP TABLE control_state")
    db.execute("""CREATE TABLE control_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        maintenance INTEGER NOT NULL DEFAULT 0,
        drain INTEGER NOT NULL DEFAULT 0,
        reason TEXT, build_node_name TEXT, changed_by TEXT, changed_at TEXT)""")
    from dms.migrations import _column_exists, migrate
    migrate(db)
    for column in ("artifact_base_uri", "artifact_base_check_uri",
                   "artifact_base_check_ok", "artifact_base_check_reason",
                   "artifact_base_check_at"):
        assert _column_exists(db, "control_state", column), column
    # 싱글톤 시드가 살아 있고 새 컬럼은 NULL(미설정 = env 사용, 하위호환)이다
    row = db.query_one("SELECT * FROM control_state WHERE id = 1")
    assert row["artifact_base_uri"] is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_control.py tests/test_migrations.py -q`
Expected: FAIL — repo 테스트 3건이 `AttributeError: 'ControlRepository' object has no attribute 'set_artifact_base'`(check 테스트는 `set_artifact_base_check`), migrations 테스트가 `AssertionError: artifact_base_uri`(`_column_exists` False). 기존 테스트는 PASS 유지.

- [ ] **Step 3: migrations.py를 고친다**

**(1)** control_state CREATE 블록(`migrations.py:283-290`)을 다음으로 교체:

```python
        """CREATE TABLE IF NOT EXISTS control_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            maintenance INTEGER NOT NULL DEFAULT 0,
            drain INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            build_node_name TEXT,
            -- 아티팩트 base(슬라이스 18 설계 §2.1). NULL = 미설정 -> env
            -- (DMS_ARTIFACT_BASE_URI) 사용 -- 기존 배포는 동작이 바뀌지 않는다
            -- (시드 불필요, 하위호환). ConfigMap 에 두지 않는 근거는
            -- 20-config.yaml:87-88 이 build_node_name 으로 이미 명문화한 그것:
            -- 운영자가 포탈에서 바꾸는 값을 ConfigMap 에 두면 재적용마다 되돌아간다.
            artifact_base_uri TEXT,
            -- 컨트롤러 관점 검증(설계 §2.4c): 컨트롤러는 read_summary 로 실제
            -- 읽기를 하는 유일한 프로세스라, 마운트가 없으면 "SUCCEEDED 인데
            -- 요약이 없는" 조용한 실패가 난다(stepper.py summary_unavailable) --
            -- 그 실패를 사전에 화면에 보이게 컨트롤러가 주기 기록을 남긴다.
            -- check_uri 를 함께 남기는 이유: 값 변경 직후 "옛 base 의 결과"를
            -- "새 base 실패"로 오독하지 않도록, 화면이 check_uri != effective 를
            -- "확인 대기 중"으로 구분한다(설계 §4: 모름과 실패를 뭉개지 않는다).
            artifact_base_check_uri TEXT,
            artifact_base_check_ok INTEGER,
            artifact_base_check_reason TEXT,
            artifact_base_check_at TEXT,
            changed_by TEXT,
            changed_at TEXT)""",
```

**(2)** `_ensure_columns` 튜플의 `("control_state", "build_node_name", "TEXT"),`(`migrations.py:419`) **바로 아래**에 추가:

```python
        # 슬라이스 18 아티팩트 base -- 기배포 DB 는 CREATE 를 다시 안 탄다(위
        # submit_wait_seconds 와 같은 이유: 양쪽에 넣지 않으면 라이브에서만 없다).
        ("control_state", "artifact_base_uri", "TEXT"),
        ("control_state", "artifact_base_check_uri", "TEXT"),
        ("control_state", "artifact_base_check_ok", "INTEGER"),
        ("control_state", "artifact_base_check_reason", "TEXT"),
        ("control_state", "artifact_base_check_at", "TEXT"),
```

- [ ] **Step 4: control.py에 전용 메서드 2개를 추가한다**

`src/dms/repositories/control.py` — `set_control_state` 메서드(`:99-111`) **바로 아래**에 추가:

```python
    def set_artifact_base(self, uri, *, actor, forced=False, affected_jobs=0):
        """아티팩트 base 전용 UPDATE(슬라이스 18 설계 §2.1). set_control_state 에
        얹지 않는다: 그 UPDATE 는 build_node_name = :bn 을 **무조건** 쓰므로 인자를
        생략한 호출이 기존 값을 조용히 NULL 로 지운다(현재는 라우트가 항상 넘겨
        잠복해 있을 뿐 -- routes_control.py). 같은 자리에 컬럼을 하나 더 얹으면 그
        함정이 그대로 복제된다 -- 이 컬럼 하나만 만지는 UPDATE 로 분리하면
        구조적으로 불가능해진다. changed_by/changed_at 도 만지지 않는다: 그 둘은
        유지보수/드레인 변경 표시라, base 변경이 덮으면 컨트롤 상태 화면의 변경
        이력이 오염된다(변경자는 감사 로그가 나른다)."""
        before = self.control_state()
        with self._db.transaction():
            self._db.execute(
                "UPDATE control_state SET artifact_base_uri = :uri WHERE id = 1",
                {"uri": uri})
            # force 통과는 반드시 감사에 남는다(설계 §2.3): affected_jobs 건의
            # 아티팩트·로그 열람이 깨진다는 사실이 추적 가능해야 한다.
            self._audit("artifact_base", "set", "artifact_base", before,
                        {"artifact_base_uri": uri, "forced": forced,
                         "affected_jobs": affected_jobs}, actor)

    def set_artifact_base_check(self, *, uri, ok, reason, now_iso=None):
        """컨트롤러 관점 검증 결과(설계 §2.4c) 전용 UPDATE. 주기 기록이라 감사를
        남기지 않고(운영자 변경이 아니다), 검증 4컬럼 밖은 만지지 않는다
        (set_artifact_base 와 같은 분리 원칙)."""
        self._db.execute(
            """UPDATE control_state SET artifact_base_check_uri = :uri,
                   artifact_base_check_ok = :ok, artifact_base_check_reason = :r,
                   artifact_base_check_at = :at WHERE id = 1""",
            {"uri": uri, "ok": 1 if ok else 0, "r": reason,
             "at": now_iso or utc_now_iso()})
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_control.py tests/test_migrations.py tests/test_api_control.py -q`
Expected: 전부 PASS (`test_api_control.py`는 set_control_state 경로 회귀 확인용 — 무변경이어야 한다)

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add src/dms/migrations.py src/dms/repositories/control.py tests/test_repo_control.py tests/test_migrations.py
git commit -m "feat(db): control_state 에 아티팩트 base + 컨트롤러 검증 컬럼 — 전용 UPDATE 분리"
```

---

### Task 2: artifact_base 모듈 — resolve 단일화 + 소비자 4곳 재배선

**Files:**
- Create: `src/dms/artifact_base.py`
- Create: `tests/test_artifact_base_resolve.py`
- Modify: `src/dms/api/artifacts.py`(strip_scheme 재수출), `src/dms/stepper.py`, `src/dms/execution_volcano.py`(생성자+`_reconstruct_summary_path`), `src/dms/wiring.py`, `src/dms/api/routes_artifacts.py`, `src/dms/api/routes_scan_paths.py`

**Interfaces:**
- Consumes: Task 1의 `set_artifact_base`, `ControlRepository.control_state()`(`control.py:96-97`), `Settings.artifact_base_uri`.
- Produces (Task 3~7이 이 이름을 그대로 쓴다):
  - `artifact_base.strip_scheme(base_uri: str) -> str` — 접두사만 제거(기존 `api/artifacts.py:46-47` 승격, 재수출 유지).
  - `artifact_base.resolve_artifact_base(control_repo, settings) -> str` — DB 값 있으면 그것, 없으면 `settings.artifact_base_uri`.
  - `VolcanoExecutionAdapter(..., artifact_base=<str | 0-인자 callable>)` — 내부 `self._artifact_base_fn`으로 정규화, `_reconstruct_summary_path`가 호출 시점 해석.
  - `JobStepper._artifact_base()` — 스텝 내부 base 3사용처의 단일 통로.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_artifact_base_resolve.py` (신규 파일 전체):

```python
"""resolve_artifact_base(슬라이스 18 설계 §2.1)와 소비자 4곳 재배선의 계약.

핵심: DB(control_state.artifact_base_uri)가 env 를 이기고, NULL 이면 env 로
떨어진다(하위호환 -- 기존 배포 무변화). 소비자가 설정 스냅숏을 캡처해 두면 base
변경이 재시작 전까지 반영되지 않는다(설계 §1-7) -- 어댑터에는 가변 callable 을
주입해 호출 시점 해석을, stepper 에는 DB 값 주입으로 스냅숏 부재를 단언한다."""
import json

from dms.artifact_base import resolve_artifact_base, strip_scheme
from dms.domain import DataJobState
from dms.execution import StubExecutionAdapter
from dms.execution_volcano import VolcanoExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper

ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///env/base"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def test_resolve_falls_back_to_env_when_db_null(db):
    repos = Repositories(db)
    assert resolve_artifact_base(repos.control, _Settings()) == "file:///env/base"


def test_resolve_prefers_db_value(db):
    repos = Repositories(db)
    repos.control.set_artifact_base("file:///db/base", actor="ops")
    assert resolve_artifact_base(repos.control, _Settings()) == "file:///db/base"


def test_strip_scheme_strips_prefix_only():
    # 전체 치환(replace) 계열과의 차이가 이 함수의 존재 이유다(설계 §2.2):
    # 접두가 아닌 위치의 file:// 는 경로의 일부로 보존돼야 한다.
    assert strip_scheme("file:///a/b") == "/a/b"
    assert strip_scheme("/a/b") == "/a/b"
    assert strip_scheme("file:///a/file://b") == "/a/file://b"


# ---- 소비자 ① stepper (3사용처: _build_spec / 성공 execution / 성공 preview) ----

def _scan_job(repos):
    from dms.domain import RequestState
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    return repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={"identity": {}, "candidates": {"primary": ["n1"]},
                     "process_count": 1, "queue": "dms-data",
                     "priority_class": "dms-mid"},
        precondition={}, actor="planner")


def test_stepper_builds_specs_with_db_base(db):
    # 소비자 ①(stepper._build_spec): 매 틱 재조회 -- 정책 재조회(stepper.py:67-69)
    # 와 같은 패턴. env 스냅숏이 남아 있으면 file:///env/base 로 깨진다.
    repos = Repositories(db)
    repos.control.set_artifact_base("file:///db/base", actor="ops")
    _scan_job(repos)
    adapter = StubExecutionAdapter()
    JobStepper(repos, adapter, settings=_Settings()).run_once()
    assert adapter.submitted_specs()[0].artifact_base == "file:///db/base"


def test_stepper_records_artifact_uri_under_db_base(db):
    # 소비자 ①-보강: artifact_uri 기록(stepper.py 성공 execution 경로)이 env 로
    # 남으면 포탈이 옛 경로를 가리킨다. StubExecutionAdapter 는 poll 기본
    # Succeeded 라 3틱이면 Pending -> Preflight -> Running -> Succeeded 다.
    repos = Repositories(db)
    repos.control.set_artifact_base("file:///db/base", actor="ops")
    jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-execution-{jid}", {"files": 1})
    stepper = JobStepper(repos, adapter, settings=_Settings())
    stepper.run_once()   # Pending -> Preflight
    stepper.run_once()   # Preflight -> Running
    stepper.run_once()   # Running -> Succeeded
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded"
    assert job["artifact_uri"] == f"file:///db/base/{jid}"


# ---- 소비자 ② VolcanoExecutionAdapter: 호출 시점 해석 ----

def test_adapter_reconstructs_summary_path_at_call_time():
    # 생성자 캡처가 남아 있으면 base 변경 후 컨트롤러 재시작 시 in-flight 잡의
    # summary 를 옛 경로에서 찾는다(설계 §1-7) -- callable 주입으로 호출 시점
    # 해석을 고정한다.
    class _K8s:
        def get(self, kind, name, namespace):
            return {"metadata": {"labels": {"dms.io/job-id": "a" * 32,
                                            "dms.io/phase": "execution"}}}
    current = {"base": "file:///old"}
    read_paths = []
    adapter = VolcanoExecutionAdapter(
        _K8s(), job_image="img", namespace="dms", storages_lookup=lambda n: None,
        read_text=lambda p: read_paths.append(p) or None,
        artifact_base=lambda: current["base"])
    adapter.read_summary("vcjob/dms-scan-execution-x")
    current["base"] = "file:///new"
    adapter.read_summary("vcjob/dms-scan-execution-x")
    assert read_paths == [f"/old/{'a' * 32}/execution/summary.json",
                          f"/new/{'a' * 32}/execution/summary.json"]


def test_adapter_still_accepts_a_fixed_string_base():
    # 기존 테스트 10여 곳과 스텁 조립 경로 호환: 문자열이면 고정 base 로 동작한다.
    adapter = VolcanoExecutionAdapter(
        object(), job_image="img", namespace="dms", storages_lookup=lambda n: None,
        read_text=lambda p: None, artifact_base="file:///fixed")
    assert adapter._artifact_base_fn() == "file:///fixed"


# ---- 소비자 ③④ 읽기 라우트 2곳 ----

def test_api_base_helper_resolves_from_db(db):
    from types import SimpleNamespace
    from dms.api.routes_artifacts import _base
    repos = Repositories(db)
    repos.control.set_artifact_base("file:///db/base", actor="ops")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repos=repos, settings=_Settings())))
    assert _base(request) == "/db/base"


def test_scan_path_stats_reads_under_db_base(client, db, tmp_path):
    # 소비자 ④(routes_scan_paths): DB base 아래 실제 리포트를 두고 통계가 그것을
    # 읽는지. 재배선이 빠지면 env(file:///artifacts/dms)를 보고 404
    # no_covering_scan 으로 조용히 퇴행한다(설계 §1-5).
    repos = Repositories(db)
    admin = ADMIN
    client.post("/api/admin/storages", json={
        "storage_name": "ceph-a", "mount_path": "/mnt/ceph",
        "managed_root": "/mnt/ceph/dms", "backend_type": "cephfs"}, headers=admin)
    client.post("/api/auth/signup", json={"username": "alice", "password": "p"})
    client.post("/api/auth/login", json={"username": "alice", "password": "p"})
    path_id = client.post("/api/user/scan-paths",
                          json={"storage_name": "ceph-a", "path": "team"}).json()["id"]
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="rk", payload={"storage": "ceph-a", "target": "team"},
        priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="ceph-a", target="team", options={}, tool="dscan",
        worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    d = tmp_path / jid / "execution"
    d.mkdir(parents=True)
    (d / "dscan-report.json").write_text(json.dumps({
        "generated_at_epoch": 1, "summary": {"total_entries": 1},
        "file_size_histogram": [], "time_histograms": {}}))
    repos.control.set_artifact_base(f"file://{tmp_path}", actor="ops")
    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 200
    assert r.json()["summary"] == {"total_entries": 1}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_artifact_base_resolve.py -q`
Expected: FAIL — 수집 단계에서 `ModuleNotFoundError: No module named 'dms.artifact_base'`

- [ ] **Step 3: artifact_base.py를 만든다**

`src/dms/artifact_base.py` (전체):

```python
"""아티팩트 base 의 단일 진실 원천(슬라이스 18).

- strip_scheme: file:// **접두사만** 벗긴다. str.replace("file://", "") 전체 치환
  계열은 경로 중간의 file:// 까지 지워 두 계열이 같은 문자열에서 다른 경로를
  만든다(설계 §2.2) -- 저장소 전체가 이 함수 하나로 통일된다.
- resolve_artifact_base: DB(control_state.artifact_base_uri)가 있으면 그것, 없으면
  env(settings.artifact_base_uri). 모든 소비자가 이 함수만 통과한다(설계 §2.1).
"""


def strip_scheme(base_uri: str) -> str:
    # api/artifacts.py 에 있던 것을 그대로 승격 -- 실행 계열(execution_*.py)이
    # FastAPI 계층(api/)을 임포트하지 않도록 중립 모듈로 옮겼다. api/artifacts.py
    # 가 재수출하므로 기존 임포트 경로는 그대로 산다.
    return base_uri[len("file://"):] if base_uri.startswith("file://") else base_uri


def resolve_artifact_base(control_repo, settings) -> str:
    """DB 값 우선, NULL 이면 env(하위호환 -- 기존 배포 무변화). Settings 는 frozen
    dataclass 라 런타임 재읽기 경로가 없고(config.py:94), 컨트롤러 루프와
    app.state 가 같은 인스턴스를 캡처한다 -- 재시작 없이 반영되려면 DB 를 매번
    조회해야 한다. 비용은 스테퍼가 이미 매 틱 정책을 DB 재조회하는 것
    (stepper.py:67-69)과 같은 규모라 논쟁이 없다."""
    row = control_repo.control_state()
    if row and row.get("artifact_base_uri"):
        return row["artifact_base_uri"]
    return settings.artifact_base_uri
```

- [ ] **Step 4: api/artifacts.py에서 strip_scheme을 재수출로 바꾼다**

`src/dms/api/artifacts.py` — import 블록(`import os / import re / import stat`) 아래에 추가:

```python
# strip_scheme 은 중립 모듈(artifact_base.py)로 승격했다(슬라이스 18 설계 §2.2) --
# 실행 계열(execution_*.py)이 FastAPI 계층(api/)을 임포트하지 않게 하기 위해서다.
# 기존 임포트 경로(from .artifacts import strip_scheme)를 위해 여기서 재수출한다.
from ..artifact_base import strip_scheme  # noqa: F401
```

그리고 기존 정의(`:46-47`)를 **삭제**:

```python
def strip_scheme(base_uri: str) -> str:
    return base_uri[len("file://"):] if base_uri.startswith("file://") else base_uri
```

- [ ] **Step 5: stepper를 재배선한다**

`src/dms/stepper.py` — **(1)** import에 한 줄 추가(`from .db import iso_plus, utc_now_iso` 위):

```python
from .artifact_base import resolve_artifact_base
```

**(2)** `_abs` 메서드(`:45-49`) 아래에 추가:

```python
    def _artifact_base(self):
        # 슬라이스 18: DB 가 env 를 이긴다(설계 §2.1). JobStepper 는 매 틱
        # 재생성되고(controller.py:34-35) 정책도 매 틱 DB 재조회라 이 조회가 새
        # 비용을 만들지 않는다. 스냅숏을 들고 있으면 base 변경이 컨트롤러 재시작
        # 전까지 반영되지 않는다(설계 §1-7).
        return resolve_artifact_base(self._repos.control, self._settings)
```

**(3)** `_build_spec`의 `artifact_base=self._settings.artifact_base_uri, timeout_seconds=timeout,`(`:82`)을 다음으로 교체:

```python
            artifact_base=self._artifact_base(), timeout_seconds=timeout,
```

**(4)** `_poll_execution`의 set_artifact 호출(`:193-196`)을 다음으로 교체:

```python
            self._repos.data_jobs.set_artifact(
                job["job_id"],
                artifact_uri=f"{self._artifact_base()}/{job['job_id']}",
                result_summary=summary)
```

**(5)** `_poll_preview`의 `artifact = f"{self._settings.artifact_base_uri}/{jid}"`(`:233`)를 다음으로 교체:

```python
            artifact = f"{self._artifact_base()}/{jid}"
```

- [ ] **Step 6: 어댑터를 호출 시점 해석으로 바꾼다**

`src/dms/execution_volcano.py` — **(1)** import에 한 줄 추가(`from .execution import ...` 위):

```python
from .artifact_base import strip_scheme
```

**(2)** 생성자(`:70-78`)를 다음으로 교체:

```python
    def __init__(self, k8s, *, job_image, namespace, storages_lookup, read_text,
                 artifact_base):
        self._k8s = k8s
        self._job_image = job_image
        self._namespace = namespace
        self._storages = storages_lookup
        self._read_text = read_text
        # summary 경로 fallback 재구성용. str(고정값) 또는 0-인자 callable(호출
        # 시점 해석) 둘 다 받는다: 설계 §2.1 이 생성자 캡처를 금지하는 이유는
        # §1-7 -- base 변경 후 컨트롤러가 재시작하면 in-flight 잡의 summary 를 옛
        # 경로에서 찾게 된다. wiring 은 resolve 클로저를 넘기고, 고정 문자열을
        # 넘기는 기존 테스트·스텁 조립은 람다로 감싸 그대로 산다.
        self._artifact_base_fn = (artifact_base if callable(artifact_base)
                                  else (lambda: artifact_base))
        self._summary_paths = {}   # ref -> artifact summary.json path (in-memory 빠른 경로)
```

**(3)** `_reconstruct_summary_path`의 마지막 return(`:222-223`)을 다음으로 교체:

```python
        # 호출 시점 해석(설계 §2.1) + 접두사 전용 스킴 제거(설계 §2.2).
        return f"{strip_scheme(self._artifact_base_fn())}/{job_id}/{phase}/summary.json"
```

- [ ] **Step 7: wiring과 읽기 라우트 2곳을 재배선한다**

**(1)** `src/dms/wiring.py` — import에 한 줄 추가(`from .execution import StubExecutionAdapter` 위):

```python
from .artifact_base import resolve_artifact_base
```

`build_execution_adapter`의 return(`:22-26`)을 다음으로 교체:

```python
    return VolcanoExecutionAdapter(
        KubernetesClient(settings.k8s_namespace),
        job_image=settings.job_image, namespace=settings.k8s_namespace,
        storages_lookup=lambda n: repos.storages.get(n), read_text=read_text,
        # 생성자 캡처 금지(설계 §2.1/§1-7): base 변경 후 컨트롤러가 재시작해도
        # 호출 시점의 DB 값으로 summary 경로를 재구성한다.
        artifact_base=lambda: resolve_artifact_base(repos.control, settings))
```

**(2)** `src/dms/api/routes_artifacts.py` — import에 한 줄 추가(`from ..execution import ExecutionError` 위):

```python
from ..artifact_base import resolve_artifact_base
```

`_base`(`:11-12`)를 다음으로 교체:

```python
def _base(request: Request) -> str:
    # 슬라이스 18: 설정 스냅숏이 아니라 DB 우선 해석(설계 §2.1). 읽기 라우트는 잡
    # 행이 아니라 현재 base 로 경로를 조립한다(설계 §1-5) -- base 는 잠금(§2.3)
    # 탓에 잡이 존재하는 한 바뀌지 않으므로 이 조립은 여전히 안전하다.
    return strip_scheme(resolve_artifact_base(request.app.state.repos.control,
                                              request.app.state.settings))
```

**(3)** `src/dms/api/routes_scan_paths.py` — import에 한 줄 추가(`from ..domain import ...` 위):

```python
from ..artifact_base import resolve_artifact_base
```

`base = strip_scheme(request.app.state.settings.artifact_base_uri)`(`:126`)를 다음으로 교체:

```python
    # 슬라이스 18: DB 우선 해석(설계 §2.1) -- routes_artifacts._base 와 같은 이유.
    base = strip_scheme(resolve_artifact_base(repos.control,
                                              request.app.state.settings))
```

- [ ] **Step 8: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_artifact_base_resolve.py tests/test_stepper_scan.py tests/test_stepper_sync.py tests/test_stepper_artifact_uri.py tests/test_execution_volcano.py tests/test_api_artifacts.py tests/test_api_scan_path_stats.py tests/test_wiring_phase3c.py tests/test_ops_hardening_small.py -q`
Expected: 전부 PASS (기존 stepper/adapter 테스트는 DB 값이 NULL이라 env 폴백으로 동작 동일, 고정 문자열 주입은 람다 래핑으로 동작 동일)

- [ ] **Step 9: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add src/dms/artifact_base.py src/dms/api/artifacts.py src/dms/stepper.py src/dms/execution_volcano.py src/dms/wiring.py src/dms/api/routes_artifacts.py src/dms/api/routes_scan_paths.py tests/test_artifact_base_resolve.py
git commit -m "feat(artifact-base): resolve 단일화 — DB 우선 해석으로 소비자 4곳 재배선"
```

---

### Task 3: 스킴 제거를 strip_scheme(접두사 전용) 한 계열로 통일

**Files:**
- Modify: `src/dms/execution_volcano.py`(`:100`, `:153-155`), `src/dms/execution_manifests.py`(`:172-175` + import)
- Create: `tests/test_strip_scheme_unification.py`

**Interfaces:**
- Consumes: Task 2의 `artifact_base.strip_scheme`.
- Produces: `execution_volcano.py`·`execution_manifests.py`에 `replace("file://"` 문자열 0건 — grep 수준 테스트가 고정한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_strip_scheme_unification.py` (신규 파일 전체):

```python
"""스킴 제거 통일(슬라이스 18 설계 §2.2). 전체 치환(str.replace("file://", ""))은
경로 중간의 file:// 까지 지워, 접두사만 벗기는 strip_scheme 계열과 **다른 경로**를
만든다 -- env 가 신뢰 입력이던 동안 잠복했지만 자유 입력을 받기 시작하면 실제
갈라짐이다(저장 시점 정규화가 그런 입력을 거부하더라도, 같은 문자열을 두 방식으로
해석하는 코드를 남겨 두지 않는다). (1) 네 지점의 동작을 접두사-전용으로 행동
수준에서, (2) 전체 치환 코드가 소스에서 사라졌음을 grep 수준으로 고정한다."""
from pathlib import Path

from dms.execution import JobSpec
from dms.execution_manifests import build_volcano_job
from dms.execution_volcano import VolcanoExecutionAdapter

SRC = Path(__file__).resolve().parent.parent / "src" / "dms"

# 경로 중간에 file:// 가 든 base. 접두사만 벗기면 /data/file://x 이고, 전체
# 치환이 남아 있으면 /data/x 가 된다 -- 두 계열이 갈라지는 최소 재현이다.
TRICKY = "file:///data/file://x"


def _spec(phase="execution"):
    return JobSpec(job_id="a" * 32, phase=phase, operation="scan", tool="dscan",
                   dryrun=False, identity={},
                   paths={"target": "/mnt/s/t", "storage": "s1"},
                   options={}, candidates={"primary": ["n1"]}, process_count=1,
                   queue="dms-data", priority_class="dms-mid", artifact_base=TRICKY)


def _adapter(k8s=None):
    return VolcanoExecutionAdapter(
        k8s if k8s is not None else object(), job_image="img", namespace="dms",
        storages_lookup=lambda n: {"mount_path": "/mnt/s"},
        read_text=lambda p: None, artifact_base=TRICKY)


def test_volumes_mount_keeps_mid_path_scheme():
    # execution_volcano._volumes: 전체 치환이면 /data/x 를 마운트해 -- 잡 파드가
    # 실제 base(/data/file://x)와 **다른 디렉터리**를 hostPath 로 받는다.
    paths = [v["hostPath"]["path"] for v in _adapter()._volumes(_spec())]
    assert "/data/file://x" in paths
    assert "/data/x" not in paths


def test_launcher_artifact_dir_keeps_mid_path_scheme():
    # execution_manifests._artifact_dir: 러너가 summary.json 을 쓰는 위치다 --
    # 마운트 계산(_volumes)과 다른 계열로 해석되면 쓰는 곳과 읽는 곳이 갈라진다.
    manifest = build_volcano_job(_spec(), job_image="img", namespace="dms",
                                 volumes=[])
    launcher = manifest["spec"]["tasks"][0]
    env = {e["name"]: e["value"]
           for e in launcher["template"]["spec"]["containers"][0]["env"]}
    assert env["DMS_JR_ARTIFACT_DIR"] == f"/data/file://x/{'a' * 32}/execution"


def test_submit_summary_path_keeps_mid_path_scheme():
    class _K8s:
        def create(self, manifest):
            pass
    a = _adapter(_K8s())
    ref = a.submit(_spec())
    assert a._summary_paths[ref] == f"/data/file://x/{'a' * 32}/execution/summary.json"


def test_reconstruct_summary_path_keeps_mid_path_scheme():
    class _K8s:
        def get(self, kind, name, namespace):
            return {"metadata": {"labels": {"dms.io/job-id": "a" * 32,
                                            "dms.io/phase": "preview"}}}
    a = _adapter(_K8s())
    assert a._reconstruct_summary_path("vcjob/x") == (
        f"/data/file://x/{'a' * 32}/preview/summary.json")


def test_full_replace_scheme_stripping_is_gone():
    # 설계 §5: 전체 치환 4곳(execution_volcano.py:100,155,223 /
    # execution_manifests.py:174)이 제거됐는지 grep 수준으로 고정한다 -- 새로
    # 생기는 것도 여기서 잡힌다.
    for name in ("execution_volcano.py", "execution_manifests.py"):
        source = (SRC / name).read_text()
        assert 'replace("file://"' not in source, name
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_strip_scheme_unification.py -q`
Expected: FAIL 4건 / PASS 2건 — `test_volumes_...`가 `assert '/data/file://x' in ['/mnt/s', '/data/x']`, `test_launcher_...`·`test_submit_...`이 `/data/x/...` != `/data/file://x/...`, grep 테스트가 `AssertionError: execution_volcano.py`. `test_reconstruct_...`는 Task 2가 이미 고쳐 PASS(그 지점의 회귀 가드로 남긴다).

- [ ] **Step 3: 남은 전체 치환 3곳을 고친다**

**(1)** `src/dms/execution_volcano.py` — `_volumes`의 `:99-100`을 다음으로 교체:

```python
        # summary.json 기록 위치 — 스킴 제거한 artifact base 도 반드시 마운트.
        # 접두사만 벗긴다(설계 §2.2): 전체 치환은 경로 중간의 file:// 까지 지워,
        # 읽기 계열(strip_scheme)과 다른 디렉터리를 마운트하게 된다.
        mount_paths.append(strip_scheme(spec.artifact_base))
```

**(2)** 같은 파일 — `submit`의 `:153-155`를 다음으로 교체:

```python
        base = strip_scheme(spec.artifact_base)  # 접두사 전용(설계 §2.2)
        self._summary_paths[ref] = f"{base}/{spec.job_id}/{spec.phase}/summary.json"
```

**(3)** `src/dms/execution_manifests.py` — 파일 머리 `import json` 아래에 추가:

```python
from .artifact_base import strip_scheme
```

`_artifact_dir`(`:172-175`)를 다음으로 교체:

```python
def _artifact_dir(spec):
    # artifact_base는 URI(file:///cephfs/...) — 파드 안 파일 연산용으로 스킴 제거.
    # 접두사만 벗긴다(설계 §2.2): 전체 치환(replace)은 경로 중간의 file:// 까지
    # 지워, 러너가 쓰는 위치가 마운트 계산·읽기 라우트와 갈라질 수 있다.
    return f"{strip_scheme(spec.artifact_base)}/{spec.job_id}/{spec.phase}"
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_strip_scheme_unification.py tests/test_execution_volcano.py tests/test_execution_manifests.py tests/test_vcjob_ttl.py tests/test_timeout_enforcement.py -q`
Expected: 전부 PASS (기존 테스트의 base는 전부 `file:///` 접두뿐이라 접두사 제거와 전체 치환의 결과가 같다)

- [ ] **Step 5: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add src/dms/execution_volcano.py src/dms/execution_manifests.py tests/test_strip_scheme_unification.py
git commit -m "refactor(artifact-base): 스킴 제거를 strip_scheme(접두사 전용)으로 통일 — 전체 치환 4곳 제거"
```

---

### Task 4: 정규화 + 실파일 왕복 즉석 검증 (+ 사유 코드 등록)

**Files:**
- Modify: `src/dms/artifact_base.py`
- Modify: `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts`
- Create: `tests/test_artifact_base_validation.py`

**Interfaces:**
- Consumes: `DomainValidationError(reason_code, detail)`(`domain.py:68-72`).
- Produces (Task 5·7이 그대로 쓴다):
  - `normalize_artifact_base(raw: str) -> str` — 정규형 `file:///<절대경로>` 반환. 실패 시 `DomainValidationError`: `artifact_base_not_absolute` / `artifact_base_traversal` / `artifact_base_scheme_in_path`.
  - `roundtrip_artifact_base(path: str) -> str | None` — 성공 None, 실패 `artifact_base_missing` / `artifact_base_not_directory` / `artifact_base_not_writable`.
  - reasonCodes.json에 위 6종 + `artifact_base_locked`(Task 5의 409 detail 선등록 — 백엔드 추출 테스트는 src⊆json만 검사하므로 선등록이 안전하다).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_artifact_base_validation.py` (신규 파일 전체):

```python
"""정규화(설계 §2.2)·즉석 검증(설계 §2.4a)의 계약.

정규화는 저장 시점 한 곳에서만 한다 -- 소비자 4곳이 방어 코드를 복제하지 않도록.
즉석 검증은 존재·디렉터리 확인에 그치지 않고 임시 파일 생성→쓰기→읽기→삭제를
**실제로** 한다. 쓰기 불가는 chmod 로 재현하는데 root 는 chmod 강등을 무시하므로
그 경우 해당 테스트를 스킵한다(거짓 초록보다 정직한 스킵이 낫다)."""
import os

import pytest

from dms.artifact_base import normalize_artifact_base, roundtrip_artifact_base
from dms.domain import DomainValidationError


def _reason(fn, *args):
    with pytest.raises(DomainValidationError) as exc:
        fn(*args)
    return exc.value.reason_code


def test_normalize_keeps_canonical_form_and_strips_trailing_slash():
    assert (normalize_artifact_base("file:///cephfs/dms/artifacts")
            == "file:///cephfs/dms/artifacts")
    assert normalize_artifact_base("/cephfs/dms/artifacts/") == "file:///cephfs/dms/artifacts"
    assert normalize_artifact_base("file:///a/b///") == "file:///a/b"


def test_normalize_rejects_relative_empty_and_root():
    assert _reason(normalize_artifact_base, "cephfs/x") == "artifact_base_not_absolute"
    assert _reason(normalize_artifact_base, "") == "artifact_base_not_absolute"
    assert _reason(normalize_artifact_base, "file://") == "artifact_base_not_absolute"
    # 루트("/") 거부: 루트를 아티팩트 트리로 쓰는 구성은 오타다.
    assert _reason(normalize_artifact_base, "/") == "artifact_base_not_absolute"


def test_normalize_rejects_traversal_segments():
    assert _reason(normalize_artifact_base, "/a/../b") == "artifact_base_traversal"
    assert _reason(normalize_artifact_base, "file:///a/..") == "artifact_base_traversal"


def test_normalize_rejects_mid_path_scheme():
    # 경로 중간 file:// 는 strip_scheme(접두사만)과 전체 치환(replace)이 다른
    # 경로를 만드는 바로 그 입력이다(설계 §2.2) -- 저장 시점에 거부해 해석기
    # 계열 차이가 실제 데이터로 드러날 일 자체를 없앤다.
    assert _reason(normalize_artifact_base, "/data/file://x") == "artifact_base_scheme_in_path"
    assert (_reason(normalize_artifact_base, "file:///data/file://x")
            == "artifact_base_scheme_in_path")


def test_roundtrip_ok_on_writable_dir(tmp_path):
    assert roundtrip_artifact_base(str(tmp_path)) is None
    assert list(tmp_path.iterdir()) == []   # probe 파일을 지웠다(왕복의 '삭제')


def test_roundtrip_missing_and_not_directory(tmp_path):
    assert roundtrip_artifact_base(str(tmp_path / "nope")) == "artifact_base_missing"
    f = tmp_path / "plain"
    f.write_text("x")
    assert roundtrip_artifact_base(str(f)) == "artifact_base_not_directory"


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root 는 chmod 권한 강등을 무시해 쓰기 불가를 재현할 수 없다")
def test_roundtrip_not_writable(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert roundtrip_artifact_base(str(locked)) == "artifact_base_not_writable"
    finally:
        locked.chmod(0o700)   # tmp_path 정리가 실패하지 않도록 복원
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_artifact_base_validation.py -q`
Expected: FAIL — 수집 단계에서 `ImportError: cannot import name 'normalize_artifact_base' from 'dms.artifact_base'`

- [ ] **Step 3: artifact_base.py에 두 함수를 추가한다**

`src/dms/artifact_base.py` — 파일 머리 docstring 아래에 import 추가:

```python
import os
import uuid

from .domain import DomainValidationError
```

파일 끝(`resolve_artifact_base` 아래)에 추가:

```python
def normalize_artifact_base(raw: str) -> str:
    """PUT/validate 입력을 정규형 file:///<절대경로> 로 정규화한다(설계 §2.2).
    저장 시점 한 곳에서만 한다 -- 소비자 4곳이 방어 코드를 복제하지 않도록.
    실패는 DomainValidationError(reason_code) -- 라우트가 422 로 나른다."""
    value = (raw or "").strip()
    if value.startswith("file://"):
        value = value[len("file://"):]
    if "file://" in value:
        # 경로 중간 file:// 금지: strip_scheme(접두사만)과 전체 치환(replace)이
        # **다른 경로**를 만드는 바로 그 입력이다(설계 §2.2). Task 3 이 해석기를
        # 한 계열로 통일했지만, 그런 값이 저장되는 일 자체를 여기서 없앤다.
        raise DomainValidationError("artifact_base_scheme_in_path", raw)
    while len(value) > 1 and value.endswith("/"):
        value = value[:-1]   # 후행 슬래시 제거 -- f"{base}/{job_id}" 조립 정합성
    if not value.startswith("/") or value == "/":
        # 상대경로·빈 값 거부 + 루트("/") 거부: 루트를 아티팩트 트리로 쓰는
        # 구성은 오타이고, 후행 슬래시 제거와 조합하면 "//" 경로를 만든다.
        raise DomainValidationError("artifact_base_not_absolute", raw)
    if ".." in value.split("/"):
        raise DomainValidationError("artifact_base_traversal", raw)
    return f"file://{value}"


def roundtrip_artifact_base(path: str) -> "str | None":
    """즉석 검증(설계 §2.4a): 존재·디렉터리 확인에 그치지 않고 임시 파일
    생성→쓰기→읽기→삭제를 **실제로** 한다. hostPath type: Directory 는 존재만
    요구하지만(설계 §1-4), 이 프로세스 관점의 쓰기 왕복이 안 되면 같은 마운트를
    쓰는 아티팩트 읽기·요약 읽기도 죽는다. 성공 None, 실패 reason_code."""
    if not os.path.exists(path):
        return "artifact_base_missing"
    if not os.path.isdir(path):
        return "artifact_base_not_directory"
    # 고유 이름: 동시 검증(포탈 폴링 + 컨트롤러 루프)이 서로의 probe 를 지우지
    # 않도록 한다.
    probe = os.path.join(path, f".dms-base-check-{uuid.uuid4().hex}")
    try:
        with open(probe, "w") as f:
            f.write("dms")
        with open(probe) as f:
            if f.read() != "dms":
                return "artifact_base_not_writable"
    except OSError:
        return "artifact_base_not_writable"
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass  # 생성 자체가 실패했으면 지울 것이 없다 -- 판정에 무관
    return None
```

- [ ] **Step 4: 사유 코드를 등록한다 (백엔드·프론트 계약 동시)**

**(1)** `frontend/src/lib/reasonCodes.json` — 마지막 줄 블록

```json
  "account_not_found", "invalid_role", "cannot_lock_self"
]
```

을 다음으로 교체:

```json
  "account_not_found", "invalid_role", "cannot_lock_self",
  "artifact_base_not_absolute", "artifact_base_traversal",
  "artifact_base_scheme_in_path", "artifact_base_missing",
  "artifact_base_not_directory", "artifact_base_not_writable",
  "artifact_base_locked"
]
```

**(2)** `frontend/src/lib/api.ts` — `REASON_MESSAGES`의 `registry_unreachable: ...` 항목(`:136`) 아래, 닫는 `};` 앞에 추가:

```ts
  // 아티팩트 base 설정(슬라이스 18). 정규화 3종 + 즉석 검증 3종 + 잠금 1종 --
  // reasonCodes.json 과 함께 갱신해야 양방향 커버리지 테스트가 초록이다.
  artifact_base_not_absolute: "아티팩트 경로는 절대 경로여야 합니다",
  artifact_base_traversal: "아티팩트 경로에 .. 세그먼트를 쓸 수 없습니다",
  artifact_base_scheme_in_path: "경로 중간에 file:// 를 쓸 수 없습니다",
  artifact_base_missing: "경로가 존재하지 않습니다",
  artifact_base_not_directory: "경로가 디렉터리가 아닙니다",
  artifact_base_not_writable: "경로에 쓸 수 없습니다",
  artifact_base_locked: "잡 이력이 있어 아티팩트 경로를 바꿀 수 없습니다",
```

- [ ] **Step 5: 통과를 확인한다 (양쪽 계약 포함)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_artifact_base_validation.py tests/test_reason_codes_coverage.py -q`
Expected: 전부 PASS
Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx vitest run src/lib/reasonCodes.test.ts`
Expected: PASS (json ⊆ REASON_MESSAGES + 죽은 키 없음)

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add src/dms/artifact_base.py tests/test_artifact_base_validation.py frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts
git commit -m "feat(artifact-base): 정규화 + 실파일 왕복 즉석 검증 — 사유 코드 7종 등록"
```

---

### Task 5: API 라우트 — GET / PUT(잠금·force·감사) / validate

**Files:**
- Create: `src/dms/api/routes_artifact_base.py`
- Create: `tests/test_api_artifact_base.py`
- Modify: `src/dms/api/app.py`

**Interfaces:**
- Consumes: Task 1의 `set_artifact_base`/`set_artifact_base_check` 컬럼, Task 2의 `resolve_artifact_base`/`strip_scheme`, Task 4의 `normalize_artifact_base`/`roundtrip_artifact_base`, `require_admin`/`audit_actor`(`api/auth.py:23,66`), `repos.agents.list_nodes`(`repositories/agents.py:24-34`), `Repositories.db`.
- Produces (Task 7·8이 이 모양을 그대로 쓴다):
  - `GET /api/admin/artifact-base` → `{"effective", "source": "db"|"env", "db_value", "env_value", "locked_by_jobs", "checks": {"api": {"ok", "reason"}, "controller": {"pending", "ok", "reason", "checked_at"}, "nodes": [{"node_name", "reported_at", "fresh", "pending", "exists", "writable"}]}}`
  - `PUT /api/admin/artifact-base` body `{"uri", "force"=false}` — 정규화→잠금→즉석 검증→저장+감사, 응답은 GET과 동일 모양. 409 detail은 문자열 `"artifact_base_locked"`(프론트 `request()`가 dict detail을 접기 때문 — `api.ts:177-184`).
  - `POST /api/admin/artifact-base/validate` body `{"uri"}` → `{"normalized", "ok": true}` 또는 422.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_artifact_base.py` (신규 파일 전체):

```python
"""아티팩트 base API(슬라이스 18 설계 §2.5)의 계약. 잠금(§2.3)은 artifact_uri 가
NULL 인 잡(Rejected 등)도 포함해야 한다 -- 그 잡들의 stdout/stderr 가 디스크의
유일한 진단 사본이라, NOT NULL 로 좁히면 정확히 그 증거를 버린다."""
import json

from dms.domain import DataJobState
from dms.repositories import Repositories

ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def _make_rejected_job(repos):
    """artifact_uri 가 NULL 인 잡. stepper 는 성공 경로에서만 artifact_uri 를
    기록하므로(stepper.py) Rejected 잡은 그 컬럼이 NULL 이다 -- 잠금이 이 잡도
    세어야 한다는 것이 이 헬퍼의 존재 이유다."""
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k1", payload={"storage": "s1", "target": "a"}, priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan", worker_pool={},
        precondition={}, actor="planner")
    repos.data_jobs.set_job_state(jid, DataJobState.REJECTED, actor="stepper")
    job = repos.data_jobs.get_job(jid)
    assert job["artifact_uri"] is None   # 전제 확인: NOT NULL 축소가 놓칠 잡
    return jid


def test_requires_admin(client):
    assert client.get("/api/admin/artifact-base").status_code == 401


def test_get_reports_env_source_when_db_null(client):
    body = client.get("/api/admin/artifact-base", headers=ADMIN).json()
    assert body["source"] == "env"
    assert body["effective"] == "file:///artifacts/dms"
    assert body["db_value"] is None
    assert body["env_value"] == "file:///artifacts/dms"
    assert body["locked_by_jobs"] == 0
    # 기본 env 경로는 테스트 머신에 없다 -- api 홉은 정직하게 실패를 낸다
    assert body["checks"]["api"] == {"ok": False, "reason": "artifact_base_missing"}
    # 컨트롤러는 아직 아무것도 기록하지 않았다 -- 실패가 아니라 "확인 대기 중"
    assert body["checks"]["controller"] == {"pending": True, "ok": None,
                                            "reason": None, "checked_at": None}
    assert body["checks"]["nodes"] == []


def test_put_normalizes_validates_and_saves(client, db, tmp_path):
    r = client.put("/api/admin/artifact-base",
                   json={"uri": f"file://{tmp_path}/"}, headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "db"
    assert body["db_value"] == f"file://{tmp_path}"     # 후행 슬래시 제거(정규형)
    assert body["checks"]["api"] == {"ok": True, "reason": None}
    row = db.query_one("SELECT artifact_base_uri FROM control_state WHERE id = 1")
    assert row["artifact_base_uri"] == f"file://{tmp_path}"


def test_put_rejects_bad_input_with_reason_codes(client, db, tmp_path):
    cases = [("relative/x", "artifact_base_not_absolute"),
             ("/a/../b", "artifact_base_traversal"),
             (f"{tmp_path}/file://x", "artifact_base_scheme_in_path"),
             (f"{tmp_path}/nope", "artifact_base_missing")]
    for uri, code in cases:
        r = client.put("/api/admin/artifact-base", json={"uri": uri}, headers=ADMIN)
        assert (r.status_code, r.json()["detail"]) == (422, code), uri
    # 422 는 곧 "저장 안 됨"(설계 §2.4a) -- 어느 실패도 DB 를 만지지 않았다
    row = db.query_one("SELECT artifact_base_uri FROM control_state WHERE id = 1")
    assert row["artifact_base_uri"] is None


def test_put_locked_when_any_job_exists_even_artifact_null(client, db, tmp_path):
    repos = Repositories(db)
    _make_rejected_job(repos)                  # artifact_uri NULL 인 잡 1건
    r = client.put("/api/admin/artifact-base",
                   json={"uri": f"file://{tmp_path}"}, headers=ADMIN)
    assert r.status_code == 409
    assert r.json()["detail"] == "artifact_base_locked"
    assert client.get("/api/admin/artifact-base",
                      headers=ADMIN).json()["locked_by_jobs"] == 1
    # 잠금은 저장을 막았다
    row = db.query_one("SELECT artifact_base_uri FROM control_state WHERE id = 1")
    assert row["artifact_base_uri"] is None


def test_put_force_passes_and_audits(client, db, tmp_path):
    repos = Repositories(db)
    _make_rejected_job(repos)
    r = client.put("/api/admin/artifact-base",
                   json={"uri": f"file://{tmp_path}", "force": True}, headers=ADMIN)
    assert r.status_code == 200
    entry = db.query(
        "SELECT * FROM audit_log WHERE mutation_class = 'artifact_base'")[-1]
    after = json.loads(entry["after_state"])
    assert after["forced"] is True and after["affected_jobs"] == 1


def test_validate_does_not_save(client, db, tmp_path):
    r = client.post("/api/admin/artifact-base/validate",
                    json={"uri": f"file://{tmp_path}"}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json() == {"normalized": f"file://{tmp_path}", "ok": True}
    row = db.query_one("SELECT artifact_base_uri FROM control_state WHERE id = 1")
    assert row["artifact_base_uri"] is None
    r2 = client.post("/api/admin/artifact-base/validate",
                     json={"uri": "/a/../b"}, headers=ADMIN)
    assert r2.status_code == 422 and r2.json()["detail"] == "artifact_base_traversal"


def test_node_hop_distinguishes_pending_from_failure(client, db, tmp_path):
    # 설계 §4: null(모름)과 실패를 뭉개지 않는다. 옛 base 를 프로브한 노드와
    # 프로브 자체가 없는 노드는 "확인 대기 중"(pending)이지 실패가 아니다.
    repos = Repositories(db)
    path = str(tmp_path)
    client.put("/api/admin/artifact-base", json={"uri": f"file://{path}"},
               headers=ADMIN)
    repos.agents.ingest("w1", {"node_name": "w1",
                               "artifact_base": {"path": path, "exists": True,
                                                 "writable": False}})
    repos.agents.ingest("w2", {"node_name": "w2",
                               "artifact_base": {"path": "/old/base",
                                                 "exists": True, "writable": True}})
    repos.agents.ingest("w3", {"node_name": "w3"})    # 프로브 없음(구버전 에이전트)
    nodes = {n["node_name"]: n for n in client.get(
        "/api/admin/artifact-base", headers=ADMIN).json()["checks"]["nodes"]}
    # w1: 현재 base 를 프로브했고 writable=False -- 실패는 실패로 보인다.
    # probe_mounts 의 status 는 writable 을 반영하지 않으므로(probes.py:35-42)
    # 판정은 status 가 아니라 writable 필드 직접이다(설계 §2.4b).
    assert nodes["w1"]["pending"] is False
    assert nodes["w1"]["exists"] is True and nodes["w1"]["writable"] is False
    # w2: 옛 base 의 결과 -- "확인 대기 중"이고 exists/writable 은 null
    assert nodes["w2"]["pending"] is True
    assert nodes["w2"]["exists"] is None and nodes["w2"]["writable"] is None
    assert nodes["w3"]["pending"] is True


def test_controller_hop_pending_until_checked_for_current_base(client, db, tmp_path):
    repos = Repositories(db)
    client.put("/api/admin/artifact-base", json={"uri": f"file://{tmp_path}"},
               headers=ADMIN)
    body = client.get("/api/admin/artifact-base", headers=ADMIN).json()
    assert body["checks"]["controller"]["pending"] is True
    # 옛 base 의 검증 결과는 pending 을 풀지 못한다(오독 방지 -- check_uri 대조)
    repos.control.set_artifact_base_check(uri="file:///old", ok=True, reason=None,
                                          now_iso="2026-08-10T00:00:00Z")
    body = client.get("/api/admin/artifact-base", headers=ADMIN).json()
    assert body["checks"]["controller"]["pending"] is True
    # 현재 base 를 검증하면 풀린다
    repos.control.set_artifact_base_check(uri=f"file://{tmp_path}", ok=True,
                                          reason=None,
                                          now_iso="2026-08-10T00:01:00Z")
    body = client.get("/api/admin/artifact-base", headers=ADMIN).json()
    assert body["checks"]["controller"] == {
        "pending": False, "ok": True, "reason": None,
        "checked_at": "2026-08-10T00:01:00Z"}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_artifact_base.py -q`
Expected: FAIL — 라우트 부재로 전부 404. 첫 테스트는 `assert 404 == 401`.

- [ ] **Step 3: 라우트를 구현한다**

`src/dms/api/routes_artifact_base.py` (신규 파일 전체):

```python
"""아티팩트 base 설정 API(슬라이스 18 설계 §2.5). 전부 admin 전용
(routes_control.py 와 같은 라우터 수준 의존성)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..artifact_base import (normalize_artifact_base, resolve_artifact_base,
                             roundtrip_artifact_base, strip_scheme)
from ..domain import DomainValidationError
from .auth import Identity, audit_actor, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class ArtifactBaseBody(BaseModel):
    uri: str
    force: bool = False


class ValidateBody(BaseModel):
    uri: str


def _job_count(repos) -> int:
    # 잠금 카운트(설계 §2.3): artifact_uri IS NOT NULL 로 좁히지 **않는다**.
    # 실패·타임아웃·취소·Rejected 잡은 그 컬럼이 NULL 이지만(stepper 는 성공
    # 경로에서만 기록한다) 디스크에는 러너가 쓴 stdout/stderr 가 있고, 그것이
    # 진단의 **유일한 사본**이다. NOT NULL 로 좁히면 정확히 그 잡들 -- 가장
    # 진단이 필요한 잡들 -- 의 증거를 버린다. 이 잠금은 §1-7(재시작 후 in-flight
    # summary 유실)과 §1-6(COALESCE 덮어쓰기로 preview 위치 유실)도 구조적으로
    # 닫는다: 잡이 존재하는 한 base 가 안 바뀌므로 두 함정의 전제가 성립하지
    # 않는다.
    return repos.db.query_one("SELECT COUNT(*) AS n FROM data_jobs")["n"]


def _controller_check(row, effective) -> dict:
    checked_uri = row.get("artifact_base_check_uri") if row else None
    if checked_uri != effective or not (row or {}).get("artifact_base_check_at"):
        # 컨트롤러가 아직 **이** base 를 검증하지 않았다(막 저장했거나 구버전
        # 컨트롤러) -- null(모름)이지 실패가 아니다(설계 §4). check_uri 대조가
        # 없으면 옛 base 의 성공/실패가 새 base 의 것으로 오독된다.
        return {"pending": True, "ok": None, "reason": None, "checked_at": None}
    return {"pending": False, "ok": bool(row["artifact_base_check_ok"]),
            "reason": row["artifact_base_check_reason"],
            "checked_at": row["artifact_base_check_at"]}


def _node_checks(repos, settings, effective_path) -> list[dict]:
    nodes = []
    for node in repos.agents.list_nodes(
            stale_seconds=settings.agent_report_stale_seconds):
        ab = (node["report"] or {}).get("artifact_base")
        # 아직 새 base 를 프로브하지 않은 노드(리포트에 필드가 없거나 다른 경로를
        # 프로브)는 "확인 대기 중" -- 실패와 뭉개지 않는다(설계 §4). 판정은
        # probe_mounts 식 status 가 아니라 exists/writable 필드 직접이다
        # (§1-12: status 는 writable 을 반영하지 않는다).
        pending = (not node["fresh"] or not isinstance(ab, dict)
                   or ab.get("path") != effective_path)
        nodes.append({
            "node_name": node["node_name"], "reported_at": node["reported_at"],
            "fresh": node["fresh"], "pending": pending,
            "exists": None if pending else bool(ab.get("exists")),
            "writable": None if pending else bool(ab.get("writable")),
        })
    return nodes


def _payload(request: Request) -> dict:
    repos = request.app.state.repos
    settings = request.app.state.settings
    row = repos.control.control_state()
    db_value = row.get("artifact_base_uri") if row else None
    effective = db_value or settings.artifact_base_uri
    path = strip_scheme(effective)
    # (a) API 즉석 홉은 GET 에서도 실파일 왕복으로 산다 -- 저장 시점 스냅숏이
    # 아니라 "지금 이 파드에서 되는가"가 화면이 답할 질문이다(설계 §2.4).
    # 왕복은 임시 파일 1개라 10s 폴링에도 무해하다.
    api_reason = roundtrip_artifact_base(path)
    return {
        "effective": effective,
        "source": "db" if db_value else "env",
        "db_value": db_value,
        "env_value": settings.artifact_base_uri,
        "locked_by_jobs": _job_count(repos),
        "checks": {
            "api": {"ok": api_reason is None, "reason": api_reason},
            "controller": _controller_check(row, effective),
            "nodes": _node_checks(repos, settings, path),
        },
    }


@router.get("/api/admin/artifact-base")
def get_artifact_base(request: Request):
    return _payload(request)


@router.post("/api/admin/artifact-base/validate")
def validate_artifact_base(body: ValidateBody, request: Request):
    # 저장 없는 (a) 검증(설계 §2.5) -- UI 의 사전 확인용.
    try:
        normalized = normalize_artifact_base(body.uri)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    reason = roundtrip_artifact_base(strip_scheme(normalized))
    if reason is not None:
        raise HTTPException(status_code=422, detail=reason)
    return {"normalized": normalized, "ok": True}


@router.put("/api/admin/artifact-base")
def put_artifact_base(body: ArtifactBaseBody, request: Request,
                      identity: Identity = Depends(require_admin)):
    repos = request.app.state.repos
    # 순서(설계 §2.5): 정규화 -> 잠금 -> 즉석 검증 -> 저장+감사. 잠금이 검증보다
    # 먼저다 -- 잠긴 상태에서 "경로가 없다"부터 보이면 운영자가 디렉터리를 만든
    # 다음에야 잠금을 알게 된다.
    try:
        normalized = normalize_artifact_base(body.uri)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    affected = _job_count(repos)
    if affected and not body.force:
        # detail 은 문자열 코드 하나만 -- 프론트 request() 는 dict detail 을
        # http_409 로 접는다(frontend/src/lib/api.ts). N 건은 GET 의
        # locked_by_jobs 가 이미 화면에 나른다.
        raise HTTPException(status_code=409, detail="artifact_base_locked")
    reason = roundtrip_artifact_base(strip_scheme(normalized))
    if reason is not None:
        # 즉석 검증 실패면 저장하지 않는다(설계 §2.4a) -- 422 가 곧 "저장 안 됨".
        raise HTTPException(status_code=422, detail=reason)
    repos.control.set_artifact_base(normalized, actor=audit_actor(identity),
                                    forced=bool(affected and body.force),
                                    affected_jobs=affected)
    return _payload(request)
```

- [ ] **Step 4: app.py에 라우터를 등록한다**

`src/dms/api/app.py` — **(1)** import 블록의 `from .routes_control import router as control_router` 아래에 추가:

```python
from .routes_artifact_base import router as artifact_base_router
```

**(2)** `app.include_router(control_router)` 아래에 추가:

```python
    app.include_router(artifact_base_router)
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_artifact_base.py tests/test_reason_codes_coverage.py tests/test_api_control.py -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add src/dms/api/routes_artifact_base.py src/dms/api/app.py tests/test_api_artifact_base.py
git commit -m "feat(api): /api/admin/artifact-base GET/PUT/validate — 잠금·force·감사·3홉 응답"
```

---

### Task 6: 에이전트 프로브 — mounts와 분리된 별도 필드

**Files:**
- Modify: `src/dms/agent/probes.py`, `src/dms/agent/runner.py`, `src/dms/api/routes_agent.py`
- Test: `tests/test_agent_probes.py`, `tests/test_agent_runner.py`, `tests/test_api_agent.py`, `tests/test_reconciler.py`

**Interfaces:**
- Consumes: Task 2의 `resolve_artifact_base`/`strip_scheme`, `probe_mounts` 관례(`probes.py:26-49`), `build_report`(`runner.py:18-35`), 리포트 응답(`routes_agent.py:22-31`).
- Produces:
  - `probes.probe_artifact_base(path, *, isdir=os.path.isdir, access=os.access) -> dict | None` — `{"path", "exists", "writable"}`, path가 falsy면 None.
  - 리포트 **최상위** 키 `"artifact_base"` (mounts 밖 — Task 5의 `_node_checks`가 이 키를 읽는다).
  - `POST /api/agent/report` 응답 키 `"artifact_base_path"`(스킴 제거된 경로) — 에이전트 state 키 `"artifact_base_path"`로 순환.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_agent_probes.py` 파일 끝에 추가:

```python
def test_probe_artifact_base_reports_exists_and_writable():
    # 슬라이스 18(설계 §2.4b): exists 는 hostPath type: Directory 기동 가능 여부의
    # 직접 신호이고, writable 은 에이전트 프로세스 uid 기준의 정직한 한계 표기다.
    from dms.agent.probes import probe_artifact_base
    r = probe_artifact_base("/base", isdir=lambda p: True,
                            access=lambda p, mode: False)
    assert r == {"path": "/base", "exists": True, "writable": False}
    r = probe_artifact_base("/nope", isdir=lambda p: False,
                            access=lambda p, mode: True)
    # 존재하지 않으면 writable 도 False 다(없는 디렉터리에 쓸 수 없다)
    assert r == {"path": "/nope", "exists": False, "writable": False}
    assert probe_artifact_base(None) is None
    assert probe_artifact_base("") is None
```

**(2)** `tests/test_agent_runner.py` 파일 끝에 추가:

```python
def test_build_report_carries_artifact_base_outside_mounts():
    # 슬라이스 18 최대의 함정(설계 §2.4b): reconciler 는 mounts 를 순회해
    # storages.status 로 매핑한다(reconciler.py) -- base 프로브가 mounts 로 새면
    # 유령 스토리지가 생긴다. 별도 최상위 필드를 계약으로 고정한다.
    report = build_report(
        "node-a", [], [], mountinfo_text="",
        mounts_fn=lambda storages, **k: [],
        tools_fn=lambda names, **k: [],
        identities_fn=lambda users, **k: [],
        os_fn=lambda storages, **k: {},
        artifact_base_path="/cephfs/dms/artifacts",
        artifact_base_fn=lambda p: {"path": p, "exists": True, "writable": True})
    assert report["artifact_base"] == {"path": "/cephfs/dms/artifacts",
                                       "exists": True, "writable": True}
    assert report["mounts"] == []          # mounts 에 섞이지 않았다


def test_build_report_artifact_base_none_when_target_unknown():
    # 서버가 아직 대상을 내리지 않았으면(부트스트랩 첫 사이클) 프로브도 없다 --
    # 서버 쪽에서 이 None 은 "확인 대기 중"으로 렌더된다(설계 §4).
    report = build_report("node-a", [], [], mountinfo_text="",
                          mounts_fn=lambda s, **k: [], tools_fn=lambda n, **k: [],
                          identities_fn=lambda u, **k: [], os_fn=lambda s, **k: {})
    assert report["artifact_base"] is None


def test_run_once_adopts_artifact_base_path_from_response(monkeypatch):
    # 에이전트는 ConfigMap 을 envFrom 으로 받지 않는다(50-agent-daemonset.yaml
    # 머리 주석) -- base 를 아는 유일한 경로가 리포트 응답이다(설계 §2.4b).
    def handler(request):
        return httpx.Response(200, json={
            "storages": [], "identity_probe_targets": [],
            "report_interval_seconds": 60,
            "artifact_base_path": "/cephfs/dms/artifacts"})

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools", lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities", lambda users, **k: [])
    runner = AgentRunner(SETTINGS, _client(handler))
    state = runner.run_once({"storages": [], "probe_targets": [], "interval": 60})
    assert state["artifact_base_path"] == "/cephfs/dms/artifacts"


def test_run_once_keeps_artifact_base_path_when_response_lacks_it(monkeypatch):
    # 구버전 서버 호환: 키가 없으면 기존 값을 유지한다 -- 빈 값으로 지워 다음
    # 리포트에서 프로브가 사라지게 하지 않는다.
    def handler(request):
        return httpx.Response(200, json={"storages": [], "identity_probe_targets": [],
                                         "report_interval_seconds": 60})

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools", lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities", lambda users, **k: [])
    runner = AgentRunner(SETTINGS, _client(handler))
    state = runner.run_once({"storages": [], "probe_targets": [], "interval": 60,
                             "artifact_base_path": "/keep"})
    assert state["artifact_base_path"] == "/keep"
```

**(3)** `tests/test_api_agent.py` 파일 끝에 추가:

```python
def test_report_response_carries_artifact_base_path(client, db):
    # 하달 경로(설계 §2.4b): 스킴 제거된 파일시스템 경로 -- 에이전트 프로브는
    # os.path 만 안다. DB 설정이 env 를 이긴다(resolve 와 같은 규칙).
    r = client.post("/api/agent/report", json=REPORT, headers=_agent_headers())
    assert r.json()["artifact_base_path"] == "/artifacts/dms"
    from dms.repositories import Repositories
    Repositories(db).control.set_artifact_base("file:///new/base", actor="ops")
    r = client.post("/api/agent/report", json=REPORT, headers=_agent_headers())
    assert r.json()["artifact_base_path"] == "/new/base"
```

**(4)** `tests/test_reconciler.py` 파일 끝에 추가:

```python
def test_artifact_base_report_field_does_not_touch_storages(db):
    # 슬라이스 18 최대 함정의 반대편 가드: 별도 필드 artifact_base 는 리콘사일러가
    # 완전히 무시해야 한다 -- 훗날 누가 이 프로브를 mounts 로 옮기면(또는
    # 리콘사일러가 이 필드를 읽기 시작하면) 스토리지 판정이 오염되는 그 순간을
    # 여기서 잡는다. (현행 고정 테스트라 RED 없이 통과한다 -- 의도된 것.)
    repos = _setup(db, {"n1": [_mount("Ready")]})
    repos.agents.ingest("n2", {"node_name": "n2", "mounts": [],
                               "artifact_base": {"path": "/cephfs/dms/artifacts",
                                                 "exists": True, "writable": True}},
                        reported_at="2026-08-02T09:59:00Z")
    result = reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW)
    # n2 의 base 프로브는 ceph-a 판정에 아무 영향이 없다: mounts 증거는 n1 뿐.
    assert result == {"ceph-a": "Ready"}
    assert repos.storages.get("ceph-a")["status_detail"] == "ready_nodes=1"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_agent_probes.py tests/test_agent_runner.py tests/test_api_agent.py tests/test_reconciler.py -q`
Expected: FAIL — probes 1건 `ImportError: cannot import name 'probe_artifact_base'`, runner 2건 `TypeError: build_report() got an unexpected keyword argument`·1건 `KeyError: 'artifact_base'`·1건 `KeyError: 'artifact_base_path'`, api_agent 1건 `KeyError: 'artifact_base_path'`. reconciler 신규 1건은 **이미 PASS**(현행 고정 가드 — 위 주석에 명시).

- [ ] **Step 3: probes.py에 프로브를 추가한다**

`src/dms/agent/probes.py` — `probe_mounts` 함수(`:26-49`) 아래에 추가:

```python
def probe_artifact_base(path, *, isdir=os.path.isdir, access=os.access):
    """아티팩트 base 프로브(슬라이스 18 설계 §2.4b). **mounts 배열에 섞지 않는다**:
    reconciler 가 mounts 를 storage_name 기준으로 storages.status 에 매핑하므로
    (reconciler.py) 섞으면 유령 스토리지가 생기거나 판정이 오염된다 -- 리포트
    최상위의 별도 필드로만 나른다(build_report 참고).

    exists 가 핵심 신호다: 잡 파드 hostPath 는 type: Directory 강제라
    (execution_manifests.py) 디렉터리가 없는 노드에서는 파드가 기동 자체를
    실패한다. writable 은 **에이전트 프로세스 uid** 의 W_OK 지 잡 파드 요청자
    uid 가 아니다 -- 정직한 한계로 화면이 문구로 표기한다. probe_mounts 의
    status 판정이 writable 을 반영하지 않는 것과 같은 이유로, 소비자는 status
    같은 요약이 아니라 이 두 필드를 직접 본다."""
    if not path:
        return None    # 서버가 아직 대상을 내리지 않았다(부트스트랩) -- 모름
    exists = bool(isdir(path))
    return {"path": path, "exists": exists,
            "writable": exists and bool(access(path, os.W_OK))}
```

- [ ] **Step 4: runner.py를 확장한다**

`src/dms/agent/runner.py` — **(1)** import 교체(`:10`):

```python
from .probes import (probe_artifact_base, probe_identities, probe_mounts,
                     probe_os_metrics, probe_tools)
```

**(2)** `build_report`(`:18-35`)를 다음으로 교체:

```python
def build_report(node_name, storages, probe_targets, *, mountinfo_text,
                 tool_names=AGENT_TOOL_NAMES, mounts_fn=None, tools_fn=None,
                 identities_fn=None, os_fn=None, read_text=None,
                 net_dev_path="/proc/net/dev", virtual_net_path="",
                 artifact_base_path=None, artifact_base_fn=None) -> dict:
    mounts_fn = mounts_fn or probe_mounts
    tools_fn = tools_fn or probe_tools
    identities_fn = identities_fn or probe_identities
    os_fn = os_fn or probe_os_metrics
    artifact_base_fn = artifact_base_fn or probe_artifact_base
    return {
        "node_name": node_name,
        "probed_at": utc_now_iso(),
        "mounts": mounts_fn(storages, mountinfo_text=mountinfo_text),
        "tools": tools_fn(list(tool_names)),
        "identities": identities_fn(probe_targets),
        "os": os_fn(storages, read_text=read_text, net_dev_path=net_dev_path,
                    virtual_net_path=virtual_net_path),
        # 슬라이스 18: mounts 와 **별도** 최상위 필드 -- reconciler 가 mounts 를
        # storages.status 로 매핑하므로 거기 섞으면 유령 스토리지가 생긴다.
        # 대상 경로가 아직 없으면(부트스트랩) None.
        "artifact_base": artifact_base_fn(artifact_base_path),
    }
```

**(3)** `run_once`의 `report = build_report(...)`(`:48-51`)를 다음으로 교체:

```python
        report = build_report(self._settings.node_name, state["storages"],
                              state["probe_targets"], mountinfo_text=mountinfo_text,
                              net_dev_path=self._settings.net_dev_path,
                              virtual_net_path=self._settings.virtual_net_path,
                              artifact_base_path=state.get("artifact_base_path"))
```

**(4)** 응답 처리(`:63-70`)를 다음으로 교체:

```python
            storages = body.get("storages", state["storages"])
            probe_targets = body.get("identity_probe_targets", state["probe_targets"])
            interval = body.get("report_interval_seconds", state["interval"])
            # 에이전트는 ConfigMap 을 envFrom 으로 받지 않는다(50-agent-daemonset
            # .yaml 머리 주석) -- base 를 아는 유일한 경로가 이 응답 필드다(설계
            # §2.4b). 구버전 서버 응답에는 키가 없다 -- 기존 값을 유지한다(지우면
            # 다음 리포트부터 프로브가 사라져 화면이 영구 "확인 대기 중"이 된다).
            artifact_base_path = body.get("artifact_base_path",
                                          state.get("artifact_base_path"))
            if (not isinstance(storages, list) or not isinstance(probe_targets, list)
                    or not isinstance(interval, int) or isinstance(interval, bool)
                    or not isinstance(artifact_base_path, (str, type(None)))):
                raise ValueError("malformed response fields")
            new_state = {"storages": storages, "probe_targets": probe_targets,
                         "interval": interval,
                         "artifact_base_path": artifact_base_path}
```

**(5)** `run_loop` 초기 state(`:79-80`)를 다음으로 교체:

```python
    state = {"storages": [], "probe_targets": [],
             "interval": settings.interval_seconds, "artifact_base_path": None}
```

- [ ] **Step 5: routes_agent.py 응답에 하달 필드를 추가한다**

`src/dms/api/routes_agent.py` — **(1)** import에 한 줄 추가(`from .auth import ...` 위):

```python
from ..artifact_base import resolve_artifact_base, strip_scheme
```

**(2)** return(`:26-31`)을 다음으로 교체:

```python
    return {
        "storages": storages,
        "identity_probe_targets": repos.control.probe_targets(
            ttl_seconds=settings.identity_probe_ttl_seconds),
        "report_interval_seconds": settings.agent_report_interval_seconds,
        # 슬라이스 18(설계 §2.4b): 에이전트는 ConfigMap 을 envFrom 으로 받지 않아
        # (50-agent-daemonset.yaml:5-12) base 를 아는 유일한 경로가 이 응답이다.
        # 스킴을 벗겨 내린다 -- 에이전트 프로브는 os.path 만 안다.
        "artifact_base_path": strip_scheme(
            resolve_artifact_base(repos.control, settings)),
    }
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_agent_probes.py tests/test_agent_runner.py tests/test_api_agent.py tests/test_reconciler.py tests/test_api_artifact_base.py tests/test_cli.py -q`
Expected: 전부 PASS

- [ ] **Step 7: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add src/dms/agent/probes.py src/dms/agent/runner.py src/dms/api/routes_agent.py tests/test_agent_probes.py tests/test_agent_runner.py tests/test_api_agent.py tests/test_reconciler.py
git commit -m "feat(agent): 아티팩트 base 노드 프로브 — mounts 와 분리된 별도 필드로 왕복"
```

---

### Task 7: 컨트롤러 artifact-base-check 루프

**Files:**
- Modify: `src/dms/artifact_base.py`(`controller_check_once`), `src/dms/controller.py`
- Test: `tests/test_artifact_base_validation.py`(함수 계약), `tests/test_controller.py`(루프 등록), `tests/test_api_artifact_base.py`(API 수렴)

**Interfaces:**
- Consumes: Task 1의 `set_artifact_base_check`, Task 2의 `resolve_artifact_base`/`strip_scheme`, Task 4의 `roundtrip_artifact_base`, `build_loops`(`controller.py:29-86`)와 `Loop` dataclass, `settings.reconcile_interval_seconds`.
- Produces: `artifact_base.controller_check_once(repos, settings) -> dict`(`{"uri", "ok", "reason"}`), 루프 이름 `"artifact-base-check"`(간격 `reconcile_interval_seconds`).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_artifact_base_validation.py` — 파일 머리 import 를 다음으로 교체:

```python
import os

import pytest

from dms.artifact_base import (controller_check_once, normalize_artifact_base,
                               roundtrip_artifact_base)
from dms.domain import DomainValidationError
from dms.repositories import Repositories
```

파일 끝에 추가:

```python
class _CtlSettings:
    artifact_base_uri = "file:///env/base"


def test_controller_check_records_failure_for_missing_base(db, tmp_path):
    # (c) 컨트롤러 자기 관점(설계 §2.4c): read_summary 마운트 부재의 "SUCCEEDED
    # 인데 요약이 없는" 조용한 실패(§1-3)를 사전에 DB 에 남겨 화면에 보이게 한다.
    repos = Repositories(db)
    repos.control.set_artifact_base(f"file://{tmp_path}/gone", actor="ops")
    result = controller_check_once(repos, _CtlSettings())
    assert result == {"uri": f"file://{tmp_path}/gone", "ok": False,
                      "reason": "artifact_base_missing"}
    st = repos.control.control_state()
    assert st["artifact_base_check_uri"] == f"file://{tmp_path}/gone"
    assert st["artifact_base_check_ok"] == 0
    assert st["artifact_base_check_reason"] == "artifact_base_missing"
    assert st["artifact_base_check_at"] is not None


def test_controller_check_records_success(db, tmp_path):
    repos = Repositories(db)
    repos.control.set_artifact_base(f"file://{tmp_path}", actor="ops")
    assert controller_check_once(repos, _CtlSettings())["ok"] is True
    st = repos.control.control_state()
    assert st["artifact_base_check_ok"] == 1
    assert st["artifact_base_check_reason"] is None
```

**(2)** `tests/test_controller.py` — `test_build_loops_names_and_intervals`(`:7-16`)의 기대 리스트를 다음으로 교체(`artifact-base-check`가 마지막):

```python
def test_build_loops_names_and_intervals(db, settings):
    loops = build_loops(settings, Repositories(db))
    assert [(l.name, l.interval_seconds) for l in loops] == [
        ("planner", settings.planner_interval_seconds),
        ("job-stepper", settings.stepper_interval_seconds),
        ("storage-reconciler", settings.reconcile_interval_seconds),
        ("retention", settings.retention_interval_seconds),
        ("batch-orchestrator", settings.batch_orchestrator_interval_seconds),
        ("pod-gc", settings.pod_gc_interval_seconds),
        ("artifact-base-check", settings.reconcile_interval_seconds)]
```

파일 끝에 추가:

```python
def test_artifact_base_check_loop_actually_writes_the_check_row(db, settings):
    # 이름만 보는 위 테스트는 lambda 안이 잘못돼도 초록이다(rollout-watcher 의
    # 같은 교훈) -- 한 틱이 실제로 검증 결과를 남기는지 행동으로 고정한다.
    # conftest 의 settings 기본 base(file:///artifacts/dms)는 테스트 머신에
    # 없으므로 실패 기록이 남아야 한다.
    repos = Repositories(db)
    loops = build_loops(settings, repos)
    run_all_once([l for l in loops if l.name == "artifact-base-check"],
                 repos, holder="h1")
    st = repos.control.control_state()
    assert st["artifact_base_check_uri"] == "file:///artifacts/dms"
    assert st["artifact_base_check_ok"] == 0
    assert st["artifact_base_check_reason"] == "artifact_base_missing"
```

**(3)** `tests/test_api_artifact_base.py` 파일 끝에 추가:

```python
def test_controller_loop_unblocks_api_controller_hop(client, db, tmp_path):
    # 저장 직후 "확인 대기 중" -> 컨트롤러 한 틱 -> 정상, 의 수렴을 API 수준에서
    # 고정한다(설계 §2.4 닭-달걀 회피: 저장 전엔 (a)만 강제, (b)(c)는 저장 후
    # 폴링으로 수렴).
    from dms.artifact_base import controller_check_once
    repos = Repositories(db)
    client.put("/api/admin/artifact-base", json={"uri": f"file://{tmp_path}"},
               headers=ADMIN)
    assert client.get("/api/admin/artifact-base", headers=ADMIN).json()[
        "checks"]["controller"]["pending"] is True
    controller_check_once(repos, client.app.state.settings)
    ctl = client.get("/api/admin/artifact-base", headers=ADMIN).json()[
        "checks"]["controller"]
    assert ctl["pending"] is False and ctl["ok"] is True
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_artifact_base_validation.py tests/test_controller.py tests/test_api_artifact_base.py -q`
Expected: FAIL — validation·api 파일이 수집 단계 `ImportError: cannot import name 'controller_check_once'`, controller 정확 리스트 테스트가 리스트 불일치(`artifact-base-check` 부재), 행동 테스트가 같은 ImportError 계열.

- [ ] **Step 3: 구현한다**

**(1)** `src/dms/artifact_base.py` 파일 끝에 추가:

```python
def controller_check_once(repos, settings) -> dict:
    """(c) 컨트롤러 자기 관점 검증(설계 §2.4c)의 루프 본체. 컨트롤러는
    read_summary 로 실제 **읽기**를 하는 유일한 프로세스다 -- 마운트가 없으면
    read_text 가 OSError 를 None 으로 접고(wiring.py) stepper 는 SUCCEEDED 를
    유지한 채 summary_unavailable 경고만 남긴다(§1-3, 실패가 조용하다). 그
    실패를 사전에, 화면에 보이게 주기적으로 자기 파일시스템에서 왕복 검증해
    결과를 control_state 에 남긴다. 검증한 uri 를 함께 남겨 GET 라우트가
    "옛 base 의 결과"를 "확인 대기 중"으로 구분한다(설계 §4)."""
    base = resolve_artifact_base(repos.control, settings)
    reason = roundtrip_artifact_base(strip_scheme(base))
    repos.control.set_artifact_base_check(uri=base, ok=reason is None,
                                          reason=reason)
    return {"uri": base, "ok": reason is None, "reason": reason}
```

**(2)** `src/dms/controller.py` — import에 한 줄 추가(`from .batch_orchestrator import ...` 위):

```python
from .artifact_base import controller_check_once
```

`build_loops`의 기본 `loops` 리스트에서 `Loop("pod-gc", ...)` 항목 **뒤**(리스트 닫는 `]` 앞)에 추가:

```python
        # 슬라이스 18(설계 §2.4c): 컨트롤러 관점의 아티팩트 base 검증. 간격은 새
        # 설정 키를 만들지 않고 storage-reconciler 와 같은 값을 쓴다 -- 같은
        # "증거 신선도" 계열이고 운영자가 따로 튜닝할 값이 아니다.
        Loop("artifact-base-check", settings.reconcile_interval_seconds,
             lambda: controller_check_once(repos, settings)),
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_artifact_base_validation.py tests/test_controller.py tests/test_api_artifact_base.py tests/test_controller_stepper.py tests/test_controller_planner.py tests/test_controller_batch.py -q`
Expected: 전부 PASS

- [ ] **Step 5: 백엔드 전체 회귀를 확인한다**

Run (포그라운드, Bash timeout 600000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest -q`
Expected: 전부 PASS — 기준선 998 + 이번 슬라이스 신규(대략 +37: repo 3, migrations 1, resolve 9, strip 6, validation 9, api 10, agent 7, reconciler 1, controller 2 — 정확 수는 실행이 확정한다. root 실행 환경이면 chmod 테스트 1건 skipped)

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add src/dms/artifact_base.py src/dms/controller.py tests/test_artifact_base_validation.py tests/test_controller.py tests/test_api_artifact_base.py
git commit -m "feat(controller): artifact-base-check 루프 — 컨트롤러 관점 검증을 control_state 에 기록"
```

---

### Task 8: 프론트 — /admin/artifact-base 화면

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/app/router.tsx`, `frontend/src/app/AppShell.tsx`
- Create: `frontend/src/features/control/useArtifactBase.ts`, `frontend/src/features/control/ArtifactBasePage.tsx`
- Test: `frontend/src/features/control/ArtifactBasePage.test.tsx` (신규)

**Interfaces:**
- Consumes: Task 5의 응답 모양(effective/source/db_value/env_value/locked_by_jobs/checks), Task 4의 사유 코드(REASON_MESSAGES 매핑 완료), `Dialog`/`Card`/`Button` 컴포넌트, `apiGet`/`apiSend`/`ApiError`.
- Produces: 라우트 `/admin/artifact-base`(RequireRole admin — `router.tsx:62`의 `/admin/control` 패턴), nav 「아티팩트 경로」, 훅 `useArtifactBase`(10s 폴링)/`useValidateArtifactBase`/`useSetArtifactBase`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/features/control/ArtifactBasePage.test.tsx` (신규 파일 전체):

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { ArtifactBasePage } from "./ArtifactBasePage";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const BASE = {
  effective: "file:///cephfs/dms/artifacts", source: "env",
  db_value: null, env_value: "file:///cephfs/dms/artifacts", locked_by_jobs: 0,
  checks: {
    api: { ok: true, reason: null },
    controller: { pending: false, ok: true, reason: null, checked_at: "2026-08-10T00:00:00Z" },
    nodes: [
      { node_name: "w1", reported_at: "t", fresh: true, pending: false, exists: true, writable: true },
      { node_name: "w2", reported_at: "t", fresh: true, pending: true, exists: null, writable: null },
    ],
  },
};

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><ArtifactBasePage /></QueryClientProvider>);
}

test("env 소스 배지 + 노드 행의 「확인 대기 중」을 실패와 구분해 렌더한다", async () => {
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(BASE)));
  wrap();
  expect(await screen.findByText("env 기본")).toBeInTheDocument();
  // w2 는 아직 새 경로를 프로브하지 않았다(설계 §4) -- "확인 대기 중"이지
  // "없음/불가"(실패)가 아니다.
  const w2 = screen.getByText("w2").closest("tr")!;
  expect(w2).toHaveTextContent("확인 대기 중");
  expect(w2).not.toHaveTextContent("불가");
  const w1 = screen.getByText("w1").closest("tr")!;
  expect(w1).toHaveTextContent("있음");
  expect(w1).toHaveTextContent("가능");
  // writable 한계 문구(설계 §2.4b: 에이전트 uid 기준)를 화면에 그대로 적는다
  expect(screen.getByText(/에이전트 프로세스\(uid\) 기준/)).toBeInTheDocument();
});

test("DB 소스 배지 + 컨트롤러 홉 실패는 사유 코드와 함께 실패로 렌더된다", async () => {
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json({
    ...BASE, source: "db", db_value: "file:///new",
    checks: { ...BASE.checks,
      controller: { pending: false, ok: false, reason: "artifact_base_missing", checked_at: "t" } },
  })));
  wrap();
  expect(await screen.findByText("DB 설정")).toBeInTheDocument();
  expect(screen.getByText("실패")).toBeInTheDocument();
  expect(screen.getByText("artifact_base_missing")).toBeInTheDocument();
});

test("검증 버튼은 저장 없이 validate 만 호출한다", async () => {
  const calls: string[] = [];
  server.use(
    http.get("/api/admin/artifact-base", () => HttpResponse.json(BASE)),
    http.post("/api/admin/artifact-base/validate", () => {
      calls.push("validate");
      return HttpResponse.json({ normalized: "file:///new", ok: true });
    }),
    http.put("/api/admin/artifact-base", () => {
      calls.push("put");
      return HttpResponse.json(BASE);
    }),
  );
  wrap();
  await screen.findByText("env 기본");
  await userEvent.type(screen.getByLabelText("새 경로"), "/new");
  await userEvent.click(screen.getByRole("button", { name: "검증" }));
  expect(await screen.findByText(/검증 통과/)).toBeInTheDocument();
  expect(calls).toEqual(["validate"]);   // PUT 이 나가지 않았다(저장 없음)
});

test("409 잠금이면 확인 다이얼로그를 강제하고, 강제 변경만 force=true 를 보낸다", async () => {
  const bodies: unknown[] = [];
  server.use(
    http.get("/api/admin/artifact-base", () =>
      HttpResponse.json({ ...BASE, locked_by_jobs: 3 })),
    http.put("/api/admin/artifact-base", async ({ request }) => {
      const body = (await request.json()) as { uri: string; force: boolean };
      bodies.push(body);
      if (!body.force) {
        return HttpResponse.json({ detail: "artifact_base_locked" }, { status: 409 });
      }
      return HttpResponse.json(BASE);
    }),
  );
  wrap();
  await screen.findByText("env 기본");
  await userEvent.type(screen.getByLabelText("새 경로"), "/new");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  // 설계 §3: N 건과 "열람이 깨집니다"를 확인시킨 뒤에만 force 재요청
  expect(await screen.findByText(/기존 잡 3건/)).toBeInTheDocument();
  expect(screen.getByText(/아티팩트·로그 열람이 깨집니다/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "강제 변경" }));
  expect(bodies).toEqual([{ uri: "/new", force: false }, { uri: "/new", force: true }]);
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx vitest run src/features/control/ArtifactBasePage.test.tsx`
Expected: FAIL — `Error: Failed to resolve import "./ArtifactBasePage"` (모듈 부재)

- [ ] **Step 3: 타입·훅·페이지를 구현한다**

**(1)** `frontend/src/lib/types.ts` — `ControlState` 인터페이스 아래에 추가:

```ts
export interface ArtifactBaseNodeCheck {
  node_name: string; reported_at: string; fresh: boolean;
  // pending = 이 노드가 아직 현재 base 를 프로브하지 않았다("확인 대기 중") --
  // 실패와 다른 상태다(설계 §4). pending 이면 exists/writable 은 null 이다.
  pending: boolean; exists: boolean | null; writable: boolean | null;
}
export interface ArtifactBaseControllerCheck {
  pending: boolean; ok: boolean | null; reason: string | null;
  checked_at: string | null;
}
export interface ArtifactBaseInfo {
  effective: string; source: "db" | "env"; db_value: string | null; env_value: string;
  locked_by_jobs: number;
  checks: {
    api: { ok: boolean; reason: string | null };
    controller: ArtifactBaseControllerCheck;
    nodes: ArtifactBaseNodeCheck[];
  };
}
```

**(2)** `frontend/src/features/control/useArtifactBase.ts` (신규 파일 전체):

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { ArtifactBaseInfo } from "../../lib/types";

// (b)(c) 홉은 저장 "후" 수렴한다(에이전트 보고 60s·컨트롤러 30s 주기) -- 화면이
// 폴링으로 따라간다(설계 §2.4). useNodes 와 같은 10s.
export const useArtifactBase = () =>
  useQuery({ queryKey: ["artifact-base"],
             queryFn: () => apiGet<ArtifactBaseInfo>("/api/admin/artifact-base"),
             refetchInterval: 10000 });

export interface ArtifactBaseBody { uri: string; force: boolean }

export const useValidateArtifactBase = () =>
  useMutation({ mutationFn: (b: { uri: string }) =>
    apiSend<{ normalized: string; ok: boolean }>(
      "POST", "/api/admin/artifact-base/validate", b) });

export const useSetArtifactBase = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (b: ArtifactBaseBody) =>
    apiSend<ArtifactBaseInfo>("PUT", "/api/admin/artifact-base", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["artifact-base"] }) });
};
```

**(3)** `frontend/src/features/control/ArtifactBasePage.tsx` (신규 파일 전체):

```tsx
import { useState } from "react";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ApiError } from "../../lib/api";
import type { ArtifactBaseNodeCheck } from "../../lib/types";
import { useArtifactBase, useSetArtifactBase, useValidateArtifactBase } from "./useArtifactBase";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

function Hop({ ok, pending, reason }: { ok: boolean | null; pending: boolean; reason: string | null }) {
  // null(모름)과 실패를 뭉개지 않는다(설계 §4) -- "확인 대기 중"은 실패가 아니다.
  if (pending) return <span className="text-muted">확인 대기 중</span>;
  return (
    <span>
      {ok ? <span className="text-ok">정상</span> : <span className="text-bad">실패</span>}
      {reason && <span className="ml-2 text-muted">{reason}</span>}
    </span>
  );
}

function NodeRow({ n }: { n: ArtifactBaseNodeCheck }) {
  return (
    <tr>
      <td className="py-1 pr-3">{n.node_name}</td>
      <td className="py-1 pr-3">{n.pending ? "확인 대기 중" : n.exists ? "있음" : "없음"}</td>
      <td className="py-1 pr-3">{n.pending ? "확인 대기 중" : n.writable ? "가능" : "불가"}</td>
    </tr>
  );
}

export function ArtifactBasePage() {
  const q = useArtifactBase();
  const setBase = useSetArtifactBase();
  const validate = useValidateArtifactBase();
  const [uri, setUri] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  const save = (force: boolean) => {
    setBase.mutate({ uri, force }, {
      onSuccess: () => setConfirmOpen(false),
      onError: (e) => {
        // 잠금(409)만 다이얼로그로 승격 -- 그 외 오류는 폼 아래 문구로 남는다.
        if ((e as ApiError).code === "artifact_base_locked") setConfirmOpen(true);
      },
    });
  };
  const d = q.data;
  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">아티팩트 경로</h1>
      {q.isLoading ? (
        <p className="text-muted">불러오는 중…</p>
      ) : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : d && (
        <>
          <Card>
            <h2 className="font-medium mb-2">현재 값</h2>
            <p className="text-sm">
              <span className="font-mono">{d.effective}</span>
              <span className={`ml-2 rounded px-2 py-0.5 text-xs ${d.source === "db" ? "bg-accent text-white" : "bg-black/10"}`}>
                {d.source === "db" ? "DB 설정" : "env 기본"}
              </span>
            </p>
            <p className="text-sm text-muted mt-1">env: <span className="font-mono">{d.env_value}</span></p>
            <p className="text-sm text-muted">DB: <span className="font-mono">{d.db_value ?? "—"}</span></p>
          </Card>
          <Card>
            <h2 className="font-medium mb-2">3홉 검증</h2>
            <dl className="text-sm space-y-1">
              <div className="flex gap-2">
                <dt className="text-muted w-28">API(즉석)</dt>
                <dd><Hop ok={d.checks.api.ok} pending={false} reason={d.checks.api.reason} /></dd>
              </div>
              <div className="flex gap-2">
                <dt className="text-muted w-28">컨트롤러</dt>
                <dd><Hop ok={d.checks.controller.ok} pending={d.checks.controller.pending}
                         reason={d.checks.controller.reason} /></dd>
              </div>
            </dl>
            {d.checks.nodes.length > 0 && (
              <table className="text-sm mt-3 w-full">
                <thead><tr className="text-left text-muted">
                  <th className="pr-3 font-medium">노드</th>
                  <th className="pr-3 font-medium">디렉터리 존재</th>
                  <th className="pr-3 font-medium">쓰기</th></tr></thead>
                <tbody>{d.checks.nodes.map((n) => <NodeRow key={n.node_name} n={n} />)}</tbody>
              </table>
            )}
            {/* 정직한 한계 표기(설계 §2.4b): W_OK 판정 주체를 화면에 그대로 적는다 */}
            <p className="text-xs text-muted mt-2">쓰기 가능 여부는 에이전트 프로세스(uid) 기준입니다 — 잡 파드 요청자 권한과 다를 수 있습니다</p>
          </Card>
          <Card>
            <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); save(false); }}>
              <label className="block">새 경로 (file:///절대경로)
                <input aria-label="새 경로" className={field} value={uri}
                       onChange={(e) => setUri(e.target.value)} /></label>
              {validate.isSuccess && (
                <p className="text-ok">{`검증 통과: ${validate.data.normalized}`}</p>
              )}
              {validate.isError && (
                <p className="text-bad">{(validate.error as ApiError).message}</p>
              )}
              {setBase.isError && (setBase.error as ApiError).code !== "artifact_base_locked" && (
                <p className="text-bad">{(setBase.error as ApiError).message}</p>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <Button type="button" disabled={validate.isPending || uri === ""}
                        onClick={() => validate.mutate({ uri })}>검증</Button>
                <Button type="submit" disabled={setBase.isPending || uri === ""}>저장</Button>
              </div>
            </form>
          </Card>
          <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}
                  title="아티팩트 경로 강제 변경" trigger={<span aria-hidden="true" />}>
            <div className="space-y-3 text-sm">
              {/* 설계 §2.3: 잠금은 실패 잡의 stdout/stderr(디스크의 유일한 진단
                  사본)까지 지키는 장치다 -- 강제 변경의 대가를 그대로 보여주고
                  확인시킨 뒤에만 force=true 를 보낸다. */}
              <p>{`기존 잡 ${d.locked_by_jobs}건이 있습니다. 경로를 바꾸면 이 잡들의 아티팩트·로그 열람이 깨집니다.`}</p>
              <div className="flex justify-end gap-2">
                <Button type="button" onClick={() => setConfirmOpen(false)}>취소</Button>
                <Button type="button" disabled={setBase.isPending}
                        onClick={() => save(true)}>강제 변경</Button>
              </div>
            </div>
          </Dialog>
        </>
      )}
    </section>
  );
}
```

- [ ] **Step 4: 라우터·네비게이션을 등록한다**

**(1)** `frontend/src/app/router.tsx` — import 블록의 `import { ControlStatePage } ...` 아래에 추가:

```tsx
import { ArtifactBasePage } from "../features/control/ArtifactBasePage";
```

`/admin/control` Route(`:62`) 아래에 추가:

```tsx
          <Route path="/admin/artifact-base" element={<RequireRole role="admin"><AppShell><ArtifactBasePage /></AppShell></RequireRole>} />
```

**(2)** `frontend/src/app/AppShell.tsx` — `컨트롤 상태` NavLink(`:23`) 아래에 추가:

```tsx
        {isAdmin && <NavLink to="/admin/artifact-base" className={linkCls}>아티팩트 경로</NavLink>}
```

- [ ] **Step 5: 통과와 전체 회귀를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx vitest run`
Expected: 전부 PASS — 기준선 215 + 신규 4 = 219 passed / 49 files
Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx tsc -b`
Expected: 출력 없음(타입 에러 0)
Run (백엔드 최종 회귀, 포그라운드 Bash timeout 600000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest -q`
Expected: 전부 PASS (Task 7 Step 5와 동일 규모)

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20
git add frontend/src/lib/types.ts frontend/src/features/control/useArtifactBase.ts frontend/src/features/control/ArtifactBasePage.tsx frontend/src/features/control/ArtifactBasePage.test.tsx frontend/src/app/router.tsx frontend/src/app/AppShell.tsx
git commit -m "feat(portal): /admin/artifact-base 화면 — 3홉 검증 패널 + 잠금 확인 다이얼로그"
```

---

## 플랜 이후: 배포·실증 (설계 §6 — 별도 ops, 플랜 밖)

플랜 실행(8태스크 커밋)이 끝나면 컨트롤러가 테스트베드에서 수행한다 — 플랜 태스크가 아니다(슬라이스 12~17과 동일 관례). 이번에는 **에이전트 코드가 바뀌었으므로** `dms`와 `dms-agent` 둘 다 범프한다(`install/docker/Dockerfile.testbed`로 빌드 — deploy/Dockerfile은 kubectl이 없다).

1. `deploy/k8s`의 태그 범프(40/41/30 → `dms:d29`, 50 → `dms-agent:d29`) 후 빌드/푸시, `kubectl apply` — 마이그레이션은 initContainer가 자동 수행(`_ensure_columns` ALTER 경로가 라이브 DB에 컬럼을 보강하는지 `\d control_state`로 확인).
2. (§6-1) DB 미설정 상태에서 `GET /api/admin/artifact-base` → `source == "env"`, `effective == file:///cephfs/dms/artifacts` — 기존 배포 무변화(하위호환).
3. (§6-2) 잡 0건(신규 환경)에서 `/cephfs` 아래 새 경로로 변경 성공 → 새 잡이 새 경로 아래에 아티팩트를 쓰는지(`ls <새경로>/<job_id>/execution/`), 포탈 아티팩트 열람이 되는지.
4. (§6-3) 잡이 있는 상태에서 PUT → 409와 잠금 다이얼로그, force → 통과 + `audit_log`에 `forced: true, affected_jobs: N`.
5. (§6-4) 없는 경로 → 422 `artifact_base_missing`으로 저장 거부.
6. (§6-5) **핵심 실증**: `/cephfs` 밖 경로(예 API 파드 안에서만 존재하는 `/tmp/x`) → API 즉석 검증은 통과하지만, 화면의 컨트롤러 홉·노드별 홉이 「확인 대기 중」→실패로 갈라지는 것이 3홉 UI로 드러나는지(§1-3, §1-4 마운트 경계의 가시화). 에이전트 보고 주기(60s)와 컨트롤러 주기(30s) 안에 수렴하는지.
7. (§6-6) 경로 중간 `file://` 입력이 저장 시점에 422 `artifact_base_scheme_in_path`로 거부되는지.

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 태스크 |
|---|---|
| §1 실측 전제(frozen Settings, 소비 4곳, 마운트 경계, type:Directory, 읽기 라우트 조립, artifact_uri NULL 잡, COALESCE, 생성자 캡처, 단일 base 보안 불변식, 스킴 제거 2계열, GC 부재, control_state 선례, 프로브 응답 하달) | 실측 고정값 표 + 각 태스크 근거 주석 |
| §2.1 control_state 저장 + NULL→env + 전용 UPDATE 분리 + resolve 단일 함수 + 소비자 재배선(생성자 캡처 제거) | Task 1(컬럼·메서드) + Task 2(resolve·재배선) |
| §2.2 정규화(후행 슬래시·`..`·중간 file://·상대경로) 저장 시점 1곳 + strip_scheme 통일 4곳 | Task 4(정규화) + Task 3(통일 — 갈라짐을 붙잡는 행동 테스트 + grep 고정) |
| §2.3 잠금(COUNT(*) 전체 — NOT NULL 축소 금지) + force + 감사 `{forced, affected_jobs}` | Task 5(라우트, `_job_count` 주석에 근거 원문) + Task 1(감사 페이로드) |
| §2.4 3홉 검증(a 즉석 왕복 / b 노드 exists·writable + uid 한계 / c 컨트롤러 주기 기록) + 닭-달걀 회피(저장 전 (a)만, 저장 후 수렴 폴링) | Task 4(a 왕복) + Task 6(b 프로브 — mounts 분리) + Task 7(c 루프) + Task 5(checks 응답)+Task 8(폴링 UI) |
| §2.5 API 표면(GET/PUT/validate, require_admin) | Task 5 |
| §3 화면(새 페이지, ControlStatePage 분리, source 배지, 검증 패널, 확인 대기 중 구분, writable 한계 문구, 잠금 다이얼로그) | Task 8 |
| §4 오류 처리(사유 코드 구분·422/409, fail-soft pending, null≠실패) | Task 4(코드 7종+매핑) + Task 5(pending 구분) + Task 8(렌더 구분) |
| §5 테스트 목록(resolve 3소비자, 정규화 5케이스, strip 통일 grep+행동, 잠금 0건/NULL 잡/force 감사, 왕복 tmp_path·저장 불발, 마이그레이션 양쪽, 프론트 배지·다이얼로그·대기 vs 실패) | Task 1~8 각 Step 1 |
| §6 실증 | 플랜 이후 절(관례 — 플랜 태스크 아님) |
| §7 하지 않는 것(운영 중 이전, 파일 이동/GC, 이중 base 조회, artifact_uri 읽기 승격, 잡 파드 preflight test -w) | 어떤 태스크도 만들지 않음 — 파일을 만지는 코드는 왕복 probe 임시 파일 1개뿐이고 즉시 삭제된다 |

**2. 플레이스홀더 점검** — "TBD"/"적절히 처리"/코드 없는 테스트 지시 없음. 신규 파일 5개(백엔드 2 + 테스트 4... 정확히는 `artifact_base.py`·`routes_artifact_base.py`·테스트 4·프론트 3)와 모든 수정 지점에 실제 코드 전문이 있고, 반복되는 실행 명령·경로도 태스크마다 전문 수록했다. 다른 태스크 참조는 Interfaces 블록의 시그니처로만 한다.

**3. 타입 일관성** — 컬럼명 `artifact_base_uri`/`artifact_base_check_{uri,ok,reason,at}`은 Task 1 스키마·리포지토리, Task 5 라우트(`_controller_check`), Task 7 루프, 테스트 SQL이 한 철자다. `resolve_artifact_base`/`strip_scheme`/`normalize_artifact_base`/`roundtrip_artifact_base`/`controller_check_once`는 Task 2·4·7이 정의하고 Task 3·5·6이 같은 이름으로 import한다. 응답 키 `effective/source/db_value/env_value/locked_by_jobs/checks.{api,controller,nodes}`와 노드 항목 `{node_name, reported_at, fresh, pending, exists, writable}`은 Task 5 라우트 → Task 8 `types.ts`/JSX/msw 픽스처가 동일 철자다. 에이전트 왕복 키 `artifact_base_path`(응답·state)와 리포트 키 `artifact_base`(`{path, exists, writable}`)는 Task 6 서버·러너·프로브·Task 5 `_node_checks`가 동일 철자다. 사유 코드 7종은 Task 4의 json·REASON_MESSAGES·Task 5 라우트·테스트가 동일 철자다.

**알려진 위험:**
- **어댑터 str|callable 이중 수용**: 타입 순수성 대신 기존 테스트 10여 곳·스텁 조립의 churn 회피를 택했다. 생성자 주석이 근거를 명문화했고, `test_adapter_still_accepts_a_fixed_string_base`가 겸용 계약을 고정한다.
- **GET마다 실파일 왕복**: 10s 폴링 × 임시 파일 1개 생성·삭제. cephfs에서 무해하지만 폴링 간격을 줄이면 곱해진다 — `_payload` 주석에 이유를 남겼다.
- **DB 값을 NULL로 되돌리는 API 없음**: env로 돌리려면 env와 같은 값을 PUT하면 된다(effective 동일). 진짜 「미설정 복원」이 필요해지면 후속에서 PUT body에 `uri: null`을 열면 된다 — 이번 범위 밖.
- **reconciler 가드 테스트는 RED 없는 현행 고정**이다(Task 6 Step 2에 명시) — 미래 회귀(프로브를 mounts로 옮기는 변경)를 잡는 것이 목적이라 의도된 것.
- **writable은 에이전트 uid(root) 기준**: 잡 파드 요청자 uid의 쓰기 가능성과 다를 수 있다 — 설계가 정직한 한계로 명시했고 화면 문구·프로브 docstring에 그대로 적었다. 잡별 실제 검증은 preflight(`test -w`)가 이미 한다.
- **controller-check 루프가 매 30s 단일 행 UPDATE**를 친다: 리스 UPDATE가 이미 틱마다 도는 DB라 순증은 무시 수준이지만, PG에서 dead tuple이 미세하게 는다 — 문제가 되면 「변화 있을 때만 쓰기」로 좁힐 수 있다(checked_at 신선도와 트레이드오프).
- **에이전트 구버전 혼재 창**: 서버만 먼저 롤아웃되면 노드 홉이 전부 「확인 대기 중」으로 남는다 — 실패로 오독되지 않는 것이 정확히 설계 §4의 의도이고, 에이전트 롤아웃 완료 시 자연 수렴한다.
