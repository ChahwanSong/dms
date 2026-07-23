# DMS 파일시스템 Resource Management 설정

파일시스템 백엔드(**CephFS / WekaFS / GPFS**)를 DMS의 RM(Resource Management)이 관리할 수 있도록
**배선(wiring)** 하는 설치 문서다. 스토리지 host-mount, RM 실행 경로(sudo/ssh), 백엔드별 LDAP 필수성,
RM Agent DaemonSet 배포, 스토리지 매핑 등록, 그리고 readiness가 `Ready`로 전환되는 조건까지 다룬다.

- **API 사용법(생성/변경/삭제·quota·block·check/sync·import 등)은 이 문서 범위가 아니다** →
  [`docs/api/resource-management-fs.md`](../docs/api/resource-management-fs.md).
- 클러스터/노드 사전 준비(스토리지 host-mount, 노드 NSS/SSSD)는 먼저
  [`dms-01-prerequisites.md`](dms-01-prerequisites.md)에서 끝냈다고 전제한다.
- 코어 배포(이미지, control-plane, LDAP env, mTLS, `dms-ssh-client`·`dms-agent-storages-sync` 등의 Secret/RBAC)는
  [`dms-02-core.md`](dms-02-core.md)에서 끝냈다고 전제한다.
- 모든 환경변수의 전체 목록·기본값은 [`dms-06-configuration.md`](dms-06-configuration.md).

> **placeholder 규약.** 아래 값(`registry.example.internal`, `cluster-a`, `/cephfs`,
> `dc=example,dc=internal`, `node1`…)은 모두 예시다. 실제 환경 값으로 치환한다.

---

## 1. 백엔드별 설정 개요

| 항목 | CephFS | WekaFS | GPFS |
|---|---|---|---|
| host-mount 경로(예) | `/cephfs` | `/weka` | `/gpfs` |
| RM 실행 경로 결정 | `DMS_FILESYSTEM_MUTATION_MODE` (env) | mapping `command_runner` | mapping `command_runner` |
| 추가 CLI 요구 | `python3` | `python3` + `weka` (인증 필요) | `mm*` (fileset/quota) |
| **LDAP bind** | **필수(eager)** | **필수(eager)** | 선택(운영은 구성 권장) |
| 등록 필수 필드 | `managed_root` | `managed_root` | `managed_root` + `filesystem_name` |
| `file_count`(inode) quota | 지원 | **미지원**(주면 실패) | 지원 |

이하 공통 설정(2~3장)을 먼저 맞춘 뒤, 백엔드별 추가 설정(4장), LDAP(5장), Agent(6장), 매핑 등록(7장) 순서로 진행한다.

---

## 2. 스토리지 host-mount (전 백엔드 공통)

RM Agent가 마운트 존재를 관측해 readiness를 판정하고, RM Worker가 그 노드(또는 `ssh_host`)에서
디렉토리/quota 연산을 실행한다. 따라서 각 백엔드 스토리지는 **매핑의 `rm_worker_nodes`에 나열될 모든 노드에
같은 절대 경로로 read-write host-mount** 되어 있어야 한다(fstab 등, [`dms-01-prerequisites.md`](dms-01-prerequisites.md)).
마운트가 없으면 그 노드의 마운트 프로브가 `Missing`으로 남아 `resource_management` readiness가 서지 않는다.

---

## 3. RM 실행 경로: `local` vs `ssh-host-exec` + NOPASSWD sudo

RM Worker가 filesystem mutation(mkdir/chown/chmod/quota/`mm*`)을 **어디서** 실행할지는 두 경로로 갈린다.

| mode | 동작 | 언제 |
|---|---|---|
| `local` | RM Worker **파드/노드 자신**에서 직접 실행(스토리지가 그 노드에 host-mount 돼 있어야 함) | RM Worker가 스토리지를 직접 마운트한 배치 |
| `ssh-host-exec` | RM Worker가 `ssh {ssh_host} sudo python3 -c …` 로 **백엔드 호스트에서** 실행 | 스토리지가 별도 백엔드/매니저 노드에만 있는 배치 |

- **CephFS**: `install/kubernetes/control-plane.yaml` → ConfigMap `dms-runtime-config` →
  `DMS_FILESYSTEM_MUTATION_MODE`(기본 `ssh-host-exec`)로 정한다.
- **GPFS / WekaFS**: 스토리지 매핑의 `backend_template.command_runner`(기본 `ssh-host-exec`)로 정한다(7장).

두 경로 모두 실제 명령은 **root 권한**이 필요하다. `DMS_FILESYSTEM_EXEC_USE_SUDO`(control-plane.yaml
ConfigMap `dms-runtime-config`, 기본 `true`)이면 명령이 `sudo`로 감싸진다. 접속/실행 계정이 root가 아니면
(전용 `dms-svc` ssh 유저나 `local` 모드의 비-root 런타임) **NOPASSWD sudo가 반드시 필요**하다 — 비대화형
실행이라 암호 프롬프트가 뜨면 그대로 실패한다. ssh `User`가 root면 sudo는 사실상 no-op이지만, `use_sudo=true`인
한 `sudo` 바이너리는 있어야 한다.

### 3.1 NOPASSWD sudoers (실행 호스트에서)

`local`이면 RM Worker 노드, `ssh-host-exec`이면 `ssh_host`의 접속 계정에 등록한다(`visudo`):

```
# /etc/sudoers.d/dms  — 접속/실행 계정 예: dms-svc
dms-svc ALL=(root) NOPASSWD: /usr/bin/python3, /usr/bin/chown, /usr/bin/chmod, /usr/bin/setfattr, \
  /usr/lpp/mmfs/bin/mmcrfileset, /usr/lpp/mmfs/bin/mmlinkfileset, /usr/lpp/mmfs/bin/mmsetquota, \
  /usr/lpp/mmfs/bin/mmlsfileset, /usr/lpp/mmfs/bin/mmunlinkfileset, /usr/lpp/mmfs/bin/mmdelfileset
```

- **CephFS/WekaFS**는 mkdir/chown/chmod/get·setfattr를 `python3` 스크립트 하나로 수행하므로 최소 `python3`
  (WekaFS quota는 추가로 `weka` CLI)만 있으면 된다.
- **GPFS**는 위 `mm*` 명령이 추가로 필요하다.
- 폭을 넓히기 싫으면 바이너리를 열거한다(원하면 `NOPASSWD: ALL`로 단순화 가능하나 최소권한 권장).

### 3.2 ssh-client key + 사전 등록 known_hosts (`ssh-host-exec`일 때)

RM Worker는 코어에서 만든 `dms-ssh-client` Secret의 private key로 `ssh_host`에 접속한다. 성립 조건:

- `ssh_host`의 접속 계정(Secret의 ssh `config`의 `User`, 기본 `root`) `authorized_keys`에 RM Worker의
  **public key** 등록.
- Secret의 ssh `config`가 **`StrictHostKeyChecking yes`** 이므로 `ssh_host`의 host key가 Secret의
  `known_hosts`에 **미리** 있어야 한다(없으면 접속 거부). `ssh-keyscan {ssh_host}` 출력을 넣는다.
  `ssh_host`를 IP로 지정했으면 known_hosts도 **같은 IP 표기**로 등록한다(hostname↔IP 표기가 일치해야 매칭).

```bash
# 각 ssh_host의 host key 수집 → dms-ssh-client Secret의 known_hosts 로 반영(코어 문서 참조)
ssh-keyscan -H node1 node2 node3 > /opt/dms-secrets/dms-known_hosts
```

> 보안: 스토리지 매핑 등록 권한만으로 임의 호스트에 mutation을 보낼 수 없다 — 실제 도달은 known_hosts +
> authorized_keys(별도 cluster-admin 작업)로 제한된다.

---

## 4. 백엔드별 추가 설정

### 4.1 CephFS

- **실행 경로**: `DMS_FILESYSTEM_MUTATION_MODE`(3장). CephFS는 host-mount된 노드에서 `python3` 스크립트로
  mkdir/chown/chmod/get·setfattr(quota는 `setfattr ceph.quota.max_bytes`)를 수행한다.
- **managed_root**: 사전 생성하지 않아도 DMS가 처음 만들 때 **`0711`** 로 생성한다(이미 있으면 운영자가 정한 권한 유지).
- **LDAP**: **필수**(5.2 참조).

### 4.2 WekaFS

- **실행 경로**: 매핑 `command_runner`(3장).
- **quota CLI 인증(필수)**: `weka fs quota set/list/reset` CLI는 cluster 인증이 필요하다 — `weka user login`
  또는 실행 호스트 환경의 `WEKA_USERNAME`/`WEKA_PASSWORD`/`WEKA_ORG`. 미인증 상태에서 quota 작업 시
  `BackendPreconditionError: WekaFS quota CLI not authenticated`. 멀티 클러스터면 매핑에 `weka_profile`
  (`weka --profile <name>`) 지정.
- **`file_count` 미지원**: WEKA path quota는 `capacity_bytes`만 지원한다. `file_count`(inode quota)를 주면
  조용히 무시가 아니라 `BackendApplyFailed`로 **거절**한다.
- **managed_root**: 운영자가 **사전 생성**하고 `0711`로 만든다.
- **LDAP**: **필수**(5.2 참조).

### 4.3 GPFS

GPFS는 `mm*` 명령을 `ssh-host-exec`로 매니저 노드에서 실행하는 것이 일반적이다. 최초 1회 아래를 준비한다.

**(a) per-fileset quota 활성화** — 비활성 시 `BackendApplyFailed`:

```bash
ssh gpfs-node1 "sudo /usr/lpp/mmfs/bin/mmchfs gpfs0 --perfileset-quota"
ssh gpfs-node1 "sudo /usr/lpp/mmfs/bin/mmlsfs gpfs0 --perfileset-quota -Y | grep perfileset"   # perfilesetQuotas:yes
```

**(b) managed_root(fileset junction 부모) 사전 생성 — 권한 `0711`** (world-traversable, not world-listable;
임의 uid 소유자가 자기 디렉토리로 `cd`는 되되 `ls`로 이름은 노출 안 되게):

```bash
ssh gpfs-node1 "sudo mkdir -p /gpfs/dms && sudo chmod 0711 /gpfs/dms"
```

**(c) SSH PATH** — non-login shell엔 `/usr/lpp/mmfs/bin`이 PATH에 없지만 코드가 `PATH=/usr/lpp/mmfs/bin:$PATH`를
자동 주입하므로 심볼릭 링크 불필요.

**(d) 등록 필수 필드** — `managed_root` + **`filesystem_name`**(대상 GPFS device, 예 `gpfs0`; `mm*` 명령 대상).
둘 중 하나라도 없으면 매핑 등록이 `422`로 거부된다(7장).

**(e) LDAP**: **선택**(미구성이면 owner/access-group 없이 폴백). 단 owner·그룹 기능을 쓰려면 결국 필요하므로
운영은 항상 구성한다.

> GPFS block quota는 내부적으로 **8 MiB 단위로 올림**된다(DMS가 set 값을 사전 정렬). read-back 비교는 모든
> 백엔드 공통 **+100 MiB tolerance**를 둔다.

---

## 5. 디렉토리 서비스(LDAP) — 백엔드별 필수성

Filesystem RM은 LDAP을 두 가지로 쓴다: (1) **identity 해석** — `owner_username`/`users`/`requester_id`를
POSIX 유저명(`uid`)으로 보고 `uidNumber`를 조회, (2) **access group 관리** — 리소스마다 `dms-grp-{dir}` 그룹을
생성/멤버 등록/삭제. 기존 회사 디렉토리(LDAP/AD)를 그대로 가리키면 되고 DMS 전용 LDAP을 새로 세울 필요는 없다.

### 5.1 DMS_LDAP_* 설정 위치

- 비-민감 값: `install/kubernetes/control-plane.yaml` → ConfigMap `dms-runtime-config` — `DMS_LDAP_URI`,
  `DMS_LDAP_BASE_DN`, `DMS_LDAP_USER_SEARCH_BASE`, `DMS_LDAP_GROUP_SEARCH_BASE`,
  `DMS_LDAP_GROUP_GID_START`/`END`(필요 시), `DMS_LDAP_USER_FILTER`(AD면 스키마에 맞춤).
- bind 자격증명: `dms-secrets` Secret — `DMS_LDAP_BIND_DN`, `DMS_LDAP_BIND_PASSWORD`.

값의 배선은 [`dms-02-core.md`](dms-02-core.md)에서, 전체 변수·기본값은 [`dms-06-configuration.md`](dms-06-configuration.md)에서 다룬다.

### 5.2 [load-bearing] 백엔드별 bind 요구

- **CephFS·WekaFS = LDAP bind 필수(eager).** 어댑터가 생성 시점에 `DMS_LDAP_URI`/`DMS_LDAP_BIND_DN`/
  `DMS_LDAP_BIND_PASSWORD`로 group manager를 구성하며, 하나라도 없거나 틀리면 **그 백엔드의 모든**
  create/patch/delete/block/import 연산이 `IdentityLookupConfigurationError`로 실패한다(그룹을 건드리지
  않는 연산도 동일). **즉 CephFS/WekaFS를 쓰면 LDAP은 선택이 아니라 필수 전제조건이다.**
- **GPFS = 선택.** LDAP 미구성이면 owner/group 없이 폴백한다(운영은 항상 구성).

### 5.3 GID 대역 + DMS의 쓰기 범위

DMS가 LDAP에 하는 쓰기는 **access group에 한정**된다(코드로 강제): `ou=groups` 아래 `cn=dms-*` 그룹의
생성/`memberUid` 수정/삭제만 하고, **사용자 계정은 만들지 않는다**(`users`/`owner_username`은 디렉토리에 이미
존재하는 실제 계정이어야 함). 그룹 GID는 `DMS_LDAP_GROUP_GID_START`~`END` 범위에서 할당되므로 기존 시스템
GID와 **충돌하지 않는 전용 대역**으로 잡는다(예 `9000000`~`9999999`). bind 계정에는 이 범위(그룹 서브트리에
`dms-*` add/modify/delete)만 부여하면 충분하다.

### 5.4 노드 신원 해석 (NSS/SSSD) — 사전 필수

RM은 `dms-grp-*` 그룹/멤버십을 만든 뒤 소유권·접근을 검증한다. 따라서 **RM 워커/백엔드 노드가 LDAP
사용자·그룹을 NSS로 해석**할 수 있어야 한다(SSSD 또는 동등한 연동; 클러스터 사전 준비는
[`dms-01-prerequisites.md`](dms-01-prerequisites.md)).

**교차노드 캐시 일관성.** RM은 그룹/멤버를 만든 뒤 **작업이 실제 실행된 executor 노드 1대에서만**
`sss_cache -E`로 SSSD 캐시를 무효화한다. 나머지 노드는 자기 캐시가 만료될 때까지 새 멤버십을 못 볼 수 있다.
→ 대응: **모든 backend 노드 SSSD의 `entry_cache_timeout`을 짧게(예 60초) 낮춘다**(기본 90분). 다중 SSSD
도메인이면 `dms-*` 그룹을 서빙하는 도메인 섹션(`[domain/...]`)의 값을 조정한다.

---

## 6. RM Agent DaemonSet 배포

파일시스템 스토리지의 `resource_management` readiness는 **RM Agent**가 각 노드의 마운트를 관측해 채운다.
매니페스트: `install/kubernetes/agent-daemonset.yaml`(DaemonSet `dms-rm-agent`).

### 6.1 이미지 = plain `dms`

`dms-rm-agent`는 **plain `dms` 이미지**로 충분하다(RM readiness = 마운트 + can-i이고 mpifileutils 툴을
검사하지 않는다). `agent-daemonset.yaml` → DaemonSet `dms-rm-agent` →
`spec.template.spec.containers[0].image`를 **코어에서 push한 실제 `dms` ref**로 바꾼다
(`registry.example.internal/dms:CHANGE_ME` 치환). ConfigMap `dms-agent-runtime-config`에서:

- `DMS_AGENT_CLUSTER_NAME` = `cluster-a`(control cluster 이름과 일치)
- `DMS_AGENT_API_URL` = **내부 전용 Service** `http://dms-api-internal.dms.svc.cluster.local`(에이전트는
  mTLS 인그레스가 아니라 전용 내부 API `dms-api-internal`로 보고한다 — mTLS off + Secret
  `dms-agent-secrets`의 `DMS_AUTH_SHARED_TOKEN` + NetworkPolicy(agent 파드만)로 제한; 근거·프로필은
  [`dms-06-configuration.md §8`](dms-06-configuration.md)·[`dms-02-core.md`](dms-02-core.md))
- `DMS_AGENT_MOUNTINFO_PATH` = `/host/proc/1/mountinfo`, `DMS_AGENT_HOST_ROOT` = `/host` (아래 6.3)

> DM용 `dms-dm-agent`는 이미지가 다르다(mpifileutils를 얹은 `dms-agent`). DM 설정은
> [`dms-05-dm-jobs.md`](dms-05-dm-jobs.md).

### 6.2 storages ConfigMap 자동 동기화 (`dms-agent-storages-sync` RBAC)

에이전트는 어떤 스토리지의 마운트를 프로브할지 ConfigMap `dms-agent-storages`(`storages.json`)에서 읽는다.
매니페스트의 값은 **bootstrap seed일 뿐**이고, 스토리지 매핑을 등록/수정/삭제(7장)할 때마다 **dms-api가 이
ConfigMap을 등록된 매핑으로 덮어쓴다**(각 `storages` 항목의 `mount_paths`에 **호스트 마운트 지점**이 채워짐).

이 덮어쓰기에는 코어에서 적용한 Role/RoleBinding **`dms-agent-storages-sync`**(control-plane.yaml,
`configmaps` `dms-agent-storages`에 get/update/patch, `dms-api`+`dms-remote`에 바인딩)가 필요하다. **없으면
patch가 `Forbidden`인데 코드가 그 예외를 삼켜(warning 로그만) ConfigMap이 조용히 갱신되지 않는다** → 새로
등록한 파일시스템 스토리지가 에이전트에 전달되지 않아 마운트 readiness가 `Missing`으로 남고, planner가
파일시스템 RM을 `missing_rm_readiness`, DM을 `no_ready_dm_candidate`로 거부한다. 존재 확인:

```bash
kubectl -n dms get role,rolebinding dms-agent-storages-sync
# 동기화 결과 확인
kubectl -n dms get configmap dms-agent-storages -o jsonpath='{.data.storages\.json}' | jq '.storages[].storage_name'
```

### 6.3 마운트 readiness = 호스트 mountinfo bind-mount (필수)

에이전트는 컨테이너에서 돌므로 기본 `/proc/self/mountinfo`로는 **컨테이너 자신의 마운트만** 보여 워커 노드의
스토리지 마운트가 안 보인다 → **모든 storage가 Missing**, readiness `false`. `agent-daemonset.yaml`은 이를
해결해 두었다(그대로 유지):

- 호스트 `/`를 `/host`로, 호스트 `/proc/1/mountinfo`를 `/host/proc/1/mountinfo`로 bind-mount
  (`/proc`은 별도 마운트라 `/host` 아래로 안 딸려오므로 **따로** 마운트).
- ConfigMap `dms-agent-runtime-config` → `DMS_AGENT_MOUNTINFO_PATH: /host/proc/1/mountinfo`가 그 경로를 가리킴.

이러면 에이전트가 호스트의 실제 마운트를 읽어 `Ready` + rw/ro를 추론한다(per-storage hostPath 개별 마운트 불필요).

### 6.4 배포 + 매핑 변경 후 rollout-restart

```bash
kubectl apply -f install/kubernetes/agent-daemonset.yaml
kubectl -n dms rollout status daemonset/dms-rm-agent --timeout=180s
```

에이전트는 `storages.json`을 **시작 시 한 번만** 읽는다. 따라서 스토리지 매핑을 등록/수정한 뒤에는 DaemonSet을
재시작해 새 목록을 다시 읽게 한다:

```bash
# API로(권장) — RM·DM 에이전트 모두 restartedAt 스탬프. 토큰은 기본 필수 → Bearer도 함께.
curl -sS --cert $CERTS/operator.crt --key $CERTS/operator.key --cacert $CERTS/dms-server-ca.crt \
  -H "authorization: Bearer $DMS_TOKEN" \
  -X POST "$DMS_API_URL/api/v1/agent/rollout-restart"
# 또는 kubectl 직접
kubectl -n dms rollout restart daemonset/dms-rm-agent
kubectl -n dms rollout status  daemonset/dms-rm-agent --timeout=180s
```

---

## 7. 스토리지 매핑 등록

스토리지 매핑은 **DMS API로 등록**한다(DB가 source of truth; 별도 파일 유지 안 함). 등록·수정·삭제·조회의
**전체 필드/CRUD/제약은** [`docs/api/resource-management-fs.md`](../docs/api/resource-management-fs.md)에 있다 —
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
| `backend_type` | 필수 | `cephfs` / `wekafs` / `gpfs` |
| `cluster_name` | 필수 | DMS 클러스터 이름 |
| `mount_path` | 필수 | 각 노드에 마운트된 절대 경로 |
| `managed_root` | **필수** | DMS가 관리하는 루트(반드시 `mount_path` 하위). 생략 시 등록 `422` |
| `filesystem_name` | **gpfs 필수** | 대상 device(예 `gpfs0`). WEKA는 선택(생략 시 `storage_name`) |
| `rm_worker_nodes` | 권장 | RM 대상 노드 목록. 에이전트가 이 노드들의 마운트 증거로 `rm_readiness` 판정 |
| `ssh_host` | 선택 | `ssh-host-exec`로 접속할 노드. 생략 시 Ready인 `rm_candidates`에서 자동 선택 |
| `command_runner` | gpfs/weka | `ssh-host-exec`(기본) 또는 `local` (CephFS는 대신 `DMS_FILESYSTEM_MUTATION_MODE`) |
| `csi_driver` | 선택 | PVC provisioning CSI. 생략 시 `csi_driver_matches` sanity 제외 |

### 7.1 CephFS

```bash
curl -sS "${CURL_MTLS[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings" \
  -d '{
    "storage_name": "cephfs-a",
    "backend_template": {
      "backend_type": "cephfs",
      "cluster_name": "cluster-a",
      "mount_path": "/cephfs",
      "managed_root": "/cephfs/dms",
      "rm_worker_nodes": ["node1","node2","node3"],
      "ssh_host": "node1",
      "csi_driver": "rook-ceph.cephfs.csi.ceph.com"
    },
    "cluster_name": "cluster-a",
    "storage_class_name": "rook-cephfs"
  }' | jq '{storage_name, status}'
```

### 7.2 WekaFS

```bash
curl -sS "${CURL_MTLS[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings" \
  -d '{
    "storage_name": "weka-a",
    "backend_template": {
      "backend_type": "wekafs",
      "cluster_name": "cluster-a",
      "filesystem_name": "weka0",
      "mount_path": "/weka",
      "managed_root": "/weka/dms",
      "rm_worker_nodes": ["node2","node3"],
      "ssh_host": "node2",
      "command_runner": "ssh-host-exec",
      "csi_driver": "csi.weka.io"
    },
    "cluster_name": "cluster-a",
    "storage_class_name": "weka-sc"
  }' | jq '{storage_name, status}'
```

### 7.3 GPFS

```bash
curl -sS "${CURL_MTLS[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/resource-management/storage-mappings" \
  -d '{
    "storage_name": "gpfs-a",
    "backend_template": {
      "backend_type": "gpfs",
      "cluster_name": "cluster-a",
      "filesystem_name": "gpfs0",
      "mount_path": "/gpfs",
      "managed_root": "/gpfs/dms",
      "rm_worker_nodes": ["node4","node5","node6"],
      "ssh_host": "gpfs-node1",
      "command_runner": "ssh-host-exec"
    },
    "cluster_name": "cluster-a"
  }' | jq '{storage_name, status}'
```

> 등록 직후엔 `data_management: Missing`이 정상이다(DM 축은 아직 미구성). RM agent를 배포/재시작하기 전이면
> `resource_management`도 `Missing`일 수 있다 → 6.4의 rollout-restart 후 8장 조건을 확인한다.

---

## 8. readiness가 `Ready` 되는 조건 (체크리스트)

파일시스템 스토리지의 `resource_management` readiness가 `Ready`가 되려면:

1. **스토리지 매핑 등록됨** — `managed_root` 명시(+ GPFS는 `filesystem_name`).
2. **`dms-agent-storages` ConfigMap 동기화됨** — `dms-agent-storages-sync` RBAC 존재(6.2). 매핑 등록 후
   해당 `storage_name`이 ConfigMap `storages.json`에 나타나야 한다.
3. **RM Agent가 `rm_worker_nodes`에서 Running** 이고 매핑 등록/변경 후 **rollout-restart** 됨(6.4).
4. **호스트 mountinfo bind-mount 활성**(6.3) — 에이전트가 실제 마운트를 관측.
5. **스토리지가 그 노드들에 rw로 host-mount** 됨(2장).

RM **연산이 실제로 성공**하려면 여기에 더해: `ssh_host` 도달성 + NOPASSWD sudo(3장), CephFS/WekaFS는 **LDAP
bind**(5.2), 노드 **NSS/SSSD**(5.4), WekaFS는 **quota CLI 인증**(4.2), GPFS는 **per-fileset quota +
managed_root 0711**(4.3)이 갖춰져야 한다.

확인:

```bash
curl -sS "${CURL_MTLS[@]}" "$DMS_API_URL/api/v1/operations/storage-mappings/cephfs-a" \
  | jq '{storage_name, sanity_status, readiness}'
# resource_management: Ready, inventory: Ready 확인 (data_management는 DM 구성 후 Ready)
```

에이전트 프로브 단독 확인:

```bash
POD=$(kubectl -n dms get pods -l app.kubernetes.io/name=dms-rm-agent -o jsonpath='{.items[0].metadata.name}')
kubectl -n dms exec "$POD" -- dms agent-probe --once | jq '.mounts[] | {storage_name, status}'
# 노드에 실제 마운트된 storage만 Ready, 나머지는 Missing이면 정상
```

---

## 다음 문서

- [`dms-04-rm-k8s-quota.md`](dms-04-rm-k8s-quota.md) — Kubernetes 네임스페이스 쿼터 RM 설정
- [`dms-05-dm-jobs.md`](dms-05-dm-jobs.md) — DM(데이터 잡: scan/sync/rm) 설정 (DM Agent 이미지·신원·Volcano)
- [`dms-06-configuration.md`](dms-06-configuration.md) — 환경변수 레퍼런스(LDAP·filesystem exec·agent 등)
- [`docs/api/resource-management-fs.md`](../docs/api/resource-management-fs.md) — 파일시스템 RM **API 사용법**(생성/변경/삭제·quota·block·check/sync·import·매핑 CRUD)
- 되돌아가기: [`dms-02-core.md`](dms-02-core.md) (control-plane·LDAP env·mTLS·Secret/RBAC), [`dms-01-prerequisites.md`](dms-01-prerequisites.md) (host-mount·NSS/SSSD)
