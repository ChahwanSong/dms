# DMS Done / Verified Status

Last updated: 2026-06-03 23:59 +0900

이 문서는 DMS 구현이 진행될 때마다 계속 갱신하는 완료/검증 기록이다.
새 phase가 끝나면 같은 구조로 `Implemented`, `Live Verification`,
`Re-run Commands`, `Not Implemented Yet`, `Comments`를 추가하거나 갱신한다.

중요한 기준:

- `Done`은 실제 테스트베드 또는 실제 외부 시스템에 연결해 확인된 기능만 의미한다.
- local pytest, stub adapter, synthetic data는 보조 회귀 검증으로만 기록한다.
- 아직 실제 backend side effect가 구현되지 않은 기능은 성공처럼 적지 않고 명확히 미구현으로 남긴다.
- Phase 18까지의 실제 live/테스트베드 검증 대상은 PostgreSQL, OpenLDAP/SSSD, Kubernetes read-only inventory, `cluster-b` Kubernetes ResourceQuota/PVC admission, `cluster-a/testbed-cephfs`와 `cluster-b/testbed-longhorn` Kubernetes ResourceQuota lifecycle, `cluster-b` multi-StorageClass quota lifecycle, requester-scoped request query, Kubernetes namespace quota dedicated query API, blocked quota update semantics, 실제 DMS Agent DaemonSet report, Agent 기반 storage mapping sanity, Kubernetes default quota reset, on-demand quota audit, drift/usage pressure/effective quota action-required aggregation, DMS-managed ResourceQuota metadata drift detection, `cluster-a/c1-worker` 및 `cluster-b/c2-worker` host-mounted CephFS filesystem create/delete lifecycle, filesystem expiry query, API-driven filesystem expiration sweep, filesystem block/unblock lifecycle, filesystem expiry update/import default, LDAP access group membership, POSIX permission boundary, CephFS directory capacity/file-count quota apply/enforcement, quota update/decrease apply/check/sync/action-required, existing directory quota-only assignment, existing directory full import, Kubernetes long-running Planner/RM Worker Deployment 기반 RM 처리, RM Worker scale/restart/stale-claim recovery evidence, observability DB write failure safe boundary, live RM Worker의 Longhorn Kubernetes ResourceQuota apply, unknown backend fail-closed/action-required, Kubernetes namespace quota `expires_at` create/update/import, expired query/action-required, on-demand expiration sweep block, DMS API trusted edge mTLS evidence validation, token+mTLS combined authentication, certificate subject actor derivation, direct spoof NetworkPolicy 차단, synthetic GPFS CSI StorageClass 기반 Kubernetes ResourceQuota live adapter apply/read-back, testbed PostgreSQL 기반 maintenance/drain/heartbeat/stale-recovery guard이다. GPFS는 IBM Storage Scale command adapter 구현과 fake executor regression까지 완료했고, live GPFS filesystem 검증은 테스트베드에 GPFS cluster가 없어 skip evidence로 남겼다.
- Phase 19의 실제 live/테스트베드 검증 대상은 read-only Data Management `scan`이다. API intake, Identity Mapping/POSIX preflight, DM Agent node selection, runtime preflight Pod, VolcanoJob, pinned real mpifileutils `dscan`, artifact file write, DB summary parsing/query/action-required가 검증됐다.
- Phase 20의 실제 live/테스트베드 검증 대상은 Data Management `sync`/`rm`이다. `dsync` preview/execution, `drm` preview/execution, explicit confirm, preview expiry, missing identity, raw option/path guard, VolcanoJob monitoring, artifact parsing/query/action-required, standalone multi-node MPI `dscan` smoke가 검증됐다. `nsync` separated-role live execution은 아직 Done으로 보지 않는다.

## Testbed Architecture

테스트베드 문서 기준:

- `/home/mason/workspace/testbed/testbed-info.json`
- `/home/mason/workspace/testbed/testbed-summary.json`
- `/home/mason/workspace/testbed/TOPOLOGY.md`
- `/home/mason/workspace/testbed/PostgreSQL.md`
- `/home/mason/workspace/testbed/OpenLDAP-SSSD.md`
- `/home/mason/workspace/testbed/CephFS.md`
- `/home/mason/workspace/testbed/Longhorn.md`

현재 DMS 검증에 사용한 topology:

- `cluster-a`
  - control cluster 역할
  - DMS control plane이 붙는 기준 cluster
  - self-managed RM target으로도 사용 가능
  - PostgreSQL NodePort 제공
  - Rook/CephFS `StorageClass/testbed-cephfs`
- `cluster-b`
  - managed cluster 역할
  - Longhorn `StorageClass/testbed-longhorn`
  - Longhorn `StorageClass/longhorn-static`
  - VM-packaged CephFS host mount `/mnt/testbed-cephfs-c2`
- OpenLDAP
  - `ldap://192.168.56.31`
  - DMS Identity Mapping은 LDAP direct read-only lookup만 성공 기준으로 사용
- PostgreSQL
  - host: `192.168.56.11`
  - NodePort: `30432`
  - 테스트 실행마다 operational DB와 observability DB를 새로 만든다.

### Topology Check Output

Command:

```bash
date '+%Y-%m-%d %H:%M:%S %z'
ssh c1-control "kubectl get nodes -o wide; printf '\n--- storageclasses ---\n'; kubectl get storageclass; printf '\n--- postgresql ---\n'; kubectl -n postgresql get pods,svc"
ssh c2-control "kubectl get nodes -o wide; printf '\n--- storageclasses ---\n'; kubectl get storageclass; printf '\n--- longhorn sample ---\n'; kubectl -n longhorn-system get pods | sed -n '1,12p'"
```

Output:

```text
2026-05-28 07:33:51 +0900

cluster-a:
NAME         STATUS   ROLES                  AGE   VERSION   INTERNAL-IP     EXTERNAL-IP   OS-IMAGE             KERNEL-VERSION      CONTAINER-RUNTIME
c1-control   Ready    control-plane,worker   32h   v1.34.6   192.168.56.11   <none>        Ubuntu 24.04.4 LTS   6.8.0-106-generic   cri-o://1.34.6
c1-worker    Ready    worker                 32h   v1.34.6   192.168.56.12   <none>        Ubuntu 24.04.4 LTS   6.8.0-106-generic   cri-o://1.34.6

--- storageclasses ---
NAME                       PROVISIONER                     RECLAIMPOLICY   VOLUMEBINDINGMODE      ALLOWVOLUMEEXPANSION   AGE
testbed-cephfs             rook-ceph.cephfs.csi.ceph.com   Delete          Immediate              true                   8h
testbed-postgresql-local   kubernetes.io/no-provisioner    Delete          WaitForFirstConsumer   false                  9h

--- postgresql ---
NAME               READY   STATUS    RESTARTS   AGE
pod/postgresql-0   1/1     Running   1          9h

NAME                          TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)          AGE
service/postgresql            ClusterIP   10.111.73.27     <none>        5432/TCP         9h
service/postgresql-nodeport   NodePort    10.104.212.222   <none>        5432:30432/TCP   9h

cluster-b:
NAME         STATUS   ROLES                  AGE   VERSION   INTERNAL-IP     EXTERNAL-IP   OS-IMAGE             KERNEL-VERSION      CONTAINER-RUNTIME
c2-control   Ready    control-plane,worker   32h   v1.34.6   192.168.56.21   <none>        Ubuntu 24.04.4 LTS   6.8.0-106-generic   cri-o://1.34.6
c2-worker    Ready    worker                 32h   v1.34.6   192.168.56.22   <none>        Ubuntu 24.04.4 LTS   6.8.0-106-generic   cri-o://1.34.6

--- storageclasses ---
NAME               PROVISIONER          RECLAIMPOLICY   VOLUMEBINDINGMODE   ALLOWVOLUMEEXPANSION   AGE
longhorn-static    driver.longhorn.io   Delete          Immediate           true                   8h
testbed-longhorn   driver.longhorn.io   Delete          Immediate           true                   8h

--- longhorn sample ---
NAME                                                READY   STATUS    RESTARTS   AGE
csi-attacher-6f99fcfc46-8xzsg                       1/1     Running   0          8h
csi-provisioner-594fbcb647-pvf6x                    1/1     Running   0          8h
csi-resizer-6b6dbb78f-jxdtz                         1/1     Running   0          8h
csi-snapshotter-6d49f75d6d-7qq44                    1/1     Running   0          8h
engine-image-ei-c9fa6d45-89p65                      1/1     Running   0          8h
engine-image-ei-c9fa6d45-lx6pn                      1/1     Running   0          8h
instance-manager-c68f508166d925d0903e7a68d9e3f44f   1/1     Running   0          8h
instance-manager-e699899236b1673563583f6ad0345a71   1/1     Running   0          8h
longhorn-csi-plugin-7hr95                           3/3     Running   0          8h
longhorn-csi-plugin-pddq5                           3/3     Running   0          8h
longhorn-driver-deployer-6f94cb9fd9-4pxv7           1/1     Running   0          8h
```

## Implemented Through Phase 18

### Phase 1: Core Lifecycle Skeleton

확실히 구현된 범위:

- API request validation and persistence skeleton
- operational DB와 observability DB 분리 구조
- authentication failure는 operational request를 만들지 않고 observability event만 기록
- authorization failure는 operational request/result에 terminal state로 기록하고 plan을 만들지 않음
- request -> plan -> run -> result lifecycle repository
- Planner skeleton
- RM Worker runtime skeleton
- DM Worker runtime skeleton
- Data Management `scan/sync/rm` request shape, preview/confirm state skeleton
- Operational Query skeleton
- storage mapping 기본 table과 uniqueness

주의:

- Phase 1의 RM/DM backend execution은 실제 filesystem, Kubernetes, Volcano side effect가 아니다.
- stub adapter 기반 회귀 검증은 가능하지만, 실제 backend 기능 완료로 보지 않는다.

### Phase 2: PostgreSQL + LDAP

확실히 구현된 범위:

- SQLite뿐 아니라 실제 PostgreSQL에서 DMS migrations 적용
- operational PostgreSQL과 observability PostgreSQL 분리
- PostgreSQL에 request/plan/run/result lifecycle state 저장
- Identity Mapping API가 중앙 LDAP를 직접 read-only 조회
- LDAP lookup 결과의 UID/GID/groups를 operational DB에 저장
- expected identity와 LDAP 결과 불일치 시 `NeedsReview`
- LDAP user missing 시 Active mapping으로 저장하지 않음
- `Disabled` mapping refresh가 mapping을 다시 활성화하지 않음

Live 검증 대상:

- 실제 PostgreSQL: `192.168.56.11:30432`
- 실제 OpenLDAP: `ldap://192.168.56.31`

### Phase 3: Inventory + Storage Mapping Sanity

확실히 구현된 범위:

- Agent report domain model 확장
  - schema version
  - reported/received/stale timestamp
  - worker role
  - mount, CSI, tool, credential, network, identity evidence
- Agent report persistence and freshness
  - Fresh/Stale 저장
  - stale report는 query 가능
  - stale report는 effective inventory에서 제외
- Agent identity mismatch rejection
  - expected actor: `node:{cluster_name}:{node_name}`
  - mismatch는 Fresh inventory에 저장하지 않고 diagnostic event 기록
- Kubernetes read-only inventory adapter
  - `ssh-kubectl`
  - `kubectl`
  - optional `python-client`
  - Node, StorageClass, CSI driver read path
- Effective inventory service
  - Kubernetes read-only inventory와 fresh Agent report 결합
  - RM/DM worker role별 candidate evidence 구성
- Storage mapping sanity
  - backend type 존재 확인
  - cluster inventory 존재 확인
  - StorageClass 존재 확인
  - CSI provisioner match/mismatch 확인
  - RM readiness, DM readiness 계산
  - `Ready`, `Degraded`, `Unknown`, `Failed`
- Storage mapping persistence
  - detailed `sanity_result`
  - `readiness`
  - `sanity_checked_at`
  - `disabled_at`, `disabled_reason`
  - `updated_by`
- Storage mapping direct control mutation audit
  - upsert/check before/after/result 기록
  - active work conflict는 409와 `Conflict` mutation 기록
- Planner guard
  - missing/failed/unknown/disabled mapping fail-closed
  - RM operation은 RM readiness 필요
  - DM operation은 DM readiness 필요
  - failed mapping request는 plan을 만들지 않고 `backend_side_effect=false`
- Operational Query
  - inventory query
  - agent report query
  - storage mapping query
  - action-required query
- API pod filesystem observation 배제
  - API process는 `mount_path` existence를 검사하지 않음
  - API-local path는 sanity/readiness/effective inventory/Planner decision에 사용하지 않음

Live 검증 대상:

- 실제 PostgreSQL
- 실제 Kubernetes read-only inventory
- 실제 StorageClass/provisioner 정보

주의:

- DMS Agent DaemonSet은 아직 구현/배포되지 않았다.
- Phase 3 live 검증의 Agent report는 API를 통해 제출한 synthetic node evidence다.
- 이 synthetic report는 repository/API/freshness/sanity/planner logic 검증에는 실제 operational DB를 사용하지만, node-local probe daemon 검증은 아니다.

### Phase 4: Kubernetes Namespace Storage Quota Live Apply

확실히 구현된 범위:

- Kubernetes namespace quota create/apply를 실제 `cluster-b` Kubernetes API에 적용
- DMS-managed `ResourceQuota` 이름 고정: `dms-storage-quota`
- `storage_class_quotas[].storage_name`을 storage mapping에서 `storage_class_name`으로 derive
- namespace-wide hard quota rendering
  - `requests.storage`
  - `persistentvolumeclaims`
- StorageClass-specific hard quota rendering
  - `testbed-longhorn.storageclass.storage.k8s.io/requests.storage`
- RM Worker가 live Kubernetes adapter로 namespace ensure 및 ResourceQuota apply 수행
- ResourceQuota apply 후 Kubernetes API에서 `spec.hard`, `status.hard`, `status.used` read-back
- operational PostgreSQL `resources`와 `results`에 live observed state 저장
- observability PostgreSQL에 ResourceQuota apply started/completed event 저장
- 실제 Longhorn PVC admission 검증
  - 64Mi PVC `Bound`
  - 추가 96Mi PVC는 `exceeded quota`로 admission reject

Live 검증 대상:

- 실제 PostgreSQL: `192.168.56.11:30432`
- 실제 Kubernetes mutation: `cluster-b`
- 실제 Longhorn StorageClass: `testbed-longhorn`

주의:

- Phase 4 단독 검증은 create/apply flow만 다뤘다. update/block/delete/sync/check lifecycle은 Phase 5에서 별도 검증했다.
- verification namespace 삭제는 테스트 cleanup으로만 수행했다. DMS lifecycle delete 구현이 아니다.
- DMS Agent DaemonSet은 아직 없으므로 Agent report는 Phase 3과 동일하게 API로 제출한 synthetic evidence다.
- ResourceQuota apply와 PVC admission은 stub/mock가 아니라 실제 Kubernetes/Longhorn backend 검증이다.

### Phase 5: Kubernetes Namespace Storage Quota Lifecycle

확실히 구현된 범위:

- Kubernetes namespace quota create 이후 운영 lifecycle 확장
  - update
  - quota decrease guard
  - block
  - unblock
  - consistency check
  - sync from live state
  - delete
- DMS-managed `ResourceQuota/dms-storage-quota`만 update/block/delete/sync/check 대상으로 처리
- delete는 `ResourceQuota/dms-storage-quota`만 삭제하고 namespace는 삭제하지 않음
- non-DMS ResourceQuota는 DMS delete에서 보존
- update 요청은 existing desired state와 request payload를 merge해 plan 생성
- quota decrease guard는 operational DB에 저장된 live `status.used`를 기준으로 backend side effect 없이 `Rejected`
- block은 restore 가능한 hard limit을 `block_state.restore_hard`에 저장하고 hard limit을 `0`으로 적용
- unblock은 저장된 restore hard limit으로 ResourceQuota 복구
- check는 live `spec.hard`와 DB desired hard limit을 read-only 비교해 `Consistent`, `Drifted`, `Missing` 상태 기록
- sync는 Kubernetes를 변경하지 않고 live `spec.hard`를 operational PostgreSQL desired/applied/observed state로 수용
- RM Worker가 operation별 observability event 기록
  - apply/update/block/unblock/check/sync/delete started/completed/failed
- API에 read-only consistency check endpoint 추가
  - `POST /api/v1/resource-management/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:check`

Live 검증 대상:

- 실제 PostgreSQL: `192.168.56.11:30432`
- 실제 Kubernetes mutation/read: `cluster-a`, `cluster-b`
- 실제 CephFS StorageClass: `cluster-a/testbed-cephfs`
- 실제 Longhorn StorageClass: `cluster-b/testbed-longhorn`

주의:

- DMS lifecycle delete는 namespace delete가 아니다. Phase 5의 delete 성공 기준은 `ResourceQuota/dms-storage-quota` 삭제다.
- namespace/PVC cleanup은 verification script cleanup으로만 수행했다.
- DMS Agent DaemonSet은 아직 없으므로 Agent report는 Phase 3과 동일하게 API로 제출한 synthetic evidence다.
- Phase 5는 multi StorageClass quota entry 전체 운영 검증이나 non-DMS ResourceQuota effective quota warning/query를 완료 범위로 보지 않는다. 해당 항목은 Phase 6에서 별도 검증했다.

### Phase 6: Kubernetes Multi-StorageClass Quota + Effective Warning

확실히 구현된 범위:

- 하나의 Kubernetes namespace quota resource에 여러 `storage_class_quotas[]` entry 허용
- 각 `storage_name` mapping에서 `storage_class_name`, `cluster_name` derive
- namespace-wide quota와 StorageClass-specific quota key를 하나의 `ResourceQuota/dms-storage-quota`에 렌더링
  - `requests.storage`
  - `persistentvolumeclaims`
  - `<storageclass>.storageclass.storage.k8s.io/requests.storage`
  - `<storageclass>.storageclass.storage.k8s.io/persistentvolumeclaims`
- multi-entry create/update lifecycle
- update에서 `storage_class_quotas[]` full replacement semantics
- duplicate `storage_name`, cross-cluster mapping, storage class mismatch validation
- StorageClass-specific `status.used`까지 포함한 decrease guard
- block/unblock이 모든 hard key를 0/restore 처리
- check가 key별 drift issue 기록
- sync가 live hard를 `quota`와 각 `storage_class_quotas[]` entry로 역산해 DB desired state 갱신
- namespace 안의 non-DMS ResourceQuota를 read-only 조회해 effective quota warning 기록
- non-DMS ResourceQuota는 DMS delete에서 보존

Live 검증 대상:

- 실제 PostgreSQL: `192.168.56.11:30432`
- 실제 Kubernetes mutation/read: `cluster-a`, `cluster-b`
- 실제 Longhorn multi-StorageClass target: `cluster-b/testbed-longhorn`, `cluster-b/longhorn-static`
- 실제 CephFS single-entry regression target: `cluster-a/testbed-cephfs`

주의:

- DMS lifecycle delete는 여전히 namespace delete가 아니다.
- namespace/PVC cleanup은 verification script cleanup으로만 수행했다.
- DMS Agent DaemonSet은 아직 없으므로 Agent report는 API로 제출한 synthetic evidence다.
- Phase 6 effective quota warning은 check/sync result evidence로 검증했다. Phase 7에서 별도 namespace quota query endpoint로 DB/live/effective warning 조회까지 검증했다.

### Phase 7: Operational Query + Blocked Quota Update Semantics

확실히 구현된 범위:

- `GET /api/v1/operations/requests`에 필수 `requester_id` query parameter 추가
- requester별 request list를 `commit_order DESC` 최신순으로 조회
- optional `limit` query parameter 지원
- repository request list 기본 limit은 1000개
- requester query용 index 추가
  - `idx_requests_requester_commit_order`
- Kubernetes namespace quota dedicated read-only query API 추가
  - `GET /api/v1/operations/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}`
- quota query API가 operational DB resource state와 live Kubernetes `ResourceQuota` state를 함께 반환
- quota query API가 DB desired hard와 live `spec.hard` diff를 `Consistent`, `Drifted`, `Missing`, `LiveOnly`, `DbOnly`, `QueryFailed` 등으로 구조화
- quota query API가 live `status.used` usage summary를 반환
- `include_non_dms=true`일 때 non-DMS `ResourceQuota`와 effective quota warning 반환
- blocked 상태 Kubernetes namespace quota update semantics 보강
  - blocked 상태 update는 허용
  - live hard limit은 계속 `0` 유지
  - unblock 복구 대상인 `block_state.restore_hard`만 최신 quota로 갱신
  - blocked 상태 decrease guard는 zero hard가 아니라 restore target hard를 기준으로 적용
- live verification script 추가
  - `scripts/phase7_operational_query_and_block_update.py`
  - `scripts/verify-phase7-testbed.sh`

Live 검증 대상:

- 실제 PostgreSQL: `192.168.56.11:30432`
- 실제 Kubernetes mutation/read: `cluster-a`, `cluster-b`
- 실제 Longhorn multi-StorageClass target: `cluster-b/testbed-longhorn`, `cluster-b/longhorn-static`
- 실제 CephFS single-entry query target: `cluster-a/testbed-cephfs`

주의:

- DMS Agent DaemonSet은 아직 없으므로 Agent report는 API로 제출한 synthetic evidence다.
- Phase 7 quota query API는 read-only다. Kubernetes object나 DMS DB desired state를 변경하지 않는다.
- `GET /api/v1/operations/requests/{request_id}` 단건 lifecycle history endpoint는 유지된다.

### Phase 8: DMS Agent DaemonSet + Real Agent Evidence

확실히 구현된 범위:

- DMS Agent runtime/prober 추가
  - `src/dms/agent_daemon.py`
  - one-shot report generation
  - loop mode report posting
- CLI 추가
  - `dms agent-probe --once`
  - `dms agent-probe --post`
  - `dms agent-loop`
- Agent가 DB 직접 접근 없이 DMS API로 report 제출
  - `POST /api/v1/agent/reports`
  - `x-dms-actor: node:{cluster_name}:{node_name}`
  - optional `Authorization: Bearer {DMS_AUTH_SHARED_TOKEN}`
- Agent node-local/read-only probe
  - node identity and node UID lookup
  - mount evidence from `/proc/self/mountinfo`
  - Kubernetes `StorageClass`/`CSIDriver`/`Node` read-only evidence
  - tool existence evidence
  - credential file presence evidence without secret content
  - network reachability evidence
  - optional POSIX identity lookup evidence
- Phase 8 Agent report schema
  - `schema_version=phase8.v1`
  - existing Phase 3 ingestion schema와 backward-compatible
  - evidence별 `status`, `reason`, `source`, `checked_at` 기록
- effective inventory normalization 보강
  - `status=Ready` evidence만 readiness candidate로 사용
  - 같은 `(worker_role, cluster_name, node_name)`의 최신 fresh report만 worker role candidate로 사용
  - non-ready Phase 8 evidence는 raw report로는 보존하지만 mapping readiness를 Ready로 만들지 않음
- Kubernetes 배포 산출물 추가
  - `deploy/Dockerfile`
  - `deploy/kubernetes/dms-agent-daemonset.yaml`
  - 기존 placeholder synthetic Agent CronJob 제거
- Phase 8 live verification scripts 추가
  - `scripts/phase8_agent_daemonset_live.py`
  - `scripts/verify-phase8-testbed.sh`

Live 검증 대상:

- 실제 PostgreSQL: `192.168.56.11:30432`
- 실제 DMS API Deployment on `cluster-a`
- 실제 DMS Agent DaemonSet
  - `cluster-a`: RM Agent, DM Agent
  - `cluster-b`: RM Agent
- 실제 Kubernetes read-only probe
  - `cluster-a/testbed-cephfs`
  - `cluster-b/testbed-longhorn`
  - `cluster-b/longhorn-static`
- 실제 Kubernetes quota lifecycle subset
  - CephFS quota create/check/delete
  - Longhorn multi-StorageClass quota create/check/delete

주의:

- Phase 8은 filesystem directory/quota mutation을 구현하지 않는다.
- Phase 8은 Data Management `scan/sync/rm` live execution이나 VolcanoJob execution을 구현하지 않는다.
- Longhorn 계열 storage는 control cluster DM Agent가 실제로 볼 수 없으므로 DM readiness가 `Missing`으로 남는다. 이 상태를 synthetic evidence로 보완하지 않고 action-required에 노출하는 것이 Phase 8의 기대 동작이다.

### Phase 9: Kubernetes Quota Operational Hardening

확실히 구현된 범위:

- Kubernetes namespace quota update에서 `reset_quota_to_default=true` 지원
- `resource_kind=kubernetes_namespace_quota`와 `resource_type` 기준 default quota policy 조회
- default policy가 없거나 policy의 `storage_name` mapping이 target cluster와 맞지 않으면 backend side effect 없이 reject
- reset 결과를 DMS DB desired/applied/observed state와 live `ResourceQuota.spec.hard`에 반영
- blocked resource reset semantics 보강
  - live hard limit은 계속 `0` 유지
  - unblock 시 복구할 `block_state.restore_hard`를 default hard로 갱신
- on-demand audit API 추가
  - `POST /api/v1/resource-management/kubernetes/namespace-quotas:audit`
  - operation kind: `kubernetes.namespace_quota.audit`
  - Kubernetes object를 변경하지 않는 read-only audit
- audit 결과에 DB desired state와 live `ResourceQuota.spec.hard` drift 구조화
- live `ResourceQuota.status.used` 기반 usage pressure 계산
- namespace 내 non-DMS `ResourceQuota`가 effective quota에 미치는 warning 계산
- latest audit/check result 기반 Kubernetes quota action-required aggregation
  - drift/missing/metadata drift/query failed
  - usage warning/critical
  - non-DMS restrictive quota warning
  - 최신 clean audit/check 이후 resolved issue 제거
- DMS-managed `ResourceQuota/dms-storage-quota` metadata hardening
  - `app.kubernetes.io/managed-by=dms`
  - `dms.io/resource-kind=kubernetes_namespace_quota`
  - `dms.io/resource-key=<cluster>:<namespace>`
  - mutation 전에 name, label, annotation ownership 확인
- Phase 9 live verification scripts 추가
  - `scripts/phase9_kubernetes_quota_operational_hardening.py`
  - `scripts/verify-phase9-testbed.sh`

Live 검증 대상:

- 실제 PostgreSQL: `192.168.56.11:30432`
- 실제 DMS API Deployment on `cluster-a`
- 실제 DMS Agent DaemonSet
  - `cluster-a`: RM Agent, DM Agent
  - `cluster-b`: RM Agent
- 실제 Kubernetes mutation/read
  - `cluster-a/testbed-cephfs`
  - `cluster-b/testbed-longhorn`
  - `cluster-b/longhorn-static`
- 실제 Longhorn PVC 생성으로 usage pressure 계산 검증

주의:

- Phase 9의 quota audit은 cron/scheduler/controller가 자동 실행하지 않는다. 운영자 또는 외부 포털이 API로 요청한 경우에만 실행한다.
- Phase 9는 filesystem directory/quota mutation을 구현하지 않는다.
- Phase 9는 Data Management `scan/sync/rm` live execution이나 VolcanoJob execution을 구현하지 않는다.
- Phase 9는 Kubernetes CPU/memory/pod/service/object quota, `LimitRange`, tenant provisioning을 구현하지 않는다.

### Phase 10: Host-Mounted CephFS Filesystem Create/Delete

확실히 구현된 범위:

- filesystem Resource Management create/delete 최소 lifecycle
  - `POST /api/v1/resource-management/filesystems`
  - `DELETE /api/v1/resource-management/filesystems/{storage_name}/{directory_name}`
- Phase 10 filesystem planner validation
  - create/delete만 허용
  - update/block/initialize/check/import/assign-quota/expiration-sweep는 명시적으로 reject
  - `quota`, `capacity_bytes`, `file_count`, `acl`, `rename`, `block`, `check`, `sync` payload field는 backend side effect 전에 reject
  - `storage_name`, `directory_name`, `access_group` safe basename guard
  - create 요청은 최소 2명 이상의 unique user 필요
  - active resource가 이미 있으면 create reject
  - delete는 DMS DB에 존재하는 non-Deleted resource만 plan 생성
- host-mounted CephFS backend adapter
  - `src/dms/backends/cephfs.py`
  - `backend_type=cephfs` storage mapping을 backend registry에서 live adapter로 연결
  - target worker node에서 SSH + structured Python wrapper로 directory create/delete 수행
  - managed root boundary, realpath escape guard, `.dms-resource.json` marker guard
  - DMS marker가 없거나 resource key가 다른 directory는 mutate/delete 거부
- OpenLDAP access group management
  - DMS-managed `posixGroup` 생성
  - requested LDAP users를 `memberUid`로 추가
  - access group name은 `dms-` prefix와 safe basename 필요
  - LDAP user가 없거나 LDAP group precondition이 실패하면 filesystem side effect 전에 `BackendApplyFailed`
- POSIX access boundary
  - directory owner `root`, group DMS access group
  - Phase 10 default mode `0770`
  - 허용 user 2명 이상 write/execute 확인
  - 비허용 LDAP user execute/write 거부 확인
- Phase 10 live verification scripts 추가
  - `scripts/phase10_ceph_host_filesystem_rm.py`
  - `scripts/verify-phase10-testbed.sh`

Live 검증 대상:

- 실제 PostgreSQL: `192.168.56.11:30432`
- 실제 OpenLDAP: `ldap://192.168.56.31`
- 실제 SSSD/NSS on `c1-worker`, `c2-worker`
- 실제 DMS API Deployment on `cluster-a`
- 실제 DMS Agent DaemonSet on Ceph host-mounted worker nodes
  - `cluster-a/c1-worker`: `cephfs-a`, `/mnt/testbed-cephfs`
  - `cluster-b/c2-worker`: `cephfs-b`, `/mnt/testbed-cephfs-c2`
- 실제 worker-node host-mounted CephFS directory create/delete

주의:

- Phase 10은 filesystem quota, update, block/unblock, consistency check/sync를 구현하지 않는다.
- Phase 10은 long-running RM Worker Deployment를 검증하지 않는다. Verification script가 기존 phase와 동일하게 `RMWorkerRuntime.run_once()`를 호출한다.
- Phase 10은 일반 운영 LDAP user account를 create/delete하지 않는다. 단, 검증용 LDAP fixture user가 부족하면 DMS phase-scoped test user를 OpenLDAP에 만들고 검증 후 삭제한다.
- Phase 10은 Data Management `scan/sync/rm` live execution이나 VolcanoJob execution을 구현하지 않는다.

### Phase 11: Filesystem Expiry Sweep and Block/Unblock

확실히 구현된 범위:

- expired/expiring filesystem resource dedicated query API
  - `GET /api/v1/operations/filesystems/expiring`
  - `expires_at` 기준 `expired`, `expiring`, `all` 조회
  - `storage_name`, `before`, `within_seconds`, `include_blocked`, `limit` filter
- API-driven filesystem expiration sweep
  - `POST /api/v1/resource-management/filesystems:expiration-sweep`
  - Phase 11 action은 `block`만 지원
  - `dry_run=true`는 backend side effect 없이 target/skip reason만 result에 기록
  - sweep은 운영자가 API로 요청한 경우에만 실행되고 cron/controller 자동 실행은 없음
- filesystem block/unblock 최소 lifecycle
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:block`
  - `permission-zero` block mode로 DMS-managed directory mode를 `0000`으로 변경
  - block 전에 restore 가능한 group/mode state를 DB `block_state`에 저장
  - unblock은 저장된 restore state를 기준으로 group/mode를 복구
  - DMS marker mismatch 또는 restore state missing은 fail-closed
- expiration/block action-required aggregation
  - expired but unblocked filesystem resource
  - sweep skipped/partial failure
  - block/unblock failure
  - LDAP access group missing
  - DMS marker mismatch
- sweep safety guard
  - `resource_type=user` 또는 기본 일반 resource만 자동 block
  - `resource_type=system`, `resource_type=admin`은 자동 block하지 않고 skip reason 기록
  - already blocked target은 중복 block하지 않음
  - same resource active work가 있으면 skip
- Phase 11 live verification scripts 추가
  - `scripts/phase11_ceph_host_filesystem_expiry.py`
  - `scripts/verify-phase11-testbed.sh`

Live 검증 대상:

- 실제 PostgreSQL: `192.168.56.11:30432`
- 실제 OpenLDAP: `ldap://192.168.56.31`
- 실제 SSSD/NSS on `c1-worker`, `c2-worker`
- 실제 DMS API Deployment on `cluster-a`
- 실제 DMS Agent DaemonSet on Ceph host-mounted worker nodes
  - `cluster-a/c1-worker`: `cephfs-a`, `/mnt/testbed-cephfs`
  - `cluster-b/c2-worker`: `cephfs-b`, `/mnt/testbed-cephfs-c2`
- 실제 worker-node host-mounted CephFS directory create, block, unblock, delete

주의:

- Phase 11은 filesystem quota, update, check/sync, import, assign-quota, usage pressure를 구현하지 않는다.
- Phase 11은 automatic cron/controller expiration sweep을 구현하지 않는다. Sweep은 API 요청으로만 수행한다.
- Phase 11은 long-running RM Worker Deployment를 검증하지 않는다. Verification script가 기존 phase와 동일하게 `RMWorkerRuntime.run_once()`를 호출한다.
- Phase 11은 일반 운영 LDAP user account를 create/delete하지 않는다. 단, 검증용 LDAP fixture user가 부족하면 DMS phase-scoped test user를 OpenLDAP에 만들고 검증 후 삭제한다.
- Phase 11은 Data Management `scan/sync/rm` live execution이나 VolcanoJob execution을 구현하지 않는다.

### Phase 12: Filesystem Quota Lifecycle and Existing Directory Import

확실히 구현된 범위:

- filesystem create with finite quota
  - `quota.capacity_bytes`
  - `quota.file_count`
  - CephFS xattr `ceph.quota.max_bytes`
  - CephFS xattr `ceph.quota.max_files`
  - xattr apply 후 read-back verification
- filesystem quota update
  - quota 증가 apply
  - blocked resource quota update는 desired/applied quota만 갱신하고 block state를 유지
  - quota 감소도 filesystem usage 조회 없이 apply
  - quota apply 후 live xattr read-back으로 requested quota 일치 여부 검증
- filesystem quota check/sync
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:check`
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:sync`
  - check는 read-only로 desired/applied quota와 live xattr quota를 비교
  - sync는 live xattr quota를 DB desired/applied/observed state로 수용하고 xattr를 변경하지 않음
  - filesystem check/sync API는 usage collection payload field를 제공하지 않고 해당 field를 unsupported로 reject
- filesystem quota action-required aggregation
  - `filesystem_quota_drifted`
  - `filesystem_quota_missing`
  - quota/import/assign precondition failure issue
  - successful sync 또는 clean latest check 후 같은 target의 drift issue 해소
- existing directory quota-only assignment
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:assign-quota`
  - unmanaged existing directory에 quota xattr 적용
  - `.dms-resource.json`에 `management_mode=quota_only`
  - quota-only resource delete는 backend directory delete로 이어지지 않도록 planner에서 reject
- existing directory full import
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:import`
  - `adopt_existing_group` access policy 지원
  - OpenLDAP/SSSD group membership, group/mode, marker, quota state 기록
  - import 후 quota update, check/sync 대상이 됨
- non-DMS directory safety guard
  - safe basename validation
  - nested path/path separator reject
  - host script에서 symlink/root escape/marker mismatch fail-closed
- SSSD propagation robustness
  - LDAP group 생성 직후 worker host access validation에서 SSSD cache refresh 및 bounded retry 수행
- Phase 12 live verification scripts 추가
  - `scripts/phase12_cephfs_quota_import.py`
  - `scripts/verify-phase12-testbed.sh`

Live 검증 대상:

- 실제 PostgreSQL: `192.168.56.11:30432`
- 실제 OpenLDAP: `ldap://192.168.56.31`
- 실제 SSSD/NSS on `c1-worker`, `c2-worker`
- 실제 DMS API Deployment on `cluster-a`
- 실제 DMS Agent DaemonSet on Ceph host-mounted worker nodes
  - `cluster-a/c1-worker`: `cephfs-a`, `/mnt/testbed-cephfs`
  - `cluster-b/c2-worker`: `cephfs-b`, `/mnt/testbed-cephfs-c2`
- 실제 worker-node host-mounted CephFS directory quota xattr apply/enforcement
- 실제 OpenLDAP group membership 기반 full import access boundary

주의:

- Phase 12는 filesystem expiry 자동 cron/scheduler/controller를 구현하지 않는다.
- Phase 12는 filesystem usage pressure 계산 또는 대용량 directory recursive usage scan을 구현하지 않는다.
- Phase 12는 long-running RM Worker Deployment loop를 검증하지 않는다. Verification script가 기존 phase와 동일하게 `RMWorkerRuntime.run_once()`를 호출한다.
- Phase 12는 Data Management `scan/sync/rm` live execution이나 VolcanoJob execution을 구현하지 않는다.
- Phase 12는 GPFS/WekaFS/Lustre live adapter를 구현하지 않는다. Backend-neutral quota/import 모델과 CephFS live adapter만 검증했다.
- Phase 12 verifier 첫 시도에서 `c1-worker`에 Debian `attr` 패키지를 설치했고 `/home/mason/workspace/testbed/dms-phase12-testbed-notes.md`에 기록했다. 최종 성공 run에서는 `setfattr`/`getfattr`가 양 worker node에 이미 존재했다.

### Phase 13: Long-Running RM Worker Runtime and GPFS Command Adapter

확실히 구현된 범위:

- Kubernetes long-running Resource Management runtime
  - `dms planner --loop` Deployment
  - `dms rm-worker --loop` Deployment
  - RM Worker Pod별 `worker_id`를 Pod name으로 설정
  - RM Worker loop가 settings-aware `BackendAdapterRegistry.with_live_defaults(repository, settings)`를 사용
  - loop iteration exception은 JSON stderr logging 후 다음 iteration으로 계속 진행
- 실제 RM Worker Deployment 경유 Phase 12 filesystem 기능 처리
  - API request만 제출하고 verifier는 `Planner.run_once()` 또는 `RMWorkerRuntime.run_once()`를 호출하지 않음
  - CephFS quota create/update/decrease/check/sync/action-required
  - existing directory quota-only assign
  - existing directory full import
  - unsafe path planning reject
- worker scale/restart/stale claim safety
  - 2 replica RM Worker Deployment에서 서로 다른 Pod가 check request를 처리
  - `plans.status='Planned'` 조건부 atomic update로 동일 plan 중복 claim 방지
  - Pod delete 후 replacement Pod가 올라오고 state가 유지됨
  - 수동 expired lease fixture가 `StaleClaim`과 stale run query로 노출됨
- deploy image/runtime 보강
  - RM Worker image에 `openssh-client` 포함
  - Docker image install extra가 PostgreSQL/LDAP dependency를 포함하도록 보강
  - Phase 13 verifier가 API, Agent, Planner, RM Worker manifest를 직접 배포
- IBM GPFS / IBM Storage Scale fileset command adapter
  - `backend_type=gpfs` filesystem mapping이 더 이상 성공 stub로 처리되지 않음
  - `mmcrfileset`, `mmlinkfileset`, `mmlsfileset`, `mmsetquota`, `mmlsquota`, `mmunlinkfileset`, `mmdelfileset` command runner abstraction
  - local/SSH/fake executor 구조
  - fileset-backed create/link/quota/read-back/check/sync/import/assign-quota/delete flow
  - parseable `-Y` output parser
  - quota disabled, per-fileset quota disabled, command missing, unsafe fileset/path, read-back mismatch fail-closed
  - GPFS Kubernetes namespace quota CSI mapping skeleton 유지
- Phase 13 live verification scripts 추가
  - `scripts/phase13_long_running_rm_worker.py`
  - `scripts/verify-phase13-testbed.sh`

Live 검증 대상:

- 실제 PostgreSQL: `192.168.56.11:30432`
- 실제 OpenLDAP: `ldap://192.168.56.31`
- 실제 SSSD/NSS on `c1-worker`, `c2-worker`
- 실제 DMS API Deployment on `cluster-a`
- 실제 DMS Agent DaemonSet on Ceph host-mounted worker nodes
  - `cluster-a/c1-worker`: `cephfs-a`, `/mnt/testbed-cephfs`
  - `cluster-b/c2-worker`: `cephfs-b`, `/mnt/testbed-cephfs-c2`
- 실제 Planner Deployment and RM Worker Deployment on Kubernetes
- 실제 worker-node host-mounted CephFS quota/import/check/sync side effects

주의:

- Phase 13은 Data Management `scan/sync/rm` live execution이나 VolcanoJob execution을 구현하지 않는다.
- Phase 13은 automatic cron/controller expiration sweep 또는 quota drift sweep을 구현하지 않는다.
- Phase 13은 production Helm/Kustomize chart를 완성하지 않는다. 검증용 manifest를 verifier script가 생성한다.
- Phase 13은 GPFS live backend를 테스트베드에서 검증하지 않는다. 테스트베드에 IBM GPFS / IBM Storage Scale cluster가 없으므로 fake executor unit tests와 skip evidence로 문서화했다.
- Phase 13은 GPFS user/group quota를 DMS filesystem quota로 일반화하지 않는다. Fileset quota만 지원한다.

### Phase 14: Runtime Hardening Before Data Management

확실히 구현된 범위:

- Observability safe write boundary
  - `ObservabilityRepository.safe_record_event()` 추가
  - API auth rejection, storage mapping sanity event, Agent report event, RM/DM Worker diagnostic event call path를 safe wrapper로 전환
  - diagnostic event write failure는 process warning log로 남기고 caller의 lifecycle result를 바꾸지 않음
  - operational DB write failure는 hard failure로 유지
- Backend registry fail-closed
  - live runtime helper `BackendAdapterRegistry.with_live_defaults(repository, settings)` 추가
  - test/dev stub helper `BackendAdapterRegistry.with_test_stubs(...)` 명시
  - unknown filesystem or Kubernetes quota backend type은 live registry에서 stub success로 fallback하지 않고 `BackendPreconditionError`로 실패
  - RM Worker는 backend precondition failure를 `BackendApplyFailed` result와 action-required 대상으로 기록
  - unsupported backend issue detail은 `unsupported_backend`으로 노출
- Kubernetes quota live adapter wiring
  - `dms rm-worker --loop` path가 settings-aware live registry를 사용
  - generic Kubernetes quota backends(`cephfs`, `longhorn`)는 `KubernetesNamespaceQuotaLiveAdapter.from_settings(settings)` 경로 사용
  - Kubernetes quota operation에서는 filesystem adapter를 먼저 고르지 않도록 lazy adapter selection 적용
  - GPFS filesystem fileset quota adapter와 GPFS Kubernetes namespace quota CSI adapter path를 분리 유지
- Phase 14 verification scripts 추가
  - `scripts/phase14_runtime_hardening.py`
  - `scripts/verify-phase14-testbed.sh`

Live 검증 대상:

- 실제 PostgreSQL: `192.168.56.11:30432`
- 실제 DMS API Deployment on `cluster-a`
- 실제 DMS Agent DaemonSet on both clusters
- 실제 Planner Deployment and RM Worker Deployment on Kubernetes
- 실제 Longhorn ResourceQuota side effect on `cluster-b/testbed-longhorn`
- observability DB diagnostic table removal 후 API/RM Worker lifecycle 유지
- ready mapping의 typo backend `cephfss` fail-closed/action-required

주의:

- Phase 14는 Data Management `scan/sync/rm` live execution이나 VolcanoJob execution을 구현하지 않는다.
- Phase 14는 worker lease heartbeat renewal 또는 maintenance/drain enforcement를 구현하지 않는다.
- Phase 14는 production Helm/Kustomize chart를 완성하지 않는다. 검증용 manifest는 verifier script가 생성한다.
- Phase 14는 GPFS live backend를 테스트베드에서 검증하지 않는다.

### Phase 15: Resource Expiry Update, Import Defaults, and Kubernetes Namespace Quota Expiry Lifecycle

확실히 구현된 범위:

- Shared expiry field policy
  - public API/DB/query response field를 `expires_at`으로 통일
  - `expiry_at`과 `clear_expires_at` payload는 unsupported field로 reject
  - timezone-aware ISO-8601과 future timestamp validation 적용
- Filesystem expiry semantics
  - create request의 `expires_at` 필수화
  - update request의 optional `expires_at` 변경 지원
  - update에서 `expires_at` 생략 시 기존 desired expiry 보존
  - import request의 optional `expires_at` 지원
  - import에서 `expires_at` 생략 시 Planner 기준 server-side now + 365일 default 설정
- Kubernetes namespace quota expiry semantics
  - create request의 `expires_at` 필수화
  - update/default-reset request의 optional `expires_at` 변경 및 생략 시 보존 지원
  - DMS-owned `ResourceQuota/dms-storage-quota` import/adoption operation 추가
  - import에서 `expires_at` 생략 시 Planner 기준 server-side now + 365일 default 설정
  - live `ResourceQuota` manifest annotation `dms.io/expires-at` 반영
- Kubernetes namespace quota expiry operations
  - `GET /api/v1/operations/kubernetes/namespace-quotas/expiring`
  - expired but unblocked quota의 `action-required` issue `kubernetes_quota_expired_unblocked`
  - `POST /api/v1/resource-management/kubernetes/namespace-quotas:expiration-sweep`
  - sweep은 live `ResourceQuota.spec.hard`를 zero hard로 변경하고 기존 hard를 `block_state.restore_hard`로 보존
  - `resource_type=system/admin`, already blocked, active work target skip 처리
- Phase 15 verification scripts 추가
  - `scripts/phase15_resource_expiry.py`
  - `scripts/verify-phase15-testbed.sh`

Live 검증 대상:

- 실제 PostgreSQL: `192.168.56.11:30432`
- 실제 OpenLDAP/SSSD on `c1-worker`
- 실제 DMS API Deployment on `cluster-a`
- 실제 DMS Agent DaemonSet on Ceph host-mounted worker nodes
- 실제 Planner Deployment and RM Worker Deployment on Kubernetes
- 실제 `cluster-a/c1-worker` host-mounted CephFS filesystem create/update side effect
- 실제 `cluster-b/testbed-longhorn` Kubernetes `ResourceQuota/dms-storage-quota` create/update/import/sweep side effect

주의:

- Phase 15는 filesystem/Kubernetes expiry 자동 cron/scheduler/controller를 구현하지 않는다.
- Phase 15는 filesystem expiry delete/archive 정책을 구현하지 않는다.
- Phase 15는 Data Management `scan/sync/rm` live execution이나 VolcanoJob execution을 구현하지 않는다.
- Phase 15는 production Helm/Kustomize chart를 완성하지 않는다. 검증용 manifest는 verifier script가 생성한다.
- Phase 15는 GPFS live backend를 테스트베드에서 검증하지 않는다.

### Phase 17: Kubernetes ResourceQuota Live Adapter Unification

확실히 구현된 범위:

- Kubernetes namespace quota adapter selection
  - `BackendAdapterRegistry.kubernetes_for_plan()`이 backend type allowlist나 GPFS special case 없이 configured Kubernetes adapter를 반환한다.
  - live registry는 `KubernetesNamespaceQuotaLiveAdapter.from_settings(settings)`를 사용한다.
  - test/dev registry는 명시적으로 `StubKubernetesNamespaceQuotaAdapter`를 사용할 수 있다.
  - `GpfsKubernetesNamespaceQuotaAdapter`와 `GENERIC_KUBERNETES_QUOTA_BACKENDS`는 제거됐다.
- GPFS CSI ResourceQuota routing
  - GPFS CSI namespace quota create는 `gpfs-kubernetes-quota-stub`이 아니라 `adapter=kubernetes-namespace-quota-live` result를 기록한다.
  - GPFS filesystem resource는 계속 IBM Storage Scale `mm*` command adapter를 사용한다.
  - Kubernetes namespace quota path에서는 GPFS `mm*` command를 실행하지 않는다.
- Future CSI backend behavior
  - WEKA/future CSI StorageClass mapping은 Kubernetes namespace quota operation에서 공통 live ResourceQuota adapter를 사용할 수 있다.
  - unknown filesystem backend는 기존 Phase 14 fail-closed 원칙을 유지한다.
- Inventory sanity
  - GPFS default CSI driver를 `spectrumscale.csi.ibm.com`으로 추가했다.
- Regression coverage
  - GPFS CSI, unknown/future CSI backend, mixed CephFS+GPFS+WEKA StorageClass quota, GPFS default CSI sanity를 테스트했다.

Live 검증 대상:

- 실제 `cluster-b` Kubernetes API
- temporary `CSIDriver/spectrumscale.csi.ibm.com`
- temporary `StorageClass/dms-phase17-gpfs-csi`
- DMS local SQLite + Planner + RMWorkerRuntime + live `ssh-kubectl` adapter
- 실제 `ResourceQuota/dms-storage-quota` apply/read-back on namespace `dms-phase17-gpfs-quota`

주의:

- Phase 17은 실제 IBM Storage Scale / GPFS CSI PVC provisioning을 검증하지 않는다. 테스트베드에 GPFS cluster가 없으므로 synthetic GPFS CSI StorageClass로 Kubernetes ResourceQuota path만 검증했다.
- Phase 17은 GPFS filesystem fileset quota behavior를 변경하지 않는다.
- Phase 17은 Data Management `scan/sync/rm` live execution이나 VolcanoJob execution을 구현하지 않는다.

### Phase 18: Operational Maintenance, Drain, and Recovery Guard

확실히 구현된 범위:

- Control state API
  - `GET /api/v1/operations/control-state`
  - `POST /api/v1/operations/control-state:enter-maintenance`
  - `POST /api/v1/operations/control-state:begin-drain`
  - `POST /api/v1/operations/control-state:resume`
  - control state mutation은 `control_mutations`에 audit record를 남긴다.
- Maintenance/drain runtime enforcement
  - maintenance/drain/scheduling-blocked 상태에서는 새 Resource Management/Data Management request intake를 409로 거부한다.
  - storage mapping, default quota policy, identity mapping, agent report 같은 non-control mutating API도 maintenance 중 거부한다.
  - operational query/control endpoint는 maintenance/drain 중에도 동작한다.
  - `DmsRepository.claim_plan()`이 transaction 내부에서 scheduling block을 다시 확인한다.
  - RM/DM worker loop가 scheduling block 상태에서는 새 plan을 claim하지 않는다.
- Drain/readiness and recovery query
  - `GET /api/v1/operations/drain-status`
  - `POST /api/v1/operations/runs:mark-stale`
  - `GET /api/v1/operations/work-summary`
  - `GET /api/v1/operations/plans/active`
  - `GET /api/v1/operations/runs/active`
- Long-running worker lease heartbeat
  - RM/DM worker는 backend call 중 `RunHeartbeat`로 `heartbeat_run()`을 주기 호출해 `lease_expires_at`을 갱신한다.
  - heartbeat 실패는 observability warning으로만 남기고 backend operation 결과를 실패시키지 않는다.
- Stale/recovery guard
  - expired `Claimed` run은 `StaleClaim`으로 표시된다.
  - expired `Running`/`Applying`/`Verifying` run은 `RecoveryNeeded`로 표시된다.
  - stale/recovery work는 자동 재실행하지 않고 action-required/operator review 대상으로 남긴다.
- Install/runbook automation
  - `install/scripts/dms-planned-shutdown.sh`
  - `install/scripts/dms-startup-recovery-check.sh`
  - `install/scripts/dms-resume.sh`
  - `install/scripts/verify-install.sh`가 control/work/drain/stale query를 확인한다.
  - `install/README.md`, `install/RUNBOOK.md`, `install/CONFIGURATION.md`가 Phase 18 절차와 endpoint를 설명한다.

검증 대상:

- Local regression: `tests/test_phase18_operational_controls.py`
- mTLS protected endpoint matrix: `tests/test_phase16_mtls_auth.py`에 Phase 18 endpoint 추가
- Shell syntax: Phase 18 install scripts

주의:

- Phase 18은 Kubernetes node drain/reboot controller를 구현하지 않는다. DMS는 drain readiness와 worker scale helper만 제공한다.
- Phase 18은 stale/recovery run을 자동 requeue하지 않는다.
- Phase 18은 Data Management `scan/sync/rm` live execution이나 VolcanoJob execution을 구현하지 않는다.

## Live Verification Results

### LDAP Direct Lookup

Command:

```bash
cd /home/mason/workspace/dms
DMS_LDAP_URI=ldap://192.168.56.31 \
DMS_LDAP_BASE_DN=dc=testbed,dc=local \
DMS_LDAP_BIND_DN=cn=admin,dc=testbed,dc=local \
DMS_LDAP_BIND_PASSWORD=testbed-admin \
DMS_LDAP_USER_SEARCH_BASE=ou=people,dc=testbed,dc=local \
DMS_LDAP_GROUP_SEARCH_BASE=ou=groups,dc=testbed,dc=local \
/tmp/dms-phase3-venv/bin/python - <<'PY'
import json
from dms.config import Settings
from dms.adapters import LdapIdentityLookupAdapter

settings = Settings.from_env()
adapter = LdapIdentityLookupAdapter.from_settings(settings)
for username in ["alice", "bob"]:
    result = adapter.lookup("ldap-main", username)
    print(json.dumps({
        "provider": result.provider,
        "posix_username": result.posix_username,
        "uid": result.uid,
        "primary_gid": result.primary_gid,
        "groups": result.groups,
        "adapter": result.source_metadata.get("adapter"),
        "user_dn": result.user_dn,
    }, sort_keys=True))
PY
```

Output:

```json
{"adapter": "ldap3-direct", "groups": ["developers", "k8s-admins"], "posix_username": "alice", "primary_gid": 10000, "provider": "ldap-main", "uid": 10000, "user_dn": "uid=alice,ou=people,dc=testbed,dc=local"}
{"adapter": "ldap3-direct", "groups": ["developers"], "posix_username": "bob", "primary_gid": 10000, "provider": "ldap-main", "uid": 10001, "user_dn": "uid=bob,ou=people,dc=testbed,dc=local"}
```

### Phase 2 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase2-testbed.sh
```

Output:

```json
{
  "alice_mapping_status": "Active",
  "bob_mapping_status_after_disable_refresh": "Disabled",
  "data_request_id": "req_d739db6a90c64c93a601bf48737190a4",
  "filesystem_request_id": "req_8bfe256e16cc404cbabe20f4cbca15fc",
  "ldap_uri": "ldap://192.168.56.31",
  "mismatch_mapping_status": "NeedsReview",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase2_obs_20260528073411",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase2_20260528073411",
  "status": "ok",
  "token": "68cc1a38"
}
```

검증 의미:

- 새 PostgreSQL DB가 생성됐다.
- migrations가 PostgreSQL에 적용됐다.
- operational DB와 observability DB가 분리됐다.
- LDAP direct lookup으로 `alice`, `bob` mapping을 만들었다.
- expected UID mismatch는 `NeedsReview`가 됐다.
- `bob` disable 후 refresh가 `Disabled`를 유지했다.

주의:

- 이 command 안의 RM/DM lifecycle 확인은 현재 구현된 lifecycle persistence 검증이다.
- 실제 filesystem mutation이나 실제 VolcanoJob 실행 검증이 아니다.

### Phase 3 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase3-testbed.sh
```

Output:

```json
{
  "action_required_issue_types": [
    "agent_report_stale",
    "csi_driver_mismatch",
    "missing_dm_readiness",
    "missing_rm_readiness",
    "storage_class_missing",
    "storage_mapping_failed"
  ],
  "active_mapping_update_conflict": {
    "id": "req_52548c4820dd42ada66132f54f5fd759",
    "kind": "request",
    "status": "Planned"
  },
  "cephfs_mapping_status": "Ready",
  "cluster_a_storage_class": "testbed-cephfs",
  "cluster_b_storage_class": "testbed-longhorn",
  "control_cluster_name": "cluster-a",
  "csi_mismatch_status": "Failed",
  "failed_mapping_request_status": "Rejected",
  "kubernetes_inventory_mode": "ssh-kubectl",
  "longhorn_mapping_status_before_mismatch_check": "Ready",
  "missing_mapping_status": "Failed",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase3_obs_20260528073419",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase3_20260528073419",
  "ready_scan_job_worker_pool": {
    "candidates": [
      {
        "filesystem_type": "posix",
        "mount_path": "/mnt/dms/cephfs-a",
        "node_name": "c1-worker",
        "readable": true,
        "storage_name": "cephfs-a",
        "writable": true
      },
      {
        "driver": "rook-ceph.cephfs.csi.ceph.com",
        "node_name": "c1-worker",
        "node_plugin_ready": true,
        "storage_classes": [
          "testbed-cephfs"
        ]
      }
    ],
    "readiness": {
      "data_management": "Ready",
      "inventory": "Ready",
      "resource_management": "Ready"
    },
    "required_mounts": [
      "cephfs-a"
    ],
    "sanity_status": "Ready",
    "selection": "agent-inventory"
  },
  "status": "ok"
}
```

검증 의미:

- 실제 `cluster-a` Kubernetes API에서 `testbed-cephfs` StorageClass를 읽었다.
- 실제 `cluster-b` Kubernetes API에서 `testbed-longhorn` StorageClass를 읽었다.
- `ssh-kubectl` read-only inventory mode가 동작했다.
- `cephfs-a -> cluster-a/testbed-cephfs` mapping이 `Ready`가 됐다.
- `longhorn-b -> cluster-b/testbed-longhorn` mapping이 deliberate mismatch 전에는 `Ready`가 됐다.
- missing StorageClass mapping은 `Failed`가 됐다.
- CSI driver mismatch mapping은 `Failed`가 됐다.
- failed mapping을 참조한 DM scan request는 `Rejected`가 됐고 plan을 만들지 않았다.
- 정상 mapping을 참조한 DM scan request는 Agent inventory 기반 worker pool을 기록했다.
- active planned work가 있는 `storage_name` mapping update는 409 conflict로 막혔다.
- stale Agent report와 failed mapping issue가 action-required에 노출됐다.

주의:

- Phase 3은 backend mutation을 하지 않는 phase다.
- 이 검증은 Kubernetes read-only inventory와 operational DB decision을 검증한다.
- 실제 DMS Agent daemon이 node-local mount/tool을 probe한 결과는 아직 아니다.

### Phase 4 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase4-testbed.sh
```

Output:

```json
{
  "cleanup_namespace_requested": true,
  "cluster_name": "cluster-b",
  "namespace_name": "dms-phase4-ade9c49f",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase4_obs_20260528094541",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase4_20260528094541",
  "request_status": "Succeeded",
  "resource_quota_name": "dms-storage-quota",
  "resource_quota_spec_hard": {
    "persistentvolumeclaims": "2",
    "requests.storage": "128Mi",
    "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "128Mi"
  },
  "resource_quota_status_used_after_allowed_pvc": {
    "persistentvolumeclaims": "1",
    "requests.storage": "64Mi",
    "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "64Mi"
  },
  "resource_status": "Succeeded",
  "result_terminal_status": "Succeeded",
  "status": "ok",
  "storage_class_name": "testbed-longhorn",
  "storage_name": "longhorn-b"
}
```

PVC admission output:

```json
{
  "allowed_pvc": {
    "name": "phase4-allowed-64mi",
    "phase": "Bound",
    "request": "64Mi"
  },
  "over_quota_pvc": {
    "name": "phase4-over-quota-96mi",
    "rejected": true,
    "request": "96Mi",
    "returncode": 1,
    "stderr": "Error from server (Forbidden): persistentvolumeclaims \"phase4-over-quota-96mi\" is forbidden: exceeded quota: dms-storage-quota"
  }
}
```

검증 의미:

- 실제 `cluster-b` Kubernetes API에 namespace와 `ResourceQuota/dms-storage-quota`가 적용됐다.
- `ResourceQuota.spec.hard`와 `status.hard`는 `128Mi`, PVC count `2`, Longhorn StorageClass-specific quota `128Mi`로 확인됐다.
- 64Mi Longhorn PVC는 `Bound`가 됐다.
- 추가 96Mi PVC는 현재 사용량 64Mi와 hard limit 128Mi 때문에 Kubernetes admission에서 거부됐다.
- operational PostgreSQL resource observed state에 ResourceQuota read-back과 PVC admission verification이 저장됐다.
- observability PostgreSQL에 `kubernetes_resourcequota_apply_started`, `kubernetes_resourcequota_apply_completed`, `pvc_admission_verification_completed` event가 기록됐다.

### Phase 5 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase5-testbed.sh
```

Output summary:

```json
{
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase5_obs_20260528155705",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase5_20260528155705",
  "status": "ok",
  "targets": [
    {
      "target": "cephfs",
      "cluster_name": "cluster-a",
      "storage_class_name": "testbed-cephfs",
      "provisioner": "rook-ceph.cephfs.csi.ceph.com",
      "decrease_guard_status": "Rejected",
      "blocked_pvc_rejected": true,
      "drift_check_status": "Drifted",
      "delete_resource_status": "Deleted",
      "non_dms_quota_preserved": true,
      "synced_resource_quota_hard": {
        "persistentvolumeclaims": "5",
        "requests.storage": "384Mi",
        "testbed-cephfs.storageclass.storage.k8s.io/requests.storage": "384Mi"
      }
    },
    {
      "target": "longhorn",
      "cluster_name": "cluster-b",
      "storage_class_name": "testbed-longhorn",
      "provisioner": "driver.longhorn.io",
      "decrease_guard_status": "Rejected",
      "blocked_pvc_rejected": true,
      "drift_check_status": "Drifted",
      "delete_resource_status": "Deleted",
      "non_dms_quota_preserved": true,
      "synced_resource_quota_hard": {
        "persistentvolumeclaims": "5",
        "requests.storage": "384Mi",
        "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "384Mi"
      }
    }
  ]
}
```

검증 의미:

- 실제 `cluster-a` Kubernetes API에서 `testbed-cephfs` ResourceQuota lifecycle을 검증했다.
- 실제 `cluster-b` Kubernetes API에서 `testbed-longhorn` ResourceQuota lifecycle을 검증했다.
- 두 target 모두 128Mi create, 256Mi update, 32Mi decrease guard reject, block/unblock, manual drift check, sync from live, delete를 통과했다.
- block 상태의 신규 PVC admission은 두 target 모두에서 거부됐다.
- delete는 DMS-managed `ResourceQuota/dms-storage-quota`만 삭제했고 같은 namespace의 non-DMS ResourceQuota는 보존했다.
- sync from live 후 operational PostgreSQL desired state에 live hard limit `384Mi`, PVC count `5`가 유지됐다.

상세 검증 기록:

- `docs/dms-phase5-verification.md`

### Phase 6 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase6-testbed.sh
```

Output summary:

```json
{
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase6_obs_20260528211617",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase6_20260528211617",
  "status": "ok",
  "targets": [
    {
      "target": "longhorn-multi-storageclass",
      "cluster_name": "cluster-b",
      "storage_names": ["longhorn-b", "longhorn-static-b"],
      "storage_class_names": ["testbed-longhorn", "longhorn-static"],
      "decrease_guard_status": "Rejected",
      "blocked_pvc_rejected": true,
      "drift_check_status": "Drifted",
      "effective_warning_types": ["non_dms_quota_more_restrictive"],
      "delete_resource_status": "Deleted",
      "non_dms_quota_preserved": true,
      "synced_resource_quota_hard": {
        "longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims": "4",
        "longhorn-static.storageclass.storage.k8s.io/requests.storage": "256Mi",
        "persistentvolumeclaims": "8",
        "requests.storage": "768Mi",
        "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "4",
        "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "512Mi"
      }
    },
    {
      "target": "cephfs-single-storageclass-regression",
      "cluster_name": "cluster-a",
      "storage_name": "cephfs-a",
      "storage_class_name": "testbed-cephfs",
      "check_status": "Consistent",
      "delete_resource_status": "Deleted"
    }
  ]
}
```

검증 의미:

- 실제 `cluster-b` Kubernetes API에서 하나의 namespace에 `testbed-longhorn`과 `longhorn-static` StorageClass별 quota를 함께 적용했다.
- StorageClass-specific storage/PVC count hard key가 모두 렌더링되고 live `status.used`로 반영됐다.
- `longhorn-static` quota만 초과하는 PVC admission reject를 확인했다.
- StorageClass-specific decrease guard가 backend side effect 없이 `Rejected` 됐다.
- block/unblock이 namespace-wide 및 StorageClass-specific hard key 전체에 적용됐다.
- manual drift check가 key별 issue를 기록했다.
- non-DMS ResourceQuota가 DMS보다 더 restrictive한 effective quota warning으로 기록됐다.
- sync from live 후 operational PostgreSQL desired state에 multi-entry hard limit이 보존됐다.
- DMS delete는 `dms-storage-quota`만 삭제했고 non-DMS ResourceQuota는 보존했다.
- `cluster-a/testbed-cephfs` single-entry lifecycle regression도 통과했다.

상세 검증 기록:

- `docs/dms-phase6-verification.md`

### Phase 7 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase7-testbed.sh
```

Output summary:

```json
{
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase7_obs_20260528223441",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase7_20260528223441",
  "request_query": {
    "requester_id": "portal:phase7-a",
    "limited_resource_keys": [
      "phase7-a:cf012d71:2",
      "phase7-a:cf012d71:1"
    ],
    "missing_requester_status": 422
  },
  "status": "ok",
  "targets": [
    {
      "target": "longhorn-query-blocked-update",
      "cluster_name": "cluster-b",
      "namespace_name": "dms-phase7-longhorn-cf012d71",
      "initial_query_status": "Consistent",
      "blocked_decrease_status": "Rejected",
      "drift_query_status": "Drifted",
      "effective_warning_types": ["non_dms_quota_more_restrictive"],
      "missing_query_status": "Missing",
      "non_dms_quota_preserved": true,
      "blocked_query_restore_hard": {
        "longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims": "5",
        "longhorn-static.storageclass.storage.k8s.io/requests.storage": "384Mi",
        "persistentvolumeclaims": "10",
        "requests.storage": "1Gi",
        "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "5",
        "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "512Mi"
      }
    },
    {
      "target": "cephfs-quota-query",
      "cluster_name": "cluster-a",
      "namespace_name": "dms-phase7-cephfs-cf012d71",
      "query_status": "Consistent"
    }
  ]
}
```

검증 의미:

- `GET /api/v1/operations/requests`가 필수 `requester_id`를 요구하고, 지정 requester의 최신 request만 반환했다.
- `cluster-b` Longhorn multi-StorageClass quota에 대해 dedicated quota query API가 DB/live `Consistent`를 반환했다.
- blocked 상태 update가 live hard를 계속 `0`으로 유지하면서 `block_state.restore_hard`를 최신 quota로 갱신했다.
- blocked 상태 decrease guard가 restore target 기준으로 backend side effect 없이 `Rejected` 됐다.
- unblock 후 live `ResourceQuota.spec.hard`가 block 중 update한 최신 restore target으로 복구됐다.
- manual drift가 quota query API에서 `Drifted`로 노출됐다.
- non-DMS ResourceQuota가 DMS보다 더 restrictive한 effective quota warning으로 노출됐다.
- DMS delete 이후 quota query API가 `Missing`을 반환했고 non-DMS ResourceQuota는 보존됐다.
- `cluster-a/testbed-cephfs` single StorageClass quota query도 `Consistent`로 통과했다.

상세 검증 기록:

- `docs/dms-phase7-verification.md`

### Phase 8 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
DMS_PHASE8_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase8-testbed.sh
```

Image build/push was verified with the same script before the final run. The host Docker daemon used the script's `docker save` + `skopeo copy` fallback for the testbed HTTP registry.

Output summary:

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
  "identity_mismatch": {"status_code": 403},
  "storage_mappings": [
    {
      "storage_name": "cephfs-a",
      "storage_class_name": "testbed-cephfs",
      "status": "Ready",
      "readiness": {
        "resource_management": "Ready",
        "data_management": "Ready",
        "inventory": "Ready"
      }
    },
    {
      "storage_name": "longhorn-b",
      "storage_class_name": "testbed-longhorn",
      "status": "Degraded",
      "readiness": {
        "resource_management": "Ready",
        "data_management": "Missing",
        "inventory": "Ready"
      }
    },
    {
      "storage_name": "longhorn-static-b",
      "storage_class_name": "longhorn-static",
      "status": "Degraded",
      "readiness": {
        "resource_management": "Ready",
        "data_management": "Missing",
        "inventory": "Ready"
      }
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

- 테스트베드 local registry에 `dms:phase8` image를 push했다. host Docker가 HTTP registry를 HTTPS로 push하려 해 실패한 경우 verifier가 `docker save`와 `c1-control`의 `skopeo copy --dest-tls-verify=false` fallback으로 registry push를 완료했다.
- 실제 `cluster-a`에 DMS API Deployment와 NodePort service를 배포했다.
- 실제 `cluster-a`에 RM/DM Agent DaemonSet을, `cluster-b`에 RM Agent DaemonSet을 배포했다.
- Agent Pod가 `schema_version=phase8.v1` report를 DMS API에 제출했고 operational PostgreSQL에 Fresh report로 저장됐다.
- Agent actor mismatch가 `403`으로 거부되고 observability event가 기록됐다.
- `cluster-a/testbed-cephfs`, `cluster-b/testbed-longhorn`, `cluster-b/longhorn-static` mapping의 RM readiness가 실제 Agent report 기반으로 `Ready`가 됐다.
- synthetic Agent report 없이 CephFS quota create/check/delete와 Longhorn multi-StorageClass quota create/check/delete subset을 실제 Kubernetes API에서 검증했다.
- Longhorn 계열 DM readiness `Missing`은 control cluster DM Agent가 Longhorn StorageClass를 실제로 볼 수 없다는 의미이며, action-required에 노출됐다.
- 실제 Phase 8 Agent reports 6개를 stale 처리하고 action-required에서 `agent_report_stale` 노출을 확인했다.

상세 검증 기록:

- `docs/dms-phase8-verification.md`

### Phase 9 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
DMS_PHASE9_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase9-testbed.sh
```

Output:

```json
{
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase9_obs_20260529115152",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase9_20260529115152",
  "phase8_reports": {
    "cluster-a:DM": {
      "node_name": "c1-worker",
      "report_id": "agent_dc8d5b05c56b4044a35b0b717d533268",
      "reported_at": "2026-05-29T02:51:58.364946+00:00"
    },
    "cluster-a:RM": {
      "node_name": "c1-control",
      "report_id": "agent_2bab07d2b82c4b43a15f8476f63b58ab",
      "reported_at": "2026-05-29T02:51:58.286588+00:00"
    },
    "cluster-b:RM": {
      "node_name": "c2-worker",
      "report_id": "agent_e55b355e42124f298edfc5faa125ac7c",
      "reported_at": "2026-05-29T02:51:59.036383+00:00"
    }
  },
  "status": "ok",
  "targets": [
    {
      "audit_request_ids": [
        "req_c08bde6af3d1477eb2756713e99ca14b",
        "req_c0a9b19c99c3407e9596c8e6adef1ded",
        "req_c9ea90ff189540499889651ac6e8c3fb"
      ],
      "create_request_id": "req_a3ff1a07d5184f2d90a1ae4f748f5a6b",
      "default_policy_id": "kubernetes_namespace_quota:user",
      "namespace": "dms-phase9-longhorn-e2326416",
      "reset_request_id": "req_8c25437a0737469082d604c83edc2aad",
      "target": "longhorn-multi"
    },
    {
      "audit_request_id": "req_6d909a6192d14e76a0189d747966f837",
      "create_request_id": "req_17b6e2de4b0145298cf92db720db6a77",
      "default_policy_id": "kubernetes_namespace_quota:ceph-user",
      "namespace": "dms-phase9-cephfs-e2326416",
      "reset_request_id": "req_a6ff77d2042942248940528e9444a28c",
      "target": "cephfs"
    }
  ]
}
```

검증 의미:

- 실제 `cluster-a`에 DMS API Deployment와 NodePort service를 배포했다.
- 실제 `cluster-a`에 RM/DM Agent DaemonSet을, `cluster-b`에 RM Agent DaemonSet을 배포했다.
- synthetic Agent report 없이 Phase 8 Agent DaemonSet report를 storage mapping readiness evidence로 사용했다.
- `cluster-b/testbed-longhorn` + `cluster-b/longhorn-static` multi-StorageClass quota에서 default reset, block 중 reset, unblock restore, drift audit/action-required, clean audit issue resolution, usage pressure, non-DMS effective warning, metadata drift를 검증했다.
- `cluster-a/testbed-cephfs` single StorageClass quota에서 default reset과 clean audit regression을 검증했다.
- quota audit은 API 요청으로만 수행했고 자동 cron/sweep은 사용하지 않았다.

상세 검증 기록:

- `docs/dms-phase9-verification.md`

### Phase 10 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
DMS_PHASE10_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase10-testbed.sh
```

Output:

```json
{
  "status": "ok",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase10_20260530213231",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase10_obs_20260530213231",
  "agent_reports": {
    "cluster-a:c1-worker:cephfs-a": {
      "report_id": "agent_fd9ba665c8034cdf9627b374d1703295",
      "mount_path": "/mnt/testbed-cephfs",
      "filesystem_type": "ceph"
    },
    "cluster-b:c2-worker:cephfs-b": {
      "report_id": "agent_29b2f203817947d79d56e4e5abb54d36",
      "mount_path": "/mnt/testbed-cephfs-c2",
      "filesystem_type": "ceph"
    }
  },
  "targets": [
    {
      "storage_name": "cephfs-a",
      "cluster_name": "cluster-a",
      "node_name": "c1-worker",
      "directory_path": "/mnt/testbed-cephfs/dms-phase10/phase10-a-9ee3124c",
      "group_name": "dms-phase10-phase10-a-9ee3124c",
      "create_request_id": "req_a8e0e0cebae94ff0a9b741ee08f788d3",
      "delete_request_id": "req_41a7690943bc457abdc2f6e2b0be3937",
      "stat": "root dms-phase10-phase10-a-9ee3124c 770 /mnt/testbed-cephfs/dms-phase10/phase10-a-9ee3124c"
    },
    {
      "storage_name": "cephfs-b",
      "cluster_name": "cluster-b",
      "node_name": "c2-worker",
      "directory_path": "/mnt/testbed-cephfs-c2/dms-phase10/phase10-b-9ee3124c",
      "group_name": "dms-phase10-phase10-b-9ee3124c",
      "create_request_id": "req_271604ce9a8a4aea97fa58d2af4bb86f",
      "delete_request_id": "req_5e53b852b3cf44f1ae6ab5946fe6c3c3",
      "stat": "root dms-phase10-phase10-b-9ee3124c 770 /mnt/testbed-cephfs-c2/dms-phase10/phase10-b-9ee3124c"
    }
  ]
}
```

검증 의미:

- 실제 `cluster-a`에 DMS API Deployment와 NodePort service를 배포했다.
- 실제 `cluster-a/c1-worker`와 `cluster-b/c2-worker`에 hostPath mount를 가진 RM Agent DaemonSet을 배포했다.
- storage mapping RM readiness는 synthetic report 없이 실제 Agent report의 host-mounted CephFS evidence로 `Ready`가 됐다.
- DMS API Pod local filesystem이나 Kubernetes application PVC 내부 directory를 filesystem RM target으로 사용하지 않았다.
- `cephfs-a`는 `/mnt/testbed-cephfs`, `cephfs-b`는 `/mnt/testbed-cephfs-c2`에서 directory create/delete side effect를 검증했다.
- DMS create flow가 OpenLDAP `posixGroup`을 만들고 allowed user 2명만 `memberUid`로 추가했다.
- worker node SSSD/NSS에서 LDAP user와 group propagation을 확인했다.
- allowed users는 tiny file create/remove가 가능했고 denied user는 execute/write 접근이 거부됐다.
- delete 후 DMS resource status가 `Deleted`가 되고 host directory가 제거됐다.
- 검증용 LDAP fixture user, DMS access group, host test directory, Kubernetes namespace는 cleanup됐다.

상세 검증 기록:

- `docs/dms-phase10-verification.md`

### Phase 11 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
DMS_PHASE11_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase11-testbed.sh
```

Output:

```json
{
  "status": "ok",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase11_20260530225127",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase11_obs_20260530225127",
  "agent_reports": {
    "cluster-a:c1-worker:cephfs-a": {
      "report_id": "agent_fdfd23d0fd40428e95f1d31c5999a864",
      "mount_path": "/mnt/testbed-cephfs",
      "filesystem_type": "ceph"
    },
    "cluster-b:c2-worker:cephfs-b": {
      "report_id": "agent_98bdf39e633c43aeac18b2b9706567a3",
      "mount_path": "/mnt/testbed-cephfs-c2",
      "filesystem_type": "ceph"
    }
  },
  "targets": [
    {
      "storage_name": "cephfs-a",
      "cluster_name": "cluster-a",
      "node_name": "c1-worker",
      "directory_path": "/mnt/testbed-cephfs/dms-phase10/phase11-expired-a-f06c4928",
      "create_request_id": "req_10f55309be7c497b9ffe5d7b132bc25e",
      "dry_run_sweep_request_id": "req_70888f43958b4220a8dd9ae19135f767",
      "sweep_request_id": "req_cc32d55fa65f4d988bbccd611eb9bc1a",
      "unblock_request_id": "req_271b8a35540043959d42ec8e587d0da3",
      "delete_request_id": "req_00093123d2224f08b9978c922ec30850",
      "blocked_stat": "root dms-phase11-phase11-expired-a-f06c4928 0 /mnt/testbed-cephfs/dms-phase10/phase11-expired-a-f06c4928"
    },
    {
      "storage_name": "cephfs-b",
      "cluster_name": "cluster-b",
      "node_name": "c2-worker",
      "directory_path": "/mnt/testbed-cephfs-c2/dms-phase10/phase11-expired-b-f06c4928",
      "create_request_id": "req_42803cd1dc4944a8afcbc42f026b9c5f",
      "dry_run_sweep_request_id": "req_a8400c9bbe884205a136255fa1d467c5",
      "sweep_request_id": "req_935c29f982e54b7ca25889548c09424b",
      "unblock_request_id": "req_a8ef587e8039448c9f1be9ec17684923",
      "delete_request_id": "req_e30918709aa6479a8b9ed3deecb44a70",
      "blocked_stat": "root dms-phase11-phase11-expired-b-f06c4928 0 /mnt/testbed-cephfs-c2/dms-phase10/phase11-expired-b-f06c4928"
    }
  ],
  "system_skip": {
    "storage_name": "cephfs-a",
    "directory_name": "phase11-system-f06c4928",
    "sweep_request_id": "req_7eaa21e1efe3439fa5e54fc760eb2708",
    "skip_reason": "resource_type_not_auto_blocked"
  }
}
```

검증 의미:

- `GET /api/v1/operations/filesystems/expiring`이 `expires_at=2000-01-01T00:00:00Z`로 생성한 expired filesystem resource를 반환했다.
- `GET /api/v1/operations/action-required`가 sweep 전 `filesystem_expired_unblocked`를 반환했다.
- `dry_run=true` expiration sweep은 POSIX access를 바꾸지 않고 target만 기록했다.
- 실제 sweep은 `cluster-a/c1-worker`와 `cluster-b/c2-worker`의 host-mounted CephFS directory를 모두 `0000`으로 block했다.
- block 중에는 허용 LDAP user `alice`, `bob`과 비허용 fixture user가 모두 directory에 접근하지 못했다.
- manual unblock 후 `alice`, `bob`은 `0770` 접근을 회복했고 비허용 fixture user는 계속 거부됐다.
- `resource_type=system` expired resource는 자동 block하지 않고 `resource_type_not_auto_blocked`로 skip됐으며 action-required에 남았다.
- delete 후 DMS resource status가 `Deleted`가 되고 host directory, DMS access group, 검증용 LDAP fixture user가 cleanup됐다.

상세 검증 기록:

- `docs/dms-phase11-verification.md`

### Phase 12 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
DMS_PHASE12_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase12-testbed.sh
```

Final Phase 12 output summary:

```json
{
  "status": "ok",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase12_20260531131324",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase12_obs_20260531131324",
  "quota_probe": [
    {
      "storage_name": "cephfs-a",
      "node_name": "c1-worker",
      "supports_capacity_quota": true,
      "supports_file_count_quota": true,
      "quota_backend": "cephfs-xattr"
    },
    {
      "storage_name": "cephfs-b",
      "node_name": "c2-worker",
      "supports_capacity_quota": true,
      "supports_file_count_quota": true,
      "quota_backend": "cephfs-xattr"
    }
  ],
  "quota_lifecycle": [
    {
      "storage_name": "cephfs-a",
      "cluster_name": "cluster-a",
      "node_name": "c1-worker",
      "directory_name": "phase12-quota-a-21c0bace",
      "decrease_request_id": "req_8c6380a107524f1a8ddeebdc8d59d346",
      "sync_request_id": "req_64c7e7ee43bf4477bc4cc838b806d5f1",
      "synced_capacity_bytes": 14680064
    },
    {
      "storage_name": "cephfs-b",
      "cluster_name": "cluster-b",
      "node_name": "c2-worker",
      "directory_name": "phase12-quota-b-21c0bace",
      "decrease_request_id": "req_bf1110d21dfa4cea9101e61c6468e7d9",
      "sync_request_id": "req_3a43473c6b7c42aa8379e7e3106ad5b9",
      "synced_capacity_bytes": 14680064
    }
  ],
  "assign_quota": {
    "storage_name": "cephfs-a",
    "directory_name": "phase12-assign-21c0bace",
    "marker_management_mode": "quota_only",
    "delete_rejected_request_id": "req_1d4dcd54b3704902ac6428d0bd0dc4ef"
  },
  "full_import": {
    "storage_name": "cephfs-b",
    "directory_name": "phase12-import-21c0bace",
    "group_name": "dms-phase12-import-21c0bace",
    "import_request_id": "req_237ba2f4ecfe48b3913528d801dca29c",
    "quota_update_request_id": "req_50f19aafa8b24b90a727825815fe240f"
  },
  "unsafe_case": {
    "request_id": "req_1313e29ba3ef415cada7dd313f74b558",
    "reasons": [
      "access_policy.users_required",
      "directory_name_invalid",
      "filesystem_access_group_required",
      "filesystem_access_policy_required"
    ]
  }
}
```

검증 의미:

- `cluster-a/c1-worker`와 `cluster-b/c2-worker`의 host-mounted CephFS에서 capacity quota와 file-count quota xattr apply/read-back이 성공했다.
- allowed LDAP user는 quota 이내 write가 가능했고 denied LDAP fixture user는 접근이 거부됐다.
- capacity quota와 file-count quota 초과가 모두 실제 CephFS에서 `Disk quota exceeded`로 실패했다.
- quota update 증가와 감소 apply, consistency check, manual drift detection, sync를 검증했다.
- filesystem check/sync verifier payload에서 usage collection field를 보내지 않는 경로를 검증했다.
- `assign-quota`는 existing unmanaged directory에 `quota_only` marker와 quota xattr를 적용했고 DMS delete는 reject됐다.
- full import는 OpenLDAP/SSSD group membership을 가진 existing directory를 DMS full-managed resource로 전환하고 import 후 quota update를 검증했다.
- unsafe nested path import는 planning 단계에서 reject됐다.

상세 검증 기록:

- `docs/dms-phase12-verification.md`

### Phase 13 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
DMS_PHASE13_SKIP_IMAGE_BUILD=1 scripts/verify-phase13-testbed.sh
```

Final Phase 13 output summary:

```json
{
  "status": "ok",
  "api_url": "http://192.168.56.11:30093",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase13_20260531141310",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase13_obs_20260531141310",
  "quota_lifecycle": [
    {
      "storage_name": "cephfs-a",
      "directory_name": "phase13-quota-a-6f00efe2",
      "create_request_id": "req_7e4ebfa3bbc44e98a00e896c9188ddfd",
      "decrease_request_id": "req_5fa04d66a6d04c4a8255ab32f3e22790",
      "sync_request_id": "req_514ed778fc7c4121afe398225b677230",
      "delete_request_id": "req_64ce5e60814e4619a5a2689dbb9d771d"
    },
    {
      "storage_name": "cephfs-b",
      "directory_name": "phase13-quota-b-6f00efe2",
      "create_request_id": "req_e50e5a8c3f8843f680b9ac84826ba5b0",
      "decrease_request_id": "req_81ed84fcefba4366b70d97e925acabf9",
      "sync_request_id": "req_f0cdf46159cc4ca3b5e4399b6073f73f",
      "delete_request_id": "req_90ff44b83d48483dabbc17b062a673c7"
    }
  ],
  "worker_scale": {
    "check_request_ids": [
      "req_5d197915eace4862ba31cc59993635d9",
      "req_37c65a11348f40569309813ef2113824"
    ],
    "worker_ids": [
      "dms-rm-worker-756cf7ffcf-2tsqm",
      "dms-rm-worker-756cf7ffcf-6tqpv"
    ]
  },
  "worker_restart": {
    "deleted_pod": "pod/dms-rm-worker-756cf7ffcf-6tqpv",
    "pods_after": [
      "pod/dms-rm-worker-756cf7ffcf-6lljw"
    ]
  },
  "stale_query": {
    "fixture_request_id": "req_4b4805519ef74dbd8a843773847f6038",
    "request_status": "StaleClaim",
    "query_count": 1,
    "run_id": "run_17f0b015dcf14ef086a26ddae8155c60"
  },
  "gpfs_live_verification": {
    "status": "skipped",
    "reason": "testbed has no IBM GPFS / IBM Storage Scale cluster"
  }
}
```

검증 의미:

- 실제 Kubernetes `dms-planner` Deployment와 `dms-rm-worker` Deployment가 PostgreSQL-backed request/plan/run lifecycle을 처리했다.
- Phase 13 verifier는 RM work 처리를 위해 `Planner.run_once()` 또는 `RMWorkerRuntime.run_once()`를 호출하지 않았다.
- `cluster-a/c1-worker`와 `cluster-b/c2-worker`의 host-mounted CephFS에서 Phase 12 quota/import/check/sync flow가 long-running RM Worker Pod 경유로 성공했다.
- RM Worker 2 replica scale-out에서 두 check request가 서로 다른 Pod worker id로 처리됐고, 최종 DB에는 request별 중복 run이 없었다.
- RM Worker Pod restart와 expired lease fixture가 각각 replacement Pod와 `StaleClaim` stale run query로 확인됐다.
- GPFS live verification은 테스트베드에 IBM GPFS / IBM Storage Scale cluster가 없어 skip됐고, command adapter는 local regression에서 fake executor로 검증했다.

중복 claim DB 확인:

```text
 request_id | run_count | states
------------+-----------+--------
(0 rows)

 total_runs | distinct_requests
------------+-------------------
         22 |                22

   status   | count
------------+-------
 StaleClaim |     1
 Succeeded  |    21
```

상세 검증 기록:

- `docs/dms-phase13-verification.md`

### Phase 14 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase14-testbed.sh
```

Final Phase 14 output summary:

```json
{
  "status": "ok",
  "operational_database": "dms_phase13_phase14_20260531211910",
  "observability_database": "dms_phase13_obs_phase14_20260531211910",
  "observability_failure": {
    "diagnostic_events_table": "dropped",
    "auth_failure_status": 401,
    "auth_request_persisted": false,
    "api_observability_log_seen": true,
    "rm_worker_observability_log_seen": true
  },
  "quota_request": {
    "request_id": "req_4015b87bc4c64e2b9ad9930bb943c427",
    "resource_key": "cluster-b:dms-phase14-quota-78de9277",
    "status": "Succeeded"
  },
  "live_resourcequota_hard": {
    "requests.storage": "128Mi",
    "persistentvolumeclaims": "2",
    "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "128Mi",
    "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "2"
  },
  "unknown_backend_request": {
    "request_id": "req_de14e278039948a39fddec1f247c3749",
    "resource_key": "phase14-unknown-a:dms-phase14-unknown-78de9277",
    "status": "BackendApplyFailed"
  },
  "action_required_match_count": 1
}
```

검증 의미:

- `diagnostic_events` table을 제거한 뒤에도 배포된 API의 auth rejection은 HTTP 401로 유지됐고 operational request가 생성되지 않았다.
- observability write failure는 API와 RM Worker log에 `observability event write failed` warning으로 남았다.
- 같은 observability failure 상태에서 long-running RM Worker가 `cluster-b/testbed-longhorn`에 실제 `ResourceQuota/dms-storage-quota`를 생성했고 request는 `Succeeded`가 됐다.
- ready storage mapping에 `backend_type=cephfss` typo가 있어도 live registry가 stub success로 fallback하지 않았고, request는 `BackendApplyFailed`와 action-required issue로 노출됐다.
- Phase 14 verifier는 Phase 13 smoke를 먼저 재실행했고, testbed live flow에서 `Planner.run_once()` 또는 `RMWorkerRuntime.run_once()`를 직접 호출하지 않았다.

상세 검증 기록:

- `docs/dms-phase14-verification.md`

### Phase 15 Live Verification

Command:

```bash
cd /home/mason/workspace/dms
DMS_PHASE13_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase15-testbed.sh
```

Final Phase 15 output summary:

```json
{
  "status": "ok",
  "operational_database": "dms_phase13_phase15_20260531235816",
  "observability_database": "dms_phase13_obs_phase15_20260531235816",
  "filesystem": {
    "resource_key": "cephfs-a:phase15-fs-a8b28b83",
    "create_request": "req_f1c63bad8b454d06acf214cd9e3fb744",
    "update_request": "req_b8f978d428c749e098c0e7c4f3e08e30",
    "expires_at": "2026-09-28T15:01:46.763311+00:00"
  },
  "kubernetes": {
    "create_request": "req_792231dec1504117a8cd063f5bde8bfd",
    "update_request": "req_1d99495af84543b68b58573bcf10faba",
    "import_request": "req_9f15a8f3f5224b62a7902a3f65e89214",
    "sweep_request": "req_12cdbc2afbb54cc4a9e570768e2704c8",
    "expired_query_count": 1,
    "sweep_hard": {
      "persistentvolumeclaims": "0",
      "requests.storage": "0",
      "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "0",
      "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "0"
    }
  }
}
```

검증 의미:

- Phase 15 verifier는 Phase 13 long-running runtime smoke를 먼저 재실행했고, testbed live flow에서 `Planner.run_once()` 또는 `RMWorkerRuntime.run_once()`를 직접 호출하지 않았다.
- Filesystem resource는 future `expires_at`으로 create된 뒤 update request로 DB desired expiry가 갱신됐다.
- Kubernetes namespace quota create는 live `ResourceQuota/dms-storage-quota`에 `dms.io/expires-at` annotation을 반영했다.
- Kubernetes namespace quota update에서 `expires_at`을 생략하면 기존 expiry가 보존됐다.
- DMS-owned live `ResourceQuota/dms-storage-quota` import/adoption에서 `expires_at` 생략 시 default expiry가 기록됐다.
- expired Kubernetes namespace quota는 expiring query와 action-required에 노출됐다.
- on-demand expiration sweep은 live `ResourceQuota.spec.hard`를 모두 `0`으로 설정했다.

상세 검증 기록:

- `docs/dms-phase15-verification.md`

### PostgreSQL Evidence Query

위 live verification이 만든 DB를 직접 조회한 결과다. DB 이름은 실행마다 바뀌므로 재검증 시에는 직전 output의 DB 이름으로 바꿔 실행한다.

Command:

```bash
cd /home/mason/workspace/dms
postgres_password="$(ssh c1-control "kubectl -n postgresql get secret postgresql-auth -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d")"
export POSTGRES_PASSWORD="${postgres_password}"
export PHASE2_DB=dms_phase2_20260528073411
export PHASE2_OBS_DB=dms_phase2_obs_20260528073411
export PHASE3_DB=dms_phase3_20260528073419
export PHASE3_OBS_DB=dms_phase3_obs_20260528073419
/tmp/dms-phase3-venv/bin/python - <<'PY'
import json, os
import psycopg

common = dict(host="192.168.56.11", port=30432, user="appuser", password=os.environ["POSTGRES_PASSWORD"])

def query(db, sql, params=()):
    with psycopg.connect(dbname=db, **common) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

out = {
    "phase2_operational_migrations": [r[0] for r in query(os.environ["PHASE2_DB"], "select version from schema_migrations order by version")],
    "phase2_observability_tables": [r[0] for r in query(os.environ["PHASE2_OBS_DB"], "select table_name from information_schema.tables where table_schema = %s order by table_name", ("public",))],
    "phase2_identity_status_counts": {r[0]: r[1] for r in query(os.environ["PHASE2_DB"], "select status, count(*) from identity_mappings group by status order by status")},
    "phase2_request_status_counts": {r[0]: r[1] for r in query(os.environ["PHASE2_DB"], "select status, count(*) from requests group by status order by status")},
    "phase3_operational_migrations": [r[0] for r in query(os.environ["PHASE3_DB"], "select version from schema_migrations order by version")],
    "phase3_storage_mapping_status_counts": {r[0]: r[1] for r in query(os.environ["PHASE3_DB"], "select sanity_status, count(*) from storage_mappings group by sanity_status order by sanity_status")},
    "phase3_agent_report_status_counts": {r[0]: r[1] for r in query(os.environ["PHASE3_DB"], "select freshness_status, count(*) from agent_reports group by freshness_status order by freshness_status")},
    "phase3_request_status_counts": {r[0]: r[1] for r in query(os.environ["PHASE3_DB"], "select status, count(*) from requests group by status order by status")},
    "phase3_control_mutation_status_counts": {str(r[0]): r[1] for r in query(os.environ["PHASE3_DB"], "select status, count(*) from control_mutations group by status order by status")},
    "phase3_observability_event_counts": {r[0]: r[1] for r in query(os.environ["PHASE3_OBS_DB"], "select event_type, count(*) from diagnostic_events group by event_type order by event_type")},
}
print(json.dumps(out, indent=2, sort_keys=True))
PY
```

Output:

```json
{
  "phase2_identity_status_counts": {
    "Active": 1,
    "Disabled": 1,
    "NeedsReview": 1
  },
  "phase2_observability_tables": [
    "diagnostic_events",
    "schema_migrations"
  ],
  "phase2_operational_migrations": [
    "operational-0001-phase1",
    "operational-0002-phase2-identity",
    "operational-0003-phase3-inventory"
  ],
  "phase2_request_status_counts": {
    "AuthorizationFailed": 1,
    "Succeeded": 2
  },
  "phase3_agent_report_status_counts": {
    "Fresh": 3,
    "Stale": 1
  },
  "phase3_control_mutation_status_counts": {
    "Conflict": 1,
    "Succeeded": 4
  },
  "phase3_observability_event_counts": {
    "agent_node_identity_mismatch": 1,
    "agent_report_accepted": 4,
    "storage_mapping_sanity_check_completed": 4
  },
  "phase3_operational_migrations": [
    "operational-0001-phase1",
    "operational-0002-phase2-identity",
    "operational-0003-phase3-inventory"
  ],
  "phase3_request_status_counts": {
    "Planned": 1,
    "Rejected": 1
  },
  "phase3_storage_mapping_status_counts": {
    "Failed": 2,
    "Ready": 1
  }
}
```

Phase 4 DB evidence:

```json
{
  "observability_events": [
    {"event_type": "agent_report_accepted", "severity": "INFO"},
    {"event_type": "agent_report_accepted", "severity": "INFO"},
    {"event_type": "agent_report_accepted", "severity": "INFO"},
    {"event_type": "storage_mapping_sanity_check_completed", "severity": "INFO"},
    {"event_type": "kubernetes_resourcequota_apply_started", "severity": "INFO"},
    {"event_type": "kubernetes_resourcequota_apply_completed", "severity": "INFO"},
    {"event_type": "rm_plan_completed", "severity": "INFO"},
    {"event_type": "pvc_admission_verification_completed", "severity": "INFO"}
  ],
  "resource_key": "cluster-b:dms-phase4-ade9c49f",
  "resource_kind": "kubernetes_namespace_quota",
  "resource_quota_spec_hard": {
    "persistentvolumeclaims": "2",
    "requests.storage": "128Mi",
    "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "128Mi"
  },
  "resource_quota_status_hard": {
    "persistentvolumeclaims": "2",
    "requests.storage": "128Mi",
    "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "128Mi"
  },
  "resource_status": "Succeeded",
  "resource_version": 2,
  "result_message": "Kubernetes ResourceQuota live apply completed",
  "result_terminal_status": "Succeeded"
}
```

Phase 5 DB evidence:

```json
{
  "operational_database": "dms_phase5_20260528155705",
  "observability_database": "dms_phase5_obs_20260528155705",
  "request_status_counts": {
    "Rejected": 2,
    "Succeeded": 16
  },
  "result_status_counts": {
    "Rejected": 2,
    "Succeeded": 16
  },
  "resources": [
    {
      "resource_key": "cluster-a:dms-phase5-cephfs-0ba48982",
      "status": "Deleted",
      "version": 8,
      "desired_quota": {
        "pvc_count": 5,
        "requests_storage_bytes": 402653184
      },
      "desired_hard": {
        "persistentvolumeclaims": "5",
        "requests.storage": "384Mi",
        "testbed-cephfs.storageclass.storage.k8s.io/requests.storage": "384Mi"
      },
      "observed_deleted": true,
      "observed_resource_quota": {
        "cluster_name": "cluster-a",
        "exists": false,
        "name": "dms-storage-quota",
        "namespace": "dms-phase5-cephfs-0ba48982"
      }
    },
    {
      "resource_key": "cluster-b:dms-phase5-longhorn-0ba48982",
      "status": "Deleted",
      "version": 8,
      "desired_quota": {
        "pvc_count": 5,
        "requests_storage_bytes": 402653184
      },
      "desired_hard": {
        "persistentvolumeclaims": "5",
        "requests.storage": "384Mi",
        "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "384Mi"
      },
      "observed_deleted": true,
      "observed_resource_quota": {
        "cluster_name": "cluster-b",
        "exists": false,
        "name": "dms-storage-quota",
        "namespace": "dms-phase5-longhorn-0ba48982"
      }
    }
  ],
  "event_counts": {
    "kubernetes_resourcequota_apply_completed": 2,
    "kubernetes_resourcequota_block_completed": 2,
    "kubernetes_resourcequota_consistency_check_completed": 2,
    "kubernetes_resourcequota_delete_completed": 2,
    "kubernetes_resourcequota_sync_completed": 4,
    "kubernetes_resourcequota_unblock_completed": 2,
    "kubernetes_resourcequota_update_completed": 2,
    "rm_plan_completed": 16
  }
}
```

Phase 6 DB evidence:

```json
{
  "operational_database": "dms_phase6_20260528211617",
  "observability_database": "dms_phase6_obs_20260528211617",
  "request_status_counts": {
    "Rejected": 1,
    "Succeeded": 12
  },
  "result_status_counts": {
    "Rejected": 1,
    "Succeeded": 12
  },
  "resources": [
    {
      "resource_key": "cluster-a:dms-phase6-cephfs-regression-12f4436d",
      "status": "Deleted",
      "version": 4,
      "desired_hard": {
        "persistentvolumeclaims": "4",
        "requests.storage": "256Mi",
        "testbed-cephfs.storageclass.storage.k8s.io/requests.storage": "256Mi"
      }
    },
    {
      "resource_key": "cluster-b:dms-phase6-longhorn-multi-12f4436d",
      "status": "Deleted",
      "version": 8,
      "desired_hard": {
        "longhorn-static.storageclass.storage.k8s.io/persistentvolumeclaims": "4",
        "longhorn-static.storageclass.storage.k8s.io/requests.storage": "256Mi",
        "persistentvolumeclaims": "8",
        "requests.storage": "768Mi",
        "testbed-longhorn.storageclass.storage.k8s.io/persistentvolumeclaims": "4",
        "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "512Mi"
      }
    }
  ],
  "effective_quota_warning_result": {
    "request_id": "req_828b47254e1f4e9788c971cba363bb98",
    "consistency_status": "Drifted",
    "issues": [
      {
        "field": "spec.hard",
        "key": "testbed-longhorn.storageclass.storage.k8s.io/requests.storage",
        "reason": "hard_limit_drifted",
        "desired": "384Mi",
        "live": "512Mi"
      }
    ],
    "effective_quota_warnings": [
      {
        "type": "non_dms_quota_more_restrictive",
        "resource_quota_name": "phase6-non-dms-quota",
        "key": "testbed-longhorn.storageclass.storage.k8s.io/requests.storage",
        "dms_hard": "384Mi",
        "non_dms_hard": "128Mi"
      }
    ]
  },
  "event_counts": {
    "kubernetes_resourcequota_apply_completed": 2,
    "kubernetes_resourcequota_update_completed": 2,
    "kubernetes_resourcequota_block_completed": 1,
    "kubernetes_resourcequota_unblock_completed": 1,
    "kubernetes_resourcequota_consistency_check_completed": 2,
    "kubernetes_resourcequota_sync_completed": 2,
    "kubernetes_resourcequota_delete_completed": 2,
    "rm_plan_completed": 12
  }
}
```

## Re-run From Scratch

### 1. Prepare Python Environment

Command:

```bash
cd /home/mason/workspace/dms
python3 -m venv /tmp/dms-phase3-venv
/tmp/dms-phase3-venv/bin/python -m pip install --upgrade pip
/tmp/dms-phase3-venv/bin/python -m pip install -e '.[test,postgres,ldap]'
```

### 2. Verify Testbed Topology

Command:

```bash
ssh c1-control "kubectl get nodes -o wide; kubectl get storageclass; kubectl -n postgresql get pods,svc"
ssh c2-control "kubectl get nodes -o wide; kubectl get storageclass; kubectl -n longhorn-system get pods | sed -n '1,12p'"
```

### 3. Verify LDAP Direct Read

Use the LDAP command in `LDAP Direct Lookup`.

### 4. Re-run Phase 2 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase2-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE2_OPERATIONAL_DB` and `DMS_PHASE2_OBSERVABILITY_DB` are set.

### 5. Re-run Phase 3 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase3-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE3_OPERATIONAL_DB` and `DMS_PHASE3_OBSERVABILITY_DB` are set.

### 6. Re-run Phase 4 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase4-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE4_OPERATIONAL_DB` and `DMS_PHASE4_OBSERVABILITY_DB` are set.

To inspect the namespace manually after the script, keep cleanup disabled:

```bash
cd /home/mason/workspace/dms
DMS_PHASE4_CLEANUP=false PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase4-testbed.sh
ssh c2-control "kubectl -n <namespace_from_output> get resourcequota dms-storage-quota -o yaml"
ssh c2-control "kubectl -n <namespace_from_output> get pvc"
ssh c2-control "kubectl delete namespace <namespace_from_output> --ignore-not-found"
```

### 7. Re-run Phase 5 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase5-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE5_OPERATIONAL_DB` and `DMS_PHASE5_OBSERVABILITY_DB` are set.

To inspect namespaces manually after the script, keep cleanup disabled:

```bash
cd /home/mason/workspace/dms
DMS_PHASE5_CLEANUP=false PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase5-testbed.sh
ssh c1-control "kubectl -n <cephfs_namespace_from_output> get resourcequota,pvc"
ssh c2-control "kubectl -n <longhorn_namespace_from_output> get resourcequota,pvc"
ssh c1-control "kubectl delete namespace <cephfs_namespace_from_output> --ignore-not-found"
ssh c2-control "kubectl delete namespace <longhorn_namespace_from_output> --ignore-not-found"
```

### 8. Re-run Phase 6 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase6-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE6_OPERATIONAL_DB` and `DMS_PHASE6_OBSERVABILITY_DB` are set.

To inspect namespaces manually after the script, keep cleanup disabled:

```bash
cd /home/mason/workspace/dms
DMS_PHASE6_CLEANUP=false PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase6-testbed.sh
ssh c2-control "kubectl -n <longhorn_multi_namespace_from_output> get resourcequota,pvc"
ssh c1-control "kubectl -n <cephfs_namespace_from_output> get resourcequota,pvc"
ssh c2-control "kubectl delete namespace <longhorn_multi_namespace_from_output> --ignore-not-found"
ssh c1-control "kubectl delete namespace <cephfs_namespace_from_output> --ignore-not-found"
```

### 9. Re-run Phase 7 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase7-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE7_OPERATIONAL_DB` and `DMS_PHASE7_OBSERVABILITY_DB` are set.

To inspect namespaces manually after the script, keep cleanup disabled:

```bash
cd /home/mason/workspace/dms
DMS_PHASE7_CLEANUP=false PATH="/tmp/dms-phase3-venv/bin:$PATH" ./scripts/verify-phase7-testbed.sh
ssh c2-control "kubectl -n <longhorn_namespace_from_output> get resourcequota,pvc"
ssh c1-control "kubectl -n <cephfs_namespace_from_output> get resourcequota,pvc"
ssh c2-control "kubectl delete namespace <longhorn_namespace_from_output> --ignore-not-found"
ssh c1-control "kubectl delete namespace <cephfs_namespace_from_output> --ignore-not-found"
```

### 10. Re-run Phase 8 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase8-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE8_OPERATIONAL_DB` and `DMS_PHASE8_OBSERVABILITY_DB` are set.

To skip rebuilding the image when the registry already has the current image:

```bash
cd /home/mason/workspace/dms
DMS_PHASE8_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase8-testbed.sh
```

The script cleans up the temporary `dms-phase8` namespace in both clusters by default.

### 11. Re-run Phase 9 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase9-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE9_OPERATIONAL_DB` and `DMS_PHASE9_OBSERVABILITY_DB` are set.

To skip rebuilding the image when the registry already has the current image:

```bash
cd /home/mason/workspace/dms
DMS_PHASE9_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase9-testbed.sh
```

The script cleans up the temporary `dms-phase9` namespace in both clusters by default.

### 12. Re-run Phase 10 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase10-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE10_OPERATIONAL_DB` and `DMS_PHASE10_OBSERVABILITY_DB` are set.

To skip rebuilding the image when the registry already has the current image:

```bash
cd /home/mason/workspace/dms
DMS_PHASE10_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase10-testbed.sh
```

The script cleans up the temporary `dms-phase10` namespace in both clusters, DMS phase-scoped LDAP fixture users, DMS access groups, and host test directories by default.

### 13. Re-run Phase 11 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase11-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE11_OPERATIONAL_DB` and `DMS_PHASE11_OBSERVABILITY_DB` are set.

To skip rebuilding the image when the registry already has the current image:

```bash
cd /home/mason/workspace/dms
DMS_PHASE11_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase11-testbed.sh
```

The script runs the Phase 10 deployment/smoke setup first, then verifies Phase 11 expiry query, dry-run sweep, block, unblock, system-resource skip, and cleanup. It cleans up the temporary `dms-phase11` namespace in both clusters, DMS phase-scoped LDAP fixture users, DMS access groups, and host test directories by default.

### 14. Re-run Phase 12 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase12-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE12_OPERATIONAL_DB` and `DMS_PHASE12_OBSERVABILITY_DB` are set.

To skip rebuilding the image when the registry already has the current image:

```bash
cd /home/mason/workspace/dms
DMS_PHASE12_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase12-testbed.sh
```

The script runs the Phase 10 deployment/smoke setup first, then verifies Phase 12 CephFS quota probe, create/update/decrease apply/check/sync/action-required, quota-only assign, full import, unsafe path rejection, and cleanup. It cleans up the temporary `dms-phase12` namespace in both clusters, DMS phase-scoped LDAP fixture users, DMS access groups, and host test directories by default.

### 15. Re-run Phase 13 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase13-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE13_OPERATIONAL_DB` and `DMS_PHASE13_OBSERVABILITY_DB` are set.

To skip rebuilding the image when the registry already has the current image:

```bash
cd /home/mason/workspace/dms
DMS_PHASE13_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase13-testbed.sh
```

The script deploys the DMS API, RM Agent DaemonSets, long-running Planner Deployment, and long-running RM Worker Deployment, then verifies Phase 12 CephFS quota/import/check/sync flows through API-only request submission. It also verifies RM Worker 2-replica scale-out, Pod restart, stale claim query, duplicate claim absence, and GPFS live skip evidence. It cleans up the temporary `dms-phase13` namespace in both clusters, DMS phase-scoped LDAP fixture users, DMS access groups, and host test directories by default.

### 16. Re-run Phase 14 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase14-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE14_DB_SUFFIX`, `DMS_PHASE13_OPERATIONAL_DB`, or
`DMS_PHASE13_OBSERVABILITY_DB` are set.

The script reuses the Phase 13 long-running runtime verifier first, then drops
the observability diagnostic table, verifies safe-write behavior, verifies
Longhorn Kubernetes ResourceQuota live apply through the deployed RM Worker, and
verifies unknown backend fail-closed/action-required behavior. It cleans up the
temporary `dms-phase14` namespace in both clusters and the Phase 14 quota
namespace on `cluster-b` by default.

### 17. Re-run Phase 15 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase15-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE15_DB_SUFFIX`, `DMS_PHASE13_OPERATIONAL_DB`, or
`DMS_PHASE13_OBSERVABILITY_DB` are set.

The script reuses the Phase 13 long-running runtime verifier first, then verifies
filesystem expiry update, Kubernetes namespace quota expiry create/update/import,
expiring query, action-required aggregation, and on-demand expiration sweep. It
cleans up the temporary `dms-phase15` namespace in both clusters and Phase 15
quota namespaces on `cluster-b` by default.

To skip rebuilding the image when `testbed-registry:5000/dms:phase15` is already
available:

```bash
cd /home/mason/workspace/dms
DMS_PHASE13_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase15-testbed.sh
```

### 18. Re-run Phase 16 Live Verification With Fresh DBs

Command:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase16-testbed.sh
```

The script creates new PostgreSQL DB names using the current timestamp unless
`DMS_PHASE16_DB_SUFFIX`, `DMS_PHASE13_OPERATIONAL_DB`, or
`DMS_PHASE13_OBSERVABILITY_DB` are set.

The script reuses the Phase 15 verifier first, which reuses the Phase 13
long-running runtime verifier. It then switches the deployed API to
`DMS_REQUIRE_MTLS_HEADER=true` and
`DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`, deploys a short-lived testbed mTLS edge
proxy, verifies valid/invalid client certificates, token failure cases,
certificate-subject actor derivation, conflicting `x-dms-actor` rejection,
direct `FAILED` mTLS evidence rejection, NetworkPolicy blocking of direct
spoofed evidence headers, and an auth-only protected endpoint matrix for all
50 protected DMS API routes. It cleans up the temporary `dms-phase16` namespace
in both clusters by default.

To skip rebuilding the image when `testbed-registry:5000/dms:phase16` is already
available:

```bash
cd /home/mason/workspace/dms
DMS_PHASE13_SKIP_IMAGE_BUILD=1 ./scripts/verify-phase16-testbed.sh
```

### 19. Phase 17 Live Kubernetes ResourceQuota Check

Phase 17 does not have a dedicated long-running verifier script because the
testbed lacks real GPFS. The verification used a temporary GPFS-like
`CSIDriver/spectrumscale.csi.ibm.com` and
`StorageClass/dms-phase17-gpfs-csi` on `cluster-b`, then executed local DMS
Planner/RMWorkerRuntime with the live `ssh-kubectl` adapter.

Evidence is recorded in `docs/dms-phase17-verification.md`.

### 20. Re-run Phase 18 Testbed Verification

Command:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase18-testbed.sh
```

The script creates or reuses fresh Phase 18 PostgreSQL databases on the testbed
NodePort PostgreSQL, applies DMS migrations, then verifies control-state API,
maintenance reject, operational work query, run heartbeat renewal,
stale/recovery classification, drain status, resume blocker, forced resume, and
control mutation audit records without creating Kubernetes or filesystem backend
side effects.

Evidence is recorded in `docs/dms-phase18-verification.md`.

### 21. Phase 19 Data Management Scan Live Path

Phase 19 read-only Data Management `scan` is verified in the testbed with both
the deterministic `dscan` fixture and a pinned real mpifileutils job image. The
verified path covers API intake, identity/POSIX preflight, DM Agent based node
selection, runtime preflight Pod, VolcanoJob submission/monitoring, artifact
write, parser, DB summary persistence, query, and action-required behavior.
An additional standalone Volcano/MPI smoke test verified the mpifileutils image
family can run `dscan` with two MPI ranks across both cluster-a nodes on a
shared CephFS RWX PVC; that smoke test is separate from the current DMS API/DM
Worker execution path.

Implemented so far:

- structured `scan` target request model with flat `storage_name`/`target_path`
  compatibility normalization
- `High`/`Mid`/`Low` public priority normalization
- `scan` option allowlist and raw command-line rejection
- `sync`/`rm` endpoint fail-closed behavior with no request/plan/job side effect
- destructive `sync`/`rm` confirm guard
- `data_jobs` Phase 19 evidence fields:
  `normalized_target`, `preflight_result`, `volcano_job_ref`,
  `result_summary`, `log_uri`
- requester-filterable Data Job list/detail queries and scan-specific query aliases
- scan preflight based on active Identity Mapping and fresh DM Agent
  mount/tool/credential/network/identity evidence
- runtime POSIX preflight Pod before Volcano submission, scheduled on the
  selected node and run as the mapped UID/GID
- live VolcanoJob submission/monitoring for read-only `scan`
- file artifact URI contract:
  `data_jobs.artifact_uri` stores the job base URI and
  `result_summary.report_uri/stdout_uri/stderr_uri/summary_uri` point at files
  under that base
- `summary.json` and `dscan-report.json` parsing into stable
  file/directory/byte/error summary
- pinned real mpifileutils job image build template at
  `install/docker/Dockerfile.mpifileutils`
- Data Management action-required entries for scan preflight/runtime failures
- settings-aware live Volcano adapter selection and explicit `stub` test/dev mode
- install ConfigMap/env docs for `DMS_DM_*` runtime settings and namespace RBAC for
  preflight Pod and VolcanoJob create/get/list/watch/delete

Verification evidence:

- `docs/dms-phase19-verification.md`
- local full regression: `143 passed in 86.93s`
- testbed readiness check: all five VMs running, cluster-a nodes Ready, Volcano
  deployments Ready, SSSD lookup for `alice`, and CephFS host mount present
- live testbed verifier: `scripts/verify-phase19-testbed.sh`, exit status 0
- live artifact base:
  `file:///mnt/testbed-cephfs/dms-phase19-artifacts-20260603220451/job_5ca2e30ba2054911aadb3db49b17da3e`
- live report URI:
  `file:///mnt/testbed-cephfs/dms-phase19-artifacts-20260603220451/job_5ca2e30ba2054911aadb3db49b17da3e/dscan-report.json`
- live Volcano ref:
  `volcano://dms-phase19/dms-scan-job-5ca2e30ba2054911aadb3db49b17da3e`
- parsed summary:
  `file_count=3`, `directory_count=2`, `total_bytes=31`, `error_count=0`
- real mpifileutils image source:
  `chahwansong/mpifileutils@e3bfee10970bb4e24204d28689e3337e9741cca4`
- testbed real mpifileutils image:
  `testbed-registry:5000/dms-mpifileutils:e3bfee1`
- real-image verifier: `scripts/verify-phase19-testbed.sh`, exit status 0 with
  `DMS_PHASE19_DM_JOB_IMAGE` and `DMS_PHASE19_DM_JOB_IMAGE_REF` set
- real-image artifact base:
  `file:///mnt/testbed-cephfs/dms-phase19-artifacts-20260603222132/job_3a55ca8ca77b433b9505ec50c38f8186`
- real-image report URI:
  `file:///mnt/testbed-cephfs/dms-phase19-artifacts-20260603222132/job_3a55ca8ca77b433b9505ec50c38f8186/dscan-report.json`
- real-image normalized summary URI:
  `file:///mnt/testbed-cephfs/dms-phase19-artifacts-20260603222132/job_3a55ca8ca77b433b9505ec50c38f8186/summary.json`
- real-image Volcano ref:
  `volcano://dms-phase19/dms-scan-job-3a55ca8ca77b433b9505ec50c38f8186`
- real-image parsed summary:
  `file_count=3`, `directory_count=2`, `total_bytes=31`, `error_count=0`
- standalone two-node MPI smoke:
  `dms-mpi-dscan-smoke` in namespace `dms-mpi-verify`, using
  `testbed-registry:5000/dms-mpifileutils-mpi:ssh`
- standalone MPI pod placement:
  launcher completed on `c1-control`, worker-0 ran on `c1-control`, worker-1 ran
  on `c1-worker`
- standalone MPI hostfile:
  `10.244.0.92`, `10.244.1.69`
- standalone MPI dscan summary:
  `total_entries=5`, `total_files=3`, `total_directories=2`,
  `total_symlinks=0`, `total_other=0`, `broken_paths=[]`

### 22. Phase 20 Data Management Sync/Rm Live Path

Phase 20 Data Management `sync`/`rm` is verified in the testbed with the pinned
real mpifileutils image and real VolcanoJob execution. The verified path covers
canonical request parsing, compatibility normalization, option allowlist,
Identity Mapping/POSIX preflight, DM Agent node selection, `dsync`/`drm` preview
VolcanoJobs, explicit confirm, confirmed execution VolcanoJobs, artifact parsing,
DB summary/query, action-required negative paths, and filesystem effects.

Implemented so far:

- structured `sync` source/destination request model with flat same-storage
  compatibility normalization
- structured `rm` target request model with flat compatibility normalization
- operation-specific option allowlists and raw command-line rejection
- destination/source overlap, storage-root rm, artifact-path, absolute path, and
  traversal guards
- `sync` source read/traverse and destination write/create POSIX preflight
- `rm` target/parent traverse/delete POSIX preflight
- DM Agent mount/tool/credential/network/identity evidence based node selection
- same-node `dsync` selection for source/destination mounted on one candidate
- `drm` selection for target-mounted candidate
- dry-run preview VolcanoJob before any mutation
- explicit confirm with TTL/fingerprint guard before execution
- confirmed `dsync` and `drm` execution VolcanoJobs
- preview/execution artifact layout:
  `<artifact_base>/<job_id>/preview/` and
  `<artifact_base>/<job_id>/execution/`
- phase-specific `summary.json`, `command.json`, `stdout.log`, `stderr.log`
  parsing into `result_summary`
- action-required query coverage for `data.scan`, `data.sync`, and `data.rm`
- install/runtime docs and env examples for Phase 20 `DMS_DM_*` settings

Live verification evidence:

- `docs/dms-phase20-verification.md`
- testbed verifier: `scripts/verify-phase20-testbed.sh`, exit status 0
- DMS image:
  `testbed-registry:5000/dms:phase20-20260603234227`
- mpifileutils source:
  `chahwansong/mpifileutils@e3bfee10970bb4e24204d28689e3337e9741cca4`
- MPI ssh image:
  `testbed-registry:5000/dms-mpifileutils-mpi:phase20-20260603234227`
- live DB suffix:
  `20260603235548`
- live operational DB:
  `dms_phase20_20260603235548`
- live observability DB:
  `dms_phase20_obs_20260603235548`
- sync Data Job:
  `job_aa4972c3cc304d82bab972ddaa5c8a9e`
- sync request:
  `req_7cab18ce63194c31a143e67661b64f01`
- sync selected tool:
  `dsync`
- sync artifact base:
  `file:///mnt/testbed-cephfs/dms-phase20-artifacts-20260603235548/job_aa4972c3cc304d82bab972ddaa5c8a9e`
- sync preview ref:
  `volcano://dms-phase20/dms-sync-preview-job-aa4972c3cc304d82bab972ddaa5c8a9e`
- sync execution ref:
  `volcano://dms-phase20/dms-sync-execution-job-aa4972c3cc304d82bab972ddaa5c8a9e`
- sync preview summary:
  `dry_run=true`, `file_count=3`, `directory_count=2`, `total_bytes=38`,
  `error_count=0`
- sync execution summary:
  `dry_run=false`, `file_count=3`, `directory_count=2`, `total_bytes=38`,
  `error_count=0`
- sync filesystem effect:
  destination contained `alpha.txt`, `beta.txt`, `nested/gamma.txt`
- rm Data Job:
  `job_01a760e616e845eb86a03ee1b9c7ae92`
- rm request:
  `req_764a6cc88299416fac61add69fc4dc04`
- rm selected tool:
  `drm`
- rm artifact base:
  `file:///mnt/testbed-cephfs/dms-phase20-artifacts-20260603235548/job_01a760e616e845eb86a03ee1b9c7ae92`
- rm preview ref:
  `volcano://dms-phase20/dms-rm-preview-job-01a760e616e845eb86a03ee1b9c7ae92`
- rm execution ref:
  `volcano://dms-phase20/dms-rm-execution-job-01a760e616e845eb86a03ee1b9c7ae92`
- rm preview summary:
  `dry_run=true`, `file_count=1`, `directory_count=1`, `total_bytes=6`,
  `target_absent=false`, `error_count=0`
- rm execution summary:
  `dry_run=false`, `file_count=1`, `directory_count=1`, `total_bytes=6`,
  `target_absent=true`, `error_count=0`
- expired preview negative case:
  `job_f0e04c42bb564bdca5797d910bfa83ce` -> `PreviewExpired`
- missing identity negative case:
  `job_a40ce4ad154e4ee3a27326bf72b2edbc` -> `PreflightFailed`
- standalone integrated MPI smoke namespace:
  `dms-phase20-mpi-20260603235548`
- standalone MPI hostfile:
  `10.244.0.203`, `10.244.1.166`
- standalone MPI dscan summary:
  `total_entries=5`, `total_files=3`, `total_directories=2`,
  `total_symlinks=0`, `total_other=0`, `broken_paths=[]`
- cleanup:
  `dms-phase20-mpi-20260603235548` and `dms-phase20` namespaces deleted by the
  verifier.

Not live Done:

- `nsync` separated-role live execution. The current worker can identify `nsync`
  candidate pools, but the Kubernetes adapter fails closed for live `nsync`
  execution until Service/role orchestration is implemented and verified.
- large-scale data movement performance, partial mutation repair automation, WAN
  policy, and production object-store artifact backend.

### 23. Optional Local Regression

이 검증은 mock/stub도 포함하므로 `Done`의 단독 근거로 쓰지 않는다. 코드 회귀 확인 용도다.

Command:

```bash
cd /home/mason/workspace/dms
python3 -m py_compile scripts/phase13_long_running_rm_worker.py scripts/phase14_runtime_hardening.py scripts/phase15_resource_expiry.py scripts/phase16_mtls_auth.py
python3 -m py_compile scripts/phase19_data_management_scan.py scripts/phase19_dscan_fixture.py scripts/phase20_data_management_sync_rm.py
python3 -m py_compile src/dms/api.py src/dms/workers.py src/dms/repositories.py src/dms/query.py tests/test_phase18_operational_controls.py tests/test_phase16_mtls_auth.py
python3 -m pytest -q
python3 -m pytest -q tests/test_phase20_data_management_sync_rm.py tests/test_phase19_data_management_scan.py tests/test_phase1_contracts.py tests/test_phase16_mtls_auth.py
python3 -m pytest -q tests/test_phase18_operational_controls.py tests/test_phase16_mtls_auth.py tests/test_phase14_runtime_hardening.py tests/test_gpfs_backend.py tests/test_phase6_kubernetes_multi_storage_quota.py tests/test_phase3_inventory.py
bash -n scripts/verify-phase20-testbed.sh scripts/verify-phase19-testbed.sh scripts/verify-phase18-testbed.sh scripts/verify-phase16-testbed.sh scripts/verify-phase15-testbed.sh scripts/verify-phase14-testbed.sh scripts/verify-phase13-testbed.sh install/scripts/*.sh
git diff --check
```

Output:

```text
149 passed in 91.01s
```

## Not Implemented Yet

다음 항목은 Phase 18까지 완료된 기능으로 보지 않는다.

- DMS API server, Planner, Worker, Agent의 production Helm/Kustomize 배포
- filesystem expiry 자동 cron/scheduler/controller
- filesystem expiry delete/archive 정책
- Kubernetes namespace quota expiry 자동 cron/scheduler/controller
- filesystem quota 자동 cron/scheduler/controller
- DMS lifecycle operation으로서의 Kubernetes namespace delete
- Kubernetes quota drift/usage pressure 자동 cron/scheduler/controller
- GPFS live staging/testbed verification on an actual IBM GPFS / IBM Storage Scale cluster
- WekaFS/Lustre filesystem quota/import live adapter
- Data Management `nsync` separated-role live execution
- Data Management large-scale performance verification
- Data Management partial mutation repair automation
- Data Management production object-store artifact backend
- JWT/OIDC provider integration
- token issuer/audience/scope validation
- full RBAC/authorization policy schema
- certificate revocation/OCSP/CRL
- automated client certificate issuance/rotation
- per-agent certificate provisioning
- production ingress-nginx-specific mTLS manifest live verification

## Comments For Next Phases

- 다음 Data Management 작업은 `nsync` separated-role live execution, large-scale performance, partial mutation repair, production object-store artifact backend다. Phase 19 read-only `scan`과 Phase 20 `dsync`/`drm` preview-confirm path는 real pinned mpifileutils image와 VolcanoJob으로 검증됐다.
- `cluster-b/testbed-longhorn` + `cluster-b/longhorn-static`은 Phase 6/7/8/9에서 multi-StorageClass quota target으로 검증했다.
- `cluster-a/testbed-cephfs`는 Phase 5/6/7/8/9에서 self-managed RM target 및 regression target으로 검증했다.
- `cluster-a/c1-worker`와 `cluster-b/c2-worker` host-mounted CephFS는 Phase 10/11/12/13/15에서 filesystem create/delete, expiry query, API-driven sweep, block/unblock, expiry update/import default, quota apply/enforcement, check/sync, import/assign, long-running RM Worker target으로 검증했다.
- `cluster-b/testbed-longhorn` Kubernetes namespace quota expiry create/update/import, expiring query, action-required, and on-demand expiration sweep은 Phase 15에서 검증했다.
- Phase 16에서 DMS API의 trusted edge mTLS evidence validation, token+mTLS combined auth, certificate subject actor derivation, direct spoof NetworkPolicy 차단을 testbed mTLS edge proxy로 검증했다.
- GPFS backend는 Phase 13에서 IBM Storage Scale fileset command adapter와 fake executor regression까지 구현됐다. 실제 IBM Storage Scale cluster에서의 staging/live 검증은 별도 phase 또는 운영 staging 작업으로 남아 있다.
- Phase 17에서 GPFS CSI Kubernetes namespace quota 경로는 CephFS/Longhorn과 같은 live `KubernetesNamespaceQuotaLiveAdapter` 경로로 통합됐고, `gpfs-kubernetes-quota-stub` production/live selection은 제거됐다.
- Phase 18에서 DMS source update, control cluster reboot, planned shutdown 전에 사용할 DB-backed control state, drain readiness, worker heartbeat, stale/recovery guard, install runbook scripts가 추가됐다.
- Phase 14에서 observability safe write boundary와 live backend registry fail-closed는 완료됐으므로, 이후 live runtime에서 stub fallback을 기대하는 테스트는 `BackendAdapterRegistry.with_test_stubs(...)`를 명시해야 한다.
- Phase 8에서 실제 Agent DaemonSet report를 검증했으므로 이후 Data Management preflight나 filesystem lifecycle은 synthetic Agent report 없이 진행해야 한다.
- Phase 4부터는 mock/stub 결과와 real backend mutation 결과를 문서에서 반드시 분리한다.
- 실제 backend mutation이 추가될 때마다 이 문서의 `Live Verification Results`와 `Not Implemented Yet`를 갱신한다.
- live verification script 이름에 `smoke`가 남아 있더라도, 이 문서에서는 어떤 부분이 실제 외부 시스템 검증이고 어떤 부분이 stub/synthetic인지 명확히 구분한다.
