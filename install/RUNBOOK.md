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
- `dms-dm-worker`는 Data Management 전제조건을 확인하기 전에는 0 replica로 둘 수 있다. `DMS_DM_JOB_IMAGE`, Volcano, artifact path, identity mapping, fresh DM Agent report가 준비된 환경에서는 1 replica로 운영할 수 있다.
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
 "mount_path":"/pvs","fileset_root":"/pvs/dms"}
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
수동 편집 불필요. 동기화 후 Agent는 재시작해야 새 설정을 반영한다.

ConfigMap 내용 확인:

```bash
ssh ion2401 "kubectl -n dms get configmap dms-agent-storages -o jsonpath='{.data.storages\.json}'" | jq '.storages[].storage_name'
```

변경 후 Agent rollout:

```bash
ssh ion2401 "kubectl -n dms rollout restart daemonset/dms-rm-agent && \
  kubectl -n dms rollout status daemonset/dms-rm-agent --timeout=120s"
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

- Mount를 볼 수 있는 node에서 Agent DaemonSet이 실행 중이 아니다.
- `DMS_AGENT_CLUSTER_NAME`이 storage mapping의 `cluster_name`과 일치하지 않는다.
- `storage_class_name` 또는 `csi_driver`가 live StorageClass와 일치하지 않는다.
- Agent report가 stale 상태다. `DMS_AGENT_REPORT_STALE_SECONDS`를 확인한다.

Agent report freshness 확인:

```bash
curl -sS "${DMS_CURL_OPTS[@]}" "$DMS_API_URL/api/v1/operations/agent-reports" | jq '.[0] | {node_name, freshness, updated_at}'
```

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

## Identity Mapping

### 조회

단일 사용자의 mapping 상태와 groups를 확인한다.

```bash
curl_dms "$DMS_API_URL/api/v1/identity-mappings?requester_id=<username>" \
  -H "authorization: Bearer $DMS_TOKEN" | jq '.[0] | {uid,gid,groups,status,ldap_lookup_at}'
```

상태별 필터링:

```bash
# Active 전체 (최근 100건)
curl_dms "$DMS_API_URL/api/v1/identity-mappings?status=Active" \
  -H "authorization: Bearer $DMS_TOKEN"

# Stale/failed 항목
curl_dms "$DMS_API_URL/api/v1/identity-mappings?failed=true" \
  -H "authorization: Bearer $DMS_TOKEN"
```

DNS 미설정 서버 또는 프록시 환경에서는 `/mgmt_storage/cocoa.song/.dms-secrets/dms-env.sh`를 사용한다.

```bash
source /mgmt_storage/cocoa.song/.dms-secrets/dms-env.sh
curl -sS "${DMS_CURL_OPTS[@]}" "$DMS_API_URL/api/v1/identity-mappings?requester_id=cocoa.song" | jq
```

`DMS_CURL_OPTS` 배열은 `--resolve`, `--noproxy`, mTLS 인증서, Bearer token, `x-dms-actor` 헤더를 포함한다.

### LDAP 전체 동기화 (sync-all)

DB의 모든 identity mapping에 대해 LDAP에서 uid/gid/groups를 일괄 재조회하여 갱신한다.
Disabled 상태인 mapping은 건너뛴다.

**dry-run (실제 DB 변경 없음):**

```bash
source /mgmt_storage/cocoa.song/.dms-secrets/dms-env.sh
curl -sS "${DMS_CURL_OPTS[@]}" \
  -X POST \
  "$DMS_API_URL/api/v1/identity-mappings/sync-all?identity_provider=ldap&dry_run=true" | jq
```

응답 예시:

```json
{
  "synced": 11158,
  "not_found": 0,
  "errors": 0,
  "updated": 0,
  "dry_run": true,
  "elapsed_seconds": 3.55,
  "total": 11158
}
```

**실제 실행:**

```bash
source /mgmt_storage/cocoa.song/.dms-secrets/dms-env.sh
curl -sS "${DMS_CURL_OPTS[@]}" \
  -X POST \
  "$DMS_API_URL/api/v1/identity-mappings/sync-all?identity_provider=ldap" | jq
```

`not_found`가 0이 아니면 해당 username 목록이 `not_found_usernames` 필드에 반환된다.
LDAP에서 찾을 수 없는 사용자는 `Stale` 상태로 표시된다.

**성능:** LDAP 연결 1개로 전체 그룹을 bulk fetch한 뒤 user 쿼리를 200명 배치 × 8 threads 병렬로 실행한다. ~11,000명 기준 4초 내외.

### 단일 사용자 refresh

LDAP를 재조회해 drift 감지(uid/gid/groups 변경 여부)만 수행한다. groups가 변경됐으면 `Stale`로 표시하며, 실제 DB 업데이트는 하지 않는다. 업데이트까지 하려면 `sync-all`을 사용한다.

```bash
source /mgmt_storage/cocoa.song/.dms-secrets/dms-env.sh
curl -sS "${DMS_CURL_OPTS[@]}" \
  -X POST \
  "$DMS_API_URL/api/v1/identity-mappings/ldap/<username>:refresh" | jq
```

### 신규/갱신 등록

```bash
source /mgmt_storage/cocoa.song/.dms-secrets/dms-env.sh
curl -sS "${DMS_CURL_OPTS[@]}" \
  -X PUT \
  -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/identity-mappings/ldap/<username>" \
  --data '{
    "requester_id": "<username>",
    "identity_provider": "ldap",
    "posix_username": "<linux_account>"
  }' | jq
```

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
