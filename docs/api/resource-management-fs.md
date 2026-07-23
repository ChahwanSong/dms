# 파일시스템 Resource Management API

DMS의 **파일시스템 RM API** 사용법 — 스토리지 매핑 등록부터 디렉토리(fileset) 생성·quota·소유권·차단·삭제,
정합성 점검, 외부 자산 채택, 만료 처리, 그리고 stuck request 해소까지. 대상 백엔드는 **CephFS / GPFS /
WekaFS**다.

이 문서는 **사용(HTTP API) 문서**다. 클러스터/백엔드 사전 설정(GPFS per-fileset quota, `managed_root`
사전 생성, LDAP/AD 연동, SSH 자격증명, 노드 NSS/SSSD, 스토리지 host-mount, 관련 RBAC)은 설치 문서
[`install/dms-03-rm-filesystem.md`](../../install/dms-03-rm-filesystem.md)에서 다룬다. 여기서는 그 준비가
끝났다고 가정한다.

- Kubernetes 네임스페이스 quota RM API → [`resource-management-k8s.md`](resource-management-k8s.md)
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

> **`requester_id`(요청 본문) ≠ 인증 actor(인증서 subject).** `requester_id`는 리소스의 논리적 요청자(자유
> 형식 id, 소유권·감사 대상)이고, 인증 actor는 API를 호출한 주체다. 둘은 별개이며 요청 본문에는 항상
> `requester_id`를 담는다.

> **부연(dev/testbed 프로필).** `DMS_REQUIRE_MTLS_VERIFIED_HEADER=false`인 비운영 프로필에서는 인증서 없이
> 평문 Bearer + `x-dms-actor`로 호출해 request/response 형태만 빠르게 확인할 수 있다:
> `CURL=(-H "Authorization: Bearer $TOKEN" -H "x-dms-actor: operator")`. **운영에서는 `x-dms-actor`가
> 신뢰되지 않고 `DMS_DEFAULT_ACTOR`도 비워야 하므로**(설정 시 API 기동 실패) 이 형태를 쓰지 않는다.

---

## 요청 처리 모델

- **모든 mutating 엔드포인트(`POST`/`PATCH`/`DELETE`)는 `202`로 접수만 하고 비동기 처리**한다. 응답 본문은
  `{"request_id": "req_...", "status": "Persisted"}`이며, 최종 상태는
  `GET /operations/requests/{request_id}` 폴링으로 확인한다(§3.2).
- **리소스 경로 키.** 파일시스템 리소스는 `{storage_name}/{directory_name}`로 지정한다(URL path). 내부
  resource_key 문자열 표기는 `{storage_name}:{directory_name}`(예: `gpfs-a:project1`)이며 operations 응답에
  이 형태로 나타난다.
- **request 상태값:**

  | 상태 | 의미 | terminal |
  |---|---|---|
  | `Persisted` | 접수됨, planner 대기 | |
  | `Planned` | planner가 실행 계획 수립 | |
  | `Applying` | rm-worker가 백엔드 실행 중 | |
  | `Verifying` | 실행 결과 검증 중 | |
  | `Succeeded` | **정상 완료** | ✓ |
  | `Failed` | 실패 처리됨 | ✓ |
  | `BackendApplyFailed` | 백엔드 실행 실패 | ✓ |
  | `Rejected` | validation 실패(`issues` 필드에 원인) | ✓ |
  | `UnknownAfterSideEffect` | side effect 발생 후 결과 불명 → `:resolve` 사용(§4) | ✓ |
  | `Conflict` | 동일 resource에 non-terminal request 존재 → 선행 request 해소 필요 | ✓ |

  > `Rejected` 응답의 `issues[0].status`가 `"Succeeded"`이면 해당 resource가 이미 정상 존재한다는 뜻이다.
  > create 대신 PATCH를 쓴다.

---

## 1. Storage Mapping CRUD

스토리지 매핑은 백엔드(스토리지)를 DMS에 등록하는 첫 단계다. **DB가 source of truth**이며 별도 파일을
유지하지 않는다. 파일시스템 요청(§2)은 등록된 `storage_name`을 대상으로 한다.

| Method | Path | 용도 |
|---|---|---|
| `GET` | `/api/v1/operations/storage-mappings` | 전체 목록(`?cluster_name=` 필터 가능) |
| `GET` | `/api/v1/operations/storage-mappings/{name}` | 단건 조회 |
| `POST` | `/api/v1/resource-management/storage-mappings` | 신규 등록(upsert) |
| `PATCH` | `/api/v1/resource-management/storage-mappings/{name}` | 수정 |
| `DELETE` | `/api/v1/resource-management/storage-mappings/{name}` | 삭제(하드 삭제) |
| `POST` | `/api/v1/resource-management/storage-mappings/{name}:check` | sanity check 재실행 |

### 1.1 backend_template 필드

| 필드 | 필수 | 설명 |
|---|---|---|
| `backend_type` | 필수 | `cephfs` / `gpfs` / `wekafs` |
| `cluster_name` | 필수 | DMS 클러스터 이름 |
| `mount_path` | 필수 | 각 노드에 마운트된 절대 경로(예: `/cephfs`) |
| `managed_root` | **필수** | DMS가 관리하는 루트 디렉토리. **반드시 `mount_path` 하위**. 생략 시 등록이 `422`로 거부(암묵 `{mount_path}/dms` 기본값은 제거됨). RM 디렉토리 연산과 DM의 `DMS_DM_PATH_BASE=managed_root` 모드가 모두 이 값을 경계/기준점으로 쓴다 |
| `filesystem_name` | **gpfs 필수** / wekafs 선택 | 대상 filesystem(device) 이름(예: `gpfs0`, `weka0`). **GPFS는 필수** — `mm*`(fileset/quota) 명령 대상이며 생략 시 `422`. WEKA는 선택(생략 시 `storage_name`), CephFS는 해당 없음 |
| `rm_worker_nodes` | 권장 | RM 작업 대상 노드 목록. Agent가 이 노드들의 마운트 증거를 모아 `rm_readiness` 판정에 쓴다 |
| `ssh_host` | 선택 | RM worker가 `ssh-host-exec` 모드에서 접속할 백엔드 노드. **생략 시 agent 보고 기반 `rm_candidates` 중 Ready 노드를 자동 선택**, 없으면 `rm_worker_nodes[0]`로 fallback |
| `command_runner` | 선택(gpfs/weka) | `local` 또는 `ssh-host-exec`. RM mutation을 워커 노드에서 직접(`local`) 실행할지, `ssh_host`로 원격 실행할지 |
| `csi_driver` | 선택 | K8s PVC provisioning용 CSI 드라이버. 생략 시 backend_type 기본값 적용(`cephfs`→`rook-ceph.cephfs.csi.ceph.com`, `gpfs`→`spectrumscale.csi.ibm.com`, `wekafs`→`csi.weka.io`) |
| `weka_profile` | 선택(wekafs) | `weka --profile <name>` 옵션(멀티 클러스터) |
| `weka_credentials` | 선택(wekafs) | `{username, password, org}`. **응답에서 `password`만 redaction**되며, 재전송 시 생략하면 DMS가 기존 값을 merge한다 |

### 1.2 등록 (POST)

```bash
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings" \
  -d '{
    "storage_name": "cephfs-cluster-a",
    "backend_template": {
      "backend_type": "cephfs",
      "cluster_name": "cluster-a",
      "mount_path": "/cephfs",
      "managed_root": "/cephfs/root",
      "rm_worker_nodes": ["node1","node2","node3"],
      "ssh_host": "node1",
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
  "$DMS_API_URL/api/v1/resource-management/storage-mappings" \
  -d '{
    "storage_name": "gpfs-a",
    "backend_template": {
      "backend_type": "gpfs",
      "cluster_name": "cluster-a",
      "filesystem_name": "gpfs0",
      "mount_path": "/gpfs",
      "managed_root": "/gpfs/dms",
      "rm_worker_nodes": ["gpfs-node1","gpfs-node2"],
      "command_runner": "ssh-host-exec",
      "ssh_host": "gpfs-node1"
    },
    "cluster_name": "cluster-a"
  }' | jq '{storage_name, status}'
```

### 1.3 조회 (GET)

```bash
# 전체 목록 (storage_name만)
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/storage-mappings" | jq '.[].storage_name'

# 클러스터별 필터
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/storage-mappings?cluster_name=cluster-a" | jq '.[].storage_name'

# 단건 상세 (sanity/readiness + rm_candidates)
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/storage-mappings/cephfs-cluster-a" \
  | jq '{storage_name, sanity_status, readiness,
         rm_candidates: [.sanity_result.agent_observed.rm_candidates[]? | {node_name, status}]}'
```

`readiness`는 축별(`resource_management`/`data_management`/`inventory`)로 `Ready`/`Missing` 등을 보고한다.
`resource_management`가 `Ready`여야 §2의 파일시스템 요청이 실제로 실행된다.

### 1.4 수정 (PATCH)

> **PATCH는 부분 patch가 아니라 전체 `backend_template`을 round-trip**해야 한다. 현재 상태를 GET으로 읽어
> 바꿀 필드만 고친 뒤 **전체를 다시 보낸다**. 부분만 보내면 나머지 필드가 사라진다. 시크릿(`weka_credentials.password`)은 생략하면
> DMS가 기존 값을 merge하므로 다시 넣지 않아도 된다.

```bash
# 예: ssh_host를 node2로 변경 (나머지 필드는 현재 값 그대로 재전송)
curl -sS "${CURL[@]}" \
  -X PATCH -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings/cephfs-cluster-a" \
  -d '{
    "storage_name": "cephfs-cluster-a",
    "backend_template": {
      "backend_type": "cephfs", "cluster_name": "cluster-a",
      "mount_path": "/cephfs", "managed_root": "/cephfs/root",
      "rm_worker_nodes": ["node1","node2","node3"],
      "ssh_host": "node2",
      "csi_driver": "rook-ceph.cephfs.csi.ceph.com"
    },
    "cluster_name": "cluster-a", "storage_class_name": "rook-cephfs"
  }' | jq '{storage_name, status, ssh_host: .mapping.backend_template.ssh_host}'
```

제약: body의 `storage_name`은 path와 일치해야 함(불일치 `400`), 없는 스토리지 `404`, 진행 중인
request/data_job이 있으면 `409`.

### 1.5 삭제 (DELETE) / sanity 재실행

```bash
# 하드 삭제 (disable/enable 엔드포인트는 없음)
curl -sS "${CURL[@]}" \
  -X DELETE "$DMS_API_URL/api/v1/resource-management/storage-mappings/cephfs-cluster-a" \
  | jq '{storage_name, deleted}'

# sanity check만 재실행
curl -sS "${CURL[@]}" \
  -X POST "$DMS_API_URL/api/v1/resource-management/storage-mappings/cephfs-cluster-a:check" \
  | jq '{storage_name, status}'
```

DELETE 응답에는 삭제된 매핑 전체가 (조회와 달리 **un-redacted**로) 포함되므로, 이 값을 로그로 남기거나
클라이언트로 그대로 전달하지 않도록 주의한다. 없는 스토리지 `404`, 진행 중 작업이 있으면 `409`.

### 1.6 Agent ConfigMap 자동 동기화 (등록 후 필수 후속)

POST/PATCH/DELETE는 `dms-agent-storages` ConfigMap을 **자동 동기화**한다(수동 편집 불필요). 다만 **agent는
`storages.json`을 기동 시 1회만 읽는다** — 매핑을 추가/변경한 뒤에는 RM·DM DaemonSet을 rollout-restart해야
새 스토리지가 반영된다:

```bash
curl -sS "${CURL[@]}" -X POST "$DMS_API_URL/api/v1/agent/rollout-restart" | jq
```

> 자동 동기화가 조용히 no-op이 되면(신규 스토리지가 에이전트에 안 뜸 → RM `missing_rm_readiness`, DM
> `no_ready_dm_candidate`) ConfigMap patch RBAC이 누락된 것이다. 이는 설치 사안이므로
> [`install/dms-03-rm-filesystem.md`](../../install/dms-03-rm-filesystem.md)를 참고한다.

---

## 2. 파일시스템 리소스 요청 API

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/filesystems` | 신규 생성: fileset/디렉토리 + quota + LDAP `dms-grp-{dir}` 그룹을 한 번에(full-managed) |
| `PATCH` | `/filesystems/{storage}/{dir}` | 부분 변경: `quota`, `owner_username`, `resource_type`, `expires_at` |
| `DELETE` | `/filesystems/{storage}/{dir}` | soft-delete: 디렉토리 잠금 + LDAP 그룹 삭제(데이터 보존) |
| `POST` | `/filesystems/{storage}/{dir}:block` | 차단: `chmod 0000`(`previous_mode` 저장) |
| `POST` | `/filesystems/{storage}/{dir}:initialize` | 차단 해제: `previous_mode`(없으면 생성 시 mode)로 복원 |
| `POST` | `/filesystems/{storage}/{dir}:check` | 정합성 점검: 백엔드/LDAP read-back, drift는 `issues[]`(side-effect 없음) |
| `POST` | `/filesystems/{storage}/{dir}:sync` | 백엔드 live state → DMS `desired_state.quota` 갱신(side-effect 없음) |
| `POST` | `/filesystems/{storage}/{dir}:import` | 외부 fileset/디렉토리를 full-managed로 채택 |
| `POST` | `/filesystems/{storage}/{dir}:assign-quota` | 외부 디렉토리에 quota만 적용(`quota_only`, delete 거부) |
| `POST` | `/filesystems:expiration-sweep` | 만료 리소스 일괄 차단(`scope`/`before`/`dry_run`) |

### 2.1 Create (생성)

fileset/디렉토리 생성 + quota + LDAP access group을 한 번에 처리한다.

```bash
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/filesystems" \
  -d '{
    "requester_id": "alice",
    "payload": {
      "storage_name": "gpfs-a",
      "directory_name": "project1",
      "users": ["alice", "bob"],
      "quota": {"capacity_bytes": 10000000000, "file_count": 1000000},
      "expires_at": "2027-01-01T00:00:00Z"
    }
  }' | jq '{request_id, status}'
```

**payload 필드:**

| 필드 | 필수 | 설명 |
|---|---|---|
| `storage_name` | 필수 | 등록된 storage mapping 이름 |
| `directory_name` | 필수 | fileset/디렉토리 이름(영숫자, 하이픈) |
| `users` | 필수 | 최소 1명. 각 user는 **LDAP에 `uid`로 존재**해야 한다(RM이 `(uid={user})`로 직접 조회해 `dms-grp-{dir}`의 `memberUid`로 등록) |
| `expires_at` | **필수** | 만료일(ISO8601). 생략 시 `Rejected` |
| `quota.capacity_bytes` | 선택 | 용량 제한(bytes) |
| `quota.file_count` | 선택 | inode 수 제한. **CephFS/GPFS만 지원** — **WEKA는 값이 있으면 `BackendApplyFailed`**(조용히 무시 안 함). WEKA에는 `capacity_bytes`만 |
| `access_group` | 선택 | 그룹명. 생략 시 `dms-grp-{directory_name}` 자동 생성 |
| `mode` | 선택 | 디렉토리 권한. 생략 시 `0750`(`0750`/`0770`만 허용) |
| `owner_username` | 선택 | 디렉토리 소유자(POSIX/LDAP user). 생략 시 `requester_id` 사용. `root`/`nobody` 등 명시 override는 거부 |
| `resource_type` | 선택 | `user`(기본)/`project`/`system`/`admin` — §2.9 |

**요청자·소유자·access group 해석(요청이 왜 실패하는지):**

- **소유자 해석은 fail-closed.** `owner_username`(없으면 `requester_id`)이 백엔드 노드에서 POSIX/LDAP user로
  해석되어야 한다(uid 범위 제한 없음). 해석 불가면 **side effect 이전에** `BackendApplyFailed`로 거부되고
  fileset/디렉토리는 만들어지지 않는다.
- **owner는 `users`에 포함되지 않아도 된다** — owner는 owner 권한 비트로, 그 외 허용 사용자는 group 권한으로
  접근한다. 따라서 비-owner 허용 사용자가 써야 하면 `mode: "0770"`을 준다.
- **CephFS/WekaFS는 LDAP bind가 필수 전제**다(어댑터가 생성 시점에 LDAP group manager를 EAGER하게 구성). LDAP이
  없거나 틀리면 그 백엔드의 **모든** create/patch/delete/block/import가 `IdentityLookupConfigurationError`로
  실패한다. GPFS는 optional(운영은 항상 구성). LDAP/AD 설정은
  [`install/dms-03-rm-filesystem.md`](../../install/dms-03-rm-filesystem.md).
- **주의(고아 그룹).** access group 생성이 owner 해석보다 **먼저** 일어나므로, owner 미해결로 거부되면 fileset은
  없는데 LDAP `dms-grp-{dir}`가 남을 수 있다. 정정 후 재시도 시 같은 그룹을 idempotent하게 재사용한다(무해).

응답: `{"request_id": "req_...", "status": "Persisted"}`. 최종 상태는 request 조회(§3.2)로 확인한다. 성공 시
디렉토리는 `owner_uid:group_gid` 소유에 지정 mode(기본 `0750`)로 설정된다.

### 2.2 PATCH (quota / owner / resource_type / expires_at)

한 요청에 여러 필드를 동시에 보낼 수 있다. **quota 상향(용량/inode 확장)이나 만료 연장(`expires_at`)도 이
PATCH로** 처리한다.

| 필드 | 효과 | 백엔드 명령 |
|---|---|---|
| `quota` | 용량/inode quota 변경 | `mmsetquota`(GPFS) / `weka fs quota set`(WEKA) / `setfattr`(CephFS) |
| `owner_username` | 디렉토리 user 소유자 변경(그룹은 유지) | `chown {uid}` |
| `resource_type` | DMS DB 태그 변경(백엔드 변화 없음) | — |
| `expires_at` | 만료일 변경(백엔드 변화 없음) | — |

```bash
# (A) quota 상향
curl -sS "${CURL[@]}" \
  -X PATCH -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/filesystems/gpfs-a/project1" \
  -d '{"requester_id":"alice","payload":{"quota":{"capacity_bytes":107374182400,"file_count":2000000}}}' \
  | jq '{request_id, status}'

# (B) 소유자 변경 — LDAP에서 uidNumber 조회 후 chown (그룹 불변)
curl -sS "${CURL[@]}" \
  -X PATCH -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/filesystems/gpfs-a/project1" \
  -d '{"requester_id":"operator","payload":{"owner_username":"bob"}}' | jq
```

소유자 변경 결과는 응답 `verification_summary.owner_change`로 확인한다:
```json
{"owner_change": {"owner_username": "bob", "uid": 10002}}
```

> **그룹 멤버 변경은 PATCH로 지원하지 않는다.** LDAP의 `dms-grp-{dir}` 그룹을 직접 수정(`ldapmodify`)하거나
> create 시 `users`로 등록한다. 멤버 drift는 `:check`(§2.5)로 발견한다.

### 2.3 차단 / 차단 해제 (`:block` / `:initialize`)

`"block": true/false`는 엔드포인트가 내부적으로 주입하므로 payload에 넣지 않는다.

```bash
# 차단 (chmod 0000 — 모든 접근 불가, 현재 mode를 previous_mode로 저장)
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/filesystems/gpfs-a/project1:block" \
  -d '{"requester_id":"alice","payload":{}}' | jq '{request_id, status}'

# 차단 해제 (previous_mode로 복원 — 없으면 생성 시 mode, 기본 0750)
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/filesystems/gpfs-a/project1:initialize" \
  -d '{"requester_id":"alice","payload":{}}' | jq '{request_id, status}'
```

| 엔드포인트 | 처리 | 결과 |
|---|---|---|
| `:block` | `chmod 0000 {path}` | `d---------`(접근 불가) |
| `:initialize` | `chmod {previous_mode} {path}` | 이전 mode 복원(없으면 생성 시 mode, 기본 `0750`) |

> `system`/`admin` `resource_type`은 `:block` 명시 호출이 `resource_type_cannot_be_blocked`로 거부된다(§2.9).

### 2.4 삭제 (soft-delete)

데이터를 즉시 지우지 않고 안전하게 잠근다(보존 기본, 영구 삭제는 운영자 수동). 백엔드별로 세부 순서가 다르다.

```bash
curl -sS "${CURL[@]}" \
  -X DELETE -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/filesystems/gpfs-a/project1" \
  -d '{"requester_id":"alice","payload":{}}' | jq '{request_id, status}'
```

| Backend | soft-delete 동작(순서) | quota reset | 영구 삭제(운영자 수동) |
|---|---|---|---|
| **GPFS** | `chown root:root` → `chmod 000` → LDAP 그룹 삭제 | 안 함 | `mmunlinkfileset` + `mmdelfileset` |
| **WEKA** | `weka fs quota reset` → `chown root:root` → `chmod 000` → LDAP 그룹 삭제 | 함 | `rm -rf` |
| **CephFS** | `chown root:root` + `chmod 000` → LDAP 그룹 삭제 | 안 함 | `rm -rf` |

- 디렉토리/fileset과 데이터는 그대로 남고 **root만 접근 가능**(`d---------`). DMS DB 상태는 `Deleted`.
- `mmdelfileset`/`rm -rf` 같은 **파괴적 명령은 실행하지 않는다**. 영구 삭제는 위 표의 수동 절차로만.
- `management_mode: quota_only`나 import로 등록된 리소스는 **삭제 거부**(외부 자산 보호; planner `Rejected` +
  백엔드 precondition 재차 거부).

soft-delete된 리소스는 action-required에 `filesystem_soft_deleted`(INFO)로 뜨며, `recommended_action`에
백엔드별 영구 삭제 방법이 안내된다:

```bash
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/action-required" \
  | jq '[.[] | select(.issue_type=="filesystem_soft_deleted")
             | {resource_key, fileset_name, recommended_action}]'
```

### 2.5 정합성 점검 (`:check`)

DMS의 `desired_state`(quota / owner / mode / group 멤버십)와 **백엔드 + LDAP** 실제 상태를 대조한다.
**side-effect 없음**, drift는 `issues[]`로 보고.

```bash
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/filesystems/gpfs-a/project1:check" \
  -d '{"requester_id":"operator","payload":{}}' | jq '.verification_summary'
```

| 항목 | 비교 | drift issue_type |
|---|---|---|
| quota.capacity_bytes | 백엔드 read-back(100 MiB tolerance) | `filesystem_quota_drifted` |
| quota.file_count(GPFS) | 정확 비교 | `filesystem_quota_drifted` |
| 디렉토리 존재 | `mmlsfileset` / `test -d` | `filesystem_quota_missing` |
| ownership.uid/gid | `stat` vs LDAP uidNumber/gidNumber | `filesystem_owner_drifted` |
| permission.mode | `stat` vs `desired_state.mode` | `filesystem_mode_drifted` |
| access_group.members | LDAP `memberUid` ⊇ `desired_state.users` | `filesystem_group_membership_drifted` |

payload 옵션: `include_quota`(기본 `true`), `include_permission`(기본 `true`),
`record_action_required`(기본 `false` — drift를 action-required로도 게시).

**Capacity tolerance(전 백엔드 공통):** `observed < desired`는 항상 `Drifted`(under-applied, 위험);
`desired ≤ observed ≤ desired + 100 MiB`는 `Consistent`; 그 이상은 `Drifted`. GPFS는 `desired`를 8 MiB
단위로 올린 값과 비교한다(백엔드 내부 round-up 반영). `file_count`는 정수 카운터라 **정확 일치**를 요구한다.

Drifted 예시:
```json
{"quota_status": "Drifted",
 "issues": [{"issue_type":"filesystem_mode_drifted","field":"permission.mode",
             "desired":"0750","observed":"0700"}]}
```

### 2.6 Live state sync (`:sync`)

백엔드의 실제 quota를 읽어 DMS `desired_state.quota`를 그 값에 맞춘다. `:check`가 drift를 보고만 하는 데
반해 `:sync`는 DMS 기록을 백엔드 진실에 정렬한다. **백엔드 quota는 건드리지 않는다.**

```bash
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/filesystems/gpfs-a/project1:sync" \
  -d '{"requester_id":"operator","payload":{}}' | jq '{request_id, status}'
```

payload 옵션: `source`(기본 `live`, 현재 `live`만), `include_quota`(기본 `true`). WEKA는 file_count 미지원이라
`capacity_bytes`만 갱신된다.

### 2.7 외부 fileset/디렉토리 채택 (`:import`)

외부에서 이미 만든 fileset(GPFS) 또는 디렉토리(WEKA/CephFS)를 `management_mode: full`로 채택한다. `stat`으로
현재 gid를 읽어 그룹을 자동 발견하므로 호출자가 `users`/`expected_group`을 명시할 필요는 없다(선택).

| 현재 그룹 | 동작 |
|---|---|
| `dms-grp-*`(이미 DMS 명명규약) | 그대로 채택. LDAP `memberUid`를 `desired_state.users`로 등록, chown 안 함 |
| 그 외 외부 그룹 | `dms-grp-{directory_name}` 새로 생성, 외부 그룹 멤버를 그대로 옮기고 디렉토리를 새 gid로 chown |

```bash
# 사전: 외부에서 fileset/디렉토리 생성 + link/chgrp 완료 (install 문서 참고)
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/filesystems/gpfs-a/import-bar:import" \
  -d '{
    "requester_id": "operator",
    "payload": {"expires_at": "2099-01-01T00:00:00Z", "quota": {"capacity_bytes": 1073741824}}
  }' | jq '.verification_summary.group_adoption'
```

응답 `group_adoption` 예시(외부 그룹 → 새 dms-grp 생성):
```json
{"live_group":"ext-team-foo","live_gid":8500001,
 "new_group":"dms-grp-import-foo","new_gid":9000006,
 "adopted_members":["alice","bob"],"changed_group":true,
 "mode":"create_dms_group_from_external"}
```

payload 필드: `expires_at`(생략 시 1년 후), `quota`(선택), `import_mode`(`full`),
`access_policy.expected_group`/`expected_mode`/`users`(선택 검증 hint).

### 2.8 Quota만 적용 (`:assign-quota`)

외부 디렉토리에 **quota만** 건다. DMS는 그룹/소유권/mode를 건드리지 않으며 `management_mode: quota_only`로
표시된다 → **이후 DELETE는 거부**(외부 자산 보호).

```bash
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/filesystems/gpfs-a/import-quota-only:assign-quota" \
  -d '{
    "requester_id": "operator",
    "payload": {"expires_at": "2099-01-01T00:00:00Z",
                "quota": {"capacity_bytes": 4294967296, "file_count": 300000}}
  }' | jq '{request_id, status}'
```

payload 필드: `expires_at`(생략 시 1년), `quota`(**필수**, `capacity_bytes` 필요; WEKA에 `file_count`를 주면
`BackendApplyFailed`), `management_mode`(`quota_only`, 다른 값 거부).

> **import vs assign-quota.** `import`는 LDAP 그룹/멤버십까지 DMS가 관리(full). `assign-quota`는 quota만
> 등록하고 외부 그룹/소유권을 손대지 않으며 DELETE를 거부한다.

### 2.9 resource_type 카테고리

리소스에 분류 태그를 붙인다. 만료 sweep / `:block`에서 카테고리별 보호 정책이 달라진다. 변경은 백엔드 명령
없이 DMS DB `desired_state.resource_type`만 갱신한다.

| 값 | 의미 | 만료 sweep 자동 차단 | `:block` 명시 호출 |
|---|---|---|---|
| `user`(기본) | 개인 작업 영역 | 대상 | 허용 |
| `project` | 프로젝트/팀 공유(분류용; user와 동일 동작) | 대상 | 허용 |
| `system` | 시스템 운영용 | 안 함(`resource_type_not_auto_blocked`) | 거부(`resource_type_cannot_be_blocked`) |
| `admin` | 관리자 전용 | 안 함 | 거부 |

create의 `resource_type` 필드나 PATCH로 지정/변경한다(§2.1, §2.2).

### 2.10 만료 일괄 차단 (`:expiration-sweep`)

만료된 모든 리소스를 일괄 차단한다. `scope.resource_type`으로 특정 카테고리만 좁힐 수 있고, `dry_run`으로
대상만 확인할 수 있다.

```bash
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/filesystems:expiration-sweep" \
  -d '{"requester_id":"sweeper","payload":{"scope":{"resource_type":"user"},"dry_run":true}}' | jq
```

payload: `scope`(예: `{"resource_type":"user"}`, `{"storage_name":"gpfs-a"}`), `before`(ISO8601 기준 시각),
`dry_run`(기본 `false`).

---

## 3. Operations 조회 (파일시스템 관련)

파일시스템 요청 결과를 확인하는 읽기 전용 조회다. 전체 operations 조회 API(인벤토리·work-summary·
action-required·control-state 등)는 [`operations.md`](operations.md) 참조.

### 3.1 Filesystem 조회

```bash
# 단건 (quota + 소유권 + 경로)
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/filesystems/gpfs-a/project1" \
  | jq '{directory_name, status, resource_type, quota, expires_at, users,
         access_group, mode, path, fileset_name}'

# storage별 전체 목록
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/filesystems/gpfs-a" | jq '[.[] | {directory_name, status, quota}]'

# 만료/만료 임박
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/filesystems/expiring?status=expired" | jq
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/filesystems/expiring?status=expiring&within_seconds=604800&brief=true" | jq
```

`/filesystems/expiring` 쿼리 파라미터:

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `status` | `expired` | `expired` / `expiring` / `all` |
| `storage_name` | (전체) | 특정 storage로 필터 |
| `within_seconds` | (없음) | `expiring`일 때 만료까지 남은 초 |
| `before` | (없음) | ISO8601 기준 시각 |
| `include_blocked` | `false` | 이미 차단된 리소스 포함 여부 |
| `limit` | `1000` | 반환 상한 |
| `brief` | `false` | `true`면 `recent_requests`/`last_block_*` 생략(대량 폴링 시 권장) |

### 3.2 Request 조회

```bash
# 단건 (전환 이력 포함) — mutating 요청 결과 폴링에 사용
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/requests/{request_id}" \
  | jq '{status: .request.status,
         results: [.results[]? | {terminal_status, error_category, message}],
         transitions: [.transitions[]? | {to_state, reason, actor}]}'

# requester별 목록 (requester_id 필수)
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/requests?requester_id=alice&limit=5" \
  | jq '[.[] | {request_id, operation, resource_key, status, requested_at}]'

# 날짜 범위 (YYYY-MM-DD 또는 ISO8601). 날짜만 주면 since=그날 00:00, until=다음 날 00:00으로 확장.
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/requests?requester_id=alice&since=2025-05-01&until=2025-05-31" \
  | jq '[.[] | {request_id, status, requested_at}]'
```

`/requests`는 `requester_id`가 **필수**다. 요청자와 무관하게 전체 활동(액티비티)을 뒤지려면
`/operations/request-activity`(offset 페이징 + `search`)를 쓴다 — [`operations.md`](operations.md) 참조.

### 3.3 Storage Mapping 조회

§1.3 참조(`/operations/storage-mappings`).

---

## 4. Stuck Request 해소 (`:resolve`)

`UnknownAfterSideEffect` 또는 `BackendApplyFailed` 상태의 request가 남아 있으면 동일 resource에 새 요청 시
`Conflict`가 난다. 백엔드 실제 상태를 확인한 뒤 request를 수동으로 종결한다.

**엔드포인트:** `POST /api/v1/resource-management/requests/{request_id}:resolve`

| resolution | 전환 상태 | 사용 시점 |
|---|---|---|
| `abandon` | `Failed` | side effect가 없었거나 수동 롤백을 마친 경우 |
| `succeeded` | `Succeeded` | 백엔드에서 직접 확인해 실제 성공 상태인 경우 |

```bash
# 1) stuck request 확인
curl -sS "${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/requests?requester_id=alice" \
  | jq '[.[] | select(.status | IN("UnknownAfterSideEffect","BackendApplyFailed"))
             | {request_id, status, resource_key}]'

# 2) 백엔드 실제 상태 확인 (GPFS fileset / LDAP group 등 — install/RUNBOOK 참고)

# 3) side effect 없음 → abandon
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/requests/{request_id}:resolve" \
  -d '{"resolution":"abandon","reason":"verified no fileset or LDAP group was created"}' | jq

# 3') 실제로 생성됐음을 확인 → succeeded
curl -sS "${CURL[@]}" \
  -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/requests/{request_id}:resolve" \
  -d '{"resolution":"succeeded","reason":"fileset confirmed linked at managed_root/<name>"}' | jq
```

응답:
```json
{"request_id":"req_...","previous_status":"UnknownAfterSideEffect","resolved_to":"Failed",
 "resolution":"abandon","actor":"operator","reason":"verified no fileset or LDAP group was created"}
```

에러: `422`(resolution 값 오류 또는 `reason` 누락), `404`(request 없음), `409`(이미 다른 terminal 상태이거나
resolve 불가 상태).

---

## 다음 문서

- 파일시스템 RM 설치·백엔드/LDAP/SSH/RBAC 사전 설정 → [`install/dms-03-rm-filesystem.md`](../../install/dms-03-rm-filesystem.md)
- 코어 배포(이미지·secret·control-plane·mTLS·ingress·migration) → [`install/dms-02-core.md`](../../install/dms-02-core.md)
- 환경변수 레퍼런스 → [`install/dms-06-configuration.md`](../../install/dms-06-configuration.md)
- Kubernetes 네임스페이스 quota RM API → [`resource-management-k8s.md`](resource-management-k8s.md)
- DM(scan/sync/rm) API → [`data-management.md`](data-management.md)
- 읽기 전용 operations 조회 전체 → [`operations.md`](operations.md)
- 운영 런북(정합성 점검·drift·stuck 정리 루틴) → [`../operations-runbook.md`](../operations-runbook.md)
- API 개요 + 인증 → [`README.md`](README.md)
