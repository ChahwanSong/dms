# DMS 환경변수 레퍼런스

DMS 프로세스(api/planner/rm-worker/dm-worker/retention/sanity)와 노드 에이전트가 읽는
**환경변수의 single source of truth**다. 값의 의미·기본값·운영 필수 여부를 카테고리별로 정리한다.

- 이 값들이 **어떤 매니페스트의 어떤 key로** 들어가고 core를 어떻게 배포하는지는 [`dms-02-core.md`](dms-02-core.md).
- 클러스터/외부 사전 준비(Volcano · `Queue`/`PriorityClass` · 공유 RWX artifact FS · DM 네임스페이스 `PodSecurity=privileged` · 노드 NSS/SSSD · 스토리지 host-mount)는 [`dms-01-prerequisites.md`](dms-01-prerequisites.md). **이 문서의 env-var는 그 prereq 위에서만 동작한다** — prereq이 빠지면 값이 옳아도 DM 잡이 스케줄되지 않거나 후보 노드가 전부 거부된다.
- 값은 전부 운영용 placeholder(`registry.example.internal`, `cluster-a`, `dc=example,dc=internal`, `/cephfs`…)다. 실제 값으로 교체한다.

## 설정이 주입되는 위치

| 소스 | 파일 | 담는 것 |
| --- | --- | --- |
| ConfigMap `dms-runtime-config` | `install/kubernetes/control-plane.yaml` | 비밀이 아닌 런타임 env 전부 (mTLS 플래그, k8s/FS mode, DM 정책·타임아웃, LDAP non-secret 등) |
| Secret `dms-secrets` | `install/kubernetes/control-plane.yaml` | `DMS_DATABASE_URL`, `DMS_OBSERVABILITY_DATABASE_URL`, `DMS_AUTH_SHARED_TOKEN`, `DMS_LDAP_BIND_DN`, `DMS_LDAP_BIND_PASSWORD` |
| 에이전트 ConfigMap `dms-agent-runtime-config` | `install/kubernetes/agent-daemonset.yaml` | `DMS_AGENT_*` |
| 비-k8s 참조 / 드리프트 체크 | `install/config/dms-runtime.env.example` | ConfigMap과 **동일한 값 세트** |

> `control-plane.yaml`의 ConfigMap과 `dms-runtime.env.example`은 같은 기본값을 담는다 — **한쪽만 바꾸지 말 것**. env-example이 명시하지만 ConfigMap이 생략한 몇몇 변수(`DMS_DM_MIN_UID`, `DMS_DM_PATH_BASE`, `DMS_DM_PRIVILEGED_*`, `DMS_DM_IDENTITY_PROVIDER` 등)는 **코드 기본값과 동일한 값**이라 ConfigMap이 코드 fallback에 의존해도 결과가 같다(드리프트 아님).

**기본값 열 규약**: shipped 값(ConfigMap/env-example 기준)을 적는다. 코드 bare fallback이 다르면 괄호로 병기하고, 그 경우 **항상 명시 설정**한다(fallback에 의존하지 않는다).

배포 전에 남은 placeholder 확인:

```bash
grep -R "CHANGE_ME\|registry.example.internal\|dms.example.internal\|postgres.example.internal\|ldap.example.internal" \
  /tmp/dms-control-plane.yaml /tmp/dms-agent-daemonset.yaml 2>/dev/null || true
```

출력이 있으면 아직 교체할 값이 남은 것이다. 파일별 편집 목록은 [`dms-02-core.md`](dms-02-core.md).

---

## 1. 인증 / mTLS

**운영 프로필 = mTLS-verified header profile.** `control-plane.yaml`의 ConfigMap `dms-runtime-config`가
`DMS_REQUIRE_MTLS_HEADER=true` + `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`로 켠다. 흐름:

1. 신뢰된 ingress/edge proxy가 client certificate를 **검증**하고 그 결과(subject + verify=SUCCESS)를 upstream header로 전달한다.
2. DMS는 actor를 **certificate subject에서 derive**한다. prefix는 `DMS_MTLS_ACTOR_PREFIX`(기본 `mtls:`) → 예: `mtls:CN=alice,...`.
3. **평문 `x-dms-actor`는 신뢰하지 않는다.** 평문 actor가 mTLS로 derive한 actor와 다르면 인증을 거부한다.
4. `DMS_DEFAULT_ACTOR`는 **비어 있어야 한다**. `DMS_REQUIRE_MTLS_HEADER=true`인데 값이 있으면 **API startup이 실패**한다.
5. shared bearer token(`DMS_AUTH_SHARED_TOKEN`)을 mTLS 위에 **추가로** 얹을 수 있다.

운영 curl은 `--cert client.crt --key client.key --cacert ca.crt` (+ 선택 `Authorization: Bearer`)로 호출하고, **`x-dms-actor`를 보내지 않는다**.

| 변수 | 기본값 | 운영 필수 | 설명 |
| --- | --- | --- | --- |
| `DMS_REQUIRE_MTLS_HEADER` | `true` | 예 | trusted ingress/edge proxy가 전달한 client cert subject evidence header를 요구한다. |
| `DMS_REQUIRE_MTLS_VERIFIED_HEADER` | `true` | 예 | client cert verify 결과가 `SUCCESS`여야 한다. `true`면 `DMS_REQUIRE_MTLS_HEADER=true`도 필수. |
| `DMS_MTLS_ACTOR_PREFIX` | `mtls:` | 아니오 | mTLS subject에서 derive한 actor prefix. |
| `DMS_DEFAULT_ACTOR` | 비어 있음 | — | mTLS 프로필에서는 **반드시 비운다**(`DMS_DEFAULT_ACTOR=` 가능). 비어 있지 않으면 startup 실패. |
| `DMS_AUTH_SHARED_TOKEN` | 없음(Secret) | 예(권장) | API가 허용하는 shared bearer token. mTLS 위에 레이어링하고 agent/스크립트가 사용. |

**mTLS evidence header** (두 family 지원):

- edge proxy 스타일: `X-DMS-Client-Cert-Subject`, `X-DMS-Client-Cert-Verify: SUCCESS`
- ingress-nginx 스타일: `ssl-client-subject-dn`, `ssl-client-verify: SUCCESS`

두 family가 동시에 들어와 subject/verify가 **충돌하면** API는 인증을 거부한다.

**startup sanity 가드**: `REQUIRE_MTLS_VERIFIED_HEADER=true` ⇒ `REQUIRE_MTLS_HEADER=true` 필수 · `REQUIRE_MTLS_HEADER=true` + non-empty `DMS_DEFAULT_ACTOR` ⇒ startup 실패.

**클라이언트/헬퍼 변수** (운영 curl·helper 스크립트가 읽음; 서버 env 아님):

| 변수 | 설명 |
| --- | --- |
| `DMS_API_URL` | Ingress URL. 예: `https://dms.example.internal` |
| `DMS_TOKEN` | `DMS_AUTH_SHARED_TOKEN`과 같은 bearer token |
| `DMS_CLIENT_CERT` / `DMS_CLIENT_KEY` | operator/automation client certificate + private key |
| `DMS_CA_CERT` | DMS API server certificate을 검증할 CA |
| `DMS_ACTOR` | 운영에서는 **unset**. dev/test fallback 전용 |

> **부연(dev/testbed 프로필).** `DMS_REQUIRE_MTLS_VERIFIED_HEADER=false`로 두면 cert 없이 평문 `Authorization: Bearer` + `x-dms-actor`만으로 호출된다. request/response 형태를 문서·로컬에서 읽기 편하게 하려는 용도이며(예: [`../docs/api/README.md`](../docs/api/README.md)의 예시), **운영에는 쓰지 않는다**.

---

## 2. 데이터베이스

| 변수 | 기본값 | 운영 필수 | 설명 |
| --- | --- | --- | --- |
| `DMS_DATABASE_URL` | 없음(Secret) | 예 | 운영 PostgreSQL URL. request/plan/run/resource/storage-mapping/agent-report/data-job 저장. |
| `DMS_OBSERVABILITY_DATABASE_URL` | 없음(Secret) | 예 | Observability PostgreSQL URL. diagnostic event 저장. 운영에서는 별도 DB. |

**Connection pool** (프로세스당·URL당 bounded pool; SQLite에는 미적용). ConfigMap/env-example에는 없고 코드 기본값을 쓰므로, 동시성을 키울 때만 override한다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_DB_POOL_MIN_SIZE` | `1` | 미리 열어두는 최소 connection 수(운영·관측 공통; 관측은 max로 clamp). |
| `DMS_DB_POOL_MAX_SIZE` | `4` | loop 프로세스(planner/rm-worker/dm-worker/sanity/retention)의 운영 DB pool 최대치. 단일 스레드라 작게 둔다. |
| `DMS_DB_API_POOL_MAX_SIZE` | `16` | API 프로세스의 운영 DB pool 최대치. API sync-handler 스레드풀이 이 값으로 cap된다. |
| `DMS_DB_OBSERVABILITY_POOL_MAX_SIZE` | `3` | 관측 DB pool 최대치(쓰기 부하 가벼워 작게). |
| `DMS_DB_POOL_TIMEOUT_SECONDS` | `35` | pool 만석 시 checkout 대기 최대(초). **`DMS_DB_STATEMENT_TIMEOUT_MS`(초 환산) 이상**이어야 한다. |
| `DMS_DB_STATEMENT_TIMEOUT_MS` | `30000` | pooled connection `statement_timeout`(ms). runaway 쿼리 강제 종료. |
| `DMS_DB_IDLE_IN_TXN_TIMEOUT_MS` | `60000` | pooled connection `idle_in_transaction_session_timeout`(ms). 누수 트랜잭션 강제 종료. |

천장 공식: `서버 PG connection ≤ Σ프로세스(op_max + obs_max)`. 기본값 기준 API×2 + loop 5개 = `2×(16+3) + 5×(4+3) = 73 < 100`. 동시성을 키울 땐 `DMS_DB_API_POOL_MAX_SIZE`와 PostgreSQL `max_connections`를 **함께** 올린다([`dms-02-core.md`](dms-02-core.md) DB 섹션). migration/대량 유지보수는 unpooled로 실행되어 위 timeout 영향을 받지 않는다.

---

## 3. 코어 / 워커 런타임

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_WORKER_LEASE_SECONDS` | `300` | planner/RM/DM worker의 claim lease. RM/DM은 backend call 중 heartbeat로 갱신. |
| `DMS_PREVIEW_TTL_SECONDS` | `86400` | `sync`/`rm` preview가 `ConfirmPending`으로 유지되는 TTL. `scan`은 confirm 없이 read-only. |
| `DMS_AGENT_REPORT_STALE_SECONDS` | `300` | storage-mapping readiness의 agent report freshness window. |
| `DMS_CONTROL_CLUSTER_NAME` | `cluster-a` | DM readiness·inventory aggregation에 쓰는 control cluster name. |

**agent_reports history 보존** (`dms retention --loop`). 100+ node가 분당 1회 보고하면 history가 수백만 행으로 자란다. node-health는 `agent_node_current`(node별 최신 1행)에서 읽으므로 history는 node-metrics 시계열용이고 나이 기준 prune이 안전하다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_AGENT_REPORT_RETENTION_SECONDS` | `2592000`(30일) | 이보다 오래된 `agent_reports` 행을 prune. **parse 시 7일(604800) 이상으로 floor** — sparkline window 아래로 못 내려감. |
| `DMS_AGENT_REPORT_RETENTION_INTERVAL_SECONDS` | `3600` | retention loop 주기. |
| `DMS_AGENT_REPORT_RETENTION_HEARTBEAT_PATH` | 설정 안 됨 | 설정 시 매 cycle heartbeat 파일 기록 → k8s livenessProbe가 loop hang 감지/재시작. |

---

## 4. LDAP / identity

RM의 access-group 관리와 DM의 요청자 POSIX 신원 resolve가 **같은 `DMS_LDAP_*` 값을 공유**한다. **CephFS·WekaFS filesystem 작업은 동작하는 LDAP bind를 eager하게 요구**(bind 실패 시 그 작업이 실패)하고, GPFS는 선택이다. 자세한 backend별 요건은 [`dms-03-rm-filesystem.md`](dms-03-rm-filesystem.md).

| 변수 | 기본값 | 운영 필수 | 설명 |
| --- | --- | --- | --- |
| `DMS_LDAP_URI` | `ldap://ldap.example.internal:389` | 예(FS/DM) | LDAP URI. |
| `DMS_LDAP_BASE_DN` | `dc=example,dc=internal` | 예 | Base DN. |
| `DMS_LDAP_BIND_DN` | 없음(Secret) | 예 | Bind DN. Secret 저장. |
| `DMS_LDAP_BIND_PASSWORD` | 없음(Secret) | 예 | Bind password. Secret 저장. |
| `DMS_LDAP_USER_SEARCH_BASE` | `ou=people,<baseDN>` | 아니오 | 사용자 검색 base. |
| `DMS_LDAP_GROUP_SEARCH_BASE` | `ou=groups,<baseDN>` | 아니오 | 그룹 검색 base. |
| `DMS_LDAP_USER_FILTER` | `(uid={username})` | 아니오 | 사용자 필터. DM identity lookup도 이 필터를 쓴다. |
| `DMS_LDAP_TIMEOUT_SECONDS` | `5` | 아니오 | LDAP timeout. |
| `DMS_LDAP_GROUP_GID_START` | `24000` (코드 fallback `9000000`) | 예(명시) | DMS 생성 access group의 GID 할당 하한. **기존 시스템/디렉터리 GID와 겹치지 않는 창**으로 사이징. |
| `DMS_LDAP_GROUP_GID_END` | `24999` (코드 fallback `9999999`) | 예(명시) | 위 할당 창 상한(START와 맞춘다). |
| `DMS_DM_IDENTITY_PROVIDER` | `ldap` | 아니오 | DM identity provider. |

DM identity는 별도 mapping 등록 없이 dm-worker preflight에서 위 `DMS_LDAP_*`로 `owner_username`(기본 `requester_id`)을 read-only lookup해 해석한다. 캐시가 없어 **fail closed** — LDAP가 응답하지 않으면 그 job preflight가 `ldap_unavailable`로 실패한다.

---

## 5. 파일시스템 RM 실행

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_FILESYSTEM_MUTATION_MODE` | `ssh-host-exec` | CephFS adapter execution mode. `ssh-host-exec` 또는 `local`. GPFS는 storage mapping의 `command_runner` 사용. |
| `DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS` | `300` (코드 fallback `30`) | filesystem host command timeout. quota read-back이 느리면 늘린다. |
| `DMS_FILESYSTEM_EXEC_USE_SUDO` | `true` | CephFS host executor가 host mutation에 sudo 사용 여부. |

파일시스템 RM 전체 설정(SSH 신뢰, host-exec, storage mapping 등록)은 [`dms-03-rm-filesystem.md`](dms-03-rm-filesystem.md).

---

## 6. Kubernetes RM (네임스페이스 쿼터)

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_KUBERNETES_INVENTORY_MODE` | `kubectl` | 읽기 전용 inventory mode. `kubectl` / `ssh-kubectl` / `python-client`. API 등록 sanity와 sanity-reconciler가 이 mode로 클러스터를 읽어 agentless managed cluster의 k8s/CSI mapping을 검증. 클러스터별 격리(한 클러스터 실패가 전체 inventory를 무력화하지 않음). |
| `DMS_KUBERNETES_MUTATION_MODE` | `kubectl` | Namespace/ResourceQuota mutation mode(전역 기본). `kubectl`(직접 도달) 또는 `ssh-kubectl`. **storage mapping의 `backend_template.mutation_mode`로 클러스터별 override 가능**. |
| `DMS_CLUSTER_KUBECONFIGS_JSON` | 설정 안 됨 | cluster name → kubeconfig path JSON. current-context를 안 쓰는 `kubectl` mode에 필요. |
| `DMS_CLUSTER_CONTROL_HOSTS_JSON` | 설정 안 됨 | cluster name → SSH host JSON. `ssh-kubectl` mode 전역 기본. mapping의 `control_host`가 있으면 그게 우선. |
| `DMS_KUBERNETES_INVENTORY_TIMEOUT_SECONDS` | `10` | inventory read timeout. |
| `DMS_KUBERNETES_MUTATION_TIMEOUT_SECONDS` | `30` | ResourceQuota mutation timeout. |

> **per-mapping override.** 전역은 `kubectl`(직접 도달 클러스터)로 두고, 직접 도달 불가한 managed 클러스터 매핑만 `backend_template`에 `mutation_mode:"ssh-kubectl"` + `control_host:"<bastion>"`을 넣으면 그 클러스터의 ResourceQuota mutation만 `ssh <bastion> kubectl ...`로 라우팅된다(없으면 전역값으로 fallback). `control_host`만 있고 `mutation_mode`가 없으면 등록 시 **422 거부**(전역 기본 `kubectl`이라 control_host가 무시되므로 명시 필요). 이 per-mapping 경로는 CSI mapping sanity(`kubectl auth can-i ... resourcequota`)에도 그대로 쓰인다. 등록·검증 절차는 [`dms-04-rm-k8s-quota.md`](dms-04-rm-k8s-quota.md).

---

## 7. 데이터 관리(DM) 잡

DM 잡은 [`dms-01-prerequisites.md`](dms-01-prerequisites.md)의 클러스터 prereq 위에서만 실행된다: **Volcano** 설치 + `Queue dms-data` + `PriorityClass dms-low/normal/high`(`install/kubernetes/volcano-queue-priorityclasses.yaml`), DM 네임스페이스 `PodSecurity=privileged`, dm-worker와 **모든 DM 잡 노드에 동일 경로로 마운트된 공유 RWX artifact FS**, 노드 NSS/SSSD, 스토리지 host-mount. queue가 없으면 잡이 영구 Pending, PriorityClass가 없으면 pod가 admission에서 거부된다.

`dms-dm-worker` Deployment **replicas=1 = DM enabled**. `0`은 DM을 **의도적으로 끌 때만** — 0이면 어떤 worker도 data job을 claim하지 않아 `scan`/`sync`/`rm`이 큐에 쌓인 채 실행되지 않는다(정상 상태 아님).

### 이미지 빌드 순서 (DMS_DM_JOB_IMAGE 트랩)

빌드 순서와 대상은 [`dms-02-core.md`](dms-02-core.md)·[`dms-05-dm-jobs.md`](dms-05-dm-jobs.md)에 있다. 요지:

1. **DM 잡 이미지**를 `install/docker/Dockerfile.mpifileutils`로 빌드→레지스트리 push → 그 ref를 `control-plane.yaml` ConfigMap `dms-runtime-config`의 `DMS_DM_JOB_IMAGE`에 넣는다(실제 push한 ref로).
2. **dms-agent 이미지**를 `install/docker/Dockerfile.agent`로 빌드하되 `--build-arg MFU_IMAGE=<위 잡 이미지>`(잡 이미지가 **먼저** 있어야 함) → `dms-dm-agent` DaemonSet이 사용. plain `dms` 이미지에는 mpifileutils tool이 없어 DM 후보가 `missing_dscan`/`dsync`/`drm_tool`로 거부된다.
3. **plain dms 이미지**(`install/docker/Dockerfile`) → api/planner/rm-worker/dm-worker/retention/sanity + `dms-rm-agent`.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_DM_JOB_IMAGE` | shipped placeholder(**반드시 실제 push된 ref로**) | DM 잡 파드가 pull하는 mpifileutils 이미지. live `scan`/`sync`/`rm`의 하드 필수. ⚠️ **fail-closed(`missing_dm_job_image`)는 값이 완전히 비었을 때만** 작동한다 — `...:CHANGE_ME` placeholder는 **truthy라 preflight를 통과**하고 잡 파드가 `ImagePullBackOff`로 죽는다. |
| `DMS_DM_JOB_IMAGE_REF` | 설정 안 됨 | 선택 — **provenance 전용**. mpifileutils tag/commit을 job result evidence에 기록만 하고 실제 pull에는 영향 없음(그건 `DMS_DM_JOB_IMAGE`). |
| `DMS_DM_SCHEDULER_BACKEND` | `volcano-job` (**운영 고정**) | DM 잡을 **네이티브 Volcano Job**(`batch.volcano.sh`)으로만 스케줄한다. Volcano 하나가 MPI 워커를 gang-schedule → **Kubeflow MPI Operator 불필요**. ⚠️ 코드 bare default `auto`는 **쓰지 말 것**: Kubeflow `MPIJob`(`kubeflow.org`)을 먼저 apply하려다 MPI Operator가 없으면 **매 잡마다 실패** 후 폴백한다. |
| `DMS_DM_NAMESPACE` | `dms` | Volcano Job을 생성·조회할 namespace. `PodSecurity=privileged` 필수. |
| `DMS_DM_SERVICE_ACCOUNT` | `dms-dm-worker` | Volcano worker pod의 ServiceAccount. |
| `DMS_DM_ARTIFACT_BASE_URI` | `file:///artifacts/dms` (코드 fallback `file:///var/lib/dms/artifacts`) | job별 stdout/stderr/report/summary URI의 base. **dm-worker와 모든 DM 잡 노드에 동일 경로로 마운트된 하나의 공유 RWX FS**여야 한다 — 노드별 로컬 hostPath면 워커·잡이 다른 노드에 뜰 때 깨진다. |
| `DMS_DM_PATH_BASE` | `mount_path` | DM 요청 path 기준점. `mount_path`(storage mount_path 기준) 또는 `managed_root`(storage별 `managed_root` prepend). `managed_root` 모드는 mapping에 `managed_root`가 있어야 하며 산출 불가 시 fail-closed. |
| `DMS_DM_KUBERNETES_MODE` | `cluster` | `cluster`=live Volcano adapter. `stub`은 로컬 테스트 전용(운영 금지). |

**타임아웃/실행 정책**

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_DM_SCAN_TIMEOUT_SECONDS` | `3600` | scan timeout. 초과 시 VolcanoJob terminate 후 실패 기록. |
| `DMS_DM_SYNC_PREVIEW_TIMEOUT_SECONDS` | `3600` | `sync` dry-run preview timeout. |
| `DMS_DM_SYNC_EXECUTION_TIMEOUT_SECONDS` | `259200`(3일) | confirmed `sync` execution timeout. |
| `DMS_DM_RM_PREVIEW_TIMEOUT_SECONDS` | `1800` | `rm` dry-run preview timeout. |
| `DMS_DM_RM_EXECUTION_TIMEOUT_SECONDS` | `3600` | confirmed `rm` execution timeout. |
| `DMS_DM_CONFIRM_REQUIRE_PREVIEW_FINGERPRINT` | `true` | confirm 시 preview fingerprint evidence 요구 여부. |
| `DMS_DM_SYNC_ALLOW_DELETE` | `true` | `sync`의 `delete=true` 옵션 허용 여부. `false`면 validation에서 차단. |
| `DMS_DM_MONITOR_POLL_SECONDS` | `5` | VolcanoJob 상태 polling interval. |
| `DMS_DM_JOB_DELETE_ON_TERMINAL` | `false` | terminal VolcanoJob cleanup 정책. |

**Queue / PriorityClass / fan-out 부트스트랩** — node/process default·max의 source of truth는 DB `data_management_policies` table/API다. 아래 env는 **bootstrap default**로만 쓴다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_DM_POLICY_DEFAULT_QUEUE` | `dms-data` | DB policy bootstrap Volcano queue. 이 이름 `Queue`가 **먼저 존재**해야 함(없으면 잡 영구 Pending). |
| `DMS_DM_POLICY_DEFAULT_PRIORITY_CLASS` | `dms-normal` | DB policy bootstrap PriorityClass. `dms-low`/`dms-normal`/`dms-high` 중 하나가 **먼저 존재**해야 함(없으면 admission 거부). |
| `DMS_DM_POLICY_DEFAULT_WORKER_NODES` | `3` | `scan`/`rm`/same-node `dsync` 기본 worker node 수. |
| `DMS_DM_POLICY_MAX_WORKER_NODES` | `3` | 위 max. |
| `DMS_DM_POLICY_DEFAULT_PROCESSES_PER_NODE` | `3` | worker pod당 기본 MPI ranks/processes. |
| `DMS_DM_POLICY_MAX_PROCESSES_PER_NODE` | `10` | 위 max. |
| `DMS_DM_DEFAULT_PRIORITY` | `Mid` | public priority label 기본값. |
| `DMS_DM_NSYNC_ENABLED` | `true` | separated-role `nsync` 후보 선택·실행 허용. `false`면 fail-closed. |
| `DMS_DM_NSYNC_SERVICE_PREFIX` | `dms-nsync` | native VolcanoJob fallback의 role service/metadata 이름 prefix. |
| `DMS_DM_DEFAULT_MAX_NODES` / `DMS_DM_MAX_NODES` / `DMS_DM_MAX_SYNC_NODES` / `DMS_DM_MAX_RM_NODES` | `1` | **legacy 호환**(사용 안 함). node counts는 DB policy/API가 SoT. |

**신원 / 권한** — 비-privileged(일반 사용자) DM 잡은 세 가지가 **모두** 있어야 한다: (1) `DMS_LDAP_*`로 요청자 POSIX 신원 resolve, (2) 잡이 도는 노드의 agent 신원 증거에 그 사용자 존재 — `DMS_AGENT_IDENTITY_USERS` 베이스라인 또는 **온디맨드 프로빙**(요청 시 자동 등록·프로빙; `DMS_DM_IDENTITY_PROBE_*`)으로 확보(+노드에서 실제 해석 가능해야 함), (3) 해석된 uid/gid ≥ `DMS_DM_MIN_UID`/`DMS_DM_MIN_GID`. 하나라도 빠지면 각각 `ldap_unavailable`/`identity_not_ready_on_node`/`uid_below_floor`로 거부. privileged root 경로는 LDAP를 우회하지만 **mTLS-verified operator를 통해서만** 동작하니 아래 scope로 좁히고 정기 검토한다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_DM_MIN_UID` | `1000` | 비-privileged LDAP 신원 uid 하한. 미만(또는 uid 0)이면 `uid_below_floor` 거부 — root pod 안 `runuser` 강하 전에 시스템 계정 실행 차단. |
| `DMS_DM_MIN_GID` | `1000` | 비-privileged gid 하한. |
| `DMS_DM_ALLOW_ROOT_REQUESTER` | `true` | 운영자 root(privileged) 실행 허용. requester_id/owner_username이 `DMS_DM_PRIVILEGED_REQUESTERS`에 속하면 uid/gid 0으로 합성 실행(LDAP·uid floor 우회). **`DMS_REQUIRE_MTLS_VERIFIED_HEADER=true` 경로에서만 실동작**(평문 root 요청은 403). denylist는 kill-switch로 유지. |
| `DMS_DM_PRIVILEGED_REQUESTERS` | `root` | 합성 root로 실행할 requester allowlist(쉼표). |
| `DMS_DM_PRIVILEGED_UID` / `DMS_DM_PRIVILEGED_GID` | `0` / `0` | privileged 요청에 합성할 uid/gid. |
| `DMS_DM_PRIVILEGED_OPERATORS` | 비어 있음 | root 요청 가능한 operator actor allowlist(쉼표). 비우면 **mTLS-verified operator 전체** 허용. |
| `DMS_DM_PRIVILEGED_SCOPES` | 비어 있음 | root 잡 허용 scope(쉼표): `storage` 또는 `storage:path-prefix`. 비우면 전체 storage. |

DM 잡 사용법(preview/confirm 플로우, 파라미터)은 [`../docs/api/data-management.md`](../docs/api/data-management.md), 설치·정책은 [`dms-05-dm-jobs.md`](dms-05-dm-jobs.md).

---

## 8. 노드 에이전트 (`DMS_AGENT_*`)

에이전트 ConfigMap은 `install/kubernetes/agent-daemonset.yaml`의 `dms-agent-runtime-config`에 있다.

| 변수 | 기본값 | 운영 필수 | 설명 |
| --- | --- | --- | --- |
| `DMS_AGENT_API_URL` | 없음 | 예 | 에이전트 클러스터에서 접근하는 DMS API URL. |
| `DMS_AGENT_CLUSTER_NAME` | 없음 | 예 | logical cluster name. storage mapping·kubeconfig JSON key와 일치. |
| `DMS_AGENT_WORKER_ROLE` | 없음 | 예 | `RM` 또는 `DM`. |
| `DMS_AGENT_MOUNTINFO_PATH` | `/proc/self/mountinfo` | 컨테이너 배포시 사실상 필수 | 마운트 존재/Ready 판정에 읽는 mount table. 기본값은 **컨테이너 자신의 마운트**라 노드 스토리지가 안 보여 **모든 storage Missing → readiness false**. 아래 bind-mount로 `/host/proc/1/mountinfo`를 가리켜야 함. |
| `DMS_AGENT_HOST_ROOT` | 설정 안 됨(권장 `/host`) | 아니오 | 호스트 root fs 마운트 경로. per-node/mount 용량(statvfs) 리포트용. readiness 자체는 mountinfo로 판정. |
| `DMS_AGENT_IDENTITY_USERS` | 없음 | DM 에이전트 권장(베이스라인) | NSS로 상시 확인할 POSIX user **베이스라인** 목록(쉼표). 여기에 없어도 **온디맨드 프로빙**이 보충한다: dm-worker가 신원 resolve 시 요청자를 probe 대상으로 등록하고, agent가 report POST 응답(`identity_probe_targets`)으로 받아 다음 사이클에 프로빙 — 신규 요청자도 목록 편집 없이 증거 확보. 프로빙은 계층형: 호스트 chroot-getent(특권 agent) → 호스트 `/etc/passwd` 파일(host-root 마운트) → 컨테이너 NSS. |
| `DMS_DM_IDENTITY_PROBE_WAIT_SECONDS` | `90` | 선택 | 신원 resolve 직후, 어떤 fresh DM 노드에도 해당 사용자 증거가 없을 때 dm-worker가 증거 도착을 기다리는 최대 시간(초). 첫 요청인 사용자도 한 번에 통과하도록 agent 사이클(기본 60s)보다 크게. `0` = 대기 없음(등록만). |
| `DMS_DM_IDENTITY_PROBE_POLL_SECONDS` | `5` | 선택 | 위 대기 중 fresh 리포트 재확인 주기(초). |
| `DMS_DM_IDENTITY_PROBE_TARGET_TTL_SECONDS` | `3600` | 선택 | 등록된 probe 대상이 agent에 배포되는 유효기간(초). 만료 행은 조회 시 정리. |
| `DMS_AGENT_REPORT_INTERVAL_SECONDS` | `60` | 아니오 | report 주기. |
| `DMS_AGENT_REPORT_TIMEOUT_SECONDS` | `5` | 아니오 | report POST timeout. |
| `DMS_AGENT_TOOLS` | `dsync,nsync,drm,dscan,kubectl` | 아니오 | tool probe 목록(쉼표). |
| `DMS_AGENT_CREDENTIAL_FILES` | 설정 안 됨 | 아니오 | report할 credential path 목록(쉼표). |
| `DMS_AGENT_NETWORK_ENDPOINTS` | 설정 안 됨 | 아니오 | probe할 network endpoint 목록(쉼표). |
| `DMS_AUTH_SHARED_TOKEN` | 없음 | 조건부 | API token이 켜져 있으면 report POST에 사용. |

**마운트 readiness — 호스트 mountinfo bind-mount (필수).** 에이전트는 컨테이너에서 돌기 때문에, `/proc/self/mountinfo`만 보면 노드 스토리지 마운트가 전혀 안 보여 전부 Missing이 된다. `agent-daemonset.yaml`에 다음이 있어야 한다:

```yaml
# ConfigMap dms-agent-runtime-config
DMS_AGENT_MOUNTINFO_PATH: "/host/proc/1/mountinfo"
DMS_AGENT_HOST_ROOT: "/host"
# 각 DaemonSet pod
volumeMounts:
  - { name: host-root,      mountPath: /host,                   readOnly: true }
  - { name: proc-mountinfo, mountPath: /host/proc/1/mountinfo,  readOnly: true }
volumes:
  - { name: host-root,      hostPath: { path: /,                 type: Directory } }
  - { name: proc-mountinfo, hostPath: { path: /proc/1/mountinfo, type: File } }
```

`host-root`만으로는 안 된다 — `/proc`는 별도 마운트라 `/host` 아래로 안 딸려오므로 `/proc/1/mountinfo`를 **따로** bind-mount한다. 검증: `kubectl -n dms exec <agent-pod> -- dms agent-probe --once | jq '.mounts[] | {storage_name,status}'` — 실제 마운트된 것만 `Ready`면 정상.

> **actor 주의.** 현재 mTLS-required 프로필에서 agent report를 Fresh로 저장하려면 agent request의 authenticated actor가 `node:{cluster_name}:{node_name}`과 일치해야 한다. 기본 mTLS derivation은 `mtls:<subject>`이므로, agent certificate subject→node actor mapping(또는 동등한 internal auth boundary)이 별도로 필요하다.

---

## 9. env-var만으로는 부족 — 함께 적용할 RBAC

아래 RBAC가 없으면 값이 옳아도 조용히 no-op하거나 Forbidden으로 실패한다. 매니페스트 편집·적용은 [`dms-02-core.md`](dms-02-core.md)·[`dms-04-rm-k8s-quota.md`](dms-04-rm-k8s-quota.md).

- **`dms-agent-storages-sync`** (Role/RoleBinding, `control-plane.yaml`, `configmaps get/update/patch` on `dms-agent-storages`, bound `dms-api` + `dms-remote`). 없으면 dms-api의 storage-mapping→ConfigMap sync가 **조용히 no-op**(Forbidden을 warning으로 삼킴) → 새 filesystem storage가 에이전트에 도달하지 못해 RM `missing_rm_readiness`, DM `no_ready_dm_candidate`. 에이전트는 `storages.json`을 **startup에 1회만** 읽으므로, mapping 변경 후에는 `POST /api/v1/agent/rollout-restart`로 DaemonSet을 rollout-restart한다.
- **`dms-api-volcano-rbac.yaml`** (`install/kubernetes/dms-api-volcano-rbac.yaml` — **control-plane.yaml에 없음, 별도 적용**). dms-api에 `pods/log` + volcano read를 부여해 `GET /operations/data-jobs/{id}/logs`(포탈 로그 tail)를 가능하게 한다.
- **`dms-dm-volcano`**(dm-worker의 `batch.volcano.sh` Job + `scheduling.volcano.sh` PodGroup), **`dms-api-dm-terminate`**(`data.cancel`이 VolcanoJob을 실제 `kubectl delete`), **`dms-api-agent-rollout`**(rollout-restart) — 모두 `control-plane.yaml`에 포함. dms-api가 control 클러스터를 kubeconfig로 접근하면 그 kubeconfig가 인증하는 SA(예: `dms-remote`)에도 rollout/storages-sync rule을 grant한다.

---

## 다음 문서

- [`dms-01-prerequisites.md`](dms-01-prerequisites.md) — 클러스터/외부 사전 준비(Volcano·Queue·PriorityClass·privileged ns·NSS/SSSD·공유 RWX·host-mount)
- [`dms-02-core.md`](dms-02-core.md) — 코어 배포(이미지 빌드·Secret·control-plane·mTLS·ingress·migration), 파일별 편집 목록
- [`dms-03-rm-filesystem.md`](dms-03-rm-filesystem.md) — 파일시스템 RM 설정(backend별 LDAP·SSH host-exec·storage mapping)
- [`dms-04-rm-k8s-quota.md`](dms-04-rm-k8s-quota.md) — k8s 네임스페이스 쿼터 RM 설정(mutation transport·CSI sanity)
- [`dms-05-dm-jobs.md`](dms-05-dm-jobs.md) — DM(데이터 잡) 설정(이미지·정책·에이전트)
- [`../docs/api/README.md`](../docs/api/README.md) — DMS API 개요 + 인증
- [`../docs/operations-runbook.md`](../docs/operations-runbook.md) — 운영 런북
