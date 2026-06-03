# DMS Phase 17 Implementation Prompt: Kubernetes ResourceQuota Live Adapter Unification

이 문서는 `docs/dms-phase16.md` 완료 이후 열일곱 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 17의 목표는 **Kubernetes namespace quota Resource Management 경로를 모든 CSI StorageClass backend에 대해 하나의 live `ResourceQuota` adapter로 통합**하는 것이다.

Phase 17은 Data Management 구현으로 넘어가기 전에 닫아야 할 Resource Management 정합성 phase다. Phase 17 시작 전에는 CephFS CSI와 Longhorn StorageClass quota는 live Kubernetes `ResourceQuota/dms-storage-quota` 경로를 탔지만, GPFS CSI mapping은 `GpfsKubernetesNamespaceQuotaAdapter` skeleton으로 분기되어 실제 Kubernetes `ResourceQuota`를 만들지 않았다. 이 차이는 운영자가 GPFS CSI StorageClass quota도 CephFS/Longhorn과 동일하게 namespace quota로 제한된다고 기대할 때 위험했다.

## Phase 17 시작 전 문제

Phase 17 시작 전 구현 기준:

- `BackendAdapterRegistry.kubernetes_for_plan()`은 `backend_type=gpfs` mapping을 만나면 `GpfsKubernetesNamespaceQuotaAdapter`를 반환한다.
- `GpfsKubernetesNamespaceQuotaAdapter`는 `gpfs-kubernetes-quota-stub` adapter result를 만들고 실제 `kubectl apply`, read-back, delete, check, sync, import, audit을 수행하지 않는다.
- CephFS와 Longhorn은 generic live `KubernetesNamespaceQuotaLiveAdapter`를 사용한다.
- `GENERIC_KUBERNETES_QUOTA_BACKENDS = {cephfs, longhorn}` allowlist 때문에 새 CSI backend, 예를 들어 GPFS CSI, WEKA CSI, Pure CSI, Lustre CSI 같은 mapping은 Kubernetes namespace quota 경로에서 backend-specific code가 없으면 fail 또는 stub 경로로 빠질 수 있다.
- Kubernetes namespace quota는 storage backend의 filesystem quota primitive가 아니라 Kubernetes API object다. 따라서 StorageClass가 Kubernetes에 존재하고 DMS storage mapping sanity/readiness guard를 통과했다면 backend type과 무관하게 같은 live adapter를 사용해야 한다.

이 문제는 GPFS filesystem fileset quota 구현과 혼동하면 안 된다. `backend_type=gpfs` filesystem resource는 IBM Storage Scale `mm*` command adapter를 계속 사용한다. Phase 17은 **Kubernetes CSI StorageClass namespace quota 경로만** 통합한다.

## 현재 구현 상태

Phase 17 구현 후 현재 코드 기준:

- `BackendAdapterRegistry.kubernetes_for_plan()`은 backend type을 보지 않고 configured Kubernetes adapter를 반환한다.
- live registry는 `KubernetesNamespaceQuotaLiveAdapter.from_settings(settings)`를 사용한다.
- test/dev registry는 명시적인 `StubKubernetesNamespaceQuotaAdapter`를 사용할 수 있다.
- `GpfsKubernetesNamespaceQuotaAdapter` class와 `GENERIC_KUBERNETES_QUOTA_BACKENDS` allowlist는 제거됐다.
- GPFS CSI, CephFS CSI, Longhorn, WEKA/future CSI StorageClass namespace quota는 모두 같은 Kubernetes `ResourceQuota/dms-storage-quota` live adapter path를 탄다.
- GPFS filesystem resource는 계속 IBM Storage Scale `mm*` command adapter를 사용하고, unknown filesystem backend는 fail-closed한다.
- GPFS default CSI driver는 inventory sanity의 `_default_csi_driver()`에 `spectrumscale.csi.ibm.com`으로 등록됐다.

## Phase 17 목표

Phase 17의 핵심 목표는 다음 일곱 가지다.

1. **All CSI StorageClass ResourceQuota paths use live Kubernetes adapter**
2. **GPFS CSI namespace quota uses `KubernetesNamespaceQuotaLiveAdapter`**
3. **Backend type allowlist 제거 또는 backend-neutral validation으로 교체**
4. **Filesystem backend adapter와 Kubernetes ResourceQuota adapter 분리 보존**
5. **Mixed backend multi-StorageClass quota 지원**
6. **Regression tests and testbed verification**
7. **Docs/install guidance update**

구현 완료 기준:

- Kubernetes namespace quota create/update/block/delete/import/check/sync/audit/expiration-sweep는 모든 storage backend에서 `KubernetesNamespaceQuotaLiveAdapter` 또는 동등한 live Kubernetes API adapter를 사용한다.
- `backend_type=gpfs` StorageClass quota request도 live `ResourceQuota/dms-storage-quota`를 target cluster에 apply하고 read-back한다.
- `GpfsKubernetesNamespaceQuotaAdapter`는 제거하거나 deprecated unused class로 남기되 registry에서 선택되지 않아야 한다.
- `gpfs-kubernetes-quota-stub` 성공 결과는 production/live path에서 더 이상 나오면 안 된다.
- Kubernetes namespace quota adapter selection은 `backend_type`이 아니라 Kubernetes quota operation 여부와 storage mapping의 `cluster_name`, `storage_class_name`, sanity/readiness를 기준으로 판단한다.
- `backend_type=weka`, `backend_type=lustre`, `backend_type=pure`, `backend_type=nfs` 등 아직 filesystem adapter가 없는 backend라도, StorageClass mapping이 sane/Ready이면 Kubernetes namespace quota ResourceQuota 경로는 live adapter를 사용할 수 있어야 한다.
- Filesystem resource operation은 계속 backend-specific adapter를 사용한다. Unknown filesystem backend는 Phase 14 fail-closed 원칙을 유지한다.
- Multi-StorageClass quota에서 CephFS, GPFS, Longhorn, future CSI backend가 섞여도 하나의 DMS-managed `ResourceQuota` hard map으로 렌더링된다.

## Adapter Selection Rule

Phase 17 이후 registry rule은 다음처럼 단순해야 한다.

```text
filesystem resource operation
  -> backend_type별 filesystem adapter
  -> unknown filesystem backend는 fail-closed

kubernetes namespace quota operation
  -> storage_class_quotas[]와 namespace-wide quota를 모두 Kubernetes ResourceQuota live adapter로 처리
  -> backend_type은 ResourceQuota adapter 선택 기준이 아님
  -> storage mapping sanity/readiness와 cluster/storage_class consistency가 precondition
```

구현 방향:

- `BackendAdapterRegistry.kubernetes_for_plan()`에서 `backend_type == GPFS_BACKEND_TYPE` special case를 제거한다.
- `GENERIC_KUBERNETES_QUOTA_BACKENDS` allowlist는 제거하거나 ResourceQuota path에 사용하지 않는다.
- `self.default_kubernetes_adapter`가 있으면 live/test registry에 따라 해당 adapter를 반환한다.
- live runtime은 `BackendAdapterRegistry.with_live_defaults()`를 통해 `KubernetesNamespaceQuotaLiveAdapter.from_settings(settings)`를 사용한다.
- test runtime은 `BackendAdapterRegistry.with_test_stubs()`를 통해 명시적으로 stub adapter를 사용할 수 있다.
- storage mapping이 필요한 request에서 mapping이 없거나 sanity/readiness가 Ready가 아니면 Planner guard가 reject해야 한다.
- namespace-wide quota만 다루고 `storage_class_quotas[]`가 비어 있는 request도 mapping 없이 live adapter를 사용할 수 있어야 한다.

예상 registry behavior:

```python
def kubernetes_for_plan(self, plan):
    if self.default_kubernetes_adapter:
        return self.default_kubernetes_adapter
    raise BackendPreconditionError("Kubernetes namespace quota live adapter is not configured")
```

단, registry 내부에서 referenced storage mappings를 검사하고 싶다면 backend type allowlist가 아니라 다음 조건을 검사한다.

- referenced storage mapping exists
- `cluster_name` matches request cluster
- `storage_class_name` is present for StorageClass-specific quota
- Planner 또는 sanity layer에서 `readiness.resource_management == Ready`

## ResourceQuota Rendering

`render_kubernetes_resource_quota_hard()`는 backend-neutral renderer로 유지한다.

Namespace-wide quota:

```json
{
  "quota": {
    "requests_storage_bytes": 107374182400,
    "pvc_count": 10
  }
}
```

렌더링:

```yaml
spec:
  hard:
    requests.storage: 100Gi
    persistentvolumeclaims: "10"
```

StorageClass-specific quota:

```json
{
  "storage_class_quotas": [
    {
      "storage_name": "gpfs-a",
      "requests_storage_bytes": 1099511627776,
      "pvc_count": 20
    }
  ]
}
```

Planner가 `gpfs-a -> storage_class_name=gpfs-csi`로 enrich한 뒤 렌더링:

```yaml
spec:
  hard:
    requests.storage: 1Ti
    persistentvolumeclaims: "20"
    gpfs-csi.storageclass.storage.k8s.io/requests.storage: 1Ti
    gpfs-csi.storageclass.storage.k8s.io/persistentvolumeclaims: "20"
```

이 렌더링은 CephFS, GPFS, Longhorn, WEKA 등 모든 CSI StorageClass에서 동일해야 한다.

## GPFS CSI Expected Behavior

GPFS CSI mapping 예시:

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
    "ssh_host": "gpfs-rm-1",
    "command_runner": "ssh-host-exec",
    "csi_driver": "spectrumscale.csi.ibm.com"
  },
  "cluster_name": "cluster-a",
  "storage_class_name": "gpfs-csi"
}
```

Kubernetes namespace quota request:

```json
{
  "requester_id": "alice",
  "payload": {
    "cluster_name": "cluster-a",
    "namespace_name": "alice",
    "allow_namespace_create": true,
    "quota": {
      "requests_storage_bytes": 4398046511104,
      "pvc_count": 20
    },
    "storage_class_quotas": [
      {
        "storage_name": "gpfs-a",
        "requests_storage_bytes": 4398046511104,
        "pvc_count": 20
      }
    ],
    "expires_at": "2099-01-01T00:00:00Z"
  }
}
```

Expected live side effect:

- target cluster namespace exists or is created when `allow_namespace_create=true`
- `ResourceQuota/dms-storage-quota` is applied in that namespace
- `metadata.labels.app.kubernetes.io/managed-by=dms`
- `metadata.labels.dms.io/resource-kind=kubernetes-namespace-quota`
- `metadata.annotations.dms.io/resource-key=<cluster>:<namespace>`
- `metadata.annotations.dms.io/storage-names=gpfs-a`
- `metadata.annotations.dms.io/expires-at=<canonical expires_at>`
- `spec.hard` contains namespace-wide and `gpfs-csi.storageclass.storage.k8s.io/*` keys
- observed state reads live `spec.hard`, `status.hard`, `status.used`, labels, annotations, uid, resourceVersion

Expected non-side-effect behavior:

- No `mm*` IBM Storage Scale command is executed for Kubernetes namespace quota.
- GPFS fileset quota state is not read or modified.
- No `gpfs-kubernetes-quota-stub` adapter result is recorded.

## Mixed Backend Multi-StorageClass Quota

Phase 17 must support a single namespace quota resource with mixed StorageClass entries:

```json
{
  "storage_class_quotas": [
    {
      "storage_name": "cephfs-a",
      "requests_storage_bytes": 536870912000,
      "pvc_count": 10
    },
    {
      "storage_name": "gpfs-a",
      "requests_storage_bytes": 1099511627776,
      "pvc_count": 20
    },
    {
      "storage_name": "weka-a",
      "requests_storage_bytes": 2199023255552,
      "pvc_count": 30
    }
  ]
}
```

Planner enrichment should produce:

```json
[
  {"storage_name": "cephfs-a", "storage_class_name": "cephfs-rwx"},
  {"storage_name": "gpfs-a", "storage_class_name": "gpfs-csi"},
  {"storage_name": "weka-a", "storage_class_name": "weka-csi"}
]
```

Live adapter should apply one `ResourceQuota` with all corresponding StorageClass-specific keys. The first mapping's backend type must not decide the adapter for the whole request.

## Storage Mapping Sanity

Storage mapping sanity remains the guard before ResourceQuota operations.

Required:

- `storage_name` exists.
- mapping is not disabled.
- `sanity_status` is not `Failed` or `Unknown`.
- `readiness.resource_management == Ready`.
- mapping `cluster_name` matches request `cluster_name`.
- request-provided `storage_class_name`, if present, matches mapping `storage_class_name`.
- duplicate derived `storage_class_name` entries are rejected.

GPFS-specific improvement:

- Add GPFS default CSI driver mapping if useful:

```python
"gpfs": "spectrumscale.csi.ibm.com"
```

However, users should still be able to override `backend_template.csi_driver` because IBM Storage Scale CSI deployments may use site-specific provisioner naming or wrappers.

Future backend rule:

- For backend types without a known default CSI driver, require `backend_template.csi_driver` when `storage_class_name` is set and sanity is expected to verify the live StorageClass provisioner.
- Kubernetes namespace quota support should not require a filesystem backend implementation.

## Operations To Cover

All of the following must use the live Kubernetes ResourceQuota adapter for every CSI backend:

- `kubernetes.namespace_quota.create`
- `kubernetes.namespace_quota.update`
- `kubernetes.namespace_quota.block`
- `kubernetes.namespace_quota.delete`
- `kubernetes.namespace_quota.import`
- `kubernetes.namespace_quota.consistency_check`
- `kubernetes.namespace_quota.sync`
- `kubernetes.namespace_quota.audit`
- `kubernetes.namespace_quota.expiration_sweep`

Create/update/block/delete/import/check/sync/audit behavior should remain the existing Phase 4-9 and Phase 15 contract. Phase 17 changes adapter selection, not the API shape.

## Tests

Required unit/regression coverage:

1. GPFS CSI namespace quota create uses `KubernetesNamespaceQuotaLiveAdapter` in live registry, not `GpfsKubernetesNamespaceQuotaAdapter`.
2. GPFS CSI create renders `gpfs-csi.storageclass.storage.k8s.io/requests.storage` and optional `persistentvolumeclaims` keys.
3. GPFS CSI create applies and reads back a live ResourceQuota in a fake kubectl executor or equivalent adapter test.
4. GPFS CSI update/check/sync/import/delete route to live adapter semantics.
5. Mixed CephFS + GPFS + Longhorn StorageClass quota renders all StorageClass keys in one hard map.
6. Unknown/future backend with valid `storage_class_name` and Ready mapping uses live ResourceQuota adapter for Kubernetes quota operations.
7. Unknown/future backend still fails closed for filesystem resource operations unless a filesystem adapter exists.
8. Namespace-wide quota with no `storage_class_quotas[]` still uses live adapter and does not require a storage mapping.
9. `gpfs-kubernetes-quota-stub` no longer appears in live registry tests.
10. `BackendAdapterRegistry.with_test_stubs()` can still use explicit test stubs where tests request them.

Existing tests to update:

- `tests/test_gpfs_backend.py::test_gpfs_kubernetes_namespace_quota_uses_gpfs_csi_mapping`
  - Replace `gpfs-kubernetes-quota-stub` expectation with live ResourceQuota behavior or move it to a registry unit test proving GPFS no longer selects the stub.
- Phase 14 runtime hardening tests
  - Ensure the live RM Worker registry uses `KubernetesNamespaceQuotaLiveAdapter` for all Kubernetes namespace quota plans.
- Phase 6 multi-StorageClass tests
  - Add GPFS entry to multi-backend matrix.

## Testbed Verification

The testbed may not have IBM Storage Scale. That is acceptable because this phase verifies the Kubernetes ResourceQuota layer, not GPFS fileset quota.

Recommended testbed strategy:

1. Use existing CephFS and Longhorn StorageClass quota tests as baseline.
2. Create a temporary GPFS-like Kubernetes StorageClass and CSIDriver object if the testbed lacks real GPFS CSI:

```yaml
apiVersion: storage.k8s.io/v1
kind: CSIDriver
metadata:
  name: spectrumscale.csi.ibm.com
spec:
  attachRequired: false
---
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: dms-test-gpfs-csi
provisioner: spectrumscale.csi.ibm.com
volumeBindingMode: Immediate
```

3. Register a DMS storage mapping with `backend_type=gpfs`, `storage_class_name=dms-test-gpfs-csi`, and `csi_driver=spectrumscale.csi.ibm.com`.
4. Submit Kubernetes namespace quota create/update/check/sync/delete requests using `storage_class_quotas[].storage_name=gpfs-a`.
5. Verify live `ResourceQuota/dms-storage-quota` exists and contains `dms-test-gpfs-csi.storageclass.storage.k8s.io/requests.storage`.
6. Do not attempt PVC provisioning unless a real GPFS CSI provisioner exists. A synthetic StorageClass validates ResourceQuota rendering/apply/read-back, not storage provisioning.
7. Record clearly that IBM Storage Scale fileset command live verification is still separate from this Kubernetes ResourceQuota verification.

If a real GPFS CSI environment is available, also create a small PVC and verify Kubernetes admission/resource usage behavior. This is optional for Phase 17 testbed because the local testbed lacks GPFS.

## Documentation Updates

Update:

- `docs/backend-gpfs.md`
  - Clarify that GPFS filesystem resources use IBM Storage Scale commands, while GPFS CSI namespace quotas use the generic live Kubernetes ResourceQuota adapter after Phase 17.
- `docs/dms-done.md`
  - Move GPFS CSI ResourceQuota stub from completed/known limitation to Phase 17 fixed item after implementation.
- `install/README.md`
  - Make clear that Kubernetes namespace quota for every CSI StorageClass, including GPFS and future WEKA, is handled through Kubernetes ResourceQuota, not backend filesystem commands.
- `install/config/storage-mappings.example.json`
  - Ensure GPFS mapping includes `storage_class_name` and `csi_driver`.

Implementation and verification evidence is recorded in `docs/dms-phase17-verification.md`.

## Out Of Scope

Phase 17 does not implement:

- GPFS live IBM Storage Scale cluster verification
- GPFS fileset quota behavior changes
- WEKA filesystem quota adapter
- CSI driver installation or storage backend provisioning
- PVC provisioning success for synthetic StorageClass fixtures
- Data Management `scan/sync/rm` live execution
- VolcanoJob create/watch/terminate
- Worker heartbeat/lease renewal
- maintenance/drain full workflow

## Phase 17 이후 다음 작업 리스트

Phase 17로 Kubernetes ResourceQuota path를 backend-neutral live adapter로 통합한 뒤, Data Management 후보는 Phase 18로 진행한다.

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

권장 순서는 Phase 17로 Kubernetes ResourceQuota live adapter 통합을 닫은 뒤, Phase 18A로 Data Management read-only scan preflight를 구현하고, 그 다음 Phase 18B로 DM Worker/VolcanoJob live execution을 여는 것이다.
