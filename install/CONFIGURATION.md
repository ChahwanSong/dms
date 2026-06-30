# DMS 운영 설정 참조

이 문서는 실제 클러스터에 DMS를 설치하기 전에 검토해야 하는 설정을 정리한다.

## 설치 파일 편집 체크리스트

처음 설치할 때는 아래 파일들을 복사해서 운영 값으로 수정한다. Secret 값이 들어간 복사본은 git에 commit하지 않는다.

| 원본 파일 | 권장 복사본 | 반드시 수정할 값 |
| --- | --- | --- |
| `install/kubernetes/control-plane.yaml` | `/tmp/dms-control-plane.yaml` | image tag, DB URL, API token, LDAP, kubeconfig JSON, mTLS 설정 |
| `install/kubernetes/ingress.example.yaml` | `/tmp/dms-ingress.yaml` | hostname, TLS Secret, ingressClassName, client CA Secret |
| `install/kubernetes/agent-daemonset.yaml` | `/tmp/dms-agent-daemonset.yaml` | image tag, `DMS_AGENT_API_URL`, `DMS_AGENT_CLUSTER_NAME`, token |
| `install/config/storage-mappings.example.json` | `/tmp/dms-storage-mappings.json` | `storage_name`, backend type, StorageClass, mount path, SSH host |
| `install/config/agent-storages.example.json` | `/tmp/dms-agent-storages.json` | agent가 report할 storage, mount path, CSI driver |
| `install/config/default-quota-policies.example.json` | `/tmp/dms-default-quota-policies.json` | 기본 capacity/file/PVC quota |
| `install/config/identity-denylist.example.json` | `/tmp/dms-identity-denylist.json` | `subject_type` (requester\|owner\|group), `subject`, `reason` (kill-switch/admission block 대상) |
| `install/postgresql/init.sql` | `/tmp/dms-init.sql` | PostgreSQL role password |

`registry.example.internal/dms:CHANGE_ME`, `CHANGE_ME`, `dms.example.internal`, `postgres.example.internal`, `ldap.example.internal`, `cluster-a` 같은 placeholder가 남아 있으면 운영 배포 전에 반드시 교체한다.

확인 명령:

```bash
grep -R "CHANGE_ME\\|registry.example.internal/dms:CHANGE_ME\\|dms.example.internal\\|postgres.example.internal\\|ldap.example.internal" /tmp/dms-*.yaml /tmp/dms-*.json 2>/dev/null || true
```

위 명령 출력이 있으면 아직 바꿀 값이 남아 있다는 뜻이다.

## 런타임 환경변수

Core database와 인증:

| 변수 | 필수 여부 | 설명 |
| --- | --- | --- |
| `DMS_DATABASE_URL` | 예 | 운영용 PostgreSQL URL. Request, plan, run, resource, storage mapping, agent report, data job을 저장한다. |
| `DMS_OBSERVABILITY_DATABASE_URL` | 예 | Observability PostgreSQL URL. Diagnostic event를 저장한다. 운영 환경에서는 별도 DB를 사용한다. |
| `DMS_AUTH_SHARED_TOKEN` | 운영에서는 예 | API가 허용하고 agent/script가 사용하는 shared bearer token. 운영 환경에서는 mTLS evidence validation과 함께 사용한다. |

PostgreSQL connection pool (프로세스당 URL 단위 bounded pool; SQLite 에는 적용되지 않음):

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_DB_POOL_MIN_SIZE` | `1` | pool 이 미리 열어두는 최소 connection 수(운영 DB·관측 DB 공통; 관측 pool 은 max 보다 클 수 없어 자동 clamp). |
| `DMS_DB_POOL_MAX_SIZE` | `4` | **loop 프로세스(planner/rm-worker/dm-worker/sanity/retention)** 의 운영 DB pool 최대 connection 수. 이들은 단일 스레드라 한 번에 ~1개만 점유 → 작게 둔다. |
| `DMS_DB_API_POOL_MAX_SIZE` | `16` | **API 프로세스** 의 운영 DB pool 최대 connection 수(동시 요청 처리). API 의 sync-handler 스레드풀이 이 값으로 cap 되어 pool 을 oversubscribe 하지 않는다. 100+ 노드로 동시성을 키울 땐 이 값과 `max_connections` 를 함께 올린다. |
| `DMS_DB_OBSERVABILITY_POOL_MAX_SIZE` | `3` | **관측(observability) DB** pool 의 최대 connection 수(쓰기 부하가 가벼워 더 작게 둔다). |
| `DMS_DB_POOL_TIMEOUT_SECONDS` | `35` | pool 이 가득 찼을 때 connection checkout 을 기다리는 최대 시간(초). **반드시 `DMS_DB_STATEMENT_TIMEOUT_MS`(초 환산) 이상**이어야 한다 — 그래야 대기자가 느리지만 합법적인(타임아웃 이내) 쿼리가 끝날 때까지 기다린다. |
| `DMS_DB_STATEMENT_TIMEOUT_MS` | `30000` | pooled connection 의 `statement_timeout`(ms). runaway 쿼리를 강제 종료한다. |
| `DMS_DB_IDLE_IN_TXN_TIMEOUT_MS` | `60000` | pooled connection 의 `idle_in_transaction_session_timeout`(ms). 누수된 열린 트랜잭션을 강제 종료한다. |

사이징 공식(최악 천장): `서버 전체 PG connection ≤ Σ프로세스 (op_max + obs_max)`. 기본값 기준
API×2 = 2×(16+3), loop 5개(planner/rm-worker/dm-worker/sanity/retention) = 5×(4+3) →
38 + 35 = **73 < 100**(stock `max_connections` − superuser_reserved 3). loop 은 단일 스레드라
실제로는 ~1개씩만 점유하고, API 는 스레드풀이 `DMS_DB_API_POOL_MAX_SIZE` 로 cap 되어 그 이상
열지 않는다. 동시성을 키울 땐 **`DMS_DB_API_POOL_MAX_SIZE` 와 PostgreSQL `max_connections` 를 함께**
올린다(자세한 내용은 `install/1.install-dms-on-pvs.md` §1.4). migration 과 대량 유지보수는 pool 을
거치지 않는 unpooled connection(`pooled=False`)으로 실행되어 위 timeout 의 영향을 받지 않는다.
| `DMS_DEFAULT_ACTOR` | 아니오 | `x-dms-actor` header가 없을 때 사용할 fallback actor. 운영 환경과 mTLS-required mode에서는 사용하지 않는다. |
| `DMS_REQUIRE_MTLS_HEADER` | 운영에서는 예 | `true`이면 trusted ingress/edge proxy가 전달한 client certificate subject evidence header를 요구한다. |
| `DMS_REQUIRE_MTLS_VERIFIED_HEADER` | 운영에서는 예 | `true`이면 trusted ingress/edge proxy가 전달한 client certificate verify result가 `SUCCESS`여야 한다. |
| `DMS_MTLS_ACTOR_PREFIX` | 아니오 | mTLS subject에서 derive한 actor prefix. 기본값은 `mtls:`. |

mTLS evidence header:

- DMS edge proxy style: `X-DMS-Client-Cert-Subject`, `X-DMS-Client-Cert-Verify: SUCCESS`
- ingress-nginx style: `ssl-client-subject-dn`, `ssl-client-verify: SUCCESS`

두 header family가 동시에 들어오고 subject 또는 verify result가 충돌하면 API는 인증을 거부한다. mTLS-required mode에서는 `DMS_DEFAULT_ACTOR`를 fallback으로 쓰지 않고, `x-dms-actor`가 mTLS subject에서 derive한 actor와 다르면 인증을 거부한다.

Startup sanity check:

- `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`이면 `DMS_REQUIRE_MTLS_HEADER=true`도 반드시 필요하다.
- `DMS_REQUIRE_MTLS_HEADER=true`일 때 `DMS_DEFAULT_ACTOR`가 비어 있지 않으면 API startup이 실패한다.
- 운영 profile에서는 `DMS_DEFAULT_ACTOR`를 설정하지 않는다. `DMS_DEFAULT_ACTOR=`처럼 빈 값은 허용된다.

운영 curl/helper 호출에서 필요한 client-side 변수:

| 변수 | 설명 |
| --- | --- |
| `DMS_API_URL` | Ingress URL. 예: `https://dms.example.internal` |
| `DMS_TOKEN` | `DMS_AUTH_SHARED_TOKEN`과 같은 bearer token |
| `DMS_CLIENT_CERT` | operator 또는 automation client certificate |
| `DMS_CLIENT_KEY` | client private key |
| `DMS_CA_CERT` | DMS API server certificate을 검증할 CA |
| `DMS_ACTOR` | 운영에서는 unset. dev/test fallback에서만 사용 |

예:

```bash
export DMS_API_URL="https://dms.example.internal"
export DMS_TOKEN="replace-with-secret"
export DMS_CLIENT_CERT="/tmp/dms-certs/operator.crt"
export DMS_CLIENT_KEY="/tmp/dms-certs/operator.key"
export DMS_CA_CERT="/tmp/dms-certs/dms-api-server-ca.crt"
unset DMS_ACTOR
```

Planned shutdown/startup/resume helper에서 추가로 사용하는 변수:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_NAMESPACE` | `dms` | DMS Deployment가 있는 namespace. |
| `DMS_KUBECTL_CONTEXT` | 설정 안 됨 | worker Deployment scale 조작에 사용할 kubectl context. |
| `DMS_WORKER_DEPLOYMENTS` | script별 기본값 | `dms-planned-shutdown.sh` 기본값은 `dms-rm-worker dms-dm-worker`, `dms-resume.sh` 기본값은 `dms-rm-worker`. DM live execution 전에는 resume 대상에 `dms-dm-worker`를 넣지 않는다. |

Worker runtime:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_WORKER_LEASE_SECONDS` | `300` | Planner/RM/DM worker lifecycle에서 사용하는 claim lease. RM/DM worker는 backend call 중 heartbeat로 이 lease를 주기적으로 갱신한다. |
| `DMS_PREVIEW_TTL_SECONDS` | `86400` | `sync`/`rm` preview가 `ConfirmPending`으로 유지되는 TTL. `scan`은 confirm 없이 read-only로 실행한다. |
| `DMS_AGENT_REPORT_STALE_SECONDS` | `300` | Storage mapping readiness에 사용하는 agent report freshness window. node-health(latest-per-node) 읽기는 `agent_node_current`에서 reported_at 기준으로 freshness를 **읽는 시점에 계산**한다. |
| `DMS_CONTROL_CLUSTER_NAME` | `cluster-a` | DM readiness와 inventory aggregation에 사용하는 cluster name. |

`agent_reports` history 보존(`dms retention --loop` 프로세스). 100+ node가 분당 1회 보고하면
`agent_reports`는 하루 수십만 행씩 수백만 행으로 자란다. node-health는 `agent_node_current`(node별
최신 보고 1행, 매 ingest마다 같은 트랜잭션에서 갱신)에서 읽으므로 history는 node-metrics 시계열
window용으로만 필요하다 → 나이 기준 prune이 안전하다. retention loop는 cutoff보다 오래된 행을
batch(각 batch가 독립 트랜잭션)로 삭제한다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_AGENT_REPORT_RETENTION_SECONDS` | `2592000` (30일) | 이보다 오래된 `agent_reports` 행을 prune한다. **parse 시 7일(604800) 이상으로 floor** — 72h node-metrics sparkline window 아래로 내려가 시계열 데이터를 지우는 일을 막는다. |
| `DMS_AGENT_REPORT_RETENTION_INTERVAL_SECONDS` | `3600` | retention loop 주기. (`dms retention --loop --interval` 기본과 동일) |
| `DMS_AGENT_REPORT_RETENTION_HEARTBEAT_PATH` | 설정 안 됨 | 설정하면 매 cycle heartbeat 파일을 써서 k8s livenessProbe가 loop hang을 감지/재시작한다. |

Data Management runtime:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_DM_NAMESPACE` | `dms` | DM Worker가 VolcanoJob을 생성하고 조회할 namespace. |
| `DMS_DM_JOB_IMAGE` | 설정 안 됨 | 승인된 mpifileutils image. live `scan`/`sync`/`rm`에는 필수이며 없으면 DM Worker가 fail-closed한다. |
| `DMS_DM_JOB_IMAGE_REF` | 설정 안 됨 | mpifileutils repo tag/commit. data job result evidence에 남긴다. |
| `DMS_DM_SERVICE_ACCOUNT` | `dms-dm-worker` | Volcano worker pod에 지정할 ServiceAccount. |
| `DMS_DM_ARTIFACT_BASE_URI` | `file:///var/lib/dms/artifacts` | job별 stdout/stderr/report/summary URI의 base. 예: `file:///artifacts/dms/<job_id>/summary.json`. |
| `DMS_DM_DEFAULT_PRIORITY` | `Mid` | public priority label 기본값. |
| `DMS_DM_DEFAULT_MAX_NODES` | `1` | legacy compatibility setting. fan-out 정책의 source of truth는 DB `data_management_policies`다. |
| `DMS_DM_MAX_NODES` | `1` | legacy compatibility setting. 새 Data Management job resource model에는 사용하지 않는다. |
| `DMS_DM_POLICY_DEFAULT_WORKER_NODES` | `3` | `scan`, `rm`, same-node `dsync` DB policy bootstrap 기본 worker node 수. |
| `DMS_DM_POLICY_MAX_WORKER_NODES` | `3` | `scan`, `rm`, same-node `dsync` DB policy bootstrap max worker node 수. |
| `DMS_DM_POLICY_DEFAULT_PROCESSES_PER_NODE` | `3` | operation별 DB policy bootstrap 기본 MPI ranks/processes per worker pod. |
| `DMS_DM_POLICY_MAX_PROCESSES_PER_NODE` | `10` | operation별 DB policy bootstrap max MPI ranks/processes per worker pod. |
| `DMS_DM_POLICY_DEFAULT_QUEUE` | `dms-data` | DB policy bootstrap Volcano queue. |
| `DMS_DM_POLICY_DEFAULT_PRIORITY_CLASS` | `dms-normal` | DB policy bootstrap PriorityClass. |
| `DMS_DM_SCHEDULER_BACKEND` | `auto` | `auto`는 MPIJob을 먼저 적용하고 불가능하면 native VolcanoJob fallback을 사용한다. `mpi-operator` 또는 `volcano-job`으로 고정할 수 있다. |
| `DMS_DM_SCAN_TIMEOUT_SECONDS` | `3600` | scan timeout. 초과하면 DM Worker가 VolcanoJob terminate 후 실패로 기록한다. |
| `DMS_DM_SYNC_PREVIEW_TIMEOUT_SECONDS` | `3600` | `sync` dry-run preview VolcanoJob timeout. |
| `DMS_DM_SYNC_EXECUTION_TIMEOUT_SECONDS` | `259200` | confirmed `sync` execution VolcanoJob timeout (기본 3일). |
| `DMS_DM_RM_PREVIEW_TIMEOUT_SECONDS` | `1800` | `rm` dry-run preview VolcanoJob timeout. |
| `DMS_DM_RM_EXECUTION_TIMEOUT_SECONDS` | `3600` | confirmed `rm` execution VolcanoJob timeout. |
| `DMS_DM_CONFIRM_REQUIRE_PREVIEW_FINGERPRINT` | `true` | confirm 시 preview fingerprint evidence를 요구할지 여부. |
| `DMS_DM_SYNC_ALLOW_DELETE` | `true` | `sync` request의 `delete=true` 옵션을 운영 정책상 허용할지 여부. `false`이면 request validation에서 막는다. |
| `DMS_DM_ALLOW_ROOT_REQUESTER` | `true` | 운영자 root(privileged) 실행 허용. requester_id/owner_username이 `DMS_DM_PRIVILEGED_REQUESTERS`(기본 `root`)에 속하면 uid/gid 0으로 합성 실행한다(LDAP·uid floor 우회). API edge에서 **mTLS-verified operator만 허용**(평문 actor의 root 요청은 403), denylist는 kill-switch로 유지. `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true` 경로에서만 실제 동작한다. |
| `DMS_DM_PATH_BASE` | `mount_path` | DM 요청 path의 기준점(글로벌). `mount_path`(현행: path가 storage mount_path 기준) 또는 `managed_root`(planner가 storage별 `managed_root` suffix를 prepend해 managed_root 기준으로 해석). `managed_root` 모드는 filesystem mapping에 `managed_root`가 명시돼 있어야 하며, 산출 불가 시 잡을 fail-closed로 거부한다. |
| `DMS_DM_MAX_SYNC_NODES` | `1` | legacy compatibility setting. `dsync`/`nsync` node counts는 DB policy/API로 관리한다. |
| `DMS_DM_MAX_RM_NODES` | `1` | legacy compatibility setting. `rm` node counts는 DB policy/API로 관리한다. |
| `DMS_DM_NSYNC_ENABLED` | `true` | separated-role `nsync` 후보 selection 및 live execution 허용 여부. `false`이면 fail-closed한다. |
| `DMS_DM_NSYNC_SERVICE_PREFIX` | `dms-nsync` | native VolcanoJob fallback에서 role service/metadata 이름 prefix로 사용할 수 있는 prefix. |
| `DMS_DM_MONITOR_POLL_SECONDS` | `5` | VolcanoJob 상태 polling interval. |
| `DMS_DM_JOB_DELETE_ON_TERMINAL` | `false` | terminal VolcanoJob cleanup 정책. |
| `DMS_DM_KUBERNETES_MODE` | `cluster` | `cluster`는 live Volcano adapter, `stub`은 로컬 테스트/dev 전용. 운영에서 `stub`을 사용하지 않는다. |

Live Data Management operation은 read-only `scan`, same-node
`dsync`, separated-role `nsync`, `drm`이다. 각 job은 DB policy/API에서 결정된
worker node 수만큼 worker pod를 만들고, worker pod 하나 안에서 policy의
`processes_per_node`만큼 MPI ranks/processes를 실행한다. `sync`와 `rm`은 반드시
dry-run preview를 먼저 생성하고, explicit confirm과 TTL/fingerprint guard를 통과한
뒤에만 execution job을 실행한다.

`file://` artifact backend를 사용할 때 실제 결과 파일은
`<DMS_DM_ARTIFACT_BASE_URI path>/<job_id>/` 아래에 생성된다. `scan`은 job base
아래에 `summary.json`, `dscan-report.json`, `stdout.log`, `stderr.log`를 쓴다.
`sync`와 `rm`은 `<job_id>/preview/`와 `<job_id>/execution/` 아래에 각각
`summary.json`, `stdout.log`, `stderr.log`, `command.json`을 쓴다. DB에는
`data_jobs.artifact_uri`, phase별 artifact URI, fingerprint, 그리고 파싱된
summary만 저장된다. 모든 Data Management job은 추가로 `<job_id>/mpi/` 아래
`submitted.yaml`, `launch.json`, `workers.json`, `scheduler.json`, `mpirun.json`을
쓴다. 이 경로는 Volcano/MPI launcher와 worker pod가 쓸 수 있고
DM Worker가 읽고 traverse할 수 있어야 한다. 사용자 target/source/destination
directory가 `0750`처럼 private이면 artifact base를 그 하위에 두지 말고 별도
DMS-managed mount/PVC/object prefix로 분리한다.

Multi-node MPI Data Management에서는 operation별 node/process default와 max가
DB policy table/API의 source of truth가 된다. Env/runtime config는 bootstrap default로만
사용한다. Prerequisite로 Volcano scheduler/CRD, MPI Operator with Volcano gang
scheduling, `MPIJob` CRD, Open MPI 기반 mpifileutils job image, DMS queue/priority
class, PodGroup/MPIJob/VolcanoJob RBAC, 그리고 shared RWX artifact path가 필요하다.
DMS는 mounted eligible node set을 CR
affinity로 제출하고 scheduler가 실제 worker nodes를 선택하게 해야 한다. 모든 job은
submitted CR YAML과 `mpi/` metadata artifact를 기록해야 한다.

Kubernetes 접근:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_KUBERNETES_INVENTORY_MODE` | `kubectl` | 읽기 전용 inventory 모드. `kubectl`, `ssh-kubectl`, `python-client` 중 하나(전역 기본 `kubectl`; `DMS_CLUSTER_CONTROL_HOSTS_JSON`에 등록된 클러스터는 이 모드와 무관하게 ssh-kubectl로 읽음 — per-cluster transport). API 등록 sanity와 sanity-reconciler가 **공통으로** 이 모드로 클러스터(`DMS_CLUSTER_KUBECONFIGS_JSON`/`DMS_CLUSTER_CONTROL_HOSTS_JSON`에 등록된)를 읽어 agent 없는 managed 클러스터의 k8s/CSI mapping을 검증한다. 클러스터별로 격리되어 한 클러스터 읽기 실패가 전체 inventory를 무력화하지 않는다. |
| `DMS_KUBERNETES_MUTATION_MODE` | `kubectl` | Namespace/ResourceQuota mutation mode (전역 기본값). `kubectl`(로컬 kubectl — in-cluster/kubeconfig로 직접 도달) 또는 `ssh-kubectl`. **storage mapping의 `backend_template.mutation_mode`로 클러스터별 override 가능**(아래 노트). |
| `DMS_CLUSTER_KUBECONFIGS_JSON` | 설정 안 됨 | DMS cluster name에서 kubeconfig path로 가는 JSON object. Current context를 쓰지 않는 `kubectl` mode에서는 필요하다. |
| `DMS_CLUSTER_CONTROL_HOSTS_JSON` | 설정 안 됨 | DMS cluster name에서 SSH host로 가는 JSON object. `ssh-kubectl` mode의 전역 기본값. mapping의 `backend_template.control_host`가 있으면 그게 우선. **mapping에 `control_host`만 있고 `mutation_mode`가 없으면 등록 시 422 거부** — 전역 기본이 `kubectl`이라 control_host가 무시되므로 `mutation_mode:"ssh-kubectl"`을 명시해야 한다. |
| `DMS_KUBERNETES_INVENTORY_TIMEOUT_SECONDS` | `10` | Inventory read timeout. |
| `DMS_KUBERNETES_MUTATION_TIMEOUT_SECONDS` | `30` | ResourceQuota mutation timeout. |

> **클러스터별 mutation 경로 (per-mapping override).** `DMS_KUBERNETES_MUTATION_MODE`는 전역 기본값이고,
> storage mapping의 `backend_template`에 `mutation_mode`(`kubectl`|`ssh-kubectl`)와 `control_host`를 넣으면
> **그 클러스터의 ResourceQuota mutation만** 해당 방식으로 라우팅된다(없으면 전역값으로 fallback). 예: 전역은
> `kubectl`(직접 도달 가능한 클러스터들)로 두고, 직접 도달 불가한 managed 클러스터 매핑만 `mutation_mode:"ssh-kubectl"`
> + `control_host:"<bastion>"`으로 지정 → DMS는 그 클러스터에 한해 `ssh <bastion> kubectl ...`로 적용한다. 한 클러스터에
> 매핑이 여러 개면 **`storage_name` 사전순 첫 매핑**의 `mutation_mode`/`control_host`가 결정한다(결정적).
> `control_host`만 주면 전역 mode는 유지하고 control host만 override한다. AUDIT/EXPIRATION-SWEEP는 멀티클러스터라 타깃별로
> 각 클러스터 전송경로를 적용한다. **이 동일한 per-mapping mode/control_host는 CSI mapping sanity 검증에도
> 그대로 쓰여서**, sanity가 그 경로로 `kubectl auth can-i ... resourcequota`를 실행한다(아래 "Storage Mapping 규칙" 참고).
>
> **보안 경계.** `control_host`는 bare hostname/IPv4만 허용(검증 `422`)되고, 실제 도달은 rm-worker의 SSH 신뢰로 제한된다
> — `ssh-kubectl`은 `StrictHostKeyChecking=yes` + `dms-ssh-client` Secret의 `known_hosts`에 등록된 호스트로만 접속되고
> 그 호스트 `root`에 rm-worker 공개키가 있어야 한다. 즉 storage mapping 등록 권한만으로 임의 호스트로 mutation을 보낼 수
> 없다(known_hosts/authorized_keys는 별도 cluster-admin 작업). 자세한 등록은 `3.dms-rm-api-k8s.md` §2.2.

Filesystem backend 실행:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DMS_FILESYSTEM_MUTATION_MODE` | `ssh-host-exec` | CephFS adapter execution mode. `ssh-host-exec` 또는 `local`. GPFS는 storage mapping의 `command_runner`를 사용한다. |
| `DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS` | `30` | Filesystem host command timeout. Quota read-back이 느리면 늘린다. |
| `DMS_FILESYSTEM_EXEC_USE_SUDO` | `true` | CephFS host executor가 host mutation에 sudo를 사용할지 여부. |

LDAP/identity:

| 변수 | 필수 여부 | 설명 |
| --- | --- | --- |
| `DMS_LDAP_URI` | identity lookup/group management에 필요 | LDAP URI. 예: `ldap://ldap.example.internal:389`. |
| `DMS_LDAP_BASE_DN` | LDAP에 필요 | Base DN. 예: `dc=example,dc=internal`. |
| `DMS_LDAP_BIND_DN` | LDAP bind에 필요 | Bind DN. Secret에 저장한다. |
| `DMS_LDAP_BIND_PASSWORD` | LDAP bind에 필요 | Bind password. Secret에 저장한다. |
| `DMS_LDAP_USER_SEARCH_BASE` | 선택 | 기본값은 `ou=people,<baseDN>`. |
| `DMS_LDAP_GROUP_SEARCH_BASE` | 선택 | 기본값은 `ou=groups,<baseDN>`. |
| `DMS_LDAP_USER_FILTER` | 선택 | 기본값은 `(uid={username})`. |
| `DMS_LDAP_TIMEOUT_SECONDS` | 선택 | 기본값은 `5`. |
| `DMS_LDAP_GROUP_GID_START` | 선택 | DMS가 생성한 group의 GID allocation lower bound. |
| `DMS_LDAP_GROUP_GID_END` | 선택 | DMS가 생성한 group의 GID allocation upper bound. |
| `DMS_DM_IDENTITY_PROVIDER` | 선택 | DM이 identity를 resolve하는 provider. 기본값은 `ldap`. |

DM identity는 별도의 mapping 등록 없이 preflight(dm-worker) 시점에 위의 `DMS_LDAP_*` 설정을 그대로 사용해 read-only LDAP lookup으로 해석한다. Lookup key는 `owner_username`(기본값은 `requester_id`이며 실제 POSIX username으로 override될 수 있다)이고 user filter는 `DMS_LDAP_USER_FILTER`(기본값 `(uid={username})`)를 따른다. **Fail closed**: LDAP가 응답하지 않으면 TTL cache 없이 해당 job preflight가 `ldap_unavailable`로 실패한다.

## Agent 환경변수

| 변수 | 필수 여부 | 설명 |
| --- | --- | --- |
| `DMS_AGENT_API_URL` | 예 | Agent cluster에서 접근하는 DMS API URL. |
| `DMS_AGENT_CLUSTER_NAME` | 예 | DMS logical cluster name. Storage mapping과 kubeconfig JSON key와 일치해야 한다. |
| `DMS_AGENT_WORKER_ROLE` | 예 | `RM` 또는 `DM`. |
| `DMS_AGENT_REPORT_INTERVAL_SECONDS` | 아니오 | 기본값은 60초. |
| `DMS_AGENT_REPORT_TIMEOUT_SECONDS` | 아니오 | 기본값은 5초. |
| `DMS_AGENT_TOOLS` | 아니오 | 쉼표로 구분한 tool probe 목록. 기본값: `dsync,nsync,drm,dscan,kubectl`. |
| `DMS_AGENT_CREDENTIAL_FILES` | 아니오 | Report할 credential path의 쉼표로 구분한 목록. |
| `DMS_AGENT_NETWORK_ENDPOINTS` | 아니오 | Probe할 network endpoint의 쉼표로 구분한 목록. |
| `DMS_AGENT_IDENTITY_USERS` | 아니오 | NSS를 통해 probe할 POSIX user의 쉼표로 구분한 목록. |
| `DMS_AUTH_SHARED_TOKEN` | API token이 enabled이면 필요 | Report post에 사용하는 shared bearer token. |

주의: 현재 mTLS-required 운영 profile에서 agent report를 Fresh로 저장하려면 agent request의 authenticated actor가 `node:{cluster_name}:{node_name}`과 일치해야 한다. 기본 mTLS actor derivation은 `mtls:<certificate-subject>`이므로, agent certificate subject-to-node actor mapping 또는 동등한 internal authentication boundary가 별도로 필요하다.

## Storage Mapping 규칙

`storage_name`을 참조하는 모든 Resource Management request는 storage mapping sanity/readiness guard에 의해 차단될 수 있다. Mapping은 존재해야 하고, disabled 상태가 아니어야 하며, `sanity_status`가 `Failed` 또는 `Unknown`이면 안 되고, 관련 readiness key가 `Ready`여야 한다.

Filesystem resource request 요구사항:

- `payload.storage_name`
- `payload.directory_name`
- 생성 시 미래 시점의 `payload.expires_at`
- 생성/import 시 최소 두 개의 `payload.users`
- Storage mapping `readiness.resource_management=Ready`

Kubernetes namespace quota request 요구사항:

- `payload.cluster_name`
- `payload.namespace_name`
- 생성 시 미래 시점의 `payload.expires_at`
- `payload.quota.requests_storage_bytes`
- `payload.quota.pvc_count`
- `payload.storage_class_quotas[].storage_name`
- 같은 `cluster_name`에 있는 모든 참조 storage mapping

Kubernetes namespace quota는 모든 CSI StorageClass backend에서 공통 live
`ResourceQuota/dms-storage-quota` adapter를 사용한다. GPFS나 WEKA 같은 backend의
filesystem quota command/API는 이 경로에서 실행되지 않는다. Filesystem adapter가
없는 backend라도 `csi_driver`, `storage_class_name`, sanity/readiness가 맞으면
Kubernetes quota mapping으로는 사용할 수 있다.

CSI/k8s mapping의 sanity 검증 축(2026-06 이후):

- `backend_template.backend_type`이 filesystem backend(`cephfs`/`wekafs`/`gpfs`)가
  **아닌** mapping(예: `ceph-csi`, `longhorn`, 기타 CSI provisioner)은 **CSI/k8s
  mapping**으로 분류된다. 이런 mapping은 DMS node agent를 두지 않는 managed
  cluster일 수 있으므로, RM/DM agent evidence 대신 **ResourceQuota mutation
  transport**를 검증한다. 즉 RM agent가 없어도 `missing_rm_readiness`/
  `missing_dm_readiness`로 Degraded되지 않고, agent report 부재만으로 Failed/
  Unknown이 되지도 않는다.
- Mutation transport 검증은 sanity-reconciler(및 API 등록 sanity)가 그 mapping에
  대해 RM worker가 실제로 쓰게 될 경로 — `DMS_KUBERNETES_MUTATION_MODE`(또는
  per-mapping `backend_template.mutation_mode`)와 `control_host` — 로
  `kubectl auth can-i <verb> resourcequota -A`를 `create`/`patch`/`delete` 세
  verb에 대해 실행해서 판정한다(전송 경로 도달성 + RBAC 권한을 한 번에 확인).
- 판정 규약: `can-i` returncode `0` = 권한 있음, `1` = **도달은 했으나 권한 없음**,
  그 외 returncode나 전송 오류(SSH 실패 등) = **도달 실패**. 결과는 sanity 결과의
  `readiness.kubernetes_mutation`(`Ready`/`Failed`/`Unknown`)과
  `mutation_observed`(`mode`/`control_host`/`reachable`/`permissions{create,patch,
  delete}`/`can_mutate`/`detail`)로 노출된다.
  - 도달 실패 → error `mutation_transport_unreachable` + `kubernetes_mutation=Failed`
    → sanity `Failed`.
  - 도달했으나 세 verb 중 하나라도 권한 없음 → error `mutation_no_permission`
    (`kubectl auth can-i = no`) + `Failed` → sanity `Failed`.
  - 세 verb 모두 허용 → check `kubernetes_mutation_transport` Passed +
    `kubernetes_mutation=Ready`.
  - mutation 어댑터가 wiring되지 않은 환경(클러스터 미등록 등)에서는
    `kubernetes_mutation=Unknown`으로 두고 CSI sanity는 error/warning이 없으면
    `Ready`로 유지한다(agent freshness gate를 적용하지 않음).
- 그 결과 mutation 경로(SSH host/known_hosts, RBAC `create/patch/delete
  resourcequota`)가 깨진 CSI mapping은 sanity가 `Failed`가 되고, planner의
  `_reject_unsafe_storage_mapping` guard가 해당 cluster의 namespace quota request를
  자동으로 차단한다. mutation 경로를 사전 검증하려면 `3.dms-rm-api-k8s.md`의
  `kubectl auth can-i create resourcequota` 점검 절차를 참고한다.

`expiry_at`이 아니라 `expires_at`을 사용한다. `expiry_at`과 `clear_expires_at`은 지원하지 않는 field다.

## Kubernetes manifest 적용 순서

Control cluster:

```bash
kubectl --context dms-control apply -f /tmp/dms-control-plane.yaml
kubectl --context dms-control -n dms wait --for=condition=complete job/dms-migrate --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-api --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-planner --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-rm-worker --timeout=180s
kubectl --context dms-control apply -f /tmp/dms-ingress.yaml
```

Target cluster:

```bash
kubectl --context cluster-a apply -f install/kubernetes/target-cluster-rbac.yaml
kubectl --context cluster-a apply -f /tmp/dms-agent-daemonset.yaml
kubectl --context cluster-a -n dms rollout status daemonset/dms-rm-agent --timeout=180s
```

여러 target cluster를 관리한다면 cluster별로 반복한다.

## Secret 생성 명령 예시

DB/token/LDAP Secret:

```bash
kubectl --context dms-control -n dms create secret generic dms-secrets \
  --from-literal=DMS_DATABASE_URL='postgresql://dms_app:APP_PASSWORD@postgres.example.internal:5432/dms' \
  --from-literal=DMS_OBSERVABILITY_DATABASE_URL='postgresql://dms_obs:OBS_PASSWORD@postgres.example.internal:5432/dms_observability' \
  --from-literal=DMS_AUTH_SHARED_TOKEN='REPLACE_WITH_RANDOM_TOKEN' \
  --from-literal=DMS_LDAP_BIND_DN='cn=dms,ou=service-accounts,dc=example,dc=internal' \
  --from-literal=DMS_LDAP_BIND_PASSWORD='REPLACE_WITH_LDAP_PASSWORD' \
  --dry-run=client -o yaml | kubectl --context dms-control apply -f -
```

Target kubeconfig Secret:

```bash
kubectl --context dms-control -n dms create secret generic dms-cluster-kubeconfigs \
  --from-file=cluster-a.kubeconfig=/tmp/dms-kubeconfigs/cluster-a.kubeconfig \
  --from-file=cluster-b.kubeconfig=/tmp/dms-kubeconfigs/cluster-b.kubeconfig \
  --dry-run=client -o yaml | kubectl --context dms-control apply -f -
```

mTLS client CA Secret:

```bash
kubectl --context dms-control -n dms create secret generic dms-client-ca \
  --from-file=ca.crt=/tmp/dms-certs/dms-client-ca.crt \
  --dry-run=client -o yaml | kubectl --context dms-control apply -f -
```

## API 예시

공통 변수를 설정한다.

```bash
export DMS_API_URL="https://dms.example.internal"
export DMS_TOKEN="replace-with-secret"
export DMS_CLIENT_CERT="client.crt"
export DMS_CLIENT_KEY="client.key"
export DMS_CA_CERT="dms-api-ca.crt"

curl_dms() {
  curl -fsS --cert "$DMS_CLIENT_CERT" --key "$DMS_CLIENT_KEY" --cacert "$DMS_CA_CERT" "$@"
}
```

Health 확인:

```bash
curl_dms "$DMS_API_URL/healthz"
```

Storage mapping 등록:

```bash
jq -c '.storage_mappings[0]' install/config/storage-mappings.example.json | \
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/storage-mappings" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data @-
```

Bulk registration에는 `install/scripts/register-storage-mappings.sh` 사용을 권장한다.

Kubernetes namespace quota 생성:

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "alice",
    "payload": {
      "cluster_name": "cluster-a",
      "namespace_name": "alice-scratch",
      "storage_class_quotas": [{"storage_name": "longhorn-a"}],
      "quota": {"requests_storage_bytes": 1000000000000, "pvc_count": 20},
      "expires_at": "2099-01-01T00:00:00Z",
      "allow_namespace_create": true
    }
  }'
```

Filesystem 생성:

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/filesystems" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "alice",
    "payload": {
      "storage_name": "cephfs-a",
      "directory_name": "alice-project",
      "users": ["alice", "bob"],
      "quota": {"capacity_bytes": 1000000000000, "file_count": 1000000},
      "expires_at": "2099-01-01T00:00:00Z"
    }
  }'
```

운영 조회:

```bash
curl_dms "$DMS_API_URL/api/v1/operations/requests?requester_id=alice&limit=20" \
  -H "authorization: Bearer $DMS_TOKEN"

curl_dms "$DMS_API_URL/api/v1/operations/action-required" \
  -H "authorization: Bearer $DMS_TOKEN"

curl_dms "$DMS_API_URL/api/v1/operations/control-state" \
  -H "authorization: Bearer $DMS_TOKEN"

curl_dms "$DMS_API_URL/api/v1/operations/work-summary" \
  -H "authorization: Bearer $DMS_TOKEN"

curl_dms "$DMS_API_URL/api/v1/operations/plans/active" \
  -H "authorization: Bearer $DMS_TOKEN"

curl_dms "$DMS_API_URL/api/v1/operations/runs/active" \
  -H "authorization: Bearer $DMS_TOKEN"

curl_dms "$DMS_API_URL/api/v1/operations/drain-status" \
  -H "authorization: Bearer $DMS_TOKEN"
```

일괄 설정 helper:

```bash
export DMS_API_URL="https://dms.example.internal"
export DMS_TOKEN="$DMS_TOKEN"
export DMS_CLIENT_CERT="client.crt"
export DMS_CLIENT_KEY="client.key"
export DMS_CA_CERT="dms-api-ca.crt"
unset DMS_ACTOR

install/scripts/register-storage-mappings.sh install/config/storage-mappings.example.json
install/scripts/register-default-quota-policies.sh install/config/default-quota-policies.example.json
# (선택) DM identity denylist 적용. denylist는 보통 비어 있고,
# DM identity 자체는 등록이 필요 없다(preflight에서 직접 LDAP 조회).
install/scripts/apply-identity-denylist.sh install/config/identity-denylist.example.json
install/scripts/verify-install.sh
```

운영 mTLS profile에서는 helper가 `DMS_CLIENT_CERT`, `DMS_CLIENT_KEY`,
`DMS_CA_CERT`, `DMS_TOKEN`을 사용해 ingress/edge proxy를 통과한다.
`DMS_ACTOR`는 dev/test fallback용이며, 운영에서는 unset 상태로 둔다.
