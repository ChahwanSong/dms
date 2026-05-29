# DMS Phase 6 Implementation Prompt

이 문서는 `docs/dms-phase5.md` 완료 이후 여섯 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 6의 목표는 Phase 5에서 완성한 Kubernetes namespace storage quota lifecycle을 **단일 StorageClass quota 운영에서 multi-StorageClass quota 운영으로 확장**하고, 같은 namespace 안의 non-DMS `ResourceQuota`가 실제 effective quota에 미치는 영향을 DMS query/check 결과로 노출하는 것이다.

Phase 6는 DMS Agent DaemonSet, filesystem quota, Volcano/mpifileutils 같은 새 runtime 축을 열지 않는다. Phase 5까지 실제 Kubernetes ResourceQuota mutation path가 검증되었으므로, 그 위에서 DMS Resource Management의 중요한 운영 기능인 “한 namespace 안 여러 스토리지별 quota”를 먼저 닫는다.

## Phase 6 목표

Phase 6의 핵심 기능은 다음 하나다.

**Kubernetes namespace multi-StorageClass quota and effective quota visibility**

구현 완료 기준은 다음과 같다.

- 하나의 DMS Kubernetes namespace quota resource가 여러 `storage_class_quotas[]` entry를 가질 수 있다.
- 각 entry는 `storage_name` mapping에서 `storage_class_name`, `cluster_name`, CSI provisioner를 derive한다.
- DMS는 하나의 `ResourceQuota/dms-storage-quota` 안에 namespace-wide quota와 여러 StorageClass-specific quota key를 함께 렌더링한다.
- create/update/block/unblock/delete/check/sync lifecycle은 multi-StorageClass hard key 전체에 대해 동작한다.
- quota decrease guard는 namespace-wide key와 각 StorageClass-specific key의 live `status.used`를 모두 확인한다.
- check는 DB desired hard와 live DMS-managed `ResourceQuota.spec.hard`의 multi-key diff를 구조화해서 기록한다.
- sync from live state는 live `spec.hard`를 source of truth로 받아 namespace-wide quota와 각 `storage_class_quotas[]` entry의 desired quota 값을 역산해 operational PostgreSQL에 저장한다.
- DMS는 namespace 안의 non-DMS `ResourceQuota`를 수정하지 않지만, query/check 결과에 effective quota warning으로 노출한다.
- live verification은 테스트베드 PostgreSQL과 실제 Kubernetes API를 사용한다.
- 검증 결과는 `docs/dms-phase6-verification.md`와 `docs/dms-done.md`에 command/output 포함해 기록한다.

## 왜 Phase 6에서 multi-StorageClass quota를 하는가

Phase 5까지 DMS는 다음을 실제 테스트베드에서 검증했다.

- Kubernetes namespace quota create/apply
- update
- live `status.used` 기반 decrease guard
- block/unblock
- consistency check
- sync from live state
- DMS-managed ResourceQuota delete
- `cluster-a/testbed-cephfs`, `cluster-b/testbed-longhorn` 양쪽 live backend 검증

하지만 현재 Phase 5 구현은 한 namespace에서 `storage_class_quotas[]` entry를 하나만 운영 검증했다. 실제 데이터센터에서는 같은 Kubernetes namespace 안에서 서로 다른 성격의 StorageClass를 함께 사용할 수 있다. 예를 들어 같은 team namespace에서 capacity storage, fast storage, shared filesystem storage를 함께 쓰면서 각 storage별 limit을 분리해야 한다.

Kubernetes `ResourceQuota`는 하나의 object 안에 여러 StorageClass-specific hard key를 넣을 수 있다. 따라서 Phase 6는 새 backend runtime을 추가하지 않고도 DMS Resource Management의 운영 표현력을 크게 늘릴 수 있다.

Phase 5 문서의 다음 phase 후보에는 DMS Agent DaemonSet과 Data Management preflight/Volcano scan이 있었다. 둘 다 중요하지만 새 배포 단위와 failure mode가 크다. Phase 6에서는 먼저 이미 live mutation이 검증된 Kubernetes ResourceQuota 도메인 안에서 multi-storage quota를 완성한다. DMS Agent DaemonSet은 Phase 7 후보로 남긴다.

## 현재 전제

Phase 5 완료 후 전제:

- Kubernetes namespace quota resource identity는 계속 `cluster_name + namespace_name`이다.
- DMS가 소유하는 Kubernetes object는 `ResourceQuota/dms-storage-quota`다.
- `storage_class_quotas[].storage_name`은 resource identity가 아니라 해당 namespace quota resource 안의 quota dimension이다.
- `storage_class_quotas[].storage_class_name`은 기본적으로 사용자가 직접 쓰지 않고 storage mapping에서 derive한다.
- payload에 `storage_class_name`이 포함되면 mapping에서 derive한 값과 일치해야 한다.
- update/block/delete/sync/check는 기존 resource가 operational PostgreSQL에 있어야 한다.
- non-DMS ResourceQuota는 DMS가 수정하거나 삭제하지 않는다.

테스트베드 topology:

- `cluster-a`
  - `testbed-cephfs`
  - provisioner `rook-ceph.cephfs.csi.ceph.com`
  - 현재 CSI StorageClass는 하나이므로 Phase 6에서는 single-entry regression target으로 사용한다.
- `cluster-b`
  - `testbed-longhorn`
  - `longhorn-static`
  - provisioner `driver.longhorn.io`
  - Phase 6 multi-StorageClass live verification target으로 사용한다.

## API와 Payload

기존 endpoint를 유지한다.

```text
POST   /api/v1/resource-management/kubernetes/namespace-quotas
PATCH  /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
POST   /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:block
DELETE /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
POST   /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:sync
POST   /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:check
```

### Create Example

```json
{
  "requester_id": "portal:phase6",
  "payload": {
    "cluster_name": "cluster-b",
    "namespace_name": "dms-phase6-longhorn-multi",
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

Expected `ResourceQuota.spec.hard`:

```yaml
hard:
  requests.storage: 1Gi
  persistentvolumeclaims: "20"
  testbed-longhorn.storageclass.storage.k8s.io/requests.storage: 512Mi
  testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims: "10"
  longhorn-static.storageclass.storage.k8s.io/requests.storage: 256Mi
  longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims: "4"
```

### Update Semantics

Phase 6에서는 `storage_class_quotas[]` update를 **full replacement**로 정의한다.

- update payload에 `storage_class_quotas[]`가 있으면 기존 list 전체를 새 list로 교체한다.
- update payload에 `storage_class_quotas[]`가 없으면 기존 list를 유지한다.
- 특정 entry 하나만 patch하는 partial update API는 Phase 6 범위가 아니다.

예시:

```json
{
  "requester_id": "portal:phase6",
  "payload": {
    "quota": {
      "requests_storage_bytes": 2147483648,
      "pvc_count": 30
    },
    "storage_class_quotas": [
      {
        "storage_name": "longhorn-b",
        "requests_storage_bytes": 1073741824,
        "pvc_count": 16
      },
      {
        "storage_name": "longhorn-static-b",
        "requests_storage_bytes": 536870912,
        "pvc_count": 8
      }
    ],
    "memo": "phase6 multi storage quota increase"
  }
}
```

### Block / Unblock

Block은 hard key 전체를 `0`으로 만든다.

```json
{
  "requester_id": "portal:phase6",
  "payload": {
    "block": true,
    "block_mode": "quota-zero",
    "reason": "maintenance window"
  }
}
```

Unblock은 `block_state.restore_hard`에 저장된 multi-key hard limit 전체를 복구한다.

```json
{
  "requester_id": "portal:phase6",
  "payload": {
    "block": false,
    "reason": "maintenance complete"
  }
}
```

### Check / Sync

Check는 DB와 live state를 read-only 비교한다.

```json
{
  "requester_id": "portal:phase6",
  "payload": {
    "scope": "single",
    "include_live_resourcequota": true,
    "include_effective_quota": true
  }
}
```

Sync는 live DMS-managed `ResourceQuota`를 operational PostgreSQL로 수용한다. non-DMS ResourceQuota는 desired state로 가져오지 않고 warning evidence로만 기록한다.

```json
{
  "requester_id": "portal:phase6",
  "payload": {
    "accept_live_state": true,
    "include_effective_quota": true,
    "reason": "accept live multi-storage quota state"
  }
}
```

## 구현 상세

### 1. Storage Mapping 준비

테스트베드 검증 전에 다음 mapping을 준비한다.

```text
cephfs-a -> cluster-a/testbed-cephfs
longhorn-b -> cluster-b/testbed-longhorn
longhorn-static-b -> cluster-b/longhorn-static
```

`longhorn-static-b` mapping은 Phase 6 verification script에서 upsert하고 sanity `Ready`를 확인한다.

### 2. Planner Validation

Planner는 다음을 검증해야 한다.

- create는 `storage_class_quotas[]` 0개 이상을 허용한다.
- Phase 6 live verification은 2개 entry 이상을 반드시 포함한다.
- `storage_class_quotas[]` entry는 object여야 한다.
- 각 entry는 `storage_name`을 가져야 한다.
- `storage_name`은 request target `cluster_name`과 같은 cluster의 mapping이어야 한다.
- mapping sanity는 `Ready`여야 한다.
- RM readiness는 `Ready`여야 한다.
- 같은 payload 안에 중복 `storage_name`이 있으면 reject한다.
- 같은 payload 안에 중복 derived `storage_class_name`이 있으면 reject한다.
- payload에 `storage_class_name`이 포함되면 mapping에서 derive한 값과 일치해야 한다.
- entry별 `requests_storage_bytes`는 양수여야 한다.
- entry별 `pvc_count`가 있으면 양수여야 한다.
- namespace-wide `quota.requests_storage_bytes`, `quota.pvc_count`는 create에서 명시되어야 한다.
- update에서 namespace-wide quota 값이 생략되면 기존 desired state 값을 유지한다.

Reject 시에는 plan을 만들지 않고 result에 `backend_side_effect=false`를 남긴다.

### 3. ResourceQuota Hard Rendering

렌더러는 다음 key를 지원한다.

Namespace-wide:

```text
requests.storage
persistentvolumeclaims
```

StorageClass-specific:

```text
<storage_class_name>.storageclass.storage.k8s.io/requests.storage
<storage_class_name>.storageclass.storage.k8s.io/persistentvolumeclaims
```

Phase 6 multi-entry payload에서는 ambiguity를 피하기 위해 entry별 `requests_storage_bytes`를 명시하게 한다. 기존 Phase 5 single-entry payload 호환을 위해 entry가 하나뿐이면 namespace-wide storage bytes fallback을 유지할 수 있다.

StorageClass-specific `pvc_count`는 optional이다. 생략하면 해당 StorageClass-specific PVC count key를 렌더링하지 않는다.

### 4. Decrease Guard

Decrease guard는 update operation에 적용한다.

비교 대상:

- `requests.storage`
- `persistentvolumeclaims`
- 모든 `<storage_class>.storageclass.storage.k8s.io/requests.storage`
- 모든 `<storage_class>.storageclass.storage.k8s.io/persistentvolumeclaims`

live used source:

- operational DB에 저장된 최신 `observed_state.resource_quota.status_used`
- 필요 시 update 전에 `:sync`를 실행해 used state를 refresh하도록 verification script를 구성한다.

하나의 key라도 desired hard가 live used보다 낮으면 request는 `Rejected`가 되어야 하며 Kubernetes side effect가 없어야 한다.

### 5. Check Diff Shape

`check_resource_quota` result에는 multi-key diff를 구조화한다.

권장 observed state:

```json
{
  "consistency_status": "Drifted",
  "resource_status": "Drifted",
  "issues": [
    {
      "field": "spec.hard",
      "key": "testbed-longhorn.storageclass.storage.k8s.io/requests.storage",
      "reason": "hard_limit_drifted",
      "desired": "512Mi",
      "live": "768Mi"
    }
  ],
  "effective_quota_warnings": []
}
```

Status 기준:

- `Consistent`: DMS-managed ResourceQuota exists, DMS metadata valid, hard key/value match
- `Drifted`: DMS-managed ResourceQuota exists but metadata or hard differs
- `Missing`: DMS-managed ResourceQuota is absent
- `CheckFailed`: Kubernetes API read or parsing failed

### 6. Sync From Live State

Sync는 live `ResourceQuota.spec.hard`를 DB state로 역산한다.

역산 규칙:

- `requests.storage` -> `desired_state.quota.requests_storage_bytes`
- `persistentvolumeclaims` -> `desired_state.quota.pvc_count`
- `<storageclass>.storageclass.storage.k8s.io/requests.storage`
  -> matching `storage_class_quotas[].requests_storage_bytes`
- `<storageclass>.storageclass.storage.k8s.io/persistentvolumeclaims`
  -> matching `storage_class_quotas[].pvc_count`

matching은 existing desired state의 `storage_class_quotas[].storage_class_name`을 기준으로 한다. live hard에 DB가 모르는 StorageClass-specific key가 있으면 desired state로 자동 편입하지 않고 `unknown_storageclass_quota_key` warning으로 기록한다.

### 7. Effective Quota Warning

Kubernetes admission은 namespace 안 여러 ResourceQuota의 교집합처럼 동작한다. DMS-managed quota가 충분히 커도 non-DMS ResourceQuota가 더 낮으면 실제 PVC 생성은 non-DMS quota에 막힐 수 있다.

Phase 6에서 DMS는 다음을 한다.

- namespace의 전체 ResourceQuota list를 read-only 조회한다.
- `dms-storage-quota` 외 object를 non-DMS quota로 분류한다.
- non-DMS hard key가 DMS desired hard보다 낮으면 warning을 만든다.
- non-DMS quota가 `0` hard limit을 가지면 warning을 만든다.
- unknown hard key는 warning으로 남긴다.
- warning은 check/sync result와 observability diagnostic event에 기록한다.

권장 warning shape:

```json
{
  "type": "non_dms_quota_more_restrictive",
  "resource_quota_name": "team-admin-quota",
  "key": "testbed-longhorn.storageclass.storage.k8s.io/requests.storage",
  "dms_hard": "512Mi",
  "non_dms_hard": "128Mi"
}
```

Phase 6는 non-DMS quota를 수정하거나 삭제하지 않는다.

### 8. Operational Query

가능하면 운영자가 namespace quota를 직접 조회할 수 있는 read API를 추가한다.

권장 endpoint:

```text
GET /api/v1/operations/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
```

응답에는 다음을 포함한다.

- operational DB resource state
- DMS-managed ResourceQuota observed state
- live DMS-managed ResourceQuota state if requested or cached
- live non-DMS ResourceQuota summary
- effective quota warnings
- last check/sync request id

단, 이 endpoint가 Phase 6 scope를 과하게 키우면 `:check` result와 `action-required` 노출까지만 Phase 6 완료 기준으로 삼고 dedicated query endpoint는 Phase 7로 넘긴다. Phase 6 완료 후에는 이 API를 별도 phase로 분리해, DMS DB에 기록된 quota state와 현재 Kubernetes cluster에 적용된 live DMS `ResourceQuota`를 한 응답에서 비교할 수 있게 한다.

## Live Verification Plan

### Target A: Longhorn multi-StorageClass

Target:

```text
cluster-b
namespace dms-phase6-longhorn-multi-<token>
storage_name longhorn-b -> testbed-longhorn
storage_name longhorn-static-b -> longhorn-static
```

Flow:

1. fresh PostgreSQL operational/observability DB를 생성한다.
2. migrations를 적용한다.
3. `cluster-b` Kubernetes read-only inventory를 조회한다.
4. `longhorn-b` mapping을 sanity `Ready`로 만든다.
5. `longhorn-static-b` mapping을 sanity `Ready`로 만든다.
6. synthetic RM Agent report를 제출한다. Phase 6는 Agent DaemonSet 구현 phase가 아니므로 이 한계는 문서에 명시한다.
7. multi-StorageClass quota create를 요청한다.
8. live `ResourceQuota.spec.hard`에 두 StorageClass-specific storage key와 PVC count key가 있는지 확인한다.
9. `testbed-longhorn` PVC를 만들고 `status.used` 증가를 확인한다.
10. `longhorn-static` PVC를 만들고 `status.used` 증가를 확인한다.
11. 한 StorageClass quota만 초과하는 PVC를 만들어 admission reject를 확인한다.
12. update로 quota를 증가시킨다.
13. update 후 두 StorageClass PVC admission이 의도한 대로 동작하는지 확인한다.
14. update로 한 StorageClass-specific hard를 live used보다 낮추는 request를 제출하고 `Rejected`와 no side effect를 확인한다.
15. block을 요청해 모든 hard key가 `0`이 되는지 확인한다.
16. block 상태에서 두 StorageClass 모두 신규 PVC admission reject를 확인한다.
17. unblock을 요청해 모든 hard key가 복구되는지 확인한다.
18. manual drift로 한 key만 변경한다.
19. check가 해당 key의 drift를 구조화해서 기록하는지 확인한다.
20. non-DMS ResourceQuota를 추가해 DMS quota보다 더 낮은 hard key를 만든다.
21. check 또는 sync가 effective quota warning을 기록하는지 확인한다.
22. sync from live state를 요청해 live hard가 DB desired state에 역산 저장되는지 확인한다.
23. delete를 요청해 `dms-storage-quota`만 삭제되고 non-DMS ResourceQuota는 보존되는지 확인한다.
24. test cleanup으로 namespace를 삭제한다.

### Target B: CephFS single-entry regression

Target:

```text
cluster-a
namespace dms-phase6-cephfs-regression-<token>
storage_name cephfs-a -> testbed-cephfs
```

Flow:

1. Phase 5 lifecycle smoke를 축소해 실행한다.
2. create/update/block/unblock/check/sync/delete가 single-entry target에서도 계속 동작하는지 확인한다.
3. CephFS에 두 번째 StorageClass가 없다는 점을 verification 문서에 기록한다.

CephFS multi-StorageClass live verification은 테스트베드에 두 번째 CephFS StorageClass가 추가된 이후 별도 phase 또는 확장 검증으로 수행한다.

## Unit Test Plan

추가할 테스트:

- renderer creates multiple StorageClass-specific keys
- renderer supports StorageClass-specific PVC count keys
- planner accepts two valid entries on same cluster
- planner rejects duplicate `storage_name`
- planner rejects duplicate derived `storage_class_name`
- planner rejects cross-cluster mapping mismatch
- planner rejects `storage_class_name` payload mismatch
- planner decrease guard rejects one StorageClass-specific key below used
- block zeros all namespace-wide and StorageClass-specific hard keys
- unblock restores all hard keys
- check reports per-key drift
- sync updates `quota` and each `storage_class_quotas[]` entry from live hard
- delete still preserves non-DMS ResourceQuota
- effective quota warning detects more restrictive non-DMS quota

## Implementation Entry Points

예상 수정 파일:

- `src/dms/adapters.py`
  - ResourceQuota hard renderer 확장
  - quantity parsing/rendering reuse
  - live adapter ResourceQuota list/read helper
  - effective quota warning 계산
  - sync reverse mapping 확장
- `src/dms/planner.py`
  - multi-entry validation
  - full replacement update semantics
  - duplicate/cross-cluster/mapping mismatch guard
  - per-key decrease guard
- `src/dms/workers.py`
  - check/sync result status and observability payload enrichment if needed
- `src/dms/repositories.py`
  - query helper if dedicated operational query endpoint를 추가하는 경우
- `src/dms/api.py`
  - optional namespace quota query endpoint
- `tests/test_phase6_kubernetes_multi_storage_quota.py`
  - planner/renderer/worker unit tests
- `scripts/phase6_kubernetes_multi_storage_quota.py`
  - live verification body
- `scripts/verify-phase6-testbed.sh`
  - fresh PostgreSQL DB creation and testbed orchestration
- `docs/dms-phase6-verification.md`
  - executed command, output, DB evidence, Kubernetes evidence
- `docs/dms-done.md`
  - Phase 6 done/not-done 상태 update

## Verification Commands

대표 명령:

```bash
cd /home/mason/workspace/dms
python3 -m py_compile src/dms/adapters.py src/dms/planner.py src/dms/workers.py src/dms/domain.py src/dms/repositories.py src/dms/api.py
/tmp/dms-phase3-venv/bin/python -m pytest -q
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase6-testbed.sh
```

`verify-phase6-testbed.sh`는 매 실행마다 fresh DB를 만든다.

```text
dms_phase6_<timestamp>
dms_phase6_obs_<timestamp>
```

## Phase 6에서 하지 않을 것

다음은 Phase 6 범위가 아니다.

- DMS Agent DaemonSet 구현
- Agent token 또는 mTLS live 인증 구현
- filesystem directory create/update/block/delete
- filesystem quota command 실행
- Kubernetes namespace delete lifecycle
- partial per-storage quota patch API
- block 상태 Kubernetes quota update의 restore target 갱신 workflow
- cross-cluster batch quota operation
- default quota policy reset workflow
- VolcanoJob live execution
- mpifileutils image build 또는 execution
- Data Management POSIX preflight
- maintenance/drain mode full workflow
- DMS API/Worker Kubernetes Deployment/Helm/Kustomize 완성

## Phase 6 완료 후 다음 Phase 후보

Phase 6 완료 후에는 다음 중 하나를 선택한다.

### Phase 7A: Kubernetes Quota Query API

Phase 6에서 check/sync result evidence로만 확인한 quota 상태를 운영자가 직접 조회할 수 있는 read-only API로 승격한다.

목표는 **DMS DB에 기록된 resource quota 정보**와 **현재 Kubernetes cluster에 실제 적용된 DMS-managed `ResourceQuota` 정보**를 같은 API 응답에서 동시에 보여주는 것이다. 이 API는 quota를 수정하지 않고, 운영자가 drift, stale DB state, live object missing, non-DMS quota interference를 빠르게 판단하는 용도다.

권장 endpoint:

```text
GET /api/v1/operations/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
```

권장 query parameter:

- `source=both|db|live`: 기본값은 `both`다. `both`는 DMS DB와 Kubernetes live object를 모두 조회한다.
- `include_non_dms=true|false`: namespace 안의 non-DMS `ResourceQuota` summary와 effective quota warning 포함 여부다.
- `include_status_used=true|false`: live `ResourceQuota.status.used` 포함 여부다.

응답에는 최소한 다음을 포함한다.

- `cluster_name`, `namespace_name`, DMS-managed quota name
- DMS DB resource row의 `resource_type`, `status`, `version`, `desired_state.quota`, rendered desired hard, `observed_state`, `created_at`, `updated_at`
- DB에 기록된 마지막 create/update/check/sync/delete request id와 terminal status
- live DMS-managed Kubernetes `ResourceQuota`의 `metadata`, `spec.hard`, `status.hard`, `status.used`, `resourceVersion`, `creationTimestamp`
- DB desired hard와 live `spec.hard`의 structured diff
- live `status.used`가 hard limit에 얼마나 근접했는지에 대한 usage summary
- `include_non_dms=true`일 때 non-DMS `ResourceQuota` 목록, hard summary, effective quota warning
- live object가 없거나 Kubernetes API 조회가 실패했을 때의 `live_status`와 diagnostic reason

응답 예:

```json
{
  "cluster_name": "cluster-b",
  "namespace_name": "team-a",
  "dms_resource_quota_name": "dms-storage-quota",
  "source": "both",
  "db": {
    "resource_type": "kubernetes_namespace_quota",
    "status": "Active",
    "version": 7,
    "desired_hard": {
      "requests.storage": "768Mi",
      "persistentvolumeclaims": "8",
      "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "512Mi",
      "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "4",
      "longhorn-static.storageclass.storage.k8s.io/requests.storage": "256Mi",
      "longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims": "4"
    },
    "last_check_request_id": "req_...",
    "last_sync_request_id": "req_..."
  },
  "live": {
    "exists": true,
    "spec_hard": {
      "requests.storage": "768Mi",
      "persistentvolumeclaims": "8",
      "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "512Mi",
      "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "4",
      "longhorn-static.storageclass.storage.k8s.io/requests.storage": "256Mi",
      "longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims": "4"
    },
    "status_used": {
      "requests.storage": "384Mi",
      "persistentvolumeclaims": "3",
      "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "256Mi",
      "longhorn-static.storageclass.storage.k8s.io/requests.storage": "128Mi"
    },
    "resource_version": "123456"
  },
  "diff": {
    "status": "Consistent",
    "issues": []
  },
  "effective_quota_warnings": []
}
```

구현 기준:

- API는 read-only여야 하며 Kubernetes object나 DMS DB desired state를 변경하지 않는다.
- live 조회는 `get resourcequotas`와 필요 시 `list resourcequotas` RBAC만 요구한다.
- DB 조회가 성공하고 live 조회가 실패해도 DB section은 반환하고 live diagnostic을 남긴다.
- live 조회가 성공하고 DB resource row가 없으면 unmanaged live quota 후보로 반환하되 DMS-owned label/annotation 여부를 명확히 표시한다.
- check/sync와 동일한 hard key parser를 재사용해 quantity 비교 방식이 API마다 달라지지 않게 한다.
- 응답은 운영자가 `kubectl get resourcequota dms-storage-quota -n <namespace> -o yaml` 결과와 대조할 수 있는 값을 포함한다.

테스트베드 검증:

- `cluster-a/testbed-cephfs` single StorageClass quota에서 DB desired hard와 live `spec.hard`가 일치하는지 API로 검증한다.
- `cluster-b/testbed-longhorn` + `cluster-b/longhorn-static` multi-StorageClass quota에서 namespace-wide key와 StorageClass-specific key가 모두 반환되는지 검증한다.
- live `ResourceQuota`를 수동 patch해 drift를 만들고 API `diff.status=Drifted`와 issue key가 check 결과와 같은지 확인한다.
- DMS DB state는 남아 있는데 live object를 삭제한 missing case를 검증한다.
- non-DMS `ResourceQuota`를 추가하고 `include_non_dms=true`에서 effective quota warning이 반환되는지 확인한다.

### Phase 7A-2: Block 상태 Kubernetes Quota Update Semantics

Phase 6 구현에서는 block 상태의 Kubernetes namespace quota resource에 update 요청이 들어와도 별도로 거부하지 않는다. 이때 update가 `resource_quota_hard`를 다시 렌더링해 live `ResourceQuota`에 non-zero hard limit을 적용할 수 있고, `block_state.restore_hard`는 block 이전 값으로 남을 수 있다. 결과적으로 DMS state는 blocked인데 Kubernetes admission은 다시 열리거나, 이후 unblock 시 block 중 update한 quota가 아니라 오래된 restore hard로 복구될 위험이 있다.

다음 phase에서는 block 상태 update를 금지하지 말고, **block 상태 update는 허용하되 live hard는 계속 `0`으로 유지**하는 정책을 구현한다.

요구 동작:

- 기존 DMS resource가 `block_state.blocked=true`인 상태에서 `update` 요청이 들어오면 planner는 update payload를 기존 desired state와 병합해 최신 restore target hard를 계산한다.
- 계산된 최신 hard limit은 `block_state.restore_hard`에 저장한다.
- live Kubernetes `ResourceQuota.spec.hard`에는 계속 모든 DMS-managed hard key를 `"0"`으로 적용한다.
- DMS desired state에는 `block=true`, `block_state.blocked=true`를 유지해 운영자가 여전히 blocked 상태임을 볼 수 있게 한다.
- 이후 `unblock` 요청은 block 이전 hard가 아니라 block 중 update로 갱신된 최신 `block_state.restore_hard`를 복구한다.
- update가 quota decrease를 포함하면 기존 decrease guard를 최신 restore target hard에 대해 적용한다. live used보다 낮은 restore target은 blocked 상태에서도 reject한다.
- `storage_class_quotas[]` full replacement semantics는 유지한다. block 중 update payload에 `storage_class_quotas[]`가 있으면 새 list 전체가 restore target이 된다.

검증 항목:

- block 후 live `ResourceQuota.spec.hard`의 namespace-wide key와 StorageClass-specific key가 모두 `"0"`인지 확인한다.
- block 상태에서 quota increase update를 요청해 request가 성공하되 live `spec.hard`는 계속 `"0"`인지 확인한다.
- 같은 상태에서 `GET /api/v1/operations/resources` 또는 dedicated query API가 `block_state.restore_hard`에 update된 quota를 보여주는지 확인한다.
- unblock 후 live `ResourceQuota.spec.hard`가 block 이전 값이 아니라 block 중 update한 최신 quota로 복구되는지 확인한다.
- block 상태에서 live used보다 낮은 decrease update가 rejected 되고 live `spec.hard`가 계속 `"0"`으로 유지되는지 확인한다.

### Phase 7A-3: Requester-scoped Request History Query

운영 request history 조회 API가 전체 request 목록을 넓게 반환하지 않도록, `GET /api/v1/operations/requests`에 필수 `requester_id` query parameter를 추가한다.

권장 endpoint:

```text
GET /api/v1/operations/requests?requester_id={requester_id}&limit={limit}
```

요구 동작:

- `requester_id`는 필수 query parameter다. 누락되면 request를 조회하지 않고 validation error를 반환한다.
- 응답은 `requests.requester_id`가 query의 `requester_id`와 일치하는 request만 포함한다.
- 정렬은 기존과 동일하게 `commit_order DESC` 최신순을 유지한다.
- `limit`은 optional query parameter다. 없으면 API/repository 기본값을 사용한다.
- `limit`이 주어지면 repository 조회에 전달해 해당 requester의 최신 request 중 지정한 개수만 반환한다.
- `GET /api/v1/operations/requests/{request_id}`는 단건 lifecycle history 조회용으로 유지하되, 필요하면 requester 권한 검증은 별도 정책으로 다룬다.

검증 항목:

- 서로 다른 `requester_id`의 request를 섞어 생성하고, API가 지정한 requester의 request만 반환하는지 확인한다.
- `limit` 없이 호출하면 repository/API 기본값이 적용되는지 확인한다.
- `limit`을 지정하면 해당 requester 범위 안에서 `commit_order DESC` 최신순으로 제한되는지 확인한다.
- `requester_id` 없이 호출하면 validation error가 반환되고 운영 DB 조회 부하를 만들지 않는지 확인한다.

### Phase 7B: DMS Agent DaemonSet

Phase 3부터 이어진 synthetic Agent report 한계를 제거한다.

- RM Agent DaemonSet on managed clusters
- DM Agent on control cluster worker nodes
- mount/tool/credential/network probe
- Agent identity evidence
- report freshness and stale handling
- storage mapping sanity가 실제 Agent report로 Ready/Failed 계산

이 phase는 Data Management live execution 전에 수행하는 것을 권장한다.

### Phase 7C: Filesystem Resource Management Minimal Lifecycle

Kubernetes ResourceQuota와 별개로 POSIX filesystem directory/quota lifecycle을 실제 backend에 붙인다.

- directory create/update/block/delete
- quota apply/check/sync
- CephFS mount 또는 GPFS template 중 하나로 시작
- destructive delete는 테스트 namespace/path로 제한

### Phase 7D: Data Management Read-only Scan Preflight

Agent DaemonSet 또는 충분히 신뢰 가능한 DM capability report가 준비된 뒤 진행한다.

- LDAP identity mapping 기반 POSIX preflight
- read-only `scan` job
- VolcanoJob 또는 local controlled executor 중 하나를 실제 검증 대상으로 선택
- scan report artifact persistence

권장 순서는 `Phase 7A -> Phase 7B -> Phase 7D`다. quota query API는 Phase 6에서 만든 Kubernetes quota lifecycle의 운영 가시성을 먼저 닫는 작은 read-only phase로 적합하다. Data Management는 실제 mount/tool/identity capability가 scheduler decision과 맞아야 하므로 Agent DaemonSet 없이 진행하면 검증 의미가 약해진다.
