# DMS Phase 7 Verification

Date: 2026-05-28 22:35 +0900

Phase 7 검증은 requester-scoped request query, Kubernetes namespace quota dedicated query API, blocked 상태 quota update semantics를 실제 테스트베드 PostgreSQL/Kubernetes에서 확인했다.

## Local Regression

Command:

```bash
cd /home/mason/workspace/dms
/tmp/dms-phase3-venv/bin/python -m pytest -q
```

Output:

```text
46 passed in 34.24s
```

Targeted regression:

```bash
/tmp/dms-phase3-venv/bin/python -m pytest -q \
  tests/test_phase1_contracts.py \
  tests/test_phase5_kubernetes_quota_lifecycle.py \
  tests/test_phase6_kubernetes_multi_storage_quota.py \
  tests/test_phase7_operational_queries.py
```

Output:

```text
29 passed in 25.84s
```

Static checks:

```bash
python3 -m py_compile \
  src/dms/api.py \
  src/dms/query.py \
  src/dms/repositories.py \
  src/dms/planner.py \
  src/dms/adapters.py \
  scripts/phase7_operational_query_and_block_update.py \
  scripts/phase2_postgres_ldap_smoke.py
git diff --check
```

Both checks passed.

## Testbed Metadata

Checked before live verification:

- `/home/mason/workspace/testbed/testbed-summary.json`
- `/home/mason/workspace/testbed/testbed-info.json`

Relevant live StorageClasses:

```text
cluster-a:
testbed-cephfs             rook-ceph.cephfs.csi.ceph.com
testbed-postgresql-local   kubernetes.io/no-provisioner

cluster-b:
longhorn-static            driver.longhorn.io
testbed-longhorn           driver.longhorn.io
testbed-longhorn-retain    driver.longhorn.io
```

## Live Verification

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase7-testbed.sh
```

Output:

```json
{
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase7_obs_20260528223441",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase7_20260528223441",
  "request_query": {
    "created_request_ids": [
      "req_ff0e252c418747ceb99d1772c92e6789",
      "req_56b861dfedd84d8f93d05a9c13196e0b",
      "req_8dfb6691337a4db7bc8c5082858fab2b"
    ],
    "limited_resource_keys": [
      "phase7-a:cf012d71:2",
      "phase7-a:cf012d71:1"
    ],
    "missing_requester_status": 422,
    "requester_id": "portal:phase7-a"
  },
  "status": "ok",
  "targets": [
    {
      "block_request_id": "req_ef9d651e768f4be98117581caf9d4986",
      "blocked_decrease_request_id": "req_1370bf27122a42138fb360d0f8c4f684",
      "blocked_decrease_status": "Rejected",
      "blocked_query_restore_hard": {
        "longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims": "5",
        "longhorn-static.storageclass.storage.k8s.io/requests.storage": "384Mi",
        "persistentvolumeclaims": "10",
        "requests.storage": "1Gi",
        "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "5",
        "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "512Mi"
      },
      "blocked_update_request_id": "req_8670fe26824b47fca7c0644084c88424",
      "cluster_name": "cluster-b",
      "create_request_id": "req_80e83cecc05e452bbd9361225ca6c653",
      "delete_request_id": "req_d12277c5983e42e59d50204f2c29b590",
      "drift_query_status": "Drifted",
      "effective_warning_types": [
        "non_dms_quota_more_restrictive"
      ],
      "initial_query_status": "Consistent",
      "missing_query_status": "Missing",
      "namespace_name": "dms-phase7-longhorn-cf012d71",
      "non_dms_quota_preserved": true,
      "sync_request_id": "req_0f91aead3a0b432c9a5bf3bd5f014a42",
      "target": "longhorn-query-blocked-update",
      "unblock_request_id": "req_9d8ad8f92b7049f29abc9ccca8f66f25",
      "update_request_id": "req_0cd05392339d4e17afe417ed9205e6e7"
    },
    {
      "cluster_name": "cluster-a",
      "create_request_id": "req_fa8d6bd918dd4bee859df91ffec09804",
      "delete_request_id": "req_c01a666fcd394f67b289557e7137f2ec",
      "namespace_name": "dms-phase7-cephfs-cf012d71",
      "query_status": "Consistent",
      "target": "cephfs-quota-query"
    }
  ]
}
```

Verified behavior:

- `GET /api/v1/operations/requests` rejects missing `requester_id` with `422`.
- Request list query returns only `portal:phase7-a` requests and honors `limit`.
- Dedicated quota query returns `Consistent` for live DB/Kubernetes state.
- Blocked update keeps live hard limits at `0` and updates `block_state.restore_hard`.
- Blocked decrease guard rejects restore target below live `status.used`.
- Unblock restores the blocked-update target, not the older pre-block hard limit.
- Manual drift is reported as `Drifted`.
- non-DMS `ResourceQuota` is reported as an effective quota warning and is preserved on DMS delete.
- DMS delete removes only `ResourceQuota/dms-storage-quota`.
- CephFS single StorageClass quota query returns `Consistent`.

## PostgreSQL Evidence

Operational DB:

```text
dms_phase7_20260528223441
```

Observability DB:

```text
dms_phase7_obs_20260528223441
```

Direct evidence query summary:

```json
{
  "request_status_counts": [
    {"status": "Rejected", "count": 1},
    {"status": "Succeeded", "count": 15}
  ],
  "result_status_counts": [
    {"terminal_status": "Rejected", "count": 1},
    {"terminal_status": "Succeeded", "count": 15}
  ],
  "requester_counts": [
    {"requester_id": "portal:phase7-a", "count": 13},
    {"requester_id": "portal:phase7-b", "count": 3}
  ],
  "resources": [
    {
      "resource_key": "cluster-a:dms-phase7-cephfs-cf012d71",
      "status": "Deleted",
      "version": 2
    },
    {
      "resource_key": "cluster-b:dms-phase7-longhorn-cf012d71",
      "status": "Deleted",
      "version": 7,
      "restored_hard": {
        "longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims": "5",
        "longhorn-static.storageclass.storage.k8s.io/requests.storage": "384Mi",
        "persistentvolumeclaims": "10",
        "requests.storage": "1Gi",
        "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "5",
        "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "512Mi"
      }
    }
  ],
  "event_counts": {
    "agent_report_accepted": 3,
    "kubernetes_resourcequota_apply_completed": 2,
    "kubernetes_resourcequota_update_completed": 2,
    "kubernetes_resourcequota_block_completed": 1,
    "kubernetes_resourcequota_unblock_completed": 1,
    "kubernetes_resourcequota_sync_completed": 1,
    "kubernetes_resourcequota_delete_completed": 2,
    "rm_plan_completed": 9
  }
}
```

The verification namespaces were requested for cleanup by the script.
