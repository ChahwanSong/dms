# DMS 설치 — DM(데이터 잡) 설정

DMS의 **DM(Data Management) 잡** — `scan` / `sync` / `rm` — 을 켜는 설치 단계다. DM은 RM과
단일 컨트롤플레인(API·planner·operational DB·요청 수명주기)을 공유하되, **실행은 Volcano
네이티브 Job**으로 분리된다: planner가 요청을 `data_job`으로 만들고, `dms-dm-worker`가
Volcano 잡(launcher 1 + worker N, mpifileutils MPI)으로 `dsync`/`dscan`/`drm`을 요청자의
**POSIX 신원(uid/gid)** 으로 실행한다.

> **이 문서는 설치 관점만** 다룬다. 요청 페이로드·preview/confirm·옵션·응답 등 **API 사용법은
> [docs/api/data-management.md](../docs/api/data-management.md)**.
>
> **선행 문서**: 클러스터 프리렉(Volcano·Queue·PriorityClass·PodSecurity·공유 FS·NSS/SSSD·
> host-mount)은 **[dms-01-prerequisites.md](dms-01-prerequisites.md)**, 코어 배포(이미지·secret·
> control-plane·mTLS·ingress·migration)는 **[dms-02-core.md](dms-02-core.md)** 에서 이미 끝냈다고
>가정한다. 여기서는 그 위에서 **DM을 켜는 DMS 측 단계**에 집중한다.

DM은 프리렉이나 아래 단계가 **하나라도 빠지면 잡이 조용히 미실행**된다(에러 없이 큐에 머물거나
preflight에서 rejected). "정상인데 안 돈다"의 대부분은 이 체크리스트의 누락이다.

---

## 0. 활성화 체크리스트 (한눈에)

| # | 단계 | 바꾸는 파일 : 키 | 빠지면 |
|---|---|---|---|
| 1 | 클러스터 프리렉 | → [dms-01](dms-01-prerequisites.md) (Volcano·`dms-data` 큐·`dms-low/normal/high` PriorityClass·PSA=privileged·공유 FS·NSS/SSSD·host-mount) | 잡 Pending / 파드 admission 거부 / 후보 전무 |
| 2 | 스케줄러 백엔드 | `control-plane.yaml` → CM `dms-runtime-config` → `DMS_DM_SCHEDULER_BACKEND: volcano-job` | `auto`면 매 잡마다 MPIJob 시도→실패 후 폴백 |
| 3 | DM job 이미지 | 빌드·push 후 `control-plane.yaml` → CM → `DMS_DM_JOB_IMAGE: <실제 ref>` | `:CHANGE_ME`면 job pod `ImagePullBackOff` |
| 4 | dms-agent 이미지 | 빌드·push 후 `agent-daemonset.yaml` → DaemonSet `dms-dm-agent` → `image:` | plain `dms` 이미지면 `missing_dscan/dsync/drm_tool` |
| 5 | 노드 신원 프로빙 | `agent-daemonset.yaml` → `dms-dm-agent` → env `DMS_AGENT_IDENTITY_USERS` + 노드 NSS/SSSD | `identity_not_ready_on_node` |
| 6 | LDAP 신원 해석 | `control-plane.yaml` → CM + Secret `dms-secrets` → `DMS_LDAP_*` | `ldap_not_configured` / `ldap_unavailable` |
| 7 | dm-worker 활성화 | `control-plane.yaml` → Deployment `dms-dm-worker` → `replicas: 1` | `0`이면 잡을 아무도 claim 안 함(영구 대기) |
| 8 | 공유 artifact FS | `control-plane.yaml` → CM `DMS_DM_ARTIFACT_BASE_URI` + `dms-dm-worker` hostPath `dm-artifacts.path` | dm-worker가 summary 못 읽음 / 멀티노드 rank-script 공유 실패 |
| 9 | DM RBAC | `control-plane.yaml`(내장) + `kubectl apply` `dms-api-volcano-rbac.yaml`(별도) | `no_ready_dm_candidate` / 로그 tail Forbidden |
| 10 | DM 정책 | `control-plane.yaml` → CM `DMS_DM_POLICY_*` (또는 DMS API) | 노드/프로세스 수·큐가 의도와 다름 |

> **MPI Operator는 설치하지 않는다.** DMS는 Volcano 네이티브 Job(`batch.volcano.sh`)만 만들고,
> Volcano의 `ssh`/`svc` plugin이 launcher↔worker를 gang-schedule + 배선하므로 Volcano 단독으로
> MPI 워커가 뜬다. Kubeflow MPI Operator / MPIJob / `kubeflow.org` / `auto` 백엔드는 쓰지 않는다.

---

## 1. 스케줄러 백엔드 = `volcano-job`

`install/kubernetes/control-plane.yaml` → ConfigMap `dms-runtime-config`:

```yaml
DMS_DM_SCHEDULER_BACKEND: "volcano-job"   # 매니페스트 기본. 절대 "auto"로 바꾸지 말 것
```

`volcano-job`은 DM 잡을 **Volcano 네이티브 Job만** 생성한다. `auto`는 Kubeflow MPIJob
(`kubeflow.org/v2beta1`)을 **먼저** 시도해, MPI Operator가 없으면 **매 잡마다** apply가 실패한
뒤 폴백하므로 쓰지 않는다. 매니페스트 기본값이 이미 `volcano-job`이니 그대로 두면 된다.

---

## 2. 이미지 빌드 (3종 — 순서가 중요)

DM은 이미지 두 개를 **추가로** 빌드해야 한다. `plain dms` 이미지는 [dms-02](dms-02-core.md)에서
이미 빌드·push 했다고 가정한다(dms-api·planner·rm-worker·dm-worker·retention·sanity + dms-rm-agent
가 이걸 쓴다). 빌드 컨텍스트는 **repo 루트**.

```
(dms-02) plain dms 이미지  ─┐
                            ├─▶ ② dms-agent 이미지 (DMS_IMAGE=plain, MFU_IMAGE=job)
① DM job 이미지  ───────────┘
```

### 2.1 DM job 이미지 → `DMS_DM_JOB_IMAGE`

mpifileutils(`dsync/dcp/dscan/drm/nsync`) + Open MPI `mpirun` + OpenSSH client/server가 든
**job pod 실행 이미지**다.

```bash
docker build -f install/docker/Dockerfile.mpifileutils \
  -t registry.example.internal/dms-mpifileutils:v1 .
docker push registry.example.internal/dms-mpifileutils:v1
```

그런 다음 `control-plane.yaml` → ConfigMap `dms-runtime-config`:

```yaml
DMS_DM_JOB_IMAGE: "registry.example.internal/dms-mpifileutils:v1"   # ← push한 실제 ref로
# DMS_DM_JOB_IMAGE_REF: "..."   # (선택) mpifileutils git tag/commit — provenance 기록용일 뿐, pull과 무관
```

> ⚠️ **`:CHANGE_ME` 트랩.** 매니페스트 기본값은 `registry.example.internal/dms-mpifileutils:CHANGE_ME`
> 이다. 이 값은 **truthy라서 fail-closed 되지 않는다** — DMS는 정상으로 보고 잡을 만들지만 job pod가
> `ImagePullBackOff`로 죽는다. **반드시 실제 push한 ref로 교체**할 것. `DMS_DM_JOB_IMAGE_REF`는
> provenance 메타데이터일 뿐이라 남겨둬도 스케줄링에 무관하다.

### 2.2 dms-agent 이미지 → `dms-dm-agent` DaemonSet

`dms-dm-agent`(DM 노드 prober)는 **plain `dms` 이미지가 아니라 dms-agent 이미지**를 써야 한다.
agent가 `shutil.which()`로 `dscan/dsync/drm` 존재를 확인하는데, plain `dms` 이미지엔 이 툴이
없어 **모든 DM 후보 노드가 `missing_dscan/dsync/drm_tool`로 거부**된다. 그래서 job 이미지의
mpifileutils 바이너리를 plain `dms` 위에 얹은 별도 이미지가 필요하다. → **job 이미지를 먼저
빌드**(§2.1)해야 `MFU_IMAGE`로 넘길 수 있다.

```bash
docker build -f install/docker/Dockerfile.agent \
  --build-arg DMS_IMAGE=registry.example.internal/dms:v1 \
  --build-arg MFU_IMAGE=registry.example.internal/dms-mpifileutils:v1 \
  -t registry.example.internal/dms-agent:v1 .
docker push registry.example.internal/dms-agent:v1
```

`install/kubernetes/agent-daemonset.yaml` → DaemonSet **`dms-dm-agent`** 컨테이너:

```yaml
image: registry.example.internal/dms-agent:v1   # ← dms-agent 이미지 (dms 아님)
```

> **`dms-rm-agent`는 plain `dms` 이미지 그대로** 둔다 — RM readiness는 mount + can-i만 보고
> mpifileutils 툴을 요구하지 않는다. DM/RM 두 DaemonSet의 이미지가 다르다는 점에 주의.

---

## 3. 노드 신원: `DMS_AGENT_IDENTITY_USERS` + NSS/SSSD

DM 잡 pod는 요청자의 POSIX uid/gid로 실행되고, 잡 스크립트가 `chown <username>` / `runuser`를
호출한다. 그래서 **각 DM 노드**가 그 계정을 신원-Ready로 보증해야 한다.

`install/kubernetes/agent-daemonset.yaml` → DaemonSet `dms-dm-agent` → env:

```yaml
- name: DMS_AGENT_IDENTITY_USERS
  value: "alice,bob"           # ← 이 노드에서 DM 잡을 돌릴 요청자 POSIX 유저(csv). 기본 placeholder를 교체
```

- 여기 나열된 유저는 agent가 `getpwnam`으로 확인(identity 증거). 요청자가 목록에 없으면 그
  노드는 `identity_not_ready_on_node`로 후보에서 빠지고, **모든 비-privileged DM 잡이 거부**된다.
- 나열한 유저는 **노드 OS에서 실제로 해석**돼야 한다 → DM 노드에 **NSS/SSSD**(또는 동등한 디렉터리
  연동)를 구성한다(프리렉 상세는 [dms-01 §노드 신원 해석](dms-01-prerequisites.md)). 잡 이미지
  안에서도 `runuser`/chown이 그 유저를 해석할 수 있어야 한다.
- privileged root 잡은 이 게이트를 우회한다(§8) — `getpwnam("root")`는 어디서나 성공하므로
  `DMS_AGENT_IDENTITY_USERS`에 `root`를 넣을 필요는 없다.

---

## 4. LDAP 신원 해석 (read-only)

dm-worker가 preflight에서 요청의 `owner_username`을 **LDAP로 조회**해 uid/gid/groups를 해석하고
job pod의 `runAsUser`/`runAsGroup`/`fsGroup`을 세팅한다(저장 매핑 없음, RM과 동일 디렉토리).
미설정 → `ldap_not_configured`, 다운 → `ldap_unavailable`로 **fail-closed**(stale 신원 없음).

`control-plane.yaml` → ConfigMap `dms-runtime-config`:

```yaml
DMS_LDAP_URI: "ldap://ldap.example.internal:389"
DMS_LDAP_BASE_DN: "dc=example,dc=internal"
DMS_LDAP_USER_SEARCH_BASE: "ou=people,dc=example,dc=internal"
DMS_LDAP_GROUP_SEARCH_BASE: "ou=groups,dc=example,dc=internal"
DMS_LDAP_USER_FILTER: "(uid={username})"
DMS_LDAP_TIMEOUT_SECONDS: "5"
```

`control-plane.yaml` → Secret `dms-secrets`(bind 자격증명):

```yaml
DMS_LDAP_BIND_DN: "cn=dms,ou=service-accounts,dc=example,dc=internal"
DMS_LDAP_BIND_PASSWORD: "<실제 비밀번호로>"
```

> RM의 CephFS/WekaFS 파일시스템 연산은 LDAP bind를 **강하게(eager) 요구**하므로 RM을 이미 설정했다면
> 이 값들은 채워져 있을 것이다([dms-03](dms-03-rm-filesystem.md) 참조). DM은 그 동일 디렉토리를 재사용한다.

---

## 5. dm-worker 활성화 (`replicas: 1`)

`install/kubernetes/control-plane.yaml` → Deployment **`dms-dm-worker`**:

```yaml
spec:
  replicas: 1     # 1 = DM 활성 (매니페스트 기본)
```

- **`1`이 정상 = DM 켜짐.** dm-worker가 DM plan을 claim → preflight → Volcano 잡 생성·폴링한다.
- **`0`은 DM을 의도적으로 끌 때만.** 0이면 아무 워커도 data job을 claim하지 않아 scan/sync/rm이
  **큐에서 영구 대기**한다(0을 "정상 유휴"로 오해하지 말 것).
- dm-worker는 **컨트롤플레인과 동일한 plain `dms` 이미지**를 쓰며 `runAsUser: 0`(root)으로 돈다 —
  공유 FS의 요청자-소유 잠긴 artifact(`summary.json`)를 읽기 위함이다. 읽기전용 오케스트레이터라
  (FS 쓰기 0, `kubectl`은 SA 토큰) root가 그 cross-uid 읽기 외 권한을 주지 않는다. api/planner/
  rm-worker는 비-root uid 65532 유지.

---

## 6. 공유 artifact 파일시스템 (동일 경로)

launcher가 쓴 rank-script/로그/summary를 **다른 노드의 worker**가 읽어야 하므로, artifact 경로는
**하나의 공유 RWX 파일시스템**이 **`dms-dm-worker` 노드와 모든 DM 잡 노드에 동일한 경로**로 마운트돼
있어야 한다(프리렉은 [dms-01 §공유 RWX](dms-01-prerequisites.md)).

`control-plane.yaml` → ConfigMap `dms-runtime-config`:

```yaml
DMS_DM_ARTIFACT_BASE_URI: "file:///artifacts/dms"   # 공유 FS 마운트포인트/dms
```

> 코드 기본값 `file:///var/lib/dms/artifacts`는 **노드-로컬**이라 멀티노드/nsync에서 깨진다.
> 매니페스트 기본은 `file:///artifacts/dms`이며, 공유 FS 마운트포인트에 맞춘다.

`control-plane.yaml` → Deployment `dms-dm-worker` → volume `dm-artifacts`:

```yaml
volumes:
  - name: dm-artifacts
    hostPath:
      path: /artifacts        # ← 공유 FS의 **마운트포인트 자체**로 치환(예: CephFS면 /cephfs). 서브디렉터리 금지
      type: Directory         # DirectoryOrCreate 아님 — FS 미마운트 시 빈 로컬 디렉터리에 조용히 bind되지 않게
volumeMounts:
  - name: dm-artifacts
    mountPath: /artifacts
    mountPropagation: HostToContainer    # rslave — host 재마운트가 컨테이너로 전파(stale bind 방지)
```

- **마운트포인트(서브경로 아님)를 `HostToContainer`(rslave)로** 마운트한다. dm-worker는 장수
  오케스트레이터라, 서브경로를 기본(`None`) propagation으로 bind하면 host가 세션 중 공유 FS를
  재마운트할 때 bind가 stale돼 새 artifact를 못 본다. 마운트포인트를 rslave로 받으면 **재기동 없이**
  복구된다(노드 `/`·마운트포인트가 `shared` 전제). job pod 볼륨도 동일 propagation(워커가 자동
  생성하는 잡 매니페스트가 처리).
- **nsync(분리 노드)**: source·destination이 서로 다른 노드 집합이면, artifact는 source도
  destination도 아닌 **모든 참여 노드에 공통 마운트된 별도 공유 스토리지**여야 한다. 공통 마운트가
  없으면 다른 역할의 worker가 rank-script를 못 읽어 전 rank가 기동 실패한다.
- **보안 모델**: 베이스 디렉토리는 `root:root 0755`(world-writable `1777` 금지). per-job artifact는
  launcher가 `umask 077` + 요청자 chown으로 **요청자-only**로 잠근다 → 타 테넌트 job pod가 공유
  FS에서 못 읽는다. dm-worker(root)가 잠긴 summary를 symlink containment 가드로 읽는다.

---

## 7. DM RBAC

DM은 RBAC 두 벌이 필요하다 — 하나는 `control-plane.yaml`에 **내장**돼 있고, 하나는 **별도 apply**다.

### 7.1 control-plane.yaml 내장 (dm-worker + terminate)

`control-plane.yaml`을 apply하면 자동 포함된다:

- **Role/RoleBinding `dms-dm-volcano`** (→ SA `dms-dm-worker`): `batch.volcano.sh/jobs`
  create·get·list·watch·delete, `podgroups` 조회, `pods`/`pods/log`/`events` 조회, `pods` create·delete.
  Volcano 잡 생성·폴링에 필요. (Kubeflow verb 없음 — MPI Operator 불필요.)
- **Role/RoleBinding `dms-api-dm-terminate`** (→ SA `dms-api`): `data.cancel`이 in-flight MPI 잡을
  실제 종료하도록 `batch.volcano.sh/jobs` get·delete.

### 7.2 별도 apply — `dms-api-volcano-rbac.yaml`

`GET /api/v1/operations/data-jobs/{id}/logs`(포탈의 잡 로그 tail이 사용)가 launcher pod 로그를
읽으려면 dms-api에 `pods/log` + Volcano read가 필요하다. 이건 **control-plane.yaml에 없으니 반드시
따로 적용**한다:

```bash
kubectl apply -f install/kubernetes/dms-api-volcano-rbac.yaml
```

(ClusterRole `dms-api-volcano-read` → SA `dms-api`: `queues`·`jobs`·`pods` read + `pods/log` get.)

### 7.3 storages sync RBAC (신규 filesystem 스토리지 전파)

신규 filesystem 스토리지가 DM 후보가 되려면 agent에 전파돼야 한다. dms-api는 스토리지 매핑
create/update/delete마다 `dms-agent-storages` ConfigMap을 patch하는데, 이는 `control-plane.yaml`의
**Role/RoleBinding `dms-agent-storages-sync`**(configmaps get·update·patch, SA `dms-api` +
`dms-remote`에 바인딩)에 의존한다. **이 RBAC가 없으면 patch가 Forbidden인데 코드가 그걸 삼켜**
ConfigMap이 조용히 안 갱신되고, 새 스토리지가 agent에 닿지 못해 **DM은 `no_ready_dm_candidate`**
(RM은 `missing_rm_readiness`)가 된다. control-plane.yaml에 내장돼 있으니 그대로 적용되면 된다.

> **agent는 storages.json을 startup에 한 번만 읽는다.** 그래서 스토리지 매핑을 바꾼 뒤에는 DaemonSet을
> rollout-restart 해야 새 스토리지가 반영된다: `POST /api/v1/agent/rollout-restart`(RM·DM DaemonSet에
> `restartedAt` stamp) 또는 `kubectl -n dms rollout restart ds/dms-dm-agent ds/dms-rm-agent`.

---

## 8. DM 정책 (노드/프로세스/큐)

DM 잡의 리소스·스케줄링 기본은 **operational DB의 per-tool 정책**에서 오고, 그 **부트스트랩 값**을
`control-plane.yaml` → ConfigMap `dms-runtime-config`로 준다:

```yaml
DMS_DM_POLICY_DEFAULT_QUEUE: "dms-data"              # Volcano 큐 (dms-01에서 만든 이름과 일치)
DMS_DM_POLICY_DEFAULT_PRIORITY_CLASS: "dms-normal"   # PriorityClass (dms-low/normal/high 중)
DMS_DM_POLICY_DEFAULT_WORKER_NODES: "3"              # worker 노드 수 기본
DMS_DM_POLICY_MAX_WORKER_NODES: "3"                  #            최대(요청이 넘으면 clamp)
DMS_DM_POLICY_DEFAULT_PROCESSES_PER_NODE: "3"        # 노드당 프로세스 기본
DMS_DM_POLICY_MAX_PROCESSES_PER_NODE: "10"           #                최대
```

- 큐/PriorityClass **이름은 [dms-01](dms-01-prerequisites.md)의 `volcano-queue-priorityclasses.yaml`
  (Queue `dms-data`, PriorityClass `dms-low`(50)/`dms-normal`(100)/`dms-high`(200))과 반드시 일치**시킨다.
  큐가 없으면 잡 영구 Pending, PriorityClass가 없으면 파드 admission 거부.
- 요청의 `resources`는 이 정책 상한 내에서만 조정된다(초과 시 최대로 clamp). 런타임에 조정하려면
  DMS API `GET/PUT /api/v1/data-management/policies[/{operation}]`(→ [docs/api/data-management.md]
  (../docs/api/data-management.md)).
- 검증 초기엔 `node_count=1, processes_per_node=1`(launcher+worker 1쌍)로 시작하면 단순하다.

---

## 9. 신원 모델 — 비-privileged 3요건 vs 운영자 root

### 9.1 비-privileged 일반 잡 (기본·권장)

일반 사용자 DM 잡이 실행되려면 **세 가지가 모두** 갖춰져야 한다:

1. **LDAP** (`DMS_LDAP_*`, §4)로 요청자 POSIX 신원(uid/gid/groups)이 해석되고,
2. 요청자가 각 DM 노드의 **`DMS_AGENT_IDENTITY_USERS`** 에 있으며 노드 **NSS/SSSD**로 해석되고(§3),
3. 해석된 uid/gid가 **하한 이상**이어야 한다 — `DMS_DM_MIN_UID`/`DMS_DM_MIN_GID`(기본 `1000`,
   control-plane.yaml에 미기재 = 기본 사용). 미만(시스템/root 계정)이면 `uid_below_floor`로 거부.

하나라도 빠지면 preflight에서 `ldap_*` / `identity_not_ready_on_node` / `uid_below_floor`로 거부된다.

### 9.2 운영자 root 경로 (특권)

운영자가 임의 사용자 데이터를 이관·정리하려 **root(uid 0)** 로 실행해야 할 때가 있다. 이 경로는
LDAP·uid 하한을 우회하고 job pod를 `runAsUser: 0`으로 띄운다. `control-plane.yaml` → CM에서
`DMS_DM_ALLOW_ROOT_REQUESTER: "true"`(매니페스트 기본)로 켜지며, 나머지는 코드 기본을 쓴다.

| 통제 | 설정(기본) | 의미 |
|---|---|---|
| feature flag | `DMS_DM_ALLOW_ROOT_REQUESTER` (`true`) | 꺼지면 root 요청은 `ldap_identity_not_found`로 거부 |
| 권한 requester 집합 | `DMS_DM_PRIVILEGED_REQUESTERS` (`root`) | 이 집합과 일치할 때만 uid/gid 0 합성 |
| **mTLS operator 강제** | `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true` 전제 | root 요청은 **mTLS-verified operator**(actor `mtls:` 접두)만. 평문 채널 root는 `403` |
| operator allowlist | `DMS_DM_PRIVILEGED_OPERATORS` (비움=verified 전체) | 특정 operator actor만 허용 |
| scope allowlist | `DMS_DM_PRIVILEGED_SCOPES` (비움=전체) | root 잡이 건드릴 `storage`/`storage:prefix` 제한 |

> **보안 노트.** `requester_id`는 클라가 채우는 인증 안 된 필드다. root 경로의 안전성은 전적으로
> **"누가 DMS DM API를 호출할 수 있는가"** 로 환원된다 — 그래서 프로덕션 mTLS-verified 프로필
> ([dms-02](dms-02-core.md))에서만 root를 받고, `DMS_DM_PRIVILEGED_REQUESTERS`/`_OPERATORS`/`_SCOPES`로
> **최소 범위로 좁혀 주기적으로 검토**할 것. 평문 `x-dms-actor` 채널로 root가 닿으면 권한상승 구멍이 된다.
> preview→confirm 게이트는 root에서도 우회되지 않는다.

---

## 10. 적용 & 검증

편집을 마쳤으면 매니페스트를 적용한다(코어는 [dms-02](dms-02-core.md)에서 이미 적용됐다고 가정):

```bash
kubectl apply -f install/kubernetes/volcano-queue-priorityclasses.yaml   # dms-01 (미적용 시)
kubectl apply -f install/kubernetes/control-plane.yaml                    # dm-worker·CM·dm-volcano RBAC
kubectl apply -f install/kubernetes/agent-daemonset.yaml                  # dms-dm-agent (dms-agent 이미지)
kubectl apply -f install/kubernetes/dms-api-volcano-rbac.yaml             # 로그 tail RBAC (별도!)

kubectl -n dms rollout status deploy/dms-dm-worker
kubectl -n dms rollout status ds/dms-dm-agent
```

프리렉·sanity/readiness 확인:

```bash
# 프리렉 (dms-01)
kubectl get queue.scheduling.volcano.sh dms-data            # STATE=Open
kubectl get priorityclass | grep dms                        # dms-low/normal/high
kubectl get ns dms -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}{"\n"}'   # privileged

# DM agent가 실제로 DM 이미지·툴을 들고 떴는지
kubectl -n dms get pods -l app.kubernetes.io/name=dms-dm-agent -o wide
```

스토리지의 DM readiness 확인/재계산(프로덕션 = mTLS-verified 프로필: 클라이언트 인증서로 인증,
actor는 인증서 subject에서 파생 — 평문 `x-dms-actor`는 신뢰하지 않음):

```bash
H=(-sS --cert client.crt --key client.key --cacert ca.crt -H "authorization: Bearer <TOKEN>")
U=https://dms.example.internal

# 스토리지 매핑의 data_management readiness가 Ready여야 planner가 통과
curl "${H[@]}" "$U/api/v1/operations/storage-mappings" | jq '.[] | {name, readiness}'

# dm-agent를 새로 붙였으면 sanity 재실행(readiness stale "Missing" 해소)
curl "${H[@]}" -X POST "$U/api/v1/resource-management/storage-mappings/cephfs-a:check"
```

> **부연(dev/testbed 프로필).** `DMS_REQUIRE_MTLS_VERIFIED_HEADER=false`인 비-프로덕션에서만 인증서
> 없이 `-H "authorization: Bearer <TOKEN>" -H "x-dms-actor: operator"`로 호출한다. 요청/응답 shape은
> 동일하다. 프로덕션에서는 `x-dms-actor`를 신뢰하지 않으므로 쓰지 않는다.

- readiness가 stale로 흐르는 걸 자동 갱신하려면 **sanity-reconciler**를 배포한다
  (`install/kubernetes/sanity-reconciler.yaml`, `DMS_SANITY_RECONCILE_INTERVAL_SECONDS` 기본 30s) —
  자세한 건 [docs/operations-runbook.md](../docs/operations-runbook.md).
- 실제 scan/sync/rm 제출·preview·confirm 절차는 [docs/api/data-management.md](../docs/api/data-management.md).

---

## 다음 문서

- **[docs/api/data-management.md](../docs/api/data-management.md)** — DM `scan`/`sync`/`rm` API(요청·preview/confirm·옵션·응답)
- **[dms-01-prerequisites.md](dms-01-prerequisites.md)** — 클러스터 프리렉(Volcano·큐·PriorityClass·PSA·공유 FS·NSS/SSSD·host-mount)
- **[dms-02-core.md](dms-02-core.md)** — 코어 배포(plain dms 이미지·secret·control-plane·mTLS·ingress·migration)
- **[dms-06-configuration.md](dms-06-configuration.md)** — 환경변수 레퍼런스(`DMS_DM_*`·`DMS_LDAP_*` 전체)
- **[docs/operations-runbook.md](../docs/operations-runbook.md)** — 운영 런북(readiness 디버깅·잡 라이프사이클·sanity-reconciler)
