# DMS 데이터 관리 API — scan / sync / rm

`data-management` 라우터(`/api/v1/data-management`)는 **파일시스템 데이터 잡**을 다룬다:
경로 스캔(`scan`), 경로 간 복제(`sync`), 삭제(`rm`). RM(파일시스템·k8s 쿼터)과 **단일
컨트롤플레인**(API·planner·수명주기·operational DB)을 공유하되, 실제 실행은 **Volcano 잡**으로
분리된다.

이 문서는 **사용법**만 다룬다. 요청 수명주기(request→plan→run)·인증(운영 = mTLS-verified
프로필)·공통 규약은 [`docs/api/README.md`](./README.md)에, DM을 **켜는 설치·활성화**(이미지 빌드,
Volcano/큐/PriorityClass 사전 준비, agent·LDAP·공유 artifact FS 구성)는
[`install/dms-05-dm-jobs.md`](../../install/dms-05-dm-jobs.md)에 있다 — 여기서는 다루지 않는다.

---

## 1. 개념

- **잡 = Volcano 네이티브 Job**(`batch.volcano.sh`). planner가 요청을 `data_job`(worker_role=DM)으로
  만들고, `dms-dm-worker`가 오케스트레이터로서 Volcano 잡을 생성·폴링한다. 잡은 **launcher 1 +
  worker N** 구조로 mpifileutils(`dscan`/`dsync`/`drm`/`nsync`, Open MPI)를 실행한다. 단일 큐
  `dms-data`에 제출되어 **gang-schedule**되고(전원 스케줄 가능할 때만 동시 기동), Volcano의
  `ssh`/`svc` plugin이 launcher↔worker를 배선한다. **dm-worker는 데이터를 만지지 않고**(읽기전용
  오케스트레이터), **잡 pod만 실제 데이터에 접촉**한다.

- **요청자 POSIX 신원으로 실행.** 잡 pod는 요청자의 uid/gid로 뜬다(`runAsUser`/`runAsGroup`/
  `fsGroup`). dm-worker가 preflight에서 `owner_username`을 **read-only LDAP**로 조회해 uid/gid를
  해석한다(RM과 동일 디렉토리, 저장 매핑 없음). 성공/실패의 핵심은 이 신원의 **파일 접근 권한**이다(§7).

- **preview → confirm 게이트(파괴 방지).** `sync`·`rm`은 파괴적이므로 먼저 dry-run **preview**로
  인벤토리와 fingerprint를 산출하고, 그 fingerprint를 **confirm**해야 실제 실행이 일어난다.
  `scan`은 읽기전용이라 preview 없이 즉시 실행된다. (게이트 개념은 [README §4](./README.md#4-preview--confirm-파괴-방지).)

- **비동기.** POST는 요청만 영속화하고 **`202`**를 반환한다. 결과는 잡 상태를 **폴링**해서 읽는다(§6).

- **노드·프로세스 수.** 잡의 병렬도는 연산별 **정책**(operational DB) 기본값을 요청 `resources`로
  상한 내에서 조정한다(§5).

> **MPI Operator/MPIJob을 쓰지 않는다.** DM 스케줄러는 Volcano 네이티브 Job 하나뿐이며, Volcano가
> 단독으로 MPI 워커를 gang-schedule한다.

---

## 2. 요청 공통 형식 (`DataJobRequest`)

세 연산 모두 같은 본문 스키마를 쓰며 `extra="forbid"`다(**최상위 unknown 필드 → `422`**).

| 필드 | 타입 | 연산 | 의미 |
|---|---|---|---|
| `requester_id` | str (필수) | 전부 | 자유형 논리 id. `owner_username`의 **기본값**. |
| `owner_username` | str | 전부 | 잡이 **실제로 실행될 POSIX 신원**(LDAP 조회 키). 생략 시 `requester_id`. API에서 POSIX username 문법 검증. |
| `target` | `{storage_name, path}` | scan·rm | 대상 경로. |
| `source` / `destination` | `{storage_name, path}` | sync | 원본 / 목적지 경로. |
| `priority` | str \| int | 전부 | `High`/`Mid`/`Low`(대소문자 무관) 또는 정수 200/100/50. 기본 `Mid`. |
| `options` | dict | 전부 | 연산별 mpifileutils 옵션(allowlist 검증, §8). |
| `resources` | `{node_count, processes_per_node}` | 전부 | 병렬도. 생략 시 정책 기본값(§5). |
| `memo` | str | 전부 | 자유 메모. |

- **경로는 storage-relative**다: 앞에 `/` 금지, `..` 금지(위반 시 `422`). 기준점은 전역 설정
  `DMS_DM_PATH_BASE`(기본 `mount_path`; `managed_root` 모드면 planner가 storage별 managed_root
  suffix를 prepend). 원본 요청 경로는 `request_payload`에 보존된다.
- **raw CLI 우회 불가.** `options`에 `raw_options`/`command_line` 같은 원시 플래그·SSH키·hostfile을
  주면 `422`. 허용 옵션만 CLI 플래그로 변환된다.
- (레거시: same-storage sync/rm/scan은 평면 필드 `storage_name` + `source_path`/`destination_path`/
  `target_path`도 마이그레이션 호환으로 수용된다. 신규 요청은 구조화 `source`/`destination`/`target`을 쓴다.)

> 아래 예시의 요청/응답은 **shape 참고용**이며 긴 필드는 `…`로 생략했다. 인증 헤더는 생략했는데,
> **운영에서는 client cert로 호출**하고 actor는 인증서 subject에서 파생된다([README §3](./README.md#3-인증--운영-프로필--mtls-verified-header)).
> curl 전체 형태는 §9의 빠른 검증 절차를 본다.

---

## 3. scan (`dscan` — preview 없음, 즉시 실행)

경로를 읽어 파일/디렉토리 수·바이트를 집계한다. 읽기전용이라 confirm이 없다.

```jsonc
// 요청
POST /api/v1/data-management/scan
{ "requester_id": "alice",
  "target": { "storage_name": "cephfs-a", "path": "dms/scan-test" } }

// 202 (요청 영속화 — job_id가 아니라 request_id를 반환)
{ "request_id": "req_b398be…", "status": "Persisted",
  "resource_key": "data.scan:cephfs-a:dms/scan-test", "operation": "data.scan",
  "target": { "storage_name": "cephfs-a", "path": "dms/scan-test" },
  "priority": "Mid", "status_query": "/api/v1/operations/data-jobs" }

// 결과: GET /api/v1/data-management/scan/jobs/{job_id}
{ "job_id": "job_13973e…", "state": "Succeeded", "selected_tool": "dscan",
  "result_summary": { "summary": {
    "file_count": 5, "directory_count": 3, "total_bytes": 22528,
    "scan_root": "dms/scan-test", "error_count": 0 } } }
```

주요 `options`(§8): `summary_only`, `max_depth`, `follow_symlinks`, `one_file_system`.

---

## 4. sync (`dsync` 동일 노드 / `nsync` 분리 노드 — preview → confirm → execution)

경로 간 데이터 복제. **파괴적**이므로 preview→confirm 게이트를 통과해야 한다. source·destination이
같은 노드 집합에 마운트되면 `dsync`, 서로 disjoint한 노드 집합이면 `nsync`가 **자동 선택**된다
(`selected_tool`로만 드러나고 요청 페이로드는 동일).

```jsonc
// 요청
POST /api/v1/data-management/sync
{ "requester_id": "alice",
  "source":      { "storage_name": "cephfs-a", "path": "dms/case/src" },
  "destination": { "storage_name": "cephfs-a", "path": "dms/case/dst" },
  "priority": "Mid",
  "resources": { "node_count": 1, "processes_per_node": 1 } }   // 생략 시 정책 기본(§5)

// 202: scan과 동일 형태 (operation "data.sync", source/destination 채워짐)

// preview 완료: GET …/sync/jobs/{job_id}  → state ConfirmPending + fingerprint
{ "state": "ConfirmPending", "selected_tool": "dsync",
  "result_summary": { "preview": {
    "state": "Succeeded", "fingerprint": "sha256:f758963f…",
    "summary": { "file_count": 2, "directory_count": 2, "dry_run": true,
                 "operation": "data.sync", "phase": "preview", "error_count": 0 } } } }

// confirm (preview fingerprint 필수)
POST /api/v1/data-management/jobs/{job_id}:confirm
{ "requester_id": "alice", "confirm": true, "preview_observed_hash": "sha256:f758963f…" }
// → 200 { "job_id": "job_cb2e80…", "status": "Confirmed" }   (fingerprint 불일치/preview 만료 → 409)

// execution 완료: GET …/sync/jobs/{job_id}
{ "state": "Succeeded", "selected_tool": "dsync",
  "result_summary": { "execution": {
    "state": "Succeeded", "fingerprint": "sha256:e232969a…",
    "summary": { "file_count": 2, "directory_count": 2, "dry_run": false,
                 "phase": "execution", "error_count": 0 } } } }
```

- **confirm 없이는 execution이 절대 없다.** confirm은 별도 엔드포인트
  `POST /api/v1/data-management/jobs/{job_id}:confirm`(`/sync/` 하위가 아님)이며, 본문에
  preview fingerprint를 `preview_observed_hash`로 실어야 한다(`DMS_DM_CONFIRM_REQUIRE_PREVIEW_FINGERPRINT=true`
  기본; 불일치 시 중간 변경으로 간주하고 `409`).
- **fingerprint 위치**: `GET …/sync/jobs/{id}`는 잡을 **최상위**로 반환한다(중간 `job` 래퍼 없음) →
  `result_summary.preview.fingerprint`. list 응답의 `resource_key`에 든 sha256(빈문자 해시)과 헷갈리지 말 것.
- 주요 `options`(§8): `delete`(src에 없는 dest 삭제 — `DMS_DM_SYNC_ALLOW_DELETE=true`일 때만),
  `contents`, `bufsize`, `batch_files`, `chmod`, `chown`.

---

## 5. rm (`drm` — preview → confirm → execution)

경로 삭제. sync와 동일한 preview→confirm 흐름을 탄다. **디렉토리 삭제는 `options.recursive=true`가
필수**다(파일 삭제는 불필요).

```jsonc
// 디렉토리를 recursive 없이 요청하면 거부
POST /api/v1/data-management/rm
{ "requester_id": "alice", "target": { "storage_name": "cephfs-a", "path": "dms/case/victim" } }
// → 422 { "detail": "rm directory requests require recursive=true" }

// 올바른 요청
POST /api/v1/data-management/rm
{ "requester_id": "alice",
  "target":  { "storage_name": "cephfs-a", "path": "dms/case/victim" },
  "options": { "recursive": true } }
// → 202 { "request_id": "…", "status": "Persisted", "operation": "data.rm", … }

// preview → confirm → execution 은 sync와 동일. 결과:
{ "state": "Succeeded", "selected_tool": "drm",
  "result_summary": {
    "summary":   { "file_count": 2, "directory_count": 2, "target_absent": true, "error_count": 0 },
    "preview":   { "fingerprint": "sha256:e7d27d44…", "summary": { "dry_run": true,  "phase": "preview"   } },
    "execution": { "fingerprint": "sha256:ddf65b9b…", "summary": { "dry_run": false, "phase": "execution" } } } }
```

- `target_absent: true` = 삭제 완료로 대상이 더 이상 없음.
- 주요 `options`(§8): `recursive`(디렉토리 필수), `stat`/`lite`(상호배타), `quiet`.
- **디렉토리 rm은 대상의 부모 디렉토리 쓰기 권한이 필요**하다(§7) — 부모가 root 소유면 요청자는
  `posix_permission_denied`로 PreflightFailed된다.

---

## 6. 상태 폴링과 잡 조회 (`request_id` → `job_id`)

**POST 응답의 `request_id`는 job_id가 아니다.** 잡을 찾는 두 경로:

1. **잡 목록으로 job_id 찾기** — `GET /api/v1/data-management/{scan|sync|rm}?requester_id=alice`가
   `job_id`와 `request_id`를 함께 반환하므로, 방금 받은 `request_id`로 행을 찾아 `job_id`를 얻는다.
   쿼리 파라미터: `requester_id`, `storage_name`, `state`, `limit`(1–1000, 기본 100).
2. **잡 상세 폴링** — `GET /api/v1/data-management/{op}/jobs/{job_id}`가 `state`·`selected_tool`·
   `result_summary`·`preflight_result`·`plan`·`worker_pool`을 담은 **단일 객체(최상위)**를 반환한다.

```jsonc
// 목록 (scan/sync/rm 동일 구조)
GET /api/v1/data-management/scan?requester_id=alice&limit=3
// → 200
[ { "job_id": "job_13973e…", "request_id": "req_3af4ff…", "operation": "data.scan",
    "state": "Succeeded", "selected_tool": "dscan", "storage_name": "cephfs-a",
    "target": "dms/scan-test", "created_at": "…",
    "payload_summary": { "requester_id": "alice", "owner_username": "alice",
      "priority_label": "Mid", "options": {}, "…": "…" } } ]
```

**잡 상태 머신**(`state`):

```
PreflightRunning → PreviewRunning → ConfirmPending → (confirm) → Running → Succeeded
                                                                          ↘ Failed / TimedOut
(scan은 preview/confirm 없이 PreflightRunning → Running → Succeeded)
```

**종결(terminal) 상태**: `Succeeded`, `Failed`, `Cancelled`, `TimedOut`, `AuthorizationFailed`,
`PreflightFailed`, `PreviewExpired`. 종결 잡만 삭제할 수 있다(§10).

> 범용 요청 상태(`GET /api/v1/operations/requests/{request_id}`)로도 진행을 볼 수 있다 —
> operations 조회 API는 [`docs/api/operations.md`](./operations.md).

---

## 7. POSIX 권한 모델 (성공/실패의 핵심)

잡 pod는 **요청자 uid/gid로 실행**되고 **supplementary group은 적용되지 않는다** — 접근은 요청자의
**primary gid + 소유권**만으로 결정된다. preview 전 preflight가 요청자 신원으로 권한 프로브를 돌려
실패를 **execution 전에** 차단한다(`posix_permission_denied` → PreflightFailed, 부작용 0).

- **sync 성공 조건**(non-root): `dsync`가 dest를 source와 동일(소유권·권한·타임스탬프)하게 맞추므로,
  **source는 요청자가 읽을 수 있어야** 하고 **destination은 요청자가 소유해야** 한다. 가장 안전한
  정상 케이스는 **source·destination 모두 요청자 소유**(group은 공통). "그룹 읽기만"으로는 dest
  디렉토리 utime에서 실패한다.
- **rm**: 대상의 **부모 디렉토리 쓰기 권한**이 필요하다.
- **운영자 root 실행**(privileged): 임의 사용자 데이터를 이관·정리해야 할 때 root(uid 0)로 실행하는
  경로가 있다. **mTLS-verified operator만** 허용되며(평문 actor의 root 요청은 `403`),
  `owner_username`/`requester_id`가 privileged 집합에 속해야 하고 scope로 대상을 제한한다. 활성화·
  범위 축소는 [`install/dms-05-dm-jobs.md`](../../install/dms-05-dm-jobs.md) 참고. preview→confirm
  게이트는 root에서도 우회되지 않는다.

---

## 8. 요청 옵션 (`options`) 레퍼런스

`options`는 연산별 **allowlist**로 검증된다 — 미지원 키·타입오류·경계초과·precondition 위반은 전부
**`422`(영속화 안 됨)**. 검증 통과분만 CLI 플래그로 변환되고, `option_fingerprint`로 해시돼 preview→
confirm 무결성(옵션 중간 변경 감지)에 쓰인다.

| 연산 | 옵션 | 타입 | 의미 / 검증 |
|---|---|---|---|
| **scan** | `summary_only` | bool | 요약만 |
| | `max_depth` | int ≥ 0 | 탐색 깊이 |
| | `follow_symlinks` / `one_file_system` | bool | 심링크 추적 / FS 경계 내 |
| **sync** | `delete` | bool | src에 없는 dest 삭제. **`DMS_DM_SYNC_ALLOW_DELETE=false`면 `422`** |
| | `contents` / `direct` / `open_noatime` / `quiet` | bool | 내용비교 / direct I/O / noatime / 조용히 |
| | `batch_files` | int [1, 1e6] | 배치 크기 |
| | `bufsize` | int [4096, 1 GiB] | 버퍼 크기 |
| | `chmod` | str | 목적지 권한비트 강제. octal `0750` 또는 `D<oct>,F<oct>`(디렉토리/파일 분리) |
| | `chown` | str | 목적지 소유자/그룹. `USER` / `:GROUP` / `USER:GROUP` |
| **rm** | `recursive` | bool | 디렉토리 삭제 시 **`true` 필수** |
| | `stat` / `lite` | bool | `--stat` / `--lite` (**상호배타**) |
| | `quiet` | bool | 조용히 |

거부 예:

```jsonc
{ "options": { "delete": "yes" } }        // → 422  sync option delete has invalid type
{ "options": { "bufsize": 100 } }         // → 422  option bufsize must be between 4096 and 1073741824
{ "options": { "compress": true } }       // → 422  unsupported sync option: compress
{ "options": { "raw_options": "--foo" } } // → 422  raw command-line option strings are not accepted
{ "options": { "chown": "a:b:c" } }       // → 422  sync option chown must contain at most one ':'
```

> **`chown`으로 다른 소유자 지정은 non-root에서 조용한 no-op이다.** 임의 소유자 변경은
> `CAP_CHOWN`(root)이 필요한데, 일반 요청자(LDAP uid로 실행)가 타 소유자를 지정하면 잡은
> **Succeeded(error_count 0)이지만 소유권은 안 바뀐다**("실패 거부"가 아니라 "성공처럼 보이는
> 미적용"). `<자기>:<자기 그룹>`은 성공. 임의 소유자로의 변경은 root 요청자 전용이다. `chmod`는
> 자기 소유 대상에 적용되므로 일반 요청자도 동작한다.

---

## 9. 노드·프로세스 수 (`resources`)와 정책

병렬도는 연산별 **DM 정책**(operational DB)이 기준이고, 요청 `resources`로 상한 내에서 조정한다.

| 항목 | 기본 | 최대 | 요청 필드 |
|---|---|---|---|
| worker 노드 수 | 3 | 3 | `resources.node_count` |
| 노드당 프로세스 | 3 | 10 | `resources.processes_per_node` |

- **clamp 규칙**: 미지정 → 기본값. 최대 초과 → 최대로 clamp(사유 기록). 이하 → 그대로(1도 허용).
- `process_count = worker_pod_count × processes_per_node`(launcher 1개 별도).
- **검증 권장**: 처음엔 `node_count=1, processes_per_node=1`(launcher+worker 1쌍)로 단순하게 시작한다.
  다중 노드/`nsync`는 모든 참여 노드에 **공유 artifact FS**가 동일 경로로 마운트돼야 rank-script를
  worker가 읽는다(설치 요건 — [`install/dms-05-dm-jobs.md`](../../install/dms-05-dm-jobs.md)).

정책 조회/수정:

```jsonc
GET /api/v1/data-management/policies                 // → 200 (배열; operation은 토폴로지별로 분리)
GET /api/v1/data-management/policies/dsync
PUT /api/v1/data-management/policies/dsync
{ "default_processes_per_node": 4, "max_processes_per_node": 8 }
```

- **`operation ∈ {scan, rm, dsync, nsync}`**(`sync` 아님 — 토폴로지별 `dsync`/`nsync`로 분리).
  잘못된 값 → `422 {"detail": "operation must be one of: scan, rm, dsync, nsync"}`.

---

## 10. confirm / cancel / delete · 신원 denylist

```jsonc
// confirm (preview fingerprint 필수) / cancel (in-flight MPI 잡 종료)
POST /api/v1/data-management/jobs/{job_id}:confirm   { "confirm": true, "preview_observed_hash": "sha256:…" }
POST /api/v1/data-management/jobs/{job_id}:cancel     // body 없음 → { "status": "Cancelled" }

// delete — 종결 잡만 삭제 가능 (in-flight는 409 → 먼저 cancel)
DELETE /api/v1/data-management/jobs/{job_id}          // → 200 { "status": "deleted" } / non-terminal → 409

// 신원 denylist (kill-switch — 기본 빈값=전체 허용)
GET    /api/v1/data-management/identity-denylist                            // → 200 []
PUT    /api/v1/data-management/identity-denylist/{subject_type}/{subject}   { "reason": "…" }  // → { "status": "Denied" }
DELETE /api/v1/data-management/identity-denylist/{subject_type}/{subject}   // → { "status": "Allowed" } (없으면 404)
```

- `subject_type ∈ {requester, owner, group}`(그 외 `400`). 차단된 신원의 요청은 preflight
  `identity_denied`로 Rejected된다.
- **zsh 주의**: colon 엔드포인트는 반드시 `"${jid}:confirm"`(브레이스). `"$jid:confirm"`은 zsh
  수식어로 변형되어 404다.

---

## 11. 종결 상태 / 흔한 실패

정상 경로(요청자가 source를 읽고 destination을 소유)는 `preview → confirm → execution Succeeded`로
dst가 src와 identical해진다. 대표 실패는 대부분 **execution 전에 조기 차단**된다(부작용 0):

| 상황 | 결과 (state / reason) |
|---|---|
| 요청자가 source 읽기 / dest 쓰기 불가 | **`PreflightFailed` `posix_permission_denied`** (§7) |
| LDAP에 없는 유저 / LDAP 미설정 / 다운 | `Rejected` `ldap_identity_not_found` / `ldap_not_configured` / `ldap_unavailable`(fail-closed) |
| denylist 등재 신원 | `Rejected` `identity_denied` (§10) |
| uid/gid가 시스템 하한 미만(시스템/root) | `Rejected` `uid_below_floor` |
| `/`·`..` 경로 · unknown 필드 · 옵션 위반 | API `422` (영속화 안 됨, §2·§8) |
| privileged root를 평문 actor로 요청 | `403` — mTLS-verified operator만 root 실행(§7) |
| 적격 DM 후보/노드 부족 | `Rejected` `no_ready_dm_candidate` / `insufficient_eligible_nodes` |
| storage 매핑의 DM readiness ≠ Ready | `Rejected` `missing_dm_readiness` |
| confirm fingerprint 불일치 / preview 만료 | `409` / `PreviewExpired` |
| execution 중 권한(utime/chown)·디스크·노드 손실·타임아웃 | `Failed` (`BackendApplyFailed` / `UnknownAfterSideEffect`) |

- `no_ready_dm_candidate`·`missing_dm_readiness`는 대개 **설치·활성화 요건 미충족**(agent 이미지에
  mpifileutils 툴 부재, storages ConfigMap sync RBAC 누락, LDAP/`DMS_AGENT_IDENTITY_USERS` 미설정
  등)이다 → [`install/dms-05-dm-jobs.md`](../../install/dms-05-dm-jobs.md).

---

## 12. 빠른 검증 절차

```bash
set +H   # zsh: colon 엔드포인트 histexpand 방지
# 운영(mTLS-verified) 프로필: 클라이언트 인증서로 인증하고 actor는 인증서 subject에서 파생된다
# (평문 x-dms-actor는 신뢰하지 않음 — ingress가 cert를 검증·전달하고 DMS가 mtls:<subject>를 actor로 쓴다).
H=(-sS --cert operator.crt --key operator.key --cacert dms-api-ca.crt \
   -H "authorization: Bearer $DMS_TOKEN")           # 기본 필수 (DMS_AUTH_SHARED_TOKEN)
U=https://dms.example.internal
#   (dev/testbed 프로필에서만: 인증서 없이 -H "x-dms-actor: operator"로 actor를 직접 지정)

# 1) sync 제출 → 202 request_id (실제 POSIX 신원이 다르면 owner_username으로 오버라이드)
curl "${H[@]}" -X POST -H 'content-type: application/json' "$U/api/v1/data-management/sync" -d '{
  "requester_id":"alice",
  "source":{"storage_name":"cephfs-a","path":"dms/case/src"},
  "destination":{"storage_name":"cephfs-a","path":"dms/case/dst"},
  "resources":{"node_count":1,"processes_per_node":1} }'

# 2) 잡 목록에서 request_id로 job_id 찾기
curl "${H[@]}" "$U/api/v1/data-management/sync?requester_id=alice&limit=5"

# 3) preview 폴링 → ConfirmPending → result_summary.preview.fingerprint 확인
curl "${H[@]}" "$U/api/v1/data-management/sync/jobs/<job_id>"

# 4) confirm (브레이스 필수!)
jid=<job_id>
curl "${H[@]}" -X POST -H 'content-type: application/json' "$U/api/v1/data-management/jobs/${jid}:confirm" \
  -d '{"confirm":true,"preview_observed_hash":"sha256:…"}'

# 5) execution 폴링 → Succeeded → dst 검증
curl "${H[@]}" "$U/api/v1/data-management/sync/jobs/${jid}"
```

---

## 다음 문서

- API 개요·인증(mTLS)·수명주기 — [`docs/api/README.md`](./README.md)
- operations 조회 API(잡·요청·work summary) — [`docs/api/operations.md`](./operations.md)
- 운영 런북(점검·유지보수·장애 대응) — [`docs/operations-runbook.md`](../operations-runbook.md)
- DM 설치·활성화(이미지·Volcano·agent·LDAP·공유 artifact FS) — [`install/dms-05-dm-jobs.md`](../../install/dms-05-dm-jobs.md)
- 클러스터 사전 준비 — [`install/dms-01-prerequisites.md`](../../install/dms-01-prerequisites.md)
- 환경변수 레퍼런스(`DMS_DM_*`·`DMS_LDAP_*`) — [`install/dms-06-configuration.md`](../../install/dms-06-configuration.md)
