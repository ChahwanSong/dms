# DMS 운영 런북

## 설치 직후 첫 점검

Control cluster에서 DMS workload 상태를 확인한다.

```bash
kubectl --context dms-control -n dms get pods,jobs,svc,ingress
kubectl --context dms-control -n dms wait --for=condition=complete job/dms-migrate --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-api --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-planner --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-rm-worker --timeout=180s
```

문제가 있으면 바로 describe/log를 본다.

```bash
kubectl --context dms-control -n dms describe pod -l app.kubernetes.io/name=dms-api
kubectl --context dms-control -n dms logs deploy/dms-api --tail=200
kubectl --context dms-control -n dms logs job/dms-migrate --tail=200
```

Target cluster RBAC와 kubeconfig 권한을 확인한다.

```bash
KUBECONFIG=/tmp/dms-kubeconfigs/cluster-a.kubeconfig kubectl get nodes
KUBECONFIG=/tmp/dms-kubeconfigs/cluster-a.kubeconfig kubectl get storageclass
KUBECONFIG=/tmp/dms-kubeconfigs/cluster-a.kubeconfig kubectl auth can-i create resourcequotas --all-namespaces
KUBECONFIG=/tmp/dms-kubeconfigs/cluster-a.kubeconfig kubectl auth can-i patch namespaces
```

`no`가 나오면 `install/kubernetes/target-cluster-rbac.yaml` 적용 여부와 ServiceAccount token을 다시 확인한다.

## 일일 Health Check

DMS API에 접근할 수 있는 operator workstation에서 실행한다.

```bash
export DMS_API_URL="https://dms.example.internal"
export DMS_TOKEN="replace-with-secret"
export DMS_CLIENT_CERT="client.crt"
export DMS_CLIENT_KEY="client.key"
export DMS_CA_CERT="dms-api-ca.crt"

curl_dms() {
  curl -fsS --cert "$DMS_CLIENT_CERT" --key "$DMS_CLIENT_KEY" --cacert "$DMS_CA_CERT" "$@"
}

install/scripts/verify-install.sh
```

운영 mTLS profile에서는 `DMS_ACTOR`를 설정하지 않는다. DMS API는 ingress/edge proxy가 검증한 client certificate subject에서 actor를 derive한다.

인증 경계를 직접 확인한다.

```bash
curl -v "$DMS_API_URL/api/v1/operations/action-required" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN"
```

정상이라면 HTTP 200과 JSON 배열이 나온다. client certificate 없이 같은 endpoint를 호출하면 실패해야 한다.

```bash
curl -v "$DMS_API_URL/api/v1/operations/action-required" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN"
```

수동 확인:

```bash
kubectl -n dms get pods,jobs,svc
kubectl -n dms logs deploy/dms-planner --tail=100
kubectl -n dms logs deploy/dms-rm-worker --tail=100
curl_dms "$DMS_API_URL/api/v1/operations/action-required" \
  -H "authorization: Bearer $DMS_TOKEN"
```

정상 steady state:

- `dms-api` 사용 가능.
- `dms-planner` 실행 중.
- `dms-rm-worker` 실행 중.
- `dms-dm-worker`는 Data Management 전제조건을 확인하기 전에는 0 replica로 둘 수 있다. `DMS_DM_JOB_IMAGE`, Volcano, artifact path, DM identity LDAP(DMS_LDAP_*) 설정 (preflight 직접 조회), fresh DM Agent report가 준비된 환경에서는 1 replica로 운영할 수 있다.
- Storage-capable RM/DM node마다 agent report가 fresh 상태다.
- 운영 request가 사용하는 storage mapping은 `readiness.resource_management=Ready`를 표시한다.
- 해결되지 않은 action-required 항목이 없다.

## mTLS/API 인증 문제

증상별 확인:

| 증상 | 확인할 것 |
| --- | --- |
| TLS handshake 실패 | client cert가 `dms-client-ca`로 발급됐는지, ingress Secret 이름이 맞는지 확인 |
| HTTP 401 `invalid token` | `DMS_TOKEN`과 `DMS_AUTH_SHARED_TOKEN`이 같은지 확인 |
| HTTP 401 `mtls_subject_required` | ingress가 upstream evidence header를 전달하는지 확인 |
| HTTP 401 `mtls_verify_failed` | ingress verify result가 `SUCCESS`인지 확인 |
| API Pod startup 실패 | `DMS_DEFAULT_ACTOR`가 비어 있는지, mTLS env 조합이 맞는지 확인 |

Ingress annotation 확인:

```bash
kubectl --context dms-control -n dms get ingress dms-api -o yaml
```

필수 annotation:

```yaml
nginx.ingress.kubernetes.io/auth-tls-secret: dms/dms-client-ca
nginx.ingress.kubernetes.io/auth-tls-verify-client: "on"
nginx.ingress.kubernetes.io/auth-tls-pass-certificate-to-upstream: "true"
```

API direct access가 열려 있으면 mTLS evidence header spoofing 위험이 있다. NetworkPolicy를 확인한다.

```bash
kubectl --context dms-control -n dms get networkpolicy dms-api-from-ingress-only -o yaml
```

## Storage Mapping 관리

### 조회

전체 목록:

```bash
source /mgmt_storage/cocoa.song/.dms-secrets/dms-env.sh
curl -sS "${DMS_CURL_OPTS[@]}" "$DMS_API_URL/api/v1/operations/storage-mappings" | jq '.[].storage_name'
```

클러스터별 필터:

```bash
curl -sS "${DMS_CURL_OPTS[@]}" \
  "$DMS_API_URL/api/v1/operations/storage-mappings?cluster_name=pvs-dms" | jq '.[].storage_name'
```

단건 조회:

```bash
curl -sS "${DMS_CURL_OPTS[@]}" \
  "$DMS_API_URL/api/v1/operations/storage-mappings/cephfs-pvs-dms" \
  | jq '{storage_name, sanity_status, readiness}'
```

### 등록 (POST)

`ssh_host`를 생략하면 agent 보고 기반 `rm_candidates` 중 Ready 노드가 자동 선택된다.

```bash
curl -sS "${DMS_CURL_OPTS[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings" \
  -d '{
    "storage_name": "cephfs-pvs-dms",
    "backend_template": {
      "backend_type": "cephfs",
      "cluster_name": "pvs-dms",
      "mount_path": "/mgmt_storage",
      "managed_root": "/mgmt_storage/root",
      "rm_worker_nodes": ["ion2401","ion2402","ion2403","ion2404","ion2405","ion2406"]
    },
    "cluster_name": "pvs-dms",
    "storage_class_name": "rook-cephfs"
  }' | jq '{storage_name, status}'
```

`ssh_host`를 고정하고 싶다면 `backend_template`에 명시한다:

```json
"ssh_host": "ion2401"
```

이미 존재하면 upsert(덮어쓰기)로 동작한다.

**`ssh_host` 자동 선택 우선순위:**

1. `ssh_host` 명시 → 해당 노드 사용
2. 생략 시 → `sanity_result.agent_observed.rm_candidates` 중 `status: Ready` 첫 번째 노드
3. rm_candidates 없음(최초 등록 직후 등) → `rm_worker_nodes[0]` fallback

**Backend별 등록 예시:**

CephFS:
```json
{"backend_type":"cephfs","cluster_name":"pvs-dms","mount_path":"/mgmt_storage",
 "managed_root":"/mgmt_storage/root","csi_driver":"rook-ceph.cephfs.csi.ceph.com"}
```

GPFS:
```json
{"backend_type":"gpfs","cluster_name":"pvs-dms","filesystem_name":"pvs",
 "mount_path":"/pvs","managed_root":"/pvs/dms"}
```

WekaFS (CSI 미설치 환경에서는 csi_driver 생략):
```json
{"backend_type":"wekafs","cluster_name":"pvs-dms","filesystem_name":"pvs_weka",
 "mount_path":"/pvs_weka","managed_root":"/pvs_weka/dms",
 "rm_worker_nodes":["ion2402","ion2403"]}
```

**WekaFS 운영 주의:**
- `weka fs quota set/list/reset` CLI는 cluster 인증 필요. RM worker가 ssh로 접속할 호스트에서 `weka user login` 또는 `WEKA_USERNAME`/`WEKA_PASSWORD`/`WEKA_ORG` 환경변수를 사전에 설정해야 quota 작업이 가능.
- WEKA path quota는 capacity_bytes만 지원. `file_count`(inode quota)는 backend가 명시적으로 거절.
- quota 작업이 필요하면 `weka_profile`로 별도 프로파일 지정 가능.

### 수정 (PATCH)

backend_template 또는 cluster_name/storage_class_name 변경 시:

```bash
curl -sS "${DMS_CURL_OPTS[@]}" \
  -X PATCH -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings/cephfs-pvs-dms" \
  -d '{
    "storage_name": "cephfs-pvs-dms",
    "backend_template": { ... },
    "cluster_name": "pvs-dms",
    "storage_class_name": "rook-cephfs"
  }' | jq '{storage_name, status}'
```

- body의 `storage_name`은 path와 반드시 일치해야 함 (불일치 시 400)
- 진행 중인 request/data_job이 있으면 409 반환

### 삭제 (DELETE)

```bash
curl -sS "${DMS_CURL_OPTS[@]}" \
  -X DELETE \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings/cephfs-pvs-dms" \
  | jq '{storage_name, deleted}'
```

- 존재하지 않으면 404
- 진행 중인 작업이 있으면 409

### Sanity check (수동 재실행)

```bash
curl -sS "${DMS_CURL_OPTS[@]}" \
  -X POST \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings/cephfs-pvs-dms:check" \
  | jq '{storage_name, status}'
```

### Agent ConfigMap 자동 동기화

POST/PATCH/DELETE 시 `dms-agent-storages` ConfigMap이 자동으로 동기화된다.
수동 편집 불필요. 단, Agent는 startup에 storages.json을 한 번만 읽으므로(loop에서 재읽기
안 함) **동기화 후 재시작해야 새 설정을 반영한다.**

ConfigMap 내용 확인:

```bash
ssh ion2401 "kubectl -n dms get configmap dms-agent-storages -o jsonpath='{.data.storages\.json}'" | jq '.storages[].storage_name'
```

변경 후 Agent rollout — RM·DM **둘 다** (새 storage의 `resource_management`는 RM agent,
`data_management`는 DM agent가 채우므로 하나만 재시작하면 나머지 축이 Missing으로 남는다):

```bash
ssh ion2401 "kubectl -n dms rollout restart daemonset/dms-rm-agent daemonset/dms-dm-agent && \
  kubectl -n dms rollout status daemonset/dms-rm-agent daemonset/dms-dm-agent --timeout=180s"
# 반영 확인: 새 storage가 (마운트된 노드에서) Ready 로 나오는지
POD=$(ssh ion2401 "kubectl -n dms get pods -l app.kubernetes.io/name=dms-dm-agent -o jsonpath='{.items[0].metadata.name}'")
ssh ion2401 "kubectl -n dms exec $POD -- dms agent-probe --once" | jq '.mounts[] | {storage_name,status}'
```

RBAC이 없는 경우(403 에러) 다음을 적용한다:

```bash
ssh ion2401 "kubectl -n dms apply -f - <<'EOF'
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: dms-agent-storages-configmap
  namespace: dms
rules:
- apiGroups: [\"\"]
  resources: [\"configmaps\"]
  resourceNames: [\"dms-agent-storages\"]
  verbs: [\"get\", \"patch\", \"update\"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: dms-remote-agent-storages-configmap
  namespace: dms
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: dms-agent-storages-configmap
subjects:
- kind: ServiceAccount
  name: dms-remote
  namespace: dms
EOF"
```

### Readiness 문제 진단

요청이 `storage_mapping_sanity`로 거절되면:

흔한 원인:

- **Agent가 워커 노드의 마운트를 못 본다 (readiness 전부 false / 매트릭스 전부 ✗).** Agent는
  컨테이너에서 돌아 기본 `DMS_AGENT_MOUNTINFO_PATH=/proc/self/mountinfo`(컨테이너 마운트)로는 호스트
  스토리지 마운트가 안 보여 모든 storage가 Missing이 된다. `agent-daemonset.yaml`에 호스트 mount table
  bind-mount(`/host/proc/1/mountinfo`)와 `DMS_AGENT_MOUNTINFO_PATH`가 있는지 확인한다(CONFIGURATION.md
  "마운트 readiness" 절 · 1.install §10.2-5). 진단:
  ```bash
  POD=$(kubectl -n dms get pods -l app.kubernetes.io/name=dms-dm-agent -o jsonpath='{.items[0].metadata.name}')
  kubectl -n dms exec "$POD" -- printenv DMS_AGENT_MOUNTINFO_PATH   # /host/proc/1/mountinfo 여야 함
  kubectl -n dms exec "$POD" -- dms agent-probe --once | jq '.mounts[] | {storage_name,status}'
  ```
  전부 `Missing`이면 mountinfo bind-mount가 빠진 것 — 위 문서의 패치로 두 DaemonSet에 볼륨/env 추가.
- Mount를 볼 수 있는 node에서 Agent DaemonSet이 실행 중이 아니다.
- `DMS_AGENT_CLUSTER_NAME`이 storage mapping의 `cluster_name`과 일치하지 않는다.
- `storage_class_name` 또는 `csi_driver`가 live StorageClass와 일치하지 않는다.
- Agent report가 stale 상태다. `DMS_AGENT_REPORT_STALE_SECONDS`를 확인한다.
- (k8s/CSI, agent 없는 managed 클러스터) 해당 클러스터가 `DMS_CLUSTER_KUBECONFIGS_JSON`(또는
  `DMS_CLUSTER_CONTROL_HOSTS_JSON`)에 없거나, API/sanity-reconciler가 kubectl/ssh-kubectl inventory로
  클러스터를 못 읽는다 → `cluster_missing`/`storage_class_missing`으로 `Failed`. 클러스터 등록과
  reconciler의 kubeconfig secret 마운트(`/etc/dms/kubeconfigs`)를 확인한다. namespace quota는 RM agent
  없이 `Degraded`만으로 사용 가능하다(§ k8s RM 가이드).

Agent report freshness 확인:

```bash
curl -sS "${DMS_CURL_OPTS[@]}" "$DMS_API_URL/api/v1/operations/agent-reports" | jq '.[0] | {node_name, freshness, updated_at}'
```

### CSI/k8s 매핑 sanity `Failed` 진단 (mutation transport)

CSI/k8s namespace-quota 매핑(`backend_type`이 `cephfs`/`wekafs`/`gpfs`가 **아닌** 매핑, 예: `ceph-csi`,
`gpfs-csi`, `weka-csi`)은 RM/DM Agent evidence가 아니라 **ResourceQuota mutation transport**로 판정한다.
즉 RM worker가 실제로 quota를 적용할 때 쓰는 경로(`kubectl` 또는 `ssh-kubectl`)로 대상 클러스터에 도달해
ResourceQuota를 생성/수정/삭제할 권한이 있는지를 sanity가 직접 검사한다. sanity service는 대상 클러스터에서
`kubectl auth can-i create|patch|delete resourcequota -A`를 실행한다.

결과는 매핑 단건 조회의 `sanity_result.readiness.kubernetes_mutation`(Ready/Failed/Unknown)과
`sanity_result.mutation_observed`에 노출된다.

```bash
curl -sS "${DMS_CURL_OPTS[@]}" \
  "$DMS_API_URL/api/v1/operations/storage-mappings/<storage_name>" \
  | jq '{status: .sanity_status, kubernetes_mutation: .sanity_result.readiness.kubernetes_mutation,
         errors: .sanity_result.errors, mutation_observed: .sanity_result.mutation_observed}'
```

CSI 매핑이 `Failed`이면 `sanity_result.errors[].code`가 다음 둘 중 하나다. 둘은 원인과 조치가 다르다.

| error code | 의미 | 원인 | 조치 |
| --- | --- | --- | --- |
| `mutation_transport_unreachable` | `reachable: false` — transport 자체가 대상 클러스터에 도달 못 함 | ssh-kubectl: `control_host` 미설정/오타/SSH 불가, 원격 호스트에 `kubectl` 없음, kubeconfig 미설정, 연결 timeout, `can-i`가 yes/no(rc 0/1)가 아닌 다른 rc로 실패 | transport 경로를 직접 점검 (아래) |
| `mutation_no_permission` | `reachable: true, can_mutate: false` — 도달은 하지만 `can-i`가 no | 대상 클러스터에서 ResourceQuota create/patch/delete RBAC 부족 | RBAC 부여 (아래) |

`mutation_observed`로 어느 transport/호스트로 무엇이 실패했는지 구분한다.

```bash
curl -sS "${DMS_CURL_OPTS[@]}" \
  "$DMS_API_URL/api/v1/operations/storage-mappings/<storage_name>" \
  | jq '.sanity_result.mutation_observed
        | {mode, control_host, reachable, can_mutate, permissions, detail}'
```

- `mode`: `kubectl`(기본, 로컬) 또는 `ssh-kubectl`. 매핑별 override는 `backend_template.mutation_mode`로 지정한다(`control_host`만 단독 지정은 등록 422 거부 — `mutation_mode:"ssh-kubectl"` 명시 필요).
- `control_host`: ssh-kubectl일 때 ssh 대상 호스트. 매핑별 override는 `backend_template.control_host`.
- `reachable`/`can_mutate`/`permissions{create,patch,delete}`: `permissions` 값은 `true`(허용)/`false`(거절)/`null`(미확인). unreachable이면 셋 다 `null`.
- `detail`: 실패 원인 메시지 (ssh stderr, can-i 비정상 rc 등).

수정 후 sanity를 재실행해 `kubernetes_mutation`이 `Ready`로 돌아오는지 확인한다.

```bash
curl -sS "${DMS_CURL_OPTS[@]}" -X POST \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings/<storage_name>:check" \
  | jq '{status, kubernetes_mutation: .mapping.readiness.kubernetes_mutation}'
```

**`mutation_transport_unreachable` 조치**

매핑의 transport 설정은 전역 mode(`DMS_KUBERNETES_MUTATION_MODE`, 기본 `kubectl`)와 클러스터별
`DMS_CLUSTER_CONTROL_HOSTS_JSON`(ssh-kubectl) / `DMS_CLUSTER_KUBECONFIGS_JSON`(kubectl)에서 오며,
매핑별로 `backend_template`의 `mutation_mode`/`control_host`가 우선한다.

- ssh-kubectl 모드: `control_host`가 매핑의 `cluster_name`에 대해 등록돼 있는지, 해당 호스트로 SSH가 되는지,
  그 호스트에서 `kubectl`이 대상 클러스터를 가리키는지 확인한다.

  ```bash
  # control_host 후보를 그대로 사용해 sanity가 내부적으로 하는 것과 동일한 명령을 재현
  ssh <control_host> "kubectl auth can-i create resourcequota -A"
  ssh <control_host> "kubectl auth can-i patch resourcequota -A"
  ssh <control_host> "kubectl auth can-i delete resourcequota -A"
  ```

  `DMS_CLUSTER_CONTROL_HOSTS_JSON`에 cluster 항목이 없으면 sanity는 `missing control host for cluster ...`
  detail과 함께 unreachable로 본다. API/sanity-reconciler Deployment의 해당 env와 매핑 `cluster_name`이
  일치하는지 확인한다. (reconciler는 kubeconfig를 `/etc/dms/kubeconfigs`에 마운트한다.)

- kubectl 모드: 해당 클러스터 kubeconfig가 `DMS_CLUSTER_KUBECONFIGS_JSON`에 있고 유효한지 확인한다.

  ```bash
  KUBECONFIG=<kubeconfig> kubectl auth can-i create resourcequota -A
  ```

설정 변경(env/Secret) 후에는 `dms-api`와 `dms-sanity-reconciler`를 rollout restart해야 새 값이 반영된다.

**`mutation_no_permission` 조치**

transport는 정상이고 RBAC만 부족한 경우다. 대상 클러스터에서 ResourceQuota를 모든 namespace 범위로
생성/수정/삭제할 권한을 부여한다. 첫 점검의 target cluster RBAC(`install/kubernetes/target-cluster-rbac.yaml`)와
ServiceAccount token이 동일한 ID로 적용됐는지 확인한다.

```bash
# 부여 후 사용 중인 자격으로 직접 확인 (셋 다 yes 여야 can_mutate=true)
ssh <control_host> "kubectl auth can-i create resourcequota -A"   # ssh-kubectl
KUBECONFIG=<kubeconfig> kubectl auth can-i delete resourcequota -A  # kubectl
```

> CSI 매핑이 sanity `Failed`이면 planner의 `_reject_unsafe_storage_mapping` 가드가 해당 클러스터/namespace의
> namespace-quota 요청을 `storage_mapping_sanity`로 차단한다. transport/RBAC를 고쳐 `kubernetes_mutation`이
> `Ready`(또는 최소 non-`Failed`)가 되면 요청이 다시 진행된다. (참고: probe가 미설정이면 — 클러스터가
> `DMS_CLUSTER_*_JSON`에 없을 때 — `kubernetes_mutation`은 `Unknown`이며 namespace-quota는 `Unknown`을
> 차단하지 않는다.)

## Background loops (sanity-reconciler / retention)

운영 백그라운드 loop는 모두 singleton(`replicas: 1`) Deployment이고 heartbeat 기반
livenessProbe로 자가 치유한다.

- `dms-sanity-reconciler` — storage-mapping readiness 재계산. (위 Readiness 진단 참고)
- `dms-retention` — `agent_reports` history 나이 기준 prune (`dms retention --loop`).
  100+ node가 분당 1회 보고하면 `agent_reports`가 수백만 행으로 자란다. node-health 읽기는
  `agent_node_current`(node별 최신 보고 1행, 매 ingest마다 같은 트랜잭션에서 UPSERT)에서
  하므로 history prune은 **안전하다** — 침묵한 node도 마지막 보고가 계속 보인다. retention은
  순수 나이 기준이며 batch(각 독립 트랜잭션)로 삭제해 긴 lock을 잡지 않는다.

```bash
# 동작 확인 (manifest: install/kubernetes/retention.yaml)
kubectl -n dms logs deploy/dms-retention --tail=50      # {"processed": N} = 삭제 행 수
kubectl -n dms exec deploy/dms-retention -- cat /tmp/dms-retention.heartbeat
# 1회만 실행 (디버그)
kubectl -n dms exec deploy/dms-retention -- dms retention
```

retention window는 `DMS_AGENT_REPORT_RETENTION_SECONDS`(기본 30일)이며 **parse 시 7일 이상으로
floor**되어 72h node-metrics window 아래로 내려갈 수 없다. loop가 멈춰도 읽기는 (더 큰 table 위에서)
계속 동작하므로 correctness 의존성이 아니다.

## Kubernetes Namespace Quota Incident

Namespace quota 하나를 check한다.

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas/<cluster>/<namespace>:check" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{"requester_id":"operator","payload":{"include_effective_quota":true}}'
```

Scope를 audit한다.

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas:audit" \
  -H "authorization: Bearer $DMS_TOKEN" \
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

Live ResourceQuota가 없지만 DB desired state가 있으면 update 또는 block/unblock으로 다시 적용한다. Live state가 operator에 의해 의도적으로 변경된 경우에는 drift를 검토한 뒤에만 sync를 실행한다.

Target cluster live object 확인:

```bash
kubectl --context <target-context> -n <namespace> get resourcequota dms-storage-quota -o yaml
kubectl --context <target-context> -n <namespace> describe resourcequota dms-storage-quota
```

Planner/RM Worker 처리 흐름 확인:

```bash
curl_dms "$DMS_API_URL/api/v1/operations/requests?requester_id=<requester>&limit=20" \
  -H "authorization: Bearer $DMS_TOKEN"

kubectl --context dms-control -n dms logs deploy/dms-planner --tail=200
kubectl --context dms-control -n dms logs deploy/dms-rm-worker --tail=200
```

## Data Management Incident

Live Data Management는 read-only `scan`, same-node `dsync`,
separated-role `nsync`, `drm`을 DB policy/API 기반 node/process resource model로
실행한다. `sync`와 `rm`은 preview 없이 실행되면 안 되며, `ConfirmPending` 상태에서
explicit confirm을 받은 뒤에만 execution job을 생성해야 한다.

data job 목록과 상세를 확인한다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/data-jobs?limit=20" \
  -H "authorization: Bearer $DMS_TOKEN"

curl_dms "$DMS_API_URL/api/v1/data-management/scan/jobs/<job_id>" \
  -H "authorization: Bearer $DMS_TOKEN"

curl_dms "$DMS_API_URL/api/v1/data-management/sync/jobs/<job_id>" \
  -H "authorization: Bearer $DMS_TOKEN"

curl_dms "$DMS_API_URL/api/v1/data-management/rm/jobs/<job_id>" \
  -H "authorization: Bearer $DMS_TOKEN"
```

confirm 대기 job을 확인한다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/data-jobs?state=ConfirmPending&limit=20" \
  -H "authorization: Bearer $DMS_TOKEN"
```

Preflight failure에서 먼저 확인할 것:

- requester Identity Mapping이 `Active`인지 확인한다.
- target/source/destination storage mapping의 `readiness.data_management`가 `Ready`인지 확인한다.
- DM Agent report가 Fresh이고 mount, required tool(`dscan`, `dsync`, `drm`, `nsync`), credential, network, POSIX user evidence를 포함하는지 확인한다.
- `GET /api/v1/data-management/policies/<operation>` 결과가 enabled이고 필요한 node/process 수가 현재 eligible node 수와 맞는지 확인한다.
- target/source/destination path가 storage-relative이고 traversal/absolute path가 아닌지 확인한다.
- `sync`는 source read/traverse와 destination parent write/execute 권한을 확인한다.
- `rm`은 parent write/execute delete 권한과 target traverse/read 권한을 확인한다.
- `preflight_result.effective_resource_model`의 `eligible_nodes`, `worker_pod_count`,
  `processes_per_node`, `process_count`, `queue`, `priority_class`가 의도한 policy와
  일치하는지 확인한다.

Volcano/runtime failure에서 먼저 확인할 것:

```bash
kubectl --context dms-control -n dms get job.batch.volcano.sh
kubectl --context dms-control -n dms get mpijob
kubectl --context dms-control -n dms get podgroup
kubectl --context dms-control -n dms describe job.batch.volcano.sh <volcano-job-name>
kubectl --context dms-control -n dms logs deploy/dms-dm-worker --tail=200
```

성공한 data job은 `data_jobs.artifact_uri`와 phase별
`result_summary.*_uri`를 기록한다. DB에는 파일 내용이 아니라 URI와 요약만
저장된다. `file://` backend의 실제 결과 파일은 다음 위치에 있다.

```text
scan:
  <DMS_DM_ARTIFACT_BASE_URI path>/<job_id>/
    summary.json
    dscan-report.json
    stdout.log
    stderr.log
  <DMS_DM_ARTIFACT_BASE_URI path>/<job_id>/mpi/
    submitted.yaml
    launch.json
    workers.json
    scheduler.json
    mpirun.json

sync/rm:
  <DMS_DM_ARTIFACT_BASE_URI path>/<job_id>/preview/
    summary.json
    command.json
    stdout.log
    stderr.log
  <DMS_DM_ARTIFACT_BASE_URI path>/<job_id>/execution/
    summary.json
    command.json
    stdout.log
    stderr.log
  <DMS_DM_ARTIFACT_BASE_URI path>/<job_id>/mpi/
    submitted.yaml
    launch.json
    workers.json
    scheduler.json
    mpirun.json
```

Multi-node MPI Data Management 환경에서는 추가로 다음을 확인한다.

- MPI Operator가 Volcano gang scheduling으로 설치되어 있고 `MPIJob` CRD가 존재한다.
- mpifileutils job image가 Open MPI 기반이며 `mpirun`, `ompi_info`, `dscan`, `dsync`,
  `drm`, `nsync`를 포함한다.
- submitted CR YAML이 artifact `mpi/submitted.yaml`에 기록된다.
- `mpi/workers.json`, `mpi/scheduler.json`, `mpi/mpirun.json`이 모든 job에 존재한다.
- DMS가 eligible mounted node set을 제출했고, 실제 scheduled worker nodes는 scheduler
  결과에서 기록됐다.
- worker node당 worker pod 1개가 affinity/anti-affinity로 유지된다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/action-required" \
  -H "authorization: Bearer $DMS_TOKEN"
```

artifact parse failure가 나면 먼저 DM Worker가 artifact base를 traverse/read할 수
있는지 확인한다. 사용자 target directory가 `0750`이고 artifact base가 그 하위에
있으면 Volcano pod는 쓸 수 있어도 DM Worker가 읽지 못할 수 있다. 운영에서는 별도
DMS-managed artifact mount/PVC/object prefix를 사용한다.

preview를 검토한 뒤 confirm한다.

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/data-management/jobs/<job_id>:confirm" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "alice",
    "confirm": true,
    "preview_observed_hash": "sha256:<preview-fingerprint>",
    "memo": "reviewed preview artifacts"
  }'
```

`PreviewExpired`가 되면 같은 job을 재사용하지 말고 새 request를 제출한다.
`sync`/`rm`이 `Failed`, `TimedOut`, partial mutation risk action-required로 닫히면
artifact와 live filesystem 상태를 확인한 뒤 새 request로 재시도한다.

## DM Identity

DM은 더 이상 identity를 DB에 저장하지 않는다. 데이터 job의 preflight(dm-worker) 단계에서 owner_username(기본값은 requester_id, 실제 POSIX username으로 override 가능)을 키로 LDAP를 read-only로 직접 조회해 uid/gid/groups를 해석한다. 조회 설정은 `DMS_LDAP_*` + `DMS_DM_IDENTITY_PROVIDER`로 관리한다.

DM 측에서 영속화되는 유일한 identity 상태는 **denylist**다. denylist는 requester/owner/group 단위의 즉시 kill-switch이자 admission block이며, 비어 있는 것이 기본값으로 이 경우 모두 허용한다.

DNS 미설정 서버 또는 프록시 환경에서는 `/mgmt_storage/cocoa.song/.dms-secrets/dms-env.sh`를 사용한다. `DMS_CURL_OPTS` 배열은 `--resolve`, `--noproxy`, mTLS 인증서, Bearer token, `x-dms-actor` 헤더를 포함한다.

### 사용자/그룹 즉시 차단 (denylist 등록)

특정 requester, owner, group을 즉시 차단한다. 차단되면 해당 subject의 데이터 job은 preflight에서 `identity_denied`로 Rejected된다.
`subject_type`은 `requester`, `owner`, `group` 중 하나다. 성공 시 `200 {"status":"Denied"}`를 반환한다.

```bash
source /mgmt_storage/cocoa.song/.dms-secrets/dms-env.sh

# requester 차단
curl -sS "${DMS_CURL_OPTS[@]}" \
  -X PUT \
  -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/data-management/identity-denylist/requester/<username>" \
  --data '{"reason": "incident-1234 격리"}' | jq

# owner 차단
curl -sS "${DMS_CURL_OPTS[@]}" \
  -X PUT \
  -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/data-management/identity-denylist/owner/<username>" \
  --data '{"reason": "compromised account"}' | jq

# group 차단
curl -sS "${DMS_CURL_OPTS[@]}" \
  -X PUT \
  -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/data-management/identity-denylist/group/<groupname>" \
  --data '{"reason": "그룹 전체 작업 일시 중단"}' | jq
```

### 차단 해제 (denylist 제거)

```bash
source /mgmt_storage/cocoa.song/.dms-secrets/dms-env.sh

# subject_type ∈ {requester, owner, group}
curl -sS "${DMS_CURL_OPTS[@]}" \
  -X DELETE \
  "$DMS_API_URL/api/v1/data-management/identity-denylist/<subject_type>/<subject>" | jq
```

성공 시 `200 {"status":"Allowed"}`를 반환한다. 항목이 없으면 `404`를 반환한다.

### denylist 조회

현재 차단된 모든 항목을 나열한다.

```bash
source /mgmt_storage/cocoa.song/.dms-secrets/dms-env.sh
curl -sS "${DMS_CURL_OPTS[@]}" \
  "$DMS_API_URL/api/v1/data-management/identity-denylist" | jq
```

### Identity 실패 트러블슈팅

데이터 job의 identity 해석이 실패하면 해당 data-job의 `preflight_result.reason`을 확인한다. 다음 네 가지 중 하나다 (과거의 `missing_active_identity_mapping`을 대체한다).

| reason | 의미 | 조치 |
| --- | --- | --- |
| `ldap_unavailable` | LDAP 연결 불가 (fail closed) | LDAP / `DMS_LDAP_*` 복구 후 job 재시도 (아래 참고) |
| `ldap_not_configured` | DM identity LDAP 미설정 | `DMS_LDAP_*` + `DMS_DM_IDENTITY_PROVIDER` 설정 |
| `ldap_identity_not_found` | LDAP에 해당 사용자 없음 | username/owner_username, POSIX username override 확인 |
| `identity_denied` | denylist에 의해 차단됨 | 필요 시 denylist에서 해제 (위 참고) |

dm-worker observability 이벤트로도 동일 상황을 추적할 수 있다.

- `data_job_identity_not_found` — `ldap_identity_not_found`에 대응
- `data_job_identity_lookup_failed` — `ldap_unavailable`에 대응
- `data_job_identity_denied` — `identity_denied`에 대응

사용자가 실제로 LDAP에서 해석되는지 직접 확인하려면 LDAP를 직접 조회한다.

```bash
source /mgmt_storage/cocoa.song/.dms-secrets/dms-env.sh
ldapsearch -x -H "$DMS_LDAP_URI" -b "$DMS_LDAP_BASE_DN" "(uid=<user>)" uidNumber gidNumber
```

### LDAP 장애 시 (ldap_unavailable)

DM은 TTL 캐시를 두지 않으며 LDAP 장애 시 **fail closed**한다. LDAP가 다운되면 preflight는 stale identity를 사용하지 않고 `ldap_unavailable`로 Rejected되고 job이 멈춘다(명확한 에러로 표시됨).

조치:

1. LDAP 서비스와 `DMS_LDAP_*` 설정을 복구한다. (위 `ldapsearch`로 연결을 확인할 수 있다.)
2. flush할 캐시는 없다. LDAP가 정상화되면 해당 데이터 job을 그대로 재시도하면 된다.

## Expiry 처리

만료된 filesystem resource를 나열한다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/filesystems/expiring?status=expired" \
  -H "authorization: Bearer $DMS_TOKEN"
```

만료된 Kubernetes namespace quota resource를 나열한다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/kubernetes/namespace-quotas/expiring?status=expired" \
  -H "authorization: Bearer $DMS_TOKEN"
```

Dry-run sweep:

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas:expiration-sweep" \
  -H "authorization: Bearer $DMS_TOKEN" \
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

실제 sweep은 dry-run target을 검토한 뒤에만 실행한다. System/admin resource는 policy에 의해 skip된다.

실제 sweep:

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas:expiration-sweep" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "operator",
    "payload": {
      "dry_run": false,
      "action": "block",
      "scope": {"cluster_name": "cluster-a"},
      "max_targets": 20
    }
  }'
```

Filesystem expiration sweep도 같은 원칙으로 dry-run 후 실행한다.

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/filesystems:expiration-sweep" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "operator",
    "payload": {
      "dry_run": true,
      "action": "block",
      "storage_name": "cephfs-a",
      "max_targets": 20
    }
  }'
```

## Worker Recovery

운영 조회를 먼저 확인한다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/work-summary" \
  -H "authorization: Bearer $DMS_TOKEN" | jq

curl_dms "$DMS_API_URL/api/v1/operations/runs/active" \
  -H "authorization: Bearer $DMS_TOKEN" | jq

curl_dms "$DMS_API_URL/api/v1/operations/runs/stale" \
  -H "authorization: Bearer $DMS_TOKEN"
```

RM/DM worker는 장시간 backend call 중 run heartbeat로 `lease_expires_at`을 갱신한다. 그래도 worker process가 죽었거나 DB heartbeat가 끊겨 expired lease가 생길 수 있으므로 startup 또는 planned shutdown 전후에 stale guard를 실행한다.

```bash
install/scripts/dms-startup-recovery-check.sh
```

이 script는 `POST /api/v1/operations/runs:mark-stale`을 호출한다. `Claimed` 상태에서 만료된 run은 `StaleClaim`으로 표시되고, `Running`/`Applying`/`Verifying` 상태에서 만료된 run은 `RecoveryNeeded`로 표시된다. DMS는 이런 run을 자동 재실행하지 않는다.

Worker를 안전하게 재시작하거나 control cluster 작업을 시작하는 기본 순서:

```bash
install/scripts/dms-planned-shutdown.sh \
  --reason "worker restart $(date -Iseconds)" \
  --timeout-seconds 900 \
  --poll-seconds 10

# 필요한 Kubernetes/host 작업을 수행한다.

install/scripts/dms-startup-recovery-check.sh

export DMS_WORKER_DEPLOYMENTS="dms-rm-worker"
install/scripts/dms-resume.sh \
  --reason "worker restart completed $(date -Iseconds)" \
  --replicas 1
```

Active backend mutation 중에는 무작정 scale-down하지 않는다. `dms-planned-shutdown.sh`가 `ready_for_shutdown=true`를 확인할 때까지 기다리고, `RecoveryNeeded`, `UnknownAfterSideEffect`, `BackendApplyFailed`가 있으면 target live state와 최근 logs를 먼저 확인한다.

## PostgreSQL Backup

업그레이드 전과 schema-changing code를 적용하기 전에 두 database를 모두 백업한다.

```bash
pg_dump "$DMS_DATABASE_URL" > dms-operational-$(date +%Y%m%d%H%M%S).sql
pg_dump "$DMS_OBSERVABILITY_DATABASE_URL" > dms-observability-$(date +%Y%m%d%H%M%S).sql
```

운영 rollout 전에 staging DB에 먼저 restore하고 새 image로 `dms migrate`를 실행한다.

## 업그레이드 절차

1. `dms-planned-shutdown.sh`로 drain mode에 진입한다.
2. Script가 `ready_for_shutdown=true`를 확인하고 worker Deployment를 0으로 scale할 때까지 기다린다.
3. PostgreSQL을 backup한다.
4. 새 image를 `dms-migrate` Job에 적용하고 실행한다.
5. `dms-api`, `dms-planner`, `dms-rm-worker` image를 교체하고 rollout한다.
6. API가 올라오면 `dms-startup-recovery-check.sh`를 실행한다.
7. `dms-resume.sh`로 control state를 normal로 되돌리고 RM worker를 scale up한다.
8. `install/scripts/verify-install.sh`를 실행한다.

명령 예시:

```bash
install/scripts/dms-planned-shutdown.sh \
  --reason "DMS upgrade to $NEW_DMS_IMAGE" \
  --timeout-seconds 900 \
  --poll-seconds 10

pg_dump "$DMS_DATABASE_URL" > dms-operational-$(date +%Y%m%d%H%M%S).sql
pg_dump "$DMS_OBSERVABILITY_DATABASE_URL" > dms-observability-$(date +%Y%m%d%H%M%S).sql

# /tmp/dms-control-plane.yaml의 image 값을 먼저 $NEW_DMS_IMAGE로 바꾼다.
kubectl --context dms-control -n dms delete job dms-migrate --ignore-not-found=true
kubectl --context dms-control apply -f /tmp/dms-control-plane.yaml
kubectl --context dms-control -n dms wait --for=condition=complete job/dms-migrate --timeout=180s

kubectl --context dms-control -n dms set image deploy/dms-api api="$NEW_DMS_IMAGE"
kubectl --context dms-control -n dms set image deploy/dms-planner planner="$NEW_DMS_IMAGE"
kubectl --context dms-control -n dms set image deploy/dms-rm-worker rm-worker="$NEW_DMS_IMAGE"

kubectl --context dms-control -n dms rollout status deploy/dms-api --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-planner --timeout=180s

install/scripts/dms-startup-recovery-check.sh

export DMS_WORKER_DEPLOYMENTS="dms-rm-worker"
install/scripts/dms-resume.sh \
  --reason "DMS upgrade completed $(date -Iseconds)" \
  --replicas 1

install/scripts/verify-install.sh
```

`job/dms-migrate`는 immutable field가 많으므로 새 image로 바꿀 때 기존 Job을 삭제하고 manifest로 다시 만드는 편이 단순하다.

## Rollback

새 image rollout 후 문제가 있으면 직전 image로 되돌린다.

```bash
kubectl --context dms-control -n dms rollout undo deploy/dms-api
kubectl --context dms-control -n dms rollout undo deploy/dms-planner
kubectl --context dms-control -n dms rollout undo deploy/dms-rm-worker
kubectl --context dms-control -n dms rollout status deploy/dms-api --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-planner --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-rm-worker --timeout=180s
```

Schema migration이 이미 실행된 뒤라면 image rollback만으로 충분하지 않을 수 있다. schema-changing release는 staging DB restore로 먼저 검증한다.

Rollback 전에도 가능하면 `dms-planned-shutdown.sh`로 drain mode에 진입한다. Rollback 후에는 `dms-startup-recovery-check.sh`와 `dms-resume.sh`를 같은 순서로 실행한다.

## 알려진 운영 공백

- Data Management `scan`/`sync`/`rm` live execution은 Volcano/MPI Operator/mpifileutils image/artifact path/identity evidence가 준비된 환경에서만 DM Worker replica를 올린다. `sync`/`rm`은 preview/confirm guard 없이 실행되면 안 된다.
- `DMS_DM_KUBERNETES_MODE=stub`은 로컬 테스트/dev 전용이다.
- 운영 Helm/Kustomize packaging은 아직 완성되지 않았다. 여기 있는 manifest는 명시적 YAML template이다.
- 서로 다른 mount를 가진 multiple local filesystem RM worker는 storage-aware worker claiming 없이는 안전하지 않다.
- WEKA filesystem backend는 아직 구현되지 않았다. Kubernetes namespace quota만 필요한 WEKA CSI StorageClass는 공통 live ResourceQuota adapter로 사용할 수 있지만, filesystem resource 요청은 fail-closed된다.
