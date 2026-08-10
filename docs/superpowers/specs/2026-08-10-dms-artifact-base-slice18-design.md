# 슬라이스 18 — 아티팩트 경로 설정 설계

`DMS_ARTIFACT_BASE_URI`(아티팩트 base)를 포탈에서 설정 가능하게 만든다. 용도는
**설치·초기 구성 전용**이다 — 운영 중 이전은 목표가 아니며, 이 전제가 §2.3 변경
잠금의 근거다.

## 1. 실측으로 확인한 전제

1. **Settings 는 frozen dataclass 고 런타임 재읽기 경로가 없다.**
   `@dataclass(frozen=True)`(`src/dms/config.py:94`), `from_env` 호출은
   `src/dms/cli.py:21`(agent)·`:31` 두 곳뿐이다. `app.state.settings`
   (`src/dms/api/app.py:32`)와 컨트롤러 루프 클로저(`src/dms/controller.py:34-35`)가
   같은 인스턴스를 캡처한다. 기본값 `file:///artifacts/dms`(`config.py:112,166-167`),
   테스트베드 값 `file:///cephfs/dms/artifacts`(`deploy/k8s/20-config.yaml:27`).
2. **base 소비 지점은 4곳이다**: ① `stepper._build_spec`(`src/dms/stepper.py:82` —
   JobStepper 는 매 틱 재생성되고(`controller.py:34-35`) 정책도 매 틱 DB 재조회다,
   `stepper.py:67-69`), ② `VolcanoExecutionAdapter` **생성자 캡처**
   (`src/dms/execution_volcano.py:71,77`, 조립 `src/dms/wiring.py:22-26`),
   ③ `routes_artifacts._base`(`src/dms/api/routes_artifacts.py:11-12`),
   ④ scan-path 통계(`src/dms/api/routes_scan_paths.py:126`).
3. **API·컨트롤러 Deployment 는 `/cephfs` hostPath(type: Directory)만 마운트한다**
   (`deploy/k8s/40-api.yaml:122-126`, `41-controller.yaml:71-75`). 새 base 가 그 트리
   밖이면 — env 든 DB 든 — 매니페스트 수정+롤아웃 없이는 컨트롤러의 `read_summary` 가
   None 이 된다: `read_text` 는 OSError 를 None 으로 흡수하고(`wiring.py:15-20`),
   stepper 는 잡을 **SUCCEEDED 로 유지**한 채 `summary_unavailable` 경고만 남긴다
   (`stepper.py:182-190`). API 아티팩트 라우트는 전부 404 가 된다. **실패가 조용하다.**
   컨트롤러가 이 마운트에 의존한다는 사실은 `41-controller.yaml:7-14` 주석이 이미
   명문화해 뒀다.
4. **잡 파드 hostPath 는 `type: Directory` 강제다**
   (`src/dms/execution_manifests.py:167-169`). k8s 는 이 디렉터리를 만들어주지 않으므로
   새 base 가 후보 노드에 미리 없으면 preflight/워커 파드가 기동 자체를 실패한다.
   뒤집으면 **노드에 디렉터리가 존재하는 것**이 파드 기동 가능 여부의 직접 신호다.
5. **읽기 라우트는 잡 행이 아니라 현재 settings 의 base 로 경로를 조립한다**
   (`routes_artifacts.py:11-12`). `data_jobs.artifact_uri` 는 화면에 텍스트로 보여줄
   뿐(`frontend/src/features/jobs/RequestDetail.tsx:182-183`) 읽기에 쓰이지 않는다.
   scan-path 통계는 읽기 실패를 `continue` 로 삼켜 `no_covering_scan` 404 로 조용히
   퇴행한다(`routes_scan_paths.py:138-139,161`).
6. **`artifact_uri` 는 성공 execution(`stepper.py:193-196`)과 성공 preview
   (`stepper.py:233-235`) 두 곳에서만 기록된다.** 실패·타임아웃·취소·Rejected 잡은
   전부 NULL 이다 — 그러나 디스크에는 러너가 stdout/stderr/summary 를 항상 쓴다
   (`src/dms_job_runner/runner.py:81-87`). 또 `set_artifact` 의
   `COALESCE(:a, artifact_uri)`(`src/dms/repositories/data_jobs.py:238`)는 :a 가
   비-NULL 이면 덮어쓰므로, 옛 base 에서 preview 하고 새 base 에서 execution 한 sync
   잡은 preview 아티팩트의 실제 위치를 잃는다.
7. **`_reconstruct_summary_path` 는 어댑터 생성 시점의 base 를 쓴다**
   (`execution_volcano.py:77,222-223`). base 변경 후 컨트롤러가 재시작하면 in-flight
   잡의 summary 를 못 찾는다.
8. **`api/artifacts.py` 의 보안 불변식은 base 가 단일하다는 전제 위에 있다** — fd
   기반 봉쇄(`src/dms/api/artifacts.py:188-210`), 존재 오라클 차단·404 뭉개기
   (`routes_artifacts.py:35-39`).
9. **스킴 제거가 두 계열로 갈라져 있다**: `strip_scheme` 은 접두사만 벗기고
   (`api/artifacts.py:46-47`; 호출 `routes_artifacts.py:12`,
   `routes_scan_paths.py:126`), `str.replace("file://", "")` 는 **전체 치환**이다 —
   정확히 4곳: `execution_volcano.py:100,155,223`, `execution_manifests.py:174`.
10. **아티팩트 파일을 지우거나 옮기는 코드는 저장소에 0건이다**(rmtree/remove/unlink
    grep 0건; `pod_gc.py` 는 k8s 파드만 지운다). base 를 바꿔도 옛 파일은 영구히 옛
    경로에 남는다. GC 도 없다.
11. **DB 이관 선례가 이미 있다**: `control_state` 싱글톤
    (`src/dms/migrations.py:283-290`, 시드 `:337-340`, `_ensure_columns` `:419`,
    리포지토리 `src/dms/repositories/control.py:96-111`).
    `deploy/k8s/20-config.yaml:87-88` 은 "운영자가 포탈에서 바꾸는 값을 ConfigMap 에
    두면 재적용마다 되돌아간다"를 `build_node_name` 으로 이미 명문화했다.
12. **에이전트 프로브**: `probe_mounts` 는 `writable` 을 계산하지만
    (`src/dms/agent/probes.py:34`) `status` 판정은 writable 을 반영하지 않는다
    (`probes.py:35-42`). 프로브 대상 목록은 리포트 **응답**에 실려 내려간다
    (`src/dms/api/routes_agent.py:23-31`).

## 2. 핵심 결정

### 2.1 저장은 `control_state`, 해석은 단일 함수

`control_state` 싱글톤 행에 `artifact_base_uri TEXT NULL` 을 추가한다(CREATE 와
`_ensure_columns` 양쪽 — §1-11 의 선례 그대로). **NULL = 미설정 → env
(`settings.artifact_base_uri`) 사용.** 기존 배포는 동작이 바뀌지 않는다(시드 불필요,
하위호환). ConfigMap 에 두지 않는 근거는 `20-config.yaml:87-88` 이 이미 적어 둔
그것이다 — 재적용마다 되돌아간다. `build_node_name` 이 정확히 그 이유로 DB 로 옮겨진
선례다.

해석은 **단일 진실 원천 함수 하나**로 모은다:
`resolve_artifact_base(control_repo, settings)` — DB 값이 있으면 그것, 없으면 env.
모든 소비자가 이것만 통과한다. 재배선 지점은 §1-2 의 3곳이다:

- `stepper._build_spec`(`stepper.py:82`): 틱마다 재조회 — 정책이 이미 매 틱 DB 를
  읽는 것(`stepper.py:67-69`)과 같은 패턴이라 비용 논쟁이 없다.
- `VolcanoExecutionAdapter`: 생성자 캡처(§1-2 ②, §1-7)를 **호출 시점 해석**으로
  바꾼다 — 컨트롤러 재시작 없이 새 값이 반영되고 §1-7 함정의 절반이 사라진다.
- API 라우트 2곳: `routes_artifacts._base`, `routes_scan_paths`.

쓰기는 **전용 메서드 `set_artifact_base(uri, actor)` + 전용 UPDATE** 로 분리한다.
기존 `set_control_state`(`repositories/control.py:98-110`)에 얹지 **않는다**: 그
UPDATE 는 `build_node_name = :bn` 을 **무조건** 쓰므로 인자를 생략한 호출이 기존
값을 조용히 NULL 로 지운다(현재는 라우트가 항상 넘겨 잠복해 있다). 같은 자리에
컬럼을 하나 더 얹으면 그 함정이 그대로 복제된다 — 컬럼 하나만 만지는 UPDATE 로
분리하면 구조적으로 불가능해진다.

### 2.2 정규화는 저장 시점 한 곳에서

정규형은 `file:///<절대경로>` 다. PUT/validate 입력을 저장 시점에 한 번만 정규화한다:
후행 슬래시 제거, `..` 세그먼트 금지, **경로 중간 `file://` 금지**, 상대경로 거부.
소비자는 정규형만 본다 — 방어 코드를 4곳에 복제하지 않는다.

함께 고친다: §1-9 의 스킴 제거 분기를 `strip_scheme`(접두사만)으로 **통일**한다.
전체 치환 4곳(`execution_volcano.py:100,155,223`, `execution_manifests.py:174`)이
대상이다. 지금까지는 env 가 신뢰 입력이라 잠복해 있었지만, 자유 입력을 받기 시작하면
`/data/file://x` 같은 값에서 두 계열이 **다른 경로**를 만든다. 저장 시점 정규화가
그런 입력을 거부하더라도, 같은 문자열을 두 방식으로 해석하는 코드를 남겨 두지 않는다.

### 2.3 변경 잠금 — 거부 + 명시적 강제 플래그 (사용자 확정)

`data_jobs` 가 **1건이라도 있으면** PUT 을 409 `artifact_base_locked` 로 거부한다.

`artifact_uri IS NOT NULL` 로 좁히지 **않는다.** 실패·타임아웃·취소·Rejected 잡은 그
컬럼이 NULL 이지만(§1-6) 디스크에는 러너가 쓴 stdout/stderr 가 있고, 그것이 진단의
**유일한 사본**이다. NOT NULL 로 좁히면 정확히 그 잡들 — 가장 진단이 필요한 잡들 —
의 증거를 버린다. 또 이 잠금은 §1-7(재시작 후 in-flight summary 유실)과
§1-6(COALESCE 덮어쓰기로 preview 위치 유실)을 **구조적으로 닫는다** — 잡이 존재하는
한 base 가 안 바뀌므로 두 함정의 전제 자체가 성립하지 않는다.

`force=true` 면 통과하되 감사 로그(`control.py:12` 의 `_audit` 관례)에
`{forced: true, affected_jobs: N}` 을 남긴다. UI 는 N 건과 "이 잡들의 아티팩트·로그
열람이 깨집니다"를 확인 다이얼로그로 강제한다(§3).

### 2.4 3홉 검증 — API + 에이전트 + 컨트롤러 (사용자 확정)

경로 하나를 세 프로세스가 각자 자기 파일시스템에서 본다. 어느 하나만으로는 §6-5 의
갈라짐(API 는 되는데 노드·컨트롤러는 안 되는)을 못 잡는다.

- **(a) API 즉석**: 저장 요청 안에서 동기 수행. 존재·디렉터리 확인에 그치지 않고
  임시 파일 **생성→쓰기→읽기→삭제**까지 실제로 한다. 실패면 422 로 저장 거부.
- **(b) 에이전트 노드별**: 리포트 응답으로 내려가는 프로브 대상(§1-12)에 base 를
  추가해 노드별 exists/writable 을 수집한다. hostPath `type: Directory` 는 존재만
  요구하므로(§1-4) **exists 가 파드 기동 가능 여부의 직접 신호**다. 정직한 한계 표기:
  writable 은 **에이전트 프로세스 uid** 의 W_OK 지 잡 파드 요청자 uid 가 아니다.
  또 `probe_mounts` 의 `status` 는 writable 을 반영하지 않으므로(§1-12) 화면·판정은
  status 가 아니라 **writable 필드를 직접** 봐야 한다.
- **(c) 컨트롤러 자기 관점**: 컨트롤러가 주기적으로 자기 파일시스템에서 base 를
  검증해 결과를 DB 에 남긴다. 이유: 컨트롤러는 `read_summary` 로 실제 **읽기**를
  하는 유일한 프로세스이고, 마운트가 없으면 §1-3 의 "SUCCEEDED 인데 요약이 없는"
  조용한 실패가 난다. 그 실패를 사전에, 화면에 보이게 만든다.

닭-달걀 회피: 저장 전엔 (a)만 강제한다. (b)(c)는 저장 **후** 수렴을 화면이 폴링해
보여준다. "확인 대기 중"을 별도 상태로 구분한다 — 아직 새 경로를 프로브하지 않은
것과 실패를 혼동하지 않는다.

### 2.5 API 표면

- `GET /api/admin/artifact-base`: `effective`, `source`(db|env), `db_value`,
  `env_value`, `locked_by_jobs`(건수), `checks`(api/controller/nodes 3홉).
- `PUT /api/admin/artifact-base`: 정규화(§2.2) → 잠금(§2.3) → 즉석 검증(§2.4a) →
  저장+감사. `force` 는 body 플래그.
- `POST /api/admin/artifact-base/validate`: 저장 없이 (a) 검증만 — UI 의 사전 확인용.

전부 `require_admin`(`routes_control.py` 관례).

## 3. 화면

새 페이지 `/admin/artifact-base`(RequireRole admin — `router.tsx:62` 의
`/admin/control` 과 같은 패턴). **ControlStatePage 에 얹지 않는다**: 3홉 검증 UI 가
커서 그 파일이 두 가지 일을 하게 된다.

- 현재 값: `effective` + `source` 배지(db|env), env 값과 DB 값 병기.
- 검증 패널: API(즉석)/컨트롤러/노드별 3홉 결과. 노드 행은 exists·writable 을 따로
  표시하고, 새 경로 저장 직후에는 "확인 대기 중" 상태를 실패와 구분해 렌더한다
  (§2.4). writable 한계 문구를 화면에 그대로 적는다(에이전트 uid 기준).
- 변경 폼: validate 버튼(저장 없음) → 저장. 잡이 있으면 409 를 받아
  `locked_by_jobs` N 건과 "이 잡들의 아티팩트·로그 열람이 깨집니다"를 확인
  다이얼로그로 강제한 뒤에만 `force=true` 재요청.

## 4. 오류 처리

- 정규화 실패(상대경로·`..`·경로 중간 `file://`)는 422 에 사유 코드를 담는다.
  reason_code 는 뭉개지 않는다 — 무엇이 거부됐는지 화면이 그대로 보여준다.
- 즉석 검증 실패도 422 다. 존재하지 않는 경로, 쓰기 불가, 디렉터리가 아님을 각각
  다른 reason_code 로 구분한다.
- 잠금은 409 `artifact_base_locked` + `affected_jobs` 건수. force 통과는 감사 로그에
  `forced: true` 를 반드시 남긴다(§2.3).
- (b)(c) 검증은 fail-soft 다: 에이전트 미보고·컨트롤러 미기록이면 해당 홉을 "확인
  대기 중"으로 낸다. **null(모름)과 실패를 뭉개지 않는다** — 슬라이스 17 §4 와 같은
  원칙이다.

## 5. 테스트

- resolve: DB NULL → env, DB 설정 → DB 값, 소비자 3곳이 전부 resolve 를 통과하는지
  (생성자 캡처가 남아 있지 않은지 — 어댑터에 base 를 바꿔 주입해 단언).
- 정규화: 후행 슬래시 제거, `..` 거부, 경로 중간 `file://` 거부, 상대경로 거부,
  `file:///` 접두 유지.
- `strip_scheme` 통일: 전체 치환 4곳(§1-9)이 제거됐는지 grep 수준 단언, 접두사가
  아닌 위치의 `file://` 이 경로에서 사라지지 않는지.
- 잠금: 잡 0건 통과 / 1건(artifact_uri NULL 인 Rejected 잡 포함) 409 / force 통과 +
  감사 페이로드 `{forced, affected_jobs}`.
- 즉석 검증: 실제 tmpdir 로 생성→쓰기→읽기→삭제 왕복, 실패 시 저장이 일어나지
  않는지.
- 마이그레이션: CREATE 경로와 `_ensure_columns` ALTER 경로 양쪽(슬라이스 14 의 실
  500 교훈 — `migrations.py:415-416` 주석에 기록된 그대로).
- 프론트: source 배지, 잠금 다이얼로그, "확인 대기 중" vs 실패 렌더 구분.

## 6. 실증 (테스트베드)

1. DB 미설정 → `effective == env`(하위호환: 기존 배포 무변화).
2. 잡 0건에서 변경 성공 → 새 잡이 새 경로 아래에 아티팩트를 쓴다.
3. 잡 있는 상태에서 PUT → 409, force → 통과하고 감사 로그에 `forced` 가 남는다.
4. 없는 경로 → 저장이 422 로 거부된다.
5. **`/cephfs` 밖 경로**(예 `/tmp/x`) → API 즉석 검증은 "쓰기 가능"이라 하지만
   에이전트·컨트롤러 관점이 갈라지는 것이 화면에 보인다(§1-3, §1-4 의 마운트 경계가
   3홉 UI 로 드러나는지) — **이 슬라이스의 핵심 실증**이다.
6. 경로 중간 `file://` 입력이 저장 시점에 거부된다.

## 7. 이 슬라이스에서 하지 않는 것

- **운영 중 이전**(사용 시나리오가 설치·초기 구성 전용 — §2.3 잠금이 그 경계다).
- 옛 아티팩트 파일의 이동/복사(§1-10 — 파일을 만지는 코드 자체를 만들지 않는다).
- 이중 base 조회(옛 경로 fallback 읽기) — §1-8 의 보안 불변식이 단일 base 전제라
  넓히지 않는다.
- 잡별 base 기록 승격(`artifact_uri` 를 읽기 경로 조립에 쓰는 변경).
- 아티팩트 보존/GC.
- 잡 파드 preflight 로 노드에서 직접 `test -w` 하기 — 에이전트 노드 리포트(§2.4b)가
  대리 증거다.
