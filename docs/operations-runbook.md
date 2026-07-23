# DMS 운영 런북

설치가 끝난 DMS 컨트롤플레인을 **운영**하는 문서다. 매일의 health check, 인증(mTLS) 장애 진단,
스토리지 매핑 관리, 계획적 종료·재개, 업그레이드·rollback, 백업, 장애 대응까지 **운영 중 절차**를
모은다.

> **설치는 여기 없다.** 클러스터/이미지/`control-plane.yaml`/mTLS/ingress/migration 등 **배포**는
> [`../install/`](../install/README.md)에, 각 API의 **요청 페이로드·옵션·응답**은
> [`api/`](api/README.md)에 있다. 이 문서는 그 둘을 **운영 관점에서 엮고 교차링크**한다.

---

## 0. 규약 (인증 · curl · 클러스터 컨텍스트)

### 인증 — 운영 = mTLS-verified 프로필

운영 컨트롤플레인은 `control-plane.yaml`에서 `DMS_REQUIRE_MTLS_HEADER=true` +
`DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`로 뜬다. 신뢰된 ingress가 **client certificate를 검증**하고
그 결과를 upstream(DMS)에 전달하며, DMS는 **actor를 인증서 subject에서 파생**한다
(`actor = {DMS_MTLS_ACTOR_PREFIX}<subject>`, 기본 prefix `mtls:`). 따라서:

- 운영 curl은 **`--cert`/`--key`/`--cacert`로 인증**한다. **평문 `x-dms-actor`는 신뢰하지 않으므로
  보내지 않는다**(파생 actor와 다르면 `actor_evidence_conflict`로 거부).
- **`DMS_DEFAULT_ACTOR`는 비어 있어야 한다.** `DMS_REQUIRE_MTLS_HEADER=true`인데 값이 있으면
  **API pod startup이 실패**한다(fail-closed).
- shared bearer token(`DMS_AUTH_SHARED_TOKEN`)은 **기본 배포에서 필수**다(mTLS 위에 gate로 얹히고,
  내부 평면 `dms-api-internal`(mTLS off)의 유일한 인증). 모든 운영 curl이 `Authorization: Bearer …`를 함께 보낸다.

이 문서의 모든 `curl` 예시는 아래 헬퍼를 전제한다.

```bash
export DMS_API_URL="https://dms.example.internal"
export DMS_CLIENT_CERT=operator.crt DMS_CLIENT_KEY=operator.key DMS_CA_CERT=dms-api-ca.crt
export DMS_TOKEN="<DMS_AUTH_SHARED_TOKEN>"   # 기본 필수 — shipped dms-secrets의 토큰과 동일 값

# 운영(mTLS): 인증서로 인증, x-dms-actor는 보내지 않는다.
DMS_CURL=(--cert "$DMS_CLIENT_CERT" --key "$DMS_CLIENT_KEY" --cacert "$DMS_CA_CERT")
[[ -n "$DMS_TOKEN" ]] && DMS_CURL+=(-H "authorization: Bearer $DMS_TOKEN")
curl_dms() { curl -fsS "${DMS_CURL[@]}" "$@"; }
```

`install/scripts/`의 헬퍼 스크립트(`dms-planned-shutdown.sh`·`dms-resume.sh`·
`dms-startup-recovery-check.sh`·`verify-install.sh`)는 **같은 env 이름**
(`DMS_API_URL`·`DMS_CLIENT_CERT`·`DMS_CLIENT_KEY`·`DMS_CA_CERT`·`DMS_TOKEN`)을 읽는다 — 위 값을
export 해두면 그대로 동작한다. `DMS_ACTOR`는 운영에서 **unset**으로 둔다.

> **부연 — dev/testbed 프로필(`DMS_REQUIRE_MTLS_VERIFIED_HEADER=false`).** 인증서 없이 평문
> `Authorization: Bearer <token>` + `x-dms-actor: operator`로 호출한다. 요청/응답 shape은 동일하다.
> 운영에서는 이 형태를 쓰지 않는다. 인증 전체 규약은 [`api/README.md`](api/README.md) §3,
> 관련 env는 [`../install/dms-06-configuration.md`](../install/dms-06-configuration.md).

### 클러스터 컨텍스트

별도 표기가 없는 `kubectl`은 **컨트롤 클러스터** 컨텍스트에서 네임스페이스 `dms`에 대해 실행한다
(`kubectl -n dms …`). **target(관리 대상) 클러스터**를 대상으로 하는 명령은 `--context <target-context>`
또는 `KUBECONFIG=<kubeconfig>`를 명시한다.

### 정상 steady state (요약)

- `dms-api`(replicas 2)·`dms-planner`·`dms-rm-worker`·`dms-dm-worker`가 모두 실행 중.
- **`dms-dm-worker`는 기본 `replicas: 1`(= DM 활성)**. DM을 의도적으로 끈 환경에서만 0이며,
  0을 "정상 유휴"로 오해하지 말 것. DM 잡은 **Volcano 네이티브 Job**(`DMS_DM_SCHEDULER_BACKEND=volcano-job`)으로
  실행되고 **Kubeflow MPI Operator는 필요 없다**.
- 스토리지 매핑을 쓰는 축(RM/DM)마다 agent report가 fresh이고 `readiness.*=Ready`.
- 해결되지 않은 `action-required` 항목이 없다.

---

## 1. 설치 직후 첫 점검

컨트롤플레인 workload와 migration을 확인한다.

```bash
kubectl -n dms get pods,jobs,svc,ingress
kubectl -n dms wait --for=condition=complete job/dms-migrate --timeout=180s
for d in dms-api dms-planner dms-rm-worker dms-dm-worker; do
  kubectl -n dms rollout status deploy/$d --timeout=180s
done
```

문제가 있으면 바로 describe/log를 본다.

```bash
kubectl -n dms describe pod -l app.kubernetes.io/name=dms-api
kubectl -n dms logs deploy/dms-api --tail=200
kubectl -n dms logs job/dms-migrate --tail=200
```

k8s 쿼터 RM을 쓴다면 target cluster RBAC/kubeconfig 권한을 확인한다(설정은
[`../install/dms-04-rm-k8s-quota.md`](../install/dms-04-rm-k8s-quota.md)).

```bash
KUBECONFIG=<cluster-a.kubeconfig> kubectl get nodes
KUBECONFIG=<cluster-a.kubeconfig> kubectl get storageclass
KUBECONFIG=<cluster-a.kubeconfig> kubectl auth can-i create resourcequotas --all-namespaces
KUBECONFIG=<cluster-a.kubeconfig> kubectl auth can-i patch namespaces
```

`no`가 나오면 `install/kubernetes/target-cluster-rbac.yaml` 적용 여부와 ServiceAccount token을
다시 확인한다.

---

## 2. 일일 health check

한 번에 훑는 스윕:

```bash
install/scripts/verify-install.sh
```

이 스크립트는 `/healthz`, control-state, inventory, storage-mappings, work-summary, active/stale run,
worker-agent-health, data scan/sync/rm 목록, action-required를 순서대로 조회한다(위 §0 env 사용).

핵심 지표 하나만 볼 때:

```bash
curl_dms "$DMS_API_URL/api/v1/operations/action-required" | jq
curl_dms "$DMS_API_URL/api/v1/operations/work-summary" | jq
```

**인증 경계 확인.** mTLS 프로필에서는 **client cert 없이** 같은 endpoint를 호출하면 ingress가
거부해야 한다(정상이면 거부).

```bash
# 성공해야 함 (cert 있음)
curl_dms "$DMS_API_URL/api/v1/operations/action-required" | jq 'length'
# 실패해야 함 (cert 없음 → ingress가 400/403)
curl -sS --cacert "$DMS_CA_CERT" "$DMS_API_URL/api/v1/operations/action-required" -o /dev/null -w '%{http_code}\n'
```

steady-state 판정 기준은 §0 "정상 steady state"를 쓴다. DM 전제조건(Volcano+Queue/PriorityClass·
`DMS_DM_JOB_IMAGE`·dms-agent 이미지·공유 artifact·DM identity)은 설치 시 갖춘다 —
[`../install/dms-01-prerequisites.md`](../install/dms-01-prerequisites.md) ·
[`../install/dms-05-dm-jobs.md`](../install/dms-05-dm-jobs.md).

---

## 3. 인증(mTLS/API) 문제 진단

| 증상 | 확인할 것 |
| --- | --- |
| TLS handshake 실패 | client cert가 `dms-client-ca`로 발급됐는지, ingress Secret 이름(`dms/dms-client-ca`)이 맞는지 |
| HTTP 401 `invalid token` | `DMS_TOKEN`과 `DMS_AUTH_SHARED_TOKEN`이 같은지 (토큰은 기본 필수) |
| HTTP 401 `mtls_subject_required` | ingress가 upstream에 cert evidence header를 전달하는지 |
| HTTP 401 `mtls_verify_failed` | ingress의 client-cert verify 결과가 `SUCCESS`인지 |
| `actor_evidence_conflict` | 평문 `x-dms-actor`를 보내고 있지 않은지 (운영에서는 보내지 않는다) |
| API pod startup 실패 | `DMS_DEFAULT_ACTOR`가 **비어 있는지**, mTLS env 조합이 맞는지 |

ingress annotation 확인(참조 매니페스트: `install/kubernetes/ingress.example.yaml`):

```bash
kubectl -n dms get ingress dms-api -o yaml | grep -A1 auth-tls
```

필수 annotation:

```yaml
nginx.ingress.kubernetes.io/auth-tls-secret: "dms/dms-client-ca"
nginx.ingress.kubernetes.io/auth-tls-verify-client: "on"
nginx.ingress.kubernetes.io/auth-tls-pass-certificate-to-upstream: "true"
```

API에 ingress를 우회한 직접 접근이 열려 있으면 evidence header spoofing 위험이 있다.
차단 NetworkPolicy를 확인한다.

```bash
kubectl -n dms get networkpolicy dms-api-from-ingress-only -o yaml
```

> 인증서 발급·ingress의 verify/evidence 구성은 [`../install/dms-02-core.md`](../install/dms-02-core.md),
> 인증 env 레퍼런스는 [`../install/dms-06-configuration.md`](../install/dms-06-configuration.md).

---

## 4. Storage mapping 운영

스토리지 매핑은 백엔드(경로·클러스터·마운트)와 그 **readiness/sanity**를 담는 레코드다. RM/DM
요청은 대상 매핑이 `Ready`여야 진행된다. 요청 페이로드 전체 스키마는
[`api/resource-management-fs.md`](api/resource-management-fs.md)에 있고, 여기서는 **운영 절차**만 다룬다.

### 4.1 조회

```bash
# 전체 이름
curl_dms "$DMS_API_URL/api/v1/operations/storage-mappings" | jq '.[].storage_name'
# 클러스터별 필터
curl_dms "$DMS_API_URL/api/v1/operations/storage-mappings?cluster_name=cluster-a" | jq '.[].storage_name'
# 단건 (sanity·readiness)
curl_dms "$DMS_API_URL/api/v1/operations/storage-mappings/cephfs-a" \
  | jq '{storage_name, sanity_status, readiness}'
```

### 4.2 등록 · 수정 · 삭제

**등록(POST).** `ssh_host`를 생략하면 agent 보고의 `rm_candidates` 중 `Ready` 노드가 자동 선택된다.

```bash
curl_dms -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings" \
  -d '{
    "storage_name": "cephfs-a",
    "backend_template": {
      "backend_type": "cephfs",
      "cluster_name": "cluster-a",
      "mount_path": "/cephfs",
      "managed_root": "/cephfs/root",
      "rm_worker_nodes": ["node1","node2","node3"]
    },
    "cluster_name": "cluster-a",
    "storage_class_name": "rook-cephfs"
  }' | jq '{storage_name, status}'
```

- `ssh_host` 자동 선택 우선순위: ① `backend_template.ssh_host` 명시 → 그 노드 · ② 생략 →
  `sanity_result.agent_observed.rm_candidates` 중 `Ready` 첫 노드 · ③ 후보 없음(최초 등록 직후) →
  `rm_worker_nodes[0]` fallback.
- 이미 존재하면 **upsert(덮어쓰기)** 로 동작한다. 백엔드별 필드(GPFS `filesystem_name`, WekaFS,
  CSI mutation transport 등)는 [`../install/dms-03-rm-filesystem.md`](../install/dms-03-rm-filesystem.md) ·
  [`api/resource-management-fs.md`](api/resource-management-fs.md).
- WekaFS 운영 주의: quota 작업은 RM worker가 ssh로 접속하는 호스트에서 `weka user login`(또는
  `WEKA_USERNAME`/`PASSWORD`/`ORG`)이 선행돼야 한다. WEKA path quota는 **`capacity_bytes`만** 지원하고
  `file_count`(inode quota)는 backend가 거절한다.

**수정(PATCH).** DMS는 **전체 `StorageMappingInput`을 받는다** — 부분(partial) 전송이 아니라
현재 상태 전체를 round-trip 한다.

```bash
curl_dms -X PATCH -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings/cephfs-a" \
  -d '{ "storage_name": "cephfs-a", "backend_template": { … 전체 … },
        "cluster_name": "cluster-a", "storage_class_name": "rook-cephfs" }' \
  | jq '{storage_name, status}'
```

- body의 `storage_name`은 path와 **반드시 일치**해야 한다(불일치 시 400).
- 진행 중인 request/data_job이 있으면 **409**.

**삭제(DELETE).** 하드 삭제다(disable/enable 없음).

```bash
curl_dms -X DELETE "$DMS_API_URL/api/v1/resource-management/storage-mappings/cephfs-a" \
  | jq '{storage_name, deleted}'
```

- 존재하지 않으면 404, 진행 중 작업이 있으면 409.

### 4.3 sanity 재실행 (`:check`)

readiness가 stale로 남았거나 방금 노드/마운트를 바꿨으면 수동 재계산한다.

```bash
curl_dms -X POST \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings/cephfs-a:check" \
  | jq '{storage_name, status}'
```

> **zsh 주의:** 콜론 액션은 `"…/cephfs-a:check"`처럼 **전체를 따옴표**로 감싼다(`$var:check`는
> 수식어로 변형돼 404가 난다).

### 4.4 agent ConfigMap 동기화 · rollout-restart (RM·DM **둘 다**)

POST/PATCH/DELETE 시 `dms-agent-storages` ConfigMap이 **자동 동기화**된다(수동 편집 불필요). 단,
**agent는 startup에 `storages.json`을 한 번만 읽으므로**(loop에서 재읽기 안 함) 매핑을 바꾼 뒤에는
DaemonSet을 **rollout-restart** 해야 새 스토리지가 반영된다.

새 스토리지의 `resource_management` readiness는 **RM agent**가, `data_management`는 **DM agent**가
채운다 — **둘 중 하나만 재시작하면 나머지 축이 `Missing`으로 남는다.** 반드시 RM·DM을 함께 재시작한다.

```bash
kubectl -n dms rollout restart daemonset/dms-rm-agent daemonset/dms-dm-agent
kubectl -n dms rollout status  daemonset/dms-rm-agent daemonset/dms-dm-agent --timeout=180s
```

또는 DMS API로(포탈의 "에이전트 재시작" 버튼이 쓰는 경로):

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/agent/rollout-restart" | jq
curl_dms     "$DMS_API_URL/api/v1/agent/rollout-status"  | jq   # desired/updated/ready·rolling
```

> ConfigMap 동기화 자체는 `control-plane.yaml`의 Role/RoleBinding **`dms-agent-storages-sync`**
> (configmaps get·update·patch, SA `dms-api`+`dms-remote`)에 의존한다. **이 RBAC가 없으면 patch가
> Forbidden인데 코드가 그걸 삼켜** ConfigMap이 조용히 갱신되지 않고, 새 스토리지가 agent에 닿지
> 못해 RM은 `missing_rm_readiness`, DM은 `no_ready_dm_candidate`가 된다(설치 시 함께 적용됨 —
> [`../install/dms-05-dm-jobs.md`](../install/dms-05-dm-jobs.md) §7).

반영 확인:

```bash
POD=$(kubectl -n dms get pods -l app.kubernetes.io/name=dms-dm-agent -o jsonpath='{.items[0].metadata.name}')
kubectl -n dms exec "$POD" -- dms agent-probe --once | jq '.mounts[] | {storage_name,status}'
```

### 4.5 readiness / sanity `Failed` 진단

요청이 `storage_mapping_sanity`로 거절되거나 매핑이 `Ready`가 아니면:

**(a) 파일시스템 백엔드(cephfs/wekafs/gpfs) — agent evidence.** 가장 흔한 원인은 **agent가 노드
마운트를 못 보는 것**(readiness 전부 false / 매트릭스 전부 ✗). agent는 컨테이너에서 돌기 때문에
호스트 마운트 테이블을 봐야 한다 — `agent-daemonset.yaml`에 `/host/proc/1/mountinfo` bind-mount와
`DMS_AGENT_MOUNTINFO_PATH=/host/proc/1/mountinfo`가 있어야 한다.

```bash
POD=$(kubectl -n dms get pods -l app.kubernetes.io/name=dms-dm-agent -o jsonpath='{.items[0].metadata.name}')
kubectl -n dms exec "$POD" -- printenv DMS_AGENT_MOUNTINFO_PATH   # /host/proc/1/mountinfo 여야 함
kubectl -n dms exec "$POD" -- dms agent-probe --once | jq '.mounts[] | {storage_name,status}'
```

전부 `Missing`이면 mountinfo bind-mount 누락이다. 그 밖의 흔한 원인: 마운트를 보는 노드에 agent가
안 떠 있음 · `DMS_AGENT_CLUSTER_NAME` ≠ 매핑의 `cluster_name` · `storage_class_name`/`csi_driver`가
live StorageClass와 불일치 · agent report stale(`DMS_AGENT_REPORT_STALE_SECONDS`).

```bash
curl_dms "$DMS_API_URL/api/v1/operations/agent-reports" | jq '.[0] | {node_name, freshness, updated_at}'
```

**(b) CSI/k8s 네임스페이스-쿼터 매핑 — ResourceQuota mutation transport.** `backend_type`이
`cephfs`/`wekafs`/`gpfs`가 **아닌** 매핑(예: `ceph-csi`)은 agent가 아니라 **RM worker가 실제로 quota를
적용하는 경로**(`kubectl` 또는 `ssh-kubectl`)로 대상 클러스터에 도달해 `resourcequota`를
create/patch/delete 할 수 있는지를 sanity가 직접 검사한다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/storage-mappings/<name>" \
  | jq '{status: .sanity_status,
         kubernetes_mutation: .sanity_result.readiness.kubernetes_mutation,
         mutation_observed: .sanity_result.mutation_observed, errors: .sanity_result.errors}'
```

`Failed`이면 `errors[].code`가 둘 중 하나이며 조치가 다르다.

| error code | 의미 | 조치 |
| --- | --- | --- |
| `mutation_transport_unreachable` | transport가 대상 클러스터에 **도달 못 함** (`control_host` 오설정/SSH 불가/원격에 kubectl 없음/kubeconfig 무효/timeout) | transport 경로 점검: `ssh <control_host> "kubectl auth can-i create resourcequota -A"` 또는 `KUBECONFIG=<kc> kubectl auth can-i create resourcequota -A` |
| `mutation_no_permission` | 도달은 하지만 `can-i`가 **no** (RBAC 부족) | 대상 클러스터에서 resourcequota create/patch/delete RBAC 부여 (`install/kubernetes/target-cluster-rbac.yaml`) |

전역 transport는 `DMS_KUBERNETES_MUTATION_MODE` + `DMS_CLUSTER_CONTROL_HOSTS_JSON`(ssh-kubectl) /
`DMS_CLUSTER_KUBECONFIGS_JSON`(kubectl)에서 오고, 매핑별로 `backend_template.mutation_mode`/`control_host`가
우선한다. env/Secret을 바꾼 뒤에는 `dms-api`와 `dms-sanity-reconciler`를 rollout restart 해야 반영된다.
수정 후 `:check`로 `kubernetes_mutation`이 `Ready`로 돌아오는지 확인한다. 설정 상세는
[`../install/dms-04-rm-k8s-quota.md`](../install/dms-04-rm-k8s-quota.md).

---

## 5. 백그라운드 loop (sanity-reconciler · retention)

운영 백그라운드 loop는 모두 singleton(`replicas: 1`) Deployment이고 heartbeat livenessProbe로
자가 치유한다.

- **`dms-sanity-reconciler`** (`install/kubernetes/sanity-reconciler.yaml`) — storage-mapping readiness
  재계산(`DMS_SANITY_RECONCILE_INTERVAL_SECONDS` 기본 30s). §4.5 진단의 자동화 버전이다.
- **`dms-retention`** (`install/kubernetes/retention.yaml`) — `agent_reports` history를 나이 기준
  prune. 100+ node가 분당 1회 보고하면 이 테이블이 수백만 행으로 자란다. node-health 읽기는 node별
  최신 1행(`agent_node_current`)에서 하므로 history prune은 **안전**하다 — 침묵한 node의 마지막 보고도
  계속 보인다. window는 `DMS_AGENT_REPORT_RETENTION_SECONDS`(기본 30일)이며 **7일 미만으로 내려가지
  않게 floor**된다.

```bash
kubectl -n dms logs deploy/dms-retention --tail=50      # {"processed": N} = 삭제 행 수
kubectl -n dms logs deploy/dms-sanity-reconciler --tail=50
```

두 loop가 멈춰도 읽기 경로(node-health·readiness 조회)는 계속 동작하므로 correctness 의존성은 아니다.

---

## 6. Worker recovery · stale run

먼저 전체 상태를 본다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/work-summary" | jq
curl_dms "$DMS_API_URL/api/v1/operations/runs/active"  | jq
curl_dms "$DMS_API_URL/api/v1/operations/runs/stale"   | jq
```

RM/DM worker는 긴 backend call 중 run heartbeat로 `lease_expires_at`을 갱신한다. 그래도 worker
process가 죽거나 heartbeat가 끊기면 expired lease가 남을 수 있으므로, startup 또는 planned shutdown
전후에 stale guard를 돌린다.

```bash
install/scripts/dms-startup-recovery-check.sh
```

이 스크립트는 `POST /api/v1/operations/runs:mark-stale`을 호출한다.

- `Claimed`에서 만료 → **`StaleClaim`** (안전하게 회수).
- `Running`/`Applying`/`Verifying`에서 만료 → **`RecoveryNeeded`** (side effect 진행 중일 수 있어
  운영자 확인 필요).
- DMS는 이런 run을 **자동 재실행하지 않는다.** `RecoveryNeeded`/`UnknownAfterSideEffect`/
  `BackendApplyFailed`가 있으면 target live state와 최근 logs를 먼저 확인한 뒤 처리한다
  (스크립트도 이때 non-zero로 종료한다).

---

## 7. 계획적 종료 · 재개

worker 재시작·노드 작업·업그레이드 전에는 무작정 scale-down 하지 말고, drain으로 active run을
비운 뒤 워커를 0으로 내린다.

**① 종료(drain + scale down).** `dms-planned-shutdown.sh`는 `control-state:begin-drain`으로 drain에
진입하고 `ready_for_shutdown=true`가 될 때까지 폴링한 뒤, worker Deployment를 0으로 내린다
(기본 `DMS_WORKER_DEPLOYMENTS="dms-rm-worker dms-dm-worker"` — **둘 다** 내린다). Kubernetes node
drain/reboot는 이 스크립트가 하지 않는다.

```bash
install/scripts/dms-planned-shutdown.sh \
  --reason "worker restart $(date -Iseconds)" \
  --timeout-seconds 900 --poll-seconds 10
```

**② 필요한 Kubernetes/host 작업 수행.**

**③ startup recovery check.**

```bash
install/scripts/dms-startup-recovery-check.sh
```

**④ 재개(resume + scale up) — RM·DM 두 워커 모두.** `dms-resume.sh`는 `control-state:resume`으로
정상 상태로 되돌리고 워커를 scale up 한다. ⚠️ **이 스크립트의 `DMS_WORKER_DEPLOYMENTS` 기본값은
`dms-rm-worker`(RM 하나뿐)** 이다 — DM이 활성(`replicas: 1`)인 운영에서는 반드시 **두 워커를 모두**
지정해야 `dms-dm-worker`가 0에서 올라온다. 안 그러면 데이터 잡이 claim되지 않고 큐에 영구 대기한다.

```bash
export DMS_WORKER_DEPLOYMENTS="dms-rm-worker dms-dm-worker"   # ← DM 활성이면 필수
install/scripts/dms-resume.sh \
  --reason "worker restart completed $(date -Iseconds)" \
  --replicas 1
```

- `--force` 없이 resume하면 `RecoveryNeeded`/`UnknownAfterSideEffect`/`BackendApplyFailed`가 남아
  있을 때 API가 **409**를 반환한다(먼저 §6으로 해소).

---

## 8. PostgreSQL backup

업그레이드 전·schema를 바꾸는 코드 적용 전에 **두 database를 모두** 백업한다.

```bash
pg_dump "$DMS_DATABASE_URL"               > dms-operational-$(date +%Y%m%d%H%M%S).sql
pg_dump "$DMS_OBSERVABILITY_DATABASE_URL" > dms-observability-$(date +%Y%m%d%H%M%S).sql
```

운영 rollout 전에 가능하면 **staging DB에 먼저 restore**하고 새 image로 `dms migrate`를 돌려
migration을 검증한다.

---

## 9. 업그레이드

순서: drain → backup → migrate → 4개 Deployment image 교체 → recovery check → resume → verify.

먼저 **`install/kubernetes/control-plane.yaml`의 image 값을 새 ref로 바꾼다** — `dms-migrate` Job의
`image:`와 4개 Deployment(`dms-api`·`dms-planner`·`dms-rm-worker`·`dms-dm-worker`)의 `image:`.
(그래야 다음 `apply`에서 되돌아가지 않는다. 네 Deployment는 모두 **동일한 plain `dms` 이미지**를 쓴다.)

```bash
NEW_DMS_IMAGE="registry.example.internal/dms:vNEXT"

# ① drain + scale down
install/scripts/dms-planned-shutdown.sh --reason "upgrade $NEW_DMS_IMAGE" \
  --timeout-seconds 900 --poll-seconds 10

# ② backup (두 DB)
pg_dump "$DMS_DATABASE_URL"               > dms-operational-$(date +%Y%m%d%H%M%S).sql
pg_dump "$DMS_OBSERVABILITY_DATABASE_URL" > dms-observability-$(date +%Y%m%d%H%M%S).sql

# ③ migrate — Job은 immutable 필드가 많아 삭제 후 재생성이 단순하다
kubectl -n dms delete job dms-migrate --ignore-not-found=true
kubectl apply -f install/kubernetes/control-plane.yaml
kubectl -n dms wait --for=condition=complete job/dms-migrate --timeout=180s

# ④ 4개 Deployment image 교체 (dm-worker 누락 주의 — 빠뜨리면 스테일 이미지로 남는다)
kubectl -n dms set image deploy/dms-api        api="$NEW_DMS_IMAGE"
kubectl -n dms set image deploy/dms-planner    planner="$NEW_DMS_IMAGE"
kubectl -n dms set image deploy/dms-rm-worker  rm-worker="$NEW_DMS_IMAGE"
kubectl -n dms set image deploy/dms-dm-worker  dm-worker="$NEW_DMS_IMAGE"
for d in dms-api dms-planner dms-rm-worker dms-dm-worker; do
  kubectl -n dms rollout status deploy/$d --timeout=180s
done

# ⑤ recovery check → resume(둘 다) → verify
install/scripts/dms-startup-recovery-check.sh
export DMS_WORKER_DEPLOYMENTS="dms-rm-worker dms-dm-worker"
install/scripts/dms-resume.sh --reason "upgrade completed $(date -Iseconds)" --replicas 1
install/scripts/verify-install.sh
```

> DM 잡/agent 이미지(`DMS_DM_JOB_IMAGE`·`dms-agent`)를 함께 올릴 때는 [dms-05](../install/dms-05-dm-jobs.md)의
> 빌드 순서(job 이미지 먼저 → agent 이미지)와 `DMS_DM_JOB_IMAGE` `:CHANGE_ME` 트랩에 유의한다.

---

## 10. Rollback

새 image rollout 후 문제가 있으면 직전 image로 되돌린다(4개 Deployment 모두).

```bash
for d in dms-api dms-planner dms-rm-worker dms-dm-worker; do
  kubectl -n dms rollout undo deploy/$d
done
for d in dms-api dms-planner dms-rm-worker dms-dm-worker; do
  kubectl -n dms rollout status deploy/$d --timeout=180s
done
```

- **schema migration이 이미 실행된 뒤라면 image rollback만으로는 부족할 수 있다.** schema를 바꾸는
  release는 §8의 staging restore로 먼저 검증한다.
- rollback 전에도 가능하면 §7의 drain으로 들어가고, rollback 후 `dms-startup-recovery-check.sh` →
  `dms-resume.sh`(두 워커)를 같은 순서로 실행한다.

---

## 11. 장애 대응 빠른 참조

먼저 §2 `action-required`와 §6 `work-summary`/`runs/stale`로 전체 상태를 본다. 이후 도메인별로:

### 11.1 RM — 파일시스템 · k8s 쿼터

요청 흐름과 live 객체를 확인한다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/requests?requester_id=<requester>&limit=20" | jq
kubectl -n dms logs deploy/dms-planner   --tail=200
kubectl -n dms logs deploy/dms-rm-worker --tail=200
# k8s 쿼터: target 클러스터의 live ResourceQuota
kubectl --context <target-context> -n <namespace> get resourcequota dms-storage-quota -o yaml
```

single check·scope audit·expiration-sweep(dry-run→real) 등 **페이로드 스키마와 절차**는
[`api/resource-management-k8s.md`](api/resource-management-k8s.md) ·
[`api/resource-management-fs.md`](api/resource-management-fs.md)에 있다. sweep은 항상 **`dry_run:true`로
대상을 먼저 검토**한 뒤 실행하며, system/admin resource는 policy가 skip한다.

### 11.2 DM — 데이터 잡

```bash
curl_dms "$DMS_API_URL/api/v1/operations/data-jobs?limit=20" | jq
curl_dms "$DMS_API_URL/api/v1/operations/data-jobs?state=ConfirmPending&limit=20" | jq
# Volcano 런타임
kubectl -n dms get job.batch.volcano.sh
kubectl -n dms get podgroup
kubectl -n dms describe job.batch.volcano.sh <volcano-job-name>
kubectl -n dms logs deploy/dms-dm-worker --tail=200
```

- **preflight 실패** 먼저 확인: 요청자 identity가 해석되는가(§11.3), source/dest 매핑
  `readiness.data_management=Ready`인가, DM agent report가 fresh이고 tool(`dscan`/`dsync`/`drm`/`nsync`)·
  mount·POSIX user evidence를 포함하는가, `preflight_result.effective_resource_model`의 노드/프로세스/
  큐/priority가 의도한 policy와 맞는가.
- **결과 artifact 위치**(DB엔 URI·요약만 저장, 실제 파일은 `DMS_DM_ARTIFACT_BASE_URI` 하위):

  ```text
  scan:     <base>/<job_id>/{summary.json, dscan-report.json, stdout.log, stderr.log}
            <base>/<job_id>/mpi/{submitted.yaml, launch.json, workers.json, scheduler.json, mpirun.json}
  sync·rm:  <base>/<job_id>/preview/{summary.json, command.json, stdout.log, stderr.log}
            <base>/<job_id>/execution/{summary.json, command.json, stdout.log, stderr.log}
            <base>/<job_id>/mpi/{…}
  ```

  artifact parse 실패 시 dm-worker(root)가 artifact base를 traverse/read 할 수 있는지 먼저 본다 —
  사용자 target 디렉토리(`0750`) 하위에 base를 두면 못 읽을 수 있다. 운영은 별도 DMS-managed
  artifact 마운트를 쓴다([dms-05](../install/dms-05-dm-jobs.md) §6).
- **preview→confirm.** `sync`/`rm`은 preview 없이 실행되면 안 된다. preview를 검토한 뒤
  `POST …/data-management/jobs/<job_id>:confirm`에 preview fingerprint를 실어 confirm한다.
  `PreviewExpired`가 되면 같은 job을 재사용하지 말고 **새 request**를 제출한다. 전체 흐름과 페이로드는
  [`api/data-management.md`](api/data-management.md).

### 11.3 DM identity · 긴급 차단 (denylist kill-switch)

DM은 identity를 DB에 저장하지 않는다. dm-worker가 preflight에서 `owner_username`을 키로 **LDAP를
read-only 조회**해 uid/gid/groups를 해석한다(fail-closed — LDAP 다운 시 stale 신원 대신 거부). 영속되는
유일한 identity 상태는 **denylist**(즉시 kill-switch, 비어 있으면 전부 허용)다.

identity 해석 실패 시 data-job의 `preflight_result.reason`:

| reason | 의미 | 조치 |
| --- | --- | --- |
| `ldap_unavailable` | LDAP 연결 불가(fail closed) | LDAP/`DMS_LDAP_*` 복구 후 재시도 (캐시 없음 — 정상화되면 그대로 재시도) |
| `ldap_not_configured` | DM identity LDAP 미설정 | `DMS_LDAP_*` + `DMS_DM_IDENTITY_PROVIDER` 설정 |
| `ldap_identity_not_found` | LDAP에 사용자 없음 | `owner_username`/POSIX username override 확인 |
| `identity_denied` | denylist 차단 | 필요 시 해제(아래) |

LDAP 해석 자체를 직접 확인:

```bash
ldapsearch -x -H "$DMS_LDAP_URI" -b "$DMS_LDAP_BASE_DN" "(uid=<user>)" uidNumber gidNumber
```

**긴급 차단/해제.** `subject_type ∈ {requester, owner, group}`. 차단되면 그 subject의 데이터 잡은
preflight에서 `identity_denied`로 Rejected 된다.

```bash
# 차단 (200 {"status":"Denied"})
curl_dms -X PUT -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/data-management/identity-denylist/requester/<username>" \
  --data '{"reason": "incident-1234 격리"}' | jq
# 해제 (200 {"status":"Allowed"}, 없으면 404)
curl_dms -X DELETE \
  "$DMS_API_URL/api/v1/data-management/identity-denylist/requester/<username>" | jq
# 현재 목록
curl_dms "$DMS_API_URL/api/v1/data-management/identity-denylist" | jq
```

privileged root 경로(`DMS_DM_ALLOW_ROOT_REQUESTER=true`)는 LDAP/uid 하한을 우회하지만 **mTLS-verified
operator만** 쓸 수 있고 `DMS_DM_PRIVILEGED_REQUESTERS`/`_OPERATORS`/`_SCOPES`로 좁혀 검토한다
([dms-05](../install/dms-05-dm-jobs.md) §9).

### 11.4 Expiry sweep

만료 resource를 나열하고, sweep은 **dry-run으로 대상을 검토한 뒤** 실행한다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/filesystems/expiring?status=expired" | jq
curl_dms "$DMS_API_URL/api/v1/operations/kubernetes/namespace-quotas/expiring?status=expired" | jq
```

`:expiration-sweep`(filesystems / kubernetes/namespace-quotas)의 `dry_run`·`action`·`scope`·`max_targets`
페이로드와 절차는 [`api/resource-management-k8s.md`](api/resource-management-k8s.md) ·
[`api/resource-management-fs.md`](api/resource-management-fs.md).

---

## 12. 알려진 운영 공백

- **DM live execution 전제조건**(Volcano + Queue/PriorityClass, DM 잡 이미지, dms-agent 이미지, 공유
  artifact 경로, identity evidence)은 **설치 시** 갖춘다([dms-01](../install/dms-01-prerequisites.md)).
  `dms-dm-worker`는 기본 `replicas: 1`(DM 활성)이며, 전제조건이 아직 없어 DM을 **끄려는 경우에만** 0으로
  둔다. DM은 **volcano-job**만 쓰므로 Kubeflow MPI Operator는 필요 없다. `sync`/`rm`은 preview/confirm
  guard 없이 실행되면 안 된다.
- `DMS_DM_KUBERNETES_MODE=stub`은 로컬 테스트/dev 전용이다(운영에서 쓰지 않는다).
- 운영 Helm/Kustomize packaging은 아직 없다 — `install/kubernetes/`의 명시적 YAML template로 배포한다.
- 서로 다른 mount를 가진 **다중 로컬-filesystem RM worker**는 storage-aware worker claiming 없이는
  안전하지 않다.
- **WekaFS quota는 `capacity_bytes`만** 지원한다(`file_count`/inode quota는 backend가 거절). quota
  작업은 RM ssh 대상 호스트의 `weka user login`이 선행돼야 한다(§4.2).

---

## 다음 문서

- DMS API 개요·인증 — [`docs/api/README.md`](api/README.md)
- 파일시스템 RM API — [`docs/api/resource-management-fs.md`](api/resource-management-fs.md)
- k8s 쿼터 RM API — [`docs/api/resource-management-k8s.md`](api/resource-management-k8s.md)
- DM scan/sync/rm API — [`docs/api/data-management.md`](api/data-management.md)
- operations 조회 API — [`docs/api/operations.md`](api/operations.md)
- 설치(코어·mTLS·ingress·migration) — [`install/dms-02-core.md`](../install/dms-02-core.md)
- 환경변수 레퍼런스 — [`install/dms-06-configuration.md`](../install/dms-06-configuration.md)
- 설치 가이드 인덱스 — [`install/README.md`](../install/README.md)
