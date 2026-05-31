# DMS Phase 14 Verification

Date: 2026-05-31 21:24 +0900

Phase 14 verifies the runtime hardening work from `docs/dms-phase14.md`:

- observability diagnostic writes are best-effort and do not change core lifecycle results
- live Resource Management backend selection is fail-closed for unsupported backends
- Kubernetes namespace quota uses the live adapter in the deployed RM Worker runtime

## Testbed

Source metadata:

- `/home/mason/workspace/testbed/testbed-summary.json`
- `/home/mason/workspace/testbed/testbed-info.json`

Relevant testbed state at verification time:

- Vagrant/VirtualBox multi-cluster testbed was provisioned and verified.
- Kubernetes: v1.34.6 on independent `cluster-a` and `cluster-b`.
- PostgreSQL: `192.168.56.11:30432`, namespace `postgresql`, workload `StatefulSet/postgresql`.
- OpenLDAP: `ldap://ldap.testbed.local`, host alias `ldap`.
- CephFS: `cluster-a/testbed-cephfs`, CSI `rook-ceph.cephfs.csi.ceph.com`.
- Longhorn: `cluster-b/testbed-longhorn`, CSI `driver.longhorn.io`.

## Commands

Local regression:

```bash
cd /home/mason/workspace/dms
python3 -m py_compile scripts/phase14_runtime_hardening.py scripts/phase4_kubernetes_quota_live.py src/dms/backend_registry.py src/dms/repositories.py src/dms/api.py src/dms/workers.py src/dms/agent.py tests/test_phase14_runtime_hardening.py
pytest tests/test_phase14_runtime_hardening.py tests/test_phase1_contracts.py tests/test_gpfs_backend.py
pytest
bash -n scripts/verify-phase14-testbed.sh scripts/verify-phase13-testbed.sh
git diff --check
```

Testbed live verification:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase14-testbed.sh
```

## Local Results

```text
tests/test_phase14_runtime_hardening.py tests/test_phase1_contracts.py tests/test_gpfs_backend.py:
33 passed in 27.76s

full pytest:
97 passed in 59.17s

py_compile: passed
bash -n: passed
git diff --check: passed
```

The Phase 14 regression tests cover:

- auth rejection remains HTTP 401 when observability writes raise
- safe observability wrapper logs a warning on failure
- RM Worker success remains `Succeeded` when diagnostic writes fail
- unsupported filesystem backend fails as `BackendApplyFailed`
- failed backend precondition appears in action-required
- live registry returns `KubernetesNamespaceQuotaLiveAdapter` for generic Longhorn quota mappings
- RM Worker does not select a filesystem adapter for Kubernetes quota operations
- GPFS filesystem and Kubernetes namespace quota adapter paths remain separate
- unknown Kubernetes quota backend is rejected by the live registry

## Testbed Results

The verifier built and deployed image `testbed-registry:5000/dms:phase14` through local registry fallback copy, then deployed:

- `Deployment/dms-api`
- `DaemonSet/dms-rm-agent` on both clusters
- `Deployment/dms-planner`
- `Deployment/dms-rm-worker`

Fresh PostgreSQL databases:

- operational: `dms_phase13_phase14_20260531211910`
- observability: `dms_phase13_obs_phase14_20260531211910`

The Phase 13 smoke flow was re-run first through long-running Planner/RM Worker deployments and completed with `status: ok`.

Important Phase 13 carry-forward evidence:

- API URL: `http://192.168.56.11:30093`
- operational DB URL: `postgresql://appuser:***@192.168.56.11:30432/dms_phase13_phase14_20260531211910`
- observability DB URL: `postgresql://appuser:***@192.168.56.11:30432/dms_phase13_obs_phase14_20260531211910`
- stale claim fixture: request `req_8852581c89274d3f86019290e7f682f2`, run `run_4260ecb7cfd74107bd8ab9def4efb380`, status `StaleClaim`
- GPFS live verification: skipped because the testbed has no IBM GPFS / IBM Storage Scale cluster

Phase 14 runtime hardening summary:

```json
{
  "action_required_match_count": 1,
  "live_resourcequota_hard": {
    "persistentvolumeclaims": "2",
    "requests.storage": "128Mi",
    "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "2",
    "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "128Mi"
  },
  "longhorn_mapping": {
    "backend_type": "longhorn",
    "readiness": {
      "data_management": "Ready",
      "inventory": "Ready",
      "resource_management": "Ready"
    },
    "storage_name": "phase14-longhorn-b"
  },
  "observability_failure": {
    "api_observability_log_seen": true,
    "auth_failure_status": 401,
    "auth_request_persisted": false,
    "diagnostic_events_table": "dropped"
  },
  "quota_request": {
    "request_id": "req_4015b87bc4c64e2b9ad9930bb943c427",
    "resource_key": "cluster-b:dms-phase14-quota-78de9277",
    "status": "Succeeded"
  },
  "rm_worker_observability_log_seen": true,
  "status": "ok",
  "unknown_backend_request": {
    "request_id": "req_de14e278039948a39fddec1f247c3749",
    "resource_key": "phase14-unknown-a:dms-phase14-unknown-78de9277",
    "status": "BackendApplyFailed"
  }
}
```

Verification meaning:

- The verifier dropped `diagnostic_events` in the observability DB after startup. The deployed API still returned HTTP 401 for an unauthenticated request, did not persist an operational request, and logged `observability event write failed`.
- With the observability diagnostic table still absent, the deployed RM Worker processed a Longhorn Kubernetes namespace quota request successfully and logged the same safe-write warning.
- The live ResourceQuota `dms-storage-quota` was created in namespace `dms-phase14-quota-78de9277` on `cluster-b` with `requests.storage=128Mi` and `testbed-longhorn.storageclass.storage.k8s.io/requests.storage=128Mi`.
- A ready storage mapping with typo backend `cephfss` did not fall back to a stub. The filesystem create request finished as `BackendApplyFailed` and appeared once in action-required.
- The verifier submitted requests only through the deployed API and waited for long-running Planner/RM Worker deployments. It did not call `Planner.run_once()` or `RMWorkerRuntime.run_once()` directly for testbed live flows.

Cleanup evidence:

- `Namespace/dms-phase14-quota-78de9277` was deleted on `cluster-b`.
- `Namespace/dms-phase14` was deleted on both clusters.
- Follow-up `kubectl get namespace dms-phase14 --ignore-not-found` on both clusters returned no output.

## Scope Notes

Phase 14 does not implement Data Management live scan/sync/rm, VolcanoJob execution, worker lease heartbeat renewal, maintenance/drain enforcement, production Helm/Kustomize completion, or GPFS live testbed deployment.
