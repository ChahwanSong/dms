# DMS Phase 9 Verification

Date: 2026-05-29 11:54 +0900

Phase 9 verifies operational hardening for Kubernetes namespace storage quota Resource Management:

- default quota policy reset
- on-demand quota audit API
- drift, usage pressure, and effective quota findings in action-required
- DMS-managed `ResourceQuota` ownership metadata hardening

Phase 9 does not add an automatic quota sweep. Drift and usage pressure checks are executed only when the operator or portal calls the audit API.

## Local Regression

Command:

```bash
cd /home/mason/workspace/dms
python3 -m pytest -q tests/test_phase9_kubernetes_quota_hardening.py
python3 -m pytest -q
chmod +x scripts/verify-phase9-testbed.sh
python3 -m py_compile scripts/phase9_kubernetes_quota_operational_hardening.py
```

Output:

```text
5 passed in 3.12s
54 passed in 37.56s
```

## Testbed Live Verification

Command:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase9-testbed.sh
```

The first image push hit the testbed HTTP registry from a Docker client expecting HTTPS. The verifier fell back to `docker save` plus `skopeo copy --dest-tls-verify=false` on `c1-control`, then the final run reused the pushed image:

```bash
DMS_PHASE9_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase9-testbed.sh
```

Deployment evidence:

```text
deployment "dms-api" successfully rolled out
daemon set "dms-rm-agent" successfully rolled out
daemon set "dms-dm-agent" successfully rolled out
daemon set "dms-rm-agent" successfully rolled out
```

Pod evidence:

```text
cluster-a:
dms-api-7475f6fc69-nlsgv   1/1 Running c1-control
dms-dm-agent-ghzjn         1/1 Running c1-worker
dms-dm-agent-h8tn4         1/1 Running c1-control
dms-rm-agent-5ldbq         1/1 Running c1-control
dms-rm-agent-gtjhr         1/1 Running c1-worker

cluster-b:
dms-rm-agent-5zp7x         1/1 Running c2-control
dms-rm-agent-tkd4f         1/1 Running c2-worker
```

Live verification summary:

```json
{
  "status": "ok",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase9_20260529115152",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase9_obs_20260529115152",
  "phase8_reports": {
    "cluster-a:DM": {
      "node_name": "c1-worker",
      "report_id": "agent_dc8d5b05c56b4044a35b0b717d533268"
    },
    "cluster-a:RM": {
      "node_name": "c1-control",
      "report_id": "agent_2bab07d2b82c4b43a15f8476f63b58ab"
    },
    "cluster-b:RM": {
      "node_name": "c2-worker",
      "report_id": "agent_e55b355e42124f298edfc5faa125ac7c"
    }
  },
  "targets": [
    {
      "target": "longhorn-multi",
      "namespace": "dms-phase9-longhorn-e2326416",
      "create_request_id": "req_a3ff1a07d5184f2d90a1ae4f748f5a6b",
      "reset_request_id": "req_8c25437a0737469082d604c83edc2aad",
      "default_policy_id": "kubernetes_namespace_quota:user",
      "audit_request_ids": [
        "req_c08bde6af3d1477eb2756713e99ca14b",
        "req_c0a9b19c99c3407e9596c8e6adef1ded",
        "req_c9ea90ff189540499889651ac6e8c3fb"
      ]
    },
    {
      "target": "cephfs",
      "namespace": "dms-phase9-cephfs-e2326416",
      "create_request_id": "req_17b6e2de4b0145298cf92db720db6a77",
      "reset_request_id": "req_a6ff77d2042942248940528e9444a28c",
      "default_policy_id": "kubernetes_namespace_quota:ceph-user",
      "audit_request_id": "req_6d909a6192d14e76a0189d747966f837"
    }
  ]
}
```

## Verified Behavior

- The verifier used real DMS Agent DaemonSet reports from Kubernetes Pods; it did not submit synthetic Agent reports.
- `cluster-b/testbed-longhorn` and `cluster-b/longhorn-static` were verified as a multi-StorageClass namespace quota target.
- `cluster-a/testbed-cephfs` was verified as a single-StorageClass namespace quota target.
- Default quota policies were stored and applied by `reset_quota_to_default=true`.
- Blocked reset kept live hard limits at zero and updated the restore target for unblock.
- On-demand `namespace-quotas:audit` detected DB/live drift and surfaced it through action-required.
- A subsequent repair plus clean audit removed the resolved drift issue from action-required.
- A small PVC plus lowered threshold produced usage pressure findings without consuming large testbed disk.
- A non-DMS `ResourceQuota` produced effective quota warning findings.
- Removing DMS ownership metadata produced metadata drift findings.
- Verification namespaces, PVCs, and ResourceQuotas were cleaned up at the end of the run.
