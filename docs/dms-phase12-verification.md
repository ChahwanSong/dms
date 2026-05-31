# DMS Phase 12 Verification

Date: 2026-05-31 09:07 +0900

Phase 12 verifies filesystem quota lifecycle and existing directory import/assign-quota on host-mounted CephFS. The verifier uses PostgreSQL-backed DMS state, actual DMS Agent reports, OpenLDAP/SSSD identity propagation, and worker-node host-mounted CephFS on both clusters.

## Local Regression

Command:

```bash
cd /home/mason/workspace/dms
pytest -q
```

Output:

```text
79 passed in 51.76s
```

Focused Phase 12 coverage added:

- create with finite filesystem quota plans backend-neutral desired quota
- invalid/too-large quota values are rejected before backend side effect
- CephFS adapter applies and records `ceph.quota.max_bytes` and `ceph.quota.max_files`
- quota update reads live usage before applying decrease
- quota decrease below DB-observed or live usage is rejected before quota mutation
- check reports consistent, drifted, missing, and usage-pressure states
- sync accepts live CephFS quota into DB desired/applied/observed state without xattr mutation
- action-required includes quota drift and usage pressure, and drift is resolved by sync
- assign-quota writes a `management_mode=quota_only` marker and refuses backend directory delete
- full import records access policy, quota, marker, and access validation state
- filesystem sync endpoint persists `filesystem.sync` requests

## Testbed Live Verification

The final testbed run used the Phase 12 wrapper to deploy/reuse the DMS API and RM Agent DaemonSets, prepare fresh PostgreSQL DBs, and run the Phase 10 baseline. After tightening the file-count assertion, the Phase 12 verifier was rerun against the same fresh Phase 12 DB using the testbed Python venv.

Commands:

```bash
cd /home/mason/workspace/dms
DMS_PHASE12_SKIP_IMAGE_BUILD=1 scripts/verify-phase12-testbed.sh

python_bin=/tmp/dms-phase3-venv/bin/python3
# same DMS_* environment reconstructed for dms_phase12_20260531090258
"${python_bin}" scripts/phase12_cephfs_quota_import.py
```

Final Phase 12 DBs:

```text
operational:   postgresql://appuser:***@192.168.56.11:30432/dms_phase12_20260531090258
observability: postgresql://appuser:***@192.168.56.11:30432/dms_phase12_obs_20260531090258
```

Host mount and quota capability evidence:

```json
{
  "host_mounts": [
    {
      "cluster_name": "cluster-a",
      "node_name": "c1-worker",
      "mount_path": "/mnt/testbed-cephfs",
      "statfs_type": "ceph",
      "storage_name": "cephfs-a"
    },
    {
      "cluster_name": "cluster-b",
      "node_name": "c2-worker",
      "mount_path": "/mnt/testbed-cephfs-c2",
      "statfs_type": "ceph",
      "storage_name": "cephfs-b"
    }
  ],
  "quota_probe": [
    {
      "storage_name": "cephfs-a",
      "node_name": "c1-worker",
      "supports_capacity_quota": true,
      "supports_file_count_quota": true,
      "quota_backend": "cephfs-xattr",
      "probe_capacity_bytes": 1048576,
      "probe_file_count": 8
    },
    {
      "storage_name": "cephfs-b",
      "node_name": "c2-worker",
      "supports_capacity_quota": true,
      "supports_file_count_quota": true,
      "quota_backend": "cephfs-xattr",
      "probe_capacity_bytes": 1048576,
      "probe_file_count": 8
    }
  ],
  "quota_tools": [
    {
      "storage_name": "cephfs-a",
      "node_name": "c1-worker",
      "setfattr": "/usr/bin/setfattr",
      "getfattr": "/usr/bin/getfattr",
      "installed_attr_package": false
    },
    {
      "storage_name": "cephfs-b",
      "node_name": "c2-worker",
      "setfattr": "/usr/bin/setfattr",
      "getfattr": "/usr/bin/getfattr",
      "installed_attr_package": false
    }
  ]
}
```

Agent and storage mapping evidence:

```json
{
  "agent_reports": {
    "cluster-a:c1-worker:cephfs-a": {
      "report_id": "agent_873e3dce743147d9b2b0281e7ca032a1",
      "mount_path": "/mnt/testbed-cephfs",
      "filesystem_type": "ceph",
      "readable": true,
      "writable": false
    },
    "cluster-b:c2-worker:cephfs-b": {
      "report_id": "agent_1e5ed27c4b8349828417964c749a5f85",
      "mount_path": "/mnt/testbed-cephfs-c2",
      "filesystem_type": "ceph",
      "readable": true,
      "writable": false
    }
  },
  "storage_mappings": [
    {
      "storage_name": "cephfs-a",
      "status": "Degraded",
      "readiness": {
        "resource_management": "Ready",
        "data_management": "Missing",
        "inventory": "Ready"
      },
      "sanity_errors": []
    },
    {
      "storage_name": "cephfs-b",
      "status": "Degraded",
      "readiness": {
        "resource_management": "Ready",
        "data_management": "Missing",
        "inventory": "Ready"
      },
      "sanity_errors": []
    }
  ]
}
```

Quota lifecycle evidence:

```json
{
  "quota_lifecycle": [
    {
      "storage_name": "cephfs-a",
      "cluster_name": "cluster-a",
      "node_name": "c1-worker",
      "directory_name": "phase12-quota-a-c16c1d2b",
      "create_request_id": "req_8d2236d5047c46cc8445780ab90b8696",
      "update_request_id": "req_569dd32578af4c8596b9142a5eb673f7",
      "check_request_id": "req_d91e21d0e6da4e08a7d3044dd3fed953",
      "decrease_request_id": "req_68227dc237a1433aa38d432eb53c91fb",
      "drift_check_request_id": "req_f909e4e7a3d44b31bf1622a8ede75fcf",
      "sync_request_id": "req_641548ee07f74d6685c101a96ceb9b45",
      "usage_warning_request_id": "req_4e5b8073b7c24637aa921d27e16d6ef5",
      "delete_request_id": "req_b7915e7bed884873bffff79f08666f00",
      "capacity_failure": "Disk quota exceeded",
      "file_count_failure": "file-29: Disk quota exceeded",
      "synced_capacity_bytes": 14680064
    },
    {
      "storage_name": "cephfs-b",
      "cluster_name": "cluster-b",
      "node_name": "c2-worker",
      "directory_name": "phase12-quota-b-c16c1d2b",
      "create_request_id": "req_f98c8a88df704437a28802e7c41f035d",
      "update_request_id": "req_4bd37ee8adab4688a94b67a3e130c501",
      "check_request_id": "req_6805575258b74efe819fd41050c70421",
      "decrease_request_id": "req_3db814d46a58444d883dcdf960b9f3f7",
      "drift_check_request_id": "req_a08f641765cf4e6eac5993d7e7a6b0c6",
      "sync_request_id": "req_3e96c526fb0b49b78501c4487247a7ba",
      "usage_warning_request_id": "req_0a31f5eb5d024ee288189f43cb595c44",
      "delete_request_id": "req_4d733e785cc2449e93ae19b0e62838d5",
      "capacity_failure": "Disk quota exceeded",
      "file_count_failure": "file-29: Disk quota exceeded",
      "synced_capacity_bytes": 14680064
    }
  ]
}
```

Import and assign-quota evidence:

```json
{
  "assign_quota": {
    "storage_name": "cephfs-a",
    "directory_name": "phase12-assign-c16c1d2b",
    "assign_request_id": "req_1e35a3aae1844ccaa07b808ad6d1a2e9",
    "check_request_id": "req_4786036fc05f4d25a066cba8f92714b6",
    "delete_rejected_request_id": "req_751da0086e314ef1a63f6ea4c581a3ba",
    "marker_management_mode": "quota_only"
  },
  "full_import": {
    "storage_name": "cephfs-b",
    "directory_name": "phase12-import-c16c1d2b",
    "group_name": "dms-phase12-import-c16c1d2b",
    "group_gid": 24003,
    "import_request_id": "req_3a8c3969ba424a2aa21453916869cc35",
    "quota_update_request_id": "req_71a9fd738b5d4b2fbc140eceacf7ff84",
    "delete_request_id": "req_c2c1477466764383b74fc60a46350824"
  },
  "unsafe_case": {
    "request_id": "req_b0eb9201ad7a451ab46f54954fea89e0",
    "reasons": [
      "access_policy.users_required",
      "directory_name_invalid",
      "filesystem_access_group_required",
      "filesystem_access_policy_required"
    ]
  }
}
```

## Verified Behavior

- `cluster-a/c1-worker` and `cluster-b/c2-worker` host-mounted CephFS both support CephFS directory quota xattrs.
- The final successful run observed `setfattr` and `getfattr` present on both worker nodes.
- During an earlier verifier attempt, the script installed Debian `attr` on `c1-worker` because `setfattr`/`getfattr` were missing there. This was recorded in `/home/mason/workspace/testbed/dms-phase12-testbed-notes.md`. The final successful run observed both tools already present on both workers.
- Filesystem create with quota applied `ceph.quota.max_bytes` and `ceph.quota.max_files` on both CephFS targets.
- Allowed LDAP user wrote within quota; denied LDAP fixture was denied access.
- Capacity quota overage failed with `Disk quota exceeded` on both targets.
- File-count quota overage failed with `Disk quota exceeded` on both targets.
- Quota increase updated CephFS xattrs and DB desired/applied/observed state.
- Quota decrease below observed/live usage was rejected without changing live xattrs.
- Consistency check reported `Consistent` before manual drift.
- Manual CephFS xattr drift was detected and surfaced in action-required as `filesystem_quota_drifted`.
- Sync accepted live quota into DB and resolved the drift issue.
- Usage threshold check surfaced `filesystem_quota_usage_warning`.
- Existing unmanaged directory assign-quota wrote a `quota_only` marker and quota xattrs.
- DMS delete for quota-only resource was rejected and the backend directory remained until test cleanup.
- Full import used an existing OpenLDAP/SSSD group with `alice` and `bob`, verified denied-user isolation, wrote marker/quota state, then accepted a quota update.
- Unsafe nested path import was rejected at planning.

## Cleanup Evidence

- The wrapper deleted the temporary `dms-phase12` namespace in both clusters.
- The final Phase 12 verifier deleted created DMS directories and DMS-created LDAP fixtures during its `finally` cleanup.
- Testbed package note exists for the earlier `c1-worker` `attr` installation: `/home/mason/workspace/testbed/dms-phase12-testbed-notes.md`.

## Notes

- Phase 12 still uses `RMWorkerRuntime.run_once()` from the verifier process. Long-running RM Worker Kubernetes Deployment/loop verification remains a later phase.
- Agent mount evidence reports `writable=false` from the containerized agent. Backend mutation is still performed by SSH/sudo on the worker host, which is expected for Phase 10-12.
- Storage mappings are `Degraded` only because DM readiness is not provided for these filesystem targets. RM readiness is `Ready`, which is the required guard for filesystem Resource Management.
- Phase 12 does not implement automatic quota cron/controller behavior, Data Management live execution, VolcanoJob execution, or GPFS/WekaFS/Lustre live adapters.
