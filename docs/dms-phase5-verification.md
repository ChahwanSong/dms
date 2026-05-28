# DMS Phase 5 Verification

Date: 2026-05-28 15:57 +0900

Phase 5 검증은 Kubernetes CSI namespace storage quota lifecycle을 실제 테스트베드에서 확인했다. 검증 대상은 두 CSI backend 모두다.

- `cluster-a/testbed-cephfs`: Rook CephFS CSI, provisioner `rook-ceph.cephfs.csi.ceph.com`
- `cluster-b/testbed-longhorn`: Longhorn CSI, provisioner `driver.longhorn.io`

## Verified Scope

각 target에서 같은 flow를 실행했다.

1. storage mapping upsert and sanity check
2. `ResourceQuota/dms-storage-quota` create/apply
3. quota update from `128Mi/2 PVC` to `256Mi/4 PVC`
4. live `status.used` 기준 quota decrease guard
5. block to zero hard limit and PVC admission reject
6. unblock to restored hard limit
7. manual live drift and read-only consistency check
8. sync from live state into operational PostgreSQL
9. DMS-managed ResourceQuota delete only
10. non-DMS ResourceQuota preservation check

Namespace deletion은 script cleanup이다. DMS lifecycle delete로 검증한 것은 `ResourceQuota/dms-storage-quota` 삭제만이다.

## Command

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase5-testbed.sh
```

## Output

```json
{
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase5_obs_20260528155705",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase5_20260528155705",
  "status": "ok",
  "targets": [
    {
      "target": "cephfs",
      "cluster_name": "cluster-a",
      "namespace_name": "dms-phase5-cephfs-0ba48982",
      "storage_name": "cephfs-a",
      "storage_class_name": "testbed-cephfs",
      "provisioner": "rook-ceph.cephfs.csi.ceph.com",
      "decrease_guard_status": "Rejected",
      "blocked_pvc_rejected": true,
      "drift_check_status": "Drifted",
      "delete_resource_status": "Deleted",
      "non_dms_quota_preserved": true,
      "synced_resource_quota_hard": {
        "persistentvolumeclaims": "5",
        "requests.storage": "384Mi",
        "testbed-cephfs.storageclass.storage.k8s.io/requests.storage": "384Mi"
      }
    },
    {
      "target": "longhorn",
      "cluster_name": "cluster-b",
      "namespace_name": "dms-phase5-longhorn-0ba48982",
      "storage_name": "longhorn-b",
      "storage_class_name": "testbed-longhorn",
      "provisioner": "driver.longhorn.io",
      "decrease_guard_status": "Rejected",
      "blocked_pvc_rejected": true,
      "drift_check_status": "Drifted",
      "delete_resource_status": "Deleted",
      "non_dms_quota_preserved": true,
      "synced_resource_quota_hard": {
        "persistentvolumeclaims": "5",
        "requests.storage": "384Mi",
        "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "384Mi"
      }
    }
  ]
}
```

## PostgreSQL Evidence

DB names are created per run by timestamp:

- operational: `dms_phase5_20260528155705`
- observability: `dms_phase5_obs_20260528155705`

Evidence query result:

```json
{
  "request_status_counts": {
    "Rejected": 2,
    "Succeeded": 16
  },
  "result_status_counts": {
    "Rejected": 2,
    "Succeeded": 16
  },
  "resources": [
    {
      "resource_key": "cluster-a:dms-phase5-cephfs-0ba48982",
      "status": "Deleted",
      "version": 8,
      "desired_quota": {
        "pvc_count": 5,
        "requests_storage_bytes": 402653184
      },
      "desired_hard": {
        "persistentvolumeclaims": "5",
        "requests.storage": "384Mi",
        "testbed-cephfs.storageclass.storage.k8s.io/requests.storage": "384Mi"
      },
      "observed_deleted": true,
      "observed_resource_quota": {
        "cluster_name": "cluster-a",
        "exists": false,
        "name": "dms-storage-quota",
        "namespace": "dms-phase5-cephfs-0ba48982"
      }
    },
    {
      "resource_key": "cluster-b:dms-phase5-longhorn-0ba48982",
      "status": "Deleted",
      "version": 8,
      "desired_quota": {
        "pvc_count": 5,
        "requests_storage_bytes": 402653184
      },
      "desired_hard": {
        "persistentvolumeclaims": "5",
        "requests.storage": "384Mi",
        "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "384Mi"
      },
      "observed_deleted": true,
      "observed_resource_quota": {
        "cluster_name": "cluster-b",
        "exists": false,
        "name": "dms-storage-quota",
        "namespace": "dms-phase5-longhorn-0ba48982"
      }
    }
  ],
  "event_counts": {
    "kubernetes_resourcequota_apply_completed": 2,
    "kubernetes_resourcequota_block_completed": 2,
    "kubernetes_resourcequota_consistency_check_completed": 2,
    "kubernetes_resourcequota_delete_completed": 2,
    "kubernetes_resourcequota_sync_completed": 4,
    "kubernetes_resourcequota_unblock_completed": 2,
    "kubernetes_resourcequota_update_completed": 2,
    "rm_plan_completed": 16
  }
}
```

Interpretation:

- 16 successful requests are the successful lifecycle operations across both targets.
- 2 rejected requests are the intentional quota decrease guard checks, one per target.
- `resources.status=Deleted` means DMS-managed `ResourceQuota/dms-storage-quota` was deleted.
- `desired_quota`와 `desired_hard=384Mi/5 PVC`는 live state sync 결과가 operational PostgreSQL에 보존됐음을 의미한다.
- observability events show started/completed events for apply, update, block, unblock, consistency check, sync, and delete.

## Local Regression

```bash
cd /home/mason/workspace/dms
python3 -m py_compile src/dms/adapters.py src/dms/planner.py src/dms/workers.py src/dms/domain.py src/dms/repositories.py src/dms/api.py src/dms/backends/gpfs.py
/tmp/dms-phase3-venv/bin/python -m pytest -q
```

Output:

```text
30 passed in 15.38s
```
