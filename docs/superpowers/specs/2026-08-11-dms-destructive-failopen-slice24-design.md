# 슬라이스 24 — 조용히 틀리는 것들: 파괴적 경로 fail-open 봉인 설계

백로그 §2.1(`BACKLOG.md:378-383`)의 4건을 닫는다. 공통 성격: 실패·미지 입력이
**파괴적 경로(drm)나 무제한 동작으로 흘러가는데 아무도 그것을 보지 못한다**.
파괴적 연산의 변경이므로 기존 정상 경로의 무회귀를 §5·§6 에서 명시적으로 보증한다.

## 1. 실측으로 확인한 전제

1. **tool 값의 유일한 원천은 placement 의 리터럴 4종이다**(`src/dms/placement.py:67,74,85,94`).
   `create_job` 은 tool 을 무검증 INSERT 한다(`src/dms/repositories/data_jobs.py:61-86`).
   `domain.Tool` StrEnum(`src/dms/domain.py:54-58`)은 **어디서도 import 되지 않는 죽은
   코드다**(전 소스 grep 0건) — "상류 enum 검증"은 사실 존재하지 않는다.
2. **`tool_argv` 의 마지막 분기는 주석 `# drm` 짜리 fall-through 다**
   (`src/dms/execution_manifests.py:49-55`): dscan/dsync/nsync 가 아닌 **모든** 문자열이
   drm 꼴 argv 를 받는다. 실행으로 확인: tool="dwalk" → `['/cephfs/x']`,
   dryrun 이면 `['--dryrun', '/cephfs/x']`. `render_tool_flags` 는 미지 도구에 `[]` 를
   돌려줘(`:36`) 플래그만 조용히 사라진다.
3. **러너는 DMS_JR_TOOL 문자열을 그대로 실행한다**: rank.sh 는
   `exec {tool} {argv}`(`src/dms_job_runner/runner.py:66-68`)이고 mpirun→runuser 로
   요청자 신원 실행이다(`commands.py:37-44`). shlex.quote 는 인자 인젝션만 막고
   **명령 이름 자체가 tool 값**이다. 같은 파일의 `_build_summary` 는 미지 도구를
   (None, None) 으로 접는 fail-closed(`runner.py:130-137`) — 열림/닫힘이 한 파일에서
   갈려 있다.
4. **stepper 도 미지 tool 에 관대하다**: `TOOL_TO_POLICY.get` 실패 → policy None →
   "타임아웃 없음"으로 계속 진행한다(`src/dms/stepper.py:70-83`, 주석이 이 선택을
   명시). 미지 도구 잡은 시간 상한조차 없이 제출된다.
5. **`storages._validate` 는 mount_path="/" + managed_root="/" 조합을 통과시킨다**
   (`src/dms/repositories/storages.py:16` 의 `p != "/"` 예외가 "/"만 살린다 — 실행으로
   ACCEPTED 확인). root="/" 를 다른 mount 와 조합하면 거부된다(`:20-22`, 실측). 이때
   `_abs` 는 `f"{root}/{rel}"` 라 `//team/data` 를 만든다(`stepper.py:46-50`, 실측).
   함정: `posixpath.normpath` 는 `//x` 를 **보존**한다(실측) — normpath 후처리로는 못
   고친다. `posixpath.join("/", rel)` 은 `/team/data` 를 준다(실측).
6. **mount_path="/" 는 잡 파드에 노드 루트를 hostPath 마운트한다**: `_volumes` 의
   조상-커버 축약이 artifact base 까지 "/" 에 삼켜 단일 볼륨
   `{"hostPath": "/", "mountPath": "/"}` 로 접힌다 — 볼륨 이름 fallback `or "root"` 가
   이 경우를 이미 코드로 예비해 뒀다(`src/dms/execution_volcano.py:112-119`).
7. **rm 검증은 managed_root 자신만 막는다**: `validate_rm_target` 은 ""/"." 만 거부
   (`src/dms/domain.py:97-103`) — root="/" 면 target "etc" 가 통과해 abs `//etc` 가 된다.
8. **`_abs` 결측 폴백은 상대경로를 그대로 돌려주고 로그 0건이다**(`stepper.py:46-50`).
   storage 삭제 가드는 라우트에만, 요청 레벨로 있다(`src/dms/api/routes_storages.py:59-60`
   → `requests.py:106-117`). **update 에는 가드가 전혀 없다**(`routes_storages.py:42-53`) —
   잡 진행 중에 managed_root 를 바꿀 수 있다. 컬럼은 NOT NULL(`migrations.py:209`)이라
   결측 트리거는 사실상 "행 삭제"와 직접 DB 조작뿐이다.
9. **고아 복구 쿼리는 ORDER/LIMIT 없는 전량 JOIN 이다**
   (`src/dms/repositories/data_jobs.py:329-341`). 호출부는 행마다 get_job + finalize 를
   행 단위 try/except 없이 돈다(`src/dms/controller.py:42-49`) — 첫 예외가 나머지
   전부를 다음 틱으로 민다. 바로 위의 `terminal_jobs_older_than` 은 LIMIT 200 +
   오래된순을 이유 주석과 함께 이미 갖췄다(`data_jobs.py:305-327`) — 선례가 같은
   파일에 있다.
10. **컨트롤러는 단일 스레드 순차 루프다**(`controller.py:126-133`). stepper 틱 5초
    (`config.py:21`), 루프 리스는 max(5·3, 30)=30초(`controller.py:103-105`).
    `finalize_from_job` 의 멱등은 read-then-write 고 `record_result` 는 무조건 INSERT
    (`requests.py:151-163,119-127`) — 리스가 깨진 동시 실행이면 results 중복 삽입이
    가능하다. 테스트베드는 replicas=1(`deploy/k8s/41-controller.yaml:21`)이라 잠복 상태.
11. **고아 대량 발생의 실경로는 preview 만료다**(`controller.py:37-41`):
    `expire_previews` 는 한 호출로 N 건을 종단 전이시키고 finalize 는 행별 후속이다 —
    그 사이 크래시면 N 건이 한꺼번에 고아가 된다.
12. `storage_in_use`·`invalid_storage` 는 이미 양쪽 등록된 사유 코드다
    (`frontend/src/lib/reasonCodes.json:21,28`) — 재사용하면 계약 테스트 추가 부담이 없다.

## 2. 핵심 결정

### 2.1 미지 도구 fail-closed — 3층, 각 층의 역할이 다르다

**이 결함이 지울 수 있는 것(구체 시나리오)**: (a) 미래에 다섯째 도구를 placement 에
추가하고 `tool_argv` 를 잊으면, 그 도구는 지금 **drm 꼴 인자**(맨몸 절대경로)로
실행된다(§1-2). 경로 positional 을 삭제 대상으로 해석하는 도구라면 스캔 의도가 삭제로
바뀌고, scan 분류 잡은 preview/confirm 게이트도 없다(`stepper.py:162-163`). (b) DB 가
신뢰 경계다 — `data_jobs.tool` 에 "sh" 를 쓸 수 있는 자는 rank.sh 가
`exec sh /<managed_root>/<target>` 이 되게 만들어(§1-3) **사용자가 내용을 통제하는
파일을 워커 노드에서 스크립트로 실행**시킨다. 검증은 어디에도 없다(§1-1).

- **층1(제어면, 1차)**: `_step_one` 진입에서 `job["tool"] not in TOOL_TO_POLICY` 면
  종단 처리한다 — Pending/Preflight/PreviewRunning 은 REJECTED,
  Executing/Running 은 FAILED(이 갈림은 `preflight_submit_failed`→REJECTED /
  `execution_submit_failed`→FAILED 의 기존 대칭 그대로, `stepper.py:144,173`).
  reason_code **`unknown_tool`** 신설. 살아 있는 phase_refs 는 `_reclaim_if_terminal`
  관례대로 best-effort terminate. §1-4 의 "타임아웃 없음" 관용 분기는 도달 불능이
  되므로 제거한다 — 그 주석이 걱정한 "매 틱 예외로 영구히 낀 잡"은 종단 처리라
  애초에 생기지 않는다.
- **층2(순수 함수)**: `tool_argv` 의 fall-through 를 명시적 `if spec.tool == "drm"` 로
  바꾸고 else 는 raise. 어댑터의 blanket except(`execution_volcano.py:156-157`)가
  `submit_failed`(detail=도구명)로 접는다 — 사유는 층1보다 거칠지만 조용하지 않고,
  층1이 앞서므로 정상 운영에선 도달하지 않는다.
- **층3(러너, 최종)**: `run_job` 이 tool 을 allowlist(`("dscan","dsync","nsync","drm")`)
  와 대조하고, 밖이면 **exec 없이** stderr 마커 + returncode≠0 으로 끝낸다.
  `dms_job_runner` 는 `dms` 를 import 하지 않는 독립 패키지라 튜플을 중복 정의하되,
  `config.AGENT_TOOL_NAMES`(`config.py:7`)와의 동일성을 저장소 테스트로 계약한다.
  이 층만이 "이미 제출된 매니페스트/env 의 사후 변조"까지 막는다.
- DB CHECK 제약은 기각한다: SQLite 는 기존 테이블에 CHECK 를 ALTER 로 못 얹고,
  테이블 재생성은 새 테이블 금지 원칙과 충돌한다. 층1~3이 대신한다.
- 정상 경로 불변: 4종 도구는 세 층 모두 무변경 통과다. 새 의존성 없음.

### 2.2 managed_root/mount_path "/" 금지 + `_abs` 는 posixpath.join

**시나리오**: 관리자가 {mount "/", root "/"} 를 등록하면(포탈 PUT 이 지금 수락한다,
§1-5) 에이전트 statvfs("/") 는 어느 노드에서나 성공해 mount Ready 가 되고, 사용자의
rm target "etc" 가 검증을 통과하며(§1-7), 잡 파드는 **노드 루트를 hostPath 로
마운트한 채**(§1-6) `drm //etc` 를 요청자 신원으로 실행한다 — 요청자 uid 가 지울 수
있는 만큼 노드 /etc 가 지워지고, 특권(root) 요청자면 전부다. `//` 는 POSIX 상
구현 정의 경로라 문자열 비교 계열(감사 로그·아티팩트 표시)과도 어긋난다.

- `_validate` 에 mount_path·managed_root 의 `"/"` 를 명시 거부한다(`invalid_storage`
  재사용, detail "root filesystem is not a storage"). CephFS/GPFS/WekaFS 는 노드
  루트에 마운트되지 않는다 — "/" 가 정당한 배포는 없다.
- `_abs` 의 f-string 결합을 `posixpath.join(managed_root, rel)` 로 바꾼다. 정상 root
  에선 출력이 동일하고(실측, `test_stepper_enrich.py:41` 이 앵커), 검증 이전에 이미
  DB 에 남아 있을 "/" 행에서도 `//` 가 생기지 않는다 — 검증은 create/update 에만
  발화하므로(§1-5) 이 2차 방어가 필요하다. normpath 후처리는 `//` 를 보존해서
  대안이 못 된다(§1-5 실측).

### 2.3 고아 복구 — LIMIT 200 + 행 단위 격리

**시나리오**: preview TTL 만료가 쌓인 상태(배치 sync 수천 건 confirm 방치)에서
만료 직후 크래시(§1-11) → 다음 틱의 무제한 스윕이 전량 JOIN + N×(get_job+finalize)
를 단일 스레드에서 돌아 **planner·stepper·pod-gc·build-watcher 전부가 그 뒤에서
선다**(§1-10). 30초 리스를 넘기면 replicas>1 배포에서 두 컨트롤러가 같은 스윕을
동시에 돌고, read-then-write 멱등의 틈으로 results 가 중복 INSERT 된다(§1-10) —
"복구"가 이력을 오염시킨다.

- 쿼리에 `ORDER BY d.updated_at ASC, d.job_id ASC LIMIT 200` — 같은 파일의
  `terminal_jobs_older_than`(§1-9) 미러. finalize 가 멱등이고 처리된 행은 술어
  (request 비종단)에서 빠지므로 틱마다 200건씩 반드시 전진한다.
- 호출부를 행 단위 try/except + `record_event` 로 격리한다 — 독 행 하나가 나머지
  199건을 영구히 막는 현 구조(§1-9)를 없앤다. 실패 행은 다음 틱 재시도.
- 0건 스윕은 정상값이다 — 이벤트를 남기지 않는다.

### 2.4 `_abs` 결측 fail-closed + storage update 가드

**폴백의 실피해(§1-8)**: dsync 목적지가 상대경로로 남으면 도구는 launcher cwd 기준
컨테이너 오버레이에 복사하고 잡은 SUCCEEDED 로 끝난다 — **데이터는 파드와 함께
증발하는데 사용자는 "성공한 sync" 를 믿는다**. drm 이면 cwd 기준 상대 삭제다.
어느 쪽도 로그 한 줄 없다.

- `_abs` 는 storage 결측/managed_root 결측이면 전용 예외를 던지고, `_step_one` 이
  받아 reason_code **`storage_missing_at_step`** 로 종단한다(상태별 REJECTED/FAILED
  갈림은 §2.1 층1과 동일) + `record_event`. 폴백 자체를 삭제하므로 "로그를 안
  남긴다"(백로그 항목 4)는 구조적으로 소멸한다. 발급된 phase_refs 는 terminate.
- **update 가드 신설**: mount_path/managed_root/backend_type 이 기존 값과 다르고
  `active_referencing_storage` 면 409 `storage_in_use`(§1-12 재사용). enabled 토글은
  가드 없이 허용한다 — 진행 중 잡의 비상 차단 경로를 막으면 안 된다. 이것이 없으면
  preview 에서 사용자가 확인한 경로와 execution 이 실제 도는 경로가 갈라진다(§1-8)
  — 확인 게이트를 우회하는 TOCTOU 다.
- 정직한 한계: delete/update 의 check-then-act 는 비원자이고(기존 delete 가드와 동일
  수준), 리포지토리 직접 호출은 라우트 가드를 우회한다 — 그래서 `_abs` fail-closed
  가 최종 방어이고, 가드는 창을 좁힐 뿐 없애지 못한다는 것을 숨기지 않는다.

### 2.5 사유 코드 — 2종 신설, 양쪽 등록

`unknown_tool`, `storage_missing_at_step` 을 `frontend/src/lib/reasonCodes.json` 과
`api.ts` 양쪽에 등록한다(계약 테스트 조건). `invalid_storage`·`storage_in_use` 는
기등록 재사용(§1-12). 새 pip/npm 의존성 없음, 새 테이블·컬럼 없음.

## 3. 화면

- 신규 화면 없음. `unknown_tool`("허용되지 않은 도구입니다 — 관리자에게 문의하세요"),
  `storage_missing_at_step`("잡 진행 중 스토리지 정의가 사라졌습니다") 문구가 기존
  reasonText 경로로 잡 상세에 렌더된다.
- StoragesPage: update 의 신규 409 `storage_in_use` 는 delete 와 같은 ApiError 공통
  경로로 흐른다 — 프론트 코드 변경은 문구 재사용뿐.

## 4. 오류 처리

- 층1 종단 시 terminate 실패는 기존 `terminate_failed` 이벤트 관례(`stepper.py:114-121`)
  를 따른다 — 고아 리소스를 조용히 두지 않는다.
- 층2 raise 가 `submit_failed`(detail=도구명)로 접히는 것을 숨기지 않는다 — 층1이
  앞서므로 도달 자체가 회귀 신호이고, detail 이 원인을 보존한다.
- 층3 러너 거부는 summary `{"returncode": 1, files/bytes null}` + stderr 마커로 남고
  제어면에선 `execution_failed` 로 접힌다 — 세분 사유는 층1의 몫이며, 층3 발동은
  층1·2가 뚫렸다는 뜻이라 그 자체가 조사 대상이다.
- 고아 스윕의 행 단위 실패는 이벤트로 남고 다음 틱에 멱등 재시도된다(§2.3).

## 5. 테스트

**정상 경로 무회귀를 보증하는 기존 테스트(파괴적 경로 변경의 안전망)**:
`tests/test_execution_manifests.py:83-86`(drm argv 정확 일치)·`:23`(rm 플래그),
`tests/test_job_runner_runner.py`(4도구 정상 실행·summary), `tests/test_stepper_scan.py`
·`test_stepper_sync.py`(상태기계 전이), `tests/test_stepper_enrich.py:35-41`
(`_abs` 절대경로 합성 — join 치환의 앵커), `tests/test_repo_storages.py`,
`tests/test_recover_orphans.py`(멱등 복구). 기준선 1131 passed 가 전부 초록이어야 한다.

신규:
- tool_argv: 미지 도구 raise, drm 명시 분기 결과 불변.
- stepper: 미지 tool Pending→Rejected(`unknown_tool`) + 파드 미제출 / Executing→Failed
  + ref terminate / 4종 도구 무변화.
- 러너: allowlist 밖 tool 은 run 콜렉터에 mpirun 호출 0건 + returncode≠0 / 튜플 ==
  `AGENT_TOOL_NAMES` 계약.
- storages: mount_path·managed_root "/" 각각 422 / 기존 정상 값 통과 / update 가드
  (경로 변경+활성 요청 409, enabled 토글은 통과, 활성 요청 없으면 경로 변경 통과).
- `_abs`: 레거시 "/" 행에서 `//` 미생성, 결측 시 종단+이벤트(기존
  `test_stepper_scan.py:155-173` 의 step_error 경로와 구분되는지).
- 고아: 201건 → 첫 틱 200 + 둘째 틱 잔여 / 독 행 1건이 나머지를 막지 않음 / 0건 무이벤트.
- 사유 코드 계약: 신설 2종 양쪽 등록(`test_reason_codes_coverage.py`).

## 6. 실증 (테스트베드)

1. **무회귀 앵커**: 기존 스토리지에 rm 잡 1건 preview→confirm→성공 — 파괴적 정상
   경로가 변경 후에도 그대로임을 실 클러스터에서 확인(§2.1 층1~3 전부 통과 경로).
2. 포탈에서 mount_path="/" 스토리지 생성 시도 → 422 `invalid_storage`.
3. Pending 잡의 tool 을 DB 에서 "dwalk" 로 UPDATE → 다음 틱 Rejected(`unknown_tool`),
   `kubectl get pod`(읽기 전용)로 파드 0건 — 층1이 제출 자체를 막았음을 증명.
4. job image 컨테이너를 로컬로 띄워 `DMS_JR_TOOL=sh` 로 run_job 단독 실행 →
   exec 없이 거부(층3) — 클러스터 무변경 검증.
5. 고아 3건 수동 재현(request 상태 되돌리기) → 한 틱 복구 + `orphan_recovery`,
   복구 후 재스윕 0건.

## 7. 이 슬라이스에서 하지 않는 것

- tool 컬럼 DB CHECK 제약 — SQLite 테이블 재생성 필요(§2.1 에 기각 근거).
- 절대경로를 plan 시점에 구워 두는 구조 변경(worker_pool 처럼) — update 가드가 실용
  봉인이고, 잔여 창은 §2.4 에 명시했다. 경로 스냅숏은 preview 표시·아티팩트 계약에
  파급이 커 별도 슬라이스 감이다.
- delete/update 가드의 트랜잭션화(check-then-act 원자화) — 창을 §2.4 에 표기하고
  `_abs` fail-closed 를 최종 방어로 삼는다.
- 러너의 도구 절대경로 고정(`/usr/local/bin/…`) — allowlist 로 충분하고, 이미지
  레이아웃 결합을 새로 만들 이유가 없다.
- `execution_failed` 의 세분화(러너 거부를 별도 코드로) — 층3 발동은 회귀 신호라
  로그 마커로 족하다(§4).
- 미지 operation(§1-1 의 `invalid_operation` 계열) 재점검 — planner 어드미션이 이미
  fail-closed 다(`placement.py:101`).
