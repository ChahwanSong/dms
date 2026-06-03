# DMS Phase 19 Implementation Prompt: Complete Data Management Scan

이 문서는 `docs/dms-phase18.md` 완료 이후 열아홉 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 19의 목표는 `docs/dms-design.md`의 Data Management 설계를 현재 DMS 코드베이스에 연결해 **Data Management `scan` 기능을 end-to-end로 완성**하는 것이다.

Phase 19는 read-only `scan`의 API authentication, operation authorization, request parsing/normalization, DB persistence, Identity Mapping 기반 POSIX permission preflight, DM Agent 기반 node selection, VolcanoJob submission, VolcanoJob monitoring, dscan result parsing, artifact 기록, scan 조회 query, action-required integration까지 한 번에 닫는다. 이 중 하나라도 stub이거나 운영 DB evidence 없이 성공 처리되면 Phase 19는 완료된 것으로 보지 않는다.

Destructive data mutation은 열지 않는다. `sync`와 `rm`은 요청 모델 확장, option allowlist, identity/POSIX preflight, worker pool selection, preview/dry-run, confirm lifecycle 모두 다음 phase에서 구현한다. Phase 19는 기존 skeleton endpoint가 있더라도 `sync`/`rm`이 backend side effect, plan claim, VolcanoJob 생성으로 이어지지 않도록 명시적으로 fail-closed 상태를 유지한다.

## Phase 19 시작 전 상태

현재 구현 기준:

- Data Management API endpoint skeleton은 이미 있다.
  - `POST /api/v1/data-management/sync`
  - `POST /api/v1/data-management/rm`
  - `POST /api/v1/data-management/scan`
  - `POST /api/v1/data-management/jobs/{job_id}:confirm`
  - `POST /api/v1/data-management/jobs/{job_id}:cancel`
  - `GET /api/v1/data-management/help`
- `OperationKind.DATA_SYNC`, `DATA_RM`, `DATA_SCAN`과 `DataJobState`가 있다.
- Planner는 Data Management request를 `DATA_JOB` resource로 plan하고 `data_jobs` row를 만든다.
- `DMWorkerRuntime`은 preview/confirm state skeleton을 갖지만 `StubVolcanoAdapter`를 사용한다.
- `dms dm-worker --loop` CLI는 존재하지만 production install guide는 DM worker replica를 0으로 두라고 명시한다.
- Agent는 `dsync,nsync,drm,dscan` tool probe와 mount evidence를 보고할 수 있다.
- Phase 18에서 maintenance/drain/scheduling block, worker heartbeat, stale/recovery guard가 구현됐다.

현재 구현과 Phase 19 설계 사이의 중요한 차이:

- `DataJobRequest`는 현재 `storage_name`, `source_path`, `destination_path`, `target_path` flat field만 갖는다.
  - Phase 19는 `scan` 대상만 canonical structured `target.storage_name`/`target.path`로 정리한다.
  - `source`/`destination` structured model과 cross-storage `sync` compatibility는 다음 phase에서 다룬다.
  - 기존 tests와 API compatibility를 위해 flat `target_path`는 migration 기간 동안 유지할 수 있다.
- `DataJobRequest.priority`는 현재 integer이고 설계 문서의 public priority는 `High`, `Mid`, `Low` label이다.
  - Phase 19는 public label과 기존 integer field의 compatibility mapping을 명확히 정의해야 한다.
  - Internal scheduling은 resolved queue/priority class를 별도 evidence로 남긴다.
- `data_jobs` table은 현재 `storage_name`, `source`, `destination`, `target`, `worker_pool`, `artifact_uri`, `preview_expires_at` 중심의 최소 schema다.
  - Phase 19는 필요한 새 evidence를 전부 top-level column으로 급하게 늘리기보다, 우선 `worker_pool`, desired/applied/observed state, result verification summary, artifact URI에 구조화해 저장할 수 있다.
  - 장기적으로 query 성능이나 API 안정성이 필요한 field만 migration으로 column화한다.
- 현재 Planner의 DM branch는 하나의 `storage_name`만 보고 `_worker_pool(storage_name)`을 호출한다.
  - Phase 19는 `scan` target storage만 대상으로 worker pool을 계산한다.
  - operation별 multi-storage target set과 `sync` source/destination 독립 검증은 다음 phase 범위다.
- 현재 `StorageMappingSanityService`의 DM readiness는 mount/CSI candidate 중심이며 tool, credential, network, identity readiness를 충분히 판정하지 않는다.
  - Phase 19는 mapping readiness `Ready`만으로 실행 가능하다고 보면 안 된다.
  - DM Worker runtime 또는 새 selector가 fresh DM Agent report에서 tool/credential/network/identity evidence를 다시 필터링해야 한다.
- 현재 `confirm_data_job()`은 `ConfirmPending` job을 `Confirmed`로 바꾸고 plan을 다시 `Planned`로 열어준다.
  - Phase 19는 `sync`/`rm` job을 새로 만들지 않아야 하며, 기존 skeleton 경로로 생성된 destructive job도 confirm이 execution을 열지 않도록 fail-closed guard를 유지한다.
- 현재 `create_app()`과 `dms dm-worker` CLI는 `StubVolcanoAdapter`를 기본 사용한다.
  - Phase 19는 test stub과 live Volcano adapter selection을 분리해야 한다.
  - live DM Worker path에서 stub success가 나오면 안 된다.
- 현재 `AuthorizationPolicy`는 real RBAC가 아니라 explicit deny marker만 거부하는 skeleton이다.
  - Phase 19는 full RBAC를 구현하지 않더라도 `scan` 실행 identity와 path read preflight를 별도로 검증해야 한다.
  - destructive operation policy와 dangerous option classification은 다음 phase에서 구현한다.
- 현재 mTLS-required profile에서 Agent report를 Fresh로 저장하려면 authenticated actor가 `node:{cluster}:{node}`와 일치해야 한다.
  - 기본 mTLS actor derivation은 `mtls:<subject>`이므로, Phase 19 live verification은 기존 Agent token/header 경로를 쓰거나 agent subject-to-node actor mapping/internal auth boundary를 구현해야 한다.

아직 완료된 것으로 보면 안 되는 항목:

- `scan` request authentication/authorization이 scan 전용 정책과 연결된 상태
- `scan` structured request parsing, compatibility normalization, DB 저장
- `scan` query/detail/action-required API
- 실제 VolcanoJob create/watch/terminate
- mpifileutils image build 또는 pinned repo 기반 live execution
- Data Management POSIX permission runtime preflight
- `scan`의 실제 `dscan` execution과 artifact 기록
- `dscan` stdout/stderr/report parsing과 result summary 저장
- `sync`/`rm` request model, preflight, preview/dry-run validation
- `sync`/`rm` confirmed destructive execution

## Phase 19 원칙

1. **Read-only first**
   - Phase 19에서 live data operation은 `scan`만 성공 완료 대상으로 연다.
   - `scan`은 read-only operation이므로 preview/confirm 없이 preflight 후 실행한다.
   - `sync`/`rm`은 destructive 가능성이 있으므로 Phase 19에서 구현하지 않고 다음 phase로 넘긴다.
   - 기존 skeleton endpoint가 노출되어 있더라도 backend side effect 없이 명시적으로 unsupported/fail-closed 응답을 반환해야 한다.

2. **No fake backend success**
   - `StubVolcanoAdapter` 성공을 live Data Management 완료로 기록하면 안 된다.
   - live runtime path는 실제 Kubernetes/Volcano object 생성, Pod 상태 감시, artifact/log 수집 evidence를 남겨야 한다.
   - live adapter를 구성할 수 없으면 request/job은 fail-closed한다.

3. **DMS chooses runtime inputs**
   - 요청자는 임의 image, command line, SSH 설정, Secret, ServiceAccount를 지정할 수 없다.
   - DMS는 운영자가 승인한 mpifileutils job image, Kubernetes ServiceAccount, Secret, artifact path만 사용한다.
   - raw command-line option string을 그대로 tool에 넘기지 않는다.

4. **Agent inventory drives node selection**
   - DM Worker selection은 fresh DM Agent report와 Kubernetes node state를 기준으로 한다.
   - stale report, missing mount, missing tool, credential missing, network unreachable evidence는 candidate에서 제외한다.
   - Phase 8 이후 원칙대로 live verification은 synthetic Agent report를 사용하지 않는다.

5. **POSIX identity is the data authorization boundary**
   - API authentication actor와 `requester_id`는 다른 개념이다.
   - Data path 권한은 `requester_id`의 active Identity Mapping과 target node의 POSIX/SSSD evidence 기준으로 검증한다.
   - 가능하면 preflight와 실제 mpifileutils pod는 같은 UID/GID/supplementary group identity로 실행한다.

6. **Volcano worker pod is not the source of truth**
   - Volcano worker pod는 mpifileutils 실행과 artifact 생성을 담당한다.
   - job lifecycle source of truth는 operational PostgreSQL이다.
   - DM Worker runtime이 VolcanoJob 상태를 감시하고 `data_jobs`, `runs`, `results`, `resources`를 갱신한다.

## Phase 19 목표

Phase 19의 핵심 기능은 다음 열세 가지다.

1. **Scan API authentication and operation authorization**
2. **Structured `scan` request model and compatibility normalization**
3. **Scan option allowlist and safe CLI rendering**
4. **Operational DB persistence for normalized scan request/job/run/result**
5. **Identity Mapping 기반 POSIX permission preflight**
6. **DM Agent mount/tool/credential/network/identity inventory 기반 node selection**
7. **mpifileutils image/tool configuration and pinning metadata**
8. **VolcanoJob submission adapter**
9. **VolcanoJob monitoring, timeout, cancel, and failure mapping**
10. **Read-only `dscan` execution and result parsing**
11. **Artifact URI contract for stdout/stderr/report**
12. **Scan status/detail/list query APIs**
13. **Action-required and installation/testbed integration**

구현 완료 기준:

- `scan` 요청은 structured target object를 지원한다.
- legacy flat `target_path` field가 남아 있더라도 public canonical response와 new implementation path는 structured target model을 기준으로 한다.
- target은 `storage_name` plus storage-relative `path`로 표현한다.
- worker node absolute path, path traversal, nested storage root escape, unsafe symlink, bind mount escape를 reject한다.
- `scan` option allowlist가 있다.
- unknown option 또는 raw command-line string은 backend side effect 없이 reject한다.
- 모든 `scan` endpoint는 Phase 16 인증 경계를 통과해야 하며, actor와 requester_id를 분리해 저장한다.
- operation authorization이 실패하면 backend side effect, plan, run, VolcanoJob 없이 terminal result를 기록한다.
- normalized scan request, selected target, options, priority, actor, requester_id는 operational DB에서 조회 가능해야 한다.
- `requester_id`에 active Identity Mapping이 없거나 mapping이 `Disabled`, `NeedsReview`, `Stale`이면 Data Management job을 실행하지 않는다.
- POSIX preflight는 requester UID/GID/groups, path read/execute capability, target node identity evidence를 구조화해 기록한다.
- DM Worker candidate pool은 fresh DM Agent report에서 mount/tool/credential/network evidence가 `Ready`인 node만 포함한다.
- candidate node가 없거나 target mount, `dscan`, identity evidence, credential/network evidence가 부족하면 VolcanoJob을 만들지 않고 `PreflightFailed` 또는 planning rejection으로 기록한다.
- `scan`은 target storage를 mount하고 `dscan` tool이 ready인 candidate pool에서 VolcanoJob을 생성한다.
- VolcanoJob은 `High`, `Mid`, `Low` priority를 Volcano queue 또는 scheduling policy로 매핑한다.
- multi-node job은 node당 worker pod 하나 원칙을 강제한다.
- Phase 19 기본 live verification은 resource 절약을 위해 1 node, 1 pod, tiny directory scan으로 수행한다.
- `scan` 완료 후 file count, directory count, total bytes, error count, report URI, log URI를 operational DB에 기록한다.
- full scan report와 detailed file list는 artifact URI로 연결하고 operational DB에는 요약만 저장한다.
- Volcano worker pod `Failed`/`Evicted`는 Data Management Job 실패로 기록하고 자동 pod retry를 하지 않는다.
- timeout 시 DM Worker runtime이 VolcanoJob을 terminate하고 job을 `TimedOut` 또는 `Failed`로 기록한다.
- cancel 시 non-terminal VolcanoJob을 terminate/delete하고, DB state와 result evidence를 `Cancelled`로 닫는다.
- DM Worker restart 이후에도 DB에 남은 VolcanoJob ref를 조회해 terminal state로 수렴하거나 recovery/action-required issue를 만든다.
- `dscan` stdout/stderr/report는 parser를 통해 summary로 변환되며 parsing failure는 scan failure 또는 artifact parsing issue로 기록한다.
- `sync`/`rm` endpoint는 Phase 19에서 plan/job/VolcanoJob을 만들지 않고 명시적으로 `unsupported_until_phase20` 또는 equivalent 409/501로 막는다.
- 기존 `confirm_data_job()` skeleton은 Phase 19 scan path에서 필요하지 않으며, destructive job을 claimable 상태로 되살리지 않도록 guard해야 한다.
- `GET /api/v1/operations/data-jobs`와 job detail query가 normalized target, selected tool, worker pool, preflight result, VolcanoJob ref, artifact URI, result summary를 보여준다.
- scan 전용 status/detail query가 request id 또는 job id 기준으로 최신 lifecycle state를 반환한다.
- `GET /api/v1/operations/action-required`가 unresolved Data Management preflight failure, Volcano failure, timeout, stale/recovery job issue를 포함한다.
- 검증 결과는 `docs/dms-phase19-verification.md`와 `docs/dms-done.md`에 command/output 포함해 기록한다.

## API Surface

기존 endpoint를 유지하되 Phase 19에서 구현하는 request model은 `scan`에 한정한다.

현재 API의 `DataJobRequest`는 flat model이므로 Phase 19 구현은 다음 순서로 진행하는 것이 안전하다.

1. `scan`용 structured `target` model을 추가한다.
2. flat `storage_name`/`target_path`를 structured target으로 normalize하는 compatibility layer를 둔다.
3. internal desired state와 Data Job response는 normalized structured target model을 사용한다.
4. flat field와 structured field가 동시에 들어와 다르면 422로 reject한다.
5. `High`, `Mid`, `Low` priority label을 지원하고 기존 integer priority를 compatibility input으로 normalize한다.
6. 기존 tests가 기대하는 flat scan request path는 migration 기간 동안 유지하되, 새 tests는 structured request를 기준으로 작성한다.

### Scan

```http
POST /api/v1/data-management/scan
```

Canonical request:

```json
{
  "requester_id": "portal:alice",
  "target": {
    "storage_name": "cephfs-a",
    "path": "project-a/input"
  },
  "priority": "Mid",
  "options": {
    "summary_only": true,
    "max_depth": 8
  },
  "memo": "phase19 read-only scan"
}
```

Behavior:

- authenticates actor and applies operation authorization before creating backend side effects
- creates Data Management request, plan, and Data Job only after intake validation succeeds
- validates target path shape and option allowlist
- Planner resolves target storage mapping and DM worker pool
- DM Worker runs POSIX/mount/tool preflight
- DM Worker creates VolcanoJob for `dscan`
- DM Worker monitors VolcanoJob and records summary/artifacts

Response:

- returns `request_id`, `job_id` if already planned synchronously, `status`, `resource_key`, normalized target, selected priority, and links or ids for status query
- if planning is asynchronous and `job_id` is not known yet, the response must still include `request_id` and a query path that lets the caller find the Data Job later
- rejected authz/intake requests return an explicit error and, where the existing request lifecycle supports it, a terminal result with `backend_side_effect=false`

### Sync and Rm

```http
POST /api/v1/data-management/sync
POST /api/v1/data-management/rm
```

Phase 19 behavior:

- do not add new `sync`/`rm` request model semantics in Phase 19
- do not create Data Management plan/job rows for newly submitted `sync`/`rm`
- do not run `dsync`, `nsync`, or `drm`
- do not implement preview/dry-run lifecycle in Phase 19
- return a clear unsupported response such as `409`/`501` with reason `unsupported_until_phase20`
- leave detailed `sync`/`rm` path validation, option allowlist, preflight, preview, confirm, and execution semantics to the next phase

### Confirm

```http
POST /api/v1/data-management/jobs/{job_id}:confirm
```

Phase 19 behavior:

- `scan` does not need confirm.
- New Phase 19 `scan` jobs should not enter `ConfirmPending`.
- If old/stub `sync`/`rm` jobs already exist, confirm must not make them claimable by a live DM Worker.
- The endpoint should return 409 with a clear reason such as `unsupported_until_phase20` for `sync`/`rm` jobs.
- This requires guarding the current `confirm_data_job()` behavior. Today it marks the job `Confirmed` and reopens the plan as `Planned`; Phase 19 must not do that for destructive operations.

### Cancel

```http
POST /api/v1/data-management/jobs/{job_id}:cancel
```

Behavior:

- If no VolcanoJob exists, mark job `Cancelled`.
- If a VolcanoJob exists and job is non-terminal, terminate/delete the VolcanoJob and record cancellation evidence.
- Cancel must not delete user data directly.

### Scan Query

Phase 19 must provide enough query surface to operate `scan` without inspecting raw DB tables.

Required query support:

```http
GET /api/v1/operations/data-jobs
GET /api/v1/operations/data-jobs/{job_id}
GET /api/v1/operations/requests?requester_id={requester_id}
GET /api/v1/operations/action-required
```

If a dedicated scan endpoint is more consistent with the codebase, it may also add:

```http
GET /api/v1/data-management/scan/jobs/{job_id}
GET /api/v1/data-management/scan?requester_id={requester_id}
```

Query requirements:

- list filters include `requester_id`, `operation=data.scan`, `storage_name`, `state`, and time/limit where existing query conventions support them
- detail returns normalized target, requester id, actor, priority, selected node pool, selected tool, preflight result, VolcanoJob ref, artifact URIs, result summary, state timestamps, and diagnostic event ids
- response must be derived from operational DB state, not live Kubernetes-only reads
- live Kubernetes state may enrich the response, but stale/unavailable Kubernetes API must not hide the DB lifecycle state

## Data Request Model

Phase 19 should move away from the current single `storage_name` plus flat `target_path` model for `scan` by introducing a canonical structured target object.

Canonical target object:

```json
{
  "storage_name": "cephfs-a",
  "path": "project-a/input"
}
```

Validation rules:

- `storage_name` must exist in `storage_mappings`.
- `path` is storage-relative and must not be absolute.
- `path` must not contain `..`, NUL, empty segment, repeated slash escape, or platform-specific path separators that bypass validation.
- `path` must resolve under the storage root on candidate DM Workers.
- DMS must reject symlink or bind mount escape detected by node-local preflight.
- `scan` target must be directory.

Compatibility:

- Existing flat `storage_name`, `target_path` should remain accepted for tests and backward compatibility during Phase 19.
- Flat compatibility maps to structured target by applying `storage_name` to `target_path`.
- If both structured and flat fields are present and disagree, reject as ambiguous.
- Internal desired state and `data_jobs` should store normalized structured target paths.
- `resource_key` generation should use deterministic normalized `scan:{storage_name}:{path}` style data, without worker-node absolute paths.

Data Job storage persistence alignment:

- Keep current `data_jobs.storage_name/source/destination/target` columns usable.
- For `scan`, store canonical structured target in plan desired state and result/verification summary even if compatibility columns contain compact display path.
- If adding columns, prefer low-risk JSON/evidence columns first, for example `preflight_result`, `volcano_job_ref`, `log_uri`, `result_summary`.
- Do not break existing `GET /api/v1/operations/data-jobs/{job_id}` callers that expect the current fields.

Deferred to next phase:

- `sync` source/destination structured model
- cross-storage `sync` resource key and storage set calculation
- `rm` target deletion semantics
- `sync`/`rm` compatibility behavior for existing flat source/destination fields

## Authentication, Authorization, and DB Intake

`scan` intake must follow the existing DMS request lifecycle rather than becoming a direct imperative endpoint.

Required intake order:

1. Authenticate request actor through Phase 16 mTLS/token rules.
2. Validate `requester_id` is present and store it as requester identity, separate from actor.
3. Reject `sync`/`rm` early with explicit unsupported response and no plan/job side effect.
4. Parse and normalize `scan` target, priority, and options.
5. Apply scan operation authorization policy.
6. Persist accepted request envelope to operational DB.
7. Persist normalized scan target/options/priority in plan desired state and/or Data Job state.
8. Create Data Job and plan only after storage mapping/readiness guards pass or record a terminal rejection if planning cannot proceed.

Authentication requirements:

- mTLS-required mode must not fall back to `DMS_DEFAULT_ACTOR`.
- actor spoofing through `x-dms-actor` remains rejected according to Phase 16.
- actor and `requester_id` are both visible in request, Data Job, result, and query responses.

Authorization requirements:

- `scan` authorization may initially be conservative, but it must be explicit and test-covered.
- denied `scan` requests must not create a VolcanoJob, run, or backend side effect.
- denied requests should be represented as `AuthorizationFailed` terminal result with reason, actor, requester id, operation, target, and `backend_side_effect=false`.
- maintenance/drain/scheduling-block state must block intake or worker claims according to Phase 18 semantics.

DB persistence requirements:

- request row stores actor, requester id, operation, resource kind/key, payload summary, and lifecycle status.
- plan row stores normalized desired state, precondition, and execution metadata including `job_id`.
- data_jobs row stores operation, storage name, normalized target, priority label/resolved value, selected tool, worker pool, state, artifact URI, VolcanoJob ref, and timestamps.
- result row stores terminal status and verification summary for success and every failure path.
- observability events store diagnostic evidence for authz failure, preflight failure, node selection failure, Volcano failure, timeout, cancel, and artifact parsing failure.

DB parsing requirements:

- API and query responses must decode JSON/evidence fields into structured JSON, not expose raw serialized strings.
- unknown or missing legacy fields must degrade compatibly for pre-Phase-19 rows.
- Phase 19 must add migrations only when existing columns cannot safely carry required scan evidence.

## Operation Registry and Options

Add an operation registry rather than scattering conditionals across API, Planner, and Worker.

Each operation entry should define:

- public operation name
- internal `OperationKind`
- required path fields
- path type rules
- execution enabled in current phase
- selected tool strategy
- allowed options
- default priority
- default/max node count
- timeout and warning threshold
- artifact schema

Phase 19 registry:

| Operation | Tool | Preview | Confirm | Execution in Phase 19 |
| --- | --- | --- | --- | --- |
| `scan` | `dscan` | no | no | yes, read-only |

`sync` and `rm` entries are deferred to Phase 20. If a registry object includes placeholders for them, they must be marked `execution_enabled=false`, `intake_enabled=false`, and must not be used to create plans or Data Jobs in Phase 19.

Option handling:

- Reject raw option strings.
- Accept only typed JSON options from the allowlist.
- Convert allowlisted options to tool args in one dedicated layer.
- Do not log secrets or raw credentials in options, diagnostic events, or artifacts.
- Phase 19 `scan` options must be read-only.
- Dangerous option classification, preview evidence, and confirm semantics are deferred with `sync`/`rm`.

The implementation phase must inspect the pinned mpifileutils repo/tag before choosing concrete `dscan` CLI flags. This document intentionally does not hardcode mpifileutils flags beyond the DMS operation mapping.

## Identity and POSIX Permission Preflight

Data Management authorization has three gates:

1. API authentication
   - mTLS evidence and bearer token according to Phase 16.
   - authenticated caller is recorded as `actor`.

2. Operation authorization
   - policy decision based on actor, operation, target, priority, and max node request.
   - denied requests become `AuthorizationFailed` without preflight or backend side effect.
   - Current `AuthorizationPolicy` is a skeleton that only denies explicit test/config markers. Phase 19 does not need to finish full RBAC, but it must keep disabled destructive operations fail-closed.

3. POSIX data path authorization
   - `requester_id` maps to active Identity Mapping.
   - target nodes must resolve the requester UID/GID/groups consistently through NSS/SSSD or equivalent.
   - preflight checks actual target read/execute capability needed for `scan`.

Preflight must record:

- identity provider
- requester id
- POSIX username
- UID
- primary GID
- supplementary groups or resolved group GIDs
- node-local identity lookup evidence
- target path check results
- mount path
- path type
- permission decision
- checked_at timestamp
- worker node
- diagnostic event id on failure

Scan permission algorithm:

1. Load active Identity Mapping for `requester_id`.
2. Reject missing, `Disabled`, `NeedsReview`, or `Stale` mapping before node-local path checks.
3. Select candidate nodes with target mount and `dscan`.
4. On each candidate, verify the mapped POSIX username resolves to the same UID, primary GID, and required supplementary groups through node-local NSS/SSSD or equivalent evidence.
5. Resolve the storage-relative target path under the detected mount root.
6. Reject absolute path, traversal, symlink escape, bind mount escape, missing path, and non-directory target.
7. Check requester identity has execute permission on every path component from mount root to target.
8. Check requester identity has read and execute permission on the target directory.
9. Select only nodes where the permission decision is `Allowed`.
10. If no node remains, fail with `data_job_permission_denied` or `data_job_missing_identity_mapping` as appropriate.

The permission check must use the mapped UID/GID/group set, not the API process user. A shell helper may be used in the testbed if needed, but the result must be structured and recorded in operational DB evidence.

Execution identity:

- The `scan` runtime preflight pod and Volcano worker pod run with the mapped UID/GID when Kubernetes/runtime supports it.
- If the implementation cannot safely run as the requester POSIX identity in a target environment, live execution must fail closed and document the missing runtime mechanism.
- Running all Data Management jobs as root and treating preflight as sufficient is not acceptable for production behavior.
- Current Phase 19 implementation uses Kubernetes pod/container security context with the active Identity Mapping UID/GID and records runtime preflight evidence before Volcano submission.

## DM Agent Inventory and Worker Pool Selection

DM Worker pool selection uses fresh Agent reports and Kubernetes node state.

Candidate requirements:

- report freshness is within configured `DMS_AGENT_REPORT_STALE_SECONDS`
- `worker_role=DM`
- node identity matches authenticated Agent identity
- required storage mount evidence is `Ready`
- required tool evidence is `Ready`
- credential evidence is `Ready` where the operation needs it
- data-operation network evidence is `Ready` where the operation needs it
- identity evidence for requester is available or can be verified by preflight
- node is schedulable for DMS/Volcano worker pod

Selection rules:

- `scan`: target-mounted healthy DM Workers with `dscan`
- `sync`/`rm`: not selected in Phase 19; worker pool selection for these operations is deferred to Phase 20.

Selection output stored in DB should include:

- selected tool
- selection reason
- candidate nodes
- rejected candidate reasons
- required mounts
- required tools
- max node cap applied
- final pod count

If no candidate pool can be constructed, planning or preflight fails without creating a VolcanoJob.

Selection implementation requirements:

- collect candidates from fresh effective Agent reports first, then verify raw report evidence when detailed rejection reasons are needed
- require Kubernetes node object to exist and be schedulable
- exclude nodes with stale reports, missing mount, missing `dscan`, failed credential evidence, failed network evidence, missing requester identity evidence, or POSIX preflight denial
- record every rejected node with stable reason codes
- choose the smallest safe pool by default; Phase 19 verification should use one node/one pod unless config explicitly allows more
- render selected node names into Volcano pod affinity/nodeSelector or equivalent scheduler constraints
- store the exact selected node list and final pod count before VolcanoJob creation

Current implementation alignment:

- `EffectiveInventoryService` already excludes stale reports and normalizes evidence where `status=Ready`, `healthy!=False`, and `node_plugin_ready!=False`.
- `StorageMappingSanityService` writes `sanity_result.agent_observed.dm_candidates`, but those candidates currently prove storage visibility more than complete Data Management readiness.
- Phase 19 selector must read raw or effective Agent reports and explicitly check required tools, credentials, networks, and requester identity evidence before creating a VolcanoJob.
- Agent `DMS_AGENT_WORKER_ROLE` should be `DM` for Data Management candidate reports.
- In mTLS-required production profile, agent report ingestion currently requires authenticated actor `node:{cluster}:{node}`. If the verifier deploys the API with mTLS-required settings, it must also provide an agent authentication path that derives or supplies that node actor. Otherwise use the existing testbed/internal Agent posting boundary and document that production mTLS agent provisioning remains a follow-up.

## Volcano Runtime

Phase 19 adds a real Volcano adapter for Data Management.

Required adapter contract:

```python
class VolcanoDataJobAdapter:
    def create_job(self, plan, data_job) -> AdapterResult: ...
    def get_job(self, job_ref) -> dict: ...
    def terminate_job(self, job_ref) -> AdapterResult: ...
```

`create_job()` must return a stable job reference containing namespace, name, UID when available, API group/kind, submitted manifest hash, selected image, command args, selected nodes, service account, queue, and created_at.

`get_job()` must return normalized state independent of the specific Volcano API object version. At minimum it should expose submitted/running/succeeded/failed/unknown, pod phases, pod node assignment, start/finish timestamps, exit codes if available, and failure message.

`terminate_job()` must be idempotent. If the job is already gone, it returns success with evidence that no user data was deleted.

Implementation requirements:

- Match the existing `VolcanoAdapter`/`StubVolcanoAdapter` method names so the Worker can switch adapters without broad call-site churn.
- Keep `StubVolcanoAdapter` available only through explicit test/dev construction.
- `create_app()` and `dms dm-worker` currently instantiate `StubVolcanoAdapter`; Phase 19 must introduce settings-aware live adapter wiring for the DM Worker runtime before enabling live `scan`.
- API route tests may still inject/use stubs, but production install docs must not instruct operators to run DM Worker live execution with the stub.
- Use Kubernetes API or `kubectl` mode through runtime settings, following existing adapter patterns.
- Create Volcano-compatible workload in the DMS Kubernetes cluster.
- Use DMS-managed namespace, ServiceAccount, image, Secret, ConfigMap, and artifact volume/path.
- Set `schedulerName: volcano` or Volcano resource kind according to the chosen implementation.
- Map priority `High`, `Mid`, `Low` to configured Volcano queue/scheduling policy.
- Constrain pods to selected candidate DM Worker nodes.
- Enforce one worker pod per node for the same job.
- Set CPU, memory, timeout, and max pod count from runtime config and operation policy.
- Attach only approved mounts and credentials.
- Do not include raw user command line.
- Record job name, namespace, UID, selected image, command args, node pool, queue, created_at.
- Watch/read job and pod states until terminal or timeout.
- Treat worker pod `Failed` or `Evicted` as job failure.
- Do not automatically retry failed worker pods in Phase 19.
- On cancel/timeout, terminate/delete the VolcanoJob and record evidence.

Submission requirements:

- render a deterministic manifest from DB state and approved runtime config
- mount only the selected storage paths and artifact path
- run `dscan` with DMS-generated args only
- set pod security context to requester UID/GID/groups where possible
- if requester execution identity cannot be represented safely, fail closed before submission
- write VolcanoJob ref to the Data Job before waiting for completion

Monitoring requirements:

- DM Worker must heartbeat its run while polling/watching Volcano
- every poll/watch update that changes high-level state should update Data Job state or observed evidence
- terminal Volcano success must not be enough by itself; dscan report parsing and artifact write must also succeed
- terminal Volcano failure, pod failure, image pull failure, scheduling failure, permission failure, timeout, and cancellation must map to distinct reason codes where evidence allows
- stale active jobs after worker restart must be discoverable through DB state and reported in action-required until recovered or terminalized

Runtime settings to add or formalize:

```text
DMS_DM_NAMESPACE=dms
DMS_DM_JOB_IMAGE=<registry>/dms-mpifileutils:<tag-or-commit>
DMS_DM_JOB_IMAGE_REF=<mpifileutils git tag or commit>
DMS_DM_SERVICE_ACCOUNT=dms-dm-worker
DMS_DM_ARTIFACT_BASE_URI=<file/object/artifact-uri>
DMS_DM_DEFAULT_PRIORITY=Mid
DMS_DM_DEFAULT_MAX_NODES=1
DMS_DM_MAX_NODES=4
DMS_DM_SCAN_TIMEOUT_SECONDS=3600
DMS_DM_MONITOR_POLL_SECONDS=5
DMS_DM_JOB_DELETE_ON_TERMINAL=false
DMS_DM_KUBERNETES_MODE=cluster
```

The exact names may follow existing `Settings` style, but all environment-specific values must be configurable and documented under `install/`.

## mpifileutils Image and Artifact Contract

Phase 19 must not depend on whichever tool happens to exist in the API or worker image. Data Management job execution uses a dedicated approved job image.

Requirements:

- Build or document a job image containing the pinned mpifileutils repo version.
- Store the mpifileutils git tag/commit in config and job result evidence.
- `dscan` availability must be probed by Agent and verified by preflight.
- The same image may contain `dsync`, `nsync`, and `drm`, but Phase 19 must not invoke them.
- Tool invocation is generated by DMS from normalized operation options.
- stdout/stderr and tool report are stored as artifacts.
- operational DB stores only summary and URIs.
- dscan result parser converts the approved report format into a stable summary schema.
- parser failure records `data_job_artifact_parse_failed` or equivalent action-required evidence.

Current implementation note:

- `install/docker/Dockerfile.mpifileutils` builds the approved job image from
  `chahwansong/mpifileutils`.
- The real pinned `dscan` report observed in the testbed does not include a
  total-byte field. The Volcano scan pod therefore writes a DMS-normalized
  `summary.json` beside the raw report, using POSIX `find`/`awk` on the mounted
  target. The DB parser reads `summary.json` first and falls back to
  `dscan-report.json` when the normalized artifact is absent.

Artifact location contract:

- `data_jobs.artifact_uri` stores the job artifact base URI.
- `result_summary.report_uri`, `stdout_uri`, `stderr_uri`, and `summary_uri` store child artifact URIs derived from that base.
- For the current `file://` backend, actual result files are written under
  `<DMS_DM_ARTIFACT_BASE_URI path>/<job_id>/`. The normalized summary is
  `<...>/<job_id>/summary.json`, the raw mpifileutils report is
  `<...>/<job_id>/dscan-report.json`, and stdout/stderr are sibling log files.
- The artifact base must be a DMS-managed path that is writable by the Volcano scan pod running as the mapped POSIX UID/GID and readable/traversable by the DM Worker. It must not be hidden under a requester-private target directory unless the DM Worker is explicitly granted traverse/read access.
- In the Phase 19 testbed verifier the target directory is under `/mnt/testbed-cephfs/dms-phase19-<timestamp>/input`, while artifacts are intentionally separated under `/mnt/testbed-cephfs/dms-phase19-artifacts-<timestamp>/<job_id>/`.

Minimum artifact summary:

```json
{
  "artifact_base_uri": "file:///artifacts/dms/job_...",
  "stdout_uri": "file:///artifacts/dms/job_.../stdout.log",
  "stderr_uri": "file:///artifacts/dms/job_.../stderr.log",
  "report_uri": "file:///artifacts/dms/job_.../dscan-report.json",
  "summary_uri": "file:///artifacts/dms/job_.../summary.json",
  "summary": {
    "file_count": 12,
    "directory_count": 3,
    "total_bytes": 4096,
    "error_count": 0,
    "scan_root": "project/input"
  }
}
```

If artifact storage is not available in the testbed, Phase 19 may use a DMS-managed PVC or host-mounted artifact path for verification, but the implementation must keep the artifact URI abstraction so production can use object storage or another approved store later.

Result parsing requirements:

- summary must include file count, directory count, total bytes, error count, and scan root.
- summary should include elapsed time and tool version/ref when available.
- detailed file listing must not be stored inline in operational DB.
- parsing must be deterministic and covered by local unit tests with normalized
  fixture output and representative raw output from the pinned mpifileutils
  version.
- if the pinned `dscan` output format differs from this expected schema, Phase 19 must update the parser contract and verification document with concrete examples.

## Data Job State Semantics

Phase 19 implements the `scan` lifecycle:

```text
Pending -> PreflightRunning -> Scheduled -> Running -> Succeeded/Failed
```

Required DB transition semantics:

- `Pending`: Data Job exists, normalized target/options persisted, no VolcanoJob yet.
- `PreflightRunning`: worker has claimed the plan/run and is evaluating identity, path, mount, tool, and node evidence.
- `PreflightFailed`: no VolcanoJob was created; result summary records the failed gate.
- `Scheduled`: VolcanoJob was submitted and job ref persisted.
- `Running`: at least one worker pod is running or Volcano reports the job running.
- `Succeeded`: Volcano terminal success, artifact writes complete, dscan summary parsed, result row recorded.
- `Failed`: terminal failure reason and evidence recorded.
- `TimedOut`: runtime timeout reached; terminate/delete attempted and evidence recorded.
- `Cancelled`: cancel requested; terminate/delete attempted when needed and evidence recorded.

`sync` and `rm` lifecycle states are deferred to Phase 20. New Phase 19 intake for these operations should not create jobs that enter `Pending`, `PreviewRunning`, or `ConfirmPending`.

Failure states:

- `AuthorizationFailed`
- `PreflightFailed`
- `Failed`
- `Cancelled`
- `TimedOut`

Common request/plan/run lifecycle:

- Phase 19 should avoid creating any new destructive Data Job plan that a live DM Worker can claim.
- Confirm returns the common plan to `Planned` only in a future phase where destructive execution is enabled.

Repository/data model changes should support:

- normalized structured target
- options summary
- priority label and resolved queue
- preflight result
- selected tool and selection reason
- worker pool details
- Volcano job reference
- timeout/warning threshold
- artifact URIs
- final summary

## Scan Live Execution

`scan` is the first live Data Management operation.

End-to-end execution sequence:

1. API authenticates actor and validates structured scan request.
2. API rejects unsupported `sync`/`rm` before side effects.
3. API persists accepted scan request with normalized payload summary.
4. Planner validates storage mapping, DM readiness, and operation authorization preconditions.
5. Planner creates Data Job and DM plan with normalized target/options/priority.
6. DM Worker claims plan, starts run heartbeat, and marks Data Job `PreflightRunning`.
7. DM Worker selects candidate nodes from fresh DM Agent reports.
8. DM Worker resolves active Identity Mapping and performs node-local POSIX permission preflight.
9. DM Worker renders and submits VolcanoJob only after preflight passes.
10. DM Worker persists VolcanoJob ref and marks Data Job `Scheduled`/`Running`.
11. DM Worker monitors VolcanoJob and pods until terminal, timeout, or cancel.
12. On success, DM Worker reads dscan artifacts, parses summary, writes result evidence, and marks job/request terminal success.
13. On failure, timeout, cancel, or parse error, DM Worker records terminal state, reason, diagnostic event, and action-required evidence where appropriate.

Planner requirements:

- target storage mapping exists and is not disabled
- target storage has DM readiness `Ready`
- target path is storage-relative and safe
- requester identity mapping is active
- target candidate pool has at least one node with target mount and `dscan`

Worker requirements:

- commit run `Running` before creating VolcanoJob
- heartbeat run while waiting for Kubernetes/Volcano completion
- run preflight before `dscan`
- create VolcanoJob only after preflight passes
- monitor pod/job state
- collect scan summary
- store artifact URIs
- record success/failure result

Live verification must use a tiny test directory and resource-conscious settings.

## Sync and Rm Deferral

Phase 19 must not implement `sync` or `rm` request handling beyond a clear fail-closed boundary.

Required Phase 19 behavior:

- New `sync`/`rm` requests return `unsupported_until_phase20` or an equivalent explicit unsupported response.
- No new `sync`/`rm` plan, run, resource, or Data Job row is created from public intake.
- No `dsync`, `nsync`, or `drm` VolcanoJob is rendered.
- `jobs/{job_id}:confirm` must not start `dsync`, `nsync`, or `drm` mutation for any pre-existing/stub job.
- Any existing stub success path must be disabled for live DM Worker mode.

Next phase owns:

- `sync` source/destination structured path validation
- `sync` `dsync`/`nsync` selection
- `rm` target delete validation
- `sync`/`rm` POSIX write/delete preflight
- dry-run/preview artifact semantics
- confirm TTL and destructive execution guard
- destructive operation recovery policy

## Operational Queries and Action Required

Existing operational query endpoints should expose richer Data Management state.

Required query fields:

- job id
- request id
- requester id
- actor
- operation
- state
- target normalized path
- selected tool
- selected worker pool
- rejected candidate summary
- preflight result
- Volcano job reference
- artifact URIs
- timeout
- created/updated timestamps

Scan query behavior:

- list returns newest jobs first with stable pagination/limit behavior consistent with existing operations queries.
- requester-scoped query must not return other requester jobs.
- detail works for running, succeeded, failed, timed out, cancelled, and preflight-failed jobs.
- detail includes both desired normalized target and observed execution target evidence.
- detail includes result summary only after parser success; before success it returns `null` or an explicit pending field.
- missing job id returns 404, not an empty successful object.
- malformed filter values return validation errors without broad DB scans.

Action-required issue types to add:

- `data_job_preflight_failed`
- `data_job_volcano_failed`
- `data_job_timed_out`
- `data_job_stale_or_recovery_needed`
- `data_job_missing_dm_readiness`
- `data_job_missing_identity_mapping`
- `data_job_permission_denied`
- `data_job_artifact_write_failed`
- `data_job_artifact_parse_failed`

Action-required must show unresolved current issues, not every historical failure.

## Testbed Live Verification

Phase 19 verification uses the existing Vagrant multi-cluster testbed.

검증 전 확인:

```bash
cd /home/mason/workspace/dms
cat /home/mason/workspace/testbed/testbed-summary.json
cat /home/mason/workspace/testbed/testbed-info.json
ssh c1-control "kubectl get nodes -o wide; kubectl get pods -A | grep -E 'volcano|dms|postgresql' || true"
ssh c1-worker "mount | grep -E 'ceph|testbed' || true"
```

Live verification target:

- PostgreSQL NodePort: `192.168.56.11:30432`
- OpenLDAP/SSSD: existing testbed LDAP users such as `alice`, `bob`
- DMS API/Planner/DM Worker on `cluster-a`
- DMS Agent DaemonSet with real DM reports
- Volcano scheduler in `cluster-a`
- small host-mounted CephFS target on `cluster-a/c1-worker`

Recommended flow:

1. Build DMS image and mpifileutils job image with explicit tag/commit evidence.
2. Deploy API, Planner, Agent, and DM Worker with Phase 19 config.
3. Verify API authentication mode, actor derivation, token/mTLS headers, and maintenance/drain state.
4. Verify fresh DM Agent report includes:
   - target mount
   - `dscan`
   - identity evidence for requester
   - credential/network readiness where configured
5. Register or reuse `cephfs-a` storage mapping with DM readiness `Ready`.
6. Register or refresh active Identity Mapping for the requester.
7. Create a tiny DMS-managed filesystem or use a pre-existing tiny safe directory under the DMS-managed test storage.
8. Submit `scan` request for a small directory.
9. Confirm Planner creates a DM plan and Data Management Job with normalized target.
10. Confirm DM Worker records identity/POSIX preflight evidence.
11. Confirm DM Worker creates a real Volcano workload.
12. Confirm Volcano worker pod runs on selected candidate node.
13. Confirm DM Worker monitors the job through terminal state.
14. Confirm `dscan` result summary and artifact URIs are recorded.
15. Confirm scan list/detail query returns normalized target, state, preflight, Volcano ref, artifact URI, and result summary.
16. Submit a scan with missing/inactive identity mapping and verify it fails before VolcanoJob creation.
17. Submit a scan against a directory the requester cannot read/execute and verify permission denial before VolcanoJob creation.
18. Submit `sync` and `rm` requests only to verify they return explicit unsupported responses without creating plan/job side effects.
19. Verify timeout/cancel path with a bounded fixture if resource cost is acceptable.

Resource constraints:

- Default live `scan` should use one node and one pod.
- Test directory should contain only a few tiny files.
- Do not run broad recursive scans over testbed storage roots.
- Do not run `sync` or `rm` preview, dry-run, or mutation in Phase 19.

## Required Command Evidence

Verification document must include:

```bash
python3 -m py_compile src/dms/*.py src/dms/backends/*.py scripts/phase19_*.py
pytest -q tests/test_phase19_data_management_scan.py
pytest -q
git diff --check
```

Testbed evidence should include:

```bash
./scripts/verify-phase19-testbed.sh
```

The verification output must show:

- operational and observability PostgreSQL DB names
- mpifileutils image tag and repo commit/tag
- DMS image tag
- selected target storage and directory
- fresh DM Agent report IDs
- selected worker pool
- selected tool
- identity mapping status and POSIX preflight decision
- Volcano job name/namespace/UID
- pod node assignment
- scan result summary
- artifact URIs
- scan list/detail query output
- negative authorization/preflight evidence
- `sync`/`rm` unsupported response evidence
- cleanup evidence

## Local Regression Tests

Minimum local tests:

- scan endpoint requires configured authentication boundary and records actor separately from requester id
- operation authorization denial records terminal result without Data Job, run, or VolcanoJob side effect
- structured request validation accepts canonical `scan`
- flat and structured path conflict is rejected
- absolute/path traversal/unsafe path is rejected
- unknown option is rejected
- normalized scan target/options/priority are persisted and decoded in query responses
- inactive identity mapping blocks Data Management execution
- missing identity mapping blocks Data Management execution before Volcano submission
- POSIX read/execute denial blocks scan before Volcano submission
- missing DM readiness blocks plan creation
- stale DM Agent report is excluded from candidate pool
- `scan` candidate pool requires target mount and `dscan`
- node selection records rejected candidate reasons
- Volcano adapter renders node constraints and one-pod-per-node rule
- Volcano adapter persists job ref before monitoring
- Volcano monitor maps pod failed/evicted/image-pull/scheduling failure to Data Job failure
- timeout terminates VolcanoJob and records timeout evidence
- `scan` success records artifact summary and result
- dscan parser converts normalized fixture output and representative real
  mpifileutils output into file/directory/byte/error summary
- artifact parse failure records failure/action-required evidence
- scan list/detail query returns normalized target, preflight, Volcano ref, artifact URI, and result summary
- requester-scoped query does not leak another requester scan job
- Volcano pod failure records Data Job failure
- cancel terminates VolcanoJob if job ref exists
- `sync`/`rm` public intake returns unsupported without creating plan/job side effects
- `sync`/`rm` confirm on pre-existing/stub jobs is blocked in Phase 19
- maintenance/drain still blocks Data Management mutating intake and worker claims
- mTLS protected endpoint matrix includes any new Data Management endpoints

## Install Documentation Updates

Update:

- `install/README.md`
- `install/CONFIGURATION.md`
- `install/RUNBOOK.md`
- `install/kubernetes/control-plane.yaml`
- `install/kubernetes/agent-daemonset.yaml`
- any new DM Worker/Volcano manifest or ConfigMap

Docs must explain:

- DM worker replica can be enabled only for Phase 19 `scan` live execution.
- `sync`/`rm` are not implemented in Phase 19 and remain explicit unsupported operations.
- API authentication/mTLS/token settings required for scan intake.
- Identity Mapping and `DMS_AGENT_IDENTITY_USERS` requirements for scan POSIX preflight.
- mpifileutils image must be pinned by tag/commit.
- Volcano scheduler, namespace, ServiceAccount, queue, and RBAC requirements.
- approved ServiceAccount/Secret/artifact path are operator-managed.
- Agent reports must include DM mount/tool identity evidence.
- scan query and action-required runbook commands.
- production Data Management must not use arbitrary user-provided images or raw CLI args.

## Out of Scope

Phase 19 does not implement:

- `sync` request model, preflight, preview, or execution
- `rm` request model, preflight, preview, or execution
- confirmed destructive `sync` execution
- confirmed destructive `rm` execution
- automatic retry of failed Volcano worker pods
- restart/resume of partially completed data mutation
- PV/PVC based data operation mount model
- arbitrary user-provided image, Secret, SSH config, or raw CLI args
- metadata operation such as `chmod`
- cross-site WAN transfer policy
- production object storage integration if a simple artifact URI backend is enough for verification
- full generic RBAC policy schema beyond the explicit scan authorization and POSIX permission gates required in Phase 19
- per-request quota/cost accounting
- large-scale performance benchmark

## Phase 19 이후 다음 작업 리스트

Phase 19가 성공하면 DMS는 Data Management의 read-only `scan` live runtime과 destructive operation에 대한 명시적 unsupported 경계를 갖는다. 다음 phase 후보:

### Phase 20A: Sync/Rm Preview and Confirmed Execution

- `sync` source/destination request model
- `rm` target request model
- `sync`/`rm` POSIX write/delete preflight
- dry-run/preview artifact semantics
- `sync` confirmed `dsync`/`nsync` execution
- `rm` confirmed `drm` execution
- preview result hash validation
- confirm TTL and stale preview guard
- destructive operation recovery policy

### Phase 20B: Multi-node Data Management Hardening

- multi-node `dscan`
- multi-node `dsync` and split-role `nsync`
- Volcano queue fairness and priority tuning
- data-operation network reachability hardening

### Phase 20C: Artifact Store and Audit Hardening

- production artifact object store integration
- artifact retention policy
- checksum/manifest for reports
- audit export

### Phase 20D: PV/PVC Data Operations

- PVC attach/mount model for Data Management pods
- StorageClass/PVC-backed source/target support
- same preflight and POSIX/security boundary as host-mounted filesystem jobs
