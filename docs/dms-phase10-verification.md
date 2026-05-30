# DMS Phase 10 Verification

Date: 2026-05-30 21:28 +0900

Phase 10 verifies the filesystem Resource Management create/delete minimum lifecycle on host-mounted Ceph filesystems. This is not a Kubernetes PVC-in-pod directory quota test. The verifier uses worker-node host mounts, OpenLDAP/SSSD identity propagation, actual DMS Agent reports, PostgreSQL-backed request/plan/result state, and `RMWorkerRuntime.run_once()` with the live CephFS host adapter.

## Local Regression

Command:

```bash
cd /home/mason/workspace/dms
pytest
```

Output:

```text
62 passed in 40.94s
```

Focused Phase 10 coverage added:

- filesystem create plans `access_group=dms-phase10-<directory>` and default `mode=0770`
- create rejects Phase 10 unsupported quota payload
- create requires at least two unique users
- create rejects an already active DB resource
- filesystem update is rejected until a later phase
- delete reuses existing desired state for safe cleanup
- CephFS adapter creates LDAP group membership before host directory mutation
- missing LDAP user is recorded as `BackendApplyFailed` before filesystem side effect

## Testbed Live Verification

Command:

```bash
cd /home/mason/workspace/dms
DMS_PHASE10_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase10-testbed.sh
```

The first run built and pushed `192.168.56.11:5000/dms:phase10`. As in earlier phases, Docker tried HTTPS against the testbed HTTP registry; the script fell back to `docker save` plus `skopeo copy --dest-tls-verify=false` on `c1-control`. The final verification run reused that image.

Host mount evidence:

```text
c1-worker:
TARGET              SOURCE               FSTYPE
/mnt/testbed-cephfs 10.111.39.236:6789:/ ceph
statfs: ceph

c2-worker:
TARGET                 SOURCE               FSTYPE
/mnt/testbed-cephfs-c2 192.168.56.22:6789:/ ceph
statfs: ceph

OpenLDAP slapd: active
c1-worker SSSD: active
c2-worker SSSD: active
```

Deployment evidence:

```text
deployment "dms-api" successfully rolled out
daemon set "dms-rm-agent" successfully rolled out
daemon set "dms-rm-agent" successfully rolled out

cluster-a:
dms-api-6cd8bb8b4f-ptjbl   1/1 Running c1-control
dms-rm-agent-hgp5b         1/1 Running c1-worker

cluster-b:
dms-rm-agent-fbwv4         1/1 Running c2-worker
```

Live verification summary:

```json
{
  "status": "ok",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase10_20260530213231",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase10_obs_20260530213231",
  "agent_reports": {
    "cluster-a:c1-worker:cephfs-a": {
      "report_id": "agent_fd9ba665c8034cdf9627b374d1703295",
      "mount_path": "/mnt/testbed-cephfs",
      "filesystem_type": "ceph",
      "readable": true
    },
    "cluster-b:c2-worker:cephfs-b": {
      "report_id": "agent_29b2f203817947d79d56e4e5abb54d36",
      "mount_path": "/mnt/testbed-cephfs-c2",
      "filesystem_type": "ceph",
      "readable": true
    }
  },
  "ldap_users": {
    "allowed_users": [
      "alice",
      "bob"
    ],
    "denied_user": "dms-phase10-denied-9ee3124c",
    "created_users": [
      "dms-phase10-denied-9ee3124c"
    ]
  },
  "targets": [
    {
      "storage_name": "cephfs-a",
      "cluster_name": "cluster-a",
      "node_name": "c1-worker",
      "directory_path": "/mnt/testbed-cephfs/dms-phase10/phase10-a-9ee3124c",
      "group_name": "dms-phase10-phase10-a-9ee3124c",
      "create_request_id": "req_a8e0e0cebae94ff0a9b741ee08f788d3",
      "delete_request_id": "req_41a7690943bc457abdc2f6e2b0be3937",
      "stat": "root dms-phase10-phase10-a-9ee3124c 770 /mnt/testbed-cephfs/dms-phase10/phase10-a-9ee3124c"
    },
    {
      "storage_name": "cephfs-b",
      "cluster_name": "cluster-b",
      "node_name": "c2-worker",
      "directory_path": "/mnt/testbed-cephfs-c2/dms-phase10/phase10-b-9ee3124c",
      "group_name": "dms-phase10-phase10-b-9ee3124c",
      "create_request_id": "req_271604ce9a8a4aea97fa58d2af4bb86f",
      "delete_request_id": "req_5e53b852b3cf44f1ae6ab5946fe6c3c3",
      "stat": "root dms-phase10-phase10-b-9ee3124c 770 /mnt/testbed-cephfs-c2/dms-phase10/phase10-b-9ee3124c"
    }
  ]
}
```

## Verified Behavior

- `cephfs-a` used `cluster-a/c1-worker` host mount `/mnt/testbed-cephfs`.
- `cephfs-b` used `cluster-b/c2-worker` host mount `/mnt/testbed-cephfs-c2`.
- Storage mapping readiness came from actual Agent reports. No synthetic Agent report was submitted by the verifier.
- The c1 and c2 DMS Agent Pods mounted the worker-node Ceph host path through `hostPath` only for evidence collection.
- The filesystem backend side effect was executed over SSH on the target worker node, not inside the API Pod and not inside an application PVC.
- The verifier used seeded LDAP users `alice` and `bob` as allowed users and created one DMS phase-scoped denied-user fixture for negative access validation.
- The DMS create flow created DMS-managed LDAP `posixGroup` objects and added only the allowed users as `memberUid`.
- SSSD/NSS propagation was verified on `c1-worker` and `c2-worker` with `getent`/`id`.
- Allowed users could create and remove a tiny file in the managed directory.
- The denied user could not execute or write the managed directory.
- Each directory had `.dms-resource.json`, owner `root`, DMS access group ownership, and mode `0770`.
- Delete removed only the DMS-owned test directory and recorded the resource status as `Deleted`.

## Cleanup Evidence

After verification, the script deleted the temporary Kubernetes namespace in both clusters. It also deleted the DMS phase-scoped denied-user LDAP fixture. A stale LDAP group left by an earlier failed verifier run was manually removed before the final verification run.

Cleanup checks:

```bash
ssh ldap "ldapsearch -x -LLL -H ldap://127.0.0.1 -D cn=admin,dc=testbed,dc=local -w testbed-admin -b ou=people,dc=testbed,dc=local '(uid=dms-phase10-*)' uid"
ssh ldap "ldapsearch -x -LLL -H ldap://127.0.0.1 -D cn=admin,dc=testbed,dc=local -w testbed-admin -b ou=groups,dc=testbed,dc=local '(cn=dms-phase10-*)' cn memberUid"
ssh c1-worker 'find /mnt/testbed-cephfs/dms-phase10 -maxdepth 1 -name "phase10-*" -print'
ssh c2-worker 'find /mnt/testbed-cephfs-c2/dms-phase10 -maxdepth 1 -name "phase10-*" -print'
```

Output:

```text
no dms-phase10 LDAP users
no dms-phase10 LDAP groups
no c1 phase10 directories
no c2 phase10 directories
```

## Notes

- Agent mount evidence reported `readable=true` and `writable=false` from the container user. This is acceptable for Phase 10 because the actual filesystem mutation is performed by the RM backend through `sudo` on the worker host, and host write probes succeeded before API verification.
- Mapping status was `Degraded` because no DM Agent readiness was provided for these filesystem targets. RM readiness was `Ready`, which is the required guard for Phase 10 create/delete.
- Phase 10 still does not implement filesystem quota, update, block/unblock, consistency check/sync, expiry sweep, or long-running RM Worker Kubernetes Deployment verification.
