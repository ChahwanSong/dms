# DMS Phase 4 Implementation Prompt

이 문서는 `docs/dms-phase1.md`, `docs/dms-phase2.md`, `docs/dms-phase3.md` 완료 이후 네 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 4의 목표는 DMS가 처음으로 실제 backend mutation을 수행하되, 범위를 Kubernetes namespace storage quota create/apply 하나로 제한해 live 검증 가능한 단위로 닫는 것이다.

Phase 4는 Phase 3의 Agent inventory, storage mapping sanity, Planner guard를 전제로 한다. 이번 단계에서 DMS는 `cluster-b`의 `testbed-longhorn` StorageClass를 사용해 DMS-managed `ResourceQuota/dms-storage-quota`를 실제 Kubernetes API에 적용하고, PVC admission 결과까지 확인한다.

## Phase 4 목표

Phase 4의 핵심 기능은 다음 하나다.

**Kubernetes namespace storage quota live create/apply**

구현 완료 기준은 다음과 같다.

- DMS RM Worker가 실제 Kubernetes API 또는 `kubectl` read/write path를 통해 target managed cluster에 namespace와 ResourceQuota를 적용한다.
- Phase 4 live target은 `cluster-b`의 `testbed-longhorn` StorageClass다.
- DMS-managed ResourceQuota 이름은 항상 `dms-storage-quota`다.
- namespace-wide `requests.storage`, namespace-wide `persistentvolumeclaims`, StorageClass-specific `testbed-longhorn.storageclass.storage.k8s.io/requests.storage`를 렌더링하고 적용한다.
- 적용 후 Kubernetes API에서 ResourceQuota `spec.hard`, `status.hard`, `status.used`를 다시 읽어 operational PostgreSQL의 resource observed state/result에 저장한다.
- PVC admission live test를 수행한다.
  - quota 안의 PVC는 Bound 되어야 한다.
  - quota 초과 PVC는 Kubernetes admission에서 `exceeded quota` 또는 동등한 이유로 거부되어야 한다.
- failed storage mapping, missing StorageClass, failed readiness 상태에서는 기존 Phase 3 Planner guard가 live mutation을 막아야 한다.
- 검증 결과는 `docs/dms-phase4-verification.md`와 `docs/dms-done.md`에 command/output 포함해 기록한다.

## 왜 Phase 4에서 Kubernetes ResourceQuota만 하는가

Phase 3까지 DMS는 다음을 실제 테스트베드에서 확인했다.

- PostgreSQL source of truth
- observability DB 분리
- LDAP-only Identity Mapping
- Kubernetes read-only inventory
- storage mapping sanity
- RM/DM readiness 기반 Planner guard

따라서 이제 첫 backend side effect를 열 수 있다. 하지만 filesystem quota, Volcano/mpifileutils, DMS Agent DaemonSet까지 동시에 구현하면 실패 원인을 좁히기 어렵다. Kubernetes ResourceQuota는 다음 이유로 첫 live mutation에 적합하다.

- 테스트베드 `cluster-b`에 Longhorn과 `testbed-longhorn` StorageClass가 이미 준비되어 있다.
- ResourceQuota는 Kubernetes API object라서 apply/read/delete 결과가 명확하다.
- PVC admission success/failure로 실제 quota 효과를 검증할 수 있다.
- Phase 3의 storage mapping sanity가 live mutation 전 guard 역할을 하는지 확인할 수 있다.

## 현재 전제

Phase 1은 다음 골격을 제공했다.

- Resource Management request/plan/run/result lifecycle
- RM Worker runtime
- `KubernetesNamespaceQuotaAdapter` interface
- Kubernetes namespace quota API skeleton
- operational/observability repository skeleton

Phase 2는 다음 기반을 완료했다.

- 실제 테스트베드 PostgreSQL live baseline
- operational DB와 observability DB 분리
- LDAP direct read-only Identity Mapping

Phase 3는 다음 기반을 완료했다.

- Kubernetes read-only inventory
- Agent report persistence/freshness
- storage mapping sanity
- `storage_name -> cluster/storage_class` mapping
- Planner fail-closed guard
- Operational Query에서 inventory, mapping, action-required 조회

테스트베드 topology:

- `cluster-a`: DMS control cluster, PostgreSQL, OpenLDAP 접근 기준, self-managed RM target 가능
- `cluster-b`: managed cluster, Phase 4 live ResourceQuota target
- `cluster-b/testbed-longhorn`: Longhorn StorageClass, CSI provisioner `driver.longhorn.io`
- PostgreSQL: `192.168.56.11:30432`
- Kubernetes access: 테스트베드에서는 `ssh-kubectl` mode로 `c2-control`의 `kubectl`을 사용한다.

## 핵심 원칙

### 1. 첫 live mutation은 ResourceQuota create/apply로 제한한다

Phase 4에서 실제로 변경해도 되는 Kubernetes object는 다음으로 제한한다.

- 테스트용 namespace
- DMS-managed `ResourceQuota/dms-storage-quota`
- PVC admission 검증용 PVC/Pod

Phase 4 구현은 filesystem directory, filesystem quota, VolcanoJob, Longhorn volume 직접 API, StorageClass, CSI driver object를 변경하지 않는다.

### 2. DMS-managed ResourceQuota만 소유한다

DMS는 namespace 안에 있는 모든 ResourceQuota를 소유하지 않는다. Phase 4에서 DMS가 create/apply/delete/sync 대상으로 삼는 ResourceQuota는 이름이 `dms-storage-quota`인 object뿐이다.

ResourceQuota에는 DMS identity label/annotation을 붙인다.

권장 metadata:

```yaml
metadata:
  name: dms-storage-quota
  labels:
    app.kubernetes.io/managed-by: dms
    dms.io/resource-kind: kubernetes-namespace-quota
  annotations:
    dms.io/resource-key: cluster-b:<namespace_name>
    dms.io/request-id: <request_id>
    dms.io/storage-names: longhorn-b
```

### 3. Resource identity는 `cluster_name + namespace_name`이다

Kubernetes namespace storage quota resource의 DMS identity는 `cluster_name + namespace_name`이다.

`storage_name`은 resource identity가 아니다. `storage_name`은 StorageClass-specific quota entry를 렌더링하기 위한 mapping input이다.

### 4. StorageClass는 mapping에서 derive한다

요청 payload의 `storage_class_quotas[].storage_name`은 operational PostgreSQL의 storage mapping을 통해 `storage_class_name`으로 변환한다.

규칙:

- `storage_name` mapping이 없으면 Planner가 fail-closed 한다.
- mapping sanity가 `Ready`가 아니면 live mutation을 만들지 않는다.
- mapping의 `cluster_name`은 Kubernetes namespace quota target cluster와 같아야 한다.
- payload가 `storage_class_name`을 직접 포함한다면 mapping에서 derive한 값과 일치해야 한다. 불일치 시 `Rejected`.

### 5. API pod filesystem state는 여전히 무관하다

Phase 4는 Kubernetes API mutation만 수행한다. API pod 또는 control process의 local mount/path 존재 여부는 ResourceQuota plan, apply, verification 판단에 사용하지 않는다.

### 6. Backend side effect 전후 상태를 PostgreSQL에 남긴다

RM Worker는 backend side effect 전에 claim과 `Applying` 상태를 operational PostgreSQL에 commit해야 한다.

ResourceQuota apply 후에는 다음을 operational PostgreSQL에 기록한다.

- desired state: 요청/plan에서 렌더링한 quota
- applied state: 실제 apply한 namespace/resourcequota manifest 요약
- observed state: Kubernetes API에서 다시 읽은 namespace/resourcequota spec/status
- result verification summary: backend side effect 여부, ResourceQuota UID, hard/used 값, PVC admission 검증 결과

## Phase 4에서 하지 않을 것

다음은 Phase 4 범위가 아니다.

- Kubernetes namespace quota update 전체
- Kubernetes namespace quota delete
- Kubernetes namespace quota sync from live state
- block=ON hard limit zeroing
- block=OFF restore
- namespace deletion
- default quota policy 기반 reset
- multi StorageClass quota entry
- cross-cluster 일괄 apply
- DMS Agent DaemonSet 구현
- filesystem quota 또는 directory mutation
- CephFS native directory quota
- VolcanoJob live execution
- mpifileutils image build 또는 execution
- DMS API/Worker Kubernetes Deployment 완성
- mTLS ingress live validation

Phase 4 구현 중 update/delete/sync endpoint가 이미 존재하더라도 live adapter는 create/apply 경로만 활성화하고, 나머지는 stub 유지 또는 명시적 `NotImplemented`/fail-closed로 처리한다.

## API와 Payload

기존 endpoint를 사용한다.

```text
POST /api/v1/resource-management/kubernetes/namespace-quotas
```

Phase 4 권장 payload:

```json
{
  "requester_id": "portal:alice",
  "payload": {
    "cluster_name": "cluster-b",
    "namespace_name": "dms-phase4-<token>",
    "resource_type": "user",
    "allow_namespace_create": true,
    "quota": {
      "requests_storage_bytes": 134217728,
      "pvc_count": 2
    },
    "storage_class_quotas": [
      {
        "storage_name": "longhorn-b",
        "requests_storage_bytes": 134217728
      }
    ]
  }
}
```

초기 Phase 4는 위 shape 하나를 우선 지원한다.

명시적으로 거부할 payload:

- `cluster_name`이 `cluster-b`가 아닌 live smoke target인 경우에는 테스트에서는 사용하지 않는다. 구현은 generic하게 둘 수 있다.
- `storage_class_quotas[]`가 여러 개인 경우
- `storage_class_quotas[].storage_name`이 없는 경우
- mapping과 다른 `storage_class_name`이 직접 들어온 경우
- `quota.requests_storage_bytes` 또는 `quota.pvc_count`가 없고 default quota policy도 없는 경우
- quota 값이 음수, 0이거나 정수 byte가 아닌 경우

## ResourceQuota Rendering

128 MiB 예시:

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: dms-storage-quota
  namespace: dms-phase4-<token>
spec:
  hard:
    requests.storage: "128Mi"
    persistentvolumeclaims: "2"
    testbed-longhorn.storageclass.storage.k8s.io/requests.storage: "128Mi"
```

렌더링 규칙:

- 내부 DB와 API payload는 byte 정수를 기준으로 한다.
- Kubernetes manifest는 가능하면 binary suffix 또는 plain integer string으로 렌더링한다.
- live verification에서는 `128Mi = 134217728` bytes를 사용한다.
- `persistentvolumeclaims`는 PVC count quota로 사용한다.
- StorageClass-specific key 형식:

```text
{storage_class_name}.storageclass.storage.k8s.io/requests.storage
```

## Kubernetes Live Adapter

새 adapter를 추가한다.

권장 이름:

- `KubernetesNamespaceQuotaLiveAdapter`
- 또는 `KubectlKubernetesNamespaceQuotaAdapter`

지원 mode:

- Phase 4 필수: `ssh-kubectl`
- 가능하면 기존 `DMS_KUBERNETES_INVENTORY_MODE`와 비슷하게 `DMS_KUBERNETES_MUTATION_MODE`를 둔다.

권장 설정:

- `DMS_KUBERNETES_MUTATION_MODE=ssh-kubectl`
- `DMS_CLUSTER_CONTROL_HOSTS_JSON={"cluster-a":"c1-control","cluster-b":"c2-control"}`
- `DMS_KUBERNETES_MUTATION_TIMEOUT_SECONDS=30`

필수 method 동작:

### `read_namespace(cluster_name, namespace_name)`

- namespace existence 조회
- 있으면 labels/annotations 요약 반환
- 없으면 `exists=false`

### `create_namespace(plan)`

- `allow_namespace_create=true`일 때만 namespace create
- 이미 있으면 no-op
- 생성/존재 상태를 applied/observed state에 기록

### `apply_resource_quota(plan)`

- namespace 존재 확인
- 필요하면 namespace 생성
- `ResourceQuota/dms-storage-quota` manifest 생성
- `kubectl apply -f -` 또는 Kubernetes API patch/apply 수행
- apply 후 `kubectl get resourcequota dms-storage-quota -o json` 재조회
- observed state에 `spec.hard`, `status.hard`, `status.used`, `metadata.uid`, labels/annotations 기록

### `delete_resource_quota(plan)`

Phase 4에서는 live delete를 구현하지 않는다. 호출되면 fail-closed하거나 stub 경로와 명확히 분리한다.

### `sync_live_state(plan)`

Phase 4에서는 구현하지 않는다.

## Planner Integration

Planner는 Phase 3 guard를 유지하고 Kubernetes namespace quota create에 다음을 추가해야 한다.

- payload top-level `storage_name`만 보는 legacy path에 의존하지 않는다.
- `storage_class_quotas[].storage_name` 전체를 검사한다.
- Phase 4 초기 구현에서는 `storage_class_quotas[]` entry가 1개 초과이면 `Rejected`.
- mapping에서 `storage_class_name`을 derive해 plan desired state에 포함한다.
- namespace-wide quota와 StorageClass-specific quota를 분리해 plan desired state에 넣는다.
- live mutation 전에 mapping `readiness.resource_management == Ready`여야 한다.
- 실패 result에는 `backend_side_effect=false`를 포함한다.

Plan metadata 권장 shape:

```json
{
  "resource_kind": "KubernetesNamespaceQuota",
  "backend_side_effect_owner": "rm-worker",
  "kubernetes_backend": {
    "cluster_name": "cluster-b",
    "namespace_name": "dms-phase4-abc123",
    "resource_quota_name": "dms-storage-quota",
    "storage_classes": [
      {
        "storage_name": "longhorn-b",
        "storage_class_name": "testbed-longhorn"
      }
    ]
  }
}
```

## RM Worker Integration

RM Worker runtime은 existing lifecycle 원칙을 유지한다.

순서:

1. claimable RM plan 조회
2. plan claim 저장
3. run state `Applying` 저장
4. live Kubernetes adapter 호출
5. ResourceQuota read-back verification
6. resource desired/applied/observed state 저장
7. terminal result 저장
8. observability event 저장

예외 처리:

- apply 전 validation failure는 `Rejected` 또는 `PreflightFailed` 계열로 backend side effect 없이 종료한다.
- apply 호출 후 verification 실패 또는 결과 저장 실패 가능성이 있으면 `UnknownAfterSideEffect` 또는 `RecoveryNeeded`로 남긴다.
- adapter command timeout은 backend 결과를 알 수 없으므로 action-required에서 운영자가 확인 가능해야 한다.

## PVC Admission Live Verification

Phase 4 live script는 DMS flow가 ResourceQuota를 만든 뒤 별도 Kubernetes command로 실제 admission을 확인한다.

검증 namespace:

```text
dms-phase4-<token>
```

검증 PVC:

- allowed PVC: 64Mi, StorageClass `testbed-longhorn`
- over-quota PVC: 96Mi, StorageClass `testbed-longhorn`

128Mi quota에서 64Mi PVC 하나는 Bound 되어야 한다. 그 뒤 96Mi PVC를 추가하면 총 요청량이 160Mi가 되므로 admission이 거부되어야 한다.

PVC 검증 후 cleanup은 다음 리소스로 제한한다.

- verification PVC/Pod
- DMS-created test namespace

단, cleanup 실패가 DMS 기능 성공을 의미하지 않도록 `docs/dms-phase4-verification.md`에 명확히 기록한다.

## Operational Query

Phase 4에서 query가 보여야 하는 정보:

- request history
- resource state
- ResourceQuota applied/observed state
- action-required issue
- diagnostic events

필요하면 다음 endpoint를 보강한다.

- `GET /api/v1/operations/requests/{request_id}`
- `GET /api/v1/operations/resources/kubernetes`
- `GET /api/v1/operations/action-required`

초기 구현에서 dedicated Kubernetes resource list endpoint가 없다면, verification script는 repository 또는 direct DB query로 evidence를 확인해도 된다. 단, `dms-done.md`에는 어떤 경로로 검증했는지 명시한다.

## Observability

Phase 4에서 남겨야 하는 diagnostic event:

- Kubernetes namespace quota live apply started
- namespace create skipped/already-exists/created
- ResourceQuota apply completed
- ResourceQuota read-back verification completed
- PVC admission verification completed
- ResourceQuota apply failed
- Planner rejected Kubernetes quota request due to mapping/sanity/readiness

Critical lifecycle state는 operational PostgreSQL에 남겨야 한다. Diagnostic event 저장 실패가 core lifecycle 성공으로 둔갑하면 안 된다.

## 데이터 모델 보강

가능하면 기존 `resources`와 `results` 구조를 우선 사용한다.

부족하면 expand-compatible migration으로 다음 정보를 표현한다.

- Kubernetes namespace quota resource identity: `cluster_name + namespace_name`
- DMS-managed ResourceQuota name
- rendered hard limits
- applied manifest summary
- observed ResourceQuota UID/resourceVersion
- observed `spec.hard`
- observed `status.hard`
- observed `status.used`
- PVC admission verification summary

새 table을 추가하기 전에 기존 `resources.desired_state`, `resources.applied_state`, `resources.observed_state`, `results.verification_summary`로 충분한지 먼저 판단한다.

## 테스트베드 Live Verification

Phase 4는 mock이 아니라 실제 테스트베드 backend mutation을 검증해야 한다.

읽어야 하는 문서:

- `/home/mason/workspace/testbed/TOPOLOGY.md`
- `/home/mason/workspace/testbed/PostgreSQL.md`
- `/home/mason/workspace/testbed/Longhorn.md`
- `/home/mason/workspace/testbed/testbed-info.json`
- `/home/mason/workspace/testbed/testbed-summary.json`
- `docs/dms-done.md`

사전 확인 command:

```bash
ssh c2-control "kubectl get nodes -o wide"
ssh c2-control "kubectl get storageclass testbed-longhorn -o yaml"
ssh c2-control "kubectl -n longhorn-system get pods"
```

최소 live flow:

1. 새 PostgreSQL operational/observability DB를 만든다.
2. DMS migrations를 적용한다.
3. `cluster-b` Kubernetes read-only inventory를 조회한다.
4. `longhorn-b -> cluster-b/testbed-longhorn` storage mapping을 sanity `Ready`로 만든다.
5. `cluster-b` RM Agent report를 제출한다.
6. Kubernetes namespace quota create request를 제출한다.
7. Planner가 RM plan을 만든다.
8. RM Worker가 live Kubernetes adapter로 ResourceQuota를 적용한다.
9. PostgreSQL resource/result state를 확인한다.
10. `kubectl -n <namespace> get resourcequota dms-storage-quota -o yaml`로 live object를 확인한다.
11. 64Mi PVC를 만들고 Bound 확인한다.
12. 96Mi PVC 생성을 시도하고 exceeded quota admission failure를 확인한다.
13. action-required에 예상치 못한 issue가 없는지 확인한다.
14. verification namespace를 cleanup한다.
15. `docs/dms-phase4-verification.md`와 `docs/dms-done.md`를 업데이트한다.

## Phase 4 검증 매트릭스

| Area | Required verification | Expected evidence |
| --- | --- | --- |
| Migration | SQLite/PostgreSQL migration 성공 | Phase 1-3 tests 유지, live DB migration rows |
| Mapping guard | Longhorn mapping sanity Ready | `storage_mappings.sanity_status=Ready` |
| Planner | K8S quota create plan 생성 | plan desired state에 namespace/quota/storage_class 포함 |
| Fail-closed | bad mapping은 plan 없음 | terminal result `backend_side_effect=false` |
| Namespace | namespace create or exists | live `kubectl get ns` output |
| ResourceQuota apply | `dms-storage-quota` created/applied | live `kubectl get resourcequota -o yaml` |
| ResourceQuota hard | hard values match request | `requests.storage=128Mi`, `persistentvolumeclaims=2`, SC-specific key |
| PostgreSQL result | desired/applied/observed state 저장 | `resources`, `results` query output |
| PVC success | 64Mi PVC admitted and Bound | live PVC output |
| PVC failure | 96Mi extra PVC rejected | `exceeded quota` event/error |
| Observability | live apply events written | observability DB diagnostic events |
| Documentation | evidence recorded | `docs/dms-phase4-verification.md`, `docs/dms-done.md` |

## 구현 순서

1. 현재 Kubernetes namespace quota skeleton과 RM Worker adapter boundary를 확인한다.
2. Phase 4 payload/plan desired state shape를 확정한다.
3. ResourceQuota hard limit renderer를 구현한다.
4. `KubernetesNamespaceQuotaLiveAdapter`를 추가한다.
5. `ssh-kubectl` command wrapper와 JSON/YAML manifest apply path를 구현한다.
6. Planner가 `storage_class_quotas[].storage_name`을 mapping에서 `storage_class_name`으로 derive하도록 보강한다.
7. RM Worker runtime이 live adapter를 주입받아 실행할 수 있게 한다.
8. ResourceQuota read-back observed state 저장을 구현한다.
9. Unit tests로 renderer, plan shape, fail-closed guard를 검증한다.
10. Live verification script를 추가한다.
11. 테스트베드에서 PostgreSQL + cluster-b Longhorn ResourceQuota live flow를 실행한다.
12. PVC admission success/failure를 검증한다.
13. `docs/dms-phase4-verification.md`와 `docs/dms-done.md`를 업데이트한다.

## 구현 및 검증 진입점

주요 진입점:

- `src/dms/adapters.py`: Kubernetes namespace quota live adapter
- `src/dms/planner.py`: Kubernetes namespace quota plan enrichment and guard
- `src/dms/workers.py`: RM Worker live adapter execution path
- `src/dms/repositories.py`: resource/result observed state persistence if needed
- `src/dms/config.py`: mutation mode/control host/timeout settings
- `src/dms/api.py`: payload validation if needed
- `tests/test_phase4_kubernetes_quota.py`: renderer/planner/adapter contract tests
- `scripts/phase4_kubernetes_quota_live.py`: live verification body
- `scripts/verify-phase4-testbed.sh`: PostgreSQL DB creation and testbed orchestration
- `docs/dms-phase4-verification.md`: executed evidence
- `docs/dms-done.md`: Done status update

대표 검증 명령:

```bash
cd /home/mason/workspace/dms
/tmp/dms-phase3-venv/bin/python -m pytest -q
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase4-testbed.sh
```

## Phase 4 문서 산출물

Phase 4 완료 시 다음 문서를 최신화한다.

- `docs/dms-phase4-verification.md`: live test command, output, DB evidence, Kubernetes evidence
- `docs/dms-done.md`: Phase 4 done/not-done 상태와 재검증 command
- `testbed/Longhorn.md`: ResourceQuota/PVC 검증 방식이 바뀐 경우
- `docs/dms-design.md`: 실제 구현 API/status/result shape가 설계와 달라진 경우

## Phase 4 완료 후 다음 Phase 후보

Phase 4 완료 후에는 다음 중 하나로 진행한다.

### Phase 5A: Kubernetes quota update/block/delete/sync

Phase 4의 create/apply가 안정적이면 같은 backend 안에서 범위를 넓힌다.

- update existing `dms-storage-quota`
- quota decrease guard using `status.used`
- block=ON hard limit zeroing
- block=OFF restore
- delete DMS-managed ResourceQuota only
- sync DB from live ResourceQuota
- DB/live consistency check

### Phase 5B: DMS Agent DaemonSet

Phase 3의 synthetic Agent report를 실제 node-local probe로 대체한다.

- RM Agent DaemonSet on managed clusters
- DM Agent on control cluster worker nodes
- mount/tool/credential/network probe
- report freshness and identity evidence

### Phase 5C: Data Management preflight and Volcano scan

Kubernetes ResourceQuota live mutation 이후 read-only Data Management부터 시작한다.

- POSIX identity preflight using LDAP mapping
- tool option registry
- `dscan` VolcanoJob live execution
- artifact URI persistence
