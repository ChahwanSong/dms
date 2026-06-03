# DMS Phase 19 Verification

Date: 2026-06-03

Scope verified in this pass:

- structured Data Management `scan` request parsing and compatibility normalization
- `sync`/`rm` endpoint fail-closed behavior
- destructive confirm guard for existing `sync`/`rm` jobs
- `data_jobs` Phase 19 evidence fields and scan query filters/detail
- scan Identity Mapping and DM Agent evidence preflight
- scan runtime POSIX preflight pod, VolcanoJob submission/monitoring, file artifact parsing, and result summary persistence
- `data_job_artifact_parse_failed` action-required path for missing/unreadable/invalid local file artifacts
- pinned real mpifileutils `dscan` job image build and live testbed scan execution
- standalone two-node MPI `dscan` smoke with Volcano, CephFS RWX PVC, and the
  real mpifileutils image family
- install manifest/config/runbook alignment for `DMS_DM_*` settings and Volcano RBAC
- live testbed DMS API/Planner/DM Agent/DM Worker deployment with a tiny CephFS scan target

Not verified in this pass:

- DMS API-driven broad or multi-node recursive scans
- Phase 20 destructive `sync`/`rm` preview, confirm, or execution

The verifier was run twice: first with the testbed fixture `dscan` executable to
exercise the control path deterministically, then with a real
`chahwansong/mpifileutils` based job image. The real `dscan` JSON report does not
include total bytes, so the Phase 19 scan pod writes a DMS-normalized
`summary.json` artifact beside `dscan-report.json`; the DB parser reads
`summary.json` first and falls back to the raw report only when needed.

## Local Regression

Command:

```bash
cd /home/mason/workspace/dms
python3 -m compileall -q src/dms
```

Output:

```text
```

Exit status: 0.

Command:

```bash
cd /home/mason/workspace/dms
python3 -m pytest tests/test_phase19_data_management_scan.py \
  tests/test_phase1_contracts.py::test_data_sync_rm_fail_closed_and_confirm_does_not_reopen_destructive_job \
  tests/test_phase1_contracts.py::test_data_management_rejects_raw_options_and_path_traversal \
  tests/test_phase3_inventory.py::test_planner_rejects_failed_mapping_and_uses_ready_agent_pool \
  tests/test_phase16_mtls_auth.py::test_all_protected_api_endpoints_reach_handler_with_mtls_auth -q
```

Output:

```text
........                                                                 [100%]
8 passed in 6.19s
```

Command:

```bash
cd /home/mason/workspace/dms
python3 -m pytest -q
```

Output:

```text
143 passed in 86.93s (0:01:26)
```

Command:

```bash
cd /home/mason/workspace/dms
python3 -m py_compile scripts/phase19_data_management_scan.py scripts/phase19_dscan_fixture.py
bash -n scripts/verify-phase19-testbed.sh
git diff --check
```

Exit status: 0.

## Testbed Readiness Check

Testbed metadata inspected:

- `/home/mason/workspace/testbed/testbed-summary.json`
- `/home/mason/workspace/testbed/testbed-info.json`
- `/home/mason/workspace/testbed/TOPOLOGY.md`

Relevant metadata:

- five Vagrant/VirtualBox VMs
- cluster-a and cluster-b Kubernetes v1.34.6
- Volcano 1.14.1 installed as secondary scheduler
- OpenLDAP/SSSD with test users `alice` and `bob`
- cluster-a CephFS mounted on hosts at `/mnt/testbed-cephfs`
- PostgreSQL reachable in cluster-a

Command:

```bash
cd /home/mason/workspace/testbed
vagrant status
```

Output:

```text
Current machine states:

ldap                      running (virtualbox)
c1-control                running (virtualbox)
c1-worker                 running (virtualbox)
c2-control                running (virtualbox)
c2-worker                 running (virtualbox)
```

Command:

```bash
ssh -o BatchMode=yes c1-control \
  'kubectl get nodes --no-headers && kubectl -n volcano-system get deploy --no-headers'
```

Output:

```text
c1-control   Ready   control-plane,worker   7d22h   v1.34.6
c1-worker    Ready   worker                 7d22h   v1.34.6
volcano-admission     1/1   1     1     7d22h
volcano-controllers   1/1   1     1     7d22h
volcano-scheduler     1/1   1     1     7d22h
```

Command:

```bash
ssh -o BatchMode=yes c1-worker \
  'getent passwd alice && getent group developers && findmnt -rn /mnt/testbed-cephfs -o FSTYPE,TARGET'
```

Output:

```text
alice:*:10000:10000:Alice Testbed:/home/alice:/bin/bash
developers:*:10000:alice,bob,bob
ceph /mnt/testbed-cephfs
```

## Live Testbed Volcano Scan

Command:

```bash
cd /home/mason/workspace/dms
scripts/verify-phase19-testbed.sh
```

Key output:

```json
{
  "artifact_uri": "file:///mnt/testbed-cephfs/dms-phase19-artifacts-20260603220451/job_5ca2e30ba2054911aadb3db49b17da3e",
  "missing_identity_job_id": "job_afc999c2623e468398cebff6220a7534",
  "report_uri": "file:///mnt/testbed-cephfs/dms-phase19-artifacts-20260603220451/job_5ca2e30ba2054911aadb3db49b17da3e/dscan-report.json",
  "scan_job_id": "job_5ca2e30ba2054911aadb3db49b17da3e",
  "scan_request_id": "req_f57d994e1ffe4bcab15936d85374e0c4",
  "state": "Succeeded",
  "summary_uri": "file:///mnt/testbed-cephfs/dms-phase19-artifacts-20260603220451/job_5ca2e30ba2054911aadb3db49b17da3e/summary.json",
  "summary": {
    "directory_count": 2,
    "error_count": 0,
    "file_count": 3,
    "scan_root": "dms-phase19-20260603220451/input",
    "total_bytes": 31
  },
  "volcano_job_ref": {
    "adapter": "volcano-kubectl",
    "job_ref": "volcano://dms-phase19/dms-scan-job-5ca2e30ba2054911aadb3db49b17da3e"
  }
}
```

Volcano evidence:

```text
NAME                                                                 STATUS      MINAVAILABLE   RUNNINGS   AGE   QUEUE
job.batch.volcano.sh/dms-scan-job-5ca2e30ba2054911aadb3db49b17da3e   Completed   1                         9s    default
```

Artifact files:

```text
/mnt/testbed-cephfs/dms-phase19-artifacts-20260603220451/job_5ca2e30ba2054911aadb3db49b17da3e/summary.json
/mnt/testbed-cephfs/dms-phase19-artifacts-20260603220451/job_5ca2e30ba2054911aadb3db49b17da3e/dscan-report.json
/mnt/testbed-cephfs/dms-phase19-artifacts-20260603220451/job_5ca2e30ba2054911aadb3db49b17da3e/stdout.log
/mnt/testbed-cephfs/dms-phase19-artifacts-20260603220451/job_5ca2e30ba2054911aadb3db49b17da3e/stderr.log
```

Report excerpt:

```json
{
  "broken_paths": [],
  "directory": "/dms/target/dms-phase19-20260603220451/input",
  "summary": {
    "total_bytes": 31,
    "total_directories": 2,
    "total_entries": 5,
    "total_files": 3,
    "total_other": 0,
    "total_symlinks": 0
  }
}
```

Verified live gates:

- DMS image deployed to `dms-phase19` namespace.
- DM Agent reported `cephfs-a` mounted on `c1-worker` with `dscan` and `alice` POSIX identity evidence.
- `phase19-alice` Identity Mapping was Active and mapped to UID/GID `10000:10000`.
- DM Worker created a runtime preflight Pod on `c1-worker`, running as UID/GID `10000:10000`, and checked target directory read/execute access before Volcano submission.
- DM Worker submitted a VolcanoJob using scheduler `volcano`; the scan pod ran on `c1-worker`.
- `data_jobs.artifact_uri` recorded the artifact base URI and result summary URIs pointed at `summary.json`, `dscan-report.json`, `stdout.log`, and `stderr.log`.
- DM Worker parsed the report into file/directory/byte/error summary and marked the scan request/job `Succeeded`.
- A missing requester identity scan failed before VolcanoJob creation.
- `sync` and `rm` returned unsupported responses without creating Data Jobs.

## Live Testbed Scan With Real mpifileutils

Pinned job image source:

- Dockerfile: `install/docker/Dockerfile.mpifileutils`
- mpifileutils ref:
  `chahwansong/mpifileutils@e3bfee10970bb4e24204d28689e3337e9741cca4`
- testbed image:
  `testbed-registry:5000/dms-mpifileutils:e3bfee1`

Dockerfile build:

```bash
cd /home/mason/workspace/dms
docker build -f install/docker/Dockerfile.mpifileutils \
  -t dms-mpifileutils-real:dockerfile .
```

Exit status: 0. The build installed `dscan`, `dsync`, `nsync`, and `drm` into
`/opt/mpifileutils/bin`.

Local image smoke:

```bash
tmpdir=$(mktemp -d /tmp/dms-mpifileutils-dockerfile.XXXXXX)
mkdir -p "$tmpdir/input/sub" "$tmpdir/out"
printf 'alpha\n' > "$tmpdir/input/a.txt"
printf 'beta\n' > "$tmpdir/input/sub/b.txt"
chmod 0755 "$tmpdir"
chmod -R a+rX "$tmpdir/input"
chmod 0777 "$tmpdir/out"
docker run --rm -u 10000:10000 -v "$tmpdir:/work" \
  dms-mpifileutils-real:dockerfile \
  sh -c 'dscan --directory /work/input --output /work/out/dscan-report.json --print >/work/out/stdout.log 2>/work/out/stderr.log && test -s /work/out/dscan-report.json'
```

The command exited 0 and produced `dscan-report.json`, `stdout.log`, and
`stderr.log` as UID/GID `10000:10000`. The observed real report summary contains
`total_entries`, `total_files`,
`total_directories`, `total_symlinks`, and `total_other`; it does not contain a
byte-total field. The Phase 19 Volcano command therefore writes
`summary.json` with `file_count`, `directory_count`, `total_bytes`, and
`error_count` using POSIX `find`/`awk` on the same mounted target.

Command:

```bash
cd /home/mason/workspace/dms
DMS_PHASE19_DM_JOB_IMAGE='testbed-registry:5000/dms-mpifileutils:e3bfee1' \
DMS_PHASE19_DM_JOB_IMAGE_REF='chahwansong/mpifileutils@e3bfee10970bb4e24204d28689e3337e9741cca4' \
scripts/verify-phase19-testbed.sh
```

Key output:

```json
{
  "artifact_uri": "file:///mnt/testbed-cephfs/dms-phase19-artifacts-20260603222132/job_3a55ca8ca77b433b9505ec50c38f8186",
  "missing_identity_job_id": "job_a9d6661539ff4f86ab13480915fd34d3",
  "report_uri": "file:///mnt/testbed-cephfs/dms-phase19-artifacts-20260603222132/job_3a55ca8ca77b433b9505ec50c38f8186/dscan-report.json",
  "scan_job_id": "job_3a55ca8ca77b433b9505ec50c38f8186",
  "scan_request_id": "req_58909af4bc3e4e09aa232fdb4a8492c7",
  "state": "Succeeded",
  "summary_uri": "file:///mnt/testbed-cephfs/dms-phase19-artifacts-20260603222132/job_3a55ca8ca77b433b9505ec50c38f8186/summary.json",
  "summary": {
    "directory_count": 2,
    "error_count": 0,
    "file_count": 3,
    "scan_root": "dms-phase19-20260603222132/input",
    "total_bytes": 31
  },
  "volcano_job_ref": {
    "adapter": "volcano-kubectl",
    "job_ref": "volcano://dms-phase19/dms-scan-job-3a55ca8ca77b433b9505ec50c38f8186"
  }
}
```

Volcano evidence:

```text
NAME                                                                 STATUS      MINAVAILABLE   RUNNINGS   AGE   QUEUE
job.batch.volcano.sh/dms-scan-job-3a55ca8ca77b433b9505ec50c38f8186   Completed   1                         13s   default
```

Artifact files:

```text
/mnt/testbed-cephfs/dms-phase19-artifacts-20260603222132/job_3a55ca8ca77b433b9505ec50c38f8186/find-errors.log
/mnt/testbed-cephfs/dms-phase19-artifacts-20260603222132/job_3a55ca8ca77b433b9505ec50c38f8186/dscan-report.json
/mnt/testbed-cephfs/dms-phase19-artifacts-20260603222132/job_3a55ca8ca77b433b9505ec50c38f8186/summary.json
/mnt/testbed-cephfs/dms-phase19-artifacts-20260603222132/job_3a55ca8ca77b433b9505ec50c38f8186/stdout.log
/mnt/testbed-cephfs/dms-phase19-artifacts-20260603222132/job_3a55ca8ca77b433b9505ec50c38f8186/stderr.log
```

Normalized summary artifact:

```json
{"summary":{"file_count":3,"directory_count":2,"total_bytes":31,"error_count":0}}
```

## Standalone Multi-Node MPI dscan Smoke

This is not the DMS API/DM Worker Phase 19 execution path. It was run as a
standalone Kubernetes/Volcano smoke test to verify that the testbed can execute
the mpifileutils image family with real MPI ranks across both cluster-a nodes on
a shared CephFS RWX PVC.

The existing DMS Phase 19 host-mounted scan target is only mounted as CephFS on
`c1-worker`; `c1-control` has the path but not the CephFS mount. For this
standalone MPI check, the test used a `testbed-cephfs` RWX PVC so both nodes
could mount the same input/artifact path.

Temporary MPI image:

- base: `dms-mpifileutils-real:dockerfile`
- additional packages: `openssh-client`, `openssh-server`
- registry image:
  `testbed-registry:5000/dms-mpifileutils-mpi:ssh`

The image was needed because MPICH Hydra uses SSH to launch remote ranks in this
minimal standalone setup. Worker containers required `SYS_CHROOT` for OpenSSH
preauth sandboxing.

VolcanoJob:

```text
namespace: dms-mpi-verify
job: dms-mpi-dscan-smoke
storage: PVC mpi-data, StorageClass testbed-cephfs, RWX
launcher command: mpiexec -launcher ssh -n 2 -f /data/artifacts/hostfile \
  /opt/mpifileutils/bin/dscan --directory /data/input \
  --output /data/artifacts/dscan-mpi-report.json --print
```

Pod placement:

```text
NAME                             READY   STATUS      IP             NODE
dms-mpi-dscan-smoke-launcher-0   0/1     Completed   10.244.0.211   c1-control
dms-mpi-dscan-smoke-worker-0     1/1     Running     10.244.0.92    c1-control
dms-mpi-dscan-smoke-worker-1     1/1     Running     10.244.1.69    c1-worker
```

MPI hostfile:

```text
10.244.0.92
10.244.1.69
```

Result:

```text
launcher phase: Succeeded
stdout: Walked 5 items in 0.031 seconds
summary:
  total_entries    : 5
  total_files      : 3
  total_directories: 2
  total_symlinks   : 0
  total_other      : 0
stderr: empty
```

`dscan-mpi-report.json` summary:

```json
{
  "directory": "/data/input",
  "summary": {
    "total_entries": 5,
    "total_files": 3,
    "total_directories": 2,
    "total_symlinks": 0,
    "total_other": 0
  },
  "broken_paths": []
}
```

This verifies that the testbed can schedule a Volcano MPI job with one worker
on `c1-control`, one worker on `c1-worker`, shared CephFS PVC data, and a real
mpifileutils `dscan` invocation. It does not prove that the current DMS
Phase 19 API/DM Worker implementation selects multiple nodes or submits
multi-node MPI scan jobs; that remains an operational enhancement beyond the
Phase 19 DMS path.

## Residual Gaps

The Phase 19 read-only scan path now has live testbed proof with both the
deterministic fixture, a pinned real mpifileutils image, and standalone
two-node MPI smoke evidence. Remaining gaps are broader DMS-integrated
operational scale and future destructive Data Management phases: DMS
API-driven multi-node recursive scans, production object storage backends,
artifact retention policy, and Phase 20 `sync`/`rm`
preview/confirm/execution.
