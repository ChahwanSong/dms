# DMS Phase 11 Verification

Date: 2026-05-30 22:55 +0900

Phase 11 verifies filesystem Resource Management expiry query, API-driven expiration sweep, and block/unblock lifecycle on host-mounted Ceph filesystems. The verifier uses PostgreSQL-backed DMS state, actual DMS Agent reports, OpenLDAP/SSSD identity propagation, and worker-node host-mounted CephFS on both clusters.

## Local Regression

Command:

```bash
cd /home/mason/workspace/dms
pytest -q
```

Output:

```text
67 passed in 44.10s
```

Focused Phase 11 coverage added:

- expired filesystem query filters by `expires_at`
- expired unblocked filesystem resources surface in action-required
- expiration sweep dry-run records targets without backend side effect
- sweep blocks user resources and skips `system` resources
- filesystem block stores restore mode/group state and marks DB resource `Blocked`
- filesystem unblock restores mode/group state, clears block state, and preserves `expires_at`
- unblock without restore state is rejected before backend side effect

## Testbed Live Verification

Command:

```bash
cd /home/mason/workspace/dms
DMS_PHASE11_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase11-testbed.sh
```

The first Phase 11 run built `192.168.56.11:5000/dms:phase11`. Docker push again attempted HTTPS against the testbed HTTP registry, so the script used the existing `docker save` plus `skopeo copy --dest-tls-verify=false` fallback. During that run the fallback path still pushed the hard-coded `dms:phase10` tag; this was fixed so the fallback now pushes the configured image tag. The final verification reused the `phase11` image with `DMS_PHASE11_SKIP_IMAGE_BUILD=1`.

An additional wrapper issue was fixed during verification: Phase 11 now reconstructs `DMS_DATABASE_URL`, `DMS_OBSERVABILITY_DATABASE_URL`, LDAP, Kubernetes inventory, and filesystem execution environment in the wrapper before running the Phase 11 Python verifier. This is needed because `verify-phase10-testbed.sh` runs as a child process and its exported environment does not propagate back to the Phase 11 wrapper.

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
dms-api-66844c8769-84rhv  1/1 Running c1-control
dms-rm-agent-vkvw5        1/1 Running c1-worker

cluster-b:
dms-rm-agent-lxjn5        1/1 Running c2-worker
```

Live verification summary:

```json
{
  "status": "ok",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase11_20260530225127",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase11_obs_20260530225127",
  "agent_reports": {
    "cluster-a:c1-worker:cephfs-a": {
      "report_id": "agent_fdfd23d0fd40428e95f1d31c5999a864",
      "mount_path": "/mnt/testbed-cephfs",
      "filesystem_type": "ceph",
      "readable": true,
      "writable": false
    },
    "cluster-b:c2-worker:cephfs-b": {
      "report_id": "agent_98bdf39e633c43aeac18b2b9706567a3",
      "mount_path": "/mnt/testbed-cephfs-c2",
      "filesystem_type": "ceph",
      "readable": true,
      "writable": false
    }
  },
  "ldap_users": {
    "allowed_users": ["alice", "bob"],
    "denied_user": "dms-phase11-denied-f06c4928",
    "created_users": ["dms-phase11-denied-f06c4928"]
  },
  "targets": [
    {
      "storage_name": "cephfs-a",
      "cluster_name": "cluster-a",
      "node_name": "c1-worker",
      "directory_name": "phase11-expired-a-f06c4928",
      "directory_path": "/mnt/testbed-cephfs/dms-phase10/phase11-expired-a-f06c4928",
      "group_name": "dms-phase11-phase11-expired-a-f06c4928",
      "create_request_id": "req_10f55309be7c497b9ffe5d7b132bc25e",
      "dry_run_sweep_request_id": "req_70888f43958b4220a8dd9ae19135f767",
      "sweep_request_id": "req_cc32d55fa65f4d988bbccd611eb9bc1a",
      "unblock_request_id": "req_271b8a35540043959d42ec8e587d0da3",
      "delete_request_id": "req_00093123d2224f08b9978c922ec30850",
      "blocked_stat": "root dms-phase11-phase11-expired-a-f06c4928 0 /mnt/testbed-cephfs/dms-phase10/phase11-expired-a-f06c4928"
    },
    {
      "storage_name": "cephfs-b",
      "cluster_name": "cluster-b",
      "node_name": "c2-worker",
      "directory_name": "phase11-expired-b-f06c4928",
      "directory_path": "/mnt/testbed-cephfs-c2/dms-phase10/phase11-expired-b-f06c4928",
      "group_name": "dms-phase11-phase11-expired-b-f06c4928",
      "create_request_id": "req_42803cd1dc4944a8afcbc42f026b9c5f",
      "dry_run_sweep_request_id": "req_a8400c9bbe884205a136255fa1d467c5",
      "sweep_request_id": "req_935c29f982e54b7ca25889548c09424b",
      "unblock_request_id": "req_a8ef587e8039448c9f1be9ec17684923",
      "delete_request_id": "req_e30918709aa6479a8b9ed3deecb44a70",
      "blocked_stat": "root dms-phase11-phase11-expired-b-f06c4928 0 /mnt/testbed-cephfs-c2/dms-phase10/phase11-expired-b-f06c4928"
    }
  ],
  "system_skip": {
    "storage_name": "cephfs-a",
    "directory_name": "phase11-system-f06c4928",
    "create_request_id": "req_e0e279c96d254139a5882a390f251776",
    "sweep_request_id": "req_7eaa21e1efe3439fa5e54fc760eb2708",
    "delete_request_id": "req_aab04155768640288b9f1ac5c174d534",
    "skip_reason": "resource_type_not_auto_blocked"
  }
}
```

## Verified Behavior

- `GET /api/v1/operations/filesystems/expiring` returned expired filesystem resources created with `expires_at=2000-01-01T00:00:00Z`.
- `GET /api/v1/operations/action-required` included `filesystem_expired_unblocked` before sweep.
- `POST /api/v1/resource-management/filesystems:expiration-sweep` with `dry_run=true` recorded targets without changing POSIX access.
- Real sweep blocked expired user resources on both c1 and c2 host-mounted CephFS targets.
- Block changed the directory mode to `0000` and stored restore state in DMS DB.
- While blocked, `alice`, `bob`, and the denied LDAP fixture could not access the directory.
- Manual unblock restored `0770` access for `alice` and `bob`.
- The denied LDAP fixture remained denied after unblock.
- `resource_type=system` expired filesystem resource was skipped by sweep with `resource_type_not_auto_blocked`.
- The system skip surfaced through `action-required`.
- Delete removed DMS-owned test directories and DMS-managed LDAP access groups.

## Cleanup Evidence

After verification, the script deleted the temporary Kubernetes namespace in both clusters and deleted the Phase 11 denied-user LDAP fixture.

Cleanup checks:

```bash
ssh ldap "ldapsearch -x -LLL -H ldap://127.0.0.1 -D cn=admin,dc=testbed,dc=local -w testbed-admin -b ou=people,dc=testbed,dc=local '(uid=dms-phase11-*)' uid"
ssh ldap "ldapsearch -x -LLL -H ldap://127.0.0.1 -D cn=admin,dc=testbed,dc=local -w testbed-admin -b ou=groups,dc=testbed,dc=local '(cn=dms-phase11-*)' cn memberUid"
ssh c1-worker 'find /mnt/testbed-cephfs/dms-phase10 -maxdepth 1 -name "phase11-*" -print'
ssh c2-worker 'find /mnt/testbed-cephfs-c2/dms-phase10 -maxdepth 1 -name "phase11-*" -print'
```

Output:

```text
no dms-phase11 LDAP users
no dms-phase11 LDAP groups
no c1 phase11 directories
no c2 phase11 directories
```

## Notes

- The Phase 11 verification still uses `RMWorkerRuntime.run_once()` from the verifier process. Long-running RM Worker Kubernetes Deployment/loop verification remains a later phase.
- Agent mount evidence continues to report `writable=false` from the agent container. Backend mutation is performed by SSH/sudo on the worker host, which is the expected Phase 10/11 execution path.
- Storage mappings are `Degraded` only because DM readiness is not provided for these filesystem targets. RM readiness is `Ready`, which is the required guard for filesystem Resource Management.
- Phase 11 does not implement filesystem quota, update, check/sync, import, assign-quota, usage pressure, or automatic cron/controller expiration sweep.
