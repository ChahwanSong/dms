# DMS Phase 20 Verification

Date: 2026-06-03

Phase 20 verifies Data Management `sync` and `rm` on top of the Phase 19
Volcano/mpifileutils scan runtime. The live verification used the testbed
metadata in `/home/mason/workspace/testbed/testbed-summary.json`, deployed DMS
to `cluster-a`, used `cluster-a/testbed-cephfs`, and executed real VolcanoJobs
with the pinned mpifileutils image.

## Scope

Verified:

- structured and compatibility request parsing for `sync` and `rm`
- option allowlist and raw option rejection
- Identity Mapping/POSIX preflight
- DM Agent mount/tool/credential/network/identity based node selection
- `dsync` dry-run preview and confirmed execution
- `drm` dry-run preview and confirmed execution
- explicit confirm, preview TTL expiry, and missing identity negative paths
- VolcanoJob submission/monitoring and artifact parsing
- query/action-required coverage for `data.scan`, `data.sync`, `data.rm`
- standalone multi-node MPI `dscan` with two worker pods on two nodes

Not live verified:

- `nsync` separated-role execution. The current live adapter fails closed for
  `nsync` execution.
- large-scale throughput/performance
- automatic partial mutation repair
- object storage artifact backend

## Images

- DMS image: `testbed-registry:5000/dms:phase20-20260603234227`
- DMS docker image: `192.168.56.11:5000/dms:phase20-20260603234227`
- mpifileutils ref:
  `chahwansong/mpifileutils@e3bfee10970bb4e24204d28689e3337e9741cca4`
- MPI ssh image:
  `testbed-registry:5000/dms-mpifileutils-mpi:phase20-20260603234227`

## Live Run

Command:

```bash
cd /home/mason/workspace/dms
DMS_PHASE20_SKIP_IMAGE_BUILD=1 \
DMS_PHASE20_CLEANUP=1 \
DMS_PHASE20_MPI_CLEANUP=1 \
DMS_PHASE20_K8S_IMAGE='testbed-registry:5000/dms:phase20-20260603234227' \
DMS_PHASE20_DOCKER_IMAGE='192.168.56.11:5000/dms:phase20-20260603234227' \
DMS_PHASE20_MPI_K8S_IMAGE='testbed-registry:5000/dms-mpifileutils-mpi:phase20-20260603234227' \
DMS_PHASE20_MPI_DOCKER_IMAGE='192.168.56.11:5000/dms-mpifileutils-mpi:phase20-20260603234227' \
scripts/verify-phase20-testbed.sh
```

Result: exit status 0.

DBs:

- operational: `dms_phase20_20260603235548`
- observability: `dms_phase20_obs_20260603235548`

Deployed workloads:

- `deployment/dms-api`
- `deployment/dms-planner`
- `deployment/dms-dm-worker`
- `daemonset/dms-dm-agent`

Cleanup:

- `namespace/dms-phase20` deleted
- `namespace/dms-phase20-mpi-20260603235548` deleted

## Sync Evidence

- Data Job: `job_aa4972c3cc304d82bab972ddaa5c8a9e`
- Request: `req_7cab18ce63194c31a143e67661b64f01`
- Selected tool: `dsync`
- Artifact base:
  `file:///mnt/testbed-cephfs/dms-phase20-artifacts-20260603235548/job_aa4972c3cc304d82bab972ddaa5c8a9e`
- Preview Volcano ref:
  `volcano://dms-phase20/dms-sync-preview-job-aa4972c3cc304d82bab972ddaa5c8a9e`
- Execution Volcano ref:
  `volcano://dms-phase20/dms-sync-execution-job-aa4972c3cc304d82bab972ddaa5c8a9e`

Preview summary:

```json
{
  "operation": "data.sync",
  "phase": "preview",
  "selected_tool": "dsync",
  "dry_run": true,
  "file_count": 3,
  "directory_count": 2,
  "total_bytes": 38,
  "error_count": 0
}
```

Execution summary:

```json
{
  "operation": "data.sync",
  "phase": "execution",
  "selected_tool": "dsync",
  "dry_run": false,
  "file_count": 3,
  "directory_count": 2,
  "total_bytes": 38,
  "error_count": 0
}
```

Filesystem evidence:

- destination contained `alpha.txt`
- destination contained `beta.txt`
- destination contained `nested/gamma.txt`

## Rm Evidence

- Data Job: `job_01a760e616e845eb86a03ee1b9c7ae92`
- Request: `req_764a6cc88299416fac61add69fc4dc04`
- Selected tool: `drm`
- Artifact base:
  `file:///mnt/testbed-cephfs/dms-phase20-artifacts-20260603235548/job_01a760e616e845eb86a03ee1b9c7ae92`
- Preview Volcano ref:
  `volcano://dms-phase20/dms-rm-preview-job-01a760e616e845eb86a03ee1b9c7ae92`
- Execution Volcano ref:
  `volcano://dms-phase20/dms-rm-execution-job-01a760e616e845eb86a03ee1b9c7ae92`

Preview summary:

```json
{
  "operation": "data.rm",
  "phase": "preview",
  "selected_tool": "drm",
  "dry_run": true,
  "file_count": 1,
  "directory_count": 1,
  "total_bytes": 6,
  "target_absent": false,
  "error_count": 0
}
```

Execution summary:

```json
{
  "operation": "data.rm",
  "phase": "execution",
  "selected_tool": "drm",
  "dry_run": false,
  "file_count": 1,
  "directory_count": 1,
  "total_bytes": 6,
  "target_absent": true,
  "error_count": 0
}
```

Filesystem evidence:

- target directory `/mnt/testbed-cephfs/dms-phase20-20260603235548/remove-me`
  was absent after execution.

## Negative Cases

- Expired preview: `job_f0e04c42bb564bdca5797d910bfa83ce` ended
  `PreviewExpired`.
- Missing identity: `job_a40ce4ad154e4ee3a27326bf72b2edbc` ended
  `PreflightFailed`.
- The verifier also checks unsafe/raw options, destination-under-source guard,
  rm root guard, confirm without explicit `confirm=true`, and wrong preview
  fingerprint rejection before backend mutation.

## Multi-node MPI Smoke

The integrated verifier created `namespace/dms-phase20-mpi-20260603235548` and a
two-replica `deployment/mpi-worker` with required pod anti-affinity. The two
worker pods were scheduled on different cluster-a nodes. The launcher ran:

```bash
mpiexec -launcher ssh -localhost "$launcher_ip" -iface eth0 -n 2 \
  -f /data/artifacts/hostfile \
  /opt/mpifileutils/bin/dscan --directory /data/input \
  --output /data/artifacts/dscan-mpi-report.json --print
```

Hostfile:

```text
10.244.0.203
10.244.1.166
```

`dscan-mpi-report.json` summary:

```json
{
  "total_entries": 5,
  "total_files": 3,
  "total_directories": 2,
  "total_symlinks": 0,
  "total_other": 0
}
```

`broken_paths` was empty. The MPI smoke verifies the mpifileutils image and
testbed network/storage can run multi-rank MPI across nodes; the Phase 20
DMS API-driven sync/rm live path itself used same-node `dsync` and `drm`.

## Re-run Commands

```bash
python3 -m compileall -q src/dms
python3 -m py_compile scripts/phase20_data_management_sync_rm.py
bash -n scripts/verify-phase20-testbed.sh install/scripts/*.sh
python3 -m pytest tests/test_phase20_data_management_sync_rm.py tests/test_phase19_data_management_scan.py tests/test_phase1_contracts.py tests/test_phase16_mtls_auth.py -q
scripts/verify-phase20-testbed.sh
```

Local results from 2026-06-03:

```text
python3 -m compileall -q src/dms
python3 -m py_compile scripts/phase19_data_management_scan.py scripts/phase20_data_management_sync_rm.py scripts/phase19_dscan_fixture.py
bash -n scripts/verify-phase19-testbed.sh scripts/verify-phase20-testbed.sh install/scripts/*.sh
python3 -m pytest tests/test_phase20_data_management_sync_rm.py tests/test_phase19_data_management_scan.py tests/test_phase1_contracts.py tests/test_phase16_mtls_auth.py -q
52 passed in 41.41s
python3 -m pytest tests/test_gpfs_backend.py::test_gpfs_data_management_planning_records_gpfs_worker_pool tests/test_phase3_inventory.py::test_planner_rejects_failed_mapping_and_uses_ready_agent_pool -q
2 passed in 2.15s
python3 -m pytest tests/test_phase20_data_management_sync_rm.py tests/test_phase19_data_management_scan.py -q
13 passed in 9.33s
python3 -m pytest -q
149 passed in 91.01s
git diff --check
```
