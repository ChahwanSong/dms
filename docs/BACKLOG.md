# DMS 백로그 (열린 항목)

**남은 일만** 적는다. 완료된 것은 [`CHANGELOG.md`](CHANGELOG.md), 지금 동작은
[`ARCHITECTURE.md`](ARCHITECTURE.md), 왜 그렇게 지었나는
[`docs/history/specs/`](history/specs/).

각 항목은 근거를 `파일:줄`로, 성격을 아이콘으로 표시한다:
🔧 착수 가능(결정만 하면 됨) · ⏸ 의도적 보류(결정이지 결함 아님) · 🔒 외부 자원에 막힘 · 📝 문서/알려진 해석.

**현재 상태**(2026-08-13): 슬라이스 1~30 완료. 배포 태그 제어면 `dms:d41`, 에이전트
`dms-agent:d35`, 잡 러너 `dms-mpifileutils:d35`(태그가 갈리는 건 정상 — 슬라이스마다
바뀐 이미지만 올린다). 슬라이스로 묶인 계획은 더 없다 — 아래는 전부 기능 확장·운영
결정·알려진 제약이다.

---

## 1. 기능 확장 (사용자가 보류 결정)

포탈 기능면에서 슬라이스 26 이 범위를 자른 것들. 결함이 아니라 아직 안 만든 기능이다.

- 🔧 **아티팩트 보존 정책(자동 만료)** — 결과 파일 **삭제**는 작업(요청) 선택 삭제(2026-10-08, CHANGELOG)로
  해소됐다: 관리자가 종단 요청을 지우면 DB 행과 함께 결과 파일·남은 파드·로그까지 정리된다. 남은 것은 시간
  기반 자동 만료(N일 지난 종단 잡의 산출물). 넣으면 같은 길이어야 한다 — 컨트롤러는 `<base>/<job_id>` 떼어냄
  (`artifact_trash.detach`, renameat)까지, 실제 삭제는 purge 파드(`purge_runner.build_purge_pod`, ARCHITECTURE
  §7 규칙 13). 정할 것: DB 행을 남기고 파일만 만료할지(그러면 아티팩트 열람 404 를 「만료됨」으로 보일 문구와
  `artifact_uri` 처리 필요), 아웃박스(`request_purges`)를 잡 단위로 넓힐지.
- 🔧 **작업(요청) 삭제 후속**(2026-10-08 범위 밖):
  - **단건 삭제 버튼(요청 상세)** — 지금은 작업 목록의 일괄 선택뿐(`POST /api/admin/requests:delete`). 같은
    저장소 메서드(`RequestPurgesRepository.delete_terminal`)를 쓰므로 버튼 + 같은 엔드포인트에 id 1개면 된다.
  - ~~**삭제된 배치의 자식**~~ — **해소**(2026-10-10, CHANGELOG 「전체 작업: 배치 단위 삭제 + 배치 열」): 배치 행이
    없는 자식 묶음(dangling batch_id)도 작업 목록에서 「기록 없음 xxxxxxxxxxxx」 묶음으로 **배치 단위** 삭제된다
    (`request_purges.delete_batch`). 개별 삭제 거부(`batch_child_not_deletable`)는 그대로다. 배치 행·자식 없이 **항목만**
    남은 묶음(옛 add_item·replace_items 경합, 롤링 업데이트 겹침 동안 옛 api 파드의 add_item 잔재)도 같은 메서드가
    expected 0 으로 지운다(2026-10-11) — 다만 화면엔 보이지 않으므로(자식 행이 없다) API 로 batch_id 를 지정해야 한다.
  - **작업 1000개(또는 항목 10000개)를 넘는 배치의 삭제**(2026-10-10 범위 밖) — 배치 하나 = 트랜잭션 하나이고 그동안 api
    전체가 DB RLock 으로 멈춰(replicas 1·커넥션 1) 상한 `MAX_BATCH_DELETE_CHILDREN = 1000`·`MAX_BATCH_DELETE_ITEMS = 10000`
    (`repositories/request_purges.py`)을 넘는 배치는 `batch_delete_too_large` 로 거부한다(목록 요약도 자식 수를 1001 에서
    멈추고, 항목 수는 batches.item_count 로 실어 포탈은 둘 다 「삭제 상한 초과」로 보인다 — 거부 판정은 상한 + 1 행까지만
    읽는다). 운영 배치 규모(항목 수 × 재스캔 횟수) 확인 필요. 후보: 묘비 상태 `Deleting`
    (배치를 먼저 그 상태로 CAS — 재실행·항목 추가가 되살리지 못하게) + 자식 청크 트랜잭션 + 마지막에 배치 행 삭제, 재개
    의미(중간에 api 가 죽으면 다음 호출이 이어서). 함께: `:rescan` 의 무조건 `set_status`(`api/routes_batches.py`
    `rescan_batch`)를 상태 CAS 로(지금은 삭제 직후 거짓 200 이 가능 — `update_meta` 도 같은 성질), 포탈 상태 라벨.
  - **배치 기록 삭제(배치 화면)의 감사·CAS** — `DELETE /api/admin/batches/{id}`(`repositories/batches.py` `delete`)는
    읽고-지우기라 CAS 가 없고, 감사 행이 없고, 공유 토큰으로도 된다(require_admin). 배치 기록만 지우므로 증거 손실은
    적지만(자식의 감사는 배치 단위 삭제 때 남는다) 일관성을 위해 `('batch','delete_record')` 감사 행 검토.
  - ~~**작업 목록의 배치 필터 / 배치 상세의 「이 배치의 작업 보기」**~~ — **해소**(2026-10-11 검증 2차 수정): 목록 API
    `GET /api/user/requests?batch_id=`(좁히기만 -- 비관리자는 여전히 자기 작업만), 포탈 `/jobs?batch=<id>`(필터 표시 +
    「필터 해제」), 배치 상세 머리줄·「배치 삭제」 창의 링크, 「기록 없음」 칸의 링크. 배치 열의 긴 이름은 끝(가르는 부분)을
    늘 보인다(앞 조각 말줄임 + 뒤 조각).
  - **작업 목록 검색의 후속**(2026-10-25 검색칸 추가 — `RequestsRepository.list` docstring): (1) 부분 일치는 인덱스를 못 타
    드문 검색어면 범위 전체를 훑는다(PG16 32만 행 0.25~0.34s, 단일 커넥션 RLock — 포탈은 검색 중 폴링을 10초로 늦춘다).
    느려지면 `pg_trgm` GIN(대상 표기 식·requester_id)을 검토 — PG 전용 DDL·확장 권한·2글자 검색어 무효가 따른다. (2) 슬라이스
    36 이전 형식의 sync·rm 키(끝에 옵션 지문)가 남아 있으면 그 행은 hex 검색어에 거짓 일치할 수 있다(scan 만 지문을 뗀다).
    (3) 요청 ID 부분 일치·단일 통합 검색창은 넣지 않았다(사용자가 말한 축은 요청자·배치 이름·대상 경로).
  - **요청 상세·결과 카드의 배치 링크** — `features/jobs/RequestDetail.tsx` 의 「배치 xxxxxxxx」 링크는 역할 검사가
    없고(비관리자에겐 403 화면), 배치 기록이 지워졌으면 404 로 가는 죽은 링크이며, 8자 표기라 작업 목록 배치 열(이름 또는
    12자)과 다르다. `OutcomeCard.tsx` 의 배치 상세 링크도 dangling 이면 죽는다. 목록 API 와 같은 배치 요약(존재·이름)을
    상세 응답에도 실어 같은 규칙(작업 목록 `BatchCell`)으로 그리면 된다.
  - **정리 루프 처리량** — 틱(15초)당 20행(`request_purger.py` `ROW_LIMIT`)이고, 파드가 남은 행은 첫 스윕(삭제 →
    Terminating)과 둘째 스윕(사라짐 확인) 두 번이 필요해 실처리량은 틱당 ~10행이다 — 작업 1000개 배치는 ~27분(2026-10-10
    검증 실측, 가짜 k8s), 한 호출의 최대(배치 10 × 1000)는 4~5시간이고 그동안 다른 삭제의 정리도 같은 줄(재시도 시각 순)에
    선다. 순서를 바꿔도(스윕한 행 먼저) 총 스윕 수가 같아 총 시간은 그대로다 — 줄이려면 상한을 올리거나(50, 틱 예산
    `TICK_BUDGET_SECONDS` 안에서 실 apiserver 지연 실측 후) 같은 틱 끝에 방금 스윕한 행을 한 번 더 세는 것. 거짓 지연은
    해소됐다(대기를 첫 k8s 스윕부터 잰다 — 예전엔 requested_at 부터 재서 줄 선 시간이 `purge_waiting_pods` 로 찍혔다).
  - **정리 루프 틱 비용이 대기열 크기에 비례**(2026-10-11 검증 지적, 일부 해소) — purge 파드를 못 띄우는 동안(purge_no_node
    등) 큰 배치 삭제의 행이 purging 에 쌓이면 전역 단계(`_reap`·`_settle` 의 `_live_rows`)가 틱마다 두 번 purging·files 행
    **전부**를 읽고, `_complete` 가 purging 행마다 FS stat(`artifact_trash.entry_states`)을 한다. 이 시간은
    `TICK_BUDGET_SECONDS` 에 들지 않고, 컨트롤러는 루프를 한 프로세스에서 차례로 돌려 planner·stepper 틱까지 밀린다.
    원 행 재등장 확인은 묶음 한 번으로 바꿨다(`present_targets` — 로컬 PG 1만 행 1.3초 → 0.37초). 남은 선형 비용:
    `in_stage` 전량 읽기 + 행마다 FS stat(CephFS 에선 로컬보다 몇 배 느릴 것). 후보: 백오프 중인 행(next_attempt_at > now)은
    `_complete` 를 건너뛰기, 틱당 완료 판정 행 수 상한(오래된 것부터 돌림차례), 또는 전역 단계도 틱 예산에 넣기.
  - **지표 박제** — 잡 통계·처리량·수행시간·요청자별 집계·plan_rejected 가 전부 행 기반이라(`repositories/metrics.py`)
    요청을 지우면 지난 창의 값이 소급해 줄어든다(수용, 확인 창이 안내). 필요하면 일 단위 집계 테이블.
  - **purge 파드 데드라인을 넘는 trash 항목**(2026-10-09 검증 중 확인) — purge 파드는 activeDeadlineSeconds 3600 이라
    한 시간 안에 못 지우는 거대 트리(수천만 파일)는 매번 중간에 죽는다. rm 이 지운 만큼은 남으므로 재시도(백오프 최대
    15분 간격)마다 줄어 결국 끝나지만, 그동안 그 행은 지연으로 보이고 그 파드가 도는 한 시간 동안 다른 요청의 파드는
    기다린다(파드는 언제나 1개). 끊길 때 앞 이름이 하나라도 지워졌으면 그 항목은 `purge_entry_failed` 로 다음부터 혼자
    실리지만, **정렬상 첫 이름**이면 지워진 것이 없어 근거가 없다(`purge_pod_failed` — 파드 수준 실패와 구별 불가) — 그
    항목이 끝날 때까지 함께 실린 행이 매번 같이 기다린다. 실측 필요. 후보: 파드 종료 사유(DeadlineExceeded) 노출로 귀속,
    실패 행에 한해 데드라인 연장, 파드 2개(새 행 전용) 허용.
  - **정리 이벤트 뷰어** — 정리 실패의 상세(`purge_failed`·`purge_ref_rejected`·`artifact_left_at_old_base`·
    `request_purged`, 전부 request_id=NULL)는 events 에만 있고 포탈엔 전역 이벤트 화면이 없다. 지금 표면은
    작업 목록 툴바의 정리 현황(`GET /api/admin/request-purges` — 대기·지연 건수와 첫 지연 사유)뿐이다.
- 🔧 **배치 CSV 업로드/내보내기·템플릿** — CSV 는 프론트 파싱(`lib/csv.ts`). 백엔드화는 미착수. (rm 배치는
  2026-10-21 완료 — CHANGELOG.)
- 🔧 **rm 경로의 다른 요청·배치와의 겹침 판정**(2026-10-21 배치 rm 의 열린 결정 D4) — 한 rm 배치 **안**의 겹침만 422
  `batch_rm_targets_overlap` 이고, 다른 요청과는 「정확히 같은 대상의 rm」(resource_key 같음 → `resource_conflict`)만 막는다.
  진행 중 sync 의 소스·목적지, 다른 rm 의 조상·자손, scan 대상과 겹치는 rm 은 둘 다 계획·실행된다(단건 rm 도 같다).
  대칭 판정(sync 계획 때도 rm 을 보는)이 없으면 반쪽이고, 위험한 순간은 확인 뒤 실행 직전이라 planner 1회 판정으로는
  부족하다 — 경로 접두 잠금 같은 별도 설계감.
- 🔧 **배치 생성 폼의 스토리지 드롭다운** — 현재 자유 입력.
- 🔧 **에이전트 설정 푸시** — 에이전트 설정은 배포 시 고정.
- 🔧 **알림/경보** — 슬라이스 14 비목표("대시보드는 표시만"). 임계 초과 알림 없음.
- 🔧 **러너 카운트 확장**(§러너) — dscan 총바이트 없음(리포트 스키마에 크기 히스토그램뿐,
  `bytes_total` 은 sync 전용), 슬라이스 15 이전 잡 소급 백필 없음, 프리뷰 카운트 DB
  미승격, 파일별 상세·에러 카운트·전송률 없음.
- ~~**보조 그룹(2026-10-07) 후속 — 포탈 chown 그룹 선택·자동 chown 그룹 정책**(D7 범위 밖)~~ — **dms-k8s 해당 없음**(2026-10-26 LDAP 코드 삭제 — 비 root 실행·보조 그룹·LDAP 이 없다; dms-ssc 의 항목). 기록:
  자동 chown 은 uid:**주 gid** 고정(`execution_manifests._auto_chown`)이고, 프로젝트 그룹 소유는 명시
  chown `uid:<gid>` 뿐인데 포탈의 chown 칸은 관리자 전용 고급 옵션이다 — 비관리자 화면은 이제 "관리자에게
  chown 지정을 요청하세요" 로 안내한다(`frontend/src/lib/syncOwnership.ts` chownEditable=false, 2026-10-08 리뷰;
  예전엔 실행할 수 없는 "chown 에 uid:<그룹 gid> 를 지정하세요" 였다). 사용자가 직접 프로젝트 그룹 소유로
  남기는 길은 아직 없다. 처방: 실행 신원의 적용된 보조 gid(잡 스냅숏과 같은 계산) 중에서 고르는 UI, 또는
  "목적지 부모 디렉터리의 그룹을 따른다" 같은 자동 정책(계획 시점 멤버십 검증 `chown_group_not_member`
  는 그대로 둔다).
- 🔧 **요청 상세 재설계(2026-10-08) 범위 밖 후보** — 출력 뷰어 안 검색(n/m)·오류 줄 강조와 이동·「크게 보기」
  대화상자(지금은 브라우저 Ctrl+F — 2,000줄 상한 안에서 전부 DOM 에 있다), URL 딥링크(`?job=&stage=&view=`),
  ConfirmPending pill 한국어 라벨, 잡 취소 2단 확인,
  공용 `Button` 의 focus-visible 링(이번엔 이 화면의 새 요소에만 달았다 -- `features/jobs/ui.ts`).
- 🔧 **요청 상세 「요청 내용」(2026-10-08) 범위 밖 후보** — 노드 탈락 사유(worker_pool.rejections) 표시 — 노드 사유
  어휘(missing_target_mount·missing_tool:<t>·identity_not_ready_on_node…)를 reasonCodes 양쪽에 등록하는 일과 nsync
  오독(rejections 는 dsync 판정) 해소가 먼저. 그 밖에 큐·우선순위 클래스 원문(worker_pool.queue·priority_class —
  지금은 상한으로 깎였을 때만 「high 요청 → mid 적용(정책 상한)」)은 표시하지 않는다(`identity_not_ready_on_node` 는 2026-10-26 노드
  신원 프로브와 함께 지웠다).
- 🔧 **모바일(375) 사이드바 접기** — 좁은 폭에서 관리자 사이드바가 본문 위 블록(약 1,060px)이라 첫 화면이 전부 사이드바다.
  요청 상세의 root 배지는 본문 첫 줄이지만 첫 화면 안은 아니다(2026-10-08 실측 y≈1127/667). 셸(`frontend/src/app/AppShell.tsx`)에서
  햄버거·접힘으로 바꾸면 본문이 첫 화면에 온다 — 셸 전역 변경이라 요청 상세 작업 범위 밖.
- 🔧 **앱 전역 의미 글자 대비(WCAG AA 4.5:1)** — 요청 상세는 의미 있는 글자를 `text-ink/70`(흰 바탕 4.94:1)으로
  올렸지만(2026-10-08), 다른 화면은 아직 `text-muted`(#888 — 흰 바탕 3.54:1, canvas 3.28:1)를 본문·표 머리·안내문에
  쓴다: `features/jobs/JobsList.tsx`(표 머리·경로 요약·시각·빈 목록 안내), `SubmitJob.tsx`(「실제 경로」·공백 안내·
  허용 스토리지 조합 note), `formFields.tsx`, 공용 `StatusPill` neutral(「Planned」 3.28:1), 레이아웃 브레드크럼 「>」.
  처방: 화면별로 `text-ink/70` 으로 올리되(장식용 코드 라벨·아이콘은 그대로), 클래스를 단언하는 테스트가 있는지 먼저 본다.
- 🔍 **상태 전이의 일반 종단 가드(검증 필요 — 코드 읽기로만 발견, 2026-10-08 요청 상세 리뷰 중)** — planner 쪽
  경합(취소가 planner 의 읽기와 Planned 쓰기 사이에 끼면 Cancelled 가 Planned 로 되살아나고 잡이 남던 것)은
  2026-10-08 작업 삭제의 선행 보강으로 **닫혔다**: emit 이 `data_jobs.create_plan_and_job`(요청 행 잠금 + plan·job
  INSERT + 조건부 Pending→Planned 한 트랜잭션, 지면 전부 롤백)이고 멱등 분기도 `requests.mark_planned_if_pending`
  (CAS)이다(`tests/test_planner.py` 경합 테스트). 남은 것: `repositories/requests._apply_state` 자체엔 여전히 종단
  가드가 없어 다른 호출자가 같은 모양의 경합을 만들 수 있고, 잡 쪽 `data_jobs.set_job_state` 도 잠금 없는
  SELECT→UPDATE 라 동시 쓰기에서 나중 커밋이 이긴다. 처방 후보: 조건부 UPDATE(`WHERE state = :expected`)·종단
  상태에서의 전이 거부, 재현 테스트부터.
- 🔧 **레지스트리 인증 확장**(2026-10-08 릴리스 태그 목록 결함 수정의 범위 밖) — 태그 조회·digest·삭제는 익명 Bearer
  토큰까지만 한다(`registry._anonymous_token`). (a) 다른 호스트의 토큰 서버(GitLab `…/jwt/auth` 형태)는 요청 위조 방지로
  realm 을 레지스트리와 같은 호스트로 묶어 거부된다 — 필요하면 운영자 허용 목록(설정)으로 연다. (b) Basic·로봇 계정 자격
  증명은 다루지 않는다(비밀 저장·전달 경로가 필요 — 평문 금지 규약).
- ~~**진행 중 잡 kill switch**(보조 그룹)~~ — **dms-k8s 해당 없음**(2026-10-26 LDAP 코드 삭제 — 비 root 실행·보조 그룹·LDAP 이 없다; dms-ssc 의 항목). 기록: `DMS_IDENTITY_SUPPLEMENTARY_GROUPS=false` 는 계획 시점
  전용이라 이미 스냅숏이 있는 잡의 재확인·적용은 끄지 못한다(`deploy/README.md` §2c 스위치 범위표).
  지금은 drain 후 취소 또는 이미지 롤백뿐이다. 처방 후보: stepper 가 읽는 control_state 플래그로
  "보조 그룹 잡은 제출 보류" 또는 "목록을 비워 주 gid 로" (후자는 단계 사이 drift 를 만들지 않도록
  preflight 부터 다시 돌려야 한다).
- 🔧 **워커 계정 충돌 전용 사유 코드** — 잡 이미지에 같은 이름의 계정이 다른 uid/gid 로 있으면 워커
  셸이 stderr `dms: account … exists with a different uid/gid` + exit 1 로 끝나고 화면엔 사유 없는
  preview_failed/execution_failed 만 보인다(`execution_manifests._identity_materialize_stmt` (2)).
  `DMS_EXEC_REASON=` 마커 + EXECUTION_REASONS·reasonCodes 등록으로 승격하면 운영자가 로그를 안 봐도 된다.
- 🔧 **3홉 '경고' 등급** — 3홉 검증은 base 자체만 본다. base 의 **부모**(공용 디렉터리)가 g+w·o+w 이면
  (`deploy/README.md` §2b-3) 공유 FS 를 마운트한 다른 호스트의 사용자가 base 옆에 디렉터리를 만들 수
  있는데 화면엔 초록이다. 부모에 o+x(711·755)가 있어도 초록인데, 그러면 모든 uid 가 base 아래 0644 아티팩트를
  직접 읽는다(§2b-3, 2026-10-08 리뷰로 문서의 711 권고 제거). 처방: 부모 mode 를 읽어 빨강이 아닌 '경고'로
  표시(저장은 막지 않음).
- ~~**보조 그룹 없는 잡의 preflight 그룹 자기검증**~~ — **dms-k8s 해당 없음**(2026-10-26 LDAP 코드 삭제 — 비 root 실행·보조 그룹·LDAP 이 없다; dms-ssc 의 항목). 기록: `_SUPP_GIDS_SELF_CHECK` 는 그룹이 실린 파드에만
  붙는다(빈 목록 잡의 매니페스트를 이 기능 이전과 바이트 단위로 같게 두려는 결정). 어드미션 웹훅이
  그룹 없는 잡에 supplementalGroups·fsGroup 을 더하면 preflight 만 그 그룹으로 통과할 수 있다(기존
  성질). 처방: 모든 비 root preflight 에 `id -G == {주 gid}` 검사(매니페스트 바이트 동일성 포기).

### dms-k8s RBAC·PV(k8s 신원 모드) 후속 — 2026-10-25

설계 `docs/history/specs/2026-10-25-dms-k8s-rbac-pv-design.md`(§10 사용자 결정 대기 + 슬라이스 1·2 구현의 열린 항목 + 보안 리뷰).

- ✅ **설계 §10 결정(2026-10-26 사용자)** — 결정 대기였던 항목을 모두 정했다(CHANGELOG 「dms-k8s 결정 항목 반영」):
  - (a) **namespaceAdmin 소유자도 맵핑** — 구현: ClusterRole kubeflow-admin + User subject 하나 + 이메일 도메인 = 계정 메일 도메인이면
    로컬파트가 그 namespace 의 admin(`k8s_rbac.namespace_admin_user`; 테스트베드 계정 도메인 `example.com`). KFAM 기여자 이름
    (`user-…-example-com-clusterrole-*`)은 종전대로 이름 규칙 파생 + 불일치 경고.
  - (b) **kubeflow-view 는 권한 아님**(무시) — 종전 그대로, 테스트로 고정.
  - (c) **동기화가 오래 실패해도 막지 않고 경고만** — 판정은 마지막 성공 스냅숏 그대로, 포탈이 오래됨·지연·실패면 PV 줄·스냅숏 기준
    줄에 경고(`pvLocation.freshnessWarns`). 한 번도 성공하지 않은 클러스터만 모름(409).
  - (d) **LDAP 코드 삭제·정리** — ✅ 완료(2026-10-26, CHANGELOG 「LDAP 코드 삭제·정리(dms-k8s)」 — 리졸버·노드 신원 프로브·
    nslcd·보조 그룹·ldap 모드 분기 삭제, 근거 없는 root 는 거부, 설정 안전장치는 유지). 리뷰 반영(토큰 컨펌 거부·배치 자식 판정
    우선·시스템 계정명 예약 `username_reserved`)까지 **d4 로 테스트베드 적용·실증**(2026-10-26).
  - (e) **배치는 직접 경로만, 등록 화면에 PV 검색 사이드바** — 구현: 배치 생성의 「PV 에서 경로 찾기」(k8s 모드)가 PV 를 검색해 그
    직접 경로를 표의 칸에 넣는다(배치 스토리지 자동 채움·불일치 잠금·rm PV 루트 잠금). 바디는 그대로 직접 경로.
  - ~~(f) 심링크 봉쇄 확장~~ — 해소(PV 주소 봉쇄, ARCHITECTURE §7).
  - (g) **scan 은 특권 운영자만** — 구현: `k8s_access.scan_allowed`(제출 403 `scan_unrestricted_only`·/me·planner·컨펌), 오버레이
    사용자 연산 `sync,rm`. 사용자 스캔 경로(`scan_path_unrestricted_only`)도 특권 운영자 전용 그대로.
  - (h) **Pure FlashBlade 는 보류**(사용자) — PV 마다 별도 파일시스템(export)이라 「스토리지 한 번 마운트 + 하위 폴더」 모델 밖이다.
    대안: PV 마다 스토리지로 등록하거나 에이전트가 필요할 때 그 export 를 마운트.
  - (i) **멀티 클러스터 라이브 확인** — 테스트베드에 두 번째 클러스터를 더해 확인(별도 항목).
  - (j) **「운영자」 = 특권 목록·부여 관리자** — 확인(사용자). 문서·화면의 「면제자」는 같은 뜻(특권 운영자).
  - (k) **「사용자 지정」으로 낸 작업은 그 사용자 목록에 보이지 않는다** — 종전(requester_id 격리) 그대로(사용자).
  - ~~(l) root sync 의 결과 소유권~~ — **결정·구현**(2026-10-26 사용자: 「아주아주 중요 — 목적지 PV 소유자로 자동 chown」): PV 목적지면
    결과를 목적지 PV 루트 uid:gid 로(preflight 측정 → --chown, 바뀌면 거부, 일반 파일 setuid/setgid 제거 — ARCHITECTURE 「PV 목적지
    root sync 의 결과 소유」).
  - ❓ **직접 경로 목적지가 PV 루트 아래일 때의 결과 소유**(2026-10-26 d4 실증에서 확인 — 사용자 결정 필요): (l) 은 **PV 주소** 목적지만
    PV 루트 소유자로 맞춘다. 특권 운영자의 직접 경로 sync(배치는 직접 경로만 — (e))가 PV 디렉터리 안을 가리키면 root 실행의 종전 규칙대로
    **소스 소유를 보존**한다(d4: 배치 `team-a/datasets/…` → `c2/lab-x/results/x4b` 결과 1000:1000, PV 루트 1500:1500). 운영자는 chown
    옵션으로 맞출 수 있다. 선택지: (1) 지금대로(직접 경로 = 운영자 책임) (2) 직접 경로가 등록된 PV 루트 아래면 그 PV 의 루트 소유자로
    자동 chown(목적지 쪽 preflight 측정을 직접 경로에도 — 배치 확인 화면에 소유자 표시) (3) 배치 생성의 「PV 에서 경로 찾기」로 넣은
    행만 (2).
- 🔧 **에이전트 채널의 노드별 자격(보안 리뷰)** — 공유 토큰(모든 노드 에이전트 보유, role admin) 보유자는 `x-dms-actor: node:<지정
  노드>` 로 행세해 `POST /api/agent/k8s-sync/claim` 에서 대상 클러스터의 리더 토큰을 받을 수 있다(`api/routes_agent.py` — 설계가
  받아들인 위험, 완화는 **읽기 전용** 리더 SA: rolebindings·pvc·pv·sc list + namespaces/kube-system get). 처방 후보: 노드별 토큰
  (에이전트 Secret 을 노드마다 — DaemonSet 이라 projected SA 토큰 + TokenReview 로 노드 신원 확인), 또는 claim 응답 토큰을 노드 공개키로
  봉인. 에이전트 채널의 TLS(CNI 암호화 포함 — 「방안 A」 항목의 근본 처방)와 함께 본다. 같은 행세로 **결과 POST 에 위조
  인벤토리**를 올려 맵핑(RoleBinding → 권한)을 지어낼 수도 있다(2026-10-26 push 보안 리뷰) — 노드별 자격이 오면 claim·result 둘
  다 그 자격에 묶고, 서버가 스스로 확인할 수 있는 것(클러스터 지문 = kube-system uid, 직전 스냅숏과의 큰 차이 경고)을 더한다.
  지금 공유 토큰 보유 = 노드 root 수준(같은 Secret 을 읽는 주체)이라 데이터 직접 접근과 같은 신뢰 경계다.
- 🔧 **테스트베드 `/data/k8s_pv` export 의 no_root_squash 노출 완화(보안 리뷰)** — testbed 저장소 `scripts/nfs-pv-export.sh` 가
  10.10.10.20-22 에 rw·no_root_squash 로 연다(root 잡이 PV 디렉터리를 만지는 실증 전제). 같은 망의 그 세 IP 에서 root 로 마운트하면
  모든 PV 데이터를 읽고 쓸 수 있다. 처방 후보: 실증 기간에만 켜는 make 타깃, export 를 워커(.21·.22)로만 좁히기(cp1 제외), 실증 데이터
  상한(1GiB) 유지. 프로덕션 GPFS·OneFS 는 사이트 스토리지 정책 소관(DMS 노드만 root 마운트).
- 🔧 **결과 POST 의 API 메모리(슬라이스 1 열린 항목)** — `POST /api/agent/k8s-sync/runs/{id}` 를 API 프로세스에서 JSON 파싱·정규화하므로
  상한(`DMS_K8S_SYNC_MAX_BYTES` 32MiB) 가까운 본문이면 API 메모리를 수백 MB 쓸 수 있다. 일반 인벤토리는 훨씬 작다 — 큰 사이트는 상한을
  낮추거나 스트리밍 파서로.
- ~~**PostgreSQL 실 DB 미실행(슬라이스 1·2)**~~ — **해소**(2026-10-26 사용자 「어 해결해」): k3 라이브 실증이 테스트베드 PG 로 돌았고,
  conftest `db` 픽스처에 PG 갈래를 더해(`DMS_TEST_DB=postgresql` + `DMS_TEST_PG_URL` — 테스트마다 새 스키마) k8s 테스트 238개(저장소·실데이터
  픽스처·맵핑·동기화·API·제출·PV 소유자·에이전트 워커)를 일회용 PostgreSQL 16 에서 통과시켰다. 그 실행이 찾은 것: PG 자리표시자 변환이 작은따옴표
  리터럴 안의 콜론(`'v1:sealed'`)까지 `%(…)s` 로 바꿔 PG 에서만 「query parameter missing」 — `db._NAMED` 가 리터럴을 건너뛰게 고쳤다(제품 SQL
  엔 아직 없었고 테스트의 날 SQL 3곳에서 드러남).
- 🔧 **e2e: 계정 화면 `?create=`·SubmitJob `?pv=` 소비 흐름(슬라이스 1 포탈 열린 항목)** — 수기 게이트. (03 레이아웃 순회의 k8s 5화면
  1280·375 는 2026-10-26 추가 — 해소.)
- ~~**PV 탐색 「이 PV로 단일 작업」 버튼을 권한 밖 PV 에서 미리 막을지**~~ — **해소**(2026-10-26): 전체 PV 권한이 없는 관리자가 자기
  맵핑 밖 PV 를 열면 버튼이 잠기고 「이 PV 에 대한 권한이 없습니다 — <ns> 에 <ID>-clusterrole-edit|admin 또는 namespaceAdmin 필요」를
  보인다(`VolumesPage.canUsePv` — 모르면 열어 둔다, 제출·서버가 다시 본다).
- ~~**nsync 목적지 걷기가 링크를 따라간다(dms-mpifileutils, 2026-10-26 2차 보안 리뷰)**~~ — **해소**(2026-10-26, d1 — k8s 테스트베드 dN 관례 첫 태그, CHANGELOG
  「nsync 목적지 링크 추종 수정 반영」): chahwansong/mpifileutils `5958e4e`·`8cd72f3`(·`2a10a18` 새 목적지 루트) 이 목적지 루트 아래를 루트 fd 기준 성분별
  `openat(O_NOFOLLOW)` 로 걷게 고쳤고(파일 생성·삭제·심링크·chmod/chown/utimes 도 그 fd 기준), `Dockerfile.mpifileutils` 의
  `MPIFILEUTILS_REF` 를 2a10a18 로 올린 뒤 planner 가드(`check_nsync_destination`·`pv_sync_needs_shared_node`)를 풀었다 — PV 스토리지를
  노드마다 따로 마운트한 사이트의 PV→PV sync 도 nsync 로 돈다. 비 PV root nsync(배치 등)도 같은 수정을 받는다. 고정값을
  옛 커밋으로 되돌리면 `tests/test_k8s_submit.py` 의 고정값 테스트가 빨간불이다.
- 📝 **PV 봉쇄의 남는 창** — 러너·rank 재확인 뒤 도구가 경로를 열 때까지의 창은 rm 봉쇄와 같다(§4 「rm 대상 봉쇄의 남은 TOCTOU 창」과 같은 성격 —
  근본 처방은 hostPath 를 PV 루트로 좁히거나 도구가 openat2(RESOLVE_BENEATH) 로 걷는 것).
- ~~**숫자만으로 된 계정 아이디의 실행 신원**~~ — **해소**(2026-10-26 사용자 결정 「거부」): 계정 생성·가입 인증번호 발급이
  `username_numeric_only`(재설정은 옛 계정을 위해 형식으로 막지 않음), k8s 「사용자 지정」 규칙·namespaceAdmin 로컬파트·프런트 미러도
  같은 패턴(`(?![0-9]+$)…`).
- 📝 **동적 PV 의 VOLUME 은 PVC 를 다시 만들면 바뀐다** — 테스트베드 실데이터 픽스처(`tests/fixtures/k8s/testbed-*.json`)는 그 시점
  사본이라 라이브와 달라질 수 있다(테스트는 사본만 본다). 경로 맵 다시 뽑기: testbed `scripts/tbinfo_pv.py pv-node-paths`.
- 📝 **csi-snapshotter 사이드카의 watch 오류 로그(테스트베드)** — VolumeSnapshot CRD 를 일부러 설치하지 않아 csi-nfs-controller 가
  로그를 계속 낸다(재시작 0, DMS 는 스냅숏을 쓰지 않는다).

### 두 번째 테스트베드(k8s, LDAP 없음) 후속 — 2026-10-21

- ~~**`overlays/prod` 설치기는 LDAP 을 필수로 요구한다 — 무 LDAP dms-k8s 사이트와 맞지 않음**~~ — **해소**(2026-10-25,
  CHANGELOG 「dms-k8s RBAC·PV 슬라이스 2」): dms-k8s 의 `overlays/prod` 가 testbed-k8s 와 같은 모양이 됐다 — LDAP 키 null 삭제,
  `DMS_IDENTITY_MODE: "k8s"`·`DMS_USER_ALLOWED_OPERATIONS: "sync,scan,rm"`, `patch-agent.yaml`(`dms agent` + LDAP env 삭제),
  render.sh 필수 키에서 LDAP_* 제거(옛 values.env 의 LDAP_* 는 무시 + 알림), install.sh 시크릿 4키. `tests/test_prod_k8s_overlay.py`.
- ~~**"LDAP 끔" 명시 스위치**~~ — **해소**(2026-10-25 → 2026-10-26 LDAP 코드 삭제): LDAP 코드·nslcd·libnss-ldapd 가 없어 끌 것이
  없다. k8s 신원 모드가 유일한 모드이고 `DMS_IDENTITY_MODE=ldap`(이나 다른 값)·`DMS_LDAP_URI` 는 기동 거부(dms-ssc 설정이 섞인
  경우의 안전장치). 에이전트는 base 에서 `dms agent` 로 직접 뜬다(오버레이의 명령 오버라이드 삭제).
- ~~**포탈 다음 태그 제안이 `kN` 을 모른다**~~ — **해소**(2026-10-26, 관례 전환): 사용자 결정 「예전 dms-ssc 때처럼 d1 부터」로 k8s
  테스트베드도 `dN` 을 쓴다(d1 = 첫 태그). 제안(`BuildForm.nextTagSuggestion`)은 그 사이트의 라이브 태그·자기 레지스트리·빌드 이력의
  `dN` 최댓값 + 1 이라 코드는 그대로다 — git 의 오버레이 태그를 읽지 않으므로 프로덕션도 자기 번호의 다음을 제안한다. 「지배적 접두를
  따라 제안」 처방은 하지 않는다(프로덕션 제안이 접두 판정에 흔들린다).
- ~~**점검 스크립트에 `--context` 가 없다**~~ — **해소**(2026-10-26 사용자 지시): `deploy/preflight-cluster.sh`·`deploy/verify.sh`·
  `deploy/overlays/prod/render.sh`(live 태그 해석)가 guard-images.sh 와 같은 문법(`--context <ctx>`/`--context=<ctx>`/`KUBE_CONTEXT`,
  플래그 우선, 없는 컨텍스트 exit 2)을 받는다 — 모든 kubectl 호출이 그 컨텍스트로 간다(`tests/test_check_scripts_context.py`, 테스트베드
  실측: current-context `dms` 인 채 `--context dmsk8s` 로 k8s 테스트베드 점검). 남은 것: `install.sh` 는 여전히 현재 컨텍스트로 kubectl 을
  부르고 render·가드에 컨텍스트를 넘기지 않는다(가드가 KUBE_CONTEXT ≠ 현재 컨텍스트면 exit 4 로 막는다), delete migrate → 가드 → apply →
  wait → rollout 을 한 번에 하는 래퍼(`apply-overlay.sh <overlay>`)는 후보.
- 🔧 **호스트 콜드부팅 리허설 미실증**(2026-10-22) — VM 단독 재부팅(k8s-w1·k8s-w2)과 hostnet 유닛 재시작은 실증했고, 그 과정에서
  testbed 부팅 가드의 두 겹 마운트 경합을 고쳤다. 호스트 전체 재부팅(8 VM 동시 기동, pkg-01 Ceph 늦게 올라옴)은 세션에서 할 수
  없다 — 다음 호스트 재부팅 뒤 testbed `make post-boot-check WAIT=900` 이 그 실증이다(testbed ARCHITECTURE §18).
- ~~**ssc DB 에 제거된 dms-w4·w5 의 `nodes` 행이 남는다**~~ — **정리**(2026-10-26 사용자 「필요없다면 정리해」): ssc 클러스터
  노드 목록에 없고 마지막 보고가 2026-10-21 인 dms-w4·w5 의 agent_nodes 2행·agent_reports 73,865행을 지웠다(node_exclusions 0행,
  builds 의 빌드 노드 이름은 이력이라 남김). 노드 기록 삭제 화면은 만들지 않았다(필요해지면 후보).

## 2. 의도적 보류 (결정 — "미구현"이 아님)

- ⏸ **회원가입 메일 인증** — 더미(email 기록만, 검증 없음, `routes_auth.py::signup`).
- ⏸ **Prometheus/Grafana** — 포탈 대시보드로 대체.
- ⏸ **롤백 버튼** — 이력에서 옛 태그 재선택으로 갈음.
- ⏸ **클러스터 내 registry** — 호스트 `pkg-01:5000` 사용(슬라이스 11 비목표: 독립 인프라 작업).
- ⏸ **레지스트리 경로 접두 미지원**(2026-10-21 테스트베드 분리 때 확인) — `DMS_BUILD_REGISTRY` 는 `host:port` 만 된다:
  `registry.py` 가 `http://{registry}/v2/{repo}/…` 로 URL 을 만들고(`pkg-01:5000/k8s` 면 `/k8s/v2/…` 로 깨진다)
  `manifest_tags.image_registry` 는 첫 경로 조각만 레지스트리로 봐 `site_image` 가 늘 None(배지 「모름」)이 된다. 그래서 두
  테스트베드는 같은 VM 에 **포트(프로세스)를 나눴다**(ssc :5000, k8s :5001) — 레지스트리를 같이 쓰면 「사용 중」 보호
  (`routes_registry._in_use_tags`)가 자기 클러스터만 봐 상대의 라이브 태그를 지울 수 있고 태그 제안이 경합한다. 필요해지면
  고칠 곳: `registry.py` URL 분해, `manifest_tags.image_registry`, 빌드 스탬프 정규식, `_registry_host`.
- ⏸ **이미지 태그 동기화 템플릿 계층** — 5파일 수기(`deploy/README.md`). 슬라이스 16 이
  드리프트 배지 + initContainer/본체 이미지 계약 테스트로 완화. "매니페스트 안 고치고
  포탈 롤아웃만" 은 배지가 잡되 무시하면 다음 apply 가 되돌린다.
- ⏸ **빌드 레이어 캐시·멀티아치** — 슬라이스 11 비목표.
- ⏸ **`DMS_JOB_IMAGE` 포탈 롤아웃** — ConfigMap 갱신 + 소비자 재시작이라 슬라이스 13
  범위 밖. `20-config.yaml` 수기 편집.
- ~~**보조 그룹의 중첩 그룹 해석**(D10, 2026-10-07)~~ — **dms-k8s 해당 없음**(2026-10-26 LDAP 코드 삭제 — 비 root 실행·보조 그룹·LDAP 이 없다; dms-ssc 의 항목). 기록: DMS 리졸버는 1단계 멤버십만 본다
  (`identity_ldap.py` 모듈 docstring). 노드 SSSD(`ldap_group_nesting_level` 기본 2)보다 덜 줄 수
  있다(안전한 방향 — "로그인 노드 `id` ⊋ DMS 그룹"). 필요해지면 깊이 상한·순환 방지와 함께.
- ~~**launcher /etc/group 대칭**(D12)~~ — **dms-k8s 해당 없음**(2026-10-26 LDAP 코드 삭제 — 비 root 실행·보조 그룹·LDAP 이 없다; dms-ssc 의 항목). 기록: launcher 의 mpirun 은 보조 그룹 없이 hostfile 을 읽는다.
  대칭(launcher 에도 dmsg<gid> 물질화) 대신 base other-x 강제(제어면 `roundtrip_artifact_base` +
  preflight `_ARTIFACT_BASE_OTHER_X_CHECK`)로 막았다 — 그래서 750/710 base 는 거부된다.
- ~~**LDAP StartTLS 인증서 검증**(D16 — 안 함, 기록만)~~ — **dms-k8s 해당 없음**(2026-10-26 LDAP 코드 삭제 — 비 root 실행·보조 그룹·LDAP 이 없다; dms-ssc 의 항목). 기록: 제어면 리졸버·에이전트 nslcd 모두 sssd
  `ldap_tls_reqcert = never` 미러(`identity_ldap.py` 모듈 docstring). 보조 그룹 이후엔 LDAP 중간자가
  그룹 멤버십까지 주입할 수 있고 제출 전 재확인도 같은 채널이다. 사내 CA 번들 + verify 옵션이 처방.
- ⏸ **요청 삭제의 옛 base 사본은 지우지 않는다**(사용자 결정 2026-10-09) — 삭제 시점 base 와 지금 base 가 다르거나
  (그 사이 force 변경) 잡의 `artifact_uri` 가 다른 base 를 가리키면 request-purge 는 그 경로를 열지도 지우지도 않고
  `artifact_left_at_old_base` 경고(옛 base·job_id·경로 표시용)만 남긴다 — DB 값(신뢰 경계)만 믿고 다른 경로를 지우지
  않는다. 테스트베드 실례: `5df671da…` 의 산출물이 `artifacts-slice18` 에 있다. 지우려면 운영자가 경고의 경로를
  확인하고 손으로.
- ⏸ **root 실행 잡의 삭제에 특권 세션을 더 요구하지 않는다**(사용자 결정 2026-10-09) — 요청 삭제는 세션 관리자
  (`require_session_admin`)면 되고 `can_run_as_root` 자격은 보지 않는다. admin 은 신뢰 앵커이고(ARCHITECTURE §7 규칙
  14), 감사 스냅숏(`audit_log` 'request'/'delete' before_state)이 run_as_root·실행 신원·전이 actor 를 보존한다.
- ⏸ **보호 계정의 셀프 비밀번호 변경 없음**(2026-10-12 root 자격 부여 리뷰) — 특권 목록(env ∪ 계정 화면 부여) 계정은
  메일 재설정이 막히고(`_refuse_privileged_reset`) 로그인 상태의 비밀번호 변경 라우트도 없다 — 바꾸려면 특권 관리자가
  지우고 다시 만든다(부여도 사라져 다시 줘야 한다). 그래서 부여 확인 창이 「비밀번호를 아는 사람은 모두 root」를 말한다.
  처방: 세션 인증 셀프 변경(현재 비밀번호 확인 + 봉인 통로 `postWithSealedPassword`/`_password_from`).
- ~~**자격을 잃은 생성자의 진행 중 배치는 남은 자식을 비 root 로 계획한다**(2026-10-12 리뷰, 종전 의미 유지)~~ — **바뀜**
  (2026-10-26 LDAP 코드 삭제): 낮출 비 root 신원이 없어 남은 자식은 계획 때 `privileged_not_authorized` 로 **거부**된다(한 배치
  안에서 root 결과와 거부가 섞일 수 있다 — 필요하면 그 배치를 취소). 옛 기록: 배치
  자식은 `PRIVILEGE_IF_ELIGIBLE`(생성자가 자격 있으면 root, 없으면 실행 신원의 LDAP uid/gid)이라, 「자격 빼기」·강등 뒤
  계획되는 자식은 거부되지 않고 비 root 로 돈다(권한은 줄어드는 방향). 한 배치 안에서 root·비 root 결과가 섞일 수 있다 —
  필요하면 그 배치를 취소한다. 세션으로 만든 배치 자식을 거부로 바꾸는 것은 옛(토큰·NULL) 배치의 동작까지 바꿔 보류.
- ⏸ **react-router 의존성 권고 보류**(`frontend/README.md`) — `GHSA-qwww-vcr4-c8h2` high,
  현재 어떤 `react-router-dom` 버전도 두 취약 범위를 동시에 못 피한다. 재검토 조건:
  `react-router-dom@8.3.0` 이상. **`npm audit fix --force` 금지.**

## 3. 외부 자원에 막힘

- ~~**LDAP 인증 바인드 전환**~~ — **dms-k8s 해당 없음**(2026-10-26 LDAP 코드 삭제 — 비 root 실행·보조 그룹·LDAP 이 없다; dms-ssc 의 항목). 기록: 코드 강화는 슬라이스 28 이 끝냈다
  (`DMS_LDAP_REQUIRE_AUTH_BIND` fail-closed, 실 파드 env 발화 실증). **막는 것**: 실제
  전환은 ① OpenLDAP(10.10.10.30) 바인드 서비스 계정(DN+PW) 발급 ② `dms-secrets` 에 주입
  ③ 플래그 `"false"→"true"`(순서 엄수 — ③ 먼저면 CrashLoopBackOff). 절차는
  `deploy/k8s/20-config.yaml` 주석. **자격증명 준비되면 재개.**
- 🔒 **포탈 빌드는 이 테스트베드에서 구조적 미해결** — 빌드 파드가 빌드 노드(=dms 워커)
  위에서 인터넷 egress(GitHub·npm·PyPI·dl.k8s.io)를 써야 하는데 테스트베드는 "pkg-01 만
  인터넷". pkg-01 은 어느 클러스터에도 join 안 해 `build_node_name` 후보에 안 뜬다.
  실 빌드 경로는 pkg-01 podman(`deploy/docker/build-and-push.sh`) 또는 빌드 시점에
  워커 노드에 인터넷을 여는 것(슬라이스 21 실증). 슬라이스 24~30 배포는 후자 방식으로
  했다. 근본 해결은 인프라 작업.

## 4. 알려진 해석·잔여 리스크 (문서)

- 📝 **파일 크기 분포에 구간별 용량이 없다**(2026-10-21, d183). dscan(포크 1b93d54)의 `file_size_histogram` 은 구간별 **개수**만
  쓴다(시간 히스토그램만 bytes). 그래서 사용량 화면은 개수·비중만 보인다 — 「1 GiB 넘는 파일이 용량의 몇 %」는 못 말한다. 해법:
  포크 `acc_emit_entry` 에 `size_bytes[bin] += size` 와 JSON `"bytes"` 키 → 잡 이미지·에이전트 재빌드. 투영·캐시·CSV·
  `SizeDistribution` 은 bytes 가 모든 구간에 있으면 자동으로 용량 열을 붙이게 이미 돼 있다.
- 📝 **스토리지끼리 파일시스템을 공유하는지 모른다**(같은 날). 에이전트 statvfs 보고에 `f_fsid` 가 없어, 같은 Ceph 를 다른
  경로로 등록한 스토리지들의 df 가 같은 값으로 나란히 보인다(테스트베드 cephfs 셋). 그래서 스토리지별 비교는 df 를 더하지 않고
  각주로만 말한다. 해법: probes 가 fsid 를 함께 보내고 화면이 같은 fsid 를 묶어 표시.

- 📝 **비 UTF-8 이름은 broken 사유가 아니다 — 보이게 할지는 결정 대기**(2026-10-20, d182 러너 수정과 함께 조사).
  러너는 이제 그런 이름에 죽지 않지만(ARCHITECTURE 「파일 이름은 바이트다」), dscan(포크 1b93d54)에는 이름 인코딩 사유가
  없어 운영자가 그런 이름이 있는지 리포트로 알 수 없다(사고 경로가 broken 이었던 건 `abnormal_time` — 10년보다 오래된
  시각 — 때문이다). 선택지: 포크 `dscan.c` 에 `invalid_name_encoding`(d_name 만 엄격 UTF-8 검증 — 전체 경로를 보면
  그 아래 자손이 전부 걸린다) + 요약 카운터 `invalid_name_entries`, 리포트·`--print` 를 유효 UTF-8(U+FFFD)로 쓰고 원래
  바이트는 `path_hex` 로. 주의: 레거시 트리엔 그런 이름이 수천 개일 수 있어 `broken_paths_total` 의미가 커진다 —
  broken 에 넣을지, 카운터만 둘지가 사용자 결정. 잡 이미지·에이전트 재빌드 필요.
- 📝 **drm 은 unlink 실패에도 종료 코드 0**(포크 `drm.c` — `mfu_flist_unlink` 가 void). 일부만 지워졌는데 rm 이
  Succeeded 로 끝날 수 있다(같은 조사에서 발견). 해법: 포크에서 실패 수를 rc 로, 또는 러너가 오류 줄을 판정.
- 📝 **rm 대상 봉쇄의 남은 TOCTOU 창**(2026-10-21 배치 rm 리뷰). 「상위 경로에 심링크 성분 없음」은 preflight·exec_preflight
  (`_RM_CONTAINMENT_CHECK`)와 러너의 drm 직전 재확인(`runner.rm_target_redirected` — launcher 가 같은 스토리지 hostPath 를
  마운트하고 root 라 가능했다) 세 번 본다. 그래도 러너 확인 → mpirun → 워커 rank 의 drm 이 경로를 lstat·걷기 시작할 때까지
  (워커 기동·MPI 초기화, 보통 수 초)는 비어 있다 — 상위 디렉터리에 쓸 수 있는 사용자가 그 사이에 정확히 심링크로 바꾸면 root
  drm 이 링크를 따라간다(예전 창은 exec_preflight 파드 → Volcano 대기 → 실행으로 분 단위였다). 닫는 길은 포크 `drm.c` 가
  대상의 상위 경로를 성분마다 `openat(O_NOFOLLOW|O_DIRECTORY)`(또는 `openat2(RESOLVE_NO_SYMLINKS|RESOLVE_BENEATH)`, 기준
  fd = managed_root)로 열고 그 fd 기준으로 지우는 것 — 잡 이미지 재빌드가 필요해 보류. 단건 root rm·root sync 목적지의 상위
  심링크도 같은 종류다(sync 목적지는 봉쇄 검사 밖).
- 📝 **제출 경계의 lone surrogate 는 422 가 아니라 500**(같은 조사). JSON 본문 `"target":"a/\udcb4x"` 는 파이썬 json·
  pydantic 을 통과해 DB INSERT 에서 UnicodeEncodeError → 500(요청 행은 안 생긴다). `domain.validate_relative_path` 에
  `path.encode("utf-8")` 실패 → `unsafe_path`(이미 양쪽 등록된 코드)를 넣으면 422 가 된다. 경로를 받는 다른 입력도 같다.

- 📝 **dsync 가 "목적지 부모 쓰기 불가" 에서 종료 코드 0**(2026-09-30 d139 실증에서 발견).
  포크 `dsync.c`(MPIFILEUTILS_REF 1b93d54) 는 목적지 존재와 무관하게 부모 W_OK 를 요구하고,
  실패하면 `MFU_LOG(ERR)` 후 `goto ERROR` 인데 `rc` 가 초기값 0 이라 **아무것도 복사하지 않고
  0 으로 끝난다**(dry-run 은 경고만이라 미리보기도 정상처럼 보인다) → DMS 는 Succeeded. 지금은
  preflight 가 목적지가 있어도 부모 쓰기를 먼저 거부해(`destination_parent_not_writable`)
  정상 흐름에선 닿지 않지만, preflight 와 실행 사이에 부모 권한이 바뀌는 경합·root-squash
  류 스토리지에선 여전히 조용한 무동작 성공이다. 근본 해법: 포크에서 그 경로 `rc = 1`(또는
  목적지가 이미 있으면 부모 검사 생략 — 그러면 "root 755 부모 아래 본인 디렉터리" sync 도 가능),
  또는 러너가 `ERROR:` 줄을 종료 코드와 함께 판정. 잡 이미지 재빌드가 필요해 보류.
- 📝 **러너가 `<phase>` 의 `chown -R` 반환코드를 보지 않는다**(`src/dms_job_runner/runner.py`,
  2026-09-30 아티팩트 쓰기 감사). mkdir 은 되고 chown 만 거부되는 드문 FS(NFSv4 idmap 등)에서
  비 root scan 이 원인 표시 없이 dscan "Failed to write output file" 로 죽는다(root_squash 면 그
  전에 makedirs 가 실패). 진단성 문제 — 반환코드 검사 + 마커. 잡 이미지 재빌드가 필요해 보류.
- 📝 **root 실행은 기존 목적지의 소유·권한을 소스에 맞춘다**(2026-09-30 사용자 결정 "관리자는 기본 root" →
  dms-k8s 는 모든 잡이 root, 의도·경고됨). root preflight 는 소유 검사를 하지 않고 dsync 는 소스 최상위 메타데이터를
  기존 목적지에 적용한다. PV 주소 목적지는 결과를 목적지 PV 루트 소유자로 맞추므로(`execution_manifests._auto_chown`)
  남는 경우는 면제 관리자의 **직접 경로** 목적지다 — 확인 스텝·배치 캡션이 경고한다. (예전 비 root 실행의 자동
  `--chown uid:주 gid` 와 보조 그룹 이야기는 2026-10-26 LDAP 코드 삭제로 해당 없음.)

- 📝 **에이전트↔API 채널이 클러스터 내부 평문 HTTP**(`http://dms-api:8080`, 2026-09-29 기록).
  관리자급 공유 토큰이 매 보고 주기마다 평문으로 지난다(LDAP 검색 계정 비밀번호의 하달은 2026-10-26
  LDAP 코드 삭제로 없어졌다). 파드 네트워크 스니핑이 가능한 공격자는 이미 공유 토큰을
  얻으므로 방안 A 가 경계를 넓히진 않았지만, 채널 TLS(또는 CNI 암호화)는 둘 다에 대한 근본
  해법이다. 실패 지속 시 재전송을 줄이려면 에이전트가 받은 블록을 메모리에 들고 로컬 백오프로
  재시도하고 서버엔 "보류 해시" 만 알리는 방식이 있다(현재는 실패가 로그·directory.error 로
  즉시 보이는 쪽을 택함).
- ~~**nslcd 그룹 멤버 속성 미러 안 함**~~ — **dms-k8s 해당 없음**(2026-10-26 LDAP 코드 삭제 — 비 root 실행·보조 그룹·LDAP 이 없다; dms-ssc 의 항목). 기록: 에이전트 nslcd 는 기본(memberUid + member)으로
  그룹을 푼다. 제어면은 `DMS_LDAP_GROUP_MEMBER_ATTR`(기본 uniqueMember)로 푼다. 신원 준비
  판정은 **이름 해석만** 보고(`placement._identity_ready`), 잡 파드의 보조 그룹(2026-10-07~)은
  **플래너 리졸버의 posixGroup gidNumber 값**(계획 시점 스냅숏 → preflight supplementalGroups·
  워커 /etc/group `dmsg<gid>`)이라 노드 NSS 와 무관하다 — nslcd 불일치는 **노드 화면이 보여주는
  그룹 한정**(uniqueMember 전용 디렉터리에서 비어 보일 수 있다). 필요하면 `map group member
  uniqueMember` 를 렌더러·하달 블록에 함께 추가.
- 📝 **NFSv4/GPFS 고유 ACL 은 제어면 base 검사 밖**(보조 그룹 D6) — `artifact_base._posix_acl_problem`
  은 POSIX ACL xattr 만 읽는다. 그런 ACL 이 base **자체**나 **상속**(NFSv4/GPFS inheritable ACE 가 `<job_id>/<phase>` 에
  주는 쓰기)으로 쓰기를 주면 3홉은 초록이다 — 남는 위험(운영 base 는 root:root 755 에 상속 ACE 없음을 직접 확인,
  `deploy/README.md` §2b-1). 2026-10-26 LDAP 코드 삭제로 잡 단위 관문(보조 그룹 잡의 preflight `test -w`·컨트롤러
  static_base_problem)은 없어졌다 — dms-k8s 의 잡은 root 라 그 관문이 막던 「같은 그룹의 다른 요청자」가 없다.
- 📝 **nsync 복합 preflight 는 PENDING 을 보고하지 않는다**(`execution_volcano.poll` 의 `pods/a,b`
  결합 — 한쪽이 대기 중이면 RUNNING). 그래서 stepper 의 PENDING 분기(`_raise_if_blocked` — 계획 뒤
  배치 제외·cordon 된 노드의 대기 단계 종단)가 nsync preflight 파드 쌍에는 걸리지 않는다 — 스케줄 전
  파드는 activeDeadlineSeconds 도 발화하지 않아(스케줄 뒤에만) cordon 된 노드의 쪽은 그대로 멈춘다. (예전 보조 그룹 큐 대기 재확인은 2026-10-26 에 지웠다 — 대기 단계 재확인을 다시
  만들면 같은 구멍에 빠진다.) 처방: 복합 결합에
  "하나라도 PENDING 이면 PENDING"(RUNNING 보다 낮은 우선) 추가.

- 📝 **`by_storage` 가 `COALESCE(storage_name, destination_storage)`** — sync 를 도착지
  기준으로 센다. 설계가 기준을 명시하지 않은 침묵의 해석(`repositories/metrics.py`).
- 📝 **KPI 의미 변화** — 요청 50건 즉석 계산 → 창 내 잡 집계. 옛 화면과 숫자가 다르다.
- 📝 **CI 없음** — e2e(슬라이스 23)·전체 스위트는 **수기 게이트**다(`deploy/README` 의
  "이미지 빌드 전" 단계). 기술적 강제 수단 부재 — 러너 상주·브라우저 캐시·시크릿 관리가
  얽힌 별도 작업.
- 📝 **`imagePullPolicy: IfNotPresent` + 태그 재사용 = 노드 캐시 stale** — 고유 태그
  관례(dNN 단조 증가)로만 완화. 같은 태그 재빌드는 노드가 옛 이미지를 쓸 수 있다.
- 📝 **`/cephfs` hostPath `type: Directory`** — 비-cephfs 노드가 스케줄 풀에 들어오면
  파드 admission 실패.
- 🔧 **러너 root 쓰기가 심링크를 따라간다**(`src/dms_job_runner/runner.py` `main().write_text`
  → `open(path, "w")`; 2026-09-09 root 전환 검토에서 확인, 전환과 무관한 기존 결함) —
  요청자가 chown 이후 자기 phase 디렉터리에 `stdout.log`/`summary.json` 심링크를 심으면
  root 가 그 잡의 스토리지 마운트·artifact base 아래 임의 경로를 truncate/덮어쓴다(내용은
  도구 stdout). 처방: chown 전에 phase dirfd 를 잡아 `os.open(name, O_WRONLY|O_CREAT|O_EXCL|
  O_NOFOLLOW, dir_fd=dfd)` 로 쓰고, 마지막 쓰기 뒤 그 세 파일을 요청자로 chown. 그러면 API
  소유자 검사(`artifact_files.inode_allowed`, 현재 `{0, 요청자}`)를 `== 요청자` 로 좁힐 수
  있다. **잡 이미지(dms-mpifileutils) 재빌드가 필요**해 제어면 root 전환(d128)과 분리했다.
  같은 계열: `parsers.py` 의 dscan-report.json following open(카운트만 파싱, fail-soft).
- 📝 **컨트롤러 롤아웃 겹침 창** — Deployment 롤아웃 직후 옛 파드가 종료 유예(30s) 안에
  마지막 틱을 마치며 옛 코드로 잡을 스텝할 수 있다(2026-09-09 d129 실증: 새 파드의
  preflight 파드 2초 뒤 옛 파드가 preview vcjob 을 구 마운트로 제출 → 그 잡만 실패,
  재실행 성공). 리스는 틱 시작에만 확인되고 TTL 30s 라 구조적으로 남는 창이다. 처방
  후보: 컨트롤러 preStop 에서 루프 중단 후 대기, 또는 리스 재확인 뒤 제출. 파드 스펙을
  바꾸는 릴리스 직후 첫 잡은 재실행이 필요할 수 있음을 운영 절차에 적어 둔다.
- 🔧 **securityContext 드리프트는 배지가 못 본다** — 포탈 롤아웃(`rollout_runner.
  image_patch_body`)은 name/image 만 patch 하고 드리프트 배지(`manifest_tags.manifest_images`
  ↔ `rollout_status._images`)도 이미지만 비교한다. 40/41 을 `kubectl apply` 하지 않은
  사이트는 d128 이미지를 65532 로 굴리게 되고(3홉 `artifact_base_not_writable` 로만 드러남)
  아무 배지도 안 뜬다. 처방: rollout_status 가 dms-api/dms-controller 의
  `containers[].securityContext.runAsUser` 를 관측해 매니페스트 값과 다르면 배지.
- 📝 **하드링크는 코드로 못 막는 부분이 있다** — `fs.protected_hardlinks=1`(+symlinks)은
  공유 FS 를 마운트하고 사용자가 link(2) 를 부를 수 있는 **모든** 호스트의 배포 전제
  (`deploy/README.md` §2b). 코드 측(nlink>1·남의 소유 거부)은 보완일 뿐이다.
- 📝 **아티팩트 소유자 검사의 `0` 허용** — 러너가 chown 뒤 root 로 쓰는 세 파일 때문.
  요청자가 w+x 를 가진 비-sticky 디렉터리의 **root 소유** 0600 파일을 `mv` 로 자기 phase
  디렉터리에 들여오면(드묾) 읽힌다. 위 러너 항목이 닫히면 함께 닫힌다.
- 📝 **pod GC 86400s** — 성공·진행 중 잡의 프리플라이트/실행 파드 로그 라이브 열람 창(2026-10-16 부터 종단 잡 중 성공이
  아닌 잡의 vcjob ref 도 max(이 창, DMS_VCJOB_TTL_SECONDS) 뒤에 지운다 -- Aborted vcjob 은 Volcano TTL 이 치우지 않았다.
  TTL 0 이면 vcjob 은 두고 Pod 만).
  실패 잡은 슬라이스 25 가 `diag_logs` 로 박제해 시한부가 아니다(부분 완화).
- 🔧 **빌드 파드 GC 도 200건 창 고착**(2026-10-16 검토 지적, 기존 결함) — 잡 파드는 `data_jobs.pods_gc_at` 완료 표시로
  고쳤지만 빌드 쪽(`pod_gc.py` 의 `builds.terminal_older_than`, seq 오래된 순 LIMIT 200)은 표시가 없어 종단 빌드가 200건을
  넘으면 같은 앞쪽 200건의 빌드·프로브 파드만 매 틱 다시 지우고 그 뒤 빌드의 파드는 남는다. 처방: builds 에도 완료 표시
  (CREATE + `_ensure_columns` + 부분 인덱스) 또는 seq 커서.
- 🔧 **빌드 삭제 docstring 의 거짓 전제** — `BuildsRepository.delete`(`repositories/builds.py` 156행)는 「파드는
  pod-gc 가 따로 수거하므로 DB 행만 지운다」고 하지만 pod-gc 는 `builds.terminal_older_than` 의 **행**으로 파드 이름을
  만든다(`pod_gc.py` 46-56행) — 행을 지우면 그 빌드·프로브 파드는 pod-gc 시야 밖으로 영영 남는다. 처방: 삭제
  라우트가 파드를 먼저 terminate(빌드 러너, 404 삼킴)하거나 빌드 삭제에도 아웃박스.
- 🔧 **pod-gc·취소의 ref 미검증 terminate**(DB 신뢰 경계) — 요청 삭제 정리는 ref 가 **그 잡의 DMS 명명**일 때만
  지운다(`purge_runner.parse_ref` — `pod/dms-preflight-<job12>-…`·`vcjob/dms-<op>-<phase>-<job12>`, 거른 ref 는
  `purge_ref_rejected`). 그러나 pod-gc(`pod_gc.py` 34행)와 취소(`api/cancel.py` `terminate_job`)는 phase_refs 값을
  그대로 terminate 한다 — 변조 행이 `pod/dms-api-…` 를 가리키면 제어면 파드를 지운다(같은 네임스페이스 — api·
  컨트롤러 Role 둘 다 pods delete, `deploy/k8s/10-rbac.yaml`). 처방: 두 경로도 `parse_ref` 를 거치게(스텁 ref
  모양 포함 — `StubPurgeRunner.ref_belongs_to_job`).
- 📝 **요청 삭제의 잔여 위험**(2026-10-08, 수용·문서화) —
  (a) 노드가 분할된 채 운영자가 Terminating 파드를 `--force` 로 지우면, 그 노드에서 실제로 아직 도는 launcher 가
  정리 **뒤에** `<base>/<job_id>/<phase>` 를 다시 만들 수 있다. 정리가 끝나기 전이면 `artifact_reappeared` 로 k8s
  단계부터 다시 하지만, 끝난 뒤면 **행 없는 디렉터리**가 남고 DMS 는 그것을 지우지 않는다(「DB 에 없는 디렉터리 =
  고아」 추론 금지) — 운영자 판단(deploy/README §12). (b) 정리 대기 중 이미지를 이 기능 이전으로 롤백하면 옛 컨트롤러는
  아웃박스를 모른다 — 다시 올리면 재개. (c) 최종 scrub 뒤에 늦게 들어온 그 id 의 이벤트는 retention(30일)이 지운다.
  (d) `requests.commit_order` 는 현존 행 기준 단조라 최신 요청을 지우면 번호가 재사용된다(UNIQUE·커서 안전).
- 📝 **로그인 감속은 프로세스 메모리**(2026-09-07, `api/login_limiter.py`) — dms-api
  레플리카가 N 이면 실효 상한이 10×N/분, 파드 재시작이 창을 비운다. replicas 1 전제
  (40-api.yaml 의 빌드 락 제약과 같은 전제)라 지금은 정확하다. 레플리카를 늘리면
  DB 테이블(login_attempts) 기반으로 옮겨야 한다.
- 📝 **비밀번호 전송 봉인은 능동 MITM·replay 를 막지 않는다**(설계 전제,
  `api/password_transport.py` docstring) — 페이지 JS 를 바꿔치기할 수 있는 공격자는
  인증서 신뢰(사용자 CA 설치/공인 CA)로만 막는다. 봉인이 막는 것은 TLS 종단 이후
  클러스터 내부 평문·TLS 검사 프록시·프록시 본문 로그·인증서 경고 무시 접속의 수동
  도청이다. **x-admin-token 부트스트랩 경로는 평문 허용**(운영자 curl, 토큰 보유 =
  admin) — 봉인해 보내면 그대로 받으므로 스크립트가 `seal_with_info` 를 쓰면 닫힌다.
- 📝 **감속 IP 키는 프록시 헤더 신뢰에 기댄다** — ingress-nginx 뒤에서는 X-Real-IP
  가 정확하지만, 프록시 없이 직접 닿는 배치(port-forward)에선 클라이언트가 헤더를
  심어 IP 키를 피할 수 있다. 사용자명 키가 주 방어라 감속 자체는 유지된다.
- 📝 **인증번호 발급·비밀번호 변경 엔드포인트는 감속 밖** — 코드 추측은 코드당 5회
  상한으로 막히지만, 발급 요청 자체(메일 폭주)는 제한이 없다(메일러가 stub 인 동안
  실해가 없어 보류).
- 📝 **노드 CPU 상한은 다른 파드의 requests 를 보지 않는다**(2026-10-16, `src/dms/node_cpu.py`
  docstring). 상한 = floor((allocatable − launcher 100m − `DMS_NODE_CPU_RESERVE_MILLICORES`) / 1000)
  의 예비는 **설정값**이다 — 컨트롤러·에이전트는 DaemonSet·제어면·빌드 파드의 실제 requests 를 볼 수
  없다(클러스터 전역 pods list 권한이 필요해 보안 범위를 넓히는 결정이라 하지 않았다). 노드의 DMS
  외 requests 합이 예비 + 내림 여유를 넘으면 워커가 그 노드에 안 들어가 Volcano 대기(Pending 상한
  없음)에 머문다 — 운영자가 `kubectl describe node` 의 Allocated resources 를 보고 예비를 올린다.
  정확히 하려면 에이전트가 자기 노드의 파드 requests 합을 보고하는 경로(노드 범위 pods list)가 먼저다.
- 📝 **워커 CPU 요청으로 배치 미리보기가 Volcano 에서 줄을 선다 — 먼저 끝난 형제의 미리보기 TTL(24h)
  위험**(2026-10-16). 예전엔 잡이 전부 BestEffort 라 Volcano 대기가 5~10초였다. 이제 노드 코어를 예약하므로
  배치의 미리보기 자식들이 노드 단위로 줄을 서고(테스트베드는 노드당 워커 1개), Previewing 배치는 미리보기
  단계 자식이 하나라도 남으면 PreviewReady 로 넘어가지 않는다(`batch_orchestrator.py` 미리보기 집계) —
  그동안 먼저 끝난 형제의 `preview_ttl`(24h, 미리보기 성공부터)이 흘러 `preview_expired` 로 Rejected 될 수
  있다. Running 배치에서 슬롯을 기다리는 확인된 자식도 같다. planner 는 자식을 DMS 예약이 적은 노드로 흩지만
  (2026-10-16 리뷰, `placement._pick_nodes` — 그 전엔 이름순 앞 N대 고정이라 max_concurrency 를 줄여도 자식이 같은
  노드에 줄을 섰다), 적격 노드들이 동시에 담는 자식 수(≈ 노드 수 × 노드 상한 ÷ (자식의 노드 수 × 노드당 프로세스))를
  넘는 max_concurrency 는 그만큼 줄을 세운다 — 큰 배치는 max_concurrency 를 그 수 이하로 두거나 나눠 내라(테스트베드:
  노드 3·상한 1·자식당 1노드×1프로세스면 3). 근본 해법(배치 안 TTL 시작을 「배치 PreviewReady」로 미루기 등)은 결정 대기.

---

## 5. 프로세스 규약·기록 (지속)

- **파킹은 이 파일에** — SDD 레저(`.superpowers/sdd/`)는 `.gitignore = *` 라 git 에
  안 들어간다(슬라이스 11·12·13 레저 소실). 워크트리와 함께 사라지므로 파킹은 여기 적는다.
- **워크트리 공유 중 커밋은 pathspec 으로** — `git add` 로 인덱스를 거치면 같은 워크트리의
  다른 세션 커밋에 내 파일이 섞이거나 남의 파일을 삼킨다(2026-08-11 실제 발생: 슬라이스 20
  Task 2 의 5파일이 커밋 `6bc2ecb` 에 통째로 흡수 — 코드 손실 없이 히스토리만 섞임).
  **항상 `git commit -- <경로들>`.**
- **포탈 빌드는 origin 에 push 된 커밋만 본다**(`--depth 1 --branch`) — 로컬 전용 커밋은
  빌드 불가. 배포 절차에서 범프 커밋을 먼저 push 해야 한다.
- **각 항목은 근거를 `파일:줄`로** 남긴다. 의도적 보류는 "미구현"이 아니라 **결정**이므로
  ⏸ 로 표시한다.
