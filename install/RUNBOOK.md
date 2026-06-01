# DMS Operations Runbook

## Daily Health Checks

Run these from an operator workstation with DMS API access.

```bash
export DMS_API_URL="https://dms.example.internal"
export DMS_TOKEN="replace-with-secret"
export DMS_ACTOR="operator@example.internal"

install/scripts/verify-install.sh
```

Manual checks:

```bash
kubectl -n dms get pods,jobs,svc
kubectl -n dms logs deploy/dms-planner --tail=100
kubectl -n dms logs deploy/dms-rm-worker --tail=100
curl -fsS "$DMS_API_URL/api/v1/operations/action-required" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

Expected steady state:

- `dms-api` available.
- `dms-planner` running.
- `dms-rm-worker` running.
- `dms-dm-worker` scaled to 0 until live DM execution is implemented.
- Agent reports fresh for every storage-capable RM/DM node.
- Storage mappings used by production requests show `readiness.resource_management=Ready`.
- No unresolved action-required items.

## Storage Mapping Readiness

If a request is rejected with `storage_mapping_sanity`, inspect mapping state:

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/storage-mappings" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

Refresh one mapping:

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/storage-mappings/<storage_name>:check" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

Common causes:

- Agent DaemonSet is not running on a node that sees the mount.
- `DMS_AGENT_CLUSTER_NAME` does not match the storage mapping `cluster_name`.
- `storage_class_name` or `csi_driver` does not match the live StorageClass.
- Agent reports are stale; check `DMS_AGENT_REPORT_STALE_SECONDS`.
- Kubernetes inventory mode cannot read target cluster.

## Kubernetes Namespace Quota Incidents

Check a namespace quota:

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas/<cluster>/<namespace>:check" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR" \
  -H "content-type: application/json" \
  --data '{"requester_id":"operator","payload":{"include_effective_quota":true}}'
```

Audit a scope:

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas:audit" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "operator",
    "payload": {
      "scope": {"cluster_name": "cluster-a"},
      "include_non_dms": true,
      "include_usage_pressure": true
    }
  }'
```

If live ResourceQuota is missing but DB desired state exists, use update or block/unblock to reapply. If live state was intentionally changed by an operator, run sync only after reviewing drift.

## Expiry Handling

List expiring filesystem resources:

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/filesystems/expiring?status=expired" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

List expiring Kubernetes namespace quota resources:

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/kubernetes/namespace-quotas/expiring?status=expired" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

Dry-run sweep:

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas:expiration-sweep" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "operator",
    "payload": {
      "dry_run": true,
      "action": "block",
      "scope": {"cluster_name": "cluster-a"},
      "max_targets": 100
    }
  }'
```

Run a real sweep only after reviewing dry-run targets. System/admin resources are skipped by policy.

## Worker Recovery

Inspect stale runs:

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/runs/stale" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

The current RM worker marks stale runs so another worker can continue planning/execution, but long-running backend calls still need operator review if a side effect may have happened. Check `action-required` and backend live state before re-submitting requests.

## PostgreSQL Backup

Back up both databases before upgrades and before applying schema-changing code.

```bash
pg_dump "$DMS_DATABASE_URL" > dms-operational-$(date +%Y%m%d%H%M%S).sql
pg_dump "$DMS_OBSERVABILITY_DATABASE_URL" > dms-observability-$(date +%Y%m%d%H%M%S).sql
```

Restore into a staging DB first and run `dms migrate` with the new image before production rollout.

## Upgrade Procedure

1. Stop new external writes at ingress if possible.
2. Scale `dms-planner` and `dms-rm-worker` to 0.
3. Wait for active RM runs to finish or become stale/action-required.
4. Back up PostgreSQL.
5. Apply the new image to `dms-migrate` Job and run it.
6. Roll `dms-api`.
7. Roll `dms-planner` and `dms-rm-worker`.
8. Run `install/scripts/verify-install.sh`.
9. Re-enable external writes.

Maintenance/drain control state exists in the DB model, but full runtime enforcement may lag the latest design. Use Kubernetes scaling and ingress controls as the operational drain mechanism.

## Known Production Gaps

- Data Management live execution must remain disabled until a non-stub Volcano adapter is installed and verified.
- Production Helm/Kustomize packaging is not complete; manifests here are explicit YAML templates.
- Worker lease renewal for very long backend calls should be reviewed before running operations expected to exceed `DMS_WORKER_LEASE_SECONDS`.
- Multiple local filesystem RM workers with disjoint mounts are unsafe without storage-aware worker claiming.
- WEKA filesystem backend is not implemented yet.
