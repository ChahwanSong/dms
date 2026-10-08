# DMS 변경 이력 (슬라이스별 빌드 기록)

DMS 를 clean-slate 로 지은 과정의 **완료 기록**이다. 각 슬라이스가 무엇을 만들었고
어떤 실증을 통과했는지, 그리고 구현 중 잡은 플랜 결함·교훈을 담는다.

- **지금 시스템이 어떻게 동작하는가**는 여기가 아니라 [`ARCHITECTURE.md`](ARCHITECTURE.md)
  와 코드의 「왜」 주석을 봐라. 이 파일은 "무엇을·언제·왜 그렇게 지었나"의 역사다.
- **왜 그렇게 설계했나**의 원문 근거는 동결된 설계문서 [`docs/history/specs/`](history/specs/)에
  있다(슬라이스별 design). 구현 플랜(TDD 스크립트)은 일회성이라 트리에서 지웠고 git
  이력에만 남는다.
- **남은 일**은 [`BACKLOG.md`](BACKLOG.md).

## 빠른 인덱스

배포 태그는 제어면 `dms:dNN` 기준이다(에이전트·잡 러너는 바뀐 슬라이스에서만 함께 오른다).

| # | 슬라이스 | 태그 | 한 줄 |
|---|---|---|---|
| — | **Phase 1~3**(기반) | d23~ | core backend · agent/controller · planner · stepper · live-adapters · job-lifecycle |
| 1~2 | 포탈 thin slice · 배치 | | 포탈 골격, 요청/배치 제출 |
| 3 | 스토리지 관리 | | 스토리지 CRUD·리컨실 |
| 4 | 운영 콘솔 | | ops 화면 |
| 5 | 잡 관측성 | | 잡 상태·아티팩트 뷰(실행 로그는 슬라이스 25 까지 409) |
| 6 | 제출 표면 | | scan/sync/rm 제출 폼 |
| 7 | 취소·타임아웃 | | 잡 취소·정책 타임아웃 |
| 8 | 사용자 스캔 경로 | | user scan paths |
| 9 | 관리자 계정·노드 | | 계정·노드 관리 |
| 10 | 운영 강화 | | pod GC·리텐션 |
| 11 | 포탈 이미지 빌드 | | 포탈 주도 빌드(테스트베드에선 구조적 제약 — §21 에서 되살림) |
| 12 | 포탈 위생 | | 이벤트 리텐션 등 |
| 13 | 포탈 롤아웃 | | 포탈 주도 롤아웃 |
| 14 | 모니터링 대시보드 | | 노드 메트릭·Sparkline |
| 15 | 러너 카운트 | d27 | 네 도구(dscan·dsync·nsync·drm) 카운트 파서 |
| 16 | 배포 안전망 | d26 | 매니페스트 드리프트 배지·migrate 락 |
| 17 | 큐 가시성 | d28 | 대기 이력·커버링 인덱스 |
| 18 | 아티팩트 경로 설정 | d29 | DB 가 env 를 이기는 artifact base |
| 19 | 계정 위생 | d30/d31 | 토큰 actor 제한·fail-closed 신원 |
| 20 | Volcano 대기 이력 | d32 | sched_wait 계측 |
| 21 | 포탈 빌드 되살리기 | d33 | 빌드 노드 적합성 프리플라이트 |
| 22 | DB 커넥션 재연결 | d34 | 죽은 커넥션 재연결·readyz 자기종료·connect_timeout |
| 23 | 포탈 e2e | (무관) | Playwright E1~E6, 기하·세션·SPA fallback·풀스택 부팅 |
| 24 | 파괴적 fail-open 봉인 | d35 | 미지 도구 3층 fail-closed·`/` 스토리지 거부·고아 스윕 격리 |
| 25 | 실행 단계 진단 | d36 | vcjob 로그 개방 + 실패 종단 시 `diag_logs` 박제 |
| 26 | 포탈 기능 잔여 | d37 | 아티팩트 다운로드(fd 재사용)·FAST-FOLLOW·고급 sync 옵션 |
| 27 | DB 정합성 | d38 | 死物 `runs` 제거(최초 파괴적 마이그레이션)·finalize 원자화 |
| 28 | 운영·보안 | d39 | 레지스트리 fail-open 비침묵화·LDAP fail-closed 플래그 |
| 29 | 포탈 위생 | d40 | 로그아웃 URL·poll_failed 문구·denylist 인코딩 |
| 30 | 테스트 부채 마감 | d41 | 전수 열거 그물·이중 경로 그물·planner 원자화 |

> 아래 상세 기록은 **작성된 순서**(연대기와 다를 수 있음)로 쌓여 있다. 확정 연대는
> 태그 순서 d26→d41 다. 각 항목은 실증 결과·구현 중 잡은 플랜 결함·교훈을 담는다.

---

## 슬라이스별 상세 기록

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

### ✅ 슬라이스 35: 잡 이미지 릴리스 통합 — **완료**(2026-08-18, d81)

d80 실증에서 남은 마지막 구멍을 닫았다: mfu 를 빌드·릴리스해도 잡은 옛 이미지로
돌았다(릴리스는 워크로드 3종만 패치, `DMS_JOB_IMAGE` 는 ConfigMap env 라 재시작
필요). **artifact_base 선례(슬라이스 18 "DB 가 env 를 이긴다") 그대로** 잡 이미지를
DB 오버라이드로 승격했다:

- `control_state.job_image`(신규 컬럼) + `resolve_job_image(control, settings)` --
  DB 값 우선, NULL 이면 env. 소비자(VolcanoAdapter 의 잡·프리플라이트 매니페스트,
  BuildRunner 프로브)는 str|callable 계약(artifact_base 와 동일)으로 **호출
  시점마다** 해석 -- 릴리스 즉시 다음 잡부터 새 이미지, 재시작·파드 churn 없음.
- 릴리스 화면 넷째 행 `job-image`: targets 가 유효값·mfu 태그 목록을 실어 주고,
  제출 시 워크로드 배치와 갈라 `set_job_image`(감사) + releases 에 즉시 Applied
  행(`record_applied`)으로 남긴다. 검증은 워크로드와 같은 규칙(태그 형식·레지스트리
  존재·same_tag -- same_tag 는 유효값 기준). COMPONENTS 에 넣지 않은 이유:
  그 표는 patch/observe/ROLLOUT_ORDER 좌표라 섞으면 컨트롤러가 없는 워크로드를
  patch 하려 든다.
- 드리프트 보정: metrics 의 job_image.live = 유효값 + `source`(db|env). source=db 면
  "다음 kubectl apply 가 되돌립니다"가 거짓이 되므로 대시보드 문구를 가른다
  ("릴리스 오버라이드가 우선이라 되돌아가지 않습니다").
- 교훈(테스트): registry in_use 테스트가 실 deploy/k8s 를 읽어 태그 bump 커밋마다
  깨졌다 -- 동봉본을 목으로 고정해 저장소 상태 의존을 끊었다.

### ✅ 슬라이스 34: 드리프트 방지 + 이미지·이력 관리 — **완료**(2026-08-18, d75)

**① 드리프트 방지(빌드 시 매니페스트 스탬프).** 빌드 파드가 tar 스냅샷 뒤
`$DMS_BUILD_IMAGES` 각 이미지의 `/src/deploy/k8s/*.yaml` 태그를 빌드 태그로
sed 스탬프한다(콜론 구분 `/$img:` 로 dms 가 dms-agent 를 안 문다) — `Dockerfile.dms`
가 그 스냅샷을 COPY 하므로 배포 시 live == 동봉 manifest 가 되어 드리프트 배지가
안 뜬다. **빌드하는 이미지 줄만** 스탬프한다: dms 만 빌드하며 agent 줄까지 스탬프하면
dms 이미지가 담은 매니페스트가 "agent 도 이 태그"라 거짓 주장해(드리프트는 그 값을
dms-api 이미지에서 읽는다) 없던 드리프트를 만든다. 빌드 폼은 현재 적용 태그(인프라
메트릭 live)를 보여주고 dNN 이면 d(N+1) 을 제안한다. 실증(d75): dms 를 태그 d75 로
포탈 빌드→배포하니 dms-api·dms-controller `live=d75 manifest=d75 drift=no`(스탬프
로그 `stamped deploy/k8s tags -> d75`). dms-agent 는 손대지 않아 기존 드리프트 유지.

**② 빌드 이력 삭제.** `DELETE /api/admin/builds/{id}`(종단만 — 활성은 409
build_not_deletable, active() 가 읽는 행을 지우면 파드 도는 중 두 번째 빌드가 뜬다).
빌드 이력 화면에 다중 선택 삭제(BatchesList 관례: 늘 렌더 툴바로 체크 시 표가 안
밀림, 2단 확인, 부분 실패를 data.failed 로).

**③ 레지스트리 이미지 관리(신규 하위 페이지 「이미지 관리」).**
`GET /api/admin/registry/images`(3종 리포 태그+in_use), `DELETE .../{repo}/{tag}`.
**사용 중 태그 보호**: live(rollout observe) + manifest(동봉본) 태그를 모아 그 태그
삭제를 레지스트리 건드리기 전 409(registry_tag_in_use)로 막는다 — 드리프트와 같은
재료라 화면 간 두 번째 진실이 없다. 삭제는 태그(매니페스트)만 지운다: 블롭 회수
(garbage-collect)·노드 캐시는 별개, 시간 기반 자동 GC 는 두지 않는다(사용자 결정).
registry.py 에 OCI Accept 헤더로 digest HEAD 조회 + DELETE(405→disabled/404→not_found
매핑) 추가.

**④ 인프라(직접 수행).** pkg-01 의 docker `registry:2` 를 데이터 볼륨(`/opt/dms-registry`,
4.7GB) 보존한 채 `-e REGISTRY_STORAGE_DELETE_ENABLED=true` 로 재생성 → DELETE 가
405→404 로 바뀜(삭제 수용). 전 노드(6대) `crictl rmi` 로 pkg-01:5000 미사용 pull
캐시 정리(사용 중은 crictl 이 거부, 노드당 ~1–1.5Gi 확보).

실증(포탈 API 종단): 사용 중 d75 삭제 시도 → 409 보호. b99d97238 3종(dms·
dms-mpifileutils·dms-agent) 삭제 → 200(digest 반환) → 재조회 부재 확인.

한계/정직: 레지스트리는 ansible 미관리(수동 docker run)라 재생성이 수동이다 —
idempotent 화하려면 별도 role 이 필요(BACKLOG 후보). 블롭은 태그를 지워도
`registry garbage-collect` 전엔 디스크에 남는다(화면·문서에 명시).

### ✅ 슬라이스 33: 로컬 소스 빌드 — **완료**(2026-08-18, d73·d74)

포탈 빌드를 git clone 에서 **빌드 노드의 로컬 소스 경로**로 완전 전환했다(사용자
결정: 병행 없이 대체). 소스 경로는 빌드 노드처럼 컨트롤 상태(`build_source_path`)가
단일 진실이고, 빌드 파드가 그 경로를 **같은 절대경로에 ro hostPath** 마운트해 tar
스냅샷으로 `/src` 를 만든다 — **커밋·push 안 한 작업 트리도 빌드된다**(SHA 는
마운트의 .git 에서 읽고 미커밋 변경은 `-dirty` 접미, 워크트리 등 읽기 불가면
`unknown` 으로 정직하게 접는다). (선택) 태그 지정(`builds.tag` 컬럼)으로 관례
태그(dNN)를 붙이면 매니페스트-우선 배포가 포탈로 완결된다. 프리플라이트에 소스
센티널 검사(`deploy/docker/Dockerfile.dms` → `build_source_unavailable`)를 앞세웠고
egress 는 quay.io·registry-1.docker.io 둘로 줄었다. 사유 코드 4 추가·2 제거
(invalid_git_ref/invalid_repo_url), `build_repo_url` 설정·`repo_host()` 제거.
전제 인프라: 테스트베드 호스트 `/home/mason/dms-dev` 를 ro NFS 로 워커에 동일
절대경로 마운트(testbed 저장소 `make storage`, 별도 세션 작업).

실증(d73·d74):
- **양성**: 포탈 제출 → 프리플라이트 OK → 본 체크아웃(`faca75e`, 클린)에서 빌드,
  `commit_sha=faca75e…`(접미 없음)·태그 `t-local1` 레지스트리 push 확인.
- **도그푸딩**: d74 는 **워크트리 경로를 소스로 포탈에서 태그 d74 로 빌드**해
  배포 — 배포 후 dms-api/controller `live == manifest == d74`, **드리프트 배지
  없는 최초의 포탈 완결 배포**. 워크트리라 SHA 는 unknown(설계된 정직 폴백).
- **음성**: 오타 경로는 프로브의 hostPath 자동 생성이 **ro NFS 부모에서 mkdir
  실패** → 프로브가 못 떠 180s 뒤 `build_preflight_timeout` 으로 접혔다(실측).
  쓰기 가능한 부모(실 클러스터 로컬 디스크)에서만 빈 디렉토리가 생겨
  `build_source_unavailable` 로 즉답한다 — 이 한계를 코드 주석과 타임아웃 사유
  문구(소스 경로 확인 안내)에 남겼다.

교훈: 게스트 마운트 경로를 호스트와 일치시킨 것(테스트베드 결정)이 워크트리
`gitdir:` 절대경로 해석을 살릴 뻔했지만, 파드가 **지정 경로만** 마운트하므로
워크트리 SHA 는 여전히 unknown 이다 — 저장소 루트를 지정하는 것이 SHA 기록의
정상 경로다.

### ✅ dscan 1b93d54 정합 — **완료**(2026-08-14)

신 dscan(chahwansong/mpifileutils `1b93d54`, top-K 제거·스트리밍 재작성·
`--batch-files`/`--broken-limit` 신설)에 전 계층을 정합했다. ① 도메인:
scan 옵션 top_k 제거(unknown_option 거부 고정), batch_files 0..10억(실측:
0 = 배칭 끔)·broken_limit 0..10,000(실측: 0 허용 — 표본 미보관, 총계는 정확)
신설 — 상한은 DMS 위생 상한(도구 파싱 parse_uint64는 uint64 전체 수용).
② 렌더: `_SCAN_VALUE_FLAGS` 교체(--broken-limit은 롱네임뿐). ③ 리포트:
신 스키마(top_k·oldest 삭제, broken_paths_total/limit·summary.scan_errors
신설)로 픽스처 전환 — 러너 파서(summary.total_entries만 읽음)는 무변경이고
그 무변경이 안전함을 신 스키마 픽스처가 증명. stats 라우트는 broken 총계
2필드(숫자만, 모양 투영)를 신규 노출하되 구형 리포트는 None(미기록, null≠0).
④ 포탈: SubmitScan·BatchCreate에서 top_k 입력 제거, 두 신규 옵션 입력
(빈값 = 플래그 생략 = 도구 기본, placeholder에 기본값 명시) + 통계 패널
파손 경로 표시 한 줄. ⑤ Dockerfile.mpifileutils REF pin 갱신.
검증: 손댄 영역 백엔드 218 passed / vitest 전체 398 passed / tsc 0 /
e2e 9 passed(스캔 제출 흐름 E4 포함 — 무변경).

---

### ✅ 슬라이스 31 «포탈 디자인 개편 — DS Cloud 스타일» — **완료**(2026-08-13, d42)

플랜 `docs/plans/2026-08-13-dms-portal-redesign-slice31.md`(새 문서 체계 첫 플랜).
vitest **312 passed / 58 files**(기준선 266 +46) / tsc 0 / e2e 9 / **airgap 게이트 통과**
(dist 외부 로드 0, 폰트 전부 `/assets/` 번들 — 라이브에서 폰트 서빙 200 확인).

회사(DS Cloud) 디자인 언어로 전면 리스킨: ① 토큰 스왑(액센트 보라→DS 블루 #1a56db,
네이비·연파랑·panel 신설, radius 12→6px, 그림자→보더) + Noto Sans KR 셀프호스팅
② 셸 개편 — TopBar("AI Storage Portal" 네이비 브랜드), **데이터 기반 사이드바**
(navigation.ts — DMS 최상위 + 작업/스토리지/운영/관리 4그룹 16링크 + NAS·Monitoring
자리, 기본 펼침), Breadcrumb ③ 컴포넌트(Button 3계층·Stepper·BottomActionBar·
InfoPanel·InfoCard, StoragePicker 공용 이사) ④ **재사용 위저드 프레임** + SubmitJob
4스텝(연산→대상→옵션→확인, 제출 바디 계약 원문 보존) ⑤ 페이지 타이틀 격상 24곳.

의존성 2건 신설(승인): `@fontsource/noto-sans-kr`(dist +13MB — airgap 의 의도된 비용,
woff 폴백 제거는 후속 최적화 후보), `lucide-react`. e2e 불변식(사이드바 240px·L4)은
전부 유지된 채 통과 — 스킨이 계약을 안 깨뜨렸다는 증거.

구현 중 에이전트 판단: busy 색을 액센트와 구분(#1749b8 — 진행 배지≠링크), blocked
가드가 위저드 canNext 뒤에서 관측 불가해지자 강제 submit 테스트로 이빨 복원,
StoragePicker 사본 잔존 대신 진짜 이사(두 정의 갈라짐 방지).

**남긴 다듬기 거리**: NavLink prefix 매칭으로 /jobs/new 에서 "내 작업"도 활성 표시
(`end` 필요), woff 폴백 7.7MB 제거 검토. 디자인은 사용자 눈 실증으로 계속 반복.

---


### ✅ 슬라이스 16 «배포 안전망» — **완료**(2026-08-10, d26 배포·§6 실증 6/6 통과)

실증 결과: (1) 매니페스트-우선 관례대로 배포하니 드리프트 배지 없음. (2) 포탈로
controller 를 d25 로 롤아웃하자 배지가 뜨고 원복하니 사라짐. (3) api·controller 를
동시에 올려 initContainer 두 개가 병렬 migrate → 둘 다 "migrated"(락 없으면 42701
로 하나가 죽는다). (4) 콜드 스타트 sync 가 즉시 거부 대신 Pending 유지 후 성공,
`identity_propagating` 이벤트에 source/destination 5노드 전부 기록. (5) 워커 5개가
w1~w5 **각각 다른 노드**에 — Volcano 가 템플릿 라벨을 파드에 전파함도 함께 확인.
(6) 에이전트 rx=2,806,825,281,085 vs 호스트 실측 2,806,825,725,719(파드 veth 는
8,536) — 호스트 netns 를 읽는다.

아래는 착수 당시의 원 항목이다(기록 보존용).

### 슬라이스 16 «배포 안전망» — 조용히 프로덕션을 깨는 것들
1. **매니페스트 드리프트 표시·경고**. 포탈 롤아웃은 살아있는 오브젝트만 바꾸므로
   이후 `kubectl apply -f deploy/k8s/`가 **옛 태그로 되돌린다**.
   `deploy/README.md` §9-4가 "드리프트 표시는 슬라이스 14 대시보드의 몫"이라 적었으나
   슬라이스 14는 만들지 않았다(설계 §2.4는 이미지+ready+판정으로만 범위 한정).
2. **스키마 변경 배포의 migrate 자동화**. `set image`는 migrate Job을 재실행하지
   않는다 — 슬라이스 14에서 실 500(`column "files_count" does not exist`), 슬라이스
   15에서도 수동 재실행 필요. "교훈"으로만 기록되고 자동화 안 됨.
3. **플래너 신원 전파 유예/재시도**. `placement.py:61,68,92`가 `no_eligible_nodes`/
   `no_ready_sync_candidate`를 **즉시 영구 거부**한다. `identity_probe_targets` 등록이
   첫 요청 시점이라 **신규 사용자의 첫 요청은 항상 실패**한다. phase3c에서 파킹
   (`progress.md:126`)됐고 슬라이스 15 실증에서 재현.
4. **워커 `podAntiAffinity` 미구현**. 원본 설계 §181이 "워커 anti-affinity(노드당 1개)"를
   명시했고 phase3c 레저 `:9`가 "플랜 텍스트엔 있으나 코드엔 없음"으로 파킹.
   `grep -rn "antiAffinity" src/dms/` → 0건. **`max_nodes` 정책이 노드가 아니라
   레플리카만 제한**하게 되어 MPI 팬아웃이 한 노드로 붕괴할 수 있다.
5. **에이전트 `hostNetwork` 네트워크 지표**. `probe_os_metrics()`가 파드 netns의
   `/proc/net/dev`를 읽어 `network_rx_bytes`/`tx`가 **veth 값**이다(`deploy/README.md`
   미해결 값). 슬라이스 14가 이 값을 차분해 대시보드 라인차트로 만들어 운영자가
   신뢰하게 됐다 — 틀린 수를 신뢰하게 만든 상태.

### ✅ 슬라이스 17 «큐 가시성» — **완료**(2026-08-10, d28 배포·§6 실증 6/6 통과)

실증 결과: (1) RBAC 적용 전에는 두 축 모두 `null`(알 수 없음)이고 엔드포인트는 200 —
`[]`로 뭉갰다면 운영자가 "큐가 한가하다"로 읽었을 상황이다. 적용 후 `Open` + `[]`가
되어 앞의 `null`과 구분된다. (2) 실 sync 잡에서 PodGroup 이 Inqueue(3초) →
Running(6초)로 잡히고 완료 후 사라졌다 — "PodGroup 은 잡 종료와 함께 삭제된다"가
화면에서 확인됐다. (3) 백필된 52건 중 **47건이 0초**였다(1초 해상도). falsy 검사가
한 곳이라도 남았다면 데이터의 90%가 조용히 사라졌을 것 — SQL 술어·히스토그램 가드·
라우트 세 계층에 각각 심은 가드가 값어치를 했다.

파생 항목(→ 슬라이스 20): PodGroup 이 잡과 함께 삭제되므로 **끝난 잡의 스케줄링
대기 이력이 없다**. 설계 §7 이 후속을 예고했다.

`runs` 테이블 부활은 **명시적으로 배제**하고 `data_jobs.submit_wait_seconds` 파생
컬럼 + 커버링 인덱스를 택했다(설계 §2.3). 아래는 착수 당시의 원 항목이다(기록 보존용).

### 슬라이스 17 «큐 가시성»
1. **Volcano 큐 현황 대시보드**(사용자 요청): 대기 중 작업 갯수, 대기 시간, 통계.
   슬라이스 14 비목표(`slice14-design.md:79,153`: "코드·RBAC 없음, CRD 읽기 + Role
   변경 필요"). 원본 설계 §300이 "Volcano 큐/우선순위"를 요구.
2. **전역 큐 대기 집계**. `runs` 테이블이 死物이다 — `migrations.py:69`가 만들지만
   읽기·쓰기 0건. 그래서 큐 대기는 요청 상세에서만 유도되고 전역 집계는 풀스캔이라
   금지됐다(`slice14-design.md:63-68`). 원본 설계 §293이 요구한 지표.
   → `runs` 부활 또는 `data_jobs`에 인덱스된 파생 컬럼 중 택일(설계 시 결정).

### ✅ 슬라이스 18 «아티팩트 경로 설정» — **완료**(2026-08-11, d29 배포·§6 실증 6/6 통과)

설계 `specs/2026-08-10-dms-artifact-base-slice18-design.md`, 플랜
`plans/2026-08-10-dms-artifact-base-slice18.md`. 백엔드 1043 / 프론트 219 / tsc 0.

실증 결과(테스트베드, d29):
1. DB 미설정 → `source: env`, `db_value: null`, `effective == env_value` — 기존 배포
   무변화. **라이브 DB 는 오래전에 만들어졌으므로 새 컬럼 5개는 `_ensure_columns`
   ALTER 경로로 들어왔다**(슬라이스 14 의 "한쪽만 넣으면 라이브에서만 컬럼이 없다"
   교훈이 이번엔 통과).
2. base 를 `/cephfs/dms/artifacts-slice18` 로 바꾸고 실 scan 잡을 돌리니
   `artifact_uri` 가 새 경로를 가리키고 `files_count=10` 이 채워졌다 — 컨트롤러가
   **새 경로에서 summary.json 을 실제로 읽어냈다**는 뜻이라 쓰기→읽기 사슬 전체가
   확인됐다. 디스크에도 dscan-report/summary/stdout/stderr 가 요청자 uid 로 있었다.
3. 잡 55건 상태에서 PUT 이 409 `artifact_base_locked`, `force:true` 로 통과.
   감사에 `{"forced": true, "affected_jobs": 53}` 기록. **부수 확인**: `before_state`
   의 `changed_by`/`changed_at` 이 그대로였다 — 전용 UPDATE 가 컨트롤 상태의 변경
   이력을 오염시키지 않는다(설계 §2.1)는 것이 라이브에서 확인됐다.
4. 없는 경로 → `validate` 가 422 `artifact_base_missing`. 파일을 주면
   `artifact_base_not_directory`. **PUT 은 잠금이 먼저라 409 가 난다** — 정규화 →
   잠금 → 즉석 검증 순서(설계 §2.5)가 라이브에서 그대로 관측됐다.
5. **핵심**: `/cephfs` 밖 경로(API 파드에만 만든 `/tmp/dms-outside`)로 바꾸니
   API `ok:true` / 컨트롤러 `artifact_base_missing` / 노드 `exists:false` 로
   **세 홉이 실제로 갈라졌다**. API 혼자 판단했다면 "쓰기 가능"으로 저장하고 끝났을
   상황이다. 아직 프로브하지 않은 노드는 `pending` 으로 실패와 구분됐다.
6. 경로 중간 `file://` → 422 `artifact_base_scheme_in_path`. 상대경로·`..` 도 각각
   고유 사유 코드로 거부.

포탈 화면(`/admin/artifact-base`)도 라이브 확인: 경로 변경 직후 노드 5행이 전부
「확인 대기 중」이었다가 60s 주기로 「있음/가능」으로 수렴하는 것을 브라우저에서 봤다.

**이번에 배포 순서를 틀렸다(기록)**: 이미지를 먼저 빌드하고 매니페스트를 나중에
올렸다. 드리프트 판정은 **그 이미지를 만든 소스 트리의 매니페스트**를 보므로
(`manifest_tags.py` 머리 주석 — Dockerfile.dms 가 deploy/k8s 를 이미지에 COPY 한다),
d29 안의 동봉본은 d28/d27 이고 라이브는 d29 라 배지가 떴다. 슬라이스 16 이 세운
**매니페스트-우선** 관례의 진짜 의미가 이것이다: **매니페스트를 먼저 올려 커밋하고,
그 커밋으로 이미지를 빌드해야** 동봉본과 라이브가 일치한다. 다음 슬라이스 배포부터
그 순서를 지킨다(그때 이 드리프트도 함께 해소된다).

아래는 착수 당시의 원 항목이다(기록 보존용).

### 슬라이스 18 «아티팩트 경로 설정» (사용자 요청)
- 포탈에서 **아티팩트 저장 경로 설정** + **가능여부 검증**(실제로 써도 문제없는지).
- 현재는 ConfigMap 환경변수 `DMS_ARTIFACT_BASE_URI=file:///cephfs/dms/artifacts` 고정.
- **설계 시 반드시 다룰 것**: 경로를 바꾸면 **기존 잡의 아티팩트를 못 읽는다**.
  `execution_volcano.py`가 `self._artifact_base`로 `summary.json` 경로를 재구성하므로
  (`:207-222`) 옛 잡의 요약/로그 열람이 깨진다. 마이그레이션/이중 조회/잡별 base 기록
  중 택일 필요. 검증은 "컨트롤러·API·잡 파드 세 곳에서 쓰기 가능한 공유 FS인가"를
  봐야 하며 노드별 마운트 상태(에이전트 리포트)와 교차 확인해야 한다.

### ✅ 슬라이스 19 «계정 위생» — **완료**(2026-08-11, d30/d31 배포·실증 통과)

설계 `specs/2026-08-10-dms-account-hygiene-slice19-design.md`, 플랜
`plans/2026-08-10-dms-account-hygiene-slice19.md`. 백엔드 1073 / 프론트 224 / tsc 0.

실증 결과(테스트베드, d30):
1. **스푸핑 차단**: 공유 토큰 + `x-dms-actor: root` → **400 `invalid_actor`**,
   `x-dms-actor: alice`(임의 사용자 사칭)도 400. 고치기 **전에** 체인을 실제로
   재현해 `ResolvedIdentity(uid=0, gid=0, privileged=True)` 가 나오는 것을 눈으로
   확인했다 — env 오버라이드 없이 **배포 기본값만으로** 열려 있었다.
2. **에이전트 무손상**: 5개 노드 전부 `node:<이름>` 경로로 계속 리포트(fresh=true).
   이게 깨졌으면 노드 지표·마운트 판정이 전부 멈춘다.
3. **백로그가 지목한 실제 피해 해소**: 유령 관리자 `s3verify` 를 실제로 삭제(204).
4. **마지막 활성 관리자 보호 3경로 전부**: 관리자를 한 명으로 줄인 뒤 삭제·강등·
   비활성화 시도가 **모두 409 `last_active_admin`**.
5. **자기 삭제 차단**: 세션 로그인 상태에서 자기 계정 삭제 → 409
   `cannot_delete_self`(토큰 경로에서는 actor 가 `shared-token` 이라 이 가드가
   발동하지 않는다 — 설계가 적어 둔 그대로).
6. **비종단 요청 가드**: 세션으로 scan 을 제출한 직후 그 계정 삭제 시도 → 409
   `account_has_active_requests`.
7. **세션 로그인으로 잡 제출이 정상 동작** — 설계 §2.2-3 이 예고한 새 실증 방식이
   실제로 성립한다(토큰으로는 더 이상 잡을 못 낸다).

리뷰에서 더 조인 것: `resolve_job_identity(session_authenticated=...)` 기본값이
fail-open 이었다. 프로덕션 호출자는 planner 하나뿐이라 정상 경로는 무관하지만,
미래에 호출자가 늘고 인자를 빠뜨리면 uid 0 승격이 조용히 되살아난다 →
**fail-closed(False)** 로 뒤집고 특권 검증 테스트 5곳이 조건을 명시하게 했다.

포탈에서 잡은 것(라이브에서만 보였다): 삭제 열이 붙으면서 계정 표가 뭉개졌다.
원인 두 가지 — `td` 자체를 flex 컨테이너로 쓴 것, 그리고 AppShell 의 flex 자식에
`min-w-0` 이 없어 안쪽 `overflow-x-auto` 가 발동하지 못하고 레이아웃 전체가 넓어져
사이드바를 밀어낸 것. 후자는 **넓은 표를 가진 모든 화면**의 잠복 결함이었다.

아래는 착수 당시의 원 항목이다(기록 보존용).

- **계정 삭제 API + 포탈 UI**. 슬라이스 9 비목표(`slice9-design.md:39-43`). 실제 피해:
  슬라이스 3 실증이 만든 임시 관리자 **`s3verify`가 아직 살아 있다**(삭제 수단 없음).
- **공유 토큰 actor 스푸핑**. 토큰 보유자가 `x-dms-actor: root`로 uid 0을 얻을 수 있다
  (`deploy/README.md` 미해결 값). 세션 기반 actor로 전환 검토.
- ❌ **회원가입 메일 인증은 이 슬라이스에서 제외** — 사용자 지시: 추후 회사 메일
  인증으로 교체할 계획이므로 지금 손대지 않는다. (현재 `routes_auth.py:22` 더미,
  코드 검증 없이 가입 가능. **의도적 보류**이며 미인지 결함이 아니다.)

### ✅ 슬라이스 21 «포탈 빌드 되살리기» — **완료**(2026-08-11, d33 배포·실증 통과)

설계 `specs/2026-08-11-dms-portal-build-slice21-design.md`, 플랜
`plans/2026-08-11-dms-portal-build-slice21.md`. 백엔드 1131 / 프론트 228 / tsc 0.

**이 테스트베드에서 포탈 빌드가 처음으로 성공했다** — 이전 유일한 기록은
`build_failed` 였다. 빌드 `824ce0e2`: `Succeeded`, tag `b824ce0e2`, commit
`80d06c5b2977`, `pkg-01:5000/dms:b824ce0e2` push 완료.

요구가 착수 당시와 달라졌다: 컨트롤플레인이 아니라 **워커 노드 하나**를 지정하고,
그 워커는 **데이터 잡 풀에서 빠지지 않는다**(잡과 빌드를 동시에). 그래서 아래
「원 항목」의 컨트롤플레인 과제 3건은 소멸했고, 대신 리소스 봉투와 축출 순서가
슬라이스의 핵심이 됐다.

실증 결과(테스트베드, d33):
1. **egress 차단 → 45초 만에 `build_node_no_egress`**, 로그에 실패 호스트 전부
   (`unreachable_443=github.com,quay.io,registry-1.docker.io`). 2시간 generic
   타임아웃이 아니라 45초다 — 이 슬라이스의 핵심 개선.
2. **실 빌드 성공.** 가장 큰 미지수였던 **npm(vite) 빌드가 memory limit 1Gi 안에서
   정상 동작**함이 확인됐다. emptyDir 피크는 **1.2G**(sizeLimit 10Gi 대비 여유 충분)
   — §2.2/§2.4 봉투는 재보정 없이 유지한다.
3. **빌드 중 데이터 잡 무손상**: 빌드가 도는 동안 scan 잡이 20초에 완료,
   `sched_wait=5`·`submit_wait=0` 으로 **평시와 동일**, **잡 파드 축출 0건**.
   (평시 기준선 95초는 신원 전파 지연이 포함돼 오히려 더 느렸다.)
4. **축출 순서의 입력값 확인**: 빌드 파드 `dms-build`/priority **10**/Burstable,
   데이터 잡 `dms-mid`/priority **100**/BestEffort. kubelet 은 "requests 초과 →
   priority 낮은 순"으로 축출하므로 압박 시 빌드가 먼저 죽는다. 실제 노드 메모리
   압박 유발은 클러스터를 위협하므로 **입력값 검증으로 대체**했다 — 정직하게 기록한다.

**실증이 찾아낸 결함 1건(고침)**: 프리플라이트 프로브는 **파드 네트워크**로 egress 를
검사하는데 빌더 이미지 pull 은 kubelet/CRI-O 가 **노드 네트워크**로 수행한다
(`imagePullPolicy: Always` 라 매 빌드마다). 두 경로가 갈려 있어, 노드 egress 만 막으면
프로브는 통과하고 빌드 파드가 `ImagePullBackOff` 로 앉는다(2시간 뒤 `build_stuck_pending`).
→ 빌더 이미지를 **`pkg-01:5000/buildah:stable` 미러**로 돌려 노드의 인터넷 의존을
없앴다. 이제 남는 인터넷 수요가 전부 빌드 파드 안이라 프로브가 검사하는 경로와
실제 필요 경로가 일치한다.

**미실행 실증 2건(정직하게 남긴다)**: §6-5 디스크 부족(`build_node_disk_low`)과
§6-6 레지스트리 차단(`build_registry_unreachable`)은 단위 테스트로만 덮였고
테스트베드 재현은 하지 않았다. 둘 다 프로브 스크립트의 같은 분기 구조라 위험도가
낮다고 판단했으나, 실행하지 않은 것은 실행하지 않은 것이다.

아래는 착수 당시의 원 항목이다(기록 보존용).

### 슬라이스 21 «포탈 빌드 되살리기» (사용자 요청, 2026-08-11)

**운영 방식이 정해졌다**: 이미지 빌드·배포가 필요할 때 **운영자가 DMS 노드 하나에
인터넷을 일시적으로 열어 준다**. 그 노드는 **컨트롤플레인(마스터) 노드** 중 하나로
한다. 포탈에서 그 노드를 빌드 노드로 지정하고, **빌드 착수 전에 인터넷 가능 여부를
먼저 확인**한 뒤 진행한다.

지금 막혀 있는 것(코드 좌표는 확인함):
1. **컨트롤플레인 노드는 빌드 노드로 지정할 수 없다.** `routes_control.py` 가
   `build_node_name` 을 `agent_nodes` 에 실재하는 노드로만 제한한다(422
   `unknown_build_node`). 에이전트는 DaemonSet 인데 컨트롤플레인 taint 때문에
   `dms-cp1` 에는 뜨지 않는다(현재 에이전트 5개 = 워커 5개). → 에이전트에 
   컨트롤플레인 toleration 을 주든, 빌드 노드 검증을 `agent_nodes` 밖으로 넓히든
   택일해야 한다. 전자는 노드 지표·마운트 프로브까지 따라오므로 부작용을 따져야 한다.
2. **egress 사전 확인이 없다.** 지금은 buildah 가 몇 분 돌다가 clone/npm 단계에서
   죽고 `build_failed` 만 남는다 — 원인이 "인터넷이 안 열렸다"인지 알 수 없다.
   짧은 프로브(예: 빌드 파드와 같은 노드·같은 이미지로 `curl -sS -m 5 <ref>` 1회)를
   빌드 제출 경로에 넣고 **고유 사유 코드**(예: `build_node_no_egress`)로 즉시
   거절해야 한다. 그래야 운영자가 "인터넷을 아직 안 열었다"를 바로 안다.
3. **빌드 파드가 컨트롤플레인에서 돌 수 있어야 한다** — `build_manifests.py` 의
   파드 스펙에 컨트롤플레인 toleration 이 필요하다.

배경: 이 테스트베드는 "pkg-01 만 인터넷, dms 노드는 ssh 만" 모델이라 포탈 빌드가
구조적으로 불가했고(§2.3), 슬라이스 18~20 의 이미지는 전부 pkg-01 에서 podman 으로
만들었다. 이 슬라이스는 그 우회를 없애는 것이 목표다.

### ✅ 슬라이스 20 «Volcano 대기 이력» — **완료**(2026-08-11, d32 배포·실증 통과)

설계 `specs/2026-08-10-dms-sched-wait-slice20-design.md`, 플랜
`plans/2026-08-10-dms-sched-wait-slice20.md`. 백엔드 1090 / 프론트 225 / tsc 0.

**원안(컨트롤러가 PodGroup 샘플링)을 기각**하고 스테퍼가 이미 매 틱 하는 vcjob
phase 관측을 썼다 — 추가 k8s 호출 0, **RBAC 변경 0**, 계약 테스트 개정 0. 측정
앵커는 전이 행이 아니라 `data_jobs.exec_submitted_at` 컬럼으로 확정했다: 전이 행
방식도 지금은 동작하지만 그 유일성이 세 모듈 교차 불변식에 얹혀 있어, 어느 쪽이
깨져도 sync/rm 값이 **조용히** 틀어진다(설계 §2.1 이 PodGroup 이름 유도를 기각한
것과 같은 실패 모드).

실증 결과(테스트베드, d32):
1. 배포 직후 `sched_wait_counted=0`, `sched_wait_excluded=55` — 백필이 없으므로
   과거 잡 55건이 전부 "기록 없음"으로 정직하게 표면화됐다(대조군 `submit_wait` 은
   백필이 있어 55건 전부 counted).
2. **실 scan 잡에서 `sched_wait_seconds=5`, 같은 잡의 `submit_wait_seconds=0`** —
   두 지표가 **서로 다른 것을 재고 있음**이 라이브에서 증명됐다. 제출 대기만 봤다면
   운영자는 "대기가 전혀 없었다"고 읽었을 잡이다. 이 슬라이스의 존재 이유다.
3. 집계 반영: `counted=1 → 2`, 히스토그램 `<10s` 버킷에 계상.
4. **Running 미도달 잡**(preflight 거부)은 `exec_submitted_at`·`sched_wait_seconds`
   둘 다 NULL — 앵커가 execution 제출 경로에서만 찍힌다.
5. 포탈: 「제출 대기 분포」와 「스케줄 대기(Volcano) 분포」가 나란히, 각각 집계/제외
   건수와 함께. 스케줄 대기 캡션이 근사 오차(스테퍼 틱 5초)를 그대로 적는다.

**라이브에서 새로 알게 된 것 — 해상도 바닥**: 관측된 두 잡이 **정확히 둘 다 5초**
였다. 스테퍼 틱이 5초라 첫 RUNNING 관측이 사실상 틱 해상도에 걸린다 — 슬라이스 17
의 제출 대기(52건 중 47건이 0초)와 **정반대 분포**다. 즉 이 값의 실질 해상도는
5초이고, 5초 미만의 실제 큐 대기는 구분되지 않는다. 0 초 기록은 vcjob 이 제출 직후
첫 폴링에서 이미 Running 일 때만 나오므로 실제로는 드물다(가드는 그래도 4계층 전부
있고 뮤테이션으로 이빨을 확인했다 — 값이 드물다는 것과 없어도 된다는 것은 다르다).
더 정밀한 값이 필요해지면 스테퍼 틱을 줄이는 것이 아니라 PodGroup
`status.conditions` 를 읽어야 하는데, 그건 슬라이스 17 §7 이 금지한 항목이다.

### 슬라이스 20 «Volcano 대기 이력» (슬라이스 17 파생) — 착수 당시 원 항목(기록 보존용)
- 끝난 잡의 스케줄링 대기가 사후에 없다 — PodGroup 이 잡 종료와 함께 삭제되기 때문.
- 백필 원천이 존재하지 않으므로 **과거 잡은 전부 NULL 이 맞다** — 지어내지 않고
  `excluded` 건수로 표면화한다.

### 슬라이스 22 후보 «SSH 의존 점검·완화» — **실증으로 대부분 해소됨(2026-08-11)**

> **결론 먼저**: Cilium 기본 구성(`routing-mode = tunnel`, `tunnel-protocol = vxlan`,
> 라이브 cilium-config 실측)에서는 **노드 간 22 번 차단이 MPI 를 깨지 않는다.**
> 테스트베드에서 직접 재현했다: w1·w2 에 `FORWARD -p tcp --dport 22 -j REJECT` 를 건
> 상태로 w2 파드 → w1 파드 22 번이 **CONNECTED**, 같은 규칙 아래 대조군(파드→외부 22)은
> **BLOCKED**. 즉 규칙이 무효했던 게 아니라 **VXLAN 캡슐화가 안쪽 22 를 가린 것**이다.
> → 아래 할 일 2·3(포트 이동·런처 대체)은 **프로덕션이 native routing 일 때만** 필요하다.
> 프로덕션에서 확인할 한 줄:
> `kubectl -n kube-system get cm cilium-config -o jsonpath='{.data.routing-mode}'`
> `tunnel` 이면 끝. `native` 면 그때 2 부터 착수한다.
>
> **남는 것은 설치 시점 SSH 하나뿐이다**(할 일 4) — 사용자 판단으로 **나중에 다룬다**.



**계기**: 사용자 프로덕션 클러스터는 노드 간 네트워크는 되지만 **SSH 가 제한**된다.

**조사 결과(2026-08-11, 코드 전수)**:
- **DMS 런타임에 노드↔노드 SSH 는 없다.** `paramiko` 0건, 노드 호스트명 접속 0건.
  legacy DMS 는 노드 root SSH 를 썼지만(`testbed/docs/ARCHITECTURE.md` §15 의
  `ssh-host-exec`) **clean-slate 구현은 그 의존이 없다.**
- **SSH 는 정확히 한 곳, MPI rank 기동에 파드↔파드로만 쓰인다.**
  vcjob 이 `plugins: {"ssh": [], "svc": []}` 선언(`execution_manifests.py:359,399`)
  → Volcano 가 파드에 키쌍 물질화 + task 별 hostfile 제공. **워커 파드가 컨테이너 안에서
  `sshd -D`** 를 띄우고(`deploy/docker/Dockerfile.mpifileutils:121`), 런처가
  `OMPI_MCA_plm_rsh_agent="ssh -o StrictHostKeyChecking=no …"`
  (`dms_job_runner/commands.py:20-22`)로 **워커 파드 호스트명**에 접속한다
  (`runner.py:165` — `/etc/volcano/<task>.host`). 대상은 전부 파드이지 노드가 아니다.
- **그래도 프로덕션에서 깨질 수 있다**: 파드↔파드 SSH 도 물리적으로는 노드 사이를
  지난다. **CNI 데이터패스에 달렸다** — 오버레이/캡슐화(VXLAN·Geneve·IPIP)면 안쪽 22 가
  터널에 감싸여 무관하지만, **네이티브 라우팅이면 노드 간 22 차단이 MPI 를 깬다.**

**할 일**:
1. **프로덕션 CNI 캡슐화 여부 확인**(사용자 환경 정보 필요) — 이게 갈림길이다.
2. 막힌다면 **파드 내 sshd 포트를 22 밖으로 이동**: `Dockerfile.mpifileutils` 의
   sshd_config `Port`, `commands.py` 의 `plm_rsh_agent` 에 `-p <port>` — 포트 기반
   제한이면 이걸로 끝난다. 가장 싼 해법.
3. 그래도 안 되면 SSH 런처 자체를 대체(PMIx/PRRTE 등) — 범위가 크고 mpifileutils
   빌드까지 얽히므로 2 가 실패한 뒤에만 검토한다.
4. **설치 시점 SSH 도 정리 대상**: `deploy/docker/registry-setup.sh` 가 노드에 SSH 해
   `/etc/containers/registries.conf.d/` 를 쓴다. 런타임은 아니지만 SSH 제한 환경에서는
   설치가 막히므로 Ansible/DaemonSet/운영자 수기 중 하나로 대체 경로를 문서화한다.

### 슬라이스 21 잔여 (다음 작업 리스트로)

1. ✅ **미실행 실증 2건 — 완료(2026-08-11)**. 둘 다 **45초** 만에 각자의 사유 코드로
   실패했고 로그에 실측값이 남았다: `build_node_disk_low`
   (`avail_bytes=17918570496 need_bytes=18957493248`, 노드에 3.5GB 파일을 만들어 공식
   아래로 내림), `build_registry_unreachable`(`unreachable_registry=pkg-01:5000`,
   `iptables -I FORWARD -p tcp --dport 5000 -j REJECT`). **세 사유 코드(egress·disk·
   registry)가 각각 구분되어** 나오는 것을 확인했다. 규칙 제거 후 대조 빌드가
   `pushed pkg-01:5000/dms:b2d3749d6` 로 성공해 원상 복구도 확인했다.
   재현 시 주의(실증에서 헛짚고 배운 것): **파드 egress 는 `FORWARD` 로 막아야 한다.**
   `OUTPUT` 은 노드 자신이 만든 트래픽만 걸리므로 파드는 그대로 나간다.
   덤으로 `build_node_report_stale` 도 의도치 않게 실증됐다 — API 가 90분간 불능이던
   동안 에이전트 리포트가 수집되지 못해 전 노드가 stale 이 됐고 제출이 422 로 거절됐다
   (§1 슬라이스 22 후보 참고).
2. **`build_failed` 세분화** — OOMKilled(memory limit 1Gi)와 sizeLimit 축출이 파드
   phase Failed 로 접혀 전부 `build_failed` 가 된다(설계 §4 가 한계로 명시). 로그가
   급단절된 build_failed 를 만나면 운영자가 OOM/축출을 의심해야 하는 상태 — 파드
   `status.containerStatuses[].state.terminated.reason` 을 읽어 구분하면 된다.
3. **리소스 봉투의 설정화** — 지금은 상수다(`build_manifests.py`). 실증에서 emptyDir
   피크 1.2G·memory 1Gi 통과가 확인됐으므로 당장 급하지 않지만, 노드 사양이 다른
   환경에서는 env 튜너블이 필요해진다.
4. **빌더 이미지 미러 갱신 절차의 자동화** — 지금은 `20-config.yaml` 주석의 수기 3줄
   (pkg-01 에서 pull/tag/push)이다. 미러가 낡으면 buildah 버전이 고정된다.
5. **pkg-01 podman 우회 삭제** — 포탈 빌드가 성공하므로 `deploy/README.md` §1 의 수기
   빌드 경로를 "비상용"으로 격하하고 §8 의 "구조적 불가" 경고를 걷어낸다. (슬라이스 21
   이 §8 경고를 아직 안 걷었다 — 실증 통과 뒤 정리하기로 했던 항목이다.)
6. **빌드 동시 2개 허용** — 지금은 `api-replicas=1` 전제의 단일 활성 빌드 가드다.

---

### ✅ 슬라이스 22 «DB 커넥션 재연결» — **구현 완료·부분 실증**(2026-08-11, d34)

설계 `specs/2026-08-11-dms-db-reconnect-slice22-design.md`, 플랜
`plans/2026-08-11-dms-db-reconnect-slice22.md`. 백엔드 1164 / 프론트 228 / tsc 0.

**통과한 실증(핵심)**: pkg-01 에서 `pg_terminate_backend` 로 dmsdb 커넥션 **3개를 전부
강제 종료**했는데 — 즉 사건과 같은 상황을 만들었는데 — **첫 요청이 200** 이었고
`reconnects: 1`, `last_reconnect_at` 이 찍혔다. **API·컨트롤러 둘 다 RESTARTS=0**
(사건 당시 컨트롤러는 크래시로 재시작했다). events 에 `db_reconnected` 2건이
남았다(api 13:43:32 / controller 13:43:31) — 양쪽 배선도 확인됐다.

**🔴 실증이 이 슬라이스 자체의 결함을 찾았다 — 자기 종료가 발화하지 않는다.**
API 파드 노드에서 5432 를 차단해 "재연결조차 실패하는" 상황을 만들었더니, 파드
이벤트가 `Readiness probe failed: context deadline exceeded (x35 over 11m)` 였다 —
**`/readyz` 가 503 을 낸 게 아니라 응답 자체를 못 했다.** 원인 사슬:
1. `Database` 는 단일 커넥션 + RLock 이라 모든 쿼리가 직렬화된다.
2. 재연결의 `connect()` 에 **연결 타임아웃이 없다**(psycopg 가 내부적으로 재시도한다 —
   로그의 `raise last_ex.with_traceback(None)` 가 그 흔적).
3. 그래서 readyz 핸들러가 락을 오래 쥔 채 매달리고, 프로브가 타임아웃한다.
4. **연속 실패 카운터는 `except` 분기에서만 증가**하는데 거기 도달하지 못하므로
   카운터가 안 늘고 **자기 종료가 영원히 발화하지 않는다.**

즉 재연결(§2.2)은 실증됐지만 **"재연결까지 실패"를 다루려던 §2.4 가 정확히 그
상황에서 무력하다.** 설계가 스스로 적은 "RLock 안에서 대기하면 API 전체가 멈춘다"가
재연결 경로에서 실현된 것이다.

**✅ 후속 조치 완료(2026-08-11)**: `db.py` 에 `DB_CONNECT_TIMEOUT_SECONDS = 5` 를
두고 `_open()` 의 psycopg 분기가 `connect_timeout` kwarg 로 넘긴다. 5 를 고른 이유는
**프로브 주기(10s)보다 짧아야 매 프로브가 반드시 503 으로 끝나기** 때문이다 — 이
값을 10 이상으로 올리면 위 사슬이 그대로 되살아난다(코드 주석에 박아 뒀다).
URL 이 이미 `connect_timeout=` 을 지정했으면 우리 기본값을 얹지 않는다(libpq 는
kwargs 를 URL 파라미터보다 우선하므로, 안 걸러내면 운영자 명시값이 조용히 무시된다).
테스트 2건 신설(`tests/test_db_reconnect.py`): 기본값 전달 / URL 명시 시 미덮어씀.
뮤테이션으로 이빨 확인(가드를 `if True:` 로 바꾸면 후자가 빨개진다).

**남은 것**: **라이브 발화 재실증** — 5432 를 다시 차단해 `/readyz` 가 (타임아웃이
아니라) 503 을 내는지, 카운터가 30 에 닿아 자기 종료가 실제로 발화하는지 확인.
단위 테스트는 카운터·리셋·비활성을 이미 고정했으므로 남은 건 라이브 확인뿐이다.

아래는 착수 당시의 원 항목이다(기록 보존용).

### 🔴 슬라이스 22 후보 «DB 커넥션 재연결» — **최우선(프로덕션 영향)**

**2026-08-11 라이브에서 실제로 발생했다.** API 파드가 90분간 `0/1 Running` 으로
방치돼 있었다. 포탈·API 전면 불능이었고 **아무도 몰랐다**.

**원인**: `src/dms/db.py` 의 `Database` 는 **단일 커넥션**(`self._conn`)을 들고
**재연결 로직이 전혀 없다** — `OperationalError`/`InterfaceError` 처리도, `closed`
검사도, 재시도도 0건이다(`db.py:32,57-80` 전수 확인). 커넥션이 한 번 끊기면 이후
모든 쿼리가 영구히 실패한다.

**왜 자동 복구가 안 되는가(이게 진짜 문제다)**:
- `/readyz` 는 정직하게 503 을 낸다(`api/app.py:51-60` — DB 에 SELECT 1). 좋은 설계다.
- 그런데 `/healthz`(liveness)는 DB 를 안 보고 **200 을 낸다** → **kubelet 이 파드를
  재시작하지 않는다.**
- readiness 503 → Service 에서 빠짐 → **API 가 죽지도 살지도 않은 채 무한정 방치**된다.
- 컨트롤러는 같은 사건에서 크래시해 재시작으로 살아났다(재시작 1회 기록) — 즉 **API 만
  이 함정에 빠진다.** 죽는 편이 나았던 셈이다.

**실측 확인**: 파드에서 DB 로 TCP 는 정상(`10.10.10.30:5432` connect OK)인데
`SELECT 1` 만 실패 — 네트워크가 아니라 **커넥션 객체가 죽은 것**이다. 파드를 지우니
즉시 복구됐다.

**할 일**:
1. `Database` 에 **재연결**을 넣는다 — 쿼리 실패 시 커넥션 상태를 보고 1회 재연결 후
   재시도. 단일 커넥션 + RLock 구조라 재연결 지점이 한 곳이라는 게 그나마 다행이다.
   트랜잭션 중간 실패는 재시도하면 안 된다(부분 적용) — 그 경계를 설계에서 정할 것.
2. **liveness 를 DB 에 묶을지 결정**한다. 묶으면 DB 가 잠깐 흔들릴 때 전 파드가 재시작
   루프에 빠질 수 있고, 안 묶으면 이번처럼 영구 방치된다. 절충안: liveness 는 그대로
   두되 **연속 N 회 readiness 실패가 지속되면 자기 종료**(고전적 패턴).
3. 이번 사건이 **왜 커넥션을 끊었는지**는 미확인이다 — 조사 중 iptables 실험을 한
   시각과 겹치지만 5432 를 막은 적은 없다. 원인 불명이라는 사실 자체를 적어 둔다.
   재연결이 있으면 원인과 무관하게 복구된다는 것이 이 항목의 요지다.

---

### ✅ 슬라이스 30 «테스트 부채 마감» — **완료**(2026-08-13, d41) — 위생 슬라이스 연쇄 종결

플랜 `plans/2026-08-12-dms-test-debt-slice30.md`. 백엔드 **1280 passed**(기준선 1266
+14) / 프론트 266 무변경 / e2e 무영향. **§2.5 의 행동 가능한 테스트 부채를 전량 소진**했다.

**테스트 그물 4건**(앱 코드 무변경): ① **전수 열거 그물** — 실 sqlite_master ==
ALL_TABLES ∪ 3(batches·batch_items·schema_migrations) 양방향 + 인덱스 16 등식. 슬라이스
27 이 발견한 ALL_TABLES 사각지대를 닫아, 이제 테이블·인덱스 추가·삭제가 반드시 걸린다.
② **이중 경로 일반 그물** — 현재 컬럼 == v1 ∪ ensure 등식으로 "CREATE 에만 넣고 ensure
를 잊는" 슬라이스 14 실 500 계열을 미래형으로 잡는다. ③ **KubernetesClient lazy-init**
이중검사 결정적 테스트(실 k8s 경로는 pragma 유지). ④ **슬라이스 15 잔여** — 파서 None
입력 그물·summary 픽스처 현행화.

**코드 위생 2건**: ⑤ `information_schema` 쿼리 2곳에 `current_schema()` 한정(타 스키마
동명 테이블 오판 봉쇄). ⑥ **planner 비원자 쌍 2곳 원자화** — 슬라이스 27 의 `_apply_state`
후속. `set_state_with_result`(전이+results 한 트랜잭션)로 `_reject`·conflict 를 교체해
`record_result` 단독 호출을 src 에서 0 으로 만들었다. 크래시 시 "종단인데 results 없음
→ 영구 결손"(finalize 계열)이 구조적으로 불가능해진다. **라이브 파드에서 코드 반영
확인**(set_state_with_result 존재), /readyz 200.

**구현 중 에이전트가 잡은 플랜 결함 2건**: T2 패리티 테스트가 정규식 0매치면 공허
통과하는 구멍(`len(pairs)==23` 자기검증 추가), T3 fast path 단언이 안쪽 재검사로 초록
유지되던 무이빨(`__enter__` 가 던지는 락으로 "락 없는 조기 반환"을 실제 단언).

**실측으로 뺀 것**: KubernetesClient 전체 커버(대역을 테스트하는 꼴), 실 PG ALTER 하니스
(위생 슬라이스 과잉) — 의도적 잔존. 완전 무효 판정은 0건(6후보 전부 부분 유효).

---

**🏁 위생 슬라이스 연쇄(27~30) 종결.** 남은 §2 백로그는 **결함이 아니라 기능 백로그·
운영 결정·의도적 제약**뿐이다: CI 기술적 강제 부재(수기 게이트), 실 k8s API 경로(실증
대상), LDAP 익명 바인드(자격증명 대기), 미구현 기능면(아티팩트 보존·배치 CSV 등),
클러스터 내 registry·Prometheus(의도적 제외), by_storage 해석·KPI 의미(침묵의 해석
기록), 프로세스 기록. 슬라이스로 묶을 결함은 더 없다.

### ✅ 슬라이스 29 «포탈 위생» — **완료·실증 통과**(2026-08-12, d40)

플랜 `plans/2026-08-12-dms-portal-hygiene-slice29.md`. 프론트 **266 passed**(기준선
257 +9) / tsc 0 / e2e 9. 앱 코드는 3파일(AppShell·useDenylist·api.ts)만, 나머지는
테스트다(백엔드·스키마 무접촉). **§2.2 의 남은 유일 포탈 🔴(로그아웃 URL)을 닫아
포탈 🔴 이 전부 사라졌다.**

- **로그아웃 URL** — qc.clear() 유지 + AppShell 명시 nav("/login"). nav 를 훅이 아닌
  AppShell 에 둔 이유는 useAuth.test 가 Router 없이 훅을 렌더하기 때문. 무한 루프
  (슬라이스 26 계열)는 /login 이 쿼리 관찰자 0 이라 성립 불가 — router.test 가 "me
  호출 횟수 불변"으로 못박고, e2e E1 이 세션 파기만 단언하던 것을 `/login` URL 도달
  까지 확장했다.
- **poll_failed 문구 일반화** — "빌드 상태를…" → "상태를 확인하지 못했습니다"(빌드·잡
  로그 공유 코드). reasonCodes.json 무접촉. **라이브 dist 번들에서 옛 문구 0건·새 문구
  존재 실증**.
- **useDenylist URL 인코딩** — encodeURIComponent 로 `#`(fragment 절단)·`?`(쿼리 흡수)
  wrong-target 봉쇄. subject 의 `/` 는 ASGI %2F 디코드라 여전히 백엔드 404(근본 해결은
  경로 재설계, 범위 밖).
- **테스트 부채 4건** — jobState 잔여 상태·BatchDetail waitFor·Sparkline NaN/Infinity·
  by_state 비배열. 앱 코드 무변경.

**구현 중 에이전트가 잡은 플랜 결함**: by_state 테스트의 `findByText("잡 통계")` 즉시
단언이 로딩 첫 렌더에도 존재해 데이터 착지 전 초록으로 끝나는 무이빨 단언이었다 —
waitFor 로 관찰 창을 데이터 뒤로 밀어 이빨을 만들었다. **실측으로 2건은 뺐다**:
PolicyDialog tool 필드는 label 감싸기로 이미 접근 가능(결함 아님), Sparkline NaN 은
슬라이스 26 이 이미 필터(테스트만 추가).

**배포**: dms d40 — 프론트만 바뀌었지만 Dockerfile.dms 가 dist 를 이미지에 COPY 하므로
재빌드 필요(제어면이 포탈 dist 를 서빙). migrate 재실행 불요.

---

### ✅ 슬라이스 28 «운영·보안» — **완료·실증 통과**(2026-08-12, d39)

플랜 `plans/2026-08-12-dms-ops-security-slice28.md`. 백엔드 **1266 passed**(기준선
1259 +7) / 프론트 **257**(255 +2) / tsc 0 / e2e 9. §2.3 의 세 항목을 다뤘다.

**항목 1 — 레지스트리 fail-open 비침묵화(tradeoff 유지).** fail-closed 로 뒤집는 건
설계 §7 이 거부한 것(레지스트리 브리프 다운에도 롤아웃이 막힘 > ImagePullBackOff)이라,
"조용한 fail-open"을 "표시되는 fail-open"으로만 바꿨다: `tag_verified` 응답 플래그 +
`release_tag_unverified` 이벤트(create_batch 성공 뒤에만 — 422/409 거절엔 유령 이벤트
없음) + 포탈 경고 배너. 검증 강제는 1비트도 안 바뀐 거동 동치 재구성이다.

**항목 3 — LDAP `DMS_LDAP_REQUIRE_AUTH_BIND` fail-closed(자격증명 없이 가능한 강화).**
플래그를 켰는데 bind DN/PW 가 결측·자리표시자(CHANGE_ME 등)면 config 경계에서
SettingsError 로 기동 거부. resolver 가 아닌 기동 시점 발화라 운영자가 배포 순간에
"인증 바인드 의도했는데 익명으로 조용히 도는" 상태를 안다. **실 파드 env 에서 발화
실증**했다(플래그만 켠 1회성 프로세스 → BIND_DN·BIND_PW 지목한 SettingsError).

**낡은 전제 정정(항목 2).** DaemonSet 600s 는 총 수렴 상한이 아니라 노드-단위 정체
상한이다(진행 틱마다 applied_at 재장전) — §2.3 참조. 순수 실증 항목이라 코드 0.

**자격증명 블록(정직한 보고).** 실제 LDAP 인증 바인드 전환만 자격증명이 막는다:
OpenLDAP 바인드 계정 발급 + dms-secrets 주입 + 플래그 "true" 전환(순서 엄수).
코드·플래그·라이브 fail-closed 는 이 세션이 끝냈고, 전환은 자격증명 대기(§2.3).

**배포**: dms 만 d39(러너·에이전트·스키마 무접촉), migrate 재실행 불요.

---

### ✅ 슬라이스 27 «DB 정합성» — **완료·실증 통과**(2026-08-12, d38)

플랜 `plans/2026-08-12-dms-db-integrity-slice27.md`(설계문서 없음 — 백로그가 「왜」).
백엔드 **1259 passed**(기준선 1254 -5 정리 +5 신규 +... 순증) / 프론트 무변경 / e2e 9.
남은 백로그(§2)에서 두 항목을 닫았다.

**항목 A — 死物 `runs` 제거(이 저장소 최초의 파괴적 마이그레이션).** CREATE·`ALL_TABLES`
삭제 + CREATE 실행 루프 **뒤에** `DROP TABLE IF EXISTS runs`(멱등 — 신규 DB no-op,
기존 DB 빈 테이블 삭제). `len==20` 단언 2곳 + 모듈 docstring + `ALL_TABLES` 를 전부
19 로 갱신. **실증**: 삭제 전 실 DB 의 runs 가 0행임을 확인(데이터 손실 없음) → migrate
재실행 → `information_schema` 에서 runs 부재 확인(기존 DB 경로가 실제로 먹었다), 나머지
테이블 무영향(22 = 19 + batches·batch_items·schema_migrations). 배포 후 `/readyz` 200.

**항목 B — `finalize_from_job` 원자화(슬라이스 24 실증이 관측한 실 결함).** 전이는
커밋됐는데 `record_result` 가 터지면 요청은 종단인데 results 행이 없고, 종단 요청은
고아 스윕 시야 밖이라 결손이 영구였다. **함정**: 단순히 `with transaction()` 으로
감싸는 건 불가능하다 — `set_state` 가 이미 트랜잭션을 열어 중첩이 되면 sqlite 는 즉사,
PG autocommit 은 안쪽 COMMIT 이 바깥을 조기 커밋해 `record_result` 가 트랜잭션 밖에서
도는 **조용한 비원자**(고치는 척만 하는 최악)가 된다. 전이 몸통을 무트랜잭션
`_apply_state` 로 추출해 `set_state`(단독)와 `finalize`(전이+results 합동)가 각자 경계를
소유하게 했다. 멱등 가드는 읽기 후 조기 반환이라 트랜잭션 밖(회귀 그물로 계약 고정).
원자화로 "results 는 있는데 요청은 비종단"이 구조적으로 불가능해져 UniqueViolation
재시도 창도 함께 닫혔다.

**남긴 것**: planner 에 동종 비원자 쌍 2곳(`_reject`·conflict) — `_apply_state` 로 후속이
각 2줄(§2.4). `ALL_TABLES` 가 batches/batch_items/schema_migrations 를 빠뜨리는 사각지대
(§2.4→슬라이스 30). finalize 원자화는 크래시 인위 유발이 어려워 단위 테스트로 계약을
고정했다(슬라이스 24 가 이미 그 결함을 실증한 바 있다).

---

### ✅ 슬라이스 26 «포탈 기능 잔여» — **완료·실증 통과**(2026-08-12, d37)

설계 `specs/2026-08-11-dms-portal-features-slice26-design.md`, 플랜
`plans/2026-08-11-dms-portal-features-slice26.md`.
백엔드 **1254 passed**(기준선 1233 +21) / 프론트 **255**(232 +23) / tsc 0 / **e2e 9**.

**넣은 것 4묶음**: ① 아티팩트 다운로드(전체 파일 획득 경로 신설) ② FAST-FOLLOW 6건
③ 고급 sync 옵션 폼(open_noatime·batch_files·bufsize·chmod·chown) ④ Sparkline 1점.
자른 것(삭제·보존 UI, 배치 CSV 개편, rm 배치)은 설계 §7 그대로 — 실증이 두 방향으로
갈라지지 않게.

**다운로드 보안 실증(실 클러스터 d37, §6-1·§6-2)** — 요청자가 phase 디렉터리 소유자라는
위협 모델을 실제로 재현했다:

| 검증 | 결과 |
|---|---|
| 정규 리포트 다운로드 | sha256 원본과 **정확히 일치**(12739B), 헤더 3종(octet-stream·attachment·nosniff) 고정, Content-Length 일치 |
| 뷰 공존 | 무 `/download` GET 은 여전히 JSON 꼬리 — 256KB 뷰 경로 불변 |
| 심링크(`→/etc/passwd`) | **404 `artifact_not_found`** — 탈출 봉쇄 |
| FIFO | **404 즉답 0.00초** — `O_NONBLOCK` 이 열기에서 안 막힌다 |
| 10G sparse | **413 `artifact_too_large`** — 헤더 전 판정, 절단 아닌 명시 거부 |
| 목록 | 심링크·FIFO **안 뜸**, sparse 만 정규파일로 노출 |

**불변식이 실증한 것**: 404 가 4경우(심링크·FIFO·미존재)에서 **body 까지 동일** — 존재
오라클 없음. 413 은 봉쇄·소유권 통과 뒤에만 나와 자기 잡 디렉터리 안에서만 관측된다.
검사한 fd 그대로 스트림하므로 경로 재해석 TOCTOU 창이 없다.

**FAST-FOLLOW 6건 + 슬라이스 23 이 찾은 flex td 결함**을 이 슬라이스가 종결했다:
- **StoragesList flex td 수리** — 9fbef86 형태(td 안 div 로 flex 이동). 슬라이스 23 e2e 의
  `knownNonTableCells: 1` 인자를 **같은 커밋에서 제거**했다(안 지우면 "수리됐으니 이 줄을
  지우라"고 e2e 가 빨개진다 — 정확 개수 단언의 상환 구조가 실제로 작동했다).
- 스토리지 배지 색(Ready=초록/Degraded=황색), api.ts 401 분기 통합(403 이 로그인으로
  안 튕긴다), Login `instanceof ApiError`(영어 원문 노출 제거), 잡 취소 오류 카드 한정,
  ConfirmDialog 닫힘 reset, Home `me.isError` 오류+재시도, 무효화 접두 중복 제거,
  Sparkline 1점 circle.

**구현 중 에이전트가 잡은 플랜 결함 2건**:
- **Home `me.isError` 가 401 을 삼키면 무한 요청 루프**가 된다. 플랜 스니펫 `if (me.isError)`
  는 세션 없음(401)까지 "서버 오류" 화면으로 뭉개는데, `/` 에서 관찰자가 언마운트되지
  않아 `dms:unauthorized`→me 무효화→재조회 401 이 끝없이 돈다(AuthContext 주석이 명시한
  루프 조건). 401 은 기존 /login 경로로 빠지도록 가드를 추가했다.
- **무효화 dedup 의 기존 테스트가 뮤테이션에 하나도 안 빨개졌다** — confirm/cancel 후 목록
  갱신을 단언하는 테스트가 실제로 없었다(플랜은 "기존 그물"로 간주했다). 취소 후 갱신
  테스트를 추가하고 폴링(2s)과 무효화를 시간축으로 구분했다.

**정직한 한계**: 로그아웃 후 URL 이 30초간 안 바뀌는 결함(useLogout 의 `qc.clear()`)은
**범위 밖 유지**했다 — 쿼리 캐시 수명주기의 별도 결정이 필요하고, 세션 파기 자체는
정상이라 보안 결함이 아니며, 지금 안 고쳐도 e2e 가 빨개지지 않는다. §2.2 에 잔존 명시.

**배포**: `dms` 만 d37(프론트+API+설정 키 1개, 러너·에이전트·스키마 무접촉 — `git diff`
확인). 스키마 무변경이라 **migrate 재실행 불요**.

**슬라이스 1~26 완료 후, 남은 §2 백로그를 위생·결함 슬라이스로 이어 처리 중이다**
(사용자 결정: 신규 기능 보류, 결함·위생 먼저 — 슬라이스 27~30). 슬라이스 27(DB
정합성)이 그 첫째다.

---

### ✅ 슬라이스 25 «실행 단계 진단» — **완료·실증 통과**(2026-08-12, d36)

설계 `specs/2026-08-11-dms-exec-diagnostics-slice25-design.md`, 플랜
`plans/2026-08-11-dms-exec-diagnostics-slice25.md`.
백엔드 **1233 passed**(기준선 1189 +44) / 프론트 **232**(228 +4) / tsc 0 / **e2e 9**.
**슬라이스 5 가 "범위 밖"으로 남긴 지 10슬라이스째, 실행 단계 진단이 아티팩트 전용에서
벗어났다.**

**한 일**: ① `read_log` 의 vcjob 거절을 풀었다(`volcano.sh/job-name` 셀렉터, launcher
항상 + 나머지는 Failed 만, launcher 가 앞). ② 실패 종단 4경로가 **종단 전이 직전에**
파드 로그를 `data_jobs.diag_logs` 에 박제한다(write-once). ③ `/logs` 는 라이브 우선·
박제 폴백이고 어느 쪽인지 `source: live|archived` 로 **정직하게 밝힌다**. ④ 실패 잡도
summary·artifact_uri 를 표면화한다.

**실증(실 클러스터 d36) — 핵심은 "파드를 지워도 살아남는가"다:**

| # | 검증 | 결과 |
|---|---|---|
| 1 | 스키마가 **기존 DB** 에 먹었나(ALTER 경로) | `information_schema` 에 `diag_logs / text / nullable` — 빈 DB 생성 경로와 다른 코드가 실제로 동작 |
| 2 | preflight 실패 박제 | `Rejected/preflight_failed` + `diag_logs` 에 `DMS_PREFLIGHT_REASON=target_not_readable` |
| 3 | **파드 삭제 전** `/logs` | `source: live` |
| 4 | **파드 삭제 후** `/logs` | **`source: archived`, 로그 내용 동일** ← 이전이라면 증거가 영영 사라지던 지점 |
| 5 | write-once(실 PG) | 재박제 시도가 첫 사본을 못 덮음, 공격 문자열 미침투 |
| 6 | 다행 조회 팽창 방지 | `list_jobs` 에 diag_logs 없음 / `get_job` 에만 있음, 차집합 정확히 `{diag_logs}` |

**배포**: `dms` 이미지만 d36(러너·에이전트 무접촉이라 d35 유지 — `git diff` 로 확인).
**스키마 변경이라 migrate Job 재실행이 필수**였고 순서는 빌드 → migrate → 40/41 apply.
빌드 커밋(`DMS_COMMIT_SHA`)이 범프 커밋과 일치하는지도 확인했다.

**구현 중 에이전트가 잡은 플랜 결함 3건** — 셋 다 "검사하는 척"을 막는 쪽이다:
- **꼬리 자르기가 상한을 넘겼다.** 플랜의 `raw[-16KB:].decode(errors="replace")` 는
  경계에서 잘린 1~3바이트 조각을 U+FFFD(**3바이트**)로 부풀려 **16386 > 16384** 를
  만든다(실측). 한국어 실패 사유가 흔한 이 시스템에서 "파드당 16KB·총 64KB" 계약이
  거짓이 되고, 덤으로 원본에 없던 깨진 글자를 진단 로그에 심는다. 선두 연속 바이트를
  **버리고** 물러나도록 고쳤다(재확인: 16383 ≤ 16384, U+FFFD 0건, 꼬리 보존).
- **플랜이 지정한 뮤테이션이 아무것도 못 잡았다**(`/logs` 폴백의 `all`→`any`). 그
  테스트의 라이브 항목이 1개뿐이라 둘이 구분되지 않았다. 실전 vcjob 은 **로그 있는
  launcher + 이미 사라진 워커**가 섞이므로, `any` 였다면 파드 하나가 null 이라는
  이유로 살아 있는 launcher 로그가 통째로 박제 사본에 가려진다 — 섞인 목록 테스트를
  새로 만들어 막았다.
- `_archived_entries` 의 **모양 검사 부재**: 문법은 맞고 모양이 틀린 diag JSON 이 오면
  `/logs` 가 500 이 되어 **라이브 열람까지 같이 죽는다**. 폴백 실패가 본 기능을
  무너뜨리면 안 되므로 "깨짐"의 정의에 모양 불일치를 포함시켰다.

**판단 1건(설계에 없던 것)**: 슬라이스 24 가 추가한 종단 경로 2건
(`unknown_tool`·`storage_missing_at_step`)은 **박제 비대상**이다. Pending 종단은 파드가
생성된 적이 없고, 진행 중 종단이라도 `_fail_closed` 가 finalize 전에 refs 를 terminate
하며, 무엇보다 그 경로의 증거는 파드 로그가 아니라 **DB 행 자체**(변조된 tool·사라진
storage)다 — 파드 로그는 정상 실행 중이던 잡의 것이라 실패 원인을 담지 않는다.
`test_fail_closed_paths_do_not_archive` 가 이 판정을 계약으로 고정한다.

**정직한 한계**: 러너가 아티팩트를 쓰기 전에 크래시하는 경로의 **실 클러스터** 실증은
결정적 유도 수단이 없어 기회 실증으로 남겼다(단위 테스트가 계약을 고정한다). 그리고
`poll_failed` 의 화면 문구는 아직 "빌드 상태를 확인하지 못했습니다"라 로그 조회 409 에도
그대로 보인다 — 설계의 "사유 코드 신설 0" 방침을 지킨 결과이고, 문구 일반화는 §2.2 에 남긴다.

---

### ✅ 슬라이스 23 «포탈 e2e 테스트» — **완료·e2e 9건 통과**(2026-08-12, 클러스터 무관)

설계 `specs/2026-08-11-dms-portal-e2e-slice23-design.md`, 플랜
`plans/2026-08-11-dms-portal-e2e-slice23.md`. **15슬라이스째 e2e 0건의 종결.**
백엔드 1189 / 프론트 228·49 / tsc 0 / **e2e 9 passed (24.2s)**.

**앱 코드 변경 0 이 계약이었고 지켰다** — `git diff 70561a8..HEAD -- src frontend/src
deploy/k8s` 가 **빈 출력**이다. data-testid 도 달지 않았다. e2e 가 앱을 바꾸기 시작하면
"실제로 배포되는 것을 검사한다"는 전제가 무너지기 때문이다. 유일한 예외는
`vite.config.ts` 의 vitest include 잠금 4줄(e2e 스펙이 vitest 에 빨려드는 것을 막는다).
새 의존성은 승인된 `@playwright/test` 1건뿐이고 **브라우저 다운로드도 0**(시스템 크롬
147, `channel:"chrome"`).

**이빨 검증 — 이 슬라이스의 존재 증명.** 두 결함을 각각 재주입해 두 계층을 비교했다:

| 재주입한 결함 | 단위 228건 | e2e |
|---|---|---|
| 사이드바 밀림(6bc2ecb 이전: `md:shrink-0`·`min-w-0` 제거) | **전부 초록** | `[L1] /admin/accounts: 표가 자기 컨테이너 안에서 스크롤하지 못하고 문서 전체가 넘쳤다(scrollWidth=1567 > clientWidth=1280)` |
| 계정 표 뭉개짐(9fbef86 이전: `td` 자체를 flex) | **전부 초록** | `[L2] /admin/accounts: computed display 가 table-cell 이 아닌 셀 4개(기대 0개)` + 어느 셀인지 전문 |

즉 **라이브에서만 드러나던 결함 유형이 이제 로컬에서 잡힌다.** 원복 후 9건 재통과.

**🔎 e2e 가 만들자마자 실물 결함 1건을 찾았다** — `StoragesList.tsx:50` 이 9fbef86 이
계정 표에서 걷어낸 것과 같은 구조로 남아 있다(§2.2 에 항목 등록). 앱 무변경 계약이라
고치지 못하고 `knownNonTableCells: 1` 로 정확한 개수를 못박았다.

**시나리오 6개(E1~E6)**: 부팅+세션 / SPA fallback 딥링크 / 레이아웃 불변식 L1~L4 순회
(1280×800 + 375×667) / 잡 종단 흐름(UI 제출→리로드 없이 Succeeded) / 목록 폴링 수렴 /
상세 잡 폴링 종단 중지. 실행은 로컬 풀스택(tmp sqlite + migrate + api **dist 서빙** +
controller 1s + agent --once) — **클러스터 불요**. dev 서버가 아니라 dist 를 서빙하는
이유는 vite dev 의 자체 fallback 이 `spa_fallback` 코드를 가려 그 회귀를 영원히 못 잡기
때문이다.

**구현 중 에이전트가 플랜을 고친 것들(전부 "검사하는 척"을 막는 강화)**:
- 플랜의 전제 단언이 사이드바 결함에 **정반대 수리를 지시**했다 — `min-w-0` 이 없으면
  래퍼의 `overflow-x-auto` 가 발동조차 못 해 래퍼는 안 넘치고 문서가 넘치는데, 플랜대로면
  `E2E_SEED_TOO_NARROW`(= "시드를 늘려라")로 보고된다. 문서 오버플로를 먼저 갈라 `[L1]`
  로 보고하도록 고쳤다.
- E6 의 `getByText("Succeeded").first()` 는 폴링 없는 **요청 카드** 배지에 걸려 잡 쿼리에
  대해 아무것도 증명하지 못했고, 잡 배열이 비어도 통과했다(§1-11 의 빈 배열 함정).
  잡 ID 가시성 + API 에서 유도한 개수 단언으로 교체.
- `window.__dmsE2eNoReload` 표식으로 "새로고침 없이"를 주석 규율이 아니라 **실행 시점에**
  강제(누가 `page.reload()` 를 끼워 넣으면 조용히 무의미해지는 대신 빨개진다).
- 하네스: 컨트롤러 spawn 을 시드 **뒤로** 옮겼다 — 기동 첫 틱의 리스 획득 버스트가 sqlite
  쓰기 락과 경합해 6회 중 1회 `POST /storages` 500 을 실제로 냈다(운영은 PG 라 하네스 한정).
- `forbidOnly: true` — CI 가 없어 수기 실행이 유일한 게이트인데 남겨진 `test.only` 하나면
  스위트가 1건으로 줄고도 exit 0 이다.

**정직한 한계**: CI 는 없다(설계 §7). 이 게이트는 **수기**이고 `deploy/README` 의
"이미지 빌드 전" 단계로 명문화했을 뿐, 기술적 강제 수단은 없다 — 숨기지 않는다.

---

### ✅ 슬라이스 24 «파괴적 경로 fail-open 봉인» — **완료·실증 5/5**(2026-08-12, d35)

설계 `specs/2026-08-11-dms-destructive-failopen-slice24-design.md`, 플랜
`plans/2026-08-11-dms-destructive-failopen-slice24.md`.
백엔드 **1189 passed**(기준선 1166 +23) / 프론트 228 / tsc 0. §2.1 의 4건을 닫았다.
이미지는 제어면·에이전트·**잡 러너까지** d35 (층3 이 잡 이미지에 살기 때문 —
`DMS_JOB_IMAGE` 가 d27 로 뒤처져 있었다).

**실증(전부 실 클러스터, 되돌릴 수 있는 조작만)**

1. **§6-1 파괴적 정상 경로 무회귀** — 전용 드릴 디렉터리
   `/cephfs/dms/slice24-rm-drill`(f1·f2·sub/f3, 소유 10003:10000)에 rm 잡을
   preview→confirm→실행. **Succeeded**, `{"files": 5, "returncode": 0}`.
   드릴 디렉터리는 사라졌고 **형제 9개는 전부 무손상**. 층1~3 을 모두 무변경
   통과함을 파괴적 연산으로 직접 확인했다.
2. **§6-2 `"/"` 등록 거부** — `{mount "/", root "/"}` 와 `{mount "/cephfs", root "/"}`
   둘 다 **422 `invalid_storage`**. 같은 요청에서 정상 조합은 **201** (무회귀).
3. **§6-3 층1 — 이번 슬라이스의 핵심 증거.** Pending 잡 2건(대조군 `dscan` +
   변조 `dwalk`)을 만들고 drain 해제. 변조 잡은 **Pending → Rejected
   `unknown_tool`**(중간 상태 없음), **pod/vcjob 0건** — 제출 자체가 막혔다.
   같은 틱에 대조군은 **Succeeded, 453 파일 스캔** — 정상 경로는 끝까지 정상이다.
   대조군이 있어서 "막혔다"와 "그냥 안 돌았다"가 구분된다.
4. **§6-4 층3 단독** — d35 잡 이미지 파드에서 `DMS_JR_TOOL=sh` 로 러너만 실행:
   `rc=1`, stderr `DMS_JR_UNKNOWN_TOOL tool='sh' allowed=('dscan','dsync','nsync','drm')`,
   `summary.json == {"returncode": 1, "files": null, "bytes": null}`(3키 계약, 모름은
   null). mpirun/ssh 시도 흔적 0 — 부작용 이전에 끊겼다.
5. **§6-5 고아 복구** — 3건 재현 → **한 틱에 전부 복구**(`orphan_recovery` 전이 3건),
   재스윕 **0건**.

**🔎 실증이 설계 전제 2건을 정정했다(§1-10 관련):**
- 설계는 "`record_result` 가 무조건 INSERT 라 **results 중복 삽입**이 가능하다 —
  복구가 이력을 오염시킨다"고 적었다. **틀렸다.** `results.request_id` 는
  **PRIMARY KEY**(`migrations.py:117`)라 중복은 구조적으로 불가능하고, 실제로는
  `UniqueViolation` 으로 **시끄럽게** 실패한다. 조용한 오염 위험은 없었다.
  따라서 플랜 §6-5(d) 의 "중복 results 원복 DELETE" 절차도 불필요했다 — 실측
  결과 request 당 results 는 정확히 1행이었다.
- 그 대신 **행 단위 격리(§2.3)가 라이브에서 실제로 발화했다**: 위 재현이
  (요청을 되돌리는 방식 탓에 results 행이 이미 있어) 독 행 3개를 만들었고,
  세 행이 **각각 독립적으로** 실패해 `orphan_recovery_failed` 이벤트 3건을 남겼으며
  **서로를 막지 않았다**. 플랜은 이 경로를 "독 행 없인 발화하지 않으니 단위 테스트
  몫"이라고 정직하게 적었는데, 재현이 우연히 진짜 독 행을 만들어 **프로덕션에서
  증명**됐다. 구 코드였다면 첫 예외가 나머지 전부를 다음 틱으로 밀었을 자리다.
- 부수 관찰: `finalize_from_job` 은 원자적이지 않다 — 상태 전이는 커밋되고 그
  뒤 `record_result` 가 터졌다(그래서 재스윕은 0인데 이벤트는 3건). 실 고아
  (finalize 가 아예 안 돈 경우)엔 results 행이 없어 이 경로가 안 생기지만,
  "부분 적용된 finalize" 자체는 남는 관찰이다 → §2.1 에 항목으로 남긴다.

**구현 중 에이전트가 잡은 플랜 결함 3건**(전부 고쳐서 반영):
- **가장 중요**: 플랜의 `posixpath.join(root, rel)` 은 `rel` 이 절대경로면 root 를
  **통째로 버린다**(`join("/cephfs/dms", "/etc") == "/etc"`). 기존 f-string 은
  `"/cephfs/dms//etc"` 로 **안에 가두고 있었다** — `//` 를 없애는 수정이 그 봉쇄까지
  걷어내면 fail-open 하나를 닫으면서 **더 나쁜 것(drm 이 managed_root 밖을 삭제)을
  연다**. `rel.lstrip("/")` 로 구현하고 회귀 테스트
  (`test_absolute_target_in_db_cannot_escape_managed_root`)를 추가했다.
- 플랜이 지정한 storages 뮤테이션이 **살아남았다**(일반 경로 규칙이 이미 `"/"` 를
  잡으므로 명시 분기는 이빨이 0이었다) → `detail == "root filesystem is not a storage"`
  까지 고정하는 테스트를 추가해 명시 분기 자체를 계약으로 걸었다.
- 플랜이 **기존 테스트 1건을 놓쳤다** — `test_run_job_unknown_tool_summary_is_nulls`
  가 미지 도구의 `rc == 0`(= 이 슬라이스가 닫는 fail-open)을 고정하고 있었다.
  삭제 대신 `_build_summary` 순수 함수 층으로 **재조준**해 회귀 그물을 보존했다.

**남은 것**: 없음(설계 §7 의 비목표는 의도적 제외). 잔여 창은 §2.4 의
check-then-act 비원자성 — `_abs` fail-closed 가 최종 방어라는 것을 코드 주석에
명시했다.

---

