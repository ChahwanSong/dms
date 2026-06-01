# DMS Phase 15 Verification

Date: 2026-06-01 00:04 +0900

Phase 15 verifies resource expiry update/import semantics and Kubernetes namespace quota expiry lifecycle from `docs/dms-phase15.md`.

## Testbed

Source metadata:

- `/home/mason/workspace/testbed/testbed-summary.json`
- `/home/mason/workspace/testbed/testbed-info.json`
- `/home/mason/workspace/testbed/TOPOLOGY.md`

Relevant testbed state:

- Kubernetes: v1.34.6 on independent `cluster-a` and `cluster-b`.
- PostgreSQL: `192.168.56.11:30432`.
- OpenLDAP/SSSD: `ldap://ldap.testbed.local`, SSSD on Kubernetes nodes.
- CephFS host mounts: `cluster-a/c1-worker:/mnt/testbed-cephfs`, `cluster-b/c2-worker:/mnt/testbed-cephfs-c2`.
- Longhorn: `cluster-b/testbed-longhorn`.

## Commands

Local regression:

```bash
cd /home/mason/workspace/dms
python3 -m py_compile scripts/phase13_long_running_rm_worker.py scripts/phase15_resource_expiry.py scripts/phase14_runtime_hardening.py
bash -n scripts/verify-phase15-testbed.sh scripts/verify-phase14-testbed.sh scripts/verify-phase13-testbed.sh
pytest -q
git diff --check
```

Testbed live verification:

```bash
cd /home/mason/workspace/dms
DMS_PHASE13_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase15-testbed.sh
```

The final run reused `testbed-registry:5000/dms:phase15`, which had been built and copied into the local testbed registry during the same verification session.

## Local Results

```text
py_compile: passed
bash -n: passed
pytest -q: 102 passed in 62.42s (0:01:02)
git diff --check: passed
```

The Phase 15 local tests cover:

- canonical `expires_at` validation and unsupported `expiry_at`/`clear_expires_at` rejection
- create-time required expiry for filesystem and Kubernetes quota resources
- update-time expiry change/preserve semantics
- import-time expiry request/default semantics
- Kubernetes namespace quota expiring query and action-required aggregation
- Kubernetes namespace quota expiration sweep block and system-resource skip behavior

## Testbed Results

Fresh PostgreSQL databases:

- operational: `dms_phase13_phase15_20260531235816`
- observability: `dms_phase13_obs_phase15_20260531235816`

The verifier first re-ran the Phase 13 long-running runtime flow on the Phase 15 image and completed with `status: ok`. This preserved the production-like path where requests are submitted through the deployed API and processed by long-running Planner/RM Worker Deployments.

Phase 15 output summary:

```json
{
  "status": "ok",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase13_phase15_20260531235816",
  "filesystem": {
    "resource_key": "cephfs-a:phase15-fs-a8b28b83",
    "create_request": "req_f1c63bad8b454d06acf214cd9e3fb744",
    "update_request": "req_b8f978d428c749e098c0e7c4f3e08e30",
    "expires_at": "2026-09-28T15:01:46.763311+00:00"
  },
  "kubernetes": {
    "create_request": "req_792231dec1504117a8cd063f5bde8bfd",
    "update_request": "req_1d99495af84543b68b58573bcf10faba",
    "import_request": "req_9f15a8f3f5224b62a7902a3f65e89214",
    "sweep_request": "req_12cdbc2afbb54cc4a9e570768e2704c8",
    "expired_query_count": 1,
    "sweep_hard": {
      "persistentvolumeclaims": "0",
      "requests.storage": "0",
      "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "0",
      "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "0"
    }
  }
}
```

Verified live behavior:

- Filesystem create accepted future `expires_at`.
- Filesystem update changed DB desired `expires_at` and completed through the deployed RM Worker.
- Kubernetes namespace quota create accepted future `expires_at` and wrote `dms.io/expires-at` on live `ResourceQuota/dms-storage-quota`.
- Kubernetes quota update without `expires_at` preserved existing expiry.
- Kubernetes quota import omitted `expires_at`, and Planner defaulted it to server-side now + 365 days.
- Expired Kubernetes quota appeared in `GET /api/v1/operations/kubernetes/namespace-quotas/expiring`.
- Expired but unblocked Kubernetes quota appeared in action-required as `kubernetes_quota_expired_unblocked`.
- On-demand Kubernetes expiration sweep zeroed live `ResourceQuota.spec.hard`.

Cleanup evidence:

- `Namespace/phase15-quota-a8b28b83`, `Namespace/phase15-import-a8b28b83`, and `Namespace/phase15-expire-a8b28b83` were deleted on `cluster-b`.
- `Namespace/dms-phase15` was deleted on both clusters.

## Notes

During verifier development, earlier attempts exposed script-only issues: missing executable bit, `HttpClient` compatibility with existing helper `json=`, Pod restart delete race, storage mapping reuse after Phase 13 carry-forward, and a too-short 60 second filesystem host exec timeout for the low-resource testbed. The final verifier includes those fixes and completed with exit code 0.

Phase 15 still does not implement automatic cron/controller expiry sweep, filesystem delete/archive expiry policy, production Helm/Kustomize packaging, Data Management live execution, or VolcanoJob create/watch/terminate.
