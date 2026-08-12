# 슬라이스 17 — 큐 가시성 설계

두 가지를 낸다: (A) 대시보드의 **Volcano 큐 현황**(대기 중 잡, 대기 시간, 통계),
(B) **전역 대기 통계**. (B)는 슬라이스 14가 "전기간 `state_transitions` 풀스캔"을
이유로 미룬 항목이다.

## 1. 실측으로 확인한 전제

병렬 정찰(4갈래)로 코드·클러스터를 직접 확인했다.

1. **PodGroup 은 잡이 끝나면 삭제된다.** 6일 된 `Completed` vcjob 이 20여 개인데
   `kubectl get pg -A` 는 0건이다. → **살아 있는 동안만** 대기 정보가 있고, 끝난
   잡의 대기 시간은 **소급 불가**다.
2. **`scheduling.volcano.sh` 권한이 RBAC 에 아예 없다**(`10-rbac.yaml` 의 Role
   `dms-api`·`dms-controller` 둘 다). 지금 어떤 큐/PodGroup 읽기도 **런타임 403**이다.
   `queues` 는 **cluster-scoped**(ClusterRole 필요), `podgroups` 는 namespaced.
3. **`dms-data` 큐에는 `capability` 도 `deserved` 도 없다**(실측: spec 은
   `{dequeueStrategy, parent, reclaimable, weight:1}` 뿐). `default` 도 weight 1 이라
   비율 환산도 불가. → **표시할 용량 숫자가 존재하지 않는다.**
4. **v1.15.0 vcjob 의 `status.conditions[]` 에는 `type`/`reason`/`message` 가 없다**
   (`{lastTransitionTime, status}` 뿐 — `execution_volcano.py:49-57` 에 이미 문서화된
   결함). → "왜 대기 중인가"는 vcjob 으로는 **구조적으로 못 얻는다.** PodGroup 의
   `status.conditions[]` 에만 있다.
5. **`data_jobs.created_at` 에 인덱스가 없다.** 그래서 `metrics.py` 의
   `created_at BETWEEN` 집계 7개가 **지금도 매 폴링마다 풀스캔**이다.
6. **잡이 실행을 시작한 시각을 남기는 컬럼이 없다.** `data_jobs` 의 시각 컬럼은
   `created_at`/`updated_at`/`preview_expires_at` 뿐이고 `updated_at` 은 매 전이마다
   덮어써진다. `runs` 테이블은 완전히 죽어 있다(읽기·쓰기 0건).
7. **기본 실행 백엔드는 `stub`**(`config.py` 기본값). 개발·테스트·CI 에 클러스터가
   없다 — `StubRolloutRunner` 와 같은 **스텁 페어가 없으면 로컬에서 전부 500**이다.
8. **커스텀 오브젝트 호출이 `_request_timeout` 을 안 넘긴다**
   (`execution_volcano.py:290/301/316`). urllib3 기본은 **무제한**이다.
9. `Database` 는 **RLock 하나로 보호되는 단일 커넥션**이다 — 긴 스캔은 엔드포인트
   하나가 아니라 **API 프로세스 전체**를 멈춘다. 타임스탬프 해상도는 **1초**.
10. **RBAC 를 고정하는 테스트가 0건**이다. 규칙을 잘못 쓰면 배포 전까지 아무 신호가 없다.

## 2. 핵심 결정

### 2.1 (A) 는 **PodGroup 이 코어**, Queue 는 `state` 하나만 읽는다

대기 중 잡과 그 대기 시간은 **PodGroup 에만** 있다(§1-4). PodGroup 은 namespaced 라
기존 Role 에 `podgroups: get,list` 한 줄이면 되고 스코프 확대가 없다. PodGroup 을
세면 `Queue.status` 의 Phase 카운터는 사실상 유도되므로, Queue 를 굳이 읽는 이유는
**`state: Open/Closed` 단 하나**다 — "잡이 왜 하나도 안 도는가"의 1순위 원인이면서
다른 어디서도 알 수 없다. 그래서 ClusterRole 은 만들되
**`verbs:["get"]` + `resourceNames:["dms-data"]`** 로 최소 표면만 연다.

`resourceNames` 는 `list` 에 적용되지 않는다 — 이 저장소가 이미 두 번 적어 둔 함정
이라, Queue 는 반드시 **이름 지정 GET** 이어야 한다.

### 2.2 **용량 게이지는 만들지 않는다**

§1-3 때문에 표시할 진실이 없다. weight 1 을 사용률처럼 그리면 없는 사실을 지어내는
것이다. "40/100 사용 중" 류의 UI 는 이 슬라이스에서 **명시적으로 제외**한다.

### 2.3 (B) 는 `data_jobs.submit_wait_seconds` 파생 컬럼 + 커버링 인덱스

슬라이스 14 가 금지한 근거는 "전기간 풀스캔"이었다. 이 방식은 그 스캔을 **쓰기
시점에 미리 접어** 근거 자체를 없앤다.

> **정정(구현 중 발견)**: 초안의 컬럼명은 `queue_wait_seconds` 였다. 그런데 바로
> 아래 §2.4 는 "이 값은 Volcano 큐 대기가 아니라 DMS 내부 픽업 지연이므로 이름을
> **제출 대기**로 한다"고 못박는다 — 화면 라벨은 "제출 대기", API 키는
> `submit_wait_*` 인데 스키마만 "queue wait" 이면 **앞으로 스키마를 읽는 사람은
> 정확히 우리가 없애려던 오해를 하게 된다.** 슬라이스 14 의 거짓 「큐 대기」 라벨을
> 고치러 온 슬라이스가 같은 거짓을 스키마에 심는 셈이라 특히 앞뒤가 안 맞는다.
> 그래서 `submit_wait_seconds` 로 바꿨다. **배포 전이라 비용 0** 이다 — 이 컬럼은
> 아직 어떤 DB 에도 없고(라이브는 d27, 해당 커밋 미배포) 마이그레이션 호환 처리도
> 필요 없다. 데이터가 쌓인 뒤에는 같은 정정이 비싸진다.

- 컬럼 `submit_wait_seconds BIGINT` 를 CREATE TABLE **과** `_ensure_columns` 양쪽에
  (기배포 DB 는 CREATE 를 다시 안 탄다 — 슬라이스 14 가 실 500 으로 배운 교훈).
- `CREATE INDEX idx_data_jobs_created ON data_jobs (created_at, submit_wait_seconds)`
  (인덱스 이름은 선두 컬럼 기준이라 컬럼 개명과 무관하게 그대로 둔다).
- 쓰기는 `set_job_state` 의 **기존 SELECT 에 `created_at` 을 얹고**, `from_state ==
  'Pending'` 인 엣지에서만 **기존 UPDATE 에 값을 추가**한다 — 추가 statement 0,
  추가 왕복 0. **write-once**(비터미널 재전이가 덮어쓰지 못하게).
- migrate 시점 `state_transitions` 1회 스캔으로 **one-shot 백필**.
- 읽기는 인덱스 커버. **덤으로 §1-5 의 기존 풀스캔 7개가 레인지 스캔이 된다** —
  이 슬라이스는 읽기 비용의 순증이 아니라 **순감**이다.

원시 시각을 남기는 대안(`started_at TEXT`)은 기각한다: 정의를 바꿀 일이 생기면
`state_transitions` 의 엔티티 프리픽스 인덱스로 잡 단위 재계산이 싸므로, 유연성
프리미엄이 "SQL 집계 불가 + 커버링 인덱스에 문자열 2개" 라는 대가를 정당화하지 못한다.

### 2.4 이름을 **정직하게** 짓는다 — 그리고 기존의 거짓 라벨도 고친다

(B) 가 재는 것은 `created_at → 첫 비-Pending 전이`, 즉 **DMS 내부 픽업 지연**이지
Volcano 큐 대기가 아니다. 그래서 이름은 **"제출 대기"** 로 한다.

같은 이유로 **슬라이스 14 가 요청 상세에 붙인 「큐 대기」 라벨도 고친다.** 그 값은
요청 Pending→Planned 의 **플래너 틱 지연**이라 "큐 대기" 라는 이름이 사용자를
오도한다(사용자는 이를 "Volcano 큐에서 기다린 시간"으로 읽는다). 진짜 Volcano 대기는
(A) 의 라이브 뷰에만 있고, 그것을 이력으로 남기는 것은 §7 의 후속 작업이다.

수행시간 분포(`updated_at − created_at`)는 **이 대기를 포함한 전체 수명**이므로,
나란히 두되 화면에 그 포함 관계를 명시한다.

### 2.5 스텁 페어를 **반드시** 함께 만든다

`StubRolloutRunner` 와 정확히 같은 짝을 큐 리더에도 만든다(§1-7). 없으면
`/api/admin/metrics/queue` 가 모든 로컬·CI 환경에서 500 이고, `app.state` 주입 기반
테스트 관례도 못 쓴다.

## 3. 화면

대시보드에 **「큐 현황」 카드**를 「잡 통계」 앞에 자립형으로 추가한다.

- 큐 상태 배지(Open/Closed) — 알 수 없으면 배지를 내지 않는다.
- Phase 별 개수(Pending/Inqueue/Running).
- 대기 중 잡 표: 잡 이름, phase, gang 크기(`minMember`), **대기 시간**
  (`now − creationTimestamp`).
- 「잡 통계」에는 **제출 대기 분포**를 수행시간 분포 옆에 추가하고, 집계 대상 건수
  (NULL 제외 후)를 함께 낸다 — 백필 공백을 화면에서 숨기지 않는다.

**중복 금지**: 처리량·성공률·도구/스토리지/사용자별 분해·실패 사유·처리 항목/바이트는
이미 「잡 통계」에 있다. 큐 섹션에서 재현하지 않는다.

**주의**: 기존 KPI 타일의 「대기」는 `created_at` 24시간 창 한정이라, 무윈도 백로그를
큐 카드가 보여주면 화면의 두 숫자가 어긋난다. 큐 카드는 **라이브 PodGroup** 을 세고
그 사실을 라벨에 드러낸다(「지금 큐에서 대기 중」).

## 4. 오류 처리

**세 상태를 절대 뭉개지 않는다**: 403(권한 없음)·404(CRD 없음/Volcano 미설치)·
정상 빈 결과. 응답은 축마다 독립적으로 null 이 될 수 있게 하고
(`{queue: {...}|null, podgroups: [...]|null}`), **`null` = 알 수 없음**,
**`[]` = 비었음**을 타입에서 구분한다. `[]` 로 뭉개면 권한 누락이 "큐가 한가함"으로
렌더된다.

`Queue.status` 의 Phase 카운터는 `omitempty` 라 **키 부재 = 0** 이다. 반면 CRD 부재·
403 일 때는 0 이 아니라 unavailable 이다 — 같은 "키 없음"이 두 의미를 가지므로
호출 계층에서 구분해 넘긴다.

모든 새 k8s 호출에 `_request_timeout` 을 **명시적으로** 넘긴다(§1-8). 5초 폴링에서
무제한 대기는 스레드풀을 고갈시킨다. 병렬·항목별 fail-soft 는 `metrics_infra` 패턴을
그대로 따른다.

## 5. 테스트

- 큐 리더: 403/404/빈 목록/정상 목록 네 경우가 각각 다른 결과로 나오는지(뭉개짐 금지).
- 스텁 페어가 클러스터 없이 결정적 값을 주는지.
- `submit_wait_seconds`: Pending→ 첫 전이에서만 기록(write-once), 재전이가 덮어쓰지
  않는지, 백필이 기존 행을 채우는지, NULL 이 집계에서 제외되는지, **값 0 이
  "기록됨"으로 취급되어 재-migrate 에 덮이지 않는지**(1초 해상도라 0 은 정상값).
- 마이그레이션: CREATE 경로와 `_ensure_columns` ALTER 경로 **양쪽**, 인덱스 생성.
- **RBAC 계약 테스트를 새로 만든다**(§1-10) — 매니페스트에 `podgroups` 읽기와
  이름 지정 Queue GET 이 실제로 있는지. 지금은 아무것도 이걸 안 붙잡는다.
- 프론트: null(알 수 없음) vs 빈 배열(비었음)이 다르게 렌더되는지.

## 6. 실증 (테스트베드)

1. RBAC 적용 전 → 엔드포인트가 **403 을 알 수 없음으로** 표시하고 빈 큐로 렌더되지
   않는지(먼저 확인해야 의미가 있다).
2. RBAC 적용 후 → 큐 상태 Open, Phase 카운트가 나오는지.
3. 실제 sync 잡을 제출해 **대기 중 PodGroup 이 표에 뜨고 대기 시간이 증가**하는지,
   완료 후 사라지는지(PodGroup 삭제 §1-1 의 귀결을 화면에서 확인).
4. `submit_wait_seconds` 가 신규 잡에 채워지고, 백필이 기존 잡을 채웠는지.
5. 제출 대기 분포가 화면에 뜨고 집계 건수가 함께 표시되는지.
6. 요청 상세의 라벨이 「큐 대기」에서 정정된 이름으로 바뀌었는지.

## 7. 이 슬라이스에서 하지 않는 것

- **용량/사용률 게이지**(§2.2 — 표시할 진실이 없다).
- **끝난 잡의 Volcano 대기 이력.** PodGroup 이 삭제되므로 샘플링을 컨트롤러로 옮겨
  `sched_wait_seconds` 를 같은 컬럼 패턴으로 남기는 **후속 슬라이스**가 필요하다.
- PodGroup `Unschedulable` condition 의 `reason`/`message` **문자열에 의존하는 UI**
  (실물 문자열 미검증 — 표시는 하되 분기하지 않는다).
- 큐 전용 페이지·내비 항목(대시보드 카드 하나로 시작).
- 큐 생성/수정/우선순위 변경 등 **쓰기** 동작.
- `runs` 테이블 부활(§2.3 이 대체).
- Prometheus/알림.
