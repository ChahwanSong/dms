# 슬라이스 24 — 파괴적 경로 fail-open 봉인 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 백로그 §2.1 의 4건 — 실패·미지 입력이 **파괴적 경로(drm)나 무제한 동작으로 흘러가는데 아무도 못 보는** 결함 — 을 구조로 닫는다. (1) 미지 도구 fail-closed 3층: 스테퍼가 `TOOL_TO_POLICY` 밖 tool 을 제출 전에 종단(`unknown_tool`, 층1), `tool_argv` 의 `# drm` fall-through 를 명시 분기 + raise 로(층2), 러너가 allowlist 밖 tool 을 exec 없이 거부(층3 — 이미 제출된 매니페스트/env 의 사후 변조까지 막는 최종 방어). (2) mount_path·managed_root `"/"` 명시 거부 + `_abs` 를 `posixpath.join` 으로(레거시 `"/"` 행의 `//` 경로 2차 방어). (3) `_abs` 결측 폴백(상대경로 무로그 반환 — dsync 를 컨테이너 오버레이에 쓰고 SUCCEEDED 로 위장하는 조용한 데이터 증발) 삭제 → `storage_missing_at_step` 종단 + 이벤트, storage update 에 delete 와 대칭인 `storage_in_use` 409 가드(경로·백엔드 변경만, enabled 토글은 통과). (4) 고아 복구 스윕에 오래된순 + LIMIT 200 + 행 단위 격리. 새 테이블·컬럼 0, 새 설정 키 0, 새 의존성 0, 신설 사유 코드 2종(`unknown_tool`/`storage_missing_at_step`)은 json/REASON_MESSAGES 양쪽 등록. 파괴적 연산의 변경이므로 기존 정상 경로 무회귀(4종 도구·rm argv·상태기계)를 기존 스위트 초록으로 보증한다.

**Architecture:** 순수 함수에서 배선 순서로 쌓는다. (1) `execution_manifests.tool_argv` — fall-through 제거(층2). 어댑터 blanket except(`execution_volcano.py:158-159`)가 `submit_failed`(detail=도구명)로 접는 것까지 테스트로 고정. (2) `stepper._step_one` 진입 가드 + `_fail_closed` 헬퍼(층1) — REJECTED/FAILED 갈림은 `preflight_submit_failed`→REJECTED / `execution_submit_failed`→FAILED 의 기존 대칭 그대로, 살아 있는 phase_refs 는 `_reclaim_if_terminal` 관례대로 best-effort terminate. `_build_spec` 의 "미지 tool → 타임아웃 없음" 관용 분기는 도달 불능이 되어 제거. (3) `dms_job_runner.runner` allowlist(층3) — `dms` 를 import 하지 않는 독립 패키지라 튜플을 중복 정의하고, `dms.config.AGENT_TOOL_NAMES`(`src/dms/config.py:7`)와의 동일성은 저장소 테스트가 강제. (4) `storages._validate` `"/"` 거부 + `routes_storages` update 가드. (5) `stepper._abs` join + 결측 예외 — **fail-closed 의 대가로 스테퍼를 실제로 돌리는 기존 테스트 8파일의 잡 팩토리에 storage 씨딩이 필요하다**(실측 목록, Task 5 Step 4). (6) `data_jobs.terminal_jobs_with_live_request` LIMIT/ORDER + `controller._stepper_step` 행 격리. 화면 신설 없음 — 신설 사유 2종의 문구가 기존 reasonText 경로로 잡 상세에 뜬다.

**Tech Stack:** Python 3.11 표준 라이브러리(`posixpath` 추가 import 뿐), FastAPI 라우트, 프론트는 `reasonCodes.json`+`api.ts` 문구 2줄(테스트·컴포넌트 무변경). DB 스키마 무변경(`tests/test_migrations.py` 의 `len(ALL_TABLES) == 20` 이 그대로 초록이어야 한다).

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-11-dms-destructive-failopen-slice24-design.md`. 플랜과 충돌하면 **설계가 이긴다**(단, 아래 「설계 §1 전제 재확인」의 정정은 이 플랜이 실측으로 갱신한 사실이다).
- **새 pip/npm 의존성 금지. 새 DB 테이블·컬럼 금지** — 이 슬라이스는 설계상 스키마 무변경이고, 실제로도 어떤 태스크도 `migrations.py` 를 건드리지 않는다. `tests/test_migrations.py` 가 `len(ALL_TABLES) == 20` 을 단언한다(만약 후속 조정으로 컬럼이 필요해지면 CREATE TABLE 과 `_ensure_columns` **양쪽**에 넣는 것이 슬라이스 14 의 교훈이지만, 이 플랜에는 그런 태스크가 없다).
- **신설 사유 코드는 `frontend/src/lib/reasonCodes.json` 과 `frontend/src/lib/api.ts` 의 REASON_MESSAGES 를 같은 커밋에서 갱신**한다 — 백엔드 `tests/test_reason_codes_coverage.py`(src/dms/ AST 리터럴 ⊆ json)와 프론트 `reasonCodes.test.ts`(json ⊆ REASON_MESSAGES, 죽은 키 금지)가 양방향으로 건다. Task 2(`unknown_tool`)·Task 5(`storage_missing_at_step`)가 각자 자기 커밋에 등록한다 — 마지막으로 미루면 그 커밋 시점에 계약 테스트가 빨간불이다. **JSON 재포맷 금지, 항목 추가만 최소 diff 로**(과거 indent 재포맷으로 131줄 diff 사고).
- **null(모름)과 실패를 섞지 않는다. 0 은 정상값 — truthy 검사 금지.** 러너 거부 summary 는 3키 계약(`{"returncode": 1, "files": null, "bytes": null}`) 유지, 고아 0건 스윕은 무이벤트가 정상이다.
- AST 계약 테스트는 `reason_code=`/`detail=` **키워드의 문자열 리터럴**과 예외 생성자 1번 인자만 추출한다(`test_reason_codes_coverage.py:36-38,81-91`) — 스테퍼의 신설 코드는 반드시 `self._fail_closed(job, reason_code="unknown_tool")` 처럼 **키워드 리터럴**로 호출해 추출되게 한다(위치 인자로 넘기면 계약 그물 밖이다).
- **커밋은 pathspec 으로 한정한다**: 신규 파일만 `git add <파일>` 선행 후, 항상 `git commit -m "..." -- <경로들>` 형태. `git add -A`·`git add .`·`git commit -a` **금지** — 워크트리 공유 중 인덱스 섞임 사고가 있었다.
- **origin push 금지, 브랜치 변경 금지, 플랜 태스크에서 `deploy/k8s` 의 이미지 태그 변경 금지**(태그 범프 d35 는 「플랜 이후: 배포·실증」의 첫 단계 — 배포자가 그 절차대로 한다). `docs/` 아래는 이 플랜 파일 외 생성·수정 금지(실증 후 BACKLOG 갱신도 플랜 밖).
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- 백엔드 테스트 명령(이 워크트리 전용, `.venv` 는 워크트리 밖 공용):
  `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest <대상> -q`
  전체 스위트는 **포그라운드**로 Bash timeout 900000ms. **기준선 1166 passed(2026-08-12 실측, 379s).**
- 프론트: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run`(**기준선 228 passed**, 2026-08-12 실측), 타입체크 `npx tsc -b`. node_modules 존재 실측 — `npm ci` 불필요(실행이 깨질 때만 `npm ci --prefer-offline --no-audit --no-fund`).
- 주석은 **한국어**로 「왜」를 적는다.

## 설계 §1 전제 재확인 (2026-08-12, 코드 직접 실측)

설계는 2026-08-11 에 쓰였고 그 사이 슬라이스 21 후속(f94bbc8)·22(4e13cda~beed7b2)가 들어갔다. 12개 항목을 전부 재확인했다 — **결론 요지는 전부 유지**되고, 정정은 라인 드리프트·주변 사실 5건이다.

| 설계 §1 항목 | 재확인 결과 |
|---|---|
| 1. placement 리터럴 4종·create_job 무검증 INSERT·`domain.Tool` 죽은 코드 | ✓ 유지. `placement.py:67,74,85,94` 그대로, `data_jobs.py:61-86` 무검증, `domain.py:54-58` 의 `Tool` 은 전 소스 grep 0건(정의뿐) |
| 2. `tool_argv` fall-through·`render_tool_flags` `[]` | ✓ 유지. `execution_manifests.py:54-55`(`# drm` 주석 + 무조건 return), `:36`. **실행 재확인**: tool="dwalk" → `['/cephfs/x']`, dryrun → `['--dryrun', '/cephfs/x']`, flags `[]` |
| 3. 러너 `exec {tool}`·runuser·`_build_summary` fail-closed | ✓ 유지. `runner.py:66-68`(rank.sh), `commands.py:37-44`(mpirun→runuser), `_build_summary` 는 `runner.py:116-137`(미지 도구 fold 주석 `:134`) |
| 4. 스테퍼 미지 tool "타임아웃 없음" 관용 | ✓ 유지. `stepper.py:70-83`(주석 70-74, `TOOL_TO_POLICY.get` 75, policy None → timeout None 78-79) |
| 5. `_validate` 의 "/" 통과·`_abs` f-string·normpath 함정 | ✓ 유지. **실행 재확인**: `_validate("s1","/","/","cephfs")` ACCEPTED / `("s1","/cephfs","/",...)` 거부 / `posixpath.normpath("//team/data")` == `"//team/data"`(보존) / `posixpath.join("/","team/data")` == `"/team/data"` / f-string 은 `"//team/data"`. `storages.py:16` 의 `p != "/"` 예외 절, `stepper.py:46-50` |
| 6. mount "/" → 노드 루트 hostPath 단일 볼륨 | ✓ 유지. `execution_volcano.py:112-120`, 볼륨 이름 fallback `or "root"` 는 `:119` |
| 7. `validate_rm_target` 은 ""/"." 만 거부 | ✓ 유지. `domain.py:97-103` |
| 8. `_abs` 폴백 무로그·delete 가드만 존재·update 무가드·NOT NULL | ✓ 유지. `stepper.py:46-50`, `routes_storages.py:59-60`(delete)·`:42-53`(update 무가드), `requests.py:106-117`, `migrations.py:209`(managed_root NOT NULL) |
| 9. 고아 쿼리 무 ORDER/LIMIT·행 격리 없음·같은 파일 선례 | ✓ 유지. `data_jobs.py:329-341`, `controller.py:44-49`(설계 표기 42-49 는 주석 포함 — 실행 루프는 44-49), 선례 `terminal_jobs_older_than` `:305-327`(LIMIT 200 + 오래된순 + 이유 주석) |
| 10. 단일 스레드 순차 루프·리스 30초·finalize 멱등 창·replicas 1 | **정정(라인 드리프트)**: 슬라이스 22 커밋(crash-restart 규약 주석)으로 `run_forever` 는 `controller.py:127-141`, 리스 획득은 `:111-113`(설계 표기 126-133/103-105). 사실관계는 전부 유지 — 리스 `max(interval*3,30)`, `finalize_from_job` read-then-write(`requests.py:151-163`)·`record_result` 무조건 INSERT(`:119-127`), `41-controller.yaml:21` replicas 1, stepper 틱 `config.py:21` 5초 |
| 11. preview 만료가 대량 고아의 실경로 | ✓ 유지. `controller.py:37-41`(expire_previews 후 행별 finalize), `data_jobs.expire_previews:292-303` |
| 12. `storage_in_use`·`invalid_storage` 기등록 | ✓ 유지. `reasonCodes.json:21,28`, `api.ts:39,55` |

**추가 정정(설계 본문·과제 지시의 주변 사실):**
- §2.1 층2 각주의 어댑터 blanket except 는 지금 `execution_volcano.py:158-159`(설계 표기 156-157).
- §2.1 층3 의 `config.AGENT_TOOL_NAMES` 는 **`src/dms/config.py:7`** 이다 — `src/dms_job_runner/` 에 config.py 는 **존재하지 않는다**(러너 패키지는 commands/parsers/runner 3파일). 계약 테스트는 tests 층에서 `dms.config` 를 import 해 잇는다(테스트는 독립 패키지 규칙의 밖이다).
- §5 기준선 "1131 passed" → **실측 1166 passed**(슬라이스 21 후속 +22 반영). 프론트 228.
- 백로그 §2.1 은 지금 `docs/superpowers/BACKLOG.md:451-466`(설계 표기 378-383 — 슬라이스 21/22 완료 기록이 위에 쌓여 밀렸다).
- 이미지 빌드 실물은 **`deploy/docker/build-and-push.sh`**(pkg-01 에서 podman, Dockerfile.{mpifileutils,dms,agent}) — `install/docker/Dockerfile.testbed` 는 이 레포에 없는 레거시 관례다. 그리고 **이 슬라이스는 `dms` 이미지만이 아니라 잡 이미지(`dms-mpifileutils`)도 범프해야 한다** — 층3(러너)이 잡 이미지에 산다. 현행 `DMS_JOB_IMAGE` 는 `d27` 로 뒤처져 있다(`20-config.yaml:22` — 제어면 d34 와 태그가 원래 따로 논다).

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/execution_manifests.py` (수정) | Task 1: `tool_argv` drm 명시 분기 + 미지 도구 raise |
| `tests/test_execution_manifests.py`, `tests/test_execution_volcano.py` (수정) | Task 1: raise·불변·submit_failed fold 고정 |
| `src/dms/stepper.py` (수정) | Task 2: `_step_one` 층1 가드 + `_fail_closed`, `_build_spec` 관용 분기 제거. Task 5: `StorageMissingAtStep`·`_abs` join/fail-closed·`_dispatch` 분리 |
| `tests/test_stepper_fail_closed.py` (신규) | Task 2 + Task 5 의 종단 계약 전부(슬라이스 주제 응집) |
| `src/dms_job_runner/runner.py` (수정) | Task 3: `ALLOWED_TOOLS` + exec 전 거부 |
| `tests/test_job_runner_runner.py` (수정) | Task 3: 부작용 0 거부 + `AGENT_TOOL_NAMES` 계약 |
| `src/dms/repositories/storages.py` (수정) | Task 4: `_validate` "/" 명시 거부 |
| `src/dms/api/routes_storages.py` (수정) | Task 4: update 가드(경로·백엔드 변경 × 활성 요청 → 409) |
| `tests/test_repo_storages.py`, `tests/test_api_storages_inuse.py` (수정) | Task 4: 422/409/토글 통과 계약 |
| `tests/test_stepper_scan.py`·`test_stepper_sync.py`·`test_stepper_artifact_uri.py`·`test_timeout_enforcement.py`·`test_vcjob_ttl.py`·`test_controller_stepper.py`·`test_events_outside_transaction.py`·`test_artifact_base_resolve.py` (수정) | Task 5: 잡 팩토리에 storage 씨딩(fail-closed 의 대가, 실측 8파일) |
| `src/dms/repositories/data_jobs.py`, `src/dms/controller.py` (수정) | Task 6: 스윕 LIMIT/ORDER + 행 단위 격리 |
| `tests/test_recover_orphans.py` (수정) | Task 6: 201건 전진·독 행 격리·0건 무이벤트·오래된순 |
| `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts` (수정, Task 2/5 분할) | 신설 사유 2종 + 한국어 문구 |

---

### Task 0: 기준선 확인 (커밋 없음)

**Files:** 없음(검증만)

**Interfaces:** 없음 — 이후 모든 태스크의 판정 기준(기준선 초록)만 만든다.

- [ ] **Step 1: 백엔드 기준선**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: `1166 passed`

- [ ] **Step 2: 프론트 기준선**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: `Tests  228 passed`, tsc 무출력 exit 0.

---

### Task 1: 층2 — `tool_argv` 미지 도구 raise (fall-through 제거)

**Files:**
- Modify: `src/dms/execution_manifests.py`
- Modify: `tests/test_execution_manifests.py`, `tests/test_execution_volcano.py`

**Interfaces:**
- Consumes: `JobSpec`(무변경), 어댑터 submit 의 blanket except(`execution_volcano.py:158-159`) — 손대지 않는다.
- Produces: `tool_argv` — dscan/dsync/nsync/drm 4종은 **바이트 단위 무변경** 출력, 그 외는 `ValueError(f"unknown tool for argv: {spec.tool!r}")`. 어댑터 경유 시 `ExecutionError("submit_failed", detail 에 도구명)` 로 접힌다(층1이 앞서므로 정상 운영에선 도달하지 않는 회귀 신호 — 설계 §4).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_execution_manifests.py` — 파일 머리 import 를 다음으로 교체:

```python
import pytest

from dms.execution import JobSpec
from dms.execution_manifests import render_tool_flags, tool_argv, build_volcano_job, build_preflight_pod
```

파일 끝에 추가:

```python
# ---- 슬라이스 24 §2.1 층2: 미지 도구는 drm 꼴 argv 로 흘러가면 안 된다 ----

def test_unknown_tool_argv_raises_instead_of_falling_through_to_drm():
    # 현행 마지막 분기는 주석 `# drm` 짜리 fall-through 다 -- dscan/dsync/nsync 가
    # 아닌 **모든** 문자열이 맨몸 절대경로 argv(= drm 꼴)를 받는다(실측: "dwalk"
    # -> ['/cephfs/x']). 미래의 다섯째 도구를 placement 에 추가하고 여기를 잊으면
    # 그 도구가 경로 positional 을 삭제 대상으로 해석할 때 스캔 의도가 삭제가
    # 된다(설계 §2.1 시나리오 a). 층1(스테퍼)이 앞서 막지만, 순수 함수 스스로도
    # 조용히 틀리면 안 된다.
    with pytest.raises(ValueError) as e:
        tool_argv(_spec(operation="scan", tool="dwalk"),
                  abs_paths={"target": "/cephfs/x"})
    assert "dwalk" in str(e.value)   # 어댑터 submit_failed 의 detail 로 보존된다


def test_drm_dryrun_argv_is_unchanged_by_the_explicit_branch():
    # 명시 분기 치환의 무회귀 앵커(기존 test_rm_argv 는 dryrun=False 만 고정한다).
    spec = _spec(operation="rm", tool="drm", dryrun=True,
                 options={"recursive": True})
    assert tool_argv(spec, abs_paths={"target": "/cephfs/junk"}) == [
        "--dryrun", "/cephfs/junk"]
```

**(2)** `tests/test_execution_volcano.py` — 파일 끝에 추가:

```python
# ---- 슬라이스 24 §2.1 층2: raise 가 submit_failed 로 접히되 조용하지 않다 ----

def test_unknown_tool_submit_folds_into_submit_failed_with_the_tool_in_detail():
    # 층2 raise 는 어댑터 blanket except 가 submit_failed 로 접는다(설계 §4) --
    # 사유는 층1(unknown_tool)보다 거칠지만 detail 이 원인을 보존하고, 여기 도달
    # 자체가 층1이 뚫렸다는 회귀 신호다. k8s 에는 아무것도 만들지 않아야 한다.
    k8s = _FakeK8s()
    with pytest.raises(ExecutionError) as e:
        _adapter(k8s).submit(_spec(tool="dwalk"))
    assert e.value.reason_code == "submit_failed"
    assert "dwalk" in e.value.detail
    assert k8s.created == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_execution_manifests.py tests/test_execution_volcano.py -q`
Expected: FAIL 2건 / PASS 나머지 — `test_unknown_tool_argv_raises_...` 는 `DID NOT RAISE`(fall-through 가 `['/cephfs/x']` 를 돌려준다), `test_unknown_tool_submit_folds_...` 도 `DID NOT RAISE`(submit 이 성공해 vcjob 이 created 에 쌓인다). `test_drm_dryrun_argv_...` 는 현행 고정 가드라 **즉시 PASS 가 맞다**.

- [ ] **Step 3: tool_argv 를 고친다**

`src/dms/execution_manifests.py` — `tool_argv` 의 마지막 두 줄

```python
    # drm
    return [*flags, *dry, abs_paths["target"]]
```

을 다음으로 교체:

```python
    if spec.tool == "drm":
        return [*flags, *dry, abs_paths["target"]]
    # 슬라이스 24 §2.1 층2: 여기가 fall-through 였다 -- dscan/dsync/nsync 가 아닌
    # 모든 문자열이 drm 꼴 argv(맨몸 절대경로)를 받았다. 미지 도구는 argv 를
    # 지어내지 않고 던진다. 어댑터의 blanket except 가 submit_failed(detail=도구명)
    # 로 접으므로 조용히 사라지지 않고, 층1(stepper unknown_tool)이 앞서므로
    # 정상 운영에선 여기 도달 자체가 회귀 신호다(설계 §4).
    raise ValueError(f"unknown tool for argv: {spec.tool!r}")
```

- [ ] **Step 4: 통과를 확인한다 (정상 4종 무회귀 포함)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_execution_manifests.py tests/test_execution_volcano.py tests/test_stepper_scan.py tests/test_stepper_sync.py tests/test_timeout_enforcement.py -q`
Expected: 전부 PASS (`test_rm_argv`·`test_rm_flags`·scan/sync argv 계열이 층2 치환의 무회귀 안전망이다 — 설계 §5)

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

`raise ValueError(...)` 줄을 `return [*flags, *dry, abs_paths["target"]]` 로 되돌려(= fall-through 복원) Step 2 명령을 다시 돌린다 → `test_unknown_tool_argv_raises_...` 와 volcano fold 테스트가 **빨개져야 한다**. 확인 후 Step 3 상태로 원복하고 Step 4 를 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "feat(execution): tool_argv 미지 도구 raise — drm fall-through 제거(층2), 어댑터는 submit_failed(detail=도구명)로 표면화" -- src/dms/execution_manifests.py tests/test_execution_manifests.py tests/test_execution_volcano.py
```

---

### Task 2: 층1 — 스테퍼 `unknown_tool` 종단 + 관용 분기 제거 + 사유 등록

**Files:**
- Modify: `src/dms/stepper.py`
- Create: `tests/test_stepper_fail_closed.py`
- Modify: `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts`

**Interfaces:**
- Consumes: `TOOL_TO_POLICY`(placement — 이미 import 되어 있다), `_finalize`/`_reclaim_if_terminal` 의 terminate·이벤트 관례, `StubExecutionAdapter`.
- Produces (Task 5 가 같은 헬퍼를 재사용한다):
  - `JobStepper._fail_closed(job, *, reason_code) -> str` — Executing/Running 이면 FAILED, 그 외(Pending/Preflight/PreviewRunning)면 REJECTED 로 `_finalize`. `job["phase_refs"]` 전부를 best-effort terminate(실패는 기존 `terminate_failed` 이벤트 관례). 반환은 종단 상태 문자열.
  - `_step_one` 진입 가드: `job["tool"] not in TOOL_TO_POLICY` → `self._fail_closed(job, reason_code="unknown_tool")` (**키워드 리터럴** — AST 계약 추출 조건).
  - `_build_spec` 은 `TOOL_TO_POLICY[job["tool"]]` 직접 인덱싱(가드 뒤라 KeyError 불능). `get_policy` None(정책 행 삭제)의 "타임아웃 없음" 관용은 유지.
  - 사유 코드 `unknown_tool` — json+REASON_MESSAGES 양쪽.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_stepper_fail_closed.py` (신규 파일 전체):

```python
"""슬라이스 24: 신뢰 경계가 깨진 잡의 fail-closed 종단(설계 §2.1 층1, §2.4).

tool 의 유일한 정상 원천은 placement 의 리터럴 4종인데 create_job 은 무검증
INSERT 다(§1-1) -- 즉 DB 가 신뢰 경계고, 여기 실리는 미지 tool 은 "다섯째 도구
추가 실수" 아니면 "DB 직접 조작"이다. 스토리지도 마찬가지다: 요청 시점엔 있었고
스텝 시점에 없다면 행 삭제/직접 조작이다(§1-8, 컬럼은 NOT NULL). 어느 쪽이든
조용히 관용하면 파괴적 경로(drm 꼴 argv, cwd 기준 상대 삭제)로 흘러가므로
제출 전에 종단시키는 것이 이 파일의 계약이다. Task 2(unknown_tool)와
Task 5(storage_missing_at_step)가 이 파일을 나눠 채운다."""
from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


class _TerminateRecordingAdapter(StubExecutionAdapter):
    def __init__(self):
        super().__init__()
        self.terminated = []

    def terminate(self, ref):
        self.terminated.append(ref)
        super().terminate(ref)


def _seed_storage(repos, name):
    # 슬라이스 24: _abs 가 fail-closed 라(Task 5) 스텝 가능한 잡은 실제 storage
    # 행이 필요하다. 이 파일 자신도 그 규칙 위에서 산다.
    if repos.storages.get(name) is None:
        repos.storages.create(storage_name=name, mount_path=f"/{name}",
                              managed_root=f"/{name}/dms", backend_type="cephfs",
                              actor="test")


def _scan_job(repos, *, tool="dscan", storage="s1"):
    _seed_storage(repos, storage)
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key=f"k-{tool}", payload={"storage": storage, "target": "a"},
        priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name=storage, target="a", options={}, tool=tool,
        worker_pool={"tool": tool, "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


# ---- §2.1 층1: 미지 tool 은 제출 전에 종단된다 ----

def test_pending_unknown_tool_is_rejected_without_any_submission(db):
    # create_job 이 무검증이라 "dwalk" 가 그대로 실린다 -- 층1이 없으면 이 잡은
    # preflight 파드를 만들고, 층2 이전 코드라면 drm 꼴 argv 까지 받는다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos, tool="dwalk")
    adapter = StubExecutionAdapter()
    result = _stepper(repos, adapter).run_once()
    assert result[jid] == "Rejected"
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "unknown_tool"
    assert repos.requests.get(rid)["state"] == "Rejected"
    assert repos.requests.last_reason_code(rid) == "unknown_tool"
    assert adapter.submitted_specs() == []   # 파드/vcjob 미제출 -- 층1의 존재 이유


def test_running_job_with_mutated_tool_fails_and_reclaims_live_refs(db):
    # 실증 §6-3 의 단위 등가물: 정상 dscan 잡을 Running 까지 보낸 뒤 DB 에서
    # tool 을 변조한다. Executing/Running 은 실행 자원이 이미 붙어 있으므로
    # REJECTED 가 아니라 FAILED(execution_submit_failed 의 기존 대칭)이고,
    # 발급된 phase_refs 는 best-effort 회수돼야 클러스터 고아가 없다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = _TerminateRecordingAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight
    stepper.run_once()   # Preflight Succeeded → Running (execution 제출)
    assert repos.data_jobs.get_job(jid)["state"] == "Running"
    db.execute("UPDATE data_jobs SET tool = 'dwalk' WHERE job_id = :j", {"j": jid})
    result = stepper.run_once()
    assert result[jid] == "Failed"
    assert repos.data_jobs.get_job(jid)["state"] == "Failed"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "unknown_tool"
    assert repos.requests.get(rid)["state"] == "Failed"
    # preflight ref(이미 끝난 파드 -- 회수 무해)와 execution ref 둘 다 회수 시도.
    assert set(adapter.terminated) == {f"stub-preflight-{jid}",
                                       f"stub-execution-{jid}"}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_stepper_fail_closed.py tests/test_reason_codes_coverage.py -q`
Expected: 신규 2건 FAIL — `test_pending_unknown_tool_...` 는 `assert result[jid] == "Rejected"` 에서 실제 `"Preflight"`(현행은 미지 tool 도 그대로 제출한다), `test_running_job_with_mutated_tool_...` 는 `assert result[jid] == "Failed"` 에서 실제 `"Succeeded"`(스텁 poll 기본값이 SUCCEEDED 라 정상 완료로 위장된다 — 이것이 바로 "조용히 틀리는" 모양이다). `test_reason_codes_coverage.py` 는 아직 PASS(리터럴이 코드에 없으므로).

- [ ] **Step 3: stepper.py 를 고친다**

**(1)** `_step_one` 을 다음으로 교체(`def _step_one(self, job) -> str:` 부터 `return state` 까지):

```python
    # FAILED 로 갈리는 상태: 실행 자원이 이미 붙었다(execution vcjob 제출 이후).
    # 그 전 단계는 REJECTED -- preflight_submit_failed→REJECTED /
    # execution_submit_failed→FAILED 의 기존 대칭을 그대로 따른다(설계 §2.1).
    _EXEC_STATES = (DataJobState.EXECUTING.value, DataJobState.RUNNING.value)

    def _fail_closed(self, job, *, reason_code):
        """신뢰 경계가 깨진 잡(미지 tool·스텝 시점 스토리지 결측)의 종단 처리.

        살아 있을 수 있는 phase_refs 는 _reclaim_if_terminal 관례대로 best-effort
        terminate 하고, 실패는 terminate_failed 이벤트로 남긴다 -- 고아 리소스를
        조용히 두지 않는다(설계 §4). 이미 끝난 파드의 terminate 는 무해하다."""
        target = (DataJobState.FAILED if job["state"] in self._EXEC_STATES
                  else DataJobState.REJECTED)
        for ref in (job["phase_refs"] or {}).values():
            try:
                self._exec.terminate(ref)
            except ExecutionError as exc:
                self._repos.observability.record_event(
                    component="stepper", severity="warning",
                    event_type="terminate_failed", message=exc.reason_code,
                    payload={"ref": ref}, request_id=job.get("request_id"))
        self._finalize(job, target, reason_code=reason_code)
        return target.value

    def _step_one(self, job) -> str:
        # 슬라이스 24 §2.1 층1: tool 의 유일한 정상 원천은 placement 의 리터럴
        # 4종이고 create_job 은 무검증 INSERT 다(§1-1) -- DB 가 신뢰 경계다.
        # 미지 tool 이 층2 이전의 fall-through 를 타면 drm 꼴 argv(파괴적)로
        # 실행되므로 제출 전에 종단시킨다. 이 가드로 _build_spec 의 "미지 tool ->
        # 타임아웃 없음" 관용 분기는 도달 불능이 되어 제거했다 -- 그 주석이
        # 걱정한 "매 틱 예외로 영구히 낀 잡"은 종단이라 애초에 생기지 않는다.
        if job["tool"] not in TOOL_TO_POLICY:
            return self._fail_closed(job, reason_code="unknown_tool")
        state = job["state"]
        if state == DataJobState.PENDING.value:
            return self._submit_preflight(job)
        if state == DataJobState.PREFLIGHT.value:
            return self._poll_preflight(job)
        if state == DataJobState.RUNNING.value:
            return self._poll_execution(job)
        if state == DataJobState.PREVIEW_RUNNING.value:
            return self._poll_preview(job)
        if state == DataJobState.EXECUTING.value:
            return self._poll_or_submit_execution(job)
        return state
```

(원본에 있던 지역변수 `jid = job["job_id"]` 는 사용처가 없으므로 옮기지 않는다.)

**(2)** `_build_spec` 의 정책 조회 블록(주석 4줄 + `policy_key = ...` + `policy = (...)` 5줄, 현행 70-77행)을 다음으로 교체:

```python
        # job["tool"]은 실행 파일 이름(dscan/dsync/nsync/drm)이지 정책 키(scan/dsync/
        # nsync/rm)가 아니다 -- planner.py가 policy를 조회할 때 쓰는 것과 동일한
        # TOOL_TO_POLICY 매핑을 거쳐야 scan/rm 잡의 정책을 정확히 찾는다.
        # 미지 tool 은 _step_one 층1 가드(슬라이스 24)가 이미 종단시켰으므로 여기서
        # 직접 인덱싱해도 KeyError 불능이다. policy None 은 이제 "정책 행이 지워진"
        # 운영 조작뿐이라 크래시 대신 타임아웃 없음으로 관용한다(기존 동작 유지).
        policy = self._repos.control.get_policy(TOOL_TO_POLICY[job["tool"]])
```

- [ ] **Step 4: 사유 코드를 양쪽에 등록한다 (같은 커밋 — 계약 조건)**

**(1)** `frontend/src/lib/reasonCodes.json` — `"preview_submit_failed", "execution_recheck_submit_failed",` 줄 **바로 아래**에 새 줄 추가(재포맷 금지, 이 한 줄만):

```json
  "unknown_tool",
```

**(2)** `frontend/src/lib/api.ts` — `invalid_operation: "지원하지 않는 연산입니다",` 줄 **바로 아래**에 추가:

```ts
  // 슬라이스 24 파괴적 경로 fail-closed. reasonCodes.json 과 같은 커밋 -- 양방향
  // 계약(reasonCodes.test.ts / test_reason_codes_coverage.py) 조건이다.
  unknown_tool: "허용되지 않은 도구입니다 — 관리자에게 문의하세요",
```

- [ ] **Step 5: 통과를 확인한다 (스테퍼 광역 회귀 + 계약 양방향)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_stepper_fail_closed.py tests/test_stepper_scan.py tests/test_stepper_sync.py tests/test_stepper_enrich.py tests/test_controller_stepper.py tests/test_timeout_enforcement.py tests/test_reason_codes_coverage.py -q`
Expected: 전부 PASS (4종 도구는 가드를 무변경 통과 — 기존 상태기계 테스트가 안전망).
Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run src/lib/reasonCodes.test.ts`
Expected: PASS (json ⊆ REASON_MESSAGES, 죽은 키 없음).

- [ ] **Step 6: 뮤테이션으로 이빨 확인 후 원복**

`_step_one` 의 가드 두 줄(`if job["tool"] not in TOOL_TO_POLICY:` 블록)을 삭제하고 Step 2 명령을 재실행 → `test_pending_unknown_tool_...` 이 `"Preflight"` 로, `test_running_job_with_mutated_tool_...` 이 `"Succeeded"` 로 **빨개져야 한다**(층2 raise 는 stepper 를 안 거치는 스텁 어댑터라 발화하지 않는다 — 층1의 이빨이 따로 증명된다). 원복 후 Step 5 재확인.

- [ ] **Step 7: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git add tests/test_stepper_fail_closed.py
git commit -m "feat(stepper): 미지 tool fail-closed 종단(unknown_tool, 층1) — 제출 전 차단·ref 회수·관용 분기 제거" -- src/dms/stepper.py tests/test_stepper_fail_closed.py frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts
```

---

### Task 3: 층3 — 러너 allowlist (exec 없는 거부)

**Files:**
- Modify: `src/dms_job_runner/runner.py`
- Modify: `tests/test_job_runner_runner.py`

**Interfaces:**
- Consumes: `run_job` 의 주입식 I/O(`run`/`write_text`/…), summary 3키 계약(`_build_summary`), `dms.config.AGENT_TOOL_NAMES == ("dscan","dsync","nsync","drm")`(`src/dms/config.py:7`).
- Produces:
  - `dms_job_runner.runner.ALLOWED_TOOLS = ("dscan", "dsync", "nsync", "drm")` — 모듈 상수. `dms` 를 import 하지 않는 독립 패키지라 **중복 정의**하고 동일성은 저장소 테스트가 강제한다.
  - `run_job`: `tool not in ALLOWED_TOOLS` 이면 **어떤 부작용(passwd 물질화·ssh 복사·명령 실행) 전에** stderr 마커 `DMS_JR_UNKNOWN_TOOL` + `summary.json {"returncode": 1, "files": null, "bytes": null}` + `return 1`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_job_runner_runner.py` — 파일 끝에 추가:

```python
# ---- 슬라이스 24 §2.1 층3: allowlist 밖 tool 은 exec 없이 거부 ----

def test_unknown_tool_is_refused_before_any_side_effect(capsys):
    # rank.sh 는 `exec {tool} {argv}` 다 -- 명령 이름 자체가 tool 값이라(§1-3)
    # DB 에 "sh" 를 쓸 수 있는 자는 사용자 통제 파일을 워커 노드에서 요청자
    # 신원의 스크립트로 실행시킬 수 있다. 층1·2 는 제어면의 방어고, 이 층만이
    # "이미 제출된 매니페스트/env 의 사후 변조"까지 막는다 -- 그래서 부작용
    # (passwd append, ssh 복사, chown, mpirun)이 하나도 시작되기 전에 끊어야 한다.
    rec = _Recorder()
    rc = _run(rec, _env(DMS_JR_TOOL="sh", DMS_JR_ARGV=json.dumps(["/etc"])))
    assert rc != 0
    assert rec.ran == []        # mpirun 은 물론 어떤 명령도 안 돌았다
    assert rec.appends == []    # /etc/passwd 물질화도 없다
    # summary 는 3키 계약 유지(설계 §4) -- 모름(files/bytes)은 null 이지 0 이 아니다.
    assert _summary(rec) == {"returncode": 1, "files": None, "bytes": None}
    # 층3 발동은 층1·2 가 뚫렸다는 조사 신호 -- grep 가능한 마커로 남긴다.
    assert "DMS_JR_UNKNOWN_TOOL" in capsys.readouterr().err


def test_runner_allowlist_matches_the_control_plane_tool_names():
    # dms_job_runner 는 dms 를 import 하지 않는 독립 패키지라 튜플을 중복 정의한다
    # (설계 §2.1 층3). 두 값이 갈라지면 다섯째 도구를 추가했을 때 "제어면은
    # 아는데 러너가 거부"하는 조용한 실패가 된다 -- 이 저장소 테스트만이 둘을
    # 잇는다(테스트 층은 독립 패키지 규칙의 밖이다).
    from dms.config import AGENT_TOOL_NAMES
    from dms_job_runner.runner import ALLOWED_TOOLS
    assert ALLOWED_TOOLS == AGENT_TOOL_NAMES
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_job_runner_runner.py -q`
Expected: 신규 2건 FAIL — `test_unknown_tool_is_refused_...` 는 `assert rc != 0` 에서 실패(레코더 기본 rc=0 으로 "sh" 가 끝까지 돈다 — mpirun 명령에 tool 이 실려 있다), `test_runner_allowlist_matches_...` 는 `ImportError: cannot import name 'ALLOWED_TOOLS'`.

- [ ] **Step 3: runner.py 를 고친다**

**(1)** `_SSH_READY_MAX_ATTEMPTS = 90` 줄 **바로 아래**에 추가:

```python
# 슬라이스 24 §2.1 층3: 러너가 exec 할 수 있는 도구의 최종 allowlist.
# dms 패키지의 config.AGENT_TOOL_NAMES 와 같은 값이어야 하지만 dms_job_runner 는
# dms 를 import 하지 않는 독립 패키지(잡 이미지에 단독 설치)라 여기 중복 정의한다
# -- 동일성은 tests/test_job_runner_runner.py 의 계약 테스트가 강제한다.
# rank.sh 가 `exec {tool}` 이라 명령 이름 자체가 tool 값이다: 층1(스테퍼)·층2
# (tool_argv)가 뚫려도 -- 이미 제출된 매니페스트/env 의 사후 변조까지 포함해 --
# 이 층만은 실행을 막는다.
ALLOWED_TOOLS = ("dscan", "dsync", "nsync", "drm")
```

**(2)** `run_job` 안, `tool = env["DMS_JR_TOOL"]` 줄과 `# 1. identity 물질화` 주석 **사이**에 추가:

```python
    if tool not in ALLOWED_TOOLS:
        # exec 은 물론 어떤 부작용(passwd 물질화·ssh 키 복사·chown)도 시작하기
        # 전에 끊는다. 마커는 grep 가능한 한 단어 -- 층3 발동 자체가 층1·2 가
        # 뚫렸다는 조사 신호다(설계 §4). summary 는 3키 계약 유지: 모름은 null.
        print(f"DMS_JR_UNKNOWN_TOOL tool={tool!r} allowed={ALLOWED_TOOLS}",
              file=sys.stderr)
        write_text(f"{artifact_dir}/summary.json",
                   json.dumps({"returncode": 1, "files": None, "bytes": None}))
        return 1
```

- [ ] **Step 4: 통과를 확인한다 (4도구 정상 실행 무회귀 포함)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_job_runner_runner.py tests/test_job_runner_commands.py tests/test_job_runner_parsers.py tests/test_config_phase2.py -q`
Expected: 전부 PASS (기존 dscan/dsync/nsync/drm 실행·summary 테스트가 층3 무회귀의 안전망이다 — 설계 §5)

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

(a) `ALLOWED_TOOLS` 에 `"sh"` 를 추가 → `test_runner_allowlist_matches_...` 가 빨개진다(계약이 값 자체를 물고 있다). (b) 가드의 `if tool not in ALLOWED_TOOLS:` 를 `if False:` 로 → `test_unknown_tool_is_refused_...` 가 빨개진다. 각각 확인 후 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "feat(job-runner): 도구 allowlist(층3) — exec·부작용 전 거부, AGENT_TOOL_NAMES 동일성 계약" -- src/dms_job_runner/runner.py tests/test_job_runner_runner.py
```

---

### Task 4: 스토리지 — `"/"` 명시 거부 + update 가드

**Files:**
- Modify: `src/dms/repositories/storages.py`, `src/dms/api/routes_storages.py`
- Modify: `tests/test_repo_storages.py`, `tests/test_api_storages_inuse.py`

**Interfaces:**
- Consumes: `DomainValidationError`, `requests.active_referencing_storage`(`requests.py:106-117` — delete 가드가 이미 쓰는 그 함수), 기존 `invalid_storage`/`storage_in_use` 사유(기등록 — 계약 테스트 무추가).
- Produces:
  - `_validate`: mount_path·managed_root 가 `"/"` 면 `DomainValidationError("invalid_storage", "root filesystem is not a storage")`. 기존 경로 규칙의 `and p != "/"` 예외 절은 사문이 되어 제거.
  - `PUT /api/admin/storages/{name}`: mount_path/managed_root/backend_type 이 저장값과 다르고(`posixpath.normpath` 비교 — 후행 슬래시 오탐 방지) `active_referencing_storage` 면 409 `storage_in_use`. **enabled 토글은 가드 없이 통과**(진행 중 잡의 비상 차단 경로). 404 판정은 가드보다 먼저.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_repo_storages.py` — `@pytest.mark.parametrize("bad", [...]` 목록에 2행 추가(기존 4행 뒤):

```python
    {"mount_path": "/", "managed_root": "/"},  # 노드 루트(슬라이스 24 §2.2) -- 현행 ACCEPTED
    {"managed_root": "/"},                     # root "/"만 -- 기존에도 mount 밖 규칙으로 거부(명시 고정)
```

**(2)** `tests/test_api_storages_inuse.py` — 파일 끝에 추가:

```python
# ---- 슬라이스 24 §2.2/§2.4: "/" 거부 + update 가드 ----

def test_create_root_filesystem_storage_is_rejected(client):
    # {mount "/", root "/"} 가 통과하면: 에이전트 statvfs("/")는 어느 노드에서나
    # 성공해 Ready, rm target "etc" 가 검증 통과(validate_rm_target 은 ""/"." 만
    # 거부), 잡 파드는 노드 루트를 hostPath 마운트한 채 drm 을 요청자 신원으로
    # 실행한다(설계 §2.2 시나리오). 등록 자체를 막는 것이 1차 방어다.
    _admin(client)
    r = client.post("/api/admin/storages", json={
        "storage_name": "rootfs", "mount_path": "/", "managed_root": "/",
        "backend_type": "cephfs"})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_storage"


def _active_request_on(client, storage):
    client.app.state.repos.requests.create(operation="scan", requester_id="admin",
        actor="admin", resource_key=f"k-{storage}",
        payload={"storage": storage, "target": "a", "options": {}}, priority="mid")


def test_update_path_change_blocked_while_referenced(client):
    # preview 에서 사용자가 확인한 경로와 execution 이 실제 도는 경로가 갈라지는
    # TOCTOU(확인 게이트 우회, 설계 §2.4) -- delete 가드와 대칭인 409.
    _admin(client); _seed_storage(client)
    _active_request_on(client, "s1")
    r = client.put("/api/admin/storages/s1", json={
        "mount_path": "/s1", "managed_root": "/s1/elsewhere",
        "backend_type": "cephfs", "enabled": True})
    assert r.status_code == 409 and r.json()["detail"] == "storage_in_use"


def test_update_enabled_toggle_allowed_while_referenced(client):
    # 비상 차단(비활성화)은 진행 중 잡이 있어도 돼야 한다(설계 §2.4) -- 가드가
    # "경로·백엔드 변경"에만 걸린다는 계약.
    _admin(client); _seed_storage(client)
    _active_request_on(client, "s1")
    r = client.put("/api/admin/storages/s1", json={
        "mount_path": "/s1", "managed_root": "/s1/dms",
        "backend_type": "cephfs", "enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] == 0


def test_update_trailing_slash_only_is_not_a_change(client):
    # 저장값은 normpath 정규화돼 있다 -- 후행 슬래시만 다른 PUT 이 "변경"으로
    # 오탐되면 enabled 토글 같은 무해 요청까지 409 가 된다. 비교도 같은 정규화로.
    _admin(client); _seed_storage(client)
    _active_request_on(client, "s1")
    r = client.put("/api/admin/storages/s1", json={
        "mount_path": "/s1/", "managed_root": "/s1/dms/",
        "backend_type": "cephfs", "enabled": True})
    assert r.status_code == 200


def test_update_path_change_allowed_when_not_referenced(client):
    _admin(client); _seed_storage(client)
    r = client.put("/api/admin/storages/s1", json={
        "mount_path": "/s1", "managed_root": "/s1/elsewhere",
        "backend_type": "cephfs", "enabled": True})
    assert r.status_code == 200 and r.json()["managed_root"] == "/s1/elsewhere"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_storages.py tests/test_api_storages_inuse.py -q`
Expected: FAIL 3건 / PASS 나머지 — repo 파라미터 `{"mount_path": "/", "managed_root": "/"}` 가 `DID NOT RAISE`(실측 ACCEPTED), `test_create_root_filesystem_...` 가 `assert 201 == 422`, `test_update_path_change_blocked_...` 가 `assert 200 == 409`. `{"managed_root": "/"}` 파라미터·토글/후행슬래시/비참조 3건은 현행 고정 가드라 **즉시 PASS 가 맞다**.

- [ ] **Step 3: `_validate` 를 고친다**

`src/dms/repositories/storages.py` — `_validate` 의 경로 루프(현행 15-17행)를 다음으로 교체:

```python
    for p in (mount_path, managed_root):
        if p == "/":
            # 슬라이스 24 §2.2: CephFS/GPFS/WekaFS 는 노드 루트에 마운트되지
            # 않는다 -- "/" 가 정당한 배포는 없다. 이 값이 살면 mount 검사가 어느
            # 노드에서나 statvfs("/") 로 Ready 가 되고, 잡 파드는 노드 루트를
            # hostPath 로 통째로 마운트하며(_volumes 의 조상-커버 축약이 전부를
            # "/" 하나로 접는다), rm 대상 검증(""/"."만 거부)을 통과한 "etc" 류가
            # 요청자 신원으로 지워진다. 검증은 create/update 에만 발화하므로 이미
            # DB 에 있는 "/" 행은 stepper._abs 의 join 이 2차 방어다(같은 슬라이스).
            raise DomainValidationError("invalid_storage",
                                        "root filesystem is not a storage")
        # 기존 규칙에서 `and p != "/"` 예외 절을 제거했다 -- 그 절의 유일한 존재
        # 이유가 "/" 를 살리는 것이었고, 이제 위에서 명시 거부한다.
        if not p.startswith("/") or posixpath.normpath(p) != p.rstrip("/"):
            raise DomainValidationError("invalid_storage", f"bad path {p!r}")
```

- [ ] **Step 4: update 가드를 만든다**

`src/dms/api/routes_storages.py` — **(1)** 파일 머리에 `import posixpath` 추가(첫 줄). **(2)** `update_storage` 를 다음으로 교체:

```python
@router.put("/api/admin/storages/{name}")
def update_storage(name: str, body: StorageUpdate, request: Request,
                   identity: Identity = Depends(require_admin)):
    repos = request.app.state.repos
    current = repos.storages.get(name)
    if current is None:
        raise HTTPException(status_code=404, detail="storage_not_found")
    # 슬라이스 24 §2.4: 진행 중 잡이 참조하는 스토리지의 경로·백엔드 변경을 막는다
    # -- preview 에서 확인한 경로와 execution 이 도는 경로가 갈라지는 TOCTOU(확인
    # 게이트 우회)의 봉인이다. enabled 토글은 가드 없이 통과: 진행 중 잡의 비상
    # 차단(비활성화) 경로를 막으면 안 된다. 비교는 저장값과 같은 normpath 정규화
    # (후행 슬래시만 다른 PUT 의 409 오탐 방지). delete 가드와 같은 요청 레벨
    # check-then-act 라 원자적이지 않다 -- 잔여 창은 stepper._abs fail-closed 가
    # 최종 방어이고, 이 가드는 창을 좁힐 뿐 없애지 못한다(설계 §2.4 정직한 한계).
    changed = (posixpath.normpath(body.mount_path) != current["mount_path"]
               or posixpath.normpath(body.managed_root) != current["managed_root"]
               or body.backend_type != current["backend_type"])
    if changed and repos.requests.active_referencing_storage(name):
        raise HTTPException(status_code=409, detail="storage_in_use")
    try:
        return repos.storages.update(
            name, mount_path=body.mount_path, managed_root=body.managed_root,
            backend_type=body.backend_type, enabled=body.enabled,
            actor=audit_actor(identity))
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    except KeyError:
        raise HTTPException(status_code=404, detail="storage_not_found")
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_storages.py tests/test_api_storages_inuse.py tests/test_api_storages.py tests/test_api_user_storages.py tests/test_reconciler.py -q`
Expected: 전부 PASS (`test_api_storages.py` 의 기존 update/delete 흐름과 정규화 저장 테스트가 무회귀 안전망 — PUT 에 활성 요청이 없는 기존 시나리오는 가드를 통과한다)

- [ ] **Step 6: 뮤테이션으로 이빨 확인 후 원복**

(a) `_validate` 의 `if p == "/":` 블록 삭제 → repo 파라미터 케이스와 `test_create_root_filesystem_...` 이 빨개진다. (b) 라우트의 `changed and` 를 지워 `if repos.requests.active_referencing_storage(name):` 로 → `test_update_enabled_toggle_...` 과 `test_update_trailing_slash_...` 가 409 로 빨개진다(가드가 "변경"에만 걸린다는 계약의 이빨). 각각 확인 후 원복, Step 5 재확인.

- [ ] **Step 7: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "feat(storages): 노드 루트(/) 등록 거부 + update 경로·백엔드 변경 가드(storage_in_use) — enabled 토글은 통과" -- src/dms/repositories/storages.py src/dms/api/routes_storages.py tests/test_repo_storages.py tests/test_api_storages_inuse.py
```

---

### Task 5: `_abs` — posixpath.join + 결측 fail-closed(`storage_missing_at_step`)

**Files:**
- Modify: `src/dms/stepper.py`
- Modify: `tests/test_stepper_fail_closed.py`
- Modify(팩토리 씨딩): `tests/test_stepper_scan.py`, `tests/test_stepper_sync.py`, `tests/test_stepper_artifact_uri.py`, `tests/test_timeout_enforcement.py`, `tests/test_vcjob_ttl.py`, `tests/test_controller_stepper.py`, `tests/test_events_outside_transaction.py`, `tests/test_artifact_base_resolve.py`
- Modify: `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts`

**Interfaces:**
- Consumes: Task 2 의 `_fail_closed`, `record_event` 관례, `test_stepper_enrich.py:41`(정상 root 의 절대경로 앵커 — join 치환 후에도 출력 동일).
- Produces:
  - `stepper.StorageMissingAtStep(storage_name)` — 모듈 레벨 예외. `_abs` 가 storage 행 없음/managed_root 빈 값에 던진다(폴백 삭제 — "로그를 안 남긴다"는 백로그 항목 4 가 구조적으로 소멸).
  - `_abs`: `posixpath.join(managed_root, rel)` — 정상 root 출력 동일, 레거시 `"/"` 행에서 `//` 미생성.
  - `_step_one`: 상태 디스패치를 `_dispatch` 로 분리하고 `StorageMissingAtStep` 를 받아 `record_event(event_type="storage_missing_at_step")` + `_fail_closed(job, reason_code="storage_missing_at_step")`.
  - 사유 코드 `storage_missing_at_step` — json+REASON_MESSAGES 양쪽.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_stepper_fail_closed.py` — 파일 끝에 추가:

```python
# ---- §2.4: 스텝 시점 스토리지 결측은 조용한 폴백이 아니라 종단이다 ----

def _sync_job(repos):
    _seed_storage(repos, "src")
    _seed_storage(repos, "dst")
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="k-sync", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync",
        worker_pool={"tool": "dsync", "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def test_missing_storage_terminates_pending_job_instead_of_retry_looping(db):
    # 현행 폴백은 상대경로를 조용히 돌려준다 -- dsync 목적지가 상대로 남으면
    # 도구는 launcher cwd 기준 컨테이너 오버레이에 복사하고 SUCCEEDED 로 끝난다:
    # 데이터는 파드와 함께 증발하는데 사용자는 "성공한 sync" 를 믿는다(설계 §2.4).
    # fail-closed 는 종단 + 이벤트다. run_once 의 step_error(예외 루프 -- 매 틱
    # 재시도로 영구히 낀다) 경로와도 구분돼야 한다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    repos.storages.delete("s1", actor="test")   # 리포지토리 직접 호출 = 라우트 가드 우회 경로
    adapter = StubExecutionAdapter()
    result = _stepper(repos, adapter).run_once()
    assert result[jid] == "Rejected"            # error:... 가 아니다 -- 종단이다
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert (repos.data_jobs.job_transitions(jid)[-1]["reason_code"]
            == "storage_missing_at_step")
    assert adapter.submitted_specs() == []      # 어떤 파드도 만들지 않았다
    kinds = [e["event_type"] for e in repos.observability.events_for_request(rid)]
    assert kinds == ["storage_missing_at_step"]  # step_error 가 아니라 전용 이벤트
    events = repos.observability.events_for_request(rid)
    assert "s1" in events[0]["message"]          # "어느 스토리지"가 이벤트에 남는다


def test_missing_storage_mid_flight_fails_executing_job_and_reclaims_refs(db):
    # confirm 뒤(Executing) 목적지 스토리지가 사라진 경우: 실행 자원이 붙은
    # 상태라 FAILED 갈림이고, 발급돼 있던 preflight/preview ref 는 회수된다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = _TerminateRecordingAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3, "bytes": 9})
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight
    stepper.run_once()   # Preflight ok → PreviewRunning
    stepper.run_once()   # Preview ok → ConfirmPending
    assert repos.data_jobs.get_job(jid)["state"] == "ConfirmPending"
    # confirm 게이트 통과를 최소 재현(라우트 없이): Executing 으로 직접 전이.
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    repos.storages.delete("dst", actor="test")
    result = stepper.run_once()                  # exec_preflight 제출 시도 → 결측
    assert result[jid] == "Failed"
    assert repos.data_jobs.get_job(jid)["state"] == "Failed"
    assert (repos.data_jobs.job_transitions(jid)[-1]["reason_code"]
            == "storage_missing_at_step")
    assert set(adapter.terminated) == {f"stub-preflight-{jid}",
                                       f"stub-preview-{jid}"}
    events = [e for e in repos.observability.events_for_request(rid)
              if e["event_type"] == "storage_missing_at_step"]
    assert len(events) == 1 and "dst" in events[0]["message"]


def test_legacy_root_slash_row_joins_without_double_slash(db):
    # 검증(§2.2)은 create/update 에만 발화한다 -- 그 이전에 DB 에 남은 root "/"
    # 행은 _abs 가 2차 방어로 흡수해야 한다. f-string 결합은 "//team/data" 를
    # 만들고 POSIX 는 "//" 를 구현 정의로 둔다. normpath 후처리는 "//x" 를
    # **보존**해서(실측) 대안이 못 된다 -- posixpath.join 만이 "/team/data" 를 준다.
    repos = Repositories(db)
    db.execute(
        """INSERT INTO storages (storage_name, mount_path, managed_root,
               backend_type, enabled, status, created_at, updated_at, updated_by)
           VALUES ('rootfs', '/', '/', 'cephfs', 1, 'Ready',
                   '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'legacy')""")
    rid, jid = _scan_job(repos, storage="rootfs")
    adapter = StubExecutionAdapter()
    _stepper(repos, adapter).run_once()
    target = adapter.submitted_specs()[0].paths["target"]
    assert target == "/a"
    assert not target.startswith("//")
```

(`_scan_job` 의 `_seed_storage` 는 `rootfs` 가 이미 직접 INSERT 로 존재하므로 `get` 가드에 걸려 no-op 이다 — 팩토리 수정 불요.)

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_stepper_fail_closed.py -q`
Expected: 신규 3건 FAIL — `test_missing_storage_terminates_pending_...` 는 `assert result[jid] == "Rejected"` 에서 실제 `"Preflight"`(폴백이 상대경로 "a" 로 제출을 그대로 통과시킨다), `test_missing_storage_mid_flight_...` 는 `assert result[jid] == "Failed"` 에서 실제 `"Executing"`, `test_legacy_root_slash_...` 는 `assert target == "/a"` 에서 실제 `"//a"`(f-string 결합).

- [ ] **Step 3: stepper.py 를 고친다**

**(1)** 파일 머리 import 블록에 `import posixpath` 추가(`import json` 다음 줄).

**(2)** `class JobStepper:` **위**(모듈 레벨, `_summary_fingerprint` 아래)에 추가:

```python
class StorageMissingAtStep(Exception):
    """_abs 가 storage 행/managed_root 를 찾지 못했다 -- 요청 시점엔 있었는데
    스텝 시점에 없다는 뜻이다(행 삭제 또는 직접 DB 조작; 라우트 update 는 가드가
    막는다 -- 슬라이스 24 §2.4). 예전 폴백(상대경로 반환, 로그 0건)은 dsync 를
    launcher cwd 기준 컨테이너 오버레이에 쓰고 SUCCEEDED 로 끝내는 조용한 데이터
    증발이었고 drm 이면 cwd 기준 상대 삭제였다 -- 예외로 끊고 종단시킨다."""

    def __init__(self, storage_name):
        self.storage_name = storage_name
        super().__init__(f"storage {storage_name!r} missing at step time")
```

**(3)** `_abs` 를 다음으로 교체:

```python
    def _abs(self, storage_name, rel):
        storage = self._repos.storages.get(storage_name)
        root = (storage or {}).get("managed_root")
        if root is None or root == "":
            # 컬럼이 NOT NULL(migrations.py:209)이라 여기 도달은 사실상 "행
            # 삭제"와 직접 DB 조작뿐이다(설계 §1-8). 폴백 금지 -- fail-closed.
            raise StorageMissingAtStep(storage_name)
        # f-string 결합이 아니라 join(설계 §2.2): 검증 이전에 DB 에 남아 있을 수
        # 있는 root "/" 행에서 f"{root}/{rel}" 은 "//rel" 을 만들고 POSIX 는
        # "//" 를 구현 정의로 취급한다(문자열 비교 계열 -- 감사 로그·아티팩트
        # 표시 -- 와도 어긋난다). normpath 후처리는 "//x" 를 보존해서(실측)
        # 대안이 못 된다. 정상 root 에선 출력이 동일하다(test_stepper_enrich 앵커).
        return posixpath.join(root, rel)
```

**(4)** Task 2 가 만든 `_step_one` 의 상태 디스패치(`state = job["state"]` 부터 `return state` 까지)를 `_dispatch` 로 분리하고, `_step_one` 을 다음 모양으로 만든다(층1 가드·주석은 Task 2 그대로):

```python
    def _step_one(self, job) -> str:
        # (Task 2 의 층1 가드 주석 그대로)
        if job["tool"] not in TOOL_TO_POLICY:
            return self._fail_closed(job, reason_code="unknown_tool")
        try:
            return self._dispatch(job)
        except StorageMissingAtStep as exc:
            # 종단 전이의 reason_code 만으론 "어느 스토리지가 없었는지"가 남지
            # 않는다 -- 이벤트로 보강한다(설계 §2.4). run_once 의 step_error
            # (매 틱 재시도 루프)와 달리 여기는 종단이라 한 번만 남는다.
            self._repos.observability.record_event(
                component="stepper", severity="error",
                event_type="storage_missing_at_step",
                message=f"storage={exc.storage_name} job={job['job_id']}",
                request_id=job.get("request_id"))
            return self._fail_closed(job, reason_code="storage_missing_at_step")

    def _dispatch(self, job) -> str:
        state = job["state"]
        if state == DataJobState.PENDING.value:
            return self._submit_preflight(job)
        if state == DataJobState.PREFLIGHT.value:
            return self._poll_preflight(job)
        if state == DataJobState.RUNNING.value:
            return self._poll_execution(job)
        if state == DataJobState.PREVIEW_RUNNING.value:
            return self._poll_preview(job)
        if state == DataJobState.EXECUTING.value:
            return self._poll_or_submit_execution(job)
        return state
```

- [ ] **Step 4: 스테퍼를 실제로 돌리는 기존 8파일의 잡 팩토리에 storage 를 씨딩한다**

폴백 삭제의 대가다: 이 8파일은 storage 행 없이 잡을 만들고 스테퍼를 돌린다(실측 — `create_job` 은 있는데 `storages.create` 가 없고 `JobStepper`/`run_all_once` 를 부르는 파일의 전수 목록). 각 파일의 잡 팩토리(또는 인라인 생성부) **맨 앞**에 다음 관례(파일 간 import 결합 금지 — 파일마다 복제)를 넣는다. 경로를 단언하는 테스트는 8파일에 없음을 실측했다(제출 경로가 "a" → "/s1/dms/a" 로 바뀌어도 깨질 단언 없음).

```python
def _seed_storage(repos, name):
    # 슬라이스 24: _abs 의 결측 폴백(상대경로 반환)이 fail-closed 로 바뀌어
    # (stepper.StorageMissingAtStep) 스텝 가능한 잡은 실제 storage 행이 필요하다.
    if repos.storages.get(name) is None:
        repos.storages.create(storage_name=name, mount_path=f"/{name}",
                              managed_root=f"/{name}/dms", backend_type="cephfs",
                              actor="test")
```

적용 지점(전부 실측 라인):
1. `tests/test_stepper_scan.py` — `_scan_job` 머리에 `_seed_storage(repos, "s1")`.
2. `tests/test_stepper_sync.py` — `_sync_job` 머리에 `_seed_storage(repos, "src")`·`_seed_storage(repos, "dst")`.
3. `tests/test_stepper_artifact_uri.py` — `_scan_job`(:26)에 "s1", `_sync_job`(:42)에 "src"/"dst".
4. `tests/test_timeout_enforcement.py` — `_dsync_job`(:29)에 "src"/"dst", `_scan_job`(:47)에 "s1".
5. `tests/test_vcjob_ttl.py` — `_scan_job`(:53)에 "s1".
6. `tests/test_controller_stepper.py` — 잡을 인라인 생성하는 두 테스트(:42 근처 "s1", :62 근처 "src"/"dst")의 `create_job` **앞**에 씨딩 호출(파일 상단에 `_seed_storage` 헬퍼 1개 정의).
7. `tests/test_events_outside_transaction.py` — `_scan_job`(:88)에 "s1". (이 파일의 `_TxTrackingDB` 는 "events INSERT 가 트랜잭션 밖"만 검사한다 — storages.create 의 트랜잭션 추가는 단언과 무관하다. Step 5 에서 실측 확인.)
8. `tests/test_artifact_base_resolve.py` — `_scan_job`(:49)에 "s1". (:154 근처의 "ceph-a" 잡은 만들자마자 SUCCEEDED 종단이라 스테퍼가 밟지 않는다 — `claim_steppable` 은 종단을 클레임하지 않는다. 씨딩 불요.)

`tests/test_recover_orphans.py` 의 `_orphan` 도 storage "s" 없이 잡을 만들지만, 잡을 즉시 SUCCEEDED 로 보내므로 스테퍼가 밟지 않는다 — 수정 불요(Step 5 가 실측 판정).

- [ ] **Step 5: 사유 코드 등록 + 통과를 확인한다**

**(1)** `frontend/src/lib/reasonCodes.json` — Task 2 가 넣은 `  "unknown_tool",` 줄을 다음으로 교체(같은 줄 확장 — 최소 diff):

```json
  "unknown_tool", "storage_missing_at_step",
```

**(2)** `frontend/src/lib/api.ts` — `unknown_tool: ...` 줄 **바로 아래**에 추가:

```ts
  storage_missing_at_step: "잡 진행 중 스토리지 정의가 사라졌습니다 — 관리자에게 문의하세요",
```

**(3)** Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_stepper_fail_closed.py tests/test_stepper_scan.py tests/test_stepper_sync.py tests/test_stepper_enrich.py tests/test_stepper_artifact_uri.py tests/test_timeout_enforcement.py tests/test_vcjob_ttl.py tests/test_controller_stepper.py tests/test_events_outside_transaction.py tests/test_artifact_base_resolve.py tests/test_recover_orphans.py tests/test_reason_codes_coverage.py -q`
Expected: 전부 PASS. 특히 `test_stepper_enrich.py::test_build_spec_uses_absolute_paths`(`:41` — `/cephfs/dms/team/data`)가 join 치환의 무회귀 앵커, `test_stepper_scan.py::test_unexpected_exception_records_step_error_event`(`:155-173`)가 "일반 예외는 여전히 step_error 루프"라는 **구분**의 앵커다.

**(4)** Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run src/lib/reasonCodes.test.ts`
Expected: PASS.

- [ ] **Step 6: 뮤테이션으로 이빨 확인 후 원복**

(a) `_abs` 의 `posixpath.join(root, rel)` 을 `f"{root}/{rel}"` 로 → `test_legacy_root_slash_...` 가 `"//a"` 로 빨개진다. (b) `raise StorageMissingAtStep(...)` 를 `return rel` 로(폴백 복원) → `test_missing_storage_terminates_pending_...` 이 `"Preflight"` 로 빨개진다. 각각 확인 후 원복, Step 5 재확인.

- [ ] **Step 7: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "feat(stepper): _abs posixpath.join + 결측 fail-closed(storage_missing_at_step) — 폴백 삭제·이벤트·ref 회수, 스테퍼 테스트 8파일 storage 씨딩" -- src/dms/stepper.py tests/test_stepper_fail_closed.py tests/test_stepper_scan.py tests/test_stepper_sync.py tests/test_stepper_artifact_uri.py tests/test_timeout_enforcement.py tests/test_vcjob_ttl.py tests/test_controller_stepper.py tests/test_events_outside_transaction.py tests/test_artifact_base_resolve.py frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts
```

---

### Task 6: 고아 복구 — 오래된순 LIMIT 200 + 행 단위 격리

**Files:**
- Modify: `src/dms/repositories/data_jobs.py`, `src/dms/controller.py`
- Modify: `tests/test_recover_orphans.py`

**Interfaces:**
- Consumes: `terminal_jobs_older_than` 의 선례(같은 파일 `:305-327` — 오래된순 + LIMIT 200 + 이유 주석), `finalize_from_job` 멱등(`requests.py:151-163`), `record_event` 무예외 계약.
- Produces:
  - `terminal_jobs_with_live_request(*, limit=200)` — `ORDER BY d.updated_at ASC, d.job_id ASC LIMIT :n`. 술어(request 비종단) 덕에 처리된 행이 다음 스윕에서 빠지므로 틱마다 반드시 전진.
  - `controller._stepper_step` 고아 루프 — 행 단위 try/except, 실패는 `orphan_recovery_failed` 이벤트(component="stepper", severity="error", request_id 포함) 후 다음 행 계속. 0건 스윕은 무이벤트(0 은 정상값).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_recover_orphans.py` — **(1)** `_orphan` 시그니처를 `def _orphan(repos, key="k"):` 로 바꾸고 본문의 `resource_key="k"` 를 `resource_key=key` 로(기본값이 기존 두 테스트를 보존한다). **(2)** 파일 끝에 추가:

```python
# ---- 슬라이스 24 §2.3: 스윕 상한 + 행 단위 격리 ----

def test_sweep_is_bounded_to_200_and_still_makes_progress(db):
    """preview 만료 직후 크래시(§1-11: expire_previews 는 한 호출로 N 건 종단,
    finalize 는 행별 후속) 같은 대량 고아에서 무제한 스윕은 단일 스레드 컨트롤러
    (§1-10)의 한 틱을 통째로 먹어 planner·stepper·pod-gc 가 전부 그 뒤에 선다.
    상한 200(같은 파일 terminal_jobs_older_than 선례 미러)이어도, finalize 가
    멱등이고 처리된 행이 술어에서 빠지므로 두 틱이면 201건이 전부 복구된다."""
    from dms.controller import build_loops, run_all_once
    repos = Repositories(db)
    for i in range(201):
        _orphan(repos, key=f"k{i}")
    assert len(repos.data_jobs.terminal_jobs_with_live_request(limit=1000)) == 201
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")
    assert len(repos.data_jobs.terminal_jobs_with_live_request(limit=1000)) == 1
    run_all_once(loops, repos, holder="h1")
    assert repos.data_jobs.terminal_jobs_with_live_request(limit=1000) == []


def test_sweep_returns_oldest_first_under_the_limit(db):
    # 잘리는 상황에서 최신순이면 가장 오래된(가장 급한) 고아가 윈도우 밖으로
    # 영영 밀린다 -- terminal_jobs_older_than 과 같은 이유, 같은 정렬.
    repos = Repositories(db)
    pairs = [_orphan(repos, key=f"k{i}") for i in range(3)]
    for i, (_rid, jid) in enumerate(pairs):
        db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j",
                   {"t": f"2026-01-0{i + 1}T00:00:00Z", "j": jid})
    rows = repos.data_jobs.terminal_jobs_with_live_request(limit=2)
    assert [r["job_id"] for r in rows] == [pairs[0][1], pairs[1][1]]


def test_poison_row_does_not_starve_the_rest_and_leaves_an_event(db):
    """행 단위 try/except 가 없으면 첫 예외가 나머지 전부를 다음 틱으로 민다
    (§1-9) -- 그리고 독 행이 영구 독이면 나머지가 **영구히** 복구되지 않는다.
    격리 후에는: 독 행만 남고(다음 틱 멱등 재시도), 이벤트가 남는다."""
    from dms.controller import build_loops, run_all_once
    repos = Repositories(db)
    poison_rid, poison_jid = _orphan(repos, key="k-poison")
    healthy_rid, _healthy_jid = _orphan(repos, key="k-healthy")
    # 독 행을 더 오래된 행으로 -- 스윕이 먼저 만나 실패해야 격리가 증명된다.
    db.execute("UPDATE data_jobs SET updated_at = '2020-01-01T00:00:00Z' "
               "WHERE job_id = :j", {"j": poison_jid})
    original = repos.requests.finalize_from_job
    state = {"raised": False}

    def flaky(request_id, *args, **kwargs):
        if request_id == poison_rid and not state["raised"]:
            state["raised"] = True          # 1회성 독 -- 다음 틱 재시도가 성공해야 한다
            raise RuntimeError("poison row")
        return original(request_id, *args, **kwargs)

    repos.requests.finalize_from_job = flaky
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")
    assert repos.requests.get(healthy_rid)["state"] == "Succeeded"   # 독이 못 막았다
    assert repos.requests.get(poison_rid)["state"] == "Planned"      # 실패 행은 남았다
    events = repos.observability.events_for_request(poison_rid)
    assert [e["event_type"] for e in events] == ["orphan_recovery_failed"]
    assert "RuntimeError" in events[0]["message"]
    run_all_once(loops, repos, holder="h1")                          # 다음 틱 멱등 재시도
    assert repos.requests.get(poison_rid)["state"] == "Succeeded"


def test_zero_orphan_sweep_records_nothing(db):
    # 0건 스윕은 정상값이다(설계 §2.3) -- "고아 없음"을 이벤트로 남기면 매 틱
    # 노이즈가 쌓여 진짜 실패가 묻힌다.
    from dms.controller import build_loops, run_all_once
    repos = Repositories(db)
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")
    row = db.query_one("SELECT COUNT(*) AS c FROM events "
                       "WHERE event_type = 'orphan_recovery_failed'")
    assert row["c"] == 0
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_recover_orphans.py -q`
Expected: 신규 4건 중 3건 FAIL / 1건 PASS — `test_sweep_is_bounded_...` 와 `test_sweep_returns_oldest_first_...` 는 `TypeError: terminal_jobs_with_live_request() got an unexpected keyword argument 'limit'`, `test_poison_row_...` 는 FAIL — 현행 쿼리는 ORDER BY 가 없어 처리 순서가 비결정적이라, 독 행이 먼저 오면 `healthy ... == "Succeeded"` 단언에서(루프 전체가 죽어 healthy 도 Planned), 독 행이 뒤에 와도 `events_for_request(poison_rid) == ["orphan_recovery_failed"]` 단언에서(현행은 이벤트를 안 남긴다) 반드시 빨개진다. 예외는 `run_all_once` 의 루프 격리가 `error:RuntimeError` 로 접는다 — 즉 지금도 "다음 틱"엔 되지만 독이 영구면 영구히 굶는다. `test_zero_orphan_sweep_...` 는 현행 고정 가드라 **즉시 PASS 가 맞다**. 이 파일의 201건 테스트는 팩토리 반복이라 이 파일만 ~수십 초 걸릴 수 있다 — 정상이다.

- [ ] **Step 3: 리포지토리 쿼리를 고친다**

`src/dms/repositories/data_jobs.py` — `terminal_jobs_with_live_request` 를 다음으로 교체:

```python
    def terminal_jobs_with_live_request(self, *, limit: int = 200):
        """잡은 터미널인데 그 request가 아직 비터미널인 (job_id, request_id, state)
        목록. 컨트롤러 크래시 등으로 finalize_from_job이 누락된 고아 복구용.

        슬라이스 24 §2.3: 오래된순 + LIMIT 200 -- 같은 파일
        terminal_jobs_older_than 의 선례 미러다. 무제한 전량 JOIN 은 preview 만료
        직후 크래시(expire_previews 가 한 호출로 N 건을 종단시키고 finalize 는
        행별 후속) 같은 대량 고아에서 단일 스레드 컨트롤러의 한 틱을 통째로
        먹는다. finalize 가 멱등이고 처리된 행은 술어(request 비종단)에서 빠지므로
        틱마다 200건씩 반드시 전진한다 -- 오래된순이라 잘려도 가장 급한 행이
        먼저다."""
        job_terminal = tuple(s.value for s in TERMINAL_DATA_JOB_STATES)
        req_terminal = tuple(s.value for s in TERMINAL_REQUEST_STATES)
        jt = ", ".join(f":jt{i}" for i in range(len(job_terminal)))
        rt = ", ".join(f":rt{i}" for i in range(len(req_terminal)))
        params = {f"jt{i}": v for i, v in enumerate(job_terminal)}
        params.update({f"rt{i}": v for i, v in enumerate(req_terminal)})
        params["n"] = limit
        return self._db.query(
            f"""SELECT d.job_id, d.request_id, d.state FROM data_jobs d
                JOIN requests r ON r.request_id = d.request_id
                WHERE d.state IN ({jt}) AND r.state NOT IN ({rt})
                ORDER BY d.updated_at ASC, d.job_id ASC LIMIT :n""", params)
```

- [ ] **Step 4: 호출부를 행 단위로 격리한다**

`src/dms/controller.py` — `_stepper_step` 의 고아 복구 블록(현행 42-49행)을 다음으로 교체:

```python
        # 고아 복구: 잡은 터미널인데 request가 크래시 등으로 비터미널로 남은 경우.
        # finalize_from_job은 idempotent(이미 터미널이면 no-op)라 안전하게 재호출
        # 가능. 슬라이스 24 §2.3: 스윕은 오래된순 200건 상한(리포지토리 몫)이고,
        # 독 행 하나가 나머지 199건을 영구히 굶기지 않도록 행 단위로 격리한다 --
        # 실패 행은 이벤트로 남고 다음 틱에 멱등 재시도된다. 0건 스윕은 정상값이라
        # 아무것도 기록하지 않는다(0 을 실패로 위장하지 않는다).
        for orphan in repos.data_jobs.terminal_jobs_with_live_request():
            try:
                job = repos.data_jobs.get_job(orphan["job_id"])
                repos.requests.finalize_from_job(
                    orphan["request_id"], DataJobState(orphan["state"]),
                    reason_code="orphan_recovery",
                    summary=(job or {}).get("result_summary"), actor="stepper")
            except Exception as exc:
                repos.observability.record_event(
                    component="stepper", severity="error",
                    event_type="orphan_recovery_failed",
                    message=f"{type(exc).__name__}: {exc}"[:500],
                    request_id=orphan["request_id"])
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_recover_orphans.py tests/test_controller.py tests/test_controller_stepper.py tests/test_repo_requests_finalize.py -q`
Expected: 전부 PASS (기존 `test_orphan_recovery_via_controller`·summary 전파 테스트가 복구 의미론 무회귀의 안전망)

- [ ] **Step 6: 뮤테이션으로 이빨 확인 후 원복**

(a) 쿼리에서 ` LIMIT :n` 을 삭제 → `test_sweep_returns_oldest_first_...`(3건 반환)와 `test_sweep_is_bounded_...`(첫 틱에 201건 전부 복구돼 잔여 1 단언 실패)가 빨개진다. (b) 호출부의 try/except 를 벗겨 원래 모양으로 → `test_poison_row_...` 가 빨개진다(healthy 가 Planned 로 남는다). 각각 확인 후 원복, Step 5 재확인.

- [ ] **Step 7: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "feat(controller+repo): 고아 스윕 오래된순 LIMIT 200 + 행 단위 격리(orphan_recovery_failed) — 독 행이 복구를 굶기지 않는다" -- src/dms/repositories/data_jobs.py src/dms/controller.py tests/test_recover_orphans.py
```

---

### Task 7: 마감 검증 — 전체 스위트 + 프론트 + 불변 조항 (커밋 없음)

**Files:** 없음(검증만)

- [ ] **Step 1: 백엔드 전체 스위트**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: **약 1188 passed**(기준선 1166 + 신규 22: T1 3 + T2 2 + T3 2 + T4 7 + T5 4 + T6 4 — 근사치다. 수가 다르면 신규 수를 다시 세되 **failed 0 이 본질**이다). `test_migrations.py` 초록 = 스키마 무변경 보증.

- [ ] **Step 2: 프론트 전체 + 타입체크**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: `Tests  228 passed`(이 슬라이스는 프론트 테스트를 추가하지 않는다 — json/문구 2줄뿐), tsc 무출력 exit 0.

- [ ] **Step 3: 계약·불변 조항 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && git status --porcelain && git log --oneline -7`
Expected: 작업 트리 clean(커밋 6건 외 잔여물 없음), `deploy/k8s` 태그·`migrations.py`·`docs/`(이 플랜 파일 제외)·`legacy/` 무변경.

---

## 플랜 이후: 배포·실증 (설계 §6 — 별도 ops, 플랜 태스크 밖)

플랜 실행이 끝나면 배포자가 테스트베드에서 수행한다(슬라이스 12~22 관례). **매니페스트-우선**: 태그를 먼저 bump→커밋하고 **그 커밋에서** 빌드한다 — `Dockerfile.dms:54` 가 `deploy/k8s` 를 이미지에 COPY 하므로 순서가 바뀌면 포탈에 드리프트 배지가 뜬다. 현재 태그 d34 → **이 슬라이스는 d35**. 이번엔 제어면(`dms`)만이 아니라 **잡 이미지(`dms-mpifileutils`)도 필수**다 — 층3(러너)이 잡 이미지에 산다(현행 `DMS_JOB_IMAGE` 는 d27). DB 는 pkg-01 의 postgres(`postgresql://dmsapp:…@10.10.10.30:5432/dmsdb`). **되돌릴 수 있는 조작만 쓰고, 파괴적 실증(§6-1)은 전용 디렉터리에서만 한다.**

**0. 태그 범프 커밋 + 빌드 + apply**

```bash
# (a) 매니페스트 범프 -- 6곳: 20-config.yaml:22 DMS_JOB_IMAGE d27→d35,
#     30-migrate-job.yaml:25 / 40-api.yaml:67,84 / 41-controller.yaml:35,52 dms d34→d35,
#     50-agent-daemonset.yaml:72 dms-agent d34→d35 (태그 동기화는 5파일 수기 -- deploy/README).
git commit -m "deploy(k8s): 제어면·에이전트·잡 이미지 d35 (슬라이스 24 파괴적 경로 fail-closed)" -- deploy/k8s
# (b) 그 커밋을 pkg-01 로 가져가 빌드·푸시(podman 은 pkg-01 에만 있다):
REGISTRY=pkg-01:5000 TAG=d35 ./deploy/docker/build-and-push.sh   # mpifileutils dms agent 3종 전부
# (c) apply (30-migrate-job 은 스키마 무변경이라 재실행 불요 -- 태그만 동기화 커밋):
kubectl apply -f deploy/k8s/20-config.yaml -f deploy/k8s/40-api.yaml \
              -f deploy/k8s/41-controller.yaml -f deploy/k8s/50-agent-daemonset.yaml
kubectl -n dms rollout status deploy/dms-api deploy/dms-controller
# (d) 사전 확인: 기존 DB 에 "/" 행이 없는지(있으면 실증 전에 관리자와 정리 계획 수립):
#     psql "postgresql://dmsapp:<PW>@10.10.10.30:5432/dmsdb" \
#       -c "SELECT storage_name, mount_path, managed_root FROM storages;"
```

**1. (§6-1) 무회귀 앵커 — rm 잡 preview→confirm→성공.** 파괴적 정상 경로가 층1~3 을 전부 무변경 통과함을 실 클러스터에서 증명한다. **반드시 전용 디렉터리에서만**:

```bash
# dms-w1 에서 전용 드릴 디렉터리 생성. <uid>:<gid>는 포탈에서 rm 을 제출할 실제
# 요청자 계정의 것(drm 은 요청자 신원으로 돌므로 소유가 맞아야 지울 수 있다).
ssh dms-w1 'sudo mkdir -p /cephfs/dms/slice24-rm-drill &&
  sudo sh -c "echo x > /cephfs/dms/slice24-rm-drill/f1; echo x > /cephfs/dms/slice24-rm-drill/f2" &&
  sudo chown -R <uid>:<gid> /cephfs/dms/slice24-rm-drill'
```

포탈에서 그 요청자로 rm 제출: storage = (managed_root 가 `/cephfs/dms` 인 기존 스토리지), target = `slice24-rm-drill`, recursive=true → preview 에서 항목 수 확인 → confirm → **Succeeded**. 검증: `ssh dms-w1 'ls /cephfs/dms/slice24-rm-drill'` → No such file, `ls /cephfs/dms` 의 다른 항목 무손상. **이 실증이 지우는 것은 `/cephfs/dms/slice24-rm-drill` 이하(f1, f2, 디렉터리 자신)뿐이다.** 원복 불요(드릴 데이터).

**2. (§6-2) 포탈에서 mount_path="/" 생성 시도 → 422.** 포탈 스토리지 등록 폼에 mount `/`, root `/` 입력 → "스토리지 설정이 올바르지 않습니다"(`invalid_storage`). API 로도: `curl -X POST .../api/admin/storages -d '{"storage_name":"rootfs","mount_path":"/","managed_root":"/","backend_type":"cephfs"}'` → 422. 아무것도 생성되지 않으므로 원복 불요.

**3. (§6-3) Pending 잡 tool 변조 → 다음 틱 `unknown_tool`, 파드 0건.** DB 직접 UPDATE 는 **pkg-01 호스트에서 psql 로** 한다(postgres 는 파드가 아니라 pkg-01 호스트 서비스다 — 슬라이스 22 실증과 동일 창구. 비밀번호는 dms-secrets 의 `DMS_DATABASE_URL` 참조):

```bash
# (a) 잡을 Pending 에 붙잡아 두기 위해 drain 을 켠다. control-state 는 전체 교체
#     PUT 이라 기존 build_node_name 을 먼저 읽어 같이 되돌려 보낸다(잃지 않게).
curl -s .../api/admin/control-state   # 현재 값 기록
curl -X PUT .../api/admin/control-state -d '{"maintenance":false,"drain":true,"reason":"slice24-e2e","build_node_name":"<기존값>"}'
# (b) 포탈에서 scan 잡 1건 제출 -> planner 가 계획해 data_jobs 에 Pending 으로 남는다.
#     job_id 는 포탈 잡 상세 또는: psql ... -c "SELECT job_id, state, tool FROM data_jobs ORDER BY created_at DESC LIMIT 1;"
# (c) tool 변조 (Pending 인 그 행만 -- WHERE 에 state 를 함께 건다):
psql "postgresql://dmsapp:<PW>@10.10.10.30:5432/dmsdb" \
  -c "UPDATE data_jobs SET tool = 'dwalk' WHERE job_id = '<jid>' AND state = 'Pending';"   # UPDATE 1 확인
# (d) drain 해제 -> 스테퍼 다음 틱(5s):
curl -X PUT .../api/admin/control-state -d '{"maintenance":false,"drain":false,"reason":null,"build_node_name":"<기존값>"}'
# (e) 판정: 잡 상세가 Rejected + "허용되지 않은 도구입니다 — 관리자에게 문의하세요".
kubectl -n dms get pod | grep <jid 앞 12자> ; kubectl -n dms get vcjob | grep <jid 앞 12자>
#     -> 둘 다 0건: 층1이 "제출 자체"를 막았다는 증거(읽기 전용 확인).
# (f) 원복: drain 은 (d)에서 이미 원복. 변조한 행은 그 자체가 종단(Rejected)됐고
#     실증 기록으로 남긴다 -- 추가 UPDATE 불요. control-state 의 나머지 값 원복 확인.
```

**4. (§6-4) 층3 단독 — 클러스터 무변경 검증.** pkg-01 에서 잡 이미지 컨테이너를 로컬로 띄워 `run_job` 만 단독 실행:

```bash
mkdir -p /tmp/slice24-l3
podman run --rm -v /tmp/slice24-l3:/art \
  -e DMS_JR_TOOL=sh -e DMS_JR_USERNAME=alice -e DMS_JR_UID=10001 -e DMS_JR_GID=10000 \
  -e DMS_JR_PROCESS_COUNT=1 -e DMS_JR_ARTIFACT_DIR=/art -e 'DMS_JR_ARGV=["/etc"]' \
  pkg-01:5000/dms-mpifileutils:d35 /usr/local/bin/dms-job-runner ; echo "rc=$?"
# 기대: rc=1, stderr 에 DMS_JR_UNKNOWN_TOOL, mpirun/ssh 시도 로그 없음(즉시 종료),
#       /tmp/slice24-l3/summary.json == {"returncode": 1, "files": null, "bytes": null}
cat /tmp/slice24-l3/summary.json && rm -rf /tmp/slice24-l3   # 원복
```

**5. (§6-5) 고아 3건 수동 재현 → 한 틱 복구 + 재스윕 0건.** request 상태를 되돌리는 DB 조작과 **원복**까지:

```bash
# (a) 대상 선정: 잡·요청 둘 다 Succeeded 인 3건 (되돌려도 의미가 보존되는 짝).
psql "postgresql://dmsapp:<PW>@10.10.10.30:5432/dmsdb" -c \
 "SELECT d.job_id, d.request_id FROM data_jobs d JOIN requests r ON r.request_id = d.request_id
   WHERE d.state = 'Succeeded' AND r.state = 'Succeeded' ORDER BY d.updated_at DESC LIMIT 3;"
# (b) 고아 만들기(요청만 비종단으로 되돌린다):
psql ... -c "UPDATE requests SET state = 'Running' WHERE request_id IN ('<r1>','<r2>','<r3>');"  # UPDATE 3
# (c) 스테퍼 다음 틱(5s) 후 판정:
psql ... -c "SELECT request_id, state FROM requests WHERE request_id IN ('<r1>','<r2>','<r3>');"
#     -> 전부 Succeeded. state_transitions 에 Running→Succeeded reason_code='orphan_recovery' 3건.
psql ... -c "SELECT COUNT(*) FROM data_jobs d JOIN requests r ON r.request_id = d.request_id
   WHERE d.state IN ('Succeeded','Failed','TimedOut','Cancelled','Rejected','PreviewExpired')
     AND r.state NOT IN ('Succeeded','Failed','Rejected','Conflict','Cancelled');"   # -> 0 (재스윕 0건)
# events 에 orphan_recovery_failed 0건(행 격리 경로는 독 행 없인 발화하지 않는다 --
# 그 경로는 §5 단위 테스트 몫임을 숨기지 않는다).
# (d) 원복 -- 복구가 이력을 어떻게 오염시켰는지까지 정직하게 치운다:
#     finalize 는 record_result 를 다시 INSERT 하므로 results 에 request 당 1행이 중복됐다.
psql ... -c "DELETE FROM results WHERE request_id IN ('<r1>','<r2>','<r3>')
             AND reason_code = 'orphan_recovery';"   # DELETE 3 -- 원래 행(reason NULL)은 남는다
#     state_transitions 의 추가 2행/req(Running 강등 + 복구)은 append-only 이력이라
#     지우지 않는다 -- 실증 흔적으로 남기는 것이 정직하다. requests.state 는 (c)에서
#     이미 원값(Succeeded)으로 수렴했다.
```

실증 5건 통과 후 `docs/superpowers/BACKLOG.md`(§2.1 항목 4건 완료 + 현황)를 별도 커밋으로 갱신한다(플랜 밖, 관례).

---

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 |
|---|---|
| §1 실측 전제 12항 | 「설계 §1 전제 재확인」표 — 전항 재실측, 정정 5건(라인 드리프트·러너 config 위치·기준선·백로그 위치·빌드 절차/잡 이미지) |
| §2.1 층1 (unknown_tool 종단·REJECTED/FAILED 대칭·ref 회수·관용 분기 제거) | Task 2 |
| §2.1 층2 (tool_argv 명시 분기 + raise, submit_failed fold) | Task 1 |
| §2.1 층3 (러너 allowlist·중복 정의 + 동일성 계약·DB CHECK 기각) | Task 3 (CHECK 는 §7 대로 만들지 않음) |
| §2.2 ("/" 거부 + `_abs` join) | Task 4(거부) + Task 5(join — 레거시 행 2차 방어) |
| §2.3 (LIMIT 200 + 오래된순 + 행 격리 + 0건 무이벤트) | Task 6 |
| §2.4 (`_abs` fail-closed + update 가드 + 정직한 한계) | Task 5 + Task 4(가드·한계 주석) |
| §2.5 (사유 2종 양쪽 등록, 기존 2종 재사용) | Task 2/5 (각자 커밋), Task 4 는 재사용만 |
| §3 화면 (신설 화면 0, 문구 2줄) | Task 2/5 의 api.ts 2줄 — 컴포넌트·프론트 테스트 무변경 |
| §4 오류 처리 (terminate_failed 관례·fold 비은폐·러너 거부 summary/마커·행 격리 이벤트) | Task 2(terminate)·1(fold)·3(summary 3키)·6(이벤트) |
| §5 테스트 목록 | 각 Task Step 1 이 1:1 이상 커버 + 무회귀 앵커(기존 스위트) 명시. 기준선 1131→1166 실측 갱신 |
| §6 실증 5항 | 「플랜 이후」절 — 실행 가능한 명령·전용 디렉터리·psql 원복까지 |
| §7 하지 않는 것 | 어떤 태스크도 DB CHECK/경로 스냅숏/가드 트랜잭션화/도구 절대경로/`execution_failed` 세분화/operation 재점검을 만들지 않음 |

**2. 뮤테이션(이빨) 매트릭스** — 각 태스크 Step 에 내장: T1 fall-through 복원 → raise 테스트 RED / T2 가드 삭제 → 종단 2건 RED / T3 allowlist 오염·가드 무력화 → 계약·거부 각각 RED / T4 "/" 블록 삭제·`changed` 무시 → 422·토글 각각 RED / T5 join→f-string·raise→폴백 → 레거시·종단 각각 RED / T6 LIMIT 삭제·try 제거 → 상한·독행 각각 RED.

**3. 타입·이름 일관성** — `_fail_closed(job, *, reason_code)` 는 Task 2 정의·Task 5 재사용(키워드 리터럴 호출이 AST 계약 조건). `StorageMissingAtStep` 은 stepper 모듈 레벨(테스트 씨딩 주석이 같은 철자 참조). `ALLOWED_TOOLS`/`AGENT_TOOL_NAMES`, `terminal_jobs_with_live_request(*, limit=200)`, 이벤트 타입 `storage_missing_at_step`/`orphan_recovery_failed`, 사유 `unknown_tool`/`storage_missing_at_step` 은 각 정의처와 테스트가 동일 철자다.

**알려진 위험 / 설계 대비 조정:**
- **Task 5 의 기존 테스트 8파일 씨딩은 설계에 없던 실측 발견이다** — fail-closed 는 "storage 없는 잡은 죽는다"는 뜻이고, 스테퍼를 돌리는 기존 테스트들이 정확히 그 상태였다. 씨딩은 폴백 시절의 우연한 관대함을 명시적 전제로 바꾸는 것이지 동작 우회가 아니다. 경로를 단언하는 테스트가 8파일에 없음을 grep 으로 확인했다.
- **unknown_tool 에는 record_event 를 달지 않았다**(storage_missing_at_step 만) — unknown_tool 은 reason_code 가 전이·results 에 그대로 영속되어 정보 손실이 없고, storage_missing 은 "어느 스토리지였는지"가 전이에 안 남아 이벤트 보강이 필요하다. 설계 §2.4 도 후자에만 `+ record_event` 를 명시한다.
- **`_fail_closed` 는 종단 phase 의 ref 도 terminate 한다**(살아 있는 것만 골라내지 않는다) — 스텁·볼케이노 어댑터 모두 존재하지 않는/끝난 리소스의 terminate 가 무해(멱등)하고, 신뢰 경계가 깨진 잡의 진단 파드 로그보다 고아 리소스 0 이 우선이다.
- **update 가드의 비교는 라우트에서 normpath** — 리포지토리 `_validate`/`update` 는 무변경(가드는 delete 와 같은 요청 레벨 관례). check-then-act 비원자 창은 설계 §2.4 그대로 문서화했고 `_abs` fail-closed 가 최종 방어다.
- **고아 201건 테스트는 팩토리 반복이라 느리다(~수십 초)** — 설계 §5 가 "201건 → 첫 틱 200 + 둘째 틱 잔여"를 명시해 그대로 구현한다. 더 빠른 대안(직접 INSERT)은 requests 의 NOT NULL 8필드를 손으로 지어내는 취약한 픽스처가 되어 기각.
- **poison 테스트의 finalize 래퍼는 1회성** — 영구 독을 흉내내면 "다음 틱 멱등 재시도"까지 한 테스트에서 증명할 수 없다. 영구 독의 종말(그 행만 영원히 실패 + 매 틱 이벤트)은 격리 구조상 자명하고 이벤트 축적은 retention 이 정리한다.
- **전체 수치 기대(≈1188)는 근사 명시** — 어긋나면 재계산하되 failed 0 이 판정 기준.

## 결정이 필요한 열린 질문

1. **`storage_in_use` 문구가 update 409 에서 "삭제할 수 없습니다"라고 말한다** — 스스로 판단해 **바꾸지 않기로 했다**. 설계 §3 이 "프론트 코드 변경은 문구 재사용뿐"이라 명시했고, `StoragesList.test.tsx` 2곳이 현행 문구를 직접 단언하고 있어 바꾸면 프론트 테스트까지 diff 가 번진다. 문구 정련("경로·백엔드를 바꾸거나 삭제할 수 없습니다")은 포탈 위생 슬라이스 감이다.
2. **실증 §6-1 의 요청자 계정(uid:gid)** — 테스트베드의 실제 포탈 계정에 따라 배포자가 치환해야 한다(플랜은 `<uid>:<gid>` 자리표시자). 특권 요청자로 하면 chown 이 불필요하지만, 비특권 경로가 더 대표적인 실사용이라 비특권을 권장한다.
3. **기존 DB 에 "/" 스토리지 행이 이미 있으면**(배포 전 확인 쿼리가 잡아낸다) — 이 슬라이스의 `_abs` join 이 `//` 는 막지만 노드 루트 hostPath 마운트 자체는 남는다. 발견 시 그 스토리지의 잡 이력을 확인하고 관리자가 delete→재등록해야 하며, 이는 운영 판단이라 플랜이 자동화하지 않는다.
