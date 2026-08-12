# 슬라이스 25 — 실행 단계 진단 설계

슬라이스 5가 "vcjob 로그는 범위 밖"(설계 `:90` — 409 `log_not_available`)으로 남긴 지
10슬라이스째, 실행 단계 진단은 아티팩트 전용이다. 러너가 아티팩트를 쓰기 전에 죽는
실패는 파드 로그가 유일한 증거인데 그 로그는 읽을 수 없고, 프리플라이트 실패의 유일한
사본은 pod GC 86400s 가 파괴한다(`BACKLOG.md:468-469`). 이 슬라이스는 vcjob 로그
읽기를 열고, 실패 종단 시 로그를 DB 에 박제해 "유일 사본이 시한부"인 상태를 끝낸다.

## 1. 실측으로 확인한 전제

1. **read_log 는 vcjob ref 를 명시적으로 거절한다**(`execution_volcano.py:243-249`,
   409 매핑 `routes_artifacts.py:59-61`). 거절 주석의 전제 두 가지를 라이브로 재검증
   했다(2026-08-11, 읽기 전용): ① "파드 이름이 잡 이름과 다르다" — 절반만 사실이다.
   Volcano 는 파드를 `<vcjob>-<task>-<index>` 로 결정적으로 명명하고(실측:
   `dms-rm-preview-8aaadd2136bc-launcher-0`), launcher 는 replicas=1 이라
   `<vcjob>-launcher-0` 하나뿐이다. ② "잡 종료 후 사라진다" — 즉시가 아니다. 종단
   vcjob 의 launcher 파드는 vcjob 오브젝트가 삭제될 때까지 남는다(실측: Completed
   137m·Aborted 7d8h 잡 모두 launcher-0 잔존).
2. **Aborted vcjob 은 Failed 파드만 남긴다.** `dms-sync-execution-7ab63afc5924`
   (Aborted, 7d8h): launcher-0 이 Error(exit 1)로 **잔존**, 워커 파드는 전부 삭제.
   Completed 잡도 launcher-0 만 잔존(워커는 Running 중 삭제). 즉 실패 원인 파드는
   보존되고 Running 파드는 종료 시 지워진다 — 워커가 먼저 죽는 실패에선 launcher
   (Running)가 지워지고 Failed 워커가 남는다는 역도 성립한다.
3. **그 launcher 의 파드 로그는 비어 있었다**(실측: `kubectl logs` 출력 0바이트).
   러너가 mpirun 출력을 `capture_output` 으로 삼켜(`dms_job_runner/runner.py:149-150`)
   아티팩트 파일로만 쓰기 때문(`runner.py:81-87`). 파드 로그에 남는 것은 러너 자신의
   파이썬 트레이스백(크래시 경로)과 프리플라이트 마커(`DMS_PREFLIGHT_REASON=`,
   `execution_manifests.py:267-291`)뿐이다 — 정확히 아티팩트가 없는 실패들의 증거다.
4. **launcher 파드에는 dms.io 라벨이 없다.** 워커 task 템플릿만 `dms.io/job-id`·
   `dms.io/task` 를 단다(`execution_manifests.py:232-235`; launcher 템플릿은 metadata
   자체가 없다 — `:337-341`,`:379-386`). 대신 Volcano 가 모든 파드에 자기 라벨을
   단다 — launcher 실측: `volcano.sh/job-name=<vcjob>`,`volcano.sh/task-spec=launcher`.
   라벨 셀렉터 조회 수단은 이미 있다: `list_pod_briefs(ns, label_selector)`
   (`execution_volcano.py:400-425`, phase·waiting_reason 포함) — 단 K8sClient
   Protocol(`:61-68`)에는 빠져 있다. `_reconstruct_summary_path`(`:215-231`) 선례는
   vcjob **오브젝트** 라벨을 읽는 것이라 파드 탐색에는 못 쓴다.
5. **vcjob TTL 은 붙지만 집행은 미실측이다.** `DMS_VCJOB_TTL_SECONDS=86400`
   (`config.py:130`, `20-config.yaml:69`)이 `_apply_ttl`(`execution_manifests.py:
   217-223`)로 얹힌다. 실측: 08-10 이후 종단 잡은 ttl=86400, 그 전(08-04) 잡은
   ttl 없음 — TTL 없는 vcjob·파드는 7일 넘게 잔존한다(pod_gc 는 `pod/`·`pods/` ref
   만 지우고 vcjob 파드는 안 건드린다 — `pod_gc.py:31`). ttl 붙은 잡 중 아직 86400s
   를 넘긴 것이 없어 **Volcano 가 실제로 지우는지는 확인 못 했다**(§6-4).
6. **프리플라이트 실패의 사본은 파드 로그 하나다.** stepper 는 마커를 읽지 않고
   generic `preflight_failed` 로 접는다(`stepper.py:165`). 로그 라우트(`routes_
   artifacts.py:48-70`)가 유일한 열람 경로인데, pod GC 86400s(`pod_gc.py:26-37`,
   `20-config.yaml:78`)가 지나면 영구 소실 — 화면은 "파드 로그를 더 이상 조회할 수
   없습니다"(`JobViewer.tsx:102-104`)가 된다.
7. **실패 잡도 아티팩트 라우트는 이미 동작한다.** 읽기 경로는 `artifact_uri` 가
   아니라 base+job_id 로 조립되고(`routes_artifacts.py:12-17,27,38`), JobViewer 는
   `artifact_uri` 와 무관하게 탭을 그린다. 도구 비0 종료 실패는 러너가 stdout/stderr/
   summary 를 쓰고 나서 exit 하므로(`runner.py:81-88`) 이미 포탈에서 보인다. 슬라이스
   18 §1-6 의 함의 중 남는 구멍은 ① 러너 도달 전 실패(아티팩트 0건) ② 실패 잡의
   `result_summary`·`artifact_uri` 미기록(`set_artifact` 는 성공 경로만 —
   `stepper.py:216-220`, `:257-258`) 둘이다.
8. **execution 로그 탭은 이미 그려진다** — JobViewer 는 phase_refs 의 모든 phase 에
   탭을 만들므로(`JobViewer.tsx:46-53`) execution 탭을 누르면 409 문구
   (`api.ts:66`)가 뜨는 상태다. 화면 구조 변경 없이 내용만 채우면 된다.
9. **박제 선례**: builds 는 64KB 꼬리를 `finish` 에서 박제하고(`repositories/
   builds.py:10,130-141`), 목록에서 그 컬럼을 제외한다(I2 — `:93-103`). write-once
   는 SQL 술어 `IS NULL` 선례가 있다(`data_jobs.mark_exec_submitted:222-225`).
10. **data_jobs 는 전부 `SELECT *` 다** — get_job(`data_jobs.py:96-98`),
    list_jobs(`:117-127`, 라우트가 행을 그대로 응답한다 `routes_jobs.py:36-44`),
    claim_steppable(`:193-201`), terminal_jobs_older_than(`:305-327`),
    succeeded_scans(`:100-115`). 큰 컬럼을 그냥 얹으면 5초 폴링마다 최대
    50×64KB=3.2MB 가 왕복한다 — builds I2 가 고친 바로 그 문제다.
11. **취소는 파드를 즉시 지운다** — cancel 라우트가 모든 phase ref 를 terminate
    한다(`api/cancel.py:7-12`). Cancelled 잡의 로그는 종단 시점에 이미 없다.
12. **metrics 합계는 Succeeded 만 센다**(`repositories/metrics.py:144-147`,
    `state = 'Succeeded'`) — 실패 잡에 typed 카운트가 실려도 집계는 오염되지 않는다.

## 2. 핵심 결정

새 pip/npm 의존성 0건, 새 테이블 0건(컬럼 1개), 새 사유 코드 0건(poll_failed 재사용).

### 2.1 vcjob 로그 읽기 — 거절을 풀고 라벨 셀렉터로 파드를 찾는다

`K8sClient` Protocol 에 `list_pod_briefs(namespace, label_selector)` 를 추가한다 —
구현은 이미 있다(§1-4). `read_log("vcjob/<name>")` 분기: 셀렉터
`volcano.sh/job-name=<name>`(ref 에 이름이 있어 vcjob GET 불요)로 브리프를 얻고,
**launcher(이름 접미 `-launcher-`, §1-1 실측 명명) 항상 + 그 외는 phase Failed 만**
읽는다 — 성공 워커의 sshd 로그는 노이즈고(§1-2 에 따라 대개 이미 없다), 남는 파드는
실패 원인 파드다. 반환 계약을 `(pod, log)` → `(pod, log, waiting_reason)` 3-튜플로
확장한다(pod/pods 경로는 waiting_reason=None): 브리프의 waiting_reason 이
ImagePullBackOff 류를 로그 없이도 화면에 올린다 — 로그가 없다는 사실(null)과 왜
없는지는 별 채널로, null 을 합성 문자열로 뭉개지 않는다. per-pod 실패는 기존 계약
그대로 None 접기+경고(`execution_volcano.py:254-259`), **list 호출 자체의 예외**는
`ExecutionError("poll_failed", …)` 로 던져 409 표면화한다 — 403 이 "로그 없음"으로
렌더되는 사고(`:393-398` 교훈)를 조회 계층에서 반복하지 않는다. 진행 중 잡의
launcher 라이브 tail 도 이 분기로 공짜다.

### 2.2 실패 종단 시 로그 박제 — `data_jobs.diag_logs`, write-once

파드가 남아 있어도 시한부다(TTL 86400·pod GC 86400). **stepper 가 실패 종단을
관측하는 순간이 로그가 확실히 존재하는 마지막 지점**이므로 거기서 박제한다:

- 새 컬럼 `diag_logs TEXT NULL` — JSON `{"phase", "at", "entries": [{"pod", "log",
  "truncated"}]}`. CREATE TABLE(`migrations.py:133` 블록)과 `_ensure_columns`
  (`:453-491`) 양쪽(슬라이스 14 의 실 500 교훈).
- **상한과 잘림 표시(요구 명시)**: 파드당 꼬리 16KB·`truncated: true`, 항목 최대
  4(launcher 우선, 다음 Failed 파드) — 총 ≤64KB 로 builds `LOG_TEXT_MAX` 와 같은
  총량. 전 항목 log=None 이어도 저장한다 — "박제 시점에 이미 없었다"는 사실 자체가
  진단이다(모름을 뭉개지 않는다).
- 트리거는 `_finalize` 에 `diag=(phase, ref)` 인자를 얹어 실패 종단 4경로가 넘긴다:
  preflight_failed(`stepper.py:165`), execution_failed/TIMED_OUT(`:222-225`),
  preview_failed/preview_timed_out(`:262-266`), execution_recheck_failed(`:294`).
  submit 실패 계열은 파드가 없어 대상이 아니다. **박제 → set_job_state 순서**:
  박제 후 크래시하면 다음 틱 재폴링이 finalize 를 재시도하고(IS NULL 이 중복을
  막는다), 역순이면 종단 잡은 다시 스텝되지 않아 박제 기회가 영영 없다.
- write-once: `WHERE diag_logs IS NULL`(§1-9 선례). 박제 실패는 finalize 를 막지
  않되 `record_event(warning, "diag_archive_failed")` 로 표면화한다 — 한 잡의 로그
  때문에 종단 전이가 막히면 잡이 낀다.
- **§1-10 대응**: 다행 조회 4곳(list_jobs·claim_steppable·terminal_jobs_older_than·
  succeeded_scans)을 명시 컬럼 목록으로 바꿔 diag_logs 를 제외한다(builds I2 선례).
  get_job(단행)은 `SELECT *` 유지 — /logs 폴백이 그걸 쓴다.

### 2.3 pod GC·vcjob TTL 은 그대로 둔다 — GC 지연·선택 보존 기각

- **GC 지연(창 확대) 기각**: 창을 얼마로 늘려도 "언젠가 지운다"인 한 유일 사본
  문제는 시점만 옮겨지고, 노드 리부트·kubelet 축출에는 애초에 무방비다. 잔해 축적은
  슬라이스 10 이 GC 를 만든 이유 그 자체다.
- **선택적 보존(실패 잡 파드 GC 제외) 기각**: 상한이 없다 — 실패율에 비례해 파드가
  무한 누적되고, 파드는 어차피 내구 저장소가 아니다.
- **박제(DB) 채택**: 유계(≤64KB/잡), DB 백업에 실리고, builds 와 규약이 통일된다.
  86400 두 값은 무변경 — 이제 "라이브 로그 열람 여유 창"일 뿐이다. 20-config.yaml
  `:74-77` 의 "유일한 사본" 주석은 사실이 아니게 되므로 함께 갱신한다.

### 2.4 실패 잡의 summary·artifact_uri 표면화

execution/preview 의 FAILED/TIMED_OUT 경로에서 `read_summary(ref)` 를 시도해 값이
있으면 `set_artifact` 를 그대로 호출한다(성공 경로 `stepper.py:216-220` 와 동일
형식). 러너는 실패에도 summary.json 을 쓰므로(§1-7) returncode 가 카드에 뜨고,
`artifact_uri` 렌더는 이미 조건부라(`RequestDetail.tsx:182-183`) 화면 코드 0줄이다.
metrics 오염은 없다(§1-12). None 이면 기록하지 않는다 — 지어내지 않는다.

### 2.5 API 표면 — /logs 라이브 우선, 박제 폴백

`GET /api/user/jobs/{id}/logs?phase=` 확장: ① vcjob phase 도 라이브 시도(§2.1) ②
라이브가 빈 목록이거나 전 항목 log=None 이고 해당 phase 의 diag_logs 가 있으면
박제 사본으로 응답. 응답에 `source: "live"|"archived"` 와 항목별
`waiting_reason`(라이브)·`truncated`(박제)를 싣는다. 종단 잡 로그는 불변이라 두
소스의 내용 충돌은 없다. 409 `log_not_available` 은 알 수 없는 prefix 방어로만
남는다(양쪽 등록 유지, 문구 무변경). admin 은 `_owned_job` 의 role 우회
(`routes_jobs.py:30-31`)로 이미 모든 잡을 볼 수 있다 — 별도 라우트를 만들지 않는다.

## 3. 화면

- **JobViewer 로그 탭**(구조 무변경, §1-8): execution 탭이 처음으로 내용을 갖는다.
  라이브 항목은 waiting_reason 이 있으면 "파드 로그 없음 — ImagePullBackOff" 처럼
  사유를 병기하고, log=null 이면 기존 문구 유지. archived 응답이면 캡션 "잡 종료
  시점에 저장된 사본 — 파드당 마지막 16KB" + 항목별 잘림 배지(아티팩트 뷰의 "뒷부분만
  표시" 배지 재사용).
- **RequestDetail 실패 잡 카드**: §2.4 로 returncode(ResultSummary)와 아티팩트 URI 가
  뜬다 — 신규 컴포넌트 없음.
- 빈 로그도 그대로 보여준다(§1-3 의 launcher 처럼) — 빈 것은 정상값일 수 있다.

## 4. 오류 처리

- 박제 실패는 finalize 를 막지 않고 이벤트로 표면화한다(§2.2) — 조용한 실패 금지.
- read_log 의 per-pod 실패는 null 접기+경고 유지, list 예외는 poll_failed 409(§2.1).
  null(파드 소실)과 409(조회 계층 오류)를 뭉개지 않는다.
- 박제 사본이 전 항목 null 인 잡: 화면에 그대로 — "종단 시점에 이미 로그가 없었다".
- diag_logs JSON 이 깨져 있으면(손상 방어) 폴백을 포기하고 라이브 결과를 그대로
  반환하며 경고 이벤트를 남긴다 — 깨진 사본으로 응답을 지어내지 않는다.
- 스텁 경로: `StubExecutionAdapter.read_log`(`execution.py:84-85`)는 3-튜플 계약으로
  맞추되 클러스터 없이 초록이어야 한다(로컬·CI).

## 5. 테스트

- 어댑터: vcjob read_log — launcher 항상 포함·Failed 워커 포함·Succeeded 워커 제외,
  3-튜플 계약(pod/pods 경로 포함), list 예외 → poll_failed, 셀렉터 문자열.
- 박제: 실패 종단 4경로 각각 diag_logs 기록(phase·entries·truncated), 16KB/4항목
  상한, write-once(두 번째 finalize 시도 무변경), 박제 예외 시 이벤트+종단 전이
  진행, 성공 종단은 박제하지 않음.
- 마이그레이션: CREATE 와 `_ensure_columns` 양쪽에 diag_logs.
- 다행 조회 4곳의 SELECT 목록에 diag_logs 부재 단언(§2.2), get_job 에는 존재.
- 라우트: 라이브 성공 시 source=live / 파드 전멸+박제 존재 시 source=archived /
  둘 다 없으면 기존 계약(null 항목), invalid prefix 409 유지.
- 스텝퍼: §2.4 — 실패 시 summary 있으면 set_artifact, None 이면 미기록.
- 프론트: waiting_reason 렌더, archived 캡션·잘림 배지, 기존 409 문구 회귀.
- 기준선 유지: 백엔드 1131 passed·프론트 228 passed/49 files 에서 증가만.

## 6. 실증 (테스트베드)

1. **러너 도달 전 실패**: confirm 후 execution 직전에 소스 경로 권한을 회수해 도구
   비0 종료가 아닌 러너 크래시를 유도 → execution 로그 탭에 트레이스백(라이브),
   종단 후 diag_logs 에 같은 내용 박제 확인.
2. **프리플라이트 실패**: 읽기 불가 target 으로 scan 제출 → Rejected, diag_logs 에
   `DMS_PREFLIGHT_REASON=` 줄 → 파드를 수동 삭제(GC 모사)한 뒤 /logs 가
   source=archived 로 같은 마커를 제공하는지 — 핵심 실증.
3. 도구 비0 종료 실패(§1-3 재현): launcher 로그가 비어 있어도 박제가 빈 항목을
   정직하게 남기고, 아티팩트 stdout/stderr 와 returncode 카드가 함께 뜨는지.
4. **TTL 집행 확인(§1-5 마감)**: ttl=86400 인 종단 vcjob 이 실제로 사라지는 시점을
   관측하고, 소멸 후 /logs 폴백이 동작하는지. 워커 파드에 `volcano.sh/job-name`
   라벨이 실제로 붙는지도 이때 실측한다(§1-4 는 launcher 만 확인했다).
5. TTL 없는 구세대 vcjob 잔해(§1-5)를 운영자가 수동 정리해도 박제 사본으로 열람이
   유지되는지 — 정리 가능 판정의 근거가 된다.

## 7. 이 슬라이스에서 하지 않는 것

진단 사본을 **못 갖는** 잡을 정직하게 남긴다:

- **Cancelled 잡** — cancel 이 모든 ref 를 즉시 terminate 해(§1-11) 박제할 원본이
  없다. 취소 전 박제로 바꾸는 것은 취소 지연을 만들므로 하지 않는다.
- **submit 실패 계열**(`*_submit_failed`) — 파드가 생성된 적이 없다.
- **워커-선행-실패의 launcher 로그** — abort 가 Running launcher 를 지우면(§1-2 의
  역방향) 복구 불가. Failed 워커 로그만 박제된다.
- **이 슬라이스 배포 전에 종단된 과거 잡** — 백필하지 않는다(diag_logs NULL 유지,
  화면은 기존 null 문구).
- 성공 잡 로그 박제(아티팩트가 이미 영구 사본), 로그 스트리밍/팔로우, 성공 워커
  sshd 로그 수집, stepper 가 프리플라이트 마커를 파싱해 사유 코드로 승격하는 것
  (박제된 로그를 사람이 읽는 것으로 충분 — 코드 신설은 후속).
- 아티팩트 파일 보존/GC(슬라이스 18 §7 유지), pod GC·vcjob TTL 값 변경(§2.3),
  진단 이벤트 payload 에 로그 본문 싣기(64KB 가 요청 상세 응답에 실리게 된다).
