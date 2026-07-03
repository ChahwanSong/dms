# DMS 설치 — Kubernetes 네임스페이스 쿼터 RM 설정

이 문서는 **Kubernetes namespace quota Resource Management를 켜기 위한 설치/설정만** 다룬다. 실제
API(생성/변경/차단/check/sync/import/audit/만료 sweep) 사용법은
[`docs/api/resource-management-k8s.md`](../docs/api/resource-management-k8s.md)를 참조한다. 이
문서는 코어 control plane이 이미 배포된 상태([`dms-02-core.md`](dms-02-core.md))를 전제한다.

핵심은 두 가지다 — **(A) 대상 클러스터 등록**(RBAC + kubeconfig), 그리고 StorageClass별 쿼터를
쓸 때만 필요한 **(B) CSI storage mapping 등록**. 선택적으로 기본 쿼터 정책을 등록한다.

---

## 1. 동작 모델 — agentless

하나의 namespace quota는 대상 클러스터에 **단일 `ResourceQuota/dms-storage-quota`** 오브젝트로
렌더링된다(이름 고정, 라벨 `dms.io/resource-kind: kubernetes-namespace-quota`).

- **agentless.** DMS control plane(`dms-api` / `dms-planner` / `dms-rm-worker`)이 대상 클러스터
  API server에 kubectl로 `ResourceQuota`를 **직접 적용**한다. 파일시스템 RM(스토리지 노드에 RM
  agent 필요)과 달리 **대상 클러스터에 DMS RM/DM agent가 없어도 된다.**
- **클러스터 라우팅.** payload/mapping의 `cluster_name`으로 kubeconfig를 고른다. 그 `cluster_name`은
  `DMS_CLUSTER_KUBECONFIGS_JSON`에 등록된 키여야 한다(§3).
- **sanity 판정.** k8s/CSI mapping의 sanity는 agent evidence가 아니라
  **(a) StorageClass 존재 + `csi_driver` 일치**와 **(b) ResourceQuota mutation transport 도달성 +
  `kubectl auth can-i create·patch·delete resourcequota` 권한**으로 판정한다. transport가 건강하면
  agent가 하나도 없어도 `Ready`이고, `Failed`만 namespace quota를 막는다(§7).
- **비파괴.** DMS는 자기 `dms-storage-quota`만 관리한다 — namespace 안의 non-DMS ResourceQuota나
  namespace 자체는 만들지도 지우지도 않는다.

> **인증(curl).** 운영 프로필은 mTLS-verified header다 — 클라이언트 인증서로 인증하고 actor는
> 인증서 **subject**에서 파생된다(평문 `x-dms-actor` 미신뢰). 아래 예시는 다음을 전제한다:
>
> ```bash
> DMS_API_URL='https://dms.cluster-a.local:30535'
> DMS_CURL_OPTS=(--cert   /opt/dms-secrets/certs/operator.crt
>                --key    /opt/dms-secrets/certs/operator.key
>                --cacert /opt/dms-secrets/certs/dms-server-ca.crt)
> # (선택) 공유 토큰을 함께 layer하면:
> #   DMS_CURL_OPTS+=(-H "authorization: Bearer <DMS_AUTH_SHARED_TOKEN>")
> ```
>
> 전체 인증 규칙은 [`docs/api/README.md`](../docs/api/README.md) 참조.

---

## 2. 사전 조건

- 코어 control plane 배포 완료([`dms-02-core.md`](dms-02-core.md)) — `dms-api`/`dms-planner`/
  `dms-rm-worker` Running, migration 완료.
- 대상 클러스터 API server가 control plane pod에서 **네트워크로 도달 가능**할 것(`kubectl` 모드),
  또는 SSH bastion 경유로 도달 가능할 것(`ssh-kubectl` 모드, §4).
- 쿼터를 적용할 namespace는 **미리 존재**해야 한다(DMS는 ResourceQuota만 만든다).

아래 예시는 control plane이 **`cluster-a`**(control cluster)에 있다고 가정한다. 가장 단순한 운영
형태는 control cluster 자신이 대상인 **단일 클러스터**(control == target)이며, 추가 대상
클러스터(`cluster-b` 등)는 §3의 절차를 반복해 등록한다.

---

## 3. 대상 클러스터 등록 (RBAC + kubeconfig)

### 3.1 대상 클러스터에 DMS RBAC 적용

파일: [`install/kubernetes/target-cluster-rbac.yaml`](kubernetes/target-cluster-rbac.yaml) — 대상
클러스터에 다음을 만든다:

- `Namespace/dms`
- `ServiceAccount/dms-remote` (namespace `dms`)
- `ClusterRole` + `ClusterRoleBinding` `dms-remote-resource-management` —
  `namespaces` get/list/create/patch/update, **`resourcequotas` get/list/create/patch/update/delete**,
  `storageclasses`/`csidrivers` get/list, `nodes` get/list.

**kubectl이 대상 클러스터를 가리키는 상태에서** 적용한다(단일 클러스터면 control cluster 자신):

```bash
kubectl --context cluster-a apply -f install/kubernetes/target-cluster-rbac.yaml
```

> **편집.** 보통 그대로 적용한다. namespace/SA 이름(`dms` / `dms-remote`)은 §3.2 스크립트 기본값과
> 짝을 이룬다 — 바꾸려면 `DMS_REMOTE_NAMESPACE` / `DMS_REMOTE_SERVICE_ACCOUNT`도 함께 바꾼다.
> `resourcequotas`의 세 mutation verb(create/patch/delete)는 transport sanity의 `can-i` 검사 대상
> 이므로 **하나라도 빠지면** 그 클러스터의 CSI mapping이 `mutation_no_permission`으로 `Failed`가 된다.

### 3.2 dms-remote SA용 kubeconfig 생성

스크립트: [`install/scripts/create-serviceaccount-kubeconfig.sh`](scripts/create-serviceaccount-kubeconfig.sh)
`<cluster-name> <output>` — 대상 API server 주소·CA·SA 토큰을 임베드한 kubeconfig를 만든다. **kubectl이
대상 클러스터를 가리키는 상태에서** 실행한다:

```bash
KUBECONFIG_OUT=/opt/dms-secrets/cluster-a.kubeconfig
kubectl config use-context cluster-a          # kubectl이 대상을 가리키게
install/scripts/create-serviceaccount-kubeconfig.sh cluster-a "$KUBECONFIG_OUT"

# 동작 확인: 이 kubeconfig로 StorageClass가 보이고 resourcequota 생성 권한이 있어야 한다
KUBECONFIG="$KUBECONFIG_OUT" kubectl get storageclass
KUBECONFIG="$KUBECONFIG_OUT" kubectl auth can-i create resourcequota -n default   # → yes
```

> - SA 토큰 유효기간은 `DMS_TOKEN_DURATION`(기본 `8760h` = 1년)으로 정한다. **만료 전 재발급**이
>   필요하다.
> - 스크립트는 kubectl v1.35+에서 제거된 `--certificate-authority-data`를 쓰지 않고
>   `--certificate-authority` + `--embed-certs=true`로 CA를 임베드한다(이미 반영됨).

### 3.3 DMS에 kubeconfig 등록 (Secret + ConfigMap 편집)

control plane(namespace `dms`)의 **두 곳**을 편집한다. 둘 다
[`install/kubernetes/control-plane.yaml`](kubernetes/control-plane.yaml) 안에 있다:

| 파일 → 오브젝트 | 키/필드 | 설정할 값 |
|---|---|---|
| `control-plane.yaml` → Secret **`dms-cluster-kubeconfigs`** | `stringData.cluster-a.kubeconfig` | §3.2에서 생성한 kubeconfig **내용**으로 교체(템플릿은 빈 placeholder). pod에는 `/etc/dms/kubeconfigs/cluster-a.kubeconfig`로 마운트된다 |
| `control-plane.yaml` → ConfigMap **`dms-runtime-config`** | `DMS_CONTROL_CLUSTER_NAME` | control cluster 이름(예 `cluster-a`) |
| `control-plane.yaml` → ConfigMap **`dms-runtime-config`** | `DMS_CLUSTER_KUBECONFIGS_JSON` | `cluster_name → 컨테이너 내부 경로` 맵. 단일 클러스터면 `{"cluster-a":"/etc/dms/kubeconfigs/cluster-a.kubeconfig"}` |

신규 설치라면 apply 전에 매니페스트에서 위 값을 채운 뒤 [`dms-02-core.md`](dms-02-core.md)의
apply/migration 절차를 따른다.

> **클러스터 이름을 `cluster-a`가 아닌 것으로 바꾸는 경우**, 세 곳을 **일관되게** 바꾼다 —
> `DMS_CONTROL_CLUSTER_NAME`, `DMS_CLUSTER_KUBECONFIGS_JSON`의 키(및 경로), Secret의 키 이름
> (`<name>.kubeconfig`). 이 키는 namespace quota payload / storage mapping의 `cluster_name`과 정확히
> 일치해야 한다.

**추가 대상 클러스터(`cluster-b`)를 이미 떠 있는 배포에 붙일 때** — patch 후 재시작:

```bash
# 1) 생성한 kubeconfig을 Secret에 base64로 추가
kubectl -n dms patch secret dms-cluster-kubeconfigs --type merge \
  -p "{\"data\":{\"cluster-b.kubeconfig\":\"$(base64 -w0 /opt/dms-secrets/cluster-b.kubeconfig)\"}}"

# 2) ConfigMap의 DMS_CLUSTER_KUBECONFIGS_JSON에 항목 추가 (기존 키 유지)
kubectl -n dms patch configmap dms-runtime-config --type merge -p \
  '{"data":{"DMS_CLUSTER_KUBECONFIGS_JSON":"{\"cluster-a\":\"/etc/dms/kubeconfigs/cluster-a.kubeconfig\",\"cluster-b\":\"/etc/dms/kubeconfigs/cluster-b.kubeconfig\"}"}}'

# 3) ConfigMap/Secret 반영을 위해 재시작
kubectl -n dms rollout restart deploy/dms-api deploy/dms-planner deploy/dms-rm-worker
```

> **sanity-reconciler도 같은 secret이 필요하다.** 주기 sweep이 managed 클러스터의 CSI mapping을
> 평가하려면 [`install/kubernetes/sanity-reconciler.yaml`](kubernetes/sanity-reconciler.yaml)도
> `dms-cluster-kubeconfigs`를 `/etc/dms/kubeconfigs`로 마운트해야 한다(매니페스트에 이미 포함).

### 3.4 등록 확인

inventory에 대상 클러스터의 StorageClass / CSI driver가 보이면 성공이다:

```bash
curl -sS "${DMS_CURL_OPTS[@]}" "$DMS_API_URL/api/v1/operations/inventory" \
  | jq '.clusters | to_entries[] | {cluster:.key,
        storage_classes:(.value.storage_classes|map(.name)),
        csi:(.value.csi_drivers|map(.name))}'
```

---

## 4. Transport mode (kubectl vs ssh-kubectl)

RM worker가 ResourceQuota를 실제로 적용하는 경로다. 전역 기본은 ConfigMap `dms-runtime-config`에서,
클러스터별 override는 storage mapping에서 지정한다.

- **`kubectl`(기본·권장).** control plane pod가 대상 API server에 **직접 도달**. 전역 설정:
  `control-plane.yaml` → ConfigMap `dms-runtime-config` →
  `DMS_KUBERNETES_MUTATION_MODE: "kubectl"`(+ 인벤토리 읽기용 `DMS_KUBERNETES_INVENTORY_MODE: "kubectl"`).
  §3의 kubeconfig를 사용한다.
- **`ssh-kubectl`.** RM worker가 **직접 도달 못 하는** managed 클러스터용. RM worker가
  `ssh <bastion> kubectl ...`로 적용한다. `bastion`(= `control_host`)은 대상 클러스터 kubectl admin을
  가진 호스트이고, **RM worker의 SSH 키가 그 호스트 `root`에 등록**돼 있어야 한다. 이 경우 RM worker에
  그 클러스터 kubeconfig가 없어도 된다.

**클러스터별 override**는 storage mapping의 `backend_template`에 넣는다(§5) — 전역 모드와 무관하게
mapping마다 적용 경로를 정할 수 있다:

| 필드 | 값 | 규칙 |
|---|---|---|
| `backend_template.mutation_mode` | `kubectl` \| `ssh-kubectl` | 생략 시 전역 `DMS_KUBERNETES_MUTATION_MODE` |
| `backend_template.control_host` | bastion hostname/IPv4 | `mutation_mode:"ssh-kubectl"`일 때 **필수**. 없으면 등록 `422` |

> `mutation_mode:"ssh-kubectl"`인데 `control_host`가 없으면 `422`. 반대로 `control_host`만 있고
> `mutation_mode`가 없어도 `422`(기본 `kubectl`에선 무시되므로 `ssh-kubectl` 명시 필요).

---

## 5. CSI storage mapping 등록 (StorageClass별 쿼터용)

namespace-wide 쿼터(`requests.storage` / `persistentvolumeclaims`)만 쓸 거면 이 단계는 **필요 없다** —
§3의 클러스터 등록만으로 충분하다. **StorageClass별 쿼터**(`storage_class_quotas[]`)를 쓰려면, 각
`storage_class_quotas[].storage_name`이 가리킬 **CSI storage mapping**을 등록해야 한다. 파일시스템
mapping과 공유하는 레코드지만, k8s 쿼터에는 `cluster_name` + `storage_class_name` + `csi_driver`만
있으면 된다. storage mapping CRUD 전체는 [`dms-03-rm-filesystem.md`](dms-03-rm-filesystem.md)를 참조한다.

```bash
curl -sS "${DMS_CURL_OPTS[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings" \
  -d '{
    "storage_name": "longhorn-a",
    "backend_template": {
      "backend_type": "longhorn",
      "csi_driver":   "driver.longhorn.io"
    },
    "cluster_name":       "cluster-a",
    "storage_class_name": "longhorn"
  }' | jq '{storage_name, status, k8s_mutation: .mapping.readiness.kubernetes_mutation}'
# → status "Ready", k8s_mutation "Ready"  (transport 도달 + can-i 통과; agent 불필요)
```

| 필드 | 설명 |
|---|---|
| `storage_name` | namespace quota가 참조할 mapping 이름 |
| `cluster_name` | 대상 클러스터(= `DMS_CLUSTER_KUBECONFIGS_JSON` 키) |
| `storage_class_name` | live StorageClass 이름(hard key prefix로 사용) |
| `backend_template.csi_driver` | live StorageClass provisioner와 **일치**해야 함(불일치 시 sanity `Failed`) |
| `backend_template.mutation_mode` / `control_host` | (선택) 클러스터별 transport 경로 — §4 |

> `backend_type`이 파일시스템 타입(`cephfs`/`wekafs`/`gpfs`)이 아니면 순수 k8s/CSI mapping으로 취급되어
> agent readiness 대신 **mutation transport 축**으로만 sanity를 판정한다. namespace 단위 k8s 오브젝트
> 이므로 **LDAP/owner/group 개념은 없다**(파일시스템 RM과 다른 점).

---

## 6. 기본 쿼터 정책 등록 (선택)

default quota policy를 등록해두면 create/PATCH에서 `reset_quota_to_default: true`로 정책 값을 다시
적용할 수 있다(`resource_kind` + `resource_type` 조합당 1개).

설정 파일:
[`install/config/default-quota-policies.example.json`](config/default-quota-policies.example.json) —
`filesystem:user`와 `kubernetes_namespace_quota:user`가 이미 들어 있다. k8s 정책 형태:

```json
{
  "resource_kind": "kubernetes_namespace_quota",
  "resource_type": "user",
  "quota": {
    "requests_storage_bytes": 1000000000000,
    "pvc_count": 20,
    "storage_class_quotas": [
      { "storage_name": "longhorn-a", "requests_storage_bytes": 1000000000000, "pvc_count": 20 }
    ]
  }
}
```

> 정책의 `storage_class_quotas[].storage_name`도 **등록된 mapping**(§5)이어야 하고, 그 mapping의
> `cluster_name`이 reset 대상 quota의 `cluster_name`과 일치해야 한다.

등록: [`install/scripts/register-default-quota-policies.sh`](scripts/register-default-quota-policies.sh)
`<json>`. **운영(mTLS) 프로필** 환경변수:

```bash
export DMS_API_URL='https://dms.cluster-a.local:30535'
export DMS_CLIENT_CERT=/opt/dms-secrets/certs/operator.crt
export DMS_CLIENT_KEY=/opt/dms-secrets/certs/operator.key
export DMS_CA_CERT=/opt/dms-secrets/certs/dms-server-ca.crt
export DMS_TOKEN='<DMS_AUTH_SHARED_TOKEN>'   # 공유 토큰을 layer한 경우만 (선택)
# DMS_ACTOR는 설정하지 않는다 — mTLS 프로필에선 actor를 인증서 subject에서 파생한다.

install/scripts/register-default-quota-policies.sh \
  install/config/default-quota-policies.example.json
# → 각 정책이 "status":"stored"
```

> **부연(testbed/dev).** mTLS를 쓰지 않는 프로필(`DMS_REQUIRE_MTLS_VERIFIED_HEADER=false`)에서는
> 인증서 없이 `DMS_ACTOR=operator` + (`DMS_AUTH_SHARED_TOKEN` 설정 시) `DMS_TOKEN`만 설정한다.

---

## 7. 검증 / sanity 판정

정상 상태:

- inventory에 대상 클러스터의 StorageClass / CSI가 보인다(§3.4).
- (SC별 쿼터를 쓰면) CSI mapping이 `status: Ready`, `readiness.kubernetes_mutation: Ready`
  (`sanity_result.mutation_observed.can_mutate: true`, create/patch/delete 모두 허용).

`Failed`(namespace quota 차단) 사유와 조치:

| error / 사유 | 조치 |
|---|---|
| `cluster_missing` | §3 클러스터 등록 확인 — `DMS_CLUSTER_KUBECONFIGS_JSON` 키와 Secret kubeconfig |
| `storage_class_missing` | 대상 클러스터에 해당 StorageClass 존재/이름 확인 |
| `csi_driver` 불일치 | mapping `csi_driver`를 live provisioner에 맞춤(§5) |
| `mutation_transport_unreachable` | kubectl 도달성·kubeconfig 확인(`ssh-kubectl`이면 bastion SSH 확인) |
| `mutation_no_permission` | `target-cluster-rbac.yaml` 재적용 — `dms-remote`의 `resourcequotas` create/patch/delete 권한(§3.1) |

CSI mapping 재검사(설정 변경 후):

```bash
curl -sS "${DMS_CURL_OPTS[@]}" -X POST \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings/longhorn-a:check" \
  | jq '{storage_name, status, k8s_mutation: .mapping.readiness.kubernetes_mutation}'
```

---

## 다음 문서

- [`docs/api/resource-management-k8s.md`](../docs/api/resource-management-k8s.md) — 네임스페이스 쿼터 API(생성/PATCH/`:block`/`:check`/`:sync`/`:import`/`:audit`/만료 sweep)
- [`docs/api/operations.md`](../docs/api/operations.md) — inventory · storage-mapping · 쿼터 상태 조회
- [`dms-03-rm-filesystem.md`](dms-03-rm-filesystem.md) — 파일시스템 RM + storage mapping CRUD 상세
- [`dms-06-configuration.md`](dms-06-configuration.md) — `DMS_CLUSTER_KUBECONFIGS_JSON`, `DMS_KUBERNETES_*` 등 환경변수 레퍼런스
- [`docs/operations-runbook.md`](../docs/operations-runbook.md) — 운영 런북
