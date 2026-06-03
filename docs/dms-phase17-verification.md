# DMS Phase 17 Verification: Kubernetes ResourceQuota Live Adapter Unification

Verification date: 2026-06-03 00:34 +0900

Phase 17 verifies that Kubernetes namespace quota Resource Management uses one
live Kubernetes `ResourceQuota/dms-storage-quota` adapter for every CSI
StorageClass backend, including GPFS CSI. This verification does not validate
IBM Storage Scale fileset commands; GPFS filesystem live validation still
requires a real GPFS cluster.

## Local Regression

Command:

```bash
cd /home/mason/workspace/dms
python3 -m pytest -q \
  tests/test_phase14_runtime_hardening.py \
  tests/test_gpfs_backend.py \
  tests/test_phase6_kubernetes_multi_storage_quota.py \
  tests/test_phase3_inventory.py
```

Output:

```text
33 passed in 15.42s
```

Coverage:

- live registry returns `KubernetesNamespaceQuotaLiveAdapter` for GPFS CSI
  Kubernetes quota plans
- unknown/future CSI backend mappings use the live Kubernetes quota adapter for
  Kubernetes quota plans
- unknown filesystem backend still fails closed for filesystem resource plans
- GPFS CSI namespace quota create records
  `adapter=kubernetes-namespace-quota-live`, not `gpfs-kubernetes-quota-stub`
- mixed CephFS + GPFS + WEKA StorageClass quota renders all hard keys into one
  DMS-managed `ResourceQuota`
- GPFS `backend_type` defaults to `spectrumscale.csi.ibm.com` for storage
  mapping sanity when `backend_template.csi_driver` is omitted

## Testbed Context

The local Vagrant testbed has Kubernetes `cluster-b` with Longhorn but does not
have IBM GPFS / IBM Storage Scale. The Phase 17 live check therefore used a
temporary synthetic GPFS CSI `CSIDriver` and `StorageClass` on `cluster-b`. This
validates Kubernetes API apply/read-back and DMS adapter selection, not PVC
provisioning by a real GPFS CSI driver.

Pre-check:

```bash
ssh c2-control "kubectl get nodes -o wide"
ssh c2-control "kubectl get storageclass"
ssh c2-control "kubectl get csidriver"
```

Observed:

```text
c2-control Ready
c2-worker  Ready
StorageClasses: longhorn-static, testbed-longhorn, testbed-longhorn-retain
CSIDriver: driver.longhorn.io
```

## Live Kubernetes ResourceQuota Check

Temporary GPFS-like cluster objects:

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
  name: dms-phase17-gpfs-csi
provisioner: spectrumscale.csi.ibm.com
volumeBindingMode: Immediate
```

DMS live path executed:

- local SQLite operational/observability DBs
- `BackendAdapterRegistry.with_live_defaults(...)`
- `Planner.run_once()`
- `RMWorkerRuntime.run_once()`
- `KubernetesNamespaceQuotaLiveAdapter` with `ssh-kubectl` through `c2-control`

Request summary:

```json
{
  "operation": "kubernetes.namespace_quota.create",
  "cluster_name": "cluster-b",
  "namespace_name": "dms-phase17-gpfs-quota",
  "storage_class_quotas": [
    {
      "storage_name": "gpfs-phase17",
      "requests_storage_bytes": 134217728,
      "pvc_count": 2
    }
  ],
  "quota": {
    "requests_storage_bytes": 268435456,
    "pvc_count": 4
  },
  "allow_namespace_create": true
}
```

DMS result summary:

```json
{
  "observed_adapter": "kubernetes-namespace-quota-live",
  "plan_adapter_class": "KubernetesNamespaceQuotaLiveAdapter",
  "planned": 1,
  "request_status": "Succeeded",
  "result_statuses": ["Succeeded"],
  "worker_processed": 1,
  "spec_hard": {
    "dms-phase17-gpfs-csi.storageclass.storage.k8s.io/persistentvolumeclaims": "2",
    "dms-phase17-gpfs-csi.storageclass.storage.k8s.io/requests.storage": "128Mi",
    "persistentvolumeclaims": "4",
    "requests.storage": "256Mi"
  }
}
```

Kubernetes read-back:

```bash
ssh c2-control "kubectl -n dms-phase17-gpfs-quota get resourcequota dms-storage-quota -o jsonpath='{.spec.hard}'"
```

Output:

```json
{
  "dms-phase17-gpfs-csi.storageclass.storage.k8s.io/persistentvolumeclaims": "2",
  "dms-phase17-gpfs-csi.storageclass.storage.k8s.io/requests.storage": "128Mi",
  "persistentvolumeclaims": "4",
  "requests.storage": "256Mi"
}
```

Annotation read-back:

```text
dms.io/storage-names=gpfs-phase17
dms.io/expires-at=2099-01-01T00:00:00+00:00
```

Cleanup completed:

```bash
ssh c2-control "kubectl delete namespace dms-phase17-gpfs-quota --ignore-not-found=true"
ssh c2-control "kubectl delete storageclass dms-phase17-gpfs-csi --ignore-not-found=true"
ssh c2-control "kubectl delete csidriver spectrumscale.csi.ibm.com --ignore-not-found=true"
```

Follow-up check returned no remaining temporary namespace, StorageClass, or
CSIDriver.

## Remaining Gaps

- Real IBM Storage Scale / GPFS CSI PVC provisioning was not verified because
  the testbed has no GPFS cluster.
- GPFS filesystem `mm*` command live validation remains a separate staging task.
- Phase 17 does not add Data Management live execution, VolcanoJob execution, or
  production Helm/Kustomize packaging.
