# DMS Done / Verified Status

Last updated: 2026-05-28 21:18 +0900

이 문서는 DMS 구현이 진행될 때마다 계속 갱신하는 완료/검증 기록이다.
새 phase가 끝나면 같은 구조로 `Implemented`, `Live Verification`,
`Re-run Commands`, `Not Implemented Yet`, `Comments`를 추가하거나 갱신한다.

중요한 기준:

- `Done`은 실제 테스트베드 또는 실제 외부 시스템에 연결해 확인된 기능만 의미한다.
- local pytest, stub adapter, synthetic data는 보조 회귀 검증으로만 기록한다.
- 아직 실제 backend side effect가 구현되지 않은 기능은 성공처럼 적지 않고 명확히 미구현으로 남긴다.
- Phase 6까지의 실제 live 검증 대상은 PostgreSQL, OpenLDAP, Kubernetes read-only inventory, `cluster-b` Kubernetes ResourceQuota/PVC admission, `cluster-a/testbed-cephfs`와 `cluster-b/testbed-longhorn` Kubernetes ResourceQuota lifecycle, `cluster-b` multi-StorageClass quota lifecycle이다.

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

## Implemented Through Phase 6

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
- Phase 6 effective quota warning은 check/sync result evidence로 검증했다. 별도 namespace quota query endpoint나 action-required aggregation은 아직 완료 범위가 아니다.

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

### 9. Optional Local Regression

이 검증은 mock/stub도 포함하므로 `Done`의 단독 근거로 쓰지 않는다. 코드 회귀 확인 용도다.

Command:

```bash
cd /home/mason/workspace/dms
/tmp/dms-phase3-venv/bin/python -m pytest -q
```

Output:

```text
37 passed in 17.11s
```

## Not Implemented Yet

다음 항목은 Phase 6까지 완료된 기능으로 보지 않는다.

- DMS API server, Planner, Worker, Agent의 Kubernetes Deployment/Helm/Kustomize 배포
- 실제 DMS Agent DaemonSet
- Agent의 node-local mount/tool/credential/network probe 구현
- 실제 filesystem directory create/update/block/delete
- 실제 filesystem quota 적용
- DMS lifecycle operation으로서의 Kubernetes namespace delete
- Kubernetes default quota policy 기반 reset workflow
- Kubernetes effective quota dedicated query/action-required aggregation beyond check/sync result evidence
- 실제 VolcanoJob create/watch/terminate
- mpifileutils image build 또는 live execution
- Data Management POSIX permission runtime preflight
- trusted ingress mTLS live validation
- maintenance/drain mode의 full operational workflow
- planned shutdown/startup recovery runbook 자동화

## Comments For Next Phases

- 다음 phase는 DMS Agent DaemonSet, filesystem quota lifecycle, 또는 Kubernetes effective quota dedicated query/action-required aggregation 중 하나로 좁혀서 진행하는 것이 적절하다.
- `cluster-b/testbed-longhorn` + `cluster-b/longhorn-static`은 Phase 6에서 multi-StorageClass quota target으로 검증했다.
- `cluster-a/testbed-cephfs`는 Phase 5/6에서 self-managed RM target 및 regression target으로 검증했다.
- Phase 4부터는 mock/stub 결과와 real backend mutation 결과를 문서에서 반드시 분리한다.
- 실제 backend mutation이 추가될 때마다 이 문서의 `Live Verification Results`와 `Not Implemented Yet`를 갱신한다.
- live verification script 이름에 `smoke`가 남아 있더라도, 이 문서에서는 어떤 부분이 실제 외부 시스템 검증이고 어떤 부분이 stub/synthetic인지 명확히 구분한다.
