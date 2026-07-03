# DMS 구성·셋업 가이드

이 디렉토리는 실제 Kubernetes 클러스터에 DMS를 설치·구성하기 위한 문서, 설정 예시, Kubernetes manifest, helper script를 모은다. 운영 설치 기준은 이 `install/` 디렉토리를 우선한다.

이 문서는 설치·구성·셋업에 필요한 **설정값, 셋업 절차, 주의사항**을 다룬다. 운영 중 데이터 잡·리소스 요청/조회 API 사용법은 별도 문서가 전담한다.

설치·읽기 순서 (권장 linear path):

0. **`0.prerequisites.md`** — 클러스터 사전 준비(PodSecurity=`privileged`, Volcano 설치, Queue `dms-data`+PriorityClass, 공유 RWX artifact FS, 노드 NSS/SSSD, 스토리지 host-mount). **가장 먼저** 갖춘다. 특히 DM(`scan`/`sync`/`rm`)은 이 중 하나라도 빠지면 잡이 에러 없이 **조용히 미실행**된다.
1. **이 `README.md`** — base DMS 설치·구성 절차(PostgreSQL, image build, mTLS/TLS, control plane, ingress, agent, storage mapping, quota policy, DM 신원, 검증, shutdown/resume). 아래 §1~§18. `1.install-dms-on-pvs.md`는 실제 클러스터에 적용한 worked example(테스트베드 기록, 참고용)이다.
2. `2.dms-rm-api-fs.md` — filesystem Resource Management API
3. `3.dms-rm-api-k8s.md` — Kubernetes namespace quota API
4. `4.dms-dm-api.md` — Data Management `scan`/`sync`/`rm` API (operations 조회 포함)
5. `5.dms-portal-setup.md` — DMS Portal(운영자/사용자 웹 UI) 설치·구성. DMS API만 소비하는 별도 앱이며 DMS 설치(위) 이후 진행한다.
6. `6.dms-portal-data-backup.md` — Portal 데이터 백업(DM `sync` 미러 백업) 운영. 포탈 DB(`PORTAL_DB_URL`)·배치 미리보기/승인/실행·경로 규칙.
7. `7.dms-portal-dashboard.md` — Portal 종합 운영 대시보드(읽기 전용). 스케줄러·큐/작업·worker node·요청·조치 필요 패널.
8. `8.dms-portal-data-scan.md` — Portal 데이터 스캔(DM `scan`) 운영.

설정값 의미는 `CONFIGURATION.md`, 운영 점검·장애·업그레이드는 `RUNBOOK.md`.

구성 대상 컴포넌트:

- Kubernetes namespace quota Resource Management: live Kubernetes `ResourceQuota/dms-storage-quota` create/update/block/delete/check/sync/import/audit.
- Filesystem Resource Management: CephFS / WEKA host-mounted adapter와 GPFS command adapter.
- Agent inventory: Kubernetes DaemonSet 기반 report.
- Data Management `scan/sync/rm`: DB policy/API 기반 node/process resource model, **Volcano 네이티브 Job scheduling**(Volcano가 MPI worker를 gang-schedule; Kubeflow MPI Operator 불필요), preflight 시 owner_username에 대한 read-only LDAP 신원 조회(직접) + DM denylist admission, DM Agent report freshness, POSIX preflight, writable shared artifact base를 사용한다. `sync`와 `rm`은 preview/confirm guard가 필수이고, separated-role `nsync`는 Volcano backend gate를 통과해야 한다.

## 문서 사용 방법

처음 설치한다면 아래 순서대로 읽고 실행한다.

1. **먼저 `0.prerequisites.md`로 클러스터 사전 준비를 갖춘다**(특히 DM을 쓸 경우 필수).
2. 이 `README.md`의 순서대로 설치·구성한다.
3. 설정값 의미가 헷갈리면 `CONFIGURATION.md`를 확인한다.
4. 설치 후 운영 점검, 장애 확인, 업그레이드는 `RUNBOOK.md`를 따른다.
5. 데이터 잡·리소스 요청/조회 API 사용법은 `2.dms-rm-api-fs.md` / `3.dms-rm-api-k8s.md` / `4.dms-dm-api.md`를 참고한다.

명령은 DMS repository root에서 실행한다고 가정한다.

```bash
cd <dms-repo-root>
```

실제 경로가 다르면 repository root로 이동한 뒤 실행한다.

## 설치 디렉토리 구성

```text
install/
  0.prerequisites.md                     # 클러스터 사전 준비 (가장 먼저 읽는다)
  README.md                              # 설치 절차 (이 문서)
  CONFIGURATION.md                       # 설정 변수와 API 예시
  RUNBOOK.md                             # 운영 점검과 장애 대응
  postgresql/init.sql                    # PostgreSQL DB/user 생성 템플릿
  docker/Dockerfile                      # 운영 image build 템플릿
  docker/Dockerfile.mpifileutils         # Data Management mpifileutils job image build 템플릿
  docker/Dockerfile.agent                # DM Agent image build (mpifileutils tool 포함, --build-arg MFU_IMAGE)
  config/dms-runtime.env.example         # 런타임 env 예시
  config/cluster-kubeconfigs.example.json
  config/agent-storages.example.json
  config/storage-mappings.example.json
  config/default-quota-policies.example.json
  config/identity-denylist.example.json
  kubernetes/control-plane.yaml          # API, Planner, RM/DM Worker, Secret/ConfigMap
  kubernetes/volcano-queue-priorityclasses.yaml  # DM 잡 Queue(dms-data) + PriorityClass(dms-low/normal/high)
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
| DM job scheduling | Volcano scheduler + Queue `dms-data` + PriorityClass (`0.prerequisites.md`) | Data Management prerequisite (native Volcano Job; MPI Operator 불필요) |

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

기본 Dockerfile은 mpifileutils upstream을 pinned ref로 Open MPI와 함께 빌드하고
`dscan`, `dsync`, `nsync`, `drm`, `mpirun`, `ompi_info`, OpenSSH client/server를
포함한다.

```bash
export DMS_DM_JOB_IMAGE="registry.example.internal/dms-mpifileutils:$(git rev-parse --short HEAD)"
docker build -f install/docker/Dockerfile.mpifileutils -t "$DMS_DM_JOB_IMAGE" .
docker push "$DMS_DM_JOB_IMAGE"
```

빌드한 job image는 반드시 실제 push된 ref로 `DMS_DM_JOB_IMAGE`에 넣는다(`...:CHANGE_ME`처럼
placeholder를 남기면 fail-closed되지 않고 잡 파드가 `ImagePullBackOff`로 죽는다). `DMS_DM_JOB_IMAGE_REF`는
빌드에 사용한 mpifileutils upstream ref를 남기는 **선택적 provenance 메타데이터**로, 동작에는 무관하다.

```bash
# 선택: provenance 기록 (동작에는 영향 없음)
export DMS_DM_JOB_IMAGE_REF="<mpifileutils-repo>@<pinned-commit>"
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

**DM Agent image (`dms-agent`).** DM 노드의 `dms-dm-agent`는 API/worker/rm-agent가 쓰는 기본
`dms` image가 아니라, mpifileutils 툴이 PATH에 있는 **`dms-agent` image**를 써야 한다. 에이전트는
`which`로 `dsync/dscan/drm/nsync`를 찾는데 기본 `dms` image엔 없어서, 그대로 두면 모든 DM 후보
노드가 `missing_dscan/dsync/drm_tool`로 거부되어 DM 잡이 스케줄되지 않는다. `Dockerfile.agent`는
위 **잡 image에서** `/opt/mpifileutils`를 복사하므로 **잡 image(위)를 먼저 빌드**한 뒤 그것을
`--build-arg MFU_IMAGE`로 넘긴다. (`dms-rm-agent`는 기본 `dms` image로 충분 — RM readiness는
mount + can-i이며 툴 게이트가 없다.)

```bash
export DMS_AGENT_IMAGE="registry.example.internal/dms-agent:$(git rev-parse --short HEAD)"
docker build \
  --build-arg MFU_IMAGE="$DMS_DM_JOB_IMAGE" \
  -f install/docker/Dockerfile.agent \
  -t "$DMS_AGENT_IMAGE" .
docker push "$DMS_AGENT_IMAGE"
```

### 2.5 Data Management scheduling prerequisites (Volcano)

Data Management 잡은 **Volcano 네이티브 Job**(`batch.volcano.sh`)으로 스케줄된다
(`DMS_DM_SCHEDULER_BACKEND=volcano-job`, control-plane.yaml 기본값). Volcano 하나가 single-node
및 multi-node의 MPI worker pod를 gang-schedule하므로 **Kubeflow MPI Operator는 필요 없다**.
`DMS_DM_SCHEDULER_BACKEND=auto`는 쓰지 않는다 — auto는 `MPIJob`(`kubeflow.org`)을 먼저 시도해
MPI Operator가 없으면 매 잡마다 apply가 실패한 뒤 폴백하기 때문이다.

DM 잡 클러스터(`dms-dm-worker`와 같은 클러스터)에 필요한 것 — 자세한 절차는 `0.prerequisites.md`:

- Volcano scheduler/controller/admission + CRD (`batch.volcano.sh` Job, `scheduling.volcano.sh` Queue/PodGroup)
- Queue `dms-data` + PriorityClass `dms-low/normal/high` — `install/kubernetes/volcano-queue-priorityclasses.yaml`로 적용한다(큐가 없으면 잡이 영구 Pending, PriorityClass가 없으면 파드가 admission에서 거부).
- DM 잡 네임스페이스 PodSecurity=`privileged`
- `DMS_DM_ARTIFACT_BASE_URI=file://...`용 공유 RWX artifact 경로(dm-worker와 모든 DM 잡 노드에 동일 경로)
- DM Worker가 DMS namespace에서 Volcano Job/PodGroup/Pod/Event/log를 create/read/watch/delete할 RBAC(`control-plane.yaml`의 `dms-dm-volcano` Role)

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
kubectl --context dms-control -n dms rollout status deploy/dms-dm-worker --timeout=180s
kubectl --context dms-control -n dms get pods,svc,deploy
```

`dms-dm-worker`는 기본 `replicas: 1`(DM 활성)이다. DM(`scan`/`sync`/`rm`)을 쓰지 않을 환경에서만 `0`으로 내려 비활성화한다.

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

DNS가 아직 준비되지 않았다면 operator workstation의 `/etc/hosts` 또는 curl `--resolve`를 사용한다. ingress mTLS 동작은 `/healthz`로 점검한다. client certificate을 제시하면 통과해야 한다.

```bash
curl --resolve dms.example.internal:443:INGRESS_IP \
  --cert /tmp/dms-certs/operator.crt \
  --key /tmp/dms-certs/operator.key \
  --cacert /path/to/dms-api-server-ca.crt \
  https://dms.example.internal/healthz
```

Client certificate 없이 호출하면 실패해야 한다.

```bash
curl --resolve dms.example.internal:443:INGRESS_IP \
  --cacert /path/to/dms-api-server-ca.crt \
  https://dms.example.internal/healthz
```

정상 운영 profile에서는 위 명령이 401 또는 TLS client certificate error로 실패해야 한다.

## 9. Agent DaemonSet 배포

Agent는 StorageClass, CSI, mount, tool, credential, network, identity evidence를 DMS API로 report한다. **filesystem backend(cephfs/wekafs/gpfs) mapping의 readiness**(RM/DM/INV 축)에 필요하다. k8s/CSI(namespace-quota) mapping은 node agent가 없는 managed cluster를 대상으로 할 수 있어 agent report로 readiness를 판정하지 않고 QUOTA(`ResourceQuota` mutation transport) 축으로 판정하므로(§10.3 참고) agent가 없어도 Degraded/Failed가 되지 않는다.

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
# dms-rm-agent = 기본 dms image; dms-dm-agent = dms-agent image(§2.4에서 빌드). 둘을 각각 치환한다.
sed -i "s#registry.example.internal/dms:CHANGE_ME#$DMS_IMAGE#g" /tmp/dms-agent-daemonset.yaml
sed -i "s#registry.example.internal/dms-agent:CHANGE_ME#$DMS_AGENT_IMAGE#g" /tmp/dms-agent-daemonset.yaml
```

DM 노드가 신원을 vouch할 사용자도 채운다(비우면 비특권 DM 잡이 전부 `identity_not_ready_on_node`로
거부).

```bash
sed -i "s#CHANGE_ME_user1,CHANGE_ME_user2#alice,bob#g" /tmp/dms-agent-daemonset.yaml
```

`ConfigMap/dms-agent-runtime-config`에서 다음을 수정한다.

```yaml
DMS_AGENT_API_URL: "https://dms.example.internal"
DMS_AGENT_CLUSTER_NAME: "cluster-a"
DMS_AGENT_REPORT_INTERVAL_SECONDS: "60"
DMS_AGENT_TOOLS: "dsync,nsync,drm,dscan,kubectl"
# 마운트 readiness 필수: 호스트 mount table을 읽는다(컨테이너 기본 /proc/self/mountinfo면 워커
# 노드 마운트가 안 보여 전부 Missing → readiness false). DaemonSet은 /proc/1/mountinfo를
# /host/proc/1/mountinfo로 bind-mount한다(manifest에 포함). 자세한 내용은 CONFIGURATION.md
# "마운트 readiness — 호스트 mountinfo bind-mount" 절.
DMS_AGENT_MOUNTINFO_PATH: "/host/proc/1/mountinfo"
DMS_AGENT_HOST_ROOT: "/host"
```

`Secret/dms-agent-secrets`의 token도 control plane의 `DMS_AUTH_SHARED_TOKEN`과 같아야 한다.

주의: 현재 mTLS-required mode에서 agent report를 ingress로 보내려면 agent Pod도 client certificate/key/CA를 가져야 한다. 기본 `agent-daemonset.yaml`은 bearer token Secret만 포함한다. 운영에서는 다음 중 하나가 필요하다.

- agent 전용 client certificate Secret을 만들고 DaemonSet에 mount한 뒤 agent HTTP client가 그 certificate을 사용하도록 구현/설정한다.
- 또는 운영자가 문서화한 내부 authentication boundary를 사용하고, direct spoof를 막는 네트워크 제어를 둔다.

`POST /api/v1/agent/reports`는 mTLS actor가 `node:{cluster}:{node}`와 일치해야 Fresh report로 저장된다. 따라서 agent certificate subject를 node actor로 매핑하는 운영자 측 설정이 필요하다.

### 9.3 apply 및 확인

```bash
kubectl --context cluster-a apply -f /tmp/dms-agent-daemonset.yaml
# manifest는 RM·DM DaemonSet을 모두 배포하므로 둘 다 확인한다.
kubectl --context cluster-a -n dms rollout status daemonset/dms-rm-agent daemonset/dms-dm-agent --timeout=180s
kubectl --context cluster-a -n dms get pods -l dms.io/worker-role -o wide
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
> 거부된다(암묵 기본값 없음). `managed_root`는 `mount_path` 아래의 절대경로여야 하며,
> RM 디렉토리 연산과 DM `DMS_DM_PATH_BASE=managed_root` 모드의 경계/기준점이 된다. (CSI-only/namespace-quota용
> mapping은 filesystem backend가 아니므로 managed_root가 필요 없다.)
>
> **GPFS는 추가로 `filesystem_name`(대상 GPFS 파일시스템/device 이름)을 명시 필수**다 — GPFS의
> `mm*`(fileset/quota) 명령이 이 이름을 대상으로 하기 때문이며, 생략하면 등록이 `422`로 거부된다(과거의
> `storage_name` 암묵 기본값은 제거됨). cephfs/wekafs는 filesystem_name이 필수가 아니다.
>
> **managed_root 권한은 `0711`로 둔다** — 리소스 디렉토리는 각각 `0750`/`0770`(소유자/그룹만)로 만들어지지만,
> 그 부모인 managed_root가 `0755`(기본 umask)면 누구나 `ls`로 **리소스 이름 목록**을 볼 수 있다. `0711`은
> 임의 uid 소유자의 traverse(`cd`)는 허용하되 list(`ls`)는 막는다. CephFS는 DMS 자동 생성 시 `0711`로
> 만들고(사전 생성 시 그 권한 유지), GPFS/WEKA는 운영자가 `mkdir -p {managed_root} && chmod 0711 {managed_root}`로 만든다.

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
    "managed_root": "/gpfs/gpfs0/dms",
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

등록 직후 storage mapping readiness를 확인한다(`verify-install.sh`도 같은 query를 포함한다).

```bash
curl -fsS \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN" \
  "$DMS_API_URL/api/v1/operations/storage-mappings" | jq
```

filesystem backend(cephfs/wekafs/gpfs) mapping은 `readiness.resource_management`가 `Ready`여야 Resource Management request가 진행된다. DM readiness는 Data Management 단계 전까지 `Missing` 또는 `Degraded`일 수 있다.

### 10.4 sanity/readiness 판정 축 (filesystem vs k8s/CSI)

storage mapping sanity는 backend type에 따라 **다른 readiness 축**으로 판정된다. `backend_type`은 두 부류로 나뉜다(이 부류 판정으로 어떤 축을 쓸지 결정된다).

- **filesystem backend**: `backend_type`이 `cephfs`/`wekafs`/`gpfs` 중 하나. host-mounted/command RM 경로를 쓴다.
- **k8s/CSI(namespace-quota) backend**: `backend_type`이 위 셋이 아닌 값(예: `longhorn`, `ceph-csi`, `gpfs-csi`, `weka-csi` 등 CSI StorageClass용 free-form 이름). live `ResourceQuota` 경로만 쓴다. (`backend_type`이 비어 있으면 `backend_type_missing`으로 거부된다.)

판정 축:

- **filesystem mapping** — node Agent evidence 기반. `readiness`에 `resource_management`(RM), `data_management`(DM), `inventory`(INV) 축이 채워지고, 이 셋과 Agent report fresh/stale 신호로 status가 결정된다(fresh report가 전혀 없으면 `Unknown`, warning이 있으면 `Degraded`).
- **k8s/CSI mapping** — **QUOTA 축**, 즉 `ResourceQuota` **mutation transport** 검증으로 판정한다. DMS가 RM worker가 실제로 quota를 적용하는 경로(per-mapping `mutation_mode`/`control_host`를 반영한 `kubectl`/`ssh-kubectl`)로 대상 cluster에 **도달 가능한지**와 `ResourceQuota`에 대한 **create/patch/delete 권한이 있는지**(`kubectl auth can-i`)를 확인한다. 결과는 `readiness.kubernetes_mutation`(`Ready`/`Failed`/`Unknown`)과 상세 필드 `sanity_result.mutation_observed`(`mode`, `control_host`, `reachable`, `permissions.{create,patch,delete}`, `can_mutate`, `detail`)로 노출된다.
  - 도달 가능 + 세 권한 모두 있음 → `kubernetes_mutation=Ready`, status `Ready`.
  - 도달 불가 → error `mutation_transport_unreachable`, `kubernetes_mutation=Failed`, status `Failed`.
  - 도달 가능하나 권한 없음 → error `mutation_no_permission`, `kubernetes_mutation=Failed`, status `Failed`.
  - transport probe가 wiring되지 않은 환경(관리 cluster의 `DMS_CLUSTER_CONTROL_HOSTS_JSON`/`DMS_CLUSTER_KUBECONFIGS_JSON` 미설정 등) → `kubernetes_mutation=Unknown`(검증 생략).
  - k8s/CSI mapping은 node agent가 없는 managed cluster를 대상으로 할 수 있으므로, agent report의 부재/stale로 인한 `missing_rm/dm_readiness` warning과 fresh/stale 집계 신호를 **건너뛴다**. 즉 agent가 없어도 transport가 정상이면 `Ready`다.

planner guard는 이 판정을 사용한다. filesystem RM/DM request는 sanity가 `Failed` 또는 `Unknown`이면 차단되지만, **k8s namespace-quota request는 `Failed`만 차단**한다(`Unknown`/`Degraded`는 통과). 따라서 transport 검증 실패가 sanity `Failed`로 표면화되면 해당 quota request도 자동으로 막힌다.

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

DM은 requester와 POSIX user를 사전 등록/sync하지 않는다. preflight 시점에 dm-worker에서 owner_username에 대한 read-only LDAP 조회(search-only)로 POSIX 신원을 직접 resolve한다. owner_username은 requester_id(자유 형식 logical id)를 기본값으로 하고, 실제 POSIX username으로 override할 수 있다(RM owner model과 동일).

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

denylist는 DM 측에 유일하게 persist되는 신원 상태다. requester/owner/group(`subject_type`은 이 셋 중 하나) 단위의 즉시 kill-switch이자 admission block이며, 기본값은 비어 있어 모두 allow한다. 등록은 선택이고, 평소에는 비워 둔다. denylist에 올라간 주체의 요청은 preflight에서 `identity_denied`로 중단된다.

초기 차단 목록은 예시 파일과 bulk-seed script로 한 번에 seed한다.

```bash
cp install/config/identity-denylist.example.json /tmp/dms-identity-denylist.json
install/scripts/apply-identity-denylist.sh /tmp/dms-identity-denylist.json
```

개별 주체의 차단 추가/해제/조회(`identity-denylist` API) 사용법은 `4.dms-dm-api.md`를 참고한다.

### 12.3 운영자 root 실행 (privileged requester, 선택)

운영자가 임의 사용자 데이터를 이관·정리하기 위해 root(uid 0)로 Data Management를 실행해야 하는 경우를 위한 **기본 비활성** 기능이다. 켜면 `requester_id`(또는 `owner_username`)가 `DMS_DM_PRIVILEGED_REQUESTERS`(기본 `root`)에 속한 요청을 uid/gid 0으로 합성 실행한다(LDAP 조회·uid floor 우회, job pod `runAsUser:0`).

```yaml
DMS_DM_ALLOW_ROOT_REQUESTER: "true"     # 기본 false
DMS_DM_PRIVILEGED_REQUESTERS: "root"
DMS_DM_PRIVILEGED_SCOPES: ""            # 비우면 전체 storage. "storage" 또는 "storage:prefix"로 제한
DMS_DM_PRIVILEGED_OPERATORS: ""         # 비우면 mTLS-verified operator 전체 허용
```

권한 부여는 API edge에서 인증된 operator에 묶인다. **반드시 `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true` 경로에서만 사용**한다 — `requester_id`는 클라이언트가 채우는 필드라, 평문 `x-dms-actor` 채널로 root가 닿으면 권한상승 구멍이 된다. root 요청도 mTLS-verified operator가 아니면 `403`, denylist 등재 시 `identity_denied`로 거부되며, preview/confirm 게이트도 우회되지 않는다. 상세는 `4.dms-dm-api.md` §10.4를 참고한다.

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

전제조건:

- 대상 storage mapping(k8s/CSI namespace-quota mapping)의 sanity `status=Ready`. 이 mapping은 QUOTA 축으로 판정되므로 `readiness.kubernetes_mutation=Ready`(transport probe가 wiring된 경우) 또는 최소한 `Failed`가 아님을 §10.3/§10.4로 확인한다(planner는 quota request를 sanity `Failed`일 때만 차단).
- 운영에 영향 없는 namespace 이름 사용 (예: `dms-smoke-quota`)

이 경로는 CephFS, Longhorn, GPFS CSI, WEKA CSI 같은 모든 CSI StorageClass backend에서
backend type과 무관하게 같은 Kubernetes `ResourceQuota/dms-storage-quota` live adapter를
사용한다(GPFS namespace quota에서도 IBM Storage Scale `mm*` command는 실행되지 않는다).

namespace quota create/조회/delete API 사용법은 `3.dms-rm-api-k8s.md`(operations 조회 포함)를
참고한다. create 후 target cluster에서 직접 확인하려면:

```bash
kubectl --context cluster-a -n dms-smoke-quota get resourcequota dms-storage-quota -o yaml
```

DMS delete는 `ResourceQuota/dms-storage-quota`만 삭제하고 namespace 자체는 남긴다. 필요하면 운영자가 namespace를 별도로 삭제한다.

```bash
kubectl --context cluster-a delete namespace dms-smoke-quota
```

### 13.3 Data Management smoke test

Data Management live execution을 열기 전에 다음 조건을 먼저 확인한다.

- target/source/destination storage mapping의 `readiness.data_management=Ready`
- requester의 `owner_username`이 dm-worker preflight의 read-only LDAP 조회로 POSIX 신원으로 해석됨 (§12) + DM denylist에 차단 항목 없음
- DM Agent report에 mount, required tool(`dscan`, `dsync`, `drm`), credential, network, POSIX user evidence가 Fresh
- `DMS_DM_JOB_IMAGE`가 실제 push된 job image ref로 설정됨(`...:CHANGE_ME` placeholder면 fail-closed 없이 `ImagePullBackOff`) + `DMS_DM_ARTIFACT_BASE_URI` 설정. `DMS_DM_JOB_IMAGE_REF`는 선택(provenance 메타데이터)
- (선택) `DMS_DM_PATH_BASE` — 요청 path 기준점. 기본 `mount_path`, `managed_root`면 planner가 storage별 `managed_root` suffix를 prepend(filesystem mapping에 `managed_root` 명시 필수). 켜면 요청 path를 managed_root 기준으로 적는다
- Volcano scheduler/CRD가 DM 잡 클러스터에서 동작 중이고 Queue `dms-data`·PriorityClass(`dms-low/normal/high`)가 존재(`0.prerequisites.md`; multi-node 잡도 Volcano가 gang-schedule하므로 MPI Operator 불필요)

`dms-dm-worker`는 기본 replicas 1로 이미 떠 있어야 한다(DM을 끄려고 0으로 내렸다면 다시 1로 올린다).

```bash
kubectl --context dms-control -n dms scale deploy/dms-dm-worker --replicas=1
kubectl --context dms-control -n dms rollout status deploy/dms-dm-worker --timeout=180s
```

`scan`/`sync`/`rm` 제출, operations 조회, preview/confirm API 사용법은 `4.dms-dm-api.md`를
참고한다. smoke는 항상 작은 테스트 directory에서 수행하고, `sync`/`rm`은 preview와 confirm을
분리한다(`rm`의 `target`은 storage root가 아닌 작은 directory여야 하며 `options.recursive=true`를
명시).

확인할 동작:

- artifact URI(`result_summary.report_uri`, `stdout_uri`, `stderr_uri`, `summary_uri`)가
  `DMS_DM_ARTIFACT_BASE_URI/<job_id>/...` 형태로 기록된다.
- Single-node live Data Management는 resource fan-out 없이 실행된다. `scan`, same-node `sync`,
  `rm`은 각각 1 selected node, 1 worker pod, 1 process이며 `result_summary.selected_node`,
  `worker_pod_count`, `process_count`로 확인한다. separated-role `nsync`가 필요한 topology는
  mutation 없이 `data_job_nsync_deferred` action-required로 남아야 정상이다.
- Multi-node Data Management(Volcano가 MPI worker pod를 gang-schedule)는 DMS가 ready mounted
  node set을 eligible set으로 제출하고 Volcano/Kubernetes scheduler가 그중 실제 feasible nodes를
  선택한다. 모든 job은 submitted CR YAML과 MPI metadata artifact를 남긴다.

### 13.4 Filesystem smoke test

전제조건:

- 대상 storage mapping의 `readiness.resource_management=Ready` (§10.3)
- 작은 테스트 directory(예: `dms-smoke-fs`)로 시작

filesystem create/조회/delete API 사용법은 `2.dms-rm-api-fs.md`(operations 조회 포함)를 참고한다.
create 성공 후 backend host에서 directory, ownership, quota xattr 또는 GPFS fileset/quota를 직접 확인한다. CephFS 예시:

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
- `deploy/dms-dm-worker` Ready 1 (DM 기본 활성); DM(`scan`/`sync`/`rm`)을 끈 환경에서만 replicas 0
- `svc/dms-api` 존재
- `ingress/dms-api` 존재

DMS API: `verify-install.sh`(§13.1)가 통과하고, action-required query가 비어 있으면 정상 steady state다. operations 조회 API 사용법은 `4.dms-dm-api.md`를 참고한다.

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

recovery check가 통과하면 control state를 normal로 되돌리고 worker를 다시 scale up한다. DM이 기본 활성(replicas 1)이므로 두 worker를 모두 되돌린다.

```bash
export DMS_WORKER_DEPLOYMENTS="dms-rm-worker dms-dm-worker"
install/scripts/dms-resume.sh \
  --reason "source update completed $(date -Iseconds)" \
  --replicas 1
```

recovery blocker가 남아 있으면 기본적으로 API가 409를 반환한다. 운영자가 live state를 확인했고 강제 resume이 필요하면 `--force`를 사용한다.

```bash
export DMS_WORKER_DEPLOYMENTS="dms-rm-worker dms-dm-worker"
install/scripts/dms-resume.sh \
  --reason "operator accepted recovery items $(date -Iseconds)" \
  --force \
  --replicas 1
```

DM(`scan`/`sync`/`rm`)을 **의도적으로 비활성화**한 환경에서만 `dms-dm-worker`를 resume 대상에서 빼거나(위 `DMS_WORKER_DEPLOYMENTS`에서 제외) 0으로 내린다.

```bash
kubectl --context dms-control -n dms scale deploy/dms-dm-worker --replicas=0
```

### 15.4 수동 API 확인

script를 쓰지 않고 직접 확인하려면 `control-state`, `work-summary`, `runs/active` 등 operations 조회 endpoint를 사용한다. API 사용법은 `4.dms-dm-api.md`를 참고한다.

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

`/healthz`로 TLS 핸드셰이크와 client certificate 검증을 확인한다.

```bash
curl -v \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  "$DMS_API_URL/healthz"
```

흔한 원인:

- client certificate이 `dms-client-ca`로 검증되지 않음
- ingress annotation 누락
- bearer token 불일치
- `DMS_CA_CERT`가 server certificate CA가 아님
- DMS API Service에 직접 접근해서 trusted mTLS evidence header가 없음

### Storage mapping이 `Failed` 또는 `Degraded`

storage-mappings 조회 API(§10.3, `operations/storage-mappings`)로 readiness 상세를 확인한다. readiness 판정 축은 backend type에 따라 다르다(§10.4).

공통 원인:

- `storage_class_name`이 target cluster에 없음
- `csi_driver`가 StorageClass provisioner와 다름
- `backend_template.backend_type` 누락(`backend_type_missing`)

filesystem mapping(cephfs/wekafs/gpfs, RM/DM/INV 축):

- Agent report가 Fresh가 아님
- filesystem mount path를 agent가 볼 수 없음

k8s/CSI(namespace-quota) mapping(QUOTA 축 = `ResourceQuota` mutation transport, `readiness.kubernetes_mutation`/`sanity_result.mutation_observed`로 확인):

- `mutation_transport_unreachable`: mutation 경로(`mutation_mode`/`control_host`에 따른 `kubectl`/`ssh-kubectl`)로 대상 cluster에 도달 불가 — kubeconfig 경로, `DMS_CLUSTER_CONTROL_HOSTS_JSON`의 control host, SSH 도달성을 확인한다.
- `mutation_no_permission`: 대상 namespace에서 `ResourceQuota` create/patch/delete 권한 없음(`kubectl auth can-i = no`) — target cluster RBAC(`target-cluster-rbac.yaml`)를 확인한다.
- 참고: k8s/CSI mapping은 agent 부재/stale로는 Failed/Degraded가 되지 않는다(QUOTA 축으로만 판정).

### RM Worker가 backend mutation 실패

worker log를 확인하고, action-required 조회 API(`4.dms-dm-api.md`)로 실패 항목을 본다.

```bash
kubectl --context dms-control -n dms logs deploy/dms-rm-worker --tail=200
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
- `dms-dm-worker`는 기본 replicas 1로 DM을 활성화한다. DM을 열기 전 §13.3 전제조건(`0.prerequisites.md`의 Volcano/Queue/PriorityClass·공유 artifact FS·DM Agent Fresh·LDAP 신원)을 확인하고, DM을 쓰지 않을 때만 0으로 내려 비활성화한다.
- `DMS_DM_ALLOW_ROOT_REQUESTER`(운영자 root 실행, §12.3)는 기본 비활성이며, 켤 경우 반드시 `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true` 경로로만 노출한다(평문 actor 채널 금지). 필요 시 `DMS_DM_PRIVILEGED_SCOPES`로 storage/경로를 제한한다.
- `UnknownAfterSideEffect`, `BackendApplyFailed`, action-required 항목은 운영 사고로 취급한다.
- 업그레이드 전 operational DB와 observability DB를 모두 백업한다.

## 18. 다음 문서

- 설정 변수와 API 예시는 `install/CONFIGURATION.md`.
- 일일 점검, 장애 대응, 업그레이드는 `install/RUNBOOK.md`.
- Backend 추가 방법은 `docs/backend-extension-guide.md`.
