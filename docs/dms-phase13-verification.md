# DMS Phase 13 Verification

Date: 2026-05-31 14:19 +0900

Phase 13 verifies that Resource Management requests are processed by Kubernetes long-running Planner and RM Worker Deployments instead of verifier-side `run_once()` calls. It also adds the IBM GPFS / IBM Storage Scale fileset command adapter. The current testbed has no IBM Storage Scale cluster, so GPFS live verification is explicitly skipped and covered by fake command executor regression tests.

## Local Regression

Command:

```bash
cd /home/mason/workspace/dms
pytest -q
```

Output:

```text
90 passed in 56.51s
```

Focused Phase 13 coverage added:

- GPFS command rendering for fileset create, link, quota, check, sync, import, assign-quota, unlink, and delete.
- GPFS parseable `-Y` output parsing for fileset, quota, and filesystem quota capability evidence.
- GPFS fail-closed behavior for missing command, filesystem quota disabled, per-fileset quota disabled, unsafe fileset name/path, ordinary directory import, quota-only delete, and read-back mismatch after side effect.
- GPFS Kubernetes namespace quota CSI mapping remains available for `spectrumscale.csi.ibm.com`.
- competing RM workers atomically claim a planned request only once.

## Testbed Live Verification

The final testbed run used a freshly rebuilt and pushed `testbed-registry:5000/dms:phase13` image, then reused it for the final replay to avoid another image build. The verifier deployed DMS API, DMS Agent DaemonSets, Planner Deployment, and RM Worker Deployment on the Vagrant multi-cluster testbed.

Command:

```bash
cd /home/mason/workspace/dms
DMS_PHASE13_SKIP_IMAGE_BUILD=1 scripts/verify-phase13-testbed.sh
```

Final Phase 13 DBs:

```text
operational:   postgresql://appuser:***@192.168.56.11:30432/dms_phase13_20260531141310
observability: postgresql://appuser:***@192.168.56.11:30432/dms_phase13_obs_20260531141310
```

Deployment evidence before verification:

```text
deployment.apps/dms-api         1/1     1            1           api          testbed-registry:5000/dms:phase13
deployment.apps/dms-planner     1/1     1            1           planner      testbed-registry:5000/dms:phase13
deployment.apps/dms-rm-worker   1/1     1            1           rm-worker    testbed-registry:5000/dms:phase13
pod/dms-api-74bb65c4cd-75ps7         1/1   Running   0
pod/dms-planner-5c6d9c4d98-fr72g     1/1   Running   0
pod/dms-rm-worker-756cf7ffcf-6tqpv   1/1   Running   0
```

Final output summary:

```json
{
  "status": "ok",
  "api_url": "http://192.168.56.11:30093",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase13_20260531141310",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase13_obs_20260531141310",
  "agent_reports": {
    "cluster-a:c1-worker:cephfs-a": {
      "report_id": "agent_41f4d22dc5744b5292b11930f9f1c06c",
      "mount_path": "/mnt/testbed-cephfs",
      "filesystem_type": "ceph"
    },
    "cluster-b:c2-worker:cephfs-b": {
      "report_id": "agent_7bca51ea3ff14ee98c7ba646dda80f47",
      "mount_path": "/mnt/testbed-cephfs-c2",
      "filesystem_type": "ceph"
    }
  },
  "quota_lifecycle": [
    {
      "storage_name": "cephfs-a",
      "directory_name": "phase13-quota-a-6f00efe2",
      "create_request_id": "req_7e4ebfa3bbc44e98a00e896c9188ddfd",
      "decrease_request_id": "req_5fa04d66a6d04c4a8255ab32f3e22790",
      "sync_request_id": "req_514ed778fc7c4121afe398225b677230",
      "delete_request_id": "req_64ce5e60814e4619a5a2689dbb9d771d",
      "synced_capacity_bytes": 14680064
    },
    {
      "storage_name": "cephfs-b",
      "directory_name": "phase13-quota-b-6f00efe2",
      "create_request_id": "req_e50e5a8c3f8843f680b9ac84826ba5b0",
      "decrease_request_id": "req_81ed84fcefba4366b70d97e925acabf9",
      "sync_request_id": "req_f0cdf46159cc4ca3b5e4399b6073f73f",
      "delete_request_id": "req_90ff44b83d48483dabbc17b062a673c7",
      "synced_capacity_bytes": 14680064
    }
  ],
  "assign_quota": {
    "storage_name": "cephfs-a",
    "directory_name": "phase13-assign-6f00efe2",
    "assign_request_id": "req_e35255025e8a46069330fadc8735bb5b",
    "check_request_id": "req_be5e80cea7be4d0a898bb8267749d14a",
    "marker_management_mode": "quota_only",
    "delete_rejected_request_id": "req_c21d72cec5054b09b5f5ee62c2db7253"
  },
  "full_import": {
    "storage_name": "cephfs-b",
    "directory_name": "phase13-import-6f00efe2",
    "group_name": "dms-phase13-import-6f00efe2",
    "import_request_id": "req_afd95e3f36fb472587f9c11a96ae3093",
    "quota_update_request_id": "req_21a5011eb6c74b1b8cea932fd9ceae72",
    "delete_request_id": "req_4b5cbea1b26a4f39b9d3ef3bfd95c8c4"
  },
  "worker_scale": {
    "check_request_ids": [
      "req_5d197915eace4862ba31cc59993635d9",
      "req_37c65a11348f40569309813ef2113824"
    ],
    "worker_ids": [
      "dms-rm-worker-756cf7ffcf-2tsqm",
      "dms-rm-worker-756cf7ffcf-6tqpv"
    ]
  },
  "worker_restart": {
    "deleted_pod": "pod/dms-rm-worker-756cf7ffcf-6tqpv",
    "pods_after": [
      "pod/dms-rm-worker-756cf7ffcf-6lljw"
    ]
  },
  "stale_query": {
    "fixture_request_id": "req_4b4805519ef74dbd8a843773847f6038",
    "request_status": "StaleClaim",
    "query_count": 1,
    "run_id": "run_17f0b015dcf14ef086a26ddae8155c60"
  },
  "gpfs_live_verification": {
    "status": "skipped",
    "reason": "testbed has no IBM GPFS / IBM Storage Scale cluster"
  }
}
```

Verification meaning:

- API requests were submitted through the DMS NodePort API and processed by `dms planner --loop` plus `dms rm-worker --loop`.
- The verifier did not call `Planner.run_once()` or `RMWorkerRuntime.run_once()` to process RM work.
- `cephfs-a` and `cephfs-b` quota create, update, decrease, check, drift, sync, assign-quota, import, and delete flows executed through RM Worker Pods.
- RM Worker replica scale-out processed two no-side-effect check requests on two different Pod worker IDs.
- RM Worker Pod deletion produced a replacement Pod and did not lose the control-plane state.
- A manually expired claimed run was surfaced through stale run query as `StaleClaim`.
- GPFS live verification was skipped because the testbed has no IBM Storage Scale deployment.

## Duplicate Claim Evidence

During Phase 13 development, live scale verification exposed a race where two RM Worker replicas could claim the same `Planned` row. The repository claim path now uses an atomic conditional update, and the final live DB has one run per request.

Command:

```bash
cd /home/mason/workspace/dms
PGPASSWORD="$(ssh c1-control "kubectl -n postgresql get secret postgresql-auth -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d")" \
psql -h 192.168.56.11 -p 30432 -U appuser -d dms_phase13_20260531141310 \
  -v ON_ERROR_STOP=1 \
  -c "select request_id, count(*) as run_count, array_agg(state order by started_at) as states from runs group by request_id having count(*) > 1 order by request_id;" \
  -c "select count(*) as total_runs, count(distinct request_id) as distinct_requests from runs;" \
  -c "select status, count(*) from plans group by status order by status;"
```

Output:

```text
 request_id | run_count | states
------------+-----------+--------
(0 rows)

 total_runs | distinct_requests
------------+-------------------
         22 |                22

   status   | count
------------+-------
 StaleClaim |     1
 Succeeded  |    21
```

## Cleanup

The verifier cleaned up the temporary `dms-phase13` namespace in both clusters:

```text
== Cleanup Phase 13 manifests ==
namespace "dms-phase13" deleted
namespace "dms-phase13" deleted
```

It also removed the phase-scoped LDAP fixture user, DMS access groups, and host test directories created by the CephFS checks.

## IBM Storage Scale References

GPFS command adapter behavior was implemented against IBM Storage Scale command semantics:

- `mmcrfileset`: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=reference-mmcrfileset-command>
- `mmlinkfileset` / fileset linking: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=filesets-linking-fileset>
- `mmlsquota`: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=reference-mmlsquota-command>
- quota files and `mmlsfs -Q`: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=system-quota-files>
- `mmsetquota`: <https://www.ibm.com/docs/en/storage-scale/5.2.3?topic=reference-mmsetquota-command>
- `mmunlinkfileset`: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=reference-mmunlinkfileset-command>
- `mmdelfileset`: <https://www.ibm.com/docs/en/storage-scale/6.0.0?topic=reference-mmdelfileset-command>
