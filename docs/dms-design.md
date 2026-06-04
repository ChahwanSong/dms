# DMS Design (Archived)

> Status: archived on 2026-06-04.
>
> This document is no longer an active design or implementation reference for DMS.
> It is kept only as historical context for early phase decisions. For current
> implementation work, use `docs/dms-done.md`, the latest `docs/dms-phase*.md`
> files, `src/`, `install/`, and live testbed evidence. If this file conflicts
> with the current implementation, `docs/dms-done.md`, current phase documents,
> code, and install/runbook files are authoritative.
>
> New DMS work must not use this document as the source of truth.

이 문서는 코드 에이전트가 DMS(Data Management Service)를 구현하고 테스트베드에서 기능 검증까지 수행하기 위해 참조하는 설계 및 구현 가이드 문서다.

이 문서는 DMS의 설계 의도, 구현 요구사항, 검증 기준, 운영 산출물 요구사항을 한곳에 정리한다. 코드 에이전트는 이 문서를 기준으로 시스템을 구현하고, 테스트베드에서 기능 검증을 수행하며, 구현 결과에 필요한 설치/사용/운영/재구성 매뉴얼을 별도 문서로 작성해야 한다.

이 문서를 작성하는 원칙은 코드 에이전트와의 대화에서 확정된 설계만 점진적으로 기록한다. 아직 확정되지 않은 세부 아키텍처, 컴포넌트 구현, DB schema, API spec, 내부 interface, 운영 방식은 확정된 것처럼 기술하지 않는다. 세부 구현은 이 문서의 원칙과 검증 기준을 만족하는 범위에서 코드 에이전트가 코드베이스 구조와 테스트 가능성을 고려해 결정한다.

## 목차

- [Design Philosophy](#design-philosophy)
  - [PostgreSQL as the Source of Truth](#postgresql-as-the-source-of-truth)
  - [Storage Resource Management System](#storage-resource-management-system)
- [Design Figures](#design-figures)
  - [Frontend and Backend Separation](#frontend-and-backend-separation)
  - [Request Lifecycle](#request-lifecycle)
  - [State Machine Reference](#state-machine-reference)
  - [Request Ordering and Consistency](#request-ordering-and-consistency)
  - [Failure Handling and Fail-over](#failure-handling-and-fail-over)
  - [Operational Observability Storage](#operational-observability-storage)
  - [Implementation Detail Boundary](#implementation-detail-boundary)
- [API Modules](#api-modules)
  - [Capability Contract Matrix](#capability-contract-matrix)
- [Identity Mapping API](#identity-mapping-api)
- [Operational Query API](#operational-query-api)
- [DMS Execution Topology and Worker Roles](#dms-execution-topology-and-worker-roles)
  - [Component Responsibility Matrix](#component-responsibility-matrix)
- [Resource Management API](#resource-management-api)
  - [External API Authentication](#external-api-authentication)
- [Data Management API](#data-management-api)
  - [Data Management Request Model](#data-management-request-model)
  - [Preview, Preflight, and Authorization](#preview-preflight-and-authorization)
  - [Tool Selection and Scheduling](#tool-selection-and-scheduling)
  - [Failure, Timeout, and Result Handling](#failure-timeout-and-result-handling)
- [Target Operating Context](#target-operating-context)
  - [DMS API Ingress](#dms-api-ingress)
  - [PostgreSQL](#postgresql)
  - [Multi-Cluster Kubernetes Environment](#multi-cluster-kubernetes-environment)
  - [Example Cluster Topology](#example-cluster-topology)
- [Resource Models](#resource-models)
  - [Filesystem Resource](#filesystem-resource)
  - [Kubernetes Namespace Storage Quota Resource](#kubernetes-namespace-storage-quota-resource)
- [DMS Worker Nodes, Agent, and Kubernetes Inventory](#dms-worker-nodes-agent-and-kubernetes-inventory)
- [StorageClass and storage_name Mapping](#storageclass-and-storage_name-mapping)
- [Requirements](#requirements)
  - [Functional Requirements](#functional-requirements)
  - [Operational Requirements](#operational-requirements)
  - [Documentation and Runbook Requirements](#documentation-and-runbook-requirements)
  - [Implementation Verification Matrix](#implementation-verification-matrix)
- [Operational Scenarios](#operational-scenarios)
  - [Initial Setup](#initial-setup)
  - [Installation and Air-gapped Deployment](#installation-and-air-gapped-deployment)
  - [Rolling Upgrade and Maintenance Drain](#rolling-upgrade-and-maintenance-drain)
  - [Planned Shutdown and Startup](#planned-shutdown-and-startup)
  - [Unplanned Power Loss and Full Reboot Recovery](#unplanned-power-loss-and-full-reboot-recovery)
  - [Inventory Refresh](#inventory-refresh)
  - [Default Quota Policy Update](#default-quota-policy-update)
  - [Resource Initialize](#resource-initialize)
  - [Expiration Sweep](#expiration-sweep)
  - [Request Submission](#request-submission)
  - [Request Query](#request-query)
  - [Resource Consistency Check](#resource-consistency-check)
  - [Operational Query API](#operational-query-api-1)
  - [Observability Query](#observability-query)
  - [Data Management Sync with dsync](#data-management-sync-with-dsync)
  - [Data Management Sync with nsync](#data-management-sync-with-nsync)
  - [Data Management rm](#data-management-rm)
  - [Data Management scan](#data-management-scan)
  - [Data Management Authorization Failure](#data-management-authorization-failure)
  - [Data Management Preflight Failure](#data-management-preflight-failure)
  - [Data Management Preview Expiration and Confirm](#data-management-preview-expiration-and-confirm)
  - [Data Management Volcano Pod Failure](#data-management-volcano-pod-failure)
  - [Data Management Timeout and Cancel](#data-management-timeout-and-cancel)
  - [Filesystem Resource Creation](#filesystem-resource-creation)
  - [Filesystem Resource Update](#filesystem-resource-update)
  - [Filesystem Resource Block](#filesystem-resource-block)
  - [Existing Directory Quota Assignment](#existing-directory-quota-assignment)
  - [Import Existing Filesystem Directory](#import-existing-filesystem-directory)
  - [Kubernetes Namespace Storage Quota Creation](#kubernetes-namespace-storage-quota-creation)
  - [Kubernetes Namespace Storage Quota Update](#kubernetes-namespace-storage-quota-update)
  - [Kubernetes Namespace Storage Quota Block](#kubernetes-namespace-storage-quota-block)
- [Architecture](#architecture)
- [Data Model](#data-model)
- [Open Questions](#open-questions)

## Design Philosophy

### PostgreSQL as the Source of Truth

DMS는 DMS Kubernetes cluster 내부에 사전 배포된 PostgreSQL을 사용하되, 운영용 PostgreSQL과 observability/log용 PostgreSQL을 논리적으로 분리한다.

운영용 PostgreSQL은 DMS의 단일 기준 상태 저장소(source of truth)다.

모든 요청, 계획, 실행 상태, 실행 결과, resource desired state, resource observed state, worker lease, ordering state, recovery state는 운영용 PostgreSQL에 기록된 상태를 기준으로 판단한다. Kubernetes, filesystem, 외부 endpoint의 실제 상태는 관측 대상이며, DMS의 기준 상태는 운영용 PostgreSQL에 있다.

Observability/log용 PostgreSQL은 진단, 로그, latency, debug event, component log, system monitoring log처럼 high-volume 또는 long-retention 성격의 데이터를 저장하기 위한 보조 DB다.

운영용 PostgreSQL과 observability/log용 PostgreSQL은 DMS Kubernetes cluster 내부의 같은 HA PostgreSQL cluster 안에서 별도 database 또는 schema로 분리하거나, 별도 in-cluster PostgreSQL instance로 분리할 수 있어야 한다. 목적은 observability write/query, retention cleanup, dashboard 조회가 운영용 DB의 request/plan/run/result 처리와 worker claim/lease 처리에 미치는 영향을 줄이는 것이다.

운영 환경의 PostgreSQL은 DMS Kubernetes cluster 내부에서 HA 구성으로 실행 중이라고 가정한다. DMS는 PostgreSQL 자체의 HA를 구현하거나 설치하는 책임을 갖지 않고, 운영자가 사전에 준비한 in-cluster PostgreSQL을 사용한다. 개발 또는 로컬 검증 환경에서는 단일 PostgreSQL instance를 허용할 수 있지만, 운영 설계 기준은 HA PostgreSQL이다.

### Storage Resource Management System

DMS는 사용자가 스토리지 리소스 할당을 요청하면 filesystem 또는 Kubernetes namespace storage quota를 관리하여 리소스를 제공하고 운영한다.

DMS가 관리하는 리소스 요청은 크게 두 종류로 나뉜다.

- Filesystem resource
- Kubernetes namespace storage quota resource

Filesystem resource는 storage root 아래 사용자 directory를 만들고, Linux group 기반 접근 권한과 filesystem별 quota 기능을 적용해 관리한다.

Kubernetes namespace storage quota resource는 Kubernetes API level에서 namespace 및 StorageClass 단위 PVC 요청 용량과 PVC 개수를 제한한다.

## Design Figures

다음 figure들은 DMS의 핵심 아키텍처, request lifecycle, resource model을 빠르게 이해하기 위한 요약 그림이다.

![DMS architecture overview](figures/dms-architecture.svg)

Figure 1. DMS는 trusted Ingress 또는 edge proxy의 mTLS 인증, DMS Kubernetes cluster 안의 API/Frontend, Planner/control plane, 운영용 PostgreSQL, observability/log용 PostgreSQL, DM Worker와 agent inventory를 중심으로 동작한다. RM Worker는 각 managed Kubernetes cluster에 dedicated node로 배포되며, Identity Mapping API는 LDAP/SSSD 같은 중앙 identity system을 read-only로 조회한다.

![DMS request lifecycle](figures/dms-request-lifecycle.svg)

Figure 2. Authorization을 통과한 mutating request는 `request -> plan -> run -> result` lifecycle을 따르며, Backend side effect는 request와 plan이 운영용 PostgreSQL에 기록된 뒤 해당 request type을 담당하는 Worker runtime이 실행한다. Data Management API의 `sync`와 `rm`은 preflight/preview 후 confirm된 execution phase로 이어지고, `scan`은 preview 없이 preflight 후 바로 execution phase로 진행한다.

![DMS resource models and storage mapping](figures/dms-resource-models.svg)

Figure 3. Filesystem resource는 `storage_name + directory_name`으로 식별하고, Kubernetes namespace storage quota는 `cluster_name + namespace_name`을 resource identity로 사용한다. Data Management Job은 storage/resource-relative path, agent-reported worker pool, selected mpifileutils tool, preflight/preview/confirm/result artifact를 별도 job state로 추적한다.

### Frontend and Backend Separation

DMS는 사용자 요청 접수/조회 경로인 Frontend와 실제 업무 실행 경로인 Backend를 분리한다.

- Frontend는 사용자의 request를 접수하고 운영용 PostgreSQL에 기록하며, 현재 상태와 결과를 조회한다.
- Frontend는 사용자의 request를 Backend에 직접 반영하지 않는다.
- Backend는 실제 Kubernetes, filesystem, storage resource 관련 업무를 수행하고 검증하는 실행 경로다.
- Planner는 운영용 PostgreSQL에 저장된 request를 읽고 실행 가능한 plan을 수립한 뒤 운영용 PostgreSQL에 저장한다.
- 각 Worker runtime은 운영용 PostgreSQL에 저장된 plan을 읽고 Backend를 통해 작업을 실행한 뒤, 실행 상태와 결과를 운영용 PostgreSQL에 다시 기록한다.
- RM Worker runtime은 리소스 관리 요청을 실행하고, DM Worker runtime은 데이터 관리 요청을 실행한다.

이 분리는 사용자 요청 접수와 외부 side effect를 의도적으로 분리하기 위한 핵심 설계 원칙이다. 사용자의 요청은 먼저 운영용 PostgreSQL에 기록되고, 계획 수립 및 저장을 거친 뒤, 요청 종류에 맞는 Worker runtime이 Backend를 통해 실행하고 검증한다.

### Request Lifecycle

Authorization을 통과해 Backend 실행 대상이 된 작업은 다음 lifecycle을 따른다.

```text
request -> plan -> run -> result
```

- `request`: 사용자의 요청을 접수하고 검증한 상태
- `plan`: 요청을 실행 가능한 작업 계획으로 변환한 상태
- `run`: 계획된 작업을 실제로 실행 중인 상태
- `result`: 실행 결과가 기록된 상태

mTLS 또는 token 인증이 실패한 요청은 request lifecycle에 넣지 않고 diagnostic observability event로만 기록한다. 인증은 성공했지만 operation authorization policy가 거부한 요청은 `AuthorizationFailed` terminal result로 기록하고 plan, run, Backend side effect를 생성하지 않는다.

각 단계는 운영용 PostgreSQL에 명시적으로 기록되어야 하며, 운영자는 각 request가 현재 어느 단계에 있는지 조회할 수 있어야 한다.

Lifecycle의 기본 흐름은 다음과 같다.

1. Frontend가 사용자 request를 접수한다.
2. Frontend가 request를 운영용 PostgreSQL에 저장한다.
3. Planner가 저장된 request를 읽고 실행 가능한 plan을 수립한다.
4. Planner가 plan을 운영용 PostgreSQL에 저장한다.
5. 요청 종류에 맞는 Worker runtime이 저장된 plan을 claim한다.
6. 해당 Worker runtime이 Backend를 통해 plan을 실행한다.
7. 해당 Worker runtime이 Backend를 통해 실행 결과를 검증한다.
8. 해당 Worker runtime이 run 상태와 result를 운영용 PostgreSQL에 저장한다.

### State Machine Reference

이 섹션은 구현 에이전트가 lifecycle 상태를 설계 의도와 다르게 해석하지 않도록 최소 상태 전이 계약을 정의한다. 실제 DB table, enum 이름, 내부 이벤트 이름은 구현 단계에서 결정할 수 있지만, 아래 의미와 terminal 처리 원칙은 유지해야 한다.

Common request lifecycle:

| State | Owner | Valid next states | Terminal | Backend side effect |
| --- | --- | --- | --- | --- |
| `Received` | API server 또는 Frontend | `Persisted`, `AuthenticationRejected` | no | no |
| `AuthenticationRejected` | API server 또는 Ingress/auth layer | none | yes | no |
| `Persisted` | API server 또는 Frontend | `AuthorizationFailed`, `Planning`, `Rejected` | no | no |
| `AuthorizationFailed` | API server 또는 Planner | none | yes | no |
| `Planning` | Planner | `Planned`, `Conflict`, `Rejected`, `Failed` | no | no |
| `Planned` | Planner 또는 API server after confirm | `Claimed`, `Cancelled` | no | no |
| `Claimed` | Worker runtime | `Running`, `StaleClaim`, `Failed` | no | no |
| `Running` | Worker runtime | `Verifying`, `Blocked`, `Failed`, `TimedOut`, `UnknownAfterSideEffect` | no | yes or dry-run |
| `Blocked` | Worker runtime 또는 API server | `Planned`, `Cancelled`, `PreviewExpired` | no | no active backend execution |
| `Verifying` | Worker runtime | `Succeeded`, `VerificationFailed`, `RecoveryNeeded` | no | already applied |
| `Succeeded` | Worker runtime | none | yes | already applied |
| `Failed` | Planner 또는 Worker runtime | none | yes | maybe |
| `TimedOut` | Worker runtime 또는 scheduler monitor | none | yes | maybe |
| `Cancelled` | API server 또는 Worker runtime | none | yes | maybe |
| `Conflict` | Planner | none | yes | no |
| `StaleClaim` | Worker runtime 또는 recovery flow | `Claimed`, `RecoveryNeeded`, `Failed` | no | maybe |
| `RecoveryNeeded` | recovery flow | `Claimed`, `Succeeded`, `Failed`, `UnknownAfterSideEffect` | no | maybe |
| `UnknownAfterSideEffect` | Worker runtime 또는 recovery flow | `RecoveryNeeded`, `Succeeded`, `Failed` | no | unknown |

`AuthenticationRejected`는 request lifecycle table에 저장하지 않고 observability/log용 PostgreSQL의 diagnostic event로만 남길 수 있다. 반면 `AuthorizationFailed`는 인증된 요청에 대한 operation-level 거부 결과이므로 운영용 PostgreSQL에서 request/result로 조회 가능해야 한다.

Resource Management run lifecycle:

| State | Owner | Valid next states | Terminal | Notes |
| --- | --- | --- | --- | --- |
| `Planned` | Planner | `Claimed` | no | 대상 resource key와 precondition을 포함해야 한다. |
| `Claimed` | RM Worker runtime | `Applying`, `StaleClaim`, `Failed` | no | lease, heartbeat, worker role을 기록해야 한다. |
| `Applying` | RM Worker runtime | `Verifying`, `BackendApplyFailed`, `TimedOut`, `UnknownAfterSideEffect` | no | Kubernetes, filesystem, storage backend side effect가 발생할 수 있다. |
| `Verifying` | RM Worker runtime | `Succeeded`, `VerificationFailed`, `RecoveryNeeded` | no | live backend state를 재조회하고 observed state를 갱신한다. |
| `Succeeded` | RM Worker runtime | none | yes | desired/applied/observed/result가 연결되어야 한다. |
| `BackendApplyFailed` | RM Worker runtime | none | yes | side effect 전후 여부와 retry 가능 여부를 기록한다. |
| `VerificationFailed` | RM Worker runtime | `RecoveryNeeded`, `Failed` | no | operation correctness에 영향을 주는 경우 recovery 대상으로 남긴다. |
| `TimedOut` | RM Worker runtime | `RecoveryNeeded`, `Failed` | maybe | timeout 시점에 side effect 여부가 불명확하면 recovery가 필요하다. |

Data Management Job lifecycle:

| State | Owner | Valid next states | Terminal | Backend side effect |
| --- | --- | --- | --- | --- |
| `Pending` | API server | `AuthorizationFailed`, `PreflightRunning`, `Cancelled` | no | no |
| `AuthorizationFailed` | API server 또는 Planner | none | yes | no |
| `PreflightRunning` | DM Worker runtime | `PreflightFailed`, `PreviewRunning`, `Scheduled`, `Cancelled` | no | no data mutation |
| `PreflightFailed` | DM Worker runtime | none | yes | no data mutation |
| `PreviewRunning` | DM Worker runtime | `PreviewSucceeded`, `Failed`, `TimedOut`, `Cancelled` | no | dry-run only |
| `PreviewSucceeded` | DM Worker runtime | `ConfirmPending`, `PreviewExpired`, `Cancelled` | no | dry-run only |
| `PreviewExpired` | API server 또는 DM Worker runtime | none | yes | no data mutation |
| `ConfirmPending` | API server | `Confirmed`, `PreviewExpired`, `Cancelled` | no | no |
| `Confirmed` | API server | `Scheduled`, `Cancelled` | no | no |
| `Scheduled` | DM Worker runtime | `Running`, `Failed`, `TimedOut`, `Cancelled` | no | no or not yet |
| `Running` | Volcano worker pod, monitored by DM Worker runtime | `Succeeded`, `Failed`, `TimedOut`, `Cancelled` | no | yes for `sync`/`rm`, read-only for `scan` |
| `Succeeded` | DM Worker runtime | none | yes | operation-specific |
| `Failed` | DM Worker runtime | none | yes | maybe |
| `Cancelled` | API server 또는 DM Worker runtime | none | yes | maybe |
| `TimedOut` | DM Worker runtime | none | yes | maybe |

`sync`와 `rm`은 `PreviewSucceeded` 이후 confirm을 받아야 `Scheduled`로 진행할 수 있다. 이때 `ConfirmPending`은 Data Management Job 전용 상태이며, 공통 request/plan/run lifecycle에는 기록하지 않는다. 공통 lifecycle은 사용자 confirm 대기 상태를 `Blocked` non-terminal state로 표현하고, confirm이 들어오면 같은 plan/request를 다시 `Planned`로 전환해 실행 phase를 진행한다. `scan`은 데이터 변경이 없으므로 `PreflightRunning -> Scheduled -> Running`으로 진행하며 preview와 confirm 상태를 거치지 않는다.

### Request Ordering and Consistency

DMS는 외부 API 요청이 의도한 순서와 다르게 도착할 수 있다는 점을 전제로 설계한다.

Ordering consistency 원칙:

- DMS는 외부 client가 보낸 요청 도착 순서만으로 resource 상태 전이를 판단하지 않는다.
- 운영용 PostgreSQL에 기록된 request commit order와 resource별 상태/version을 기준으로 일관성을 판단한다.
- 동일 resource에 대한 변경 요청은 resource 단위로 직렬화되어야 한다.
- 동일 resource에 대한 후속 mutating request는 선행 mutating request가 terminal result에 도달하기 전에는 Backend에 적용하지 않는다.
- 선행 mutating request가 success이면 후속 request는 갱신된 resource version과 current desired/observed state를 기준으로 plan을 생성한다.
- 선행 mutating request가 failure, recovery-needed, unknown-after-side-effect 상태이면 후속 request는 Backend에 적용하지 않고 대기, 재시도, conflict, failed 중 하나로 처리한다.
- Planner는 request를 plan으로 변환하기 전에 대상 resource의 현재 desired state, observed state, pending request, pending plan을 확인해야 한다.
- Create, update, initialize, block, quota assignment, import 같은 mutating request는 대상 resource의 존재 여부, 중복 여부, 현재 상태에 대한 precondition을 검증해야 한다.
- 선행되어야 하는 request가 아직 plan/result 단계까지 진행되지 않았다면, 후속 request는 대기, 재시도, 또는 conflict 상태로 기록되어야 한다.
- 이미 더 최신 state가 반영된 뒤 도착한 오래된 요청은 그대로 실행하지 않고 conflict, stale request, 또는 정책상 허용된 merge 대상으로 처리해야 한다.
- conflict 처리 결과도 운영용 PostgreSQL에 기록하고 query로 조회 가능해야 한다.

예를 들어 동일 resource에 대해 `create -> update -> block` 의도가 있었지만 API가 `block`, `update`, `create` 순서로 도착할 수 있다. 이 경우 DMS는 Backend에 즉시 반영하지 않고 운영용 PostgreSQL에 request를 기록한 뒤, Planner가 resource 존재 여부, request dependency, current version을 기준으로 실행 가능 여부와 순서를 판단해야 한다.

### Failure Handling and Fail-over

DMS는 failure에 대한 fail-over 기능을 기본적으로 갖춰야 한다.

Failure handling 원칙:

- API server, Planner, RM Worker runtime, DM Worker runtime, Agent는 restart되어도 운영용 PostgreSQL 상태를 기준으로 작업을 재개할 수 있어야 한다.
- Worker runtime이 plan을 claim한 뒤 실패하거나 중단되어도 해당 작업이 영구적으로 stuck 상태가 되면 안 된다.
- 작업 claim은 무기한 lock이 아니라 timeout 또는 lease 기반이어야 한다.
- lease가 만료된 작업은 같은 role의 다른 Worker runtime이 재claim하거나 recovery flow로 전환할 수 있어야 한다.
- 모든 Backend side effect는 가능한 한 idempotent하게 구현해야 한다.
- retry는 attempt count, next retry time, last error, error category를 남기고 bounded backoff를 적용해야 한다.
- 영구 실패와 일시 실패를 구분해야 한다.
- 외부 system, Kubernetes API, filesystem command, ssh command, storage CLI 호출은 timeout을 가져야 한다.
- timeout 없는 blocking call은 허용하지 않는다.
- DB transaction은 짧게 유지하고, 외부 side effect 수행 중 장시간 DB lock을 보유하지 않는다.
- Worker runtime은 Backend side effect를 시작하기 전에 claim, heartbeat, `Applying` 또는 `Running` 상태를 운영용 PostgreSQL에 먼저 commit해야 한다.
- Backend side effect 이후 terminal, blocked, 또는 handled failure 결과는 worker runtime이 별도 transaction으로 commit해야 하며, CLI/API caller의 outer transaction 성공에 의존해서는 안 된다.
- 동일 resource에 대한 직렬화는 deadlock이 발생하지 않도록 lock ordering, lease, precondition 검증을 기준으로 구현해야 한다.
- deadlock 또는 lock wait timeout이 발생하면 명확한 실패 상태와 재시도 가능 여부를 운영용 PostgreSQL에 기록해야 한다.
- long-running operation은 heartbeat 또는 progress update를 남겨야 한다.
- heartbeat가 오래 갱신되지 않은 run은 stale run으로 판단하고 recovery 대상이 되어야 한다.
- 어떤 failure도 request, plan, run이 영원히 `running`, `pending`, `claimed` 상태에 머무르게 해서는 안 된다.
- fail-over와 recovery 결과는 query 및 observability query로 조회 가능해야 한다.

구현 시 코딩 에이전트는 hang, deadlock, stale lock, duplicate execution, partial side effect, retry storm을 깊게 고려해야 한다.

### Operational Observability Storage

DMS는 운영 디버깅을 위해 critical lifecycle state를 운영용 PostgreSQL에 저장하고, diagnostic observability event를 observability/log용 PostgreSQL에 저장한다.

관찰성 데이터는 외부 log/monitoring system을 대체하기 위한 것이 아니라, DMS source of truth와 연결된 운영 이력, 상태 전이, 실패 원인을 운영용 PostgreSQL의 critical lifecycle state와 observability/log용 PostgreSQL의 diagnostic event 기준으로 추적하기 위한 것이다.

운영용 PostgreSQL에는 operation correctness와 recovery에 필요한 critical lifecycle state를 저장한다.

Observability/log용 PostgreSQL에는 운영 디버깅에 필요한 diagnostic observability event를 저장한다.

Operation 관련 데이터는 critical lifecycle state와 diagnostic observability event로 구분한다.

Critical lifecycle state:

- request
- plan
- run claim
- run status
- result
- desired state
- applied state
- verification state
- resource current desired state
- resource observed state

Diagnostic observability event:

- 상세 structured log
- latency metric
- debug event
- component log
- system monitoring log

운영용 PostgreSQL에 기록해야 하는 critical lifecycle state:

- resource `create`, `update`, `block`, `initialize`, `delete` request의 before state
- request가 목표로 한 desired state
- request payload에 포함된 `requester_id`
- Worker runtime이 실제 적용한 applied state
- 적용 후 verification state
- request, plan, run, result 단계별 성공/실패 이력
- recovery-needed 또는 unknown-after-side-effect 상태
- resource current desired state
- resource observed state

Observability/log용 PostgreSQL에 기록할 diagnostic observability event:

- API 호출 이력
- API 호출 성공/실패 여부
- API latency
- actor 정보. 여기서 actor는 API 호출 주체, Worker runtime 실행 주체, Agent, system job, diagnostic event 발생 주체 같은 실행 또는 이벤트 주체이며, `requester_id`와 같은 개념이 아니다.
- request id 및 request payload 요약
- error 요약 및 error category
- Worker runtime의 중요한 구조화 로그
- API server의 중요한 구조화 로그
- Agent의 중요한 구조화 로그
- DMS 운영상의 system monitoring log

관찰성 데이터 조회 원칙:

- Observability query는 운영용 PostgreSQL의 critical lifecycle state와 observability/log용 PostgreSQL의 diagnostic observability event를 함께 조회할 수 있어야 한다.
- Query API는 운영 디버깅에 필요한 정보를 가능한 많이 반환한다.
- API 호출 주체가 받은 payload를 목적에 맞게 필터링, 요약, 시각화한다.
- resource, request id, requester id, actor, component, time range, success/failure, error category 기준으로 조회 가능해야 한다.
- `requester_id`는 request payload에 포함된 business/audit requester identity이고, `actor`는 인증된 API 호출 주체 또는 observability event를 발생시킨 API server, Worker runtime, Agent, system job 같은 실행 또는 이벤트 주체다.
- Diagnostic observability event는 중요도와 종류별 retention을 가져야 하며, 중요하지 않은 데이터는 retention policy에 따라 삭제될 수 있어야 한다.
- Retention cleanup, dashboard query, large log scan은 observability/log용 PostgreSQL에서 수행하여 운영용 PostgreSQL의 critical path에 영향을 주지 않도록 한다.
- Critical lifecycle state 저장 실패는 DMS 핵심 resource operation을 성공으로 처리하지 못하게 해야 한다.
- Backend side effect 이후 critical lifecycle state 저장에 실패하면 DMS는 해당 run을 success로 간주하지 않고 recovery-needed 또는 unknown-after-side-effect 상태로 다뤄야 한다.
- recovery-needed 또는 unknown-after-side-effect 상태의 작업은 같은 role의 다음 Worker runtime 또는 recovery flow가 live backend verification을 수행하여 운영용 PostgreSQL state를 reconcile해야 한다.
- Diagnostic observability event 저장 실패는 가능한 한 core resource operation을 실패시키지 않는다.
- Diagnostic observability event 저장 실패 자체는 최소한의 critical error state 또는 fallback log로 남겨야 한다.
- 필요하면 observability/log용 PostgreSQL에 저장된 diagnostic observability event를 외부 log/monitoring system으로 export할 수 있어야 한다.
- 상세 table schema, index, retention policy, log payload 구조는 구현 단계에서 결정한다.

현재 구현은 `DMS_OBSERVABILITY_DATABASE_URL`이 `DMS_DATABASE_URL`과 다르면 diagnostic event write/query를 별도 SQLAlchemy session factory로 분리한다. 운영 DB에는 request/plan/run/result/state transition/resource/data job/action issue 같은 critical lifecycle state가 남고, observability/log DB에는 `diagnostic_events`가 남는다. `/v1/ops/observability`는 diagnostic event 필터 조회를 제공하고, `/v1/ops/correlation`은 운영 DB의 lifecycle state와 observability/log DB의 diagnostic event를 `request_id`, `job_id`, `resource_key` 기준으로 묶어 반환한다. Diagnostic event 저장 실패는 core operation을 가능한 한 실패시키지 않고 운영 DB transaction 안에 `observability_write_failed` fallback event를 남기려 시도한다.

현재 구현의 worker transaction boundary는 claim과 `Applying`/`Running` state transition을 backend side effect 전에 commit하고, terminal/blocked/handled failure 결과를 worker 내부에서 다시 commit하는 방식이다. 따라서 backend side effect 이후 final result commit 전에 worker가 종료되더라도 request/plan/run은 이미 `Applying` 또는 `Running`으로 남아 startup recovery와 action-required query의 대상이 된다.

현재 구현은 `dms planner-loop`, `dms worker-loop --role rm`, `dms worker-loop --role dm`을 Kubernetes Deployment로 배포하여 운영 DB의 request/plan/lease를 지속 처리한다. 수동 `run-once` API는 검증과 break-glass 운영용으로 유지한다. `dms agent-loop`는 DaemonSet으로 배포되어 node-local mount health를 `/v1/agent/reports`에 주기적으로 저장한다. `/v1/ops/agents`의 기본 view는 fresh/healthy report를 node별로 병합한 effective capability view이며, scheduler가 보는 mount/tool/credential union과 contributing report id/timestamp를 함께 반환한다. 개별 raw 최신 report가 필요한 디버깅과 audit에는 `effective=false`를 사용한다.

### Implementation Detail Boundary

이 설계 문서는 DMS의 중심 설계 철학, 운영 환경, 리소스 모델, lifecycle, 주요 설계 제약, 구현 완료 검증 기준을 기록한다. 코드 에이전트가 이 문서를 구현 입력으로 사용할 때는 아래 원칙을 따라 세부 구현을 채워야 한다.

다음 항목은 실제 구현 시 코딩 에이전트가 이 문서의 원칙을 기준으로 설계하고 반영한다. 이 문서에서는 구체적으로 명시하지 않는다.

구현 단계에서는 코딩 에이전트가 전체 코드베이스의 구조, consistency, 테스트 가능성, 운영 안정성, 구현 복잡도를 고려하여 최선의 결정을 한다. 따라서 이 문서는 별도의 `V1 Implementation Contract`를 고정하지 않는다.

구현 시 cluster 이름, namespace, storage backend, mount path, image tag, queue 이름, timeout, quota 기본값, credential reference 같은 환경별 값은 가능한 한 코드에 하드코딩하지 않고 configuration, manifest values, 운영용 PostgreSQL state, 또는 metadata 입력으로 관리한다. 불가피하게 하드코딩이 필요한 경우에는 해당 값이 필요한 이유, 적용 범위, 변경 방법, 제거 또는 configuration 전환 계획을 코드 주석이나 관련 문서에 남겨야 한다.

- 세부 DB schema
- 세부 API spec
- Planner, Worker runtime, Backend 사이의 구체적인 interface
- 내부 table 분리 방식
- 내부 payload 구조
- adapter method signature

## API Modules

DMS API는 요청이 다루는 대상과 처리 책임에 따라 다음 모듈로 구분한다.

- Resource Management API: 리소스 관리 요청을 받아 처리하는 API 모듈.
- Data Management API: 데이터 이동, 복제, 삭제, 디렉토리 분석 등의 데이터 관리 요청을 받아 처리하는 API 모듈.
- Identity Mapping API: DMS requester identity와 LDAP/SSSD 중앙 identity system의 UID/GID/group 조회 결과를 DMS 내부 mapping data로 등록, 검증, 조회, 비활성화하는 공통 API 모듈.
- Operational Query API: 운영자 장애 대응, 정기 점검, 용량 관리, 감사 대응을 위한 read-only list/detail/history query API 모듈.

현재 이 문서는 Resource Management API를 중심으로 상세 내용을 기록한다. Data Management API는 초기 operation 범위와 job 실행 원칙을 설계 수준에서 정의하며, Identity Mapping API는 requester identity와 POSIX identity mapping의 처리 원칙과 예시를 정의한다. Operational Query API는 운영자가 조치 대상을 빠르게 찾는 데 필요한 공통 조회 capability를 정의한다. 구체적인 endpoint, request/response schema, error schema, Volcano Job manifest 구조는 구현 단계에서 확정한다.

### Capability Contract Matrix

이 표는 endpoint 이름이나 HTTP method를 고정하기 위한 API spec이 아니라, 구현 에이전트가 각 capability의 책임 경계와 side effect 여부를 일관되게 해석하기 위한 계약이다. Resource/Data mutating capability는 인증과 operation authorization을 통과한 뒤 request lifecycle에 기록되어야 하며, Backend side effect는 plan이 운영용 PostgreSQL에 저장된 뒤에만 수행한다.

Direct Control Mutation은 DMS control/config state 또는 운영 제어 action을 즉시 적용하는 별도 경로다. 이 경로는 resource/data request lifecycle에 넣지 않고 운영용 PostgreSQL의 `control_mutations` audit record로 추적한다. 현재 Direct Control Mutation class는 `direct_config_mutation`, `operational_action`, `manual_executor_action`이다.

Resource Management capability contract:

| Capability | Resource key | Mutating | Planner | Executor | Backend side effect | Required result/evidence |
| --- | --- | --- | --- | --- | --- | --- |
| filesystem create | `storage_name + directory_name` | yes | required | RM Worker runtime | directory create, permission, quota | desired/applied/observed state, final verification |
| filesystem update | `storage_name + directory_name` | yes | required | RM Worker runtime | quota, metadata, access control update | before/after desired state, observed state |
| filesystem block | `storage_name + directory_name` | yes | required | RM Worker runtime | readonly chmod or root-owned chmod access block | block mode, preserved restore mode, verification |
| filesystem initialize | `storage_name + directory_name` | yes | required | RM Worker runtime | quota reset only | default policy used, new quota, observed state |
| existing directory quota assignment | `storage_name + directory_name` | yes | required | RM Worker runtime | quota apply only | quota-only managed state, observed quota |
| import existing filesystem directory | `storage_name + directory_name` | yes | required | RM Worker runtime | normally read/record, optional verification commands | imported state, access control interpretation, final verification |
| resource consistency check | single resource key or explicit scope such as `storage_name` | yes for DMS verification state, no backend mutation | required | RM Worker runtime or control-plane live reader | read-only live backend check only | existence result, DB/live diff, observed state snapshot |
| resource lifecycle delete | resource-specific | yes | required | RM Worker runtime | filesystem directory delete or DMS-managed Kubernetes ResourceQuota delete | deletion evidence, lifecycle state `Deleted`, namespace preservation evidence for Kubernetes |
| expiration sweep | all managed resources | yes | required | Planner and RM Worker runtime through generated block requests/plans | indirect block side effect | sweep summary and child block request/result links |
| default quota policy update | policy key | yes | not required, Direct Control Mutation | API server/control plane | DB policy state only | old/new policy, actor, existing resources auto-applied=false |
| storage mapping management | `storage_name` | yes | not required, Direct Control Mutation | API server/control plane and Agent/inventory validation | DB mapping state, sanity check | mapping version, sanity result, active-work rejection, impact analysis |
| Kubernetes namespace quota create | `cluster_name + namespace_name` | yes | required | RM Worker runtime | namespace optional create, ResourceQuota create/update | ResourceQuota spec/status, labels/annotations |
| Kubernetes namespace quota update | `cluster_name + namespace_name` | yes | required | RM Worker runtime | ResourceQuota update | quota delta, effective usage check, observed state |
| Kubernetes namespace quota block | `cluster_name + namespace_name` | yes | required | RM Worker runtime | ResourceQuota hard limits set to zero or restored | preserved quota, applied hard limits, status.used |
| Kubernetes namespace quota DB sync from live state | `cluster_name + namespace_name` | yes | required | RM Worker runtime or control-plane live reader | DB update from live ResourceQuota | live spec/status, previous DB state, updated DB state |
| resource query | resource-specific | no | not required | API server or query service | none, optional live read only | stored state plus optional observed refresh |

Data Management capability contract:

| Capability | Job/resource key | Mutating | Planner | Executor | Backend side effect | Required result/evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `sync` | `job_id`, source, destination | yes | required | DM Worker runtime plus Volcano worker pod | data copy/update after confirm | selected tool, preview, confirm, logs, report URI |
| `rm` | `job_id`, target | yes | required | DM Worker runtime plus Volcano worker pod | data delete after confirm | preview, confirm, deleted summary, logs |
| `scan` | `job_id`, target | no data mutation | required | DM Worker runtime plus Volcano worker pod | read-only filesystem scan | scan report URI, summary counts, errors |
| `help` | operation name | no | not required | API server | none | operation description, allowed options, tool mapping |
| `cancel` | `job_id` | yes for lifecycle state | conditional | API server and DM Worker runtime | terminate VolcanoJob if already created | cancelled state, termination evidence |
| job detail/query | `job_id` | no | not required | API server or query service | none | lifecycle state, selected tool, worker pool, artifacts |

Identity and operational capability contract:

| Capability | Key | Mutating | Planner | Executor | Backend side effect | Required result/evidence |
| --- | --- | --- | --- | --- | --- | --- |
| identity mapping register/update | `requester_id + identity_provider` | yes | not required, Direct Control Mutation | API server/control plane | DMS DB write, central identity read only | UID/GID/groups, verification result, control mutation audit |
| identity mapping refresh | `requester_id + identity_provider` | yes | optional | API server, Agent, or control-plane executor | DMS DB update, central identity read only | previous/current mapping, stale status |
| identity mapping query/list | mapping key or filter | no | not required | API server or query service | none | mapping state and verification metadata |
| identity mapping disable | mapping key | yes | optional | API server or control-plane executor | DMS DB state update only | disabled state, requester, reason |
| maintenance/drain control | DMS deployment or component scope | yes for DMS control state | not required, Direct Control Mutation | API server/control plane | no user resource mutation | maintenance mode, drain status, blocked scheduling evidence, control mutation audit |
| startup recovery check | DMS deployment or component scope | yes for recovery state | not required, Direct Control Mutation operational action | control plane and Worker runtime | read-only live backend check only | stale lease recovery, resumed component readiness, unresolved issue list, control mutation audit |
| action-required query | operational issue filter | no | not required | query service | none | current issue list with severity, reason, suggested action |
| Operational Query API | filter-specific | no | not required | query service | none, optional live read only | list/detail/history response with diagnostic correlation |
| Observability query | event/filter-specific | no | not required | query service | none | lifecycle/event correlation and retention-aware result |

## Identity Mapping API

Identity Mapping API는 DMS request의 `requester_id`를 중앙 identity system에서 조회 가능한 POSIX identity로 연결하기 위한 공통 API다. Resource Management API와 Data Management API는 이 mapping을 사용해 requester identity, UID, primary GID, supplementary group, POSIX permission 검증 결과를 일관되게 기록하고 판단한다.

초기 API 범위:

- requester identity mapping 등록 또는 갱신
- 등록된 mapping의 LDAP/SSSD read 검증 및 refresh
- mapping 조회와 list
- mapping 비활성화

처리 원칙:

- 중앙 identity system은 read-only source로 사용한다.
- DMS는 LDAP/SSSD 같은 중앙 identity system에 user, group, group membership을 생성, 수정, 삭제하지 않는다.
- Identity Mapping API는 운영용 PostgreSQL에 DMS 내부 mapping data를 기록하는 API이며, 중앙 identity system에 write하는 API가 아니다.
- 등록 요청에는 `requester_id`, `identity_provider`, `posix_username`을 포함한다. UID, primary GID, group list는 expected value로 줄 수 있지만, 최종 저장 값은 중앙 identity system read 결과와 검증 결과를 기준으로 한다.
- DMS는 LDAP read, SSSD NSS 조회, 또는 target worker node의 `getent`, `id` 같은 read-only 조회를 통해 `posix_username`, UID, GID, group membership을 검증해야 한다.
- 요청의 expected UID/GID/group과 중앙 identity system 조회 결과가 불일치하면 mapping 등록은 실패하거나 `NeedsReview` 같은 비활성 상태로 기록해야 한다. 이 경우 Data Management job과 filesystem access control 작업의 authorization에 사용하지 않는다.
- Mapping은 `Active`, `Disabled`, `NeedsReview`, `Stale` 같은 상태를 가질 수 있어야 한다. 오래된 mapping은 refresh 또는 재검증 후 사용해야 한다.
- 구현 기준으로 `/v1/identity-mappings/refresh`는 중앙 identity를 다시 read-only 조회하고, 요청 시 fresh Agent report의 worker-side `identities` evidence를 비교한다. 중앙 UID/GID/group drift 또는 worker mismatch가 있으면 기존 저장 UID/GID/group을 덮어쓰지 않고 `Stale`, `stale_at`, `mismatch_reason`을 기록한다. 새 중앙 값을 받아들이려면 `/v1/identity-mappings` upsert를 다시 호출한다.
- `/v1/identity-mappings/disable`은 mapping을 `Disabled`로 전환하고 `disabled_at`과 reason을 남긴다. Disabled mapping은 refresh로 되살리지 않으며, 다시 사용하려면 명시적으로 upsert한다.
- `/v1/identity-mappings` list는 `requester_id`, `identity_provider`, `status`, `failed=true` 필터를 제공해 `NeedsReview`/`Stale`/`Disabled` mapping을 운영자가 조회할 수 있어야 한다.
- Data Management API는 authenticated request payload의 `requester_id` mapping을 기준으로 POSIX permission preflight와 job 실행 identity를 결정한다.
- Resource Management API의 filesystem access control 검증은 DMS 내부 mapping과 중앙 identity system의 read-only 조회 결과를 기준으로 수행한다.

Identity Mapping 등록 요청 예:

```json
{
  "request_id": "identity-map-20260513-001",
  "requester_id": "portal:alice",
  "identity_provider": "ldap-main",
  "posix_username": "alice",
  "expected_uid": 12001,
  "expected_primary_gid": 12000,
  "expected_groups": [
    "dms-users",
    "project-a100"
  ],
  "memo": "Alice requester identity for DMS data management jobs"
}
```

DMS 검증 예:

```bash
getent passwd alice
id -u alice
id -g alice
id -G -n alice
```

등록 결과 예:

```json
{
  "request_id": "identity-map-20260513-001",
  "requester_id": "portal:alice",
  "status": "Active",
  "identity_provider": "ldap-main",
  "posix_username": "alice",
  "uid": 12001,
  "primary_gid": 12000,
  "groups": [
    {
      "name": "dms-users",
      "gid": 12000
    },
    {
      "name": "project-a100",
      "gid": 31001
    }
  ],
  "verified_at": "2026-05-13T00:00:00Z"
}
```

Refresh 요청 예:

```json
{
  "requester_id": "portal:alice",
  "identity_provider": "ldap-main",
  "verify_workers": true,
  "worker_role": "dm",
  "node_names": [
    "c1-control",
    "c1-worker"
  ],
  "worker_report_max_age_seconds": 120
}
```

Disable 요청 예:

```json
{
  "requester_id": "portal:alice",
  "identity_provider": "ldap-main",
  "reason": "requester deprovisioned"
}
```

Data Management 요청에서의 사용 예:

```json
{
  "request_id": "sync-20260513-001",
  "requester_id": "portal:alice",
  "operation": "sync",
  "source": {
    "storage_name": "a100-weka-2021",
    "path": "datasets/input"
  },
  "destination": {
    "storage_name": "h100-weka-2024",
    "path": "datasets/input-copy"
  }
}
```

이 경우 DMS는 `portal:alice` mapping을 조회하고, LDAP/SSSD read 검증 결과로 확인된 `alice`의 UID/GID/group을 기준으로 source read 권한과 destination directory write 권한을 preflight에서 검증한다.

## Operational Query API

Operational Query API는 운영자가 장애 대응, 정기 점검, 용량 관리, 감사 대응을 위해 사용하는 DMS 공통 read-only 조회 API다. 이 API는 Resource Management API, Data Management API, Identity Mapping API, Worker/Agent 상태, observability event를 운영 관점에서 연결해 보여준다.

운영 지원 query capability:

- 현재 DMS에서 운영자 review 또는 action이 필요한 항목 조회. Failed, error, warning, drift, stale, timeout, authorization-denied, identity mismatch, worker/agent unhealthy, resource missing 같은 현재 조치 대상을 하나의 operational issue list로 반환해야 한다.
- 특정 resource의 lifecycle history 조회. Create, update, block, initialize, import, quota assignment, DB sync, expiration sweep에서 파생된 block request, verification result를 시간순으로 조회할 수 있어야 한다.
- 특정 requester의 resource request history 조회. `requester_id`, time range, operation, resource kind, status, error category 기준으로 필터링할 수 있어야 한다.
- 만료된 resource list 조회. `expires_at`, resource kind, type, current block state, expiration sweep 처리 여부, skip/failure reason을 함께 반환해야 한다.
- 만료 예정 resource list 조회. configurable lookahead window를 기준으로 곧 만료될 resource를 조회할 수 있어야 한다.
- `block=ON` 상태 resource list 조회. block mode, block reason, block request id, blocked_at, requester id, 원복 가능 state를 함께 반환해야 한다.
- failed, conflict, stale, recovery-needed, unknown-after-side-effect 상태의 request/run list 조회. 운영자가 후속 조치 대상을 빠르게 찾을 수 있어야 한다.
- pending, running, long-running request/run list 조회. Worker lease, heartbeat, attempt count, elapsed time, warning threshold 초과 여부를 함께 반환해야 한다.
- resource drift 또는 DB/live mismatch 후보 list 조회. 운영용 PostgreSQL desired/applied/observed state와 live backend 조회 결과가 다른 항목, resource consistency check의 `Drifted`/`Missing`/`CheckFailed` 결과, effective quota warning, inventory sanity check failure를 포함해야 한다.
- quota usage와 capacity pressure list 조회. resource별 quota, observed usage, usage ratio, count quota usage, threshold warning을 반환해야 한다.
- worker node, Worker runtime, Agent health와 capability inventory 조회. worker role, node, heartbeat, mount, CSI driver, storage capability, data-operation network reachability, tool capability를 확인할 수 있어야 한다. 기본 운영 view는 scheduler가 사용하는 effective capability와 일치해야 하며, raw report view는 디버깅과 audit 목적으로 별도 조회 가능해야 한다.
- storage backend, `storage_name`, StorageClass mapping history와 sanity check result 조회. mapping 변경 이력과 현재 적용 상태를 추적할 수 있어야 한다.
- Identity Mapping API의 stale, disabled, failed verification mapping list 조회. requester identity 관련 authorization 문제를 운영자가 빠르게 찾을 수 있어야 한다.
- default quota policy와 policy change history 조회. policy 변경 전후 값, requester id, request id, applied resource 영향 범위를 조회할 수 있어야 한다.
- maintenance/drain/rolling upgrade 상태 조회. 현재 maintenance mode, drain progress, blocked scheduling, component version, schema migration version, startup recovery check 결과를 운영자가 확인할 수 있어야 한다.
- Data Management Job history 조회. operation, requester, source/destination/target resource, priority, selected tool, status, preflight failure reason, timeout, cancel 여부 기준으로 조회할 수 있어야 한다.
- Data Management preview 상태 조회. confirm 대기 중인 preview, TTL 만료 preview, 위험 option이 포함된 preview를 조회할 수 있어야 한다.
- diagnostic event correlation 조회. request id, resource key, worker id, diagnostic event id를 기준으로 운영용 PostgreSQL의 lifecycle state와 observability/log용 PostgreSQL의 diagnostic event를 연결해 조회할 수 있어야 한다.

Action-required query는 단순히 모든 실패 이력을 나열하는 API가 아니라, 현재 운영자가 봐야 하는 unresolved issue를 우선순위와 함께 보여주는 read-only API다. 응답에는 issue id 또는 correlation key, severity, category, affected resource/job/worker, current state, first_seen, last_seen, related request/run/job id, diagnostic event id, recommended next action, suppress/acknowledge 가능 여부를 포함할 수 있어야 한다. 구체적인 acknowledge/suppress workflow는 구현 단계에서 결정하되, 이 query 자체는 Kubernetes object, filesystem state, 중앙 identity system state를 변경하지 않는다.

Operational Query API는 기본적으로 read-only API다. Live backend 조회를 수행할 수는 있지만, Kubernetes object, filesystem state, 중앙 identity system state를 변경해서는 안 된다.

## DMS Execution Topology and Worker Roles

DMS 시스템 자체는 별도의 DMS 전용 Kubernetes cluster인 DMS Kubernetes cluster에서 동작한다. DMS API server, Frontend, Planner, control plane component, DM Worker runtime은 이 cluster에 배포된다. 운영용 PostgreSQL과 observability/log용 PostgreSQL도 DMS Kubernetes cluster 내부에 사전 배포된 HA PostgreSQL service 또는 동등한 in-cluster PostgreSQL endpoint로 제공된다.

`RM Worker`와 `DM Worker`는 실제 Kubernetes worker node 역할이며, 다음 두 종류로 구분한다.

- RM Worker: 각 managed Kubernetes cluster에 별도로 배포되는 dedicated Kubernetes worker node다. GPU workload node와 구분되며, RM Worker runtime이 Kubernetes workload로 이 node에 scheduling된다. 이 node는 해당 cluster의 Kubernetes API, StorageClass, CSI driver, filesystem mount, quota capability, identity/group system 상태를 cluster-local하게 검증하고 resource management 작업을 실행한다.
- DM Worker: DMS Kubernetes cluster에 포함되는 Kubernetes worker node다. DM Worker runtime이 Kubernetes workload로 이 node에 scheduling된다. 이 node에는 네트워크 제약, mount 제약, filesystem client, 운영 정책을 고려해 데이터 이동, 복제, 삭제, 디렉토리 분석 대상 storage가 filesystem으로 mount된다. 모든 DM Worker가 모든 storage를 mount한다고 가정하지 않으며, node별 capability를 기준으로 실행 가능 작업을 판단한다.

`RM Worker`와 `DM Worker`는 배포 위치와 node-local capability의 기준 단위이고, Worker runtime은 plan claim, 실행, 검증, result 기록을 수행하는 논리적 실행 주체다. `worker_id` 또는 `executor_id`는 실행 주체를 식별할 수 있어야 하며, 구현 단계에서는 worker role 또는 worker type도 추적할 수 있어야 한다. Worker runtime이 운영용 PostgreSQL에 직접 접근하는지, DMS control plane을 경유하는지는 내부 interface 설계에서 결정한다.

Resource management plan은 RM Worker runtime이 claim하고, data management plan은 DM Worker runtime이 claim한다. 서로 다른 worker role이 다른 API 모듈의 plan을 실행하지 않도록 plan claim 조건과 scheduling 조건을 분리해야 한다.

DM Worker runtime은 data management plan을 claim하고 VolcanoJob을 생성, 감시, 종료하며 상태와 결과를 운영용 PostgreSQL 및 observability/log용 PostgreSQL에 기록하는 controller다. Volcano worker pod는 mpifileutils 실행, stdout/stderr 생성, report/artifact 생성만 담당하며, DMS lifecycle state의 source of truth를 직접 소유하지 않는다.

DMS Agent는 Worker runtime과 논리적으로 분리된 node-local capability reporting component다. Agent는 RM Worker와 DM Worker에 Kubernetes workload로 배포되며, mount, CSI driver, network, credential, tool capability, local execution 상태를 보고한다. 실제 구현에서는 Worker runtime과 Agent가 같은 pod 또는 process로 통합될 수 있다.

### Component Responsibility Matrix

이 표는 구현 에이전트가 컴포넌트 내부 구조를 자유롭게 설계하더라도 지켜야 하는 책임 경계다. 실제 프로세스, deployment, package 분리는 구현 단계에서 달라질 수 있지만, `must not`에 해당하는 동작은 다른 컴포넌트로 새지 않아야 한다.

| Component | Owns | May write Operational PG | May write Observability PG | May call external backend | Must not |
| --- | --- | --- | --- | --- | --- |
| DMS API Ingress/auth layer | TLS termination, mTLS client certificate verification, token forwarding or validation integration | no, except implementation-chosen auth audit path | yes, auth diagnostic event | no | create plan, run backend side effect |
| API server / Frontend | request acceptance, request validation envelope, authn/authz integration, request persistence, status/query surface | yes, request/result for rejected or accepted requests | yes | only read-only live query if explicitly implemented | apply Kubernetes/filesystem/storage mutation directly |
| Planner | precondition check, resource ordering, plan creation, conflict/stale decision | yes, plan state, conflict/rejected result | yes | read-only backend/inventory lookup if needed | perform long-running backend side effects |
| Resource Management Backend adapter | Kubernetes/filesystem/storage operation implementation detail | through RM Worker runtime or controlled interface | yes | yes | decide business lifecycle without plan/precondition |
| RM Worker runtime | claim resource management plan, execute adapter call, verify live state, record result | yes, run/result/observed state/lease | yes | yes, cluster-local Kubernetes/filesystem/storage | execute Data Management plan |
| DM Worker runtime | claim data management plan, build candidate pool, create/monitor/terminate VolcanoJob, record job state | yes, Data Management Job/run/result/lease | yes | Kubernetes API for VolcanoJob and read-only inventory | run mpifileutils payload directly as source of truth without job lifecycle |
| Volcano worker pod | mpifileutils execution, stdout/stderr/report/artifact production | no direct lifecycle ownership by default | logs/artifacts through configured path | filesystem/data path access | update DMS lifecycle state as source of truth |
| DMS Agent | node-local capability report, mount/CSI/tool/network/credential visibility | optional through server-validated ingestion only | yes | local node probes, read-only checks | mutate resource desired state or run user operation |
| Operational Query service | read-only list/detail/history across lifecycle state | no, except optional observed-state refresh through defined query path | yes for query diagnostic | read-only live backend query if requested | mutate Kubernetes/filesystem/storage |
| Maintenance/upgrade controller | maintenance mode, drain coordination, startup recovery check | yes, maintenance/drain/recovery state | yes | Kubernetes API read, controlled DMS component rollout integration if implemented | mutate user resources or bypass normal authorization |
| Migration/setup job | schema migration and initial seed data | yes, migration metadata and seed state | yes | PostgreSQL only, plus setup validation calls | perform user resource operations |

PostgreSQL write path는 구현 단계에서 direct DB access, internal API, queue, repository layer 중 하나로 설계할 수 있다. 단, critical lifecycle state의 source of truth는 운영용 PostgreSQL이며, observability/log용 PostgreSQL만으로 operation correctness를 판단해서는 안 된다.

## Resource Management API

Resource Management API는 리소스 관련 요청을 API로 받고 처리한다.

이 섹션에서 별도 수식 없이 쓰는 Worker는 RM Worker runtime을 의미한다.

이 섹션은 DMS가 제공해야 하는 API capability를 정의한다. 구체적인 endpoint, HTTP method, request/response schema, error schema는 이 문서에서 확정하지 않는다.

초기 API 범위:

- `create`
- `update`
- `block`
- `initialize`
- `query`
- expiration sweep
- observability query
- existing directory quota assignment
- import existing filesystem directory
- Kubernetes namespace quota import/adoption
- resource consistency check
- Kubernetes namespace storage quota DB sync from live state
- storage backend template and `storage_name` mapping management

`delete` API는 실제 backend side effect가 있는 명시적 lifecycle operation으로 구현할 수 있다. 삭제는 위험도가 높으므로 구현은 대상 resource kind별 안전장치를 둔다. Filesystem resource delete는 `delete_backend=true` 같은 명시적 확인 없이는 실패해야 하고, Kubernetes namespace storage quota delete는 기본적으로 DMS가 관리하는 ResourceQuota만 삭제하며 namespace 자체 삭제는 별도 운영 정책으로 남긴다.

Resource Management API의 `delete`는 DMS-managed resource lifecycle 삭제를 의미한다. Data Management API의 `rm`은 target directory 삭제 요청을 의미하며 Resource Management API의 resource lifecycle `delete`와 구분한다.

Operation 의미:

| Operation | 대상 | 의미 |
| --- | --- | --- |
| `create` | Filesystem resource, Kubernetes namespace storage quota resource | 새 DMS-managed resource의 desired state를 만들고 Backend 적용 계획을 생성한다. |
| `update` | 기존 resource 또는 DMS policy state | quota, metadata, type, 만료 시간, access control, 기본 quota policy 같은 기존 상태를 변경한다. |
| `block` | 기존 resource | resource 사용을 차단하거나 차단 해제한다. |
| `initialize` | 기존 resource | 대상 resource의 quota desired state를 현재 type별 기본 quota policy 기준으로 재설정한다. |
| `query` | request, resource, inventory, observability state | 운영용 PostgreSQL 상태를 기본 기준으로 조회하고, 필요한 경우 live backend 조회 결과와 observability/log용 PostgreSQL의 diagnostic event를 함께 반환한다. |
| `delete` | 기존 resource | DMS-managed backend object를 명시적으로 삭제하고 운영 DB lifecycle state를 `Deleted`로 기록한다. Filesystem delete는 명시 확인을 요구하고, Kubernetes delete는 DMS 전용 ResourceQuota 삭제를 기본 동작으로 한다. |
| expiration sweep | 만료된 resource 집합 | `expires_at` 기준으로 만료된 resource를 찾아 `block=ON` 처리 대상으로 전환한다. |
| existing directory quota assignment | 기존 filesystem directory | DMS가 생성하지 않은 directory를 quota-only managed resource로 등록하고 quota를 적용한다. |
| import existing filesystem directory | 기존 filesystem directory | DMS가 생성하지 않은 기존 directory를 검증 후 full DMS-managed filesystem resource로 전환하고, 현재 filesystem 상태를 초기 desired/applied/observed state로 기록한다. |
| Kubernetes namespace quota import/adoption | 기존 DMS 전용 Kubernetes ResourceQuota | 운영용 PostgreSQL에 등록되지 않았거나 복구가 필요한 DMS 전용 `ResourceQuota/dms-storage-quota`를 검증 후 Kubernetes namespace quota resource로 편입하고, live hard와 import expiry를 초기 desired/applied/observed state로 기록한다. |
| resource consistency check | DMS DB에 등록된 Filesystem resource, Kubernetes namespace storage quota resource | 운영용 PostgreSQL의 desired/applied/observed state와 실제 storage backend 또는 Kubernetes backend live state를 read-only로 조회해 resource 실재 여부와 상태 일치 여부를 비교하고 검증 결과를 기록한다. |
| Kubernetes namespace storage quota DB sync from live state | DMS-managed Kubernetes ResourceQuota | effective quota 경고 또는 DB 손실/불일치 상황에서 실제 Kubernetes의 DMS-managed ResourceQuota 상태를 검증하고 운영용 PostgreSQL의 quota state를 live state 기준으로 갱신한다. |
| storage backend template and `storage_name` mapping management | storage backend template, `storage_name` mapping | 운영 중(runtime)에 새 storage mapping을 추가하거나 기존 mapping을 수정, 비활성화하고, Kubernetes API inventory와 DMS Agent report를 기준으로 sanity check를 수행한다. 이 capability도 다른 DMS API와 동일한 인증 및 operation authorization policy를 따른다. |

기본 quota policy를 생성하거나 변경하는 작업은 `initialize`가 아니다. 이 작업은 `update` capability 안의 별도 policy update 동작으로 처리한다.

API 처리 원칙:

- 모든 Resource Management API request는 운영용 PostgreSQL에 기록한다.
- 모든 Resource Management API request는 `requester_id`를 포함해야 한다.
- DMS 외부 API는 mTLS(mutual TLS, client certificate 검증 포함)와 token 기반 API 인증을 요구한다. 이 인증을 통과한 요청은 authenticated request로 받아들인다.
- mTLS client certificate 또는 token에서 확인한 API 호출 주체는 `actor`로 기록한다. Actor는 operation authorization policy의 기본 판단 주체다.
- 구현은 trusted Ingress 또는 edge proxy가 mTLS client certificate을 검증한 뒤 upstream API로 전달하는 certificate evidence header를 인증 evidence로 사용할 수 있다. DMS edge proxy는 `X-DMS-Client-Cert-Subject`와 `X-DMS-Client-Cert-Verify: SUCCESS`를 사용하고, ingress-nginx는 `ssl-client-subject-dn`과 `ssl-client-verify: SUCCESS`를 사용한다. Edge는 기존 client-provided evidence header를 제거하거나 덮어써야 하며, 두 header family가 동시에 들어오고 값이 충돌하면 API는 인증을 거부한다. API Service direct access는 NetworkPolicy나 동등한 제어로 막아야 한다.
- DMS는 authenticated request의 payload에 포함된 `requester_id`를 requester identity로 신뢰한다. `requester_id`를 인증 주체에서 derive하거나 token claim과 대조하는 것을 필수로 하지 않는다.
- `requester_id`는 resource ownership, POSIX 권한 검증, audit 대상 identity로 사용한다.
- Operation authorization policy는 `actor`, operation, resource kind, target cluster/storage, dangerous option 여부 같은 입력을 기준으로 operation 수행 허가 여부를 판단한다. 구체적인 policy schema, role model, allow/deny rule format, policy 저장 방식은 구현 단계에서 확정한다.
- 인증은 성공했지만 operation authorization policy가 거부한 요청은 Backend side effect, plan, run을 생성하지 않고 `AuthorizationFailed` terminal result로 운영용 PostgreSQL에 기록한다. 이 결과에는 actor, `requester_id`, operation, target resource, policy decision reason, diagnostic event id를 포함해야 한다.
- 현재 구현의 기본 mutation authorization은 fail-closed다. `DMS_ADMIN_ACTORS`에 포함된 actor만 mutating request를 수행할 수 있고, 운영자가 `DMS_ALLOW_AUTHENTICATED_MUTATIONS=true`를 명시한 배포에서만 non-admin authenticated actor의 mutation을 허용한다.
- mTLS 또는 token 인증 자체가 실패한 요청은 request lifecycle에 넣지 않고 observability/log용 PostgreSQL에 diagnostic event로 기록한다.
- `requester_id`는 운영용 PostgreSQL의 request lifecycle state에 기록해야 한다.
- create/update/block/initialize/expiration sweep/quota assignment/import 같은 mutating 요청은 Backend에 즉시 반영하지 않는다.
- resource consistency check 요청은 Backend를 변경하지 않는 read-only verification operation이지만, check request, plan, run, result, observed state snapshot, DB/live diff를 운영용 PostgreSQL에 기록하므로 lifecycle request로 처리한다.
- query 요청은 운영용 PostgreSQL에 저장된 상태를 기본 조회 기준으로 사용한다.
- query 응답에 최신 backend 상태가 필요한 경우에는 Kubernetes API, filesystem, storage backend를 live 조회할 수 있다.
- live 조회를 수행한 경우 DMS는 가능한 한 조회 결과를 운영용 PostgreSQL observed state 또는 observability/log용 PostgreSQL diagnostic event로 기록한다.
- query 응답은 가능한 많은 정보를 반환하고, 응답을 받은 주체가 필요한 형태로 payload를 가공한다.
- `request_id`는 tracking, correlation, audit을 위한 DMS 전체 unique ID다. 같은 `request_id`가 다시 들어오면 새 요청으로 처리하지 않고 conflict로 판단한다.
- `request_id`는 payload replay나 동일 payload 재호출의 성공 응답 재사용을 의미하지 않는다.
- mutating request는 ordering consistency를 위해 대상 resource key, request commit order, resource version 또는 precondition을 기준으로 처리되어야 한다.
- 순서가 뒤바뀌어 도착한 요청은 Backend에 직접 반영하지 않고 Planner 단계에서 대기, 재시도, conflict, stale request 중 하나로 판단해야 한다.

Direct Control Mutation 처리 원칙:

- `storage_mapping.upsert`, `identity_mapping.upsert`, `default_quota_policy.upsert`, `control.maintenance`는 `direct_config_mutation`으로 분류한다.
- `observability.retention_cleanup`, `control.startup_recovery_check`는 `operational_action`으로 분류한다.
- `control.planner_run_once`, `control.worker_run_once`는 `manual_executor_action`으로 분류한다.
- 이 작업들은 Resource/Data request lifecycle에 넣지 않고 `control_mutations`에 `mutation_id`, `mutation_class`, `operation`, `target_key`, `actor`, before/after state, status, result/error summary를 기록한다.
- Authorization이 거부된 direct control mutation 시도도 `AuthorizationFailed` status로 audit record를 남긴다.
- Storage mapping 변경은 같은 `storage_name`을 참조하는 active request, plan, run, Data Management job이 있으면 `Conflict`로 거부한다.
- Default quota policy 변경은 기존 resource에 자동 적용하지 않는다. 기존 resource quota 변경은 별도 initialize/update request로 수행한다.
- Maintenance mode는 direct API path가 운영 DB control state를 즉시 변경하며, 별도 `control.maintenance` plan/worker path를 두지 않는다.

모든 request 공통 필드:

- `request_id`: DMS 전체에서 unique한 요청 ID.
- `requester_id`: 요청자 ID. Authenticated request payload에 포함된 값을 신뢰한다.
- `requested_at`: DMS가 요청을 접수한 timestamp.
- `memo`: optional 운영자 메모.

DMS는 request payload field와 별도로 인증된 API 호출 주체인 actor를 기록해야 한다. Actor는 operation authorization policy 판단, audit, observability query에 사용한다.

System job 또는 내부 자동화 요청도 request로 기록하는 경우 `requester_id`를 가져야 하며, 이 값은 해당 system job 또는 automation actor를 식별할 수 있어야 한다.

Quota 입력 해석 원칙:

- 요청에 quota가 명시되어 있으면 Planner는 명시 quota를 기준으로 desired state를 만든다.
- 요청에 quota가 명시되어 있지 않으면 Planner는 대상 resource에 기존 적용 quota가 있는지 먼저 확인한다.
- 기존 적용 quota는 운영용 PostgreSQL에 저장된 resource desired/applied/observed quota state를 우선 기준으로 판단한다. 필요한 경우 Backend live 조회로 검증할 수 있다.
- 기존 적용 quota가 있으면 Planner는 그 quota를 유지하는 plan을 만든다.
- 기존 적용 quota가 없으면 Planner는 resource kind와 type에 해당하는 기본 quota policy를 기준으로 desired state를 만든다.
- 기존 적용 quota도 없고 적용 가능한 기본 quota policy도 없으면 Planner는 요청을 실패 처리해야 한다.
- Update 요청에서 quota 필드가 생략되면 기존 quota를 유지한다. 단, 기존 quota가 없는 resource 상태라면 동일한 quota 입력 해석 원칙에 따라 default policy를 사용하거나 실패 처리한다.
- `reset_quota_to_default=true` 또는 `initialize`는 기존 적용 quota를 유지하지 않고 기본 quota policy 기준으로 quota를 재설정한다.

Create API 처리 원칙:

- Create 요청도 `request -> plan -> run -> result` lifecycle을 따른다.
- Frontend는 create 요청을 검증 가능한 request로 운영용 PostgreSQL에 기록한다.
- Planner는 quota 입력 해석 원칙에 따라 create plan의 quota desired state를 결정한다.
- 완전히 새로운 DMS-managed resource create는 기존 적용 quota가 없으므로, 명시 quota가 없으면 기본 quota policy를 사용한다.
- 완전히 새로운 DMS-managed resource create에서 명시 quota도 없고 적용 가능한 기본 quota policy도 없으면 Planner는 요청을 실패 처리해야 한다.
- RM Worker runtime은 plan을 기준으로 Backend에 resource를 생성하고, 생성 후 실제 상태를 다시 조회하여 observed state와 result를 운영용 PostgreSQL에 기록한다.

Update API 처리 원칙:

- Update 요청도 `request -> plan -> run -> result` lifecycle을 따른다.
- Frontend는 update 요청을 검증 가능한 request로 운영용 PostgreSQL에 기록한다.
- Planner는 운영용 PostgreSQL의 기존 resource state, inventory, observed state를 기준으로 update 가능 여부와 실행 plan을 만든다.
- RM Worker runtime은 plan을 기준으로 Backend를 통해 update를 적용하고, 적용 후 실제 상태를 다시 조회하여 observed state와 result를 운영용 PostgreSQL에 기록한다.
- Update는 quota 증가, quota 감소, metadata 변경, access control 변경, 만료 시간 변경처럼 성격이 다른 변경을 포함할 수 있으므로 Planner가 변경 유형을 구분해야 한다.
- Type별 기본 quota policy를 변경하는 작업은 `initialize`가 아니라 update의 별도 동작으로 처리한다.
- 기본 quota policy update는 운영용 PostgreSQL에 저장된 policy state를 변경하는 작업이며, 특정 resource의 Kubernetes ResourceQuota나 filesystem quota를 직접 변경하는 작업이 아니다.
- 기본 quota policy는 resource kind와 type을 기준으로 관리한다. 예를 들어 filesystem `type=user`, Kubernetes `type=project`는 서로 다른 policy다.
- 기본 quota policy update 후 기존 resource quota를 기본값으로 맞추려면 별도의 resource initialize 또는 `reset_quota_to_default=true` update request가 필요하다.

Block API 처리 원칙:

- Block 요청도 `request -> plan -> run -> result` lifecycle을 따른다.
- Block은 resource provisioning을 일시적으로 차단하거나 차단 해제하는 기능이다.
- Block 상태는 `ON` 또는 `OFF`로 표현한다.
- `ON` 요청은 대상 resource를 사용할 수 없거나 제한적으로만 사용할 수 있는 상태로 전환한다.
- `type=system`, `type=admin` resource에 대한 `block=ON` 요청은 실패해야 한다.
- `OFF` 요청은 block 이전의 원래 desired state로 복구한다.
- Planner는 block 적용 전 복구에 필요한 원래 desired state를 추적할 수 있어야 한다.
- RM Worker runtime은 block 적용 또는 해제 후 실제 backend 상태를 다시 조회하여 observed state와 result를 운영용 PostgreSQL에 기록한다.

Resource consistency check 처리 원칙:

- Resource consistency check는 운영용 PostgreSQL에 등록된 DMS-managed resource가 실제 backend에 존재하는지, 그리고 DB에 기록된 desired/applied/observed state와 live backend state가 일치하는지 비교하는 명시적 Resource Management API capability다.
- 이 operation은 Filesystem resource와 Kubernetes namespace storage quota resource를 모두 지원하지만, 하나의 요청이 항상 두 resource kind 전체를 일괄 scan하는 API로 해석되면 안 된다.
- 요청자는 check 범위를 명시해야 한다. 최소 범위는 단일 resource이고, 구현은 운영 편의를 위해 `storage_name` 단위 batch check, resource kind, cluster, namespace, type, requester, status, 최근 check 결과 같은 필터 기반 batch check를 지원할 수 있다.
- 범위가 명시되지 않은 요청을 전체 resource 일괄 check로 기본 처리하지 않는다. 전체 check가 필요하면 호출자가 명시적인 `all` 또는 동등한 scope를 지정해야 하며, 구현은 authorization, rate limit, pagination, chunking, timeout, partial result 처리를 적용해야 한다.
- 단일 Filesystem resource check는 `storage_name + directory_name`으로 대상을 식별한다.
- 단일 Kubernetes namespace storage quota check는 `cluster_name + namespace_name`으로 대상을 식별한다.
- `storage_name` scope는 해당 storage를 참조하는 DMS-managed resource set을 대상으로 한다. Filesystem resource에서는 같은 `storage_name`을 가진 모든 등록 resource가 대상이고, Kubernetes namespace storage quota resource에서는 `storage_class_quotas[].storage_name`이 해당 `storage_name`을 참조하는 resource가 대상이다. StorageClass별 quota가 없는 namespace-wide-only Kubernetes quota resource는 `storage_name` scope만으로 선택하지 않는다.
- 이 operation은 backend를 수정하지 않는다. Filesystem, storage backend, Kubernetes object에 대한 write, create, update, delete, quota repair를 수행하지 않는다.
- 이 operation은 단순 query와 다르다. Query는 저장 상태 조회가 기본이고 선택적으로 live 조회를 수행할 수 있지만, consistency check는 명시적으로 live backend 조회와 DB/live 비교를 수행하고 그 결과를 request lifecycle과 verification history로 기록한다.
- Frontend는 consistency check request와 requested scope를 운영용 PostgreSQL에 저장하고, Planner는 scope를 구체적인 target resource set으로 해석한 뒤 각 대상 resource가 운영용 PostgreSQL에 등록되어 있는지와 대상 resource kind별 check plan을 생성한다.
- RM Worker runtime 또는 구현상 동등한 control-plane live reader는 check plan을 claim하고 backend live state를 read-only로 조회한다.
- Filesystem resource check는 storage mapping, RM Worker mount visibility, directory existence, storage root boundary, owner, group, permission, ACL 사용 여부, quota limit, block mode, backend quota capability를 live 조회하고 DB state와 비교해야 한다. Phase 12 filesystem check/sync API는 대용량 directory에서 IO overhead가 큰 recursive usage scan을 수행하지 않으며 usage collection payload field를 제공하지 않는다.
- Kubernetes namespace storage quota check는 target cluster, namespace existence, DMS-managed ResourceQuota existence, ResourceQuota name, labels/annotations, `spec.hard`, `status.used`, namespace-wide quota, optional StorageClass-specific quota keys, derived StorageClass existence, CSI driver/storage mapping consistency를 live 조회하고 DB state와 비교해야 한다.
- Check result는 최소한 `Consistent`, `Drifted`, `Missing`, `CheckFailed` 같은 결과 범주를 구분할 수 있어야 한다. 구체적인 enum 이름은 구현 단계에서 정할 수 있다.
- `Consistent`는 live backend resource가 존재하고, DB desired/applied state와 정책상 비교해야 하는 live state가 일치함을 의미한다.
- `Drifted`는 resource는 존재하지만 quota, permission, ownership, ResourceQuota spec, label/annotation, StorageClass mapping 등 비교 대상 중 하나 이상이 DB state와 다름을 의미한다.
- `Missing`은 운영용 PostgreSQL에는 resource가 등록되어 있지만, live backend에서 해당 filesystem directory 또는 DMS-managed Kubernetes ResourceQuota/namespace를 찾을 수 없음을 의미한다.
- `CheckFailed`는 backend 조회 권한, network, mount, API, tool, timeout 문제로 비교 자체를 완료하지 못한 상태를 의미한다.
- Check result에는 requested scope, resolved target resource list 또는 summary, DB state snapshot, live state snapshot, diff summary, checked fields, skipped fields와 reason, worker/executor id, checked_at, diagnostic event id를 포함해야 한다.
- Consistency check는 mismatch를 자동 repair하지 않는다. Kubernetes namespace storage quota에서 live state를 DB 기준으로 다시 적용하려면 update flow를 사용하고, live state를 DB에 받아들이려면 Kubernetes namespace storage quota DB sync from live state API를 사용한다. Filesystem resource drift는 update, block, import, 또는 별도 repair flow로 처리한다.
- Consistency check 결과는 Operational Query API에서 drift 후보, missing resource, check failure, 최근 verification history로 조회 가능해야 한다.

Existing directory quota assignment 처리 원칙:

- DMS가 생성하거나 등록하지 않은 기존 filesystem directory에 대해서도 quota만 설정하는 별도 요청을 지원한다.
- 요청에는 대상 `storage_name`과 `directory_name`이 포함된다.
- `storage_name`은 storage root를 결정하며, `directory_name`은 해당 storage root 바로 아래의 directory 이름이다.
- 기존 directory quota assignment도 `storage_name + directory_name` 조합으로 대상을 식별한다.
- `directory_name`은 basename이며 path separator를 포함할 수 없다.
- Nested path 또는 relative path는 existing directory quota assignment 대상이 아니다.
- 이 기능은 directory lifecycle 전체를 DMS가 소유하는 것이 아니라, 기존 directory에 대한 quota 관리 상태를 DMS가 추적하는 모델이다.
- 요청이 접수되면 DMS는 해당 directory를 quota-only managed resource로 운영용 PostgreSQL에 기록해야 한다.
- 이후 query는 운영용 PostgreSQL에 기록된 quota-only 관리 상태와 observed state를 기준으로 응답한다.
- 예를 들어 Kubernetes namespace에서 임의로 생성된 PVC backend directory처럼 운영용 PostgreSQL에 resource로 등록되어 있지 않은 directory도, `storage_name`의 storage root 바로 아래 `directory_name`으로 식별 가능하고 quota capability가 있으면 quota 적용 대상이 될 수 있다.

Import existing filesystem directory 처리 원칙:

- Import 요청도 `request -> plan -> run -> result` lifecycle을 따른다.
- Import는 DMS가 생성하지 않았지만 이미 존재하는 filesystem directory를 full DMS-managed filesystem resource로 전환하는 명시적 operation이다.
- Import는 quota-only managed resource와 다르다. Import가 성공하면 DMS는 해당 directory의 quota뿐 아니라 update, block, access control, metadata, expiration, delete 같은 filesystem resource lifecycle을 소유한다. Delete는 별도 명시 확인과 안전장치를 거친다.
- 요청에는 대상 `storage_name`과 `directory_name`이 포함된다.
- `storage_name`은 storage root를 결정하며, `directory_name`은 해당 storage root 바로 아래의 directory 이름이다.
- `directory_name`은 basename이며 path separator를 포함할 수 없다.
- Nested path 또는 relative path는 import 대상이 아니다.
- 대상 directory는 실제 filesystem에 존재해야 하며, storage root 밖으로 escape하는 symlink, bind mount, path traversal, unsafe ownership 상태는 거부해야 한다.
- 대상 directory가 이미 full DMS-managed filesystem resource이면 import를 수행하지 않고 conflict 또는 no-op success로 처리해야 한다.
- 대상 directory가 quota-only managed resource이면 import를 통해 full DMS-managed filesystem resource로 승격할 수 있다. 이 경우 기존 quota-only 상태와 import 전환 이력을 운영용 PostgreSQL에 기록해야 한다.
- 대상 directory가 운영용 PostgreSQL에 등록되어 있지 않은 unmanaged directory이면 import 성공 시 새 DMS-managed filesystem resource로 기록한다.
- Import 요청은 optional `expires_at`을 받을 수 있다. 값이 주어지면 timezone-aware ISO-8601 timestamp로 검증하고 현재 시각보다 과거이거나 같으면 실패해야 한다.
- Import 요청에 expiry timestamp가 없으면 Planner 기준 server-side now부터 365일 뒤 값을 canonical `expires_at`으로 설정한다.
- `expiry_at`과 `clear_expires_at`은 import payload에서 지원하지 않는 field로 reject한다.
- 기존 filesystem marker에 expiry metadata가 있더라도 source of truth는 import request 값 또는 import default 값이다. 기존 marker 값은 observed evidence로 기록할 수 있지만 운영용 PostgreSQL desired state를 자동으로 덮어쓰면 안 된다.
- Planner와 RM Worker runtime은 import 전 directory의 현재 filesystem 상태를 live 조회해야 한다.
- 조회해야 하는 상태에는 owner, group, permission, ACL 사용 여부, quota 설정, filesystem type, storage backend capability가 포함된다.
- Access control import는 명시적으로 해석 가능해야 한다. 요청이 사용자 리스트 또는 DMS-managed group 정책을 제공하거나, DMS가 기존 Linux group membership을 중앙 identity system에서 해석할 수 있어야 한다. 둘 다 불가능하면 import는 실패해야 한다.
- Import 성공 시 현재 filesystem 상태를 초기 desired state, applied state, observed state로 기록한다. Import 자체는 요청에 명시된 전환 정책이 없는 한 기존 permission, ownership, group membership, quota를 조용히 덮어쓰지 않는다.
- Import는 final verification을 반드시 수행해야 한다. RM Worker runtime은 import plan 생성 시점에 읽은 filesystem state와 import 완료 직전 또는 직후 다시 조회한 filesystem state를 비교하고, 소유권 전환의 기준이 된 owner, group, permission, ACL, quota, backend capability가 정책상 허용되지 않게 변경된 경우 import를 success로 처리하지 않아야 한다.
- Import 대상 directory는 가능하면 운영 maintenance window 또는 외부 변경이 제한된 상태에서 처리해야 한다. 외부 변경 가능성이 높아 final verification 기준을 만족하지 못하면 DMS는 conflict, retryable failure, 또는 failed 상태로 기록해야 한다.
- Import 전후의 관리 모드 전환, requester id, request id, requested_at, imported filesystem state, 검증 결과는 critical lifecycle state로 운영용 PostgreSQL에 기록해야 한다.
- Import plan/run/result를 실제 실행한 RM Worker runtime 또는 recovery flow의 실행 주체는 `worker_id` 또는 `executor_id`로 추적해야 한다. Diagnostic observability event에서는 해당 실행 주체를 actor로 기록할 수 있다.

Initialize 처리 원칙:

- `initialize`는 특정 filesystem resource 또는 Kubernetes namespace storage quota resource를 대상으로 하는 resource-scoped operation이다.
- `initialize`는 type별 기본 quota policy 자체를 생성하거나 갱신하는 작업이 아니다.
- `initialize`는 cluster/storage inventory refresh, DB bootstrap, schema migration, 일반 sanity check를 의미하지 않는다.
- Resource initialize의 목적은 대상 resource의 quota desired state를 현재 운영용 PostgreSQL에 저장된 type별 기본 quota policy에 맞춰 재설정하는 것이다.
- 구현상 이 동작은 update의 한 형태로 표현할 수 있다. 예를 들어 `reset_quota_to_default=true` 같은 update option으로 모델링할 수 있다.
- Planner는 대상 resource가 운영용 PostgreSQL에 존재하는지, 대상 resource type에 해당하는 기본 quota policy가 존재하는지, quota reset이 정책상 허용되는지 검증해야 한다.
- 기본 quota로 재설정하는 과정에서 quota 감소가 발생할 수 있다. Filesystem reset은 사용량을 admission 입력으로 쓰지 않고 backend adapter 적용 결과를 기록하며, Kubernetes reset은 ResourceQuota `status.used`와 `force=true` 정책을 따른다.
- `initialize`는 quota reset 이외의 metadata, memo, expiration, type, 사용자 리스트, namespace 생성 여부를 변경하지 않는다.
- 대상 resource type에 기본 quota policy가 정의되어 있지 않으면 Planner는 요청을 실패 처리해야 한다.
- RM Worker runtime은 plan을 기준으로 Backend에 quota reset을 적용하고, 적용 후 실제 quota와 사용량을 다시 조회하여 observed state와 result를 운영용 PostgreSQL에 기록한다.

Expiration sweep 처리 원칙:

- DMS는 관리하는 모든 resource에 대해 expiration 기반 만료 처리를 수행하는 API를 제공한다.
- Expiration sweep API는 운영용 PostgreSQL에 저장된 `expires_at`과 resource 상태를 기준으로 만료된 resource를 찾는다.
- 만료된 resource는 `block=ON` 처리 대상이 된다.
- Expiration sweep은 Backend에 즉시 반영하지 않고, 만료된 resource별로 `block=ON` request 또는 plan을 운영용 PostgreSQL에 기록한다.
- 만료된 resource의 `type`이 `system` 또는 `admin`이면 `block=ON`으로 전환하지 않고 실패 또는 skip 결과로 기록한다.
- 이후 RM Worker runtime이 일반 block lifecycle과 동일하게 Backend를 통해 적용하고 검증한다.
- 이미 block 상태인 만료 resource는 중복 적용하지 않고 현재 block desired/applied state를 유지하는 방식으로 처리해야 한다.
- Expiration sweep 결과는 어떤 resource가 대상이었고, 어떤 resource가 block request/plan으로 전환되었으며, 어떤 resource가 skip되었는지 조회 가능해야 한다.

### External API Authentication

DMS 외부에서 DMS 내부로 API를 호출할 때는 mTLS(mutual TLS)와 token 기반 인증 정보가 필요하다. 이 문서에서 외부 DMS API 인증 문맥의 TLS는 server certificate 검증과 client certificate 검증을 모두 수행하는 mTLS를 의미한다. PostgreSQL 접속 설정에서 말하는 TLS는 별도 문맥이며 mTLS 사용 여부는 PostgreSQL 설정에 따라 결정한다.

외부 API 호출 원칙:

- DMS API는 Kubernetes Ingress를 통해 외부에서 호출할 수 있게 구성한다.
- 외부 client는 mTLS를 통해 DMS API endpoint에 접속해야 한다.
- 외부 client는 mTLS handshake에서 client certificate을 제공해야 하며, DMS API Ingress는 configured CA를 기준으로 client certificate을 검증해야 한다.
- 외부 client는 요청마다 token 기반 인증 정보를 제공해야 한다.
- mTLS client certificate 검증 또는 token 인증 정보가 없는 외부 요청은 거부한다.
- mTLS client certificate 검증과 token 인증을 통과한 요청은 authenticated request로 받아들이지만, 각 operation은 별도의 authorization policy 검증을 통과해야 한다.
- Ingress 또는 edge proxy는 client가 보낸 certificate evidence header를 그대로 신뢰하지 않아야 한다. DMS edge proxy는 `X-DMS-Client-Cert-*`와 `ssl-client-*`를 제거하고 verified client certificate에서 얻은 subject와 verify result를 upstream header로 설정해야 한다. ingress-nginx는 `auth-tls-*` annotation이 upstream에 전달하는 `ssl-client-subject-dn`과 `ssl-client-verify`를 사용한다.
- API server는 배포 설정에 따라 client certificate subject뿐 아니라 verified result(`SUCCESS`)도 요구할 수 있어야 한다.
- Maintenance/drain, rolling upgrade, shutdown readiness, startup recovery check 같은 운영 control capability도 동일한 외부 API 인증 원칙을 사용한다. 인증 체계를 일반/운영 API로 분리하지 않으며, 권한 부여는 capability별 authorization policy로 판단한다.
- 인증된 API 호출 주체인 actor는 client certificate subject/SAN 또는 token claim에서 derive할 수 있다. Actor 추출 규칙은 구현 단계에서 확정한다.
- DMS API의 server certificate과 client certificate 검증용 CA certificate은 운영자가 초기 셋업 시 제공하거나 참조할 수 있어야 한다.
- token 발급, 갱신, 권한 범위, 저장 방식의 세부 구현은 이 문서에서 확정하지 않는다.
- Ingress controller별 annotation, secret 이름, certificate 전달 방식 같은 세부 설정은 구현 단계에서 결정한다. 현재 구현 산출물은 ingress-nginx 예시 manifest, 테스트베드 ingress-nginx 설치/검증 스크립트, 테스트베드 mTLS proxy 검증 스크립트를 제공한다.

## Data Management API

Data Management API는 데이터 이동, 복제, 삭제, 디렉토리 분석 같은 데이터 관리 요청을 API로 받고 처리한다.

이 섹션은 Data Management API의 초기 operation 범위, 실행 도구 선택 원칙, 검증 원칙, Volcano 기반 job scheduling 원칙을 설계 수준에서 정의한다. 구체적인 endpoint, HTTP method, request/response schema, error schema, Volcano Job manifest 구조는 구현 단계에서 확정한다.

Data Management API의 작업은 DM Worker에서 실행되는 DM Worker runtime이 controller로 수행한다. DM Worker는 DMS Kubernetes cluster의 Kubernetes node로 구성되며, DM Worker runtime과 실제 Volcano worker pod는 Kubernetes workload로 해당 node에 scheduling된다. DM Worker runtime은 plan claim, VolcanoJob 생성/감시/종료, status/result 기록을 담당하고, Volcano worker pod는 mpifileutils 실행과 artifact 생성을 담당한다.

이 문서에서 `mpifileutils`는 DMS data operation에 사용하는 [mpifileutils repository](https://github.com/chahwansong/mpifileutils)를 의미한다. DMS는 운영자가 승인한 data management job image 안에 특정 mpifileutils git tag 또는 commit을 pinning하여 사용해야 한다. 단, mpifileutils repository가 업데이트되면 tag 또는 commit ref를 바꾸어 image를 재빌드하고 배포할 수 있는 절차를 편리하게 지원해야 한다. 요청자가 임의 image, credential, SSH 설정을 지정하는 것은 허용하지 않는다. Job image, Kubernetes Secret, ServiceAccount, credential은 DMS 운영자가 승인한 항목만 사용한다.

초기 API 범위:

- `sync`: source file 또는 source directory를 destination directory로 동기화하는 요청. 내부적으로 `dsync` 또는 `nsync`를 자동 선택한다.
- `rm`: target directory 삭제 요청. 내부적으로 `drm`을 사용한다.
- `scan`: target directory 분석 요청. 내부적으로 `dscan`을 사용한다.
- `help`: DMS operation 설명, request field 설명, operation별 허용 mpifileutils option과 내부 도구 매핑 정보를 반환한다.
- `cancel`: terminal state에 도달하지 않은 Data Management Job을 취소하고, 필요한 경우 관련 Volcano job을 terminate한다.

초기 data operation은 `sync`, `rm`, `scan` 세 가지지만, Data Management API는 이 목록에 고정되면 안 된다. 구현은 operation registry, operation별 request validator, option allowlist, dangerous option classification, preflight rule, preview/confirm policy, tool selection strategy, result artifact schema를 operation 단위로 확장할 수 있게 구성해야 한다. 예를 들어 향후 `copy`, `chmod` 같은 operation이 추가될 수 있으며, 새 operation 추가가 기존 `sync`, `rm`, `scan` lifecycle이나 Resource Management API 구현을 크게 변경하도록 만들면 안 된다.

별도 메타데이터 수정 API는 Data Management API의 초기 범위에서 제외한다. 다만 향후 `chmod` 같은 metadata-oriented data operation이 추가될 가능성은 열어둔다. DMS resource lifecycle desired state에 포함되는 permission, access control, quota, owner/group 정책 변경은 현재 Resource Management API 책임이다. `sync` 과정에서 파일 복제 semantics의 일부로 metadata가 보존되는 경우는 별도 메타데이터 수정 operation으로 보지 않는다.

Data Management API의 `rm`은 target directory 삭제 요청을 의미한다. Resource Management API의 `delete`는 DMS-managed resource lifecycle 삭제를 의미하므로 두 operation은 구분한다.

디렉토리 분석은 목적에 따라 구분한다. Resource lifecycle 검증에 필요한 usage, quota, permission, ownership, ACL, capability 조회는 Resource Management API의 검증 책임이다. 데이터 이동, 복제, 삭제, 정리 작업을 위한 심층 디렉토리 분석은 Data Management API의 `scan` 책임으로 다룬다.

### Data Management Request Model

`sync`, `rm`, `scan` 요청은 모두 job 기반으로 처리한다. API server는 요청을 접수하면 Data Management Job을 생성하고 즉시 `job_id`를 반환한다. `sync`와 `rm`은 하나의 `job_id` 안에서 preview phase, confirm phase, execution phase를 순차적으로 진행한다. Preview와 실제 실행은 별도 `job_id`가 아니라 같은 Data Management Job의 phase/state로 추적한다. `scan`은 데이터를 변경하지 않으므로 preview 없이 preflight 후 바로 execution phase로 진행한다. 상태, 상세 로그, 결과 요약, artifact 위치는 별도 조회 API에서 확인한다.

사용자는 worker node 기준 absolute path를 직접 지정하지 않는다. Source, destination, target은 DMS에 등록된 storage 또는 resource ID와 그 내부 relative path로 지정한다. `sync`의 source는 file 또는 directory를 허용하고, `sync`의 destination은 directory만 허용한다. `rm`과 `scan`의 target은 directory만 허용한다. DMS는 Resource Management API가 관리하는 storage, mount, namespace, resource inventory를 기준으로 실제 worker node mount path를 해석한다. DMS는 등록된 storage root 또는 resource boundary 밖으로 escape하는 path traversal, unsafe symlink, bind mount, root outside path를 거부해야 한다.

Operation option은 operation별 allowlist로 검증한다. 사용자가 제공한 raw command-line option string을 그대로 mpifileutils에 전달하지 않는다. DMS는 허용된 option만 실행 도구 인자로 변환한다. 이 allowlist와 tool mapping은 operation registry에 포함되어야 하며, 새 operation을 추가할 때 독립적으로 확장 가능해야 한다. `sync --delete`처럼 destination 데이터를 삭제할 수 있는 option은 위험 option으로 분류하고 preview 결과와 confirm 요청에서 명시적으로 확인되어야 한다.

요청 priority는 `High`, `Mid`, `Low` 중 하나로 지정할 수 있으며 기본값은 `Mid`다. 초기 설계에서는 모든 요청자가 세 priority 중 하나를 지정할 수 있다. 이 값은 Volcano queue 또는 Volcano scheduling policy와 매핑한다.

### Preview, Preflight, and Authorization

`sync`와 `rm`은 실제 데이터 변경 전에 dry-run mode의 preview phase를 먼저 수행하고, 사용자가 preview 결과를 confirm해야 execution phase로 진행한다. Preview 결과에는 예상 변경 범위, overwrite 또는 delete 위험 여부, 선택된 내부 도구 후보, worker pool 후보, 주요 검증 결과가 포함되어야 한다. Preview 결과에는 configurable TTL이 있으며, 기본값은 24시간이다. TTL이 만료된 preview는 confirm할 수 없고 Data Management Job은 `PreviewExpired` 상태가 된다.

`scan`은 데이터를 변경하지 않으므로 preview phase와 confirm 없이 최초 요청을 바로 execution phase로 수행한다. 단, 요청 형식, option allowlist, path boundary, mount 상태, POSIX 권한 같은 preflight 검증은 동일하게 적용한다.

Preflight는 후보 DM Worker에서 짧은 job으로 수행한다. API server는 요청 형식, option allowlist, path boundary, policy 같은 control-plane 검증을 수행하고, mount 존재 여부와 filesystem 권한처럼 node-local 확인이 필요한 항목은 preflight job에서 검증한다.

Preflight 검증 항목:

- source, destination, target 존재 여부와 path type. `sync` source는 file 또는 directory여야 하고, `sync` destination과 `rm`/`scan` target은 directory여야 한다.
- 후보 DM Worker에서 필요한 filesystem mount가 실제로 보이는지
- `ls` 같은 lightweight filesystem health check와 응답 시간
- 요청자가 source를 읽고 destination 또는 target에 필요한 쓰기/삭제 작업을 수행할 수 있는지
- storage endpoint, transfer tool endpoint, credential-bound endpoint에 대한 data-operation network reachability
- job image, mpifileutils tool, credential, ServiceAccount, Secret 사용 가능 여부

Data Management API는 Resource Management API와 동일하게 DMS API의 External API Authentication 원칙을 따른다. 외부 client는 mTLS로 DMS API endpoint에 접속해야 하며, mTLS client certificate 검증과 token 기반 인증을 모두 통과해야 한다. mTLS client certificate 검증 또는 token 인증 정보가 없는 Data Management API 요청은 거부한다.

인증 통과는 authenticated request로 받아들인다는 의미이며, Data Management operation 수행 허가는 `actor`, operation, target storage/resource, dangerous option 여부를 기준으로 별도 authorization policy에서 판단한다. 인증은 성공했지만 authorization policy가 거부한 요청은 `AuthorizationFailed`로 기록하고 preflight, preview, execution을 시작하지 않는다.

DMS는 authenticated request payload의 `requester_id`를 Identity Mapping API로 등록 및 검증된 UID/GID/group mapping과 연결하고, 데이터 path 접근 허가는 POSIX filesystem 권한을 기준으로 판단한다. 중앙 identity system은 read-only로 조회하며, 가능하면 preflight와 실제 mpifileutils job은 같은 POSIX identity로 실행되어야 한다.

Preflight가 실패하면 Data Management Job은 생성 이력과 함께 `PreflightFailed` 같은 최종 실패 상태로 기록하고 실제 mpifileutils job은 시작하지 않는다.

### Tool Selection and Scheduling

DMS는 Data Management API 작업을 Volcano scheduler를 통해 job 형태로 실행한다. DMS의 Volcano scheduling에는 `High`, `Mid`, `Low` 세 가지 priority queue가 있으며 기본 queue는 `Mid`다.

DM Worker runtime은 Resource Management API가 관리하는 storage inventory, DMS Agent report, worker node health, mount topology, credential, tool capability, data-operation network reachability, node load/capacity를 기준으로 candidate DM Worker pool을 만들고 VolcanoJob을 생성한다. 실제 worker pod 배치는 Volcano가 이 pool 안에서 수행한다.

멀티노드 병렬 작업에서는 같은 job의 worker pod가 같은 Kubernetes node에 함께 배치될 수 없다. DMS는 Volcano 또는 Kubernetes scheduling rule을 사용해 node당 하나의 worker pod 원칙을 강제해야 한다. 초기 기본 실행 모델은 node당 worker pod 하나, pod당 MPI rank 하나다. Pod당 여러 MPI rank는 추후 runtime configuration으로 확장할 수 있다.

Node 수와 resource 요청은 DMS가 기본값을 결정한다. DMS는 전역 기본값, operation별 override, 요청별 제한적 override를 지원해야 한다. 요청자는 필요한 경우 max node 수를 지정할 수 있지만, DMS의 기본 max node 상한과 운영 정책 상한을 초과할 수 없다. CPU, memory, timeout, priority default, max node 수, MPI 관련 설정은 runtime configurable해야 한다.

`sync` 내부 도구 선택:

- Source와 destination을 모두 mount한 healthy DM Worker가 있으면 DMS는 해당 node들을 candidate pool로 만들고 `dsync`를 사용한다.
- `dsync` candidate pool에는 DMS 기본 max node 상한과 요청 max node 상한을 적용한다.
- Source와 destination을 동시에 mount할 수 있는 node가 없고, source-mounted node pool과 destination-mounted node pool을 따로 만들 수 있으면 DMS는 `nsync`를 사용한다.
- `nsync` 사용 시 DMS는 source role node pool과 destination role node pool을 각각 만들고 Volcano job에서 role을 나누어 배치한다.
- `dsync`와 `nsync` 모두 실행 가능한 candidate pool을 만들 수 없으면 요청은 preflight 또는 planning 단계에서 실패해야 한다.

`rm`은 target path가 mount된 healthy DM Worker들을 candidate pool로 만들고 `drm`을 사용한다. `scan`은 target path가 mount된 healthy DM Worker들을 candidate pool로 만들고 `dscan`을 사용한다. 두 operation 모두 DMS 기본 max node 상한과 요청 max node 상한을 적용한다.

Job detail 조회에는 DMS가 선택한 내부 도구, 선택 이유, worker pool summary, priority, preflight 결과 요약, confirm 여부가 포함되어야 한다. Preflight 실패 시에는 failed check item, failure reason, error message, worker node, diagnostic event id를 운영자가 조회할 수 있어야 한다.

Kubernetes PV/PVC 기반 데이터 작업은 초기 지원 대상이 아니라 향후 확장 대상으로 둔다. 확장 시에는 host filesystem mount를 pod에 붙여 병렬 I/O를 수행하듯이, PV/PVC를 Data Management job pod에 attach 또는 mount하여 `sync`, `rm`, `scan`에 동일한 preflight, scheduling, failure 원칙을 적용한다.

### Failure, Timeout, and Result Handling

DM Worker runtime controller의 lease 만료, restart, fail-over는 회복 가능해야 한다. Fail-over된 DM Worker runtime은 운영용 PostgreSQL과 Kubernetes API에서 기존 Data Management Job 및 VolcanoJob 상태를 재조회하고 lifecycle state를 이어서 기록한다.

이미 생성된 Volcano worker pod가 `Failed` 또는 `Evicted` 상태가 되면 해당 Data Management Job은 즉시 실패로 종료한다. 이 경우 worker pod 자동 재시도는 수행하지 않는다. DM Worker runtime fail-over가 발생하더라도 실패한 Volcano worker pod를 재시작하지 않으며, 재실행은 사용자가 새 Data Management request/job으로 수행한다.

Volcano job이 장시간 pending, running, 또는 hang 상태에 머무를 수 있으므로 operation별 configurable warning threshold와 timeout을 둔다. Warning threshold를 넘으면 운영자에게 알릴 수 있는 diagnostic event를 남기고, timeout을 넘으면 DMS가 Volcano job을 terminate하고 Data Management Job을 `TimedOut` 또는 실패 상태로 기록한다.

Data Management Job 상태는 최소한 다음 상태를 구분할 수 있어야 한다.

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

`sync`와 `rm`은 일반적으로 `Pending -> PreflightRunning -> PreviewRunning -> PreviewSucceeded -> ConfirmPending -> Confirmed -> Scheduled -> Running -> Succeeded/Failed` 흐름을 따른다. 이 흐름은 Data Management Job 전용 lifecycle이다. 공통 request/plan/run row는 preview confirm 대기 중 `Blocked`로 남고, `ConfirmPending` 문자열을 공통 lifecycle state로 사용하지 않는다. `scan`은 preview와 confirm 없이 `Pending -> PreflightRunning -> Scheduled -> Running -> Succeeded/Failed` 흐름을 따른다. Authorization 실패, preflight 실패, 취소, timeout은 각 단계에서 terminal state로 전환될 수 있다.

DMS는 운영용 PostgreSQL에 job lifecycle state, 상태 요약, requester identity, operation, source/destination/target resource, relative path, option summary, priority, worker pool summary, preflight 결과, selected tool, confirm 여부, final status, log URI, result URI를 저장해야 한다. 상세 stdout/stderr, mpifileutils report, scan report 같은 큰 결과는 파일 또는 artifact 저장소에 저장하고 운영용 PostgreSQL에는 URI와 요약만 저장한다.

`scan` 완료 후 운영용 PostgreSQL에는 파일 수, 디렉토리 수, 총 용량, 오류 수, scan report URI 같은 요약을 저장한다. 전체 scan report와 상세 file list는 artifact로 저장한다.

감사 로그에는 requester identity, operation, resource/path, option summary, priority, worker pool summary, preflight 결과, confirm 여부, final status가 남아야 한다. 특히 `rm`, `sync --delete`, path boundary 위반, 권한 실패, timeout, cancel, failed job은 원인 추적이 가능하도록 diagnostic observability event를 남겨야 한다.

## Target Operating Context

### DMS API Ingress

DMS API는 Kubernetes Ingress를 통해 외부 client에 노출된다.

초기 셋업 시 필요한 DMS API Ingress 관련 정보:

- external API hostname
- Ingress class 또는 Ingress controller 정보
- TLS server certificate 및 private key secret 정보
- mTLS client certificate 검증에 사용할 CA certificate 또는 CA bundle
- token 인증 설정에 필요한 secret 또는 issuer 정보
- 허용할 외부 network 또는 ingress policy 정보

운영자는 이 정보를 사용해 외부 client가 mTLS와 token을 통해 DMS API를 호출할 수 있도록 구성한다.

Ingress 또는 edge proxy는 API upstream으로 client certificate subject와 verification result를 전달하고, API server는 `DMS_REQUIRE_MTLS_HEADER=true` 및 `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true` 설정에서 이를 요구한다. DMS edge proxy는 `X-DMS-Client-Cert-Subject`와 `X-DMS-Client-Cert-Verify: SUCCESS`를 전달하고, ingress-nginx는 `auth-tls-*` annotation으로 `ssl-client-subject-dn`과 `ssl-client-verify: SUCCESS`를 전달한다. API Service 직접 접근은 header spoof를 막기 위해 NetworkPolicy 또는 동등한 네트워크 제어로 차단해야 한다. 현재 테스트베드 검증은 ingress-nginx namespace/IngressClass가 없는 환경에서 short-lived mTLS edge proxy와 NetworkPolicy를 임시 배포해 client certificate handshake, trusted evidence injection, direct spoof 차단을 확인했다. `install/kubernetes/ingress.example.yaml`은 ingress-nginx 운영 예시이며, production ingress-nginx controller의 live termination 검증은 별도 staging 검증 항목이다.

### PostgreSQL

DMS는 Kubernetes 위에서 동작하는 운영 시스템으로 설계한다.

운영용 PostgreSQL과 observability/log용 PostgreSQL은 DMS 설치 전에 DMS Kubernetes cluster 내부에 HA 구성으로 준비되어 있어야 한다. DMS 설치 절차는 PostgreSQL을 새로 배포하는 절차가 아니라, 사전 준비된 in-cluster PostgreSQL을 DMS가 사용할 수 있도록 접속 정보, 권한, Kubernetes Secret, migration 정책, 검증 절차를 정리하고 적용하는 절차다.

초기 셋업 과정에서는 운영자가 어떤 정보를 어디에 기록하고, 어떤 파일을 수정하며, 어떤 명령 또는 스크립트로 검증해야 하는지 명확해야 한다. 비밀번호, private key, token 같은 secret 값은 문서에 평문으로 기록하지 않고 Kubernetes Secret 또는 외부 secret management system에 저장하며, 문서와 config에는 Secret 이름과 key만 기록한다.

초기 셋업 시 필요한 운영용 PostgreSQL 관련 정보:

- Kubernetes namespace
- Service DNS name 또는 Service name과 port
- HA 구성 방식과 primary/readiness 확인 방법
- database name
- schema name 또는 search path 정책
- application username 또는 service account 정보
- migration username과 migration 실행 권한 여부
- password, token, certificate 등 인증 정보를 담은 Kubernetes Secret 이름과 key
- TLS 사용 여부 및 필요한 CA/cert/key 정보
- connection pool 관련 기본값 또는 제한값
- PVC, StorageClass, backup, restore, disaster recovery 정책
- NetworkPolicy 또는 DMS component 접근 허용 조건

초기 셋업 시 필요한 observability/log용 PostgreSQL 관련 정보:

- Kubernetes namespace
- Service DNS name 또는 Service name과 port
- HA 구성 방식과 readiness 확인 방법
- database name
- schema name 또는 search path 정책
- application username 또는 service account 정보
- migration username과 migration 실행 권한 여부
- password, token, certificate 등 인증 정보를 담은 Kubernetes Secret 이름과 key
- TLS 사용 여부 및 필요한 CA/cert/key 정보
- connection pool 관련 기본값 또는 제한값
- diagnostic observability event retention 기본 정책
- 외부 log/monitoring system export 사용 여부 및 endpoint 정보
- PVC, StorageClass, backup, restore, disaster recovery 정책
- NetworkPolicy 또는 DMS component 접근 허용 조건

운영용 PostgreSQL과 observability/log용 PostgreSQL은 서로 다른 connection pool, timeout, migration, retention, backup 정책을 가질 수 있어야 한다.

PostgreSQL HA, backup, restore, PITR 시스템은 운영자가 제공한다. DMS 구현은 해당 시스템을 직접 소유하지 않지만, 설치와 운영 가이드에서 backup schedule, restore target, PITR 가능 여부, 최근 backup 검증 시각, planned shutdown 전 확인 명령, startup 후 복구 검증 명령을 기록하고 확인할 수 있게 해야 한다.

이 정보는 운영자가 설치 시 쉽게 준비하고 검증할 수 있어야 하며, 환경별 실제 값은 `testbed/` 디렉터리의 모든 관련 테스트베드 구성 문서와 설치 values/config 파일에 나누어 기록한다. `testbed/`에는 PostgreSQL의 배포 위치, namespace, Service, database, user, Secret reference, PVC, 검증 명령을 포함해 테스트베드에 설치된 시스템과 검증 환경의 실제 상태를 기록하고, 실제 DMS 설치 values/config에는 DMS component가 사용할 Service DNS, database/schema, Secret reference, TLS, pool, migration 설정을 기록한다. 구현 중 테스트베드에 시스템을 추가하거나 구성이 바뀌면 `testbed/` 아래에 필요한 문서를 추가하거나 기존 문서를 갱신한다.

### Multi-Cluster Kubernetes Environment

운영 환경에는 독립적인 여러 managed Kubernetes cluster가 있을 수 있으며, 각 cluster별로 storage가 존재할 수 있다. DMS control plane은 별도의 DMS Kubernetes cluster에서 동작하고, 각 managed cluster에는 dedicated RM Worker가 배포된다.

DMS Kubernetes cluster와 각 managed cluster의 DMS worker node 사이의 control-plane communication path는 네트워크적으로 서로 통신 가능하다고 가정한다. Firewall, NAT, routing 제약은 기본 설계 제약으로 두지 않으며, control plane에서 worker node로 명령을 전달할지 worker node가 control plane으로 연결해 작업을 가져갈지는 내부 interface 설계에서 결정한다. 이 전제는 Data Management API scheduling에서 사용하는 data-operation network reachability와 구분한다. 단, DMS는 관리 안정성을 위해 hostname, namespace, mount, CSI driver, StorageClass 등에 대한 sanity check를 구현해야 한다.

현재 운영 중이거나 DMS가 고려해야 하는 storage solution 예시:

- IBM GPFS
- DDN Lustre
- Weka
- Pure Storage
- VAST
- XFS
- NFS
- Longhorn
- Ceph

이 목록은 고정된 whitelist가 아니다. 운영 환경에는 새로운 proprietary 또는 open source storage solution이 추가될 수 있으며, DMS는 storage backend 추가를 전제로 설계해야 한다.

추후 proprietary filesystem과 다른 open source filesystem으로 쉽게 확장할 수 있도록 storage backend 정의, filesystem별 quota 적용 방식, Kubernetes StorageClass 매핑은 template 및 확장 가능한 디자인 패턴을 기반으로 구현한다.

Storage backend 확장 원칙:

- 새로운 storage solution 추가가 DMS core lifecycle 변경을 요구하지 않아야 한다.
- Storage backend별 설정은 template 형태로 정의할 수 있어야 한다.
- Template은 backend type, mount path, quota capability, quota unit 변환, CSI driver, StorageClass 매핑, sanity check 항목을 표현할 수 있어야 한다.
- Filesystem별 quota 적용은 adapter 또는 strategy 형태로 확장 가능해야 한다.
- 특정 backend에서 지원하지 않는 기능은 명시적으로 capability가 없다고 표현하고, Planner가 이를 기준으로 요청을 reject하거나 다른 plan을 수립해야 한다.

### Example Cluster Topology

다음은 DMS가 고려해야 하는 multi-cluster storage 운영 환경 예시다. A100 cluster와 H100 cluster는 설명을 위한 예시 이름이며, 실제 DMS가 적용될 인프라의 cluster 이름, cluster 개수, storage 조합은 운영 환경마다 달라질 수 있다.

이 예시에서 A100 cluster와 H100 cluster의 WEKA mount는 서로 다른 WEKA backend storage system을 사용하는 것으로 표현한다. 예를 들어 A100 cluster는 2021년에 도입한 WEKA storage system을 사용하고, H100 cluster는 2024년에 도입한 별도 WEKA storage system을 사용할 수 있다.
CephFS는 예시의 A100 cluster와 H100 cluster가 동일한 Ceph backend storage system을 공유하는 것으로 표현한다.

```text
Rancher Management Cluster
├─ DMS Kubernetes Cluster
│  ├─ Kubernetes Control Plane
│  ├─ DMS Control Plane Workloads
│  │  ├─ DMS API / Frontend
│  │  ├─ Planner
│  │  └─ Control Plane Services
│  ├─ DM Worker
│  │  ├─ dm-worker-a100-weka
│  │  │  ├─ Mounted Filesystems: A100 WEKA /weka-prod, /weka-shared
│  │  │  ├─ DM Worker runtime
│  │  │  └─ DMS Agent
│  │  ├─ dm-worker-h100-weka
│  │  │  ├─ Mounted Filesystems: H100 WEKA /weka-prod
│  │  │  ├─ DM Worker runtime
│  │  │  └─ DMS Agent
│  │  ├─ dm-worker-weka-transfer
│  │  │  ├─ Mounted Filesystems: A100 WEKA /weka-prod, H100 WEKA /weka-prod
│  │  │  ├─ DM Worker runtime
│  │  │  └─ DMS Agent
│  │  ├─ dm-worker-shared-ceph
│  │  │  ├─ Mounted Filesystems: shared CephFS /cephfs-prod, /cephfs-shared
│  │  │  ├─ DM Worker runtime
│  │  │  └─ DMS Agent
│  │  └─ dm-worker-ceph-gpfs
│  │     ├─ Mounted Filesystems: shared CephFS /cephfs-prod, GPFS /gpfs-dataset
│  │     ├─ DM Worker runtime
│  │     └─ DMS Agent
│  │
│  └─ DMS Internal Services
│
├─ A100 GPU Kubernetes Cluster
│  ├─ Kubernetes Control Plane
│  ├─ GPU Workload Nodes
│  │  ├─ Mounted Filesystems
│  │  │  ├─ WEKA: /weka-prod
│  │  │  ├─ WEKA: /weka-shared
│  │  │  └─ CephFS: /cephfs-prod
│  │  └─ GPU Workloads
│  ├─ RM Worker
│  │  ├─ Dedicated Worker Node Pool
│  │  ├─ Mounted Filesystems
│  │  │  ├─ WEKA: /weka-prod
│  │  │  ├─ WEKA: /weka-shared
│  │  │  └─ CephFS: /cephfs-prod
│  │  ├─ RM Worker runtime
│  │  └─ DMS Agent
│  ├─ CSI Drivers
│  │  ├─ WEKA CSI
│  │  └─ CephFS CSI
│  ├─ StorageClasses
│  │  ├─ weka-a100-rwx
│  │  └─ cephfs-a100-rwx
│  └─ Namespaces
│     ├─ team-a
│     └─ team-b
│
├─ H100 GPU Kubernetes Cluster
│  ├─ Kubernetes Control Plane
│  ├─ GPU Workload Nodes
│  │  ├─ Mounted Filesystems
│  │  │  ├─ WEKA: /weka-prod
│  │  │  ├─ CephFS: /cephfs-prod
│  │  │  └─ GPFS: /gpfs-dataset
│  │  └─ GPU Workloads
│  ├─ RM Worker
│  │  ├─ Dedicated Worker Node Pool
│  │  ├─ Mounted Filesystems
│  │  │  ├─ WEKA: /weka-prod
│  │  │  ├─ CephFS: /cephfs-prod
│  │  │  └─ GPFS: /gpfs-dataset
│  │  ├─ RM Worker runtime
│  │  └─ DMS Agent
│  ├─ CSI Drivers
│  │  ├─ WEKA CSI
│  │  ├─ CephFS CSI
│  │  └─ GPFS CSI
│  ├─ StorageClasses
│  │  ├─ weka-h100-rwx
│  │  ├─ cephfs-h100-rwx
│  │  └─ gpfs-h100-rwx
│  └─ Namespaces
│     ├─ team-a
│     └─ team-c
│
└─ Backend Storage Systems
   ├─ WEKA Storage System for A100
   │  └─ Filesystems
   │     ├─ /weka-prod
   │     └─ /weka-shared
   │
   ├─ WEKA Storage System for H100
   │  └─ Filesystems
   │     └─ /weka-prod
   │
   ├─ Ceph Storage System
   │  └─ Filesystems
   │     ├─ cephfs-prod
   │     └─ cephfs-shared
   │
   └─ GPFS Storage System
      └─ Filesystems
         └─ /gpfs-dataset
```

Multi-cluster 운영 가정:

- 모든 node의 hostname은 unique하다.
- Kubernetes namespace는 cluster 내부에서 unique하다.
- 서로 다른 cluster에 같은 namespace 이름이 존재할 수 있다.
- DMS의 Kubernetes namespace 최종 identity와 uniqueness 기준은 `cluster_name + namespace_name` 조합이다.
- `namespace_name` 단독 global uniqueness는 요구하지 않는다. 서로 다른 cluster에 같은 namespace 이름이 존재하는 것은 정상적인 multi-cluster 운영 상황이다.
- DMS API server, Frontend, Planner, control plane component는 DMS Kubernetes cluster에 배포된다.
- RM Worker는 각 managed Kubernetes cluster에 배포되는 dedicated worker node이며 GPU workload node와 구분된다.
- RM Worker runtime과 DMS Agent는 Kubernetes workload로 RM Worker에 scheduling된다.
- Filesystem resource의 directory 생성, quota 적용, permission/access control 변경, block, import 검증은 dedicated RM Worker에 mount된 filesystem에서 수행한다. 공유 filesystem이므로 이 node에서 적용한 filesystem metadata와 quota 상태는 GPU workload node에서도 동일하게 관측된다고 가정한다.
- DMS Agent는 일반 GPU workload node 전체에 기본 배포하지 않는다. 일반 workload node의 mount/CSI 상태 검증은 기본 Resource Management API 실행 조건이 아니며, 필요 시 별도 inventory 또는 health-check operation으로 확장한다.
- Resource Management API의 기본 sanity check는 Kubernetes API와 dedicated RM Worker에서 확인 가능한 mount, CSI driver, quota capability를 기준으로 한다.
- DM Worker는 각 managed Kubernetes cluster에 배포되지 않고 DMS Kubernetes cluster에 Kubernetes node로 구성된다.
- DM Worker runtime과 DMS Agent는 Kubernetes workload로 DM Worker에 scheduling된다.
- DM Worker에는 데이터 작업 대상 storage가 filesystem으로 mount되어 있어야 한다. Node별 mount 구성은 다를 수 있고, 운영 환경에 따라 하나의 node가 여러 storage 또는 모든 storage를 mount할 수도 있다.
- 예시의 A100/H100 managed cluster storage mount와 DMS Kubernetes cluster의 DM Worker mount는 같은 backend storage를 각 cluster/node에서 별도로 mount한 것이다.
- Data Management API job scheduling은 DM Worker별 source storage mount, target storage mount, data-operation network reachability, credential, tool capability, node load/capacity를 고려해야 한다.

## Resource Models

### Filesystem Resource

Filesystem resource의 기본 모델은 다음과 같다.

- storage root 아래 사용자 directory를 생성한다.
- Directory access control은 Linux group을 기준으로 관리한다.
- Directory를 사용할 사용자 리스트는 resource 요청에 포함된다.
- DMS는 요청에 포함된 사용자 리스트와 DMS identity mapping을 기준으로 기존 Linux group membership을 조회하고, directory group ownership과 permission을 계획하고 적용한다.
- 운영 환경은 중앙 identity/group system을 사용한다고 가정한다.
- 실제 운영 Kubernetes cluster에서는 SSSD/LDAP을 중앙 identity/group system으로 사용할 예정이다.
- DMS는 중앙 identity/group system을 read-only로 조회하며, directory access control에 필요한 Linux group membership을 중앙 identity/group system에 생성하거나 수정하지 않는다.
- DMS는 사용자 계정 또는 조직 identity의 원천 시스템은 소유하지 않는다. 사용자 계정 자체는 LDAP, AD, IAM, 또는 운영 환경의 기존 identity system에 존재한다고 가정한다.
- Directory ownership과 permission은 filesystem metadata이므로 공유 filesystem에서는 mount된 node들에서 동일하게 관측되어야 한다.
- Linux group membership은 filesystem metadata가 아니라 identity system 상태다. DMS는 중앙 identity/group system을 read-only로 조회하고 target node에서 `getent`, `id` 등으로 조회 일관성을 검증해야 한다.
- 요청된 사용자가 identity system에 존재하지 않거나 target node에서 일관되게 조회되지 않으면 Planner는 요청을 실패 처리하거나 정책에 따라 보류해야 한다.
- filesystem 특유의 directory quota 기능을 통해 quota를 적용한다.

Filesystem별 quota mechanism 예:

- GPFS: fileset
- Lustre: project quota
- XFS: group quota의 block quota와 inode/file-count quota
- Ceph: directory 또는 sub-tree quota

Filesystem마다 quota 적용 방식이 다르므로, DMS는 filesystem별 adapter 또는 strategy 형태의 확장 가능한 디자인 패턴을 사용해야 한다. Adapter는 storage backend template에 정의된 capability와 연결되어야 하며, 새로운 filesystem 추가가 DMS core lifecycle 변경으로 이어지지 않아야 한다.

Filesystem quota는 두 종류가 있으며, 사용자 요청으로 주어진다.

- Capacity quota: 일반적으로 hard quota를 적용하여 사용량이 quota를 초과하면 write가 불가능하게 한다. 사용자 입력 단위는 TB 기준이며 소수점 입력을 허용한다.
- Count quota: 파일 개수 quota다. 사용자 입력 단위는 Million 기준이며 소수점 입력을 허용한다.

사용자 입력에서 `TB`는 decimal terabyte로 해석한다. 즉, `1TB = 10^12 bytes`다.

Filesystem default quota policy:

- `type=user`: capacity quota `1TB`, count quota `5M`
- `type=project`: capacity quota `1TB`, count quota `5M`
- `type=system`: quota 제한 없음. 운영 DB default policy에는 `{unlimited:true}` sentinel로 저장한다.
- `type=admin`: quota 제한 없음. 운영 DB default policy에는 `{unlimited:true}` sentinel로 저장한다.

Filesystem file count quota 기본값은 capacity quota `1TB`당 `5M` files로 해석한다. `5M` count quota는 5,000,000 file count로 해석한다. 운영용 PostgreSQL 내부에서는 capacity quota는 byte 단위 정수로, count quota는 정수 file count로 저장한다. Kubernetes PVC count quota는 file count quota가 아니므로 이 filesystem file count quota 원칙의 적용 대상이 아니다.

이 기본 quota policy는 명시 quota와 기존 적용 quota가 모두 없는 요청의 plan 생성 기준이며, resource initialize 또는 `reset_quota_to_default=true` update의 quota reset 기준이다. 기본 quota policy 자체를 변경하는 작업은 update의 별도 동작으로 처리한다.

Filesystem `type=system`, `type=admin`은 quota 제한 없음이 의도된 운영 정책이다. 기존 적용 quota가 없는 상태에서 명시 quota도 없으면 Planner는 `{unlimited:true}` default policy를 사용해 non-enforcing quota state를 만든다. 이 경우 quota field가 비어 있는 것은 입력 누락이 아니라 unlimited sentinel의 결과다. 해당 type에 bounded quota를 적용해야 하는 site는 요청에 명시 quota를 넣거나 default policy를 concrete quota 값으로 바꾼 뒤 별도 update/initialize flow를 수행한다.

Filesystem resource create 요청은 다음 정보를 포함한다.

- `directory_name`: 최대 32글자. 추후 filesystem directory 이름으로 사용되므로 format 제약이 필요하다. 같은 `storage_name` 안에서 unique해야 한다.
- `storage_name`: DMS 내부 논리 storage ID. 예를 들어 `weka-A100-prod`는 A100 cluster에 `/weka-prod`로 mount되어 있는 WEKA filesystem을 의미할 수 있다. DMS 전체에서 unique해야 한다.
- `request_id`: 최대 128글자. Tracking, correlation, audit을 위해 필요하며 DMS 전체에서 unique해야 한다. 앞부분만 봐도 요청을 식별할 수 있어야 하며, 날짜/시간 또는 짧은 random suffix를 포함해 전역 unique하게 만든다.
- `requester_id`: 요청자 ID. 모든 Resource Management API request에 포함되어야 하며, DMS는 payload에 포함된 값을 requester identity로 신뢰한다.
- `requested_at`: 리소스 생성 요청을 받은 timestamp.
- `expires_at`: 리소스 만료 timestamp.
- `type`: `user`, `project`, `admin`, `system`. type별 기본 quota 값을 다르게 적용할 수 있다.
- `capacity_quota`: optional capacity quota. 사용자 입력 단위는 TB 기준이며 소수점 입력을 허용한다. 생략 시 quota 입력 해석 원칙을 따른다.
- `count_quota`: optional file-count quota. 사용자 입력 단위는 Million 기준이며 소수점 입력을 허용한다. 생략 시 quota 입력 해석 원칙을 따른다.
- `users`: 해당 directory를 사용할 사용자 리스트. DMS는 이 리스트를 Linux group 기반 access control의 기준으로 사용한다.
- `memo`: 운영자 메모.

구현 중 필요한 필드는 추가할 수 있다.

`storage_name + directory_name` 조합으로 이 resource가 어떤 storage의 어느 directory를 의미하는지 식별할 수 있어야 한다. 운영용 PostgreSQL도 이 key 조합에 맞게 unique constraint를 둔다.

운영용 PostgreSQL에는 각 resource에 대한 `memo` column을 추가한다. 이 column에는 운영자 메모가 저장될 예정이다.

가능한 filesystem은 XFS, GPFS, Lustre, WekaFS, PureFS, NFS 등등 기타 filesystem이다. 이 목록은 확장 가능해야 하며, 실제 구현과 검증 대상 filesystem은 storage backend capability와 운영 우선순위에 따라 단계적으로 확정한다.

Filesystem resource update로 변경 가능한 항목:

- quota
- type별 기본 quota policy 기준 quota reset (`reset_quota_to_default=true`)
- `directory_name`
- 사용자 리스트
- `expires_at`
- `memo`
- `type`

Filesystem resource update는 quota 감소 요청에 `force`를 요구하지 않는다. Filesystem quota 감소는 사용량 admission 없이 backend adapter 적용과 read-back 검증으로 처리한다.

Filesystem resource update 시 검증해야 하는 항목:

- 대상 `storage_name`이 운영용 PostgreSQL에 존재하는지 확인한다.
- 대상 `storage_name + directory_name` resource가 운영용 PostgreSQL에 존재하는지 확인한다.
- 대상 storage root와 directory가 실제 filesystem에 존재하는지 확인한다.
- directory 이름 변경 요청일 경우 새 `directory_name` format과 uniqueness를 검증한다.
- 사용자 리스트 변경 요청일 경우 Linux group membership, directory group ownership, permission 변경 plan을 검증한다.
- `reset_quota_to_default=true` 요청일 경우 대상 resource type에 해당하는 기본 quota policy가 존재하는지 확인한다.
- quota 변경 요청일 경우 현재 운영용 PostgreSQL의 DMS desired quota와 비교해 증가 요청인지 감소 요청인지 확인한다. 이 admission check는 filesystem 사용량을 조회하지 않는다.
- 기존 finite quota보다 큰 finite quota, 또는 finite quota를 unlimited로 푸는 요청은 `force` 없이 backend adapter 실행을 시도한다. Backend adapter 실패는 일반 apply failure로 기록될 수 있다.
- 기존 finite quota보다 작은 finite quota, 또는 기존 unlimited quota를 finite quota로 바꾸는 요청도 사용량 admission이나 `force=true` 없이 backend adapter 실행을 시도한다.
- 실제 filesystem quota command가 실패할 수 있으며, 실패 시 request/result에 backend 오류를 기록한다.
- 만료 시간 변경이 정책상 허용되는지 확인한다.
- update 후에는 filesystem별 quota와 permission 상태를 다시 조회하여 운영용 PostgreSQL의 observed state를 갱신한다.

Filesystem resource block 동작:

- `block=ON`이면 directory 접근을 차단하거나 제한한다.
- `type=system`, `type=admin` filesystem resource에 대한 `block=ON` 요청은 실패해야 한다.
- `block=OFF`이면 block 이전에 보존한 directory permission mode로 복구한다. Block mode 자체는 ownership이나 group membership을 변경하지 않는다.
- Filesystem block mode는 요청 또는 정책으로 선택할 수 있어야 한다.
- `readonly` mode는 directory permission을 read-only로 변경한다.
- `root-owned` mode는 운영상 ownership 변경을 수행하지 않고 directory permission을 `000`으로 변경하여 일반 사용자가 사용할 수 없게 한다. 이름은 운영자-facing 차단 모드 명칭으로 유지한다.
- `readonly`에서 `root-owned`로, 또는 `root-owned`에서 `readonly`로 block mode를 변경할 수 있어야 한다.
- Block mode 변경도 lifecycle을 따르며, 적용 후 permission mode와 observed ownership/group 상태를 검증 evidence로 기록해야 한다.

Existing directory quota assignment:

- DMS가 생성하지 않았고 운영용 PostgreSQL에 resource로 등록되어 있지 않은 기존 directory에도 quota를 설정할 수 있어야 한다.
- 이 요청은 `storage_name`과 `directory_name`을 기준으로 대상을 식별한다.
- `storage_name`이 storage root를 결정하므로, 대상 directory의 실제 위치는 해당 storage root 바로 아래의 `directory_name`이다.
- 대상 directory는 반드시 해당 `storage_name`의 storage root 바로 아래에 있어야 한다.
- `directory_name`은 basename이어야 하며, nested path 또는 relative path를 표현할 수 없다.
- DMS는 `/`, `..`, path separator, path traversal, symlink escape, storage root 외부 path 지정 같은 위험한 입력을 거부해야 한다.
- 대상 directory가 이미 DMS-managed filesystem resource이면 일반 update flow를 사용해야 한다.
- 대상 directory가 DMS-managed resource가 아니면 quota-only managed resource로 운영용 PostgreSQL에 기록한다.
- quota-only managed resource는 directory 생성, 삭제, 사용자 리스트 기반 access control을 기본적으로 소유하지 않는다.
- quota-only managed resource에서 DMS가 소유하는 범위는 quota desired state, quota observed state, memo, request/result tracking이다.
- quota 적용 전 대상 storage backend가 directory quota capability를 제공하는지 확인해야 한다.
- quota 적용 후 filesystem별 quota 상태를 다시 조회하여 운영용 PostgreSQL의 observed state를 갱신한다.

Import existing filesystem directory:

- DMS가 생성하지 않았지만 이미 존재하는 filesystem directory를 명시적 import operation으로 full DMS-managed filesystem resource로 전환할 수 있어야 한다.
- Import 대상은 `storage_name + directory_name`으로 식별한다.
- Import 대상 directory는 해당 `storage_name`의 storage root 바로 아래에 실제로 존재해야 하며, `directory_name`은 basename이어야 한다.
- Import는 quota-only assignment와 달리 성공 후 DMS가 directory lifecycle과 access control ownership을 갖는 전환이다.
- Import 성공 후 해당 resource는 일반 filesystem resource update, block, initialize, expiration 처리 대상이 된다.
- Delete는 dummy operation으로 처리하지 않는다. Import된 resource도 일반 filesystem resource와 동일하게 명시 확인 후 실제 directory delete 대상이 될 수 있다.
- Import 시 DMS는 filesystem에서 현재 directory 상태를 조회하고 이를 초기 state로 기록해야 한다.
- 조회해야 하는 상태에는 directory owner, group, permission, ACL 여부, Linux group membership 해석 결과, capacity quota, count quota, filesystem type, backend quota capability가 포함된다.
- 사용자 리스트 또는 DMS-managed group 정책이 요청에 명시되지 않은 경우, DMS는 기존 directory group과 중앙 identity/group system을 통해 사용자 리스트를 해석해야 한다.
- 기존 access control을 해석할 수 없거나 DMS가 이후 lifecycle을 안정적으로 소유할 수 없는 상태이면 import는 실패해야 한다.
- Import는 기존 permission, ownership, group membership, quota를 기본적으로 보존한다. Import와 동시에 변경이 필요한 경우에는 import 이후 별도 update로 처리하거나, 구현 단계에서 명시적 import option으로만 허용한다.
- Import는 final verification을 반드시 수행해야 한다. DMS는 import 완료 전후로 filesystem state를 다시 조회하고, import 기준 상태와 실제 상태가 정책상 허용되지 않게 달라졌으면 import를 success로 처리하지 않는다.
- Import는 가능하면 maintenance window 또는 외부 변경이 제한된 상태에서 수행한다. Final verification 실패는 conflict, retryable failure, 또는 failed 상태로 기록해야 한다.
- 대상 directory가 이미 quota-only managed resource이면 import는 quota-only 상태를 full DMS-managed 상태로 승격하는 전환으로 처리할 수 있다.
- 대상 directory가 이미 full DMS-managed resource이면 import는 conflict 또는 no-op success로 처리해야 한다.
- Import 전 unmanaged 또는 quota-only 상태, import 후 DMS-managed 상태, import 시점에 관찰한 filesystem state, requester id, request id, 검증 결과는 운영용 PostgreSQL에 기록해야 한다.
- Import 실행 주체는 `worker_id` 또는 `executor_id`로 추적해야 하며, diagnostic observability event에서는 이 실행 주체를 actor로 기록할 수 있다.

### Kubernetes Namespace Storage Quota Resource

Kubernetes resource model은 namespace 단위 storage quota 관리를 기본으로 한다.

DMS는 각 Kubernetes cluster에 대해 namespace, StorageClass, CSI driver, PVC, ResourceQuota 정보를 수집하고, 사용자의 요청에 따라 namespace storage quota를 생성, 수정, 차단, 조회한다.

Kubernetes namespace storage quota resource의 DMS identity와 최종 uniqueness 기준은 `cluster_name + namespace_name`이다.

Kubernetes namespace storage quota 요청에서 `storage_class_quotas[].storage_name`은 StorageClass별 quota entry를 지정할 때 사용하는 primary storage input이다.

DMS는 StorageClass별 quota entry가 요청된 경우 운영용 PostgreSQL에 저장된 `storage_name` mapping에서 `cluster_name`, `storage_class_name`, backend type, CSI driver, access mode를 조회한다.

`storage_class_quotas[].storage_class_name`은 사용자가 기본적으로 직접 입력하는 값이 아니라 `storage_class_quotas[].storage_name` mapping에서 derive되는 값이다. 구현 단계에서 디버깅 또는 호환성 목적으로 요청 payload에 `storage_class_quotas[].storage_class_name`을 허용하더라도, Planner는 payload 값이 mapping에서 derive한 값과 일치하는지 검증해야 하며 불일치하면 요청을 실패 처리해야 한다.

`storage_class_quotas[].storage_name`과 derived `storage_class_name`은 Kubernetes namespace storage quota resource의 identity가 아니라, 해당 namespace quota resource 안에 포함되는 quota dimension 또는 quota entry로 취급한다.

같은 `cluster_name + namespace_name` resource가 이미 존재하는 상태에서 create 요청이 다시 들어오면 Planner는 conflict로 처리해야 한다. 기존 namespace resource에 StorageClass별 quota entry를 추가하거나 변경하는 작업은 update 요청으로만 허용한다.

DMS가 Kubernetes에서 직접 관리하는 quota 대상:

- namespace 단위 전체 PVC 요청 용량 quota
- namespace 단위 PVC 개수 quota
- StorageClass별 PVC 요청 용량 quota
- StorageClass별 PVC 개수 quota

Kubernetes namespace storage quota는 실제 filesystem의 byte-level 사용량을 직접 제한하지 않는다. 이 quota는 Kubernetes API level에서 PVC 생성 및 PVC 확장 요청을 제한하는 기능이다.

따라서 실제 filesystem 사용량, directory quota, inode/file-count quota는 filesystem resource model에서 별도로 관리한다.

예를 들어 CephFS CSI, WEKA CSI, GPFS CSI, Longhorn CSI 등을 통해 PVC를 생성하는 경우, DMS는 해당 namespace에 ResourceQuota를 설정하여 PVC 요청 용량과 PVC 개수를 제한한다.

반면, 일반 Kubernetes workload node에 직접 mount된 `/weka-prod`, `/cephfs-prod`, `/gpfs-dataset` 등의 경로를 pod에 hostPath, local PV, bind mount 등으로 제공하는 경우에는 Kubernetes namespace quota만으로 실제 사용량을 제한할 수 없으므로 filesystem resource model을 사용한다.

Kubernetes resource create 요청은 다음 정보를 포함한다.

- `namespace_name`: Kubernetes namespace 이름. RFC 1123 DNS label 형식을 따른다. 최대 63자이며, lowercase alphanumeric 문자와 `-`만 허용한다. namespace 이름은 cluster 내부에서 unique하며, DMS에서는 `cluster_name + namespace_name` 조합으로 식별한다. `namespace_name` 단독 global uniqueness는 요구하지 않는다.
- `cluster_name`: DMS 내부 논리 Kubernetes cluster ID. 예: `a100-gpu-prod`, `h100-gpu-prod`, `research-gpu-prod`.
- `request_id`: 최대 128글자. DMS 전체에서 unique해야 하며 tracking, correlation, audit을 위해 사용한다.
- `requester_id`: 요청자 ID. 모든 Resource Management API request에 포함되어야 하며, DMS는 payload에 포함된 값을 requester identity로 신뢰한다.
- `requested_at`: 리소스 생성 요청을 받은 timestamp.
- `expires_at`: 리소스 만료 timestamp.
- `type`: `user`, `project`, `admin`, `system`. type별 기본 quota 값과 정책을 다르게 적용할 수 있다.
- `namespace_capacity_quota`: optional namespace-wide PVC 요청 용량 quota. 사용자 입력 단위는 TB 기준이며 소수점 입력을 허용한다. 생략 시 quota 입력 해석 원칙을 따른다.
- `namespace_pvc_count_quota`: optional namespace-wide PVC 개수 quota. 생략 시 quota 입력 해석 원칙을 따른다.
- `storage_class_quotas`: optional StorageClass별 quota entry list. 이 필드가 없거나 비어 있으면 namespace-wide ResourceQuota만 적용할 수 있다.
- `storage_class_quotas[].storage_name`: StorageClass별 quota를 적용할 DMS 내부 논리 storage ID. DMS는 이 mapping에서 `storage_class_name`을 derive한다.
- `storage_class_quotas[].storage_class_name`: optional debug/compatibility field. 포함된 경우 `storage_name` mapping과 일치해야 한다.
- `storage_class_quotas[].capacity_quota`: optional StorageClass별 PVC 요청 용량 quota.
- `storage_class_quotas[].pvc_count_quota`: optional StorageClass별 PVC 개수 quota.
- `allow_namespace_create`: namespace가 없을 경우 DMS가 namespace를 생성할지 여부.
- `memo`: 운영자 메모.

Kubernetes namespace quota에서는 파일 개수 quota를 직접 지원하지 않는다. Kubernetes 쪽의 count는 파일 개수가 아니라 PVC object 개수로 정의한다.

파일 개수 제한이 필요한 경우에는 backend filesystem의 inode quota, directory quota, project quota, fileset quota 등을 사용해야 한다.

DMS는 하나의 namespace에 대해 DMS 전용 ResourceQuota를 하나만 생성하고 관리한다.

이미 namespace에 다른 ResourceQuota가 존재할 수 있으므로, DMS는 기존 quota object를 수정하지 않는다. Kubernetes에서는 여러 개의 ResourceQuota가 동시에 적용될 수 있으며, 이 경우 모든 quota 조건을 만족해야 PVC 생성 또는 확장이 가능하다.

따라서 DMS query API는 DMS가 관리하는 quota뿐 아니라 namespace에 존재하는 전체 ResourceQuota도 함께 조회하여 effective quota 관점의 경고를 제공해야 한다.

Effective quota 경고가 발생했을 때 운영자는 두 방향 중 하나를 선택할 수 있어야 한다.

- 실제 Kubernetes 상태가 맞고 운영용 PostgreSQL state가 틀린 경우: Kubernetes namespace storage quota DB sync from live state API를 사용해 실제 DMS-managed ResourceQuota 상태를 운영용 PostgreSQL에 받아들인다.
- 운영용 PostgreSQL의 desired quota가 맞고 Kubernetes namespace의 실제 ResourceQuota가 drift된 경우: 별도 API를 만들지 않고 기존 Kubernetes namespace storage quota update flow를 사용해 DB desired quota를 namespace에 다시 적용한다.

Kubernetes namespace storage quota DB sync from live state API는 Kubernetes namespace에 존재하는 DMS 전용 ResourceQuota를 authoritative live state로 보고 운영용 PostgreSQL을 갱신하는 명시적 복구 operation이다. 이 API는 Kubernetes object를 수정하지 않으며, DB desired/applied/observed state와 recovery 이력만 갱신한다.

Kubernetes namespace quota import/adoption API는 DB에 resource가 없거나 복구가 필요한 상태에서 live DMS 전용 `ResourceQuota/dms-storage-quota`를 DMS-managed namespace quota resource로 편입하는 명시적 operation이다. Import 요청은 optional `expires_at`을 받을 수 있다. 값이 주어지면 timezone-aware ISO-8601 timestamp로 검증하고 현재 시각보다 과거이거나 같으면 실패해야 한다. 값이 없으면 Planner 기준 server-side now부터 365일 뒤 값을 canonical `expires_at`으로 설정한다. `expiry_at`과 `clear_expires_at`은 import payload에서 지원하지 않는 field로 reject한다. Live annotation의 expiry 값은 observed evidence일 뿐이며, 운영용 PostgreSQL desired state의 source of truth는 import request 값 또는 import default 값이다.

DMS는 Kubernetes namespace quota import/adoption 또는 sync-from-live API를 실행하기 전에 다음을 검증해야 한다.

- 대상 `cluster_name + namespace_name`이 요청에서 명확히 지정되었는지 확인한다.
- 대상 namespace에 DMS 전용 ResourceQuota인 `dms-storage-quota`가 존재하는지 확인한다.
- ResourceQuota의 label/annotation이 DMS-managed Kubernetes namespace storage quota resource임을 나타내는지 확인한다.
- ResourceQuota metadata의 `cluster_name`, `namespace_name`, resource key가 요청 대상 또는 복구 대상과 일치하는지 확인한다.
- ResourceQuota `spec.hard`와 `status.used`를 읽고 DMS가 지원하는 namespace-wide storage quota, PVC count quota, StorageClass별 quota key로 해석 가능한지 확인한다.
- live ResourceQuota가 현재 사용량보다 낮은 quota, `0` hard limit, 알 수 없는 quota key, 외부 ResourceQuota와의 effective quota 충돌 같은 warning을 포함하는 경우 이를 result와 diagnostic observability event에 기록한다.

DB sync가 성공하면 DMS는 live ResourceQuota의 `spec.hard`, `status.used`, metadata, 조회 시점, requester id, request id, 이전 DB state, 갱신 후 DB state를 운영용 PostgreSQL에 기록해야 한다. 이때 DMS가 소유하지 않는 다른 ResourceQuota는 DMS desired state로 가져오지 않는다. 다른 ResourceQuota가 effective quota에 영향을 주는 경우에는 query warning 또는 diagnostic event로 남긴다.

DMS 전용 Kubernetes ResourceQuota 이름은 namespace마다 고정된 `dms-storage-quota`를 사용한다.

고정 이름을 사용하는 이유는 DMS가 하나의 namespace에서 ResourceQuota 하나만 관리하기 때문이다. Cluster, namespace, storage mapping 같은 식별 정보는 object name에 넣지 않고 운영용 PostgreSQL의 resource key와 Kubernetes labels/annotations로 추적한다.

DMS-managed ResourceQuota metadata 예:

```yaml
metadata:
  name: dms-storage-quota
  labels:
    app.kubernetes.io/managed-by: dms
    dms.io/resource-kind: kubernetes-namespace-storage-quota
  annotations:
    dms.io/cluster-name: <cluster_name>
    dms.io/namespace-name: <namespace_name>
    dms.io/resource-id: <dms_resource_id_or_resource_key>
```

DMS API에서는 사용자 입력 단위를 TB 기준으로 받는다. `TB`는 decimal terabyte이며 `1TB = 10^12 bytes`로 해석한다. 운영용 PostgreSQL 내부에서는 용량을 byte 단위 정수로 저장한다. Kubernetes에 적용할 때는 이 byte 값을 기준으로 resource quantity로 변환한다.

Kubernetes default quota policy:

- `type=user`: namespace 전체 `requests.storage` quota `1TB`, PVC count quota `20`
- `type=project`: namespace 전체 `requests.storage` quota `4TB`, PVC count quota `200`
- `type=system`: quota 제한 없음. `{unlimited:true}` sentinel을 default policy로 저장한다.
- `type=admin`: quota 제한 없음. `{unlimited:true}` sentinel을 default policy로 저장한다.

Kubernetes default quota policy에서는 StorageClass별 quota를 설정하지 않는다. StorageClass별 quota는 `storage_class_quotas` 요청이나 별도 policy가 있을 때만 plan에 포함한다.

이 기본 quota policy는 명시 quota와 기존 적용 quota가 모두 없는 요청의 plan 생성 기준이며, resource initialize 또는 `reset_quota_to_default=true` update의 quota reset 기준이다. 기본 quota policy 자체를 변경하는 작업은 update의 별도 동작으로 처리한다.

Kubernetes resource initialize 또는 `reset_quota_to_default=true` update는 대상 resource의 DMS-managed ResourceQuota를 기본 policy와 일치시키는 동작이다. 기본 policy에 StorageClass별 quota가 없으면 DMS-managed ResourceQuota의 StorageClass별 desired quota key를 제거한다.

Kubernetes quota에서 StorageClass별 quota를 비활성화한다는 의미는 ResourceQuota `hard`에서 해당 StorageClass별 quota key를 제거한다는 뜻이다. Quota 값을 `0`으로 설정하는 것은 비활성화가 아니라 차단이며, `block=ON` 동작에서만 사용한다.

Kubernetes namespace storage quota update로 변경 가능한 항목:

- namespace-wide capacity quota
- namespace-wide PVC count quota
- StorageClass별 quota entry 추가, 수정, 제거
- type별 기본 quota policy 기준 quota reset (`reset_quota_to_default=true`)
- `expires_at`
- `memo`
- `type`

Kubernetes namespace storage quota update 시 검증해야 하는 항목:

- 대상 `cluster_name`이 운영용 PostgreSQL에 존재하는지 확인한다.
- 대상 `namespace_name`이 운영용 PostgreSQL에 존재하는지 확인한다.
- 대상 `cluster_name + namespace_name` 조합이 운영용 PostgreSQL에 존재하는지 확인한다.
- DMS 관리 ResourceQuota가 실제 cluster에 존재하는지 확인한다.
- 요청된 `storage_class_quotas[].storage_name`이 운영용 PostgreSQL에 존재하는지 확인한다.
- 각 `storage_class_quotas[]` entry의 `storage_name` mapping에서 `storage_class_name`을 derive하고, derived StorageClass가 현재도 실제 cluster에 존재하는지 확인한다.
- 요청 payload에 `storage_class_quotas[].storage_class_name`이 포함된 경우 derived `storage_class_name`과 일치하는지 확인한다.
- `reset_quota_to_default=true` 요청일 경우 대상 resource type에 해당하는 기본 quota policy가 존재하는지 확인한다.
- quota 증가 요청인지 감소 요청인지 확인한다.
- quota 감소 요청일 경우 현재 사용량보다 작은 quota로 낮추는지 확인한다.
- 만료 시간 변경이 정책상 허용되는지 확인한다.

Kubernetes ResourceQuota는 현재 사용량보다 낮은 hard limit으로 변경될 수 있다. 이 경우 기존 PVC가 삭제되지는 않지만, namespace는 quota 초과 상태가 되어 신규 PVC 생성이나 PVC 확장이 제한된다.

DMS는 운영 안정성을 위해 기본적으로 현재 사용량보다 낮은 quota 감소를 거부한다. 단, `force=true` 또는 `block` 처리 목적의 update인 경우에는 허용할 수 있다.

Update 후에는 Kubernetes의 `ResourceQuota.status.used`와 `ResourceQuota.status.hard`를 다시 조회하여 운영용 PostgreSQL의 observed state를 갱신한다.

Kubernetes namespace storage quota block 동작:

- `block=ON`이면 DMS 관리 ResourceQuota의 모든 quota hard limit을 `0`으로 설정한다.
- `type=system`, `type=admin` Kubernetes namespace storage quota resource에 대한 `block=ON` 요청은 실패해야 한다.
- 대상 quota에는 namespace 단위 전체 PVC 요청 용량 quota, namespace 단위 PVC 개수 quota, StorageClass별 PVC 요청 용량 quota, StorageClass별 PVC 개수 quota가 포함된다.
- `block=OFF`이면 block 이전의 원래 desired quota로 복구한다.
- Block 적용 또는 해제 후에는 Kubernetes의 `ResourceQuota.status.used`와 `ResourceQuota.status.hard`를 다시 조회하여 운영용 PostgreSQL의 observed state를 갱신한다.

## DMS Worker Nodes, Agent, and Kubernetes Inventory

이 섹션은 `DMS Execution Topology and Worker Roles`에서 정의한 worker node, Worker runtime, DMS Agent 모델을 전제로 inventory 수집, report 인증, sanity check 책임을 정리한다.

DMS Agent는 node-local capability를 수집해 DMS server에 보고한다. Agent는 managed cluster의 일반 GPU workload node 전체에 기본 배포하지 않는다. 기본 inventory와 실행 검증은 dedicated RM Worker 및 Kubernetes API에서 확인 가능한 상태를 기준으로 한다. 일반 GPU workload node의 mount/CSI 상태 검증은 필요 시 별도 inventory 또는 health-check operation으로 확장한다.

Agent report 인증 및 신뢰 원칙:

- Agent는 client certificate 또는 agent token으로 DMS server에 인증해야 한다.
- DMS server는 agent identity와 node identity를 검증해야 한다.
- Agent report에는 `cluster_name`, node identity, worker role 또는 worker type이 포함되어야 한다.
- 인증에 실패한 report는 거부한다.
- agent identity와 node identity가 일치하지 않는 report는 거부한다.
- 인증 실패 또는 identity mismatch는 observability/log용 PostgreSQL에 diagnostic event로 기록해야 한다.
- Agent 권한은 node-local report 제출에 필요한 최소 권한만 가져야 한다.

DMS server는 agent report와 Kubernetes API inventory를 결합하여 resource management 관점에서 다음을 판단한다.

- 특정 cluster에 어떤 StorageClass가 있는지, 어떤 namespace들이 있는지
- 특정 StorageClass가 어떤 CSI driver를 사용하는지
- CSI controller, CSI node DaemonSet, CSIDriver 같은 cluster-level 상태가 Kubernetes API 기준으로 정상인지
- 해당 CSI driver가 dedicated RM Worker에서 사용 가능한지
- 특정 filesystem mount point가 어느 managed cluster의 어느 RM Worker에 존재하는지
- DMS `storage_name`이 실제 backend storage 및 Kubernetes StorageClass와 일관되게 매핑되는지

예를 들어 `weka-a100-rwx` StorageClass가 A100 cluster에 존재하더라도, dedicated RM Worker에서 WEKA CSI node plugin 또는 필요한 mount/provisioning capability를 확인할 수 없으면 sanity check 실패로 처리한다.

또한 filesystem 요청을 받았지만 `/weka-prod` mount 정보가 agent에서 보고되지 않는다면 DMS는 sanity check 실패로 처리한다.

DMS server는 DM Worker inventory를 통해 다음을 판단할 수 있어야 한다. 여기서 network endpoint 도달성은 DMS control-plane 통신 경로가 아니라 데이터 작업에 필요한 storage endpoint, transfer tool endpoint, credential-bound endpoint 접근성을 의미한다.

- 어떤 DM Worker에 어떤 backend storage filesystem이 mount되어 있는지
- 해당 node가 어떤 source storage와 target storage 사이의 데이터 작업을 실행할 수 있는지
- 해당 node가 필요한 data-operation network endpoint에 도달할 수 있는지
- 해당 node에 필요한 credential과 execution permission이 준비되어 있는지
- 해당 node에서 directory analysis, copy, replication, delete 같은 데이터 작업에 필요한 tool을 실행할 수 있는지
- 해당 node의 load/capacity가 Data Management API job scheduling 조건을 만족하는지

Agent, Worker runtime, heartbeat, report schema의 구체적인 구조는 구현 단계에서 결정한다.

현재 구현의 generic DMS Agent는 `worker_role=agent`로 node inventory를 보고하고, Data Management capability reporter는 `worker_role=dm`으로 tool/mount/identity capability를 보고한다. `DMS_AGENT_IDENTITY_USERS`가 설정된 agent-loop는 local NSS/SSSD compatible lookup으로 POSIX UID/GID/group evidence를 `identities`에 포함한다. DM scheduling은 stale report를 제외하고 같은 node의 fresh DM report를 union하여 mount/tool/identity/credential capability를 판단한다. `/v1/ops/agents`도 기본적으로 같은 effective union을 반환하므로 운영자가 보는 capability와 scheduler decision이 일치한다. 응답에는 `effective_identities`와 `contributing_reports[].identities`가 포함되어 어떤 raw report가 identity evidence와 capability union에 기여했는지 추적할 수 있고, 최신 raw report만 확인해야 하는 경우에는 `effective=false`를 사용한다. 이 때문에 DaemonSet inventory report와 job-specific capability report가 분리되어 있어도 최신 generic report가 `dsync`, `nsync`, `drm`, `dscan` capability를 가리지 않는다.

## StorageClass and storage_name Mapping

DMS 내부에서는 Kubernetes StorageClass를 직접 user-facing storage ID로 노출하지 않고, `storage_name`이라는 논리 ID로 관리한다.

Kubernetes namespace storage quota 요청에서 StorageClass별 quota를 적용할 때 사용자는 `storage_class_quotas[].storage_name`을 지정한다. StorageClass별 quota가 필요 없으면 `storage_class_quotas[]`를 생략하고 namespace-wide quota만 적용할 수 있다.

DMS는 기본적으로 Kubernetes StorageClass 자체를 생성하지 않는다. Managed Kubernetes cluster의 CSI driver와 StorageClass 생성, 변경, 삭제는 cluster 운영자의 책임이며, DMS는 운영자가 사전에 준비한 StorageClass를 `storage_name` mapping으로 등록하고 검증해 사용한다. 추후 DMS가 StorageClass 생성까지 자동화해야 한다면, 이는 namespace quota create/update와 구분되는 별도 명시적 workflow로 설계해야 한다.

DMS는 `storage_class_quotas[].storage_name` mapping을 통해 `storage_class_name`을 derive한다.

따라서 `storage_name`이 DMS 전체에서 unique하면, 요청 payload에 `storage_class_quotas[].storage_class_name`을 중복 입력하지 않아도 DMS는 어떤 Kubernetes StorageClass에 quota를 적용해야 하는지 알 수 있다.

요청 payload에 `storage_class_quotas[].storage_class_name`이 optional field로 포함된 경우에는 mapping에서 derive된 `storage_class_name`과 일치해야 한다. 이 검증은 잘못된 client payload나 stale configuration을 조기에 발견하기 위한 sanity check다.

`storage_name` mapping은 설치 시 초기 values/config 또는 mapping file로 로드할 수 있지만, 설치 시점에만 고정되는 정적 정보가 아니다. DMS는 runtime에 storage backend template과 `storage_name` mapping을 추가, 수정, 비활성화할 수 있는 management capability를 제공해야 한다. 이 capability도 별도 API surface로 분리하지 않고 Resource Management API capability로 제공하며, 다른 DMS API와 동일한 인증 및 operation authorization policy 대상이다.

Runtime mapping 변경은 운영용 PostgreSQL에 versioned state와 변경 이력으로 기록해야 하며, 변경 후에는 Kubernetes API inventory와 DMS Agent report를 기준으로 StorageClass 존재 여부, CSI driver 상태, mount path, quota capability, backend storage 일관성을 sanity check해야 한다. API pod의 filesystem path visibility는 운영 배포에서 storage가 mount되지 않는 것이 정상일 수 있으므로 authoritative readiness로 사용하지 않고 diagnostic evidence로만 기록한다. Sanity check를 통과하지 못한 mapping은 일반 사용자 요청에 사용할 수 없는 상태로 남기고 실패 원인과 diagnostic event를 조회 가능하게 해야 한다.

이미 DMS-managed resource가 참조 중인 `storage_name` mapping은 조용히 다른 backend로 재매핑하면 안 된다. 기존 resource에 영향을 줄 수 있는 mapping 수정이나 비활성화는 영향 범위를 계산하고, 정책상 허용되는 경우에만 명시적인 운영자 요청과 검증 이력으로 처리해야 한다.

매핑 예시:

```yaml
- storage_name: k8s-a100-weka-rwx
  cluster_name: a100-gpu-prod
  storage_class_name: weka-a100-rwx
  backend_type: WEKA
  access_mode: RWX
  mount_path: /weka-prod
  csi_driver: weka.csi.driver

- storage_name: k8s-h100-cephfs-rwx
  cluster_name: h100-gpu-prod
  storage_class_name: cephfs-h100-rwx
  backend_type: CephFS
  access_mode: RWX
  mount_path: /cephfs-prod
  csi_driver: cephfs.csi.ceph.com

- storage_name: k8s-research-local-rwo
  cluster_name: research-gpu-prod
  storage_class_name: local-rwo
  backend_type: LocalPV
  access_mode: RWO
  csi_driver: example.local.csi
```

이 매핑은 운영용 PostgreSQL에 저장하며, DMS Agent report와 Kubernetes API inventory를 통해 계속 검증한다.

StorageClass는 cluster 내부에서만 unique하므로, 운영용 PostgreSQL에서는 `cluster_name + storage_class_name` 조합을 unique key로 사용한다.

`storage_name`은 DMS 전체에서 unique해야 한다.

## Requirements

### Functional Requirements

- 운영용 PostgreSQL을 단일 기준 상태 저장소로 사용해야 한다.
- Observability/log용 PostgreSQL은 diagnostic observability event 저장소로 분리해야 한다.
- Authorization을 통과해 Backend 실행 대상이 된 작업은 `request -> plan -> run -> result` lifecycle을 따라야 한다.
- 사용자 요청 접수/조회 Frontend와 실제 업무 실행 Backend는 분리되어야 한다.
- Frontend는 사용자 request를 Backend에 직접 반영하지 않아야 한다.
- 모든 DMS request는 `requester_id`를 포함해야 하며, DMS는 requester identity를 운영용 PostgreSQL에 기록해야 한다.
- DMS API의 `requester_id` 신뢰 모델은 API 처리 원칙을 따른다. DMS는 인증된 request payload의 `requester_id`를 requester identity로 신뢰하며, 별도의 actor와 구분해 저장해야 한다.
- DMS는 requester identity와 LDAP/SSSD 중앙 identity system의 UID/GID/group mapping data를 운영용 PostgreSQL에 등록, 조회, 검증, 비활성화하는 Identity Mapping API를 제공해야 한다.
- Identity Mapping API와 access control 검증은 중앙 identity system을 read-only로 조회해야 하며, LDAP/SSSD에 user, group, group membership을 생성, 수정, 삭제하지 않아야 한다.
- 구현은 API pod의 NSS/SSSD를 조회하는 `nss` backend와 LDAP을 직접 조회하는 `ldap` backend를 제공한다. 테스트베드는 `DMS_IDENTITY_LOOKUP_MODE=ldap`으로 OpenLDAP을 read-only 조회하고, c1/c2 노드 SSSD `getent`, Identity Mapping refresh worker verification, stale mismatch, disable 후 DM preflight failure, LDAP requester 기반 RM/DM 요청을 `scripts/verify-dms-ldap-live.sh`로 검증한다.
- Data Management API는 인증된 request payload의 `requester_id`를 LDAP/SSSD UID/GID/group으로 매핑하고 POSIX filesystem 권한을 기준으로 데이터 path 접근 허가를 판단해야 한다.
- Planner는 request를 실행 가능한 plan으로 변환하고 운영용 PostgreSQL에 저장해야 한다.
- Planner는 순서가 뒤바뀌어 도착한 mutating request에 대해 resource별 ordering consistency를 보장해야 한다.
- DMS API server, Frontend, Planner, control plane component는 DMS Kubernetes cluster에 배포되어야 한다.
- 각 Worker runtime은 저장된 plan을 기준으로 Backend를 통해 실행 및 검증해야 한다.
- Worker runtime failure, restart, stale claim 상황에서도 작업은 같은 worker role 안에서 fail-over 또는 recovery 가능해야 한다.
- Resource Management API 작업은 각 managed Kubernetes cluster의 dedicated RM Worker에서 실행되는 RM Worker runtime이 수행해야 한다.
- RM Worker는 GPU workload node와 구분되는 dedicated Kubernetes worker node여야 하며, cluster-local Kubernetes API, filesystem mount, CSI driver, quota capability를 기준으로 resource management 작업을 실행하고 검증해야 한다.
- RM Worker runtime과 DMS Agent는 Kubernetes workload로 RM Worker에 scheduling되어야 한다.
- Data Management API 작업은 DMS Kubernetes cluster의 DM Worker에서 실행되는 DM Worker runtime이 수행해야 한다.
- DM Worker runtime과 DMS Agent는 Kubernetes workload로 DM Worker에 scheduling되어야 한다.
- DM Worker는 데이터 작업 대상 storage를 node별 filesystem mount로 제공해야 하며, node별 mount 구성은 다를 수 있다.
- Data Management API job scheduling은 DM Worker별 source storage mount, target storage mount, data-operation network reachability, credential, tool capability, node load/capacity를 고려해야 한다.
- Data Management API request는 Resource Management API와 동일하게 mTLS와 token 기반 DMS API 인증을 통과해야 한다.
- Operation authorization policy는 인증된 `actor`를 기본 판단 주체로 사용해야 하며, 인증 성공이 operation 수행 허가를 의미하지 않아야 한다.
- 인증은 성공했지만 operation authorization policy에서 거부된 요청은 `AuthorizationFailed` terminal result로 기록하고 Backend side effect, plan, run을 생성하지 않아야 한다.
- mTLS 또는 token 인증 실패 요청은 request lifecycle에 넣지 않고 diagnostic observability event로 기록해야 한다.
- Data Management API는 초기 operation으로 `sync`, `rm`, `scan`, `help`, `cancel` capability를 제공해야 한다.
- Data Management API 구현은 `copy`, `chmod` 같은 향후 operation 추가를 고려해 operation registry, validator, option allowlist, preflight rule, preview/confirm policy, tool selection strategy, result artifact handling을 operation별로 확장 가능하게 설계해야 한다.
- Data Management API의 `sync`, `rm`, `scan`은 job 기반으로 실행하고 즉시 `job_id`를 반환해야 한다.
- Data Management API의 source, destination, target은 DMS에 등록된 storage 또는 resource ID와 relative path로 지정해야 하며, worker node absolute path를 user-facing input으로 받지 않아야 한다.
- Data Management API의 `sync` source는 file 또는 directory를 허용하고, `sync` destination과 `rm`/`scan` target은 directory만 허용해야 한다.
- Data Management API option은 operation별 allowlist로 검증해야 하며, raw command-line option string을 그대로 실행 도구에 전달하지 않아야 한다. mpifileutils 세부 flag는 `options.tool_options`의 구조화된 key/value로만 받아야 하고, `sync`는 실행 시점에 `dsync` 또는 `nsync`가 결정되므로 두 tool에서 의미가 같은 공통 option만 허용해야 한다. report/output 경로, `nsync` role-map/role-mode처럼 DMS가 소유하는 tool flag는 사용자 요청으로 설정할 수 없어야 한다.
- Data Management API의 `sync`와 `rm`은 하나의 `job_id` 안에서 dry-run preview phase 후 confirm을 거쳐 execution phase로 진행해야 하며, preview 결과는 configurable TTL을 가져야 한다. 기본 TTL은 24시간이다.
- Data Management API의 `scan`은 preview phase와 confirm 없이 바로 execution phase로 수행해야 한다.
- Data Management API의 `rm`과 `sync --delete` 같은 삭제성 option은 명시적 confirm 없이는 실제 데이터 변경 job으로 실행하지 않아야 한다.
- Data Management API preflight는 후보 DM Worker에서 수행해야 하며, source/destination/target 존재 여부, path type, mount 상태, filesystem health, POSIX 권한, credential, tool capability를 검증해야 한다.
- Data Management API는 mpifileutils 기반 approved job image를 사용해야 하며, image는 `https://github.com/chahwansong/mpifileutils`의 특정 git tag 또는 commit으로 pinning되어야 한다.
- Data Management API `sync`는 mount topology를 기준으로 `dsync` 또는 `nsync`를 자동 선택해야 한다.
- Data Management API `rm`은 `drm`, `scan`은 `dscan`을 사용해야 한다.
- DM Worker runtime은 Volcano scheduler를 통해 `High`, `Mid`, `Low` priority queue 중 하나로 job을 실행해야 하며 기본 queue는 `Mid`여야 한다.
- DM Worker runtime은 candidate DM Worker pool을 DMS inventory와 worker health 기준으로 제한하고, 실제 worker pod 배치는 Volcano가 해당 pool 안에서 수행해야 한다.
- Data Management API 멀티노드 병렬 작업은 같은 job의 worker pod가 같은 node에 함께 배치되지 않도록 강제해야 한다.
- Data Management API job에서 pod가 `Failed` 또는 `Evicted` 상태가 되면 자동 재시도 없이 job을 실패로 종료해야 한다.
- Data Management API job은 warning threshold, timeout, cancel 처리를 지원해야 한다.
- Data Management API의 별도 메타데이터 수정 operation은 현재 범위에서 제외해야 한다.
- DMS는 filesystem resource와 Kubernetes namespace storage quota resource를 관리해야 한다.
- DMS는 Resource Management API로 `create`, `update`, `block`, `initialize`, `query`, `delete`, `expiration sweep`, `default quota policy update`, `existing directory quota assignment`, `import existing filesystem directory`, `Kubernetes namespace quota import/adoption`, `resource consistency check`, `Kubernetes namespace storage quota DB sync from live state` API를 제공해야 한다.
- DMS는 Operational Query API로 현재 review/action이 필요한 unresolved issue list, resource history, requester별 request history, expired/expiring resource list, `block=ON` resource list, failed/recovery-needed request/run list, long-running request/run list, quota usage pressure, DB/live drift 후보, worker/agent health, identity mapping 상태, Data Management Job/preview 상태, diagnostic event correlation을 조회할 수 있어야 한다.
- DMS는 `storage_name + directory_name`을 기준으로 DMS에 등록되지 않은 filesystem directory의 quota를 설정하는 요청을 지원해야 한다.
- DMS는 기존 filesystem directory를 명시적으로 import하여 full DMS-managed filesystem resource로 전환하는 요청을 지원해야 한다.
- DMS는 운영용 PostgreSQL에 등록된 Filesystem resource와 Kubernetes namespace storage quota resource에 대해 실제 backend resource 존재 여부와 DB/live state 일치 여부를 read-only로 비교하는 consistency check 요청을 지원해야 한다.
- Resource consistency check 요청은 단일 resource key, `storage_name` 단위 scope, 또는 명시적 필터 기반 scope를 가져야 하며, scope 없는 요청을 전체 filesystem/Kubernetes resource 일괄 check로 처리하지 않아야 한다.
- DMS는 effective quota 경고 또는 DB 손실/불일치 상황에서 실제 Kubernetes의 DMS-managed ResourceQuota를 검증하고 운영용 PostgreSQL의 Kubernetes namespace storage quota state를 live state 기준으로 갱신하는 요청을 지원해야 한다.
- Resource Management API의 `delete` API는 dummy success로 처리하지 않는다. Filesystem delete는 명시 확인 후 실제 directory를 삭제하고, Kubernetes namespace storage quota delete는 DMS 전용 ResourceQuota를 실제 삭제한 뒤 lifecycle state를 기록한다.
- Data Management API의 `rm` operation은 target directory 삭제 요청으로 Resource Management API의 resource lifecycle `delete`와 구분해야 한다.
- Initialize API는 특정 Kubernetes namespace storage quota resource 또는 filesystem resource의 quota desired state를 type별 기본 quota policy 기준으로 재설정할 수 있어야 한다.
- Type별 기본 quota policy를 운영용 PostgreSQL에 기록하거나 갱신하는 작업은 update의 별도 동작으로 처리해야 한다.
- Expiration sweep API는 DMS가 관리하는 모든 resource의 `expires_at`을 평가하고 만료된 resource를 `block=ON` 처리 대상으로 전환해야 한다. 단, `type=system`, `type=admin` resource는 `block=ON`으로 전환하지 않고 실패 또는 skip 결과로 기록해야 한다.
- Update API는 Kubernetes namespace storage quota와 filesystem resource의 quota, metadata, 만료 시간, type 변경, 기본 quota policy 변경을 처리해야 한다.
- Kubernetes namespace storage quota update는 DB desired quota가 맞고 실제 namespace의 DMS-managed ResourceQuota가 drift된 경우 DB 기준 quota를 다시 적용하는 repair use case로도 사용할 수 있어야 한다.
- Update 후에는 실제 backend 상태를 다시 조회하여 observed state를 갱신해야 한다.
- Update, initialize, block, quota assignment, import 같은 mutating request는 대상 resource의 현재 상태와 precondition을 검증해야 한다.
- Block API는 resource provisioning의 `ON`/`OFF` 차단 상태를 처리해야 한다.
- Block API는 `type=system`, `type=admin` resource에 대한 `block=ON` 요청을 실패 처리해야 한다.
- `block=OFF`는 block 이전의 원래 desired state로 복구해야 한다.
- Kubernetes `block=ON`은 DMS 관리 ResourceQuota의 모든 hard limit을 `0`으로 설정해야 한다.
- Filesystem `block=ON`은 `readonly` mode 또는 chmod 기반 `root-owned` mode를 지원해야 하며, 두 mode 간 전환도 가능해야 한다.
- Query API는 운영용 PostgreSQL에 저장된 상태를 기본 조회 기준으로 사용해야 하며, 필요한 경우 backend live 조회를 수행할 수 있어야 한다.
- Query API가 backend live 조회를 수행한 경우 가능한 한 조회 결과를 운영용 PostgreSQL observed state 또는 observability/log용 PostgreSQL diagnostic event로 기록해야 한다.
- Query API는 가능한 많은 정보를 반환하고 client 또는 호출 주체가 payload를 가공하도록 해야 한다.
- DMS는 운영 디버깅을 위한 critical lifecycle state를 운영용 PostgreSQL에 기록하고, diagnostic observability event를 observability/log용 PostgreSQL에 기록해야 한다.
- Observability query는 운영용 PostgreSQL의 request lifecycle 이력과 observability/log용 PostgreSQL의 API 호출 이력, actor, latency, error 요약, component 구조화 로그, system monitoring log를 함께 조회할 수 있어야 한다.
- Observability event의 actor는 이벤트를 발생시킨 API server, RM Worker runtime, DM Worker runtime, Agent, system job 같은 주체를 의미하며, request lifecycle의 `requester_id`와 구분해야 한다.
- Diagnostic observability event는 중요도와 종류별 retention을 가져야 하며, 중요하지 않은 데이터는 삭제될 수 있어야 한다.
- Critical lifecycle state 저장 실패는 DMS 핵심 resource operation을 성공으로 처리하지 못하게 해야 하며, 이 경우 명확한 error message와 recovery 상태를 남겨야 한다.
- Diagnostic observability event 저장 실패는 가능한 한 core resource operation을 실패시키지 않아야 하며, 최소한의 critical error state 또는 fallback log로 추적해야 한다.
- 필요하면 관찰성 데이터를 외부 log/monitoring system으로 export할 수 있어야 한다.
- DMS는 failure handling과 fail-over를 기본 기능으로 제공해야 한다.
- DMS는 작업이 영구 hang 상태가 되거나 deadlock으로 멈추지 않도록 timeout, lease, retry, stale run recovery를 고려해야 한다.
- DMS는 source code update와 component 배포 시 API 조회와 request 접수 경로를 중단하지 않는 rolling upgrade capability를 제공해야 한다.
- DMS는 maintenance/drain mode를 제공해야 하며, 해당 mode에서는 새 mutating request를 운영용 PostgreSQL에 접수할 수 있더라도 Backend execution plan claim, RM Worker execution, DM Worker scheduling, VolcanoJob 생성은 resume 전까지 시작하지 않아야 한다.
- DMS는 maintenance/drain mode 진입, drain status 조회, resume, startup recovery check를 DMS API capability와 운영자용 CLI/스크립트로 제공해야 한다.
- DMS database schema migration은 expand-contract 원칙을 따라 backward-compatible하게 설계해야 하며, 새 버전과 직전 버전 component가 rolling upgrade 중 같은 운영용 PostgreSQL을 사용할 수 있어야 한다.
- DMS는 계획 정전, 데이터센터 전원 차단, 서버 전체 재부팅을 대비해 정보 손실 없이 shutdown/startup할 수 있는 구현을 제공해야 한다.
- 계획 shutdown은 maintenance mode 진입, 새 Backend scheduling 중지, running 작업 drain, critical lifecycle state 기록 확인, PostgreSQL HA/backup/PITR 상태 확인, DMS component 종료 순서를 포함해야 한다.
- 갑작스러운 power loss 또는 전체 reboot 후 DMS는 운영용 PostgreSQL의 request/plan/run/result, lease, heartbeat, Kubernetes/VolcanoJob 상태, live backend observed state를 재조회하여 stale, recovery-needed, unknown-after-side-effect 대상을 복구 또는 운영자 조치 대상으로 기록해야 한다.
- DMS API는 Ingress를 통해 외부에서 호출할 수 있어야 한다.
- DMS 외부에서 DMS 내부로 들어오는 API 요청은 mTLS(client certificate 검증 포함)와 token 기반 인증을 사용해야 한다.
- Maintenance/drain, rolling upgrade, shutdown readiness, startup recovery check 같은 운영 control API도 동일한 DMS API 인증을 사용하고, 별도의 일반/운영 인증 체계로 분리하지 않는다. 단, operation authorization policy는 운영 control capability별로 별도로 판단해야 한다.
- 운영자는 request, plan, run, result 상태를 조회할 수 있어야 한다.
- 초기 셋업 시 필요한 사전 의존성 정보, in-cluster Service, Kubernetes Secret, config/values 입력 위치, 검증 명령을 구현 산출물과 설정 구조에 반영해야 한다.
- DMS는 폐쇄망 환경에서도 반복 가능하게 배포할 수 있도록 offline bundle, image 반입, manifest/chart, migration, 검증 스크립트, rollback 가능한 설치 방식을 제공해야 한다.
- DMS는 agent report와 Kubernetes API inventory를 결합해 storage 및 cluster sanity check를 수행해야 한다.
- DMS Agent report는 client certificate 또는 agent token으로 인증되어야 한다.
- DMS server는 agent identity와 node identity를 검증해야 한다.
- 인증 실패 또는 identity mismatch가 있는 agent report는 거부하고 observability/log용 PostgreSQL에 diagnostic event로 기록해야 한다.
- Agent 권한은 report 제출에 필요한 최소 권한만 가져야 한다.
- DMS Agent는 RM Worker와 DM Worker에 배포되어야 하며, 일반 GPU workload node 전체에는 기본 배포하지 않는다.
- DMS는 DM Worker의 source/target mount, network, credential, tool capability, load/capacity inventory를 수집하거나 검증할 수 있어야 한다.
- DMS는 새로운 storage backend 추가를 위한 template 및 확장 가능한 adapter/strategy 구조를 제공해야 한다.
- DMS는 기존 directory quota assignment와 import existing filesystem directory에서 storage root 외부 path, path separator, path traversal, symlink escape를 방지해야 한다.
- DMS는 import existing filesystem directory 처리 시 현재 filesystem state를 live 조회하고, 해석 가능한 access control과 quota state를 초기 desired/applied/observed state로 기록해야 한다.
- DMS는 filesystem directory access control에 필요한 Linux user/group/membership을 중앙 identity/group system에서 read-only로 조회하고 검증해야 한다.
- 실제 운영 Kubernetes cluster의 중앙 identity/group system은 SSSD/LDAP 사용을 전제로 한다.
- DMS는 directory group ownership과 permission을 filesystem metadata로 직접 적용할 수 있어야 한다.
- DMS는 사용자 계정 또는 조직 identity의 원천 시스템을 직접 소유하지 않으며, 존재하지 않거나 target node에서 일관되게 조회되지 않는 사용자에 대한 access control 요청은 실패 또는 보류 상태로 처리해야 한다.
- 검증 환경에서는 SSSD/LDAP 또는 이에 준하는 중앙 identity/group system을 사용해 Linux user/group 조회, group membership 조회, target node 조회 일관성을 검증해야 한다.

### Operational Requirements

- 운영용 PostgreSQL과 observability/log용 PostgreSQL은 DMS Kubernetes cluster 내부에 HA 구성으로 제공된다고 가정한다.
- DMS는 운영용 PostgreSQL과 observability/log용 PostgreSQL의 in-cluster Service DNS, Secret reference, TLS 설정, database/schema, migration 권한이 올바른지 초기 셋업 또는 시작 시점에 검증할 수 있어야 한다.
- DMS는 운영용 PostgreSQL 또는 observability/log용 PostgreSQL 장애, 네트워크 단절, 인증 실패, schema 접근 실패, migration 권한 부족 같은 연결 및 권한 문제를 명확한 오류로 노출해야 한다.
- DMS component restart 후에도 운영용 PostgreSQL 상태를 기준으로 작업을 재개할 수 있어야 한다.
- Worker runtime role별 claim은 timeout 또는 lease 기반이어야 하며, stale claim은 recovery 대상이 되어야 한다.
- 외부 API, Kubernetes API, filesystem, ssh, storage CLI 호출은 timeout을 가져야 한다.
- DMS는 deadlock, lock wait timeout, stale run을 감지하고 실패 또는 재시도 상태로 전환해야 한다.
- DMS는 request, plan, run이 영구적으로 pending/running/claimed 상태에 머무르지 않도록 해야 한다.
- DMS는 mTLS client certificate 검증 또는 token 인증 정보가 없는 외부 API 요청을 거부해야 한다.
- DMS는 인증되지 않았거나 identity mismatch가 있는 agent report를 거부해야 한다.
- 요청 접수 경로는 실제 업무 실행 지연에 의해 직접 막히지 않아야 한다.
- DMS API server와 query-serving component는 rolling update 중에도 외부 API availability를 유지하도록 복수 replica, readiness/liveness/startup probe, graceful termination, PodDisruptionBudget, `maxUnavailable=0` 또는 동등한 배포 전략을 고려해야 한다.
- Maintenance/drain mode는 운영용 PostgreSQL에 기록된 DMS control state로 판단해야 하며, Planner, RM Worker runtime, DM Worker runtime은 이 상태를 존중해 새 Backend side effect와 새 VolcanoJob 생성을 시작하지 않아야 한다.
- Maintenance/drain mode 중에도 read-only query와 drain/status/recovery query는 가능해야 하며, mutating request를 접수할 경우 실행은 resume 이후로 보류되어야 한다.
- Rolling upgrade 중 running 작업은 drain 완료, timeout, recovery-needed, unknown-after-side-effect 중 하나로 명확히 기록되어야 하며, 기록 없이 사라진 작업이 없어야 한다.
- Schema migration은 migration version과 상태를 기록해야 하며, backward-compatible expand 단계와 제거성 contract 단계가 같은 배포 단위에 섞이지 않도록 해야 한다.
- PostgreSQL HA, backup, restore, PITR 설정 자체는 운영자가 제공하지만, DMS는 해당 설정 정보, 검증 명령, 실패 시 조치 기준을 설치 및 운영 산출물에서 참조할 수 있게 구성해야 한다.
- Multi-cluster 환경에서 hostname uniqueness와 `cluster_name + namespace_name` namespace identity를 검증해야 한다.
- Namespace identity의 최종 uniqueness 기준은 `cluster_name + namespace_name`이다. `namespace_name` 단독 global uniqueness는 요구하지 않는다.
- Resource Management API의 기본 sanity check는 Kubernetes API와 dedicated RM Worker에서 확인 가능한 StorageClass, CSI driver, mount point, backend storage mapping의 불일치를 기준으로 실패 처리해야 한다.
- 폐쇄망 설치는 public internet 접근을 요구하지 않아야 하며, 필요한 container image, manifest, chart, binary, migration, 예제 설정, checksum, 설치 검증 도구를 offline bundle 또는 사내 registry/mirror 방식으로 제공할 수 있어야 한다.

### Documentation and Runbook Requirements

구현 에이전트는 DMS 시스템 구현뿐 아니라 설치, 사용, 운영, 재구성, 테스트베드 검증에 필요한 문서와 실행 가능한 절차를 함께 제공해야 한다. 이 섹션은 구현 완료 시 반드시 남겨야 하는 문서 산출물의 범위를 정의한다. 각 문서는 한국어로 작성하고, 운영자가 따라 실행할 수 있도록 필요한 입력 정보, 수정해야 하는 파일, 실행할 명령 또는 스크립트, 기대 결과, 실패 시 조치 기준을 포함해야 한다.

- `docs/install.md`: 최초 설치와 폐쇄망 설치를 다룬다. 사전 준비 시스템, Kubernetes cluster 정보, PostgreSQL HA/backup/PITR 확인, registry, Secret, values/config, migration, component 배포 순서, 설치 후 smoke test, rollback 또는 재시도 절차를 포함해야 한다.
- `docs/usage.md`: 사용자와 API caller가 DMS API를 사용하는 방법을 다룬다. Resource Management API, Data Management API, Identity Mapping API, Operational Query API의 기본 사용 흐름, 인증 입력, request 제출, 상태 조회, preview/confirm, cancel, 주요 error 해석을 포함해야 한다.
- `docs/operations.md`: 운영자가 DMS를 안정적으로 운영하기 위한 절차를 다룬다. Action-required query, observability query, 장애 대응, worker/agent health 확인, maintenance/drain mode, rolling upgrade, planned shutdown/startup, unplanned power loss recovery, PostgreSQL backup/restore 확인, troubleshooting checklist를 포함해야 한다.
- `docs/reconfiguration.md`: 설치 후 runtime 재구성 절차를 다룬다. `storage_name` mapping 추가/수정/비활성화, StorageClass 등록, default quota policy 변경, RM/DM Worker 추가 또는 제거, identity mapping refresh, mpifileutils image tag/commit 변경과 재배포, storage backend template 확장 절차를 포함해야 한다.
- `testbed/`: 테스트베드 또는 특정 검증 환경의 전체 구성 정보를 기록한다. PostgreSQL, registry, identity provider, cluster, storage backend처럼 현재 존재하는 시스템뿐 아니라 구현 중 추가로 설치한 시스템, 검증용 컴포넌트, 관련 명령과 검증 결과도 이 디렉터리 아래에 문서화한다. DMS 구현과 검증 시 특정 파일명에 한정하지 않고 `testbed/`의 모든 관련 정보를 참조한다. Secret 값은 평문으로 기록하지 않고 Secret reference와 검증 명령만 남긴다.
- `tests/results/`: 구현 검증 결과를 기록한다. 각 결과 문서는 실행한 커맨드 또는 테스트, 주요 output, 성공/실패 결과, 실패 원인, 재시도 여부, 관련 commit 또는 테스트베드 상태를 포함해야 한다. 배경과 자세한 설명을 반드시 포함하도록 한다.

문서 요구사항은 구현 기능과 분리된 부가 산출물이 아니라 구현 완료 조건의 일부다. 구현 에이전트는 `Implementation Verification Matrix`의 검증 항목을 수행할 때 관련 매뉴얼 절차와 스크립트도 함께 검증해야 한다. 특히 설치, rolling upgrade, maintenance/drain, planned shutdown/startup, unplanned reboot recovery, runtime reconfiguration 절차는 테스트베드에서 가능한 범위로 실행하거나 축소 검증하고, 테스트베드 한계 때문에 검증하지 못한 부분은 명시적으로 기록해야 한다.

### Implementation Verification Matrix

구현 에이전트는 세부 테스트 프레임워크를 자유롭게 선택할 수 있지만, 다음 검증 항목은 구현 완료 판정의 최소 기준으로 다뤄야 한다. 실제 테스트는 unit, integration, e2e, smoke test, fault-injection test, manual validation script 중 적절한 방식으로 나눌 수 있다.

| Area | Required verification | Expected evidence |
| --- | --- | --- |
| Authentication failure | mTLS client certificate 또는 token 인증 실패 요청은 request lifecycle에 들어가지 않는다. | diagnostic event, API rejection response, 운영용 PostgreSQL request 미생성 |
| Authorization failure | 인증은 통과했지만 operation policy가 거부한 요청은 `AuthorizationFailed`로 종료된다. | actor, `requester_id`, policy reason, no plan, no run, no backend side effect |
| Request persistence | mutating request는 Backend side effect 전에 운영용 PostgreSQL에 저장된다. | request row/state, request commit timestamp, no direct backend mutation path |
| Direct Control Mutation audit | control/config mutation과 운영 action은 request lifecycle 대신 별도 audit record로 추적된다. | `/v1/ops/control-mutations`, authorization failure audit, before/after state |
| Request ordering | 동일 resource의 후속 mutating request는 선행 request terminal 전 Backend에 적용되지 않는다. | conflict/wait/stale 상태, resource version/precondition 기록 |
| Planner conflict | 존재하지 않는 resource update, 중복 create, stale request는 Backend 호출 없이 실패 또는 conflict 처리된다. | failed/conflict result, diagnostic event, backend call absence |
| Worker lease | Worker runtime claim은 lease/heartbeat를 가지며 stale claim은 recovery 대상이 된다. | lease record, heartbeat, stale detection, re-claim 또는 recovery result |
| Worker side-effect boundary | Worker runtime은 Backend side effect 전에 claim과 `Applying`/`Running` 상태를 commit한다. | backend 호출 직전 별도 DB session에서 request/plan/run state 조회, crash-after-side-effect recovery 대상화 |
| Critical state failure | Backend side effect 이후 critical lifecycle state 저장 실패는 success로 처리하지 않는다. | `RecoveryNeeded` 또는 `UnknownAfterSideEffect`, recovery query result |
| Observability separation | high-volume diagnostic event는 observability/log용 PostgreSQL에 기록되고 critical state와 구분된다. | lifecycle state와 diagnostic event correlation query |
| Agent authentication | 인증 실패 또는 node identity mismatch agent report는 inventory에 반영되지 않는다. | rejection event, unchanged inventory |
| Inventory sanity | Kubernetes API inventory와 Agent report 불일치는 sanity check failure로 조회된다. | failed mapping or inventory status, reason, diagnostic event |
| Storage mapping runtime change | `storage_name` mapping 추가/수정/비활성화는 version, 영향 범위, sanity result를 남긴다. | mapping history, version, affected resource check |
| Identity mapping | 중앙 identity system은 read-only로 조회되고 DMS mapping state만 갱신된다. | LDAP/SSSD read evidence, no central write credential/use |
| Filesystem path safety | path separator, traversal, symlink escape, storage root outside path는 거부된다. | rejected request or preflight failure reason |
| Filesystem create/update/block | RM Worker runtime은 적용 후 quota, permission, ownership을 재조회한다. Phase 12 quota apply 경로는 recursive usage scan을 수행하지 않는다. | desired/applied/observed/result history |
| Existing directory quota assignment | 기존 directory는 생성/삭제/access control ownership 없이 quota-only managed state로 추적된다. | quota-only resource state, quota adapter evidence |
| Import existing directory | import는 현재 filesystem state를 읽고 final verification 후 full managed state로 전환한다. | imported state, transition history, verification result |
| Resource consistency check | 명시한 단일 resource, `storage_name` scope, 또는 filter scope 안에서 DB에 등록된 filesystem/Kubernetes resource의 live 존재 여부와 DB/live state 차이를 read-only로 비교한다. | requested scope, `Consistent`/`Drifted`/`Missing`/`CheckFailed` result, DB snapshot, live snapshot, diff summary |
| Kubernetes namespace identity | 같은 namespace 이름이 서로 다른 cluster에 있어도 `cluster_name + namespace_name`으로 구분된다. | two resources with same namespace name and different cluster names |
| Kubernetes StorageClass quota optionality | `storage_class_quotas[]` 없이 namespace-wide ResourceQuota만 생성/유지할 수 있다. | ResourceQuota hard fields without StorageClass-specific hard keys |
| Kubernetes quota live DB sync | DMS-managed ResourceQuota live state를 기준으로 운영용 PostgreSQL state를 갱신할 수 있다. | previous DB state, live spec/status, updated DB state |
| Kubernetes quota repair update | DB desired quota가 맞는 경우 update flow로 ResourceQuota를 DB 기준으로 재적용한다. | reapplied ResourceQuota spec/status and result |
| Data Management path type | `sync` source는 file 또는 directory만, `sync` destination과 `rm`/`scan` target은 directory만 허용한다. | preflight success/failure reason by path type |
| Data Management authorization | Data Management operation authorization failure는 preflight, preview, execution을 시작하지 않는다. | `AuthorizationFailed`, no VolcanoJob |
| Data Management preview | `sync`/`rm`은 dry-run preview와 confirm 없이는 execution phase로 가지 않는다. | DataJob `PreviewSucceeded`/`ConfirmPending`, common lifecycle `Blocked`, no mutation before confirm |
| Preview TTL | preview TTL 만료 후 confirm은 거부되고 job은 `PreviewExpired`가 된다. | expired state, rejected confirm response |
| Data Management tool selection | mount topology에 따라 `dsync` 또는 `nsync`가 선택되고 이유가 기록된다. | selected tool, candidate pool summary, selection reason |
| Volcano scheduling | multi-node job은 node당 worker pod 하나 원칙을 강제한다. | VolcanoJob/Kubernetes scheduling rule and pod placement evidence |
| Pod failure handling | Volcano worker pod `Failed` 또는 `Evicted`는 자동 재시도 없이 Data Management Job 실패로 이어진다. | failed job state, pod status, no retry attempt |
| Timeout and cancel | warning threshold, timeout, cancel은 job state와 diagnostic event로 남는다. | `TimedOut` or `Cancelled`, termination evidence |
| Action-required query | 현재 unresolved failure/error/warning/drift/stale 항목을 운영자가 조치할 수 있는 형태로 조회한다. | issue list with severity, category, affected target, related lifecycle state, recommended action |
| Operational Query | failed/recovery-needed/long-running/drift/identity/job history 조회가 가능하다. | query responses with lifecycle and diagnostic correlation |
| Rolling upgrade availability | component rolling update 중 API query와 request 접수 경로가 유지된다. | successful API smoke test during rollout, readiness/PDB/rollout evidence |
| Maintenance drain | maintenance/drain mode에서 새 Backend execution과 VolcanoJob scheduling이 중지되고 running 작업이 terminal 또는 recovery 상태로 정리된다. | drain status, no new plan claim/job scheduling, completed or recovery-marked runs |
| Expand-contract migration | schema migration 중 직전 버전과 새 버전 component가 같은 운영용 PostgreSQL을 사용할 수 있다. | migration version history, compatibility test, rollback or retry evidence |
| Planned shutdown/startup | runbook 순서로 DMS를 내리고 올린 뒤 lifecycle state와 pending/recovery 대상이 손실 없이 유지된다. | shutdown readiness check, startup recovery check, unchanged request/run/result history |
| Unplanned reboot recovery | component 또는 cluster 강제 재시작 후 stale lease와 불명확한 side effect가 복구 또는 action-required issue로 기록된다. | stale/recovery scan result, recovered or `RecoveryNeeded`/`UnknownAfterSideEffect` state |
| Operations runbooks | rolling upgrade, planned shutdown/startup, power-loss recovery 절차가 스크립트와 매뉴얼로 재현 가능하다. | documented commands, script output, operator validation checklist |
| Documentation artifacts | 설치, 사용, 운영, 재구성, 테스트베드 검증 문서가 구현 결과와 함께 최신 상태로 제공된다. | `docs/install.md`, `docs/usage.md`, `docs/operations.md`, `docs/reconfiguration.md`, `testbed/`, `tests` |
| Installation smoke test | 설치 후 PostgreSQL, Ingress auth, inventory, identity, resource operation smoke test가 실행 가능하다. | `scripts/` 또는 동등 명령 결과와 문서화된 output |

## Operational Scenarios

이 장의 Resource Management operation scenario에서 별도 수식 없이 쓰는 Worker는 RM Worker runtime을 의미한다. Data Management operation scenario는 DM Worker runtime과 Volcano worker pod의 책임을 명시적으로 구분한다.

### Initial Setup

운영자는 DMS 설치 전에 DMS Kubernetes cluster 내부에 운영용 PostgreSQL과 observability/log용 PostgreSQL을 준비하고, DMS 설치 시 해당 PostgreSQL을 사용하기 위한 정보를 제공한다. DMS 설치 절차는 PostgreSQL 배포 자체를 기본 책임으로 갖지 않는다.

DMS는 제공된 정보를 사용해 운영용 PostgreSQL과 observability/log용 PostgreSQL의 Service DNS resolve, TCP 연결, TLS 인증, database/schema 접근, application user read/write 권한, migration user 권한, observability/log write 권한을 검증하고, 필요한 경우 schema migration 가능 여부를 확인한다. 초기 셋업의 목표는 DMS가 사용할 기준 상태 저장소와 진단 이벤트 저장소를 명확히 준비하는 것이다.

### Installation and Air-gapped Deployment

DMS는 일반 Kubernetes 환경뿐 아니라 폐쇄망 환경에서도 반복 가능하게 설치할 수 있어야 한다.

매뉴얼 산출물의 전체 목록과 범위는 `Documentation and Runbook Requirements`를 따른다. 이 섹션은 설치 구현과 설치 runbook이 지원해야 하는 흐름을 정의한다.

설치 구현과 설치 runbook은 다음 내용을 A-Z로 지원해야 한다.

1. 사전 시스템 및 컴포넌트 준비 확인
   - DMS Kubernetes cluster와 managed Kubernetes cluster의 Kubernetes version, OS, container runtime, CNI, Ingress controller, DNS, StorageClass, CSI driver 조건을 확인한다.
   - DMS Kubernetes cluster 내부에 운영용 PostgreSQL과 observability/log용 PostgreSQL이 준비되어 있는지 확인한다. 운영 환경은 HA PostgreSQL을 기준으로 하고, 개발 또는 로컬 검증 환경은 단일 instance를 허용할 수 있다.
   - Volcano scheduler, 중앙 identity/group system(SSSD/LDAP), storage backend client, filesystem mount, DM Worker용 mpifileutils image, 사내 registry 또는 offline bundle 준비 상태를 확인한다.
   - 운영자가 사용할 로컬 또는 bastion 환경에 `kubectl`, `helm` 또는 `kustomize`, registry login 도구, `psql` 또는 PostgreSQL client image 사용 방법, SSH 접속 정보가 준비되어 있는지 확인한다.

2. 환경 정보 수집 및 기록
   - 운영 환경 metadata에는 cluster 이름, kubeconfig 위치, node, Kubernetes version, CNI, Volcano, registry, network 정보를 기록한다.
   - 테스트베드 환경 metadata는 특정 파일명에 한정하지 않고 `testbed/` 디렉터리의 모든 관련 문서와 파일에 기록한다. 구현을 시작하기 전과 검증 전에 `testbed/`의 관련 정보를 확인하고, PostgreSQL, identity provider, registry, storage backend, filesystem mount, network, Secret reference, 검증 명령 같은 실제 상태가 바뀌면 해당 디렉터리 아래 문서를 추가하거나 갱신한다.
   - identity provider metadata에는 provider 이름, LDAP/SSSD read endpoint 또는 NSS/SSSD 조회 방식, read-only service account 또는 Secret reference, test user/group, `getent`/`id` 검증 명령, DMS requester identity와 POSIX username mapping 예시를 기록한다. 중앙 identity system write credential은 DMS 설치 입력으로 받지 않는다.
   - storage backend template과 `storage_name` mapping에는 backend storage, StorageClass, mount path, CSI driver, access mode, quota capability를 기록한다.
   - DM Worker별 filesystem mount, credential, tool capability, data-operation network reachability, load/capacity 기준을 기록한다.

3. 설치 values/config 및 Secret 준비
   - Helm chart, Kustomize, raw manifest 중 지원하는 설치 방식을 명확히 고르고, 해당 방식에서 수정해야 하는 values 또는 config file 목록을 제공한다.
   - values/config에는 DMS namespace, image registry와 tag, DMS API Ingress hostname/class/mTLS, PostgreSQL Service DNS와 database/schema, PostgreSQL Secret reference, connection pool, timeout, migration policy, observability retention, worker resource request/limit, Volcano queue, storage mapping file path, identity provider reference, identity mapping seed file path를 기입한다.
   - Kubernetes Secret에는 PostgreSQL password/token, TLS CA/cert/key, client certificate, API token, registry pull secret, storage credential을 저장한다. 설치 가이드는 `kubectl create secret ...` 또는 `kubectl apply -f ...` 기준 명령을 제공해야 한다.
   - 폐쇄망에서는 image tarball export/import, 사내 registry mirror, chart/manifest 반입, checksum 또는 signature 검증 절차를 제공해야 한다.

4. 설치 전 검증 명령 실행
   - DMS Kubernetes cluster context와 namespace를 확인한다: `kubectl config current-context`, `kubectl get nodes`, `kubectl get ns`.
   - PostgreSQL 상태를 확인한다: `kubectl -n <postgres_namespace> get pods,svc,secret,pvc`, `kubectl -n <postgres_namespace> rollout status ...`, `pg_isready` 또는 `psql` 기반 접속 확인.
   - DMS namespace에서 PostgreSQL Service DNS resolve, TCP 연결, TLS 인증, database/schema 접근, migration 권한, read/write 권한을 검증한다.
   - Ingress DNS, mTLS server certificate, client certificate CA, token issuer 또는 Secret 참조를 검증한다.
   - storage mount, CSI driver, StorageClass, quota capability, SSSD/LDAP read-only user/group 조회, Volcano queue 상태를 검증한다.

5. DMS component 설치
   - namespace, ServiceAccount, RBAC, ConfigMap, Secret, CRD가 필요한 경우 CRD를 먼저 적용한다.
   - database schema migration을 실행하고, migration 실패 시 rollback 또는 재시도 기준을 명확히 따른다.
   - DMS API server, Frontend, Planner, control plane component, RM Worker runtime, DM Worker runtime, Agent를 정해진 순서로 배포한다.
   - 각 managed cluster에는 dedicated RM Worker와 RM Worker runtime, Agent를 배포한다.
   - DMS Kubernetes cluster에는 DM Worker를 구성하고 DM Worker runtime과 Agent를 배포한다.

6. 설치 후 검증 및 초기 데이터 등록
   - DMS API 인증 smoke test를 수행한다.
   - PostgreSQL schema version, 운영용 PostgreSQL write/read, observability/log용 PostgreSQL diagnostic event write/read를 확인한다.
   - Kubernetes inventory refresh와 sanity check를 실행한다.
   - storage backend template, `storage_name` mapping, 초기 default quota policy를 등록 또는 확인한다. 설치 후에도 운영자는 runtime management capability로 새 mapping을 추가하거나 기존 mapping을 수정, 비활성화할 수 있어야 한다.
   - Identity Mapping API로 초기 requester identity mapping 예시를 등록하고, LDAP/SSSD read 검증 결과가 운영용 PostgreSQL에 기록되는지 확인한다.
   - filesystem mount, quota capability, CSI driver, StorageClass, SSSD/LDAP read-only 연동을 다시 검증한다.
   - create/query/update/block/initialize/import 같은 핵심 Resource Management operation smoke test를 수행한다.

7. 운영 절차와 실패 대응 기록
   - 설치 실패 시 rollback, cleanup, 재시도 절차를 제공한다.
   - upgrade, downgrade, backup, restore, disaster recovery 절차를 제공한다.
   - rolling upgrade 중 API availability 확인, maintenance/drain mode 진입과 resume, schema migration version 확인, rollback 또는 재시도 기준을 제공한다.
   - 계획 정전 또는 전체 shutdown/startup runbook에는 shutdown readiness check, drain status 확인, PostgreSQL HA/backup/PITR 상태 확인, DMS component 종료/기동 순서, startup recovery check를 포함한다.
   - PostgreSQL backup/restore와 DMS schema migration rollback 기준을 함께 설명한다.
   - 폐쇄망에서 외부 log/monitoring system export를 사용하지 않는 경우의 fallback 운영 방법을 제공한다.
   - troubleshooting checklist와 흔한 error message별 조치 방법을 제공한다.

구현 산출물은 위 순서를 실제로 실행할 수 있도록 `scripts/`와 `config/` 또는 chart values에 필요한 파일을 제공해야 한다. 예를 들어 `config/dms-values.yaml`, `config/storage-backends.yaml`, `config/storage-mapping.yaml`, `config/identity-mappings.yaml`, `config/secret-refs.yaml` 같은 설치 입력 파일이나 그에 대응하는 chart values 파일을 두고, 설치 가이드는 각 파일의 어떤 key에 어떤 값을 기입해야 하는지 설명해야 한다. PostgreSQL 검증, migration, 설치 검증, inventory refresh, identity mapping 검증, smoke test는 `scripts/validate-postgresql.sh`, `scripts/run-migrations.sh`, `scripts/verify-install.sh`, `scripts/refresh-inventory.sh`, `scripts/smoke-test.sh` 같은 명시적인 스크립트 또는 동등한 명령 예제로 제공되어야 한다. Rolling upgrade와 shutdown/startup 운영을 위해서는 `scripts/enter-maintenance.sh`, `scripts/drain-dms.sh`, `scripts/resume-dms.sh`, `scripts/verify-upgrade.sh`, `scripts/planned-shutdown.sh`, `scripts/startup-recovery-check.sh` 같은 명시적인 스크립트 또는 동등한 CLI command 예제를 제공해야 한다.

폐쇄망 설치 산출물은 public registry나 외부 package repository 접근 없이 설치 가능해야 한다.

Offline bundle에는 최소한 다음 항목이 포함되어야 한다.

- DMS component container images
- DMS Agent image
- 설치 manifest 또는 chart
- CRD가 필요한 경우 CRD manifest
- database migration artifact
- 예제 values/config
- storage backend template 예제
- certificate/secret 생성 예제
- smoke test 및 sanity check 실행 예제
- checksum 또는 signature 검증 정보

구체적인 packaging 방식은 구현 단계에서 결정하되, 운영자가 폐쇄망 반입, 설치, 검증, 장애 조치까지 문서만 보고 수행할 수 있어야 한다.

### Rolling Upgrade and Maintenance Drain

운영자는 DMS source code update와 component image 배포를 API availability를 유지한 상태로 수행할 수 있어야 한다. 이 요구사항의 무중단 범위는 외부 client가 DMS API에 접근해 read/query를 수행하고 request를 접수할 수 있는 상태를 유지하는 것이다. 배포 중 Backend execution과 Worker scheduling은 maintenance/drain policy에 따라 일시 중지될 수 있다.

Rolling upgrade는 maintenance/drain mode를 사용한다. 운영자가 maintenance mode에 진입하면 API server는 read-only query, 상태 조회, drain/status/recovery query를 계속 제공해야 한다. 새 mutating request를 접수하는 정책을 선택한 경우에도 Frontend는 request를 운영용 PostgreSQL에 저장하되, Planner와 Worker runtime은 resume 전까지 새 Backend side effect, 새 plan claim, 새 RM Worker execution, 새 DM Worker scheduling, 새 VolcanoJob 생성을 시작하지 않는다.

Drain은 이미 running 또는 claimed 상태인 작업을 명확한 상태로 정리하는 절차다. 가능한 작업은 정상 완료까지 기다리고, 오래 걸리거나 멈춘 작업은 timeout, stale claim, recovery-needed, unknown-after-side-effect 같은 상태로 기록한다. Drain 완료 조건은 running/claimed 작업이 없거나, 운영자가 확인할 수 있는 recovery/action-required 상태로 전환되어 더 이상 배포 중인 component 종료로 정보가 손실되지 않는 상태다.

API server, Frontend, query-serving component는 rolling update 중에도 availability를 유지하도록 복수 replica, readiness/liveness/startup probe, graceful termination, PodDisruptionBudget, `maxUnavailable=0` 또는 동등한 배포 전략을 사용해야 한다. Planner, RM Worker runtime, DM Worker runtime, Agent는 component restart 후 운영용 PostgreSQL의 lifecycle state와 lease를 기준으로 안전하게 재개해야 한다.

Schema migration은 expand-contract 원칙을 따른다. Expand 단계에서는 새 column/table/index 또는 backward-compatible metadata를 추가해 새 버전과 직전 버전 component가 동시에 동작할 수 있어야 한다. Contract 단계에서 제거성 변경이나 비호환 schema 변경을 수행해야 할 경우에는 별도 후속 배포로 분리하고, 운영자는 migration version, 적용 상태, 실패 원인, rollback 또는 재시도 기준을 확인할 수 있어야 한다.

관련 설치/운영/사용 매뉴얼은 rolling upgrade 사전 점검, maintenance mode 진입, drain 상태 확인, migration 실행 또는 확인, component rolling update, smoke test, resume, rollback 또는 재시도 절차와 API/CLI 사용 흐름을 `Documentation and Runbook Requirements`에 따라 분리해 제공해야 한다.

### Planned Shutdown and Startup

데이터센터 계획 정전, 서버실 작업, 전체 Kubernetes cluster shutdown처럼 사전 공지가 가능한 이벤트에서는 운영자가 DMS를 정보 손실 없이 내리고 다시 올릴 수 있어야 한다. Planned shutdown은 DMS 운영용 PostgreSQL에 기록된 critical lifecycle state를 기준으로 수행하며, DMS는 PostgreSQL 자체의 HA/backup/PITR 시스템을 소유하지 않지만 해당 설정과 검증 정보를 별도 설치/운영 runbook에서 확인하도록 요구한다.

Planned shutdown 절차는 먼저 maintenance mode에 진입하여 새 Backend scheduling과 새 VolcanoJob 생성을 막는다. 그 다음 drain status를 확인하고 running RM/DM 작업이 정상 완료되거나 timeout/recovery 상태로 기록될 때까지 기다린다. 운영자는 shutdown readiness check를 통해 pending/claimed/running/recovery-needed 작업, critical lifecycle state 저장 실패, PostgreSQL 연결 상태, observability/log 기록 상태, unresolved action-required issue를 확인한다.

Shutdown readiness가 충족되면 운영자는 DMS component를 정해진 순서로 종료한다. 일반 원칙은 API availability가 더 이상 필요 없는 시점에 API/Frontend를 종료하고, Planner와 Worker runtime이 새 작업을 claim하지 않는 상태를 확인한 뒤 Worker runtime과 Agent를 종료하는 것이다. 구체적인 Kubernetes scale-down 또는 rollout pause 방식은 구현 단계에서 결정하되, 종료 순서와 검증 명령은 별도 운영 runbook에 남겨야 한다.

Startup 절차는 PostgreSQL HA service와 DMS namespace 의존성이 먼저 정상인지 확인한 뒤 DMS component를 기동한다. Startup recovery check는 운영용 PostgreSQL의 request/plan/run/result, Worker lease, heartbeat, Data Management Job, VolcanoJob, resource observed state를 재조회하여 stale claim, interrupted run, recovery-needed, unknown-after-side-effect 대상을 식별한다. 복구 가능한 대상은 재claim 또는 verification flow로 전환하고, 자동 판단이 위험한 대상은 action-required issue로 운영자에게 노출한다.

### Unplanned Power Loss and Full Reboot Recovery

갑작스러운 데이터센터 정전, 서버 전체 재부팅, DMS Kubernetes cluster 전체 장애처럼 사전 drain 없이 중단되는 이벤트에서도 DMS는 운영용 PostgreSQL에 기록된 상태를 기준으로 정보 손실을 최소화하고 복구 가능해야 한다. DMS는 실행 중이던 component memory state를 신뢰하지 않고, 기동 후 운영용 PostgreSQL과 Kubernetes API, VolcanoJob 상태, filesystem/Kubernetes backend live state를 재조회한다.

Unplanned recovery의 핵심은 중단 시점의 side effect 여부를 명확히 모르는 작업을 성공으로 간주하지 않는 것이다. Backend side effect 이전으로 확인된 작업은 재시도 또는 재claim할 수 있고, side effect 이후 결과 기록이 불완전한 작업은 `RecoveryNeeded` 또는 `UnknownAfterSideEffect`로 남긴다. RM Worker runtime은 live backend verification으로 filesystem quota, permission, Kubernetes ResourceQuota 상태를 확인하고, DM Worker runtime은 Data Management Job과 VolcanoJob, output artifact, target path 상태를 확인한다.

Startup recovery check는 자동 복구 결과와 운영자 조치 대상을 모두 기록해야 한다. 자동으로 복구한 작업은 recovery result를 운영용 PostgreSQL에 기록하고, 자동 판단이 어려운 작업은 action-required query에서 severity, category, affected target, likely cause, recommended next action과 함께 조회 가능해야 한다. Observability/log용 PostgreSQL이 복구 초기에 사용 불가능한 경우에도 critical lifecycle state는 운영용 PostgreSQL에 우선 기록되어야 하며, diagnostic event는 가능한 시점에 남기거나 fallback log로 추적한다.

운영 runbook은 갑작스러운 전체 reboot 이후 확인해야 할 순서를 제공해야 한다. 최소한 PostgreSQL HA 정상화 확인, DMS component readiness 확인, startup recovery check 실행, unresolved action-required issue 조회, resource consistency check 또는 Data Management Job 상태 확인, 필요 시 maintenance resume 수행 절차를 포함해야 한다.

### Inventory Refresh

운영자는 inventory refresh 기능을 통해 cluster, storage, StorageClass, mount point, CSI driver inventory를 재수집하거나 재검증할 수 있다.

DMS는 Kubernetes API inventory와 agent report를 결합해 운영용 PostgreSQL에 저장된 `storage_name` 매핑이 실제 운영 환경과 일치하는지 검증한다.

### Default Quota Policy Update

운영자는 update의 별도 동작을 통해 type별 기본 quota policy를 변경할 수 있다. 이 문서에서 기본 quota policy 변경은 `initialize`가 아니다.

기본 quota policy는 운영용 PostgreSQL에 저장되며, 명시 quota와 기존 적용 quota가 모두 없는 요청, resource initialize, `reset_quota_to_default=true` update의 plan 생성 기준으로 사용된다. 명시 quota와 기존 적용 quota가 모두 없는 요청에서 적용 가능한 기본 quota policy가 없으면 Planner는 요청을 실패 처리한다. `{unlimited:true}`는 적용 가능한 default policy이며, quota 제한 없음이라는 의도된 운영 예외를 뜻한다.

Kubernetes namespace storage quota 기본값:

- `type=user`: namespace 전체 `requests.storage` `1TB`, PVC count `20`
- `type=project`: namespace 전체 `requests.storage` `4TB`, PVC count `200`
- `type=system`: quota 제한 없음. `{unlimited:true}` sentinel을 default policy로 저장한다.
- `type=admin`: quota 제한 없음. `{unlimited:true}` sentinel을 default policy로 저장한다.

Kubernetes 기본값에는 StorageClass별 quota를 포함하지 않는다.

Filesystem directory quota 기본값:

- `type=user`: capacity `1TB`, count `5M`
- `type=project`: capacity `1TB`, count `5M`
- `type=system`: quota 제한 없음. `{unlimited:true}` sentinel을 default policy로 저장한다.
- `type=admin`: quota 제한 없음. `{unlimited:true}` sentinel을 default policy로 저장한다.

Filesystem file count quota 기본값은 capacity `1TB`당 `5M` files 기준이다. Kubernetes PVC count quota는 file count quota가 아니므로 변경하지 않는다.

기본 quota policy update는 운영용 PostgreSQL의 policy state를 변경하는 작업이며, 기존 resource의 quota를 자동으로 변경하지 않는다.

### Resource Initialize

운영자는 `initialize` API를 통해 특정 resource의 quota desired state를 type별 기본 quota policy 기준으로 재설정할 수 있다.

Filesystem resource initialize는 `storage_name + directory_name`으로 대상을 식별한다.

Kubernetes namespace storage quota resource initialize는 `cluster_name + namespace_name`으로 대상을 식별한다. StorageClass별 desired quota가 남아 있는지 판단할 때는 운영용 PostgreSQL의 `storage_name` mapping과 여기서 derive되는 `storage_class_name`을 함께 조회한다.

Resource initialize는 의미상 `reset_quota_to_default=true` update와 같다. API surface를 별도 `initialize`로 제공하더라도 Planner와 RM Worker runtime은 동일한 quota reset 검증과 실행 원칙을 따라야 한다.

Planner는 대상 resource의 현재 type, 현재 desired state, 기본 quota policy를 조회하여 reset plan을 만든다. 대상 resource type에 기본 quota policy가 없으면 요청을 실패 처리한다. 기본 quota가 `{unlimited:true}`이면 quota 제한 없음 상태를 명시적으로 기록한다. Filesystem quota reset은 update와 마찬가지로 사용량 기반 admission check를 하지 않고, backend adapter 적용 후 quota evidence를 기록한다. Kubernetes quota reset은 ResourceQuota `status.used`와 `force=true` 정책을 따른다.

Filesystem resource initialize는 capacity quota와 count quota만 기본 policy 기준으로 재설정한다. Directory 이름, 사용자 리스트, access control, memo, 만료 시간, type은 변경하지 않는다.

Kubernetes namespace storage quota resource initialize는 namespace-wide `requests.storage` quota와 PVC count quota를 기본 policy 기준으로 재설정한다. 기본 policy에 StorageClass별 quota가 없으면 DMS-managed ResourceQuota의 StorageClass별 desired quota key를 `hard`에서 제거한다. Namespace, StorageClass mapping, memo, 만료 시간, type은 변경하지 않는다.

RM Worker runtime은 reset plan을 Backend에 적용한 뒤 Kubernetes ResourceQuota 또는 filesystem quota의 실제 상태를 다시 조회하여 result와 observed state를 운영용 PostgreSQL에 기록한다.

### Expiration Sweep

운영자는 Expiration sweep API를 호출하여 DMS가 관리하는 모든 resource의 만료 여부를 평가할 수 있다.

DMS는 운영용 PostgreSQL에 저장된 `expires_at`을 기준으로 만료된 resource를 찾는다.

만료된 resource는 `block=ON` 처리 대상이 된다. DMS는 만료된 resource에 대해 직접 Backend를 호출하지 않고, resource별 `block=ON` request 또는 plan을 운영용 PostgreSQL에 기록한다.

단, 만료된 resource의 `type`이 `system` 또는 `admin`이면 `block=ON`으로 전환하지 않고 실패 또는 skip 결과로 기록한다.

RM Worker runtime은 일반 block 처리와 동일하게 filesystem resource 또는 Kubernetes namespace storage quota에 block을 적용하고 검증한다.

Expiration sweep은 다음 정보를 result로 남겨야 한다.

- 평가한 resource 수
- 만료된 resource 수
- `block=ON` request 또는 plan을 생성한 resource 목록
- 이미 block 상태라 skip한 resource 목록
- policy 또는 상태 문제로 block 전환에 실패한 resource 목록

Expiration sweep result와 이후 개별 block result는 query와 observability query로 조회 가능해야 한다.

### Request Submission

사용자는 DMS에 작업 요청을 제출한다.

Frontend는 요청을 검증한 뒤 운영용 PostgreSQL에 `request` 상태로 기록한다. 요청 접수 단계에서는 Backend를 호출하거나 실제 Kubernetes 또는 filesystem 변경을 직접 수행하지 않는다.

### Request Query

사용자는 제출한 요청의 현재 상태를 조회한다.

DMS query는 운영용 PostgreSQL에 기록된 상태를 기본 조회 기준으로 사용한다.

다만 최신 backend 상태가 필요한 query는 Kubernetes API, filesystem, storage backend를 live 조회할 수 있다. live 조회를 수행한 경우 DMS는 가능한 한 조회 결과를 운영용 PostgreSQL observed state 또는 observability/log용 PostgreSQL diagnostic event로 기록한다.

Query API는 응답 payload를 최소화하거나 특정 client view에 맞게 과도하게 가공하지 않는다. DMS는 가능한 많은 정보를 반환하고, 정보를 받은 주체가 필요한 형태로 payload를 가공한다.

DMS는 운영용 PostgreSQL에 기록된 lifecycle 상태를 기준으로 request가 `request`, `plan`, `run`, `result` 중 어느 단계에 있는지 반환한다.

Filesystem query는 운영용 PostgreSQL에 저장된 resource 상태, quota 설정, memo, agent-reported observed state를 함께 반환해야 한다. 필요한 경우 filesystem live 조회를 통해 quota, permission 상태를 갱신하고 반환할 수 있다.

Kubernetes quota query는 DMS가 관리하는 ResourceQuota뿐 아니라 namespace에 존재하는 전체 ResourceQuota에 대해 운영용 PostgreSQL에 저장된 inventory, observed state, effective quota 관련 경고 정보를 함께 반환해야 한다. 필요한 경우 Kubernetes API를 live 조회하여 namespace의 전체 ResourceQuota와 effective quota 경고를 계산하고 반환할 수 있다.

Effective quota 경고를 확인한 운영자가 실제 Kubernetes의 DMS-managed ResourceQuota 상태를 기준으로 DB를 갱신해야 한다고 판단하면 Kubernetes namespace storage quota DB sync from live state API를 호출한다. 반대로 DB desired quota가 맞고 실제 namespace ResourceQuota가 drift된 상태라면 기존 Kubernetes namespace storage quota update API를 사용해 DB 기준 quota를 다시 적용한다.

### Resource Consistency Check

운영자는 DMS DB에 등록된 resource가 실제 backend에 존재하는지, 그리고 DB state와 live state가 일치하는지 확인하기 위해 Resource consistency check를 호출할 수 있다.

Resource consistency check 요청은 반드시 check scope를 포함한다. Scope는 단일 Filesystem resource, 단일 Kubernetes namespace storage quota resource, `storage_name` 단위 resource set, 또는 구현에서 허용한 필터 기반 resource set일 수 있다. Scope가 없는 요청을 전체 resource scan으로 간주하지 않는다.

`storage_name` 단위 check는 해당 storage를 참조하는 DMS-managed resource들을 batch 대상으로 삼는다. Filesystem resource에서는 동일한 `storage_name` 아래 등록된 모든 filesystem resource를 확인한다. Kubernetes namespace storage quota resource에서는 `storage_class_quotas[].storage_name`이 해당 `storage_name`을 참조하는 namespace quota resource를 확인한다. StorageClass별 quota 없이 namespace-wide quota만 가진 Kubernetes resource는 `storage_name` scope의 대상이 아니며, cluster/namespace 또는 별도 Kubernetes scope로 지정해야 한다.

Filesystem resource check는 `storage_name + directory_name`으로 대상을 식별한다. RM Worker runtime은 해당 storage mapping과 mount를 기준으로 directory가 실제로 존재하는지 확인하고, owner, group, permission, ACL, quota limit, block mode, backend capability를 read-only로 조회한다. 조회 결과는 운영용 PostgreSQL의 desired/applied/observed state와 비교한다. 대용량 storage에서 비용이 큰 recursive usage scan은 이 단계의 filesystem check 대상이 아니며, Phase 12 filesystem check/sync API에는 usage collection payload field를 두지 않는다.

Kubernetes namespace storage quota check는 `cluster_name + namespace_name`으로 대상을 식별한다. RM Worker runtime은 target cluster에서 namespace와 DMS-managed ResourceQuota가 실제로 존재하는지 확인하고, ResourceQuota name, labels/annotations, `spec.hard`, `status.used`, namespace-wide quota, StorageClass-specific quota key, derived StorageClass 존재 여부를 read-only로 조회한다. 조회 결과는 운영용 PostgreSQL의 desired/applied/observed state와 비교한다.

필터 기반 check를 지원하는 경우에는 resource kind, cluster, `storage_name`, namespace, type, requester, lifecycle status, 최근 consistency check 결과 같은 필터를 사용할 수 있다. 필터 기반 check는 대상 resource set을 먼저 확정하고, 각 resource별 결과와 전체 summary를 모두 남겨야 한다.

Consistency check는 Backend를 변경하지 않는다. Check 결과가 `Drifted` 또는 `Missing`이어도 DMS는 자동으로 resource를 수정, 생성, 삭제, repair하지 않는다.

Check 결과는 `Consistent`, `Drifted`, `Missing`, `CheckFailed` 같은 범주와 함께 requested scope, resolved target resource, DB snapshot, live snapshot, diff summary, skipped field reason, worker/executor id, checked_at, diagnostic event id를 운영용 PostgreSQL에 기록한다.

운영자는 결과에 따라 후속 조치를 선택한다. Kubernetes namespace storage quota에서 DB desired state가 맞다면 update API로 ResourceQuota를 DB 기준으로 재적용하고, live state를 받아들여 DB를 갱신해야 한다면 Kubernetes namespace storage quota DB sync from live state API를 호출한다. Filesystem resource drift는 update, block, import, 또는 구현 단계에서 정의되는 별도 repair flow로 처리한다.

### Operational Query API

운영자는 장애 대응, 정기 점검, 용량 관리, 감사 대응을 위해 Operational Query API를 사용한다.

Operational Query API는 단순 resource detail 조회보다 운영자가 바로 조치 대상을 찾을 수 있는 list/detail/history 조회를 제공해야 한다. 대표 조회 대상은 현재 review/action이 필요한 unresolved issue, 특정 resource의 lifecycle history, 특정 requester의 요청 history, 만료된 resource와 만료 예정 resource, `block=ON` resource, failed 또는 recovery-needed request/run, long-running request/run, quota usage pressure, DB/live drift 후보, worker/agent health, identity mapping 문제, Data Management Job과 preview 상태, diagnostic event correlation이다.

Action-required query는 현재 DMS 운영자가 확인해야 하는 실패, 에러, 경고, drift, stale 상태를 하나의 목록으로 모은다. 대표 항목은 `AuthorizationFailed` 증가, `RecoveryNeeded`, `UnknownAfterSideEffect`, `CheckFailed`, `Drifted`, `Missing`, Worker lease stale, long-running run, repeated retry failure, Agent identity mismatch, unhealthy Worker/Agent, identity mapping `NeedsReview`, Data Management `PreflightFailed`, `TimedOut`, Volcano pod `Failed`/`Evicted`, expiration sweep skip/failure, quota pressure threshold 초과다.

각 action-required item은 severity, category, affected component/resource/job, current state, first_seen, last_seen, related request/run/job id, diagnostic event id, likely cause, recommended next action을 포함해야 한다. 이 API는 read-only이며, 항목을 자동으로 repair하거나 backend state를 변경하지 않는다.

이 API는 기본적으로 read-only이며, live backend 조회가 필요한 경우에도 Kubernetes object, filesystem state, 중앙 identity system state를 변경하지 않는다.

### Observability Query

운영자는 DMS 운영 디버깅을 위해 운영용 PostgreSQL에 기록된 critical lifecycle state와 observability/log용 PostgreSQL에 기록된 diagnostic observability event를 조회할 수 있다.

Observability query는 resource operation의 before, desired, applied, verification, result 이력과 API/Worker runtime/Agent/system monitoring 로그를 함께 조회할 수 있어야 한다.

조회 기준 예:

- resource key
- request id
- actor
- component
- operation type
- success/failure
- error category
- time range

Observability query도 가능한 많은 정보를 반환하고, 호출 주체가 필요한 형태로 payload를 가공한다.

현재 구현된 query endpoint:

- `/v1/ops/observability`: `request_id`, `actor`, `component`, `resource_key`, `event_type`, `severity`, `success`, `error_category`, `created_from`, `created_to`, `limit` 기준 diagnostic event 조회
- `/v1/ops/control-mutations`: `mutation_class`, `operation`, `actor`, `target_key`, `status`, time range 기준 Direct Control Mutation audit 조회
- `/v1/ops/control-mutations/{mutation_id}`: 단일 Direct Control Mutation audit detail 조회
- `/v1/ops/correlation`: `request_id`, `job_id`, `resource_key`, `mutation_id`, `operation`, `actor`, `target_key` 기준 lifecycle state, control mutation, diagnostic event correlation 조회
- `POST /v1/ops/observability/retention-cleanup`: diagnostic event retention 기준 삭제
- `/v1/ops/resources/filesystems`: `storage_name`, `requester_id`, `status`, `block_state`, `management_mode`, `resource_type`, `expired` 기준 filesystem resource 조회
- `/v1/ops/resources/kubernetes`: `cluster_name`, `namespace_name`, `requester_id`, `status`, `block_state`, `resource_type`, `expired` 기준 Kubernetes namespace quota resource 조회
- `/v1/policies/default-quotas`: type별 default quota policy 조회 및 갱신

현재 구현 기준 Resource Management runtime은 Kubernetes namespace quota에 대해 in-cluster Kubernetes API로 namespace와 `ResourceQuota/dms-storage-quota`를 실제 생성/patch/delete한다. Filesystem resource는 simulated success를 사용하지 않고 storage mapping의 `backend_type`별 adapter를 선택한다. CephFS는 host-mounted worker node에서 SSH 기반 executor로 directory, POSIX permission, CephFS quota xattr을 적용한다. GPFS/IBM Storage Scale은 fileset-backed directory model을 사용하며 `mmcrfileset`, `mmsetquota Device:Fileset --block Soft:Hard --files Soft:Hard`, `mmlinkfileset`, `mmlsfileset -Y`, `mmlsquota -j Fileset -v -Y Device`, `mmunlinkfileset`, `mmdelfileset` command evidence를 남긴다. quota backend capability가 없거나 command probe가 실패하는 mapping은 fail-closed하여 directory/fileset side effect를 만들지 않는다.

현재 구현 기준 storage mapping sanity는 `sanity_result.api_observed`와 `sanity_result.agent_observed`를 분리한다. `api_observed`는 API pod filesystem에서 보이는 `mount_path` 상태를 참고용으로 남기지만 `authoritative=false`이며 top-level `sanity_status`를 실패로 만들지 않는다. Authoritative inventory는 fresh Agent report를 worker role별로 병합해 계산하며, `readiness.resource_management`, `readiness.data_management`, `readiness.inventory`에 role별 상태를 기록한다. 아직 Agent inventory가 없으면 `Unknown`으로 남기고, `cluster_name`이 지정된 mapping에서 해당 cluster의 fresh Agent report가 storage mount를 보고하지 않으면 그 role readiness를 `Failed`로 둔다.

현재 구현 기준 Data Management `volcano-live` executor는 manifest artifact 생성에 그치지 않고 in-cluster Kubernetes API로 Service와 VolcanoJob을 실제 생성한다. Agent report의 effective mount/tool capability를 기준으로 candidate pool을 만들고, stale threshold를 넘은 Agent report는 제외하며, 선택된 `claimed_node_name`을 Volcano pod `nodeAffinity`에 반영한다. `scan` worker pod는 anti-affinity로 node당 worker pod 하나를 강제한다. Volcano pod는 운영자가 승인한 image 안의 configured command template으로 `https://github.com/chahwansong/mpifileutils` pinned commit에서 빌드한 실제 `dsync`, `drm`, `dscan`, `nsync`를 실행하며, command 미존재 또는 non-zero exit은 DataJob 실패로 기록한다. API caller는 `options.tool_options`로 mpifileutils 세부 option을 요청할 수 있지만, API server가 operation별 allowlist, type, 상호 배타 조건을 먼저 검증하고 raw command-line string은 거부한다. `sync`는 실제 실행 tool이 mount topology에 따라 `dsync` 또는 `nsync`로 늦게 결정되므로 `dryrun`, `delete`, `batch_files`, `contents`, `direct`, `open_noatime`, `bufsize`, `quiet`처럼 두 tool에서 같은 의미인 공통 option만 허용한다. `dscan`은 DMS가 관리하는 `{target}`, `{report}`와 검증된 `{tool_options}`로 실행되어 `dscan-report.json` artifact를 생성하고, DMS는 `scan_report_uri`와 `scan_report_available`을 job result summary에 기록한다. `sync`/`rm`은 preview와 confirm 전에는 실제 mutation을 수행하지 않고, confirm/cancel 자체도 mutation authorization을 통과해야 한다. `sync` destination이 source 자신이거나 source 하위이면 preflight에서 거부한다. Service 생성 후 VolcanoJob 생성이 실패하면 생성된 Service cleanup evidence를 `termination_summary`에 기록한다. `volcano-live` separated-role `nsync`는 source/destination role Service와 launcher pod를 생성하고, `nsync --role-mode map --role-map 0:src,1:dst`와 검증된 `{tool_options}`를 OpenMPI로 실행한다. `nsync`는 single-node fallback을 하지 않으며, API pod가 node-local source/destination path를 직접 stat하지 못하는 배포에서는 role pod runtime preflight가 source read, destination write, pod network/SSH readiness를 확인한 뒤 실행한다.

`local`과 `volcano-manifest` Data Management executor는 개발 전용이다. `volcano-manifest`는 drift를 줄이기 위해 `volcano-live`와 동일한 Volcano resource builder를 사용해 Service/VolcanoJob artifact를 생성하지만 Kubernetes API에 apply하거나 live 상태를 watch하지 않는다. SQLite/local 개발 프로필이 아닌 배포에서 development executor가 선택되면 DM worker는 `UnsafeConfiguration`으로 fail-closed된다. 운영 배포는 `DMS_DM_EXECUTOR=volcano-live`를 명시해야 하며, development executor를 비-SQLite 배포 프로필에서 의도적으로 쓰는 경우에만 `DMS_DM_ALLOW_DEVELOPMENT_EXECUTORS=true`를 설정한다.

### Data Management Sync with dsync

사용자가 `sync` 요청을 제출하면 DMS는 request와 Data Management Job을 운영용 PostgreSQL에 기록하고 `job_id`를 반환한다.

API server 또는 Planner는 request envelope, operation option allowlist, source/destination resource reference, relative path boundary, dangerous option 여부를 검증한다.

DM Worker runtime은 storage inventory, Agent report, DM Worker mount topology를 기준으로 source와 destination을 모두 mount한 healthy DM Worker candidate pool을 찾는다.

Source와 destination이 같은 DM Worker pool에 동시에 mount 가능하면 DM Worker runtime은 `dsync`를 selected tool로 기록한다.

DM Worker runtime은 candidate pool에서 preflight를 수행한다. Preflight는 source path type이 file 또는 directory인지, destination이 directory인지, requester POSIX identity가 source read와 destination write 권한을 가지는지, mount와 filesystem health가 정상인지 확인한다.

Preflight가 통과하면 `sync`는 dry-run preview phase를 수행한다. Preview result에는 예상 copy/update/delete 범위, overwrite 위험, `--delete` 같은 dangerous option 여부, selected tool, worker pool summary, TTL 만료 시각이 포함되어야 한다.

사용자가 TTL 안에 preview를 confirm하면 DM Worker runtime은 VolcanoJob을 생성한다. Volcano worker pod는 approved mpifileutils image 안에서 `dsync`를 실행하고, stdout/stderr와 report artifact를 생성한다.

DM Worker runtime은 VolcanoJob과 worker pod 상태를 감시하고, 완료 후 result summary, log URI, report URI, final status를 운영용 PostgreSQL에 기록한다.

### Data Management Sync with nsync

`sync` 요청에서 source와 destination을 동시에 mount한 DM Worker가 없지만, source-mounted DM Worker pool과 destination-mounted DM Worker pool을 각각 만들 수 있으면 DM Worker runtime은 `nsync`를 selected tool로 기록한다.

`nsync` scenario에서도 request persistence, authorization, preflight, preview, confirm, Volcano scheduling 원칙은 `dsync`와 동일하다.

차이는 worker pool 구성이다. DM Worker runtime은 source role pool과 destination role pool을 분리해 기록하고, VolcanoJob manifest 또는 동등한 scheduling 표현에 role별 node selection이 반영되도록 한다.

Preflight는 API/Planner 단계에서 storage boundary, source/destination role pool, tool capability, role별 Kubernetes node affinity 입력을 검증한다. API pod가 node-local hostPath를 직접 볼 수 없는 경우에는 source role pod에서 source read 가능 여부를, destination role pod에서 destination directory write 가능 여부를 runtime preflight로 검증하고 그 결과를 `nsync-result.json` artifact에 남긴다. Data-operation network reachability와 credential-bound endpoint 접근성도 launcher가 OpenMPI 실행 전에 pod Service DNS/IP와 SSH readiness로 확인한다.

Confirmed `nsync`는 source headless Service, destination headless Service, launcher task를 포함하는 VolcanoJob을 생성한다. Source role pod에는 source hostPath만, destination role pod에는 destination hostPath만 mount한다. Launcher는 `nsync` command template의 `{source}`, `{destination_path}`, `{role_map}`, `{tool_options}` placeholder를 확정하고, `delete`와 `dryrun`을 포함한 mpifileutils flag는 모두 검증된 `{tool_options}`로만 렌더링한다. OpenMPI host list에는 source/destination pod IP를 각각 1 rank씩 지정한다. 결과 artifact는 `volcano-manifest.json`, `volcano-services.json`, `nsync-result.json`을 포함하며, DMS job result summary에는 `service_names`, `services_manifest_uri`, `result_uri`, pod summary가 기록된다.

Source pool 또는 destination pool 중 하나라도 충분하지 않으면 요청은 preflight 또는 planning 단계에서 실패해야 하며, DMS는 실패한 pool, mount, network, credential 이유를 조회 가능하게 기록한다.

### Data Management rm

사용자가 `rm` 요청을 제출하면 DMS는 target resource와 relative path를 기준으로 Data Management Job을 생성한다.

`rm` target은 directory만 허용한다. API server 또는 Planner는 target path가 등록된 storage/resource boundary 밖으로 escape하지 않는지, option allowlist와 dangerous option 정책을 만족하는지 검증한다.

DM Worker runtime은 target directory가 mount된 healthy DM Worker candidate pool을 만들고 `drm`을 selected tool로 기록한다.

`rm`은 실제 삭제 전에 dry-run preview phase를 반드시 거친다. Preview result에는 삭제 예정 파일/디렉토리 범위, 삭제량 요약, path boundary 검증 결과, worker pool summary가 포함되어야 한다.

사용자가 TTL 안에 confirm하면 DM Worker runtime은 VolcanoJob을 생성하고, Volcano worker pod는 `drm`을 실행한다. 완료 후 DM Worker runtime은 deleted summary, error count, log URI, report URI, final status를 운영용 PostgreSQL에 기록한다.

Confirm 전에는 실제 삭제를 수행하지 않아야 하며, confirm이 만료되면 `PreviewExpired`로 종료한다.

### Data Management scan

사용자가 `scan` 요청을 제출하면 DMS는 target resource와 relative path를 기준으로 Data Management Job을 생성한다.

`scan` target은 directory만 허용한다. `scan`은 read-only operation이므로 preview와 confirm 없이 preflight 후 바로 execution phase로 진행한다.

DM Worker runtime은 target directory가 mount된 healthy DM Worker candidate pool을 만들고 `dscan`을 selected tool로 기록한다.

Volcano worker pod는 `dscan`을 실행하고 scan report artifact를 생성한다. DM Worker runtime은 파일 수, 디렉토리 수, 총 용량, error count, scan report URI, log URI를 운영용 PostgreSQL에 요약 저장한다.

`scan`이 read-only라 하더라도 path boundary, mount health, requester read permission, tool capability, timeout, pod failure handling 원칙은 `sync`/`rm`과 동일하게 적용한다.

### Data Management Authorization Failure

mTLS client certificate 검증과 token 인증을 통과한 `sync`, `rm`, `scan`, `cancel` 요청이라도 operation authorization policy가 거부하면 DMS는 `AuthorizationFailed` terminal result를 기록한다.

이 경우 DMS는 preflight job, preview phase, VolcanoJob, mpifileutils 실행을 시작하지 않는다.

Result에는 actor, `requester_id`, operation, source/destination/target resource, policy decision reason, diagnostic event id가 포함되어야 한다.

### Data Management Preflight Failure

Preflight가 실패하면 Data Management Job은 `PreflightFailed` terminal state로 종료한다.

대표 실패 원인은 path type 불일치, path boundary 위반, mount 미존재, filesystem health check timeout, requester POSIX permission 부족, identity mapping 비활성 또는 stale 상태, credential 또는 ServiceAccount 미존재, selected tool 미탑재, data-operation network reachability 실패다.

Preflight failure result에는 failed check item, worker node, source/destination/target path summary, failure reason, diagnostic event id를 포함해야 한다. 실제 mpifileutils job은 시작하지 않는다.

### Data Management Preview Expiration and Confirm

`sync`와 `rm` preview result는 기본 24시간 TTL을 가진다.

사용자가 TTL 안에 confirm하면 DMS는 같은 `job_id`의 state를 `Confirmed`로 전환하고 execution phase scheduling을 시작할 수 있다.

TTL이 지난 뒤 confirm 요청이 들어오면 DMS는 confirm을 거부하고 Data Management Job을 `PreviewExpired` terminal state로 기록한다.

Preview를 다시 수행하려면 사용자는 새 Data Management request를 제출해야 한다.

### Data Management Volcano Pod Failure

DM Worker runtime controller의 restart, lease 만료, fail-over는 운영용 PostgreSQL과 Kubernetes API의 기존 Data Management Job 및 VolcanoJob 상태를 재조회하여 회복 가능해야 한다.

그러나 이미 생성된 Volcano worker pod가 `Failed` 또는 `Evicted` 상태가 되면 해당 Data Management Job은 자동 재시도 없이 실패로 종료한다.

DM Worker runtime fail-over가 발생하더라도 실패한 Volcano worker pod를 재시작하지 않는다. 사용자가 재실행을 원하면 새 Data Management request/job을 제출한다.

Result에는 failed pod, node, phase, container exit code 또는 eviction reason, log URI, diagnostic event id를 남겨야 한다.

구현 기준으로 `volcano-live` executor는 운영 DB에 `external_namespace`, `external_job_name`, `external_job_uid`, `external_service_name`, `external_phase`, `external_pod_summary`, `termination_summary`를 기록한다. 여러 Service를 쓰는 `nsync` job은 result summary에 `service_names`와 `services_manifest_uri`를 추가로 기록한다. DM Worker runtime은 bounded watch로 VolcanoJob phase와 pod summary를 갱신하고, startup recovery check는 stale `Running` Data Management Job의 live VolcanoJob을 inspect하여 이미 terminal이면 DMS result로 복구하고 아직 active이거나 판단 실패이면 `RecoveryNeeded`로 남긴다.

### Data Management Timeout and Cancel

각 Data Management operation은 configurable warning threshold와 timeout을 가져야 한다.

Warning threshold를 넘으면 DMS는 job을 즉시 실패시키지 않고 운영자가 볼 수 있는 diagnostic event를 남긴다.

Timeout을 넘으면 DM Worker runtime은 관련 VolcanoJob을 terminate하고 Data Management Job을 `TimedOut` terminal state로 기록한다.

사용자가 `cancel`을 호출하면 DMS는 terminal state에 도달하지 않은 Data Management Job에 대해 cancel 가능 여부를 판단한다. VolcanoJob이 이미 생성된 경우 DM Worker runtime은 VolcanoJob terminate를 수행하고 `Cancelled` result에 termination evidence를 기록한다.

구현 기준으로 cancel API도 live external reference가 있으면 Kubernetes API delete를 직접 수행한다. delete 실패로 live side effect가 계속될 가능성이 있으면 cancel 완료로 기록하지 않고 오류를 반환한다.

### Filesystem Resource Creation

사용자가 filesystem resource 생성을 요청하면 DMS는 request를 운영용 PostgreSQL에 저장한다.

Planner는 `storage_name`, `directory_name`, quota, 사용자 리스트, type, 만료 시간 등을 기준으로 plan을 생성한다.

Quota가 요청에 명시되지 않으면 Planner는 quota 입력 해석 원칙에 따라 기존 적용 quota를 확인하고, 없으면 type별 기본 quota policy를 사용한다. 완전히 새로운 filesystem resource에서는 기존 적용 quota가 없으므로 기본 quota policy가 없고 명시 quota도 없으면 요청은 실패한다. `type=system`, `type=admin`의 `{unlimited:true}`는 기본 quota policy가 존재하는 상태이며, quota 제한 없음으로 계획된다.

RM Worker runtime은 Backend를 통해 storage root 아래 directory를 만들고, Linux group 기반 권한을 설정하며, filesystem별 quota adapter를 통해 capacity quota와 count quota를 적용한다.

요청에 포함된 사용자 리스트는 directory 접근 권한의 기준이다. RM Worker runtime은 Backend를 통해 해당 사용자들의 DMS identity mapping과 중앙 identity/group system의 read-only 조회 결과를 검증하고, 이미 존재하는 Linux group membership을 기준으로 directory group ownership과 permission을 적용한다. 필요한 group membership이 중앙 identity system에 존재하지 않으면 요청은 실패 또는 보류 상태로 처리한다.

RM Worker runtime은 실행 후 directory, permission, quota, 조회된 user/group membership이 요청과 일치하는지 검증하고 result를 운영용 PostgreSQL에 기록한다.

### Filesystem Resource Update

사용자가 filesystem resource update를 요청하면 DMS는 request를 운영용 PostgreSQL에 저장한다.

Planner는 대상 resource의 운영용 PostgreSQL state, agent-reported filesystem state, storage backend capability를 기준으로 update 가능 여부를 검증하고 plan을 생성한다.

Update plan은 quota 변경, 기본 quota policy 기준 quota reset, directory 이름 변경, 사용자 리스트 변경, 만료 시간 변경, memo 변경, type 변경을 포함할 수 있다.

Quota 필드가 생략된 update는 기존 quota를 유지한다. 기존 quota가 없는 resource 상태라면 Planner는 quota 입력 해석 원칙에 따라 기본 quota policy를 사용하거나 실패 처리한다.

Filesystem quota update admission은 현재 DMS desired quota를 기준으로 한다. 사용량은 quota 감소 허용 여부를 판단하는 입력으로 쓰지 않는다. 증가와 감소 모두 `force` 없이 backend adapter 실행을 시도한다. 기존 quota가 unlimited인 상태에서 finite quota를 새로 거는 요청도 사용량 admission 없이 backend apply 결과와 read-back 검증으로 판단한다.

RM Worker runtime은 Backend를 통해 필요한 변경을 적용한다. 사용자 리스트 변경은 Linux group 기반 access control desired state 변경과 filesystem metadata 변경으로 실행되며, 중앙 identity/group system의 group membership은 변경하지 않는다. quota 변경은 filesystem별 quota adapter를 통해 실행된다.

`reset_quota_to_default=true` update는 대상 filesystem resource의 type에 해당하는 기본 capacity quota와 count quota를 desired state로 설정한다. 이 동작은 directory 이름, 사용자 리스트, memo, 만료 시간, type을 변경하지 않는다.

RM Worker runtime은 적용 후 directory, permission, read-only로 조회한 group membership, quota를 다시 조회하고 observed state와 result를 운영용 PostgreSQL에 기록한다. Phase 12 filesystem quota apply 경로는 recursive usage scan을 수행하지 않는다.

### Filesystem Resource Block

사용자가 filesystem resource block을 요청하면 DMS는 request를 운영용 PostgreSQL에 저장한다.

대상 resource의 `type`이 `system` 또는 `admin`이고 요청이 `block=ON`이면 Planner는 요청을 실패 처리하고 Backend에 반영하지 않는다.

`block=ON`이면 Planner는 대상 directory의 현재 permission mode를 복구 가능한 상태로 보존하고 block plan을 생성한다. Ownership과 group membership은 block mode가 변경하지 않는 observed access-control context로 기록한다.

Filesystem block은 `readonly` mode 또는 `root-owned` mode로 실행할 수 있다.

- `readonly`: directory permission을 read-only로 변경한다.
- `root-owned`: directory ownership을 변경하지 않고 permission을 `000`으로 변경하여 일반 사용자가 사용할 수 없게 한다. 이 명칭은 root 소유권 변경이 아니라 운영 정책상 강한 접근 차단 모드를 의미한다.

이미 block 상태인 resource에 대해 block mode를 `readonly`에서 `root-owned`로, 또는 `root-owned`에서 `readonly`로 변경할 수 있다.

`block=OFF`이면 Planner는 block 이전의 원래 desired state로 복구하는 plan을 생성한다.

RM Worker runtime은 Backend를 통해 block 적용, block mode 변경, 또는 block 해제를 실행하고 permission mode를 검증한다. Ownership과 group membership은 변경 대상이 아니라 observed evidence로 함께 기록하며, result를 운영용 PostgreSQL에 저장한다.

### Existing Directory Quota Assignment

사용자가 `storage_name`과 기존 `directory_name`을 지정하여 quota 설정을 요청하면 DMS는 request를 운영용 PostgreSQL에 저장한다.

Planner는 대상 `storage_name`이 운영용 PostgreSQL에 존재하는지, `directory_name`이 안전한 directory 이름인지, directory가 해당 storage root 바로 아래에 실제로 존재하는지, 이미 DMS-managed resource로 등록되어 있는지, storage backend가 directory quota를 지원하는지 검증한다.

대상 directory가 운영용 PostgreSQL에 등록되어 있지 않으면 Planner는 quota-only managed resource로 기록하고 quota 적용 plan을 생성한다.

Quota가 요청에 명시되지 않으면 Planner는 대상 directory에 기존 적용 quota가 있는지 확인한다. 기존 적용 quota가 있으면 그 quota를 유지한 채 DMS quota-only managed resource로 추적하고, 기존 적용 quota가 없으면 type별 기본 quota policy를 사용한다. 기존 적용 quota도 없고 적용 가능한 기본 quota policy도 없으면 요청은 실패한다.

이 flow는 directory 생성, 삭제, 사용자 리스트 기반 access control을 기본적으로 수행하지 않는다. 목적은 기존 directory에 대해 capacity quota와 count quota를 적용하고 추적하는 것이다.

RM Worker runtime은 Backend를 통해 filesystem별 quota adapter를 실행하고, 적용 후 quota 상태를 다시 조회하여 result를 운영용 PostgreSQL에 기록한다.

Kubernetes namespace에서 DMS를 거치지 않고 생성된 PVC backend directory라도, 해당 directory가 storage root 바로 아래의 basename으로 안전하게 식별될 수 있고 quota capability가 있으면 이 flow의 대상이 될 수 있다.

CSI driver가 내부적으로 `volumes/csi/csi-vol-xxxx`, `.csi/pvc-xxxx` 같은 nested backend path를 사용하는 경우, 그 path는 existing directory quota assignment API의 `directory_name`으로 받을 수 없다. Nested CSI/PVC backend directory quota 관리는 추후 `storage_name + pvc_uid`, `storage_name + backend_volume_id`, 또는 adapter-resolved backend volume flow 같은 별도 설계로 다룬다.

### Import Existing Filesystem Directory

사용자가 `storage_name`과 기존 `directory_name`을 지정하여 import를 요청하면 DMS는 request를 운영용 PostgreSQL에 저장한다.

Planner는 대상 `storage_name`이 존재하는지, `directory_name`이 안전한 basename인지, directory가 storage root 바로 아래에 실제로 존재하는지, 이미 DMS-managed resource인지, quota-only managed resource인지, filesystem backend가 DMS lifecycle ownership에 필요한 capability를 제공하는지 검증한다.

RM Worker runtime은 Backend를 통해 directory의 현재 owner, group, permission, ACL 여부, quota 설정, filesystem type, backend capability를 live 조회한다.

Access control은 요청에 명시된 사용자 리스트, DMS identity mapping, 또는 기존 directory group을 중앙 identity/group system에서 read-only로 조회하여 해석한다. DMS가 사용자 리스트와 기존 group membership을 안정적으로 해석할 수 없으면 import는 실패한다.

Import가 성공하면 DMS는 final verification을 통과한 현재 filesystem 상태를 초기 desired/applied/observed state로 운영용 PostgreSQL에 기록하고, 해당 directory를 full DMS-managed filesystem resource로 전환한다.

대상이 quota-only managed resource였으면 DMS는 quota-only에서 full DMS-managed로 승격된 전환 이력을 기록한다. 대상이 운영용 PostgreSQL에 없던 unmanaged directory였으면 새 imported DMS-managed filesystem resource로 기록한다.

Import는 기본적으로 기존 permission, ownership, group membership, quota를 변경하지 않는다. 이후 변경은 일반 filesystem update flow로 처리한다.

RM Worker runtime은 import 완료 전후로 filesystem state를 다시 조회하여 final verification을 수행한다. Import 기준 상태와 최종 조회 상태가 정책상 허용되지 않게 달라졌으면 import를 success로 처리하지 않고 conflict, retryable failure, 또는 failed 상태로 기록한다.

### Kubernetes Namespace Storage Quota Creation

사용자가 Kubernetes namespace storage quota 생성을 요청하면 DMS는 request를 운영용 PostgreSQL에 저장한다.

Planner는 `cluster_name`, `namespace_name`, namespace-wide quota, optional `storage_class_quotas[]`, `allow_namespace_create` 값을 기준으로 plan을 생성한다.

Planner는 각 `storage_class_quotas[]` entry의 `storage_name` mapping에서 `storage_class_name`을 derive하고, 해당 StorageClass가 실제 cluster에 존재하는지 확인해야 한다. 요청 payload에 `storage_class_quotas[].storage_class_name`이 포함된 경우에는 mapping과 일치하는지 확인하고, 불일치하면 요청을 실패 처리한다. `storage_class_quotas[]`가 없거나 비어 있으면 namespace-wide quota만 plan에 포함할 수 있다.

Namespace-wide quota가 요청에 명시되지 않으면 Planner는 quota 입력 해석 원칙에 따라 기존 DMS-managed namespace-wide quota state를 확인하고, 없으면 type별 기본 quota policy를 사용한다. 완전히 새로운 Kubernetes namespace storage quota resource에서는 기존 DMS-managed quota가 없으므로 기본 quota policy가 없고 명시 quota도 없으면 요청은 실패한다. StorageClass별 quota는 `storage_class_quotas[]` 요청이나 별도 policy가 없으면 생성하지 않는다.

RM Worker runtime은 Backend를 통해 namespace 존재 여부, derived StorageClass 존재 여부, CSI driver 상태, DMS 전용 ResourceQuota 존재 여부를 확인한다.

필요한 경우 RM Worker runtime은 namespace를 생성하고, DMS 전용 ResourceQuota를 생성 또는 수정한다.

RM Worker runtime은 적용 후 Kubernetes API를 통해 ResourceQuota 상태를 검증하고 result를 운영용 PostgreSQL에 기록한다.

### Kubernetes Namespace Storage Quota Update

사용자가 Kubernetes namespace storage quota update를 요청하면 DMS는 request를 운영용 PostgreSQL에 저장한다.

Planner는 대상 `cluster_name`, `namespace_name`, optional `storage_class_quotas[]` mapping에서 derive된 StorageClass, DMS 관리 ResourceQuota, 현재 quota 사용량, 만료 시간 정책을 검증하고 plan을 생성한다.

Update plan은 namespace-wide quota 변경, StorageClass별 quota entry 추가/수정/제거, 기본 quota policy 기준 quota reset, 만료 시간 변경, memo 변경, type 변경을 포함할 수 있다.

Quota 필드가 생략된 update는 기존 quota를 유지한다. 기존 quota가 없는 resource 상태라면 Planner는 quota 입력 해석 원칙에 따라 기본 quota policy를 사용하거나 실패 처리한다.

Quota 감소 요청일 경우 Planner는 현재 사용량보다 작은 quota로 낮추는지 확인한다. 기본 정책은 현재 사용량보다 낮은 quota 감소를 거부하는 것이다. `force=true` 또는 `block` 처리 목적의 update인 경우에는 예외적으로 허용할 수 있다.

`reset_quota_to_default=true` update는 대상 Kubernetes namespace storage quota resource의 type에 해당하는 기본 namespace-wide `requests.storage` quota와 PVC count quota를 desired state로 설정한다. 기본 policy에 StorageClass별 quota가 없으면 DMS-managed ResourceQuota의 StorageClass별 desired quota key를 `hard`에서 제거한다. 이 동작은 namespace, StorageClass mapping, memo, 만료 시간, type을 변경하지 않는다.

RM Worker runtime은 Backend를 통해 DMS 관리 ResourceQuota를 수정한 뒤, Kubernetes API에서 `ResourceQuota.status.used`와 `ResourceQuota.status.hard`를 다시 조회한다.

RM Worker runtime은 재조회한 상태를 운영용 PostgreSQL observed state에 반영하고 result를 운영용 PostgreSQL에 기록한다.

이 update flow는 effective quota 경고 이후 DB desired quota를 Kubernetes namespace에 다시 반영하는 repair use case에도 사용한다. 이 경우 사용자는 기존 desired quota를 유지하거나 동일한 quota 값을 명시하고, RM Worker runtime은 DMS-managed ResourceQuota를 DB 기준으로 재적용한 뒤 `spec.hard`와 `status.used`를 다시 조회하여 result와 observed state를 갱신한다.

### Kubernetes Namespace Storage Quota Block

사용자가 Kubernetes namespace storage quota block을 요청하면 DMS는 request를 운영용 PostgreSQL에 저장한다.

대상 resource의 `type`이 `system` 또는 `admin`이고 요청이 `block=ON`이면 Planner는 요청을 실패 처리하고 Backend에 반영하지 않는다.

`block=ON`이면 Planner는 대상 ResourceQuota의 현재 desired quota를 복구 가능한 상태로 보존하고, DMS 관리 ResourceQuota의 모든 hard limit을 `0`으로 설정하는 plan을 생성한다.

`block=OFF`이면 Planner는 block 이전의 원래 desired quota로 복구하는 plan을 생성한다.

RM Worker runtime은 Backend를 통해 DMS 관리 ResourceQuota를 수정한 뒤, Kubernetes API에서 `ResourceQuota.status.used`와 `ResourceQuota.status.hard`를 다시 조회한다.

RM Worker runtime은 block 적용 또는 해제 결과와 observed state를 운영용 PostgreSQL에 기록한다.

## Architecture

세부 컴포넌트 구조와 내부 interface는 실제 구현 단계에서 결정한다.

확정된 아키텍처 원칙:

- 운영용 PostgreSQL은 DMS의 source of truth다.
- Observability/log용 PostgreSQL은 diagnostic observability event 저장소다.
- 운영용 PostgreSQL과 observability/log용 PostgreSQL은 DMS Kubernetes cluster 내부에 사전 준비된 HA PostgreSQL service 또는 동등한 in-cluster PostgreSQL endpoint를 사용한다.
- DMS API는 Kubernetes Ingress를 통해 외부 client에 노출된다.
- 외부 API 호출은 mTLS(client certificate 검증 포함)와 token 기반 인증을 요구한다.
- mTLS와 token 인증을 통과한 요청은 authenticated request로 받아들이며, operation 수행 허가는 actor 기반 authorization policy가 별도로 판단한다.
- API server와 query-serving component는 rolling upgrade 중에도 외부 API availability를 유지할 수 있도록 배포되어야 한다.
- Frontend는 사용자 요청 접수와 상태/result 조회를 담당한다.
- Frontend는 사용자 요청을 Backend에 직접 반영하지 않는다.
- Planner는 운영용 PostgreSQL에 저장된 request를 plan으로 변환하고 저장한다.
- Maintenance/drain mode는 운영용 PostgreSQL의 DMS control state로 표현하며, Planner와 Worker runtime은 이 상태를 기준으로 새 Backend side effect와 새 VolcanoJob 생성을 보류한다.
- Worker runtime 역할과 worker node 배치 기준은 `DMS Execution Topology and Worker Roles`의 정의를 따른다.
- RM Worker runtime은 운영용 PostgreSQL에 저장된 resource management plan을 기준으로 Backend를 통해 실행 및 검증한다.
- DM Worker runtime은 운영용 PostgreSQL에 저장된 data management plan을 기준으로 VolcanoJob을 생성/감시/종료하고, status/result를 기록한다. 실제 mpifileutils 실행은 Volcano worker pod가 수행한다.
- Backend는 실제 업무 실행과 검증에 필요한 기능을 제공한다.
- DMS server는 agent report의 agent identity와 node identity를 검증한다.
- DMS server는 agent report와 Kubernetes API inventory를 결합해 cluster/storage inventory를 관리한다.
- Filesystem별 quota 적용은 adapter 또는 strategy 형태의 확장 가능한 구조로 설계한다.
- Storage backend 정의는 template 기반으로 확장 가능해야 하며, 새로운 backend 추가가 DMS core lifecycle 변경을 요구하지 않아야 한다.
- Worker runtime claim과 resource serialization은 lease, timeout, precondition을 기반으로 하며 fail-over 가능해야 한다.
- Component restart, rolling upgrade, planned shutdown, unplanned reboot 이후에는 운영용 PostgreSQL lifecycle state와 live backend verification을 기준으로 recovery를 수행한다.
- Database schema migration은 rolling upgrade를 고려해 expand-contract 방식으로 설계하고 migration version과 상태를 추적한다.
- API server, RM Worker runtime, DM Worker runtime, Agent는 critical lifecycle state를 운영용 PostgreSQL에 기록하고, diagnostic observability event를 observability/log용 PostgreSQL에 기록한다.
- Observability/log용 PostgreSQL에 저장되는 diagnostic observability event는 필요하면 외부 log/monitoring system으로 export 가능해야 한다.
- 실행 상태와 결과는 운영용 PostgreSQL에 기록한다.

## Data Model

이 섹션은 논리 데이터 모델 원칙만 기록한다. 구체적인 DB schema, table 구조, index, migration 방식은 실제 구현 단계에서 결정한다.

확정된 데이터 모델 원칙:

- request lifecycle의 각 단계는 운영용 PostgreSQL에 표현되어야 한다.
- request, plan, run, result는 조회 가능한 형태로 저장되어야 한다.
- Direct Control Mutation은 request lifecycle과 분리된 `control_mutations` audit record로 조회 가능해야 한다.
- Data Management `ConfirmPending` 같은 job phase state는 `data_jobs`에만 저장하고, 공통 request/plan/run lifecycle은 `Blocked` 같은 공통 non-terminal state로 표현해야 한다.
- 모든 request는 `request_id`, `requester_id`, `requested_at`을 공통 필드로 가져야 한다.
- plan은 request와 연결되어 저장되어야 한다.
- run과 result는 plan 및 request와 연결되어 저장되어야 한다.
- run과 result에는 필요한 경우 실제 실행 주체를 나타내는 `worker_id` 또는 `executor_id`를 기록할 수 있어야 한다.
- run과 result에는 RM Worker runtime과 DM Worker runtime을 구분할 수 있는 worker role 또는 worker type을 기록할 수 있어야 한다.
- 실행 결과와 오류 정보는 request와 연결되어 추적 가능해야 한다.
- query 가능한 운영 상태는 운영용 PostgreSQL에 저장된 상태를 기본 기준으로 구성되어야 한다.
- query 중 backend live 조회로 얻은 observed state는 가능한 한 운영용 PostgreSQL에 기록되어야 한다.
- resource operation의 before, desired, applied, verification, result 이력은 추적 가능해야 한다.
- API 호출 이력, 성공/실패, latency, actor, request 정보, error 요약은 observability/log용 PostgreSQL에서 추적 가능해야 한다. API request 문맥의 actor는 인증된 API 호출 주체이고, component log 또는 diagnostic event 문맥의 actor는 해당 event를 발생시킨 실행 주체다. 이 중 operation correctness에 필요한 error state는 운영용 PostgreSQL에도 최소 critical lifecycle state로 기록되어야 한다.
- RM Worker runtime, DM Worker runtime, API server, Agent의 중요한 구조화 로그는 observability/log용 PostgreSQL에 기록되어 조회 가능해야 한다.
- Agent report 인증 실패와 identity mismatch는 observability/log용 PostgreSQL에 diagnostic event로 기록되어 조회 가능해야 하며, operation correctness에 영향을 주는 경우 운영용 PostgreSQL에도 critical state로 기록되어야 한다.
- DMS 운영상의 system monitoring log는 observability/log용 PostgreSQL에 기록되어 조회 가능해야 한다.
- Action-required query를 위해 unresolved issue의 category, severity, affected target, related lifecycle id, first_seen, last_seen, current state, recommended next action을 계산하거나 저장할 수 있어야 한다.
- 관찰성 데이터는 중요도와 종류별 retention policy를 적용할 수 있어야 한다.
- Critical lifecycle state와 diagnostic observability event는 구분되어야 한다.
- Critical lifecycle state 저장 실패로 인한 operation failure는 명확한 error message와 recovery 상태로 추적 가능해야 한다.
- Backend side effect 이후 critical lifecycle state 저장에 실패한 run은 recovery-needed 또는 unknown-after-side-effect 상태로 추적 가능해야 한다.
- Backend side effect 전에 worker claim, heartbeat, `Applying` 또는 `Running` state transition이 운영용 PostgreSQL에 commit되어야 한다.
- Diagnostic observability event 저장 실패는 최소한의 critical error state 또는 fallback log로 추적 가능해야 한다.
- Worker runtime claim lease, heartbeat, attempt count, retry schedule, stale run, fail-over/recovery 결과는 추적 가능해야 한다.
- deadlock, lock wait timeout, timeout, partial failure, retry exhaustion은 error category로 구분 가능해야 한다.
- resource별 request ordering, current version, precondition, conflict/stale 상태는 추적 가능해야 한다.
- DMS control state는 maintenance mode, drain state, scheduling blocked 여부, mode 변경 actor, reason, changed_at, resume condition을 추적할 수 있어야 한다.
- Direct Control Mutation은 mutation class, operation, target key, actor, before/after state, status, result/error summary, started_at, finished_at을 추적할 수 있어야 한다.
- Rolling upgrade 상태는 component version, image tag, schema migration version, rollout status, compatibility window, upgrade started_at/completed_at, failure reason을 추적할 수 있어야 한다.
- Planned shutdown/startup 상태는 shutdown readiness check 결과, drain summary, PostgreSQL HA/backup/PITR 확인 결과, component stop/start 상태, startup recovery check 결과를 추적할 수 있어야 한다.
- Unplanned recovery 결과는 stale lease, interrupted run, observed live state, recovery decision, 자동 복구 여부, action-required issue 연결 정보를 추적할 수 있어야 한다.
- Schema migration은 version, phase, expand/contract 구분, applied_at, applied_by, success/failure, rollback 또는 retry 가능 여부를 추적할 수 있어야 한다.
- `request_id`는 DMS 전체에서 unique해야 한다.
- 같은 `request_id`가 다시 들어오면 새 request로 처리하지 않고 conflict로 추적해야 하며, `request_id`만으로 payload replay나 동일 payload 재호출의 성공 응답 재사용을 의미하지 않아야 한다.
- 모든 request는 `requester_id`를 가져야 한다.
- DMS API의 `requester_id`는 API 처리 원칙에 따라 인증된 request payload에서 제공된 값을 신뢰하여 저장한다.
- Identity Mapping API는 `requester_id`, `identity_provider`, `posix_username`, UID, primary GID, supplementary group list, mapping status, verified_at, stale_at, disabled_at, verification result, mismatch reason을 운영용 PostgreSQL에 추적 가능해야 한다.
- 중앙 identity system은 read-only source이므로 DMS data model은 LDAP/SSSD write state가 아니라 DMS 내부 mapping state와 read verification result를 저장해야 한다.
- `actor`는 mTLS client certificate 또는 token에서 확인한 API 호출 주체이며, operation authorization policy의 기본 판단 주체로 추적 가능해야 한다.
- Data Management API의 `requester_id`는 authenticated request payload의 requester identity, DMS identity mapping, LDAP/SSSD UID/GID/group read result, POSIX permission 검증 결과와 함께 추적 가능해야 한다.
- `requester_id`는 request lifecycle, resource ownership, POSIX 권한 검증, audit, observability query에서 추적 가능해야 한다.
- `requester_id`는 actor와 구분해야 한다. `requester_id`는 요청 payload의 business/audit requester identity이고, actor는 인증된 API 호출 주체 또는 diagnostic observability event의 이벤트 주체다.
- AuthorizationFailed result는 actor, `requester_id`, operation, target resource, policy decision reason, diagnostic event id를 추적할 수 있어야 한다.
- `storage_name`은 DMS 전체에서 unique해야 한다.
- Filesystem resource는 `storage_name + directory_name` 조합으로 unique해야 한다.
- DMS-created filesystem resource, imported DMS-managed filesystem resource, quota-only managed existing directory는 구분되어야 한다.
- Imported DMS-managed filesystem resource는 `created_by_dms=false`이지만 `lifecycle_owner=dms`인 resource로 표현 가능해야 한다.
- quota-only managed existing directory는 directory lifecycle과 access control ownership 없이 quota desired state와 observed state를 추적해야 한다.
- quota-only managed existing directory는 import operation을 통해 full DMS-managed filesystem resource로 승격될 수 있어야 한다.
- Import transition은 이전 관리 모드, import 후 관리 모드, requester id, request id, requested_at, imported filesystem state, validation result를 추적해야 한다.
- Kubernetes StorageClass는 cluster 내부에서만 unique하므로 `cluster_name + storage_class_name` 조합으로 unique해야 한다.
- Kubernetes namespace는 cluster 내부에서 unique하며, 운영용 PostgreSQL에서는 `cluster_name + namespace_name` 조합으로 unique해야 한다.
- Kubernetes namespace storage quota resource의 DMS identity와 최종 uniqueness 기준은 `cluster_name + namespace_name`이다.
- `namespace_name` 단독 global uniqueness는 요구하지 않는다. 중복 namespace 이름이 서로 다른 cluster에 존재하는 것은 정상적인 multi-cluster 운영 상황이다.
- Kubernetes `storage_class_quotas[].storage_name`은 StorageClass별 quota entry의 primary storage input이며, `storage_class_name`은 `storage_name` mapping에서 derive해야 한다.
- Kubernetes `storage_name`과 derived `storage_class_name`은 resource identity가 아니라 namespace storage quota resource 내부의 optional quota dimension 또는 quota entry로 추적한다.
- DMS는 Kubernetes StorageClass 자체를 기본 생성 대상으로 소유하지 않으며, 운영자가 사전에 준비한 StorageClass를 `storage_name` mapping으로 등록하고 검증해 사용해야 한다.
- Storage backend template과 `storage_name` mapping은 runtime에 추가, 수정, 비활성화 가능해야 하며, version, 변경 이력, sanity check 결과, 영향 범위를 운영용 PostgreSQL과 observability event로 추적해야 한다.
- DMS 전용 Kubernetes ResourceQuota 이름은 namespace 안에서 `dms-storage-quota`로 고정한다.
- DMS 전용 Kubernetes ResourceQuota의 실제 DMS resource identity는 운영용 PostgreSQL resource key와 labels/annotations로 추적한다.
- Resource consistency check 결과는 requested scope, resolved target resource key 또는 target summary, check status, DB state snapshot, live state snapshot, diff summary, checked fields, skipped fields, worker/executor id, checked_at, diagnostic event id를 추적해야 한다.
- Resource consistency check는 명시적으로 지정된 Filesystem resource, Kubernetes namespace storage quota resource, `storage_name` 단위 resource set, 또는 filter 기반 resource set에 대해 live resource existence와 DB/live drift 여부를 추적할 수 있어야 한다.
- Kubernetes namespace storage quota DB sync from live state 결과는 live ResourceQuota spec/status, 이전 DB state, 갱신 후 DB state, warning, requester id, request id, sync timestamp를 추적해야 한다.
- DM Worker별 source/target filesystem mount, data-operation network reachability, credential, tool capability, load/capacity는 Data Management API job scheduling 입력으로 추적 가능해야 한다.
- Data Management Job은 `Pending`, `AuthorizationFailed`, `PreflightRunning`, `PreflightFailed`, `PreviewRunning`, `PreviewSucceeded`, `PreviewExpired`, `ConfirmPending`, `Confirmed`, `Scheduled`, `Running`, `Succeeded`, `Failed`, `Cancelled`, `TimedOut` 같은 상태로 추적 가능해야 한다.
- Data Management Job은 selected mpifileutils tool, tool selection reason, priority, worker pool summary, preflight result, confirm status, warning, timeout, final status를 추적 가능해야 한다.
- Data Management Job의 상세 stdout/stderr, mpifileutils report, scan report는 artifact URI로 추적 가능해야 하며, 운영용 PostgreSQL에는 상태와 요약을 저장해야 한다.
- DMS가 관리하는 resource는 만료 처리를 위해 `expires_at`을 추적할 수 있어야 한다.
- Resource create request는 future `expires_at`을 필수로 받아야 한다.
- Resource update/default-reset request는 optional `expires_at`을 받을 수 있고, 생략하면 기존 `expires_at`을 보존해야 한다.
- Filesystem import와 Kubernetes namespace quota import/adoption은 request에 `expires_at`이 있으면 그 값을 검증해 사용하고, 없으면 server-side now + 365일을 default `expires_at`으로 설정해야 한다.
- API request, DB, response의 expiry field는 `expires_at`으로 통일하고 `expiry_at`과 `clear_expires_at`은 지원하지 않는 field로 reject해야 한다.
- Expiration sweep 결과와 sweep으로 생성된 `block=ON` request 또는 plan은 추적 가능해야 한다.
- 사용자 입력의 `TB`는 decimal terabyte로 해석하며, `1TB = 10^12 bytes`다.
- 용량 quota는 운영용 PostgreSQL 내부에서 byte 단위 정수로 저장한다.
- Filesystem count quota는 운영용 PostgreSQL 내부에서 정수 file count로 저장한다.
- Kubernetes count quota는 file count가 아니라 PVC object count로 저장한다.
- type별 기본 quota policy는 운영용 PostgreSQL에 저장되어야 하며, resource kind와 type을 기준으로 조회 가능해야 한다.
- Filesystem `type=system`, `type=admin`은 quota 제한 없음 default policy를 `{unlimited:true}` sentinel로 명시적으로 표현할 수 있어야 한다.
- 기본 quota policy 변경은 initialize가 아니라 update의 별도 policy update 동작으로 추적되어야 한다.
- 기본 quota policy state와 개별 resource desired state는 구분되어야 한다.
- Quota 입력이 생략된 요청에서 사용할 기존 적용 quota state는 운영용 PostgreSQL의 desired/applied/observed quota state를 기준으로 추적 가능해야 한다.
- resource initialize 또는 `reset_quota_to_default=true` update 결과는 기존 resource의 desired quota 변경과 observed state 재조회 결과를 추적할 수 있어야 한다.
- quota 제한 없음은 별도 sentinel이나 nullable field처럼 구현 단계에서 정한 명시적 표현으로 저장되어야 한다.
- resource에는 운영자 메모를 위한 `memo` column을 둔다.
- Filesystem resource의 사용자 리스트, DMS-tracked Linux group, 중앙 identity/group system의 read-only group membership observed state, directory ownership, permission state는 추적 가능해야 한다.
- update 결과는 기존 resource의 desired state 변경과 observed state 재조회 결과를 추적할 수 있어야 한다.
- block 상태와 block mode는 resource별로 추적 가능해야 한다.
- `block=OFF` 복구를 위해 block 이전의 desired state를 추적할 수 있어야 한다.

## Open Questions

현재 남아 있는 open question은 없다.
