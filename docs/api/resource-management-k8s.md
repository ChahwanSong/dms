# Kubernetes 네임스페이스 쿼터 RM API

DMS의 **Kubernetes namespace quota** Resource Management API 사용법이다. 하나의 namespace에 대한
저장 용량·PVC 개수 한도를 대상 클러스터의 단일 `ResourceQuota/dms-storage-quota` 오브젝트로 관리한다.

- Filesystem(GPFS/WekaFS/CephFS) RM API → [`resource-management-fs.md`](resource-management-fs.md)
- 조회 전용(Operations) API → [`operations.md`](operations.md)
- API 개요·인증 전반 → [`README.md`](README.md)

> **사전 준비는 설치 영역이다.** 이 API를 쓰려면 먼저 ① 대상 클러스터 등록(kubeconfig +
> `dms-remote` RBAC)과 ② `storage_class_quotas`가 참조할 storage mapping 등록이 끝나 있어야 한다.
> 그 절차·매니페스트 편집은 [`install/dms-04-rm-k8s-quota.md`](../../install/dms-04-rm-k8s-quota.md)에
> 있다. 이 문서는 **API 사용법**만 다룬다.

> **AGENTLESS.** namespace-quota RM은 대상 클러스터에 DMS RM/DM agent를 요구하지 않는다 — control
> plane(RM worker)이 대상 클러스터 API server에 `ResourceQuota`를 직접 apply한다.

---

## 인증과 요청 준비

운영(production) 배포는 **mTLS-verified header 프로필**이다 — control-plane이
`DMS_REQUIRE_MTLS_HEADER=true` + `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`로 뜬다. 신뢰된 ingress가
클라이언트 인증서를 검증해 upstream으로 넘기고, DMS는 **인증서 subject**에서 actor를 파생한다
(prefix `DMS_MTLS_ACTOR_PREFIX`, 기본 `mtls:`). 평문 `x-dms-actor` 헤더는 이 프로필에서 신뢰하지
않으며, `DMS_DEFAULT_ACTOR`는 비어 있어야 한다(설정돼 있으면 API가 기동 거부). 선택적으로 shared
bearer token(`DMS_AUTH_SHARED_TOKEN`)을 함께 요구할 수 있다.

따라서 모든 curl은 **클라이언트 인증서**로 호출한다(평문 `x-dms-actor` 없음):

```bash
DMS_API_URL=https://dms.example.internal
CURL=(curl -sS
  --cert   /etc/dms-client/client.crt
  --key    /etc/dms-client/client.key
  --cacert /etc/dms-client/ca.crt)
# shared token을 함께 요구하도록 배포된 경우에만:
#   CURL+=(-H "authorization: Bearer $DMS_AUTH_SHARED_TOKEN")
```

- **`requester_id`(payload 필드)** 는 이 quota의 논리적 요청자/소유자다 — 감사·정책 판정에 쓰인다.
  호출을 실제로 수행한 신원(감사 actor)은 mTLS 인증서 subject에서 파생되며 `requester_id`와 별개다.

> **부연 — testbed/dev 프로필.** `DMS_REQUIRE_MTLS_VERIFIED_HEADER=false`인 개발/테스트 배포는
> 인증서 없이 평문 Bearer + `x-dms-actor`로 호출한다:
> `curl -H "authorization: Bearer $TOKEN" -H "x-dms-actor: operator" ...`. 아래 예시의 요청/응답
> 본문 형태는 두 프로필에서 동일하므로, 읽기 편의를 위해 그대로 읽으면 된다.

---

## 동작 모델

DMS는 하나의 namespace quota 리소스를 대상 클러스터의 **단일 `ResourceQuota/dms-storage-quota`**
오브젝트로 렌더링한다(이름 고정, DMS 전용).

- 라벨 `app.kubernetes.io/managed-by: dms`, `dms.io/resource-kind: kubernetes-namespace-quota`;
  어노테이션 `dms.io/resource-key: <cluster>:<namespace>`.
- **`spec.hard` 키 매핑**:

  | DMS payload | ResourceQuota hard key |
  |---|---|
  | `quota.requests_storage_bytes` | `requests.storage` (namespace-wide) |
  | `quota.pvc_count` | `persistentvolumeclaims` (namespace-wide) |
  | `storage_class_quotas[].requests_storage_bytes` (또는 `capacity_bytes`) | `<sc>.storageclass.storage.k8s.io/requests.storage` |
  | `storage_class_quotas[].pvc_count` | `<sc>.storageclass.storage.k8s.io/persistentvolumeclaims` |

  - `requests.storage`는 bytes → k8s quantity(`Mi`/`Gi`)로 렌더링된다.
  - **hard key가 최소 1개** 있어야 한다 — namespace-wide `quota` 또는 `storage_class_quotas[]` 중
    하나 이상. 0개면 create는 `kubernetes_quota_hard_key_required`로 거부.
- **StorageClass 유도**: `storage_class_quotas[].storage_name`은 storage class 이름을 직접 쓰지
  않고 **등록된 storage mapping 이름**을 참조한다. DMS가 그 mapping에서 `storage_class_name` +
  `cluster_name`을 derive한다.
- **클러스터 라우팅**: payload의 `cluster_name`으로 등록된 kubeconfig를 골라 그 클러스터 API server에
  ResourceQuota를 apply/read한다.
- **read-only / 비파괴 원칙**: DMS는 자기 `dms-storage-quota`만 관리한다. namespace 내 non-DMS
  ResourceQuota는 변경하지 않고 effective-quota 경고만 남기며, DMS delete 시에도 보존한다. lifecycle
  delete는 **namespace를 삭제하지 않는다**(ResourceQuota만 제거).

---

## API 요약

| Method | Path | 동작 |
|---|---|---|
| `POST` | `/api/v1/resource-management/kubernetes/namespace-quotas` | 신규 생성: `dms-storage-quota` apply (+ read-back) |
| `PATCH` | `…/kubernetes/namespace-quotas/{cluster}/{namespace}` | 변경: `quota` / `storage_class_quotas[]`(full replace) / `expires_at` / `resource_type` |
| `POST` | `…/{cluster}/{namespace}:block` | 차단/해제: `{"block":true}` 모든 hard→`0`(restore 저장), `{"block":false}` 복원 |
| `DELETE` | `…/{cluster}/{namespace}` | DMS `dms-storage-quota`만 삭제(namespace/PVC/non-DMS RQ 보존) |
| `POST` | `…/{cluster}/{namespace}:check` | consistency check: DB desired vs live `spec.hard` + metadata drift (side-effect 없음) |
| `POST` | `…/{cluster}/{namespace}:sync` | live `spec.hard`를 역산해 DB desired 갱신 (side-effect 없음) |
| `POST` | `…/{cluster}/{namespace}:import` | **DMS-managed** `dms-storage-quota`를 DB 관리로 (재)채택 |
| `POST` | `…/kubernetes/namespace-quotas:audit` | read-only audit: DB↔live drift 구조화 (Kubernetes 변경 없음) |
| `POST` | `…/kubernetes/namespace-quotas:expiration-sweep` | 만료된 quota 일괄 차단 (`scope`/`expired_before`/`dry_run`) |
| `GET` | `/api/v1/operations/kubernetes/namespace-quotas/{cluster}/{namespace}` | 단건 조회 (DB desired + live + effective warning) |
| `GET` | `/api/v1/operations/kubernetes/namespace-quotas/expiring` | 만료 임박/만료 목록 |

> 모든 mutating 엔드포인트는 `202 {request_id, status:"Persisted"}`만 돌려주고 비동기로 처리한다.
> 최종 상태는 `GET /api/v1/operations/requests/{id}`로 폴링한다(아래 [결과 조회](#결과-조회-operations-api)).

---

## 생성 (Create)

```bash
"${CURL[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas" \
  -d '{
    "requester_id": "alice",
    "payload": {
      "cluster_name": "cluster-a",
      "namespace_name": "team-alpha",
      "quota": {"requests_storage_bytes": 107374182400, "pvc_count": 50},
      "storage_class_quotas": [
        {"storage_name": "longhorn-a", "requests_storage_bytes": 53687091200, "pvc_count": 20},
        {"storage_name": "cephfs-a",   "requests_storage_bytes": 53687091200, "pvc_count": 30}
      ],
      "expires_at": "2027-01-01T00:00:00Z"
    }
  }' | jq '{request_id, status}'
```

응답:

```json
{ "request_id": "req-...", "status": "Persisted" }
```

**payload 필드:**

| 필드 | 필수 | 설명 |
|---|---|---|
| `cluster_name` | 필수 | 대상 클러스터(kubeconfig key). 누락 시 HTTP `422`, 요청 미생성 |
| `namespace_name` | 필수 | 대상 namespace. 누락 시 HTTP `422` |
| `quota.requests_storage_bytes` | 조건부 | namespace-wide 용량 → `requests.storage`. 값 주면 >0 |
| `quota.pvc_count` | 조건부 | namespace-wide PVC 수 → `persistentvolumeclaims`. 값 주면 >0 |
| `storage_class_quotas[]` | 선택 | StorageClass별 quota. **entry가 2개 이상이면 각 entry에 `requests_storage_bytes`(또는 `capacity_bytes`) 필수** |
| `storage_class_quotas[].storage_name` | 필수(entry) | 등록된 storage mapping 이름(→ SC/cluster derive) |
| `storage_class_quotas[].requests_storage_bytes` / `capacity_bytes` | 위 참고 | SC별 용량 → `<sc>.storageclass.storage.k8s.io/requests.storage` |
| `storage_class_quotas[].pvc_count` | 선택 | SC별 PVC 수 |
| `expires_at` | **필수** | 만료일(ISO8601). 생략 시 `Rejected` |
| `resource_type` | 선택 | `user`(기본)/`project`/`system`/`admin` (아래 [resource_type](#resource_type-과-default-초기화)) |

> **hard key 최소 1개 규칙**: `quota`만(SC 없이) 또는 `storage_class_quotas`만(quota 없이) 보내도
> 된다. 둘 다 비어 hard key가 0개면 `kubernetes_quota_hard_key_required`로 `Rejected`.

**주요 거부 사유**(planner `reason` 문자열):

- `expires_at_required` — 만료일 누락.
- `storage_class_quotas_must_be_list` / `storage_class_quota_storage_name_missing` /
  `duplicate_storage_name` — SC 목록 형식/entry 문제.
- `quota_*_invalid` / `storage_class_quota_*_invalid` — 값이 0 이하·비정수.
- `storage_class_quota_requests_storage_bytes_required` — multi-SC(2개 이상)인데 entry에 용량 없음.
- `storage_mapping_cluster_mismatch` — mapping의 `cluster_name`이 요청 `cluster_name`과 다름.
- `kubernetes_namespace_quota_resource_already_exists` — 동일 `{cluster}:{namespace}`가 이미 존재
  (non-Deleted). 갱신은 PATCH 사용. `Deleted` 상태면 재생성 허용.

**렌더링되는 `spec.hard`**(위 요청; SC 이름은 mapping의 `storage_class_name` — 여기선
`longhorn-a`→`longhorn`, `cephfs-a`→`cephfs`로 가정):

```
requests.storage:                                             100Gi
persistentvolumeclaims:                                       50
longhorn.storageclass.storage.k8s.io/requests.storage:        50Gi
longhorn.storageclass.storage.k8s.io/persistentvolumeclaims:  20
cephfs.storageclass.storage.k8s.io/requests.storage:          50Gi
cephfs.storageclass.storage.k8s.io/persistentvolumeclaims:    30
```

---

## 변경 (PATCH)

```bash
"${CURL[@]}" -X PATCH -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/team-alpha" \
  -d '{
    "requester_id": "operator",
    "payload": {
      "quota": {"requests_storage_bytes": 214748364800, "pvc_count": 80},
      "storage_class_quotas": [
        {"storage_name": "longhorn-a", "requests_storage_bytes": 107374182400, "pvc_count": 40}
      ]
    }
  }' | jq '{request_id, status}'
```

- **`storage_class_quotas[]`는 full replacement**다 — PATCH에 보낸 목록이 통째로 새 상태가 된다
  (빠진 SC entry의 hard key는 제거). 일부만 바꾸려면 유지할 entry까지 모두 포함해 보낸다.
- **decrease guard**: 어떤 hard(`requests.storage`/`persistentvolumeclaims`/각 SC key)를 live
  `status.used` 아래로 낮추려 하면 `Rejected`(데이터 손실 방지). namespace-wide + SC별 `status.used`를
  각각 확인한다.
- `expires_at`/`resource_type`만 단독 변경 가능(ResourceQuota 변화 없이 DB만 갱신).

---

## 차단 / 차단 해제 (`:block`)

namespace quota는 `:block` 하나로 차단·해제를 모두 처리한다(filesystem과 달리 별도 `:initialize`
없음). payload의 `"block"`(boolean)으로 방향을 지정한다.

```bash
# 차단 — 모든 hard key를 0으로(현재 hard는 restore_hard로 저장)
"${CURL[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/team-alpha:block" \
  -d '{"requester_id": "operator", "payload": {"block": true}}' | jq '{request_id, status}'

# 차단 해제 — block_state.restore_hard로 복원
"${CURL[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/team-alpha:block" \
  -d '{"requester_id": "operator", "payload": {"block": false}}' | jq '{request_id, status}'
```

- `block:true` — 모든 hard를 `0`으로 만들어 신규 PVC/용량 요청을 막는다(기존 사용량은 유지). DB
  lifecycle `status`가 `Blocked`로 바뀐다.
- `block:false` — `block_state.restore_hard`에 저장된 직전 hard로 복원(저장 state 없으면
  `block_restore_state_missing`으로 reject). `status`는 `Succeeded`로 복귀.
- `resource_type`이 `system`/`admin`이면 차단 거부(`resource_type_cannot_be_blocked`).
- blocked 상태에서도 PATCH로 desired hard는 갱신 가능(복원 시 새 값 반영).

---

## 삭제 (`DELETE`)

```bash
"${CURL[@]}" -X DELETE -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/team-alpha" \
  -d '{"requester_id": "operator", "payload": {}}' | jq '{request_id, status}'
```

DMS `dms-storage-quota` ResourceQuota 오브젝트만 삭제한다. **namespace, PVC, non-DMS
ResourceQuota는 보존**한다(DMS lifecycle delete ≠ namespace delete).

---

## 정합성 검사 (`:check`)

DB `desired_state`(hard 키 set)와 live `ResourceQuota.spec.hard` + **metadata**를 대조한다.
side-effect 없음. drift는 결과의 `issues[]`로 보고한다.

```bash
"${CURL[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/team-alpha:check" \
  -d '{"requester_id": "operator", "payload": {}}' | jq '{request_id, status}'
```

| 검사 항목 | drift 시 issue_type |
|---|---|
| hard 키별 값(`requests.storage`/`persistentvolumeclaims`/각 SC key) | `kubernetes_quota_drifted` |
| ResourceQuota 부재 | `kubernetes_quota_missing` |
| `metadata.labels.dms.io/resource-kind` 불일치 | `kubernetes_quota_metadata_drift` |
| `metadata.annotations.dms.io/resource-key` 불일치 | `kubernetes_quota_metadata_drift` |
| namespace 내 **non-DMS ResourceQuota** 존재 | `kubernetes_effective_quota_warning` |

> **effective quota 경고**: namespace에 DMS 외 다른 ResourceQuota가 있으면 실제 유효 quota는 모든
> RQ의 교집합(가장 엄격한 값)이 된다. DMS는 그 RQ를 변경하지 않고 경고만 남긴다.

---

## live 동기화 (`:sync`)

live `ResourceQuota.spec.hard`를 읽어 DB `desired_state`(`quota` + 각 `storage_class_quotas[]`)로
**역산**해 맞춘다. live 오브젝트는 변경하지 않는다(side-effect 없음).

```bash
"${CURL[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/team-alpha:sync" \
  -d '{"requester_id": "operator", "payload": {}}' | jq '{request_id, status}'
```

`requests.storage`/`persistentvolumeclaims` → `quota`로, `<sc>.storageclass.storage.k8s.io/*` 키 →
해당 `storage_class_quotas[]` entry로 역매핑한다(SC 이름 → storage mapping `storage_name`).

---

## 채택 (`:import`)

`:import`은 **이미 DMS-managed 라벨이 붙은** `dms-storage-quota` ResourceQuota를 DMS **DB 관리로
(재)채택**한다. live `spec.hard`만 읽어 DB desired에 반영하는 **DB-only** 동작이다(ResourceQuota
오브젝트 자체는 변경하지 않음 — `backend_side_effect:false`).

> **지원 범위 — DMS-managed RQ만.** 현재 구현은 외부(비-DMS) ResourceQuota를 채택하지 않는다. 주
> 용도는 "DMS가 만든 RQ는 그대로 있는데 DB row만 없거나 `Deleted`인 경우(예: DB 복구·재구축) → live
> RQ를 DB로 재채택". 새 namespace에 quota를 도입하려면 `:import`가 아니라 [Create](#생성-create)를 쓴다.

**채택 전제조건 — live RQ가 반드시 가져야 하는 것:**

| 항목 | 값 | 불일치 시 |
|---|---|---|
| `metadata.name` | `dms-storage-quota` | `unexpected ResourceQuota name` |
| label `app.kubernetes.io/managed-by` | `dms` | `refusing to mutate non-DMS ResourceQuota` |
| label/annotation `dms.io/resource-kind` | `kubernetes-namespace-quota` | `refusing to mutate ResourceQuota with invalid DMS resource kind` |
| annotation `dms.io/resource-key` | `<cluster>:<namespace>` (요청과 일치) | `refusing to mutate ResourceQuota for different DMS resource key` |
| DB resource | 없음 또는 `Deleted` | non-Deleted면 `kubernetes_namespace_quota_resource_already_exists` |

```bash
"${CURL[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/team-beta:import" \
  -d '{
    "requester_id": "operator",
    "payload": {
      "expires_at": "2099-01-01T00:00:00Z",
      "storage_class_quotas": [{"storage_name": "longhorn-a"}]
    }
  }' | jq '{request_id, status}'
```

- live RQ가 없으면 실패(`ResourceQuota does not exist`).
- 채택 후 PATCH/check/sync/block 대상이 된다(full-managed). SC 키가 있으면 `storage_class_quotas`
  힌트로 역매핑하고, 힌트가 없으면 `storage_mapping_candidates`로 추론한다(모든 SC 키를 매핑하지
  못하면 실패).
- **외부(비-DMS) RQ 시도 시**: `refusing to mutate non-DMS ResourceQuota`로 거부되며, 부작용 없는
  precondition 실패로 `BackendApplyFailed`(error_category `backend_precondition`,
  issue_type `kubernetes_quota_import_preflight_failed`)로 종료된다. 남은 실패 기록은 필요 시
  `POST …/requests/{id}:resolve`(`abandon`)로 정리한다.

---

## 감사 (`:audit`)

여러 namespace의 DMS-managed quota를 **read-only**로 한 번에 점검한다(Kubernetes 변경 없음).
`scope`로 범위를 지정한다 — 단일 namespace(`cluster_name`+`namespace_name`) 또는 부분 scope
(`cluster_name`만 / `resource_type`만).

> ⚠️ **`scope`는 필수**다. 완전히 비우면(`payload:{}`) `audit_scope_required`로 `Rejected`된다.

```bash
# 단일 namespace
"${CURL[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas:audit" \
  -d '{"requester_id":"operator","payload":{"scope":{"cluster_name":"cluster-a","namespace_name":"team-alpha"}}}' | jq

# 광범위(부분 scope) — cluster_name(또는 resource_type)만으로 해당 범위 전체
"${CURL[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas:audit" \
  -d '{"requester_id":"operator","payload":{"scope":{"cluster_name":"cluster-a"}}}' | jq
```

- DB desired ↔ live `spec.hard` drift, metadata drift(`dms.io/resource-kind` label /
  `dms.io/resource-key` annotation)를 구조화해 result에 담는다.
- 결과는 quota action-required aggregation(최신 audit/check 기반)에 반영된다.

---

## 만료 sweep (`:expiration-sweep`)

만료된 namespace quota를 일괄 차단한다.

```bash
"${CURL[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas:expiration-sweep" \
  -d '{"requester_id":"sweeper","payload":{"scope":{"resource_type":"user"},"dry_run":true}}' | jq
```

- `dry_run:true` — 대상/스킵 사유만 보고(side-effect 없음). 실제 실행은 만료 + 차단 가능한
  (`user`/`project`) resource를 `block`한다. `system`/`admin`은 자동 차단 제외.
- `scope.cluster_name`/`scope.resource_type`/`expired_before`로 범위를 좁힐 수 있다.

---

## resource_type 과 default 초기화

- `resource_type`(`user`/`project`/`system`/`admin`)은 만료 sweep·`:block` 보호 정책에 영향을 준다
  (`system`/`admin`은 자동/명시 차단 거부). create/PATCH로 지정·변경한다.
- `reset_quota_to_default:true`(create/PATCH)는 등록된 default quota policy 값으로 hard를
  재설정한다(정책 미존재 시 관련 검증에서 reject).

default quota policy는 `resource_kind`+`resource_type` 조합당 1개를 등록한다(운영자 설정):

```bash
"${CURL[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/default-quota-policies" \
  -d '{
    "resource_kind": "kubernetes_namespace_quota",
    "resource_type": "user",
    "quota": {
      "requests_storage_bytes": 107374182400,
      "pvc_count": 20,
      "storage_class_quotas": [
        {"storage_name": "ceph-rbd-a", "requests_storage_bytes": 53687091200, "pvc_count": 10}
      ]
    }
  }' | jq '{policy_id, status}'
```

> policy의 `storage_class_quotas[].storage_name`도 등록된 storage mapping이어야 하고, 그 mapping의
> `cluster_name`이 reset 대상 quota의 `cluster_name`과 일치해야 한다.

---

## 결과 조회 (Operations API)

모든 mutating 호출은 `202 {request_id, status:"Persisted"}`만 돌려준다. 적용 결과는 **(1) 요청 단위
진행/결과**와 **(2) namespace quota 리소스 상태(DB↔live)**로 조회한다. 전체 Operations API는
[`operations.md`](operations.md) 참조.

### 요청 결과 폴링

```bash
# 요청 단건: 상태 + 전이 이력 + 결과 요약
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/requests/<request_id>" | jq

# 간단 폴링 루프 (terminal 상태까지)
REQ=<request_id>
until "${CURL[@]}" "$DMS_API_URL/api/v1/operations/requests/$REQ" \
  | jq -e '([.request.status] | inside(["Succeeded","Failed","Rejected","Conflict","BackendApplyFailed","UnknownAfterSideEffect"]))' >/dev/null
do sleep 2; done
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/requests/$REQ" | jq '.request.status'
```

성공 `Succeeded`, 거부 `Rejected`, backend 실패 `BackendApplyFailed` / `UnknownAfterSideEffect`
(후자는 부작용 불확실 → `POST …/requests/{id}:resolve`로 수동 처리; [`resource-management-fs.md`](resource-management-fs.md)
와 [`operations-runbook.md`](../operations-runbook.md) 참조).

### namespace quota 상태 조회 (DB desired + live + diff)

```bash
# 단건 (DB desired_hard + live spec.hard + diff + effective warning)
"${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/kubernetes/namespace-quotas/cluster-a/team-alpha" | jq

# 만료된 / 7일 내 만료 예정 목록
"${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/kubernetes/namespace-quotas/expiring?status=expired" | jq
"${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/kubernetes/namespace-quotas/expiring?status=expiring&within_seconds=604800" | jq
```

단건 응답 형태(요약):

```json
{
  "db":   { "status": "Succeeded", "desired_hard": { "requests.storage": "100Gi", "...": "..." } },
  "live": { "exists": true, "spec_hard": { "requests.storage": "100Gi", "...": "..." }, "status_used": { "...": "..." } },
  "diff": { "status": "Consistent", "issues": [] }
}
```

- `source=both|db|live`, `include_non_dms=true`(non-DMS RQ 목록), `include_status_used=true`(현재
  사용량)으로 범위를 조절한다.
- `diff.status`: `Consistent` / `Drifted`(`issues[]`에 키별 차이) / `Missing`(live 없음) 등.
- `/namespace-quotas/expiring` 쿼리 파라미터: `status`, `cluster_name`, `within_seconds`, `before`,
  `include_blocked`, `limit`.

---

## 주의 / FAQ

- **namespace는 사전에 존재해야 한다.** DMS는 ResourceQuota만 만들고 namespace는 만들지/지우지 않는다.
- **effective quota**: namespace에 non-DMS ResourceQuota가 있으면 실제 한도는 모든 RQ의 가장 엄격한
  값이다. DMS는 그것을 변경하지 않고 경고만 남긴다.
- **decrease guard**로 live `status.used` 아래로는 못 낮춘다 — 먼저 사용량을 줄이거나 PVC를 정리한다.
- **cross-cluster 불가**: `storage_class_quotas[].storage_name`이 가리키는 mapping의 `cluster_name`이
  요청 `cluster_name`과 다르면 reject(한 quota는 한 클러스터/namespace에만 적용).
- filesystem RM과 달리 **LDAP/owner/group 개념이 없다**(quota는 namespace 단위 k8s 오브젝트).
- **agentless**: 대상 클러스터에 DMS agent가 없어도 된다. k8s/CSI mapping의 sanity는 agent evidence가
  아니라 **ResourceQuota mutation transport**(RM worker가 실제로 quota를 적용하는 경로)의 도달성과
  `kubectl auth can-i create·patch·delete resourcequota` 권한으로 판정한다. namespace quota를 막는
  `Failed` 사유: 클러스터 미등록 · StorageClass 부재 · `csi_driver` 불일치 · transport 도달 실패
  (`mutation_transport_unreachable`) · 권한 부족(`mutation_no_permission`). 이 sanity 축의 구성·
  운영은 [`install/dms-04-rm-k8s-quota.md`](../../install/dms-04-rm-k8s-quota.md)에 있다.

---

## 전체 흐름 예 (cluster-a Ceph CSI)

control plane은 별도 클러스터, 대상은 Ceph CSI StorageClass(`ceph-rbd`/`ceph-cephfs`)를 가진
`cluster-a`. 1)·2) 설정은 [`install/dms-04-rm-k8s-quota.md`](../../install/dms-04-rm-k8s-quota.md).

```bash
# 1) 대상 클러스터 cluster-a 등록 (RBAC + SA kubeconfig + Secret/ConfigMap) — 설치 문서
#    확인: inventory에 cluster-a의 storage_classes/csi_drivers가 보여야 함
# 2) Ceph CSI storage mapping 등록 (ceph-rbd-a / ceph-cephfs-a) — 설치 문서
#    transport 도달·can-i 통과 시 sanity Ready (agent 불필요)

# 3) 대상 namespace는 미리 생성 (DMS는 ResourceQuota만 관리)
kubectl --context cluster-a create namespace dms-csi-validation

# 4) namespace quota 생성 (ceph-rbd + ceph-cephfs 동시 사용)
REQ=$("${CURL[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas" \
  -d '{
    "requester_id": "alice",
    "payload": {
      "cluster_name": "cluster-a",
      "namespace_name": "dms-csi-validation",
      "quota": {"requests_storage_bytes": 107374182400, "pvc_count": 20},
      "storage_class_quotas": [
        {"storage_name": "ceph-rbd-a",    "requests_storage_bytes": 53687091200, "pvc_count": 10},
        {"storage_name": "ceph-cephfs-a", "requests_storage_bytes": 53687091200, "pvc_count": 10}
      ],
      "expires_at": "2027-01-01T00:00:00Z"
    }
  }' | jq -r '.request_id')

# 5) 결과 폴링 → Succeeded
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/requests/$REQ" | jq '.request.status'

# 6) 상태 조회 → diff Consistent
"${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/kubernetes/namespace-quotas/cluster-a/dms-csi-validation" \
  | jq '{db: .db.status, diff: .diff.status, live_hard: .live.spec_hard}'

# 7) (참고) 대상 클러스터에서 직접 확인
kubectl --context cluster-a -n dms-csi-validation get resourcequota dms-storage-quota -o jsonpath='{.spec.hard}'
```

위 4)가 생성하는 live `spec.hard`:

```
requests.storage:                                              100Gi
persistentvolumeclaims:                                        20
ceph-rbd.storageclass.storage.k8s.io/requests.storage:         50Gi
ceph-rbd.storageclass.storage.k8s.io/persistentvolumeclaims:   10
ceph-cephfs.storageclass.storage.k8s.io/requests.storage:      50Gi
ceph-cephfs.storageclass.storage.k8s.io/persistentvolumeclaims: 10
```

라벨 `app.kubernetes.io/managed-by: dms`, `dms.io/resource-kind: kubernetes-namespace-quota` /
어노테이션 `dms.io/resource-key: cluster-a:dms-csi-validation`,
`dms.io/storage-names: ceph-rbd-a,ceph-cephfs-a`가 함께 부여된다.

---

## 다음 문서

- [`install/dms-04-rm-k8s-quota.md`](../../install/dms-04-rm-k8s-quota.md) — k8s 쿼터 RM 설치/사전
  준비(클러스터 등록, storage mapping, mutation transport RBAC).
- [`resource-management-fs.md`](resource-management-fs.md) — 파일시스템 RM API.
- [`operations.md`](operations.md) — operations 조회 API(요청·리소스·인벤토리 상태).
- [`operations-runbook.md`](../operations-runbook.md) — 운영 런북(`:resolve`, 만료·차단 운영 등).
- [`README.md`](README.md) — DMS API 개요와 인증(mTLS 운영 프로필).
- [`install/dms-06-configuration.md`](../../install/dms-06-configuration.md) — 환경변수 레퍼런스.
