# DMS Phase 12 Verification

Date: 2026-05-31 13:16 +0900

Phase 12 verifies filesystem quota lifecycle and existing directory import/assign-quota on host-mounted CephFS. The verifier uses PostgreSQL-backed DMS state, actual DMS Agent reports, OpenLDAP/SSSD identity propagation, and worker-node host-mounted CephFS on both clusters.

## Local Regression

Command:

```bash
cd /home/mason/workspace/dms
pytest -q
```

Output:

```text
80 passed in 50.83s
```

Focused Phase 12 coverage added:

- create with finite filesystem quota plans backend-neutral desired quota
- invalid/too-large quota values are rejected before backend side effect
- CephFS adapter applies and records `ceph.quota.max_bytes` and `ceph.quota.max_files`
- quota update applies decreases without reading usage before mutation
- quota decrease is verified by live CephFS xattr read-back after mutation
- check reports consistent, drifted, and missing states
- check/sync reject unsupported filesystem usage payload fields
- sync accepts live CephFS quota into DB desired/applied/observed state without xattr mutation
- action-required includes quota drift, and drift is resolved by sync
- assign-quota writes a `management_mode=quota_only` marker and refuses backend directory delete
- full import records access policy, quota, marker, and access validation state
- filesystem sync endpoint persists `filesystem.sync` requests

## Testbed Live Verification

The final testbed run used the Phase 12 wrapper to deploy/reuse the DMS API and RM Agent DaemonSets, prepare fresh PostgreSQL DBs, run the Phase 10 baseline, then run the updated Phase 12 quota/import verifier. This run validates the filesystem quota decrease policy change: decrease requests are applied without usage admission and verified by CephFS xattr read-back. It also validates the verifier check/sync payloads without filesystem usage collection fields.

Commands:

```bash
cd /home/mason/workspace/dms
DMS_PHASE12_SKIP_IMAGE_BUILD=1 scripts/verify-phase12-testbed.sh
```

Final Phase 12 DBs:

```text
operational:   postgresql://appuser:***@192.168.56.11:30432/dms_phase12_20260531131324
observability: postgresql://appuser:***@192.168.56.11:30432/dms_phase12_obs_20260531131324
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
      "report_id": "agent_ae1249eba59247e0853b9d24e168d44e",
      "mount_path": "/mnt/testbed-cephfs",
      "filesystem_type": "ceph",
      "readable": true,
      "writable": false
    },
    "cluster-b:c2-worker:cephfs-b": {
      "report_id": "agent_13955305b0524c38a7b1722d82938594",
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
      "directory_name": "phase12-quota-a-21c0bace",
      "create_request_id": "req_a02cce8ea1c941dc8fffcb58cea56e9d",
      "update_request_id": "req_b6822657c0c74b6dabd5c6a5611ba764",
      "check_request_id": "req_5a05f38596274dbe8bf8a08427247c56",
      "decrease_request_id": "req_8c6380a107524f1a8ddeebdc8d59d346",
      "drift_check_request_id": "req_12f1be3d32a842268e84d7d18962c747",
      "sync_request_id": "req_64c7e7ee43bf4477bc4cc838b806d5f1",
      "delete_request_id": "req_45a359a1d00347a585f24359523071e7",
      "capacity_failure": {
        "returncode": 1,
        "stderr": "dd: error writing '/mnt/testbed-cephfs/dms-phase10/phase12-quota-a-21c0bace/over-capacity.bin': Disk quota exceeded"
      },
      "file_count_failure": {
        "returncode": 9,
        "stderr": "bash: line 1: /mnt/testbed-cephfs/dms-phase10/phase12-quota-a-21c0bace/file-29: Disk quota exceeded"
      },
      "synced_capacity_bytes": 14680064
    },
    {
      "storage_name": "cephfs-b",
      "cluster_name": "cluster-b",
      "node_name": "c2-worker",
      "directory_name": "phase12-quota-b-21c0bace",
      "create_request_id": "req_ab42afebd2f04c0baa5c91e68fda8d33",
      "update_request_id": "req_8da697955fce4aecb615ffbf5b9e0979",
      "check_request_id": "req_653517b0c7144048a1435891bbecbba2",
      "decrease_request_id": "req_bf1110d21dfa4cea9101e61c6468e7d9",
      "drift_check_request_id": "req_9888486d2d474fd29454fe6b1886265b",
      "sync_request_id": "req_3a43473c6b7c42aa8379e7e3106ad5b9",
      "delete_request_id": "req_4899988c8bd14e098d828efb11564a46",
      "capacity_failure": {
        "returncode": 1,
        "stderr": "dd: error writing '/mnt/testbed-cephfs-c2/dms-phase10/phase12-quota-b-21c0bace/over-capacity.bin': Disk quota exceeded"
      },
      "file_count_failure": {
        "returncode": 9,
        "stderr": "bash: line 1: /mnt/testbed-cephfs-c2/dms-phase10/phase12-quota-b-21c0bace/file-29: Disk quota exceeded"
      },
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
    "directory_name": "phase12-assign-21c0bace",
    "assign_request_id": "req_808df586e89945e8bd25efb9fa26aa81",
    "check_request_id": "req_b056114b7ff942f3aa769be331533162",
    "delete_rejected_request_id": "req_1d4dcd54b3704902ac6428d0bd0dc4ef",
    "marker_management_mode": "quota_only"
  },
  "full_import": {
    "storage_name": "cephfs-b",
    "directory_name": "phase12-import-21c0bace",
    "group_name": "dms-phase12-import-21c0bace",
    "group_gid": 24003,
    "import_request_id": "req_237ba2f4ecfe48b3913528d801dca29c",
    "quota_update_request_id": "req_50f19aafa8b24b90a727825815fe240f",
    "delete_request_id": "req_49b73e393d2c4e35bef4969c816964fd"
  },
  "unsafe_case": {
    "request_id": "req_1313e29ba3ef415cada7dd313f74b558",
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
- Quota decrease was applied without usage admission and verified by xattr read-back.
- Consistency check reported `Consistent` before manual drift.
- Manual CephFS xattr drift was detected and surfaced in action-required as `filesystem_quota_drifted`.
- Sync accepted live quota into DB and resolved the drift issue.
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
- Phase 12 filesystem quota apply/check/sync verification does not perform recursive usage scans or usage-pressure calculation.
- Phase 12 does not implement automatic quota cron/controller behavior, Data Management live execution, VolcanoJob execution, or GPFS/WekaFS/Lustre live adapters.
