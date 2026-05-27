# DMS Phase 3 Verification

Date: 2026-05-27

## Scope

Phase 3 verified Agent inventory and `storage_name` mapping sanity from
`docs/dms-phase3.md`.

- Agent report persistence, freshness, stale exclusion, and identity mismatch handling
- Kubernetes read-only inventory merged with worker-role Agent reports
- Storage mapping sanity for StorageClass existence, CSI driver match, and RM/DM readiness
- Planner fail-closed behavior for failed or unsafe storage mappings
- Direct Control Mutation audit and active-work conflict behavior for mapping updates

The DMS API process does not inspect API-pod local `mount_path` existence.
Storage execution feasibility is decided from worker-role Agent reports plus
Kubernetes read-only inventory.

## Local Tests

Command:

```bash
cd /home/mason/workspace/dms
python3 -m pytest -q
```

Result:

```text
22 passed in 11.30s
```

The same suite was run in the Phase 3 dependency venv:

```bash
/tmp/dms-phase3-venv/bin/python -m pytest -q
```

Result:

```text
22 passed in 11.30s
```

Covered additions:

- Fresh Agent reports are included in effective inventory; stale reports are persisted as `Stale` and excluded.
- Actor/node mismatch returns 403 and does not create fresh inventory.
- `cluster-a/testbed-cephfs` and `cluster-b/testbed-longhorn` mappings can become `Ready` from Kubernetes inventory and Agent evidence.
- Missing StorageClass and CSI driver mismatch produce `Failed` mapping sanity with actionable error codes.
- API-local backend `mount_path` values do not appear in `sanity_result`.
- Planner rejects failed mapping requests without a plan or backend side effect.
- Ready DM operations use an `agent-inventory` worker pool.
- Active requests block `storage_mapping.upsert` and write a `Conflict` control mutation.

## Testbed Live Smoke

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase3-testbed.sh
```

The script created separate PostgreSQL databases on the testbed PostgreSQL
NodePort and used read-only Kubernetes inventory through:

```text
DMS_KUBERNETES_INVENTORY_MODE=ssh-kubectl
DMS_CLUSTER_CONTROL_HOSTS_JSON={"cluster-a":"c1-control","cluster-b":"c2-control"}
```

Result:

```json
{
  "cephfs_mapping_status": "Ready",
  "cluster_a_storage_class": "testbed-cephfs",
  "cluster_b_storage_class": "testbed-longhorn",
  "control_cluster_name": "cluster-a",
  "csi_mismatch_status": "Failed",
  "failed_mapping_request_status": "Rejected",
  "kubernetes_inventory_mode": "ssh-kubectl",
  "longhorn_mapping_status_before_mismatch_check": "Ready",
  "missing_mapping_status": "Failed",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase3_obs_20260528000045",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase3_20260528000045",
  "status": "ok"
}
```

Additional evidence from the same output:

```json
{
  "ready_scan_job_worker_pool": {
    "selection": "agent-inventory",
    "required_mounts": ["cephfs-a"],
    "sanity_status": "Ready"
  },
  "active_mapping_update_conflict": {
    "kind": "request",
    "status": "Planned"
  },
  "action_required_issue_types": [
    "agent_report_stale",
    "csi_driver_mismatch",
    "missing_dm_readiness",
    "missing_rm_readiness",
    "storage_class_missing",
    "storage_mapping_failed"
  ]
}
```

## Verified Matrix

| Area | Result |
| --- | --- |
| PostgreSQL migration | Phase 3 schema additions applied through application startup migrations on live PostgreSQL. |
| DB separation | Operational DB and observability DB used separate PostgreSQL databases. |
| Kubernetes inventory | `cluster-a/testbed-cephfs` and `cluster-b/testbed-longhorn` were read with `ssh kubectl` and no Kubernetes mutation. |
| Agent ingest | Matching `node:{cluster}:{node}` actors stored Fresh reports; mismatched actor was rejected. |
| Freshness | Ancient report became `Stale`, was queryable, and was excluded from effective worker inventory. |
| CephFS mapping | `cephfs-a -> cluster-a/testbed-cephfs` reached `Ready`. |
| Longhorn mapping | `longhorn-b -> cluster-b/testbed-longhorn` reached `Ready` before the deliberate CSI mismatch check. |
| Missing StorageClass | Missing StorageClass mapping reached `Failed` with `storage_class_missing`. |
| CSI mismatch | Deliberate `cephfs` template over Longhorn StorageClass reached `Failed` with `csi_driver_mismatch`. |
| API pod filesystem | A fake API-local `mount_path` did not affect `sanity_result`; Agent evidence drove readiness. |
| Planner guard | Failed mapping data scan was `Rejected` and no plan was created. |
| Worker pool | Ready data scan stored an `agent-inventory` worker pool. |
| Active conflict | Mapping update during active planned work returned 409 and recorded a `Conflict` control mutation. |
| Action-required | Failed mapping, missing StorageClass, CSI mismatch, stale Agent report, and readiness issues were queryable. |

## Notes

- Phase 3 smoke performs no filesystem, Kubernetes ResourceQuota, namespace, or Volcano mutation.
- Synthetic Agent reports were submitted to exercise inventory logic against real Kubernetes read-only inventory.
- Testbed PostgreSQL password was read from Kubernetes Secret
  `postgresql/postgresql-auth` and was not written to this document.
- The host system Python lacks `psycopg`, so live verification used `/tmp/dms-phase3-venv`.
