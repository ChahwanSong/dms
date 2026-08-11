# 슬라이스 21 — 포탈 빌드 되살리기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 포탈 주도 이미지 빌드(슬라이스 11)를 테스트베드에서 실사용 가능하게 만든다. (1) 빌드 파드에 리소스 봉투(cpu 250m/1000m · mem 128Mi/1Gi · eph 10Gi/12Gi)와 PriorityClass `dms-build`(10, Never)를 달고 emptyDir sizeLimit 을 20Gi→10Gi 로 내려 「빌드는 데이터 잡을 굶기지도 축출하지도 않고, 압박 시 항상 빌드가 먼저 혼자 죽는다」를 구조로 보장한다. (2) 착수 전 적합성 프로브 파드(egress 다중 호스트 TCP 443 / pkg-01:5000 / `os.statvfs` 디스크 공식)가 빌드 노드에서 실검사해 「인터넷 안 열림」을 2시간 generic 타임아웃이 아니라 수 분 안에 고유 사유 코드(`build_node_no_egress` 등)로 표면화한다. (3) 제출 시 동기 검증 2종(`invalid_repo_url`/`build_node_report_stale`), (4) 회수 시 Pending 구분(`build_stuck_pending`), (5) 프로브 파드 GC(종단 빌드만), (6) 신설 사유 코드 8종을 프론트/백엔드 계약 양쪽에 등록한다.

**Architecture:** 아래에서 위로 쌓는다. (1) `deploy/k8s/05` 에 PriorityClass `dms-build`(값 10 < dms-low 50, preemptionPolicy Never) — 없는 클래스를 참조하면 파드 생성이 admission 거절이므로 모든 파드 변경보다 먼저다. (2) `build_manifests.build_build_pod` 에 봉투·클래스·sizeLimit 10Gi. (3) 같은 파일에 프로브 파드 매니페스트(순수 함수) + `repo_host` 파서, `BuildRunner.submit_preflight`(멱등, AlreadyExists 관용 — `submit` 선례 복제), `StubBuildRunner` 는 즉시 OK. 프로브 ref 는 기존 `buildpod/` 프리픽스를 재사용해 poll/read_log/terminate 가 공짜다. (4) `BuildWatcher` pending 루프를 상태기계로: 프로브 없음→생성 / 진행 중→대기(+`DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS` 회수) / 실패 마커→화이트리스트 채택 / 성공 OK→빌드 제출·mark_running. DB 에 상태를 더 만들지 않는다 — 프로브 파드 이름이 build_id 에서 결정적이라 언제든 다시 찾을 수 있다. running 루프 회수 분기에는 poll 1회를 넣어 PENDING 이면 `build_stuck_pending`. (5) `routes_builds` 검증 사슬에 동기 2종. (6) `pod_gc` 가 종단 빌드의 프로브 파드도 수거. (7) 프론트는 Pending 캡션 한 줄 + 사유 문구뿐 — 별도 상태 기계 없음.

**Tech Stack:** Python 3.11 표준 라이브러리(`urllib.parse`·`socket`·`os.statvfs` — 프로브 스크립트는 job_image 의 python3, Dockerfile.mpifileutils:81 이 python:3.11-slim 기반이라 보장), FastAPI 라우트, React 18 + Vitest/msw. **새 의존성 없음, DB 스키마 무변경**(새 테이블은 물론 새 컬럼도 불필요 — 프로브 상태는 파드 이름 결정성이 대신한다).

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-11-dms-portal-build-slice21-design.md`. 플랜과 충돌하면 **설계가 이긴다**.
- **새 pip/npm 의존성 금지.** (Task 1 이 pyyaml 없이 텍스트 단언을 쓰는 이유다 — venv 에 yaml 이 없음을 실측했다.)
- **새 DB 테이블 금지**(컬럼 추가는 허용되지만 이 슬라이스는 컬럼도 만들지 않는다). 만약 후속 조정으로 컬럼이 필요해지면 CREATE TABLE 과 `_ensure_columns` **양쪽**에 넣는다(슬라이스 14 실 500 교훈).
- **새 사유 코드는 `frontend/src/lib/reasonCodes.json` 과 `frontend/src/lib/api.ts` 의 REASON_MESSAGES 를 같은 커밋에서 갱신**한다 — 백엔드 `tests/test_reason_codes_coverage.py`(src 리터럴 ⊆ json)와 프론트 `reasonCodes.test.ts`(json ⊆ REASON_MESSAGES, 죽은 키 금지)가 양방향으로 건다. 이 플랜에서는 Task 4(6종)·Task 5(2종)가 각자 자기 커밋에 등록한다 — 마지막 태스크로 미루면 Task 4/5 커밋 시점에 백엔드 계약 테스트가 빨간불이다.
- **PriorityClass 는 파드보다 먼저 적용돼야 한다** — 없는 클래스를 참조하면 파드 생성이 admission 거절된다(`05-volcano-queue-priorityclass.yaml:11` 머리 주석의 그 규칙). Task 1 이 매니페스트를 먼저 만들고, 실제 클러스터 적용 순서는 「플랜 이후」 절에 명시한다.
- **스텁 경로(`StubBuildRunner`)는 클러스터 없이 초록이어야 한다** — 프리플라이트를 포함해 즉시 성공 유지(`submit_preflight` 가 OK 로그를 심고 poll 은 SUCCEEDED). 로컬·CI 의 모든 빌드 테스트가 이 계약 위에 있다.
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- **origin 으로 push 금지, 브랜치 변경 금지, `deploy/k8s` 의 이미지 태그 변경 금지**(배포는 호출자가 한다). 커밋만 한다.
- 백엔드 테스트 명령(이 워크트리 전용, `.venv` 는 워크트리 밖 공용):
  `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest <대상> -q`
  전체 스위트는 **포그라운드**로 Bash timeout 900000ms. **기준선 1090 passed.**
- 프론트: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/frontend && npx vitest run`(**기준선 225 passed / 49 files**), 타입체크 `npx tsc -b`. **워크트리에 node_modules 가 없으므로 Task 0 이 `npm ci --prefer-offline --no-audit --no-fund` 를 먼저 돌린다.**
- 주석은 **한국어**로 「왜」를 적는다.

## 실측 고정값 (코드 직접 확인)

| 항목 | 값 |
|---|---|
| 빌드 파드 현행 | nodeSelector `{kubernetes.io/hostname: node}`(`build_manifests.py:61`), schedulerName·resources·priorityClassName 부재, privileged(`:66`), restartPolicy Never(`:59`), activeDeadlineSeconds=timeout(`:60`), emptyDir sizeLimit 20Gi(`:72-75`, 주석 스스로 「경험적 상한」 `:67-71`). env 로만 값 전달(스크립트 본문 비보간 — `test_build_manifests.py:34-44` 가 고정) |
| ref 계약 | `BUILD_REF_PREFIX = "buildpod"`(`build_runner.py:14`), `_name` 이 `prefix/name` 파싱(`:20-24`) — **파드 이름만 맞으면 poll/read_log/terminate 는 어떤 파드든 동작**한다(프로브 재사용의 근거). poll 은 객체 없음→FAILED(`:72-73`), phase 매핑 `_POD_PHASE`(`:15-17`) |
| AlreadyExists 관용 선례 | `BuildRunner.submit`(`build_runner.py:47-63`): create 실패 시 같은 이름 파드 존재 확인→성공 취급. `submit_preflight` 가 이 계약을 그대로 복제한다 |
| 워처 현행 | pending 루프가 곧장 submit→mark_running(`build_watcher.py:45-53`), running 루프 회수 분기 `created_at < cutoff`(`:68-87`) — **poll 없이 회수**(`test_build_watcher.py:151` 이 `polled == []` 를 단언 — Task 4 가 이 단언을 바꾼다), 종단 finish(`:89-98`), I6 빌드별 예외 격리(`:61-66`), `_TERMINAL`(`:15`) |
| builds 리포지토리 | `build_pod_name = f"dms-build-{build_id[:12]}"[:63]`(`builds.py:20-21`), `pending()/running()` seq ASC(`:111-115`), `mark_running` 은 Pending→Running 만(`:117-120`), `finish` 는 COALESCE(log/sha)·64KB 꼬리·종단 불변(`:122-133`), `terminal_older_than` 은 finished_at 기준(`:135-144`) |
| 제출 검증 사슬 | 유지보수→`build_node_not_set`→`unknown_image`→`invalid_git_ref`→409 빠른 거절→`create()` 트랜잭션 가드(`routes_builds.py:41-67`). repo_url 은 `body.repo_url or settings.build_repo_url` 무검증 통과(`:61`) |
| preflight 마커 선례 | `execution_manifests._preflight_script`(`:256-291`): 실패 = `echo DMS_PREFLIGHT_REASON=<code>; exit 1`, 성공 = `echo DMS_PREFLIGHT_OK`. 파드는 job_image·nodeSelector·restartPolicy Never(`:297-328`) |
| fresh 판정 | `agents.list_nodes`: `fresh = reported_at > (now - stale_seconds)` **엄격 부등호**(`repositories/agents.py:24-34`), `ingest` 는 노드당 1행 교체(`:9-22`) — reported_at 인자로 과거 시각 주입 가능 |
| 설정 | `_SERVER_INT_KEYS` 에 빌드 키 2종(`config.py:31-36`), Settings 빌드 필드(`:128-135`), from_env 는 `**extra` 로 int 키 일괄 주입(`:154-155,165`) — **키 튜플에만 넣으면 from_env 배선은 공짜**. `job_image`(`:121`)는 volcano 배선에서 항상 설정(`20-config.yaml:22` = `pkg-01:5000/dms-mpifileutils:d27`) |
| 배선 | `wiring.build_build_runner`(`wiring.py:32-42`): stub 이면 `StubBuildRunner`, 아니면 `BuildRunner(k8s, namespace, registry, builder_image, timeout_seconds)`. `app.state.build_runner`(`api/app.py:37`), 컨트롤러 build-watcher 루프(`controller.py:79-83`), pod-gc 루프(`:67-70`) |
| pod_gc 현행 | build_runner 있으면 종단 빌드의 **빌드 파드 1개만** terminate(`pod_gc.py:39-47`), `test_pod_gc.py:183-188` 이 `deleted == 1`·ref 1개를 단언(Task 6 이 바꾼다), 비종단 불가침(테스트 8), None 이면 조회 자체 스킵(테스트 9b) |
| PriorityClass 실물 | dms-low 50 / dms-mid 100 / dms-high 200, 전부 PreemptLowerPriority(`05-…yaml:24-49`), 「missing PriorityClass → admission REJECT」 주석(`:11`). venv 에 pyyaml 없음(실측 `ModuleNotFoundError`) — 텍스트 단언으로 고정 |
| 20-config.yaml 빌드 절 | `:86-97` — REGISTRY/BUILDER_IMAGE/REPO_URL/WATCHER_INTERVAL/TIMEOUT. 새 키는 이 절에 붙인다 |
| 사유 코드 계약 | 백엔드 AST 추출은 `detail=`/`reason_code=` **키워드의 문자열 상수**와 예외 생성자 1번 인자만(`test_reason_codes_coverage.py:36-38,70-92`) — 조건식(IfExp)·변수 경유·화이트리스트 set 리터럴은 미추출이지만, 프론트 표시를 위해 8종 전부 json+REASON_MESSAGES 에 등록한다. 프론트는 json ⊆ REASON_MESSAGES + 죽은 키 금지(`reasonCodes.test.ts:19-28`, 허용 예외 http_401/422/500/503) |
| 프로브 대상 호스트 실측 | builder image quay.io(`config.py:129`), 베이스 이미지는 전부 docker.io: node:20-bookworm-slim(`Dockerfile.dms:13`)·python:3.11-slim-bookworm(`Dockerfile.dms:24`, `Dockerfile.mpifileutils:81`)·debian:bookworm(`Dockerfile.mpifileutils:17`) → registry-1.docker.io. 레지스트리 `pkg-01:5000`(`config.py:128`) |
| 디스크 공식 상수 | 설계 §1-12/§2.5 실측: evictionHard nodefs 10%·imagefs 15%(미러 상수 0.15), sizeLimit 10Gi + 마진 2Gi → `avail ≥ 0.15·total + 12GiB`. dms-w1 대입 21.78GB ≥ 18.96GB 통과 |
| build_id | uuid4 hex(`builds.py:47`) — [0-9a-f] 뿐이라 `dms-build-pf-` 프리픽스가 `dms-build-<hex>` 와 절대 충돌하지 않는다(테스트 더블의 ref 판별 근거) |
| 테스트 관례 | conftest `db`/`settings`/`client`(`conftest.py:8-24`) — client 는 stub 백엔드라 `app.state.build_runner` 가 StubBuildRunner. `test_config.py` 의 `VALID` env dict 관례. 워처 테스트는 파일 로컬 `_Runner` 더블 + `repos` 픽스처(`test_build_watcher.py:10-47`) |
| 기준선 | 백엔드 1090 passed, 프론트 225 passed / 49 files |

## 파일 구조

| 파일 | 책임 |
|---|---|
| `deploy/k8s/05-volcano-queue-priorityclass.yaml` (수정) | PriorityClass `dms-build`(10, Never) 문서 추가 |
| `tests/test_build_priorityclass.py` (신규) | dms-build 값·Never·기존 3계급 불변 텍스트 고정 |
| `src/dms/build_manifests.py` (수정) | 봉투·클래스·sizeLimit 상수화(Task 2) + `repo_host`·`_PROBE_SCRIPT`·`build_probe_pod`(Task 3) |
| `src/dms/repositories/builds.py` (수정) | `build_probe_pod_name` — 프로브 파드 결정적 이름 |
| `src/dms/build_runner.py` (수정) | `BuildRunner.submit_preflight`(+생성자 `job_image`/`preflight_timeout_seconds`) + `StubBuildRunner.submit_preflight` |
| `src/dms/config.py`, `deploy/k8s/20-config.yaml` (수정) | `DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS`(기본 180) 양쪽 |
| `src/dms/wiring.py` (수정) | BuildRunner 에 job_image·preflight timeout 전달 |
| `src/dms/build_watcher.py` (수정, 전체 교체) | 프리플라이트 상태기계 + `parse_preflight_reason` + `build_stuck_pending` 구분 |
| `src/dms/controller.py` (수정) | BuildWatcher 에 preflight_timeout_seconds 전달 |
| `src/dms/api/routes_builds.py` (수정) | `invalid_repo_url`·`build_node_report_stale` 동기 422 |
| `src/dms/pod_gc.py` (수정) | 종단 빌드의 프로브 파드 수거(비종단 불가침 유지) |
| `tests/test_build_manifests.py`, `test_build_runner.py`, `test_build_watcher.py`, `test_api_builds.py`, `test_pod_gc.py`, `test_config.py` (수정) | 각 계층 계약 |
| `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts` (수정, Task 4/5 분할) | 신설 사유 코드 8종 + 한국어 매핑 |
| `frontend/src/features/builds/BuildDetail.tsx` (+test 수정) | Pending 프리플라이트 캡션 + egress 문구 검증 |

---

### Task 0: 워크트리 준비 — 프론트 의존성 + 기준선

**Files:** 없음(설치·검증만, 커밋 없음)

**Interfaces:** 없음 — 이후 모든 태스크의 실행 전제(node_modules 존재, 기준선 초록)만 만든다.

- [ ] **Step 1: 프론트 의존성 설치 (워크트리에 node_modules 가 없다)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/frontend && npm ci --prefer-offline --no-audit --no-fund`
Expected: `added <N> packages` 후 exit 0. (네트워크 불가 환경이면 `--prefer-offline` 이 캐시로 해결한다 — 실패 시 캐시 상태를 먼저 의심할 것.)

- [ ] **Step 2: 백엔드 기준선 확인**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: `1090 passed`

- [ ] **Step 3: 프론트 기준선 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/frontend && npx vitest run && npx tsc -b`
Expected: `Test Files  49 passed`, `Tests  225 passed`, tsc 무출력 exit 0.

---

### Task 1: PriorityClass `dms-build`(10, Never) 매니페스트

**Files:**
- Modify: `deploy/k8s/05-volcano-queue-priorityclass.yaml`
- Create: `tests/test_build_priorityclass.py`

**Interfaces:**
- Consumes: 기존 3계급 문서 구조(`05-…yaml:24-49`), admission 거절 규칙 주석(`:11`).
- Produces (Task 2·3 매니페스트가 이 이름을 그대로 참조한다): 클러스터 오브젝트 `PriorityClass/dms-build` — `value: 10`(< dms-low 50), `preemptionPolicy: Never`, `globalDefault: false`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_build_priorityclass.py` (신규 파일 전체):

```python
"""슬라이스 21 §2.3: PriorityClass dms-build(10, Never).

kubelet 축출 순서는 ①사용량이 requests 를 초과하는가 ②pod priority ③초과량이다.
BestEffort 데이터 잡은 요청 0 이라 항상 ①그룹이고, 빌드도 memory requests 128Mi
(§2.2)를 실압박에선 초과해 같은 그룹 -- ②priority 가 방향을 가른다. 값 10 <
dms-low 50 이므로 "빌드가 항상 먼저 죽는다"가 명문화되고, Never 라 빌드는 아무도
선점하지 않는다.

pyyaml 이 venv 에 없어(새 pip 의존성 금지) 텍스트 수준으로 고정한다 --
test_manifest_tags.py 가 deploy/k8s 를 텍스트로 읽는 것과 같은 관례다."""
from pathlib import Path

YAML = (Path(__file__).resolve().parent.parent / "deploy" / "k8s"
        / "05-volcano-queue-priorityclass.yaml")


def _dms_build_block():
    docs = YAML.read_text().split("\n---\n")
    matches = [d for d in docs if "name: dms-build" in d]
    assert len(matches) == 1, "dms-build PriorityClass 문서가 정확히 하나여야 한다"
    return matches[0]


def test_dms_build_priorityclass_is_below_every_data_job_class_and_never_preempts():
    block = _dms_build_block()
    assert "kind: PriorityClass" in block
    assert "value: 10" in block                  # < dms-low 50 -- 축출 1순위
    assert "preemptionPolicy: Never" in block    # 빌드는 아무도 선점하지 않는다
    assert "globalDefault: false" in block       # 클러스터 기본값 오염 금지


def test_existing_data_job_classes_are_untouched():
    # 잡 우선순위 3계급은 이 슬라이스의 대상이 아니다(§2.3: 데이터 잡은 손대지
    # 않는다) -- 실수로 지워지거나 값이 바뀌면 잡 제출이 admission 거절된다.
    text = YAML.read_text()
    for needle in ("name: dms-low", "value: 50", "name: dms-mid", "value: 100",
                   "name: dms-high", "value: 200"):
        assert needle in text, needle
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_build_priorityclass.py -q`
Expected: FAIL 1건 / PASS 1건 — `test_dms_build_priorityclass_...` 가 `AssertionError: dms-build PriorityClass 문서가 정확히 하나여야 한다`(현재 0개), `test_existing_...` 은 현행 고정 가드라 즉시 PASS.

- [ ] **Step 3: 매니페스트에 dms-build 문서를 추가한다**

`deploy/k8s/05-volcano-queue-priorityclass.yaml` — 파일 **끝**(dms-high 문서 뒤)에 추가:

```yaml
---
# 슬라이스 21(포탈 빌드): 빌드 파드·프리플라이트 프로브 파드 전용. value 10 <
# dms-low 50 -- kubelet 축출 랭킹(①requests 초과 여부 ②priority ③초과량)에서
# 빌드가 어떤 데이터 잡보다도 먼저 죽는 방향을 명문화한다(잡은 전부 BestEffort 라
# ①그룹, 빌드도 requests 128Mi 를 실압박에선 초과해 같은 그룹 -- ②가 가른다).
# preemptionPolicy Never: 빌드는 아무도 선점하지 않는다(요구사항: 잡 축출 금지).
# 이 클래스가 클러스터에 없으면 참조하는 파드 생성이 admission 거절되므로(위 머리
# 주석의 그 규칙), 슬라이스 21 컨트롤러 이미지 배포 **전에** 반드시 apply 한다.
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata:
  name: dms-build
value: 10
globalDefault: false
preemptionPolicy: Never
description: "DMS portal-driven image builds -- evicted before any data job, never preempts."
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_build_priorityclass.py tests/test_release_manifest_contract.py tests/test_manifest_tags.py -q`
Expected: 전부 PASS (뒤 둘은 deploy/k8s 를 읽는 기존 테스트의 회귀 확인 — 무변경이어야 한다)

- [ ] **Step 5: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21
git add deploy/k8s/05-volcano-queue-priorityclass.yaml tests/test_build_priorityclass.py
git commit -m "deploy(k8s): PriorityClass dms-build(10, Never) — 빌드는 항상 먼저 죽고 아무도 선점하지 않는다"
```

---
### Task 2: 빌드 파드 봉투 — requests/limits · dms-build · sizeLimit 10Gi

**Files:**
- Modify: `src/dms/build_manifests.py`
- Modify: `tests/test_build_manifests.py`

**Interfaces:**
- Consumes: Task 1 의 `PriorityClass/dms-build`, 현행 `build_build_pod` 시그니처(무변경).
- Produces (Task 3 프로브 공식이 상수를 공유하고, 「플랜 이후」 실증이 수치를 재보정한다):
  - 모듈 상수 `BUILD_SIZELIMIT_GIB = 10`, `BUILD_DISK_MARGIN_GIB = 2`.
  - 파드 spec: `priorityClassName: "dms-build"`, 컨테이너 `resources` = requests `{cpu 250m, memory 128Mi, ephemeral-storage 10Gi}` / limits `{cpu 1000m, memory 1Gi, ephemeral-storage 12Gi}`, emptyDir `sizeLimit: "10Gi"`. nodeSelector·schedulerName 미지정 유지.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_build_manifests.py` — 파일 끝에 추가:

```python
# ---- 슬라이스 21 §2.2/§2.3/§2.4: 리소스 봉투 + dms-build 클래스 + sizeLimit ----

def test_build_container_resource_envelope_pins_the_design_numbers():
    # 수치는 전부 워커 실측(allocatable 1800m/1355Mi/36.4GB) 역산이다(설계 §2.2):
    # - cpu limit 1000m 이 실질 보호막 -- 빌드가 날뛰어도 잡+제어면 몫 800m 이 남는다.
    # - memory requests 128Mi 는 일부러 작게 -- 실압박에서 빌드가 항상 "requests
    #   초과" 축출 그룹에 들어 ②priority(dms-build 10)가 방향을 가른다.
    # - memory limit 1Gi 는 노드 eviction 전에 빌드가 먼저 혼자 OOM-kill 되는
    #   의도된 1차 방어(M8 재발 방지).
    c = _pod()["spec"]["containers"][0]
    assert c["resources"] == {
        "requests": {"cpu": "250m", "memory": "128Mi", "ephemeral-storage": "10Gi"},
        "limits": {"cpu": "1000m", "memory": "1Gi", "ephemeral-storage": "12Gi"},
    }


def test_build_pod_uses_the_dms_build_priority_class():
    # 미적용 클러스터에서는 admission 거절이다 -- Task 1 매니페스트가 먼저 apply
    # 돼야 한다(플랜 이후 절의 배포 순서).
    assert _pod()["spec"]["priorityClassName"] == "dms-build"


def test_sizelimit_is_10gi_and_not_above_the_eph_limit():
    # §2.4: 20Gi 는 실측 여유(eviction 임계 차감 후 15.7GB)보다 커서 sizeLimit
    # 이전에 노드 압박 eviction(같은 노드 파드 전체가 후보)이 먼저 온다 -- 10Gi 로
    # 내려 레이어 폭주 시 빌드 파드만 축출되게 한다. eph limits(12Gi)는 emptyDir
    # 사용량 포함 파드 단위 집행이라 sizeLimit ≤ limits 여야 sizeLimit 이 먼저
    # 발화한다(관계 단언).
    pod = _pod()
    assert pod["spec"]["volumes"][0]["emptyDir"]["sizeLimit"] == "10Gi"
    eph_limit = pod["spec"]["containers"][0]["resources"]["limits"]["ephemeral-storage"]
    assert 10 <= int(eph_limit.removesuffix("Gi"))


def test_scheduling_shape_is_unchanged_nodeselector_and_default_scheduler():
    # §2.1: nodeSelector+default-scheduler 유지 -- requests 를 얹어 스케줄러
    # Fit(노드 여유 검사)을 공짜로 사고, 지정 노드 보장은 hostname nodeSelector 가
    # 그대로 한다. (현행 고정 가드 -- Step 2 에서 즉시 PASS 가 맞다.)
    pod = _pod()
    assert pod["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "dms-w1"}
    assert "schedulerName" not in pod["spec"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_build_manifests.py -q`
Expected: FAIL 3건 / 나머지 PASS — `test_build_container_resource_envelope_...` 와 `test_sizelimit_...` 이 `KeyError: 'resources'`, `test_build_pod_uses_...` 가 `KeyError: 'priorityClassName'`. (`test_sizelimit_...` 은 resources KeyError 전에 sizeLimit `assert '20Gi' == '10Gi'` 로 먼저 죽을 수도 있다 — 어느 쪽이든 FAIL 이 본질.) `test_scheduling_shape_...` 는 현행 고정 가드라 즉시 PASS. 기존 `test_containers_volume_has_a_size_limit` 는 truthy 단언이라 계속 PASS.

- [ ] **Step 3: build_manifests.py 를 고친다**

**(1)** `_SCRIPT = r"""..."""` 정의 **바로 아래**(43행 `def build_build_pod` 위)에 상수 추가:

```python
# §2.4: buildah 저장소 emptyDir sizeLimit(GiB). 실측 여유(dms-w1 fs 21.78GB -
# eviction 임계 15% ≈ 6.07GB → 15.7GB)보다 작아야, 레이어 폭주 시 노드 압박
# eviction(같은 노드 파드 전체가 축출 후보)이 아니라 sizeLimit 축출(빌드 파드만)이
# 먼저 온다. 프리플라이트 프로브의 디스크 공식(§2.5)도 이 상수를 공유한다 --
# 한쪽만 바꾸면 "프리플라이트는 통과했는데 빌드가 노드를 위협"하는 갈라짐이 생긴다.
# 10Gi 는 현 노드 여유 공식을 통과하는 최대 봉투이며 아직 미측정치다 -- 실증(§6-2)
# 이 du 로 실제 피크를 재서 재보정한다.
BUILD_SIZELIMIT_GIB = 10
# 프리플라이트 디스크 공식의 여유 마진(GiB) -- 빌드 중 같은 노드 다른 파드의
# 로그·쓰기층 몫. eph limits = sizeLimit + 마진(12Gi)으로도 쓰인다.
BUILD_DISK_MARGIN_GIB = 2
```

**(2)** `build_build_pod` 의 `"spec": {...}` 블록 전체(`"restartPolicy": "Never",` 부터 emptyDir 볼륨까지)를 다음으로 교체:

```python
        "spec": {
            "restartPolicy": "Never",
            "activeDeadlineSeconds": timeout_seconds,
            "nodeSelector": {"kubernetes.io/hostname": node},
            # §2.3: 값 10 < dms-low 50 -- kubelet 축출 랭킹 ②priority 에서 빌드가
            # 어떤 데이터 잡보다 먼저 죽는다. 미적용 클러스터에서는 admission
            # 거절이므로 05-volcano-queue-priorityclass.yaml 을 먼저 apply 할 것.
            "priorityClassName": "dms-build",
            "containers": [{
                "name": "build", "image": builder_image,
                "command": ["sh", "-c", _SCRIPT],
                "env": [{"name": k, "value": v} for k, v in env.items()],
                "securityContext": {"privileged": True},
                # §2.2 실측 역산 봉투 -- requests 로 스케줄러 Fit(노드 여유 검사)을
                # 사고, limits 가 노드를 지킨다:
                # - cpu 1000m: 최혼잡 워커(w2 425m)에서도 675m ≤ 1800m 라 스케줄이
                #   막히지 않고, 빌드가 날뛰어도 잡+제어면 몫 800m 이 남는다.
                # - memory requests 128Mi 를 일부러 작게: 실압박에서 빌드가 항상
                #   "requests 초과" 축출 그룹에 들어 dms-build(10)가 방향을 가른다.
                # - memory limit 1Gi: 노드 eviction 전에 빌드가 먼저, 혼자
                #   OOM-kill 되는 의도된 1차 방어(M8 재현 방지). npm(vite) 빌드가
                #   1Gi 안에서 도는지는 실증(§6-2)이 확정한다.
                # - eph limits 12Gi ≥ sizeLimit 10Gi: limits 는 emptyDir 포함
                #   파드 단위 집행 -- sizeLimit 이 레이어 상한, limits 가 로그·
                #   쓰기층 오버플로 캐치다(§2.4 3중 방어의 ②③).
                "resources": {
                    "requests": {"cpu": "250m", "memory": "128Mi",
                                 "ephemeral-storage": f"{BUILD_SIZELIMIT_GIB}Gi"},
                    "limits": {"cpu": "1000m", "memory": "1Gi",
                               "ephemeral-storage":
                                   f"{BUILD_SIZELIMIT_GIB + BUILD_DISK_MARGIN_GIB}Gi"},
                },
                "volumeMounts": [{"name": "containers", "mountPath": "/var/lib/containers"}],
            }],
            # emptyDir 이므로 kubelet ephemeral-storage 회계 안이다(§1-2). 수치
            # 근거는 위 BUILD_SIZELIMIT_GIB 주석.
            "volumes": [{"name": "containers",
                        "emptyDir": {"sizeLimit": f"{BUILD_SIZELIMIT_GIB}Gi"}}],
        },
```

(기존 volumeMounts 위의 20Gi 경험적 상한 주석 블록은 이 교체로 삭제된다 — 근거가 상수 주석으로 이동했다.)

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_build_manifests.py tests/test_build_runner.py tests/test_build_watcher.py tests/test_api_builds.py -q`
Expected: 전부 PASS (러너/워처/API 는 매니페스트 dict 의 새 키에 무관심 — 무변경이어야 한다)

- [ ] **Step 5: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21
git add src/dms/build_manifests.py tests/test_build_manifests.py
git commit -m "feat(build): 빌드 파드 봉투 — cpu 250m/1000m·mem 128Mi/1Gi·eph 10Gi/12Gi + dms-build + sizeLimit 10Gi"
```

---

### Task 3: 프리플라이트 프로브 — 매니페스트 + submit_preflight + 설정 키

**Files:**
- Modify: `src/dms/build_manifests.py`(repo_host·프로브 스크립트·프로브 파드), `src/dms/repositories/builds.py`(`build_probe_pod_name`), `src/dms/build_runner.py`(BuildRunner 생성자·`submit_preflight`·Stub), `src/dms/wiring.py`, `src/dms/config.py`, `deploy/k8s/20-config.yaml`
- Modify: `tests/test_build_manifests.py`, `tests/test_build_runner.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: Task 2 의 `BUILD_SIZELIMIT_GIB`/`BUILD_DISK_MARGIN_GIB`, `BUILD_REF_PREFIX`(`build_runner.py:14`), AlreadyExists 관용 선례(`build_runner.py:47-63`), 마커 관례(`execution_manifests.py:256-291`).
- Produces (Task 4 워처·Task 5 라우트·Task 6 GC 가 이 이름·모양을 그대로 쓴다):
  - `repositories.builds.build_probe_pod_name(build_id: str) -> str` — `f"dms-build-pf-{build_id[:12]}"[:63]`.
  - `build_manifests.repo_host(repo_url: str) -> str | None` — urlsplit hostname, 파싱 불가 None.
  - `build_manifests.build_probe_pod(*, build_id, repo_url, node, namespace, registry, job_image, timeout_seconds) -> dict` — 파싱 불가 repo_url 이면 `ValueError`.
  - `BuildRunner.__init__(k8s, *, namespace, registry, builder_image, timeout_seconds, job_image="", preflight_timeout_seconds=180)` — 기존 키워드는 그대로, 신규 2개는 기본값(기존 테스트 무변경).
  - `BuildRunner.submit_preflight(build) -> str`(ref `buildpod/dms-build-pf-…`, AlreadyExists 관용, 실패 `ExecutionError("submit_failed", "preflight: …")`), `StubBuildRunner.submit_preflight(build) -> str`(즉시 OK 로그 시딩).
  - `Settings.build_preflight_timeout_seconds: int = 180`(`DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS`).
  - 프로브 성공 마커 `DMS_PREFLIGHT_OK`, 실패 마커 `DMS_PREFLIGHT_REASON=<code>` — code ∈ {`build_node_no_egress`, `build_registry_unreachable`, `build_node_disk_low`}.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_build_manifests.py` — 파일 머리 import 를 다음으로 교체:

```python
import re

from dms.build_manifests import build_build_pod, build_probe_pod, repo_host
```

파일 끝에 추가:

```python
# ---- 슬라이스 21 §2.5: 적합성 프로브 파드 (매니페스트는 순수 함수) ----

def _probe(repo_url="https://github.com/ChahwanSong/dms.git", timeout_seconds=180):
    return build_probe_pod(build_id=BID, repo_url=repo_url, node="dms-w1",
                           namespace="dms", registry="pkg-01:5000",
                           job_image="pkg-01:5000/dms-mpifileutils:d27",
                           timeout_seconds=timeout_seconds)


def test_probe_identity_small_envelope_and_class():
    pod = _probe()
    # 결정적 이름 + 63자 상한: 워처가 상태를 DB 에 두지 않고도 "이 빌드의 프로브"를
    # 언제든 다시 찾는 근거다(buildpod/ ref 재사용 -- poll/read_log/terminate 공짜).
    assert pod["metadata"]["name"] == "dms-build-pf-0123456789ab"
    assert len(pod["metadata"]["name"]) <= 63
    assert pod["metadata"]["labels"]["dms.io/build-id"] == BID
    assert pod["spec"]["restartPolicy"] == "Never"
    assert pod["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "dms-w1"}
    assert pod["spec"]["priorityClassName"] == "dms-build"   # 빌드와 같은 축출 방향
    assert pod["spec"]["activeDeadlineSeconds"] == 180
    c = pod["spec"]["containers"][0]
    # job_image(캐시 존재·pull 은 pkg-01 만 필요)여야 프로브 기동 자체가 인터넷과
    # 무관하다 -- builder image(quay.io)면 위음성/위양성이 난다(설계 §2.5).
    assert c["image"] == "pkg-01:5000/dms-mpifileutils:d27"
    # 작은 봉투: 소켓 3~4개와 statvfs 뿐 -- 프로브가 노드에 압박을 만들면 안 된다.
    assert c["resources"] == {"requests": {"cpu": "50m", "memory": "32Mi"},
                              "limits": {"cpu": "200m", "memory": "128Mi"}}


def test_probe_targets_travel_as_env_not_in_the_script():
    c = _probe()["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    # repo 호스트(파싱) + quay.io(빌더 이미지) + registry-1.docker.io(베이스 이미지
    # -- Dockerfile.dms:13,24 / Dockerfile.mpifileutils:17 이 전부 docker.io).
    assert env["DMS_PF_EGRESS_HOSTS"] == "github.com quay.io registry-1.docker.io"
    assert env["DMS_PF_REGISTRY"] == "pkg-01:5000"
    assert env["DMS_PF_NEED_BYTES"] == str(12 * 1024 ** 3)   # sizeLimit 10Gi + 마진 2Gi
    assert c["command"][:2] == ["python3", "-c"]
    script = c["command"][2]
    # 값이 스크립트 본문에 박히면 repo_url 이 코드가 된다(빌드 파드와 같은 원칙).
    assert "github.com" not in script
    assert "pkg-01:5000" not in script


def test_probe_script_follows_the_preflight_marker_convention():
    # execution_manifests._preflight_script 와 같은 마커 문법(실패 = REASON= + exit 1,
    # 성공 = OK) -- 워처가 한 가지 파서 계열로 읽는다.
    script = _probe()["spec"]["containers"][0]["command"][2]
    assert "DMS_PREFLIGHT_REASON=" in script
    assert "DMS_PREFLIGHT_OK" in script
    for code in ("build_node_no_egress", "build_registry_unreachable",
                 "build_node_disk_low"):
        assert code in script, code
    assert "os.statvfs" in script and "0.15" in script   # eviction 미러 상수


def test_probe_host_list_dedups_the_repo_host():
    env = {e["name"]: e["value"]
           for e in _probe(repo_url="https://quay.io/x.git")["spec"]["containers"][0]["env"]}
    assert env["DMS_PF_EGRESS_HOSTS"] == "quay.io registry-1.docker.io"


def test_repo_host_parses_https_and_rejects_unparseable():
    assert repo_host("https://github.com/ChahwanSong/dms.git") == "github.com"
    assert repo_host("http://pkg-01:8080/r.git") == "pkg-01"
    assert repo_host("not a url") is None
    assert repo_host("") is None
    # scp 형(git@host:path)은 urlsplit 이 호스트를 못 뽑는다 -- 라우트가 422
    # invalid_repo_url 로 명시 거절한다(조용한 오동작 대신).
    assert repo_host("git@github.com:ChahwanSong/dms.git") is None
```

**(2)** `tests/test_build_runner.py` — 파일 끝에 추가:

```python
# ---- 슬라이스 21 §2.5: submit_preflight (프로브 파드 멱등 제출) ----

def _pf_runner(k8s):
    return BuildRunner(k8s, namespace="dms", registry="pkg-01:5000",
                       builder_image="quay.io/buildah/stable:latest",
                       timeout_seconds=7200,
                       job_image="pkg-01:5000/dms-mpifileutils:d27",
                       preflight_timeout_seconds=180)


PF_BUILD = {**BUILD, "repo_url": "https://github.com/ChahwanSong/dms.git"}


def test_submit_preflight_creates_probe_pod_under_the_buildpod_ref():
    k8s = _FakeK8s()
    runner = _pf_runner(k8s)
    ref = runner.submit_preflight(PF_BUILD)
    assert ref == f"{BUILD_REF_PREFIX}/dms-build-pf-0123456789ab"
    pod = k8s.created[0]
    assert pod["metadata"]["name"] == "dms-build-pf-0123456789ab"
    assert pod["spec"]["containers"][0]["image"] == "pkg-01:5000/dms-mpifileutils:d27"
    assert pod["spec"]["activeDeadlineSeconds"] == 180
    # 같은 buildpod/ ref 계약이라 poll/read_log/terminate 가 공짜다 -- 이게
    # 프로브에 별도 러너를 만들지 않은 이유다.
    k8s.set_status("dms-build-pf-0123456789ab", {"phase": "Succeeded"})
    assert runner.poll(ref) == ExecStatus.SUCCEEDED


def test_submit_preflight_is_idempotent_when_probe_already_exists():
    # 워처가 매 틱 재호출한다 -- AlreadyExists 를 실패로 접으면 두 번째 틱부터
    # 멀쩡한 빌드가 전부 Failed 다(submit 의 관용 선례와 같은 계약).
    k8s = _FakeK8s()
    ref1 = _pf_runner(k8s).submit_preflight(PF_BUILD)
    k8s.fail_create = True
    ref2 = _pf_runner(k8s).submit_preflight(PF_BUILD)
    assert ref2 == ref1


def test_submit_preflight_unparseable_repo_url_is_submit_failed():
    # 라우트가 제출 시점에 invalid_repo_url 로 거르지만(§2.5 동기), 검증 전에
    # 만들어진 구형 Pending 행이 남아 있을 수 있다 -- 원시 ValueError 가 아니라
    # ExecutionError(submit_failed)로 나와야 워처가 Failed 로 기록한다.
    with pytest.raises(ExecutionError) as e:
        _pf_runner(_FakeK8s()).submit_preflight({**BUILD, "repo_url": "not a url"})
    assert e.value.reason_code == "submit_failed"
    assert e.value.detail.startswith("preflight:")   # 빌드 파드 제출 실패와 구분


def test_submit_preflight_create_failure_without_existing_pod_raises():
    k8s = _FakeK8s()
    k8s.fail_create = True
    with pytest.raises(ExecutionError) as e:
        _pf_runner(k8s).submit_preflight(PF_BUILD)
    assert e.value.reason_code == "submit_failed"


def test_stub_submit_preflight_is_immediately_ok_without_a_cluster():
    # 스텁 경로 계약(설계 §4): 프리플라이트 포함 즉시 성공 -- poll 은 어떤 ref 든
    # SUCCEEDED 이므로 OK 마커 로그만 있으면 워처가 같은 틱에 빌드 제출로 넘어간다.
    stub = StubBuildRunner()
    ref = stub.submit_preflight(BUILD)
    assert ref == f"{BUILD_REF_PREFIX}/dms-build-pf-0123456789ab"
    assert stub.poll(ref) == ExecStatus.SUCCEEDED
    assert "DMS_PREFLIGHT_OK" in stub.read_log(ref)
```

**(3)** `tests/test_config.py` — 파일 끝에 추가:

```python
def test_build_preflight_timeout_default_and_env_override():
    # 슬라이스 21 §2.5: 프로브 대기 상한. _SERVER_INT_KEYS 튜플에만 넣으면
    # from_env 의 **extra 가 배선한다 -- 필드/키 양쪽이 실제로 이어졌는지 고정.
    assert Settings.from_env(VALID).build_preflight_timeout_seconds == 180
    s = Settings.from_env({**VALID, "DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS": "60"})
    assert s.build_preflight_timeout_seconds == 60
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_build_manifests.py tests/test_build_runner.py tests/test_config.py -q`
Expected: `tests/test_build_manifests.py` 가 수집 단계 에러 — `ImportError: cannot import name 'build_probe_pod' from 'dms.build_manifests'`. `tests/test_build_runner.py` 신규 5건 FAIL — `TypeError: BuildRunner.__init__() got an unexpected keyword argument 'job_image'`, 스텁 테스트는 `AttributeError: 'StubBuildRunner' object has no attribute 'submit_preflight'`. `tests/test_config.py` 신규 1건 FAIL — `AttributeError: 'Settings' object has no attribute 'build_preflight_timeout_seconds'`.

- [ ] **Step 3: 결정적 프로브 이름을 만든다**

`src/dms/repositories/builds.py` — `build_pod_name`(`:20-21`) **바로 아래**에 추가:

```python
def build_probe_pod_name(build_id: str) -> str:
    # 슬라이스 21 §2.5: 적합성 프로브 파드. 빌드 파드와 같은 결정적 이름 규칙이라
    # 워처가 프로브 상태를 DB 에 두지 않고도(컬럼 0개) "이 빌드의 프로브"를 매 틱
    # 다시 찾을 수 있다 -- 멱등 create + poll 만으로 상태기계가 성립한다.
    # "pf" 세그먼트는 hex(build_id)와 절대 충돌하지 않아 빌드 파드와 판별 가능하다.
    return f"dms-build-pf-{build_id[:12]}"[:63]
```

- [ ] **Step 4: build_manifests.py 에 repo_host·프로브를 만든다**

**(1)** 파일 머리 import 를 다음으로 교체:

```python
"""빌드 파드 매니페스트. 순수 함수 -- k8s 클라이언트에 접근하지 않는다."""
from urllib.parse import urlsplit

from .repositories.builds import (BUILD_IMAGES, build_pod_name,
                                  build_probe_pod_name, build_tag)
```

**(2)** Task 2 에서 추가한 상수 블록 **바로 아래**에 추가:

```python
def repo_host(repo_url: str) -> str | None:
    """repo_url 에서 egress 프로브 대상 호스트를 뽑는다. 파싱 불가면 None.

    라우트(제출 시 422 invalid_repo_url)와 프로브 매니페스트가 **같은 함수**를
    쓴다 -- 두 곳이 다르게 파싱하면 "제출은 통과했는데 프로브를 못 만드는" 창이
    생긴다. scp 형(git@host:path)은 urlsplit 이 호스트를 못 뽑아 None 이다 --
    지원 확대가 아니라 명시 거절이 목적이다(테스트베드는 https 만 쓴다)."""
    try:
        return urlsplit(repo_url or "").hostname
    except ValueError:
        # 잘못된 IPv6 브래킷 등 urlsplit 자체가 던지는 경우 -- 파싱 불가와 동치.
        return None


# 프리플라이트 프로브 스크립트(§2.5). 실행 preflight 의 마커 관례를 그대로 따른다
# (execution_manifests._preflight_script: 실패 = DMS_PREFLIGHT_REASON=<code> +
# exit 1, 성공 = DMS_PREFLIGHT_OK) -- 워처가 같은 파서 계열로 읽는다. 대상
# 호스트·수치는 전부 env 로 나른다(빌드 스크립트와 같은 인젝션 회피 원칙: 값이
# 본문에 박히면 repo_url 이 코드가 된다). 이미지는 job_image(python:3.11-slim
# 기반 -- Dockerfile.mpifileutils:81 -- 이라 python3 보장, 워커 캐시 존재, pull 도
# pkg-01 만 필요) -- 프로브 기동 자체가 인터넷과 무관해야 "인터넷만 없는 노드"를
# 정확히 판별한다.
_PROBE_SCRIPT = r"""
import os
import socket
import sys


def reachable(host, port):
    # TCP 연결만 본다(각 5s): 운영 모델의 질문이 "인터넷이 열렸는가"라는 이진
    # 질문이기 때문이다. 선별 개방(예: github 만)이면 여기를 통과하고 npm 에서
    # 죽는다 -- 그건 기존대로 build_failed + 로그의 몫이다(설계 §2.5 정직한 한계).
    try:
        with socket.create_connection((host, port), timeout=5.0):
            return True
    except OSError:
        return False


def fail(reason, detail):
    # 마커보다 detail 을 먼저 찍는다 -- 로그 꼬리 박제(64KB)에서 마커가 잘리는
    # 것보다 detail 이 잘리는 편이 낫다(마커가 없으면 build_preflight_failed 로
    # 접혀 사유가 뭉개진다).
    print(detail)
    print("DMS_PREFLIGHT_REASON=" + reason)
    sys.exit(1)


egress_hosts = os.environ["DMS_PF_EGRESS_HOSTS"].split()
unreachable = [h for h in egress_hosts if not reachable(h, 443)]
if unreachable:
    # 실패 호스트 전부를 로그로 -- "어느 호스트가 막혔나"가 운영자의 첫 질문이다.
    fail("build_node_no_egress", "unreachable_443=" + ",".join(unreachable))

registry = os.environ["DMS_PF_REGISTRY"]
reg_host, _, reg_port = registry.partition(":")
if not reachable(reg_host, int(reg_port or "443")):
    fail("build_registry_unreachable", "unreachable_registry=" + registry)

# 노드 fs 여유 검사: 컨테이너 overlay 의 "/" 는 노드 fs 를 그대로 보고한다
# (nodefs=imagefs 동일 실측). 0.15 는 kubelet evictionHard(imagefs 15%, 2026-08-11
# configz 실측)의 미러 상수다 -- kubelet 설정이 바뀌면 여기도 같이 갱신할 것.
# NEED_BYTES = sizeLimit(10Gi) + 마진(2Gi) -- build_manifests 상수에서 온다.
st = os.statvfs("/")
avail = st.f_bavail * st.f_frsize
total = st.f_blocks * st.f_frsize
need = int(0.15 * total) + int(os.environ["DMS_PF_NEED_BYTES"])
if avail < need:
    fail("build_node_disk_low",
         "avail_bytes=%d need_bytes=%d total_bytes=%d" % (avail, need, total))
print("disk avail_bytes=%d need_bytes=%d" % (avail, need))
print("DMS_PREFLIGHT_OK")
"""

# 고정 egress 대상(§2.5-①): quay.io 는 빌더 이미지(kubelet 이 pull), docker.io
# 베이스 이미지(node:20-bookworm-slim -- Dockerfile.dms:13, python:3.11-slim-bookworm
# -- Dockerfile.dms:24 / Dockerfile.mpifileutils:81, debian:bookworm --
# Dockerfile.mpifileutils:17)는 registry-1.docker.io 에서 온다.
_PROBE_STATIC_HOSTS = ("quay.io", "registry-1.docker.io")


def build_probe_pod(*, build_id, repo_url, node, namespace, registry, job_image,
                    timeout_seconds) -> dict:
    host = repo_host(repo_url)
    if not host:
        # 라우트가 제출 시점에 invalid_repo_url 로 거른다 -- 여기 도달은 검증 전에
        # 만들어진 구형 Pending 행뿐이고, BuildRunner 가 submit_failed 로 접는다.
        raise ValueError(f"cannot parse repo host from {repo_url!r}")
    hosts = [host] + [h for h in _PROBE_STATIC_HOSTS if h != host]
    env = {
        "DMS_PF_EGRESS_HOSTS": " ".join(hosts),
        "DMS_PF_REGISTRY": registry,
        # 빌드 파드 봉투와 같은 상수(§2.4) -- 프리플라이트가 통과한 노드에서
        # sizeLimit 이 반드시 담길 수 있어야 두 방어가 한 공식이 된다.
        "DMS_PF_NEED_BYTES": str((BUILD_SIZELIMIT_GIB + BUILD_DISK_MARGIN_GIB)
                                 * 1024 ** 3),
    }
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": build_probe_pod_name(build_id), "namespace": namespace,
                     "labels": {"dms.io/build-id": build_id,
                                "dms.io/phase": "build-preflight"}},
        "spec": {
            "restartPolicy": "Never",
            # 프로브 자체의 벽시계 상한 -- 워처의 프리플라이트 타임아웃과 같은 값.
            # 단 activeDeadlineSeconds 는 스케줄 후에만 발화하므로(§1-9) 영구
            # Pending 프로브는 워처의 created_at 기반 회수만 잡는다.
            "activeDeadlineSeconds": timeout_seconds,
            "nodeSelector": {"kubernetes.io/hostname": node},
            # 빌드와 같은 클래스(§2.3): 프로브도 데이터 잡보다 먼저 죽고 아무도
            # 선점하지 않는다. 미적용 클러스터에서는 admission 거절 -- 배포 순서 참고.
            "priorityClassName": "dms-build",
            "containers": [{
                "name": "preflight", "image": job_image,
                "command": ["python3", "-c", _PROBE_SCRIPT],
                "env": [{"name": k, "value": v} for k, v in env.items()],
                # 작은 봉투: 소켓 3~4개와 statvfs 뿐이다 -- 프로브가 노드에
                # 유의미한 압박을 만들면 검사가 검사 대상을 오염시킨다.
                "resources": {"requests": {"cpu": "50m", "memory": "32Mi"},
                              "limits": {"cpu": "200m", "memory": "128Mi"}},
            }],
        },
    }
```

- [ ] **Step 5: BuildRunner/StubBuildRunner 에 submit_preflight 를 만든다**

`src/dms/build_runner.py` — **(1)** import 를 다음으로 교체:

```python
from .build_manifests import build_build_pod, build_probe_pod
from .execution import ExecStatus, ExecutionError
from .repositories.builds import build_pod_name, build_probe_pod_name
```

**(2)** `BuildRunner.__init__` 을 다음으로 교체:

```python
    def __init__(self, k8s, *, namespace, registry, builder_image, timeout_seconds,
                 job_image="", preflight_timeout_seconds=180):
        self._k8s = k8s
        self._ns = namespace
        self._registry = registry
        self._builder_image = builder_image
        self._timeout_seconds = timeout_seconds
        # 슬라이스 21 §2.5: 프로브는 builder image 가 아니라 job_image 로 띄운다 --
        # 워커에 캐시돼 있고 pull 도 pkg-01 만 필요해 프로브 기동이 인터넷과
        # 무관하다. 기본값은 기존 생성 호출(테스트 다수) 무변경용이고, 실제 배선
        # (wiring.py)은 항상 settings 값을 명시한다.
        self._job_image = job_image
        self._preflight_timeout_seconds = preflight_timeout_seconds
```

**(3)** `submit` 메서드 **바로 아래**에 추가:

```python
    def submit_preflight(self, build) -> str:
        """적합성 프로브 파드(§2.5)를 멱등 제출한다. submit 과 같은 계약: 이름이
        build_id 에서 결정적이라 AlreadyExists 는 이전 틱이 만든 이 빌드 자신의
        프로브다 -- 성공 취급해야 워처의 매 틱 재호출("없음→생성"과 "진행 중→대기"
        를 상태 저장 없이 한 호출로 접는 장치)이 안전하다."""
        try:
            manifest = build_probe_pod(
                build_id=build["build_id"], repo_url=build["repo_url"],
                node=build["node_name"], namespace=self._ns,
                registry=self._registry, job_image=self._job_image,
                timeout_seconds=self._preflight_timeout_seconds)
        except Exception as exc:
            # 프로브 생성 실패는 기존 submit_failed 재사용(§4) -- "preflight:"
            # detail 접두가 빌드 파드 제출 실패와 구분한다(새 코드를 만들지 않는다).
            raise ExecutionError("submit_failed",
                                 f"preflight: {str(exc)[:180]}") from exc
        name = manifest["metadata"]["name"]
        ref = f"{BUILD_REF_PREFIX}/{name}"
        try:
            self._k8s.create(manifest)
        except Exception as exc:
            # submit 의 AlreadyExists 관용 선례 그대로: 존재하면 성공 취급.
            try:
                exists = self._k8s.get("Pod", name, self._ns) is not None
            except Exception:
                exists = False
            if not exists:
                raise ExecutionError("submit_failed",
                                     f"preflight: {str(exc)[:180]}") from exc
        return ref
```

**(4)** `StubBuildRunner.submit` **바로 아래**에 추가:

```python
    def submit_preflight(self, build) -> str:
        # 클러스터 없는 경로도 프리플라이트를 "즉시 통과"로 지나가야 한다(설계 §4:
        # 로컬·CI 가 클러스터 없이 초록) -- poll 은 어떤 ref 든 SUCCEEDED 이므로
        # OK 마커 로그만 심으면 워처가 같은 틱에 빌드 제출로 넘어간다.
        ref = f"{BUILD_REF_PREFIX}/{build_probe_pod_name(build['build_id'])}"
        self._log[ref] = "DMS_PREFLIGHT_OK\n"
        return ref
```

- [ ] **Step 6: 설정 키와 배선을 잇는다**

**(1)** `src/dms/config.py` — `_SERVER_INT_KEYS` 의 `("DMS_BUILD_TIMEOUT_SECONDS", "build_timeout_seconds", 7200),` 항목 **바로 아래**에 추가:

```python
    # 슬라이스 21 §2.5: 적합성 프로브(프리플라이트 파드) 대기 상한. 프로브는
    # 캐시된 job_image 로 수 초면 종단한다 -- 이 창을 넘기면 노드 다운/스케줄
    # 불가로 보고 build_preflight_timeout 으로 즉시 회수한다(2h generic 대기를
    # 수 분으로 줄이는 것이 이 슬라이스의 존재 이유다).
    ("DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS", "build_preflight_timeout_seconds", 180),
```

**(2)** 같은 파일 — `Settings` 데이터클래스의 `build_timeout_seconds: int = 7200` **바로 아래**에 추가:

```python
    build_preflight_timeout_seconds: int = 180
```

**(3)** `src/dms/wiring.py` — `build_build_runner` 의 return 을 다음으로 교체:

```python
    return BuildRunner(KubernetesClient(settings.k8s_namespace),
                       namespace=settings.k8s_namespace,
                       registry=settings.build_registry,
                       builder_image=settings.build_builder_image,
                       timeout_seconds=settings.build_timeout_seconds,
                       # 프로브는 job_image(§2.5): 워커 캐시 존재 + pull 은
                       # pkg-01 만 필요 -- 프로브 기동이 인터넷과 무관해야
                       # "인터넷만 없는 노드"를 정확히 판별한다.
                       job_image=settings.job_image,
                       preflight_timeout_seconds=settings.build_preflight_timeout_seconds)
```

**(4)** `deploy/k8s/20-config.yaml` — `DMS_BUILD_TIMEOUT_SECONDS: "7200"` 줄 **바로 아래**에 추가:

```yaml
  # 슬라이스 21: 빌드 적합성 프리플라이트(프로브 파드) 대기 상한(초). 프로브는
  # 캐시된 DMS_JOB_IMAGE 로 수 초면 끝난다 -- 이 창을 넘기면 노드 다운/스케줄
  # 불가로 보고 build_preflight_timeout 으로 즉시 회수한다(위 2h generic 타임아웃
  # 을 기다리지 않는다). 포탈 Pending 캡션의 "최대 약 3분"이 이 값이다.
  DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS: "180"
```

- [ ] **Step 7: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_build_manifests.py tests/test_build_runner.py tests/test_config.py tests/test_builds_repo.py tests/test_api_builds.py tests/test_wiring_phase3c.py -q`
Expected: 전부 PASS (기존 BuildRunner 호출부는 신규 키워드 기본값으로 무변경, StubBuildRunner 의 기존 계약도 그대로)

- [ ] **Step 8: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21
git add src/dms/build_manifests.py src/dms/repositories/builds.py src/dms/build_runner.py src/dms/wiring.py src/dms/config.py deploy/k8s/20-config.yaml tests/test_build_manifests.py tests/test_build_runner.py tests/test_config.py
git commit -m "feat(build): 적합성 프로브 — egress/레지스트리/디스크 3종 python3 검사 + submit_preflight 멱등 제출 + DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS"
```

---
### Task 4: 워처 상태기계 — 프로브 생성/대기/채택/타임아웃 + build_stuck_pending

**Files:**
- Modify: `src/dms/build_watcher.py`(전체 교체), `src/dms/controller.py`
- Modify: `tests/test_build_watcher.py`
- Modify: `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts` (같은 커밋 — 계약 조건)

**Interfaces:**
- Consumes: Task 3 의 `submit_preflight`/`build_probe_pod_name`/`Settings.build_preflight_timeout_seconds`, 마커 `DMS_PREFLIGHT_OK`·`DMS_PREFLIGHT_REASON=`, `builds.finish` COALESCE·64KB(`builds.py:122-133`).
- Produces (Task 6 GC·Task 7 프론트가 이 코드에 의존한다):
  - `BuildWatcher.__init__(repos, runner, *, timeout_seconds=None, preflight_timeout_seconds=None)` — None 은 해당 회수 비활성(기존 하위호환 규칙과 동일).
  - `build_watcher.parse_preflight_reason(log_text) -> str | None`.
  - 신설 사유 코드 6종이 builds.reason_code 로 박제된다: `build_preflight_timeout`, `build_preflight_failed`, `build_stuck_pending`, `build_node_no_egress`, `build_registry_unreachable`, `build_node_disk_low`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_build_watcher.py` — **(1)** 파일 머리 import 를 다음으로 교체:

```python
import pytest
from dms.build_runner import BUILD_REF_PREFIX, StubBuildRunner
from dms.build_watcher import BuildWatcher, parse_commit_sha, parse_preflight_reason
from dms.db import Database, iso_plus, utc_now_iso
from dms.execution import ExecStatus, ExecutionError
from dms.migrations import migrate
from dms.repositories import Repositories
```

**(2)** `_Runner` 클래스 전체를 다음으로 교체:

```python
class _Runner:
    """빌드 파드(dms-build-<hex>)와 프로브 파드(dms-build-pf-<hex>)를 ref 로
    판별하는 페어. build_id 는 uuid4 hex 라 'pf' 세그먼트와 절대 충돌하지 않는다."""

    def __init__(self, status=ExecStatus.SUCCEEDED, log="DMS_COMMIT_SHA=deadbeef\n",
                 probe_status=ExecStatus.SUCCEEDED, probe_log="DMS_PREFLIGHT_OK\n"):
        self.status = status
        self.log = log
        self.probe_status = probe_status
        self.probe_log = probe_log
        self.submitted = []
        self.probe_submitted = []
        self.polled = []
        self.terminated = []
        self.fail_submit = None
        self.fail_submit_preflight = None
        self.fail_poll = None  # I6: reason_code로 세팅하면 poll에서 ExecutionError
        self.fail_read_log = None  # reason_code로 세팅하면 read_log에서 ExecutionError

    def _is_probe(self, ref):
        return "/dms-build-pf-" in ref

    def submit(self, build):
        if self.fail_submit:
            raise ExecutionError(self.fail_submit, "nope")
        self.submitted.append(build["build_id"])
        return f"{BUILD_REF_PREFIX}/dms-build-{build['build_id'][:12]}"

    def submit_preflight(self, build):
        if self.fail_submit_preflight:
            raise ExecutionError(self.fail_submit_preflight, "preflight: nope")
        self.probe_submitted.append(build["build_id"])
        return f"{BUILD_REF_PREFIX}/dms-build-pf-{build['build_id'][:12]}"

    def poll(self, ref):
        self.polled.append(ref)
        if self.fail_poll:
            raise ExecutionError(self.fail_poll, "transient")
        return self.probe_status if self._is_probe(ref) else self.status

    def read_log(self, ref):
        if self.fail_read_log:
            raise ExecutionError(self.fail_read_log, "boom")
        return self.probe_log if self._is_probe(ref) else self.log

    def terminate(self, ref):
        self.terminated.append(ref)
```

**(3)** `test_running_build_past_deadline_is_terminated_and_marked_timeout` 안의 두 줄

```python
    assert runner.terminated == [f"{BUILD_REF_PREFIX}/dms-build-{bid[:12]}"]
    assert runner.polled == []  # 마감 넘긴 빌드는 poll을 부르지 않고 바로 회수한다
```

을 다음으로 교체:

```python
    assert runner.terminated == [f"{BUILD_REF_PREFIX}/dms-build-{bid[:12]}"]
    # 슬라이스 21 §2.1: 회수 전에 딱 한 번 poll 해 PENDING(스케줄 불가)을 구분한다.
    # 이 테스트의 파드는 RUNNING 이므로 generic build_timeout 이 맞다.
    assert runner.polled == [f"{BUILD_REF_PREFIX}/dms-build-{bid[:12]}"]
```

**(4)** 파일 끝에 추가:

```python
# ---- 슬라이스 21 §2.5: 프리플라이트 상태기계 (Pending -> 프로브 -> 빌드 제출) ----

def test_pending_build_creates_probe_and_waits_while_probe_runs(repos):
    bid = _mk(repos)
    runner = _Runner(probe_status=ExecStatus.RUNNING)
    out = BuildWatcher(repos, runner).run_once()
    assert runner.probe_submitted == [bid]
    assert runner.submitted == []            # 프로브가 끝나기 전엔 빌드 제출 금지
    assert repos.builds.get(bid)["state"] == "Pending"
    assert out == {"submitted": 0, "finished": 0}


def test_probe_success_with_ok_marker_submits_build_and_marks_running(repos):
    bid = _mk(repos)
    runner = _Runner(status=ExecStatus.RUNNING)   # 프로브 OK, 빌드 파드는 아직 돈다
    out = BuildWatcher(repos, runner).run_once()
    assert runner.probe_submitted == [bid]
    assert runner.submitted == [bid]
    assert repos.builds.get(bid)["state"] == "Running"
    assert out["submitted"] == 1


def test_probe_failure_adopts_whitelisted_marker_and_freezes_probe_log(repos):
    bid = _mk(repos)
    log = ("unreachable_443=github.com,quay.io\n"
           "DMS_PREFLIGHT_REASON=build_node_no_egress\n")
    runner = _Runner(probe_status=ExecStatus.FAILED, probe_log=log)
    out = BuildWatcher(repos, runner).run_once()
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_node_no_egress")
    # 실패 호스트 목록이 /log 로 보인다 -- "어느 호스트가 막혔나"가 첫 질문이다.
    assert "unreachable_443=github.com" in row["log_text"]
    assert runner.submitted == []
    assert out["finished"] == 1


def test_probe_marker_outside_whitelist_folds_to_build_preflight_failed(repos):
    # 파드 로그는 신뢰 입력이 아니다(설계 §4) -- 임의 문자열이 사유 코드로 승격되면
    # 프론트 매핑에 없는 코드가 지어내진다.
    bid = _mk(repos)
    runner = _Runner(probe_status=ExecStatus.FAILED,
                     probe_log="DMS_PREFLIGHT_REASON=totally_made_up\n")
    BuildWatcher(repos, runner).run_once()
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_preflight_failed")


def test_probe_failure_without_any_marker_is_build_preflight_failed(repos):
    # 프로브가 스케줄은 됐는데 로그 없이 죽은 경우(OOM 등) -- 코드를 지어내지 않고
    # 접는다. log_text=None 은 COALESCE 라 기존 값을 지우지 않는다.
    bid = _mk(repos)
    runner = _Runner(probe_status=ExecStatus.FAILED, probe_log=None)
    BuildWatcher(repos, runner).run_once()
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_preflight_failed")
    assert row["log_text"] is None


def test_probe_success_without_ok_marker_waits_for_next_tick(repos):
    # Succeeded 인데 로그가 아직 안 읽히면(일시 결손) 실패를 지어내지 않는다 --
    # 다음 틱이 재시도하고, 영영 안 오면 프리플라이트 타임아웃이 최후 회수다.
    bid = _mk(repos)
    runner = _Runner(probe_status=ExecStatus.SUCCEEDED, probe_log=None)
    out = BuildWatcher(repos, runner).run_once()
    assert repos.builds.get(bid)["state"] == "Pending"
    assert runner.submitted == []
    assert out == {"submitted": 0, "finished": 0}


def test_probe_submit_failure_is_recorded_as_failed(repos):
    # 프로브 생성 실패(k8s API 오류)는 기존 submit_failed 재사용(설계 §4).
    bid = _mk(repos)
    runner = _Runner()
    runner.fail_submit_preflight = "submit_failed"
    BuildWatcher(repos, runner).run_once()
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "submit_failed")


def test_transient_probe_poll_error_leaves_build_pending(repos):
    # I6 관용구를 프로브에도: 일시 오류로 Failed 못박지 않는다 -- 다음 틱 재시도,
    # 영구 오류는 프리플라이트 타임아웃이 회수한다.
    bid = _mk(repos)
    runner = _Runner()
    runner.fail_poll = "poll_failed"
    out = BuildWatcher(repos, runner).run_once()   # 예외 없이 반환돼야 한다
    assert repos.builds.get(bid)["state"] == "Pending"
    assert out == {"submitted": 0, "finished": 0}


def test_pending_build_past_preflight_deadline_is_reclaimed(repos):
    # 프로브가 스케줄조차 안 되는 경우(노드 다운)의 유일한 탈출구 -- 로그 없이
    # build_preflight_timeout 이 잡는다(설계 §4).
    bid = _mk(repos)
    created_at = repos.builds.get(bid)["created_at"]
    runner = _Runner(probe_status=ExecStatus.PENDING, probe_log="scheduling...\n")
    now = iso_plus(created_at, 181)   # 기본 프리플라이트 창(180)을 막 넘겼다
    out = BuildWatcher(repos, runner,
                       preflight_timeout_seconds=180).run_once(now_iso=now)
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_preflight_timeout")
    assert row["log_text"] == "scheduling...\n"    # 회수 전에 로그부터 박제
    assert runner.terminated == [f"{BUILD_REF_PREFIX}/dms-build-pf-{bid[:12]}"]
    assert runner.probe_submitted == []            # 회수 틱에 새 프로브를 만들지 않는다
    assert out["finished"] == 1


def test_pending_build_within_preflight_deadline_waits(repos):
    bid = _mk(repos)
    created_at = repos.builds.get(bid)["created_at"]
    runner = _Runner(probe_status=ExecStatus.RUNNING)
    now = iso_plus(created_at, 100)   # 창(180)에 못 미침
    BuildWatcher(repos, runner, preflight_timeout_seconds=180).run_once(now_iso=now)
    assert repos.builds.get(bid)["state"] == "Pending"
    assert runner.terminated == []


def test_preflight_timeout_disabled_by_default_regardless_of_age(repos):
    # preflight_timeout_seconds 를 안 주면(기존 호출자 하위호환) 회수하지 않는다 --
    # timeout_seconds 와 같은 규칙. 실제 배선(controller.py)은 항상 settings 를 넘긴다.
    bid = _mk(repos)
    runner = _Runner(probe_status=ExecStatus.RUNNING)
    far_future = iso_plus(utc_now_iso(), 10_000_000)
    BuildWatcher(repos, runner).run_once(now_iso=far_future)
    assert repos.builds.get(bid)["state"] == "Pending"
    assert runner.terminated == []


# ---- 슬라이스 21 §2.1: 회수 분기의 Pending 구분 ----

def test_running_build_stuck_pending_past_deadline_gets_its_own_code(repos):
    # activeDeadlineSeconds 는 스케줄 후에만 발화한다(§1-9) -- 2시간째 파드가
    # PENDING 이면 "노드에 자리가 없다/노드 문제"라는 뜻이고, generic build_timeout
    # 으로 접으면 운영자가 원인을 모른다.
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    created_at = repos.builds.get(bid)["created_at"]
    runner = _Runner(status=ExecStatus.PENDING, log="0/6 nodes are available...\n")
    now = iso_plus(created_at, 7201)
    out = BuildWatcher(repos, runner, timeout_seconds=7200).run_once(now_iso=now)
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_stuck_pending")
    assert runner.terminated == [f"{BUILD_REF_PREFIX}/dms-build-{bid[:12]}"]
    assert out["finished"] == 1


def test_reclaim_poll_failure_falls_back_to_generic_build_timeout(repos):
    # 구분용 poll 이 실패해도 회수 자체는 막히면 안 된다 -- generic 코드로 폴백.
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    created_at = repos.builds.get(bid)["created_at"]
    runner = _Runner()
    runner.fail_poll = "poll_failed"
    now = iso_plus(created_at, 7201)
    BuildWatcher(repos, runner, timeout_seconds=7200).run_once(now_iso=now)
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_timeout")


# ---- 스텁 경로 계약 + 마커 파서 ----

def test_stub_runner_full_flow_with_preflight_succeeds_without_a_cluster(repos):
    # 로컬·CI 계약(설계 §4): StubBuildRunner 는 프리플라이트 포함 **한 틱에**
    # Succeeded 다(pending 루프가 제출·mark_running 하면 같은 run_once 의 running
    # 재조회가 종단시킨다 -- 기존 흐름과 동일).
    bid = _mk(repos)
    out = BuildWatcher(repos, StubBuildRunner(), timeout_seconds=7200,
                       preflight_timeout_seconds=180).run_once()
    row = repos.builds.get(bid)
    assert row["state"] == "Succeeded"
    assert row["commit_sha"] == "stubcommit"
    assert out == {"submitted": 1, "finished": 1}


@pytest.mark.parametrize("text,expected", [
    ("DMS_PREFLIGHT_REASON=build_node_no_egress\n", "build_node_no_egress"),
    ("noise\nDMS_PREFLIGHT_REASON=build_node_disk_low\nmore\n", "build_node_disk_low"),
    ("no marker here", None),
    ("DMS_PREFLIGHT_REASON=\n", None),
    (None, None),
])
def test_parse_preflight_reason(text, expected):
    assert parse_preflight_reason(text) == expected
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_build_watcher.py -q`
Expected: 수집 단계 에러 — `ImportError: cannot import name 'parse_preflight_reason' from 'dms.build_watcher'`

- [ ] **Step 3: build_watcher.py 를 전체 교체한다**

`src/dms/build_watcher.py` (전체):

```python
"""빌드 상태를 파드에서 DB로 옮기는 컨트롤러 루프.

루프 안의 예외는 controller.run_all_once 가 삼켜 stderr 로만 내보낸다 --
그래서 실패는 예외로 새지 않고 반드시 builds.state 로 드러나야 한다.

슬라이스 21(§2.5): Pending 빌드는 곧장 제출하지 않는다 -- 적합성 프로브 파드가
빌드 노드에서 egress/레지스트리/디스크를 실검사하고, 통과해야 빌드 파드를 낸다.
상태를 DB 에 더 만들지 않는다: 프로브 파드 이름이 build_id 에서 결정적이고
(build_probe_pod_name) submit_preflight 가 멱등(AlreadyExists 관용)이라,
"없음→생성"과 "진행 중→대기"가 상태 저장 없이 매 틱 재호출 한 번으로 접힌다."""
import logging

from .build_runner import BUILD_REF_PREFIX
from .db import iso_plus, utc_now_iso
from .execution import ExecStatus, ExecutionError
from .repositories.builds import build_pod_name, build_probe_pod_name

logger = logging.getLogger(__name__)

_MARKER = "DMS_COMMIT_SHA="
_PF_MARKER = "DMS_PREFLIGHT_REASON="
_PF_OK = "DMS_PREFLIGHT_OK"
# 프로브 로그는 신뢰 입력이 아니다(설계 §4) -- 마커가 이 화이트리스트 밖이면
# 코드를 지어내지 않고 build_preflight_failed 로 접는다. 세 코드는 프로브 스크립트
# (build_manifests._PROBE_SCRIPT)·frontend/src/lib/reasonCodes.json 과 같은 철자다.
_PF_REASONS = frozenset({"build_node_no_egress", "build_registry_unreachable",
                         "build_node_disk_low"})
_TERMINAL = (ExecStatus.SUCCEEDED, ExecStatus.FAILED, ExecStatus.TIMED_OUT)


def _marker_value(log_text, marker):
    """로그에서 marker= 한 줄의 값만 뽑는다 -- 로그 형식(buildah/파이썬 출력)은
    언제든 바뀌므로 형식을 파싱하지 않고 마커 관례 하나만 본다."""
    if not log_text:
        return None
    for line in log_text.split("\n"):
        line = line.strip()
        if line.startswith(marker):
            value = line[len(marker):].strip()
            if value:
                return value
    return None


def parse_commit_sha(log_text):
    """빌드 스크립트가 찍는 DMS_COMMIT_SHA= 마커에서 커밋을 뽑는다."""
    return _marker_value(log_text, _MARKER)


def parse_preflight_reason(log_text):
    """프로브가 찍는 DMS_PREFLIGHT_REASON= 마커에서 사유 코드 후보를 뽑는다 --
    실행 preflight(execution_manifests._preflight_script)와 같은 마커 관례라
    운영자가 한 가지 문법만 알면 된다. 화이트리스트 판정은 호출자(run_once)가
    한다 -- 파서는 정책을 모른다."""
    return _marker_value(log_text, _PF_MARKER)


class BuildWatcher:
    def __init__(self, repos, runner, *, timeout_seconds=None,
                 preflight_timeout_seconds=None):
        self._repos = repos
        self._runner = runner
        # None이면 나이 기반 회수를 하지 않는다(기존 호출자와의 하위호환 기본값) --
        # 실제 배선(controller.py)은 항상 settings 값 둘 다 넘긴다.
        self._timeout_seconds = timeout_seconds
        self._preflight_timeout_seconds = preflight_timeout_seconds

    def _ref(self, build):
        return f"{BUILD_REF_PREFIX}/{build_pod_name(build['build_id'])}"

    def _pf_ref(self, build):
        return f"{BUILD_REF_PREFIX}/{build_probe_pod_name(build['build_id'])}"

    def _reclaim(self, build_id, ref, *, reason_code):
        """로그 박제 -> 파드 삭제 -> Failed 박제. 타임아웃 계열 회수의 공통 순서:
        terminate 를 먼저 하면 유일한 증거(파드 로그)가 함께 사라진다."""
        try:
            log_text = self._runner.read_log(ref)
        except ExecutionError:
            # 로그 조회 실패로 회수 자체를 막으면 다시 갇힌다 -- None 은 finish 의
            # COALESCE 라 기존 log_text 를 지우지 않는다.
            log_text = None
        try:
            self._runner.terminate(ref)
        except ExecutionError:
            pass  # best-effort -- 타임아웃 판정 자체는 지켜야 한다
        self._repos.builds.finish(build_id, state="Failed",
                                  reason_code=reason_code, log_text=log_text)

    def run_once(self, *, now_iso=None) -> dict:
        submitted = finished = 0
        now = now_iso or utc_now_iso()
        pf_cutoff = (iso_plus(now, -self._preflight_timeout_seconds)
                     if self._preflight_timeout_seconds is not None else None)
        for build in self._repos.builds.pending():
            build_id = build["build_id"]
            pf_ref = self._pf_ref(build)
            # 회수 판정을 프로브 생성보다 먼저 -- 회수 틱에 새 프로브를 만들지
            # 않는다. 프로브가 스케줄조차 안 되는 경우(노드 다운)의 유일한
            # 탈출구다: 프로브의 activeDeadlineSeconds 는 스케줄 후에만 발화한다.
            if pf_cutoff is not None and build["created_at"] < pf_cutoff:
                self._reclaim(build_id, pf_ref,
                              reason_code="build_preflight_timeout")
                finished += 1
                continue
            try:
                # 매 틱 멱등 제출(AlreadyExists 관용 -- BuildRunner.submit 선례):
                # "없음→생성"과 "진행 중→대기"가 상태 저장 없이 한 호출로 접힌다.
                self._runner.submit_preflight(build)
            except ExecutionError as exc:
                # 프로브 생성 실패(k8s API 오류)는 기존 submit_failed 재사용(§4) --
                # detail 의 "preflight:" 접두가 빌드 파드 제출 실패와 구분한다.
                self._repos.builds.finish(build_id, state="Failed",
                                          reason_code=exc.reason_code)
                finished += 1
                continue
            try:
                status = self._runner.poll(pf_ref)
            except ExecutionError as exc:
                # I6 관용구: 일시 오류 하나로 Failed 못박지 않는다 -- 다음 틱이
                # 재시도하고, 영구 오류는 위 프리플라이트 타임아웃이 회수한다.
                logger.warning("build preflight poll error build_id=%s: %s",
                               build_id, exc)
                continue
            if status not in _TERMINAL:
                continue
            try:
                log_text = self._runner.read_log(pf_ref)
            except ExecutionError:
                log_text = None
            if status == ExecStatus.SUCCEEDED:
                if log_text is None or _PF_OK not in log_text:
                    # 종료는 성공인데 OK 마커를 아직 못 읽었다(로그 일시 결손) --
                    # 실패를 지어내지 않고 다음 틱 재시도. 최후 회수는 타임아웃.
                    continue
                try:
                    self._runner.submit(build)
                except ExecutionError as exc:
                    self._repos.builds.finish(build_id, state="Failed",
                                              reason_code=exc.reason_code)
                    finished += 1
                    continue
                self._repos.builds.mark_running(build_id)
                submitted += 1
                continue
            # 프로브 실패: 마커를 화이트리스트로만 채택(§2.5) -- 로그 전체를
            # 박제해 실패 호스트/실측 바이트가 /log 로 보이게 한다(64KB 꼬리
            # 규칙은 finish 가 적용).
            reason = parse_preflight_reason(log_text)
            if reason not in _PF_REASONS:
                reason = "build_preflight_failed"
            self._repos.builds.finish(build_id, state="Failed",
                                      reason_code=reason, log_text=log_text)
            finished += 1

        cutoff = (iso_plus(now, -self._timeout_seconds)
                 if self._timeout_seconds is not None else None)
        for build in self._repos.builds.running():
            build_id = build["build_id"]
            ref = self._ref(build)
            # I6: 빌드별로 예외를 격리한다(stepper.py와 같은 관용구) -- poll의 일시적
            # 오류(예: apiserver 재시작으로 상태 조회 1회 실패) 하나가 즉시 Failed로
            # 못박히면 파드는 계속 돌아 이미지를 push하는데 포탈은 실패를 보여주는
            # 불일치가 생긴다. 로그만 남기고 상태를 그대로 두면 다음 틱이 재시도한다.
            # C2(b)의 나이 기반 회수(아래 cutoff 체크)가 없으면 영구 오류 시 Running에
            # 갇히므로 반드시 이 둘이 함께 있어야 한다.
            try:
                if cutoff is not None and build["created_at"] < cutoff:
                    # 회수 전에 딱 한 번 poll: PENDING 이면 "스케줄되지 못한 채
                    # 마감"(§2.1 build_stuck_pending) -- activeDeadlineSeconds 는
                    # 스케줄 후에만 발화하므로 이 구분은 여기서만 가능하다.
                    # poll 실패는 회수를 막지 않고 generic 코드로 폴백한다.
                    try:
                        stuck = self._runner.poll(ref) == ExecStatus.PENDING
                    except ExecutionError:
                        stuck = False
                    self._reclaim(build_id, ref,
                                  reason_code="build_stuck_pending" if stuck
                                  else "build_timeout")
                    finished += 1
                    continue
                status = self._runner.poll(ref)
                if status not in _TERMINAL:
                    continue
                log_text = self._runner.read_log(ref)
                self._repos.builds.finish(
                    build_id,
                    state="Succeeded" if status == ExecStatus.SUCCEEDED else "Failed",
                    reason_code=None if status == ExecStatus.SUCCEEDED else "build_failed",
                    commit_sha=parse_commit_sha(log_text),
                    log_text=log_text)
                finished += 1
            except Exception as exc:
                logger.warning("build watcher error build_id=%s: %s: %s",
                               build_id, type(exc).__name__, exc)
        return {"submitted": submitted, "finished": finished}
```

- [ ] **Step 4: 컨트롤러 배선 + 사유 코드 6종 등록**

**(1)** `src/dms/controller.py` — build-watcher 루프 등록을 다음으로 교체:

```python
    if build_runner is not None:
        loops.append(Loop("build-watcher", settings.build_watcher_interval_seconds,
                          lambda: BuildWatcher(
                              repos, build_runner,
                              timeout_seconds=settings.build_timeout_seconds,
                              # 슬라이스 21 §2.5: 프로브 대기 상한 -- 이게 없으면
                              # Pending 빌드가 프로브 영구 오류 시 갇힌다.
                              preflight_timeout_seconds=(
                                  settings.build_preflight_timeout_seconds),
                          ).run_once()))
```

**(2)** `frontend/src/lib/reasonCodes.json` — `"preview_expired", "build_timeout", "build_failed",` 줄을 다음으로 교체:

```json
  "preview_expired", "build_timeout", "build_failed",
  "build_stuck_pending", "build_preflight_timeout", "build_preflight_failed",
  "build_node_no_egress", "build_registry_unreachable", "build_node_disk_low",
```

**(3)** `frontend/src/lib/api.ts` — `build_timeout: "빌드가 제한 시간을 넘겨 중단되었습니다",` 줄 **바로 아래**에 추가:

```typescript
  // 슬라이스 21 빌드 프리플라이트/회수 세분화. reasonCodes.json 과 같은 커밋 --
  // 양방향 계약(reasonCodes.test.ts / test_reason_codes_coverage.py) 조건이다.
  build_stuck_pending: "빌드 파드가 스케줄되지 못한 채 제한 시간을 넘겼습니다 — 빌드 노드 상태·여유를 확인하세요",
  build_preflight_timeout: "빌드 적합성 확인이 제한 시간을 넘겼습니다 — 빌드 노드가 내려갔거나 프로브가 스케줄되지 못했을 수 있습니다",
  build_preflight_failed: "빌드 적합성 확인에 실패했습니다 — 로그를 확인하세요",
  build_node_no_egress: "빌드 노드에서 인터넷으로 나갈 수 없습니다 — 운영자가 인터넷을 아직 열지 않았을 수 있습니다",
  build_registry_unreachable: "빌드 노드에서 이미지 레지스트리에 연결할 수 없습니다",
  build_node_disk_low: "빌드 노드의 디스크 여유가 부족합니다 — 로그의 실측 수치를 확인하세요",
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_build_watcher.py tests/test_controller.py tests/test_reason_codes_coverage.py tests/test_api_builds.py -q`
Expected: 전부 PASS

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/frontend && npx vitest run src/lib/reasonCodes.test.ts`
Expected: `2 passed` — json ⊆ REASON_MESSAGES(커버리지)와 죽은 키 금지 양쪽 초록

- [ ] **Step 6: 커밋 (백엔드+프론트 계약을 한 커밋에)**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21
git add src/dms/build_watcher.py src/dms/controller.py tests/test_build_watcher.py frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts
git commit -m "feat(build): 워처 프리플라이트 상태기계 — 프로브 멱등 생성/대기/화이트리스트 채택/타임아웃 + build_stuck_pending 구분(+사유 코드 6종)"
```

---
### Task 5: 라우트 동기 검증 — invalid_repo_url · build_node_report_stale

**Files:**
- Modify: `src/dms/api/routes_builds.py`
- Modify: `tests/test_api_builds.py`
- Modify: `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts` (같은 커밋 — 계약 조건)

**Interfaces:**
- Consumes: Task 3 의 `repo_host`, `agents.list_nodes(stale_seconds=...)` 의 fresh 판정(`repositories/agents.py:24-34`, 엄격 부등호), 기존 검증 사슬(`routes_builds.py:41-58`).
- Produces: 422 `invalid_repo_url`(호스트 파싱 불가 — egress 프로브 대상을 못 만든다), 422 `build_node_report_stale`(빌드 노드 리포트 stale — 노드 다운 즉답). 순서는 invalid_git_ref 뒤 · 409 빠른 거절 앞. egress·디스크는 동기로 검사하지 않는다 — API 파드는 다른 노드다(설계 §2.5).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_builds.py` — 파일 끝에 추가:

```python
# ---- 슬라이스 21 §2.5 동기 검증: repo_url 호스트 / 빌드 노드 리포트 신선도 ----

def test_unparseable_repo_url_is_rejected(client):
    # scp 형(git@host:path)은 urlsplit 이 호스트를 못 뽑는다 -- egress 프로브
    # 대상을 만들 수 없으므로 파드를 띄우기 전에 즉답으로 거른다.
    _set_build_node(client)
    r = client.post("/api/admin/builds",
                    json={"git_ref": "main", "images": ["dms"],
                          "repo_url": "git@github.com:ChahwanSong/dms.git"},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "invalid_repo_url"


def test_default_repo_url_from_settings_passes_host_validation(client):
    # repo_url 생략 시 settings.build_repo_url(https://github.com/...)이 쓰인다 --
    # 기본값 경로가 새 검증에 걸리면 기존 제출 흐름 전체가 퇴행한다.
    _set_build_node(client)
    r = client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 202


def test_stale_build_node_report_is_rejected_at_the_exact_threshold(client):
    # fresh 판정은 reported_at > (now - stale_seconds) 엄격 부등호다(agents.py
    # list_nodes) -- 정확히 문턱 나이의 리포트는 stale 이다. 경계값을 고정해 두면
    # 부등호가 >= 로 바뀌는 회귀도 잡힌다(라우트 호출 시점의 now 는 ingest 시점
    # 이상이므로 어느 쪽이든 문턱 리포트는 stale 로 판정돼야 한다).
    from dms.db import iso_plus, utc_now_iso
    node = "dms-w1"
    stale = client.app.state.settings.agent_report_stale_seconds
    client.app.state.repos.agents.ingest(node, {})   # PUT 검증(node_exists) 통과용
    r = client.put("/api/admin/control-state",
                   json={"maintenance": False, "drain": False, "reason": None,
                         "build_node_name": node},
                   headers=ADMIN)
    assert r.status_code == 200
    # 마지막 리포트를 정확히 문턱 나이로 교체 -- ingest 는 노드당 1행 교체라
    # agent_nodes 의 유일 행이 이 시각이 된다.
    client.app.state.repos.agents.ingest(
        node, {}, reported_at=iso_plus(utc_now_iso(), -stale))
    r = client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "build_node_report_stale"


def test_fresh_build_node_report_passes_the_stale_gate(client):
    _set_build_node(client)   # ingest 가 지금 막 리포트를 넣는다 -- fresh
    r = client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 202
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_builds.py -q`
Expected: FAIL 2건 / 나머지 PASS — `test_unparseable_repo_url_...` 가 `assert 202 == 422`(현재 repo_url 무검증 통과), `test_stale_build_node_report_...` 가 `assert 202 == 422`(신선도 게이트 부재). 통과 경로 2건(`test_default_repo_url_...`, `test_fresh_...`)은 즉시 PASS(현행 고정 가드 — 구현 후에도 퇴행 없음을 고정하는 짝이다).

- [ ] **Step 3: routes_builds.py 검증 사슬에 2종을 추가한다**

**(1)** import 블록의 `from ..build_runner import BUILD_REF_PREFIX` **바로 아래**에 추가:

```python
from ..build_manifests import repo_host
```

**(2)** `submit_build` 의 invalid_git_ref 블록과 409 빠른 거절 사이 — 즉

```python
    ref = (body.git_ref or "").strip()
    if not _REF_RE.fullmatch(ref) or ".." in ref or ref.startswith("-"):
        raise HTTPException(status_code=422, detail="invalid_git_ref")
```

바로 아래에 추가:

```python
    settings = request.app.state.settings
    repo_url = body.repo_url or settings.build_repo_url
    # 슬라이스 21 §2.5 동기 ①: 호스트를 못 뽑으면 egress 프로브 대상을 만들 수
    # 없다 -- 프로브 파드를 띄우기 전에 즉답한다. 프로브 매니페스트와 같은 파서
    # (build_manifests.repo_host)를 쓴다: 두 곳이 다르게 파싱하면 "제출은
    # 통과했는데 프로브를 못 만드는" 창이 생긴다. scp 형(git@host:path)도 여기서
    # 걸린다 -- 명시 거절이지 지원 축소가 아니다(빌드 스크립트는 https 전제).
    if repo_host(repo_url) is None:
        raise HTTPException(status_code=422, detail="invalid_repo_url")
    # 슬라이스 21 §2.5 동기 ②: 빌드 노드 리포트가 stale 이면 노드 다운일 공산이
    # 크다 -- 비동기 프로브(최대 180s 창)까지 가지 않고 제출 시점에 즉답한다.
    # fresh 판정은 agents.list_nodes 의 그것(reported_at > now - stale) 재사용 --
    # 판정을 여기서 복제하면 노드 화면과 다른 답을 주는 두 번째 진실이 생긴다.
    # egress·디스크는 동기로 검사하지 않는다: API 파드는 다른 노드라 무의미하다.
    fresh = {n["node_name"]
             for n in repos.agents.list_nodes(
                 stale_seconds=settings.agent_report_stale_seconds)
             if n["fresh"]}
    if node not in fresh:
        raise HTTPException(status_code=422, detail="build_node_report_stale")
```

**(3)** 같은 함수의 `repos.builds.create(` 호출에서

```python
        build_id = repos.builds.create(
            repo_url=body.repo_url or request.app.state.settings.build_repo_url,
```

을 다음으로 교체(위에서 확정한 repo_url 재사용 — 검증한 값과 저장하는 값이 같은 변수여야 한다):

```python
        build_id = repos.builds.create(
            repo_url=repo_url,
```

**(4)** `frontend/src/lib/reasonCodes.json` — `"build_node_not_set", "build_in_progress", "unknown_image", "invalid_git_ref",` 줄을 다음으로 교체:

```json
  "build_node_not_set", "build_in_progress", "unknown_image", "invalid_git_ref",
  "invalid_repo_url", "build_node_report_stale",
```

**(5)** `frontend/src/lib/api.ts` — `invalid_git_ref: "git ref 형식이 올바르지 않습니다",` 줄 **바로 아래**에 추가:

```typescript
  // 슬라이스 21 동기 검증 2종(제출 시 422). reasonCodes.json 과 같은 커밋.
  invalid_repo_url: "저장소 URL에서 호스트를 읽을 수 없습니다 — https://호스트/경로 형식으로 입력하세요",
  build_node_report_stale: "빌드 노드의 에이전트 리포트가 오래되었습니다 — 노드가 내려갔을 수 있습니다",
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_builds.py tests/test_reason_codes_coverage.py -q`
Expected: 전부 PASS (기존 제출 테스트는 전부 `_set_build_node` 가 방금 ingest 한 fresh 리포트 위에서 돈다 — 무변경이어야 한다)

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/frontend && npx vitest run src/lib/reasonCodes.test.ts`
Expected: `2 passed`

- [ ] **Step 5: 커밋 (백엔드+프론트 계약을 한 커밋에)**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21
git add src/dms/api/routes_builds.py tests/test_api_builds.py frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts
git commit -m "feat(api): 빌드 제출 동기 검증 — invalid_repo_url(호스트 파싱) + build_node_report_stale(fresh 재사용)"
```

---

### Task 6: pod_gc 확장 — 종단 빌드의 프로브 파드 수거

**Files:**
- Modify: `src/dms/pod_gc.py`
- Modify: `tests/test_pod_gc.py`

**Interfaces:**
- Consumes: Task 3 의 `build_probe_pod_name`, `builds.terminal_older_than`(`builds.py:135-144`), `BuildRunner.terminate` 멱등(k8s.delete 가 404 흡수).
- Produces: 종단 빌드마다 빌드 파드 + 프로브 파드 **2개 ref** 를 같은 창(after_seconds)으로 terminate. 비종단 빌드의 프로브는 불가침 — 지우면 워처 pending 루프가 poll FAILED(객체 없음)를 프로브 실패로 오인해 멀쩡한 빌드를 `build_preflight_failed` 로 죽인다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_pod_gc.py` — **(1)** import 의 `from dms.repositories.builds import build_pod_name` 을 다음으로 교체:

```python
from dms.repositories.builds import build_pod_name, build_probe_pod_name
```

**(2)** `test_terminal_build_pod_is_terminated_when_build_runner_given` 의 단언부

```python
    assert result == {"deleted": 1}
    # I5: 리터럴 "buildpod" 대신 build_runner.py가 export하는 상수를 쓴다 -- 네 곳
    # (build_runner/build_watcher/pod_gc/routes_builds)이 각자 리터럴을 들고 있으면
    # 한 곳만 드리프트해도 조용히 깨진다.
    assert build_runner.terminated == [f"{BUILD_REF_PREFIX}/{build_pod_name(bid)}"]
```

을 다음으로 교체:

```python
    # 슬라이스 21: 종단 빌드는 빌드 파드 + 프리플라이트 프로브 파드 2개를 남긴다 --
    # 같은 창으로 함께 수거한다(프로브 로그도 종단 후 after_seconds 동안은 증거로
    # 보존된다 -- 20-config.yaml 의 GC 창 주석과 같은 이유).
    assert result == {"deleted": 2}
    # I5: 리터럴 "buildpod" 대신 build_runner.py가 export하는 상수를 쓴다 -- 네 곳
    # (build_runner/build_watcher/pod_gc/routes_builds)이 각자 리터럴을 들고 있으면
    # 한 곳만 드리프트해도 조용히 깨진다.
    assert build_runner.terminated == [
        f"{BUILD_REF_PREFIX}/{build_pod_name(bid)}",
        f"{BUILD_REF_PREFIX}/{build_probe_pod_name(bid)}",
    ]
```

**(3)** 파일 끝에 추가:

```python
def test_build_pod_terminate_failure_still_reclaims_the_probe_pod(db):
    """10. ref 별 예외 격리: 빌드 파드 terminate 가 죽어도 프로브 파드 수거는
    계속된다(기존 잡 파드 GC 의 per-ref 격리와 같은 계약)."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()

    class _BoomOnBuildPod:
        def __init__(self):
            self.terminated = []

        def terminate(self, ref):
            self.terminated.append(ref)
            if "/dms-build-pf-" not in ref:
                raise RuntimeError("boom")

    build_runner = _BoomOnBuildPod()
    bid = _make_build(repos)
    repos.builds.mark_running(bid)
    repos.builds.finish(bid, state="Succeeded")
    finished_at = repos.builds.get(bid)["finished_at"]
    now = iso_plus(finished_at, 3601)

    gc = PodGarbageCollector(repos, adapter, after_seconds=3600,
                             build_runner=build_runner)
    result = gc.run_once(now_iso=now)

    assert result == {"deleted": 1}   # 프로브만 성공 카운트
    assert build_runner.terminated == [
        f"{BUILD_REF_PREFIX}/{build_pod_name(bid)}",
        f"{BUILD_REF_PREFIX}/{build_probe_pod_name(bid)}",
    ]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_pod_gc.py -q`
Expected: FAIL 2건 / 나머지 PASS — 테스트 7 이 `assert {'deleted': 1} == {'deleted': 2}`, 신규 테스트 10 이 `assert {'deleted': 0} == {'deleted': 1}`(현재 프로브 ref 를 아예 안 만든다). 비종단 불가침(테스트 8)·None 스킵(테스트 9/9b)은 계속 PASS.

- [ ] **Step 3: pod_gc.py 의 빌드 블록을 고친다**

**(1)** import 를 다음으로 교체:

```python
from .build_runner import BUILD_REF_PREFIX
from .repositories.builds import build_pod_name, build_probe_pod_name
```

**(2)** 빌드 파드 수거 블록(`if self._build_runner is not None:` 이하 전체)을 다음으로 교체:

```python
        # 종단 빌드가 남긴 빌드 파드 + 프리플라이트 프로브 파드(슬라이스 21)를
        # 같은 창(after_seconds)으로 수거한다. 비종단 빌드는 절대 건드리지 않는다:
        # 빌드 파드가 사라지면 poll 이 FAILED 로 읽고, 프로브 파드가 사라지면
        # 워처 pending 루프가 프로브 실패로 오인해 멀쩡한 빌드를
        # build_preflight_failed 로 죽인다. terminate 는 ref 별로 격리한다 --
        # 한 파드의 실패가 나머지 수거를 막으면 안 된다(잡 파드 GC 와 같은 계약).
        if self._build_runner is not None:
            for build in self._repos.builds.terminal_older_than(
                    self._after, limit=self._limit, now_iso=now_iso):
                for pod in (build_pod_name(build["build_id"]),
                            build_probe_pod_name(build["build_id"])):
                    ref = f"{BUILD_REF_PREFIX}/{pod}"
                    try:
                        self._build_runner.terminate(ref)
                        deleted += 1
                    except Exception as exc:
                        logger.warning("build pod gc failed ref=%s: %s", ref, exc)
        return {"deleted": deleted}
```

**(3)** 모듈 docstring 의 마지막 문장(`빌드 파드도 같은 이유로 **종단 빌드만** 대상이다 -- ...`)을 다음으로 교체:

```python
빌드 파드·프리플라이트 프로브 파드도 같은 이유로 **종단 빌드만** 대상이다 --
비종단 빌드의 빌드 파드가 사라지면 BuildRunner.poll 이 객체 없음을 FAILED 로
오인하고, 프로브 파드가 사라지면 BuildWatcher pending 루프가 멀쩡한 빌드를
build_preflight_failed 로 오기록한다."""
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_pod_gc.py tests/test_controller.py -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21
git add src/dms/pod_gc.py tests/test_pod_gc.py
git commit -m "feat(gc): 종단 빌드의 프리플라이트 프로브 파드도 같은 창으로 수거 — 비종단 불가침 유지"
```

---
### Task 7: 프론트 — Pending 프리플라이트 캡션 + 계약 마감(전체 스위트)

**Files:**
- Modify: `frontend/src/features/builds/BuildDetail.tsx`
- Modify: `frontend/src/features/builds/BuildDetail.test.tsx`

**Interfaces:**
- Consumes: Task 4/5 가 등록한 REASON_MESSAGES 8종(특히 `build_node_no_egress` 의 설계 §3 핵심 문구), 기존 `reasonText` 렌더 경로(`BuildDetail.tsx:35`), msw 테스트 관례(`BuildDetail.test.tsx` setupServer/renderPage/buildRow).
- Produces: state === "Pending" 일 때만 캡션 `적합성 확인(프리플라이트) 포함 — 최대 약 3분` 렌더. BuildsPage·ControlStatePage 는 **무변경**(설계 §3: 워커 select 가 이미 요구와 부합, 신규 422 는 기존 ApiError 경로로 흐른다 — `api.ts` 문구는 Task 5 에서 이미 추가됐다).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/features/builds/BuildDetail.test.tsx` — 파일 끝에 추가:

```typescript
test("Pending 빌드는 프리플라이트 캡션을 보여준다", async () => {
  // 슬라이스 21 §3: Pending 은 이제 "제출 대기"가 아니라 적합성 확인(프로브,
  // 최대 180s)을 포함한다 -- 별도 상태 기계 없이 캡션 한 줄이 그 사실을 알린다.
  server.use(
    http.get("/api/admin/builds/b1", () =>
      HttpResponse.json(buildRow({ state: "Pending", commit_sha: null, finished_at: null }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: null })),
  );
  renderPage();
  expect(await screen.findByText("적합성 확인(프리플라이트) 포함 — 최대 약 3분")).toBeInTheDocument();
});

test("종단 빌드에는 프리플라이트 캡션이 없다", async () => {
  server.use(
    http.get("/api/admin/builds/b1", () => HttpResponse.json(buildRow({ state: "Succeeded" }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: "ok\n" })),
  );
  renderPage();
  await screen.findByText("dms-w1");   // 데이터 로드 완료를 기다린 뒤 부재를 단언
  expect(screen.queryByText(/프리플라이트/)).not.toBeInTheDocument();
});

test("egress 실패 사유가 '인터넷을 아직 열지 않았을 수 있습니다' 문구로 보인다", async () => {
  // 설계 §3 의 핵심 문구 -- "운영자가 인터넷을 안 열었다를 즉시 안다"의 실체가
  // 이 한 줄이다. 원시 코드는 노출하지 않는다(reasonText 매핑 경로).
  server.use(
    http.get("/api/admin/builds/b1", () =>
      HttpResponse.json(buildRow({ state: "Failed", reason_code: "build_node_no_egress" }))),
    http.get("/api/admin/builds/b1/log", () =>
      HttpResponse.json({ build_id: "b1", log: "unreachable_443=github.com\n" })),
  );
  renderPage();
  expect(await screen.findByText(/인터넷을 아직 열지 않았을 수 있습니다/)).toBeInTheDocument();
  expect(screen.queryByText("build_node_no_egress")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/frontend && npx vitest run src/features/builds/BuildDetail.test.tsx`
Expected: FAIL 1건 / 나머지 PASS — `Pending 빌드는 프리플라이트 캡션...` 이 `Unable to find an element with the text: 적합성 확인(프리플라이트) 포함 — 최대 약 3분`. 캡션 부재 테스트와 egress 문구 테스트는 즉시 PASS(후자는 Task 4 의 REASON_MESSAGES 가 이미 렌더 경로에 있다 — 회귀 가드로 남긴다).

- [ ] **Step 3: BuildDetail 에 캡션을 넣는다**

`frontend/src/features/builds/BuildDetail.tsx` — StatusPill 행

```tsx
            <div className="flex items-center gap-3">
              <StatusPill state={b?.state ?? "—"} variant={b ? buildPillVariant(b.state) : undefined} />
              <span className="text-muted">태그 {b?.tag ?? "—"}</span>
            </div>
```

을 다음으로 교체:

```tsx
            <div className="flex items-center gap-3">
              <StatusPill state={b?.state ?? "—"} variant={b ? buildPillVariant(b.state) : undefined} />
              <span className="text-muted">태그 {b?.tag ?? "—"}</span>
            </div>
            {/* 슬라이스 21 §3: Pending 은 적합성 확인(프리플라이트 프로브, 최대
                DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS=180s)을 포함한다 -- 별도 상태
                기계는 만들지 않는다. 실패는 어차피 고유 사유 코드로 드러난다. */}
            {b?.state === "Pending" && (
              <p className="text-muted">적합성 확인(프리플라이트) 포함 — 최대 약 3분</p>
            )}
```

- [ ] **Step 4: 통과를 확인한다 (프론트 전체 + 타입체크)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/frontend && npx vitest run && npx tsc -b`
Expected: `Test Files  49 passed`, `Tests  228 passed`(기준선 225 + 신규 3), tsc 무출력 exit 0

- [ ] **Step 5: 백엔드 전체 스위트 (계약 마감)**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21 && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice21/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: **1128 passed**(기준선 1090 + 이 플랜 신규 38: Task1 2 + Task2 4 + Task3 11 + Task4 20 + Task5 4 + Task6 1 — parametrize 전개 포함 근사치다. 수가 다르면 신규 테스트 수를 다시 세되, **failed 0 이 본질**이다)

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice21
git add frontend/src/features/builds/BuildDetail.tsx frontend/src/features/builds/BuildDetail.test.tsx
git commit -m "feat(portal): 빌드 Pending 프리플라이트 캡션 + egress 사유 핵심 문구 검증"
```

---

## 플랜 이후: 배포·실증 (설계 §6 — 별도 ops, 플랜 밖)

플랜 실행(Task 0 준비 + Task 1~7 커밋 7건)이 끝나면 컨트롤러가 테스트베드에서 수행한다 — 플랜 태스크가 아니다(슬라이스 12~20 과 동일 관례). 프론트(BuildDetail)와 서버(api/controller) 코드가 바뀌었고 에이전트는 무변경이다 — `dms` 이미지만 범프한다(`install/docker/Dockerfile.testbed` 로 빌드 — deploy/Dockerfile 은 kubectl 이 없다).

1. **PriorityClass 를 파드보다 먼저 적용한다(순서 필수)**: `kubectl apply -f deploy/k8s/05-volcano-queue-priorityclass.yaml` → `kubectl get priorityclass dms-build` 로 존재 확인. 이 순서를 어기면 새 컨트롤러가 만드는 빌드·프로브 파드가 전부 admission 거절돼 `submit_failed` 로 표면화된다(포탈은 살아 있지만 빌드는 전멸).
2. `kubectl apply -f deploy/k8s/20-config.yaml`(신규 키 `DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS: "180"` 포함).
3. `dms` 이미지 빌드·푸시 후 40/41/30 태그 범프·apply — **태그 결정과 범프는 배포자(컨트롤러)가 한다**(플랜은 태그 불변). 에이전트 DaemonSet(50)은 건드리지 않는다.
4. (§6-1, **핵심**) 대상 워커에서 `iptables -I OUTPUT -p tcp --dport 443 -j REJECT` 로 egress 만 차단 → 빌드 제출 → **수 분 안에** `build_node_no_egress` + 로그에 실패 호스트 목록(`unreachable_443=...`). 2시간 generic 타임아웃이 아니어야 한다. 검증 후 `iptables -D OUTPUT -p tcp --dport 443 -j REJECT` 로 원복. 443 만 막으므로 apiserver(6443)·pkg-01:5000·kubelet 경로는 살아 있어 프로브가 정상 기동한다 — job_image 선택(§2.5)의 값어치가 여기서 증명된다.
5. (§6-2) 인터넷 개방 후 재제출 → 프리플라이트 통과, 실 빌드 성공(3 이미지 push + commit_sha). 빌드 중 `kubectl exec <빌드파드> -- du -s /var/lib/containers` 를 주기 샘플해 **emptyDir 피크 실측** → `BUILD_SIZELIMIT_GIB`(10)·봉투 수치 재보정 근거로 기록.
6. (§6-3, **핵심**) 빌드가 도는 동안 sync 잡 제출 → 잡 정상 완료 + 잡 파드 축출 0건(`kubectl get pod -w`) + **`sched_wait_seconds`(슬라이스 20 컬럼)와 잡 총 수행시간을 평시 기준선과 대조**. 수행시간이 정책 `execution_timeout_seconds` 의 몇 %인지 숫자로 남긴다 — cpu 경합은 잡을 죽이지 않고 느리게 만들 뿐이라, 축출 0건만으로는 「굶기지 않는다」가 증명되지 않는다. 평시 기준선은 빌드 없이 같은 잡을 먼저 한 번 돌려 잡는다.
7. (§6-4) memory limit 을 일시 256Mi 로 낮춘 빌드로 **축출 방향 실증**: 빌드만 OOM-kill(build_failed), 동시 sync 잡 무사 — 「빌드가 항상 먼저 죽는다」(§2.3).
8. (§6-5) 노드 로컬 큰 파일로 여유를 공식 아래로 → `build_node_disk_low` 즉시 실패 + 로그에 `avail_bytes=... need_bytes=...` 실측 바이트, 파일 제거 후 통과.
9. (§6-6) pkg-01:5000 일시 차단 → `build_registry_unreachable` 이 egress 실패와 구분되는지.
10. 실증 통과 후 백로그(`BACKLOG.md:296-303` pkg-01 podman 우회)를 별도 커밋으로 갱신한다.

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 태스크 |
|---|---|
| §1 실측 전제(nodeSelector 현행, emptyDir 회계, BestEffort 잡, Volcano 무회계, PriorityClass 3종, 검증 사슬, watcher 전파 공짜, Pending 은 파드 타임아웃 불가, job_image python3 보장, 노드 여유 실측) | 실측 고정값 표 + 각 태스크 근거 주석 |
| §2.1 nodeSelector 유지 + requests 로 Fit + `build_stuck_pending` 구분 | Task 2(shape 고정 테스트) + Task 4(회수 분기 poll 1회) |
| §2.2 봉투 3종 수치(cpu 250m/1000m·mem 128Mi/1Gi·eph 10Gi/12Gi) | Task 2 |
| §2.3 dms-build(10, Never) 신설·빌드/프로브에 부착·데이터 잡 무개입 | Task 1(매니페스트+기존 3계급 불변 가드) + Task 2·3(파드 부착) |
| §2.4 sizeLimit 20→10Gi + 3중 방어(프리플라이트 df·sizeLimit·eph limits) + 상수 공유 | Task 2(상수) + Task 3(프로브 공식이 같은 상수) |
| §2.5 동기(invalid_repo_url·build_node_report_stale) + 프로브 파드(3종 검사·job_image·마커 관례·결정적 이름·buildpod ref 재사용·워처 상태기계·타임아웃) | Task 5(동기) + Task 3(매니페스트·러너) + Task 4(상태기계) |
| §2.6 사유 코드 8종 양쪽 등록 | Task 4(6종)·Task 5(2종) — 같은 커밋 규칙 준수 |
| §3 화면(ControlStatePage 무변경·BuildsPage 무변경·BuildDetail 문구·Pending 캡션) | Task 4/5(api.ts 문구)·Task 7(캡션+문구 검증) |
| §4 오류 처리(화이트리스트 밖 접기·스케줄 불가 프로브는 타임아웃·OOM/축출은 기존 build_failed·프로브 생성 실패 submit_failed 재사용·스텁 즉시 성공) | Task 4(테스트 각 1건) + Task 3(스텁) — OOM 세분화는 §7 대로 하지 않음 |
| §5 테스트 목록 | Task 1~7 각 Step 1 (매니페스트 수치/프로브 env·마커/워처 상태기계 전 분기/라우트 경계값/pod_gc 불가침/계약 양방향/스텁 경로) |
| §6 실증 | 플랜 이후 절(관례 — 플랜 태스크 아님) |
| §7 하지 않는 것(잡 Burstable 승격, cordon/taint, 리포트 확장, OOM 세분화, 봉투 설정화, 동시 2빌드, 컨트롤플레인 toleration) | 어떤 태스크도 만들지 않음 — 데이터 잡 매니페스트·에이전트 코드는 이 플랜에서 한 줄도 안 바뀐다 |

**2. 플레이스홀더 점검** — "TBD"/"적절히"/코드 없는 스텝 없음. 프로브 python3 스크립트 전문, 워처 전체 교체본, 라우트/GC/프론트의 교체 전후 코드, 반복 실행 명령 전문 수록. 다른 태스크 참조는 Interfaces 시그니처로만 한다.

**3. 타입 일관성** — `build_probe_pod_name`(builds.py)·`build_probe_pod`/`repo_host`/`BUILD_SIZELIMIT_GIB`/`BUILD_DISK_MARGIN_GIB`(build_manifests)·`submit_preflight`(runner/stub)·`parse_preflight_reason`/`_PF_REASONS`(watcher)·`build_preflight_timeout_seconds`(config/wiring/controller)는 Task 3·4 가 정의하고 Task 4·5·6 이 같은 철자로 import 한다. 프로브 env 키 `DMS_PF_EGRESS_HOSTS`/`DMS_PF_REGISTRY`/`DMS_PF_NEED_BYTES` 와 마커 `DMS_PREFLIGHT_OK`/`DMS_PREFLIGHT_REASON=` 은 스크립트·매니페스트 테스트·워처·워처 테스트가 동일 철자다. 사유 코드 8종은 watcher/routes 리터럴 ↔ reasonCodes.json ↔ REASON_MESSAGES ↔ 프론트 테스트가 동일 철자다.

**알려진 위험 / 설계 대비 조정:**
- **사유 코드 등록을 최종 태스크에 몰지 않고 Task 4(6종)/Task 5(2종) 커밋에 분산**했다 — 「같은 커밋 갱신」 Global Constraint(백엔드 AST 계약 테스트가 커밋 단위로 빨간불이 된다)가 태스크 순서 지시보다 우선한다. 최종 Task 7 은 캡션+문구 검증과 전체 스위트 마감을 맡는다.
- **프로브 3종 코드는 AST 추출에 안 걸린다**(화이트리스트 set 리터럴·변수 경유 finish) — 백엔드 계약 테스트가 강제하는 것은 `build_preflight_timeout`(kw 리터럴)과 라우트 2종뿐이지만, 프론트 표시를 위해 8종 전부 등록한다. 죽은 키 검사(json ⊆ 허용)와도 정합.
- **Succeeded + OK 마커 미확인은 「대기」**로 처리한다(실패 지어내기 금지, 타임아웃이 최후 회수) — 설계는 「Succeeded+OK→제출」만 명시했고 그 부정형의 처리는 §4 의 「모름과 실패를 뭉개지 않는다」 계열로 해석했다. 전용 테스트로 고정.
- **프리플라이트 타임아웃 기준은 build.created_at** 이다(프로브 파드 생성 시각이 아니라) — 프로브는 첫 틱(≤15s)에 생기므로 오차는 틱 하나이고, DB 에 상태를 만들지 않는 설계 선택의 대가다. 제출 직후 워처 첫 틱까지의 지연을 포함해도 캡션 「최대 약 3분」 안이다.
- **submit_preflight 를 매 틱 재호출**한다(create→AlreadyExists→get 존재 확인, 틱당 k8s 호출 +2) — 15s 틱·활성 빌드 ≤1 이라 상수 비용. 프로브 종단 후에도 회수 전까지 재호출되지만 파드가 존재하므로 항상 관용 경로다.
- **egress 검사는 모든 호스트를 443 으로만** 본다 — http:// repo(80)여도 443 을 찍는다. 테스트베드 repo 는 https 이고, 설계의 질문 자체가 「인터넷(443)이 열렸는가」다.
- **회수 분기의 poll 1회 추가**로 기존 테스트의 `polled == []` 단언을 뒤집었다 — 설계 §2.1 이 명시한 분기 추가의 직접 결과이며 Task 4 Step 1(3)이 근거 주석과 함께 교체한다.
- **BuildRunner 생성자 신규 인자에 기본값**(job_image=""·preflight 180)을 뒀다 — 기존 테스트 호출부 churn 회피. 실배선(wiring)은 항상 settings 를 명시하고, 스텁 경로는 인자가 아예 없다.
- **statvfs("/") 는 컨테이너 overlay 시점**을 본다 — nodefs=imagefs 동일(실측)이라 노드 fs 와 같지만, 노드 구성이 바뀌면(imagefs 분리) 공식의 전제가 깨진다 — 스크립트 주석에 미러 상수와 함께 명기했다.
- **전체 스위트 기대 수치(1128/228)는 근사 명시**다 — parametrize 전개를 셌지만, 수가 어긋나면 신규 테스트 수를 재계산하되 failed 0 이 판정 기준이다.




