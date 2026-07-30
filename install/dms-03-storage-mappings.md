# DMS 설치 — 스토리지 매핑(인벤토리) 등록

스토리지 백엔드(**CephFS / WekaFS / GPFS**)와 **CSI 스토리지**(`ceph-csi` / `gpfs-csi` / `weka-csi`)를
DMS에 **스토리지 매핑**으로 등록하는 설치 문서다. 매핑은 DMS의 스토리지 인벤토리이자
**DM 데이터 잡(scan/sync/rm)의 대상 목록**이며, 노드 에이전트가 어떤 마운트를 프로브할지도 여기서 정해진다.
스토리지 host-mount, 멀티 클러스터 kubeconfig/RBAC, 매핑 등록 절차, 그리고 readiness가 `Ready`로
전환되는 조건까지 다룬다.

- **API 사용법(등록/수정/삭제/`:check`의 전체 필드·응답)은 이 문서 범위가 아니다** →
  [`docs/api/storage-mappings.md`](../docs/api/storage-mappings.md).
- 클러스터/노드 사전 준비(스토리지 host-mount, 노드 NSS/SSSD)는 먼저
  [`dms-01-prerequisites.md`](dms-01-prerequisites.md)에서 끝냈다고 전제한다.
- 코어 배포(이미지, control-plane, LDAP env, mTLS, Secret/RBAC)는
  [`dms-02-core.md`](dms-02-core.md)에서 끝냈다고 전제한다.
- 모든 환경변수의 전체 목록·기본값은 [`dms-05-configuration.md`](dms-05-configuration.md).

> **placeholder 규약.** 아래 값(`registry.example.internal`, `cluster-a`, `/cephfs`,
> `dc=example,dc=internal`, `node1`…)은 모두 예시다. 실제 환경 값으로 치환한다.

---

## 1. 매핑 종류 — 파일시스템 vs CSI

| 항목 | 파일시스템 매핑 | CSI 매핑 |
|---|---|---|
| `backend_type` | `cephfs` / `wekafs` / `gpfs` | `ceph-csi` / `gpfs-csi` / `weka-csi` |
| 접근 방식 | 노드에 **host-mount된 경로**를 직접 읽고 쓴다 | **PVC**를 잡 파드에 붙여 쓴다 |
| 필수 필드 | `mount_path` + `managed_root`(+ GPFS는 `filesystem_name`) | `cluster_name` + `storage_class_name` + `csi_driver` |
| DM 용도 | 경로↔경로 scan/sync/rm | **PVC↔PVC sync 대상** |
| host-mount 필요 | **필요**(2장) | 불필요 |
| `data_management` readiness | 에이전트 마운트 증거로 `Ready` | 마운트 증거가 없어 `Missing`이 정상 |

> CSI 매핑은 등록만으로 인벤토리에 잡히며, DM sync의 PVC 대상으로 지정할 수 있다. 파일시스템 매핑과
> **같은 레코드/같은 API**를 쓰되 필수 필드만 다르다.

파일시스템 백엔드별 차이는 다음과 같다:

| 항목 | CephFS | WekaFS | GPFS |
|---|---|---|---|
| host-mount 경로(예) | `/cephfs` | `/weka` | `/gpfs` |
| 등록 필수 필드 | `managed_root` | `managed_root` | `managed_root` + `filesystem_name` |
| 기본 `csi_driver` | `rook-ceph.cephfs.csi.ceph.com` | `csi.weka.io` | `spectrumscale.csi.ibm.com` |

---

## 2. 스토리지 host-mount (파일시스템 백엔드 공통)

노드 에이전트가 마운트 존재를 관측해 readiness를 판정하고, DM 잡 파드가 그 경로에서 데이터를 읽고 쓴다.
따라서 각 파일시스템 스토리지는 **DM 잡을 돌릴 모든 노드에 같은 절대 경로로 read-write
host-mount** 되어 있어야 한다(fstab 등, [`dms-01-prerequisites.md`](dms-01-prerequisites.md)).
마운트가 없으면 그 노드의 마운트 프로브가 `Missing`으로 남아 `data_management` readiness가 서지 않고,
planner가 DM 잡을 `no_ready_dm_candidate`로 거부한다.

`managed_root`는 **반드시 `mount_path` 하위**여야 하며, DM의 `DMS_DM_PATH_BASE=managed_root` 모드가
이 값을 경로 경계/기준점으로 쓴다.

> **노드 신원 해석(NSS/SSSD).** DM 잡은 요청자의 POSIX uid/gid로 실행되므로 잡을 돌릴 노드가 LDAP
> 사용자·그룹을 NSS로 해석할 수 있어야 한다(SSSD 또는 동등한 연동). 클러스터 사전 준비는
> [`dms-01-prerequisites.md`](dms-01-prerequisites.md), DM 측 게이트는
> [`dms-04-dm-jobs.md §3`](dms-04-dm-jobs.md).

---

## 3. 대상 클러스터 등록 (RBAC + kubeconfig)

인벤토리(StorageClass·CSI driver·노드)는 대상 클러스터의 API server에서 **읽기 전용**으로 수집한다.
그 접근에 필요한 것이 대상 클러스터의 RBAC + kubeconfig다.

> **control cluster(`cluster-a`)는 [`dms-02-core.md`](dms-02-core.md) §5에서 이미 등록**됐다(같은
> `target-cluster-rbac.yaml` + `create-serviceaccount-kubeconfig.sh`). 이 3장은 **원격 대상
> 클러스터(`cluster-b` 등)**를 추가할 때 그 클러스터에 대해 반복하는 절차다 — 단일 클러스터
> 배포는 건너뛰고 4장으로 간다. 아래 예시의 `cluster-a`는 각 대상 클러스터 이름으로 바꿔 읽는다.

### 3.1 대상 클러스터에 DMS RBAC 적용

파일: [`install/kubernetes/target-cluster-rbac.yaml`](kubernetes/target-cluster-rbac.yaml) — 대상
클러스터에 다음을 만든다:

- `Namespace/dms`
- `ServiceAccount/dms-remote` (namespace `dms`)
- `ClusterRole` + `ClusterRoleBinding` `dms-remote-inventory` — `nodes`/`namespaces` get/list,
  `storageclasses`/`csidrivers` get/list. **읽기 전용이다** — 이 SA는 대상 클러스터에 아무것도
  만들거나 바꾸지 않는다.

**kubectl이 대상 클러스터를 가리키는 상태에서** 적용한다(단일 클러스터면 control cluster 자신):

```bash
kubectl --context cluster-a apply -f install/kubernetes/target-cluster-rbac.yaml
```

> **편집.** 보통 그대로 적용한다. namespace/SA 이름(`dms` / `dms-remote`)은 §3.2 스크립트 기본값과
> 짝을 이룬다 — 바꾸려면 `DMS_REMOTE_NAMESPACE` / `DMS_REMOTE_SERVICE_ACCOUNT`도 함께 바꾼다.

### 3.2 dms-remote SA용 kubeconfig 생성

스크립트: [`install/scripts/create-serviceaccount-kubeconfig.sh`](scripts/create-serviceaccount-kubeconfig.sh)
`<cluster-name> <output>` — 대상 API server 주소·CA·SA 토큰을 임베드한 kubeconfig를 만든다. **kubectl이
대상 클러스터를 가리키는 상태에서** 실행한다:

```bash
KUBECONFIG_OUT=/opt/dms-secrets/cluster-a.kubeconfig
kubectl config use-context cluster-a          # kubectl이 대상을 가리키게
install/scripts/create-serviceaccount-kubeconfig.sh cluster-a "$KUBECONFIG_OUT"

# 동작 확인: 이 kubeconfig로 StorageClass가 보여야 한다
KUBECONFIG="$KUBECONFIG_OUT" kubectl get storageclass
KUBECONFIG="$KUBECONFIG_OUT" kubectl get csidrivers
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

> control cluster(`cluster-a`) 값은 [`dms-02-core.md`](dms-02-core.md) §5에서 이미 채웠다. 여기서는
> **추가 대상 클러스터**에 대해 위 두 곳(Secret 키 + `DMS_CLUSTER_KUBECONFIGS_JSON` 항목)을 더한다
> (patch + rollout restart는 아래).

> **클러스터 이름을 `cluster-a`가 아닌 것으로 바꾸는 경우**, 세 곳을 **일관되게** 바꾼다 —
> `DMS_CONTROL_CLUSTER_NAME`, `DMS_CLUSTER_KUBECONFIGS_JSON`의 키(및 경로), Secret의 키 이름
> (`<name>.kubeconfig`). 이 키는 storage mapping의 `cluster_name`과 정확히 일치해야 한다.

**추가 대상 클러스터(`cluster-b`)를 이미 떠 있는 배포에 붙일 때** — patch 후 재시작:

```bash
# 1) 생성한 kubeconfig을 Secret에 base64로 추가
kubectl -n dms patch secret dms-cluster-kubeconfigs --type merge \
  -p "{\"data\":{\"cluster-b.kubeconfig\":\"$(base64 -w0 /opt/dms-secrets/cluster-b.kubeconfig)\"}}"

# 2) ConfigMap의 DMS_CLUSTER_KUBECONFIGS_JSON에 항목 추가 (기존 키 유지)
kubectl -n dms patch configmap dms-runtime-config --type merge -p \
  '{"data":{"DMS_CLUSTER_KUBECONFIGS_JSON":"{\"cluster-a\":\"/etc/dms/kubeconfigs/cluster-a.kubeconfig\",\"cluster-b\":\"/etc/dms/kubeconfigs/cluster-b.kubeconfig\"}"}}'

# 3) ConfigMap/Secret 반영을 위해 재시작
kubectl -n dms rollout restart deploy/dms-api deploy/dms-planner deploy/dms-dm-worker
```

> **sanity-reconciler도 같은 secret이 필요하다.** 주기 sweep이 managed 클러스터의 매핑을
> 평가하려면 [`install/kubernetes/sanity-reconciler.yaml`](kubernetes/sanity-reconciler.yaml)도
> `dms-cluster-kubeconfigs`를 `/etc/dms/kubeconfigs`로 마운트해야 한다(매니페스트에 이미 포함).

### 3.4 등록 확인

inventory에 대상 클러스터의 StorageClass / CSI driver가 보이면 성공이다:

```bash
curl -sS "${CURL_MTLS[@]}" "$DMS_API_URL/api/v1/operations/inventory" \
  | jq '.clusters | to_entries[] | {cluster:.key,
        storage_classes:(.value.storage_classes|map(.name)),
        csi:(.value.csi_drivers|map(.name))}'
```

(`CURL_MTLS` / `DMS_API_URL`은 4장에서 정의한다.)

---

## 4. 스토리지 매핑 등록

스토리지 매핑은 **DMS API로 등록**한다(DB가 source of truth; 별도 파일 유지 안 함). 등록·수정·삭제·조회의
**전체 필드/CRUD/제약은** [`docs/api/storage-mappings.md`](../docs/api/storage-mappings.md)에 있다 —
여기서는 설치에 필요한 최소 커맨드만 싣는다.

**인증(운영 프로필 = mTLS-verified header).** operator는 mTLS 인그레스로 호출하며 **actor는 인증서 subject에서
파생**된다(평문 `x-dms-actor`는 신뢰하지 않음). 아래 변수를 먼저 잡는다:

```bash
DMS_API_URL=https://dms.cluster-a.local        # mTLS 인그레스 (NodePort면 --resolve 병행)
CERTS=/opt/dms-secrets/certs
CURL_MTLS=(--cert $CERTS/operator.crt --key $CERTS/operator.key --cacert $CERTS/dms-server-ca.crt
           -H "authorization: Bearer $DMS_TOKEN")   # 토큰은 기본 필수 (shipped dms-secrets)
```

> **부연(테스트베드/dev 프로필, `DMS_REQUIRE_MTLS_VERIFIED_HEADER=false`).** 이땐 인증서 없이 평문 Bearer +
> `x-dms-actor`로 호출한다(`-H "authorization: Bearer $DMS_TOKEN" -H "x-dms-actor: operator"`). 요청/응답
> 형태만 빠르게 보고 싶을 때 쓰는 읽기 편의용이며, 운영 경로는 위 mTLS다.

**설치에 필요한 `backend_template` 핵심 필드:**

| 필드 | 필수 | 설명 |
|---|---|---|
| `backend_type` | 필수 | `cephfs` / `wekafs` / `gpfs` / `ceph-csi` / `gpfs-csi` / `weka-csi` |
| `cluster_name` | 필수 | DMS 클러스터 이름(= `DMS_CLUSTER_KUBECONFIGS_JSON`의 키) |
| `mount_path` | 파일시스템 필수 | 각 노드에 마운트된 절대 경로 |
| `managed_root` | 파일시스템 **필수** | DMS가 관리하는 루트(반드시 `mount_path` 하위). 생략 시 등록 `422` |
| `filesystem_name` | **gpfs 필수** | 대상 device(예 `gpfs0`). WEKA는 선택(생략 시 `storage_name`) |
| `csi_driver` | 선택(CSI는 사실상 필수) | live StorageClass provisioner와 **일치**해야 한다. 생략 시 `csi_driver_matches` sanity 제외 |
| `weka_profile` | 선택(weka) | `weka --profile <name>`(멀티 클러스터) |
| `weka_credentials` | 선택(weka) | `{username, password, org}`. 응답에서 `password`만 redaction |

### 4.1 CephFS

```bash
curl -sS "${CURL_MTLS[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/storage-mappings" \
  -d '{
    "storage_name": "cephfs-a",
    "backend_template": {
      "backend_type": "cephfs",
      "cluster_name": "cluster-a",
      "mount_path": "/cephfs",
      "managed_root": "/cephfs/dms",
      "csi_driver": "rook-ceph.cephfs.csi.ceph.com"
    },
    "cluster_name": "cluster-a",
    "storage_class_name": "rook-cephfs"
  }' | jq '{storage_name, status}'
```

### 4.2 WekaFS

```bash
curl -sS "${CURL_MTLS[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/storage-mappings" \
  -d '{
    "storage_name": "weka-a",
    "backend_template": {
      "backend_type": "wekafs",
      "cluster_name": "cluster-a",
      "filesystem_name": "weka0",
      "mount_path": "/weka",
      "managed_root": "/weka/dms",
      "csi_driver": "csi.weka.io"
    },
    "cluster_name": "cluster-a",
    "storage_class_name": "weka-sc"
  }' | jq '{storage_name, status}'
```

### 4.3 GPFS

`filesystem_name`(대상 GPFS device, 예 `gpfs0`)이 **필수**다 — `managed_root`와 함께 둘 중 하나라도
없으면 등록이 `422`로 거부된다.

```bash
curl -sS "${CURL_MTLS[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/storage-mappings" \
  -d '{
    "storage_name": "gpfs-a",
    "backend_template": {
      "backend_type": "gpfs",
      "cluster_name": "cluster-a",
      "filesystem_name": "gpfs0",
      "mount_path": "/gpfs",
      "managed_root": "/gpfs/dms",
      "csi_driver": "spectrumscale.csi.ibm.com"
    },
    "cluster_name": "cluster-a"
  }' | jq '{storage_name, status}'
```

### 4.4 CSI 스토리지 (PVC↔PVC sync 대상)

host-mount 없이 PVC로만 접근하는 스토리지는 CSI 타입으로 등록한다. `cluster_name` +
`storage_class_name` + `csi_driver`만 있으면 된다.

```bash
curl -sS "${CURL_MTLS[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/storage-mappings" \
  -d '{
    "storage_name": "ceph-csi-a",
    "backend_template": {
      "backend_type": "ceph-csi",
      "cluster_name": "cluster-a",
      "csi_driver": "rook-ceph.cephfs.csi.ceph.com"
    },
    "cluster_name": "cluster-a",
    "storage_class_name": "rook-cephfs"
  }' | jq '{storage_name, status}'
```

> `gpfs-csi`(`spectrumscale.csi.ibm.com`) · `weka-csi`(`csi.weka.io`)도 같은 형태다.

### 4.5 재검사 (`:check`) · 수정 · 삭제

```bash
# sanity 재실행 (설정/에이전트 변경 후 readiness 갱신)
curl -sS "${CURL_MTLS[@]}" -X POST \
  "$DMS_API_URL/api/v1/storage-mappings/cephfs-a:check" | jq '{storage_name, status}'

# 수정 — 부분 patch가 아니라 전체 backend_template을 round-trip해야 한다
curl -sS "${CURL_MTLS[@]}" -X PATCH -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/storage-mappings/cephfs-a" -d '{ ... 전체 본문 ... }' | jq

# 삭제 (하드 삭제; 진행 중 작업이 있으면 409)
curl -sS "${CURL_MTLS[@]}" -X DELETE \
  "$DMS_API_URL/api/v1/storage-mappings/cephfs-a" | jq '{storage_name, deleted}'
```

> 등록 직후엔 `data_management: Missing`이 정상이다(DM agent가 아직 새 목록을 못 읽었거나 DM 축이
> 미구성). 5장의 rollout-restart 후 6장 조건을 확인한다. CSI 매핑은 `data_management`가 계속
> `Missing`인 것이 정상이다.

---

## 5. 에이전트 storages ConfigMap 동기화

에이전트는 어떤 스토리지의 마운트를 프로브할지 ConfigMap `dms-agent-storages`(`storages.json`)에서 읽는다.
매니페스트의 값은 **bootstrap seed일 뿐**이고, 스토리지 매핑을 등록/수정/삭제할 때마다 **dms-api가 이
ConfigMap을 등록된 매핑으로 덮어쓴다**(각 `storages` 항목의 `mount_paths`에 **호스트 마운트 지점**이 채워짐).

이 덮어쓰기에는 코어에서 적용한 Role/RoleBinding **`dms-agent-storages-sync`**(control-plane.yaml,
`configmaps` `dms-agent-storages`에 get/update/patch, `dms-api`+`dms-remote`에 바인딩)가 필요하다. **없으면
patch가 `Forbidden`인데 코드가 그 예외를 삼켜(warning 로그만) ConfigMap이 조용히 갱신되지 않는다** → 새로
등록한 스토리지가 에이전트에 전달되지 않아 마운트 readiness가 `Missing`으로 남고, planner가 DM 잡을
`no_ready_dm_candidate`로 거부한다. 존재 확인:

```bash
kubectl -n dms get role,rolebinding dms-agent-storages-sync
# 동기화 결과 확인
kubectl -n dms get configmap dms-agent-storages -o jsonpath='{.data.storages\.json}' | jq '.storages[].storage_name'
```

**에이전트는 `storages.json`을 시작 시 한 번만 읽는다.** 따라서 스토리지 매핑을 등록/수정한 뒤에는
DaemonSet을 재시작해 새 목록을 다시 읽게 한다:

```bash
# API로(권장) — 토큰은 기본 필수 → Bearer도 함께.
curl -sS "${CURL_MTLS[@]}" -X POST "$DMS_API_URL/api/v1/agent/rollout-restart"
# 또는 kubectl 직접
kubectl -n dms rollout restart daemonset/dms-dm-agent
kubectl -n dms rollout status  daemonset/dms-dm-agent --timeout=180s
```

> DM 노드 에이전트(`dms-dm-agent`)의 이미지·신원 설정·host mountinfo bind-mount는
> [`dms-04-dm-jobs.md`](dms-04-dm-jobs.md)에서 다룬다.

---

## 6. readiness가 `Ready` 되는 조건 (체크리스트)

매핑의 readiness는 **두 축**이다.

| 축 | 판정 근거 | `Ready`가 아니면 |
|---|---|---|
| `data_management` | 노드 에이전트가 보고한 마운트 + mpifileutils 도구 + 신원 증거 | planner가 DM 잡을 `no_ready_dm_candidate`로 거부 |
| `inventory` | 대상 클러스터의 live StorageClass 존재 + `csi_driver` 일치 | `storage_class_missing` / `csi_driver_mismatch` sanity 오류 |

파일시스템 스토리지의 `data_management`가 `Ready`가 되려면:

1. **스토리지 매핑 등록됨** — `managed_root` 명시(+ GPFS는 `filesystem_name`).
2. **`dms-agent-storages` ConfigMap 동기화됨** — `dms-agent-storages-sync` RBAC 존재(5장). 매핑 등록 후
   해당 `storage_name`이 ConfigMap `storages.json`에 나타나야 한다.
3. **DM Agent가 대상 노드에서 Running** 이고 매핑 등록/변경 후 **rollout-restart** 됨(5장).
4. **호스트 mountinfo bind-mount 활성** — 에이전트가 실제 마운트를 관측
   ([`dms-04-dm-jobs.md`](dms-04-dm-jobs.md)).
5. **스토리지가 그 노드들에 rw로 host-mount** 됨(2장).

`inventory`가 `Ready`가 되려면 3장의 클러스터 등록이 끝나 있고, 매핑의 `storage_class_name`이 대상
클러스터에 실제로 존재하며 `csi_driver`가 live provisioner와 일치해야 한다.

확인:

```bash
curl -sS "${CURL_MTLS[@]}" "$DMS_API_URL/api/v1/operations/storage-mappings/cephfs-a" \
  | jq '{storage_name, sanity_status, readiness}'
# inventory: Ready 확인 (data_management는 DM 구성 후 Ready)
```

에이전트 프로브 단독 확인:

```bash
POD=$(kubectl -n dms get pods -l app.kubernetes.io/name=dms-dm-agent -o jsonpath='{.items[0].metadata.name}')
kubectl -n dms exec "$POD" -- dms agent-probe --once | jq '.mounts[] | {storage_name, status}'
# 노드에 실제 마운트된 storage만 Ready, 나머지는 Missing이면 정상
```

`Failed` 사유와 조치:

| error / 사유 | 조치 |
|---|---|
| `cluster_missing` | 3장 클러스터 등록 확인 — `DMS_CLUSTER_KUBECONFIGS_JSON` 키와 Secret kubeconfig |
| `storage_class_missing` | 대상 클러스터에 해당 StorageClass 존재/이름 확인 |
| `csi_driver_mismatch` | 매핑 `csi_driver`를 live provisioner에 맞춤(4장) |

---

## 다음 문서

- [`dms-04-dm-jobs.md`](dms-04-dm-jobs.md) — DM(데이터 잡: scan/sync/rm) 설정 (DM Agent 이미지·신원·Volcano)
- [`dms-05-configuration.md`](dms-05-configuration.md) — 환경변수 레퍼런스(LDAP·kubernetes·agent 등)
- [`docs/api/storage-mappings.md`](../docs/api/storage-mappings.md) — 스토리지 매핑 **API 사용법**(등록/수정/삭제/`:check`)
- [`docs/api/operations.md`](../docs/api/operations.md) — inventory · storage-mapping 조회
- 되돌아가기: [`dms-02-core.md`](dms-02-core.md) (control-plane·LDAP env·mTLS·Secret/RBAC), [`dms-01-prerequisites.md`](dms-01-prerequisites.md) (host-mount·NSS/SSSD)
