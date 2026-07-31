# 스토리지 매핑 API

스토리지 매핑(스토리지 인벤토리)은 **백엔드 스토리지를 DMS에 등록**하는 레코드다. DM 데이터
잡(`scan`/`sync`/`rm`)이 대상 스토리지를 찾는 근거이자, 노드 에이전트가 어떤 마운트를 프로브할지 정하는
목록이며, 인벤토리(StorageClass·CSI driver) 대조의 기준이다. **DB가 source of truth**이며 별도 파일을
유지하지 않는다.

이 문서는 **사용(HTTP API) 문서**다. 스토리지 host-mount, 노드 NSS/SSSD, 멀티 클러스터 kubeconfig/RBAC
같은 설치·사전 준비는 [`install/dms-03-storage-mappings.md`](../../install/dms-03-storage-mappings.md)에서
다룬다.

- DM(데이터 잡: scan/sync/rm) API → [`data-management.md`](data-management.md)
- 읽기 전용 operations 조회 전체(인벤토리·work-summary·action-required·control-state 등) →
  [`operations.md`](operations.md)
- 인증 상세와 API 개요 → [`README.md`](README.md)

---

## 인증과 curl 규약

**운영 프로필 = mTLS-verified header.** DMS API는 신뢰 ingress가 클라이언트 인증서를 검증한 뒤 upstream으로
넘기고, DMS는 **인증서 subject에서 actor를 파생**한다(prefix는 `DMS_MTLS_ACTOR_PREFIX`, 기본 `mtls:`).
따라서 운영에서는 curl에 **클라이언트 인증서**를 붙이고 평문 `x-dms-actor` 헤더는 **보내지 않는다**(신뢰되지
않음). 공유 bearer 토큰(`DMS_AUTH_SHARED_TOKEN`)은 **기본 배포에서 필수**이므로 `Authorization: Bearer`를
항상 얹는다. 이 문서의 모든 예시는 아래 `CURL` 배열을 전제로 한다.

```bash
DMS_API_URL="https://dms.cluster-a.local"

# 운영(mTLS-verified) 프로필: 인증서로 인증, actor는 인증서 subject에서 파생
CURL=(--cert /etc/dms/client.crt --key /etc/dms/client.key --cacert /etc/dms/ca.crt)
# 공유 bearer 토큰은 기본 배포에서 필수 (shipped dms-secrets가 이를 싣는다):
CURL+=(-H "Authorization: Bearer $DMS_AUTH_SHARED_TOKEN")
```

> **부연(dev/testbed 프로필).** `DMS_REQUIRE_MTLS_VERIFIED_HEADER=false`인 비운영 프로필에서는 인증서 없이
> 평문 Bearer + `x-dms-actor`로 호출해 request/response 형태만 빠르게 확인할 수 있다:
> `CURL=(-H "Authorization: Bearer $TOKEN" -H "x-dms-actor: operator")`. **운영에서는 `x-dms-actor`가
> 신뢰되지 않고 `DMS_DEFAULT_ACTOR`도 비워야 하므로**(설정 시 API 기동 실패) 이 형태를 쓰지 않는다.

---

## 1. 엔드포인트 요약

**읽기는 `operations` 라우터**, **쓰기는 전용 `/api/v1/storage-mappings` 라우터**로 나뉜다.

| Method | Path | 용도 |
|---|---|---|
| `GET` | `/api/v1/operations/storage-mappings` | 전체 목록(`?cluster_name=&limit=&offset=`) |
| `GET` | `/api/v1/operations/storage-mappings/{name}` | 단건 조회(redacted, `sanity_result` 포함) |
| `POST` | `/api/v1/storage-mappings` | 신규 등록(upsert) |
| `PATCH` | `/api/v1/storage-mappings/{name}` | 수정(전체 round-trip) |
| `DELETE` | `/api/v1/storage-mappings/{name}` | 삭제(하드 삭제) |
| `POST` | `/api/v1/storage-mappings/{name}:check` | sanity check 재실행 |

> **콜론 액션 주의.** zsh에서는 `"$DMS_API_URL/api/v1/storage-mappings/${name}:check"`처럼 브레이스로
> 감싸 호출한다(`"$name:check"`는 수식어로 변형돼 404).

쓰기 계열(POST/PATCH/DELETE/`:check`)은 request→plan→run 상태 머신을 타지 않고 **동기적으로 처리**된다 —
응답이 곧 결과이며 폴링할 `request_id`가 없다.

---

## 2. backend_template 필드

> **`(cluster_name, storage_class_name)`은 유니크하다**(`uq_storage_class_mapping`). 같은 클러스터의
> 한 StorageClass를 두 매핑이 가리킬 수 없으므로, 같은 스토리지를 host-mount(fs)와 PVC(CSI) 양쪽으로
> 등록하려면 StorageClass를 서로 다르게 잡는다(아래 예시: `rook-cephfs` / `rook-cephfs-csi`).

매핑은 최상위 필드(`storage_name` · `cluster_name` · `storage_class_name`)와 백엔드별
`backend_template`으로 이루어진다.

| 필드 | 필수 | 설명 |
|---|---|---|
| `backend_type` | 필수 | 파일시스템: `cephfs` / `gpfs` / `wekafs`. CSI(k8s 볼륨): `ceph-csi` / `gpfs-csi` / `weka-csi` |
| `cluster_name` | 필수 | DMS 클러스터 이름(= `DMS_CLUSTER_KUBECONFIGS_JSON`의 키) |
| `mount_path` | 파일시스템 필수 | 각 노드에 마운트된 절대 경로(예: `/cephfs`) |
| `managed_root` | 파일시스템 **필수** | DMS가 관리하는 루트 디렉토리. **반드시 `mount_path` 하위**. 생략 시 등록이 `422`로 거부된다. DM의 `DMS_DM_PATH_BASE=managed_root` 모드가 이 값을 경계/기준점으로 쓴다 |
| `filesystem_name` | **gpfs 필수** / wekafs 선택 | 대상 filesystem(device) 이름(예: `gpfs0`, `weka0`). 생략 시 GPFS는 `422`, WEKA는 `storage_name`으로 폴백. CephFS는 해당 없음 |
| `csi_driver` | 선택(CSI는 사실상 필수) | 이 스토리지의 PVC를 provisioning하는 CSI 드라이버. live StorageClass의 provisioner와 **일치**해야 하며 불일치 시 sanity `csi_driver_mismatch`. 생략 시 `csi_driver_matches` 검사에서 제외 |
| `weka_profile` | 선택(weka) | `weka --profile <name>` 옵션(멀티 클러스터) |
| `weka_credentials` | 선택(weka) | `{username, password, org}`. **응답에서 `password`만 redaction**되며, 재전송 시 생략하면 DMS가 기존 값을 merge한다 |

> **파일시스템 매핑 vs CSI 매핑.** `cephfs`/`gpfs`/`wekafs`는 **호스트 마운트**를 가진 파일시스템 매핑이라
> `mount_path` + `managed_root`가 필수이고, 노드 에이전트의 마운트 증거로 DM readiness가 선다.
> `ceph-csi`/`gpfs-csi`/`weka-csi`는 **PVC 기반 CSI 매핑**이라 호스트 마운트가 없고
> (`cluster_name` + `storage_class_name` + `csi_driver`만 있으면 등록된다), DM의 **PVC↔PVC sync 대상**으로
> 쓰인다. CSI 매핑은 마운트 증거가 없으므로 `data_management` 축이 `Missing`으로 남는 것이 정상이다.

---

## 3. 등록 (POST)

```bash
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/storage-mappings" \
  -d '{
    "storage_name": "cephfs-cluster-a",
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

응답: `{"storage_name": "cephfs-cluster-a", "status": "Degraded"}` — `status`는 sanity 결과이며 등록
직후 agent 마운트 증거가 아직 없으면 `Degraded`로 시작할 수 있다. 이미 존재하면 upsert(덮어쓰기)로 동작한다.

GPFS 예시(등록 시 `filesystem_name` 필수):

```bash
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
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

CSI 매핑 예시(PVC↔PVC sync 대상 — 호스트 마운트 없음):

```bash
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/storage-mappings" \
  -d '{
    "storage_name": "ceph-csi-a",
    "backend_template": {
      "backend_type": "ceph-csi",
      "cluster_name": "cluster-a",
      "csi_driver": "rook-ceph.cephfs.csi.ceph.com"
    },
    "cluster_name": "cluster-a",
    "storage_class_name": "rook-cephfs-csi"
  }' | jq '{storage_name, status}'
```

---

## 4. 조회 (GET — operations 라우터)

```bash
# 전체 목록 (storage_name만)
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/storage-mappings" | jq '.[].storage_name'

# 클러스터별 필터
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/storage-mappings?cluster_name=cluster-a" | jq '.[].storage_name'

# 단건 상세 (sanity/readiness + DM 후보 노드)
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/storage-mappings/cephfs-cluster-a" \
  | jq '{storage_name, sanity_status, readiness,
         dm_candidates: [.sanity_result.agent_observed.dm_candidates[]? | {node_name, status}]}'
```

`readiness`는 축별로 `Ready`/`Missing` 등을 보고한다. 축은 **두 개**다:

| 축 | 판정 근거 | 영향 |
|---|---|---|
| `data_management` | 노드 에이전트(`dms-dm-agent`)가 보고한 마운트 + mpifileutils 도구 + 신원 증거 | `Ready`가 아니면 planner가 DM 잡을 `no_ready_dm_candidate`로 거부 |
| `inventory` | 대상 클러스터에서 읽은 live StorageClass 존재 + `csi_driver` 일치 | 불일치는 `storage_class_missing` / `csi_driver_mismatch` sanity 오류 |

---

## 5. 수정 (PATCH)

> **PATCH는 부분 patch가 아니라 전체 `backend_template`을 round-trip**해야 한다. 현재 상태를 GET으로 읽어
> 바꿀 필드만 고친 뒤 **전체를 다시 보낸다**. 부분만 보내면 나머지 필드가 사라진다.
> 시크릿(`weka_credentials.password`)은 생략하면 DMS가 기존 값을 merge하므로 다시 넣지 않아도 된다.

```bash
# 예: managed_root를 변경 (나머지 필드는 현재 값 그대로 재전송)
curl -sS "${CURL[@]}" \
  -X PATCH -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/storage-mappings/cephfs-cluster-a" \
  -d '{
    "storage_name": "cephfs-cluster-a",
    "backend_template": {
      "backend_type": "cephfs", "cluster_name": "cluster-a",
      "mount_path": "/cephfs", "managed_root": "/cephfs/dms",
      "csi_driver": "rook-ceph.cephfs.csi.ceph.com"
    },
    "cluster_name": "cluster-a", "storage_class_name": "rook-cephfs"
  }' | jq '{storage_name, status, managed_root: .mapping.backend_template.managed_root}'
```

제약: body의 `storage_name`은 path와 일치해야 함(불일치 `400`), 없는 스토리지 `404`, 진행 중인
request/data_job이 있으면 `409`.

---

## 6. 삭제 (DELETE) / sanity 재실행 (`:check`)

```bash
# 하드 삭제 (disable/enable 엔드포인트는 없음)
curl -sS "${CURL[@]}" \
  -X DELETE "$DMS_API_URL/api/v1/storage-mappings/cephfs-cluster-a" \
  | jq '{storage_name, deleted}'

# sanity check만 재실행
curl -sS "${CURL[@]}" \
  -X POST "$DMS_API_URL/api/v1/storage-mappings/cephfs-cluster-a:check" \
  | jq '{storage_name, status}'
```

DELETE 응답에는 삭제된 매핑 전체가 (조회와 달리 **un-redacted**로) 포함되므로, 이 값을 로그로 남기거나
클라이언트로 그대로 전달하지 않도록 주의한다. 없는 스토리지 `404`, 진행 중 작업이 있으면 `409`.

---

## 7. Agent ConfigMap 자동 동기화 (등록 후 필수 후속)

POST/PATCH/DELETE는 `dms-agent-storages` ConfigMap을 **자동 동기화**한다(수동 편집 불필요). 다만 **agent는
`storages.json`을 기동 시 1회만 읽는다** — 매핑을 추가/변경한 뒤에는 DaemonSet을 rollout-restart해야
새 스토리지가 반영된다:

```bash
curl -sS "${CURL[@]}" -X POST "$DMS_API_URL/api/v1/agent/rollout-restart" | jq
```

> 자동 동기화가 조용히 no-op이 되면(신규 스토리지가 에이전트에 안 뜸 → DM `no_ready_dm_candidate`)
> ConfigMap patch RBAC이 누락된 것이다. 이는 설치 사안이므로
> [`install/dms-03-storage-mappings.md`](../../install/dms-03-storage-mappings.md)를 참고한다.

---

## 다음 문서

- 스토리지 매핑 설치·사전 준비(host-mount·NSS/SSSD·멀티 클러스터 RBAC/kubeconfig) →
  [`install/dms-03-storage-mappings.md`](../../install/dms-03-storage-mappings.md)
- DM(scan/sync/rm) API → [`data-management.md`](data-management.md)
- 읽기 전용 operations 조회 전체 → [`operations.md`](operations.md)
- 운영 런북(sanity 점검·drift·stuck 정리 루틴) → [`../operations-runbook.md`](../operations-runbook.md)
- API 개요 + 인증 → [`README.md`](README.md)
