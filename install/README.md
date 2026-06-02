# DMS 운영 설치 가이드

이 디렉토리는 실제 Kubernetes 클러스터에 DMS를 설치하고 운영하기 위한 문서, 설정 예시, Kubernetes manifest, helper script를 모은다. 기존 `deploy/` 디렉토리는 phase/testbed 검증에 사용된 manifest가 섞여 있으므로 운영 설치 기준은 이 `install/` 디렉토리를 우선한다.

현재 구현 기준으로 운영 환경에서 열어도 되는 범위와 아직 열면 안 되는 범위가 다르다.

- Kubernetes namespace quota Resource Management: live Kubernetes `ResourceQuota/dms-storage-quota` create/update/block/delete/check/sync/import/audit 가능.
- Filesystem Resource Management: CephFS host-mounted adapter와 GPFS command adapter 가능. GPFS live 검증은 별도 staging 필요.
- Agent inventory: Kubernetes DaemonSet 기반 report 가능.
- Data Management `scan/sync/rm`: 현재 CLI `dm-worker`는 `StubVolcanoAdapter`를 사용하므로 production에서 실행하지 않는다. API는 배포되더라도 DM worker replica는 0으로 둔다.

## 문서 사용 방법

처음 설치한다면 아래 순서대로 읽고 실행한다.

1. 이 `README.md`의 순서대로 설치한다.
2. 설정값 의미가 헷갈리면 `CONFIGURATION.md`를 확인한다.
3. 설치 후 운영 점검, 장애 확인, 업그레이드는 `RUNBOOK.md`를 따른다.

명령은 DMS repository root에서 실행한다고 가정한다.

```bash
cd /home/mason/workspace/dms
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
  config/dms-runtime.env.example         # 런타임 env 예시
  config/cluster-kubeconfigs.example.json
  config/agent-storages.example.json
  config/storage-mappings.example.json
  config/default-quota-policies.example.json
  config/identity-mappings.example.json
  kubernetes/control-plane.yaml          # API, Planner, RM Worker, Secret/ConfigMap
  kubernetes/agent-daemonset.yaml        # RM/DM Agent DaemonSet
  kubernetes/target-cluster-rbac.yaml    # Target cluster용 ServiceAccount/RBAC
  kubernetes/managed-rm-worker.yaml      # storage node local RM Worker 예시
  kubernetes/ingress.example.yaml        # ingress-nginx mTLS 예시
  scripts/create-serviceaccount-kubeconfig.sh
  scripts/register-storage-mappings.sh
  scripts/register-default-quota-policies.sh
  scripts/register-identity-mappings.sh
  scripts/verify-install.sh
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

DM Agent는 Data Management live execution이 열릴 때까지 운영에서 꼭 배포하지 않아도 된다. 배포한다면 `dms-dm-agent`도 확인한다.

## 10. Storage mapping 등록

Storage mapping은 `storage_name`과 실제 backend 정보를 연결한다. 모든 RM request는 mapping sanity/readiness guard를 통과해야 한다.

수정할 파일:

- `install/config/storage-mappings.example.json`

### 10.1 파일 작성

```bash
cp install/config/storage-mappings.example.json /tmp/dms-storage-mappings.json
```

실제 backend만 남기고 값들을 바꾼다.

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

WEKA는 아직 구현되지 않았으므로 active mapping에 넣지 않는다.

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

## 12. Identity mapping 등록

LDAP identity lookup을 사용할 경우 requester와 POSIX user를 연결한다.

수정할 파일:

- `install/config/identity-mappings.example.json`

```bash
cp install/config/identity-mappings.example.json /tmp/dms-identity-mappings.json
```

예시:

```json
{
  "identity_mappings": [
    {
      "identity_provider": "ldap",
      "requester_id": "alice",
      "posix_username": "alice",
      "expected_uid": 10001,
      "expected_primary_gid": 10001,
      "expected_groups": ["research-a"]
    }
  ]
}
```

등록:

```bash
install/scripts/register-identity-mappings.sh /tmp/dms-identity-mappings.json
```

조회:

```bash
curl -fsS \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" \
  "$DMS_API_URL/api/v1/identity-mappings" | jq
```

`NeedsReview`가 나오면 LDAP 실제 UID/GID/groups와 기대값이 다르다는 뜻이다.

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
- inventory query
- storage mapping query
- worker/agent health query
- action-required query

### 13.2 Kubernetes namespace quota smoke test

운영에 영향 없는 namespace 이름을 사용한다.

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

### 13.3 Filesystem smoke test

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
- `deploy/dms-dm-worker` replicas 0
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

## 15. 자주 발생하는 문제

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

## 16. 운영 안전 주의사항

- 예시 password나 token을 사용하지 않는다.
- Secret 값이 들어간 파일을 git commit하지 않는다.
- Ingress authentication 없이 DMS API를 노출하지 않는다. 운영 환경에서는 mTLS client certificate 검증과 `DMS_AUTH_SHARED_TOKEN`을 함께 사용한다.
- DMS API Pod/Service로 직접 접근할 수 있으면 mTLS evidence header spoofing이 가능하므로 NetworkPolicy 또는 동등한 네트워크 제어를 적용한다.
- `DMS_DEFAULT_ACTOR`는 운영 환경에서 설정하지 않는다.
- 운영 script 호출 시에는 `DMS_CLIENT_CERT`, `DMS_CLIENT_KEY`, `DMS_CA_CERT`, `DMS_TOKEN`을 설정하고 `DMS_ACTOR`는 unset 상태로 둔다.
- Target cluster kubeconfig가 `target-cluster-rbac.yaml`의 RBAC 범위로 제한되어 있는지 확인한다.
- 하나의 storage mapping과 하나의 non-production namespace부터 시작한다.
- `dms-dm-worker`는 0 replica로 유지한다.
- `UnknownAfterSideEffect`, `BackendApplyFailed`, action-required 항목은 운영 사고로 취급한다.
- 업그레이드 전 operational DB와 observability DB를 모두 백업한다.

## 17. 다음 문서

- 설정 변수와 API 예시는 `install/CONFIGURATION.md`.
- 일일 점검, 장애 대응, 업그레이드는 `install/RUNBOOK.md`.
- Backend 추가 방법은 `docs/backend-extension-guide.md`.
