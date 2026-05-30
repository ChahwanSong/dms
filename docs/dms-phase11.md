# DMS Phase 11 Implementation Prompt

이 문서는 `docs/dms-phase10.md` 완료 이후 열한 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 11의 목표는 Phase 10에서 검증한 **워커 노드 host-mounted CephFS filesystem create/delete lifecycle** 위에, `expires_at` 기반 만료 정책과 filesystem block/unblock 최소 lifecycle을 실제 테스트베드에서 검증하는 것이다.

중요: Phase 11의 만료 처리는 cron/controller처럼 자동으로 주기 실행하지 않는다. 운영자 또는 외부 포털이 API를 호출했을 때만 expired resource를 조회하거나 sweep을 실행한다. sweep 결과는 DMS DB와 `action-required`에 남겨 운영자가 처리해야 할 항목을 모아서 볼 수 있어야 한다.

Phase 11은 filesystem quota create/update/check/sync, filesystem usage pressure, long-running RM Worker Deployment 운영 검증, Data Management `scan/sync/rm` live execution을 열지 않는다. 이번 phase는 `expires_at`을 실제 운영 상태로 연결하고, 만료된 filesystem resource를 안전하게 block/unblock할 수 있는 최소 경로를 먼저 닫는다.

## Phase 11 목표

Phase 11의 핵심 기능은 다음 여섯 가지다.

1. **Expired/expiring filesystem resource query**
2. **On-demand filesystem expiration sweep API**
3. **Filesystem block 최소 lifecycle**
4. **Filesystem unblock restore lifecycle**
5. **Expiration/block 관련 action-required aggregation**
6. **c1/c2 host-mounted CephFS + OpenLDAP/SSSD live verification**

구현 완료 기준은 다음과 같다.

- Phase 10 create에서 저장한 `expires_at` metadata를 기준으로 expired/expiring filesystem resource를 조회할 수 있다.
- expiration sweep은 운영자가 API로 요청한 경우에만 실행된다.
- expiration sweep은 DMS DB에 존재하고 `Deleted`가 아닌 filesystem resource만 대상으로 한다.
- expiration sweep은 `resource_type=user` 또는 기본 일반 resource만 자동 block 대상으로 삼는다.
- `resource_type=system`, `resource_type=admin` resource는 자동 block하지 않고 skip reason을 result와 action-required에 남긴다.
- 이미 `Blocked` 상태인 expired resource는 중복 block하지 않고 skipped 또는 already_blocked 결과로 기록한다.
- block은 DMS ownership marker가 확인되는 managed directory에만 적용된다.
- block은 기존 group/mode restore state를 DB에 저장한 뒤 directory 접근을 닫는다.
- unblock은 저장된 restore state를 기준으로 group/mode를 복구한다.
- restore state가 없거나 marker가 맞지 않으면 unblock은 fail-closed 한다.
- block/unblock은 일반 운영 LDAP user account를 생성/삭제하지 않는다.
- Phase 10과 동일하게 접근 권한 검증은 테스트베드 OpenLDAP/SSSD 기반으로 수행한다.
- 최소 2명 이상의 허용 user와 1명 이상의 비허용 user로 block/unblock 전후 접근 경계를 검증한다.
- c1/c2 양쪽 CephFS host mount에서 모두 create -> expired query -> sweep/block -> unblock -> delete를 실제 검증한다.
- 검증 결과는 `docs/dms-phase11-verification.md`와 `docs/dms-done.md`에 command/output 포함해 기록한다.

## 현재 전제

Phase 10 완료 후 전제:

- filesystem create/delete endpoint가 실제 host-mounted CephFS backend side effect를 수행한다.
  - `POST /api/v1/resource-management/filesystems`
  - `DELETE /api/v1/resource-management/filesystems/{storage_name}/{directory_name}`
- filesystem block endpoint skeleton은 존재하지만 Phase 10에서는 unsupported로 reject된다.
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:block`
- filesystem expiration sweep endpoint skeleton은 존재하지만 Phase 10에서는 unsupported로 reject된다.
  - `POST /api/v1/resource-management/filesystems:expiration-sweep`
- `filesystem.expiration_sweep` operation kind는 domain에 존재한다.
- RM Worker runtime은 filesystem operation을 adapter에 dispatch할 수 있다.
- CephFS host adapter는 create/delete만 구현되어 있고 block/unblock은 Phase 10 unsupported 상태다.
- Phase 10 live verification은 다음 target을 사용했다.
  - `cluster-a/c1-worker`: `/mnt/testbed-cephfs`
  - `cluster-b/c2-worker`: `/mnt/testbed-cephfs-c2`
- DMS Agent report, storage mapping sanity, OpenLDAP/SSSD, PostgreSQL-backed request/plan/result state는 실제 테스트베드에서 검증됐다.
- long-running RM Worker Kubernetes Deployment/loop는 아직 운영형 live verification 대상이 아니다. Phase 11도 기존 verification script가 `RMWorkerRuntime.run_once()`를 호출하는 방식으로 검증한다.

## 왜 Phase 11에서 Expiry와 Block/Unblock을 먼저 하는가

Filesystem Resource Management는 create/delete만으로는 운영 lifecycle이 부족하다. 운영 resource에는 만료 시간이 있고, 만료 후 바로 삭제하면 데이터 손실 가능성이 크다. 따라서 첫 만료 정책은 삭제가 아니라 **접근 차단(block)** 으로 둔다.

Phase 11에서 filesystem quota 전체를 열지 않는 이유:

- quota primitive는 backend마다 다르다. CephFS xattr, GPFS fileset quota, WekaFS quota, Lustre project quota 등 구현 축이 크다.
- block/unblock은 quota보다 운영상 먼저 필요한 안전장치다.
- 만료 정책을 block으로 연결해두면 이후 quota/check/sync가 추가되어도 lifecycle 상태 모델을 재사용할 수 있다.
- Data Management로 넘어가기 전에 filesystem access boundary와 action-required 흐름을 실제 storage에서 먼저 검증해야 한다.

## API Surface

### Expired/Expiring Query

Phase 11에서 dedicated operational query API를 추가한다.

```text
GET /api/v1/operations/filesystems/expiring
```

권장 query parameter:

```text
storage_name=<optional>
status=expired|expiring|all        # optional, default: expired
before=<ISO-8601 timestamp>        # optional, default: now
within_seconds=<integer>           # optional, expiring 조회 window
include_blocked=<true|false>       # optional, default: false
limit=<integer>                    # optional, default: repository/API 기본값
```

예시:

```bash
curl -s -H 'x-dms-actor: api-client' \
  'http://127.0.0.1:8000/api/v1/operations/filesystems/expiring?status=expired&storage_name=cephfs-a'
```

응답 예시:

```json
[
  {
    "resource_kind": "filesystem",
    "resource_key": "cephfs-a:phase11-expired-a",
    "storage_name": "cephfs-a",
    "directory_name": "phase11-expired-a",
    "resource_type": "user",
    "status": "Succeeded",
    "expires_at": "2026-05-30T00:00:00Z",
    "expired": true,
    "seconds_overdue": 3600,
    "block_state": {
      "blocked": false
    },
    "updated_at": "2026-05-30T01:00:00Z"
  }
]
```

규칙:

- 이 API는 DB 조회 API다. live filesystem mutation을 수행하지 않는다.
- 기본적으로 `Deleted` resource는 제외한다.
- 기본적으로 이미 `Blocked` resource는 제외하되, `include_blocked=true`이면 표시한다.
- `expires_at`이 없으면 만료 평가 대상이 아니다.
- `resource_type`이 없으면 일반 user resource로 취급한다.
- `before`와 `within_seconds`는 둘 다 ISO timestamp 비교 기준을 명확히 result에 표시한다.

### Expiration Sweep

기존 skeleton endpoint를 구현한다.

```text
POST /api/v1/resource-management/filesystems:expiration-sweep
```

요청 예시:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "scope": {
      "storage_name": "cephfs-a",
      "resource_type": "user"
    },
    "expired_before": "2026-05-30T00:00:00Z",
    "action": "block",
    "dry_run": false,
    "max_targets": 100,
    "reason": "phase11 expiration sweep"
  }
}
```

응답 예시:

```json
{
  "request_id": "req_...",
  "status": "Persisted"
}
```

sweep result의 `verification_summary` 예시:

```json
{
  "sweep_timestamp": "2026-05-30T00:05:00Z",
  "expired_before": "2026-05-30T00:00:00Z",
  "action": "block",
  "dry_run": false,
  "target_count": 2,
  "blocked_count": 1,
  "skipped_count": 1,
  "failed_count": 0,
  "targets": [
    {
      "resource_key": "cephfs-a:phase11-expired-a",
      "storage_name": "cephfs-a",
      "directory_name": "phase11-expired-a",
      "result": "blocked",
      "block_request_id": "req_..."
    },
    {
      "resource_key": "cephfs-a:phase11-system-a",
      "storage_name": "cephfs-a",
      "directory_name": "phase11-system-a",
      "result": "skipped",
      "reason": "resource_type_not_auto_blocked",
      "resource_type": "system"
    }
  ]
}
```

규칙:

- `action`은 Phase 11에서 `block`만 지원한다.
- `dry_run=true`이면 대상 산정과 skip/failure reason만 기록하고 backend side effect는 수행하지 않는다.
- `max_targets`는 작게 시작한다. 권장 기본값은 100, 허용 상한은 1000이다.
- `expired_before`가 없으면 server-side now를 사용한다.
- `scope.storage_name`이 있으면 해당 storage만 대상으로 한다.
- `scope.resource_type`이 있으면 해당 resource type만 대상으로 한다.
- sweep은 `storage_mapping` RM readiness가 `Ready`가 아닌 target을 side effect 전에 skip 또는 fail-closed 처리한다.
- sweep은 same resource에 active request/plan/run이 있으면 해당 target을 skip하고 `resource_has_active_work`를 기록한다.
- sweep은 per-target 결과를 result에 저장해야 한다.
- 구현 편의상 Phase 11에서는 sweep plan이 target list를 직접 처리해도 된다. repository schema가 자연스럽게 지원하면 resource별 child block request를 생성해도 된다. 어떤 방식이든 target별 audit trail이 request/result에서 추적 가능해야 한다.
- child request를 만들 경우 parent sweep request id를 payload 또는 result summary에 기록한다.

### Filesystem Block / Unblock

기존 keyed endpoint를 구현한다.

```text
POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:block
```

Block 요청 예시:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "block": true,
    "block_mode": "permission-zero",
    "reason": "manual phase11 block"
  }
}
```

Unblock 요청 예시:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "block": false,
    "reason": "manual phase11 unblock"
  }
}
```

규칙:

- `block=true`는 existing filesystem resource가 있어야 한다.
- `block=false`는 existing filesystem resource가 있고 restore 가능한 `block_state`가 있어야 한다.
- `resource_type=system` 또는 `resource_type=admin`에 대한 `block=true`는 manual request에서도 기본 reject한다. 추후 별도 override policy가 필요하면 후속 phase에서 다룬다.
- block/unblock은 `quota`, `capacity_bytes`, `file_count`, `rename`, `acl` payload를 받지 않는다.
- block/unblock은 DMS-created ownership marker가 맞는 directory만 변경한다.
- marker의 `resource_key`, `storage_name`, `directory_name`이 request target과 다르면 fail-closed 한다.
- block은 access LDAP group을 삭제하지 않는다. group membership은 유지하고 filesystem permission만 닫는다.
- unblock은 access LDAP group을 새로 만들지 않는다. 기존 DB/applied state의 group을 복구 대상으로 사용한다. group이 사라졌으면 fail-closed 하고 action-required로 올린다.

## 기능 1: Expired/Expiring Filesystem Query

Operational Query Service에 filesystem resource 만료 조회를 추가한다.

필요 repository helper:

```text
list_filesystem_resources_expiring(
  storage_name=None,
  status="expired",
  before=now,
  within_seconds=None,
  include_blocked=False,
  limit=...
)
```

조회 기준:

- `resource_kind='filesystem'`
- `status != 'Deleted'`
- `desired_state.expires_at` 또는 `applied_state.expires_at` 존재
- `expires_at <= before`이면 expired
- `before < expires_at <= before + within_seconds`이면 expiring
- `desired_state.block_state.blocked=true` 또는 `observed_state.blocked=true`이면 blocked로 판단

응답에는 최소 다음 필드를 포함한다.

- `resource_key`
- `storage_name`
- `directory_name`
- `resource_type`
- `status`
- `expires_at`
- `expired`
- `seconds_overdue`
- `block_state`
- `updated_at`
- 최근 관련 request summary

## 기능 2: On-Demand Expiration Sweep

Planner는 `filesystem.expiration_sweep` 요청을 Phase 11 supported operation으로 전환한다.

Planner guard:

- `action`이 없으면 `block`으로 간주한다.
- `action != block`은 unsupported로 reject한다.
- `dry_run`은 boolean만 허용한다.
- `max_targets`는 양수 integer만 허용한다.
- `expired_before`는 ISO-8601 timestamp만 허용한다.
- sweep resource key는 현재 skeleton의 `filesystem-expiration-sweep`을 유지해도 된다.
- sweep 요청 자체는 특정 storage에 대한 active work와 conflict하지 않아야 한다. 단, per-target side effect 전에는 target resource active work를 검사한다.

Worker apply:

- DB에서 target list를 확정한 뒤 result에 snapshot으로 저장한다.
- `dry_run=true`이면 side effect 없이 result를 `Succeeded`로 남긴다.
- `dry_run=false`이면 target별 block을 수행한다.
- target별 block은 filesystem adapter의 공통 block method를 재사용한다.
- 일부 target 실패가 있으면 sweep request는 `Succeeded`로 둘지 `VerificationFailed`로 둘지 정책을 명확히 정한다. 권장 정책은 sweep 자체가 실행됐고 per-target 실패가 result/action-required에 남으면 terminal status는 `Succeeded`, 모든 target 산정 전 치명 오류는 `Failed`다.
- target별 side effect 이후 terminal commit 실패 가능성은 기존 `UnknownAfterSideEffect`/action-required 회복 모델을 따른다.

sweep target 처리 정책:

| 조건 | 처리 |
| --- | --- |
| resource `Deleted` | 제외 |
| `expires_at` 없음 | 제외 |
| 아직 만료 전 | 제외 |
| 이미 `Blocked` | skip `already_blocked` |
| `resource_type=user` 또는 미지정 | block 대상 |
| `resource_type=system` | skip `resource_type_not_auto_blocked` |
| `resource_type=admin` | skip `resource_type_not_auto_blocked` |
| RM readiness Missing/Failed | skip 또는 fail `rm_readiness_not_ready` |
| marker mismatch | fail `filesystem_marker_mismatch` |
| active request/plan/run | skip `resource_has_active_work` |

## 기능 3: Filesystem Block

CephFS host-mounted adapter에 block subset을 구현한다.

권장 block mode:

```text
permission-zero
```

`permission-zero` semantics:

- block 전 marker를 읽고 target resource와 일치하는지 확인한다.
- 현재 directory owner/group/mode를 읽어 restore state로 저장한다.
- 현재 access group name/gid도 저장한다.
- directory mode를 `0000`으로 변경한다.
- owner/group은 되도록 변경하지 않는다. 권한만 닫아 restore를 단순화한다.
- root/sudo를 사용하는 DMS RM Worker는 unblock을 위해 계속 접근 가능해야 한다.

block applied/observed state 예시:

```json
{
  "adapter": "cephfs-host-mounted",
  "operation": "block",
  "directory_name": "phase11-expired-a",
  "path": "/mnt/testbed-cephfs/dms-phase10/phase11-expired-a",
  "block_state": {
    "blocked": true,
    "block_mode": "permission-zero",
    "blocked_at": "2026-05-30T00:05:00Z",
    "blocked_by_request_id": "req_...",
    "reason": "phase11 expiration sweep",
    "restore": {
      "owner": "root",
      "group_name": "dms-phase10-phase11-expired-a",
      "gid": 12345,
      "mode": "0770"
    }
  },
  "resource_status": "Blocked"
}
```

block idempotency:

- 이미 `block_state.blocked=true`이고 live mode도 blocked 상태이면 backend side effect 없이 `already_blocked=true` result를 남긴다.
- DB는 blocked 상태를 유지한다.
- restore state는 기존 값을 덮어쓰지 않는다.

block failure:

- marker가 없으면 fail-closed.
- marker target이 다르면 fail-closed.
- restore state를 읽기 전에 permission 변경을 하면 안 된다.
- permission 변경 후 verification이 실패하면 `BackendApplyFailed` 또는 `VerificationFailed`로 남기고 action-required에서 운영자가 확인할 수 있게 한다.

## 기능 4: Filesystem Unblock

CephFS host-mounted adapter에 unblock subset을 구현한다.

unblock semantics:

- DB resource가 존재해야 한다.
- DB나 observed/applied state에 `block_state.blocked=true`가 있어야 한다.
- restore state가 있어야 한다.
- marker가 request target과 일치해야 한다.
- directory group/mode를 restore state로 복구한다.
- restore 후 allowed users가 다시 접근 가능해야 한다.
- denied user는 unblock 후에도 접근 불가해야 한다.
- 성공하면 `block_state.blocked=false`를 desired/applied/observed state에 기록한다.
- resource status는 `Succeeded` 또는 기존 active status로 되돌린다. Phase 11에서는 `Succeeded`로 두는 것을 권장한다.

unblock observed state 예시:

```json
{
  "adapter": "cephfs-host-mounted",
  "operation": "unblock",
  "directory_name": "phase11-expired-a",
  "path": "/mnt/testbed-cephfs/dms-phase10/phase11-expired-a",
  "block_state": {
    "blocked": false,
    "unblocked_at": "2026-05-30T00:10:00Z",
    "unblocked_by_request_id": "req_...",
    "restore": {
      "owner": "root",
      "group_name": "dms-phase10-phase11-expired-a",
      "gid": 12345,
      "mode": "0770"
    }
  },
  "resource_status": "Succeeded"
}
```

unblock failure:

- restore state가 없으면 `filesystem_block_restore_missing`.
- LDAP group이 없어졌거나 SSSD/NSS에서 조회되지 않으면 `filesystem_access_group_missing`.
- marker mismatch이면 `filesystem_marker_mismatch`.
- live mode 복구 후 허용 user access check가 실패하면 `filesystem_unblock_verification_failed`.

## 기능 5: Action-Required

`GET /api/v1/operations/action-required`에 filesystem expiry/block issue를 추가한다.

추가 issue type:

```text
filesystem_expired_unblocked
filesystem_expiration_sweep_skipped
filesystem_expiration_sweep_partial_failure
filesystem_block_failed
filesystem_unblock_restore_missing
filesystem_access_group_missing
filesystem_marker_mismatch
filesystem_block_verification_failed
filesystem_unblock_verification_failed
```

action-required 생성 기준:

- expired query 기준으로 만료됐지만 아직 block되지 않은 일반 filesystem resource는 `filesystem_expired_unblocked`.
- 최신 expiration sweep result에서 skip/failure가 있으면 target별 issue를 표시한다.
- 최신 block/unblock result에서 marker, restore, access group, verification 실패가 있으면 issue를 표시한다.
- 최신 unblock이 성공해 block issue가 해소되면 이전 block failure issue는 사라져야 한다.
- 최신 sweep에서 같은 target이 정상 처리되면 이전 sweep skip/failure issue는 사라져야 한다.

권장 issue shape:

```json
{
  "issue_type": "filesystem_expired_unblocked",
  "severity": "warning",
  "resource_kind": "filesystem",
  "resource_key": "cephfs-a:phase11-expired-a",
  "storage_name": "cephfs-a",
  "directory_name": "phase11-expired-a",
  "expires_at": "2026-05-30T00:00:00Z",
  "recommended_action": "run filesystem expiration sweep or manually block the resource",
  "source_request_id": "req_..."
}
```

## DB State Requirements

Filesystem resource desired/applied/observed state는 backend-neutral field를 우선 사용한다.

권장 desired state:

```json
{
  "storage_name": "cephfs-a",
  "directory_name": "phase11-expired-a",
  "users": ["alice", "bob"],
  "access_group": "dms-phase10-phase11-expired-a",
  "mode": "0770",
  "resource_type": "user",
  "expires_at": "2026-05-30T00:00:00Z",
  "block_state": {
    "blocked": true,
    "block_mode": "permission-zero",
    "restore": {
      "owner": "root",
      "group_name": "dms-phase10-phase11-expired-a",
      "gid": 12345,
      "mode": "0770"
    }
  }
}
```

원칙:

- `expires_at`은 create payload에서 받은 값을 유지한다.
- block/unblock이 `expires_at`을 삭제하거나 변경하면 안 된다.
- block은 `block_state.restore`를 저장한다.
- unblock은 restore state를 보존하되 `blocked=false`로 바꾼다.
- backend-specific field는 `observed_state.backend_details` 또는 adapter-specific section 아래 둔다.
- DMS ownership marker는 DB state와 live file 모두에 남아 있어야 한다.

## Adapter Contract Extension

Phase 10 adapter contract에 다음을 추가한다.

```text
block_directory(resource, block_plan) -> observed_state
unblock_directory(resource, unblock_plan) -> observed_state
read_directory_access_state(resource) -> observed_state
```

CephFS live adapter는 위 contract를 구현한다. GPFS skeleton은 같은 method를 갖되 live side effect는 stub/mock으로 유지해도 된다. 다만 unsupported backend가 조용히 성공하면 안 되며, capability가 없으면 fail-closed 해야 한다.

확장성 원칙:

- `permission-zero`는 공통 block mode로 시작한다.
- 향후 GPFS/WekaFS/Lustre/NFS appliance가 들어와도 core planner/query schema는 유지한다.
- backend별 ACL/read-only/snapshot/quota block 방식은 adapter capability로 분기한다.
- Phase 11은 quota-based block을 구현하지 않는다.

## Testbed Live Verification

검증은 Phase 10과 동일하게 c1/c2 host-mounted CephFS를 대상으로 한다.

기본 target:

```text
cluster-a/c1-worker: /mnt/testbed-cephfs
cluster-b/c2-worker: /mnt/testbed-cephfs-c2
```

검증 전 확인:

```bash
ssh c1-worker "findmnt ${DMS_PHASE11_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}"
ssh c1-worker "stat -f -c '%T' ${DMS_PHASE11_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}"
ssh c1-worker "test -w ${DMS_PHASE11_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}"

ssh c2-worker "findmnt ${DMS_PHASE11_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}"
ssh c2-worker "stat -f -c '%T' ${DMS_PHASE11_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}"
ssh c2-worker "test -w ${DMS_PHASE11_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}"
```

Identity 검증 원칙:

- 허용 user는 최소 2명 이상이어야 한다.
- 비허용 user는 최소 1명 이상이어야 한다.
- seeded LDAP user가 부족하면 Phase 11 전용 LDAP test user를 생성하고 검증 후 삭제한다.
- local fallback user/group으로 성공 처리하면 안 된다.
- DMS create flow가 LDAP access group을 만들고 membership을 설정해야 한다.
- block/unblock은 LDAP group membership을 변경하지 않고 filesystem permission만 변경한다.

권장 검증 flow:

1. 새 PostgreSQL operational/observability DB를 준비한다.
2. API와 Agent를 배포한다.
3. c1/c2 Agent report가 실제 host mount evidence를 제출했는지 확인한다.
4. `cephfs-a`, `cephfs-b` storage mapping RM readiness가 `Ready`인지 확인한다.
5. `alice`, `bob`을 허용 user로 두고 denied user fixture를 준비한다.
6. c1 target에 `expires_at`이 과거인 filesystem resource를 create한다.
7. c2 target에도 `expires_at`이 과거인 filesystem resource를 create한다.
8. create 후 allowed users는 write 가능하고 denied user는 접근 불가인지 확인한다.
9. `GET /api/v1/operations/filesystems/expiring`에서 두 resource가 expired로 조회되는지 확인한다.
10. `POST /api/v1/resource-management/filesystems:expiration-sweep`을 `dry_run=true`로 실행해 target 산정만 확인한다.
11. 같은 sweep을 `dry_run=false`로 실행한다.
12. RM Worker `run_once()`로 sweep 또는 child block request를 처리한다.
13. c1/c2 worker host에서 allowed users와 denied user 모두 blocked directory에 접근할 수 없는지 확인한다.
14. DB resource status가 `Blocked`이고 `block_state.restore`가 저장됐는지 확인한다.
15. `GET /api/v1/operations/action-required`에 예상 skip/failure 외 미해결 issue가 없는지 확인한다.
16. manual unblock request를 보낸다.
17. RM Worker `run_once()`로 unblock을 처리한다.
18. allowed users가 다시 접근 가능하고 denied user는 여전히 접근 불가인지 확인한다.
19. `resource_type=system` 또는 `resource_type=admin` expired resource를 만들고 sweep에서 자동 block이 skip되는지 확인한다.
20. skip reason이 sweep result와 action-required에 표시되는지 확인한다.
21. cleanup delete를 실행하고 directory와 DMS-managed LDAP group이 정리됐는지 확인한다.

권장 shell access check:

```bash
ssh c1-worker "sudo -u alice test -w /mnt/testbed-cephfs/dms-phase10/phase11-expired-a"
ssh c1-worker "sudo -u bob test -w /mnt/testbed-cephfs/dms-phase10/phase11-expired-a"
ssh c1-worker "sudo -u <denied-user> test ! -x /mnt/testbed-cephfs/dms-phase10/phase11-expired-a"

# after block
ssh c1-worker "sudo -u alice test ! -x /mnt/testbed-cephfs/dms-phase10/phase11-expired-a"
ssh c1-worker "sudo -u bob test ! -x /mnt/testbed-cephfs/dms-phase10/phase11-expired-a"
ssh c1-worker "sudo -u <denied-user> test ! -x /mnt/testbed-cephfs/dms-phase10/phase11-expired-a"

# after unblock
ssh c1-worker "sudo -u alice test -w /mnt/testbed-cephfs/dms-phase10/phase11-expired-a"
ssh c1-worker "sudo -u bob test -w /mnt/testbed-cephfs/dms-phase10/phase11-expired-a"
ssh c1-worker "sudo -u <denied-user> test ! -x /mnt/testbed-cephfs/dms-phase10/phase11-expired-a"
```

c2 target도 동일한 검증을 `/mnt/testbed-cephfs-c2`에서 수행한다.

## Local Regression Tests

최소 unit/integration test:

- expired query filters filesystem resources by `expires_at`.
- expired query excludes `Deleted` resources.
- expired query excludes blocked resources by default and includes them with `include_blocked=true`.
- expiration sweep rejects invalid `expired_before`, invalid `action`, invalid `max_targets`.
- dry-run sweep records target list without backend side effect.
- sweep skips `system` and `admin` resources.
- sweep skips already blocked resources.
- sweep blocks user resources and stores per-target result.
- block rejects missing resource.
- block rejects `system`/`admin` resource.
- block rejects marker mismatch.
- block stores restore owner/group/mode before permission mutation.
- block is idempotent when already blocked.
- unblock rejects missing restore state.
- unblock restores group/mode and clears blocked flag.
- unblock preserves `expires_at`.
- action-required includes expired unblocked resources.
- action-required includes latest sweep skip/failure issues.
- action-required removes resolved filesystem issues after successful block/unblock or clean sweep.

권장 command:

```bash
cd /home/mason/workspace/dms
pytest
```

## Verification Artifact

Phase 11 완료 시 `docs/dms-phase11-verification.md`를 작성한다.

반드시 포함할 내용:

- 실행 날짜와 git commit 또는 working tree 상태
- pytest 결과
- PostgreSQL operational/observability DB 이름
- DMS API/Agent deployment evidence
- c1/c2 host mount `findmnt`, `stat -f`, write probe 결과
- c1/c2 Agent report id와 mount evidence
- OpenLDAP/SSSD user/group verification
- create request ids
- expired query output
- dry-run sweep request/result
- real sweep request/result
- block observed state와 host `stat` output
- blocked 상태 access check 결과
- manual unblock request/result
- unblock 후 access check 결과
- `system`/`admin` resource skip result
- action-required output
- cleanup evidence

## Phase 11에서 하지 않을 것

다음은 Phase 11 범위가 아니다.

- filesystem quota create/update/initialize
- CephFS `ceph.quota.max_bytes`, `ceph.quota.max_files` 적용
- GPFS/WekaFS/Lustre live quota verification
- filesystem consistency check/sync
- filesystem quota drift/usage pressure action-required
- automatic expiration sweep cron/controller
- delete-on-expiry 정책
- trash/quarantine workflow
- Data Management `scan/sync/rm` live execution
- VolcanoJob create/watch/terminate
- long-running RM Worker Kubernetes Deployment/loop 운영 검증
- production Helm/Kustomize packaging 완성
- trusted ingress mTLS live validation

## Phase 11 완료 후 다음 Phase 후보

Phase 11이 성공하면 DMS는 Kubernetes storage quota RM과 host-mounted filesystem create/delete, expiry query, API-driven expiration sweep, block/unblock access lifecycle을 테스트베드에서 검증한 상태가 된다.

### Phase 12A: Filesystem Quota Lifecycle

- CephFS directory quota xattr 적용
- quota capability probe
- finite quota create/update
- quota decrease guard
- quota check/sync
- quota drift/usage pressure action-required
- GPFS/WekaFS 등 backend 확장 adapter contract 보강

### Phase 12B: Existing Directory Import / Assign Quota

- 기존 directory import
- DMS marker initialize
- existing directory ownership/permission preflight
- import 후 quota assignment
- non-DMS directory safety guard

### Phase 12C: Long-Running RM Worker Runtime Deployment

- `dms rm-worker --loop` settings/live adapter wiring
- c1/c2 dedicated RM Worker node 배포
- Agent와 Worker의 host mount capability 공유
- worker lease, stale claim, restart recovery, `UnknownAfterSideEffect` action-required 검증

### Phase 12D: Data Management Read-only Scan Preflight

- filesystem resource boundary를 read-only scan target으로 사용
- DM Agent report 기반 candidate pool
- LDAP identity mapping과 POSIX permission preflight
- mpifileutils/VolcanoJob 이전의 local scan preflight 검증

권장 순서는 Phase 12A 또는 12B로 filesystem 기능을 더 닫은 뒤 Phase 12C long-running runtime을 진행하는 것이다. backend 기능과 runtime 배포 문제를 한 phase에 섞으면 장애 원인 분리가 어려워진다.
