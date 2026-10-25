# dms-k8s: k8s RBAC·PV 기반 권한과 PV 스토리지 (2026-10-25 설계, v2 — 적대적 검토 3관점 반영)

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
| `pvc` / `pvc_name` | PVC 의 metadata.name (`kubectl get pvc` NAME 열 — models) | **PV 이름** |
| `volume` / `volume_name` | PV 의 metadata.name (`kubectl get pvc` VOLUME 열 — pvc-5c25…·k8s-44c2…) | **볼륨**(경로용) |
| `storage_class` | StorageClass 이름 | StorageClass |

라벨 상수는 프런트 `features/k8s/k8sTerms.ts` 한 곳. 표기 `nsLabel(ns, sc) = "${ns} (${sc})"`.

## 2. 결정

### 신원·권한

- **D1 신원 모드**: `DMS_IDENTITY_MODE ∈ {ldap, k8s}`(코드 기본 ldap — 공용 코드·기존 테스트 보존). testbed-k8s 오버레이와
  dms-k8s 의 prod 오버레이가 `k8s` 를 켜고 렌더 계약 테스트로 고정한다. k8s 모드는 「모든 잡 root」와 「권한 = k8s 맵핑」을
  **한 스위치**로 묶는다. k8s 모드인데 `DMS_LDAP_URI` 가 있으면 기동 거부(SettingsError).
- **D2 root 강제**: k8s 모드의 모든 단건 제출은 서버가 `payload.run_as_root = True` 를 싣는다. body 가 명시 `false` 면 422
  `run_as_root_required`. privilege_policy(REQUESTED)·stepper 근거 재확인은 그대로 통과한다.
- **D3 root 허용 술어 하나**: `identity.root_allowed(...)` — k8s 모드 = **세션 인증 요청인가**(auth_method == session),
  ldap 모드 = 기존 privilege_eligible. **세 지점이 같은 술어**: 제출(routes_requests — 토큰 제출은 `privileged_not_authorized`),
  계획(resolve_job_identity 의 REQUESTED 경로), 컨펌(routes_jobs.confirm_job 의 REQUESTED 재검사). 배치 자식(IF_ELIGIBLE)은
  지금처럼 특권 목록 판정. k8s 모드는 **그룹 denylist 를 평가하지 않는다**(groups=[] — LDAP 없음; 이름 규칙만). 그룹 규칙
  추가는 k8s 모드에서 422 `denylist_group_unsupported`.
- **D4 맵핑 면제(unrestricted)**: = 기존 `can_run_as_root`(관리자 + 특권 목록/계정 부여 + 세션). 요청 행 기반 동등 술어
  `k8s_access.unrestricted_request(req)` = 요청자 계정 role admin ∧ 특권 목록/부여 ∧ auth_method session(배치 자식은 면제) —
  planner·컨펌이 쓴다. 면제자는 raw path·전체 PV. 그 밖(일반 사용자, 목록 밖 관리자)은 **PV 주소만** + 맵핑 판정.
  `can_run_as_root` 의미 불변(계정 관리 경계). k8s 모드 계정 화면의 root 자격 라벨은 「전체 PV·직접 경로 권한」으로 바꾼다.
  `/api/auth/me` 에 `identity_mode`, `unrestricted`, `user_operations` 를 더한다.
- **D5 맵핑 규칙**(사용자 지시 그대로): RoleBinding 이 `roleRef.kind == ClusterRole`, `roleRef.name ∈ {kubeflow-admin,
  kubeflow-edit}`, 이름이 `^(?P<id>.+)-clusterrole-(?P<level>admin|edit)$` 이면 `dms_user = id.replace("-", ".")`, access = level
  (둘 다 rw). 이 규칙 밖(namespaceAdmin·default-editor·kubeflow-view·SA 바인딩·roleRef 불일치)은 **인식하지 않은 바인딩**으로
  저장·표시만(권한 없음). 인식된 바인딩마다 `subject_mismatch`(User subject 의 이름·이메일 로컬파트가 파생 ID 와 다름)와
  `account_exists`(같은 이름의 DMS 계정)를 계산해 권한 화면에 경고로 보인다. 맵핑 ID 는 **DMS 계정 username 과 정확히 일치**
  해야 한다(계정은 가입 또는 관리자 생성 — 권한 화면에 「계정 만들기」 바로가기).
- **D15 사용자 연산**: k8s 오버레이가 `DMS_USER_ALLOWED_OPERATIONS="sync,scan,rm"` 을 싣는다(코드 기본 {sync} 불변).
  포탈의 연산 옵션은 `me.user_operations` 로 그린다. rm 안전 = D12 + 본인 컨펌.

### 동기화

- **D6 호출 주체**: 지정 노드(primary·secondary)의 **에이전트**가 별도 데몬 스레드에서 외부 API 를 부른다(pull-claim). 서버가
  스케줄·대체·기록을 쥐고, 결과 적용은 **컨트롤러 DB 전용 루프**가 한다.
- **D7 스냅숏 원칙**: 필수 목록 = **rolebindings + pvcs**(권한·PV 이름·VOLUME 의 원천). pvs·storageclasses 는 선택 — 403 이면
  그 목록은 이전 값 유지 + run 경고(`partial`), 영향은 `{subdir}`·`{attr:}`·`{handle:}` 해석과 provisioner 표시뿐.
  필수 목록이 실패한 run 은 스냅숏 무변경. 동기화가 실패해도 마지막 성공 스냅숏으로 판정(「n분 전 기준」 표시). 한 번도 성공
  하지 않은 클러스터는 `k8s_mapping_unknown`(모름 ≠ 없음).
- **D16 배정 규칙(결정적 두 개)**: (1) 정규 run 은 **primary 전용**. `due_at`(= last_attempt_at + interval; sync_requested_at 이
  있으면 그 값; 시도 이력이 없으면 클러스터 생성·활성 시각)부터 **90초**가 지나도록 primary 가 claim 하지 않으면 secondary 도
  받는다. (2) primary run 이 **노드성 사유**(unreachable·dns_failed·timeout·expired)로 끝나면 30초 뒤 secondary 전용으로 1회
  대체 재시도(trigger=failover). tls·401·403·too_large·bad_response 는 대체하지 않는다. 다음 정규 주기는 다시 primary. 토큰이
  없거나 열 수 없는 클러스터는 due 에서 빼고 run 을 만들지 않는다(상태 token_missing/token_unreadable). 노드 둘 다 없으면
  표시만(`no_node`) — 다른 노드로 확장하지 않는다(방화벽).

### 스토리지·경로

- **D8 PV = 주소 층**: 제출 때 (cluster, namespace, pvc[, 하위경로]) → (storage_name, target = root[/하위경로])로 해석해 **기존**
  `build_data_payload` 를 그대로 부르고, PV 메타를 payload 에 덧붙인다. resource_key·planner storage admission·placement·
  stepper._abs·실행·usage 키 무변경. 사람이 읽는 표기는 `requests.target_label`(검색 대상에도 포함 — 아래 D17).
- **D9 경로 템플릿**: managed_root 기준 상대 템플릿. 자리표시자 `{volume}`, `{pvc}`, `{namespace}`, `{storageclass}`,
  `{subdir}`(PV volumeAttributes 의 subdir, 대소문자 무시), `{attr:<키>}`(volumeAttributes, 대소문자 무시), `{handle:<구분자>:<i>}`
  (volume_handle 분할 i번째). 우선순위 2단: **SC 매핑 덮어쓰기 > backend_type 프리셋**. 프리셋 레지스트리(`pv_paths.PRESETS`):
  gpfs `{volume}/{volume}-data`, onefs `data/csi/{volume}`, nfs `{subdir}` — 새 벤더 = 프리셋 한 줄 또는 SC 덮어쓰기(WekaFS 는
  `{handle:/:2…}` 류, NetApp 은 `{attr:internalName}` 류로 표현 가능; Pure FlashBlade(PV 마다 별도 export)는 이 모델 밖 — PV 별
  스토리지 등록이 대안, BACKLOG). 렌더 값 검증: `{subdir}`·`{attr:}`·`{handle:}` 은 `/` 허용(앞뒤 `/` 제거), 그 밖은 `/` 금지,
  공통으로 NUL·`.`·`..` 성분 금지, 결과는 `validate_relative_path` 통과 필수. 값이 없으면 해석 불가(`pv_path_unresolved` — null ≠ 실패).
- **D10 SC 매핑**: `storage_class → storage_name (+템플릿 덮어쓰기)` 전역 키(사용자 전제). 같은 SC 이름이 두 클러스터에 보이면
  화면 경고. 매핑 대상 스토리지는 kind ∈ {pv, both}. 스토리지 삭제는 같은 트랜잭션에서 매핑을 지우고 감사(sync_pairs 선례).
  매핑된 스토리지의 kind 를 filesystem 으로 바꾸면 422 `storage_has_sc_mappings`.
- **D11 스토리지 종류**: `storages.kind ∈ {filesystem, pv, both}`(NULL = filesystem). 백엔드 라벨에 `onefs`·`nfs` 추가
  (test_storage_backends 갱신, 「backend_type 은 라벨일 뿐」 불변식은 「PV 경로 프리셋 키이기도 하다」로 갱신). 종류별 요구:
  filesystem = 현행. pv·both = backend 프리셋이 있거나 SC 덮어쓰기로 쓸 것(등록 화면이 프리셋 유무를 보이고, 프리셋이 없는
  백엔드는 「StorageClass 화면에서 템플릿 지정 필요」 안내). 직접 경로 피커는 filesystem·both 만(pv 전용은 면제자에게도 숨김 —
  운영자는 PV 주소로).
- **D12 PV 루트 rm 금지**: **정규화한 최종 target == pv.root** 면 422 `pv_root_rm_forbidden`('.'·'./' 우회 차단). planner 재확인도 같다.
- **D13 재확인(planner·컨펌, 같은 함수)**: payload 에 pv 블록이 있으면 현재 스냅숏으로 다시 해석해 (storage, root) 가 다르면
  `pv_moved`, PVC·클러스터가 사라졌으면 `pv_not_found`, 각 쪽 target 이 root 와 같거나 root+'/' 아래가 아니면 `pv_target_outside`.
  비면제 요청은 모든 쪽에 pv 블록이 있어야 하고(없으면 `raw_path_not_allowed` — DB 신뢰 경계), 맵핑 권한을 다시 판정
  (`k8s_access_denied`).
- **D14 sync 허용 쌍**: 술어 하나 `k8s_access.pair_gate_exempt(payload)` = k8s 모드 ∧ 모든 쪽이 pv 블록. **제출·계획 3b·컨펌 세
  지점**이 같은 술어로 면제. 관리자 전용 스토리지 판정(user_enabled=0)은 **해석된 storage_name** 에 적용(유지). 포탈은 k8s 모드에서
  sync 쌍 필터(needPairs)를 끄고 스토리지 화면의 쌍 패널에 「k8s 모드에서는 PV 권한으로 판정」을 표기.
- **D17 대상 라벨**: `requests.target_label TEXT`(CREATE + _ensure_columns). PV 주소 제출이면 「PV 이름 — ns (sc) / 하위경로」
  (sync 는 「 → 」로 두 쪽), raw 면 NULL. 작업 목록 path 검색은 `lower(_TARGET_TEXT_SQL) OR lower(target_label)` 로 건다.

## 3. 데이터 모델 (TEXT 는 JSON 직렬화 — dump_json/load_json, 시각은 ISO TEXT; 새 테이블에 request_id/job_id/entity_id 열 없음)

```
k8s_sync_settings   id=1 단일 행: primary_node TEXT, secondary_node TEXT, interval_seconds INTEGER(기본 300, ≥60), updated_by, updated_at
k8s_clusters        name PK, api_server, tls_server_name, ca_pem, insecure_skip_tls_verify INTEGER 0, token_enc, enabled INTEGER 1,
                    fingerprint(kube-system ns uid — 같은 클러스터 중복 등록 경고; 선택 목록 실패면 NULL), sync_requested_at,
                    last_attempt_at, last_success_at, last_reason, last_error, consecutive_failures INTEGER 0, server_version,
                    failover_after(대체 재시도 예정 시각 NULL), config_rev INTEGER 0(주소·토큰·TLS 바뀔 때 +1),
                    applied_run_seq INTEGER(마지막 적용 run 의 seq), created_by/at, updated_by/at
k8s_sync_runs       seq auto PK, run_id TEXT UNIQUE, cluster_name, node_name, role(primary|secondary), trigger(schedule|manual|failover),
                    status(claimed|received|applied|failed|expired|superseded|discarded), claimed_at, deadline_at, finished_at,
                    applied_at, config_rev, reason, phase, http_status, error, server_version, duration_ms,
                    counts TEXT, warnings TEXT, changes TEXT(개수 + 이름 상위 50), payload TEXT(종단 시 NULL)
                    인덱스 (cluster_name, seq), (status), (node_name, seq)
k8s_storage_classes (cluster_name, name) PK, provisioner, parameters TEXT, reclaim_policy, volume_binding_mode
k8s_pvs             (cluster_name, name) PK, driver, volume_handle, volume_attributes TEXT, nfs_server, nfs_path, storage_class,
                    claim_namespace, claim_name, capacity, access_modes TEXT, phase, reclaim_policy, volume_mode
k8s_pvcs            (cluster_name, namespace, name) PK, uid, storage_class, volume_name, phase, capacity, access_modes TEXT, created_at
                    인덱스 (cluster_name, volume_name)
k8s_role_bindings   (cluster_name, namespace, name) PK, role_kind, role_name, subjects TEXT, dms_user, access(admin|edit|NULL),
                    subject_mismatch INTEGER(NULL=해당 없음)  인덱스 (dms_user)
k8s_storage_class_maps  storage_class PK, storage_name, path_template, updated_by, updated_at
storages            + kind TEXT(NULL=filesystem)   — CREATE + _ensure_columns, storages 를 test_migrations 의 _V1_COLUMNS 그물에 편입
requests            + target_label TEXT            — CREATE + _ensure_columns(_V1 그물 안 — ensure 개수 갱신)
```

## 4. 동기화 흐름과 에이전트 계약

1. 보고 본문에 능력 선언 `k8s_sync: {"v": 1}`(agent_nodes.report 에 그대로 저장된다). 보고 응답에 `k8s_sync: {"role":
   "primary"|"secondary"|null, "poll_seconds": 15}`(비밀 없음).
2. role 이 있는 에이전트의 워커 스레드(데몬)가 poll_seconds 마다 `POST /api/agent/k8s-sync/claim {node_name}` → **최대 1건**
   `{"run": {run_id, cluster, api_server, tls_server_name, ca_pem, insecure_skip_tls_verify, token, page_limit:500,
   deadline_seconds:120, max_bytes:33554432} | null}`. 작업을 받았으면 결과를 올린 뒤 바로 다시 claim(여러 클러스터는 직렬).
   claim 은 last_attempt_at CAS(`UPDATE k8s_clusters SET last_attempt_at=:now, sync_requested_at=NULL, failover_after=NULL
   WHERE name=:c AND last_attempt_at IS :seen`)로 경합을 정리하고 run 행(claimed, deadline_at = now + deadline + 30초 유예,
   config_rev)을 만든다. 토큰은 이때만 secret_box 를 열어 싣는다(능력 선언·지정 노드만).
3. 워커: httpx(`trust_env=False`, `follow_redirects=False`, `Timeout(30.0, connect=5.0)`), TLS = ca_pem 이 있으면
   `ssl.create_default_context(cadata=…)`, insecure 면 검증 안 함, 둘 다 없으면 `ssl.create_default_context()`(이미지 기본 신뢰
   저장소); tls_server_name 이 있으면 요청 extensions `sni_hostname`. 순서: `/version`(실패해도 run 실패 아님) → storageclasses →
   pvcs → pvs → rolebindings, `?limit=500&continue=`(410 이면 그 목록 1회 재시작, 쪽 수 상한 400). 쪽마다 투영(§4.1)해 바로
   직렬화 조각으로 쌓고 max_bytes 를 넘으면 `k8s_sync_too_large` 실패. deadline_seconds 가 되면 스스로 중단하고 `k8s_sync_timeout`
   + phase 로 실패 보고. 오류 분류: dns_failed·unreachable·tls_failed·unauthorized(401)·forbidden(403, phase)·timeout·
   list_expired·too_large·bad_response. 메시지에 토큰·헤더 금지. pvs·storageclasses 의 403 은 그 목록만 null + warnings.
   kube-system namespace uid 는 `GET /api/v1/namespaces/kube-system` 로 fingerprint(실패 무시).
4. `POST /api/agent/k8s-sync/runs/{run_id} {node_name, ok, reason?, phase?, http_status?, error?, server_version?, fingerprint?,
   duration_ms, warnings?, data?:{storage_classes|null, pvcs, pvs|null, role_bindings}}` → run 이 그 노드의 claimed·기한(유예 포함)
   안이면 `received`(payload 저장, 본문 상한 max_bytes), 아니면 409. 실패 보고는 즉시 클러스터 요약(last_reason·
   consecutive_failures·D16 대체 예정)까지 갱신.
5. 컨트롤러 `k8s-inventory` 루프(10초, DB 전용, 틱 예산 ~10초): (a) 클러스터마다 **seq 가 가장 큰 received run 하나만** 적용하고
   더 오래된 received 는 superseded(payload NULL). (b) 적용 = 현재 스냅숏 키를 읽어 파이썬에서 diff → 바뀐 행만 INSERT·UPDATE·
   DELETE(첫 적용은 다행 INSERT 묶음), run applied·클러스터 last_success_at·consecutive_failures=0·applied_run_seq·fingerprint 를
   **한 트랜잭션**; 시작은 `UPDATE … SET status='applied' WHERE run_id=:r AND status='received'` 영향 1 확인, 클러스터가 있고
   config_rev 가 run 의 값과 같을 때만(아니면 discarded). 예외면 롤백 후 별도 트랜잭션으로 failed(`k8s_sync_apply_failed`).
   (c) 기한 지난 claimed → expired(+ D16 대체 예정). (d) 지운 클러스터의 스냅숏 행을 묶음 단위로 정리. 이벤트는 상태가 바뀔 때만,
   커밋 뒤에. 컨트롤러 리스 holder 에 HOSTNAME 을 넣는다(cli.py — 컨테이너 PID 가 늘 1 이라 롤아웃 겹침에 리스가 무력했다).
6. 보존: `_retention_step` 에 k8s_sync_runs 정리(30일, 클러스터별 최근 50건과 마지막 성공 run 은 보존).

### 4.1 투영 형식(에이전트 → 서버)

```
storage_classes: [{name, provisioner, parameters:{}, reclaim_policy, volume_binding_mode}]
pvcs:            [{namespace, name, uid, storage_class, volume_name, phase, capacity, access_modes:[], created_at}]
pvs:             [{name, driver, volume_handle, volume_attributes:{}, nfs_server, nfs_path, storage_class,
                   claim_namespace, claim_name, capacity, access_modes:[], phase, reclaim_policy, volume_mode}]
role_bindings:   [{namespace, name, role_kind, role_name, subjects:[{kind, name, namespace}]}]
```

### 4.2 사유 코드(동기화·관리)

k8s_sync_dns_failed · k8s_sync_unreachable · k8s_sync_tls_failed · k8s_sync_unauthorized · k8s_sync_forbidden ·
k8s_sync_timeout · k8s_sync_list_expired · k8s_sync_too_large · k8s_sync_bad_response · k8s_sync_expired · k8s_sync_no_node ·
k8s_sync_token_missing · k8s_sync_token_unreadable · k8s_sync_apply_failed · k8s_cluster_exists · k8s_cluster_not_found ·
invalid_k8s_cluster_name · invalid_k8s_api_server(https://host[:port] 만 — 경로·쿼리 금지) · k8s_ca_invalid · k8s_token_required ·
invalid_k8s_sync_settings(주기 <60·같은 노드 둘) · k8s_node_unknown(보고된 적 없는 노드) · storage_class_not_found ·
storage_not_pv_capable · invalid_pv_path_template · storage_has_sc_mappings · denylist_group_unsupported
(reasonCodes.json 과 api.ts REASON_MESSAGES 양쪽 — 서버는 `reason_code=`/`detail=` 키워드 리터럴로)

## 5. 관리자·사용자 API

조회 = `require_admin`, 등록·토큰·주소·호출 노드·주기 변경 = `require_session_admin`, `:sync` = `require_admin`(스크립트 가능),
사용자 = `require_user`.

```
GET  /api/admin/k8s/overview      {now, settings:{primary_node, secondary_node, interval_seconds, poll_seconds},
                                   nodes:[{node_name, role, fresh, capable, reported_at, internal_ip, last_claim_at, last_ok_at,
                                   last_fail_at, last_reason, serving:[cluster…]}],
                                   clusters:[ClusterSummary], totals:{clusters, namespaces, pvcs, bindings, users}}
PUT  /api/admin/k8s/settings      {primary_node|null, secondary_node|null, interval_seconds}
GET  /api/admin/k8s/clusters      [ClusterSummary]
POST /api/admin/k8s/clusters      {name, api_server, tls_server_name?, ca_pem?(PEM 또는 base64 PEM), insecure_skip_tls_verify, enabled,
                                   token_enc(봉인, purpose k8s_cluster_token, subject=name) | token(평문 — 봉인 필수 설정이면 422;
                                   base64 로 감싼 JWT 면 풀어서 저장)}
PUT  /api/admin/k8s/clusters/{n}  같은 필드(토큰 생략 = 유지; api_server 를 바꾸면 토큰 재입력 필수 422 k8s_token_required)
DELETE /api/admin/k8s/clusters/{n}  (클러스터 행만 — 스냅숏은 컨트롤러가 정리)
POST /api/admin/k8s/clusters/{n}:sync   sync_requested_at = now
GET  /api/admin/k8s/runs?cluster=&node=&before=<seq>&limit=   [SyncRun] (seq DESC)
GET  /api/admin/k8s/storage-classes  [{cluster, name, provisioner, parameters, reclaim_policy, pvc_count, storage_name|null,
                                   storage_kind|null, path_template|null, effective_template|null, template_source("storageclass"|
                                   "preset"|null), sample:{namespace, pvc, volume, root|null, abs_root|null, problem|null}|null,
                                   duplicate_in:[cluster…]}]
PUT  /api/admin/k8s/storage-classes/{sc}  {storage_name|null, path_template|null}   (null storage_name = 매핑 해제)
GET  /api/admin/k8s/volumes?cluster=&namespace=&storage_class=&q=&user=&limit=   {clusters:[ClusterFreshness], truncated, volumes:[VolumeRow]}
GET  /api/admin/k8s/access?q=     {clusters:[ClusterFreshness], users:[{dms_user, account_exists, namespaces:[{cluster, namespace,
                                   access, binding, subject_mismatch}], volume_count}], unrecognized:[{cluster, namespace, name,
                                   role_kind, role_name, subjects}]}
GET  /api/user/k8s/volumes        {clusters:[ClusterFreshness], volumes:[VolumeRow]}   (자기 맵핑 범위만; 면제자는 전체)
```

```
ClusterFreshness = {name, synced_at|null, stale, status}
ClusterSummary = {name, api_server, tls_server_name, tls_mode:"ca"|"insecure"|"default", token:"set"|"missing"|"unreadable",
  token_expires_at|null, enabled, status:"ok"|"syncing"|"delayed"|"failing"|"never"|"no_node"|"disabled"|"token_missing"|
  "token_unreadable", last_attempt_at, last_success_at, last_reason, last_error, consecutive_failures, server_version,
  fingerprint, duplicate_of|null, interval_seconds,
  counts:{storage_classes, pvs, pvcs, pvcs_bound, namespaces, bindings, recognized_bindings, users}|null,
  last_run:{run_id, node_name, role, trigger, status, duration_ms, reason, warnings}|null}
SyncRun = {seq, run_id, cluster_name, node_name, role, trigger, status, claimed_at, finished_at, applied_at, duration_ms,
  reason, phase, http_status, error, server_version, counts, warnings, changes:{pvcs:{added,removed,names}, bindings:{…}}}
VolumeRow = {cluster, namespace, storage_class, pvc, volume|null, phase, capacity, access_modes, created_at,
  storage_name|null, root|null(managed_root 기준), abs_root|null, selectable, reason|null, access:"admin"|"edit"|null,
  users?:[dms_user](관리자 응답만)}
```

`delayed` = 마지막 성공이 2×주기보다 오래됨(GET 시 계산). namespace 개수는 PVC·바인딩의 distinct.

## 6. 제출·권한

- body: `pv: {cluster, namespace, pvc}` + `target`(= PV 안 하위 경로, '' 허용 — rm 은 D12) / sync 는 `source_pv` + `source`,
  `destination_pv` + `destination`(쪽마다 선택 — 면제자는 PV↔raw 혼합). 같은 쪽에 storage 와 pv 를 함께 주면 422
  `pv_and_storage_conflict`.
- **게이트 순서**: 유지보수 → 연산 allowlist → 클러스터 존재·한 번 이상 동기화(409 `k8s_mapping_unknown`) → PV 해석(422
  `pv_not_found`·`pv_not_bound`·`pv_storageclass_unmapped`·`pv_path_unresolved`·`pv_root_rm_forbidden`) → **해석된 storage** 로
  storage_admin_only → 맵핑·raw 게이트(403 `raw_path_not_allowed`·`k8s_access_denied`) → sync 쌍(D14 면제) → owner·root 게이트(D2·D3)
  → build_data_payload → `payload.pv|source_pv|destination_pv = {cluster, namespace, pvc, volume, storage_class, root, path}`,
  `target_label`.
- 권한 기준 ID = 요청자(비면제자). 면제 관리자의 「사용자 지정」 = 화면 필터 + owner_username 기록(요청 상세 「대상 사용자」).
  k8s 모드에서 owner_username 검증은 계정 아이디 규칙(`[a-z0-9][a-z0-9._-]{0,63}`)과 맞춘다.
- 사용자 스캔 경로(routes_scan_paths)는 k8s 모드에서 **면제자 전용**(비면제자 422 — 후속: PV 주소 기반, BACKLOG).
- 새 사유 코드(제출): run_as_root_required · pv_and_storage_conflict · pv_not_found · pv_not_bound · pv_storageclass_unmapped ·
  pv_path_unresolved · pv_root_rm_forbidden · raw_path_not_allowed · k8s_access_denied · k8s_mapping_unknown · pv_moved ·
  pv_target_outside.

## 7. 포탈

- 사이드바 「스토리지」 그룹에 **k8s 연동**(관리자) — 하위 탭: **개요**(호출 노드 카드: 역할·생존·IP「대상 API 서버 방화벽에
  허용」·마지막 성공/실패·맡은 클러스터 / 주기 / 클러스터 카드: 상태·n분 전·개수·마지막 run·지금 동기화·편집) / **StorageClass**
  (매핑·경로 템플릿·예시 경로·중복 경고) / **PV 탐색**(검색·필터·상세: 실제 경로·권한 사용자·「이 PV로 단일 작업」) / **사용자
  권한**(사용자별·namespace별, 근거 바인딩 원문, subject 불일치·계정 없음 경고, 계정 만들기, 인식하지 않은 바인딩 접힘) /
  **동기화 이력**(seq 커서, 상세 Dialog: 추가·삭제 개수와 이름). 클러스터 등록·편집 Dialog: 이름, API 서버(IP·도메인), TLS(CA
  붙여넣기 PEM/base64 · 검증 안 함 + 경고 · TLS 서버 이름), 토큰(쓰기 전용, 봉인 전송), 그리고 대상 클러스터에 만들 **읽기 전용
  ClusterRole·SA·장기 토큰 Secret YAML**(복사 버튼).
- 스토리지 Dialog: 종류 라디오(파일시스템 / PV / 둘 다) + 종류별 안내, 백엔드 프리셋 템플릿 읽기 전용 표시, 연결된 StorageClass 목록.
  목록에 종류 배지. 백엔드 select 에 onefs·nfs.
- 단일 작업(SubmitJob): 분기 키는 `me.unrestricted`(+ `me.identity_mode`). 측면마다 LocationField —
  비면제자: namespace(sc) select → PV 이름 select(Bound 아님 비활성, 「용량 · 상태」) → PV 안 경로 + 「실제 경로」·「n분 전 기준」.
  권한 namespace 0개: "rolebinding <ID>-clusterrole-edit|admin 이 있는 namespace 가 없습니다 — 관리자에게 요청하세요".
  아직 동기화 안 됨 / 조회 실패를 0개와 다른 문구로. 면제자: 측면마다 「PV | 직접 경로」 Segmented, 1구획 「권한 기준 사용자」
  (datalist — 비우면 전체 PV 검색 입력 + 결과 목록(상한 50, truncated 안내)). 목록 밖 관리자: 사용자 흐름 + "직접 경로·사용자
  지정은 계정 화면의 「전체 PV·직접 경로 권한」이 필요" 한 줄. k8s 모드: rootEffective = true(바디 run_as_root:true), root 체크박스·
  LDAP 그룹 안내·sync 쌍 필터 제거, 「모든 작업은 root 로 실행」 표시, 연산 옵션은 me.user_operations.
- 표기: pathParts — payload 에 pv 블록이 있으면 `PV 이름 — namespace (sc) / 하위경로`(빈 하위경로 = 「(PV 전체)」).
  요청 상세에 PV 행(클러스터 · 볼륨 · StorageClass).
- 계정 화면: k8s 모드에서 root 자격 라벨 「전체 PV·직접 경로 권한」.
- 디자인 규약: 이모지·왼쪽 선 강조·점+텍스트 배지 금지, 색 토큰만, StatusPill variant 명시, td flex 금지, 표 안 버튼 nowrap,
  긴 토큰 overflow-wrap:anywhere + minmax(0,1fr), FormSection·nav 에 aria-label 금지, 라벨은 서로 부분 문자열이 되지 않게.

## 8. 테스트베드 (dmsk8s 전용 — ssc 무간섭; testbed 저장소 + dmsk8s 클러스터)

- 호스트 export `/data/k8s_pv` → 10.10.10.20-22 (rw,sync,no_subtree_check,no_root_squash), **새 파일**
  `/etc/exports.d/dms_k8s_pv.exports`(nfs-pv-export.sh 소유 — mgmt·dms_src exports 무변경). 워커(k8s-w1·w2) 마운트
  `/k8s_pv/{nfs,gpfs,onefs}`(storage.yml 새 play, hosts dmsk8s_workers). tbinfo `pv-exports`·`pv-mounts`, validate.sh·
  post-boot-check.sh 허용 집합 갱신 + 음성 확인(dms-w* 에 /k8s_pv 없음). Makefile storage 에 nfs-pv-export.sh,
  csi-nfs-dmsk8s·pv-fixtures-dmsk8s 타깃. 실증 데이터는 작게(총 1GiB 이하).
- csi-driver-nfs v4.13.5(install-once, 태그 고정 URL 4개). SC(모두 onDelete retain·reclaimPolicy Retain·volumeBindingMode
  Immediate·mountOptions nfsvers=4.2, server 10.10.10.1): tb-nfs(share /data/k8s_pv/nfs, subDir
  `${pvc.metadata.namespace}/${pvc.metadata.name}`), tb-gpfs(share /data/k8s_pv/gpfs, subDir
  `${pv.metadata.name}/${pv.metadata.name}-data`), tb-onefs(정적 PV `k8s-<10hex>` — 디렉터리 /data/k8s_pv/onefs/data/csi/k8s-<10hex>
  를 미리 만들고 PV volumeAttributes {server, share: /data/k8s_pv/onefs/data/csi/k8s-<10hex>}, storageClassName tb-onefs, PVC 에 volumeName).
- 픽스처: ClusterRole kubeflow-admin·edit·view(rules: []). ns team-a·team-b·team-c. 바인딩:
  - team-a: junwoo-song-clusterrole-admin(kubeflow-admin, User junwoo.song@example.com), alice-clusterrole-edit(kubeflow-edit)
  - team-b: alice-clusterrole-admin, cocoa-song-clusterrole-edit
  - 인식되지만 엉뚱한 ID(계정 없음 경고): user-yo-hong-example-com-clusterrole-edit(kubeflow-edit)
  - 인식 안 됨(대조군): namespaceAdmin(kubeflow-admin), default-editor(kubeflow-edit, SA), bob-clusterrole-view(kubeflow-view),
    이름은 규칙에 맞지만 roleRef 가 기본 ClusterRole/edit 인 `bob-clusterrole-edit`(team-a)
  - PVC: team-a models(tb-gpfs)·datasets(tb-nfs)·kraken-kv-cache(tb-onefs 정적), team-b models(tb-gpfs — 같은 이름 다른 ns)·
    scratch(tb-nfs), team-c archive(tb-nfs, 사용자 바인딩 없음), Pending PVC 1(없는 SC)
  - busybox 마커 Job: 각 PVC 에 `.dms-marker` = `<ns>/<pvc>/<volume>` + 작은 샘플 파일.
  - 리더: ns dms-reader, SA dms-inventory, ClusterRole dms-inventory-reader(list: rolebindings, persistentvolumeclaims,
    persistentvolumes, storageclasses; get: namespaces/kube-system), ClusterRoleBinding, 장기 토큰 Secret(SA.secrets 에 넣지 않음).
  - 실데이터 픽스처: `kubectl --context dmsk8s get --raw` 로 받은 목록 JSON 을 DMS tests/fixtures/k8s/ 에 저장(토큰 없음).
- DMS 등록: 스토리지 cephfs-dms = filesystem(기존), k8s-pv-nfs(/k8s_pv/nfs, nfs) = pv, k8s-pv-gpfs(/k8s_pv/gpfs, gpfs) = both,
  k8s-pv-onefs(/k8s_pv/onefs, onefs) = pv. SC 매핑 tb-nfs→k8s-pv-nfs, tb-gpfs→k8s-pv-gpfs, tb-onefs→k8s-pv-onefs. 클러스터 dmsk8s
  (https://10.10.10.20:6443 + CA + 토큰), 같은 클러스터를 https://k8s-cp1:6443 로 한 번 더 등록해 두 클러스터 표기·중복 경고·도메인
  주소 실증(실증 뒤 삭제 가능). 호출 노드 primary k8s-w1 · secondary k8s-w2, 주기 300초. 계정 junwoo.song·alice·cocoa.song(일반) +
  목록 밖 admin 1명.
- 실증 체크리스트: 세 벤더 경로에서 마커 찾기(scan), 일반 사용자·목록 밖 admin·면제 관리자 3역할, PV→PV sync(컨펌까지)·
  cephfs-dms↔PV 혼합(면제자), PV 루트 rm 거부, 401·SAN 불일치(tls_server_name=dms-k8s.local) 오류 표시, primary 대체(호스트에서
  에이전트 프로세스 SIGSTOP → 90초 규칙·expired → SIGCONT), 권한 회수 반영(바인딩 삭제 → 동기화 → 제출 403).
  멀티 클러스터의 다른 API 서버는 단위 테스트 + 같은 클러스터 이중 등록까지(두 번째 VM 은 사용자 결정 — BACKLOG).

## 9. 구현 슬라이스와 파일 소유

0. **테스트베드**(testbed 저장소 + dmsk8s): §8 구축 + 실데이터 픽스처 JSON.
1. **인벤토리 백엔드**: 마이그레이션(§3 전부), `k8s_rbac.py`(D5)·`k8s_sync.py`(D16 배정·상태·오류 분류·jwt exp)·`pv_paths.py`(D9) 순수 모듈,
   `repositories/k8s.py`, `agent/k8s_fetch.py` + runner 워커 스레드, `api/routes_agent.py`(claim·result·보고 응답), `api/routes_k8s.py`
   (§5 관리자 + 사용자 volumes/access), 컨트롤러 `k8s-inventory` 루프·보존, 설정 키, 봉인 용도 `k8s_cluster_token`(서버·프런트 상수),
   cli.py holder, 사유 코드 §4.2, 테스트.
2. **제출·권한·스토리지**: D1~D4·D12~D17, 스토리지 kind·백엔드 라벨·SC 매핑 정리, me 필드, denylist 그룹, 오버레이(testbed-k8s
   `DMS_IDENTITY_MODE`·`DMS_USER_ALLOWED_OPERATIONS`) + prod 오버레이 모드, 사유 코드 §6, 테스트.
3. **포탈**: k8s 연동 화면 5탭(새 파일 features/k8s/*), 스토리지 Dialog·목록, SubmitJob LocationField, 표기, 계정 라벨, e2e 03 순회.
4. 실증·적대적 리뷰·개선 반복, 문서(CLAUDE.md·ARCHITECTURE 불변식 — 「서버 생략 기본값을 root 로 바꾸지 마라」·「backend_type 은
   라벨일 뿐」의 k8s 모드 예외, deploy/README 런북: 대상 클러스터 리더 SA·토큰, DMS 등록 절차).

## 10. 사용자 결정 대기(BACKLOG)

namespaceAdmin(소유자)·이메일 subject 기반 ID · kubeflow-view 를 읽기 전용으로 볼지 · 동기화가 오래 실패할 때 차단 시점 ·
LDAP 코드 삭제 여부 · 배치(BatchCreate) PV 주소 · 심링크 봉쇄를 sync·scan 으로 확장 · 사용자 스캔 경로의 PV 주소화 ·
Pure FlashBlade(PV 별 export) 모델 · 멀티 클러스터 라이브 실증용 두 번째 VM · 「운영자」= 특권 목록·부여 관리자 해석 확인 ·
지정된 사용자(owner)에게 그 작업을 목록에 보일지.
