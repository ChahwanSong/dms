# DMS 변경 이력 — 2026-09-07 ~ 2026-10-21 (ssc 테스트베드 시절 · 이관본 · 동결)
> 원본 = docs/CHANGELOG.md @5bec2e69 L2072–3897 · L366–406 · L3898–4083, 2026-11-06 이관. L366–406 은 2026-09-09 d126 항목 2개다 — 원본에선 2026-11-03 ⑦·⑥ 항목 사이에 끼어 있어 날짜 자리(d127 과 d125 사이)로 옮겼다. **동결 — 편집 금지**: 본문의 경로·절 번호·태그는 당시 기준이다(여기 `dN` = ssc 계열 태그, 옛 deploy/README 절 번호는 지금 deploy/README 「부록 Z. 옛 절 번호 대응」). 이후 기록은 [`../CHANGELOG.md`](../CHANGELOG.md), 그 앞은 [`changelog-2026-08-slices.md`](changelog-2026-08-slices.md).

### ✅ 배치 rm 자동 확인(옵션, 기본 꺼짐) + drm 심링크 실증 — **완료·실증**(2026-10-21, d184)

사용자 요청: 「Rm 배치작업에서 sync와 마찬가지로 사용자 확인 버튼 없이 바로 rm 작업 수행하도록 (자동 확인) 하는 옵션 추가해줘.」 —
같은 날 d183 의 결정 D1(「rm 배치는 자동 확인 없음 — 늘 사람이 「배치 확인」」)을 사용자가 뒤집었다. 단건 sync·rm 은 그대로
사람이 컨펌한다(이 경로 밖).

- **술어 하나**: `domain.auto_confirmable`(명시 집합 sync·rm) — 켜는 라우트(생성·실행 설정 PATCH)·orchestrator PreviewReady
  분기·`auto_confirm_hold` 세 곳이 같이 쓴다(예전엔 셋이 각자 `== SYNC`). scan(확인 단계 없음)은 여전히 422
  `auto_confirm_sync_only` — 이름은 옛 탭·스크립트 계약이라 그대로 두고 문구만 「sync·rm 배치에만」으로 고쳤다(양쪽). DB 변조
  미지 연산은 자동 확인하지 않는다(`confirm_gated` 와 방향이 반대인 fail-closed).
- **켤 때 「되돌릴 수 없음」 동의**: rm 은 생성 `auto_confirm: true` 와 실행 설정의 꺼짐 → 켜짐에 본문 `acknowledge_irreversible:
  true`(StrictBool)가 있어야 한다 — 없으면 새 사유 422 `batch_rm_auto_confirm_ack_required`(reasonCodes.json·api.ts 양쪽). 사람의 rm
  `:confirm` 이 매번 받던 동의(`batch_rm_confirm_ack_required`)를 켜는 순간 한 번 받는 것이다. 이미 켜진 배치에 true 를 다시 실은
  저장은 아무것도 켜지 않아 묻지 않고, 끄기·sync 는 보지 않는다.
- **감사**: 생성에서 켠 자동 확인도 감사 행 `batch/auto_confirm_on`(after = 켠 사람·연산, rm 은 `acknowledged_irreversible: true`)
  — 예전엔 끄기(`auto_confirm_off`)·종단 배치에서 켜기(`execution_settings`)만 남고 생성에서 켠 것은 배치 행 말고 기록이 없었다
  (sync 도 이제 남는다). 실행 설정 경로는 `execution_settings` after 에 같은 키. 새 DB 열은 없다 — 켠 사람 칸을 쓸 수 있는 손은
  동의 칸도 쓰므로 동의 열은 방어가 아니라 기록일 뿐이고, 방어는 확인 직전 자격 재확인이다. 그래서 자동 확인의 `batch/confirm`
  행(actor `system:auto-confirm`, `files_total`·`files_unknown` 은 사람 확인과 같은 `preview_files`)에는 동의를 쓰지 않는다 —
  루프는 동의를 본 적이 없다(`auto_confirm_by` 가 켤 때의 행을 가리킨다).
- **실행 경로는 그대로**: 같은 `BatchesRepository.confirm`(회차·Queued CAS + 도장 + 감사 + `expected_auto_confirm_by`), 도장 찍힌
  미리보기만 실행, 만료된 미리보기는 실행하지 않음, 항목 추가·재실행은 Previewing 으로 돌아가 켜져 있고 보류가 없을 때만 다시 자동
  확인, `target_path_redirected` 봉쇄(preflight·exec_preflight·러너)도 그대로. `auto_confirm_hold` 의 옵션 재검증을
  `validate_options` → `validate_batch_options`(사람 확인의 `_reject_stale_options` 와 같은 함수 — rm 은 recursive 동의까지; sync 는
  같은 결과).
- **포탈**: 켜는 체크·「자동 실행 끄기」·자동 안내문이 같은 명시 집합(`useBatches.autoConfirmable`). 생성 화면 rm 에 「미리보기 후
  자동 실행」(기본 꺼짐) — 켜면 빨간 「자동 삭제 경고」(「미리보기가 끝나면 사람 확인 없이 root 권한으로 삭제됩니다 — 되돌릴 수
  없습니다」) + **필수** 동의 체크(체크 전 생성 잠금·힌트, 체크했을 때만 `acknowledge_irreversible` 을 싣는다; 자동 실행 체크나
  연산을 바꾸면 풀리고, 다른 연산에서 rm 으로 들어오면 자동 실행도 꺼진 채 시작), 연산 안내·「삭제 범위」·요약 「실행 확인 = 자동 —
  미리보기가 끝나면 확인 없이 삭제」(빨강)·제출 위 경고가 켜진 동안 「확인 없이 삭제」로 바뀐다. 실행 설정 변경 다이얼로그 rm 도
  같은 체크 + 꺼짐 → 켜짐일 때만 경고·필수 동의. 배치 상세: 헤더 빨간 「자동 실행 — 미리보기가 끝나면 확인 없이 삭제 (켠 사람)」,
  「자동 실행 끄기」, Previewing·PreviewReady·보류·끝난 배치·항목 추가 안내의 rm 문구, 실행 설정 행. 목록의 「자동」은 rm 이면 빨강.
  sync 화면은 그대로다.
- 문서: CLAUDE.md(「rm 배치에 자동 확인을 넣지 마라」 → 2026-10-21 사용자 결정), ARCHITECTURE 「배치 재검토·확인 게이트」.
- **e2e E13**(`12-batch-rm-auto-confirm.spec.ts`): 생성 화면에서 rm + 자동 실행 → 경고·동의 체크 전엔 생성 잠김 → 체크 → 「배치
  확인」을 누르지 않고 실 오케스트레이터가 미리보기 → 확인 대기 → 자동 확인 → 가짜 drm 실행으로 **하네스 스토리지의 그 경로가 실제로
  지워지고**(walk 5·2) 목록 밖 형제는 남는다, 375 헤더 넘침 0. 동의 없는 API 켜기는 422 `batch_rm_auto_confirm_ack_required`, 끈 rm
  배치는 확인 대기에서 멈추고(3초 더, 파일 그대로) 취소로 정리. E12 는 「자동 실행 칸 없음」 → 「기본 꺼짐」, 03-layout 에 rm 자동
  실행 켠 생성 화면 1280·375.
- 테스트: 백엔드 전체 3456(skip 7) — 그중 `-k "batch or auto_confirm or reason"` 572 · 마이그레이션·계약 117, vitest 1392 · tsc, e2e 26 통과.
  `auto_confirmable` 에서 rm 을 빼면 rm 자동 확인 테스트 9건이 빨간불(변이 확인). 실 Playwright 캡처(1280·375): 생성 화면 경고·
  동의, 실행 설정 다이얼로그, 진행 중·끝난 상세 헤더 — 가로 넘침 0.

- **리뷰 반영**(같은 날): 자동 확인의 같은 UPDATE 동의 절이 계정 화면 부여(root_granted)를 다시 보지 않아, 직전 판정 뒤
  회수된 부여가 그 틱의 확인을 막지 못했다(sync 도 같았던 틈) — env 목록이거나 지금 부여가 서 있어야 한다. 「CSV로 전체 교체」
  rm 안내가 자동 실행 배치에서도 「배치 확인을 거쳐」라고 약속하던 것, 동의 체크박스 aria-label 이 보이는 문장을 덮어쓰던 것.
- **롤링 배포 주의**: d184 이전에 열어 둔 포탈 탭은 rm 배치를 늘 「자동 실행 없음」으로 그리고 「자동 실행 끄기」도 없다(옛
  화면은 sync 만 안다) — 배포 뒤 열린 포탈 탭을 새로고침한 다음 rm 자동 실행을 쓴다. 옛 탭에서 켜는 것은 불가능하다(서버가
  동의를 요구한다).
- **drm 심링크 실증**(사용자 요청 「drm이 symlink를 따라가서 원본 파일까지 삭제하는지 실증 체크해봐」, d183 잡 이미지의 실 drm,
  root 단건 rm, /cephfs-third 일회용 트리): 대상 디렉터리 안에 바깥 원본을 가리키는 상대·절대·하위 디렉터리 안·끊어진 링크
  → 미리보기 9·삭제 9(대상과 그 안 항목, 링크는 링크만), 원본 파일·디렉터리 전부 남음. 대상 자체가 디렉터리 링크·파일 링크
  → 1·1, 링크만 지워지고 원본 남음. 즉 **drm 은 걷는 동안 링크를 따라가지 않는다** — 따라가는 것은 경로 해석의 **중간** 성분
  (커널)뿐이고, 그 경우는 d183 의 봉쇄가 `target_path_redirected` 로 막는다(ARCHITECTURE rm 봉쇄 불변식에 기록).
- **실증(d184, 테스트베드)**: 동의 없이 rm 자동 실행 켜기 → 422 `batch_rm_auto_confirm_ack_required`. 자동 실행 끈 rm 배치 →
  PreviewReady 에서 60초 더 멈춤(디스크 17항목 그대로) → 취소. 동의하고 켠 rm 배치 → **「배치 확인」 없이** Previewing →
  PreviewReady → Running → Completed, a·b 만 지워지고 keep·keep2·x 남음. 감사: `batch/auto_confirm_on`(mason,
  acknowledged_irreversible) → `batch/confirm`(system:auto-confirm, files_total 9). 실 Chrome: 생성 화면 경고·필수 동의(체크 전
  생성 잠김), 상세 「자동 실행 — 미리보기가 끝나면 확인 없이 삭제 (켠 사람 mason)」, 1280·375 넘침 0, 콘솔 오류 0. 백엔드 3460,
  vitest 1393, e2e 26 통과. 배포: api·controller d184(잡 이미지 d183·에이전트 d179 그대로). 실증 데이터 정리.

### ✅ 배치 rm + 사용량 분석 파일 크기 분포·스토리지 합계 — **완료·실증**(2026-10-21, d183)

사용자 요청: 「1. 배치작업에 rm도 할수있도록 추가해줘.」 근거: 슬라이스 26 §7 「대량 삭제는 항목별 미리보기·확인 흐름 설계가
선행」 — 2026-10-07 배치 확인 게이트(회차·도장·항목별 미리보기 요약)로 충족됐다. 조사·결정: u1021-1(백엔드)·u1021-2(포탈)·
design-1021 ①.

- **서버**: `domain.validate_batch` 가 rm 을 받는다(`BATCH_OPERATIONS`). 확인 게이트 연산을 술어 하나로 —
  `domain.confirm_gated`(scan 밖 전부, 미지 연산도 게이트 쪽): 생성 상태·Queued 재생성 뒤 복귀·`:rescan`·**orchestrator
  Running 분기**. 이전 Running 분기는 `== SYNC` 라, rm 을 허용만 하면 확인 도장이 찍힌 자식이 scan 가지로 떨어져 **영영
  ConfirmPending**(만료까지 정체)이었고 Running 중 Queued 는 사람 확인 없이 미리보기됐다(조사 탐침으로 재현).
- **자동 확인 없음**(D1): 되돌릴 수 없는 root 삭제라 늘 사람이 「배치 확인」 — 생성·실행 설정 422 `auto_confirm_sync_only`
  (문구를 rm 문맥으로), PreviewReady 분기의 `== SYNC`, `auto_confirm_hold` 세 겹이 DB 변조 행까지 막는다(테스트로 고정).
  → 같은 날 사용자 결정으로 뒤집혔다(위 「배치 rm 자동 확인」 — 켤 때 「되돌릴 수 없음」 동의를 붙여 옵션으로).
  실행 신원은 배치 정책 그대로(자격 있으면 root, D2).
- **한 배치 안 겹침 422 `batch_rm_targets_overlap`**(D3, 새 사유 코드 — reasonCodes.json·api.ts 양쪽): 같은 경로 두 번·조상과
  자손(정규화 경로의 **성분** 단위 — a 와 ab·「a b」는 무관). `validate_batch` 한 곳이라 생성·항목 추가·수정·교체 모두, 판정
  집합은 종단 항목 포함 전체. 자식 충돌 판정은 resource_key 가 정확히 같을 때만이라 a 와 a/b 가 둘 다 돌아 확인 창 합계가
  부풀고 a/b 는 실패 항목이 됐다. 다른 요청과의 겹침은 BACKLOG.
- `domain.validate_batch_options`(validate_options + rm recursive 동의) — 실행 설정 PATCH·`_reject_stale_options`(확인·재실행
  전)도 recursive 를 뺀 rm 옵션을 422 `rm_recursive_required` 로 바로 거부(예전엔 저장되고 재실행에서 항목마다 Rejected).
- 확인 감사 행 `batch/confirm` after 에 `files_total`(ConfirmPending 미리보기 files 합 — 아는 값만)·`files_unknown`(null≠0,
  bool·음수는 모름) — 「누가 몇 개 삭제를 확인했나」. sync 에도 같이 남는다.
- **포탈**: 연산을 두 축으로 가른다 — 항목 모양(sync = 쌍, 그 밖 = 대상 하나)과 확인 흐름(`needsBatchConfirm` = sync·rm 명시
  집합). 생성 화면 rm: 1열 「삭제할 경로」, 재귀 삭제(필수) 동의 체크·stat/lite 상호 배타·quiet(SubmitJob 과 같은 문구), 「삭제
  범위」 빨간 안내, 행 즉답(관리 디렉토리 자신·「..」·「/」, 그리고 겹침 — `lib/rmTargets.ts` 가 서버와 같은 규칙으로 「2행: 1행의
  경로(x) 아래입니다」처럼 행끼리 말한다), rm 정책 실값 캡션, 자동 실행 칸 없음, 요약 「실행 확인 = 직접 …(rm 은 자동 실행
  없음)」·제출 버튼 위 경고. 「배치 확인」 대화상자 rm 갈래: 삭제 문장·삭제 경고, 「삭제 대상 N개(대상 경로 포함)」·「대상 경로
  없음」(미리보기 0), **항목별 삭제 대상 전부**(수 + 실제 경로, 목록만 스크롤, 0 = 「대상 경로를 찾지 못함」 빨강), 「되돌릴 수
  없음」 체크 뒤에만 「삭제 실행 확인」(열 때마다 풀림), 소유권 줄은 sync 만, 넓은 창(lg — md 폭에선 1280×900 에서 버튼이 창
  아래로 밀렸다). 헤더 안내·실행 설정 행·항목 추가(재확인 안내 + 겹침 즉답)·CSV 교체(교체만으론 안 지워진다 + 겹침 즉답)·
  펼침(「삭제한 항목」+「삭제 대상(미리보기)」)·실행 설정 변경 다이얼로그 rm 키 집합(빠지면 recursive 를 「옛 옵션」으로 빼고
  scan 옵션을 보냈다 — 계약 테스트를 rm 까지 넓혔다).
- **e2e E12**(`11-batch-rm.spec.ts`): 생성 화면 → 겹침 즉답 → 실 오케스트레이터가 확인 대기에서 멈추고(3초 더 지나도, 파일
  그대로) → 대화상자가 실 조인으로 항목별 삭제 대상(5·2·0)을 그리고 → 체크 뒤 「삭제 실행 확인」 → **하네스 스토리지의 그
  경로가 실제로 지워지고** 목록 밖 형제는 남는다. 이를 위해 e2e 컨트롤러 진입점을 `fixtures/e2e_controller.py` 로 바꿨다 —
  같은 `cli.main` 배선에 drm 잡만 가짜 drm(fixtures/bin/drm, 하네스 tmp 스토리지 아래만, 진입점·스크립트 두 겹 봉쇄)으로 실제로
  돌리고 출력은 러너 파서로 읽는 어댑터(argv 는 실 빌더). 다른 연산은 종전 stub 그대로. 03-layout 에 rm 생성 화면 1280·375.
- 테스트: 백엔드 배치 8파일 244 · `-k "batch or reason or option"` 598, vitest 1351, e2e 24 통과(Running 분기를 `== SYNC` 로
  되돌리면 rm 테스트 3건이 빨간불 — 변이 확인).
- **리뷰 반영**(같은 날):
  - **rm 대상 봉쇄**(중간): root 배치 rm 은 `test -w "$(dirname 대상)"` 가 늘 참이라, 사용자가 상위 디렉터리를 심링크
    (`users/alice/data -> ../bob`)로 바꿔 두면 확인 창의 「실제 경로」와 다른 트리를 drm 이 지웠다(스토리지 mount_path 전체가
    마운트된다 — 스크래치 재현). rm preflight·exec_preflight 에 「realpath(상위) == realpath(managed_root) + 글자 그대로의 상대
    상위 경로」(managed_root 는 positional `$2`, 마지막 성분 링크는 허용) — 아니면 새 사유 `target_path_redirected`(양쪽 등록).
    stepper 가 target 을 조합한 같은 managed_root 를 `spec.paths` 에 싣는다(없으면 빌더 거부). 러너도 drm 직전에 같은 규칙으로
    다시 본다(env `DMS_JR_RM_ROOT`, `DMS_EXEC_REASON=target_path_redirected`) — **잡 이미지(dms-mpifileutils)가 바뀐다**. 남은 창은
    BACKLOG §4. 실 dash 로 셸·러너 두 판정을 같은 트리에 대조(`tests/test_rm_containment_sh.py`).
  - **rm 확인의 「되돌릴 수 없음」을 서버도 본다**(중간): 롤링 배포 중 옛 포탈 탭은 rm 배치에도 sync 문구(「복사 대상」)의
    확인 창을 그려 {preview_round} 한 번으로 root drm 을 시작했다. `ConfirmBatchBody.acknowledge_irreversible`(StrictBool) —
    rm 은 명시 true 가 없으면 422 `batch_rm_confirm_ack_required`(양쪽 등록), 포탈은 체크했을 때만 싣는다, sync 는 그대로.
  - **겹침·동질성 판정을 쓰기 트랜잭션 안으로**(낮음): 추가·수정·교체가 트랜잭션 밖 스냅샷으로 판정해 두 탭이 a 와 a/b 를
    같이 넣을 수 있었다 — 저장소가 항목 행 → 배치 행 잠금 뒤 읽은 목록으로 콜백 판정(PG·sqlite), 끼어들기를 결정적으로 재현한
    테스트.
  - **자동 확인 감사 행에도 `files_total`·`files_unknown`**(낮음): 사람·자동 확인이 같은 `batch_orchestrator.preview_files`.

사용자 요청 2·3: 「사용량 분석 화면에 최근 스캔 기준 파일의 사이즈별 정보도 보여지게끔 추가해줘.」 「사용량 분석에 스토리지별
사용량 및 정보들의 총합을 볼수있도록 구현해줘.」 조사·결정: u1021-3(데이터·합계)·u1021-4(UI)·design-1021 ②③.

- **파일 크기 분포(최신 scan)**: 데이터는 이미 목록 latest·이력 포인트에 실려 있었다(dscan `file_size_histogram`, 10구간 —
  **개수만**, 구간별 용량은 리포트에 없다). 공용 가로 막대 목록 `components/ui/SizeDistribution`(줄 = 구간: 「4 KiB 이하」…「4 TiB
  초과」 · 막대(최다 구간 대비) · N개 · 비중, 단색 — 크기는 온도 축이 아니다; 라벨 규칙 `lib/sizeBuckets.ts`, 줄 title 에 정확한
  바이트 경계, 모름·빈 트리·일부 구간 모름을 따로 말한다). 펼친 상세 맨 위 「최신 scan」 블록(lg: 수치 2×2 | 분포).
- **스토리지 합계** `GET /api/admin/usage/storage-totals?storage=`: 대상 경로가 겹쳐(team ⊃ team/sub1) 단순 합은 이중 계산이라
  **최상위 대상만**(경로 성분 단위 조상 판정 — team 은 team-x 의 조상이 아니다), 각 대상의 최신 성공 scan 을 목록 열과 같은 규칙으로
  더한다. 지표마다 {sum, known, unknown}(known 0 → null, null≠0), hot 은 bytes 가중(hot_ratio 와 한 벌), 크기 분포는 같은 구간
  서명끼리, scan 시점 범위, 1만 창(넘으면 truncated). 리포트 읽기는 호출당 500(나머지 pending — 포탈이 1초 재조회), 읽기 실패는
  10분 기억해 예산을 다시 먹지 않는다(영구 캐시엔 넣지 않는다). **파일시스템(df)**: 에이전트가 이미 보내는 statvfs 를 그 스토리지가
  **Ready 마운트인 노드만** 골라 싣는다(마운트 안 된 노드는 루트 디스크 값을 보고한다 — 실측), 스토리지끼리 더하지 않는다(테스트베드
  cephfs 셋은 같은 파일시스템). 화면: 패널 맨 위 「이 스토리지 합계」(실 사용량·파일·디렉터리·hot·df | 크기 분포 + 각주 「최상위
  N개 기준 · 하위 M개 제외 · scan 시점 …」), 머리말 아래 접힌 「스토리지별 비교」(열 때만 요청, 행 클릭 = 그 탭, 부분 합은 「이상」).
- 리뷰 반영: 못 읽는 리포트가 읽기 예산을 독점하던 것, 재조회 실패 뒤 「읽는 중」, 태블릿 폭 칸 겹침, 비교 표 부분 합 표식.
- **실증(d183, 실 Chrome·테스트베드)**:
  - 사용량: cephfs-dms 합계 17,786,075 B(17.0 MiB)·파일 662·디렉터리 243·최상위 4/9(하위 5 제외)·크기 분포 590/66/2/4·hot 100%·
    scan 시점 65일 전~18일 전 — 조사 때 손으로 계산한 기대값과 같다(단순 합은 34,563,326 B). df 32.0 MiB / 22.8 GiB(Ready 노드),
    펼친 상세 artifacts 분포 552/66/2(=620), 비교 표는 열 때만 나머지 2개 요청, 1280·375 넘침 0, 콘솔 오류 0.
  - 배치 rm(/cephfs-third 일회용 트리): 겹침(a, a/sub) 422 `batch_rm_targets_overlap` → 배치(a·b·없는 경로) 미리보기 6·3·0 =
    실제 트리와 같음, 확인 대기 중 디스크 그대로(17항목), 동의 없는 확인 422 `batch_rm_confirm_ack_required` → 실 Chrome 대화상자:
    항목별 목록·「대상 경로를 찾지 못함」, 체크 전 주 버튼 잠김 → 체크 → 「삭제 실행 확인」 → Completed, **a·b 만 지워지고 keep·
    keep2·x 는 남음**. 심링크 우회(`x/link -> ../keep2`, 대상 x/link/sub, root 단건 rm) → Rejected `target_path_redirected`,
    keep2/sub/secret.txt 그대로. 회귀 scan 10/50. 백엔드 3445, vitest 1383, e2e 24 통과. 배포: api·controller·잡 이미지 d183
    (에이전트 d179 그대로). 실증 요청·트리는 정리.

### ✅ 사용량 분석 스토리지별 탭 + 창 상한 1만 + UI 정리 / 비 UTF-8 파일 이름에 잡이 Failed 되던 문제 — **완료·실증**(2026-10-20, d182)

사용자 요청 1: 「사용자 분석 내용이 지금은 처음부터 전체 등록된 내용이 보여지고 필터를 걸면 전체의 5천개가 서버에서
스캔되는거같아. 이 구현내용을 각 스토리지 별로 처음부터 나뉘어져서 보이게끔 하고 필터 및 서버의 스캔도 각 스토리지 별로
진행되도록 구현 업데이트해줘. 서버의 스캔 상한선도 5천개에서 1만개로 상향 조정해줘. 마지막에 깔끔하고 심플하고 직관적으로
보이게 UI 개선작업도 수행해.」

- **처음부터 스토리지 탭**(「전체」 없음): 등록 이름순 | 미등록 이름순(「미등록」 표시, 정리 버튼은 그 탭의 경고 띠). 목록·
  지표 정렬 창·CSV 요청에 늘 storage 가 실린다 — 서버의 창(최근 스캔 순 상한)도 그 스토리지 안에서 만든다. 기본 탭은
  기록 있는 등록 스토리지 이름순 첫째(URL 에 쓰지 않고, 고른 뒤엔 붙잡는다). URL `?storage=` = 탭, `&target=` = 펼친 행.
- **창 상한 1만**(`routes_usage._WINDOW_LIMIT`): 상수만 올리면 1만 행 CSV 동시 2~3건이 api 1Gi 를 넘었다(실측 +465MiB/건).
  목록은 정렬 키만 500개 묶음으로 계산하고 반환 행만 전체 포인트, 내보내기는 500 타깃 묶음 스트리밍(JSON 모양 그대로, count
  맨 뒤), 다이제스트 캐시 `put_many`. 1만 타깃 sqlite: 내보내기 16.5초·+469MiB → 2.8초·+53MiB, 지표 정렬 15초·+283MiB →
  1.3초·+29MiB. 응답은 옛 구현과 바이트 비교 테스트로 고정(`tests/test_usage_equivalence.py`).
- 인덱스 `idx_data_jobs_scan_target (storage_name, state, operation, target, updated_at)` — 스토리지별 조회가 DB 수준에서
  그 스토리지만 읽는다(부분 인덱스는 PG generic plan 함정으로 쓰지 않는다). 탭 요약 API `GET /api/admin/usage/storages`
  (window_limit + 스토리지별 대상 수·성공 scan 수·최근 스캔, 등록 ∪ 성공 scan 이름). 총 사용량은 싣지 않는다(타깃 경로가 겹쳐
  합이 이중 계산). storage 는 서버에서 선택 인자(배포 중 옛 탭 호환), 실렸으면 빈 값이라도 정확 일치.
- **UI 정리**: 스토리지 열·행 배지 제거(6열), 경로 열 고정(375 가로 스크롤 중에도 어느 경로인지), 넓은 화면에선 경로 칸이
  남는 폭만(긴 경로가 최근 스캔 열을 밀지 않는다), 펼친 상세는 타일 4개 → 한 줄 수치 + 공용 Segmented + 스캔 이력 접기,
  375 에서 상세 폭 = 보이는 표 폭(예전엔 표 전체를 밀어야 보였다), 각주 한 줄, CSV 는 탭 하나씩 `dms-usage-<storage>-…csv`.
- 리뷰(5개 축 + 반박 검증) 13건 반영 — 빈 이름 탭이 전 스토리지로 새던 범위, 요약 재조회의 기본 탭 흔들림·자리표시 회귀,
  경로 검색 중 「1만 개 안에서 정렬」 단정, 흐린 정렬 아이콘(opacity)이 고정 칸 위로 비침, 탭 key·화살표 포커스 노출 등.
- 실증(d182, 실 Chrome): 탭 cephfs-dms(9)·cephfs-secondary(0)·cephfs-third(1), scan-targets·export 6건 모두 storage 실림,
  실 사용량 내림 16.0 MiB → 6 B, CSV 두 파일이 각각 그 스토리지 행만(9·1), 화살표는 요청 0건·Enter 로 선택, 375 넘침 0·상세
  293 = 래퍼 293, 행 접기 후 ?storage 유지, 콘솔 오류 0. PG 에 새 인덱스 생성 확인.

사용자 요청 2: 「작업을 수행하다가 파일이름에 오류가 있으면 Failed 가 되고있어. Broken path로 분류된거 같긴한데, Failed 로
종료가 안되고 나머지 파일/디렉토리들도 수행 완료되게 해줘.」

- 원인: 러너가 도구 출력을 strict UTF-8 로 디코드했다(`subprocess.run(text=True)`). 도구(dscan·dsync·drm·nsync)는 이름을
  바이트로 다뤄 **이미 전부 처리하고 rc 0** 인데, dscan --print 의 broken 표본·drm --dryrun 목록·오류 줄에 CP949 이름 바이트가
  찍히면 러너가 UnicodeDecodeError 로 죽어 Failed(rm 은 미리보기에서 죽어 컨펌 불가). 그 경로가 broken 인 이유는 이름이 아니라
  `abnormal_time`(시각이 10년보다 오래됨)이다.
- 수정(잡 이미지만): `runner._subprocess_run` — UTF-8 `errors="replace"`(surrogateescape 금지: 로그·summary·DB·JSON 에서
  터진다), `parsers.parse_scan_counts` 도 replace(크래시만 고치면 files/bytes 가 조용히 null 이었다). 실제 자식 프로세스로
  원시 바이트를 찍는 회귀 테스트. ARCHITECTURE 불변식 「파일 이름은 바이트다」.
- 재현·실증(테스트베드 cephfs-third, EUC-KR 「다운로드」 디렉터리 + 2009년 시각 파일 + Latin-1·깨진 바이트 이름, 264 항목):
  수정 전(잡 이미지 d179) scan → `execution_failed`(launcher 의 같은 `UnicodeDecodeError … 0xb4`), rm 미리보기 →
  `preview_failed`. 수정 뒤(d182) 같은 트리: scan Succeeded files 264·bytes 265,216(find 실측과 같다), stdout.log 의 broken
  2줄은 U+FFFD 로; quiet scan 도 값 채움; rm 미리보기 6 → 컨펌 → Succeeded(실제로 지워짐); dsync 미리보기 264 → 컨펌 →
  Succeeded, 소스·목적지 매니페스트(이름 바이트·종류·권한·크기)와 내용 해시 일치, 2009 mtime 보존, `ls -b` 이름 바이트 동일;
  회귀 scan(d64-sync-a) 10/50 그대로. 백엔드 3334, vitest 1317+, e2e 23 통과. 배포: api·controller·잡 이미지 d182(에이전트 d179
  그대로). 실증 요청·트리는 정리.
- 후속(BACKLOG §4): 비 UTF-8 이름을 리포트에 드러낼지(dscan 사유·카운터 — 결정 대기), drm unlink 실패 rc 0, 제출 경계 lone
  surrogate 500.

### ✅ 사용량 분석 목록 열 정렬(실 사용량·파일 수·hot 비율·스캔 횟수·최근 스캔) — **완료·실증**(2026-10-19, d181)

사용자 요청: 「사용량 분석에서 실사용량, 파일수, hot비율 등을 오름/내림차순 정렬할수있게 기능 구현해줘.」

- **서버 정렬**(`scan-targets`·`export` 의 `sort`=last_scan·scans·bytes·files·hot + `order`): 목록은 100개로 잘려 오므로
  화면이 받은 묶음만 정렬하면 「최근 N개 안의 순위」가 되어 그 밖의 큰 디렉터리가 상위에 안 나온다. last_scan·scans 는
  SQL, 실 사용량·파일 수·hot 비율은 리포트 요약 값이라(잡 행 files_count 는 디렉터리까지 센 항목 수라 화면의 total_files 와
  다르다) 필터에 맞는 대상 창(최근 스캔 순 상한 5000)의 최신 요약(캐시)으로 화면과 같은 값을 계산해 정렬한 뒤 자른다
  (`usage_metrics`). 모름(—)은 방향과 상관없이 맨 뒤.
- hot 비율 규칙은 포탈·서버 두 벌 — 공용 예제 `hotRatioCases.json` 을 vitest·pytest 가 함께 읽는다.
- 화면: 머리글 5개가 정렬 버튼(다시 누르면 방향, 다른 열은 큰·많은·최신 것부터, aria-sort 는 정렬 중인 열만), CSV 도 같은
  정렬, 목록이 상한(100)을 채우면 「상위 100개만」 안내(예전엔 조용히 잘렸다).
- 실증(d181, 실 Chrome): 테스트베드 9개 대상 — 실 사용량 내림(16.0 MiB → 6 B)·오름, 파일 수 내림(620 → 1)·오름이 값 순서와
  일치, aria-sort 는 정렬 열만, 넘침 0, 콘솔 오류 0(테스트베드 hot 은 전부 100% — 순서는 서버 테스트로 고정). 백엔드 3309,
  vitest 1288, e2e 23 통과. 배포는 dms 이미지만(api·controller d181).

### ✅ 큐 흐름 히어로 가로 배치 1024px 부터 + TTL 이전 vcjob 정리 — **완료·실증**(2026-10-17, d180)

d179 보고의 남은 두 사항(사용자: 「작업 진행해 줘.」).

- **1024–1279px**: 세로 레일이 카드 오른쪽을 비워 두던 것을 가로 흐름으로(`lg:`). 이 폭의 칸(≈96–130px)엔 「Volcano 실행 중」이
  한 줄에 못 들어 본선 라벨 줄을 두 줄 자리(40px)로 고정하고 리본 높이를 lg 69px · xl 53px 로 나눴다(원·리본은 라벨 줄 수와
  상관없이 같은 높이). 확인 관문 라벨은 한 줄(고리 기하 그대로), 끝 마디만 홀로 넘어가지 않게 숫자 끝 마디·세 낱말 라벨의
  끝 낱말을 줄바꿈 없는 공백으로 붙였다. xl(1280+)·좁은 폭(세로 레일)은 그대로.
- **TTL 도입 이전 vcjob**: 테스트베드에 남아 있던 24개(2026-08-03~04 생성, Completed, ttlSecondsAfterFinished 없음 — 지우기
  전 목록 확인)와 그 launcher 파드를 지웠다(vcjob·launcher·PodGroup 0). BACKLOG 항목 삭제.
- 실증(d180, 실 Chrome): 대시보드 히어로 1024·1180 가로 격자, 1440 xl, 375 세로 레일 — 가로 넘침 0, 콘솔 오류 0. vitest 1284,
  e2e 23 통과. 배포는 dms 이미지만(api·controller d180, 에이전트·잡 이미지 d179 그대로), guard-images 통과.

### ✅ 워커가 프로세스 수만큼 CPU 를 항상 요청 + 큐 흐름 히어로(흐르는 파스텔 리본) — **완료·실증**(2026-10-16, d179)

사용자 지시: 「기본으로 worker가 cpu를 실제로 요청하게 해. 프로세스 개수만큼 코어 사용하도록. 옵션이 아니라 항상 그렇도록
해줘.」 · 「큐 대기가 일종의 파이프라인 이잖아? … 시각적으로 흐름 형식으로 애니메이션화해서 예쁘고 직관적으로 보여줬으면
좋겠어. 최신 UI 트랜드를 참고해서 구현해줘.」 · 「3번의 내용이 대시보드의 제일 위에 대표적으로 보여지게 해줘. 애니메이션 색깔
파스텔 흐르는 느낌으로 각 단계별로!」

- **워커 CPU 요청(항상)**: 모든 워커 컨테이너(primary·nsync 양면, 미리보기·실행)가 `requests.cpu = 노드당 프로세스 수`
  (`execution_manifests.processes_per_node` — hostfile slots 와 같은 단일 원천, limits 없음 — CFS 스로틀이 MPI 를 늦춘다),
  launcher 100m, preflight·exec_preflight 맨 Pod 는 BestEffort 그대로. slice21 의 「BestEffort 유지」 결정을 번복한다.
  이제 Volcano 큐가 실제로 줄을 선다(예전엔 전부 BestEffort 라 8개 잡이 128코어를 한꺼번에 나눠 썼다).
- **노드 CPU 상한(영구 Pending 방지)**: 에이전트가 Node allocatable 을 보고(실패하면 마지막 값을 stale 로 이어 싣는다),
  상한 = floor((allocatable − 100m − `DMS_NODE_CPU_RESERVE_MILLICORES`(500)) / 1000). planner 는 상한 < 1 노드를 빼고(0대면
  `node_cpu_insufficient`), 고른 노드의 최소 상한으로 노드당 프로세스를 줄인다(「요청 2 → 노드 CPU 상한 1」 표시·이벤트).
  노드는 (상한 충족, 남은 여유 충족, DMS 예약 코어, 이름) 순으로 골라 잡이 한 노드에 몰려 줄 서지 않는다. stepper 는 제출
  직전 다시 줄이고, Volcano 대기 중 상한이 더 작아지면 `node_cpu_insufficient_at_step` 으로 끝낸다. 정책 노드당 프로세스 ≤ 1024.
- **큐 흐름 히어로**: 대시보드 맨 위. 배치 대기 → 계획 대기 → 잡 픽업 → 사전 점검 → Volcano 대기 → 실행 중 → 완료가 단계별
  파스텔(버터·피치·코랄·라벤더·페리윙클·아쿠아·민트)을 잇는 리본 위 역으로 서고, 리본이 천천히 흐르며 최근 5분 실제 단계
  이동량(`flow` 축 — state_transitions PK 역순 5000행, 새 인덱스 없음)만큼 빛 입자가 구간을 흐른다. 확인 대기(오키드)는 실행
  중에서 사전 점검으로 돌아가는 고리. 붐비는 역은 숨쉬는 후광, 오래 기다리면 링 색(주의·경고), 모르면 점선·「—」. 375 는 세로
  레일, 움직임 줄이기면 전부 정지. 라이브러리 없이 CSS·SVG(에어갭).
- 리뷰: 리뷰 워크플로 3회(확인 15 + 재검토 3, 전부 반영 — 1024–1279px 은 세로 배치로 남김). 덤으로 로그아웃 직후 셸의 me 401
  재조회 경합을 고쳤다(router.test 가 렌더 타이밍에 따라 빨갛던 원인).
- 배포: dms-mpifileutils·dms·dms-agent d179, 에이전트를 **먼저** 릴리스해 allocatable 보고가 깔린 뒤 api·controller(상한을
  모르는 창 없음), 잡 이미지 d179, guard-images 통과.
- 실증(d179, 실 Chrome, 일회용 데이터 `/cephfs/managed/cpuproof-1016`·배치 2개·자식 12개 삭제): 테스트베드(노드 allocatable
  1800m, 정책 노드당 2) 6항목 scan 배치(동시 6) → 자식 worker_pool `procs_per_node 1 (요청 2, 노드 상한 1, cpu_cap_known)`,
  vcjob 6개 모두 worker `requests.cpu: "1"` ×4 · launcher `100m` → 대시보드 히어로 「Volcano 대기 5 · 실행 중 1」, 대기 표에
  Volcano 사유 「Inqueue · gang 5 · NotEnoughResources … Insufficient cpu」 → 차례로 실행돼 6개 전부 성공·실패 0.
  1280·375 가로 넘침 0, 콘솔 오류 0, 움직임 줄이기에서 애니메이션 26 → 0. 첫 시도는 컨트롤러 롤아웃 겹침(옛 파드가 종료
  유예 30초 동안 루프를 계속 돌아 5개를 옛 코드로 계획·제출 — BACKLOG 「롤아웃 겹침 창」)으로 무효, 다시 돌려 확인.

### ✅ 배치 취소 뒤 남는 launcher/worker 파드 · 실제 CPU 사용률 · 큐 현황 상세화 — **완료·실증**(2026-10-16, d178)

사용자 보고·요청: 「배치작업 Cancel 했는데 몇분 후에 k8s 을 직접 확인해보니 아직 돌아가고 있는 launcher/worker pod 들이
보여. 이거 개선해 줘.」 · 「대시보드의 cpu 사용량은 어떻게 집계하는거야? 코어수가 128이어도 200이 넘어가고 막 그러던데.」 ·
「대시보드에서 큐 현황에 대해 조금만 더 자세히 그리고 개선해서 보여주면 좋겠어.」

- **배치 취소 원인(재현)**: 취소가 자식마다 k8s 를 지운 **뒤에야** 항목·배치를 썼다 — 그 사이 stepper 가 지워진 vcjob 을
  실패로 보고 슬롯이 비면 orchestrator 가 Queued 항목에서 **새 자식**을 만들었고, 그 자식은 Pending 으로 남아 취소 뒤에
  끝까지 돌았다. 그 밖에 종료 부분 실패 시 500·launcher PID 1 이 SIGTERM 을 무시해 30초 Terminating·컨트롤러 그물 없음.
- **해결**: 한 트랜잭션으로 Queued 장벽 + 배치 **Cancelling**(비종단 — orchestrator 가 굴리지 않고 종단 전용·재큐 경로가
  받지 않는다, 409 `batch_cancelling`) → 자식마다 종료(실패한 자식은 기록하지 않음 — 거짓 취소 금지) → 다 끝나면 Cancelled.
  stepper 는 그 배치 자식의 다음 단계를 내지 않고, 취소가 지우러 온 증거(`batch_cancel_terminate` 이벤트)가 있을 때만 실패를
  Cancelled 로 기록(실패 수·「실패분 재실행」 밖). planner 는 Pending 자식을 계획하지 않고, 컨트롤러 그물(`sweep_cancelled`,
  유예 60초·무작위 순서·자식마다 격리)이 남은 자식을 마저 끝낸다. 잡 파드 종료 유예 5초. 포탈 상태 「취소 중」.
- **pod-gc**: 치운 잡에 `data_jobs.pods_gc_at`(+ 부분 인덱스) — 같은 앞쪽 200건만 되풀이하던 고착 해소. 성공이 아닌 종단
  잡의 vcjob 도 max(창, vcjob TTL) 뒤에 지운다(Aborted vcjob 은 Volcano TTL 이 안 치운다, TTL 0 이면 Pod 만). 실패한 잡은
  1시간 뒤 재시도.
- **CPU**: 대시보드 값은 CPU 사용률이 아니라 `/proc/loadavg` 의 load1(실행 중 + 실행 대기 + I/O 대기(D) 스레드 수의 평균)이라
  코어 수를 넘을 수 있었다(CephFS·GPFS I/O 대기). 에이전트가 `/proc/stat` 차분으로 실제 사용률(0–100%, I/O 대기 별도)을 보내고
  화면은 「CPU 사용률」과 「부하(load 1분)」를 나눠 보인다(노드 화면 「42.3% · 부하 213.45 / 128코어」).
- **큐 현황**: Volcano PodGroup 만 보이던 카드를 DMS 대기 단계 띠(계획·픽업·사전 점검·Volcano 대기·실행 중·확인 대기·배치
  대기) + 대기 항목 표(단계 필터, 단계마다 20건)로. 확인 대기는 사람이 누를 수 있는 것만(확인된 배치의 차례 대기는 배치
  대기 held), 실행 전 재점검은 사전 점검으로, 드레인·유지보수 배지는 사유를 툴팁으로.
- 리뷰: 리뷰 워크플로 2회(확인 19건 + 재검토 14건, 전부 반영 — 빌드 파드 GC 창은 BACKLOG). 이 과정에서 7ccf4c6 이 BACKLOG
  미해결 항목 ~17건을 잘못 지운 것을 되살렸다(0c940b5).
- 배포: dms-mpifileutils·dms·dms-agent d178(agent 빌드는 같은 태그의 mpifileutils 가 필요해 셋을 함께), 잡 이미지 d178(러너
  무변경), guard-images 통과.
- 실증(d178, 실 Chrome, 일회용 데이터 `/cephfs/managed/cancelproof-1016`·배치·요청 삭제): 6항목 scan 배치(동시 2)가 워커 파드
  Running 일 때 화면 「취소」 → 응답 Cancelled, 파드 +0.2s Terminating·+4.5s 0개, vcjob 0, 81초 동안 새 자식 없음(2개 그대로),
  배치 Cancelled·성공 0·실패 0·항목 6 전부 Cancelled(cancelled_by_batch). 배포 뒤 첫 pod-gc 로 8일 된 preflight 파드·Aborted
  vcjob 0 개(남은 24 개는 TTL 이전 Completed — 일회성 정리 대상). 노드 CPU 사용률 3–6%(노드에서 직접 잰 /proc/stat 4.2% 와
  일치), 큐 현황 확인 대기 1·표 행, 1280·375 넘침 0, 콘솔 오류 0.

### ✅ 계정 화면에서 root 실행 자격 부여(재배포 없이) — **완료·실증**(2026-10-12, d177)

사용자 보고: 「관리 -> 계정 에서 일반 사용자명에게 admin 역할을 줬어. 근데 root로 실행 체크 옵션이 작업 등록할때 안뜨네.
해결해줘.」

- **원인(설계대로)**: root 실행 자격 = admin 역할 + 세션 + 특권 목록(env `DMS_PRIVILEGED_REQUESTERS`, 기동 때 한 번 읽음).
  역할만 바꾸면 목록 밖이라 `can_run_as_root=false` → 체크박스 대신 안내. 목록은 **관리자 사이의 경계**(2026-10-07)라 「admin =
  root」로 바꾸지 않았다 — 목록 밖 관리자가 아무 계정이나 admin 으로 올려 root 를 얻게 된다.
- **해결**: 이미 root 자격이 있는 세션 관리자가 계정 화면 「root 실행」 열에서 활성 admin 계정에 자격을 주고 뺀다
  (`PUT /api/admin/accounts/{u}/root`, `accounts.root_granted`, 감사 `root_grant`/`root_revoke`). 효력 집합
  `privileged.privileged_names`(env ∪ 부여된 활성 admin) — 제출·me·실행 신원 지정·배치·planner·자동 확인, 보호 집합
  `protected_names`(env ∪ 플래그 계정, 비활성 포함) — 계정 가드·셀프 재설정. 강등·승격은 남은 플래그를 지우고, 공유 토큰
  actor 이름은 부여 불가, 회수 뒤 컨펌 대기 root 잡은 컨펌에서 Rejected. SubmitJob 안내가 어디서 받는지 말한다.
- 리뷰: 자동 보안 리뷰(비활성 부여 계정 보호 누락) + 리뷰 워크플로 확인 9건 반영, 남은 둘은 BACKLOG(보호 계정 셀프 비밀번호
  변경 없음 · 회수 뒤 진행 중 배치 자식은 비 root 로 계획).
- 실증(d177, 실 Chrome, 일회용 계정 `rootproof*` 삭제): mason(env 목록)이 계정 화면에서 admin 계정 생성(봉인 전송) → 그 계정의
  별도 세션은 `can_run_as_root:false`·체크박스 없음·안내 → 「자격 주기」(확인 창: 비밀번호를 아는 사람은 모두 root) → 행 「있음 ·
  부여」·포커스 상태 글자 → 다시 로그인 없이 체크박스 표시·기본 켜짐 → 「자격 빼기」 → 체크박스 없음, 감사 create·root_grant·
  root_revoke(mason), 375 넘침 0, 콘솔 오류 0.

### ✅ 사용량 분석: 미등록 스토리지 기록 스토리지별 정리 + CSV 스토리지 열 누락 수정 — **완료·실증**(2026-10-12, d176)

사용자 요청: 「사용량 분석에 "미등록 스토리지" 에 대한 항목들은 스토리지 항목별로 전체 clean up 할수있도록 해줘. 그리고 사용량
csv로 추출하니까 두번째 행부터는 backend type, mount path등 몇몇 column 들이 누락되어서 저장되는데 이슈 확인하고 해결해줘.」

- **CSV 원인**: 포탈이 backend_type·mount_path·managed_root(→ abs_path)를 **지금 등록된** 스토리지 목록에서만 찾아, 삭제된
  (미등록) 스토리지의 행은 네 열이 비었다(최근 스캔 순이라 첫 행만 등록 스토리지). 잡·요청은 스토리지 설정을 스냅숏하지 않아
  지운 스토리지의 원천은 스토리지 감사 기록뿐이다 → 내보내기 행마다 `storage`(등록 = 지금 값, 미등록 =
  `StoragesRepository.last_known_config`, 없으면 null) + CSV 열 `storage_registered`·`storage_info_source`. 요약 키가 파손·
  히스토그램 열과 겹치면 `summary_` 접두.
- **정리**: `GET /api/admin/usage/unregistered-storages`(이름·사용량 규모·정리할 작업·섞인 배치·마지막 설정, 묶음 질의) +
  `POST …:purge`(세션 관리자, 회차 단건 ≤ 200·배치 ≤ 10, 일반 작업 삭제와 **같은 몸통**에 `scan_storage=` — 트랜잭션 안
  재판정 `storage_registered`·`storage_history_mismatch`·`storage_history_batch_mixed`, 회차 감사 `storage_history/purge`,
  `exclude_*`·`remaining_untried` 로 회차를 잇는다). 대상 = 그 스토리지의 scan 요청 전부(성공·실패·취소·잡 없는 거부) —
  rm·sync 와 스토리지 데이터는 그대로. 포탈: 사용량 분석 「미등록 스토리지」 칸(스토리지별 빨강 「기록 정리」 → 수·범위
  안내 + 확인 → 회차 반복 → 결과 줄), 목록 행 「미등록」, 사라진 스토리지 필터 해제, 경로 버튼 한 줄.
- 리뷰 워크플로(4관점 + 반박 검증) 확인 11건 반영(멈춤 조건, 실패 scan 만 남은 행, 섞인 배치 이중 계산, 필터 고착, 첫 회차
  오류, 포커스 반복, 재조회 실패, 이름마다 전체 훑기).
- 실증(d176, 실 Chrome, 일회용 스토리지 `usageproof-*`(cephfs-dms 와 같은 마운트)·대상 `dms_test/usageproof-*` 정리): 등록 →
  Ready → scan 단건 2·배치 1 성공 → 스토리지 삭제 → 「미등록 스토리지」 칸(마지막 설정 gpfs · /cephfs, 대상 3·정리할 작업 3)·
  행 「미등록」·375 넘침 0 → CSV 미등록 3행이 `last_known`(gpfs·/cephfs·/cephfs/managed·절대경로)으로 채워짐, 등록 9행
  `registered` → 끝난 지 60초 안의 정리는 3건 모두 「방금 끝난 작업」으로 건너뛰고 행이 남음(설계대로) → 다시 「기록 정리」 →
  3개 삭제(배치 포함)·목록·칸에서 사라짐·배치·요청 404·결과 줄 포커스, 정리 대기 0, 콘솔 오류 0.

### ✅ 실행을 멈추는 취소 버튼 강조 — **완료·실증**(2026-10-12, d175)

사용자 요청: "화면 버튼에 취소버튼을 조금 눈에띄게 강조해줘."

- `Button` 에 `danger` 변형(흰 바탕 · 빨강 글자·테두리 `bad`, 호버 `badbg`) + `components/ui/CancelButton`(danger + `CircleX`
  아이콘 `aria-hidden` — 접근 이름은 글자 그대로라 정확 일치 단언·e2e 무수정). 솔리드로 칠하지 않는다 — 「작업 컨펌」 같은 주
  동작보다 앞서 보이면 오클릭을 부른다.
- 적용: 잡 카드 「취소」, 결과 배너 「작업 취소」·「컨펌하지 않고 취소」·「요청 취소」, 배치 상세 「취소」. 대화상자 닫기
  「취소」·「편집 취소」·폼 이동 「취소」는 회색 ghost 그대로(멈추는 동작만 빨갛게 — BatchDetail.test 가 확인 대화상자 닫기를 회색으로 고정).
- 실증(d175, 실 Chrome, 일회용 rm `dms_test/cancelbtn-*` 정리): 컨펌 대기 화면 카드·배너 버튼 color·border `rgb(180,35,24)`·흰 바탕·
  아이콘, 「작업 컨펌」은 파랑 솔리드 그대로, 호버 `rgb(254,228,226)`, 375 가로 넘침 0, 배너 버튼 → 잡·요청 Cancelled, 콘솔 오류 0.

### ✅ 컨펌 대기·대기 중 작업 취소 버튼 + 취소 경합 · 스토리지 간 sync 경로 오판 · 작업 단계 설명 툴팁 — **완료·실증**(2026-10-12, d174)

사용자 요청(세 가지, rm 미리보기 작업 중 함께 받음): "1. Pending과 confirm pending 에 취소 버튼이 필요해. 2. 소스 스토리지가
/home/gpu1, 목적지 스토리지가 /mirr24/gpu1 이고 경로를 둘다 cocoa.song으로 적으니까 「목적지가 소스 하위 경로일 수 없습니다」
라고 오류메시지가 발생해. 이상한데 확인하고 문제있으면 해결해줘. 3. Dms 의 작업 단계에 대한 내용을 툴팁으로 설명 추가해줘."
(같은 날 앞서 취소 구현을 설명하며 찾은 경합도 이 묶음에서 막는다.)

- **취소 버튼**: 잡 카드 「취소」를 끝나지 않은 모든 상태에(예전엔 컨펌 대기에서 숨겨, 미리보기를 보고 「안 하겠다」를 화면에서 할 길이
  없었고 만료 24시간 동안 같은 대상은 Conflict). 요청 상단 배너에 단건 잡 Pending 「작업 취소」, 단건 컨펌 대기 「컨펌하지 않고
  취소」(배치 자식은 배너가 배치 안내 — 카드 「취소」는 있다). 배너 실패 문구는 배너에만.
- **취소 경합**(`api/cancel.sweep_after_cancel`, `requests.cancel_if_pending`): ① 취소가 스냅숏의 ref 만 끝내던 창 — 그 사이 stepper 가
  제출한 단계(실행 vcjob 등)가 Cancelled 잡 밑에서 끝까지 돌았다 → Cancelled 커밋 **뒤** 다시 읽어 새 ref 를 끝냄(실패는
  `terminate_failed` 이벤트). ② stepper 가 먼저 종단시켰는데 200 Cancelled(「요청 Cancelled · 잡 Succeeded」) → 실제 결과로 맞추고
  409 `already_terminal`. ③ 잡 없는 요청 취소 vs planner 잡 생성 — 요청만 Cancelled 로 덮이고 잡은 Pending 으로 남아 실행됐다 →
  Pending CAS(지면 다시 읽어 잡 경로). 배치 취소와 오케스트레이터 자식 생성의 경합(BACKLOG 기재)은 이번 범위 밖.
- **스토리지 간 sync 경로 오판**: `domain.validate_sync_paths` 가 스토리지를 보지 않고 상대경로끼리만 비교했다 → 같은 스토리지일
  때만 상대경로 포함 검사(제출 시점), 다른 스토리지는 `sync_overlap_problem`(managed_root 를 붙인 실제 경로)으로 planner 가 도구를
  정한 **뒤 dsync 에서만** 판정 — 사용자 사례 통과, 한 노드에 함께 마운트된 두 스토리지의 루트가 포개져 실제로 같은 곳(/c/m 의
  t/x → /c/m/t 의 x)은 새로 막는다. nsync(서로 다른 노드의 다른 파일시스템)는 비교하지 않는다(검증 지적 — 처음엔 제출·배치 API 에서
  거부해 CPU 노드 /home 과 GPU 노드 /home/gpu1 같은 합법 sync 를 막을 수 있었다). stepper `_abs` 와 같은 조합 규칙
  (`managed_abs_path`). 문구 「…(스토리지 경로를 붙인 실제 경로 기준)」.
- **단계·상태 설명 툴팁**: 설명 원본 `frontend/src/lib/stateHelp.json`(요청·잡·배치 상태 + 단계, 백엔드 계약 테스트가 서버 상태
  목록과 키를 대조). 상태 배지(전체 작업 목록·요청 상단·잡 카드·배치 목록·배치 상세)는 포커스·마우스·탭으로 설명을 열고 Esc 로
  닫는다(첫 줄 「상태 코드 X」), 단계 머리(사전 점검·미리보기 / 실행)와 컨펌 관문에 「? 」 설명 버튼. 도메인별로 명시해서만 붙인다
  (빌드·릴리스 배지는 그대로). 설명 sr-only 를 배지 감싸개 안에 묶어 375 가로 스크롤 표에서 문서가 넓어지던 것(e2e L1 619/375)을
  잡았다.
- **검증 지적 반영**(취소·경로·툴팁 렌즈 + 지적마다 반박 검증, 확인 9건): ① 배치 취소에 커밋 뒤 회수가 없어 취소 중 제출된 root
  실행이 Cancelled 배치 밑에서 돌았다(재현) → 자식마다 회수, 요청 마무리는 회수 뒤 지금 잡 상태로(그 사이 성공한 자식을 Cancelled 로
  덮지 않음), 잡 없는 자식은 Pending CAS. ② 경로 판정을 처음엔 제출·배치 API 에서 했는데, nsync(소스·목적지가 다른 노드의 다른
  파일시스템)에서는 문자열 비교가 무의미해 합법 sync 를 막을 수 있었다 → planner 가 도구를 정한 뒤 dsync 에서만. ③ 툴팁 클릭이
  토글이라 진입·포커스로 연 직후 닫혔다(터치 첫 탭에 안 뜸) → 클릭은 열기만. ④ 설명 노드 sr-only → hidden(낭독기 읽기 모드·복사에
  섞이던 것, 가로 스크롤 표의 문서 넓힘도 함께 사라짐). ⑤ 문구 4곳 정정: 실행 직전 재점검은 권한·경로만(파일 변화 재비교 없음),
  scan 소요는 대기열 시간 포함, 확인 대기에도 이미 확인된 항목은 계속 실행, 시간 초과도 일부 변경이 남을 수 있음.
- 테스트: 백엔드 새 `test_api_cancel_race.py` 10(커밋 뒤 회수·정직한 409·회수 실패 이벤트·planner 경합·CAS·컨펌 대기 취소·배치
  취소 회수·배치 자식 실제 결과·배치 잡 없는 자식 경합), `test_sync_cross_storage_paths.py`(순수 판정 표·제출·배치·planner dsync 거부/
  nsync 비교 안 함/사용자 사례 dsync 계획), `test_state_help_contract.py` 3. vitest 1227(+11), `tsc -b`, 빌드 외부 URL 0, e2e 21(새 E9 2).

배포: 테스트베드 **d174**(아래 rm 미리보기 묶음과 함께 — dms-api·dms-controller + 잡 이미지 dms-mpifileutils d174, guard-images
통과, 오버레이 newTag·patch-config DMS_JOB_IMAGE d174).

실증(2026-10-12, 일회용 경로만 — 끝에 정리):
- 스토리지 간 같은 상대경로 sync(cephfs-dms `xsproof-…` → cephfs-secondary `xsproof-…`): 예전엔 422 → 202, 미리보기 「복사 대상 3」
  까지 진행(한 노드에 함께 마운트 = dsync, 실제 경로는 겹치지 않음).
- 실 Chrome: 컨펌 대기 XS 의 잡 카드 「취소」 표시, 배너 「컨펌하지 않고 취소」 → 잡 Cancelled. 컨펌 대기 rm(Q)은 잡 카드 「취소」 →
  Cancelled. 관문 「?」·전체 작업 목록 상태 배지·단계 머리 설명 툴팁이 열리고 Esc 로 닫힘, 1280·375 문서 넘침 0, 콘솔 오류 0.

### ✅ rm 미리보기 「삭제 대상 모름」 → 삭제 대상 수 — **완료·실증**(2026-10-12, d174)

사용자 보고: "rm 작업에 대한 미리보기의 결과가 삭제대상 모름 으로 표시돼. 확인해서 개선해줘."

- **원인**(테스트베드 실측 — 최근 rm 잡 11건 전부 미리보기 files null, 같은 잡 실행은 2·4·6): `drm --dryrun` 은 삭제하지
  않아 「Removed N items」를 찍지 않는다 — walk 요약 뒤에 지울 목록(`mfu_flist_print`, 앞·뒤 10개만, 중간 `<snip>`)뿐이다
  (이미지와 같은 mpifileutils 1b93d54 소스로 확인). 러너가 실행 파서(`parse_rm_counts`)를 미리보기에도 써서 늘 null →
  컨펌 창·미리보기 결과 「삭제 대상 모름」, 지문도 사실상 상수. 2026-09-17 dsync 미리보기와 같은 유형이다.
- **수정**: `parsers.parse_rm_dryrun_counts` — walk 요약의 첫 최종 줄 = 지울 수(DMS 는 drm 에 regex 필터를 싣지 않아 walk
  목록 전체가 지울 대상, 실행의 「Removed N」과 같은 값 — 실측 Walked 6 → Removed 6, 대상 경로 포함). 러너는 drm 미리보기에서
  이 파서를 쓴다. 미리보기 argv 에서 `--quiet` 를 뺀다(drm·dsync·nsync — `-q` 가 로그 수준을 NONE 으로 내려 walk 요약·
  「Planned actions」를 지워 quiet 미리보기는 고쳐도 늘 모름이었다, 실행에는 그대로). 포탈: rm 의 수가 무엇을 센 것인지
  (대상 경로 포함 합계), 0 = 대상 경로를 찾지 못함, 모름(옛 잡) = 미리보기 출력의 'Walked N items' 줄, quiet 툴팁.
- **검증 지적 반영**(도구 출력·argv·UI 렌즈 + 지적마다 반박 검증): ① dry-run 파서의 「Removed N 우선」 분기 제거 — dry-run 은
  그 줄을 절대 찍지 않아 목록의 파일 이름(「Removed 1 items.txt」)만 잡아 walk 25만을 1 로 덮었다(재현). ② walk 정규식을 MFU
  INFO 접두사에 고정 — 앞서 찍히는 「Walking <경로>」·walk 오류 줄의 경로 텍스트가 개수로 잡혔다(재현, dsync 미리보기의 기존
  노출도 함께 닫힘). ③ drm 의 0 은 빈 대상이 아니라 대상 lstat 실패(빈 디렉터리도 대상 자신 1) — 포탈이 「대상 경로를 찾지
  못함」으로 따로 말한다(컨펌 창·미리보기 결과·KPI). ④ 실패 시점 미리보기에는 rm 설명 줄을 붙이지 않음. ⑤ 「목록 확인」 대신
  'Walked N items' 줄(목록은 앞·뒤 10개). ⑥ quiet 툴팁에 실행 결과 개수도 「—」. 범위 밖으로 남긴 것: 취소 경합·컨펌 대기 잡의
  포탈 취소 버튼(같은 날 취소 구현 설명에서 사용자에게 보고).
- **푸시 뒤 보안 검토 반영**(parser-differential): 경로 이름에 줄바꿈 + 「[ts] Walked 1 items in 0.0 seconds (」를 심으면 줄 머리
  앵커를 통과하는 가짜 줄이 진짜 요약 앞(「Walking」·walk 오류 줄)이나 뒤(drm 목록)에 생겨 컨펌 창 수를 속일 수 있었다. 가짜는
  더할 수만 있으므로 walk 횟수보다 많이 보이면 모름 — drm 미리보기는 최종 walk 줄 정확히 1개, dsync 미리보기는 2개 이하.
- 테스트: 백엔드 parsers·runner·manifests 133(실측 캡처, 큰 트리 `<snip>`, quiet 출력, 파일 이름·경로 이름 공격 재현, 0 =
  대상 없음/빈 디렉터리 = 1, 미리보기 argv 의 quiet 제외·실행 유지), vitest 1216(+5), `tsc -b`, 빌드 외부 URL 0, e2e 19.

배포: 테스트베드 **d174**(잡 이미지 dms-mpifileutils 와 dms 를 함께 — 러너 파서는 잡 이미지, 미리보기 argv 는 제어면. d173 은
보안 보강 전 커밋이라 빌드만 하고 배포하지 않았다).

실증(2026-10-12, 일회용 트리 — 대상 자신 + sub/ + 파일 3 = 5항목): rm 미리보기 A(기본) **5개**, Q(quiet 옵션) **5개**(미리보기 argv 에
--quiet 없음 확인), 없는 경로 M **0개**(「Failed to stat」 → Walked 0). 실 Chrome: 미리보기 결과 「삭제 대상 5개」 + 「대상 경로 자신과 그
아래 … 합계」, 컨펌 창 「삭제 대상 5개(대상 경로 포함, 파일·디렉터리 합계)」, M 은 「삭제 대상 0개 — 대상 경로를 찾지 못했습니다…」·KPI
「미리보기: 대상 경로를 찾지 못함」. A 컨펌 → 실행 「Removed 5 items」(미리보기와 같음). Q·M 은 취소, 트리 정리.

### ✅ 배치 sync 자동 확인(옵션, 기본 꺼짐) — **완료·실증**(2026-10-11, d171·d172)

사용자 요청: "운영자 권한으로 sync 실행 시, preview 후 confirm 을 자동으로 승인해서 바로 작업 실행시키도록 하고싶어.
구현 검토해 줘." → 검토(정독 5·설계·반박 3 워크플로) 뒤 사용자 결정: **단건 sync 는 그대로 사람이 승인**, **배치 sync 는
자동 승인을 옵션으로(기본 false)**, 자격은 **특권 세션 관리자만**, 포탈 체크박스 기본 꺼짐. 원 설계 「preview→confirm 은
root 도 우회 불가」(clean-slate 스펙)와 d161 「사람이 보지 않은 미리보기는 자동 컨펌하지 않는다」를 **배치 옵션에 한해**
사전 동의로 바꾸는 결정이다 — 단건 쪽은 그대로.

- **설계**: 새 실행 경로를 만들지 않는다. `batches.auto_confirm_by`(켠 사람, NULL = 꺼짐)가 있는 sync 배치는 PreviewReady
  에서 orchestrator 가 사람의 「배치 확인」과 **같은** `BatchesRepository.confirm`(회차·Queued 없음 CAS + 그 순간
  ConfirmPending 자식 도장 + audit_log `batch/confirm`, actor `system:auto-confirm` — 계정 이름 규칙 밖의 ':' — 와 after 의
  `auto_confirm_by`)을 부른다. 도장·Running 분기(도장 없는 미리보기면 Previewing 복귀)·항목 추가/재실행의 Previewing 복귀는
  그대로라 확인은 여전히 한 회차의 미리보기 전부를 대표한다.
- **켜기·끄기**: 생성 본문 `auto_confirm`(StrictBool, sync 만 — scan 은 422 `auto_confirm_sync_only`)과 종단 배치의 실행
  설정 변경(PATCH /execution)에서만 켠다(둘 다 특권 3중 게이트 — 토큰·목록 밖 관리자 403). 끄기는 어느 상태에서나
  `POST /api/admin/batches/{id}:auto-confirm-off`(require_admin — 실행을 줄이기만 한다, 유지보수 중에도, 멱등, 감사
  `batch/auto_confirm_off`).
- **확인 직전 재검증**(`batch_orchestrator.auto_confirm_hold`, DB 가 신뢰 경계): 사이트 특권 실행 꺼짐
  (`auto_confirm_privileged_disabled`), 켠 사람이 특권 목록 밖(`auto_confirm_not_privileged`), 계정이 비관리자·비활성·없음
  (`auto_confirm_not_admin`), 옵션이 지금 규칙에 어긋남(예 `chown_name_not_supported`)이면 확인 대기에 남고 사람이 누른다
  — 경고 이벤트 `batch_auto_confirm_held` 는 회차마다 한 번. 상세 GET 의 `auto_confirm_hold` 가 같은 술어다.
- **포탈**: 생성 화면 「실행 제어」에 「미리보기 후 자동 실행」(sync 만, 기본 꺼짐) + 요약 「실행 확인」 행 + 연산 안내 문장
  교체. 배치 상세: 헤더 「자동 실행 (켠 사람 X)」(어느 상태에서나), 미리보기 중·확인 대기 안내(곧 실행 / 보류 사유),
  「자동 실행 끄기」, 실행 설정 「실행 확인」 행, 끝난 자동 배치의 재실행 안내, 항목 추가 문구, 확인 대화상자의 「시스템이 곧
  같은 확인」 안내. 실행 설정 변경 대화상자에 토글(바꿨을 때만 실음). 배치 목록 작업 칸 「자동」.
- **검증 지적 반영**(보안·동시성·UI 세 렌즈 + 지적마다 반박 검증, 확인 7건): ① 끄기가 진행 중인 자동 확인을 못 막았다 —
  틱이 배치를 읽은 뒤 「자동 실행 끄기」가 200 을 받았는데 루프가 옛 읽기로 확인해 root 로 돌던 창(재현) → 확인 UPDATE 를
  동의에 묶음(`confirm(expected_auto_confirm_by=…)` — 같은 문장에서 `auto_confirm_by` 일치 + 계정 활성 관리자 EXISTS). ②
  컨트롤러가 틱마다 orchestrator 를 새로 만들어 「회차마다 한 번」 경고가 5초마다 쌓였다 → 모듈 수준 중복 방지(테스트도 틱마다
  새 인스턴스). ③ UI: 헤더 상시 표시·끝난 배치 재실행 안내·확인 대화상자 경합 안내·사람 확인이 자동 확인에 진 409 를
  「이미 자동 확인돼 실행 중」으로·사이트 특권 꺼짐 별도 사유·보류 시 미리보기 중 안내/항목 추가 문구. 기각 2건(위조 행에서만
  생기는 순환, 켠 사람 유지는 의도한 설계).
- 테스트: 백엔드 새 `test_batch_auto_confirm.py` 26(켜기·끄기·게이트·같은 확인 함수·도장·전체 주기·Queued 둑·사람과의 경합 두
  방향·재검토 재확인·보류 6갈래·틱 중간 끄기/강등 재현·위조 scan 행·단건 무관·기배포 DB 마이그레이션) + 실 PG16 통과, 마이그레이션
  그물 41 → 42. vitest 1211(+15 — 생성 체크박스·바디·요약, 상세 안내·보류·끄기·헤더·대화상자·409 경합, 실행 설정 토글,
  목록 표시), `tsc -b`, 빌드 외부 URL 0, e2e 19(새 E8 2건 — 생성 화면 체크 → 실 오케스트레이터 자동 확인 → Completed,
  끈 배치는 확인 대기에서 멈춤 → 사람 확인 → Completed). 최종 백엔드 전체 2994 passed.
- 실증에서 잡은 것(d172): 375 폭 배치 상세가 가로로 넘쳤다 — 끝난 배치의 버튼 넷이 한 줄이라 자동 아닌 배치도 387~394/375
  (기존 결함), 「자동 실행 (켠 사람 …)」 표시가 붙으면 455/375. 헤더 세 줄을 flex-wrap 으로, e2e E8 에 375 문서 넘침 0 단언
  (수정 전 클래스로 되돌리면 452 > 375 로 실패함을 확인).

배포: 테스트베드 **d171**(dms-api·dms-controller, 잡 이미지·에이전트 d158 그대로) — migrate 가 `batches.auto_confirm_by`
를 붙였다(기존 배치 전부 꺼짐). **d172** = 375 헤더 수정만(실 Chrome 375: 끝난 sync·scan 배치 상세 393·394 → 375/375,
버튼 두 줄). guard-images 통과.

실증(2026-10-11, 일회용 경로 `dms_test/dst_new/autoproof-*` 만):
- 실 Chrome(mason): 생성 화면 체크박스 기본 꺼짐 → 켜면 요약 「자동 — 미리보기가 끝나면 바로 실행」. 켠 배치 A 는 Previewing
  (헤더 「자동 실행 (켠 사람 mason)」·「자동 실행 끄기」) → PreviewReady → **5초 뒤 사람 확인 없이 Running** → Completed. 감사
  `batch/confirm` actor `system:auto-confirm`·auto_confirm_by mason·회차 1·도장 1, 이벤트 `batch_auto_confirmed`, 자식 잡
  ConfirmPending → Executing(batch-orchestrator) → Succeeded, 목적지 생성.
- 브레이크: 켠 배치 B 를 미리보기 중 「자동 실행 끄기」 → 확인 대기 뒤 15초 그대로(자식 ConfirmPending), 안내가 「「배치 확인」을
  눌러야」로 돌아옴, 감사 `batch/auto_confirm_off`(mason) → 취소(자식 Cancelled, 목적지 없음).
- 권한: 공유 토큰으로 `auto_confirm: true` 배치 생성 403 `privileged_not_authorized`(아무것도 안 생김).
- 보류 경로(특권 목록·강등·옵션)는 라이브 설정을 바꾸지 않고 단위 테스트(틱 중간 끄기·강등 재현 포함)로 고정.
- 정리: A·B 배치 단위 삭제, A 가 만든 목적지 삭제.

### ✅ 전체 작업: 배치 단위 삭제 + 「배치」 열 — **완료·실증**(2026-10-10, d170)

사용자 요청: "전체 작업에서 배치 요청도 배치 단위로 삭제할수 있도록, 그리고 배치작업에 대한 식별도 할수있게 column
추가해줘". 전제(이전 결정 유지): 배치 자식은 **개별로는** 지우지 않는다(`batch_child_not_deletable`), 삭제는 세션 관리자·
종단·조용한 창, scan 은 확인 창에서 사용량 경고. 결정(스펙 §0·§12 기본값): 배치 화면 「배치 삭제」는 **기록만** 지우는
동작을 유지하고 문구만 고친다, 배치당 작업 상한 1000(넘으면 `batch_delete_too_large` — 청크 삭제는 BACKLOG), 「배치」 열은
관리자만, 목록 시각의 「 KST」 는 머리줄로.

- **저장소**(`repositories/request_purges.py`): 단건 몸통을 트랜잭션을 열지 않는 집합 코어 `_purge_locked` 로 뽑아 단건
  (`delete_terminal`, CAS `batch_id IS NULL`)과 새 `delete_batch`(CAS `batch_id = :bid`)가 같이 쓴다. `delete_batch` = 배치
  하나(또는 배치 행이 없는 dangling 묶음)를 **한 트랜잭션**으로: 잠금 batch_items → batches → requests(request_id 순) →
  data_jobs(오케스트레이터·`reject_queued_item` 과 같은 방향), 게이트(배치 종단 → 자식 ≤ 1000 → 확인 창이 본 자식 수 CAS →
  다른 배치 항목 비참조 → 자식 전부 종단 → 잡 전부 종단 → 조용한 창), 자식 전부 삭제 + batch_items·batches DELETE + 유령
  자식 재확인(PG READ COMMITTED 의 동시 INSERT) + 감사(자식마다 기존 행 — 스냅숏에 batch_id 추가 — + `('batch','delete')`
  1행, 32 KiB). 자식 집합은 `requests.batch_id`(재실행 이력 포함 — 항목 참조가 아니다). `batches.list_summaries`(목록용 배치
  요약 6키), `add_item` 은 배치 행 재확인(지워진 배치에 고아 항목 방지).
- **API**: `POST /api/admin/requests:delete` 가 본문 `batches:[{batch_id, expected_request_count}]`(호출당 ≤ 10)를 받아
  `deleted_batches`·`skipped_batches` 를 돌려준다(배치마다 예외 격리 `batch_delete_failed`). `GET /api/user/requests` 는
  **관리자 응답의 배치 자식 행에만** 배치 요약(존재·이름·상태·자식 수·비종단 자식 수·성공 scan 자식 수)을 붙인다. 사유
  코드 신규 4(`batch_delete_too_large`·`batch_changed`·`batch_delete_failed`·`delete_batch_selection_too_large`) + 문구 교체 2
  (`batch_not_deletable`·`batch_child_not_deletable`).
- **포탈**(`features/jobs`): 「요청」 다음에 **「배치」 열**(관리자만 — 이름 링크, 이름 없으면 id 12자, 기록이 지워진 배치는
  링크 없는 「삭제됨 xxxxxxxxxxxx」, 단건 「—」). 배치 자식 행의 체크박스는 **배치 전체 토글**(`batchGroups.ts` — 같은 배치의 불러온
  행이 함께 체크·옅은 배경, 고를 수 없는 배치는 진행 중·끝나지 않은 작업·1000개 초과·요약 없음별 사유 title), 헤더 전체
  선택은 단건만. 툴바 「배치 B개 선택됨(작업 M개 · 성공 scan k개)」(M·k 는 서버 수 — 화면 밖 자식까지), 배치 10개 초과 잠금,
  유령 정리(재실행으로 다시 활성이 되면 선택에서 빼고 알림), 결과 「N개 · 배치 B개(작업 M개) 삭제됨 · …제외」 + 배치마다
  제외 줄(「배치 X: 사유 (작업 abc…)」). 확인 창은 배치 목록(이름·연산·상태·작업 수)·안내 두 줄(목록·필터와 무관하게 전부,
  배치 기록도 지워져 재실행 불가)·사용량 경고 합을 보이고, 연 순간의 자식 수를 `expected_request_count` 로 보낸다. 폭 예산:
  요청 id 10rem → 8rem, 배치 열 6rem, 시각 칸 「 KST」 제거(`kstStampBare`, 머리줄 「생성(KST)」). 단건만 고른 경우의 모든
  문구는 글자 단위로 그대로다. 배치 상세 「배치 삭제」 확인 문구는 「배치 기록만 삭제 — 작업까지는 전체 작업에서 배치 단위로」.
- **검증 1차 지적 반영**(17건 — DB 동시성·API 보안·UI 세 렌즈): CSV 항목 교체(`replace_items`)도 트랜잭션 안에서 배치 행·
  상태를 다시 봐 배치 단위 삭제와 경합하면 404(예전엔 지울 길 없는 고아 항목 + 거짓 200), 잠금 batch_items → batches. 아웃박스
  기존 행 조회를 자식마다가 아니라 묶음마다 한 번(PG 일반 계획의 Seq Scan 이 자식마다 아웃박스를 훑어 1000자식 배치 10연속에서
  트랜잭션이 361 → 747ms 로 늘던 것 → 339 → 317ms). 배치 감사 스냅숏은 자식 id(자식 감사와 중복)부터 줄이고 항목은 남긴다.
  정리 루프의 파드 대기는 그 행의 첫 k8s 스윕부터 잰다(1000자식 배치에서 거짓 「지연」 260건 → 0). `expected_request_count` 는
  JSON 정수만(true·"1"·1.0 422). 배치를 단건보다 먼저 처리해 한 응답이 같은 요청을 「제외」와 「삭제」로 동시에 말하지 않는다.
  포탈: 배치 열 고정 6rem **두 줄**(접두가 같은 이름 구별, 「삭제됨 + id 12자」가 다 보임, ink/70), 목록에서 밀려난 배치는
  「목록에서 사라져」(고를 수 없게 된 것과 나눔), 제외 줄의 작업 id 는 상세 링크, 배치 10개 초과 꼬리말을 짧게(1024·375 툴바
  높이 무변 실측), 불러온 행이 끝나지 않았으면 서버 요약이 0 이어도 묶음 선택 불가, 헤더 전체 선택 이름 「…(배치 제외)」, 문구
  셋(「아직 삭제할 수 없습니다」·요약 없는 묶음 「배치 정보를 확인할 수 없어…」·확인 창 「배치 기록 없음」), 배치 목록·상세의
  기록만 삭제 안내(늘 있는 줄·「삭제됨 xxxxxxxxxxxx」로 보인다는 예고).
- **검증 2차 지적 반영**(19건 — DB 동시성·API 보안·UI): 목록 배치 요약은 배치마다 `LIMIT 1001` 갈래로 세고 넘으면 capped
  (비종단·성공 scan 은 null — 실 PG 배치 밖 성공 scan 1M 위 자식 2만 110.8ms → 0.4ms, data_jobs 전체 Seq Scan 소멸).
  retention `prune_events` 는 PG 에서 `FOR UPDATE SKIP LOCKED`(기다리지 않으니 배치 삭제와 교착하지 않는다 — 실 PG 재현
  스크립트 deadlock → 둘 다 성공). 배치 감사는 넘치면 항목을 「들어가는 만큼(자식 없는 항목 먼저)」 남기고 null 키를 뺀 줄인
  모양 + item_count·items_kept(항목 600개: 0개 → 600개, 1만 개: 0 → 918개). 항목 상한 1만(`batch_delete_too_large`,
  5만 항목 509ms → 3문장 거부). 배치 행·자식 없이 항목만 남은 묶음도 지운다(옛 경합·롤링 업데이트 잔재 — 그 항목이 막던
  단건 삭제도 풀린다). 다른 배치 항목이 자식을 가리키면 새 사유 `batch_child_shared`(배치 단위로 고른 사람에게 「배치 단위로
  선택해 삭제하세요」를 돌려주던 순환 문구). 정리 루프 전역 단계의 원 행 재등장 확인을 묶음 한 번으로(`present_targets` —
  1만 행 틱 1.3s → 0.37s, 나머지 선형 비용은 BACKLOG). 목록 API `batch_id` 필터 + 포탈 `/jobs?batch=`(필터 표시·해제) + 배치
  상세 머리줄·「배치 삭제」 창·「기록 없음」 칸의 링크(오래된 배치의 작업을 찾을 길이 없던 것). 포탈 배치 칸: 고를 수 없는
  배치는 둘째 줄에 사유(「배치 진행 중」·「작업 진행 중」·「작업 1000개 초과」, 체크박스 aria-describedby — hover 전용이던
  것), 긴 이름은 앞 조각 말줄임 + **끝 조각**(「…이관(1차)」·「…(2차)」 구별), 1440px 이상은 12rem 한 줄(1920 행 높이 39px
  균일 — 실측), 「삭제됨」 → 「기록 없음」(그 묶음만 거른 목록 링크), 오른쪽 여백. 판정 순서를 서버와 맞춤(1000개 초과가 끝나지
  않은 작업보다 앞). 포커스를 쥔 배치 체크박스가 폴링으로 잠기면 포커스를 툴바 상태 줄로. 확인 창: 기록 없는 묶음만이면
  「배치 기록도 함께 지워져…」를 그리지 않음, 두 목록에 보이는 머리말. 「단건」 → 「단일 작업」, 생성·갱신 시각 사이 여백
  (2px → 13px), 일반 사용자의 요청 id 칸은 10rem 그대로. 라우트 docstring 에 「모양이 틀린 본문은 유지보수 중에도 422」
  (다른 Pydantic 라우트와 같은 성질 — 수정 없음). 1280 표 래퍼 958/958 유지(실측).
- **검증 3차 지적 반영**(11건 — DB 동시성·API 보안·UI): 인덱스 `idx_requests_batch(batch_id)` → `idx_requests_batch_order
  (batch_id, commit_order)`(만든 뒤 옛 것 DROP, 전수 열거 그물 갱신) + 한 배치의 자식 읽기를 범위 표기(`batch_match` — `batch_id
  >= :b AND batch_id <= :b` + `ORDER BY batch_id, commit_order`)로: PG 가 정렬 키를 상수로 지워 큰 배치에서 UNIQUE(commit_order)
  역방향·Seq Scan + LIMIT 로 테이블 대부분을 훑던 것(실 PG 32만 행: `?batch_id=` 오래된 배치 31.6ms·자식 2만 배치 22~41ms →
  0.02~0.03ms, 일반 계획 동일, 요약 갈래 6ms → 0.3ms). `delete_batch` 상한 판정은 상한 + 1 행까지만(자식 2만 배치 거부 73.6ms
  · 20001행 → 0.4ms · 2행). 목록 요약에 `batch_item_count`(batches.item_count — 항목 1만 초과 배치도 포탈이 미리 잠근다).
  `batch_child_shared` 로 빠진 배치는 같은 호출에서 다른 배치가 지워지면 다시 시도([A, B]·[B, A] 같은 결과, 사슬도 바퀴마다
  풀림). 같은 호출에서 고른 배치가 거부되면 그 자식 id 의 단건 사유(「배치 단위로 선택해…」)를 싣지 않는다. 포탈: 배치 열 폭을
  머리칸이 예약(`w-[6.5rem]` — 첫 배치 행이 폴링으로 들어와도 표 재배치·행 밀림 0), 1440px 이상 12rem 폐지(모든 폭 6rem, 긴
  이름 두 줄), 요청 id 6.5rem·생성 칸 여백 8px — 대상 열 1280 202 → 210px(글꼴을 제대로 띄운 HEAD 208px 이상, 1280~1920 에서
  단일 행이 HEAD 보다 높은 폭 0 — 실측). 잠긴 배치의 포커스 복귀는 같은 행의 요청 링크(행이 없으면 상태 줄), `preventScroll`,
  빈 곳 클릭 뒤엔 옮기지 않음(스크롤 1200px 위치 유지 실측). 「필터 해제」 뒤 포커스는 상태 필터로. 사유 줄 「작업 진행 중」 →
  「미완료 작업 있음」, 「작업 1000개 초과」 → 「삭제 상한 초과」(6rem 한 줄 실측). 옛 용어 「삭제된 배치」 주석 정리.
- 테스트(검증 1차 수정 반영 후): 백엔드 2940 passed(새 저장소·API·목록 요약·오케스트레이터 재생성 불가·add_item·replace_items
  경합, 실 PG 두 커넥션 경합 실증 스크립트 + 수정 범위 실 PG 재실행), vitest 1180 passed(배치 열·묶음 선택·비활성 사유·헤더·유령 정리·필터·상한·결과 조합·확인 창 스냅숏·훅·
  `kstStampBare`), `tsc -b`, 빌드 외부 URL 0, e2e 16 passed(새 2건 — 실 오케스트레이터 배치 + 재스캔 4자식을 한 행 체크로 묶음
  삭제·배치/자식 404·공유 토큰 403·1280 표 래퍼 넘침 없음·375 기하, 배치 기록만 지운 묶음 「삭제됨」 삭제).
- 테스트(검증 2차 수정 반영 후): 백엔드 2953 passed(요약 상한·문장 모양, prune SKIP LOCKED, 감사 부분 항목·줄인 모양, 항목
  상한, 항목만 남은 묶음, batch_child_shared, present_targets·전역 단계 문장 수 고정, batch_id 필터), vitest 1190(라우터
  로그아웃 1건은 알려진 부하 flake — 단독 18 passed; 새: 사유 줄·aria-describedby, 앞·뒤 조각 이름, capped, 배치 필터·해제·
  「기록 없음」 링크, 포커스 이동 2건, 확인 창 머리말·기록 없는 묶음 안내, 요청 칸 폭), `tsc -b`, 빌드 외부 URL 0, e2e 16
  passed(배치 상세 → 거른 목록 → 필터 해제, 「기록 없음」 링크 → 거른 목록에서 삭제). 실 PG 재측정: 요약·교착·감사·항목
  비용·정리 루프 틱(검증 스크립트 재실행).
- 테스트(검증 3차 수정 반영 후): 백엔드 2963 passed(인덱스 교체·기배포 DB 경로·필터 문장 계획, 상한 + 1 읽기·판정 순서,
  항목 수 요약, batch_child_shared 재시도(두 순서·사슬·진전 없음 멈춤), 거부된 배치 자식의 단건 사유 접기) + 수정 범위 실 PG16
  163 passed, vitest 1194 passed(항목 상한·사유 줄 문구, 배치 머리칸 예약 폭, 포커스 복귀 3건(같은 행 링크·상태 줄·빈 곳 클릭 뒤
  무변) preventScroll, 「필터 해제」 뒤 포커스), `tsc -b`, 빌드 외부 URL 0, e2e 16 passed(배치 열 폭 = 104px · 1920 에서도 같음
  · 배치 행 없는 화면에서도 같음).
- **검증 4차 지적 반영**(7건 — DB·UI): PG 세션 **jit=off**(`db.py` 연결 직후 `SET jit = off`, 재연결 공용, 실패는 삼키고 연결 유지 — 시작 옵션은
  PG 10 이하·풀러에서 모든 연결을 FATAL 로 만들어 쓰지 않는다, URL options 가 jit 를 말하면 그 값): 목록 요약의 배치당 UNION ALL 갈래가 문장 비용을 JIT 임계 위로 올려 쪽에 큰 배치 50개가
  섞인 관리자 목록 GET 이 5.7초, 그동안 /readyz 5.5초 대기(readinessProbe 실패) → 0.23초 · /readyz 9ms(실 PG16 재측정).
  포탈 배치 칸은 **상태와 무관하게 두 줄 이하** — 막히면 사유 줄이 뒤 조각(이제 text-xs)·「기록 없음」의 id 줄을 대신한다
  (긴 이름 배치가 Running → Completed 폴링에 59 → 43px 로 줄어 표가 최대 160px 움직이던 것 → 0, 1280~1920·375 실측). 배치
  머리칸 예약 폭 = 6.5rem + 1px(UA 왼쪽 여백 — 104px 은 링크 칸 105px 보다 짧아 첫 배치 행이 열을 넓혔다). 막힌 체크박스의
  설명에 처방까지(사유 줄 안 sr-only — 「삭제 상한 초과 — 작업이 1000개 또는 항목이 10000개를…」). 확인 대기(PreviewReady)
  배치는 「배치 확인 대기」·「배치 상세에서 확인하거나 취소한 뒤…」(「배치 진행 중 · 끝난 뒤에」였다). 「필터 해제」는 키보드로
  눌렀을 때만 상태 필터로 포커스(마우스엔 select 의 :focus-visible 링이 남았다), 칩 title = 이름 + id. 규칙 줄을 줄여 375·414
  툴바 184/168 → 168px, 768 은 HEAD 와 같은 152px(한 줄).
- 테스트(검증 4차 수정 반영 후): 백엔드 2967 passed(세션 jit=off SET·실패 시 연결 유지·재연결 재적용·URL 의 jit 명시 존중·sqlite
  무변), vitest 1196 passed(칸 두 줄 구조 — 막힘 ↔ 고를 수 있음 전이, 뒤 조각 text-xs, 설명 = 사유 + 처방, 확인 대기 사유,
  필터 해제 키보드/마우스 포커스, 칩 title, 머리칸 폭 클래스, 사유 한 줄 폭), `tsc -b`, 빌드 외부 URL 0, e2e 17 passed(새 1건
  — 목록 응답만 흉내 내 1280·375 에서 Running → Completed → Running 전이 전후 행 높이·아래 행 y 동일, 배치 열 폭이 배치 행
  유무와 **정확히** 같음, 사유 줄 말줄임 없음, 규칙 줄 375 ≤ 2줄·768 1줄; 옛 머리칸 폭·세 줄 칸으로 되돌리면 각각 실패함을
  확인). 기존 E7 배치 열 단언도 「≤ 1px」 → 정확히 같음·105px.

- 최종 전체 회귀(jit 를 시작 옵션 → 연결 직후 SET 으로 바꾼 뒤): 백엔드 2968 passed.

배포: 테스트베드 **d170**(dms-api·dms-controller — 잡 이미지·에이전트 d158 그대로, guard-images 통과). migrate initContainer 가
인덱스 교체를 적용(`idx_requests_batch_order` 존재 확인), API 세션 `SHOW jit` = off.

실증(2026-10-11, 이 실증용 일회용 배치 P1~P5 만 — 기존 배치·dangling 묶음 무접촉):
- 권한: 공유 토큰 403 `admin_session_required`, 일반 사용자(alice) 403 `admin_required`, 기대 자식 수가 틀린 배치는
  `batch_changed` 로 건너뜀(무변).
- 실 Chrome(1280, 관리자): 「배치」 열 — 재스캔으로 자식 4개인 P1 은 이름 링크(앞 조각 + 끝 조각 두 줄), 배치 상세에서 기록만
  지운 P2 는 「기록 없음 290234aabc4c」, 확인 대기 P3 는 체크박스 잠김 + 「배치 확인 대기」 사유 줄. P1 한 행 체크 → 자식 4행
  전부 체크, 툴바 「배치 2개 선택됨(작업 5개 · 성공 scan 4개)」, 확인 창(배치 2개 목록·전부 삭제 안내·사용량 경고) → 「5개 영구
  삭제」 → 200 `deleted_batches` 2(P2 `dangling: true`), 「배치 2개(작업 5개) 삭제됨」, 행 5개 사라짐. 표 래퍼 958/958, 375 문서
  넘침 0(표는 래퍼 안 가로 스크롤), 콘솔 오류 0.
- 일관 삭제: P1·P2 의 batches·batch_items·자식 요청·잡 행 0, 배치 감사 1행씩(스냅숏 = 배치 행·자식 id·항목 / `dangling: true`)
  + 자식마다 요청 삭제 감사, 요청 이벤트 0, 배치 GET 404, 정리 대기열 pending 0·stalled 0, P1 잡 결과 폴더 4개 → 없음,
  `.dms-trash` 비어 있음(root 0700), k8s 잔여물 0.
- 서버 게이트: 확인 대기 P3 → `batch_not_deletable`(무변), 취소 직후 → `request_recently_finished`, 정지 창 뒤 삭제. 막 끝난
  P4 → `request_recently_finished`, 70초 뒤 삭제. P5 삭제. 끝에 5개 배치 전부 행 0·배치 감사 1건씩·결과 폴더 없음·정리
  대기열 0, 지운 배치 id 로 요청이 다시 생기지 않음(오케스트레이터 여러 틱 뒤 0).
- 범위 밖 관찰: 배치 상세 「배치 삭제」(기록만, `batches.delete`)는 감사 행을 남기지 않는 기존 관례 그대로다.

### ✅ 작업(요청) 선택 삭제 — DB·결과 파일·파드·로그 일관 삭제 — **완료·실증**(2026-10-09, d169)

사용자 요청: "포탈에서, 작업 목록에서 선택적으로 삭제하는 기능을 추가하고 싶어. 상태가 완료된 작업만 삭제를 허용할 예정이고,
삭제할때 시스템 전체적으로 일관되게 삭제하는게 중요해. 예를 들면 해당 작업과 관련된 아티팩트 및 로그들도 db정보와 함께
삭제되어야겠지." 사용자 결정: 종단 요청 전부(Succeeded·Failed·Rejected·Conflict·Cancelled — 진행 중·ConfirmPending 불가),
**관리자만**, scan 도 삭제 가능(확인 창에 사용량 분석 경고), **배치 자식은 개별 삭제 불가**. 설계 열린 질문 5개는 권장안으로
확정: 삭제된 배치의 자식은 계속 불가(BACKLOG), 조용한 창 60초, root 잡에 특권 세션 추가 요구 없음, 기존 누수(pod-gc 창·
Aborted vcjob·빌드 삭제 docstring)는 BACKLOG, 옛 base 사본은 경고만.

- **DB 는 API 가 한 트랜잭션으로 즉시**(`repositories/request_purges.py` `delete_terminal`, 설계 D1): 게이트(요청 종단 ∧
  `batch_id IS NULL` ∧ batch_items 비참조 ∧ 잡 전부 종단 ∧ 요청·잡 마지막 갱신 후 `DMS_REQUEST_DELETE_QUIET_SECONDS`) →
  requests → data_jobs 행 잠금(PG FOR UPDATE) → 7개 테이블(requests·results·plans(job_id NULL plan 포함)·data_jobs·
  state_transitions 3종·events·scan_report_digests) DELETE → 마지막 CAS DELETE(지면 전부 롤백, `request_not_deletable`) →
  **정리 아웃박스** `request_purges`(잡 id·phase_refs·artifact_uri·삭제 시점 base) + `audit_log('request','delete')` 1행
  (32 KiB 상한 스냅숏 — 요청·결과·전이 actor·잡 실행 신원 4키·run_as_root, diag_logs·요약 원문 제외, 넘치면 전이부터
  자르고 끝내 뼈대·id 만). 커밋 한 번으로 목록·상세·잡·아티팩트·로그·사용량·지표가 동시에 일관된다 — 읽기 경로에 「삭제
  중」 필터 0. FK 가 0건이라 삭제 목록이 곧 일관성의 전부다 — `PURGED_TABLES`/`PURGE_EXEMPT_TABLES` 와 스키마 전수 열거
  테스트(`test_request_delete_covers_every_reference_table`)가 새 테이블의 누락을 잡는다.
- **API**(`api/routes_request_purge.py`): `POST /api/admin/requests:delete` — **세션 관리자만**(공유 토큰 403
  `admin_session_required` — 컨펌·취소 기록과 root 산출물을 없애는 증거 삭제라 빌드·레지스트리 삭제와 같은 경계), 유지보수 503,
  1..200건(422 `empty_selection`·`delete_selection_too_large`), 중복 접기, 형식 오류 id 는 `request_not_found`(존재 오라클
  없음), **부분 성공**(deleted[].job_ids / skipped[].reason / purge_pending, 전부 skipped 여도 200). `GET
  /api/admin/request-purges`(조회 — 토큰도 됨): pending·stalled·oldest·items 50 — 정리의 유일한 운영 표면. api 는 FS·k8s 무접촉.
  artifact base 잠금 = 잡 + 정리 대기(force 없이 정리 중 base 가 바뀌지 않게).
- **컨트롤러 `request-purge` 루프**(`request_purger.py`·`purge_runner.py`·`artifact_trash.py`, 기본 15초): 아웃박스 행을
  `k8s` → `files` → `purging` → finish 로 멱등 수렴. k8s: **전체 job_id 로 확인된 객체만** 지운다 — 라벨 `dms.io/job-id`
  Pod·vcjob + launcher(`volcano.sh/job-name in (ref·라벨 vcjob ∪ 결정적 이름 6개)` 로 찾되 소유 vcjob(ownerReference uid)이
  이 잡의 라벨 vcjob 이거나 이미 없는 고아일 때만 — launcher 엔 dms 라벨이 없다). 기록된 ref 는 그 잡의 DMS 명명일 때만
  받아(`parse_ref` — 변조 행이 제어면 파드를 가리키는 것을 거른다, 거른 ref 는 `purge_ref_rejected`) launcher 를 찾을 이름
  후보로만 쓴다. 삭제 후 다시 세어 **Terminating 포함 0** 이 아니면 파일로 넘어가지 않는다(종료 중 launcher 가 디렉터리를
  다시 만든다; 10분 넘으면 `purge_waiting_pods`).
  files: cap 없는 제어면 root 는 요청자 소유 0755 phase 를 못 지우므로(실측 EACCES) `<base>/<job_id>` 를 `<base>/.dms-trash/
  <job_id>`(root 0700, 같은 FS) 로 **renameat 만** 한다(base fd·fstat 검사, phase 안으로 들어가지 않음). purging: 전역 1개의
  단명 **purge 파드**(root + DAC_OVERRIDE·FOWNER 만, base 볼륨 하나, SA 토큰 없음, 읽기 전용 루트 fs, 고정 스크립트 + positional
  이름 — 이름을 전부 먼저 검사하고 하나라도 틀리면 아무것도 지우지 않음, `rm -rf --one-file-system` 은 이름마다 끝까지 시도
  하고 하나라도 실패면 마지막에 exit 1, 앞선 파드가 못 지운 행은 다음부터 혼자 실림, 노드 = 지금 base 를
  writable 로 보고한 신선 노드 − 배치 제외·cordon·스케줄 불가)가 trash 를 비운다. 완료는 파드 상태가 아니라 FS 재확인(base·
  trash 둘 다 없음) → finish(늦게 들어온 events·digest·전이 최종 scrub + 행 삭제, 원 행이 되살아났으면 no-op) + `request_purged`.
  포기 없음 — 백오프(min(15s×2^n, 900s)) + last_error 로 지연 표면화. **「DB 에 없는 디렉터리 = 고아」 추론 금지**(아웃박스가
  가리키는 job_id 만), 옛 base 는 열지도 지우지도 않고 `artifact_left_at_old_base`, 지운 id 가 DB 에 다시 있으면
  `purge_target_still_present`(k8s·FS 무접촉). 모든 purge 이벤트는 request_id=NULL. RBAC 변경 없음.
- **ARCHITECTURE §7 root 제어면 규칙 13 개정**: 컨트롤러의 유일한 FS 변경은 `.dms-trash` mkdirat 과 `<job_id>` renameat 이고
  실제 삭제는 purge 파드(규칙 12 — 제어면이 아니고, 이미 도는 잡 launcher 보다 엄격히 약하다). `src/dms` 의 셸·subprocess·
  shutil·os.access·chown·chmod 0건과 정리 모듈의 unlink·rmdir 0건을 `tests/test_control_plane_static.py` 가 AST 로 고정.
- **선행 보강**(삭제가 새로 여는 경합을 구조적으로 닫음, 설계 D9): stepper — `set_phase_ref` 가 행 부재면 False(PG 행 FOR UPDATE
  로 삭제 트랜잭션과 직렬화), 네 제출 경로가 그때 방금 만든 ref 를 terminate + `submitted_for_deleted_job`(terminate 실패면
  error), `_reclaim_if_terminal` 도 행 부재면 회수, 행 없는 잡의 `step_error` 는 request_id=NULL. planner — emit 을
  `data_jobs.create_plan_and_job`(요청 행 잠금 + plan·job INSERT + 조건부 Pending→Planned 한 트랜잭션, 지면 전부 롤백)으로,
  멱등 분기는 `requests.mark_planned_if_pending` — 삭제된 요청에 잡을 만들어 실행하던 길과 함께 **취소↔planner 경합(Cancelled
  → Planned 부활)도 닫혔다**(BACKLOG 🔍 항목 갱신). 사용량 digest `put` 은 잡 행이 있을 때만.
- **포탈**: 작업 목록(관리자만) 체크 열 — 종단·단건만 활성(진행 중·배치 항목은 disabled + 이유 title), 전체 선택(불러온 쪽 전부,
  indeterminate), 필터 변경·폴링으로 사라진 행은 선택에서 빠짐, 200개 초과면 잠금. 툴바(자리 예약 — 표가 뛰지 않음)에
  「N개 선택됨(성공 scan M개)」/「N개 삭제됨 · M개 제외」+ 항목별 사유, 정리 현황 「결과 파일·파드 정리 중 N건 · 지연 M건」
  (+ 바 아래 「정리 지연: 사유」). 확인 창(`DeleteRequestsDialog`): 연 순간 선택을 스냅숏, 개수·연산별·앞 10건, 함께 지워지는 것, **스토리지의 실제
  데이터는 건드리지 않음(실행 취소가 아님)**, 성공 scan 이 있으면 사용량 분석 경고, 「되돌릴 수 없음을 확인」 체크 뒤에만
  「N개 영구 삭제」. 요청 상세는 404 면 「요청을 찾을 수 없습니다 — 삭제됐거나 볼 수 없는 요청입니다」 화면(재시도 없음, 요청
  폴링·포커스 재조회·잡 조회 중지). 삭제 성공 시 지운 요청·잡 캐시 제거 + 목록·정리 현황·사용량·잡 통계·감사·artifact-base
  무효화를 기다린 뒤 결과 표시. 덤: 상태 필터에서 요청 상태가 아닌 Previewing·ConfirmPending 제거(고르면 늘 0건이던 결함).
- 사유 코드(양쪽 등록): `request_not_deletable`·`request_job_active`·`request_recently_finished`·`batch_child_not_deletable`·
  `delete_selection_too_large`·`purge_k8s_failed`·`purge_waiting_pods`·`purge_base_unavailable`·`purge_base_unsafe`·
  `artifact_dir_unexpected`·`purge_detach_failed`·`purge_no_node`·`purge_pod_failed`·`purge_target_still_present`·
  `purge_row_invalid`·`purge_failed`(2차 반영으로 `request_delete_failed`·`purge_pod_stuck`, 3차로 `purge_entry_failed` 추가), `request_not_found`·
  `job_not_found`·`admin_session_required` 문구 갱신.
- 스키마·설정: 새 테이블 `request_purges`(새 테이블이라 `_ensure_columns` 불필요) + 인덱스 `idx_data_jobs_request`·
  `idx_plans_request`·`idx_batch_items_request`(전수 열거 그물 갱신). env `DMS_REQUEST_DELETE_QUIET_SECONDS`(60, api, 음수 기동 거부)·
  `DMS_REQUEST_PURGE_INTERVAL_SECONDS`(15, controller, 1 미만 기동 거부) — `deploy/k8s/20-config.yaml` 에 기본값 그대로.
  운영 runbook 은 `deploy/README.md` §12(지연 사유별 조치, Terminating 파드 강제 삭제 판단, `.dms-trash`, 정리 대기 중 롤백 금지).
- **구현 중 설계에서 바뀐 것**: (1) 정리 루프는 실행 어댑터 대신 purge_runner 의 자기 클라이언트로 지운다(먼저 나열해야
  `k8s_deleted` 를 정확히 센다) — `KubernetesClient.list_vcjob_briefs`(Terminating 표시·uid, CRD 404 는 빈 목록이 아니라
  예외)와 `list_pod_briefs` 의 `deleting`. (2) 틱 순서는
  끝난 purge 파드 수거·실패 귀속 → 행 단계 → 새 파드·완료 판정(반대면 pending_trash 가 풀린 새 사본을 「파드가 못 지운 것」으로
  오인 — 테스트가 잡음). (3) 새 사유 `purge_row_invalid`(깨진 아웃박스 행 — 진행하면 위험)와 경고 `artifact_reappeared`(떼어낸 뒤
  base 에 다시 생기면 k8s 단계부터). (4) purge 파드 Pending 600초·이미지 pull 실패 등은 즉시 실패로 접는다(activeDeadlineSeconds
  는 스케줄 전 파드엔 발화하지 않는다). (5) `finish` 는 원 행 재출현을 트랜잭션 안에서 다시 보고, 같은 id 재삭제(DB 복원 뒤)는
  아웃박스 행을 갱신(잡 열쇠 합집합 — PK 충돌 500 대신). (6) planner 경합 테스트는 `tests/test_planner.py` 에 둔다.
- **검증 지적 반영(1차, 2026-10-09)** — 13건(fs-k8s 2·db-api 2·ui 9): (a) purge 스크립트가 `set -e` 로 첫 rm 실패에서
  멈춰 정렬상 뒤 이름이 시도조차 안 되고, FS 기반 귀속이 같은 파드의 무고한 행까지 같은 백오프로 영영 묶었다 → 이름마다 끝까지
  시도 + 마지막 exit 1, `purge_pod_failed` 행은 다음부터 혼자 실림(실 sh 로 재현·고정). (b) k8s 스윕이 결정적 이름·ref 이름
  (job_id[:12])으로 지워, 접두가 같은 변조·복원 아웃박스 행이 살아 있는 다른 잡의 launcher·worker·vcjob 을 죽일 수 있었다
  (target_still_present 는 전체 id 비교) → 전체 id 확인(라벨, launcher 는 소유 vcjob 의 라벨·uid — 파드 나열 **뒤** 조회라
  살아 있는 주인을 놓치지 않는다), 이름으로는 아무것도 지우지 않음. DB 쪽 접두 가드(`substr(job_id,1,12)`)는 넣지 않았다
  — 인덱스를 못 타는 전체 스캔이 행·틱마다 돌고, k8s 확인이 이미 정확하다. (c) `batch_items.request_id` 인덱스 누락(게이트가
  행 잠금을 쥔 채 요청마다 전체 스캔) → `idx_batch_items_request`. (d) 확인 창 사용량 경고가 요청 **상태**로 판정해 취소 경합의
  「요청 Cancelled · scan 잡 Succeeded」(사용량 분석은 잡 상태를 본다)를 놓쳤다 → 목록 API 에 `has_succeeded_scan`
  (`data_jobs.usage_scan_request_ids` — 사용량 분석과 같은 조건). (e) 포탈: 진행 중 주 버튼 disabled 로 포커스가 <body> 로
  떨어짐 → aria-disabled + 가드, 성공해 닫히면 트리거가 잠겨 <body> → 툴바 결과 줄(tabIndex -1)로 / 같은 틱 두 번 클릭이 POST
  2회 + 「0개 삭제됨」 → 동기 ref 가드 / 새 제출에 밀려 선택이 말없이 줄던 것 → 「N개는 목록에서 사라져 선택에서 뺐습니다」 /
  선택 불가 사유가 hover title 뿐 → 미선택 안내가 두 규칙을 다 말함(ink/70 — text-muted 3.54:1 대비 미달도 함께) / 폴링 오류
  복구 뒤 전체 선택 칸 indeterminate 소실 → 콜백 ref / 한국어 낱말 중간 줄바꿈 → break-keep, 지연 사유는 바 아래 한 줄
  (넓은 화면에서 툴바가 두 줄로 접히던 것) / `maintenance_mode` 문구가 「새 작업 제출」만 말함 → 「작업 제출·삭제·빌드 같은
  변경」. CLAUDE.md 의 공유 토큰 목록·참조 테이블 규칙 한 줄은 서브에이전트가 고칠 수 없는 파일이라 오케스트레이터 몫으로 남겼다.
- **검증 지적 반영(2차, 2026-10-09)** — 10건(fs-k8s 2·db-api 2·ui 6): (a) 끝나지 않는 purge 파드(죽은 노드의 Running·
  Terminating 고착, D 상태 rm 으로 데드라인 미집행)가 「진행 중」으로만 보여 그 뒤의 모든 정리가 last_error 없이(stalled 0·
  이벤트 0) 멈췄다 → `list_purge_pods` 가 모든 파드의 나이를 보고 데드라인 + 유예(3600 + 600초)를 넘긴 미종료 파드를 stuck
  으로, 그 뒤에 막힌 행(trash 항목이 있는 due 행)을 새 사유 `purge_pod_stuck` 으로 defer(이벤트 행마다 1회). 파드는 지우지도
  하나 더 띄우지도 않는다(운영자가 노드 확인 후 force — runbook 행 추가), 파드가 사라지면 표시를 거둔다. (b) `due()` 가 삭제 순이라
  k8s 단계에서 오래 기다리는 행 20개가 매 틱 상한을 채워 새 삭제가 시작조차 못 했다 → 재시도 시각 순 + 지연이 된 대기 행의
  재확인 간격 = 기다린 시간의 1/10(상한 300초). (c) 잠금 없이 상태를 읽은 종단 전이(finalize_from_job·set_state_with_result)가
  삭제 커밋 뒤에 쓰면 0행 UPDATE 위에 전이·results 가 커밋돼 영구 고아(지표 plan_rejected 가 읽음)가 됐다(조용한 창이 유일한
  가드, 창 0 이면 무작위 경합 3,664회 중 66건) → `_apply_state`·`set_job_state` 가 PG 행 잠금 + UPDATE 영향 행 수 확인(0 이면
  KeyError 로 전부 롤백), `finish` 가 results 도 scrub. (d) 일괄 삭제 중 한 항목의 DB 오류가 응답 전체를 500 으로 만들어 이미
  지운 항목을 숨기고 뒤 항목을 건너뛰었다 → 그 항목만 새 사유 `request_delete_failed` 로 제외하고 계속, 깊게 중첩된 변조 JSON 의
  RecursionError 도 깨진 JSON 으로. (e) 포탈: 부분 성공 때 목록 재조회 렌더가 onDeleted 의 선택 비우기를 덮어 결과 요약 대신
  「2개 선택됨 · 1개는 목록에서 사라져…」가 뜨고 제외된 행이 체크된 채 남았다(실 브라우저에서만 재현) → 유령 정리 함수형 갱신 +
  삭제 결과가 떠 있는 동안 「사라짐」을 세지 않음(e2e 로 고정 — jsdom 은 재현 못 함) / 414~768px 에서 긴 미선택 안내가 두 줄로
  접혀 첫 체크에 툴바가 104→56px 로 줄며 표가 튐 → 상태 줄은 lg 미만에서 늘 자기 행 + 두 줄 높이 예약, 선택 규칙은 늘 있는 별도
  줄 / 배치 자식 안내가 없는 삭제 경로(「배치 화면에서 관리」)를 가리킴 → 「배치를 지워도 작업 기록은 남습니다」(배치 삭제 창도
  같은 사실을 말함) / 삭제 POST 진행 표시 없음 → 「삭제 중…」(버튼·role=status) + 창 aria-busy / 문구: 조용한 창 숫자 제거(「잠시
  뒤에」), 「실패 로그 박제」→「실패 시 보관한 로그」, 툴바 이름 「작업 일괄 처리」 / 375px 에서 대상 열이 두 글자 폭 → 최소
  8rem(넘치면 표 가로 스크롤). CLAUDE.md 두 줄(공유 토큰 목록에 작업 삭제, 요청·잡 참조 테이블은 삭제 목록에도)은 이번에도
  서브에이전트 권한 밖이라 적용할 패치만 준비했다.
- **검증 지적 반영(3차, 2026-10-09)** — 4건(backend 2·ui 2): (a) 파드 수준 실패(레지스트리 순단의 ImagePullBackOff·
  Pending 상한·생성 실패)가 실린 행 전부를 `purge_pod_failed` 로 몰고, 1차의 「실패 행은 혼자」 규칙이 그 행들을 한 행씩
  직렬로 되돌렸다(20행 = 파드 21개·42틱, 새 삭제도 그 뒤에 섬) → 귀속을 둘로: 근거가 있을 때만 새 사유 `purge_entry_failed`
  (끝난 상태로 끝난 파드에서 마지막으로 지워진 이름보다 앞에 남은 이름 + 그 바로 뒤 첫 남은 이름 — 스크립트가 순서대로
  지우고 실패해도 계속하므로, 끊긴 파드의 시도조차 못 한 뒷이름은 근거가 아니다), 그 밖은 `purge_pod_failed` 로 표시·이벤트만
  남기고 계속 묶어 싣는다. 혼자 갈 행은 묶을 행이 없을 때 가고(맨 앞이라고 새 삭제 묶음을 세우지 않는다) due 5분이 넘으면
  먼저(굶김 상한), 혼자 간 파드에서 또 남으면 혼자 남는다. runbook 의 `purge_pod_failed` 행(「다른 요청은 끝나고 이 요청만
  혼자」가 이미지 pull·Pending 에선 거짓)을 고치고 `purge_entry_failed` 행 추가, BACKLOG 의 거대 트리 항목에 「정렬상 첫
  이름이면 근거가 없다」 한계를 적었다. (b) 일괄 삭제 응답의 정리 대기 건수(`pending_count`)가 항목별 격리 밖이라 DB 가
  도중에 끊기면(재연결 실패) 이미 지운 항목을 500 뒤에 숨겼다 → 세지 못하면 `purge_pending: null`(모름 — 포탈은 이 값을
  읽지 않는다, 타입 `number | null`). (c) 포탈: 360~393px 에서 삭제마다 나타나는 「결과 파일·파드 정리 중 N건」이 버튼 행에
  끼어 「선택 해제」가 다음 줄로 밀려 툴바가 128↔174px 를 오가며 표가 손가락 밑에서 튐 → 정리 자리를 늘 렌더(lg 미만은
  맨 아래 자기 행·한 줄 예약, lg 이상은 버튼 앞) + 두 버튼을 줄바꿈 없는 한 덩어리로(320~768px 툴바 152px 고정, lg 이상
  80px 고정 — 드문 「정리 지연: 사유」 줄만 알림으로 늘어난다). (d) 창이 열린 동안 폴링이 선택한 행을 모두 목록에서 지워
  「선택 삭제」가 잠긴 뒤 닫기·Esc 로 닫으면 포커스가 <body> 로 떨어짐 → 트리거가 잠겨 있으면 성공 때처럼 툴바 결과 줄로.
- 남은 일(BACKLOG): 보존 정책(자동 만료), 단건 삭제 버튼, 삭제된 배치의 자식, 지표 박제, 정리 이벤트 뷰어, pod-gc 200건 창 고착,
  Aborted vcjob TTL 비회수, 빌드 삭제 docstring 거짓 전제, pod-gc·취소의 ref 미검증 terminate, 요청 삭제 잔여 위험(노드 분할 +
  force 삭제 → 행 없는 디렉터리 등).

테스트: 백엔드 전체 2858 passed(새 파일 9개 241건 — 라우트·원자성·전수 열거 그물·trash FS·purge 파드 계약·실 sh 스크립트·
k8s 스윕·루프·stepper 삭제 경합, 그리고 planner 경합·digest·base 잠금·설정·배선·마이그레이션 계약 확장; `test_rollout_runner`
의 파드 흉내에 `deletion_timestamp=None` 보강 — 실 V1ObjectMeta 는 늘 가진 속성), 프런트 vitest 1136
(알려진 부하 플레이크 router 로그아웃 1건은 단독 재실행 통과) + tsc + 빌드(`dist/index.html` 외부 URL 0), e2e 11 passed(새 E7 2건 —
UI 삭제 → 행·상세 소멸 → 정리 대기 0 수렴, 공유 토큰 403·무변화). 검증 지적 반영(1차) 뒤: 백엔드 2866 passed(실 sh
스크립트의 실패 이후 계속·혼자 싣기·접두 형제 잡 무접촉·소유 uid 경합·목록 has_succeeded_scan 회귀 테스트), vitest 1141
(포커스·같은 틱 두 번 클릭·선택 빠짐 알림·indeterminate 복구·잡 상태 기준 경고), 빌드 외부 URL 0, e2e 11 passed(E7 에
「삭제 후 포커스 = 결과 줄」 실 브라우저 단언 추가). 검증자 탐침 3건(무고한 행 정체·접두 형제 파드·접두 형제 vcjob)이
이제 재현되지 않는다. 검증 지적 반영(2차) 뒤: 백엔드 2877 passed(stuck 파드 표면화·굶김·재시도 순서·대기 간격 상한·경합 상태
쓰기 롤백·PG 행 잠금 문장·finish 의 results scrub·항목별 DB 오류·깊은 중첩 JSON — 각각 옛 코드에서 빨간불 확인), vitest
1145(진행 표시·문구·상태 줄 구조), 빌드 외부 URL 0, e2e 13 passed(E7 에 부분 성공 경합·툴바 높이(414·768·1280)·375 대상 열
— 옛 코드에서 둘 다 검증자 증상 그대로 실패). 검증자 PG 탐침: 종단 전이↔삭제 경합 결정적 재현 PASS(results 0), 무작위
스트레스 60초(조용한 창 0) 3,291건 삭제에 고아 0(이전 results 66건). 검증 지적 반영(3차) 뒤: 백엔드 2882 passed(파드 수준
실패의 묶음 유지·끊긴 파드의 귀속·근거 판정 단위·혼자 갈 행의 굶김 상한·DB 장애 중 응답 200), vitest 1148(잠긴 트리거의 포커스
복귀 2건 — 옛 코드에서 빨간불 확인, 정리 자리 구조), 빌드 외부 URL 0, e2e 14 passed(E7 에 정리 대기 0·1·200건 툴바 높이
(360·375·393·1280)·두 버튼 한 행). 검증자 탐침 2건(직렬 회복 — 이제 파드 1개·2틱, 장애 중 500 — 이제 200)이 재현되지 않는다.

화면 다듬기(마지막 검증 낮음·미세): 정리 상태 줄을 모든 폭에서 자기 행(1024~1150px 에서 선택 시 툴바가 두 행으로 접혀 28px
튀던 것), 제외 사유는 사유별 한 줄(「사유 (N개: 앞 3개 id 외 K개)」 — 대부분 제외되면 표가 한두 화면 밀렸다), 하나도 안 지워지면
「삭제된 작업 없음 · N개 제외」, 확인 창 진행 문구는 보조기기 전용(「삭제 중…」 두 번 보이던 것)·미리보기 목록 높이 상한(375px 에서
경고 문구가 화면 밖). 최종: 백엔드 2882, vitest 1150(router 플레이크 1건 단독 재실행 통과), 빌드 외부 URL 0, e2e 14 passed.

배포: d169(빌드 / 커밋 359a856) — dms 이미지만(정리 파드는 기존 잡 이미지 사용). 릴리스 dms-api·dms-controller(migrate
initContainer 가 request_purges 테이블·인덱스 생성), 오버레이 dms newTag d169(가드 통과).

실증(테스트베드, 일회용 요청만 — 기존 데이터 무접촉):
- 사전: 결과 폴더 `<job_id>` 173개 전부 0:0 755, `.dms-trash` 없음, k8s 기준선(파드 105·vcjob 56) 기록.
- 케이스: A scan(mason → 실행 신원 alice, 비 root — phase 가 alice 10001 소유), B scan(root, 전부 0:0), C sync(alice, 미리보기 →
  컨펌 → 성공, preview·execution 둘 다 alice 소유), D rm(미리보기 후 취소), E sync(목적지 부모 쓰기 불가로 거부 — 결과 폴더 없음,
  Error preflight 파드), F rm(ConfirmPending — 진행 중). 일반 사용자의 scan·rm 은 원래대로 403 operation_admin_only.
- 권한: 공유 Bearer 토큰 → 403 `admin_session_required`, alice 세션 → 403 `admin_required`, 행 무변화.
- 실 Chrome(/jobs, mason): F·기존 배치 항목 체크박스 disabled + 이유 title, 「5개 선택됨(성공 scan 2개)」, 확인 창(5개, sync 2 ·
  scan 2 · rm 1, 성공 scan 2개 사용량 경고, 스토리지 데이터 무접촉, 되돌릴 수 없음 확인) → POST 200 deleted 5·skipped 0 → 「5개
  삭제됨」, 5행 목록에서 사라짐, 지운 요청 상세는 「요청을 찾을 수 없습니다 — 삭제됐거나 볼 수 없는 요청입니다」.
- 커밋 직후 DB(api 파드 읽기 전용 SELECT): 5건 모두 requests·results·plans·data_jobs·events·전이·digest 0, audit_log
  ('delete') 1건씩(actor mason, 스냅숏에 요청 payload·run_as_root·잡별 identity(username·uid·gid·privileged)·전이 actor).
- 수렴(1~2분): request-purges pending 0, 지운 잡 `<job_id>` 와 `.dms-trash/<job_id>` 전부 부재(alice 소유 phase 포함),
  `.dms-trash` root 0700 비어 있음, 지운 잡의 preflight 파드·vcjob·Error 파드 0, purge 파드 0. `request_purged` 이벤트 5건
  (request_id NULL, outcomes deleted 4·absent 1(E), k8s_deleted 3·3·6·1·0). 사용량: `dms_test/src` scan 5 → 3건, 최신이 이전 scan.
- 정지 창: F 를 취소 직후 삭제 → skipped `request_recently_finished`, 70초 뒤 → deleted, 수렴 후 결과 폴더 수 173(기준선).
- 무관 객체 무접촉: 기준선 대비 사라진 다른 잡의 vcjob·launcher 는 Volcano TTL(86400s) 회수, api·controller·빌드 파드는 롤아웃·
  빌드 정리 — 지운 잡 외 요청(예: 92ce91b8)과 그 결과 폴더는 그대로.

### ✅ 릴리스 태그 목록 결함 6건(레지스트리 404·tags null·페이지 나눔·익명 토큰·새로고침·순서) — **완료·실증**(2026-10-08, d168)

사용자 보고: "프로덕션에서 포탈에서 이미지 빌드했는데 릴리즈에 해당 이미지 버전이 목록에서 안보여" → 점검(3갈래 조사 +
가짜 레지스트리 재현) 중 사용자가 다시 시행해 정상 확인 — "기존 소스 그대로, 함께 확인된 결함 중 R2·3·4·5·8·10 만 해결,
HTTP 관련은 건드리지 마". 그래서 조회 주소(`http://{registry}/v2/…`)·리다이렉트·프록시·타임아웃은 그대로다.
- **R2 레지스트리가 아닌 404**(`registry.py`): 예전엔 모든 404 를 「리포 없음 = 태그 0개」로 접어, 웹서버·ingress 기본
  백엔드의 404 면 경고 없이 빈 목록 + 실제로 있는 태그 제출이 422 `unknown_tag` 였다. 이제 []는 레지스트리 자신의 404
  (`Docker-Distribution-Api-Version` 헤더 또는 `NAME_UNKNOWN`/`NOT_FOUND` 오류 본문)일 때만, 그 밖은 응답 불가(None, 로그).
- **R10 `{"tags": null}`**: 태그를 전부 지운 리포에 v2 가 주는 응답 — 응답 불가(화면 전체 빨간 문구)가 아니라 태그 0개.
- **R5 페이지 나눔**: `Link: <…>; rel="next"` 를 같은 레지스트리(스킴·호스트·포트) 안에서만 상한 50쪽까지 따라간다.
  상한·다른 호스트 다음 주소면 `TagList.truncated` — 잘린 목록에 없는 태그는 "없다"가 아니라 검증 안 됨(제출 202 +
  `tag_verified:false`, `routes_releases._unverifiable`), 화면 행에 「목록 일부만 보입니다」.
- **R8 토큰 인증**: 401 + `WWW-Authenticate: Bearer realm=…` 면 그 realm 에서 **익명** 토큰을 받아 한 번 재시도(태그 조회·
  digest·삭제 공통 `_send`). 자격 증명은 다루지 않는다(Basic 은 그대로 실패), 토큰은 로그에 남기지 않는다. realm 은 레지스트리와
  **같은 호스트**일 때만 따른다(커밋 보안 검토 SSRF 지적 반영 — 응답이 정한 주소로 api 파드가 요청을 보내므로 평문 HTTP 중간자가
  realm 을 메타데이터·내부 서비스로 바꿀 수 있었다). 다른 호스트의 토큰 서버는 BACKLOG.
- **R4 순서**: 문자열 정렬이라 `d100` 이 `d98` 앞, 자동 태그(`b`+8자리)와 섞여 새 태그가 목록 중간에 묻혔다. 이제 태그 목록
  응답이 이 리포를 push 한 최근 성공 빌드의 태그를 맨 위(빌드 완료 최신 먼저, `built` 에 완료 시각 — 레지스트리에 실제로 있는
  태그만)에, 나머지는 숫자를 고려한 내림차순으로 준다. 드롭다운은 「d167 · 최근 빌드 10-08 13:42」(KST).
- **R3 새로고침**: 릴리스 화면이 빌드 전에 읽은 목록을 들고 있어 새로고침 전까지 새 태그가 안 보였다. 빌드가 성공하는 순간
  (`useBuilds` 목록·`useBuild` 상세) 태그 목록 쿼리 무효화, 창 포커스 재조회(30초 넘게 묵었을 때만 — 비싼 조회), 화면 진입은
  늘 재조회, 「태그 목록 새로고침」 버튼.
- 백엔드·API 계약: 태그 목록 응답 행에 `built`·`tags_truncated` 추가(옵셔널, 구 화면 호환), 사유 코드·스키마 변경 없음.
- **검증 후속**(실제 로컬 레지스트리·토큰 서버 재현): 페이지 루프에 같은 주소 반복 감지·둘째 페이지부터 시간 예산(8s)·받은
  토큰 재사용(페이지마다 401·발급 왕복이 곱해져 최악 수십 분이던 것), 둘째 페이지 이후 실패·읽을 수 없는/다른 호스트 next 는
  읽은 목록을 버리지 않고 잘림, realm 의 기존 query 보존, 챌린지 파싱 확장(합쳐진 헤더·따옴표 없는 값·대문자·탭), 헤더에 실을 수
  없는 토큰 거부(예외 문구로 토큰이 새지 않게), 숫자 정렬 키가 유니코드 숫자·4300자 넘는 숫자에서 500 내던 것(ASCII 숫자만,
  길이→글자 비교), 잘린 목록이어도 최근 빌드 태그는 드롭다운에 남김, `release_tag_unverified` 이벤트에 사유(registry silent /
  tag list truncated), 화면은 `built` 모양이 어긋나도 죽지 않음.

테스트: 백엔드 레지스트리·릴리스 관련 99 passed(test_registry 를 `_request` 심 기반으로 다시 씀 — 404 판별·tags null·페이지
나눔·상한·시간 예산·루프·다른 호스트·깨진 Link·익명 토큰·토큰 재사용·realm query·챌린지 변형·위험 토큰·Basic·토큰 비로깅·
digest/삭제 토큰, 순서·built·잘림 제출·이벤트 사유·정렬 키), 프런트 1111 passed + tsc + 빌드(외부 URL 0), e2e 9 passed.

배포: d168(빌드 / 커밋 40887d7 — d167 은 SSRF 보완 전이라 릴리스하지 않음), dms 이미지만, 오버레이 dms newTag d168(가드 통과).
실증(테스트베드, 평문 HTTP 레지스트리 pkg-01:5000 — 동작 불변 확인): 태그 목록 응답 registry_ok true, dms 행 맨 위 d168·d167·d166…
(built 에 빌드 완료 시각), dms-agent·job-image 는 그 리포를 push 한 빌드만. 실 Chrome 릴리스 화면 드롭다운 「d168 (현재) · 최근
빌드 10-08 22:10」·「d167 · 최근 빌드 10-08 22:06」…, 「태그 목록 새로고침」 → 태그 목록 요청 1건, 콘솔 오류 0.

### ✅ 요청 상세: 요청 내용 카드(옵션·실행 권한·실행 신원·자원) + root 머리 배지 — **완료·실증**(2026-10-08, d166)

사용자 요청: "작업에 대한 결과를 웹에서 보는데, 대상, 절대경로, 요청자, 제출대기, 수행시간 등은 있는데 더 자세한 요청
옵션들이나 root 권한 실행 등에 대한 내용이 전부 빠져 있어. 채워 넣고 UI 직관적이고 심플하게 개선해." 프런트만(백엔드·API·
reasonCodes 무변경).
- **배치**: 결과 배너 바로 아래 「요청 내용」 카드 한 장(region, 접지 않음) — h3 두 묶음 「대상·옵션」(대상·절대경로·옵션·
  우선순위·노드 지정)과 「실행 권한·자원」(실행 권한·실행 신원·보조 그룹(gid)·목적지 소유·실행 도구 + 실행 노드). 2열은
  xl(1280) 이상 — 사이드바가 폭을 먹는 1024–1279 에서 2열이면 값 칸이 768 의 1열보다 좁았다. root 일 때만 머리말 h1 **밖**에
  빨간 배지 「root(특권) 실행」(계획 스냅숏 privileged)/「root(특권) 요청」(계획 전·거부) — 본문 첫 줄이라 배너·KPI 아래
  카드보다 먼저 보인다. 비 root 는 배지 없이 카드의 「실행 권한」이 「root 아님 — 실행 신원의 uid/gid(권한 그대로 적용)」로
  확정한다(화면에 「root」라는 글자가 없으면 관리자가 남의 신원으로 낸 요청 — 2026-09-30 사고 모양 — 을 문장에서 추론해야 했다).
- **판정은 순수 모듈** `frontend/src/features/jobs/requestSpec.ts`(카드와 배지가 한 모델을 읽는다): root 의 진실은
  `worker_pool.identity.privileged`, 계획 전엔 요청 의도(`identity.privilege_policy` 미러 — `run_as_root === true`·배치
  자식(`batch_id` 파이썬 truthy)·그 밖 비 root, 공유 토큰 배치 자식은 계획 전에도 비 root 확정), uid 0 은 근거로 쓰지 않는다.
  실행되지 않은 root 요청은 「요청됨」 시제이고 「목적지 소유」를 단정하지 않는다(작업 없이 끝난 요청도). 계획과 의도가 어긋나면
  설명 줄 — `run_as_root` 없이 root 로 계획된 단건은 2026-09-30 규칙 변경 전 관리자 요청(정상 기록)일 수 있어 비정상이라
  단정하지 않고, 「서버가 거부」는 아직 끝나지 않은 작업에만(stepper 재확인). 옵션은 domain `_OPTION_SPECS` 카탈로그 순서로
  칩 + 출처 있는 설명(설명 없는 칩은 한 줄에 이어서), 서버 기본과 같은 값은 「기본값: …」 한 줄, 모양이 틀린 값·카탈로그 밖
  키는 원문(60자, 12개 + 「외 N개」), rm recursive true 는 생략. 우선순위는 원문, 정책 상한으로 깎였을 때만 「high 요청 → mid
  적용(정책 상한)」. 배치 자식의 노드 지정은 nsync 면 「출발·목적지 각각」(resolve_fanout 면당 캡 — 총 최대 2배). 실행 도구는
  잡이 하나면 카드에(프로세스 수·노드당·실행 노드), 여럿이면 잡 카드마다. 경로 칸은 출발·도착 조각 사이에서만 줄을 바꾼다
  (`storagePaths.pathParts/absParts` — 경로 이름의 「 → 」·줄바꿈에서 갈리지 않고, 문자열이 아닌 값은 「?」). 전부 DB 신뢰
  경계 방어(변조 payload·worker_pool·options 에서 죽지 않음, null≠0).
- **옮김·제거(한 사실은 한 곳)**: 머리말 「우선순위 mid」 → 카드, 배너의 대상·절대경로 → 카드(배너 dl 은 사유만), 잡 카드의
  보조 그룹 행 → 카드(root 는 행 없음), 잡이 하나일 때 잡 카드 도구 라벨·「데이터 작업 1개」 개수 제거, 아티팩트 줄은 잡 카드
  맨 아래 「상태 전이 N건」 옆, 성공 부제는 「{KST} 완료」만. 제출 폼과 같은 문구를 쓰도록 `optionRules.ts` 에 상수 추출
  (SYNC_OPTION_HELP·OPEN_NOATIME_NON_ROOT·PRIV_*, 바이트 동일). managed_root 가 관리자 응답에만 있다던 낡은 주석 정정.
- **같은 사실 한 번만**(검증 2차 반영): root 실행 신원 꼬리는 남의 신원을 지정했을 때만 「이름만 기록되고 권한은 root」,
  비 root 자동 chown 의 목적지 소유는 이름을 되풀이하지 않고 「실행 신원 소유 — uid:gid(주 그룹)」, 기본 bufsize 도 사람 단위,
  「·」는 앞 글자에 묶여 줄 첫머리로 가지 않음, 라벨 칸 6rem(경로가 이름 중간에서 덜 갈림), 아티팩트 줄 앞 구분선. 서버가
  root 실행을 막은 옛 작업(privilege_not_requested)은 「root 실행」 배지 없이 「root(특권)로 계획됨 — … 서버가 실행을
  중단했습니다」, 단건인데 작업 기록을 못 읽으면 「root 아님」 대신 「root 지정 없음 — … 확인하지 못했습니다」(2026-09-30 전
  관리자 행은 지정 없이 root 였다), 비 root 에 실린 open_noatime 은 상세 전용 문구(EPERM 으로 실패할 수 있음).
- 표시하지 않음: 노드 탈락 사유·큐·우선순위 클래스 원문·LDAP 그룹 이름(BACKLOG).
- 테스트: vitest 1106(신규 93 — `requestSpec.test.ts` 71·`RequestSpecCard.test.tsx` 19·`storagePaths.test.ts` 2·
  `requestOutcome.test.ts` 1, 기존 단언 2건은 위치 변경으로 갱신), e2e 9 passed(E4 에 배지·region 단언 추가), 빌드 산출물
  외부 URL 0.

배포: d166(빌드 / 커밋 fe0aefd) — dms 이미지만. 릴리스 dms-api·dms-controller, 오버레이 dms newTag d166(가드 통과).

실증(테스트베드 실데이터, 실 Chrome 1440/1280/375 — 전 화면 가로 넘침 0·콘솔 오류 0·외부 요청 0·4xx/5xx 0):
- alice 본인 sync: 배지 없음, 「root 아님 — 실행 신원의 uid/gid(권한 그대로 적용)」, 「alice (요청자 본인) · uid 10001 · gid
  10000(주 그룹)」, 보조 그룹 10010 + 주의문, 목적지 소유 「실행 신원 소유 — 10001:10000(주 그룹)」, 옵션 「지정한 옵션 없음」 +
  「기본값: batch_files=1000000 · bufsize=4194304(4.0 MiB)」, 「dsync · 4 노드 · 프로세스 8개(노드당 2)」 + 실행 노드 4대.
- mason root scan: 머리말 「root(특권) 실행」, 「root(특권) — 권한 검사 우회」, 「mason (요청자 본인) · uid 0 · gid 0」, 보조 그룹 행 없음.
- mason → alice 신원 scan(비 root): 배지 없음, 「alice (관리자가 지정한 다른 사용자) · uid 10001 · gid 10000(주 그룹)」.
- 배치 자식 sync: 「root(특권) 실행」 + 「배치 항목 — 배치를 만든 관리자의 root 자격이 적용됐습니다.」, 목적지 소유 「소스의
  소유자·그룹 그대로(root 실행)」. root rm: 빨간 「삭제가 root 권한으로 수행됩니다」.

### ✅ 요청 상세: 단계 분리(사전 점검·미리보기 / 실행) + 결과 배너 — **완료·실증**(2026-10-08, d164·d165)

사용자 요청: "요청 결과 화면 아래쪽의 preflight(preview)와 execution 의 output/log 를 분리해서 보이게 + 결과 화면 개선".
세 안(CI 파이프라인형·진단 우선형·차분한 탭형) → 두 심사 모두 진단 우선형 1위 → 단일 구현 스펙으로 합쳐 구현.
- **배치**(`/jobs/:requestId`): 머리말(h1 `{operation} 요청`·id·요청자·제출·우선순위·배치 링크·「자동 갱신 중」) → 결과 배너
  (`OutcomeCard` — 제목·요청 pill / 사유·대상·절대경로 / 「다음 할 일」 + CTA / KPI 4칸) → 잡 카드(① 사전 점검·미리보기 →
  작업 컨펌 관문 줄 → ② 실행, scan 은 ① → 연결 문구 → ②) → 활동(전이 이력·진단 이벤트, xl 2열). 옛 「요청 정보」 카드 대체.
- **판정은 순수 모듈 둘**: `stageModel.ts`(단계 상태·시각·실패 지점 — 제출 접두 → 사유 접두 → 단계 사이 관문(fail-closed) →
  마지막 종단 전이 from_state → 가장 깊은 ref → 흐름 첫 단계, 뒤 둘은 "위치 추정"이라 단정 안 함), `requestOutcome.ts`(배너 문장·
  초점 잡·KPI). 미리보기 실패의 result_summary 는 미리보기 summary 라(stepper `_surface_failed_artifact`) ①의 「미리보기 결과
  (실패 시점)」으로만 그린다. 처음 이름 `jobStages.ts` 는 컴포넌트 `JobStages.tsx` 와 대소문자만 달라 대소문자 무시 파일시스템에서
  import 가 엉뚱하게 풀려 리뷰에서 바꿨다.
- **리뷰 반영**(같은 날, 적대적 리뷰 확정 10건): stepper 의 단계 사이 관문(`_build_spec` 제출 전 검사 — 신원 변경·LDAP 보류 소진·
  root 근거·chown·스토리지·base 정적 관문·노드 제외)이 끊은 잡은 마지막 전이 from_state 가 **이미 통과한** 단계라, 그 단계를 실패로
  칠하고 그 로그를 자동으로 열던 것을 다음 단계 「시작 전」(사유·진단 이벤트 안내, 로그 없음)으로 고쳤다. 노드 제외는 앞 파드 스케줄
  대기 재검사와 구별이 안 돼 "위치 추정"(다음 제출 보류 기록이 있으면 확정). 컨펌 직후 재점검 파드 전 종단은 「소요」 없이 시작 전,
  실행 vcjob 큐 대기(`exec_submitted_at` 있고 `sched_wait_seconds` 없음)는 「진행 중」이 아니라 「스케줄 대기 중」이고 그때 관문에
  끊기면 "이미 복사/삭제됐을 수 있다" 경고를 하지 않는다. LDAP 재확인 보류(`identity_recheck_deferred`) 중엔 끝난 단계를 진행 중으로
  그리지 않고 「제출 보류 — LDAP 재확인 불가 n/max」. 그 밖: 진행 중 로그가 단계 끝에 마지막 한 번 더 읽힘(꼬리·박제 사본), 라이브
  로그·아티팩트 목록 재조회 실패가 보던 내용을 지우지 않음, 잡 첫 조회 실패 때 2초마다 화면 전체가 골격으로 흔들리던 것(데이터 없는
  실패는 폴링 중지), 실패 잡이 배너 초점일 때 다른 잡의 「작업 컨펌」이 사라지던 것, 흐린 상태 배지 글자 대비(ink/70).
- **리뷰 2~4차**(3관점 검증 × 2라운드 + 단독 반박): base 권한 사유(`artifact_base_not_traversable`·`_group_writable`)는
  사전 점검·재점검 **파드 마커**이기도 해, 관문 이벤트(`artifact_base_unsafe_at_step`)나 실행 상태 Failed 일 때만 관문으로
  읽는다 — 그룹 없는 잡은 마커 확정(그 파드 로그 자동 열림), 그룹 잡은 같은 응답의 요청이 종단이고 그 잡의
  `identity_groups_checked(preflight)` 가 있으면 확정(그보다 새 관문 이벤트는 보존 삭제·100건 창에서 먼저 지워질 수
  없다), 아니면 "위치 추정". LDAP 제출 보류 중 취소는 다음 단계 시작 전 취소, 보류가 풀린 뒤에도 통과한 파드 단계 소요에
  보류 시간을 섞지 않음, 다음 단계 ref 가 남은 Preflight→취소는 그 단계가 제출된 것. 「작업 컨펌」 창은 늘 각 잡의 관문
  줄에(폴링으로 배너 초점이 바뀌어도 열린 창 유지) — 배너엔 「컨펌하러 가기」(스크롤 + 포커스). 자동 열림은 사용자가 손대기
  전까지 모델을 따름(실시간 틈에 잘못 연 뷰어 정리). 첫 조회(데이터 없음) 진행 중 상태가 바뀌면 그 요청에 합류하지 않고
  취소 후 다시 읽음(TanStack 5 의 cancelRefetch 는 데이터가 있을 때만). 잡 조회 오류 캐시로 재진입하면 붉은 경보 대신 중립
  골격. 의미 글자 대비 text-muted·ink/60 → ink/70(칩 크기·도구 라벨·보조 그룹 행·실행 결과 키·ConfirmDialog 본문).
- **출력**: 단계 행마다 칩(로그 + 그 phase 파일) → 구획 안 인라인 뷰어(구획마다 선택 하나 — 미리보기·실행 stdout 동시 비교).
  뷰어: CSS counter 줄번호·줄바꿈 토글(localStorage, try/catch)·2,000줄 상한·꼬리부터 열기·진행 중 로그 3s 라이브 + 바닥 붙기·
  「로그 저장」(Blob, clipboard 아님)·JSON 정렬. 종단 실패는 실패 단계 로그 1건만 자동으로 연다(사용자가 고르거나 닫으면 끝).
  phase 범위 진단 이벤트(payload job_id·phase)는 단계 행 주석으로(행마다 3건). 칩 접근성 이름은 옛 탭 이름 그대로.
- **결함 수리**: TimedOut 이 잡 종단 집합에 없어(폴링 무한·「취소」 잔존) 추가, 요청 상세 3s 폴링(요청 pill·전이 이력이 낡았다),
  잡 0개 + 요청 비종단에서도 잡 폴링(`[].some()` 함정 — 제출 직후 들어온 상세가 영영 안 갱신), 상태 전이 때 아티팩트 목록 재조회
  (`refreshKey` + keepPreviousData), 목록 실패가 로그 칩을 가리지 않음, 재조회 실패는 화면을 지우지 않고 띠만, transitions·
  phase_refs·events 방어 정규화, 375 에서 32자 id `overflow-wrap:anywhere`.
- **뺀 것**: 「완료됐지만 0건」 주의 — 미리보기·실행 summary 의 files 가 같은 뜻이 아니다(dsync dry-run 훑은 수 / nsync 계획 변경 수 /
  실행 처리 수) — 비교하면 거짓 경보. 범위 밖 후보는 BACKLOG §1.
- 새 의존성·색 토큰·외부 URL 0, 공용 Button·StatusPill·tailwind 설정 무변경(ConfirmDialog 는 본문 글자 대비만), 백엔드 무변경.
  BACKLOG 에 앱 전역 의미 글자 대비, 요청 취소 ↔ planner 경합(검증 필요 — 리뷰 중 코드 읽기로 발견).

- **실화면 다듬기**(d164 실 Chrome 에서 발견 → d165): 결과 타일 bytes 원값 괄호는 1 KiB 이상에서만(「10 B (10 B)」 중복),
  컨펌 대기 중 수행시간 보조 줄은 「진행 중」이 아니라 「컨펌 대기 중 (대기 시간 포함)」.

배포: d164(빌드 / 커밋 59bc487) → d165(5f36cde) — dms 이미지만(포탈 SPA 는 dms 이미지 web 스테이지가 빌드). 릴리스 dms-api·
dms-controller, 오버레이 dms newTag d165(가드 통과, 라이브 = 동봉 매니페스트).

실증(테스트베드 실데이터, 실 Chrome 1440/1280/375 — 전 화면 가로 넘침 0·콘솔 오류 0·외부 요청 0·4xx/5xx 0):
- sync 성공(alice): ① 사전 점검·미리보기 / 작업 컨펌 관문 줄 / ② 실행 분리, 단계 행마다 칩(로그 + 그 phase 파일), 보조 그룹 재확인
  통과 이벤트가 행 주석, 실행 「스케줄 대기 5초」, 칩 → 구획 안 뷰어(375 포함).
- scan 성공(mason): ① → 「미리보기·컨펌 없이 바로 실행됩니다」 → ②. 사전 점검 거부 scan: 배너 「사전 점검 단계에서 거부되었습니다」,
  ① 붉은 테두리 + `DMS_PREFLIGHT_REASON=target_not_readable` 로그 자동 열림, ② 「실행 안 됨」.
- LDAP 회수로 끊긴 sync: 배너 「실행 직전 재점검을 시작하기 전에 실패했습니다」, ①·컨펌 완료, 재점검 「시작 전 실패」 + 이벤트 주석,
  로그 자동 열림·지어낸 소요 없음, 경고 이벤트라 「진단 이벤트」 펼침.
- ConfirmPending(alice `dms_test/src → dst_new/reqdetail-d164`, 컨펌하지 않음): 「작업 컨펌」·「컨펌하러 가기」 각 1개, 배너 버튼 →
  관문 줄 「작업 컨펌」 포커스·화면 안, 창이 잡 폴링 2회 뒤에도 유지, 닫으면 포커스 복귀(1440·375). 취소 후 「컨펌 전에 취소되었습니다」·
  목적지 미생성 확인.

테스트: 프런트 1013 passed(RequestDetail.test 42건 중 bytes 괄호 기대값 1건만 갱신 · JobViewer.test 17건 → JobStages.test 이식 +
신규) + tsc + 빌드(외부 URL 0), e2e 9 passed(E4 에 375 순회 추가, E5·E6 무수정). 픽스처 라우팅 실 Chrome 캡처 1440/1280/375 8개
시나리오(가로 넘침 0·콘솔 오류 0).

### ✅ LDAP 보조 그룹 인정 — **완료·실증**(2026-10-08, d163)

사용자 요청: "sync 목적지 권한 체크의 '보조 그룹으로 받은 권한은 인정되지 않습니다'를 근본적으로 인정하게" → 설계 검토(D1–D18)
→ 사용자 결정(권장안 + D2 보류 후 3번 재시도·D3 gid 범위 제한 없음·D9 켜진 채 출하·D11/D12/D16 안 함·D18 사전 점검 없음).
구현 C1~C5 → 적대적 리뷰(확정 11) → 반영 → 검증(신규 6) → 반영.
- **계획 시점 숫자 스냅숏**(`identity.py`): LDAP gidNumber 만(posixGroup, 이름·DN 은 어디로도 안 흐른다) worker_pool.identity
  4키(`supplementary_gids`·`_status` applied/none/over_limit/disabled/privileged·`_excluded`·`_found`). 256개 초과 = 미적용(D4),
  root 잡 = 빈 목록, 키 부재(배포 전 계획) = [](더 좁은 권한). 비특권 주 gid 0 거부(D5), owner_username 자격 planner 재확인
  (API 와 같은 술어), 명시 chown 의 gid 는 주 그룹 ∪ 적용된 보조 그룹만(`chown_group_not_member`, D7).
- **적용 지점**(`execution_manifests`): preflight 파드 **pod 수준** supplementalGroups + 자기 검증(`id -G` 같은 집합), 워커는
  `/etc/group` 의 `dmsg<gid>` 줄(sshd initgroups)을 가드 밖에서 항상·목록 검증 먼저·기존 계정 uid+gid 대조·`id -G` 검증(D13,
  `identity_groups_not_applied`). preflight·워커 목록은 같은 스냅숏에서 파생(계약 테스트).
- **재확인**(`stepper`): 4개 제출 직전 + 큐 대기(PENDING 2패스) — 스냅숏 ⊆ 최신 LDAP·uid/gid 그대로여야 제출(D1,
  `identity_changed_at_step`), 모양 위반은 `identity_missing_at_step`. LDAP 불가는 보류 후 재시도 3번(간격 ≥60s, strict 이벤트 계수,
  4번째에 `ldap_unavailable`, D2), 틱 서킷 + LDAP 예산 10s(루프 리스 30s 보호, 예산 경계는 미계수 보류).
- **LDAP 리졸버**(D14): 사용자 엔트리 중복·결과 코드 4/11·그룹 페이지 상한은 fail-closed, 페이징 500×20, resolve 마감 10s,
  시작 URI 기억(죽은 앞쪽 URI 를 매번 내지 않고, 검색만 실패하는 URI 에서도 벗어남), bind 뒤 스키마 읽기 끔(get_info=NONE).
- **artifact base**(D6·D12 대체): g+w(gid 무관)·POSIX ACL(access 쓰기 항목·default 존재) 거부, other-x 강제, 보조 그룹 잡은
  제출 전 컨트롤러 정적 관문도. 공유 부모는 root:root 750(gid 0 LDAP 그룹이 있으면 700) — 711 은 base 에만.
- **포탈**(D15): 요청 상세 잡 카드 '보조 그룹(gid)' 행 + 스토리지 종류별 주의문(NFS 16개·Lustre MDS 재판정), 조건부 문구.
- **운영**: 스위치 `DMS_IDENTITY_SUPPLEMENTARY_GROUPS`(기본 켬, 계획 시점 전용 — prod `values.env` 배관), README §2c(스위치 범위표·
  스토리지별 조건·배포 직후 확인·롤백 절차), ARCHITECTURE 불변식 5·7·8·9·10·§4 틱 시간.
- 남는 위험(문서화): gid 0 LDAP 그룹 멤버는 root 그룹 권한(D3 수용), StartTLS 인증서 미검증(D16), NFSv4/GPFS 상속 ACE 는 검사 밖,
  그룹 없는 비 root 잡은 잡 단위로 default POSIX ACL 을 보지 않음(3홉 화면만 빨강), 중첩 그룹 미해석(D10).

배포: d163(빌드 c19e1e9f / 커밋 827e49a) — dms 이미지만(워커 셸은 매니페스트 인라인). 릴리스 dms-api·dms-controller, 오버레이 dms newTag.
testbed 저장소: dmsproj(gid 10010, alice — bob 은 비소속 대조) ansible 편입(304865d, 라이브 엔트리는 그대로).

실증(테스트베드, D17):
- 양성: `dms_test/suppgrp/ro`(root:dmsproj 0750) scan(mason, 실행 신원 alice, 비 root) → Succeeded files 2 — d162 까지는
  target_not_readable. preflight 파드 `spec.securityContext.supplementalGroups=[10010]`(컨테이너 SC 엔 없음), 커널 적용
  `status...user.linux.supplementalGroups=[10000,10010]`, 워커 env `DMS_JR_SUPP_GIDS=10010`, 4단계 재확인 통과 이벤트.
  alice sync `ldap-e2e/proj-shared` → `rw`(2770) Succeeded files 2(소유 10001:10000 — 자동 chown 주 그룹), chown `10001:10010`
  → 그룹 10010(예전 EPERM), chown `10001:10020` → `chown_group_not_member`(잡 없음), 그룹 쓰기 디렉터리 안 rm Succeeded.
- 대조: bob → target_not_readable·status none·preflight 파드에 securityContext·env 없음(종전과 같은 매니페스트); root scan → privileged.
- 회수(D1): alice sync ConfirmPending → LDAP 에서 dmsproj 제거 → 확인 → exec_preflight 제출 전 `identity_changed_at_step`
  (missing [10010]) Failed, 목적지 미생성 → 멤버십 원복.
- 위조(drain 중 DB 수정): [10010,10010] → identity_missing_at_step, bob [10010]+applied → identity_changed_at_step,
  bob [10010]+none → identity_missing_at_step(상태 결속), alice [0]+applied → identity_changed_at_step, 직접 INSERT 요청 행
  (alice → owner bob) → privileged_not_authorized, owner "" → invalid_owner_username.
- LDAP 장애(컨트롤러 노드 → pkg-01:389 DROP): 그룹 잡 보류 1/4·2/4·3/4(≥60s 간격) → 4번째 `ldap_unavailable`(+197s), 그룹 없는
  잡은 LDAP 없이 진행, stepper 틱 최대 5.1s(ldap 5.0s/10s, circuit open) — 리스 30s 안. 차단 해제 후 컨트롤러 LDAP 정상.
- 실 Chrome(alice): 요청 상세 '보조 그룹(gid) 10010' + 스토리지 주의문, 콘솔 오류 0(캡처 d163-alice-sync-request-detail.png).
- 정리: 픽스처 root rm, dms_test 원복, LDAP 기준선, drain off, 미종단 요청 0.

테스트: 백엔드 전체 2580 passed, 프런트 892 passed + tsc + 빌드(외부 URL 0), e2e 9 passed.

### ✅ LDAP 연결 강화 — 컨트롤러 영구 정지 결함 수리 — **완료·실증**(2026-10-07, d162)

보조 그룹 설계 검토에서 찾은 기존 결함을 사용자 결정으로 보조 그룹과 분리해 먼저 출하했다. LDAP 호출에 타임아웃이 없고 다중
URI 를 ServerPool(exhaust=True)로 묶어, 한 번 실패한 서버를 영구히 건너뛰다 전부 죽으면 connect() 가 영원히 돌아오지 않았다
(단일 스레드 컨트롤러 전체 정지, livenessProbe 없음).
- URI 를 순서대로 하나씩(`identity_ldap.connect_first`), 연결·StartTLS·bind·검색 각각 `DMS_LDAP_TIMEOUT_SECONDS`(기본 5,
  0.5~60) 상한, 연결 매번 unbind. receive_timeout 은 정수 올림(ldap3 가 SO_RCVTIMEO 로 pack -- 실 LDAP 확인에서 발견).
- 다중값 그룹 cn 펼침(요청이 plan_error 로 Pending 에 갇히던 크래시), planner 틱 서킷(한 틱에 LDAP 불가 1회면 나머지는
  다음 틱으로 -- `LdapCircuitOpen`).

배포: d162(빌드 695873ae / 커밋 69d3124) — dms 이미지만. 실증: 개발 호스트에서 실 LDAP 로 죽은 URI 를 앞에 두면 2초 뒤
페일오버로 alice(uid 10001, dmsproj·dmsusers) 해석, URI 2개 전부 죽으면 4.0초에 IdentityUnavailable. 라이브 d162 에서
alice 비특권 sync 제출 → planner 가 LDAP 로 신원 해석(worker_pool.identity uid 10001/gid 10000) → preflight 판정까지 정상,
컨트롤러 로그 LDAP 오류 0. 테스트: 백엔드 전체 통과(connect_first·ldap3 인자·unbind·다중값 cn·틱 서킷·에이전트 디렉터리 계약).

### ✅ 배치 sync 확인 강화 + 공유 토큰의 계정·배포 경로 차단 — **완료·실증**(2026-10-07, d161)

사용자 요청: "운영자가 배치작업으로 sync 할 때, 컨펌이 필요한지 체크" → 점검 결과(배치당 1회 확인은 동작하지만 우회
경로가 있음)를 보고 "고치는 방향으로 구현". 적대적 리뷰 3회(확정 26 → 9 → 5건)를 반영했다.
- 우회 경로였던 것: Running·PreviewReady 배치에 항목 추가·재실행 → 새 미리보기가 사람 확인 없이 자동 컨펌돼 root 실행;
  공유 토큰(모든 노드 에이전트 보유)·특권 목록 밖 관리자의 항목 추가·재실행·확인; 배치 자식의 단건 컨펌; 토큰으로 계정을
  지우고 다시 만들어 특권 세션 획득; 토큰 릴리스로 수정 전 이미지 롤백; 목록 밖 관리자의 "root" 계정 생성·셀프 재설정.
- 배치 확인 계약: 새 Queued 는 커밋 뒤 CAS 로 Previewing 복귀, 확인 회차(`batches.preview_round`)·Queued 없음 CAS,
  확인 트랜잭션의 **확인 도장**(confirmed_fingerprint) — orchestrator 는 도장이 지금 지문과 같은 자식만 실행하고 도장 없는
  미리보기가 보이면 Previewing 으로(업그레이드 전 옛 행 포함). 실행 중 자식은 확인 대기를 막지 않고, 확인 대기 배치도
  루프가 기록만 하러 돈다. 상태 전이 전부 CAS, 자식 생성은 요청 INSERT + 항목 claim 한 트랜잭션, 확인 감사 행.
- 경계: 계정 변경(403 `accounts_session_required`)·배포 경로(`require_session_admin`, 403 `admin_session_required`)는
  세션 관리자만, 특권 목록 이름 계정은 특권 세션 관리자만(403 `privileged_account_protected`, 셀프 재설정 불가).
- 포탈: 「배치 확인」 대화상자(실행할·실행 중·완료·실행 안 됨+원인·복사 대상·동시 실행 상한·만료·delete·소유권, 연 회차
  고정), 「확인 대기」 배지(주의색), 단계별 배너·항목 추가 안내, 409 헤더 표시.
- 동작 변화(운영 공지 대상): 배포 직후 Running sync 배치에 옛 코드가 만든 미확인 미리보기가 있으면 한 번 다시 확인을
  요구한다. 토큰으로 계정·빌드·릴리스·컨트롤 상태·레지스트리 삭제·artifact base 를 바꾸던 자동화는 403(포탈 세션으로).
  :confirm 은 본문 `{"preview_round": N}` 필수(옛 스크립트는 422). 특권 목록 이름 계정은 셀프 비밀번호 재설정 불가.
- 남은 것(범위 밖, 리뷰 기각 사유 기록): 공유 토큰이 남의 단건 run_as_root 잡을 컨펌할 수 있음(지문 일치 필요, 사전
  존재), 세션 관리자는 누구나 빌드·릴리스 가능(관리자 사이 경계는 심층 방어).

배포: d161(빌드 81c58b8a / 커밋 275ad21) — dms 이미지만. 릴리스 dms-api·dms-controller, 오버레이 dms newTag d161
(에이전트·잡 이미지 d158 유지). `batches.preview_round` 컬럼은 기동 마이그레이션(_ensure_columns)이 붙였다.

실증(테스트베드 https://dms.local):
- 토큰: 계정 생성·역할·비활성화·삭제 403 accounts_session_required, 배치 항목 추가·rescan 403 privileged_not_authorized,
  릴리스·빌드 403 admin_session_required, 조회(GET)는 200, alice 계정·배치 불변.
- 배치 2a5f6937(1항목): PreviewReady(회차 1·files 4) → 자식 단건 컨펌 409 batch_child_confirm_via_batch(잡 그대로
  ConfirmPending) → 실 Chrome: 「확인 대기」 배지 text-attn/bg-attnbg(title PreviewReady)·배너·대화상자(1/1·복사 대상 4개·
  상한 1·만료 KST·소유권 root 문구) → 「실행 확인」 POST `{"preview_round":1}` 200, 콘솔 오류 0(캡처 d161-1007b-*.png)
  → Completed → 완료 배치에 추가 = Previewing → PreviewReady(회차 2), 30초 자동 실행 없음 → 회차 없는 확인 422
  preview_round_required, 옛 회차 409 batch_preview_changed, 회차 2 확인 200 → Completed. 감사 행 2건(회차 1·2, stamped 1).
- 배치 9ad866b6(3항목, 상한 1): 확인(회차 1) 뒤 #0 실행 중·#1·#2 확인받고 대기 중에 항목 추가 → 응답 Previewing →
  #0 은 끝까지 돌아 Previewing 중에 기록(Succeeded), #1·#2 는 재확인 전까지 시작 안 됨 → #3 미리보기 → PreviewReady
  (회차 2) → 옛 회차 409 → 회차 2 확인 → #1→#2→#3 차례 실행 → Completed.
- 정리: 실증 목적지 6개를 root rm 단건(잡별 컨펌)으로 삭제, dms_test 는 dst·dst_fail·dst_new·dst_root·src 로 원복.

테스트: 백엔드 2280 passed(신규 tests/test_batch_confirm_gate.py 37 — 게이트·재검토·CAS 경합·확인 도장·회차·실행 중
자식·확인 대기 기록·materialize claim, 계정·특권 이름·셀프 재설정 가드, 배포 경로 토큰 403), 프런트 874 passed + tsc +
빌드(외부 URL 0), e2e 9 passed.

### ✅ 단일 작업·배치 생성 화면 단일 페이지화(시트 + 오른쪽 제출 요약) — **완료·실증**(2026-10-06, d159·d160)

사용자 요청: "데스크탑 웹 환경의 dms 작업 제출하는 화면(사용자, 운영자 모두)의 UI layout 및 디자인이 모바일 환경처럼
폭이 좁고 핸드폰 화면에 맞춰진 것처럼 되어 있다 — 심플하고 직관적이고 한눈에 보이는 디자인으로". 원인: 두 화면이 4스텝
위저드(max-w-xl 576px / max-w-2xl 672px 카드)라 1440 화면 오른쪽 절반이 비고, 한 스텝이 드롭다운 하나뿐이라 전체를
보려면 다음·이전을 오가야 했으며, 확인 스텝 옵션은 JSON 한 줄이라 넘쳤다.
- 공용 `components/form/SubmitLayout`: 한 장의 시트(번호 구획 — 실제 순서라서 번호) + 오른쪽 sticky 제출 요약(제출 버튼·
  잠김 이유 포함). xl(1280)부터 2열·그 아래 1열(시트 → 요약), DOM 한 벌, 왼쪽 기준선(mx-auto 없음), max-w-6xl.
  입력 | 설명 나란히(FieldRow), 옵션은 값 토큰. 위저드 자산(Wizard·BottomActionBar·Stepper) 삭제.
- 단일 작업: 작업 종류(연산 + 한 줄 설명) → 소스와 목적지(소스 → 목적지 한 줄, 입력 아래 "실제 경로") → 실행 옵션 →
  실행 설정(관리자). 제출 바디·게이트·`rootEffective`·허용 쌍·목적지 조건 카드는 그대로, Enter 는 제출하지 않음.
  경로 즉답(`storagePaths.relativePathProblem` = `domain.validate_relative_path` 미러): "/" 시작·".."·관리 디렉토리
  자신은 이유를 보이고 제출을 잠근다.
- 배치 생성: 작업 종류 → 대상 스토리지와 항목(입력 방식 세그먼트, 항목 표 스크롤) → 실행 옵션 → 실행 제어 → 이름·메모.
- 적대적 리뷰(20 에이전트, 확정 6): "실제 경로"를 다듬은 값으로 짓던 것(보내는 값 그대로 + 거부 경로는 이유), 세그먼트
  한글 음절 줄바꿈, sync 배치 캡션 모순, 관리자 부제의 rm 배치 오안내, 긴 라벨에서 설명 어긋남, e2e 주석 과장(→ 200자
  경로 칸 봉쇄 단언 추가, 변이 시험으로 빨간불 확인).
- d160: 사용자 화면이 관리자 전용 `/api/admin/policies` 를 부르던 403 잡음 제거(위저드 시절부터의 현행 동작, 실증 중 발견).

배포: d159(빌드 0ce47c16 / 커밋 35d1da3), d160(빌드 3222bc3b / 커밋 87519f0) — dms 이미지만. 릴리스 dms-api·
dms-controller, 오버레이 dms newTag(에이전트·잡 이미지는 d158 유지).

실증(실 Chrome·https://dms.local): 관리자 단일 작업(1440) — 구획 4개 + 제출 요약이 한 화면, 「다음」 버튼 0개. scan·cephfs-dms 에
"/dms_test/src" 를 넣으면 「"/" 로 시작할 수 없습니다」 + 제출 잠김, "dms_test/src" 로 고치면 「실제 경로:
/cephfs/managed/dms_test/src」·root 기본 체크·요약(scan · cephfs-dms:dms_test/src · batch_files=1000000 ·
broken_limit=100 · (정책 기본: low) · root) → 제출 → 상세 이동 → Succeeded(d159 7f75f5b2, d160 70077633).
사용자(alice): 3구획(실행 설정 없음)·연산 sync 만. 배치 생성: 5구획 + 생성 요약. 페이지·콘솔 오류는 d159 에서
1건(사용자 정책 403 — d160 에서 제거) → d160 0건. 캡처 after-d160-*-{1440,1280,375}.png(이전 before-*-1440.png).

테스트: 프런트 855 passed(SubmitJob 58·BatchCreate 49 — 위저드 동선을 제출 버튼 게이트로, Enter 미제출·사용자 바디
전체 모양·고급 옵션 오류 시 접힘 방지·경로 즉답·정책 API 게이트 추가) + tsc + 빌드(외부 URL 0), e2e 9 passed(E3 에
/jobs/new·/admin/batches/new 1280·375 순회 + 칸 봉쇄 단언, E4 한 화면 흐름), 백엔드 프런트 계약 테스트 60 passed.

### ✅ 노드 배치 제외 + 다시 포함 + k8s cordon 자동 반영 — **완료·실증**(2026-10-02, d158)

사용자 요청: "특정 노드를 스케줄러에서 제외 -- 문제가 생긴 노드에 더 이상 job 이 안 들어가게" → 조사(DMS 는 노드를 k8s 가
아니라 planner 가 에이전트 보고로 골라 required affinity 로 고정 -- `kubectl cordon` 만으로는 막히지 않고 그 노드가 낀 잡은
gang 이 안 서서 Pending 에 영원히 멈췄다) → 결정 "포탈 배치 제외 후 cordon 자동 반영, 배치 항목은 종료, 다시 포함도".
- 포탈 노드 화면 「배치」 열: 「배치 제외」(사유)·「다시 포함」, 배지(배치 제외·cordon·일시 불가), 대시보드 배지, 감사
  `node_exclusion`. 새 테이블 `node_exclusions`.
- 에이전트가 자기 노드의 cordon·NoSchedule/NoExecute taint 를 `report.k8s_node` 로 보고(kubelet 조건 taint 는 transient --
  새 계획만 피함). 에이전트 DaemonSet 은 NoExecute taint 를 견딘다.
- 강제: planner 후보(남은 노드로 계획, 막힘이 원인일 때만 0대 → `nodes_excluded`), 제출 직전·제출 뒤 스케줄 전(PENDING)
  재검사 → `node_excluded_at_step` 종단(배치 항목도 종료), 컨펌 409 `node_excluded` + 종단. 노드에서 이미 실행 중인 잡은
  그대로. nsync 런처도 후보 안으로.
- 적대적 리뷰(19 에이전트): 제출됐지만 스케줄 전 단계 미검사, NoExecute 에 에이전트가 쫓겨나 반영 실패, 일시 조건 taint 로
  계획된 잡을 죽이던 것, sync 거부 사유 오귀속, PostgreSQL NUL 500 등 반영.

배포: d158(빌드 412f7522 / 커밋 21438af, dms-mpifileutils+dms+dms-agent 세 이미지). 릴리스 dms-api·dms-controller·
dms-agent·job-image d158, 에이전트 DaemonSet 의 NoExecute toleration 은 렌더한 오버레이의 DaemonSet 문서만 `kubectl apply`
(diff 가 toleration 추가 하나뿐임을 확인 -- 전체 `apply -k` 는 불변 migrate Job 때문에 실패).

실증(실 Chrome·API, 페이지 오류 0): 에이전트 5대 모두 실제 read_node 로 `schedulable: true` 보고(null 아님). 기준 scan
후보 w1~w4 → 포탈에서 dms-w1 배치 제외(사유 입력) → 배지·머리글 "배치 제외·cordon 1대"·감사 → scan 후보 w2~w5 →
sync 를 ConfirmPending 까지 보낸 뒤 첫 후보 dms-w2 제외 → 컨펌 409 `node_excluded`·잡/요청 Rejected(`node_excluded_at_step`)
→ 다시 포함 → scan 후보 w1~w4 복귀. `kubectl cordon dms-w1` → 에이전트 보고 `schedulable: false (cordoned)` → 포탈 cordon
배지 → scan 이 w1 을 피해 w2~w5 로 Succeeded(예전엔 영원히 Pending) → uncordon → 복귀.

참고(기존 동작): 에이전트 재시작 직후 첫 보고는 마운트 목록이 비어(부트스트랩) 그 노드가 잠시(다음 보고까지 ≤60초) 후보에서
빠진다 -- 에이전트 롤아웃 직후 scan 후보가 3대로 줄어든 것으로 확인.

테스트: 백엔드 2237 passed(노드 제외 29: placement·planner·stepper 제출 직전/PENDING/RUNNING·컨펌·API·감사·에이전트 프로브·
배치 항목, DaemonSet toleration·RBAC 계약), 프런트 847 passed + tsc + 빌드(외부 URL 0), e2e 9 passed.

### ✅ 사용량 분석 개선(목록 컬럼·필터·정렬·온도 툴팁·CSV) + 히스토그램 즉시 툴팁 — **완료·실증**(2026-10-02, d156·d157)

사용자 요청 다섯 가지 + 작업 중 추가 두 가지("막대에 마우스 올리면 바로 -- 지금은 몇 초 기다려야", "배치작업의 히스토그램도").
1. **목록 컬럼** 실 사용량·파일 수·hot 비율(atime 180일) -- 타깃마다 최신 성공 scan 기준. 리포트를 못 읽으면 용량만 DB
   기록값 + "(DB)", 파일 수·hot 은 —.
2. **온도 추이 툴팁 위치·내용**: 열 번호 비율로 차트 전체 폭 위에 놓던 결함(실측 첫 열 x=299 인데 툴팁 x=407, 끝 열 515 인데
   1082) -> 열의 실제 사각형 옆(포털·고정 좌표). 시각·요청자·실 사용량·파일 수·hot 비율 + 색 견본별 구간("1일 이내"·
   "181일~1년"·"10년 이상")·용량·비중, 180일 경계 점선.
3. **필터**: 스토리지(정확히 일치, 등록 ∪ 스캔 기록 이름 -- 미등록 표시) + 경로(부분 문자열) 동시.
4. **최근 스캔 정렬**(서버가 limit 전에) + "N일 전", 30일 주황·90일 빨강.
5. **CSV 내보내기**: 지금 필터·정렬, 항목당 한 줄, 69열(테스트베드) -- 스토리지 정보·절대경로·스캔 횟수·최초/최근·경과
   일수·최신 요청/잡/요청자·실 사용량·직전 대비 증감·hot 비율 3축·요약 수치·파손 경로 수·축×구간 바이트·크기 구간 개수.
   RFC 4180 + 수식 주입 방어 + UTF-8 BOM. 상한 5000(넘으면 알림).
- **즉시 툴팁**(components/ui/HoverTip): 배치 상세 히스토그램·대시보드 잡 통계 막대가 브라우저 title(지연 1초+) 대신
  진입 즉시 구간·값·비중·누적.
- 백엔드: scan 리포트 **투영 결과 캐시** `scan_report_digests`(불변 리포트 -- 목록·내보내기·이력이 공유), 필터·정렬·
  최근 잡 창 쿼리, `/export`·`/scan-storages`. hot 비율이 dscan 마지막 구간 `[3651d,INF]`(상한 없음) 때문에 "—"가 되던
  기존 결함도 고침.
- 적대적 리뷰(21 에이전트) 반영: 깊은 중첩 리포트의 RecursionError 500(요청자가 자기 아티팩트를 바꿀 수 있다), 내보내기
  1만 행이 api 메모리 한도·이벤트 루프 정지, 폴링으로 막대가 줄면 툴팁이 페이지를 죽이던 것, 좁은 화면·포커스 스크롤 툴팁.
- d157: 실 Chrome 에서 오른쪽 정렬 「스캔 횟수」와 「최근 스캔」이 붙어 "4"+"1시간 전"이 "41시간 전"으로 읽히는 것을 보고
  여백.

실증(d156 빌드 72522a2f / 커밋 4482335, d157 빌드 5d2f1145 / 커밋 fc97ffc, 실 Chrome·페이지 오류 0): 목록 8타깃 새 열 값
(ldap-e2e 16.0 MiB·26·100%), 39~59일 전 주황, 오름차순 첫 행 team/sub1(59일 전), cephfs-dms+"ldap" 3행, 온도 툴팁이 첫·끝 열
오른쪽 8px 에 붙고 범례 9구간, CSV 실제 다운로드(BOM·8행·69열·"8개 항목을 내보냈습니다"), 배치 히스토그램 툴팁이 hover
3ms 뒤 표시(title 0개), 대시보드 처리량 막대도 즉시. 375px: 페이지 가로 넘침 0, 툴팁 화면 안.

테스트: 백엔드 2207 passed(사용량 분석 +13·마이그레이션), 프런트 842 passed + tsc + 빌드(외부 URL 0), e2e 9 passed.

### ✅ 배치 실행 설정 변경(종단 배치) + 대시보드 새로고침 + scan 히스토그램 툴팁 — **완료·실증**(2026-10-02, d155)

사용자 요청 세 가지.
1. **배치 취소 후 재실행 시 실행 환경 변경**: 종단(완료·취소) 배치에서 동시 실행 상한·우선순위·노드 수·노드당 프로세스 수·
   연산 옵션을 바꿀 수 있다(`PATCH /api/admin/batches/{id}/execution`, 배치 상세 「실행 설정 변경」). 바뀐 값은 다음 재실행
   (전체·실패분·선택 재실행, 항목 추가)의 자식부터 적용되고 끝난 항목의 기록은 그대로. 활성·PreviewReady 는 409
   `batch_settings_locked`. 생성과 같은 특권 게이트·검증, 바뀐 키만 audit_log `batch/execution_settings`. 실행 신원·연산·
   항목은 대상 아님. 옛 규칙 옵션(이름 chown·top_k)이 남은 배치도 여기서 고쳐 다시 돌릴 수 있다.
   - 적대적 리뷰(16 에이전트) 반영: sync `:rerun-failed` 가 무조건 Running 이라 바꾼 delete·chown 이 배치 미리보기·확인 없이
     root 로 돌던 것(→ `:rescan` 과 같은 분기, sync 는 Previewing), orchestrator 가 틱 스냅샷 행으로 자식을 만들어 취소→변경→
     재실행이 한 틱 안이면 옛 값이 실리던 것(→ `_drive` 가 행을 다시 읽음), 다이얼로그의 숫자 칸·명시 false·64 초과 저장값·
     옛 옵션 키 처리, 공허하게 통과하던 음성 테스트.
2. **대시보드 노드/리소스 새로고침 버튼**: 노드 메트릭(고른 기간 그대로)과 노드 목록을 다시 읽고 옆에 「갱신 HH:MM:SS」.
3. **scan 히스토그램**: 데이터 온도 막대 위 용량 글자(길면 잘림)를 빼고 누적 % 선만 — 구간별 용량·비중은 막대 툴팁.
   파일 크기 분포도 툴팁(개수·비중), 총 개수는 제목 「파일 크기 분포(개수) · 총 N개」로.

실증(d155 빌드 9ce7bacc / 커밋 afb3de4, 실 Chrome·페이지 오류 0):
- 실행 설정: 실증 배치(scan, 노드 4·프로세스 1) 실행 중 PATCH → 409 `batch_settings_locked`, 취소 뒤 다이얼로그가 지금 값
  (4/1)으로 채워지고 「변경 없음」 잠김 → 노드 2·프로세스 2 저장 → 전체 재실행 → Completed. 새 자식 payload node_count 2·
  procs 2, **mpi-hostfile 2줄 모두 `slots=2`**, 감사 `mason {node_count:4, procs_per_node:1} → {2, 2}`. 옛 `top_k` 배치의
  다이얼로그는 "지원하지 않는 옛 옵션 — 저장하면 빠집니다: top_k=100"(저장은 하지 않음).
- 대시보드: 새로고침 클릭 → 메트릭 1회 재조회, 갱신 시각 12:50:12 → 12:50:14.
- 히스토그램(8월 데이터 온도 실증 배치): 차트 안 용량 글자 0, 막대 툴팁 `[2d,7d]: 565.9 KiB (61%)`, 제목 「파일 크기
  분포(개수) · 총 547개」, 툴팁 `0~4K: 484 (88%)`.

테스트: 백엔드 2194 passed(실행 설정 32+α·다이얼로그 옵션 키 == 서버 스펙 계약·orchestrator 재확인·sync 재실행 미리보기),
프런트 814 passed + tsc + 빌드(외부 URL 0), e2e 9 passed.

### ✅ 잡 러너 mpi-hostfile IP 전용(워커 준비 재시도) + `workers_unreachable` — **완료·실증**(2026-10-02, d154)

프로덕션 간헐 실패: dscan rc 255, stderr `ssh: Could not resolve hostname <job>-worker-0.<job>: Temporary failure in name
resolution`, hostfile 에 worker-0 만 이름·나머지는 IP. 원인 — 러너가 호스트당 getent 를 **한 번만** 하고 실패하면 DNS 이름을
그대로 hostfile 에 썼고, ssh 대기는 90회 뒤 조용히 통과했다. Volcano svc 는 Ready 파드만 DNS 에 올리는데(publishNotReady-
Addresses 없음) launcher 가 워커보다 먼저 Ready 가 되는 일이 흔해, 성공한 테스트베드 잡의 hostfile 에도 앞 번호 워커가 이름으로
남아 있었다 — mpirun 시점 이름 조회가 한 번 더 실패하면(airgap 업스트림 SERVFAIL → EAI_AGAIN) 잡이 죽었다.
- 러너 `_wait_workers_ready`: 호스트마다 getent 가 **IP 를 줄 때까지** + 그 IP 로 ssh 가 될 때까지 재시도, 전체 공유 제한
  300초(`DMS_JR_WORKER_READY_TIMEOUT_SECONDS`). hostfile 은 **IP 만**(이름 폴백 제거 — ARCHITECTURE 불변식). 워커별
  `DMS_JR_WORKER_READY host= ip= waited=` 줄.
- 제한 초과면 mpirun 없이 `DMS_EXEC_REASON=workers_unreachable` + `DMS_JR_WORKER_UNREACHABLE host= stage=resolve|ssh` 를
  남기고 summary returncode null. stepper 가 표식을 사유 코드 `workers_unreachable` 로 승격(폴백 preview_failed/
  execution_failed, TIMED_OUT 무변경), 사유 코드 양쪽 등록.

실증(d154 빌드 9ff1b82f / 커밋 41fe294, 잡 이미지 dms-mpifileutils:d154 릴리스): 테스트베드 scan 4회(dms_test·ldap-e2e 교대,
4노드) 전부 Succeeded, mpi-hostfile 16줄 전부 IP, 워커 16개 중 15개가 IP 를 받기까지 1~2초 대기(옛 코드라면 이름으로 남았을
워커). 실패 경로는 실 d154 이미지로 — 풀리지 않는 워커 이름 + 제한 6초: mpirun 없이 7초 뒤 exit 1, 표식·`stage=resolve
ready=0/1` 줄, summary `returncode: null`, mpi-hostfile 미생성.

테스트: 백엔드 2154 passed(러너 재시도·공유 제한·실패 표식·stepper 승격·사유 코드 커버리지), 적대적 리뷰 반영(뮤테이션 4/4 검출).

### ✅ 정책 카드 지표 한 줄 정렬 — **완료·실증**(2026-10-02, d152·d153)

사용자 요청: 정책의 작업 종류별 카드(dsync·nsync 등)에서 병렬 실행·우선순위·큐 등이 두 줄에 걸쳐 있는데 한 줄에
들어갈 것 같다 — 부족하면 자연스럽게 두 줄로.
- 고정 3열(병렬·우선순위·큐) + 2열(타임아웃 둘) 두 격자를 **한 격자**로: `grid-cols-[repeat(auto-fit,minmax(11rem,1fr))]`.
  넓으면 다섯 칸 한 줄, 좁아지면 칸 수가 줄며 줄바꿈.
- d152 실 Chrome 에서 좁은 칸의 한국어 보조 문구가 낱자로 끊기는 것("낮춰집/니다", "프로세/스")을 보고 d153 에서
  `break-keep`(단어 단위 줄바꿈).

실증(d152 빌드 65de15be / 커밋 cd9c618 → d153 빌드 9c904448 / 커밋 5c9a619, 실 Chrome·페이지 오류 0): dsync·nsync 카드
지표 칸 — 폭 1440·1280 은 한 줄 5칸, 1024 는 3+2, 768 은 2+2+1, 375 는 1열, 모든 폭에서 가로 넘침 없음. d153 은 보조 문구가
단어 사이에서만 줄바꿈.

테스트: 프런트 799 passed(정책 지표 격자 테스트 추가) + tsc + 빌드(외부 URL 0), e2e 9 passed.

### ✅ 포탈 서브네임(운영자 설정) + 탭 아이콘 — **완료·실증**(2026-10-02, d150·d151)

사용자 요청 2건: ① 브라우저 탭 아이콘을 마블 '닥터 둠'으로 ② 포탈 메인 이름의 서브네임을 운영자가 설정(예:
"AI Storage Portal - SSC", DAI-CAE, DAI-OA).
- **서브네임**: `control_state.portal_subtitle`(CREATE + `_ensure_columns`), GET `/api/portal-info`(공개 — 로그인
  화면도 그린다), PUT `/api/admin/portal-settings`(관리자, 감사 `portal_settings`, 40자·제어/서식 문자 거부 →
  `invalid_portal_subtitle`). 관리 → **포탈 설정** 화면(미리보기·저장·지우기). 사이드바는 이름 아래 한 줄(240px 폭에
  "메인 - 서브" 한 줄은 넘친다), 로그인 화면·브라우저 탭 제목은 "AI Storage Portal - SSC". 2026-08-20 의 "서브텍스트
  없음" 결정을 이번 요청으로 바꿨다.
- **탭 아이콘**: `frontend/public/favicon.svg`(번들 — 런타임 airgap, 외부 URL 0). 닥터 둠은 마블의 저작권·상표
  캐릭터라 그 도안을 옮기지 않고 **오리지널 "강철 가면" 아이콘**을 넣었다 — 사용 허가된 이미지가 있으면 이 파일만
  교체하면 된다(포탈 설정 화면에 안내).
- **d150 실증에서 잡은 결함**: 서버는 200 image/svg+xml 을 줬는데 아이콘이 깨져 보였다 — SVG 주석 안의 `--` 가
  XML 위반이라 브라우저 파싱 실패(빌드·타입검사·단위 테스트 어느 것도 못 잡음). d151 에서 고치고 `tests/test_favicon.py`
  가 SVG 를 XML 로 읽고 외부 참조가 없음을 고정한다.

실증(테스트베드 d150 빌드 104c82b6 / 커밋 07ba1fc → d151 빌드 8744be66 / 커밋 a0781ea): 실 Chrome(페이지 오류 0) —
포탈 설정에서 "SSC" 저장 → 미리보기·사이드바 두 번째 줄 "SSC"·탭 제목 "AI Storage Portal - SSC"(대시보드로 이동해도
유지)·쿠키 없는 로그인 화면 브랜드와 탭 제목 "AI Storage Portal - SSC" → 지우기로 원복(서브네임 없음, 탭 제목 "AI
Storage Portal"). d151: /favicon.svg 200 image/svg+xml, 탭 아이콘·화면 미리보기 요청 둘 다 200, 이미지 naturalWidth
150(그려짐), 16·32·64·128px 렌더 확인.

테스트: 백엔드 2129 passed(d150) + test_favicon 3, 프런트 798 passed + tsc + 빌드(외부 URL 0), e2e 9 passed.

### ✅ sync chown 숫자 uid:gid 전용(이름·옛 배치 거부) + 대시보드 노드/리소스 한 줄 — **완료·실증**(2026-10-01, d149)

사용자 결정 2건: ① chown 이름 처리는 "1번 — 서버가 숫자 아닌 chown 을 거부", 이름이 든 기존 배치도 거부 ② 대시보드
노드/리소스를 펼침 없이 한 줄로(Load(1분평균)·메모리·수신·송신, 스토리지 사용% 제외).
- **왜**(d146 때 미룬 결정, 이번에 코드로 재확인 — d146 기록·경고 문구의 원인 서술 일부 정정): 잡 이미지에 LDAP NSS 가
  없어 dsync/nsync 의 getpwnam/getgrnam 이 컨테이너 파일만 본다. LDAP 이름은 못 찾아 미리보기가 사유 없는
  preview_failed, `users` 같은 이름은 데비안 기본 gid(100)로 풀려 미리보기는 통과하고 실행에서 실패/무시, **root 실행이면
  실행 신원 이름이 /etc/passwd 에 덧붙인 uid 0 줄로 풀려 목적지가 조용히 root 소유**(숫자 변환 실패가 아니다).
- **세 곳에서 막는다**(`domain.chown_problem` 하나로 판정: 숫자 = 정상, 이름 모양 = `chown_name_not_supported`, 그 밖 =
  `invalid_option`): 제출 검증(단건·배치 생성·항목 추가/수정/교체 422), 이름이 든 옛 배치(확인·항목 재실행·실패분
  재실행·전체 재실행 422 — `_reject_stale_options`; 자식 생성 시점엔 orchestrator 가 그 항목만 Rejected — 예외를 올리면
  run_once 가 **모든 배치**를 매 틱 막는다; `reject_queued_item` 은 아직 Queued 일 때만 바꾸고 그때만 실패 집계), 규칙
  전의 대기 잡(stepper `_build_spec` 관문, dsync·nsync, `chown_name_at_step` + fail-closed).
- **포탈**: 단건·배치 생성의 이름 chown 은 경고가 아니라 오류(다음 잠김, 서버와 같은 분류), 고급 옵션은 오류가 있으면
  스텝을 오가도 펼친 채, 배치 상세는 확인·재실행 거부 사유를 보이고(전엔 버튼이 죽은 것처럼 보였다) 이름 배치엔 "더 이상
  실행할 수 없음" 안내. 대시보드 노드/리소스는 노드당 한 줄(펼침·load5/15·스토리지 사용%·마운트/도구/계정 요약 제거 —
  스토리지 사용%는 공유 FS 라 노드마다 같은 값, 상세는 노드 화면).
- **적대적 리뷰 2라운드**(1차 7건 → 2차 0건): 확인·재실행 422 가 화면에 안 보이던 것, 거부 집계 경합(삭제와 겹치면
  failed_count 과다), nsync 관문 테스트 공백, 단건 폼에 뜨던 배치 전용 문구, 접힌 고급 옵션에 숨은 오류.

실증(테스트베드 d149, 빌드 f62544b2 / 커밋 2140afb): d148 에서 이름 chown 옛 배치를 만들어 즉시 취소(그땐 받아들여짐) →
d149 스크립트 13/13 — 단건 이름 4모양 422 `chown_name_not_supported`·이상한 모양 `invalid_option`·숫자 202(즉시 취소)·배치
생성 422·옛 배치 전체 재실행/항목 추가/전체 교체/항목 수정 422 + 상태·항목 불변. 실 Chrome(페이지 오류 0): 대시보드 노드 5개
× 4칸(Load(1분평균) 코어 2 기준선·메모리·수신·송신), load1/5/15·마운트 줄 0, 노드 이름 버튼 0; 옛 배치 상세 안내 +
전체 재실행 거부 사유 표시. 실증 배치 삭제.

테스트: 백엔드 2114 passed, 프런트 790 passed + tsc + 빌드(외부 URL 0), e2e 9 passed.

### ✅ 인증 메일 Knox 릴레이 + 포탈 메일 설정 + 인증번호 무차별 대입 상한 — **완료·가짜 릴레이 실증**(2026-10-01, d147·d148)

사용자 요청: `request_verification_code()` 를 참고 코드(mailer.py — 메신저 서버의 Knox 메일 릴레이
`knox_mail_dms_certi`, TCP 8025, Bearer RELAY_TOKEN)로 교체하고, Knox 인증 키·릴레이 IP·포트(8025) 등을 포탈에서
설정. 사내 실 API 는 테스트베드 밖이라 "구현 및 fake 검증 및 리뷰까지" — 가짜 릴레이 `tests/fake_knox_relay.py`
(mailer 가 기대하는 계약의 거울)로 끝까지 태웠다. **실제 릴레이 응답 모양은 사내에서 처음 확인된다**(계약이 다르면
mailer 쪽 가정이 틀린 것).
- **발송**(`api/mailer.py` = 참고 구현 + 리다이렉트 미추종·프록시 무시 + `/healthz` 연결 확인 + 테스트 메일):
  인증번호는 HTML 본문에만, 발송 성공 뒤에만 저장(실패가 메일함의 이전 코드를 죽이지 않게), 요청은 갔는데 응답이
  없으면(`relay_no_response`) 저장 + 화면 "발송 확인이 늦어지고 있다", HTTP 가 아닌 응답(포트 오설정)은
  `relay_bad_response` 502. 미지·빈 발송 방식은 발급 전 500.
- **상한**: 수신자 5/10분(참고 구현)·IP 20/10분·전체 300/10분·동시 8 — 모두 통과할 때만 모두 기록, 거절 이벤트는
  (상한, 키)마다 창에 한 번. **인증번호 추측 상한 세 층**: 코드당 5회, 아이디·용도별 누적 10회/24시간
  (`verification_failures`, 재발급으로 초기화 안 됨 → 429 `verification_locked`), IP 별 틀린 코드 20회/24시간
  (429 `verification_client_locked`, 검사~기록 원자).
- **포탈 관리 → 메일 설정**(`routes_mail_settings`, 단일 행 `mail_settings`, 해석은 `mail_config.resolve_mail_config`
  하나 — 포탈 > env > 기본, 칸별 출처 태그): 발송 방식·프로토콜·IP·포트·인증 키·타임아웃·서비스명, 연결 확인, 테스트
  메일. 키는 봉인 전송(비밀번호와 같은 통로, 용도 `mail_relay_token`) + `secret_box` 저장 암호화, 응답·감사·이벤트에
  값 없음. 키는 주소에 묶임(주소 변경 시 재입력 422, env 키는 env URL 에만, 오래된 탭의 키 저장 409), 변경·발송은 세션
  관리자만, stub 전환은 위험 확인 체크 뒤에만, 키 지우기는 2단계·키만. 로그인·계정 화면의 `@samsung.com` 하드코딩
  제거(`GET /api/auth/mail-info`).
- **적대적 리뷰 워크플로 5라운드**(1차 지적 반영 뒤 2차 20건 → 3차 9건 → 4차 2건 → 5차 0건): 재발급마다 시도 횟수가
  초기화돼 4자리를 하루 ~30% 맞히던 계정 탈취(high, 재현), 아이디별 상한만으로는 IP 하나가 계정을 돌며 하루 ~1.4 계정
  (high), 거절이 앞 상한(남의 수신자 몫)을 태우던 잠금 DoS, 거절마다 events 행, 오래된 탭의 키 저장이 진짜 키를 남이
  바꾼 주소에 묶던 유출, 동시 요청이 IP 상한을 2~3배 넘던 경합(락 없으면 32/20 — 대조 실험), HTTP 아닌 응답을 "갔을
  수도"로 분류해 보낸 척하던 것, 저장 중 입력 유실, 프록시·리다이렉트 테스트가 회귀를 못 잡던 것 등.

실증(테스트베드 d147 → d148, 가짜 릴레이 luminous:8025, dms-api 파드에서 도달 확인): 빌드 b5359403(커밋 c2f28bb) →
dms-api·controller d147 applied. 스크립트 22/22 + 누적 잠금 별도 — 봉인 키 저장(평문 422)·키 없이 주소 변경 422·
연결 확인·테스트 메일·가입(메일 코드)·로그인·재설정·수신자 상한 429(Retry-After 600)·막힌 요청 무발송·마지막 코드
유효·틀린 코드 10회 뒤 재발급·맞는 코드 모두 429 `verification_locked`(retry-after 86386)·잘못된 키 502 + 메일함 이전
코드로 가입 201(발송 성공 뒤에만 저장)·테스트 메일 reason=unauthorized. 실 Chrome(페이지 오류 0): stub 경고 → UI 로
knox_relay·IP·포트·키 저장 → 연결 확인·테스트 메일 성공, 포트 9025 면 키 재입력 요구·저장 꺼짐, 로그인 화면 도메인
안내·"인증번호를 보냈습니다"(개발용 코드 미노출) → 아웃박스 코드로 UI 가입 완료, 인증 메일 HTML 렌더 확인. 프로토콜·포트
칸의 출처 태그가 두 줄로 쪼개져 d148(빌드 d670dddf, 커밋 a2273a8)로 폭 수정 → 태그 6개 모두 한 줄. UI 로 원복(키 지우기
2단계 → stub 확인 체크 전 저장 꺼짐 → 저장) — 발송 방식 env(stub)·포탈 칸 전부 비움·키 없음, 테스트 계정 삭제, 가짜 릴레이
종료.

테스트: 백엔드 2093 passed(3차 수정 기준 전체) + 4차 수정 관련 파일 통과, 프런트 784 passed + router 로그아웃 1건(부하
시 알려진 불안정 — 단독 18/18) + tsc + 빌드(외부 URL 0), e2e 9 passed(최종 코드).

### ✅ sync 목적지 조건·소유권 안내(사용자·운영자) + 정책 카드 한 줄·정책 키 제목 — **완료·실증**(2026-10-01, d146)

사용자 요청 2건: ① Sync 안내사항에 "목적지가 없는 경우"와 "기본적으로 목적지는 요청자 본인 uid:gid 로 셋업된다"를
추가 — 사용자 및 운영자 포탈 둘 다 ② 정책 카드를 한 줄씩, 이름을 동기화·노드 간 동기화 대신 dsync·nsync 등으로.
- **안내 문구의 단일 출처** `lib/syncOwnership`(서버 `_auto_chown` 미러): chown 지정 > root(소스 소유 보존) > 실행 신원
  uid:LDAP 주 gid. 사용자는 늘 "요청자 본인", 운영자는 실제 설정대로 — 운영자 기본(root)은 "소스 그대로"라서 사용자 요청
  문구("요청자 본인 uid:gid")를 운영자에게 그대로 쓰면 거짓이 된다.
- **단일 작업**: 대상 단계 카드 "목적지 조건과 소유권"(목적지가 없는 경우 — 새로 만들고 상위는 이미 있어야 하며 중간
  디렉토리는 안 만듦 / 이미 있는 경우 / 소유권 / 보조 그룹 권한 미적용), 확인 단계 "목적지 조건"·"목적지 소유" 행.
  **배치 생성**: 같은 안내(root) + 확인 행. **배치 상세** 항목 추가·수정·CSV 교체에 한 줄 안내. 거부 사유 문구는 역할
  중립("작업을 실행하는 계정").
- **적대적 검증 워크플로 4회**(사실 — 백엔드·러너·포크 dsync/nsync 소스·테스트베드 실측, UI·테스트, 누락 탐색)가 처음
  문구의 오류를 잡았다: root 는 최상위만이 아니라 **이미 있던 같은 경로 항목 전부**를 소스 소유·권한·시각으로 되돌린다
  (dsync/nsync 기본 비교); 비 root 에서 남의 소유로 chown 하면 dsync 는 실패하지만 **nsync 는 소유 변경을 건너뛰고
  성공**; 권한 비트·시각은 소스 그대로라 소스 그룹 권한이 주 그룹에 적용; preflight·도구는 uid·주 gid 만(보조 그룹 미적용);
  **chown 의 이름은 잡 컨테이너(LDAP NSS 없음)에서 풀리지 않아 미리보기가 실패하고 root 실행에선 uid 0 으로 잘못
  해석**(기존 결함 — 이번엔 예시를 숫자로 바꾸고 이름 입력에 경고만, 서버 검증 변경은 별도 결정). 4차 지적 0건.
- **정책 카드**: 2열 격자 → 한 줄에 하나, 제목 = 정책 키(scan·dsync·nsync·rm — 수정 창 제목·감사 로그와 같은 이름),
  실행 도구명이 다른 scan(dscan)·rm(drm)만 옆에 표기.

실증(테스트베드 d146): 빌드 38245b0e(커밋 b9ee2d9) → dms-api·controller 릴리스 applied, 가드 "이미지 변경 없음". 실 Chrome
(페이지 오류 0, 제출 없음): alice 카드 "요청자 본인 계정 기준"·목적지 없는/있는 경우·"요청자 본인(alice)의 uid:gid"·"실행
신원" 미노출, 확인 행 목적지 소유 = 요청자 본인(alice)의 uid:gid(주 그룹); mason(root 기본) 카드 = 소스 소유 그대로(기존
같은 경로 항목 포함)·상위 디렉토리는 있어야(권한 검사 우회), 확인 행 = 소스의 소유자·그룹 그대로(root 실행); 정책 카드 4장
x=260·폭 1160 세로 배치, 제목 dsync / nsync / rm(도구 drm) / scan(도구 dscan); 배치 생성 안내·배치 상세 항목 추가 한 줄 정상.

테스트: 백엔드 2019 passed, 프런트 764 passed + tsc + 빌드(외부 URL 0), e2e 9 passed.

### ✅ 배치 작업 메뉴를 작업 그룹으로 + 배치 동시 실행 상한 기본 32 — **완료·실증**(2026-10-01, d145)

사용자 요청: 배치 작업 메뉴가 운영 밑에 있는데 작업 밑에 둬야 한다, 동시 실행 기본값을 32로.
- **메뉴**: 작업 그룹 = 단일 작업 → 배치 작업 → 전체 작업(제출 동선 둘 다음 목록). 배치 작업은 관리자 전용
  화면이라 항목에 `adminOnly` -- 사용자에겐 지금처럼 단일·전체 두 항목만. 크럼도 `HOME > DMS > 작업 > 배치
  작업`(생성·상세는 부모 항목으로 귀속).
- **동시 실행 상한 프리필 2 → 32**(`BatchCreate.DEFAULT_MAX_CONCURRENCY`, placeholder 도). 서버엔 기본값이
  없고(필수 필드, 1..64) 화면이 늘 바디에 싣는다 -- API 무변경. 상한 의미(배치 하나가 동시에 미리보기·실행하는
  항목 수, 배치 간 전체 상한 없음, 자원이 모자라면 Volcano 큐 대기)는 그대로.

실증(테스트베드 d145): 빌드 74ab5b48(커밋 d96a11e) → dms-api·controller 릴리스 applied, 가드 "이미지 변경
없음". 실 Chrome(페이지 오류 0): mason 사이드바 운영 = [대시보드, 사용량 분석, 빌드, 릴리스, 컨트롤 상태,
아티팩트 경로], 작업 = [단일 작업, 배치 작업, 전체 작업], `/admin/batches` 크럼 `HOME > DMS > 작업 > 배치 작업`,
배치 생성 실행 제어 단계의 동시 실행 상한 = 32(제출 전 단계에서 멈춤 -- 배치 미생성); alice 작업 = [단일 작업,
전체 작업].

테스트: 프런트 743 passed + tsc + 빌드(외부 URL 0), e2e 9 passed(백엔드 코드 무변경).

### ✅ 감사 로그 무한 스크롤 — **완료·실증**(2026-10-01, d144)

사용자 보고: 감사 로그 히스토리가 수십 개 수준밖에 안 보인다 -- 스크롤을 내리면 계속 더 보이게. 원인은
`GET /api/admin/audit-log` 가 최신 50건(limit 기본값)만 주고 화면이 그 한 번의 조회를 그렸던 것.
- **API**: `before`(키셋 커서 = 이전 쪽 마지막 행의 id) + `limit` 1..200(범위 밖 422 -- 예전엔 상한이 없어
  limit 로 전량 SELECT 가 가능했다). 파라미터가 없으면 예전처럼 최신 50건, 응답 모양(목록) 그대로. offset 이
  아닌 이유: 보는 동안 새 기록이 위에 쌓여도 쪽 경계가 밀리지 않는다(id 는 PK 라 범위 조회가 싸다).
- **포탈**: `useInfiniteAuditLog`(한 쪽 50건, 덜 찬 쪽 = 끝) + 표 끝 감시 노드(IntersectionObserver, 바닥
  200px 전) -- 전체 작업 화면과 같은 방식. "N건 표시 — 스크롤하면 더 불러옵니다" / "마지막 기록입니다 — 전체
  N건". 다음 쪽 실패는 알리고 **자동으로 다시 당기지 않는다**(감시 노드가 보이는 채라 관찰자가 다시 붙을
  때마다 실패 요청이 연달아 나간다) -- 「다시 시도」 버튼. 재조회 실패는 받아 둔 목록을 두고 경고만.

실증(테스트베드 d144): 빌드 2e755975(커밋 88a6794) → dms-api·controller 릴리스 applied, 가드 "이미지 변경
없음". API 8항목 ALL OK -- 파라미터 없음 = 최신 50건, 50건씩 끝까지 340건 7쪽(내림차순·겹침 없음) = 200건
쪽 2쪽과 같은 결과, limit=0·201·100000·before=0 → 422. 실 Chrome(페이지 오류 0): 스크롤마다 50→100→…→300→340건,
요청 `before=291,241,191,141,91,41`, 끝에서 "마지막 기록입니다 — 전체 340건"(API 합계와 일치).

테스트: 백엔드 2019 passed, 프런트 741 passed + tsc + 빌드(외부 URL 0), e2e 9 passed.

### ✅ 사용자 sync 허용 스토리지 쌍(기본 전부 불가) + 메뉴 접힘 상태 유지 — **완료·실증**(2026-09-30, d143)

사용자 요청 2건: ① 왼쪽 메뉴는 처음 로그인했을 때 전부 펼치고, 그 후 화면을 오갈 때는 접힘 상태 유지
② 사용자 sync 가능한 스토리지 쌍 policy -- 기본 전부 불가에 허용 쌍을 추가, 사용자가 소스 또는 목적지를
고르면 다른 쪽 선택지에서 불가 항목을 뺀다.
- **메뉴**: `lib/navState`(localStorage `dms.nav.collapsed.v3`) -- 셸이 라우트마다 리마운트돼도·새로고침·
  새 탭에서도 유지. 로그인 성공·로그아웃이 저장분을 지워 로그인 직후는 전부 펼침. d142 의 경로 이동 자동
  펼침은 "유지"를 깨서 제거하고, 접힌 그룹이 현재 화면을 품으면 헤더에 "현재 화면" 표식(aria-describedby).
  e2e 가 잡은 회귀: 그룹 버튼에 aria-label 을 달자 `getByLabel("스토리지")` 가 단일 작업 폼 셀렉트와 겹쳐
  E4 strict 위반 → 이름은 내용에서, 표식은 aria-hidden + 설명으로.
- **허용 쌍**: `sync_pairs`(방향 있음 -- A→B·B→A 별개, A→A 도 한 쌍) + `SyncPairsRepository`(멱등 추가·
  삭제·감사, 존재 확인은 삽입과 같은 트랜잭션). 강제는 `sync_pair_allowed` 하나로 제출 403·planner 재확인
  (`pair_exempt` = 관리자 계정·토큰 모양·배치 자식)·컨펌 게이트. 스토리지 삭제는 같은 트랜잭션에서 쌍 정리.
  API `/api/admin/sync-pairs`(목록·추가·삭제), `/api/user/sync-pairs`(restricted + 사용자가 고를 수 있는
  스토리지끼리의 쌍만).
- **포탈**: 관리 → 정책에 「사용자 Sync 허용 스토리지 쌍」 매트릭스(행 소스 × 열 목적지, 체크 = 즉시 저장,
  관리자 전용·비활성이 낀 칸은 회색 "사용자 미적용", 허용 쌍 칩 목록, 적용 쌍 0개 경고). 단일 작업 화면은
  사용자에게 허용 조합만 보인다(`lib/syncPairs.syncChoices` -- 한쪽을 고르면 다른 쪽이 그 짝만, 현재
  선택은 목록에서 빠져도 남겨 비울 수 있게, 허용 밖 조합은 대상·확인 단계에서 이유와 함께 잠금). 관리자는
  조회·필터 없음.
- 독립 리뷰(에이전트 1): high/medium 0, low 6건 전부 반영 -- 배치 면제가 d142 관리자 전용 판정으로 번진 것
  분리(`pair_exempt`), 존재 확인 트랜잭션화, 업그레이드 노트(롤아웃 시점 Pending 사용자 sync 는 Rejected),
  확인 단계 문구, 삭제 시 쌍 캐시 무효화, 재조회 실패에도 매트릭스 유지, 죽은 `groupLabelFor` 제거.
- **업그레이드 주의**(deploy/README §6): 허용 쌍이 비어 있으면 일반 사용자 sync 가 전부 403 -- 롤아웃 직후
  관리 → 정책에서 조합을 허용해야 한다.

실증(테스트베드 d143): 빌드 96d9c8af(커밋 793fa18) → dms-api·controller 릴리스 applied, 가드 "이미지 변경
없음". API 13항목 ALL OK -- 쌍 0개: alice 조회 restricted·빈 목록, alice sync 403 `sync_pair_not_allowed`, 관리자
조회 restricted=false / 쌍 추가 201 → alice 조회에 보임, 반대 방향 403 / **planner 게이트**: 제출 202 직후 쌍
해제 → Rejected `sync_pair_not_allowed` / **컨펌 게이트**: ConfirmPending 중 해제 → alice 컨펌 403, 잡은
ConfirmPending 유지 → 취소 / 관리자는 비허용 조합도 202(취소). 감사 로그에 sync_pair add·remove 11행.
실 Chrome(페이지 오류 0): alice 선택지 처음 소스·목적지 [cephfs-dms, cephfs-third](쌍 없는 cephfs-secondary
제외) → 소스 cephfs-third 선택 시 목적지 [cephfs-third] → 목적지 cephfs-dms 선택 시 소스 [cephfs-dms];
mason 정책 매트릭스 9칸·체크 3·"허용된 쌍 3개"; 메뉴는 저장분 없는 새 컨텍스트에서 전부 펼침 → 운영 접기 →
사이드바 링크 이동·운영 화면 직접 이동·새로고침에도 접힘 유지 + 헤더 "현재 화면". 테스트베드에는 alice
흐름용으로 **cephfs-dms → cephfs-dms 쌍 하나**를 남겼다.

테스트: 백엔드 2018 passed, 프런트 737 passed + tsc + 빌드(외부 URL 0), e2e 9 passed.

### ✅ 포탈 개선 7종 — 스토리지 사용 범위(관리자 전용)·노드 상세 모달·메뉴 펼침·잡 통계·아티팩트 툴팁·정책·sync 조건 — **완료·실증**(2026-09-30, d142)

사용자 요청 7건: ① 스토리지를 관리자에게만/사용자에게도 노출할지 설정(비활성 두 종류: 완전 비활성 ·
사용자에게만 비활성) ② 노드 상세를 창으로, UI 개선, 마지막 리포트 열을 맨 끝에, 오래되면 빨간 경고 ③ 왼쪽
메뉴 기본 전부 펼침 ④ 대시보드 잡 통계 내용별 구분·테이블 경계 ⑤ 아티팩트 경로 변경 주의점 경고 툴팁
⑥ 정책 UI 개선 ⑦ sync 목적지 상위 디렉토리 쓰기 권한 조건 명시.
- **사용 범위**: `storages.user_enabled`(CREATE + `_ensure_columns`, 기배포 NULL → 1 백필) × 기존 `enabled`.
  정의는 `storage_open_to_users` 하나. 관리자 전용 강제 지점: 사용자 목록 제외(관리자 피커는 "(관리자 전용)"),
  제출 403 `storage_admin_only`, planner 재확인, **컨펌 게이트**(ConfirmPending 중 관리자 전용이 된 사용자
  잡), 스캔 경로 등록. PUT 생략 = 유지(옛 클라이언트). 스토리지 탭: 행 셀렉트 + 범례(개수) + 비활성 행의
  이전 범위, 다이얼로그 세 상태 라디오.
- **노드**: 목록(상태·Ready 배지·CPU·메모리 막대·상세·**마지막 리포트 맨 끝**), 서버 fresh=false 면 빨간
  "리포트 지연"(placement 와 같은 판정), 노드 시각과 서버 수신 시각 2분 초과 차이도 빨강(음수는 "시계 또는
  프로브 지연" -- probed_at 이 프로브 전에 찍혀 원인을 단정하지 않음). 상세는 xl 모달(요약 타일·구획 표).
- **메뉴**: 첫 진입 전부 펼침(sessionStorage 키 v2). **잡 통계**: 요약 타일 + 구획 4개 + 박스·boxed 표 +
  세그먼트 기간 선택. **아티팩트**: "변경 전 주의" WarnTooltip(의존성 없음, hover·focus·클릭·Esc).
  **정책**: 도구별 카드(용도, 병렬도, nsync 면당·합계 2배, clamp, 적용 시점) + 묶음 다이얼로그.
  **sync 조건**: 대상 스텝 카드에 "상위 디렉토리가 이미 있고 쓰기 권한 필요(목적지가 있어도)" + 실제
  절대경로(`destinationParent`), 확인 스텝·배치 생성에 재노출. 공용: Dialog size·창 닫기(X, DOM 끝).
- 배포 전 화면 확인: vite 개발 서버(/api → 테스트베드 프록시) + 세션 쿠키로 7개 화면을 실데이터로 찍어
  범례 톤·메모리 열 여백·모달 X·사유 표 "건수" 줄바꿈·목적지 문장을 다듬었다.
- 독립 리뷰(에이전트 1) 11건 중 10건 반영(위 컨펌 게이트, 토큰 판정 좁힘(shared-token·node:* 모양만),
  시각 차이 문구, nsync 면당, 기본 우선순위 적용 시점, 비활성 행 이전 범위, 상위 디렉토리 존재 조건, 툴팁
  Esc·틈, X 첫 포커스, 테스트 공백). 전환 전 등록된 스캔 경로의 통계 조회는 잡 생성 불가·완전 비활성과 같은
  동작이라 유지.

실증(테스트베드 d142): cephfs-third 를 관리자 전용으로 → alice 목록에서 빠짐, mason 피커 admin_only 표식,
alice 제출 403 `storage_admin_only`, 필드 없는 옛 PUT 은 관리자 전용 유지 → 전체 사용으로 원복(라이브 PG
마이그레이션: 기존 행 user_enabled=1). 라이브 브라우저로 7개 화면 확인(페이지 오류 0). 테스트: 백엔드
2002 passed, 프런트 718 passed + tsc + 빌드(외부 URL 0), e2e 9 passed.

### ✅ 관리자 기본 root(포탈) + artifact 쓰기 감사·preflight base 통과 검사 — **완료·실증**(2026-09-30, d141)

사용자 요청: "관리자는 기본 root로 실행하도록 해줘" / "artifact 가 공용 스토리지인데 디렉토리가 루트
소유권·접근권한이라 예전 수정 때 root 로 실행되도록 됐던 것 같다 — artifact 쓰기 이슈가 없는지 확인".
- **관리자 기본 root**: 포탈 SubmitJob 이 유일한 구현 지점 — 자격(`/api/auth/me` `can_run_as_root` =
  관리자 + 특권 목록 + 세션) 있는 관리자는 'root 권한으로 실행' 기본 켜짐, 실행 신원에 **다른 사용자**를
  적으면 기본 꺼짐(같은 날 사고 경로를 기본값으로 되살리지 않음), 관리자에겐 확정값을 항상 명시.
  서버는 명시 true 만 root(생략 = 비 root). 적대적 리뷰(13 에이전트)가 "서버 생략 = root" 초안을 잡았다:
  오늘 배포된 d140 포탈 탭(생략 = 비 root 라고 표시)이 새로고침 전까지 화면과 다른 root 로 돈다.
- **artifact 쓰기 감사**(워크플로 21 에이전트 — 코드·라이브·이력 → 반박 검증 → 종합): 관리자 root 로
  새로 생기는 문제는 없다. "root 로 바꿨던 것" 은 d128 **제어면**(api/controller, base 가 root:root 라
  65532 로는 3홉 쓰기 실패) 이야기이고 잡 launcher/worker 는 원래 root, 도구만 요청자 신원이다. 살아
  있는 조건 하나: 도구가 요청자 uid·**주 gid 만**으로 `<base>/<job>/<phase>` 의 mpi-hostfile·rank.sh 를
  읽고 dscan 리포트를 쓰므로 **base 자체에 other x 가 필요**(711/755) — 그런데 README 는 "권장 700" 이었다.
  → preflight `_ARTIFACT_BASE_CHECK`(실행 신원으로 `test -x /dms-artifact-base`,
  `artifact_base_not_traversable`), README·ARCHITECTURE 불변식 정정, BACKLOG(러너 chown 반환코드).

실증(테스트베드):
- 기준선(d140): base 700 + alice 비 root sync → preflight 통과 후 **preview_failed**("Open RTE was unable
  to open the hostfile").
- d141, base 700: alice 비 root sync·mason owner=alice 비 root scan → preflight **artifact_base_not_traversable**,
  mason root sync(run_as_root: true) → Succeeded(root 잡은 base mode 무관). base 711: 비 root sync·scan
  Succeeded, dscan-report.json 10001:10000 0644·API 200. 끝나면 base 755 원복 확인.
- API: mason 생략 → 비 root → ldap_identity_not_found(옛 탭·스크립트가 조용히 root 가 되지 않음),
  true → root 성공, owner=alice + false → dst_fail destination_not_writable, alice root → 403.
- 라이브 브라우저: me.can_run_as_root=true, 체크박스 기본 켜짐 → 실제 제출 바디 run_as_root: true →
  잡 identity uid 0(확인 후 취소), 실행 신원 alice → 자동 꺼짐·확인 스텝 "실행 신원의 uid/gid", 사용자는
  체크박스 없음.
- 테스트: 백엔드 1981 passed, 프런트 700 passed + tsc + 빌드(외부 URL 0), e2e 9 passed.
- 잔여: 체크박스 캡션 `</strong>` 뒤 공백 누락(HEAD 에서 수정, 다음 빌드에 포함).

운영 확인 권고: 운영 artifact base 의 mode·ACL — `stat -c '%a %U:%G' <base>` 가 700/750/770 이면 비 root
잡(일반 사용자 전부, 실행 신원을 지정한 관리자 잡)이 d141 부터 `artifact_base_not_traversable` 로
거부된다(그 전엔 preview 에서 hostfile 오류) — `chmod 711`(또는 755). 부모 디렉터리 770 은 그대로 둬도 된다.

### ✅ sync 가 목적지 소유권을 바꾸던 사고 — root 실행 명시 opt-in + 목적지 권한 preflight — **완료·실증**(2026-09-30, d139→d140)

사용자 보고(프로덕션): `dms_test`(root 755) 아래 사용자 903436(gid 104)이 쓸 수 없는
`dst_fail`(903333 소유, 700)로 `src → dst_fail` sync 를 냈는데 실패하지 않고 `dst_fail` 소유가
903436:104 로 바뀌며 성공했다. 원인(코드 + 테스트베드 재현): `identity.resolve_job_identity` 가
특권 목록의 세션 요청자면 owner_username·화면과 무관하게 **무조건 uid 0** 을 줬다 → preflight 가
root 로 통과, 비특권이면 붙는 `--chown` 도 빠짐, root dsync 는 소스 최상위 디렉터리의
소유·권한·시각을 **기존 목적지에 적용**한다. 테스트베드에서 mason(특권)·owner=alice 로 같은 구조를
만들어 identity uid 0, `dst_fail` bob(10002) → alice(10001) 를 재현했다.
- **root 는 명시적일 때만**: `identity.privilege_policy` 단일 규칙 — 단건은 `run_as_root: true`
  (StrictBool, admin + 자격(세션) 아니면 403 `privileged_not_authorized`), 배치 자식만 "자격 있으면
  root", 그 외(기본)는 실행 신원의 LDAP uid/gid. 포탈에 관리자 전용 'root 권한으로 실행' 체크박스
  (기본 꺼짐, 확인 스텝에 실행 권한 표시), `open_noatime` 은 root 실행에서만.
- **stepper 재확인**: 신원은 계획 시 worker_pool 에 얼기 때문에 규칙 변경 전에 계획된 root 잡이
  배포 뒤에도 root 로 돈다 — 매 제출 직전 요청 행을 다시 읽어 근거가 없으면
  `privilege_not_requested` 로 종단.
- **preflight 목적지 검사**: 목적지가 있으면 목적지 자체의 쓰기·진입(`destination_not_writable`),
  비 root 면 소유자 == 실행 uid(`destination_not_owned` — 남의 그룹 쓰기 디렉터리는 dsync 가
  데이터를 복사한 뒤 최상위 chmod/utime EPERM 으로 부분 복사 Failed 였다, 실측), 그다음 항상 부모 쓰기.
- 적대적 리뷰(워크플로 19 에이전트, 14건 중 8건 생존 → 5개 결함) 반영: 얼린 root 신원 재확인,
  관리자 단건 `open_noatime` EPERM(비 root 인데 기본 ON), e2e E4/E6(LDAP 없는 하네스가 암묵 root 에
  기댐), 운영 문서(deploy/README·20-config), chown 캡션.
- **d139 실증에서 잡은 회귀 → d140**: d139 는 "목적지가 있으면 부모 대신 목적지만" 보도록 바꿨는데,
  root 755 부모 아래 alice 본인 `dst` 로의 sync 가 **Succeeded 인데 복사 0건**이었다. 포크 dsync 는
  목적지 존재와 무관하게 부모 W_OK 를 요구하고 실패 시 `rc` 초기값 0 인 채 `goto ERROR` 로 **종료
  코드 0** 이다(dry-run 은 경고만이라 미리보기도 정상). 예전 부모 검사는 오탐이 아니라 도구의 실제
  요구였다 → 부모 검사를 항상 유지(d140). dsync rc 0 자체는 BACKLOG §4(포크 패치 필요).

실증(테스트베드, `repro-owner.py` 가 제출·컨펌·종단 후 `dms_test` 소유·목록 스냅숏 전후 비교):
- 얼린 잡(d138 에서 mason owner=alice → dst_fail 을 계획해 ConfirmPending, identity uid 0) → d139
  릴리스 후 컨펌 → exec_preflight 전에 **Failed `privilege_not_requested`**, 변화 없음.
- d140 스위트 10/10: V1 mason owner=alice→dst_fail `destination_not_writable`(사고 경로, 변화 없음) ·
  V2 alice→dst_fail 동일 · V3 alice→dst/grp(bob 770, 그룹원) `destination_not_owned`(복사 0) · V4
  alice→dst(부모 root 755) `destination_parent_not_writable` · V4b alice→dst/mine(본인·부모 본인)
  **Succeeded, 파일 복사·소유 10001:10000 700 불변** · V5 mason owner=alice→dst/mine alice uid 로
  Succeeded · V6 mason root 명시→신규 dst_new root 로 Succeeded · V7 alice root 요청 403 · V8 mason
  (LDAP 밖) 생략 `ldap_identity_not_found` · V9 mason root 명시→dst_fail 은 경고대로 소유가 소스에 맞춰짐.
- 라이브 브라우저(d139): 관리자 옵션 스텝에 체크박스(기본 꺼짐)·open_noatime 잠금+캡션, 확인 스텝
  "실행 신원의 uid/gid(권한 그대로 적용)" / root 선택 시 경고 문구, 사용자(alice)에겐 체크박스 없음.
- 테스트: 백엔드 1958 passed, 프런트 699 passed + tsc + 빌드(외부 URL 0), e2e 9 passed.

운영 영향: 특권 목록의 **LDAP 밖 로컬 관리자 계정**은 이제 '실행 신원'을 지정하거나 'root 권한으로
실행'을 체크해야 잡이 돈다(아니면 `ldap_identity_not_found`). 관리자의 단건 scan 도 기본은 실행
신원 권한이라 전체를 훑으려면 root 체크.

### ✅ 방안 A — 디렉터리 설정을 보고 응답으로 하달, 포탈 이미지 릴리스만으로 에이전트 수렴 — **완료·실증**(2026-09-29, d138)

사용자 질문: "2단계(apply -k)가 꼭 필요한가 / 1단계만으로 충분해질 방법은?" — 포탈 릴리스는 DaemonSet
**이미지만** 패치하므로(rollout_runner.image_patch_body) env 로 배선한 bind 계정은 apply 없이는 파드에
들어가지 않는다. 방안 비교(API 하달 / 볼륨 마운트 / 포탈의 매니페스트 apply / SA 로 Secret 직접 읽기)
끝에 **A 채택**: 제어면이 자기 LDAP 설정을 `/api/agent/report` 응답 `directory` 로 내려주고 에이전트가
nslcd 를 수렴시킨다. DaemonSet env 는 첫 보고 전 부트스트랩일 뿐이다.
- 단일 추출점 `identity_ldap.ldap_directory_config` 를 리졸버(`build_ldap_resolver`)와 하달 블록
  (`agent_directory.directory_block`)이 함께 쓴다 — 같은 설정을 두 배선으로 나눴다가 한쪽이 빠진
  이번 사고 유형 자체를 제거.
- 비밀번호 노출 최소화: 보고에 `directory` 객체를 실은(능력 선언) 에이전트에게만, 에이전트가
  보고한 해시와 HMAC(session_secret, 설정)이 **다를 때만** 전체 블록(비밀번호 포함), 같으면
  `{hash}` 만. 옛 에이전트엔 키 자체가 없다. 보고·로그·오류 문구엔 비밀번호가 없다.
- 에이전트 `agent/directory.NslcdDirectory`: 같은 엔트리포인트 렌더러(`--render-nslcd-conf`)로
  `.dms-new` 에 렌더 → 내용이 같고 nslcd 가 건강하면 해시만 기록, 아니면 원자적 교체 + nslcd 재기동
  (부트스트랩 nslcd 를 PID 파일로 정지·거둠, `/proc/<pid>/comm` 확인으로 PID 재사용 보호, 잔여 소켓
  제거). 렌더 거부·기동 실패는 오류로 보고하고 해시를 올리지 않아 다음 주기에 재시도. 관리 여부는
  엔트리포인트가 nslcd 를 띄운 컨테이너에서만(`DMS_AGENT_NSLCD_MANAGED=1`).
- 테스트 `test_agent_directory` 38건(서버 능력 게이트·해시 게이트·HMAC·미구성 null·리졸버와 같은
  추출·DB 에 비밀번호 없음 / 실제 렌더러로 수렴·무변경 무재기동·죽은 nslcd 재기동·렌더 거부 시
  보존·소켓 타임아웃 재시도·잔여 소켓·PID 재사용·잘못된 블록·비관리 / 러너 능력 선언·비밀번호
  미전송·apply 예외 격리 / 설정·엔트리포인트). ARCHITECTURE §6, BACKLOG(에이전트 채널 TLS, nslcd
  그룹 멤버 속성).
- 적대적 리뷰(워크플로, 5개 관점 → 발견마다 독립 반박 검증): 23 에이전트, 18건 발견 중 17건 생존 → 중복을 합쳐 7개 결함, 전부 반영: [중] A dash echo 가 백슬래시를 해석해 'Q7\cz'→'Q7'·'\\' 절반·리터럴 '\n' 이 개행 가드 우회(d02fead 렌더러 결함 실측, 에이전트는 성공 보고) → printf '%s'; B 수렴 뒤 해시만 오는 주기엔 nslcd 생존 미확인·좀비를 산 것으로 판정 → 생존 확인 + waitpid(WNOHANG)/stat; C 실패해도 옛 해시 유지로 설정 되돌리면 고착 → 실패 시 해시 비움. [하] D 비밀번호 전송 빈도 문서 과소서술 정정; E pidfile 이 nslcd 소유 /run/nslcd(심볼릭 링크 유도) → root 전용 /run/dms-agent + O_NOFOLLOW; F 수렴·실패가 kubectl logs 에 안 보임 → 상태 변화마다 한 줄; G 단일 추출 테스트 동어반복 → 가짜 ldap3 로 리졸버 실제 인자 고정. 보강: nslcd 최소 env(공유 토큰이 데몬 environ 에 남지 않게). 뮤테이션 점검: 각 버그를 되살리면 해당 테스트 실패 7/7. pytest 1912.
- 실증(테스트베드 d138, 2026-09-29~30): ① 라이브 DaemonSet 에서 bind env 제거(kubectl set env …-, 프로덕션 모양) → d137 5노드 모두 익명 바인드 확인(40/40). ② **포탈 릴리스만**(dms-agent·dms-api·dms-controller d138, apply 없음): 5노드 모두 env 에 bind 가 없는데도 nslcd.conf 에 binddn/bindpw(API 하달), ldap_simple_bind_s("uid=search_dms,…") over StartTLS, getent 정상, 보고 directory source=api·error None, 비밀번호가 로그·보고에 없음, 75초 안정 구간 동안 재기동 없음(PID·시작 횟수 불변); kubectl logs 에 'directory applied source=api … bind=dn=… restarted=yes'. ③ dms-w1 nslcd SIGKILL → 다음 해시만 오는 주기에 'nslcd not running … restarting' → 'nslcd restarted', 새 PID, alice 해석 복구, 좀비 0. ④ alice sync end-to-end(bind env 없는 DaemonSet): 신원 준비 → Planned(110s) → preview → 컨펌 → Succeeded(files 2), 타인 404. ⑤ 복원: 오버레이 dms·agent newTag d138 + guard exit 0 + apply -k → env 부트스트랩이 API 설정과 같아 5노드 모두 재기동 없이 해시 채택(시작 1회, 'restarted=no'), 75초 안정(45/45). 릴리스 3종 Applied.

### ✅ 에이전트 nslcd LDAP bind 계정·StartTLS·URI 정규화 — **완료·실증**(2026-09-29, dms-agent d137)

**프로덕션 사고**(요청 b07e68d0, yo.hong, sync gpfs-mirr24-test): 마운트가 있는 ion2106~2109 전부
`identity_not_ready_on_node` → 5분 뒤 Rejected. 현장 진단: dms-agent 컨테이너에 `DMS_LDAP_BIND_DN/PW`
가 주입되지 않아 nslcd 가 **익명 바인드** — 사내 LDAP 은 익명에게 rootDSE 만 보여주고 사용자 검색은
막아 cocoa.song 까지 아무도 해석되지 않았다(파드 안에서 binddn/bindpw 만 넣자 즉시 해석). 부수로
sssd 식 콤마 URI 가 nslcd `uri` 에 그대로 들어가 한 덩어리 호스트명(gaierror)이 되어 페일오버가 없었다.
테스트베드 slapd 는 익명 읽기를 허용해 둘 다 가려졌다(사전 실측: `uri` 1줄, `ldap_simple_bind_s(NULL,NULL)`).

권장안(DaemonSet 에 bind 자격 주입 + URI 정규화)에 **원론 보강 하나**: 제어면은 StartTLS 를 bind **전에**
올리는데 에이전트 nslcd 엔 StartTLS 설정 자체가 없었다 — bind 계정만 넣었다면 검색 계정 비밀번호가 평문
389 로 나갔을 것이다. 그래서 엔트리포인트가 제어면 리졸버(`identity_ldap.py`)를 통째로 미러한다:
- `50-agent-daemonset.yaml`: `DMS_LDAP_BIND_DN`·`DMS_LDAP_USE_START_TLS` ← dms-config,
  `DMS_LDAP_BIND_PW` ← dms-secrets **키 하나만**(secretKeyRef; envFrom 금지 원칙 유지), `optional: true`.
- `agent-entrypoint.sh`: URI 콤마/공백 분해·후행 `/` 제거 → `uri` 한 줄씩; StartTLS(미설정=true,
  `_parse_bool` 미러)면 `ssl start_tls` + `tls_reqcert never`; umask 077 로 처음부터 0600; 값의 개행은
  설정 주입 방지로 거부(값 비노출); DN 만 있고 PW 없으면 경고 후 익명; 앞뒤 공백 PW 경고; 기동 로그에
  `dms-agent: nslcd uri=N server(s) start_tls=on bind=dn=… (password set)` 한 줄(비밀번호 없이) —
  이번 사고엔 이런 신호가 없었다. `--render-nslcd-conf <path>` 렌더 전용 모드.
- 테스트: `test_agent_nslcd_conf` 21건(엔트리포인트 실제 실행), `test_agent_daemonset_contract` +4
  (자격 출처, secret 키는 SHARED_TOKEN·BIND_PW 둘뿐, envFrom 금지, ConfigMap 키 존재, **엔트리포인트가
  읽는 `DMS_LDAP_*` == DaemonSet 이 주는 변수 양방향** — 옛 매니페스트면 BIND_DN/BIND_PW/USE_START_TLS
  누락으로 실패함을 확인). pytest 1853(+렌더 5건 별도 21/21). ARCHITECTURE §6 불변식.
- 실증(테스트베드): 포탈 빌드 d137(mfu+dms+agent, commit d02fead) → **1단계** 릴리스 dms-agent d137
  (새 이미지+옛 매니페스트 = 프로덕션 롤아웃 전환 구간): 5노드 모두 StartTLS 수행(이전 0회)·익명·해석
  정상·진단 줄 `bind=anonymous` (40/40) → **2단계** 오버레이 agent newTag d137 + guard exit 0 + `apply -k`:
  5노드 모두 `binddn/bindpw` + `ldap_simple_bind_s("uid=search_dms,…")` over StartTLS, `getent`
  alice·cocoa.song 정상, nslcd.conf 0600, 비밀번호가 `kubectl logs` 에 없음(45/45). alice sync
  end-to-end: 신원 준비 → Planned(100s) → preview → 컨펌 → Succeeded(files 2), 타인 404.
- 프로덕션 적용: `git pull`(dms-ssc) → 포탈 빌드 images=[dms-mpifileutils, dms, dms-agent] 새 태그 →
  릴리스 dms-agent(이미지 먼저) → `deploy/overlays/ssc` 태그 맞춘 뒤 guard + `apply -k`(env 적용).
  기동 로그에서 `bind=dn=uid=search_sc,…` 확인.

### ✅ 사용자 포탈 스토리지 피커에 관리 루트(managed_root) 표시 — **완료·실증**(2026-09-29, d136)

사용자 보고: 단일 작업 요청에서 스토리지를 고르면 운영자 포탈엔 "관리 디렉토리: …" 캡션이
뜨는데 사용자 화면엔 없다. 원인은 `/api/user/storages` 가 2026-08-15 결정으로 managed_root 를
**관리자에게만** 실었기 때문(당시 소비 화면이 관리자 전용). 사용자 셀프서비스 sync 도 같은
`StoragePicker` 를 쓰고 입력 경로가 managed_root 기준 상대경로라 같은 이유로 뿌리가 필요하다 →
역할 무관으로 싣는다(사용자 결정). `mount_path`·`status_detail` 은 계속 숨김. 프런트는 무변경
(서버가 주면 캡션을 그림). 계약 테스트 갱신.
- 실증(테스트베드 d136, 2026-09-29): 포탈 빌드 d136(commit 8002cac) → 릴리스 dms-api·dms-controller Applied. API: 비관리자 alice·관리자 mason 모두 /api/user/storages 3행에 managed_root(/cephfs/managed 등) 포함, mount_path·status_detail 없음(6/6 PASS). 실 브라우저(Playwright, alice 세션): /jobs/new → 다음 → 소스 스토리지 cephfs-dms 선택 시 캡션 '관리 디렉토리: /cephfs/managed — 입력 경로는 이 아래 상대경로입니다' 표시(role user). 오버레이 testbed dms newTag d136, guard exit 0. pytest 1833·vitest(jobs) 90·tsc.

### ✅ 잡 옵션 서버 기본값 + "작업 컨펌" 창의 미리보기 요약 — **완료·실증**(2026-09-17, d135)

**사용자 요청 1**: sync `batch_files 1,000,000`·`bufsize 4,194,304`, scan `batch_files 1,000,000`·
`broken_limit 100` 을 포탈 프리필이면서 **서버 기본값**으로. 조사(같은 날)에서 "안 골랐는데
적용되는 값"은 비특권 sync 의 `--chown uid:gid` 자동 주입, 관리자/배치 sync `open_noatime` ON,
sync 프리필뿐이었고 서버는 기본값을 박지 않았다(빈값 = 도구 기본). `domain._OPTION_DEFAULTS` 가
검증 뒤 생략 키를 채우고(rm 은 없음 — recursive 는 동의 게이트), payload/요청 상세에 그대로
남는다. sync `batch_files` 하한 1→0: 서버 기본값이 생기며 "키 생략 = 배칭 끔" 표현이 사라져
0 명시가 유일한 표현. 포탈은 `SCAN_INT_FIELDS` 로 scan 도 프리필하고 placeholder/캡션이
"비우면 기본 N 적용 · 0 = 배칭 끔" 을 말한다. `test_domain_option_defaults` 가 프리필 == 서버
기본(optionRules.ts 파싱)과 범위 안임을 고정.

**사용자 요청 2**: ConfirmPending 의 "미리보기 확인" → **"작업 컨펌"**(버튼·제목, 확인 버튼
"컨펌"). "(요약 없음)" 원인: 창이 `result_summary`(실행 **종단** 결과)를 읽어 컨펌 시점엔
항상 NULL 이었고 `_poll_preview` 는 dry-run summary.json 을 지문에만 쓰고 버렸다. 흐름은
Pending → Planned(planner) → **Preflight**(요청자 신원으로 경로 접근 검사 파드) →
**PreviewRunning**(dsync/drm `--dryrun` Volcano 잡) → **ConfirmPending**(지문 저장) → 컨펌 →
Executing(exec_preflight 재검증) → Running → Succeeded. 이제 `data_jobs.preview_summary`
(CREATE + `_ensure_columns`, JSON) 에 지문과 같은 UPDATE 로 사본을 남기고 창이 "복사 대상
N개 · 12.0 MiB"(rm 은 삭제 대상, null=모름·0 정상값 구분) 로 보여준다. 구 잡은 그 사실을
말한다. 마이그레이션 열거 그물 38.
- 실증(테스트베드, 2026-09-17): 포탈 빌드 d135(commit f2db73f) → 릴리스 dms-api·dms-controller Applied(migrate 가 preview_summary 컬럼 보강). 옵션 생략 sync(alice): 요청·잡 options = {batch_files 1000000, bufsize 4194304}, preview/execution vcjob argv 에 --batch-files 1000000 --bufsize 4194304(+비특권 --chown), 컨펌 → Succeeded(files 2). 옵션 생략 scan(admin): options = {batch_files 1000000, broken_limit 100}, argv --batch-files 1000000 --broken-limit 100, Succeeded. batch_files 0 명시 sync: options {batch_files 0, bufsize 4194304}, argv --batch-files 0 → 취소. 서빙 번들에 '작업 컨펌' 포함·'미리보기 확인' 0건. **추가 발견·수정**: 첫 실증에서 preview_summary 가 {returncode 0, files null, bytes null} — dsync --dryrun 은 복사 요약을 찍지 않고 walk 요약만 남겨 러너 파서가 개수를 못 뽑았고, 그래서 지문(summary 해시)이 사실상 상수라 fingerprint_mismatch 보호가 무력했다(기존 결함). runner: dryrun 이면 소스 walk 항목 수를 files 로(parsers.parse_sync_dryrun_counts, bytes 는 null) → dms-mpifileutils d135 빌드·잡 이미지 오버라이드 전환(job-image 릴리스, Applied) → 재실증: preview_summary {files 2, bytes null, rc 0}, 지문 == sha256(preview_summary), 컨펌 → d135 잡 이미지에서 Succeeded, 실행 요약 files 2·bytes 10 불변. 오버레이 testbed dms newTag d135, guard exit 0. (DMS_JOB_IMAGE patch-config 는 종전대로 d110 유지 — DB 오버라이드가 진실.)

### ✅ 에이전트 호스트 루트 단일 마운트 — 스토리지 등록만으로 자동 프로브 — **완료·실증**(2026-09-16, dms-agent d124)

**프로덕션 보고**: 스토리지를 등록하고 노드에 마운트해도 포탈은 Missing — 에이전트 DaemonSet 이
스토리지마다 hostPath 를 손으로 나열해야 프로브(`isdir/access/statvfs`)가 그 경로를 볼 수 있었다
(마운트포인트 여부만 호스트 mountinfo 로 봤다). 방안 비교 후 A 채택: 호스트 `/` 를 `/host/root` 에
**읽기 전용·HostToContainer** 로 한 번만 붙이고 프로브가 `mount_path` 를 그 접두로 번역
(`DMS_AGENT_HOST_ROOT`). 컨트롤러가 DaemonSet 을 패치하는 B(라이브·git 분기, 스토리지마다 재시작)와
`hostPID`+`/proc/1/root` C(CAP_SYS_PTRACE)는 기각.
- writable 은 `os.access(W_OK)` 대신 호스트 mountinfo 옵션(per-mount·superblock rw/ro) — 에이전트는
  root 라 W_OK 는 "마운트가 rw 인가" 만 답했고, ro 바인드 아래 W_OK 는 전부 False 라 placement
  (`require_writable`)가 sync 목적지를 전부 배제하는 함정을 피했다. 아티팩트 base 는 덮는 마운트 옵션.
- 전파 자가 진단 `propagation_stale`(호스트 `/` 가 shared 아님)·`host_root_missing`; 보고에 `probe_mode`.
- 레거시 모드(env 미설정 = 직접 경로) 로 이미지 → 매니페스트 순 무중단 롤아웃.
- 계약 `test_agent_daemonset_contract`: hostPath 는 `/`·`/proc/*`·`/sys/*` 만(스토리지별 hostPath 재도입
  차단), host-root readOnly+HostToContainer, env==mountPath. 단위 `test_agent_host_root` 10건.
  pytest 전체·ARCHITECTURE §6/§7·README §2.
- 실증(테스트베드, 2026-09-16): 포탈 빌드 d134(dms-mpifileutils·dms·dms-agent 한 빌드 — agent 는 같은 태그의 dms/mfu 를 FROM 하므로 단독 빌드 불가, d124 단독 시도는 manifest unknown 으로 실패) → 릴리스 3종 Applied(새 이미지+옛 매니페스트 = 레거시 모드로 무중단) → apply -k 로 DaemonSet 볼륨이 host-root 하나로 교체·5/5 롤아웃. 기준선: 전 노드 probe_mode=host_root, cephfs-dms Ready 5, cephfs-third/secondary 는 설계대로 Degraded 3/5·2/5(w1-3/w4-5), writable True(옵션 기반), artifact_base exists+writable. 파드 기동 **후** 노드 5대에 /cephfs-alt 바인드 마운트 → 포탈 등록만으로(DaemonSet 무수정) Ready ready_nodes=5. w5 umount → 60초 내 Degraded 4/5, w5 사유 not_a_mountpoint·writable False → 재마운트 → Ready 5. alice sync cephfs-dms→cephfs-alt preview→confirm→Succeeded(files=2). 실측: readOnly 는 최상위 바인드만 — 파드 안 touch /host/root/cephfs/x 성공(전파된 하위 마운트는 호스트 옵션 rw 유지, recursiveReadOnly 는 HostToContainer 와 병용 불가) → 문서·주석을 그 의미로 정정. 정리(스토리지 삭제·바인드 해제) 완료, guard exit 0.

### ✅ hostPath 볼륨 이름 RFC 1123 정규화 + 제출 실패 원문 보존 — **완료·실증**(2026-09-15, d133)

**프로덕션 사고**: mount_path=`/mgmt_storage` 사이트에서 첫 sync 요청이 `preflight_submit_failed:
submit_failed` 로 즉시 Rejected. 원인은 `execution_volcano._volumes` 가 볼륨 이름을 경로에서
슬래시만 `-` 로 바꿔 만들어 `mgmt_storage`(밑줄) 가 되고 apiserver 가 422(`spec.volumes[0].name:
Invalid value … RFC 1123 label`, `volumeMounts[0].name: Not found`)로 거부한 것. 테스트베드
(`/cephfs`)에선 드러나지 않았다. 진단은 더 나빴다 — 컨트롤러가 예외를 삼켜 코드만 남고 원문은
로그·events·DB 어디에도 없어 컨트롤러 파드 안에서 제출 경로를 재현해야 했다.
- `volume_name(mount_path)`: 허용 밖 문자 연속 → `-`, 소문자화, 경로 sha256 앞 8자 **항상**
  덧붙임(`/data_1` vs `/data-1` 충돌 방지), 63자 상한. volumeMounts 는 같은 이름을 파생.
- 원문 보존: `stepper._record_submit_failure` 가 4개 제출 지점에서 관측 이벤트 `submit_failed`
  (message=원문) + `diag_logs` 합성 항목(`pod="submit:<phase>"`)을 남김(기록 실패는 종단을 안
  막음). API `get_job_logs` 는 phase_ref 없이도 박제를 200(archived, ref=null)으로 돌려주고,
  포탈 `JobViewer` 는 reason_code 의 `<phase>_submit_failed:` 접두사로 로그 탭을 만들어 그
  자리에서 원문을 보여준다.
- 테스트: `test_volume_name`(규칙·결정성·충돌·`/mgmt_storage` 로 Pod/vcjob 렌더 + 마운트 짝),
  `test_stepper_submit_failure_record`, `test_api_job_logs` 폴백, JobViewer 탭 2건. pytest 1803·
  vitest 694·tsc·빌드 외부 URL 0. ARCHITECTURE §6 불변식 추가.
- 실증(테스트베드 d133, 2026-09-15): 워크트리 코드로 /mgmt_storage 스토리지의 preflight Pod·Volcano Job(sync/scan 4종)을 렌더해 apiserver 서버측 dry-run 수용(rc=0), 볼륨 이름을 옛 방식 mgmt_storage 로 되돌리면 프로덕션과 동일한 422(RFC 1123; vcjob 은 Volcano admission 거부) 재현. 포탈 빌드 d133(commit 2555693) → 릴리스 dms-api·dms-controller Applied(live==manifest) → alice sync 회귀 잡 preview→confirm→Succeeded(files=2), 접근 매트릭스 소유자/관리자 200·타인 404. 오버레이 testbed newTag d133, guard exit 0. dms-ssc 에 체리픽·푸시(프로덕션은 git pull 후 포탈 빌드·릴리스로 반영).

### ✅ 스토리지 백엔드 식별자 추가 — DDN Lustre·Pure Storage·NetApp — **완료·실증**(2026-09-15, d132)

사용자 요청: 등록 백엔드에 DDN Lustre, Pure Storage("everpure" 로 표기), NetApp 추가. 사전
확인 결과 `backend_type` 은 검증(`_BACKENDS`)·저장·표시·진행 중 잡 변경 가드(409
storage_in_use)에만 쓰이는 라벨이고 마운트 프로브(mountinfo 마운트포인트 집합)·잡 볼륨
(hostPath=mount_path)·경로 해석(stepper._abs)은 백엔드를 보지 않는다 — 그래서 값만 더했고
런타임 분기·마이그레이션(컬럼에 CHECK 없음)은 없다. 식별자는 기존 관례(소문자 제품명)로
`lustre`·`purestorage`·`netapp`, 표시명은 DDN Lustre (EXAScaler) / Pure Storage (FlashBlade) /
NetApp (ONTAP). 셋을 함께 움직여야 하는 세 곳(서버 상수·프런트 BACKENDS·api.ts invalid_storage
문구)은 `tests/test_storage_backends.py` 가 순서열 일치로 고정한다. pytest 1787·vitest 692·
tsc·빌드 외부 URL 0.
- 실증(테스트베드 d132, 2026-09-15): 포탈 빌드 d132(commit 5372fd4) → 릴리스 dms-api·dms-controller Applied(live==manifest, 배지 applied). 라이브 API 로 lustre·purestorage·netapp 각각 create 201 → 목록에 식별자 그대로 → delete 200, cephfs→netapp update 200, 'everpure'·'Lustre'·'nfs' 는 422 invalid_storage, 잔재 없음(17/17 PASS, verify-backends.py). 서빙 번들(index-BPbKFE7N.js)에 세 표시명 포함. 오버레이 testbed newTag d132 로 bump.

### ✅ 사용량 분석 — 타깃 상세를 클릭한 행 아래 펼침(expand) — **완료·실증**(2026-09-14, d131)

사용자 요청: 항목을 클릭하면 상세가 모든 항목 아래(별도 카드)에 나오던 것을 expand 형태로.
`TargetDetail`(요약 타일·실 사용량 추이·온도 추이·스캔 이력)을 분리해 선택한 행 바로 아래
펼침 행(colSpan, 왼쪽 강조선, 회전 chevron)으로 렌더. 한 번에 하나: 같은 행 재클릭 = 접기
(URL 파라미터 제거), 다른 행 클릭 = 펼침 이동. URL(storage/target)이 그대로 진실이라 딥링크·
새로고침·뒤로가기 유지; 행 버튼 aria-expanded/aria-controls, 펼침 영역 role=region. 표시
창·온도 축 state 는 상세 국소(타깃 전환 시 초기화). 선택 타깃이 현재 목록(검색 결과)에 없으면
표 아래 폴백 카드(제목 포함). 테스트 2건 추가(펼침 이동·접기·다음 형제 행, 폴백), vitest
691·tsc·빌드 외부 URL 0.
- 실증(테스트베드 d131, 실 Chrome): main 푸시 → cron 동기화 → 포탈 빌드 d131(자리표시자 base, commit e1b8e79) → 포탈 릴리스 dms-api·dms-controller Applied → 배지 live == manifest d131 → 오버레이 newTag d131 bump 커밋 → Job 삭제·가드 통과·apply -k → diff 0. 화면: 타깃 5개 중 `ldap-e2e/growth` 클릭 → 그 행 **바로 아래** region(좌표 대조)·aria-expanded=true·URL `?storage=…&target=ldap-e2e%2Fgrowth`, 나머지 타깃은 펼침 아래에 이어짐(캡처 d131-usage-expanded-1.png); `ldap-e2e/group-shared` 클릭 → 펼침 이동(region 1개, 첫 항목 false; 캡처 -2.png); 재클릭 → region 0·URL `/admin/usage`.

### ✅ base 매니페스트 사이트 중립화 + 오버레이 apply 이미지 가드 — **완료·실증**(2026-09-14, d130)

사용자 보고(프로덕션): 소스를 git pull 하면 소스에 커밋된 테스트베드 태그(d1xx)가 배포를
덮어써 이미지 태그 문제가 난다. 진단: 포탈 빌드·릴리스 경로는 원래 사이트 값으로 일관됐고
(빌드는 이미지에 COPY 되는 사본만 스탬프, 릴리스는 build_registry + 레지스트리 실태그),
오염 경로는 base(`deploy/k8s`)에 커밋된 테스트베드 실 레지스트리·태그를 raw
`kubectl apply -f`·`apply -k deploy/k8s`(images 변환 없음)·낡은 values.env 로 재적용하는
것뿐이었다. 두 층으로 닫았다:
- **B: base 는 사이트 중립 자리표시자만**(`set-by-overlay.invalid/<img>:set-by-overlay`,
  image 6줄 + DMS_JOB_IMAGE). 실 태그는 오버레이가 넣는다 — 신규 `deploy/overlays/testbed`
  (테스트베드의 매니페스트-우선 단일 진실: newTag d130/d123 + DMS_JOB_IMAGE d110), prod/ssc
  는 `images.name` 을 자리표시자로. `.invalid` 라 raw apply 는 ErrImagePull 로 즉시 실패
  하고 `site_image` 가 동봉 자리표시자를 None(모름)으로 접는다. 계약 테스트 2건(base 전
  이미지 줄 자리표시자, 오버레이 images.name 일치).
- **가드**(`deploy/overlays/guard-images.sh`, `deploy/install.sh`·ssc install.sh 가 apply 직전
  호출): 렌더된 Deployment/DaemonSet 이미지가 라이브와 다르면 exit 3 거부, 의도한 변경은
  `ALLOW_IMAGE_CHANGE=1`, 첫 설치는 통과 — 포탈 릴리스 뒤 오버레이 태그를 안 맞춘 재적용이
  라이브를 옛 태그로 되돌리는 잔여 사고(C)를 막는다.
- 사전 dry-run(2026-09-14): testbed 오버레이 kustomize → 라이브 diff 0·서버 dry-run 통과;
  ssc/prod 샘플 values 렌더 → migrate·initContainer·DMS_JOB_IMAGE 포함 전부 사이트 값,
  자리표시자·pkg-01 잔여 0; 빌드 스탬프 sed 가 빌드 대상만 치환; pytest 1771. 부수 발견:
  테스트베드 agent 커밋값 d118 ≠ 라이브 d123(포탈 릴리스 후 bump 누락 — 같은 사고 유형).
- 실증(2026-09-14): main 푸시 → cron 동기화 → **포탈 빌드 d130**(자리표시자 base 에서;
  동봉 매니페스트 = dms 계보 5줄 d130, agent·잡 이미지 자리표시자 → site_image None) →
  **포탈 릴리스** dms-api·dms-controller Applied(migrate init 포함 d130) → 배지 원천:
  api/controller live == manifest d130, agent·job_image manifest 모름. 릴리스 직후 오버레이
  (d129)로 `kubectl diff -k` → 4곳 d130→d129 되돌림 예정(C 재현) → 가드가 exit 3 으로
  거부(두 Deployment 나열), 플래그 시 허용 → 오버레이 d130 bump 커밋·푸시 → Job 삭제 →
  가드 통과 → `apply -k` → diff 0. **raw `kubectl apply -f deploy/k8s/40-api.yaml` 실 재현**:
  새 파드 `ErrImagePull`(set-by-overlay.invalid DNS 실패)로 Pending, 기존 d130 파드가 계속
  서비스(readyz 200 유지), 오버레이 apply 로 d130 복구·롤아웃 완료. d130 에서 alice sync
  Succeeded·열람 매트릭스·3홉 정상. 완료된 migrate Job 은 불변이라 오버레이 태그 변경 뒤
  `kubectl diff -k` 가 exit 2 를 낸다 — README 절차대로 apply 전 Job 삭제(가드는 Job 을
  보지 않는다).

### ✅ 테스트베드·DMS 재기동 영속성 — 공유 FS 레이아웃 코드화 + 재부팅 실증 — **완료**(2026-09-09)

사용자 요청: 테스트베드·DMS 를 재기동해도 현재 상태(공용 디렉터리 770, base 755, 데이터
root `/cephfs/managed` + e2e 픽스처, 스토리지 managed_root)가 그대로이도록. 조사: 테스트베드
IaC(`make ceph`)는 CephFS 마운트만 하고 그 안의 디렉터리·픽스처는 손으로 만든 것이라
`make destroy` 재프로비저닝이면 사라진다(VM 재부팅·`vm-down/up` 은 OSD 볼륨이 남아 유지).
DB 상태(스토리지·control_state)는 PostgreSQL(pkg-01)에 있어 DMS 재기동과 무관.
- `deploy/testbed/dms-shared-fs.yml`(신규, 멱등): `/cephfs/dms` 770 · `artifacts` 755 ·
  `managed` 755 · `ldap-e2e` 픽스처(소유자·mode·내용 실물 그대로, growth old-* 는 고정 과거
  mtime) · nsync managed 디렉터리(마운트 있을 때만). 테스트베드의 ansible.cfg/인벤토리를 그대로
  쓴다(테스트베드 저장소는 이 세션의 워크트리 격리로 편집 불가 — Makefile 타겟 한 줄은
  플레이북 헤더에 스니펫). README §2 의 수동 mkdir 을 권한 포함으로 정정, §2b 에 영속성 절.
- 실증: 드라이런 = 현재 상태와 전부 일치(유일한 변경 old-*.bin mtime 고정) → 적용
  changed=2(mtime 2건 + nsync managed 2곳 생성) → 재실행 changed=0. DMS `rollout restart`
  (api·controller·agent) 정상. k8s 노드 6대 동시 재부팅(30초 내 복귀, fstab CephFS 자동
  마운트, 770/755/755 그대로, kubelet·crio active) → pkg-01 재부팅(14초 복귀, PostgreSQL·
  slapd·registry(restart=always)·ceph(cephadm systemd) 자동 기동, 볼륨 healthy) → 노드 Ready
  6대, DMS 파드 전부 Running(재시작 1회), readyz ok, 3홉 정상, 스토리지 `cephfs-dms=
  /cephfs/managed` Ready, control-state 유지. 재부팅 후 alice sync 잡(에이전트 첫 보고·프로브 뒤 제출) preview → 확인 → **Succeeded**(files 2·bytes 10), 열람 매트릭스 alice 200·mason 200·cocoa.song 404, 노드 홉 5대 정상.

### ✅ 공용 디렉터리 root:root 770 지원 — 잡 파드의 아티팩트 base 전용 마운트 — **완료**(2026-09-09, d129)

사용자 요청: DMS 공용 디렉터리(`/cephfs/dms`)를 root:root 770 으로 만들고 DMS 가 정상
셋업·동작하는지 확인. 실험(d128, 770 적용): 제어면·에이전트(전부 root)는 3홉 포함 정상
이지만 요청자 uid 로 도는 부분이 막힌다 — (1) 테스트베드는 `cephfs-dms` 의 managed_root
가 `/cephfs/dms` 로 드리프트해 있어(README §6 시드는 `/cephfs/managed`) alice sync 가
preflight 에서 10초 만에 `source_not_readable`, (2) 데이터가 밖이어도 잡 파드가 `/cephfs`
전체를 마운트해 rank.sh 실행·dscan 리포트 쓰기가 `/cephfs/dms` 를 통과해야 한다(에이전트
파드 `runuser -u alice` 프로브: dms-BLOCKED·artifact-BLOCKED). (2)는 DMS 설계 문제라
고쳤다: `execution_volcano._volumes` 가 아티팩트 base 를 스토리지 마운트와 별개의
**전용 hostPath 볼륨**으로 `/dms-artifact-base`(`artifact_base.ARTIFACT_MOUNT`)에 붙이고
`execution_manifests._artifact_dir` 이 그 파드 안 경로를 러너에 준다 — 마운트 루트 위의
호스트 부모 권한은 커널이 검사하지 않으므로 공용 디렉터리를 770 으로 잠가도 잡이 돈다.
호스트 경로(제어면 읽기·artifact_uri)는 그대로. 스토리지 mount_path 가 그 경로와 겹치면
`invalid_storage`. (1)은 배포 전제로 문서화(README §2b-3): 데이터 root 는 공용 디렉터리
밖. 잡 이미지 변경 없음(러너는 env 경로만 쓴다).
- 실증(테스트베드, 2026-09-09): 게이트 백엔드 1769 passed(신규 5). 순서대로 —
  ① `/cephfs/dms` 를 root:root 770 으로(에이전트 root 셸 `chown 0:0; chmod 770`), 요청자
  프로브 `runuser -u alice`: dms-BLOCKED·data-BLOCKED·artifact-BLOCKED. 제어면 3홉(API·
  컨트롤러·노드 5대)·readyz 는 전부 정상(root). ② d128(구 마운트) + 데이터 root
  `/cephfs/dms`: alice sync → 10s 만에 `source_not_readable`(preflight, 요청자 uid).
  ③ 픽스처를 `/cephfs/managed/ldap-e2e` 로 복사(cp -a, 소유권·mode 보존)하고 `cephfs-dms`
  managed_root 를 `/cephfs/managed` 로 PUT(README §6 시드 복원) → d128 에서 다시: preflight
  통과, preview 가 30s 만에 `preview_failed` — stderr "Open RTE was unable to open the
  hostfile: /cephfs/dms/artifacts/<job>/preview/mpi-hostfile"(요청자로 도는 mpirun 이 770
  부모를 통과 못 함). ④ d129 롤아웃(root·cap 0 유지) 후 같은 잡: preview → 확인 →
  **Succeeded**(files 2·bytes 10), 목적지 `/cephfs/managed/ldap-e2e/dest/rootcheck-d129b/
  g1.txt` 생성, vcjob(preview·execution) 파드 스펙 = 볼륨 `cephfs:/cephfs` +
  `dms-artifact-base:/cephfs/dms/artifacts→/dms-artifact-base`, `DMS_JR_ARTIFACT_DIR=
  /dms-artifact-base/<job>/<phase>`; 호스트 쪽 산출물은 그대로 `/cephfs/dms/artifacts/<job>/
  execution`(phase alice:dmsusers 755, root 산출물 3종 0644). 열람 매트릭스 alice 200·
  mason 200·cocoa.song 404, root 전용 부정 케이스(심링크·하드링크·rename·FIFO·mode 000 →
  404, allowlist 422, root:root 755 validate 200) 전부 유지, 트레이스백 0. `/cephfs/dms` 는
  770 root:root 로 **유지**한다(테스트베드 = 운영 조건).
- 관찰(BACKLOG): d129 롤아웃 직후 첫 잡은 옛 컨트롤러 파드가 종료 유예 중 마지막 스텝
  (preview vcjob 제출, 새 파드의 preflight 파드 2초 뒤)을 옛 코드로 수행해 구 마운트로
  실패했다 — 재실행은 성공. 롤아웃 겹침 창의 일반 현상이지 이 변경의 결함이 아니다.

### ✅ 제어면(api·컨트롤러) root 전환 + 봉쇄 사슬 강화 — **완료**(2026-09-09, d128)

사용자 지시: 운영 공용 디렉터리(아티팩트 base)가 `root:root` 라 3홉 검증이 비root
(65532)에서 실패한다 — api·컨트롤러를 root 로 돌리되 포탈 API 에 취약점이 생기지 않게
코드 에이전트 문서에 규칙을 박고, 구현 전 모든 케이스(preflight·아티팩트/로그 읽기
권한 등)를 실제 코드로 검토하라. 검토는 6영역(아티팩트 읽기·artifact base·정적/입력·
컨트롤러 fs·이미지/매니페스트·os.access/subprocess) 리뷰 + 발견마다 2렌즈 반박 검증
(에이전트 61개)으로 했고, 결과 MUST-FIX 를 전부 같은 커밋에 넣었다:

- **매니페스트**(`40-api.yaml`·`41-controller.yaml`): 컨테이너 수준 securityContext
  `runAsUser 0·runAsGroup 0·allowPrivilegeEscalation false·readOnlyRootFilesystem true·
  capabilities drop ALL`. 파드 수준이 아니라 migrate initContainer 는 65532 유지, Dockerfile
  `USER 65532` 유지(root 는 매니페스트만이 진실). 계약 테스트(`test_release_manifest_contract`
  root 절 7건 + `manifest_tags` securityContext 리더)가 모양·오버레이 우회까지 고정.
- **봉쇄 사슬 공용화**(`src/dms/artifact_files.py`, 신규): 단일 open(O_NOFOLLOW|O_NONBLOCK)
  → S_ISREG → **nlink==1** → **소유자 st_uid∈{0, 요청자 uid}** → fd realpath 봉쇄 → 크기
  상한. 65532 시절 EACCES 가 조용히 하던 3번째 장벽(rename/하드링크로 들여온 남의 파일)을
  소유자·nlink 검사가 대체한다. API 라우트 6곳(목록·뷰·다운로드·scan 통계 3종)이
  `owner_uid=job_owner_uid(job)` 를 넘기고(None 은 fail-closed 404), 목록도 같은 판정.
- **컨트롤러 summary.json**(`wiring.build_summary_reader`): 예전 `open(path).read()` 는 root
  로 심링크를 따라가 남의 파일을 요청자 잡 상세(result_summary)에 그대로 노출하고, mkfifo
  면 단일 스레드 컨트롤러 전체 정지, 다GB 면 OOM 크래시 루프, 비-UTF-8 이면 매 틱
  step_error 였다 — 같은 사슬로 열고 1MiB 상한·ValueError 도 None(summary_unavailable).
- **artifact base allowlist**(`DMS_ARTIFACT_BASE_ALLOWED_PREFIXES`, 기본 `/cephfs`): root 면
  존재하는 모든 디렉터리가 저장 가능해지므로 validate/PUT/GET 즉석 홉·컨트롤러 홉 모두
  allowlist → 쓰기 왕복 순. 새 사유 `artifact_base_outside_allowlist`(양쪽 등록).
- **신원 가드**(`stepper.identity_problem`): uid/gid 부재를 0 으로 기본값 처리하던
  execution_manifests 경로를 제출 전에 끊는다 — `identity_missing_at_step`(양쪽 등록).
  uid 0 은 privileged 짝이 맞으면 정당.
- **테스트**: root 로 pytest 하면 도달 불가한 EACCES 분기 2건 skipif(삭제 아님), 신규
  `test_artifact_files.py`(소유자·하드링크·심링크·FIFO·크기·비-UTF-8·경로 모양, 실제
  wiring 클로저 대상). 아티팩트를 읽는 API 테스트 픽스처는 요청자 uid 를 싣는다.
- **문서**: CLAUDE.md 규약 1개, ARCHITECTURE §3-11 + §7 「root 제어면」 14규칙, deploy/README
  §2b(배포 전제: base root:root 비-world-writable, 모든 호스트 protected_hardlinks/symlinks,
  allowlist, 검증 명령, 롤백), 40/41/00 매니페스트 주석 정정.
- **구현 후 적대적 리뷰(5렌즈 + 반박 검증, 세션 한도로 일부 검증은 직접 판정)** 반영:
  allowlist 파서가 `..`/`.` 성분을 정규화 전에 거부(`/cephfs/..` 가 realpath `/` 로
  allowlist 를 무력화하던 것, 검증됨) · base 는 **프로세스 euid 소유·비-world-writable**
  이어야 통과(`artifact_base_not_owned`/`artifact_base_world_writable`; root 면 allowlist
  아래 777 디렉터리가 그대로 통과했다 — 테스트베드 base 777 도 755 로 정정) · 컨트롤러
  리더 배선 고정 테스트(wiring 한 줄을 옛 open() 으로 되돌려도 초록이던 것, 검증됨) ·
  디렉터리가 uid 0 을 주는 비특권 요청은 계획 시점 `identity_root_without_privilege`
  (stepper 가드는 변조 행 백스톱) · `owner_uid` 를 네 공개 함수의 기본값 없는 키워드
  인자로(생략 = TypeError) · 매니페스트 파서가 흐름/블록 시퀀스를 같은 list 로 정규화
  (kubectl 출력 붙여넣기 호환) · 오버레이 그물을 모든 YAML + kustomization inline 까지 ·
  음수 uid·비-dict worker_pool 도 fail-closed · 문구(root 는 cap 없이 소유자 mode 의
  지배를 받는다, DAC_READ_SEARCH 금지, /tmp 없음) 정정.
- **배포 주의**: securityContext 는 매니페스트 필드라 **포탈 롤아웃(이미지 patch)만으로는
  실리지 않는다** — d128 은 `kubectl apply -f deploy/k8s/40-api.yaml -f 41-controller.yaml`
  을 한 번 반드시 거쳐야 하고, 드리프트 배지는 이미지만 비교하므로 이 어긋남을 못 본다
  (BACKLOG: securityContext.runAsUser 드리프트 관측).
- **보류(BACKLOG)**: 러너의 root 쓰기 심링크 추종(`runner.write_text`)과 root 산출물 3종
  chown — 잡 이미지 재빌드가 필요해 이번 범위 밖. 그때 API 소유자 검사를 `== 요청자` 로
  좁힌다.
- 실증(테스트베드 d128, 2026-09-09): 게이트 백엔드 1764 passed(신규 50)·vitest 689·tsc·
  dist 외부 URL 0. 배포: base `/cephfs/dms/artifacts` 를 777→`root:root 755` 로 정정 후
  `kubectl apply`(00/20/30/40/41) — api·controller 파드 `uid=0(root)`, CapPrm/Eff/Bnd 전부
  `0000000000000000`, `/app`·`/tmp` touch → Read-only file system, migrate initContainer
  securityContext 없음(65532)·Completed, ConfigMap `DMS_ARTIFACT_BASE_ALLOWED_PREFIXES=/cephfs`.
  3홉: API(즉석)·컨트롤러·노드 5대 전부 정상(실 Chrome 「모두 정상」 + 새 문구, 캡처
  `d128-artifact-base.png`). 잡: alice sync(ldap-e2e/group-shared → dest/rootcheck-d128)
  Planned 100s → ConfirmPending → confirm → **Succeeded**(files 2·bytes 10 — 컨트롤러가
  root 로 summary.json 을 봉쇄 사슬로 읽음); 열람 매트릭스 alice 200 · mason(admin) 200 ·
  cocoa.song 404 job_not_found(아티팩트·실행 로그·프리플라이트 로그 동일). 잡 디렉터리는
  root:root, phase 는 alice(10001:10000) 755, root 산출물 3종 0644. root 전용 부정 케이스
  (에이전트 root 셸로 alice 의 execution 디렉터리에 심음): 심링크→/etc/shadow ·
  하드링크(타인 1001 소유 0600, nlink 2) · rename(타인 0600, nlink 1) · FIFO · 자기 소유
  mode 000 → 전부 **404 artifact_not_found, 0.0s**, 목록에서 leak 제외, 다운로드도 404;
  root 소유 0644 와 stdout.log 는 200. allowlist: validate `file:///tmp`·`/etc`·`/cephfs2`
  → 422 artifact_base_outside_allowlist(readOnlyRootFilesystem 의 EROFS 보다 먼저),
  `/cephfs/dms/../dms/artifacts` → 422 artifact_base_traversal; root:root 755 디렉터리
  validate → 200 ok, 프로브 잔여 0. API·컨트롤러 로그 트레이스백 0, HTTP 500 0.

### ✅ 스토리지 등록 백엔드 선택 목록 — **완료**(2026-09-09, d127)

사용자 보고: 스토리지 등록에서 이름 gpu1·마운트 /home/gpu1·관리 루트 /home/gpu1·
백엔드 "IBM GPFS" 로 저장하니 "스토리지 설정이 올바르지 않습니다". 원인: 서버
(`repositories/storages._BACKENDS`)는 `cephfs`·`gpfs`·`wekafs` 식별자만 받는데 화면의
백엔드 칸이 자유 입력이라 표시명이 그대로 갔다(이름·경로는 규칙 통과). 수정: 백엔드를
선택 목록(CephFS / IBM GPFS (Storage Scale) / WekaFS → 식별자)으로, 기존 행의 알 수
없는 값은 편집 시 옵션으로 보존, `invalid_storage` 문구에 이름·경로·관리 루트·백엔드
규칙 명시. (즉시 우회: 백엔드에 `gpfs` 입력.)

### ✅ 설치기 이미지 push 단계 + 신규 사이트 "레지스트리 연결 불가" 오진 수정 — **완료**(2026-09-09, d126)

사용자 보고: 초기 구축 시 install(-ssc).sh 에 push 단계가 없어 레지스트리에 이미지가
없고, 포탈이 "레지스트리 연결 불가"를 보였다. 요청: 설치기에 push 를 넣되(반입과
별개로 자동), 실패해도 파드 구동엔 영향 없다는 것을 WARN 으로 구분해 알릴 것.

- `deploy/install.sh`(prod)·`deploy/overlays/ssc/install.sh`(dms-ssc) 1b 단계
  `push_images`: 이 호스트의 podman/docker 에 있는 dms·dms-agent·dms-mpifileutils·
  buildah 를 values.env 의 REGISTRY/태그로 push. 로컬 이미지 없음·push 실패·
  podman/docker 없음은 전부 **WARN**(설치 계속) — 노드에 반입된 이미지면 파드 구동은
  무관하고 포탈 레지스트리/릴리스/빌드(FROM pull)만 영향이라는 문구를 붙인다.
  끝에 레지스트리 시점의 리포별 태그를 조회해 OK/WARN 으로 보여준다(정보).
  `REGISTRY_TLS_VERIFY=false`(values.env) 면 podman push `--tls-verify=false`.
  `--dry-run` 은 push 대상만 나열.
- 오진 수정: `registry.fetch_repo_tags` 가 404(리포 없음, v2 NAME_UNKNOWN)를
  "연결 불가"(None)로 접던 것을 빈 목록(`[]`)으로 — 신규 사이트의 릴리스 화면
  `registry_ok=false` 오류·레지스트리 화면 "조회 실패"가 사라지고 "태그 없음"으로
  정직하게 보인다. 연결 실패·5xx 는 여전히 None. 문구도 "주소·네트워크 확인 /
  신규 사이트는 빈 목록" 으로 구체화.

### ✅ 컨트롤 상태 no_proxy 힌트(사이트 실제 값) — **완료·실증**(2026-09-09, d126)

사용자 요청: 컨트롤 상태 화면의 no_proxy 힌트로 레지스트리 주소·주소:포트·localhost·
127.0.0.1·.svc·.cluster.local·워커 노드 주소를 포탈에 보여 달라.

- `GET /api/admin/control-state/proxy-hints`(admin): `DMS_BUILD_REGISTRY` 에서
  host/host:port, 고정 항목(localhost·127.0.0.1·.svc·.cluster.local), 워커 노드
  InternalIP(control-plane 라벨 제외, IP 모르는 노드 생략). 노드 조회는 fail-soft
  (`nodes_known=false`). `auto_added` 로 서버가 저장 시 자동으로 보태는 항목도 함께.
- 노드 IP 출처: 에이전트 보고에는 IP 가 없어 API 가 k8s Node 를 읽는다 —
  `KubernetesClient.list_node_addresses`(순수 파서 `node_addresses_from`) +
  **RBAC ClusterRole `dms-api-nodes-readonly`(nodes get/list)** 추가. 기배포 사이트는
  `10-rbac.yaml` 재적용(오버레이 apply) 필요 — 없으면 힌트가 노드 IP 만 생략한다.
- 화면: 프록시 제외 입력 아래 "권장 값: …" + 「권장 값 채우기」 버튼 + 워커 노드 수.
- 실증(테스트베드 d126): RBAC 적용 후 `kubectl auth can-i list nodes --as=
  system:serviceaccount:dms:dms-api` = yes; `GET /api/admin/control-state/proxy-hints`
  = registry pkg-01:5000 / host pkg-01, nodes = dms-w1~w5(10.10.10.11~15, dms-cp1
  제외), suggested = pkg-01,pkg-01:5000,localhost,127.0.0.1,.svc,.cluster.local,
  10.10.10.11~15. 실 Chrome: 컨트롤 상태 화면 힌트 문구 동일 + 「권장 값 채우기」로
  입력이 그 목록으로 채워짐(캡처 d126-no-proxy-hint.png).

### ✅ 사내 프록시 CA(TLS 가로채기 프록시) — **완료·실증**(2026-09-09, d125)

사용자 요청: 빌드 이미지에서 사내 프록시를 쓰려면 CA 가 필요하다 — 사내 CA 경로를
포탈에서 입력하면 반영되게. (확인: CA 파일·경로는 빌드 노드에만 있으면 된다.)
사용자 지적: `--tls-verify=false` 는 push 전용이라 해결책이 아니고, 파드 스펙은
코드가 생성하므로 밖에서 `-v pip.conf` 류를 끼워 넣을 수 없다 — 즉 CA 주입 경로가
build_manifests.py 에 있어야 한다.

- `control_state.build_proxy_ca_path`(CREATE + _ensure_columns, 전수 그물 36→37),
  PUT 검증 `invalid_proxy_ca_path`(절대 경로·셸 문자 없음), 컨트롤 상태 화면 입력.
- 빌드 파드: 파일 hostPath(type File) → `/etc/dms-proxy-ca/ca.crt`; 스크립트가
  시스템 번들(Fedora `/etc/pki/tls/certs/ca-bundle.crt` 또는 Debian) + 사내 CA 합본을
  만들어 buildah 자신은 `SSL_CERT_FILE`, RUN 단계는 `-v /tmp/dms-proxy-ca:
  /etc/dms-proxy-ca:ro` + `--env`(SSL_CERT_FILE·NODE_EXTRA_CA_CERTS·npm_config_cafile·
  PIP_CERT·REQUESTS_CA_BUNDLE·CURL_CA_BUNDLE·GIT_SSL_CAINFO) + 같은 키 `--unsetenv`.
- 프로브: 부모 디렉토리 hostPath(type 없음) → 존재·PEM 검사
  `build_proxy_ca_missing`, 프록시 CONNECT 위 TLS 핸드셰이크(시스템 CA + 사내 CA)
  `build_proxy_tls_failed`(발급자 로그).
- 부수: 2026-09-09 실사고 재발 방지 — 롤링 중 새 파드의 migrate 가 컬럼을 더하면
  옛 파드의 psycopg 준비 문장(`SELECT *`)이 "cached plan must not change result
  type"(FeatureNotSupported)으로 영원히 실패해 control_state 를 읽는 모든 요청이
  500 이었다(같은 태그 재적용으로 롤아웃이 없어 11시간 방치). db.py 죽음 판정에
  세 번째 모드로 추가: 커넥션은 살아 있어도 재연결(빈 캐시) + 1회 재시도.
- **실증(테스트베드, d125)** — luminous 에 TLS 가로채기 프록시(proxy.py
  `--ca-key-file/--ca-cert-file/--ca-signing-key-file`, 자체 생성 CA "DMS Test
  Intercepting Proxy CA")를 세우고 `ssh -R 7229:127.0.0.1:3129 dms-w1` 로 빌드 노드
  loopback 에 걸었다(호스트 네트워크 모드와 결합). 노드에서 `curl -x
  http://127.0.0.1:7229 https://pypi.org` = CA 없이 exit 60(TLS 실패), `--cacert` 로 200.
  1. Before(d124, CA 미지정): 빌드 45초 만에 `build_failed` — buildah 가 베이스
     이미지(node:20-bookworm-slim) pull 의 `registry-1.docker.io` 인증서 검증에서
     죽음(사용자가 예상한 정확한 증상).
  2. After(d125, CA 경로 `/home/ubuntu/dms-proxy-ca.pem`): 프리플라이트 로그 `tls via
     proxy ok host=quay.io issuer=DMS Test Intercepting Proxy CA`; 빌드 파드에
     `proxy-ca` hostPath(type File) → `/etc/dms-proxy-ca/ca.crt`, env
     `DMS_BUILD_PROXY_CA`; 스크립트 `bundle_certs=147`(시스템 146 + 사내 1); dms
     이미지 **빌드 성공 195초**. 가로채기 프록시 로그에 registry-1.docker.io·
     auth.docker.io·cloudfront(buildah pull) + registry.npmjs.org(npm ci) +
     pypi.org/files.pythonhosted.org(pip) + dl.k8s.io(curl) + deb.debian.org(apt) —
     즉 buildah 자신과 RUN 단계 컨테이너 모두 재서명된 인증서를 사내 CA 로 통과했다
     (`--env` 가 RUN 단계에 적용됨을 실증).
  3. 최종 이미지 검사(`kubectl run pkg-01:5000/dms:ca-after`): CA 관련 env 0개,
     `/etc/dms-proxy-ca` 없음, 파이썬 기본 신뢰 저장소 정상 — `--unsetenv` 가
     런타임 이미지를 오염시키지 않았다.
  4. 부정: 없는 경로 `/home/ubuntu/no-such-ca.pem` → 25초 만에
     `build_proxy_ca_missing`; 다른 CA(테스트베드 포탈 CA) → 30초 만에
     `build_proxy_tls_failed`(프로브가 그 CA 로 핸드셰이크를 검증하므로 "CA 가
     틀렸다"를 빌드 전에 알린다).
  정직한 한계: 인증 프록시(Basic)는 미지원. apt 는 http(deb.debian.org)라 CA 와 무관.
  git 은 이번 dms 단독 빌드에 없었으나 `GIT_SSL_CAINFO` 로 같은 번들을 받는다
  (mpifileutils 빌드 시 github.com clone).

### ✅ 빌드 파드 호스트 네트워크 모드(loopback 프록시) — **완료·실증**(2026-09-09, d123)

사용자 보고: 프록시를 `ssh -R` 리버스 터널로 빌드 노드 호스트의 `localhost:7227`
에 걸어 쓰는데, 빌드 파드는 자기 네트워크 네임스페이스라 파드 안의 127.0.0.1 이
파드 자신이어서 닿지 않는다 — hostNetwork 우회 검토·구현 요청.

- 검토: 파드 `hostNetwork` 만으로는 부족하다. buildah 의 RUN 단계는 기본
  `--network=private` 로 자기 netns 를 또 만들어 npm/pip/apt 가 보는 localhost 는
  RUN 컨테이너 자신이다. 프리플라이트 프로브도 같은 네트워크여야 CONNECT 검사가
  거짓 실패하지 않는다.
- 구현: `host_network_for(proxy)` — 프록시 호스트가 loopback(`localhost`/`127.0.0.1`/
  `::1`)이면 자동, 아니면 `control_state.build_host_network` 스위치(컨트롤 상태
  체크박스). 켜지면 빌드·프로브 파드 `hostNetwork: true` + `dnsPolicy:
  ClusterFirstWithHostNet`(NO_PROXY 대상인 사내 레지스트리 이름을 클러스터 DNS 로
  풀기 위해) + 빌드 스크립트 `buildah bud --network=host`(env
  `DMS_BUILD_NETWORK=host`). 컬럼 1(CREATE + _ensure_columns, 전수 그물 35→36).
  현재 상태 카드가 "호스트 네트워크(자동 — 프록시가 127.0.0.1)" / "(스위치)" /
  "파드 네트워크" 로 판정 근거를 보여준다.
- **실증(테스트베드, 사용자 구성 재현)**: luminous 의 proxy.py(127.0.0.1:3128) 를
  `ssh -R 7227:127.0.0.1:3128 dms-w1` 로 빌드 노드에 걸어 dms-w1 에는
  `127.0.0.1:7227`/`[::1]:7227` 만 리슨(sshd 기본 GatewayPorts=no).
  1. Before(d122): 프록시 `http://127.0.0.1:7227` → 30초 만에 `build_proxy_unreachable`
     (프로브 파드 hostNetwork 없음) — 사용자 증상 그대로.
  2. After(d124): 같은 설정 → 프로브·빌드 파드 `hostNetwork=true`,
     `dnsPolicy=ClusterFirstWithHostNet`, podIP = 노드 IP 10.10.10.11, 빌드 파드 env
     `DMS_BUILD_NETWORK=host`; dms 이미지 빌드 **성공 135초**. 프록시 액세스 로그
     (클라이언트 = 터널의 luminous 쪽 127.0.0.1)에 registry-1.docker.io·npmjs·
     pypi·deb.debian.org·dl.k8s.io — 즉 buildah pull 과 RUN 단계(npm/pip/apt/curl)
     모두 호스트 loopback 의 터널을 탔다(파드 hostNetwork + `--network=host` 조합의
     증명).
  3. 부수 실측 — d123 태그 충돌: 전날 포탈에서 같은 태그(d123)로 3이미지 빌드·릴리스가
     있었고 제 d123 빌드가 레지스트리를 덮어썼지만 노드 캐시(IfNotPresent)의 옛 d123
     이 그대로 떴다(BACKLOG 의 "태그 재사용 = stale" 그대로). d124 로 재빌드. 커밋
     5ef2cee 메시지의 "체크아웃 경합" 서술은 오진이며 이 항목이 정정본이다.

### ✅ 빌드 프록시 + 신규 사이트 태그 누출 차단 — **완료·실증**(2026-09-08, d120/d121)

사용자 보고(프로덕션 SSC 클러스터에서 dms-ssc 셋업 후): "빌드 화면에 이미지 태그가
d119 로 나온다 — 신규면 d1 이 맞다", "빌드 노드가 프록시로만 인터넷에 닿는다 —
http_proxy/https_proxy 를 포탈에서 설정하게 하고 빌드로 실증해 달라".

**태그 누출 원인**: 이미지에 동봉된 `deploy/k8s` 는 소스 트리의 값이라, 포탈 밖에서
부트스트랩한 이미지는 테스트베드 커밋값 `pkg-01:5000/dms:d119` 를 그대로 담는다.
서버는 그것을 "이 사이트의 매니페스트 기준"으로 읽어 드리프트 배지·레지스트리
"사용 중"에 남의 태그를 실었고, 포탈 빌드의 스탬프 sed 는 `$DMS_BUILD_REGISTRY/img:`
로만 맞춰 다른 레지스트리 사이트에서는 한 줄도 안 맞아 **조용히 무동작**이었다.
빌드 화면의 제안 태그는 라이브 태그 하나만 봤다.

- `manifest_tags.site_image`: 동봉 이미지의 레지스트리 ≠ `DMS_BUILD_REGISTRY` 면
  None(모름) — routes_metrics(드리프트)·routes_registry(사용 중) 적용. 스탬프는
  `[registry]/<img>:<tag>` 전체 치환(dms/dms-agent 콜론 경계 유지, 테스트가 sh 로
  실제 sed 를 실행해 고정). 빌드 화면 제안 = 라이브·레지스트리 dms 태그·빌드 이력의
  dNN 최대+1, 없으면 **d1**; 라이브 태그는 정직하게 그대로 표시.
- **빌드 프록시**: `control_state.build_http_proxy/https_proxy/no_proxy`(CREATE +
  _ensure_columns, 전수 그물 32→35), PUT control-state 검증(`invalid_proxy_url`:
  http(s)://host[:port] 만·자격증명 거부, `invalid_no_proxy`), 컨트롤 화면 입력 3종
  + 현재 상태·이력 diff, BuildRunner 가 제출 시점 콜러블로 읽어 빌드·프로브 파드
  env(HTTP(S)_PROXY/NO_PROXY 대소문자, NO_PROXY 에 레지스트리·localhost 자동), 프로브
  egress 는 CONNECT 터널 검사·`build_proxy_unreachable`.
- **d120 실사고와 교훈**: 프록시 컬럼의 CREATE TABLE 주석에 적은 `user:pass@` 를
  PostgreSQL 어댑터(`db._NAMED` → `%(name)s`)가 `:pass` 파라미터로 읽어 migrate 가
  "query parameter missing: pass" 로 죽었다(SQLite 는 주석을 그대로 넘겨 전 테스트
  초록). 구 파드(d119)가 계속 서비스해 무중단이었고 d121 로 재빌드. 회귀 그물:
  파라미터 없이 실행되는 모든 마이그레이션 문장(주석 포함)에 `:name` 토큰이 없음을
  PostgreSQL 방언 대역으로 고정(`test_no_migration_statement_smuggles_a_named_placeholder_in_comments`).
- **워처 관용(d122)**: 프로브 create 의 일시 오류(파드는 생겼는데 응답만 잃음)를 즉시
  Failed 로 못박아 실제 프로브가 찍은 `build_proxy_unreachable` 대신 `submit_failed`
  가 남은 실측 → poll 오류와 같은 I6 관용구(경고 로그 + 다음 틱 재시도, 영구 오류는
  프리플라이트 타임아웃 회수). 빌드 파드 제출 실패는 k8s 오류 문구를 log_text 로 박제.
- **실증(테스트베드, 컨트롤러 d121, 프록시 = luminous `proxy.py` 10.10.10.1:3128)**:
  1. 부정: 프록시를 닿지 않는 `:3129` 로 저장 → 빌드 제출 30초 만에
     `build_proxy_unreachable`(프로브 로그 `proxy=http://10.10.10.1:3129`).
  2. 긍정: `:3128` 로 저장 → 3이미지(dms-mpifileutils·dms·dms-agent, 태그 px1) 빌드
     **성공 260초**. 빌드 파드 env = `HTTP_PROXY/HTTPS_PROXY=http://10.10.10.1:3128`,
     `NO_PROXY=pkg-01:5000,pkg-01,localhost,127.0.0.1`(+소문자), 프로브 env
     `DMS_PF_PROXY`. 프록시 액세스 로그(클라이언트 = 빌드 노드 10.10.10.11):
     `CONNECT registry-1.docker.io/auth.docker.io/production.cloudfront.docker.com`
     (buildah 베이스 pull) · `CONNECT registry.npmjs.org`(npm ci) · `CONNECT
     pypi.org/files.pythonhosted.org`(pip) · `GET deb.debian.org:80/...`(apt, 평문) ·
     `CONNECT github.com`(mpifileutils 의존 git clone) · `CONNECT dl.k8s.io`(kubectl
     curl) · `CONNECT quay.io`(프리플라이트). **pkg-01 항목 0건** — push/pull 은
     NO_PROXY 로 프록시를 우회했다. 즉 buildah 가 자기 env 를 RUN 컨테이너·pull 에
     전파하므로 **HTTP_PROXY/HTTPS_PROXY/NO_PROXY(대소문자) 만으로 충분**하다는 것이
     클라이언트 종류별로 확인됐다(사용자 질문 "http_proxy, https_proxy 만 하면
     되는지"에 대한 답: 그 둘 + no_proxy 에 사내 레지스트리, 그리고 서버가 자동으로
     보탠다).
  3. d122(dms) 도 프록시 상태로 빌드해 두 번째 확인 후 프록시 해제·적용.
  정직한 한계: 테스트베드는 직접 egress 도 열려 있어 "프록시 없이는 실패한다"는
  증명이 아니라 "모든 클라이언트가 프록시 env 를 따른다"는 증명이다. 인증 프록시
  (Basic)는 미지원(평문 자격증명 저장 금지) — 프록시 쪽 IP allowlist 로 푼다.

### ✅ 웹 인증 하드닝: 로그인 감속 + 비밀번호 전송 봉인 — **완료·실증**(2026-09-07, d119)

웹 보안 검토(2026-09-07, dms-ssc 기준)에서 남은 두 갭을 닫았다. 사용자 결정:
"로그인 rate limiting(1분 10회) + 사용자 비밀번호 암호화, 전 코드베이스 정밀 적용".

**전수 감사 결과(구현 전)**: 저장은 이미 전 경로 scrypt(`accounts._hash_password`:
create·set_password·reset_password) — 갭은 **전송**이었다. 브라우저→API 본문의
`password` 가 평문이고 TLS 는 ingress 에서 끝나 클러스터 내부 hop·TLS 검사 프록시·
인증서 경고를 무시한 접속에서 그대로 보인다. 부수 누출: FastAPI 기본 422 가 오류
항목 `input` 에 본문(비밀번호 포함)을 에코.

- **전송 봉인** (`api/password_transport.py` ↔ `lib/passwordTransport.ts`): 서버
  P-256 키를 `DMS_SESSION_SECRET` 에서 HKDF 로 결정적 유도(레플리카 동일·DB 없음·
  시크릿 회전 = 키 회전) → 브라우저 임시 키 ECDH → HKDF-SHA256 → AES-256-GCM,
  AAD 에 용도|사용자명 바인딩. 비밀번호를 받는 **네 경로 전부**(login/signup/
  password-reset/admin accounts) 가 `_password_from` 하나를 거치고, 프런트 훅 넷은
  `postWithSealedPassword` 하나를 거친다(평문 `password` 는 와이어에 실리지 않음).
  라이브 정책 `DMS_PASSWORD_ENCRYPTION_REQUIRED=true`(from_env 기본) 는 평문을 422
  `password_encryption_required` 로 거절; x-admin-token 부트스트랩만 평문 허용.
  kid 불일치는 별도 사유(프런트가 키 재수신 후 1회 재시도), 나머지 실패는 한 사유
  (복호 오라클 방지). WebCrypto 없는 평문 접속은 평문으로 떨어지지 않고
  `password_encryption_unavailable` 로 멈춘다. 의존성 `cryptography` 추가(빌드 타임).
- **로그인 감속** (`api/login_limiter.py`): 사용자명·클라이언트 IP 키별 슬라이딩
  60s/10회 **실패** 계수, 상한이면 검증 **전에** 429 `login_rate_limited` +
  `Retry-After`. 거절된 시도·봉인 오류는 세지 않는다(영구 잠금 DoS 방지), 성공은
  user 키를 비운다. 상한 도달 시 키당 1회 `events(auth/login_rate_limited)`. IP 는
  X-Real-IP → XFF 마지막 → 소켓(첫 항목은 위조 가능). 0 = 명시적 비활성.
- **422 에코 차단**: RequestValidationError 핸들러가 `type/loc/msg` 만 남긴다.
- 사유 코드 6종 추가(양방향 계약), ConfigMap 키 3종, e2e E1 이 실브라우저 봉인
  본문(평문 없음)을 단언, Python↔WebCrypto 상호운용은 e2e + 테스트베드로 실증.
- **실증(테스트베드, dms:d119 = 커밋 2d7a6d2, 에이전트 d118 유지)** — 적용 전 임시
  파드로 이미지 검증(cryptography 50.0.1·새 모듈 import·번들에 transport-key·
  동봉 매니페스트 새 키). 라이브 `https://dms.local`:
  1. 평문 로그인(정답) → **422 `password_encryption_required`**
  2. 봉인 로그인(파이썬 `seal_with_info`) → **200** + `Set-Cookie dms_session … secure`
  3. 봉인 오답 10회 → 401 ×10, 11번째 **정답** → **429 `login_rate_limited`,
     `Retry-After: 60`**; 형식 오류 422 응답에 본문 문자열 에코 없음
  4. 61초 뒤 봉인 정답 → 200(창 만료로 자동 해제)
  5. 실 Chrome(playwright, vite dev → port-forward → 라이브 파드): 로그인 폼 제출
     본문 키 = `username, password_enc`(평문 `password` 키 없음, kid = 라이브 키
     `7215cbe0…`), 요청 순서 transport-key → login(200) → me, 대시보드 도달; 별도
     컨텍스트에서 오답 10회 후 정답 → 429 + 화면에 "로그인 시도가 너무 많습니다 —
     1분 뒤 다시 시도하세요" 표시.
  게이트: backend 전체 스위트(1656) · vitest 676 · tsc · e2e 9/9 · dist 외부 URL 0.
