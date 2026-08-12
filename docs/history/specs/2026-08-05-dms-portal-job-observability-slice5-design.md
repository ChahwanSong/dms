# DMS 포탈 — 슬라이스 5 (잡 관측성: 로그·아티팩트 열람 + 잡 상세) 설계

2026-08-05. Phase 4 포탈의 다섯 번째 슬라이스. 상위 스펙 `2026-08-02-dms-clean-slate-design.md`
§8(포탈)·§9(모니터링)·§5(아티팩트)의 하위 구현 문서. 슬라이스 1~4는 구현·실증·배포 완료.
충돌 시 상위 스펙이 이긴다.

## 0. 배경 & 범위

지금 잡이 실패하면 **원인을 포탈에서 볼 수 없다.** 상위 스펙 §8은 "자기 잡의 상태/로그/결과/
수행시간/실패 사유 조회"를, §9는 "launcher 파드 로그 tail + 아티팩트 stdout/stderr"를 요구하는데:

- `RequestDetail.tsx`는 state·operation·reason_code만 렌더한다. API가 이미 내려주는 `transitions`
  (전이 이력)와 `result_summary`를 버린다.
- 아티팩트(`stdout.log`/`stderr.log`/`summary.json`/`dscan-report.json`)는 파일로 남지만 **조회
  API가 없다**.
- `read_pod_log`는 `execution_volcano.py:242`에 구현돼 있으나 **호출부가 0건**이다. preflight 실패
  사유는 `kubectl logs` 없이는 확인 불가다.
- scan 잡은 `artifact_uri`가 영원히 NULL이다 — `_poll_execution` 성공 경로가
  `set_artifact(artifact_uri=None, ...)`를 호출한다(`stepper.py:136`). preview를 거치는 sync만
  `_poll_preview`에서 URI를 받는다(`stepper.py:171`).

인프라 조건은 이미 갖춰져 있다: `dms-api` 파드가 `/cephfs`를 마운트하고
(`deploy/k8s/40-api.yaml:63-65`), `app.state.execution_adapter`를 보유한다. 신규 인프라는 없다.

### 0.1 담는 것

- **아티팩트 목록·본문 조회 API**(백엔드 신규) — 소유권 검사 + 경로 봉쇄 + 크기 상한/tail.
- **preflight 파드 로그 API**(백엔드 신규) — 기존 `read_pod_log`를 어댑터 포트에 노출.
- **scan 잡 `artifact_uri` 기록 교정**(백엔드 신규) — 성공 경로가 URI를 남기도록.
- **RequestDetail 강화**(프론트) — 상태 전이 타임라인, 수행시간, result_summary, 로그/아티팩트
  뷰어, 로딩·에러 상태.

### 0.2 비목표

- **vcjob launcher 파드 로그** — ref가 `vcjob/<name>`이라 launcher 파드를 라벨로 찾아야 하고, 잡
  종료 후 파드가 사라지면 조회 불가다. execution 단계의 진단은 아티팩트 `stdout.log`/`stderr.log`
  로 충분하다(job-runner가 항상 기록한다 — `dms_job_runner/runner.py:79-80`). 후속 슬라이스.
- 아티팩트 **다운로드**(바이너리 스트리밍)·삭제·보존 정책 UI.
- `user_scan_paths`(사용자 scan 경로 등록 → 서브트리 통계) — 이 슬라이스가 여는 아티팩트 읽기
  경로를 전제로 하는 별도 슬라이스.
- §9 시계열/집계 대시보드.

## 1. 화면 지도

| 화면 | 경로 | 변경 |
|---|---|---|
| 요청 상세 | `/jobs/:requestId`(확장) | 전이 타임라인·수행시간·요약 + 로그/아티팩트 뷰어 |

새 라우트는 없다. 사용자·관리자 모두 접근하되, **API가 소유권을 강제**한다(관리자는 전체).

## 2. 백엔드

### 2.1 아티팩트 조회 API (routes_artifacts.py 신규)

아티팩트 레이아웃은 실측 기준 **2단 고정**이다:
`<artifact_base>/<job_id>/<phase>/<file>` (예: `.../execution/stdout.log`).

```
GET /api/user/jobs/{job_id}/artifacts
    -> [{phase, name, size, modified_at}]           # 파일 목록(2단 평면화)
GET /api/user/jobs/{job_id}/artifacts/{phase}/{name}?tail=N
    -> {phase, name, size, truncated, content}      # 텍스트 본문
```

**경로 탈출은 "정규화 후 검사"가 아니라 "구성으로 불가능하게" 만든다:**

- `phase`는 허용 목록(`preflight`, `preview`, `execution`)에만 매칭. 그 외는 `422 invalid_phase`.
- `name`은 `^[A-Za-z0-9._-]+$`에만 매칭(슬래시·`..`·널바이트 불가). 그 외는 `422 invalid_artifact_name`.
- 경로는 검증된 조각으로 **조립**한다: `join(base, job_id, phase, name)`.
- **심링크 봉쇄(방어 심층화)**: `os.path.realpath(candidate)`가 `realpath(base/job_id)` 아래가
  아니면 `403 artifact_forbidden`. 스펙 §5의 "symlink containment 가드" 요구를 만족한다.
- `job_id`도 `^[0-9a-f]{32}$`로 검증한다(DB에서 가져온 값이지만 경로 조립에 쓰이므로).

**크기 상한**: 기본 상한 256KB. 파일이 더 크면 **끝부분**만 반환하고 `truncated: true`.
`?tail=N`이 오면 마지막 N줄(최대 5000줄). 로그 진단은 대개 끝을 보는 일이다.

**소유권**: `_owned_job`(`routes_jobs.py:24-33`)을 그대로 재사용 — 본인 잡 또는 관리자. 없으면
`404 job_not_found`(존재 여부 자체를 숨긴다).

**아티팩트 base 미마운트**: 디렉터리가 없으면 목록은 빈 배열, 본문은 `404 artifact_not_found`.
500을 내지 않는다 — 컨트롤플레인 오구성이 사용자 화면을 깨뜨리지 않게.

### 2.2 preflight 파드 로그 API

```
GET /api/user/jobs/{job_id}/logs?phase=preflight   -> {phase, ref, entries: [{pod, log}]}
```

- `phase_refs`에서 ref를 읽는다. `pod/<n>`은 1건, `pods/<n1>,<n2>`(nsync 양쪽 preflight)는 2건.
- `vcjob/<n>`은 이 슬라이스 범위 밖 → `409 log_not_available`(아티팩트를 보라는 안내를 프론트가 한다).
- 실행 어댑터 포트(`src/dms/execution.py`)에 **`read_log(ref) -> list[tuple[str, str]]`** 추가.
  - `VolcanoExecutionAdapter`: `pod`/`pods` prefix만 처리, 기존 `k8s.read_pod_log`로 각 파드 읽기.
    파드가 이미 사라졌으면 그 항목의 log는 `null`(전체를 실패시키지 않는다).
  - `StubExecutionAdapter`: 테스트용 스크립트 가능한 반환값.
- 파드 로그도 256KB 상한·tail을 적용한다.

### 2.3 scan 잡 artifact_uri 기록 교정

`stepper._poll_execution`의 SUCCEEDED 경로가 `artifact_uri=None`을 넘기는 것을
`f"{self._settings.artifact_base_uri}/{job['job_id']}"`로 바꾼다(`_poll_preview`가 이미 쓰는 값과
동일한 형식). 이미 preview에서 URI가 기록된 sync 잡은 같은 값으로 덮어써도 무해하다.

### 2.4 기존 그대로

`routes_jobs.py`의 confirm/cancel/list, `_owned_job`/`_owned_request`, data_jobs 리포지토리,
어댑터의 submit/poll/read_summary/terminate는 **변경하지 않는다**(포트에 `read_log`만 추가).

## 3. 프론트엔드

### 훅

- `features/jobs/useArtifacts.ts` — `useArtifacts(jobId)`(목록), `useArtifactFile(jobId, phase,
  name)`(본문, `enabled`로 지연 로드), `useJobLogs(jobId, phase)`.

### 화면 (RequestDetail 확장)

- **요청 카드**: 상태 pill, operation, **수행시간**(`created_at` → 마지막 전이 시각), 요청자.
- **전이 타임라인**: `transitions`를 시간순으로 — from→to, reason_code, actor, 시각. 실패 전이는
  눈에 띄게(`text-bad`).
- **잡 카드**: 기존 confirm/cancel 유지 + `result_summary` 요약(키·값 표) + `artifact_uri`.
- **뷰어**: 잡별로 탭 — `stdout.log` / `stderr.log` / `summary.json` / `dscan-report.json`(있는 것만)
  / `preflight 로그`. 선택 시에만 본문을 조회한다(지연 로드). 본문은 `<pre>` 모노스페이스,
  `truncated`면 "뒷부분만 표시" 배지.
- 로딩·에러 상태를 모두 렌더한다(현재 RequestDetail은 둘 다 없다).

### 배선/타입

- `lib/types.ts`: `ArtifactEntry`, `ArtifactFile`, `JobLogs` 추가. `DataJob`에 `artifact_uri`·
  `result_summary`가 이미 있는지 확인하고 없으면 추가.
- `lib/api.ts` reason_code 맵: `artifact_not_found`, `artifact_forbidden`, `invalid_phase`,
  `invalid_artifact_name`, `log_not_available`("이 단계는 파드 로그를 제공하지 않습니다 —
  아티팩트를 확인하세요") 추가.

## 4. 테스트

- **백엔드(pytest)**: 경로 탈출 시도(`..`, 절대경로, 슬래시 포함 name, 허용 밖 phase) 전부 422/403;
  심링크가 base 밖을 가리키면 403(tmp_path로 실제 심링크 생성); 타인 잡 404, 관리자는 200; 크기
  상한 초과 시 truncated + tail 동작; base 디렉터리 부재 시 빈 목록/404; `read_log`가 `pod`/`pods`
  ref를 처리하고 `vcjob`은 409; 파드 소실 시 부분 null; stepper 성공 경로가 artifact_uri를 기록.
- **프론트(vitest+MSW)**: 타임라인 렌더(전이 순서·reason_code), 수행시간 계산, 탭 전환 시에만
  본문 요청, truncated 배지, 로딩·에러 상태, `log_not_available` 안내.

## 5. 배포/실증 (구현 후)

마이그레이션 변경 없음(스키마 불변) → migrate Job 재실행 불필요. dms 이미지 재빌드(d13) →
dms-api·dms-controller 재배포(stepper 교정이 controller에 있다).

실증: 일부러 실패시킨 preflight의 원인을 **포탈에서만** 확인 → 성공한 scan 잡의
`dscan-report.json`·`stdout.log`를 포탈에서 열람 → 전이 타임라인·수행시간 표시 확인 → 경로 탈출
시도가 거부되는지(`..`, 다른 잡의 job_id) → 타인 잡 접근이 404인지.

## 6. 결정 기록

- 아티팩트 경로는 **검증된 조각으로 조립**한다(정규화 후 검사 아님) + realpath 심링크 봉쇄.
- 아티팩트 레이아웃은 2단 고정(`<job_id>/<phase>/<file>`)으로 API를 단순화한다.
- vcjob launcher 로그는 **범위 밖** — execution 진단은 아티팩트 stdout/stderr로 한다.
- 크기 상한 256KB, 기본은 **끝부분**(tail) — 로그 진단은 끝을 본다.
- 어댑터 포트에 `read_log`만 추가하고 나머지 메서드는 불변.
