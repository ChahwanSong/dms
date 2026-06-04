# DMS Phase 21: Data Management Minimal Functional Execution

이 문서는 `docs/dms-phase20.md` 완료 이후 다음 구현 phase의 범위를 정리한다.
Phase 21의 목표는 Data Management `scan`, `rm`, same-node `sync` 기능을 현재 DMS
구현 구조에 맞춰 단순한 live execution 모델로 정렬하는 것이다.

Status: implemented and testbed-verified on 2026-06-04. Verification evidence is
recorded in `docs/dms-done.md` under "Phase 21 Data Management Minimal
Functional Execution". The next phase should start from the "Next Phase: Job
Resource Model" section below.

Phase 21에서는 job당 node/process 리소스 모델을 일반화하지 않는다. 모든 live Data
Management job phase는 우선 기능 정확성에 집중해 다음 기본 실행 모델로 고정한다.

- 1 selected node
- 1 Volcano worker pod
- 1 mpifileutils process

job당 configurable node count, pod당 process/rank count, multi-node fan-out, separated-role
`nsync` orchestration은 다음 phase에서 리소스 모델을 정한 뒤 완성한다.

## Current Baseline

Phase 20에서 live verified 된 범위:

- `scan`: real `dscan` VolcanoJob, artifact parsing, DB summary/query
- `sync`: same-node `dsync` preview/execution, explicit confirm, TTL/fingerprint guard
- `rm`: `drm` preview/execution, explicit confirm, TTL/fingerprint guard
- `sync`/`rm`: missing identity, raw option/path guard, action-required
- standalone multi-node MPI/TCP smoke: `mpirun` 기반 `dscan` 실행

Phase 20 이후 남은 범위:

- `scan`, `dsync`, `drm` live path는 기능 검증은 됐지만, selected candidate가 여러 개일
  때 job당 node/process 사용량을 어떤 정책으로 정할지 아직 phase 문서에 정리되지 않았다.
- current worker는 separated-role `nsync` candidate pool을 식별할 수 있지만,
  `KubernetesVolcanoAdapter` live path는 `nsync live execution is not enabled in this adapter path`
  로 fail-closed한다.
- `nsync` role Service, hostfile, role-map, launcher pod, cleanup/recovery evidence는
  아직 live Done으로 보지 않는다.

## Phase 21 Scope

Phase 21은 다음을 구현한다.

1. `scan`/`dscan`을 selected candidate 하나, worker pod 하나, `dscan` process 하나로 실행한다.
2. `rm`/`drm` preview와 execution을 각각 selected candidate 하나, worker pod 하나,
   `drm` process 하나로 실행한다.
3. same-node `sync`/`dsync` preview와 execution을 각각 selected candidate 하나, worker
   pod 하나, `dsync` process 하나로 실행한다.
4. selected candidate list가 여러 개여도 Phase 21 live execution은 첫 번째 ready
   candidate 하나만 사용한다.
5. Volcano task `replicas`는 `scan`, `rm`, same-node `sync` 모두 1로 고정한다.
6. API request에는 node count, process count, rank count, raw MPI option field를
   추가하지 않는다.
7. runtime preflight와 final summary에는 selected node, worker pod count, process count가
   Phase 21 minimal model과 일치하는지 evidence로 남긴다.
8. separated-role `nsync`가 필요한 topology는 fail-closed로 유지하되, candidate pool과
   deferred reason을 query/action-required에서 확인 가능하게 남긴다.
9. Phase 19 `scan`과 Phase 20 `dsync`/`drm` regression을 깨지 않는다.

## Phase 21 Non-Scope

Phase 21은 다음을 구현하지 않는다.

- job당 configurable node/process/rank resource model
- user/request-driven resource hint
- multi-node `scan` fan-out
- multi-node `drm` fan-out
- multi-node same-node `dsync` fan-out
- pod당 multiple MPI ranks/processes
- separated-role `nsync` live execution
- `nsync` source/destination role Service orchestration
- generated `nsync` hostfile/role-map/launcher pod
- arbitrary user-provided MPI launcher/network/mpifileutils flags
- retry/resume of partial mutation
- large-scale throughput benchmark

## Minimal Execution Model

Phase 21은 기능 구현과 검증에 집중하기 위해 effective execution resources를 다음처럼
고정한다.

| Operation | Tool | Phase | Selected nodes | Worker pods | Tool processes |
| --- | --- | --- | ---: | ---: | ---: |
| `scan` | `dscan` | execution | 1 | 1 | 1 |
| `rm` | `drm` | preview | 1 | 1 | 1 |
| `rm` | `drm` | execution | 1 | 1 | 1 |
| same-node `sync` | `dsync` | preview | 1 | 1 | 1 |
| same-node `sync` | `dsync` | execution | 1 | 1 | 1 |

Implementation requirements:

- Candidate 중복 제거 기준은 최소 `cluster_name + node_name`이어야 한다.
- `scan`, `rm`, same-node `dsync`는 selected candidate list가 여러 개여도 첫 번째
  ready candidate 하나만 사용한다.
- Volcano manifest에는 단일 worker task만 있어야 하며 `replicas: 1`이어야 한다.
- worker pod는 selected node에 pinning 또는 affinity로 배치되어야 한다.
- worker pod는 mapped POSIX UID/GID/groups 또는 runtime이 지원하는 equivalent security
  context로 실행되어야 한다.
- pod summary에는 실제 scheduled pod 수와 node name이 기록되어야 한다.
- result summary에는 최소한 `selected_node`, `worker_pod_count`, `process_count`를 남긴다.

설정 처리:

- 기존 `DMS_DM_MAX_NODES`, `DMS_DM_MAX_SYNC_NODES`, `DMS_DM_MAX_RM_NODES`는 Phase 21에서
  multi-node fan-out 설정으로 해석하지 않는다.
- 설정값이 1보다 커도 Phase 21 live execution의 effective selected node count는 1이다.
- API request에는 node/process/rank count field를 추가하지 않는다.
- 다음 phase에서 resource model을 정하기 전까지 operation별 default/max resource 정책은
  runtime behavior로 노출하지 않는다.

## Tool Selection

`sync` tool selection 순서:

1. source와 destination을 동시에 mount한 same-node candidate가 있으면 `dsync`를 선택한다.
2. same-node candidate가 여러 개면 첫 번째 ready candidate 하나만 selected candidate로
   사용한다.
3. same-node candidate가 없고 separated source/destination role pool만 있으면 Phase 21은
   `nsync`를 실행하지 않고 fail-closed한다.
4. fail-closed result에는 `selected_tool=nsync`, source/destination role candidate summary,
   `deferred_phase=phase22`, `reason=nsync_live_execution_deferred`를 남긴다.
5. `DMS_DM_NSYNC_ENABLED=false`이면 separated-role 후보가 있어도 동일하게 fail-closed한다.

preflight/result evidence:

- selected tool and selection reason
- selected same-node candidate, if `dsync`
- source/destination role pools, if separated-role `nsync` would be required
- rejected candidates and reasons
- effective selected node count
- effective worker pod count
- effective process count

## Authorization And Runtime Preflight

Phase 21은 Phase 19/20의 authorization boundary를 그대로 유지한다.

- API caller authentication is required.
- active Identity Mapping must exist for the requester.
- POSIX UID/GID/groups from active Identity Mapping are used for runtime checks.
- API pod must not use API-local filesystem visibility as authority.
- DM Agent reported mount/tool/identity evidence is used for candidate selection.
- stale Agent reports are excluded.
- runtime preflight pod verifies the selected node can access the path with the mapped identity.

Operation-specific checks:

- `scan`: selected path read/traverse permission and `dscan` tool readiness
- `rm`: target/parent boundary, preview-before-execution, explicit confirm, `drm` readiness
- same-node `sync`: source read/traverse, destination parent write/execute, destination not source
  or source child, preview-before-execution, explicit confirm, `dsync` readiness
- separated-role `nsync`: do not mutate; record deferred/fail-closed evidence

## Kubernetes/Volcano Shape

Phase 21 extends or verifies the existing `KubernetesVolcanoAdapter` behavior only for the
minimal single-worker model.

Expected Volcano shape:

```text
VolcanoJob/dms-{scan|sync|rm}-{preview|execution}-<job-id>
  task/worker
    replicas: 1
    nodeAffinity: selected node
    mounts: selected storage hostPath(s)
    command: approved mpifileutils command
```

Tool command shape:

```bash
# scan
/opt/mpifileutils/bin/dscan <validated options> <target> --output <dscan-report.json>

# rm preview
/opt/mpifileutils/bin/drm --dryrun <validated options> <target>

# rm execution
/opt/mpifileutils/bin/drm <validated options> <target>

# sync preview
/opt/mpifileutils/bin/dsync --dryrun <validated options> <source> <destination>

# sync execution
/opt/mpifileutils/bin/dsync <validated options> <source> <destination>
```

The exact CLI rendering must follow the current mpifileutils wrapper/adapter implementation.
DMS-owned paths, report output paths, and tool options remain generated or allowlisted by DMS.

## Artifact And DB Summary

`data_jobs.artifact_uri` remains the job base URI.

Expected artifact layout:

```text
<artifact_base>/<job_id>/
  preflight/
    preflight.json
  preview/
    command.json
    volcano-job.json
    stdout.log
    stderr.log
    summary.json
  execution/
    command.json
    volcano-job.json
    stdout.log
    stderr.log
    summary.json
    dscan-report.json
```

`scan` may write `dscan-report.json` directly under the execution phase path or under the
existing scan report path used by the current implementation. The DB summary must include the
actual `scan_report_uri`.

Minimal summary fields:

- operation
- selected_tool
- phase
- dry_run, if preview/execution mutation operation
- selected_node
- worker_pod_count: `1`
- process_count: `1`
- artifact_uri
- report URI, if produced
- file_count
- directory_count
- total_bytes
- error_count
- exit_code

For separated-role `nsync` fail-closed/deferred result, summary should include:

- selected_tool: `nsync`
- deferred_phase: `phase22`
- reason: `nsync_live_execution_deferred`
- source_role_pool
- destination_role_pool
- backend_side_effect: `false`

## Query / Action-Required

Query-visible Phase 21 fields:

- `selected_tool`
- `selected_node`
- `worker_pod_count`
- `process_count`
- `scan_report_uri`
- `preview_summary`
- `execution_summary`
- `artifact_uri`
- `external_job_ref`
- `external_pod_summary`

Action-required issues:

- `data_job_preflight_failed`
- `data_job_missing_identity_mapping`
- `data_job_runtime_preflight_failed`
- `data_job_volcano_failed`
- `data_job_artifact_parse_failed`
- `data_job_nsync_deferred`

`data_job_nsync_deferred` is not a successful `nsync` execution. It is explicit evidence that the
request required separated-role `nsync` and Phase 21 intentionally did not mutate data.

## Testbed Verification Plan

테스트베드 리소스를 아끼기 위해 tiny fixture만 사용한다.

필수 metadata 확인:

```bash
cat /home/mason/workspace/testbed/testbed-summary.json
ssh c1-control "kubectl get nodes -o wide; kubectl -n volcano-system get deploy,pod"
ssh c1-control "findmnt -rn /shared_directory -o FSTYPE,TARGET,SOURCE"
ssh c1-worker "findmnt -rn /shared_directory -o FSTYPE,TARGET,SOURCE"
```

필수 live scenarios:

1. `scan` regression: one selected node, one Volcano worker pod, one `dscan` process.
2. `rm` preview regression: one selected node, one Volcano worker pod, one `drm --dryrun` process.
3. `rm` execution regression: confirm 이후 one selected node, one Volcano worker pod,
   one `drm` process.
4. same-node `sync` preview regression: one selected node, one Volcano worker pod,
   one `dsync --dryrun` process.
5. same-node `sync` execution regression: confirm 이후 one selected node, one Volcano worker pod,
   one `dsync` process.
6. multiple candidate regression: Agent report가 여러 candidate를 제공해도 live manifest는
   selected candidate 하나와 `replicas: 1`로 수렴한다.
7. separated-role `nsync` negative: source-only/destination-only candidate pool이 있으면
   `nsync` live execution을 시작하지 않고 fail-closed/deferred evidence를 남긴다.
8. missing identity and POSIX denial negative cases remain fail-closed before mutation.
9. DB query에서 artifact URI, report URI, selected node, pod/process count를 확인한다.
10. `git diff --check`, compile, pytest regression을 통과한다.

## Required Tests

Local/unit:

- `scan` selected candidates가 여러 node여도 manifest task `replicas`는 1
- `rm` selected candidates가 여러 node여도 preview/execution manifest task `replicas`는 1
- same-node `dsync` selected candidates가 여러 node여도 preview/execution manifest task
  `replicas`는 1
- request payload가 node/process/rank count 또는 raw MPI option을 받을 수 없음
- runtime/final summary가 selected node, worker pod count, process count를 기록
- split source/destination candidate일 때 `nsync`는 Phase 21에서 fail-closed/deferred
- `DMS_DM_NSYNC_ENABLED=false`이면 fail-closed
- `dsync` candidate가 있으면 `dsync` 우선
- source read denial
- destination write denial
- preview fingerprint guard
- confirm opens execution phase only after valid preview
- cancel deletes active VolcanoJob
- `scan`, `dsync`, `drm` regression

Recommended commands:

```bash
python3 -m compileall -q src/dms
python3 -m pytest tests/test_phase20_data_management_sync_rm.py tests/test_phase19_data_management_scan.py -q
bash -n scripts/verify-phase21-testbed.sh scripts/verify-phase20-testbed.sh scripts/verify-phase19-testbed.sh install/scripts/*.sh
scripts/verify-phase21-testbed.sh
git diff --check
```

## Install / Runbook Updates

Phase 21 구현 시 업데이트할 문서:

- `install/README.md`
- `install/CONFIGURATION.md`
- `install/RUNBOOK.md`
- `install/config/dms-runtime.env.example`
- `install/kubernetes/control-plane.yaml`
- `install/scripts/verify-install.sh`
- `docs/dms-done.md`

설정 문서에 명확히 남길 것:

- Phase 21 minimal execution model: 1 selected node, 1 worker pod, 1 tool process
- `DMS_DM_MAX_NODES`, `DMS_DM_MAX_SYNC_NODES`, `DMS_DM_MAX_RM_NODES` are not Phase 21
  fan-out controls
- `DMS_DM_NSYNC_ENABLED` does not mean `nsync` live execution is Done in Phase 21
- separated-role `nsync` remains fail-closed/deferred until the next phase
- artifact/report URI locations
- VolcanoJob inspection and cleanup behavior

## Next Phase: Job Resource Model

다음 phase는 `docs/dms-phase22.md`에 정리한다. Phase 22에서는 Phase 21 minimal model
위에 job당 node/process 리소스 모델을 정하고 완성한다. 다음 phase 범위에는 다음이
포함된다.

- operation별 default node count와 max node count
- operation별 default processes-per-pod/ranks-per-pod와 max value
- request에서 허용할 resource hint 여부와 authorization/policy boundary
- node당 worker pod 하나 강제를 위한 per-node task 또는 anti-affinity/topology spread
- generated hostfile slots/rank metadata
- scheduling summary와 observed pod placement DB query
- multi-node `scan`/`drm`/`dsync` fan-out
- separated-role `nsync` live execution
- `nsync` source/destination role Service orchestration
- `nsync` generated hostfile/role-map/launcher pod
- `mpirun` TCP launcher/interface policy
- scan/rm/dsync/nsync multi-node regression and testbed verification

## Completion Checklist

Phase 21 완료 조건:

- `scan`은 1 selected node, 1 worker pod, 1 `dscan` process로 실행된다.
- `rm` preview/execution은 각각 1 selected node, 1 worker pod, 1 `drm` process로 실행된다.
- same-node `sync` preview/execution은 각각 1 selected node, 1 worker pod, 1 `dsync`
  process로 실행된다.
- selected candidate list가 여러 개여도 live manifest는 첫 번째 ready candidate 하나와
  `replicas: 1`로 수렴한다.
- API request는 node/process/rank count 또는 raw MPI option을 받지 않는다.
- runtime preflight와 result summary가 selected node, worker pod count, process count를
  남긴다.
- separated-role `nsync` topology는 mutation 없이 fail-closed/deferred evidence를 남긴다.
- Phase 19 `scan`과 Phase 20 `dsync`/`drm` regression이 통과한다.
- testbed live verification은 mock/stub과 real backend evidence를 분리해 기록한다.
