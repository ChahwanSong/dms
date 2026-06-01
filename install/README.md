# DMS Production Installation Guide

이 디렉토리는 실제 Kubernetes cluster에 DMS를 설치하고 운영하기 위한 문서, 설정 예시, Kubernetes manifest, helper script를 모은다. 기존 `deploy/` 디렉토리는 phase/testbed 검증에 사용된 manifest가 섞여 있으므로 운영 설치 기준은 이 `install/` 디렉토리를 우선한다.

현재 구현 기준으로 production에서 열어도 되는 범위와 아직 열면 안 되는 범위가 다르다.

- Kubernetes namespace quota Resource Management: live Kubernetes `ResourceQuota/dms-storage-quota` create/update/block/delete/check/sync/import/audit 가능.
- Filesystem Resource Management: CephFS host-mounted adapter와 GPFS command adapter 가능. GPFS live 검증은 별도 staging 필요.
- Agent inventory: Kubernetes DaemonSet 기반 report 가능.
- Data Management `scan/sync/rm`: 현재 CLI `dm-worker`는 `StubVolcanoAdapter`를 사용하므로 production에서 실행하지 않는다. API는 배포되더라도 DM worker replica는 0으로 둔다.

## Install Directory Layout

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

## Installation Order

1. Decide topology.
   - Control cluster: runs DMS API, Planner, central RM Worker, optional Agent, and PostgreSQL access.
   - Target clusters: storage/GPU clusters where DMS audits Kubernetes StorageClasses and mutates namespace `ResourceQuota`.
   - Filesystem RM target nodes: worker/admin nodes that can access CephFS/GPFS backend primitives.

2. Prepare PostgreSQL.
   - Create operational DB and observability DB.
   - Apply least-privilege DB users using `postgresql/init.sql` as a starting point.
   - Keep operational and observability DBs separate for production.

3. Build and publish the DMS image.
   - The image must include the package with extras needed by your mode: `postgres`, `ldap`, and optionally `kubernetes`.
   - For `DMS_KUBERNETES_*_MODE=kubectl`, the image must include `kubectl`.
   - For `DMS_KUBERNETES_*_MODE=ssh-kubectl`, the image must include `ssh`, and target control hosts must have `kubectl`.
   - `docker/Dockerfile` is a production-oriented image template with `kubectl`, `ssh`, `postgres`, `ldap`, and `kubernetes` dependencies.
   - Example build command: `docker build -f install/docker/Dockerfile -t registry.example.internal/dms:$(git rev-parse --short HEAD) .`

4. Create target-cluster credentials.
   - Apply `kubernetes/target-cluster-rbac.yaml` to each target cluster.
   - Use `scripts/create-serviceaccount-kubeconfig.sh` to generate a kubeconfig per cluster.
   - Store generated kubeconfigs as the `dms-cluster-kubeconfigs` Secret in the control cluster.

5. Create control-cluster secrets.
   - `dms-secrets`: DB URLs, API shared token, LDAP bind secret.
   - `dms-cluster-kubeconfigs`: kubeconfig files for all target clusters.
   - `dms-ssh-client`: SSH key, `known_hosts`, and SSH config for CephFS/GPFS `ssh-host-exec`.

6. Deploy DMS control plane.
   - Edit `kubernetes/control-plane.yaml`.
   - Set image, cluster names, DB URLs, shared token, LDAP settings, kubeconfig JSON, and resource requests.
   - If exposing the API through ingress, copy and edit `kubernetes/ingress.example.yaml`.
   - Apply the manifest.
   - Wait for `dms-migrate` Job, `dms-api`, `dms-planner`, and `dms-rm-worker`.

7. Deploy Agents.
   - Edit `kubernetes/agent-daemonset.yaml` per cluster.
   - Set `DMS_AGENT_CLUSTER_NAME`, `DMS_AGENT_API_URL`, shared token, storage list, tool list, and node selectors/tolerations.
   - Apply to control and target clusters as needed.

8. Register storage mappings.
   - Edit `config/storage-mappings.example.json` into your real storage mapping file.
   - Run `scripts/register-storage-mappings.sh`.
   - Re-run mapping checks until `readiness.resource_management` and, if needed, `readiness.data_management` are `Ready`.

9. Register default quota policies and identity mappings.
   - Use `config/default-quota-policies.example.json`.
   - Configure LDAP settings first if using identity mapping endpoints.
   - Use `scripts/register-identity-mappings.sh` after LDAP bind settings are verified.

10. Run verification.
    - Run `scripts/verify-install.sh`.
    - Create one non-production namespace quota target and run create/check/delete.
    - Create one non-production filesystem target if enabling filesystem RM.

## Topology Decisions

### Recommended Kubernetes Mode

For production, prefer `kubectl` mode with mounted kubeconfigs unless your security model requires SSH to control hosts.

```text
DMS_KUBERNETES_INVENTORY_MODE=kubectl
DMS_KUBERNETES_MUTATION_MODE=kubectl
DMS_CLUSTER_KUBECONFIGS_JSON={"cluster-a":"/etc/dms/kubeconfigs/cluster-a.kubeconfig","cluster-b":"/etc/dms/kubeconfigs/cluster-b.kubeconfig"}
```

This mode keeps target cluster access explicit and auditable through Kubernetes service accounts.

### Filesystem RM Mode

Use one of these patterns.

- Central RM Worker with `ssh-host-exec`: safe for multiple storage systems when each storage mapping names the right `ssh_host` or `rm_worker_nodes`. The central worker SSHes to the backend admin/RM node.
- Single RM Worker with all mounts and `local`: safe only if one worker pod can see every managed filesystem at the exact `mount_path`.
- Multiple local RM Workers with different mounts: not recommended in the current implementation because RM worker claim does not yet pin a plan to a storage-specific worker.

### Data Management

Do not scale `dms-dm-worker` above 0 in production until live Volcano/mpifileutils execution is implemented and verified. Leaving API routes available is acceptable if operators understand that data jobs will stay planned or blocked without a worker.

## External Requirements

- Kubernetes clusters reachable from the DMS control cluster.
- PostgreSQL 14+ or compatible managed PostgreSQL.
- Container registry reachable by all clusters.
- `kubectl` access or SSH access to target control hosts.
- LDAP/SSSD or equivalent POSIX identity source if filesystem RM creates groups or validates users.
- Storage backend admin capability:
  - CephFS: host-mounted path and `setfattr`/`getfattr` support on RM execution host.
  - GPFS: IBM Storage Scale commands available on command runner and fileset quota enabled.
  - WEKA: not implemented yet; do not put WEKA entries in active storage mappings until a `backend_type=weka` adapter is implemented.

## Production Safety Notes

- Do not use the example passwords or tokens.
- Do not expose DMS API without ingress authentication. The built-in shared token is a minimum bootstrap control, not a complete production auth system.
- Ensure target cluster kubeconfigs are scoped to the RBAC in `target-cluster-rbac.yaml`.
- Start with one storage mapping and one non-production namespace.
- Keep `dms-dm-worker` at 0 replicas.
- Treat `UnknownAfterSideEffect`, `BackendApplyFailed`, and action-required entries as operational incidents.
- Back up both PostgreSQL databases before upgrades.
