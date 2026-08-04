# DMS 신규 구현 설계

2026-08-02. 이 문서는 clean-slate 재구현의 방향을 정의한다. legacy(`legacy/`)는 도메인 지식의
출처일 뿐, 아키텍처의 기준이 아니다. 이 문서와 legacy가 다르면 이 문서가 이긴다.

## 0. DMS란

여러 스토리지(CephFS/GPFS/WekaFS 등 POSIX 마운트)에 대한 데이터 관리 작업 — 디렉토리 분석
(dscan), 동기화(dsync/nsync), 삭제(drm) — 을 Kubernetes 위에서 MPI 병렬로 실행하고, 그 전 과정을
포탈에서 운영하는 시스템. mpifileutils 포크(https://github.com/ChahwanSong/mpifileutils.git)의
4개 도구만 사용한다: `dscan`, `dsync`, `nsync`, `drm` (dscan/nsync는 포크 전용).

## 1. 핵심 원칙 (모든 설계 결정의 상위 규칙)

- **상태(DB)가 유일한 진실이다.** 요청 처리 lifecycle은 request → plan → run → result이며, 모든
  전이는 `state_transitions`에 기록된다. 클라이언트는 제출 후 상태를 폴링한다.
- **모든 프로세스는 언제 죽어도 되는 재시작 가능한 루프다.** 각 루프는 `run_once()`를 반복하고,
  진행 상황은 전부 DB에 있다. 메시지 큐 없음, 프로세스 간 직접 통신 없음 — 컴포넌트는 서로를
  모르고 테이블만 본다.
- **외부 세계의 사실은 DB에 캐시돼 있어도 믿지 않는다.** 마운트·도구·신원·Volcano 잡 상태는
  실행 시점에 확인하고, 확인할 수 없으면 진행하지 않는다 (fail-closed).
- **DMS 관련 모든 리소스는 포탈에서 생성·수정·삭제·모니터링할 수 있다.** 컨테이너 이미지의
  빌드와 롤아웃까지 포함한다.
- **모든 거부·실패에는 기계가 읽을 수 있는 사유 코드가 붙는다** (`missing_tool:dsync`,
  `no_eligible_nodes`, `posix_permission_denied`, ...). 조용한 실패 금지 — 설정·권한
  문제는 삼키지 말고 기동 시 검증하거나 상태로 노출한다.

## 2. 전제 (prerequisite)

- 단일 Kubernetes 클러스터 (v1.34+), CRI-O, Cilium, Volcano, PostgreSQL, MetalLB, ingress-nginx
  구성 완료. 멀티 클러스터 지원 없음 — kubeconfig 관리, ssh-kubectl 같은 원격 transport 없음.
- 모든 노드에 관리용 스토리지 디렉토리가 마운트되어 있고, DMS 운영 데이터(아티팩트, 레지스트리
  데이터 등)는 전부 그 아래에 둔다. root-squash 없이 root 읽기/쓰기가 가능해야 한다.
- 로컬 레지스트리는 노드에서 도달 가능한 **고정 엔드포인트**(NodePort 또는 MetalLB IP)로 노출하고,
  전 노드 CRI-O `registries.conf`에 insecure 등록(또는 TLS CA 신뢰)을 부트스트랩에서 완료한다.
  이미지 pull 참조는 항상 이 엔드포인트를 쓴다 — 이미지 pull은 노드의 CRI-O가 수행하므로
  서비스 DNS(`*.svc.cluster.local`) 주소는 동작하지 않는다.
- Volcano queue 1개(`dms-data`) + PriorityClass 3개: `dms-low`(50) / `dms-mid`(100) /
  `dms-high`(200). DMS 워커 노드에 배포되는 모든 잡 컨테이너는 Volcano 스케줄러를 쓴다.

## 3. 시스템 구성

| 컴포넌트 | 형태 | 역할 |
|---|---|---|
| `dms-api` | Deployment ×2 | REST API + 포탈 SPA 정적 서빙 + 세션 인증. 동기 CRUD(스토리지·계정·정책·denylist)와 요청 접수 |
| `dms-controller` | Deployment ×1 | 모든 루프의 숙주: planner, job-stepper, storage-reconciler, build-runner, retention. 루프별 독립 `run_once()`, 리더 리스로 다중 replica 안전 |
| `dms-agent` | DaemonSet | 노드 증거 수집(마운트/도구/identity/OS 메트릭) → API POST |
| `registry` | Deployment ×1 | 로컬 컨테이너 레지스트리(registry:2), 데이터는 관리용 스토리지 아래 |
| 잡 파드 | Volcano Job | launcher + worker(sshd) gang scheduling으로 mpifileutils MPI 실행 |

- 네임스페이스는 `dms` 하나. DB는 PostgreSQL **하나** — legacy의 operational/observability/portal
  3개 DB를 통합하고, 포탈 계정·진단 이벤트도 같은 DB 테이블로 둔다.
- **포탈/API 노출은 ingress-nginx + MetalLB로 HA를 확보한다**: `dms-api`(ClusterIP)를
  ingress-nginx 뒤에 두고, ingress-nginx는 MetalLB LoadBalancer IP로 노출한다. `dms-api` ×2
  replica + LB 조합으로 단일 노드 장애에도 포탈이 살아있다. NodePort 직접 노출은 쓰지 않는다.
- 언어/스택: 백엔드 Python + FastAPI, 프론트엔드 React + Vite + TypeScript. 별도 BFF 없음 —
  `dms-api`가 포탈 백엔드를 겸한다.
- Kubernetes 접근은 kubernetes Python 클라이언트(in-cluster)만. kubectl 서브프로세스 금지.
- 설정은 env var(`DMS_*`)로 하되 개수를 최소화하고, 기동 시 검증한다. placeholder 값
  (`CHANGE_ME` 류)이 truthy로 게이트를 통과하는 구멍을 만들지 않는다.

### 인증

- 포탈: 서명 쿠키 세션. 계정은 운영 DB에 저장하고 role(user/admin) 컬럼이 인터페이스를 결정.
  - 관리자 계정: 운영 토큰(`DMS_ADMIN_TOKEN`)으로 인증해 생성/리셋.
  - 사용자 계정: 셀프 가입. 사내 메일 인증은 **인터페이스만 두고 더미로 구현** (지금은 코드
    검증 없이 생성 가능; 이후 실제 메일 발송으로 교체).
- 내부 컴포넌트(에이전트)와 관리자의 API 직접 호출(디버깅/운영)은 shared token. mTLS 없음 —
  legacy의 이중 API 평면(dms-api/dms-api-internal)을 만들지 않는다.
- LDAP은 로그인에 쓰지 않는다. 오직 잡 실행 신원 해석에만 사용.

## 4. 도메인 모델

### 테이블 (20개)

```
requests, plans, runs, results, state_transitions   ← lifecycle (원칙 그대로)
data_jobs                                           ← 잡 상세: 도구, 경로, preview 지문, volcano ref, artifact uri
storages                                            ← 스토리지 등록: 이름, mount_path, managed_root, 종류, 활성여부, 상태
policies                                            ← 도구별 행(scan/dsync/nsync/rm): 노드 fan-out 상한, 프로세스 수, queue, priority, timeout
identity_denylist, identity_probe_targets           ← 신원 kill-switch + 온디맨드 프로빙 대상
agent_reports(이력), agent_nodes(노드별 최신 1행)
accounts                                            ← 포탈 계정 (role: user/admin)
user_scan_paths                                     ← 사용자가 등록한 scan 조회 경로 (storage, path)
builds, releases                                    ← 이미지 빌드 기록, 컴포넌트별 배포 태그
component_leases, control_state, audit_log, events  ← 리더 리스, 유지보수/드레인, 감사, 진단 이벤트
```

### 상태머신 (legacy 22개 상태에서 필수만)

```
Request:  Pending → Planned → Running → Succeeded | Failed | Rejected | Conflict | Cancelled
DataJob:
  scan:     Pending → Preflight → Running → 터미널
  sync/rm:  Pending → Preflight → PreviewRunning → ConfirmPending → Executing → 터미널
  터미널:   Succeeded / Failed / TimedOut / Cancelled / Rejected / PreviewExpired
```

잡의 `TimedOut`/`PreviewExpired`는 요청 레벨에서는 `Failed`/`Rejected`로 종결되며, 구체 사유는
result의 사유 코드로 남긴다.

### 검증 규칙 (legacy에서 계승)

- 경로는 storage-relative — 해당 스토리지의 **managed_root 기준** 상대 경로다 (절대경로 =
  managed_root + 상대경로). managed_root가 모든 잡 경로의 containment 경계이며, legacy의
  `DMS_DM_PATH_BASE` 같은 기준 전환 설정은 두지 않는다. 선행 `/`, `..`, NUL 금지. sync
  destination이 source와 같거나 하위면 거부. rm은 managed_root 자체(빈 상대 경로) 대상 거부 +
  `recursive` 명시 필수.
- 잡 옵션은 allowlist + 타입/범위 검증. 원시 CLI 문자열 주입 불가.
- 동일 resource_key(경로+옵션 지문)의 미종결 선행 요청이 있으면 `Conflict`.
- `owner_username`은 API 경계에서 POSIX 유저명 정규식 검증 (runuser/chown으로 흘러가므로).

## 5. 데이터 잡 실행

### 비블로킹 스텝 모델

job-stepper 루프가 `FOR UPDATE SKIP LOCKED`로 "진행할 차례인 잡"을 잡아 **한 스텝만** 수행하고
(잡 제출 / Volcano 상태 폴링 / 아티팩트 파싱 중 하나) 상태를 기록한 뒤 놓는다. MPI 잡이 도는 몇
시간 동안 아무 프로세스도 블로킹하지 않는다. 따라서:

- 동시 잡 수는 replica 수와 무관하다 (controller 1개로 수백 잡).
- DB 커넥션은 프로세스당 풀 하나면 된다 (legacy: 워커 32 replica → max_connections 400 요구).
- legacy의 리스 하트비트 스레드, run `Blocked` 파킹, 고아 run 청소 스윕이 모두 불필요하다.
- confirm은 `ConfirmPending → Executing` 상태 전이 API일 뿐, plan 재클레임이 없다.

### Planner 어드미션 (fail-closed)

요청을 플랜으로 바꾸기 전에: 스토리지 미등록/비활성 → `storage_missing`/`storage_disabled`,
신선한 에이전트 증거로 뒷받침되지 않는 스토리지 → `storage_not_ready`, 동일 리소스 선행 요청 →
`Conflict`. 정책 행 존재 검사는 도구가 확정되는 시점에 한다 — scan/rm은 어드미션에서, sync는
도구 선택(preflight) 시점에. 해당 도구의 행이 없으면 `missing_policy`.

### 실행 신원

- preflight 시점에 LDAP **실시간 조회**로 요청자(또는 owner_username)의 uid/gid/그룹을 해석.
  캐시·저장 매핑 없음. LDAP 불능이면 잡은 fail-closed (`ldap_unavailable`).
- **uid 하한은 두지 않는다** (legacy의 MIN_UID/MIN_GID 제거).
- denylist(requester/owner/group, 대소문자 무관)가 최우선 kill-switch. privileged 경로보다 먼저
  평가된다.
- 관리자 root 특권 경로: 관리자가 임의 사용자 데이터를 이관·정리할 때 uid/gid 0으로 실행.
  포탈 관리자 인터페이스에서만 제출 가능. preview→confirm 게이트는 root도 우회 불가.
  - 설정: `DMS_ALLOW_PRIVILEGED_REQUESTERS`(bool)와 `DMS_PRIVILEGED_REQUESTERS`(콤마구분
    actor 목록). **기본값 `true` / `root,admin`** — 인증된 `requester_id`가 이 목록에 있으면
    root로 실행되고 노드-로컬 신원 검사를 건너뛴다. 게이트는 클라이언트가 보내는
    `owner_username`이 아니라 **인증된 `requester_id`** 기준이라 일반 사용자는 승격 불가(403).
    끄려면 `DMS_ALLOW_PRIVILEGED_REQUESTERS=false` 또는 `DMS_PRIVILEGED_REQUESTERS=""`(명시적 빈값).
- 잡 파드에는 NSS가 없어도 되도록, 해석된 uid/gid를 `/etc/passwd`에 물질화한다 (job-runner 담당).
- 에이전트는 리포트 응답으로 받은 프로브 대상 사용자의 노드별 해석 가능 여부를 증거로 보고한다.

### 도구 선택 (자동)

- scan → `dscan`, rm → `drm`.
- sync: 신선한 에이전트 증거에서 source·destination 마운트를 **모두 가진 노드가 1개 이상**이면
  `dsync`, 없고 source 후보와 destination 후보가 각각 1개 이상이면 `nsync` (role 분리:
  source-worker ×N + destination-worker ×M, role별 hostfile, preflight 파드도 role별 2개), 둘 다
  아니면 `no_ready_sync_candidate`로 거부.
- 정책의 fan-out은 **상한**이다: 실제 노드 수 = min(적격 노드 수, 정책 상한). 적격 노드가 상한보다
  적어도 잡은 축소 실행되며, 적격 노드가 0일 때만 거부한다.
- 노드별 탈락 사유를 기록한다: `missing_target_mount`, `missing_tool:<name>`,
  `identity_not_ready_on_node`, `stale_agent_report`, ...

### preview → confirm (sync/rm 필수 게이트)

1. **런타임 preflight**: 잡 이미지로 검사 파드를 띄워 해석된 실행 신원의 uid/gid로 실제 POSIX
   권한을 확인
   (`source_not_readable`, `destination_parent_not_writable`, ...). nsync는 role별 2개 파드 모두
   통과해야 한다.
2. **preview**: `--dryrun` MPI 잡. summary JSON의 sha256 지문을 계산한다. **빈 summary는 지문을
   내지 않으며 confirm 불가.**
3. **confirm**: 요청자가 preview 지문을 제시해야 통과 (불일치 409). preview TTL(기본 24h) 만료 시
   `PreviewExpired` — 새 요청 제출 필요.
4. **execution**: preflight 재검증 후 실제 실행 (confirm 사이에 신원/후보가 변했을 수 있으므로).

### Volcano 실행

- 네이티브 `batch.volcano.sh/v1alpha1 Job` **한 형식만** 지원. **MPI Operator(MPIJob)는 설치도
  사용도 하지 않는다** — legacy의 mpi-operator/auto 폴백 경로를 만들지 않는다. 제출·조회·삭제는
  kubernetes Python 클라이언트로.
- gang scheduling: `minAvailable = worker + launcher`, `plugins: {ssh, svc}`, queue/priorityClass는
  정책에서. launcher ×1 + worker ×N (nsync는 source ×N + destination ×M). 노드 고정은
  nodeAffinity + 워커 anti-affinity(노드당 1개).
- **worker 파드는 sshd만 띄운다.** launcher가 mpirun을 해석된 실행 신원(runuser)으로 실행한다.
- **잡 파드 내부 로직은 셸 문자열 조립이 아니라 잡 이미지에 포함된 `dms-job-runner`(Python)가
  담당한다**: hostfile 대기, SSH 준비 배리어, `/etc/passwd` 물질화, mpirun 실행, summary/로그
  아티팩트 기록. 단위 테스트 가능하고 이미지와 함께 버전된다. 컨트롤플레인은 환경변수(경로,
  도구, 플래그, 프로세스 수)만 넘긴다.
- phase별 타임아웃은 정책 행에서 (기본값: scan 1h / dsync·nsync preview 1h, execution 3d /
  rm preview 30m, execution 1h).
- 우선순위는 요청 시 low/mid/high 중 선택(기본 mid)하고, 정책이 연산별 기본값과 허용 상한을
  정한다. 선택값은 Volcano PriorityClass(`dms-low`/`dms-mid`/`dms-high`)로 매핑된다.

### 아티팩트

```
<관리 스토리지>/artifacts/<job_id>/
  scan/      summary.json, dscan-report.json, stdout.log, stderr.log   # scan 잡
  preview/   summary.json, stdout.log, stderr.log                      # sync·rm
  execution/ summary.json, stdout.log, stderr.log                      # sync·rm
  mpi/       submitted.yaml, hostfile, ...(디버깅 증거)
```
- per-job 디렉토리는 **잡 실행 신원**(preflight에서 해석된 owner uid/gid) 소유·전용(umask 077)
  으로 잠근다. 포탈 열람 권한은 별개로 API 레벨에서 검사한다(본인 잡 또는 관리자).
- 컨트롤플레인(controller/api)은 관리용 스토리지를 마운트하고 root로 아티팩트를 읽는다
  (§2 전제: root-squash 없음). 읽을 때는 symlink containment 가드(realpath가 base 밖이면 거부).
- DB에는 URI와 요약만 저장. scan 결과(dscan 리포트)도 아티팩트에 두고 포탈이 조회한다.

### Cancel

Volcano 잡 종료가 **성공한 뒤에만** DB를 `Cancelled`로 기록한다. 종료 실패 시 취소 실패로 보고
(거짓 취소 금지).

## 6. 스토리지와 에이전트

### 스토리지 등록 (단순화)

- `storages`: 이름(PK), mount_path, managed_root(mount_path 하위 — **잡 경로 해석의 기준점이자
  containment 경계**, §4 검증 규칙 참조), backend 종류(cephfs/gpfs/wekafs — 표시용 메타데이터),
  활성/비활성, 상태. 포탈 관리자 CRUD, 모든 변경은 audit_log에
  before/after 기록.
- legacy의 CSI/StorageClass 조회, sanity 검사 8종, 클러스터 인벤토리 어댑터는 **만들지 않는다**.
  스토리지 상태는 한 가지 질문으로 환원한다: "신선한 에이전트 리포트에 이 마운트가 Ready로
  보이는가" — storage-reconciler 루프가 agent_nodes만 보고 Ready/Degraded/Unknown을 주기 재계산.

### 노드 에이전트

- 프로브: 마운트(`/host/proc/1/mountinfo` 파싱; 존재/읽기/쓰기), 도구 4종 존재·버전, 요청된
  사용자의 identity 해석 가능 여부, OS 메트릭(load/mem/cpu/disk/net).
- 60초 주기로 API POST (shared token + `node:<name>` actor 검증). `agent_reports`(이력) +
  `agent_nodes`(최신 1행) 동시 갱신. 신선도는 저장하지 않고 **읽는 시점에** 판정 (기본 300초).
- **설정 전달은 리포트 응답으로**: POST 응답에 현재 스토리지 목록과 identity 프로브 대상을
  실어 보낸다. legacy의 ConfigMap 동기화 + DaemonSet 재시작 패턴을 제거 — 스토리지 등록 후
  다음 리포트 주기부터 자동 반영된다.

## 7. 이미지 빌드·배포 (포탈 주도)

- **이미지 3종** (legacy 4종에서 포탈 이미지를 dms에 흡수):
  1. `dms-mpifileutils` — 잡 이미지: mpifileutils 포크 빌드 + openmpi + sshd + `dms-job-runner`
  2. `dms` — api + controller + SPA 정적 빌드 포함
  3. `dms-agent` — `dms` 위에 mpifileutils 바이너리 오버레이 (도구 버전 프로브용)
- **로컬 레지스트리**: 클러스터 내 registry:2, 데이터는 관리용 스토리지 아래. 모든 컴포넌트·잡
  이미지는 여기서 pull.
- **빌드 노드 지정**: 인터넷 가능한 노드가 제한적이므로, 관리자가 포탈에서 빌드 노드를 지정한다
  (DB 저장). 빌드는 그 노드에 고정된 K8s Job: git clone(지정 repo/ref) → buildah 빌드 → 로컬
  레지스트리 push. `builds`에 커밋/태그/digest/로그/결과 기록. 빌드 Job은 **privileged**로
  실행한다(관리자 전용 기능, 지정 빌드 노드 한정). 레지스트리가 HTTP면 push는
  `--tls-verify=false`.
- **롤아웃**: 관리자가 포탈에서 컴포넌트별 배포 태그를 선택 → controller가 k8s API로
  Deployment/DaemonSet 이미지를 교체하고 롤아웃 상태를 추적. `releases`가 컴포넌트별 현재/이력
  태그의 진실이다.
- **부트스트랩**: 최초 설치만 리포의 매니페스트를 `kubectl apply` (레지스트리와 초기 이미지는
  수동 반입). 이후 코드 업데이트 → 빌드 → 롤아웃은 전부 포탈에서.

## 8. 포탈

- React + Vite + TypeScript SPA, `dms-api`가 정적 서빙. role에 따라 사용자/관리자 인터페이스
  트리를 분리하고, 프론트 라우팅과 백엔드 API 양쪽에서 role을 검사한다 (`/api/user/*`,
  `/api/admin/*`).
- **사용자**: 잡 제출(sync/rm은 preview 요약 확인 → confirm 클릭 강제) + 자기 잡의 상태/로그/
  결과/수행시간/실패 사유 조회. scan은 직접 실행하지 않는다 — (storage, 경로)를 등록해 두면
  (`user_scan_paths`), 그 경로를 커버하는 **최신 완료 scan의 해당 서브트리 통계**를 조회한다.
  등록 경로의 소유권 검증은 하지 않는다 (노출되는 것은 파일 수·용량·온도 히스토그램 같은 집계
  통계뿐이다).
- **관리자**: 전체 잡 관리(취소, 특권 root 잡), scan 실행/결과, 스토리지 CRUD, 정책, denylist,
  계정 관리, 노드 대시보드(에이전트 신선도·마운트·도구·메트릭), 빌드·릴리스, 컨트롤 상태
  (유지보수/드레인), 감사 로그.

### 디자인 지침 (AI 티 금지)

- **전체 테마는 밝은 계열**로 하고, 심플하고 직관적인(intuitive) 형태를 선호한다. Figma
  Community에서 좋은 예시들을 잘 찾아 참고할 것.
- 최신 프론트엔드 기술을 적극 사용한다 — 프레임워크와 주변 라이브러리(라우팅·서버 상태 관리·
  차트 포함)는 최신 안정 버전 기준.
- 이모지·이모티콘 금지. 왼쪽 선 강조 박스 패턴 금지.
- 상태 뱃지: "왼쪽 점 + 상태 텍스트 둥근 뱃지" 금지. Figma Community에서 직관적이되 흔하지 않은
  상태 표현을 찾아 참고할 것.
- 컬러셋은 Figma Community에서 선정해 참고하되, 정상=초록 / 비정상=빨강 관례는 유지.

## 9. 모니터링

포탈에서 DMS 관련 모든 리소스 상태를 확인할 수 있어야 한다. 대시보드는 두 축으로 설계한다:

- **노드/리소스 대시보드 — Grafana를 운영·디버깅에 쓰듯 구성.** 시계열 그래프 중심: 노드별
  CPU/메모리/디스크/네트워크 추이, 에이전트 신선도, 마운트/도구/identity 증거 상태. 기간 선택과
  노드별 드릴다운을 지원한다. 데이터는 `agent_reports` 이력에서 (retention이 추이 윈도우를
  보장한다).
- **잡 통계 대시보드 — 의미 있는 통계를 최대한 제공.** 기간별 잡 처리량과 성공/실패율, 수행시간
  분포, 큐 대기시간, 도구별/스토리지별/사용자별 분해, 실패 사유 상위 목록, 전송 파일 수·바이트
  추이.

개별 리소스 상세:

- 잡: 상태, 수행시간, 실패 사유 코드, preview/execution 요약, 로그(launcher 파드 로그 tail +
  아티팩트 stdout/stderr).
- 인프라: 컴포넌트 배포 상태(이미지 태그, replica, 롤아웃), Volcano 큐/우선순위, 레지스트리·빌드
  이력.
- 진단: 요청 단위 correlation(`events` 테이블, request_id 기준), 상태 전이 이력, 감사 로그.

별도 모니터링 스택(Prometheus/Grafana 배포)은 이번 범위 밖 — 포탈 대시보드가 그 역할을 대신한다.
데이터 출처는 DB(에이전트 리포트 이력 포함)와 k8s API다.

## 10. 테스트 전략

- SQL은 저장소 계층에 모으고 SQLite/PostgreSQL 양쪽 호환으로 작성 — 전체 스위트가 서비스 없이
  SQLite로 돈다 (`FOR UPDATE SKIP LOCKED` 등 PG 전용 경로는 방언 분기).
- 외부 세계(k8s/Volcano, LDAP, 파일시스템 프로브)는 어댑터 경계 뒤에 두고 테스트는 stub 페어로.
- `dms-job-runner`는 순수 Python이므로 단위 테스트 대상.
- 프론트는 컴포넌트 테스트 + 핵심 플로우(로그인, 제출→confirm) 위주로 가볍게.

## 11. Legacy에서 의도적으로 버리는 것

코딩 에이전트는 다음을 legacy에서 발견해도 **재현하지 말 것**:

- DB 3개 (observability/portal DB 분리), 포탈 BFF·포탈 전용 오케스트레이터 4종
- 멀티 클러스터: kubeconfig Secret, ssh-kubectl transport, 원격 클러스터 RBAC
- mTLS 이중 API 평면 (dms-api / dms-api-internal), ingress client-CA 검증
- kubectl 서브프로세스 호출, MPI Operator(MPIJob)·auto 스케줄러 폴백
- 블로킹 워커 + 리스 하트비트 + run Blocked 파킹 + 고아 정리 스윕 3종
- 수백 줄 셸 스크립트 f-string 조립 (job-runner로 대체)
- uid/gid 하한 (MIN_UID/MIN_GID)
- 이메일 6자리 코드 실구현 (더미로 대체), CSI/StorageClass sanity 검사
- 에이전트 스토리지 ConfigMap 동기화 + rollout-restart 엔드포인트
- 사용하지 않는 LifecycleState/OperationKind, `identity.*` 연산, RM 잔재 전부

## 12. 저장소 구성 (제안)

```
src/dms/            # 백엔드: api/, controller/(루프들), adapters/, repositories/, domain.py
src/dms_job_runner/ # 잡 파드 런처 (잡 이미지에 포함)
portal/             # React SPA
images/             # Dockerfile 3종 + build-images.sh (프록시/CA build-arg 지원)
install/            # 부트스트랩 매니페스트 + 설치 문서 (최소화)
tests/
docs/
```
