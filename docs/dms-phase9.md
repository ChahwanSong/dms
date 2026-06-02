# DMS Phase 9 Implementation Prompt

이 문서는 `docs/dms-phase8.md` 완료 이후 아홉 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 9의 목표는 Phase 4부터 Phase 8까지 검증한 **Kubernetes storage quota Resource Management**를 운영자가 더 안전하게 사용할 수 있도록 보강하는 것이다.

Phase 9는 새 backend runtime을 열지 않는다. Filesystem Resource Management, Data Management `scan/sync/rm`, VolcanoJob live execution, 일반 Kubernetes tenant provisioning은 이번 phase의 범위가 아니다. 이번 phase는 Kubernetes namespace storage quota 도메인 안에서 default quota reset, on-demand quota audit, drift/usage pressure 판정, action-required aggregation, DMS ownership metadata hardening을 구현한다.

중요: quota drift와 usage pressure 확인은 cron처럼 자동으로 주기 실행하지 않는다. Phase 9에서는 운영자나 외부 포털이 API를 호출했을 때만 live Kubernetes state를 읽고, 그 결과를 operational DB와 action-required query에 반영한다.

## Phase 9 목표

Phase 9의 핵심 기능은 다음 네 가지다.

1. **Kubernetes default quota policy reset workflow**
2. **On-demand Kubernetes quota audit API**
3. **Quota drift / usage pressure action-required aggregation**
4. **DMS-managed ResourceQuota ownership metadata hardening**

구현 완료 기준은 다음과 같다.

- Kubernetes namespace quota update에서 `reset_quota_to_default=true`를 지원한다.
- default quota policy는 `resource_kind=kubernetes_namespace_quota`와 `resource_type` 기준으로 조회한다.
- default policy가 없는데 reset이 요청되면 backend side effect 없이 `Rejected`로 종료한다.
- reset 결과는 DMS DB desired state, live `ResourceQuota.spec.hard`, observed state, result summary에 모두 기록된다.
- blocked 상태에서 reset이 들어오면 Phase 7 blocked update semantics를 유지한다.
  - live hard limit은 계속 `0`으로 유지한다.
  - unblock 시 복구할 `block_state.restore_hard`만 default quota hard로 갱신한다.
- on-demand audit API를 추가해 운영자가 요청한 scope의 Kubernetes namespace quota를 live read/check할 수 있다.
- audit은 Kubernetes object를 변경하지 않는다.
- audit은 DB desired state와 live `ResourceQuota.spec.hard` drift를 구조화한다.
- audit은 live `ResourceQuota.status.used`를 기준으로 usage pressure를 계산한다.
- audit은 namespace 안의 non-DMS `ResourceQuota`가 effective quota에 미치는 warning을 포함할 수 있다.
- audit 결과는 operational DB에 저장되고, `GET /api/v1/operations/action-required`에서 운영자가 처리해야 할 항목으로 조회된다.
- 이후 같은 resource에 대해 audit/check가 다시 실행되어 문제가 해소되면 action-required에서 해당 issue가 사라져야 한다.
- DMS-managed `ResourceQuota/dms-storage-quota` mutation은 name뿐 아니라 DMS ownership label/annotation을 확인한 뒤 수행한다.
- 검증은 테스트베드의 `cluster-a/testbed-cephfs`, `cluster-b/testbed-longhorn`, `cluster-b/longhorn-static`에서 synthetic Agent report 없이 수행한다.
- 검증 결과는 `docs/dms-phase9-verification.md`와 `docs/dms-done.md`에 command/output 포함해 기록한다.

## 왜 Phase 9에서 Kubernetes storage quota RM을 보강하는가

Phase 8까지 DMS는 다음을 실제 테스트베드에서 검증했다.

- Kubernetes namespace quota create/apply/update/delete
- quota decrease guard
- block/unblock
- check/sync
- multi-StorageClass quota
- non-DMS ResourceQuota effective warning
- requester-scoped request query
- Kubernetes namespace quota dedicated query API
- 실제 Agent DaemonSet 기반 storage mapping readiness

따라서 Kubernetes storage quota lifecycle은 기본 기능 관점에서는 충분히 구현됐다. 하지만 운영자가 실제로 쓰기에는 아직 다음 빈틈이 남아 있다.

- default quota policy를 저장할 수는 있지만, Kubernetes quota resource를 default policy로 reset하는 live workflow가 닫혀 있지 않다.
- drift와 usage pressure는 check/query에서 확인할 수 있으나, 운영자가 한 화면에서 처리할 action-required 항목으로 모으는 기능이 부족하다.
- drift/pressure 확인을 자동 cron으로 돌리기 전에, 우선 API 요청 기반으로 정확한 read/check 결과와 action-required 반영 semantics를 검증해야 한다.
- DMS-managed `ResourceQuota` mutation safety가 production 수준으로 가려면 DMS ownership metadata 확인을 더 엄격하게 해야 한다.

Phase 9는 Kubernetes storage quota RM의 마지막 운영 보강 phase로 둔다. 이 단계가 끝나면 다음 phase에서는 Filesystem Resource Management minimal lifecycle 또는 Data Management read-only preflight로 넘어가는 것이 적절하다.

## 현재 전제

Phase 8 완료 후 전제:

- Kubernetes namespace quota resource identity는 `cluster_name + namespace_name`이다.
- DMS가 소유하는 Kubernetes object는 namespace 안의 `ResourceQuota/dms-storage-quota`다.
- `storage_class_quotas[].storage_name`은 StorageClass별 quota dimension이다.
- `storage_class_name`은 storage mapping에서 derive한다.
- DMS는 live Kubernetes API에서 `ResourceQuota.spec.hard`, `status.hard`, `status.used`를 읽을 수 있다.
- DMS는 namespace 안의 non-DMS `ResourceQuota`를 read-only list할 수 있다.
- DMS는 dedicated quota query API로 DB state와 live state를 함께 조회할 수 있다.
- DMS Agent DaemonSet report가 storage mapping readiness의 authoritative evidence다.
- `GET /api/v1/operations/action-required`는 이미 request attention, failed mapping, missing readiness, stale Agent report 등을 반환한다.
- repository request list 기본 limit은 1000개다.

테스트베드 topology:

- `cluster-a`
  - control cluster 역할
  - self-managed RM target
  - Rook/CephFS `StorageClass/testbed-cephfs`
- `cluster-b`
  - managed cluster 역할
  - Longhorn `StorageClass/testbed-longhorn`
  - Longhorn `StorageClass/longhorn-static`
- PostgreSQL
  - `192.168.56.11:30432`
  - 테스트 실행마다 operational DB와 observability DB를 새로 만든다.

## 기능 1: Kubernetes Default Quota Policy Reset

### API

기존 default quota policy API를 사용한다.

```text
POST /api/v1/resource-management/default-quota-policies
```

Kubernetes namespace quota update API에 `reset_quota_to_default=true` payload를 추가한다.

```text
PATCH /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
```

### Default Policy Payload

권장 payload:

```json
{
  "resource_kind": "kubernetes_namespace_quota",
  "resource_type": "user",
  "quota": {
    "requests_storage_bytes": 1073741824,
    "pvc_count": 20,
    "storage_class_quotas": [
      {
        "storage_name": "longhorn-b",
        "requests_storage_bytes": 536870912,
        "pvc_count": 10
      },
      {
        "storage_name": "longhorn-static-b",
        "requests_storage_bytes": 268435456,
        "pvc_count": 4
      }
    ]
  }
}
```

`storage_class_quotas`는 optional이다.

- policy에 `storage_class_quotas`가 있으면 mapping에서 `storage_class_name`을 derive해 hard key를 렌더링한다.
- policy에 `storage_class_quotas`가 없으면 namespace-wide quota만 남기고 기존 DMS desired state의 StorageClass-specific quota hard key는 제거한다.
- policy의 `storage_name` mapping이 없거나 target cluster와 맞지 않으면 reset request는 `Rejected`가 되어야 한다.

### Reset Payload

예시:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "reset_quota_to_default": true,
    "resource_type": "user",
    "memo": "reset team namespace quota to default policy"
  }
}
```

규칙:

- `resource_type`이 payload에 있으면 해당 type의 default policy를 사용한다.
- `resource_type`이 payload에 없으면 기존 resource desired state의 `resource_type`을 사용한다.
- default policy가 없으면 backend side effect 없이 reject한다.
- reset은 namespace name, cluster name, DMS resource identity를 변경하지 않는다.
- reset은 `memo`, `expires_at` 같은 운영 metadata를 payload에 있으면 갱신할 수 있다.
- reset은 quota decrease guard를 통과해야 한다.
- reset 결과가 live `status.used`보다 낮으면 backend side effect 없이 reject한다.
- blocked resource에서 reset하면 live `ResourceQuota.spec.hard`는 계속 `0`이어야 한다.
- blocked resource에서 reset하면 `block_state.restore_hard`가 default hard로 바뀌고, unblock 시 default hard로 복구되어야 한다.

### Render Example

위 default policy는 다음 hard limit으로 렌더링된다.

```yaml
hard:
  requests.storage: 1Gi
  persistentvolumeclaims: "20"
  testbed-longhorn.storageclass.storage.k8s.io/requests.storage: 512Mi
  testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims: "10"
  longhorn-static.storageclass.storage.k8s.io/requests.storage: 256Mi
  longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims: "4"
```

## 기능 2: On-Demand Kubernetes Quota Audit API

### API

새 read-only request API를 추가한다.

```text
POST /api/v1/resource-management/kubernetes/namespace-quotas:audit
```

이 API는 운영자가 요청했을 때만 실행된다. API server, Planner, Worker가 주기적으로 자동 호출하면 안 된다.

권장 operation kind:

```text
kubernetes.namespace_quota.audit
```

권장 resource kind:

```text
kubernetes_namespace_quota
```

resource key:

- 단일 대상이면 `<cluster_name>:<namespace_name>`
- 여러 대상이면 `kubernetes-namespace-quota-audit`

### Single-Target Audit Payload

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "scope": {
      "cluster_name": "cluster-b",
      "namespace_name": "team-a"
    },
    "include_non_dms": true,
    "include_usage_pressure": true,
    "usage_thresholds": {
      "warning_percent": 80,
      "critical_percent": 95
    },
    "record_action_required": true,
    "reason": "operator requested quota audit"
  }
}
```

### Filtered Audit Payload

Phase 9는 전체 cluster scan을 무조건 넓게 돌리지 않는다. 운영자가 명시한 filter 범위 안에서만 audit한다.

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "scope": {
      "cluster_name": "cluster-b",
      "requester_id": "portal:team-a",
      "status": ["Active", "Blocked"]
    },
    "include_non_dms": true,
    "include_usage_pressure": true,
    "usage_thresholds": {
      "warning_percent": 70,
      "critical_percent": 90
    },
    "max_targets": 50,
    "record_action_required": true
  }
}
```

지원 scope:

- `cluster_name`
- `namespace_name`
- `requester_id`
- `resource_type`
- `status`
- `storage_name`

`storage_name` scope는 해당 storage mapping을 사용하는 namespace quota resource만 audit한다. StorageClass-specific quota 없이 namespace-wide quota만 가진 resource는 `storage_name` filter에 걸리지 않을 수 있다.

### Audit Semantics

Audit은 다음을 수행한다.

- operational DB에서 target resource를 조회한다.
- target cluster에서 live `ResourceQuota/dms-storage-quota`를 read-only 조회한다.
- `include_non_dms=true`이면 같은 namespace의 전체 `ResourceQuota` list를 read-only 조회한다.
- DB desired hard와 live `spec.hard`를 비교한다.
- live `status.used`와 hard limit을 비교해 usage pressure를 계산한다.
- non-DMS `ResourceQuota`가 DMS quota보다 더 restrictive하면 effective quota warning을 생성한다.
- 결과를 operational DB result/observed state에 저장한다.
- `record_action_required=true`이면 action-required query가 이 audit result를 반영할 수 있게 저장한다.

Audit은 다음을 하지 않는다.

- Kubernetes object 생성, patch, delete
- DB desired state 변경
- live state sync
- drift repair
- namespace cleanup
- 자동 재시도 scheduling

Drift repair는 기존 update API로 DB desired state를 다시 apply하거나, 기존 sync API로 live state를 DB로 받아들이는 별도 명시 요청으로 처리한다.

### Audit Result Shape

권장 result summary:

```json
{
  "audit_status": "ActionRequired",
  "target_count": 2,
  "issue_count": 3,
  "targets": [
    {
      "cluster_name": "cluster-b",
      "namespace_name": "team-a",
      "resource_key": "cluster-b:team-a",
      "resource_status": "Drifted",
      "issues": [
        {
          "issue_type": "kubernetes_quota_drifted",
          "severity": "WARN",
          "field": "spec.hard",
          "key": "testbed-longhorn.storageclass.storage.k8s.io/requests.storage",
          "desired": "512Mi",
          "live": "768Mi"
        }
      ],
      "usage_pressure": [
        {
          "issue_type": "quota_usage_warning",
          "severity": "WARN",
          "key": "requests.storage",
          "used": "820Mi",
          "hard": "1Gi",
          "used_percent": 80.08
        }
      ],
      "effective_quota_warnings": [
        {
          "type": "non_dms_quota_more_restrictive",
          "resource_quota_name": "team-admin-quota",
          "key": "requests.storage",
          "dms_hard": "1Gi",
          "non_dms_hard": "512Mi"
        }
      ]
    }
  ]
}
```

Status 기준:

- `Consistent`: DB desired hard와 live DMS-managed ResourceQuota hard가 일치하고 pressure/warning이 없다.
- `ActionRequired`: drift, missing, pressure, effective warning, metadata issue 중 하나 이상이 있다.
- `PartialFailure`: 일부 target은 audit됐지만 일부 target 조회가 실패했다.
- `Failed`: target resolution 또는 live read가 전부 실패했다.

## 기능 3: Drift / Usage Pressure Action-Required Aggregation

### Action-Required API

기존 endpoint를 확장한다.

```text
GET /api/v1/operations/action-required
```

Phase 9에서 action-required는 최신 on-demand audit/check 결과를 바탕으로 Kubernetes quota issue를 포함해야 한다.

반환할 issue type:

- `kubernetes_quota_drifted`
- `kubernetes_quota_missing`
- `kubernetes_quota_live_only`
- `kubernetes_quota_db_only`
- `kubernetes_quota_metadata_drift`
- `kubernetes_quota_query_failed`
- `non_dms_quota_more_restrictive`
- `non_dms_quota_zero_limit`
- `quota_usage_warning`
- `quota_usage_critical`

권장 issue shape:

```json
{
  "issue_type": "quota_usage_critical",
  "severity": "CRITICAL",
  "resource_kind": "kubernetes_namespace_quota",
  "resource_key": "cluster-b:team-a",
  "cluster_name": "cluster-b",
  "namespace_name": "team-a",
  "key": "requests.storage",
  "used": "970Mi",
  "hard": "1Gi",
  "used_percent": 94.73,
  "source_request_id": "req_...",
  "first_seen": "2026-05-29T12:00:00+09:00",
  "last_seen": "2026-05-29T12:00:00+09:00",
  "recommended_action": "increase quota, free storage, block new writes, or contact namespace owner"
}
```

### Resolution Semantics

Phase 9는 action-required issue를 자동으로 해결하지 않는다. 다만 최신 audit/check 결과가 정상으로 바뀌면 query 결과에서 사라져야 한다.

권장 구현:

- 별도 issue table을 추가하지 않아도 된다면, resource별 최신 `kubernetes.namespace_quota.audit` 또는 `kubernetes.namespace_quota.consistency_check` result를 읽어 action-required를 계산한다.
- schema가 필요하면 `resource_issues` 같은 작은 table을 추가할 수 있다.
- 어떤 방식을 쓰든 이후 audit에서 같은 issue가 사라지면 action-required에 남아 있으면 안 된다.
- broad audit이 여러 resource를 검사하는 경우 resource별 issue가 독립적으로 계산되어야 한다.

### Usage Pressure Rules

기본 threshold:

```json
{
  "warning_percent": 80,
  "critical_percent": 95
}
```

규칙:

- `status.used / hard`를 key별로 계산한다.
- storage byte quota와 PVC count quota를 모두 지원한다.
- namespace-wide key와 StorageClass-specific key를 모두 지원한다.
- hard가 `0`이고 resource가 DMS block 상태이면 usage pressure 대신 blocked 상태로 해석한다.
- hard가 `0`인데 block 상태가 아니면 `quota_usage_critical` 또는 metadata/drift issue로 표시한다.
- live `status.used`가 없으면 pressure issue를 만들지 않고 `usage_unknown` diagnostic을 남긴다.
- `include_non_dms=true`이면 같은 key에 대해 non-DMS quota까지 포함한 effective hard를 계산할 수 있다. 이 경우 DMS hard 기준 pressure와 effective hard 기준 pressure를 구분해 기록한다.
- threshold는 request payload로 override할 수 있으나 `critical_percent`는 `warning_percent`보다 커야 한다.

## 기능 4: DMS ResourceQuota Ownership Metadata Hardening

### Required Metadata

DMS-managed `ResourceQuota`에는 다음 metadata를 붙인다.

```yaml
metadata:
  name: dms-storage-quota
  labels:
    app.kubernetes.io/managed-by: dms
    dms.io/resource-kind: kubernetes-namespace-quota
  annotations:
    dms.io/resource-key: <cluster_name>:<namespace_name>
    dms.io/request-id: <request_id>
    dms.io/storage-names: <comma-separated-storage-names>
```

Phase 9에서 새로 apply되는 ResourceQuota는 위 metadata를 가져야 한다. 기존 live object에 일부 annotation이 없으면 check/audit에서 `kubernetes_quota_metadata_drift`로 표시한다.

### Mutation Safety

Kubernetes mutation을 수행하는 adapter는 다음을 확인해야 한다.

- object name이 `dms-storage-quota`다.
- `app.kubernetes.io/managed-by=dms` label이 있다.
- `dms.io/resource-kind=kubernetes-namespace-quota` label 또는 annotation이 있다.
- `dms.io/resource-key`가 현재 request의 `<cluster_name>:<namespace_name>`과 일치한다.

기존 Phase 4~8에서 생성한 object가 annotation을 갖지 않는 경우가 있을 수 있다. Phase 9는 다음 방식 중 하나를 선택한다.

- check/audit에서 metadata drift로 먼저 노출하고, update/reset repair apply에서 DMS metadata를 보강한다.
- 또는 mutation 전 metadata가 부족하면 fail-closed하고 운영자가 explicit repair API를 호출하게 한다.

테스트베드에서는 첫 번째 방식을 권장한다. 이미 DMS-managed label이 있고 resource key가 DB에서 명확하면 update/reset apply 때 metadata를 보강해도 된다. 단, non-DMS ResourceQuota에는 절대 DMS metadata를 붙이지 않는다.

## API 요약

Phase 9에서 사용하는 주요 API:

```text
POST  /api/v1/resource-management/default-quota-policies
PATCH /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
POST  /api/v1/resource-management/kubernetes/namespace-quotas:audit
POST  /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:check
POST  /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:sync
GET   /api/v1/operations/action-required
GET   /api/v1/operations/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
```

`namespace-quotas:audit`은 여러 target 또는 filter scope를 다루기 위한 API다. 단일 resource의 단순 consistency check는 기존 `:check` endpoint를 계속 사용할 수 있다.

## 구현 상세

### Step 1: Domain and Repository

- 새 operation kind를 추가한다.
  - `kubernetes.namespace_quota.audit`
- audit request는 read-only RM operation으로 처리한다.
- audit result에는 target별 issue, pressure, effective warning, diagnostics를 저장한다.
- action-required query가 latest audit/check result를 읽을 수 있는 repository helper를 추가한다.
- 필요하면 resource별 latest audit/check 조회 index를 추가한다.

권장 helper:

```python
def list_latest_kubernetes_quota_findings(
    *,
    cluster_name: str | None = None,
    requester_id: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    ...
```

### Step 2: Default Policy Reset Planner

- `reset_quota_to_default=true`를 Kubernetes namespace quota update validation에 추가한다.
- default policy를 `resource_kind + resource_type`으로 조회한다.
- policy quota를 desired state로 merge하지 말고 reset target으로 사용한다.
- policy의 `storage_class_quotas[]`는 full replacement로 처리한다.
- policy에 없는 StorageClass-specific quota는 제거한다.
- 기존 decrease guard를 reset 결과에도 적용한다.
- blocked 상태에서는 restore target만 바꾸고 live zero hard는 유지한다.

### Step 3: Audit Planner and Worker

- audit 대상 resource를 scope로 resolve한다.
- `max_targets`를 둬 실수로 너무 넓은 live query가 실행되지 않게 한다.
- 기본 `max_targets`는 100 이하로 둔다.
- audit plan은 target list와 include option을 desired state에 저장한다.
- Worker는 target마다 existing live adapter의 read methods를 재사용한다.
- Worker는 Kubernetes object를 변경하지 않는다.
- Worker는 target별 DB/live/effective/pressure 결과를 result summary에 저장한다.

### Step 4: Action-Required Query

- `OperationalQueryService.action_required()`에 Kubernetes quota finding aggregation을 추가한다.
- latest audit/check 결과에서 issue가 있는 resource만 반환한다.
- same resource/key/issue type은 중복 반환하지 않는다.
- resolved issue는 최신 audit/check 결과 기준으로 제거한다.
- issue에는 recommended action을 넣는다.

권장 recommended action:

- drift: `run update to re-apply DB desired state or run sync to accept live state`
- missing: `recreate DMS-managed ResourceQuota or delete DMS resource record after review`
- usage pressure: `increase quota, free storage, or contact namespace owner`
- non-DMS restrictive quota: `review non-DMS ResourceQuota owner`
- metadata drift: `repair DMS metadata with an update/reset apply or investigate manual changes`

### Step 5: Ownership Metadata

- ResourceQuota manifest renderer에 required label/annotation을 보강한다.
- live read summary에 labels/annotations를 포함한다.
- check/audit diff에 metadata drift issue를 포함한다.
- mutation safety check를 `managed-by` label 단독 확인에서 resource kind/resource key 확인으로 확장한다.

## Testbed Live Verification

새 verification script를 추가한다.

```text
scripts/phase9_kubernetes_quota_operational_hardening.py
scripts/verify-phase9-testbed.sh
```

검증 전 테스트베드 metadata를 확인한다.

```bash
cat /home/mason/workspace/testbed/testbed-info.json
cat /home/mason/workspace/testbed/testbed-summary.json
```

권장 검증 흐름:

1. fresh operational/observability PostgreSQL DB를 만든다.
2. DMS API와 Worker를 실행한다.
3. Phase 8 Agent DaemonSet report가 fresh 상태인지 확인한다.
4. `cephfs-a`, `longhorn-b`, `longhorn-static-b` storage mapping을 등록하고 sanity `Ready`를 확인한다.
5. Kubernetes namespace quota default policy를 등록한다.
6. `cluster-b`에 custom multi-StorageClass quota를 만든다.
7. `reset_quota_to_default=true` update를 실행한다.
8. live `ResourceQuota.spec.hard`가 default policy hard로 바뀌었는지 확인한다.
9. blocked 상태에서 reset을 실행해 live hard가 계속 `0`이고 unblock 후 default hard로 복구되는지 확인한다.
10. live ResourceQuota를 수동 patch해 drift를 만든다.
11. `namespace-quotas:audit` API를 호출해 drift issue가 result와 action-required에 나타나는지 확인한다.
12. update 또는 sync로 drift를 해결하고 audit을 다시 호출한다.
13. action-required에서 drift issue가 사라지는지 확인한다.
14. 작은 PVC를 생성하고 낮은 threshold override로 usage pressure warning을 검증한다.
15. non-DMS ResourceQuota를 추가하고 effective quota warning이 audit와 action-required에 나타나는지 확인한다.
16. DMS-managed ResourceQuota metadata 일부를 의도적으로 제거하거나 변경해 metadata drift가 표시되는지 확인한다.
17. `cluster-a/testbed-cephfs` single StorageClass target에서도 reset/audit regression을 수행한다.
18. verification namespace, PVC, ResourceQuota를 cleanup한다.

테스트베드 리소스가 부족하므로 usage pressure 검증은 큰 PVC를 만들지 않는다. 작은 PVC를 만든 뒤 request threshold를 낮게 설정해 pressure issue 계산만 검증한다.

예시:

```json
{
  "usage_thresholds": {
    "warning_percent": 1,
    "critical_percent": 90
  }
}
```

## Required Command Evidence

verification 문서에는 최소한 다음 output을 남긴다.

```bash
curl -s -H 'x-dms-actor: api-client' \
  -X POST http://127.0.0.1:8000/api/v1/resource-management/default-quota-policies \
  -d '<policy payload>'

curl -s -H 'x-dms-actor: api-client' \
  -X PATCH http://127.0.0.1:8000/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/<namespace> \
  -d '<reset payload>'

ssh c2-control 'kubectl -n <namespace> get resourcequota dms-storage-quota -o yaml'

curl -s -H 'x-dms-actor: api-client' \
  -X POST http://127.0.0.1:8000/api/v1/resource-management/kubernetes/namespace-quotas:audit \
  -d '<audit payload>'

curl -s -H 'x-dms-actor: api-client' \
  http://127.0.0.1:8000/api/v1/operations/action-required
```

## Local Tests

Unit/API tests:

- default policy reset renders namespace-wide hard only.
- default policy reset renders multi-StorageClass hard.
- reset rejects when default policy is missing.
- reset rejects when policy storage mapping is missing, failed, disabled, or cross-cluster.
- reset applies decrease guard.
- blocked reset updates `block_state.restore_hard` and keeps live hard zero.
- audit resolves single target and returns `Consistent`.
- audit detects drifted hard key.
- audit detects missing ResourceQuota.
- audit calculates usage warning/critical for storage bytes and PVC count.
- audit includes non-DMS effective quota warning when requested.
- action-required includes latest audit issues.
- action-required removes issue after subsequent clean audit.
- metadata drift is detected.
- mutation safety refuses non-DMS ResourceQuota.

Live tests:

- `cluster-b/testbed-longhorn` + `longhorn-static` multi-StorageClass reset/audit.
- `cluster-a/testbed-cephfs` single StorageClass reset/audit regression.
- no synthetic Agent report usage.
- no automatic cron/sweep execution.

## Phase 9에서 하지 않을 것

다음은 Phase 9 범위가 아니다.

- cron, scheduler, controller 형태의 자동 quota sweep
- DMS lifecycle operation으로서의 Kubernetes namespace delete
- Kubernetes CPU/memory/pod/service/object quota management
- `LimitRange` management
- `NetworkPolicy`, `RoleBinding`, `ServiceAccount` tenant provisioning
- `VolumeSnapshot` quota management
- StorageClass 생성/수정/삭제
- Longhorn/Ceph native volume API 직접 호출
- filesystem directory create/update/block/delete live mutation
- filesystem quota live mutation
- Data Management `scan/sync/rm` live execution
- VolcanoJob create/watch/terminate
- production Helm/Kustomize packaging 완성
- trusted edge mTLS validation은 Phase 16에서 완료. production ingress-nginx-specific live validation은 별도 staging 항목으로 유지

## Phase 9 완료 후 다음 Phase 후보

Phase 9가 성공하면 Kubernetes storage quota Resource Management는 운영 보강까지 닫힌 것으로 본다. 다음 phase는 Kubernetes 일반 tenant management로 넓히기보다 아래 중 하나를 선택하는 것이 좋다.

### Phase 10A: Filesystem Resource Management Minimal Lifecycle

- CephFS test path 또는 GPFS template 중 하나 선택
- directory create/check/sync/delete minimal lifecycle
- quota mutation은 test path에 제한
- Kubernetes quota lifecycle과 같은 DB/live drift/action-required model 재사용

### Phase 10B: Data Management Read-only Scan Preflight

- 실제 DM Agent report 기반 candidate pool 사용
- LDAP identity mapping과 POSIX permission preflight 연결
- read-only `scan` request/job preflight를 실제 runtime evidence로 검증
- scan artifact persistence는 작게 시작

권장 순서는 Phase 10A다. Phase 8 결과에서 Longhorn 계열 DM readiness가 control cluster DM Agent 기준 `Missing`으로 남았고, filesystem live lifecycle도 아직 구현되지 않았기 때문이다. Filesystem RM을 먼저 닫으면 이후 Data Management preflight가 더 명확한 resource boundary 위에서 진행된다.
