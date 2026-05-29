# DMS Phase 8 Verification

Date: 2026-05-29 06:47 +0900

Phase 8 implements and verifies the real DMS Agent DaemonSet path. The important verification boundary is that storage mapping sanity and the quota lifecycle subset used **Phase 8 Agent reports posted by Kubernetes Pods**, not synthetic reports submitted by the live verification script.

## Local Regression

Command:

```bash
cd /home/mason/workspace/dms
python3 -m pytest -q
```

Output:

```text
49 passed in 35.48s
```

Additional checks:

```bash
PYTHONPATH=src \
DMS_AGENT_CLUSTER_NAME=cluster-a \
DMS_AGENT_NODE_NAME=test-node \
DMS_AGENT_WORKER_ROLE=RM \
DMS_AGENT_TOOLS=definitely-missing-dms-tool \
python3 -m dms.cli agent-probe --once | python3 -m json.tool >/tmp/dms-agent-probe.json

python3 -m py_compile \
  src/dms/agent_daemon.py \
  src/dms/cli.py \
  src/dms/inventory.py \
  scripts/phase8_agent_daemonset_live.py

git diff --check -- \
  src/dms/agent_daemon.py \
  src/dms/cli.py \
  src/dms/inventory.py \
  tests/test_phase8_agent_daemon.py \
  deploy/Dockerfile \
  deploy/kubernetes/dms-agent-daemonset.yaml \
  deploy/kubernetes/dms-cluster.yaml \
  deploy/kubernetes/managed-cluster-rm-worker.yaml \
  scripts/phase8_agent_daemonset_live.py \
  scripts/verify-phase8-testbed.sh
```

Representative one-shot Agent report output:

```json
{
  "cluster_name": "cluster-a",
  "node_name": "test-node",
  "node_uid": "test-node",
  "schema_version": "phase8.v1",
  "worker_role": "RM",
  "tools": [
    {
      "name": "definitely-missing-dms-tool",
      "path": null,
      "reason": "command not found in PATH",
      "status": "Missing"
    }
  ]
}
```

## Testbed Pre-check

Command:

```bash
ssh c1-control 'kubectl get nodes -o wide; kubectl get storageclass -o wide'
ssh c2-control 'kubectl get nodes -o wide; kubectl get storageclass -o wide'
ssh c1-control 'systemctl is-active docker-registry && curl -fsS http://192.168.56.11:5000/v2/ && echo registry-ok'
```

Observed:

```text
cluster-a nodes: c1-control Ready, c1-worker Ready
cluster-a StorageClass: testbed-cephfs -> rook-ceph.cephfs.csi.ceph.com
cluster-b nodes: c2-control Ready, c2-worker Ready
cluster-b StorageClass: testbed-longhorn, longhorn-static, testbed-longhorn-retain -> driver.longhorn.io
local registry: active, /v2/ returned {}
```

## Live Verification

Command:

```bash
cd /home/mason/workspace/dms
DMS_PHASE8_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase8-testbed.sh
```

The image was built and pushed once with `./scripts/verify-phase8-testbed.sh`. The host Docker daemon is not configured for the testbed HTTP registry, so the script fell back to `docker save` plus `skopeo copy` on `c1-control`. The final verification run reused the pushed image with `DMS_PHASE8_SKIP_IMAGE_BUILD=1`.

Image evidence:

```text
docker build -f deploy/Dockerfile -t 192.168.56.11:5000/dms:phase8 .
docker push failed; falling back to docker save + skopeo copy on c1-control
Writing manifest to image destination
```

Kubernetes deployment evidence:

```text
deployment "dms-api" successfully rolled out
daemon set "dms-rm-agent" successfully rolled out
daemon set "dms-dm-agent" successfully rolled out
daemon set "dms-rm-agent" successfully rolled out
```

Pod evidence:

```text
cluster-a:
dms-api-5998f8f4bc-6vs59   1/1 Running c1-control
dms-dm-agent-b29vm         1/1 Running c1-worker
dms-dm-agent-fwtxj         1/1 Running c1-control
dms-rm-agent-5sfzn         1/1 Running c1-worker
dms-rm-agent-lldlk         1/1 Running c1-control

cluster-b:
dms-rm-agent-khxt5         1/1 Running c2-worker
dms-rm-agent-mdsnh         1/1 Running c2-control
```

Agent log evidence:

```text
{"event": "agent_report_posted", "report_id": "agent_d1774e37b58c4e85ac523e33585b35ff", "status": "Fresh"}
```

Live verification summary:

```json
{
  "status": "ok",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase8_20260529064715",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase8_obs_20260529064715",
  "phase8_reports": {
    "cluster-a:DM": {
      "node_name": "c1-worker",
      "report_id": "agent_d7eba85f5bff4c76a5c667d330f1e70e"
    },
    "cluster-a:RM": {
      "node_name": "c1-worker",
      "report_id": "agent_3dda47cfbd2f4d95ada039cda1ca654d"
    },
    "cluster-b:RM": {
      "node_name": "c2-worker",
      "report_id": "agent_96cccd7d417e4881bc66eb9af2b495bc"
    }
  },
  "identity_mismatch": {
    "status_code": 403
  },
  "storage_mappings": [
    {
      "storage_name": "cephfs-a",
      "storage_class_name": "testbed-cephfs",
      "status": "Ready",
      "readiness": {
        "resource_management": "Ready",
        "data_management": "Ready",
        "inventory": "Ready"
      },
      "rm_candidate_count": 2,
      "dm_candidate_count": 2
    },
    {
      "storage_name": "longhorn-b",
      "storage_class_name": "testbed-longhorn",
      "status": "Degraded",
      "readiness": {
        "resource_management": "Ready",
        "data_management": "Missing",
        "inventory": "Ready"
      },
      "rm_candidate_count": 2,
      "dm_candidate_count": 0
    },
    {
      "storage_name": "longhorn-static-b",
      "storage_class_name": "longhorn-static",
      "status": "Degraded",
      "readiness": {
        "resource_management": "Ready",
        "data_management": "Missing",
        "inventory": "Ready"
      },
      "rm_candidate_count": 2,
      "dm_candidate_count": 0
    }
  ],
  "quota_subset": [
    {
      "target": "cephfs",
      "namespace": "dms-phase8-cephfs-30271240",
      "create_request_id": "req_110fce7e43044356988731cdca596250"
    },
    {
      "target": "longhorn",
      "namespace": "dms-phase8-longhorn-30271240",
      "create_request_id": "req_41a6484b3d2b4d6a9f5f5d9d75b5178c"
    }
  ],
  "stale_handling": {
    "marked_stale": 6,
    "stale_report_count": 6
  },
  "action_required_issue_types": ["agent_report_stale", "missing_dm_readiness"]
}
```

검증 의미:

- `dms-api`가 테스트베드 `cluster-a`에 배포되고 `NodePort 30088`로 Agent report를 받았다.
- `cluster-a`에는 RM/DM Agent DaemonSet을, `cluster-b`에는 RM Agent DaemonSet을 배포했다.
- Agent Pod가 `POST /api/v1/agent/reports`로 `schema_version=phase8.v1` report를 제출했고 operational PostgreSQL에 Fresh report로 저장됐다.
- actor mismatch report는 `403`으로 거부됐고 observability event가 기록됐다.
- `cluster-a/testbed-cephfs`, `cluster-b/testbed-longhorn`, `cluster-b/longhorn-static` storage mapping의 RM readiness가 실제 Agent report로 `Ready`가 됐다.
- control cluster DM Agent는 Longhorn StorageClass를 볼 수 없으므로 Longhorn 계열 mapping의 DM readiness는 `Missing`으로 남고, action-required에 `missing_dm_readiness`가 노출됐다. 이는 synthetic evidence로 보완하지 않은 기대 결과다.
- synthetic Agent report 없이 CephFS quota create/check/delete와 Longhorn multi-StorageClass quota create/check/delete subset을 실제 Kubernetes API에서 검증했다.
- 실제 Phase 8 Agent reports 6개를 stale 처리하고 `GET /api/v1/operations/action-required`에서 `agent_report_stale` 노출을 확인했다.
- verification namespace `dms-phase8`과 quota test namespace는 cleanup됐다.

## Re-run

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase8-testbed.sh
```

이미 registry에 image가 올라가 있고 코드 변경이 없으면 다음처럼 image build를 건너뛸 수 있다.

```bash
cd /home/mason/workspace/dms
DMS_PHASE8_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase8-testbed.sh
```
