# DMS 아키텍처 (현재 상태 지도)

**지금 시스템이 어떻게 도는가.** 유지보수의 진입점이다. 메커니즘의 세부는 코드가
「왜」 주석으로 말하니, 이 문서는 **지도**(무엇이 어디 사는가)와 **불변식**(위반하면
깨지는 규약, 전부 `파일:줄`로 앵커)을 담는다. 왜 그렇게 됐나는
[`docs/history/`](history/), 빌드 역사는 [`CHANGELOG.md`](CHANGELOG.md).

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
   │    (+build-watcher·rollout-watcher·request-purge:             │
   │     runner 있을 때만)                                         │
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
| **제어면 루프** | 리스 아래 주기적 run_once — 요청→잡 상태기계를 굴리고, 삭제한 요청의 잔재를 정리한다 | `controller · planner · stepper · reconciler · batch_orchestrator · pod_gc · retention · build_watcher · rollout_watcher · request_purger(+purge_runner · artifact_trash)` |
| **데이터·영속** | 단일 커넥션 DB + 이중 경로 마이그레이션 + 17개 리포지토리 | `db · migrations · domain · repositories/*` |
| **실행·배포** | Volcano 잡 제출·폴링·로그 + 포탈 빌드 + 제어면 롤아웃 | `execution* · placement · build_* · rollout_* · dms_job_runner/*` |
| **API·포탈** | FastAPI + React dist, 이중 인증, 사유 코드 계약 | `api/* · frontend/src/*` |
| **에이전트·신원** | 노드 프로브·보고 + LDAP 신원 fail-closed 확정 | `agent/* · identity · identity_ldap` |
| **설정·배선·진입** | env 전수 검증 → frozen Settings → 어댑터 조립 | `config · wiring · cli · artifact_base · registry · metrics_series` |

## 2. 요청 → 잡 생명주기 (데이터가 흐르는 길)

```
API POST /api/user/requests
   → requests.Pending
   → [planner 10s] conflict→계정→storage→identity→placement 게이트 통과
        → data_jobs.create_plan_and_job (plan+job INSERT + 조건부 Pending→Planned, 한 트랜잭션)
                                                            (게이트 실패: Rejected/Conflict — 원자적)
   → [job-stepper 5s] claim_steppable → 상태기계(_dispatch):
        scan:  Pending→Preflight→Running→(Succeeded|Failed|TimedOut)
        sync/rm: Pending→Preflight→Preview→ConfirmPending
                 →[사용자 confirm]→Executing(exec_preflight 재검증)→Running→종단
        각 phase: execution_adapter.submit/poll/read_summary/terminate
        종단: _finalize = set_job_state + requests.finalize_from_job (박제 후 전이)
   → [관리자 선택 삭제 — 종단 요청·잡 전부 종단·배치 자식 아님·조용한 창 60s 경과]
        POST /api/admin/requests:delete → request_purges.delete_terminal 한 트랜잭션:
        요청·결과·plan·잡·전이·이벤트·digest DELETE + 아웃박스 request_purges + audit_log 1행
     [배치 단위 — 종단 배치(또는 배치 행 없는 묶음)·자식 전부 종단·잡 전부 종단·조용한 창·자식 수 CAS·자식 ≤ 1000·항목 ≤ 10000]
        같은 POST 의 batches:[{batch_id, expected_request_count}] (≤ 10) → request_purges.delete_batch 배치마다 한 트랜잭션:
        자식(requests.batch_id) 전부 _purge_locked(단건과 같은 몸통) + batch_items + batches DELETE
        + 아웃박스 자식 N행 + audit_log 자식 N + ('batch','delete') 1행 — 전부 아니면 전무
   → [request-purge 15s] k8s(ref·라벨 스윕 → 객체 0) → files(<base>/<job_id> → .dms-trash)
        → purging(purge 파드가 trash 비움) → finish(최종 scrub + 아웃박스 행 삭제)
```

병렬로: **reconciler** 가 에이전트 리포트 → `storages.status`(planner 의 storage
게이트 입력), **batch-orchestrator** 가 배치 item → `requests.create`(planner 입력
생산), **pod-gc/retention** 이 종단 잔재 정리, **build-watcher/rollout-watcher** 가
포탈 빌드·롤아웃을 굴린다. **request-purge** 는 API 가 이미 지운 요청의 DB 밖 잔재(남은 Pod·vcjob,
결과 파일)를 아웃박스 `request_purges` 의 열쇠로 정리한다 — 화면 일관성은 API 의 삭제 커밋 한 번으로
성립하고, 이 루프는 비동기로 수렴만 한다.

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

11. **제어면(api·controller)은 root 다 — 파일시스템 권한은 2차 방어가 아니다**
    (2026-09-09). 운영 아티팩트 base 가 root:root 라 uid 0 으로 돌린다(capabilities 전부
    drop, 이미지 fs 읽기 전용, `deploy/k8s/40-api.yaml`·`41-controller.yaml` 컨테이너
    수준). "커널이 거부할 것"은 근거가 아니다 — 인가는 DB(`_owned_job`/`require_admin`)와
    코드 봉쇄(`artifact_files.py`)에만 있다. 규칙 전체는 §7 「root 제어면」.

---
## 4. 제어면 루프 (Control-plane loops)

controller.run_forever가 monotonic 스케줄로 루프별 리스(loop:<name>)를 틱마다 획득한 뒤 planner/stepper/reconciler/retention/batch-orchestrator/pod-gc/artifact-base-check(+조건부 build-watcher/rollout-watcher/request-purge)의 run_once를 반복 실행하며, 루프 본체 예외는 루프 단위로 격리하고 리스 획득의 지속 DB 장애만 프로세스를 죽인다.

**데이터·제어 흐름**: run_forever(controller.py:138): 1초 틱마다 next_due 지난 루프를 run_all_once([loop])로 실행 → try_acquire_lease("loop:<name>", holder, max(interval*3,30)s) 실패면 skipped_lease, 성공이면 fn() 실행(예외는 "error:<Type>"으로 접힘). 데이터 경로: 요청 생성(API) → requests.Pending → planner가 게이트 통과 시 data_job 생성+Planned → stepper가 Pending→Preflight→(scan: Running→종단 / sync·rm: Preview→ConfirmPending→[confirm]→Executing(exec_preflight 재검증)→Executing/Running→종단), 각 phase는 execution_adapter.submit/poll/read_summary/terminate 뒤 → _finalize가 set_job_state+requests.finalize_from_job. 병렬로: reconciler가 agents.fresh_reports→storages.status(planner의 storage admission 입력), batch-orchestrator가 batch item→requests.create(planner 입력 생산), pod-gc·retention이 종단 잔재 정리, build-watcher가 builds.pending/running→파드 제출·poll→builds.finish, rollout-watcher가 releases.active head→patch_image→observe→finish/abort. request-purge(purge_runner 있을 때만)가 request_purges.due→purge_runner.sweep(k8s 객체 0 확인)→artifact_trash.detach→purge 파드→FS 재확인→request_purges.finish.

### 모듈

| 파일 | 책임 |
|---|---|
| `src/dms/controller.py` | 숙주: build_loops(루프 조립, 68-107행)·run_all_once(리스+예외 격리, 111행)·run_forever(monotonic next_due, sleep(1) 틱, 138행). _stepper_step(35행)이 스텝+preview 만료+고아 스윕을 한 루프에 묶는다. |
| `src/dms/planner.py` | planner(기본 10s): Pending 요청 50건을 conflict→계정→storage→identity→placement→fanout 게이트로 걸러 create_plan_and_job(plan·job INSERT + 조건부 Pending→Planned 한 트랜잭션 — 그 사이 취소·삭제된 요청엔 아무것도 남기지 않고 "gone") 으로 emit. 신원 전파 유예(_identity_grace_active, 기본 300s)면 상태 무변경 defer. |
| `src/dms/stepper.py` | job-stepper(기본 5s): drain 게이트 후 claim_steppable 잡을 상태기계(_dispatch, 273행)로 전진. 미지 tool·스토리지 결측은 _fail_closed로 종단, 실패 종단 전 diag 박제(_archive_diag), 취소 경합은 _reclaim_if_terminal이, 삭제 경합(행 부재)은 _record_ref/_reclaim_deleted 가 회수. |
| `src/dms/reconciler.py` | storage-reconciler(기본 30s): 신선 에이전트 리포트만으로 storages.status를 Unknown/Ready/Degraded로 재계산, 값이 바뀔 때만 set_status. |
| `src/dms/batch_orchestrator.py` | batch-orchestrator(기본 5s): 활성 배치의 Queued item을 max_concurrency 쓰로틀로 request로 materialize, 자식 종단 집계(counts bump), Previewing→PreviewReady / Running에서 ConfirmPending 자식 쓰로틀 confirm(운영자의 배치 확인 1회가 자식 전부를 대표 -- 자식별 컨펌 없음). preview 만료는 stepper `expire_previews` 가 Rejected(preview_expired)로 끝내고 항목은 실패로 집계된다(자동 재미리보기 없음). Running 은 확인 도장이 지금 미리보기와 같은 자식만 컨펌한다. 상태 전이(Completed·PreviewReady)는 스냅샷 뒤 변경을 덮지 않는 CAS. |
| `src/dms/pod_gc.py` | pod-gc(기본 600s, 창 86400s): 종단 잡의 pod/-접두 phase_refs와 종단 빌드의 빌드·프로브 파드만 terminate. ref별 예외 격리. |
| `src/dms/retention.py` | retention(기본 3600s): agent_reports·events를 보존일(기본 30d) 밖에서 배치 5000건 삭제. correctness 아닌 최적화. |
| `src/dms/build_watcher.py` | build-watcher(기본 15s, build_runner 있을 때만): Pending 빌드는 프리플라이트 프로브 파드(멱등 제출)→OK 마커 확인 후 빌드 파드 제출, Running 빌드는 poll→finish. 나이 기반 회수(preflight 180s/build 7200s)와 I6 일시 오류 관용이 짝. |
| `src/dms/rollout_watcher.py` | rollout-watcher(기본 10s, rollout_runner 있을 때만): 릴리스 head(최소 seq)만 record-then-patch로 적용하고 완료는 오직 클러스터 관찰로 판정. 실패는 finish+abort_pending 단일 트랜잭션(_fail), 벽시계 회수(_reclaim)가 observe보다 먼저. |
| `src/dms/queue_reader.py` | 컨트롤러 루프가 아님 — api/app.py:51이 app.state에 주입하고 routes_metrics.py:189가 요청 시 읽는 Volcano 큐 가시성 리더(None=모름 vs []=빈 큐 구분이 최초 권위). |
| `src/dms/artifact_base.py` | artifact-base-check 루프 본체 controller_check_once(84행): artifact_base를 컨트롤러 자기 파일시스템에서 왕복 검증해 control_state에 uri+ok+reason 기록(간격은 reconcile과 공유). |
| `src/dms/request_purger.py` | request-purge(기본 15s `DMS_REQUEST_PURGE_INTERVAL_SECONDS`, purge_runner 있을 때만, 2026-10-08): 요청 삭제 아웃박스(`request_purges`)를 k8s → files → purging 으로 수렴시킨다. 틱 = 끝난 purge 파드 수거·실패 귀속(_reap) → due 행(purging 제외, **재시도 시각 순** 20건·20s 예산 — 오래 기다리는 행이 새 삭제를 굶기지 않게)의 k8s 스윕·파일 떼어냄 → 새 purge 파드(전역 1개)·FS 완료 판정 → finish(최종 scrub + 행 삭제) + `request_purged`. 실패는 백오프(min(interval×2^n, 900s))로 계속 재시도하고 last_error 로 지연 표면화(GET /api/admin/request-purges). 끝나지 않는 purge 파드(생성 후 데드라인 3600s + 유예 600s — 죽은 노드·D 상태 rm)는 지우지도 하나 더 띄우지도 않고 그 뒤에 막힌 행을 `purge_pod_stuck` 으로 표면화한다(운영자가 노드 확인 후 force). 끝난 파드의 실패 귀속은 파드 탓(`purge_pod_failed` — 이미지 pull·Pending 상한·하나도 못 지움, 계속 묶어 싣는다)과 항목 탓(`purge_entry_failed` — 같은 파드의 다른 이름은 지워졌는데 남음·끊긴 지점의 이름, 묶을 행이 없을 때 혼자 — `_entry_failures`)으로 나눈다. k8s 대기가 지연(`purge_waiting_pods`)이 되면 재확인 간격이 기다린 시간의 1/10(상한 300s). |
| `src/dms/purge_runner.py` | request-purge 의 k8s I/O: parse_ref(그 잡의 DMS 명명 ref 만 — 모양 거르기일 뿐 소유 확인 아님)·sweep(**전체 job_id 로 확인된 객체만**: 라벨 `dms.io/job-id` Pod·vcjob + launcher `volcano.sh/job-name in (ref·라벨 vcjob ∪ 결정적 이름 6개)` 중 소유 vcjob(ownerReference uid)이 이 잡의 라벨 vcjob 이거나 이미 없는(고아) 것 — launcher 엔 dms 라벨이 없다, 이름(job_id[:12])만으로는 지우지 않는다, Terminating 포함 남은 수)·purge 파드(build_purge_pod: root + DAC_OVERRIDE·FOWNER 만, base 볼륨 하나, 고정 PURGE_SCRIPT + positional 이름 — rm 실패는 이름마다 끝까지 시도하고 마지막에 exit 1, 결정적 이름)·StubPurgeRunner(비 volcano). |
| `src/dms/artifact_trash.py` | request-purge 파일 단계(컨트롤러 전용): base 를 fd 로 열어 전제(소유자 euid·g+w/o+w 없음) 확인 → `<base>/<job_id>` 를 `<base>/.dms-trash/<job_id>`(root 0700, 같은 FS) 로 renameat(dir_fd). 삭제는 하지 않는다. entry_states 가 (base 에 있음, trash 에 있음)으로 진행을 FS 에서 도출. |

### 불변식 (위반하면 깨진다)

- 리스 획득은 의도적으로 per-loop try 밖(controller.py:114-127, run_all_once): 지속 DB 장애 시 예외 전파로 프로세스가 죽어야 한다(crash-restart = HTTP 헬스 없는 컨트롤러의 자기 종료 동등물). try 안으로 옮기면 조용히 도는 정지가 된다 — test_persistent_lease_death_still_crashes_the_controller가 집행부.
- 리스 규약(repositories/control.py:145 try_acquire_lease): 같은 holder는 항상 갱신, 다른 holder는 expires_at 경과 후에만 탈취. lease_seconds = max(interval*3, 30) (controller.py:122-124). 리스는 틱 중 갱신되지 않으므로 모든 루프 본체는 즉시 반환해야 한다(rollout_watcher.py:5-7 주석이 명문화).
- **틱 LDAP 시간**(2026-10-07, 보조 그룹): planner·stepper 는 한 틱의 resolve 누적 시간이 `identity.LDAP_TICK_BUDGET_SECONDS`(10)를 넘으면 새 resolve 를 하지 않고(서킷과 같은 보류 — planner 는 요청을 Pending 으로, stepper 는 미계수 보류), 진행 중 resolve 엔 남은 예산을 deadline 으로 넘긴다(리졸버는 URI 시도 전·사용자 검색 전·그룹 페이지마다 확인; resolve 자체 마감 `identity.LDAP_RESOLVE_DEADLINE_SECONDS`=10 — 남은 몫이 그 이상이면 deadline 을 넘기지 않는다, `identity.tick_resolve_deadline`). **남은 몫이 resolve 를 멈추면(`IdentityDeadlineExceeded`) 장애가 아니라 예산 소진**이다 — planner 는 `LdapCircuitOpen`(요청 Pending), stepper 는 미계수 보류(`ldap_budget`). 자체 마감·전송 오류만 진짜 판정이라 틱 첫 resolve 가 진행을 보장한다(2026-10-08 리뷰: 예전엔 둘 다 plain IdentityUnavailable 이라 LDAP 가 멀쩡해도 예산 경계의 요청이 종단 거부·D2 계수됐다). 다중 URI 는 시작 위치를 기억한다(sticky — 마지막으로 붙은 URI, 마감으로 못 가 본 첫 URI; `identity_ldap.connect_first` rotation) — 죽은 앞쪽 URI 의 타임아웃을 resolve 마다 내지 않고, 마감이 뒤쪽 URI 를 영영 굶기지 않는다. 최악 = 예산 + 진행 중 한 단계(한 URI 의 연결·StartTLS·bind = 3 × `DMS_LDAP_TIMEOUT_SECONDS` — bind 뒤 서버 정보 읽기(rootDSE·subschema 검색 2회)는 `ldap3.Server(get_info=NONE)` 로 끈다; 켜져 있으면 5T) = 10 + 15 = 25 < 루프 리스 30s. 넘으면 두 번째 컨트롤러가 리스를 탈취해 같은 루프를 동시에 돈다(중복 제출). `ROLLOUT_REQUEST_TIMEOUT_SECONDS` 와 같은 성격이라 설정 키로 빼지 않는다 — 리스 계산식·LDAP 타임아웃·예산 중 하나를 바꾸면 셋을 함께 본다(타임아웃 > 6s 면 여유가 깨진다, deploy/README §2c). 측정점: stepper 의 stderr `stepper: tick …s ldap …s/10s circuit=…`(느린 틱·서킷·예산 소진 틱만).
- stepper 층1 가드(stepper.py:258, _step_one): tool이 TOOL_TO_POLICY 밖이면 제출 전 _fail_closed(unknown_tool) — DB가 신뢰 경계(create_job은 무검증 INSERT)이고, fall-through하면 drm 꼴 argv(파괴적)로 실행된다.
- 신원 가드(stepper._build_spec → identity_problem, 2026-09-09): worker_pool.identity 의 uid/gid 가 int(bool 제외)·username 비어 있지 않음·privileged==(uid==0) 이 아니면 제출 전 _fail_closed(identity_missing_at_step) — execution_manifests 의 uid/gid 0·username root 기본값이 변조 행에서 도달 불능이 된다. 모든 제출 경로(preflight/preview/exec_preflight/execution)가 _build_spec 을 지나므로 단일 관문. 2026-10-07(보조 그룹)부터 같은 모양 검사가 **비특권 주 gid 0**(D5 백스톱 — planner 는 `identity_root_group_without_privilege` 로 거부하지만 규칙 전에 계획된 잡·변조 행은 runAsGroup 0 을 싣는다)과 **보조 gid 4키**(`identity.supplementary_gids_problem`: 하드 상수 MAX_SUPPLEMENTARY_GROUPS(256)·GID_MAX(2147483647)만 보고 — 설정값을 보지 않아 설정 변경이 위조로 보이지 않는다 — 엄격 오름차순·주 gid 미포함·privileged 면 빈 목록·상태 키와 목록의 결속(비어 있지 않음 ⇔ applied))도 본다. **보조 gid 키 부재·None 은 [] 다(거부 아님)** — 부재 = 계획 시점 미적용(배포 전에 계획된 잡)이고 모르는 값을 지어내지 않는다(더 좁은 권한). 비리스트·무효 원소·결속 위반만 identity_missing_at_step. vcjob PENDING 폴링도 같은 검사를 즉시 돈다(제출 뒤 변조 — 도구 시작 전이라 종단해도 잃을 것이 없다).
- **보조 그룹 재확인**(2026-10-07 D1·D2, stepper.py 모듈 docstring): 보조 그룹은 계획 시점 스냅숏(worker_pool.identity 4키)이고 **늘지 않는다**(최신 LDAP 에만 있는 gid 는 무시 — 새 그룹은 재신청). 목록이 비어 있지 않은 비 root 잡은 4 제출 경로(_build_spec)에서 LDAP 를 다시 보고(스냅숏 ⊆ 최신 유효 집합(상한 적용 전)·uid/gid 동일, 아니면·계정 삭제면 identity_changed_at_step), vcjob PENDING(preview·execution)에선 모양은 즉시·LDAP 비교는 클레임 루프 **뒤 2패스**(`_run_queued_rechecks` — 그 틱의 첫 LDAP 사용이 언제나 계수되는 제출 시도라 "한 번 불가를 보면 나머지는 보류"가 문언 그대로 성립하고, updated_at 을 갱신하지 않는 폴링 잡이 제출을 굶기지 않는다). 파드 단계(preflight·exec_preflight) PENDING 은 큐 재확인 대상이 아니다(바로 뒤에 제출 재확인이 온다). 제출 재확인의 LDAP 불가는 **상태 불변 보류**(updated_at 갱신 = 큐 뒤로) — 60s 간격(`_LDAP_RECHECK_SPACING_SECONDS`)으로 재시도 3회(`_LDAP_RECHECK_RETRIES`) 뒤 또 실패하면 ldap_unavailable 종단. 시도 횟수는 **이벤트가 카운터**다(`identity_recheck_deferred` +1, `identity_groups_checked` 리셋 — 스키마 변경 없음); 카운터 이벤트는 `record_event_strict` 로 쓰고 쓰지 못하면 그 잡을 ldap_unavailable 로 종단한다("3번만"을 보장할 수 없으면 무한 재시도 대신 종단). 같은 틱 서킷·예산·간격 미달 보류는 미계수(남은 예산이 resolve 를 멈춘 경우 — `IdentityDeadlineExceeded` — 도 예산 보류, 캐시하지 않음). 큐 대기 재확인의 실패는 미계수 no-op(무이벤트 — '3번' 계수는 제출 재확인만). 사용자별 데이터 오류(`IdentityLookupInvalid` — 사용자 엔트리 중복·결과 코드 4·11·그룹 페이지 상한)는 서킷을 열지 않는다(그 잡만 계수); 전송 오류·마감·서버 전역 결과 코드만 연다. 틱 캐시(사용자 → 최신값, None=계정 삭제도 캐시)는 JobStepper 인스턴스 dict 다 — 틱을 넘는 메모리 상태 금지(위 함정: 인스턴스는 틱마다 새로 생성). 보류 중 Succeeded 파드가 사라지면 preflight_failed 가 아니라 ldap_unavailable(+ `identity_recheck_failed` 이벤트 reason `held_pod_vanished`). 보조 gid 가 있는데 LDAP 미구성이면 ldap_not_configured 종단. LDAP 원문(URI·소켓 오류)은 이벤트에 싣지 않는다(요청 이벤트는 비관리자 요청자에게도 반환된다).
- _abs는 posixpath.join + rel.lstrip("/")(stepper.py:91-109): lstrip 없으면 join이 절대경로 둘째 인자에서 root를 버려 변조된 절대 target이 managed_root 밖을 지운다. root 결측은 StorageMissingAtStep으로 fail-closed(폴백 금지 — 예전 폴백은 조용한 데이터 증발).
- diag 박제는 종단 전이 **전**(stepper.py:151-157, _finalize): 박제 후 크래시면 다음 틱이 finalize 재시도(archive는 IS NULL이 중복 방지), 역순이면 종단 잡은 다시 스텝되지 않아 박제 기회가 영영 사라진다.
- 잡 파드의 mpi-hostfile 에는 **IP 만** 쓴다(2026-10-02 프로덕션 간헐 실패 수리, runner._wait_workers_ready): Volcano svc 서비스는 Ready 파드만 DNS 에 올리고(publishNotReadyAddresses 없음) launcher 가 워커보다 먼저 Ready 가 되는 일이 흔해, 예전처럼 getent 한 번 실패 시 `<pod>.<svc>` 이름을 남기면 mpirun(orted ssh) 시점의 이름 조회 실패(airgap 에선 "Temporary failure in name resolution")가 rc 255 로 잡을 죽였다. 이름 폴백을 되살리지 마라 -- 준비가 안 되면 mpirun 없이 DMS_EXEC_REASON=workers_unreachable 로 끝내고, 스테퍼가 그 마커를 미리보기·실행 실패 사유로 승격한다(_run_failure_reason, 화이트리스트 execution_manifests.EXECUTION_REASONS; 마커·사유 문자열은 러너에 중복 정의 + 계약 테스트).
- preflight 실패 사유는 파드 로그의 DMS_PREFLIGHT_REASON= 마커를 화이트리스트로만 승격(stepper.py:192-211 _preflight_reason; preflight→preflight_failed, exec_preflight→execution_recheck_failed 폴백): 로그는 신뢰 입력이 아니라 밖의 문자열을 reason_code에 박으면 프론트 매핑 없는 원문 코드가 화면에 뜬다. 박제와 사유는 **한 번의** read_log에서 나온다(_archive_diag가 raw 반환) — 두 번 읽으면 그 사이 파드 GC로 박제 로그와 사유가 어긋난다.
- 제출 직후 _reclaim_if_terminal 재독(stepper.py:203-225): claim_steppable 스냅샷엔 잠금이 없어(autocommit) claim~제출 사이 취소된 잡의 파드가 클러스터 고아가 되는데, cancel_job은 종단에 409·terminate_job은 no-op이라 여기서 안 치우면 아무도 못 치운다. **행 자체가 없으면**(claim 뒤 요청 삭제, 2026-10-08) 네 제출 경로 모두 `set_phase_ref` 의 False(PG 는 행 FOR UPDATE — 삭제 트랜잭션과 직렬화)를 보고 방금 만든 ref 를 terminate 하고 `submitted_for_deleted_job`(request_id=NULL — 지워진 id 로 쓴 이벤트는 고아다) 후 "gone" — 스냅숏 상태로 폴백해 진행하지 않는다(요청 없는 rm/sync 실행).
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
- **request-purge 는 k8s 객체가 0(Terminating 포함)이 되기 전에는 파일을 만지지 않는다**(request_purger._step_k8s): 종료 중 launcher 가 `<job_id>/<phase>` 를 다시 만든다. 그 행의 **첫 k8s 스윕부터**(outcomes `k8s_waiting_since` — requested_at 이 아니다: 큰 배치 삭제는 수천 행이 줄을 서서 줄 선 시간이 거짓 지연으로 찍혔다, 2026-10-10) 10분이 지나도 남으면 `purge_waiting_pods` 로 지연 표면화할 뿐 넘어가지 않는다(노드가 정말 죽었을 때만 운영자가 force 삭제). 떼어낸 뒤 base 에 디렉터리가 다시 생기면 k8s 단계부터 다시 한다(`artifact_reappeared`).
- **아웃박스 행이 가리키는 job_id 외에는 지우지 않는다**(「DB 에 없는 디렉터리 = 고아」 추론 금지 — DB 복원·리셋 한 번이면 base 전체가 지워진다). 행의 base 가 지금 base 와 다르면 옛 base 는 열지도 지우지도 않고 `artifact_left_at_old_base` 경고. 지운 id 가 requests/data_jobs 에 다시 있으면 k8s·FS 무접촉(`purge_target_still_present`), 행 모양이 깨졌으면(`purge_row_invalid`) 아무것도 안 한다. ref 는 그 잡의 DMS 명명일 때만 받는다(purge_runner.parse_ref — 변조 행이 제어면 파드를 가리키는 것을 거른다, 거른 ref 는 `purge_ref_rejected`). **k8s 객체는 전체 job_id 로 확인된 것만 지운다**(라벨 `dms.io/job-id`, launcher 는 소유 vcjob 의 라벨·uid) — 객체 이름은 job_id[:12] 만 담아, 접두가 같은 변조·복원 행이 이름으로 지우면 살아 있는 다른 잡을 죽인다(target_still_present 는 전체 id 만 비교한다, 2026-10-09).
- **purge 이벤트는 전부 request_id=NULL**(id 는 payload): 지운 id 로 쓰면 어디서도 안 보이는 고아가 되고 finish 의 scrub 이 지운다. `purge_failed` 는 last_error 가 바뀔 때만.
- request-purge 틱 순서(request_purger.run_once): 끝난 purge 파드의 실패 귀속은 그 틱의 떼어냄 **전에** 한다 — 파드가 비운 뒤 같은 이름의 새 사본(pending_trash 해소)이 trash 에 들어오면 그것을 「파드가 못 지운 것」으로 오인한다. 완료는 파드 상태가 아니라 FS 재확인(base·trash 둘 다 없음)으로만.

### 함정 (모르면 밟는다)

- build-watcher·rollout-watcher는 runner가 None이면 루프 자체가 조립되지 않는다(controller.py:88-107) — 배선 누락 시 조용히 빠지고 에러가 없다. request-purge 도 같다(purge_runner None → 루프 없음, 2026-10-08) — cli 는 언제나 `wiring.build_purge_runner` 를 넘기고(비 volcano = StubPurgeRunner: 회수할 객체 0, purge 파드는 기록만 하고 **파일을 지우지 않는다** — 스텁 어댑터는 아티팩트를 안 쓰므로 보통 trash 가 비어 있다), `tests/test_cli.py` 가 `request-purge=ok` 로 배선을 고정한다. 빠지면 삭제한 요청의 파드·결과 파일이 영원히 남고 아웃박스 행이 쌓인다(오류 없음 — 정리 현황의 pending 만 는다).
- request-purge 도 drain 이면 완전 no-op 이다(새 purge 파드를 띄우지 않는다 — stepper 와 같은 규칙). 정리 대기 중 이미지를 이 기능 이전으로 롤백하면 옛 컨트롤러는 아웃박스를 모른다 — 행은 남고(재업그레이드 시 재개) 그동안 결과 파일·파드가 남는다(deploy/README §12).
- stepper는 control_state.drain이면 완전 no-op(stepper.py:72-74)이지만 controller의 _stepper_step 안 preview 만료·고아 스윕은 drain과 무관하게 돈다(controller.py:37-60).
- artifact-base-check와 storage-reconciler는 같은 설정 키(reconcile_interval_seconds)를 공유한다(controller.py:82-86) — 간격 튜닝이 둘 다에 걸린다.
- JobStepper·Planner·워처들은 틱마다 새로 생성된다(build_loops의 람다) — 인스턴스 변수는 다음 틱까지 살아남지 않는다. RolloutWatcher의 DaemonSet 진행 시계가 releases.progress 컬럼으로 지속되는 이유(rollout_watcher.py:87-90).
- planner 유예 이벤트(identity_propagating)는 사유(payload)가 바뀔 때만 기록(planner.py:100-114) — 틱마다 남기면 grace 300s에 최대 30건이 이벤트 목록(limit 100)을 덮는다.
- planner 유예 판정은 "identity_not_ready_on_node 노드가 하나라도"(planner.py:18-33): eligible_nodes가 노드당 첫 실패 사유 하나만 기록하고 identity 검사가 마지막이라 성립 — "모든 노드" 요구로 바꾸면 실 테스트베드(일부 노드 미마운트)에서 유예가 아예 발동하지 않는다.
- diag 꼬리 자르기는 바이트 기준이고 UTF-8 경계 조각은 버린다(stepper.py:39-50): errors="replace"로 넘기면 U+FFFD 부풀림으로 상한(16KB x 4 = 64KB, builds.LOG_TEXT_MAX와 계약 테스트로 곱 고정)을 넘는다.
- exec_preflight의 phase 이름이 초기 preflight와 다른 이유(stepper.py:429-432): 파드 이름이 phase를 포함해서, 같으면 초기 preflight 파드 잔존 시 AlreadyExists→submit_failed.
- _build_spec에서 policy None은 크래시가 아니라 타임아웃 없음 관용(stepper.py:135-141) — 층1 가드 이후 policy None은 "정책 행이 지워진" 운영 조작뿐.
- batch 자식의 auth_method는 **배치 생성 시점의 인증 방식을 물려받는다**(batches.auth_method 박제 → batch_orchestrator._materialize 상속, 구형 NULL 행은 "token" 폴백). d51부터 배치 생성 자체가 세션+allowlist 특권 게이트로 통일돼 신규 배치의 자식은 특권(root)으로 계획된다 — planner의 특권 판정(session_authenticated + allowlist)이 이 상속값에 걸린다.
- DaemonSet 진행 시계 리셋엔 세대 게이트 선행(rollout_watcher.py:99-105): 패치 직후 옛 세대 status의 updated==desired를 믿고 progress를 올리면 이후 진짜 진행이 시계를 한 번도 못 리셋한다.
- Deployment에는 진행 시계 리셋을 하지 않는다(rollout_watcher.py:92-95): applied_at이 sticky PDE 판별 기준이라 앞당기면 진짜 PDE까지 stale로 읽혀 유일한 종단 수단이 사라진다. 대신 타임아웃 x3(_DEPLOY_TIMEOUT_FACTOR).
- build 프로브 SUCCEEDED인데 OK 마커 미확인이면 실패를 지어내지 않고 다음 틱 재시도(build_watcher.py:131-135) — 로그 일시 결손 대비, 최후 회수는 preflight 타임아웃.
- queue_reader.py는 이 과제 목록에 있지만 컨트롤러 루프가 아니다 — build_loops에 없고 api/app.py:51에서 app.state로 주입돼 라우트가 요청 시 읽는다.
- Queue는 반드시 이름 지정 GET(queue_reader.py:14-16): RBAC resourceNames는 list에 적용되지 않는다(저장소가 두 번 적어 둔 함정). PodGroup은 이름 규칙·라벨이 계약이 아니라 목록+필터만 안전.
- _stepper_step의 orphan 스윕 상한(오래된순 LIMIT 200)은 리포지토리(terminal_jobs_with_live_request) 몫이다 — controller.py에는 상한 코드가 안 보인다. 0건 스윕은 정상값이라 아무것도 기록하지 않는다.

### 결합점

- execution_adapter(execution.py StubExecutionAdapter 또는 실 어댑터): stepper·pod-gc가 submit/poll/read_summary/read_log/terminate를 호출 — 살아 있는 잡의 파드/vcjob 생명주기의 유일한 경계. 예외는 **삭제된 잡**: 행이 없어 어댑터의 시야 밖이므로 request-purge 가 purge_runner(자기 KubernetesClient — `list_pod_briefs`·`list_vcjob_briefs`·delete, 404 삼킴)로 라벨(전체 job_id)·소유 vcjob 으로 확인한 객체를 직접 지운다(ref 는 parse_ref 로 거른 뒤 launcher 를 찾을 vcjob 이름 후보로만). purge_runner 는 실행 어댑터 프로토콜과 분리돼 있다(build_runner 선례).
- purge_runner(PurgeRunner/StubPurgeRunner): request-purge 의 ref_belongs_to_job/sweep/list_purge_pods/create_purge_pod/delete_purge_pod. purge 파드 이미지는 잡 이미지(resolve_job_image 클로저), 노드는 agents.list_nodes 의 신선 보고 중 지금 base 를 exists·writable 로 본 노드 − node_exclusions.blocked_nodes − k8s_unschedulable. artifact_trash(떼어냄·entry_states)는 컨트롤러 전용(api 는 import 하지 않는다).
- repositories/*: control(리스 try_acquire_lease·control_state·get_policy·set_artifact_base_check), requests(list_pending·finalize_from_job·set_state_with_result), data_jobs(claim_steppable·expire_previews·terminal_jobs_with_live_request·archive_diag_logs·mark_exec_submitted), agents(fresh_reports·prune_reports), storages(get/set_status), batches, builds(pending/running/finish/terminal_older_than), releases(active/finish/abort_pending/note_progress), observability(record_event·prune_events), request_purges(due/in_stage/advance/defer/fail/target_still_present/finish — request-purge 전용).
- placement.py: planner의 select_tool_and_candidates/resolve_fanout/TOOL_TO_POLICY — stepper도 같은 TOOL_TO_POLICY로 정책 조회·층1 가드.
- identity.py resolve_job_identity: planner 4단계 게이트. identity_resolver는 run_forever 인자로 주입.
- artifact_base.py resolve_artifact_base(DB가 env를 이김)·controller_check_once: stepper의 artifact_uri 생성과 artifact-base-check 루프가 공유.
- build_runner(BuildRunner): build-watcher의 submit_preflight/submit/poll/read_log/failure_reason·pod-gc의 빌드 파드 terminate. 파드 이름은 repositories/builds.build_pod_name·build_probe_pod_name에서 결정적.
- rollout_runner: rollout-watcher의 patch_image/observe/pod_briefs. 판정은 rollout_status.assess_deployment/assess_daemonset, 좌표는 repositories/releases.COMPONENTS.
- api 계층: batch-orchestrator가 만드는 requests를 planner가 소비(생산-소비 사슬), reconciler가 쓰는 storages.status를 planner storage admission이 소비. queue_reader는 api/routes_metrics.py:189가 소비.
- frontend/src/lib/reasonCodes.json + tests/test_reason_codes_coverage.py: 워처들이 만드는 reason_code 문자열의 커버리지 가드.

---

## 5. 데이터·영속 (Data & persistence)

단일 커넥션 + RLock 직렬화 DB 계층(db.py) 위에 CREATE/_ensure_columns 이중 경로 마이그레이션(migrations.py)과 도메인 검증(domain.py), 그리고 테이블별 write-once·원자화 규약을 강제하는 17개 리포지토리로 SQLite/PostgreSQL 이중 방언 영속을 제공한다.

**데이터·제어 흐름**: API/컨트롤러 → Repositories(repositories/__init__.py, 유일 진입점) → Database.execute/query(db.py) → _run(RLock 직렬화 → _adapt 방언 변환 → 실행 → PG 죽음 판정 시 _reconnect+1회 재시도) → SQLite 파일 or PostgreSQL. 쓰기 경로: 리포지토리 메서드가 db.transaction()으로 BEGIN→(업무 UPDATE/INSERT + state_transitions/audit_log 동반 기록)→COMMIT; 진단은 예외로 observability.record_event가 트랜잭션 밖 단독 INSERT. 기동 경로: initContainer/one-shot Job → migrate(db) → pg_advisory_lock → CREATE IF NOT EXISTS 스크립트 → DROP runs → _ensure_columns(ALTER 보강) → _widen_count_columns → 인덱스 생성 → _backfill_submit_wait → 시드(policies/control_state). 읽기 집계: metrics.job_stats가 typed 컬럼 GROUP BY + 파이썬측 시각 감산으로 대시보드 응답을 만든다.

### 모듈

| 파일 | 책임 |
|---|---|
| `src/dms/db.py` | 단일 커넥션 DB 계층: named param(:name)→방언 변환(_adapt), RLock 전역 직렬화, PG 죽음 판정(_connection_is_dead 이중 게이트)+재연결 1회 재시도(_run), transaction() 컨텍스트(_txn_depth로 트랜잭션 중 재시도 금지), connect_timeout=5s, PG 세션 jit=off(연결 직후 SET, best-effort), utc_now_iso/iso_plus/iso_epoch/dump_json/load_json 공용 유틸. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/migrations.py` | 전체 스키마 선언 스크립트: pg_advisory_lock(MIGRATE_LOCK_KEY)으로 migrate() 전 구간 직렬화, CREATE TABLE IF NOT EXISTS(신규 DB) + _ensure_columns ALTER(구형 DB) 이중 경로, _widen_count_columns(int4→BIGINT), _backfill_submit_wait, DROP TABLE runs(유일 파괴적 마이그레이션), policies/control_state 멱등 시드, ALL_TABLES(19개) 열거. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/domain.py` | DB 무지 도메인 모델: 상태 enum(RequestState/DataJobState + TERMINAL_* frozenset), 경로 검증(validate_relative_path/sync_paths/rm_target), 연산별 옵션 allowlist(_OPTION_SPECS→validate_options), option_fingerprint(sha256), build_resource_key/build_data_payload, resolve_priority. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/__init__.py` | Repositories 집합체: db 하나로 17개 리포지토리(accounts/agents/requests/storages/control/data_jobs/batches/scan_paths/builds/observability/releases/metrics/sync_pairs/mail_settings/scan_digests/node_exclusions/request_purges)를 묶어 API·컨트롤러의 유일한 DB 진입점이 된다. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/data_jobs.py` | data_jobs+plans: create_plan_and_job(planner emit — plan·job INSERT + 요청 조건부 Pending→Planned 한 트랜잭션, 요청 행 FOR UPDATE; create_plan/create_job 은 픽스처용 단독 경로), set_phase_ref(행 부재 = False), set_job_state(종단 가드+submit_wait write-once), claim_steppable(FOR UPDATE SKIP LOCKED), mark_exec_submitted/record_sched_wait/archive_diag_logs(IS NULL 술어 write-once), terminal_jobs_older_than/terminal_jobs_with_live_request(오래된순 LIMIT 200 GC·고아 스윕), _ROW_COLUMNS_SANS_DIAG(다행 조회에서 diag_logs 배제). |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/requests.py` | requests+results+state_transitions: create(MAX(commit_order)+1, auth_method 기본 token fail-closed), set_state/_apply_state(경계·몸통 분리), finalize_from_job/set_state_with_result(전이+results INSERT 원자화), find_active/last_reason_code/has_active_for_requester. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/storages.py` | storages CRUD+감사: _validate가 노드 루트 "/" 등록을 명시 거부(storages.py:16-29), managed_root⊆mount_path 강제, create/update/delete 전부 트랜잭션 안에서 audit_log 동반. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/control.py` | policies(delete+insert upsert)/identity_denylist/control_state 싱글톤(id=1)/set_artifact_base·set_artifact_base_check(컬럼 분리 UPDATE)/component_leases(try_acquire_lease)/identity_probe_targets/audit_entries. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/builds.py` | builds: create가 active() 확인+INSERT를 한 트랜잭션으로 묶어 '활성 빌드 1개' 강제, seq=MAX+1 단조 증가, finish(log_text 꼬리 64KB 절단, 종단 가드 술어), list는 log_text 제외(I2), build_tag/build_pod_name 결정적 이름. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/releases.py` | releases: ROLLOUT_ORDER(agent→api→controller)·COMPONENTS 좌표표, create_batch(active 가드+seq 지속화 원자), _ORDER('seq IS NULL ASC, seq ASC, id ASC' 방언 중립 NULL 정렬), mark_applying/finish/note_progress(정체 시계 재장전)/abort_pending. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/observability.py` | events 진단 채널: record_event는 절대 예외를 안 올리고(try/except+logger.warning) 업무 트랜잭션 밖 단독 INSERT, prune_events는 배치 5000행씩 독립 트랜잭션으로 전량 소진 루프 — PG 는 지울 행을 `FOR UPDATE SKIP LOCKED` 로 잠근다(남이 쥔 행은 다음 틱, 2026-10-11 -- 아무것도 기다리지 않아 요청 삭제와 교착하지 않는다). |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/metrics.py` | 읽기 전용 대시보드 집계: agent_reports blob은 앱측 파싱, data_jobs typed 컬럼만 SQL GROUP BY(SUM CASE, FILTER 금지), duration은 파이썬에서 감산, submit/sched_wait는 IS (NOT) NULL 술어 2쿼리+excluded 건수 표면화. |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/accounts.py` | accounts: scrypt 해시(_hash/_verify_password), create/set_role/set_disabled/delete 전부 감사 동반 트랜잭션, delete는 user_scan_paths 동반 삭제, active_admin_count(마지막 관리자 잠금 방지 재료). |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/agents.py` | agent_reports(이력)+agent_nodes(노드별 최신 1행)를 ingest 한 트랜잭션으로 동기 유지, fresh 판정은 문자열 시각 비교, prune_reports 배치 소진 루프. |
| `src/dms/repositories/batches.py` | batches+batch_items: create(헤더+항목 N행 원자 INSERT), 항목 상태 전이(_touch_item), bump_counts 증분 갱신, reset_failed_items(재시도용 Queued 복귀+카운트 차감). list_summaries(2026-10-10): 작업 목록(관리자 응답)의 배치 자식 행에 붙는 배치 요약 8키(존재·이름·상태·자식 수·capped·비종단 자식 수·성공 scan 자식 수·항목 수(batches.item_count — 배치 단위 삭제의 항목 상한을 화면이 미리 본다, 배치 행 없으면 None)) — 자식 집합은 requests.batch_id. 한 배치의 자식 읽기(요약 갈래·목록 `?batch_id=`·delete_batch 상한 세기)는 `requests.batch_match`(범위 표기) + `ORDER BY batch_id, commit_order` 로 `idx_requests_batch_order` 를 LIMIT 만큼만 읽는다 — `batch_id = :b` 로 되돌리면 PG 가 큰 배치에서 commit_order 역방향·Seq Scan + LIMIT 로 테이블 대부분을 훑는다(2026-10-11). **세기는 배치마다 cap(배치 단위 삭제 자식 상한) + 1 에서 멈춘다**(2026-10-11: 배치마다 `LIMIT` 갈래의 UNION ALL, 100갈래 묶음마다 문장 셋; 넘으면 capped·비종단·성공 scan 은 None) — 3초 폴링마다 큰 배치의 자식 전부를 세고 그 전부로 성공 scan 세미조인을 돌면 PG 가 data_jobs 전체 Seq Scan 으로 계획을 바꿔 API RLock 을 쥐었다. 성공 scan 판정은 cap 이하 배치의 자식별 EXISTS(data_jobs(request_id) 인덱스). add_item·replace_items(CSV 교체)는 트랜잭션 안에서 배치 행을 다시 본다(없으면 None → 404, 지워진 배치에 고아 항목 방지 — 이미 남은 고아 항목 묶음은 delete_batch 가 expected 0 으로 지운다). replace_items 는 상태도 다시 봐(종단 아님 → False → 409) 라우트 판정 뒤 재실행 경합을 닫고, PG 잠금은 batch_items → batches(오케스트레이터 `_record_terminal` 과 같은 방향). delete 는 **배치 기록만**(자식 보존 — 작업까지는 request_purges.delete_batch). |
| `src/dms/repositories/scan_digests.py` + `src/dms/api/routes_usage.py` | 사용량 분석 리포트 요약 캐시(2026-10-02): 성공 scan 잡 1건의 dscan-report.json **투영 결과**를 job_id 단위로 `scan_report_digests` 에 둔다(리포트는 불변이라 값 무효화 없음, put 은 잡 행이 있을 때만 — 요청 삭제 경합의 재삽입 방지, 못 읽은 리포트는 두지 않음, 읽을 때마다 재투영 -- DB 신뢰 경계). 목록(`scan-targets`: 타깃마다 최신 1건 = 실 사용량·파일 수·hot 비율 컬럼)·내보내기(`export`: 전 타깃 최신+직전)·이력이 같은 캐시를 쓴다. 필터 storage(정확)·path(부분)·정렬 order 는 SQL 에서 limit 전에 건다(`DataJobsRepository._scan_target_where`). |
| `src/dms/repositories/request_purges.py` | 요청 삭제(2026-10-08): delete_terminal = 게이트(종단·batch_id NULL·batch_items 비참조·잡 전부 종단·조용한 창 DMS_REQUEST_DELETE_QUIET_SECONDS) + requests → data_jobs 행 잠금(PG FOR UPDATE) + PURGED_TABLES 7개 DELETE + CAS DELETE(패배 = 전부 롤백) + 정리 아웃박스 `request_purges` INSERT(job_id·phase_refs·artifact_uri·삭제 시점 base) + `audit_log('request','delete')` 32 KiB 스냅숏(전이 actor·run_as_root·실행 신원 4키, diag_logs·요약 원문 제외) — 한 트랜잭션, 이벤트 없음. 아웃박스 조작 due/advance/defer/fail(백오프 상한 900s, 포기 없음)/target_still_present/finish(늦은 events·results·digest·전이 최종 scrub + 행 삭제, 원 행이 되살아났으면 no-op)/status 는 컨트롤러 request-purge 루프 전용. 같은 id 재삭제(DB 복원 뒤)는 아웃박스 행을 새 삭제로 갱신(잡 열쇠 합집합). 몸통은 트랜잭션을 열지 않는 집합 코어 `_purge_locked`(2026-10-10)이고 단건(delete_terminal, CAS `batch_id IS NULL`)과 배치 단위(`delete_batch`, CAS `batch_id = :bid`)가 같이 쓴다. delete_batch = 배치(또는 배치 행이 없는 dangling 묶음) 하나를 한 트랜잭션으로: 게이트(배치 종단 → 자식 ≤ max_children → 확인 창이 본 자식 수 CAS → 다른 배치 항목 비참조 → 자식 전부 종단 → 잡 전부 종단 → 조용한 창, 첫 해당이 사유) + `_purge_locked`(자식 전부) + batch_items·batches DELETE + 유령 자식 재확인 + `audit_log('batch','delete')`(32 KiB, 배치 이름·옵션·실행 신원·항목·자식 id — 넘치면 중복인 자식 id 부터, 그다음 항목을 줄인 모양·들어가는 만큼(자식 없는 항목 먼저), item_count·items_kept 를 남긴다). 패배는 batch_changed. 항목 상한(`MAX_BATCH_DELETE_ITEMS`)도 batch_delete_too_large(상한 판정은 상한 + 1 행까지만 읽는다 — 넘는 배치의 자식·항목 전부를 잠그지 않는다), 다른 배치 항목이 자식을 가리키면 batch_child_shared(라우트는 같은 호출에서 다른 배치가 지워지면 그 사유의 배치를 다시 시도), 배치 행·자식 없이 항목만 남은 묶음도 지운다(2026-10-11). present_targets = target_still_present 의 묶음판(정리 루프 전역 단계가 대기열 전부를 거를 때). |
| `/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src/dms/repositories/scan_paths.py` | user_scan_paths: covers() 조상-경로 커버 판정(순수 함수), add(사전 존재 확인→INSERT→id 재조회), get_owned/delete_owned 소유자 스코프 강제. |

### 불변식 (위반하면 깨진다)

- db.py:112-126 _run — 재연결+재시도는 _txn_depth==0이고 _connection_is_dead가 참일 때만 정확히 1회. 트랜잭션 중(_txn_depth>0) 재시도는 부분 적용을 '만들어내는' 동작이라 금지(db.py:49-52).
- db.py:172-209 transaction() — COMMIT이 죽음으로 실패하면 재시도 절대 금지: 새 커넥션의 COMMIT은 빈 트랜잭션 no-op '성공'이라 유실을 성공으로 위장한다. 죽은 커넥션엔 ROLLBACK도 생략(원 예외 보존).
- db.py 트랜잭션은 중첩 불가 — BEGIN이 무조건이라 sqlite는 즉사, PG는 안쪽 COMMIT이 바깥을 조기 커밋해 조용히 비원자가 된다. 재사용은 requests._apply_state처럼 경계/몸통 분리로만(requests.py:81-98 주석).
- migrations.py:474-515 _ensure_columns — 새 컬럼은 CREATE TABLE과 _ensure_columns **양쪽**에 넣어야 한다(슬라이스 14 실 500 교훈: 한쪽만 넣으면 라이브에서만 컬럼이 없다). data_jobs 컬럼은 data_jobs.py:25 _ROW_COLUMNS_SANS_DIAG까지 세 곳(tests/test_repo_diag_logs.py 컬럼 패리티 계약).
- migrations.py:38-47 — migrate() 전 구간이 pg_advisory_lock(0x444D5310)으로 직렬화된다. 락 키는 2**63 미만 유지 필수(psycopg가 큰 int를 numeric으로 보내 pg_advisory_lock(numeric) 미존재로 initContainer 전멸, migrations.py:19-23).
- migrations.py:361-371 — DROP TABLE IF EXISTS runs는 반드시 CREATE 루프 **뒤**(tests/test_migrations.py:505 소스 순서 계약). ALL_TABLES는 도메인 19개이고 실 DB는 +batches/batch_items/schema_migrations=22개 — tests/test_migrations.py:522가 양방향 등식으로 전수 고정(+ 보조 테이블들, request_purges 포함), 인덱스 19개도 등식 고정.
- 요청 삭제 목록이 곧 일관성의 전부다(FK 0건) — request_id·job_id(·전이 entity_id)를 담는 테이블을 새로 만들면 `request_purges.PURGED_TABLES` 와 `_purge_locked`(delete_terminal·delete_batch 공용 몸통)·finish 의 DELETE 에 넣거나, 지우지 않는 이유와 함께 `PURGE_EXEMPT_TABLES`(batch_items·audit_log·request_purges)에 넣는다. 빠뜨리면 지운 요청의 행이 조용히 고아가 된다 — tests/test_repo_request_purges.py `test_request_delete_covers_every_reference_table` 가 스키마 전수 열거로 강제한다. 삭제 트랜잭션의 잠금 순서는 requests → data_jobs(planner create_plan_and_job 과 같은 방향, stepper 는 data_jobs 만) — 역순으로 함께 잠그는 경로를 만들면 교착이다. 배치 단위 삭제(2026-10-10, `delete_batch`)는 그 앞에 **batch_items → batches** 를 잠근다(batch_items → batches → requests(request_id 순) → data_jobs) — 오케스트레이터 `_record_terminal`·`reject_queued_item`·API `_recount` 와 같은 방향이다. batches 를 먼저 잠그고 batch_items 를 나중에 잠그는 경로를 만들면 이들과 교착이다. 배치의 자식 집합은 **`requests.batch_id`**(항목의 request_id 가 아니다 — 재실행은 항목 참조를 NULL 로 되돌려 옛 자식은 batch_id 로만 찾힌다)이고, 확인 창이 본 자식 수가 CAS 다(자식은 늘어나기만 하므로 수가 버전). 지운 뒤 같은 batch_id 의 요청이 남아 있으면(PG READ COMMITTED 의 FOR UPDATE 가 못 보는 동시 INSERT) 전부 롤백한다(유령 재확인) — 항목 행이 지워진 뒤엔 오케스트레이터 `_materialize` 의 claim CAS 가 0행이라 자식이 다시 생기지 않는다. 배치 하나 = 트랜잭션 하나이고 그동안 API 전체가 DB RLock 으로 멈추므로 자식 ≤ 1000·항목 ≤ 10000·호출당 배치 ≤ 10 상한이 readiness 를 지킨다(`request_purges.MAX_BATCH_DELETE_*`·`routes_request_purge.MAX_DELETE_BATCHES` — 넘는 배치의 청크 삭제는 BACKLOG). events 는 자식마다 request_id 순으로 지우므로 retention `prune_events`(id 순)와 잠금 순서가 다르다 — prune 은 PG 에서 `FOR UPDATE SKIP LOCKED` 로 남이 쥔 행을 건너뛰어 **어떤 잠금도 기다리지 않는다**(2026-10-11, 예전엔 배치 삭제와 deadlock detected). prune 에 잠금 대기를 되돌리지 말 것. 상태 쓰기(`RequestsRepository._apply_state`·`DataJobsRepository.set_job_state`)는 읽기를 PG 행 잠금으로 하고 UPDATE 영향 행 수가 1 이 아니면 KeyError 로 전이·results INSERT 까지 롤백한다 — 잠금 없이 읽고 삭제 커밋 뒤에 쓰면 지운 요청의 고아 전이·결과가 커밋됐다(조용한 창만이 막던 경합). `idx_data_jobs_request`·`idx_plans_request`·`idx_batch_items_request`(배치 항목 참조 게이트) 가 요청마다의 잠금·삭제·게이트를 인덱스 범위로 만든다.
- results.request_id는 PK — 중복 INSERT는 UniqueViolation. finalize_from_job(requests.py:162-186)·set_state_with_result(requests.py:188-201)가 전이+results INSERT를 한 트랜잭션으로 원자화해 '종단인데 results 없음'과 PK 중복 창을 함께 닫는다.
- data_jobs.set_job_state(data_jobs.py:143-183) — 종단 상태는 절대 되돌리지 않는다(조용히 무시 + 트랜잭션 밖 terminal_guard_skip 이벤트). 일어나지 않은 전이는 state_transitions에 기록하지 않는다.
- write-once 3종은 SQL 술어(IS NULL)가 최종 강제: submit_wait_seconds(set_job_state의 Pending→비Pending 엣지), exec_submitted_at(mark_exec_submitted, data_jobs.py:229-240), sched_wait_seconds(record_sched_wait, data_jobs.py:260-290), diag_logs(archive_diag_logs, data_jobs.py:242-258). _backfill_submit_wait도 IS NULL 필터로 이 계약을 우회하지 않는다(migrations.py:518-527).
- requests.commit_order / builds.seq / releases.seq — 단조 증가는 DB 제약이 아니라 애플리케이션(MAX+1)이 지킨다. commit_order 는 **현존 행 기준** 단조다(2026-10-08 요청 삭제: 최신 요청을 지우면 다음 제출이 그 번호를 다시 받는다 — UNIQUE 위반 없음, 무한 스크롤 커서 `before` 와 planner 순서 비교(현존 비종단끼리)는 안전, 수용). builds.seq에 UNIQUE/NOT NULL을 못 거는 이유: SQLite ALTER ADD COLUMN 제약 불가로 CREATE/ALTER 두 경로가 같은 스키마로 수렴해야 함(migrations.py:269-274 주석, releases.py:38-48).
- '활성 1개' 가드는 존재 확인+INSERT를 같은 트랜잭션(=같은 RLock 구간)에서: builds.create(builds.py:62-74), releases.create_batch(releases.py:72-77). 단 프로세스 경계는 못 넘는다 — replicas=1 전제.
- GC·고아 스윕은 오래된순(updated_at ASC)+LIMIT 200: terminal_jobs_older_than(data_jobs.py:340-362, updated_at 단조 비감소 전제), terminal_jobs_with_live_request(data_jobs.py:364-386, finalize 멱등+처리 행이 술어에서 빠져 틱마다 전진).
- observability.record_event는 절대 예외를 올리지 않고 업무 트랜잭션 밖에서 단독 INSERT(observability.py:16-29) — 진단 실패가 업무 변경을 롤백하면 본말전도.
- storages._validate(storages.py:10-34) — mount_path/managed_root에 '/'(노드 루트) 명시 거부, managed_root는 mount_path 하위여야 함. 검증은 create/update에만 발화 — 기존 '/' 행은 stepper._abs가 2차 방어.
- DB_CONNECT_TIMEOUT_SECONDS=5(db.py:13-20)는 프로브 주기 10s보다 짧아야 매 프로브가 반드시 503으로 끝난다 — 10 이상으로 올리면 자기 종료(§2.4)가 영영 발화하지 않는 결함이 부활한다. URL이 connect_timeout을 명시하면 그 값이 이긴다(db.py:85).
- PG 세션은 **jit=off**(db.py `_disable_jit` — 연결 직후 `SET jit = off`, 재연결에도 같은 `_open`, 2026-10-11) — DMS 쿼리는 전부 OLTP 인데 PG 기본 jit=on 은 추정 비용이 부푼 문장(작업 목록 배치 요약의 배치당 UNION ALL 갈래 등)에 문장마다 수 초의 LLVM 컴파일을 붙여 단일 커넥션 RLock 뒤의 /readyz 를 막았다(쪽에 큰 배치 50개: 5.7초 → 0.2초). 지우지 말 것. 시작 옵션(-c jit=off)으로 바꾸지 말 것 — jit GUC 가 없는 PG 10 이하·시작 파라미터를 거부하는 풀러에서 모든 연결이 FATAL 이 된다; SET 은 실패해도 삼키고 연결을 그대로 쓴다. URL options 가 jit 를 말하면 그 값이 이긴다(SET 하지 않음).
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
- request_purges: delete_terminal 은 api routes_request_purge 가(요청마다 자기 트랜잭션), 아웃박스 조작은 컨트롤러 request-purge 루프가, pending_count 는 routes_artifact_base._job_count(base 잠금 = 잡 + 정리 대기)와 POST 응답의 purge_pending 이, status 는 GET /api/admin/request-purges 가 소비한다. 감사 행(mutation_class 'request', operation 'delete')은 감사 화면이 원문 그대로 보인다.

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
| `src/dms/execution_volcano.py` | VolcanoExecutionAdapter(ref prefix pod/pods/vcjob 라우팅, TimedOut 판정, summary 경로 재구성, vcjob 라벨 기반 로그) + 실 KubernetesClient(lazy in-cluster init, workload patch/get, queue/podgroup 조회, 2026-10-08 요청 삭제 정리용 list_vcjob_briefs(`deleting`·`uid` — uid 는 launcher ownerReference 대조)·list_pod_briefs 의 `deleting`(Terminating) — purge_runner 가 「남은 객체」에 Terminating 도 센다, vcjob 나열의 404(CRD 부재)는 빈 목록이 아니라 예외) |
| `src/dms/build_manifests.py` | 순수 빌더: buildah 빌드 Pod(privileged, 자원 봉투·emptyDir sizeLimit)와 적합성 프로브 Pod(egress 443 TCP + 레지스트리 + 노드 디스크 statvfs 검사). proxy_env(2026-09-08): control_state 의 http/https/no_proxy → 대소문자 두 벌 env, NO_PROXY 에 레지스트리 호스트·localhost 자동 추가; 프로브는 프록시가 있으면 CONNECT 터널로 egress 검사(build_proxy_unreachable). 스탬프 sed 는 레지스트리까지 치환(신규 사이트에서 무동작이던 결함 수정) |
| `src/dms/build_runner.py` | BuildRunner: 멱등 submit/submit_preflight(AlreadyExists=자기 파드), poll, failure_reason(OOMKilled/Evicted 구분), read_log, terminate + StubBuildRunner(클러스터 없는 경로) |
| `src/dms/rollout_runner.py` | RolloutRunner: image_patch_body(strategic merge, initContainer 조건부) patch_image / observe(정규화 dict) / pod_briefs(best-effort 진단) + StubRolloutRunner |
| `src/dms/rollout_status.py` | 순수 함수: snake/camel 정규화(normalize_deployment/daemonset) + 수렴 판정 assess_deployment(세대 게이트→수렴→stale 필터된 PDE→ReplicaFailure 노출)/assess_daemonset(실패 확정은 워처 벽시계 몫) |
| `src/dms/manifest_tags.py` | PyYAML 없는 부분집합 YAML 파서로 동봉 deploy/k8s 매니페스트의 이미지 태그·DMS_JOB_IMAGE를 읽음(드리프트 배지용, 런타임 조회 전면 fail-soft / 계약 테스트 헬퍼는 assert). site_image(2026-09-08): 동봉 이미지의 레지스트리가 settings.build_registry 와 다르면 None — 다른 사이트(테스트베드) 태그가 신규 사이트의 기준값으로 새지 않게 routes_metrics·routes_registry 가 이 필터를 거친다 |
| `src/dms/wiring.py` | settings.execution_backend=="volcano" 여부로 실/스텁 어댑터·러너·큐리더 선택; artifact_base는 호출 시점 해석 클로저로 주입 |
| `src/dms_job_runner/runner.py` | launcher 오케스트레이션: 층3 ALLOWED_TOOLS 가드 → passwd 물질화 → ssh 키 복사 → hostfile 대기(nsync는 src/dst 각각) → 워커 준비 대기(_wait_workers_ready: getent 가 **IP 를 줄 때까지** + 그 IP 로 ssh 될 때까지, 전체 제한 WORKER_READY_TIMEOUT_SECONDS=300) → **IP 만** 쓴 mpi-hostfile → rank.sh 생성 → artifact_dir chown → runuser mpirun → stdout/stderr/summary.json 기록. 준비 실패면 mpirun 없이 DMS_EXEC_REASON=workers_unreachable 마커 + summary(returncode null) |
| `src/dms_job_runner/commands.py` | 순수 명령 빌더: mpirun(env+runuser --preserve-environment, ob1/tcp), ssh 키 복사(positional 인자로 인젝션 차단), ssh probe, getent hosts, nsync role-map |
| `src/dms_job_runner/parsers.py` | 도구 출력 파서(전부 fail-soft, 예외 금지): dsync Items/(N bytes) 마지막 매치, nsync Planned actions 합계+volume 단위 환산, drm Removed N items, dscan 리포트 JSON total_entries |

### 불변식 (위반하면 깨진다)

- 제어면 root 는 매니페스트 컨테이너 수준에만(40-api.yaml api / 41-controller.yaml controller: runAsUser 0·runAsGroup 0·allowPrivilegeEscalation false·readOnlyRootFilesystem true·capabilities drop ALL; migrate initContainer·파드 수준·30-migrate-job 은 securityContext 없음; Dockerfile.dms USER 65532 유지; 오버레이는 Deployment 를 패치하지 않음) — tests/test_release_manifest_contract.py 의 root 절이 전부 고정한다(manifest_tags.container_security_context 는 `- name:` 항목 단위 탐색 — bare _find(securityContext) 금지, 50-agent 처럼 블록이 여럿이면 엉뚱한 것을 집는다)
- base(deploy/k8s)의 image:·DMS_JOB_IMAGE 는 사이트 중립 자리표시자(manifest_tags.PLACEHOLDER_REGISTRY/…:PLACEHOLDER_TAG)뿐 — 실 값은 오버레이(deploy/overlays/testbed|prod|ssc 의 images/newTag·patch-config)가 넣고 tests/test_release_manifest_contract 가 base 전 줄과 오버레이 images.name 을 고정한다(2026-09-14). 동봉값이 자리표시자면 site_image 가 None(모름)으로 접는다 — 특정 사이트 태그를 커밋하면 git pull 한 다른 사이트의 raw apply·배지·"사용 중" 판정이 남의 값을 쓴다(실사고)
- 오버레이 apply 는 이미지를 암묵적으로 바꾸지 않는다(deploy/overlays/guard-images.sh: 렌더 워크로드 이미지 != 라이브면 exit 3, ALLOW_IMAGE_CHANGE=1 로만 허용; prod/ssc install.sh 가 apply 직전 호출) — 포탈 릴리스는 라이브만 바꾸고 오버레이 파일은 그대로라 재적용이 옛 태그로 되돌리는 사고(테스트베드 d130→d129 diff 실증)를 막는다
- 동봉 매니페스트 값은 site_image(manifest_tags.py) 를 거쳐서만 화면·판정에 쓴다 — 레지스트리가 사이트와 다른 동봉값(포탈 밖 부트스트랩 이미지가 실어 온 테스트베드 태그)은 None(모름)이지 기준값이 아니다; 빌드 스탬프는 `[registry]/<img>:<tag>` 전체를 치환해 첫 포탈 빌드부터 동봉값이 그 사이트 것이 된다(build_manifests.py 스탬프 루프)
- 사내 프록시 CA 는 두 층에 실려야 한다(build_manifests 스크립트 ca_args): 파드의 buildah(Go, SSL_CERT_FILE)와 RUN 단계 컨테이너(-v 마운트 + 클라이언트별 --env) — 한쪽만 주면 pull 은 되는데 npm/pip 이 죽거나 그 반대; 번들은 시스템 CA + 사내 CA 합본(사내 CA 단독은 비-가로채기 사이트에서 진짜 인증서를 깨뜨림); --unsetenv 로 최종 이미지에서 제거(런타임에 없는 경로가 남으면 TLS 전체 붕괴); 프로브는 부모 디렉토리 마운트(파일이 없어도 떠서 build_proxy_ca_missing), 빌드 파드는 type File
- 호스트 네트워크 모드는 세 곳이 한 스위치다(build_manifests.host_network_for → 파드 hostNetwork + ClusterFirstWithHostNet + 빌드 스크립트 `--network=host`, 프로브 파드도 동일) — 파드만 켜면 buildah RUN 단계의 localhost 가 RUN 컨테이너 자신이라 loopback 프록시에 못 닿고, 프로브만 빠지면 build_proxy_unreachable 거짓 실패; loopback 프록시 호스트는 자동, 그 외는 control_state.build_host_network
- 빌드 프록시는 control_state(DB) 가 진실이고 BuildRunner 가 제출 시점마다 콜러블로 읽는다(wiring.py) — ConfigMap 에 두면 재적용마다 되돌아간다(build_node_name 과 같은 이유); 프록시 URL 은 자격증명 없이 저장(routes_control.validate_proxy_url, invalid_proxy_url) — 평문 비밀번호 금지 규약; NO_PROXY 는 항상 사이트 레지스트리를 품는다(proxy_env) — 빠지면 push 가 프록시로 나가 사내 레지스트리에 못 닿는다
- tool allowlist 3층이 전부 살아 있어야 한다: 층1 stepper._step_one(stepper.py:258, TOOL_TO_POLICY 밖 tool→fail_closed unknown_tool), 층2 execution_manifests.tool_argv(:61, 미지 도구는 argv를 지어내지 않고 ValueError→어댑터가 submit_failed로 접음), 층3 dms_job_runner/runner.py:21,36 ALLOWED_TOOLS(exec·부작용 전 종단). ALLOWED_TOOLS는 dms.config.AGENT_TOOL_NAMES(config.py:7)와 동일 값이어야 하며 dms_job_runner가 독립 패키지라 중복 정의 — tests/test_job_runner_runner.py 계약 테스트가 동일성을 강제한다.
- activeDeadlineSeconds는 반드시 task 템플릿의 PodSpec에 건다(execution_manifests._apply_task_deadlines:207) — Volcano v1.15.0 CRD가 Job.spec의 미지 필드를 조용히 prune해 타임아웃이 영원히 미발화한다. 반대로 ttlSecondsAfterFinished는 Job.spec 허용 필드라 거기 얹는다(_apply_ttl:223).
- 잡 파드 안의 아티팩트 경로는 항상 ARTIFACT_MOUNT(/dms-artifact-base, artifact_base.py) 아래다 — execution_volcano._volumes 가 base 를 스토리지 마운트와 별개의 전용 hostPath 볼륨으로 붙이고 execution_manifests._artifact_dir 이 그 경로를 러너에 준다(DMS_JR_ARTIFACT_DIR). 예전처럼 스토리지 마운트가 상위라고 생략하면 요청자 uid 도구가 공용 디렉터리(base 의 부모)를 통과해야 해 부모 잠금(750·700, §7 불변식 5)이 잡을 죽인다. 스토리지 mount_path 가 이 경로와 겹치면 invalid_storage(storages._validate). 호스트 경로(<base>/<job>/<phase>)는 제어면 읽기·artifact_uri 전용.
- hostPath 볼륨 이름은 execution_volcano.volume_name(mount_path) 만 만든다 — RFC 1123 label(소문자·숫자·'-', 63자) + 경로 해시 8자(충돌 방지). 경로 문자열을 그대로 이름에 쓰면 밑줄·대문자·점이 있는 사이트(/mgmt_storage, 2026-09-15 프로덕션)에서 apiserver 가 422 로 거부해 모든 잡이 submit_failed 가 된다. 제출 실패의 원문(exc.detail)은 stepper._record_submit_failure 가 이벤트(submit_failed)와 diag_logs 합성 항목(pod="submit:<phase>")으로 남기고, API get_job_logs 는 ref 없이도 그 박제를 돌려준다 — 코드만 남기고 원문을 삼키지 마라.
- 에이전트 마운트 프로브의 writable 은 host_root 모드에서 **호스트 mountinfo 옵션**(per-mount·superblock rw/ro)이다(probes.parse_mount_table/_mount_rw) — 에이전트는 root 라 os.access(W_OK) 는 "마운트가 rw 인가" 만 답했고, 호스트 루트 바인드 아래에선 경로가 최상위(ro)인지 전파된 하위 마운트(rw)인지에 따라 값이 갈려 신뢰할 수 없다(ro 쪽이면 placement(require_writable) 가 sync 목적지 노드를 전부 배제한다). 전파 자가 진단(`propagation_stale`: 호스트엔 마운트포인트인데 /host/root 아래에 없음 — 호스트 `/` 가 shared 가 아님)과 `host_root_missing` 은 조용한 Missing 을 금지한다. DMS_AGENT_HOST_ROOT 미설정은 레거시 직접 경로 모드(롤아웃은 이미지 → 매니페스트 순).
- 에이전트 nslcd(노드 NSS)는 제어면 리졸버(`identity_ldap.py`)를 **미러**한다(2026-09-29 프로덕션 사고): 같은 URI 목록(sssd 콤마 형식 → `uri` 한 줄씩, 후행 `/` 제거), 같은 StartTLS 정책(bind **전** TLS, `tls_reqcert never` = `CERT_NONE`), 같은 검색 계정(`DMS_LDAP_BIND_DN` ← dms-config, `DMS_LDAP_BIND_PW` ← dms-secrets 키 하나만 secretKeyRef). bind 계정이 빠지면 익명 바인드가 되는데, 익명 사용자 검색을 막는 디렉터리에선 아무도 해석되지 않아 전 요청이 `identity_not_ready_on_node` 가 된다(테스트베드 slapd 는 익명 읽기를 허용해 가려졌다). `deploy/docker/agent-entrypoint.sh` 가 읽는 `DMS_LDAP_*` 와 DaemonSet 이 주입하는 변수는 같아야 한다 — `test_agent_daemonset_contract` 가 양방향으로, `test_agent_nslcd_conf` 가 렌더 결과를 고정한다. **DaemonSet env 는 첫 보고 전 부트스트랩일 뿐이고 진실은 제어면이다**(방안 A, 2026-09-29): `identity_ldap.ldap_directory_config` 가 리졸버와 에이전트 블록(`agent_directory.directory_block`)의 유일한 추출점이고, 보고 응답 `directory` 로 내려가며 에이전트(`agent/directory.NslcdDirectory`)가 같은 엔트리포인트 렌더러로 nslcd.conf 를 다시 쓰고 바뀌었거나 nslcd 가 죽었을 때(수렴 뒤 해시만 오는 주기에도 생존 확인, 좀비는 죽은 것으로) 재기동한다 — 그래서 포탈 이미지 릴리스만으로 수렴한다. 적용이 실패하면 보고 해시를 비워 다음 응답이 반드시 전체 블록이 되게 한다(재시도·설정 되돌림 수렴). 렌더러는 값을 반드시 `printf '%s'` 로 쓴다 — dash 의 `echo` 는 백슬래시 이스케이프를 해석해 `\c`·`\\`·`\0nnn` 이 든 비밀번호를 조용히 바꾼다. 비밀번호는 능력 선언(보고의 `directory` 객체)한 에이전트에게, 해시(HMAC(session_secret))가 다를 때만 실리고 보고·로그·오류엔 없다 — 실제 전송 시점은 노드마다 에이전트 (재)시작 시 1회, 설정·session_secret 변경 시, 적용이 계속 실패하는 동안 매 주기다. 에이전트 채널은 클러스터 내부 평문 HTTP 다(관리자급 공유 토큰이 같은 채널을 매 주기 지나므로 신뢰 경계 확장 아님; 채널 TLS 는 BACKLOG). 에이전트의 nslcd 관리용 PID 파일은 root 전용 `/run/dms-agent`(nslcd 소유 `/run/nslcd` 아님), nslcd 는 최소 env 로 띄운다.
- **root(uid 0) 여부는 제출 시점에 확정된다**(2026-09-30 프로덕션 사고 + 같은 날 사용자 결정 "관리자는 기본 root"): 단건 요청은 payload `run_as_root is True` 이고 요청자가 자격(`api.auth.can_run_as_root` = 관리자 + `identity.privilege_eligible`: 허용 설정 + 세션 인증 + 특권 목록)을 가질 때만 root 다(`PRIVILEGE_REQUESTED`, 명시 true 인데 자격 없으면 `privileged_not_authorized` 거부 — 조용히 낮추지 않음). 서버는 **명시 true 만** root 로 받고 생략·false 는 비 root 다(`routes_requests.submit` — 생략을 root 로 읽으면 옛 포탈 탭·me 판정 불일치가 화면에 없던 root 로 돈다, 적대적 리뷰). "관리자 기본 root" 는 포탈(SubmitJob `rootEffective`)이 정해 관리자에겐 확정값을 항상 명시로 싣는다: 자격 있는 관리자는 **root**(체크박스 기본 켜짐), 단 **다른 실행 신원**(owner_username ≠ 요청자)을 지정했으면 그 사용자의 **LDAP uid/gid**(사고 경로를 기본값으로 되살리지 않음). payload 에 키가 없으면 planner 는 실행 신원의 LDAP uid/gid(`PRIVILEGE_NEVER`, fail-closed). `/api/auth/me` 의 `can_run_as_root`(`api.auth.can_run_as_root`, 제출 게이트와 같은 판정)가 포탈 체크박스 표시·기본값의 근거다. 배치 자식(관리자 전용 화면)만 종전 "자격 있으면 root"(`PRIVILEGE_IF_ELIGIBLE`). 예전엔 특권 목록에 있기만 하면 어느 화면이든 root 였고, root 로 돈 dsync 가 소스 최상위의 소유·권한을 **기존 목적지 디렉터리에 적용**해 남의 700 디렉터리 소유자를 바꾸며 성공했다. 제출 게이트(`routes_requests`, StrictBool)와 planner 가 둘 다 판정하고(정책 규칙은 `identity.privilege_policy` 하나), stepper 는 root 잡을 매 제출 직전 요청 행으로 재확인해 근거가 없으면 `privilege_not_requested` 로 종단한다 — 신원은 계획 시 worker_pool 에 얼어서, 규칙 변경 전에 계획된 잡(ConfirmPending 등)이 배포 뒤에도 root 로 도는 구멍을 막는다. sync 의 `open_noatime` 은 root 실행에서만 싣는다(비 root 의 O_NOATIME 은 타인 소유 파일에서 EPERM).
- sync preflight 는 목적지가 **이미 있으면 목적지 자체**를 본다(`execution_manifests._DEST_CHECK`): 쓰기·진입 불가 → `destination_not_writable`, 비특권이면 소유자 ≠ 실행 uid → `destination_not_owned`(dsync 가 최상위 chmod/utime 을 하므로 남 소유면 부분 복사 뒤 EPERM 실패 — 실측). 그다음 부모 쓰기는 목적지가 **있어도** 본다 — dsync(포크 dsync.c)가 목적지 존재와 무관하게 부모 W_OK 를 요구하고 실패 시 복사 0건으로 **종료 코드 0** 을 내므로, 건너뛰면 "root 755 부모 아래 본인 디렉터리" sync 가 아무것도 안 한 Succeeded 가 된다(d139 실증에서 잡음). 실행 모드(user/root)는 positional 인자.
- 잡 옵션의 서버 기본값은 `domain._OPTION_DEFAULTS` 가 **검증 뒤** 박는다(2026-09-17): sync `batch_files 1,000,000`·`bufsize 4,194,304`, scan `batch_files 1,000,000`·`broken_limit 100`, rm 없음(`recursive` 는 동의 게이트). 포탈 프리필(`optionRules.SYNC_INT_FIELDS/SCAN_INT_FIELDS`)과 같은 숫자여야 하고 `test_domain_option_defaults` 가 두 소스의 일치를 고정한다. 그래서 sync 의 "배칭 끔"은 키 생략이 아니라 **0 명시**(하한 0)다 — 생략은 기본값이다.
- 미리보기 dry-run 의 summary.json 사본은 `data_jobs.preview_summary`(JSON, 2026-09-17)에 지문과 **같은 UPDATE** 로 남는다(`set_preview(summary=)`) — 컨펌 창은 `result_summary`(실행 종단 결과, 컨펌 시점엔 항상 NULL) 가 아니라 이것을 보여준다. 없으면(구 잡·읽기 실패) NULL = 모름.
- artifact_base의 스킴 제거는 접두사 전용 strip_scheme만 — 전체 replace는 경로 중간의 file://까지 지워 러너 기록 위치와 마운트 계산·읽기 라우트가 갈라진다(execution_manifests._artifact_dir:180, execution_volcano._volumes:109-112, _reconstruct_summary_path:233).
- 어댑터의 artifact_base는 생성자 캡처 금지, 호출 시점 해석 callable(execution_volcano.py:82-88; wiring.py:23-29) — base 변경 후 컨트롤러 재시작 시 in-flight 잡 summary를 옛 경로에서 찾는 사고 방지.
- worker의 required podAntiAffinity(같은 job·같은 task, execution_manifests._worker_affinity:244)는 resolve_fanout의 node_count=min(len(candidates),max_nodes)(placement.py:123) 전제 위에서만 안전 — 레플리카가 후보 노드 수를 넘지 않아 산개 불가 영구 Pending이 구조적으로 없다. 셀렉터를 넓히거나 fanout 공식을 바꾸면 이 짝이 깨진다. **숨은 전제: 후보가 전부 스케줄 가능해야 한다** — planner 는 k8s 스케줄러가 아니라 에이전트 보고로 후보를 고르고 앞에서 N대를 굳히므로, cordon·NoSchedule taint 노드가 후보에 끼면 gang 이 안 서서 Pending 에 영원히 멈춘다(데이터 잡엔 Pending 상한이 없다). 그래서 아래 노드 배치 제외 불변식이 이 전제를 지킨다.
- 노드 배치 제외(2026-10-02, `repositories/node_exclusions.py` 모듈 docstring): 관리자 배치 제외(`node_exclusions` 테이블, 포탈 노드 화면 「배치 제외」·「다시 포함」, 감사 `node_exclusion`)와 k8s 스케줄 불가(에이전트가 자기 노드의 cordon·NoSchedule/NoExecute taint 를 `report.k8s_node.schedulable` 로 보고 — `agent/probes.probe_k8s_node`, null 은 모름이라 막지 않음)를 `blocked_nodes`/`k8s_unschedulable` 한 벌로 판정하고 **세 곳**이 강제한다: ① planner 후보 선정(`placement.eligible_nodes` — 도구 검사 뒤·신원 검사 앞, 남은 노드로 바로 계획, 0대면 `nodes_excluded` 즉시 거부 — 신원 대기 노드가 섞이면 기존 유예 코드), ② stepper 제출 직전(`_build_spec` → `NodeBlockedAtStep` → `node_excluded_at_step` 종단, 다시 계획하지 않음, 배치 항목도 그대로 종료), ③ 컨펌(`routes_jobs.confirm_job` 409 `node_excluded` + 종단), ④ stepper 폴링의 PENDING(제출됐지만 아직 스케줄 전 -- Volcano 큐·gang 대기, preflight 파드 대기; `_raise_if_blocked` 로 종단·회수 -- 제출 직전 검사만으로는 큐 대기 중 막힌 노드를 못 봤다). 노드에서 **이미 실행 중인**(RUNNING) 잡은 건드리지 않는다. 관리자·배치·토큰 제출에도 예외 없다. k8s 일시 조건 taint(node.kubernetes.io/{not-ready,unreachable,*-pressure,network-unavailable} -- 에이전트가 `transient` 로 표시)는 새 계획에서만 피하고 이미 계획된 잡은 종단하지 않는다(`k8s_hard_block`). 에이전트 DaemonSet 은 NoExecute taint 를 견딘다(tolerations Exists/NoExecute -- 쫓겨나면 taint 를 보고 못 하고 마지막 schedulable=true 가 남는다; NoSchedule 은 견디지 않는다 -- 원래 에이전트가 없던 taint 노드에 새로 뜨지 않게, `test_agent_daemonset_contract`). nsync 런처도 후보(출발 ∪ 목적지) 안에 고정한다(예전엔 affinity 없음 — 막힌 노드에 앉을 수 있었다). 노드에 잡을 보내는 새 경로를 만들면 같은 함수를 거쳐라. dms-agent 의 ClusterRole `nodes get`(10-rbac.yaml)은 이제 쓰인다 — 지우면 cordon 이 조용히 무시된다(fail-open).
- sync preflight는 목적지가 **존재하는데 디렉토리가 아니면** 거절한다(_preflight_script:291,298 destination_not_directory), 부모 쓰기 검사보다 **먼저**. 부모 권한만 보던 시절 목적지가 기존 파일이면 통과했고, dsync가 그 파일을 먼저 지운 뒤 디렉토리를 못 만들어(mkdir errno=2) 원본은 그대로인데 목적지만 사라지는 순수 데이터 손실이 났다(실증 d65). 검사는 role=None 통합 스크립트와 nsync의 role="destination" **양쪽**에 있어야 한다 — nsync는 목적지 노드에서 후자만 돈다.
- preflight 마커 사유는 PREFLIGHT_REASONS 화이트리스트 전수(execution_manifests.py:323 + parse_preflight_reason:329)와 스크립트 실물이 일치해야 한다 — 계약 테스트가 스크립트에서 마커를 추출해 대조한다. 코드 철자는 전부 frontend reasonCodes.json·api.ts REASON_MESSAGES와 동일(build 프로브 화이트리스트와 같은 규약).
- **preflight 의 보조 그룹과 워커의 보조 그룹은 같은 원천·같은 커밋**(2026-10-07 D13): preflight 파드의 **pod 수준** `securityContext.supplementalGroups` 와 워커 env `DMS_JR_SUPP_GIDS` 는 같은 spec.identity 에서 `execution_manifests._supplementary_gids` 하나로 파생하고(빌더가 `supplementary_gids_problem` 으로 다시 검사 — 문제면 ValueError → submit_failed; privileged 면 []), 둘을 바꿀 땐 같은 커밋에서만 바꾼다 — 갈라지면 preflight 는 그룹으로 `test -w` 를 통과하고 rank 엔 그 그룹이 없어 dsync 의 "부모 쓰기 불가 → 복사 0건 rc0 Succeeded"(위 sync preflight 불변식)가 다시 열린다. supplementalGroups 는 컨테이너 SC·워커/launcher 파드에 두지 않는다(sshd/runuser 의 initgroups 가 덮어써 rank 엔 무효 — 워커 그룹은 /etc/group 줄로만 들어간다), `supplementalGroupsPolicy` 는 지정하지 않는다(D8, Merge 기본). 양쪽 다 자기 그룹을 스스로 확인한다: preflight `_SUPP_GIDS_SELF_CHECK`(id -g == 주 gid, `id -G` 가 **{주 gid} ∪ 목록과 같은 집합 — 양방향**), 워커 셸(아래 함정의 물질화 5단계) — 아니면 `identity_groups_not_applied`(PREFLIGHT_REASONS·EXECUTION_REASONS 양쪽). 부분집합 검사로 느슨하게 하지 마라: 어드미션 웹훅이 그룹·fsGroup 을 더하거나 이미지 계정이 이미지 그룹에 속하면 preflight 만 추가 권한으로 통과한다. 목록이 빈 잡(그룹 없음·배포 전 계획)은 매니페스트가 이 기능 이전과 **바이트 단위로 같다**(env 키·supplementalGroups·자기검증 조각 모두 없음 — 그런 잡에 웹훅이 그룹을 더하는 경우는 자기검증 밖, BACKLOG). 그룹이 실린 preflight 에만 base other-x 비트(`_ARTIFACT_BASE_OTHER_X_CHECK` — 그룹 x 로 거짓 통과하는 `test -x` 대신 `stat -c %A` 10번째 문자)와 base 쓰기 불가(`_ARTIFACT_BASE_NOT_WRITABLE_CHECK` — `test -w` 는 access(2) 라 NFSv4/GPFS ACL 까지 반영)가 붙는다(§7 불변식 5).
- preflight Pod 이름은 phase(underscore→hyphen 치환)+role로 스코프(build_preflight_pod:325-334) — 한 잡이 preflight/exec_preflight 두 번 띄우고 nsync는 src/dst로 갈라지므로 이름 충돌(AlreadyExists)과 DNS-1123 위반(underscore→422)을 막는다. exec_preflight도 _PREFLIGHT_PHASES(execution_volcano.py:129)로 Pod 라우팅되어야 한다 — Volcano Job으로 지으면 이름에 underscore가 들어가 422.
- vcjob의 deadline 판정은 status.state.reason/message에서만(execution_volcano._vcjob_deadline_exceeded:50) — CRD의 conditions[] 항목에는 reason이 없어 파드식으로 읽으면 영원히 TIMED_OUT을 못 잡는다.
- workload patch는 strategic-merge-patch content-type 명시(KubernetesClient.patch_workload:392) — 기본 json-patch+json이면 apiserver 422. initContainers 절은 실제 initContainer가 있는 컴포넌트에만 붙인다(rollout_runner.image_patch_body:32-37) — patchMergeKey 병합은 없는 이름을 새 컨테이너로 추가해 파드 기동을 망가뜨린다.
- summary.json은 항상 정확히 3키 {returncode, files, bytes}, 모름은 null(runner._build_summary:135) — 파싱이 잡을 죽이는 경로는 없다(parsers 전체 fail-soft, 예외 금지 계약).
- 빌드/프로브 파드 이름은 build_id에서 결정적 → create의 AlreadyExists는 이전 틱의 자기 파드이므로 존재 확인 후 성공 취급(build_runner.submit:54-70, submit_preflight:90-101). ref 접두 BUILD_REF_PREFIX(build_runner.py:14)가 유일 출처 — build_watcher/pod_gc/routes_builds가 여기서 import.
- BUILD_SIZELIMIT_GIB(10)+BUILD_DISK_MARGIN_GIB(2)를 빌드 파드 봉투(emptyDir sizeLimit·eph limits)와 프로브 디스크 공식(DMS_PF_NEED_BYTES)이 공유(build_manifests.py:52-55,152-153) — 한쪽만 바꾸면 "프리플라이트 통과했는데 빌드가 노드 위협"으로 갈라진다. 프로브의 0.15는 kubelet evictionHard 미러 상수(:119).
- assess_deployment는 세대 게이트(observed>=generation)를 먼저, 수렴 검사를 PDE 스캔보다 먼저 본다(rollout_status.py:123-133) — 순서를 바꾸면 옛 ReplicaSet 기준 거짓 성공 또는 sticky PDE 거짓 실패(복구 배치 전체 중단). stale PDE는 lastUpdateTime<since(applied_at)로 판별(_is_stale_pde:100), 근거 없으면 stale 아님(PDE는 유일한 실패 확정 수단).
- ROLLOUT_REQUEST_TIMEOUT_SECONDS=10(execution_volcano.py:34)은 리더 리스 TTL(30s)과 맞물린 내부 불변식(틱 최악 2회 호출 20s<30s) — 설정 키로 노출 금지. get_queue/list_podgroups/list_pod_briefs/list_vcjob_briefs/patch/get_workload 전부 이 타임아웃 필수(urllib3 기본 무제한).
- repo_host(build_manifests.py:58)는 라우트 검증(invalid_repo_url 422)과 프로브 매니페스트가 같은 함수를 써야 한다 — 갈라지면 "제출은 통과, 프로브 생성 실패" 창이 생긴다.
- nsync hostfile 순서는 source 먼저(rank 0..N-1)→destination(runner.run_job 의 3단계, _wait_workers_ready 가 순서 보존) — commands.nsync_role_map의 rank 배정과 일치해야 role이 안 뒤집힌다.

### 함정 (모르면 밟는다)

- Volcano svc 플러그인은 task 이름의 하이픈을 언더스코어로 바꾼 hostfile을 만든다: source-worker → /etc/volcano/source_worker.host (runner.main 의 wait_hostfile, 테스트베드 실측). 하이픈 경로를 읽으면 빈 hostfile → mpirun "no nodes available".
- KubernetesClient.read_pod_log는 _preload_content=False로 원시 응답을 직접 디코드(execution_volcano.py:367-376) — 기본값이면 bytes의 repr(b'DMS_PREFLIGHT_OK\n')이 포탈에 그대로 노출된 실증 사고가 있다.
- 비 root(비특권) 실행의 sync에는 --chown <실행 신원 uid>:<LDAP 주 gid>가 자동 주입된다(execution_manifests._auto_chown) — 실행 신원은 owner_username 이 있으면 그 사용자, 없으면 요청자. 소스가 남(root) 소유면 runuser 신원으로 목적지 chown이 불가해 dsync 는 데이터 복사 뒤 Failed(nsync 는 소유 변경만 건너뛰고 Succeeded) 되는 함정을 막는다. dsync/nsync 의 기본 비교(UID·GID·PERM·MTIME, DMS 는 -o 를 넘기지 않음)는 목적지에 이미 있던 같은 경로 항목의 메타데이터도 소스(또는 --chown/--chmod 값)로 다시 맞춘다 — root 실행이면 그 항목 전부가 소스 소유가 된다. 사용자가 chown을 명시하면 개입하지 않는다. 포탈 안내(lib/syncOwnership)가 이 세 갈래의 미러다(2026-10-01) — 이 규칙을 바꾸면 그 문구도 함께 바꿔라. chown 은 **숫자 uid:gid 만** 받는다(2026-10-01 사용자 결정, `domain.chown_problem` 하나로 판정): 이름은 잡 컨테이너(LDAP NSS 없음 — dsync/nsync 의 getpwnam/getgrnam 이 컨테이너 파일만 본다)에서 LDAP 이름은 못 찾아 미리보기가 사유 없는 preview_failed, `users` 같은 이름은 데비안 기본 gid 로 풀려 실행에서 실패/무시, **root 실행이면 실행 신원 이름이 /etc/passwd 의 uid 0 줄로 풀려 목적지가 조용히 root 소유**가 됐다. 그래서 세 곳이 막는다 — 제출 검증(단건·배치 생성·항목 추가/수정/교체 → 422 `chown_name_not_supported`), 이름이 든 옛 배치(확인·재실행·재스캔 → 422 `routes_batches._reject_stale_options` — 종단 배치의 실행 설정 변경으로 숫자로 고치면 다시 돈다, 자식 생성 시점엔 `BatchOrchestrator._materialize` 가 그 항목만 Rejected — 예외를 올리면 run_once 가 모든 배치를 매 틱 막는다), 규칙 전의 대기 잡(stepper `_build_spec` 관문 → `chown_name_at_step` 이벤트 + fail-closed). 이름을 다시 허용하려면 제출 시점에 LDAP 으로 숫자화하는 경로가 먼저다 — 컨테이너에 이름을 넘기는 순간 root 실행의 uid 0 함정이 돌아온다. **보조 그룹(2026-10-07 D7 v1)**: 잡이 LDAP 보조 그룹을 달고 돌아도 자동 주입은 여전히 uid:**주 gid** 다(결과 소유 그룹을 어느 보조 그룹으로 할지 근거가 없다). 프로젝트 그룹 소유로 남기려면 명시 chown `uid:<프로젝트 gid>` — 그 gid 는 **계획 시점**에 실행 신원의 {주 gid} ∪ **적용된** 보조 gid(over_limit·disabled·none 이면 주 gid 만)에 드는지 검증된다(`identity.check_chown_group` → `chown_group_not_member`, 비 root 만). 예전엔 비소속 gid 면 dsync 가 데이터를 다 복사한 뒤 EPERM 으로 Failed 였다. 포탈 chown 그룹 선택 UI 는 없다(BACKLOG).
- worker sshd는 UsePAM=no(물질화 계정은 /etc/shadow 없음→PAM이 거부)·StrictModes=no로 띄운다(execution_manifests._worker_command_script:118-144); /root home은 스킵(Volcano ssh 플러그인이 읽기전용 마운트). launcher는 mpirun 전 artifact_dir을 요청자 소유로 chown해야 도구가 결과 파일을 쓸 수 있다(runner.py:90-95).
- 워커의 보조 그룹은 **/etc/group 줄로만** 들어간다(2026-10-07 D13, `execution_manifests._identity_materialize_stmt` docstring) — sshd 는 로그인 때 initgroups(user, 주 gid)로 /etc/group 을 읽어 rank 의 그룹을 정하므로 파드 supplementalGroups 는 무효다. 물질화 순서가 계약이다: (1) `DMS_JR_SUPP_GIDS` 전체를 **아무것도 쓰기 전에** 검증(숫자·콤마만, 선행 0·11자리 이상·> 2147483647 거부 — "0" 자체는 D3 로 허용) (2) 계정이 이미 있으면(사이트 커스텀 이미지·이미지 계정명 충돌) getent 의 **uid·gid 둘 다** DMS_JR_UID/GID 와 같아야 한다 — 아니면 stderr `dms: account … exists with a different uid/gid` + exit 1(새 사유 코드 없음 → preview_failed/execution_failed; 예전엔 조용히 건너뛰어 sshd 가 **이미지 계정의 신원**으로 rank 를 돌렸다 — root 잡의 owner 이름이 이미지 계정과 겹쳐도 이제 실패한다) (3) passwd 줄은 없을 때만 (4) 합성 이름 `dmsg<gid>` 줄은 (2)·(3)과 무관하게 가드 밖에서 항상·중복 없이(LDAP 그룹명을 싣지 않아 콜론·개행 주입과 이미지 그룹명 충돌이 없다; dscan/dsync 출력의 그룹명이 dmsg<gid> 로 보이는 건 사소) (5) `id -G user` 가 {주 gid} ∪ 목록과 **같은 집합**인지 양방향 확인, stderr `dms: groups=…` 진단. 바깥 가드(root·username·uid)가 거짓인데 목록이 있으면 `elif` 가 실패시킨다(웹훅이 runAsUser 를 바꾼 경우 — preflight 는 그룹을 가진 채 통과했으니 조용히 진행하면 단계 사이 drift). 그룹 관련 실패는 `DMS_EXEC_REASON=identity_groups_not_applied`(실패 워커 파드 로그를 러너 마커와 같은 경로로 승격). 셸은 신원 값을 env 로만 참조한다(스크립트 문자열 보간 금지) — tests/test_worker_identity_materialize_sh.py 가 실 sh(dash)로 고정.
- k8s 예외는 ApiException 타입이 아니라 status 속성 duck-typing으로 판별(execution_volcano.py:419-427) — .venv에 kubernetes 패키지가 없어 테스트가 그 타입을 만들 수 없다. 403은 _log_forbidden으로 반드시 구분 로그(:429) — RBAC 거부가 "객체 없음"과 똑같이 렌더된 사고의 교훈.
- vcjob 로그는 launcher(이름에 -launcher-) 전부 + 그 외 Failed 파드만 모은다(_read_vcjob_logs:269) — 성공 워커 sshd 로그는 노이즈. per-pod 실패는 (pod, None, waiting_reason)으로 접지만 list 호출 자체의 예외는 poll_failed로 던진다(403이 "로그 없음"으로 뭉개지지 않게). 파드 0개는 실패가 아니라 빈 목록.
- KubernetesClient는 in-cluster config를 최초 호출까지 lazy + 이중검사 잠금으로 세 API 핸들을 원자 세팅, _core를 마지막에(_ensure:313-327) — 병렬 observe 시 반쯤 초기화된 핸들 접근 방지.
- 프로브 Pod의 activeDeadlineSeconds는 스케줄 후에만 발화한다(build_manifests.py:162-165) — 영구 Pending 프로브는 워처의 created_at 기반 회수만 잡는다.
- 빌드 프로브·파드에 priorityClassName dms-build(값 10<dms-low 50) — PriorityClass 미적용 클러스터에선 admission 거절이므로 05-volcano-queue-priorityclass.yaml을 먼저 apply(build_manifests.py:167-169,202-205).
- 프로브 실패 시 detail을 마커(DMS_PREFLIGHT_REASON)보다 먼저 출력(build_manifests.py:97-103) — 64KB 로그 꼬리 박제에서 마커가 잘리면 사유가 build_preflight_failed로 뭉개진다.
- 레지스트리가 평문 HTTP라 push --tls-verify=false만으론 부족 — dms-agent가 FROM pkg-01:5000/…를 pull하므로 registries.conf.d에 insecure 등록이 먼저다(build_manifests._SCRIPT:9-13). dms-agent 빌드는 --build-arg로 베이스 태그를 명시 고정 — 없으면 ARG 기본값 :dev로 엉뚱한 베이스에서 조용히 "성공"한다(:28-36).
- nsync는 dsync 파서로 파싱 불가(별개 도구, Items:/bytes 미출력) — Planned actions 합계(블랙리스트 skipped-dst-only 제외)+planned/copied-volume 단위 환산, 모르는 단위면 이전 매치로 물러나지 않고 None(parsers.py:25-89).
- dscan argv는 --output $DMS_SCAN_REPORT 항상 + --print는 quiet가 아닐 때만(--quiet와 상충, execution_manifests.tool_argv). 값 옵션은 batch_files/broken_limit 뿐(dscan 1b93d54 — top-k는 기능 삭제, --broken-limit은 롱네임뿐, 둘 다 0 허용: batch_files 0 = 배칭 끔). $DMS_SCAN_REPORT 경로와 summary가 읽는 경로는 _scan_report_path 한 곳(runner.py:110-114) — 갈라지면 scan 카운트가 조용히 null.
- manifest_tags의 동봉본 경로는 개발 체크아웃(parents[2])→/app/deploy/k8s 폴백(:40-43); 롤아웃 직후 live!=manifest는 정상(포탈 롤아웃은 매니페스트를 안 고침 — 다음 kubectl apply가 되돌릴 위험의 표시가 목적). initContainer 이미지 드리프트는 배지에 안 뜬다 — 계약 테스트(init_container_image)가 그 침묵을 메운다(:217-227).
- StubExecutionAdapter.read_log도 실 어댑터와 같은 3-튜플 (pod, log, waiting_reason) 계약(execution.py:84-87); log=None은 "얻을 수 없었다", 빈 문자열은 정상값(러너가 그 지점까지 못 간 경우 -- 정상 launcher 로그에는 워커마다 DMS_JR_WORKER_READY 줄, 준비 실패 시 DMS_EXEC_REASON=/DMS_JR_WORKER_UNREACHABLE 줄이 있다).
- BuildRunner.failure_reason은 구분 재료가 없으면(파드 GC·조회 실패) 지어내지 않고 build_failed 유지(build_runner.py:114-138) — Evicted는 파드 수준 reason, OOMKilled은 containerStatuses.terminated.reason.
- 워커 준비 대기(runner._wait_workers_ready)는 **전체 공유** 제한(WORKER_READY_TIMEOUT_SECONDS=300, env DMS_JR_WORKER_READY_TIMEOUT_SECONDS 로 덮어씀) 안에서 호스트마다 getent 가 IP 를 줄 때까지 + 그 IP 로 ssh 가 될 때까지 기다리고, 넘기면 **mpirun 을 돌리지 않고** DMS_EXEC_REASON=workers_unreachable 로 끝낸다. 예전의 "워커당 90회 탐침 뒤 조용히 진행"(구 _wait_ssh_ready)을 되살리지 마라 -- 그게 프로덕션 rc 255 간헐 실패의 절반이었다. 제한은 실제 경과 시간이다: main 이 time.monotonic 을 넘기고(테스트가 고정), clock 미지정(테스트)은 sleep 만 세는 가상 시계다.

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
| `src/dms/api/app.py` | create_app: 라우터 26개 조립(2026-10-08 routes_request_purge 포함), SessionMiddleware(쿠키 dms_session), /healthz, /readyz(DB SELECT 1 + 연속 실패 30회 시 exit_fn=SIGTERM 자기종료, 성공 1회면 카운터 리셋), dist 정적 서빙(/assets mount)+spa_fallback(존재하는 파일이면 FileResponse, 아니면 index.html) |
| `src/dms/api/auth.py` | Identity(actor,role,auth) 네임드튜플, current_identity(): Bearer 공유토큰(x-dms-actor는 node:<DNS-1123>만, 빈값→shared-token, 그 외 400) vs 세션(요청마다 계정 disabled 재검사), require_user/require_admin, audit_actor()(token: 접두 표시용) |
| `src/dms/api/routes_auth.py` | signup/login/password-reset/admin accounts — 비밀번호를 받는 네 경로 전부 `_password_from()` 하나로 평문을 얻는다(password_enc 봉인 해제 또는 정책 허용 시 평문; 토큰 부트스트랩만 평문 상시 허용), login 은 검증 **전에** 감속기(429 login_rate_limited+Retry-After, 실패만 계수·성공 시 user 키 clear), GET /api/auth/transport-key(무인증 공개키), logout/me, x-admin-token 부트스트랩(감사 actor=token:admin-token) |
| `src/dms/api/password_transport.py` | 비밀번호 전송 봉인(2026-09-07): DMS_SESSION_SECRET→HKDF→P-256 정적 키(결정적, 레플리카 동일), 브라우저 임시 키와 ECDH→HKDF-SHA256→AES-256-GCM, AAD=`dms-password-transport-v1\|<purpose>\|<username>`, kid 불일치만 key_mismatch 로 구분·나머지는 invalid 한 사유(복호 오라클 방지), seal()/seal_with_info()는 테스트·운영 스크립트용 브라우저 판 |
| `src/dms/api/mailer.py` + `src/dms/mail_config.py` + `src/dms/api/routes_mail_settings.py` | 인증 메일(2026-10-01): 메신저 서버의 Knox 메일 릴레이(knox_mail_dms_certi, POST /send·GET /healthz, Bearer RELAY_TOKEN)를 표준 라이브러리 HTTP 로 부른다(프록시 무시). 설정 해석은 `mail_config.resolve_mail_config` 하나(포탈 mail_settings > env DMS_MAILER_BACKEND·DMS_MAIL_* > 기본, 칸별, 요청마다 DB 재조회). 관리 → 메일 설정(GET/PUT·연결 확인·테스트 메일). 가짜 릴레이 `tests/fake_knox_relay.py`(mailer 가 기대하는 계약의 거울 -- 테스트·테스트베드 실검증용) |
| `src/dms/secret_box.py` + `src/dms/repositories/mail_settings.py` | 저장 비밀 봉인(2026-10-01): DMS_SESSION_SECRET→HKDF(용도별 info)→AES-256-GCM(AAD=용도), `v1:`+b64(nonce\|ct), 못 열면 None(예외 아님). mail_settings 단일 행(id=1, NULL 칸 = env 기본값), 감사 mutation_class `mail_settings` 는 토큰을 "set"/null 과 relay_token_change(set·replaced·cleared)로만 적는다 |
| `src/dms/api/routes_portal.py` | 포탈 서브네임(2026-10-02): `control_state.portal_subtitle` 한 칸(NULL = 없음), GET /api/portal-info(공개 -- 로그인 화면도 그린다)·PUT /api/admin/portal-settings(관리자, 감사 `portal_settings`, 40자·제어/서식 문자 거부). 메인 이름은 프런트 상수(features/portal/usePortal), 사이드바 이름 아래 줄·로그인 화면·탭 제목 "메인 - 서브". 탭 아이콘은 `frontend/public/favicon.svg`(번들 -- airgap) |
| `src/dms/api/login_limiter.py` | LoginRateLimiter: 사용자명·IP 키별 슬라이딩 창 실패 deque(기본 10회/60s), 상한 뒤 요청은 검증 전에 429 이고 실패로 세지 않음(영구 잠금 DoS 방지), record_failure 는 상한 도달 키만 반환(이벤트 1회), attempts/window 0 = 명시적 비활성, 프로세스 메모리(레플리카 N 배 실효) |
| `src/dms/api/routes_request_purge.py` | 작업(요청) 선택 삭제(2026-10-08): `POST /api/admin/requests:delete`(세션 관리자만, 유지보수 503, 1..200건 — 422 empty_selection·delete_selection_too_large, 중복 접기, 부분 성공 deleted[].job_ids/skipped[].reason/purge_pending — 판정은 저장소 delete_terminal), `GET /api/admin/request-purges`(require_admin, 정리 대기 pending·stalled·items — 정리의 유일한 운영 표면). FS·k8s 무접촉. artifact base 잠금(`routes_artifact_base._job_count`)은 잡 + 정리 대기 행 — 정리 대기 중 base 가 force 없이 바뀌지 않게. |
| `src/dms/api/routes_requests.py` | 제출(202; maintenance 503, scan은 admin 전용 403, owner_username 특권 게이트, 422 reason 세분화), 목록(admin은 전체, 행마다 `has_succeeded_scan` — 사용량 분석 지점(성공 scan **잡**) 보유 여부, 작업 삭제 확인 창의 사용량 경고용. 요청 상태가 아니라 잡 상태 기준 — 취소 경합의 「요청 Cancelled · 잡 Succeeded」), 상세(events 101건 조회로 잘림 판별), 취소(전 잡 종단이면 거짓 취소 대신 finalize_from_job 화해 후 409) |
| `src/dms/api/routes_jobs.py` | _owned_request/_owned_job 소유권 검사(비소유는 404로 뭉갬), confirm(fingerprint 대조·preview 만료 처리), job 단위 cancel |
| `src/dms/api/cancel.py` | terminate_job(): 종단이면 no-op, phase_refs 전부 adapter.terminate — 종료 성공 후에만 DB Cancelled(거짓 취소 금지)의 실행부 |
| `src/dms/artifact_files.py` | **API·컨트롤러 공용** 봉쇄 사슬(2026-09-09 root 전환): open_artifact_fd(단일 open O_NOFOLLOW\|O_NONBLOCK→fstat S_ISREG→nlink==1→소유자 st_uid∈{0,요청자}→/proc/self/fd 봉쇄), inode_allowed(목록·열기 동일 판정), job_owner_uid(잡 행의 요청자 uid, bool/str 거부), read_contained_text(컨트롤러 summary 읽기: 크기 상한 1MiB·비-UTF-8 도 None), PHASES/NAME_RE/JOB_ID_RE 화이트리스트 |
| `src/dms/api/artifacts.py` | artifact_files 위의 API 층: 목록(scandir(dfd)+MAX_ENTRIES/MAX_SCAN, inode_allowed 로 하드링크·남의 소유 제외), 뷰(꼬리 MAX_BYTES), 다운로드 스트림(fstat 시점 size 캡), open_artifact_stream(봉쇄 통과 뒤에만 크기 상한), tail_lines는 \n 전용 분할; 라우트는 owner_uid=job_owner_uid(job) 필수 |
| `src/dms/api/routes_artifacts.py` | 잡 아티팩트 목록/뷰(tail)/다운로드(octet-stream+attachment+nosniff), /logs: 라이브 우선 + diag_logs 박제 폴백(빈 문자열은 폴백 조건 아님), 봉쇄 실패·미존재는 동일 404(존재 오라클 차단) |
| `src/dms/api/routes_agent.py` | /api/agent/report: actor==node:<node_name> 일치 검증 후 ingest, 응답에 enabled storages·probe targets·report 주기·artifact_base_path(스킴 제거) 하달 |
| `src/dms/api/routes_storages.py 외 admin 계열(accounts/nodes/policies/denylist/batches/control/artifact_base/builds/releases/metrics)` | 전부 APIRouter(dependencies=[Depends(require_admin)]) 또는 라우트별 require_admin; storages만 user_router(/api/user/storages, require_user) 별도 |
| `frontend/src/lib/api.ts` | REASON_MESSAGES 한국어 매핑+reasonText(prefix:suffix 복합 코드 번역), request(): 오류 파싱 한 벌·비JSON이면 http_<status> 합성·401에만 dms:unauthorized 발화, ApiError(status,code) |
| `frontend/src/lib/reasonCodes.json` | 백엔드가 낼 수 있는 사유 코드의 단일 목록 — 프론트 reasonCodes.test.ts와 백엔드 tests/test_reason_codes_coverage.py가 같은 파일을 읽는 양방향 계약의 축 |
| `frontend/src/app/ (AuthContext.tsx, RequireRole.tsx, router.tsx, queryClient.ts, AppShell.tsx, ErrorBoundary.tsx)` | dms:unauthorized→me invalidate(clear 금지), 역할 게이트 라우팅(/admin/* 15개+user 4개), retry:false·staleTime 5000, ErrorBoundary key={pathname} |
| `frontend/src/features/*/use*.ts` | react-query 폴링 훅: requests 3s, 요청 상세 3s(요청 비종단 동안) + request jobs 2s(요청 비종단 또는 비종단 잡이 있을 때 — 잡 0개여도 돈다, 전부 종단이면 중지 · e2e E6, 데이터 없이 실패하면 「다시 시도」까지 중지 · 요청 404(삭제됐거나 볼 수 없음)면 요청 폴링·포커스 재조회와 잡 조회를 모두 끄고 「없는 요청」 화면), 열어 둔 진행 중 단계 로그 3s(단계가 끝나면 마지막 1회 · 그 외 로그·아티팩트 목록은 폴링 없음 — 목록은 잡 상태 전이 때 같은 키로 다시 읽어 실패해도 이전 목록 유지), dashboard/metrics 5s, batches 4s/상세 2.5s(종단 중지), nodes·artifact-base 10s, builds/releases는 진행 중일 때만, 작업 목록(관리자)의 정리 현황 request-purges 5s(대기 있을 때)/30s. 작업 삭제(`jobs/useJobs.useDeleteRequests`)는 지운 요청·잡 키를 제거하고 목록·정리 현황·사용량·잡 통계·감사·artifact-base 무효화를 **기다린 뒤** 성공한다(결과 문구 시점에 행이 이미 없다) |
| `frontend/e2e/ + frontend/playwright.config.ts` | 풀스택 e2e 6개(01-boot-session~06-request-delete): global-setup이 migrate/api/controller/agent 부팅·시드(실패=throw, skip 금지), :8093 선점 거부, workers:1(단일 sqlite), 시스템 크롬, forbidOnly. artifact base 는 tmp 아래 실디렉터리(api·controller 같은 값 — 작업 삭제 정리 루프가 실제로 연다), api 조용한 창 0초·controller request-purge 1초 |

### 불변식 (위반하면 깨진다)

- 스토리지 사용 범위(2026-09-30)는 `repositories.storages.storage_open_to_users` 하나로 정의한다: enabled=0 완전 비활성(planner `storage_disabled`), enabled=1·user_enabled=0 **관리자 전용**(사용자에게만 비활성), 그 외 전체 사용(user_enabled NULL = 컬럼 이전 행 = 공개, migrate 가 1 로 백필). 관리자 전용은 표시(비관리자 `/api/user/storages` 에서 제외)만이 아니라 제출 게이트(`routes_requests.submit` 403 `storage_admin_only`)·planner(계획 시점 재확인, 계정 역할 기준 — 토큰은 API 가 실제로 만드는 모양(shared-token·node:*)만 관리자, 계정 행 없으면 비관리자)·컨펌 게이트(`routes_jobs.confirm_job` — ConfirmPending 동안 관리자 전용이 된 사용자 잡의 실행 시작 거부)·스캔 경로 등록이 모두 막는다(완전 비활성은 종전대로 진행 중 잡을 막지 않는다) — 새로 스토리지를 고르는 사용자 경로를 만들면 같은 함수를 거쳐라. PUT 의 `user_enabled` 생략은 "현재 값 유지"(옛 클라이언트가 경로만 고쳐 관리자 전용을 조용히 풀지 않게).
- 사용자 sync 허용 스토리지 쌍(2026-09-30, `repositories/sync_pairs.py` 모듈 docstring)은 **기본 전부 불가**다: 비관리자 sync 는 `sync_pairs` 에 (소스, 목적지) 행이 있을 때만 허용되고(방향 있음 — A→B 와 B→A 는 별개, A→A 도 한 쌍), 판정은 `sync_pair_allowed` 하나로 제출(`routes_requests.submit` 403)·planner(`pair_exempt` 아니면 `sync_pair_not_allowed` 거부 — 관리자 계정·API 가 만드는 토큰 모양·**배치 자식**만 면제; 배치 면제는 쌍에만 쓰고 관리자 전용 스토리지 판정(`requester_is_admin`, 계정 역할 기준)으로 번지지 않는다)·컨펌(`routes_jobs.confirm_job`, ConfirmPending 중 해제된 쌍) 세 곳이 강제한다. 포탈 단일 작업 화면의 소스·목적지 상호 필터(`/api/user/sync-pairs` → `lib/syncPairs.syncChoices`)는 표시일 뿐이다. 스토리지 삭제는 같은 트랜잭션에서 그 스토리지가 낀 쌍을 지운다(같은 이름 재등록이 옛 허용을 물려받지 않게) — 사용자 sync 를 받는 새 경로를 만들면 같은 함수를 거쳐라. 이 테이블이 비어 있는 채로 올리면 사용자 sync 가 전부 거부된다(업그레이드 주의, deploy/README §6).
- 배치 실행 설정(동시 실행 상한·우선순위·노드 수·노드당 프로세스 수·연산 옵션)은 **종단(Completed/Cancelled) 배치에서만** 바뀐다(2026-10-02, `routes_batches.patch_batch_execution` → `BatchesRepository.update_execution_settings` 의 SQL 가드 `WHERE status IN 종단` + 같은 트랜잭션의 audit_log `batch/execution_settings`). 활성·PreviewReady 는 409 `batch_settings_locked`: 자식은 materialize 시점에 배치 행을 읽어 payload 에 굳히므로(`BatchOrchestrator._materialize`) 도는 중에 바꾸면 "일부 옛값·일부 새값"이 된다 — 종단이면 살아 있는 자식이 없고 바뀐 값은 다음 재실행의 자식부터 적용된다. 이를 위해 orchestrator 는 틱 스냅샷이 아니라 **굴리기 직전에 다시 읽은 배치 행**으로 자식을 만든다(`_drive` 재확인 — 스냅샷 뒤 취소→설정 변경→재실행이 끝나면 옛 값으로 만들던 창, 적대적 리뷰 재현). 실행 설정 변경은 생성과 같은 특권 3중 게이트(`_require_batch_privilege`)·같은 검증(`validate_batch_controls`·`validate_options`)을 거친다. 실행 신원(owner_username)·연산·항목은 이 변경의 대상이 아니다(신원·특권 재료는 생성 시점 사실). options 는 통째 교체이고, 포탈 다이얼로그(`BatchExecutionSettingsDialog`)의 옵션 키 집합은 `domain._OPTION_SPECS` 와 같다(`test_batch_execution_dialog_contract`) — 그 밖의 저장 키는 서버가 거부하는 옛 옵션이라 "저장하면 빠진다"고 보여 주고 뺀다. 새 옵션을 추가하면 다이얼로그에도 넣어라.
- 배치 재검토·확인 게이트(2026-10-07): **sync 배치 자식은 운영자가 배치 확인으로 본 미리보기만 실행된다.**
  - 재검토: sync 배치에 Queued 를 새로 만드는 모든 경로(전체·실패분·선택 재실행, 항목 추가, 활성 배치의 Queued 항목 수정)는 새 Queued 커밋 **뒤** 배치를 Previewing 으로 되돌린다(CAS — `routes_batches._reopen_after_new_queued`). 종단뿐 아니라 Running·PreviewReady 도. Running 에서 되돌리면 실행 중(Executing) 자식은 끝까지 돌고(슬롯은 차지하되 확인 대기 전이는 막지 않는다), 확인받고 슬롯을 기다리던 자식은 재확인까지 멈춘다.
  - 확인 대기: orchestrator 의 Previewing → PreviewReady 는 Queued·미리보기 단계 자식이 없고 확인할 미리보기가 있을 때만(`BatchesRepository.mark_preview_ready`, CAS) 이고 그때마다 **확인 회차**(`batches.preview_round`, NULL = 0)를 +1 한다. 확인할 것 없이 이미 확인된 실행 중 자식만 남았으면 Running 으로 돌린다. 확인 대기 배치도 루프가 **기록만** 하러 돈다(`list_awaiting_confirm` — 그 사이 끝난 자식을 항목에 남기고 전부 끝나면 완료; 만들기·컨펌·전이 없음).
  - 확인(`:confirm` → `BatchesRepository.confirm`): 본문의 회차(대화상자를 연 회차, 없으면 422 `preview_round_required`)·`status='PreviewReady'`·Queued 없음을 한 문장으로 CAS(다르면 409 `batch_preview_changed`/`batch_not_confirmable` — 확인 대기 → 다시 미리보기 → 확인 대기(ABA) 사이에 생긴 항목이 확인에 묻지 않게) + 같은 트랜잭션에서 그 순간 ConfirmPending 자식 잡에 **확인 도장**(`confirmed_fingerprint = preview_fingerprint`) + audit_log `batch/confirm`(확인자·회차·도장 수·옵션).
  - 실행: Running sync 분기는 **도장이 지금 미리보기 지문과 같은 자식만** 실행한다. Queued·미리보기 단계 자식·도장 없는(또는 지문이 바뀐) 미리보기가 보이면 아무것도 컨펌하지 않고 배치를 Previewing 으로 되돌린다 — 라우트 복귀를 놓친 경합과 업그레이드 전 옛 코드가 Running 중에 만든 미리보기까지 막는 둑이다.
  - 미리보기 만료: 배치 자식도 stepper `expire_previews` 가 Rejected(preview_expired)로 끝내고 항목은 실패로 집계된다(자동 재미리보기 없음 — 재실행으로 다시 미리보기). Running 은 만료된 미리보기를 컨펌하지 않는다.
  - 자식 생성: 요청 INSERT 와 항목 claim(`claim_queued_item` — Queued + 같은 저장 payload)이 한 트랜잭션이고, 생성 시점 재검증 거부(`reject_queued_item`)도 같은 payload 가드다(스냅샷 뒤 고친 항목을 옛 경로로 만들거나 옛 사유로 거부하던 창).
  - 특권: **새 경로를 실행시키는 배치 라우트 전부**(항목 추가·수정·교체, 선택·실패분·전체 재실행, 확인)는 생성과 같은 특권 3중 게이트(`_require_batch_privilege`)를 다시 통과한다 — 자식은 배치 행의 세션 인증·요청자를 물려받아 root 로 돈다. 실행을 줄이기만 하는 라우트(항목 삭제·취소·배치 기록 삭제·메타 수정)는 require_admin — 배치 화면의 「배치 삭제」(`DELETE /api/admin/batches/{id}`)는 **배치 기록만** 지우고 자식 작업은 남긴다. 작업까지 지우는 배치 단위 삭제는 `POST /api/admin/requests:delete` 의 batches(세션 관리자, 아래 경계)이고 배치 기록도 함께 지운다 — 기록만 지운 뒤 남은 자식 묶음(배치 행 없는 batch_id)도 같은 길로 지운다. 배치 자식은 단건 컨펌(`POST /api/user/jobs/{id}:confirm`)으로 실행을 시작할 수 없다(409 `batch_child_confirm_via_batch`).
- 공유 토큰과 특권 이름의 경계(2026-10-07): 공유 Bearer 토큰(모든 노드 에이전트가 보유, role admin)은 **계정 변경**(생성·삭제·역할·비활성화 — 403 `accounts_session_required`)과 **배포 경로**(빌드 제출·삭제, 릴리스, 컨트롤 상태 변경, 레지스트리 태그 삭제, artifact base 변경 — `auth.require_session_admin`, 403 `admin_session_required`)와 **작업(요청) 삭제**(`POST /api/admin/requests:delete` — 단건과 배치 단위(본문 batches, 2026-10-10) 모두, 같은 403 — 컨펌·취소 기록과 root 실행 산출물을 없애는 증거 삭제라, 2026-10-08)를 할 수 없다. 토큰으로 allowlist 이름의 계정을 다시 만들거나 릴리스로 수정 전 이미지로 롤백하면 위 게이트가 통째로 무효가 됐다. 조회(GET)·스토리지·정책 등 그 밖의 관리 경로는 토큰도 된다. 첫 관리자 부트스트랩은 별도 비밀 x-admin-token. 또 특권 목록(`DMS_PRIVILEGED_REQUESTERS`) 이름의 계정은 **특권 세션 관리자만** 만들고·역할을 바꾸고·끄고·지운다(`routes_accounts.guard_privileged_account`, 403 `privileged_account_protected`) — 목록은 관리자 사이의 경계라, 목록 밖 관리자가 "root" 계정을 만들어 그 세션으로 root 자격을 얻던 길을 막는다(특권 실행이 꺼져 있으면 가드 없음). 같은 이유로 목록 이름의 계정은 셀프 비밀번호 재설정(메일 인증번호)을 쓰지 않는다(`routes_auth._refuse_privileged_reset` — 메일 경로는 아무 세션 관리자나 바꿀 수 있다). 단 이 경계는 심층 방어다: 세션 관리자는 누구나 빌드·릴리스를 할 수 있다.
- reasonCodes.json 단일 파일 양방향 계약: 백엔드에 새 detail=/reason_code= 리터럴을 추가하면 frontend/src/lib/reasonCodes.json과 api.ts REASON_MESSAGES를 같은 커밋에 갱신해야 한다 — reasonCodes.test.ts(전 코드 매핑+죽은 키 금지)와 tests/test_reason_codes_coverage.py(src/dms AST 추출)가 같은 JSON을 대조한다
- 비밀번호는 평문으로 저장·전송되지 않는다(2026-09-07): 저장은 accounts.py `_hash_password`(scrypt) 한 곳, 전송은 프런트 `postWithSealedPassword`(passwordTransport.ts) ↔ 백엔드 `_password_from`(routes_auth.py) 한 쌍 — 비밀번호를 받는 새 엔드포인트/훅은 반드시 이 두 통로를 거친다(우회하면 그 경로만 평문이 되고 아무 테스트도 빨간불이 아니다; test_api_auth_hardening 이 현재 네 경로를 전수 고정). 라이브(from_env)는 `password_encryption_required=True` 로 평문 422 거절, dataclass 직접 생성(테스트)은 False — account_verification_required 와 같은 두 층
- 인증 메일(2026-10-01, api/mailer.py·routes_auth.request_verification_code): 인증번호는 **HTML 본문에만** 들어간다(제목·프리헤더·observability 이벤트·예외 메시지·응답 금지 -- 릴레이 로그와 이벤트가 그것들을 남긴다; stub 의 stub_code 에코만 예외). knox_relay 의 수신자별 상한(5통/10분, app.state.mail_throttle)은 코드를 **발급하기 전에** 검사한다(발급 뒤에 막으면 새 코드가 메일함의 코드를 죽인다). 발송 실패는 502 `verification_email_failed` -- 보낸 척하지 않는다. 미지 발송 방식(env 오타)은 코드 발급 전에 500 `mailer_misconfigured`.
- 릴레이 토큰(RELAY_TOKEN)은 비밀번호와 같은 봉인 통로로만 받고(password_transport PURPOSES 의 `mail_relay_token`, AAD 사용자 자리 `mail_settings`) DB 에는 `secret_box` 봉인(세션 시크릿 HKDF, AES-GCM)으로만 둔다 -- 응답·감사 로그(relay_token="set")·이벤트에 값도 봉인도 싣지 않는다. 세션 시크릿을 바꾸면 봉인이 안 열린다(token_unreadable -- 화면이 "다시 입력"으로 알리고 발송은 relay_misconfigured 로 실패, 빈 토큰으로 보내지 않는다).
- 릴레이 토큰은 **주소에 묶인다**(2026-10-01 리뷰): 관리자 권한만으로 릴레이 주소를 자기 서버로 돌리고 테스트 메일로 Bearer 토큰을 받아내는 경로를 막는 세 겹 -- (1) PUT 이 해석 주소(`resolve_from_row` 의 relay_url)를 바꾸는데 포탈 토큰이 있고 새 토큰도 지우기도 없으면 422 `mail_relay_token_required`, (2) env 토큰(DMS_MAIL_RELAY_TOKEN)은 주소가 env URL 그대로일 때만 쓴다(포탈이 주소 칸을 하나라도 정하면 `env_token_unbound`), (3) 릴레이 HTTP 는 프록시 무시 + **리다이렉트를 따라가지 않는다**(mailer `_NoRedirect` -- 3xx 는 relay_http_3xx 실패). 설정 변경·연결 확인·테스트 메일은 **세션 관리자만**(403 `mail_settings_session_required` -- 공유 토큰·node: actor 는 조회만). 이 셋 중 하나를 풀면 토큰 유출 경로가 다시 열린다. 검사는 저장 트랜잭션 안에서 잠근 행으로 하고(`MailSettingsRepository.update(guard=)`, PG 는 FOR UPDATE), 키를 싣는 저장에는 포탈이 화면이 보던 적용 주소(`seen_relay_url`)를 실어 그 사이 다른 관리자가 주소를 바꿨으면 409 `mail_settings_changed` -- 오래된 탭의 "키만 저장"이 진짜 키를 본 적 없는 주소에 묶지 않게.
- 인증 메일 엔드포인트는 무인증이라 수신자 상한(5/10분) 말고도 클라이언트 IP(20/10분)·전체(300/10분) 상한과 동시 발송 슬롯(8, `app.state.mail_send_slots` 비차단 획득 -- 못 얻으면 429 Retry-After 10)이 knox_relay 경로를 감싼다(모두 프로세스 메모리, 레플리카 N 배 실효). 슬롯을 먼저 잡고, 세 상한은 `mail_throttle_lock` 아래 `SendThrottle.peek` 으로 **모두 통과할 때만 모두 `record`** 한다 -- 거절된 요청이 앞 상한(특히 남의 수신자 몫)을 태우면 자기 IP 상한을 다 쓴 공격자가 메일 없이 임의의 사용자 재설정을 막는다(2026-10-01 재리뷰). 거절 이벤트(`verification_email_throttled`)는 (상한, 키)마다 창에 한 번(`mail_throttle_notes`) -- 무인증 거절마다 행을 쓰면 events 가 무제한으로 자란다. 인증번호는 **발송 성공 뒤에만** 저장한다(`new_verification_code` → 발송 → `issue_verification_code(code=)`) -- 실패한 발송이 메일함에 있는 이전 코드를 죽이지 않게. 단 요청은 나갔고 응답만 못 받은 경우(`relay_no_response` -- urllib 이 URLError 로 감싸지 않는 응답 대기·읽기 단계의 타임아웃·끊김)는 갔을 수 있으니 저장하고 200 + `delivery_uncertain`. 상대가 HTTP 가 아닌 것을 답한 경우(BadStatusLine·LineTooLong·UnknownProtocol, RemoteDisconnected 제외 -- `mailer._NOT_HTTP`)는 릴레이가 아니므로 `relay_bad_response`(502, 저장 안 함) -- 이 분류를 넓히면 포트를 잘못 넣은 설정이 "보낸 척"이 된다.
- 4자리 인증번호의 무차별 대입 상한은 **세 층**이다(2026-10-01 재리뷰): 코드당 `VERIFICATION_MAX_ATTEMPTS`(5), 재발급으로 초기화되지 않는 (username, purpose)별 누적 `VERIFICATION_FAILURE_LIMIT`(10회/첫 실패부터 24시간, 테이블 `verification_failures`, `repositories/accounts.py`), 그리고 여러 계정에 나눠 던지는 추측을 막는 접속 IP 별 `VERIFICATION_GUESS_PER_CLIENT_LIMIT`(틀린 코드 20회/24시간, `routes_auth._consume_code`, `app.state.verification_guess_limiter` -- 로그인 감속기와 같은 LoginRateLimiter, 프로세스 메모리). 아이디 누적 상한이면 발급과 소비 모두 429 `verification_locked`, IP 상한이면 소비가 429 `verification_client_locked` -- 둘 다 맞는 코드여도. 코드당 상한만 두면 "5번 틀리고 재발급"으로 하루 ~30%, 아이디별 상한만 두면 한 IP 가 계정을 돌며 하루 ~1.4 계정을 맞힌다. signup·password-reset 의 코드 소비는 `_consume_code` 하나로만 한다(새 소비 경로가 이걸 우회하면 IP 상한이 빠진다). 성공한 소비만 아이디 누적을 지운다(IP 누적은 지우지 않는다) -- 재발급·만료 정리 경로에 그 행을 지우는 코드를 넣지 마라. 발송 방식 stub 은 응답에 stub_code 를 돌려줘 아이디만 알면 누구나 재설정할 수 있다 -- 운영은 `DMS_MAILER_BACKEND=knox_relay`, 포탈의 stub 전환은 확인 체크 뒤에만 저장된다.
- 봉인 상수(HKDF salt/info·AAD 접두·버전·곡선)는 password_transport.py 와 passwordTransport.ts 가 바이트 단위로 동일해야 한다(test_password_transport.test_constants_mirror_the_frontend_module 가 원문 대조) — 한쪽만 바꾸면 전 로그인이 password_encryption_invalid
- 로그인 감속은 비밀번호 검증 **전에** 판정하고, 거절(429)된 시도는 실패로 세지 않는다(login_limiter.py docstring) — 뒤집으면 검증이 오라클이 되거나 공격자가 1분에 한 번 찔러 계정을 영구히 잠근다(DoS); 봉인 오류(422)도 실패로 세지 않는다(추측이 아니다)
- 클라이언트 IP 는 X-Real-IP → X-Forwarded-For **마지막** 항목 → 소켓 순(auth.py client_ip) — XFF 첫 항목은 클라이언트가 심을 수 있다(ingress-nginx 는 덧붙인다); IP 키는 보조 축이고 사용자명 키가 주 방어
- 422 검증 오류 응답은 `type/loc/msg` 만 싣는다(app.py RequestValidationError 핸들러) — FastAPI 기본의 `input` 에코는 비밀번호 필드가 실린 형식 오류 요청의 평문을 응답(프록시 로그)에 되돌린다
- Identity.auth에 기본값 없음(auth.py:27) — 새 생성 지점이 필드를 빠뜨리면 조용한 session 오분류 대신 TypeError로 즉시 터지는 것이 의도; Identity.actor는 절대 변형 금지(에이전트 인증·특권 판정·소유권 검사가 원값 비교), 표시용은 audit_actor()만(auth.py:30)
- _NODE_NAME_RE 정의는 auth.py:15 한 곳 — routes_agent.py:6이 import; 두 곳으로 갈라지면 토큰 게이트가 통과시킨 node:<이름>을 ingest_report가 거절한다
- 아티팩트 봉쇄 사슬은 artifact_files.open_artifact_fd 하나(API 의 open_artifact_stream/read_artifact/list_artifacts 와 컨트롤러의 wiring.build_summary_reader 가 공유) — 검사 순서 계약: 단일 open(O_NOFOLLOW|O_NONBLOCK)→fstat S_ISREG→nlink==1→소유자(st_uid∈{0, 요청자 uid})→fd 봉쇄(assert_contained)→그 뒤에만 크기 상한(순서가 바뀌면 404/413 갈림이 존재·크기 오라클). 라우트는 owner_uid=job_owner_uid(job) 를 반드시 넘긴다(None → fail-closed 404)
- artifact_not_found/artifact_forbidden은 뷰·다운로드 라우트 모두 body까지 동일한 404(routes_artifacts.py:44-49, 67-70) — 라우트 간 응답이 갈리면 그 차이가 존재 오라클
- 취소는 terminate 성공 후에만 DB를 Cancelled로 기록(cancel.py 모듈 docstring; routes_requests.py:140-174) — 전 잡 종단+요청 비종단 창에서는 취소 기록 대신 finalize_from_job(orphan_recovery)으로 화해 후 409(거짓 취소 금지)
- dms:unauthorized 이벤트는 401 전용(api.ts:216-218) — 403 등에서 발화하면 권한 없는 화면마다 로그인으로 튕긴다; AuthContext는 me를 invalidate만 한다(clear는 pending 리셋→401 무한 루프, AuthContext.tsx:5-9)
- 'token:' 접두는 감사 actor 예약 네임스페이스(auth.py:9, routes_auth.py:60-67) — 사용자명에 ':' 금지가 전제라 어떤 셀프가입도 이 값에 도달 불가
- /readyz 503 본문 {"status":"degraded"} 및 상태 코드는 프로브 계약으로 불변(app.py:84-85); 연속 실패 카운터는 성공 1회에 리셋(app.py:86), limit(기본 30)에서 exit_fn 발화
- spa_fallback은 라우터 include 뒤에 등록(app.py:92-119) — /api·/healthz·/docs가 먼저 매칭된다는 순서가 SPA 서빙의 전제; static_root 밖 normpath 결과는 index.html 폴백

### root 제어면 (2026-09-09 — 위반하면 취약점이 된다)

dms-api/dms-controller 는 uid 0 이다(40-api.yaml·41-controller.yaml 컨테이너 securityContext: runAsUser 0, capabilities drop ALL, readOnlyRootFilesystem, allowPrivilegeEscalation false; migrate initContainer·30-migrate-job·Dockerfile USER 65532 는 그대로 — tests/test_release_manifest_contract.py 가 이 모양을 고정). 이유: 운영 아티팩트 base 가 root:root 라 65532 로는 3홉 쓰기 왕복이 항상 실패했다. 그 대가로 아래가 **유일한** 방어다:

1. **파일시스템 권한은 2차 방어가 아니다.** 인가는 DB(`_owned_job` routes_jobs.py, `require_admin` auth.py, scan_paths `get_owned`+covers)와 코드 봉쇄뿐. "커널이 거부할 것"을 근거로 검사를 생략하지 마라. (cap 을 버려 남의 0600 은 여전히 EACCES 지만 root 소유 파일은 mode 와 무관하게 열린다 — 이 차이에 기대는 코드도 쓰지 마라.)
2. **artifact base 아래를 여는 모든 코드(api·controller)는 artifact_files.open_artifact_fd 사슬을 쓴다**: 단일 open(O_RDONLY|O_NOFOLLOW|O_NONBLOCK) → fstat S_ISREG → nlink==1 → 소유자 st_uid∈{0, 요청자 uid} → realpath(/proc/self/fd/N) 봉쇄 → 크기 상한 → 모든 OSError/ValueError 를 artifact_not_found/None 으로. 경로 문자열을 두 번 해석하지 않는다. 새 읽기 라우트는 open_artifact_stream/read_artifact/list_artifacts 에 owner_uid=job_owner_uid(job) 를 넘기고 그 앞에 _owned_job 또는 require_admin 을 둔다. DB 행의 artifact_uri 를 그대로 열지 말고 job_id 에서 재조립한다(JOB_ID_RE/PHASES/NAME_RE).
3. **소유자 검사가 0 을 함께 허용하는 이유**는 러너가 chown **뒤에** root 로 stdout.log/stderr.log/summary.json 을 쓰기 때문(dms_job_runner/runner.py) — 러너가 그 셋을 요청자로 chown 하면 `== 요청자` 로 좁힌다(BACKLOG). st_uid==0 만 허용하는 게이트는 쓰지 마라(요청자가 미리 만든 정규 파일을 root 의 open("w") 가 재사용하면 요청자 소유).
4. **root 가 요청자 소유 디렉터리(<base>/<job_id>/<phase>, chown 이후)에 경로로 open("w")/os.chmod/os.path.exists 를 하지 않는다.** 쓰기는 dirfd + O_CREAT|O_EXCL|O_NOFOLLOW, 또는 chown 하지 않는 root 소유 디렉터리에(현재 제어면엔 그런 쓰기가 0건 — 러너의 write_text 가 남은 결함, BACKLOG).
5. **<base> 와 <base>/<job_id> 는 요청자 쓰기 불가**(root:root·비-world-writable·**비-group-writable**·POSIX ACL 쓰기 항목·default ACL 없음; 러너는 <job_id> 를 root 로 만들고 <phase> 만 chown). 깨지면 assert_contained 의 기준(realpath(<base>/<job_id>))을 요청자가 심링크로 옮길 수 있다. 2026-10-07 부터 비 root 잡은 실행 신원의 LDAP 보조 그룹을 달고 돈다(§8) — 그래서 base 의 g+w 는 **그 그룹의 모든 요청자**에게 base 쓰기를 주는 world-writable 과 같은 공격이고, 보조 gid 0 도 인정하므로(D3) root 그룹 g+w 도 안전하지 않아 **gid 와 무관하게** 금지다(D6). base 의 **부모(공용 디렉터리)는 그룹 쓰기·other 통과 금지 — root:root 750(그런 LDAP 그룹이 있으면 700)**: D3 로 보조 gid 0 이 허용되므로 770 은 gidNumber 0 LDAP 그룹 멤버의 잡에 base 옆 생성 권한(root 그룹 rwx)을 준다(planner 가 그런 잡에 warning 이벤트 `identity_groups_root_group`). 부모에 other-x(711·755)도 금지다 — 요청자가 부모를 통과하면 base(755)·0755 잡 디렉터리·0644 아티팩트를 직접 읽어 API 소유자 검사(artifact_files.open_artifact_fd)를 우회한다(711/755 는 base 자체의 선택지; 2026-10-08 리뷰로 부모 711 권고 제거). 잡 파드는 base 를 전용 볼륨(artifact_base.ARTIFACT_MOUNT, execution_volcano._volumes)으로 받아 요청자 uid 도구가 그 부모를 통과하지 않는다(d129). 그러나 **base 자체는 other 실행(x)이 있어야 한다(711/755; 700/710/750/770 금지)** — 마운트 루트의 mode 는 커널이 그대로 보고, launcher 의 mpirun 은 **보조 그룹 없이**(root → 사용자 전환 전) <phase> 의 mpi-hostfile 을 읽는다(D12 — launcher /etc/group 대칭은 하지 않았다). 그룹으로 여는 750/710 은 그룹을 가진 preflight·rank 는 통과하고 launcher 쪽에서 "unable to open the hostfile" 로 죽는다. **제어면 강제**(`artifact_base.roundtrip_artifact_base`: g+w(gid 무관)·POSIX ACL 쓰기 named 항목·default ACL → `artifact_base_group_writable`, o+x 없음 → `artifact_base_not_traversable`)는 **저장(PUT/validate 422)·3홉 표시용**이고 잡 제출을 막지 않는다(stepper·planner 는 artifact_base_check_ok 를 읽지 않는다). **잡 단위 차단은 둘**: (a) 보조 그룹이 실린 비 root 잡은 매 제출 직전 컨트롤러가 같은 규칙(`artifact_base.static_base_problem` — g+w·POSIX ACL·default ACL·o+x, 쓰기 프로브 없음)을 base 에 직접 적용해 종단한다(stepper `ArtifactBaseUnsafeAtStep`; env 로 준 base·저장 뒤 바뀐 mode 도 여기서 막힌다. 컨트롤러가 base 를 stat 하지 못하면 (b)에 맡긴다 — 통과로 치지 않는다). default ACL 은 (b)가 못 보는 경로라 (a)가 유일한 잡 단위 차단이다(2026-10-08 리뷰: base 엔 쓰기가 없고 default ACL 만 있으면 러너가 root 로 만드는 <job_id>/<phase> 가 상속해 그 그룹의 다른 사용자가 rank.sh 를 바꿔치기할 수 있었다). (b) preflight: 모든 비 root 잡은 실행 신원 uid 로 `test -x`(`_ARTIFACT_BASE_CHECK`), 보조 그룹이 있는 잡은 other-x **비트**(`_ARTIFACT_BASE_OTHER_X_CHECK` — 그룹 x 로 거짓 통과 방지)와 `test -w` 쓰기 불가(`_ARTIFACT_BASE_NOT_WRITABLE_CHECK` → `artifact_base_group_writable`; access(2) 라 base **자체**에 걸린 NFSv4/GPFS ACL 도 반영 — 그런 ACL 의 **상속**(inheritable ACE)은 어느 쪽도 보지 못하는 남는 위험이다)까지(2026-09-30 감사 — README 의 옛 "권장 700" 은 비 root 잡을 전부 깨뜨리는 권고였고, 옛 "그룹이 모든 요청자의 주 gid 면 750/770 도 통한다" 는 이제 거부된다). 사용자 데이터 root(storages.managed_root)는 공용 디렉터리 아래에 두지 않는다 — preflight 가 요청자 uid 로 검사한다.
6. **하드링크는 코드로 못 막는 부분이 있다** — fs.protected_hardlinks=1(+symlinks) 은 공유 FS 를 마운트하고 사용자가 link(2) 를 부를 수 있는 모든 호스트의 배포 전제(deploy/README §2b). 코드 측 보완은 규칙 2 의 nlink/소유자.
7. **api/controller 는 셸을 띄우거나 os.access 를 쓰지 않는다.** 권한 질문은 fd 에 연산을 시도해서 답한다(roundtrip_artifact_base). 요청자 관점의 mode·ACL 은 `os.stat`·`os.getxattr`(system.posix_acl_access/default **읽기**만, `_posix_acl_problem` — ENODATA/ENOTSUP 만 "ACL 없음", 그 밖의 OSError·형식 오류는 fail-closed)로 직접 본다 — 2026-10-07 보조 그룹 이후에도 셸·os.access·chown·chmod 0건. 프로세스가 필요하면 파드 스펙 + env(스크립트 본문에 사용자 값 보간 금지). 현재 src/dms(agent 제외)에 subprocess/shutil/tempfile/os.access/chown/chmod 0건 — 유지(2026-10-08 부터 `tests/test_control_plane_static.py` 가 AST 로 고정 — 파드 스펙의 셸 문자열은 코드가 아니다).
8. **관리자·사용자 제공 경로는 (1) 역할 게이트 (2) prefix allowlist (3) realpath 아래 검사 (4) 소유자·mode** 넷 다(routes_artifact_base.py `_check_path` = allowlist_reason → roundtrip_artifact_base(소유자 == euid, o+w 거부, g+w 거부(gid 무관), POSIX ACL 쓰기 named 항목·default ACL 거부, o+x 없음 거부, 그 뒤 쓰기 왕복 — 거부는 모두 프로브 **앞**이라 거부된 경로엔 프로브 파일을 만들지 않는다; 순서는 쓰기 노출(g+w → ACL) 먼저·통과(o+x) 다음이라 둘 다 어기면 더 위험한 쪽을 보인다); controller_check_once 도 같은 순서). root 면 allowlist 아래 어떤 디렉터리에도 프로브를 쓸 수 있으므로 (4) 가 "base 는 root:root 비-world-writable" 전제를 실제로 강제한다. `realpath != path` 거부는 쓰지 마라(base 접두 심링크는 의도적 허용). allowlist 파서는 `..` 성분을 정규화 **전**에 거부한다(normpath 가 `/cephfs/..` 를 `/` 로 접어 무제한이 된다).
9. **uid/gid 를 0 으로 기본값 처리하지 않는다 — 부재는 거부.** stepper._build_spec 의 identity_problem 가드(identity_missing_at_step; 음수·비-dict worker_pool 포함)가 execution_manifests 의 0/root 기본값을 도달 불능으로 만든다. uid 0 은 privileged 플래그와 짝이 맞으면 정당(특권 요청자); 디렉터리가 비특권 사용자에게 uid 0 을 주면 계획 시점에 identity_root_without_privilege(identity.py) 로 거부 — stepper 가드는 변조 행 백스톱이다. 2026-10-07(D5): **주 gid 0** 도 같다 — 비특권 경로의 주 gid 0 은 계획 시점 `identity_root_group_without_privilege`(identity.resolve_job_identity), 규칙 전에 계획된 잡·변조 행은 stepper 백스톱(identity_problem `root_group_without_privilege` → identity_missing_at_step). 보조 gid 0 은 D3 로 **인정**한다(사용자 승인 — 위 불변식 5 의 부모 750/700 이 그 대가). **보조 gid 키 부재·None = [] 는 의도적 예외다**(배포 전에 계획된 잡 = 주 gid 만 = 더 좁은 권한) — "강화"한답시고 부재를 거부로 바꾸면 배포 시점에 진행 중이던 잡이 전부 identity_missing_at_step 으로 끊긴다. 0 이나 다른 값으로 채우지도 않는다.
10. **artifact-base 3홉 "정상"의 뜻**: 쓰기 왕복은 세 홉 모두 uid 0 관점 — 마운트 존재 + rw + root_squash/EROFS/ENOSPC 아님. 여기에 API·컨트롤러 홉은 2026-10-07 부터 요청자 관점의 **mode·POSIX ACL 판정**(소유자 root·o+w 없음·g+w 없음(gid 무관)·POSIX ACL 쓰기 named 항목·default ACL 없음·o+x 있음)을 더한다 — "정상" = 그 base 가 비 root 잡에 통과 가능하고 요청자(보조 그룹 포함)에게 mode 비트·POSIX ACL 로는 쓰기를 주지 않는다. 그래도 어떤 비root uid 의 실제 접근을 **증명**하지는 않는다(NFSv4/GPFS ACL·root_squash 류 서버 판정은 못 본다) — 잡 단위의 진짜 판정은 preflight 가 실행 신원으로 하고(`test -x`, 그룹 잡은 other-x 비트 + `test -w`), 그룹 잡은 컨트롤러가 제출 직전 같은 mode·POSIX ACL 규칙을 직접 적용한다(`static_base_problem` — 불변식 5). 노드 홉(에이전트)은 종전 그대로다. 예전 README 가 허용하던 750/710 base 도 이제 빨간불(`artifact_base_not_traversable`)이다.
11. **이미지 fs 는 이미지 빌드만 채운다.** /app/static·/app/deploy/k8s·site-packages 는 root:root 라 uid 0 이 쓸 수 있다 — readOnlyRootFilesystem 이 유일한 보호. DMS_STATIC_DIR 을 hostPath/emptyDir/공유 FS 로 돌리지 마라(spa_fallback 은 문자열 prefix 봉쇄라 트리가 이미지 고정일 때만 안전).
12. **securityContext 를 "강화"한답시고 runAsNonRoot/runAsUser 65532 를 (오버레이 포함) 넣지 마라** — 운영 base 에서 즉시 artifact_base_not_writable 회귀. 반대로 capabilities 를 더하면(DAC_OVERRIDE) 남의 mode 비트 게이트까지 사라진다 — 주면 사유를 주석에. 요청 삭제의 **purge 파드**(purge_runner.build_purge_pod, 2026-10-08)는 제어면이 아니다: 제어면 컨테이너에 cap 을 더하지 않으려고 삭제를 단명 파드로 뺐고, 그 파드는 root + DAC_OVERRIDE·FOWNER 만(근거는 빌더 docstring), base 볼륨 하나(스토리지 무마운트), SA 토큰 없음, 고정 스크립트 + positional 이름 — 이미 도는 잡 launcher(root + 기본 cap 전부 + base·스토리지 rw)보다 엄격히 약하다(`tests/test_purge_pod_manifest.py` 가 모양을 고정).
13. **컨트롤러 루프는 파일시스템에서 아무것도 삭제하지 않는다**(pod_gc/retention 은 k8s API + SQL). 2026-10-08 개정: 컨트롤러의 **유일한** FS 변경은 request-purge 의 `.dms-trash` 생성(mkdirat 0700)과 `<base>/<job_id>` → `.dms-trash/<job_id>` renameat(base 를 fd 로 열어 fstat 검사 — 소유자 euid·g+w/o+w 없음, trash 는 O_NOFOLLOW·0700·같은 st_dev, `<job_id>` 는 euid 소유 디렉터리만 — dir_fd 상대, `<phase>` 안으로 들어가지 않음, artifact_trash.py)뿐이고, 실제 삭제는 purge 파드(규칙 12)가 한다 — cap 0 인 제어면 root 는 요청자 소유 0755 `<phase>` 를 지울 수 없다(EACCES). 정리 모듈에 unlink·rmdir·shutil 0건(`tests/test_control_plane_static.py`). 아티팩트 보존 정책(자동 만료)을 넣으면 같은 길(떼어냄 + purge 파드).
14. **admin 은 신뢰 앵커다.** admin 은 이미 특권 잡·빌드 소스 경로·privileged 빌드 파드로 클러스터 root 상당 — root 전환 후 admin 이 요청자 잡의 root 소유 아티팩트를 읽는 것은 의도된 동작.
15. **에이전트는 호스트 `/` 를 한 번만 마운트한다**(2026-09-16). DaemonSet 이 호스트 `/` 를 `/host/root` 에 `readOnly`·`HostToContainer` 로 붙이고 프로브가 mount_path 를 그 접두로 번역한다(`DMS_AGENT_HOST_ROOT`) — 스토리지마다 hostPath 를 나열하지 않는다(등록만 하면 모든 노드에서 프로브). `readOnly` 는 **최상위 바인드(호스트 루트 fs)** 에만 걸리고 HostToContainer 로 전파된 하위 마운트(스토리지)는 호스트 옵션(rw)을 유지한다 — k8s `recursiveReadOnly` 는 HostToContainer 와 병용 불가(실측: 파드 안 `touch /host/root/cephfs/x` 성공). 이는 종전 스토리지별 rw hostPath 와 같은 권한이고 델타는 호스트 루트 fs **읽기**뿐이다. 에이전트는 root 이지만 파일 내용을 서빙하는 코드가 없고 stat/statvfs/access 뿐이다. readOnly 를 빼거나 스토리지별 hostPath 를 다시 넣지 마라 — `test_agent_daemonset_contract.py` 가 둘 다 고정한다.

### 함정 (모르면 밟는다)

- submit의 scan 게이트는 원시 문자열 비교(routes_requests.py:80-84) — Operation(...) 변환은 422 변환 try 밖이라 여기서 ValueError가 나면 500이 된다
- artifacts open의 O_NONBLOCK은 필수(artifacts.py:194-199) — 사용자가 자기 phase 디렉터리에 mkfifo를 걸면 open이 영원히 블록, AnyIO 스레드풀(~40) 고갈로 SPA까지 전체 정지하고 팟 재시작 전까지 안 돌아온다
- 하드 링크는 realpath 봉쇄로 못 잡는다(artifact_files.assert_contained docstring) — 앱 층은 nlink>1 거부 + 소유자 검사로 보완하고, 근본 방어는 /cephfs 를 마운트한 **모든** 호스트(로그인·계산 노드 포함)의 fs.protected_hardlinks=1·protected_symlinks=1(링커 커널의 may_linkat 에서 검사, MDS 는 안 함 — 배포 전제, deploy/README §2b). 커널 내장 기본은 0 이고 배포판 sysctl.d 가 1 을 넣는다
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

**데이터·제어 흐름**: [에이전트 방향] run_loop(빈 상태로 시작) → AgentRunner.run_once: mountinfo 읽기 → build_report(mounts/tools/identities/os/artifact_base) → POST /api/agent/report → routes_agent.ingest_report: actor 검증 → agents.ingest → 응답 {storages, identity_probe_targets, report_interval_seconds, artifact_base_path} → 에이전트 상태 갱신 → sleep(max(1, interval)) 반복. once 모드는 부트스트랩+본 사이클 2회. [신원 방향] planner.py:154 → resolve_job_identity(control, resolver, ..., session_authenticated=(auth_method=="session")): owner=(owner_username or requester_id) → 자격 판정(allow_privileged AND session AND requester∈privileged_requesters) → privilege 정책(identity.privilege_policy: run_as_root is True → REQUESTED(자격 없으면 privileged_not_authorized), 배치 자식 → IF_ELIGIBLE, 그 외 → NEVER) → 특권 = 자격 AND 정책≠NEVER → group deny 규칙 있으면 특권 경로도 그룹만 선해석 → control.is_denied(데니리스트) → 특권이면 uid0/gid0 반환(보조 그룹 status privileged, 목록 []), 아니면 LDAP resolve(사용자 엔트리 + 그룹 검색 — cn·posixGroup gidNumber) → 그룹 포함 재차 is_denied → 비특권 주 gid 0 거부(D5) → 보조 gid 스냅숏(스위치 `DMS_IDENTITY_SUPPLEMENTARY_GROUPS` 가 켜져 있으면 valid_supplementary_gids → limit_supplementary_gids, 꺼져 있으면 status disabled) → control.register_probe_target(owner) → ResolvedIdentity → planner 가 D7 chown gid 멤버십 검사 후 worker_pool.identity 에 4키(supplementary_gids·_status·_excluded·_found)를 얼린다(원시 group_gids 는 싣지 않는다). planner 는 신원 해석 **앞**에서 owner_username 모양(`invalid_owner_username`)과 다른 실행 신원 지정 자격(`identity.owner_override_allowed`, API 와 같은 술어 → `privileged_not_authorized`)을 다시 본다. 등록된 probe target 은 다음 에이전트 응답에 실려 노드별 probe_identities 로 검증되고, planner 의 신원 전파 grace(DMS_PLANNER_IDENTITY_GRACE_SECONDS=300)가 그 전파 지연을 흡수한다.

### 모듈

| 파일 | 책임 |
|---|---|
| `src/dms/agent/runner.py` | 에이전트 루프. build_report()로 5종 프로브를 합쳐 POST /api/agent/report(Bearer 공유 토큰 + x-dms-actor: node:<이름>) 후, 응답으로 storages/probe_targets/interval/artifact_base_path 상태를 갱신. DB 무지의 순수 HTTP 클라이언트. |
| `src/dms/agent/probes.py` | 순수 프로브 로직(시스템 접근은 전부 파라미터 주입): parse_mountinfo/probe_mounts(Ready·Missing 판정), probe_tools(dscan/dsync/nsync/drm 발견+--version), probe_identities(pwd/grp 로컬 조회), probe_os_metrics(loadavg·meminfo·statvfs·net_dev), probe_artifact_base(exists/writable). |
| `src/dms/identity.py` | 실행 신원 오케스트레이션. ResolvedIdentity 모델(+ 보조 그룹 스냅숏 4키·리졸버 원시 group_gids), IdentityUnavailable/IdentityLookupInvalid/IdentityDeadlineExceeded/IdentityRejected/LdapCircuitOpen 예외, resolve_job_identity()가 데니리스트→특권 게이트→LDAP 해석→D5→보조 gid 스냅숏 순서로 신원을 확정. 보조 그룹 규칙의 순수 함수(valid_supplementary_gids·limit_supplementary_gids·supplementary_gids_problem)·owner_override_allowed·check_chown_group 과 하드 상수(GID_MAX·MAX_SUPPLEMENTARY_GROUPS·LDAP_TICK_BUDGET_SECONDS·LDAP_RESOLVE_DEADLINE_SECONDS)·tick_resolve_deadline 을 planner·stepper·execution_manifests·API 가 공유. |
| `src/dms/identity_ldap.py` | ldap3 기반 resolver. RFC 4515 필터 이스케이프 후 uid= 사용자 검색(uidNumber·gidNumber, **중복 엔트리는 fail-closed**) + 그룹 검색 — 멤버 속성 스위치 `DMS_LDAP_GROUP_MEMBER_ATTR`(memberUid 면 uid, member/uniqueMember 면 사용자 엔트리 DN), cn·gidNumber·objectClass, 페이징 500 × 최대 20쪽. 비성공 결과 코드는 fail-closed(4·11·페이지 상한·중복 = IdentityLookupInvalid, 그 밖(32 포함)·전송 오류·자체 마감 = IdentityUnavailable, 호출자 deadline 에 걸린 중단 = IdentityDeadlineExceeded), resolve 하나는 자체 마감 10s·호출자 deadline 중 이른 시각 안에서만 새 연산 시작. connect_first 순차 페일오버(시작 URI 기억 — sticky)·연결마다 unbind·bind 뒤 서버 정보 읽기 끔(get_info=NONE). build_ldap_resolver()는 uri/user_base/group_base 중 하나라도 placeholder 면 None 반환(=LDAP 미구성). |
| `src/dms/config.py` | AgentSettings(from_env: API URL·토큰 fail-fast, mountinfo/net_dev/virtual_net 경로) 및 서버측 신원 설정(allow_privileged_requesters 기본 True, privileged_requesters 기본 {root,admin}, ldap_require_auth_bind 기동 시 fail-closed 검증, config.py:180-190). |
| `src/dms/api/routes_agent.py` | 리포트 인제스트 엔드포인트. node_name 재검증 + actor 일치 강제 후 repos.agents.ingest(), 응답에 enabled 스토리지 목록·probe_targets(TTL)·보고 주기·artifact_base_path 를 내림. |
| `src/dms/repositories/control.py` | identity_denylist(is_denied — requester/owner/group 소문자 비교, has_group_denies)와 identity_probe_targets(register_probe_target: DELETE+INSERT 로 last_requested_at 갱신, probe_targets: TTL cutoff 정리 후 목록). |
| `src/dms/wiring.py` | build_identity_resolver() — settings 로 build_ldap_resolver 를 조립하는 유일한 진입점. |

### 불변식 (위반하면 깨진다)

- 특권 승격 3중 게이트 + 명시(2026-09-30): allow_privileged AND session_authenticated AND requester∈privileged_requesters 는 **자격**이고, 실제 uid 0 은 자격 AND privilege 정책(단건 run_as_root 명시·배치 자식)일 때만이다(identity.privilege_eligible·privilege_policy·resolve_job_identity). stepper 는 root 잡을 매 제출 직전 요청 행으로 재확인해 근거가 없으면 privilege_not_requested 로 종단한다(규칙 변경 전에 얼린 신원·변조 행). session_authenticated 기본값은 False(fail-closed) — 인자를 빠뜨린 새 호출자는 특권이 안 붙는 쪽으로 실패한다(identity.py:53-59 주석).
- 데니리스트는 최우선 kill-switch: 특권 경로보다 먼저 평가되고(identity.py:71-73), 특권 경로도 group 규칙이 등재돼 있으면(control.has_group_denies) LDAP 그룹을 선해석해 검사한다(identity.py:62-70).
- resolver=None(LDAP 미구성)이면 비특권 경로는 무조건 IdentityRejected(ldap_not_configured) — 로컬 폴백 없음(identity.py:76-77). LDAP 예외는 IdentityUnavailable→ldap_unavailable 로 fail-closed(identity.py:78-81).
- DMS_LDAP_REQUIRE_AUTH_BIND=true 면 BIND_DN/PW 결측·placeholder 시 기동 자체를 거부(SettingsError) — 익명 바인드로의 침묵 강등을 배포 시점에 발화시킨다(config.py:180-190). 검증은 identity_ldap 이 아니라 config 에 있다(발화 시점이 기동이어야 운영자가 알아챔).
- 에이전트 리포트의 actor 게이트: 토큰 경로 x-dms-actor 는 node:<이름> 형식만 허용되고(auth.py:49-60, _NODE_NAME_RE), routes_agent.ingest_report 가 body.node_name 과 identity.actor 의 일치를 재강제(agent_node_identity_mismatch 403). 노드 이름 정규식은 auth 가 유일 출처 — 두 규칙이 갈라지면 위조가 가능해진다(routes_agent.py 머리 주석).
- artifact_base 는 리포트 최상위 별도 필드지 mounts 배열이 아니다 — reconciler 가 mounts 를 storages.status 로 매핑하므로 섞으면 스토리지 판정이 오염된다(probes.py:92-103 docstring, runner.py:41-44).
- LDAP 필터 입력은 반드시 _escape_filter(RFC 4515)를 거친다 — username 이 uid= 와 memberUid= 필터에, 사용자 엔트리 DN 이 member/uniqueMember= 필터에 직결되므로 인젝션 방지의 유일한 방어선(identity_ldap._escape_filter·resolve).
- **보조 그룹(2026-10-07 D3·D4·D9·D10·D14, identity.py·identity_ldap.py 모듈 docstring)**: 실행(파드·워커 셸)으로 흐르는 그룹 값은 **objectClass posixGroup 엔트리의 gidNumber 숫자뿐**이다(sambaGroupMapping 같은 다른 클래스의 gidNumber 는 POSIX 소속이 아니다). cn·DN 은 denylist 이름 매칭 전용이고 컨테이너·셸·k8s 로 가지 않는다(워커 /etc/group 이름은 합성 `dmsg<gid>`). gidNumber 가 없거나·다중값·비숫자인 그룹은 gid 만 건너뛰고 이름은 남긴다(권한을 줄이는 쪽). **중첩 그룹은 해석하지 않는다**(1단계 멤버십 — 노드 SSSD 보다 덜 줄 수 있다, 안전한 방향). 범위: 하한 없음(0·65534 인정 — D3 사용자 승인), 상한은 **k8s 의 IsValidGroupID = MaxInt32(2147483647)** — 넘는 값을 하나라도 실으면 preflight Pod 생성이 422 라 그 사용자의 비 root 잡이 전부 깨지므로 '제외'(화면·`identity_groups_filtered` 이벤트에 보인다). 주 gid 와 같은 값은 조용히 뺀다(중복). 유효 목록이 256(MAX_SUPPLEMENTARY_GROUPS) 을 넘으면 **통째로 미적용**(status over_limit — 자르면 어느 그룹이 빠지는지 드러나지 않는다, 거부하면 돌던 사용자가 회귀). 스위치 `DMS_IDENTITY_SUPPLEMENTARY_GROUPS`(기본 켬, 오타는 꺼짐 쪽)는 **계획 시점 전용**이다 — planner 만 읽고, 진행 중 잡은 스냅숏대로 끝까지 간다(스위치가 덮지 않는 변경은 deploy/README §2c 표). 다른 실행 신원(owner_username ≠ 요청자)은 특권이라 planner 가 계획 시점에 API 와 **같은 술어**(`identity.owner_override_allowed`: 요청자 계정이 관리자 + 특권 허용 + 특권 목록)로 다시 보고(DB 직접 쓰기 방어 — 그 경로가 남의 uid 와 이제 보조 그룹까지 얻는다; 계정 행이 없거나 강등된 요청자의 배치 자식도 거부), 그 앞에서 비문자열·빈·형식 불일치 owner_username 을 `invalid_owner_username` 으로 끊는다(전엔 "" 가 '본인'으로, 비문자열은 매 틱 plan_error 로 새었다). 스냅숏 username ↔ 요청 owner 바인딩은 하지 않는다(D11 — 행을 실존 사용자 값으로 일관되게 바꾸는 위조는 지금의 uid 위조와 같은 등급).
- probe_mounts 의 Ready 판정은 exists AND is_mountpoint AND readable(R_OK+X_OK)이고 writable 은 판정에 반영되지 않는다 — 소비자는 status 요약이 아니라 필드를 직접 본다(probes.py:26-48).

### 함정 (모르면 밟는다)

- build_report 의 read_text 폴백(runner.py:29-32)을 지우면 probe_os_metrics 내부 호출이 전부 try/except Exception 이라 OS 지표 전체가 조용히 null 이 된다 — 테스트는 os_fn 을 주입하므로 초록을 유지해 CI 로는 못 잡는다.
- run_once 응답 처리에서 artifact_base_path 는 body.get(..., 기존값) — 구버전 서버 응답에 키가 없으면 기존 값을 유지한다. 이 폴백을 지우면 다음 리포트부터 프로브가 사라져 화면이 영구 '확인 대기 중'이 된다(runner.py:77-82). 에이전트는 ConfigMap envFrom 을 안 받아 base 를 아는 유일한 경로가 이 응답 필드다.
- DMS_AGENT_VIRTUAL_NET_PATH 기본은 반드시 미설정 — 파드 안의 /sys/devices/virtual/net 은 파드 netns 의 가상 인터페이스라, 마운트 없이 기본 경로를 쓰면 이름이 겹치는 호스트 물리 NIC 가 가상으로 오판돼 지표에서 빠진다(config.py:234-240). 설정됐는데 못 읽으면 필터를 끄고 lo 제외 전량 합을 유지한다(probes.py:149-154, 지표를 잃는 쪽이 더 나쁜 실패).
- /proc/net/* 는 netns 범위라 파드에서 기본 경로를 읽으면 veth 값이 나온다 — 네트워크만 DaemonSet 이 마운트한 /host/proc/1/net/dev 를 주입받고, loadavg/meminfo 는 네임스페이스되지 않아 기본 경로가 이미 호스트 값이다(probes.py:156-159).
- probe_artifact_base 의 writable 은 잡 파드 요청자 uid 기준이 아니다 — 레거시(직접 경로) 모드에선 에이전트 프로세스 uid 의 W_OK, host_root 모드(2026-09-16 이후 기본)에선 base 를 덮는 호스트 마운트의 rw/ro 옵션이다. 정직한 한계로 화면이 문구로 표기한다(probes.py probe_artifact_base). exists 가 핵심 신호: 잡 파드 hostPath 가 type: Directory 강제라 디렉터리 없는 노드에선 파드 기동 자체가 실패한다.
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

**데이터·제어 흐름**: os.environ → cli.main(argv 파싱) → [agent] AgentSettings.from_env → agent.runner.run_loop / [그 외] Settings.from_env → Database.connect → [migrate] migrations.migrate / [api] create_app(settings, db)→uvicorn / [controller] Repositories(db) → wiring.wire_reconnect_event(db, repos) → build_identity_resolver·build_execution_adapter·build_build_runner·build_rollout_runner·build_purge_runner(settings 기반 stub/volcano 분기) → controller.build_loops → run_all_once(--once, 결과 stdout) 또는 run_forever. 아티팩트 경로 해석은 소비자 → resolve_artifact_base(control_state DB값 우선, NULL이면 settings.artifact_base_uri) → strip_scheme → 파일시스템.

### 모듈

| 파일 | 책임 |
|---|---|
| `src/dms/config.py` | Settings/AgentSettings frozen dataclass + from_env 전수 검증(_SERVER_INT_KEYS 26개 int 키 테이블 — 2026-10-08 DMS_REQUEST_DELETE_QUIET_SECONDS(≥0)·DMS_REQUEST_PURGE_INTERVAL_SECONDS(≥1) 포함, _is_placeholder 부분일치, SettingsError.problems 누적), LDAP require-auth-bind fail-closed(config.py:181-190), artifact_base_allowed_prefixes(DMS_ARTIFACT_BASE_ALLOWED_PREFIXES: 절대경로 접두 튜플, 상대경로·"/"·".." 성분은 기동 거부, "."·"//" 는 normpath 로 접음, 빈 값 = 무제한) |
| `src/dms/wiring.py` | execution_backend(stub/volcano)에 따른 어댑터 선택: build_execution_adapter·build_build_runner·build_rollout_runner·build_queue_reader·build_purge_runner(request-purge, 2026-10-08) 5개 팩토리 + build_identity_resolver(LDAP) + wire_reconnect_event(DB 재연결 이벤트 훅, api·controller 공용) + build_summary_reader(컨트롤러 summary.json 읽기 — 어댑터가 조립한 <base>/<job_id>/<phase>/summary.json 을 조각으로 되돌려 artifact_files.read_contained_text 로, 소유자 uid 는 잡 행에서) |
| `src/dms/cli.py` | argparse 진입점: agent(서버 Settings 없이 AgentSettings만) / migrate / api(uvicorn+create_app) / controller(Repositories+4팩토리 조립, --once는 run_all_once, 아니면 run_forever). SettingsError는 stderr 출력 후 exit 2 |
| `src/dms/artifact_base.py` | 아티팩트 base 단일 진실 원천: strip_scheme(접두사만), resolve_artifact_base(DB 우선→env), normalize_artifact_base(정규형 file:///abs, 422용 DomainValidationError), allowlist_reason(prefix allowlist, realpath 기준, artifact_base_outside_allowlist), static_base_problem(요청자 관점 g+w·POSIX ACL·o+x 판정 — 3홉·PUT 과 stepper 의 그룹 잡 제출 관문 공용), roundtrip_artifact_base(실제 쓰기 왕복 프로브 — uid 0 관점), controller_check_once(allowlist→왕복 순으로 주기 검증 결과를 control_state에 기록) |
| `src/dms/registry.py` | 레지스트리 v2 태그 조회 fetch_repo_tags: 모든 실패를 None으로 접는 fail-soft, 캐시 없음, 정렬 반환(화면 순서는 routes_releases._order_tags — 최근 빌드 → 숫자 고려 내림차순), 타임아웃 3s/connect 2s. 2026-10-08: Link rel=next 페이지 나눔을 같은 레지스트리 안에서만 따라가고 페이지 상한(_MAX_PAGES)·둘째 페이지부터의 시간 예산(_PAGES_BUDGET_SECONDS)·같은 주소 반복·다른 호스트/읽을 수 없는 next·둘째 페이지 이후 실패면 그때까지의 목록을 TagList.truncated 로, 401 Bearer 면 익명 토큰 1회 재시도(받은 토큰은 다음 페이지에 재사용, digest·삭제도). 조회 주소는 http:// 고정(사용자 지시 — 건드리지 않는다) |
| `src/dms/metrics_series.py` | DB/HTTP 없는 순수 시계열 조립: build_node_points(샘플 단위 fail-soft, 네트워크 카운터 차분), clamp_window_hours, bucket_chars_for(SUBSTR 접두 절단), duration_histogram + DURATION_BUCKETS/SUBMIT_WAIT_BUCKETS |

### 불변식 (위반하면 깨진다)

- 필수 4키(DMS_DATABASE_URL/SHARED_TOKEN/ADMIN_TOKEN/SESSION_SECRET)는 결측뿐 아니라 CHANGE_ME/REPLACE_WITH_ 부분일치도 기동 거부 — _is_placeholder(config.py:72-79)가 부분일치인 이유: SHARED_TOKEN은 Bearer로 admin을 주므로 예시값 기동 = 무인증 admin
- from_env는 문제를 전부 모아 SettingsError(problems)로 한 번에 던진다(config.py:161-192) — cli.py:20-25,30-35가 각 problem을 stderr에 찍고 exit 2
- DMS_LDAP_REQUIRE_AUTH_BIND=true면 bind DN/PW 결측·자리표시자 시 기동 거부(config.py:181-190) — 익명 바인드 침묵 강등 차단, 발화 시점이 배포(기동)이어야 운영자가 알아챈다
- 아티팩트 base는 모든 소비자가 resolve_artifact_base 하나만 통과(artifact_base.py:22-30) — DB(control_state.artifact_base_uri) 우선, NULL이면 env. wiring.py:27-29는 이를 생성자 캡처가 아닌 lambda로 넘겨 호출 시점 DB값 사용을 보장
- file:// 제거는 strip_scheme(접두사만, artifact_base.py:15-19)으로 저장소 전체 통일 — str.replace 전체 치환은 경로 중간 file://까지 지워 다른 경로를 만든다. normalize_artifact_base는 그런 값의 저장 자체를 422로 거부(artifact_base.py:40-44)
- registry.fetch_repo_tags에서 None(응답 불가)과 [](태그 0개)는 다른 값(registry.py 모듈 docstring) — 구분이 무너지면 unknown_tag 검증이 조용히 fail-open 되거나 존재하는 태그를 잘못 차단. []는 레지스트리가 그렇게 말할 때만: 레지스트리의 「리포 없음」 404(Docker-Distribution-Api-Version 헤더 또는 NAME_UNKNOWN/NOT_FOUND 오류 본문)와 200 {"tags": null}(2026-10-08) — 레지스트리가 아닌 서버의 404 는 None. 잘린 목록(truncated)에 없는 태그는 "없다"가 아니라 검증 안 됨(routes_releases._unverifiable — 제출은 tag_verified:false 로 통과)
- registry는 캐시 금지(registry.py 모듈 docstring) — 제출 경로 unknown_tag 검증이 낡은 목록으로 실존 태그를 차단하는 것이 설계 §7이 금지한 방향
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
- DMS_REQUEST_DELETE_QUIET_SECONDS(기본 60, api)는 요청 삭제의 심층 방어(늦은 stepper·planner 스냅숏·종료 중 파드)라 0 은 e2e 하네스 전용이다 — 운영에서 0 으로 내리면 방금 끝난 잡의 제출 경합 창이 그대로 열린다(구조적 방어 set_phase_ref·create_plan_and_job 은 남지만 이중 방어가 사라진다). DMS_REQUEST_PURGE_INTERVAL_SECONDS(기본 15, controller)는 정리 백오프의 기준 단위이기도 하다(min(interval×2^n, 900s)).
- agent 서브커맨드는 서버 Settings 검증·DB 연결을 전혀 타지 않는다(cli.py:19-28) — DMS_DATABASE_URL 없이 뜬다

### 결합점

- cli → controller.build_loops/run_all_once/run_forever (controller.py): 조립된 identity_resolver·execution_adapter·build_runner·rollout_runner·purge_runner를 주입
- cli api → api/app.py create_app(settings, db): uvicorn으로 서빙, create_app 내부에서도 wire_reconnect_event 사용
- cli migrate → migrations.migrate(db)
- cli agent → agent/runner.run_loop(AgentSettings)
- wiring → execution.StubExecutionAdapter / execution_volcano.{KubernetesClient,VolcanoExecutionAdapter} / build_runner.{Stub,}BuildRunner / rollout_runner.{Stub,}RolloutRunner / queue_reader.{Stub,Volcano}QueueReader / identity_ldap.build_ldap_resolver
- wiring.build_execution_adapter → repos.storages.get(스토리지 lookup) + artifact_base.resolve_artifact_base(호출 시점 DB 조회 lambda)
- artifact_base → repos.control(control_state 읽기, set_artifact_base_check 쓰기), domain.DomainValidationError(라우트가 422로 운반), api/artifacts.py가 strip_scheme 재수출
- registry.fetch_repo_tags 소비처: 롤아웃 targets 화면(빈 목록+경고 강등)과 제출 경로 unknown_tag 검증(None 이거나 잘린 목록에 없으면 검증 스킵 — routes_releases._unverifiable, 이벤트 release_tag_unverified 의 payload.reason 이 registry silent / tag list truncated 를 가른다)
- metrics_series ← db.iso_epoch(별칭 _epoch), MetricsRepository.node_series 출력을 소비, /api/admin/metrics 계열 라우트가 사용
- wire_reconnect_event → repos.observability.record_event + db.on_reconnect 훅(db.py의 dialect/reconnect_count)

---
