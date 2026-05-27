# DMS Phase 3 Implementation Prompt

이 문서는 `docs/dms-phase1.md`와 `docs/dms-phase2.md` 완료 이후 세 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 3의 목표는 실제 backend mutation을 넓히기 전에, DMS가 어떤 cluster, node, storage mapping, worker role, tool capability를 신뢰하고 사용할 수 있는지 판단하는 inventory 기반을 닫는 것이다.

Phase 3에서는 Phase 2의 PostgreSQL live baseline과 LDAP-only Identity Mapping 위에 Agent inventory와 `storage_name` mapping sanity를 구현한다. 이 단계가 끝나야 Kubernetes namespace storage quota live adapter, filesystem quota adapter, Data Management Volcano live execution이 안전하게 이어질 수 있다.

## Phase 3 목표

Phase 3의 핵심 기능은 다음 하나다.

**Agent inventory와 `storage_name` mapping sanity**

구현 완료 기준은 다음과 같다.

- DMS Agent report를 PostgreSQL source-of-truth에 저장하고 freshness를 계산한다.
- Agent report와 Kubernetes read-only inventory를 결합해 effective inventory를 만든다.
- `storage_name` mapping 등록/수정 시 backend template, cluster, StorageClass, CSI driver, mount, worker role readiness를 검증한다.
- Storage mapping sanity 결과를 운영용 PostgreSQL에 versioned state로 저장하고 Operational Query API로 조회한다.
- Planner는 존재하지 않거나 sanity가 실패한 mapping을 참조하는 RM/DM plan을 생성하지 않는다.
- 테스트베드에서 `cluster-a`의 `testbed-cephfs` StorageClass와 `cluster-b`의 `testbed-longhorn` StorageClass를 정상 mapping으로 live 검증한다.
- 테스트베드에서 존재하지 않는 StorageClass 또는 CSI driver 불일치 mapping을 실패 mapping으로 live 검증한다.
- Phase 3 검증은 Kubernetes, filesystem, storage backend에 mutation을 수행하지 않는다. 모든 backend 확인은 read-only inventory와 Agent report 기준이다.

## 왜 Phase 3A를 먼저 하는가

Phase 2 이후 DMS는 운영 DB와 LDAP identity 기반을 갖췄지만, 아직 다음 질문에 운영적으로 답하지 못한다.

- 이 `storage_name`이 실제 어느 cluster의 어느 StorageClass와 연결되는가?
- 해당 StorageClass가 target cluster에 실제 존재하는가?
- CSI provisioner가 backend template의 기대값과 일치하는가?
- RM Worker role에서 이 storage를 관리할 node-local mount 또는 quota capability가 관측되는가?
- DM Worker role에서 이 storage를 data operation 대상으로 사용할 mount, tool, credential, network evidence가 관측되는가?
- Agent report가 너무 오래되어 scheduler/worker 선택에 사용하면 안 되는 상태인가?

이 판단 없이 Kubernetes ResourceQuota live mutation이나 Volcano/mpifileutils 실행을 먼저 구현하면, 잘못된 mapping 또는 stale inventory를 기준으로 backend side effect가 발생할 수 있다. 따라서 Phase 3은 live mutation이 아니라 실행 대상 선택의 안전성을 먼저 닫는다.

## 현재 전제

Phase 1은 다음 골격을 제공한다.

- Agent report ingestion skeleton
- `agent_reports` table
- `storage_mappings` table과 `storage_name` uniqueness
- `StorageInventoryAdapter` interface
- `BackendAdapterRegistry`
- RM/DM Planner와 Worker runtime skeleton
- Operational Query skeleton

Phase 2는 다음 기반을 완료했다.

- 테스트베드 PostgreSQL live baseline
- 운영 DB와 observability/log DB 분리
- LDAP direct read-only Identity Mapping
- PostgreSQL + LDAP live smoke test

테스트베드는 다음 시스템을 제공한다.

- `cluster-a`: DMS cluster 역할이며, 동시에 self-managed RM target으로도 사용할 수 있다.
- `cluster-b`: managed cluster 역할이며, RM Worker/Agent inventory 검증 대상이다.
- `cluster-a` PostgreSQL
- OpenLDAP/SSSD
- Volcano
- Rook/CephFS on `cluster-a`
- `testbed-cephfs` StorageClass on `cluster-a`
- Longhorn on `cluster-b`
- `testbed-longhorn` StorageClass on `cluster-b`

Phase 3 구현은 테스트베드에 특화되면 안 된다. 테스트베드는 구현 정확성 검증 용도이며, 구현은 실제 데이터센터의 멀티 Kubernetes cluster와 node-local Agent report를 전제로 한다.

테스트베드 topology 해석:

- DMS control cluster는 `cluster-a`다.
- `cluster-a`는 DMS control plane과 DM Worker를 실행하는 cluster이면서, 자기 자신의 Kubernetes/StorageClass resource를 관리하는 self-managed RM target이 될 수 있다.
- `cluster-b`는 DMS control plane 외부의 managed cluster로 취급한다.
- RM readiness는 target cluster별로 판단한다. `cluster-a` CephFS mapping은 `cluster-a` RM Agent evidence를, `cluster-b` Longhorn mapping은 `cluster-b` RM Agent evidence를 사용한다.
- DM readiness는 DMS control cluster인 `cluster-a`의 DM Agent evidence를 기준으로 판단한다. DM Worker를 `cluster-b`에 배포한다고 가정하지 않는다.

## 핵심 원칙

### 1. Agent report는 node-local capability evidence다

DMS Agent는 Worker runtime과 논리적으로 분리된 capability reporter다.

Agent report는 다음 정보를 표현할 수 있어야 한다.

- `cluster_name`
- `node_name`
- `node_uid`
- `worker_role`: `RM` 또는 `DM`
- mount evidence
- CSI evidence
- tool capability
- credential evidence
- network reachability evidence
- identity evidence
- probe timestamp
- report schema version

Agent는 resource desired state를 변경하지 않는다. Agent report는 backend mutation의 원인이 아니라, Planner/Worker가 실행 가능성을 판단하는 read-only evidence다.

### 2. Agent identity mismatch는 inventory에 반영하지 않는다

Agent report를 제출하는 actor는 보고된 node identity와 일치해야 한다.

기본 actor 형식:

```text
node:{cluster_name}:{node_name}
```

actor가 보고된 node와 일치하지 않으면 report를 `agent_reports`에 Fresh inventory로 저장하지 않고 observability diagnostic event만 남긴다. Phase 1의 skeleton 동작을 유지하되, Phase 3에서는 이 실패가 storage mapping sanity에 섞이지 않음을 테스트해야 한다.

### 3. Fresh report만 authoritative inventory에 사용한다

Agent report는 시간이 지나면 stale이 된다.

구현은 다음 설정을 제공해야 한다.

- `DMS_AGENT_REPORT_STALE_SECONDS`
- `DMS_CONTROL_CLUSTER_NAME`

기본값은 300초로 시작한다. 테스트에서는 더 짧은 값을 주입할 수 있어야 한다.

Effective inventory 계산 시 stale report는 제외한다. 단, query 응답에는 stale report를 숨기지 말고 `freshness_status=Stale`과 stale reason을 표시한다.

Freshness 판정은 query/effective inventory 계산 시점의 `reported_at` 또는 `received_at` 기준으로 수행한다. 구현은 stale report를 계산에서 제외해야 하며, stale 상태가 처음 관측되면 `stale_at`과 `freshness_status=Stale`을 운영 DB에 기록할 수 있어야 한다. Background sweeper가 없어도 Phase 3 검증은 query/recheck path에서 stale 계산과 persisted update를 확인할 수 있어야 한다.

### 4. API pod filesystem observation은 DMS 판단 입력이 아니다

DMS API server pod 또는 control-plane process에서 어떤 `mount_path`가 보이든, 그 local filesystem state는 DMS의 storage mapping sanity, readiness, scheduling, Planner guard 판단에 사용하지 않는다.

API server는 request validation, persistence, control mutation, query를 처리하는 frontend/control-plane component다. 실제 RM/DM 실행 가능성은 worker role별 DMS Agent report와 Kubernetes read-only inventory를 기준으로만 판단한다.

구현은 API process에서 `mount_path` existence를 검사하지 않는다. 디버깅 목적으로 API pod의 local path 정보를 별도 diagnostic event에 남기는 것은 허용할 수 있지만, 그 값은 `sanity_result`, `readiness`, `effective_inventory`, `planner` decision에 포함하면 안 된다.

### 5. Storage mapping은 Direct Control Mutation이다

`storage_mapping.upsert`는 Resource/Data request lifecycle에 넣지 않는다. Direct Control Mutation audit record로 추적한다.

Storage mapping 변경은 같은 `storage_name`을 참조하는 active request, plan, run, Data Management job이 있으면 거부해야 한다. 거부 결과도 `control_mutations`에 `Conflict` 또는 동등한 실패 상태로 남긴다.

Active work conflict 기준:

- request/plan/run은 terminal lifecycle state가 아닌 상태를 active로 본다.
- Data Management job은 `Succeeded`, `Failed`, `Cancelled`, `TimedOut`, `AuthorizationFailed`, `PreflightFailed`, `PreviewExpired`가 아닌 상태를 active로 본다.
- filesystem/data operation은 payload의 top-level `storage_name`을 기준으로 한다.
- Kubernetes namespace quota operation은 `storage_class_quotas[].storage_name` 전체를 기준으로 한다. namespace-wide quota만 있고 `storage_class_quotas[]`가 비어 있으면 특정 `storage_name` conflict로 보지 않는다.
- `sync`가 source/destination storage를 분리해 지원되는 phase에서는 source와 destination 양쪽 storage mapping을 모두 conflict 대상으로 본다.

### 6. Planner는 unsafe mapping을 backend plan으로 만들지 않는다

Phase 3 이후 Planner는 storage-backed operation에서 다음 조건을 검사해야 한다.

- operation이 storage mapping을 요구하는 경우 payload 또는 `storage_class_quotas[]`의 `storage_name`이 운영 DB에 존재한다.
- mapping이 disabled/deleted 상태가 아니어야 한다.
- mapping sanity가 `Failed`이면 plan을 만들지 않고 `Rejected` 또는 `Failed` terminal result를 남긴다.
- mapping sanity가 `Unknown`이면 operation 특성에 따라 `Blocked` 또는 `Rejected`로 처리한다. 실 backend side effect가 가능한 operation은 fail-closed가 기본이다.
- Data Management operation은 DM readiness가 없으면 plan을 만들지 않는다.
- Resource Management operation은 RM readiness가 없으면 plan을 만들지 않는다.

개발용 stub 테스트가 필요한 경우에도 명시적인 test storage mapping을 등록해야 한다. 존재하지 않는 mapping을 조용히 `unmapped`로 통과시키는 동작은 Phase 3 이후 운영 경로에서 허용하지 않는다.

## Phase 3에서 하지 않을 것

다음은 Phase 3 범위가 아니다.

- 실제 filesystem directory create/update/block
- 실제 filesystem quota 적용
- 실제 Kubernetes `ResourceQuota` create/update/delete
- 실제 Kubernetes namespace create/delete
- 실제 VolcanoJob create/watch/terminate
- mpifileutils image build 또는 실행
- Data Management POSIX permission runtime preflight
- mTLS Ingress live validation
- rolling upgrade, shutdown/startup recovery runbook 구현

이 항목들은 Phase 3 이후 단계로 분리한다.

권장 다음 순서:

1. Phase 3 완료: Agent inventory와 storage mapping sanity
2. 다음 phase: Kubernetes namespace storage quota live adapter
3. 그 다음 phase: Data Management preflight와 Volcano/mpifileutils live execution

## 구현 범위

### Agent Report Model 보강

현재 `AgentReport`가 부족하면 backward-compatible하게 확장한다.

필수 표현:

- `schema_version`
- `reported_at`
- `cluster_name`
- `node_name`
- `node_uid`
- `worker_role`
- `mounts[]`
- `csi[]`
- `tools[]`
- `credentials[]`
- `networks[]`
- `identity_evidence`

Mount evidence 예:

```json
{
  "storage_name": "cephfs-a",
  "mount_path": "/mnt/dms/cephfs-a",
  "filesystem_type": "ceph",
  "source": "rook-ceph/testbed-cephfs",
  "readable": true,
  "writable": true,
  "quota_capability": {
    "supported": false,
    "mode": "none",
    "reason": "testbed CSI mount does not expose quota xattr write permission"
  }
}
```

CSI evidence 예:

```json
{
  "driver": "rook-ceph.cephfs.csi.ceph.com",
  "storage_classes": ["testbed-cephfs"],
  "node_plugin_ready": true
}
```

Tool evidence 예:

```json
{
  "name": "dscan",
  "version": "0.11.1",
  "path": "/usr/local/bin/dscan",
  "healthy": true
}
```

초기 구현에서 기존 `tools: list[str]` 형태를 유지해야 한다면, repository 저장 시 정규화해서 effective inventory에서는 structured tool evidence로 다룬다.

### Agent Report Persistence

`agent_reports`는 최신 report와 history query를 모두 지원해야 한다.

필수 저장 정보:

- `report_id`
- `cluster_name`
- `node_name`
- `node_uid`
- `worker_role`
- `report_json`
- `freshness_status`
- `reported_at`
- `received_at`
- `stale_at`
- `schema_version`
- `validation_status`
- `validation_error`

기존 schema에 없는 column은 expand-compatible migration으로 추가한다. SQLite와 PostgreSQL 양쪽에서 migration이 동작해야 한다.

### Kubernetes Read-Only Inventory Adapter

Phase 3에서는 Kubernetes API를 read-only로 조회하는 adapter를 구현한다.

필수 조회:

- cluster별 Node name/UID/labels/taints
- cluster별 StorageClass name/provisioner/parameters/reclaimPolicy/volumeBindingMode
- cluster별 CSI driver 또는 CSI node visibility 중 구현 가능한 최소 정보
- namespace existence는 mapping sanity에는 필수는 아니지만, 다음 phase를 위해 read path를 둘 수 있다.

구현 방식:

- 운영 배포에서는 in-cluster config 또는 mounted kubeconfig를 사용할 수 있어야 한다.
- 테스트베드에서는 `/home/mason/workspace/testbed/shared_directory/.testbed-state/kubeconfigs/{cluster}.conf`를 사용할 수 있어야 한다.
- adapter는 write, apply, patch, delete를 수행하지 않는다.
- Kubernetes Python client를 쓰거나, 내부적으로 `kubectl`을 호출할 수 있다. 단, parsing은 가능한 JSON 기반으로 수행한다.

권장 설정:

- `DMS_CLUSTER_KUBECONFIGS_JSON`
- `DMS_KUBERNETES_INVENTORY_TIMEOUT_SECONDS`
- `DMS_KUBERNETES_INVENTORY_MODE`: `python-client`, `kubectl`, `ssh-kubectl` 중 하나. 호스트에 `kubectl`이 없는 테스트베드에서는 `ssh-kubectl`로 control node의 `kubectl`을 사용할 수 있어야 한다.
- `DMS_CLUSTER_CONTROL_HOSTS_JSON`: `ssh-kubectl` mode에서 cluster별 control node SSH alias를 지정한다.

예:

```json
{
  "cluster-a": "/home/mason/workspace/testbed/shared_directory/.testbed-state/kubeconfigs/cluster-a.conf",
  "cluster-b": "/home/mason/workspace/testbed/shared_directory/.testbed-state/kubeconfigs/cluster-b.conf"
}
```

`DMS_CLUSTER_CONTROL_HOSTS_JSON` 예:

```json
{
  "cluster-a": "c1-control",
  "cluster-b": "c2-control"
}
```

### Effective Inventory Service

`StorageInventoryAdapter.effective_inventory()` skeleton을 실제 service로 확장한다.

Effective inventory는 다음 source를 결합한다.

- 운영 DB의 latest fresh Agent report
- Kubernetes read-only inventory
- storage mappings
- freshness policy

출력은 최소한 다음 구조를 가져야 한다.

```json
{
  "clusters": {
    "cluster-a": {
      "nodes": [],
      "storage_classes": [],
      "csi_drivers": [],
      "agent_reports": {
        "fresh": [],
        "stale": []
      }
    }
  },
  "worker_roles": {
    "RM": {
      "cluster-a": {
        "nodes": [],
        "mounts_by_storage_name": {}
      }
    },
    "DM": {
      "cluster-a": {
        "nodes": [],
        "mounts_by_storage_name": {},
        "tools": []
      }
    }
  }
}
```

구체 schema는 구현자가 조정할 수 있지만, storage mapping sanity와 Operational Query가 필요한 정보를 잃으면 안 된다.

### Storage Mapping Sanity

Storage mapping sanity는 `storage_name` mapping의 사용 가능성을 검증한다.

입력:

- `StorageMappingInput`
- Kubernetes read-only inventory
- fresh Agent report effective inventory

검증 항목:

- `storage_name` unique
- `backend_template.backend_type` 존재
- `cluster_name`이 지정된 경우 해당 cluster inventory 존재
- `storage_class_name`이 지정된 경우 target cluster에 StorageClass 존재
- StorageClass provisioner와 `backend_template.csi_driver` 또는 backend별 expected provisioner 일치
- backend template의 필수 field 존재
- RM readiness: target cluster의 fresh RM Agent가 해당 storage/mount/quota evidence를 보고하는지
- DM readiness: DMS cluster의 fresh DM Agent가 해당 storage/mount/tool/network evidence를 보고하는지
- stale Agent report 제외 여부
- API-local path observation은 수행하지 않으며, DMS 판단 입력으로 사용하지 않음

Sanity status는 다음 범주를 표현할 수 있어야 한다.

| Status | 의미 |
| --- | --- |
| `Ready` | 필수 mapping, Kubernetes inventory, 필요한 worker role evidence가 충족됨 |
| `Degraded` | 핵심 mapping은 맞지만 일부 optional evidence 또는 role readiness가 부족함 |
| `Unknown` | 아직 authoritative Agent inventory가 없어 판단이 보류됨 |
| `Failed` | StorageClass 불일치, CSI driver 불일치, 필수 template 누락, stale-only inventory 등 backend side effect를 막아야 하는 상태 |

Top-level `sanity_status`는 기존 `storage_mappings.sanity_status` field를 유지해 저장한다. 자세한 결과는 JSON field로 별도 저장한다.

권장 `sanity_result` shape:

```json
{
  "storage_name": "cephfs-a",
  "status": "Ready",
  "checked_at": "2026-05-27T23:40:00+09:00",
  "kubernetes_observed": {
    "cluster_name": "cluster-a",
    "storage_class_name": "testbed-cephfs",
    "storage_class_exists": true,
    "provisioner": "rook-ceph.cephfs.csi.ceph.com"
  },
  "agent_observed": {
    "fresh_reports": 2,
    "stale_reports": 0,
    "rm_readiness": "Ready",
    "dm_readiness": "Ready"
  },
  "readiness": {
    "resource_management": "Ready",
    "data_management": "Ready",
    "inventory": "Ready"
  },
  "checks": [
    {
      "name": "storage_class_exists",
      "status": "Passed"
    }
  ],
  "warnings": [],
  "errors": []
}
```

### Storage Mapping Persistence 보강

`storage_mappings`에 부족한 필드가 있으면 migration을 추가한다.

권장 추가 field:

- `sanity_result`
- `sanity_checked_at`
- `readiness`
- `disabled_at`
- `disabled_reason`
- `updated_by`

`disabled_at`과 `disabled_reason`은 Phase 3에서 필수로 추가한다. Planner guard가 disabled mapping을 fail-closed로 처리해야 하므로 disabled state는 optional extension이 아니다.

기존 Phase 1/2 tests를 깨뜨리지 않도록 기존 column과 API response는 유지한다.

### Storage Mapping Upsert 동작

`POST /api/v1/resource-management/storage-mappings` 또는 기존 endpoint는 upsert 후 sanity check를 수행해야 한다.

기대 동작:

- 인증/인가를 통과한 actor만 수행 가능하다.
- active request/plan/run/data job이 같은 `storage_name`을 참조하면 거부한다.
- payload validation 실패는 저장하지 않는다.
- sanity check가 `Ready`, `Degraded`, `Unknown`, `Failed` 중 하나로 저장된다.
- `Failed` mapping은 저장할 수 있다. 운영자가 실패 상태와 이유를 조회해야 하기 때문이다.
- 저장 결과와 before/after diff는 `control_mutations`에 남긴다.
- observability event에는 mapping status와 주요 error reason을 남긴다.

### Manual Sanity Recheck

운영자는 storage mapping을 변경하지 않고 sanity만 다시 계산할 수 있어야 한다.

권장 endpoint:

```text
POST /api/v1/resource-management/storage-mappings/{storage_name}:check
```

이 operation은 Direct Control Mutation 또는 operational action으로 기록한다. Resource/Data request lifecycle에는 넣지 않는다.

### Planner Integration

Planner는 storage-backed request를 plan으로 바꾸기 전에 mapping sanity를 확인한다.

RM operations:

- filesystem create/update/block/initialize/delete/import/assign/check
- Kubernetes namespace quota create/update/block/delete/sync

DM operations:

- data sync/rm/scan

필수 처리:

- operation이 storage mapping을 요구하는데 `storage_name`이 없으면 `Rejected`
- 요구된 `storage_name` mapping이 없으면 `Rejected`
- mapping `sanity_status=Failed`이면 `Rejected` 또는 `Failed`
- RM readiness가 없는데 RM operation이면 `Blocked` 또는 `Rejected`
- DM readiness가 없는데 DM operation이면 `Blocked` 또는 `Rejected`
- failure result에는 `backend_side_effect=false`, `storage_name`, `sanity_status`, `sanity_result` summary를 포함한다.

Operation별 mapping requirement:

| Operation class | Mapping requirement |
| --- | --- |
| filesystem create/update/block/initialize/delete/import/assign/check | payload top-level `storage_name` 필수 |
| data sync/rm/scan | payload top-level `storage_name` 필수. source/destination storage 분리가 도입되면 양쪽 mapping 필수 |
| Kubernetes namespace quota with `storage_class_quotas[]` | 각 entry의 `storage_name` 필수 |
| Kubernetes namespace-wide quota only | `storage_name` 필수 아님. `storage_class_quotas[]`가 없거나 비어 있으면 namespace-wide quota만 plan 가능 |

Kubernetes namespace quota operation에서 `storage_class_quotas[]`가 여러 개라면 모든 `storage_name` mapping을 확인해야 한다. Phase 3 초기 구현에서 단일 `storage_name`만 지원한다면, 여러 entry payload는 명시적으로 `Rejected` 처리하고 문서화한다.

### Operational Query API

Phase 3에서는 다음 query를 제공하거나 기존 Operational Query에 필터를 추가한다.

권장 endpoint:

- `GET /api/v1/operations/inventory`
- `GET /api/v1/operations/agent-reports`
- `GET /api/v1/operations/agent-reports?freshness=stale`
- `GET /api/v1/operations/storage-mappings`
- `GET /api/v1/operations/storage-mappings/{storage_name}`
- `GET /api/v1/operations/action-required`

`action-required`에는 최소한 다음 이슈가 표시되어야 한다.

- `storage_mapping_failed`
- `storage_mapping_unknown`
- `agent_report_stale`
- `missing_rm_readiness`
- `missing_dm_readiness`
- `storage_class_missing`
- `csi_driver_mismatch`

### Observability

Phase 3에서 남겨야 하는 diagnostic event:

- agent report accepted
- agent report rejected due to identity mismatch
- agent report marked stale
- inventory refresh started/completed/failed
- storage mapping sanity check started/completed/failed
- planner rejected request due to mapping sanity

Diagnostic event는 observability/log DB에 저장한다. Critical lifecycle decision은 운영용 PostgreSQL에도 결과로 남겨야 한다.

## 데이터 모델 보강

구체 schema는 구현자가 정하되, 다음 정보를 표현 가능해야 한다.

### `agent_reports`

- report identity
- cluster/node/role identity
- raw report JSON
- normalized capability summary
- freshness status
- validation status
- reported/received/stale timestamps

### `storage_mappings`

- `storage_name`
- backend template
- cluster/storage class mapping
- version
- top-level sanity status
- detailed sanity result JSON
- readiness JSON
- updated actor/time
- disabled state if implemented

### `control_mutations`

Storage mapping upsert/check/recheck 결과:

- mutation class
- operation
- target key
- actor
- before state
- after state
- status
- result/error summary

### `diagnostic_events`

Inventory and sanity correlation:

- `storage_name`
- `cluster_name`
- `node_name`
- `worker_role`
- `request_id` if planner rejection is tied to a request
- `mutation_id` if direct control mutation is tied to storage mapping upsert/check

## 테스트베드 검증 기준

Phase 3 live verification은 테스트베드 메타데이터를 먼저 확인한 뒤 진행한다.

읽어야 하는 문서:

- `/home/mason/workspace/testbed/testbed-info.json`
- `/home/mason/workspace/testbed/testbed-summary.json`
- `/home/mason/workspace/testbed/TOPOLOGY.md`
- `/home/mason/workspace/testbed/PostgreSQL.md`
- `/home/mason/workspace/testbed/OpenLDAP-SSSD.md`
- `/home/mason/workspace/testbed/CephFS.md`
- `/home/mason/workspace/testbed/Longhorn.md`

테스트베드 관련 사실:

- `cluster-a`에는 `testbed-cephfs` StorageClass가 있다.
- `cluster-a`는 DMS control cluster이면서 self-managed RM target으로 사용할 수 있다.
- `cluster-b`는 managed cluster이며 `testbed-longhorn` StorageClass가 있다.
- `cluster-a`의 CephFS CSI provisioner는 `rook-ceph.cephfs.csi.ceph.com`이다.
- `cluster-b`의 Longhorn CSI provisioner는 `driver.longhorn.io`이다.
- `cluster-a`와 `cluster-b` kubeconfig는 shared directory에 있다.
- PostgreSQL은 `cluster-a` NodePort `192.168.56.11:30432`로 접근 가능하다.

### 최소 live smoke

1. PostgreSQL operational/observability DB를 Phase 2 방식처럼 분리 생성한다.
2. migration을 적용한다.
3. `cluster-a`와 `cluster-b` Kubernetes read-only inventory를 수집한다.
4. `cluster-a` node UID를 조회해 self-managed RM Agent report와 DM Agent report를 제출한다.
5. `cluster-b` node UID를 조회해 managed-cluster RM Agent report를 제출한다.
6. `cluster-a` CephFS 정상 mapping을 등록한다.

예시 정상 mapping:

```json
{
  "storage_name": "cephfs-a",
  "backend_template": {
    "backend_type": "cephfs",
    "filesystem_name": "testbed-cephfs",
    "mount_path": "/mnt/dms/cephfs-a",
    "quota_capability": {
      "mode": "none",
      "reason": "Phase 3 inventory only"
    },
    "csi_driver": "rook-ceph.cephfs.csi.ceph.com",
    "data_network": "testbed-hostonly"
  },
  "cluster_name": "cluster-a",
  "storage_class_name": "testbed-cephfs"
}
```

예상:

- Kubernetes inventory에서 `testbed-cephfs` StorageClass를 찾는다.
- CSI provisioner가 template과 일치한다.
- fresh Agent report가 있으면 readiness가 `Ready` 또는 `Degraded`로 계산된다.
- top-level `sanity_status`가 `Ready` 또는 명확한 warning이 있는 `Degraded`가 된다.

7. `cluster-b` Longhorn 정상 mapping을 등록한다.

예시 Longhorn 정상 mapping:

```json
{
  "storage_name": "longhorn-b",
  "backend_template": {
    "backend_type": "longhorn",
    "mount_path": "/mnt/dms/longhorn-b",
    "quota_capability": {
      "mode": "kubernetes-resourcequota",
      "reason": "Phase 3 inventory only; live ResourceQuota mutation is next phase"
    },
    "csi_driver": "driver.longhorn.io",
    "data_network": "testbed-hostonly"
  },
  "cluster_name": "cluster-b",
  "storage_class_name": "testbed-longhorn"
}
```

예상:

- Kubernetes inventory에서 `testbed-longhorn` StorageClass를 찾는다.
- CSI provisioner가 `driver.longhorn.io`로 template과 일치한다.
- fresh `cluster-b` RM Agent report가 있으면 resource management readiness가 `Ready` 또는 `Degraded`로 계산된다.
- DM readiness는 DMS control cluster인 `cluster-a`의 DM Agent evidence를 기준으로 판단한다.

8. 실패 mapping을 등록한다.

예시 실패 mapping:

```json
{
  "storage_name": "missing-longhorn-b",
  "backend_template": {
    "backend_type": "longhorn",
    "mount_path": "/mnt/dms/missing",
    "csi_driver": "driver.longhorn.io"
  },
  "cluster_name": "cluster-b",
  "storage_class_name": "missing-longhorn"
}
```

예상:

- `cluster-b`에서 StorageClass가 없으므로 `sanity_status=Failed`
- result에는 `storage_class_missing` 또는 동등한 error code가 남는다.
- Planner는 이 mapping을 참조하는 request를 backend plan으로 만들지 않는다.

추가 실패 mapping으로 `storage_class_name=testbed-longhorn`, `csi_driver=wrong.example.com`을 등록해 `csi_driver_mismatch`도 검증한다.

9. Agent identity mismatch report를 제출한다.

예상:

- HTTP/API 또는 CLI path에서 거부된다.
- `agent_reports` Fresh inventory에는 반영되지 않는다.
- observability event가 남는다.

10. stale threshold를 짧게 설정하고 오래된 Agent report를 제외하는지 검증한다.

예상:

- stale report는 query에서 보인다.
- effective inventory와 mapping readiness에는 사용되지 않는다.
- action-required에 stale issue가 표시된다.

11. 정상 mapping을 참조하는 Data Management `scan` request를 제출해 Planner가 DM readiness를 plan metadata에 기록하는지 확인한다.

Phase 3에서는 DM Worker가 실제 VolcanoJob을 만들 필요는 없다. Planner가 mapping sanity와 worker pool evidence를 반영하는지만 확인한다.

## Phase 3 검증 매트릭스

| Area | Required verification | Expected evidence |
| --- | --- | --- |
| Migration | SQLite와 PostgreSQL migration 성공 | new columns/tables applied without breaking Phase 1/2 tests |
| Agent ingest | matching actor report accepted | `agent_reports` Fresh row |
| Agent identity mismatch | mismatched actor rejected | no Fresh report, diagnostic event |
| Freshness | stale threshold 적용 | stale report excluded from effective inventory |
| Kubernetes inventory | cluster-a/cluster-b StorageClass read-only 조회 | `testbed-cephfs` on cluster-a, `testbed-longhorn` on cluster-b |
| Effective inventory | fresh Agent + Kubernetes inventory 병합 | role별 mount/tool/storage class summary |
| Mapping sanity success | cluster-a `testbed-cephfs`, cluster-b `testbed-longhorn` mapping check | `Ready` or justified `Degraded` |
| Mapping sanity failure | missing StorageClass mapping check | `Failed`, error reason stored |
| CSI mismatch | wrong `csi_driver` rejected or failed sanity | `csi_driver_mismatch` |
| Planner guard | Failed mapping request does not create backend plan | terminal result with `backend_side_effect=false` |
| DM readiness | Data Management planning uses DM Agent evidence | worker pool/readiness in data job or plan metadata |
| RM readiness | Resource Management planning uses RM Agent evidence | readiness summary in plan or rejection reason |
| Control mutation audit | mapping upsert/check is audited | `control_mutations` before/after/result |
| Operational query | mapping/inventory/action-required query works | failed mapping and stale report queryable |
| Observability | inventory/sanity diagnostic events written | observability DB events |

## 구현 순서

1. 현재 tests와 Phase 2 smoke entrypoint를 확인한다.
2. Agent report domain model을 backward-compatible하게 확장한다.
3. `agent_reports` migration과 repository query를 추가한다.
4. Agent report freshness 계산을 구현한다.
5. Kubernetes read-only inventory adapter를 추가한다.
6. Effective inventory service를 구현한다.
7. Storage mapping sanity calculator를 구현한다.
8. `storage_mappings`에 detailed sanity result/readiness persistence를 추가한다.
9. Storage mapping upsert와 manual recheck path를 sanity calculator와 연결한다.
10. Planner가 storage mapping existence/sanity/readiness를 확인하도록 강화한다.
11. Operational Query API에 inventory, agent report, storage mapping query를 추가한다.
12. Unit/contract tests를 추가한다.
13. PostgreSQL + Kubernetes read-only live smoke script를 추가한다.
14. 테스트베드에서 live smoke를 실행한다.
15. `docs/dms-phase3-verification.md`에 evidence를 기록한다.

## 구현 및 검증 진입점

Phase 3 구현의 주요 진입점은 다음과 같다.

- `src/dms/domain.py`: `AgentReport`, `StorageMappingInput`, readiness/sanity enum 또는 model
- `src/dms/agent.py`: Agent report validation, freshness handling
- `src/dms/adapters.py`: Kubernetes read-only inventory adapter, effective inventory adapter
- `src/dms/repositories.py`: agent report query, storage mapping sanity persistence, active work conflict check
- `src/dms/migrations.py`: Phase 3 expand-compatible schema migration
- `src/dms/api.py`: storage mapping upsert/check, inventory and mapping query
- `src/dms/planner.py`: mapping sanity/readiness guard
- `src/dms/query.py`: action-required, storage mapping, agent health query
- `src/dms/cli.py`: optional inventory refresh or agent report submit helper
- `tests/test_phase3_inventory.py`: unit/contract tests
- `scripts/phase3_inventory_smoke.py`: PostgreSQL + Kubernetes read-only live smoke body
- `scripts/verify-phase3-testbed.sh`: testbed orchestration
- `docs/dms-phase3-verification.md`: 실행 결과와 검증 evidence

대표 검증 명령:

```bash
cd /home/mason/workspace/dms
python3 -m pytest -q
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase3-testbed.sh
```

## Phase 3 문서 산출물

다음 문서를 구현 결과와 함께 최신화한다.

- `docs/dms-phase3-verification.md`: 실행한 local/PostgreSQL/Kubernetes inventory tests, 명령, 주요 output, 성공/실패 결과
- `testbed/CephFS.md`: CephFS StorageClass, CSI provisioner, smoke PVC 정보가 바뀐 경우 갱신
- `testbed/Longhorn.md`: Longhorn StorageClass, CSI provisioner, ResourceQuota smoke 정보가 바뀐 경우 갱신
- `testbed/PostgreSQL.md`: PostgreSQL 접속/검증 정보가 바뀐 경우 갱신
- `docs/dms-design.md`: Phase 3 구현으로 확정된 API endpoint, status enum, sanity result shape가 기존 설계와 달라진 경우 갱신

## Phase 3 완료 후 다음 Phase 후보

Phase 3 완료 후 추천 순서는 다음과 같다.

### 다음 Phase: Kubernetes namespace storage quota live adapter

테스트베드의 `cluster-b` `testbed-longhorn` StorageClass를 우선 사용해 다음을 live 검증한다. `cluster-a` `testbed-cephfs`는 CephFS/PVC 경로와 self-managed RM target 검증에도 사용할 수 있다.

- namespace optional create
- DMS-managed `ResourceQuota/dms-storage-quota` apply
- namespace-wide `requests.storage`와 PVC count quota
- StorageClass-specific quota key rendering
- PVC admission success/failure
- block=ON hard limit zeroing
- block=OFF restore
- live state sync from ResourceQuota spec/status
- DB/live consistency check

### 그 다음 Phase: Data Management preflight와 Volcano live execution

Agent inventory와 storage mapping sanity를 기반으로 다음을 구현한다.

- DM worker candidate pool
- POSIX identity permission preflight using LDAP mapping
- operation별 mpifileutils option registry
- `scan` live VolcanoJob
- `sync`/`rm` preview and confirm
- artifact/report URI persistence
