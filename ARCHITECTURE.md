# DMS 아키텍처 (현재 상태 지도)

**지금 시스템이 어떻게 도는가.** 유지보수의 진입점이다. 메커니즘의 세부는 코드가
「왜」 주석으로 말하니, 이 문서는 **지도**(무엇이 어디 사는가)와 **불변식**(위반하면
깨지는 규약, 전부 `파일:줄`로 앵커)을 담는다. 왜 그렇게 됐나는
[`docs/history/`](docs/history/), 빌드 역사는 [`CHANGELOG.md`](CHANGELOG.md).

> 이 문서를 갱신하는 때: 코드를 바꿔 아래 **불변식 하나가 성립하지 않게 되거나, 새
> 불변식이 생기거나, 모듈의 책임이 바뀔** 때. 메커니즘을 자세히 바꾼 것만으로는 갱신
> 불필요 — 코드의 「왜」 주석이 진실이다.

## 1. 프로세스와 서브시스템

DMS 는 한 소스 트리(`src/dms/`)에서 나오는 **네 프로세스**로 돈다. `cli.py` 서브커맨드가
진입점이고, `wiring.py` 가 설정(`execution_backend` 등)에 따라 stub/실 어댑터를 조립한다.

```
                    ┌─────────────────────────────────────────────┐
   브라우저(SPA) ──▶│  api        create_app: FastAPI + React dist │
   스크립트(토큰) ─▶│             세션쿠키/공유토큰 이중 인증        │◀── /readyz(DB SELECT 1)
                    └───────────────┬─────────────────────────────┘
                                    │ Repositories (유일 DB 파사드)
                    ┌───────────────▼─────────────────────────────┐
                    │  PostgreSQL / SQLite   (db.py: 단일 커넥션+RLock+재연결)
                    └───────────────▲─────────────────────────────┘
                                    │ Repositories
   ┌────────────────────────────────┴────────────────────────────┐
   │  controller   run_forever: 리스 아래 run_once 루프들          │
   │    planner(10s)·job-stepper(5s)·reconciler(30s)·retention·    │──▶ Volcano/k8s
   │    batch-orchestrator·pod-gc·artifact-base-check              │    (execution adapter)
   │    (+build-watcher·rollout-watcher: runner 있을 때만)         │
   └────────────────────────────────▲────────────────────────────┘
                                    │ POST /api/agent/report
   ┌────────────────────────────────┴────────────────────────────┐
   │  agent  DaemonSet: 마운트·도구·신원·OS지표 프로브 → 보고       │
   │         응답으로 storages·artifact_base 설정 수신             │
   └──────────────────────────────────────────────────────────────┘
```

여섯 서브시스템(§4~§9에서 각각 상술):

| 서브시스템 | 한 줄 | 핵심 파일 |
|---|---|---|
| **제어면 루프** | 리스 아래 주기적 run_once — 요청→잡 상태기계를 굴린다 | `controller · planner · stepper · reconciler · batch_orchestrator · pod_gc · retention · build_watcher · rollout_watcher` |
| **데이터·영속** | 단일 커넥션 DB + 이중 경로 마이그레이션 + 12개 리포지토리 | `db · migrations · domain · repositories/*` |
| **실행·배포** | Volcano 잡 제출·폴링·로그 + 포탈 빌드 + 제어면 롤아웃 | `execution* · placement · build_* · rollout_* · dms_job_runner/*` |
| **API·포탈** | FastAPI + React dist, 이중 인증, 사유 코드 계약 | `api/* · frontend/src/*` |
| **에이전트·신원** | 노드 프로브·보고 + LDAP 신원 fail-closed 확정 | `agent/* · identity · identity_ldap` |
| **설정·배선·진입** | env 전수 검증 → frozen Settings → 어댑터 조립 | `config · wiring · cli · artifact_base · registry · metrics_series` |

## 2. 요청 → 잡 생명주기 (데이터가 흐르는 길)

```
API POST /api/user/requests
   → requests.Pending
   → [planner 10s] conflict→계정→storage→identity→placement 게이트 통과
        → data_jobs.create_job + requests.Planned          (게이트 실패: Rejected/Conflict — 원자적)
   → [job-stepper 5s] claim_steppable → 상태기계(_dispatch):
        scan:  Pending→Preflight→Running→(Succeeded|Failed|TimedOut)
        sync/rm: Pending→Preflight→Preview→ConfirmPending
                 →[사용자 confirm]→Executing(exec_preflight 재검증)→Running→종단
        각 phase: execution_adapter.submit/poll/read_summary/terminate
        종단: _finalize = set_job_state + requests.finalize_from_job (박제 후 전이)
```

병렬로: **reconciler** 가 에이전트 리포트 → `storages.status`(planner 의 storage
게이트 입력), **batch-orchestrator** 가 배치 item → `requests.create`(planner 입력
생산), **pod-gc/retention** 이 종단 잔재 정리, **build-watcher/rollout-watcher** 가
포탈 빌드·롤아웃을 굴린다.

## 3. 교차 불변식 (서브시스템을 관통하는 규약)

이 열 가지는 어디를 고치든 내면화해야 한다. 서브시스템별 세부는 §4~§9.

1. **DB 가 신뢰 경계다.** `create_job` 은 무검증 INSERT(`repositories/data_jobs.py`)라
   tool·경로가 변조될 수 있다. 방어는 3층: stepper 층1(`unknown_tool`, `stepper.py:258`),
   `_abs` 의 `posixpath.join + lstrip("/")`(변조 절대경로가 managed_root 를 못 벗어남,
   `stepper.py:91`), 러너 allowlist(`dms_job_runner/runner.py`).

2. **리스 crash-restart = 컨트롤러의 자기 종료.** 리스 획득은 의도적으로 per-loop try
   **밖**(`controller.py:114`)이라, 지속 DB 장애면 예외가 전파돼 프로세스가 죽고 재시작이
   새 커넥션을 얻는다(컨트롤러엔 HTTP 헬스가 없다). try 안으로 옮기면 "조용히 도는
   정지"가 된다 — `test_persistent_lease_death_still_crashes_the_controller` 가 집행부.

3. **종단 전이는 원자적이다.** 상태 전이 + `record_result` 를 한 트랜잭션으로:
   `finalize_from_job`·`set_state_with_result`(`repositories/requests.py`), rollout `_fail`.
   별도 커밋이면 사이 크래시가 "종단인데 results 없음"을 만들고, 종단 요청은 고아 스윕
   시야 밖이라 **영구 결손**이다. (naive `with transaction()` wrap 은 불가 — `set_state` 가
   이미 트랜잭션을 열어 중첩이 sqlite 즉사·PG 조용한 비원자. `_apply_state` 무트랜잭션
   몸통 경유가 처방.)

4. **null(모름) ≠ 실패 ≠ 0.** `if x:` truthy 검사로 뭉개지 마라. queue_reader 의
   None(모름) vs `[]`(빈 큐)(`queue_reader.py`), diag 로그 None vs `""`, 카운트의 0 —
   전부 `is None` 명시 비교. 리더/최초 관측이 접으면 상위가 못 되살린다.

5. **사유 코드는 양방향 계약.** `frontend/src/lib/reasonCodes.json` ∈ `src/dms/`(AST),
   `reasonCodes.json` ⊆ `api.ts` REASON_MESSAGES(프론트). AST 추출기는 `reason_code=`
   **키워드 리터럴**만 읽는다 — 위치 인자로 넘기면 커버리지 밖으로 샌다.

6. **스키마는 이중 경로.** 새 컬럼은 CREATE TABLE 과 `_ensure_columns`(구형 DB ALTER)
   **양쪽**(`migrations.py`). 전수 열거 그물(`test_migrations.py`)이 테이블·인덱스
   추가·삭제를 잡는다. 컬럼이 CREATE 에만 있으면 신규 DB 는 통과하고 기배포 DB 만
   500(슬라이스 14 실 사고).

7. **DB 가 env 를 이긴다** (artifact base). `resolve_artifact_base`(`artifact_base.py`)는
   `control_state` DB 값을 먼저 보고 NULL 이면 `settings.artifact_base_uri`. 포탈에서 바꾼
   값이 재적용에 안 되돌아가는 이유.

8. **매니페스트-우선 배포.** 이미지 태그를 먼저 bump·커밋하고 **그 커밋에서** 빌드한다
   (`Dockerfile.dms` 가 `deploy/k8s` 를 이미지에 COPY — 순서가 바뀌면 포탈 드리프트 배지).

9. **루프 인스턴스는 틱마다 재생성된다** (`build_loops` 의 람다). 인스턴스 변수는 다음
   틱까지 안 살아남는다 — 지속 상태(예: DaemonSet 진행 시계)는 반드시 DB 컬럼으로
   (`releases.progress`). 리스는 틱 중 갱신 안 되므로 **모든 루프 본체는 즉시 반환**해야 한다.

10. **record-then-patch + patch 직후 반환** (rollout). 릴리스는 DB 에 먼저 기록하고 patch
    한다. dms-controller 자기 패치면 곧 SIGTERM 이라 이후 관찰·DB 쓰기를 신뢰 못 하고,
    완료 판정은 **오직 클러스터 관찰**(다음 틱/후임 파드)로만 한다.

---
## 4. 제어면 루프 (Control-plane loops)

controller.run_forever가 monotonic 스케줄로 루프별 리스(loop:<name>)를 틱마다 획득한 뒤 planner/stepper/reconciler/retention/batch-orchestrator/pod-gc/artifact-base-check(+조건부 build-watcher/rollout-watcher)의 run_once를 반복 실행하며, 루프 본체 예외는 루프 단위로 격리하고 리스 획득의 지속 DB 장애만 프로세스를 죽인다.

**데이터·제어 흐름**: run_forever(controller.py:138): 1초 틱마다 next_due 지난 루프를 run_all_once([loop])로 실행 → try_acquire_lease("loop:<name>", holder, max(interval*3,30)s) 실패면 skipped_lease, 성공이면 fn() 실행(예외는 "error:<Type>"으로 접힘). 데이터 경로: 요청 생성(API) → requests.Pending → planner가 게이트 통과 시 data_job 생성+Planned → stepper가 Pending→Preflight→(scan: Running→종단 / sync·rm: Preview→ConfirmPending→[confirm]→Executing(exec_preflight 재검증)→Executing/Running→종단), 각 phase는 execution_adapter.submit/poll/read_summary/terminate 뒤 → _finalize가 set_job_state+requests.finalize_from_job. 병렬로: reconciler가 agents.fresh_reports→storages.status(planner의 storage admission 입력), batch-orchestrator가 batch item→requests.create(planner 입력 생산), pod-gc·retention이 종단 잔재 정리, build-watcher가 builds.pending/running→파드 제출·poll→builds.finish, rollout-watcher가 releases.active head→patch_image→observe→finish/abort.

### 모듈

| 파일 | 책임 |
|---|---|
| `src/dms/controller.py` | 숙주: build_loops(루프 조립, 68-107행)·run_all_once(리스+예외 격리, 111행)·run_forever(monotonic next_due, sleep(1) 틱, 138행). _stepper_step(35행)이 스텝+preview 만료+고아 스윕을 한 루프에 묶는다. |
| `src/dms/planner.py` | planner(기본 10s): Pending 요청 50건을 conflict→계정→storage→identity→placement→fanout 게이트로 걸러 create_plan+create_job 후 Planned 전이. 신원 전파 유예(_identity_grace_active, 기본 300s)면 상태 무변경 defer. |
| `src/dms/stepper.py` | job-stepper(기본 5s): drain 게이트 후 claim_steppable 잡을 상태기계(_dispatch, 273행)로 전진. 미지 tool·스토리지 결측은 _fail_closed로 종단, 실패 종단 전 diag 박제(_archive_diag), 취소 경합은 _reclaim_if_terminal이 회수. |
| `src/dms/reconciler.py` | storage-reconciler(기본 30s): 신선 에이전트 리포트만으로 storages.status를 Unknown/Ready/Degraded로 재계산, 값이 바뀔 때만 set_status. |
| `src/dms/batch_orchestrator.py` | batch-orchestrator(기본 5s): 활성 배치의 Queued item을 max_concurrency 쓰로틀로 request로 materialize, 자식 종단 집계(counts bump), Previewing→PreviewReady / Running에서 ConfirmPending 자식 쓰로틀 confirm, preview 만료는 Queued로 reset. |
| `src/dms/pod_gc.py` | pod-gc(기본 600s, 창 86400s): 종단 잡의 pod/-접두 phase_refs와 종단 빌드의 빌드·프로브 파드만 terminate. ref별 예외 격리. |
| `src/dms/retention.py` | retention(기본 3600s): agent_reports·events를 보존일(기본 30d) 밖에서 배치 5000건 삭제. correctness 아닌 최적화. |
| `src/dms/build_watcher.py` | build-watcher(기본 15s, build_runner 있을 때만): Pending 빌드는 프리플라이트 프로브 파드(멱등 제출)→OK 마커 확인 후 빌드 파드 제출, Running 빌드는 poll→finish. 나이 기반 회수(preflight 180s/build 7200s)와 I6 일시 오류 관용이 짝. |
| `src/dms/rollout_watcher.py` | rollout-watcher(기본 10s, rollout_runner 있을 때만): 릴리스 head(최소 seq)만 record-then-patch로 적용하고 완료는 오직 클러스터 관찰로 판정. 실패는 finish+abort_pending 단일 트랜잭션(_fail), 벽시계 회수(_reclaim)가 observe보다 먼저. |
| `src/dms/queue_reader.py` | 컨트롤러 루프가 아님 — api/app.py:51이 app.state에 주입하고 routes_metrics.py:189가 요청 시 읽는 Volcano 큐 가시성 리더(None=모름 vs []=빈 큐 구분이 최초 권위). |
| `src/dms/artifact_base.py` | artifact-base-check 루프 본체 controller_check_once(84행): artifact_base를 컨트롤러 자기 파일시스템에서 왕복 검증해 control_state에 uri+ok+reason 기록(간격은 reconcile과 공유). |

### 불변식 (위반하면 깨진다)

- 리스 획득은 의도적으로 per-loop try 밖(controller.py:114-127, run_all_once): 지속 DB 장애 시 예외 전파로 프로세스가 죽어야 한다(crash-restart = HTTP 헬스 없는 컨트롤러의 자기 종료 동등물). try 안으로 옮기면 조용히 도는 정지가 된다 — test_persistent_lease_death_still_crashes_the_controller가 집행부.
- 리스 규약(repositories/control.py:145 try_acquire_lease): 같은 holder는 항상 갱신, 다른 holder는 expires_at 경과 후에만 탈취. lease_seconds = max(interval*3, 30) (controller.py:122-124). 리스는 틱 중 갱신되지 않으므로 모든 루프 본체는 즉시 반환해야 한다(rollout_watcher.py:5-7 주석이 명문화).
- stepper 층1 가드(stepper.py:258, _step_one): tool이 TOOL_TO_POLICY 밖이면 제출 전 _fail_closed(unknown_tool) — DB가 신뢰 경계(create_job은 무검증 INSERT)이고, fall-through하면 drm 꼴 argv(파괴적)로 실행된다.
- _abs는 posixpath.join + rel.lstrip("/")(stepper.py:91-109): lstrip 없으면 join이 절대경로 둘째 인자에서 root를 버려 변조된 절대 target이 managed_root 밖을 지운다. root 결측은 StorageMissingAtStep으로 fail-closed(폴백 금지 — 예전 폴백은 조용한 데이터 증발).
- diag 박제는 종단 전이 **전**(stepper.py:151-157, _finalize): 박제 후 크래시면 다음 틱이 finalize 재시도(archive는 IS NULL이 중복 방지), 역순이면 종단 잡은 다시 스텝되지 않아 박제 기회가 영영 사라진다.
- 제출 직후 _reclaim_if_terminal 재독(stepper.py:203-225): claim_steppable 스냅샷엔 잠금이 없어(autocommit) claim~제출 사이 취소된 잡의 파드가 클러스터 고아가 되는데, cancel_job은 종단에 409·terminate_job은 no-op이라 여기서 안 치우면 아무도 못 치운다.
- pod-gc는 종단 잡·종단 빌드만(pod_gc.py:1-8, 39-44): 비종단 잡 파드를 지우면 stepper가 실패로 오인, 비종단 빌드 파드를 지우면 poll이 FAILED로 읽고, 프로브 파드를 지우면 워처가 멀쩡한 빌드를 build_preflight_failed로 죽인다.
- rollout _fail의 finish+abort_pending은 단일 트랜잭션(rollout_watcher.py:44-59): 따로 커밋하면 사이 크래시로 "앞 실패 시 뒤 중단"이 반대로 깨지거나 새 배치가 rollout_aborted로 오살된다. 이 트랜잭션 안에서 patch 호출 금지(record-then-patch 계약 위반).
- rollout은 patch 직후 반드시 반환(rollout_watcher.py:157-162): dms-controller 자기 패치면 곧 SIGTERM — 이후 관찰/DB 쓰기를 신뢰할 수 없고, 완료 판정은 오직 클러스터 관찰(다음 틱/후임 파드)로만 한다.
- rollout _fail의 reason_code는 반드시 키워드 인자 + 절단은 함수 안(rollout_watcher.py:33-41): tests/test_reason_codes_coverage.py 추출기가 reason_code= 키워드 리터럴만 읽는다 — 위치 인자·호출부 [:200]이면 사유 코드가 커버리지 가드 밖으로 샌다.
- build-watcher 회수 판정은 프로브 생성보다 먼저(build_watcher.py:98-104): 프로브가 스케줄조차 안 되면 activeDeadlineSeconds가 발화하지 않아 이 순서가 유일한 탈출구. _reclaim은 로그 박제→terminate→Failed 순(terminate 먼저면 유일한 증거 소멸, 74-88행).
- I6 관용구 + 나이 회수는 반드시 짝(build_watcher.py:117-124, 161-166 / rollout_watcher.py:202-205): poll 일시 오류는 Failed 못박지 않고 다음 틱 재시도, 영구 오류는 벽시계 회수가 푼다 — 한쪽만 있으면 오판 또는 영구 잠김.
- rollout 벽시계 회수(_reclaim)는 observe보다 먼저(rollout_watcher.py:108-112): 조회가 지속 실패해도 회수는 돼야 배치 잠김이 풀린다. COMPONENTS 좌표 조회도 try 안(133-147행) — 밖에서 KeyError면 회수 코드가 도달 불능.
- planner _reject·conflict는 set_state_with_result 원자 메서드(planner.py:73-80, 133-137): set_state+record_result 별도 커밋이면 사이 크래시가 "Rejected인데 results 없음"(고아 스윕 시야 밖 영구 결손)을 만든다.
- 고아 스윕은 행 단위 격리(controller.py:48-60): 독 행 하나의 실패가 orphan_recovery_failed 이벤트로 남고 나머지를 굶기지 않는다. finalize_from_job은 멱등(이미 터미널이면 no-op)이라 재호출 안전.
- build 프로브 사유는 화이트리스트 3종만 채택(build_watcher.py:23-27, 149-151): 프로브 로그는 신뢰 입력이 아니다 — 밖이면 build_preflight_failed로 접는다. 세 코드 철자는 build_manifests._PROBE_SCRIPT·frontend reasonCodes.json과 동일해야 한다.
- queue_reader의 None(모름) vs [](빈 큐) 구분은 리더가 최초 권위(queue_reader.py:1-3): 여기서 한 번 접히면 상위 계층이 되살릴 수 없다. 큐 이름 dms-data는 RBAC resourceNames와 결합돼 설정으로 빼지 않는다.

### 함정 (모르면 밟는다)

- build-watcher·rollout-watcher는 runner가 None이면 루프 자체가 조립되지 않는다(controller.py:88-107) — 배선 누락 시 조용히 빠지고 에러가 없다.
- stepper는 control_state.drain이면 완전 no-op(stepper.py:72-74)이지만 controller의 _stepper_step 안 preview 만료·고아 스윕은 drain과 무관하게 돈다(controller.py:37-60).
- artifact-base-check와 storage-reconciler는 같은 설정 키(reconcile_interval_seconds)를 공유한다(controller.py:82-86) — 간격 튜닝이 둘 다에 걸린다.
- JobStepper·Planner·워처들은 틱마다 새로 생성된다(build_loops의 람다) — 인스턴스 변수는 다음 틱까지 살아남지 않는다. RolloutWatcher의 DaemonSet 진행 시계가 releases.progress 컬럼으로 지속되는 이유(rollout_watcher.py:87-90).
- planner 유예 이벤트(identity_propagating)는 사유(payload)가 바뀔 때만 기록(planner.py:100-114) — 틱마다 남기면 grace 300s에 최대 30건이 이벤트 목록(limit 100)을 덮는다.
- planner 유예 판정은 "identity_not_ready_on_node 노드가 하나라도"(planner.py:18-33): eligible_nodes가 노드당 첫 실패 사유 하나만 기록하고 identity 검사가 마지막이라 성립 — "모든 노드" 요구로 바꾸면 실 테스트베드(일부 노드 미마운트)에서 유예가 아예 발동하지 않는다.
- diag 꼬리 자르기는 바이트 기준이고 UTF-8 경계 조각은 버린다(stepper.py:39-50): errors="replace"로 넘기면 U+FFFD 부풀림으로 상한(16KB x 4 = 64KB, builds.LOG_TEXT_MAX와 계약 테스트로 곱 고정)을 넘는다.
- exec_preflight의 phase 이름이 초기 preflight와 다른 이유(stepper.py:429-432): 파드 이름이 phase를 포함해서, 같으면 초기 preflight 파드 잔존 시 AlreadyExists→submit_failed.
- _build_spec에서 policy None은 크래시가 아니라 타임아웃 없음 관용(stepper.py:135-141) — 층1 가드 이후 policy None은 "정책 행이 지워진" 운영 조작뿐.
- batch 자식은 auth_method="token"으로 생성(batch_orchestrator.py:66-73) — 기계 materialize라 특권 없이 실 LDAP 신원로만 돈다. planner의 특권 판정(session_authenticated)이 이 값에 걸린다.
- DaemonSet 진행 시계 리셋엔 세대 게이트 선행(rollout_watcher.py:99-105): 패치 직후 옛 세대 status의 updated==desired를 믿고 progress를 올리면 이후 진짜 진행이 시계를 한 번도 못 리셋한다.
- Deployment에는 진행 시계 리셋을 하지 않는다(rollout_watcher.py:92-95): applied_at이 sticky PDE 판별 기준이라 앞당기면 진짜 PDE까지 stale로 읽혀 유일한 종단 수단이 사라진다. 대신 타임아웃 x3(_DEPLOY_TIMEOUT_FACTOR).
- build 프로브 SUCCEEDED인데 OK 마커 미확인이면 실패를 지어내지 않고 다음 틱 재시도(build_watcher.py:131-135) — 로그 일시 결손 대비, 최후 회수는 preflight 타임아웃.
- queue_reader.py는 이 과제 목록에 있지만 컨트롤러 루프가 아니다 — build_loops에 없고 api/app.py:51에서 app.state로 주입돼 라우트가 요청 시 읽는다.
- Queue는 반드시 이름 지정 GET(queue_reader.py:14-16): RBAC resourceNames는 list에 적용되지 않는다(저장소가 두 번 적어 둔 함정). PodGroup은 이름 규칙·라벨이 계약이 아니라 목록+필터만 안전.
- _stepper_step의 orphan 스윕 상한(오래된순 LIMIT 200)은 리포지토리(terminal_jobs_with_live_request) 몫이다 — controller.py에는 상한 코드가 안 보인다. 0건 스윕은 정상값이라 아무것도 기록하지 않는다.

### 결합점

- execution_adapter(execution.py StubExecutionAdapter 또는 실 어댑터): stepper·pod-gc가 submit/poll/read_summary/read_log/terminate를 호출 — 파드/vcjob 생명주기의 유일한 경계.
- repositories/*: control(리스 try_acquire_lease·control_state·get_policy·set_artifact_base_check), requests(list_pending·finalize_from_job·set_state_with_result), data_jobs(claim_steppable·expire_previews·terminal_jobs_with_live_request·archive_diag_logs·mark_exec_submitted), agents(fresh_reports·prune_reports), storages(get/set_status), batches, builds(pending/running/finish/terminal_older_than), releases(active/finish/abort_pending/note_progress), observability(record_event·prune_events).
- placement.py: planner의 select_tool_and_candidates/resolve_fanout/TOOL_TO_POLICY — stepper도 같은 TOOL_TO_POLICY로 정책 조회·층1 가드.
- identity.py resolve_job_identity: planner 4단계 게이트. identity_resolver는 run_forever 인자로 주입.
- artifact_base.py resolve_artifact_base(DB가 env를 이김)·controller_check_once: stepper의 artifact_uri 생성과 artifact-base-check 루프가 공유.
- build_runner(BuildRunner): build-watcher의 submit_preflight/submit/poll/read_log/failure_reason·pod-gc의 빌드 파드 terminate. 파드 이름은 repositories/builds.build_pod_name·build_probe_pod_name에서 결정적.
- rollout_runner: rollout-watcher의 patch_image/observe/pod_briefs. 판정은 rollout_status.assess_deployment/assess_daemonset, 좌표는 repositories/releases.COMPONENTS.
- api 계층: batch-orchestrator가 만드는 requests를 planner가 소비(생산-소비 사슬), reconciler가 쓰는 storages.status를 planner storage admission이 소비. queue_reader는 api/routes_metrics.py:189가 소비.
- frontend/src/lib/reasonCodes.json + tests/test_reason_codes_coverage.py: 워처들이 만드는 reason_code 문자열의 커버리지 가드.

---

## 5. 데이터·영속 (Data & persistence)

단일 커넥션 + RLock 직렬화 DB 계층(db.py) 위에 CREATE/_ensure_columns 이중 경로 마이그레이션(migrations.py)과 도메인 검증(domain.py), 그리고 테이블별 write-once·원자화 규약을 강제하는 12개 리포지토리로 SQLite/PostgreSQL 이중 방언 영속을 제공한다.

**데이터·제어 흐름**: API/컨트롤러 → Repositories(repositories/__init__.py, 유일 진입점) → Database.execute/query(db.py) → _run(RLock 직렬화 → _adapt 방언 변환 → 실행 → PG 죽음 판정 시 _reconnect+1회 재시도) → SQLite 파일 or PostgreSQL. 쓰기 경로: 리포지토리 메서드가 db.transaction()으로 BEGIN→(업무 UPDATE/INSERT + state_transitions/audit_log 동반 기록)→COMMIT; 진단은 예외로 observability.record_event가 트랜잭션 밖 단독 INSERT. 기동 경로: initContainer/one-shot Job → migrate(db) → pg_advisory_lock → CREATE IF NOT EXISTS 스크립트 → DROP runs → _ensure_columns(ALTER 보강) → _widen_count_columns → 인덱스 생성 → _backfill_submit_wait → 시드(policies/control_state). 읽기 집계: metrics.job_stats가 typed 컬럼 GROUP BY + 파이썬측 시각 감산으로 대시보드 응답을 만든다.

### 모듈

| 파일 | 책임 |
|---|---|
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/db.py` | 단일 커넥션 DB 계층: named param(:name)→방언 변환(_adapt), RLock 전역 직렬화, PG 죽음 판정(_connection_is_dead 이중 게이트)+재연결 1회 재시도(_run), transaction() 컨텍스트(_txn_depth로 트랜잭션 중 재시도 금지), connect_timeout=5s, utc_now_iso/iso_plus/iso_epoch/dump_json/load_json 공용 유틸. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/migrations.py` | 전체 스키마 선언 스크립트: pg_advisory_lock(MIGRATE_LOCK_KEY)으로 migrate() 전 구간 직렬화, CREATE TABLE IF NOT EXISTS(신규 DB) + _ensure_columns ALTER(구형 DB) 이중 경로, _widen_count_columns(int4→BIGINT), _backfill_submit_wait, DROP TABLE runs(유일 파괴적 마이그레이션), policies/control_state 멱등 시드, ALL_TABLES(19개) 열거. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/domain.py` | DB 무지 도메인 모델: 상태 enum(RequestState/DataJobState + TERMINAL_* frozenset), 경로 검증(validate_relative_path/sync_paths/rm_target), 연산별 옵션 allowlist(_OPTION_SPECS→validate_options), option_fingerprint(sha256), build_resource_key/build_data_payload, resolve_priority. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/__init__.py` | Repositories 집합체: db 하나로 12개 리포지토리(accounts/agents/requests/storages/control/data_jobs/batches/scan_paths/builds/observability/releases/metrics)를 묶어 API·컨트롤러의 유일한 DB 진입점이 된다. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/data_jobs.py` | data_jobs+plans: create_plan/create_job(전이 기록 포함 트랜잭션), set_job_state(종단 가드+submit_wait write-once), claim_steppable(FOR UPDATE SKIP LOCKED), mark_exec_submitted/record_sched_wait/archive_diag_logs(IS NULL 술어 write-once), terminal_jobs_older_than/terminal_jobs_with_live_request(오래된순 LIMIT 200 GC·고아 스윕), _ROW_COLUMNS_SANS_DIAG(다행 조회에서 diag_logs 배제). |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/requests.py` | requests+results+state_transitions: create(MAX(commit_order)+1, auth_method 기본 token fail-closed), set_state/_apply_state(경계·몸통 분리), finalize_from_job/set_state_with_result(전이+results INSERT 원자화), find_active/last_reason_code/has_active_for_requester. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/storages.py` | storages CRUD+감사: _validate가 노드 루트 "/" 등록을 명시 거부(storages.py:16-29), managed_root⊆mount_path 강제, create/update/delete 전부 트랜잭션 안에서 audit_log 동반. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/control.py` | policies(delete+insert upsert)/identity_denylist/control_state 싱글톤(id=1)/set_artifact_base·set_artifact_base_check(컬럼 분리 UPDATE)/component_leases(try_acquire_lease)/identity_probe_targets/audit_entries. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/builds.py` | builds: create가 active() 확인+INSERT를 한 트랜잭션으로 묶어 '활성 빌드 1개' 강제, seq=MAX+1 단조 증가, finish(log_text 꼬리 64KB 절단, 종단 가드 술어), list는 log_text 제외(I2), build_tag/build_pod_name 결정적 이름. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/releases.py` | releases: ROLLOUT_ORDER(agent→api→controller)·COMPONENTS 좌표표, create_batch(active 가드+seq 지속화 원자), _ORDER('seq IS NULL ASC, seq ASC, id ASC' 방언 중립 NULL 정렬), mark_applying/finish/note_progress(정체 시계 재장전)/abort_pending. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/observability.py` | events 진단 채널: record_event는 절대 예외를 안 올리고(try/except+logger.warning) 업무 트랜잭션 밖 단독 INSERT, prune_events는 배치 5000행씩 독립 트랜잭션으로 전량 소진 루프. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/metrics.py` | 읽기 전용 대시보드 집계: agent_reports blob은 앱측 파싱, data_jobs typed 컬럼만 SQL GROUP BY(SUM CASE, FILTER 금지), duration은 파이썬에서 감산, submit/sched_wait는 IS (NOT) NULL 술어 2쿼리+excluded 건수 표면화. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/accounts.py` | accounts: scrypt 해시(_hash/_verify_password), create/set_role/set_disabled/delete 전부 감사 동반 트랜잭션, delete는 user_scan_paths 동반 삭제, active_admin_count(마지막 관리자 잠금 방지 재료). |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/agents.py` | agent_reports(이력)+agent_nodes(노드별 최신 1행)를 ingest 한 트랜잭션으로 동기 유지, fresh 판정은 문자열 시각 비교, prune_reports 배치 소진 루프. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/batches.py` | batches+batch_items: create(헤더+항목 N행 원자 INSERT), 항목 상태 전이(_touch_item), bump_counts 증분 갱신, reset_failed_items(재시도용 Queued 복귀+카운트 차감). |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/scan_paths.py` | user_scan_paths: covers() 조상-경로 커버 판정(순수 함수), add(사전 존재 확인→INSERT→id 재조회), get_owned/delete_owned 소유자 스코프 강제. |

### 불변식 (위반하면 깨진다)

- db.py:112-126 _run — 재연결+재시도는 _txn_depth==0이고 _connection_is_dead가 참일 때만 정확히 1회. 트랜잭션 중(_txn_depth>0) 재시도는 부분 적용을 '만들어내는' 동작이라 금지(db.py:49-52).
- db.py:172-209 transaction() — COMMIT이 죽음으로 실패하면 재시도 절대 금지: 새 커넥션의 COMMIT은 빈 트랜잭션 no-op '성공'이라 유실을 성공으로 위장한다. 죽은 커넥션엔 ROLLBACK도 생략(원 예외 보존).
- db.py 트랜잭션은 중첩 불가 — BEGIN이 무조건이라 sqlite는 즉사, PG는 안쪽 COMMIT이 바깥을 조기 커밋해 조용히 비원자가 된다. 재사용은 requests._apply_state처럼 경계/몸통 분리로만(requests.py:81-98 주석).
- migrations.py:474-515 _ensure_columns — 새 컬럼은 CREATE TABLE과 _ensure_columns **양쪽**에 넣어야 한다(슬라이스 14 실 500 교훈: 한쪽만 넣으면 라이브에서만 컬럼이 없다). data_jobs 컬럼은 data_jobs.py:25 _ROW_COLUMNS_SANS_DIAG까지 세 곳(tests/test_repo_diag_logs.py 컬럼 패리티 계약).
- migrations.py:38-47 — migrate() 전 구간이 pg_advisory_lock(0x444D5310)으로 직렬화된다. 락 키는 2**63 미만 유지 필수(psycopg가 큰 int를 numeric으로 보내 pg_advisory_lock(numeric) 미존재로 initContainer 전멸, migrations.py:19-23).
- migrations.py:361-371 — DROP TABLE IF EXISTS runs는 반드시 CREATE 루프 **뒤**(tests/test_migrations.py:505 소스 순서 계약). ALL_TABLES는 도메인 19개이고 실 DB는 +batches/batch_items/schema_migrations=22개 — tests/test_migrations.py:522가 양방향 등식으로 전수 고정, 인덱스 16개도 등식 고정.
- results.request_id는 PK — 중복 INSERT는 UniqueViolation. finalize_from_job(requests.py:162-186)·set_state_with_result(requests.py:188-201)가 전이+results INSERT를 한 트랜잭션으로 원자화해 '종단인데 results 없음'과 PK 중복 창을 함께 닫는다.
- data_jobs.set_job_state(data_jobs.py:143-183) — 종단 상태는 절대 되돌리지 않는다(조용히 무시 + 트랜잭션 밖 terminal_guard_skip 이벤트). 일어나지 않은 전이는 state_transitions에 기록하지 않는다.
- write-once 3종은 SQL 술어(IS NULL)가 최종 강제: submit_wait_seconds(set_job_state의 Pending→비Pending 엣지), exec_submitted_at(mark_exec_submitted, data_jobs.py:229-240), sched_wait_seconds(record_sched_wait, data_jobs.py:260-290), diag_logs(archive_diag_logs, data_jobs.py:242-258). _backfill_submit_wait도 IS NULL 필터로 이 계약을 우회하지 않는다(migrations.py:518-527).
- requests.commit_order / builds.seq / releases.seq — 단조 증가는 DB 제약이 아니라 애플리케이션(MAX+1)이 지킨다. builds.seq에 UNIQUE/NOT NULL을 못 거는 이유: SQLite ALTER ADD COLUMN 제약 불가로 CREATE/ALTER 두 경로가 같은 스키마로 수렴해야 함(migrations.py:269-274 주석, releases.py:38-48).
- '활성 1개' 가드는 존재 확인+INSERT를 같은 트랜잭션(=같은 RLock 구간)에서: builds.create(builds.py:62-74), releases.create_batch(releases.py:72-77). 단 프로세스 경계는 못 넘는다 — replicas=1 전제.
- GC·고아 스윕은 오래된순(updated_at ASC)+LIMIT 200: terminal_jobs_older_than(data_jobs.py:340-362, updated_at 단조 비감소 전제), terminal_jobs_with_live_request(data_jobs.py:364-386, finalize 멱등+처리 행이 술어에서 빠져 틱마다 전진).
- observability.record_event는 절대 예외를 올리지 않고 업무 트랜잭션 밖에서 단독 INSERT(observability.py:16-29) — 진단 실패가 업무 변경을 롤백하면 본말전도.
- storages._validate(storages.py:10-34) — mount_path/managed_root에 '/'(노드 루트) 명시 거부, managed_root는 mount_path 하위여야 함. 검증은 create/update에만 발화 — 기존 '/' 행은 stepper._abs가 2차 방어.
- DB_CONNECT_TIMEOUT_SECONDS=5(db.py:13-20)는 프로브 주기 10s보다 짧아야 매 프로브가 반드시 503으로 끝난다 — 10 이상으로 올리면 자기 종료(§2.4)가 영영 발화하지 않는 결함이 부활한다. URL이 connect_timeout을 명시하면 그 값이 이긴다(db.py:85).
- domain.py는 DB를 모른다(domain.py:1) — 검증·상태머신·fingerprint만. 시각 산술은 SQL로 이식 불가(julianday=SQLite, EXTRACT=PG 전용)라 전부 파이썬 iso_epoch로 뺀다(db.py:33-38).

### 함정 (모르면 밟는다)

- db.transaction()을 naive하게 중첩하면 안 된다 — requests.set_state가 이미 트랜잭션을 연다. 다른 트랜잭션 안에서 상태 전이를 원자화하려면 _apply_state(몸통)를 직접 불러야 한다(finalize_from_job이 그 두 번째 소유자, requests.py:81-88).
- _connection_is_dead는 sqlite에서 항상 False(sqlite OperationalError는 문법 오류·no-such-table 포함) — 재연결 로직은 PG 전용이고, 직접 생성(_url=None, 테스트 더블 관례)된 Database도 죽음 처리를 아예 안 한다(db.py:95-110).
- data_jobs에 컬럼을 추가하면 고칠 곳이 세 곳이다: migrations의 CREATE, _ensure_columns, data_jobs._ROW_COLUMNS_SANS_DIAG(data_jobs.py:20-31). 다행 조회 4곳은 diag_logs(행당 64KB)를 절대 싣지 않는다 — get_job(단행)만 SELECT *.
- PG의 files_count/bytes_count int4 천장(2147483647): 기배포 DB는 _ensure_columns가 타입을 안 바꾸므로 _widen_count_columns가 별도로 넓힌다 — 현재 타입이 integer일 때만 ALTER해 매 배포 ACCESS EXCLUSIVE 락을 피한다(migrations.py:450-471).
- _column_exists는 information_schema를 current_schema()로 좁혀 본다 — 안 좁히면 다른 스키마(백업 복원 backup.data_jobs)의 동명 컬럼을 '이미 있다'로 오판해 ALTER를 건너뛰고 라이브만 컬럼이 없는 500이 재현된다(migrations.py:437-447).
- releases 정렬은 반드시 _ORDER('seq IS NULL ASC, seq ASC, id ASC') — 'seq ASC'만 쓰면 SQLite는 NULL을 먼저, PG는 나중에 놓아 head 선정이 방언마다 갈린다(releases.py:43-49).
- COMPONENTS의 init_container 키는 컴포넌트별 — dms-agent에는 키 자체가 없어야 한다. 없는 initContainer를 strategic merge로 패치하면 병합이 아니라 새 컨테이너 생성이 된다(releases.py:13-20).
- set_control_state는 build_node_name을 무조건 쓰므로 인자 생략 호출이 기존 값을 NULL로 지운다 — 그래서 artifact_base 계열은 해당 컬럼만 만지는 전용 UPDATE로 분리돼 있다(control.py:113-142). 같은 자리에 컬럼을 얹으면 함정이 복제된다.
- set_job_state의 actor는 _guard_component로 정규화된다 — stepper/batch-orchestrator 외(API 사용자명)는 전부 'api'로 접는다. 이벤트 component에 사용자명이 새면 고카디널리티로 대시보드 그룹핑이 깨진다(data_jobs.py:11-18, 34-35).
- metrics 집계에서 submit/sched_wait 술어는 반드시 IS (NOT) NULL — falsy 검사(COALESCE=0)로 바꾸면 0(같은 초 픽업/같은 틱 스케줄이라는 정상값)이 미기록으로 새 나간다(metrics.py:112-142). diag_logs entries의 log도 null(못 얻음)과 ""(빈 로그)를 구분한다.
- submit_wait_seconds는 DMS 내부 픽업 지연(스테퍼 틱 간격 포함)이지 Volcano 큐 대기가 아니다 — sched_wait_seconds가 큐 대기의 근사(틱 5s+status 갱신 지연 포함). 컬럼명·화면 라벨이 이 구분을 계약으로 갖는다(migrations.py:162-194).
- mark_exec_submitted/archive_diag_logs는 updated_at을 건드리지 않는다 — 클레임 순서(ORDER BY updated_at)·GC 나이 계산에 끼어들면 안 되기 때문(data_jobs.py:234-236, 246-248).
- prune_events/prune_reports는 배치가 남는 한 계속 도는 소진 루프다 — 틱당 1배치만 지우면 유입이 1.4행/초를 넘는 순간 영원히 못 따라잡는다(observability.py:47-67).
- builds.list는 SELECT *를 쓰면 안 된다 — log_text 64KB×50행×5초 폴링=3.2MB 왕복(I2, builds.py:93-103). data_jobs의 diag_logs 배제와 같은 계열.
- control.upsert_policy는 DELETE+INSERT라 트랜잭션 필수(control.py:32-43); migrate의 policies 시드는 WHERE NOT EXISTS라 운영자가 포탈에서 고친 값을 절대 되돌리지 않는다(migrations.py:406-430).
- requests.active_referencing_storage는 비종단 요청 전량을 가져와 payload JSON을 앱측에서 훑는다(requests.py:117-128) — payload가 TEXT라 SQL로 못 거른다. 비종단 행이 많으면 비싸진다.

### 결합점

- repositories/__init__.py의 Repositories가 API 라우트(src/dms/api/)와 컨트롤러(controller.py)·플래너(planner.py)·스테퍼(stepper.py)·배치 오케스트레이터(batch_orchestrator.py)에 주입되는 유일한 DB 파사드다.
- migrate(db)는 cli.py와 테스트 conftest, k8s initContainer(40-api.yaml/41-controller.yaml의 migrate)와 one-shot Job(30-migrate-job.yaml)이 호출한다 — 시그니처 불변 계약.
- db.Database.on_reconnect 훅은 wiring.py가 record_event(events 테이블)로 배선한다; reconnect_count/last_reconnect_at은 /readyz 200 본문이 읽는다(db.py:53-57).
- data_jobs.claim_steppable/set_job_state/mark_exec_submitted/record_sched_wait/archive_diag_logs는 stepper.py가 소비한다; set_job_state의 submit_wait 엣지와 stepper 틱이 짝이다.
- data_jobs.terminal_jobs_older_than은 pod_gc.py(Volcano/preflight 파드 회수), terminal_jobs_with_live_request는 controller.py 고아 스윕(finalize_from_job 재시도)이 소비한다.
- requests.finalize_from_job/set_state_with_result는 각각 스테퍼 종단화·planner의 Rejected/Conflict 판정이 부른다; requests.active_referencing_storage는 storages 삭제/변경 가드(storage_in_use)가 쓴다.
- builds/releases 리포지토리는 build_runner.py·build_watcher.py·rollout_runner.py·rollout_watcher.py(controller.build_loops)가 상태기계 저장소로 쓴다; releases.COMPONENTS/ROLLOUT_ORDER가 k8s 매니페스트의 워크로드·컨테이너 이름과 결합돼 있다.
- control.try_acquire_lease(component_leases)가 컨트롤러 리더 리스의 실체 — db.transaction() BEGIN 시점 재연결 허용이 컨트롤러 무크래시 같은-틱 복구를 만든다(db.py:175-180).
- metrics.job_stats/node_series는 대시보드 API가 소비하고, idx_data_jobs_created(_sched) 커버링 인덱스(migrations.py:383-394)와 짝이다; agent_reports/agent_nodes는 agent/ 리포트 ingest 경로가 생산한다.
- domain.py의 검증·resource_key·fingerprint는 API 제출 경로와 planner가 공유한다(옵션 allowlist는 runner의 mpifileutils 실 플래그와 대응).
- retention.py가 observability.prune_events/agents.prune_reports를 주기 호출한다(retention_interval 기준 배치 소진).

---

## 6. 실행·배포 (Execution & rollout)

데이터 잡(scan/sync/rm)을 프리플라이트 Pod와 Volcano Job(launcher+sshd 워커, mpirun/runuser)으로 제출·폴링·로그/summary 수집하고, 같은 KubernetesClient 위에서 포탈 빌드(buildah Pod + egress/디스크 프로브)와 제어면 롤아웃(Deployment/DaemonSet 이미지 strategic-merge 패치 + 수렴 판정)을 수행한다.

**데이터·제어 흐름**: 잡: stepper(_step_one 층1 unknown_tool 가드) → planner가 placement.select_tool_and_candidates+resolve_fanout으로 도구·후보·큐·프로세스 수 확정 → stepper가 JobSpec 조립(절대경로 포함) → VolcanoExecutionAdapter.submit: phase가 preflight/exec_preflight면 build_preflight_pod(nsync는 src+dst 두 파드, 복합 ref \"pods/a,b\"), 아니면 build_volcano_job(vcjob) → k8s create → poll(ref prefix로 Pod/vcjob GET, DeadlineExceeded→TIMED_OUT; 복합 ref는 fail-closed 결합) → 잡 파드 안에서는 launcher가 dms-job-runner 실행: hostfile→rank.sh(\"exec {tool} argv\")→runuser mpirun→artifact_dir(strip_scheme(base)/job_id/phase)에 summary.json/stdout/stderr → 어댑터 read_summary(read_text로 파일 읽기, 재시작 시 라벨로 경로 재구성)/read_log(vcjob은 volcano.sh/job-name 라벨로 launcher 전부+Failed 워커만) → 종료는 terminate(delete, 404 멱등). 빌드: api/routes_builds 제출 → build_watcher 틱 → BuildRunner.submit_preflight(프로브 Pod) → poll+read_log 마커(DMS_PREFLIGHT_OK / DMS_PREFLIGHT_REASON=…) → submit(buildah Pod: git clone→buildah bud 3종→insecure push) → 로그 마커(DMS_COMMIT_SHA/DMS_BUILD_OK) → 실패 시 failure_reason으로 OOM/Evicted 구분. 롤아웃: releases 행 → rollout_watcher → RolloutRunner.patch_image(strategic merge, migrate initContainer 동반 패치) → observe(get_workload→normalize_*) → assess_deployment/assess_daemonset로 applied/progressing/failed 판정 → 타임아웃 진단은 pod_briefs.

### 모듈

| 파일 | 책임 |
|---|---|
| `src/dms/execution.py` | 실행 어댑터 경계: ExecStatus/JobSpec/ExecutionError + ExecutionAdapter Protocol(submit/poll/read_summary/terminate/read_log) + 결정적 StubExecutionAdapter |
| `src/dms/placement.py` | 순수 함수: 신선한 에이전트 리포트로 도구 선택(scan→dscan, rm→drm, sync→dsync 코로케이션 우선·nsync 폴백)·후보 노드·노드별 rejections 산출, resolve_fanout(큐/priority clamp/node·process 수) |
| `src/dms/execution_manifests.py` | 순수 빌더: Volcano Job(launcher+worker sshd)·preflight Pod 매니페스트, tool_argv(allowlist 층2), 비특권 sync --chown 자동주입, task별 activeDeadlineSeconds, required podAntiAffinity 산개 |
| `src/dms/execution_volcano.py` | VolcanoExecutionAdapter(ref prefix pod/pods/vcjob 라우팅, TimedOut 판정, summary 경로 재구성, vcjob 라벨 기반 로그) + 실 KubernetesClient(lazy in-cluster init, workload patch/get, queue/podgroup 조회) |
| `src/dms/build_manifests.py` | 순수 빌더: buildah 빌드 Pod(privileged, 자원 봉투·emptyDir sizeLimit)와 적합성 프로브 Pod(egress 443 TCP + 레지스트리 + 노드 디스크 statvfs 검사) |
| `src/dms/build_runner.py` | BuildRunner: 멱등 submit/submit_preflight(AlreadyExists=자기 파드), poll, failure_reason(OOMKilled/Evicted 구분), read_log, terminate + StubBuildRunner(클러스터 없는 경로) |
| `src/dms/rollout_runner.py` | RolloutRunner: image_patch_body(strategic merge, initContainer 조건부) patch_image / observe(정규화 dict) / pod_briefs(best-effort 진단) + StubRolloutRunner |
| `src/dms/rollout_status.py` | 순수 함수: snake/camel 정규화(normalize_deployment/daemonset) + 수렴 판정 assess_deployment(세대 게이트→수렴→stale 필터된 PDE→ReplicaFailure 노출)/assess_daemonset(실패 확정은 워처 벽시계 몫) |
| `src/dms/manifest_tags.py` | PyYAML 없는 부분집합 YAML 파서로 동봉 deploy/k8s 매니페스트의 이미지 태그·DMS_JOB_IMAGE를 읽음(드리프트 배지용, 런타임 조회 전면 fail-soft / 계약 테스트 헬퍼는 assert) |
| `src/dms/wiring.py` | settings.execution_backend=="volcano" 여부로 실/스텁 어댑터·러너·큐리더 선택; artifact_base는 호출 시점 해석 클로저로 주입 |
| `src/dms_job_runner/runner.py` | launcher 오케스트레이션: 층3 ALLOWED_TOOLS 가드 → passwd 물질화 → ssh 키 복사 → hostfile 대기(nsync는 src/dst 각각) → getent IP 해석 → ssh barrier → rank.sh 생성 → artifact_dir chown → runuser mpirun → stdout/stderr/summary.json 기록 |
| `src/dms_job_runner/commands.py` | 순수 명령 빌더: mpirun(env+runuser --preserve-environment, ob1/tcp), ssh 키 복사(positional 인자로 인젝션 차단), ssh probe, getent hosts, nsync role-map |
| `src/dms_job_runner/parsers.py` | 도구 출력 파서(전부 fail-soft, 예외 금지): dsync Items/(N bytes) 마지막 매치, nsync Planned actions 합계+volume 단위 환산, drm Removed N items, dscan 리포트 JSON total_entries |

### 불변식 (위반하면 깨진다)

- tool allowlist 3층이 전부 살아 있어야 한다: 층1 stepper._step_one(stepper.py:258, TOOL_TO_POLICY 밖 tool→fail_closed unknown_tool), 층2 execution_manifests.tool_argv(:61, 미지 도구는 argv를 지어내지 않고 ValueError→어댑터가 submit_failed로 접음), 층3 dms_job_runner/runner.py:21,36 ALLOWED_TOOLS(exec·부작용 전 종단). ALLOWED_TOOLS는 dms.config.AGENT_TOOL_NAMES(config.py:7)와 동일 값이어야 하며 dms_job_runner가 독립 패키지라 중복 정의 — tests/test_job_runner_runner.py 계약 테스트가 동일성을 강제한다.
- activeDeadlineSeconds는 반드시 task 템플릿의 PodSpec에 건다(execution_manifests._apply_task_deadlines:207) — Volcano v1.15.0 CRD가 Job.spec의 미지 필드를 조용히 prune해 타임아웃이 영원히 미발화한다. 반대로 ttlSecondsAfterFinished는 Job.spec 허용 필드라 거기 얹는다(_apply_ttl:223).
- artifact_base의 스킴 제거는 접두사 전용 strip_scheme만 — 전체 replace는 경로 중간의 file://까지 지워 러너 기록 위치와 마운트 계산·읽기 라우트가 갈라진다(execution_manifests._artifact_dir:180, execution_volcano._volumes:109-112, _reconstruct_summary_path:233).
- 어댑터의 artifact_base는 생성자 캡처 금지, 호출 시점 해석 callable(execution_volcano.py:82-88; wiring.py:23-29) — base 변경 후 컨트롤러 재시작 시 in-flight 잡 summary를 옛 경로에서 찾는 사고 방지.
- worker의 required podAntiAffinity(같은 job·같은 task, execution_manifests._worker_affinity:244)는 resolve_fanout의 node_count=min(len(candidates),max_nodes)(placement.py:123) 전제 위에서만 안전 — 레플리카가 후보 노드 수를 넘지 않아 산개 불가 영구 Pending이 구조적으로 없다. 셀렉터를 넓히거나 fanout 공식을 바꾸면 이 짝이 깨진다.
- preflight Pod 이름은 phase(underscore→hyphen 치환)+role로 스코프(build_preflight_pod:325-334) — 한 잡이 preflight/exec_preflight 두 번 띄우고 nsync는 src/dst로 갈라지므로 이름 충돌(AlreadyExists)과 DNS-1123 위반(underscore→422)을 막는다. exec_preflight도 _PREFLIGHT_PHASES(execution_volcano.py:129)로 Pod 라우팅되어야 한다 — Volcano Job으로 지으면 이름에 underscore가 들어가 422.
- vcjob의 deadline 판정은 status.state.reason/message에서만(execution_volcano._vcjob_deadline_exceeded:50) — CRD의 conditions[] 항목에는 reason이 없어 파드식으로 읽으면 영원히 TIMED_OUT을 못 잡는다.
- workload patch는 strategic-merge-patch content-type 명시(KubernetesClient.patch_workload:392) — 기본 json-patch+json이면 apiserver 422. initContainers 절은 실제 initContainer가 있는 컴포넌트에만 붙인다(rollout_runner.image_patch_body:32-37) — patchMergeKey 병합은 없는 이름을 새 컨테이너로 추가해 파드 기동을 망가뜨린다.
- summary.json은 항상 정확히 3키 {returncode, files, bytes}, 모름은 null(runner._build_summary:135) — 파싱이 잡을 죽이는 경로는 없다(parsers 전체 fail-soft, 예외 금지 계약).
- 빌드/프로브 파드 이름은 build_id에서 결정적 → create의 AlreadyExists는 이전 틱의 자기 파드이므로 존재 확인 후 성공 취급(build_runner.submit:54-70, submit_preflight:90-101). ref 접두 BUILD_REF_PREFIX(build_runner.py:14)가 유일 출처 — build_watcher/pod_gc/routes_builds가 여기서 import.
- BUILD_SIZELIMIT_GIB(10)+BUILD_DISK_MARGIN_GIB(2)를 빌드 파드 봉투(emptyDir sizeLimit·eph limits)와 프로브 디스크 공식(DMS_PF_NEED_BYTES)이 공유(build_manifests.py:52-55,152-153) — 한쪽만 바꾸면 "프리플라이트 통과했는데 빌드가 노드 위협"으로 갈라진다. 프로브의 0.15는 kubelet evictionHard 미러 상수(:119).
- assess_deployment는 세대 게이트(observed>=generation)를 먼저, 수렴 검사를 PDE 스캔보다 먼저 본다(rollout_status.py:123-133) — 순서를 바꾸면 옛 ReplicaSet 기준 거짓 성공 또는 sticky PDE 거짓 실패(복구 배치 전체 중단). stale PDE는 lastUpdateTime<since(applied_at)로 판별(_is_stale_pde:100), 근거 없으면 stale 아님(PDE는 유일한 실패 확정 수단).
- ROLLOUT_REQUEST_TIMEOUT_SECONDS=10(execution_volcano.py:34)은 리더 리스 TTL(30s)과 맞물린 내부 불변식(틱 최악 2회 호출 20s<30s) — 설정 키로 노출 금지. get_queue/list_podgroups/list_pod_briefs/patch/get_workload 전부 이 타임아웃 필수(urllib3 기본 무제한).
- repo_host(build_manifests.py:58)는 라우트 검증(invalid_repo_url 422)과 프로브 매니페스트가 같은 함수를 써야 한다 — 갈라지면 "제출은 통과, 프로브 생성 실패" 창이 생긴다.
- nsync hostfile 순서는 source 먼저(rank 0..N-1)→destination(runner.run_job:55-60) — commands.nsync_role_map의 rank 배정과 일치해야 role이 안 뒤집힌다.

### 함정 (모르면 밟는다)

- Volcano svc 플러그인은 task 이름의 하이픈을 언더스코어로 바꾼 hostfile을 만든다: source-worker → /etc/volcano/source_worker.host (runner.main.wait_hostfile:186-194, 테스트베드 실측). 하이픈 경로를 읽으면 빈 hostfile → mpirun "no nodes available".
- KubernetesClient.read_pod_log는 _preload_content=False로 원시 응답을 직접 디코드(execution_volcano.py:367-376) — 기본값이면 bytes의 repr(b'DMS_PREFLIGHT_OK\n')이 포탈에 그대로 노출된 실증 사고가 있다.
- 비특권 요청자의 sync에는 --chown uid:gid가 자동 주입된다(execution_manifests._auto_chown:64) — 소스가 남(root) 소유면 runuser 신원으로 목적지 chown이 불가해 데이터는 복사되고 잡만 Failed 되는 함정을 막는다. 사용자가 chown을 명시하면 개입하지 않는다.
- worker sshd는 UsePAM=no(물질화 계정은 /etc/shadow 없음→PAM이 거부)·StrictModes=no로 띄운다(execution_manifests._worker_command_script:118-144); /root home은 스킵(Volcano ssh 플러그인이 읽기전용 마운트). launcher는 mpirun 전 artifact_dir을 요청자 소유로 chown해야 도구가 결과 파일을 쓸 수 있다(runner.py:90-95).
- k8s 예외는 ApiException 타입이 아니라 status 속성 duck-typing으로 판별(execution_volcano.py:419-427) — .venv에 kubernetes 패키지가 없어 테스트가 그 타입을 만들 수 없다. 403은 _log_forbidden으로 반드시 구분 로그(:429) — RBAC 거부가 "객체 없음"과 똑같이 렌더된 사고의 교훈.
- vcjob 로그는 launcher(이름에 -launcher-) 전부 + 그 외 Failed 파드만 모은다(_read_vcjob_logs:269) — 성공 워커 sshd 로그는 노이즈. per-pod 실패는 (pod, None, waiting_reason)으로 접지만 list 호출 자체의 예외는 poll_failed로 던진다(403이 "로그 없음"으로 뭉개지지 않게). 파드 0개는 실패가 아니라 빈 목록.
- KubernetesClient는 in-cluster config를 최초 호출까지 lazy + 이중검사 잠금으로 세 API 핸들을 원자 세팅, _core를 마지막에(_ensure:313-327) — 병렬 observe 시 반쯤 초기화된 핸들 접근 방지.
- 프로브 Pod의 activeDeadlineSeconds는 스케줄 후에만 발화한다(build_manifests.py:162-165) — 영구 Pending 프로브는 워처의 created_at 기반 회수만 잡는다.
- 빌드 프로브·파드에 priorityClassName dms-build(값 10<dms-low 50) — PriorityClass 미적용 클러스터에선 admission 거절이므로 05-volcano-queue-priorityclass.yaml을 먼저 apply(build_manifests.py:167-169,202-205).
- 프로브 실패 시 detail을 마커(DMS_PREFLIGHT_REASON)보다 먼저 출력(build_manifests.py:97-103) — 64KB 로그 꼬리 박제에서 마커가 잘리면 사유가 build_preflight_failed로 뭉개진다.
- 레지스트리가 평문 HTTP라 push --tls-verify=false만으론 부족 — dms-agent가 FROM pkg-01:5000/…를 pull하므로 registries.conf.d에 insecure 등록이 먼저다(build_manifests._SCRIPT:9-13). dms-agent 빌드는 --build-arg로 베이스 태그를 명시 고정 — 없으면 ARG 기본값 :dev로 엉뚱한 베이스에서 조용히 "성공"한다(:28-36).
- nsync는 dsync 파서로 파싱 불가(별개 도구, Items:/bytes 미출력) — Planned actions 합계(블랙리스트 skipped-dst-only 제외)+planned/copied-volume 단위 환산, 모르는 단위면 이전 매치로 물러나지 않고 None(parsers.py:25-89).
- dscan argv는 --output $DMS_SCAN_REPORT 항상 + --print는 quiet가 아닐 때만(--quiet와 상충, execution_manifests.tool_argv:43-48). $DMS_SCAN_REPORT 경로와 summary가 읽는 경로는 _scan_report_path 한 곳(runner.py:110-114) — 갈라지면 scan 카운트가 조용히 null.
- manifest_tags의 동봉본 경로는 개발 체크아웃(parents[2])→/app/deploy/k8s 폴백(:40-43); 롤아웃 직후 live!=manifest는 정상(포탈 롤아웃은 매니페스트를 안 고침 — 다음 kubectl apply가 되돌릴 위험의 표시가 목적). initContainer 이미지 드리프트는 배지에 안 뜬다 — 계약 테스트(init_container_image)가 그 침묵을 메운다(:217-227).
- StubExecutionAdapter.read_log도 실 어댑터와 같은 3-튜플 (pod, log, waiting_reason) 계약(execution.py:84-87); log=None은 "얻을 수 없었다", 빈 문자열은 정상값(launcher는 대개 빈 로그).
- BuildRunner.failure_reason은 구분 재료가 없으면(파드 GC·조회 실패) 지어내지 않고 build_failed 유지(build_runner.py:114-138) — Evicted는 파드 수준 reason, OOMKilled은 containerStatuses.terminated.reason.
- ssh readiness barrier는 워커당 최대 90회(1s) 폴링 후 준비 안 돼도 경고 없이 진행(runner._wait_ssh_ready:124) — 최종 재시도/타임아웃은 mpirun 몫(legacy 동일).

### 결합점

- stepper.py — allowlist 층1(unknown_tool fail-closed) + JobSpec 조립(절대경로 주입) 후 ExecutionAdapter.submit/poll/read_summary/read_log/terminate를 소비하는 유일한 잡 구동자
- planner.py — placement.select_tool_and_candidates/resolve_fanout 호출; PlacementError.rejections shape(scan/rm flat, sync nested)로 신원 전파 유예 vs 진짜 결격을 판별
- wiring.py — execution_backend 설정으로 Volcano/Stub 어댑터·BuildRunner·RolloutRunner·QueueReader 선택; repos.storages.get을 storages_lookup으로, resolve_artifact_base를 호출 시점 클로저로 주입
- build_watcher.py — BuildRunner.submit_preflight/submit/poll/read_log/failure_reason/terminate를 틱마다 호출, 로그 마커(DMS_PREFLIGHT_OK/DMS_BUILD_OK/DMS_COMMIT_SHA) 파싱
- rollout_watcher.py — RolloutRunner.patch_image/observe/pod_briefs + rollout_status.assess_deployment/assess_daemonset 소비; 크래시 복구 시 spec 이미지 재패치
- repositories/builds.py — BUILD_IMAGES/build_pod_name/build_probe_pod_name/build_tag의 출처(build_manifests·build_runner가 import)
- repositories/releases.py — COMPONENTS(kind/workload/container/init_container 좌표)의 단일 진실; manifest_tags와 롤아웃 경로가 공유
- pod_gc.py, api/routes_builds.py — BUILD_REF_PREFIX를 build_runner에서 import(리터럴 중복 금지)
- agent(fresh_reports) — placement의 입력: 노드별 mounts/tools/identities Ready 판정 재료; config.AGENT_TOOL_NAMES가 층3 ALLOWED_TOOLS와 계약 테스트로 묶임
- queue_reader.py — KubernetesClient.get_queue/list_podgroups(scheduling.volcano.sh) 소비(큐 가시성 API)
- artifact_base.py — strip_scheme/resolve_artifact_base: 매니페스트·어댑터·wiring이 공유하는 아티팩트 경로 규약
- 잡 이미지 — dms_job_runner가 단독 설치되어 /usr/local/bin/dms-job-runner가 launcher 컨테이너 command; DMS_JR_* env(execution_manifests._launcher_env/_worker_env)가 유일한 입력 채널

---

## 7. API·포탈 (API & portal)

FastAPI 앱(create_app) 하나가 세션 쿠키·공유 토큰 이중 인증 뒤로 user/admin/agent API를 노출하고 같은 프로세스에서 React SPA(dist)를 서빙하며, 프론트는 reasonCodes.json 단일 파일 양방향 계약으로 사유 코드를 한국어로 표시하고 react-query 폴링으로 상태를 따라간다.

**데이터·제어 흐름**: 브라우저 SPA(fetch, credentials:include) 또는 스크립트(Bearer) → SessionMiddleware/current_identity(auth.py) → /api/user|admin|agent 라우트 → app.state.repos(DB)·execution_adapter·queue_reader·rollout_runner → JSON(오류는 detail=사유 코드) → 프론트 request()(api.ts)가 ApiError(code)로 변환·reasonText로 한국어 렌더 → react-query refetchInterval 폴링이 화면 갱신. 비-API GET은 spa_fallback(app.py:119)이 index.html 반환. 에이전트는 POST /api/agent/report로 상태를 올리고 storages·artifact_base_path 설정을 응답으로 내려받는다.

### 모듈

| 파일 | 책임 |
|---|---|
| `src/dms/api/app.py` | create_app: 라우터 18개 조립, SessionMiddleware(쿠키 dms_session), /healthz, /readyz(DB SELECT 1 + 연속 실패 30회 시 exit_fn=SIGTERM 자기종료, 성공 1회면 카운터 리셋), dist 정적 서빙(/assets mount)+spa_fallback(존재하는 파일이면 FileResponse, 아니면 index.html) |
| `src/dms/api/auth.py` | Identity(actor,role,auth) 네임드튜플, current_identity(): Bearer 공유토큰(x-dms-actor는 node:<DNS-1123>만, 빈값→shared-token, 그 외 400) vs 세션(요청마다 계정 disabled 재검사), require_user/require_admin, audit_actor()(token: 접두 표시용) |
| `src/dms/api/routes_auth.py` | signup(무인증, email 검증 더미)/login(세션 재발급)/logout/me + x-admin-token 헤더로 admin 계정 부트스트랩(감사 actor=token:admin-token) |
| `src/dms/api/routes_requests.py` | 제출(202; maintenance 503, scan은 admin 전용 403, owner_username 특권 게이트, 422 reason 세분화), 목록(admin은 전체), 상세(events 101건 조회로 잘림 판별), 취소(전 잡 종단이면 거짓 취소 대신 finalize_from_job 화해 후 409) |
| `src/dms/api/routes_jobs.py` | _owned_request/_owned_job 소유권 검사(비소유는 404로 뭉갬), confirm(fingerprint 대조·preview 만료 처리), job 단위 cancel |
| `src/dms/api/cancel.py` | terminate_job(): 종단이면 no-op, phase_refs 전부 adapter.terminate — 종료 성공 후에만 DB Cancelled(거짓 취소 금지)의 실행부 |
| `src/dms/api/artifacts.py` | fd 기반 TOCTOU 제거 아티팩트 읽기/목록/스트림: O_NOFOLLOW\|O_NONBLOCK 단일 open→fstat→/proc/self/fd 봉쇄→크기 상한, 목록은 scandir(dfd)+MAX_ENTRIES/MAX_SCAN, tail_lines는 \n 전용 분할 |
| `src/dms/api/routes_artifacts.py` | 잡 아티팩트 목록/뷰(tail)/다운로드(octet-stream+attachment+nosniff), /logs: 라이브 우선 + diag_logs 박제 폴백(빈 문자열은 폴백 조건 아님), 봉쇄 실패·미존재는 동일 404(존재 오라클 차단) |
| `src/dms/api/routes_agent.py` | /api/agent/report: actor==node:<node_name> 일치 검증 후 ingest, 응답에 enabled storages·probe targets·report 주기·artifact_base_path(스킴 제거) 하달 |
| `src/dms/api/routes_storages.py 외 admin 계열(accounts/nodes/policies/denylist/batches/control/artifact_base/builds/releases/metrics)` | 전부 APIRouter(dependencies=[Depends(require_admin)]) 또는 라우트별 require_admin; storages만 user_router(/api/user/storages, require_user) 별도 |
| `frontend/src/lib/api.ts` | REASON_MESSAGES 한국어 매핑+reasonText(prefix:suffix 복합 코드 번역), request(): 오류 파싱 한 벌·비JSON이면 http_<status> 합성·401에만 dms:unauthorized 발화, ApiError(status,code) |
| `frontend/src/lib/reasonCodes.json` | 백엔드가 낼 수 있는 사유 코드의 단일 목록 — 프론트 reasonCodes.test.ts와 백엔드 tests/test_reason_codes_coverage.py가 같은 파일을 읽는 양방향 계약의 축 |
| `frontend/src/app/ (AuthContext.tsx, RequireRole.tsx, router.tsx, queryClient.ts, AppShell.tsx, ErrorBoundary.tsx)` | dms:unauthorized→me invalidate(clear 금지), 역할 게이트 라우팅(/admin/* 15개+user 4개), retry:false·staleTime 5000, ErrorBoundary key={pathname} |
| `frontend/src/features/*/use*.ts` | react-query 폴링 훅: requests 3s, request jobs 2s(전 잡 종단이면 중지), dashboard/metrics 5s, batches 4s/상세 2.5s(종단 중지), nodes·artifact-base 10s, builds/releases는 진행 중일 때만 |
| `frontend/e2e/ + frontend/playwright.config.ts` | 풀스택 e2e 5개(01-boot-session~05-polling): global-setup이 migrate/api/controller/agent 부팅·시드(실패=throw, skip 금지), :8093 선점 거부, workers:1(단일 sqlite), 시스템 크롬, forbidOnly |

### 불변식 (위반하면 깨진다)

- reasonCodes.json 단일 파일 양방향 계약: 백엔드에 새 detail=/reason_code= 리터럴을 추가하면 frontend/src/lib/reasonCodes.json과 api.ts REASON_MESSAGES를 같은 커밋에 갱신해야 한다 — reasonCodes.test.ts(전 코드 매핑+죽은 키 금지)와 tests/test_reason_codes_coverage.py(src/dms AST 추출)가 같은 JSON을 대조한다
- Identity.auth에 기본값 없음(auth.py:27) — 새 생성 지점이 필드를 빠뜨리면 조용한 session 오분류 대신 TypeError로 즉시 터지는 것이 의도; Identity.actor는 절대 변형 금지(에이전트 인증·특권 판정·소유권 검사가 원값 비교), 표시용은 audit_actor()만(auth.py:30)
- _NODE_NAME_RE 정의는 auth.py:15 한 곳 — routes_agent.py:6이 import; 두 곳으로 갈라지면 토큰 게이트가 통과시킨 node:<이름>을 ingest_report가 거절한다
- 아티팩트 봉쇄 사슬은 open_artifact_stream 하나(artifacts.py:178) — 뷰·다운로드 공유, 검사 순서 계약: 단일 open→fstat S_ISREG→fd 봉쇄(_assert_contained)→그 뒤에만 크기 상한(순서가 바뀌면 404/413 갈림이 존재·크기 오라클)
- artifact_not_found/artifact_forbidden은 뷰·다운로드 라우트 모두 body까지 동일한 404(routes_artifacts.py:44-49, 67-70) — 라우트 간 응답이 갈리면 그 차이가 존재 오라클
- 취소는 terminate 성공 후에만 DB를 Cancelled로 기록(cancel.py 모듈 docstring; routes_requests.py:140-174) — 전 잡 종단+요청 비종단 창에서는 취소 기록 대신 finalize_from_job(orphan_recovery)으로 화해 후 409(거짓 취소 금지)
- dms:unauthorized 이벤트는 401 전용(api.ts:216-218) — 403 등에서 발화하면 권한 없는 화면마다 로그인으로 튕긴다; AuthContext는 me를 invalidate만 한다(clear는 pending 리셋→401 무한 루프, AuthContext.tsx:5-9)
- 'token:' 접두는 감사 actor 예약 네임스페이스(auth.py:9, routes_auth.py:60-67) — 사용자명에 ':' 금지가 전제라 어떤 셀프가입도 이 값에 도달 불가
- /readyz 503 본문 {"status":"degraded"} 및 상태 코드는 프로브 계약으로 불변(app.py:84-85); 연속 실패 카운터는 성공 1회에 리셋(app.py:86), limit(기본 30)에서 exit_fn 발화
- spa_fallback은 라우터 include 뒤에 등록(app.py:92-119) — /api·/healthz·/docs가 먼저 매칭된다는 순서가 SPA 서빙의 전제; static_root 밖 normpath 결과는 index.html 폴백

### 함정 (모르면 밟는다)

- submit의 scan 게이트는 원시 문자열 비교(routes_requests.py:80-84) — Operation(...) 변환은 422 변환 try 밖이라 여기서 ValueError가 나면 500이 된다
- artifacts open의 O_NONBLOCK은 필수(artifacts.py:194-199) — 사용자가 자기 phase 디렉터리에 mkfifo를 걸면 open이 영원히 블록, AnyIO 스레드풀(~40) 고갈로 SPA까지 전체 정지하고 팟 재시작 전까지 안 돌아온다
- 하드 링크는 봉쇄로 못 막는다(artifacts.py:72-80 docstring) — 앱 층 해결 불가, 배포에서 별도 파일시스템+fs.protected_hardlinks로 다뤄야 한다
- tail_lines는 '\n'으로만 분할(artifacts.py:165-175) — splitlines()는 \r에서도 쪼개 rsync류 진행률 로그의 tail=N이 N줄이 아니게 된다
- registry_unreachable·tag_unverified는 프론트 전용 코드(api.ts:155-162) — 백엔드는 detail로 내지 않고 targets 응답 registry_ok=false / 202의 tag_verified:false로 알린다; 죽은 키 테스트의 허용 예외는 http_401/422/500/503뿐(reasonCodes.test.ts:25)
- reasonText는 복합 코드(prefix:suffix)를 접두 번역+접미 병기로 처리(api.ts:182-192) — stepper가 f"{prefix}:{reason_code}"를 합성하므로 정확 일치 조회만으로는 영원히 미번역
- request()의 오류 파싱은 한 벌이어야 한다(api.ts:207-215) — 과거 401 전용 분기 복제가 폴백·문구 드리프트의 원인; 인그레스가 만든 비JSON 401은 http_401 합성
- Home은 401이 아닌 me 오류를 /login으로 흘리지 않는다(router.tsx:34-47) — 일시 500을 세션 만료로 오독시키지 않기 위해 재시도 버튼 렌더; 401만 리다이렉트(그래야 AuthContext 루프가 끊긴다)
- get_job_logs 박제 폴백 조건은 '빈 목록 또는 전 항목 log=None'(routes_artifacts.py:154) — 빈 문자열은 정상값이라 truthy 검사 금지; 깨진 diag_logs는 폴백 포기+diag_logs_corrupt 이벤트(라이브 열람까지 죽이지 않기 위해)
- readyz 실패 카운터는 리스트 트릭 + 락 없음(app.py:61-64) — 단일 커넥션 RLock 직렬화 전제; 이 전제가 바뀌면 카운터가 레이스한다
- poll_failed 문구는 문맥 중립이어야 한다(api.ts:129-131) — 빌드 폴링과 잡 로그 409가 같은 코드를 공유하므로 '빌드' 단어 금지
- e2e는 CI 없음 — npm run test:e2e 수기 실행이 유일한 게이트(playwright.config.ts), :8093 선점 시 낡은 서버 오염을 막으려 부팅 자체를 거부
- ErrorBoundary에 key={pathname} 필수(router.tsx:59-62) — 없으면 네비게이션 후에도 잡힌 에러 폴백이 잔존
- useLogin 성공 시 qc.clear()(useAuth.ts:13)가 교차 사용자 캐시 누수를 막는 유일한 지점 — AuthContext는 일부러 clear하지 않는다
- reasonCodes.json에는 브리프 목록에 없지만 실제 발생하는 코드가 있다(api.ts:73-77 주석) — no_covering_scan·cannot_lock_self 등은 삭제하면 기존 프론트 테스트가 깨진다

### 결합점

- src/dms/repositories.py·db.py — app.state.repos/Database를 모든 라우트가 소비; wire_reconnect_event(db, repos)로 재연결 흔적 이벤트 기록(app.py:53)
- src/dms/wiring.py — build_execution_adapter(취소 terminate·로그 read_log), build_queue_reader(/api/admin/metrics/queue), build_rollout_runner(releases targets 관찰 전용, patch 안 함), build_build_runner
- src/dms/artifact_base.py — resolve_artifact_base/strip_scheme을 아티팩트 라우트(_base)와 agent report 응답이 공유(DB 우선 해석)
- src/dms/domain.py — Operation/DataJobState/TERMINAL_*/build_data_payload/resolve_priority가 제출 검증·상태 판정의 원천
- 에이전트 데몬셋 → POST /api/agent/report — envFrom 없이 artifact_base_path를 이 응답으로만 수신(routes_agent.py:30-34)
- controller/stepper가 기록한 reason_code(합성 prefix: 포함)를 프론트 reasonText가 최종 소비 — 사유 코드 계약의 생산자
- tests/test_reason_codes_coverage.py가 frontend/src/lib/reasonCodes.json을 읽는다 — 백엔드 테스트가 프론트 파일에 의존하는 교차 결합
- k8s readiness probe → /readyz — 자기종료(SIGTERM)는 restartPolicy 재시작에 의존; planner의 특권 승격은 requests.create에 실린 auth_method를 읽는다(routes_requests.py:105-107)

---

## 8. 에이전트·신원 (Agent & identity)

노드 DaemonSet 에이전트가 마운트·도구·로컬 신원·OS 지표·아티팩트 base를 프로브해 /api/agent/report 로 밀어올리고, 서버 측에서는 LDAP resolver + 데니리스트 + 특권 게이트로 잡 실행 신원(uid/gid/groups/privileged)을 fail-closed 로 확정한다.

**데이터·제어 흐름**: [에이전트 방향] run_loop(빈 상태로 시작) → AgentRunner.run_once: mountinfo 읽기 → build_report(mounts/tools/identities/os/artifact_base) → POST /api/agent/report → routes_agent.ingest_report: actor 검증 → agents.ingest → 응답 {storages, identity_probe_targets, report_interval_seconds, artifact_base_path} → 에이전트 상태 갱신 → sleep(max(1, interval)) 반복. once 모드는 부트스트랩+본 사이클 2회. [신원 방향] planner.py:154 → resolve_job_identity(control, resolver, ..., session_authenticated=(auth_method=="session")): owner=(owner_username or requester_id) → 특권 판정(allow_privileged AND session AND requester∈privileged_requesters) → group deny 규칙 있으면 특권 경로도 그룹만 선해석 → control.is_denied(데니리스트) → 특권이면 uid0/gid0 반환, 아니면 LDAP resolve → 그룹 포함 재차 is_denied → control.register_probe_target(owner) → ResolvedIdentity. 등록된 probe target 은 다음 에이전트 응답에 실려 노드별 probe_identities 로 검증되고, planner 의 신원 전파 grace(DMS_PLANNER_IDENTITY_GRACE_SECONDS=300)가 그 전파 지연을 흡수한다.

### 모듈

| 파일 | 책임 |
|---|---|
| `src/dms/agent/runner.py` | 에이전트 루프. build_report()로 5종 프로브를 합쳐 POST /api/agent/report(Bearer 공유 토큰 + x-dms-actor: node:<이름>) 후, 응답으로 storages/probe_targets/interval/artifact_base_path 상태를 갱신. DB 무지의 순수 HTTP 클라이언트. |
| `src/dms/agent/probes.py` | 순수 프로브 로직(시스템 접근은 전부 파라미터 주입): parse_mountinfo/probe_mounts(Ready·Missing 판정), probe_tools(dscan/dsync/nsync/drm 발견+--version), probe_identities(pwd/grp 로컬 조회), probe_os_metrics(loadavg·meminfo·statvfs·net_dev), probe_artifact_base(exists/writable). |
| `src/dms/identity.py` | 실행 신원 오케스트레이션. ResolvedIdentity 모델, IdentityUnavailable/IdentityRejected 예외, resolve_job_identity()가 데니리스트→특권 게이트→LDAP 해석 순서로 신원을 확정. |
| `src/dms/identity_ldap.py` | ldap3 기반 resolver. RFC 4515 필터 이스케이프 후 uid/gidNumber·memberUid 그룹 검색. build_ldap_resolver()는 uri/user_base/group_base 중 하나라도 placeholder 면 None 반환(=LDAP 미구성). |
| `src/dms/config.py` | AgentSettings(from_env: API URL·토큰 fail-fast, mountinfo/net_dev/virtual_net 경로) 및 서버측 신원 설정(allow_privileged_requesters 기본 True, privileged_requesters 기본 {root,admin}, ldap_require_auth_bind 기동 시 fail-closed 검증, config.py:180-190). |
| `src/dms/api/routes_agent.py` | 리포트 인제스트 엔드포인트. node_name 재검증 + actor 일치 강제 후 repos.agents.ingest(), 응답에 enabled 스토리지 목록·probe_targets(TTL)·보고 주기·artifact_base_path 를 내림. |
| `src/dms/repositories/control.py` | identity_denylist(is_denied — requester/owner/group 소문자 비교, has_group_denies)와 identity_probe_targets(register_probe_target: DELETE+INSERT 로 last_requested_at 갱신, probe_targets: TTL cutoff 정리 후 목록). |
| `src/dms/wiring.py` | build_identity_resolver() — settings 로 build_ldap_resolver 를 조립하는 유일한 진입점. |

### 불변식 (위반하면 깨진다)

- 특권 승격 3중 게이트: allow_privileged AND session_authenticated AND requester∈privileged_requesters 전부 참이어야 uid 0 (identity.py:60-61, resolve_job_identity). session_authenticated 기본값은 False(fail-closed) — 인자를 빠뜨린 새 호출자는 특권이 안 붙는 쪽으로 실패한다(identity.py:53-59 주석).
- 데니리스트는 최우선 kill-switch: 특권 경로보다 먼저 평가되고(identity.py:71-73), 특권 경로도 group 규칙이 등재돼 있으면(control.has_group_denies) LDAP 그룹을 선해석해 검사한다(identity.py:62-70).
- resolver=None(LDAP 미구성)이면 비특권 경로는 무조건 IdentityRejected(ldap_not_configured) — 로컬 폴백 없음(identity.py:76-77). LDAP 예외는 IdentityUnavailable→ldap_unavailable 로 fail-closed(identity.py:78-81).
- DMS_LDAP_REQUIRE_AUTH_BIND=true 면 BIND_DN/PW 결측·placeholder 시 기동 자체를 거부(SettingsError) — 익명 바인드로의 침묵 강등을 배포 시점에 발화시킨다(config.py:180-190). 검증은 identity_ldap 이 아니라 config 에 있다(발화 시점이 기동이어야 운영자가 알아챔).
- 에이전트 리포트의 actor 게이트: 토큰 경로 x-dms-actor 는 node:<이름> 형식만 허용되고(auth.py:49-60, _NODE_NAME_RE), routes_agent.ingest_report 가 body.node_name 과 identity.actor 의 일치를 재강제(agent_node_identity_mismatch 403). 노드 이름 정규식은 auth 가 유일 출처 — 두 규칙이 갈라지면 위조가 가능해진다(routes_agent.py 머리 주석).
- artifact_base 는 리포트 최상위 별도 필드지 mounts 배열이 아니다 — reconciler 가 mounts 를 storages.status 로 매핑하므로 섞으면 스토리지 판정이 오염된다(probes.py:92-103 docstring, runner.py:41-44).
- LDAP 필터 입력은 반드시 _escape_filter(RFC 4515)를 거친다 — username 이 uid= 와 memberUid= 필터에 직결되므로 인젝션 방지의 유일한 방어선(identity_ldap.py:8-10, 22-30).
- probe_mounts 의 Ready 판정은 exists AND is_mountpoint AND readable(R_OK+X_OK)이고 writable 은 판정에 반영되지 않는다 — 소비자는 status 요약이 아니라 필드를 직접 본다(probes.py:26-48).

### 함정 (모르면 밟는다)

- build_report 의 read_text 폴백(runner.py:29-32)을 지우면 probe_os_metrics 내부 호출이 전부 try/except Exception 이라 OS 지표 전체가 조용히 null 이 된다 — 테스트는 os_fn 을 주입하므로 초록을 유지해 CI 로는 못 잡는다.
- run_once 응답 처리에서 artifact_base_path 는 body.get(..., 기존값) — 구버전 서버 응답에 키가 없으면 기존 값을 유지한다. 이 폴백을 지우면 다음 리포트부터 프로브가 사라져 화면이 영구 '확인 대기 중'이 된다(runner.py:77-82). 에이전트는 ConfigMap envFrom 을 안 받아 base 를 아는 유일한 경로가 이 응답 필드다.
- DMS_AGENT_VIRTUAL_NET_PATH 기본은 반드시 미설정 — 파드 안의 /sys/devices/virtual/net 은 파드 netns 의 가상 인터페이스라, 마운트 없이 기본 경로를 쓰면 이름이 겹치는 호스트 물리 NIC 가 가상으로 오판돼 지표에서 빠진다(config.py:234-240). 설정됐는데 못 읽으면 필터를 끄고 lo 제외 전량 합을 유지한다(probes.py:149-154, 지표를 잃는 쪽이 더 나쁜 실패).
- /proc/net/* 는 netns 범위라 파드에서 기본 경로를 읽으면 veth 값이 나온다 — 네트워크만 DaemonSet 이 마운트한 /host/proc/1/net/dev 를 주입받고, loadavg/meminfo 는 네임스페이스되지 않아 기본 경로가 이미 호스트 값이다(probes.py:156-159).
- probe_artifact_base 의 writable 은 에이전트 프로세스 uid 기준 W_OK 지 잡 파드 요청자 uid 가 아니다 — 정직한 한계로 화면이 문구로 표기한다(probes.py:100-103). exists 가 핵심 신호: 잡 파드 hostPath 가 type: Directory 강제라 디렉터리 없는 노드에선 파드 기동 자체가 실패한다.
- build_ldap_resolver 의 bind_dn/bind_pw 는 빈 문자열이면 None 으로 강등돼 익명 바인드가 된다(identity_ldap.py:51-54) — 이 침묵 강등을 막는 유일한 장치가 DMS_LDAP_REQUIRE_AUTH_BIND(기본 false)다.
- planner 는 req.auth_method 컬럼으로 session 여부를 판정하는데 기배포 DB 의 구형 행은 NULL 이라 자동으로 비특권이다(planner.py:160-162 주석) — 특권이 안 붙는다고 버그가 아니다.
- probe_tools 는 --version 실패를 fail-soft 처리한다: 도구 status 는 Ready 유지, reason=version_probe_failed:<타입>(probes.py:65-66). Missing 은 shutil.which 실패(tool_not_found)일 때만이다.
- register_probe_target 은 UPSERT 가 아니라 DELETE+INSERT(control.py:168-174)이고, probe_targets 조회가 TTL cutoff 이전 행을 먼저 DELETE 한다(control.py:183) — TTL(기본 3600s) 내 재요청이 없으면 타깃이 사라져 에이전트가 그 사용자를 더는 프로브하지 않는다.

### 결합점

- planner.py:154 가 resolve_job_identity 의 유일한 프로덕션 호출자 — 결과 identity(username/privileged)를 select_tool_and_candidates 배치와 잡 실행에 넘기고, IdentityRejected.reason_code 로 요청을 reject 한다. 신원 전파 지연은 _identity_grace_active(DMS_PLANNER_IDENTITY_GRACE_SECONDS)로 defer.
- wiring.py:build_identity_resolver → identity_ldap.build_ldap_resolver 로 resolver 조립(placeholder 설정이면 None).
- api/routes_agent.py 가 에이전트 리포트를 repos.agents.ingest 로 저장하고, repos.storages.list(enabled)·repos.control.probe_targets(TTL)·artifact_base(resolve_artifact_base+strip_scheme)를 응답으로 되돌린다 — 에이전트 설정 배포 채널.
- api/auth.py 의 _NODE_NAME_RE 와 토큰 경로 actor 게이트(node:<이름> 전용)가 routes_agent 의 노드 위장 방어와 한 몸 — 규칙의 유일 출처는 auth.
- repositories/control.py 가 identity_denylist(is_denied/has_group_denies)와 identity_probe_targets(register_probe_target/probe_targets) 저장소 — identity.py 와 routes_agent.py 양쪽이 소비.
- reconciler 가 리포트의 mounts 를 storages.status 로 매핑(runner.py:41-42 주석의 전제) — artifact_base 분리 불변식의 이유.
- config.AGENT_TOOL_NAMES(dscan/dsync/nsync/drm)가 probe_tools 대상이며, planner 의 도구 배치(select_tool_and_candidates)가 이 발견 결과(fresh_reports)를 소비.
- agents.fresh_reports(stale_seconds=DMS_AGENT_REPORT_STALE_SECONDS)가 planner 후보 산정의 입력 — 보고 주기(60s)와 stale 창(300s)의 비율이 배치 가용성을 결정.

---

## 9. 설정·배선·진입 (Config, wiring, entry)

env를 기동 시점에 전수 검증해 frozen Settings/AgentSettings로 만들고, cli 서브커맨드(migrate/api/controller/agent)가 wiring의 백엔드 선택 팩토리로 어댑터·러너를 조립해 각 프로세스를 띄우며, 아티팩트 base 해석·레지스트리 태그 조회·시계열 조립의 공용 순수 유틸을 제공한다.

**데이터·제어 흐름**: os.environ → cli.main(argv 파싱) → [agent] AgentSettings.from_env → agent.runner.run_loop / [그 외] Settings.from_env → Database.connect → [migrate] migrations.migrate / [api] create_app(settings, db)→uvicorn / [controller] Repositories(db) → wiring.wire_reconnect_event(db, repos) → build_identity_resolver·build_execution_adapter·build_build_runner·build_rollout_runner(settings 기반 stub/volcano 분기) → controller.build_loops → run_all_once(--once, 결과 stdout) 또는 run_forever. 아티팩트 경로 해석은 소비자 → resolve_artifact_base(control_state DB값 우선, NULL이면 settings.artifact_base_uri) → strip_scheme → 파일시스템.

### 모듈

| 파일 | 책임 |
|---|---|
| `src/dms/config.py` | Settings/AgentSettings frozen dataclass + from_env 전수 검증(_SERVER_INT_KEYS 25개 int 키 테이블, _is_placeholder 부분일치, SettingsError.problems 누적), LDAP require-auth-bind fail-closed(config.py:181-190) |
| `src/dms/wiring.py` | execution_backend(stub/volcano)에 따른 어댑터 선택: build_execution_adapter·build_build_runner·build_rollout_runner·build_queue_reader 4개 팩토리 + build_identity_resolver(LDAP) + wire_reconnect_event(DB 재연결 이벤트 훅, api·controller 공용) |
| `src/dms/cli.py` | argparse 진입점: agent(서버 Settings 없이 AgentSettings만) / migrate / api(uvicorn+create_app) / controller(Repositories+4팩토리 조립, --once는 run_all_once, 아니면 run_forever). SettingsError는 stderr 출력 후 exit 2 |
| `src/dms/artifact_base.py` | 아티팩트 base 단일 진실 원천: strip_scheme(접두사만), resolve_artifact_base(DB 우선→env), normalize_artifact_base(정규형 file:///abs, 422용 DomainValidationError), roundtrip_artifact_base(실제 쓰기 왕복 프로브), controller_check_once(주기 검증 결과를 control_state에 기록) |
| `src/dms/registry.py` | 레지스트리 v2 태그 조회 fetch_repo_tags: 모든 실패를 None으로 접는 fail-soft(registry.py:39-48), 캐시 없음, 정렬 반환, 타임아웃 3s/connect 2s |
| `src/dms/metrics_series.py` | DB/HTTP 없는 순수 시계열 조립: build_node_points(샘플 단위 fail-soft, 네트워크 카운터 차분), clamp_window_hours, bucket_chars_for(SUBSTR 접두 절단), duration_histogram + DURATION_BUCKETS/SUBMIT_WAIT_BUCKETS |

### 불변식 (위반하면 깨진다)

- 필수 4키(DMS_DATABASE_URL/SHARED_TOKEN/ADMIN_TOKEN/SESSION_SECRET)는 결측뿐 아니라 CHANGE_ME/REPLACE_WITH_ 부분일치도 기동 거부 — _is_placeholder(config.py:72-79)가 부분일치인 이유: SHARED_TOKEN은 Bearer로 admin을 주므로 예시값 기동 = 무인증 admin
- from_env는 문제를 전부 모아 SettingsError(problems)로 한 번에 던진다(config.py:161-192) — cli.py:20-25,30-35가 각 problem을 stderr에 찍고 exit 2
- DMS_LDAP_REQUIRE_AUTH_BIND=true면 bind DN/PW 결측·자리표시자 시 기동 거부(config.py:181-190) — 익명 바인드 침묵 강등 차단, 발화 시점이 배포(기동)이어야 운영자가 알아챈다
- 아티팩트 base는 모든 소비자가 resolve_artifact_base 하나만 통과(artifact_base.py:22-30) — DB(control_state.artifact_base_uri) 우선, NULL이면 env. wiring.py:27-29는 이를 생성자 캡처가 아닌 lambda로 넘겨 호출 시점 DB값 사용을 보장
- file:// 제거는 strip_scheme(접두사만, artifact_base.py:15-19)으로 저장소 전체 통일 — str.replace 전체 치환은 경로 중간 file://까지 지워 다른 경로를 만든다. normalize_artifact_base는 그런 값의 저장 자체를 422로 거부(artifact_base.py:40-44)
- registry.fetch_repo_tags에서 None(응답 불가)과 [](태그 0개)는 다른 값(registry.py 모듈 docstring) — 구분이 무너지면 unknown_tag 검증이 조용히 fail-open 되거나 존재하는 태그를 잘못 차단. {"tags": null}도 None으로 접는다(registry.py:44-48)
- registry는 캐시 금지(registry.py:8-12) — 제출 경로 unknown_tag 검증이 낡은 목록으로 실존 태그를 차단하는 것이 설계 §7이 금지한 방향
- wire_reconnect_event는 api(create_app)와 controller(cli.py:63)가 같은 함수를 공유(wiring.py:72-82) — 두 곳이 각자 훅을 만들면 이벤트 모양이 갈라져 SQL 집계가 깨진다. record_event는 절대 예외를 올리지 않는 계약
- DMS_READYZ_EXIT_FAILURES=0은 명시적 비활성(config.py:42-47 주석) — 관찰 전용 탈출구
- Settings는 frozen dataclass — 런타임 재읽기 경로가 없어 재시작 없이 반영돼야 하는 값(artifact base)은 DB 조회로 우회한다(artifact_base.py:23-26)
- metrics_series는 DB/HTTP 접근 없는 순수 함수 모듈(metrics_series.py:1) — 샘플 하나가 깨져도 그 샘플만 버리는 fail-soft가 모듈 전역 계약

### 함정 (모르면 밟는다)

- _parse_csv_set: 미설정(absent)은 default, 명시적 빈 문자열("")은 빈 집합으로 default를 덮어쓴다(config.py:100-106) — 특권 요청자를 끄려면 빈 값을 명시해야 한다
- AgentSettings.virtual_net_path 기본은 반드시 미설정(config.py:234-240) — 파드 안 /sys/devices/virtual/net은 파드 netns의 것이라, 기본 경로를 쓰면 호스트 인터페이스를 다른 네임스페이스 집합으로 거르게 돼 이름 겹치는 호스트 인터페이스가 가상으로 오판된다
- cli.py:56-63 controller는 wiring을 모듈 속성(wiring.wire_reconnect_event)으로 호출 — from-import로 묶으면 monkeypatch 스파이가 안 통한다(주석 명시)
- wiring의 volcano 계열 import는 함수 내부 지연 import(wiring.py:14,36-37,54-55,66-67) — stub 백엔드에서 k8s 의존성 없이 돌게 하는 구조라, 최상위로 올리면 로컬·CI가 깨진다
- 재시도 설정은 의도적으로 없다(config.py:61-63) — 실패한 rm/sync 자동 재실행은 파괴적, 재실행은 배치 :rerun-failed와 사용자 재제출로만
- roundtrip_artifact_base는 존재·isdir 확인이 아니라 실제 쓰기→읽기→삭제 왕복(artifact_base.py:56-81), probe 파일명은 uuid — 동시 검증(포탈 폴링+컨트롤러 루프)이 서로의 probe를 지우지 않게
- normalize_artifact_base는 후행 슬래시 제거 + 루트("/") 거부(artifact_base.py:45-50) — 루트 허용 시 후행 슬래시 제거와 조합돼 "//" 경로가 나온다
- duration_histogram에서 `if not v`가 아니라 `v is None or v < 0`(metrics_series.py:129-133) — 0초는 결측이 아니라 실제 값(같은 초 픽업)
- _num은 bool을 걸러낸다(metrics_series.py:27-31) — bool이 int 서브클래스라 True가 1.0으로 샌다
- build_node_points: 카운터 None 샘플을 지나면 prev도 None(metrics_series.py:105-107) — 다음 구간도 의도적으로 null(마지막 유효 카운터를 기억하지 않는 단순화)
- DMS_POD_GC_AFTER_SECONDS 기본 86400인 이유(config.py:24-29): preflight Pod은 아티팩트를 안 쓰므로 진단이 파드 로그에만 있다 — 1h GC는 유일 사본을 지운다
- agent 서브커맨드는 서버 Settings 검증·DB 연결을 전혀 타지 않는다(cli.py:19-28) — DMS_DATABASE_URL 없이 뜬다

### 결합점

- cli → controller.build_loops/run_all_once/run_forever (controller.py): 조립된 identity_resolver·execution_adapter·build_runner·rollout_runner를 주입
- cli api → api/app.py create_app(settings, db): uvicorn으로 서빙, create_app 내부에서도 wire_reconnect_event 사용
- cli migrate → migrations.migrate(db)
- cli agent → agent/runner.run_loop(AgentSettings)
- wiring → execution.StubExecutionAdapter / execution_volcano.{KubernetesClient,VolcanoExecutionAdapter} / build_runner.{Stub,}BuildRunner / rollout_runner.{Stub,}RolloutRunner / queue_reader.{Stub,Volcano}QueueReader / identity_ldap.build_ldap_resolver
- wiring.build_execution_adapter → repos.storages.get(스토리지 lookup) + artifact_base.resolve_artifact_base(호출 시점 DB 조회 lambda)
- artifact_base → repos.control(control_state 읽기, set_artifact_base_check 쓰기), domain.DomainValidationError(라우트가 422로 운반), api/artifacts.py가 strip_scheme 재수출
- registry.fetch_repo_tags 소비처: 롤아웃 targets 화면(빈 목록+경고 강등)과 제출 경로 unknown_tag 검증(None이면 검증 스킵)
- metrics_series ← db.iso_epoch(별칭 _epoch), MetricsRepository.node_series 출력을 소비, /api/admin/metrics 계열 라우트가 사용
- wire_reconnect_event → repos.observability.record_event + db.on_reconnect 훅(db.py의 dialect/reconnect_count)

---
