# DMS Phase 8 Implementation Prompt

이 문서는 `docs/dms-phase7.md` 완료 이후 여덟 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 8의 목표는 Phase 3부터 Phase 7까지 테스트베드 검증에서 사용하던 **synthetic Agent report 한계**를 제거하고, Kubernetes DaemonSet으로 실행되는 실제 DMS Agent가 node-local capability를 수집해 DMS API에 보고하게 만드는 것이다.

Phase 8은 filesystem quota mutation, Data Management `scan/sync/rm` live execution, VolcanoJob 실행을 새로 열지 않는다. 이번 phase에서는 실제 Agent evidence가 operational DB, effective inventory, storage mapping sanity, action-required 조회에 연결되는지 먼저 검증한다.

## Phase 8 목표

Phase 8의 핵심 기능은 다음 다섯 가지다.

1. **DMS Agent runtime and node-local prober**
2. **RM/DM Agent DaemonSet manifests**
3. **Agent report API posting loop and identity verification**
4. **Storage mapping sanity with real Agent reports**
5. **CephFS and Longhorn testbed live verification without synthetic Agent reports**

구현 완료 기준은 다음과 같다.

- `dms` CLI 또는 module entrypoint로 Agent가 one-shot probe와 loop mode를 실행할 수 있다.
- Agent는 Kubernetes Pod 안에서 node identity, worker role, mount, CSI, tool, credential, network, POSIX identity evidence를 수집한다.
- Agent는 기존 `POST /api/v1/agent/reports` endpoint에 report를 제출한다.
- 제출 시 actor는 `node:{cluster_name}:{node_name}`이어야 하며, mismatch report는 기존처럼 거부된다.
- Agent report는 기존 Phase 3 schema와 backward-compatible해야 하며, Phase 8 evidence에는 source/status/reason/checked_at 같은 진단 정보를 포함한다.
- `deploy/kubernetes`의 placeholder CronJob report 방식을 실제 DaemonSet 또는 DaemonSet 템플릿으로 교체한다.
- 테스트베드의 `cluster-a/testbed-cephfs`, `cluster-b/testbed-longhorn`, `cluster-b/longhorn-static` storage mapping sanity가 synthetic report 없이 실제 Agent report 기반으로 계산된다.
- Phase 4~7 Kubernetes namespace quota lifecycle 검증 중 storage mapping readiness가 실제 Agent report를 사용한다.
- 검증 결과는 `docs/dms-phase8-verification.md`와 `docs/dms-done.md`에 command/output 포함해 기록한다.

## 왜 Phase 8에서 Agent DaemonSet을 하는가

Phase 7까지 DMS는 PostgreSQL, LDAP, Kubernetes read-only inventory, Kubernetes `ResourceQuota` live mutation, multi-StorageClass quota, requester-scoped query, quota dedicated query API, blocked quota update semantics를 실제 테스트베드에서 검증했다.

하지만 Phase 3 이후 storage mapping sanity와 Planner guard가 사용하는 Agent evidence는 live verification script가 API에 직접 제출한 synthetic report였다. 이 상태로 Data Management preflight나 filesystem quota lifecycle을 진행하면 scheduler와 readiness 판단이 실제 node-local 상태를 반영하는지 검증하기 어렵다.

따라서 Phase 8은 다음 backend 기능을 추가하기 전에 Agent evidence의 source를 실제 Pod runtime으로 바꾼다. 이 단계가 끝나야 이후 Phase에서 Data Management read-only scan preflight 또는 filesystem resource lifecycle을 신뢰 가능한 node evidence 위에서 검증할 수 있다.

## 현재 전제

Phase 7 완료 후 전제:

- Agent report ingestion API는 이미 있다.
  - `POST /api/v1/agent/reports`
  - actor identity: `node:{cluster_name}:{node_name}`
- `AgentReport`에는 `cluster_name`, `node_name`, `node_uid`, `worker_role`, `mounts`, `csi`, `tools`, `credentials`, `networks`, `identity_evidence`가 있다.
- operational DB에는 Agent report freshness와 stale marking이 저장된다.
- effective inventory는 Kubernetes read-only inventory와 fresh Agent report를 결합한다.
- storage mapping sanity는 Kubernetes StorageClass/provisioner와 Agent RM/DM readiness를 함께 계산한다.
- Planner guard는 RM operation에는 `readiness.resource_management=Ready`, DM operation에는 `readiness.data_management=Ready`를 요구한다.
- Phase 4~7 live verification scripts는 synthetic RM/DM Agent report를 직접 제출했다.
- `deploy/kubernetes/dms-cluster.yaml`과 `deploy/kubernetes/managed-cluster-rm-worker.yaml`에는 placeholder Agent CronJob이 있으나, 실제 node-local prober가 아니다.

테스트베드 topology:

- `cluster-a`
  - control cluster 역할
  - self-managed RM target으로도 사용 가능
  - Rook/CephFS `StorageClass/testbed-cephfs`
- `cluster-b`
  - managed cluster 역할
  - Longhorn `StorageClass/testbed-longhorn`
  - Longhorn `StorageClass/longhorn-static`
- PostgreSQL
  - `192.168.56.11:30432`
  - 테스트 실행마다 operational DB와 observability DB를 새로 만든다.

## 기능 1: Agent Runtime and Prober

### Entry Point

권장 구현:

```text
src/dms/agent_daemon.py
```

권장 CLI:

```bash
dms agent-probe --once
dms agent-loop --interval 60
```

`agent-probe --once`는 테스트와 디버깅을 위해 report JSON을 stdout에 출력하거나 API에 한 번 제출한다. `agent-loop`는 DaemonSet container command로 사용하고, 주기적으로 probe 후 API에 제출한다.

### Runtime Configuration

Agent는 환경 변수와 optional mounted config file을 사용한다.

필수 또는 권장 환경 변수:

```text
DMS_AGENT_API_URL=http://dms-api.dms.svc.cluster.local
DMS_AGENT_CLUSTER_NAME=cluster-a
DMS_AGENT_WORKER_ROLE=RM|DM
DMS_AGENT_NODE_NAME=<downward API spec.nodeName>
DMS_AGENT_REPORT_INTERVAL_SECONDS=60
DMS_AGENT_REPORT_TIMEOUT_SECONDS=5
DMS_AUTH_SHARED_TOKEN=<optional shared token>
```

권장 config file:

```text
/etc/dms/agent/storages.json
```

예시:

```json
{
  "storages": [
    {
      "storage_name": "testbed-cephfs",
      "backend_type": "cephfs",
      "cluster_name": "cluster-a",
      "storage_class_name": "testbed-cephfs",
      "csi_driver": "rook-ceph.cephfs.csi.ceph.com",
      "mount_paths": ["/mnt/dms/testbed-cephfs"],
      "network_endpoints": []
    },
    {
      "storage_name": "testbed-longhorn",
      "backend_type": "longhorn",
      "cluster_name": "cluster-b",
      "storage_class_name": "testbed-longhorn",
      "csi_driver": "driver.longhorn.io",
      "mount_paths": [],
      "network_endpoints": []
    }
  ]
}
```

Agent는 config에 적힌 값을 그대로 성공 evidence로 보고하면 안 된다. config는 probe target 목록일 뿐이며, report에는 실제 관측 결과와 실패 reason을 구분해서 기록한다.

### Probe Evidence

Agent는 다음 evidence를 수집한다.

Node identity:

- `cluster_name`
- `node_name`
- `node_uid`
- `worker_role`
- pod name/namespace
- report timestamp

`node_uid`는 Kubernetes API로 현재 Node를 read해 `metadata.uid`를 사용한다. RBAC 또는 API 접근이 실패하면 fallback value를 쓰되, report evidence에 `status=Unknown`과 reason을 남긴다.

Mount evidence:

- configured `storage_name`
- configured `mount_path`
- path exists 여부
- mountpoint 여부
- filesystem type/source/options from `/proc/self/mountinfo`
- readonly 여부
- `statvfs` capacity/available summary if available
- status: `Ready`, `Missing`, `Unknown`, `Failed`

중요한 원칙:

- API server local filesystem은 readiness 판단에 사용하지 않는다.
- Agent Pod가 실제로 볼 수 없는 host mount를 Ready로 보고하지 않는다.
- testbed에서 mount가 준비되지 않은 storage는 synthetic Ready가 아니라 `Missing` 또는 `Unknown`으로 보고한다.

CSI evidence:

- configured `csi_driver`
- configured `storage_class_name`
- Kubernetes API에서 `StorageClass` exists/provisioner 확인
- Kubernetes API에서 `CSIDriver` exists 확인
- 가능하면 node-local CSI plugin socket 또는 같은 node의 CSI node pod readiness 확인
- status: `Ready`, `Missing`, `Unknown`, `Failed`

CSI probe는 read-only여야 한다. Phase 8에서 CSI object나 StorageClass를 생성, 수정, 삭제하지 않는다.

Tool evidence:

- `dsync`
- `nsync`
- `drm`
- `dscan`
- `kubectl`
- backend별 optional command

Agent는 `PATH`에서 command 존재 여부와 version command가 안전하면 version을 기록한다. command가 없으면 failure가 아니라 `Missing` capability로 기록한다.

Credential evidence:

- service account token projected 여부
- configured credential file exists 여부
- permission mode summary
- secret value는 절대 report에 포함하지 않는다.

Network evidence:

- configured endpoint DNS resolve
- optional TCP connect with small timeout
- control-plane API URL reachability
- storage/data endpoint reachability if configured

POSIX identity evidence:

- `DMS_AGENT_IDENTITY_USERS`가 설정된 경우 local NSS/SSSD compatible lookup을 수행한다.
- `uid`, `gid`, `groups`, lookup status를 기록한다.
- user가 없거나 SSSD가 준비되지 않은 경우 `Missing` 또는 `Failed`로 기록하고 Agent loop 자체는 계속 실행한다.

## 기능 2: Agent Report Shape and API Posting

### Report Schema

기존 `AgentReport` schema와 backward-compatible해야 한다.

권장 `schema_version`:

```text
phase8.v1
```

예시:

```json
{
  "schema_version": "phase8.v1",
  "reported_at": "2026-05-28T14:00:00Z",
  "cluster_name": "cluster-b",
  "node_name": "c2-worker",
  "node_uid": "node-uid-from-kubernetes-api",
  "worker_role": "RM",
  "mounts": [
    {
      "storage_name": "testbed-longhorn",
      "path": "/mnt/dms/testbed-longhorn",
      "status": "Missing",
      "reason": "configured mount path does not exist",
      "source": "agent-prober",
      "checked_at": "2026-05-28T14:00:00Z"
    }
  ],
  "csi": [
    {
      "driver": "driver.longhorn.io",
      "storage_classes": ["testbed-longhorn", "longhorn-static"],
      "status": "Ready",
      "source": "kubernetes-api",
      "checked_at": "2026-05-28T14:00:00Z"
    }
  ],
  "tools": [
    {
      "name": "dscan",
      "status": "Missing",
      "reason": "command not found in PATH"
    }
  ],
  "credentials": [],
  "networks": [],
  "identity_evidence": {}
}
```

### API Posting

Agent는 DMS API에 다음 header를 포함해 제출한다.

```text
x-dms-actor: node:{cluster_name}:{node_name}
authorization: Bearer {DMS_AUTH_SHARED_TOKEN}
```

`DMS_AUTH_SHARED_TOKEN`이 설정되지 않은 개발/testbed profile에서는 `x-dms-actor`만으로 기존 인증 skeleton이 통과할 수 있다. Phase 8 문서와 verification에는 어떤 auth mode를 사용했는지 명시한다.

### Failure Handling

Agent loop는 다음 원칙을 따른다.

- probe 하나가 실패해도 전체 report 제출을 중단하지 않는다.
- API posting 실패 시 다음 interval에 재시도한다.
- API posting 실패, auth 실패, schema validation 실패는 stdout/stderr에 구조화된 로그로 남긴다.
- retry 폭주는 피한다. 기본 interval은 60초 이상으로 둔다.
- 테스트베드 리소스가 부족하므로 backoff와 timeout을 짧게 유지한다.

## 기능 3: Kubernetes DaemonSet Manifests

### Manifest Scope

`deploy/kubernetes`에 Agent deployment manifests를 추가하거나 기존 placeholder CronJob을 교체한다.

권장 파일:

```text
deploy/kubernetes/dms-agent-daemonset.yaml
```

또는 기존 파일에 포함된 Agent CronJob을 DaemonSet으로 바꿔도 된다. 단, Phase 8 verification에서 실제 적용한 manifest path를 기록한다.

### RM Agent

RM Agent는 managed cluster에 배포한다.

대상:

- `cluster-a` for `testbed-cephfs`
- `cluster-b` for `testbed-longhorn`
- `cluster-b` for `longhorn-static`

요구 사항:

- `DMS_AGENT_WORKER_ROLE=RM`
- 각 cluster의 `DMS_AGENT_CLUSTER_NAME`이 정확해야 한다.
- Kubernetes API read-only RBAC를 갖는다.
- StorageClass/CSIDriver/Node read-only 권한을 갖는다.
- CSI plugin socket 또는 kubelet plugin dir hostPath mount가 필요한 경우 read-only로 제한한다.
- host root filesystem 전체를 mount하지 않는다.
- privileged container는 Phase 8 기본값으로 사용하지 않는다. 꼭 필요하면 verification에 이유를 남긴다.

### DM Agent

DM Agent는 DMS control cluster worker node에 배포한다.

기본 대상:

- `cluster-a`

요구 사항:

- `DMS_AGENT_WORKER_ROLE=DM`
- DM Worker가 실제로 볼 수 있는 mount/tool/identity/network capability만 Ready로 보고한다.
- `cluster-b` Longhorn storage가 control cluster DM node에서 실제로 mount되어 있지 않다면 Ready로 꾸미지 않고 `Missing` 또는 `Unknown`으로 보고한다.
- 이 경우 storage mapping sanity는 `resource_management=Ready`, `data_management=Missing` 또는 `Degraded`가 될 수 있으며, Phase 8 verification에 실제 상태로 기록한다.

### Resource Efficiency

테스트베드 CPU, memory, disk가 부족하므로 Agent resource request는 낮게 둔다.

권장값:

```yaml
resources:
  requests:
    cpu: 25m
    memory: 64Mi
  limits:
    cpu: 100m
    memory: 128Mi
```

기본 report interval은 60초 이상으로 둔다. live verification에서 stale test가 필요하면 stale threshold를 낮추되, 긴 대기 시간을 만들지 않는다.

## 기능 4: Storage Mapping Sanity Integration

Phase 8에서는 기존 sanity logic을 최대한 재사용한다.

요구 사항:

- `StorageMappingSanityService`가 Phase 8 report의 structured evidence를 기존 `mounts_by_storage_name`, `csi_by_driver` grouping에 포함할 수 있어야 한다.
- 기존 Phase 3 report도 계속 처리한다.
- csi evidence가 `status=Ready`가 아닌 경우 Ready candidate로 쓰지 않는다.
- mount evidence가 `status=Ready`가 아닌 경우 Ready candidate로 쓰지 않는다.
- stale Agent report는 기존처럼 effective inventory에서 제외한다.
- 같은 node의 최신 fresh report가 여러 개 있으면 effective inventory가 중복 candidate를 과도하게 만들지 않도록 한다. 필요하면 latest-per-role-node view를 추가한다.
- action-required query는 stale report, identity mismatch, storage mapping `Failed`/`Unknown`, active mapping의 missing RM readiness를 운영자가 확인할 수 있게 보여준다.

중요한 검증 기준:

- `cluster-a/testbed-cephfs`는 실제 RM Agent report로 resource management readiness를 확인한다.
- `cluster-b/testbed-longhorn`은 실제 RM Agent report로 resource management readiness를 확인한다.
- `cluster-b/longhorn-static`은 실제 RM Agent report로 resource management readiness를 확인한다.
- DM readiness는 실제 DM Agent가 볼 수 있는 storage만 Ready로 둔다. 보이지 않는 storage를 synthetic evidence로 보완하지 않는다.

## 기능 5: Testbed Live Verification

새 verification script를 추가한다.

```text
scripts/phase8_agent_daemonset_live.py
scripts/verify-phase8-testbed.sh
```

검증 전 테스트베드 metadata를 확인한다.

```bash
cat /home/mason/workspace/testbed/testbed-info.json
cat /home/mason/workspace/testbed/testbed-summary.json
```

권장 검증 흐름:

1. fresh operational/observability PostgreSQL DB를 만든다.
2. DMS API를 실행한다.
3. Phase 8 Agent image를 테스트베드에서 사용할 수 있게 준비한다.
   - 로컬 registry를 쓰는 경우 registry 주소와 image tag를 verification doc에 기록한다.
   - 새 패키지 설치가 필요하면 `/home/mason/workspace/testbed` 문서에 남긴다.
4. `cluster-a`에 RM Agent DaemonSet을 배포한다.
5. `cluster-b`에 RM Agent DaemonSet을 배포한다.
6. `cluster-a` control cluster에 DM Agent DaemonSet을 배포한다.
7. Agent Pod가 `Running`이고 restart loop가 없는지 확인한다.
8. `GET /api/v1/operations/agent-reports`로 fresh report가 들어오는지 확인한다.
9. deliberate mismatch report를 제출해 identity mismatch가 403으로 거부되고 observability event가 기록되는지 확인한다.
10. `GET /api/v1/operations/inventory`에서 실제 Agent report가 effective inventory에 반영되는지 확인한다.
11. `cluster-a/testbed-cephfs`, `cluster-b/testbed-longhorn`, `cluster-b/longhorn-static` storage mapping을 등록하고 sanity check를 수행한다.
12. 각 mapping의 RM readiness가 실제 Agent report 기반으로 `Ready`인지 확인한다.
13. DM readiness는 실제 DM Agent가 관측한 상태 그대로 기록한다.
14. stale threshold를 낮추거나 report loop를 잠시 중단해 stale handling과 action-required 노출을 확인한다.
15. Phase 7 quota dedicated query API로 DB/live quota state 조회가 계속 동작하는지 확인한다.
16. Phase 6/7 quota lifecycle subset을 synthetic Agent report 없이 수행한다.
    - `cluster-a/testbed-cephfs` create/check/delete
    - `cluster-b/testbed-longhorn` + `longhorn-static` multi-StorageClass create/check/delete
17. verification namespace, quota, PVC, Agent manifests를 cleanup한다.

### Required Command Evidence

verification 문서에는 최소한 다음 output을 남긴다.

```bash
kubectl --context cluster-a -n dms get pods -o wide
kubectl --context cluster-b -n dms get pods -o wide
kubectl --context cluster-a -n dms logs ds/dms-rm-agent --tail=80
kubectl --context cluster-b -n dms logs ds/dms-rm-agent --tail=80
curl -s -H 'x-dms-actor: api-client' http://127.0.0.1:8000/api/v1/operations/agent-reports
curl -s -H 'x-dms-actor: api-client' http://127.0.0.1:8000/api/v1/operations/inventory
curl -s -H 'x-dms-actor: api-client' http://127.0.0.1:8000/api/v1/operations/action-required
```

context 이름이 테스트베드에서 다르면 실제 command를 기록한다.

## Suggested Implementation Order

### Step 1: Agent prober unit

- `src/dms/agent_daemon.py` 또는 유사 module 추가
- mountinfo parser 추가
- tool lookup 추가
- network probe 추가
- identity lookup 추가
- Kubernetes Node/StorageClass/CSIDriver read-only probe 추가
- one-shot probe JSON 생성

검증:

```bash
python -m pytest -q tests/test_phase8_agent_daemon.py
```

### Step 2: API posting client and CLI

- `dms agent-probe --once`
- `dms agent-loop --interval`
- `x-dms-actor` generation
- optional bearer token support
- posting retry/backoff
- structured log output

검증:

```bash
python -m pytest -q tests/test_phase3_inventory.py tests/test_phase8_agent_daemon.py
```

### Step 3: Inventory normalization

- Phase 8 structured evidence를 effective inventory grouping에 반영
- `status=Ready` evidence만 readiness candidate로 사용
- stale report handling regression test 추가
- identity mismatch regression test 유지

검증:

```bash
python -m pytest -q tests/test_phase3_inventory.py tests/test_phase7_operational_queries.py
```

### Step 4: Kubernetes DaemonSet manifests

- RM Agent DaemonSet manifest 추가
- DM Agent DaemonSet manifest 추가
- read-only RBAC 추가
- testbed용 config map/secret wiring 추가
- existing placeholder Agent CronJob 정리

검증:

```bash
kubectl --context cluster-a apply -f deploy/kubernetes/dms-agent-daemonset.yaml
kubectl --context cluster-b apply -f deploy/kubernetes/dms-agent-daemonset.yaml
```

실제 context와 overlay 방식은 테스트베드에 맞게 조정한다.

### Step 5: Testbed live verification

- `scripts/phase8_agent_daemonset_live.py`
- `scripts/verify-phase8-testbed.sh`
- CephFS and Longhorn storage mapping sanity
- quota lifecycle subset without synthetic Agent reports
- stale/action-required verification

검증:

```bash
./scripts/verify-phase8-testbed.sh
```

## API Summary

Phase 8에서 새 public user-facing API는 추가하지 않는다.

기존 Agent report endpoint를 실제 DaemonSet runtime이 사용한다.

```text
POST /api/v1/agent/reports
```

운영 검증에 사용하는 existing query endpoint:

```text
GET /api/v1/operations/agent-reports
GET /api/v1/operations/inventory
GET /api/v1/operations/storage-mappings
GET /api/v1/operations/storage-mappings/{storage_name}
GET /api/v1/operations/action-required
GET /api/v1/operations/worker-agent-health
GET /api/v1/operations/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}
```

Agent report 제출 예:

```bash
curl -s \
  -X POST \
  -H 'content-type: application/json' \
  -H 'x-dms-actor: node:cluster-b:c2-worker' \
  http://127.0.0.1:8000/api/v1/agent/reports \
  -d @report.json
```

## Done Documentation

Phase 8 완료 시 다음 문서를 갱신한다.

- `docs/dms-phase8-verification.md`
  - local pytest command/output
  - image build/push command/output
  - testbed metadata 확인 output
  - DaemonSet apply/status/log output
  - Agent report API response sample
  - inventory/action-required response sample
  - CephFS/Longhorn storage mapping sanity result
  - quota lifecycle subset result without synthetic reports
- `docs/dms-done.md`
  - `Implemented Through Phase 8`
  - Phase 8 implemented scope
  - live verification target and output
  - re-run command
  - still-not-implemented list에서 실제 Agent DaemonSet 관련 항목 제거 또는 축소
- 테스트베드 문서
  - Agent image registry/tag
  - 추가 설치한 패키지
  - 적용한 manifest와 cleanup 방법

## Not In Scope

다음은 Phase 8 범위가 아니다.

- filesystem directory create/update/block/delete live mutation
- CephFS/GPFS/POSIX quota command 실행
- Kubernetes namespace lifecycle delete
- Data Management `scan/sync/rm` live execution
- VolcanoJob create/watch/terminate
- mpifileutils production image build and pinning
- full mTLS ingress validation
- Agent auto-upgrade/rollout controller
- Helm chart 또는 Kustomize production packaging 완성
- UI/dashboard
- 일반 GPU workload node 전체 inventory rollout

## Phase 8 완료 후 다음 Phase 후보

Phase 8이 성공하면 다음 phase는 둘 중 하나로 좁히는 것이 좋다.

### Phase 9A: Data Management Read-only Scan Preflight

- 실제 DM Agent report 기반 candidate pool 사용
- LDAP identity mapping과 POSIX permission preflight 연결
- read-only `scan` request/job skeleton을 실제 runtime preflight까지 검증
- scan artifact persistence는 작게 시작

### Phase 9B: Filesystem Resource Management Minimal Lifecycle

- CephFS test path 또는 GPFS template 중 하나 선택
- directory create/check/sync/delete minimal lifecycle
- quota mutation은 destructive risk가 있으므로 test path에 제한
- Kubernetes quota lifecycle과 같은 DB/live drift model 재사용

권장 순서는 Phase 8 결과에 따라 결정한다. DM Agent가 실제 mount/tool/identity evidence를 충분히 제공하면 `Phase 9A`가 좋고, 데이터 작업용 mount 준비가 부족하면 `Phase 9B`로 filesystem resource lifecycle을 먼저 좁혀 진행한다.
