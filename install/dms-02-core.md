# DMS 코어 배포 (Core)

DMS control plane을 프로덕션 클러스터에 올리는 절차다. 순서는 **이미지 빌드·push →
mTLS 인증서 발급 → `control-plane.yaml` 편집 → 적용(+migration) → 추가 RBAC → Ingress →
스모크 테스트**.

- 시작 전 [`dms-01-prerequisites.md`](dms-01-prerequisites.md)를 끝낸다(PostgreSQL 2개 DB,
  로컬 registry, ingress-nginx, 그리고 DM을 쓸 경우 Volcano·Queue/PriorityClass·공유 artifact
  FS·노드 신원·스토리지 host-mount).
- 아래의 `registry.example.internal`, `cluster-a`, `dms.example.internal`,
  `dc=example,dc=internal`, `/cephfs`는 **예시 placeholder**다. 각자 환경 값으로 치환한다.
- 명령은 DMS repository root에서 실행한다고 가정한다(`cd <dms-repo-root>`).

인증은 **프로덕션 = mTLS-verified header 프로필**이 기준이다. 신뢰하는 ingress가 클라이언트
인증서를 검증해 upstream으로 전달하고, DMS는 **인증서 subject**에서 actor를 유도한다(§4). 평문
`x-dms-actor`는 이 프로필에서 신뢰되지 않는다.

---

## 1. 이미지 빌드·push (3개)

DMS는 컨테이너 이미지 **3개**를 쓴다. 역할과 빌드 소스는 다음과 같다.

| 이미지 | Dockerfile | 쓰는 컴포넌트 |
|---|---|---|
| **DM job** (mpifileutils) | `install/docker/Dockerfile.mpifileutils` | DM 잡 파드(Volcano MPI worker). `DMS_DM_JOB_IMAGE`로 참조 |
| **plain dms** | `install/docker/Dockerfile` | dms-api / planner / rm-worker / dm-worker / retention / sanity-reconciler + **dms-rm-agent** |
| **dms-agent** | `install/docker/Dockerfile.agent` | **dms-dm-agent** DaemonSet 전용 (plain dms + mpifileutils 도구 바이너리) |

빌드 의존성상 순서는 **DM job → plain dms → dms-agent**다. dms-agent 이미지는 `FROM <plain dms>`
위에 DM job 이미지에서 `/opt/mpifileutils`를 복사해 만들므로 앞의 두 이미지가 먼저 push돼 있어야
한다(`--build-arg MFU_IMAGE`에 **DM job 이미지를 먼저** 지정).

```bash
REGISTRY=registry.example.internal
TAG=v1        # 조직 규칙에 맞는 immutable 태그로 치환

# (1) DM job 이미지 — mpifileutils(dscan/dsync/drm 등). github egress 필요(없으면 미러 사용).
docker build -f install/docker/Dockerfile.mpifileutils \
  -t "$REGISTRY/dms-mpifileutils:$TAG" .
docker push "$REGISTRY/dms-mpifileutils:$TAG"

# (2) plain dms 이미지 — 코어 서비스 전부 + dms-rm-agent.
docker build -f install/docker/Dockerfile \
  -t "$REGISTRY/dms:$TAG" .
docker push "$REGISTRY/dms:$TAG"

# (3) dms-agent 이미지 — plain dms(DMS_IMAGE) + mpifileutils 도구(MFU_IMAGE=DM job 이미지).
docker build -f install/docker/Dockerfile.agent \
  --build-arg DMS_IMAGE="$REGISTRY/dms:$TAG" \
  --build-arg MFU_IMAGE="$REGISTRY/dms-mpifileutils:$TAG" \
  -t "$REGISTRY/dms-agent:$TAG" .
docker push "$REGISTRY/dms-agent:$TAG"
```

> **프록시 전용 네트워크에서 빌드.** 인터넷이 프록시(예: `127.0.0.1:7227`)로만 되는 환경이면
> 위 `docker build`들에 **빌드-타임 프록시**를 주면 된다. 편의를 위해 래퍼 스크립트
> `install/docker/build-images.sh`가 세 DMS 이미지(+포탈)를 의존 순서대로 빌드한다.
>
> ```bash
> REGISTRY=registry.example.internal TAG=v1 \
>   PROXY=http://127.0.0.1:7227 ./install/docker/build-images.sh
> # 직결 인터넷이면 PROXY 없이 그냥 실행(= 위 수동 docker build와 동일).
> # 일부만: IMAGES="mpifileutils dms agent" ...   레지스트리 push까지: PUSH=1 ...
> ```
>
> - `PROXY`를 주면 각 빌드에 predefined 프록시 build-arg(`http_proxy`/`https_proxy`/`no_proxy`,
>   포탈은 `npm_config_proxy`도)와 `--network=host`가 붙는다. 이 build-arg들은 **빌드 중
>   apt/curl/git/pip/npm만 프록시를 타게 하고 최종 이미지에는 남지 않는다** — ENV가 아니라 ARG라
>   런타임 컨테이너(웹서버·노드 간 호출)는 프록시로 새지 않고, `docker history`에도 안 남는다.
> - **`--network=host`가 필요한 이유**: 빌드 컨테이너 안의 `127.0.0.1`은 호스트가 아니라 컨테이너
>   자기 루프백이라, 호스트-로컬 프록시(`127.0.0.1:PORT`)에 닿으려면 빌드가 호스트 네트워크를
>   공유해야 한다. (프록시가 별도 호스트면 `PROXY=http://<host>:<port>`만 주면 되고 host network는
>   불필요.)
> - **별도 프록시용 Dockerfile은 두지 않는다** — 하나의 Dockerfile이 build-arg 유무로 두 경우를
>   모두 처리한다(중복 Dockerfile은 drift 위험).

> **사내 CA로 빌드 (TLS 가로채기 프록시 / 사내 HTTPS 엔드포인트).** 회사망이 TLS를 가로채 재서명하거나
> (사내 MITM 프록시), 레지스트리·엔드포인트가 사내 CA로 서명된 HTTPS면, 빌드 중 apt/curl/git/pip/npm이
> 그 인증서를 신뢰해야 한다. `build-images.sh`에 `CA_CERT`로 **PEM CA 파일 경로**를 주면 된다.
>
> ```bash
> REGISTRY=registry.example.internal TAG=v1 \
>   CA_CERT=/etc/pki/ca-trust/source/anchors/corp-root.crt \
>   ./install/docker/build-images.sh
> # TLS까지 가로채는 프록시면 PROXY=... 와 함께 준다.
> ```
>
> - `CA_CERT`는 파일이라 build-arg로 못 싣는다 — 스크립트가 빌드 컨텍스트(`install/docker/certs/`)로
>   **스테이징**했다가 종료 시 제거한다(작업트리에 안 남고 `.gitignore`로 커밋도 차단). 각 Dockerfile은
>   그 디렉토리를 이미지 트러스트 스토어로 COPY하고 `update-ca-certificates`를 돌린다(SPA는
>   `NODE_EXTRA_CA_CERTS`, pip은 `PIP_CERT`/`REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE`가 시스템 번들을 가리킴).
> - **프록시와 달리 CA는 런타임 이미지에 일부러 남긴다.** 사내 엔드포인트(k8s API·LDAP·PostgreSQL·DMS
>   API 자체)가 그 CA로 서명돼 있어 **실행 중인 서비스도 신뢰해야** 하기 때문이다. `dms-agent`는 `dms`
>   이미지를 FROM 하므로 자동 상속한다.
> - `CA_CERT` 없이 빌드하면 디렉토리엔 `.gitkeep`만 있어 모든 COPY/update가 **깨끗한 no-op** — 기존
>   빌드와 동일하게 동작한다. (PEM 형식 필수; 여러 CA는 한 파일에 concat 가능.)

> **⚠️ `DMS_DM_JOB_IMAGE`는 반드시 실제 push한 ref여야 한다 (함정).** ConfigMap의 기본값
> `registry.example.internal/dms-mpifileutils:CHANGE_ME`를 그대로 두면 DMS는 이 값을 유효한
> 문자열로 취급해 **fail-closed 하지 않는다** — DM 잡 파드가 뜨는 순간 `ImagePullBackOff`로 죽는다.
> §3에서 이 키를 (1)의 실제 ref로 바꾼다.
>
> `DMS_DM_JOB_IMAGE_REF`는 **선택**(provenance/audit용 git ref 라벨)일 뿐 잡 스케줄에는 관여하지
> 않는다. 채우려면 `Dockerfile.mpifileutils`의 `MPIFILEUTILS_REF` 값을 쓴다.
>
> **plain dms 이미지만으로는 DM이 안 된다** — mpifileutils 도구가 없어 DM 후보가
> `missing_dscan`/`missing_dsync`/`missing_drm_tool`로 거부된다. DM 잡 노드의 에이전트는 반드시
> (3) dms-agent 이미지를 써야 한다(DaemonSet 배포는 [`dms-05-dm-jobs.md`](dms-05-dm-jobs.md)).

---

## 2. mTLS 인증서 발급

ingress mTLS 종단에 필요한 인증서 3종을 만든다. 안전한 위치(예: `certs/`, 700 권한)에 보관한다.

1. **Client CA** — operator/BFF 클라이언트 인증서를 서명하고, ingress가 이걸로 클라이언트를 검증한다.
2. **Server CA + server cert** — API 호스트(`dms.example.internal`)의 TLS. 클라이언트는 server CA를
   `--cacert`로 신뢰한다.
3. **Operator client cert** — DMS 호출용. **CN이 곧 actor**가 된다(§4).

```bash
CERTS=certs; mkdir -p "$CERTS"

# (1) Client CA
openssl req -x509 -newkey rsa:4096 -nodes -days 3650 \
  -keyout "$CERTS/dms-client-ca.key" -out "$CERTS/dms-client-ca.crt" \
  -subj "/CN=dms-client-ca/O=cluster-a"

# (2) Server CA + server cert (CN/SAN = API 호스트)
openssl req -x509 -newkey rsa:4096 -nodes -days 3650 \
  -keyout "$CERTS/dms-server-ca.key" -out "$CERTS/dms-server-ca.crt" \
  -subj "/CN=dms-server-ca/O=cluster-a"

openssl req -newkey rsa:2048 -nodes \
  -keyout "$CERTS/dms-api-server.key" -out "$CERTS/dms-api-server.csr" \
  -subj "/CN=dms.example.internal/O=cluster-a"
echo "subjectAltName=DNS:dms.example.internal" > "$CERTS/san.ext"
openssl x509 -req -days 3650 \
  -in "$CERTS/dms-api-server.csr" \
  -CA "$CERTS/dms-server-ca.crt" -CAkey "$CERTS/dms-server-ca.key" \
  -CAcreateserial -extfile "$CERTS/san.ext" \
  -out "$CERTS/dms-api-server.crt"

# (3) Operator client cert — CN=operator → DMS actor "mtls:operator"
openssl req -newkey rsa:2048 -nodes \
  -keyout "$CERTS/operator.key" -out "$CERTS/operator.csr" \
  -subj "/CN=operator/O=cluster-a"
openssl x509 -req -days 3650 \
  -in "$CERTS/operator.csr" \
  -CA "$CERTS/dms-client-ca.crt" -CAkey "$CERTS/dms-client-ca.key" \
  -CAcreateserial \
  -out "$CERTS/operator.crt"
```

server cert를 담은 TLS Secret은 `control-plane.yaml`에 **없으므로 별도로** 만든다(Ingress가
`secretName: dms-api-tls`로 참조).

```bash
kubectl -n dms create secret tls dms-api-tls \
  --cert="$CERTS/dms-api-server.crt" --key="$CERTS/dms-api-server.key"
```

Client CA(`dms-client-ca.crt`)는 `control-plane.yaml`의 `dms-client-ca` Secret에 넣는다(§3).

> 운영자·포탈 BFF마다 별도 client cert를 (3)과 같이 발급한다. CN을 사람/시스템별로 구분하면
> 감사 로그의 actor(`mtls:<CN>`)로 구별된다. Portal BFF cert는 [`portal-01-setup.md`](portal-01-setup.md).

---

## 3. `control-plane.yaml` 편집

`install/kubernetes/control-plane.yaml` 하나에 Namespace(`dms`, PodSecurity=privileged) · SA ·
ConfigMap · Secret · RBAC · `dms-migrate` Job · Deployment(api ×2 / planner / rm-worker /
**dm-worker replicas=1**) · Service · NetworkPolicy가 모두 들어 있다. 아래 항목을 **환경 값으로
치환**한 뒤 적용한다. (secret 값이 들어가므로 편집본은 git에 커밋하지 않는다.)

### ConfigMap `dms-runtime-config`

| 키 | placeholder | 설정값 |
|---|---|---|
| `DMS_CONTROL_CLUSTER_NAME` | `cluster-a` | 컨트롤 클러스터 이름 |
| `DMS_CLUSTER_KUBECONFIGS_JSON` | `{"cluster-a":"/etc/dms/kubeconfigs/cluster-a.kubeconfig"}` | 클러스터명·kubeconfig 파일명 일치 (kubeconfig 생성은 [`dms-04-rm-k8s-quota.md`](dms-04-rm-k8s-quota.md)) |
| `DMS_LDAP_URI` | `ldap://ldap.example.internal:389` | 실제 LDAP/AD URI |
| `DMS_LDAP_BASE_DN` | `dc=example,dc=internal` | base DN |
| `DMS_LDAP_USER_SEARCH_BASE` | `ou=people,dc=example,dc=internal` | user search base |
| `DMS_LDAP_GROUP_SEARCH_BASE` | `ou=groups,dc=example,dc=internal` | group search base |
| `DMS_DM_JOB_IMAGE` | `registry.example.internal/dms-mpifileutils:CHANGE_ME` | **§1 (1)의 실제 push한 ref (⚠️ 반드시 교체 — §1 함정)** |
| `DMS_DM_JOB_IMAGE_REF` | `CHANGE_ME_MPIFILEUTILS_GIT_REF` | (선택) provenance git ref |

그대로 두는(=이미 프로덕션 값) 인증·스케줄러 키:

- `DMS_REQUIRE_MTLS_HEADER: "true"`, `DMS_REQUIRE_MTLS_VERIFIED_HEADER: "true"`,
  `DMS_MTLS_ACTOR_PREFIX: "mtls:"` — 프로덕션 인증 프로필(§4). **`DMS_DEFAULT_ACTOR`는 추가하지
  않는다** — `REQUIRE_MTLS_HEADER=true`일 때 이 값이 설정돼 있으면 **API가 기동에 실패**한다.
- `DMS_DM_SCHEDULER_BACKEND: "volcano-job"` — Volcano 네이티브 Job. `auto`로 바꾸지 않는다
  (MPIJob을 먼저 시도해 매 잡마다 실패한다). Kubeflow MPI Operator는 불필요.
- `DMS_DM_POLICY_DEFAULT_QUEUE: "dms-data"`, `DMS_DM_POLICY_DEFAULT_PRIORITY_CLASS: "dms-normal"` —
  prereqs의 `volcano-queue-priorityclasses.yaml` 이름과 반드시 일치시킨다(다르면 잡이 Pending).

### Secret `dms-secrets`

| 키 | placeholder | 설정값 |
|---|---|---|
| `DMS_DATABASE_URL` | `postgresql://dms_app:CHANGE_ME@postgres.example.internal:5432/dms` | operational DB URL |
| `DMS_OBSERVABILITY_DATABASE_URL` | `postgresql://dms_obs:CHANGE_ME@.../dms_observability` | observability DB URL |
| `DMS_AUTH_SHARED_TOKEN` | `CHANGE_ME_LONG_RANDOM_TOKEN` | (선택) mTLS 위에 얹는 공유 bearer 토큰 |
| `DMS_LDAP_BIND_DN` | `cn=dms,ou=service-accounts,dc=example,dc=internal` | LDAP 서비스 계정 DN |
| `DMS_LDAP_BIND_PASSWORD` | `CHANGE_ME` | bind 암호 |

> CephFS·WekaFS 파일시스템 작업은 **동작하는 LDAP bind가 eager 필수**다(GPFS는 선택). bind가
> 안 되면 해당 백엔드 RM이 실패한다. 상세 [`dms-03-rm-filesystem.md`](dms-03-rm-filesystem.md).

### Secret `dms-client-ca`

- `ca.crt` — §2 (1)에서 만든 `dms-client-ca.crt` PEM을 붙여넣는다. ingress mTLS가 클라이언트
  인증서를 이 CA로 검증한다(annotation `auth-tls-secret: dms/dms-client-ca`, §6).

### Secret `dms-ssh-client`

- `id_ed25519` / `known_hosts` — 파일시스템 백엔드(CephFS/GPFS/Weka) 관리 명령을 노드에서 실행하는
  SSH private key와 host key. 발급·등록 절차는 [`dms-03-rm-filesystem.md`](dms-03-rm-filesystem.md).
  (`config`는 그대로 둔다. 파일 권한은 rm-worker의 initContainer가 세팅한다.)

### Secret `dms-cluster-kubeconfigs`

- `cluster-a.kubeconfig` — 컨트롤/타깃 클러스터 kubeconfig로 교체. 생성은
  [`dms-04-rm-k8s-quota.md`](dms-04-rm-k8s-quota.md)(`create-serviceaccount-kubeconfig.sh`).

### image 라인

- 모든 `image: registry.example.internal/dms:CHANGE_ME`를 §1 (2) **plain dms** ref로 바꾼다.
  등장 위치: `Job/dms-migrate`, `Deployment/dms-api`, `Deployment/dms-planner`,
  `Deployment/dms-rm-worker`(initContainer + 컨테이너), `Deployment/dms-dm-worker`.

### dm-worker artifact hostPath (DM 사용 시)

- `Deployment/dms-dm-worker`의 `dm-artifacts` 볼륨 `hostPath.path: /artifacts`를 **공유 artifact FS의
  마운트포인트**로 바꾼다(서브디렉터리가 아니라 마운트포인트 자체). 상세는 prereqs §0.5 /
  [`dms-05-dm-jobs.md`](dms-05-dm-jobs.md). `dms-dm-worker`는 `replicas: 1`(DM 활성)로 둔다 —
  **`0`은 DM을 의도적으로 끌 때만** 쓴다(0이면 어떤 워커도 데이터 잡을 claim하지 않아 scan/sync/rm이
  조용히 미실행된다).

---

## 4. 인증 프로필 (mTLS-verified header)

프로덕션 프로필의 동작:

1. 신뢰하는 ingress가 클라이언트 인증서를 **Client CA로 검증**하고
   (`auth-tls-verify-client: on`), 검증한 인증서를 upstream 헤더로 전달한다
   (`auth-tls-pass-certificate-to-upstream: true`).
2. DMS는 `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`이므로 그 검증된 인증서만 신뢰하고, **subject의
   CN**에 `DMS_MTLS_ACTOR_PREFIX`(기본 `mtls:`)를 붙여 actor로 삼는다. 예: `CN=operator` →
   actor `mtls:operator`(감사 로그에 이 값이 남는다).
3. 평문 `x-dms-actor` 헤더는 **신뢰되지 않는다**. `DMS_DEFAULT_ACTOR`는 비어 있어야 하며, 설정 시
   API가 기동에 실패한다(§3).
4. (선택) `DMS_AUTH_SHARED_TOKEN`을 얹으면 mTLS에 더해 `Authorization: Bearer <token>`도 요구된다.
5. **DMS는 evidence 헤더(`ssl-client-*`)를 무조건 신뢰**하므로, `dms-api`는 **cert를 종단하는 ingress만**
   닿아야 한다 — 안 그러면 in-cluster 아무 파드나 그 헤더를 스푸핑해 operator actor를 위조할 수 있다.
   이를 **`dms-api-from-ingress-only` NetworkPolicy**(`control-plane.yaml`에 포함, §5 apply 시 함께 적용)가
   강제한다: `dms-api` ingress를 `ingress-nginx` 네임스페이스에서만 허용. **cert 종단 지점이 다르면**
   (다른 proxy·네임스페이스) 그 `namespaceSelector`/`podSelector`를 실제 진입점에 맞게 고친다.

따라서 프로덕션 curl은 **client cert + server CA**를 쓰고 `x-dms-actor`는 보내지 않는다(§8).

> **노드 에이전트는 이 mTLS 평면을 쓰지 않는다.** agent는 actor가 `node:{cluster}:{node}`여야 하는데
> mTLS는 `mtls:<subject>`로 도출하므로 인증 불가 → 에이전트는 §5의 **전용 내부 API `dms-api-internal`**
> (mTLS off + shared token + agent-only NetworkPolicy)로 보고한다. 근거·설정은
> [`dms-06-configuration.md §1·§8`](dms-06-configuration.md).

> **부연(테스트베드/개발 프로필).** `DMS_REQUIRE_MTLS_VERIFIED_HEADER=false`로 내리면 인증서 없이
> 평문 `Authorization: Bearer <token>` + `x-dms-actor: <name>`만으로 호출할 수 있다. 이는 요청/응답
> 형태를 인증서 없이 읽기 위한 편의일 뿐 프로덕션 경로가 아니다. API 예시는
> [`../docs/api/README.md`](../docs/api/README.md) 참고.

---

## 5. control-plane 적용 + migration 확인

편집본을 적용한다. `dms-migrate` Job이 함께 뜨면서 두 DB(operational·observability)에 스키마를
적용한다(DB는 prereqs에서 준비돼 있어야 한다).

```bash
kubectl apply -f install/kubernetes/control-plane.yaml    # 편집본 경로
kubectl apply -f install/kubernetes/dms-api-internal.yaml  # 노드 에이전트 전용 내부 API (아래)

kubectl -n dms wait --for=condition=complete job/dms-migrate --timeout=120s
kubectl -n dms logs job/dms-migrate                        # "migrations applied"
```

> **`dms-api-internal` = agent 전용 인증 평면 (기본).** 외부 `dms-api`는 mTLS를 켜므로 actor가
> `mtls:<subject>`가 되어 노드 에이전트(`node:{cluster}:{node}` actor 필요)가 인증할 수 없다. 그래서
> 에이전트는 `image` 라인만 `control-plane.yaml`과 같은 ref로 맞춘 **별도 내부 API `dms-api-internal`**
> (mTLS **off** + `dms-secrets`의 shared token + agent-only NetworkPolicy, ClusterIP)로 보고한다.
> `agent-daemonset.yaml`의 `DMS_AGENT_API_URL`이 이 서비스를 가리킨다. 근거·프로필은
> [`dms-06-configuration.md §1·§8`](dms-06-configuration.md).

pod 확인:

```bash
kubectl -n dms get pods
# dms-api ×2, dms-api-internal, dms-planner, dms-rm-worker, dms-dm-worker(1/1), dms-migrate(Completed)
```

> **storages sync RBAC은 이미 `control-plane.yaml`에 포함**돼 있다(Role/RoleBinding
> `dms-agent-storages-sync`, `configmaps` get/update/patch on `dms-agent-storages`, `dms-api`와
> `dms-remote` **둘 다**에 바인딩). 이게 없으면 storage-mapping → ConfigMap 동기화가 `Forbidden`을
> **조용히 삼켜** no-op이 되고 → 새 파일시스템 스토리지가 에이전트에 전달되지 않아 RM은
> `missing_rm_readiness`, DM은 `no_ready_dm_candidate`가 된다. 존재 확인:
> ```bash
> kubectl -n dms get role,rolebinding dms-agent-storages-sync
> ```
> 에이전트는 `storages.json`을 **기동 시 1회만** 읽으므로, 스토리지 매핑을 바꾼 뒤에는 DaemonSet을
> rollout-restart 한다(`POST /api/v1/agent/rollout-restart`, [`dms-05-dm-jobs.md`](dms-05-dm-jobs.md)).

---

## 6. 추가 RBAC (`dms-api-volcano-rbac.yaml`)

`control-plane.yaml`에 **없는** 별도 RBAC이다. 반드시 적용한다.

```bash
kubectl apply -f install/kubernetes/dms-api-volcano-rbac.yaml
```

dms-api SA에 `pods/log`(+ Volcano read)를 부여한다 — `GET /api/v1/operations/data-jobs/{id}/logs`
(포탈의 데이터-잡 로그 tail)가 이 권한을 쓴다. **없으면 로그 tail이 `Forbidden`**으로 실패한다.

---

## 7. Ingress 배포

신뢰하는 ingress-nginx(mTLS 종단)가 설치돼 있어야 한다(prereqs). `install/kubernetes/ingress.example.yaml`을
템플릿으로 아래를 치환해 적용한다.

**편집 항목** (`ingress.example.yaml`):

- `spec.tls[0].hosts[0]` 및 `spec.rules[0].host` — `dms.example.internal` → 실제 API 호스트
  (§2 server cert의 CN/SAN과 **일치**해야 한다).
- `spec.tls[0].secretName` — `dms-api-tls` (§2에서 만든 TLS Secret).
- annotation `auth-tls-secret: "dms/dms-client-ca"` — Client CA Secret(namespace/name). 그대로.
- annotation은 mTLS를 강제한다(그대로 둔다):
  - `auth-tls-verify-client: "on"` — 클라이언트 인증서를 Client CA로 검증
  - `auth-tls-verify-depth: "2"`
  - `auth-tls-pass-certificate-to-upstream: "true"` — 검증한 인증서를 DMS로 전달(→ §4의 actor 유도)
  - `proxy-body-size: "2m"`

```bash
kubectl apply -f install/kubernetes/ingress.example.yaml    # 편집본
kubectl -n dms get ingress dms-api
```

---

## 8. 스모크 테스트

프로덕션 프로필(mTLS)로 호출한다. `x-dms-actor`는 보내지 않는다.

```bash
# 인증 불필요 health
curl -sS --cacert certs/dms-server-ca.crt https://dms.example.internal/healthz

# 인증 필요 read — actor는 operator cert의 CN에서 "mtls:operator"로 유도된다
curl -sS \
  --cert certs/operator.crt --key certs/operator.key \
  --cacert certs/dms-server-ca.crt \
  https://dms.example.internal/api/v1/operations/storage-mappings
# → 아직 매핑이 없으면 []
```

- `DMS_AUTH_SHARED_TOKEN`을 얹었다면 `-H "authorization: Bearer <token>"`을 추가한다.
- API 호스트가 아직 DNS에 없으면 `--resolve dms.example.internal:443:<INGRESS_IP>`를 붙인다.

여기까지면 코어가 떴다. `data_management` readiness가 아직 `Missing`이어도 정상이다 — DM 축
(에이전트·artifact FS·큐)이 [`dms-05-dm-jobs.md`](dms-05-dm-jobs.md)에서 구성되면 `Ready`로 바뀐다.

---

## 다음 문서

- [`dms-03-rm-filesystem.md`](dms-03-rm-filesystem.md) — 파일시스템 RM 설정(SSH 백엔드 credential,
  per-backend LDAP, 스토리지 매핑 등록).
- [`dms-04-rm-k8s-quota.md`](dms-04-rm-k8s-quota.md) — k8s 네임스페이스 쿼터 RM(타깃 클러스터 kubeconfig·RBAC).
- [`dms-05-dm-jobs.md`](dms-05-dm-jobs.md) — DM 잡(에이전트 DaemonSet, artifact FS, 신원, 큐).
- [`dms-06-configuration.md`](dms-06-configuration.md) — 환경변수 레퍼런스.
- [`portal-01-setup.md`](portal-01-setup.md) — 포탈 설치(별도 앱, BFF↔DMS mTLS).
- API 사용법은 [`../docs/api/README.md`](../docs/api/README.md)부터.
