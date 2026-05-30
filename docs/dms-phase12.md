# DMS Phase 12 Implementation Prompt

이 문서는 `docs/dms-phase11.md` 완료 이후 열두 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 12의 목표는 Phase 11에서 검증한 **host-mounted CephFS filesystem create/delete, expiry query, API-driven sweep, block/unblock lifecycle** 위에 다음 두 범위를 함께 구현하고 실제 테스트베드에서 검증하는 것이다.

- **Phase 12A: Filesystem Quota Lifecycle**
- **Phase 12B: Existing Directory Import / Assign Quota**

중요: Phase 12는 Kubernetes PVC 내부 directory quota가 아니다. `docs/dms-design.md` 기준 filesystem resource는 `storage_name + directory_name`으로 식별하며, dedicated RM Worker node에 host-mounted 된 shared filesystem의 storage root 아래 directory를 관리한다. Phase 12의 live backend target은 Phase 10/11과 동일하게 c1/c2 worker node host-mounted CephFS다.

Phase 12는 long-running RM Worker Kubernetes Deployment/loop 운영 검증, Data Management `scan/sync/rm` live execution, VolcanoJob 실행, GPFS/WekaFS/Lustre live implementation을 열지 않는다. 이번 phase는 CephFS quota primitive를 DMS filesystem RM 공통 모델에 연결하고, 기존 directory를 안전하게 DMS quota 관리 대상으로 편입하는 경로를 먼저 닫는다.

## Phase 12 목표

Phase 12의 핵심 기능은 다음 두 묶음이다.

### Phase 12A: Filesystem Quota Lifecycle

1. **Filesystem quota capability probe**
2. **CephFS directory quota xattr apply**
3. **Finite quota create/update**
4. **Quota decrease guard**
5. **Quota check/sync**
6. **Quota drift/usage pressure action-required**

### Phase 12B: Existing Directory Import / Assign Quota

1. **Existing directory quota-only assignment**
2. **Existing directory full import**
3. **DMS marker initialize**
4. **Existing directory ownership/permission/quota preflight**
5. **Import 후 quota assignment/update**
6. **Non-DMS directory safety guard**

구현 완료 기준은 다음과 같다.

- filesystem create에서 명시 finite quota가 있으면 directory create 후 CephFS quota xattr가 실제 적용된다.
- filesystem update에서 quota 증가가 실제 CephFS quota xattr에 반영된다.
- filesystem update에서 quota 감소 요청이 live usage보다 작으면 backend side effect 없이 reject된다.
- quota check가 DB desired/applied quota와 live CephFS quota/usage를 read-only 비교하고 `Consistent`, `Drifted`, `Missing`, `CheckFailed`를 기록한다.
- quota sync가 live CephFS quota state를 DMS DB desired/applied/observed state로 수용한다. sync는 filesystem xattr를 변경하지 않는다.
- latest check/sync result 기준으로 quota drift, missing, check failure, usage pressure가 `GET /api/v1/operations/action-required`에 집계된다.
- 기존 unmanaged directory에 quota-only assignment를 수행할 수 있다.
- quota-only assignment는 directory lifecycle과 access control 전체를 DMS가 소유하지 않으며, quota state만 DMS DB와 marker에 기록한다.
- 기존 directory full import는 directory 존재, storage root boundary, symlink/bind escape, marker conflict, owner/group/mode, LDAP group 해석, quota capability를 preflight한 뒤 DMS-managed resource로 전환한다.
- import는 명시 정책 없이 기존 permission/ownership/quota를 조용히 덮어쓰지 않는다.
- import 또는 assign-quota 후에도 최소 2명의 허용 LDAP user와 1명의 비허용 LDAP user로 접근 경계를 검증한다.
- Phase 12 live verification은 `cluster-a/c1-worker`와 `cluster-b/c2-worker`의 host-mounted CephFS에서 모두 수행한다.
- 검증 결과는 `docs/dms-phase12-verification.md`와 `docs/dms-done.md`에 command/output 포함해 기록한다.

## 현재 전제

Phase 11 완료 후 전제:

- filesystem create/delete가 실제 host-mounted CephFS backend side effect를 수행한다.
  - `POST /api/v1/resource-management/filesystems`
  - `DELETE /api/v1/resource-management/filesystems/{storage_name}/{directory_name}`
- filesystem block/unblock이 실제 host-mounted CephFS permission state를 변경한다.
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:block`
- expiration sweep은 API 요청으로만 실행된다.
  - `POST /api/v1/resource-management/filesystems:expiration-sweep`
- expired/expiring filesystem resource query가 있다.
  - `GET /api/v1/operations/filesystems/expiring`
- filesystem update, initialize, assign-quota, import, check endpoint skeleton은 존재하지만 Phase 11까지는 quota/import side effect가 unsupported다.
  - `PATCH /api/v1/resource-management/filesystems/{storage_name}/{directory_name}`
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:initialize`
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:assign-quota`
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:import`
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:check`
- Phase 12에서 filesystem sync endpoint가 없다면 추가한다.
  - 권장: `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:sync`
- CephFS host adapter는 create/delete/block/unblock만 live 구현되어 있다.
- Phase 10/11 live verification은 다음 target을 사용했다.
  - `cluster-a/c1-worker`: `/mnt/testbed-cephfs`
  - `cluster-b/c2-worker`: `/mnt/testbed-cephfs-c2`
- DMS Agent report, storage mapping sanity, OpenLDAP/SSSD, PostgreSQL-backed request/plan/result state는 실제 테스트베드에서 검증됐다.
- long-running RM Worker Kubernetes Deployment/loop는 아직 운영형 live verification 대상이 아니다. Phase 12도 기존 verifier가 `RMWorkerRuntime.run_once()`를 호출하는 방식으로 검증한다.

## 왜 Phase 12A와 12B를 함께 하는가

Filesystem quota lifecycle만 구현하면 DMS가 직접 만든 새 directory에는 quota를 걸 수 있지만, 운영 환경에 이미 존재하는 directory를 DMS 관리 대상으로 편입할 수 없다. 반대로 import/assign-quota만 구현하고 quota lifecycle을 닫지 않으면 import 후 실제 quota 운영, drift 확인, usage pressure 대응이 불가능하다.

Phase 12에서 두 축을 함께 묶는 이유:

- 기존 directory import와 quota assignment는 모두 live quota capability와 quota apply primitive가 필요하다.
- quota check/sync는 새 DMS-created directory와 기존 imported directory 양쪽에 공통으로 필요하다.
- non-DMS directory safety guard를 quota lifecycle과 동시에 검증해야 나중에 Data Management로 넘어가기 전에 resource boundary가 명확해진다.
- c1/c2 host-mounted CephFS가 이미 Phase 10/11에서 검증됐으므로 같은 testbed에서 quota primitive와 import preflight를 추가 검증하기 좋다.

## API Surface

### Filesystem Create With Quota

기존 create endpoint에 explicit finite quota payload를 허용한다.

```text
POST /api/v1/resource-management/filesystems
```

요청 예시:

```json
{
  "requester_id": "portal:alice",
  "payload": {
    "storage_name": "cephfs-a",
    "directory_name": "phase12-quota-a",
    "users": ["alice", "bob"],
    "access_group": "dms-phase12-phase12-quota-a",
    "resource_type": "user",
    "mode": "0770",
    "quota": {
      "capacity_bytes": 8388608,
      "file_count": 32
    },
    "expires_at": "2026-07-01T00:00:00Z"
  }
}
```

규칙:

- `quota`가 없으면 Phase 10/11 create behavior를 유지한다.
- `quota.capacity_bytes`와 `quota.file_count`는 둘 중 하나 이상 있어야 finite quota apply 대상이다.
- Phase 12는 finite quota만 필수 구현한다. `quota.unlimited=true`로 quota를 clear하는 흐름은 후속 phase로 미룰 수 있다.
- quota 값은 양수 integer여야 한다.
- quota 값이 testbed 또는 운영 safety limit보다 크거나 작으면 planning 단계에서 reject한다.
- quota apply는 directory create, marker write, access permission 설정 후 실행한다.
- quota apply 실패 시 request는 `BackendApplyFailed` 또는 `VerificationFailed`로 남기고 partial directory state를 action-required에서 볼 수 있어야 한다.

### Filesystem Quota Update

기존 update endpoint를 Phase 12에서 quota update 대상으로 연다.

```text
PATCH /api/v1/resource-management/filesystems/{storage_name}/{directory_name}
```

요청 예시:

```json
{
  "requester_id": "portal:alice",
  "payload": {
    "quota": {
      "capacity_bytes": 33554432,
      "file_count": 128
    },
    "reason": "increase quota for phase12 validation"
  }
}
```

규칙:

- existing filesystem resource가 있어야 한다.
- `Deleted` resource는 update할 수 없다.
- `Blocked` resource의 quota update는 허용한다. 단 live permission block은 유지하고 quota desired/applied state만 갱신한다.
- quota field가 생략되면 기존 quota를 유지한다.
- quota 감소 요청은 live usage보다 작은 값이면 backend side effect 없이 reject한다.
- quota 증가 요청은 capability가 `Ready`일 때만 CephFS xattr를 변경한다.
- update는 access group, users, permission, rename을 Phase 12에서 함께 변경하지 않는다. quota 외 변경은 명시적으로 unsupported로 reject한다.

### Filesystem Quota Check

기존 check endpoint를 filesystem quota check로 구현한다.

```text
POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:check
```

요청 예시:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "include_usage": true,
    "include_quota": true,
    "include_permission": true,
    "usage_thresholds": {
      "warning_percent": 80,
      "critical_percent": 95
    },
    "record_action_required": true
  }
}
```

규칙:

- check는 read-only다. CephFS xattr, directory permission, LDAP group, DB desired state를 변경하지 않는다.
- check는 DB resource state와 live backend state를 비교한다.
- check result는 request/result lifecycle에 저장한다.
- latest clean check 이후 이전 drift/usage action-required issue는 해소되어야 한다.

### Filesystem Quota Sync

Phase 12에서 filesystem sync endpoint가 없다면 추가한다.

```text
POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:sync
```

요청 예시:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "source": "live",
    "include_quota": true,
    "include_usage": true,
    "reason": "accept manually adjusted CephFS quota"
  }
}
```

규칙:

- sync는 live CephFS quota xattr를 읽어 DMS DB desired/applied/observed state로 수용한다.
- sync는 CephFS xattr나 directory permission을 변경하지 않는다.
- sync는 marker mismatch, missing directory, unsafe path, capability missing이면 fail-closed 한다.
- sync 후 동일 target에 대한 quota drift action-required issue는 해소되어야 한다.
- sync는 quota-only assigned resource와 full imported resource 모두 지원한다.

### Existing Directory Assign Quota

기존 endpoint를 quota-only management path로 구현한다.

```text
POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:assign-quota
```

요청 예시:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "management_mode": "quota_only",
    "initialize_marker": true,
    "quota": {
      "capacity_bytes": 16777216,
      "file_count": 64
    },
    "reason": "phase12 assign quota to existing directory"
  }
}
```

규칙:

- 대상 directory는 storage mapping의 managed root 바로 아래에 이미 존재해야 한다.
- `directory_name`은 safe basename이어야 하고 path separator를 포함할 수 없다.
- symlink, bind mount, realpath escape, storage root 밖 path는 reject한다.
- 대상이 이미 full DMS-managed resource이면 assign-quota가 아니라 update로 처리해야 한다.
- 대상이 이미 quota-only managed resource이면 quota update semantics를 따른다.
- assign-quota는 access control, owner/group/mode를 변경하지 않는다.
- assign-quota는 일반 운영 LDAP user account를 생성/삭제하지 않는다.
- `initialize_marker=true`이면 `.dms-resource.json`에 `management_mode=quota_only` marker를 쓴다.
- quota-only resource에 대한 DMS delete는 backend directory 삭제로 연결하지 않는다. Phase 12에서는 delete를 reject하거나 unregister-only 정책을 별도로 명시해야 한다.

### Existing Directory Import

기존 endpoint를 full DMS-managed import path로 구현한다.

```text
POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:import
```

요청 예시:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "import_mode": "full",
    "initialize_marker": true,
    "access_policy": {
      "mode": "adopt_existing_group",
      "expected_group": "dms-phase12-existing-a",
      "expected_mode": "0770",
      "users": ["alice", "bob"],
      "denied_users": ["dms-phase12-denied-a"]
    },
    "quota": {
      "capacity_bytes": 16777216,
      "file_count": 64
    },
    "preserve_existing_data": true,
    "reason": "phase12 import existing directory"
  }
}
```

규칙:

- import는 DMS가 생성하지 않았지만 이미 존재하는 directory를 full DMS-managed filesystem resource로 전환하는 명시적 operation이다.
- 대상 directory는 실제 filesystem에 존재해야 한다.
- import는 storage root 밖으로 escape하는 symlink, bind mount, path traversal, unsafe ownership 상태를 reject한다.
- 대상이 이미 full DMS-managed resource이면 conflict 또는 no-op success 정책을 명확히 한다.
- 대상이 quota-only managed resource이면 import를 통해 full DMS-managed resource로 승격할 수 있다.
- import 전 live 상태를 읽어 owner, group, mode, ACL 여부, quota limit, quota usage, capacity usage, file-count usage, filesystem type, marker 상태를 기록한다.
- access control 해석은 명시적이어야 한다.
  - `adopt_existing_group`: 기존 Linux group이 OpenLDAP/SSSD로 해석되고 요청 users와 membership이 일치해야 한다.
  - `set_dms_group`: 요청이 DMS-managed access group 생성을 명시해야 하며 기존 permission 변경을 result에 기록해야 한다.
- Phase 12 verifier는 우선 `adopt_existing_group`을 live 검증한다. `set_dms_group`은 unit test 또는 후속 phase로 미룰 수 있다.
- import는 명시 정책 없이 기존 permission, ownership, quota를 덮어쓰지 않는다.
- import 성공 시 현재 live state와 요청 quota를 초기 desired/applied/observed state로 기록한다.
- import 성공 후에는 update, block/unblock, check/sync, expiration sweep 대상이 될 수 있다.

## Quota Model

DMS filesystem quota model은 backend-neutral field를 우선 사용한다.

```json
{
  "quota": {
    "capacity_bytes": 8388608,
    "file_count": 32
  },
  "quota_state": {
    "backend_type": "cephfs",
    "capacity": {
      "desired_bytes": 8388608,
      "applied_bytes": 8388608,
      "observed_bytes": 8388608,
      "backend_key": "ceph.quota.max_bytes"
    },
    "file_count": {
      "desired_count": 32,
      "applied_count": 32,
      "observed_count": 32,
      "backend_key": "ceph.quota.max_files"
    },
    "usage": {
      "used_bytes": 1048576,
      "used_files": 4,
      "usage_source": "cephfs-xattr"
    }
  }
}
```

원칙:

- DB desired state는 사용자가 요청한 backend-neutral quota를 저장한다.
- DB applied state는 adapter가 실제 적용했다고 확인한 quota를 저장한다.
- DB observed state는 live backend read-back quota와 usage를 저장한다.
- backend-specific xattr 이름은 CephFS adapter 내부 또는 `backend_details`에만 둔다.
- core planner/query/action-required는 `capacity_bytes`, `file_count`, `used_bytes`, `used_files`, `used_percent` 같은 backend-neutral field를 사용한다.
- quota 값을 string 단위(`8Mi`)로 받을지 integer byte로 받을지 API에서 명확히 정한다. Phase 12 권장은 integer byte/count만 허용해 모호성을 줄이는 것이다.

## CephFS Adapter Requirements

CephFS host-mounted adapter는 Phase 12에서 다음 live capability를 구현한다.

```text
probe_quota_capability(storage_mapping) -> capability
apply_quota(resource, quota_plan) -> observed_quota
read_quota_state(resource) -> observed_quota
read_usage(resource) -> observed_usage
check_quota(resource, desired_state) -> check_result
sync_quota_from_live(resource) -> synced_state
assign_quota_only(resource, quota_plan) -> observed_state
import_directory(resource, import_plan) -> observed_state
```

CephFS quota primitive:

- capacity quota는 CephFS directory xattr `ceph.quota.max_bytes`로 적용한다.
- file-count quota는 CephFS directory xattr `ceph.quota.max_files`로 적용한다.
- usage는 CephFS가 제공하는 recursive usage xattr가 사용 가능하면 그것을 사용한다.
- testbed verifier는 작은 fixture에 대해 `du`/`find` fallback으로 usage sanity를 교차 확인할 수 있다. 단 production adapter가 fallback source를 쓰면 `usage_source`에 명확히 기록해야 한다.
- xattr apply/read를 위해 worker node에 `setfattr`/`getfattr`가 필요하면 verification script가 패키지를 확인하고, 설치한 경우 testbed 문서에 남긴다.

Capability probe는 최소 다음을 반환한다.

```json
{
  "supports_directory_create": true,
  "supports_capacity_quota": true,
  "supports_file_count_quota": true,
  "supports_usage_bytes": true,
  "supports_file_count_usage": true,
  "supports_permission_mode": true,
  "supports_marker": true,
  "quota_backend": "cephfs-xattr",
  "checked_at": "2026-05-31T00:00:00Z"
}
```

fail-closed 원칙:

- quota capability가 없거나 알 수 없으면 quota apply 전에 reject한다.
- marker mismatch는 reject한다.
- directory missing은 reject한다.
- storage root escape는 reject한다.
- xattr apply 후 read-back이 요청 quota와 다르면 `VerificationFailed`로 기록한다.
- quota update 중 일부 xattr만 적용된 경우 observed state와 recovery issue를 남긴다.

## Planner Guard

Phase 12 Planner는 다음을 검증한다.

공통:

- `storage_name`, `directory_name` safe basename.
- storage mapping 존재.
- RM readiness `Ready`.
- backend capability가 필요한 operation을 지원.
- 같은 resource에 active work가 있으면 conflict.
- `Deleted` resource update/check/sync/block/import는 reject.

Quota validation:

- `quota.capacity_bytes`는 positive integer.
- `quota.file_count`는 positive integer.
- 둘 다 없으면 quota operation은 reject.
- configured minimum/maximum quota boundary를 벗어나면 reject.
- `capacity_bytes`와 `file_count`를 동시에 지원하지 않는 backend이면 unsupported field를 reject.
- quota decrease는 live usage를 read-only 조회한 뒤 usage보다 작은 target이면 reject.
- live usage 조회가 실패하면 decrease는 fail-closed.

Import/assign validation:

- existing directory가 live filesystem에 존재해야 한다.
- unmanaged directory는 marker가 없거나 compatible marker만 허용한다.
- `quota_only` marker와 full managed marker를 구분한다.
- import access policy가 없거나 LDAP group/users를 해석할 수 없으면 reject.
- local `/etc/passwd`, `/etc/group`만으로 user/group preflight를 통과시키면 안 된다.
- import/assign은 nested path, symlink, bind mount, unsafe owner/mode를 reject한다.

## Worker Behavior

RM Worker는 Phase 12 filesystem operation을 adapter에 dispatch한다.

Operation별 behavior:

| Operation | Worker behavior |
| --- | --- |
| `filesystem.create` with quota | create directory/access marker 후 quota apply/read-back |
| `filesystem.update` quota | decrease guard 후 quota xattr update/read-back |
| `filesystem.assign_quota` | existing directory preflight, quota-only marker, quota apply/read-back |
| `filesystem.import` | existing directory preflight, marker initialize, state capture, optional quota apply/read-back |
| `filesystem.consistency_check` | read-only live state compare, result 저장 |
| `filesystem.sync` | read-only live state 조회 후 DB desired/applied/observed state 갱신 |

Worker result에는 최소 다음을 포함한다.

- `quota_status`: `Applied`, `Consistent`, `Drifted`, `Missing`, `Unsupported`, `CheckFailed`
- `quota_state`
- `usage`
- `capability`
- `preflight`
- `marker`
- `path`
- `management_mode`: `full` 또는 `quota_only`
- `verification_summary.issues`

## Action-Required

`GET /api/v1/operations/action-required`에 Phase 12 filesystem quota/import issue를 추가한다.

추가 issue type:

```text
filesystem_quota_capability_missing
filesystem_quota_apply_failed
filesystem_quota_verification_failed
filesystem_quota_drifted
filesystem_quota_missing
filesystem_quota_usage_warning
filesystem_quota_usage_critical
filesystem_quota_check_failed
filesystem_quota_decrease_blocked
filesystem_import_preflight_failed
filesystem_assign_quota_failed
filesystem_marker_missing
filesystem_marker_mismatch
filesystem_unsafe_existing_directory
filesystem_access_group_unresolved
```

생성 기준:

- latest check result에서 quota drift/missing/check failure가 있으면 action-required에 표시한다.
- latest check result에서 usage threshold를 넘으면 warning/critical issue를 표시한다.
- quota apply/update/import/assign failure는 target별 issue로 표시한다.
- latest clean check 또는 successful sync 이후 같은 target의 drift issue는 해소되어야 한다.
- successful quota update 이후 같은 target의 quota apply failure issue는 해소되어야 한다.
- import preflight failure는 같은 target의 successful import 또는 explicit cleanup 이후 해소되어야 한다.

권장 issue shape:

```json
{
  "issue_type": "filesystem_quota_drifted",
  "severity": "WARN",
  "resource_kind": "filesystem",
  "resource_key": "cephfs-a:phase12-quota-a",
  "storage_name": "cephfs-a",
  "directory_name": "phase12-quota-a",
  "field": "quota.capacity_bytes",
  "desired": 8388608,
  "live": 16777216,
  "source_request_id": "req_...",
  "recommended_action": "run filesystem sync to accept live state or update quota to reapply desired state"
}
```

## Testbed Live Verification

검증은 Phase 10/11과 동일하게 c1/c2 host-mounted CephFS를 대상으로 한다.

기본 target:

```text
cluster-a/c1-worker: /mnt/testbed-cephfs
cluster-b/c2-worker: /mnt/testbed-cephfs-c2
```

검증 전 확인:

```bash
ssh c1-worker "findmnt ${DMS_PHASE12_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}"
ssh c1-worker "stat -f -c '%T' ${DMS_PHASE12_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}"
ssh c1-worker "command -v setfattr && command -v getfattr"

ssh c2-worker "findmnt ${DMS_PHASE12_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}"
ssh c2-worker "stat -f -c '%T' ${DMS_PHASE12_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}"
ssh c2-worker "command -v setfattr && command -v getfattr"
```

Identity 검증 원칙:

- 허용 user는 최소 2명 이상이어야 한다.
- 비허용 user는 최소 1명 이상이어야 한다.
- seeded LDAP user가 부족하면 Phase 12 전용 LDAP test user를 생성하고 검증 후 삭제한다.
- local fallback user/group으로 성공 처리하면 안 된다.
- import full verification은 OpenLDAP group membership과 worker node SSSD/NSS propagation을 반드시 확인한다.

### Phase 12A 권장 검증 Flow

1. 새 PostgreSQL operational/observability DB를 준비한다.
2. API와 Agent를 배포한다.
3. c1/c2 Agent report가 실제 host mount evidence를 제출했는지 확인한다.
4. `cephfs-a`, `cephfs-b` storage mapping RM readiness가 `Ready`인지 확인한다.
5. c1/c2 target 모두에서 quota capability probe가 `supports_capacity_quota=true`, `supports_file_count_quota=true`를 반환하는지 확인한다.
6. c1 target에 quota가 있는 filesystem resource를 create한다.
7. c2 target에도 quota가 있는 filesystem resource를 create한다.
8. create 후 CephFS xattr read-back이 requested quota와 일치하는지 확인한다.
9. allowed user가 quota 이내 작은 파일을 쓸 수 있는지 확인한다.
10. allowed user가 capacity quota를 초과하는 파일을 쓰면 실패하는지 확인한다.
11. file-count quota가 지원되면 allowed user가 file-count quota 초과 시 실패하는지 확인한다.
12. quota increase update를 수행하고 xattr read-back이 증가한 quota와 일치하는지 확인한다.
13. 증가된 quota 안에서 이전에 실패하던 write가 성공하는지 확인한다.
14. live usage보다 작은 quota decrease request가 backend side effect 없이 `Rejected` 되는지 확인한다.
15. check request가 `Consistent`를 반환하는지 확인한다.
16. verifier가 수동으로 CephFS quota xattr를 변경해 drift를 만든다.
17. check request가 `Drifted`를 반환하고 action-required에 `filesystem_quota_drifted`가 표시되는지 확인한다.
18. sync request가 live quota를 DB에 수용하고 action-required drift issue가 해소되는지 확인한다.
19. usage threshold를 넘는 작은 quota/fixture를 만들어 `filesystem_quota_usage_warning` 또는 `filesystem_quota_usage_critical`이 표시되는지 확인한다.
20. cleanup delete를 실행하고 directory, quota marker, DMS-managed LDAP group이 정리됐는지 확인한다.

### Phase 12B 권장 검증 Flow

1. c1 worker host의 managed root 아래 existing unmanaged directory fixture를 만든다.
2. fixture directory는 safe basename이어야 하며 marker가 없어야 한다.
3. assign-quota request를 보내 quota-only marker와 CephFS quota xattr가 적용되는지 확인한다.
4. quota-only resource check가 `Consistent`를 반환하는지 확인한다.
5. quota-only resource에 대한 backend directory delete가 수행되지 않도록 reject 또는 unregister-only 정책을 확인한다.
6. c2 worker host의 managed root 아래 full import용 existing directory fixture를 만든다.
7. OpenLDAP에 Phase 12 전용 access group을 만들고 `alice`, `bob`을 member로 넣는다.
8. c2 worker SSSD/NSS에서 group membership이 보이는지 확인한다.
9. existing directory owner/group/mode를 import policy와 맞춘다.
10. import request를 `adopt_existing_group` 정책으로 보낸다.
11. import result가 owner/group/mode/quota/usage/marker state를 DB desired/applied/observed state에 기록하는지 확인한다.
12. import 후 allowed users는 write 가능하고 denied user는 접근 불가인지 확인한다.
13. import 후 quota assignment 또는 quota update를 수행한다.
14. quota enforcement, check, drift, sync가 imported resource에서도 동일하게 동작하는지 확인한다.
15. unsafe existing directory cases를 최소 하나 이상 검증한다.
    - symlink escape
    - nested path
    - marker mismatch
    - LDAP group unresolved
16. cleanup에서 fixture directory, marker, quota xattr, Phase 12 LDAP group/user fixture를 정리한다.

권장 small quota values:

```text
initial capacity quota: 8Mi
increased capacity quota: 32Mi
file count quota: 16 or 32
warning threshold: 80%
critical threshold: 95%
```

테스트베드는 CPU/Memory/Disk가 제한되어 있으므로 verifier는 큰 파일을 만들지 않는다. capacity enforcement는 sparse file이 아닌 실제 small file write로 검증하되 총 사용량은 수십 MiB 이하로 유지한다.

## Local Regression Tests

최소 unit/integration test:

- create with quota plans backend-neutral desired quota.
- create with quota rejects invalid negative/zero/non-integer quota.
- create with quota rejects quota above configured safety max.
- create without quota preserves Phase 10/11 behavior.
- CephFS adapter applies `ceph.quota.max_bytes` and `ceph.quota.max_files`.
- CephFS adapter verifies xattr read-back.
- update quota increase updates desired/applied/observed state.
- update quota decrease below observed usage is rejected before backend side effect.
- check returns `Consistent` for matching DB/live quota.
- check returns `Drifted` for manually changed live quota.
- check returns `Missing` for missing directory.
- sync accepts live quota into DB without filesystem mutation.
- action-required includes quota drift and usage pressure.
- action-required resolves drift after successful sync or clean check.
- assign-quota requires existing directory.
- assign-quota rejects symlink/path escape.
- assign-quota writes quota-only marker and does not change owner/group/mode.
- import requires existing directory.
- import rejects unresolved LDAP group.
- import records current owner/group/mode/quota/usage.
- import can promote quota-only resource to full managed resource.
- imported full resource supports quota update/check/sync.
- unsupported GPFS/WekaFS skeleton path fails closed for live quota side effect.

권장 command:

```bash
cd /home/mason/workspace/dms
pytest
```

## Verification Artifact

Phase 12 완료 시 `docs/dms-phase12-verification.md`를 작성한다.

반드시 포함할 내용:

- 실행 날짜와 git commit 또는 working tree 상태
- pytest 결과
- PostgreSQL operational/observability DB 이름
- DMS API/Agent deployment evidence
- c1/c2 host mount `findmnt`, `stat -f`, xattr tool 확인 결과
- c1/c2 Agent report id와 mount evidence
- OpenLDAP/SSSD user/group verification
- quota capability probe output
- create-with-quota request/result ids
- CephFS quota xattr read-back output
- quota enforcement write success/failure output
- quota update increase request/result
- quota decrease guard rejected request/result
- check consistent output
- manual drift command와 drift check output
- action-required quota drift/usage output
- sync request/result와 action-required resolution output
- assign-quota request/result와 quota-only marker output
- import request/result와 imported state output
- unsafe existing directory reject cases
- cleanup evidence

## Phase 12에서 하지 않을 것

다음은 Phase 12 범위가 아니다.

- GPFS/WekaFS/Lustre live quota implementation
- Kubernetes PVC 내부 directory quota
- Longhorn block volume 내부 filesystem quota
- quota clear/unlimited lifecycle 전체
- filesystem ACL 기반 access-control update
- rename/move operation
- imported production directory backend delete 정책 일반화
- automatic quota drift/usage cron/controller
- automatic expiration sweep cron/controller
- trash/quarantine workflow
- Data Management `scan/sync/rm` live execution
- VolcanoJob create/watch/terminate
- long-running RM Worker Kubernetes Deployment/loop 운영 검증
- production Helm/Kustomize packaging 완성
- trusted ingress mTLS live validation

## Phase 12 완료 후 다음 Phase 후보

Phase 12가 성공하면 DMS는 Kubernetes storage quota RM과 host-mounted CephFS filesystem create/delete, expiry/block lifecycle, finite quota lifecycle, existing directory import/assign-quota를 테스트베드에서 검증한 상태가 된다.

다음 후보:

### Phase 13A: Long-Running RM Worker Runtime Deployment

- `dms rm-worker --loop` settings/live adapter wiring
- c1/c2 dedicated RM Worker node 배포
- Agent와 Worker의 host mount capability 공유
- worker lease, stale claim, restart recovery, `UnknownAfterSideEffect` action-required 검증

### Phase 13B: Filesystem Quota Policy / Initialize

- filesystem default quota policy
- `filesystem.initialize`
- `reset_quota_to_default=true`
- quota clear/unlimited policy
- imported/quota-only resource policy inheritance

### Phase 13C: Data Management Read-only Scan Preflight

- filesystem resource boundary를 read-only scan target으로 사용
- DM Agent report 기반 candidate pool
- LDAP identity mapping과 POSIX permission preflight
- mpifileutils/VolcanoJob 이전의 local scan preflight 검증

권장 순서는 Phase 13A로 long-running RM Worker runtime을 실제 배포해 Phase 10~12 filesystem RM 기능을 운영형 loop에서 재검증한 뒤, Phase 13B 또는 Phase 13C로 넘어가는 것이다.
