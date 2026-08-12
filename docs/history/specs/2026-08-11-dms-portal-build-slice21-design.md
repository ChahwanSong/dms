# 슬라이스 21 — 포탈 빌드 되살리기 설계

포탈 주도 이미지 빌드(슬라이스 11)를 이 테스트베드에서 실사용 가능하게 만든다.
확정 전제(사용자 결정): 빌드 노드는 **워커 노드 하나**를 포탈에서 지정하고, 그 워커는
데이터 잡 풀에서 빠지지 않으며(cordon/전용화 금지), 빌드가 데이터 잡을 굶기거나
축출해선 안 된다. 인터넷은 빌드할 때만 운영자가 그 노드에 연다 — 착수 전 적합성
확인이 필수다.

## 1. 실측으로 확인한 전제

1. **빌드 파드는 이미 nodeSelector 다** — `{kubernetes.io/hostname: node}`
   (`src/dms/build_manifests.py:61`). schedulerName 미지정(default-scheduler),
   tolerations·priorityClassName·resources 전부 부재(`:58-76`), privileged(`:66`),
   restartPolicy Never(`:59`), activeDeadlineSeconds=timeout(`:60`). QoS BestEffort·priority 0.
2. **buildah 저장소는 emptyDir(sizeLimit 20Gi)** 하나로 `/var/lib/containers` 마운트
   (`build_manifests.py:72-75`). 주석 스스로 20Gi 가 측정치 아닌 "경험적 상한"이라
   밝힌다(`:67-71`). emptyDir 이므로 kubelet ephemeral-storage 회계 안이다.
3. **데이터 잡 파드는 전부 BestEffort 다.** `_container()` 는 resources 를 만들지 않고
   (`src/dms/execution_manifests.py:84-91`), preflight(`:297-311`)·launcher(`:337-341`,
   `:379-386`)·worker(`:342-355`,`:387-396`) 어디에도 resources 가 없다. vcjob spec 에
   minResources 도 없다(`:356-362`,`:397-402`).
4. **제어면은 Burstable 이다**: api 100m/256Mi–1cpu/1Gi(`deploy/k8s/40-api.yaml:115-121`),
   controller 동일(`41-controller.yaml:64-70`), agent 25m/64Mi–250m/256Mi
   (`50-agent-daemonset.yaml:135-141`), migrate init 50m/128Mi–500m/512Mi(`40-api.yaml:75-81`).
5. **Volcano 는 아무 리소스도 회계하지 않는다.** 큐 dms-data 는 weight 1·reclaimable 뿐
   capability 없음(`05-volcano-queue-priorityclass.yaml:13-19`, 라이브 spec 동일 실측).
   라이브 스케줄러 actions 는 `"enqueue, allocate, backfill"` — preempt/reclaim 이 없다
   (volcano-scheduler-configmap 실측). 잡 요청이 0 이라 predicates 는 항상 통과한다.
6. **PriorityClass 는 dms-low 50 / dms-mid 100 / dms-high 200** 뿐, 전부
   PreemptLowerPriority(`05-…yaml:22-49`, kubectl get priorityclass 실측). 데이터 잡은
   정책 클램프로 `PRIORITY_CLASS` 매핑(`src/dms/domain.py:61-62`, `placement.py:113-121`),
   stepper 기본 dms-mid(`stepper.py:89`). 없는 클래스 참조는 admission 거절(`05-…yaml:11`).
7. **제출 검증 사슬**(`src/dms/api/routes_builds.py:38-68`): 유지보수 → build_node_not_set
   → unknown_image → invalid_git_ref(각 422) → build_in_progress(409, 빠른 거절 +
   `builds.create()` 트랜잭션 안 진짜 가드 `repositories/builds.py:54-66`, replicas=1
   전제). **노드 적합성 검사는 전무**, repo_url 은 무검증(`routes_builds.py:23,61`).
8. **상태 전이와 사유 코드**: Pending→(BuildWatcher 15초 틱 submit)→Running→종단
   (`src/dms/build_watcher.py:43-52`). watcher 는 `ExecutionError.reason_code` 를 그대로
   `finish(state=Failed, …)` 로 박제한다(`:48-50`) — 코드만 정의하면 전파는 공짜다.
   타임아웃 회수는 created_at 기준 build_timeout(`:68-87`), 종단 실패는
   build_failed(`:89-98`), 파드 소실도 FAILED(`src/dms/build_runner.py:72-73`). 로그는
   64KB 꼬리 박제(`builds.py:10,124-125`).
9. **Pending 은 파드 타임아웃으로 못 잡는다** — activeDeadlineSeconds 는 스케줄 후에만
   발화(`routes_control.py:31-34` 주석 명문화). 유일한 탈출구는 watcher 의
   created_at+7200s generic 회수다(`src/dms/config.py:32-36`).
10. **빌드 노드 후보는 이미 워커 5대다.** PUT control-state 가 trim→None 정규화 후
    agent_nodes 실재 노드만 허용(`routes_control.py:30-36`), 프론트는 자유 입력 금지
    select(`ControlStatePage.tsx:63-74`, `useNodes.ts:4-6`). 에이전트 DaemonSet 은
    toleration 이 없어 워커에만 뜬다 — 요구와 이미 부합, 코드 변경 불요. 백로그
    초안(`BACKLOG.md:190-215`)의 컨트롤플레인 과제는 요구 변경으로 **소멸**했다.
11. **에이전트 리포트에 빌드 적합성 재료가 부족하다.** probe_os_metrics 는 load·memory·
    설정된 스토리지 마운트 statvfs 뿐(`src/dms/agent/probes.py:111-138`) — **노드 루트
    디스크 여유는 어디에도 없고** egress 프로브도 없다(`agent/runner.py:33-45`).
12. **클러스터 실측(2026-08-11 재확인)**: 워커 allocatable 1800m / 1388172Ki(≈1355Mi) /
    36.4GB(eph). requests 합 최대는 w2 425m·490Mi, eph requests 는 전 노드 0.
    evictionHard = memory 100Mi·nodefs 10%·imagefs 15%(configz, soft 없음). dms-w1 fs:
    capacity 40.48GB·available 21.78GB, nodefs=imagefs 동일. 15% 임계 6.07GB →
    eviction 까지 여유 ≈15.7GB — **sizeLimit 20Gi 를 다 쓰기 전에 노드 압박 eviction
    이 먼저 온다.** 유일한 포탈 빌드 기록은 build_failed 였고 "인터넷 미개방"을 사유
    코드로 구분 못 한다는 것이 백로그에 명시돼 있다(`BACKLOG.md:296-303`). builder
    image 는 quay.io(인터넷), job image 는 `pkg-01:5000/dms-mpifileutils:d27`
    (`20-config.yaml:22,90`) — python:3.11-slim 기반(`Dockerfile.mpifileutils:81`)이라
    python3 보장, 워커 캐시 존재, pull 도 레지스트리만 필요하다.

## 2. 핵심 결정

### 2.1 노드 지정·스케줄링 — nodeSelector 유지, requests 로 Fit 검사를 산다

nodeName 핀으로 바꿀 것이 없다 — 이미 nodeSelector+default-scheduler 다(§1-1). 이
구조를 유지한 채 §2.2 의 requests 를 달면 스케줄러 Fit(노드 여유 검사)이 공짜로 붙고,
"지정한 노드에서만 돈다"는 hostname nodeSelector 가 그대로 보장한다. 노드 1대
고정이라 여유 부족은 Pending 이고, 파드 타임아웃이 못 잡는다(§1-9). watcher 타임아웃
회수 분기에서 poll 이 PENDING 이면 generic `build_timeout` 대신 **`build_stuck_pending`**
으로 구분 박제한다(`build_watcher.py:68-87` 분기 추가) — 운영자가 원인을 안다.

### 2.2 리소스 봉투 — cpu 250m/1000m · memory 128Mi/1Gi · eph 10Gi/12Gi

빌드 컨테이너에 명시한다. 수치 근거는 전부 §1-12 실측 역산이다:
- **cpu requests 250m / limits 1000m.** 최혼잡 워커(w2 425m)에서도 675m ≤ 1800m 라
  스케줄이 막히지 않는다. **limit 1000m 이 이 슬라이스의 실질 보호막이다**: 빌드가
  아무리 날뛰어도 allocatable 1800m 중 최소 **800m** 이 잡+제어면 몫으로 남는다(노드
  capacity 는 2코어지만 kube/system reserved 각 100m 을 뺀 1800m 이 기준이다).
  정직한 한계: BestEffort 잡의 cpu.shares 는 2 라 경합 시 뒤로 밀리므로 잡이
  **느려진다** — cpu 는 압축성이라 죽지는 않지만, 느려진 잡이 정책
  `execution_timeout_seconds` 에 걸리면 결국 죽는다. 그 경계는 실측으로만 안다(§6-3).
- **memory requests 128Mi / limits 1Gi.** requests 를 일부러 작게 둔다 — §2.3 의 축출
  랭킹에서 빌드가 "requests 초과" 그룹에 항상 들게 하는 장치다. limit 1Gi 는
  api/controller limit 과 같은 값으로, 노드 총 1.92Gi 에서 그 이상은 빌드 자체가
  노드를 위협한다(M8 재현). cgroup 이 1Gi 에서 빌드를 OOM-kill 하는 것이 **의도된 1차
  방어**다 — 노드 eviction 전에 빌드가 먼저, 혼자 죽는다. npm(vite) 빌드가 1Gi 안에서
  도는지는 실측으로만 확정된다 — §6-2 가 잰다.
- **ephemeral-storage requests 10Gi / limits 12Gi.** limits 는 emptyDir 사용량 포함
  파드 단위 집행이다(sizeLimit 10Gi 보다 크게 둬 sizeLimit 이 레이어 상한, limits 가
  로그·쓰기층 오버플로 캐치). 정직한 한계: eph requests 는 allocatable(36.4GB, 정적)만
  보고 라이브 사용량(18.7GB)을 못 본다 — Fit 은 형식이고 실제 디스크 방어는 §2.4 의
  sizeLimit 과 §2.5 의 프리플라이트 df 다.

### 2.3 축출 순서 — dms-build PriorityClass(10, Never), 데이터 잡은 손대지 않는다

최대 함정(§1-3): 잡이 전부 BestEffort 라 빌드에만 requests 를 주면 kubelet 이 잡을
먼저 축출한다 — 요구가 정반대로 구현된다. kubelet 축출 순서는 ①사용량이 requests 를
초과하는가 ②pod priority ③초과량 순. BestEffort 잡은 요청 0 이라 **항상** ①그룹이고,
빌드도 requests 128Mi(§2.2)를 실압박에선 초과해 같은 그룹 — ②priority 가 가른다:

- **PriorityClass `dms-build` 신설**: value 10(< dms-low 50), `preemptionPolicy:
  Never`, globalDefault false. `05-volcano-queue-priorityclass.yaml` 에 추가하고 빌드
  파드·프리플라이트 프로브 파드에 단다. 지금도 priority 0 으로 잡보다 낮지만(§1-1),
  "빌드는 아무도 선점하지 않고 항상 먼저 죽는다"를 명문화한다. 미적용 시 파드 생성이
  admission 거절이므로(§1-6) 배포 순서에 명시한다.
- **데이터 잡 파드에는 requests 를 주지 않는다(BestEffort 유지).** Burstable 승격은
  Volcano gang(minAvailable)·회계와 얽혀 범위를 넘고, priority 만으로 순서가 보장되므로
  불필요하다. 잔여 창을 숨기지 않는다: 빌드 사용량 128Mi 이하인 순간엔 빌드가 ①그룹
  밖이라 뒤로 가지만, 그 사용량이면 빌드는 압박의 원인이 아니다.
- 역방향(잡이 빌드를 선점)은 kube-scheduler 선점 발화 조건(잡 requests>0)이 없고
  Volcano 는 preempt 액션이 꺼져 있다(§1-5) — 선점에 기대는 설계는 양쪽 다 불가하고
  기댈 필요도 없다: 빌드 봉투(§2.2)가 애초에 노드를 다 차지하지 못한다.

### 2.4 buildah 저장소 — emptyDir 유지, sizeLimit 20Gi→10Gi, 3중 방어

hostPath 로 옮길 문제가 아니라 이미 회계 안이다(§1-2). 문제는 수치다: 20Gi 는 실측
여유 15.7GB(eviction 임계 차감 후)보다 커서 sizeLimit 이전에 노드 eviction 이 먼저
발화한다(§1-12) — 그 시점 축출 후보는 같은 노드 파드 전체다. **sizeLimit 을 10Gi 로
내린다.** 방어는 3중: ① 프리플라이트 df(§2.5)가 착수 전 실여유 확인 ② sizeLimit
10Gi 가 레이어 폭주 시 빌드 파드만 축출 ③ eph limits 12Gi(§2.2)가 잔여 오버플로
캐치. 10Gi 는 "현 노드 여유 공식(§2.5)을 통과하는 최대 봉투"이며 여전히 미측정치다 —
§6-2 가 실제 피크를 재서 재보정한다. 정당하게 10Gi 를 넘는 빌드는 sizeLimit 축출로
죽고 build_failed 로 표면화된다.

### 2.5 적합성 프리플라이트 — 동기(즉답) + 프로브 파드(실검사) 혼합

**동기(API, 제출 시)** — `routes_builds.submit_build` 검증 사슬(§1-7)에 추가:
repo_url 호스트 파싱 불가 → 422 `invalid_repo_url`(egress 프로브 대상을 못 만든다).
빌드 노드 에이전트 리포트 stale → 422 `build_node_report_stale`(`agents.py:24-34` 의
fresh 판정 재사용) — 노드 다운을 즉답으로 거른다. egress·디스크는 동기로 검사하지
않는다 — API 파드는 다른 노드라 무의미하다.

**프로브 파드(비동기, BuildWatcher pending→submit 사이, §1-8 삽입 지점)** — 실제
빌드 노드에서 단발 파드로 검사한다. 실행 preflight 의 마커 선례
(`execution_manifests.py:256-291` 의 `DMS_PREFLIGHT_REASON=` + exit 1)를 따른다:
- **이미지는 `settings.job_image`**(§1-12) — builder image(quay.io)면 노드 캐시가 있을
  때 "이미지는 떴는데 clone 은 실패" 위음성이, 레지스트리 임의 이미지면 "프로브는
  떴는데 빌드 pull 실패" 위양성이 난다. job image 는 워커에 캐시돼 있고 pull 도
  pkg-01 만 필요해 프로브 기동 자체가 인터넷과 무관하다.
- **검사 3종(python3 단일 스크립트)**: ① egress — repo_url 호스트·quay.io(빌더
  이미지)·registry-1.docker.io(베이스 이미지: `Dockerfile.dms:13,24`,
  `Dockerfile.mpifileutils:17`)에 TCP 443 연결(각 5s), 하나라도 실패 →
  `build_node_no_egress`(로그에 실패 호스트 전부). ② pkg-01:5000 연결 실패 →
  `build_registry_unreachable`. ③ `os.statvfs("/")` 노드 fs 여유(컨테이너 overlay 는
  노드 fs 를 그대로 보고) — `avail ≥ 0.15·total + 10Gi(sizeLimit) + 2Gi(마진)` 미달 →
  `build_node_disk_low`(로그에 실측 바이트). 0.15 는 evictionHard 실측(§1-12)의 미러
  상수(kubelet 변경 시 함께 갱신, 주석 명기). 실측 대입: 21.78GB ≥ 18.96GB 통과(여유 2.8GB).
- **워처 상태기계**: 프로브 파드 이름은 결정적(`dms-build-pf-<build_id[:12]>`), ref 는
  기존 `buildpod/` 프리픽스 재사용(`build_runner.py:20-24`) — poll/read_log/terminate 가
  공짜다. pending 빌드 틱마다: 없음→생성(AlreadyExists 관용, `build_runner.py:50-62`
  선례) / 진행 중→대기하되 created_at 이 신규 설정 `DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS`
  (기본 180s)를 넘으면 `build_preflight_timeout` / Failed→마커를 **화이트리스트로만**
  코드 채택(밖이면 `build_preflight_failed`) / Succeeded+`DMS_PREFLIGHT_OK`→빌드 파드
  제출·mark_running. 실패 시 프로브 로그를 finish 의 log_text 로 박제(64KB 규칙).
  종단 빌드의 pf 파드는 pod_gc 가 같은 창으로 수거(`pod_gc.py:39-47` 확장). 비동기라
  API 동기 경로가 안 길어진다 — 활성 빌드 1개 가드(§1-7)의 경합 창도 안 넓어진다.
- 정직한 한계: TCP 443 연결은 "인터넷이 열렸는가"라는 운영 모델의 이진 질문에 답한다.
  선별 개방(예: github 만)이면 통과 후 npm 에서 죽는다 — 기존대로 build_failed + 로그.
- **실증에서 발견해 고친 것 — 프로브가 검사하는 경로와 빌드가 쓰는 경로가 갈렸다.**
  빌더 이미지(`DMS_BUILD_BUILDER_IMAGE`)가 quay.io 를 직접 가리키면 그 pull 은
  kubelet/CRI-O 가 **노드 네트워크**로 수행한다(게다가 imagePullPolicy: Always 라 매
  빌드마다). 프로브는 **파드 네트워크**로 검사하므로 이 경로를 못 본다 — 실증에서
  노드 egress 만 막았더니 프로브는 통과했는데 빌드 파드가 ImagePullBackOff 로 앉는
  상태가 실제로 재현됐다(kubelet 이벤트: `quay.io:443 connection refused`). 그 상태는
  §2.1 의 `build_stuck_pending` 이 결국 잡지만 2시간 뒤다.
  → **빌더 이미지를 로컬 레지스트리 미러(`pkg-01:5000/buildah:stable`)로 바꾼다.**
  그러면 노드는 인터넷 없이 빌더를 받고, 남는 인터넷 수요(git clone·npm·PyPI·apt)가
  전부 빌드 파드 안으로 모여 **프로브가 검사하는 경로와 실제 필요 경로가 일치한다**.
  프리플라이트의 레지스트리 검사가 이 경로까지 덮는 것도 부수 효과다.

### 2.6 실패 사유 코드 — 8종 신설, 양쪽 등록

`invalid_repo_url`·`build_node_report_stale`(동기 422), `build_node_no_egress`·
`build_registry_unreachable`·`build_node_disk_low`·`build_preflight_timeout`·
`build_preflight_failed`(프로브→finish 박제), `build_stuck_pending`(§2.1). 전부
`frontend/src/lib/reasonCodes.json`(`:9,27-29` 옆)과 `api.ts`(`:114-124` 옆) 양쪽 등록 —
계약 테스트 조건. watcher 가 코드를 그대로 박제하므로(§1-8) 백엔드 전파 코드는 0 에
가깝다.

## 3. 화면

- **ControlStatePage 는 무변경** — 워커 select 가 이미 요구와 부합한다(§1-10).
- **BuildsPage 제출 폼**: 신규 422(`build_node_report_stale`, `invalid_repo_url`)가
  기존 ApiError 경로로 흐른다 — `api.ts` 문구만 추가.
- **BuildDetail**: reason_code 는 이미 `reasonText` 로 렌더된다(`BuildDetail.tsx:35`).
  핵심 문구 — `build_node_no_egress`: "빌드 노드에서 인터넷으로 나갈 수 없습니다 —
  운영자가 인터넷을 아직 열지 않았을 수 있습니다". 이것이 "안 열었다를 즉시 안다"의
  실체다. 실패 빌드의 로그 뷰(/log)에 프로브 로그가 실린다(막힌 호스트·디스크 바이트).
- Pending 캡션 한 줄: "적합성 확인(프리플라이트) 포함 — 최대 약 3분". 별도 상태 기계는
  만들지 않는다 — 실패는 어차피 고유 코드로 드러난다.

## 4. 오류 처리

- 프로브 마커는 화이트리스트 밖이면 `build_preflight_failed` 로 접는다 — 파드 로그는
  신뢰 입력이 아니므로 코드를 지어내지 않는다(§2.5).
- 프로브 파드가 스케줄조차 안 되면(노드 다운 등) 로그 없이 `build_preflight_timeout` 이
  잡는다 — log_text=None 은 COALESCE 라 기존 값을 안 지운다(`builds.py:129`).
- 빌드 OOMKilled(1Gi limit)·sizeLimit 축출은 파드 phase Failed 로 접혀 기존
  `build_failed` 가 된다 — poll 이 phase 만 본다(`build_runner.py:65-74`). 로그가
  급단절된 build_failed 는 OOM/축출을 의심하라고 운영 문서에 적고, 사유 세분화는 §7 로
  미룬다 — 숨기지 않고 미룬다.
- 프로브 생성 실패(k8s API 오류)는 기존 `submit_failed` 재사용 + detail 구분.
- 스텁 경로는 프리플라이트 포함 즉시 성공 유지 — 로컬·CI 가 클러스터 없이
  초록이어야 한다(`build_runner.py:94-113` `StubBuildRunner` 선례).

## 5. 테스트

- 매니페스트: requests/limits 3종 수치, priorityClassName dms-build, sizeLimit 10Gi,
  eph limit ≥ sizeLimit 관계 단언, nodeSelector·schedulerName 미지정 유지.
- 프로브 매니페스트: job_image 사용, 대상 호스트 env(repo_url 호스트 파싱 포함),
  작은 봉투, dms-build 클래스, 결정적 이름 63자 절단.
- 워처 상태기계: 프로브 없음→생성 / 진행 중→대기 / 성공 OK→빌드 제출+mark_running /
  실패 마커→고유 코드+프로브 로그 박제 / 화이트리스트 밖→build_preflight_failed /
  프리플라이트 타임아웃 / Pending 타임아웃→build_stuck_pending(RUNNING 은 build_timeout).
- 라우트: repo_url 호스트 불가 422, stale 노드 422 — fresh 경계값(정확히 stale 문턱).
- pod_gc: 종단 빌드 수거 시 pf 파드 삭제, 비종단 빌드의 pf 파드 불가침.
- 사유 코드 계약: 신설 8종 양쪽 등록(`tests/test_reason_codes_coverage.py`).
- 스텁 경로: 프리플라이트 포함 제출→Succeeded 가 클러스터 없이 도는지.

## 6. 실증 (테스트베드)

1. **egress 가 막힌 상태**로 빌드 제출 → 수 분 안에 `build_node_no_egress` + 로그에
   실패 호스트 목록. 2시간 generic 타임아웃이 아니어야 한다(§1-12 재현·구분) — 핵심.
   **재현 방법(운영 조건 변경 반영)**: 이 테스트베드는 워커 5대 모두 인터넷이 열려
   있으므로 "안 연 상태"가 자연 상태가 아니다. 대상 워커에서 되돌릴 수 있는 조작으로
   막는다 — `iptables -I OUTPUT -p tcp --dport 443 -j REJECT`(검증 후 `-D` 로 제거).
   443 만 막으므로 apiserver(6443)·레지스트리(pkg-01:5000)·kubelet 경로는 살아 있어
   프로브 파드는 정상 기동한다 — 그래서 이 조작이 정확히 "인터넷만 없는 노드"를
   만든다(§2.5 가 job_image 를 고른 이유가 여기서 값어치를 한다).
2. 인터넷 개방 후 재제출 → 프리플라이트 통과, 실 빌드 성공(3 이미지 push·commit_sha).
   빌드 중 `kubectl exec … du -s /var/lib/containers` 를 주기 샘플해 **emptyDir 피크
   사용량을 실측**, §2.2/§2.4 수치를 재보정해 기록한다.
3. **빌드가 도는 동안 sync 잡을 제출**(핵심): 잡 정상 완료, 잡 파드 축출 0건(kubectl
   get pod -w), 그리고 **두 수치를 평시와 대조**한다 — `sched_wait_seconds`(슬라이스
   20 컬럼)와 **잡 총 수행시간**. 후자가 필요한 이유: cpu 경합은 잡을 죽이지 않고
   **느리게** 만드는데, 느려진 잡이 정책 `execution_timeout_seconds` 에 걸리면 결국
   죽는다(§2.2 의 정직한 한계). 수행시간이 타임아웃의 몇 %까지 갔는지를 숫자로
   남겨야 "굶기지 않는다"가 증명된다 — 축출 0건만으로는 부족하다. 평시 기준선은
   빌드를 돌리지 않은 상태에서 같은 잡을 한 번 돌려 먼저 잡는다.
4. 축출 방향 실증: memory limit 을 일시 256Mi 로 낮춘 빌드를 제출해 빌드만 OOM-kill
   로 죽고(build_failed) 동시 진행 중인 sync 잡이 무사한지 — "빌드가 항상 먼저
   죽는다"(§2.3)의 방향 검증.
5. 노드 로컬에 큰 파일을 만들어 여유를 공식(§2.5) 아래로 → `build_node_disk_low`
   즉시 실패 + 로그에 실측 바이트, 파일 제거 후 통과.
6. pkg-01:5000 일시 차단 → `build_registry_unreachable` 로 egress 실패와 구분되는지.

## 7. 이 슬라이스에서 하지 않는 것

- **데이터 잡 파드에 requests 부여(Burstable 승격)** — §2.3 이 priority 로 순서를
  보장하므로 불필요, Volcano gang·회계와 얽혀 별도 슬라이스 감이다.
- cordon/taint/전용화(요구사항 금지), 프리플라이트 결과의 별도 저장/화면 패널(실패는
  사유 코드·로그 박제로 충분).
- 에이전트 리포트에 노드 루트 디스크·egress 프로브 추가 — 시점 민감 검사는 착수 직전
  프로브 파드가 실측하는 편이 정확하다(§2.5). 리포트 확장은 후속.
- OOMKilled/Evicted 를 build_failed 에서 세분화하는 사유 코드(§4 에 한계 명시).
- 봉투 수치의 설정화(env 튜너블) — 상수+주석 유지, §6-2 실측으로 후속 재보정.
- pkg-01 podman 우회 삭제 — 실증 통과 뒤 백로그(`BACKLOG.md:296-303`) 갱신으로.
- 클러스터 내 registry 구축, 빌드 동시 2개 허용(replicas=1 전제 유지).
- 백로그 초안(`BACKLOG.md:190-215`)의 컨트롤플레인 toleration·검증 완화 — 워커 노드
  확정으로 과제가 소멸했다. 초안대로 구현하지 말 것.
