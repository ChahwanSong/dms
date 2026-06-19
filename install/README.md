# DMS 운영 설치 가이드

이 디렉토리는 실제 Kubernetes 클러스터에 DMS를 설치하고 운영하기 위한 문서, 설정 예시, Kubernetes manifest, helper script를 모은다. 운영 설치 기준은 이 `install/` 디렉토리를 우선한다.

현재 구현 기준으로 운영 환경에서 열어도 되는 범위와 아직 열면 안 되는 범위가 다르다.

- Kubernetes namespace quota Resource Management: live Kubernetes `ResourceQuota/dms-storage-quota` create/update/block/delete/check/sync/import/audit 가능.
- Filesystem Resource Management: CephFS host-mounted adapter와 GPFS command adapter 가능. GPFS live 검증은 별도 staging 필요.
- Agent inventory: Kubernetes DaemonSet 기반 report 가능.
- Data Management `scan/sync/rm`: DB policy/API 기반 node/process resource model, MPIJob+Volcano scheduling, native VolcanoJob fallback, preflight 시 owner_username에 대한 read-only LDAP 신원 조회(직접) 통과 (+ DM denylist admission), fresh DM Agent report, POSIX preflight, writable shared artifact base가 준비된 경우 live execution을 운영할 수 있다. `sync`와 `rm`은 preview/confirm guard가 필수이고, separated-role `nsync`는 MPI/Volcano backend gate를 통과해야 한다.

## 문서 사용 방법

처음 설치한다면 아래 순서대로 읽고 실행한다.

1. 이 `README.md`의 순서대로 설치한다.
2. 설정값 의미가 헷갈리면 `CONFIGURATION.md`를 확인한다.
3. 설치 후 운영 점검, 장애 확인, 업그레이드는 `RUNBOOK.md`를 따른다.

명령은 DMS repository root에서 실행한다고 가정한다.

```bash
cd <dms-repo-root>
```

실제 경로가 다르면 repository root로 이동한 뒤 실행한다.

## 설치 디렉토리 구성

```text
install/
  README.md                              # 설치 절차
  CONFIGURATION.md                       # 설정 변수와 API 예시
  RUNBOOK.md                             # 운영 점검과 장애 대응
  postgresql/init.sql                    # PostgreSQL DB/user 생성 템플릿
  docker/Dockerfile                      # 운영 image build 템플릿
  docker/Dockerfile.mpifileutils         # Data Management mpifileutils job image build 템플릿
  config/dms-runtime.env.example         # 런타임 env 예시
  config/cluster-kubeconfigs.example.json
  config/agent-storages.example.json
  config/storage-mappings.example.json
  config/default-quota-policies.example.json
  config/identity-denylist.example.json
  kubernetes/control-plane.yaml          # API, Planner, RM Worker, Secret/ConfigMap
  kubernetes/agent-daemonset.yaml        # RM/DM Agent DaemonSet
  kubernetes/target-cluster-rbac.yaml    # Target cluster용 ServiceAccount/RBAC
  kubernetes/managed-rm-worker.yaml      # storage node local RM Worker 예시
  kubernetes/ingress.example.yaml        # ingress-nginx mTLS 예시
  scripts/create-serviceaccount-kubeconfig.sh
  scripts/register-storage-mappings.sh
  scripts/register-default-quota-policies.sh
  scripts/apply-identity-denylist.sh
  scripts/verify-install.sh
  scripts/dms-planned-shutdown.sh
  scripts/dms-startup-recovery-check.sh
  scripts/dms-resume.sh
```

## 0. 설치 전에 정할 값

먼저 운영 환경 값을 표로 정리한다. 아래 예시는 설명용이다.

| 항목 | 예시 | 어디에 사용 |
| --- | --- | --- |
| Control cluster kubeconfig context | `dms-control` | DMS API/Planner/RM Worker 배포 |
| Target cluster 이름 | `cluster-a`, `cluster-b` | DMS logical cluster name |
| Container registry | `registry.example.internal` | DMS image push/pull |
| DMS image tag | `registry.example.internal/dms:2026-06-02-abcdef0` | 모든 manifest image |
| DMS namespace | `dms` | 기본 manifest namespace |
| DMS API hostname | `dms.example.internal` | Ingress TLS/mTLS |
| PostgreSQL host | `postgres.example.internal:5432` | `DMS_DATABASE_URL` |
| Operational DB | `dms` | request/plan/run/resource 저장 |
| Observability DB | `dms_observability` | diagnostic event 저장 |
| LDAP URI/base DN | `ldap://ldap.example.internal:389`, `dc=example,dc=internal` | identity lookup/group |
| Target kubeconfig path inside Pod | `/etc/dms/kubeconfigs/cluster-a.kubeconfig` | `DMS_CLUSTER_KUBECONFIGS_JSON` |
| CephFS/GPFS RM SSH host | `cephfs-rm-1`, `gpfs-rm-1` | filesystem backend command |
| DM job image/artifacts | `registry.example.internal/dms-mpifileutils:<git-ref>`, `file:///artifacts/dms` | `DMS_DM_JOB_IMAGE`, `DMS_DM_ARTIFACT_BASE_URI` |
| DM MPI scheduling | Volcano scheduler, MPI Operator with Volcano gang scheduling | multi-node Data Management prerequisite |

For `file://` Data Management artifacts, `scan` result files are written under
`<DMS_DM_ARTIFACT_BASE_URI path>/<job_id>/`. `sync` and `rm` write phase-scoped
artifacts under `<job_id>/preview/` and `<job_id>/execution/`. Keep this artifact
base on a DMS-managed path that the Volcano Pod can write and the DM Worker can
read. Do not place it under a requester-private target/source/destination
directory unless the DM Worker has explicit traverse/read access.

> dm-worker와 DM job pod는 공유 FS의 *마운트포인트*를 `mountPropagation: HostToContainer`(rslave)로 마운트해,
> host가 세션 중 FS를 언/재마운트해도 컨테이너 bind가 stale되지 않고 새 마운트가 전파되도록 한다(서브경로를 기본
> `None` propagation으로 bind하면 dm-worker가 다른 노드 job artifact를 못 보게 된다). `control-plane.yaml`의
> dm-worker와 `managed-rm-worker.yaml`에 반영돼 있다.

관리 workstation에 필요한 도구도 확인한다.

```bash
kubectl version --client
docker version
openssl version
jq --version
curl --version
```

PostgreSQL을 직접 초기화한다면 `psql`도 필요하다.

```bash
psql --version
```

## 1. PostgreSQL 준비

DMS는 operational DB와 observability DB를 분리해서 사용한다. 운영에서는 두 DB를 같은 PostgreSQL instance 안에 둘 수는 있지만, DB user와 database는 분리한다.

### 1.1 init SQL 복사 및 비밀번호 교체

수정할 파일:

- `install/postgresql/init.sql`

이 파일을 직접 수정해서 운영 비밀번호를 넣을 수도 있지만, secret 값을 git working tree에 남기지 않는 것이 좋다. 권장 방식은 임시 파일을 만든 뒤 실행하고 삭제하는 것이다.

```bash
cp install/postgresql/init.sql /tmp/dms-init.sql
sed -i 's/CHANGE_ME_DMS_APP_PASSWORD/REPLACE_WITH_STRONG_APP_PASSWORD/g' /tmp/dms-init.sql
sed -i 's/CHANGE_ME_DMS_OBS_PASSWORD/REPLACE_WITH_STRONG_OBS_PASSWORD/g' /tmp/dms-init.sql
```

`REPLACE_WITH_STRONG_*`는 실제 긴 비밀번호로 바꾼다.

### 1.2 PostgreSQL에 적용

PostgreSQL superuser 또는 DB 생성 권한이 있는 계정으로 실행한다.

```bash
psql "postgresql://postgres@postgres.example.internal:5432/postgres" -f /tmp/dms-init.sql
rm -f /tmp/dms-init.sql
```

Managed PostgreSQL이라 role/database 생성 권한이 제한되어 있으면, DBA에게 다음을 요청한다.

- login role `dms_app`
- login role `dms_obs`
- database `dms`, owner `dms_app`
- database `dms_observability`, owner `dms_obs`
- 각 DB의 public schema에 table/sequence 생성 권한

### 1.3 DB 접속 확인

```bash
psql "postgresql://dms_app:REPLACE_WITH_STRONG_APP_PASSWORD@postgres.example.internal:5432/dms" -c 'select 1'
psql "postgresql://dms_obs:REPLACE_WITH_STRONG_OBS_PASSWORD@postgres.example.internal:5432/dms_observability" -c 'select 1'
```

두 명령 모두 `1`을 출력해야 한다.

## 2. DMS image build 및 registry push

수정할 파일:

- 필요 시 `install/docker/Dockerfile`

기본 Dockerfile은 `kubectl`, `ssh`, `postgres`, `ldap`, `kubernetes` dependency를 포함한다.

### 2.1 image tag 결정

```bash
export DMS_IMAGE="registry.example.internal/dms:$(git rev-parse --short HEAD)"
echo "$DMS_IMAGE"
```

### 2.2 build

```bash
docker build -f install/docker/Dockerfile -t "$DMS_IMAGE" .
```

Kubernetes version과 맞는 `kubectl`을 넣고 싶으면 build arg를 사용한다.

```bash
docker build \
  --build-arg KUBECTL_VERSION=v1.34.0 \
  -f install/docker/Dockerfile \
  -t "$DMS_IMAGE" .
```

### 2.3 push

```bash
docker push "$DMS_IMAGE"
```

Control cluster와 target cluster의 node에서 image pull이 가능한지 확인한다. 예를 들어 임시 Pod로 확인할 수 있다.

```bash
kubectl --context dms-control run dms-image-check \
  --image="$DMS_IMAGE" \
  --restart=Never \
  --command -- dms --help

kubectl --context dms-control logs pod/dms-image-check
kubectl --context dms-control delete pod dms-image-check
```

Private registry를 쓴다면 imagePullSecret을 별도로 만들고 manifest에 추가해야 한다. 현재 template에는 imagePullSecret이 들어 있지 않다.

### 2.4 Data Management job image build

Data Management는 DMS API/worker image에 우연히 존재하는 tool을 쓰지 않고
별도의 승인된 mpifileutils job image를 사용한다. Live 경로는
`dscan`, `dsync`, `drm`, `nsync`, Open MPI `mpirun`, `ssh`, `sshd`를 포함한 image가
필요하다. 운영 MPI runtime은 Open MPI이며, DMS가 생성하는
launcher command는 Open MPI TCP transport를 사용한다. MPI transport는 container 내부
SSH/launcher 구현을 사용할 수 있지만,
요청 payload가 SSH key, hostfile, NIC, raw mpirun option을 직접 지정하면 안 된다.

수정할 파일:

- 필요 시 `install/docker/Dockerfile.mpifileutils`

기본 Dockerfile은 `chahwansong/mpifileutils` pinned ref를 Open MPI로 빌드하고
`dscan`, `dsync`, `nsync`, `drm`, `mpirun`, `ompi_info`, OpenSSH client/server를
포함한다.

```bash
export DMS_DM_JOB_IMAGE="registry.example.internal/dms-mpifileutils:$(git rev-parse --short HEAD)"
docker build -f install/docker/Dockerfile.mpifileutils -t "$DMS_DM_JOB_IMAGE" .
docker push "$DMS_DM_JOB_IMAGE"
```

빌드한 image와 upstream ref를 control-plane manifest 또는 runtime env에 기록한다.

```bash
export DMS_DM_JOB_IMAGE_REF="chahwansong/mpifileutils@e3bfee10970bb4e24204d28689e3337e9741cca4"
```

Control cluster node에서 pull과 mpifileutils binary 실행이 가능한지 확인한다.

```bash
kubectl --context dms-control run dms-dm-image-check \
  --image="$DMS_DM_JOB_IMAGE" \
  --restart=Never \
  --command -- dscan --help

kubectl --context dms-control logs pod/dms-dm-image-check
kubectl --context dms-control delete pod dms-dm-image-check
```

### 2.5 Data Management MPI scheduling prerequisites

Single-node live Data Management는 단일 VolcanoJob worker pod 모델이다. Multi-node MPI
execution을 열려면 control/managed execution cluster에 Volcano와 MPI Operator를 함께
설치하고, MPI Operator가 Volcano gang scheduling을 사용하도록 설정해야 한다.

Multi-node MPI prerequisite:

- Volcano scheduler and CRDs
- MPI Operator with Volcano gang scheduling enabled
- `MPIJob` CRD
- DMS Data Management queue and priority classes
- RBAC for DM Worker to create/read/watch/delete MPIJob, VolcanoJob, PodGroup, Pods,
  Events, and logs in the DMS namespace
- shared RWX artifact path for `DMS_DM_ARTIFACT_BASE_URI=file://...`

DMS는 `MPIJob`이 Volcano PodGroup queue/gang/priority scheduling을 만들고 DMS worker
node affinity/anti-affinity를 보존할 수 있을 때만 MPIJob backend를 사용한다. 이를
검증할 수 없으면 DMS는 scheduling constraint를 약화시키지 않고 해당 Data Management job에
대해 native VolcanoJob launcher/worker orchestration을 사용한다.

## 3. Target cluster RBAC와 kubeconfig 생성

DMS가 관리할 각 target cluster마다 `install/kubernetes/target-cluster-rbac.yaml`을 적용하고, DMS control plane이 사용할 kubeconfig를 만든다.

### 3.1 target cluster에 RBAC 적용

수정할 파일:

- 보통은 수정 없이 `install/kubernetes/target-cluster-rbac.yaml` 사용
- namespace 또는 ServiceAccount 이름을 바꾸려면 이 파일과 script env를 같이 맞춘다

예: `cluster-a`에 적용

```bash
kubectl --context cluster-a apply -f install/kubernetes/target-cluster-rbac.yaml
kubectl --context cluster-a -n dms get serviceaccount dms-remote
kubectl --context cluster-a get clusterrole dms-remote-resource-management
kubectl --context cluster-a get clusterrolebinding dms-remote-resource-management
```

`cluster-b`도 관리한다면 반복한다.

```bash
kubectl --context cluster-b apply -f install/kubernetes/target-cluster-rbac.yaml
```

### 3.2 target cluster kubeconfig 생성

helper는 현재 `kubectl` context가 target cluster를 가리키는 상태에서 실행해야 한다.

```bash
mkdir -p /tmp/dms-kubeconfigs

kubectl config use-context cluster-a
install/scripts/create-serviceaccount-kubeconfig.sh \
  cluster-a \
  /tmp/dms-kubeconfigs/cluster-a.kubeconfig

kubectl config use-context cluster-b
install/scripts/create-serviceaccount-kubeconfig.sh \
  cluster-b \
  /tmp/dms-kubeconfigs/cluster-b.kubeconfig
```

생성된 kubeconfig가 target cluster를 읽을 수 있는지 확인한다.

```bash
KUBECONFIG=/tmp/dms-kubeconfigs/cluster-a.kubeconfig kubectl get storageclass
KUBECONFIG=/tmp/dms-kubeconfigs/cluster-b.kubeconfig kubectl get storageclass
```

### 3.3 control cluster Secret으로 kubeconfig 저장

```bash
kubectl --context dms-control create namespace dms --dry-run=client -o yaml | \
  kubectl --context dms-control apply -f -

kubectl --context dms-control -n dms create secret generic dms-cluster-kubeconfigs \
  --from-file=cluster-a.kubeconfig=/tmp/dms-kubeconfigs/cluster-a.kubeconfig \
  --from-file=cluster-b.kubeconfig=/tmp/dms-kubeconfigs/cluster-b.kubeconfig \
  --dry-run=client -o yaml | \
  kubectl --context dms-control apply -f -
```

Secret key 이름과 `DMS_CLUSTER_KUBECONFIGS_JSON` 안의 파일 경로가 일치해야 한다.

```text
Secret key: cluster-a.kubeconfig
Pod path:   /etc/dms/kubeconfigs/cluster-a.kubeconfig
```

## 4. mTLS/TLS 인증서와 API token 준비

운영 profile은 mTLS client certificate 검증과 shared bearer token을 함께 사용한다.

### 4.1 API token 생성

```bash
openssl rand -hex 32
```

출력값을 `DMS_AUTH_SHARED_TOKEN`으로 사용한다.

### 4.2 client certificate CA 준비

이미 조직 CA가 있으면 그 CA bundle을 사용한다. 실습용 self-signed CA 예시는 다음과 같다.

```bash
mkdir -p /tmp/dms-certs

openssl req -x509 -newkey rsa:4096 -nodes -days 365 \
  -keyout /tmp/dms-certs/dms-client-ca.key \
  -out /tmp/dms-certs/dms-client-ca.crt \
  -subj "/CN=dms-client-ca/O=example"
```

운영에서는 CA private key를 Kubernetes cluster에 넣지 않는다. DMS에는 `ca.crt`만 넣는다.

### 4.3 client certificate 발급 예시

```bash
openssl req -newkey rsa:2048 -nodes \
  -keyout /tmp/dms-certs/operator.key \
  -out /tmp/dms-certs/operator.csr \
  -subj "/CN=operator/O=example"

openssl x509 -req -days 90 \
  -in /tmp/dms-certs/operator.csr \
  -CA /tmp/dms-certs/dms-client-ca.crt \
  -CAkey /tmp/dms-certs/dms-client-ca.key \
  -CAcreateserial \
  -out /tmp/dms-certs/operator.crt \
  -sha256
```

DMS는 mTLS-required mode에서 actor를 `mtls:<certificate-subject>` 형태로 기록한다. 예시 certificate subject가 `CN=operator,O=example`이면 actor는 `mtls:CN=operator,O=example`이다.

### 4.4 Ingress server TLS secret 준비

서버 인증서는 조직 인증서나 cert-manager를 사용하는 것이 일반적이다. 직접 Secret을 만든다면 다음 형태다.

```bash
kubectl --context dms-control -n dms create secret tls dms-api-tls \
  --cert=/path/to/dms-api-server.crt \
  --key=/path/to/dms-api-server.key \
  --dry-run=client -o yaml | \
  kubectl --context dms-control apply -f -
```

Client certificate CA bundle Secret은 다음처럼 만든다.

```bash
kubectl --context dms-control -n dms create secret generic dms-client-ca \
  --from-file=ca.crt=/tmp/dms-certs/dms-client-ca.crt \
  --dry-run=client -o yaml | \
  kubectl --context dms-control apply -f -
```

## 5. SSH backend credential 준비

CephFS `ssh-host-exec` 또는 GPFS command runner를 쓰려면 RM Worker가 backend admin host에 SSH 접속할 수 있어야 한다.

### 5.1 SSH key 준비

운영에서는 backend mutation 권한이 허용된 전용 계정을 만들고, 그 계정의 SSH key를 사용한다.

```bash
ssh-keygen -t ed25519 -f /tmp/dms-backend-ssh -C dms-rm-worker
```

Public key를 backend host의 전용 계정 `authorized_keys`에 등록한다.

### 5.2 known_hosts 생성

```bash
ssh-keyscan cephfs-rm-1 gpfs-rm-1 > /tmp/dms-known_hosts
```

호스트명이 다르면 실제 `storage-mappings.json`의 `ssh_host`와 같은 이름을 사용한다.

### 5.3 Secret 생성

```bash
cat >/tmp/dms-ssh-config <<'EOF'
Host *
  IdentitiesOnly yes
  StrictHostKeyChecking yes
  UserKnownHostsFile /home/dms/.ssh/known_hosts
  IdentityFile /home/dms/.ssh/id_ed25519
EOF

kubectl --context dms-control -n dms create secret generic dms-ssh-client \
  --from-file=id_ed25519=/tmp/dms-backend-ssh \
  --from-file=known_hosts=/tmp/dms-known_hosts \
  --from-file=config=/tmp/dms-ssh-config \
  --dry-run=client -o yaml | \
  kubectl --context dms-control apply -f -
```

## 6. Control plane manifest 편집

수정할 파일:

- `install/kubernetes/control-plane.yaml`

운영에서는 이 파일을 직접 수정하기보다 복사본을 만드는 것을 권장한다.

```bash
cp install/kubernetes/control-plane.yaml /tmp/dms-control-plane.yaml
```

### 6.1 image tag 교체

```bash
sed -i "s#registry.example.internal/dms:CHANGE_ME#$DMS_IMAGE#g" /tmp/dms-control-plane.yaml
```

### 6.2 ConfigMap 수정

`/tmp/dms-control-plane.yaml`의 `ConfigMap/dms-runtime-config`에서 다음 값을 환경에 맞춘다.

```yaml
DMS_CONTROL_CLUSTER_NAME: "cluster-a"
DMS_KUBERNETES_INVENTORY_MODE: "kubectl"
DMS_KUBERNETES_MUTATION_MODE: "kubectl"
DMS_CLUSTER_KUBECONFIGS_JSON: '{"cluster-a":"/etc/dms/kubeconfigs/cluster-a.kubeconfig","cluster-b":"/etc/dms/kubeconfigs/cluster-b.kubeconfig"}'
DMS_FILESYSTEM_MUTATION_MODE: "ssh-host-exec"
DMS_REQUIRE_MTLS_HEADER: "true"
DMS_REQUIRE_MTLS_VERIFIED_HEADER: "true"
DMS_MTLS_ACTOR_PREFIX: "mtls:"
DMS_LDAP_URI: "ldap://ldap.example.internal:389"
DMS_LDAP_BASE_DN: "dc=example,dc=internal"
DMS_LDAP_USER_SEARCH_BASE: "ou=people,dc=example,dc=internal"
DMS_LDAP_GROUP_SEARCH_BASE: "ou=groups,dc=example,dc=internal"
```

주의:

- `DMS_REQUIRE_MTLS_HEADER=true`일 때 `DMS_DEFAULT_ACTOR`를 넣으면 API가 startup에서 실패한다.
- `DMS_CLUSTER_KUBECONFIGS_JSON`의 path는 Pod 내부 path다.
- `ssh-kubectl` 모드를 쓰려면 `DMS_CLUSTER_CONTROL_HOSTS_JSON`도 필요하다.

### 6.3 Secret placeholder 교체

같은 파일의 `Secret/dms-secrets`에 실제 값을 넣는다.

```yaml
DMS_DATABASE_URL: "postgresql://dms_app:REPLACE_WITH_STRONG_APP_PASSWORD@postgres.example.internal:5432/dms"
DMS_OBSERVABILITY_DATABASE_URL: "postgresql://dms_obs:REPLACE_WITH_STRONG_OBS_PASSWORD@postgres.example.internal:5432/dms_observability"
DMS_AUTH_SHARED_TOKEN: "REPLACE_WITH_RANDOM_TOKEN"
DMS_LDAP_BIND_DN: "cn=dms,ou=service-accounts,dc=example,dc=internal"
DMS_LDAP_BIND_PASSWORD: "REPLACE_WITH_LDAP_PASSWORD"
```

Secret 값이 들어간 `/tmp/dms-control-plane.yaml`은 commit하지 않는다.

### 6.4 embedded Secret을 이미 별도 생성했다면

앞 단계에서 `dms-cluster-kubeconfigs`, `dms-ssh-client`, `dms-client-ca`를 이미 만들었다면 `/tmp/dms-control-plane.yaml` 안의 같은 이름 Secret 문서를 삭제하거나 그대로 apply해서 덮어쓰지 않도록 주의한다.

초보자에게는 둘 중 하나를 권장한다.

- 방법 A: `/tmp/dms-control-plane.yaml` 안의 모든 Secret placeholder를 실제 값으로 채워 한 번에 apply한다.
- 방법 B: Secret은 `kubectl create secret ... --dry-run=client -o yaml | kubectl apply -f -`로 따로 만들고, `/tmp/dms-control-plane.yaml`에서는 Secret 문서를 삭제한다.

## 7. Control plane 배포

### 7.1 apply

```bash
kubectl --context dms-control apply -f /tmp/dms-control-plane.yaml
```

### 7.2 migration Job 확인

```bash
kubectl --context dms-control -n dms wait --for=condition=complete job/dms-migrate --timeout=180s
kubectl --context dms-control -n dms logs job/dms-migrate
```

Job이 실패하면 다음을 본다.

```bash
kubectl --context dms-control -n dms describe job dms-migrate
kubectl --context dms-control -n dms get pods -l job-name=dms-migrate
kubectl --context dms-control -n dms logs -l job-name=dms-migrate
```

가장 흔한 원인은 DB URL, 비밀번호, network policy, PostgreSQL 권한 문제다.

### 7.3 Deployment rollout 확인

```bash
kubectl --context dms-control -n dms rollout status deploy/dms-api --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-planner --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-rm-worker --timeout=180s
kubectl --context dms-control -n dms get pods,svc,deploy
```

`dms-dm-worker`는 기본 `replicas: 0`이 정상이다.

### 7.4 API Service 내부 확인

임시 port-forward로 내부 `/healthz`를 확인한다.

```bash
kubectl --context dms-control -n dms port-forward svc/dms-api 18080:80
```

다른 터미널에서:

```bash
curl -fsS http://127.0.0.1:18080/healthz
```

운영에서는 외부 API 요청이 ingress mTLS를 통과해야 하므로 port-forward는 설치 점검용으로만 사용한다.

## 8. Ingress 배포

수정할 파일:

- `install/kubernetes/ingress.example.yaml`

복사본을 만든다.

```bash
cp install/kubernetes/ingress.example.yaml /tmp/dms-ingress.yaml
```

### 8.1 hostname과 secret 이름 수정

`/tmp/dms-ingress.yaml`에서 다음을 실제 값으로 바꾼다.

```yaml
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - dms.example.internal
      secretName: dms-api-tls
  rules:
    - host: dms.example.internal
```

Annotation은 ingress-nginx 기준이다.

```yaml
nginx.ingress.kubernetes.io/auth-tls-secret: "dms/dms-client-ca"
nginx.ingress.kubernetes.io/auth-tls-verify-client: "on"
nginx.ingress.kubernetes.io/auth-tls-pass-certificate-to-upstream: "true"
```

이 annotation이 있어야 ingress가 client certificate을 검증하고 upstream에 `ssl-client-subject-dn`, `ssl-client-verify` evidence header를 전달한다.

### 8.2 apply 및 확인

```bash
kubectl --context dms-control apply -f /tmp/dms-ingress.yaml
kubectl --context dms-control -n dms get ingress dms-api
kubectl --context dms-control -n dms describe ingress dms-api
```

DNS가 아직 준비되지 않았다면 operator workstation의 `/etc/hosts` 또는 curl `--resolve`를 사용한다.

```bash
curl --resolve dms.example.internal:443:INGRESS_IP \
  --cert /tmp/dms-certs/operator.crt \
  --key /tmp/dms-certs/operator.key \
  --cacert /path/to/dms-api-server-ca.crt \
  -H "authorization: Bearer REPLACE_WITH_RANDOM_TOKEN" \
  https://dms.example.internal/api/v1/operations/action-required
```

Client certificate 없이 호출하면 실패해야 한다.

```bash
curl --resolve dms.example.internal:443:INGRESS_IP \
  --cacert /path/to/dms-api-server-ca.crt \
  https://dms.example.internal/api/v1/operations/action-required
```

정상 운영 profile에서는 위 명령이 401 또는 TLS client certificate error로 실패해야 한다.

## 9. Agent DaemonSet 배포

Agent는 StorageClass, CSI, mount, tool, credential, network, identity evidence를 DMS API로 report한다. Storage mapping readiness에 필요하다.

수정할 파일:

- `install/kubernetes/agent-daemonset.yaml`
- `install/config/agent-storages.example.json`

### 9.1 agent storage config 작성

예시 파일을 복사해서 실제 storage만 남긴다.

```bash
cp install/config/agent-storages.example.json /tmp/dms-agent-storages.json
```

예: CephFS와 Longhorn을 쓰는 cluster-a

```json
{
  "storages": [
    {
      "storage_name": "cephfs-a",
      "backend_type": "cephfs",
      "cluster_name": "cluster-a",
      "storage_class_name": "cephfs-rwx",
      "csi_driver": "rook-ceph.cephfs.csi.ceph.com",
      "mount_paths": ["/mnt/cephfs-rwx"],
      "network_endpoints": []
    },
    {
      "storage_name": "longhorn-a",
      "backend_type": "longhorn",
      "cluster_name": "cluster-a",
      "storage_class_name": "longhorn",
      "csi_driver": "driver.longhorn.io",
      "mount_paths": [],
      "network_endpoints": []
    }
  ]
}
```

ConfigMap으로 적용한다.

```bash
kubectl --context cluster-a create namespace dms --dry-run=client -o yaml | \
  kubectl --context cluster-a apply -f -

kubectl --context cluster-a -n dms create configmap dms-agent-storages \
  --from-file=storages.json=/tmp/dms-agent-storages.json \
  --dry-run=client -o yaml | \
  kubectl --context cluster-a apply -f -
```

### 9.2 agent manifest 수정

복사본을 만든다.

```bash
cp install/kubernetes/agent-daemonset.yaml /tmp/dms-agent-daemonset.yaml
sed -i "s#registry.example.internal/dms:CHANGE_ME#$DMS_IMAGE#g" /tmp/dms-agent-daemonset.yaml
```

`ConfigMap/dms-agent-runtime-config`에서 다음을 수정한다.

```yaml
DMS_AGENT_API_URL: "https://dms.example.internal"
DMS_AGENT_CLUSTER_NAME: "cluster-a"
DMS_AGENT_REPORT_INTERVAL_SECONDS: "60"
DMS_AGENT_TOOLS: "dsync,nsync,drm,dscan,kubectl"
```

`Secret/dms-agent-secrets`의 token도 control plane의 `DMS_AUTH_SHARED_TOKEN`과 같아야 한다.

주의: 현재 mTLS-required mode에서 agent report를 ingress로 보내려면 agent Pod도 client certificate/key/CA를 가져야 한다. 기본 `agent-daemonset.yaml`은 bearer token Secret만 포함한다. 운영에서는 다음 중 하나가 필요하다.

- agent 전용 client certificate Secret을 만들고 DaemonSet에 mount한 뒤 agent HTTP client가 그 certificate을 사용하도록 구현/설정한다.
- 또는 운영자가 문서화한 내부 authentication boundary를 사용하고, direct spoof를 막는 네트워크 제어를 둔다.

현재 코드 기준으로 `POST /api/v1/agent/reports`는 mTLS actor가 `node:{cluster}:{node}`와 일치해야 Fresh report로 저장된다. mTLS subject-to-node actor mapping은 아직 별도 구현이 필요하다.

### 9.3 apply 및 확인

```bash
kubectl --context cluster-a apply -f /tmp/dms-agent-daemonset.yaml
kubectl --context cluster-a -n dms rollout status daemonset/dms-rm-agent --timeout=180s
kubectl --context cluster-a -n dms get pods -l app.kubernetes.io/name=dms-rm-agent -o wide
```

Data Management live execution을 운영하려면 DM Agent report가 Fresh여야 한다.
`scan`/`sync`/`rm`을 열지 않는 환경에서는 배포하지 않을 수 있지만, 열 경우
`dms-dm-agent`도 rollout과 report freshness를 확인한다.

## 10. Storage mapping 등록

Storage mapping은 `storage_name`과 실제 backend 정보를 연결한다. 모든 RM request는 mapping sanity/readiness guard를 통과해야 한다.

수정할 파일:

- `install/config/storage-mappings.example.json`

### 10.1 파일 작성

```bash
cp install/config/storage-mappings.example.json /tmp/dms-storage-mappings.json
```

실제 backend만 남기고 값들을 바꾼다.

> **filesystem backend(cephfs/wekafs/gpfs)는 `managed_root`를 반드시 명시**해야 한다 — 생략하면 등록이 `422`로
> 거부된다(과거의 `mount_path/dms` 암묵 기본값은 제거됨). `managed_root`는 `mount_path` 아래의 절대경로여야 하며,
> RM 디렉토리 연산과 DM `DMS_DM_PATH_BASE=managed_root` 모드의 경계/기준점이 된다. (CSI-only/namespace-quota용
> mapping은 filesystem backend가 아니므로 managed_root가 필요 없다.)

CephFS 예시:

```json
{
  "storage_name": "cephfs-a",
  "backend_template": {
    "backend_type": "cephfs",
    "cluster_name": "cluster-a",
    "mount_path": "/mnt/cephfs-rwx",
    "managed_root": "/mnt/cephfs-rwx/dms",
    "rm_worker_nodes": ["cephfs-rm-1"],
    "ssh_host": "cephfs-rm-1",
    "csi_driver": "rook-ceph.cephfs.csi.ceph.com"
  },
  "cluster_name": "cluster-a",
  "storage_class_name": "cephfs-rwx"
}
```

Kubernetes StorageClass backend 예시:

```json
{
  "storage_name": "longhorn-a",
  "backend_template": {
    "backend_type": "longhorn",
    "csi_driver": "driver.longhorn.io"
  },
  "cluster_name": "cluster-a",
  "storage_class_name": "longhorn"
}
```

GPFS 예시:

```json
{
  "storage_name": "gpfs-a",
  "backend_template": {
    "backend_type": "gpfs",
    "filesystem_name": "gpfs0",
    "mount_path": "/gpfs/gpfs0",
    "fileset_root": "/gpfs/gpfs0/dms",
    "quota_scope": "fileset",
    "fileset_name_template": "dms-{directory_name}",
    "rm_worker_nodes": ["gpfs-rm-1"],
    "ssh_host": "gpfs-rm-1",
    "command_runner": "ssh-host-exec",
    "command_timeout_seconds": 300,
    "csi_driver": "spectrumscale.csi.ibm.com"
  },
  "cluster_name": "cluster-a",
  "storage_class_name": "gpfs-csi"
}
```

WEKA 예시:

```json
{
  "storage_name": "weka-a",
  "backend_template": {
    "backend_type": "wekafs",
    "filesystem_name": "default",
    "mount_path": "/weka/default",
    "managed_root": "/weka/default/dms",
    "quota_scope": "directory",
    "rm_worker_nodes": ["weka-rm-1"],
    "ssh_host": "weka-rm-1",
    "command_runner": "ssh-host-exec",
    "csi_driver": "csi.weka.io"
  },
  "cluster_name": "cluster-a",
  "storage_class_name": "weka-sc"
}
```

WEKA filesystem backend(`backend_type: wekafs`)도 CephFS/GPFS와 함께 host-mounted
filesystem create/update/block/delete/check/sync/import 경로를 지원한다
(`WekaFsHostMountedFilesystemBackendAdapter`). 단 WEKA는 inode(`file_count`) quota를
지원하지 않으므로 `quota`에는 `capacity_bytes`만 보낸다(`file_count`를 주면 조용히 무시하지
않고 `BackendApplyFailed`로 실패). Kubernetes namespace quota는 backend type과 무관한 live
`ResourceQuota` 경로를 사용하므로, 어떤 CSI StorageClass든 provisioner가 `csi_driver`와
일치하고 storage mapping sanity/readiness가 `Ready`이면 `storage_class_quotas[].storage_name`
대상으로 사용할 수 있다.

### 10.2 등록 명령

운영 mTLS 환경에서 helper를 실행한다.

```bash
export DMS_API_URL="https://dms.example.internal"
export DMS_TOKEN="REPLACE_WITH_RANDOM_TOKEN"
export DMS_CLIENT_CERT="/tmp/dms-certs/operator.crt"
export DMS_CLIENT_KEY="/tmp/dms-certs/operator.key"
export DMS_CA_CERT="/path/to/dms-api-server-ca.crt"
unset DMS_ACTOR

install/scripts/register-storage-mappings.sh /tmp/dms-storage-mappings.json
```

### 10.3 readiness 확인

```bash
curl -fsS \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" \
  "$DMS_API_URL/api/v1/operations/storage-mappings" | jq
```

`readiness.resource_management`가 `Ready`여야 Resource Management request가 진행된다. DM readiness는 Data Management 단계 전까지 `Missing` 또는 `Degraded`일 수 있다.

## 11. 기본 quota policy 등록

수정할 파일:

- `install/config/default-quota-policies.example.json`

```bash
cp install/config/default-quota-policies.example.json /tmp/dms-default-quota-policies.json
```

예시:

```json
{
  "default_quota_policies": [
    {
      "resource_kind": "filesystem",
      "resource_type": "user",
      "quota": {
        "capacity_bytes": 1000000000000,
        "file_count": 1000000
      }
    },
    {
      "resource_kind": "kubernetes_namespace_quota",
      "resource_type": "user",
      "quota": {
        "requests_storage_bytes": 1000000000000,
        "pvc_count": 20,
        "storage_class_quotas": [
          {
            "storage_name": "longhorn-a",
            "requests_storage_bytes": 1000000000000,
            "pvc_count": 20
          }
        ]
      }
    }
  ]
}
```

등록:

```bash
install/scripts/register-default-quota-policies.sh /tmp/dms-default-quota-policies.json
```

## 12. DM 신원 처리 (LDAP preflight + denylist)

이전의 `identity_mappings` 테이블과 `/api/v1/identity-mappings*` API는 제거됐다(대체됨). 더 이상 requester와 POSIX user를 사전 등록/sync하지 않는다. 대신 DM은 preflight 시점에 dm-worker에서 owner_username에 대한 read-only LDAP 조회(search-only)로 POSIX 신원을 직접 resolve한다. owner_username은 requester_id(자유 형식 logical id)를 기본값으로 하고, 실제 POSIX username으로 override할 수 있다(RM owner model과 동일).

이 방식은 FAIL CLOSED다. TTL 캐시가 없으므로 LDAP가 끊기면 stale 신원으로 통과시키지 않고 preflight를 중단한다.

- LDAP 미설정: `ldap_not_configured`
- LDAP 응답 불가: `ldap_unavailable`
- 사용자 없음: `ldap_identity_not_found`
- denylist에 의해 차단: `identity_denied`

### 12.1 dm-worker LDAP 설정

preflight가 신원을 직접 조회할 수 있도록 dm-worker에 `DMS_LDAP_*` env가 채워져 있어야 한다(6.2/6.3에서 설정한 값과 동일). provider는 `DMS_DM_IDENTITY_PROVIDER`로 고른다(기본값 `ldap`).

```yaml
DMS_DM_IDENTITY_PROVIDER: "ldap"
DMS_LDAP_URI: "ldap://ldap.example.internal:389"
DMS_LDAP_BASE_DN: "dc=example,dc=internal"
DMS_LDAP_USER_SEARCH_BASE: "ou=people,dc=example,dc=internal"
DMS_LDAP_GROUP_SEARCH_BASE: "ou=groups,dc=example,dc=internal"
DMS_LDAP_BIND_DN: "cn=dms,ou=service-accounts,dc=example,dc=internal"
DMS_LDAP_BIND_PASSWORD: "REPLACE_WITH_LDAP_PASSWORD"
```

upstream LDAP을 local OpenLDAP으로 복제해서 쓰는 환경이라면 별도 관심사인 `install/scripts/sync-ldap-to-local.py`를 계속 사용한다.

### 12.2 DM identity denylist (선택)

denylist는 DM 측에 유일하게 persist되는 신원 상태다. requester/owner/group 단위의 즉시 kill-switch이자 admission block이며, 기본값은 비어 있어 모두 allow한다. 등록은 선택이고, 평소에는 비워 둔다.

특정 주체를 차단/해제/조회하려면 `identity-denylist` API를 쓴다. `{subject_type}`은 `requester`, `owner`, `group` 중 하나다.

```bash
# 차단 추가
curl -fsS -X PUT \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" \
  "$DMS_API_URL/api/v1/data-management/identity-denylist/requester/alice"

# 차단 해제
curl -fsS -X DELETE \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" \
  "$DMS_API_URL/api/v1/data-management/identity-denylist/requester/alice"

# 차단 조회
curl -fsS \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" \
  "$DMS_API_URL/api/v1/data-management/identity-denylist/requester/alice" | jq
```

차단 목록을 한 번에 seed하려면 예시 파일과 bulk-seed script를 쓴다.

```bash
cp install/config/identity-denylist.example.json /tmp/dms-identity-denylist.json
install/scripts/apply-identity-denylist.sh /tmp/dms-identity-denylist.json
```

denylist에 올라간 주체의 요청은 preflight에서 `identity_denied`로 중단된다.

## 13. 설치 검증

### 13.1 helper 검증

```bash
export DMS_API_URL="https://dms.example.internal"
export DMS_TOKEN="REPLACE_WITH_RANDOM_TOKEN"
export DMS_CLIENT_CERT="/tmp/dms-certs/operator.crt"
export DMS_CLIENT_KEY="/tmp/dms-certs/operator.key"
export DMS_CA_CERT="/path/to/dms-api-server-ca.crt"
unset DMS_ACTOR

install/scripts/verify-install.sh
```

확인하는 항목:

- `/healthz`
- control state query
- inventory query
- storage mapping query
- work summary query
- active plan/run query
- drain status query
- stale/recovery run query
- worker/agent health query
- action-required query

### 13.2 Kubernetes namespace quota smoke test

운영에 영향 없는 namespace 이름을 사용한다.
이 요청은 CephFS, Longhorn, GPFS CSI, WEKA CSI 같은 모든 CSI StorageClass
backend에서 같은 Kubernetes `ResourceQuota/dms-storage-quota` live adapter를
사용한다. GPFS namespace quota smoke test에서도 IBM Storage Scale `mm*` command는
실행되지 않는다.

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "operator",
    "payload": {
      "cluster_name": "cluster-a",
      "namespace_name": "dms-smoke-quota",
      "storage_class_quotas": [{"storage_name": "longhorn-a"}],
      "quota": {"requests_storage_bytes": 1073741824, "pvc_count": 2},
      "expires_at": "2099-01-01T00:00:00Z",
      "allow_namespace_create": true
    }
  }' | jq
```

request id를 얻은 뒤 상태를 확인한다.

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/requests?requester_id=operator&limit=10" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" | jq
```

Target cluster에서 ResourceQuota가 생겼는지 확인한다.

```bash
kubectl --context cluster-a -n dms-smoke-quota get resourcequota dms-storage-quota -o yaml
```

정리하려면 DMS delete request를 사용한다.

```bash
curl -fsS -X DELETE "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/dms-smoke-quota" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{"requester_id":"operator","payload":{}}' | jq
```

DMS delete는 `ResourceQuota/dms-storage-quota`를 삭제하고 namespace 자체는 삭제하지 않는다. 필요하면 운영자가 namespace를 별도로 삭제한다.

```bash
kubectl --context cluster-a delete namespace dms-smoke-quota
```

### 13.3 Data Management smoke test

Data Management live execution을 열기 전에 다음 조건을 먼저 확인한다.

- target/source/destination storage mapping의 `readiness.data_management=Ready`
- requester의 `owner_username`이 dm-worker preflight의 read-only LDAP 조회로 POSIX 신원으로 해석됨 (§12 — `identity_mappings` 사전 등록 계층은 제거됨) + DM denylist에 차단 항목 없음
- DM Agent report에 mount, required tool(`dscan`, `dsync`, `drm`), credential, network, POSIX user evidence가 Fresh
- `DMS_DM_JOB_IMAGE`, `DMS_DM_JOB_IMAGE_REF`, `DMS_DM_ARTIFACT_BASE_URI`가 운영 값으로 설정됨
- (선택) `DMS_DM_PATH_BASE` — 요청 path 기준점. 기본 `mount_path`(현행), `managed_root`면 planner가 storage별 `managed_root` suffix를 prepend(filesystem mapping에 `managed_root` 명시 필수). 켜면 요청 path를 managed_root 기준으로 적는다(아래 smoke 예시 path도 그에 맞춰 조정)
- Volcano CRD/scheduler가 control 또는 managed cluster에서 동작 중
- multi-node MPI execution을 열 경우 MPI Operator가 Volcano gang scheduling으로
  설치되어 있고 `MPIJob` CRD가 동작 중

조건이 맞으면 DM Worker를 1 replica로 올린다.

```bash
kubectl --context dms-control -n dms scale deploy/dms-dm-worker --replicas=1
kubectl --context dms-control -n dms rollout status deploy/dms-dm-worker --timeout=180s
```

작은 directory로 scan을 제출한다.

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/data-management/scan" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "alice",
    "target": {"storage_name": "cephfs-a", "path": "dms-smoke-fs"},
    "priority": "Mid",
    "options": {"summary_only": true}
  }' | jq
```

상태와 artifact URI를 조회한다.

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/data-jobs?requester_id=alice&operation=data.scan&limit=5" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" | jq
```

`result_summary.report_uri`, `stdout_uri`, `stderr_uri`, `summary_uri`가
`DMS_DM_ARTIFACT_BASE_URI/<job_id>/...` 형태로 기록돼야 한다.

`sync`/`rm` smoke는 반드시 작은 테스트 directory에서 preview와 confirm을
분리해서 수행한다. 예시는 same-storage `dsync` 경로다.

Single-node live Data Management는 resource fan-out 없이 실행된다.
`scan`, same-node `sync`, `rm`은 각각 1 selected node, 1 worker pod, 1 process로
실행되며, `result_summary.selected_node`, `worker_pod_count`, `process_count`로
evidence를 확인한다. separated-role `nsync`가 필요한 topology는 mutation 없이
`data_job_nsync_deferred` action-required로 남아야 정상이다.

Multi-node MPI Data Management는 DMS가 ready mounted node set을
eligible set으로 제출하고, Volcano/Kubernetes scheduler가 그중 실제 feasible nodes를
선택한다. 모든 job은 submitted CR YAML과 MPI metadata artifact를 남겨야 한다.

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/data-management/sync" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "alice",
    "source": {"storage_name": "cephfs-a", "path": "dms-smoke-src"},
    "destination": {"storage_name": "cephfs-a", "path": "dms-smoke-dst"},
    "options": {"contents": true}
  }' | jq

curl -fsS "$DMS_API_URL/api/v1/operations/data-jobs?requester_id=alice&operation=data.sync&limit=5" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" | jq
```

detail의 `state`가 `ConfirmPending`이고 `result_summary.preview.summary.dry_run=true`
이면 preview artifact를 검토한 뒤 confirm한다.

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/data-management/jobs/<job_id>:confirm" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "alice",
    "confirm": true,
    "preview_observed_hash": "sha256:<preview-fingerprint>"
  }' | jq
```

`rm` smoke도 같은 confirm 절차를 사용한다. `target`은 storage root가 아닌 작은
테스트 directory여야 하며 `options.recursive=true`를 명시한다.

### 13.4 Filesystem smoke test

CephFS/GPFS RM을 활성화했다면 작은 테스트 directory로 시작한다.

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/filesystems" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "operator",
    "payload": {
      "storage_name": "cephfs-a",
      "directory_name": "dms-smoke-fs",
      "resource_type": "user",
      "users": ["alice", "bob"],
      "quota": {"capacity_bytes": 1073741824, "file_count": 10000},
      "expires_at": "2099-01-01T00:00:00Z"
    }
  }' | jq
```

성공 후 backend host에서 directory, ownership, quota xattr 또는 GPFS fileset/quota를 확인한다. CephFS 예시:

```bash
ssh cephfs-rm-1 'ls -ld /mnt/cephfs-rwx/dms/dms-smoke-fs'
ssh cephfs-rm-1 'getfattr -d -m "ceph.quota.*" /mnt/cephfs-rwx/dms/dms-smoke-fs'
```

## 14. 설치 후 정상 상태

Control cluster:

```bash
kubectl --context dms-control -n dms get pods,jobs,svc,ingress
```

정상 예:

- `job/dms-migrate` 완료
- `deploy/dms-api` Ready
- `deploy/dms-planner` Ready
- `deploy/dms-rm-worker` Ready
- `deploy/dms-dm-worker` replicas 0, 또는 Data Management live execution을 활성화한 환경에서는 Ready 1
- `svc/dms-api` 존재
- `ingress/dms-api` 존재

DMS API:

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/action-required" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" | jq
```

정상 steady state에서는 action-required가 비어 있어야 한다.

## 15. Planned shutdown, startup recovery, resume

운영자가 DMS image update, DB migration, control cluster reboot, node drain을 수행하기 전에는 DMS API의 drain mode를 먼저 켠다. DB의 `dms_control_state`가 source of truth이며, maintenance/drain 중 새 Resource Management/Data Management 요청과 control/config mutation은 409로 거부된다. 조회 endpoint는 계속 사용할 수 있다.

### 15.1 planned shutdown

환경변수를 설정한다.

```bash
export DMS_API_URL="https://dms.example.internal"
export DMS_TOKEN="REPLACE_WITH_RANDOM_TOKEN"
export DMS_CLIENT_CERT="/tmp/dms-certs/operator.crt"
export DMS_CLIENT_KEY="/tmp/dms-certs/operator.key"
export DMS_CA_CERT="/path/to/dms-api-server-ca.crt"
export DMS_NAMESPACE="dms"
export DMS_KUBECTL_CONTEXT="dms-control"
export DMS_WORKER_DEPLOYMENTS="dms-rm-worker dms-dm-worker"
unset DMS_ACTOR
```

먼저 Kubernetes scale down 없이 API drain 동작과 readiness만 확인한다.

```bash
install/scripts/dms-planned-shutdown.sh \
  --reason "source update dry-run $(date -Iseconds)" \
  --timeout-seconds 120 \
  --poll-seconds 5 \
  --dry-run
```

실제 planned shutdown에서는 dry-run을 빼고 실행한다.

```bash
install/scripts/dms-planned-shutdown.sh \
  --reason "source update $(date -Iseconds)" \
  --timeout-seconds 900 \
  --poll-seconds 10
```

이 script가 하는 일:

- `POST /api/v1/operations/control-state:begin-drain`
- `GET /api/v1/operations/drain-status`를 반복 조회
- `GET /api/v1/operations/work-summary` 출력
- `deploy/dms-rm-worker`, `deploy/dms-dm-worker`를 0 replica로 scale down

이 script가 하지 않는 일:

- Kubernetes node `cordon/drain/reboot`
- PostgreSQL backup
- DMS image 변경

### 15.2 startup recovery check

API Pod와 DB가 올라온 뒤 worker를 다시 열기 전에 recovery 상태를 확인한다.

```bash
install/scripts/dms-startup-recovery-check.sh
```

이 script는 다음을 수행한다.

- `POST /api/v1/operations/runs:mark-stale`
- `GET /api/v1/operations/control-state`
- `GET /api/v1/operations/drain-status`
- `GET /api/v1/operations/work-summary`
- `GET /api/v1/operations/runs/stale`
- `GET /api/v1/operations/action-required`
- `GET /api/v1/operations/worker-agent-health`

`RecoveryNeeded`, `UnknownAfterSideEffect`, `BackendApplyFailed`가 있으면 resume하지 말고 backend live state와 `action-required`를 먼저 확인한다. action-required 항목을 운영자가 확인했고 resume이 필요하면 다음처럼 명시한다.

```bash
install/scripts/dms-startup-recovery-check.sh --allow-action-required
```

### 15.3 resume

recovery check가 통과하면 control state를 normal로 되돌리고 worker를 다시 scale up한다.

```bash
export DMS_WORKER_DEPLOYMENTS="dms-rm-worker"
install/scripts/dms-resume.sh \
  --reason "source update completed $(date -Iseconds)" \
  --replicas 1
```

recovery blocker가 남아 있으면 기본적으로 API가 409를 반환한다. 운영자가 live state를 확인했고 강제 resume이 필요하면 `--force`를 사용한다.

```bash
export DMS_WORKER_DEPLOYMENTS="dms-rm-worker"
install/scripts/dms-resume.sh \
  --reason "operator accepted recovery items $(date -Iseconds)" \
  --force \
  --replicas 1
```

Data Management live execution 전제조건이 준비되지 않은 환경에서는 `dms-dm-worker`를 0 replica로 유지한다. 이 경우 resume 후 직접 scale을 조정한다.

```bash
kubectl --context dms-control -n dms scale deploy/dms-dm-worker --replicas=0
```

### 15.4 수동 API 확인

script를 쓰지 않고 직접 확인하려면 다음 endpoint를 사용한다.

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/control-state" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" | jq

curl -fsS "$DMS_API_URL/api/v1/operations/work-summary" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" | jq

curl -fsS "$DMS_API_URL/api/v1/operations/runs/active" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" | jq
```

## 16. 자주 발생하는 문제

### API Pod가 시작하지 않음

확인:

```bash
kubectl --context dms-control -n dms describe pod -l app.kubernetes.io/name=dms-api
kubectl --context dms-control -n dms logs deploy/dms-api
```

흔한 원인:

- `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`인데 `DMS_REQUIRE_MTLS_HEADER=true`가 아님
- `DMS_REQUIRE_MTLS_HEADER=true`인데 `DMS_DEFAULT_ACTOR`가 설정됨
- PostgreSQL URL 또는 비밀번호 오류
- Secret key 누락

### mTLS 요청이 401 또는 TLS error

확인:

```bash
curl -v \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" \
  "$DMS_API_URL/api/v1/operations/action-required"
```

흔한 원인:

- client certificate이 `dms-client-ca`로 검증되지 않음
- ingress annotation 누락
- bearer token 불일치
- `DMS_CA_CERT`가 server certificate CA가 아님
- DMS API Service에 직접 접근해서 trusted mTLS evidence header가 없음

### Storage mapping이 `Failed` 또는 `Degraded`

확인:

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/storage-mappings" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" | jq
```

흔한 원인:

- `storage_class_name`이 target cluster에 없음
- `csi_driver`가 StorageClass provisioner와 다름
- Agent report가 Fresh가 아님
- filesystem mount path를 agent가 볼 수 없음
- `backend_template.backend_type` 누락

### RM Worker가 backend mutation 실패

확인:

```bash
kubectl --context dms-control -n dms logs deploy/dms-rm-worker --tail=200
curl -fsS "$DMS_API_URL/api/v1/operations/action-required" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" | jq
```

흔한 원인:

- target kubeconfig 권한 부족
- SSH key 또는 known_hosts 오류
- backend host에서 `setfattr`/`getfattr` 또는 GPFS command 없음
- LDAP/SSSD group membership 전파 지연
- quota 감소 precondition 실패

## 17. 운영 안전 주의사항

- 예시 password나 token을 사용하지 않는다.
- Secret 값이 들어간 파일을 git commit하지 않는다.
- Ingress authentication 없이 DMS API를 노출하지 않는다. 운영 환경에서는 mTLS client certificate 검증과 `DMS_AUTH_SHARED_TOKEN`을 함께 사용한다.
- DMS API Pod/Service로 직접 접근할 수 있으면 mTLS evidence header spoofing이 가능하므로 NetworkPolicy 또는 동등한 네트워크 제어를 적용한다.
- `DMS_DEFAULT_ACTOR`는 운영 환경에서 설정하지 않는다.
- 운영 script 호출 시에는 `DMS_CLIENT_CERT`, `DMS_CLIENT_KEY`, `DMS_CA_CERT`, `DMS_TOKEN`을 설정하고 `DMS_ACTOR`는 unset 상태로 둔다.
- Target cluster kubeconfig가 `target-cluster-rbac.yaml`의 RBAC 범위로 제한되어 있는지 확인한다.
- 하나의 storage mapping과 하나의 non-production namespace부터 시작한다.
- source update, control cluster reboot, planned node drain 전에는 `dms-planned-shutdown.sh`로 drain mode에 진입한다.
- startup 후에는 `dms-startup-recovery-check.sh`를 통과한 다음 `dms-resume.sh`를 실행한다.
- `dms-dm-worker`는 0 replica로 유지한다.
- `UnknownAfterSideEffect`, `BackendApplyFailed`, action-required 항목은 운영 사고로 취급한다.
- 업그레이드 전 operational DB와 observability DB를 모두 백업한다.

## 18. 다음 문서

- 설정 변수와 API 예시는 `install/CONFIGURATION.md`.
- 일일 점검, 장애 대응, 업그레이드는 `install/RUNBOOK.md`.
- Backend 추가 방법은 `docs/backend-extension-guide.md`.
