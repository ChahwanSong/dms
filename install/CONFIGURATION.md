# DMS Production Configuration Reference

This document lists the settings that must be reviewed before installing DMS in a real cluster.

## Runtime Environment Variables

Core database and auth:

| Variable | Required | Description |
| --- | --- | --- |
| `DMS_DATABASE_URL` | yes | Operational PostgreSQL URL. Stores requests, plans, runs, resources, storage mappings, agent reports, data jobs. |
| `DMS_OBSERVABILITY_DATABASE_URL` | yes | Observability PostgreSQL URL. Stores diagnostic events. Use a separate DB in production. |
| `DMS_AUTH_SHARED_TOKEN` | yes for bootstrap | Shared bearer token accepted by API and used by agents/scripts. Put it behind ingress auth or mTLS in production. |
| `DMS_DEFAULT_ACTOR` | no | Fallback actor if `x-dms-actor` header is absent. Avoid in production. |

Worker runtime:

| Variable | Default | Description |
| --- | --- | --- |
| `DMS_WORKER_LEASE_SECONDS` | `300` | Claim lease used by Planner/RM/DM worker lifecycle. |
| `DMS_PREVIEW_TTL_SECONDS` | `86400` | Data Management preview TTL. DM live execution is not production-enabled yet. |
| `DMS_AGENT_REPORT_STALE_SECONDS` | `300` | Agent report freshness window used for storage mapping readiness. |
| `DMS_CONTROL_CLUSTER_NAME` | `cluster-a` | Cluster name used for DM readiness and inventory aggregation. |

Kubernetes access:

| Variable | Default | Description |
| --- | --- | --- |
| `DMS_KUBERNETES_INVENTORY_MODE` | `ssh-kubectl` | `kubectl`, `ssh-kubectl`, or `python-client` for read-only inventory. |
| `DMS_KUBERNETES_MUTATION_MODE` | `ssh-kubectl` | `kubectl` or `ssh-kubectl` for namespace/ResourceQuota mutation. |
| `DMS_CLUSTER_KUBECONFIGS_JSON` | unset | JSON object mapping DMS cluster name to kubeconfig path. Required for `kubectl` mode unless using current context. |
| `DMS_CLUSTER_CONTROL_HOSTS_JSON` | unset | JSON object mapping DMS cluster name to SSH host. Required for `ssh-kubectl` mode. |
| `DMS_KUBERNETES_INVENTORY_TIMEOUT_SECONDS` | `10` | Timeout for inventory reads. |
| `DMS_KUBERNETES_MUTATION_TIMEOUT_SECONDS` | `30` | Timeout for ResourceQuota mutations. |

Filesystem backend execution:

| Variable | Default | Description |
| --- | --- | --- |
| `DMS_FILESYSTEM_MUTATION_MODE` | `ssh-host-exec` | CephFS adapter execution mode: `ssh-host-exec` or `local`. GPFS uses its storage mapping `command_runner`. |
| `DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS` | `30` | Filesystem host command timeout. Increase for slow quota read-back. |
| `DMS_FILESYSTEM_EXEC_USE_SUDO` | `true` | Whether CephFS host executor uses sudo for host mutations. |

LDAP/identity:

| Variable | Required | Description |
| --- | --- | --- |
| `DMS_LDAP_URI` | for identity lookup/group management | LDAP URI, for example `ldap://ldap.example.internal:389`. |
| `DMS_LDAP_BASE_DN` | for LDAP | Base DN, for example `dc=example,dc=internal`. |
| `DMS_LDAP_BIND_DN` | for LDAP bind | Bind DN. Store in Secret. |
| `DMS_LDAP_BIND_PASSWORD` | for LDAP bind | Bind password. Store in Secret. |
| `DMS_LDAP_USER_SEARCH_BASE` | optional | Defaults to `ou=people,<baseDN>`. |
| `DMS_LDAP_GROUP_SEARCH_BASE` | optional | Defaults to `ou=groups,<baseDN>`. |
| `DMS_LDAP_USER_FILTER` | optional | Defaults to `(uid={username})`. |
| `DMS_LDAP_TIMEOUT_SECONDS` | optional | Defaults to `5`. |
| `DMS_LDAP_GROUP_GID_START` | optional | GID allocation lower bound for DMS-created groups. |
| `DMS_LDAP_GROUP_GID_END` | optional | GID allocation upper bound for DMS-created groups. |

## Agent Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `DMS_AGENT_API_URL` | yes | URL to DMS API from the agent cluster. |
| `DMS_AGENT_CLUSTER_NAME` | yes | DMS logical cluster name. Must match storage mappings and kubeconfig JSON keys. |
| `DMS_AGENT_WORKER_ROLE` | yes | `RM` or `DM`. |
| `DMS_AGENT_REPORT_INTERVAL_SECONDS` | no | Defaults to 60 seconds. |
| `DMS_AGENT_REPORT_TIMEOUT_SECONDS` | no | Defaults to 5 seconds. |
| `DMS_AGENT_TOOLS` | no | Comma-separated tool probes. Default: `dsync,nsync,drm,dscan,kubectl`. |
| `DMS_AGENT_CREDENTIAL_FILES` | no | Comma-separated credential paths to report. |
| `DMS_AGENT_NETWORK_ENDPOINTS` | no | Comma-separated network endpoints to probe. |
| `DMS_AGENT_IDENTITY_USERS` | no | Comma-separated POSIX users to probe through NSS. |
| `DMS_AUTH_SHARED_TOKEN` | if API token enabled | Shared bearer token for posting reports. |

## Storage Mapping Rules

Every Resource Management request that references `storage_name` is blocked by storage mapping sanity/readiness guards. A mapping must exist, must not be disabled, must not have `sanity_status` `Failed` or `Unknown`, and must have the relevant readiness key set to `Ready`.

Filesystem resource requests require:

- `payload.storage_name`
- `payload.directory_name`
- future `payload.expires_at` on create
- at least two `payload.users` on create/import
- storage mapping `readiness.resource_management=Ready`

Kubernetes namespace quota requests require:

- `payload.cluster_name`
- `payload.namespace_name`
- future `payload.expires_at` on create
- `payload.quota.requests_storage_bytes`
- `payload.quota.pvc_count`
- `payload.storage_class_quotas[].storage_name`
- all referenced storage mappings in the same `cluster_name`

Use `expires_at`, not `expiry_at`. `expiry_at` and `clear_expires_at` are unsupported fields.

## API Examples

Set common variables:

```bash
export DMS_API_URL="https://dms.example.internal"
export DMS_TOKEN="replace-with-secret"
export DMS_ACTOR="admin@example.internal"
```

Health:

```bash
curl -fsS "$DMS_API_URL/healthz"
```

Storage mapping:

```bash
jq -c '.storage_mappings[0]' install/config/storage-mappings.example.json | \
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/storage-mappings" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR" \
  -H "content-type: application/json" \
  --data @-
```

For bulk registration, prefer `install/scripts/register-storage-mappings.sh`.

Kubernetes namespace quota create:

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "alice",
    "payload": {
      "cluster_name": "cluster-a",
      "namespace_name": "alice-scratch",
      "storage_class_quotas": [{"storage_name": "longhorn-a"}],
      "quota": {"requests_storage_bytes": 1000000000000, "pvc_count": 20},
      "expires_at": "2099-01-01T00:00:00Z",
      "allow_namespace_create": true
    }
  }'
```

Filesystem create:

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/filesystems" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "alice",
    "payload": {
      "storage_name": "cephfs-a",
      "directory_name": "alice-project",
      "users": ["alice", "bob"],
      "quota": {"capacity_bytes": 1000000000000, "file_count": 1000000},
      "expires_at": "2099-01-01T00:00:00Z"
    }
  }'
```

Operational queries:

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/requests?requester_id=alice&limit=20" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"

curl -fsS "$DMS_API_URL/api/v1/operations/action-required" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

Bulk setup helpers:

```bash
install/scripts/register-storage-mappings.sh install/config/storage-mappings.example.json
install/scripts/register-default-quota-policies.sh install/config/default-quota-policies.example.json
install/scripts/register-identity-mappings.sh install/config/identity-mappings.example.json
```
