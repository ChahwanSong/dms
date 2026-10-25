# dms-k8s: k8s RBAC·PV 기반 권한과 PV 스토리지 (2026-10-25 설계)

> dms-k8s 브랜치 전용. main·dms-ssc 는 LDAP 모델 그대로다. 이 문서는 구현의 계약서다 — 구현이 이 문서와 달라지면 코드가
> 진실이고(CLAUDE.md 드리프트 규칙) 이 문서는 「왜 그렇게 설계했나」의 근거로 동결한다.

## 0. 사용자 지시(원문 요약)

- (1) LDAP 을 쓰지 않는다. 권한 = k8s API 로 가져온 맵핑. `rolebindings -A` 의 이름 `<user>-clusterrole-admin|edit`
  (ROLE `ClusterRole/kubeflow-admin|kubeflow-edit`) → 그 사용자는 그 namespace 의 **모든 PVC 에 읽기·쓰기**. 사용자 ID =
  이름 앞부분의 `-` 를 `.` 로(`junwoo-song-clusterrole-admin` → `junwoo.song` = DMS ID). `pvc -A` 로 namespace·PVC·VOLUME·
  SC·용량·접근 모드. 포탈에서 사용자는 **사람이 읽는 이름**(k8s PVC NAME 열 — 사용자는 「PV name」이라 부름)을 보고, 경로는
  **VOLUME 열**(pvc-uuid·k8s-xxxx — 사용자는 「pvc name」이라 부름)로 만든다.
- (2) 모든 잡은 root. 여러 클러스터의 StorageClass·PVC 간 sync·scan·rm. 벤더 경로: IBM GPFS `{managed_root}/{VOLUME}/
  {VOLUME}-data/`, Dell OneFS `{managed_root}/data/csi/{VOLUME}`, 테스트베드 NFS 서버 + csi-driver-nfs. WekaFS·Pure·NetApp 은
  나중 — 확장 가능하게. 비 k8s 스토리지도 등록되고, 운영자는 비 k8s ↔ PV 작업 가능. SC 는 전 클러스터 유일, namespace 는
  클러스터마다 유일 → 표기 「namespace (storageclass)」.
- (3) 포탈: 사용자는 권한 있는 namespace(sc) → PV 이름 → 경로. 운영자는 사용자 지정 / 전체 검색 / raw path. 사용자별 권한
  검색·확인. k8s API 를 부를 DMS 노드(primary·secondary, 상태) 등록, 클러스터별 API 서버 주소(IP·도메인)+토큰 등록, 가져온
  정보·동기화 상태 UI, 호출 주기 조정(기본 5분 전체). 스토리지 종류(PV / 파일시스템 / 둘 다)를 구분하고 등록 정보를 요구.
- 보안을 지나치게 조이지 말고 기능·UI·사용성에 강점. 테스트베드(NFS + csi-driver-nfs)에서 실증, 여러 번 검증·리뷰·개선.

## 1. 용어 (코드는 k8s 용어, 화면은 사용자 용어)

| 코드 식별자 | k8s | 화면 라벨 |
|---|---|---|
| `pvc_name` (`pvc`) | PVC 의 metadata.name (`kubectl get pvc` NAME 열 — models) | **PV 이름** |
| `volume_name` (`volume`) | PV 의 metadata.name (`kubectl get pvc` VOLUME 열 — pvc-5c25…·k8s-44c2…) | **볼륨**(경로용) |
| `storage_class` | StorageClass 이름 | StorageClass |

라벨 상수는 프런트 `features/k8s/k8sTerms.ts` 한 곳. 표기 `nsLabel(ns, sc) = "${ns} (${sc})"`.

## 2. 결정 (D1~D14)

- **D1 신원 모드**: `DMS_IDENTITY_MODE ∈ {ldap, k8s}`(코드 기본 ldap — 공용 코드·기존 테스트 보존, k8s 오버레이가 켠다).
  k8s 모드는 「모든 잡 root」와 「권한 = k8s 맵핑」을 **한 스위치**로 묶는다(root 인데 맵핑 판정이 꺼진 중간 상태 금지).
- **D2 root 강제**: k8s 모드의 모든 단건 제출은 서버가 `payload.run_as_root = True` 를 싣는다. body 가 명시 `false` 면 422
  `run_as_root_required`(화면이 비 root 라 말하는데 root 로 도는 불일치 방지). privilege_policy·stepper 의 「root 근거 재확인」은
  그대로 통과한다(payload 에 근거가 명시로 남는다).
- **D3 세션 조건 유지**: 공유 토큰(모든 에이전트 보유, role admin)으로 낸 제출은 k8s 모드에서도 root 가 안 된다 →
  `privileged_not_authorized`. `resolve_job_identity(root_for_all=True)` 는 특권 목록 대신 **세션 인증만** 본다.
- **D4 맵핑 면제(unrestricted)**: = 기존 `can_run_as_root`(관리자 + 특권 목록/계정 부여 + 세션). 면제자는 raw path·전체 PV 를
  쓴다. 그 밖(일반 사용자, 목록 밖 관리자)은 **PV 주소만** 쓰고 맵핑으로 판정한다. `can_run_as_root` 의 의미는 바꾸지 않는다
  (계정 관리 경계 — routes_accounts). 「admin 역할만으로 root 자격 금지」 원칙이 「admin 역할만으로 맵핑 면제 금지」로 이어진다.
- **D5 맵핑 규칙**(사용자 지시 그대로): RoleBinding 이 `roleRef.kind == ClusterRole`, `roleRef.name ∈ {kubeflow-admin,
  kubeflow-edit}` 이고 이름이 `^(?P<id>.+)-clusterrole-(?P<level>admin|edit)$` 이면 `dms_user = id.replace("-", ".")`, access =
  level. 둘 다 rw(D 사용자: edit/admin 이면 모든 PVC 읽기·쓰기). 그 밖의 바인딩(namespaceAdmin·default-editor·kubeflow-view·
  SA 바인딩·규칙 불일치)은 **인식하지 않은 바인딩**으로 저장·표시만 한다(권한 없음). subjects 는 원문으로 저장해 화면에서
  교차 확인. (namespaceAdmin 소유자 포함·이메일 subject 기반 추출은 사용자 결정 대기 — BACKLOG.)
- **D6 호출 주체**: 지정 노드의 **에이전트**가 별도 스레드에서 외부 API 를 부른다(pull-claim). 서버가 스케줄·페일오버·기록을
  쥐고 에이전트는 「DB 를 모르는 HTTP 클라이언트」로 남는다. 결과 적용(스냅숏 교체)은 **컨트롤러 DB 전용 루프**가 한다(API
  단일 커넥션 보호, 리스 안 즉시 반환 규약).
- **D7 스냅숏 원칙**: 다섯 목록(namespaces·storageclasses·pvs·pvcs·rolebindings)이 **모두 성공한 run 만** 클러스터 단위로
  원자 교체한다. 실패 run 은 이전 스냅숏 유지(부분 실패를 빈 목록으로 접으면 전 사용자 권한이 증발). 동기화가 실패해도
  마지막 성공 스냅숏으로 판정하고(사용자 「보안을 조이지 말라」), 화면에 「n분 전 기준」을 늘 보인다. 한 번도 성공하지
  않은 클러스터의 PV 는 판정 불가 `k8s_mapping_unknown`.
- **D8 PV = 주소 층**: PV 는 「스토리지 + managed_root 기준 상대경로」 위의 주소다. 제출 때 (cluster, namespace, pvc[, 하위경로])
  → (storage_name, target = pv_root/하위경로)로 해석해 **기존** `build_data_payload` 를 그대로 부르고, PV 메타를 payload 에 덧붙인다.
  resource_key·planner·placement·stepper._abs·실행·usage 키는 무변경(물리 경로 — PV 주소와 raw 경로가 같은 곳이면 정확히 충돌).
- **D9 경로 템플릿**: `{managed_root}` 기준 상대 템플릿. 자리표시자 `{volume}`(VOLUME), `{pvc}`, `{namespace}`, `{storageclass}`,
  `{subdir}`(PV `spec.csi.volumeAttributes.subdir`, 대소문자 무시 — csi-driver-nfs 가 치환을 끝낸 실제 하위 디렉터리).
  우선순위: SC 매핑 덮어쓰기 > 스토리지 템플릿 > 벤더 프리셋(backend_type). 프리셋: gpfs `{volume}/{volume}-data`, onefs
  `data/csi/{volume}`, nfs `{subdir}`. 렌더 값에 `/`(subdir 제외)·NUL·`.`·`..` 가 있으면 거부, 결과는 `validate_relative_path`
  통과 필수. 확장: 새 벤더 = 프리셋 한 줄(또는 운영자 템플릿).
- **D10 SC 매핑**: `storage_class → storage_name (+템플릿 덮어쓰기)` 전역 키(사용자 전제 「SC 전 클러스터 유일」). 같은 SC 이름이
  두 클러스터에 보이면 화면 경고. 매핑 대상 스토리지는 kind ∈ {pv, both}.
- **D11 스토리지 종류**: `storages.kind ∈ {filesystem, pv, both}`(NULL = filesystem — 기존 행). 백엔드 라벨에 `onefs`·`nfs` 추가.
  filesystem 은 raw 경로만(면제자), pv 는 PV 주소만(면제자는 raw 도 허용 — 운영 편의), both 는 둘 다.
- **D12 PV 루트 rm 금지**: PV 주소 rm 의 하위 경로가 비면(PV 루트) 422 `pv_root_rm_forbidden`(CSI 볼륨 디렉터리 소멸 방지).
  sync 목적지·소스, scan 은 PV 루트 허용.
- **D13 재확인**: planner 와 컨펌에서 PV 주소를 현재 스냅숏으로 다시 해석해 (storage, root) 가 payload 와 다르면 `pv_moved`
  (PVC 재생성으로 VOLUME 이 바뀐 경우), PVC 가 사라졌으면 `pv_not_found`, 권한이 회수됐으면 `k8s_access_denied`(면제자 제외).
- **D14 sync 허용 쌍**: k8s 모드에서 양쪽이 PV 주소인 sync 는 쌍 게이트 면제(권한 = 맵핑). raw 경로는 면제자 전용이라 쌍과
  무관. 관리자 전용 스토리지(user_enabled=0) 판정은 유지(PV 가 사는 스토리지가 관리자 전용이면 사용자 불가).

## 3. 데이터 모델 (모두 새 테이블 — request_id/job_id/entity_id 열 없음)

```
k8s_sync_settings   id=1 단일 행: primary_node, secondary_node, interval_seconds(기본 300, ≥60), updated_by, updated_at
k8s_clusters        name PK, api_server, tls_server_name NULL, ca_pem NULL, insecure_skip_tls_verify INT 0, token_enc NULL,
                    enabled INT 1, sync_requested_at, last_attempt_at, last_success_at, last_reason, last_error,
                    consecutive_failures INT 0, server_version, created_by/at, updated_by/at
k8s_sync_runs       run_id PK, cluster_name, node_name, role(primary|secondary), trigger(schedule|manual), status(claimed|
                    received|applied|failed|expired), claimed_at, deadline_at, finished_at, applied_at, reason, phase,
                    http_status, error, server_version, duration_ms, counts JSON, changes JSON, payload TEXT(적용 뒤 NULL)
                    인덱스 (cluster_name, claimed_at), (status)
k8s_namespaces      (cluster_name, name) PK, created_at
k8s_storage_classes (cluster_name, name) PK, provisioner, parameters JSON, reclaim_policy, volume_binding_mode
k8s_pvs             (cluster_name, name) PK, driver, volume_handle, volume_attributes JSON, nfs_server, nfs_path,
                    storage_class, claim_namespace, claim_name, capacity, access_modes JSON, phase, reclaim_policy, volume_mode
k8s_pvcs            (cluster_name, namespace, name) PK, uid, storage_class, volume_name, phase, capacity, access_modes JSON, created_at
k8s_role_bindings   (cluster_name, namespace, name) PK, role_kind, role_name, subjects JSON, dms_user NULL, access NULL(admin|edit)
                    인덱스 (dms_user)
k8s_storage_class_maps  storage_class PK, storage_name, path_template NULL, updated_by, updated_at
storages            + kind TEXT(NULL=filesystem), + pv_path_template TEXT NULL   (CREATE + _ensure_columns 양쪽, storages 를
                    test_migrations 의 _V1_COLUMNS 그물에 편입)
```

## 4. 동기화 흐름과 에이전트 계약

1. 보고 본문에 능력 선언 `k8s_sync: {"v": 1}`(새 에이전트만). 보고 응답에 `k8s_sync: {"role": "primary"|"secondary"|null,
   "poll_seconds": 15}`(비밀 없음).
2. role 이 있는 에이전트의 워커 스레드(데몬, 보고 루프와 별개)가 poll_seconds 마다 `POST /api/agent/k8s-sync/claim
   {node_name}` → `{"runs": [{run_id, cluster, api_server, tls_server_name, ca_pem, insecure_skip_tls_verify, token,
   page_limit(500), deadline_seconds(120)}]}`. 토큰은 이때만 secret_box 를 열어 싣는다(능력 선언·지정 노드만).
3. 배정(서버, 순수 함수 `k8s_sync.assign`): 클러스터가 **due** = enabled ∧ 토큰 있음 ∧ 활성 run 없음 ∧ (sync_requested_at 있음 ∨
   last_attempt 없음 ∨ now − last_attempt ≥ interval). primary 는 신선(보고 300초 안)·능력 있으면 due 클러스터를 받는다.
   secondary 는 (primary 미지정 ∨ primary 비신선·무능력 ∨ 그 클러스터의 직전 run 이 primary 의 실패·만료 ∨ due 가 된 지
   90초가 지나도록 아무도 claim 안 함)일 때 받는다. 둘 다 안 되면 표시만(`no_node`) — 다른 노드로 확장하지 않는다(방화벽).
4. 워커는 httpx(trust_env=False, follow_redirects=False, Timeout(connect=5, read=30)) 로 `/version`, `/api/v1/namespaces`,
   `/apis/storage.k8s.io/v1/storageclasses`, `/api/v1/persistentvolumes`, `/api/v1/persistentvolumeclaims`,
   `/apis/rbac.authorization.k8s.io/v1/rolebindings` 를 `?limit=500&continue=` 로 끝까지 받는다(410 이면 그 목록 1회 재시작,
   쪽 수 상한 400). TLS: ca_pem 이 있으면 그 CA, insecure 면 검증 안 함, 둘 다 없으면 시스템 CA; tls_server_name 이 있으면 SNI.
   쪽마다 필요한 필드만 투영한다(§4.1). 오류는 phase·분류(reason)·http_status 로 접고 메시지에 토큰을 싣지 않는다.
5. `POST /api/agent/k8s-sync/runs/{run_id} {node_name, ok, reason?, phase?, http_status?, error?, server_version?,
   duration_ms, data?}` → run 이 그 노드의 claimed·기한 안이면 `received`(payload 저장), 아니면 409.
6. 컨트롤러 `k8s-inventory-apply` 루프(10초, DB 전용): received run 을 오래된 순으로 적용(파싱·D5 도출·diff·클러스터 단위
   교체·run applied·클러스터 last_success/consecutive_failures=0 을 한 트랜잭션), 실패 run 은 클러스터 요약만 갱신, 기한 지난
   claimed run 은 expired. 상태가 바뀔 때만 이벤트(component k8s-sync).

### 4.1 투영 형식(에이전트 → 서버)

```
namespaces:      [{name, created_at}]
storage_classes: [{name, provisioner, parameters:{}, reclaim_policy, volume_binding_mode}]
pvs:             [{name, driver, volume_handle, volume_attributes:{}, nfs_server, nfs_path, storage_class,
                   claim_namespace, claim_name, capacity, access_modes:[], phase, reclaim_policy, volume_mode}]
pvcs:            [{namespace, name, uid, storage_class, volume_name, phase, capacity, access_modes:[], created_at}]
role_bindings:   [{namespace, name, role_kind, role_name, subjects:[{kind, name, namespace}]}]
```

### 4.2 사유 코드(동기화)

k8s_sync_dns_failed · k8s_sync_unreachable · k8s_sync_tls_failed · k8s_sync_unauthorized · k8s_sync_forbidden ·
k8s_sync_timeout · k8s_sync_list_expired · k8s_sync_too_large · k8s_sync_bad_response · k8s_sync_expired · k8s_sync_no_node ·
k8s_sync_token_missing · k8s_sync_token_unreadable · k8s_sync_apply_failed

## 5. 관리자·사용자 API

모두 JSON. 조회 = `require_admin`, 변경 = `require_session_admin`(공유 토큰 규약), 사용자 = `require_user`.

```
GET  /api/admin/k8s/overview      {now, settings:{primary_node, secondary_node, interval_seconds, poll_seconds},
                                   nodes:[{node_name, role:"primary"|"secondary"|null, fresh, capable, reported_at}],
                                   clusters:[ClusterSummary], totals:{clusters, namespaces, pvcs, bindings, users}}
PUT  /api/admin/k8s/settings      {primary_node|null, secondary_node|null, interval_seconds} (노드는 보고된 이름만, 서로 달라야)
GET  /api/admin/k8s/clusters      [ClusterSummary]
POST /api/admin/k8s/clusters      {name, api_server, tls_server_name?, ca_pem?, insecure_skip_tls_verify, enabled,
                                   token_enc (봉인, purpose k8s_cluster_token, subject=name) | token(평문 — 봉인 필수 설정이면 422)}
PUT  /api/admin/k8s/clusters/{n}  같은 필드(토큰 생략 = 유지; api_server 를 바꾸면 토큰 재입력 필수 422 k8s_token_required)
DELETE /api/admin/k8s/clusters/{n}  (스냅숏 행도 삭제)
POST /api/admin/k8s/clusters/{n}:sync   sync_requested_at = now
GET  /api/admin/k8s/runs?cluster=&before=&limit=   [SyncRun] (claimed_at DESC 커서)
GET  /api/admin/k8s/storage-classes  [{cluster, name, provisioner, parameters, reclaim_policy, pvc_count,
                                   storage_name|null, path_template|null, effective_template|null, template_source
                                   ("storageclass"|"storage"|"preset"|null), sample:{namespace, pvc, volume, root|null,
                                   abs_root|null, problem|null}|null, duplicate_in:[cluster…]}]
PUT  /api/admin/k8s/storage-classes/{sc}  {storage_name|null, path_template|null}   (null storage_name = 매핑 해제)
GET  /api/admin/k8s/volumes?cluster=&namespace=&storage_class=&q=&user=&limit=   {synced_at, truncated, volumes:[VolumeRow]}
GET  /api/admin/k8s/access?q=     {users:[{dms_user, account_exists, namespaces:[{cluster, namespace, access, binding}],
                                   volume_count}], unrecognized:[{cluster, namespace, name, role_kind, role_name, subjects}]}
GET  /api/user/k8s/volumes        {synced_at, stale, volumes:[VolumeRow]}   (자기 맵핑 범위만)
```

```
ClusterSummary = {name, api_server, tls_server_name, tls_mode:"ca"|"insecure"|"system", token:"set"|"missing"|"unreadable",
  token_expires_at|null, enabled, status:"ok"|"syncing"|"delayed"|"failing"|"never"|"no_node"|"disabled", last_attempt_at,
  last_success_at, last_reason, last_error, consecutive_failures, server_version, interval_seconds,
  counts:{namespaces, storage_classes, pvs, pvcs, pvcs_bound, bindings, recognized_bindings, users}|null,
  last_run:{run_id, node_name, role, status, duration_ms, reason}|null}
SyncRun = {run_id, cluster_name, node_name, role, trigger, status, claimed_at, finished_at, applied_at, duration_ms,
  reason, phase, http_status, error, server_version, counts, changes:{pvcs:{added,removed}, bindings:{added,removed}}}
VolumeRow = {cluster, namespace, storage_class, pvc, volume|null, phase, capacity, access_modes, created_at,
  storage_name|null, root|null(managed_root 기준), abs_root|null, selectable, reason|null, access:"admin"|"edit"|null,
  users?:[dms_user](관리자 응답만)}
```

`delayed` = 마지막 성공이 2×주기보다 오래됨(GET 시 계산). `/api/auth/me` 에 `identity_mode`, `unrestricted` 추가
(`can_run_as_root` 의미 불변).

## 6. 제출·권한

- body: `pv: {cluster, namespace, pvc}` + `target`(= PV 안 하위 경로, '' 허용 — rm 제외) / sync 는 `source_pv` + `source`,
  `destination_pv` + `destination`(쪽마다 선택 — 면제자는 PV↔raw 혼합). `storage` 와 `pv` 를 같은 쪽에 함께 주면 422
  `pv_and_storage_conflict`.
- 서버 해석: 스냅숏에서 PVC(Bound·volume 있음 — 아니면 422 `pv_not_bound`, 없으면 422 `pv_not_found`) → SC 매핑(없으면
  422 `pv_storageclass_unmapped`) → 템플릿 렌더(실패 422 `pv_path_unresolved`) → target = root[/하위경로] → 기존 검증·
  build_data_payload → `payload.pv|source_pv|destination_pv = {cluster, namespace, pvc, volume, storage_class, root, path}`.
- k8s 모드 게이트: 비면제자는 모든 쪽이 PV 주소여야 함(아니면 403 `raw_path_not_allowed`), 각 쪽 맵핑 권한(없으면 403
  `k8s_access_denied`, 그 클러스터 스냅숏이 없으면 409 `k8s_mapping_unknown`). 권한 기준 ID = owner_username(면제자만 지정
  가능 — 화면 필터 의미) 또는 요청자.
- 제출 게이트 순서·사유 코드는 양방향 등록(reasonCodes.json + api.ts REASON_MESSAGES).

## 7. 포탈

- 사이드바 「스토리지」 그룹에 **k8s 연동**(관리자) — 하위 탭: 개요(호출 노드·주기·클러스터 카드·지금 동기화) / StorageClass
  (매핑·경로 규칙·예시 경로) / PV 탐색 / 사용자 권한 / 동기화 이력. 클러스터 등록·편집 Dialog(토큰 봉인 전송, kubeconfig 의 CA
  붙여넣기, 검증 안 함 토글 + 경고, TLS 서버 이름).
- 스토리지 Dialog: 종류 라디오(파일시스템 / PV / 둘 다), 백엔드 프리셋 템플릿 + 미리보기, 목록에 종류 배지.
- 단일 작업: 측면마다 LocationField — 사용자: namespace(sc) select → PV 이름 select(Bound 아님은 비활성) → PV 안 경로 + 「실제 경로」
  ·「n분 전 기준」. 면제 관리자: 측면마다 「PV | 직접 경로」 + 1구획 「권한 기준 사용자」(비우면 전체 PV 검색). k8s 모드에서는
  root 체크박스·LDAP 그룹 안내를 걷고 「모든 작업은 root 로 실행」을 표시.
- 표기: payload 에 pv 블록이 있으면 `PV 이름 — namespace (sc) / 하위경로` (pathParts 한 곳).
- 디자인 규약: 이모지·왼쪽 선 강조·점+텍스트 배지 금지, 색 토큰만, StatusPill variant 명시, td flex 금지, 긴 토큰 overflow-wrap.

## 8. 테스트베드 (dmsk8s 전용 — ssc 무간섭)

- 호스트 export `/data/k8s_pv` → 10.10.10.20-22(rw,no_root_squash) 새 파일 `/etc/exports.d/dms_k8s_pv.exports`. 워커 마운트
  `/k8s_pv/{nfs,gpfs,onefs}`. csi-driver-nfs v4.13.5(install-once, 태그 고정 URL). SC: tb-nfs(subDir `${pvc.metadata.namespace}/
  ${pvc.metadata.name}`), tb-gpfs(subDir `${pv.metadata.name}/${pv.metadata.name}-data`), tb-onefs(share …/onefs/data/csi, 정적 PV
  `k8s-<10hex>`), 모두 onDelete retain·Retain.
- 픽스처: ClusterRole kubeflow-admin·edit·view(rules: []), ns team-a·team-b·team-c, 사이트 모양 바인딩(junwoo-song-clusterrole-admin,
  alice-clusterrole-edit …) + 대조군(namespaceAdmin, default-editor, *-clusterrole-view, KFAM 꼴 user-…), PVC(같은 이름 다른 ns 포함,
  Pending 1), 마커 파일, 리더 SA(dms-reader/dms-inventory, 읽기 전용 ClusterRole, 장기 토큰 Secret).
- DMS 등록: 스토리지 k8s-pv-nfs·k8s-pv-gpfs(both)·k8s-pv-onefs, 클러스터 dmsk8s(https://10.10.10.20:6443 + CA + 토큰), 호출 노드
  primary k8s-w1 · secondary k8s-w2, 주기 300초. 비 k8s ↔ PV 실증은 cephfs-dms.

## 9. 구현 슬라이스

1. **인벤토리 백엔드**: 마이그레이션(§3 전부), k8s_rbac(D5)·k8s_sync(배정·상태)·pv_paths(D9) 순수 모듈, 저장소, 에이전트 워커
   (httpx), 에이전트·관리자 라우트(§5 의 k8s 부분), 컨트롤러 적용 루프, 설정 키, 봉인 용도 `k8s_cluster_token`, 사유 코드.
2. **제출·권한**: 신원 모드(D1~D4), 제출 해석(§6), planner·컨펌 재확인(D13), 스토리지 kind·템플릿·백엔드 라벨(D11),
   me 필드, 사용자 PV 목록 API, 표기용 payload.
3. **포탈**: k8s 연동 화면, 스토리지 Dialog, 단일 작업 LocationField, 표기.
4. **테스트베드**: §8.
5. 실증·적대적 리뷰·개선 반복, 문서(CLAUDE.md·ARCHITECTURE 불변식 — 「서버 생략 기본값을 root 로 바꾸지 마라」의 k8s 모드 예외).

## 10. 사용자 결정 대기(BACKLOG)

namespaceAdmin(소유자)·이메일 subject 기반 ID · kubeflow-view 를 읽기 전용으로 볼지 · 동기화가 오래 실패할 때 차단 시점 ·
LDAP 코드 삭제 여부 · 배치(BatchCreate) PV 주소 · 심링크 봉쇄를 sync·scan 으로 확장할지 · 멀티 클러스터 라이브 실증용 두 번째 VM.
