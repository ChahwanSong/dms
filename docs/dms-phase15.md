# DMS Phase 15 Implementation Prompt: Resource Expiry Update, Import Defaults, and Kubernetes Namespace Quota Expiry Lifecycle

이 문서는 `docs/dms-phase14.md` 완료 이후 열다섯 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 15의 목표는 **filesystem resource와 Kubernetes namespace quota resource의 expiry update/import semantics를 정리하고**, Kubernetes namespace quota Resource Management에도 filesystem Resource Management와 같은 expiry query/action-required/on-demand sweep lifecycle을 추가하는 것이다.

현재 상태:

- filesystem resource는 create payload의 `expires_at` metadata를 기준으로 expired/expiring query, action-required, on-demand expiration sweep/block 흐름을 가진다.
- 그러나 기존 filesystem resource의 expiry time을 update request로 갱신하는 기능은 명확히 열려 있지 않다.
- filesystem import는 payload passthrough로 `expires_at`이 DB desired state에 남을 수는 있지만, import 전용 default/future timestamp validation과 marker 반영 규칙이 정의되어 있지 않다.
- Kubernetes namespace quota resource는 create/update/reset payload에 `expires_at` 같은 운영 metadata를 보존할 여지는 있지만, create-time required validation, update-time preserve semantics, expired query, action-required, sweep side effect가 없다.
- Kubernetes namespace quota는 DB가 비어 있는 기존 live `ResourceQuota`를 DMS resource로 편입하는 import/adoption operation이 없고, sync-from-live도 import expiry를 설정하지 않는다.

Phase 15는 Data Management `scan/sync/rm` 구현으로 넘어가기 전에 resource expiry write/update/import semantics와 Kubernetes namespace quota expiry lifecycle을 닫는다.

## Phase 15 목표

Phase 15의 핵심 기능은 다음 일곱 가지다.

1. **Shared expiry field normalization and sanity validation**
2. **Existing filesystem resource expiry update**
3. **Import-time expiry defaulting for filesystem and Kubernetes namespace quota**
4. **Kubernetes namespace quota expiry metadata validation and persistence**
5. **Expired/expiring Kubernetes namespace quota query API**
6. **Expired-but-unblocked action-required aggregation**
7. **On-demand Kubernetes namespace quota expiration sweep**

구현 완료 기준:

- filesystem resource create request는 `expires_at`을 필수로 받는다.
- filesystem resource update request는 `expires_at`을 optional하게 변경할 수 있고, 생략하면 기존 expiry timestamp를 보존한다.
- Kubernetes namespace quota create request는 `expires_at`을 필수로 받는다.
- Kubernetes namespace quota update/default-reset request는 `expires_at`을 optional하게 변경할 수 있고, 생략하면 기존 expiry timestamp를 보존한다.
- filesystem import request와 Kubernetes namespace quota import/adoption request는 optional `expires_at`을 받는다.
- import request에 expiry timestamp가 있으면 그 값을 사용하고, 없으면 planner 기준 server-side now부터 365일 뒤 값을 canonical `expires_at`으로 설정한다.
- import request는 finite expiry를 반드시 갖는다.
- DB에는 기존 filesystem create semantics와 일관되게 canonical field `expires_at`으로 저장한다.
- API request, DB desired/applied/observed state, response field는 모두 `expires_at`만 사용한다.
- `expiry_at`이나 `clear_expires_at`이 payload에 들어오면 unsupported field로 보고 backend side effect 없이 `Rejected`로 종료한다.
- expiry timestamp는 timezone이 있는 ISO-8601 timestamp로 검증한다. `Z` suffix와 timezone offset을 허용한다.
- create/update/reset/import 같은 expiry write request에서 expiry timestamp가 planner 기준 server-side now보다 과거이거나 같으면 backend side effect 없이 `Rejected`로 종료한다.
- expiry timestamp가 없는 resource는 expiry 평가 대상이 아니다.
- expired/expiring query API로 Kubernetes namespace quota resource를 조회할 수 있다.
- expired 상태인데 block되지 않은 Kubernetes namespace quota는 `GET /api/v1/operations/action-required`에 올라가야 한다.
- on-demand expiration sweep API가 expired Kubernetes namespace quota를 실제 block 처리할 수 있다.
- sweep은 cron/controller처럼 자동 실행하지 않는다. 운영자 또는 외부 포털이 API로 요청한 경우에만 실행한다.
- sweep은 DMS-owned `ResourceQuota/dms-storage-quota`만 mutate한다.
- sweep은 기존 Kubernetes quota block semantics를 재사용한다.
  - live `ResourceQuota.spec.hard`를 zero hard로 변경한다.
  - 기존 hard limit은 `block_state.restore_hard`에 보존한다.
  - unblock 시 기존 hard limit으로 복구된다.
- `resource_type=system` 또는 `resource_type=admin`은 기본 자동 block 대상에서 제외하고 skip reason을 result/action-required에 남긴다.
- 이미 blocked 상태인 expired resource는 중복 block하지 않고 skipped 또는 already_blocked로 기록한다.
- active request/plan/run이 있는 resource는 sweep에서 skip하고 `resource_has_active_work`를 기록한다.
- 검증 결과는 `docs/dms-phase15-verification.md`와 `docs/dms-done.md`에 command/output 포함해 기록한다.

## Field Naming and Validation

기존 filesystem Resource Management는 `expires_at`을 사용한다. Phase 15도 API request, DB, desired state, applied state, observed state, query response의 field를 `expires_at`으로 통일한다.

`expiry_at`은 의미가 같은 alias이므로 별도 public API field로 열지 않는다. 두 이름을 모두 허용하면 validation, OpenAPI/schema, client SDK, UI form, audit log에서 불필요한 분기와 conflict case가 생긴다. 따라서 Phase 15 구현은 `expires_at`만 지원하고, `expiry_at`과 `clear_expires_at`은 unsupported field로 reject한다.

권장 normalization 규칙:

```text
create payload.expires_at present              -> desired_state.expires_at
create payload.expires_at missing              -> Rejected, backend_side_effect=false
update/default-reset payload.expires_at present -> desired_state.expires_at
update/default-reset payload.expires_at missing -> preserve existing desired_state.expires_at
import payload.expires_at present              -> desired_state.expires_at
import payload.expires_at missing              -> server-side now + 365 days
payload.expires_at=null                        -> Rejected, backend_side_effect=false
payload.expiry_at present                      -> Rejected, backend_side_effect=false
payload.clear_expires_at present               -> Rejected, backend_side_effect=false
```

권장 timestamp 규칙:

- `expires_at`은 timezone-aware ISO-8601이어야 한다.
- `2026-07-01T00:00:00Z`와 `2026-07-01T09:00:00+09:00`는 허용한다.
- timezone 없는 `2026-07-01T00:00:00`은 reject한다.
- planner가 request를 검증하는 시점의 server-side now보다 작거나 같은 timestamp는 reject한다.
- normalized 저장값은 UTC offset을 포함한 ISO string으로 둔다. 예: `2026-07-01T00:00:00+00:00`.
- create request에는 `expires_at`이 반드시 있어야 한다.
- update/default-reset request에서 `expires_at`을 생략하면 기존 resource의 `expires_at`을 그대로 유지한다.
- update/default-reset 대상 resource에 기존 `expires_at`이 없으면, request가 새 미래 `expires_at`을 제공하지 않는 한 reject한다. legacy no-expiry 상태를 계속 보존하지 않기 위함이다.

과거 timestamp reject는 **write request**에 적용한다. expired/expiring query의 `before`나 sweep의 `expired_before`는 과거 timestamp일 수 있다.

Import default 규칙:

- filesystem import와 Kubernetes namespace quota import/adoption은 `expires_at`이 없으면 server-side now 기준 365일 뒤 timestamp를 생성한다.
- default timestamp는 API client가 보내는 시간이 아니라 Planner가 request를 검증/plan하는 시점의 server-side UTC clock을 기준으로 계산한다.
- default 저장값도 canonical `expires_at`이며 UTC offset을 포함한다.
- import request에 `expires_at`이 있으면 default를 적용하지 않고 요청값을 검증/normalize한다.
- import request의 요청값도 현재 시각보다 과거이거나 같으면 reject한다.
- import request에서 `expiry_at` 또는 `clear_expires_at`이 들어오면 unsupported field로 reject한다.
- existing marker나 live annotation에 expiry metadata가 있더라도 source of truth는 import request 또는 default value다. 기존 marker/annotation 값은 observed evidence로 기록할 수 있지만 DB desired state를 자동으로 덮어쓰면 안 된다.

권장 reject reason 추가:

```text
expires_at_required
expires_at_field_unsupported
expires_at_default_failed
```

## Filesystem Resource Expiry Create/Update

### Create Requires Expiry

기존 filesystem create endpoint는 `expires_at`을 필수로 받는다. Phase 15에서는 기존 create path도 shared expiry helper를 사용하게 정리한다.

```text
POST /api/v1/resource-management/filesystems
```

요청 예시:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "storage_name": "cephfs",
    "directory_name": "team-a",
    "quota": {
      "capacity_bytes": 2147483648,
      "file_count": 20000
    },
    "access_policy": {
      "mode": "managed_group",
      "users": ["alice", "bob"]
    },
    "expires_at": "2026-08-01T00:00:00Z",
    "reason": "create project filesystem"
  }
}
```

규칙:

- `expires_at`이 없으면 `expires_at_required`로 reject한다.
- `expires_at`은 timezone-aware ISO-8601이고 planner server-side now보다 미래여야 한다.
- `expiry_at`이나 `clear_expires_at`이 있으면 unsupported field로 reject한다.

### Update Expiry

기존 filesystem update endpoint를 확장한다.

```text
PATCH /api/v1/resource-management/filesystems/{storage_name}/{directory_name}
```

expiry만 update:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "expires_at": "2026-08-01T00:00:00Z",
    "reason": "extend project filesystem expiry"
  }
}
```

quota와 expiry 동시 update:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "quota": {
      "capacity_bytes": 2147483648,
      "file_count": 20000
    },
    "expires_at": "2026-08-01T00:00:00Z",
    "reason": "increase quota and extend expiry"
  }
}
```

규칙:

- update payload에 `expires_at`이 없으면 기존 expiry metadata를 보존한다.
- update 대상 기존 resource에 `expires_at`이 없으면, request가 새 미래 `expires_at`을 제공하지 않는 한 `expires_at_required`로 reject한다.
- update 대상 filesystem resource가 없거나 `Deleted`이면 reject한다.
- expiry-only update는 quota, POSIX mode, group, block state를 바꾸면 안 된다.
- expiry-only update는 DMS ownership marker의 metadata만 갱신하거나, backend marker update가 아직 구현하기 어렵다면 operational DB desired/applied state에 정확히 기록하고 adapter observed state에 marker update unsupported 여부를 명확히 남긴다.
- 과거 또는 현재 timestamp는 reject한다.
- invalid timestamp는 reject한다.
- `expiry_at`이나 `clear_expires_at`이 있으면 unsupported field로 reject한다.
- expired-unblocked filesystem action-required는 expiry가 미래로 연장되면 사라져야 한다.

구현 힌트:

- `PHASE12_FILESYSTEM_UPDATE_ALLOWED_PAYLOAD_FIELDS`에 `expires_at`만 추가한다.
- filesystem create validation도 같은 shared expiry validation helper를 쓰도록 정리한다.
- CephFS host adapter update path가 marker `.dms-resource.json`을 갱신한다면 `expires_at`도 반영한다.

## Import-Time Expiry Semantics

### Filesystem Import

기존 filesystem import endpoint를 확장한다.

```text
POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:import
```

요청 expiry 사용:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "import_mode": "full",
    "initialize_marker": true,
    "access_policy": {
      "mode": "adopt_existing_group",
      "expected_group": "dms-team-a",
      "expected_mode": "0770",
      "users": ["alice", "bob"],
      "denied_users": ["mallory"]
    },
    "quota": {
      "capacity_bytes": 2147483648,
      "file_count": 20000
    },
    "expires_at": "2027-06-01T00:00:00Z",
    "reason": "import existing project filesystem"
  }
}
```

요청 expiry 생략:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "import_mode": "full",
    "initialize_marker": true,
    "access_policy": {
      "mode": "adopt_existing_group",
      "expected_group": "dms-team-a",
      "expected_mode": "0770",
      "users": ["alice", "bob"]
    },
    "reason": "import existing project filesystem"
  }
}
```

두 번째 요청은 Planner가 다음처럼 canonical expiry를 채워야 한다.

```json
{
  "expires_at": "<planner_server_utc_now_plus_365_days>"
}
```

규칙:

- `expires_at`이 있으면 shared expiry validation을 적용한다.
- `expires_at`이 없으면 server-side now + 365일을 `expires_at`으로 설정한다.
- import payload의 `expires_at`이 과거 또는 현재 timestamp이면 reject한다.
- import payload의 `expiry_at`이나 `clear_expires_at`은 unsupported field로 reject한다.
- quota-only managed filesystem resource를 full import로 승격하는 경우에도 request expiry 또는 default expiry를 새 full-managed desired state에 기록한다.
- import marker `.dms-resource.json`에는 normalized `expires_at`을 기록한다.
- 기존 marker에 `expires_at`이 있더라도 request/default 값과 다르면 request/default 값을 source of truth로 쓰고, 기존 marker 값은 `observed_state.previous_marker_expires_at` 같은 evidence로 남긴다.
- import 성공 후 해당 filesystem은 기존 expiry query/action-required/sweep 대상이 된다.

### Kubernetes Namespace Quota Import

Kubernetes namespace quota에도 explicit import/adoption endpoint를 추가한다.

```text
POST /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:import
```

권장 operation kind:

```text
kubernetes.namespace_quota.import
```

요청 expiry 사용:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "resource_quota_name": "dms-storage-quota",
    "resource_type": "user",
    "storage_class_quotas": [
      {
        "storage_name": "longhorn-b"
      }
    ],
    "expires_at": "2027-06-01T00:00:00Z",
    "reason": "import existing DMS ResourceQuota"
  }
}
```

요청 expiry 생략:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "resource_quota_name": "dms-storage-quota",
    "resource_type": "user",
    "reason": "import existing DMS ResourceQuota"
  }
}
```

규칙:

- import 대상은 live Kubernetes namespace에 이미 존재하는 DMS-owned `ResourceQuota/dms-storage-quota`다.
- Phase 15 import는 live `ResourceQuota.spec.hard`를 변경하지 않는다.
- live `spec.hard`를 읽어 DB desired/applied/observed state를 bootstrap한다.
- `expires_at`이 있으면 shared expiry validation을 적용한다.
- `expires_at`이 없으면 server-side now + 365일을 `expires_at`으로 설정한다.
- import payload의 `expiry_at`이나 `clear_expires_at`은 unsupported field로 reject한다.
- import payload의 `expires_at`이 과거 또는 현재 timestamp이면 reject한다.
- live `ResourceQuota`의 annotation `dms.io/expires-at`이 있더라도 source of truth는 import request 또는 default value다. live annotation 값은 observed evidence로 기록할 수 있다.
- adapter가 안전하게 metadata patch를 지원하면 import 후 `dms.io/expires-at: <normalized expires_at>` annotation을 반영한다. 지원하지 않으면 DB desired/applied state에는 정확히 기록하고 observed state에 `annotation_update_unsupported=true`를 남긴다.
- `storage_class_quotas[].storage_name`이 생략되면 live hard의 StorageClass quota key를 repository storage mapping으로 역산한다. 역산이 불가능하거나 ambiguous하면 reject한다.
- non-DMS `ResourceQuota` adoption은 Phase 15 범위가 아니다. DMS metadata가 없거나 `resource_quota_name`이 DMS 전용 이름이 아니면 fail-closed한다.
- import 성공 후 해당 Kubernetes namespace quota는 expiry query/action-required/sweep 대상이 된다.

## Kubernetes Namespace Quota Expiry Metadata

### Create Requires Expiry

기존 create endpoint를 확장하고 `expires_at`을 필수로 받는다.

```text
POST /api/v1/resource-management/kubernetes/namespace-quotas
```

요청 예시:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "cluster_name": "cluster-b",
    "namespace_name": "team-a",
    "allow_namespace_create": true,
    "resource_type": "user",
    "quota": {
      "requests_storage_bytes": 1073741824,
      "pvc_count": 20
    },
    "storage_class_quotas": [
      {
        "storage_name": "longhorn-b",
        "requests_storage_bytes": 536870912,
        "pvc_count": 10
      }
    ],
    "expires_at": "2026-07-01T00:00:00Z",
    "memo": "team-a initial quota"
  }
}
```

persisted desired state에는 다음처럼 저장돼야 한다.

```json
{
  "expires_at": "2026-07-01T00:00:00+00:00"
}
```

규칙:

- `expires_at`이 없으면 `expires_at_required`로 reject한다.
- `expires_at`은 timezone-aware ISO-8601이고 planner server-side now보다 미래여야 한다.
- `expiry_at`이나 `clear_expires_at`이 있으면 unsupported field로 reject한다.

### Update Expiry

기존 update endpoint를 확장한다.

```text
PATCH /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
```

quota와 expiry 동시 update:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "quota": {
      "requests_storage_bytes": 2147483648,
      "pvc_count": 40
    },
    "storage_class_quotas": [
      {
        "storage_name": "longhorn-b",
        "requests_storage_bytes": 1073741824,
        "pvc_count": 20
      }
    ],
    "expires_at": "2026-08-01T00:00:00Z",
    "memo": "quota and expiry extended"
  }
}
```

expiry만 update:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "expires_at": "2026-08-01T00:00:00Z",
    "memo": "expiry extended"
  }
}
```

규칙:

- update payload에 `expires_at`이 없으면 기존 expiry metadata를 보존한다.
- update 대상 기존 resource에 `expires_at`이 없으면, request가 새 미래 `expires_at`을 제공하지 않는 한 `expires_at_required`로 reject한다.
- `reset_quota_to_default=true` update에서도 optional `expires_at` update를 허용한다.
- blocked resource update semantics는 Phase 7/9와 동일하게 유지한다. blocked 상태에서 expiry만 연장하는 것은 live hard를 non-zero로 되돌리면 안 된다.
- expiry-only update는 quota decrease guard와 별개 metadata update다. quota 변경이 없고 expiry만 변경하는 request는 현재 hard limit을 그대로 유지해야 한다.
- 과거 또는 현재 timestamp는 reject한다.
- invalid timestamp는 reject한다.
- `expiry_at`이나 `clear_expires_at`이 있으면 unsupported field로 reject한다.

### Default Reset With Expiry Update

기존 default reset endpoint도 expiry metadata를 optional하게 받는다.

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "reset_quota_to_default": true,
    "resource_type": "user",
    "expires_at": "2026-08-01T00:00:00Z",
    "memo": "reset quota and extend expiry"
  }
}
```

규칙:

- default quota policy는 quota hard limit만 제공한다.
- expiry metadata는 request payload의 값을 사용하거나, payload에 없으면 기존 resource desired state를 보존한다.
- default reset 대상 기존 resource에 `expires_at`이 없으면, request가 새 미래 `expires_at`을 제공하지 않는 한 `expires_at_required`로 reject한다.
- `expiry_at`이나 `clear_expires_at`이 있으면 unsupported field로 reject한다.

## Kubernetes Expired/Expiring Query

새 operational query API를 추가한다.

```text
GET /api/v1/operations/kubernetes/namespace-quotas/expiring
```

권장 query parameter:

```text
cluster_name=<optional>
namespace_name=<optional>
resource_type=<optional>
status=expired|expiring|all        # optional, default: expired
before=<ISO-8601 timestamp>        # optional, default: now
within_seconds=<integer>           # optional, expiring 조회 window
include_blocked=<true|false>       # optional, default: false
limit=<integer>                    # optional, default: repository/API 기본값
```

응답 예시:

```json
[
  {
    "resource_kind": "kubernetes_namespace_quota",
    "resource_key": "cluster-b:team-a",
    "cluster_name": "cluster-b",
    "namespace_name": "team-a",
    "resource_type": "user",
    "status": "Succeeded",
    "expires_at": "2026-07-01T00:00:00+00:00",
    "expired": true,
    "expiring": false,
    "seconds_overdue": 3600,
    "block_state": {
      "blocked": false
    },
    "resource_quota_name": "dms-storage-quota",
    "desired_hard": {
      "requests.storage": "1Gi",
      "persistentvolumeclaims": "20",
      "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "512Mi",
      "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "10"
    },
    "updated_at": "2026-07-01T01:00:00+00:00"
  }
]
```

규칙:

- 이 API는 DB 조회 API다. live Kubernetes mutation을 수행하지 않는다.
- 기본적으로 `Deleted` resource는 제외한다.
- 기본적으로 blocked resource는 제외하고, `include_blocked=true`이면 표시한다.
- `expires_at`이 없으면 만료 평가 대상이 아니다.
- `before`가 없으면 server-side now를 기준으로 한다.
- `status=expiring`은 `before < expires_at <= before + within_seconds`인 resource를 반환한다.
- `status=all`은 expired 또는 expiring resource를 모두 반환한다.
- 잘못된 timestamp/status는 HTTP 422로 반환한다.

## Kubernetes Expiration Sweep

새 mutating request API를 추가한다.

```text
POST /api/v1/resource-management/kubernetes/namespace-quotas:expiration-sweep
```

권장 operation kind:

```text
kubernetes.namespace_quota.expiration_sweep
```

권장 resource kind:

```text
kubernetes_namespace_quota
```

권장 resource key:

```text
kubernetes-namespace-quota-expiration-sweep
```

요청 예시:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "scope": {
      "cluster_name": "cluster-b",
      "resource_type": "user"
    },
    "expired_before": "2026-07-01T00:00:00Z",
    "action": "block",
    "dry_run": false,
    "max_targets": 100,
    "reason": "quota expiry sweep"
  }
}
```

단일 namespace만 대상으로 하는 요청:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "scope": {
      "cluster_name": "cluster-b",
      "namespace_name": "team-a"
    },
    "expired_before": "2026-07-01T00:00:00Z",
    "action": "block",
    "dry_run": false,
    "reason": "team-a quota expired"
  }
}
```

규칙:

- Phase 15에서 `action`은 `block`만 지원한다.
- `dry_run=true`이면 target 산정과 skip/failure reason만 result에 기록하고 live backend side effect를 수행하지 않는다.
- `max_targets` 기본값은 100, 허용 상한은 1000으로 둔다.
- `expired_before`가 없으면 server-side now를 사용한다.
- `scope.cluster_name`이 있으면 해당 cluster만 대상으로 한다.
- `scope.namespace_name`이 있으면 해당 namespace만 대상으로 한다. 이 경우 `cluster_name`도 필수다.
- `scope.resource_type`이 있으면 해당 resource type만 대상으로 한다.
- target resource는 DMS DB에 존재하고 `Deleted`가 아니어야 한다.
- target resource는 expired 상태여야 한다.
- target resource가 already blocked이면 side effect 없이 skipped로 기록한다.
- target resource에 active request/plan/run이 있으면 skipped로 기록한다.
- target storage mappings의 RM readiness가 `Ready`가 아니면 side effect 전에 skipped 또는 fail-closed 처리한다.
- target의 live `ResourceQuota`가 없으면 failed target으로 기록하거나, policy상 DMS resource를 missing으로 action-required에 남긴다. 자동 recreate는 Phase 15 범위가 아니다.
- live `ResourceQuota`가 DMS-managed metadata를 만족하지 않으면 mutate하지 않고 failed target으로 기록한다.
- sweep은 target별 결과를 `verification_summary.targets[]`에 남긴다.

sweep result의 `verification_summary` 예시:

```json
{
  "adapter": "kubernetes-namespace-quota-expiration-sweep",
  "operation": "kubernetes.namespace_quota.expiration_sweep",
  "backend_side_effect": true,
  "dry_run": false,
  "expired_before": "2026-07-01T00:00:00+00:00",
  "action": "block",
  "target_count": 3,
  "blocked_count": 1,
  "skipped_count": 2,
  "failed_count": 0,
  "targets": [
    {
      "resource_key": "cluster-b:team-a",
      "cluster_name": "cluster-b",
      "namespace_name": "team-a",
      "expires_at": "2026-07-01T00:00:00+00:00",
      "result": "blocked",
      "backend_side_effect": true,
      "observed_state": {
        "resource_quota_name": "dms-storage-quota",
        "spec_hard": {
          "requests.storage": "0",
          "persistentvolumeclaims": "0"
        }
      }
    },
    {
      "resource_key": "cluster-b:team-system",
      "cluster_name": "cluster-b",
      "namespace_name": "team-system",
      "resource_type": "system",
      "result": "skipped",
      "reason": "resource_type_not_auto_blocked"
    },
    {
      "resource_key": "cluster-b:team-active",
      "cluster_name": "cluster-b",
      "namespace_name": "team-active",
      "result": "skipped",
      "reason": "resource_has_active_work"
    }
  ]
}
```

## Action-Required Semantics

`GET /api/v1/operations/action-required`는 expired but unblocked Kubernetes namespace quota를 반환해야 한다.

권장 issue:

```json
{
  "issue_type": "kubernetes_quota_expired_unblocked",
  "severity": "WARN",
  "resource_kind": "kubernetes_namespace_quota",
  "resource_key": "cluster-b:team-a",
  "cluster_name": "cluster-b",
  "namespace_name": "team-a",
  "resource_type": "user",
  "expires_at": "2026-07-01T00:00:00+00:00",
  "seconds_overdue": 3600,
  "recommended_action": "run Kubernetes namespace quota expiration sweep or manually block the resource"
}
```

규칙:

- expired이고 block되지 않았으면 action-required에 올라간다.
- sweep으로 block이 성공한 뒤에는 기본 action-required에서 사라져야 한다.
- `include_blocked` query에는 보이더라도 action-required는 blocked resource를 기본적으로 제외한다.
- `resource_type=system/admin`도 expired 상태는 action-required로 표시할 수 있다. 다만 sweep 자동 block은 skip하고 운영자 판단을 요구한다.
- sweep target failure 또는 skip은 별도 issue로 남길 수 있다.
  - `kubernetes_quota_expiration_sweep_failed`
  - `kubernetes_quota_expiration_sweep_skipped`

filesystem action-required도 expiry update 이후 정확히 재계산돼야 한다.

- expired filesystem의 `expires_at`을 미래로 update하면 `filesystem_expired_unblocked` issue가 사라져야 한다.
- 미래 expiry를 과거로 update하는 방식은 허용하지 않는다. 테스트에서 expired 상태가 필요하면 repository fixture 또는 짧은 미래 expiry 후 대기 방식을 사용한다.

## Implementation Hints

### Shared Expiry Helper

- filesystem과 Kubernetes quota가 함께 쓰는 helper를 둔다.
- helper 책임:
  - `expires_at` 단일 field validation
  - create operation에서 missing `expires_at` reject
  - update/default-reset operation에서 missing `expires_at`이면 기존 값 preserve
  - update/default-reset 대상 기존 resource에 `expires_at`이 없을 때 missing `expires_at` reject
  - `expiry_at`/`clear_expires_at` unsupported field reject
  - timezone-aware ISO parsing
  - write request timestamp future validation
  - import operation에서 missing expiry를 server-side now + 365일 default로 채우기
  - normalized ISO string 반환
- reject reason은 테스트 가능하게 안정적인 문자열로 둔다.
  - `expires_at_required`
  - `expires_at_invalid`
  - `expires_at_timezone_required`
  - `expires_at_not_future`
  - `expires_at_field_unsupported`
  - `expires_at_default_failed`

### Domain

- `OperationKind.K8S_QUOTA_EXPIRATION_SWEEP = "kubernetes.namespace_quota.expiration_sweep"` 추가.
- `OperationKind.K8S_QUOTA_IMPORT = "kubernetes.namespace_quota.import"` 추가.
- `RM_OPERATIONS`에 sweep/import operation 추가.

### Repository

- filesystem의 `list_filesystem_resources_expiring(...)`와 동일한 형태로 `list_kubernetes_namespace_quota_resources_expiring(...)`를 추가한다.
- source는 `resources` table의 `resource_kind='kubernetes_namespace_quota'`.
- `desired_state.expires_at`을 우선 사용하고, 필요하면 `applied_state.expires_at` fallback을 허용한다.
- 반환 row에는 `cluster_name`, `namespace_name`, `resource_type`, `expires_at`, `expired`, `expiring`, `seconds_overdue`, `block_state`를 포함한다.

### Planner

- filesystem create/update validation에 shared expiry validation을 적용한다.
- filesystem import validation에 shared expiry validation과 import defaulting을 적용한다.
- filesystem update allowed payload fields에 `expires_at`만 추가한다.
- filesystem import allowed payload fields에 `expires_at`만 추가한다.
- Kubernetes quota create/update/default-reset/import validation에 shared expiry validation을 적용한다.
- Kubernetes quota create payload에서 `expires_at`이 없으면 reject한다.
- update/default-reset payload에 `expires_at`이 없으면 기존 desired state를 보존한다.
- update/default-reset 대상 기존 resource에 `expires_at`이 없으면 request가 새 `expires_at`을 제공하지 않는 한 reject한다.
- import payload에 expiry 관련 field가 없으면 request-scoped default가 아니라 planner server-side now + 365일 default를 desired state에 저장한다.
- `expiry_at`이나 `clear_expires_at`이 있으면 unsupported field로 reject한다.
- Kubernetes namespace quota import request validation을 추가한다.
  - `cluster_name`/`namespace_name`은 path parameter에서 채운다.
  - resource가 DB에 이미 존재하고 `Deleted`가 아니면 reject 또는 documented no-op가 아니라 conflict로 처리한다.
  - `resource_quota_name` 기본값은 `dms-storage-quota`.
  - `resource_type` 기본값은 `user`.
  - non-DMS ResourceQuota adoption은 reject.
  - live hard의 StorageClass key를 storage mapping으로 역산할 수 없으면 reject.
- sweep request validation을 추가한다.
  - `expired_before` timestamp validation
  - `action=block`만 허용
  - `dry_run` boolean 검증
  - `max_targets` range 검증
  - `scope.namespace_name`이 있으면 `scope.cluster_name` 필수
- sweep desired state에 resolved targets를 포함한다.
- expiry-only Kubernetes update가 현재 quota hard를 유지하도록 `_merge_kubernetes_quota_desired()`와 `_should_preserve_kubernetes_quota_hard()` 흐름을 점검한다.

### Worker

- filesystem expiry-only update가 불필요한 quota/permission side effect를 만들지 않도록 adapter update result를 점검한다.
- filesystem import plan의 desired/applied state와 marker에 normalized `expires_at`이 남는지 점검한다.
- `RMWorkerRuntime._apply()`에 Kubernetes namespace quota import dispatch를 추가한다.
- `RMWorkerRuntime._apply()`에 Kubernetes quota expiration sweep dispatch를 추가한다.
- 구현은 filesystem expiration sweep처럼 하나의 sweep plan이 target list를 직접 처리해도 된다.
- 각 Kubernetes target에 대해 existing desired state를 기반으로 synthetic block plan을 만들고 기존 `kubernetes_adapter.apply_resource_quota()` path를 재사용한다.
- block 성공 시 repository resource state를 `Blocked` 또는 기존 block semantics와 같은 status로 갱신한다.
- dry-run에서는 backend side effect 없이 result만 기록한다.
- per-target failure는 sweep 전체를 즉시 unknown으로 만들지 말고 target result에 기록한다. 단, repository/core lifecycle write failure는 hard failure로 둔다.

### Adapter

- filesystem marker metadata에 `expires_at` create/import/update를 반영한다.
- Kubernetes `ResourceQuota` manifest annotations에 expiry metadata를 반영한다.
  - `dms.io/expires-at: <normalized expires_at>`
- Kubernetes import는 live `ResourceQuota.spec.hard`를 변경하지 않는다. metadata annotation patch는 안전하게 지원될 때만 수행하고, 미지원이면 DB state와 observed warning으로 남긴다.
- expiry annotation은 source of truth가 아니다. operational DB desired state가 source of truth다.
- live query/check/sync에서 annotation을 observed metadata로 반환하면 운영 디버깅에 도움이 된다.

### Query

- `OperationalQueryService.kubernetes_namespace_quota_expiring(...)` 추가.
- `OperationalQueryService.action_required()`에 expired-unblocked Kubernetes quota issue를 추가한다.
- filesystem expiry update 후 existing filesystem action-required 계산이 정확히 바뀌는지 regression test를 추가한다.
- sweep failure/skip result 기반 action-required aggregation은 filesystem expiration sweep과 유사하게 구현한다.

## Testbed Live Verification

Phase 15 live verification은 기존 Vagrant multi-cluster testbed를 사용한다.

기본 전제:

- fresh PostgreSQL operational/observability DB를 사용한다.
- DMS API, DMS Agent DaemonSet, Planner Deployment, RM Worker Deployment를 배포한다.
- Phase 14 verifier 또는 Phase 13 long-running RM Worker verifier를 재사용할 수 있다.
- verifier는 `Planner.run_once()` 또는 `RMWorkerRuntime.run_once()`를 직접 호출하지 않는다.

검증 항목:

1. host-mounted CephFS filesystem resource를 미래 `expires_at`과 함께 생성한다.
2. 같은 filesystem resource에 미래 `expires_at` update를 보내면 desired/applied state와 marker metadata가 갱신되는지 확인한다.
3. filesystem create/update request에서 `expires_at`이 없거나 과거/current 값이면 `Rejected`되고 backend side effect가 없는지 확인한다.
4. filesystem import request에 미래 `expires_at`을 넣으면 canonical `expires_at`으로 저장되고 marker에 반영되는지 확인한다.
5. filesystem import request에서 expiry를 생략하면 server-side now + 365일 default `expires_at`이 저장되는지 확인한다.
6. filesystem import request에서 과거 `expires_at`, `expiry_at`, 또는 `clear_expires_at`을 보내면 `Rejected`되고 marker/quota side effect가 없는지 확인한다.
7. expired filesystem resource의 expiry를 미래로 연장하면 `filesystem_expired_unblocked` action-required issue가 사라지는지 확인한다. 이 expired fixture는 repository fixture 또는 짧은 미래 expiry 후 대기 방식으로 만든다.
8. `cluster-b/testbed-longhorn` storage mapping으로 namespace quota를 생성하면서 미래 `expires_at`을 넣는다.
9. DB desired state와 query response에는 canonical `expires_at`으로 저장/반환되는지 확인한다.
10. Kubernetes quota create request에서 `expires_at`이 없거나 과거/current 값이면 `Rejected`되고 live `ResourceQuota`를 만들거나 변경하지 않는지 확인한다.
11. Kubernetes namespace quota import request에 미래 `expires_at`을 넣으면 canonical `expires_at`으로 저장되고 live hard는 변경되지 않는지 확인한다.
12. Kubernetes namespace quota import request에서 expiry를 생략하면 server-side now + 365일 default `expires_at`이 저장되는지 확인한다.
13. Kubernetes namespace quota import request에서 과거 `expires_at`, `expiry_at`, 또는 `clear_expires_at`을 보내면 `Rejected`되고 live `ResourceQuota`가 변경되지 않는지 확인한다.
14. future `expires_at` resource는 `status=expired` query와 action-required에 나타나지 않는다.
15. 짧은 미래 expiry를 설정하고 만료될 때까지 bounded wait한 뒤 expired query API가 target을 반환하는지 확인한다.
16. expired but unblocked 상태가 action-required에 `kubernetes_quota_expired_unblocked`로 올라간다.
17. `dry_run=true` expiration sweep은 target만 기록하고 live ResourceQuota hard를 바꾸지 않는다.
18. `dry_run=false` expiration sweep은 live `ResourceQuota/dms-storage-quota` hard를 zero로 만든다.
19. block 후 action-required에서 expired-unblocked issue가 사라진다.
20. unblock request가 기존 hard limit을 복구한다.
21. `resource_type=system` expired quota는 sweep에서 skip되고 skip reason이 result/action-required에 남는다.
22. namespace cleanup은 테스트 cleanup으로만 수행한다. DMS lifecycle namespace delete를 구현한 것으로 보지 않는다.

검증 결과는 `docs/dms-phase15-verification.md`와 `docs/dms-done.md`에 기록한다.

## Minimum Local Tests

- filesystem update payload의 `expires_at`이 existing desired state에 저장된다.
- filesystem update payload에서 `expires_at`을 생략하면 기존 desired state의 `expires_at`이 보존된다.
- filesystem update payload의 과거/current `expires_at`은 `Rejected`되고 plan이 생성되지 않는다.
- filesystem update payload의 `expiry_at` 또는 `clear_expires_at`은 `Rejected`되고 plan이 생성되지 않는다.
- filesystem create payload에 `expires_at`이 없으면 `Rejected`되고 plan이 생성되지 않는다.
- filesystem import payload의 `expires_at`이 desired state와 marker metadata에 저장된다.
- filesystem import payload의 missing expiry가 server-side now + 365일 default로 채워진다.
- filesystem import payload의 과거/current `expires_at`, `expiry_at`, 또는 `clear_expires_at`은 `Rejected`되고 plan이 생성되지 않는다.
- expired filesystem의 expiry를 미래로 update하면 action-required issue가 사라진다.
- Kubernetes create payload의 `expires_at`이 desired state에 저장된다.
- Kubernetes create payload에 `expires_at`이 없으면 `Rejected`되고 plan이 생성되지 않는다.
- Kubernetes update payload의 `expires_at`이 기존 desired state에 merge된다.
- Kubernetes update/default-reset payload에서 `expires_at`을 생략하면 기존 desired state의 `expires_at`이 보존된다.
- Kubernetes payload의 `expiry_at` 또는 `clear_expires_at`은 `Rejected`되고 plan이 생성되지 않는다.
- Kubernetes import payload의 `expires_at`이 desired state와 observed metadata evidence에 저장된다.
- Kubernetes import payload의 missing expiry가 server-side now + 365일 default로 채워진다.
- Kubernetes import payload의 과거/current `expires_at`, `expiry_at`, 또는 `clear_expires_at`은 `Rejected`되고 plan이 생성되지 않는다.
- Kubernetes import는 live `ResourceQuota.spec.hard`를 바꾸지 않고 DB desired/applied/observed state를 bootstrap한다.
- invalid timestamp, timezone 없는 timestamp, 과거/current timestamp가 backend side effect 없이 reject된다.
- expired Kubernetes namespace quota query가 expired/expiring/all을 올바르게 반환한다.
- expired but unblocked quota가 action-required에 나타난다.
- blocked quota는 기본 action-required에서 제외된다.
- dry-run sweep은 live adapter call 없이 target summary만 기록한다.
- real sweep은 Kubernetes quota block adapter path를 호출하고 resource state/block_state를 갱신한다.
- system/admin resource는 sweep에서 skip된다.
- active work target은 sweep에서 skip된다.
- long-running RM Worker registry는 Phase 14 live fail-closed behavior를 유지한다.

## Phase 15에서 하지 않을 것

다음은 Phase 15 범위가 아니다.

- automatic cron/controller 기반 expiry sweep
- Kubernetes namespace delete lifecycle
- non-DMS ResourceQuota mutation
- non-DMS ResourceQuota adoption/import
- quota drift/usage pressure 자동 cron/controller
- filesystem expiry 자동 cron/controller
- filesystem expiry delete/archive policy
- Data Management `scan/sync/rm` live execution
- VolcanoJob create/watch/terminate
- DM Worker long-running runtime 검증
- worker lease/heartbeat renewal과 stale recovery guard
- maintenance/drain enforcement와 control API/CLI
- production Helm chart 완성
- GPFS live testbed 구축
- WekaFS/Lustre live implementation

## Phase 15 이후 다음 작업 리스트

Data Management 후보는 resource expiry update/import default와 Kubernetes namespace quota expiry lifecycle 이후로 미뤘지만, 현재 DMS API 인증 경계는 아직 shared token과 trusted actor header skeleton 수준이다. 따라서 다음 phase는 Data Management 구현 전에 external API mTLS validation과 auth boundary hardening을 먼저 닫는 Phase 16으로 진행한다.

### Phase 16: External API mTLS Validation and Auth Boundary Hardening

- `DMS_REQUIRE_MTLS_HEADER`와 `DMS_REQUIRE_MTLS_VERIFIED_HEADER` 설정 추가
- DMS edge proxy header family(`X-DMS-Client-Cert-*`) validation
- ingress-nginx header family(`ssl-client-*`) validation
- verify result `SUCCESS` 요구와 missing/failed/conflicting evidence reject
- mTLS subject 기반 actor derivation
- mTLS-required mode에서 `x-dms-actor` spoofing/conflict reject
- mTLS-required mode에서 `DMS_DEFAULT_ACTOR` fallback 비활성화
- shared bearer token과 mTLS evidence를 함께 요구하는 production install profile
- mTLS ingress example, client CA Secret, NetworkPolicy 또는 동등한 direct access 차단 문서화
- testbed에서 valid/invalid client certificate, missing/wrong token, direct spoof 차단 검증

구체 구현 프롬프트는 `docs/dms-phase16.md`를 따른다.

### Phase 17: Kubernetes ResourceQuota Live Adapter Unification

- Phase 17에서 GPFS CSI namespace quota가 live Kubernetes `ResourceQuota` adapter를 타도록 수정 완료
- CephFS, GPFS, Longhorn, WEKA 등 모든 CSI StorageClass namespace quota 경로를 backend-neutral live adapter로 통합 완료
- filesystem backend-specific adapter selection과 Kubernetes ResourceQuota adapter selection 분리 완료

구체 구현 프롬프트는 `docs/dms-phase17.md`를 따른다.

### Phase 18A: Data Management Read-only Scan Preflight

- filesystem resource boundary를 read-only scan target으로 사용
- DM Agent report 기반 candidate pool
- POSIX identity/mount/tool preflight
- VolcanoJob 이전 local scan preflight 검증

### Phase 18B: DM Worker Runtime and VolcanoJob Skeleton

- `dms dm-worker --loop` Deployment
- VolcanoJob create/watch/delete skeleton
- job lease/recovery
- artifact URI and preview lifecycle

### Phase 18C: Filesystem Policy and Initialize

- filesystem default quota policy
- `filesystem.initialize`
- `reset_quota_to_default=true`
- quota clear/unlimited lifecycle

권장 순서는 Phase 16 external API mTLS validation과 Phase 17 Kubernetes ResourceQuota live adapter 통합 완료 이후, Phase 18A로 Data Management read-only scan preflight를 구현하는 것이다.
