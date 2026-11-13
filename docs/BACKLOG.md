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
k8s 신원 모드 → 포크 → 사용량 → ③~⑦ 설치 묶음 → 테스트베드).

- ~~**아티팩트 보존 정책(자동 만료)**~~ — 해소(2026-10-26, CHANGELOG 「BACKLOG 결정 묶음 구현」 — controller `artifact-expiry` 루프,
  `DMS_ARTIFACT_RETENTION_DAYS` 기본 1500·0 = 끔, 열람은 410 `artifact_expired`, 운영은 deploy/README §12b). 남은 후속:
  - 🔧 **만료된 scan 리포트를 읽으려는 화면** — 사용자 스캔 경로 통계(`routes_scan_paths`)는 만료된 scan 잡에도 읽기 예산
    (`_MAX_READ_ATTEMPTS`)을 쓰고, 사용량 분석(`routes_usage`)은 요약 캐시(`scan_report_digests`)에 없는 만료 리포트를 매번
    읽으려다 「못 읽음」으로 센다(ENOENT 라 싸다; 이미 캐시된 이력은 그대로 보인다). `artifacts_expired_at` 이 있으면 건너뛰기를
    권장. 관리자 scan-stats 는 만료 뒤 자연스럽게 404 `no_scan_report`.
- 🔧 **작업(요청) 삭제 후속**(2026-10-08 범위 밖 — 운영은 deploy/README §12):
  - **작업 2000개(또는 항목 10000개)를 넘는 배치의 삭제**(2026-10-26 사용자 결정 「큰 배치 삭제 숫자를 늘려줘」로 자식 상한
    1000 → 2000) — 배치 하나 = 트랜잭션 하나이고 그동안 api 전체가 DB RLock 으로 멈춰(replicas 1·커넥션 1) 상한
    (`repositories.request_purges.MAX_BATCH_DELETE_CHILDREN = 2000`·`MAX_BATCH_DELETE_ITEMS = 10000`)을 넘는 배치는
    `batch_delete_too_large` 로 거부한다(최대 배치 하나가 RLock 을 ~0.7–2초 쥐어 readiness 한 번을 놓칠 수 있다 — 연속 3회가
    아니면 파드는 빠지지 않는다; 목록 요약도 자식 수를 2001 에서 멈춘다). 운영 배치 규모(항목 수 × 재스캔 횟수) 확인 필요. 후보:
    묘비 상태 `Deleting`(배치를 먼저 그 상태로 CAS — 재실행·항목 추가가 되살리지 못하게) + 자식 청크 트랜잭션 + 마지막에 배치 행
    삭제, 재개 의미(중간에 api 가 죽으면 다음 호출이 이어서). 함께: `:rescan` 의 무조건 `set_status`(`api.routes_batches.rescan_batch`)를
    상태 CAS 로(지금은 삭제 직후 거짓 200 이 가능 — `update_meta` 도 같은 성질), 포탈 상태 라벨.
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
    2000개(상한)면 ~1시간, 한 호출의 최대(배치 10 × 2000)는 9~10시간이고 그동안 다른 삭제의 정리도 같은 줄(재시도 시각 순)에
    선다(결과 파일 만료 행도 같은 줄이지만 대기 50행 상한 — `artifact_expiry.BACKLOG_MAX`). 순서를 바꿔도(스윕한 행 먼저) 총
    스윕 수가 같아 총 시간은 그대로다 — 줄이려면 상한을 올리거나(50, 틱 예산 `TICK_BUDGET_SECONDS` 안에서 실 apiserver 지연
    실측 후) 같은 틱 끝에 방금 스윕한 행을 한 번 더 세는 것.
  - **정리 루프 틱 비용이 대기열 크기에 비례**(2026-10-11 검증 지적, 일부 해소) — purge 파드를 못 띄우는 동안(purge_no_node
    등) 큰 배치 삭제의 행이 purging 에 쌓이면 전역 단계(`_reap`·`_settle` 의 `_live_rows`)가 틱마다 두 번 purging·files 행
    **전부**를 읽고, `_complete` 가 purging 행마다 FS stat(`artifact_trash.entry_states`)을 한다. 이 시간은
    `TICK_BUDGET_SECONDS` 에 들지 않고, 컨트롤러는 루프를 한 프로세스에서 차례로 돌려 planner·stepper 틱까지 밀린다.
    원 행 재등장 확인은 묶음 한 번으로 바꿨다(`present_targets` — 로컬 PG 1만 행 1.3초 → 0.37초). 남은 선형 비용:
    `in_stage` 전량 읽기 + 행마다 FS stat(공유 FS 에선 로컬보다 몇 배 느릴 것). 후보: 백오프 중인 행(next_attempt_at > now)은
    `_complete` 를 건너뛰기, 틱당 완료 판정 행 수 상한(오래된 것부터 돌림차례), 또는 전역 단계도 틱 예산에 넣기.
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
  (COUNT) 응답.
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
   Avi 뒤 Envoy 의 TLS 핸드셰이크가 실패한다. 지금은 정기 작업 달력(deploy/README §14-2)뿐. 처방: S12 WARN(만료 30일 전) + 포탈 개요 경고.
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
    파일째 걷는다 — ⑥ 「소크 뒤 Ingress·MetalLB 삭제」와 한 묶음.
11. 🔧 **dms-api·dms-controller requests 결정** — base `40-api.yaml`·`41-controller.yaml`(렌더 그대로)의 requests 는 100m/256Mi(limits
    1/1Gi)이고 설계 v6 2-1 표의 dms-api 250m/512Mi 와 다르다. 마스터 자원 예산(deploy/README §0-2)은 렌더 값을 인용한다. 실측(로그인
    scrypt 슬롯·큰 배치 삭제의 RLock·사용량 분석 창)으로 정한다.
12. 🔒 (테스트베드) **testbed 저장소 후속**(`~/dms-dev/testbed` — 이 저장소의 세션은 고치지 않는다) — 옛 deploy/README 절 번호를
    가리키는 곳을 [`deploy/testbed/README.md`](../deploy/testbed/README.md)(부록 Z 대응표 — deploy/README)로: README `§13-2`·「Shared-FS
    layout: … (DMS README §2)」·dmsk8s DB URL(pkg-01 `dmsdb_k8s` — 낡음: T7 뒤 앱 DB 는 CNPG `dms-pg`, pkg-01 URL 은 `dms-secrets-prev` 의
    되돌림 값), `docs/ARCHITECTURE.md` 의 `§2g-7`·`§13-6b`·`§2e-6` 참조와 §17 의 같은 DB URL·「아래 절 번호는 dms-ssc 판 기준」 문장,
    `group_vars/dmsk8s.yml` 의 `2g-7` 주석. 그 저장소의 §2e·§2g 계열과 §1·§2·§3/§3b·§4/§5·§6·§8·§10-7 참조는 같은 주제의 일반판 절에
    떨어지므로 그대로 둬도 된다(deploy/README 머리의 번호 정책).
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
      기다린다(§4 「노드 CPU 상한은 다른 파드의 requests 를 보지 않는다」). **사용자 지시로 손대지 않는다 — 기록만**(2026-11-05).
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

CHANGELOG 「⑥ 입구(Envoy Gateway)」, 설계 v6 #13~#15·4-3·8-1(S0-5·S8·S12)·8-2·10 T8, deploy/README §2g.

- 📝 **소크 뒤 Ingress·MetalLB 삭제** — testbed-k8s 오버레이의 Ingress·MetalLB(되돌림용, testbed `portal_edge: ingress`)를 지운다 — 그때
  testbed IaC 가 dmsk8s 의 ingress-nginx·MetalLB 를 걷고 되돌림 경로가 없어진다(deploy/testbed/README.md §7). 같은 묶음: base
  `46-ingress.yaml`·`47-metallb-public-pool.yaml` 파일째 삭제 + prod `patch-remove-ingress.yaml`·`tests/test_testbed_k8s_overlay.py` 단언 정리
  (2026-11-06 신규 10).
- 📝 **EG 리스너에 hostname 이 없어 접근 로그에 SNI 가 없다** — `%REQUESTED_SERVER_NAME%` 는 늘 null 이라 필드를 지웠다(3fd7da9).
  리스너에 hostname 을 두게 되면(지금은 hostname·80·HSTS 없음 — deploy/README §2g-1) 그때 다시 싣는다.
- 📝 **설계 ⑥ 의 선택·뒤로 미룬 것** — Envoy 접근 로그 수집처·보존(K15·보안 S2 — 지금은 stdout), Envoy stats(:19001)는 끔(bd53e9b, EnvoyProxy
  `telemetry.metrics.prometheus.disable`; 지표가 필요해지면 노드 밖을 막는 방화벽과 함께 다시 켠다), C 경로에서 남의 EG 설정이 shutdown-manager
  이미지를 docker.io 로 두어도 DMS EnvoyProxy 가 이미지를 명시하므로 영향 없음(확인만), 전역 rate limit(EG `rateLimit`) 미사용 — 쓰게 되면
  ratelimit 이미지를 lock 에. `platform/gateway`·`addons/gateway-api` 는 워크로드가 없어 `guard-images.sh` 대상이 아니다(Envoy 이미지 교체는
  렌더 diff 로 본다).

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
  EOL·범위 밖 → reuse + WARN·소유자 요청 문구)는 심 테스트(`tests/test_install_detect.py` — CNPG·EG 버전 표)로만 봤다. 실클러스터에서 볼 일이
  생기면(예: 다음 CNPG·EG 업그레이드 리허설에서 옛 판으로 되돌린 테스트베드, EG v1.8 을 깐 일회용 클러스터) `--plan` 표·S8/S9 의 「건드리지
  않는다」를 확인한다.
- 📝 **설치 흐름 밖에 남긴 사이트 확인** — `SHARED_DIR_ID` 를 values.env 에 손으로 옮긴다(S0 결과를 일회용 파드로 읽어 제안·대조하는 길은 없다 —
  틀리면 S4 가 「센티널 불일치」로 멈춘다), SHARED_DIR 변경 때 레지스트리 저장소 이동은 deploy/README §2e-5 손 절차(설치기는 S4·S11 가드 exit 5 로
  멈출 뿐), 클러스터 도메인 ≠ cluster.local 은 preflight WARN 뿐(EG `KUBERNETES_CLUSTER_DOMAIN` values 키 없음 — 설치기는 매 실행 다시 렌더하므로
  `deploy/.rendered` 손 패치로는 못 넘는다; 렌더를 건너뛰는 길은 두지 않았다 — 값 키를 더하는 쪽), 마스터 리스너 포트(`GATEWAY_LISTEN_PORT`,
  기본 :443)·:19000~19003 비어 있음(K6)·`crictl inspecti … pinned`·`fs.protected_*` 는 노드 셸 체크리스트(MANUAL)뿐, S12 의 413(21MiB)·15초 스트림·
  원 IP 는 deploy/README §2g-5 손 명령.
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
- 📝 (선택) **testbed-k8s `values.env` 에 `PORTAL_DOMAIN=dms-k8s.local`·`PORTAL_VIP=10.10.10.1`** — 설치기 S12·`verify.sh` 의 VIP 경유 readyz 가
  edge 경로를 보게(지금은 env 를 줄 때만 본다). 렌더·설치기 테스트 영향 확인 뒤.
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

## 2. 의도적 보류 (결정 — "미구현"이 아님)

- ⏸ **Prometheus/Grafana** — 포탈 대시보드로 대체(Envoy stats 도 꺼 둔다 — deploy/README §2g-2).
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

- 📝 **파일 크기 분포에 구간별 용량이 없다**(2026-10-21, d183). dscan(포크 1b93d54)의 `file_size_histogram` 은 구간별 **개수**만
  쓴다(시간 히스토그램만 bytes). 그래서 사용량 화면은 개수·비중만 보인다 — 「1 GiB 넘는 파일이 용량의 몇 %」는 못 말한다. 해법:
  포크 `acc_emit_entry` 에 `size_bytes[bin] += size` 와 JSON `"bytes"` 키 → 잡 이미지·에이전트 재빌드. 투영·캐시·CSV·
  `SizeDistribution` 은 bytes 가 모든 구간에 있으면 자동으로 용량 열을 붙이게 이미 돼 있다.
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
  - 앞단 계약(Envoy 가 X-Real-IP 를 지우고 PROXY 원 IP 를 XFF 끝에 덧붙임 — ⑥ 입구 묶음)이 깨지면 **기록된 IP 가 틀린다**. 막는 판정은 틀리지
    않는다.
  - 프록시 없이 직접 닿는 배치(port-forward)는 XFF 를 클라이언트가 심을 수 있다. 위조 IP 로 출발지 경보를 부풀려도 이벤트는 창당 50건까지이고
    (그 뒤 출발지는 기록 안 됨 — 전체 실패 경보가 규모를 요약), 정작 진짜 출발지 경보가 그 몫에 밀려 빠질 수 있다.
  - `GATEWAY_PROXY_PROTOCOL=off` 사이트(2026-11-14 — LB 가 PROXY v2 를 못 붙인다, deploy/README §2g-4)에는 그 계약이 없어 앱이 사용자 원 IP 를
    모른다. 접근 로그·보안 이벤트의 `client_ip` 는 LB(SE) 주소이고, 출발지 경보(`DMS_LOGIN_ALARM_SOURCE_FAILURES`)는 prod 렌더가 0(끔)으로
    넣는다(`deploy/render.sh` — 고정 오버레이는 patch-config.yaml 에 직접 넣고, 없으면 렌더가 note 로 알린다). 전체 실패 경보와 아이디 기준
    상한·거절은 그대로라 막는 판정은 바뀌지 않는다. 원 IP 는 LB 의 VS 로그에서 시각으로 맞출 뿐이라, 출발지 단위 조사가 필요하면 LB 로그 보존을
    사이트에 요청한다 — 근본 해결은 PROXY v2 를 붙이는 LB(deploy/README §2g-4 의 컷오버)다.
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
