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
> 관련 env는 [`../install/dms-05-configuration.md`](../install/dms-05-configuration.md).

### 클러스터 컨텍스트

별도 표기가 없는 `kubectl`은 **컨트롤 클러스터** 컨텍스트에서 네임스페이스 `dms`에 대해 실행한다
(`kubectl -n dms …`). **target(관리 대상) 클러스터**를 대상으로 하는 명령은 `--context <target-context>`
또는 `KUBECONFIG=<kubeconfig>`를 명시한다.

### 정상 steady state (요약)

- `dms-api`(replicas 2)·`dms-planner`·`dms-dm-worker`가 모두 실행 중.
- **`dms-dm-worker`는 매니페스트 기본 `replicas: 32`(= DM 활성)**. DM을 의도적으로 끈 환경에서만 0이며,
  0을 "정상 유휴"로 오해하지 말 것. DM 잡은 **Volcano 네이티브 Job**(`DMS_DM_SCHEDULER_BACKEND=volcano-job`)으로
  실행되고 **Kubeflow MPI Operator는 필요 없다**.
- 스토리지 매핑의 축(`data_management`·`inventory`)마다 agent report가 fresh이고 `readiness.*=Ready`.
- 해결되지 않은 `action-required` 항목이 없다.

---

## 1. 설치 직후 첫 점검

컨트롤플레인 workload와 migration을 확인한다.

```bash
kubectl -n dms get pods,jobs,svc,ingress
kubectl -n dms wait --for=condition=complete job/dms-migrate --timeout=180s
for d in dms-api dms-planner dms-dm-worker; do
  kubectl -n dms rollout status deploy/$d --timeout=180s
done
```

문제가 있으면 바로 describe/log를 본다.

```bash
kubectl -n dms describe pod -l app.kubernetes.io/name=dms-api
kubectl -n dms logs deploy/dms-api --tail=200
kubectl -n dms logs job/dms-migrate --tail=200
```

등록된 대상 클러스터가 있으면 인벤토리 읽기용 RBAC/kubeconfig를 확인한다(설정은
[`../install/dms-03-storage-mappings.md`](../install/dms-03-storage-mappings.md) §3).

```bash
KUBECONFIG=<cluster-a.kubeconfig> kubectl get nodes
KUBECONFIG=<cluster-a.kubeconfig> kubectl get storageclass
KUBECONFIG=<cluster-a.kubeconfig> kubectl get csidrivers
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
[`../install/dms-04-dm-jobs.md`](../install/dms-04-dm-jobs.md).

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
> 인증 env 레퍼런스는 [`../install/dms-05-configuration.md`](../install/dms-05-configuration.md).

---

## 4. Storage mapping 운영

스토리지 매핑은 백엔드(경로·클러스터·마운트)와 그 **readiness/sanity**를 담는 레코드다. DM
요청은 대상 매핑의 `data_management`가 `Ready`여야 진행된다. 요청 페이로드 전체 스키마는
[`api/storage-mappings.md`](api/storage-mappings.md)에 있고, 여기서는 **운영 절차**만 다룬다.

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

**등록(POST).** 파일시스템 매핑은 `mount_path` + `managed_root`가 필수다(GPFS는 `filesystem_name`도).

```bash
curl_dms -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/storage-mappings" \
  -d '{
    "storage_name": "cephfs-a",
    "backend_template": {
      "backend_type": "cephfs",
      "cluster_name": "cluster-a",
      "mount_path": "/cephfs",
      "managed_root": "/cephfs/root",
      "csi_driver": "rook-ceph.cephfs.csi.ceph.com"
    },
    "cluster_name": "cluster-a",
    "storage_class_name": "rook-cephfs"
  }' | jq '{storage_name, status}'
```

- 이미 존재하면 **upsert(덮어쓰기)** 로 동작한다. 백엔드별 필드(GPFS `filesystem_name`, WekaFS,
  CSI 타입)는 [`../install/dms-03-storage-mappings.md`](../install/dms-03-storage-mappings.md) ·
  [`api/storage-mappings.md`](api/storage-mappings.md).
- CSI 매핑(`ceph-csi`/`gpfs-csi`/`weka-csi`)은 호스트 마운트가 없어 `cluster_name` +
  `storage_class_name` + `csi_driver`만으로 등록되며, `data_management` 축은 `Missing`이 정상이다
  (PVC↔PVC sync 대상).

**수정(PATCH).** DMS는 **전체 `StorageMappingInput`을 받는다** — 부분(partial) 전송이 아니라
현재 상태 전체를 round-trip 한다.

```bash
curl_dms -X PATCH -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/storage-mappings/cephfs-a" \
  -d '{ "storage_name": "cephfs-a", "backend_template": { … 전체 … },
        "cluster_name": "cluster-a", "storage_class_name": "rook-cephfs" }' \
  | jq '{storage_name, status}'
```

- body의 `storage_name`은 path와 **반드시 일치**해야 한다(불일치 시 400).
- 진행 중인 request/data_job이 있으면 **409**.

**삭제(DELETE).** 하드 삭제다(disable/enable 없음).

```bash
curl_dms -X DELETE "$DMS_API_URL/api/v1/storage-mappings/cephfs-a" \
  | jq '{storage_name, deleted}'
```

- 존재하지 않으면 404, 진행 중 작업이 있으면 409.

### 4.3 sanity 재실행 (`:check`)

readiness가 stale로 남았거나 방금 노드/마운트를 바꿨으면 수동 재계산한다.

```bash
curl_dms -X POST \
  "$DMS_API_URL/api/v1/storage-mappings/cephfs-a:check" \
  | jq '{storage_name, status}'
```

> **zsh 주의:** 콜론 액션은 `"…/cephfs-a:check"`처럼 **전체를 따옴표**로 감싼다(`$var:check`는
> 수식어로 변형돼 404가 난다).

### 4.4 agent ConfigMap 동기화 · rollout-restart

POST/PATCH/DELETE 시 `dms-agent-storages` ConfigMap이 **자동 동기화**된다(수동 편집 불필요). 단,
**agent는 startup에 `storages.json`을 한 번만 읽으므로**(loop에서 재읽기 안 함) 매핑을 바꾼 뒤에는
DaemonSet을 **rollout-restart** 해야 새 스토리지가 반영된다.

새 스토리지의 `data_management` readiness는 **DM agent**(`dms-dm-agent`)가 채운다 — 재시작하지 않으면
그 축이 `Missing`으로 남는다.

```bash
kubectl -n dms rollout restart daemonset/dms-dm-agent
kubectl -n dms rollout status  daemonset/dms-dm-agent --timeout=180s
```

또는 DMS API로(포탈의 "에이전트 재시작" 버튼이 쓰는 경로):

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/agent/rollout-restart" | jq
curl_dms     "$DMS_API_URL/api/v1/agent/rollout-status"  | jq   # desired/updated/ready·rolling
```

> ConfigMap 동기화 자체는 `control-plane.yaml`의 Role/RoleBinding **`dms-agent-storages-sync`**
> (configmaps get·update·patch, SA `dms-api`+`dms-remote`)에 의존한다. **이 RBAC가 없으면 patch가
> Forbidden인데 코드가 그걸 삼켜** ConfigMap이 조용히 갱신되지 않고, 새 스토리지가 agent에 닿지
> 못해 DM이 `no_ready_dm_candidate`가 된다(설치 시 함께 적용됨 —
> [`../install/dms-04-dm-jobs.md`](../install/dms-04-dm-jobs.md) §7).

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

**(b) `inventory` 축 — live StorageClass / CSI driver 대조.** 매핑의 `storage_class_name`이 대상
클러스터에 실제로 존재하고 `csi_driver`가 live provisioner와 일치해야 한다. CSI 매핑
(`ceph-csi`/`gpfs-csi`/`weka-csi`)은 agent 증거가 없으므로 사실상 이 축만 본다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/storage-mappings/<name>" \
  | jq '{status: .sanity_status,
         inventory: .sanity_result.readiness.inventory, errors: .sanity_result.errors}'
```

| error code | 의미 | 조치 |
| --- | --- | --- |
| `cluster_missing` | 매핑의 `cluster_name`이 인벤토리에 없음 | `DMS_CLUSTER_KUBECONFIGS_JSON` 키와 Secret `dms-cluster-kubeconfigs`를 확인(§1) |
| `storage_class_missing` | 대상 클러스터에 그 StorageClass가 없음 | `KUBECONFIG=<kc> kubectl get storageclass`로 실제 이름 확인 후 매핑 수정 |
| `csi_driver_mismatch` | `csi_driver` ≠ live provisioner | `kubectl get sc <name> -o jsonpath='{.provisioner}'` 값으로 매핑 수정 |

인벤토리 읽기 경로는 `DMS_KUBERNETES_INVENTORY_MODE` + `DMS_CLUSTER_KUBECONFIGS_JSON`(kubectl) /
`DMS_CLUSTER_CONTROL_HOSTS_JSON`(ssh-kubectl)에서 온다. env/Secret을 바꾼 뒤에는 `dms-api`와
`dms-sanity-reconciler`를 rollout restart 해야 반영된다. 수정 후 `:check`로 `inventory`가 `Ready`로
돌아오는지 확인한다. 설정 상세는
[`../install/dms-03-storage-mappings.md`](../install/dms-03-storage-mappings.md) §3.

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

DM worker는 긴 backend call 중 run heartbeat로 `lease_expires_at`을 갱신한다. 그래도 worker
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
(기본 `DMS_WORKER_DEPLOYMENTS="dms-dm-worker"`). Kubernetes node
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

**④ 재개(resume + scale up).** `dms-resume.sh`는 `control-state:resume`으로 정상 상태로 되돌리고
워커를 scale up 한다. `DMS_WORKER_DEPLOYMENTS` 기본값은 `dms-dm-worker`이며, `--replicas`는
**종료 전 replica 수와 맞춘다**(매니페스트 기본은 32). 잘못 낮추면 DM 동시성이 조용히 줄어든다.

```bash
install/scripts/dms-resume.sh \
  --reason "worker restart completed $(date -Iseconds)" \
  --replicas 32        # ← 종료 전 dms-dm-worker replica 수
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

순서: drain → backup → migrate → Deployment image 교체 → recovery check → resume → verify.

먼저 **매니페스트의 image 값을 새 ref로 바꾼다** — `control-plane.yaml`의 `dms-migrate` Job과
Deployment(`dms-api`·`dms-planner`·`dms-dm-worker`), 그리고 별도 매니페스트인
`dms-api-internal.yaml`·`retention.yaml`·`sanity-reconciler.yaml`. 그래야 다음 `apply`에서
되돌아가지 않는다.

> **plain `dms` 이미지를 쓰는 Deployment는 6개다** — `dms-api`, `dms-api-internal`, `dms-planner`,
> `dms-dm-worker`, `dms-retention`, `dms-sanity-reconciler`. 하나라도 빠뜨리면 스테일 이미지로
> 남는다. 특히 `dms-api-internal`은 **노드 agent와 포탈 BFF가 실제로 호출하는 평면**이라 누락 시
> 증상이 늦게 드러난다. 배포 전 실제 대상을 확인한다:
> `kubectl -n dms get deploy -o wide | grep -E "/dms:"`

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

# ④ Deployment image 교체 — plain dms 이미지를 쓰는 6개 전부
kubectl -n dms set image deploy/dms-api               api="$NEW_DMS_IMAGE"
kubectl -n dms set image deploy/dms-api-internal      api="$NEW_DMS_IMAGE"
kubectl -n dms set image deploy/dms-planner           planner="$NEW_DMS_IMAGE"
kubectl -n dms set image deploy/dms-dm-worker         dm-worker="$NEW_DMS_IMAGE"
kubectl -n dms set image deploy/dms-retention         retention="$NEW_DMS_IMAGE"
kubectl -n dms set image deploy/dms-sanity-reconciler sanity-reconciler="$NEW_DMS_IMAGE"
for d in dms-api dms-api-internal dms-planner dms-dm-worker dms-retention dms-sanity-reconciler; do
  kubectl -n dms rollout status deploy/$d --timeout=180s
done

# ④-1 실제로 모두 교체됐는지 확인 — `rollout status`는 옛 파드가 가용성을 채우고 있으면
#      성공이라고 보고할 수 있다. 파드 이미지와 남은 ReplicaSet을 직접 본다.
kubectl -n dms get pods -o json | python3 -c "
import json,sys
bad=[(p['metadata']['name'], c['image'])
     for p in json.load(sys.stdin)['items'] if p['status'].get('phase')=='Running'
     for c in p['spec']['containers'] + p['spec'].get('initContainers',[])
     if '/dms:' in c['image'] and '$NEW_DMS_IMAGE'.split(':')[-1] not in c['image']]
print('구버전 이미지 파드:', bad or '없음')"
kubectl -n dms get rs -o custom-columns='NAME:.metadata.name,DESIRED:.spec.replicas,IMAGE:.spec.template.spec.containers[0].image' | awk '$2>0' 

# ⑤ recovery check → resume → verify
install/scripts/dms-startup-recovery-check.sh
install/scripts/dms-resume.sh --reason "upgrade completed $(date -Iseconds)" --replicas 32
install/scripts/verify-install.sh
```

> DM 잡/agent 이미지(`DMS_DM_JOB_IMAGE`·`dms-agent`)를 함께 올릴 때는 [dms-04](../install/dms-04-dm-jobs.md)의
> 빌드 순서(job 이미지 먼저 → agent 이미지)와 `DMS_DM_JOB_IMAGE` `:CHANGE_ME` 트랩에 유의한다.

---

## 10. Rollback

새 image rollout 후 문제가 있으면 직전 image로 되돌린다(교체한 Deployment 모두).

```bash
# §9 ④에서 교체한 것과 반드시 같은 목록이어야 한다 — 일부만 되돌리면 혼합 버전 상태가 된다.
ROLL="dms-api dms-api-internal dms-planner dms-dm-worker dms-retention dms-sanity-reconciler"
for d in $ROLL; do kubectl -n dms rollout undo deploy/$d; done
for d in $ROLL; do kubectl -n dms rollout status deploy/$d --timeout=180s; done
```

- **schema migration이 이미 실행된 뒤라면 image rollback만으로는 부족할 수 있다.** schema를 바꾸는
  release는 §8의 staging restore로 먼저 검증한다.
- rollback 전에도 가능하면 §7의 drain으로 들어가고, rollback 후 `dms-startup-recovery-check.sh` →
  `dms-resume.sh`를 같은 순서로 실행한다.

---

## 11. 장애 대응 빠른 참조

먼저 §2 `action-required`와 §6 `work-summary`/`runs/stale`로 전체 상태를 본다. 이후 도메인별로:

### 11.1 요청 흐름 (planner)

요청이 plan으로 넘어가는 단계에서 막혔는지 먼저 본다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/requests?requester_id=<requester>&limit=20" | jq
curl_dms "$DMS_API_URL/api/v1/operations/plans/active" | jq
kubectl -n dms logs deploy/dms-planner --tail=200
```

`Rejected`면 `issues[]`에 사유가 담긴다(가장 흔한 것은 `no_ready_dm_candidate` — §4.5로).
`UnknownAfterSideEffect`/`BackendApplyFailed`/`Conflict`로 굳은 요청은
[`api/operations.md` §stuck request 해소](api/operations.md)의 `:resolve`로 종결한다.

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
  artifact 마운트를 쓴다([dms-04](../install/dms-04-dm-jobs.md) §6).
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

여러 건(예: 퇴사자 계정)을 한꺼번에 넣을 때는 JSON 파일로 일괄 적용한다 — 형식은
[`install/config/identity-denylist.example.json`](../install/config/identity-denylist.example.json):

```bash
install/scripts/apply-identity-denylist.sh <identity-denylist.json>
```

privileged root 경로(`DMS_DM_ALLOW_ROOT_REQUESTER=true`)는 LDAP/uid 하한을 우회하지만 **mTLS-verified
operator만** 쓸 수 있고 `DMS_DM_PRIVILEGED_REQUESTERS`/`_OPERATORS`/`_SCOPES`로 좁혀 검토한다
([dms-04](../install/dms-04-dm-jobs.md) §9).

---

## 12. 알려진 운영 공백

- **DM live execution 전제조건**(Volcano + Queue/PriorityClass, DM 잡 이미지, dms-agent 이미지, 공유
  artifact 경로, identity evidence)은 **설치 시** 갖춘다([dms-01](../install/dms-01-prerequisites.md)).
  `dms-dm-worker`는 매니페스트 기본 `replicas: 32`(DM 활성)이며, 전제조건이 아직 없어 DM을 **끄려는 경우에만** 0으로
  둔다. DM은 **volcano-job**만 쓰므로 Kubeflow MPI Operator는 필요 없다. `sync`/`rm`은 preview/confirm
  guard 없이 실행되면 안 된다.
- `DMS_DM_KUBERNETES_MODE=stub`은 로컬 테스트/dev 전용이다(운영에서 쓰지 않는다).
- 운영 Helm/Kustomize packaging은 아직 없다 — `install/kubernetes/`의 명시적 YAML template로 배포한다.
- **agent report retention은 이력(`agent_reports`)만 prune한다.** 노드별 최신 1행 테이블
  (`agent_node_current`)은 자동으로 지워지지 않으므로, 노드를 영구히 제거했으면 그 행을 수동으로
  삭제해야 대시보드에서 `Stale`로 남지 않는다(SQL 예시는
  [`../install/migration-rm-removal.md`](../install/migration-rm-removal.md) §2).

---

## 다음 문서

- DMS API 개요·인증 — [`docs/api/README.md`](api/README.md)
- 스토리지 매핑 API — [`docs/api/storage-mappings.md`](api/storage-mappings.md)
- DM scan/sync/rm API — [`docs/api/data-management.md`](api/data-management.md)
- operations 조회 API — [`docs/api/operations.md`](api/operations.md)
- 설치(코어·mTLS·ingress·migration) — [`install/dms-02-core.md`](../install/dms-02-core.md)
- 환경변수 레퍼런스 — [`install/dms-05-configuration.md`](../install/dms-05-configuration.md)
- 설치 가이드 인덱스 — [`install/README.md`](../install/README.md)
