# DMS Storage Backend Extension Guide

이 문서는 DMS에 CephFS, GPFS 외의 새 backend storage를 추가할 때 따라야 하는 구현 절차와 검증 기준을 정리한다. 예시는 WEKA를 기준으로 들지만, Lustre, BeeGFS, NFS appliance 같은 다른 shared filesystem backend에도 같은 원칙을 적용한다.

## Current Backend Shape

DMS backend 확장은 세 경로로 나뉜다.

1. Filesystem Resource Management
   - `resource_kind=filesystem` 요청을 처리한다.
   - `storage_name + directory_name`이 resource identity다.
   - backend adapter가 directory/fileset 생성, quota 적용, permission block, check, sync, import, delete를 수행한다.
   - 현재 live 구현은 CephFS host-mounted adapter와 GPFS command adapter다.

2. Kubernetes Namespace Quota Resource Management
   - `resource_kind=kubernetes_namespace_quota` 요청을 처리한다.
   - `cluster_name + namespace_name`이 resource identity다.
   - 대부분의 CSI backend는 Kubernetes `ResourceQuota/dms-storage-quota`를 generic live adapter로 처리할 수 있다.
   - backend native filesystem quota와 Kubernetes `ResourceQuota`는 혼동하면 안 된다.

3. Data Management Worker Pool Metadata
   - `data.scan`, `data.sync`, `data.rm` planning에서 worker pool 후보, mount path, tool candidate, data network 같은 metadata를 제공한다.
   - live Data Management 실행은 storage backend adapter와 별개로 Volcano/mpifileutils runtime에서 처리한다.

새 backend는 필요한 경로만 구현한다. 예를 들어 Kubernetes namespace quota만 필요하면 generic CSI ResourceQuota backend로 등록할 수 있고, filesystem directory quota까지 필요하면 filesystem adapter를 추가해야 한다.

## Required Design Decisions

구현 전에 backend별 capability를 먼저 결정한다.

- `backend_type`: lowercase stable identifier. 예: `weka`.
- filesystem resource model: directory, fileset, project, volume, share 중 무엇을 DMS `directory_name`으로 매핑할지 정한다.
- quota primitive: capacity quota, file-count quota, inode quota, soft/hard limit, quota unit, rounding rule.
- access control primitive: POSIX group/mode, ACL, backend-native policy 중 어떤 것을 DMS `users`, `access_group`, `mode`, block/unblock에 매핑할지 정한다.
- command/API runner: RM worker local command, SSH host command, REST API, vendor SDK 중 하나를 선택한다.
- read-back verification: apply 후 live state를 어떤 command/API로 검증할지 정한다.
- import/adoption policy: 기존 directory를 full-managed 또는 quota-only managed resource로 받을 수 있는지 정한다.
- delete policy: DMS-created resource만 삭제할지, imported resource는 fail-closed할지 정한다.
- Kubernetes CSI behavior: `StorageClass` provisioner, DMS `ResourceQuota` 지원 여부, backend-specific StorageClass parameter validation 필요 여부.
- Data Management behavior: DM worker가 어떤 mount path, network, tool 후보를 필요로 하는지 정한다.

지원하지 않는 capability는 조용히 성공시키지 말고 `BackendPreconditionError`로 fail-closed해야 한다. 특히 DMS 공통 quota payload의 `file_count`를 backend가 지원하지 않으면 planner 또는 adapter에서 명시적으로 reject해야 한다.

## Storage Mapping Template

새 backend는 운영자가 `storage_name` mapping으로 등록한다. template field는 backend adapter가 해석하며, 공통적으로 다음 필드를 권장한다.

```json
{
  "storage_name": "weka-a",
  "backend_template": {
    "backend_type": "weka",
    "filesystem_name": "default",
    "mount_path": "/weka/default",
    "managed_root": "/weka/default/dms",
    "quota_scope": "directory",
    "rm_worker_nodes": ["weka-rm-1"],
    "ssh_host": "weka-rm-1",
    "command_runner": "ssh-host-exec",
    "command_timeout_seconds": 300,
    "csi_driver": "csi.weka.io",
    "data_network": "storage-net-a"
  },
  "cluster_name": "cluster-a",
  "storage_class_name": "weka-sc"
}
```

Template field guidance:

- `backend_type`: filesystem/Data Management adapter dispatch key. 새 backend의 filesystem 또는 DM adapter를 추가할 때 live registry에 등록한다. Kubernetes namespace quota만 사용할 CSI backend는 Phase 17 이후 별도 adapter registry 등록이 필요 없다.
- `filesystem_name`: backend filesystem, device, namespace, tenant, 또는 cluster-specific filesystem identifier.
- `mount_path`: command runner에서 보이는 mounted filesystem root.
- `managed_root`: DMS가 직접 생성하는 filesystem resource의 parent directory. 반드시 `mount_path` 아래여야 한다.
- `quota_scope`: backend-specific quota target. 예: `directory`, `fileset`, `project`.
- `command_runner`: `local`, `ssh-host-exec`, 또는 backend adapter가 명시적으로 지원하는 값.
- `ssh_host` / `rm_worker_nodes`: `ssh-host-exec` 사용 시 command를 실행할 host.
- `command_timeout_seconds`: quota accounting 또는 read-back command가 오래 걸릴 수 있는 backend는 충분히 크게 잡는다.
- `csi_driver`: Kubernetes StorageClass sanity와 namespace quota ResourceQuota mapping에 사용한다.
- `data_network`: Data Management worker pool metadata에 사용한다.

## Filesystem Adapter Implementation

새 filesystem backend는 `src/dms/backends/<backend>.py`에 둔다.

필수 구성:

- `BACKEND_TYPE` constant. 예: `WEKA_BACKEND_TYPE = "weka"`.
- backend template dataclass. 예: `WekaBackendTemplate.from_storage_mapping(mapping)`.
- command/API executor abstraction. Fake executor unit test를 위해 protocol을 분리한다.
- quota renderer/strategy. DMS common quota model을 backend-native quota command/API로 변환한다.
- `FilesystemBackendAdapter` protocol 구현체.

Adapter method contract:

| Method | Required behavior |
| --- | --- |
| `create(plan)` | DMS-managed directory/fileset/project를 만들고 optional quota, access group/mode, marker를 적용한다. |
| `update(plan)` | quota 또는 metadata update를 적용한다. expiry-only update를 지원하지 않으면 side effect 없이 명확히 fail-closed한다. |
| `block(plan)` | block=true이면 access 차단 또는 quota-zero 등 backend별 block policy를 적용하고, block=false이면 restore state로 복구한다. |
| `delete(plan)` | DMS-created full-managed resource만 삭제한다. imported/quota-only resource는 기본적으로 fail-closed한다. |
| `consistency_check(plan)` | live state를 read-only로 조회하고 DB desired state와 비교해 `Consistent`, `Drifted`, `Missing`, `ActionRequired` 계열 evidence를 남긴다. |
| `sync_live_state(plan)` | live quota state를 DB desired state로 수용한다. backend state를 변경하지 않는다. |
| `import_directory(plan)` | 기존 directory/fileset/project를 full-managed resource로 편입한다. ownership marker와 access/quota sanity를 검증한다. |
| `assign_quota_only(plan)` | 기존 target에 quota-only management를 적용한다. lifecycle ownership 없이 quota desired state와 marker를 추적한다. |
| `initialize(plan)` | 현재 planner에서는 filesystem initialize가 열려 있지 않다. 구현체는 check로 위임하거나 unsupported로 fail-closed한다. |

Adapter result는 `AdapterResult`를 반환해야 한다.

- `applied_state`: 실제 적용한 side effect와 backend metadata.
- `observed_state`: read-back verification 결과, issue, status.
- `message`: result에 기록될 사람이 읽을 수 있는 요약.
- `artifact_uri`: 큰 command output이나 report가 있으면 artifact URI만 저장한다.

Side effect safety:

- side effect 전 capability check를 수행한다.
- command missing, quota disabled, unsafe path, unsupported quota field는 side effect 없이 `BackendPreconditionError`로 끝낸다.
- command가 일부 성공한 뒤 read-back verification이 실패하면 일반 성공으로 기록하지 않는다. recovery/action-required가 가능하도록 exception과 command evidence를 남긴다.
- command evidence에는 argv, return code, stderr/stdout tail, timeout 여부, side-effect 여부를 남기되 secret은 기록하지 않는다.

## Registry Wiring

`src/dms/backend_registry.py`를 수정한다.

Filesystem path:

```python
if backend_type == WEKA_BACKEND_TYPE:
    return WekaFilesystemBackendAdapter.from_storage_mapping(mapping)
```

Kubernetes namespace quota path:

- 새 CSI backend를 추가해도 Kubernetes namespace quota adapter allowlist에는 등록하지 않는다.
- Phase 17 이후 Kubernetes namespace quota는 backend type과 무관하게 공통 `KubernetesNamespaceQuotaLiveAdapter`를 사용한다.
- backend-specific filesystem quota adapter를 Kubernetes namespace quota path에서 호출하면 안 된다.

Data Management path:

```python
if self._backend_type(mapping) == WEKA_BACKEND_TYPE:
    template = WekaBackendTemplate.from_storage_mapping(mapping)
    return WekaDataManagementAdapter(template).worker_pool(storage_name)
```

Live filesystem registry는 unknown backend를 stub으로 fallback하지 않는다. filesystem adapter registry 등록 누락은 `unsupported_backend`로 실패해야 한다. Kubernetes namespace quota path는 backend type과 무관하게 공통 live ResourceQuota adapter를 사용한다.

## Inventory, Sanity, and Agent Updates

`src/dms/inventory.py`:

- `_default_csi_driver()`에 backend default CSI driver를 추가한다. WEKA는 일반적으로 `csi.weka.io`를 사용한다.
- backend-specific StorageClass parameter를 검증해야 하면 sanity check에 warning/error를 추가한다.

`src/dms/agent_daemon.py`와 deployment config:

- Agent `storages.json`에 새 storage entry를 추가한다.
- filesystem RM 또는 DM에 host mount가 필요하면 `mount_paths`를 반드시 넣는다.
- backend CLI가 필요하면 `DMS_AGENT_TOOLS`에 tool 이름을 추가한다.
- RM readiness는 target cluster의 RM Agent evidence를 기준으로 판단한다.
- DM readiness는 control cluster DM Agent evidence를 기준으로 판단한다.

WEKA 예시:

```json
{
  "storage_name": "weka-a",
  "backend_type": "weka",
  "cluster_name": "cluster-a",
  "storage_class_name": "weka-sc",
  "csi_driver": "csi.weka.io",
  "mount_paths": ["/weka/default"],
  "network_endpoints": ["weka-mgmt.example.com:14000"]
}
```

## Planner and API Changes

가능하면 새 backend 추가만으로 public request schema를 바꾸지 않는다. DMS core request model은 backend-neutral이어야 한다.

기존 common payload를 우선 사용한다.

- filesystem create/update quota: `quota.capacity_bytes`, `quota.file_count`
- filesystem expiry: `expires_at`
- Kubernetes namespace quota: `quota.requests_storage_bytes`, `quota.pvc_count`, `storage_class_quotas[].storage_name`
- check/sync/import/assign-quota common fields

Planner 변경이 필요한 경우:

- backend가 특정 common quota field를 지원하지 않는 경우 reject reason을 추가한다.
- backend-specific required payload는 가능하면 `backend_template`에 둔다.
- `expiry_at`, `clear_expires_at` 같은 대체 expiry field를 새 backend에서 다시 열지 않는다. API/DB/response field는 `expires_at`으로 통일한다.

## Kubernetes Namespace Quota Behavior

CSI backend가 Kubernetes `ResourceQuota`로 PVC request storage와 PVC count를 제한할 수 있으면 generic live adapter를 사용한다.

이 경우 새 backend implementation은 다음을 보장해야 한다.

- `storage_mapping.cluster_name`과 request `cluster_name`이 일치한다.
- `storage_mapping.storage_class_name`이 request entry의 derived StorageClass로 사용된다.
- `StorageClass.provisioner`가 expected `csi_driver`와 일치한다.
- `ResourceQuota/dms-storage-quota`는 DMS labels/annotations를 유지한다.
- live `ResourceQuota` missing/drift는 check/audit/action-required에 노출된다.

Backend-native filesystem quota와 Kubernetes namespace quota는 독립 경로다. 예를 들어 WEKA directory quota command를 Kubernetes namespace quota apply path에서 실행하면 안 된다.

## Data Management Worker Pool

새 backend가 Data Management 대상이면 `DataManagementStorageAdapter` 형태의 worker pool metadata를 제공한다.

권장 shape:

```python
{
    "selection": "agent-inventory",
    "backend_type": "weka",
    "required_mounts": [storage_name],
    "mount_path": template.mount_path,
    "filesystem_name": template.filesystem_name,
    "data_network": template.data_network,
    "tool_candidates": ["dsync", "nsync", "drm", "dscan"],
    "requires_posix_identity": True,
    "candidates": [],
}
```

DM worker pool metadata는 planning/scheduling input이다. 실제 data movement command execution은 DM Worker/Volcano/mpifileutils runtime에서 별도로 검증한다.

## Test Requirements

새 backend는 live testbed가 없더라도 fake executor regression을 먼저 추가해야 한다.

Minimum unit coverage:

- storage mapping template parsing.
- quota renderer command/API payload rendering.
- safe path validation: basename, managed root escape, unsafe backend target name.
- create command/API sequence and read-back verification.
- update quota apply and read-back verification.
- expiry-only update behavior.
- check reports `Consistent`, `Drifted`, and `Missing`.
- sync copies live quota into `synced_desired_state` without backend side effect.
- import requires existing backend target and validates marker/access policy.
- assign-quota-only applies quota without taking delete ownership.
- delete refuses imported/quota-only resources.
- missing command/API capability fails closed before side effect.
- unsupported quota fields fail closed.
- post-side-effect read-back mismatch becomes recovery/action-required, not success.
- registry selects the backend for filesystem plans.
- registry does not select filesystem adapter for Kubernetes namespace quota plans.
- Kubernetes namespace quota path uses the generic live adapter for every CSI backend.
- Data Management planning records backend worker pool metadata.

Recommended command:

```bash
pytest -q tests/test_<backend>_backend.py tests/test_phase14_runtime_hardening.py
```

If the testbed has the backend:

- add `scripts/phaseXX_<backend>_backend.py`.
- add `scripts/verify-phaseXX-testbed.sh`.
- record live evidence in `docs/dms-phaseXX-verification.md`.
- update `/home/mason/workspace/testbed` metadata docs when new software, mounts, credentials, or cluster objects are installed.

If the testbed does not have the backend:

- document the live skip reason.
- keep fake executor regression mandatory.
- do not mark live backend validation complete.

## Documentation Requirements

For each backend, add a dedicated backend document under `docs/`.

Required sections:

- storage mapping template and required fields.
- filesystem operations and supported lifecycle ownership modes.
- quota model and DMS common quota field mapping.
- unsupported fields and fail-closed behavior.
- Kubernetes namespace quota behavior.
- Data Management worker pool metadata.
- local regression command.
- live validation prerequisites and current verification status.

`docs/backend-gpfs.md` is the reference format for backend-specific documentation.

## WEKA Implementation Notes

WEKA should be added as `backend_type: "weka"`.

Likely implementation shape:

- `src/dms/backends/weka.py`
- `WekaBackendTemplate`
- `WekaCommandExecutor`
- `WekaQuotaStrategy`
- `WekaFilesystemBackendAdapter`
- optional `WekaDataManagementAdapter`

WEKA-specific decisions to close before implementation:

- exact quota command/API and output parser.
- whether `file_count` quota is supported; if not, reject `quota.file_count`.
- whether DMS should create plain directories under `managed_root` or require a backend-native filesystem/subdirectory primitive.
- whether import/adoption requires existing WEKA quota metadata.
- whether block/unblock should use POSIX mode, ACL, quota-zero, or a backend-native policy.
- whether delete should remove directories or only DMS-created marker/quota state.
- whether WEKA StorageClass parameters need sanity validation beyond `csi_driver` and StorageClass provisioner.

Until these decisions are implemented and verified, `backend_type=weka` must remain fail-closed in live worker runtime.
