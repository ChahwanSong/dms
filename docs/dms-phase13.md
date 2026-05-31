# DMS Phase 13 Implementation Prompt

이 문서는 `docs/dms-phase12.md` 완료 이후 열세 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 13의 목표는 Phase 10-12에서 verifier process가 직접 `Planner.run_once()`와 `RMWorkerRuntime.run_once()`를 호출하던 Resource Management 실행 경로를 실제 Kubernetes long-running runtime으로 승격하고, IBM GPFS backend skeleton을 IBM Storage Scale command 기반 구현으로 전환하는 것이다.

Phase 13의 핵심 범위는 다음 두 묶음이다.

- **Phase 13A: Long-Running RM Worker Runtime Deployment**
- **Phase 13B: IBM GPFS / IBM Storage Scale Fileset Backend Implementation**

중요: Phase 13A는 Data Management live execution이나 VolcanoJob 실행이 아니다. 이번 phase는 Resource Management plan을 운영형 loop에서 claim, execute, verify, recover하는 경로를 닫는다. Phase 13B는 테스트베드에 GPFS가 없어 live verification은 못 하더라도, 실제 IBM Storage Scale 환경에서 실행 가능한 command adapter를 구현하고 fake command executor 기반 regression test로 command rendering, parse, failure, recovery behavior를 검증한다.

## Phase 13 목표

### Phase 13A: Long-Running RM Worker Runtime Deployment

1. `dms planner --loop` Kubernetes Deployment 검증
2. `dms rm-worker --loop` Kubernetes Deployment 검증
3. RM Worker loop가 stub adapter가 아니라 storage mapping별 live adapter registry를 사용하도록 wiring
4. Phase 10-12 filesystem RM 기능을 verifier-side `run_once()` 호출 없이 API request만으로 처리
5. Worker lease, stale claim, retry/recovery, restart behavior 검증
6. Worker health/action-required/diagnostic event 운영 조회 보강

### Phase 13B: IBM GPFS / IBM Storage Scale Backend

1. GPFS storage mapping schema 보강
2. IBM Storage Scale command runner abstraction
3. Fileset-backed filesystem create/delete
4. Fileset quota apply/read-back/check/sync
5. GPFS existing linked fileset import/assign-quota
6. GPFS command output parser and unit tests

구현 완료 기준은 다음과 같다.

- Phase 13 live verifier는 DMS API, DMS Agent DaemonSet, Planner Deployment, RM Worker Deployment를 Kubernetes에 배포한다.
- verifier는 RM Worker를 직접 호출하지 않는다. API request를 제출하고 PostgreSQL request/plan/run/result state와 backend state를 polling해 검증한다.
- Planner loop가 request를 planned state로 전환하고, RM Worker loop가 plan을 claim해 실행한다.
- RM Worker loop는 `BackendAdapterRegistry.with_live_defaults(repository, settings)` 또는 동등한 settings-aware registry를 사용해 CephFS live adapter를 선택한다.
- Phase 12 quota lifecycle, import/assign-quota, check/sync, delete cleanup flow가 long-running RM Worker Deployment 경유로 c1/c2 host-mounted CephFS에서 성공한다.
- RM Worker Pod restart 또는 stale lease 상황에서 duplicate side effect 없이 recovery/action-required evidence가 남는다.
- `GET /api/v1/operations/runs/stale`, `GET /api/v1/operations/worker-agent-health`, `GET /api/v1/operations/action-required`가 long-running RM Worker 상태를 운영자가 이해할 수 있게 반환한다.
- GPFS backend는 더 이상 `gpfs-filesystem-stub` 성공을 반환하지 않는다. GPFS command capability가 없으면 fail-closed하고, command executor가 주입되면 IBM Storage Scale command를 실행한다.
- GPFS unit tests는 command rendering, parseable `-Y` output parse, quota rounding, failed command, read-back mismatch, import preflight reject를 검증한다.
- GPFS live verification은 testbed에 GPFS가 없으므로 skip evidence로 문서화한다. 단 구현은 staging/production GPFS node에서 실행 가능한 형태여야 한다.
- 검증 결과는 `docs/dms-phase13-verification.md`와 `docs/dms-done.md`에 command/output 포함해 기록한다.

## 현재 전제

Phase 12 완료 후 전제:

- API server는 Kubernetes Deployment로 테스트베드에 배포 가능하다.
- DMS Agent는 c1/c2 worker node에서 DaemonSet으로 실행되며 host-mounted CephFS evidence를 보고한다.
- CLI에는 `dms planner --loop`와 `dms rm-worker --loop`가 존재한다.
- Phase 12 verifier는 `RMWorkerRuntime.run_once()`를 verifier process 안에서 직접 호출한다.
- CephFS backend adapter는 host-mounted worker node에 SSH/sudo로 filesystem create/delete/block/unblock/quota/import/check/sync side effect를 수행한다.
- `BackendAdapterRegistry`는 storage mapping의 `backend_type`에 따라 CephFS 또는 GPFS adapter를 선택한다.
- GPFS backend는 현재 skeleton/stub 수준이며 실제 IBM Storage Scale command side effect를 실행하지 않는다.
- 테스트베드에는 CephFS와 Longhorn은 있지만 GPFS/IBM Storage Scale cluster는 없다.

## 왜 Phase 13에서 RM Worker Deployment를 먼저 하는가

Phase 10-12까지 DMS는 live backend 기능을 검증했지만, 실행 주체는 verifier process였다. 실제 운영에서는 API request가 저장된 뒤 Planner와 RM Worker가 지속 loop로 처리해야 한다.

Phase 13A를 먼저 닫아야 하는 이유:

- request/plan/run/result lifecycle의 lease와 recovery model이 실제 운영 형태로 검증된다.
- Kubernetes Pod restart, stale claim, duplicate claim 방지, partial side effect 후 unknown 상태를 조기에 잡을 수 있다.
- Phase 12 quota/import 기능을 사람이 verifier에서 한 번씩 `run_once()`로 밀어주는 방식에서 벗어난다.
- 이후 Data Management Worker, VolcanoJob, cron/controller형 sweep도 같은 loop/recovery 기반 위에서 구현할 수 있다.

GPFS를 같은 phase에 포함하는 이유:

- Phase 12에서 filesystem quota model과 import/assign-quota contract가 닫혔으므로, CephFS 외 backend를 실제 command adapter로 확장하기 좋다.
- IBM Storage Scale은 directory quota가 아니라 fileset quota를 중심으로 모델링해야 하므로 DMS backend abstraction의 현실성을 검증할 수 있다.
- 테스트베드 GPFS 부재 때문에 live 검증은 skip하지만, command rendering과 parser를 구현하면 staging GPFS 검증 준비가 끝난다.

## API Surface

Phase 13은 Resource Management API surface를 크게 늘리지 않는다. 기존 API를 long-running runtime으로 실행한다.

검증 대상 API:

```text
POST   /api/v1/resource-management/filesystems
PATCH  /api/v1/resource-management/filesystems/{storage_name}/{directory_name}
DELETE /api/v1/resource-management/filesystems/{storage_name}/{directory_name}
POST   /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:block
POST   /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:assign-quota
POST   /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:import
POST   /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:check
POST   /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:sync
POST   /api/v1/resource-management/filesystems:expiration-sweep
GET    /api/v1/operations/action-required
GET    /api/v1/operations/runs/stale
GET    /api/v1/operations/worker-agent-health
```

Phase 13 verifier는 request를 제출한 뒤 다음 상태를 polling한다.

- request terminal status
- plan status
- run state and worker id
- result verification summary
- resource desired/applied/observed state
- diagnostic event correlation id
- action-required issue 생성/해소

## Long-Running Runtime Deployment

### Planner Deployment

Planner는 운영 DB를 바라보는 단일 또는 소수 replica Deployment로 배포한다.

권장 container command:

```yaml
command: ["dms", "planner", "--loop"]
args: ["--interval", "2", "--limit", "25"]
```

요구사항:

- Planner loop는 request를 반복적으로 plan으로 전환해야 한다.
- 한 iteration 실패가 process 전체 종료로 이어지지 않도록 error logging과 backoff를 둔다.
- schema migration은 API 또는 별도 migrate job에서 이미 수행되어야 한다. Planner Deployment가 매 loop마다 heavy migration을 반복하면 안 된다.
- 동일 request에 중복 plan이 생기지 않아야 한다.

### RM Worker Deployment

RM Worker는 Resource Management side effect를 수행하는 long-running Deployment다.

권장 container command:

```yaml
env:
  - name: POD_NAME
    valueFrom:
      fieldRef:
        fieldPath: metadata.name
command: ["dms", "rm-worker"]
args: ["--worker-id", "$(POD_NAME)", "--loop", "--interval", "2"]
```

요구사항:

- CLI worker construction은 settings-aware backend registry를 사용해야 한다.
  - 현재 코드가 test stub registry만 호출하면 CephFS live adapter가 env/settings를 충분히 받지 못할 수 있다.
  - Phase 13 이후 live runtime에서는 `BackendAdapterRegistry.with_live_defaults(repository, settings)`로 통일한다.
- RM Worker Pod는 PostgreSQL operational/observability DB 접속 정보를 Secret/Env로 받아야 한다.
- CephFS host executor를 위해 SSH private key, known_hosts 또는 host alias config를 Secret/ConfigMap으로 mount한다.
- worker id는 Pod마다 고유해야 한다.
- replica를 2개 이상으로 늘려도 DB claim/lease로 동일 plan 중복 실행이 없어야 한다.
- SIGTERM을 받으면 새 plan claim을 중단하고, 현재 plan이 side effect 전이면 안전하게 종료하며, side effect 이후이면 result 또는 recovery evidence를 남긴다.
- long operation 중 heartbeat/lease renewal을 설계한다. 최소한 apply 전, verify 전, complete 전 heartbeat를 기록하고, 후속 phase에서 operation 중 periodic heartbeat로 확장 가능해야 한다.

### Adapter Wiring

Phase 13에서는 RM Worker Deployment가 stub adapter 경로로 성공하면 안 된다.

규칙:

- `backend_type=cephfs` storage mapping은 `CephFsHostMountedFilesystemBackendAdapter`를 사용한다.
- `backend_type=gpfs` storage mapping은 새 GPFS command adapter를 사용한다.
- backend type이 unknown이면 planner 또는 worker가 fail-closed한다.
- live backend capability가 없는 mapping에 write side effect plan을 적용하지 않는다.
- adapter 선택 결과는 run/result에 `adapter`, `backend_type`, `storage_name`, `worker_id`로 기록한다.

## Lease, Stale Claim, Recovery

Phase 13은 long-running runtime의 operational safety를 검증한다.

필수 behavior:

- RM Worker는 loop 시작 시 stale run을 mark한다.
- claim된 run의 lease가 만료되면 `StaleClaim` 또는 recovery 대상 상태가 되어야 한다.
- side effect 전 실패는 `BackendApplyFailed`와 `backend_side_effect=false`로 남긴다.
- side effect 후 exception 또는 Pod kill은 `UnknownAfterSideEffect` 또는 `RecoveryNeeded`로 남겨야 한다.
- recovery가 자동으로 재실행 가능한 operation과 운영자 확인이 필요한 operation을 구분한다.
- duplicate execution이 위험한 operation은 idempotency marker와 live state read-back을 통해 no-op 또는 recovery-needed로 처리한다.

권장 fault injection:

1. RM Worker Deployment replica 1로 정상 create/update/check/sync flow를 검증한다.
2. replica 2로 늘리고 동시에 여러 request를 제출해 중복 claim이 없는지 확인한다.
3. worker lease seconds를 짧게 설정한 테스트 request를 만들고 stale run query가 표시되는지 확인한다.
4. backend precondition failure를 유도해 `BackendApplyFailed`가 action-required로 노출되는지 확인한다.
5. 가능하면 test-only adapter hook으로 apply 이후 verify 이전 exception을 유도해 `UnknownAfterSideEffect` recovery evidence를 확인한다.

## GPFS / IBM Storage Scale Backend Model

IBM Storage Scale은 GPFS 기술 기반이다. DMS 문서와 코드에서는 `backend_type=gpfs`를 유지하되, 사용자-facing 설명에는 `IBM GPFS / IBM Storage Scale`을 함께 표기한다.

Phase 13 GPFS filesystem resource는 **fileset-backed directory**로 구현한다.

원칙:

- DMS `directory_name`은 `fileset_root` 바로 아래의 junction basename이다.
- DMS-created GPFS resource는 fileset을 만들고 junction path에 link한다.
- quota는 GPFS fileset quota로 적용한다.
- quota가 필요한 existing directory import/assign-quota는 일반 directory가 아니라 linked fileset이어야 한다.
- 일반 GPFS directory에 per-directory quota를 적용하는 것처럼 구현하면 안 된다.
- Phase 13은 GPFS user/group quota를 DMS filesystem resource quota로 쓰지 않는다.
- GPFS usage 값은 quota 감소 admission에 사용하지 않는다. Phase 12 filesystem 정책과 동일하게 backend apply 후 read-back으로 검증한다.

### GPFS Storage Mapping

권장 mapping shape:

```json
{
  "storage_name": "gpfs-a",
  "backend_template": {
    "backend_type": "gpfs",
    "filesystem_name": "gpfs0",
    "mount_path": "/gpfs/gpfs0",
    "fileset_root": "/gpfs/gpfs0/dms",
    "quota_scope": "fileset",
    "fileset_name_template": "dms-{directory_name}",
    "rm_worker_nodes": ["gpfs-rm-1"],
    "ssh_host": "gpfs-rm-1",
    "command_runner": "ssh-host-exec",
    "csi_driver": "spectrumscale.csi.ibm.com",
    "data_network": "storage-net-a"
  },
  "cluster_name": "cluster-a",
  "storage_class_name": "gpfs-csi",
  "sanity_status": "Ready"
}
```

Mapping validation:

- `filesystem_name` is required.
- `mount_path` is required.
- `fileset_root` is required for filesystem RM operations.
- `quota_scope` must be `fileset` for Phase 13.
- `fileset_name_template` must render a safe GPFS fileset name from a safe DMS directory basename.
- one executable RM worker node or SSH host must be configured.
- `spectrumscale.csi.ibm.com` remains the default CSI provisioner name for Kubernetes StorageClass mapping.

### IBM Storage Scale Command References

Phase 13 implementation must follow IBM official command semantics.

- `mmcrfileset` creates a fileset. IBM documents that the fileset is not in the namespace until linked, and that create/link separation lets administrators establish policies and quotas before linking.
- `mmlinkfileset` creates a junction path for a fileset and requires root authority.
- `mmlsfileset` displays fileset status and can emit parseable `-Y` output. Avoid `-d` and `-i` in normal check paths because IBM notes those options can be expensive on large filesystems.
- `mmsetquota` sets user, group, or fileset quota limits. Phase 13 uses fileset quota with block and file hard limits.
- `mmlsquota -j Fileset -v -Y Device` reads fileset quota evidence, including limits and current usage. DMS parses limits and records usage only as observed evidence, not as admission policy.
- GPFS quota files exist only when quotas are enabled for the filesystem. `mmlsfs Device -Q` is the capability probe input.
- Fileset quotas require GPFS quota support and, where configured, per-fileset quota support.
- IBM documents that quota limits are not enforced for root users by default. Enforcement verification must use non-root POSIX/LDAP users.
- `mmunlinkfileset` removes a fileset junction. `mmdelfileset` deletion requires unlink first for linked filesets and can be constrained by dependent filesets or snapshots.

Reference URLs:

- IBM Storage Scale `mmcrfileset`: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=reference-mmcrfileset-command>
- IBM Storage Scale `mmlinkfileset`: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=reference-mmlinkfileset-command>
- IBM Storage Scale `mmlsfileset`: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=reference-mmlsfileset-command>
- IBM Storage Scale `mmsetquota`: <https://www.ibm.com/docs/en/storage-scale/5.2.3?topic=reference-mmsetquota-command>
- IBM Storage Scale `mmlsquota`: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=reference-mmlsquota-command>
- IBM Storage Scale filesets and quotas: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=filesets-quotas>
- IBM Storage Scale quota files: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=system-quota-files>
- IBM Storage Scale `mmunlinkfileset`: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=reference-mmunlinkfileset-command>
- IBM Storage Scale `mmdelfileset`: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=reference-mmdelfileset-command>

## GPFS Command Adapter Requirements

### Command Runner

Add a command executor abstraction similar to the CephFS host executor.

```text
GpfsCommandExecutor.run(argv: list[str], timeout_seconds: int) -> CommandResult
```

Rules:

- Use argv lists, not shell string interpolation.
- Support SSH host execution and local/fake execution.
- Capture stdout, stderr, exit code, command argv, duration, timeout flag.
- Do not log secrets.
- Every command result recorded in DMS result must redact host credentials and keep enough evidence for audit.
- Unit tests must use fake executor, not local GPFS commands.

### Capability Probe

Probe commands:

```text
command -v mmcrfileset
command -v mmlinkfileset
command -v mmlsfileset
command -v mmsetquota
command -v mmlsquota
command -v mmunlinkfileset
command -v mmdelfileset
mmlsfs <filesystem_name> -Q -Y
mmlsfs <filesystem_name> --perfileset-quota -Y
mmlsfileset <filesystem_name> -Y
```

Capability output:

```json
{
  "backend_type": "gpfs",
  "quota_backend": "gpfs-fileset-quota",
  "supports_directory_create": true,
  "supports_capacity_quota": true,
  "supports_file_count_quota": true,
  "supports_usage_bytes": false,
  "supports_file_count_usage": false,
  "supports_fileset_create": true,
  "supports_fileset_link": true,
  "supports_fileset_delete": true,
  "supports_permission_mode": true,
  "supports_marker": true,
  "filesystem_name": "gpfs0",
  "fileset_root": "/gpfs/gpfs0/dms",
  "checked_at": "2026-06-01T00:00:00Z"
}
```

Fail-closed cases:

- GPFS command missing.
- command exits non-zero.
- filesystem quota disabled or unknown.
- per-fileset quota unsupported or unknown for a quota operation.
- fileset root missing or outside mount path.
- junction path is unsafe, symlink escape, or not under `fileset_root`.

### Fileset Name and Junction Path

Render:

```text
fileset_name = fileset_name_template.format(directory_name=<safe basename>)
junction_path = fileset_root + "/" + directory_name
```

Rules:

- `directory_name` must already pass DMS basename validation.
- rendered `fileset_name` must match a conservative allowlist such as `[A-Za-z0-9._-]+`.
- rendered `junction_path` must be under `fileset_root`.
- existing path must not be a symlink.
- existing import/assign must resolve to a linked GPFS fileset using `mmlsfileset <fs> -J <junction> -Y`.
- DMS marker remains `.dms-resource.json` at the junction root where POSIX semantics allow it.

### Create

Command flow:

```text
mmlsfileset <fs> <fileset> -Y
mmcrfileset <fs> <fileset> --inode-space new
mmsetquota <fs>:<fileset> --block <soft>:<hard> --files <soft>:<hard>
mmlinkfileset <fs> <fileset> -J <junction_path>
chgrp <group> <junction_path>
chmod <mode> <junction_path>
write .dms-resource.json
mmlsquota -j <fileset> -v -Y <fs>
mmlsfileset <fs> <fileset> -L -Y
```

Rules:

- If quota is present, set quota before linking when possible.
- If quota is absent, create/link still works, but quota state must not pretend quota was applied.
- If create succeeds but link fails, result must include partial fileset state and recovery guidance.
- If link succeeds but permission/marker fails, result must be `UnknownAfterSideEffect` or `RecoveryNeeded`.
- Do not use `mmcrfileset -J` in the first implementation unless rollback semantics are clearly handled. Separate create/quota/link is easier to audit.

### Quota Apply

Phase 13 maps DMS quota to GPFS fileset quota.

```text
capacity_bytes -> GPFS block hard limit
file_count     -> GPFS file hard limit
```

Rendering policy:

- Set soft and hard limit equal to the requested finite quota unless a later policy adds grace semantics.
- Render block limits in `K`, `M`, `G`, or `T` suffix form accepted by `mmsetquota`.
- Prefer exact KiB rendering by rounding up `capacity_bytes` to KiB.
- Record `requested_bytes`, `rendered_block_limit`, and backend read-back values.
- GPFS may round data block limits to filesystem block size. Verification should allow documented backend rounding but must record it explicitly in `quota_state.backend_rounding`.
- File count maps to inode quota. Keep Phase 12 configured max unless GPFS min release capability proves a larger safe limit.

Example command:

```text
mmsetquota gpfs0:dms-project-alpha --block 8192K:8192K --files 32:32
```

Read-back:

```text
mmlsquota -j dms-project-alpha -v -Y gpfs0
```

Rules:

- Parse `-Y` output using a structured parser.
- Convert returned KB block hard limit to bytes.
- Compare read-back hard limit to requested quota under the rounding policy.
- Do not call `mmcheckquota` in normal apply/check paths. It is I/O intensive and belongs to operator-guided recovery.

### Check and Sync

Check:

- read linked fileset status
- read quota state
- compare DMS desired/applied quota with live GPFS quota
- compare permission/marker if requested
- return `Consistent`, `Drifted`, `Missing`, or `CheckFailed`

Sync:

- read live fileset quota
- update DMS desired/applied/observed quota to live state
- do not change GPFS state
- resolve matching drift action-required issue

Filesystem check/sync API still has no usage collection option. GPFS `mmlsquota` may return current usage, but DMS must not expose filesystem `include_usage` or usage-threshold behavior in Phase 13.

### Delete

DMS-created GPFS resource delete flow:

```text
read marker
mmlsfileset <fs> <fileset> -L -Y
mmunlinkfileset <fs> <fileset>
mmdelfileset <fs> <fileset>
```

Rules:

- Only DMS-created full-managed filesets may be backend-deleted.
- Quota-only assigned filesets must not be deleted by DMS delete.
- Imported full-managed fileset deletion must be explicit policy-gated. Phase 13 may reject backend delete for imported filesets unless `delete_policy=delete_imported_fileset` is explicitly introduced and tested.
- Do not use force unlink by default.
- If snapshots, dependent filesets, open files, or GPFS constraints prevent deletion, fail with recovery evidence instead of masking success.

### Block and Unblock

Block/unblock for GPFS uses the same DMS POSIX permission model as CephFS.

Rules:

- Validate junction path maps to expected fileset before chmod.
- Preserve previous mode/block state in applied state.
- `block_mode=chmod-0000` is supported.
- Restore previous mode on unblock.
- Do not change GPFS quota during block/unblock.

### Import and Assign-Quota

Assign-quota:

- target junction path must already exist.
- target must be a linked GPFS fileset.
- DMS writes `management_mode=quota_only` marker if requested.
- DMS applies fileset quota and records read-back.
- DMS does not change owner/group/mode.

Full import:

- target junction path must already exist.
- target must be a linked GPFS fileset.
- access policy must be explicit, matching Phase 12 import semantics.
- DMS records fileset name, junction path, owner/group/mode, marker, quota state.
- import must not silently overwrite permission, ownership, or quota.

Reject:

- ordinary GPFS directory that is not a fileset junction when quota is requested.
- nested path outside `fileset_root`.
- symlink escape.
- fileset name mismatch.
- marker mismatch.
- unresolved LDAP/SSSD group.

## GPFS Quota State Shape

Use the Phase 12 backend-neutral quota model and add GPFS details under backend metadata.

```json
{
  "quota": {
    "capacity_bytes": 8388608,
    "file_count": 32
  },
  "quota_state": {
    "backend_type": "gpfs",
    "quota_backend": "gpfs-fileset-quota",
    "fileset_name": "dms-project-alpha",
    "junction_path": "/gpfs/gpfs0/dms/project-alpha",
    "capacity": {
      "desired_bytes": 8388608,
      "applied_bytes": 8388608,
      "observed_bytes": 8388608,
      "backend_key": "blockLimit",
      "rendered_limit": "8192K:8192K"
    },
    "file_count": {
      "desired_count": 32,
      "applied_count": 32,
      "observed_count": 32,
      "backend_key": "filesLimit",
      "rendered_limit": "32:32"
    },
    "backend_rounding": {
      "capacity_unit": "KiB",
      "rounded_up": false
    }
  }
}
```

## Planner Guard

Phase 13 Planner requirements:

Long-running runtime:

- request planning remains idempotent.
- active plan/run conflict checks must work with Deployment loops.
- stale request/run states must be visible to operators.

GPFS:

- `backend_type=gpfs` filesystem create/update/delete/check/sync/import/assign-quota must use GPFS backend capability.
- quota operation requires `quota_scope=fileset`.
- quota operation requires GPFS quota capability from mapping, live probe, or explicit cached readiness.
- ordinary directory quota is unsupported.
- unsafe fileset name and junction path are rejected.
- imported/quota-only GPFS delete policy is fail-closed.

## Worker Behavior

Operation별 RM Worker behavior:

| Operation | Long-running RM Worker behavior |
| --- | --- |
| `filesystem.create` CephFS | Deployment worker claims plan and runs existing CephFS host adapter |
| `filesystem.update` CephFS quota | Deployment worker applies quota without usage admission and verifies read-back |
| `filesystem.import/assign_quota` CephFS | Deployment worker executes Phase 12 import/assign flow |
| `filesystem.check/sync` CephFS | Deployment worker records check/sync result without direct verifier call |
| `filesystem.create` GPFS | command adapter creates fileset, quota, junction, permission, marker |
| `filesystem.update` GPFS quota | command adapter runs fileset quota apply/read-back |
| `filesystem.delete` GPFS | command adapter unlinks/deletes only DMS-created full-managed fileset |
| `filesystem.import/assign_quota` GPFS | command adapter validates existing linked fileset and applies policy |
| `filesystem.check/sync` GPFS | command adapter reads fileset/quota state and records drift or sync |

Worker result must include:

- `worker_id`
- `adapter`
- `backend_type`
- `storage_name`
- `operation`
- `backend_side_effect`
- `command_evidence` for GPFS, redacted
- `quota_state` where relevant
- `fileset_state` for GPFS
- `marker`
- `path` or `junction_path`
- `verification_summary.issues`
- `recovery_required` when side effect state is unclear

## Action-Required

Phase 13 should add or verify these issue types:

```text
rm_worker_deployment_unhealthy
rm_worker_stale_claim
rm_worker_recovery_needed
rm_worker_unknown_after_side_effect
rm_worker_repeated_backend_failure
gpfs_command_missing
gpfs_filesystem_quota_disabled
gpfs_perfileset_quota_disabled
gpfs_fileset_missing
gpfs_fileset_unlinked
gpfs_fileset_junction_mismatch
gpfs_quota_apply_failed
gpfs_quota_verification_failed
gpfs_import_preflight_failed
gpfs_delete_policy_refused
```

Existing Phase 12 filesystem quota issue types remain valid for backend-neutral query surfaces.

## Testbed Live Verification

Phase 13A live verification runs on the existing Vagrant multi-cluster testbed.

Target:

```text
cluster-a/c1-worker: /mnt/testbed-cephfs
cluster-b/c2-worker: /mnt/testbed-cephfs-c2
```

Verifier requirements:

- Prepare fresh PostgreSQL operational/observability DBs.
- Deploy DMS API.
- Deploy DMS Agent DaemonSets on c1/c2.
- Deploy Planner Deployment.
- Deploy RM Worker Deployment.
- Submit storage mappings for `cephfs-a`, `cephfs-b`.
- Wait for agent reports and RM readiness.
- Submit Phase 12 quota lifecycle requests through API only.
- Do not call `Planner.run_once()` or `RMWorkerRuntime.run_once()` in the verifier.
- Poll request terminal state.
- Verify backend CephFS xattrs and directory permissions over SSH.
- Scale RM Worker Deployment to 2 replicas and verify multiple requests do not duplicate side effects.
- Delete one RM Worker Pod during a controlled test and verify stale/recovery behavior.
- Cleanup namespace, test directories, LDAP fixtures.

Recommended script:

```bash
cd /home/mason/workspace/dms
scripts/verify-phase13-testbed.sh
```

`DMS_PHASE13_SKIP_IMAGE_BUILD=1` may be used only after the `phase13` image has
already been built and pushed with the Phase 13 Dockerfile. The RM Worker image
needs `openssh-client` for the CephFS `ssh-host-exec` path.

The verifier may reuse Phase 12 helper functions, but the processing path must be Kubernetes loop-based.

## GPFS Verification Without Testbed GPFS

Because the current testbed has no IBM Storage Scale cluster, Phase 13 GPFS verification is split:

### Required local regression

- fake GPFS executor renders expected commands for create/update/delete/check/sync/import/assign-quota.
- parser handles representative `mmlsfileset -Y`, `mmlsfs -Q -Y`, `mmlsfs --perfileset-quota -Y`, `mmlsquota -j -v -Y` outputs.
- missing command fails capability probe.
- quota disabled fails before side effect.
- per-fileset quota disabled fails before quota side effect.
- read-back mismatch returns verification failure.
- command timeout returns backend failure with command evidence.
- import rejects ordinary directory and unlinked fileset.
- delete refuses imported/quota-only fileset by default.
- GPFS Kubernetes namespace quota mapping keeps existing CSI ResourceQuota behavior.

### Optional staging checklist

If a GPFS staging node is later available, run:

```bash
ssh gpfs-rm-1 "command -v mmcrfileset && command -v mmlinkfileset && command -v mmsetquota && command -v mmlsquota"
ssh gpfs-rm-1 "mmlsfs gpfs0 -Q -Y"
ssh gpfs-rm-1 "mmlsfs gpfs0 --perfileset-quota -Y"
ssh gpfs-rm-1 "mmlsfileset gpfs0 -Y"
```

Then execute a small fileset create/quota/link/write/delete fixture with a non-root test user. Staging output should be recorded in a future `docs/dms-phase13-gpfs-staging.md` or appended to Phase 13 verification.

## Local Regression Tests

Minimum tests:

- `dms rm-worker --loop` construction passes settings into backend registry.
- Planner loop and RM Worker loop can process multiple queued requests in background test threads or subprocesses.
- two RM Worker instances do not claim the same plan.
- stale run is exposed through operational query.
- backend precondition failure appears in action-required.
- Phase 12 filesystem quota lifecycle works through loop helpers without direct `run_once()` calls.
- GPFS command adapter creates expected command sequence for fileset create with quota.
- GPFS command adapter parses fileset/quota read-back.
- GPFS quota apply allows backend rounding and records rounding evidence.
- GPFS check reports drift on changed live quota.
- GPFS sync accepts live quota into DMS state.
- GPFS import requires linked fileset.
- GPFS delete refuses quota-only and imported resources by default.
- GPFS command failures include redacted command evidence.

Recommended command:

```bash
cd /home/mason/workspace/dms
pytest
```

## Verification Artifact

Phase 13 완료 시 `docs/dms-phase13-verification.md`를 작성한다.

반드시 포함할 내용:

- 실행 날짜와 working tree 상태
- pytest 결과
- Phase 13 live verifier command
- PostgreSQL operational/observability DB 이름
- DMS API Deployment evidence
- Planner Deployment evidence
- RM Worker Deployment evidence
- RM Worker Pod ids and worker ids
- c1/c2 Agent report ids
- storage mapping readiness
- request ids for long-running create/update/check/sync/import/assign/delete flow
- run ids and worker ids that processed each request
- diagnostic event excerpts
- worker scale/restart/stale test evidence
- action-required before/after evidence
- cleanup evidence
- GPFS live verification skip reason
- GPFS unit test/fake executor coverage summary
- IBM Storage Scale command reference links used for implementation

## Phase 13에서 하지 않을 것

다음은 Phase 13 범위가 아니다.

- Data Management `scan/sync/rm` live execution
- VolcanoJob create/watch/terminate
- DM Worker long-running runtime 검증
- automatic quota drift cron/controller
- automatic expiration sweep cron/controller
- production Helm chart 완성
- GPFS live testbed 구축
- GPFS AFM, snapshot, ILM, policy engine integration
- GPFS user/group quota를 DMS filesystem quota로 일반화
- WekaFS/Lustre live implementation
- filesystem usage collection API 부활
- Kubernetes namespace quota usage pressure 변경

## Phase 13 완료 후 다음 Phase 후보

Phase 13이 성공하면 DMS는 Kubernetes 안의 long-running Planner/RM Worker loop로 Phase 10-12 CephFS Resource Management 기능을 처리하고, GPFS backend는 실제 IBM Storage Scale command adapter 구현을 갖춘 상태가 된다.

다음 후보:

### Phase 14: Runtime Hardening Before Data Management

Data Management 구현 전에 Resource Management와 DMS runtime 기본 구현의 운영 안정성 gap 중 observability write boundary와 backend selection 문제를 먼저 닫는다.

- observability write failure가 core lifecycle을 깨뜨리지 않도록 safe write boundary 적용
- backend registry stub fallback 제거, unknown backend fail-closed, Kubernetes namespace quota live adapter wiring 보강

구체 구현 프롬프트는 `docs/dms-phase14.md`를 따른다.

### Phase 15: Resource Expiry Update, Import Defaults, and Kubernetes Namespace Quota Expiry Lifecycle

Phase 14 hardening 이후 filesystem resource와 Kubernetes namespace quota resource의 existing resource expiry update/import semantics를 닫고, Kubernetes namespace quota resource에도 filesystem resource와 같은 expiry lifecycle을 추가한다.

- filesystem resource update payload에서 `expires_at`/`expiry_at`/`clear_expires_at`으로 expiry timestamp 설정/변경/해제
- Kubernetes namespace quota create/update/default-reset payload에서 expiry timestamp 설정/변경/해제
- filesystem import와 Kubernetes namespace quota import/adoption에서 expiry timestamp가 있으면 요청값 사용, 없으면 server-side now + 365일 default 설정
- `expiry_at` alias를 API에서 받아 canonical `expires_at`으로 normalize
- write request의 과거/current expiry timestamp, timezone 없는 timestamp, alias conflict를 backend side effect 없이 reject
- import request의 `clear_expires_at=true`를 backend side effect 없이 reject
- expired/expiring Kubernetes namespace quota query API
- expired but unblocked quota action-required aggregation
- on-demand expiration sweep으로 DMS-managed `ResourceQuota/dms-storage-quota` block 처리

구체 구현 프롬프트는 `docs/dms-phase15.md`를 따른다.

### Phase 16A: Data Management Read-only Scan Preflight

- filesystem resource boundary를 read-only scan target으로 사용
- DM Agent report 기반 candidate pool
- POSIX identity/mount/tool preflight
- VolcanoJob 이전 local scan preflight 검증

### Phase 16B: DM Worker Runtime and VolcanoJob Skeleton

- `dms dm-worker --loop` Deployment
- VolcanoJob create/watch/delete skeleton
- job lease/recovery
- artifact URI and preview lifecycle

### Phase 16C: Filesystem Policy and Initialize

- filesystem default quota policy
- `filesystem.initialize`
- `reset_quota_to_default=true`
- quota clear/unlimited lifecycle

권장 순서는 Phase 14로 runtime hardening을 먼저 닫고, Phase 15로 resource expiry update/import default와 Kubernetes namespace quota expiry lifecycle을 구현한 뒤, Phase 16A로 Data Management read-only scan preflight를 구현하는 것이다.
