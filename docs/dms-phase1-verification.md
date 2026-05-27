# DMS Phase 1 Verification

Date: 2026-05-27

## Local Contract Tests

Command:

```bash
cd /home/mason/workspace/dms
pytest -q
```

Result:

```text
9 passed in 4.87s
```

Covered contracts:

- authentication rejection writes only an observability diagnostic event
- authorization rejection writes operational request/result and creates no plan/run
- request and plan persistence happen before backend adapter side effects
- planner blocks same-resource ordering conflicts before worker execution
- expired worker lease becomes `StaleClaim`
- operational DB and observability DB can be separated
- filesystem resource key and storage mapping uniqueness constraints exist
- Data Management `ConfirmPending` stays in `data_jobs`, not common lifecycle state
- Data Management rejects raw command-line option strings and unsafe paths
- GPFS backend skeleton routes filesystem quota work through GPFS filesystem
  adapter
- GPFS backend skeleton routes Kubernetes namespace quota work through GPFS CSI
  mapping metadata
- GPFS Data Management planning records GPFS mount/tool/identity worker-pool
  requirements

## Testbed Metadata

Checked before testbed validation:

- `/home/mason/workspace/testbed/testbed-info.json`
- `/home/mason/workspace/testbed/testbed-summary.json`
- `/home/mason/workspace/testbed/TOPOLOGY.md`
- `/home/mason/workspace/testbed/PostgreSQL.md`

Relevant facts at Phase 1 verification time:

- five Vagrant VMs are running
- `cluster-a` and `cluster-b` each have two Ready Kubernetes nodes
- Volcano system pods are Running/Completed on `cluster-a`
- base testbed did not deploy PostgreSQL by default at initial Phase 1
  verification time

Lightweight checks run:

```bash
cd /home/mason/workspace/testbed
vagrant status --machine-readable
ssh -o BatchMode=yes c1-control 'kubectl get nodes --no-headers'
ssh -o BatchMode=yes c2-control 'kubectl get nodes --no-headers'
ssh -o BatchMode=yes c1-control 'kubectl get pods -n volcano-system --no-headers'
ssh -o BatchMode=yes c1-control 'kubectl apply --dry-run=client -f -' < deploy/kubernetes/dms-cluster.yaml
ssh -o BatchMode=yes c2-control 'kubectl apply --dry-run=client -f -' < deploy/kubernetes/managed-cluster-rm-worker.yaml
```

Result summary:

- `ldap`, `c1-control`, `c1-worker`, `c2-control`, `c2-worker` are running
- `c1-control`, `c1-worker`, `c2-control`, and `c2-worker` are Ready
- `volcano-admission`, `volcano-controllers`, and `volcano-scheduler` are Running
- DMS cluster and managed-cluster Kubernetes manifests pass client-side dry-run
  validation on the testbed control nodes

The current testbed metadata does not list IBM GPFS/Spectrum Scale or IBM
Spectrum Scale CSI. GPFS backend validation is therefore limited to skeleton
contract tests and generic Kubernetes manifest dry-run checks. Actual GPFS
quota, fileset, mount, CSI, and Data Management path validation remains pending
until a GPFS-capable testbed or staging cluster is available.

PostgreSQL-backed live deployment was not run during the initial Phase 1
verification because PostgreSQL was not deployed in the base testbed then.
This gap was closed in Phase 2. See `docs/dms-phase2-verification.md` for the
live PostgreSQL migration, lifecycle, DB separation, and LDAP identity mapping
evidence.

## Extension Points

- replace `StubFilesystemBackendAdapter` with GPFS/Lustre/XFS/Ceph/Weka
  filesystem strategies
- replace `StubKubernetesNamespaceQuotaAdapter` with Kubernetes API calls
- replace `StubVolcanoAdapter` with VolcanoJob create/watch/terminate logic
- replace `AuthVerifier`, `AuthorizationPolicy`, and `StubIdentityLookupAdapter`
  with site mTLS/token/policy/LDAP integrations
- expand `StorageInventoryAdapter` to merge Kubernetes API inventory with DMS
  Agent reports
