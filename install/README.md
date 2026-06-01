# DMS 운영 설치 가이드

이 디렉토리는 실제 Kubernetes 클러스터에 DMS를 설치하고 운영하기 위한 문서, 설정 예시, Kubernetes manifest, helper script를 모은다. 기존 `deploy/` 디렉토리는 phase/testbed 검증에 사용된 manifest가 섞여 있으므로 운영 설치 기준은 이 `install/` 디렉토리를 우선한다.

현재 구현 기준으로 운영 환경에서 열어도 되는 범위와 아직 열면 안 되는 범위가 다르다.

- Kubernetes namespace quota Resource Management: live Kubernetes `ResourceQuota/dms-storage-quota` create/update/block/delete/check/sync/import/audit 가능.
- Filesystem Resource Management: CephFS host-mounted adapter와 GPFS command adapter 가능. GPFS live 검증은 별도 staging 필요.
- Agent inventory: Kubernetes DaemonSet 기반 report 가능.
- Data Management `scan/sync/rm`: 현재 CLI `dm-worker`는 `StubVolcanoAdapter`를 사용하므로 production에서 실행하지 않는다. API는 배포되더라도 DM worker replica는 0으로 둔다.

## 설치 디렉토리 구성

```text
install/
  README.md
  CONFIGURATION.md
  RUNBOOK.md
  postgresql/
    init.sql
  docker/
    Dockerfile
  config/
    dms-runtime.env.example
    cluster-kubeconfigs.example.json
    agent-storages.example.json
    storage-mappings.example.json
    default-quota-policies.example.json
    identity-mappings.example.json
  kubernetes/
    control-plane.yaml
    agent-daemonset.yaml
    target-cluster-rbac.yaml
    managed-rm-worker.yaml
    ingress.example.yaml
  scripts/
    create-serviceaccount-kubeconfig.sh
    register-storage-mappings.sh
    register-default-quota-policies.sh
    register-identity-mappings.sh
    verify-install.sh
```

## 설치 순서

1. 토폴로지를 결정한다.
   - Control cluster: DMS API, Planner, 중앙 RM Worker, 선택적 Agent를 실행하고 PostgreSQL에 접근한다.
   - Target cluster: DMS가 Kubernetes StorageClass를 audit하고 namespace `ResourceQuota`를 변경하는 storage/GPU cluster다.
   - Filesystem RM target node: CephFS/GPFS backend primitive에 접근할 수 있는 worker/admin node다.

2. PostgreSQL을 준비한다.
   - Operational DB와 observability DB를 만든다.
   - `postgresql/init.sql`을 시작점으로 사용해 최소 권한 DB user를 적용한다.
   - Production에서는 operational DB와 observability DB를 분리해서 유지한다.

3. DMS image를 build하고 publish한다.
   - Image는 사용하는 mode에 필요한 package extras를 포함해야 한다: `postgres`, `ldap`, 선택적으로 `kubernetes`.
   - `DMS_KUBERNETES_*_MODE=kubectl`이면 image에 `kubectl`이 있어야 한다.
   - `DMS_KUBERNETES_*_MODE=ssh-kubectl`이면 image에 `ssh`가 있어야 하고 target control host에는 `kubectl`이 있어야 한다.
   - `docker/Dockerfile`은 `kubectl`, `ssh`, `postgres`, `ldap`, `kubernetes` dependency를 포함한 production 지향 image template이다.
   - 예시 build 명령: `docker build -f install/docker/Dockerfile -t registry.example.internal/dms:$(git rev-parse --short HEAD) .`

4. Target cluster 인증 정보를 만든다.
   - 각 target cluster에 `kubernetes/target-cluster-rbac.yaml`을 적용한다.
   - Cluster별 kubeconfig를 만들기 위해 `scripts/create-serviceaccount-kubeconfig.sh`를 사용한다.
   - 생성한 kubeconfig는 control cluster의 `dms-cluster-kubeconfigs` Secret에 저장한다.

5. Control cluster Secret을 만든다.
   - `dms-secrets`: DB URL, API shared token, LDAP bind secret.
   - `dms-cluster-kubeconfigs`: 모든 target cluster의 kubeconfig file.
   - `dms-ssh-client`: CephFS/GPFS `ssh-host-exec`용 SSH key, `known_hosts`, SSH config.

6. DMS control plane을 배포한다.
   - `kubernetes/control-plane.yaml`을 편집한다.
   - Image, cluster name, DB URL, shared token, LDAP setting, kubeconfig JSON, resource request를 설정한다.
   - API를 ingress로 노출한다면 `kubernetes/ingress.example.yaml`을 복사해서 편집한다.
   - Manifest를 적용한다.
   - `dms-migrate` Job, `dms-api`, `dms-planner`, `dms-rm-worker`가 준비될 때까지 기다린다.

7. Agent를 배포한다.
   - Cluster별로 `kubernetes/agent-daemonset.yaml`을 편집한다.
   - `DMS_AGENT_CLUSTER_NAME`, `DMS_AGENT_API_URL`, shared token, storage list, tool list, node selector/toleration을 설정한다.
   - 필요에 따라 control cluster와 target cluster에 적용한다.

8. Storage mapping을 등록한다.
   - `config/storage-mappings.example.json`을 실제 storage mapping 파일로 편집한다.
   - `scripts/register-storage-mappings.sh`를 실행한다.
   - `readiness.resource_management`와 필요 시 `readiness.data_management`가 `Ready`가 될 때까지 mapping check를 반복한다.

9. 기본 quota policy와 identity mapping을 등록한다.
   - `config/default-quota-policies.example.json`을 사용한다.
   - Identity mapping endpoint를 사용할 경우 LDAP setting을 먼저 설정한다.
   - LDAP bind setting이 검증된 뒤 `scripts/register-identity-mappings.sh`를 사용한다.

10. 검증을 실행한다.
    - `scripts/verify-install.sh`를 실행한다.
    - Non-production namespace quota target 하나를 만들고 create/check/delete를 실행한다.
    - Filesystem RM을 활성화한다면 non-production filesystem target 하나도 만든다.

## 토폴로지 결정

### 권장 Kubernetes 모드

운영 환경에서는 보안 모델상 SSH control host가 필요한 경우가 아니라면 mounted kubeconfig를 사용하는 `kubectl` 모드를 권장한다.

```text
DMS_KUBERNETES_INVENTORY_MODE=kubectl
DMS_KUBERNETES_MUTATION_MODE=kubectl
DMS_CLUSTER_KUBECONFIGS_JSON={"cluster-a":"/etc/dms/kubeconfigs/cluster-a.kubeconfig","cluster-b":"/etc/dms/kubeconfigs/cluster-b.kubeconfig"}
```

이 모드는 target cluster 접근을 Kubernetes service account를 통해 명시적이고 audit 가능하게 유지한다.

### Filesystem RM 모드

아래 pattern 중 하나를 사용한다.

- `ssh-host-exec`를 사용하는 중앙 RM Worker: 각 storage mapping이 올바른 `ssh_host` 또는 `rm_worker_nodes`를 지정하면 여러 storage system에 안전하게 사용할 수 있다. 중앙 worker가 backend admin/RM node로 SSH 접속한다.
- 모든 mount를 가진 단일 `local` RM Worker: 하나의 worker pod가 모든 managed filesystem을 정확한 `mount_path`에서 볼 수 있을 때만 안전하다.
- 서로 다른 mount를 가진 여러 local RM Worker: 현재 구현에서는 RM worker claim이 plan을 storage-specific worker에 고정하지 않으므로 권장하지 않는다.

### Data Management

Live Volcano/mpifileutils execution이 구현되고 검증될 때까지 운영 환경에서 `dms-dm-worker`를 0보다 크게 scale하지 않는다. 운영자가 worker 없이 data job이 planned 또는 blocked 상태로 남을 수 있음을 이해한다면 API route를 열어 두는 것은 가능하다.

## 외부 요구사항

- DMS control cluster에서 접근 가능한 Kubernetes cluster.
- PostgreSQL 14+ 또는 호환되는 managed PostgreSQL.
- 모든 cluster에서 접근 가능한 container registry.
- Target control host에 대한 `kubectl` 접근 또는 SSH 접근.
- Filesystem RM이 group을 만들거나 user를 검증한다면 LDAP/SSSD 또는 동등한 POSIX identity source.
- Storage backend admin capability:
  - CephFS: RM execution host에서 host-mounted path와 `setfattr`/`getfattr` 지원.
  - GPFS: Command runner에서 IBM Storage Scale command 사용 가능, fileset quota 활성화.
  - WEKA: 아직 구현되지 않았다. `backend_type=weka` adapter가 구현되기 전까지 active storage mapping에 WEKA entry를 넣지 않는다.

## 운영 안전 주의사항

- 예시 password나 token을 사용하지 않는다.
- Ingress authentication 없이 DMS API를 노출하지 않는다. Built-in shared token은 최소 bootstrap control이지 완전한 운영 인증 체계가 아니다.
- Target cluster kubeconfig가 `target-cluster-rbac.yaml`의 RBAC 범위로 제한되어 있는지 확인한다.
- 하나의 storage mapping과 하나의 non-production namespace부터 시작한다.
- `dms-dm-worker`는 0 replica로 유지한다.
- `UnknownAfterSideEffect`, `BackendApplyFailed`, action-required 항목은 운영 사고로 취급한다.
- 업그레이드 전 두 PostgreSQL database를 모두 백업한다.
