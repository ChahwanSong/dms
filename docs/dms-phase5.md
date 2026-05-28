# DMS Phase 5 Implementation Prompt

이 문서는 `docs/dms-phase1.md`, `docs/dms-phase2.md`, `docs/dms-phase3.md`, `docs/dms-phase4.md` 완료 이후 다섯 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 5의 목표는 Phase 4에서 처음 연 Kubernetes namespace storage quota live mutation을 운영 lifecycle로 확장하되, 여전히 Kubernetes CSI ResourceQuota 범위로 제한해 테스트베드에서 실제 검증 가능한 단위로 닫는 것이다.

Phase 5는 Phase 4의 `cluster-b/testbed-longhorn` ResourceQuota create/apply 검증을 전제로 한다. 이번 단계에서 DMS는 DMS-managed `ResourceQuota/dms-storage-quota`에 대해 update, quota decrease guard, block/unblock, delete, DB/live consistency check, DB sync from live state를 실제 Kubernetes API 경로로 검증한다.

중요: Phase 5의 Kubernetes CSI Resource Management live verification은 테스트베드에 있는 두 CSI backend를 모두 대상으로 한다.

- `cluster-a/testbed-cephfs`: Rook CephFS CSI, provisioner `rook-ceph.cephfs.csi.ceph.com`
- `cluster-b/testbed-longhorn`: Longhorn CSI, provisioner `driver.longhorn.io`

## Phase 5 목표

Phase 5의 핵심 기능은 다음 하나다.

**Kubernetes CSI namespace storage quota lifecycle after create**

구현 완료 기준은 다음과 같다.

- DMS RM Worker가 실제 Kubernetes API 또는 `kubectl` read/write path를 통해 target cluster의 DMS-managed `ResourceQuota/dms-storage-quota`를 update한다.
- live verification은 `cluster-a/testbed-cephfs`와 `cluster-b/testbed-longhorn` 양쪽에서 수행한다.
- quota 감소 요청은 live `ResourceQuota.status.used`를 기준으로 guard한다.
  - 기본 정책: 현재 사용량보다 작은 hard limit으로 낮추는 update는 backend side effect 없이 거부한다.
  - block 동작은 별도 operation이므로 이 guard의 예외로 처리할 수 있다.
- `block=ON`은 복구 가능한 restore state를 operational PostgreSQL에 남긴 뒤 DMS-managed ResourceQuota hard limit을 0으로 만든다.
- `block=OFF`는 보존된 restore state 또는 DB desired state를 기준으로 hard limit을 복구한다.
- delete는 DMS-managed `ResourceQuota/dms-storage-quota`만 삭제한다. namespace는 DMS lifecycle delete 대상으로 보지 않는다.
- consistency check는 DB desired/applied/observed state와 live Kubernetes ResourceQuota state를 read-only로 비교해 `Consistent`, `Drifted`, `Missing`, `CheckFailed` 중 하나로 기록한다.
- sync from live state는 live `ResourceQuota`를 source of truth로 받아 DB applied/observed state를 갱신하되 Kubernetes object는 변경하지 않는다.
- 모든 live mutation과 read-only check 결과는 operational PostgreSQL의 resource/result state와 observability PostgreSQL diagnostic event로 추적한다.
- 검증 결과는 `docs/dms-phase5-verification.md`와 `docs/dms-done.md`에 command/output 포함해 기록한다.

## 왜 Phase 5에서 Kubernetes CSI quota lifecycle을 하는가

Phase 4까지 DMS는 다음을 실제 테스트베드에서 확인했다.

- 실제 PostgreSQL operational/observability DB 분리
- LDAP direct read-only Identity Mapping
- Kubernetes read-only inventory
- storage mapping sanity와 Planner fail-closed guard
- `cluster-b/testbed-longhorn` ResourceQuota create/apply
- Longhorn PVC admission success/failure

따라서 Phase 5는 새 runtime 종류를 열기보다 같은 Kubernetes ResourceQuota backend 안에서 운영 lifecycle을 완성하는 것이 적절하다. 또한 Phase 5에서는 Longhorn만 반복 검증하지 않고 `cluster-a/testbed-cephfs`도 같은 기준으로 검증한다. 이렇게 해야 DMS의 Kubernetes CSI Resource Management가 특정 CSI backend에 고정되지 않고, StorageClass/provisioner mapping을 통해 일반화되어 있음을 확인할 수 있다.

DMS Agent DaemonSet, filesystem quota, Volcano/mpifileutils는 모두 새 runtime failure 축이 크다. 반면 ResourceQuota update/block/delete/sync는 Phase 4 adapter, Planner guard, PostgreSQL state model을 그대로 사용하면서 실제 운영에 필요한 lifecycle을 늘릴 수 있다.

## 현재 전제

Phase 1은 다음 골격을 제공했다.

- Resource Management request/plan/run/result lifecycle
- RM Worker runtime
- Kubernetes namespace quota API skeleton
- operational/observability repository skeleton

Phase 2는 다음 기반을 완료했다.

- 실제 테스트베드 PostgreSQL live baseline
- operational DB와 observability DB 분리
- LDAP direct read-only Identity Mapping

Phase 3는 다음 기반을 완료했다.

- Kubernetes read-only inventory
- Agent report persistence/freshness
- storage mapping sanity
- `storage_name -> cluster/storage_class` mapping
- Planner fail-closed guard
- Operational Query에서 inventory, mapping, action-required 조회

Phase 4는 다음 기반을 완료했다.

- `cluster-b` namespace create or ensure
- DMS-managed `ResourceQuota/dms-storage-quota` create/apply
- namespace-wide quota와 StorageClass-specific quota rendering
- ResourceQuota `spec.hard`, `status.hard`, `status.used` read-back
- operational PostgreSQL resource/result persistence
- observability PostgreSQL apply started/completed event
- 실제 Longhorn PVC admission 검증

테스트베드 topology:

- `cluster-a`: DMS control cluster, PostgreSQL, OpenLDAP 접근 기준, self-managed RM target 가능
- `cluster-a/testbed-cephfs`: Rook CephFS StorageClass, CSI provisioner `rook-ceph.cephfs.csi.ceph.com`
- `cluster-b`: managed cluster, Phase 4 live ResourceQuota target
- `cluster-b/testbed-longhorn`: Longhorn StorageClass, CSI provisioner `driver.longhorn.io`
- PostgreSQL: `192.168.56.11:30432`
- Kubernetes access: 테스트베드에서는 `ssh-kubectl` mode로 `c1-control`, `c2-control`의 `kubectl`을 사용한다.

## 핵심 원칙

### 1. Phase 5 live mutation은 DMS-owned ResourceQuota lifecycle로 제한한다

Phase 5에서 실제로 변경해도 되는 Kubernetes object는 다음으로 제한한다.

- 테스트용 namespace
- DMS-managed `ResourceQuota/dms-storage-quota`
- PVC admission 검증용 PVC/Pod

Phase 5 구현은 filesystem directory, filesystem quota, VolcanoJob, Longhorn/Ceph volume 직접 API, StorageClass, CSI driver object를 변경하지 않는다.

### 2. CephFS와 Longhorn을 같은 contract로 검증한다

Phase 5 live verification은 두 target 모두에서 같은 DMS contract를 검증한다.

| Target | Cluster | StorageClass | Provisioner | Control host |
| --- | --- | --- | --- | --- |
| CephFS | `cluster-a` | `testbed-cephfs` | `rook-ceph.cephfs.csi.ceph.com` | `c1-control` |
| Longhorn | `cluster-b` | `testbed-longhorn` | `driver.longhorn.io` | `c2-control` |

각 target은 독립 namespace를 사용한다.

```text
dms-phase5-cephfs-<token>
dms-phase5-longhorn-<token>
```

각 target은 독립 `storage_name` mapping을 사용한다.

```text
cephfs-a -> cluster-a/testbed-cephfs
longhorn-b -> cluster-b/testbed-longhorn
```

두 target 중 하나라도 live 검증을 생략하면 Phase 5 완료로 기록하지 않는다. 단, 테스트베드 자체 장애로 한쪽 CSI backend가 unavailable이면 `docs/dms-phase5-verification.md`와 `docs/dms-done.md`에 미검증 사유를 명확히 남긴다.

### 3. namespace 삭제는 DMS lifecycle delete가 아니다

Phase 5 delete operation은 `ResourceQuota/dms-storage-quota`만 삭제한다. namespace 삭제는 live verification script cleanup으로만 수행할 수 있으며, DMS 기능 성공으로 기록하지 않는다.

검증 script가 namespace cleanup을 수행하는 경우 `docs/dms-phase5-verification.md`에 다음을 구분해 적는다.

- DMS lifecycle delete: `ResourceQuota/dms-storage-quota` 삭제
- test cleanup: PVC/Pod/namespace 삭제

### 4. DMS-managed ResourceQuota만 소유한다

DMS는 namespace 안에 있는 모든 ResourceQuota를 소유하지 않는다. Phase 5에서 update/block/delete/sync/check 대상으로 삼는 ResourceQuota는 이름이 `dms-storage-quota`이고 DMS identity label/annotation을 가진 object뿐이다.

권장 metadata:

```yaml
metadata:
  name: dms-storage-quota
  labels:
    app.kubernetes.io/managed-by: dms
    dms.io/resource-kind: kubernetes-namespace-quota
  annotations:
    dms.io/resource-key: <cluster_name>:<namespace_name>
    dms.io/request-id: <request_id>
    dms.io/storage-names: <storage_name>
```

Delete safety 검증에서는 같은 namespace에 non-DMS ResourceQuota를 추가할 수 있다. 이 object는 DMS delete에서 삭제되면 안 된다.

### 5. Resource identity는 계속 `cluster_name + namespace_name`이다

Kubernetes namespace storage quota resource의 DMS identity는 `cluster_name + namespace_name`이다.

`storage_name`은 resource identity가 아니다. `storage_name`은 StorageClass-specific quota entry를 렌더링하기 위한 mapping input이다.

### 6. StorageClass는 mapping에서 derive한다

Phase 5 update/block/unblock/sync/check에서도 StorageClass-specific quota entry는 operational PostgreSQL의 storage mapping에서 derive한다.

규칙:

- `storage_name` mapping이 없으면 Planner가 fail-closed 한다.
- mapping sanity가 `Ready`가 아니면 live mutation을 만들지 않는다.
- mapping의 `cluster_name`은 Kubernetes namespace quota target cluster와 같아야 한다.
- payload가 `storage_class_name`을 직접 포함한다면 mapping에서 derive한 값과 일치해야 한다. 불일치 시 `Rejected`.

### 7. DB desired state와 live state를 명확히 구분한다

Phase 5는 update와 sync가 모두 있으므로 source of truth를 명확히 구분해야 한다.

- update: DB desired state 또는 request desired state가 기준이며 Kubernetes ResourceQuota를 그 기준으로 반영한다.
- block: DB desired state를 restore 가능한 상태로 보존하고 live hard limit을 0으로 반영한다.
- unblock: 보존된 restore state 또는 DB desired state가 기준이며 Kubernetes ResourceQuota를 복구한다.
- consistency check: DB와 live를 비교만 하고 변경하지 않는다.
- sync from live: live ResourceQuota state를 받아 DB applied/observed state를 갱신하고 Kubernetes는 변경하지 않는다.

### 8. Backend side effect 전후 상태를 PostgreSQL에 남긴다

RM Worker는 backend side effect 전에 claim과 `Applying` 상태를 operational PostgreSQL에 commit해야 한다.

ResourceQuota lifecycle operation 후에는 다음을 operational PostgreSQL에 기록한다.

- desired state: 요청/plan에서 렌더링한 quota 또는 보존된 restore state
- applied state: 실제 patch/apply/delete/sync/check manifest 또는 action summary
- observed state: Kubernetes API에서 다시 읽은 namespace/resourcequota spec/status
- result verification summary: backend side effect 여부, ResourceQuota UID/resourceVersion, hard/used 값, PVC admission 검증 결과, consistency diff

## Phase 5에서 하지 않을 것

다음은 Phase 5 범위가 아니다.

- DMS lifecycle operation으로서의 namespace delete
- Kubernetes namespace quota create flow 재설계
- multi StorageClass quota entry의 전체 운영 검증
- default quota policy 기반 reset 전체 구현
- cross-cluster 일괄 update/block/delete/sync
- non-DMS ResourceQuota effective quota warning 전체 구현
- DMS Agent DaemonSet 구현
- filesystem quota 또는 directory mutation
- CephFS/Longhorn native backend volume API 직접 호출
- VolcanoJob live execution
- mpifileutils image build 또는 execution
- DMS API/Worker Kubernetes Deployment 완성
- mTLS ingress live validation

## API와 Payload

기존 endpoint를 우선 사용한다.

```text
PATCH  /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
POST   /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:block
DELETE /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
POST   /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:sync
```

Consistency check endpoint가 없다면 다음 중 하나로 추가한다. Phase 5에서는 단일 namespace quota check만 필요하다.

```text
POST /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:check
```

또는 Operational Query 계층에 read-only endpoint로 추가할 수 있다.

```text
POST /api/v1/operations/resources/kubernetes/{cluster_name}/{namespace_name}:check
```

구현자는 기존 API 구조와 repository 경계를 보고 더 자연스러운 쪽을 선택한다. 단, check는 backend mutation을 수행하지 않아야 한다.

### Update payload

Phase 5 권장 update payload:

```json
{
  "requester_id": "portal:alice",
  "payload": {
    "resource_type": "user",
    "quota": {
      "requests_storage_bytes": 268435456,
      "pvc_count": 4
    },
    "storage_class_quotas": [
      {
        "storage_name": "longhorn-b",
        "requests_storage_bytes": 268435456
      }
    ],
    "memo": "phase5 quota increase"
  }
}
```

CephFS target에서는 `storage_name`만 다르게 사용한다.

```json
{
  "storage_class_quotas": [
    {
      "storage_name": "cephfs-a",
      "requests_storage_bytes": 268435456
    }
  ]
}
```

명시적으로 거부할 payload:

- path parameter의 `cluster_name`과 payload `cluster_name`이 다르다.
- path parameter의 `namespace_name`과 payload `namespace_name`이 다르다.
- mapping과 다른 `storage_class_name`이 직접 들어온다.
- quota 값이 음수, 0이거나 정수 byte가 아니다.
- 감소 요청인데 live `status.used`보다 작은 quota로 낮추려고 한다.
- 대상 DMS resource가 operational PostgreSQL에 없다.
- live namespace 또는 DMS-managed ResourceQuota가 없다.

### Block payload

권장 payload:

```json
{
  "requester_id": "portal:alice",
  "payload": {
    "block": true,
    "block_mode": "quota-zero",
    "reason": "phase5 block verification"
  }
}
```

Unblock payload:

```json
{
  "requester_id": "portal:alice",
  "payload": {
    "block": false,
    "reason": "phase5 unblock verification"
  }
}
```

규칙:

- `block=true`는 current desired hard limit을 restore state로 저장한다.
- `block=true`는 `requests.storage`, `persistentvolumeclaims`, StorageClass-specific `requests.storage`를 모두 0으로 렌더링한다.
- `block=false`는 restore state가 없으면 fail-closed 한다.
- `resource_type`이 `system` 또는 `admin`인 resource에 대한 `block=true`는 Planner에서 거부한다.

### Delete payload

DELETE body를 지원하지 않는 client를 고려해 request body 없이도 동작 가능해야 한다. `requester_id`가 필요한 현재 API shape라면 query/body/header 중 기존 project convention에 맞춰 전달한다.

Phase 5 delete 규칙:

- `ResourceQuota/dms-storage-quota`만 삭제한다.
- namespace는 삭제하지 않는다.
- non-DMS ResourceQuota는 삭제하지 않는다.
- live ResourceQuota가 이미 없으면 DB state와 비교해 idempotent delete로 처리할지 `Missing`으로 처리할지 명확히 선택한다. 권장 기본값은 `Missing` terminal result와 `backend_side_effect=false`다.

### Sync payload

권장 payload:

```json
{
  "requester_id": "portal:alice",
  "payload": {
    "accept_live_state": true,
    "reason": "operator accepted live ResourceQuota"
  }
}
```

규칙:

- sync는 Kubernetes object를 변경하지 않는다.
- live `ResourceQuota/dms-storage-quota`가 없으면 `Missing`으로 기록한다.
- live object가 DMS identity label/annotation을 만족하지 않으면 fail-closed 한다.
- sync 결과는 DB applied/observed state와 result summary에 live `spec.hard`, `status.hard`, `status.used`, metadata를 저장한다.

### Check payload

권장 payload:

```json
{
  "requester_id": "portal:alice",
  "payload": {
    "scope": "single",
    "include_live_resourcequota": true
  }
}
```

규칙:

- check는 Kubernetes object를 변경하지 않는다.
- DB desired/applied/observed snapshot과 live snapshot을 비교한다.
- 비교 대상은 최소한 `spec.hard`, DMS labels/annotations, namespace 존재 여부, ResourceQuota 존재 여부다.
- `status.used`는 drift 판단의 참고값으로 저장하되 desired drift로 처리하지 않는다.

## ResourceQuota Rendering

Phase 5도 Phase 4 renderer를 재사용한다.

Longhorn 256 MiB update 예시:

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: dms-storage-quota
  namespace: dms-phase5-longhorn-<token>
spec:
  hard:
    requests.storage: "256Mi"
    persistentvolumeclaims: "4"
    testbed-longhorn.storageclass.storage.k8s.io/requests.storage: "256Mi"
```

CephFS 256 MiB update 예시:

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: dms-storage-quota
  namespace: dms-phase5-cephfs-<token>
spec:
  hard:
    requests.storage: "256Mi"
    persistentvolumeclaims: "4"
    testbed-cephfs.storageclass.storage.k8s.io/requests.storage: "256Mi"
```

block 예시:

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: dms-storage-quota
spec:
  hard:
    requests.storage: "0"
    persistentvolumeclaims: "0"
    <storage-class-name>.storageclass.storage.k8s.io/requests.storage: "0"
```

렌더링 규칙:

- 내부 DB와 API payload는 byte 정수를 기준으로 한다.
- Kubernetes manifest는 가능하면 binary suffix 또는 plain integer string으로 렌더링한다.
- live verification에서는 `128Mi`, `256Mi`, `64Mi`, `32Mi`처럼 작은 값을 사용한다.
- `persistentvolumeclaims`는 PVC count quota로 사용한다.
- StorageClass-specific key 형식:

```text
{storage_class_name}.storageclass.storage.k8s.io/requests.storage
```

## Kubernetes Live Adapter

Phase 4 live adapter를 확장한다.

권장 이름:

- `KubernetesNamespaceQuotaLiveAdapter`
- 또는 `KubectlKubernetesNamespaceQuotaAdapter`

지원 mode:

- Phase 5 필수: `ssh-kubectl`
- 기존 `DMS_KUBERNETES_MUTATION_MODE`와 `DMS_CLUSTER_CONTROL_HOSTS_JSON`를 재사용한다.

권장 설정:

- `DMS_KUBERNETES_MUTATION_MODE=ssh-kubectl`
- `DMS_CLUSTER_CONTROL_HOSTS_JSON={"cluster-a":"c1-control","cluster-b":"c2-control"}`
- `DMS_KUBERNETES_MUTATION_TIMEOUT_SECONDS=30`

필수 method 동작:

### `read_resource_quota(cluster_name, namespace_name)`

- namespace existence 조회
- `ResourceQuota/dms-storage-quota` existence 조회
- 있으면 metadata, labels/annotations, `spec.hard`, `status.hard`, `status.used`, UID/resourceVersion 반환
- 없으면 `exists=false`

### `apply_resource_quota(plan)`

- Phase 4 create/apply path를 update에도 재사용한다.
- namespace와 DMS-managed ResourceQuota가 이미 존재해야 하는 update에서는 namespace auto-create를 수행하지 않는다.
- apply 후 `kubectl get resourcequota dms-storage-quota -o json` 재조회
- observed state에 `spec.hard`, `status.hard`, `status.used`, metadata 기록

### `delete_resource_quota(plan)`

- `ResourceQuota/dms-storage-quota`만 삭제
- delete 후 같은 name을 다시 조회해 missing 확인
- namespace existence는 유지되는지 확인
- non-DMS ResourceQuota가 있으면 그대로 남아 있는지 optional verification으로 기록

### `sync_live_state(plan)`

- live ResourceQuota read-only 조회
- DB update에 필요한 normalized live snapshot 반환
- Kubernetes object를 patch/apply/delete하지 않음

### `check_resource_quota(plan)`

- live ResourceQuota read-only 조회
- DB snapshot과 비교할 수 있는 normalized live snapshot 반환
- Kubernetes object를 patch/apply/delete하지 않음

## Planner Integration

Planner는 Phase 3/4 guard를 유지하고 Kubernetes namespace quota lifecycle operation에 다음을 추가한다.

- update/block/delete/sync/check는 대상 resource가 operational PostgreSQL에 있어야 한다.
- update/block/delete는 target resource의 prior active request/plan/run이 있으면 backend side effect 없이 conflict 또는 wait/reject로 처리한다.
- update는 current DB desired quota, request quota, live `status.used`를 비교한다.
- update decrease guard는 live `status.used`보다 낮은 hard limit을 backend side effect 없이 거부한다.
- block은 restore state를 plan desired state 또는 execution metadata에 포함한다.
- unblock은 restore state가 없으면 fail-closed 한다.
- delete는 DMS-managed ResourceQuota만 대상으로 plan을 만든다.
- sync/check는 read-only operation이지만 request lifecycle에는 남긴다.
- failed mapping, missing mapping, disabled mapping, `readiness.resource_management != Ready`는 기존처럼 fail-closed 한다.
- CephFS와 Longhorn 모두 같은 Planner path를 통과해야 하며, backend별 special-case branching은 StorageClass/provisioner validation과 renderer key 정도로 제한한다.

Plan metadata 권장 shape:

```json
{
  "resource_kind": "KubernetesNamespaceQuota",
  "backend_side_effect_owner": "rm-worker",
  "operation": "kubernetes.namespace_quota.update",
  "kubernetes_backend": {
    "cluster_name": "cluster-b",
    "namespace_name": "dms-phase5-longhorn-abc123",
    "resource_quota_name": "dms-storage-quota",
    "storage_classes": [
      {
        "storage_name": "longhorn-b",
        "storage_class_name": "testbed-longhorn",
        "provisioner": "driver.longhorn.io"
      }
    ]
  },
  "quota_decrease_guard": {
    "checked": true,
    "used": {
      "requests.storage": "64Mi"
    },
    "decision": "allowed"
  }
}
```

## RM Worker Integration

RM Worker runtime은 existing lifecycle 원칙을 유지한다.

공통 순서:

1. claimable RM plan 조회
2. plan claim 저장
3. run state `Applying` 저장
4. live Kubernetes adapter 호출
5. ResourceQuota read-back verification
6. resource desired/applied/observed state 저장
7. terminal result 저장
8. observability event 저장

Operation별 차이:

- update: apply 후 hard/used read-back을 저장한다.
- block: restore state와 zero hard limit 적용 결과를 저장한다.
- unblock: restored hard limit 적용 결과를 저장한다.
- delete: delete confirmation과 namespace still exists evidence를 저장한다.
- sync: backend side effect 없이 live snapshot을 DB state로 반영한다.
- check: backend side effect 없이 diff summary를 result에 저장한다.

예외 처리:

- update 전 validation failure는 `Rejected` 또는 동등한 terminal status로 backend side effect 없이 종료한다.
- apply/delete 호출 후 verification 실패 또는 결과 저장 실패 가능성이 있으면 `UnknownAfterSideEffect` 또는 `RecoveryNeeded`로 남긴다.
- adapter command timeout은 backend 결과를 알 수 없으므로 action-required에서 운영자가 확인 가능해야 한다.
- observability event 저장 실패가 core lifecycle success로 둔갑하면 안 된다.

## Observability

Phase 5에서 남겨야 하는 diagnostic event:

- `kubernetes_resourcequota_update_started`
- `kubernetes_resourcequota_update_completed`
- `kubernetes_resourcequota_update_rejected`
- `kubernetes_resourcequota_block_started`
- `kubernetes_resourcequota_block_completed`
- `kubernetes_resourcequota_unblock_completed`
- `kubernetes_resourcequota_delete_started`
- `kubernetes_resourcequota_delete_completed`
- `kubernetes_resourcequota_consistency_checked`
- `kubernetes_resourcequota_sync_completed`

모든 event payload에는 가능하면 `cluster_name`, `namespace_name`, `storage_name`, `storage_class_name`, `provisioner`를 포함한다. Critical lifecycle state는 operational PostgreSQL에 남겨야 한다.

## 데이터 모델 보강

가능하면 기존 `resources`와 `results` 구조를 우선 사용한다.

부족하면 expand-compatible migration으로 다음 정보를 표현한다.

- Kubernetes namespace quota resource identity: `cluster_name + namespace_name`
- DMS-managed ResourceQuota name
- storage_name, storage_class_name, CSI provisioner
- current desired hard limits
- applied hard limits
- observed ResourceQuota UID/resourceVersion
- observed `spec.hard`
- observed `status.hard`
- observed `status.used`
- block restore hard limits
- block state
- consistency check status
- consistency diff summary
- sync source snapshot

새 table을 추가하기 전에 기존 `resources.desired_state`, `resources.applied_state`, `resources.observed_state`, `results.verification_summary`로 충분한지 먼저 판단한다.

## 테스트베드 Live Verification

Phase 5는 mock이 아니라 실제 테스트베드 backend mutation을 검증해야 한다.

읽어야 하는 문서:

- `/home/mason/workspace/testbed/TOPOLOGY.md`
- `/home/mason/workspace/testbed/PostgreSQL.md`
- `/home/mason/workspace/testbed/CephFS.md`
- `/home/mason/workspace/testbed/Longhorn.md`
- `/home/mason/workspace/testbed/testbed-info.json`
- `/home/mason/workspace/testbed/testbed-summary.json`
- `docs/dms-done.md`
- `docs/dms-phase4-verification.md`

사전 확인 command:

```bash
ssh c1-control "kubectl get nodes -o wide; kubectl get storageclass testbed-cephfs -o yaml; kubectl -n rook-ceph get pods"
ssh c2-control "kubectl get nodes -o wide; kubectl get storageclass testbed-longhorn -o yaml; kubectl -n longhorn-system get pods"
```

최소 live flow는 CephFS와 Longhorn 양쪽 target에 대해 수행한다.

1. 새 PostgreSQL operational/observability DB를 만든다.
2. DMS migrations를 적용한다.
3. `cluster-a`와 `cluster-b` Kubernetes read-only inventory를 조회한다.
4. `cephfs-a -> cluster-a/testbed-cephfs` storage mapping을 sanity `Ready`로 만든다.
5. `longhorn-b -> cluster-b/testbed-longhorn` storage mapping을 sanity `Ready`로 만든다.
6. `cluster-a`와 `cluster-b` RM Agent report를 제출한다.
7. 각 target namespace에 Kubernetes namespace quota create request로 `128Mi`, PVC count `2` ResourceQuota를 만든다.
8. 각 target에서 64Mi PVC를 만들고 Bound 확인한다.
9. 각 target에서 update request로 quota를 `256Mi`, PVC count `4`로 올린다.
10. 각 target에서 192Mi PVC를 만들고 Bound 확인한다.
11. 각 target에서 decrease guard 검증: 현재 used보다 작은 quota 감소 요청이 backend side effect 없이 거부되는지 확인한다.
12. 각 target에서 block=ON request를 제출하고 ResourceQuota hard limit이 0인지 확인한다.
13. 각 target에서 block 상태의 새 PVC admission이 거부되는지 확인한다.
14. 각 target에서 block=OFF request를 제출하고 hard limit이 restore되는지 확인한다.
15. 각 target에서 manual drift를 만든다. 예: `kubectl patch resourcequota dms-storage-quota`로 `requests.storage`를 다른 값으로 변경한다.
16. 각 target에서 consistency check request가 `Drifted`를 기록하는지 확인한다.
17. 각 target에서 sync request가 live hard/used를 DB applied/observed state로 받아들이는지 확인한다.
18. 각 target에서 delete request로 `ResourceQuota/dms-storage-quota`만 삭제한다.
19. 각 target namespace는 남아 있고 non-DMS ResourceQuota는 삭제되지 않았는지 확인한다.
20. action-required에 예상치 못한 issue가 없는지 확인한다.
21. verification PVC/namespace를 cleanup한다.
22. `docs/dms-phase5-verification.md`와 `docs/dms-done.md`를 업데이트한다.

## Phase 5 검증 매트릭스

| Area | Required verification | CephFS evidence | Longhorn evidence |
| --- | --- | --- | --- |
| Migration | SQLite/PostgreSQL migration 성공 | live DB migration rows | live DB migration rows |
| Mapping guard | Storage mapping sanity Ready | `cephfs-a` Ready | `longhorn-b` Ready |
| Create baseline | Phase 4 create/apply flow 유지 | hard 128Mi, PVC count 2 | hard 128Mi, PVC count 2 |
| PVC baseline | 64Mi PVC admitted | CephFS PVC Bound | Longhorn PVC Bound |
| Update increase | existing ResourceQuota update | hard 256Mi | hard 256Mi |
| PVC after update | larger PVC admitted | 192Mi PVC Bound | 192Mi PVC Bound |
| Decrease guard | used보다 작은 quota 감소 거부 | terminal result, no side effect | terminal result, no side effect |
| Block | hard limit zeroing | live hard `0`, restore state 저장 | live hard `0`, restore state 저장 |
| Admission while blocked | new PVC rejected | Kubernetes admission forbidden | Kubernetes admission forbidden |
| Unblock | restore hard limit | live hard restored | live hard restored |
| Drift check | manual live patch 감지 | `Drifted`, diff summary | `Drifted`, diff summary |
| Sync from live | live state를 DB에 반영 | DB hard equals live hard | DB hard equals live hard |
| Delete | DMS ResourceQuota only deleted | `dms-storage-quota` missing, namespace remains | `dms-storage-quota` missing, namespace remains |
| Delete safety | non-DMS ResourceQuota preserved | non-DMS object still exists | non-DMS object still exists |
| Observability | lifecycle events written | diagnostic events include CephFS target | diagnostic events include Longhorn target |
| Documentation | evidence recorded | `docs/dms-phase5-verification.md` | `docs/dms-phase5-verification.md` |

## 구현 순서

1. 현재 Kubernetes namespace quota API, Planner, RM Worker, adapter boundary를 확인한다.
2. Phase 5 resource state shape를 확정한다.
3. ResourceQuota hard limit renderer를 update/block/unblock에서 재사용 가능하게 정리한다.
4. live adapter에 read/update/delete/check/sync method를 추가한다.
5. Planner가 update/block/delete/sync/check operation별 guard를 수행하게 한다.
6. quota decrease guard가 live `status.used`를 기준으로 동작하게 한다.
7. block restore state 저장과 unblock 복구 로직을 구현한다.
8. delete가 DMS-managed ResourceQuota만 삭제하도록 검증한다.
9. consistency check diff summary를 구현한다.
10. sync from live state가 Kubernetes side effect 없이 DB state만 갱신하게 한다.
11. Unit tests로 renderer, decrease guard, block restore, delete safety, check/sync contract를 검증한다.
12. Live verification script를 추가한다.
13. 테스트베드에서 PostgreSQL + `cluster-a/testbed-cephfs` lifecycle flow를 실행한다.
14. 테스트베드에서 PostgreSQL + `cluster-b/testbed-longhorn` lifecycle flow를 실행한다.
15. 두 target의 결과를 비교해 backend-specific special-case가 없는지 확인한다.
16. `docs/dms-phase5-verification.md`와 `docs/dms-done.md`를 업데이트한다.

## 구현 및 검증 진입점

주요 진입점:

- `src/dms/adapters.py`: Kubernetes namespace quota live adapter extension
- `src/dms/planner.py`: update/block/delete/sync/check plan enrichment and guard
- `src/dms/workers.py`: RM Worker live adapter execution path
- `src/dms/repositories.py`: resource/result observed state persistence if needed
- `src/dms/config.py`: mutation mode/control host/timeout settings
- `src/dms/api.py`: payload validation and check endpoint if needed
- `tests/test_phase5_kubernetes_quota_lifecycle.py`: renderer/planner/adapter contract tests
- `scripts/phase5_kubernetes_quota_lifecycle.py`: live verification body
- `scripts/verify-phase5-testbed.sh`: PostgreSQL DB creation and CephFS/Longhorn testbed orchestration
- `docs/dms-phase5-verification.md`: executed evidence
- `docs/dms-done.md`: Done status update

대표 검증 명령:

```bash
cd /home/mason/workspace/dms
/tmp/dms-phase3-venv/bin/python -m pytest -q
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase5-testbed.sh
```

## Phase 5 문서 산출물

Phase 5 완료 시 다음 문서를 최신화한다.

- `docs/dms-phase5-verification.md`: live test command, output, DB evidence, Kubernetes evidence
- `docs/dms-done.md`: Phase 5 done/not-done 상태와 재검증 command
- `testbed/CephFS.md`: CephFS ResourceQuota/PVC 검증 방식이 바뀐 경우
- `testbed/Longhorn.md`: Longhorn ResourceQuota/PVC 검증 방식이 바뀐 경우
- `docs/dms-design.md`: 실제 구현 API/status/result shape가 설계와 달라진 경우

## Phase 5 완료 후 다음 Phase 후보

Phase 5 완료 후에는 다음 중 하나로 진행한다.

### Phase 6A: DMS Agent DaemonSet

Phase 3의 synthetic Agent report를 실제 node-local probe로 대체한다.

- RM Agent DaemonSet on managed clusters
- DM Agent on control cluster worker nodes
- mount/tool/credential/network probe
- report freshness and identity evidence
- storage mapping sanity가 synthetic report 없이 실제 Agent report로 Ready/Failed 계산

### Phase 6B: Data Management preflight and Volcano scan

Kubernetes ResourceQuota lifecycle이 안정적이면 read-only Data Management부터 시작한다.

- POSIX identity preflight using LDAP mapping
- tool option registry
- `dscan` VolcanoJob live execution
- artifact URI persistence

단, Phase 6B는 실제 Agent DaemonSet 없이 synthetic Agent report에 의존하면 검증 의미가 약해진다. 운영 구현 순서로는 Phase 6A를 먼저 수행하는 것을 권장한다.
