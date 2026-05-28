# DMS Phase 4 Verification

Date: 2026-05-28

## Scope

Phase 4 verified the first real DMS backend mutation from `docs/dms-phase4.md`.

- Planner derives Kubernetes `storage_class_name` from `storage_class_quotas[].storage_name`.
- RM Worker applies `ResourceQuota/dms-storage-quota` to `cluster-b`.
- Applied `ResourceQuota` is read back from Kubernetes and persisted in operational PostgreSQL.
- PVC admission is verified against the live quota using Longhorn on `cluster-b`.

The DMS API process still does not inspect local mount paths. Runtime feasibility is based on worker-role Agent reports, storage mapping sanity, and Kubernetes inventory.

## Local Regression

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" python -m pytest -q
```

Output:

```text
26 passed in 12.95s
```

Additional targeted checks:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" python -m py_compile scripts/phase4_kubernetes_quota_live.py
git diff --check -- src/dms/adapters.py src/dms/backend_registry.py src/dms/config.py src/dms/planner.py src/dms/workers.py tests/test_gpfs_backend.py tests/test_phase4_kubernetes_quota.py scripts/phase4_kubernetes_quota_live.py scripts/verify-phase4-testbed.sh docs/dms-phase4.md
```

Both commands completed with no output.

Note: full `git diff --check` currently reports trailing whitespace in unrelated `AGENTS.md`, which was pre-existing/user-owned in this phase and was not modified.

## Testbed Live Verification

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase4-testbed.sh
```

The script created fresh PostgreSQL databases and used:

```text
DMS_KUBERNETES_INVENTORY_MODE=ssh-kubectl
DMS_KUBERNETES_MUTATION_MODE=ssh-kubectl
DMS_CLUSTER_CONTROL_HOSTS_JSON={"cluster-a":"c1-control","cluster-b":"c2-control"}
```

Output:

```json
{
  "cleanup_namespace_requested": true,
  "cluster_name": "cluster-b",
  "namespace_name": "dms-phase4-ade9c49f",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase4_obs_20260528094541",
  "observability_event_types": [
    "pvc_admission_verification_completed",
    "rm_plan_completed",
    "kubernetes_resourcequota_apply_completed",
    "kubernetes_resourcequota_apply_started"
  ],
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase4_20260528094541",
  "plan_id": "plan_dfc03a308ab64c6d876dcb9616ffd5c7",
  "request_id": "req_57bf5119b0d34e089650b32df6a068a2",
  "request_status": "Succeeded",
  "resource_quota_name": "dms-storage-quota",
  "resource_quota_spec_hard": {
    "persistentvolumeclaims": "2",
    "requests.storage": "128Mi",
    "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "128Mi"
  },
  "resource_quota_status_used_after_allowed_pvc": {
    "persistentvolumeclaims": "1",
    "requests.storage": "64Mi",
    "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "64Mi"
  },
  "resource_status": "Succeeded",
  "result_terminal_status": "Succeeded",
  "status": "ok",
  "storage_class_name": "testbed-longhorn",
  "storage_name": "longhorn-b"
}
```

PVC admission evidence from the same output:

```json
{
  "allowed_pvc": {
    "name": "phase4-allowed-64mi",
    "phase": "Bound",
    "request": "64Mi"
  },
  "over_quota_pvc": {
    "name": "phase4-over-quota-96mi",
    "rejected": true,
    "request": "96Mi",
    "returncode": 1,
    "stderr": "Error from server (Forbidden): error when creating \"STDIN\": persistentvolumeclaims \"phase4-over-quota-96mi\" is forbidden: exceeded quota: dms-storage-quota, requested: requests.storage=96Mi,testbed-longhorn.storageclass.storage.k8s.io/requests.storage=96Mi, used: requests.storage=64Mi,testbed-longhorn.storageclass.storage.k8s.io/requests.storage=64Mi, limited: requests.storage=128Mi,testbed-longhorn.storageclass.storage.k8s.io/requests.storage=128Mi"
  }
}
```

## PostgreSQL Evidence

Command:

```bash
postgres_password="$(ssh c1-control "kubectl -n postgresql get secret postgresql-auth -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d")"
export POSTGRES_PASSWORD="${postgres_password}"
export PHASE4_DB=dms_phase4_20260528094541
export PHASE4_OBS_DB=dms_phase4_obs_20260528094541
/tmp/dms-phase3-venv/bin/python - <<'PY'
import json, os
import psycopg

common = dict(host="192.168.56.11", port=30432, user="appuser", password=os.environ["POSTGRES_PASSWORD"])

def query(db, sql):
    with psycopg.connect(dbname=db, **common) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [desc.name for desc in cur.description]
            return [dict(zip(cols, row)) for row in rows]

def load(value):
    return json.loads(value) if isinstance(value, str) else value

resource = query(os.environ["PHASE4_DB"], "select * from resources order by updated_at desc limit 1")[0]
observed = load(resource["observed_state"])
result = query(os.environ["PHASE4_DB"], "select terminal_status, message from results order by created_at desc limit 1")[0]
events = query(os.environ["PHASE4_OBS_DB"], "select event_type, severity from diagnostic_events order by created_at asc")
print(json.dumps({
    "resource_kind": resource["resource_kind"],
    "resource_key": resource["resource_key"],
    "resource_status": resource["status"],
    "resource_version": resource["version"],
    "resource_quota_spec_hard": observed["resource_quota"]["spec_hard"],
    "resource_quota_status_hard": observed["resource_quota"]["status_hard"],
    "pvc_admission_verification": observed["pvc_admission_verification"],
    "result_terminal_status": result["terminal_status"],
    "result_message": result["message"],
    "observability_events": events,
}, indent=2, sort_keys=True))
PY
```

Output:

```json
{
  "observability_events": [
    {"event_type": "agent_report_accepted", "severity": "INFO"},
    {"event_type": "agent_report_accepted", "severity": "INFO"},
    {"event_type": "agent_report_accepted", "severity": "INFO"},
    {"event_type": "storage_mapping_sanity_check_completed", "severity": "INFO"},
    {"event_type": "kubernetes_resourcequota_apply_started", "severity": "INFO"},
    {"event_type": "kubernetes_resourcequota_apply_completed", "severity": "INFO"},
    {"event_type": "rm_plan_completed", "severity": "INFO"},
    {"event_type": "pvc_admission_verification_completed", "severity": "INFO"}
  ],
  "resource_key": "cluster-b:dms-phase4-ade9c49f",
  "resource_kind": "kubernetes_namespace_quota",
  "resource_quota_spec_hard": {
    "persistentvolumeclaims": "2",
    "requests.storage": "128Mi",
    "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "128Mi"
  },
  "resource_quota_status_hard": {
    "persistentvolumeclaims": "2",
    "requests.storage": "128Mi",
    "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "128Mi"
  },
  "resource_status": "Succeeded",
  "resource_version": 2,
  "result_message": "Kubernetes ResourceQuota live apply completed",
  "result_terminal_status": "Succeeded"
}
```

The persisted `pvc_admission_verification` field contains the same `Bound` 64Mi PVC and rejected 96Mi PVC evidence shown above.

## Re-run

Use a clean timestamped DB pair:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase4-testbed.sh
```

Keep the verification namespace for manual Kubernetes inspection:

```bash
cd /home/mason/workspace/dms
DMS_PHASE4_CLEANUP=false PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase4-testbed.sh
ssh c2-control "kubectl -n <namespace_from_output> get resourcequota dms-storage-quota -o yaml"
ssh c2-control "kubectl -n <namespace_from_output> get pvc"
ssh c2-control "kubectl delete namespace <namespace_from_output> --ignore-not-found"
```

## Verified Matrix

| Area | Result |
| --- | --- |
| PostgreSQL migration | Phase 4 live script created fresh operational/observability PostgreSQL DBs and application startup applied migrations. |
| Mapping guard | `longhorn-b -> cluster-b/testbed-longhorn` reached `Ready` before live mutation. |
| Planner | K8S quota create plan included derived `testbed-longhorn` StorageClass and rendered hard quota values. |
| Namespace | Verification namespace was created by the live adapter because `allow_namespace_create=true`. |
| ResourceQuota apply | `ResourceQuota/dms-storage-quota` was applied to `cluster-b`. |
| ResourceQuota read-back | `spec.hard`, `status.hard`, and `status.used` were read back and stored. |
| PVC success | 64Mi Longhorn PVC reached `Bound`. |
| PVC failure | Additional 96Mi PVC was rejected by Kubernetes admission with `exceeded quota`. |
| Observability | apply started/completed and PVC verification events were written to the observability DB. |
| Cleanup | Test namespace deletion was requested after evidence collection. |

## Notes

- Phase 4 implements Kubernetes namespace quota create/apply only.
- Kubernetes quota update, block, delete, and DB sync are still future phases.
- The verification still submits synthetic Agent reports because the real DMS Agent DaemonSet is not implemented yet.
- The backend mutation and PVC admission checks are live against `cluster-b`, not mock/smoke adapter calls.
