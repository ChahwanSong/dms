# DMS Phase 7 Implementation Prompt

이 문서는 `docs/dms-phase6.md` 완료 이후 일곱 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 7의 목표는 Phase 6에서 완성한 Kubernetes namespace multi-StorageClass quota lifecycle 위에 **운영 조회 API와 blocked 상태 update semantics**를 추가해, 운영자가 DMS DB state와 Kubernetes live state를 직접 비교하고 requester별 request history를 안전하게 조회할 수 있게 하는 것이다.

Phase 7는 DMS Agent DaemonSet, filesystem quota, Data Management live execution 같은 새 runtime 축을 열지 않는다. Phase 4~6에서 이미 실제 Kubernetes ResourceQuota mutation/check/sync/delete path를 검증했으므로, 이번 phase에서는 그 기능을 운영자가 신뢰하고 사용할 수 있게 read-only query와 lifecycle edge case를 먼저 닫는다.

## Phase 7 목표

Phase 7의 핵심 기능은 다음 세 가지다.

1. **Requester-scoped request history query**
2. **Kubernetes namespace quota dedicated query API**
3. **Blocked 상태 Kubernetes quota update semantics**

구현 완료 기준은 다음과 같다.

- `GET /api/v1/operations/requests`는 필수 `requester_id` query parameter를 요구하고, 해당 requester의 request만 `commit_order DESC` 최신순으로 반환한다.
- `limit` query parameter는 optional이다. 없으면 API/repository 기본값을 사용한다.
- Kubernetes namespace quota 전용 read-only API를 추가해 DMS DB에 기록된 quota state와 현재 Kubernetes cluster의 live `ResourceQuota` state를 한 응답에서 비교한다.
- quota query API는 DMS-managed `ResourceQuota/dms-storage-quota`, non-DMS `ResourceQuota`, effective quota warning, DB/live diff를 구조화해 반환한다.
- blocked 상태의 Kubernetes namespace quota에 update가 들어오면 live hard limit은 계속 `0`으로 유지하고, unblock 시 복구할 `block_state.restore_hard`만 최신 quota로 갱신한다.
- blocked 상태 update에도 기존 decrease guard를 적용해 live `status.used`보다 낮은 restore target은 backend side effect 없이 거부한다.
- local unit tests와 테스트베드 PostgreSQL/Kubernetes live verification을 모두 수행한다.
- 검증 결과는 `docs/dms-phase7-verification.md`와 `docs/dms-done.md`에 command/output 포함해 기록한다.

## 왜 Phase 7에서 운영 조회와 blocked update를 하는가

Phase 6까지 DMS는 다음을 실제 테스트베드에서 검증했다.

- Kubernetes namespace quota create/apply/update
- quota decrease guard
- block/unblock
- consistency check
- sync from live state
- delete
- multi-StorageClass quota rendering and lifecycle
- non-DMS `ResourceQuota` effective warning evidence
- `cluster-a/testbed-cephfs`, `cluster-b/testbed-longhorn`, `cluster-b/longhorn-static` live 검증

하지만 Phase 6 완료 시점에도 운영자가 바로 쓰기에 부족한 부분이 남아 있다.

- `GET /api/v1/operations/requests`는 requester별 필터 없이 넓은 목록을 반환한다.
- quota state는 check/sync result evidence로는 확인 가능하지만, DMS DB state와 Kubernetes live state를 한 번에 조회하는 dedicated API가 없다.
- blocked 상태에서 update를 허용할 경우, live quota가 다시 열리거나 unblock 복구 대상이 오래된 hard limit으로 남을 수 있다.

따라서 Phase 7은 새 backend를 추가하기 전에 Kubernetes quota 운영면을 안정화하는 작은 phase로 진행한다. 이 단계가 끝나면 DMS Agent DaemonSet이나 Data Management preflight를 진행할 때도 request history와 quota state를 API로 직접 확인할 수 있다.

## 현재 전제

Phase 6 완료 후 전제:

- Kubernetes namespace quota resource identity는 `cluster_name + namespace_name`이다.
- DMS가 소유하는 Kubernetes object는 `ResourceQuota/dms-storage-quota`다.
- `storage_class_quotas[]`는 하나의 namespace quota resource 안의 quota dimension이다.
- DMS는 `ResourceQuota.spec.hard`, `status.hard`, `status.used`를 read-back할 수 있다.
- DMS는 namespace 안의 non-DMS `ResourceQuota`를 read-only list할 수 있다.
- check/sync path는 `kubernetes_resource_quota_hard_issues()`와 `effective_resource_quota_warnings()`로 DB/live diff와 effective warning을 계산한다.
- Operational API service에는 아직 Kubernetes namespace quota live adapter가 연결되어 있지 않다.
- `GET /api/v1/operations/requests`는 현재 requester filter 없이 repository `list_requests()`를 호출한다.
- repository request list 기본 limit은 API/repository 기본값을 따른다. Phase 7 구현 시점의 기본값을 source of truth로 사용한다.

테스트베드 topology:

- `cluster-a`
  - `testbed-cephfs`
  - provisioner `rook-ceph.cephfs.csi.ceph.com`
  - single StorageClass quota regression target
- `cluster-b`
  - `testbed-longhorn`
  - `longhorn-static`
  - provisioner `driver.longhorn.io`
  - multi-StorageClass quota target
- PostgreSQL
  - `192.168.56.11:30432`
  - 테스트 실행마다 operational DB와 observability DB를 새로 만든다.

## 기능 1: Requester-scoped Request History Query

### API

기존 endpoint를 유지하되 query contract를 변경한다.

```text
GET /api/v1/operations/requests?requester_id={requester_id}&limit={limit}
```

요구 사항:

- `requester_id`는 필수 query parameter다.
- `requester_id`가 없거나 빈 문자열이면 request list를 조회하지 않고 validation error를 반환한다.
- 응답은 `requests.requester_id`가 query의 `requester_id`와 정확히 일치하는 request만 포함한다.
- 정렬은 기존과 동일하게 `commit_order DESC` 최신순이다.
- `limit`은 optional query parameter다.
- `limit`이 없으면 API/repository 기본값을 사용한다.
- `limit`이 있으면 repository 조회에 전달한다.
- `GET /api/v1/operations/requests/{request_id}`는 단건 lifecycle history 조회용으로 유지한다.

예시:

```bash
curl -s \
  -H 'x-dms-actor: api-client' \
  'http://127.0.0.1:8000/api/v1/operations/requests?requester_id=portal:team-a&limit=50'
```

응답 예:

```json
[
  {
    "request_id": "req_...",
    "requester_id": "portal:team-a",
    "actor": "api-client",
    "operation": "k8s_quota_update",
    "resource_kind": "kubernetes_namespace_quota",
    "resource_key": "cluster-b:team-a",
    "status": "Succeeded",
    "commit_order": 42
  }
]
```

### Repository 변경

권장 repository contract:

```python
def list_requests(
    self,
    *,
    requester_id: str,
    limit: int = DEFAULT_REQUEST_LIST_LIMIT,
) -> list[dict[str, Any]]:
    ...
```

권장 SQL:

```sql
SELECT *
FROM requests
WHERE requester_id = ?
ORDER BY commit_order DESC
LIMIT ?
```

운영 DB가 커질 수 있으므로 migration에 다음 index를 추가한다.

```sql
CREATE INDEX IF NOT EXISTS idx_requests_requester_commit_order
ON requests(requester_id, commit_order DESC);
```

### 테스트

Unit/API tests:

- 서로 다른 `requester_id`의 request를 섞어 생성하고, API가 지정한 requester의 request만 반환하는지 확인한다.
- `limit` 없이 호출하면 repository/API 기본값이 적용되는지 확인한다.
- `limit=2`로 호출하면 해당 requester 범위 안에서 최신 2개만 반환되는지 확인한다.
- `requester_id` 없이 호출하면 FastAPI validation error가 반환되는지 확인한다.
- 빈 `requester_id`는 422 또는 명시 validation error로 reject한다.

PostgreSQL live verification:

- fresh operational DB에 requester `portal:phase7-a`, `portal:phase7-b` request를 섞어 생성한다.
- API를 통해 각 requester별 request list가 분리되는지 확인한다.
- `limit` 지정/미지정 결과를 command output으로 남긴다.

## 기능 2: Kubernetes Namespace Quota Dedicated Query API

### API

새 read-only endpoint를 추가한다.

```text
GET /api/v1/operations/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
```

권장 query parameter:

- `source=both|db|live`
  - 기본값은 `both`다.
  - `db`는 operational DB만 조회한다.
  - `live`는 Kubernetes live state만 조회한다.
  - `both`는 둘 다 조회하고 diff를 계산한다.
- `include_non_dms=true|false`
  - 기본값은 `false`다.
  - `true`이면 namespace 안의 non-DMS `ResourceQuota` summary와 effective quota warning을 포함한다.
- `include_status_used=true|false`
  - 기본값은 `true`다.
  - `false`이면 응답에서 live `status.used`를 생략할 수 있다.

예시:

```bash
curl -s \
  -H 'x-dms-actor: api-client' \
  'http://127.0.0.1:8000/api/v1/operations/kubernetes/namespace-quotas/cluster-b/team-a?include_non_dms=true'
```

### 응답 Shape

응답에는 최소한 다음을 포함한다.

- `cluster_name`, `namespace_name`, `resource_key`
- DMS-managed `resource_quota_name`
- `source`
- DB section
  - `exists`
  - `resource_id`
  - `resource_type`
  - `status`
  - `version`
  - `desired_state.quota`
  - `desired_state.storage_class_quotas`
  - rendered `desired_hard`
  - `applied_state`
  - `observed_state`
  - `created_at`, `updated_at`
- request summary section
  - same resource key의 최근 create/update/block/unblock/check/sync/delete request
  - last check request id/status
  - last sync request id/status
- live section
  - `exists`
  - metadata name/namespace/labels/annotations
  - `spec_hard`
  - `status_hard`
  - `status_used` if requested
  - `resource_version`
  - `creation_timestamp`
- diff section
  - `status`: `Consistent`, `Drifted`, `Missing`, `DbMissing`, `DbOnly`, `LiveOnly`, `QueryFailed`
  - `issues`
- effective quota section
  - non-DMS `ResourceQuota` summary when requested
  - `effective_quota_warnings`
- diagnostic section
  - DB 조회 실패 또는 Kubernetes API 조회 실패 reason
  - partial response 여부

응답 예:

```json
{
  "cluster_name": "cluster-b",
  "namespace_name": "team-a",
  "resource_key": "cluster-b:team-a",
  "dms_resource_quota_name": "dms-storage-quota",
  "source": "both",
  "db": {
    "exists": true,
    "status": "Active",
    "version": 7,
    "desired_hard": {
      "requests.storage": "768Mi",
      "persistentvolumeclaims": "8",
      "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "512Mi",
      "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "4",
      "longhorn-static.storageclass.storage.k8s.io/requests.storage": "256Mi",
      "longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims": "4"
    },
    "last_check_request_id": "req_...",
    "last_sync_request_id": "req_..."
  },
  "live": {
    "exists": true,
    "spec_hard": {
      "requests.storage": "768Mi",
      "persistentvolumeclaims": "8",
      "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "512Mi",
      "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "4",
      "longhorn-static.storageclass.storage.k8s.io/requests.storage": "256Mi",
      "longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims": "4"
    },
    "status_used": {
      "requests.storage": "384Mi",
      "persistentvolumeclaims": "3",
      "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "256Mi",
      "longhorn-static.storageclass.storage.k8s.io/requests.storage": "128Mi"
    },
    "resource_version": "123456"
  },
  "diff": {
    "status": "Consistent",
    "issues": []
  },
  "effective_quota_warnings": []
}
```

### Implementation Notes

- API는 read-only여야 한다. Kubernetes object나 DMS DB desired state를 변경하지 않는다.
- `OperationalQueryService`에 namespace quota query method를 추가한다.
- `AppServices`에 Kubernetes namespace quota read adapter를 주입할 수 있게 한다.
- default app construction에서는 `KubernetesNamespaceQuotaLiveAdapter.from_settings(settings)`를 사용한다.
- unit tests에서는 fake/stub quota adapter를 주입할 수 있어야 한다.
- `source=db` 호출은 Kubernetes adapter가 없어도 성공해야 한다.
- `source=live` 또는 `source=both`에서 Kubernetes 조회가 실패하면 가능한 DB section은 반환하고 `diagnostics`에 실패 reason을 남긴다.
- DB desired hard 계산은 기존 `render_kubernetes_resource_quota_hard()`를 재사용한다.
- DB/live diff 계산은 기존 `kubernetes_resource_quota_hard_issues()`를 재사용한다.
- effective warning은 기존 `effective_resource_quota_warnings()`를 재사용한다.
- live DMS-managed object가 존재하지만 DMS label/annotation이 없으면 `Drifted` issue로 표시한다.
- live DMS-managed object가 없고 DB resource row가 있으면 `Missing`으로 표시한다.
- live object는 있는데 DB resource row가 없으면 `LiveOnly` 또는 `DbMissing`으로 표시하고 DMS-owned label 여부를 함께 반환한다.
- non-DMS `ResourceQuota`는 수정하거나 삭제하지 않는다.

### Repository Helpers

필요하면 다음 helper를 추가한다.

```python
def list_requests_for_resource(
    self,
    *,
    resource_kind: str,
    resource_key: str,
    operations: tuple[str, ...] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    ...
```

이 helper는 quota query 응답의 request summary와 last check/sync id 계산에 사용한다.

권장 index:

```sql
CREATE INDEX IF NOT EXISTS idx_requests_resource_operation_order
ON requests(resource_kind, resource_key, operation, commit_order DESC);
```

기존 `idx_requests_resource`가 충분하면 새 index는 생략할 수 있다. PostgreSQL live DB에서 query plan이 문제가 되지 않는 범위라면 Phase 7에서는 단순 구현을 우선한다.

### 테스트

Unit/API tests:

- DB-only query가 resource row의 desired/applied/observed state와 rendered desired hard를 반환하는지 확인한다.
- live-only query가 stub/live adapter의 `spec_hard`, `status_hard`, `status_used`를 반환하는지 확인한다.
- both query가 DB desired hard와 live `spec_hard` diff를 `Consistent`로 계산하는지 확인한다.
- live `spec_hard`를 다르게 주면 `Drifted`와 key별 issue가 반환되는지 확인한다.
- DB resource row는 있는데 live object가 없으면 `Missing`을 반환하는지 확인한다.
- non-DMS quota가 DMS quota보다 restrictive하면 `effective_quota_warnings`를 반환하는지 확인한다.
- `source=db`는 Kubernetes adapter 없이도 성공하는지 확인한다.

Live verification:

- `cluster-a/testbed-cephfs` single StorageClass quota에서 DB desired hard와 live `spec.hard`가 일치하는지 API로 검증한다.
- `cluster-b/testbed-longhorn` + `cluster-b/longhorn-static` multi-StorageClass quota에서 namespace-wide key와 StorageClass-specific key가 모두 반환되는지 검증한다.
- live `ResourceQuota`를 수동 patch해 drift를 만들고 API `diff.status=Drifted`와 issue key가 check 결과와 같은지 확인한다.
- DMS DB state는 남아 있는데 live object를 삭제한 missing case를 검증한다.
- non-DMS `ResourceQuota`를 추가하고 `include_non_dms=true`에서 effective quota warning이 반환되는지 확인한다.

## 기능 3: Blocked 상태 Kubernetes Quota Update Semantics

### 문제

Phase 6 구현에서는 blocked 상태의 Kubernetes namespace quota resource에 update 요청이 들어와도 별도로 거부하지 않는다. 이 경우 update가 `resource_quota_hard`를 다시 렌더링해 live `ResourceQuota`에 non-zero hard limit을 적용할 수 있고, `block_state.restore_hard`는 block 이전 값으로 남을 수 있다.

그 결과 다음 문제가 생길 수 있다.

- DMS desired state는 blocked인데 Kubernetes admission은 다시 열릴 수 있다.
- 이후 unblock 시 block 중 update한 최신 quota가 아니라 오래된 restore hard로 복구될 수 있다.
- block 중 quota decrease가 live used보다 낮아도 guard가 잘못된 hard limit 기준으로 판단할 수 있다.

### 요구 동작

blocked 상태 update는 금지하지 않는다. 대신 다음 정책을 적용한다.

- 기존 DMS resource가 `block_state.blocked=true`인 상태에서 `update` 요청이 들어오면 planner는 update payload를 기존 desired state와 병합해 최신 restore target hard를 계산한다.
- 계산된 최신 hard limit은 `block_state.restore_hard`에 저장한다.
- live Kubernetes `ResourceQuota.spec.hard`에는 계속 모든 DMS-managed hard key를 `"0"`으로 적용한다.
- DMS desired state에는 `block=true`, `block_state.blocked=true`를 유지한다.
- 이후 `unblock` 요청은 block 이전 hard가 아니라 block 중 update로 갱신된 최신 `block_state.restore_hard`를 복구한다.
- update가 quota decrease를 포함하면 기존 decrease guard를 최신 restore target hard에 대해 적용한다.
- live used보다 낮은 restore target은 blocked 상태에서도 `Rejected`가 되어야 한다.
- `storage_class_quotas[]` full replacement semantics는 유지한다. block 중 update payload에 `storage_class_quotas[]`가 있으면 새 list 전체가 restore target이 된다.

### Implementation Notes

- Planner의 Kubernetes desired state 생성 단계에서 blocked existing resource를 감지한다.
- update payload merge는 restore target desired state 기준으로 수행한다.
- restore target hard를 렌더링한 뒤 `zero_kubernetes_resource_quota_hard(restore_hard)`를 live desired hard로 둔다.
- decrease guard는 zero hard가 아니라 restore target hard와 live `status.used`를 비교해야 한다.
- check/sync 동작은 기존 semantics를 유지한다.
  - check는 blocked resource의 DB desired hard가 zero인 상태를 live zero hard와 비교하면 `Consistent`가 될 수 있다.
  - query API는 `block_state.restore_hard`를 별도 field로 보여주어 운영자가 unblock target을 확인할 수 있어야 한다.

### 테스트

Unit tests:

- block 후 update increase request는 plan desired `resource_quota_hard`가 모두 `"0"`이고 `block_state.restore_hard`만 증가한 hard로 바뀌는지 확인한다.
- block 후 update에서 `storage_class_quotas[]` full replacement가 restore target에 반영되는지 확인한다.
- block 후 update decrease가 live used보다 낮으면 `Rejected`이고 backend side effect가 없는지 확인한다.
- unblock은 최신 `block_state.restore_hard`를 복구하는지 확인한다.

Live verification:

- `cluster-b` multi-StorageClass target에서 block 후 live `ResourceQuota.spec.hard`의 namespace-wide key와 StorageClass-specific key가 모두 `"0"`인지 확인한다.
- block 상태에서 quota increase update를 요청해 request가 성공하되 live `spec.hard`는 계속 `"0"`인지 확인한다.
- dedicated quota query API가 `block_state.restore_hard`에 update된 quota를 보여주는지 확인한다.
- unblock 후 live `ResourceQuota.spec.hard`가 block 이전 값이 아니라 block 중 update한 최신 quota로 복구되는지 확인한다.
- block 상태에서 live used보다 낮은 decrease update가 rejected 되고 live `spec.hard`가 계속 `"0"`으로 유지되는지 확인한다.

## API Summary

Phase 7에서 추가 또는 변경되는 endpoint:

```text
GET /api/v1/operations/requests?requester_id={requester_id}&limit={limit}
GET /api/v1/operations/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
```

기존 Kubernetes quota mutation endpoint는 유지한다.

```text
POST   /api/v1/resource-management/kubernetes/namespace-quotas
PATCH  /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
POST   /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:block
DELETE /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
POST   /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:sync
POST   /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:check
```

## Suggested Implementation Order

### Step 1: Requester-scoped request list

- Repository `list_requests()`에 required `requester_id` filter 추가
- optional `limit` 처리
- API query parameter 추가
- SQLite/PostgreSQL migration index 추가
- unit/API tests 추가

검증:

```bash
python -m pytest -q tests/test_phase1_contracts.py
```

### Step 2: Quota query service skeleton

- `OperationalQueryService.kubernetes_namespace_quota()` 추가
- DB-only response 구현
- rendered desired hard 계산
- request summary helper 추가
- unit tests 추가

검증:

```bash
python -m pytest -q tests/test_phase4_kubernetes_quota.py tests/test_phase6_kubernetes_multi_storage_quota.py
```

### Step 3: Quota query live adapter integration

- `AppServices`에 Kubernetes quota adapter 주입
- `create_app()` default construction에서 settings 기반 live adapter 구성
- `source=live|both` 구현
- DB/live diff와 effective warnings 추가
- stub adapter 기반 API tests 추가

검증:

```bash
python -m pytest -q tests/test_phase7_operational_queries.py
```

### Step 4: Blocked update semantics

- Planner blocked update path 수정
- decrease guard를 restore target hard 기준으로 적용
- block/update/unblock unit tests 추가

검증:

```bash
python -m pytest -q tests/test_phase5_kubernetes_quota_lifecycle.py tests/test_phase6_kubernetes_multi_storage_quota.py tests/test_phase7_operational_queries.py
```

### Step 5: Live verification script

새 script를 추가한다.

```text
scripts/phase7_operational_query_and_block_update.py
scripts/verify-phase7-testbed.sh
```

검증 흐름:

1. fresh operational/observability PostgreSQL DB를 만든다.
2. `cluster-a/testbed-cephfs`와 `cluster-b/testbed-longhorn`, `cluster-b/longhorn-static` storage mapping을 준비한다.
3. synthetic RM/DM Agent report를 제출한다. Phase 7는 Agent DaemonSet 구현 phase가 아니므로 이 한계를 verification doc에 명시한다.
4. requester `portal:phase7-a`, `portal:phase7-b` request를 섞어 생성한다.
5. `GET /api/v1/operations/requests?requester_id=portal:phase7-a`가 해당 requester request만 반환하는지 확인한다.
6. `limit` 지정 결과가 최신순으로 제한되는지 확인한다.
7. `cluster-a` CephFS quota를 생성하고 quota query API로 DB/live `Consistent`를 확인한다.
8. `cluster-b` Longhorn multi-StorageClass quota를 생성하고 quota query API가 namespace-wide key와 두 StorageClass-specific key를 반환하는지 확인한다.
9. non-DMS `ResourceQuota`를 추가하고 `include_non_dms=true`에서 effective warning을 확인한다.
10. live `ResourceQuota`를 patch해 drift를 만들고 quota query API가 `Drifted` issue를 반환하는지 확인한다.
11. live object를 삭제해 `Missing` 상태를 확인한다.
12. block 상태 update increase/unblock restore를 실제 Kubernetes API에서 확인한다.
13. block 상태 decrease guard reject를 확인한다.
14. DMS delete는 `dms-storage-quota`만 삭제하고 non-DMS quota는 보존하는지 재확인한다.
15. verification namespace/PVC를 cleanup한다.

## Done Documentation

Phase 7 완료 시 다음 문서를 갱신한다.

- `docs/dms-phase7-verification.md`
  - local pytest command/output
  - PostgreSQL DB names
  - API request/response samples
  - Kubernetes live command/output
  - drift/missing/effective warning evidence
  - block-update-unblock evidence
- `docs/dms-done.md`
  - `Implemented Through Phase 7`
  - Phase 7 implemented scope
  - live verification target and output
  - re-run command
  - still-not-implemented list

## Not In Scope

다음은 Phase 7 범위가 아니다.

- DMS Agent DaemonSet 배포
- filesystem directory/quota live mutation
- GPFS/CephFS POSIX quota command 실행
- Data Management `scan/sync/rm` live execution
- VolcanoJob live execution
- namespace delete lifecycle
- UI/dashboard
- requester 권한 모델 전면 재설계
- pagination cursor API
- quota mutation API shape 변경

## Phase 7 완료 후 다음 Phase 후보

### Phase 8A: DMS Agent DaemonSet

Phase 3부터 이어진 synthetic Agent report 한계를 제거한다.

- RM Agent DaemonSet on managed clusters
- DM Agent on control cluster worker nodes
- mount/tool/credential/network probe
- Agent identity evidence
- report freshness and stale handling
- storage mapping sanity가 실제 Agent report로 Ready/Failed 계산

이 phase는 Data Management live execution 전에 수행하는 것을 권장한다.

### Phase 8B: Filesystem Resource Management Minimal Lifecycle

Kubernetes ResourceQuota와 별개로 POSIX filesystem directory/quota lifecycle을 실제 backend에 붙인다.

- directory create/update/block/delete
- quota apply/check/sync
- CephFS mount 또는 GPFS template 중 하나로 시작
- destructive delete는 테스트 namespace/path로 제한

### Phase 8C: Data Management Read-only Scan Preflight

Agent DaemonSet 또는 충분히 신뢰 가능한 DM capability report가 준비된 뒤 진행한다.

- LDAP identity mapping 기반 POSIX preflight
- read-only `scan` job
- VolcanoJob 또는 local controlled executor 중 하나를 실제 검증 대상으로 선택
- scan report artifact persistence

권장 순서는 `Phase 8A -> Phase 8C`다. Data Management는 실제 mount/tool/identity capability가 scheduler decision과 맞아야 하므로 Agent DaemonSet 없이 진행하면 검증 의미가 약해진다.
