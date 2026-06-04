# DMS Phase 22: Data Management MPI Resource Model and Volcano Scheduling

이 문서는 Phase 21 이후 Data Management `scan`, `rm`, `sync`의 multi-node MPI
execution과 scheduler-integrated resource model을 구현하기 위한 설계/구현 기준이다.

Status: implemented and testbed-verified for the Phase 22 success paths on 2026-06-04
KST. Phase 22 builds on the Phase 21 1 node, 1 worker pod, 1 process live path and
extends it with DB-backed resource policies, Open MPI launcher behavior, MPI metadata
artifacts, MPIJob+Volcano scheduling for `scan`, and native VolcanoJob fallback for
`dsync`, `drm`, and `nsync`. The failure/negative cases in this document are the required
testbed matrix for the next hardening pass; they are listed separately from the successful
Open MPI live verification evidence.

## Objective

Phase 22의 목표는 Data Management job을 Kubernetes/Volcano scheduling model과 정렬해
실제 datacenter Kubernetes 환경에서 운영 가능한 multi-node MPI workload로 실행하는
것이다.

Phase 22는 다음 기능을 구현한다.

1. `scan`, `rm`, same-node `dsync`, separated-role `nsync`의 job resource policy를 DB와
   API로 관리한다.
2. request는 optional resource hint를 줄 수 있고, DMS는 operation policy의 default/max로
   clamp한 effective resources를 기록한다.
3. worker node당 worker pod는 정확히 1개로 강제한다.
4. worker pod 하나 안에서는 MPI ranks/processes를 여러 개 실행할 수 있다.
5. DMS는 storage mount/tool/identity/network evidence로 eligible node set을 만들고,
   scheduler가 그 set 안에서 실제 feasible nodes를 선택하게 한다.
6. MPI Operator와 Volcano를 함께 사용해 `MPIJob` + Volcano queue/gang/priority 기준으로
   MPI workload를 실행한다.
7. `MPIJob`이 Phase 22의 role-specific scheduling 요구사항을 충족하지 못하면 native
   VolcanoJob launcher/worker backend로 전환한다.
8. 모든 job은 submitted CR YAML과 MPI scheduling/runtime metadata를 artifact로 남긴다.

## Current Baseline

Phase 21 현재 구현은 다음과 같다.

- `scan`, same-node `sync`, `rm`은 각각 1 selected node, 1 Volcano worker pod,
  1 tool process로 실행된다.
- 여러 ready candidate가 있어도 첫 번째 candidate 하나만 선택한다.
- live adapter는 selected node를 `nodeSelector` 또는 hostname affinity로 고정한다.
- `sync`에서 same-node `dsync` candidate가 없고 separated source/destination role pool만
  있으면 `nsync_live_execution_deferred`로 fail-closed한다.
- `DMS_DM_MAX_NODES`, `DMS_DM_MAX_SYNC_NODES`, `DMS_DM_MAX_RM_NODES`는 Phase 21에서
  fan-out control이 아니다.

Phase 22는 이 모델을 multi-node resource model로 확장한다.

## Decisions From Deep Interview

확정된 Phase 22 방향:

- `scan`: default 3 worker nodes, 3 processes per node.
- `rm`: default 3 worker nodes, 3 processes per node.
- same-node `dsync`: default 3 worker nodes total, 3 processes per node.
- separated-role `nsync`: default source 3 worker nodes + destination 3 worker nodes,
  total 6 worker nodes, 3 processes per node.
- max policy:
  - `scan`, `rm`, `dsync`: max 3 worker nodes, max 10 processes per node.
  - `nsync`: max source 3 worker nodes, max destination 3 worker nodes,
    max 10 processes per node.
- `node_count` means worker pod count. Launcher pod is not included in `node_count`.
- Worker node당 worker pod 1개는 scheduling-level hard requirement다.
- `processes_per_node` maps to MPI ranks/slots per worker pod.
- Request resource hint is optional.
- Resource hints are clamped to operation policy max with evidence. They are not rejected
  only because they exceed max.
- Policy scope is operation-level in Phase 22.
- Policy source of truth is DB. Env/runtime config only bootstraps default rows.
- Policy runtime update applies only to new jobs.
- Policy history table is deferred.
- Policy DB read failure is fail-closed/action-required.
- `sync`/`rm` keep preview/confirm guard. Live mutation cannot bypass preview.
- `nsync` live execution is in scope.
- MPI interface selection is automatic by default. DMS may later use policy and agent-reported
  TCP interface evidence as an override, but request payload must not accept raw MPI interface
  options.
- Phase 22 runtime and verification use Open MPI, not MPICH. DMS-generated launcher commands
  must use Open MPI `mpirun` semantics and TCP transport without accepting raw user-supplied
  MPI flags.
- DMS does not rely on host SSH. MPI Operator/image internals may use SSH if the operator image
  requires it.
- All Data Management jobs must always write MPI metadata artifacts.
- Submitted CR YAML is a mandatory artifact.
- Phase 22 live artifact backend is `file://` shared RWX path.
- Artifact base URI remains env/runtime config, currently `DMS_DM_ARTIFACT_BASE_URI`.
- Artifact retention and cleanup policy are deferred.

## Resource Policy Model

Add a DB-backed operation policy table, for example:

```text
data_management_policies
  operation                  text primary key
  default_worker_nodes       integer
  default_source_nodes       integer nullable
  default_destination_nodes  integer nullable
  max_worker_nodes           integer
  max_source_nodes           integer nullable
  max_destination_nodes      integer nullable
  default_processes_per_node integer
  max_processes_per_node     integer
  default_queue              text nullable
  default_priority_class     text nullable
  default_timeout_seconds    integer nullable
  enabled                    boolean
  updated_at                 timestamptz
  updated_by                 text
```

Operation rows:

| Operation | Default | Max | Processes |
| --- | --- | --- | --- |
| `scan` | 3 worker nodes | 3 worker nodes | default 3, max 10 per node |
| `rm` | 3 worker nodes | 3 worker nodes | default 3, max 10 per node |
| `dsync` | 3 worker nodes total | 3 worker nodes total | default 3, max 10 per node |
| `nsync` | source 3 + destination 3 | source 3 + destination 3 | default 3, max 10 per node |

Required policy APIs:

- `GET /api/v1/data-management/policies`
- `GET /api/v1/data-management/policies/{operation}`
- `PUT /api/v1/data-management/policies/{operation}`

Request resource hints:

```json
{
  "resources": {
    "node_count": 3,
    "processes_per_node": 3
  }
}
```

For separated-role `nsync`:

```json
{
  "resources": {
    "source_node_count": 3,
    "destination_node_count": 3,
    "processes_per_node": 3
  }
}
```

Request fields must not expose raw `mpirun`, scheduler, hostfile, SSH, NIC, or arbitrary
mpifileutils options.

Effective resource summary must include:

- requested resource hint
- policy row/version used
- clamped effective worker/source/destination node count
- effective processes per node
- expected total MPI ranks
- launcher pod count
- worker pod count
- clamp reasons, if any
- scheduler backend
- queue and priority class

## Scheduling Model

Phase 22 uses scheduler selection, not DMS fixed node selection, for multi-node jobs.

DMS responsibilities:

1. Read fresh DM Agent reports.
2. Filter nodes by storage mount, tool readiness, identity evidence, credential evidence,
   network evidence, and stale report exclusion.
3. Build an eligible node set for the operation.
4. Submit a CR whose worker pod template is constrained to the eligible node set.
5. Require one worker pod per selected worker node.
6. Let Volcano/Kubernetes select the actual feasible nodes based on queue, priority,
   gang scheduling, resources, taints/tolerations, and current cluster state.
7. Read back scheduled pod placement and record the actual selected nodes.

Scheduler responsibilities:

1. Admit the job only when gang requirements can be met.
2. Select actual nodes from the DMS-provided eligible set.
3. Keep the job pending/in-queue if enough eligible nodes are not currently available.
4. Respect queue and priority class.

Example:

```text
DMS worker nodes: A, B, C, D, E, F
/mnt/ceph mounted nodes: A, B, E, F
Request: scan /mnt/ceph/dir1 with 3 worker nodes, 3 processes per node
```

DMS must not arbitrarily choose `A,B,E` before scheduling. Instead it submits worker
placement constraints like:

```text
worker replicas: 3
slots/processes per worker: 3
eligible hostnames: A, B, E, F
worker anti-affinity topology: kubernetes.io/hostname
```

The scheduler then places the 3 worker pods on any feasible 3 nodes among `A,B,E,F`.
Actual selected nodes are written after scheduling, for example `A,E,F`.

The worker pod template must include:

- required node affinity: `kubernetes.io/hostname In <eligible node set>`
- required pod anti-affinity for the same data job worker role, with
  `topologyKey: kubernetes.io/hostname`
- `schedulerName: volcano` or equivalent MPI Operator scheduling integration
- DMS labels for job id, request id, operation, phase, tool, and role

The launcher pod is not counted as a worker node. It still consumes cluster resources and
must be included in gang/minAvailable calculations.

## MPI Operator And Volcano Backend

Preferred backend:

```text
MPIJob
  runPolicy.schedulingPolicy
    queue
    priorityClass
    minAvailable
    minResources, if needed
  mpiReplicaSpecs
    Launcher: replicas 1
    Worker: replicas = effective worker node count
```

`slotsPerWorker` or the MPIJob-supported equivalent must be set from
`processes_per_node`. The launcher command runs Open MPI `mpirun` and invokes the approved
mpifileutils command. Hostfiles must use Open MPI-compatible slot entries such as
`<worker-pod-ip> slots=<processes_per_node>` instead of MPICH/Hydra-specific repeated-host
or `-launcher ssh` syntax.

The first implementation gate is mandatory:

1. Install/verify MPI Operator with Volcano gang scheduling enabled in the testbed.
2. Verify `MPIJob` CRD exists.
3. Submit a minimal MPIJob with Volcano queue/priority/gang scheduling.
4. Confirm the generated PodGroup and pods carry the expected queue/priority/gang semantics.
5. Confirm worker template node affinity and pod anti-affinity are preserved.
6. Confirm MPI ranks run over TCP without host SSH dependency from DMS.

If this gate fails, or if `MPIJob` cannot express the role-specific worker templates needed
for `nsync`, Phase 22 must switch to native VolcanoJob launcher/worker orchestration rather
than weakening scheduling correctness.

Native VolcanoJob fallback requirements:

- Use Volcano queue/gang/priority directly.
- Create explicit launcher, worker, source-worker, and destination-worker tasks as needed.
- Preserve the same artifact, DB summary, identity, preview/confirm, and scheduler evidence
  contracts.
- Keep worker node당 worker pod 1개 through task-level affinity/anti-affinity.

## Operation Semantics

### Scan

`scan` runs `dscan` as an MPI workload.

- Eligible nodes must have the target storage mounted and `dscan` ready.
- Runtime preflight must validate requester identity and POSIX read/traverse access.
- Worker replicas default to 3 and are clamped by policy and eligible node availability.
- `processes_per_node` defaults to 3.
- The report is parsed into DB summary and query-visible fields.

### RM

`rm` runs `drm` as an MPI workload with the existing preview/confirm guard.

- Preview uses dry-run mode.
- Execution requires explicit confirm and preview fingerprint/TTL guard.
- Eligible nodes must have the target storage mounted and `drm` ready.
- Runtime preflight must validate target read/traverse and parent write/execute permission.
- Worker replicas default to 3.
- Partial mutation retry/resume policy is deferred.

### Same-Node Dsync

Same-node `dsync` is selected when source and destination are both mounted on the same
eligible node set.

- Eligible nodes must have both source and destination mounts and `dsync` ready.
- Preview uses dry-run mode.
- Execution requires explicit confirm and preview fingerprint/TTL guard.
- Worker replicas default to 3 total.
- Source/destination path boundary guards from Phase 20 remain mandatory.

### Separated-Role Nsync

Separated-role `nsync` is selected when same-node `dsync` is not possible and separate
source/destination role pools are available.

- Source eligible nodes must have source storage mounted and `nsync` ready.
- Destination eligible nodes must have destination storage mounted and `nsync` ready.
- Defaults are source 3 worker nodes and destination 3 worker nodes.
- Role-specific worker placement is mandatory.
- `nsync` live execution is in scope, but it must pass the MPI Operator role-template gate.
  If MPIJob cannot model separate source and destination worker pools under one gang, native
  VolcanoJob is required.

## Candidate Shortage Policy

Phase 22 should distinguish request max clamping from actual eligible node shortage.

- If request exceeds policy max, clamp to max and record `resource_hint_clamped`.
- If fewer eligible nodes exist than the effective default/requested count, the scheduler
  should wait only when the shortage is due to current resource availability.
- If DMS inventory shows fewer eligible mounted nodes than required, fail before CR submission
  with action-required evidence unless the policy explicitly allows inventory down-clamp.

Initial Phase 22 policy should be strict:

- `required_worker_nodes` must be satisfied by eligible inventory.
- `required_source_nodes` and `required_destination_nodes` must be satisfied for `nsync`.
- Testbed verification may use a smaller explicit policy row for resource efficiency, but the
  production default documented above remains 3/3.

## Artifact Contract

`data_jobs.artifact_uri` remains the job base URI:

```text
<DMS_DM_ARTIFACT_BASE_URI>/<job_id>/
```

Phase 22 live scope supports `file://` artifact base on a shared RWX path visible to launcher,
worker pods, and DM Worker. Object store artifact backend is deferred.

Every job must write:

```text
<artifact_base>/<job_id>/
  mpi/
    submitted.yaml
    launch.json
    workers.json
    scheduler.json
    mpirun.json
```

For `scan`:

```text
<artifact_base>/<job_id>/
  dscan-report.json
  summary.json
  stdout.log
  stderr.log
  mpi/
    submitted.yaml
    launch.json
    workers.json
    scheduler.json
    mpirun.json
```

For `sync`/`rm`:

```text
<artifact_base>/<job_id>/preview/
  command.json
  summary.json
  stdout.log
  stderr.log
  mpi/
    submitted.yaml
    launch.json
    workers.json
    scheduler.json
    mpirun.json

<artifact_base>/<job_id>/execution/
  command.json
  summary.json
  stdout.log
  stderr.log
  mpi/
    submitted.yaml
    launch.json
    workers.json
    scheduler.json
    mpirun.json
```

Metadata requirements:

- `submitted.yaml`: exact submitted MPIJob or VolcanoJob YAML.
- `launch.json`: launcher pod name, image, command summary, queue, priority, phase.
- `workers.json`: worker pods, assigned nodes, role, rank slots, phase, container status.
- `scheduler.json`: backend, schedulerName, queue, priorityClass, gang/minAvailable,
  PodGroup ref/status if available, pending reasons.
- `mpirun.json`: process count, processes per node, rank mapping, interface mode, exit code,
  start/end timestamps.

DB stores artifact URIs and parsed summaries, not full artifact file contents.

## Authorization And Preflight

Phase 22 preserves Phase 19/20/21 authorization boundaries.

- API authentication is required.
- Active Identity Mapping is required.
- Mapped POSIX UID/GID/groups are used for preflight and runtime pods.
- API-local filesystem visibility is never authority.
- DM Agent reports are the source of storage mount/tool/network/identity evidence.
- Stale reports are excluded.
- Runtime preflight must run on scheduler-eligible nodes or in a scheduling shape that proves
  the mapped identity can access the mounted path on the target node set.
- Mutation jobs must never skip preview/confirm.

The launcher and worker pods should run with the mapped POSIX identity where possible. If the
operator requires a different launcher security context, workers must still execute data
access under the mapped POSIX identity and the exception must be recorded in metadata.

## Query And Action Required

Data job query must expose:

- effective resource model
- eligible node set
- scheduled node set
- launcher pod summary
- worker pod summary
- queue/priority/gang status
- submitted CR ref
- artifact URI
- MPI metadata URIs
- selected tool
- report URI and parsed summary
- clamp reasons
- pending/failure/action-required reason

Action-required cases include:

- missing policy row and bootstrap failed
- policy DB read failure
- no fresh DM Agent reports
- insufficient eligible mounted nodes
- missing required mpifileutils tool
- missing active Identity Mapping
- POSIX preflight failure
- MPI Operator not installed or unusable
- MPIJob/Volcano integration gate failed
- scheduler timeout
- artifact write/parse failure
- `nsync` role-specific scheduling unsupported by selected backend

## Install And Testbed Prerequisites

Phase 22 requires the control/managed execution cluster to have:

- Volcano scheduler and CRDs.
- MPI Operator installed with Volcano gang scheduling enabled.
- `MPIJob` CRD available if the MPIJob backend is enabled.
- A queue and priority classes for DMS Data Management jobs.
- A ServiceAccount/RBAC that can create/read/watch/delete MPIJob, VolcanoJob, PodGroup,
  Pods, Events, and logs in the DMS namespace.
- Approved mpifileutils job image with `dscan`, `dsync`, `drm`, `nsync`, Open MPI `mpirun`,
  `ompi_info`, and MPI runtime support.
- Shared RWX artifact path for `file://` artifacts.
- DM Agent reports that include mount evidence and, in later refinement, usable TCP interface
  evidence.

If the testbed is missing MPI Operator or related packages, install them during Phase 22
verification and document the installed version and configuration in the testbed directory
according to `/home/mason/workspace/AGENTS.md`.

The install documentation must list MPI Operator + Volcano as prerequisites before enabling
Phase 22 live Data Management.

## Implementation Work Items

1. Add policy table migration and repository APIs.
2. Add policy HTTP endpoints and auth checks.
3. Add request `resources` model with strict validation and no raw MPI/scheduler options.
4. Resolve effective resources from DB policy, request hints, and operation kind.
5. Extend preflight to build eligible node sets instead of fixed selected nodes.
6. Add MPIJob adapter with Volcano schedulingPolicy support.
7. Add mandatory gate verification for MPIJob + Volcano scheduling behavior.
8. Add native VolcanoJob fallback adapter if MPIJob cannot satisfy role-specific scheduling.
9. Implement multi-node `dscan`.
10. Implement multi-node `drm` preview/execution.
11. Implement multi-node same-node `dsync` preview/execution.
12. Implement separated-role `nsync` live execution.
13. Add submitted CR YAML and MPI metadata artifact writers.
14. Parse metadata/artifact summaries into DB result summary.
15. Extend query/action-required surfaces.
16. Update install docs and runtime examples.
17. Add local regression tests for policy, resource clamping, manifests, artifact layout, and
    query/action-required.
18. Add testbed verification with resource-efficient policy overrides.

## Verification Plan

Local regression:

- request validation rejects raw MPI/scheduler fields
- resource hint clamp
- policy CRUD and bootstrap
- missing policy fail-closed
- eligible node set construction
- worker anti-affinity and node affinity manifest shape
- submitted CR YAML artifact generation
- MPI metadata summary parsing
- `sync`/`rm` preview/confirm guard regression
- `nsync` backend selection and unsupported-backend action-required

Testbed verification:

1. Verify Volcano scheduler/CRDs.
2. Install or verify MPI Operator with Volcano gang scheduling enabled.
3. Verify the approved mpifileutils image uses Open MPI (`mpirun`, `ompi_info`) and does not
   depend on MPICH/Hydra-only options.
4. Submit a minimal MPIJob and confirm PodGroup/queue/priority/gang behavior.
5. Use a shared directory or CephFS-backed path as storage.
6. Configure resource-efficient policy rows if the testbed cannot provide production defaults.
7. Run `scan` with multiple eligible nodes when available.
8. Run `rm` preview and execution on a small target.
9. Run same-node `dsync` preview and execution on a small source/destination.
10. Run separated-role `nsync` if the backend gate supports role-specific scheduling.
11. Confirm each job records submitted CR YAML and mandatory MPI metadata.
12. Confirm query surfaces actual scheduled nodes, worker counts, process counts, artifact URIs,
    and scheduler status.

Failure/negative testbed verification:

- Missing active Identity Mapping: submit `scan`, `dsync`, `nsync`, and `drm` requests as a
  requester with no active mapping. DMS must fail closed/action-required before creating any
  mpifileutils MPIJob/VolcanoJob.
- POSIX identity unavailable on eligible workers: register a mapping whose UID/GID or username
  is not reported by fresh DM Agent evidence for the mounted nodes. DMS must reject the job
  before `dscan`, `dsync`, `nsync`, or `drm` can run.
- `scan` permission denied: create a target directory that exists on the mounted storage but is
  not readable/executable by the mapped POSIX identity. Runtime preflight must fail with a
  POSIX permission reason and no `dscan` workload submission.
- `dsync` source read denied: make the source directory unreadable or not searchable by the
  mapped identity. DMS must fail before `dsync` preview/execution submission.
- `dsync` destination write denied: make the destination parent or existing destination not
  writable/searchable by the mapped identity. DMS must fail before `dsync` preview/execution
  submission.
- `nsync` source/destination role denied: independently test source read denial and destination
  write denial. DMS must fail before separated-role `nsync` submission and must not create a
  partial source-only or destination-only workload.
- `drm` delete denied: make the target readable/searchable but remove write/search permission
  from the parent, or make the target inaccessible. DMS must fail before `drm` preview/execution
  submission.
- POSIX availability mismatch across candidate nodes: allow access on one mounted node but deny
  or omit identity evidence on another required node. DMS must exclude invalid nodes and fail
  with insufficient eligible nodes if the effective policy cannot be satisfied.
- Missing mount/tool evidence: remove or stale the DM Agent report for the mount or required
  tool (`dscan`, `dsync`, `nsync`, `drm`) and confirm fail-closed handling before job
  submission.
- For every negative case, verify the query/action-required response includes the identity,
  POSIX, mount, or tool reason; `data_jobs.artifact_uri` may point to a preflight/error
  artifact, but there must be no mpifileutils output report such as `dscan-report.json` from an
  executed MPI workload.

## Testbed Verification Evidence

Latest successful Phase 22 testbed run:

- Date: 2026-06-04 KST
- verifier: `scripts/verify-phase22-testbed.sh`
- suffix: `20260604232855`
- namespace: `dms-phase22-20260604232855` (removed by verifier cleanup)
- artifact root:
  `file:///mnt/testbed-cephfs/dms-phase22-artifacts-20260604232855`
- mpifileutils image:
  `testbed-registry:5000/dms-mpifileutils:phase22-20260604231136`
- MPI runtime: Open MPI (`mpirun`, `ompi_info`), TCP transport
- testbed override: resource-efficient 2 worker nodes and 1 process per worker for the
  multi-node cases, because the Vagrant testbed has only two cluster-a Kubernetes nodes.

Verified success paths:

- `scan`: submitted as `MPIJob` with Volcano scheduling metadata; ran on `c1-control` and
  `c1-worker`; recorded `worker_pod_count=2`, `process_count=2`,
  `submitted_kind=MPIJob`, `scheduler_backend=mpi-operator`, `dscan-report.json`, and
  mandatory `mpi/submitted.yaml`, `mpi/launch.json`, `mpi/workers.json`,
  `mpi/scheduler.json`, `mpi/mpirun.json`.
- same-node `dsync` preview/execution: submitted through the native VolcanoJob fallback;
  ran on `c1-control` and `c1-worker`; recorded `worker_pod_count=2`,
  `process_count=2`, copied the test files, and wrote all mandatory MPI metadata artifacts.
- separated-role `nsync` execution: submitted through the native VolcanoJob fallback; ran
  with source/destination role workers across `c1-control` and `c1-worker`; copied the test
  files and wrote host/role-map metadata.
- `drm` preview/execution: submitted through the native VolcanoJob fallback; removed the
  small test target and wrote all mandatory MPI metadata artifacts.

The latest run confirms the Open MPI path and the scheduler/artifact plumbing. It does not
claim execution of every negative matrix case listed above.

## Non-Scope

Phase 22 does not implement:

- artifact retention/cleanup policy
- object store artifact backend
- policy history table
- user-provided raw MPI flags
- user-provided scheduler/queue override unless later explicitly allowed
- partial mutation retry/resume after failed `dsync`, `drm`, or `nsync`
- throughput benchmark or performance tuning beyond functional verification
- automatic MPI interface override from agent reports, unless needed to make TCP verification pass

## Done Criteria

Phase 22 is Done only when:

- Policy table/API exists and runtime policy updates affect new jobs.
- `scan`, `rm`, same-node `dsync`, and separated-role `nsync` have a live execution path or a
  documented backend gate failure with native VolcanoJob fallback implemented.
- Worker pod count and processes per node match effective policy.
- Worker node당 worker pod 1개 is enforced by scheduling constraints.
- DMS submits eligible node set constraints and records scheduler-selected actual nodes.
- Volcano queue/gang/priority scheduling is verified in the testbed.
- Every job writes submitted CR YAML and mandatory MPI metadata artifacts.
- Query and action-required expose resource/scheduler/artifact evidence.
- Phase 21 regression tests remain green.
