# DMS Phase 10 Implementation Prompt

이 문서는 `docs/dms-phase9.md` 완료 이후 열 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 10의 목표는 Phase 9까지 닫은 Kubernetes storage quota Resource Management 다음 단계로, **워커 노드에 host-mounted 된 Ceph filesystem에 대한 Filesystem Resource Management create/delete 최소 lifecycle**을 실제 테스트베드에서 검증하는 것이다.

중요: Filesystem Resource Management는 Kubernetes Pod PVC 안에 mount된 volume directory에 quota를 적용하는 기능이 아니다. `docs/dms-design.md` 기준 filesystem resource는 `storage_name + directory_name`으로 식별하며, dedicated RM Worker node에 host-mounted 된 shared filesystem의 storage root 아래 directory를 관리한다. Phase 10에서는 이 중 create/delete와 create 시 최소 POSIX 접근 경계 검증만 구현하고, quota/update/block/check/sync/action-required 확장은 다음 phase로 넘긴다.

Phase 10은 Data Management `scan/sync/rm`, VolcanoJob live execution, Kubernetes tenant provisioning, long-running RM Worker runtime 배포 검증을 열지 않는다. 이번 phase는 host-mounted filesystem backend의 create/delete side effect를 먼저 구현하고, 검증 스크립트가 기존 phase와 같은 방식으로 `RMWorkerRuntime.run_once()`를 호출해 filesystem backend side effect를 검증한다. long-running RM Worker runtime의 Kubernetes Deployment/loop 운영 검증은 filesystem 기능을 모두 닫은 뒤 별도 phase에서 진행한다.

## Phase 10 목표

Phase 10의 핵심 기능은 다음 여섯 가지다.

1. **c1/c2 host-mounted Ceph RM test targets 확정**
2. **Filesystem live backend adapter create/delete subset**
3. **Safe basename, managed root, ownership marker guard**
4. **Create된 directory의 최소 POSIX access boundary 검증**
5. **DMS-created directory safe delete**
6. **Dual-cluster Ceph host mount live verification without synthetic Agent reports**

구현 완료 기준은 다음과 같다.

- 테스트베드 검증 대상 storage는 `cluster-a`와 `cluster-b` 각각의 worker node에 host-mounted 된 Ceph filesystem으로 한다.
- `cluster-a` target은 `c1-worker` host mount, `cluster-b` target은 `c2-worker` host mount를 사용한다.
- DMS API pod local filesystem과 Kubernetes application PVC mount는 filesystem RM 검증 근거로 사용하지 않는다.
- 실제 DMS Agent report가 각 RM Worker node에서 host mount/readiness evidence를 제출해야 한다.
- storage mapping은 실제 Agent report와 Kubernetes inventory를 기준으로 RM readiness `Ready`가 되어야 한다.
- filesystem create는 host-mounted storage root 아래 안전한 basename directory만 생성한다.
- Phase 10 create는 directory lifecycle과 최소 POSIX permission boundary만 다룬다. finite quota, rename, block, consistency check, quota sync는 구현하지 않는다.
- 기능 검증 시에는 최소 2명 이상의 허용 사용자를 가정하고, 해당 directory에 허용되지 않은 다른 사용자가 접근하지 못하는지 실제 worker node에서 검증한다.
- Linux 계정은 테스트베드 중앙 identity system인 OpenLDAP에서 관리되고 worker node에서는 SSSD/NSS로 조회되어야 한다. 로컬 `/etc/passwd`, `/etc/group`만으로 통과시키는 검증은 허용하지 않는다.
- DMS filesystem create API는 일반 운영 user account를 생성/삭제하지 않는다. 단, 테스트베드에 검증용 LDAP user 수가 부족하면 verification fixture로 DMS phase 전용 LDAP test user를 생성하고 검증 후 삭제할 수 있다.
- DMS filesystem access group 생성과 LDAP user의 group membership 추가/제거는 허용하며, 테스트베드 검증에서 반드시 실제 OpenLDAP write와 SSSD 전파를 확인한다.
- Phase 10에서 `quota`, `acl`, `rename`, `block`, `check`, `sync` 성격의 payload가 들어오면 조용히 무시하지 말고 unsupported로 reject한다.
- delete는 DMS가 생성하고 DMS ownership marker가 확인되는 test directory만 삭제한다.
- live verification은 실제 PostgreSQL, 실제 Kubernetes Agent report, 실제 worker-node host mount를 사용한다.
- 검증 결과는 `docs/dms-phase10-verification.md`와 `docs/dms-done.md`에 command/output 포함해 기록한다.

## 테스트베드 Storage Target 결정

### 1차 target: c1/c2 host-mounted Ceph

Phase 10의 filesystem RM 검증 target은 두 개다.

```text
target: cephfs-a
cluster: cluster-a
rm_worker_node: c1-worker
backend_type: cephfs
host_mount_path: <testbed c1 Ceph host mount path>
managed_root: <host_mount_path>/dms-phase10
```

```text
target: cephfs-b
cluster: cluster-b
rm_worker_node: c2-worker
backend_type: cephfs
host_mount_path: <testbed c2 Ceph host mount path>
managed_root: <host_mount_path>/dms-phase10
```

테스트베드 문서 또는 metadata에 mount path가 명시되어 있으면 그 값을 사용한다. 현재 테스트베드 문서 기준 c1 Rook CephFS host mount path는 `/mnt/testbed-cephfs`, c2 VM-packaged CephFS host mount path는 `/mnt/testbed-cephfs-c2`로 기록되어 있으므로 다음 값을 기본값으로 둔다.

```text
cluster-a/c1-worker: /mnt/testbed-cephfs
cluster-b/c2-worker: /mnt/testbed-cephfs-c2
```

검증 스크립트는 path를 하드코딩하기보다 환경 변수로 override 가능해야 한다.

```text
DMS_PHASE10_C1_CEPH_MOUNT_PATH=/mnt/testbed-cephfs
DMS_PHASE10_C2_CEPH_MOUNT_PATH=/mnt/testbed-cephfs-c2
```

선정 이유:

- `docs/dms-design.md`는 filesystem resource lifecycle을 dedicated RM Worker에 mount된 filesystem에서 수행한다고 정의한다.
- Phase 10은 그 lifecycle 중 directory create/delete 경로만 먼저 닫아 backend execution boundary를 검증한다.
- c1/c2 cluster 각각에 Ceph가 떠 있고, 각 cluster의 worker node host path에 mount되어 있으므로 multi-cluster filesystem RM 검증에 적합하다.
- host-mounted Ceph path는 Kubernetes application PVC 내부 directory가 아니므로 filesystem RM 책임 경계와 맞다.
- Phase 8 이후 storage readiness는 synthetic report가 아니라 실제 DMS Agent report를 사용할 수 있다.

### Kubernetes PVC는 Phase 10 filesystem target에서 제외

Kubernetes CSI `StorageClass`와 smoke PVC는 Ceph health와 Kubernetes storage integration 확인에는 사용할 수 있지만, Phase 10 filesystem RM target은 아니다.

제외 이유:

- Filesystem RM은 RM Worker node host mount에서 수행해야 한다.
- Pod PVC 내부 path는 pod lifecycle과 PVC binding에 종속된다.
- PVC 내부 directory quota 적용은 DMS filesystem resource identity인 `storage_name + directory_name`과 다르게 Kubernetes volume 내부 관리가 된다.
- PVC/PV backend directory import는 추후 `storage_name + pvc_uid` 또는 backend volume id 기반 별도 설계로 다룬다.

### Longhorn은 Phase 10 filesystem target에서 제외

`cluster-b/testbed-longhorn`, `cluster-b/longhorn-static`, `cluster-b/testbed-longhorn-retain`은 Phase 10의 filesystem RM target으로 쓰지 않는다.

제외 이유:

- Longhorn은 block volume 기반 RWO PVC이므로 host-mounted shared filesystem directory lifecycle 검증 대상이 아니다.
- Phase 4~9에서 Longhorn은 Kubernetes `ResourceQuota`와 PVC admission 검증 target으로 이미 사용했다.
- directory quota는 Longhorn 자체 기능이 아니라 PVC 내부 filesystem 또는 Kubernetes quota에 의존하게 되어 DMS filesystem RM 검증 의미가 흐려진다.

### `/shared_directory`는 DMS filesystem RM backend target에서 제외

테스트베드 host shared directory `/shared_directory`도 primary target으로 쓰지 않는다.

제외 이유:

- VirtualBox shared directory는 테스트베드 파일 교환용 host convenience path다.
- Kubernetes CSI storage, datacenter shared filesystem, quota-capable backend를 대표하지 않는다.
- DMS Agent와 Worker가 봐야 하는 storage mapping/inventory/readiness model을 우회할 위험이 있다.

### GPFS는 문서/adapter template만 유지

현재 repository에는 GPFS backend skeleton과 `docs/backend-gpfs.md`가 있지만 테스트베드에는 GPFS/IBM Storage Scale이 설치되어 있지 않다. Phase 10 live verification target은 GPFS가 아니라 host-mounted Ceph다. GPFS adapter는 unit test와 backend abstraction 회귀 검증 대상으로 유지한다.

### Backend 확장성 요구

Phase 10의 live target은 host-mounted CephFS지만, 구현은 CephFS 전용 코드로 core lifecycle에 고정하면 안 된다. 이후 WekaFS, IBM GPFS/Storage Scale, Lustre, BeeGFS, NFS appliance 등 다른 shared filesystem이 추가될 수 있으므로 filesystem RM은 backend adapter/strategy 확장을 전제로 구현한다.

원칙:

- DMS core lifecycle은 `create/update/initialize/block/unblock/check/delete` 같은 operation 의미와 DB state transition만 담당한다. 단, Phase 10 구현 범위는 `create/delete`로 제한한다.
- Filesystem별 command, quota primitive, usage 조회 방식, permission/ACL capability 차이는 backend adapter가 담당한다.
- `storage_name` mapping의 `backend_template.backend_type`으로 adapter를 선택한다.
- adapter는 registry/factory 방식으로 추가되어야 하며, 새 backend 추가가 API schema, planner 공통 lifecycle, repository schema의 큰 변경을 요구하지 않아야 한다.
- CephFS quota xattr 이름인 `ceph.quota.max_bytes`, `ceph.quota.max_files`는 CephFS adapter 내부에만 존재해야 한다.
- GPFS fileset/fileset quota, WekaFS quota, Lustre project quota 등 backend-specific primitive는 각 adapter가 DMS 공통 quota model로 변환한다.
- 공통 quota model은 `quota.capacity_bytes`, `quota.file_count`, `quota.unlimited`를 사용하고, DB에는 backend-neutral desired/applied/observed state를 저장한다.
- adapter는 capability probe 결과를 구조화해서 반환해야 한다. 예: `supports_directory_create`, `supports_capacity_quota`, `supports_file_count_quota`, `supports_usage_bytes`, `supports_file_count_usage`, `supports_permission_mode`, `supports_acl`, `supports_safe_delete`.
- 추후 phase의 finite quota 요청은 해당 backend adapter가 필요한 quota capability를 `Ready`로 보고한 경우에만 실행한다. capability가 없거나 불명확하면 directory side effect 전에 fail-closed한다.
- backend-specific observed metadata는 `observed_state.backend_details` 아래에 저장하고, 공통 query/check/action-required는 backend-neutral field를 우선 사용한다.
- path safety, basename validation, managed root boundary, marker ownership 검증은 공통 guard로 유지하되, backend가 추가 검증을 요구하면 adapter precondition으로 확장한다.

Phase 10에서 구현할 adapter contract:

```text
probe_capability(storage_mapping) -> capability
ensure_managed_root(storage_mapping) -> observed_root
create_directory(storage_mapping, directory_name, marker, access_plan) -> observed_directory
read_directory_state(resource) -> observed_state
delete_directory(resource, delete_policy) -> observed_state
```

Filesystem adapter 자체는 LDAP에 직접 write하지 않는다. LDAP group 생성과 membership 변경은 Phase 10 filesystem create 실행 경로 안에서 identity/group management adapter가 수행하고, filesystem adapter는 SSSD/NSS에 전파된 POSIX group만 사용해 `chgrp`, `chmod`, access validation을 실행한다. 검증 스크립트가 DMS API 밖에서 group을 미리 생성해 성공 조건을 우회하면 안 된다.

추후 phase에서 확장할 adapter contract:

```text
apply_quota(resource, quota_plan) -> observed_quota
clear_or_set_unlimited_quota(resource) -> observed_quota
set_block_state(resource, block_plan) -> observed_permission
read_state(resource, include_usage, include_quota) -> observed_state
```

Phase 10에서는 CephFS adapter만 live create/delete side effect를 검증하더라도, unit test는 adapter registry와 unsupported backend fail-closed path를 포함해야 한다. GPFS skeleton은 이 contract를 만족하는 mock 또는 dry-run adapter 수준으로 유지해 추후 IBM GPFS/Storage Scale 구현 시 core lifecycle 재작업 없이 확장할 수 있어야 한다.

## 현재 전제

Phase 9 완료 후 전제:

- operational PostgreSQL과 observability PostgreSQL 분리 구조가 있다.
- Resource Management request -> plan -> run -> result lifecycle이 있다.
- filesystem API endpoint skeleton은 이미 있다.
  - `POST /api/v1/resource-management/filesystems`
  - `DELETE /api/v1/resource-management/filesystems/{storage_name}/{directory_name}`
  - `PATCH /api/v1/resource-management/filesystems/{storage_name}/{directory_name}`
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:block`
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:initialize`
  - `POST /api/v1/resource-management/filesystems/{storage_name}/{directory_name}:check`
- Phase 10에서 구현할 endpoint는 create와 delete다. 나머지 filesystem endpoint는 후속 phase에서 구현하거나 unsupported로 명확히 응답한다.
- 현재 filesystem adapter는 stub 또는 GPFS skeleton 중심이며 실제 filesystem side effect를 수행하지 않는다.
- DMS Agent DaemonSet은 mount evidence를 API에 제출할 수 있다.
- storage mapping sanity는 Agent report와 Kubernetes inventory를 결합한다.
- Phase 9까지 Kubernetes storage quota lifecycle과 action-required aggregation은 실제 테스트베드에서 검증됐다.
- long-running RM Worker runtime code path는 존재하지만, Kubernetes Deployment로 배포된 운영형 loop를 통해 live verification한 상태는 아니다.

테스트베드 topology:

- `cluster-a`
  - c1 cluster
  - worker node: `c1-worker`
  - Ceph host mount target: `cephfs-a`
- `cluster-b`
  - c2 cluster
  - worker node: `c2-worker`
  - Ceph host mount target: `cephfs-b`
- PostgreSQL
  - `192.168.56.11:30432`
  - 테스트 실행마다 operational DB와 observability DB를 새로 만든다.

## 기능 1: Host-Mounted Ceph RM Worker Targets

Phase 10 verifier는 Kubernetes PVC를 filesystem RM target으로 만들지 않는다. 대신 각 cluster의 worker node host mount를 확인한다.

Verifier가 먼저 확인할 조건:

```bash
ssh c1-worker "findmnt ${DMS_PHASE10_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}"
ssh c1-worker "stat -f -c '%T' ${DMS_PHASE10_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}"
ssh c1-worker "test -w ${DMS_PHASE10_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}"

ssh c2-worker "findmnt ${DMS_PHASE10_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}"
ssh c2-worker "stat -f -c '%T' ${DMS_PHASE10_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}"
ssh c2-worker "test -w ${DMS_PHASE10_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}"
```

권장 DMS-managed root:

```text
<mount_path>/dms-phase10
```

권장 storage mapping:

```json
{
  "storage_name": "cephfs-a",
  "backend_template": {
    "backend_type": "cephfs",
    "cluster_name": "cluster-a",
    "mount_path": "/mnt/testbed-cephfs",
    "managed_root": "/mnt/testbed-cephfs/dms-phase10",
    "rm_worker_nodes": ["c1-worker"]
  },
  "cluster_name": "cluster-a",
  "storage_class_name": "testbed-cephfs"
}
```

```json
{
  "storage_name": "cephfs-b",
  "backend_template": {
    "backend_type": "cephfs",
    "cluster_name": "cluster-b",
    "mount_path": "/mnt/testbed-cephfs-c2",
    "managed_root": "/mnt/testbed-cephfs-c2/dms-phase10",
    "rm_worker_nodes": ["c2-worker"]
  },
  "cluster_name": "cluster-b",
  "storage_class_name": null
}
```

규칙:

- DMS filesystem adapter는 `managed_root` 아래만 변경한다.
- `directory_name`은 basename만 허용한다.
- `.`/`..`, slash, backslash, null byte, leading dash, empty string, overly long name은 reject한다.
- symlink traversal은 금지한다.
- `realpath(candidate)`가 `realpath(managed_root)` 아래인지 확인한다.
- delete는 DMS ownership marker가 있는 directory만 허용한다.

권장 DMS ownership marker:

```text
.dms-resource.json
```

예시:

```json
{
  "managed_by": "dms",
  "resource_kind": "filesystem",
  "resource_key": "cephfs-a:project-alpha",
  "storage_name": "cephfs-a",
  "directory_name": "project-alpha"
}
```

## 기능 2: Filesystem Live Backend Adapter

권장 구현:

```text
src/dms/backends/cephfs.py
```

권장 adapter:

```text
CephFsHostMountedFilesystemBackendAdapter
```

Phase 10의 testbed execution mode는 host-mounted filesystem을 가진 worker node에서 command wrapper를 실행하는 방식으로 한다.

권장 execution mode:

```text
DMS_FILESYSTEM_MUTATION_MODE=ssh-host-exec
```

필요한 설정 또는 mapping field:

```text
DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS=30
backend_template.mount_path=<host mount path>
backend_template.managed_root=<host mount path>/dms-phase10
backend_template.rm_worker_nodes=["c1-worker" | "c2-worker"]
```

이 방식의 이유:

- API pod local filesystem을 관찰하지 않는다.
- Kubernetes application PVC mount를 filesystem RM target으로 쓰지 않는다.
- dedicated RM Worker node의 host-mounted filesystem에서 directory operation을 수행한다.
- `docs/dms-design.md`의 “RM Worker에 mount된 filesystem에서 Resource Management 작업 실행” 경계를 따른다.

Production 배포에서는 `ssh-host-exec` 대신 RM Worker runtime이 해당 node에서 직접 command wrapper를 실행하거나, host mount를 `hostPath`로 붙인 node-local RM Worker workload에서 실행할 수 있다. Phase 10은 filesystem backend 기능 검증이 목표이므로 long-running RM Worker Deployment를 아직 요구하지 않는다.

Adapter가 수행해야 하는 command는 structured wrapper로 제한한다. raw shell string을 plan payload에서 직접 실행하면 안 된다.

권장 command allowlist:

- `mkdir`
- `stat`
- `chmod`
- `chgrp`
- `find`
- `rm`
- `realpath`
- `id`

## 기능 3: Agent / Storage Mapping Readiness

Phase 10은 synthetic Agent report를 쓰지 않는다.

Agent evidence는 각 cluster worker node의 실제 host mount를 확인해야 한다.

권장 방식:

- Agent DaemonSet이 `c1-worker`와 `c2-worker`의 Ceph host mount path를 같은 path로 `hostPath` mount한다.
- Agent는 configured storage path에 대해 `stat`, `stat -f`, optional read/write probe를 수행한다.
- report에는 storage별 `cluster_name`, `storage_name`, `node_name`, `mount_path`, filesystem type, read/write probe 결과를 남긴다.

Agent report 예시:

```json
{
  "storage_name": "cephfs-a",
  "cluster_name": "cluster-a",
  "mount_path": "/mnt/testbed-cephfs",
  "status": "Ready",
  "filesystem_type": "ceph",
  "source": "host-mounted-rm-worker",
  "node_name": "c1-worker",
  "read_probe": "ok",
  "write_probe": "ok"
}
```

Planner guard:

- filesystem RM operation은 `readiness.resource_management=Ready`가 아니면 reject한다.
- readiness는 API pod local path나 testbed `/shared_directory`로 보완하지 않는다.
- CephFS smoke PVC 성공은 Ceph health evidence일 뿐 filesystem RM readiness가 아니다.

## 기능 4: Filesystem Create

### API

기존 filesystem create API를 사용한다.

```text
POST /api/v1/resource-management/filesystems
```

예시:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "storage_name": "cephfs-a",
    "directory_name": "project-alpha",
    "resource_type": "user",
    "users": ["alice", "bob"],
    "access_group": "dms-phase10-project-alpha",
    "expires_at": "2026-06-30T00:00:00Z",
    "memo": "phase10 filesystem create"
  }
}
```

Planner validation:

- storage mapping이 존재해야 한다.
- mapping sanity/readiness 중 RM readiness가 `Ready`여야 한다.
- backend type이 Phase 10 지원 대상이어야 한다.
- mapping에 `mount_path`, `managed_root`, RM worker node evidence가 있어야 한다.
- directory name이 safe basename이어야 한다.
- `storage_name + directory_name` resource가 이미 Active면 reject한다.
- `users`는 최소 2명 이상이어야 하며, Phase 10에서는 테스트베드 OpenLDAP에 이미 존재하는 LDAP user로 resolve되어야 한다.
- DMS create API는 user account를 생성하거나 삭제하지 않는다. 요청된 user가 LDAP에 없으면 fail-closed한다. 테스트용 user가 부족한 경우에는 API 호출 전 verification fixture 단계에서 OpenLDAP에 DMS phase 전용 test user를 생성한다.
- `access_group`이 없으면 DMS가 `dms-phase10-<directory_name>` 형식의 DMS-managed LDAP group name을 derive한다.
- `access_group`은 OpenLDAP `ou=groups,dc=testbed,dc=local` 아래 `posixGroup`으로 생성되거나, 이미 DMS marker/metadata가 있는 test group이면 재사용될 수 있다.
- create 실행 전 또는 실행 중 LDAP group membership에 요청된 `users`를 추가하고, worker node SSSD/NSS에서 `getent group <access_group>` 및 `id <user>`로 전파를 확인해야 한다.
- 비허용 검증용 user는 create payload에 넣지 않고 verification config로 지정한다. 이 user는 LDAP에는 존재해야 하지만 `access_group` member가 아니어야 한다.
- `expires_at`은 Phase 10에서 resource metadata로 저장하되, 만료 평가나 만료에 따른 block/delete side effect는 수행하지 않는다. expiry 정책은 Phase 10 다음 phase에서 구현한다.
- `quota`, `acl`, `rename`, `block`, `check`, `sync` 등 Phase 10 범위를 넘는 field 또는 operation은 unsupported로 reject한다.

Worker behavior:

1. target storage의 RM worker node에서 `managed_root` 존재 여부를 확인하고 없으면 생성한다.
2. OpenLDAP에서 DMS-managed access group을 생성하거나 기존 DMS-managed group을 확인한다.
3. 요청된 LDAP users를 access group membership에 추가한다.
4. target worker node에서 SSSD/NSS lookup으로 group과 membership 전파를 확인한다.
5. target directory가 이미 있으면 ownership marker를 확인한다.
6. 없으면 `mkdir`로 생성한다.
7. `.dms-resource.json` marker를 기록한다.
8. LDAP/SSSD로 확인된 POSIX group을 directory group ownership에 적용한다.
9. group member만 접근 가능하도록 permission mode를 적용한다. Phase 10 기본값은 `0770` 또는 운영 정책상 read-only가 필요하면 `0750`이다.
10. 최소 2명의 허용 사용자가 directory에 접근 가능한지 확인한다.
11. 허용되지 않은 검증용 user가 directory에 접근할 수 없는지 확인한다.
12. `stat`, `find`, `getent`, `id`로 observed state를 읽는다.
13. result와 resource observed state를 operational DB에 저장한다.

Observed state는 최소한 다음을 포함한다.

```json
{
  "adapter": "cephfs-host-mounted",
  "cluster_name": "cluster-a",
  "storage_name": "cephfs-a",
  "directory_name": "project-alpha",
  "node_name": "c1-worker",
  "path": "/mnt/testbed-cephfs/dms-phase10/project-alpha",
  "exists": true,
  "owner_uid": 0,
  "group_name": "dms-phase10-project-alpha",
  "group_gid": 20010,
  "identity_source": "openldap-sssd",
  "ldap_group_dn": "cn=dms-phase10-project-alpha,ou=groups,dc=testbed,dc=local",
  "ldap_members": ["alice", "bob"],
  "sssd_membership_observed": {
    "alice": ["dms-phase10-project-alpha"],
    "bob": ["dms-phase10-project-alpha"]
  },
  "mode": "0770",
  "access_validation": {
    "allowed_users": {
      "alice": "ok",
      "bob": "ok"
    },
    "denied_users": {
      "mallory": "denied"
    }
  }
}
```

## 기능 5: Filesystem Delete

### API

```text
DELETE /api/v1/resource-management/filesystems/{storage_name}/{directory_name}
```

예시:

```json
{
  "requester_id": "portal:ops",
  "payload": {
    "reason": "phase10 cleanup"
  }
}
```

규칙:

- DMS가 생성한 resource만 delete한다.
- `.dms-resource.json` marker가 없거나 resource key가 다르면 reject한다.
- Phase 10 verifier는 테스트 directory에 큰 데이터를 만들지 않는다.
- 기본 delete는 directory가 DMS marker 외에 비어 있거나 verifier가 만든 작은 test file만 있는 경우로 제한한다.
- non-empty delete, recursive production delete, trash/quarantine flow는 Phase 10 범위가 아니다.
- delete 후 worker node에서 target path가 사라졌는지 확인하고 resource lifecycle state를 `Deleted`로 기록한다.

## 구현 단계

### Step 1: Testbed Host Mount Audit

- c1/c2 cluster 각각에 Ceph가 떠 있고 worker node host path에 mount되어 있는지 확인한다.
- mount path, filesystem type, read/write probe 결과를 기록한다.
- OpenLDAP `slapd`와 worker node SSSD가 정상인지 확인한다.
- 접근 검증에 사용할 LDAP user를 확인한다. 최소 2명은 허용 사용자, 최소 1명은 비허용 사용자로 둔다.
- 테스트베드에 허용 2명과 비허용 1명을 만족하는 LDAP user가 없으면 로컬 계정으로 대체하지 말고, OpenLDAP에 DMS phase 전용 test user fixture를 생성해 검증한다.
- fixture user는 `dms-phase10-*` 같은 명확한 prefix, 테스트 전용 uid/gid range, `/bin/bash` shell, `/home/<user>` home을 사용하고, `docs/dms-phase10-verification.md`에 LDIF와 cleanup 결과를 기록한다.
- fixture user는 검증 완료 후 삭제한다. 기존 seeded user인 `alice`, `bob` 등 non-DMS LDAP user는 생성/삭제/rename하지 않는다.
- 테스트베드 문서와 실제 mount path가 다르면 verifier는 실제 값을 우선하고 `docs/dms-phase10-verification.md`에 명확히 남긴다.
- 필요한 package를 설치했다면 `/home/mason/workspace/testbed` 문서에 기록한다.

### Step 2: Backend and Settings

- `src/dms/backends/cephfs.py`를 추가한다.
- `CephFsHostMountedFilesystemBackendAdapter`를 구현한다.
- `Settings`에 filesystem host exec 설정을 추가한다.
- `BackendAdapterRegistry.filesystem_for_plan()`에서 `backend_type=cephfs` mapping을 CephFS adapter로 연결한다.
- command execution은 list argv 기반으로만 수행하고 shell string은 금지한다.

### Step 3: Agent / Storage Mapping

- Agent가 host-mounted path readiness를 제출할 수 있게 deployment/config를 보강한다.
- storage mapping sanity가 `cephfs-a`, `cephfs-b`의 RM readiness를 실제 Agent report 기준으로 계산하는지 확인한다.
- API pod local path나 PVC mount evidence를 readiness로 사용하지 않는다.

### Step 4: Planner Validation

- filesystem operation validation을 Phase 10 수준으로 강화한다.
- storage mapping missing/failed/disabled/unknown은 fail-closed한다.
- directory name safety를 Planner와 adapter 양쪽에서 검증한다.
- create payload의 `users`는 최소 2명 이상이어야 하며 OpenLDAP user로 resolve되고 target worker node SSSD/NSS에서도 POSIX identity로 조회되어야 한다.
- DMS-managed LDAP access group 이름을 validate한다.
- verification config의 denied user가 OpenLDAP에는 존재하지만 access group에 속하지 않는지 확인한다.
- unsupported payload field는 조용히 무시하지 말고 reject한다.

### Step 5: LDAP Group Membership Management

- filesystem create 실행 경로가 테스트베드 OpenLDAP에 DMS-managed `posixGroup`을 생성해야 한다.
- group name은 `dms-phase10-<directory_name>` 또는 요청 payload의 `access_group`을 사용하되 safe group name만 허용한다.
- group은 `ou=groups,dc=testbed,dc=local` 아래에 만들고, `gidNumber`는 테스트베드 충돌을 피하는 DMS phase 전용 range에서 할당한다.
- `memberUid`에는 create payload의 기존 LDAP users만 추가한다.
- DMS create flow는 account 생성/삭제, user DN rename, password 변경을 수행하지 않는다. 테스트 user fixture 생성/삭제는 verification setup/cleanup 단계에서만 허용한다.
- LDAP write 후 `ldapsearch`로 group object와 `memberUid`를 확인한다.
- `c1-worker`, `c2-worker`에서 SSSD cache를 refresh 또는 invalidate한 뒤 `getent group <group>`과 `id <user>`로 membership 전파를 확인한다.
- cleanup은 Phase 10에서 생성한 DMS-managed test group과 DMS phase 전용 test user fixture에 한해 membership 제거 또는 group/user 삭제를 허용한다. 기존 LDAP group이나 seeded user account는 수정하지 않는다.

### Step 6: Worker Evidence

- RM Worker runtime은 검증 스크립트에서 `run_once()`로 실행한다. Kubernetes에 long-running `dms-rm-worker` Deployment를 배포해 loop로 처리하는 것은 Phase 10 범위가 아니다.
- RM Worker가 filesystem operation started/completed/failed diagnostic event를 observability DB에 기록하게 한다.
- applied/observed state에는 command result, cluster name, node name, path, marker, LDAP group DN, POSIX group, permission, allowed user access result, denied user access result를 포함한다.
- backend side effect 이후 terminal result commit 실패 시 recovery/action-required 대상이 되도록 기존 transaction boundary를 유지한다.

### Step 7: Delete Lifecycle

- delete는 DMS-created resource만 허용한다.
- `.dms-resource.json` marker가 없거나 resource key가 다르면 reject한다.
- delete 후 target path가 없어졌는지 worker node에서 확인하고 resource lifecycle state를 `Deleted`로 기록한다.
- delete cleanup은 DMS가 Phase 10에서 생성한 LDAP access group에 한해 membership 제거 또는 group 삭제를 수행할 수 있다.
- marker mismatch, unmanaged existing directory, non-empty production directory recursive delete는 Phase 10 범위 밖으로 둔다.

### Step 8: Testbed Verification Script

새 verification script를 추가한다.

```text
scripts/phase10_ceph_host_filesystem_rm.py
scripts/verify-phase10-testbed.sh
```

검증 전 테스트베드 metadata를 확인한다.

```bash
cat /home/mason/workspace/testbed/testbed-info.json
cat /home/mason/workspace/testbed/testbed-summary.json
rg -n "ceph|mount|/mnt" /home/mason/workspace/testbed
```

권장 검증 흐름:

1. fresh operational/observability PostgreSQL DB를 만든다.
2. `c1-worker`와 `c2-worker`의 Ceph host mount를 확인한다.
3. 각 target에서 host mount read/write probe를 실행한다.
4. DMS API와 Agent를 배포한다.
5. actual Agent report가 `cephfs-a`, `cephfs-b` host mount evidence를 제출하는지 확인한다.
6. storage mapping `cephfs-a`, `cephfs-b`를 등록하고 RM readiness `Ready`를 확인한다.
7. 테스트베드 OpenLDAP에서 허용 사용자 최소 2명과 비허용 사용자 최소 1명을 확인한다.
8. LDAP user fixture가 부족하면 DMS phase 전용 test user를 OpenLDAP에 생성하고, worker node SSSD/NSS에서 조회되는지 확인한다.
9. 허용 사용자 2명 이상을 포함한 create request를 실행한다. 이 request 처리 과정에서 DMS가 DMS-managed LDAP access group을 만들고 허용 사용자만 `memberUid`로 추가해야 한다.
10. `ldapsearch`로 group object와 membership을 확인한다.
11. `c1-worker`, `c2-worker`에서 SSSD/NSS로 group과 membership이 조회되는지 확인한다.
12. worker node에서 directory, marker, LDAP-derived group ownership, permission mode를 확인한다.
13. 허용 사용자 2명 이상이 directory에 `cd`, `touch`, `rm` 같은 최소 read/write/execute 작업을 할 수 있는지 확인한다.
14. 비허용 사용자는 directory에 `cd`, `touch`, `ls` 등 접근이 거부되는지 확인한다.
15. delete API로 DMS-owned test directory만 삭제한다.
16. delete 후 target path가 없어졌는지 확인한다.
17. DMS-managed LDAP access group과 fixture user cleanup을 수행하고, seeded user account가 변경되지 않았음을 확인한다.
18. 테스트 directory를 cleanup하되 Ceph host mount 자체는 테스트베드 구성으로 유지한다.

테스트베드 리소스가 부족하므로 접근 검증은 작은 marker/test file 몇 개로 제한한다.

## Required Command Evidence

verification 문서에는 최소한 다음 output을 남긴다.

```bash
ssh ldap "systemctl is-active slapd"
ssh ldap "ldapsearch -x -LLL -H ldap://127.0.0.1 -D cn=admin,dc=testbed,dc=local -w <ldap-admin-password> -b ou=people,dc=testbed,dc=local '(|(uid=<allowed-user-1>)(uid=<allowed-user-2>)(uid=<denied-user>))' uid uidNumber gidNumber"
# If seeded LDAP users are insufficient, include ldapadd/ldapdelete evidence for DMS phase-scoped fixture users.
ssh ldap "ldapadd -x -H ldap://127.0.0.1 -D cn=admin,dc=testbed,dc=local -w <ldap-admin-password> -f <dms-phase10-user-fixture.ldif>"
ssh ldap "ldapsearch -x -LLL -H ldap://127.0.0.1 -D cn=admin,dc=testbed,dc=local -w <ldap-admin-password> -b ou=people,dc=testbed,dc=local '(uid=dms-phase10-*)' uid uidNumber gidNumber"
ssh ldap "ldapdelete -x -H ldap://127.0.0.1 -D cn=admin,dc=testbed,dc=local -w <ldap-admin-password> uid=<dms-phase10-fixture-user>,ou=people,dc=testbed,dc=local"
ssh ldap "ldapsearch -x -LLL -H ldap://127.0.0.1 -D cn=admin,dc=testbed,dc=local -w <ldap-admin-password> -b ou=groups,dc=testbed,dc=local '(cn=<dms-access-group>)' cn gidNumber memberUid"
ssh c1-worker "systemctl is-active sssd && getent group <dms-access-group> && id <allowed-user-1> && id <allowed-user-2> && id <denied-user>"
ssh c2-worker "systemctl is-active sssd && getent group <dms-access-group> && id <allowed-user-1> && id <allowed-user-2> && id <denied-user>"

ssh c1-worker "findmnt ${DMS_PHASE10_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}"
ssh c1-worker "stat -f -c '%T' ${DMS_PHASE10_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}"
ssh c1-worker "mkdir -p ${DMS_PHASE10_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}/dms-phase10/.probe && touch ${DMS_PHASE10_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}/dms-phase10/.probe/write-test"
ssh c1-worker "getent group <dms-access-group> && id <allowed-user-1> && id <allowed-user-2> && id <denied-user>"
ssh c1-worker "sudo -u <allowed-user-1> test -x <created-directory> && sudo -u <allowed-user-1> touch <created-directory>/.access-allowed-1"
ssh c1-worker "sudo -u <allowed-user-2> test -x <created-directory> && sudo -u <allowed-user-2> touch <created-directory>/.access-allowed-2"
ssh c1-worker "sudo -u <denied-user> test ! -x <created-directory> && sudo -u <denied-user> test ! -w <created-directory>"

ssh c2-worker "findmnt ${DMS_PHASE10_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}"
ssh c2-worker "stat -f -c '%T' ${DMS_PHASE10_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}"
ssh c2-worker "mkdir -p ${DMS_PHASE10_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}/dms-phase10/.probe && touch ${DMS_PHASE10_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}/dms-phase10/.probe/write-test"
ssh c2-worker "getent group <dms-access-group> && id <allowed-user-1> && id <allowed-user-2> && id <denied-user>"
ssh c2-worker "sudo -u <allowed-user-1> test -x <created-directory> && sudo -u <allowed-user-1> touch <created-directory>/.access-allowed-1"
ssh c2-worker "sudo -u <allowed-user-2> test -x <created-directory> && sudo -u <allowed-user-2> touch <created-directory>/.access-allowed-2"
ssh c2-worker "sudo -u <denied-user> test ! -x <created-directory> && sudo -u <denied-user> test ! -w <created-directory>"
```

API evidence:

```bash
curl -s -H 'x-dms-actor: api-client' \
  -X POST http://127.0.0.1:8000/api/v1/resource-management/filesystems \
  -d '<filesystem create payload>'

# The create result must include LDAP group create/modify evidence:
# identity_source=openldap-sssd, ldap_group_dn, ldap_members, sssd_membership_observed.

curl -s -H 'x-dms-actor: api-client' \
  -X DELETE http://127.0.0.1:8000/api/v1/resource-management/filesystems/cephfs-a/<directory> \
  -d '<filesystem delete payload>'
```

## Local Tests

Unit/API tests:

- directory name validation rejects unsafe names.
- filesystem create rejects missing mapping.
- filesystem create rejects mapping without RM readiness.
- filesystem create rejects missing host mount evidence.
- filesystem create rejects fewer than two users in Phase 10 verification profile.
- filesystem create rejects users that do not resolve through OpenLDAP and target worker node SSSD/NSS.
- filesystem create creates or verifies a DMS-managed LDAP access group for the resource.
- filesystem create adds requested existing LDAP users to the DMS-managed access group.
- filesystem create rejects account creation/deletion attempts.
- filesystem create rejects quota payload in Phase 10.
- filesystem create records marker and observed state with live adapter fake.
- filesystem create records allowed user access result and denied user access result.
- filesystem update/initialize/block/check endpoints return explicit unsupported response in Phase 10.
- delete refuses marker mismatch.
- delete removes only DMS-created test directory and records `Deleted` lifecycle state.

Live tests:

- `c1-worker` host-mounted Ceph target.
- `c2-worker` host-mounted Ceph target.
- actual Agent report based storage mapping readiness.
- no synthetic Agent report usage.
- no API pod local filesystem observation.
- no Kubernetes application PVC directory quota path.
- Longhorn is not used as filesystem RM target.
- OpenLDAP group creation and membership update are performed by the DMS create flow and verified with operation result evidence plus `ldapsearch`.
- SSSD membership propagation is verified on `c1-worker` and `c2-worker` with `getent`/`id`.
- no local-only `/etc/passwd` or `/etc/group` fixture is accepted as success evidence.
- if seeded LDAP users are insufficient, DMS phase-scoped LDAP test users are created and deleted as verification fixtures with `ldapadd`/`ldapdelete` evidence.
- no seeded or non-DMS LDAP user account is created, deleted, renamed, or modified.
- at least two allowed users can access the created directory.
- at least one denied user cannot access the created directory.
- no long-running `dms-rm-worker` Deployment/loop execution in Phase 10.

## Phase 10에서 하지 않을 것

다음은 Phase 10 범위가 아니다.

- Kubernetes Pod PVC 내부 directory quota 적용
- filesystem quota create/update/initialize
- filesystem block/unblock
- filesystem consistency check/sync
- filesystem drift/usage pressure action-required aggregation
- long-running RM Worker runtime Kubernetes Deployment/loop 운영 검증
- Data Management `scan/sync/rm` live execution
- VolcanoJob create/watch/terminate
- Longhorn filesystem directory lifecycle 검증
- GPFS/IBM Storage Scale live verification
- production recursive delete or trash/quarantine workflow
- nested CSI backend directory quota management
- Kubernetes PVC backend volume import by PVC UID
- POSIX ACL full management
- 일반 운영 LDAP user account 생성/삭제
- seeded/non-DMS LDAP user account 변경
- 기존 non-DMS LDAP group 변경
- cross-cluster filesystem replication
- automatic expiration sweep execution
- quota drift/usage pressure cron sweep
- production Helm/Kustomize packaging 완성
- trusted ingress mTLS live validation

## Phase 10 완료 후 다음 Phase 후보

Phase 10이 성공하면 DMS는 Kubernetes storage quota RM과 c1/c2 host-mounted Ceph filesystem RM create/delete 최소 lifecycle, 그리고 create된 directory의 기본 POSIX 접근 경계를 실제 테스트베드에서 검증한 상태가 된다.

### Phase 11A: Resource Expiry Policy

Phase 10 다음 phase에서는 filesystem resource의 `expires_at`을 실제 운영 정책으로 연결한다.

- create 시 저장된 `expires_at` metadata를 기준으로 expired/expiring resource를 조회한다.
- 운영자가 API로 호출하는 on-demand expiration sweep을 먼저 구현한다. 초기 구현은 cron/controller처럼 자동 주기 실행하지 않는다.
- sweep은 만료된 filesystem resource를 찾아 resource별 child request 또는 plan을 생성한다.
- 만료된 일반 resource는 `block=ON` 처리 대상으로 전환한다. 따라서 이 phase에는 filesystem block/unblock 최소 구현도 함께 포함한다.
- `type=system`, `type=admin` resource는 자동 block하지 않고 skip/failure reason을 결과에 기록한다.
- 이미 blocked 상태인 만료 resource는 중복 block하지 않고 현재 block state를 유지한다.
- expiration sweep result에는 대상 resource 수, 생성된 child request, skip/failure reason, requester id, sweep timestamp를 기록한다.
- Operational Query API에 expired/expiring filesystem resource 조회와 expiration sweep skip/failure action-required 항목을 추가한다.
- 테스트베드에서는 짧은 `expires_at`을 가진 filesystem resource를 만들고, API로 sweep을 호출해 block 전환과 query 결과를 실제 검증한다.

### Phase 11B: Filesystem RM Completion

- existing directory quota assignment
- import existing filesystem directory
- filesystem quota create/update/initialize
- filesystem consistency check/sync
- filesystem quota drift/usage pressure action-required 확장
- POSIX owner/group/permission 정책 보강
- quota capability가 target별로 다른 경우의 운영 정책 정리

### Phase 11C: Long-Running RM Worker Runtime Deployment

이 phase는 filesystem 기능이 충분히 닫힌 뒤 진행한다.

- `dms rm-worker --loop`가 settings 기반 live adapter를 사용하도록 wiring 보강
- `deploy/kubernetes/managed-cluster-rm-worker.yaml`을 테스트베드 c1/c2 cluster에 맞게 적용
- `dms.io/worker-role=rm` node label 또는 nodeSelector/affinity 정책 정리
- RM Worker runtime과 Agent가 같은 dedicated RM Worker node에서 host mount capability를 공유하도록 배포
- planner/API/worker를 Kubernetes workload로 배포하고, verification script가 직접 `run_once()`를 호출하지 않아도 plan claim부터 result commit까지 처리되는지 검증
- worker lease, stale claim, restart recovery, `UnknownAfterSideEffect` action-required를 실제 loop에서 검증

### Phase 11D: Data Management Read-only Scan Preflight

- 실제 DM Agent report 기반 candidate pool 사용
- LDAP identity mapping과 POSIX permission preflight 연결
- Phase 10/11 filesystem resource boundary를 scan target으로 사용
- read-only `scan` request/job preflight를 실제 runtime evidence로 검증

권장 순서는 Phase 11A 이후 Phase 11B, 그 다음 Phase 11C다. Filesystem RM 기능이 완성되기 전에 long-running RM Worker runtime을 먼저 닫으면, runtime 배포 문제와 backend 기능 결함이 섞여 장애 원인 분리가 어려워진다. Filesystem 기능을 먼저 충분히 검증한 뒤, 같은 기능을 long-running RM Worker Deployment 경로로 재검증하는 순서가 더 안전하다.
