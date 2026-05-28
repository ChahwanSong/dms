# DMS Phase 6 Verification

Date: 2026-05-28 21:18 +0900

Phase 6 검증은 Kubernetes namespace multi-StorageClass quota와 effective quota warning을 실제 테스트베드에서 확인했다.

## Verified Scope

Longhorn multi-StorageClass target:

- Cluster: `cluster-b`
- Namespace: `dms-phase6-longhorn-multi-12f4436d`
- Storage mappings:
  - `longhorn-b -> cluster-b/testbed-longhorn`
  - `longhorn-static-b -> cluster-b/longhorn-static`

CephFS regression target:

- Cluster: `cluster-a`
- Namespace: `dms-phase6-cephfs-regression-12f4436d`
- Storage mapping:
  - `cephfs-a -> cluster-a/testbed-cephfs`

Verified flow:

1. storage mapping sanity for all three mappings
2. multi-StorageClass `ResourceQuota/dms-storage-quota` create
3. StorageClass-specific `requests.storage` and `persistentvolumeclaims` hard key rendering
4. PVC admission and `status.used` update for both Longhorn StorageClasses
5. per-StorageClass over-quota admission reject
6. multi-entry quota update
7. per-StorageClass decrease guard using live `status.used`
8. block/unblock across all hard keys
9. keyed drift check
10. non-DMS ResourceQuota effective quota warning
11. sync from live state into operational PostgreSQL
12. DMS-managed ResourceQuota delete only, preserving non-DMS ResourceQuota
13. CephFS single-entry lifecycle regression

Namespace deletion was test cleanup only.

## Command

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase6-testbed.sh
```

## Output

```json
{
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase6_obs_20260528211617",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase6_20260528211617",
  "status": "ok",
  "targets": [
    {
      "target": "longhorn-multi-storageclass",
      "cluster_name": "cluster-b",
      "namespace_name": "dms-phase6-longhorn-multi-12f4436d",
      "storage_names": ["longhorn-b", "longhorn-static-b"],
      "storage_class_names": ["testbed-longhorn", "longhorn-static"],
      "decrease_guard_status": "Rejected",
      "blocked_pvc_rejected": true,
      "drift_check_status": "Drifted",
      "effective_warning_types": ["non_dms_quota_more_restrictive"],
      "delete_resource_status": "Deleted",
      "non_dms_quota_preserved": true,
      "synced_resource_quota_hard": {
        "longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims": "4",
        "longhorn-static.storageclass.storage.k8s.io/requests.storage": "256Mi",
        "persistentvolumeclaims": "8",
        "requests.storage": "768Mi",
        "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "4",
        "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "512Mi"
      }
    },
    {
      "target": "cephfs-single-storageclass-regression",
      "cluster_name": "cluster-a",
      "namespace_name": "dms-phase6-cephfs-regression-12f4436d",
      "storage_name": "cephfs-a",
      "storage_class_name": "testbed-cephfs",
      "check_status": "Consistent",
      "delete_resource_status": "Deleted"
    }
  ]
}
```

## PostgreSQL Evidence

DB names:

- operational: `dms_phase6_20260528211617`
- observability: `dms_phase6_obs_20260528211617`

Evidence summary:

```json
{
  "request_status_counts": {
    "Rejected": 1,
    "Succeeded": 12
  },
  "result_status_counts": {
    "Rejected": 1,
    "Succeeded": 12
  },
  "resource_status": {
    "cluster-a:dms-phase6-cephfs-regression-12f4436d": "Deleted",
    "cluster-b:dms-phase6-longhorn-multi-12f4436d": "Deleted"
  },
  "longhorn_multi_desired_hard": {
    "longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims": "4",
    "longhorn-static.storageclass.storage.k8s.io/requests.storage": "256Mi",
    "persistentvolumeclaims": "8",
    "requests.storage": "768Mi",
    "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "4",
    "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "512Mi"
  },
  "event_counts": {
    "kubernetes_resourcequota_apply_completed": 2,
    "kubernetes_resourcequota_update_completed": 2,
    "kubernetes_resourcequota_block_completed": 1,
    "kubernetes_resourcequota_unblock_completed": 1,
    "kubernetes_resourcequota_consistency_check_completed": 2,
    "kubernetes_resourcequota_sync_completed": 2,
    "kubernetes_resourcequota_delete_completed": 2,
    "rm_plan_completed": 12
  }
}
```

Check/sync evidence for effective quota warning:

```json
[
  {
    "request_id": "req_828b47254e1f4e9788c971cba363bb98",
    "terminal_status": "Succeeded",
    "consistency_status": "Drifted",
    "issues": [
      {
        "field": "spec.hard",
        "key": "testbed-longhorn.storageclass.storage.k8s.io/requests.storage",
        "reason": "hard_limit_drifted",
        "desired": "384Mi",
        "live": "512Mi"
      }
    ],
    "effective_quota_warnings": [
      {
        "type": "non_dms_quota_more_restrictive",
        "resource_quota_name": "phase6-non-dms-quota",
        "key": "testbed-longhorn.storageclass.storage.k8s.io/requests.storage",
        "dms_hard": "384Mi",
        "non_dms_hard": "128Mi"
      }
    ]
  },
  {
    "request_id": "req_adaa0d9c29ef4818a1e0f5532b2ae93b",
    "terminal_status": "Succeeded",
    "sync_warnings": [],
    "effective_quota_warnings": [
      {
        "type": "non_dms_quota_more_restrictive",
        "resource_quota_name": "phase6-non-dms-quota",
        "key": "testbed-longhorn.storageclass.storage.k8s.io/requests.storage",
        "dms_hard": "512Mi",
        "non_dms_hard": "128Mi"
      }
    ]
  }
]
```

## Local Regression

```bash
cd /home/mason/workspace/dms
python3 -m py_compile src/dms/adapters.py src/dms/planner.py scripts/phase6_kubernetes_multi_storage_quota.py
/tmp/dms-phase3-venv/bin/python -m pytest -q
```

Output:

```text
37 passed in 17.11s
```
