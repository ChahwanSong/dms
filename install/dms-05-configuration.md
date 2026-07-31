# DMS 환경변수 레퍼런스

DMS 프로세스(api/planner/dm-worker/retention/sanity)와 노드 에이전트가 읽는
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

**운영 인증은 두 평면이 기본이다.** ① **외부 평면**(운영자·포탈) = mTLS-verified header profile —
`dms-api`가 `DMS_REQUIRE_MTLS_HEADER=true` + `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`로 켠다(아래).
② **내부 평면**(노드 에이전트 + 포탈 BFF) = mTLS **off** + shared token — 전용 `dms-api-internal`로,
이 신뢰 in-cluster 클라이언트들이 mTLS로는 각각 `node:{cluster}:{node}`·per-operator `x-dms-actor`를
낼 수 없기 때문이다(상세·근거는 §7, 매니페스트 `install/kubernetes/dms-api-internal.yaml`). 아래는
**외부 평면(mTLS)** 흐름이다.

**외부 평면 = mTLS-verified header profile.** `control-plane.yaml`의 ConfigMap `dms-runtime-config`가
`DMS_REQUIRE_MTLS_HEADER=true` + `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`로 켠다. 흐름:

1. 신뢰된 ingress/edge proxy가 client certificate를 **검증**하고 그 결과(subject + verify=SUCCESS)를 upstream header로 전달한다.
2. DMS는 actor를 **certificate subject에서 derive**한다. prefix는 `DMS_MTLS_ACTOR_PREFIX`(기본 `mtls:`) → 예: `mtls:CN=alice,...`.
3. **평문 `x-dms-actor`는 신뢰하지 않는다.** 평문 actor가 mTLS로 derive한 actor와 다르면 인증을 거부한다.
4. `DMS_DEFAULT_ACTOR`는 **비어 있어야 한다**. `DMS_REQUIRE_MTLS_HEADER=true`인데 값이 있으면 **API startup이 실패**한다.
5. shared bearer token(`DMS_AUTH_SHARED_TOKEN`)은 **기본 배포에서 필수**다 — mTLS 위에 gate로 얹혀 모든 API 호출이 함께 보내며, 내부 평면 `dms-api-internal`(mTLS **off**)의 유일한 인증이라 shipped `dms-secrets`가 이를 싣는다. 비우면 내부 평면·agent·포탈이 인증 불가.

운영 curl은 `--cert client.crt --key client.key --cacert ca.crt` **+ `Authorization: Bearer <token>`**(기본 필수)로 호출하고, **`x-dms-actor`를 보내지 않는다**.

| 변수 | 기본값 | 운영 필수 | 설명 |
| --- | --- | --- | --- |
| `DMS_REQUIRE_MTLS_HEADER` | `true` | 예 | trusted ingress/edge proxy가 전달한 client cert subject evidence header를 요구한다. |
| `DMS_REQUIRE_MTLS_VERIFIED_HEADER` | `true` | 예 | client cert verify 결과가 `SUCCESS`여야 한다. `true`면 `DMS_REQUIRE_MTLS_HEADER=true`도 필수. |
| `DMS_MTLS_ACTOR_PREFIX` | `mtls:` | 아니오 | mTLS subject에서 derive한 actor prefix. |
| `DMS_DEFAULT_ACTOR` | 비어 있음 | — | mTLS 프로필에서는 **반드시 비운다**(`DMS_DEFAULT_ACTOR=` 가능). 비어 있지 않으면 startup 실패. |
| `DMS_AUTH_SHARED_TOKEN` | 없음(Secret) | **예(필수)** | shared bearer token. mTLS 위에 gate로 얹혀 모든 API 호출이 함께 보내며, 내부 평면 `dms-api-internal`(mTLS off)의 유일한 인증. agent·포탈·스크립트가 사용. |

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
| `DMS_DB_POOL_MIN_SIZE` | `1` | **API** pool의 floor(미리 열어둘 최소 connection). 첫 요청 cold-connect 지연을 없앤다. loop에는 적용되지 않는다(아래 `WORKER` 항목). 관측은 max로 clamp. |
| `DMS_DB_WORKER_POOL_MIN_SIZE` | `0` | **loop 프로세스**(planner/dm-worker/sanity/retention) pool의 floor. 기본 `0` — idle 워커는 커넥션을 0개로 반납하고 필요시에만 연결(idle은 `max_idle`≈600초 후 reap). 워커를 많이 띄울 때 idle obs floor(=replica당 1개)를 없애 예산을 절약한다. cold-connect 지연이 문제면 `1`로 올린다. |
| `DMS_DB_POOL_MAX_SIZE` | `4` | loop 프로세스(planner/dm-worker/sanity/retention)의 운영 DB pool 최대치. 단일 스레드라 작게 둔다. |
| `DMS_DB_API_POOL_MAX_SIZE` | `16` | API 프로세스의 운영 DB pool 최대치. API sync-handler 스레드풀이 이 값으로 cap된다. |
| `DMS_DB_OBSERVABILITY_POOL_MAX_SIZE` | `3` | 관측 DB pool 최대치(쓰기 부하 가벼워 작게). |
| `DMS_DB_POOL_TIMEOUT_SECONDS` | `35` | pool 만석 시 checkout 대기 최대(초). **`DMS_DB_STATEMENT_TIMEOUT_MS`(초 환산) 이상**이어야 한다. |
| `DMS_DB_STATEMENT_TIMEOUT_MS` | `30000` | pooled connection `statement_timeout`(ms). runaway 쿼리 강제 종료. |
| `DMS_DB_IDLE_IN_TXN_TIMEOUT_MS` | `60000` | pooled connection `idle_in_transaction_session_timeout`(ms). 누수 트랜잭션 강제 종료. |

**천장(ceiling) 공식**: `서버 PG connection ≤ Σ프로세스(op_max + obs_max)`.

**주의 — 프로세스 수는 replica 수로 세야 한다.** 출하 매니페스트 기준으로 `dms-api`는 replicas 2,
`dms-dm-worker`는 **replicas 32**(`control-plane.yaml`)이며, `dms-api-internal`은 API와 같은 풀
설정을 쓰는 별도 Deployment다. 따라서 기본값 기준 실제 천장은

```
dms-api            2 × (16+3) =  38
dms-api-internal   1 × (16+3) =  19
dms-dm-worker     32 × ( 4+3) = 224
planner·sanity·retention  3 × ( 4+3) =  21
                                 -----
                                   302  (+ superuser_reserved 3, + 일시적 migrate Job)
```

즉 **stock `max_connections=100`으로는 부족하다** — 32-replica 기본 배치는 `max_connections=400`
급을 전제로 한다([`dms-01 §3.4`](dms-01-prerequisites.md)). 이는 *모든 풀이 동시에 max까지 차는* 상한이며, **실측 정상상태는 훨씬 낮다** — loop는 `--interval`(5초) 폴링으로 op를 ~1개씩만 잡고, `DMS_DB_WORKER_POOL_MIN_SIZE=0`이라 idle obs floor가 0이다(min_size=0은 idle 커넥션을 reap해 커넥션 수를 워커 수와 사실상 분리한다). 동시성을 키울 땐 `DMS_DB_API_POOL_MAX_SIZE`와 PostgreSQL `max_connections`를 **함께** 올린다([`dms-02-core.md`](dms-02-core.md) DB 섹션, 워커 대량 확장은 [`dms-01 §3.4`](dms-01-prerequisites.md)). migration/대량 유지보수는 unpooled로 실행되어 위 timeout 영향을 받지 않는다.

---

## 3. 코어 / 워커 런타임

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_WORKER_LEASE_SECONDS` | `300` | planner/DM worker의 claim lease. DM worker는 backend call 중 heartbeat로 갱신. |
| `DMS_RECOVERY_SWEEP_LEASE_SECONDS` | `30` | 주기 복구 스윕(stale run 정리·orphan run 닫기 등)의 **단일-리더 lease**. 여러 worker replica 중 이 lease 보유자 1명만 스윕을 돌려 N× 중복을 없앤다. 짧게 둬 잡 실행으로 바빠진 리더가 빨리 인계하게 한다(`component_leases` 테이블). |
| `DMS_PREVIEW_TTL_SECONDS` | `86400` | `sync`/`rm` preview가 `ConfirmPending`으로 유지되는 TTL. `scan`은 confirm 없이 read-only. |
| `DMS_AGENT_REPORT_STALE_SECONDS` | `300` | storage-mapping readiness의 agent report freshness window. |
| `DMS_CONTROL_CLUSTER_NAME` | `cluster-a` | DM readiness·inventory aggregation에 쓰는 control cluster name. |
| `DMS_DATA_JOB_ATTENTION_WINDOW_SECONDS` | `604800` (7일) | 종료된 데이터 잡이 "조치 필요"에 남아 있는 시간(초). `0`이면 창 없음(조건에 맞는 잡 전부 노출). 잡 row 자체는 이력으로 보존되고, 알람만 이 창으로 제한된다. |

### 2.1 sanity reconciler · planner 게이트

storage mapping의 `readiness`는 **마지막 검사 결과가 저장된 값**이라 주기적으로 갱신되지 않으면
양방향으로 낡는다(낡은 `Missing`이 정상 작업을 막고, 낡은 `Ready`가 사라진 agent를 가린다).
`dms sanity-reconciler --loop`가 이를 새로 고치며, 아래 값들은
[`kubernetes/sanity-reconciler.yaml`](kubernetes/sanity-reconciler.yaml)이 설정한다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_SANITY_RECONCILE_ENABLED` | `true` | 리컨실러 스윕 활성화. `false`면 루프가 아무 것도 하지 않는다(readiness는 등록/`:check` 시점에만 갱신). |
| `DMS_SANITY_RECONCILE_INTERVAL_SECONDS` | `30` | 스윕 주기(초). 한 스윕은 클러스터 인벤토리를 **1회** 읽어 전 매핑에 재사용한다. |
| `DMS_SANITY_RECONCILE_HEARTBEAT_PATH` | 설정 안 됨 | 매 사이클 갱신되는 heartbeat 파일 경로. k8s liveness probe가 이 파일의 age를 본다. |
| `DMS_SANITY_TTL_SECONDS` | `120` | readiness를 신뢰할 수 있는 최대 나이(초). 아래 planner 게이트가 이 값을 쓴다. |
| `DMS_SANITY_PLANNER_GATE_ENABLED` | `false` | `true`면 planner가 **`Ready`지만 TTL보다 오래된** readiness를 fail-closed로 거부한다(`dm_readiness_stale`). 리컨실러를 먼저 배포·정상 확인한 뒤 켠다 — 반대로 하면 모든 데이터 잡이 막힌다. |
| `DMS_SANITY_EVENT_RECOMPUTE_ENABLED` | `false` | `true`면 agent 리포트 수신 시 그 노드가 보고한 storage의 readiness를 즉시 재계산한다(주기 스윕을 기다리지 않음). 리포트 처리 경로에 부하를 더하므로 기본은 꺼져 있다. |

> **DM worker 수평 확장.** `dms-dm-worker`는 **replicas를 늘리면 최대 그 수만큼 잡을
> 동시 실행**한다(각 워커가 잡 1개를 claim→완료까지 처리). claim은 `FOR UPDATE SKIP LOCKED`(PostgreSQL)라
> N개 워커가 **서로 다른** plan을 원자적으로 집어 경합·중복 claim·`not claimable` 로그 노이즈가 없고, 주기
> 복구 스윕은 **리더 1명만** 돈다(`DMS_RECOVERY_SWEEP_LEASE_SECONDS`). 확장 시 함께 볼 것:
> - **DB 연결 예산**: loop 프로세스당 최대 `DMS_DB_POOL_MAX_SIZE`(기본 4) connection. `replicas × 4 +
>   API/기타`가 PostgreSQL `max_connections`(스톡 100)를 넘지 않게 한다(초과 시 checkout timeout). 워커는
>   single-thread라 `DMS_DB_POOL_MAX_SIZE=2`로 낮춰 상한을 반감해도 된다.
> - **동시 잡 수 상한** = `min(replicas, 노드 용량 ÷ 잡당 파드 수)`. 그 이상 replica는 idle 폴링·연결만 늘린다.
> - 포탈 배치는 `PORTAL_BACKUP_CONCURRENCY`(기본 8)만큼 제출하므로 실제 병렬을 원하면 replicas와 **맞춘다**.

**agent_reports history 보존** (`dms retention --loop`). 100+ node가 분당 1회 보고하면 history가 수백만 행으로 자란다. node-health는 `agent_node_current`(node별 최신 1행)에서 읽으므로 history는 node-metrics 시계열용이고 나이 기준 prune이 안전하다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_AGENT_REPORT_RETENTION_SECONDS` | `2592000`(30일) | 이보다 오래된 `agent_reports` 행을 prune. **parse 시 7일(604800) 이상으로 floor** — sparkline window 아래로 못 내려감. |
| `DMS_AGENT_REPORT_RETENTION_INTERVAL_SECONDS` | `3600` | retention loop 주기. |
| `DMS_AGENT_REPORT_RETENTION_HEARTBEAT_PATH` | 설정 안 됨 | 설정 시 매 cycle heartbeat 파일 기록 → k8s livenessProbe가 loop hang 감지/재시작. |

---

## 4. LDAP / identity

DM의 요청자 POSIX 신원 resolve가 이 `DMS_LDAP_*` 값을 쓴다. DMS는 이 디렉토리를 **read-only로만** 조회하며 계정·그룹을 만들지 않는다. DM 신원 게이트는 [`dms-04-dm-jobs.md §3·§4`](dms-04-dm-jobs.md).

| 변수 | 기본값 | 운영 필수 | 설명 |
| --- | --- | --- | --- |
| `DMS_LDAP_URI` | `ldap://ldap.example.internal:389` | 예(DM) | LDAP URI. |
| `DMS_LDAP_BASE_DN` | `dc=example,dc=internal` | 예 | Base DN. |
| `DMS_LDAP_BIND_DN` | 없음(Secret) | 예 | Bind DN. Secret 저장. |
| `DMS_LDAP_BIND_PASSWORD` | 없음(Secret) | 예 | Bind password. Secret 저장. |
| `DMS_LDAP_USER_SEARCH_BASE` | `ou=people,<baseDN>` | 아니오 | 사용자 검색 base. |
| `DMS_LDAP_GROUP_SEARCH_BASE` | `ou=groups,<baseDN>` | 아니오 | 그룹 검색 base. |
| `DMS_LDAP_USER_FILTER` | `(uid={username})` | 아니오 | 사용자 필터. DM identity lookup도 이 필터를 쓴다. |
| `DMS_LDAP_TIMEOUT_SECONDS` | `5` | 아니오 | LDAP timeout. |
| `DMS_DM_IDENTITY_PROVIDER` | `ldap` | 아니오 | DM identity provider. |

DM identity는 별도 mapping 등록 없이 dm-worker preflight에서 위 `DMS_LDAP_*`로 `owner_username`(기본 `requester_id`)을 read-only lookup해 해석한다. 캐시가 없어 **fail closed** — LDAP가 응답하지 않으면 그 job preflight가 `ldap_unavailable`로 실패한다.

---

## 5. 스토리지 인벤토리 (Kubernetes 읽기)

DMS는 등록된 클러스터의 StorageClass·CSI driver·노드를 **읽기 전용**으로 수집해 storage mapping sanity를 판정한다. 대상 클러스터에는 아무것도 만들거나 바꾸지 않는다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_KUBERNETES_INVENTORY_MODE` | `kubectl` | 읽기 전용 inventory mode. `kubectl` / `ssh-kubectl` / `python-client`. API 등록 sanity와 sanity-reconciler가 이 mode로 클러스터를 읽어 agentless managed cluster의 CSI mapping을 검증. 클러스터별 격리(한 클러스터 실패가 전체 inventory를 무력화하지 않음). |
| `DMS_CLUSTER_KUBECONFIGS_JSON` | 설정 안 됨 | cluster name → kubeconfig path JSON. current-context를 안 쓰는 `kubectl` mode에 필요. |
| `DMS_CLUSTER_CONTROL_HOSTS_JSON` | 설정 안 됨 | cluster name → SSH host JSON. **여기 등록된 클러스터는 `DMS_KUBERNETES_INVENTORY_MODE`와 무관하게** `ssh <host> kubectl ...`로 읽는다(`adapters/inventory.py`의 per-cluster transport). 즉 mode가 `kubectl`이어도 이 항목이 있으면 그 클러스터에는 SSH 키가 필요하다. kubeconfig로 직접 도달 가능한 클러스터는 **여기에 넣지 않는 편이 낫다** — 넣는 순간 SSH 의존이 생긴다. |
| `DMS_KUBERNETES_INVENTORY_TIMEOUT_SECONDS` | `10` | inventory read timeout. |
| `DMS_KUBERNETES_MUTATION_TIMEOUT_SECONDS` | `30` | **DM 전용** — dm-worker가 Volcano 잡을 kubectl로 다룰 때의 timeout(`adapters/volcano.py`). |

> **SSH가 필요한 경우** — `DMS_KUBERNETES_INVENTORY_MODE=ssh-kubectl`이거나, mode와 무관하게
> `DMS_CLUSTER_CONTROL_HOSTS_JSON`에 그 클러스터가 등록된 경우 — dms-api·dms-api-internal·
> dms-sanity-reconciler 파드에 해당 bastion 접속용 SSH 키를 **직접 마운트**해야 한다. 컨트롤
> 플레인은 더 이상 SSH 키 Secret을 기본 제공하지 않는다.
>
> **키가 없으면 그 클러스터는 `cluster_missing`으로 sanity `Failed`가 되고**, 그 클러스터의 CSI
> 매핑은 플래너의 admission gate에 걸려 데이터 잡을 받지 못한다. 실제로 테스트베드에서 이 조합
> (mode=`kubectl` + control_host 지정 + 키 없음)으로 재현했다. 클러스터 등록 절차는
> [`dms-03-storage-mappings.md §3`](dms-03-storage-mappings.md).

---

## 6. 데이터 관리(DM) 잡

DM 잡은 [`dms-01-prerequisites.md`](dms-01-prerequisites.md)의 클러스터 prereq 위에서만 실행된다: **Volcano** 설치 + `Queue dms-data` + `PriorityClass dms-low/normal/high`(`install/kubernetes/volcano-queue-priorityclasses.yaml`), DM 네임스페이스 `PodSecurity=privileged`, dm-worker와 **모든 DM 잡 노드에 동일 경로로 마운트된 공유 RWX artifact FS**, 노드 NSS/SSSD, 스토리지 host-mount. queue가 없으면 잡이 영구 Pending, PriorityClass가 없으면 pod가 admission에서 거부된다.

`dms-dm-worker` Deployment **replicas=32(매니페스트 기본) = DM enabled · 최대 32-way 동시 실행** (32는 `max_connections≥400` 전제 — 규모에 맞게 조정, 위 §3 "DM worker 수평 확장"). `0`은 DM을 **의도적으로 끌 때만** — 0이면 어떤 worker도 data job을 claim하지 않아 `scan`/`sync`/`rm`이 큐에 쌓인 채 실행되지 않는다(정상 상태 아님).

### 이미지 (DMS_DM_JOB_IMAGE)

이미지 3종의 빌드 순서·명령은 [`dms-02-core.md §1`](dms-02-core.md), 그 ref를 어디에 넣는지는
[`dms-04-dm-jobs.md §2`](dms-04-dm-jobs.md)에 있다. `:CHANGE_ME` placeholder가 fail-closed되지 않는
트랩은 dms-04 §2.1에 설명돼 있다.

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

> 위 타임아웃은 잡이 **정상 실행 중 너무 오래 걸릴 때**의 상한이다. 런처가 **실패**하면(예: MPI
> 기동 오류) VolcanoJob이 `PodFailed → AbortJob` 정책으로 즉시 종료되어 dm-worker가 수 초 내 실패
> 처리한다 — 타임아웃까지 "Running"으로 매달리지 않는다. 또한 런처는 워커 sshd가 실제로 응답할
> 때까지 대기(SSH 준비 배리어)한 뒤 mpirun을 띄우므로 sshd 기동 레이스로 인한 간헐적 실패를 피한다.

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
| `DMS_DM_IDENTITY_PROBE_WAIT_SECONDS` | `90` | **온디맨드 프로빙** — 신원 resolve 직후 어떤 fresh DM 노드에도 그 사용자 증거가 없으면 dm-worker가 증거 전파를 기다리는 최대 시간(초). 첫 요청 사용자도 한 번에 통과하도록 agent report 주기(`DMS_AGENT_REPORT_INTERVAL_SECONDS`, 기본 60s)보다 크게 — 베이스라인 없이 순수 온디맨드로 갈 땐 2×(≈150s) 권장. `0`=대기 없음(등록만). |
| `DMS_DM_IDENTITY_PROBE_POLL_SECONDS` | `5` | 위 대기 중 fresh 리포트 재확인 주기(초). |
| `DMS_DM_IDENTITY_PROBE_TARGET_TTL_SECONDS` | `3600` | 등록된 probe 대상을 agent에 배포하는 유효기간(초). 만료 행은 조회 시 정리. 동시 활성 요청자가 많아도 상한(최근순 100)만 프로빙하므로 대규모 디렉터리에서도 부하 일정. |
| `DMS_DM_ALLOW_ROOT_REQUESTER` | `true` | 운영자 root(privileged) 실행 허용. requester_id/owner_username이 `DMS_DM_PRIVILEGED_REQUESTERS`에 속하면 uid/gid 0으로 합성 실행(LDAP·uid floor 우회). **`DMS_REQUIRE_MTLS_VERIFIED_HEADER=true` 경로에서만 실동작**(평문 root 요청은 403). denylist는 kill-switch로 유지. |
| `DMS_DM_PRIVILEGED_REQUESTERS` | `root` | 합성 root로 실행할 requester allowlist(쉼표). |
| `DMS_DM_PRIVILEGED_UID` / `DMS_DM_PRIVILEGED_GID` | `0` / `0` | privileged 요청에 합성할 uid/gid. |
| `DMS_DM_PRIVILEGED_OPERATORS` | 비어 있음 | root 요청 가능한 operator actor allowlist(쉼표). 비우면 **mTLS-verified operator 전체** 허용. |
| `DMS_DM_PRIVILEGED_SCOPES` | 비어 있음 | root 잡 허용 scope(쉼표): `storage` 또는 `storage:path-prefix`. 비우면 전체 storage. |

DM 잡 사용법(preview/confirm 플로우, 파라미터)은 [`../docs/api/data-management.md`](../docs/api/data-management.md), 설치·정책은 [`dms-04-dm-jobs.md`](dms-04-dm-jobs.md).

---

## 7. 노드 에이전트 (`DMS_AGENT_*`)

에이전트 ConfigMap은 `install/kubernetes/agent-daemonset.yaml`의 `dms-agent-runtime-config`에 있다.

| 변수 | 기본값 | 운영 필수 | 설명 |
| --- | --- | --- | --- |
| `DMS_AGENT_API_URL` | 없음 | 예 | 에이전트 클러스터에서 접근하는 DMS API URL. |
| `DMS_AGENT_CLUSTER_NAME` | 없음 | 예 | logical cluster name. storage mapping·kubeconfig JSON key와 일치. |
| `DMS_AGENT_WORKER_ROLE` | 없음 | 예 | `DM`(현재 유일한 worker role). |
| `DMS_AGENT_MOUNTINFO_PATH` | `/proc/self/mountinfo` | 컨테이너 배포시 사실상 필수 | 마운트 존재/Ready 판정에 읽는 mount table. 기본값은 **컨테이너 자신의 마운트**라 노드 스토리지가 안 보여 **모든 storage Missing → readiness false**. 아래 bind-mount로 `/host/proc/1/mountinfo`를 가리켜야 함. |
| `DMS_AGENT_HOST_ROOT` | 설정 안 됨(권장 `/host`) | DM 에이전트 사실상 필수 | 호스트 root fs 마운트 경로(`/host` bind-mount, 아래 참조). ① per-node/mount 용량(statvfs) 리포트, ② **온디맨드 신원 프로빙의 호스트 해석 루트** — `chroot $HOST_ROOT getent`(SSSD/LDAP 유저 — agent 컨테이너가 **root + `SYS_CHROOT`**여야 하며 출하 매니페스트는 그렇지 않다, dms-04 §3)와 `$HOST_ROOT/etc/passwd`(노드-로컬 유저) 계층이 이 경로를 쓴다. 미설정 시 두 호스트 계층이 건너뛰어져 **컨테이너 NSS만** 남아 노드 사용자를 해석 못 한다. readiness 판정 자체는 mountinfo. |
| `DMS_AGENT_IDENTITY_USERS` | 없음 | DM 에이전트 권장(베이스라인) | NSS로 상시 확인할 POSIX user **베이스라인** 목록(쉼표). 여기에 없어도 **온디맨드 프로빙**이 보충한다: dm-worker가 신원 resolve 시 요청자를 probe 대상으로 등록하고, agent가 report POST 응답(`identity_probe_targets`)으로 받아 다음 사이클에 프로빙 — 신규 요청자도 목록 편집 없이 증거 확보. 프로빙은 계층형: 호스트 `chroot /host getent`(SSSD/LDAP 유저용 — agent 컨테이너가 **root + `SYS_CHROOT`**여야 하며 출하 매니페스트는 그렇지 않다, dms-04 §3) → 호스트 `/etc/passwd` 파일(host-root 마운트) → 컨테이너 NSS. **온디맨드 튜닝(`DMS_DM_IDENTITY_PROBE_*`)은 agent가 아니라 dm-worker 설정 → §6**. |
| `DMS_AGENT_REPORT_INTERVAL_SECONDS` | `60` | 아니오 | report 주기. |
| `DMS_AGENT_REPORT_TIMEOUT_SECONDS` | `5` | 아니오 | report POST timeout. |
| `DMS_AGENT_TOOLS` | `dsync,nsync,drm,dscan,kubectl` | 아니오 | tool probe 목록(쉼표). |
| `DMS_AGENT_CREDENTIAL_FILES` | 설정 안 됨 | 아니오 | report할 credential path 목록(쉼표). |
| `DMS_AGENT_NETWORK_ENDPOINTS` | 설정 안 됨 | 아니오 | probe할 network endpoint 목록(쉼표). |
| `DMS_AUTH_SHARED_TOKEN` | 없음 | 필수 | 에이전트가 내부 API `dms-api-internal`(mTLS off)로 report POST할 때의 인증. 내부 평면의 유일한 자격증명이라 필수. |

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

> **내부 신뢰 평면 = agent + 포탈 BFF (기본 배포).** 두 in-cluster 클라이언트가 외부 mTLS 프로필로는
> 인증할 수 없다: ① **노드 에이전트** — report ingestion이 actor `node:{cluster}:{node}`를 요구하는데
> mTLS는 `mtls:<subject>`로 도출(평문 `x-dms-actor` 거부); ② **포탈 BFF** — 다중 운영자 신원을
> `x-dms-actor: mtls:<operator>`로 실어 나르는데 BFF cert 하나면 전원이 단일 actor로 뭉개진다. 그래서
> **기본 배포는 인증을 두 평면으로 나눈다**: **외부**(직접 운영자·자동화) = mTLS API `dms-api`(개별
> client cert), **신뢰 in-cluster 클라이언트**(agent + 포탈) = 전용 내부 API **`dms-api-internal`** —
> mTLS **off** + shared token(`DMS_AUTH_SHARED_TOKEN`) + NetworkPolicy(agent DaemonSet + `dms-portal` ns
> 만, ClusterIP)로 `x-dms-actor`를 신뢰한다. 같은 코드·같은 DB, auth 프로필만 다르다. 매니페스트
> `install/kubernetes/dms-api-internal.yaml`; `DMS_AGENT_API_URL`·포탈 `PORTAL_DMS_API_URL`이 이 내부
> 서비스를 가리킨다. 인증 흐름 전체는 §1.
>
> **보안 필수.** mTLS `dms-api`는 `ssl-client-*` evidence 헤더를 무조건 신뢰하므로, **cert를 종단하는
> ingress/proxy만** 닿게 해야 한다(control-plane.yaml의 `dms-api-from-ingress-only` NetworkPolicy) —
> 아니면 in-cluster 아무 파드나 evidence 헤더를 스푸핑해 operator actor를 위조할 수 있다.

---

## 8. env-var만으로는 부족 — 함께 적용할 RBAC

아래 RBAC가 없으면 값이 옳아도 조용히 no-op하거나 Forbidden으로 실패한다. 매니페스트 편집·적용은 [`dms-02-core.md`](dms-02-core.md)·[`dms-03-storage-mappings.md`](dms-03-storage-mappings.md).

- **`dms-agent-storages-sync`** (Role/RoleBinding, `control-plane.yaml`, `configmaps get/update/patch` on `dms-agent-storages`, bound `dms-api` + `dms-remote`). 없으면 dms-api의 storage-mapping→ConfigMap sync가 **조용히 no-op**(Forbidden을 warning으로 삼킴) → 새 filesystem storage가 에이전트에 도달하지 못해 DM이 `no_ready_dm_candidate`가 된다. 에이전트는 `storages.json`을 **startup에 1회만** 읽으므로, mapping 변경 후에는 `POST /api/v1/agent/rollout-restart`로 DaemonSet을 rollout-restart한다.
- **`dms-api-volcano-rbac.yaml`** (`install/kubernetes/dms-api-volcano-rbac.yaml` — **control-plane.yaml에 없음, 별도 적용**). dms-api에 `pods/log` + volcano read를 부여해 `GET /operations/data-jobs/{id}/logs`(포탈 로그 tail)를 가능하게 한다.
- **`dms-dm-volcano`**(dm-worker의 `batch.volcano.sh` Job + `scheduling.volcano.sh` PodGroup), **`dms-api-dm-terminate`**(`data.cancel`이 VolcanoJob을 실제 `kubectl delete`), **`dms-api-agent-rollout`**(rollout-restart) — 모두 `control-plane.yaml`에 포함. dms-api가 control 클러스터를 kubeconfig로 접근하면 그 kubeconfig가 인증하는 SA(예: `dms-remote`)에도 rollout/storages-sync rule을 grant한다.

---

## 다음 문서

- [`dms-01-prerequisites.md`](dms-01-prerequisites.md) — 클러스터/외부 사전 준비(Volcano·Queue·PriorityClass·privileged ns·NSS/SSSD·공유 RWX·host-mount)
- [`dms-02-core.md`](dms-02-core.md) — 코어 배포(이미지 빌드·Secret·control-plane·mTLS·ingress·migration), 파일별 편집 목록
- [`dms-03-storage-mappings.md`](dms-03-storage-mappings.md) — 스토리지 매핑(인벤토리) 등록(백엔드 타입·멀티 클러스터·readiness)
- [`dms-04-dm-jobs.md`](dms-04-dm-jobs.md) — DM(데이터 잡) 설정(이미지·정책·에이전트)
- [`../docs/api/README.md`](../docs/api/README.md) — DMS API 개요 + 인증
- [`../docs/operations-runbook.md`](../docs/operations-runbook.md) — 운영 런북
