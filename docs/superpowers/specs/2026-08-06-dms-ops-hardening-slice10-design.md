# DMS — 슬라이스 10 (운영 안정화 묶음) 설계

2026-08-06. 슬라이스 4~9가 리뷰에서 남긴 후속 항목과, 갭 분석이 잡아둔 자잘한 결함들을 한 번에
정리하는 슬라이스다. 상위 스펙 `2026-08-02-dms-clean-slate-design.md`가 상위 규칙이다.

새 기능을 거의 만들지 않는다. **이미 있는 것이 제대로 동작하게** 만드는 것이 목적이다.

## 0. 담는 것

| # | 항목 | 근거 |
|---|---|---|
| 1 | 종료된 Volcano Job / preflight Pod **GC** | 슬라이스 7 명시적 비목표로 남긴 것. 클러스터에 잔해가 무한 축적된다 |
| 2 | 정책 `default_priority`를 **제출 기본값으로 적용** | 슬라이스 4 리뷰. 지금은 `routes_requests.py`가 `"mid"` 하드코딩 |
| 3 | `dms.io/phase` 라벨 하드코딩 버그 | 슬라이스 5 후속. `exec_preflight` 파드에 `preflight` 라벨이 붙어 summary 경로 재구성이 어긋난다 |
| 4 | `PreviewReady` 배치도 취소 가능하게 | 슬라이스 7 리뷰. 취소가 실제로 종료하게 된 지금은 막을 이유가 없다 |
| 5 | `get_job_logs`의 tail이 `splitlines()` | 슬라이스 5 후속. 아티팩트 경로는 이미 `split("\n")`로 고쳤는데 로그 경로만 남았다 |
| 6 | `batches.list()`가 `options`를 복원하지 않음 | 슬라이스 2 후속. 목록만 raw JSON 문자열을 준다 |
| 7 | `reset_failed_items`가 트랜잭션 밖 | 슬라이스 2 후속. 크래시 시 부분 상태 |
| 8 | **dead config 제거** — `DMS_JOB_MAX_ATTEMPTS` | 소비처 0건. 스펙에 재시도 요구가 **없다** |
| 9 | 감사 화면에 `mutation_class` 컬럼 | 슬라이스 9 리뷰. 계정 변경이 `role`/`disabled` 만으로 표시돼 맥락이 없다 |
| 10 | `react-router` moderate 취약점 | 런타임 의존성 |

## 0.1 비목표 — 명시적으로 하지 않는 것

- **자동 재시도 구현**. 상위 스펙에 재시도 요구가 없고, 실패한 `rm`/`sync`를 자동 재실행하는 것은
  파괴적이다. 포탈에는 이미 배치 `:rerun-failed`와 사용자 재제출이 있다. 따라서 **설정을 지운다**
  (§1.8). 아무 일도 하지 않는 설정을 남기면 운영자가 재시도가 된다고 믿는다.
- **유지보수 중 `BatchOrchestrator` 정지**. 슬라이스 4 최종 리뷰가 명시적으로 반대했다 — 상태
  전이도 reason_code도 없이 조용히 멈추는 것은 지금보다 나쁘고, planner·stepper까지 막으면
  `drain`을 다른 이름으로 재구현하는 꼴이다. 대신 배너가 사실을 말하도록 이미 고쳤다.
- **감사 actor 접두사**(토큰 인증 시 `token:` 표시) — 슬라이스 9 리뷰가 accepted limitation으로
  둔 것. 별도 판단이 필요하다.
- `events` 테이블 활성화, ErrorBoundary — 각각 별도 슬라이스.

## 1. 설계

### 1.1 GC (컨트롤러 신규 루프 + 매니페스트 1필드)

두 종류의 잔해가 남는다:

- **Volcano Job**: `Job.spec`에 `ttlSecondsAfterFinished`가 **실재한다**(v1.15.0 CRD의 허용
  필드 목록에 포함 — 슬라이스 7에서 확인). 매니페스트에 넣으면 Volcano가 알아서 지운다.
  값은 신규 설정 `DMS_VCJOB_TTL_SECONDS`(기본 86400 = 1일)로 둔다. 아티팩트는 별도로 남으므로
  진단은 계속 가능하다(슬라이스 5).
- **preflight Pod**: 베어 파드에는 TTL이 없다. **컨트롤러 GC 루프**를 새로 만든다:
  - `data_jobs`에서 **종단** 잡을 훑어 `phase_refs`의 `pod/`·`pods/` ref를 모은다.
  - 잡이 종단이 된 지 `DMS_POD_GC_AFTER_SECONDS`(기본 3600) 이상 지났으면
    `execution_adapter.terminate(ref)`로 지운다(`terminate`는 404를 삼키는 멱등 계약이다).
  - 지운 ref는 다시 지우지 않도록 **`phase_refs`에서 제거**하지 않는다 — 진단에 필요하다.
    대신 잡별로 GC 완료를 표시할 컬럼을 만들지 않고, **최근 N건만** 훑고 실패는 무시한다
    (멱등하므로 중복 삭제는 무해하다). 루프 주기는 기존 컨트롤러 루프 관례를 따른다.
  - 리스 기반 단일 실행(`component_leases`)을 기존 루프와 동일하게 적용한다.

**안전장치**: GC는 **종단 잡만** 대상으로 한다. 비종단 잡의 파드를 지우면 stepper가 그것을
실패로 오인한다.

### 1.2 `default_priority` 적용

`routes_requests.py`의 `submit`이 `body.priority`를 그대로 쓰는데, `RequestBody.priority`의
기본값이 `"mid"`라 정책의 `default_priority`가 무시된다.

- 클라이언트가 **명시하지 않았을 때만** 정책 값을 쓴다: `RequestBody.priority`의 기본값을
  `None`으로 바꾸고, `None`이면 그 연산의 정책에서 `default_priority`를 읽는다.
- 정책이 없거나 값이 없으면 `"mid"`로 폴백한다(현행 유지).
- 연산 → 정책 키 매핑은 `placement.TOOL_TO_POLICY`의 역방향이 아니라 **연산 자체**로 정한다:
  `scan`→`scan`, `rm`→`rm`, `sync`→ 도구가 preflight에서 정해지므로 **제출 시점에는 알 수 없다**.
  따라서 sync는 `dsync` 정책의 `default_priority`를 쓴다(두 sync 도구의 기본값이 다르면 dsync를
  대표로 삼는다 — 설계 결정으로 기록).
- 프론트는 지금처럼 항상 값을 보내므로 동작이 바뀌지 않는다. 배치 orchestrator는
  `priority="mid"` 하드코딩인데(`batch_orchestrator._materialize`) 같은 규칙을 적용한다.

### 1.3 `dms.io/phase` 라벨

`execution_manifests.py:291`이 preflight Pod 라벨을 `"preflight"`로 하드코딩한다. `exec_preflight`
파드에도 같은 라벨이 붙어, `_reconstruct_summary_path`가 컨트롤러 재시작 후 잘못된 phase 경로를
만든다. `spec.phase`를 쓰도록 고친다(Volcano Job 쪽은 이미 그렇게 돼 있다).

### 1.4 `PreviewReady` 배치 취소

`cancel_batch`의 상태 가드가 `("Previewing", "Running")`인데, `PreviewReady`는 자식들이
`ConfirmPending`으로 미리보기를 붙들고 있는 상태다. 허용 목록에 추가한다.

### 1.5 로그 tail 공용화

`artifacts.py`에 `tail_lines(text, n)`를 만들고(`split("\n")` 기반, `MAX_TAIL_LINES` 클램프
포함), `read_artifact`와 `routes_artifacts.get_job_logs`가 **둘 다** 그것을 쓴다.

### 1.6 `batches.list()` options 복원

`get`/`list_active`는 `load_json`으로 복원하는데 `list`만 안 한다. 맞춘다.

### 1.7 `reset_failed_items` 트랜잭션

`with self._db.transaction():`으로 감싼다.

### 1.8 dead config 제거

`config.py`의 `job_max_attempts`와 `DMS_JOB_MAX_ATTEMPTS` 매핑, `deploy/k8s/20-config.yaml`의
항목을 **삭제**한다. §0.1의 이유를 커밋 메시지와 이 문서에 남긴다.

### 1.9 감사 화면 컬럼

`AuditLog.tsx`에 `mutation_class` 컬럼(맨 앞)을 추가한다. 타입에는 이미 있다.

### 1.10 의존성

`react-router`를 취약점이 해소된 버전으로 올린다. 라우팅은 앱 전체가 쓰므로 **전체 프론트
테스트가 통과해야** 한다.

**후속(2026-08-06, Task 6에서 조사·이연):** `npm audit --omit=dev` 기준 패치된 라인은
`react-router-dom` **7.18.0+**뿐이다 — 이미 설치된 6.x 최신(6.30.4)도 여전히 취약으로
플래그된다. 즉 이 취약점의 수정은 **react-router v7 메이저 업그레이드**를 요구하고,
`npm audit fix`(`--force` 포함, 스크래치 사본에서 검증)로도 메이저 경계를 넘지 못해
자동 해결되지 않는다. 라우팅은 앱 전체에 걸쳐 있어 메이저 업그레이드 범위가 이 슬라이스를
넘어서므로, 수정을 **별도 슬라이스로 이연**한다. 그때까지 moderate 등급 권고 **2건**이
남는다(수용된 리스크).

## 2. 테스트

- GC: 종단 잡의 pod ref가 GC 대상이 되고 **비종단 잡은 대상이 아니다**; 나이 기준이 지켜진다;
  `terminate` 실패는 루프를 죽이지 않는다; 리스가 없으면 돌지 않는다. 매니페스트에
  `ttlSecondsAfterFinished`가 설정값대로 들어가고, 설정이 0/None이면 키가 없다.
- `default_priority`: 클라이언트가 priority를 **주지 않으면** 정책 값이 쓰이고, **주면** 그 값이
  이긴다; 정책이 없으면 `mid`.
- phase 라벨: `exec_preflight` 파드의 라벨이 `exec_preflight`다.
- `PreviewReady` 배치 취소가 200이고 자식이 종료된다.
- `tail_lines`가 `\r`를 쪼개지 않고 `MAX_TAIL_LINES`를 클램프한다; 로그 라우트가 그것을 쓴다.
- `batches.list()`의 `options`가 dict다.
- 프론트: 감사 화면에 클래스 컬럼; 라우터 회귀 없음.

## 3. 배포/실증

마이그레이션 변경 없음. **컨트롤러에 새 루프가 생기므로 controller 재배포 필수**. 이미지 d19.

실증: scan 잡을 하나 돌려 종료시킨 뒤 (a) vcjob에 `ttlSecondsAfterFinished`가 실려 있는지,
(b) GC 루프가 종단 잡의 preflight 파드를 실제로 지우는지 `kubectl get pods`로 확인. priority를
생략한 제출이 정책 기본값을 받는지. `exec_preflight` 파드 라벨 확인. 감사 화면 컬럼 확인.

## 4. 결정 기록

- **자동 재시도는 구현하지 않고 설정을 제거한다** — 스펙 근거가 없고 파괴적 연산에 위험하다.
- GC는 vcjob은 `ttlSecondsAfterFinished`, preflight Pod은 **컨트롤러 루프**로 나눈다.
- GC는 **종단 잡만** 대상으로 한다.
- sync의 `default_priority`는 **dsync 정책**을 대표로 읽는다(제출 시점에 도구 미확정).
- 유지보수 중 오케스트레이터 정지는 **하지 않는다**(슬라이스 4 리뷰 결정 유지).
- **알려진 결합(대응 없음, 기록만):** `poll()`은 "object not found"를 `FAILED`로 매핑한다.
  새 vcjob TTL(`DMS_VCJOB_TTL_SECONDS`) 하에서, 잡이 완료된 뒤 stepper가 폴링하지 않는
  동안(예: `drain`이 주말 내내 걸려 있거나, 컨트롤러가 24시간 넘게 정지) TTL이 vcjob을
  GC해버리면, 나중에 폴링한 stepper는 `None`을 받아 그 잡을 **`Failed`로 확정**한다 —
  성공한 `rm`이 실패로 오보고되는 경우다. drain 또는 컨트롤러 정지가
  `DMS_VCJOB_TTL_SECONDS`보다 길면 완료된 잡이 Failed 로 오분류될 수 있다 — 장기 drain
  전에는 TTL 을 늘리거나 drain 을 짧게 유지한다. 이번 슬라이스에서는 `poll()`의
  not-found 처리 자체를 바꾸지 않는다(별도 슬라이스 후보).
