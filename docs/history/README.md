# docs/history — 동결된 역사 (편집 금지)

여기는 DMS 를 지은 과정의 **역사적 근거**다. 설계문서(`specs/` 29편)와, 2026-11-06 에 CHANGELOG 에서
옮겨 온 옛 변경 이력 두 편이 들어 있다.

## 규칙

- **편집하지 마라.** 이 문서들은 작성 시점의 사실이고, 그대로 둔다. 오타·라인 드리프트도
  고치지 않는다 — 역사는 당시 그대로가 정확한 기록이다. (이 README 만 예외다 — 색인이라서
  새 이관·대체가 생기면 고친다.)
- **현재 동작을 여기서 찾지 마라.** 시스템이 지금 어떻게 도는지는
  [`../ARCHITECTURE.md`](../ARCHITECTURE.md)와 코드의 「왜」 주석이 정의한다.
  이 설계문서들은 "그때 왜 그렇게 하기로 했나"의 근거일 뿐, 이후 작업이 전제를
  바꿨을 수 있다. 무엇이 바뀌었는지는 아래 「설계 색인」의 「바뀐 곳」을 본다.
- 문서 안의 경로 참조(`docs/superpowers/...` 등)·절 번호·태그는 작성 당시의 것이다. 현재 위치와
  다를 수 있으나 고치지 않는다(동결).

## 무엇이 있나

- `specs/` — 설계(design) 29편: 원본 마스터 설계(clean-slate) 1, 슬라이스 1~26 설계 26, dms-k8s 설계 2
  (2026-10-25 RBAC·PV v2, 2026-11-03 프로덕션 v6). 각 문서의 값어치:
  - **「실측으로 확인한 전제」** — 그 슬라이스가 코드를 읽고 확인한 사실(슬라이스 11~26 설계에 있다)
  - **「핵심 결정」·「결정 기록」** — 무엇을 왜 그렇게 하기로 했나(슬라이스 1~10 은 「결정 기록」 절)
  - **「이 슬라이스에서 하지 않는 것」·「비목표」** — 의도적으로 자른 범위(왜 지금의 시스템이 그런지)
- [`changelog-2026-09-to-10-21.md`](changelog-2026-09-to-10-21.md) — 2026-09-07 ~ 10-21(ssc 태그 d119~d184)
  완료 기록. 원본 `docs/CHANGELOG.md` @5bec2e69 L2072–4083 + L366–406 을 바이트 그대로 옮겼다(머리 3줄에 줄 범위).
- [`changelog-2026-08-slices.md`](changelog-2026-08-slices.md) — 2026-08 슬라이스(Phase 1~3·슬라이스 1~35)의 옛
  빠른 인덱스와 상세 기록. 원본 L13–56 + L4085–4998 을 바이트 그대로 옮겼다.
- 그 뒤(2026-10-21 테스트베드 분리 이후)의 기록은 [`../CHANGELOG.md`](../CHANGELOG.md)에 있다. 거기 「옛 기록」 절에
  이관본 어디에도 항목이 없는 슬라이스 32·36·37 을 git 이력으로 보충해 두었다.

## 설계 색인 — 지금도 유효한가, 무엇이 대체했나

상태(판정 = 2026-11-06 문서 감사):

- **a** — 지금 코드의 근거로 유효하다. 이후 작업이 넓혔을 수는 있어도 결정을 뒤집지 않았다.
- **a\*** — 대체로 유효하나 일부 결정이 이후 작업으로 바뀌었다. 바뀐 부분은 「바뀐 곳」에 적었다.
- **b** — 상당 부분이 이후 작업으로 대체됐다. 역사로만 읽는다.

「완료 기록」·「바뀐 곳」의 「…」는 CHANGELOG 항목 제목(앞부분)이다. **08** = `changelog-2026-08-slices.md`,
**09** = `changelog-2026-09-to-10-21.md`, **현행** = `../CHANGELOG.md`. 「08 인덱스 #N」은 08 의 옛 「빠른 인덱스」 표의 N 행이다.

| 설계 (`specs/`) | 주제 | 상태 | 완료 기록 | 바뀐 곳 (a\*·b) |
|---|---|---|---|---|
| `2026-08-02-dms-clean-slate-design.md` | 원본 마스터 설계 | b | 08 인덱스 「Phase 1~3」 | 원래 계획이지 지어진 시스템이 아니다(2026-08-05 이후 갱신 없음). 지금 구조는 ARCHITECTURE.md, 신원·권한은 `2026-10-25-dms-k8s-rbac-pv-design.md` + 현행 「LDAP 코드 삭제·정리(dms-k8s)」(k8s 신원 모드 전용), 배포·설치는 v6 + 현행 「① B3」~「⑦ 설치기」. 코드 주석의 「상위 스펙 §N」·「스펙 §4·§5·§8」은 이 문서다 |
| `2026-08-04-dms-portal-thin-slice-design.md` | 슬라이스 1 — 포탈 첫 얇은 전체 슬라이스 | a\* | 08 인덱스 #1~2 | 디자인 시스템(C / Soft SaaS) → 08 「✅ 슬라이스 31 «포탈 디자인 개편 — DS Cloud 스타일»」. 로그인 → 09 「웹 인증 하드닝: 로그인 감속 + 비밀번호 전송 봉인」·현행 「② 로그인·식별」 |
| `2026-08-04-dms-portal-batch-slice2-design.md` | 슬라이스 2 — 배치(대량 묶음) | a | 08 인덱스 #1~2 | — |
| `2026-08-05-dms-portal-storage-mgmt-slice3-design.md` | 슬라이스 3 — 스토리지 관리·감사 로그 | a | 08 인덱스 #3 | — |
| `2026-08-05-dms-portal-ops-console-slice4-design.md` | 슬라이스 4 — 정책·denylist·컨트롤 상태 | a\* | 08 인덱스 #4 | 컨트롤 상태 「현재 상태」·변경 이력 → 슬라이스 36(현행 「옛 기록」 보충 표). denylist 그룹 규칙 → 현행 「dms-k8s RBAC·PV」(k8s 모드는 그룹 denylist 를 평가하지 않는다 — 추가는 422 `denylist_group_unsupported`) |
| `2026-08-05-dms-portal-job-observability-slice5-design.md` | 슬라이스 5 — 잡 관측성(로그·아티팩트·잡 상세) | a\* | 08 인덱스 #5 | vcjob 로그 열람(이 설계에선 범위 밖 — 409 `log_not_available`) → 08 「✅ 슬라이스 25 «실행 단계 진단»」. 아티팩트 다운로드 → 08 「✅ 슬라이스 26 «포탈 기능 잔여»」 |
| `2026-08-05-dms-portal-submit-surface-slice6-design.md` | 슬라이스 6 — rm·scan 제출·스토리지 선택·특권 제출 | b | 08 인덱스 #6 | scan 제출 화면 → 슬라이스 37(단일 작업으로 흡수 — 현행 「옛 기록」 보충 표). 특권 제출 → 09 「sync 가 목적지 소유권을 바꾸던 사고 — root 실행 명시 opt-in + 목적지 권한 preflight」, 현행 「dms-k8s RBAC·PV」(모든 잡 root + RoleBinding 맵핑, 일반 사용자는 PV 주소만) |
| `2026-08-05-dms-cancel-timeout-slice7-design.md` | 슬라이스 7 — 취소·타임아웃 정합성 | a\* | 08 인덱스 #7 | 취소 → 09 「컨펌 대기·대기 중 작업 취소 버튼 + 취소 경합」·「배치 취소 뒤 남는 launcher/worker 파드」. 시한 초과 판정 → 현행 「라이브 기능 전수 점검(2026-11-04) + 확정 결함 5건 수정」(vcjob Aborted 의 TimedOut 분류·사전 점검 고정 시한) |
| `2026-08-05-dms-user-scan-paths-slice8-design.md` | 슬라이스 8 — 사용자 scan 경로·서브트리 통계 | b | 08 인덱스 #8 | 포탈 「내 스캔 경로」 화면·라우트 → 슬라이스 37 에서 제거(현행 「옛 기록」 보충 표). scan 은 특권 운영자만 → 현행 「dms-k8s 결정 항목 반영」. 백엔드 `/api/user/scan-paths`(`dms.api.routes_scan_paths`)는 남아 있다 |
| `2026-08-06-dms-admin-accounts-nodes-slice9-design.md` | 슬라이스 9 — 계정 관리·노드 대시보드 | a | 08 인덱스 #9 | — |
| `2026-08-06-dms-ops-hardening-slice10-design.md` | 슬라이스 10 — 운영 안정화 묶음 | a | 08 인덱스 #10 | — |
| `2026-08-06-dms-portal-image-build-slice11-design.md` | 슬라이스 11 — 포탈 주도 이미지 빌드 | a\* | 08 인덱스 #11 | 빌드 노드 → 08 「✅ 슬라이스 21 «포탈 빌드 되살리기»」. GitHub clone → 08 「✅ 슬라이스 33: 로컬 소스 빌드」. 프록시 → 09 「빌드 프록시 + 신규 사이트 태그 누출 차단」·「사내 프록시 CA(TLS 가로채기 프록시)」. 레지스트리 → 현행 「④ 클러스터 내 레지스트리」 |
| `2026-08-06-dms-portal-hygiene-slice12-design.md` | 슬라이스 12 — 포탈 위생·진단 가능성 | a | 08 인덱스 #12 | — |
| `2026-08-06-dms-portal-rollout-slice13-design.md` | 슬라이스 13 — 포탈 주도 롤아웃 | a | 08 인덱스 #13 | — |
| `2026-08-09-dms-monitoring-dashboard-slice14-design.md` | 슬라이스 14 — 모니터링 대시보드 | a | 08 인덱스 #14 | — |
| `2026-08-10-dms-runner-counts-slice15-design.md` | 슬라이스 15 — 러너 files/bytes 파싱 | a | 08 인덱스 #15 | — |
| `2026-08-10-dms-deploy-safety-slice16-design.md` | 슬라이스 16 — 배포 안전망 | a\* | 08 「✅ 슬라이스 16 «배포 안전망»」 | 드리프트 판정 → 08 「✅ 슬라이스 34: 드리프트 방지 + 이미지·이력 관리」(빌드 때 매니페스트 태그 스탬프), 09 「base 매니페스트 사이트 중립화 + 오버레이 apply 이미지 가드」(동봉값이 자리표시자면 배지 「모름」) |
| `2026-08-10-dms-queue-visibility-slice17-design.md` | 슬라이스 17 — 큐 가시성 | a | 08 「✅ 슬라이스 17 «큐 가시성»」 | — |
| `2026-08-10-dms-artifact-base-slice18-design.md` | 슬라이스 18 — 아티팩트 경로 설정 | a | 08 「✅ 슬라이스 18 «아티팩트 경로 설정»」 | — |
| `2026-08-10-dms-account-hygiene-slice19-design.md` | 슬라이스 19 — 계정 위생 | a | 08 「✅ 슬라이스 19 «계정 위생»」 | — |
| `2026-08-10-dms-sched-wait-slice20-design.md` | 슬라이스 20 — Volcano 대기 이력 | a | 08 「✅ 슬라이스 20 «Volcano 대기 이력»」 | — |
| `2026-08-11-dms-portal-build-slice21-design.md` | 슬라이스 21 — 포탈 빌드 되살리기 | a | 08 「✅ 슬라이스 21 «포탈 빌드 되살리기»」 | — |
| `2026-08-11-dms-db-reconnect-slice22-design.md` | 슬라이스 22 — DB 커넥션 재연결 | a | 08 「✅ 슬라이스 22 «DB 커넥션 재연결»」 | — |
| `2026-08-11-dms-portal-e2e-slice23-design.md` | 슬라이스 23 — 포탈 e2e | a | 08 「✅ 슬라이스 23 «포탈 e2e 테스트»」 | — |
| `2026-08-11-dms-destructive-failopen-slice24-design.md` | 슬라이스 24 — 파괴적 경로 fail-open 봉인 | a | 08 「✅ 슬라이스 24 «파괴적 경로 fail-open 봉인»」 | — |
| `2026-08-11-dms-exec-diagnostics-slice25-design.md` | 슬라이스 25 — 실행 단계 진단 | a | 08 「✅ 슬라이스 25 «실행 단계 진단»」 | — |
| `2026-08-11-dms-portal-features-slice26-design.md` | 슬라이스 26 — 포탈 기능 잔여 | a | 08 「✅ 슬라이스 26 «포탈 기능 잔여»」 | — |
| `2026-10-25-dms-k8s-rbac-pv-design.md` | dms-k8s RBAC·PV 권한과 PV 스토리지(v2) | a\* | 현행 「dms-k8s RBAC·PV」 | D1 신원 모드 스위치(`DMS_IDENTITY_MODE ∈ {ldap, k8s}`, 코드 기본 ldap) → 현행 「LDAP 코드 삭제·정리(dms-k8s)」(k8s 신원 모드 전용 — 스위치는 기동 거부 안전장치로만 남음). §10 「사용자 결정 대기」 → 현행 「dms-k8s 결정 항목 반영」. v1 은 git 이력(커밋 7d0712c4) |
| `2026-11-03-dms-k8s-prod-design.md` | dms-k8s 프로덕션 설계 v6 | a | 현행 「① B3」~「⑦ 설치기」 | 아래 「v6 를 읽을 때」 |

### v6 를 읽을 때

- v6 는 **구현 기준선**이다 — 묶음 ①~⑦ 이 이 문서를 따라 구현·실증됐다(현행 CHANGELOG 의 ①~⑦ 항목).
- **§11 「남은 확인 요청 (운영자 정보)」 표는 설치 전 미결**이다 — 사이트마다 설치 전에 정한다. 지금 그 값을 정하는 절차는
  [`../../deploy/README.md`](../../deploy/README.md) §0 과 [`../../deploy/overlays/prod/README.md`](../../deploy/overlays/prod/README.md)다.
- v1~v5 는 저장소 밖(세션 작업 사본)에만 있었다. 이 저장소에는 v6 만 있다(커밋 d6435507).
- 제목의 「(최종본, 논의용)」은 낡았다(구현이 끝났다). 파일은 동결이라 고치지 않고 여기서 정정한다.
- 구현이 설계와 다른 점은 현행 CHANGELOG 「⑦ 설치기」의 「설계와 다른 점」에 있다. 그 밖의 차이는 코드가 진실이다
  (CLAUDE.md 드리프트 규칙).

## 저장소에 없는 설계 — 코드가 가리키지만 파일이 없다

세션 작업 사본으로만 쓰이고 커밋되지 않은 설계를 코드 주석이 이름으로 가리키는 곳이 있다(2026-11-06 HEAD 기준 28줄).
파일을 찾지 말고 아래 「대신 볼 곳」을 본다.

| 코드가 부르는 이름 | 가리키는 곳 | 대신 볼 곳 |
|---|---|---|
| 「d192 spec §1·§2」(2026-10-27 — summary v2·도구 결과 사유) | `src/dms_job_runner/runner.py`·`parsers.py`, `tests/test_job_runner_tool_detail.py`, `frontend/src/features/jobs/StageResults.test.tsx` — 8줄 | 현행 「dms-ssc 이식」(d192 부분), `dms_job_runner.runner._build_summary`·`_derive_reason` docstring, dms-ssc 라인 CHANGELOG 「실패 사유를 도구 출력에서 정확히 · summary v2 · …」(`git show origin/dms-ssc:docs/CHANGELOG.md`) |
| 「작업 삭제 스펙 §6.1·§6.2·§9」(2026-10-08) | `tests/test_planner.py`, `tests/test_stepper_deleted_job.py`, `frontend/src/features/jobs/DeleteRequestsDialog.tsx` — 3줄 | 09 「작업(요청) 선택 삭제 — DB·결과 파일·파드·로그 일관 삭제」·「전체 작업: 배치 단위 삭제 + 「배치」 열」, `src/dms/repositories/request_purges.py` docstring, deploy/README §12 |
| 「요청 상세 스펙」(§4.9·§5.2·§9·§11.1·C1~C10, 2026-10-08 재설계) | `frontend/src/features/jobs/` 의 ConfirmDialog·JobCard·JobStages(·test)·Timeline·requestOutcome·stageModel·useJobs·RequestDetail.redesign.test — 16줄 | 09 「요청 상세: 단계 분리(사전 점검·미리보기 / 실행) + 결과 배너」·「요청 상세: 요청 내용 카드(옵션·실행 권한·실행 신원·자원) + root 머리 배지」 |
| 「심사 스펙 §7」(사용량 분석 필터 묶음이 하지 않은 것) | `docs/BACKLOG.md` 의 사용량 분석 항목 — 1줄 | 현행 「사용량 분석 필터」 |

코드 주석의 「상위 스펙 §N」·「스펙 §4·§5·§8」(약 18줄)은 저장소에 **있다** — `specs/2026-08-02-dms-clean-slate-design.md`.

## 어디로 갔나 (트리에서 지운 것의 복구)

- **구현 플랜(TDD 스크립트)** — 일회성이라 트리에서 지웠다. git 이력에 영구 보존돼 있다.
  - `docs/superpowers/plans/`(Phase 1~3·슬라이스 1~30 플랜 36편) — 커밋 d345cf60(2026-08-13 문서 재편)에서 지웠다.
    이력: `git log --all --oneline -- docs/superpowers/plans/`, 목록: `git ls-tree --name-only d345cf60^ docs/superpowers/plans/`,
    본문: `git show d345cf60^:docs/superpowers/plans/<파일>`.
  - `docs/plans/`(2026-08-13 슬라이스 31·32 플랜 2편) — 커밋 07911aca 에서 지웠다.
    이력: `git log --all --oneline -- docs/plans/`, 본문: `git show 07911aca^:docs/plans/<파일>`.
- **완료 기록** — 2026-10-21 이후는 [`../CHANGELOG.md`](../CHANGELOG.md), 그 전은 이 디렉터리의 이관본 두 편.
- **2026-10-28 DMS-SSC → DMS-k8s 인계 문서**(`handoff-2026-10-28-dms-k8s.md`)는 이 브랜치에 없고 `origin/dms-ssc` 에만 있다 —
  `git show origin/dms-ssc:docs/history/handoff-2026-10-28-dms-k8s.md`. 코드·CHANGELOG 의 「인계 문서 §N」은 이 문서다.
