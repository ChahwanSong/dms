# dms-k8s 인계 — mpifileutils 7a3189c 와 DMS d188~d193 수정 (2026-10-28, DMS-SSC 세션 작성)

이 문서 하나로 dms-k8s 라인이 이번 수정들을 판단·이식할 수 있게 정리했다. **핵심은 mpifileutils(포크) 업데이트**이고, DMS 쪽
변경은 그 도구 동작을 정확히 쓰고 보여 주기 위한 것이다. 참조한 상태: `origin/dms-ssc` = 978af29(이 문서 커밋 직전),
`origin/dms-k8s` = 694cd40, 포크 `main` = `nsync-fixes-d189` = **7a3189c**.

> dms-k8s 는 LDAP 이 없고(k8s 신원 모드), **모든 잡이 root** 로 돌며, **k8s PV/PVC 주소**를 지원하도록 갈라져 있다. 아래 판정은
> 그 차이를 반영했지만, 이식 여부·방식의 최종 결정은 dms-k8s 세션(과 그 사용자)의 몫이다. 코드는 그 라인의 CLAUDE.md 규약을 따른다.

---

## 0. 한눈에 — 권장 순서

| 순서 | 할 일 | 가치 | 난이도 |
|---|---|---|---|
| 1 | **mpifileutils 핀 ae781bd → 7a3189c** (`deploy/docker/Dockerfile.mpifileutils` 의 `ARG MPIFILEUTILS_REF`) | **높음** — ae781bd 에 root 데이터 손실 결함(C8) 등이 남아 있다 | 낮음(주석 블록만 충돌) |
| 2 | d190 포탈 옵션 문구(open_noatime·direct 도움말) | 중간 | 낮음 |
| 3 | d191 경로 봉쇄 — k8s 의 PV 봉쇄와 **설계를 통일**해서 | 높음(직접 경로 sync 가 지금 봉쇄 밖) | 중~높음(설계 결정 포함) |
| 4 | d192 러너(summary v2 + 실패 사유) + 제어면(통계·옵션 검증) | 높음 | 러너·제어면 cherry-pick 거의 깨끗 |
| 5 | d192/d193 포탈(결과·컨펌·배치·대시보드·도움말) | 높음 | 손으로(포탈은 k8s 가 크게 갈라짐) |

1 을 단독으로 먼저 올려도 된다(현재 k8s 러너 파서와 출력 형식이 호환). 다만 4 없이 올리면 새 도구가 내는 「부분 실패 → 0 이 아닌 종료」가
화면에 일반 `execution_failed` 로만 보인다.

---

## 1. mpifileutils 포크 — 무엇이 바뀌었나 (ae781bd → 7a3189c)

저장소 `https://github.com/chahwansong/mpifileutils.git`(이전 위치 — GitHub 가 `ChahwanSong/mpifileutils` 로 안내), 브랜치
`nsync-fixes-d189`, 2026-10-28 `main` 도 같은 커밋으로 fast-forward. DMS 빌드는 전체 clone 뒤 SHA checkout 이라 브랜치 무관하게
`MPIFILEUTILS_REF=7a3189cba75c93b4e66da722235f370790a2f88a` 로 지정하면 된다.

### 1.1 커밋

| 커밋 | 도구 | 바뀐 동작 |
|---|---|---|
| f53e458 | dsync | 타입 교체(디렉터리↔비디렉터리)에서 지우지 못한 목적지 항목은 그 소스 항목과 **그 아래 전부를 복사에서 뺀다**(전엔 복사가 실패하면서 메타데이터가 남은 디렉터리에 0644 를 찍어 root 외 아무도 못 들어갔다 — D3). 새 목적지에서 거짓 「Failed to stat」 없음. |
| c4dbf20 | nsync | **타입 교체 안전화**: 비어 있지 않은 디렉터리는 `--delete` 없이 바꾸지 않음(전엔 재귀 삭제 — **C8 데이터 손실**) → `replace-dir` 실패 1건. 배치 ≥2 타입 교체 선처리(C4·C7·C10), 목적지 rank 배리어, `--batch-files 0` 허용(C6). **항목별 실패 목록**(`Failed items: X (copy a, …)` + `  failed <op> \`path\`: <strerror> (errno N)` — 경로 이스케이프) + `--max-errors N`(기본 **65000**, 0=무제한), ENOSPC/EDQUOT/EROFS 즉시 정지, 계획을 다 못 하면 rc≠0, 소스 스캔 오류면 그 실행의 `--delete` 끔. **경로 안전**: 스캔·소스 읽기·목적지 쓰기를 루트 fd 아래 `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)`(없으면 O_NOFOLLOW 걷기) — 목적지 루트 심링크 추종(C12) 등 차단. 목적지 파일 O_NONBLOCK·일반 파일만, 새 파일 O_EXCL 소유자 비트만, set-ID 는 소유자를 못 맞추면 제거. 소스 FIFO·소켓·장치는 건너뜀(목적지 같은 이름 항목 유지, `--delete` 여도). 작은 파일 다수 배치의 O(n²) 제거. |
| 0a0c361 | 시험 | `test/nsync/` 회귀 하네스(개발 컨테이너 Dockerfile 포함). |
| db1e33c | nsync | `--open-noatime` 이 **실제로 동작**(빌드에 `_GNU_SOURCE` 가 없어 O_NOATIME 이 0 이었다 — k8s 의 ae781bd nsync 는 지금 atime 을 보존하지 않는다). 남의 파일은 EPERM 뒤 O_NOATIME 없이 다시 열어 복사. `--direct` 는 nsync 에서 받기만 하는 무동작(로그 「--direct has no effect in nsync」). `Options:` 줄 끝에 `open-noatime=N`. |
| 43ba468 | 공용(dsync) | 복사 읽기 O_NOATIME EPERM 폴백(비 root 의 남의 파일). |
| 461bb73 | 시험 | T35~T37. |
| e418ca7 | 공용(dsync) | `--contents --open-noatime` 비교 열기도 EPERM 폴백. |
| **998608b** | drm·dsync | **지우지 못한 항목이 남으면 rc≠0**(전엔 rc 0) — `mfu_flist_unlink` 가 int 를 돌려준다. `Removed N items in …` 은 **실제로 지운 수**(전엔 시도 수) + 실패 시 오류 줄 `Remove failed for F of T items`. dsync 목적지 부모 쓰기 불가 → rc 1(전엔 아무것도 안 하고 rc 0). dsync 가 소스 특수 파일에 메타데이터를 시도하지 않고 경고 한 줄 `Skipped N special file(s) in the source (FIFO, socket or device): not copied`. |
| **6172405** | nsync | 실행 끝에 `Completed actions: copy=… mkdir=… symlink-update=… meta-update=… remove=… copied-bytes=…`(**실제로 반영된** 종류별 수, dry-run 에는 없음). `nsync.md` §13.7. |
| 7a3189c | 시험 | README(117 checks). |

ae781bd 에 남아 있는 결함(dms-ssc d188 실증): **C8**(nsync 디렉터리→파일 교체에서 `--delete` 없이 목적지 전용 항목 재귀 삭제 — k8s 는
모든 잡이 root 라 그대로 데이터 손실), C4·C7·C10(배치 ≥2·목적지 rank ≥2 타입 교체 실패), C6(`--batch-files 0` 거부), C12(목적지 루트
심링크를 따라가 0:0 755 생성), D3(dsync 디렉터리 0644). 전부 c4dbf20·f53e458 에서 고쳐졌다.

### 1.2 DMS 가 읽는 출력 줄(계약)

모든 MFU 로그 줄은 `[YYYY-MM-DDTHH:MM:SS] ` 로 시작하고, 오류 줄은 `[ts] [rank] [file:line] ERROR: <msg>` 다. nsync 는 경로의 제어 문자·
`\`·`:`·`=` 를 `\xHH` 로 찍어 이름으로 줄을 위조할 수 없다. **dsync·drm 은 경로를 그대로 찍는다**(줄바꿈 포함 가능) — 파서는 이 차이를
전제로 짠다.

| 도구 | 줄 | 뜻 |
|---|---|---|
| nsync | `Planned actions: copy=… mkdir=… symlink-update=… meta-update=… remove=… skipped-dst-only=…` | 계획(실행·미리보기 공통). 정지한 실행은 돈 배치만. |
| nsync | `Completed actions: … copied-bytes=…` | 실제 반영(실행만, 6172405). |
| nsync | `Failed items: X (op a, …); listed below: Y` + `  failed <op> \`path\`: … (errno N)` | 실패 목록. op = scan·type-change·replace-dir·remove·mkdir·symlink·copy·metadata. |
| nsync | `Type changes: to-directory A, from-directory B` / `A directory that is replaced by a non-directory must be empty unless --delete is given…` | 타입 교체(뒤 줄은 `--delete` 없을 때만). |
| nsync | `Skipped N special file(s) …` / `Source scan had N error(s): --delete is off` / `Encountered N scan error(s)` / `Not attempted after a stop: N` / `Stopping after batch … reached --max-errors N` / `… (stopped early)` | 특수 파일·스캔 오류(소스 쪽이면 삭제 끔)·정지. |
| dsync | 최종 블록 `Seconds: x` 다음 `Items: N`, `Data: … (N bytes)` | 만든 항목·쓴 바이트(복사 계획 블록의 `Items:` 와 구별 — 중단되면 최종 블록이 없다). |
| dsync | `ERROR: Failed to copy \`src' to \`dst'` / `File \`dst' mknod() failed` / `Create \`…' mkdir() failed` | 복사·생성 실패(mknod 실패 파일은 Items 에 없다). |
| dsync | `N destination item(s) changed type but could not be removed` / `Type change not applied: \`…' could not be removed (still a directory), a non-empty directory is replaced only with --delete; …` | 타입 교체 거부(뒤 꼬리는 디렉터리 + `--delete` 꺼짐일 때만). |
| drm·dsync | `Removed N items in …` + `Remove failed for F of T items` | 실제로 지운 수 + 실패(998608b). |
| 공용 | stderr `ABORT: …` / `MPI_ABORT was invoked` | MPI 중단(stdout 에는 없다). |

### 1.3 검증 하네스

포크 `test/nsync/run.sh`(117 checks, T01~T43) — 개발 컨테이너(`test/nsync/dev-image/`)에서 root 로. 이번에 더한 것: T38(dsync
`--contents --open-noatime` 비 root), T39(drm 부분 실패 rc·수), T40(dsync `--delete` 잔여 → rc≠0), T41(nsync Completed), T42(dsync
부모 쓰기 불가 rc 1), T43(dsync 특수 파일). `docker exec mfu-dev bash /work/tests/run.sh T39 T41` 처럼 접두로 고른다.

### 1.4 포크에 남은 것(dms-ssc BACKLOG)

dsync 미리보기는 변경 계획을 내지 않는다(소스 walk 수만) · **dsync 는 복사에 실패한 파일을 목적지에 0 B 로 남긴다**(mknod 먼저 —
d192 실증) · dsync·drm 오류 줄 경로 미이스케이프(사유 위조, 낮음) · dsync walk 오류는 `--delete` 일 때만 보임 · 루트 고정(B: 루트를
O_NOFOLLOW 로 한 번 열어 dev/ino 고정 — 봉쇄의 마지막 몇 초 창) · nsync P3 잔여.

---

## 2. DMS(dms-ssc) 변경 → dms-k8s 적용 판정

판정 근거: 커밋별 3-way 병합 시뮬레이션(실제 cherry-pick·테스트는 하지 않았다). merge-base b66b117(2026-08-30) — k8s 는 「nsync 역할별
볼륨」(ssc 4f2c7c9 = k8s 928b3c9) 뒤에 갈라져 ssc 98cdf61 이후 커밋은 k8s 에 없다(포크 핀만 k8s 가 따로 ae781bd).

| dms-ssc 커밋 | 내용 | 판정 |
|---|---|---|
| 1e1dedb | 핀 → ae781bd | **이미 같은 상태**(k8s f1d5e4e/e2808e0) |
| 2dceca8 → f1cd393 → **c4a2562** | 핀 → c4dbf20 → 461bb73 → **7a3189c** | **권장**. 두 브랜치 차이는 주석 블록 + ARG 줄뿐 → ssc 판 `Dockerfile.mpifileutils` 를 통째로 가져오고 k8s 주석(PV 목적지 전제 등)을 다시 붙인다(공용 파일 — 바이트 동일 목표). `tests/test_k8s_submit.py::test_job_image_pins_an_nsync_that_does_not_follow_destination_links` 는 거부 목록 방식이라 통과. |
| d7ec11e | d190 포탈: open_noatime·direct 도움말 | **손으로 일부**. 「비 root 에도 open_noatime」은 k8s 에 이미 같은 효과(전부 root — SubmitJob 이 늘 싣는다). 가져올 것: `optionRules` 의 `OPEN_NOATIME_HELP`·`DIRECT_HELP`(「nsync 면 무시」), `requestSpec.noatimeWarn`·`RequestSpecCard.NOATIME_DETAIL` 제거(옛 비 root 행 전용). 「실행 신원 소유가 아닌 파일…」 문구는 root 전용에 맞게 단순화. 충돌: SubmitJob 3·SubmitJob.test 5·optionRules 1·RequestSpecCard 1. |
| 4a13415 | d191 경로 봉쇄 | **손으로 + 설계 결정**(§3) |
| **4a27b6e** | d192 실패 사유·summary v2·통계·포탈 | 러너·제어면 cherry-pick 거의 깨끗, 포탈 손으로(§4). 선행: c4a2562(7a3189c — 파서가 `Completed actions` 등 새 줄에 의존), d191 사실상 선행. |
| d83446f | d193 포탈 실화면 반영 | 포탈 이식 때 함께(§4.4) |
| 오버레이·문서 커밋 | ssc 클러스터·실증 기록 | **해당 없음**(k8s 는 `deploy/overlays/testbed-k8s` dN). BACKLOG 의 포크 항목만 옮길 가치. |

---

## 3. 경로 봉쇄(d191) — k8s PV 봉쇄와 통일

dms-ssc 사용자 결정: 「경로의 모든 단계를 보고 한번 아니라 여러번 보고 거부」 + 「Rm도 똑같이 거부」. 규칙 하나 — 경로의 실제 위치 ==
realpath(managed_root) + 글자 그대로의 상대경로(끝 성분 포함, 아직 없는 끝부분은 존재하는 가장 깊은 상위로, managed_root 자신·그 위
링크는 허용, `//`·`/./`·`/../` 거부).

| 항목 | dms-ssc d191 | dms-k8s 현재 |
|---|---|---|
| 규칙 | `execution_manifests._PATH_CONTAINED_FN`(셸) / `runner.path_redirected`(Python 미러) | `_PV_CONTAIN_FN`(execution_manifests.py:516) / `contain.pv_path_redirected` |
| 대상 | 모든 sync 소스·목적지 + rm 대상 | **PV 주소인 쪽만**(`paths["contain"]`) + PV scan 대상 |
| 직접 경로 sync | 봉쇄 | **봉쇄 없음** — k8s 는 전부 root 라 C12(루트 심링크로 남의 디렉터리에 쓰고 소유 변경)·C13(소스 링크 + delete 로 목적지 삭제)이 열려 있다 |
| 없는 끝 | 가장 깊은 존재 상위 | 부모 하나(없으면 막힘) |
| 사유 | `source_path_redirected`·`destination_path_redirected`(신설), rm `target_path_redirected` | 전부 `target_path_redirected` + `DMS_JR_PV_PATH_REDIRECTED` 줄 |
| 마지막 재확인 | sync 워커 셸(sshd 앞), rm 러너(drm 직전) | dsync·dscan 런처 mpirun 직전, nsync rank 마다 도구 exec 직전 — **k8s 쪽이 더 늦어 창이 짧다** |
| rm 끝 링크 | **거부**(ssc 사용자 결정) | 허용(「삭제는 링크만 지웁니다」) |

권장: 심링크 규칙을 하나로 — `_PATH_CONTAINED_FN`/`path_redirected`(가장 깊은 상위 방식)를 모든 쪽(직접 경로 포함)에 쓰고, PV 는 「PV 루트
아래」 글자 경계만 따로(`_PV_WITHIN_FN` 식). 재확인 위치는 k8s 방식(런처·rank)을 직접 경로까지 넓히는 쪽이 낫다. 사유 코드를 PV sync 에도
source/destination 으로 나눌지는 k8s 세션 결정. **rm 끝 링크 거부는 ssc 사용자 결정이라 k8s 사용자 확인이 필요하다.**
기계적 충돌(시뮬레이션): execution_manifests 6곳(`_preflight_script` sync·rm, PREFLIGHT_REASONS, EXECUTION_REASONS), stepper 1곳(루트를
한 번 읽는 자리에 `source_root`·`destination_root` 키만), runner 1곳(k8s 는 `TARGET_PATH_REDIRECTED` 를 contain 에서 import), api.ts 1, 테스트
4. 깨끗: 워커 셸 블록·`_worker_path_env`·`_PATH_CONTAINED_FN`·`test_rm_containment_sh.py`·`test_sync_containment_sh.py`(신규). 깨질 k8s
테스트: `test_direct_dest_owner`·`test_pv_containment_sh`·`test_pv_owner_chown`(sync JobSpec 에 두 루트 키가 없으면 `_sync_roots` ValueError),
`test_pv_containment_sh::test_non_pv_jobs_are_byte_identical`(전제가 바뀐다).

---

## 4. d192·d193 — 실패 사유를 도구 출력에서 정확히 · summary v2 · 통계 · 포탈

dms-ssc 사용자 요청: 「실패사유는 최대한 정확하게 포탈에 표시해서 사용자가 이해할수있도록. 통계 등도 구현 업데이트 된 내용 반영해서
정확하게. 포탈은 UI 개선도 같이」. 포탈 괴리 감사(35건) + 구현 적대적 리뷰(40건) 반영.

### 4.1 러너(잡 이미지) — `src/dms_job_runner/parsers.py`·`runner.py`

- **summary.json v2**: 핵심 3키(`returncode`·`files`·`bytes`)는 그대로, 상세 키를 더한다(`detail_version: 2`, `tool`, `dryrun`, `quiet`,
  nsync `planned`·`done`·`copied_bytes`·`planned_bytes`·`type_changes`·`type_change_needs_delete`·`failures{total,by_op}`·`not_attempted`·
  `stopped_early`·`stop_cause`·`scan_errors`·`source_scan_errors`·`delete_disabled`·`metadata_*`·`noatime_fallbacks`·`skipped_special`,
  dsync `source_items`(미리보기)·`copied_items`·`copied_bytes`·`copy_failed`·`create_failed`·`removed`·`remove_failed`·`type_change_refused`·
  `scan_incomplete`·`tool_errors`, drm `target_items`·`removed`·`remove_failed`, 공용 `failure_samples[]`(≤10, op·경로 256자·errno)·`aborted`·
  `reason`). 키 표 = `parsers` 모듈 docstring + `parse_tool_detail`. 파일은 ASCII(json ensure_ascii) — 도구 문자열은 표본 경로 하나뿐.
- **files 뜻**(통계의 원천): nsync 실행 = `done` 합(실제 반영, 계획 합 아님), dsync 실행 = 최종 Items − 실패 복사(mknod 실패 겹침 제외), drm
  실행 = 실제로 지운 수. 미리보기 뜻은 그대로(nsync 계획 합 · dsync 소스 walk 수 · drm 지울 대상 walk 수).
- **위조 방어**: 요약 줄 정규식을 MFU 시각 접두사에 고정·마지막 매치, dsync `Removed` 는 둘 이상이면 모름, `Remove failed` 는 마지막 `Removed`
  뒤의 것만, dsync Items 는 최종 블록만, abort 는 stderr 만.
- **도구 결과 사유**: 도구가 0 이 아닌 코드로 끝나면 `_derive_reason`(처음 맞는 규칙) → launcher 로그 첫 줄 `DMS_EXEC_REASON=<code>` →
  stepper 가 기존 마커 경로(`execution_manifests.EXECUTION_REASONS` ⊇ `TOOL_FAILURE_REASONS`, 러너 상수와 계약 테스트)로 승격.
  | 코드 | 조건 |
  |---|---|
  | `tool_aborted` | stderr 에 MPI 중단 |
  | `destination_full` / `destination_read_only` | (sync 만) nsync stop_cause 또는 실패 errno 28·122 / 30 |
  | `sync_stopped_max_errors` | nsync `--max-errors` 정지 |
  | `sync_type_change_refused` | 비어 있지 않은 디렉터리 + `--delete` 꺼짐 **증거가 있을 때만**(nsync `replace-dir` 만 + 안내 줄, dsync 꼬리 줄 수 == 거부 수 + 제거 errno 39/17) |
  | `source_scan_incomplete` | nsync 소스 쪽 스캔 오류 + 삭제가 실제로 꺼짐(`Source scan had N` == 전체) |
  | `sync_items_failed` / `rm_items_failed` | 그 밖의 항목 실패 |
  | `preview_scan_incomplete` | nsync 미리보기 스캔 오류(미리보기는 이것과 abort 만) |
  그 뒤 도구 출력 꼬리(stdout 80·stderr 20줄, 총 10 KiB, `  | ` 접두 — 마커로 시작할 수 없다)를 덧붙여 포탈 「실행 로그」에서 원인이 보인다.
- 조기 반환(미지 도구·워커 준비 실패·봉쇄 거부)과 quiet 실행은 3키(quiet 는 수 null + `quiet: true`).
- k8s 이식: `parsers.py` 충돌 0(k8s 의 `total_bytes`·`_is_count` 유지). `runner.py` 텍스트 충돌 3 — import 목록(둘 다), 상수 블록(d192 상수
  추가, `TARGET_PATH_REDIRECTED` 재정의는 빼고 contain 에서), k8s 7b 블록(nsync rank 마커 끌어올리기)을 d192 의 8(summary v2)·9(사유 마커·꼬리)
  **앞에**. 깨질 k8s 테스트: `test_pv_containment_sh.py:330-331` 의 부분 문자열 검사(rc≠0 꼬리에 `"  | DMS_EXEC_REASON=…"` 가 다시 찍힌다) →
  줄 단위 비교로. 새 테스트 `tests/test_job_runner_tool_detail.py`(실 출력 15종 픽스처)는 그대로 가져간다.

### 4.2 제어면

- 미리보기 실패의 summary 는 `result_summary`(표시)에만 — `files_count`/`bytes_count` 는 실행 결과만(`data_jobs.set_artifact(promote_counts=…)`).
- metrics API: `files_total`/`bytes_total` → **`sync_items_total`·`sync_bytes_total`·`rm_items_total`**(모든 종단, 실패·중단된 실행의 부분 처리
  포함, 성공이 아닌 행은 summary v2 행만), 분해 행 `failed` = Failed+TimedOut + `rejected` + `cancelled`(Cancelled+PreviewExpired),
  `failure_reasons` 에서 취소·만료 제외. **프런트와 함께 옮겨야 한다**(응답 키가 바뀐다).
- 옵션: direct + bufsize → bufsize 는 4096·2^k ≤ 4 MiB(422 `direct_bufsize_misaligned`) — dsync O_DIRECT 가 4 MiB 청크의 약수가 아닌 버퍼로
  큰 파일에서 MPI 중단. 단건·배치 생성·항목 추가/수정/교체·실행 설정 변경 모두(k8s 의 배치 CSV·PV 검색 경로도 `domain.validate_options` 를
  지나는지 확인 필요).
- 배치: `list_items_detail` 에 `job_tool` 조인, 확인 감사 행 `files_by_tool`(잡 행 도구가 권위, 없으면 summary.tool).
- k8s 이식: stepper·domain·batch_orchestrator·repositories/batches·data_jobs·metrics·routes_batches 충돌 0, routes_metrics·artifact_files 기준판
  동일. execution_manifests 1곳(EXECUTION_REASONS 줄 — k8s 집합에 `| TOOL_FAILURE_REASONS`). 새 `tests/test_direct_bufsize_paths.py` 는 공유
  토큰 + 직접 경로 + `run_as_root: False` 로 제출 → k8s 에서는 픽스처를 세션 로그인 + root 로 고쳐야 한다(미실행 추정).

### 4.3 포탈(d192)

- 공용 판독기 `frontend/src/lib/toolSummary.ts`(신규) — 결과·미리보기·컨펌·배치가 같은 뜻·라벨: `readSummary`, `summaryTool`(잡 행 도구 우선),
  `failureCounts`, `previewWarnings`, `opLabel`, `errnoText`, `PRE_TOOL_REASONS`/`isPreToolReason`.
- 실행 결과(StageResults): 도구별 라벨 타일(계획과 다르면 계획 수), 「실패 항목」(연산별 칩·표본 op·errno 이유·경로·stdout.log 열기), 특수
  파일·타입 교체·스캔·정지·quiet·도구 오류 안내, 옛 3키 summary 는 중립 라벨.
- 컨펌·미리보기: nsync 종류별 변경 예정(삭제 포함)·바이트, dsync 「소스 전체 N개(변경 수는 실행 후 확정)」, 타입 교체·특수 파일·스캔 경고.
- 배너(requestOutcome): 도구 시작 전 거부 = 「데이터는 바뀌지 않았습니다」, sync 실패·취소 문구는 복사·삭제·교체, 반영 수를 알면 그 수,
  안내는 「실패 항목」과 stdout.log(abort 면 stderr).
- 배치: 확인 창 합계를 도구 뜻별로, 항목 라벨 「반영한 항목」(v2)/「항목 수」(옛), direct 의 nsync 안내.
- 대시보드: 연산별 처리량 타일, 분해 표 성공/실패/거부/취소·만료, KPI 「실패(24h)」 = Failed+TimedOut.
- 도움말: delete(타입 교체에 필요·특수 파일 — nsync 는 목적지 같은 이름 유지, dsync 는 지운 뒤 만들지 않음·스캔 오류면 꺼짐), quiet(도구가
  아무것도 찍지 않음), contents(dsync 바이트·nsync SHA-256), 소유권(chown 시 set-ID 제거 — k8s 는 PV 목적지 전부·스토리지 기본 소유자 규칙·명시
  chown 에 붙인다), direct(nsync 무시·버퍼 조건), max-errors 65,000, 경로 심링크 규칙, 「실제 경로」→「지정 경로」, atime(relatime 은 하루
  한 번 정도 갱신, CephFS 는 갱신 안 될 수 있음), 사유 문구(「실제 경로로 다시 요청하세요」 제거).
- 시뮬레이션 충돌: 깨끗 — StageResults(+test)·JobStatsSection·Dashboard(+test)·format·TargetTable·usageStats·e2e_controller·toolSummary.
  requestOutcome 4(k8s 의 LDAP `groupWarningOf` 삭제·bytes `exactBytes` 툴팁 — d192 의 `groupDeleteWarning` 은 LDAP 전용이라 버림),
  ConfirmDialog 2(k8s `resultOwner`·「결과 소유」 유지 + d192 `confirmWarnings`), BatchDetail 1(import), OutcomeCard 1, api.ts 1(**k8s 의
  `target_path_redirected` 는 rm·PV sync·PV scan 공용이라 d192 의 「삭제 대상…」 문구를 그대로 쓰면 틀린다**), reasonCodes.json 1(합집합),
  optionRules 1(d190 먼저면 해소), syncOwnership 2, requestSpec 2, **SubmitJob 7·BatchCreate 5**(k8s 는 PV 주소·전부 root·CSV — 손으로:
  「지정 경로」, `SYNC/RM_PATH_LINK_RULE`, direct·quiet 도움말, `directBufsizeError`, `SYNC_MAX_ERRORS_NOTE`, `BUFSIZE_HELP`), 테스트 7.
- **LDAP 전용이라 버릴 것**: `groupDeleteWarning`, BatchCreate 의 비 root 폴백·보조 그룹 문단, `PRE_TOOL_REASONS` 의 `identity_groups_not_applied`·
  `ldap_*`·`artifact_base_group_writable`·`identity_changed_at_step`(두어도 무해), 「실행 신원 소유가 아닌 파일」 문구.
- **k8s 에 더할 것**: `PRE_TOOL_REASONS` 에 k8s 고유 「도구 시작 전」 사유 — `chown_not_allowed`, `pv_owner_unknown`·`pv_owner_changed` 등;
  큐 관문 목록은 k8s 의 `_raise_if_blocked` 계열과 대조. k8s 에만 있는 결과 파일 만료(`artifact_expired`)에서 「stdout.log 열기」가 404 로 끝나지
  않게.

### 4.4 d193(실화면 반영, d83446f)

컨펌 경고: 삭제 꺼짐 + 비어 있지 않은 디렉토리 경고가 함께면 「교체를 시도합니다」 · 「실패 항목」에 dsync 실패 파일이 목적지에 빈 파일로
남을 수 있다는 안내 · 대시보드 분해 표 lg 둘/2xl 셋(1440 에서 열 잘림).

### 4.5 dms-ssc 에서의 검증

백엔드 3708 passed · 프런트 vitest 1501 · tsc · e2e 28 · dist 외부 URL 0. 라이브(d192·d193, 일회용 데이터 정리·요청 purge):
| 케이스 | 결과 |
|---|---|
| N1 nsync 비 root, 읽을 수 없는 소스 파일 3 | `sync_items_failed` · 반영 복사 5·속성 1(계획 9) · 실패 항목 복사 3(errno 13) |
| N2 nsync root, 목적지 비어 있지 않은 디렉터리 ← 소스 파일, 삭제 꺼짐 | 미리보기 경고 → 실행 `sync_type_change_refused`(replace-dir errno 39) |
| N3 nsync root, delete + 링크 + FIFO | Succeeded · 반영 복사 3·디렉토리 1·링크 1·속성 1·삭제 3 · 특수 파일 1 |
| N4 nsync 비 root, 읽을 수 없는 하위 디렉터리 | 미리보기 `preview_scan_incomplete` |
| Q1 quiet | `execution_failed` + quiet·수 모름(의도) |
| R1 rm 비 root, root 소유 하위 | `rm_items_failed` · 지움 4 · 못 지움 5 |
| D1 dsync root 타입 교체 거부 | `sync_type_change_refused` |
| D2 dsync 비 root 읽을 수 없는 파일 2 | `sync_items_failed` · 복사 3·실패 2(0 B 파일 잔여 → d193 안내) |
| O1 direct + bufsize 8 MiB·1000000 / 1 MiB | 422 / 수락 |
통계(24h): sync 반영 19(=6+9+1+3)·57 B, rm 4 — 손으로 더한 값과 일치.

**k8s(전부 root)에서의 실증 제안**: 권한 실패(N1·N4·R1·D2 모양)는 root 로 재현되지 않는다 — 포크 하네스(T12·T39·T41)와 러너 실 출력 테스트로
갈음하고, 라이브는 root 로 되는 것만: N2·D1(타입 교체 거부), N3(특수 파일·삭제·반영 수), **목적지 가득 참**(CephFS 쿼터
`setfattr -n ceph.quota.max_bytes -v 1048576 <목적지 디렉터리>` → nsync 즉시 정지 `destination_full`), O1(422), 대시보드 합계 대조.
PV 주소 잡도 러너가 보는 경로는 managed_root + 상대경로라 summary·사유 동작은 같다.

---

## 5. dms-k8s 특이사항 체크리스트

1. 순서: 핀(7a3189c) → d190 상수 → d191(통일 설계) → d192 → d193.
2. 모든 잡 root: O_NOATIME EPERM 폴백·비 root 소유권 수정은 영향이 거의 없지만 **nsync `--open-noatime` 실효(db1e33c)는 root 에도 의미가 있다**.
3. `target_path_redirected` 하나가 rm·PV sync·PV scan 공용 — 문구와 「도구 시작 전 거부」 판정은 넓은 뜻 유지.
4. `Dockerfile.mpifileutils` 는 공용 파일(바이트 동일 목표) — ssc 판 + k8s 주석.
5. scan 은 특권 운영자 전용, d191 은 scan 을 봉쇄하지 않는다 — k8s 의 PV scan 봉쇄는 유지.
6. metrics 응답 키 변경은 백엔드·프런트 한 번에.
7. 문서: ARCHITECTURE 의 경로 봉쇄 불변식·「도구 결과」·summary 불릿, k8s §7 「PV 주소 봉쇄」의 「삭제는 링크만 지운다」 문장.

## 6. 참조

- dms-ssc 커밋: c4a2562(핀) · 4a27b6e(d192) · d83446f(d193) · 4a13415(d191) · d7ec11e(d190), 문서 978af29(CHANGELOG d192·BACKLOG).
- 코드: `src/dms_job_runner/parsers.py`(키 표 docstring) · `runner.py`(`_build_summary`·`_derive_reason`·`TOOL_FAILURE_REASONS`) ·
  `src/dms/execution_manifests.py`(`TOOL_FAILURE_REASONS`·`_PATH_CONTAINED_FN`) · `frontend/src/lib/toolSummary.ts` · `docs/ARCHITECTURE.md`.
- 포크: `nsync.md` §13(타입 교체·실패 처리·경로 안전·§13.7 결과 수와 종료 코드), `test/nsync/README.md`.
- 보고서(포크 결함·수정 경위): https://claude.ai/artifact/T5G9BDAcE943V3b8bCuf2d

---

## 7. 전달 방법 (사용자용)

이 문서는 `origin/dms-ssc` 의 `docs/history/handoff-2026-10-28-dms-k8s.md` 로 push 돼 있다(같은 저장소라 dms-k8s 작업 트리에서 바로 읽힌다).
「DMS-k8s 개발」 세션에 다음 한 줄을 보내면 된다:

> `git fetch origin && git show origin/dms-ssc:docs/history/handoff-2026-10-28-dms-k8s.md` 로 DMS-SSC 세션의 인계 문서를 읽고, 0절 순서대로
> dms-k8s 에 맞게 이식해줘(우선 mpifileutils 핀 7a3189c). 설계 결정이 필요한 곳(경로 봉쇄 통일, rm 끝 링크 거부)은 나에게 먼저 물어봐.

같은 내용을 웹 페이지로도 올렸다(대화 끝의 링크). 그 세션은 Artifact 읽기로도 열 수 있다.
