# Filesystem RM API — Validation Report (CephFS live + GPFS/WEKA source compare)

날짜: 2026-06-15 · 기준 문서: `install/2.dms-rm-api-fs.md` · 배포 이미지: `pkg-01:5000/dms:f5de481-strict`
검증 대상(live): CephFS host-mounted storage mapping **`cephfs-dms`** (`/cephfs/dms`, dms 클러스터).
GPFS/WEKA는 테스트베드가 없어 **소스 코드 기준 비교**(라이브 미실행). **소스 변경 없음** — 발견한
이슈는 §3 "Fix-later"에 기록.

---

## 1. 요약

- CephFS 라이브에서 **모든 filesystem RM API(정상 + 예외/오용)** 를 실행: **46 케이스 전부 기대 동작과 일치**.
  (자동 판정은 45 PASS / 1 FAIL이었으나, 그 1건은 **하니스의 false-negative** — 동작은 정확했고
  검증 스크립트가 사유를 `.message`가 아닌 `.issues[].reason`에서만 찾아 생긴 오판. §2.1 #12 참고.)
- **입력 검증/거부 로직은 백엔드 중립**(`planner._reject_invalid_filesystem_request`)이라, CephFS에서
  확인한 모든 "오용 → Rejected" 케이스는 **GPFS/WEKA에서도 동일**하다(같은 코드 경로).
- 백엔드별 **실행(성공 경로) 차이**는 일부 존재: WEKA `file_count` 미지원, import hint 검증 차이,
  soft-delete 단계 차이 등(§2.2, §2.3).
- 발견된 **고쳐야 할 점 5건**(대부분 마이너/문구) — §3.

---

## 2. 결과

### 2.1 CephFS 라이브 — API별 정상 + 예외 케이스

| # | API | 시나리오(입력) | 기대 | 실제 | 판정 |
|---|-----|---------------|------|------|------|
| 1 | create | 정상(alice owner, 0770, quota, future expiry) | Succeeded | Succeeded · `alice:dms-grp-vb-a 770` | ✅ |
| 2 | create | `expires_at` 누락 | Rejected | `expires_at_required` | ✅ |
| 3 | create | 과거 `expires_at` | Rejected | `expires_at_not_future` | ✅ |
| 4 | create | `users` 빈 배열 | Rejected | `filesystem_users_minimum_one_required` | ✅ |
| 5 | create | `mode:"0700"` (미허용) | Rejected | `filesystem_mode_unsupported` | ✅ |
| 6 | create | `access_group` 비-`dms-` prefix | Rejected | `filesystem_access_group_must_be_dms_managed` | ✅ |
| 7 | create | 중복(이미 존재) | Rejected | `filesystem_resource_already_exists` | ✅ |
| 8 | create | 미지원 필드(top-level `capacity_bytes`) | Rejected | `filesystem_payload_fields_unsupported` | ✅ |
| 9 | create | `quota.capacity_bytes:0` | Rejected | `filesystem_quota_capacity_bytes_invalid` | ✅ |
| 10 | create | `resource_type:"weird"` | Rejected | `filesystem_resource_type_unsupported` | ✅ |
| 11 | create | 불안전 `directory_name`(`bad/name`) | HTTP 422(요청 미생성) | HTTP 422 | ✅ |
| 12 | create | owner 미해결(`requester_id=ghostuser`) | BackendApplyFailed(부작용 없음) | BackendApplyFailed · "not a resolvable POSIX user" · dir 미생성 | ✅ (자동판정만 false-neg) |
| 13 | check | 정상(존재 리소스) | Succeeded | Succeeded | ✅ |
| 14 | check | 미존재 리소스 | Rejected | `filesystem_resource_missing` | ✅ |
| 15 | check | xattr drift 후 | Succeeded + action-required | `filesystem_quota_drifted` 기록 | ✅ |
| 16 | update | quota 증가 | Succeeded | Succeeded | ✅ |
| 17 | update | quota **감소**(FS는 허용) | Succeeded | Succeeded | ✅ |
| 18 | update | 빈 payload | Rejected | `filesystem_update_payload_empty` | ✅ |
| 19 | update | 미존재 리소스 | Rejected | `filesystem_resource_missing` (+ 잉여 `expires_at_required`) | ✅ (§3 #4) |
| 20 | update | 미지원 필드(`users`) | Rejected | `filesystem_payload_fields_unsupported` (+ `filesystem_update_payload_empty`) | ✅ |
| 21 | update | `resource_type:"weird"` | Rejected | `filesystem_resource_type_unsupported` | ✅ |
| 22 | sync | 정상 | Succeeded | Succeeded | ✅ |
| 23 | sync | 미존재 리소스 | Rejected | `filesystem_resource_missing` | ✅ |
| 24 | block | 정상 | Succeeded · mode→`0000` | Succeeded · `0` | ✅ |
| 25 | initialize | 정상(unblock) | Succeeded · mode 복원 | Succeeded · `770` | ✅ |
| 26 | block | 미존재 리소스 | Rejected | `filesystem_resource_missing` | ✅ |
| 27 | block | `resource_type:system` 차단 거부 | Rejected | `resource_type_cannot_be_blocked` | ✅ |
| 28 | assign-quota | 비관리 디렉토리에 quota_only | Succeeded | Succeeded | ✅ |
| 29 | assign-quota | `quota` 누락 | Rejected | `filesystem_quota_required` | ✅ |
| 30 | assign-quota | 이미 full-managed 리소스 | Rejected | `filesystem_resource_already_exists` | ✅ |
| 31 | import | 멤버 있는 `dms-grp-*` 소유 dir(mode 일치) | Succeeded | Succeeded | ✅ |
| 32 | import | `expected_mode` 불일치(preflight) | BackendApplyFailed | `filesystem_import_preflight_failed` | ✅ |
| 33 | import | root 소유·빈 그룹(fix A) | Succeeded | Succeeded | ✅ |
| 34 | import | 이미 관리 중 | Rejected | `filesystem_resource_already_exists` | ✅ |
| 35 | delete | quota_only 삭제 거부 | Rejected | `filesystem_quota_only_delete_refused` | ✅ |
| 36 | delete | 미존재 리소스 | Rejected | `filesystem_resource_missing` | ✅ |
| 37 | delete | 정상 soft-delete | Succeeded · 잠금(0000)+그룹제거+데이터보존 | Succeeded · mode `0`, group removed | ✅ |
| 38 | expiration-sweep | dry-run(만료 리소스) | Succeeded(부작용 없음) | Succeeded · `would_block` | ✅ |
| 39 | expiration-sweep | live(만료 user 리소스 차단) | Succeeded · mode→0000 | Succeeded · `0` | ✅ |
| 40-43 | query | GET 단건/목록/expiring/`requests`(requester_id 누락) | 200/200/200/422 | 200/200/200/422 | ✅ |

> 모든 mutating 요청은 비동기(`202 Persisted`) → `GET /operations/requests/{id}` 폴링으로 최종 상태 확인.
> 정상 케이스는 백엔드(CephFS) 실제 상태(stat/getfattr/getent)까지 대조해 부작용을 확인함.

### 2.2 백엔드 중립 vs 백엔드별 (GPFS/WEKA 소스 비교)

**백엔드 중립(= CephFS 결과가 GPFS/WEKA에 그대로 적용):** 위 표의 **모든 Rejected/422 케이스**(#2–#11,
#14, #18–#21, #23, #26–#27, #29–#30, #34–#36)는 `planner._reject_invalid_filesystem_request`에서
처리되며, 이는 `resource_kind == filesystem`이면 백엔드와 무관하게 동일하게 실행된다. 따라서 입력
검증/거부 동작은 **세 백엔드 동일**.

**백엔드별 실행(성공/precondition 경로) 차이:**

| 항목 | CephFS | GPFS | WEKA |
|------|--------|------|------|
| 단위 | directory (`mkdir`) | fileset (`mmcrfileset`/`mmlinkfileset`, junction) | directory (`mkdir`) |
| quota 적용 | `setfattr ceph.quota.max_bytes/max_files` | `mmsetquota` (8 MiB 올림) | `weka fs quota set` |
| **`file_count` quota** | ✅ 지원 | ✅ 지원(inode) | ❌ **거부**(`_reject_unsupported_quota_fields` → BackendApplyFailed) |
| owner 해석 | `pwd.getpwnam`(워커 NSS/SSSD) | `identity_lookup` LDAP | `identity_lookup` LDAP |
| owner 미해결 | BackendApplyFailed (strict) | BackendApplyFailed (strict) | BackendApplyFailed (strict) |
| allowed-user access probe | ✅ host script `sudo -u` | ✅ (`access_validation`) | ✅ (`access_validation`) |
| **import `access_policy.expected_mode`/`expected_group` 검증** | ✅ **강제(fail-closed)** | ❌ **무시** | ❌ **무시** |
| import 그룹 자동발견(`_adopt_full_group`) | ✅ | ✅ | ✅ |
| 빈-그룹 import(fix A) | ✅ | ✅ | ✅ (공유 `ensure_group_members`) |
| soft-delete 단계 | `chown root:root`+`chmod 000`→그룹삭제 (quota reset 안 함) | `chown`→`chmod 000`→그룹삭제 (quota reset 안 함) | **`quota reset`**→`chown`→`chmod 000`→그룹삭제 |
| 영구삭제(수동) | `rm -rf` | `mmunlinkfileset`+`mmdelfileset` | `rm -rf` |

### 2.3 GPFS/WEKA에서 결과가 달라지는 케이스(예상)

CephFS 표의 케이스를 GPFS/WEKA로 그대로 실행하면 **달라지는** 것만:

- **#9/#16/#17/#28 등 `file_count` 포함 quota** → WEKA에서는 `file_count`가 있으면 **BackendApplyFailed**
  (CephFS/GPFS는 Succeeded). 용량만 보내면 WEKA도 Succeeded.
- **#32 import `expected_mode` 불일치** → CephFS는 BackendApplyFailed(preflight)지만 **GPFS/WEKA는 힌트를
  무시하고 Succeeded**(§3 #2).
- 정상 #1/#24/#37 등의 **부작용 형태**가 다름(fileset vs directory, soft-delete 단계) — 의미상 동등.

---

## 3. 발견 이슈 — ✅ 전부 수정 완료 (2026-06-15, 후속 커밋)

> 아래 1~5는 모두 수정되어 main에 반영됐다(전체 스위트 215 passed). 각 항목 끝의 **[FIXED]** 참고.

1. **(마이너, 전 백엔드) create의 owner-precondition 실패 issue_type 오라벨.**
   owner 미해결 create가 `BackendApplyFailed`로 끝날 때 `verification_summary.issues[].issue_type`가
   **`filesystem_block_failed`** 로 기록됨(메시지는 정확: "requester '…' is not a resolvable POSIX user").
   create인데 "block_failed"는 오해 소지 → `filesystem_create_precondition_failed`(또는
   `filesystem_owner_unresolvable`) 류로 라벨 교체 권장. 위치: owner precondition 실패를 action-required로
   집계하는 경로.

2. **(중간, GPFS/WEKA) import `access_policy.expected_mode`/`expected_group` 힌트 미검증.**
   문서(`2.dms-rm-api-fs.md` §3.8)는 두 필드를 "검증용 hint"로 안내하나, **CephFS만 fail-closed로
   강제**(`cephfs.py` import_directory)하고 **GPFS/WEKA `_adopt_existing`은 무시**한다. → import 시
   잘못된 mode/group을 줘도 GPFS/WEKA는 통과. **둘 중 하나로 통일** 권장: (a) GPFS/WEKA에도 동일 preflight
   추가, 또는 (b) 문서에 "expected_mode/expected_group은 CephFS에서만 강제"로 명시.

3. **(문서, 정보) WEKA `file_count` 거부의 명시성.**
   WEKA는 `file_count`를 조용히 무시하지 않고 **명시적으로 거부**(BackendApplyFailed)한다. 문서 §3.2/§3.9에
   "WEKA file_count 미지원"은 있으나, "포함 시 **요청이 실패**"임을 한 줄 더 분명히 하면 좋음(현재는 미지원
   ≈ 무시로 오해 가능).

4. **(마이너, 전 백엔드) update(PATCH) on missing resource가 잉여 `expires_at_required`를 같이 보고.**
   미존재 리소스 PATCH는 `filesystem_resource_missing`이 정답인데 `expires_at_required`도 함께 나온다
   (update는 expires_at 필수가 아님). 기능엔 영향 없음(어차피 Rejected). `_append_expiry_issues`가
   update + 빈 existing_desired에서 expires_at를 요구하지 않도록 정리 권장.

5. **(마이너, 전 백엔드) update(PATCH)에 미지원 필드만 보내면 사유 2개(`*_unsupported` + `*_payload_empty`).**
   중복 느낌이나 오답은 아님. 우선순위 낮음.

> 위 1·4·5는 라벨/사유 문구 수준(기능 영향 없음). **2가 실질적 동작 불일치**로 우선순위 가장 높음.

### [FIXED] 수정 매핑 (2026-06-15)
- **#1** GPFS/WEKA `_adopt_full_group`에 `expected_mode`/`expected_group` preflight 추가
  (부작용 이전 fail-closed, CephFS 패리티) — `gpfs.py`, `weka.py`. 단위 테스트
  `test_gpfs_backend.py`/`test_weka_backend.py`의 `*_import_expected_*_mismatch_fails_closed`/`*_hints_match_adopts`.
- **#2** `workers._rm_precondition_issue`: owner 해석 실패 → `filesystem_owner_unresolved`,
  create 일반 실패 → `filesystem_create_failed`(operation-aware 폴백). 테스트
  `test_filesystem_precondition_issue_type.py`.
- **#3** `2.dms-rm-api-fs.md` §3.2/§3.9: WEKA `file_count`는 **주면 `BackendApplyFailed`로 실패**임을 명시.
- **#4/#5** `planner._append_expiry_issues`(missing 리소스에서 `expires_at_required` 미발생) +
  update 분기(미지원 필드만이면 `_payload_empty` 미동반). 테스트
  `test_phase10_filesystem_rm.py::test_phase12_filesystem_update_requires_existing_quota_only_payload`.

---

## 4. 재현 방법

```bash
# CephFS 라이브 검증 드라이버(정상+예외 46케이스, 결과 RESULT| 라인으로 출력)
bash <세션 tmp>/fs_rm_full_validate.sh   # 예: $CLAUDE_JOB_DIR/tmp/fs_rm_full_validate.sh
# 환경: source /data/mgmt_storage/dms-deploy/secrets/dms-env.sh (DMS_API_URL/DMS_TOKEN)
# 백엔드 확인: ssh -i …/dms-backend-ssh root@dms-w1 'stat/getfattr/getent …'
```
GPFS/WEKA 라이브 검증은 해당 클러스터 부재로 미실행(소스 기준 비교만). 검증 시 위 §2.2/§2.3의 차이를
기대값으로 사용.
