# DMS Resource Management API 검증 Handoff (2026-06-09 ~ 06-10)

## 환경 요약

| 항목 | 값 |
|---|---|
| Control plane | pvs-dms 클러스터 (ion2401~2406, k8s v1.35.5, RHEL 9.4, CRI-O, Cilium) |
| 작업 노드 | rts2411 (claude code runner, internet via `localhost:7227` proxy, local registry `rts2411:5000`) |
| Storage backends | GPFS (`/pvs`, ion2404~2406 마운트), CephFS (`/mgmt_storage`), WekaFS (미설치) |
| LDAP | **로컬 OpenLDAP** `ldap://75.23.118.45:3389` (rts2411 docker, CephFS 영구저장) |
| 서비스 LDAP | `ldap://75.23.118.14` (read-only, sync 소스만, 직접 사용 안 함) |
| DMS API | `https://dms.pvs-dms.local:30535` (NodePort) |
| 환경변수 | `source /mgmt_storage/cocoa.song/.dms-secrets/dms-env.sh` (DMS_CURL_OPTS 포함) |
| 현재 이미지 | `rts2411:5000/dms:b7eb1e6` 이상 (작업 진행에 따라 갱신) |

**kubectl 사용**: `NO_PROXY=75.23.118.25,$NO_PROXY KUBECONFIG=~/.kube/config-pvs-dms /mgmt_storage/cocoa.song/dms/install/docker/kubectl ...`

## 완료된 검증 (커밋 순서)

### 1. GPFS Resource Management 기초 동작 (`fac70ac`~`56da159`)
- **SSH PATH 주입**: `SshGpfsCommandExecutor`가 `PATH=/usr/lpp/mmfs/bin:$PATH` 자동 주입
- **GID 범위**: 9000000~9999999 (기존 시스템 GID 최대 901437과 충돌 없음)
- **LDAP referral**: `auto_referrals=False` (ldap3 2.9.1 호환)
- **GPFS 8 MiB rounding**: `render_gpfs_block_limit`과 `_quota_readback_issue` 모두 8 MiB(8192 KiB) boundary 정렬

### 2. 로컬 OpenLDAP 셋업 (`9f334a0`)
- rts2411에서 `osixia/openldap:1.5.0` 컨테이너 실행 (포트 3389)
- 11,180명 + 448개 그룹 sync 완료 (`install/scripts/sync-ldap-to-local.py`, ~2분 30초)
- `cn=admin,dc=supercom,dc=samsung` / `dms-ldap-admin` (admin)
- **OS SSSD 주의**: ion 노드 OS는 서비스 LDAP을 봐서 `dms-grp-*` 그룹을 모름 → `sudo -u user touch` 방식 access probe는 신뢰 불가

### 3. LDAP 그룹 관리 정책
- **그룹 prefix**: `dms-grp-{directory_name}` (이전 `dms-access-group-`에서 변경, `4b9c1bd`)
- 코드 강제: `_require_dms_group_name()` (`dms-` 시작), 백엔드 모두 `delete_group` 전 prefix guard
- 사용자 OU 쓰기 절대 금지 (코드에 없음)

### 4. Filesystem Resource API 동작 검증

| 엔드포인트 | 상태 | 비고 |
|---|---|---|
| `POST /filesystems` | ✓ | quota + LDAP group + GPFS fileset 한번에 처리 |
| `PATCH /filesystems/{s}/{d}` | 미검증 | quota 변경 |
| `POST /filesystems/{s}/{d}:block` | ✓ | `chmod 0000`, `block: true` 자동 주입 (`e4b03c3`) |
| `POST /filesystems/{s}/{d}:initialize` | ✓ | 차단 해제, **`FILESYSTEM_BLOCK + block: false`로 라우팅** (`6e8d48f`) — `block_state.previous_mode` 없으면 `desired_state.mode` fallback (`50a8c20`) |
| `DELETE /filesystems/{s}/{d}` | ✓ | **soft-delete**: chown root:root + chmod 000, fileset Linked 유지, mmunlinkfileset 안 함, mmdelfileset 안 함, LDAP 그룹 삭제 (`bfd1168`) |
| `GET /operations/filesystems/{s}/{d}` | ✓ | 단건 조회 (구현 추가, `7b1685a`) |
| `GET /operations/filesystems/{s}` | ✓ | storage별 목록 |
| `GET /operations/filesystems/expiring` | ✓ | 만료/만료예정 |

### 5. Stuck Request 해소 API (`e4e9ec7`)
- `POST /api/v1/resource-management/requests/{id}:resolve`
- `resolution: "abandon"` → `Failed`, `"succeeded"` → `Succeeded`
- `UnknownAfterSideEffect`/`BackendApplyFailed`만 처리 가능, `reason` 필수

### 6. 코드 최적화
- **GPFS `_capability` SSH 단일화** (`1ebdece`): `command -v` 7회 + `mmlsfs` 2회 → 각 1회
- **access probe 개선** (`1ebdece`): `sudo -u touch` (실패) → `stat + getent group` 방식 (정확)
- **fileset_root → managed_root 통합** (`9c46519`): GPFS 백엔드 필드명
- **cephfs/weka delete_group guard 통일** (`13cd3a6`): `dms-` prefix 검사 추가

### 7. action-required 정리
- **agent_report_stale 중복 제거** (`4c97bb9`): `(node_name, worker_role, cluster_name)` 별 최신 1건만, Fresh 리포트 있으면 자동 숨김, `reported_at` 추가
- **filesystem_soft_deleted 노출** (`d922058`): soft-delete된 resource를 INFO severity로 표시

### 8. 잡다한 수정
- `FILESYSTEM_INITIALIZE` planner validation 추가 (`df5a106`)
- `FILESYSTEM_DELETE` 후 resource status `Deleted` 자동 설정 (`b7eb1e6`) — 이전엔 `Succeeded`로 남는 버그
- `:block`/`:initialize` payload 자동 주입 (`e4b03c3`)
- `expiring` 라우트 충돌 해결 — `expiring`이 `{storage_name}` 패턴보다 먼저 등록되도록

## 현재 등록된 storage mappings

| storage_name | backend_type | 상태 | 비고 |
|---|---|---|---|
| `gpfs-pvs-dms` | gpfs | Ready | filesystem_name=pvs, mount_path=/pvs, managed_root=/pvs/dms, ssh_host=75.23.118.28 (ion2404 IP) |
| `cephfs-pvs-dms` | cephfs | Degraded | DM 미활성으로 정상 |
| `wekafs-pvs-dms` | gpfs(설정오류, 실 wekafs) | Degraded | WEKA CSI 미설치 |

## 확인된 제약 / 주의사항

### LDAP
- **pod 내부에서 호스트명 DNS 해석 안 됨**: `ldapm`, `ldaps` 등 모두 실패. **반드시 IP 사용** (`ldap://75.23.118.45:3389`)
- **서비스 LDAP은 read-only**: 그룹 쓰기 시 referral 반환하지만 master(`ldapm` = `75.23.51.49`)는 pod에서 접근 불가
- **search_sc 계정은 master에서도 쓰기 권한 없음** (`insufficientAccessRights`)

### GPFS
- **per-fileset quota 활성화 필수**: `mmchfs pvs --perfileset-quota` (최초 1회)
- **8 MiB 단위 올림**: `capacity_bytes=10000000000` → 실제 quota `9773056K` (≈9.32 GiB)
- **rm-worker SSH 사전조건**:
  - `ssh_host`는 IP로 (호스트명 DNS 실패)
  - `dms-ssh-client` Secret의 `known_hosts`에 IP **평문** (해시화 `-H` 옵션 사용 금지)
  - SSH config에 `User root` 포함
  - ion 노드 root 계정에 rm-worker 공개키 등록
- **`/pvs/dms` 디렉토리 사전 생성 필수**

### SSSD
- ion 노드 OS의 SSSD는 서비스 LDAP을 바라봄 → 로컬 OpenLDAP의 `dms-grp-*` 그룹을 OS가 모름
- `sudo -u {user}`는 그룹 멤버십 없이 실행됨 → access probe는 `stat + getent group` 방식 사용 (코드 변경됨)

### Soft-delete 동작
- **fileset은 Linked 상태 유지**, 디렉토리 filesystem에 그대로 보임
- `chown root:root` + `chmod 000`만 적용 → root 외 접근 불가
- LDAP 그룹은 삭제됨, DB 상태 `Deleted` 마킹
- 수동 완전 삭제 (필요 시): `mmunlinkfileset pvs dms-{dir}` → `mmdelfileset pvs dms-{dir}`
- `management_mode: quota_only` / import된 fileset은 삭제 거부 (외부 fileset 보호)

### Request 라이프사이클
- **terminal states**: Succeeded, Failed, BackendApplyFailed, Rejected, UnknownAfterSideEffect, Conflict, TimedOut, Cancelled, AuthN/Z*Failed
- **`Succeeded`가 Completed와 동일한 의미** (Completed라는 별도 상태 없음)
- **non-terminal request 있으면 같은 resource에 새 request 시 Conflict** → resolve API로 수동 처리 필요
- **Rejected 응답의 `issues[0].status`가 `Succeeded`면**: resource 이미 존재한다는 뜻 (create 대신 PATCH 사용)

### Workflow 변경 필수 절차

이미지/Secret 변경 시:

```bash
# 빌드 + push
ssh rts2411 "cd /mgmt_storage/cocoa.song/dms && sudo docker build --build-arg http_proxy=http://localhost:7227 --build-arg https_proxy=http://localhost:7227 --network=host -f install/docker/Dockerfile.testbed -t localhost:5000/dms:<HASH> ."
ssh rts2411 "sudo docker push localhost:5000/dms:<HASH>"

# rollout
ssh ion2401 "kubectl -n dms set image deployment/dms-api api=rts2411:5000/dms:<HASH>"
ssh ion2401 "kubectl -n dms set image deployment/dms-planner planner=rts2411:5000/dms:<HASH>"
ssh ion2401 "kubectl -n dms set image deployment/dms-rm-worker rm-worker=rts2411:5000/dms:<HASH>"
ssh ion2401 "kubectl -n dms set image daemonset/dms-rm-agent agent=rts2411:5000/dms:<HASH>"
```

## 문서

- `install/install-dms-on-pvs.md` — 13절(Storage Mapping CRUD)까지의 설치 과정
- `install/dms-resource-management-api.md` — 본 검증의 모든 사용법 (GPFS 환경, 로컬 OpenLDAP, Filesystem RM API, Operations 조회, resolve, soft-delete, 차단/해제)

## 검증 안 된 / 다음 단계 후보

| 항목 | 상태 |
|---|---|
| `:assign-quota`, `:import`, `:check`, `:sync` 엔드포인트 | 미검증 |
| `filesystem-expiration-sweep` | 미검증 |
| Kubernetes namespace quota API 전체 | 미검증 |
| Data management API (sync/rm/scan) | 미검증 |
| WEKA backend (CSI 미설치, weka CLI 인증 필요) | 미검증 |
| CephFS create 검증 | 미검증 (storage mapping은 등록됨) |
| `mmdelfileset` 수동 cleanup 절차 실제 실행 | 미검증 |
| 만료(expiry) 동작 검증 | 미검증 |

## 주요 커밋 타임라인

```
b7eb1e6  Fix: FILESYSTEM_DELETE always sets resource status to Deleted
4c97bb9  Fix: deduplicate agent_report_stale, hide when fresh exists, add reported_at
1cd0aec  Docs/Fix: add mmunlinkfileset to soft-delete manual cleanup
bfd1168  Fix: remove mmunlinkfileset from delete (chown+chmod only)
d922058  Feat: GPFS soft-delete (unlink+lock instead of mmdelfileset, action-required)
4b9c1bd  Rename: dms-access-group- → dms-grp-
50a8c20  Fix: unblock falls back to desired_state.mode
6e8d48f  Fix: :initialize routes to FILESYSTEM_BLOCK with block=False
df5a106  Fix: add FILESYSTEM_INITIALIZE to FILESYSTEM_RM_OPERATIONS
e4b03c3  Fix: inject block=true/false into :block/:initialize payloads
7b1685a  Feat: GET /operations/filesystems/{s}/{d} and /{s} list
e4e9ec7  Feat: POST /requests/{id}:resolve API
1ebdece  Opt: batch GPFS command-check, stat+getent access validation
56da159  Fix: 8 MiB boundary in quota readback comparison
3198c2c  Fix: align GPFS block quota to 8 MiB boundary
9f334a0  Config: LDAP GID 9000000-9999999, master URI, policy doc
fac70ac  Fix: inject /usr/lpp/mmfs/bin into PATH for SSH
9c46519  Rename: fileset_root → managed_root in GPFS backend
3ee6531  Docs: add OpenLDAP setup, sync script, RM verification
```
