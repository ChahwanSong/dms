# DMS Phase 1 Implementation Prompt

이 문서는 `docs/dms-design.md`를 기반으로 첫 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. 목적은 전체 기능을 한 번에 완성하는 것이 아니라, 이후 phase들이 안전하게 확장할 수 있는 DMS 전체 스켈레톤과 핵심 아키텍처 계약을 먼저 구현하는 것이다.

## Phase 1 목표

DMS(Data Management Service)는 멀티 Kubernetes cluster와 다양한 storage backend가 있는 데이터센터 환경에서 storage resource provisioning, quota 관리, data operation 실행, 운영 조회를 통합하는 시스템이다.

Phase 1에서는 다음을 구현한다.

- DMS 전체 컴포넌트 경계를 반영한 코드베이스 스켈레톤
- 운영용 PostgreSQL을 source of truth로 삼는 request/plan/run/result lifecycle
- observability/log용 PostgreSQL을 diagnostic event 저장소로 분리하는 구조
- Resource Management, Data Management, Identity Mapping, Operational Query API 모듈의 최소 골격
- Planner, RM Worker runtime, DM Worker runtime, Agent, Backend adapter interface의 최소 실행 루프
- DMS cluster, managed cluster, RM Worker, DM Worker, Agent의 배포 토폴로지 반영
- lease, heartbeat, timeout, stale claim, recovery-needed 상태를 담을 수 있는 데이터 모델
- filesystem resource와 Kubernetes namespace storage quota resource의 공통 domain model
- storage backend template, `storage_name` mapping, identity mapping, agent inventory의 확장 가능한 모델
- 실제 backend side effect는 안전한 stub/mock adapter로 시작하되, API/Planner/Worker 경계는 실제 운영 구조와 같게 유지

Phase 1의 성공 기준은 “모든 실제 storage 기능 완성”이 아니라, 이후 phase에서 backend adapter와 세부 API를 채워도 request ordering, fail-over, observability, data model 경계가 흔들리지 않는 골격을 갖추는 것이다.

## 핵심 철학

### 1. PostgreSQL이 기준 상태다

운영용 PostgreSQL은 DMS의 단일 기준 상태 저장소다. request, plan, run, result, desired/applied/observed state, worker lease, ordering state, recovery state는 운영용 PostgreSQL에 기록된 상태를 기준으로 판단한다.

Kubernetes, filesystem, storage backend, LDAP/SSSD, VolcanoJob의 live state는 관측 대상이다. live state가 운영용 PostgreSQL을 대체하지 않는다.

Diagnostic log, debug event, latency, component log, system monitoring log처럼 high-volume 또는 long-retention 데이터는 observability/log용 PostgreSQL에 저장한다. Critical lifecycle state 저장 실패는 operation success로 처리하면 안 된다. Diagnostic event 저장 실패는 가능한 한 core operation을 실패시키지 말고 fallback critical event로 추적한다.

### 2. Frontend와 Backend side effect를 분리한다

API server/Frontend는 요청을 검증하고 운영용 PostgreSQL에 저장하며 상태를 조회한다. Frontend는 Kubernetes, filesystem, storage backend를 직접 변경하지 않는다.

Backend side effect는 request가 저장되고 Planner가 plan을 저장한 뒤, 해당 worker role의 Worker runtime이 plan을 claim한 후에만 수행한다.

공통 lifecycle은 다음 계약을 따른다.

```text
request -> plan -> run -> result
```

Authorization을 통과한 mutating request는 이 lifecycle에 들어간다. mTLS/token 인증 자체가 실패한 요청은 request lifecycle에 넣지 않고 observability diagnostic event로만 기록한다. 인증은 성공했지만 operation authorization policy가 거부한 요청은 `AuthorizationFailed` terminal result로 운영용 PostgreSQL에 기록하고 plan/run/backend side effect를 만들지 않는다.

### 3. 실행은 role별 Worker runtime이 소유한다

Planner는 request를 실행 가능한 plan으로 변환하고, resource ordering과 precondition을 확인한다. Planner는 long-running backend side effect를 수행하지 않는다.

RM Worker runtime은 Resource Management plan만 claim하고, filesystem/Kubernetes/storage backend adapter를 호출한 뒤 live verification 결과를 운영용 PostgreSQL에 기록한다.

DM Worker runtime은 Data Management plan만 claim하고, preflight/preview/confirm/execution 상태를 관리하며 VolcanoJob 생성/감시/종료를 담당한다. mpifileutils를 실행하는 Volcano worker pod는 stdout/stderr/report/artifact만 생산하며 DMS lifecycle state의 source of truth를 직접 소유하지 않는다.

DMS Agent는 Worker runtime과 별개로 node-local mount, CSI, network, credential, tool, identity evidence를 보고하는 capability reporter다.

### 4. 모든 장기 작업은 lease와 recovery를 가진다

Worker claim은 무기한 lock이 아니다. lease, heartbeat, attempt count, timeout, retry schedule, stale detection을 데이터 모델에 포함한다.

Worker runtime은 backend side effect 전에 claim과 `Applying` 또는 `Running` 상태를 운영용 PostgreSQL에 commit해야 한다. Side effect 이후 terminal/blocked/failure result는 별도 transaction으로 commit한다.

Component restart, rolling upgrade, planned shutdown, unplanned reboot 이후에는 운영용 PostgreSQL lifecycle state와 live backend verification을 기준으로 recovery를 수행한다. 어떤 request/plan/run도 영구적으로 `pending`, `claimed`, `running` 상태에 머물러서는 안 된다.

### 5. Resource별 ordering을 보장한다

외부 client 요청 도착 순서를 신뢰하지 않는다. 동일 resource에 대한 mutating request는 resource key, request commit order, resource version, precondition을 기준으로 직렬화한다.

선행 mutating request가 terminal state에 도달하기 전 후속 mutating request를 backend에 적용하지 않는다. 중복 create, 존재하지 않는 resource update, stale request, pending dependency는 Planner 단계에서 conflict/wait/retry/reject로 기록한다.

### 6. Actor와 requester_id를 구분한다

`actor`는 mTLS client certificate 또는 token에서 확인한 API 호출 주체다. Operation authorization policy의 기본 판단 주체다.

`requester_id`는 authenticated request payload에 포함된 business/audit requester identity다. DMS는 이 값을 resource ownership, POSIX 권한 검증, audit, query 기준으로 저장한다.

Data Management와 filesystem access control은 `requester_id`를 Identity Mapping API의 POSIX UID/GID/group mapping과 연결해 판단한다. 중앙 identity system은 read-only source이며 DMS는 LDAP/SSSD user/group/group membership을 생성, 수정, 삭제하지 않는다.

### 7. Backend는 adapter와 template로 확장한다

새 storage backend 추가가 DMS core lifecycle 변경을 요구하면 안 된다.

Filesystem quota 적용은 adapter/strategy로 분리한다. Storage backend template은 backend type, mount path, quota capability, quota unit conversion, CSI driver, StorageClass mapping, sanity check 항목을 표현할 수 있어야 한다.

`storage_name`은 DMS 전체에서 unique한 logical storage ID다. Kubernetes StorageClass는 cluster 내부에서만 unique하므로 `cluster_name + storage_class_name`을 unique key로 다룬다.

## DMS 배포 토폴로지

Phase 1 구현은 컴포넌트의 배포 위치와 실행 책임을 먼저 고정해야 한다. 특히 RM Worker와 DM Worker는 같은 의미의 worker가 아니며, 서로 다른 cluster 위치와 node-local capability를 가진다.

기본 토폴로지:

```text
DMS Kubernetes Cluster
├─ Ingress / edge proxy
├─ API Server / Frontend
├─ Planner / Control Plane
├─ Operational PostgreSQL
├─ Observability PostgreSQL
├─ DM Worker nodes
│  ├─ DM Worker runtime
│  ├─ DMS Agent
│  └─ Mounted storage for data jobs
└─ Volcano scheduler / worker pods

Managed Kubernetes Cluster A
├─ GPU workload nodes
├─ CSI drivers / StorageClasses / namespaces
└─ Dedicated RM Worker node or node pool
   ├─ RM Worker runtime
   ├─ DMS Agent
   └─ Mounted filesystem / quota / CSI visibility

Managed Kubernetes Cluster B
└─ same pattern as Cluster A
```

DMS Kubernetes Cluster에는 DMS API server, Frontend, Planner, control plane component, 운영용 PostgreSQL, observability/log용 PostgreSQL, DM Worker node, DM Worker runtime, DM Worker용 DMS Agent, Data Management 실행을 위한 VolcanoJob/worker pod가 배포된다.

각 managed Kubernetes cluster에는 dedicated RM Worker node 또는 node pool을 둔다. RM Worker는 GPU workload node와 구분되며, 해당 cluster의 Kubernetes API, StorageClass, CSI driver, filesystem mount, quota capability, identity/group system 상태를 cluster-local하게 검증하고 Resource Management 작업을 실행한다. RM Worker runtime과 DMS Agent는 Kubernetes workload로 RM Worker에 scheduling된다.

DM Worker는 각 managed cluster에 배포하지 않는다. DM Worker는 DMS Kubernetes Cluster 안의 Kubernetes worker node로 구성하며, DM Worker runtime과 DMS Agent가 Kubernetes workload로 이 node에 scheduling된다. DM Worker에는 데이터 이동, 복제, 삭제, scan 대상 storage가 filesystem으로 mount되어야 한다. 모든 DM Worker가 모든 storage를 mount한다고 가정하지 않고, node별 mount, tool, credential, data-operation network reachability, load/capacity를 기준으로 candidate pool을 만든다.

일반 GPU workload node 전체에는 DMS Agent를 기본 배포하지 않는다. Resource Management의 기본 검증은 Kubernetes API와 dedicated RM Worker에서 확인 가능한 mount, CSI driver, quota capability를 기준으로 한다. 일반 workload node의 mount/CSI 상태 검증은 필요 시 별도 inventory 또는 health-check operation으로 확장한다.

Backend storage는 DMS cluster의 DM Worker와 managed cluster의 RM/GPU node에 각각 mount될 수 있는 외부 또는 공유 storage다. 예를 들어 managed cluster의 GPU node와 RM Worker가 같은 WEKA/Ceph/GPFS backend를 mount하고, DMS cluster의 DM Worker도 data operation을 위해 같은 backend를 별도 mount할 수 있다.

DMS Kubernetes Cluster와 managed cluster의 DMS worker node 사이 control-plane communication path는 네트워크적으로 통신 가능하다고 가정한다. 단, 이 control-plane 통신 가능성과 Data Management job이 요구하는 data-operation network reachability는 별개로 모델링한다.

DMS server는 Kubernetes API inventory와 DMS Agent report를 결합해 다음을 판단해야 한다.

- RM 관점: cluster별 StorageClass, CSI driver, namespace, dedicated RM Worker mount, quota capability, `storage_name` mapping sanity
- DM 관점: DM Worker별 source/target mount, tool capability, credential, network reachability, node load/capacity, selected mpifileutils tool 후보

구현 시 중요한 구분:

- RM Worker와 DM Worker는 node 배치 단위다.
- RM Worker runtime과 DM Worker runtime은 해당 node 위에서 실행되는 Kubernetes workload다.
- DMS Agent도 RM Worker와 DM Worker에 배포되는 Kubernetes workload다.
- Resource Management plan은 RM Worker runtime만 claim한다.
- Data Management plan/job은 DM Worker runtime만 claim한다.
- Volcano worker pod는 mpifileutils 실행과 artifact 생성만 담당하며 DMS lifecycle source of truth를 직접 소유하지 않는다.

## 전체 아키텍처 스켈레톤

Phase 1 구현은 최소한 아래 모듈 경계를 갖춰야 한다. 실제 package/process/deployment 이름은 구현자가 정하되, 책임 경계는 유지한다.

### API Ingress/Auth Layer

- Kubernetes Ingress 또는 edge proxy 뒤에서 mTLS client certificate evidence와 token을 검증할 수 있는 구조를 둔다.
- Phase 1에서는 실제 인증 연동을 stub/configurable verifier로 시작해도 되지만, 인증 실패와 authorization 실패의 lifecycle 처리는 분리한다.
- 인증 실패는 운영 request를 만들지 않고 diagnostic event만 남긴다.

### API Server / Frontend

- Resource Management API, Data Management API, Identity Mapping API, Operational Query API의 route/module skeleton을 둔다.
- request envelope validation, actor 추출, requester_id 저장, authorization policy 호출, request persistence를 담당한다.
- mutating request를 backend에 직접 반영하지 않는다.
- query는 운영용 PostgreSQL 상태를 기본으로 반환하고, live read가 필요한 경우에도 side effect 없는 path로 분리한다.

### Planner

- 운영용 PostgreSQL의 persisted request를 읽어 plan을 만든다.
- resource key별 pending request/plan/run, resource version, desired/observed state, maintenance mode를 확인한다.
- conflict, stale, rejected, authorization failed, default quota resolution, precondition failure를 backend 호출 없이 기록할 수 있어야 한다.
- plan에는 worker role, resource key, operation kind, desired state, precondition, execution metadata를 포함한다.

### RM Worker Runtime

- Resource Management plan만 lease 기반으로 claim한다.
- Backend adapter를 호출하기 전에 `Claimed`와 `Applying` 상태를 commit한다.
- filesystem directory/quota/permission adapter와 Kubernetes ResourceQuota adapter는 Phase 1에서 stub/mock로 시작하되 interface를 실제 side effect에 맞게 설계한다.
- 실행 후 live verification 결과를 observed state, result, diagnostic event로 기록한다.

### DM Worker Runtime

- Data Management plan/job만 lease 기반으로 claim한다.
- `sync`, `rm`, `scan` job lifecycle skeleton을 구현한다.
- `sync`/`rm`은 preflight, preview, confirm, execution phase를 같은 `job_id` 안에서 추적한다.
- `scan`은 preview/confirm 없이 preflight 후 execution으로 진행한다.
- VolcanoJob 생성/감시/종료 adapter는 Phase 1에서 stub/mock로 시작하되, selected tool, candidate pool, priority, artifact URI, timeout/cancel 상태를 기록할 수 있어야 한다.

### Backend Adapters

필수 interface skeleton:

- filesystem backend adapter: create/update/block/initialize/delete/consistency check/import/quota-only assignment
- filesystem quota strategy: GPFS/Lustre/XFS/Ceph/Weka 등 backend별 확장 지점
- Kubernetes namespace quota adapter: namespace read/create, DMS-managed `ResourceQuota` apply/read/delete, live DB sync
- storage inventory adapter: Kubernetes API inventory와 agent report 결합
- identity lookup adapter: NSS/SSSD 또는 LDAP read-only lookup
- Volcano adapter: Data Management worker pod/job 생성, 조회, 종료

### DMS Agent

- agent report ingestion API와 report validation skeleton을 둔다.
- report에는 cluster_name, node identity, worker role/type, mount, CSI, tool, credential, network, identity evidence를 담을 수 있어야 한다.
- 인증 실패 또는 node identity mismatch report는 inventory에 반영하지 않고 diagnostic event로 남긴다.

### Operational Query Service

- action-required issue, request/run history, resource history, worker/agent health, identity mapping status, data job status, diagnostic correlation query의 skeleton을 둔다.
- Phase 1에서는 모든 query를 완성하지 않아도 되지만, unresolved issue를 표현할 데이터 모델과 query 확장 지점은 만든다.

### Persistence / Repository Layer

- 운영용 PostgreSQL session/repository와 observability/log용 PostgreSQL session/repository를 분리할 수 있게 한다.
- `DMS_OBSERVABILITY_DATABASE_URL`이 운영 DB와 다르면 별도 connection/session을 사용한다.
- migration version, control state, worker lease, request/plan/run/result, resource state, data job state, diagnostic event 저장 경계를 명확히 둔다.

## Phase 1 논리 데이터 모델

구체 schema는 구현자가 정하되, 다음 개념은 반드시 표현 가능해야 한다.

- `requests`: request_id, requester_id, actor, operation, resource kind/key, payload summary, requested_at, status
- `plans`: request_id, plan status, worker role, operation kind, resource key, desired state, precondition, attempt metadata
- `runs`: request_id, plan_id, worker_id/executor_id, worker role, lease, heartbeat, state, started_at, updated_at
- `results`: request_id, plan_id, run_id, terminal status, error category, message, verification summary
- `state_transitions`: lifecycle audit trail
- `resources`: filesystem resource와 Kubernetes namespace storage quota resource의 current desired/applied/observed state
- `storage_mappings`: `storage_name`, backend template, cluster/storage class mapping, version, sanity status
- `default_quota_policies`: resource kind + type별 default quota
- `identity_mappings`: requester_id, identity_provider, posix_username, UID/GID/groups, status, verified_at, stale_at, disabled_at
- `agent_reports`: node-local capability report와 freshness/effective view
- `data_jobs`: job_id, request_id, operation, source/destination/target, priority, selected tool, worker pool, preflight/preview/confirm/execution state, artifact URI
- `control_mutations`: direct control mutation audit record
- `dms_control_state`: maintenance/drain mode, scheduling blocked flag, reason, changed_by, changed_at
- `diagnostic_events`: observability/log DB의 structured diagnostic event

필수 uniqueness:

- `request_id`: DMS 전체 unique
- `storage_name`: DMS 전체 unique
- Filesystem resource: `storage_name + directory_name`
- Kubernetes namespace storage quota resource: `cluster_name + namespace_name`
- Kubernetes StorageClass mapping: `cluster_name + storage_class_name`
- Identity mapping: `requester_id + identity_provider`

## 핵심 상태 계약

공통 request/run state는 최소한 다음 의미를 표현할 수 있어야 한다.

- non-terminal: `Received`, `Persisted`, `Planning`, `Planned`, `Claimed`, `Running`, `Applying`, `Blocked`, `Verifying`, `StaleClaim`, `RecoveryNeeded`
- terminal: `AuthenticationRejected`, `AuthorizationFailed`, `Succeeded`, `Failed`, `TimedOut`, `Cancelled`, `Conflict`, `Rejected`, `VerificationFailed`, `UnknownAfterSideEffect`

Data Management Job state는 공통 lifecycle과 분리해 `data_jobs`에 저장한다.

- `Pending`
- `AuthorizationFailed`
- `PreflightRunning`
- `PreflightFailed`
- `PreviewRunning`
- `PreviewSucceeded`
- `PreviewExpired`
- `ConfirmPending`
- `Confirmed`
- `Scheduled`
- `Running`
- `Succeeded`
- `Failed`
- `Cancelled`
- `TimedOut`

중요: Data Management의 `ConfirmPending`은 공통 request/plan/run state로 쓰지 않는다. 공통 lifecycle은 confirm 대기 중 `Blocked`로 표현하고, job phase는 `data_jobs`에서 추적한다.

## API 모듈 골격

### Resource Management API

초기 skeleton capability:

- filesystem create/update/block/initialize/query/delete
- existing directory quota assignment
- import existing filesystem directory
- resource consistency check
- expiration sweep
- Kubernetes namespace storage quota create/update/block/query/delete
- Kubernetes namespace storage quota DB sync from live state
- storage backend template 및 `storage_name` mapping management
- default quota policy update

Resource Management mutating capability는 plan과 RM Worker를 거친다. 단, storage mapping, default quota policy, maintenance mode 같은 config/control mutation은 request lifecycle이 아니라 `control_mutations` audit record로 추적한다.

### Data Management API

초기 skeleton capability:

- `sync`: source file/directory를 destination directory로 동기화. 실행 시 `dsync` 또는 `nsync` 자동 선택.
- `rm`: target directory 삭제. 내부 tool은 `drm`.
- `scan`: target directory 분석. 내부 tool은 `dscan`.
- `help`: operation 설명과 허용 option/schema 반환.
- `cancel`: terminal 전 job 취소 및 VolcanoJob 종료.

사용자 입력은 worker absolute path가 아니라 registered storage/resource + relative path여야 한다. Path traversal, symlink escape, storage root 밖 경로, raw command-line option string은 거부한다.

`sync`와 `rm`은 preview TTL과 confirm을 요구한다. 기본 preview TTL은 24시간이다. 삭제성 option은 confirm 없이는 execution으로 가지 않는다.

### Identity Mapping API

초기 skeleton capability:

- requester identity mapping upsert
- read-only LDAP/SSSD/NSS verification
- mapping refresh
- mapping list/query
- mapping disable

상태는 `Active`, `Disabled`, `NeedsReview`, `Stale` 등을 표현할 수 있어야 한다. Disabled mapping은 refresh로 되살리지 않고 명시적 upsert가 필요하다.

### Operational Query API

초기 skeleton capability:

- action-required unresolved issue list
- request/plan/run/result history
- resource lifecycle history
- failed/recovery-needed/long-running/stale run query
- worker/agent effective capability query
- identity mapping issue query
- data job/preview status query
- diagnostic event correlation query

Operational Query API는 read-only다. Live backend read를 수행하더라도 Kubernetes/filesystem/identity system을 변경하지 않는다.

## Resource 모델 요약

### Filesystem Resource

- identity: `storage_name + directory_name`
- directory는 storage root 바로 아래 basename이어야 한다.
- access control은 Linux group 기반으로 관리한다.
- identity/group membership은 LDAP/SSSD/NSS를 read-only로 조회한다.
- quota는 capacity quota와 file count quota를 가진다.
- 사용자 입력 `TB`는 decimal terabyte이며 `1TB = 10^12 bytes`다.
- count quota `5M`은 5,000,000 files다.
- default quota:
  - `user`: 1TB, 5M files
  - `project`: 1TB, 5M files
  - `system`: unlimited sentinel
  - `admin`: unlimited sentinel
- block mode는 `readonly`와 chmod 기반 `root-owned`를 지원할 수 있어야 한다.
- quota-only managed existing directory와 imported full DMS-managed directory를 구분한다.

### Kubernetes Namespace Storage Quota Resource

- identity: `cluster_name + namespace_name`
- DMS는 namespace마다 DMS-managed ResourceQuota 하나만 관리한다.
- ResourceQuota 이름은 `dms-storage-quota`로 고정한다.
- namespace-wide `requests.storage`, PVC count quota를 관리한다.
- StorageClass별 quota는 `storage_name` mapping에서 derived `storage_class_name`으로 적용한다.
- Kubernetes namespace quota는 PVC 요청 용량과 PVC object count를 제한하며, filesystem byte-level usage나 file count quota를 직접 제한하지 않는다.
- default quota:
  - `user`: namespace `requests.storage` 1TB, PVC count 20
  - `project`: namespace `requests.storage` 4TB, PVC count 200
  - `system`: unlimited sentinel
  - `admin`: unlimited sentinel
- `block=ON`은 DMS-managed ResourceQuota의 hard limit을 0으로 설정한다.

## 구현 순서 제안

1. 프로젝트 기본 구조, 설정 로더, DB connection 분리, migration skeleton을 만든다.
2. domain enum/value object/resource key/request envelope를 먼저 만든다.
3. 운영 DB repository와 observability repository interface를 만든다.
4. request persistence, authorization failure, control mutation audit 흐름을 API server에 연결한다.
5. Planner run-once/loop skeleton과 resource ordering/precondition stub을 만든다.
6. RM Worker run-once/loop skeleton, lease/heartbeat/stale claim 처리를 만든다.
7. DM Worker run-once/loop skeleton, data job state machine과 Volcano adapter stub을 만든다.
8. Agent report ingestion과 effective inventory view skeleton을 만든다.
9. Operational Query skeleton과 action-required issue projection을 만든다.
10. 최소 unit/integration test를 추가한다: auth failure vs authz failure, request persistence before side effect, planner conflict, worker lease/stale recovery, observability DB 분리, resource key uniqueness, data job confirm state 분리.

## Phase 1에서 피해야 할 구현

- API server가 직접 filesystem/Kubernetes/storage mutation을 수행하지 않는다.
- live backend state를 운영 DB source of truth로 대체하지 않는다.
- Data Management `ConfirmPending`을 공통 lifecycle state로 섞지 않는다.
- requester_id를 actor에서 강제로 derive하거나 같은 개념으로 저장하지 않는다.
- raw shell option string을 mpifileutils에 그대로 넘기는 구조를 만들지 않는다.
- storage backend별 코드를 core lifecycle에 직접 박아 넣지 않는다.
- timeout 없는 외부 호출, 무기한 DB lock, side effect 중 장시간 transaction 보유를 만들지 않는다.
- Phase 1 stub가 나중에 실제 adapter로 교체될 수 없는 형태로 굳어지게 만들지 않는다.

## Phase 1 산출물

- 실행 가능한 API/Planner/Worker/Agent skeleton
- 운영 DB와 observability DB 분리 가능한 설정
- lifecycle state와 data job state를 저장/조회하는 최소 persistence layer
- backend adapter, identity lookup, inventory, Volcano adapter interface
- 최소 CLI 또는 process entrypoint:
  - API server
  - planner loop 또는 run-once
  - RM worker loop 또는 run-once
  - DM worker loop 또는 run-once
  - agent loop 또는 report submitter
  - migration command
- 최소 테스트와 검증 결과 문서
- 이후 phase에서 세부 Resource Management/Data Management 기능을 채울 수 있는 TODO 또는 extension point

Phase 1 구현자는 세부 schema/API/interface는 위 원칙을 만족하는 범위에서 코드베이스 일관성과 테스트 가능성을 기준으로 결정한다.
