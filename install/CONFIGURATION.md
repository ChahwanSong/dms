# DMS 운영 설정 참조

이 문서는 실제 클러스터에 DMS를 설치하기 전에 검토해야 하는 설정을 정리한다.

## 런타임 환경변수

Core database와 인증:

| 변수 | 필수 여부 | 설명 |
| --- | --- | --- |
| `DMS_DATABASE_URL` | 예 | 운영용 PostgreSQL URL. Request, plan, run, resource, storage mapping, agent report, data job을 저장한다. |
| `DMS_OBSERVABILITY_DATABASE_URL` | 예 | Observability PostgreSQL URL. Diagnostic event를 저장한다. 운영 환경에서는 별도 DB를 사용한다. |
| `DMS_AUTH_SHARED_TOKEN` | bootstrap에서는 예 | API가 허용하고 agent/script가 사용하는 shared bearer token. 운영 환경에서는 ingress auth 또는 mTLS 뒤에 둔다. |
| `DMS_DEFAULT_ACTOR` | 아니오 | `x-dms-actor` header가 없을 때 사용할 fallback actor. 운영 환경에서는 피한다. |

Worker runtime:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_WORKER_LEASE_SECONDS` | `300` | Planner/RM/DM worker lifecycle에서 사용하는 claim lease. |
| `DMS_PREVIEW_TTL_SECONDS` | `86400` | Data Management preview TTL. DM live execution은 아직 production-enabled가 아니다. |
| `DMS_AGENT_REPORT_STALE_SECONDS` | `300` | Storage mapping readiness에 사용하는 agent report freshness window. |
| `DMS_CONTROL_CLUSTER_NAME` | `cluster-a` | DM readiness와 inventory aggregation에 사용하는 cluster name. |

Kubernetes 접근:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_KUBERNETES_INVENTORY_MODE` | `ssh-kubectl` | 읽기 전용 inventory 모드. `kubectl`, `ssh-kubectl`, `python-client` 중 하나. |
| `DMS_KUBERNETES_MUTATION_MODE` | `ssh-kubectl` | Namespace/ResourceQuota mutation mode. `kubectl` 또는 `ssh-kubectl`. |
| `DMS_CLUSTER_KUBECONFIGS_JSON` | 설정 안 됨 | DMS cluster name에서 kubeconfig path로 가는 JSON object. Current context를 쓰지 않는 `kubectl` mode에서는 필요하다. |
| `DMS_CLUSTER_CONTROL_HOSTS_JSON` | 설정 안 됨 | DMS cluster name에서 SSH host로 가는 JSON object. `ssh-kubectl` mode에서는 필요하다. |
| `DMS_KUBERNETES_INVENTORY_TIMEOUT_SECONDS` | `10` | Inventory read timeout. |
| `DMS_KUBERNETES_MUTATION_TIMEOUT_SECONDS` | `30` | ResourceQuota mutation timeout. |

Filesystem backend 실행:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_FILESYSTEM_MUTATION_MODE` | `ssh-host-exec` | CephFS adapter execution mode. `ssh-host-exec` 또는 `local`. GPFS는 storage mapping의 `command_runner`를 사용한다. |
| `DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS` | `30` | Filesystem host command timeout. Quota read-back이 느리면 늘린다. |
| `DMS_FILESYSTEM_EXEC_USE_SUDO` | `true` | CephFS host executor가 host mutation에 sudo를 사용할지 여부. |

LDAP/identity:

| 변수 | 필수 여부 | 설명 |
| --- | --- | --- |
| `DMS_LDAP_URI` | identity lookup/group management에 필요 | LDAP URI. 예: `ldap://ldap.example.internal:389`. |
| `DMS_LDAP_BASE_DN` | LDAP에 필요 | Base DN. 예: `dc=example,dc=internal`. |
| `DMS_LDAP_BIND_DN` | LDAP bind에 필요 | Bind DN. Secret에 저장한다. |
| `DMS_LDAP_BIND_PASSWORD` | LDAP bind에 필요 | Bind password. Secret에 저장한다. |
| `DMS_LDAP_USER_SEARCH_BASE` | 선택 | 기본값은 `ou=people,<baseDN>`. |
| `DMS_LDAP_GROUP_SEARCH_BASE` | 선택 | 기본값은 `ou=groups,<baseDN>`. |
| `DMS_LDAP_USER_FILTER` | 선택 | 기본값은 `(uid={username})`. |
| `DMS_LDAP_TIMEOUT_SECONDS` | 선택 | 기본값은 `5`. |
| `DMS_LDAP_GROUP_GID_START` | 선택 | DMS가 생성한 group의 GID allocation lower bound. |
| `DMS_LDAP_GROUP_GID_END` | 선택 | DMS가 생성한 group의 GID allocation upper bound. |

## Agent 환경변수

| 변수 | 필수 여부 | 설명 |
| --- | --- | --- |
| `DMS_AGENT_API_URL` | 예 | Agent cluster에서 접근하는 DMS API URL. |
| `DMS_AGENT_CLUSTER_NAME` | 예 | DMS logical cluster name. Storage mapping과 kubeconfig JSON key와 일치해야 한다. |
| `DMS_AGENT_WORKER_ROLE` | 예 | `RM` 또는 `DM`. |
| `DMS_AGENT_REPORT_INTERVAL_SECONDS` | 아니오 | 기본값은 60초. |
| `DMS_AGENT_REPORT_TIMEOUT_SECONDS` | 아니오 | 기본값은 5초. |
| `DMS_AGENT_TOOLS` | 아니오 | 쉼표로 구분한 tool probe 목록. 기본값: `dsync,nsync,drm,dscan,kubectl`. |
| `DMS_AGENT_CREDENTIAL_FILES` | 아니오 | Report할 credential path의 쉼표로 구분한 목록. |
| `DMS_AGENT_NETWORK_ENDPOINTS` | 아니오 | Probe할 network endpoint의 쉼표로 구분한 목록. |
| `DMS_AGENT_IDENTITY_USERS` | 아니오 | NSS를 통해 probe할 POSIX user의 쉼표로 구분한 목록. |
| `DMS_AUTH_SHARED_TOKEN` | API token이 enabled이면 필요 | Report post에 사용하는 shared bearer token. |

## Storage Mapping 규칙

`storage_name`을 참조하는 모든 Resource Management request는 storage mapping sanity/readiness guard에 의해 차단될 수 있다. Mapping은 존재해야 하고, disabled 상태가 아니어야 하며, `sanity_status`가 `Failed` 또는 `Unknown`이면 안 되고, 관련 readiness key가 `Ready`여야 한다.

Filesystem resource request 요구사항:

- `payload.storage_name`
- `payload.directory_name`
- 생성 시 미래 시점의 `payload.expires_at`
- 생성/import 시 최소 두 개의 `payload.users`
- Storage mapping `readiness.resource_management=Ready`

Kubernetes namespace quota request 요구사항:

- `payload.cluster_name`
- `payload.namespace_name`
- 생성 시 미래 시점의 `payload.expires_at`
- `payload.quota.requests_storage_bytes`
- `payload.quota.pvc_count`
- `payload.storage_class_quotas[].storage_name`
- 같은 `cluster_name`에 있는 모든 참조 storage mapping

`expiry_at`이 아니라 `expires_at`을 사용한다. `expiry_at`과 `clear_expires_at`은 지원하지 않는 field다.

## API 예시

공통 변수를 설정한다.

```bash
export DMS_API_URL="https://dms.example.internal"
export DMS_TOKEN="replace-with-secret"
export DMS_ACTOR="admin@example.internal"
```

Health 확인:

```bash
curl -fsS "$DMS_API_URL/healthz"
```

Storage mapping 등록:

```bash
jq -c '.storage_mappings[0]' install/config/storage-mappings.example.json | \
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/storage-mappings" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR" \
  -H "content-type: application/json" \
  --data @-
```

Bulk registration에는 `install/scripts/register-storage-mappings.sh` 사용을 권장한다.

Kubernetes namespace quota 생성:

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

Filesystem 생성:

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

운영 조회:

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/requests?requester_id=alice&limit=20" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"

curl -fsS "$DMS_API_URL/api/v1/operations/action-required" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

일괄 설정 helper:

```bash
install/scripts/register-storage-mappings.sh install/config/storage-mappings.example.json
install/scripts/register-default-quota-policies.sh install/config/default-quota-policies.example.json
install/scripts/register-identity-mappings.sh install/config/identity-mappings.example.json
```
