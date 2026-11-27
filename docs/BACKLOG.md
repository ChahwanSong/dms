# DMS 백로그 (열린 항목)

**남은 일만** 적는다. 완료된 것은 [`CHANGELOG.md`](CHANGELOG.md)(2026-10-21 테스트베드 분리 이후 — 그 전 기록은
[`history/changelog-2026-09-to-10-21.md`](history/changelog-2026-09-to-10-21.md)·[`history/changelog-2026-08-slices.md`](history/changelog-2026-08-slices.md)),
지금 동작은 [`ARCHITECTURE.md`](ARCHITECTURE.md), 배포·운영 절차는 [`deploy/README.md`](../deploy/README.md)(테스트베드는
[`deploy/testbed/README.md`](../deploy/testbed/README.md)), 왜 그렇게 지었나는 [`docs/history/specs/`](history/specs/).

각 항목은 근거를 `모듈.심볼`(셸·yaml 은 파일과 함수·키 이름 — 줄 번호는 흐르므로 쓰지 않는다)로, 성격을 아이콘으로 표시한다:
🔧 착수 가능(결정만 하면 됨) · ⏸ 의도적 보류(결정이지 결함 아님) · 🔒 외부 자원에 막힘 · 📝 문서/알려진 해석 · 🔍 검증 필요 ·
🐞 알려진 결함 · 🧹 정리 · 💡 요청이 오면.

**기준**(2026-11-06): dms-k8s 라인 — k8s 신원 모드 전용(모든 잡 root, 권한 = k8s RoleBinding 맵핑). LDAP·보조 그룹·dms-ssc
클러스터 항목은 dms-ssc 라인의 몫이라 여기 없다. 해소된 항목은 지운다 — 코드·테스트·다른 문서가 「제목」으로 인용하는 것만 한 줄
묘비(`~~제목~~ — 해소(날짜, CHANGELOG 「…」)`)로 남긴다(§5).

**옛 번호**: 코드·테스트 주석의 `BACKLOG §2.1`~`§2.5` 는 2026-08 구조의 번호다(슬라이스 24·27~30 이 닫은 항목 —
[`history/changelog-2026-08-slices.md`](history/changelog-2026-08-slices.md)). 지금의 「## 2. 의도적 보류」와 다르다.

---

## 1. 기능 확장 (사용자가 보류 결정)

포탈·잡 기능의 확장과 후속 — 결함이 아니라 아직 안 만든 기능·결정 대기다. 설치·운영 후속은 아래 소절에 있다(2026-11-06 신규 →
k8s 신원 모드 → 포크 → 사용량 → 포탈 개선·배치 스케줄러 → 사내 SSO → 마운트 현황·노드 기록 → sync 진행률·단계별 등록 → ③~⑦ 설치 묶음 →
테스트베드).

- ~~**아티팩트 보존 정책(자동 만료)**~~ — 해소(2026-10-26, CHANGELOG 「BACKLOG 결정 묶음 구현」 — controller `artifact-expiry` 루프,
  `DMS_ARTIFACT_RETENTION_DAYS` 기본 1500·0 = 끔, 열람은 410 `artifact_expired`, 운영은 deploy/README §12b). 남은 후속:
  - 🔧 **만료된 scan 리포트를 읽으려는 화면** — 사용자 스캔 경로 통계(`routes_scan_paths`)는 만료된 scan 잡에도 읽기 예산
    (`_MAX_READ_ATTEMPTS`)을 쓰고, 사용량 분석(`routes_usage`)은 요약 캐시(`scan_report_digests`)에 없는 만료 리포트를 매번
    읽으려다 「못 읽음」으로 센다(ENOENT 라 싸다; 이미 캐시된 이력은 그대로 보인다). `artifacts_expired_at` 이 있으면 건너뛰기를
    권장. 관리자 scan-stats 는 만료 뒤 자연스럽게 404 `no_scan_report`.
- 🔧 **작업(요청) 삭제 후속**(2026-10-08 범위 밖 — 운영은 deploy/README §12):
  - ~~**작업 5000개(또는 항목 10000개)를 넘는 배치의 삭제**~~ — 해소(2026-11-19 사용자 결정 「배치 단위 삭제 상한 기본 50000개로」,
    CHANGELOG 같은 날 항목): 상한은 설정 `DMS_BATCH_DELETE_MAX_CHILDREN`(기본 50000, 1–50000), 작은 배치(≤ `DMS_BATCH_DELETE_SYNC_MAX_CHILDREN`
    기본 500)는 지금처럼 한 트랜잭션, 큰 배치는 묘비(상태 `Deleting` + `batch_deletions`) + 컨트롤러 `batch-delete` 청크(≤ 100) — 재개는
    묘비 행의 커서·백오프. 함께 해소: `update_meta` 의 무조건 쓰기 → `status <> 'Deleting'` 조건(0행 = 409 `batch_deleting`), 포탈 상태
    라벨 「삭제 중」. 남은 후속(아래 2026-11-19 📝 다섯).
  - 📝 **이미 열려 있던 끝난 배치 상세 탭에 「삭제 중」이 뜨지 않는다**(2026-11-19 d35 테스트베드 실증) — 끝난(Completed·Cancelled) 배치 상세는
    `useBatch` 가 주기 폴링을 멈춘다. 탭 복귀(visibilitychange)·재연결 때는 5초 staleTime 이 지났으면 요약을 다시 읽어 「삭제 중」·404 를 보므로,
    옛 모습(편집·재실행 버튼이 있는 화면)은 그 화면이 **계속 보이는 동안**만 남는다 — 나란히 띄운 창, 다른 사람이 지우는 동안 계속 보는 화면(그
    버튼은 서버가 409 `batch_deleting`/404 로 막는다 — 데이터 위험 없음). 고친다면 상세가 상태 `Deleting` 을 받았을 때만 짧게 폴링하거나, 끝난
    배치도 느린 주기(예 30초)로 요약만 다시 읽기.
  - 📝 **묘비는 되돌릴 수 없다**(2026-11-19 설계 C9) — 「삭제 취소」를 넣으면 반쯤 지워진 살아 있는 배치가 생긴다. 막히면 「삭제 지연」으로
    남아 저절로 다시 시도한다(문제 작업을 취소하거나 조용한 창이 지나면 풀린다). 영구히 막히는 변조 행(묘비 뒤 다른 배치 항목이 자식을
    가리킴 등)은 운영자가 그 항목·작업을 정리해야 끝난다 — 화면 동선(「막힌 작업 보기」 외의 처방)은 운영 사례가 생기면.
  - 📝 **백그라운드 삭제 중 읽기 경로의 일시 불일치**(2026-11-19 — 원래 설계 D1 「한 커밋 일관성」을 이 경로에선 포기) — 5만 배치면 1–2분
    동안 남은 자식이 작업 목록·사용량 분석에 보이며 줄어든다(배치 칸 「삭제 중 N%」가 그 사실을 말한다). 읽기 경로마다 「삭제 중」 필터를
    두는 것은 열 곳이 넘어 하지 않았다.
  - 📝 **백그라운드 경로 감사의 자식 경로·결과 메시지 미보존**(2026-11-19 설계 D4 — 판단) — 501개 이상 배치는 자식별 32 KiB 스냅숏 대신
    청크 요약(`('batch','delete_chunk')` — id·상태·잡 id·실행 신원 지정·결과 소유)만 남는다(5만이면 자식별 ~70MB·감사 화면 1000쪽).
    자식별이 필요하면 `delete_batch_chunk` 의 `_purge_locked(per_request_audit=False)` 를 True 로 한 줄(디스크·감사 화면 비용은 위 숫자).
  - 📝 **목록 요약은 5000 에서 세기를 멈춘다**(2026-11-19, `request_purges.BATCH_LIST_COUNT_CAP`) — 3초 폴링 비용을 지키려고(상한 50000 으로
    세면 큰 배치 하나에 폴링당 PG 190–197ms). 5000 이 넘는 배치의 정확한 수·판정은 확인 창의 미리보기(`POST
    /api/admin/requests:delete-preview`)에서만 보인다(목록·상태 줄은 「5,000개 이상」).
  - ~~**배치 단위 삭제 CAS 를 최대 commit_order 까지 묶는 강화안**~~ — 해소(2026-11-19): 본문 `expected_max_commit_order`(선택, strict)를
    수와 함께 CAS 로 본다(동기 세기 직후·묘비 B단계·A단계). 포탈은 확인 창 미리보기의 최대를 늘 보낸다(옛 서버로 미리보기가 404 면 수만).
  - 📝 **항목만 남은 배치 묶음은 화면에 안 보인다** — 배치 행·자식 없이 항목만 남은 묶음(옛 add_item·replace_items 경합, 롤링 업데이트
    겹침 동안 옛 api 파드의 add_item 잔재)도 `request_purges.delete_batch` 가 expected 0 으로 지우지만 자식 행이 없어 작업 목록에
    나오지 않는다 — API 로 batch_id 를 지정해야 한다(CHANGELOG 이관본 [`history/changelog-2026-09-to-10-21.md`](history/changelog-2026-09-to-10-21.md)
    「전체 작업: 배치 단위 삭제 + 「배치」 열」).
  - **작업 목록 검색의 후속**(2026-10-25 검색칸 추가 — `RequestsRepository.list` docstring): (1) 부분 일치는 인덱스를 못 타
    드문 검색어면 범위 전체를 훑는다(PG16 32만 행 0.25~0.34s, 단일 커넥션 RLock — 포탈은 검색 중 폴링을 10초로 늦춘다).
    느려지면 `pg_trgm` GIN(대상 표기 식·requester_id)을 검토 — PG 전용 DDL·확장 권한·2글자 검색어 무효가 따른다. (2) 슬라이스
    36 이전 형식의 sync·rm 키(끝에 옵션 지문)가 남아 있으면 그 행은 hex 검색어에 거짓 일치할 수 있다(scan 만 지문을 뗀다).
    (3) 요청 ID 부분 일치·단일 통합 검색창은 넣지 않았다(사용자가 말한 축은 요청자·배치 이름·대상 경로).
  - ~~**요청 상세·결과 카드의 배치 링크**~~ — 해소(2026-11-11 — 상세 응답에 관리자만 `batch_exists`·`batch_name`
    (`routes_requests.get_request`), 화면은 `RequestDetail.BatchRefLink`·`OutcomeCard` 가 작업 목록 `BatchCell` 규칙으로).
  - **정리 루프 처리량** — 틱(15초)당 20행(`request_purger.ROW_LIMIT`)이고, 파드가 남은 행은 첫 스윕(삭제 → Terminating)과 둘째
    스윕(사라짐 확인) 두 번이 필요해 실처리량은 틱당 ~10행이다 — 작업 1000개 배치는 ~27분(2026-10-10 검증 실측, 가짜 k8s),
    2000개면 ~1시간, 5000개면 ~2.5시간, 50000개(상한, 2026-11-19)면 ~25시간(외삽 — 미측정)이다. 2026-11-19 부터 백그라운드 배치 삭제가
    넣은 행은 배치마다 별도 레인(`request_purges.purge_group`)이라 `due()` 가 레인을 돌아가며 집는다 — 큰 배치 뒤의 다른 삭제가 그 배치의
    첫 스윕(~10시간)을 기다리지 않는다(굶김만 막는다 — 처리량은 그대로; 결과 파일 만료 행은 대기 50행 상한 — `artifact_expiry.BACKLOG_MAX`;
    회차 보존 정리 행은 대기 100행 상한 — `batch_round_prune.BACKLOG_MAX`). 순서를 바꿔도(스윕한 행 먼저) 총
    스윕 수가 같아 총 시간은 그대로다 — 줄이려면 상한을 올리거나(50, 틱 예산 `TICK_BUDGET_SECONDS` 안에서 실 apiserver 지연
    실측 후) 같은 틱 끝에 방금 스윕한 행을 한 번 더 세는 것.
  - **정리 루프 틱 비용이 대기열 크기에 비례**(2026-10-11 검증 지적, 일부 해소) — purge 파드를 못 띄우는 동안(purge_no_node
    등) 큰 배치 삭제의 행이 purging 에 쌓이면 전역 단계(`_reap`·`_settle` 의 `_live_rows`)가 틱마다 두 번 purging·files 행
    **전부**를 읽고, `_complete` 가 purging 행마다 FS stat(`artifact_trash.entry_states`)을 한다. 이 시간은
    `TICK_BUDGET_SECONDS` 에 들지 않고, 컨트롤러는 루프를 한 프로세스에서 차례로 돌려 planner·stepper 틱까지 밀린다.
    원 행 재등장 확인은 묶음 한 번으로 바꿨다(`present_targets` — 로컬 PG 1만 행 1.3초 → 0.37초). 남은 선형 비용:
    `in_stage` 전량 읽기 + 행마다 FS stat(공유 FS 에선 로컬보다 몇 배 느릴 것). 후보: 백오프 중인 행(next_attempt_at > now)은
    `_complete` 를 건너뛰기, 틱당 완료 판정 행 수 상한(오래된 것부터 돌림차례), 또는 전역 단계도 틱 예산에 넣기.
    `due()`(레인 공평성, 2026-11-19)도 due 행 전부를 정렬한다 — 아웃박스 5만 행(한 레인) + 1000행에서 limit 20 한 문장이 로컬 PG16
    171–186ms(전체 열 윈도) → 131–138ms(키 열로 순위 → 고른 행만 조인 — 같은 순서 확인). 컨트롤러 커넥션이라 readiness 와는 무관하고
    `status()` 는 22–27ms 다. 큰 삭제가 겹쳐 대기열이 수십만이 되면 레인별 LIMIT(PG LATERAL) 이나 `(COALESCE(purge_group,''),
    next_attempt_at)` 식 인덱스를 검토.
  - **지표 박제** — 잡 통계·처리량·수행시간·요청자별 집계·plan_rejected 가 전부 행 기반이라(`repositories.metrics`)
    요청을 지우면 지난 창의 값이 소급해 줄어든다(수용, 확인 창이 안내). 필요하면 일 단위 집계 테이블.
  - **purge 파드 데드라인을 넘는 trash 항목**(2026-10-09 검증 중 확인) — purge 파드는 activeDeadlineSeconds 3600 이라
    한 시간 안에 못 지우는 거대 트리(수천만 파일)는 매번 중간에 죽는다. rm 이 지운 만큼은 남으므로 재시도(백오프 최대
    15분 간격)마다 줄어 결국 끝나지만, 그동안 그 행은 지연으로 보이고 그 파드가 도는 한 시간 동안 다른 요청의 파드는
    기다린다(파드는 언제나 1개). 끊길 때 앞 이름이 하나라도 지워졌으면 그 항목은 `purge_entry_failed` 로 다음부터 혼자
    실리지만, **정렬상 첫 이름**이면 지워진 것이 없어 근거가 없다(`purge_pod_failed` — 파드 수준 실패와 구별 불가) — 그
    항목이 끝날 때까지 함께 실린 행이 매번 같이 기다린다. 실측 필요. 후보: 파드 종료 사유(DeadlineExceeded) 노출로 귀속,
    실패 행에 한해 데드라인 연장, 파드 2개(새 행 전용) 허용.
  - **정리 이벤트 뷰어** — 정리 실패의 상세(`purge_failed`·`purge_ref_rejected`·`artifact_left_at_old_base`·
    `request_purged`·만료 요약 `artifact_expiry_queued`, 전부 request_id=NULL — 만료 완료 `artifacts_expired` 만 그 요청에 붙는다)는
    events 에만 있고 포탈엔 전역 이벤트 화면이 없다. 지금 표면은 작업 목록 툴바의 정리 현황(`GET /api/admin/request-purges` — 대기·지연
    건수와 첫 지연 사유)뿐이다.
- ~~**rm 경로의 다른 요청·배치와의 겹침 판정**~~ — 해소(2026-10-27, CHANGELOG 「BACKLOG 결정 묶음 구현」 — planner 1b 의 대칭
  `path_conflict` + stepper 실행 관문 `_overlap_hold`, ARCHITECTURE §4 「rm 경로 겹침 잠금」). 남은 한계:
  - 📝 **루트가 포개진 다른 이름의 스토리지**(`/c/m` 과 `/c/m/t` 를 따로 등록)끼리는 비교하지 않는다(`domain.path_conflict`
    docstring — 두 작업이 한 노드의 같은 이름 공간에서 도는지 모른다). 필요하면 managed_root 를 붙인 실제 경로로.
  - 📝 **둘째 관문(GATE_EXECUTION)에서 오래 기다린 잡**은 기다리기 전의 exec_preflight 판정으로 실행한다 — 이 배포 전에
    계획된 행·경합에서만 생긴다. 처방 후보: 보류가 길면 exec_preflight 부터 다시.
- 2026-10-26 결정 묶음 C·D(CHANGELOG 「BACKLOG 결정 묶음 구현」)로 해소 — 코드 주석이 이 제목으로 가리킨다:
  - ~~**단건 삭제 버튼(요청 상세)**~~ — 해소(2026-10-26).
  - ~~**배치 기록 삭제의 감사·CAS**(배치 화면)~~ — 해소(2026-10-26 — `BatchesRepository.delete`, 세션 관리자만).
  - ~~**배치 CSV 백엔드화**(업로드/내보내기·템플릿)~~ — 해소(2026-10-26 — `batch_csv`).
  - ~~**배치 생성 폼의 스토리지 드롭다운**~~ — 해소(2026-10-26 — `features/batches/BatchStoragePicker.tsx`).
- 🔧 **에이전트 설정 푸시** — 디렉터리 설정은 보고 응답으로 하달된다(방안 A, 2026-09-29). 그 밖의 에이전트 env 는 배포 시
  고정(DaemonSet env — 바꾸려면 `install.sh --only S11`).
- 🔧 **알림/경보** — 임계 초과의 바깥 알림(메일·메신저)이 없다. 포탈은 표시만 한다(대시보드·보안 이벤트 화면의 로그인 경보).
- 🔧 **러너 카운트 확장** — 슬라이스 15 이전 잡의 소급 백필 없음, 전송률 없음. (미리보기 수가 DB 지표 원천이 아닌 것은 의도 —
  지표 원천 `files_count` 는 실행·scan 결과만, 미리보기 수는 `preview_summary`·`result_summary` 표시용.)
- 🔧 **요청 상세 재설계(2026-10-08) 범위 밖 후보** — 출력 뷰어 안 검색(n/m)·오류 줄 강조와 이동·「크게 보기」
  대화상자(지금은 브라우저 Ctrl+F — 2,000줄 상한 안에서 전부 DOM 에 있다), URL 딥링크(`?job=&stage=&view=`),
  ConfirmPending pill 한국어 라벨, 잡 취소 2단 확인,
  공용 `Button` 의 focus-visible 링(이번엔 이 화면의 새 요소에만 달았다 — `features/jobs/ui.ts`).
- 🔧 **요청 상세 「요청 내용」(2026-10-08) 범위 밖 후보** — 노드 탈락 사유(worker_pool.rejections) 표시 — 노드 사유
  어휘(missing_target_mount·missing_tool:<t>…)를 reasonCodes 양쪽에 등록하는 일과 nsync 오독(rejections 는 dsync 판정) 해소가
  먼저. 그 밖에 큐·우선순위 클래스 원문(worker_pool.queue·priority_class — 지금은 상한으로 깎였을 때만 「high 요청 → mid 적용(정책
  상한)」)은 표시하지 않는다.
- ~~**모바일(375) 사이드바 접기**~~ — 해소(2026-11-11 — md 미만에서 메뉴·사용자 패널을 `AppShell` 의 「메뉴」 버튼으로 여닫는다,
  기본 닫힘; e2e 03 모바일 spot check 가 첫 화면 h1·열린 메뉴를 잰다).
- 🔧 **배치 목록 쪽 넘김·총수**(2026-11-11 CHANGELOG 「포탈 전수조사」의 범위 밖 — 잘림 안내만 넣었다) — 배치 목록(작업 → 배치 작업)은
  최근 생성 100건 창이다(`useBatches.BATCH_LIST_WINDOW`, `routes_batches.list_batches` 의 `limit` 기본 100·상한 `_MAX_BATCH_LIST` 1000 —
  포탈은 창 + 1 건을 받아 잘렸다는 것만 안다: 탭 건수의 「+」·목록 아래 안내문). 창 밖의 오래된 배치는 목록·탭 건수·목록의 「선택 삭제」
  (기록만)에 없다 — 상세(「배치 삭제」 포함)는 전체 작업의 「배치」 열 링크나 주소 `/admin/batches/<id>` 로 연다. 처방 후보:
  `(created_at, batch_id)` 커서 쪽 넘김(지금 정렬은 `BatchesRepository.list` 의 created_at DESC 하나라 동률을 batch_id 로 깬다) 또는 총수
  (COUNT) 응답. (배치 **상세**의 항목은 2026-11-16 부터 서버 쪽 넘김이다 — 목록은 그대로.)
- 🔧 **앱 전역 의미 글자 대비(WCAG AA 4.5:1)** — 요청 상세는 의미 있는 글자를 `text-ink/70`(흰 바탕 4.94:1)으로
  올렸지만(2026-10-08), 다른 화면은 아직 `text-muted`(#888 — 흰 바탕 3.54:1, canvas 3.28:1)를 본문·표 머리·안내문에
  쓴다: `features/jobs/JobsList.tsx`(표 머리·경로 요약·시각·빈 목록 안내), `SubmitJob.tsx`(「실제 경로」·공백 안내·
  허용 스토리지 조합 note), `formFields.tsx`, 공용 `StatusPill` neutral(「Planned」 3.28:1), 레이아웃 브레드크럼 「>」.
  처방: 화면별로 `text-ink/70` 으로 올리되(장식용 코드 라벨·아이콘은 그대로), 클래스를 단언하는 테스트가 있는지 먼저 본다.
- 🔍 **상태 전이의 일반 종단 가드(검증 필요 — 코드 읽기로만 발견, 2026-10-08)** — planner 쪽 경합(취소가 planner 의 읽기와
  Planned 쓰기 사이에 끼면 Cancelled 가 Planned 로 되살아나던 것)은 닫혔다: emit 이 `data_jobs.create_plan_and_job`(요청 행 잠금 +
  plan·job INSERT + 조건부 Pending→Planned 한 트랜잭션)이고 멱등 분기도 `requests.mark_planned_if_pending`(CAS)이다
  (`tests/test_planner.py` 경합 테스트). 남은 것: `repositories.requests._apply_state` 자체엔 여전히 종단 가드가 없어 다른 호출자가
  같은 모양의 경합을 만들 수 있고, 잡 쪽 `data_jobs.set_job_state` 도 잠금 없는 SELECT→UPDATE 라 동시 쓰기에서 나중 커밋이 이긴다.
  처방 후보: 조건부 UPDATE(`WHERE state = :expected`)·종단 상태에서의 전이 거부, 재현 테스트부터.
- 🔧 **레지스트리 인증 확장**(2026-10-08 릴리스 태그 목록 결함 수정의 범위 밖) — 태그 조회·digest·삭제는 익명 Bearer
  토큰까지만 한다(`registry._anonymous_token`). (a) 다른 호스트의 토큰 서버(GitLab `…/jwt/auth` 형태)는 요청 위조 방지로
  realm 을 레지스트리와 같은 호스트로 묶어 거부된다 — 필요하면 운영자 허용 목록(설정)으로 연다. (b) Basic·로봇 계정 자격
  증명은 다루지 않는다(비밀 저장·전달 경로가 필요 — 평문 금지 규약).
- 🔧 **워커 계정 충돌 전용 사유 코드** — 잡 이미지에 실행 신원과 같은 이름의 계정이 다른 uid/gid 로 있으면 워커 셸이 stderr
  `dms: account … exists with a different uid/gid` + exit 1 로 끝나고 화면엔 사유 없는 preview_failed/execution_failed 만 보인다
  (`execution_manifests._identity_materialize_stmt` (1) — 모든 잡이 root 라도 실행 신원 이름 → 0:0 별칭 계정을 만든다).
  `DMS_EXEC_REASON=` 마커 + EXECUTION_REASONS·reasonCodes 등록으로 승격하면 운영자가 로그를 안 봐도 된다.
- 🔧 **3홉 '경고' 등급** — 3홉 검증은 base 자체만 본다. base 의 **부모**(공용 디렉터리)가 g+w·o+w 이면
  (deploy/README §2b-3) 공유 FS 를 마운트한 다른 호스트의 사용자가 base 옆에 디렉터리를 만들 수 있는데 화면엔 초록이다.
  부모에 o+x(711·755)가 있어도 초록인데, 그러면 모든 uid 가 base 아래 0644 아티팩트를 직접 읽는다(§2b-3). 처방: 부모 mode 를
  읽어 빨강이 아닌 '경고'로 표시(저장은 막지 않음).

### 2026-11-06 문서 전면 정리에서 나온 후속 — 신규 14

배포 문서를 프로덕션 기준으로 다시 쓰면서(감사·prod 렌더 대조·코드 대조) 찾은 것. 지금은 deploy/README 가 현 상태와 우회 방법을
적는다. 번호는 이 묶음 안의 번호다(테스트베드 항목은 「(테스트베드)」 표시).

1. 🔧 **앱 튜닝 키를 values.env 로** — 메일 백엔드 고정(`DMS_MAILER_BACKEND` — 효과 값은 `mail_config.resolve_mail_config`: 포탈 DB >
   env > 기본 `stub`)·로그인 보호(`DMS_LOGIN_*`)·결과 보존일(`DMS_ARTIFACT_RETENTION_DAYS`) 같은 dms-config 키는 values 키가 없다. 영구
   변경은 사이트 체크아웃의 추적 파일 `deploy/overlays/prod/patch-config.yaml` 로컬 수정 → `install.sh --only S11` 이고(deploy/README
   §3-4 — 새 DMS 커밋으로 올릴 때 그 수정을 들고 가야 한다), ConfigMap 손 수정은 다음 설치기 실행(매 실행 재렌더 + S11 apply)이
   되돌린다. 처방: `render.sh` 가 values 의 선택 키를 patch-config 에 싣는다(빈 값 = 코드 기본). 메일 릴레이 토큰을 env 로 두려면
   (`DMS_MAIL_RELAY_TOKEN` — 주소도 env 일 때만 쓴다, `mail_config` docstring) dms-secrets 4키 밖 키가 필요한데 설치기 시크릿 게이트
   (`install.sh` `secrets_gate` — 4키)에 그 자리가 없다 — 지금은 포탈 메일 설정(저장 암호화)이 길이다.
2. 🔧 **렌더의 values 형식 검사** — `render.sh` 는 `LOCAL_ADMIN`·`EMAIL_DOMAIN`·`PORTAL_DOMAIN`·`PORTAL_VIP` 를 비었는지·`REPLACE_` 인지만
   보고 형식은 보지 않는다. `LOCAL_ADMIN` 오타면 첫 관리자가 특권 목록(`DMS_PRIVILEGED_REQUESTERS = root,admin,<LOCAL_ADMIN>`) 밖이
   되고, `EMAIL_DOMAIN` 오타면 인증 메일이 엉뚱한 주소로 간다. 처방: 계정 이름 규칙(`accounts._USERNAME_RE`)·도메인·FQDN·IPv4 형식
   검사 + 렌더 결과의 `DMS_PRIVILEGED_REQUESTERS` 덮기 강제(지금 렌더가 강제하는 덮기는 `DMS_JOB_IMAGE`·`DMS_BUILD_REGISTRY`·
   `DMS_BUILD_BUILDER_IMAGE` 뿐이다).
3. 🔧 **S12 가 메일 stub 을 보지 않는다** — 메일 백엔드 `stub` 은 인증번호를 응답에 돌려준다(`routes_auth` 인증번호 발급의
   `stub_code`). 이 상태로 포탈을 열면 누구나 남의 아이디로 가입해 그 RoleBinding 의 PV 권한을 얻는다. 지금 막는 것은 S13
   체크리스트 줄과 문서 게이트(deploy/README §6-2)뿐이다. 처방: verify.sh(S12)가 api 의 효과 메일 백엔드(포탈 DB > env)를 읽어 stub
   이면 WARN.
4. 🔧 **저장소 안 봉인 로그인 도우미** — 자동화는 세션 로그인이 필요한데(공유 토큰 Bearer 제출은 403 `privileged_not_authorized` —
   root 는 세션만) 평문 로그인은 422 `password_encryption_required` 다. 지금은 deploy/README §10-7 의 짧은 예
   (`dms.api.password_transport.seal_with_info` + `GET /api/auth/transport-key` → `password_enc`)를 손으로 옮긴다. 처방: `deploy/`
   아래 표준 라이브러리만 쓰는 도우미(쿠키 jar 출력) + 테스트.
5. 🔧 **`DMS_ADMIN_TOKEN` 회전·끄기** — 토큰 부트스트랩(`routes_auth.create_admin_account` 의 `x-admin-token` 경로)은 첫 관리자 뒤에도
   열려 있어 admin 계정(특권 목록 이름 포함 — `guard_privileged_account` 를 거치지 않는다)을 만들 수 있다. 토큰은 필수 env 라
   (`config.Settings` 필수 4키) 끌 수 없고 회전 도구도 없다 — 지금은 ns `dms` 시크릿 읽기 권한을 DMS 최고 권한으로 다루고, 회전은
   시크릿 수정 → `--only S11`(deploy/README §3b-3·§3b-4). 처방: 첫 관리자 뒤 부트스트랩을 닫는 스위치(또는 계정이 하나라도 있으면
   거부) + 회전 절차.
6. 🔧 **포탈 빌드 파드 tolerations** — 빌드·빌드 프리플라이트 파드에 tolerations 가 없다(`build_manifests` — 0건) → 빌드 노드
   (= 인터넷 노드)는 NoSchedule taint 없는 워커여야 한다(deploy/README §8 0)). taint 를 건 전용 빌드 노드를 쓰려면 컨트롤 상태에
   tolerations(또는 그 노드의 taint 를 견디는 옵션)를 더한다.
7. 🔧 **포탈 TLS 인증서 만료 감시** — `PORTAL_TLS_SECRET`(기본 `dms-portal-tls`)의 만료를 보는 곳이 없다(S12·포탈 둘 다). 만료되면
   LB 뒤 Traefik 이 SNI 로 고르는 그 인증서로 포탈 HTTPS 접속이 인증서 오류로 실패한다(2026-11-16 전에는 Envoy). 지금은 정기 작업 달력
   (deploy/README §14-2)뿐. 처방: S12 WARN(만료 30일 전 — `ingress-probe.sh` 가 이미 s_client 로 인증서를 읽는다) + 포탈 개요 경고.
8. 🧹 **빌드 레지스트리 코드 기본값 `pkg-01:5000`** — `config.Settings.build_registry` 의 코드 기본값이 테스트베드 주소다
   (`tests/test_config_phase3c.py` 가 고정). prod·testbed-k8s 렌더는 덮으므로 동작엔 영향이 없지만 env 가 빠지면 조용히 테스트베드 이름을
   쓴다. 처방: 기본을 빈 값 + 빌드 화면에서만 「빌드 레지스트리 미설정」 — 테스트 기대도 함께. (base `deploy/k8s/20-config.yaml` 의 값은
   2026-11-06 사이트 중립 자리표시자로 바꿨다 — 코드 기본만 남았다. `deploy/docker/Dockerfile.agent` 의 ARG 기본 `pkg-01:5000/…:dev` 도
   같은 날 지웠다 — 두 빌드 경로가 늘 `--build-arg` 로 주고, 빠지면 FROM 이 실패한다.)
9. 🧹 (테스트베드) **테스트베드 도구의 ssc 기본값·분기** — `deploy/testbed/dms-shared-fs.yml` 의 기본 `dms_cluster` 가 `dms`(ssc)이고
   LDAP 픽스처 분기(ssc)가 남아 있다 — dmsk8s 는 늘 `-e dms_cluster=dmsk8s` 로 돌려야 한다(deploy/testbed/README.md §4). 이 브랜치 기본을
   `dmsk8s` 로 바꾸거나 ssc 분기를 걷을지 결정(동작 변경). `deploy/docker/registry-setup.sh` 도 이 브랜치에선 ssc 전용(dmsk8s 거부 —
   옛 deploy/README §0 은 지웠다) — 지울지 함께 결정.
10. 🧹 (테스트베드·base) **base `46-ingress.yaml`·`47-metallb-public-pool.yaml` 의 ssc 값**(`dms.local`·`10.20.20.100/32`·pkg-01 미러 주석) —
    prod 는 `patch-remove-ingress.yaml` 로 지우고 testbed-k8s 는 패치로 덮는다. 소크 뒤 testbed-k8s 의 Ingress·MetalLB 를 지울 때 base 에서
    파일째 걷는다 — ⑥ 「소크 뒤 Ingress·MetalLB 삭제」와 한 묶음. (입구 Ingress `dms-portal-traefik` 은 base 앱이 아니라 플랫폼 층
    `deploy/platform/ingress` 라 이 정리와 무관하다.)
11. 🔧 **dms-api·dms-controller requests 결정** — base `40-api.yaml`·`41-controller.yaml`(렌더 그대로)의 requests 는 100m/256Mi(limits
    1/1Gi)이고 설계 v6 2-1 표의 dms-api 250m/512Mi 와 다르다. 마스터 자원 예산(deploy/README §0-2)은 렌더 값을 인용한다. 실측(로그인
    scrypt 슬롯·큰 배치 삭제의 RLock·사용량 분석 창)으로 정한다.
12. 🔒 (테스트베드) **testbed 저장소 후속**(`~/dms-dev/testbed` — 이 저장소의 세션은 고치지 않는다) — 옛 deploy/README 절 번호를
    가리키는 곳을 [`deploy/testbed/README.md`](../deploy/testbed/README.md)(부록 Z 대응표 — deploy/README)로: README `§13-2`·「Shared-FS
    layout: … (DMS README §2)」·dmsk8s DB URL(pkg-01 `dmsdb_k8s` — 낡음: T7 뒤 앱 DB 는 CNPG `dms-pg`, pkg-01 URL 은 `dms-secrets-prev` 의
    되돌림 값), `docs/ARCHITECTURE.md` 의 `§2e-6` 참조(§19.6)와 §17 의 같은 DB URL·「아래 절 번호는 dms-ssc 판 기준」 문장. (`docs/ARCHITECTURE.md`
    의 `§2g-7`·`§13-6b` 참조와 `group_vars/dmsk8s.yml` 의 `2g-7` 주석은 2026-11-16 Traefik 교체 판에서 해소 — 사본 sync, 커밋은 사용자. 이제 §2g 만
    가리킨다.) 그 저장소의 §2e 계열·§2g(하위 번호 없이)와 §1·§2·§3/§3b·§4/§5·§6·§8·§10-7 참조는 같은 주제의 일반판 절에 떨어지므로 그대로 둬도
    된다(deploy/README 머리의 번호 정책).
13. 감사(2026-11-04~05 라이브 기능 전수 점검)에서 남긴 것:
    - 🔧 **실행 시한 초과의 전용 사유 코드가 없다** — vcjob 실행 시한 초과는 잡을 TimedOut 으로 끝내지만 사유는 `execution_failed` 다
      (`stepper.JobStepper._poll_execution` 의 TIMED_OUT 분기 — 2026-11-05 시한 분류 수정 전엔 이 분기에 닿지 못했다). 화면 제목은
      「시간이 초과」인데 사유 줄은 「실행에 실패」. 처방: `execution_timed_out` 을 양쪽 등록(`reasonCodes.json`·`api.ts` REASON_MESSAGES)하고 그
      분기에 쓴다 — 사전 점검·미리보기·재확인은 이미 전용 코드(`preflight_timed_out`·`preview_timed_out`·`execution_recheck_timed_out`)다.
    - 📝 **로그아웃 뒤 옛 서명 쿠키가 살아 있다**(설계 한계) — 세션은 서버 저장소 없는 서명 쿠키(starlette `SessionMiddleware`, 쿠키
      `dms_session`, 수명 14일 = starlette 기본 `max_age`)라 로그아웃(`routes_auth.logout` — `session.clear()`)은 그 브라우저의 쿠키만
      비운다. 그 전에 복사된 쿠키는 14일 동안 유효하다. 끊는 길은 비밀번호 변경·관리자 재설정(세션 세대 `session_epoch` 회전 —
      `auth.current_identity` 가 401 `session_revoked`), 계정 비활성화(비활성 동안만 — 401 `account_disabled`, 다시 켜면 옛 쿠키가 산다, §4 항목),
      `DMS_SESSION_SECRET` 회전(전원)이다(deploy/README §10-6). 처방 후보: 로그아웃도 세대를 회전(그 계정의 다른
      세션도 끝난다 — 의도 확인), 또는 서버 측 세션 저장소. §4 「비활성화는 세션 세대를 회전하지 않는다」와 같은 뿌리.
    - 📝 (테스트베드) **워커 CPU** — 워커 2 vCPU 에 DMS 밖 requests 가 700m 를 넘으면 잡 워커가 노드 CPU 상한에 걸려 Volcano 에서 무기한
      기다린다(§4 「노드 CPU 상한은 다른 파드의 requests 를 보지 않는다」). **사용자 지시로 손대지 않는다 — 기록만**(2026-11-05). 2026-11-26 에
      실제로 걸렸다: k8s-w1 요청 합 705m(남음 1095m)라 launcher 100m + 워커 1000m 가 5m 모자라 nsync·2노드 dsync 갱이 영구 Pending(§4 같은 항목).
    - 📝 **launcher 파드에 `dms.io/job-id` 라벨이 없다** — vcjob 의 worker task 템플릿은 `dms.io/job-id`·`dms.io/task` 를 달지만
      (`execution_manifests._worker_task_metadata` — 자기참조 anti-affinity 용) launcher task 템플릿에는 metadata 가 없다
      (`execution_manifests.build_volcano_job`·`_build_nsync_job`). `kubectl -l dms.io/job-id=<id>` 로는 launcher 가 안 잡힌다 — Volcano 가
      다는 `volcano.sh/job-name` 으로 찾는다(DMS 도 그렇게 찾는다 — `purge_runner` docstring). 처방: launcher 템플릿에도 같은 라벨 — vcjob
      지문(`dms.io/spec-fingerprint`)이 바뀌므로 배포 순간 409 를 맞은 진행 중 제출이 채택되지 않는 영향(`execution_volcano.adoption_problem`)을
      먼저 본다.
14. 📝 **배포 문서의 「미검증」 단계** — deploy/README·prod README·deploy/testbed/README 에 「미검증」으로 표시한 사실·단계(특히 §5-6 재해
    복구 — 전체 재설치 뒤 시크릿 사본 복원·S2 의 기존 시크릿 재사용·레지스트리 이미지 복원·`pg_restore` 순서)는 실 클러스터 리허설로
    확인하고 표시를 지운다. 리허설은 일회용 클러스터(⑦ T9 의 k3s 방식) 또는 테스트베드 점검 창에서.

### dms-k8s RBAC·PV(k8s 신원 모드) 후속 — 2026-10-25

설계 `docs/history/specs/2026-10-25-dms-k8s-rbac-pv-design.md`(§10 사용자 결정 + 슬라이스 1·2 의 열린 항목 + 보안 리뷰).

- ✅ **설계 §10 결정(2026-10-26 사용자)** — 결정 대기였던 (a)~(l) 을 모두 정했고 (h)·(i) 말고는 구현했다
  (CHANGELOG 「dms-k8s 결정 항목 반영」 — 현재 동작은 ARCHITECTURE §3 불변식 12·§7). 남은 것과 지금도 쓰는 용어:
  - ⏸ (h) **Pure FlashBlade 는 보류**(사용자 — 2026-10-26 결정 묶음에서 다시 「백로그에 남겨둠」) — PV 마다 별도 파일시스템(export)이라
    「스토리지 한 번 마운트 + 하위 폴더」 모델(`pv_paths`) 밖이다. 대안: PV 마다 스토리지로 등록하거나 에이전트가 필요할 때 그 export 를 마운트.
  - 🔒 (i) **멀티 클러스터 라이브 확인** — 지금은 단위 테스트 + 같은 클러스터 이중 등록까지다. 두 번째 클러스터가 있어야 한다(§3).
  - 📝 (j) **「운영자」 = 특권 목록·부여 관리자** — 문서·화면의 「면제자」·「특권 운영자」는 같은 뜻(`can_run_as_root`).
  - 📝 (k) **「사용자 지정」으로 낸 작업은 그 사용자 목록에 보이지 않는다** — requester_id 격리 그대로(사용자 결정).
  - ~~(l) root sync 의 결과 소유권~~ — 결정·구현(2026-10-26 — 목적지 스토리지 기본 소유자, 특권 운영자 옵션 `pv_owner: "pv_root"`;
    CHANGELOG 「스토리지 기본 소유자(uid:gid) — PV 목적지 sync 결과 소유」).
- ~~**직접 경로 목적지가 PV 루트 아래일 때**의 결과 소유~~ — 결정·구현(2026-10-26 결정 (4), CHANGELOG 「BACKLOG 결정 묶음 구현」 A1 —
  `k8s_access.direct_dest_owner`).
- ~~**overlays/prod 의 LDAP 필수**~~ — 해소(2026-10-25, CHANGELOG 「dms-k8s RBAC·PV」 — prod 렌더의 LDAP 0건, `tests/test_prod_k8s_overlay.py`).
- ~~**PostgreSQL 실 DB 미실행(슬라이스 1·2)**~~ — 해소(2026-10-26, CHANGELOG 「PostgreSQL 로 k8s 테스트 실행 + 자리표시자 리터럴 결함」 —
  conftest `db` 의 `DMS_TEST_DB=postgresql` + `DMS_TEST_PG_URL`).
- 💡 **PV 주소 기반 사용자 스캔 경로** — 사용자 스캔 경로(`routes_scan_paths`)는 k8s 모드에서 면제자 전용이다(422
  `scan_path_unrestricted_only` — 일반 사용자에게 열면 맵핑 밖 PV 서브트리의 통계를 보는 길이 된다). scan 자체가 특권 운영자 전용이라
  (`k8s_access.scan_allowed` — §10 결정 (g)) 요청이 오면 PV 주소로 등록·판정하는 길을 만든다.
- 🔧 **에이전트 채널의 노드별 자격(보안 리뷰)** — 공유 토큰(모든 노드 에이전트 보유, role admin) 보유자는 `x-dms-actor: node:<지정
  노드>` 로 행세해 `POST /api/agent/k8s-sync/claim` 에서 대상 클러스터의 리더 토큰을 받을 수 있다(`api.routes_agent` — 설계가
  받아들인 위험, 완화는 **읽기 전용** 리더 SA: rolebindings·pvc·pv·sc list + namespaces/kube-system get). 같은 행세로 **결과 POST 에
  위조 인벤토리**를 올려 맵핑(RoleBinding → 권한)을 지어낼 수도 있다(2026-10-26 push 보안 리뷰). 처방 후보: 노드별 토큰(DaemonSet
  이라 projected SA 토큰 + TokenReview 로 노드 신원 확인) 또는 claim 응답 토큰을 노드 공개키로 봉인 — 오면 claim·result 둘 다 그
  자격에 묶고, 서버가 스스로 확인할 수 있는 것(클러스터 지문 = kube-system uid, 직전 스냅숏과의 큰 차이 경고)을 더한다. 에이전트
  채널 TLS(§4 「에이전트↔API 채널이 클러스터 내부 평문 HTTP」)와 함께 본다. 지금 공유 토큰 보유 = 노드 root 수준(같은 Secret 을 읽는
  주체)이라 데이터 직접 접근과 같은 신뢰 경계다.
- 🔧 **결과 POST 의 API 메모리(슬라이스 1 열린 항목)** — `POST /api/agent/k8s-sync/runs/{id}` 를 API 프로세스에서 JSON 파싱·정규화하므로
  상한(`DMS_K8S_SYNC_MAX_BYTES` 32MiB) 가까운 본문이면 API 메모리를 수백 MB 쓸 수 있다. 일반 인벤토리는 훨씬 작다 — 큰 사이트는 상한을
  낮추거나 스트리밍 파서로.
- 🔧 **e2e: 계정 화면 `?create=`·SubmitJob `?pv=` 소비 흐름** — 수기 게이트 e2e 에 아직 없다.

### dms-ssc 이식 후속 — mpifileutils 7a3189c · d190~d193 (2026-10-28)

CHANGELOG 「dms-ssc 이식」. 포크(`chahwansong/mpifileutils`) 항목은 dms-ssc BACKLOG 와 같다 — 포크는 두 라인 공용이라 한쪽에서 고치면 둘 다
받는다(잡 이미지 `Dockerfile.mpifileutils` 의 `MPIFILEUTILS_REF` 를 올려 재빌드).

- 🔧 **dsync 미리보기는 변경 계획을 내지 않는다**(nsync 쪽은 d192 로 해결 — 확인 창·배치 확인이 nsync 종류별 변경 예정(삭제 포함)을
  보인다). dsync `--dryrun` 은 소스·목적지 walk 수만 찍어 DMS 는 「소스 전체 N개(변경 수는 실행 후 확정)」로 정직하게 표시만 한다 — 삭제
  수·거부될 타입 교체를 미리 못 본다. 처방(포크): `dsync_strmap_compare` 의 결정 지점(only-src·타입·크기·심링크 차이·메타 갱신, `--delete`
  의 only-dst)에서 dry-run 에도 세어 `Dry run plan: copy=… remove=… type-change=… meta-update=…` 한 줄 → 러너 파서·포탈은 nsync 와 같은
  모양으로 읽는다(포크 하네스·잡 이미지 재빌드).
- 🔧 **dsync 가 복사에 실패한 파일을 목적지에 빈 파일로 남긴다**(dms-ssc d192 실증 D2: 0 B·소스 모드). mknod 로 먼저 만들고 내용을
  복사하는 구조라 실패한 파일의 이름이 목적지에 보인다(다시 실행하면 크기가 달라 다시 복사). 포탈 「실패 항목」이 그 사실을 말한다(d193).
  root 만 도는 dms-k8s 에선 권한 실패가 드물지만 공간·읽기 오류로 같은 모양이 난다. 처방(포크): 복사 단계 뒤 실패한 목적지 이름을 모아
  지우고 메타데이터 단계에서 건너뛴다(nsync 는 이미 「실패한 전송이 만든·자른 파일만 지운다」).
- 📝 **dsync·drm 의 원시 경로로 사유 판정을 흔들 수 있다**(d192 리뷰, 낮음). 오류 줄에 경로를 이스케이프 없이 찍어, 줄바꿈이 든 이름이
  가짜 `… (errno=28 …)` 줄을 더하면 그 잡의 사유가 `destination_full`·`destination_read_only` 로 바뀔 수 있다(수 판정은 최종 블록·마지막
  줄만, abort 는 stderr 만 믿어 막혔다). 자기 잡의 표시만 바뀐다 — 처방(포크): dsync·drm 오류 줄 경로 이스케이프(nsync 처럼).
- 📝 **dsync walk 오류는 삭제가 켜졌을 때만 보인다**. `Errors detected walking source path` 는 `--delete` 일 때만 찍혀 삭제 꺼진 dsync 는
  소스 일부를 못 읽어도 summary 에 `scan_incomplete` 가 없다(rc 도 0). 처방(포크): walk 오류 수를 늘 요약 줄로.
- 🔧 **포크(`nsync-fixes-d189` 7a3189c = 포크 main)의 그 밖의 남은 것** — 루트 고정(B)(§4 「경로 봉쇄의 남은 TOCTOU 창」 — 봉쇄의
  마지막 몇 초 창) · nsync P3: 배치 여럿에서 ENOSPC 정지 뒤 배치의 삭제는 돌지 않음(다음 실행이 이어 감) · ENOSPC 뒤에도 진행 중이던 전송은
  끝까지 읽혀 버려짐.
- 📝 **quiet 실행은 결과 수·실패 항목·도구 사유가 없다**(의도 — 세 도구 모두 `-q` 가 로그를 NONE 으로). 포탈 도움말·결과 화면이 그렇게
  말하고 summary 는 `quiet: true` + 수 null, 실패는 MPI 중단(stderr — `tool_aborted`)이 아니면 일반 `execution_failed`. 결과가 필요하면 quiet 를 끈다.
- 📝 **이식 전에 계획된 direct + 맞지 않는 bufsize 잡은 그대로 돈다**(낮음). 제출·배치 경로의 검증(422 `direct_bufsize_misaligned`)만
  넣었고 stepper 관문은 없다 — 그런 잡이 중단되면 이제 `tool_aborted` 로 보인다.
- 📝 **nsync `--max-errors` 는 DMS 가 넘기지 않는다** — 포크 기본 65,000 이 늘 적용된다(포탈 `SYNC_MAX_ERRORS_NOTE`). 더 일찍 멈추게 하려면
  옵션 allowlist(`domain._OPTION_SPECS`)·`tool_argv`·포탈 칸을 함께 더해야 한다(지금은 요청 없음).
- 📝 **배치 상세는 자식의 실행 결과 상세를 보이지 않는다** — 배치 항목은 `files_count` 만 싣고 result_summary 를 싣지 않아, 「실패 항목」·
  dsync 0 B 잔여 안내(d193)는 자식 요청 상세(StageResults)에서만 보인다. 필요해지면 항목 응답에 summary 의 실패 요약만 싣는 후속.

### 사용량 분석 필터 후속 — 2026-10-29

CHANGELOG 「사용량 분석 필터」. 이번 묶음이 **의도적으로 하지 않은 것**과 그 근거 — 필요해지면 여기서 꺼낸다.

- 🧹 **옛 `scan-targets`·`export` 제거** — 새 포탈은 `/targets`·`/targets/export` 를 쓰고 옛 둘은 롤링 배포 중 열린 옛 탭 호환으로 동결했다
  (본문 무변경 — `tests/test_usage_equivalence.py` 가 그 증거). 다음 정리 때 라우트·참조 그물을 함께 지운다(포탈이 옛 엔드포인트를 더는
  부르지 않는 것을 확인한 뒤).
- 🔧 **PV 축 필터의 SQL 푸시다운** — PV 축(클러스터·namespace·PV 이름·StorageClass·권한 사용자·구분)은 정렬과 무관한 최근 스캔 순 머리
  창(`_WINDOW_LIMIT` 1만 + 1)을 파이썬이 거른다 — 대상이 1만을 넘는 스토리지에서만 「최근 스캔된 1만 개 안」으로 제한되고 matched_count 가
  null(모름)이 된다(화면·CSV·합계·필터 선택지가 그렇게 말한다 — 「오래된 순」으로 정렬해도 판정 범위는 최근 1만 개다). 경로·PV 이름 검색(q,
  같은 날)의 **PV 이름에만 맞는** 대상도 같은 창에 묶인다(모든 토큰이 경로에 든 대상은 창 밖까지 SQL 로 더한다 — `routes_usage._with_path_hits`).
  그런 스토리지가 실제로 생기면 PV 루트 접두 술어(`target = :root OR target LIKE :root || '/%'` 묶음 — 검색은 토큰에 맞는 PV 이름의 루트들)를
  SQL 로 내려 창 제약을 없애거나, 계획 시점 귀속 칸(data_jobs·requests — 이중 경로·백필·요청 삭제 목록)을 다시 검토한다(이번엔 A 안 기각:
  planner·`recheck_request` 반환형 변경·백필이 옛 직접 경로 scan 을 「모름」으로 굳힌다).
- 💡 **볼륨(PV 객체 이름) 필터** — 표시(title)·CSV 에만 싣는다. 요청이 오면 `PvFilter` 에 축 하나·facets 목록 하나를 더한다.
- 💡 **스토리지를 가로지르는 사용자 합계**(「alice 의 전 스토리지 사용량」 한 숫자) — 만들지 않았다. 스토리지끼리 더할 수 없는 이유(겹친
  대상·한 파일시스템을 여러 스토리지가 공유)가 스토리지 합계와 같다. UsageOverview·「스토리지별 비교」도 무필터 그대로.
- 💡 **k8s 화면에서 사용량으로 딥링크** — 접근 화면(사용자별)·PV 탐색 행 → 사용량 탭 필터. URL 키(cluster·namespace·pvc·storage_class·
  user·scope·requester)는 PV 탐색과 같게 미리 맞춰 두었다.
- 📝 **요청자 기준은 `requests.requester_id`** — 상세의 「요청자」는 잡 신원 `identity.username`(owner 지정 시 owner)이라 다를 수 있다.
  owner 기준 필터·상세 표기 변경은 요청이 오면.
- 📝 **PV 상위 디렉터리는 「PV 밖」** — namespace 디렉터리 같은 PV 루트의 상위 경로를 그 namespace 로 묶지 않는다. Released PV 의 claimRef
  귀속(PVC 를 지운 뒤 남은 PV)도 하지 않는다 — PV 주소로 scan 한 기록이 있으면 그 기록으로 산다.
- 📝 **권한 사용자 축에 면제 운영자는 없다** — 관리자·특권 운영자의 전체 접근은 바인딩이 아니라 나오지 않는다(포탈 도움말이 말한다).
  계정이 없는 dms_user(`user.yo.hong.example.com` 같은 파생 이름)도 거르지 않는다.
- 🐞 **기존 결함(이번에 고치지 않음)**: 사용량 `path`·`q` 검색은 `lower()` 가 없어 PG 는 대소문자를 구분하고 sqlite 는 ASCII 만 무시한다
  (새 `/targets`·`/targets/export` 도 같은 SQL 이라 같다 — 고치면 옛 엔드포인트와 결과가 갈라진다), 그리고 옛 `scan-targets`·`export` 의
  NUL 이 든 `path`·`q`·`storage` 는 PG 에서 500 이다. 옛 둘은 고치면 `test_usage_equivalence` 의 참조와 갈라지므로 옛 엔드포인트 제거와
  같은 묶음에서. 새 엔드포인트(`/targets`·`/targets/export`·`/facets`·`storage-totals`)는 storage·path 의 NUL 을 422 로 막고, 새 필터 값은
  제어문자 전부를 422 로 막는다.

### 포탈 개선·배치 스케줄러 후속 — 2026-11-16

CHANGELOG 의 2026-11-16 d28 항목(사용량 전체 목록·최신 scan 차트 · 배치 대상 스토리지 열·큰 배치 상세 쪽 넘김·검색 · 배치 스케줄러).
지금 동작은 ARCHITECTURE §5 「batches」·「사용량 분석」·불변식 「배치를 가리키는 새 테이블은 배치 삭제 목록에도」, §7 「배치 재검토·확인
게이트」의 「스케줄 회차」.

- ~~**스케줄 회차가 쌓는 자식 요청의 정리 정책**~~ — 해소(2026-11-16 d29, 사용자 결정 「최근 5회차만 남기고 나머지 정리」 — CHANGELOG
  「스케줄 회차 최근 5회차 보존·배치 단위 삭제 상한 5000」, 같은 날 기본 5 → 10 — CHANGELOG 「스케줄 배치 회차 보존 기본 5 → 10」): 스케줄이
  있는 배치는 최근 `DMS_BATCH_SCHEDULE_KEEP_ROUNDS`(기본 10)회차만 남고 컨트롤러 `batch-round-prune` 루프가 오래된 회차의 자식을 요청
  삭제와 같은 몸통으로 지운다(회차 경계 `batch_rounds`, 감사 청크 요약 `('batch','prune_rounds')`). 배치 단위 삭제 상한도 2000 → 5000(당시 —
  2026-11-19 부터 설정 `DMS_BATCH_DELETE_MAX_CHILDREN` 기본 50000).
- 🔧 **사람의 `:rescan` 감사 행**(축소, 2026-11-16) — 사람의 「전체 재실행」도 회차 경계 `batch_rounds` 에 via rescan·actor(세션 사용자)·시각·
  되돌린 항목 수가 남는다(배치당 최근 200회차). `audit_log` 행은 여전히 없다(스케줄 회차만 `('batch','scheduled_rescan')`) — 배치를 지우면
  경계 행도 함께 지워지므로 영구 감사가 필요하면 `rescan_if_terminal` 트랜잭션에 한 행.
- 🔧 **회차 보존 정리의 자식별 감사**(2026-11-16 결정의 대가) — 정리 경로는 자식별 32 KiB 스냅숏(`('request','delete')`)을 남기지 않고
  청크 요약 1행에 자식별 최소 기록(id·상태·생성 시각·owner_username·dest_owner {rule, owner}·잡 id·상태·tool — d30 부터; d29 의 run_as_root 칸은
  배치 자식 payload 에 그 키가 없어 늘 null 이었다)만 싣는다 — 정리된 회차 자식의 결과 메시지·전이 전문은
  사라진다(배치 정의·회차 발사·확인 감사는 그대로). 필요해지면 설정으로 자식별 감사를 켜는 옵션.
- 🔧 **미등록 스토리지 정리 포탈 연속 회차의 readiness**(기존 성질) — 회차 상한은 배치 10·단건 200·배치 자식 합 100000
  (`request_purges.within_call_budget`, 2026-11-19)이다. 2026-11-19 부터 배치는 작업 삭제와 같은 진입점 `delete_batch_auto` 로 가고, API 한
  트랜잭션(동기) 몫은 한 회차에 2 × `DMS_BATCH_DELETE_SYNC_MAX_CHILDREN`(기본 1000 자식)까지다 — 나머지는 묘비 + 컨트롤러 청크로 넘어간다. 배치
  트랜잭션 사이에는 2ms 양보(`request_purges.RLOCK_YIELD_SECONDS`)가 있다. 그래도 포탈(`useUsage` 정리 루프)은 회차 사이에 쉬지 않고 다시
  부르므로, 회차마다 RLock 몫(동기 ≤ 1000 자식 + 배치마다 A단계 세기·B단계 묘비 + 단건 ≤ 200)을 연달아 쥔다. 처방은 그대로 회차 사이 쉼
  또는 서버 쪽 간격.
- 🔧 **회차 보존 정리 처리량** — 정리 행은 아웃박스 처리량(틱당 ~10행 ≈ 40행/분)에 묶이고 루프는 자기 대기 행 100(`BACKLOG_MAX`)에서
  쉰다 — 항목 수천 개를 시간 단위로 도는 스케줄은 정리가 따라가지 못해 자식이 쌓일 수 있다(스케줄 화면의 80% 경고에 보인다).
- 🔧 **배포 전에 이미 상한을 넘은 스케줄 배치** — 옛 회차(기록 이전)는 기록된 회차가 K개가 된 뒤에야 정리되므로(보수적 선택 — 배포 직후
  대량 삭제 없음) 일일 스케줄이면 ~10일(기본 보존 10 — 5 시절엔 ~5일) 동안 배치 단위로 지울 수 없다.
- 🔧 **배치 목록에 스케줄 요약 표시** — 목록 행(`GET /api/admin/batches`)이 이미 `schedule` 요약을 싣지만 `BatchesList` 는 그리지 않는다
  (상세 머리 배지·배치 스케줄러 화면에서만 보인다).
- 🔧 **배치 항목 일괄 삭제 컬렉션 엔드포인트** — 상세의 「선택 삭제」는 지금 쪽(≤ 100개) 항목마다 병렬 `DELETE …/items/{seq}` 다(쪽을 넘기거나
  결과가 바뀌면 보이지 않는 선택을 정리한다 — 확인한 집합만 지운다). 한 번에 수백 개를 지우려면 한 트랜잭션·한 감사 행의 컬렉션
  엔드포인트(`POST …/items:delete` 꼴 — `…/items/{seq}` 와 세그먼트 수가 달라 라우트 충돌 없음).
- 🧹 **배치 상세 view 없음(옛 탭 호환)의 정리 시점** — `GET /api/admin/batches/{id}` 의 view 없음은 롤링 배포 중 열린 옛 탭 때문에 전 항목 조인
  그대로다(항목 없는 머리를 주면 옛 탭의 확인 창이 「0 / 전체 0」으로 열려 root 삭제를 맹목 승인한다 — `routes_batches.get_batch` docstring).
  새 포탈은 `summary`·`items`·`payloads`·`all` 만 쓴다. 옛 탭이 없다고 볼 수 있을 때(d28 다음 배포 이후 등) view 없음을 걷고, 같은 때 포탈의
  옛 파드 물러서기(`batchView` 의 전체 응답 파생 — 기존 `BatchDetail.test.tsx` fixture 가 그 경로를 시험한다)도 걷는다.
- 🔍 **API 응답 압축(GZip) 검토** — 앱에 압축 미들웨어가 없어 큰 JSON 이 비압축이다(합성 측정: 옛 배치 상세 전체 응답 N=5000 sync 3.06 MB —
  쪽 넘김 뒤 2.5초 폴링 한 번은 요약 + 한 쪽 ~62–71 KB). 입구 Traefik 의 압축은 사이트 몫이고 DMS 는 Traefik 을 바꾸지 않는다. 넣는다면
  `RequestBodyLimit`(가장 바깥 미들웨어)과의 순서, 스트리밍 내보내기(`StreamingResponse`)·결과 파일 다운로드, CPU 비용을 함께 본다.
- 🔧 **dms-ssc 역이식 후보**(이 라인의 세션은 dms-ssc 를 고치지 않는다 — dms-ssc 세션이 골라서 cherry-pick. 이름·모양은 구현할 때 dms-ssc
  WIP 와 맞췄고, 아래 목록은 push 된 dms-ssc d197/d198(origin/dms-ssc e8786abe)과 대조한 뒤 2026-11-19 origin/dms-ssc bd6f8613 까지 다시 확인했다 —
  `Pager` null 모드·`pageable_count`·`batch_item_search`·`disable_schedule`·`confirm_wait` 는 bd6f8613 에도 없다): 공용 `Pager` 의 null 모드(`pageCount: null` +
  `hasMore` — 쪽 수 모름), `/targets` 봉투의 `has_more`·`pageable_count`(`offset` 메아리는 dms-ssc 에도 있다), 배치 상세 `view=items` 의 `filters`
  메아리와 PV 이름 검색(`batch_item_search` — dms-ssc 는 경로 부분 문자열·대소문자 구분), 스케줄 삭제 = `require_session_admin`(dms-ssc 는 bd6f8613
  에서도 `require_admin`), 취소 + 스케줄 끄기를 `:cancel` 본문 `disable_schedule` 한 트랜잭션으로, 그 밖에 회차 예외 백오프(due `exclude`)·사전 판정의
  스토리지 꺼짐·denylist(스토리지 없음은 dms-ssc 8516568d 가 이미 넣었다)·`confirm_wait` 경고·PG 잠금 순서 스파이 테스트. (k8s 전용 사유 문구 —
  요청자 자격 상실 = 거부 — 는 dms-ssc 의 「비 root 강등」과 뜻이 달라 옮기지 않는다.) 2026-11-16 d29·2026-11-19 추가분은 dms-ssc 가 이식해
  후보에서 뺐다: 회차 경계(`batch_rounds`)·회차 보존 정리(`batch_round_prune`·`prune_round_children`)·스케줄 행 `child_count_limit`(dms-ssc
  4269c900 = d199), 배치 단위 삭제 상한 설정 키 50000·동기 문턱·묘비(`Deleting` + `batch_deletions`) + 컨트롤러 `batch-delete` 청크·
  `requests:delete-preview`·호출당 예산(`within_call_budget`)·`list_summaries` 스칼라 서브쿼리·목록 행의 상한 키·사유 `batch_delete_too_many_items`·
  `batch_deleting`·정리 아웃박스 `purge_group` 레인·포탈 `lib/deleteLimits.ts`(dms-ssc bd6f8613 — 2026-11-19).
- 🔧 **dms-ssc 에서 골라 올 것 — 스토리지 사용 중 가드**: dms-ssc 14cb6741(활성 **배치**도 「사용 중」 — 경로 변경·삭제 가드가 비종단 배치 항목이
  가리키는 스토리지도 보고, 스토리지 행 잠금과 배치 되살리기(회차 발사·`rescan_if_terminal`)의 잠금으로 직렬화)과 8516568d 의 스토리지 **생성**
  가드(진행 중 요청·배치가 가리키는 이름이면 409 `storage_in_use`). dms-k8s 의 `routes_storages` 경로 변경·삭제 가드는 활성 요청
  (`requests.active_referencing_storage`)만 보고 생성 가드는 없다. 스토리지 경로 바인딩은 **파괴적** 스케줄의 지문에만 있고 그것도 발사 순간의
  비교라, 비파괴 스케줄과 발사 뒤 아직 요청이 되지 않은 항목은 그사이 바뀐 경로에서 돌 수 있다(dms-ssc 14cb6741 커밋 설명과 같은 틈 — 코드
  읽기, **미검증**).
- 🧹 **vite 빌드 청크 경고** — `npm run build` 가 `index-*.js` 1,095 kB(gzip 356 kB)로 「chunk > 500 kB」 경고를 낸다(실패 아님 — d28 게이트에서
  확인, 그 전 빌드와는 비교하지 않았다). 후보: 무거운 화면(사용량 분석·배치 상세·차트)의 `React.lazy` 라우트 분할 또는 `manualChunks`. 번들
  airgap 규칙(외부 URL 0)은 그대로.

### 사내 SSO(OIDC) 후속 — 2026-11-18

CHANGELOG 「사내 AD SSO 로그인(OIDC Authorization Code + PKCE)」·「테스트베드 사내 AD SSO 환경 구축·실증 + 실증 결함 3건 수정」, deploy/README
§6-9. 지금 동작은 `api.oidc`·`api.routes_oidc` 모듈 docstring(설정은 사이트가 만드는 선택 Secret `dms-oidc` 에만 — CLAUDE.md). (테스트베드 CA 항목은
「테스트베드(dmsk8s) 후속」.)

- 💡 **로그아웃 때 IdP `end_session_endpoint`** — 지금 로그아웃(`routes_auth.logout`)은 DMS 세션만 끝낸다. IdP 세션이 남아 다시 SSO 를 누르면 같은
  AD 계정으로 비밀번호 없이 들어오므로(로그인 실패 뒤 다시 눌러도 같은 계정·같은 오류), 다른 AD 계정으로 바꾸려면 IdP 에서 따로 로그아웃해야 한다.
- 💡 **SSO 로그인 성공·실패를 보안 이벤트로** — `routes_oidc.oidc_callback` 의 실패는 `/login?sso_error=<사유>` 로 돌려보낼 뿐이고 보안 이벤트 화면
  (비밀번호 로그인 실패·경보)에는 남지 않는다.
- 💡 **계정 화면에 「SSO 로 만든 계정」 표시** — 자동 생성(`routes_oidc._ensure_account` — 감사 actor `sso`, 아무도 모르는 무작위 비밀번호)한 계정이
  계정 화면에서 구별되지 않는다.
- ⏸ **IdP `sub` 를 계정에 묶는 안** — 하지 않음: DB 칸이 필요하고 첫 연결을 먼저 한 쪽(선점)을 막지 못한다. 메일 ↔ 계정 결속은 「사용자가 IdP 에서
  자기 email 을 바꿀 수 없다」는 IdP 설정과 선택 키 `DMS_OIDC_REQUIRE_EMAIL_VERIFIED` 가 지킨다(deploy/README §6-9).
- 🔧 (선택) **S11 설정 재시작 판정이 Secret `dms-oidc` 를 보지 않는다** — 설치기 S11 은 dms-config·dms-secrets 의 resourceVersion(`install.sh`
  `config_rev`)만 비교하므로 `dms-oidc` 만 바꾼 실행은 dms-api 를 다시 띄우지 않는다. 지금은 런북 §6-9 의 손 `rollout restart deploy/dms-api` 가
  대신한다. 판정에 dms-oidc resourceVersion(dms-api 만 읽는다 — 없으면 빈 값)을 더할지 정한다.
- 🔧 (선택) **SSO 의 INFO 로그가 찍히지 않는다** — `dms` 로거에 로깅 설정이 없어(`cli.py` 의 `uvicorn.run` 은 uvicorn 로거만 설정) 루트 기본
  WARNING 아래 줄은 버려진다. 그래서 `routes_oidc` 의 계정 자동 생성(`sso: created account …`)·로그인 성공·IdP error 줄은 출력되지 않는다
  (2026-11-18 d33 은 운영자가 봐야 하는 email_verified 줄만 WARNING 으로 올렸고, 로그인 실패 `sso login failed:` 는 원래 WARNING). 자동 생성
  기록은 감사(actor `sso`)에 남는다. 필요하면 계정 생성 줄도 WARNING 으로 올리거나 `dms` 로거 레벨을 설정으로 연다.

### 포탈 이미지 빌드 메모리 — 2026-11-23

- ✅ **빌드 파드 Node 힙 여유** — 2026-11-23 해소: 빌드 파드 memory limit 1Gi → 2Gi + Dockerfile `NODE_OPTIONS=--max-old-space-size=1024`
  (CHANGELOG 「빌드 파드 메모리 상한 1Gi → 2Gi」). 힙 필요량은 `build:image` 약 400MB·테스트 포함 tsc 약 550MB 라 여유 약 2.5배. 다시 모자라면
  둘을 같이 올린다(`tests/test_portal_image_build.py` 가 힙 ≥ 768MB·상한 − 힙 ≥ 768MB 를 고정). 여유 측정:
  `NODE_OPTIONS=--max-old-space-size=400 npm run build:image`.

### 마운트 현황 후속 — 2026-11-23

CHANGELOG 「스토리지 > 마운트 현황(노드 × 스토리지 매트릭스) + 에이전트 공용 디렉터리 덮는 마운트 보고」. 지금 동작은 `mount_status`·
`mount_status_k8s`·`api.routes_mount_status` 모듈 docstring 과 `frontend/src/features/mounts/` 파일 머리 주석.

- ✅ **라이브 실증** — d38 에서 끝냈다(CHANGELOG 「스토리지 > 마운트 현황」 의 「배포·라이브 실증(d38)」 — 기대값 일치, 사고 유형 가짜 보고 탐지·정리).
- ✅ **API·컨트롤러 파드가 뜬 노드 꼬리표** — 2026-11-23 API 파드 base 점검 이식과 함께 끝냈다(`mount_status_pods`). 재료는 k8s 파드 목록이
  아니라 **DB**(api_base_checks 파드별 신선 행·control_state 의 컨트롤러 점검 노드 — 화면이 10초마다 묻는데 요청마다 k8s API 를 부르지
  않는다): 행 `roles` 의 `api_pod`·`controller_pod`(포탈 「dms-api」·「컨트롤러」), 최상위 `control_pods`(파드별 점검), 공용 디렉터리 칸이
  문제인 노드거나 파드의 경로 점검이 실패하면(권한 사유 제외 — 컨트롤러 쓰기 왕복 실패 포함) 꼬리표가 경고(그 조합이 사고 서명), 그런
  행은 「문제 있는 노드」에 든다(공용 열 한 칸), 표 밖 노드(prod 마스터 — 에이전트 없음)의 파드는 각주. 라이브 실증은 아직(testbed-k8s 다음 태그 — 롤링 때 새 파드가 시작 게이트에
  막힌 노드의 꼬리표가 보이는지).
- 🔍 **판정 구멍 둘 수정의 라이브 실증** — 2026-11-27(CHANGELOG 「마운트 현황 판정 구멍 둘」, dms-ssc d207 리뷰 이식) 아직: ① 끊긴 마운트 —
  테스트베드 한 노드에서 FUSE 마운트를 죽이거나(또는 그 노드의 다음 보고 전 가짜 보고 ingest — `is_mountpoint: true`·`exists: false`) 칸이
  「끊긴 마운트」(빨강)·「문제 n」·「마운트 문제」 타일·「문제 있는 스토리지」에 드는지, 스토리지 목록 배지(Degraded)는 종전과 같은지 ② 어디에도
  없는 스토리지 — 없는 mount_path 로 스토리지를 등록(다음 보고 뒤) 열 전체가 경고 아이콘 「없음」·모든 신선 노드에 「문제 1」 ③ 다른 노드엔
  붙은 스토리지의 없음(라이브 w1/cephfs-secondary 모양)은 그대로 회색·문제 0. 확인 뒤 등록한 시험 스토리지는 지운다.
- 📝 **dms-ssc 와 남은 차이(같은 날 이식 기준)** — 규칙(끊긴 마운트는 문제, 어디에도 없는 열의 없음은 문제, ro 는 정상 노드 옆에서만)은
  같고 모양이 다르다: ssc `routes_mounts` 는 칸·아티팩트 경로 칸마다 `problem` 플래그를 싣고 요약 수가 그 플래그만 세며(포탈은 플래그가 없는
  옛 서버에서만 다시 셈), 상태 이름이 missing/absent 다. k8s 는 행 `problem_count` 만 싣고 포탈이 열 규칙을 거울(`mountTerms.columnFacts`·
  `cellProblem`)로 다시 재며 상태 이름이 broken/absent 다(교차 픽스처 `mountProblemCases.json` 이 서버·포탈을 묶는다). ssc 는 아티팩트 경로
  칸에 덮는 스토리지 칸의 missing·absent 를 겹치지만(`covering_storage_missing:<이름>`), k8s 공용 디렉터리 칸은 에이전트의 덮는 마운트
  (mount_point·fstype — local·differs)로만 판정한다. 칸마다 problem 플래그를 실을지는 두 라인을 같이 정한다(응답 모양 — 공용 코어 계약).
- 💡 **에이전트가 끊긴 마운트를 직접 가리기** — 지금은 서버가 `is_mountpoint` 로 추론한다(Missing + missing_mount_path + is_mountpoint true).
  에이전트가 경로 stat 의 errno(ENOTCONN·ESTALE·EIO)를 따로 사유로 싣으면 죽은 FUSE·낡은 NFS 와 전파 끊김(부모 마운트가 덮음)을 구별해
  조치 문장을 나눌 수 있다(두 라인 공용 에이전트 — dms-agent 재빌드 필요).
- 💡 **`/api/admin/artifact-base` 의 checks.nodes 에 mount_point·fstype** — 아티팩트 경로 화면의 「3홉 검증」 노드 표도 로컬 디렉터리를 구별하게.
- 💡 **공유 마운트 source(서버:경로) 비교** — 지금 「다른 노드와 다름」은 (마운트 지점, fstype) 과반만 본다. 같은 nfs4 라도 다른 서버를 붙인
  노드는 못 가린다(에이전트가 mountinfo 의 source 를 더 실어야 한다).
- 💡 **로컬 파티션 위 디렉터리** — 덮는 마운트가 '/' 가 아니라 /data 같은 로컬 파티션이면 local 이 아니라 과반과의 비교(differs)로만 드러난다
  (과반이 없는 2노드 구성에선 둘 다 「정상」).
- ⏸ **PV 단위 행·열** — 하지 않음: 에이전트가 PV 별 점검을 하지 않아 데이터가 없고, 잡 파드는 스토리지 mount_path 를 hostPath 로 붙여 칸 하나가
  그 스토리지의 PV 접근 가능성을 말한다.
- 💡 (선택) **칸 안 용량 막대** — 지금은 보조 줄 「사용 41%」와 툴팁·시트의 바이트뿐.
- ~~(선택) **`agent_nodes` 퇴역 행 정리**~~ — 해소(2026-11-25, CHANGELOG 「stale 노드 기록 정리(기록 삭제·자동 정리·새 이름 힌트)」 — 남은 일은
  아래 「노드 기록 정리 후속」).
- 🔧 (선택) **노드 화면 마운트 사유 원문** — 노드 상세의 「마운트」 표는 아직 원문 사유 코드(not_a_mountpoint 등)를 그대로 찍는다 — `mountTerms`
  의 문구를 쓸지(공용 코어 import 방향 주의).

### 노드 기록 정리 후속 — 2026-11-25

CHANGELOG 「stale 노드 기록 정리(기록 삭제·자동 정리·새 이름 힌트)」. 지금 동작은 `node_forget` 모듈 docstring·`repositories.agents.forget_node`
docstring, 운영 절차는 deploy/README §14-3 「노드 이름이 바뀐 뒤 옛 행 정리」.

- 🔍 **라이브 실증** — 아직(testbed-k8s 다음 태그 d44+): ① 「리포트 지연」 행 기록 삭제·409 두 갈래·공유 토큰 403, ② 보고 이력이 많은 노드의 삭제
  시간(보존 30일이면 노드당 약 4.3만 행을 한 트랜잭션 — API 단일 커넥션이 그동안 막힌다, readiness 문턱 안이어야 한다), ③ 자동 정리를 켰을 때
  ClusterRoleBinding `dms-controller-nodes-readonly` 적용(S11) 전후의 `agent_node_forget_skipped`(k8s_list_failed → 사라짐), ④ 새 에이전트의
  `machine_key` 힌트(테스트베드 dmsk8s2 = 호스트의 k3s 컨테이너 노드라 machine-id 가 다른 노드와 같거나 없을 수 있다 — 같으면 정확히 1개 규칙이 힌트를
  내지 않는다).
- 📝 **이미 멈춘 옛 이름에는 「새 이름」 힌트가 없다** — 힌트 재료(`machine_key`)는 이 기능을 담은 dms-agent 롤아웃 뒤의 보고부터 생긴다. OA 사이트의
  h-aii-dms26001 처럼 그 전에 멈춘 행은 버튼으로만 정리한다.
- 📝 **호스트 이름만 바꾼 경우 자동 정리는 돌지 않을 수 있다** — 옛 Node 객체가 NotReady 로 남아 있으면(베어메탈·RKE2 는 아무도 지우지 않는다)
  「k8s 에 그 Node 가 더는 없을 때만」 규칙에 걸려 지우지 않는다. 클러스터 운영자가 옛 Node 를 지우거나 버튼으로 정리한다(deploy/README §14-3 ⑤).
  NotReady 를 「없음」으로 읽는 선택지는 만들지 않았다(재부팅·네트워크 단절 중인 살아 있는 노드와 구분할 수 없다).
- 💡 **노드 화면 「에이전트 없음」 행** — k8s 에 Node 는 있는데 에이전트 보고가 없는 노드(에이전트 파드가 못 뜬 인프라 문제 — OA 의 Cilium 장애
  s-dai-dms-26001~26004)를 노드 화면에 행으로. 마운트 현황은 이미 각주(`mount_status.build` 의 cluster.agentless_nodes)로 보인다. api 의 nodes 읽기 권한으로 된다.
- 💡 **기록 삭제 창의 「k8s 에 이 Node 있음/없음」** — 사람이 지울 때도 클러스터에서 정말 빠졌는지를 창에서 보이게(마운트 현황의 「클러스터에 없음」
  꼬리표와 같은 재료).
- 🔧 **컨트롤 상태 빌드 노드 선택지의 리포트 지연 표시** — `ControlStatePage` 의 빌드 노드 select 는 리포트 지연 노드도 라벨 없이 선택지로 보인다
  (k8s 연동의 호출 노드 선택지는 「보고 지연」 라벨이 있다). 고르면 빌드 제출이 `build_node_report_stale` 로 거절되니 결함은 아니다.
- 💡 **마운트 현황 열의 관리 디렉토리** — 노드 상세 「마운트」 표에는 2026-11-25 에 넣었다(프런트 조인). 마운트 현황(`mount_status.build`)은
  dms-ssc 와 공유하는 공용 코어라 그 응답의 열 모양을 바꾸지 않았다 — 요청이 오면 두 라인을 같이.
- 💡 **dms-ssc 이식** — 삭제 API·버튼·힌트는 라인 공통 성격이다(옛 이름 문제는 LDAP 과 무관). 자동 정리는 ssc 의 RBAC 매니페스트에 같은 바인딩이
  필요하다.

### sync 진행률 후속 — 2026-11-26

CHANGELOG 「sync 진행률 — 작업 상세 진행 중 실행·미리보기 행의 dsync·nsync 진행 칸 + mpirun 출력 스트리밍·출력 상한」. 지금 동작은
`dms_job_runner.stream`·`dms_job_runner.progress`·`dms.job_progress` 모듈 docstring, `frontend/src/features/jobs/progressModel.ts` 머리 주석, 계약
`frontend/src/lib/progressContract.json`, ARCHITECTURE §6 「러너 출력은 스트리밍·상한」·§7 「sync 진행률은 표시 전용」.

- 🔍 **라이브 실증의 남은 몫** — d49 실증(2026-11-27)에서 핵심은 통과했다: dsync 1·2노드 실행·미리보기·`batch_files` 배치 되풀이(전체 항목 막대)·복사 중
  취소, nsync 배치 11개·2개·1개(배치 크기를 줄여서만 — 사용자 지시 「검증할때만 배치사이즈를 줄여서」), /progress 5초·숨김 탭 멈춤·no-store·금지
  키 0, 수치가 stdout.log 진행 줄과 일치, 잡 목록·배치 화면 /progress 0건. 남은 것: (a) 실증 후속 넷(2026-11-27 — 종단 전이 뒤 아티팩트 목록
  지연 재조회 `useArtifacts.ARTIFACTS_SETTLE_MS`, 헛걸음 연속 3회부터 느린 폴링 `useArtifacts.PROGRESS_SLOW_AFTER_MISSES`, nsync 배치 여럿의 늘
  붙는 캡션 `progressModel.CAPTION`(스캔·배치 단계만), 배치 rm ④ 동의 체크의 `data-wizard-nudge`)을 다음 태그에서 다시 본다 — 복사 중 취소 직후 칩이 새로고침
  없이 맞춰지는지, 2~10초 미리보기에 수치가 뜨는지, 캡션이 스크린숏에 찍히는지, 막힌 「다음」이 1440×900·390×844 에서 동의 체크로 가는지.
  (b) 실증에서 밟지 않은 경로 — 파일 생성 %(생성이 늘 10초 미만이었다), 「워커 준비」·「소스 탐색」·「복사 준비」·「스캔·배치 준비」 문구,
  noneYet 중립 문구, 90초 정체 경고, 연속 3회 뒤 15초 간격 직접 측정, 실제 탭 숨김(헤드리스는 visibilityState 흉내였다). nsync·2노드 dsync 는
  테스트베드 CPU 때문에(아래 §4 「노드 CPU 상한은 다른 파드의 requests 를 보지 않는다」의 2026-11-26 사례) 실증 창 동안 되돌림 입구
  ingress-nginx 를 0 으로 줄여야 선다 — 그동안 ingress-nginx-admission 웹훅(failurePolicy Fail)이 클러스터의 **모든** Ingress 쓰기를 거부하므로
  설치기 S8·S11·손 apply 는 창 밖에서 하고, 끝나면 replicas 를 되돌린다.
  (c) d50 실증 후속 셋(2026-11-27): dsync 끝 스냅숏의 탐색 수 사실 줄(`walk` — 미리보기 끝에 「소스 탐색 N 항목」이 뜨는지), 일시 available:false 에
  스냅숏 유지(`progressModel.nextHeld`), 미리보기 진행 중 「미리보기 요약이 저장되지 않았습니다」 숨김 — 다음 태그에서 다시 본다.
- 📝 **실행 중 응답 하나만 available:false 가 될 수 있다**(2026-11-27 d50 실증 F3 — 화면은 고쳤고 API 는 그대로): copy 85% 중 18:16:44.846Z 응답
  하나만 200 `available:false` 였다. 앞뒤 응답의 나이(18:16:39 age 5 → mtime 약 :34, 18:16:49 age 5 → mtime 약 :44)로 보면 러너의 rename
  (`.progress.json.tmp~` → progress.json)과 같은 순간의 읽기다 — 공유 NFS 에서 옛 inode 의 캐시된 핸들로 열거나 읽다가 실패(ESTALE·순간 ENOENT)한
  것으로 추정(errno 는 남지 않아 확인 안 함). 포탈은 이제 같은 잡·단계에서 한 번 받은 스냅숏을 놓지 않아 보이지 않는다(헛걸음 수는 여전히 1 —
  연속 3회 문턱 아래). API 처방 후보: 실패 경로에서 열기·읽기를 원인 구분 없이 한 번 더(불변식 16 — errno 로 가르지 않고 base 판정은 그 뒤
  BaseProbe 그대로; 「아직 없음」 폴링마다 열기 1회가 더 든다) — 화면 문제가 없어져 하지 않았다.
- 🔧 **시간초과·취소의 SIGKILL 이면 넘침 끝 링을 잃는다** — 러너는 SIGTERM 처리기를 두지 않는다(PID 1 신호 의미 유지 — `stream` 모듈 docstring).
  그래서 넘침 모드의 끝 링(≤ 16 MiB — 최종 요약이 있는 꼬리)과 마지막 1초 이내 로그·쓰기 큐 잔량이 사라진다(앞 64 MiB 와 progress.json 은 남는다).
  처방 후보: SIGTERM 에서 끝 링만 붙이고 나가는 처리기(유예 시간 안 — PID 1 의미와의 충돌을 먼저 본다).
- 🔧 **summary.json·조기 종료 stderr.log 의 쓰기 통로** — 아직 `runner._write_text`(경로 기반 open("w"))다. 그중 봉쇄 두 경로(dsync·dscan 봉쇄, rm
  대상 봉쇄)의 stderr.log 는 chown -R 뒤라 요청자 소유 `<phase>` 안의 경로 쓰기다(ARCHITECTURE §7 「root 제어면」 4 — dms-k8s 는 모든 잡이 root
  라 위협은 아니다). `stream.ArtifactWriter` 의 dirfd + O_EXCL 통로로 옮기면 같은 절 3 의 소유자 검사를 `== 요청자` 로 좁히는 길도 열린다.
- 💡 **drm·dscan 진행률** — 같은 장치로 넓힐 수 있다(`progress` 정규식 표에 도구 추가 + 계약 `tools` + `progressModel` 제목). 지금은 sync(dsync·
  nsync)만 progress.json 을 쓴다.
- 💡 **실행 중 stdout.log 꼬리 보기** — stdout.log 는 이제 실행 중에도 이어 쓰지만 아티팩트 목록은 잡 상태 전이 때만(종단 전이 뒤엔 3·10·30초
  뒤 한 번씩 더 — `useArtifacts.ARTIFACTS_SETTLE_MS`) 다시 읽어 실행 중 칩 크기는 다음 전이까지 그대로다(진행은 진행 칸의 몫, 스테퍼는
  stdout.log 를 읽지 않는다). 실행 중 로그 칩 폴링은 요청이 오면 — 공유 base 가 NFS 면 다른 노드에서 최대 약 30초 늦게 보인다(아래 📝).
- 📝 **종단 아닌 전이 뒤의 칩도 NFS 가시 지연에 낡을 수 있다**(2026-11-27 d49 후속 리뷰 관찰 — 이번엔 고치지 않음): 지연 재조회
  (`useArtifacts.ARTIFACTS_SETTLE_MS`)는 종단 전이에서만 예약한다. 미리보기가 끝나 ConfirmPending 으로 가면 컨트롤러는 summary.json 을 읽고
  전이했어도 API 노드의 목록에는 아직 없을 수 있고(C1 에서 취소 직후 목록에 없던 execution summary.json 이 약 13초 뒤 조회에서 보였다),
  ConfirmPending 은 사람이 컨펌할 때까지 다음 전이가 없어 미리보기 단계 칩(summary.json·stderr.log 크기)이 새로고침 전까지 낡은 채 남을 수
  있다 — 미리보기 결과 카드는 DB(`preview_summary`)를 읽어 영향 없음. 고칠 때는 지연 재조회를 단계가 끝나는 모든 전이(refreshKey 변경 전부, 또는
  미리보기 → ConfirmPending)로 넓힐지를 비용(전이당 목록 요청 3회)과 함께 정한다.
- 💡 **진행 계약에 심장박동 값**(리뷰 nit 의 남은 몫) — 정체 문턱 `progressModel.STALE_AFTER_S`(90) ≥ 러너 `progress.HEARTBEAT_SECONDS`(30) × 3 은
  지금 `progressModel.test` 가 러너 소스를 읽어 고정한다. 계약에 `heartbeat_s` 를 더하면 백엔드 형식 시험(`test_job_progress_contract.py` 최상위
  키 집합)도 같이 바꿔야 해 미뤘다.
- 📝 **nsync 배치가 1개면 끝날 때까지 수치가 없다**(사용자 승인 「nsync 는 배치 단위로 찍는것 오케이야」 — 포크를 고치지 않는다): 기본
  `batch_files` 100만이면 100만 항목 미만 sync 는 배치 1개라 진행 칸이 단계와 안내(「nsync 는 배치가 끝날 때만 진행을 알립니다 — 이 작업은 배치가 1개라 끝날 때 한 번 표시됩니다」)만 보인다(막대 없음).
  배치 안 시간 간격 진행 줄(포크 수정)은 요청이 오면.
- 📝 **넘침 모드의 dsync 반영 항목 모름** — 항목별 오류·WARN 줄이 파서 상한(결합 메모리 64 MiB)을 넘은 dsync 실행은, 실패가 하나라도 있거나
  링이 줄을 밀어냈으면 summary `files`·`copied_items` 가 null 이라 통계 합계에서 그 잡 몫이 빠진다(결과 카드에 「도구 출력이 너무 커서 …」 한 줄).
  예전엔 같은 출력에서 launcher 메모리가 수 GB 까지 늘거나 OOM 으로 잡이 죽을 수 있었다 — 정직한 모름을 고른 대가. 비 BMP 이름이 섞이면 문턱이
  16 Mi 문자로 내려간다.
- 📝 **진행 신선도는 노드 시계에 기댄다** — `age_seconds` = API 서버 시각 − progress.json mtime(러너 노드가 찍은 시각 — NTP 가정, 음수는 0).
- 📝 **공유 base 가 NFS 면 실행 중 stdout.log 는 다른 노드에서 늦게 보인다**(2026-11-27 d49 실증 관찰 — 기능 판정 밖): 쓰는 쪽 launcher(w2)와
  읽는 쪽 API(w1)에서 목록의 크기·mtime 이 처음 약 4분은 약 31초, 그 뒤 10초 간격으로 한꺼번에 바뀌었고 다운로드한 꼬리가 도구 시각보다 최대
  약 30초 늦었다(목록 1336 B 직후 다운로드 1244 B 도 한 번). 「1초 주기로 이미 디스크에」(`stream` 설계)는 쓰는 노드 기준이다. rename 으로 쓰는
  progress.json 은 지연이 없었다(age ≤ 9초) — 진행 칸은 영향 없음. 원인은 NFS 클라이언트 속성 캐시로 추정(확인 안 함). 종단 뒤 칩 재조회의
  마지막 30초가 이 값에 맞춘 것이다.
- 📝 **복사 중 취소한 dsync 의 목적지 루트가 0700 root:root 로 남는다**(2026-11-27 d49 실증 C1 관찰 — 이전부터의 dsync 동작, 진행률과 무관):
  dsync 는 만든 디렉터리의 소유자·권한·시각을 마지막 속성 단계에서 맞추고(`--chown` 결과 소유 규칙 포함 — `execution_manifests._auto_chown`),
  취소는 그 전에 끊는다. 같은 목적지로 다시 sync 하면 속성 단계가 맞추는지는 아직 안 봤다.
- 📝 **dsync 복사 % 는 바이트 기준·배치 안**이다 — 크기가 섞이면 체감과 다르다(2026-11-26 실측: 큰 파일이 먼저면 20초에 73%, 남은 작은 파일
  10만 개에 21분). 막대에 기준 문구·HelpTip 을 두고 남은 시간은 숨긴다. 도구가 99.5% 부터 「100%」를 찍으므로 `step.done` 이 아니면 99 로 묶는다. d51 실증에서도 큰 파일 뒤 5초 만에 73%, 남은
  작은 파일 50,000개에 약 7분(평균 1.6 MiB/s)이라 막대가 멈춘 듯 보였다 — 항목 기준 수치(생성·속성 단계, 배치 모드 overall)를 앞에 둘지 검토 후보.
- 📝 **바꿀 것 없는 재동기화의 결과 카드가 「반영한 항목 —」·「복사한 항목 모름」**(2026-11-27 d51 실증 관찰 — 이전부터의 동작): dsync 는 복사가
  없으면 개수 줄을 내지 않아 summary `files`·`bytes` 가 null 이다. null≠0 규약에는 맞지만 사용자에게는 「0건 반영」이 「모름」으로 보인다 — 비교 통계
  블록(Items·차이 0)에서 0 을 확정할 수 있는지 파서 쪽 검토 후보.
- ⏸ **잡 목록·배치 화면에서 진행률 모아 보기** — 하지 않음(사용자 결정 2026-11-26 「포탈에서 각 작업 항목에 들어갔을때만」 — `JobsList.test`·
  `BatchDetail.test` 가 /progress 0건을 고정).

### 단계별 등록(위저드) 후속 — 2026-11-26

CHANGELOG 「단일·배치 등록 상단 스테퍼 — …」. 지금 동작은 `frontend/src/components/form/Wizard.tsx` 머리 주석(규칙 전문)과
`features/jobs/submitGates.ts`·`features/batches/batchGates.ts`.

- 🔍 **라이브 실증의 남은 몫** — d49 실증(2026-11-27, 관리자 세션)에서 실 Chrome 1440·390 으로 단일 sync·rm 과 배치 rm(자동 실행 켬) 단계 이동(가로
  넘침 0·콘솔 오류 0), rm 제출 빨강, ② 경로 칸 Enter(잘못된 경로면 머묾·유효하면 다음 단계·제출 없음), 실 dsync 위저드 등록을 확인했다. 실증
  후속(2026-11-27): 배치 rm ④ 에서 동의 전 막힌 「다음」이 화면 밖 동의 체크로 포커스·스크롤하지 않던 것을 `data-wizard-nudge` 로 고쳤다(e2e
  03 의 `layoutBatch` 가 1280·375 에서 포커스·바 가림을 잰다) — 다음 태그에서 1440×900·390×844 로 다시 본다. 남은 것: 1280·768 실 Chrome, 일반
  사용자·운영자 역할 × PV/직접 경로, 배치 scan/sync·「스토리지를 CSV 로 정하기」·PV 시트.
- 📝 **막힌 「다음」의 프로그램 포커스에는 포커스 링이 안 보인다**(2026-11-27 d50 실증 관찰 — 고치지 않음): `data-wizard-nudge` 가 동의 체크로
  옮기는 포커스는 마우스·터치로 「다음」을 누른 뒤의 스크립트 포커스라 브라우저가 `:focus-visible` 을 켜지 않는다(키보드로 누르면 켜진다).
  시각 단서는 스크롤과 막힌 이유 줄이다. 링을 늘 보이려면 넛지 대상에 따로 강조(잠깐의 테두리 등)를 둘지 정한다.
- ~~터치 연속 탭의 더블클릭 가드~~ — 해소(2026-11-27 d49 실증: 390 폭 Chrome 터치 에뮬레이션의 `touchscreen.tap` 두 번·`locator.tap` 두 번 모두
  둘째 탭이 detail 2 로 제출 노드에 떨어져 걸러졌다 — 제출 요청 0. 실기기 Safari 는 안 봤다).
- 💡 **위저드 이탈 경고(beforeunload)** — 입력이 있는 채 사이드바 링크·새로고침으로 떠나면 값이 사라진다(새로고침하면 `?step=` 이 첫 막힘 단계로
  맞춰진다 — 빈 폼으로 검토를 보이지 않는다).
- 💡 **스토리지가 많을 때 배치 ② 카드 묶음** — 목업(스토리지 6개)에서는 한 화면이다. 사이트 스토리지가 많아지면 묶음 접기·검색을 더한다.
- 📝 **브라우저 뒤로 가기의 뜻이 「이전 단계」다** — 제출 뒤 상세에서 뒤로 가면 빈 폼의 옛 단계 항목을 지나야 떠난다(히스토리를 가지칠 수단이
  없어 제출 성공 이동은 push 그대로 — 사이드바 링크로 떠난다).

### ③ 공용 디렉터리(SHARED_DIR) 후속 — 2026-11-03

CHANGELOG 「③ 공용 디렉터리」, 설계 v6(`docs/history/specs/2026-11-03-dms-k8s-prod-design.md`) 9절 「③ 공유 FS 세부」·10절 T3,
deploy/README §2d. (테스트베드 옛 base 정리는 「테스트베드(dmsk8s) 후속」.)

- 📝 **등록 가드(`storage_overlaps_dms_state`)의 한계**
  - 문자열 비교다(스토리지 경로는 노드 경로라 api 파드에서 realpath 로 풀 수 없다) — 심링크로 우회한 등록은 못 막는다.
  - 가드 이전에 등록된 겹치는 행은 경로를 바꿀 때만 걸리고, 그 전에는 「아티팩트 경로」 화면의 경고만 뜬다(차단하지 않는다 — 진행 중
    잡의 비상 비활성화를 막지 않으려고).
- 📝 **S0 는 실행한 노드 하나의 마운트만 본다** — 나머지 노드는 S7 몫이다. 어긋난 기존 디렉터리는 고치지 않으므로(공용 경로라
  chmod·chown 이 전 노드에 번진다) 운영자가 확인해 손으로 고친다.
- 📝 **프로브 정리 실패는 INFO(판정 밖)** — stderr 와 결과에 지울 경로와 `rm -rf` 명령을 남긴다(남 소유 0700 `.probe-<nonce>` 는
  lchown·fchmod 로 되찾은 뒤 지운다 — 2026-11-03 리뷰).
- 📝 **S7 파드 이미지 pull** — 마스터에 레지스트리 신뢰가 없으면 cp 노드가 이미지 pull 실패로 「모름」(exit 4)이 된다(④·S0-4 레지스트리
  신뢰 drop-in 이 전제). 그 전에는 마스터에 미리 pull 해 둔다.

### ④ 클러스터 내 레지스트리 후속 — 2026-11-03

CHANGELOG 「④ 클러스터 내 레지스트리」, 설계 v6 7·8-1(S0-4·S0-5·S-1·S4~S6)·10 T4, deploy/README §2e.

- 📝 **익명 쓰기 레지스트리**(설계 7-2·7-4 수용) — 클러스터 안 모든 파드가 push·delete 가능 — 클러스터를 나눠 쓰면 남의 파드가 dms 태그를
  덮어쓸 수 있다(IfNotPresent 캐시 노드는 무영향, 새 노드·재스케줄이 영향). 완화 후보: CiliumNetworkPolicy(fromEntities host·remote-node +
  ns dms 파드)로 ingress 제한 — 노드 netns pull·인터넷 노드 push 를 막지 않는지 테스트베드 검증 뒤. 포탈은 dms 3종 태그만 다룬다.
- 🔧 **guard-images.sh UX**(`deploy/overlays/guard-images.sh`) — dms-registry 교체 거부에도 일반 줄 「오버레이 태그(newTag / values.env
  DMS_TAG)를 라이브에 맞춰라」가 먼저 나온다(레지스트리는 `deployment.yaml`·`images.lock` 이 원천), 첫 설치(라이브 없음)의 「변경 없음」
  문구, init 컨테이너 이미지는 비교하지 않는다(렌더 이미지 추출이 첫 컨테이너만 뽑는다).
- 📝 **GC 로 블롭이 실제 지워지는 경로는 T4 에서 0건**(별칭이 블롭 공유) — 다음 GC 때 확인(deploy/README §2e-5).
- 📝 **레지스트리 1개(#46)** — HA(2개 이상)는 G3(S7 ②)가 PASS 인 사이트에서만 다시 본다(설계 7-1 — NFS 부정 캐시로 `blob unknown`).
  1개라 레지스트리가 있는 마스터 급사 때 약 90~110초 공백(설계 7-6 표 — 테스트베드 실측).
- 📝 **태그 DELETE(Distribution v3 의 태그 단위 삭제)는 하지 않았다** — 포탈은 지금처럼 digest 로 매니페스트를 지운다(설계 7-3, DMS 코드는
  바꾸지 않는다). 마스터 사전 pull 은 ⑦ S11 이 한다(`pull-probe.py --image <dms> --nodes <마스터>`).
- 📝 **REGISTRY_PORT ≠ 5000** — Service 포트만 바뀌고 컨테이너는 :5000 그대로다(테스트가 고정). 노드 신뢰·고정 패턴은 `<IP>:<PORT>`.

### ⑤ DB(CNPG) 후속 — 2026-11-03

CHANGELOG 「⑤ DB(CNPG)」, 설계 v6 6·8-1(S2·S9·S10·S12)·8-2·10 T6/T6'/T7, deploy/README §2f.

- 📝 **primary 노드의 스토리지만 멈추면 자동 장애조치가 막힌다**(T6' 4 — 설명된 편차): CNPG 는 57초에 「initiating a failover」였지만 옛 primary 가
  멈추기를 기다려 정지 동안 승격하지 않았다(스토리지가 돌아오면 쌓인 커밋 flush → 새 primary +8초). 길어지면 사람이 그 노드를 전원 차단으로 확정해
  노드 사망 경로로 넘긴다(deploy/README §2f-7) — **그 「정지 중 전원 차단」 경로는 시험하지 않았다**(전원 차단 뒤 쌓였던 커밋·pg_rewind 양상 미확인).
  경보 후보: Cluster 가 failover 시작 뒤 N초 넘게 새 primary 없음.
- 📝 **CNPG 오퍼레이터 두 레플리카가 노드 장애 뒤 한 노드에 모일 수 있다**(preferred anti-affinity 는 스케줄 때만 보고 다시 펼치지 않는다 —
  테스트베드 T6' 뒤 둘 다 한 워커). 애드온 패치에 `topologySpreadConstraints`(`kubernetes.io/hostname`, maxSkew 1)를 둘지 검토 — 이것도 스케줄
  때만이라 `DoNotSchedule` 면 노드가 돌아올 때까지 둘째가 Pending(그동안 1개), `ScheduleAnyway` 면 지금과 같다. 리더 선출이라 한 노드에 모여도
  동작은 하고, 그 노드가 죽으면 웹훅(9443)·리컨사일이 재스케줄까지 빈다.
- 📝 **`smartShutdownTimeout` 의 「switchover 없는 종료」 경로는 시험하지 않았다**(T6' 2) — drain 은 CNPG switchover 를 거쳐 0 대 180 이 같았다. 180 이
  의미 있는 경우(primary 파드가 switchover 없이 종료 — 예: 노드 셧다운·kubelet 축출)는 미실증. 렌더 값은 0 그대로.
- 📝 **앱 PG 세션의 `application_name` 이 비어 있다**(T7 — `pg_stat_activity` 에서 api·controller 를 주소로만 가른다) — 설계 6-3 URL 에
  `application_name=dms` 추가 검토(관측 편의). 넣으면 `pg-secrets.py` 의 URL 조립·`check` 필수 파라미터 규칙과 라이브 `DMS_DATABASE_URL`(`set-url`)을
  함께 바꾼다.
- 📝 **설계 ⑤ 의 선택 항목은 하지 않았다** — dms-controller 2개(#25 「1 → 실증 뒤 2」 — 2개면 preferred anti-affinity), 오퍼레이터 PDB,
  pg_dump 의 PriorityClass(워커가 잡으로 가득하면 덤프 파드가 Pending → activeDeadline(50분) 뒤 Job 실패로 보이고 다음 시각에 다시; 요청이
  100m/256Mi 라 드물다). **사용자 결정 필요**(2026-11-03 ⑤ 리뷰 — 「백업이 조용히 건너뛰어진다」 지적): `dms-addon-critical`(선점)을 주면 워커가
  잡으로 찬 동안 **매시간** 실행 중 데이터 잡(gang)을 선점한다 — 애드온 컨트롤러(재스케줄이 드묾)와 비용이 다르다. 대안: `preemptionPolicy: Never`
  의 전용 PriorityClass(큐 순서만 앞) 또는 덤프 연속 실패 경보.
- 📝 **남은 코드 보강(설계 9 「⑤ 뒤에 따로」)** — **B4** migrate 도중 재연결 감지 → 실패(우선순위 낮음), **B1** SQLSTATE 25006(read-only
  transaction) 처리·readyz 쓰기 확인(우선순위 낮음 — `target_session_attrs=read-write` 는 새 연결만 거른다(추정: switchover 뒤 옛 primary 에 남은
  연결)), B2·B6 선택, B5 보류.
- 🔧 **T6·T6' 실행 런북 위치** — 전체 실행 런북은 저장소 밖 작업 노트다. 확정된 명령은 이미 옮겼다(T7 이관 순서·대조 SQL → deploy/README
  §2f-2·§2f-5, T6' 수치·스토리지 정지 대응·durable 쓰기 측정 → §2f-7). 장애 리허설을 다시 하게 되면 그 런북을 deploy/testbed/README.md(§9 검증
  도구)에 옮긴다.
- 📝 **prod 의 마스터 배치는 테스트베드에서 실증되지 않는다**(설계 10 — testbed-k8s 의 api·controller 는 cp1 메모리 때문에 워커에 남는다):
  prod `patch-placement.yaml` 의 마스터 배치, 마스터 1대 장애, 실제 마스터 3대의 PG required anti-affinity 는 프로덕션(또는 마스터 3대 시험
  클러스터)에서 처음 본다.

### ⑥ 입구(Envoy Gateway) 후속 — 2026-11-03

CHANGELOG 「⑥ 입구(Envoy Gateway)」(2026-11-03 — 옛 입구), 설계 v6 #13~#15·4-3·8-1(S0-5·S8·S12)·8-2·10 T8, deploy/README §2g.
**2026-11-16 입구를 Envoy Gateway 에서 클러스터에 이미 있는 Traefik 하나로 바꿨다**(사용자 결정 — 병행 없음;
CHANGELOG 「입구 — Envoy Gateway 폐기, 클러스터의 Traefik 하나로 교체」,
ARCHITECTURE §6 「입구는 클러스터에 이미 있는 Traefik 하나이고…」·「입구의 원 IP 계약」). 이 제목은 다른 파일이 「BACKLOG ⑥」으로 가리켜 그대로 둔다.
Envoy·EG 전용 후속(EG 리스너 hostname·접근 로그 SNI, Envoy 접근 로그 수집처·stats :19001, EG rate limit, 남의 EG 설정의 shutdown-manager 이미지,
`platform/gateway` 의 가드 제외)은 그 층과 함께 사라져 지웠다 — 입구 접근 로그·지표(K15)는 이제 사이트 Traefik 의 몫이다. 남은 입구 후속:

- 📝 **소크 뒤 Ingress·MetalLB 삭제** — testbed-k8s 오버레이의 nginx Ingress `dms-portal`·MetalLB 풀(되돌림용, testbed `portal_edge: ingress` — 2026-11-04
  Envoy 컷오버 때 남겼고, 2026-11-16 Traefik 전환 뒤에도 testbed `validate` 가 되돌림 VIP 200 을 단언한다)을 **Traefik 입구 소크 뒤** 지운다 — 정리 시점은
  결정 대기(Traefik 입구가 얼마나 무사고로 돌면 되돌림 길을 거둘지). 그때 testbed IaC 가 dmsk8s 의 ingress-nginx·MetalLB 를 걷고 되돌림 경로가 없어진다
  (deploy/testbed/README.md §7). 같은 묶음: base `46-ingress.yaml`·`47-metallb-public-pool.yaml` 파일째 삭제 + prod `patch-remove-ingress.yaml`·
  `tests/test_testbed_k8s_overlay.py` 단언 정리(2026-11-06 신규 10). 입구 Ingress `dms-portal-traefik`(플랫폼 층)은 이름이 달라 그대로다.
- 🔒 **프로덕션 첫 Traefik 입구 적용**(사이트 운영 몫 포함 — deploy/README §2g): ① LB 가 PROXY v2 를 붙이면 Traefik websecure `proxyProtocol.trustedIPs` 에
  LB 출발지(`<SE 주소>`)를 Rancher chartValues / HelmChartConfig(`kube-system/rke2-traefik`)로 넣는다(`forwardedHeaders` 는 비움 — trustedIPs 가 없으면 Traefik 이
  PROXY 바이트를 TLS 로 읽어 연결이 깨진다), ② 사이트 체크아웃 pull → values.env 의 옛 `GATEWAY_*` 줄 정리(남아도 알림 뒤 무시)·`PORTAL_DOMAIN` 확인 → 설치기
  (S8 이 Ingress 를 만들고 노드마다 확인) → 포탈 빌드 → 릴리스(새 앱 — 본문 상한·keep-alive 120; 설치기는 `live` 태그면 앱 이미지를 바꾸지
  않는다 — LB 를 옮기기 전에, deploy/README §4-2 5·6·§2g-8 3), ③ LB 풀 = Traefik 이 도는 노드 전부 :443, 헬스 = TCP 443 또는 SNI·Host 를 `PORTAL_DOMAIN` 으로 한 HTTPS `/readyz`
  (SNI 없는 HTTPS 모니터는 404 → DOWN), ④ 실증 — 로그인 실패 보안 이벤트의 `client_ip` 가 사용자 PC 인지(SE 주소면 trustedIPs·SNAT 확인). 그 뒤 아래 옛 Envoy
  층 정리.
- 📝 **옛 Envoy 층은 설치기가 걷지 않는다** — 2026-11-16 전에 DMS 가 Envoy 입구를 깔았던 클러스터에는 Gateway·HTTPRoute·CTP·BTP·EnvoyProxy `dms-portal`·
  GatewayClass `dms`·EG 웹훅·ns `envoy-gateway-system`·EG CRD·cluster RBAC 가 남는다(설치기·감지는 더 보지 않는다 — 옛 항목 이름은 사용법 오류). 손 런북
  (deploy/README §2g-8 「Envoy 정리 런북」 1~7 — 테스트베드에서 그 순서로 실행): DMS 입구 객체 → EG mutating webhook(컨트롤러보다 먼저) → ns·cluster RBAC → EG CRD → Gateway API CRD 는
  **라벨 `app.kubernetes.io/managed-by=dms-install` 인 것만** + VAP(RKE2 는 rke2-traefik-crd 가 Helm 으로 Gateway API CRD 를 깔아 둘 수 있다 — 라벨로 가른다).
  PriorityClass `dms-addon-critical` 은 남긴다(CNPG 가 쓴다). 노드 쪽(마스터 sysctl `90-dms-envoy.conf`·:443 INPUT 방화벽)은 사이트가 둔 경우 걷는다.
- 📝 **Traefik 판 올림·보안 수정은 사이트 몫** — DMS 는 Traefik 을 설치·변경하지 않고 감지 `traefik` 도 판을 판정하지 않는다(설치 태그를 근거로만 적는다).
  RKE2 는 rke2-traefik 이 RKE2 판을 따라간다. 테스트베드는 testbed IaC `traefik` 역할이 프로덕션과 같은 판(`hardened-traefik:v3.7.4-build20260608`)을
  고정한다 — 프로덕션 판을 따라 올린다(testbed 저장소 몫).
- 🔍 **느린 큰 업로드와 Traefik readTimeout 60s** — Traefik 기본 `respondingTimeouts.readTimeout` 60s 는 요청 본문 읽기 상한이라, 본문 상한(20MiB)에 가까운
  업로드를 약 340KiB/s 보다 느리게 올리면 끊긴다(다운로드·스트리밍은 writeTimeout 0 이라 무관 — 테스트베드 65.7초 다운로드 200). 지금 큰 본문은 배치 JSON·CSV
  (CSV 2MiB 상한)뿐이라 사내망에서는 닿지 않을 것으로 본다(미실측). 생기면 사이트가 HelmChartConfig 로 websecure `transport.respondingTimeouts.readTimeout` 을
  올린다 — DMS 는 바꾸지 않는다(감지 `traefik` 이 현재 값을 note 로 보인다).
- 🔧 **keep-alive 와 사이트 idleConnTimeout 의 대소를 아무도 보지 않는다** — `DMS_API_KEEP_ALIVE_SECONDS`(기본 120)는 입구의 상류 유휴 타임아웃(Traefik
  기본 `serversTransport.forwardingTimeouts.idleConnTimeout` 90s)보다 길어야 한다(ARCHITECTURE §7 「요청 본문 상한·keep-alive 는 앱이 낸다」). 사이트가
  Traefik 쪽을 120s 이상으로 올리면 본문 있는 POST 의 간헐 502 가 돌아오는데 감지 `traefik` 은 `readTimeout` 만 읽는다. 처방: 감지가 args 의
  `--serversTransport.forwardingTimeouts.idleConnTimeout` 을 읽어 렌더 값(patch-config 에 키가 없으면 코드 기본 120) 이상이면 WARN.
- 📝 **본문 상한이 클러스터 안 에이전트 요청에도 걸린다**(2026-11-16 — Envoy 때는 입구 경유만 걸렸다): 에이전트 보고(`POST /api/agent/report`) 등도
  `DMS_MAX_REQUEST_BODY_BYTES`(기본 20MiB) 안이어야 한다. 예외는 스스로 상한을 재는 k8s 동기화 결과 POST 하나(`body_limit._SELF_LIMITED_PATHS`, 기본 33MiB).
  보고는 노드 하나의 마운트·스토리지 프로브라 20MiB 에 닿을 일은 드물다고 보지만(미실측), 에이전트 로그에 413 `request_too_large` 가 보이면 상한을
  올리거나(patch-config, dms-api 재시작) 그 라우트가 스스로 상한을 재게 해 `_SELF_LIMITED_PATHS` 에 넣는다(라우트 모양 전체 일치로만).

### ⑦ 설치기 후속 — 2026-11-03

CHANGELOG 「⑦ 설치기」, 설계 v6 8-1·8-2·9 ⑦·10 T9, deploy/README §2h, prod README. 설치기·감지기·preflight 가 이 제목을 출력한다 —
제목을 바꾸지 않는다.

- 🔧 **Volcano 층(`deploy/addons/volcano/v1.16.0`)** — 상류 매니페스트 + SOURCE·sha256 + DMS 패치 층(이미지 자리표시자 → lock digest, 라벨,
  `system-cluster-critical` 유지 — 설계 2-1), `versions.env` 의 `VOLCANO_VERSION`, lock 3줄(scheduler·controller-manager·admission — resolve 로
  확인한 amd64 digest: `vc-scheduler:v1.16.0 sha256:90cadf93…`, `vc-controller-manager:v1.16.0 sha256:e5def827…`, `vc-webhook-manager:v1.16.0
  sha256:f688627b…`), 렌더러의 `addons/volcano`. 그때까지 감지가 A(없음)면 **S1 이 멈춘다**(「클러스터 운영자에게 ≥ v1.15 설치 요청」) — 설치기
  S9b 와 install-detect 의 plan·vcjob 은 층이 생기면 그대로 쓴다(테스트베드·지금까지의 사이트는 C).
- 🔧 **새 사이트 DB 첫 이관(import bootstrap)** — 외부 PG 를 쓰던 사이트는 S2 `pg-secrets.py check` 가 FAIL 하고
  deploy/README §2f-2 「이관 순서」(덤프 → import → `set-url`)로 안내한다. CNPG `bootstrap.initdb.import` 로 Cluster 를 만드는 흐름(설계 6-4 「첫 이관」)을 설치기 단계로
  넣을지는 결정 대기(import 경로 자체는 T6 이 일회용 Cluster 로 실증 — 행 수·md5 일치).
- 🔧 **`build-and-push.sh` 사내 CA 주입** — 첫 부트스트랩·비상 빌드(`deploy/docker/build-and-push.sh` — S4 뒤 PAUSE 가 찍는 명령)는
  `podman build` 에 사내 CA 를 넘기지 않는다. 프록시 env 는 RUN 단계에 가지만 CA 는 이미지 자신의 신뢰 저장소뿐이라, TLS 를 가로채는 사내
  프록시 사이트에서는 npm·pip·curl·git 이 x509 로 실패해 첫 설치가 PAUSE 에서 멈춘다(포탈 빌드는 컨트롤 상태 `build_proxy_ca_path` →
  `build_manifests` 의 시스템 번들 + 사내 CA 번들 주입이 있다). 처방: `PROXY_CA=<파일>` env → 같은 번들을 만들어 `podman build -v …:ro` +
  클라이언트별 `--env`·`--unsetenv`(최종 이미지에서 걷기) — `build_manifests` 와 같은 방식 + 테스트. 지금은 deploy/README §1 「실패하면」의
  우회(미검증 — 가로채기 없는 망에서 `PUSH=0` 빌드 → `podman save` → `skopeo copy`).
- 🔧 **`kubectl-cnpg` 플러그인·checksums 반입**(K8 — 인터넷 노드 → 설치 호스트). 지금 설치기는 플러그인 없이 동작한다(switchover 는
  `status.targetPrimary`).
- 🔧 **T9 에서 만들지 않은 감지 행 — D>·C!** — D>(DMS 소유인데 고정보다 높은 버전 → keep·WARN, 다운그레이드하지 않음)·C!(남이 설치한
  EOL·범위 밖 → reuse + WARN·소유자 요청 문구)는 심 테스트(`tests/test_install_detect.py` — CNPG 버전 표; 2026-11-16 전에는 EG 표도)로만 봤다.
  실클러스터에서 볼 일이 생기면(예: 다음 CNPG 업그레이드 리허설에서 옛 판으로 되돌린 테스트베드) `--plan` 표·S9 의 「건드리지 않는다」를 확인한다.
  (Traefik 의 C! — trustedIPs 없음·`proxyProtocol.insecure`·정적 설정이 configFile — 는 판이 아니라 설정 WARN 이다.)
- 📝 **설치 흐름 밖에 남긴 사이트 확인** — `SHARED_DIR_ID` 를 values.env 에 손으로 옮긴다(S0 결과를 일회용 파드로 읽어 제안·대조하는 길은 없다 —
  틀리면 S4 가 「센티널 불일치」로 멈춘다), SHARED_DIR 변경 때 레지스트리 저장소 이동은 deploy/README §2e-5 손 절차(설치기는 S4·S11 가드 exit 5 로
  멈출 뿐), `crictl inspecti … pinned`·`fs.protected_*` 는 노드 셸 체크리스트(MANUAL)뿐, S12 의 413(21MiB — 2026-11-16 부터 앱의 JSON
  `request_too_large`)·긴 스트림·원 IP 는 deploy/README §2g 손 명령. (옛 EG 의 클러스터 도메인 ≠ cluster.local 걱정과 마스터 리스너 포트·:19000~19003
  확인은 2026-11-16 입구 교체로 사라졌다.)
  앱 튜닝 키(메일·로그인 보호·보존일)의 values 키는 「2026-11-06 … 후속」 1.
- 📝 **S6 판정 범위는 DMS 노드만**(설계 S6 행에는 범위가 없다): S2b·S10 과 같은 정의(`pg-path-probe.py` 의 `dms_nodes`)로 다른 팀 노드는 pull 은
  해 보되 정보로만 보인다. 모든 노드를 판정해야 한다면 `pull-probe.py` 의 판정 대상을 바꾼다 — 사용자 확인 대기.
- 📝 **PAUSE 종료 코드 10** — S4 뒤 「push 대기」를 실패(1)와 가른다. 자동화가 0 을 「끝」으로 읽어도 안전하게 0 이 아니다. 레지스트리에 이미
  lock·DMS 이미지가 다 있으면 멈추지 않는다(멱등 재실행).
- 📝 **프로브는 같은 클러스터에서 동시에 둘 돌리지 않는다** — S2b·S6·마스터 프로브는 시작할 때 같은 라벨의 옛 DaemonSet·파드를 지운다(설치 운영자는
  한 명 전제).
- 🔧 **리뷰 1 고침 가운데 라이브로 못 본 것**(2026-11-03 ⑦ 리뷰 1 — 심 테스트만): S13 첫 관리자 부트스트랩 명령(port-forward + `-H @<토큰 파일>` —
  계정을 만드는 변경이라 테스트베드에서 돌리지 않았다), 실패 certgen 재실행·`--upgrade-addon registry`(T9 일회용 k3s 는 external 레지스트리)·`--adopt db`
  의 S9 dry-run(T9 의 db 는 늘 A). 실측한 것: 태그를 올린 `--plan`(dms-migrate immutable → 앱 층 PASS)·verify 의 서비스 프록시 readyz·VIP 무응답
  WARN, 라벨 붙인 애드온 ns SSA·`--upgrade-addon cnpg`·`--adopt priorityclass`·`--adopt cnpg`(`--force-conflicts` SSA 뒤 D=).
- 🔧 **에이전트 SIGTERM 처리**(2026-11-17 S11 노드 비례 대기 조사에서 확인 — CHANGELOG 「설치기 S11 에이전트 DaemonSet 대기를 노드 수 비례로」) —
  `dms agent`(컨테이너 PID 1, 셸 없이 바로 실행 — `deploy/k8s/50-agent-daemonset.yaml` command·args)에 SIGTERM 처리기가 없어(`agent` 패키지에
  `signal` 처리 0건) 옛 파드가 종료 유예 30초(terminationGracePeriodSeconds 기본)를 다 채운다. 그래서 ds/dms-agent 롤링이 노드당 ~30초 길다
  (테스트베드 실측 노드당 ~31초 중 30초, 6노드면 ≈ 3분 — 설치기 S11 노드 비례 대기·포탈 릴리스 모두). 처리기를 넣을 때는 보고 루프 중단과 진행 중
  보고 마무리 순서를 먼저 본다.

### 테스트베드(dmsk8s) 후속

이 브랜치의 테스트베드는 k8s 클러스터(컨텍스트 `dmsk8s`) 하나다 — DMS 쪽 운영은 [`deploy/testbed/README.md`](../deploy/testbed/README.md), 인프라는
testbed 저장소. 2026-11-06 신규 테스트베드 항목(9·10·12·13 워커 CPU)은 위 「2026-11-06 … 후속」, 콜드부팅은 §3.

- 📝 **옛 base `/cephfs/dms/artifacts` 의 보존 기한·삭제 시점** — 결정 대기. ③ T3 전환은 복사라 옛 base 가 되돌림 창으로 그대로 남는다
  (deploy/testbed/README.md §7).
- 🧹 **`dms-shared-fs.yml` 의 옛 공용 디렉터리 태스크** — `/cephfs/dms`·`/cephfs/dms/artifacts` 태스크(`findmnt … FSTYPE == ceph` 단정)는 dmsk8s 에선
  옛 base 다. 보존 창이 끝나면 dmsk8s 쪽을 걷고(일반화한 마운트 가드는 `deploy/shared-dir-init.sh` 에 있다), 데이터 root·픽스처는 CephFS 에 그대로
  둔다.
- 📝 **pkg-01 외부 PG(`dmsdb_k8s`) 정리 시점** — 결정 대기. ⑤ T7 이관 뒤로는 되돌림용이다(`dms-secrets-prev` → `pg-secrets.py restore-url`; 이관 뒤
  쓰기는 거기 없다 — deploy/testbed/README.md §7).
- 📝 (선택) **testbed-k8s `values.env` 에 `PORTAL_VIP=10.10.10.1`** — 설치기 S12·`verify.sh` 의 VIP 경유 readyz 가 edge 경로를 보게(지금은 env 를
  줄 때만 본다). 렌더·설치기 테스트 영향 확인 뒤. (`PORTAL_DOMAIN=dms-k8s.local` 은 2026-11-16 부터 입구 Ingress host 라 고정 오버레이도 필수 — 이미 들어
  있어 S8·S12 의 `ingress-probe.sh` 는 env 없이 돈다.)
- 🔧 **테스트베드 `/data/k8s_pv` export 의 no_root_squash 노출 완화(보안 리뷰)** — testbed 저장소 `scripts/nfs-pv-export.sh` 가 dmsk8s 세 노드
  (10.10.10.20-22)에 rw·no_root_squash 로 연다(root 잡이 PV 디렉터리를 만지는 실증 전제). 같은 망의 그 세 IP 에서 root 로 마운트하면 모든 PV
  데이터를 읽고 쓸 수 있다. 처방 후보: 실증 기간에만 켜는 make 타깃, export 를 워커(.21·.22)로만 좁히기(cp1 제외), 실증 데이터 상한(1GiB) 유지.
  프로덕션 GPFS·OneFS 는 사이트 스토리지 정책 소관(DMS 노드만 root 마운트).
- 📝 **동적 PV 의 VOLUME 은 PVC 를 다시 만들면 바뀐다** — 테스트베드 실데이터 픽스처(`tests/fixtures/k8s/testbed-*.json`)는 그 시점
  사본이라 라이브와 달라질 수 있다(테스트는 사본만 본다). 경로 맵 다시 뽑기: testbed `scripts/tbinfo_pv.py pv-node-paths`.
- 📝 **csi-snapshotter 사이드카의 watch 오류 로그** — VolumeSnapshot CRD 를 일부러 설치하지 않아 csi-nfs-controller 가 로그를 계속 낸다(재시작 0,
  DMS 는 스냅숏을 쓰지 않는다).
- 📝 **T-SE 시험 흔적 정리**(사용자 확인 뒤) — `/cephfs-third/tse-w1`, `/cephfs-secondary/tse-w1-nsync`·`tse-w2*`, 레지스트리 `t4-gc:tse-w1`.
  w1 의 firewalld 는 꺼져 있지만 `--permanent` 포트 7개가 `/etc/firewalld/zones/public.xml` 에 남아 켜면 열린 채 시작한다(CHANGELOG
  「테스트베드 워커 EL9(T-SE)」).
- 🔍 (testbed 저장소) **cp443 은퇴 태스크의 첫 실행 rc 2** — 2026-11-16 Traefik 전환 뒤 `make baseline ANSIBLE='ansible-playbook --limit k8s-cp1 --tags cp443'`
  (cp1 sysctl `90-dms-envoy.conf`·nft `dms_testbed_cp443` 걷기)이 첫 실행에서 make rc 2 였고 재실행은 rc 0·changed 0 으로 수렴했다 — 원인 미확인. 결과
  상태는 맞다(`make validate-dmsk8s`·`post-boot-check.sh` FAIL 0). 다시 돌릴 일이 있으면 첫 실행 로그를 남긴다(이 저장소의 세션은 testbed 를 고치지 않는다).
- 📝 (testbed 저장소) **테스트베드 CA 에 keyUsage 가 없다**(2026-11-18 사내 SSO 실증) — 지금 이미지의 Python 3.11 은 받아들이지만 Python 3.13 의 엄격 검증
  (`ssl.create_default_context` 의 `VERIFY_X509_STRICT` 기본 — `oidc._ssl_context`)이면 그 CA 가 서명한 인증서(Keycloak — dms-api 의 `DMS_OIDC_CA_PEM`)를
  거부해 SSO 가 `sso_upstream_unreachable` 로 깨진다. 이미지의 Python 판을 올릴 때 CA 를 keyUsage(keyCertSign·cRLSign) 포함으로 재발급한다.

## 2. 의도적 보류 (결정 — "미구현"이 아님)

- ⏸ **Prometheus/Grafana** — 포탈 대시보드로 대체(입구 Traefik 의 지표·접근 로그는 사이트 몫 — DMS 는 Traefik 을 바꾸지 않는다).
- ⏸ **롤백 버튼** — 이력에서 옛 태그를 다시 골라 릴리스하는 것으로 갈음(deploy/README §5-1·§9). `kubectl rollout undo` 는 되돌림 수단이 아니다.
- ⏸ **레지스트리 경로 접두 미지원** — `DMS_BUILD_REGISTRY` 는 `host:port` 만 된다: `registry` 모듈이 `http://{registry}/v2/{repo}/…` 로 URL 을
  만들고(`host:5000/k8s` 면 `/k8s/v2/…` 로 깨진다) `manifest_tags.image_registry` 는 첫 경로 조각만 레지스트리로 봐 `site_image` 가 늘 None(배지
  「모름」)이 된다. 그래서 클러스터마다 레지스트리를 나눈다 — 레지스트리를 같이 쓰면 「사용 중」 보호(`routes_registry._in_use_tags`)가 자기
  클러스터만 봐 상대의 라이브 태그를 지울 수 있고 태그 제안이 경합한다(deploy/README §2e-1). 필요해지면 고칠 곳: `registry` URL 분해,
  `manifest_tags.image_registry`, 빌드 스탬프 정규식, `_registry_host`.
- ⏸ **이미지 태그의 원천은 사이트에 하나 — 포탈 릴리스는 그 원천을 고치지 않는다** — testbed-k8s 는 오버레이 `newTag` + `patch-config.yaml`
  `DMS_JOB_IMAGE`(커밋), prod 는 values.env `DMS_TAG`·`DMS_AGENT_TAG`·`MFU_TAG`(명시 태그, 또는 `live` = 라이브에서 읽음 — `render.sh`).
  명시 태그 사이트·테스트베드가 포탈 릴리스 뒤 원천을 안 맞추면 다음 설치기·apply 가 `guard-images.sh` exit 3 으로 멈춘다(되돌리지 않는다).
  `MFU_TAG=live` 는 ConfigMap `DMS_JOB_IMAGE` 를 읽는다 — 잡 이미지 릴리스는 DB `control_state.job_image`(`job_image.resolve_job_image` — DB 가 env 를
  이긴다)만 바꾸므로 live 는 마지막 설치기 값을 돌려주고 실제로 쓰이는 것은 릴리스 값이다. 릴리스가 원천(values·오버레이)을 고치는 계층은 두지 않는다.
- ⏸ **다음 태그 제안은 `dN` 최댓값 + 1 만**(2026-10-26) — 포탈 빌드 화면의 제안(`BuildForm.nextTagSuggestion`)은 그 사이트의 라이브 태그·자기
  레지스트리·빌드 이력의 `dN` 최댓값 + 1 이다 — git 의 오버레이 태그를 읽지 않으므로 프로덕션도 자기 번호의 다음을 제안한다. 다른 접두
  (옛 `kN` 등)를 따라 제안하는 처방은 하지 않는다(프로덕션 제안이 접두 판정에 흔들린다).
- ⏸ **빌드 레이어 캐시·멀티아치** — 슬라이스 11 비목표.
- ⏸ **요청 삭제의 옛 base 사본은 지우지 않는다**(사용자 결정 2026-10-09) — 삭제 시점 base 와 지금 base 가 다르거나
  (그 사이 force 변경) 잡의 `artifact_uri` 가 다른 base 를 가리키면 request-purge 는 그 경로를 열지도 지우지도 않고
  `artifact_left_at_old_base` 경고(옛 base·job_id·경로 표시용)만 남긴다 — DB 값(신뢰 경계)만 믿고 다른 경로를 지우지
  않는다. 지우려면 운영자가 경고의 경로를 확인하고 손으로.
- ⏸ **root 실행 잡의 삭제에 특권 세션을 더 요구하지 않는다**(사용자 결정 2026-10-09) — 요청 삭제는 세션 관리자
  (`require_session_admin`)면 되고 `can_run_as_root` 자격은 보지 않는다. admin 은 신뢰 앵커이고(ARCHITECTURE §7 규칙
  14), 감사 스냅숏(`audit_log` 'request'/'delete' before_state)이 run_as_root·실행 신원·전이 actor 를 보존한다.
- ⏸ **보호 계정의 셀프 비밀번호 변경 없음**(2026-10-12 root 자격 부여 리뷰 → 2026-11-03) — 특권 목록(env ∪ 계정 화면 부여) 계정은
  메일 재설정이 막히고(`_refuse_privileged_reset`) 로그인 상태의 비밀번호 변경 라우트도 없다. 다른 특권 세션 관리자의 관리자 재설정이 길이다 —
  **특권 관리자가 한 명뿐이면** 그 계정의 비밀번호는 바꿀 길이 없다(관리자 재설정은 자기 자신이 409). 두 번째 특권 관리자를 두거나,
  x-admin-token 부트스트랩으로 새 관리자를 만든 뒤 env 목록으로 승격한다(배포 변경). 그래서 부여 확인 창이 「비밀번호를 아는 사람은 모두 root」를
  말한다. 처방: 세션 인증 셀프 변경(현재 비밀번호 확인 + 봉인 통로 `postWithSealedPassword`/`_password_from`).
- 📝 **자격을 잃은 생성자의 진행 중 배치** — 배치 자식은 특권 목록 판정(IF_ELIGIBLE)이라, 생성자가 자격을 잃은 뒤 계획되는 남은 자식은
  `privileged_not_authorized` 로 거부된다(2026-10-26 LDAP 코드 삭제부터 — 낮출 비 root 신원이 없다). 한 배치 안에서 root 결과와 거부가 섞일 수
  있다 — 필요하면 그 배치를 취소한다.

## 3. 외부 자원에 막힘

- 🔒 **호스트 콜드부팅 리허설 미실증**(테스트베드, 2026-10-22) — VM 단독 재부팅(k8s-w1·k8s-w2)과 hostnet 유닛 재시작은 실증했고, 그 과정에서
  testbed 부팅 가드의 두 겹 마운트 경합을 고쳤다. 호스트 전체 재부팅(VM 동시 기동, pkg-01 Ceph 늦게 올라옴)은 세션에서 할 수 없다 — 다음 호스트
  재부팅 뒤 testbed `make post-boot-check WAIT=900` 이 그 실증이다(testbed ARCHITECTURE §18).
- 🔒 **멀티 클러스터 라이브 확인** — 두 번째 클러스터가 필요하다(위 「dms-k8s RBAC·PV」 (i)).
- 🔒 **testbed 저장소 후속** — 다른 저장소다(위 「2026-11-06 … 후속」 12).

## 4. 알려진 해석·잔여 리스크 (문서)

- 📝 **API 자체 artifact base 점검 후속**(2026-11-23 dms-ssc 4add57bf 이식 — ARCHITECTURE §7 불변식 16, deploy/README §12e). 남은 것:
  - 🐞 **(최우선) 미마운트 노드 컨트롤러의 정리 루프가 「없음 → 완료」로 끝난다**(2026-11-23 이식 리뷰 — dms-ssc 와 공통, 만료는 이
    브랜치에만). 41-controller 의 hostPath `type: Directory` 는 경로 존재만 보고 prod 는 api·controller 를 같은 마스터 집합에 두므로
    (`patch-placement`), 사고 노드(로컬 빈 `<SHARED_DIR>/dms/artifacts`, root:root 0755)에 컨트롤러도 뜰 수 있다. 그 디렉터리는
    `artifact_trash._open_base` 를 통과하고(소유자 = euid, g+w·o+w 없음) `detach` 가 'absent' → `entry_states` (False, False) →
    `request_purger._complete` 가 `_finish`(만료는 `_finish_expiry`)로 아웃박스 행을 지운다(한 틱 — `test_absent_artifact_dir_is_outcome_absent` 가 고정하는 동작).
    같은 길: 사람의 요청 삭제·`batch_deleter`·`batch_round_prune`(스케줄 배치는 자동)·`artifact_expiry`. 공유 스토리지의 진짜
    `<base>/<job_id>` 는 영구 고아(설계 D12 — 고아를 추론해 지우지 않는다), 흔적은 `request_purged` 의 outcomes 'absent' 뿐(이벤트 보존 30일).
    가드 모양: `RequestPurger.run_once` 의 drain 검사 바로 뒤에서 `PROCESS_RUNNER.run(("purger", path), lambda: availability_check(path,
    identity_job_ids(repos, settings, uri)))` — ok 가 True 가 아니면 `_reap`·due·`_settle` 전에 return(아무 행도 끝내지 않음), 사유는 이미
    등록된 `purge_base_unavailable`, 전이 때만 stderr·이벤트. 테스트: 빈 base + 최근 성공 잡 → 아웃박스 행이 남고 `request_purged` 없음.
    사용자 지시(dms-ssc 선행 → 동일 이식)대로 다음 묶음에서 두 라인에 같이 넣는다. 그때까지 운영 신호는 컨트롤러 점검(§12e 「컨트롤러
    노드의 base 가 비면」).
  - 📝 **멈춘 마운트 감지 전 창에서 readyz 가 굶을 수 있다**(이식 리뷰 — 이번 변경 전부터 있었고 이번에 줄었다). readyz·모니터는 메모리만
    읽고 FS 점검은 `BaseCheckRunner`(키당 작업 스레드 1개·10초)라 멈춘 마운트에 매달리지 않지만, hung 단락은 모니터 회차가 멈춘 뒤 10초가
    지나야 켜진다 — 그 전(최대 ~70초)의 아티팩트·사용량·scan-stats 첫 열기는 요청 스레드를 D 상태로 영구히 묶는다. 묶인 스레드가 anyio
    스레드풀(기본 40)을 다 쓰면 동기 def 인 readyz 가 굶어 NotReady(replicas 1 이면 포탈 전체가 Service 에서 빠짐; `/healthz` 는 async 라
    재시작 루프는 없음). readyz 를 async 로 바꾸면 DB RLock 대기가 이벤트 루프를 막아 이번엔 안 바꾼다 — 운영: 마운트 복구 뒤
    `rollout restart deploy/dms-api`(§12e).
  - 📝 **지운 API 파드의 점검 행이 신선 창(180초) 동안 경보를 유지한다**(2026-11-23 d40 실증) — 롤아웃 원복으로 실패 파드가 지워진 뒤에도
    약 3분 동안 「문제 1건」·엇갈림 상자·「dms-api ×2」 경고·「파드 점검 실패」·문제 노드 1 이 남았다가 「보고 끊김」이 된다. 줄이려면 모니터가
    꺼질 때(SIGTERM) 자기 행을 끝냄으로 표시하거나, 관리 API 가 파드 목록(이미 형제 판정에 쓰는 k8s 조회)과 대조해 없는 파드를 뺀다.
  - 💡 (선택) 503 `artifact_base_unavailable` 에 `Retry-After` — 지금은 헤더가 없다(포탈은 health 감시로 복구를 안다).
  - ✅ 마운트 현황(`GET /api/admin/mount-status`)에 API 파드·컨트롤러 점검을 겹치기 — 2026-11-23 `mount_status_pods` 로 끝냈다(위 「마운트
    현황 후속」의 꼬리표 항목 — 행 꼬리표·`control_pods`·문제 노드 한 칸 규칙, dms-ssc routes_mounts 와 같은 재료·규칙). 남은 차이: 표
    밖 노드(에이전트 없음)의 파드는 각주 한 줄이라 그 노드의 마운트 상태는 여전히 모른다(에이전트를 그 노드에 띄우지 않는 한).
  - 미리보기 단계의 같은 굳음: `stepper._poll_preview` 의 summary None 은 지금도 즉시 `empty_preview`(Rejected)로 끝난다 — 실행 단계와
    같은 유한 지연(`_defer_summary`)을 걸지 결정. 실행 단계 지연은 컨트롤러 프로세스 메모리라 재시작마다 상한 한 번이 더 붙는다(TTL/2
    여유가 그 몫 — 재시작이 TTL 안에 여러 번이면 GC 뒤 FAILED 로 둔갑할 수 있다; 지속하려면 잡 행에 처음 미룬 시각).
  - 릴리스 실패 사유에 API 시작 게이트 정보: 게이트에 막힌 새 파드는 Running·unready 라 `rollout_failed:ProgressDeadlineExceeded…`
    만 남는다. rollout_watcher 가 `api_base_checks` 의 신선·waiting 행(「새 파드 X(노드 Y): 아티팩트 경로 점검 실패 — 사유」)을 덧붙이는
    것은 사유 문자열 형식(200자 절단·포탈 prefix 번역)과 맞물려 이번엔 뺐다 — 관리 화면의 API 파드 목록이 그 사실을 보인다.
  - (dms-ssc 원문의 「지연 중 잡이 클레임 앞자리를 차지한다」는 같은 커밋의 `touch`(이 브랜치는 `touch_held`)로 이미 닫혔다 —
    `test_deferred_job_moves_to_the_back_of_the_claim_queue`. 이식 때 뺐다.)

- 📝 **파일 크기 분포에 구간별 용량이 없다**(2026-10-21, d183). dscan(포크 1b93d54)의 `file_size_histogram` 은 구간별 **개수**만
  쓴다(시간 히스토그램만 bytes). 그래서 사용량 화면은 개수·비중만 보인다 — 「1 GiB 넘는 파일이 용량의 몇 %」는 못 말한다. 해법:
  포크 `acc_emit_entry` 에 `size_bytes[bin] += size` 와 JSON `"bytes"` 키 → 잡 이미지·에이전트 재빌드. 투영·캐시·CSV 는 bytes 가
  구간에 있으면 자동으로 용량 열을 붙이게 이미 돼 있다. 화면 차트는 2026-11-16 부터 공용 `ScanHistograms.SizeHistogram`(옛
  `SizeDistribution` 삭제 — 사용량 시트·스토리지 합계·배치 scan 항목이 같이 쓴다)이고 **개수 막대만** 그린다 — 용량을 보이려면 그
  컴포넌트도 고친다(dms-ssc 와 같은 export 를 유지).
- 📝 **스토리지끼리 파일시스템을 공유하는지 모른다**(같은 날). 에이전트 statvfs 보고에 `f_fsid` 가 없어, 같은 파일시스템을 다른
  경로로 등록한 스토리지들의 df 가 같은 값으로 나란히 보인다(예: 테스트베드 cephfs 셋). 그래서 스토리지별 비교는 df 를 더하지 않고
  각주로만 말한다. 해법: probes 가 fsid 를 함께 보내고 화면이 같은 fsid 를 묶어 표시.
- 📝 **비 UTF-8 이름의 남은 표시**(2026-10-26 「카운터만 둠」 결정, CHANGELOG 「BACKLOG 결정 묶음 구현」) — 포크 dscan 이
  `summary.invalid_name_entries`(이름 d_name 이 엄격 UTF-8 이 아닌 항목 수 — 나쁜 이름의 디렉터리는 한 번, 그 자손은 세지 않는다)를 쓰고
  broken 사유·`broken_paths_total` 에는 더하지 않는다. 포탈은 배치 항목 scan 리포트·사용량 분석에 보이지만, 단건 scan 요청 상세에는 dscan
  리포트 칸이 없어 이 수가 안 보인다(관리자 전용 `useRequestScanStats` 를 붙이는 후속). broken_paths 는 원시 바이트 그대로이고 별도 hex 표기는
  하지 않았다.
- 📝 **경로 봉쇄(rm·sync·PV scan)의 남은 TOCTOU 창**(2026-10-21 배치 rm 리뷰, 2026-10-28 sync·PV 까지 한 규칙). 「상위 경로에 심링크 성분 없음」은
  preflight·exec_preflight(`_RM_CONTAINMENT_CHECK`)와 러너의 도구 직전 재확인(`dms_job_runner.contain` — launcher 가 같은 스토리지 hostPath 를
  마운트하고 root 라 가능했다) 세 번 본다. 그래도 러너 확인 → mpirun → 워커 rank 의 도구가 경로를 lstat·걷기 시작할 때까지(워커 기동·MPI 초기화,
  보통 수 초)는 비어 있다 — 상위 디렉터리에 쓸 수 있는 사용자가 그 사이에 정확히 심링크로 바꾸면 root 도구가 링크를 따라간다. PV 주소·직접 경로
  sync 의 소스·목적지도 같은 봉쇄라(마지막은 dsync·dscan 런처 mpirun 직전, nsync 각 rank 의 도구 exec 직전) 남는 창의 종류가 같다. 닫는 길은
  **포크 루트 고정(B)**: 각 루트를 시작에 한 번 `O_NOFOLLOW` 로 열어 dev/ino 를 고정하고 모든 단계·루트 메타 복원을 그 fd 로(지금은 단계마다
  경로로 다시 연다), 대상의 상위 경로는 성분마다 `openat(O_NOFOLLOW|O_DIRECTORY)`(또는 `openat2(RESOLVE_NO_SYMLINKS|RESOLVE_BENEATH)`, 기준 fd =
  managed_root), 소스 루트가 디렉터리가 아니면 실행 거부, 선택 `--beneath <mount>` — 잡 이미지 재빌드가 필요해 보류(dms-ssc BACKLOG 와 같은 항목).
  다른 처방: hostPath 를 PV 루트로 좁히기.
- 📝 **제출 경계의 lone surrogate 는 422 가 아니라 500**. JSON 본문 `"target":"a/\udcb4x"` 는 파이썬 json·pydantic 을 통과해 DB INSERT 에서
  UnicodeEncodeError → 500(요청 행은 안 생긴다). `domain.validate_relative_path` 에 `path.encode("utf-8")` 실패 → `unsafe_path`(이미 양쪽
  등록된 코드)를 넣으면 422 가 된다. 경로를 받는 다른 입력도 같다.
- 📝 **러너가 `<phase>` 의 `chown -R` 반환코드를 보지 않는다**(`dms_job_runner.runner`, 2026-09-30 아티팩트 쓰기 감사). mkdir 은 되고 chown 만
  거부되는 드문 FS(NFSv4 idmap 등)에서 scan 이 원인 표시 없이 dscan "Failed to write output file" 로 죽을 수 있다. 진단성 문제 — 반환코드 검사 +
  마커. 잡 이미지 재빌드가 필요해 보류.
- 📝 **root 실행은 기존 목적지의 소유·권한을 소스에 맞춘다**(dms-k8s 는 모든 잡이 root, 의도·경고됨). root preflight 는 소유 검사를 하지 않고
  dsync 는 소스 최상위 메타데이터를 기존 목적지에 적용한다. PV 주소 목적지와 PV 를 담는 스토리지의 직접 경로 목적지는 결과를 목적지 스토리지
  기본 소유자(특권 운영자 옵션이면 잰 PV 루트 소유자)로 맞추므로(`execution_manifests._auto_chown`·`k8s_access.direct_dest_owner` — 소유는 그 값,
  권한·시각은 여전히 소스) 남는 경우는 면제 관리자의 **직접 경로** 목적지(배치 포함) 중 PV 를 담지 않는 스토리지(filesystem, both 의 PV 루트
  밖)다 — 확인 스텝·배치 캡션이 경고한다.
- 📝 **에이전트↔API 채널이 클러스터 내부 평문 HTTP**(`http://dms-api:8080`, 2026-09-29 기록). 관리자급 공유 토큰이 매 보고 주기마다 평문으로
  지난다. 파드 네트워크 스니핑이 가능한 공격자는 이미 공유 토큰을 얻으므로 방안 A 가 경계를 넓히진 않았지만, 채널 TLS(또는 CNI 암호화)가
  근본 해법이다(§1 「에이전트 채널의 노드별 자격」과 함께). 실패 지속 시 재전송을 줄이려면 에이전트가 받은 블록을 메모리에 들고 로컬 백오프로
  재시도하고 서버엔 "보류 해시" 만 알리는 방식이 있다(현재는 실패가 로그·directory.error 로 즉시 보이는 쪽을 택함).
- 📝 **NFSv4/GPFS 고유 ACL 은 제어면 base 검사 밖** — `artifact_base._posix_acl_problem` 은 POSIX ACL xattr 만 읽는다. 그런 ACL 이 base
  **자체**나 **상속**(NFSv4/GPFS inheritable ACE 가 `<job_id>/<phase>` 에 주는 쓰기)으로 쓰기를 주면 3홉은 초록이다 — 남는 위험(운영 base 는
  root:root 755 에 상속 ACE 없음을 직접 확인 — deploy/README §2b-1). dms-k8s 의 잡은 모두 root 라 「같은 그룹의 다른 요청자」 경로는 없다.
- 📝 **nsync 복합 preflight 는 PENDING 을 보고하지 않는다**(`execution_volcano.poll` 의 `pods/a,b` 결합 — 한쪽이 대기 중이면 RUNNING). 그래서
  stepper 의 PENDING 분기(`_raise_if_blocked` — 계획 뒤 배치 제외·cordon 된 노드의 대기 단계 종단)가 nsync preflight 파드 쌍에는 걸리지 않는다 —
  스케줄 전 파드는 activeDeadlineSeconds 도 발화하지 않아(스케줄 뒤에만) cordon 된 노드의 쪽은 그대로 멈춘다(대기 단계 재확인을 다시 만들면
  같은 구멍에 빠진다). 처방: 복합 결합에 "하나라도 PENDING 이면 PENDING"(RUNNING 보다 낮은 우선) 추가.
- 📝 **`by_storage` 가 `COALESCE(storage_name, destination_storage)`** — sync 를 도착지 기준으로 센다. 설계가 기준을 명시하지 않은 침묵의
  해석(`repositories.metrics`).
- 📝 **CI 없음** — e2e·전체 스위트는 **수기 게이트**다(deploy/README §8 「이미지 빌드 전 게이트」). 기술적 강제 수단 부재 — 러너 상주·브라우저
  캐시·시크릿 관리가 얽힌 별도 작업.
- 📝 **`imagePullPolicy: IfNotPresent` + 태그 재사용 = 노드 캐시 stale** — 고유 태그 관례(dN 단조 증가)로만 완화. 같은 태그 재빌드는 노드가
  옛 이미지를 쓸 수 있다.
- 🔧 **러너 root 쓰기가 심링크를 따라간다**(`dms_job_runner.runner` `main().write_text` → `open(path, "w")`; 2026-09-09 root 전환 검토에서 확인,
  전환과 무관한 기존 결함) — 요청자가 chown 이후 자기 phase 디렉터리에 `stdout.log`/`summary.json` 심링크를 심으면 root 가 그 잡의 스토리지
  마운트·artifact base 아래 임의 경로를 truncate/덮어쓴다(내용은 도구 stdout). 처방: chown 전에 phase dirfd 를 잡아 `os.open(name,
  O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW, dir_fd=dfd)` 로 쓰고, 마지막 쓰기 뒤 그 세 파일을 요청자로 chown. 그러면 API 소유자 검사
  (`artifact_files.inode_allowed`, 현재 `{0, 요청자}`)를 `== 요청자` 로 좁힐 수 있다. **잡 이미지(dms-mpifileutils) 재빌드가 필요**해 제어면
  root 전환(d128)과 분리했다. 같은 계열: `parsers` 의 dscan-report.json following open(카운트만 파싱, fail-soft).
- 📝 **컨트롤러 롤아웃 겹침 창** — Deployment 롤아웃 직후 옛 파드가 종료 유예(30s) 안에 마지막 틱을 마치며 옛 코드로 잡을 스텝할 수 있다
  (2026-09-09 d129 실증: 새 파드의 preflight 파드 2초 뒤 옛 파드가 preview vcjob 을 구 마운트로 제출 → 그 잡만 실패, 재실행 성공). 리스는 틱
  시작에만 확인되고 TTL 30s 라 구조적으로 남는 창이다. 처방 후보: 컨트롤러 preStop 에서 루프 중단 후 대기, 또는 리스 재확인 뒤 제출. 파드
  스펙을 바꾸는 릴리스 직후 첫 잡은 재실행이 필요할 수 있다 — controller 롤아웃 뒤 약 30초(옛 파드 종료 유예)가 지나서 잡을 낸다.
- ~~**securityContext 드리프트는 배지가 못 본다**~~ — 해소(2026-10-26, CHANGELOG 「BACKLOG 결정 묶음 구현」 — 릴리스 화면의 「보안 설정
  드리프트」 배지, `routes_releases` 의 `security_drift`). 대시보드(`/api/admin/metrics/infra`)에는 아직 없다 — 필요하면 같은 계산을 재사용.
- 📝 **하드링크는 코드로 못 막는 부분이 있다** — `fs.protected_hardlinks=1`(+symlinks)은 공유 FS 를 마운트하고 사용자가 link(2) 를 부를 수
  있는 **모든** 호스트의 배포 전제(deploy/README §2b). 코드 측(nlink>1·남의 소유 거부)은 보완일 뿐이다.
- 📝 **아티팩트 소유자 검사의 `0` 허용** — 러너가 chown 뒤 root 로 쓰는 세 파일 때문. 요청자가 w+x 를 가진 비-sticky 디렉터리의 **root 소유**
  0600 파일을 `mv` 로 자기 phase 디렉터리에 들여오면(드묾) 읽힌다. 위 러너 항목이 닫히면 함께 닫힌다.
- 📝 **pod GC 86400s** — 성공·진행 중 잡의 프리플라이트/실행 파드 로그 라이브 열람 창(2026-10-16 부터 종단 잡 중 성공이 아닌 잡의 vcjob ref 도
  max(이 창, DMS_VCJOB_TTL_SECONDS) 뒤에 지운다 — Aborted vcjob 은 Volcano TTL 이 치우지 않았다. TTL 0 이면 vcjob 은 두고 Pod 만). 실패 잡은
  `diag_logs` 로 박제해 시한부가 아니다(부분 완화).
- ~~**빌드 파드 GC 도 200건 창 고착**~~ — 해소(2026-10-26, CHANGELOG 「BACKLOG 결정 묶음 구현」 — `builds.pods_gc_at` 완료 표시).
- ~~**빌드 삭제 docstring 의 거짓 전제**~~ — 해소(2026-10-26, CHANGELOG 「BACKLOG 결정 묶음 구현」 — `routes_builds.delete_build` 가 행을 지우기
  전에 빌드·프로브 파드를 terminate, 실패면 502 `build_pod_cleanup_failed`).
- 🔧 **claim_steppable 의 클레임 굶주림**(2026-10-27 코드 읽기로 발견, 기존 결함 — 재현 테스트부터) — `data_jobs.claim_steppable` 은 updated_at
  오래된 순 LIMIT 10 인데 상태가 바뀌지 않는 단순 폴링은 updated_at 을 밀지 않는다 — 오래 도는·Volcano 대기 잡이 10개를 넘으면 그보다 새 잡은
  틱마다 클레임되지 못한다(배치 max_concurrency 는 64까지). 지금은 실행 관문 보류만 `touch_held` 로 민다. 처방 후보: 클레임 순서를 별도
  칼럼(last_polled_at)으로, 또는 폴링마다 민다(updated_at 을 나이·GC 기준으로 쓰는 곳 — `mark_exec_submitted` 가 일부러 안 건드리는 이유 — 을
  먼저 본다).
- 🔧 **깨진 JSON 행 하나가 stepper 틱 전체를 멈춘다**(같은 발견) — steppable 행의 `phase_refs`·`worker_pool`·`options` 가 깨진 JSON 이면
  `claim_steppable` 안의 `_hydrate` 가 예외를 올려 매 틱 모든 잡이 멈춘다. 처방: 행 단위 격리(그 행만 이벤트를 남기고 건너뛰거나 fail-closed
  종단 — DB 가 신뢰 경계).
- 🔧 **pod-gc·취소의 ref 미검증 terminate**(DB 신뢰 경계) — 요청 삭제 정리는 ref 가 **그 잡의 DMS 명명**일 때만 지운다(`purge_runner.parse_ref` —
  `pod/dms-preflight-<job12>-…`·`vcjob/dms-<op>-<phase>-<job12>`, 거른 ref 는 `purge_ref_rejected`). 그러나 pod-gc
  (`pod_gc.PodGarbageCollector._targets` → `run_once` 의 terminate)와 취소(`api.cancel.terminate_job`)는 phase_refs 값을 그대로 terminate 한다 —
  변조 행이 `pod/dms-api-…` 를 가리키면 제어면 파드를 지운다(같은 네임스페이스 — api·컨트롤러 Role 둘 다 pods delete, `deploy/k8s/10-rbac.yaml`).
  처방: 두 경로도 `parse_ref` 를 거치게(스텁 ref 모양 포함 — `StubPurgeRunner.ref_belongs_to_job`).
- 📝 **요청 삭제의 잔여 위험**(2026-10-08, 수용·문서화) —
  (a) 노드가 분할된 채 운영자가 Terminating 파드를 `--force` 로 지우면, 그 노드에서 실제로 아직 도는 launcher 가 정리 **뒤에**
  `<base>/<job_id>/<phase>` 를 다시 만들 수 있다. 정리가 끝나기 전이면 `artifact_reappeared` 로 k8s 단계부터 다시 하지만, 끝난 뒤면 **행 없는
  디렉터리**가 남고 DMS 는 그것을 지우지 않는다(「DB 에 없는 디렉터리 = 고아」 추론 금지) — 운영자 판단(deploy/README §12). (b) 정리 대기 중
  이미지를 이 기능 이전으로 롤백하면 옛 컨트롤러는 아웃박스를 모른다 — 다시 올리면 재개. (c) 최종 scrub 뒤에 늦게 들어온 그 id 의 이벤트는
  retention(30일)이 지운다. (d) `requests.commit_order` 는 현존 행 기준 단조라 최신 요청을 지우면 번호가 재사용된다(UNIQUE·커서 안전).
- 📝 **로그인·인증 보호 장치는 프로세스 메모리**(2026-09-07 → 2026-11-03, `api.login_limiter`·`api.login_guard`)
  - 대상: 아이디 실패 상한·총 시도 상한, 인증번호 전체 오답 상한, scrypt 슬롯, 대기형 전체 스위치, 경보 카운터·이벤트 몫
  - dms-api 레플리카가 N 이면 실효 상한이 N 배이고, 파드 재시작이 모두 비운다(보안 이벤트 화면이 「시작 이후 값」으로 알린다).
  - replicas 1 이 전제다(40-api.yaml 의 빌드 락 제약과 같은 전제). 레플리카를 늘리면 DB 기반으로 옮겨야 한다.
- 📝 **비밀번호 전송 봉인은 능동 MITM·replay 를 막지 않는다**(설계 전제, `api.password_transport` docstring) — 페이지 JS 를 바꿔치기할 수 있는
  공격자는 인증서 신뢰(사용자 CA 설치/공인 CA)로만 막는다. 봉인이 막는 것은 TLS 종단 이후 클러스터 내부 평문·TLS 검사 프록시·프록시 본문
  로그·인증서 경고 무시 접속의 수동 도청이다. **x-admin-token 부트스트랩 경로는 평문 허용**(운영자 curl, 토큰 보유 = admin) — 봉인해 보내면
  그대로 받으므로 스크립트가 `seal_with_info` 를 쓰면 닫힌다(토큰의 상시성은 §1 「2026-11-06 … 후속」 5).
- 📝 **원 IP 기록은 앞단 계약에 기댄다**(2026-11-03 — 원 IP 는 상한 키가 아니라 기록·경보 전용, X-Real-IP 무시, XFF 마지막 항목 → 피어)
  - 앞단 계약(입구 Traefik 이 `forwardedHeaders` 를 비운 채 클라이언트의 X-Forwarded-*·X-Real-Ip 를 지우고 원 IP 를 XFF 끝에 덧붙임 — 2026-11-16,
    ARCHITECTURE §6 「입구의 원 IP 계약」; 그 전에는 Envoy 가 같은 일을 했다)이 깨지면 **기록된 IP 가 틀린다**. 막는 판정은 틀리지 않는다. 사이트가
    Traefik `forwardedHeaders` 를 신뢰(trustedIPs·insecure)로 바꾸는 것이 그 대표다 — 감지 `traefik` 이 note 로 보인다.
  - 프록시 없이 직접 닿는 배치(port-forward)는 XFF 를 클라이언트가 심을 수 있다. 위조 IP 로 출발지 경보를 부풀려도 이벤트는 창당 50건까지이고
    (그 뒤 출발지는 기록 안 됨 — 전체 실패 경보가 규모를 요약), 정작 진짜 출발지 경보가 그 몫에 밀려 빠질 수 있다.
  - LB 가 PROXY v2 를 붙이지 않는 사이트(Traefik trustedIPs 를 두지 않는다 — 옛 `GATEWAY_PROXY_PROTOCOL=off` 모드는 2026-11-16 Envoy 와 함께 없앴다)
    에서는 앱이 사용자 원 IP 를 모른다(LB 가 SNAT 하면). 접근 로그·보안 이벤트의 `client_ip` 는 LB(SE) 주소이고, 모든 사용자가 한 출발지로 묶이는
    출발지 경보(`DMS_LOGIN_ALARM_SOURCE_FAILURES`)는 사이트가 patch-config 로 0(끔)을 넣는다 — 렌더가 더는 자동으로 넣지 않는다(`deploy/render.sh`
    머리 주석 「입구」). 전체 실패 경보와 아이디 기준 상한·거절은 그대로라 막는 판정은 바뀌지 않는다. 원 IP 는 LB 의 VS 로그에서 시각으로 맞출 뿐이라,
    출발지 단위 조사가 필요하면 LB 로그 보존을 사이트에 요청한다 — 근본 해결은 LB 의 PROXY v2 + Traefik trustedIPs(deploy/README §2g)다.
- 📝 **비활성화는 세션 세대를 회전하지 않는다**(2026-11-03)
  - 비활성 계정의 세션은 요청마다 재검사로 401 이 되고, 그 요청에서 쿠키가 비워진다.
  - 그런데 비활성 기간에 한 번도 쓰이지 않은 쿠키는 다시 활성화하면 되살아난다(쿠키 수명 14일, starlette 기본 — 로그아웃도 같은 한계:
    §1 「2026-11-06 … 후속」 13).
  - 탈취가 의심되면 비활성화가 아니라 **관리자 비밀번호 재설정**으로 끊는다.
- 📝 **대기형 전체 스위치는 env + 재시작으로만 켠다**(2026-11-03, D18 미정)
  - 재시작은 메모리 카운터(아이디 감속 포함)도 비운다. prod 에서 env 를 바꾸는 영구 경로는 deploy/README §3-4.
  - 포탈에서 재시작 없이 켜는 스위치는 결정 뒤에 만든다.
  - 스위치를 켠 채 공격이 설정 속도를 넘으면 정상 로그인도 기다리거나 503 을 받는다.
- 📝 **노드 CPU 상한은 다른 파드의 requests 를 보지 않는다**(2026-10-16, `node_cpu` 모듈 docstring). 상한 = floor((allocatable − launcher 100m −
  `DMS_NODE_CPU_RESERVE_MILLICORES`) / 1000) 의 예비는 **설정값**이다 — 컨트롤러·에이전트는 DaemonSet·제어면·빌드 파드의 실제 requests 를 볼 수
  없다(클러스터 전역 pods list 권한이 필요해 보안 범위를 넓히는 결정이라 하지 않았다). 노드의 DMS 외 requests 합이 예비 + 내림 여유를 넘으면
  워커가 그 노드에 안 들어가 Volcano 대기(Pending 상한 없음)에 머문다 — 운영자가 `kubectl --context <ctx> describe node` 의 Allocated resources 를
  보고 예비를 올린다. 정확히 하려면 에이전트가 자기 노드의 파드 requests 합을 보고하는 경로(노드 범위 pods list)가 먼저다.
  - **사례(2026-11-26 테스트베드 — sync 진행률 실증 준비)**: k8s-w1 allocatable 1800m, 실행 중 파드 요청 합 705m(cnpg-controller-manager·dms-api·
    dms-controller·ingress-nginx·cilium init·coredns 각 100m, dms-pg 50m, csi-nfs 30m, dms-agent 25m) → 남음 1095m. 상한은 floor((1800 − 100 − 500)
    / 1000) = 1 이라 planner 는 w1 에 워커 1개(1000m)를 놓는데, Volcano binpack 이 launcher(100m)를 w1 에 먼저 놓으면 w1 에 1100m 가 필요해
    **5m 모자라 갱이 영구 Pending** 이다(Insufficient cpu — stepper 의 PENDING 재검사 ③ 은 보고된 상한 1 로만 보므로 회수하지 않는다). nsync 는
    늘 양 노드(w1 + w2)를 써서 테스트베드에서 DMS 경유 nsync·2노드 dsync 가 서지 않았다(이전 시도 2건 같음). 상한 공식은 launcher 100m 를 노드마다
    빼지만 DMS 밖 요청이 예비 500m + 내림 여유(이 노드 200m)를 넘은 것이 원인이다. 예비를 701m 이상으로 올리면 상한이 0 이 되어 즉시
    `node_cpu_insufficient` 로 거부된다(멈춘 Pending 보다 낫지만 w1 을 쓸 수 없다). 실증 때는 되돌림 입구 ingress-nginx 를 줄여 100m 를 확보한다
    (위 「sync 진행률 후속」의 라이브 실증).
- 📝 **워커 CPU 요청으로 배치 미리보기가 Volcano 에서 줄을 선다 — 먼저 끝난 형제의 미리보기 TTL(24h) 위험**(2026-10-16). 잡이 노드 코어를
  예약하므로 배치의 미리보기 자식들이 노드 단위로 줄을 서고, Previewing 배치는 미리보기 단계 자식이 하나라도 남으면 PreviewReady 로 넘어가지
  않는다(`batch_orchestrator` 미리보기 집계) — 그동안 먼저 끝난 형제의 `preview_ttl`(24h, 미리보기 성공부터)이 흘러 `preview_expired` 로 Rejected 될 수
  있다. Running 배치에서 슬롯을 기다리는 확인된 자식도 같다. planner 는 자식을 DMS 예약이 적은 노드로 흩지만(`placement._pick_nodes`), 적격 노드들이
  동시에 담는 자식 수(≈ 노드 수 × 노드 상한 ÷ (자식의 노드 수 × 노드당 프로세스))를 넘는 max_concurrency 는 그만큼 줄을 세운다 — 큰 배치는
  max_concurrency 를 그 수 이하로 두거나 나눠 내라(예: 테스트베드 노드 3·상한 1·자식당 1노드×1프로세스면 3). 근본 해법(배치 안 TTL 시작을 「배치
  PreviewReady」로 미루기 등)은 결정 대기.

---

## 5. 프로세스 규약·기록 (지속)

- **파킹은 이 파일에** — SDD 레저(`.superpowers/sdd/`)는 `.gitignore = *` 라 git 에
  안 들어간다(슬라이스 11·12·13 레저 소실). 워크트리와 함께 사라지므로 파킹은 여기 적는다.
- **워크트리 공유 중 커밋은 pathspec 으로** — `git add` 로 인덱스를 거치면 같은 워크트리의
  다른 세션 커밋에 내 파일이 섞이거나 남의 파일을 삼킨다(2026-08-11 실제 발생: 슬라이스 20
  Task 2 의 5파일이 커밋 `6bc2ecb` 에 통째로 흡수 — 코드 손실 없이 히스토리만 섞임).
  **항상 `git commit -- <경로들>`.** (새 파일은 `git add -- <경로>` 로 그 파일만 올린 뒤 pathspec 커밋.)
- **포탈 빌드는 빌드 노드의 로컬 체크아웃을 그대로 빌드한다**(슬라이스 33, `build_manifests` — hostPath ro 를 시작 시점에 tar 스냅숏) — push 는
  필요 없고, 빌드 전에 그 체크아웃을 원하는 커밋으로 맞춘다. 빌드 기록의 SHA 는 마운트에서 읽는다(`-dirty` = 커밋 안 된 변경이 있었다; git
  worktree 경로면 `.git` 파일이 마운트 밖을 가리켜 `unknown` — 로컬 체크아웃을 쓴다, deploy/README §8 0)).
- **각 항목은 근거를 `모듈.심볼`로** 남긴다(줄 번호는 흐른다). 의도적 보류는 "미구현"이 아니라 **결정**이므로 ⏸ 로 표시한다.
- **해소하면 지운다** — 완료 기록은 CHANGELOG 가 맡는다. 다른 파일(코드·테스트 주석·문서)이 「제목」으로 인용하는 항목만 한 줄 묘비로
  남기고, 지우기 전에 `git grep -F '<제목>'` 으로 인용을 확인한다. 이 파일의 「## 1.」~「## 5.」·「③~⑦ … 후속」 제목은 코드·출력이 가리키므로
  바꾸지 않는다.
