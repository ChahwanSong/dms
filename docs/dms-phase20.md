# DMS Phase 20: Complete Data Management Sync/Rm

이 문서는 `docs/dms-phase19.md`의 read-only `scan` runtime 위에
Data Management `sync`와 `rm`을 구현한 Phase 20의 설계, 구현 상태,
검증 기준을 기록한다.

Phase 20의 destructive operation은 즉시 실행되지 않는다. `sync`와 `rm`은
authorization, Identity Mapping/POSIX preflight, dry-run preview, explicit
confirm, preview TTL/fingerprint guard를 거친 뒤에만 실제 mpifileutils
VolcanoJob을 생성한다.

## 현재 구현 상태

2026-06-03 기준으로 Phase 20은 다음을 구현했다.

- `POST /api/v1/data-management/sync`
  - canonical `source.storage_name`/`source.path`,
    `destination.storage_name`/`destination.path`
  - legacy `storage_name`/`source_path`/`destination_path` compatibility input
  - option allowlist/type/range validation
  - source/destination overlap, absolute path, traversal reject
  - source read/traverse and destination write/create POSIX preflight
  - DM Agent evidence based node selection
  - same-node topology에서 `dsync` selection
  - dry-run preview VolcanoJob
  - explicit confirm 후 execution VolcanoJob
  - preview/execution artifact parsing and DB summary persistence
- `POST /api/v1/data-management/rm`
  - canonical `target.storage_name`/`target.path`
  - legacy `storage_name`/`target_path` compatibility input
  - `recursive=true` guard, root/artifact path reject
  - target/parent traversal and delete POSIX preflight
  - `drm` dry-run preview and confirmed execution VolcanoJob
  - target absence post-check and DB summary persistence
- Confirm/cancel/query/action-required
  - `confirm=true` required
  - confirm caller authorization boundary
  - preview TTL expiry -> `PreviewExpired`
  - preview fingerprint guard when configured
  - active VolcanoJob termination during cancel
  - `data.scan`, `data.sync`, `data.rm` action-required aggregation
- Artifact contract
  - `data_jobs.artifact_uri` stores the job base URI
  - `result_summary.preview` and `result_summary.execution` store phase child
    `summary_uri`, `stdout_uri`, `stderr_uri`, `command_uri`, fingerprint
  - DB stores summary and URIs, not full file lists
- Install/runtime config
  - sync/rm timeout, confirm fingerprint, delete guard, node-limit, nsync settings
  - pinned mpifileutils image build includes `dscan`, `dsync`, `nsync`, `drm`

Live verified in the testbed:

- `dsync` preview and confirmed execution on `cluster-a/testbed-cephfs`
- `drm` preview and confirmed execution on `cluster-a/testbed-cephfs`
- real VolcanoJob submission/monitoring and artifact parsing
- Identity Mapping/POSIX preflight and missing-identity negative path
- expired preview, confirm guard, raw option/path guard negative paths
- standalone multi-node MPI `dscan` smoke with two worker pods on two nodes

Not live Done:

- `nsync` separated-role live execution is not enabled in the current
  Kubernetes adapter path. The planner/worker selection model records `nsync`
  candidates, but the live adapter fails closed for `nsync` execution until
  the Service/role orchestration path is implemented and verified.
- Large-scale performance, partial-mutation repair, WAN policy, and object-store
  artifact backend are still out of scope.

## Phase 20 원칙

1. **Preview before mutation**
   - `sync`와 `rm`은 실제 backend mutation 전에 dry-run preview를 반드시 수행한다.
   - Preview가 성공해도 실제 data mutation은 일어나면 안 된다.
   - Preview artifact와 DB summary를 사용자/operator가 조회할 수 있어야 한다.
   - Confirm은 preview TTL 안에서만 가능하다.

2. **Confirm is a second authorization boundary**
   - Request intake authorization과 confirm authorization을 분리한다.
   - Confirm caller actor, requester_id, job_id, preview hash/summary, TTL decision을
     DB와 observability에 남긴다.
   - Confirm이 실패하면 plan을 claimable state로 열지 않는다.

3. **DMS owns commands**
   - 요청자는 image, command, raw CLI args, SSH 설정, Secret, ServiceAccount를
     지정할 수 없다.
   - DMS가 승인한 image, namespace, ServiceAccount, artifact path, command template만
     사용한다.
   - mpifileutils flags는 operation별 allowlist와 type validation을 통과한 값만
     렌더링한다.

4. **POSIX identity is still the data authorization boundary**
   - API actor와 `requester_id`는 다른 개념이다.
   - Data path 권한은 active Identity Mapping과 target node의 POSIX/SSSD evidence로
     검증한다.
   - Preview pod와 confirmed mutation pod는 같은 mapped UID/GID/supplementary groups로
     실행한다.

5. **Tool selection follows mount topology**
   - `dsync`: source와 destination을 동시에 mount한 같은 candidate node pool이 있을 때.
   - `nsync`: source-mounted pool과 destination-mounted pool을 따로 만들 수 있고,
     data-operation network/credential readiness가 충분할 때.
   - `drm`: target-mounted healthy candidate pool이 있을 때.
   - 어떤 경우에도 API pod의 local filesystem view는 authoritative evidence가 아니다.

6. **Small live verification, production-safe design**
   - Testbed 검증은 tiny directory와 one-node/low-resource setting으로 한다.
   - 구현은 testbed에 묶이지 않아야 하며 실제 datacenter Kubernetes/Volcano 환경에서
     확장 가능한 manifest/RBAC/config shape를 가져야 한다.

7. **No automatic retry after mutation starts**
   - Preview job은 실패하면 재실행 가능하지만 같은 job에서 자동 retry하지 않는다.
   - Confirmed mutation Volcano pod가 `Failed`/`Evicted`되면 Data Job은 실패로 닫는다.
   - 사용자가 재시도하려면 새 request를 제출한다.
   - Partial mutation 가능성이 있으면 result와 action-required에 명확히 남긴다.

## Phase 20 목표

Phase 20의 핵심 기능은 다음 열여섯 가지다.

1. **`sync` structured source/destination request model**
2. **`rm` structured target request model**
3. **Operation-specific option allowlist and safe CLI rendering**
4. **Operational DB persistence for preview, confirm, and mutation evidence**
5. **`sync` source/destination storage mapping and boundary validation**
6. **`rm` target directory delete boundary validation**
7. **Identity Mapping/POSIX preflight for write/delete operations**
8. **DM Agent based source/destination/target node selection**
9. **`dsync` selection and `nsync` candidate/fail-closed handling**
10. **`sync` dry-run preview with artifact parsing**
11. **`rm` dry-run preview with artifact parsing**
12. **Confirm API with TTL, actor, authorization, and preview evidence guard**
13. **Confirmed `dsync`/`drm` VolcanoJob submission and monitoring**
14. **Mutation result artifact parsing and DB summary persistence**
15. **Query/action-required/runbook/install updates**
16. **Local and live testbed verification**

구현 완료 기준:

- `POST /api/v1/data-management/sync`는 canonical structured source/destination을
  받는다.
- `POST /api/v1/data-management/rm`은 canonical structured target을 받는다.
- legacy flat fields는 compatibility input으로만 허용되고 canonical response는
  structured path model을 사용한다.
- flat field와 structured field가 동시에 들어와 다르면 422로 reject한다.
- source/destination/target path는 storage-relative path만 허용한다.
- absolute path, traversal, nested storage root escape, unsafe symlink, bind mount escape를
  backend side effect 전에 reject한다.
- unknown option 또는 raw command-line string은 backend side effect 없이 reject한다.
- `sync` destination이 source 자신이거나 source 하위이면 reject한다.
- `rm` target은 directory만 허용한다.
- `rm` target이 storage root 자체이거나 DMS-managed artifact root이면 reject한다.
- requester Identity Mapping이 `Active`가 아니면 preview도 실행하지 않는다.
- DM Agent report가 stale이거나 mount/tool/identity/credential/network evidence가
  부족하면 VolcanoJob을 만들지 않는다.
- Preview는 real dry-run VolcanoJob으로 실행되고, preview result가 DB와 artifact에
  기록된다.
- Preview 성공 job은 `ConfirmPending`이 되고 `preview_expires_at`을 갖는다.
- Confirm TTL이 지나면 job은 `PreviewExpired`로 terminal 처리된다.
- Confirm 성공 후 같은 job이 `Confirmed` -> `Scheduled` -> `Running` -> terminal state로
  진행된다.
- Confirmed mutation execution은 same-node `sync`에서는 real `dsync`,
  `rm`에서는 real `drm` VolcanoJob으로 실행된다. separated-role `nsync`는
  현재 live adapter에서 fail-closed하며 Done 범위가 아니다.
- `sync`/`rm` stdout/stderr/report/summary artifacts가
  `<DMS_DM_ARTIFACT_BASE_URI path>/<job_id>/preview/`와
  `<DMS_DM_ARTIFACT_BASE_URI path>/<job_id>/execution/` 아래에 분리 저장된다.
- DB에는 full file list가 아니라 summary와 URI만 저장한다.
- Action-required query가 preflight, preview, confirm, mutation, artifact, timeout,
  partial mutation risk를 보여준다.
- Phase 19 `scan` local and live verifier가 regression으로 계속 통과한다.

## API Surface

기존 endpoint를 유지한다.

```http
POST /api/v1/data-management/sync
POST /api/v1/data-management/rm
POST /api/v1/data-management/jobs/{job_id}:confirm
POST /api/v1/data-management/jobs/{job_id}:cancel
GET  /api/v1/operations/data-jobs
GET  /api/v1/operations/data-jobs/{job_id}
GET  /api/v1/data-management/sync/jobs/{job_id}
GET  /api/v1/data-management/rm/jobs/{job_id}
GET  /api/v1/operations/action-required
```

### Sync Request

Canonical request:

```json
{
  "requester_id": "portal:alice",
  "source": {
    "storage_name": "cephfs-a",
    "path": "project-a/input"
  },
  "destination": {
    "storage_name": "cephfs-a",
    "path": "project-a/output"
  },
  "priority": "Mid",
  "options": {
    "delete": false,
    "contents": true,
    "batch_files": 1000,
    "bufsize": 4194304
  },
  "memo": "phase20 dry-run then confirm sync"
}
```

Same-storage compatibility input:

```json
{
  "requester_id": "portal:alice",
  "storage_name": "cephfs-a",
  "source_path": "project-a/input",
  "destination_path": "project-a/output",
  "priority": "Mid",
  "options": {"contents": true}
}
```

Behavior:

- validates request and options before creating backend side effects
- persists request, plan, Data Job with normalized source/destination
- Planner verifies both source and destination storage mappings
- DM Worker selects `dsync` or `nsync`
- DM Worker runs POSIX/runtime preflight
- DM Worker creates preview dry-run VolcanoJob
- preview success creates `ConfirmPending` job
- confirm schedules actual mutation VolcanoJob
- result summary and artifact URIs are persisted

Response:

- initial response returns `request_id`, `job_id` when available, state, normalized
  source/destination, selected priority, and status query links
- preview completion is observed through query/detail API
- confirm response returns accepted state and job status link

### Rm Request

Canonical request:

```json
{
  "requester_id": "portal:alice",
  "target": {
    "storage_name": "cephfs-a",
    "path": "project-a/to-delete"
  },
  "priority": "Mid",
  "options": {
    "recursive": true
  },
  "memo": "phase20 dry-run then confirm rm"
}
```

Behavior:

- target must be a directory
- target must be storage-relative and inside the mapped storage root
- target must not be storage root itself
- target must not overlap DMS artifact base
- preview uses `drm --dryrun`
- confirm uses `drm` without `--dryrun`
- result summary records deleted/planned item counts, bytes when available,
  error count, and artifact URIs

### Confirm

```http
POST /api/v1/data-management/jobs/{job_id}:confirm
```

Recommended request body:

```json
{
  "requester_id": "portal:alice",
  "confirm": true,
  "preview_observed_hash": "sha256:...",
  "memo": "reviewed preview output"
}
```

Confirm requirements:

- job exists and operation is `data.sync` or `data.rm`
- job state is `ConfirmPending`
- `preview_expires_at` is in the future
- request body has explicit `confirm=true`
- confirm caller authenticates and passes operation authorization
- requester_id matches original job requester unless an operator override policy exists
- preview artifact hash or equivalent stable preview evidence matches when provided
- plan is reopened only after all checks pass

Failure behavior:

- expired preview -> `PreviewExpired` terminal Data Job, request/result terminal status
- wrong state -> 409, no plan reopen
- authz failure -> terminal authz result, no plan reopen
- missing preview evidence -> 409/422, no plan reopen

### Cancel

Cancel must work for:

- `Pending`
- `PreflightRunning`
- `PreviewRunning`
- `PreviewSucceeded`
- `ConfirmPending`
- `Confirmed`
- `Scheduled`
- `Running`

If a VolcanoJob or Service exists, cancel must attempt delete/terminate and record
termination evidence. If delete fails and a mutation may still be running, do not
record a clean `Cancelled` success; return error and create action-required evidence.

## Request Model and Validation

Add structured path models:

```python
class DataPathTarget(BaseModel):
    storage_name: str
    path: str

class DataPathPair(BaseModel):
    source: DataPathTarget
    destination: DataPathTarget
```

Normalization rules:

- `scan`: existing Phase 19 target normalization remains unchanged.
- `rm`: uses the same target normalization as scan, with delete-specific checks.
- `sync`: canonical fields are `source` and `destination`.
- legacy same-storage sync maps:
  - `storage_name` + `source_path` -> `source`
  - `storage_name` + `destination_path` -> `destination`
- if legacy and structured values conflict, reject.
- canonical internal payload stores:
  - `source.storage_name`
  - `source.path`
  - `destination.storage_name`
  - `destination.path`
  - `priority_label`, `priority`, `priority_input`
  - sanitized options

Path validation:

- reject empty path
- reject absolute path
- reject `..` traversal after normalization
- reject path components that escape managed root through symlink/bind mount during runtime
  preflight
- reject destination under source for sync
- reject source equals destination
- reject rm target root (`"."`, `""`, storage root equivalent)
- reject paths under DMS artifact base if artifact base shares the same filesystem

## Option Allowlist

The pinned image was inspected with:

```bash
docker run --rm dms-mpifileutils-real:dockerfile dsync --help
docker run --rm dms-mpifileutils-real:dockerfile nsync --help
docker run --rm dms-mpifileutils-real:dockerfile drm --help
```

Phase 20 should implement a conservative allowlist. DMS controls `--dryrun`
itself; request payload must not directly toggle preview vs execution.

### Sync Common Options

Allowed public options:

| Public key | Type | dsync flag | nsync flag | Notes |
| --- | --- | --- | --- | --- |
| `delete` | bool | `--delete` | `--delete` | dangerous; preview and confirm required |
| `batch_files` | int > 0 | `--batch-files N` | `--batch-files N` | bounded max required |
| `contents` | bool | `--contents` | `--contents` | may be expensive |
| `direct` | bool | `--direct` | `--direct` | only if storage mapping allows direct IO |
| `open_noatime` | bool | `--open-noatime` | `--open-noatime` | only if mapped UID has permission |
| `bufsize` | int > 0 | `--bufsize SIZE` | `--bufsize SIZE` | bounded min/max |
| `quiet` | bool | `--quiet` | `--quiet` | logs still stored |

Reserved/generated by DMS:

- `dryrun`
- `role_mode`
- `role_map`
- source/destination paths
- report/log/artifact paths
- OpenMPI host list
- namespace/service names

Reject in Phase 20 unless explicitly implemented with additional safety:

- `xattrs`
- `chunksize`
- `dereference`
- `no_dereference`
- `link_dest`
- `sparse`
- `progress`
- `verbose`
- `trace`
- arbitrary `tool_options`
- raw command-line strings

### Rm Options

Allowed public options:

| Public key | Type | drm flag | Notes |
| --- | --- | --- | --- |
| `recursive` | bool | implicit | Must be true for directory rm request |
| `stat` | bool | `--stat` | default true for preview summary when affordable |
| `lite` | bool | `--lite` | mutually exclusive with `stat` |
| `quiet` | bool | `--quiet` | logs still stored |

Reserved/generated by DMS:

- `dryrun`
- target path
- input/output report path
- artifact paths

Reject in Phase 20 unless additional path-filter policy is designed:

- `input`
- `output`
- `text`
- `exclude`
- `match`
- `name`
- `aggressive`
- `traceless`
- `progress`
- `verbose`
- raw command-line strings

`drm --aggressive` must always be rejected in Phase 20 because it is incompatible
with dry-run semantics and raises partial-deletion risk.

## Planner Requirements

Planner must:

- keep Phase 18 maintenance/drain/scheduling-blocked intake behavior
- create Data Job plans for validated `sync`/`rm` public intake
- continue creating scan plans unchanged
- store normalized source/destination/target in desired state
- verify referenced storage mappings exist and are not disabled
- verify DM readiness for all involved mappings
- reject `sync` if source/destination mappings are missing, failed, unknown, or disabled
- reject `rm` if target mapping is missing, failed, unknown, or disabled
- create resource keys that avoid collisions:
  - `data.sync:<source_storage>:<source_path>:<dest_storage>:<dest_path>:<option_fingerprint>`
  - `data.rm:<target_storage>:<target_path>:<option_fingerprint>`
- preserve active-work conflict semantics for the same resource key
- set execution metadata phase to `preview` for new `sync`/`rm` jobs
- not set execution metadata phase to `execution` before confirm

Planner must not:

- choose `dsync`/`nsync` solely from storage names
- trust API-local path existence
- create mutation plans when option validation fails
- create plans for raw command-line options

## DM Worker Runtime Requirements

### Common Flow

For `sync` and `rm`:

1. Claim DM plan.
2. Move Data Job to `PreflightRunning`.
3. Run identity and agent inventory preflight.
4. Run Kubernetes/runtime POSIX preflight Pod(s).
5. On failure, record `PreflightFailed`, no preview job.
6. Move to `PreviewRunning`.
7. Submit dry-run preview VolcanoJob.
8. Monitor preview job.
9. Parse preview artifacts.
10. Move to `PreviewSucceeded` then `ConfirmPending`.
11. Set `preview_expires_at`.
12. Mark common plan/run/request blocked while waiting for confirm.
13. After confirm, claim plan again in execution phase.
14. Move to `Confirmed`/`Scheduled`/`Running`.
15. Submit execution VolcanoJob.
16. Monitor terminal state.
17. Parse execution artifacts.
18. Mark final terminal state.

Run heartbeat must continue during preview and execution waits.

### Sync Preflight

Identity:

- active mapping for requester
- UID/GID/supplementary groups recorded
- node POSIX identity evidence exists for source and destination pools

Source:

- path exists
- source is file or directory
- requester can read source
- requester can traverse all parent directories
- if directory, requester can execute/traverse directory

Destination:

- destination parent exists or can be created by requester
- requester can write/execute destination parent
- if destination exists, requester has write/update permissions as required
- destination is not source or source child
- destination does not overlap artifact path

Mount/tool:

- `dsync` candidate needs source and destination mount on the same node
- `nsync` source role needs source mount and `nsync`
- `nsync` destination role needs destination mount and `nsync`
- network/credential evidence must be Ready for separated-role `nsync`

### Rm Preflight

Identity:

- active mapping for requester
- UID/GID/supplementary groups recorded

Target:

- target exists
- target is a directory
- target is not storage root
- target is not DMS artifact root
- requester can traverse parent directories
- requester has delete permission through parent write/execute capability
- requester can traverse/read target for preview enumeration

Mount/tool:

- target-mounted candidate has `drm`
- credential/network evidence Ready if configured

### Runtime Preflight Pods

Use the approved job image and same security context as execution:

- `runAsUser`: mapped UID
- `runAsGroup`: mapped primary GID
- supplementary groups from Identity Mapping
- `allowPrivilegeEscalation: false`
- no user-supplied image or command

Preflight artifacts should be stored under:

```text
<artifact_base>/<job_id>/preflight/
```

For `nsync`, preflight has at least:

- source role pod/source path read check
- destination role pod/destination write check
- launcher/network/service readiness check

## Tool Selection

Tool selection algorithm:

1. Build source candidate list and destination candidate list from fresh DM Agent reports.
2. For same-node overlap, prefer `dsync` if at least one node has both mounts and `dsync`.
3. If no same-node overlap, evaluate separated-role `nsync`.
4. `nsync` requires source pool, destination pool, `nsync` tool, network evidence,
   credential evidence, and Volcano Service support.
5. If both are possible, default to `dsync` for lower orchestration complexity unless
   request/options or policy explicitly require separated-role.
6. If no tool path is possible, fail preflight before preview.

Record:

- selected tool
- selection reason
- accepted candidates
- rejected candidates with reasons
- role pools for `nsync`
- max node/rank decisions

## Volcano Adapter Requirements

Extend the Phase 19 adapter rather than adding a new backend.

### Common artifact layout

For all Phase 20 jobs:

```text
<DMS_DM_ARTIFACT_BASE_URI path>/<job_id>/
  preflight/
  preview/
    summary.json
    stdout.log
    stderr.log
    tool-report.json or tool-report.txt
    command.json
  execution/
    summary.json
    stdout.log
    stderr.log
    tool-report.json or tool-report.txt
    command.json
```

`data_jobs.artifact_uri` stores the job base URI. `result_summary` must contain
phase-specific child URIs.

### dsync preview

Preview command shape:

```bash
dsync --dryrun <validated-options> "$source" "$destination" \
  > "$artifact/preview/stdout.log" \
  2> "$artifact/preview/stderr.log"
```

DMS must also generate `preview/summary.json` by parsing stdout/stderr and/or by
performing bounded POSIX summary checks where safe. Minimum summary:

- planned operation: `sync`
- selected tool: `dsync`
- source
- destination
- delete enabled
- dry-run true
- files considered/copied/updated/deleted when extractable
- error count
- warning list

### dsync execution

Execution command shape:

```bash
dsync <validated-options> "$source" "$destination" \
  > "$artifact/execution/stdout.log" \
  2> "$artifact/execution/stderr.log"
```

Minimum execution summary:

- selected tool
- source/destination
- delete enabled
- exit code
- error count
- post-execution check summary
- stdout/stderr URIs

### nsync preview/execution

`nsync` requires separated-role orchestration.

Manifest must include:

- source role Service
- destination role Service
- launcher task
- source role pod with source hostPath only
- destination role pod with destination hostPath only
- launcher with generated host list/role map
- role-specific node affinity
- role labels for action-required/debugging

Command shape:

```bash
nsync --role-mode map --role-map <generated-map> \
  --dryrun <validated-options> "$source" "$destination"
```

For execution, omit `--dryrun`.

Reserved `--role-mode` and `--role-map` must never be accepted from user options.

Result summary must include:

- selected tool: `nsync`
- source role pool
- destination role pool
- Service names/URIs
- launcher pod
- role pod summaries
- network readiness
- dry-run or execution summary

If Service creation succeeds but VolcanoJob creation fails, cleanup Services and
record cleanup evidence. If cleanup fails, create action-required.

### drm preview

Preview command shape:

```bash
drm --dryrun <validated-options> "$target" \
  > "$artifact/preview/stdout.log" \
  2> "$artifact/preview/stderr.log"
```

Minimum preview summary:

- planned operation: `rm`
- selected tool: `drm`
- target
- dry-run true
- planned file count when extractable
- planned directory count when extractable
- planned bytes when safely computed
- error count
- warning list

### drm execution

Execution command shape:

```bash
drm <validated-options> "$target" \
  > "$artifact/execution/stdout.log" \
  2> "$artifact/execution/stderr.log"
```

Minimum execution summary:

- selected tool: `drm`
- target
- exit code
- deleted file/directory count when extractable
- error count
- post-check target absence or remaining entries
- stdout/stderr URIs

## DB and Repository Requirements

Prefer existing JSON evidence columns where reasonable, but add migrations if
query stability or correctness requires them.

Required persisted evidence:

- normalized source/destination/target
- operation options after allowlist normalization
- selected tool and tool selection reason
- worker pool and rejected candidates
- role pools for `nsync`
- preflight result
- preview result summary
- preview artifact URIs
- preview hash or stable preview evidence fingerprint
- preview_expires_at
- confirm actor/requester/timestamp
- confirmed execution result summary
- execution artifact URIs
- Volcano job refs for preview and execution
- Service refs for nsync
- cancellation/termination summary

Recommended `result_summary` shape:

```json
{
  "operation": "data.sync",
  "selected_tool": "dsync",
  "phase": "execution",
  "preview": {
    "state": "Succeeded",
    "artifact_uri": "file:///artifacts/dms/job_x/preview",
    "summary_uri": "file:///artifacts/dms/job_x/preview/summary.json",
    "stdout_uri": "file:///artifacts/dms/job_x/preview/stdout.log",
    "stderr_uri": "file:///artifacts/dms/job_x/preview/stderr.log",
    "fingerprint": "sha256:..."
  },
  "execution": {
    "state": "Succeeded",
    "artifact_uri": "file:///artifacts/dms/job_x/execution",
    "summary_uri": "file:///artifacts/dms/job_x/execution/summary.json",
    "stdout_uri": "file:///artifacts/dms/job_x/execution/stdout.log",
    "stderr_uri": "file:///artifacts/dms/job_x/execution/stderr.log"
  },
  "summary": {
    "file_count": 3,
    "directory_count": 2,
    "total_bytes": 31,
    "error_count": 0
  }
}
```

If a separate `preview_summary` column is added, query APIs should still return a
single coherent job detail object.

## State Semantics

Expected `sync`/`rm` lifecycle:

```text
Pending
  -> PreflightRunning
  -> PreflightFailed | PreviewRunning
  -> PreviewSucceeded
  -> ConfirmPending
  -> PreviewExpired | Confirmed | Cancelled
  -> Scheduled
  -> Running
  -> Succeeded | Failed | TimedOut | Cancelled
```

Common request/plan/run lifecycle:

- While preview is waiting for confirm, common request/plan/run can be `Blocked`
  with reason `data_job_confirm_pending`.
- Confirm moves plan/request back to claimable state only after guard checks.
- Terminal Data Job must close common result.

No state transition may skip preview for `sync`/`rm`.

## Query and Action Required

Data Job list/detail must show:

- operation
- state
- requester/actor
- normalized source/destination/target
- selected tool
- tool selection reason
- priority
- worker pool
- role pools
- preflight result
- preview state/result/artifacts
- confirm status
- preview expiry
- Volcano refs
- Service refs for nsync
- execution result/artifacts
- final summary
- diagnostic event ids

Add filters where useful:

- `operation=data.sync|data.rm|data.scan`
- `state=ConfirmPending`
- `preview_expired=true`
- `selected_tool=dsync|nsync|drm|dscan`
- `requester_id`
- `storage_name`

Action-required issue types:

- `data_job_preview_failed`
- `data_job_preview_expired`
- `data_job_confirm_rejected`
- `data_job_mutation_failed`
- `data_job_partial_mutation_risk`
- `data_job_nsync_service_cleanup_failed`
- `data_job_permission_denied`
- `data_job_artifact_write_failed`
- `data_job_artifact_parse_failed`
- existing Phase 19 issue types remain valid

Action-required must represent unresolved/current operational work, not every
historical preview failure.

## Security Requirements

- API authentication/mTLS/token boundaries remain unchanged.
- mTLS protected endpoint tests must include any new sync/rm query aliases.
- Requester-controlled image/command/secret/service account is rejected.
- Raw CLI args are rejected.
- Confirm requires explicit body flag, not just POST to endpoint.
- Confirm records actor and requester separately.
- Destructive operation with `delete=true` or `rm` must never execute if preview
  evidence is missing.
- Destination/source path overlap is rejected before preview.
- Artifact base must not be under requester-private target directory unless DM Worker
  has explicit read/traverse access.
- Logs must not contain secrets.
- `drm --aggressive` is always rejected.
- `nsync` role map is generated by DMS only.

## Install and Config Updates

Update:

- `install/README.md`
- `install/CONFIGURATION.md`
- `install/RUNBOOK.md`
- `install/config/dms-runtime.env.example`
- `install/kubernetes/control-plane.yaml`
- `install/kubernetes/agent-daemonset.yaml` if new agent evidence is needed
- `install/scripts/verify-install.sh`

Config to add or formalize:

```text
DMS_DM_SYNC_PREVIEW_TIMEOUT_SECONDS=1800
DMS_DM_SYNC_EXECUTION_TIMEOUT_SECONDS=3600
DMS_DM_RM_PREVIEW_TIMEOUT_SECONDS=1800
DMS_DM_RM_EXECUTION_TIMEOUT_SECONDS=3600
DMS_DM_PREVIEW_TTL_SECONDS=86400
DMS_DM_CONFIRM_REQUIRE_PREVIEW_FINGERPRINT=true
DMS_DM_SYNC_ALLOW_DELETE=false
DMS_DM_MAX_SYNC_NODES=2
DMS_DM_MAX_RM_NODES=1
DMS_DM_NSYNC_ENABLED=true
DMS_DM_NSYNC_SERVICE_PREFIX=dms-nsync
```

Runbook must explain:

- how to identify `ConfirmPending` jobs
- how to inspect preview artifacts
- how to confirm a job
- how to let preview expire and resubmit
- how to cancel preview/running jobs
- how to inspect VolcanoJobs and nsync Services
- how to handle partial mutation risk action-required
- how to verify `sync`/`rm` are no longer unsupported in Phase 20 deployments

## Testbed Live Verification

Phase 20 verification must inspect testbed metadata before running:

```bash
cd /home/mason/workspace/dms
cat /home/mason/workspace/testbed/testbed-summary.json
cat /home/mason/workspace/testbed/testbed-info.json
cat /home/mason/workspace/testbed/TOPOLOGY.md
ssh c1-control "kubectl get nodes -o wide; kubectl -n volcano-system get deploy"
ssh c1-worker "getent passwd alice; getent group developers; findmnt -rn /mnt/testbed-cephfs -o FSTYPE,TARGET"
```

Required live scenarios:

1. Deploy DMS API/Planner/DM Agent/DM Worker with Phase 20 config.
2. Use real pinned mpifileutils job image.
3. Register storage mapping `cephfs-a` with DM readiness `Ready`.
4. Register active Identity Mapping for `alice`.
5. Create tiny source directory and destination parent under testbed CephFS.
6. Submit same-storage `sync` request.
7. Verify preview runs with `dsync --dryrun`.
8. Verify preview artifacts and `ConfirmPending`.
9. Confirm sync.
10. Verify execution VolcanoJob runs real `dsync`.
11. Verify destination files match expected content.
12. Verify Data Job `Succeeded`, artifact URIs, summary, query.
13. Submit `rm` request against tiny directory.
14. Verify preview runs with `drm --dryrun`.
15. Confirm rm.
16. Verify target directory is removed.
17. Verify Data Job `Succeeded`, artifact URIs, summary, query.
18. Negative: expired preview cannot be confirmed.
19. Negative: missing identity fails before preview VolcanoJob.
20. Negative: destination under source is rejected before preview.
21. Negative: rm storage root is rejected before preview.
22. Negative: raw options rejected.
23. Negative: confirm without explicit confirm flag rejected.
24. Regression: Phase 19 scan verifier still passes.

Optional live scenario if resource cost is acceptable:

- separated-role `nsync` using two tiny host-mounted paths on distinct worker nodes.

If testbed topology cannot safely prove `nsync`, Phase 20 can mark `nsync` as
unit/integration verified with manifest and fake role executor, but then
`docs/dms-done.md` must not call `nsync` live Done. `dsync`, `drm`, preview,
confirm, and destructive guard still must be live verified.

Resource constraints:

- Use tiny directories only.
- Do not sync broad storage roots.
- Do not rm shared testbed directories.
- Keep DM Worker replicas minimal.
- Clean up test directories and namespace.

## Required Command Evidence

Local evidence:

```bash
python3 -m compileall -q src/dms
python3 -m py_compile scripts/phase20_data_management_sync_rm.py
python3 -m pytest tests/test_phase20_data_management_sync_rm.py -q
python3 -m pytest -q
bash -n scripts/verify-phase20-testbed.sh install/scripts/*.sh
git diff --check
```

Image evidence:

```bash
docker build -f install/docker/Dockerfile.mpifileutils -t dms-mpifileutils-real:phase20 .
docker run --rm dms-mpifileutils-real:phase20 dsync --help
docker run --rm dms-mpifileutils-real:phase20 nsync --help
docker run --rm dms-mpifileutils-real:phase20 drm --help
```

Testbed evidence:

```bash
./scripts/verify-phase20-testbed.sh
```

Verification document must include:

- DMS image tag
- mpifileutils image tag/ref
- operational and observability DB names
- storage mapping and identity mapping evidence
- selected tools (`dsync`, `drm`, and `nsync` if live verified)
- preview VolcanoJob evidence
- confirm request/actor evidence
- execution VolcanoJob evidence
- artifact URIs
- source/destination/target file evidence
- query output
- action-required negative cases
- cleanup evidence

## Local Regression Tests

Minimum tests:

- sync canonical source/destination accepted
- sync legacy same-storage fields normalize to canonical model
- sync conflicting legacy/structured fields rejected
- sync absolute/path traversal rejected
- sync destination equals source rejected
- sync destination under source rejected
- rm canonical target accepted
- rm storage root rejected
- rm raw options rejected
- sync option allowlist enforces type/range
- rm option allowlist enforces type/range
- raw command-line strings rejected for sync/rm
- missing identity blocks before preview
- inactive/stale identity blocks before preview
- stale DM Agent report excluded
- dsync selected for same-node source/destination mount
- nsync selected for split source/destination role pools
- no candidate pool fails before preview
- sync source read denial fails preflight
- sync destination write denial fails preflight
- rm delete permission denial fails preflight
- preview creates dry-run VolcanoJob only
- preview success records `ConfirmPending` and `preview_expires_at`
- confirm with explicit flag opens plan
- confirm without explicit flag rejected
- confirm after TTL marks `PreviewExpired`
- confirm for wrong requester rejected unless operator policy allows
- confirmed dsync execution writes artifacts and result summary
- confirmed nsync manifest contains source/destination Services and generated role map
- confirmed drm execution writes artifacts and result summary
- Volcano failure maps to Data Job failure
- timeout terminates VolcanoJob and records timeout evidence
- cancel deletes active VolcanoJob/Services or reports action-required on cleanup failure
- action-required includes preview/mutation/partial risk issues
- requester-scoped data job query does not leak other requester jobs
- Phase 19 scan tests still pass
- mTLS protected endpoint matrix includes new endpoints

## Documentation Updates

Create or update:

- `docs/dms-phase20-verification.md`
- `docs/dms-done.md`
- `install/README.md`
- `install/CONFIGURATION.md`
- `install/RUNBOOK.md`

`docs/dms-done.md` must distinguish:

- live verified `dsync`
- live verified `drm`
- live verified `nsync`, if any
- unit/manifest-only `nsync`, if live testbed cannot prove it
- remaining gaps such as large-scale performance, object storage artifact backend,
  partial mutation repair, or WAN policy

## Out of Scope

Phase 20 does not implement:

- automatic retry/resume of partially completed mutations
- repair of partially completed sync/rm beyond action-required evidence
- arbitrary user-provided mpifileutils options outside allowlist
- arbitrary user-provided image, Secret, ServiceAccount, SSH config, or command
- broad performance benchmark
- production object-store artifact backend if file URI is enough for verification
- chmod/chown/metadata-only data management operations
- cross-site WAN transfer policy
- snapshot/rollback integration
- full generic RBAC policy language beyond explicit operation authorization hooks,
  confirm authorization, and POSIX preflight gates

## Completion Checklist

Phase 20 is complete only when all are true:

- `sync` no longer returns `unsupported_until_phase20`.
- `rm` no longer returns `unsupported_until_phase20`.
- `sync` and `rm` both require preview before mutation.
- Confirm is guarded by TTL, explicit confirm flag, authorization, and preview evidence.
- `dsync` preview and execution are live verified.
- `drm` preview and execution are live verified.
- `nsync` is either live verified or explicitly documented as not live Done with
  unit/manifest evidence only.
- Phase 19 `scan` still passes local and testbed regression.
- Query/action-required exposes preview, confirm, and mutation state.
- Install/runbook docs explain how to operate and troubleshoot Phase 20.
- `docs/dms-phase20-verification.md` and `docs/dms-done.md` contain command output
  and testbed evidence.
